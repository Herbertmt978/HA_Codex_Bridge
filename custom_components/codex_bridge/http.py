"""Authenticated Home Assistant HTTP views for private Bridge file traffic."""

from __future__ import annotations

from collections.abc import Mapping
import asyncio
import json
import re

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


class CodexBridgeMcpCredentialView(HomeAssistantView):
    """Write-only credential operations outside WebSocket payload logging."""

    url = "/api/codex_bridge/mcp/credentials"
    name = "api:codex_bridge:mcp_credentials"
    requires_auth = True
    operations = frozenset({"create", "replace", "remove"})

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def post(self, request: web.Request) -> web.Response:
        _require_admin(request)
        headers = {"Cache-Control": "no-store"}
        try:
            if request.content_type != "application/json":
                raise ValueError()
            body = bytearray()
            async with asyncio.timeout(15):
                async for chunk in request.content.iter_chunked(4096):
                    body.extend(chunk)
                    if len(body) > 24 * 1024:
                        raise ValueError()
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError()
            operation = payload.pop("operation", None)
            if operation not in self.operations:
                raise ValueError()
            name = payload.get("name")
            if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
                raise ValueError()
            runtime = async_get_runtime(self.hass)
            runtime.client.require_capability("mcp_management_v1" if operation in {"edit", "state"} else "mcp_credentials_v1")
            if operation == "create":
                allowed = {"name", "url", "local", "local_acknowledged", "authentication", "auth_acknowledged", "require_tool_selection"}
                if set(payload) - allowed or "authentication" not in payload:
                    raise ValueError()
                await runtime.client.async_add_mcp(payload)
            elif operation == "replace":
                if set(payload) != {"name", "authentication", "auth_acknowledged"}:
                    raise ValueError()
                await runtime.client.async_replace_mcp_credential(name, {key: value for key, value in payload.items() if key != "name"})
            elif operation == "remove":
                if set(payload) != {"name"}:
                    raise ValueError()
                await runtime.client.async_replace_mcp_credential(name, None)
            else:
                allowed = ({"name", "enabled", "revision"} if operation == "state" else
                           {"name", "url", "revision", "endpoint_acknowledged", "credential_action", "authentication"})
                if set(payload) - allowed:
                    raise ValueError()
                await runtime.client.async_manage_mcp(name, {key: value for key, value in payload.items() if key != "name"}, state=operation == "state")
            # Never reflect even an unexpected provider response.
            return web.json_response({"saved": True}, headers=headers)
        except (ValueError, TypeError, UnicodeError, asyncio.TimeoutError):
            return web.json_response({"code": "mcp_request_invalid"}, status=400, headers=headers)
        except BridgeApiError as error:
            if error.code == "mcp_restart_required":
                return web.json_response({"code": "mcp_restart_required"}, status=503, headers=headers)
            response = bridge_error_response(error)
            response.headers.update(headers)
            return response
        except RuntimeError:
            response = _runtime_unavailable_response()
            response.headers.update(headers)
            return response


class CodexBridgeMcpConnectionView(CodexBridgeMcpCredentialView):
    """Bounded, write-only connection edits and lifecycle changes."""

    url = "/api/codex_bridge/mcp/connections"
    name = "api:codex_bridge:mcp_connections"
    operations = frozenset({"edit", "state"})


_DISCORD_DIAGNOSTICS = frozenset({
    "task_recovery_unavailable",
    "discord_dependency_unavailable",
    "gateway_unavailable",
    "task_status_unavailable",
    "delivery_permission_denied",
    "delivery_rate_limited",
    "credential_rejected",
    "delivery_unavailable",
})


