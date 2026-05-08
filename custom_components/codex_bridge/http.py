from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .bridge_api import BridgeApiAuthError, BridgeApiConnectionError, BridgeApiError
from .runtime import async_get_runtime


class CodexBridgeAttachmentUploadView(HomeAssistantView):
    url = "/api/codex_bridge/threads/{thread_id}/attachments"
    name = "api:codex_bridge:thread_attachments"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def post(self, request: web.Request, thread_id: str) -> web.Response:
        try:
            runtime = async_get_runtime(self.hass)
            reader = await request.multipart()
            field = await reader.next()
            if field is None or field.name != "file":
                return web.json_response({"message": "file is required"}, status=400)

            content = await field.read(decode=False)
            result = await runtime.client.async_upload_attachment(
                thread_id=thread_id,
                filename=field.filename or "upload.bin",
                content_type=field.headers.get("Content-Type", "application/octet-stream"),
                content=content,
            )
        except RuntimeError as exc:
            return web.json_response({"message": str(exc)}, status=404)
        except BridgeApiAuthError as exc:
            return web.json_response({"message": str(exc)}, status=401)
        except BridgeApiConnectionError as exc:
            return web.json_response({"message": str(exc)}, status=502)
        except BridgeApiError as exc:
            return web.json_response({"message": str(exc)}, status=500)

        return web.json_response(result, status=201)


class CodexBridgeArtifactDownloadView(HomeAssistantView):
    url = "/api/codex_bridge/threads/{thread_id}/artifacts/{artifact_id}"
    name = "api:codex_bridge:thread_artifact"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, thread_id: str, artifact_id: str) -> web.Response:
        try:
            runtime = async_get_runtime(self.hass)
            artifacts = await runtime.client.async_list_artifacts(thread_id)
            artifact = next(item for item in artifacts if item["artifact_id"] == artifact_id)
            download = await runtime.client.async_download_artifact(thread_id, artifact_id)
        except RuntimeError as exc:
            return web.json_response({"message": str(exc)}, status=404)
        except StopIteration:
            return web.json_response({"message": "artifact not found"}, status=404)
        except BridgeApiAuthError as exc:
            return web.json_response({"message": str(exc)}, status=401)
        except BridgeApiConnectionError as exc:
            return web.json_response({"message": str(exc)}, status=502)
        except BridgeApiError as exc:
            return web.json_response({"message": str(exc)}, status=500)

        return web.Response(
            body=download.content,
            content_type=artifact["mime_type"],
            headers={
                "Content-Disposition": f'attachment; filename="{artifact["filename"]}"',
            },
        )


def async_register_http_views(hass: HomeAssistant) -> None:
    hass.http.register_view(CodexBridgeAttachmentUploadView(hass))
    hass.http.register_view(CodexBridgeArtifactDownloadView(hass))
