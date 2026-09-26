"""Entry removal revokes local authority before any best-effort App discovery."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant

import custom_components.codex_bridge as integration
from custom_components.codex_bridge.bridge_api import BridgeApiError
from custom_components.codex_bridge.const import (
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    CONF_CONNECTION_TYPE,
    CONNECTION_TYPE_EXTERNAL_LEGACY,
    CONNECTION_TYPE_SUPERVISOR,
    DATA_ENTRIES,
    DATA_PANEL_REGISTERED,
    DOMAIN,
)
from custom_components.codex_bridge.ha_mcp_shortcut import FIXED_URL, HaMcpShortcut, _fingerprint
from custom_components.codex_bridge.runtime import CodexBridgeRuntime

from test_ha_mcp_shortcut import _connect, shortcut as grant_fixture


shortcut = grant_fixture


async def _prepare_removal(case, monkeypatch):
    name = await _connect(case)
    await case.helper.async_close()
    case.auth.async_remove_refresh_token.reset_mock()
    case.auth.async_create_refresh_token.reset_mock()
    case.auth.async_create_access_token.reset_mock()
    case.client.async_close = AsyncMock()
    factory = Mock(return_value=case.client)
    monkeypatch.setattr(integration, "BridgeApiClient", factory)
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: object())
    entry = SimpleNamespace(entry_id="entry-owned", data={
        CONF_CONNECTION_TYPE: CONNECTION_TYPE_SUPERVISOR,
        CONF_BRIDGE_URL: "http://127.0.0.1:8766",
        CONF_BRIDGE_TOKEN: "synthetic-bridge-token",
    })
    return name, entry, factory


async def test_entry_removal_revokes_before_discovery_then_cleans_exact_binding(shortcut, monkeypatch):
    case = shortcut
    name, entry, factory = await _prepare_removal(case, monkeypatch)
    original_remove = case.auth.async_remove_refresh_token.side_effect

    def revoke(token):
        factory.assert_not_called()
        original_remove(token)

    case.auth.async_remove_refresh_token.side_effect = revoke

    async def ready():
        assert case.tokens == {}
        assert case.store.saved["record"]["state"] == "disconnecting"
        return SimpleNamespace(capabilities=("mcp_credential_binding_v1",))

    case.client.async_ready = AsyncMock(side_effect=ready)
    await integration.async_remove_entry(case.hass, entry)
    assert case.tokens == {}
    assert case.store.saved == {"record": None}
    case.auth.async_remove_refresh_token.assert_called_once()
    case.client.async_remove_managed_mcp.assert_awaited_once_with(name, {
        "expected_url": FIXED_URL,
        "expected_token_sha256": _fingerprint("synthetic.jwt.initial"),
    })
    case.client.async_close.assert_awaited_once()
    case.auth.async_create_refresh_token.assert_not_called()
    case.auth.async_create_access_token.assert_not_called()


async def test_cancellation_during_app_discovery_leaves_authority_revoked(shortcut, monkeypatch):
    case = shortcut
    _, entry, _ = await _prepare_removal(case, monkeypatch)
    discovery_started = asyncio.Event()

    async def ready():
        assert case.tokens == {}
        discovery_started.set()
        await asyncio.Event().wait()

    case.client.async_ready = AsyncMock(side_effect=ready)
    task = asyncio.create_task(integration.async_remove_entry(case.hass, entry))
    try:
        await asyncio.wait_for(discovery_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert case.tokens == {}
    assert case.store.saved["record"]["state"] == "disconnecting"
    case.auth.async_remove_refresh_token.assert_called_once()
    case.client.async_remove_managed_mcp.assert_not_called()
    case.auth.async_create_refresh_token.assert_not_called()
    case.auth.async_create_access_token.assert_not_called()


async def test_unreachable_app_keeps_revoked_grant_cleanup_journal(shortcut, monkeypatch):
    case = shortcut
    _, entry, _ = await _prepare_removal(case, monkeypatch)
    case.client.async_ready = AsyncMock(side_effect=BridgeApiError(code="bridge_unavailable"))
    await integration.async_remove_entry(case.hass, entry)
    assert case.tokens == {}
    assert case.store.saved["record"]["state"] == "disconnecting"
    case.auth.async_remove_refresh_token.assert_called_once()
    case.client.async_remove_managed_mcp.assert_not_called()
    case.client.async_close.assert_awaited_once()
    case.auth.async_create_refresh_token.assert_not_called()


async def test_missing_app_configuration_cannot_prevent_local_revocation(shortcut, monkeypatch):
    case = shortcut
    _, entry, factory = await _prepare_removal(case, monkeypatch)
    del entry.data[CONF_BRIDGE_URL]
    await integration.async_remove_entry(case.hass, entry)
    assert case.tokens == {}
    assert case.store.saved["record"]["state"] == "disconnecting"
    factory.assert_not_called()
    case.auth.async_remove_refresh_token.assert_called_once()


async def test_external_entry_removal_does_not_touch_supervisor_owned_grant(shortcut, monkeypatch):
    case = shortcut
    _, entry, factory = await _prepare_removal(case, monkeypatch)
    entry.data[CONF_CONNECTION_TYPE] = CONNECTION_TYPE_EXTERNAL_LEGACY
    await integration.async_remove_entry(case.hass, entry)
    assert case.tokens
    assert case.store.saved["record"]["state"] == "connected"
    factory.assert_not_called()
    case.auth.async_remove_refresh_token.assert_not_called()


@pytest.mark.parametrize("boundary", ["shutdown", "unload"])
async def test_real_home_assistant_lifecycle_cancels_shortcut_renewal_timer(tmp_path, boundary):
    hass = HomeAssistant(str(tmp_path))
    client = SimpleNamespace(async_close=AsyncMock())
    helper = HaMcpShortcut(
        hass, "timer-entry", client, connection_type=CONNECTION_TYPE_SUPERVISOR,
        supports_capability=lambda _value: False, selection_callback=AsyncMock(),
    )
    runtime = CodexBridgeRuntime(
        entry_id="timer-entry", title="Test Bridge", client=client,
        connection_type=CONNECTION_TYPE_SUPERVISOR, discovery_uuid=None,
        api_version=1, ha_mcp_shortcut=helper,
    )
    hass.data[DOMAIN] = {
        DATA_ENTRIES: {runtime.entry_id: runtime}, DATA_PANEL_REGISTERED: False,
    }
    try:
        await helper.async_setup()
        # Capture the actual HA interval handle; no scheduler/event seam is mocked.
        handle = helper._remove_timer.__self__._timer_handle
        assert not handle.cancelled()
        if boundary == "shutdown":
            await hass.async_stop(force=True)
        else:
            assert await integration.async_unload_entry(
                hass, SimpleNamespace(entry_id=runtime.entry_id),
            )
            assert runtime.entry_id not in hass.data[DOMAIN][DATA_ENTRIES]
            client.async_close.assert_awaited_once()
        assert handle.cancelled()
    finally:
        await helper.async_close()
        await hass.async_stop(force=True)
