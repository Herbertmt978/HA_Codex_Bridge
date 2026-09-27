"""Validate actual panel payloads and definite refusal codes at HA's boundary."""

import json
from pathlib import Path

import aiohttp
from aiohttp import web
import pytest
import voluptuous as vol

from custom_components.codex_bridge.bridge_api import BridgeApiClient
from custom_components.codex_bridge.protocol import ReadyRecord
from codex_bridge_service.models import BridgeReadinessRecord
from custom_components.codex_bridge.websocket_api import (
    ws_read_chat_context, ws_read_workspace_context, ws_send_prompt,
)
from test_bridge_api import _fixture, TOKEN
from test_websocket_api import _runtime, _Hass, _Connection

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
REQUESTS = json.loads((FIXTURES / "context_requests.json").read_text(encoding="utf-8"))
PROBLEMS = json.loads((FIXTURES / "context_problems.json").read_text(encoding="utf-8"))
COMMANDS = {"codex_bridge/read_workspace_context": ws_read_workspace_context,
            "codex_bridge/read_chat_context": ws_read_chat_context,
            "codex_bridge/send_prompt": ws_send_prompt}


def test_selected_runtime_capabilities_survive_both_typed_ready_contracts():
    capabilities = ("api_v1", "durable_goals_v1", "subagents_v1", "usage_history_v1", "elapsed_time_limit_v1")
    payload = BridgeReadinessRecord(bridge={"version": "0.20.0"}, capabilities=capabilities).model_dump(mode="json")
    assert ReadyRecord.from_payload(payload).capabilities == capabilities


def _ready():
    ready = _fixture("ready_v1.json")
    ready["capabilities"] = list(set(ready["capabilities"]) | {"workspace_context_v1", "chat_context_v1"})
    return ready


@pytest.mark.parametrize("payload", list(REQUESTS.values()), ids=list(REQUESTS))
async def test_panel_nullable_context_payload_is_validated_and_forwarded(payload, bridge_server_factory):
    received = []

    async def handler(request):
        if request.path == "/ready":
            return web.json_response(_ready())
        received.append((request.path, await request.json()))
        if request.path.endswith("/prompts"):
            return web.json_response({"run_id": "run_context", "status": "starting"}, status=202)
        return web.json_response({"status": "ready", "text": "Exact context"})

    server = await bridge_server_factory(handler)
    runtime, _ = _runtime()
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        runtime.client = client
        runtime.capabilities = ("workspace_context_v1", "chat_context_v1")
        await client.async_ready()
        hass, connection = _Hass(runtime), _Connection()
        command = COMMANDS[payload["type"]]
        message = command._ws_schema({"id": 701, **payload})
        command(hass, connection, message)
        await hass.finish()
        for task in hass.tasks:
            task.result()
        assert not connection.errors
        assert len(connection.results) == len(received) == 1
        if payload["type"] == "codex_bridge/send_prompt":
            for field in ("workspace_context", "chat_context"):
                if field in payload:
                    assert received[0][1][field] == payload[field]
        elif payload["type"] == "codex_bridge/read_workspace_context":
            assert received[0] == ("/threads/destination/workspace-context/read", {"path": "notes.txt"})
        else:
            assert received[0] == ("/threads/destination/chat-context/read", {"source_thread_id": "earlier-chat"})


@pytest.mark.parametrize("problem", PROBLEMS, ids=[problem["code"] for problem in PROBLEMS])
async def test_context_refusal_survives_http_protocol_and_websocket(problem, bridge_server_factory):
    received = []

    async def handler(request):
        if request.path == "/ready":
            return web.json_response(_ready())
        received.append(await request.json())
        return web.json_response({"detail": {"code": problem["code"], "retryable": False,
                                "message": "private-file-content-sentinel"}}, status=problem["status"])

    server = await bridge_server_factory(handler)
    runtime, _ = _runtime()
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        runtime.client = client
        runtime.capabilities = ("workspace_context_v1", "chat_context_v1")
        await client.async_ready()
        hass, connection = _Hass(runtime), _Connection()
        message = ws_send_prompt._ws_schema({"id": 702, **REQUESTS["send_file"]})
        ws_send_prompt(hass, connection, message)
        await hass.finish()
        for task in hass.tasks:
            task.result()
        assert received[0]["workspace_context"] == REQUESTS["send_file"]["workspace_context"]
        assert connection.errors == [(702, problem["code"], problem["message"])]
        assert not connection.results
        assert "private-file-content-sentinel" not in str(connection.errors)


@pytest.mark.parametrize("invalid", [0, -1, "1", 9_007_199_254_740_992])
def test_nullable_file_selector_keeps_its_bounds(invalid):
    with pytest.raises(vol.Invalid):
        ws_read_workspace_context._ws_schema({"id": 703, **REQUESTS["read_file"], "start_line": invalid})


@pytest.mark.parametrize("field,invalid", [("message_sequence", 0), ("start_char", -1), ("end_char", 0),
                                         ("start_char", 1_048_577), ("end_char", "3")])
def test_nullable_chat_selector_keeps_its_bounds(field, invalid):
    payload = json.loads(json.dumps(REQUESTS["send_chat"]))
    payload["chat_context"][0][field] = invalid
    with pytest.raises(vol.Invalid):
        ws_send_prompt._ws_schema({"id": 704, **payload})
