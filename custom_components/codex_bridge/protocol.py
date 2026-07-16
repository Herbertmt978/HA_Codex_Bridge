"""Safe, versioned protocol records for the private Codex Bridge API."""

from dataclasses import dataclass, field
from ipaddress import ip_address, ip_network
import re
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from .const import (
    API_CURRENT,
    API_MAXIMUM,
    API_MINIMUM,
    BRIDGE_TOKEN_MAX_LENGTH,
    BRIDGE_TOKEN_MIN_LENGTH,
    DISCOVERY_SERVICE,
    DISCOVERY_SLUG_SUFFIX,
    DISCOVERY_SOURCE,
    LEGACY_API_VERSION,
)


_DISCOVERY_UUID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_PRIVATE_APP_NETWORKS = (
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("fc00::/7"),
)
_HOST_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,61}[a-z0-9])?$")
_SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SAFE_VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_IMAGE_REVISION_PATTERN = re.compile(
    r"^(?:[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64}|sha256:[A-Fa-f0-9]{64})$"
)
_RELEASE_LOCK_DIGEST_PATTERN = re.compile(r"^[A-Fa-f0-9]{64}$")
_ARCHITECTURES = frozenset({"amd64", "aarch64", "unknown"})
_READINESS_STATES = frozenset({"ready", "auth_required", "degraded_catalogue", "fatal"})
_READINESS_REASONS = frozenset(
    {
        "authentication_required",
        "catalogue_stale",
        "runtime_unavailable",
        "runtime_version_mismatch",
        "sandbox_unavailable",
    }
)
_KNOWN_CAPABILITIES = frozenset(
    {
        "api_v1",
        "legacy_v0",
        "automations_v1",
        "mcp_admin_v1",
        "skills_v1",
        "plugins_v1",
        "agents_v1",
        "web_search_v1",
        "image_generation_v1",
    }
)
_KNOWN_PROBLEM_CODES = frozenset(
    {
        "api_incompatible",
        "app_server_unavailable",
        "already_exists",
        "auth_cancel_unsupported",
        "auth_operation_conflict",
        "auth_unavailable",
        "authentication_failed",
        "authentication_required",
        "authorization_failed",
        "agents_unavailable",
        "automation_conflict",
        "automation_error",
        "automation_invalid",
        "automation_not_found",
        "automation_revision_conflict",
        "bad_request",
        "bridge_problem",
        "capabilities_conflict",
        "capabilities_error",
        "capabilities_invalid",
        "capabilities_unavailable",
        "conflict",
        "durable_operation_too_large",
        "event_cursor_expired",
        "event_payload_too_large",
        "event_projection_invalid",
        "event_store_capacity_exhausted",
        "event_wait_capacity_exhausted",
        "interaction_outcome_unknown",
        "interaction_already_resolved",
        "interaction_kind_mismatch",
        "interaction_not_found",
        "interaction_stale",
        "interaction_thread_mismatch",
        "not_found",
        "invalid_event_filter",
        "invalid_relative_path",
        "mcp_config_conflict",
        "mcp_disabled",
        "mcp_elicitation_unavailable",
        "mcp_request_invalid",
        "mcp_runtime_invalid",
        "mcp_server_not_found",
        "mcp_unavailable",
        "payload_too_large",
        "path_escape",
        "quota_exceeded",
        "range_not_satisfiable",
        "rate_limited",
        "reservation_conflict",
        "resource_gone",
        "resource_limit",
        "runtime_attachments_not_ready",
        "runtime_closed",
        "runtime_conflict",
        "runtime_event_payload_too_large",
        "runtime_idempotency_capacity",
        "runtime_mutation_conflict",
        "runtime_queue_full",
        "runtime_request_conflict",
        "runtime_projection_invalid",
        "runtime_thread_busy",
        "runtime_unavailable",
        "secure_operations_unavailable",
        "steer_outcome_unknown",
        "thread_event_cursor_expired",
        "thread_prompt_pending",
        "turn_cancelling",
        "turn_changed",
        "upload_conflict",
        "workspace_error",
        "wrong_type",
    }
)
_KNOWN_PROBLEM_RESOURCES = frozenset(
    {
        "artifact",
        "artifact_snapshot",
        "archive_container",
        "archive_destination",
        "archive_entries",
        "archive_entry",
        "archive_expanded",
        "archive_metadata",
        "archive_ratio",
        "bytes",
        "depth",
        "durable_operation",
        "entries",
        "event_payload",
        "event_store",
        "file",
        "filesystem_scan",
        "filesystem_space",
        "private",
        "private_storage",
        "quota_ledger",
        "snapshot_lease",
        "upload",
        "upload_file",
        "upload_request",
        "upload_sessions",
        "workspace",
    }
)
_KNOWN_EVENT_SCOPES = frozenset({"auth", "global", "runtime", "thread"})


