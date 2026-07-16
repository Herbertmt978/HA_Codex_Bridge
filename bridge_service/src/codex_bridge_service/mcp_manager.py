"""Safe, native app-server management for Home Assistant MCP connections.

The manager deliberately supports only streamable HTTPS servers authenticated by
native OAuth.  It is not a generic Codex configuration editor: stdio commands,
environment variables, bearer-token variables, headers, and arbitrary config
keys would cross the Bridge's trusted-process boundary and are rejected.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import ipaddress
import re
import socket
from threading import RLock
from typing import Callable, Protocol
from urllib.parse import SplitResult, urlsplit, urlunsplit


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

    def config_value(self) -> dict[str, str]:
        value = {"url": self.url}
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
        self._lock = RLock()
        self._startup: dict[str, tuple[str, str | None]] = {}
        self._oauth_completion: dict[str, bool] = {}
        self._elicitation_handler_registered = False
        self._register_callbacks()

    @property
    def elicitation_handler_registered(self) -> bool:
        """Whether the app-server supports the mandatory decline-only handler."""

        return self._elicitation_handler_registered

    @property
    def enabled(self) -> bool:
        """Whether the administrator explicitly enabled outbound MCP."""

        return self._enabled

    def list_servers(self) -> list[dict[str, object]]:
        """Return only configured safe servers and bounded native status metadata."""

        self._require_enabled()
        with self._lock:
            definitions, _version = self._read_definitions()
            statuses = self._read_statuses()
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
                    "endpoint": _endpoint_display(definition.url),
                    "auth": _auth_display(auth_status),
                    "startup": startup,
                    "tool_count": _bounded_collection_size(tools),
                    "resource_count": _bounded_collection_size(resources)
                    + _bounded_collection_size(templates),
                }
                title = _safe_display_text(info.get("title"), 160)
                version = _safe_display_text(info.get("version"), 64)
                if title is not None:
                    view["title"] = title
                if version is not None:
                    view["version"] = version
                if failure is not None:
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
    ) -> dict[str, object]:
        self._require_enabled()
        self._require_elicitation_handler()
        definition = McpServerDefinition(
            name=_validate_name(name),
            url=_validate_https_url(url, resolver=self._resolver),
            oauth_client_id=_validate_public_field(oauth_client_id),
            oauth_resource=_validate_public_field(oauth_resource),
        )
        with self._mutation_lease():
            with self._lock:
                definitions, version = self._read_definitions()
                if definition.name in definitions:
                    raise McpConflictError()
                self._write_config_value(
                    key_path=f"mcp_servers.{definition.name}",
                    value=definition.config_value(),
                    version=version,
                )
                self._reload()
        return self._view_for_created(definition)

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
                self._reload()
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

        The production app-server also starts with an empty MCP config
        override. This native write removes stale user configuration without
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
                if not isinstance(config, Mapping):
                    raise McpProtocolError()
                version = _optional_user_config_version(result.get("layers"))
                if version is None:
                    self._startup.clear()
                    self._oauth_completion.clear()
                    return
                self._write_config_value(
                    key_path="mcp_servers",
                    value=None,
                    version=version,
                )
                self._reload()
                self._startup.clear()
                self._oauth_completion.clear()

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise McpDisabledError()

    def _require_elicitation_handler(self) -> None:
        if not self._elicitation_handler_registered:
            raise McpElicitationUnavailableError()

    def _view_for_created(self, definition: McpServerDefinition) -> dict[str, object]:
        return {
            "name": definition.name,
            "transport": "streamable_http",
            "endpoint": _endpoint_display(definition.url),
            "auth": "oauth" if definition.oauth_client_id else "none",
            "startup": "starting",
            "tool_count": 0,
            "resource_count": 0,
        }

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
        # MCP servers are never allowed to collect additional data through this
        # surface.  A future UX requires a separate explicitly-reviewed flow.
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
                definition = _definition_from_config(raw_name, raw_value)
            except McpValidationError:
                # Unsafe existing native config is never reflected back into HA.
                continue
            definitions[definition.name] = definition
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
    ) -> None:
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


def _definition_from_config(name: object, value: object) -> McpServerDefinition:
    normalized_name = _validate_name(name)
    if not isinstance(value, Mapping) or set(value) - {
        "url",
        "oauth_client_id",
        "oauth_resource",
    }:
        raise McpValidationError()
    return McpServerDefinition(
        name=normalized_name,
        url=_validate_https_url(value.get("url")),
        oauth_client_id=_validate_public_field(value.get("oauth_client_id")),
        oauth_resource=_validate_public_field(value.get("oauth_resource")),
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


def _endpoint_display(url: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path if parsed.path and parsed.path != "/" else ""
    return f"https://{parsed.netloc}{path}"
