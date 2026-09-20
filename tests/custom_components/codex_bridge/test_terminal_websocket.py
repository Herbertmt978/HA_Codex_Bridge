"""Terminal commands are authorised by HA and never expose runtime options."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.setup import async_setup_component

from custom_components.codex_bridge.const import DATA_ENTRIES, DOMAIN
from custom_components.codex_bridge.websocket_api import async_register_websocket_commands


@pytest.mark.parametrize("admin", [False, True])
async def test_terminal_commands_are_admin_only(hass, hass_ws_client, hass_access_token, hass_read_only_access_token, admin):
    assert await async_setup_component(hass, "websocket_api", {})
    client = AsyncMock()
    client.async_terminal.return_value = {"state": "running"}
    hass.data.setdefault(DOMAIN, {})[DATA_ENTRIES] = {"entry": SimpleNamespace(client=client)}
    async_register_websocket_commands(hass)
    connection = await hass_ws_client(hass, hass_access_token if admin else hass_read_only_access_token)
    for index, operation in enumerate(("open", "read", "write", "resize", "close"), 1):
        await connection.send_json({"id": index, "type": f"{DOMAIN}/terminal", "operation": operation, "thread_id": "chat", "session_id": "a" * 32})
        response = await connection.receive_json()
        assert response["success"] is admin
    if admin:
        assert client.async_terminal.await_count == 5
    else:
        client.async_terminal.assert_not_called()


async def test_terminal_cannot_override_command_or_sandbox(hass, hass_ws_client, hass_access_token):
    assert await async_setup_component(hass, "websocket_api", {})
    client = AsyncMock()
    hass.data.setdefault(DOMAIN, {})[DATA_ENTRIES] = {"entry": SimpleNamespace(client=client)}
    async_register_websocket_commands(hass)
    connection = await hass_ws_client(hass, hass_access_token)
    await connection.send_json({"id": 1, "type": f"{DOMAIN}/terminal", "operation": "open", "thread_id": "chat", "command": "sh", "cwd": "/", "sandboxPolicy": {"type": "dangerFullAccess"}})
    assert (await connection.receive_json())["success"] is False
    client.async_terminal.assert_not_called()
