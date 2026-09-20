"""Exercise administrator authorisation at the actual HA WebSocket boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.setup import async_setup_component

from custom_components.codex_bridge.const import DATA_ENTRIES, DOMAIN
from custom_components.codex_bridge.websocket_api import async_register_websocket_commands


@pytest.mark.parametrize("admin", [False, True])
async def test_host_access_controls_are_admin_only(hass, hass_ws_client, hass_access_token, hass_read_only_access_token, admin):
    assert await async_setup_component(hass, "websocket_api", {})
    client = AsyncMock()
    client.async_host_access.return_value = {"state": "ready", "enabled": False}
    client.async_enable_host_access.return_value = {"state": "ready", "enabled": True}
    client.async_revoke_host_access.return_value = {"enabled": False}
    hass.data.setdefault(DOMAIN, {})[DATA_ENTRIES] = {"entry": SimpleNamespace(client=client)}
    async_register_websocket_commands(hass)
    connection = await hass_ws_client(hass, hass_access_token if admin else hass_read_only_access_token)
    for index, method in enumerate(("host_access", "enable_host_access", "revoke_host_access"), 1):
        payload = {"id": index, "type": f"{DOMAIN}/{method}"}
        if method == "enable_host_access":
            payload.update(scope_revision="a" * 64, acknowledged=True)
        await connection.send_json(payload)
        response = await connection.receive_json()
        assert response["success"] is admin
        operation = getattr(client, "async_" + method)
        if admin:
            operation.assert_awaited_once()
        else:
            assert response["error"]["code"] == "unauthorized"
            operation.assert_not_called()
