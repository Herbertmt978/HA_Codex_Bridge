"""Exercise the actual readiness wire through Integration and browser gating."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from fastapi.testclient import TestClient
import pytest

from codex_bridge_service.app import create_app
from codex_bridge_service.models import RuntimeProfile
from custom_components.codex_bridge.bridge_api import BridgeApiClient
from custom_components.codex_bridge.community_mcp import CodexBridgeCommunityMcpView
from custom_components.codex_bridge.community_mcp_discovery import CommunityMcpEndpoint
from custom_components.codex_bridge.protocol import ReadyRecord
from custom_components.codex_bridge.runtime import CodexBridgeRuntime
from custom_components.codex_bridge.websocket_api import ws_get_config
from test_ha_mcp_http import Request
from test_websocket_api import _Hass, _Connection

CAPABILITY = "community_mcp_quick_connect_v1"
TOKEN = "synthetic-private-bridge-token-0123456789abcdef"


@pytest.mark.parametrize("mode", ["paired", "older", "mcp_disabled", "local_disabled"])
async def test_community_capability_survives_real_producer_client_runtime_and_public_gates(
    tmp_path, bridge_server_factory, mode,
):
    workspace = tmp_path / "workspaces"
    home = tmp_path / "codex-home"
    workspace.mkdir()
    home.mkdir()
    app = create_app(root_path=tmp_path / "state", auth_token=TOKEN,
        runtime_profile=RuntimeProfile.HOME_ASSISTANT, workspace_root=workspace,
        codex_home=home, enable_mcp=mode != "mcp_disabled",
        enable_local_mcp=mode != "local_disabled")
    response = TestClient(app).get("/ready", headers={
        "Authorization": "Bearer " + TOKEN, "X-Codex-Bridge-Api": "1"})
    assert response.status_code == 200
    payload = response.json()
    assert (CAPABILITY in payload["capabilities"]) == (mode in {"paired", "older"})
    if mode == "older":
        # An older App still advertises its existing MCP APIs, but not this shortcut.
        payload["capabilities"].remove(CAPABILITY)
        assert "mcp_admin_v1" in payload["capabilities"]
        assert "mcp_local_v1" in payload["capabilities"]
    expected = mode == "paired"
    assert (CAPABILITY in ReadyRecord.from_payload(payload).capabilities) == expected
    received = []

    async def handler(request):
        assert request.headers["Authorization"] == "Bearer " + TOKEN
        if request.path == "/ready":
            return web.json_response(payload)
        assert request.path == "/mcp/community/connection"
        received.append(await request.json())
        return web.json_response({"state": "not_connected", "server_name": None, "reused": False})

    server = await bridge_server_factory(handler)
    user = SimpleNamespace(id="synthetic-admin", is_admin=True, is_active=True)
    endpoint = CommunityMcpEndpoint("fixture_ha_mcp", "ha-community-123456789abc",
        "http://ha.local:9583/private_synthetic-fixture", "8")
    async with aiohttp.ClientSession() as session:
        client = BridgeApiClient(session, str(server.make_url("")), TOKEN)
        runtime = CodexBridgeRuntime("entry", "Codex", client, "supervisor", "a" * 32, 1)
        assert await runtime.async_refresh_capabilities(force=True)
        assert runtime.supports_capability(CAPABILITY) == expected
        hass = _Hass(runtime)
        hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=user))
        connection = _Connection()
        ws_get_config(hass, connection, {"id": 1, "type": "codex_bridge/get_config"})
        await hass.finish()
        assert not connection.errors
        assert (CAPABILITY in connection.results[0][1]["capabilities"]) == expected
        with patch("custom_components.codex_bridge.community_mcp.async_discover_community_mcp",
                   AsyncMock(return_value=endpoint)) as discover:
            public = await CodexBridgeCommunityMcpView(hass).get(Request(user))
            state = json.loads(public.text)
            assert state["state"] == ("not_connected" if expected else "enable_mcp")
            assert "private_synthetic-fixture" not in public.text
            if expected:
                discover.assert_awaited_once_with(hass)
                assert received == [{"name": endpoint.name, "url": endpoint.url,
                                     "connect": False, "acknowledged": False}]
                assert state["version"] == "8"
            else:
                discover.assert_not_awaited()
                assert received == []
