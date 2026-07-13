"""Authenticated Home Assistant HTTP views for private Bridge file traffic."""

from __future__ import annotations

from collections.abc import Mapping

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import Unauthorized

from .bridge_api import BridgeApiError
from .http_streaming import (
    DOWNLOAD_STREAM_CHUNK_BYTES,
    HttpStreamingError,
    LEGACY_UPLOAD_REQUEST_MAX_BYTES,
    async_read_upload_create,
    attachment_disposition,
    bridge_error_response,
    iter_request_body,
    parse_upload_chunk_request,
    safe_download_headers,
    safe_range_request_headers,
    streaming_error_response,
)
from .runtime import async_get_runtime


def _require_admin(request: web.Request) -> None:
    user = request.get("hass_user")
    if user is None or not user.is_admin:
        raise Unauthorized()


def _runtime_unavailable_response() -> web.Response:
    return web.json_response(
        {"code": "not_configured", "message": "Codex Bridge is not configured"},
        status=503,
    )


class CodexBridgeAttachmentUploadView(HomeAssistantView):
    """Deprecated external-v0 multipart compatibility endpoint."""

    url = "/api/codex_bridge/threads/{thread_id}/attachments"
    name = "api:codex_bridge:thread_attachments"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def post(self, request: web.Request, thread_id: str) -> web.Response:
        _require_admin(request)
        try:
            runtime = async_get_runtime(self.hass)
            if runtime.api_version != 0:
                raise HttpStreamingError(410, "legacy_transport_unavailable")
            content_type = request.headers.get("Content-Type")
            content_length = request.content_length
            if (
                content_type is None
                or not content_type.lower().startswith(
                    "multipart/form-data; boundary="
                )
                or content_length is None
                or content_length <= 0
            ):
                raise HttpStreamingError(400, "request_invalid")
            if content_length > LEGACY_UPLOAD_REQUEST_MAX_BYTES:
                raise HttpStreamingError(413, "payload_too_large")
            result = await runtime.client.async_stream_legacy_attachment(
                thread_id,
                content_type=content_type,
                content_length=content_length,
                content=iter_request_body(
                    request, expected_bytes=content_length
                ),
            )
        except HttpStreamingError as error:
            return streaming_error_response(error)
        except BridgeApiError as error:
            return bridge_error_response(error)
        except RuntimeError:
            return _runtime_unavailable_response()

        return web.json_response(result, status=201)


class CodexBridgeUploadCreateView(HomeAssistantView):
    url = "/api/codex_bridge/threads/{thread_id}/uploads"
    name = "api:codex_bridge:upload_create"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def post(self, request: web.Request, thread_id: str) -> web.Response:
        _require_admin(request)
        try:
            payload = await async_read_upload_create(request)
            runtime = async_get_runtime(self.hass)
            result = await runtime.client.async_create_upload(thread_id, **payload)
        except HttpStreamingError as error:
            return streaming_error_response(error)
        except BridgeApiError as error:
            return bridge_error_response(error)
        except RuntimeError:
            return _runtime_unavailable_response()
        return web.json_response(result, status=201)


class CodexBridgeUploadSessionView(HomeAssistantView):
    url = "/api/codex_bridge/threads/{thread_id}/uploads/{upload_id}"
    name = "api:codex_bridge:upload_session"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(
        self,
        request: web.Request,
        thread_id: str,
        upload_id: str,
    ) -> web.Response:
        _require_admin(request)
        try:
            runtime = async_get_runtime(self.hass)
            result = await runtime.client.async_get_upload(thread_id, upload_id)
        except BridgeApiError as error:
            return bridge_error_response(error)
        except RuntimeError:
            return _runtime_unavailable_response()
        return web.json_response(result)

    async def delete(
        self,
        request: web.Request,
        thread_id: str,
        upload_id: str,
    ) -> web.Response:
        _require_admin(request)
        try:
            runtime = async_get_runtime(self.hass)
            result = await runtime.client.async_cancel_upload(thread_id, upload_id)
        except BridgeApiError as error:
            return bridge_error_response(error)
        except RuntimeError:
            return _runtime_unavailable_response()
        return web.json_response(result)


