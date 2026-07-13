"""Integration lifecycle tests for Codex Bridge."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.codex_bridge import async_setup_entry, async_unload_entry
from custom_components.codex_bridge.bridge_api import (
    BridgeApiAuthError,
    BridgeApiConnectionError,
    BridgeApiIncompatibleError,
)
from custom_components.codex_bridge.const import (
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    CONF_CONNECTION_TYPE,
    CONF_DISCOVERY_UUID,
    CONNECTION_TYPE_EXTERNAL_LEGACY,
    CONNECTION_TYPE_SUPERVISOR,
    DATA_ENTRIES,
    DATA_VIEWS_REGISTERED,
    DATA_WS_REGISTERED,
    DOMAIN,
)


TOKEN = "a" * 48


def _entry(hass: HomeAssistant) -> ConfigEntry:
    entry = MockConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Codex Bridge App",
        data={
            CONF_BRIDGE_URL: "http://127.0.0.1:8766",
            CONF_BRIDGE_TOKEN: TOKEN,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_SUPERVISOR,
            CONF_DISCOVERY_UUID: "0123456789abcdef0123456789abcdef",
        },
        source="hassio",
        unique_id="0123456789abcdef0123456789abcdef",
    )
    entry.add_to_hass(hass)
    return entry


async def test_setup_and_reload_keep_views_and_websocket_registration_process_lifetime(
    hass,
):
    entry = _entry(hass)
    client = Mock()
    client.async_ready = AsyncMock()
    client.async_close = AsyncMock()
    client.require_api_v1 = Mock()
    client.negotiated_api_version = 1
    client.async_ready.return_value = object()
    with (
        patch("custom_components.codex_bridge.BridgeApiClient", return_value=client),
        patch("custom_components.codex_bridge.async_register_http_views") as http_views,
        patch(
            "custom_components.codex_bridge.async_register_websocket_commands"
        ) as websocket,
        patch(
            "custom_components.codex_bridge.async_register_panel", new=AsyncMock()
        ) as panel,
        patch("custom_components.codex_bridge.async_remove_panel") as remove_panel,
    ):
        assert await async_setup_entry(hass, entry)
        assert await async_unload_entry(hass, entry)
        assert await async_setup_entry(hass, entry)

    assert http_views.call_count == 1
    assert websocket.call_count == 1
    assert panel.await_count == 2
    assert remove_panel.call_count == 1
    assert hass.data[DOMAIN][DATA_VIEWS_REGISTERED]
    assert hass.data[DOMAIN][DATA_WS_REGISTERED]
    assert entry.entry_id in hass.data[DOMAIN][DATA_ENTRIES]
    client.async_close.assert_awaited_once()
    runtime = hass.data[DOMAIN][DATA_ENTRIES][entry.entry_id]
    assert runtime.connection_type == CONNECTION_TYPE_SUPERVISOR
    assert runtime.discovery_uuid == entry.unique_id
    assert runtime.api_version == 1


async def test_external_entry_requires_the_explicit_legacy_capability(hass):
    entry = MockConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="External Codex Bridge",
        data={
            CONF_BRIDGE_URL: "http://127.0.0.1:8766",
            CONF_BRIDGE_TOKEN: TOKEN,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_EXTERNAL_LEGACY,
        },
        source="user",
        unique_id=f"{DOMAIN}:external",
    )
    entry.add_to_hass(hass)
    client = Mock()
    client.async_ready = AsyncMock(return_value=object())
    client.require_legacy_v0 = Mock()
    client.negotiated_api_version = 0
    with (
        patch("custom_components.codex_bridge.BridgeApiClient", return_value=client),
        patch("custom_components.codex_bridge.async_register_http_views"),
        patch("custom_components.codex_bridge.async_register_websocket_commands"),
        patch("custom_components.codex_bridge.async_register_panel", new=AsyncMock()),
    ):
        assert await async_setup_entry(hass, entry)

    client.require_legacy_v0.assert_called_once_with()
    runtime = hass.data[DOMAIN][DATA_ENTRIES][entry.entry_id]
    assert runtime.connection_type == CONNECTION_TYPE_EXTERNAL_LEGACY
    assert runtime.discovery_uuid is None
    assert runtime.api_version == 0


async def test_setup_refuses_a_second_active_connection(hass):
    first = _entry(hass)
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Second Codex Bridge",
        data={
            CONF_BRIDGE_URL: "http://127.0.0.2:8766",
            CONF_BRIDGE_TOKEN: "b" * 48,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_SUPERVISOR,
            CONF_DISCOVERY_UUID: "f" * 32,
        },
        source="hassio",
        unique_id="f" * 32,
    )
    second.add_to_hass(hass)
    client = Mock()
    client.async_ready = AsyncMock(return_value=object())
    client.require_api_v1 = Mock()
    client.negotiated_api_version = 1
    with (
        patch("custom_components.codex_bridge.BridgeApiClient", return_value=client),
        patch("custom_components.codex_bridge.async_register_http_views"),
        patch("custom_components.codex_bridge.async_register_websocket_commands"),
        patch("custom_components.codex_bridge.async_register_panel", new=AsyncMock()),
    ):
        assert await async_setup_entry(hass, first)
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, second)

    assert set(hass.data[DOMAIN][DATA_ENTRIES]) == {first.entry_id}


async def test_partial_setup_closes_runtime_and_preserves_permanent_registrations(hass):
    entry = _entry(hass)
    client = Mock()
    client.async_ready = AsyncMock(return_value=object())
    client.async_close = AsyncMock()
    client.require_api_v1 = Mock()
    client.negotiated_api_version = 1
    panel = AsyncMock(side_effect=[RuntimeError("panel failed"), None])
    with (
        patch("custom_components.codex_bridge.BridgeApiClient", return_value=client),
        patch("custom_components.codex_bridge.async_register_http_views") as http_views,
        patch(
            "custom_components.codex_bridge.async_register_websocket_commands"
        ) as websocket,
        patch("custom_components.codex_bridge.async_register_panel", panel),
    ):
        with pytest.raises(RuntimeError, match="panel failed"):
            await async_setup_entry(hass, entry)
        assert not hass.data[DOMAIN][DATA_ENTRIES]
        assert await async_setup_entry(hass, entry)

    assert http_views.call_count == 1
    assert websocket.call_count == 1
    assert panel.await_count == 2
    client.async_close.assert_awaited_once()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (BridgeApiAuthError(), ConfigEntryAuthFailed),
        (BridgeApiConnectionError(), ConfigEntryNotReady),
        (BridgeApiIncompatibleError(), ConfigEntryNotReady),
    ],
)
async def test_setup_maps_safe_connection_failures(hass, error, expected):
    entry = _entry(hass)
    client = Mock()
    client.async_ready = AsyncMock(side_effect=error)
    with patch("custom_components.codex_bridge.BridgeApiClient", return_value=client):
        with pytest.raises(expected):
            await async_setup_entry(hass, entry)
