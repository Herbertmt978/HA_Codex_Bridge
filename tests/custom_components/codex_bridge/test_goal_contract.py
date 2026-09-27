"""Manual-goal HA proxy contracts, without native model or Home Assistant mutation."""

import pytest
import aiohttp
import voluptuous as vol
from unittest.mock import create_autospec
from aiohttp import web

from custom_components.codex_bridge.bridge_api import BridgeApiCapabilityError, BridgeApiClient
from custom_components.codex_bridge.const import DOMAIN
from custom_components.codex_bridge.protocol import ReadyRecord
from custom_components.codex_bridge.websocket_api import ws_get_goal, ws_goal_action
from test_websocket_api import _Connection, _Hass, _runtime
from test_bridge_api import TOKEN, _fixture


async def test_goal_websocket_uses_only_local_chat_and_reviewed_action():
    runtime, _broker = _runtime()
    hass = _Hass(runtime)
    connection = _Connection()
    runtime.client.async_get_goal.return_value = {"revision": 0, "goal": None, "continuation": "manual_turns_only"}
    runtime.client.async_goal_action.return_value = {"revision": 1, "goal": {"status": "paused"}}
    get_request = ws_get_goal._ws_schema({"id": 401, "type": f"{DOMAIN}/get_goal", "thread_id": "local-chat"})
    ws_get_goal(hass, connection, get_request)
    await hass.finish()
    runtime.client.async_get_goal.assert_awaited_once_with("local-chat")
    payload = {"action": "create", "expected_revision": 0, "client_request_id": "goal-create",
               "objective": "Repair importer", "completion_criteria": ["Existing data retained"]}
    action_request = ws_goal_action._ws_schema({"id": 402, "type": f"{DOMAIN}/goal_action", "thread_id": "local-chat", **payload})
    ws_goal_action(hass, connection, action_request)
    await hass.finish()
    runtime.client.async_goal_action.assert_awaited_once_with("local-chat", payload)
    assert connection.errors == []
    assert connection.results[1][0] == 402
    runtime.client.async_send_prompt.assert_not_awaited()


@pytest.mark.parametrize("change", [
    {"thread_id": "../another-chat"}, {"expected_revision": -1}, {"action": "run_forever"},
    {"mode": "full_auto"}, {"provider_thread_id": "private-native-handle"},
    {"completion_criteria": ["criterion"] * 17}, {"progress": "x" * 8193},
])
def test_goal_websocket_rejects_unreviewed_scope_and_limits(change):
    with pytest.raises(vol.Invalid):
        ws_goal_action._ws_schema({"id": 403, "type": f"{DOMAIN}/goal_action", "thread_id": "local-chat",
                                   "action": "resume", "expected_revision": 1, "client_request_id": "resume-reviewed", **change})


async def test_goal_bridge_client_requires_capability_and_uses_private_route_only():
    client = BridgeApiClient(None, "http://127.0.0.1:8787", TOKEN)
    client._async_json = create_autospec(client._async_json, spec_set=True)
    with pytest.raises(BridgeApiCapabilityError):
        await client.async_get_goal("local-chat")
    client._async_json.assert_not_awaited()
    ready = _fixture("ready_v1.json")
    ready["capabilities"].append("durable_goals_v1")
    parsed = ReadyRecord.from_payload(ready)
    client._api_version = 1
    client._capabilities = frozenset(parsed.capabilities)
    await client.async_get_goal("local-chat")
    client._async_json.assert_awaited_once_with("GET", "/threads/local-chat/goal")
    payload = {"action": "resume", "expected_revision": 1, "client_request_id": "resume-once"}
    await client.async_goal_action("local-chat", payload)
    client._async_json.assert_awaited_with("POST", "/threads/local-chat/goal/actions", json_body=payload)


@pytest.mark.parametrize(("code", "status", "message"), [
    ("goal_revision_conflict", 409, "The goal changed; refresh before trying again"),
    ("goal_conflict", 409, "Pause before editing; finished goals need a new goal"),
    ("goals_unavailable", 503, "This App cannot manage goals for this chat"),
    ("goal_invalid", 422, "Review the goal text and completion confirmation"),
    ("goal_chat_not_found", 404, "The chat or project no longer exists"),
    ("goal_context_stale", 409, "The accepted goal changed; review it and send the retained prompt again"),
])
async def test_goal_refusals_survive_real_http_protocol_and_websocket(code, status, message, bridge_server_factory):
    ready = _fixture("ready_v1.json")
    ready["capabilities"].append("durable_goals_v1")
    requests = []
    async def handler(request):
        if request.path == "/ready":
            return web.json_response(ready)
        requests.append((request.method, request.path, await request.json()))
        return web.json_response({"detail": {"code": code, "retryable": False,
                                             "message": "private-task-text-sentinel"}}, status=status)
    server = await bridge_server_factory(handler)
    runtime, _broker = _runtime()
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        await client.async_ready()
        runtime.client = client
        hass = _Hass(runtime)
        connection = _Connection()
        ws_goal_action(hass, connection, {"id": 404, "type": f"{DOMAIN}/goal_action", "thread_id": "local-chat",
                                         "action": "resume", "expected_revision": 1, "client_request_id": "reviewed-once"})
        await hass.finish()
        assert hass.tasks
        for task in hass.tasks:
            task.result()  # Do not let the mock HA gather hide programming errors.
    assert requests == [("POST", "/threads/local-chat/goal/actions", {
        "action": "resume", "expected_revision": 1, "client_request_id": "reviewed-once",
    })]
    assert connection.errors == [(404, code, message)]
    assert connection.results == []
    assert "private-task-text-sentinel" not in repr(connection.errors)
