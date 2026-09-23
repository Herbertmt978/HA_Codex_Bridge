"""Safe, native app-server management for Home Assistant MCP connections.

Public streamable HTTPS servers support native OAuth. Approved local endpoints
and static credentials use the confined relay. This is not a generic Codex
configuration editor: stdio commands, environment variables, routing headers
and arbitrary config keys are rejected.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import hashlib
import hmac
import ipaddress
import re
import secrets
import socket
from threading import RLock
from typing import Callable, Protocol
from urllib.parse import SplitResult, urlsplit, urlunsplit

from .codex_app_server import mcp_config_is_disabled
from .mcp_local_policy import LocalMcpError
from .mcp_relay import McpRelay, McpRelayRecoveryError
from .mcp_credentials import parse_credential


_MAX_SERVERS = 32
_MAX_STATUS_PAGES = 4
_MAX_NAME_BYTES = 64
_MAX_URL_BYTES = 2048
_MAX_PUBLIC_FIELD_BYTES = 512
_MAX_OAUTH_URL_BYTES = 8192
_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z", re.ASCII)
_DNS_LABEL_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z", re.ASCII)
_RESERVED_HOSTS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "local",
        "invalid",
        "test",
        "example",
        "home.arpa",
    }
)
_CREDENTIAL_PATTERN = re.compile(
    r"(?:bearer\s+|(?:api|access|refresh)[_-]?token|api[_-]?key|"
    r"client[_-]?secret|password|private[_-]?key)\s*[:=]",
    re.IGNORECASE,
)
MCP_DISABLED_MESSAGE = "Enable MCP in the Codex Bridge App configuration and restart"


class McpManagerError(RuntimeError):
    """Base error with a fixed public code and no provider-controlled detail."""

    code = "mcp_unavailable"
    retryable = True


class McpValidationError(McpManagerError):
    code = "mcp_request_invalid"
    retryable = False


class McpNotFoundError(McpManagerError):
    code = "mcp_server_not_found"
    retryable = False


class McpConflictError(McpManagerError):
    code = "mcp_config_conflict"
    retryable = True


class McpUnavailableError(McpManagerError):
    code = "mcp_unavailable"
    retryable = True


class McpElicitationUnavailableError(McpUnavailableError):
    """MCP mutations are unsafe until decline-only elicitation is installed."""

    code = "mcp_elicitation_unavailable"


class McpDisabledError(McpUnavailableError):
    """MCP administration is disabled by the Home Assistant App option."""

    code = "mcp_disabled"
    retryable = False


class McpLocalDisabledError(McpUnavailableError):
    code = "mcp_local_disabled"
    retryable = False


class McpRecoveryRequiredError(McpUnavailableError):
    code = "mcp_restart_required"
    retryable = False


class McpProtocolError(McpManagerError):
    code = "mcp_runtime_invalid"
    retryable = True


class _Lease(Protocol):
    def release(self) -> None: ...


class _RuntimeGate(Protocol):
    def acquire_config_mutation(self) -> _Lease: ...


class _AppServer(Protocol):
    def request(
        self,
        method: str,
        params: object = None,
        *,
        timeout_seconds: float | None = None,
    ) -> object: ...


def _resolve_host(host: str) -> tuple[str, ...]:
    """Return DNS addresses when available, without making DNS a hard dependency."""

    try:
        records = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return ()
    return tuple(
        dict.fromkeys(
            record[4][0]
            for record in records
            if isinstance(record[4], tuple) and record[4]
        )
    )


def _has_non_public_dns_answer(
    host: str, resolver: Callable[[str], tuple[str, ...]]
) -> bool:
    """Best-effort DNS SSRF screen; egress controls still own DNS-rebind safety."""

    # DNS answers are a point-in-time observation and can change after this
    # check. Resolver failures intentionally remain usable so an unavailable
    # DNS service cannot turn a valid public configuration into an outage.
    try:
        answers = resolver(host)
    except (OSError, ValueError):
        return False
    for answer in answers:
        try:
            address = ipaddress.ip_address(answer)
        except ValueError:
            continue
        if not address.is_global:
            return True
    return False


@dataclass(frozen=True, slots=True)
class McpServerDefinition:
    name: str
    url: str
    oauth_client_id: str | None = None
    oauth_resource: str | None = None
    local: bool = False
    relayed: bool = False
    auth_mode: str = "none"
    credential_configured: bool = False
    enabled: bool = True

    def config_value(self) -> dict[str, object]:
        if self.local or self.relayed:
            # Local upstream URLs must never become native Codex destinations.
            raise McpValidationError()
        value: dict[str, object] = {"url": self.url}
        if not self.enabled:
            value["enabled"] = False
        if self.oauth_client_id is not None:
            value["oauth_client_id"] = self.oauth_client_id
        if self.oauth_resource is not None:
            value["oauth_resource"] = self.oauth_resource
        return value


class McpManager:
    """Serialize native MCP configuration and expose a secret-free projection."""

    def __init__(
        self,
        app_server: _AppServer,
        runtime_gate: _RuntimeGate,
        *,
        request_timeout_seconds: float = 30.0,
        resolver: Callable[[str], tuple[str, ...]] = _resolve_host,
        enabled: bool = False,
        relay: McpRelay | None = None,
    ) -> None:
        if request_timeout_seconds <= 0:
            raise ValueError("MCP request timeout must be positive")
        if type(enabled) is not bool:
            raise ValueError("MCP enabled state must be a boolean")
        self._app_server = app_server
        self._runtime_gate = runtime_gate
        self._request_timeout_seconds = float(request_timeout_seconds)
        self._resolver = resolver
        self._enabled = enabled
        self._relay = relay
        self._lock = RLock()
        self._startup: dict[str, tuple[str, str | None]] = {}
        self._active_names: frozenset[str] = frozenset()
        self._oauth_completion: dict[str, bool] = {}
        self._elicitation_handler_registered = False
        self._startup_config_sanitized = False
        self._revision_key = secrets.token_bytes(32)
        self._mutation_serial = 0
        self._recovery_required = False
        self._register_callbacks()

    @property
    def elicitation_handler_registered(self) -> bool:
        """Whether the app-server has a safe fail-closed MCP request handler."""

        return self._elicitation_handler_registered

    @property
    def enabled(self) -> bool:
        """Whether the administrator explicitly enabled outbound MCP."""

        return self._enabled

    def is_active_server(self, name: str) -> bool:
        """Consult the last validated config without a re-entrant app-server call."""

        # The callback may arrive while a config mutation holds the manager
        # lock and waits for app-server I/O. Read an immutable snapshot here.
        return self._enabled and name in self._active_names and not self._recovery_required

    def list_servers(self) -> list[dict[str, object]]:
        """Return only configured safe servers and bounded native status metadata."""

        self._require_enabled()
        with self._lock:
            definitions, version = self._read_definitions()
            status_unavailable = False
            try:
                statuses = self._read_statuses()
            except McpUnavailableError:
                # Keep saved connections manageable while discovery is failing.
                # Unknown counts are not evidence that a server exposes no tools.
                statuses = {}
                status_unavailable = True
            views: list[dict[str, object]] = []
            for definition in definitions.values():
                status = statuses.get(definition.name, {})
                startup, failure = self._startup.get(definition.name, ("unknown", None))
                auth_status = _enum(
                    status.get("authStatus"),
                    {"unsupported", "notLoggedIn", "bearerToken", "oAuth"},
                    "unknown",
                )
                server_info = status.get("serverInfo")
                info = server_info if isinstance(server_info, Mapping) else {}
                tools = status.get("tools")
                resources = status.get("resources")
                templates = status.get("resourceTemplates")
                view: dict[str, object] = {
                    "name": definition.name,
                    "transport": "streamable_http",
                    "network": "local" if definition.local else "public",
                    "endpoint": _endpoint_display(definition.url, private_path=definition.relayed),
                    "auth": definition.auth_mode if definition.relayed else _auth_display(auth_status),
                    "startup": startup,
                    "tool_count": _bounded_collection_size(tools),
                    "resource_count": _bounded_collection_size(resources)
                    + _bounded_collection_size(templates),
                    "enabled": definition.enabled,
                    "revision": self._revision(definition.name, version),
                }
                if not definition.enabled:
                    view.update(startup="paused", tool_count=0, resource_count=0)
                elif status_unavailable:
                    view.update(startup="unknown", status_unavailable=True)
                if definition.auth_mode != "none":
                    view["credential_configured"] = definition.credential_configured
                title = _safe_display_text(info.get("title"), 160)
                server_version = _safe_display_text(info.get("version"), 64)
                if title is not None:
                    view["title"] = title
                if server_version is not None:
                    view["version"] = server_version
                if failure is not None and definition.enabled:
                    view["failure"] = failure
                if definition.name in self._oauth_completion:
                    view["oauth_complete"] = self._oauth_completion[definition.name]
                views.append(view)
            return views

    def create_server(
        self,
        *,
        name: object,
        url: object,
        oauth_client_id: object = None,
        oauth_resource: object = None,
        local: bool = False,
        local_acknowledged: bool = False,
        authentication: object = None,
        auth_acknowledged: bool = False,
    ) -> dict[str, object]:
        self._require_enabled()
        self._require_elicitation_handler()
        normalized_name = _validate_name(name)
        try:
            credential = parse_credential(authentication)
        except LocalMcpError:
            raise McpValidationError() from None
        static_auth = credential.mode != "none"
        if (type(auth_acknowledged) is not bool or auth_acknowledged != static_auth
                or (static_auth and (oauth_client_id is not None or oauth_resource is not None))):
            raise McpValidationError()
        if static_auth and self._relay is None:
            raise McpUnavailableError()
        if type(local) is not bool or type(local_acknowledged) is not bool:
            raise McpValidationError()
        if local:
            if self._relay is None or not self._relay.local_enabled:
                raise McpLocalDisabledError()
            if not local_acknowledged or oauth_client_id is not None or oauth_resource is not None:
                raise McpValidationError()
            definition = None
        else:
            if local_acknowledged:
                raise McpValidationError()
            definition = McpServerDefinition(
                name=normalized_name,
                url=_validate_https_url(url, resolver=self._resolver),
                oauth_client_id=_validate_public_field(oauth_client_id),
                oauth_resource=_validate_public_field(oauth_resource),
            )
        with self._mutation_lease():
            with self._lock:
                definitions, version = self._read_definitions()
                if normalized_name in definitions:
                    raise McpConflictError()
                if len(definitions) >= _MAX_SERVERS:
                    raise McpValidationError()
                if local or static_auth:
                    try:
                        canonical = self._relay.add(normalized_name, url, local=local, credential=credential)
                    except LocalMcpError:
                        raise McpValidationError() from None
                    except Exception:
                        raise McpUnavailableError() from None
                    definition = McpServerDefinition(normalized_name, canonical, local=local, relayed=True,
                                                     auth_mode=credential.mode, credential_configured=credential.configured)
                assert definition is not None
                try:
                    self._write_config_value(
                        key_path=f"mcp_servers.{definition.name}",
                        value=self._native_value(definition),
                        version=version,
                    )
                except McpManagerError:
                    if definition.relayed:
                        try:
                            self._relay.remove(normalized_name)
                        except Exception:
                            raise McpUnavailableError() from None
                    raise
                self._reload()
                self._active_names = self._active_names | {definition.name}
        return self._view_for_created(definition)

    def _revision(self, name: str, version: str) -> str:
        # This process owns relay mutations; native versions also detect edits
        # made outside it. A fresh key invalidates forms retained across restart.
        value = f"{name}\0{version}\0{self._mutation_serial}".encode()
        return hmac.new(self._revision_key, value, hashlib.sha256).hexdigest()

    def _check_revision(self, name: str, version: str, revision: object) -> None:
        if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{64}", revision):
            raise McpValidationError()
        if not hmac.compare_digest(self._revision(name, version), revision):
            raise McpConflictError()

    def _require_recovery(self, name: str) -> None:
        self._recovery_required = True
        self._active_names = frozenset()
        if self._relay is not None:
            self._relay.deactivate(name)
        # No active/queued turn exists while the configuration lease is held.
        close = getattr(self._runtime_gate, "close", None)
        if callable(close):
            close()

    def _apply_definition(self, previous: McpServerDefinition, updated: McpServerDefinition,
                          version: str) -> str:
        """Write and reload, restoring the old definition if the runtime rejects it."""
        try:
            self._write_config_value(key_path=f"mcp_servers.{updated.name}",
                                     value=self._native_value(updated), version=version)
            self._reload()
            definitions, current_version = self._read_definitions()
            if definitions.get(updated.name) != updated:
                raise McpConflictError()
        except McpManagerError:
            self._active_names = self._active_names - {previous.name}
            try:
                # Read even after a failed write: a timeout may have committed.
                definitions, current_version = self._read_definitions()
                current = definitions.get(previous.name)
                if current == updated:
                    self._write_config_value(key_path=f"mcp_servers.{previous.name}",
                                             value=self._native_value(previous), version=current_version)
                elif current != previous:
                    raise McpConflictError()
                else:
                    self._native_value(previous)
                self._reload()
                # A reconciliation read may have observed the attempted write.
                # Keep the callback snapshot aligned with the restored config.
                if previous.enabled:
                    self._active_names = self._active_names | {previous.name}
                else:
                    self._active_names = self._active_names - {previous.name}
            except McpManagerError:
                self._require_recovery(previous.name)
                raise McpRecoveryRequiredError() from None
            raise
        self._mutation_serial += 1
        self._startup.pop(updated.name, None)
        self._oauth_completion.pop(updated.name, None)
        return current_version

    def set_server_enabled(self, name: object, *, enabled: object, revision: object) -> dict[str, object]:
        self._require_enabled()
        self._require_elicitation_handler()
        normalized = _validate_name(name)
        if type(enabled) is not bool:
            raise McpValidationError()
        with self._mutation_lease(), self._lock:
            definitions, version = self._read_definitions()
            previous = definitions.get(normalized)
            if previous is None:
                raise McpNotFoundError()
            self._check_revision(normalized, version, revision)
            updated = replace(previous, enabled=enabled)
            if previous.enabled != enabled:
                version = self._apply_definition(previous, updated, version)
            if enabled:
                self._active_names = self._active_names | {normalized}
            else:
                self._active_names = self._active_names - {normalized}
            view = self._view_for_created(updated)
            view["revision"] = self._revision(normalized, version)
            return view

    def edit_server(self, name: object, *, url: object, revision: object,
                    endpoint_acknowledged: bool = False, credential_action: object = None,
                    authentication: object = None) -> dict[str, object]:
        """Destination edits are explicit, write-only and require a paused connection."""
        self._require_enabled()
        self._require_elicitation_handler()
        normalized = _validate_name(name)
        if endpoint_acknowledged is not True or credential_action not in ("keep", "replace", "remove"):
            raise McpValidationError()
        try:
            credential = parse_credential(authentication)
        except LocalMcpError:
            raise McpValidationError() from None
        if (credential_action == "replace") != (credential.mode != "none"):
            raise McpValidationError()
        with self._mutation_lease(), self._lock:
            definitions, version = self._read_definitions()
            previous = definitions.get(normalized)
            if previous is None:
                raise McpNotFoundError()
            self._check_revision(normalized, version, revision)
            if previous.enabled:
                raise McpConflictError()
            if previous.relayed:
                try:
                    canonical = self._relay.edit_paused_endpoint(normalized, url,
                        credential_action=credential_action, credential=credential)
                    metadata = self._relay.metadata(normalized)
                except McpRelayRecoveryError:
                    self._require_recovery(normalized)
                    raise McpRecoveryRequiredError() from None
                except LocalMcpError:
                    raise McpValidationError() from None
                except Exception:
                    raise McpUnavailableError() from None
                updated = replace(previous, url=canonical, auth_mode=metadata["auth"],
                                  credential_configured=metadata["credential_configured"])
                self._mutation_serial += 1
            else:
                # Native OAuth is scoped to server name and URL. The explicit
                # keep decision permits its existing store; a new URL may need login.
                if credential_action != "keep":
                    raise McpValidationError()
                updated = replace(previous, url=_validate_https_url(url, resolver=self._resolver))
                version = self._apply_definition(previous, updated, version)
            self._startup.pop(normalized, None)
            self._oauth_completion.pop(normalized, None)
            view = self._view_for_created(updated)
            view["revision"] = self._revision(normalized, version)
            return view

    def remove_server(self, name: object) -> None:
        self._require_enabled()
        normalized_name = _validate_name(name)
        with self._mutation_lease():
            with self._lock:
                definitions, version = self._read_definitions()
                if normalized_name not in definitions:
                    raise McpNotFoundError()
                # Codex's native config writer treats a replace with null as
                # deletion of the key.  Keep this operation inside the same CAS
                # write/reload sequence as creation.
                self._write_config_value(
                    key_path=f"mcp_servers.{normalized_name}",
                    value=None,
                    version=version,
                )
                if definitions[normalized_name].relayed:
                    try:
                        self._relay.remove(normalized_name)
                    except Exception:
                        raise McpUnavailableError() from None
                self._reload()
                self._active_names = self._active_names - {normalized_name}
                self._startup.pop(normalized_name, None)
                self._oauth_completion.pop(normalized_name, None)

    def start_oauth_login(self, name: object) -> str:
        self._require_enabled()
        self._require_elicitation_handler()
        normalized_name = _validate_name(name)
        with self._mutation_lease():
            with self._lock:
                definitions, _version = self._read_definitions()
                if normalized_name not in definitions:
                    raise McpNotFoundError()
                if definitions[normalized_name].relayed:
                    raise McpValidationError()
                result = self._request(
                    "mcpServer/oauth/login",
                    {"name": normalized_name, "timeoutSecs": 300},
                )
        if not isinstance(result, Mapping):
            raise McpProtocolError()
        authorization_url = _validate_oauth_authorization_url(
            result.get("authorizationUrl"), resolver=self._resolver
        )
        # This is intentionally returned directly and is never retained in
        # manager state, events, diagnostics, or logs.
        return authorization_url

    def disable_all_servers(self) -> None:
        """Delete only native MCP configuration while disabled.

        The production app-server starts with explicit disabled server
        overrides and verifies them before exposing application requests. This native write removes stale user configuration without
        parsing or rewriting unrelated plugin, skill, or instruction settings.
        """

        if self._enabled:
            raise McpConflictError()
        with self._mutation_lease():
            with self._lock:
                result = self._request("config/read", {"includeLayers": True})
                if not isinstance(result, Mapping):
                    raise McpProtocolError()
                config = result.get("config")
                if not mcp_config_is_disabled(config):
                    raise McpProtocolError()
                version = _optional_user_config_version(result.get("layers"))
                if version is None:
                    self._active_names = frozenset()
                    self._startup.clear()
                    self._oauth_completion.clear()
                    return
                self._write_config_value(
                    key_path="mcp_servers",
                    value=None,
                    version=version,
                )
                self._reload()
                self._active_names = frozenset()
                self._startup.clear()
                self._oauth_completion.clear()

    def sanitize_startup_servers(self) -> None:
        """Prepare the persisted MCP root before an enabled generation can use it.

        The app server always starts with a session override that masks native
        MCP configuration.  When MCP is enabled, replace the user-layer root
        with exactly the entries that pass public HTTPS/OAuth or approved
        local relay validation, then reload it.  Nothing can unmask that generation until
        both operations succeed.
        """

        if not self._enabled:
            self.disable_all_servers()
            return
        with self._mutation_lease():
            with self._lock:
                self._startup_config_sanitized = False
                result = self._request("config/read", {"includeLayers": True})
                definitions, version = _validated_user_definitions(
                    result,
                    resolver=self._resolver,
                    relay=self._relay,
                )
                if self._relay is not None:
                    try:
                        self._relay.retain({name for name, item in definitions.items() if item.relayed})
                    except Exception:
                        raise McpUnavailableError() from None
                self._write_config_value(
                    key_path="mcp_servers",
                    value={
                        definition.name: self._native_value(definition)
                        for definition in definitions.values()
                    },
                    version=version,
                )
                self._reload()
                self._startup_config_sanitized = True
                self._active_names = frozenset(
                    name for name, definition in definitions.items() if definition.enabled
                )

    def activate_validated_mcp_config(self) -> None:
        """Drop the bootstrap mask only after successful startup sanitation."""

        self._require_enabled()
        with self._lock:
            if not self._startup_config_sanitized:
                raise McpUnavailableError()
            activate = getattr(self._app_server, "activate_validated_mcp_config", None)
            if not callable(activate):
                raise McpUnavailableError()
        # A clean generation can emit MCP startup callbacks before this
        # synchronous handoff returns.  Those callbacks update manager state,
        # so never retain the manager lock across the client restart.
        try:
            activate()
        except Exception:
            raise McpUnavailableError() from None

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise McpDisabledError()
        if self._recovery_required:
            raise McpRecoveryRequiredError()

    def _require_elicitation_handler(self) -> None:
        if not self._elicitation_handler_registered:
            raise McpElicitationUnavailableError()

    def _view_for_created(self, definition: McpServerDefinition) -> dict[str, object]:
        view = {
            "name": definition.name,
            "transport": "streamable_http",
            "endpoint": _endpoint_display(definition.url, private_path=definition.relayed),
            "auth": definition.auth_mode if definition.relayed else "oauth" if definition.oauth_client_id else "none",
            "startup": "starting",
            "tool_count": 0,
            "resource_count": 0,
            "network": "local" if definition.local else "public",
            "enabled": definition.enabled,
        }
        if not definition.enabled:
            view["startup"] = "paused"
        if definition.auth_mode != "none":
            view["credential_configured"] = definition.credential_configured
        return view

    def replace_credential(self, name: object, authentication: object = None, *, acknowledged: bool = False, remove: bool = False) -> dict[str, object]:
        self._require_enabled()
        normalized = _validate_name(name)
        try:
            credential = None if remove else parse_credential(authentication)
        except LocalMcpError:
            raise McpValidationError() from None
        if not remove and (acknowledged is not True or credential.mode == "none"):
            raise McpValidationError()
        with self._mutation_lease():
            with self._lock:
                definitions, _version = self._read_definitions()
                definition = definitions.get(normalized)
                if definition is None:
                    raise McpNotFoundError()
                if not definition.relayed or definition.auth_mode == "none" or self._relay is None:
                    raise McpValidationError()
                # Rotation may persist before reload fails. Invalidate open
                # destination forms even when its final outcome is uncertain.
                self._mutation_serial += 1
                try:
                    self._relay.replace_credential(normalized, credential)
                    self._native_value(definition)
                except Exception:
                    raise McpUnavailableError() from None
                self._reload()
                self._startup.pop(normalized, None)
        return {"name": normalized, "auth": credential.mode if credential else definition.auth_mode,
                "credential_configured": credential is not None}

    def _native_value(self, definition: McpServerDefinition) -> dict[str, object]:
        if definition.relayed:
            if self._relay is None:
                raise McpLocalDisabledError()
            try:
                value = self._relay.native_config(definition.name, active=definition.enabled)
                if not definition.enabled:
                    value["enabled"] = False
                return value
            except Exception:
                raise McpUnavailableError() from None
        return definition.config_value()

    def _register_callbacks(self) -> None:
        register_notification = getattr(
            self._app_server, "register_notification_handler", None
        )
        if callable(register_notification):
            try:
                register_notification(
                    "mcpServer/startupStatus/updated", self._on_startup_status
                )
                register_notification(
                    "mcpServer/oauthLogin/completed", self._on_oauth_completed
                )
            except Exception:
                # A missing method is handled by request operations/readiness;
                # provider-controlled error text must not escape this boundary.
                pass
        register_request = getattr(self._app_server, "register_request_handler", None)
        if callable(register_request):
            try:
                register_request(
                    "mcpServer/elicitation/request", self._decline_elicitation
                )
            except Exception:
                self._elicitation_handler_registered = False
            else:
                self._elicitation_handler_registered = True

    def _on_startup_status(self, notification: object) -> None:
        params = _callback_params(notification)
        name = params.get("name")
        status = params.get("status")
        if not _valid_name(name) or status not in {
            "starting",
            "ready",
            "failed",
            "cancelled",
        }:
            return
        failure = (
            "reauthentication_required"
            if params.get("failureReason") == "reauthenticationRequired"
            else None
        )
        with self._lock:
            self._startup[name] = (status, failure)

    def _on_oauth_completed(self, notification: object) -> None:
        params = _callback_params(notification)
        name = params.get("name")
        success = params.get("success")
        if not _valid_name(name) or type(success) is not bool:
            return
        with self._lock:
            self._oauth_completion[name] = success

    @staticmethod
    def _decline_elicitation(_request: object) -> dict[str, str]:
        # Keep the default fail-closed until the attended RuntimeBroker
        # registers its exact-turn interaction handler.
        return {"action": "decline"}

    def _read_definitions(self) -> tuple[dict[str, McpServerDefinition], str]:
        result = self._request("config/read", {"includeLayers": True})
        if not isinstance(result, Mapping):
            raise McpProtocolError()
        version = _user_config_version(result.get("layers"))
        config = result.get("config")
        if not isinstance(config, Mapping):
            raise McpProtocolError()
        raw_servers = config.get("mcp_servers", {})
        if raw_servers is None:
            raw_servers = {}
        if not isinstance(raw_servers, Mapping) or len(raw_servers) > _MAX_SERVERS:
            raise McpProtocolError()
        definitions: dict[str, McpServerDefinition] = {}
        for raw_name, raw_value in raw_servers.items():
            try:
                definition = _definition_from_config(
                    raw_name, raw_value, relay=self._relay, effective=True,
                )
            except McpValidationError:
                # Unsafe existing native config is never reflected back into HA.
                continue
            definitions[definition.name] = definition
        self._active_names = frozenset(
            name for name, definition in definitions.items() if definition.enabled
        )
        return definitions, version

    def _read_statuses(self) -> dict[str, Mapping[str, object]]:
        statuses: dict[str, Mapping[str, object]] = {}
        cursor: str | None = None
        for _page in range(_MAX_STATUS_PAGES):
            params: dict[str, object] = {"limit": _MAX_SERVERS}
            if cursor is not None:
                params["cursor"] = cursor
            result = self._request("mcpServerStatus/list", params)
            if not isinstance(result, Mapping):
                raise McpProtocolError()
            data = result.get("data")
            if not isinstance(data, list) or len(data) > _MAX_SERVERS:
                raise McpProtocolError()
            for item in data:
                if not isinstance(item, Mapping) or not _valid_name(item.get("name")):
                    continue
                statuses[str(item["name"])] = item
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                return statuses
            if (
                not isinstance(next_cursor, str)
                or not next_cursor
                or len(next_cursor.encode("utf-8")) > 1024
            ):
                raise McpProtocolError()
            cursor = next_cursor
        raise McpProtocolError()

    def _write_config_value(
        self, *, key_path: str, value: object, version: str
    ) -> str:
        result = self._request(
            "config/batchWrite",
            {
                "edits": [
                    {
                        "keyPath": key_path,
                        "mergeStrategy": "replace",
                        "value": value,
                    }
                ],
                "expectedVersion": version,
                "reloadUserConfig": True,
            },
            conflict_on_failure=True,
        )
        if not isinstance(result, Mapping):
            raise McpProtocolError()
        if result.get("status") not in {"ok", "okOverridden"} or not _safe_version(
            result.get("version")
        ):
            raise McpConflictError()
        return result["version"]

    def _reload(self) -> None:
        result = self._request("config/mcpServer/reload", None)
        if not isinstance(result, Mapping):
            raise McpProtocolError()

    def _request(
        self,
        method: str,
        params: object,
        *,
        conflict_on_failure: bool = False,
    ) -> object:
        try:
            return self._app_server.request(
                method,
                params,
                timeout_seconds=self._request_timeout_seconds,
            )
        except McpManagerError:
            raise
        except Exception:
            if conflict_on_failure:
                # Native expected-version failures are reported as an opaque
                # app-server error.  The value was locally validated, so only
                # expose the retryable CAS outcome rather than its raw text.
                raise McpConflictError() from None
            raise McpUnavailableError() from None

    def _mutation_lease(self):
        acquire = getattr(self._runtime_gate, "acquire_config_mutation", None)
        if not callable(acquire):
            raise McpUnavailableError()
        try:
            lease = acquire()
        except Exception:
            # RuntimeGate deliberately provides the public conflict semantics;
            # routes map the fixed code without leaking implementation detail.
            raise McpConflictError() from None
        return _LeaseContext(lease)


class _LeaseContext:
    def __init__(self, lease: _Lease) -> None:
        self._lease = lease

    def __enter__(self) -> _Lease:
        return self._lease

    def __exit__(self, *_args: object) -> None:
        self._lease.release()


def _definition_from_config(
    name: object,
    value: object,
    *,
    resolver: Callable[[str], tuple[str, ...]] = _resolve_host,
    relay: McpRelay | None = None,
    effective: bool = False,
) -> McpServerDefinition:
    normalized_name = _validate_name(name)
    if not isinstance(value, Mapping) or type(value.get("enabled", True)) is not bool:
        raise McpValidationError()
    enabled = value.get("enabled", True)
    value = {key: item for key, item in value.items() if key != "enabled"}
    if relay is not None:
        original = relay.original_url(normalized_name, value, effective=effective)
        if original is not None:
            metadata = relay.metadata(normalized_name)
            return McpServerDefinition(normalized_name, original, local=metadata["local"], relayed=True,
                                       auth_mode=metadata["auth"], credential_configured=metadata["credential_configured"],
                                       enabled=enabled)
    if effective and isinstance(value, Mapping):
        defaults = {"environment_id": "local", "tool_timeout_sec": None}
        for key, expected in defaults.items():
            if key in value and (type(value[key]) is not type(expected) or value[key] != expected):
                raise McpValidationError()
        value = {key: item for key, item in value.items() if key not in defaults}
    if not isinstance(value, Mapping) or set(value) - {
        "url",
        "oauth_client_id",
        "oauth_resource",
    }:
        raise McpValidationError()
    return McpServerDefinition(
        name=normalized_name,
        url=_validate_https_url(value.get("url"), resolver=resolver),
        oauth_client_id=_validate_public_field(value.get("oauth_client_id")),
        oauth_resource=_validate_public_field(value.get("oauth_resource")),
        enabled=enabled,
    )


def _validate_name(value: object) -> str:
    if not _valid_name(value):
        raise McpValidationError()
    assert isinstance(value, str)
    return value


def _valid_name(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value.encode("utf-8")) <= _MAX_NAME_BYTES
        and _NAME_PATTERN.fullmatch(value) is not None
    )


def _validate_https_url(
    value: object,
    *,
    resolver: Callable[[str], tuple[str, ...]] = _resolve_host,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > _MAX_URL_BYTES
        or value != value.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise McpValidationError()
    try:
        parsed = urlsplit(value)
        _validate_url_parts(parsed)
    except (TypeError, ValueError):
        raise McpValidationError() from None
    host = parsed.hostname
    assert host is not None
    normalized_host = host.lower().rstrip(".")
    try:
        normalized_host.encode("ascii")
    except UnicodeEncodeError:
        raise McpValidationError() from None
    if _host_is_disallowed(normalized_host):
        raise McpValidationError()
    if _has_non_public_dns_answer(normalized_host, resolver):
        raise McpValidationError()
    port = parsed.port
    authority = normalized_host if port is None else f"{normalized_host}:{port}"
    return urlunsplit(("https", authority, parsed.path or "/", "", ""))


def _validate_url_parts(parsed: SplitResult) -> None:
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.hostname is None
    ):
        raise McpValidationError()
    # Accessing ``port`` validates malformed / out-of-range ports.
    _ = parsed.port


def _host_is_disallowed(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return True
    if host in _RESERVED_HOSTS or host.endswith((".localhost", ".local", ".internal")):
        return True
    if len(host) > 253 or "." not in host:
        return True
    return any(
        not label or _DNS_LABEL_PATTERN.fullmatch(label) is None
        for label in host.split(".")
    )


def _validate_public_field(value: object) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > _MAX_PUBLIC_FIELD_BYTES
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or _CREDENTIAL_PATTERN.search(value) is not None
    ):
        raise McpValidationError()
    return value


def _validate_oauth_authorization_url(
    value: object,
    *,
    resolver: Callable[[str], tuple[str, ...]] = _resolve_host,
) -> str:
    """Accept only a public HTTPS URL without altering its signed query."""

    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > _MAX_OAUTH_URL_BYTES
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise McpProtocolError()
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed.hostname is None
        ):
            raise ValueError()
        # Accessing ``port`` validates malformed and out-of-range values.
        _ = parsed.port
    except (TypeError, ValueError):
        raise McpProtocolError() from None
    assert parsed.hostname is not None
    host = parsed.hostname.lower().rstrip(".")
    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        raise McpProtocolError() from None
    if _host_is_disallowed(host) or _has_non_public_dns_answer(host, resolver):
        raise McpProtocolError()
    return value


def _user_config_version(layers: object) -> str:
    version = _optional_user_config_version(layers)
    if version is None:
        raise McpProtocolError()
    return version


def _optional_user_config_version(layers: object) -> str | None:
    if not isinstance(layers, list) or len(layers) > 32:
        raise McpProtocolError()
    for layer in layers:
        if not isinstance(layer, Mapping):
            continue
        source = layer.get("name")
        if not isinstance(source, Mapping) or source.get("type") != "user":
            continue
        version = layer.get("version")
        if not _safe_version(version):
            raise McpProtocolError()
        assert isinstance(version, str)
        return version
    return None


def _validated_user_definitions(
    result: object,
    *,
    resolver: Callable[[str], tuple[str, ...]],
    relay: McpRelay | None = None,
) -> tuple[dict[str, McpServerDefinition], str]:
    """Return only safe servers from the raw user layer behind a session mask."""

    if not isinstance(result, Mapping):
        raise McpProtocolError()
    effective_config = result.get("config")
    if not mcp_config_is_disabled(effective_config):
        # The only reason it is safe to inspect persisted layers is that the
        # bootstrap process has already disabled every effective MCP entry.
        raise McpProtocolError()
    layers = result.get("layers")
    if not isinstance(layers, list) or len(layers) > 32:
        raise McpProtocolError()
    user_config: Mapping[str, object] | None = None
    user_version: str | None = None
    for layer in layers:
        if not isinstance(layer, Mapping):
            raise McpProtocolError()
        source = layer.get("name")
        config = layer.get("config")
        if not isinstance(source, Mapping) or not isinstance(config, Mapping):
            raise McpProtocolError()
        source_type = source.get("type")
        if source_type == "sessionFlags":
            servers = config.get("mcp_servers", {})
            if not isinstance(servers, Mapping) or any(
                not _valid_name(name) or value not in (
                    {"enabled": False, "url": "https://disabled.invalid/mcp"},
                    {"enabled": False, "command": "false"},
                )
                for name, value in servers.items()
            ):
                raise McpProtocolError()
            continue
        if source_type != "user":
            if not _mcp_root_is_empty(config.get("mcp_servers")):
                # The session mask blocks these layers in the bootstrap process,
                # but a system or project entry could reappear after activation.
                # The Bridge cannot safely rewrite another layer's authority.
                raise McpProtocolError()
            continue
        version = layer.get("version")
        if not _safe_version(version) or not isinstance(config, Mapping):
            raise McpProtocolError()
        profile = source.get("profile")
        if profile is not None:
            if not isinstance(profile, str) or not _mcp_root_is_empty(
                config.get("mcp_servers")
            ):
                # The user profile is a separate persisted layer.  It is not
                # the root that config/batchWrite updates, so it must not
                # contribute MCP after the bootstrap mask is removed.
                raise McpProtocolError()
            continue
        if user_config is None:
            assert isinstance(version, str)
            user_config = config
            user_version = version
        elif not _mcp_root_is_empty(config.get("mcp_servers")):
            raise McpProtocolError()
    if user_config is None or user_version is None:
        raise McpProtocolError()
    raw_servers = user_config.get("mcp_servers", {})
    definitions: dict[str, McpServerDefinition] = {}
    if isinstance(raw_servers, Mapping) and len(raw_servers) <= _MAX_SERVERS:
        for name, value in raw_servers.items():
            try:
                definition = _definition_from_config(
                    name,
                    value,
                    resolver=resolver,
                    relay=relay,
                )
            except McpValidationError:
                continue
            definitions[definition.name] = definition
    return definitions, user_version


def _mcp_root_is_empty(value: object) -> bool:
    return value is None or (isinstance(value, Mapping) and not value)


def _safe_version(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value.encode("utf-8")) <= 512
        and all(ord(char) >= 32 and ord(char) != 127 for char in value)
    )


def _callback_params(notification: object) -> Mapping[str, object]:
    params = getattr(notification, "params", notification)
    return params if isinstance(params, Mapping) else {}


def _enum(value: object, allowed: set[str], default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default


def _auth_display(value: str) -> str:
    return {
        "unsupported": "unsupported",
        "notLoggedIn": "oauth_required",
        "oAuth": "oauth",
        # This configuration manager never creates bearer servers.  Existing
        # native configuration is still not reflected as a token-capable setup.
        "bearerToken": "unsupported",
    }.get(value, "unknown")


def _bounded_collection_size(value: object) -> int:
    if isinstance(value, Mapping) or isinstance(value, list):
        return min(len(value), 10_000)
    return 0


def _safe_display_text(value: object, maximum: int) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        return None
    return value


def _endpoint_display(url: str, *, private_path: bool = False) -> str:
    parsed = urlsplit(url)
    if private_path:
        return f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path if parsed.path and parsed.path != "/" else ""
    return f"https://{parsed.netloc}{path}"
