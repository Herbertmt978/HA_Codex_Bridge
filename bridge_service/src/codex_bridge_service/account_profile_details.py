"""Bounded, private usage reads for saved Home Assistant ChatGPT sign-ins."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any, Callable

from .account import normalize_chatgpt_plan_type
from .account_profiles import AccountProfileError, AccountProfileStore, _credential_account_id
from .auth_state import account_status
from .codex_app_server import CodexAppServerClient
from .limits import _app_server_limits_status
from .models import LimitsStatusRecord


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
        "updated_at": limits.updated_at if limits is not None else None,
    }


class AccountProfileDetailsProbe:
    """Read one profile without replacing the managed chat app-server's sign-in."""

    def __init__(
        self,
        store: AccountProfileStore,
        *,
        codex_command: str,
        active_limits: Callable[[], LimitsStatusRecord | None],
        client_factory: Callable[..., Any] = CodexAppServerClient,
    ) -> None:
        self._store = store
        self._codex_command = codex_command
        self._active_limits = active_limits
        self._client_factory = client_factory
        self._lock = Lock()
        self._cache: dict[str, tuple[str, float, dict[str, Any]]] = {}

    def read(self, profile_id: str) -> dict[str, Any]:
        # Profile identifiers and public metadata come only from the validated registry.
        profile = next((item for item in self._store.list_profiles() if item["id"] == profile_id), None)
        if profile is None:
            raise AccountProfileError("The saved account was not found.")
        plan = profile["plan"] if isinstance(profile["plan"], str) else None
        if profile["active"]:
            limits = self._active_limits()
            if limits is None or not limits.available:
                return _public_details("unavailable", plan, None)
            return _public_details("available", limits.plan_type or plan, limits)

        with self._lock:
            raw, account_id = self._store.target_credential(profile_id)
            fingerprint = hashlib.sha256(raw).hexdigest()
            cached = self._cache.get(profile_id)
            if cached and cached[0] == fingerprint and monotonic() - cached[1] < 60:
                return dict(cached[2])
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
                            return _public_details("reauthentication_required", plan, None)
                        plan = normalize_chatgpt_plan_type(identity["account"].get("planType")) or plan
                        response = client.request("account/rateLimits/read", timeout_seconds=5.0)
                    if not isinstance(response, dict):
                        return _public_details("unavailable", plan, None)
                    reported_id = response.get("accountId")
                    if reported_id is not None and reported_id != account_id:
                        return _public_details("unavailable", plan, None)
                    with (home / "auth.json").open("rb") as stream:
                        refreshed = stream.read(1_048_577)
                    if len(refreshed) > 1_048_576:
                        return _public_details("unavailable", plan, None)
                    if _credential_account_id(refreshed) != account_id:
                        return _public_details("unavailable", plan, None)
                    if not self._store.refresh_saved_credential(profile_id, raw, refreshed):
                        return _public_details("unavailable", plan, None)
                    limits = _app_server_limits_status(response)
                    if limits is None or not limits.available:
                        return _public_details("unavailable", plan, None)
                    details = _public_details("available", limits.plan_type or plan, limits)
                    self._cache[profile_id] = (hashlib.sha256(refreshed).hexdigest(), monotonic(), details)
                    return dict(details)
            except Exception:
                # No provider or process diagnostics, credential data, or raw IDs cross the API.
                return _public_details("unavailable", plan, None)