class CodexBridgeUploadChunkView(HomeAssistantView):
    url = (
        "/api/codex_bridge/threads/{thread_id}/uploads/{upload_id}/chunks/{index}"
    )
    name = "api:codex_bridge:upload_chunk"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def put(
        self,
        request: web.Request,
        thread_id: str,
        upload_id: str,
        index: str,
    ) -> web.Response:
        _require_admin(request)
        try:
            parsed_index, offset, content_length, digest = parse_upload_chunk_request(
                request, index
            )
            runtime = async_get_runtime(self.hass)
            result = await runtime.client.async_upload_chunk(
                thread_id,
                upload_id,
                parsed_index,
                offset=offset,
                content_length=content_length,
                sha256=digest,
                content=iter_request_body(request, expected_bytes=content_length),
            )
        except HttpStreamingError as error:
            return streaming_error_response(error)
        except BridgeApiError as error:
            return bridge_error_response(error)
        except RuntimeError:
            return _runtime_unavailable_response()
        return web.json_response(result)


class CodexBridgeUploadCompleteView(HomeAssistantView):
    url = "/api/codex_bridge/threads/{thread_id}/uploads/{upload_id}/complete"
    name = "api:codex_bridge:upload_complete"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def post(
        self,
        request: web.Request,
        thread_id: str,
        upload_id: str,
    ) -> web.Response:
        _require_admin(request)
        try:
            runtime = async_get_runtime(self.hass)
            result = await runtime.client.async_complete_upload(thread_id, upload_id)
        except BridgeApiError as error:
            return bridge_error_response(error)
        except RuntimeError:
            return _runtime_unavailable_response()
        return web.json_response(result, status=201)


class CodexBridgeArtifactDownloadView(HomeAssistantView):
    url = "/api/codex_bridge/threads/{thread_id}/artifacts/{artifact_id}"
    name = "api:codex_bridge:thread_artifact"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(
        self,
        request: web.Request,
        thread_id: str,
        artifact_id: str,
    ) -> web.StreamResponse:
        _require_admin(request)
        response_started = False
        try:
            runtime = async_get_runtime(self.hass)
            if runtime.api_version == 0:
                artifacts = await runtime.client.async_list_artifacts(thread_id)
                artifact = next(
                    (
                        item
                        for item in artifacts
                        if isinstance(item, Mapping)
                        and item.get("artifact_id") == artifact_id
                    ),
                    None,
                )
                if artifact is None:
                    raise HttpStreamingError(404, "not_found")
                download = runtime.client.async_stream_legacy_artifact(
                    thread_id, artifact_id
                )
            else:
                range_headers = safe_range_request_headers(request)
                download = runtime.client.async_stream_artifact(
                    thread_id,
                    artifact_id,
                    range_header=range_headers.get("Range"),
                    if_range=range_headers.get("If-Range"),
                )
                artifact = None
            async with download as upstream:
                upstream_headers = upstream.headers
                if artifact is not None:
                    upstream_headers = {
                        "Content-Disposition": attachment_disposition(
                            artifact.get("filename")
                        ),
                        "Content-Length": upstream.headers.get("Content-Length"),
                    }
                headers = safe_download_headers(upstream.status, upstream_headers)
                response = web.StreamResponse(status=upstream.status, headers=headers)
                await response.prepare(request)
                response_started = True
                if upstream.status != 416:
                    remaining = int(headers["Content-Length"])
                    async for block in upstream.iter_chunked(
                        DOWNLOAD_STREAM_CHUNK_BYTES
                    ):
                        if block:
                            if len(block) > remaining:
                                raise HttpStreamingError(
                                    502, "bridge_response_invalid"
                                )
                            await response.write(block)
                            remaining -= len(block)
                    if remaining != 0:
                        raise HttpStreamingError(502, "bridge_response_invalid")
                await response.write_eof()
                return response
        except HttpStreamingError as error:
            if response_started:
                raise ConnectionResetError("Bridge download stream failed") from None
            return streaming_error_response(error)
        except BridgeApiError as error:
            if response_started:
                raise ConnectionResetError("Bridge download stream failed") from None
            return bridge_error_response(error)
        except RuntimeError:
            if response_started:
                raise ConnectionResetError("Bridge download stream failed") from None
            return _runtime_unavailable_response()


def async_register_http_views(hass: HomeAssistant) -> None:
    hass.http.register_view(CodexBridgeAttachmentUploadView(hass))
    hass.http.register_view(CodexBridgeUploadCreateView(hass))
    hass.http.register_view(CodexBridgeUploadSessionView(hass))
    hass.http.register_view(CodexBridgeUploadChunkView(hass))
    hass.http.register_view(CodexBridgeUploadCompleteView(hass))
    hass.http.register_view(CodexBridgeArtifactDownloadView(hass))
