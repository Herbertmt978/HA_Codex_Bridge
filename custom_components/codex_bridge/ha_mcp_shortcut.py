"""Own one explicitly authorised HA MCP grant without retaining bearer tokens."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta
import hashlib
import math
import re
import secrets
from time import time
from typing import Any

from homeassistant.auth.models import TOKEN_TYPE_NORMAL, User
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.helpers import llm
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .bridge_api import BridgeApiClient, BridgeApiError
from .const import CONNECTION_TYPE_SUPERVISOR, DOMAIN

FIXED_URL = "http://homeassistant:8123/api/mcp/assist"
ACCESS_TOKEN_EXPIRATION = timedelta(hours=8)
REFRESH_INTERVAL = timedelta(hours=1)
_CAPABILITIES = frozenset({
    "mcp_admin_v1", "mcp_local_v1", "mcp_credentials_v1",
    "mcp_tool_permissions_v1", "mcp_credential_binding_v1",
    "assist_mcp_selection_v1",
})
_SERVER = re.compile(r"ha-assist-[a-f0-9]{12}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_STATES = {"preparing", "creating", "connected", "rotating", "disconnecting"}
_BINDING_CONFLICT = "mcp_credential_binding_conflict"
_ABSENT = {"mcp_server_not_found", "resource_gone"}
_RETRY = {"mcp_config_conflict", "runtime_mutation_conflict"}
_MESSAGES = {
    "not_connected": "Home Assistant MCP is not connected.",
    "unavailable": "Home Assistant MCP is unavailable. Check its integration and App settings.",
    "configured": "Home Assistant MCP is configured. Choose allowed tools and check the server status before use.",
    "paused": "Choose the permitted tools and enable the Home Assistant MCP server.",
    "connected": "Home Assistant MCP is ready with your selected tools.",
    "expired": "Home Assistant MCP access has expired. Refresh the existing authorisation.",
    "retry": "Home Assistant MCP could not update while the runtime is busy. Try again when idle.",
    "reauthorise": "Home Assistant MCP needs administrator authorisation again.",
    "cleanup_pending": "Home Assistant authorisation was revoked. Server cleanup will retry when available.",
    "invalid_journal": "Home Assistant MCP recovery needs attention. No new authorisation was created.",
}


class HaMcpShortcutError(RuntimeError):
    """Expose only fixed public errors, never upstream responses or token data."""

    def __init__(self, code: str = "unavailable") -> None:
        self.code = code if code in _MESSAGES else "unavailable"
        super().__init__(_MESSAGES[self.code])


class HaMcpShortcut:
    """Journal an entry-owned grant and bind every native edit to its old secret."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, client: BridgeApiClient | None, *,
        connection_type: str, supports_capability: Callable[[str], bool],
        selection_callback: Callable[[str, bool], Awaitable[None]],
    ) -> None:
        if not isinstance(entry_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", entry_id):
            raise HaMcpShortcutError()
        self.hass = hass
        self.client = client
        self.connection_type = connection_type
        self._supports = supports_capability
        self._select = selection_callback
        self._client_id = f"https://github.com/Herbertmt978/HA_Codex_Bridge/ha-mcp/{entry_id}"
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.ha_mcp.{entry_id}", private=True)
        self._record: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self._remove_timer: Callable[[], None] | None = None
        self._remove_revoke: Callable[[], None] | None = None
        self._remove_start_listener: Callable[[], None] | None = None
        self._invalid_journal = False
        self._loaded = False
        self._closed = False
        self._notice: str | None = None

    async def async_setup(self) -> None:
        """Restore/recover owned metadata; never create a server or refresh token."""
        async with self._lock:
            try:
                saved = await self._store.async_load()
                self._record = _parse_record(saved)
                self._loaded = True
            except Exception:  # noqa: BLE001 - private recovery must fail closed
                self._invalid_journal = True
                return
            if self._record is not None:
                if self._record["state"] != "connected":
                    await self._disconnect_locked()
                elif await self._owned_token() is None:
                    await self._disconnect_locked()
                    self._notice = "reauthorise"
                elif not self._environment():
                    if self._environment_pending():
                        self._notice = "unavailable"
                        if self.hass.state is not CoreState.running:
                            self._remove_start_listener = self.hass.bus.async_listen_once(
                                EVENT_HOMEASSISTANT_STARTED, self._after_start,
                            )
                        self._watch_revocation()
                    else:
                        await self._disconnect_locked()
                        self._notice = "reauthorise"
                else:
                    self._watch_revocation()
            self._remove_timer = async_track_time_interval(
                self.hass, self._tick, REFRESH_INTERVAL, cancel_on_shutdown=True,
            )

    async def async_close(self) -> None:
        """Stop this runtime's listeners while preserving the explicit grant."""
        self._closed = True
        if self._remove_start_listener is not None:
            self._remove_start_listener()
            self._remove_start_listener = None
        self._unwatch()
        if self._remove_timer is not None:
            self._remove_timer()
            self._remove_timer = None
        async with self._lock:
            pass

    async def async_remove(self) -> None:
        """Load and revoke on removal, even after unload, without starting timers."""
        try:
            async with self._lock:
                if not self._loaded:
                    try:
                        self._record = _parse_record(await self._store.async_load())
                        self._loaded = True
                    except Exception:  # noqa: BLE001 - do not infer authority from corrupt storage
                        self._invalid_journal = True
                if self._invalid_journal:
                    raise HaMcpShortcutError("invalid_journal")
                await self._disconnect_locked()
        finally:
            await self.async_close()

    async def async_status(self) -> dict[str, Any]:
        async with self._lock:
            if self._invalid_journal:
                return self._status("invalid_journal")
            if self._record is None:
                return self._status(self._notice or ("not_connected" if self._environment() else "unavailable"))
            if self._record["state"] != "connected":
                return self._status("cleanup_pending")
            token = await self._owned_token()
            if token is None:
                await self._disconnect_locked()
                self._notice = "reauthorise"
                return self._status("cleanup_pending" if self._record else "reauthorise")
            if not self._environment() and self._environment_pending():
                return self._status("unavailable")
            if not self._environment():
                await self._disconnect_locked()
                self._notice = "reauthorise"
                return self._status("cleanup_pending" if self._record else "reauthorise")
            if self._record["access_expires_at"] <= time():
                return self._status("expired")
            if self._notice == "retry":
                return self._status("retry")
            try:
                rows = await self.client.async_list_mcp()
            except BridgeApiError:
                return self._status("configured")
            if not isinstance(rows, list) or len(rows) > 128:
                return self._status("configured")
            matches = [item for item in rows if isinstance(item, Mapping) and item.get("name") == self._record["server_name"]]
            if len(matches) > 1:
                return self._status("configured")
            row = matches[0] if matches else None
            if row is None:
                await self._disconnect_locked()
                self._notice = "reauthorise"
                return self._status("cleanup_pending" if self._record else "reauthorise")
            if row.get("enabled") is not True:
                return self._status("paused")
            if (row.get("startup") == "ready" and row.get("credential_configured") is True
                    and row.get("tool_policy") == "selected" and type(row.get("tool_count")) is int
                    and row["tool_count"] > 0 and row.get("status_unavailable") is not True):
                return self._status("connected")
            return self._status("configured")

    async def async_connect(self, user: User, *, acknowledged: bool = False) -> dict[str, Any]:
        """Accept only the actual authenticated administrator supplied by HA HTTP."""
        async with self._lock:
            if acknowledged is not True or self._closed or self._invalid_journal:
                raise HaMcpShortcutError()
            try:
                current = await self.hass.auth.async_get_user(getattr(user, "id", ""))
            except Exception:  # noqa: BLE001 - auth storage failures confer no authority
                raise HaMcpShortcutError() from None
            if current is None or current is not user or current.is_active is not True or current.is_admin is not True:
                raise HaMcpShortcutError()
            if not self._environment():
                raise HaMcpShortcutError()
            if not self._loaded:
                try:
                    self._record = _parse_record(await self._store.async_load())
                    self._loaded = True
                except Exception:  # noqa: BLE001 - never overwrite an uncertain existing grant
                    self._invalid_journal = True
                    raise HaMcpShortcutError("invalid_journal") from None
            if self._record is not None:
                raise HaMcpShortcutError("cleanup_pending" if self._record["state"] != "connected" else "configured")
            name = "ha-assist-" + secrets.token_hex(6)
            self._record = {"owner_id": user.id, "token_id": None, "server_name": name,
                            "fingerprint": None, "pending_fingerprint": None,
                            "access_expires_at": 0.0, "pending_expires_at": None, "state": "preparing"}
            try:
                await self._save()
                self._loaded = True
                token = await self.hass.auth.async_create_refresh_token(
                    current, client_id=self._client_id, client_name=self._token_name(name),
                    token_type=TOKEN_TYPE_NORMAL, access_token_expiration=ACCESS_TOKEN_EXPIRATION,
                )
                self._record["token_id"] = token.id
                await self._save()
                jwt = self.hass.auth.async_create_access_token(token)
                self._record.update(fingerprint=_fingerprint(jwt), access_expires_at=time() + ACCESS_TOKEN_EXPIRATION.total_seconds(), state="creating")
                await self._save()
                await self.client.async_add_mcp({
                    "name": name, "url": FIXED_URL, "local": True,
                    "local_acknowledged": True, "authentication": {"mode": "bearer", "token": jwt},
                    "auth_acknowledged": True, "require_tool_selection": True,
                })
                await self._select(name, True)
                self._record["state"] = "connected"
                await self._save()
                self._watch_revocation()
                self._notice = None
                return self._status("configured")
            except asyncio.CancelledError:
                await self._disconnect_locked()
                raise
            except Exception as error:  # noqa: BLE001 - no secret-bearing exception may escape
                retry = isinstance(error, BridgeApiError) and error.code in _RETRY
                await self._disconnect_locked()
                raise HaMcpShortcutError("retry" if retry else "unavailable") from None

    async def async_refresh(self) -> dict[str, Any]:
        """Rotate access from an existing owned grant; never renew its authority."""
        async with self._lock:
            if self._closed or self._invalid_journal:
                raise HaMcpShortcutError()
            if self._record is None:
                raise HaMcpShortcutError("reauthorise")
            if self._record["state"] != "connected":
                await self._disconnect_locked()
                return self._status("cleanup_pending" if self._record else "reauthorise")
            token = await self._owned_token()
            if not self._environment() and self._environment_pending() and token is not None:
                raise HaMcpShortcutError()
            if not self._environment() or token is None:
                await self._disconnect_locked()
                self._notice = "reauthorise"
                return self._status("cleanup_pending" if self._record else "reauthorise")
            try:
                jwt = self.hass.auth.async_create_access_token(token)
                self._record.update(state="rotating", pending_fingerprint=_fingerprint(jwt),
                                    pending_expires_at=time() + ACCESS_TOKEN_EXPIRATION.total_seconds())
                await self._save()
                await self.client.async_replace_mcp_credential(self._record["server_name"], {
                    "authentication": {"mode": "bearer", "token": jwt}, "auth_acknowledged": True,
                    "expected_url": FIXED_URL, "expected_token_sha256": self._record["fingerprint"],
                })
                self._record.update(state="connected", fingerprint=self._record["pending_fingerprint"],
                                    access_expires_at=self._record["pending_expires_at"],
                                    pending_fingerprint=None, pending_expires_at=None)
                await self._save()
                self._notice = None
                return self._status("configured")
            except asyncio.CancelledError:
                await self._disconnect_locked()
                raise
            except Exception as error:  # noqa: BLE001 - failures have fixed public classifications
                if isinstance(error, BridgeApiError) and error.code in _RETRY:
                    self._record.update(state="connected", pending_fingerprint=None, pending_expires_at=None)
                    await self._save()
                    self._notice = "retry"
                    return self._status("retry")
                await self._disconnect_locked()
                self._notice = "reauthorise"
                raise HaMcpShortcutError("reauthorise") from None

    async def async_disconnect(self) -> dict[str, Any]:
        async with self._lock:
            if self._invalid_journal:
                raise HaMcpShortcutError("invalid_journal")
            await self._disconnect_locked()
            self._notice = None
            return self._status("cleanup_pending" if self._record else "not_connected")

    def _environment(self) -> bool:
        try:
            return (self.connection_type == CONNECTION_TYPE_SUPERVISOR
                    and all(self._supports(capability) is True for capability in _CAPABILITIES)
                    and len(self.hass.config_entries.async_loaded_entries("mcp_server")) == 1
                    and any(api.id == llm.LLM_API_ASSIST for api in llm.async_get_apis(self.hass)))
        except Exception:  # noqa: BLE001 - unavailable discovery cannot confer authority
            return False

    def _environment_pending(self) -> bool:
        """An enabled MCP entry may be between unload and setup during reload."""
        try:
            if (self.connection_type != CONNECTION_TYPE_SUPERVISOR
                    or not all(self._supports(capability) is True for capability in _CAPABILITIES)):
                return False
            entries = self.hass.config_entries.async_entries(
                "mcp_server", include_ignore=False, include_disabled=False,
            )
            if len(entries) != 1:
                return False
            return entries[0].state in {
                ConfigEntryState.NOT_LOADED,
                ConfigEntryState.UNLOAD_IN_PROGRESS,
                ConfigEntryState.SETUP_IN_PROGRESS,
                ConfigEntryState.SETUP_RETRY,
            }
        except Exception:  # noqa: BLE001 - uncertain entry state cannot preserve authority
            return False

    async def _after_start(self, _event: Any) -> None:
        self._remove_start_listener = None
        if not self._closed:
            await self.async_status()

    async def _owned_token(self) -> Any:
        record = self._record
        if record is None:
            return None
        token = self.hass.auth.async_get_refresh_token(record["token_id"]) if record["token_id"] else None
        owner = await self.hass.auth.async_get_user(record["owner_id"])
        if (token is None or owner is None or owner.is_active is not True or owner.is_admin is not True
                or not self._matches_token(token) or token.user is not owner
                or (token.expire_at is not None and token.expire_at <= time())):
            return None
        try:
            self.hass.auth.async_validate_refresh_token(token)
        except Exception:  # noqa: BLE001 - provider details are not public
            return None
        return token

    def _matches_token(self, token: Any) -> bool:
        record = self._record
        return (record is not None and token.user.id == record["owner_id"]
                and token.client_id == self._client_id and token.token_type == TOKEN_TYPE_NORMAL
                and token.client_name == self._token_name(record["server_name"]))

    async def _disconnect_locked(self) -> None:
        record = self._record
        if record is None:
            return
        self._unwatch()
        # Revocation precedes remote cleanup, even if journal storage is unavailable.
        token = self.hass.auth.async_get_refresh_token(record["token_id"]) if record["token_id"] else None
        if token is not None and self._matches_token(token):
            self.hass.auth.async_remove_refresh_token(token)
        if record["token_id"] is None:
            owner = await self.hass.auth.async_get_user(record["owner_id"])
            if owner is not None:
                for candidate in tuple(owner.refresh_tokens.values()):
                    if self._matches_token(candidate):
                        self.hass.auth.async_remove_refresh_token(candidate)
        record["state"] = "disconnecting"
        pending = False
        try:
            await self._save()
        except Exception:  # noqa: BLE001 - still revoke and detach on storage failure
            pending = True
        try:
            await self._select(record["server_name"], False)
        except Exception:  # noqa: BLE001 - preserve retryable cleanup journal
            pending = True
        digests = list(dict.fromkeys(value for value in (record["pending_fingerprint"], record["fingerprint"]) if value))
        if digests:
            if not self._supports("mcp_credential_binding_v1"):
                pending = True
            else:
                for digest in digests:
                    try:
                        await self.client.async_remove_managed_mcp(record["server_name"], {
                            "expected_url": FIXED_URL, "expected_token_sha256": digest,
                        })
                        break
                    except BridgeApiError as error:
                        if error.code in _ABSENT:
                            break
                        if error.code == _BINDING_CONFLICT:
                            continue
                        pending = True
                        break
                    except Exception:  # noqa: BLE001 - no upstream error may expose a secret
                        pending = True
                        break
        if not pending:
            self._record = None
            try:
                await self._save()
            except Exception:  # noqa: BLE001 - retained journal allows idempotent recovery
                self._record = record

    async def _save(self) -> None:
        await self._store.async_save({"record": dict(self._record) if self._record else None})

    def _status(self, state: str) -> dict[str, Any]:
        return {"code": state, "state": state, "message": _MESSAGES[state], "available": state == "connected",
                "configured": self._record is not None and self._record["state"] == "connected",
                "server_name": self._record["server_name"] if self._record else None,
                "requires_tool_selection": True}

    @staticmethod
    def _token_name(name: str) -> str:
        return f"Codex Bridge HA Assist ({name})"

    def _unwatch(self) -> None:
        if self._remove_revoke is not None:
            self._remove_revoke()
            self._remove_revoke = None

    def _watch_revocation(self) -> None:
        self._unwatch()
        if self._record and self._record["token_id"]:
            self._remove_revoke = self.hass.auth.async_register_revoke_token_callback(
                self._record["token_id"], self._revoked,
            )

    def _revoked(self) -> None:
        if not self._closed:
            self.hass.async_create_task(self.async_disconnect())

    async def _tick(self, _now: Any) -> None:
        if self._closed:
            return
        try:
            if self._record is not None and self._record["state"] != "connected":
                await self.async_disconnect()
            elif self._record is not None:
                await self.async_refresh()
        except HaMcpShortcutError:
            pass


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_record(saved: Any) -> dict[str, Any] | None:
    if saved is None or saved == {"record": None}:
        return None
    if not isinstance(saved, dict) or set(saved) != {"record"} or not isinstance(saved["record"], dict):
        raise ValueError("Invalid private journal")
    record = saved["record"]
    if set(record) != {"owner_id", "token_id", "server_name", "fingerprint", "pending_fingerprint", "access_expires_at", "pending_expires_at", "state"}:
        raise ValueError("Invalid private journal")
    if (not isinstance(record["owner_id"], str) or not 1 <= len(record["owner_id"]) <= 128
            or not isinstance(record["server_name"], str) or not _SERVER.fullmatch(record["server_name"])
            or not isinstance(record["state"], str) or record["state"] not in _STATES
            or (record["token_id"] is not None and (not isinstance(record["token_id"], str) or not 1 <= len(record["token_id"]) <= 128))):
        raise ValueError("Invalid private journal")
    for key in ("fingerprint", "pending_fingerprint"):
        if record[key] is not None and (not isinstance(record[key], str) or not _DIGEST.fullmatch(record[key])):
            raise ValueError("Invalid private journal")
    for key in ("access_expires_at", "pending_expires_at"):
        value = record[key]
        if value is None and key == "pending_expires_at":
            continue
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 32503680000:
            raise ValueError("Invalid private journal")
    if record["state"] in {"connected", "rotating"} and (not record["token_id"] or not record["fingerprint"] or not record["access_expires_at"]):
        raise ValueError("Invalid private journal")
    if record["state"] == "rotating" and (not record["pending_fingerprint"] or not record["pending_expires_at"]):
        raise ValueError("Invalid private journal")
    return dict(record)
