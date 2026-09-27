"""Child-only negotiated HA transport; requires the coordinator's Linux lane."""

import json
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
from aiohttp import web
import pytest

from custom_components.codex_bridge.bridge_api import BridgeApiClient, BridgeApiCapabilityError
from custom_components.codex_bridge.const import DATA_ENTRIES, DOMAIN
from custom_components.codex_bridge.event_broker import EventBroker
from custom_components.codex_bridge.runtime import CodexBridgeRuntime
from custom_components.codex_bridge.websocket_api import ws_child_agent_action

FIXTURES = Path(__file__).parents[2] / "fixtures"
TOKEN = "bridge-token-0123456789abcdef0123456789"


@pytest.mark.parametrize("supported", [False, True])
async def test_child_routes_require_negotiation_and_preserve_exact_target(bridge_server_factory, supported):
    ready = json.loads((FIXTURES / "ready_v1.json").read_text(encoding="utf-8"))
    if supported:
        ready["capabilities"].append("subagents_v1")
    calls = []

    async def handler(request):
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.headers["X-Codex-Bridge-Api"] == "1"
        if request.path == "/ready":
            return web.json_response(ready)
        payload = await request.json() if request.method == "POST" else None
        calls.append((request.method, request.path, payload))
        return web.json_response({"children": []} if request.method == "GET" else {"outcome": "accepted"})

    server = await bridge_server_factory(handler)
    child_id = "a" * 32
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        await client.async_ready()
        if supported:
            await client.async_child_agents("parent")
            await client.async_child_agent_action("parent", child_id, "refresh")
            await client.async_child_agent_action("parent", child_id, "stop", revision=7, client_request_id="reviewed-stop")
            with pytest.raises(ValueError):
                await client.async_child_agent_action("parent", child_id, "follow-up")
        else:
            with pytest.raises(BridgeApiCapabilityError):
                await client.async_child_agents("parent")
            with pytest.raises(BridgeApiCapabilityError):
                await client.async_child_agent_action("parent", child_id, "stop", revision=7, client_request_id="reviewed-stop")
    assert calls == ([
        ("GET", "/threads/parent/children", None),
        ("POST", f"/threads/parent/children/{child_id}/refresh", {}),
        ("POST", f"/threads/parent/children/{child_id}/stop", {"revision": 7, "client_request_id": "reviewed-stop"}),
    ] if supported else [])


@pytest.mark.parametrize(("code", "message"), [
    ("child_stale", "This child's ownership or runtime changed. Refresh and verify its status"),
    ("child_ownership_conflict", "The runtime could not verify this child's parent and workspace"),
    ("child_follow_up_unavailable", "Direct child follow-up is unavailable in this runtime"),
    ("child_control_uncertain", "The stop outcome could not be confirmed. Verify this child's status"),
    ("child_turn_changed", "This child's active turn changed. Verify its status before stopping it"),
])
async def test_child_refusals_survive_real_client_and_ws_without_private_error(code, message, bridge_server_factory):
    ready = json.loads((FIXTURES / "ready_v1.json").read_text(encoding="utf-8"))
    ready["capabilities"].append("subagents_v1")

    async def handler(request):
        if request.path == "/ready":
            return web.json_response(ready)
        return web.json_response({"detail": {"code": code, "message": "private-provider-sentinel", "retryable": False}}, status=409)

    server = await bridge_server_factory(handler)
    tasks = []
    errors = []
    results = []
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        await client.async_ready()
        dummy_client = AsyncMock()
        dummy_client.async_refresh_ready.return_value = SimpleNamespace(capabilities=())
        broker = EventBroker(dummy_client, initial_cursor=0)
        runtime = CodexBridgeRuntime("entry", "Codex", client, "supervisor", "a" * 32, 1, broker)

        def create_task(coroutine, *_args, **_kwargs):
            task = asyncio.create_task(coroutine)
            tasks.append(task)
            return task

        hass = SimpleNamespace(data={DOMAIN: {DATA_ENTRIES: {"entry": runtime}}},
                               async_create_task=create_task,
                               async_create_background_task=create_task)
        connection = SimpleNamespace(
            send_error=lambda *values: errors.append(values),
            send_result=lambda *values: results.append(values),
        )
        ws_child_agent_action(hass, connection, {"id": 8, "type": f"{DOMAIN}/child_agent_action",
                              "thread_id": "parent", "child_id": "a" * 32, "action": "stop",
                              "revision": 7, "client_request_id": "reviewed-stop"})
        await asyncio.gather(*tasks)
    assert errors == [(8, code, message)]
    assert results == []
    assert "private-provider-sentinel" not in repr(errors)
