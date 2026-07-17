"""Fail-closed typed contract for the App-owned browser worker.

This module deliberately describes high-level browser operations only.  It is
shared by the Codex dynamic-tool broker and the private worker transport; raw
JavaScript, CDP, headers, cookies, and arbitrary filesystem paths are not part
of the wire shape.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import ipaddress
import re
from typing import Annotated, Any, Literal, Mapping, Union
from urllib.parse import urlsplit, urlunsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from .generated_images import validate_generated_image_result
from .workspace import WorkspaceInputError


MAX_PAGE_TEXT_CHARS = 32 * 1024
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_SCREENSHOT_BYTES = 4 * 1024 * 1024
_SESSION_PATTERN = r"^brs_[0-9a-f]{16}$"
_BLOCKED_HOSTS = {
    "homeassistant",
    "hassio",
    "localhost",
    "supervisor",
    "codex_bridge",
    "codex-bridge",
}
_BLOCKED_SUFFIXES = (
    ".home.arpa",
    ".internal",
    ".lan",
    ".local",
    ".localhost",
)
_PDF_HEADER = re.compile(br"^%PDF-(?:1\.[0-7]|2\.0)(?:\r\n|\r|\n)")
_PDF_ACTIVE_NAMES = (
    b"/aa",
    b"/embeddedfile",
    b"/javascript",
    b"/js",
    b"/launch",
    b"/openaction",
    b"/richmedia",
)


class BrowserContractError(ValueError):
    """The requested browser operation or worker response is not safe."""


def validate_browser_pdf_bytes(value: object) -> bytes:
    """Accept one bounded, complete, inert Chromium print artifact.

    Browser PDFs are private downloads, but they still cross a process trust
    boundary.  Requiring the container header and terminal marker prevents a
    truncated worker response from being published, while active PDF action
    dictionaries are excluded because Chromium's print output does not need
    them.
    """

    if not isinstance(value, bytes) or not value or len(value) > MAX_ARTIFACT_BYTES:
        raise BrowserContractError("worker artifact is invalid")
    if _PDF_HEADER.match(value) is None:
        raise BrowserContractError("worker artifact is invalid")
    stripped = value.rstrip(b"\x00\t\n\x0c\r ")
    if not stripped.endswith(b"%%EOF"):
        raise BrowserContractError("worker artifact is invalid")
    lowered = value.lower()
    for name in _PDF_ACTIVE_NAMES:
        if re.search(re.escape(name) + br"(?=[\s<>{}\[\]()/]|$)", lowered):
            raise BrowserContractError("worker artifact is invalid")
    return value


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)


def normalize_public_url(value: object) -> str:
    """Return a canonical public HTTP(S) URL or fail before DNS/network use."""

    if not isinstance(value, str) or not value or len(value) > 4096:
        raise BrowserContractError("navigation target is not allowed")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise BrowserContractError("navigation target is not allowed")
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or parsed.username is not None or parsed.password is not None:
            raise BrowserContractError("navigation target is not allowed")
        if not parsed.hostname:
            raise BrowserContractError("navigation target is not allowed")
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        if not host or host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_SUFFIXES):
            raise BrowserContractError("navigation target is not allowed")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or any(not label for label in host.split(".")):
                raise BrowserContractError("navigation target is not allowed") from None
        else:
            if (
                not address.is_global
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_private
                or address.is_reserved
                or address.is_unspecified
            ):
                raise BrowserContractError("navigation target is not allowed")
        port = parsed.port
    except (UnicodeError, ValueError):
        raise BrowserContractError("navigation target is not allowed") from None
    default_port = (scheme == "http" and port in {None, 80}) or (
        scheme == "https" and port in {None, 443}
    )
    display_host = f"[{host}]" if ":" in host else host
    netloc = display_host if default_port else f"{display_host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, parsed.fragment))


def _selector(value: str) -> str:
    if not value or any(ord(character) < 32 for character in value):
        raise ValueError("selector is invalid")
    return value


class _SessionAction(_StrictModel):
    session_id: str = Field(pattern=_SESSION_PATTERN)


class OpenAction(_StrictModel):
    action: Literal["open"]
    url: str
    wait_until: Literal["domcontentloaded", "load"] = "domcontentloaded"
    timeout_ms: int = Field(default=15_000, ge=1_000, le=30_000)

    _normalize_url = field_validator("url")(normalize_public_url)


class NavigateAction(_SessionAction):
    action: Literal["navigate"]
    url: str
    wait_until: Literal["domcontentloaded", "load"] = "domcontentloaded"
    timeout_ms: int = Field(default=15_000, ge=1_000, le=30_000)

    _normalize_url = field_validator("url")(normalize_public_url)


class InspectAction(_SessionAction):
    action: Literal["inspect"]
    selector: str | None = Field(default=None, max_length=512)
    max_chars: int = Field(default=16 * 1024, ge=1, le=MAX_PAGE_TEXT_CHARS)

    @field_validator("selector")
    @classmethod
    def validate_selector(cls, value: str | None) -> str | None:
        return None if value is None else _selector(value)


class ClickAction(_SessionAction):
    action: Literal["click"]
    selector: str = Field(min_length=1, max_length=512)
    timeout_ms: int = Field(default=10_000, ge=100, le=30_000)

    _validate_selector = field_validator("selector")(_selector)


class TypeAction(_SessionAction):
    action: Literal["type"]
    selector: str = Field(min_length=1, max_length=512)
    text: str = Field(max_length=8192)
    clear: bool = True
    submit: bool = False
    timeout_ms: int = Field(default=10_000, ge=100, le=30_000)

    _validate_selector = field_validator("selector")(_selector)


class SelectAction(_SessionAction):
    action: Literal["select"]
    selector: str = Field(min_length=1, max_length=512)
    value: str = Field(max_length=1024)
    timeout_ms: int = Field(default=10_000, ge=100, le=30_000)

    _validate_selector = field_validator("selector")(_selector)


class WaitAction(_SessionAction):
    action: Literal["wait"]
    selector: str | None = Field(default=None, max_length=512)
    text: str | None = Field(default=None, max_length=1024)
    timeout_ms: int = Field(default=1_000, ge=50, le=10_000)

    @field_validator("selector")
    @classmethod
    def validate_selector(cls, value: str | None) -> str | None:
        return None if value is None else _selector(value)

    @model_validator(mode="after")
    def only_one_condition(self) -> "WaitAction":
        if self.selector is not None and self.text is not None:
            raise ValueError("wait accepts at most one condition")
        return self


class ScreenshotAction(_SessionAction):
    action: Literal["screenshot"]
    full_page: bool = False
    format: Literal["png", "jpeg"] = "png"
    quality: int | None = Field(default=None, ge=1, le=95)

    @model_validator(mode="after")
    def validate_quality(self) -> "ScreenshotAction":
        if self.format == "png" and self.quality is not None:
            raise ValueError("PNG screenshots do not accept quality")
        return self


class PdfAction(_SessionAction):
    action: Literal["pdf"]
    format: Literal["A4", "Letter"] = "A4"
    landscape: bool = False
    print_background: bool = True


class CloseAction(_SessionAction):
    action: Literal["close"]


BrowserAction = Annotated[
    Union[
        OpenAction,
        NavigateAction,
        InspectAction,
        ClickAction,
        TypeAction,
        SelectAction,
        WaitAction,
        ScreenshotAction,
        PdfAction,
        CloseAction,
    ],
    Field(discriminator="action"),
]
_ACTION_ADAPTER = TypeAdapter(BrowserAction)


class BrowserPageProjection(_StrictModel):
    url: str
    title: str = Field(max_length=512)
    text: str = Field(default="", max_length=MAX_PAGE_TEXT_CHARS)

    _normalize_url = field_validator("url")(normalize_public_url)


class _ArtifactEnvelope(_StrictModel):
    kind: Literal["screenshot", "pdf"]
    mime_type: Literal["image/png", "image/jpeg", "application/pdf"]
    data_base64: str = Field(min_length=4, max_length=((MAX_ARTIFACT_BYTES + 2) // 3) * 4)


class BrowserWorkerError(_StrictModel):
    code: Literal[
        "browser_unavailable",
        "navigation_blocked",
        "navigation_failed",
        "page_timeout",
        "selector_not_found",
        "session_closed",
        "session_expired",
        "worker_failed",
    ]
    retryable: bool


class _OkResponse(_StrictModel):
    status: Literal["ok"]
    session_id: str = Field(pattern=_SESSION_PATTERN)
    page: BrowserPageProjection | None = None
    artifact: _ArtifactEnvelope | None = None


class _ErrorResponse(_StrictModel):
    status: Literal["error"]
    session_id: str = Field(pattern=_SESSION_PATTERN)
    error: BrowserWorkerError


_WORKER_RESPONSE_ADAPTER = TypeAdapter(
    Annotated[Union[_OkResponse, _ErrorResponse], Field(discriminator="status")]
)


@dataclass(frozen=True, slots=True)
class BrowserWorkerArtifact:
    kind: Literal["screenshot", "pdf"]
    mime_type: Literal["image/png", "image/jpeg", "application/pdf"]
    data: bytes


@dataclass(frozen=True, slots=True)
class BrowserWorkerResponse:
    status: Literal["ok", "error"]
    session_id: str
    page: BrowserPageProjection | None = None
    artifact: BrowserWorkerArtifact | None = None
    error: BrowserWorkerError | None = None


def parse_browser_action(value: object) -> BrowserAction:
    if isinstance(value, Mapping) and value.get("action") in {"open", "navigate"}:
        # Preserve the safe failure category while still withholding the target.
        normalize_public_url(value.get("url"))
    try:
        return _ACTION_ADAPTER.validate_python(value)
    except (ValidationError, BrowserContractError):
        raise BrowserContractError("browser action is invalid") from None


def _decode_artifact(value: _ArtifactEnvelope) -> BrowserWorkerArtifact:
    try:
        encoded = value.data_base64.encode("ascii")
        data = base64.b64decode(encoded, validate=True)
    except (UnicodeEncodeError, ValueError):
        raise BrowserContractError("worker artifact is invalid") from None
    maximum = MAX_SCREENSHOT_BYTES if value.kind == "screenshot" else MAX_ARTIFACT_BYTES
    if not data or len(data) > maximum:
        raise BrowserContractError("worker artifact is invalid")
    if value.kind == "screenshot":
        try:
            validated_mime, validated_data = validate_generated_image_result(
                value.data_base64,
                value.mime_type,
            )
        except WorkspaceInputError:
            raise BrowserContractError("worker artifact is invalid") from None
        if validated_mime != value.mime_type or validated_data != data:
            raise BrowserContractError("worker artifact is invalid")
    elif value.mime_type == "application/pdf":
        validate_browser_pdf_bytes(data)
    else:
        raise BrowserContractError("worker artifact is invalid")
    return BrowserWorkerArtifact(kind=value.kind, mime_type=value.mime_type, data=data)


def parse_worker_response(value: object) -> BrowserWorkerResponse:
    if isinstance(value, Mapping) and value.get("status") == "ok":
        artifact = value.get("artifact")
        if isinstance(artifact, Mapping) and artifact.get("mime_type") not in {
            "image/png",
            "image/jpeg",
            "application/pdf",
        }:
            raise BrowserContractError("worker artifact is invalid")
    try:
        parsed = _WORKER_RESPONSE_ADAPTER.validate_python(value)
    except (ValidationError, BrowserContractError):
        raise BrowserContractError("browser worker response is invalid") from None
    if isinstance(parsed, _ErrorResponse):
        return BrowserWorkerResponse(
            status="error", session_id=parsed.session_id, error=parsed.error
        )
    artifact = _decode_artifact(parsed.artifact) if parsed.artifact is not None else None
    return BrowserWorkerResponse(
        status="ok",
        session_id=parsed.session_id,
        page=parsed.page,
        artifact=artifact,
    )


def _tool_input_schema(model: type[_StrictModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    properties = dict(schema.get("properties", {}))
    properties.pop("action", None)
    required = [item for item in schema.get("required", []) if item != "action"]
    result: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if required:
        result["required"] = required
    if "$defs" in schema:
        result["$defs"] = schema["$defs"]
    return result


def browser_dynamic_tool_spec() -> dict[str, object]:
    """Return the locked client-owned Codex namespace tool projection."""

    descriptions: Mapping[str, str] = {
        "open": "Open one public HTTP(S) page in a new ephemeral browser session.",
        "navigate": "Navigate an existing ephemeral session to a public HTTP(S) page.",
        "inspect": "Read a bounded plain-text projection of the current page.",
        "click": "Click one element selected by a bounded CSS selector.",
        "type": "Type bounded text into one selected element.",
        "select": "Select one bounded value in a selected form control.",
        "wait": "Wait briefly for time, one selector, or bounded visible text.",
        "screenshot": "Capture the current page as a private PNG or JPEG artifact.",
        "pdf": "Print the current page to a private bounded PDF artifact.",
        "close": "Close and destroy an ephemeral browser session.",
    }
    models: tuple[tuple[str, type[_StrictModel]], ...] = (
        ("open", OpenAction),
        ("navigate", NavigateAction),
        ("inspect", InspectAction),
        ("click", ClickAction),
        ("type", TypeAction),
        ("select", SelectAction),
        ("wait", WaitAction),
        ("screenshot", ScreenshotAction),
        ("pdf", PdfAction),
        ("close", CloseAction),
    )
    return {
        "type": "namespace",
        "name": "ha_browser",
        "description": (
            "Bounded App-owned browser actions for public pages. No raw code, "
            "headers, cookies, downloads, local services, or persistent profile."
        ),
        "tools": [
            {
                "type": "function",
                "name": name,
                "description": descriptions[name],
                "deferLoading": False,
                "inputSchema": _tool_input_schema(model),
            }
            for name, model in models
        ],
    }
