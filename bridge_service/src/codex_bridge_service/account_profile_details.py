"""Bounded, private usage reads for saved Home Assistant ChatGPT sign-ins."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any, Callable

from .account import normalize_chatgpt_plan_type
from .account_profiles import AccountProfileError, AccountProfileStore, _credential_account_id
from .auth_state import account_status
from .codex_app_server import CodexAppServerClient
from .limits import _app_server_limits_status
from .models import LimitsStatusRecord
from .workspace import WorkspaceNotFoundError


# Keep this minimal, credential-free config in lockstep with the App's managed
# permission profiles. An isolated Codex home cannot start with auth.json alone:
# the immutable App requirements select ha_bridge, which must be defined here.
# test_account_profile_details.py checks equality with initialize_runtime.py.
_PROBE_CONFIG_PAYLOAD = b"""cli_auth_credentials_store = "file"
default_permissions = "ha_bridge"

[permissions.ha_observe]
description = "Home Assistant read-only workspace sandbox"

[permissions.ha_observe.filesystem]
":minimal" = "read"

[permissions.ha_observe.filesystem.":workspace_roots"]
"." = "read"

[permissions.ha_observe.network]
enabled = false
allow_local_binding = false
allow_upstream_proxy = false

[permissions.ha_bridge]
description = "Home Assistant workspace-only sandbox"

[permissions.ha_bridge.filesystem]
":minimal" = "read"

[permissions.ha_bridge.filesystem.":workspace_roots"]
"." = "write"
".codex" = "write"
".git" = "write"
".agents" = "write"
".cursor" = "write"
".vscode" = "write"

