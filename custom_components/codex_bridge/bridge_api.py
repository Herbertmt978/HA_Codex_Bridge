from dataclasses import dataclass
from typing import Any

import aiohttp


class BridgeApiError(RuntimeError):
    pass


class BridgeApiConnectionError(BridgeApiError):
    pass


class BridgeApiAuthError(BridgeApiError):
    pass


@dataclass(slots=True)
class BridgeDownload:
    content: bytes
    content_type: str


class BridgeApiClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, token: str) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._token = token

    @property
    def base_url(self) -> str:
        return self._base_url

    async def async_health(self) -> dict[str, Any]:
        return await self._async_json("GET", "/health")

    async def async_list_threads(self) -> list[dict[str, Any]]:
        return await self._async_json("GET", "/threads")

    async def async_get_thread(self, thread_id: str) -> dict[str, Any]:
        return await self._async_json("GET", f"/threads/{thread_id}")

    async def async_create_thread(self, title: str, mode: str) -> dict[str, Any]:
        return await self._async_json(
            "POST",
            "/threads",
            json_body={
                "title": title,
                "mode": mode,
            },
            expected_status={201},
        )

    async def async_send_prompt(self, thread_id: str, prompt: str) -> dict[str, Any]:
        return await self._async_json(
            "POST",
            f"/threads/{thread_id}/prompts",
            json_body={"prompt": prompt},
            expected_status={202},
        )

    async def async_get_events(self, thread_id: str, after: int = 0) -> list[dict[str, Any]]:
        return await self._async_json(
            "GET",
            f"/threads/{thread_id}/events/replay?after={after}",
        )

    async def async_list_artifacts(self, thread_id: str) -> list[dict[str, Any]]:
        return await self._async_json("GET", f"/threads/{thread_id}/artifacts")

    async def async_upload_attachment(
        self,
        thread_id: str,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> dict[str, Any]:
        form_data = aiohttp.FormData()
        form_data.add_field(
            "file",
            content,
            filename=filename,
            content_type=content_type,
        )
        return await self._async_json(
            "POST",
            f"/threads/{thread_id}/attachments",
            data=form_data,
            expected_status={201},
        )

    async def async_download_artifact(self, thread_id: str, artifact_id: str) -> BridgeDownload:
        return await self._async_download(
            "GET",
            f"/threads/{thread_id}/artifacts/{artifact_id}",
        )

    async def _async_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        data: Any = None,
        expected_status: set[int] | None = None,
    ) -> Any:
        response = await self._async_request(
            method,
            path,
            json_body=json_body,
            data=data,
            expected_status=expected_status,
        )
        async with response:
            return await response.json()

    async def _async_download(
        self,
        method: str,
        path: str,
    ) -> BridgeDownload:
        response = await self._async_request(method, path)
        async with response:
            return BridgeDownload(
                content=await response.read(),
                content_type=response.headers.get("Content-Type", "application/octet-stream"),
            )

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        data: Any = None,
        expected_status: set[int] | None = None,
    ) -> aiohttp.ClientResponse:
        if expected_status is None:
            expected_status = {200}
        try:
            response = await self._session.request(
                method,
                f"{self._base_url}{path}",
                headers={"Authorization": f"Bearer {self._token}"},
                json=json_body,
                data=data,
            )
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise BridgeApiConnectionError("cannot connect to bridge service") from exc

        if response.status in expected_status:
            return response

        try:
            payload = await response.json()
        except aiohttp.ContentTypeError:
            payload = {}
        finally:
            response.release()

        detail = payload.get("detail", "bridge request failed")
        if response.status == 401:
            raise BridgeApiAuthError(str(detail))
        raise BridgeApiError(str(detail))
