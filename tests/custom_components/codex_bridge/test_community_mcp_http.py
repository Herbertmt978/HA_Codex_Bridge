"""Administrator quick connect binds visible consent to fresh private discovery."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import Unauthorized

from custom_components.codex_bridge.community_mcp import CodexBridgeCommunityMcpView
from custom_components.codex_bridge.community_mcp_discovery import CommunityMcpEndpoint, CommunityMcpDiscoveryError
from custom_components.codex_bridge.bridge_api import BridgeApiError
from custom_components.codex_bridge.const import CONNECTION_TYPE_SUPERVISOR
from test_ha_mcp_http import Request


@pytest.fixture
def view():
    user = SimpleNamespace(id="fixture-admin", is_admin=True, is_active=True)
    hass = SimpleNamespace(auth=SimpleNamespace(async_get_user=AsyncMock(return_value=user)))
    endpoint = CommunityMcpEndpoint("fixture_ha_mcp", "ha-community-123456789abc",
                                   "http://ha.local:9583/private_synthetic-fixture", "8.5.0")
    client = SimpleNamespace(async_community_mcp=AsyncMock(return_value={
        "state": "configured", "server_name": endpoint.name, "reused": False,
        "url": endpoint.url, "token": "synthetic-private-secret"}))
    runtime = SimpleNamespace(client=client, connection_type=CONNECTION_TYPE_SUPERVISOR,
                              async_refresh_capabilities=AsyncMock(), supports_capability=lambda _: True)
    with patch("custom_components.codex_bridge.community_mcp.async_get_runtime", return_value=runtime), patch(
        "custom_components.codex_bridge.community_mcp.async_discover_community_mcp", AsyncMock(return_value=endpoint)) as discover:
        yield SimpleNamespace(view=CodexBridgeCommunityMcpView(hass), client=client,
                              endpoint=endpoint, runtime=runtime, discover=discover, user=user)


@pytest.mark.asyncio
async def test_connect_uses_private_endpoint_and_public_response(view):
    response = await view.view.post(Request(view.user, {
        "acknowledged": True, "consent_revision": view.endpoint.consent_revision}))
    assert response.status == 200
    assert response.headers["Cache-Control"] == "no-store"
    result = json.loads(response.text)
    assert result["destination"] == "http://ha.local:9583"
    assert "private_synthetic-fixture" not in response.text
    assert "synthetic-private-secret" not in response.text
    view.client.async_community_mcp.assert_awaited_once_with(
        name=view.endpoint.name, url=view.endpoint.url, connect=True)


@pytest.mark.asyncio
async def test_status_does_not_connect(view):
    response = await view.view.get(Request(view.user))
    assert response.status == 200
    view.client.async_community_mcp.assert_awaited_once_with(
        name=view.endpoint.name, url=view.endpoint.url, connect=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [None, SimpleNamespace(is_admin=False), SimpleNamespace(is_admin=True, is_active=False)])
async def test_non_admin_or_inactive_never_discovers_or_connects(view, user):
    for method in (view.view.get, view.view.post):
        with pytest.raises(Unauthorized):
            await method(Request(user, {}))
    view.discover.assert_not_awaited()
    view.client.async_community_mcp.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"acknowledged": 1, "consent_revision": "a" * 64},
    {"acknowledged": True, "consent_revision": "a" * 64, "url": "private-fixture"},
    {"acknowledged": True, "consent_revision": "a" * 64, "user_id": "fixture-other"}, []])
async def test_invalid_consent_identity_and_destination_never_reach_discovery(view, payload):
    response = await view.view.post(Request(view.user, payload))
    assert response.status == 400
    view.discover.assert_not_awaited()


@pytest.mark.asyncio
async def test_rotated_secret_or_changed_destination_requires_fresh_consent(view):
    response = await view.view.post(Request(view.user, {"acknowledged": True, "consent_revision": "0" * 64}))
    assert response.status == 409
    assert json.loads(response.text)["state"] == "connection_changed"
    view.client.async_community_mcp.assert_not_awaited()


@pytest.mark.asyncio
async def test_capability_and_external_mode_never_read_secret(view):
    view.runtime.supports_capability = lambda _: False
    assert json.loads((await view.view.get(Request(view.user))).text)["state"] == "enable_mcp"
    view.runtime.connection_type = "external"
    assert json.loads((await view.view.get(Request(view.user))).text)["state"] == "unsupported"
    view.discover.assert_not_awaited()


@pytest.mark.asyncio
async def test_fixed_upstream_errors_never_reflect_private_detail(view):
    error = BridgeApiError("community_mcp_connection_changed")
    error.args = ("private-fixture",)
    view.client.async_community_mcp.side_effect = error
    response = await view.view.get(Request(view.user))
    assert "private-fixture" not in response.text
    assert json.loads(response.text)["state"] == "connection_changed"
    view.discover.side_effect = CommunityMcpDiscoveryError("ambiguous")
    assert json.loads((await view.view.get(Request(view.user))).text)["state"] == "ambiguous"