[permissions.ha_bridge.network]
enabled = false
allow_local_binding = false
allow_upstream_proxy = false
"""


def _public_details(status: str, plan: str | None, limits: LimitsStatusRecord | None) -> dict[str, Any]:
    windows: list[dict[str, Any]] = []
    if limits is not None:
        for name, window in (("5 hours", limits.primary), ("Weekly", limits.secondary)):
            if window is not None:
                windows.append({
                    "name": name,
                    "remaining_percent": window.remaining_percent,
                    "resets_at": window.resets_at,
                })
    resets = limits.reset_credits if limits is not None else None
    count = resets.get("available_count") if isinstance(resets, dict) else None
    credits = resets.get("credits") if isinstance(resets, dict) else None
    expiries = [item.get("expires_at") for item in credits if isinstance(item, dict)] if isinstance(credits, list) else []
    known = [value for value in expiries if type(value) is int and value > 0]
    return {
        "status": status,
        "plan": normalize_chatgpt_plan_type(plan),
        "windows": windows,
        "available_resets": count if type(count) is int and count >= 0 else None,
        "next_reset_expiry": min(known) if known and count else None,
        "expiry_complete": bool(count and isinstance(credits, list) and len(credits) >= count),
        "updated_at": (
            limits.updated_at or datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        ) if status == "available" and limits is not None else None,
    }


def _verified_snapshot(raw: bytes | None, account_id: str) -> dict[str, Any] | None:
    """Accept only the bounded public projection for the exact saved identity."""
    if raw is None:
        return None
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {"version", "account_id", "details"}:
            return None
        if value["version"] != 1 or value["account_id"] != account_id:
            return None
        details = value["details"]
        if not isinstance(details, dict) or set(details) != {
            "status", "plan", "windows", "available_resets", "next_reset_expiry",
            "expiry_complete", "updated_at",
        } or details["status"] != "available":
            return None
        plan = details["plan"]
        if plan is not None and normalize_chatgpt_plan_type(plan) != plan:
            return None
        windows = details["windows"]
        if not isinstance(windows, list) or len(windows) > 2:
            return None
        seen: set[str] = set()
        for window in windows:
            if not isinstance(window, dict) or set(window) != {
                "name", "remaining_percent", "resets_at",
            }:
                return None
            name = window["name"]
            remaining = window["remaining_percent"]
            reset = window["resets_at"]
            if name not in ("5 hours", "Weekly") or name in seen:
                return None
            if remaining is not None and (
                type(remaining) not in (int, float) or not isfinite(remaining)
                or not 0 <= remaining <= 100
            ):
                return None
            if reset is not None and (type(reset) is not int or reset <= 0):
                return None
            seen.add(name)
        resets = details["available_resets"]
        expiry = details["next_reset_expiry"]
        updated = details["updated_at"]
        if resets is not None and (type(resets) is not int or not 0 <= resets <= 10000):
            return None
        if expiry is not None and (type(expiry) is not int or expiry <= 0):
            return None
        if type(details["expiry_complete"]) is not bool:
            return None
        if not isinstance(updated, str) or len(updated) > 40:
            return None
        parsed = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return details
    except (TypeError, ValueError, OverflowError):
        return None


class AccountProfileDetailsProbe:
    """Read one profile without replacing the managed chat app-server's sign-in."""

    def __init__(
        self,
        store: AccountProfileStore,
        *,
        codex_command: str,
        active_limits: Callable[[], LimitsStatusRecord | None],
        client_factory: Callable[..., Any] = CodexAppServerClient,
        poll_interval_seconds: float = 60.0,
    ) -> None:
        if not 0 < poll_interval_seconds <= 3600:
            raise ValueError("profile usage poll interval must be between 0 and 3600 seconds")
        self._store = store
        self._codex_command = codex_command
        self._active_limits = active_limits
        self._client_factory = client_factory
        self._lock = Lock()
        self._cache: dict[str, tuple[str, float, dict[str, Any]]] = {}
        self._retry_after: dict[str, tuple[str, float, str]] = {}
        self._poll_interval_seconds = poll_interval_seconds
        self._stop_polling = Event()
        self._poll_thread: Thread | None = None
        self._next_inactive = 0

    def start_polling(self) -> None:
        if self._poll_thread is not None:
            return
        self._stop_polling.clear()
        thread = Thread(
            target=self._poll_loop, name="account-profile-usage", daemon=True,
        )
        thread.start()
        self._poll_thread = thread

    def stop_polling(self, *, timeout_seconds: float = 30.0) -> bool:
        """Bound shutdown; keep the private store open if a CLI read is stuck."""
        thread = self._poll_thread
        if thread is None:
            return True
        self._stop_polling.set()
        thread.join(timeout=timeout_seconds)
        if thread.is_alive():
            return False
        self._poll_thread = None
        return True

    def _poll_loop(self) -> None:
        while not self._stop_polling.wait(self._poll_interval_seconds):
            try:
                self.poll_once()
            except Exception:
                # Background refresh cannot affect task admission or App health.
                continue

    def poll_once(self) -> None:
        """Refresh the active snapshot and at most one inactive saved sign-in."""
        profiles = self._store.list_profiles()
        active = next((item for item in profiles if item["active"]), None)
        if active is not None:
            self.read(active["id"])
        inactive = [item for item in profiles if not item["active"]]
        if inactive:
            profile = inactive[self._next_inactive % len(inactive)]
            self._next_inactive += 1
            self.read(profile["id"])

    def _persist_verified(self, profile_id: str, account_id: str, details: dict[str, Any]) -> None:
        raw = json.dumps(
            {"version": 1, "account_id": account_id, "details": details},
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        if _verified_snapshot(raw, account_id) is not None:
            self._store.write_usage_snapshot(profile_id, account_id, raw)

    def _stale_or_unavailable(
        self, profile_id: str, account_id: str, plan: str | None, status: str,
    ) -> dict[str, Any]:
        try:
            snapshot = _verified_snapshot(self._store.read_usage_snapshot(profile_id), account_id)
        except (AccountProfileError, WorkspaceNotFoundError):
            snapshot = None
        if snapshot is None:
            return _public_details(status, plan, None)
        return {**snapshot, "status": "stale", "reauthentication_required": status == "reauthentication_required"}

    def _read_active(self, profile_id: str, plan: str | None) -> dict[str, Any]:
        try:
            _, account_id = self._store.target_credential(profile_id)
            _, current_id = self._store.current_credential()
            if current_id != account_id:
                return self._stale_or_unavailable(profile_id, account_id, plan, "unavailable")
        except (AccountProfileError, WorkspaceNotFoundError):
            return _public_details("unavailable", plan, None)
        limits = self._active_limits()
        try:
            _, still_current_id = self._store.current_credential()
        except (AccountProfileError, WorkspaceNotFoundError):
            still_current_id = None
        if still_current_id != account_id:
            return self._stale_or_unavailable(profile_id, account_id, plan, "unavailable")
        if limits is None or not limits.available:
            return self._stale_or_unavailable(profile_id, account_id, plan, "unavailable")
        details = _public_details("available", limits.plan_type or plan, limits)
        try:
            self._persist_verified(profile_id, account_id, details)
        except (AccountProfileError, WorkspaceNotFoundError):
            pass
        return details

    def read(self, profile_id: str) -> dict[str, Any]:
        # Profile identifiers and public metadata come only from the validated registry.
        profile = next((item for item in self._store.list_profiles() if item["id"] == profile_id), None)
        if profile is None:
            raise AccountProfileError("The saved account was not found.")
        plan = profile["plan"] if isinstance(profile["plan"], str) else None
        if profile["active"]:
            return self._read_active(profile_id, plan)

        with self._lock, self._store.credential_operation():
            latest = next((item for item in self._store.list_profiles() if item["id"] == profile_id), None)
            if latest is None:
                raise AccountProfileError("The saved account was not found.")
            if latest["active"]:
                latest_plan = latest["plan"] if isinstance(latest["plan"], str) else None
                return self._read_active(profile_id, latest_plan)
            raw, account_id = self._store.target_credential(profile_id)
            fingerprint = hashlib.sha256(raw).hexdigest()
            cached = self._cache.get(profile_id)
            if cached and cached[0] == fingerprint and monotonic() - cached[1] < 60:
                return dict(cached[2])
            retry = self._retry_after.get(profile_id)
            if retry and retry[0] == fingerprint and monotonic() - retry[1] < 60:
                return self._stale_or_unavailable(profile_id, account_id, plan, retry[2])

            def failed(status: str) -> dict[str, Any]:
                self._retry_after[profile_id] = (fingerprint, monotonic(), status)
                return self._stale_or_unavailable(profile_id, account_id, plan, status)

            try:
                with tempfile.TemporaryDirectory(prefix="usage-", dir=self._store.private_root) as name:
                    home = Path(name)
                    descriptor = os.open(home / "auth.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    try:
                        with os.fdopen(descriptor, "wb") as stream:
                            stream.write(raw)
                    except BaseException:
                        # fdopen owns the descriptor once it succeeds.
                        raise
                    config_descriptor = os.open(home / "config.toml", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(config_descriptor, "wb") as stream:
                        stream.write(_PROBE_CONFIG_PAYLOAD)
                    with self._client_factory(
                        codex_command=self._codex_command,
                        codex_home=home,
                        initialize_timeout_seconds=5.0,
                        request_timeout_seconds=5.0,
                        shutdown_grace_seconds=2.0,
                        callback_workers=1,
                        max_pending_requests=2,
                        enable_mcp=False,
                    ) as client:
                        identity = client.request("account/read", {"refreshToken": True}, timeout_seconds=5.0)
                        if account_status(identity)["state"] != "ok":
                            return failed("reauthentication_required")
                        plan = normalize_chatgpt_plan_type(identity["account"].get("planType")) or plan
                        response = client.request("account/rateLimits/read", timeout_seconds=5.0)
                    if not isinstance(response, dict):
                        return failed("unavailable")
                    reported_id = response.get("accountId")
                    if reported_id is not None and reported_id != account_id:
                        return failed("unavailable")
                    with (home / "auth.json").open("rb") as stream:
                        refreshed = stream.read(1_048_577)
                    if len(refreshed) > 1_048_576:
                        return failed("unavailable")
                    if _credential_account_id(refreshed) != account_id:
                        return failed("unavailable")
                    if not self._store.refresh_saved_credential(profile_id, raw, refreshed):
                        return failed("unavailable")
                    limits = _app_server_limits_status(response)
                    if limits is None or not limits.available:
                        return failed("unavailable")
                    details = _public_details("available", limits.plan_type or plan, limits)
                    try:
                        self._persist_verified(profile_id, account_id, details)
                    except (AccountProfileError, WorkspaceNotFoundError):
                        pass
                    self._cache[profile_id] = (hashlib.sha256(refreshed).hexdigest(), monotonic(), details)
                    self._retry_after.pop(profile_id, None)
                    return dict(details)
            except Exception:
                # No provider or process diagnostics, credential data, or raw IDs cross the API.
                return failed("unavailable")
