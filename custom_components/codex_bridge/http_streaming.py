"""Bounded helpers for the private Home Assistant file transport."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
import json
import re
from typing import Any
from urllib.parse import quote, unquote_to_bytes

from aiohttp import web

from .bridge_api import (
    BridgeApiAuthError,
    BridgeApiCapabilityError,
    BridgeApiConflictError,
    BridgeApiConnectionError,
    BridgeApiEndpointError,
    BridgeApiError,
    BridgeApiGoneError,
    BridgeApiIncompatibleError,
    BridgeApiPayloadTooLargeError,
    BridgeApiRangeNotSatisfiableError,
    BridgeApiTimeoutError,
)


UPLOAD_METADATA_MAX_BYTES = 64 * 1024
UPLOAD_CHUNK_MAX_BYTES = 8 * 1024 * 1024
LEGACY_UPLOAD_REQUEST_MAX_BYTES = 101 * 1024 * 1024
DOWNLOAD_STREAM_CHUNK_BYTES = 64 * 1024
_MAX_IDENTIFIER_INTEGER = 2**63 - 1
_UPLOAD_CREATE_FIELDS = frozenset(
    {"filename", "mime_type", "relative_path", "size_bytes", "sha256"}
)
_UPLOAD_CREATE_REQUIRED_FIELDS = frozenset({"filename", "size_bytes", "sha256"})
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ETAG_PATTERN = re.compile(r'^"[0-9a-f]{64}"$')
_PARTIAL_CONTENT_RANGE = re.compile(
    r"^bytes ([0-9]{1,19})-([0-9]{1,19})/([0-9]{1,19})$"
)
_UNSATISFIED_CONTENT_RANGE = re.compile(r"^bytes \*/([0-9]{1,19})$")
_ATTACHMENT_DISPOSITION = re.compile(
    r'^attachment; filename="(?P<fallback>[A-Za-z0-9._-]{1,255})"; '
    r"filename\*=UTF-8''(?P<encoded>[A-Za-z0-9!#$&+.^_`|~%\-]{1,2048})$"
)


class HttpStreamingError(RuntimeError):
    """A safe HTTP boundary failure with no private upstream detail."""

    def __init__(self, status: int, code: str) -> None:
        self.status = status
        self.code = code
        super().__init__(code.replace("_", " "))

    def __repr__(self) -> str:
        return f"{type(self).__name__}(status={self.status!r}, code={self.code!r})"


def _contains_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _bounded_decimal(value: object, *, maximum: int = _MAX_IDENTIFIER_INTEGER) -> int:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 19
        or not value.isdecimal()
    ):
        raise HttpStreamingError(400, "request_invalid")
    parsed = int(value)
    if parsed > maximum:
        raise HttpStreamingError(400, "request_invalid")
    return parsed


def _bounded_text(
    value: object,
    *,
    maximum_bytes: int,
    allow_none: bool = False,
) -> str | None:
    if allow_none and value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or _contains_control(value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise HttpStreamingError(400, "request_invalid")
    return value


async def async_read_upload_create(request: web.Request) -> dict[str, Any]:
    """Read the small upload manifest without ever reading a binary body."""

    if request.content_type != "application/json":
        raise HttpStreamingError(415, "content_type_invalid")
    declared = request.content_length
    if declared is not None and declared > UPLOAD_METADATA_MAX_BYTES:
        raise HttpStreamingError(413, "payload_too_large")

    body = bytearray()
    async for block in request.content.iter_chunked(16 * 1024):
        if len(body) + len(block) > UPLOAD_METADATA_MAX_BYTES:
            raise HttpStreamingError(413, "payload_too_large")
        body.extend(block)
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        raise HttpStreamingError(400, "request_invalid") from None
    if not isinstance(payload, dict):
        raise HttpStreamingError(400, "request_invalid")
    fields = set(payload)
    if (
        not _UPLOAD_CREATE_REQUIRED_FIELDS <= fields
        or not fields <= _UPLOAD_CREATE_FIELDS
    ):
        raise HttpStreamingError(400, "request_invalid")

    filename = _bounded_text(payload.get("filename"), maximum_bytes=255)
    mime_type = _bounded_text(
        payload.get("mime_type", "application/octet-stream"), maximum_bytes=255
    )
    relative_path = _bounded_text(
        payload.get("relative_path"), maximum_bytes=2048, allow_none=True
    )
    if relative_path is not None and len(relative_path.split("/")) > 16:
        raise HttpStreamingError(400, "request_invalid")
    size_bytes = payload.get("size_bytes")
    sha256 = payload.get("sha256")
    if (
        type(size_bytes) is not int
        or not 1 <= size_bytes <= _MAX_IDENTIFIER_INTEGER
        or not isinstance(sha256, str)
        or _SHA256_PATTERN.fullmatch(sha256) is None
    ):
        raise HttpStreamingError(400, "request_invalid")
    return {
        "filename": filename,
        "mime_type": mime_type,
        "relative_path": relative_path,
        "size_bytes": size_bytes,
        "sha256": sha256,
    }


def parse_upload_chunk_request(
    request: web.Request,
    index: str,
) -> tuple[int, int, int, str]:
    """Validate the fixed-chunk transport metadata before reading its body."""

    parsed_index = _bounded_decimal(index)
    offset = _bounded_decimal(request.headers.get("Upload-Offset"))
    length = _bounded_decimal(request.headers.get("Content-Length"))
    if length > UPLOAD_CHUNK_MAX_BYTES:
        raise HttpStreamingError(413, "payload_too_large")
    digest = request.headers.get("X-Chunk-SHA256")
    if (
        length == 0
        or not isinstance(digest, str)
        or _SHA256_PATTERN.fullmatch(digest) is None
    ):
        raise HttpStreamingError(400, "request_invalid")
    return parsed_index, offset, length, digest


async def iter_request_body(
    request: web.Request,
    *,
    expected_bytes: int,
) -> AsyncIterator[bytes]:
    """Yield an upload body with a small fixed HA-side memory ceiling."""

    received = 0
    async for block in request.content.iter_chunked(DOWNLOAD_STREAM_CHUNK_BYTES):
        received += len(block)
        if received > expected_bytes:
            raise HttpStreamingError(400, "content_length_invalid")
        if block:
            yield block
    if received != expected_bytes:
        raise HttpStreamingError(400, "content_length_invalid")


def _safe_attachment_disposition(value: object) -> str:
    if not isinstance(value, str) or _contains_control(value):
        return 'attachment; filename="download"'
    match = _ATTACHMENT_DISPOSITION.fullmatch(value)
    if match is None:
        return 'attachment; filename="download"'
    fallback = match.group("fallback")
    encoded = match.group("encoded")
    if fallback in {".", ".."} or fallback.startswith("."):
        return 'attachment; filename="download"'
    try:
        decoded_bytes = unquote_to_bytes(encoded)
        decoded = decoded_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return 'attachment; filename="download"'
    if (
        not decoded
        or decoded in {".", ".."}
        or "/" in decoded
        or "\\" in decoded
        or _contains_control(decoded)
        or len(decoded_bytes) > 255
        or quote(decoded, safe="") != encoded
    ):
        return 'attachment; filename="download"'
    return value


def attachment_disposition(filename: object) -> str:
    """Build a safe attachment header from untrusted legacy metadata."""

    if (
        not isinstance(filename, str)
        or not filename
        or "/" in filename
        or "\\" in filename
        or _contains_control(filename)
        or len(filename.encode("utf-8")) > 255
    ):
        return 'attachment; filename="download"'
    fallback = re.sub(r"[^A-Za-z0-9._-]", "_", filename).strip("._")
    if not fallback:
        fallback = "download"
    return (
        f'attachment; filename="{fallback}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


def _safe_content_length(value: object) -> str | None:
    if value is None:
        return None
    parsed = _bounded_decimal(value)
    return str(parsed)


def safe_download_headers(
    status: int,
    upstream: Mapping[str, str],
) -> dict[str, str]:
    """Return only validated end-to-end download metadata."""

    if status not in {200, 206, 416}:
        raise HttpStreamingError(502, "bridge_response_invalid")
    try:
        content_length = _safe_content_length(upstream.get("Content-Length"))
    except HttpStreamingError:
        raise HttpStreamingError(502, "bridge_response_invalid") from None
    content_range = upstream.get("Content-Range")

    if status == 206:
        match = (
            _PARTIAL_CONTENT_RANGE.fullmatch(content_range)
            if isinstance(content_range, str)
            else None
        )
        if match is None or content_length is None:
            raise HttpStreamingError(502, "bridge_response_invalid")
        start, end, total = map(int, match.groups())
        if start > end or end >= total or end - start + 1 != int(content_length):
            raise HttpStreamingError(502, "bridge_response_invalid")
    elif status == 416:
        match = (
            _UNSATISFIED_CONTENT_RANGE.fullmatch(content_range)
            if isinstance(content_range, str)
            else None
        )
        if match is None:
            raise HttpStreamingError(502, "bridge_response_invalid")
        content_length = "0"
    elif content_range is not None or content_length is None:
        raise HttpStreamingError(502, "bridge_response_invalid")

    etag = upstream.get("ETag")
    if etag is not None and (
        not isinstance(etag, str) or _ETAG_PATTERN.fullmatch(etag) is None
    ):
        raise HttpStreamingError(502, "bridge_response_invalid")

    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store, no-transform",
        "Content-Disposition": _safe_attachment_disposition(
            upstream.get("Content-Disposition")
        ),
        "Content-Type": "application/octet-stream",
        "X-Content-Type-Options": "nosniff",
    }
    if content_length is not None:
        headers["Content-Length"] = content_length
    if content_range is not None:
        headers["Content-Range"] = content_range
    if etag is not None:
        headers["ETag"] = etag
    return headers


def safe_range_request_headers(request: web.Request) -> dict[str, str]:
    """Select only bounded Range metadata from the authenticated browser."""

    selected: dict[str, str] = {}
    for name in ("Range", "If-Range"):
        value = request.headers.get(name)
        if value is None:
            continue
        if len(value) > 256 or _contains_control(value):
            raise HttpStreamingError(400, "range_invalid")
        selected[name] = value
    return selected


def bridge_error_response(error: Exception) -> web.Response:
    """Map Bridge failures to stable, non-sensitive browser responses."""

    status = 502
    code = "bridge_error"
    if isinstance(error, BridgeApiTimeoutError):
        status, code = 504, "bridge_timeout"
    elif isinstance(error, BridgeApiPayloadTooLargeError):
        status, code = 413, "payload_too_large"
    elif isinstance(error, BridgeApiRangeNotSatisfiableError):
        status, code = 416, "range_not_satisfiable"
    elif isinstance(error, BridgeApiConflictError):
        status, code = 409, "conflict"
    elif isinstance(error, BridgeApiGoneError):
        status, code = 410, "resource_gone"
    elif isinstance(error, BridgeApiEndpointError):
        status, code = 400, "request_invalid"
    elif isinstance(error, BridgeApiAuthError):
        status, code = 502, "bridge_authentication_failed"
    elif isinstance(error, BridgeApiConnectionError):
        status, code = 502, "bridge_unavailable"
    elif isinstance(error, BridgeApiCapabilityError | BridgeApiIncompatibleError):
        status, code = 503, "bridge_incompatible"
    elif isinstance(error, BridgeApiError) and error.status in {
        400,
        404,
        409,
        410,
        413,
        416,
    }:
        status = error.status
        code = {
            400: "request_invalid",
            404: "not_found",
            409: "conflict",
            410: "resource_gone",
            413: "payload_too_large",
            416: "range_not_satisfiable",
        }[status]
    return web.json_response(
        {"code": code, "message": "Codex Bridge request failed"}, status=status
    )


def streaming_error_response(error: HttpStreamingError) -> web.Response:
    """Render a local boundary failure without reflecting untrusted input."""

    message = (
        "Codex Bridge request failed"
        if error.status >= 500
        else "Request is invalid"
    )
    return web.json_response(
        {"code": error.code, "message": message}, status=error.status
    )
