"""HA public entrypoint contracts for explicit previous-chat context."""

from unittest.mock import AsyncMock

import aiohttp
from aiohttp import web
import pytest

from custom_components.codex_bridge.bridge_api import BridgeApiClient, BridgeApiError, BridgeApiConflictError
from custom_components.codex_bridge.protocol import ProblemRecord
from custom_components.codex_bridge.websocket_api import ws_chat_context, ws_read_chat_context, ws_send_prompt
from test_bridge_api import _fixture, TOKEN
from test_websocket_api import _runtime, _Hass, _Connection


async def test_chat_context_client_negotiates_and_forwards_exact_selection(bridge_server_factory):
    requests = []

    async def handler(request):
        if request.path == "/ready":
            ready = _fixture("ready_v1.json")
            ready["capabilities"].append("chat_context_v1")
            return web.json_response(ready)
        body = await request.json() if request.can_read_body else None
        requests.append((request.method, request.path, dict(request.query), body))
        if request.method == "POST" and request.path.endswith("/prompts"):
            return web.json_response({"run_id": "run_context", "status": "starting"}, status=202)
        return web.json_response({"text": "exact attributed preview"})

    server = await bridge_server_factory(handler)
    reference = {"source_thread_id": "thr_source", "message_sequence": 4, "start_char": 1, "end_char": 3, "content_revision": "a" * 64}
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        with pytest.raises(BridgeApiError):
            await client.async_chat_context("thr_dest", source_thread_id="thr_source")
        await client.async_ready()
        await client.async_chat_context("thr_dest", source_thread_id="thr_source", before_sequence=6)
        await client.async_read_chat_context("thr_dest", **{key: value for key, value in reference.items() if key != "content_revision"})
        await client.async_send_prompt("thr_dest", "Prompt", chat_context=[reference], client_request_id="context-request")
    assert requests[0] == ("GET", "/threads/thr_dest/chat-context/thr_source", {"before_sequence": "6"}, None)
    assert requests[1][3] == {key: value for key, value in reference.items() if key != "content_revision"}
    assert requests[2][3]["chat_context"] == [reference]


async def test_chat_context_websocket_preserves_selectors_and_rejects_old_app():
    runtime, _ = _runtime()
    runtime.capabilities = ("chat_context_v1",)
    runtime.client.async_chat_context = AsyncMock(return_value={"messages": []})
    runtime.client.async_read_chat_context = AsyncMock(return_value={"text": "Preview"})
    runtime.client.async_send_prompt = AsyncMock(return_value={"run_id": "run_1"})
    hass, connection = _Hass(runtime), _Connection()
    ws_chat_context(hass, connection, {"id": 1, "thread_id": "dest", "source_thread_id": "source", "before_sequence": 4})
    ws_read_chat_context(hass, connection, {"id": 2, "thread_id": "dest", "source_thread_id": "source", "message_sequence": 3, "start_char": 0, "end_char": 2})
    reference = {"source_thread_id": "source", "content_revision": "a" * 64}
    ws_send_prompt(hass, connection, {"id": 3, "thread_id": "dest", "prompt": "Prompt", "chat_context": [reference]})
    await hass.finish()
    runtime.client.async_chat_context.assert_awaited_once_with("dest", source_thread_id="source", before_sequence=4)
    runtime.client.async_read_chat_context.assert_awaited_once_with("dest", source_thread_id="source", message_sequence=3, start_char=0, end_char=2)
    runtime.client.async_send_prompt.assert_awaited_once_with("dest", "Prompt", client_request_id=None, chat_context=[reference])
    runtime.capabilities = ()
    ws_send_prompt(hass, connection, {"id": 4, "thread_id": "dest", "prompt": "Prompt", "chat_context": [reference]})
    await hass.finish()
    assert connection.errors[-1][1] == "capabilities_unavailable"
    assert runtime.client.async_send_prompt.await_count == 1


@pytest.mark.parametrize("state", ["changed", "deleted", "expired", "inaccessible"])
async def test_safe_chat_context_errors_survive_websocket_without_private_detail(state):
    runtime, _ = _runtime()
    problem = ProblemRecord.from_payload(409, {"detail": {"code": "chat_context_" + state, "message": "private-detail"}})
    runtime.client.async_read_chat_context = AsyncMock(side_effect=BridgeApiConflictError(problem=problem))
    hass, connection = _Hass(runtime), _Connection()
    ws_read_chat_context(hass, connection, {"id": 1, "thread_id": "dest", "source_thread_id": "source"})
    await hass.finish()
    assert connection.errors[0][1] == "chat_context_" + state
    assert "private-detail" not in str(connection.errors)
