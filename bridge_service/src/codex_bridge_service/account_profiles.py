"""Private, bounded credential snapshots for HA-hosted ChatGPT profiles."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import stat
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .account import normalize_chatgpt_plan_type
from .workspace import WorkspaceBoundary, WorkspaceNotFoundError

_MAX_CREDENTIAL_BYTES = 1024 * 1024
_MAX_REGISTRY_BYTES = 64 * 1024
_MAX_PROFILES = 16
_PROFILE_ID = re.compile(r"[a-f0-9]{32}\Z")
_ACCOUNT_CLAIM = "https://api.openai.com/auth"


class AccountProfileError(ValueError):
    """A profile cannot be saved or activated safely."""


class AccountProfileReauthenticationRequiredError(AccountProfileError):
    """A saved sign-in can no longer be refreshed by Codex."""


def _credential_account_id(raw: bytes) -> str:
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("auth_mode") != "chatgpt":
            raise ValueError
        tokens = payload["tokens"]
        if not isinstance(tokens, dict):
            raise ValueError
        account_id = None
        for token_name in ("id_token", "access_token"):
            token = tokens.get(token_name)
            if not isinstance(token, str):
                continue
            parts = token.split(".")
            if len(parts) < 2:
                continue
            try:
                encoded = parts[1]
                decoded = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
                claim = decoded.get(_ACCOUNT_CLAIM, {}) if isinstance(decoded, dict) else {}
                account_id = claim.get("chatgpt_account_id") if isinstance(claim, dict) else None
            except (binascii.Error, TypeError, ValueError, UnicodeError):
                continue
            if account_id:
                break
        if not account_id:
            account_id = tokens.get("account_id")
        if not isinstance(account_id, str) or not 1 <= len(account_id) <= 256:
            raise ValueError
        if any(ord(char) < 33 or ord(char) == 127 for char in account_id):
            raise ValueError
        return account_id
    except (binascii.Error, IndexError, KeyError, TypeError, ValueError, UnicodeError):
        raise AccountProfileError("The ChatGPT account identity is unavailable.") from None


def _label(value: str) -> str:
    if not isinstance(value, str):
        raise AccountProfileError("Enter an account label.")
    label = value.strip()
    if not 1 <= len(label) <= 60 or any(ord(char) < 32 or ord(char) == 127 for char in label):
        raise AccountProfileError("Enter an account label of up to 60 characters.")
    return label


class AccountProfileStore:
    """Store profile credentials without exposing them through the public API."""

    def __init__(self, root: Path, codex_home: Path) -> None:
        self.private_root = Path(root)
        self._profiles = WorkspaceBoundary(root, create=True)
        self._codex = WorkspaceBoundary(codex_home, create=True)
        root_fd = self._profiles.open_directory_fd(".")
        try:
            root_mode = stat.S_IMODE(os.fstat(root_fd).st_mode)
        finally:
            os.close(root_fd)
        if root_mode & 0o077:
            self.close()
            raise AccountProfileError("Saved account storage is not private.")
        self._lock = RLock()

    def close(self) -> None:
        self._profiles.close()
        self._codex.close()

    @staticmethod
    def _read_bounded(
        boundary: WorkspaceBoundary, relative: str, limit: int,
        *, harden_permissions: bool = False,
    ) -> bytes:
        with boundary.open_regular_file(relative) as stream:
            mode = stat.S_IMODE(os.fstat(stream.fileno()).st_mode)
            if mode & 0o077:
                if harden_permissions:
                    os.fchmod(stream.fileno(), 0o600)
                else:
                    raise AccountProfileError("Saved account storage is not private.")
            raw = stream.read(limit + 1)
        if not raw or len(raw) > limit:
            raise AccountProfileError("The saved account file is invalid.")
        return raw

    def _registry(self) -> dict[str, object]:
        try:
            raw = self._read_bounded(self._profiles, "profiles.json", _MAX_REGISTRY_BYTES)
        except WorkspaceNotFoundError:
            return {"version": 1, "active_profile_id": None, "profiles": []}
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {
                "version", "active_profile_id", "profiles"
            } or value["version"] != 1 or not isinstance(value["profiles"], list):
                raise ValueError
            if len(value["profiles"]) > _MAX_PROFILES:
                raise ValueError
            active = value["active_profile_id"]
            if active is not None and (not isinstance(active, str) or not _PROFILE_ID.fullmatch(active)):
                raise ValueError
            ids: set[str] = set()
            account_ids: set[str] = set()
            for profile in value["profiles"]:
                if not isinstance(profile, dict) or set(profile) != {"id", "label", "account_id", "plan"}:
                    raise ValueError
                profile_id = profile["id"]
                account_id = profile["account_id"]
                if (not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id)
                    or not isinstance(account_id, str) or not 1 <= len(account_id) <= 256
                    or any(ord(char) < 33 or ord(char) == 127 for char in account_id)
                    or _label(profile["label"]) != profile["label"]
                    or profile["plan"] is not None and normalize_chatgpt_plan_type(profile["plan"]) != profile["plan"]
                    or profile_id in ids or account_id in account_ids):
                    raise ValueError
                ids.add(profile_id)
                account_ids.add(account_id)
            if active is not None and active not in ids:
                raise ValueError
            return value
        except (TypeError, ValueError, UnicodeError):
            raise AccountProfileError("The saved account list needs repair.") from None

    def _save_registry(self, value: dict[str, object]) -> None:
        self._profiles.atomic_write_bytes(
            "profiles.json",
            (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
        )

    def list_profiles(self) -> list[dict[str, object]]:
        with self._lock:
            registry = self._registry()
            try:
                _, current_account_id = self.current_credential()
            except (AccountProfileError, WorkspaceNotFoundError):
                current_account_id = None
            return [
                {"id": profile["id"], "label": profile["label"], "plan": profile["plan"],
                 "active": profile["account_id"] == current_account_id}
                for profile in registry["profiles"]
            ]

    def current_credential(self) -> tuple[bytes, str]:
        raw = self._read_bounded(
            self._codex, "auth.json", _MAX_CREDENTIAL_BYTES,
            harden_permissions=True,
        )
        return raw, _credential_account_id(raw)

    def current_credential_optional(self) -> bytes | None:
        try:
            return self._read_bounded(
                self._codex, "auth.json", _MAX_CREDENTIAL_BYTES,
                harden_permissions=True,
            )
        except WorkspaceNotFoundError:
            return None

    def refresh_current_if_saved(self) -> None:
        try:
            raw, account_id = self.current_credential()
        except (AccountProfileError, WorkspaceNotFoundError):
            return
        with self._lock:
            registry = self._registry()
            profile = next((item for item in registry["profiles"] if item["account_id"] == account_id), None)
            if profile is not None:
                self._profiles.atomic_write_bytes(f"{profile['id']}/auth.json", raw)

    def preserve_current_for_new_login(self) -> bytes:
        """Refresh the saved copy before detaching the current local sign-in."""
        raw, account_id = self.current_credential()
        with self._lock:
            registry = self._registry()
            profile = next(
                (item for item in registry["profiles"] if item["account_id"] == account_id),
                None,
            )
            if profile is None:
                raise AccountProfileError("Save the current account before adding another.")
            self._profiles.atomic_write_bytes(f"{profile['id']}/auth.json", raw)
        return raw

    def save_current(self, label: str, account_response: object) -> dict[str, object]:
        label = _label(label)
        if not isinstance(account_response, dict) or not isinstance(account_response.get("account"), dict):
            raise AccountProfileError("Sign in with ChatGPT before saving this account.")
        account = account_response["account"]
        if account.get("type") != "chatgpt":
            raise AccountProfileError("Only ChatGPT sign-ins can be saved.")
        plan = normalize_chatgpt_plan_type(account.get("planType"))
        raw, account_id = self.current_credential()
        with self._lock:
            registry = self._registry()
            profiles = registry["profiles"]
            existing = next((item for item in profiles if item["account_id"] == account_id), None)
            if existing is None:
                if len(profiles) >= _MAX_PROFILES:
                    raise AccountProfileError("The saved account limit has been reached.")
                profile_id = uuid4().hex
                self._profiles.create_directory(profile_id)
                existing = {"id": profile_id, "label": label, "account_id": account_id, "plan": plan}
                self._profiles.atomic_write_bytes(f"{profile_id}/auth.json", raw)
                profiles.append(existing)
            else:
                existing["label"] = label
                existing["plan"] = plan
                self._profiles.atomic_write_bytes(f"{existing['id']}/auth.json", raw)
            registry["active_profile_id"] = existing["id"]
            self._save_registry(registry)
            return {"id": existing["id"], "label": label, "plan": plan, "active": True}

    def target_credential(self, profile_id: str) -> tuple[bytes, str]:
        if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
            raise AccountProfileError("The saved account was not found.")
        with self._lock:
            registry = self._registry()
            profile = next((item for item in registry["profiles"] if item["id"] == profile_id), None)
            if profile is None:
                raise AccountProfileError("The saved account was not found.")
            raw = self._read_bounded(self._profiles, f"{profile_id}/auth.json", _MAX_CREDENTIAL_BYTES)
            if _credential_account_id(raw) != profile["account_id"]:
                raise AccountProfileError("The saved account identity has changed.")
            return raw, profile["account_id"]

    def refresh_saved_credential(
        self, profile_id: str, previous: bytes, refreshed: bytes,
    ) -> bool:
        """Keep a provider-refreshed token only if this profile is still unchanged."""
        if not isinstance(refreshed, bytes) or not refreshed or len(refreshed) > _MAX_CREDENTIAL_BYTES:
            return False
        with self._lock:
            current, account_id = self.target_credential(profile_id)
            if current != previous or _credential_account_id(refreshed) != account_id:
                return False
            if refreshed != current:
                self._profiles.atomic_write_bytes(f"{profile_id}/auth.json", refreshed)
            return True

    def activate_credential(self, raw: bytes) -> None:
        if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_CREDENTIAL_BYTES:
            raise AccountProfileError("The saved account file is invalid.")
        _credential_account_id(raw)
        self._codex.atomic_write_bytes("auth.json", raw)

    def restore_credential(self, raw: bytes | None) -> None:
        if raw is None:
            try:
                self._codex.unlink_regular_file("auth.json")
            except WorkspaceNotFoundError:
                pass
        else:
            # The prior App sign-in may have used an API key. Rollback restores
            # the exact bounded native file, even when it is not saveable as a
            # ChatGPT profile.
            if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_CREDENTIAL_BYTES:
                raise AccountProfileError("The previous account file is invalid.")
            self._codex.atomic_write_bytes("auth.json", raw)
        with self._lock:
            registry = self._registry()
            try:
                current_id = _credential_account_id(raw) if raw is not None else None
            except AccountProfileError:
                current_id = None
            registry["active_profile_id"] = next(
                (item["id"] for item in registry["profiles"] if item["account_id"] == current_id),
                None,
            )
            self._save_registry(registry)

    def commit_active(self, profile_id: str) -> None:
        with self._lock:
            registry = self._registry()
            if not any(item["id"] == profile_id for item in registry["profiles"]):
                raise AccountProfileError("The saved account was not found.")
            registry["active_profile_id"] = profile_id
            self._save_registry(registry)

    def remove_inactive(self, profile_id: str) -> None:
        if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
            raise AccountProfileError("The saved account was not found.")
        with self._lock:
            registry = self._registry()
            profile = next((item for item in registry["profiles"] if item["id"] == profile_id), None)
            if profile is None:
                raise AccountProfileError("The saved account was not found.")
            if any(item["id"] == profile_id and item["active"] for item in self.list_profiles()):
                raise AccountProfileError("Switch away from this account before removing it.")
            self._profiles.unlink_regular_file(f"{profile_id}/auth.json")
            self._profiles.remove_empty_directory(profile_id)
            registry["profiles"].remove(profile)
            if registry["active_profile_id"] == profile_id:
                registry["active_profile_id"] = None
            self._save_registry(registry)