def _discord_public_status(value: object) -> dict[str, object] | None:
    """Project only typed, non-secret App status through the HA boundary."""

    if not isinstance(value, dict):
        return None
    if any(
        type(value.get(key)) is not bool
        for key in ("enabled", "credential_present", "connected")
    ):
        return None
    if type(value.get("revision")) is not int or value["revision"] < 0:
        return None
    users, guilds = value.get("dm_user_ids"), value.get("guilds")
    def valid_id(item: object) -> bool:
        return isinstance(item, str) and re.fullmatch(r"[0-9]{17,20}", item) is not None

    if not isinstance(users, list) or len(users) > 32 or not all(valid_id(item) for item in users):
        return None
    if not isinstance(guilds, list) or len(guilds) > 16:
        return None
    safe_guilds = []
    for guild in guilds:
        if not isinstance(guild, dict) or not valid_id(guild.get("guild_id")):
            return None
        channels, members = guild.get("channel_ids"), guild.get("user_ids")
        if (
            not isinstance(channels, list) or not 1 <= len(channels) <= 32
            or not isinstance(members, list) or not 1 <= len(members) <= 32
            or not all(valid_id(item) for item in channels + members)
        ):
            return None
        safe_guilds.append({
            "guild_id": guild["guild_id"],
            "channel_ids": channels[:],
            "user_ids": members[:],
        })
    diagnostic = value.get("diagnostic")
    if diagnostic is not None and (
        not isinstance(diagnostic, str) or diagnostic not in _DISCORD_DIAGNOSTICS
    ):
        diagnostic = "discord_status_unavailable"
    return {
        "enabled": value["enabled"],
        "dm_user_ids": users[:],
        "guilds": safe_guilds,
        "credential_present": value["credential_present"],
        "revision": value["revision"],
        "connected": value["connected"],
        "diagnostic": diagnostic,
    }


class CodexBridgeDiscordView(HomeAssistantView):
    """Administrator-only, bounded Discord policy and write-only credential."""

    url = "/api/codex_bridge/discord"
    name = "api:codex_bridge:discord"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        _require_admin(request)
        headers = {"Cache-Control": "no-store"}
        try:
            runtime = async_get_runtime(self.hass)
            result = await runtime.client.async_get_discord_config()
            public = _discord_public_status(result)
            if public is None:
                return web.json_response({"code": "bridge_invalid_response"}, status=502, headers=headers)
            return web.json_response(public, headers=headers)
        except BridgeApiError as error:
            response = bridge_error_response(error)
            response.headers.update(headers)
            return response
        except RuntimeError:
            response = _runtime_unavailable_response()
            response.headers.update(headers)
            return response

    async def put(self, request: web.Request) -> web.Response:
        _require_admin(request)
        headers = {"Cache-Control": "no-store"}
        try:
            if request.content_type != "application/json":
                raise ValueError()
            body = bytearray()
            async with asyncio.timeout(15):
                async for chunk in request.content.iter_chunked(4096):
                    body.extend(chunk)
                    if len(body) > 16 * 1024:
                        raise ValueError()
            payload = json.loads(body)
            if not isinstance(payload, dict) or set(payload) - {
                "enabled", "dm_user_ids", "guilds", "bot_token"
            }:
                raise ValueError()
            runtime = async_get_runtime(self.hass)
            await runtime.client.async_set_discord_config(payload)
            return web.json_response({"saved": True}, headers=headers)
        except (ValueError, TypeError, UnicodeError, asyncio.TimeoutError):
            return web.json_response({"code": "discord_config_invalid"}, status=400, headers=headers)
        except BridgeApiError as error:
            response = bridge_error_response(error)
            response.headers.update(headers)
            return response
        except RuntimeError:
            response = _runtime_unavailable_response()
            response.headers.update(headers)
            return response

    async def delete(self, request: web.Request) -> web.Response:
        _require_admin(request)
        headers = {"Cache-Control": "no-store"}
        try:
            runtime = async_get_runtime(self.hass)
            await runtime.client.async_revoke_discord()
            return web.json_response({"revoked": True}, headers=headers)
        except BridgeApiError as error:
            response = bridge_error_response(error)
            response.headers.update(headers)
            return response
        except RuntimeError:
            response = _runtime_unavailable_response()
            response.headers.update(headers)
            return response


def async_register_http_views(hass: HomeAssistant) -> None:
    hass.http.register_view(CodexBridgeDiscordView(hass))
    hass.http.register_view(CodexBridgeMcpCredentialView(hass))
    hass.http.register_view(CodexBridgeMcpConnectionView(hass))
    hass.http.register_view(CodexBridgeAttachmentUploadView(hass))
    hass.http.register_view(CodexBridgeUploadCreateView(hass))
    hass.http.register_view(CodexBridgeUploadSessionView(hass))
    hass.http.register_view(CodexBridgeUploadChunkView(hass))
    hass.http.register_view(CodexBridgeUploadCompleteView(hass))
    hass.http.register_view(CodexBridgeArtifactDownloadView(hass))