class ProtocolError(RuntimeError):
    """A structured failure which never retains remote details or secrets."""

    code = "protocol_error"
    retryable = False

    def __init__(
        self, code: str | None = None, *, retryable: bool | None = None
    ) -> None:
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        super().__init__(self.code.replace("_", " "))

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, retryable={self.retryable!r})"
        )


class EndpointError(ProtocolError):
    code = "endpoint_invalid"


class ApiIncompatibleError(ProtocolError):
    code = "api_incompatible"


def _is_int(value: object) -> bool:
    return type(value) is int


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EndpointError("payload_invalid")
    return value


def _safe_version(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _SAFE_VERSION_PATTERN.fullmatch(value) is None:
        raise EndpointError("payload_invalid")
    return value


def _safe_pattern_string(value: object, pattern: re.Pattern[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise EndpointError("payload_invalid")
    return value.lower()


def _safe_nonnegative_int(value: object) -> int | None:
    if value is None:
        return None
    if not _is_int(value) or value < 0:
        return None
    return value


def _safe_identifier(value: object) -> str | None:
    if not isinstance(value, str) or _SAFE_IDENTIFIER_PATTERN.fullmatch(value) is None:
        return None
    return value


def validate_bridge_identifier(value: object) -> str:
    """Validate a Bridge-generated identifier before using it in a URL path."""

    identifier = _safe_identifier(value)
    if identifier is None:
        raise EndpointError("identifier_invalid")
    return identifier


@dataclass(frozen=True, slots=True)
class ApiRange:
    minimum: int
    maximum: int

    def __post_init__(self) -> None:
        if (
            not _is_int(self.minimum)
            or not _is_int(self.maximum)
            or self.minimum < 0
            or self.maximum < self.minimum
        ):
            raise EndpointError("api_range_invalid")

    @classmethod
    def from_payload(cls, value: object) -> "ApiRange":
        payload = _mapping(value)
        return cls(payload.get("minimum"), payload.get("maximum"))


def negotiate_api(api: ApiRange, *, allow_legacy_v0: bool = False) -> int:
    """Return the highest shared API version, or fail closed."""

    if allow_legacy_v0 and api.minimum <= LEGACY_API_VERSION <= api.maximum:
        if api.maximum < API_MINIMUM:
            return LEGACY_API_VERSION

    selected = min(api.maximum, API_MAXIMUM)
    if max(api.minimum, API_MINIMUM) > selected:
        raise ApiIncompatibleError()
    return selected


def _default_problem_code(status: int) -> str:
    if status == 400:
        return "bad_request"
    if status == 401:
        return "authentication_failed"
    if status == 403:
        return "authorization_failed"
    if status == 404:
        return "not_found"
    if status == 409:
        return "conflict"
    if status == 410:
        return "resource_gone"
    if status == 413:
        return "payload_too_large"
    if status == 416:
        return "range_not_satisfiable"
    if status == 429:
        return "rate_limited"
    return "bridge_problem"


@dataclass(frozen=True, slots=True)
class ProblemRecord:
    """A bounded, secret-free projection of a Bridge problem response."""

    status: int
    code: str
    retryable: bool
    resource: str | None = None
    minimum_cursor: int | None = None
    minimum_sequence: int | None = None
    snapshot_required: bool = False
    snapshot_cursor: int | None = None
    scope: str | None = None
    thread_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not _is_int(self.status)
            or not 100 <= self.status <= 599
            or self.code not in _KNOWN_PROBLEM_CODES
            or type(self.retryable) is not bool
        ):
            raise EndpointError("problem_invalid")

    @classmethod
    def from_payload(cls, status: int, value: object) -> "ProblemRecord":
        if not _is_int(status) or not 100 <= status <= 599:
            raise EndpointError("problem_invalid")

        payload = value if isinstance(value, Mapping) else {}
        detail_value = payload.get("detail")
        detail = detail_value if isinstance(detail_value, Mapping) else payload

        remote_code = detail.get("code")
        code = (
            remote_code
            if isinstance(remote_code, str) and remote_code in _KNOWN_PROBLEM_CODES
            else _default_problem_code(status)
        )
        retryable_value = detail.get("retryable")
        retryable = (
            retryable_value
            if type(retryable_value) is bool
            else status >= 500 or status == 429
        )

        resource_value = detail.get("resource")
        resource = (
            resource_value
            if isinstance(resource_value, str)
            and resource_value in _KNOWN_PROBLEM_RESOURCES
            else None
        )
        snapshot_value = detail.get("snapshot")
        snapshot = snapshot_value if isinstance(snapshot_value, Mapping) else {}
        snapshot_required_value = snapshot.get("required")
        snapshot_required = (
            snapshot_required_value if type(snapshot_required_value) is bool else False
        )
        scope_value = snapshot.get("scope")
        scope = (
            scope_value
            if isinstance(scope_value, str) and scope_value in _KNOWN_EVENT_SCOPES
            else None
        )

        return cls(
            status=status,
            code=code,
            retryable=retryable,
            resource=resource,
            minimum_cursor=_safe_nonnegative_int(detail.get("minimum_cursor")),
            minimum_sequence=_safe_nonnegative_int(detail.get("minimum_sequence")),
            snapshot_required=snapshot_required,
            snapshot_cursor=_safe_nonnegative_int(snapshot.get("cursor")),
            scope=scope,
            thread_id=_safe_identifier(snapshot.get("thread_id")),
        )


def validate_bridge_url(value: object) -> str:
    """Accept only a private, origin-only HTTP(S) Bridge endpoint."""

    if (
        not isinstance(value, str)
        or value != value.strip()
        or any(ord(character) < 33 or ord(character) == 127 for character in value)
    ):
        raise EndpointError()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise EndpointError() from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise EndpointError()

    try:
        address = ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None:
        if (
            address.is_unspecified
            or address.is_multicast
            or not (address.is_private or address.is_loopback or address.is_link_local)
        ):
            raise EndpointError()
    else:
        labels = parsed.hostname.split(".")
        is_private_suffix = parsed.hostname.endswith(
            ".local"
        ) or parsed.hostname.endswith(".home.arpa")
        if not all(_HOST_LABEL_PATTERN.fullmatch(label) for label in labels) or not (
            parsed.hostname == "localhost" or len(labels) == 1 or is_private_suffix
        ):
            raise EndpointError()

    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


def _bridge_origin(host: str, port: int) -> str:
    rendered_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return validate_bridge_url(f"http://{rendered_host}:{port}")


def _validate_discovery_host(value: object) -> str:
    """Require Supervisor discovery to identify a literal private App IP."""

    if not isinstance(value, str) or value != value.strip():
        raise EndpointError("discovery_invalid")
    try:
        address = ip_address(value)
    except ValueError:
        raise EndpointError("discovery_invalid") from None
    normalized = str(address)
    if normalized != value or not any(
        address in network for network in _PRIVATE_APP_NETWORKS
    ):
        raise EndpointError("discovery_invalid")
    return normalized


def validate_bridge_token(value: object) -> str:
    """Validate an opaque App-issued token without ever echoing it."""

    if (
        not isinstance(value, str)
        or len(value) < BRIDGE_TOKEN_MIN_LENGTH
        or len(value) > BRIDGE_TOKEN_MAX_LENGTH
        or value.lower() in {"change-me", "replace-this-with-a-long-random-token"}
        or any(
            character.isspace() or not character.isprintable() for character in value
        )
    ):
        raise EndpointError("token_invalid")
    return value


@dataclass(frozen=True, slots=True)
class DiscoveryRecord:
    """Validated Supervisor discovery data; its bearer token is never repr'd."""

    source: str
    service: str
    slug: str
    uuid: str
    host: str = field(repr=False)
    port: int
    token: str = field(repr=False)
    api: ApiRange = field(default_factory=lambda: ApiRange(API_CURRENT, API_CURRENT))

    def __post_init__(self) -> None:
        if (
            self.source != DISCOVERY_SOURCE
            or self.service != DISCOVERY_SERVICE
            or not isinstance(self.slug, str)
            or not (
                self.slug == DISCOVERY_SLUG_SUFFIX
                or self.slug.endswith(f"_{DISCOVERY_SLUG_SUFFIX}")
            )
            or not isinstance(self.uuid, str)
            or _DISCOVERY_UUID_PATTERN.fullmatch(self.uuid) is None
            or not isinstance(self.host, str)
            or not _is_int(self.port)
            or not 1 <= self.port <= 65535
        ):
            raise EndpointError("discovery_invalid")
        _bridge_origin(_validate_discovery_host(self.host), self.port)
        validate_bridge_token(self.token)
        negotiate_api(self.api)

    @property
    def base_url(self) -> str:
        return _bridge_origin(self.host, self.port)

    @classmethod
    def from_payload(cls, value: object) -> "DiscoveryRecord":
        payload = _mapping(value)
        return cls(
            source=payload.get("source"),
            service=payload.get("service"),
            slug=payload.get("slug"),
            uuid=payload.get("uuid"),
            host=payload.get("host"),
            port=payload.get("port"),
            token=payload.get("token"),
            api=ApiRange.from_payload(payload.get("api")),
        )


@dataclass(frozen=True, slots=True)
class ReadyRecord:
    """The safe subset of an authenticated Bridge readiness response."""

    api: ApiRange
    bridge_version: str | None
    app_version: str | None
    codex_version: str | None
    image_revision: str | None
    release_lock_digest: str | None
    architecture: str
    capabilities: tuple[str, ...]
    readiness_state: str
    readiness_reasons: tuple[str, ...]

    @property
    def is_v1(self) -> bool:
        return self.api.minimum <= API_CURRENT <= self.api.maximum

    @classmethod
    def from_payload(
        cls, value: object, *, allow_legacy_v0: bool = False
    ) -> "ReadyRecord":
        payload = _mapping(value)
        if payload.get("status") != "ok":
            raise EndpointError("ready_invalid")
        api_value = payload.get("api")
        if api_value is None:
            if not allow_legacy_v0:
                raise ApiIncompatibleError()
            return cls(
                api=ApiRange(LEGACY_API_VERSION, LEGACY_API_VERSION),
                bridge_version=None,
                app_version=None,
                codex_version=None,
                image_revision=None,
                release_lock_digest=None,
                architecture="unknown",
                capabilities=(),
                readiness_state="ready",
                readiness_reasons=(),
            )
        api_payload = _mapping(api_value)
        api = ApiRange.from_payload(api_payload)
        api_current = api_payload.get("current")
        if (
            not _is_int(api_current)
            or not api.minimum <= api_current <= api.maximum
            or api_payload.get("legacy_version") != LEGACY_API_VERSION
            or type(api_payload.get("legacy_supported")) is not bool
            or (
                api.maximum < API_MINIMUM
                and api_payload.get("legacy_supported") is not True
            )
        ):
            raise EndpointError("ready_invalid")
        negotiate_api(api, allow_legacy_v0=allow_legacy_v0)

        bridge = _mapping(payload.get("bridge", {}))
        app = _mapping(payload.get("app", {}))
        codex = _mapping(payload.get("codex", {}))
        image = _mapping(payload.get("image", {}))
        capabilities_value = payload.get("capabilities", ())
        readiness = _mapping(payload.get("readiness", {}))
        if (
            not isinstance(capabilities_value, list)
            or not all(isinstance(item, str) for item in capabilities_value)
            or readiness.get("state") not in _READINESS_STATES
            or not isinstance(readiness.get("reasons"), list)
            or not all(
                isinstance(item, str) and item in _READINESS_REASONS
                for item in readiness["reasons"]
            )
            or payload.get("architecture") not in _ARCHITECTURES
        ):
            raise EndpointError("ready_invalid")
        capabilities = tuple(
            item for item in capabilities_value if item in _KNOWN_CAPABILITIES
        )
        if api.maximum >= API_MINIMUM and "api_v1" not in capabilities:
            raise EndpointError("ready_invalid")
        return cls(
            api=api,
            bridge_version=_safe_version(bridge.get("version")),
            app_version=_safe_version(app.get("version")),
            codex_version=_safe_version(codex.get("version")),
            image_revision=_safe_pattern_string(
                image.get("revision"), _IMAGE_REVISION_PATTERN
            ),
            release_lock_digest=_safe_pattern_string(
                image.get("release_lock_digest"),
                _RELEASE_LOCK_DIGEST_PATTERN,
            ),
            architecture=payload["architecture"],
            capabilities=capabilities,
            readiness_state=readiness["state"],
            readiness_reasons=tuple(readiness["reasons"]),
        )
