"""Integration lifecycle tests for Codex Bridge."""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.codex_bridge import (
    PLATFORMS,
    async_setup_entry,
    async_unload_entry,
)
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
    DATA_VIEWS_REGISTERED,
    DATA_WS_REGISTERED,
    DOMAIN,
)
from custom_components.codex_bridge.runtime import async_get_runtime, normalize_web_search_mode

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def _setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Direct lifecycle tests must hold the lock normally owned by HA's entry manager.
    async with entry.setup_lock:
        return await async_setup_entry(hass, entry)


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
        assert await _setup_entry(hass, entry)
        assert await async_unload_entry(hass, entry)
        assert await _setup_entry(hass, entry)

    assert http_views.call_count == 1
    assert websocket.call_count == 1
    assert panel.await_count == 2
    assert remove_panel.call_count == 1
    assert hass.data[DOMAIN][DATA_VIEWS_REGISTERED]
    assert hass.data[DOMAIN][DATA_WS_REGISTERED]
    client.async_close.assert_awaited_once()
    runtime = entry.runtime_data
    assert runtime.connection_type == CONNECTION_TYPE_SUPERVISOR
    assert runtime.discovery_uuid == entry.unique_id
    assert runtime.api_version == 1
    assert runtime.task_event_forwarder is not None


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
        assert await _setup_entry(hass, entry)

    client.require_legacy_v0.assert_called_once_with()
    runtime = entry.runtime_data
    assert runtime.connection_type == CONNECTION_TYPE_EXTERNAL_LEGACY
    assert runtime.discovery_uuid is None
    assert runtime.api_version == 0


async def test_supervisor_runtime_defaults_live_web_search_when_advertised(hass):
    entry = _entry(hass)
    client = Mock()
    client.async_ready = AsyncMock(
        return_value=Mock(capabilities=("api_v1", "web_search_v1"))
    )
    client.async_close = AsyncMock()
    client.require_api_v1 = Mock()
    client.negotiated_api_version = 1
    with (
        patch("custom_components.codex_bridge.BridgeApiClient", return_value=client),
        patch("custom_components.codex_bridge.async_register_http_views"),
        patch("custom_components.codex_bridge.async_register_websocket_commands"),
        patch("custom_components.codex_bridge.async_register_panel", new=AsyncMock()),
        patch("custom_components.codex_bridge.async_remove_panel"),
    ):
        assert await _setup_entry(hass, entry)
        runtime = entry.runtime_data
        assert runtime.web_search_mode == "live"
        assert runtime.web_search_payload() == {"web_search": "live"}
        assert await async_unload_entry(hass, entry)


@pytest.mark.parametrize(
    ("value", "connection_type", "capabilities", "expected"),
    [
        (None, CONNECTION_TYPE_SUPERVISOR, ("web_search_v1",), "live"),
        ("disabled", CONNECTION_TYPE_SUPERVISOR, ("web_search_v1",), "disabled"),
        ("browser", CONNECTION_TYPE_SUPERVISOR, ("web_search_v1",), "disabled"),
        (None, CONNECTION_TYPE_SUPERVISOR, (), "live"),
        ("disabled", CONNECTION_TYPE_SUPERVISOR, (), "disabled"),
        (None, CONNECTION_TYPE_EXTERNAL_LEGACY, ("web_search_v1",), "disabled"),
    ],
)
def test_web_search_mode_is_normalized_to_supported_supervisor_values(
    value, connection_type, capabilities, expected
):
    assert (
        normalize_web_search_mode(
            value,
            connection_type=connection_type,
            capabilities=capabilities,
        )
        == expected
    )


async def test_setup_refuses_a_second_active_connection(hass):
    # The conversation dependency needs HA's exposed-entity registry.
    assert await async_setup_component(hass, "homeassistant", {})
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
    client = Mock()
    client.async_ready = AsyncMock(return_value=object())
    client.async_close = AsyncMock()
    client.require_api_v1 = Mock()
    client.negotiated_api_version = 1
    client.async_get_status = AsyncMock(return_value={})
    client.async_list_threads = AsyncMock(return_value=[])

    async def wait_for_events(*, after: int) -> None:
        await asyncio.Event().wait()

    client.async_replay_events = AsyncMock(side_effect=wait_for_events)
    with (
        patch("custom_components.codex_bridge.BridgeApiClient", return_value=client),
        patch("custom_components.codex_bridge.async_register_http_views"),
        patch("custom_components.codex_bridge.async_register_websocket_commands"),
        patch("custom_components.codex_bridge.async_register_panel", new=AsyncMock()),
        patch("custom_components.codex_bridge.async_remove_panel"),
        patch("homeassistant.components.frontend.async_setup", new=AsyncMock(return_value=True)),
    ):
        assert await hass.config_entries.async_setup(first.entry_id)
        second.add_to_hass(hass)
        assert not await hass.config_entries.async_setup(second.entry_id)

        assert first.state is ConfigEntryState.LOADED
        assert second.state is ConfigEntryState.SETUP_RETRY
        assert await hass.config_entries.async_unload(first.entry_id)


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
            await _setup_entry(hass, entry)
        client.async_close.assert_awaited_once()
        assert await _setup_entry(hass, entry)

    assert http_views.call_count == 1
    assert websocket.call_count == 1
    assert panel.await_count == 2
    client.async_close.assert_awaited_once()
    assert await async_unload_entry(hass, entry)


async def test_v1_event_consumer_is_config_entry_owned_and_cancelled_on_unload(hass):
    entry = _entry(hass)
    started = asyncio.Event()

    async def replay_events(*, after: int):
        assert after == 0
        started.set()
        await asyncio.Event().wait()

    client = Mock()
    client.async_ready = AsyncMock(return_value=object())
    client.async_close = AsyncMock()
    client.require_api_v1 = Mock()
    client.negotiated_api_version = 1
    client.async_replay_events = AsyncMock(side_effect=replay_events)
    client.async_wait_events = AsyncMock()
    with (
        patch("custom_components.codex_bridge.BridgeApiClient", return_value=client),
        patch("custom_components.codex_bridge.async_register_http_views"),
        patch("custom_components.codex_bridge.async_register_websocket_commands"),
        patch("custom_components.codex_bridge.async_register_panel", new=AsyncMock()),
        patch("custom_components.codex_bridge.async_remove_panel"),
    ):
        assert await _setup_entry(hass, entry)
        await asyncio.wait_for(started.wait(), 1)
        runtime = entry.runtime_data
        broker_task = runtime.event_broker._task
        assert broker_task is not None
        assert broker_task in entry._background_tasks

        assert await async_unload_entry(hass, entry)

    assert broker_task.cancelled()
    client.async_close.assert_awaited_once()


async def test_token_reload_closes_old_broker_before_starting_replacement(hass):
    entry = _entry(hass)
    first_client = Mock(
        async_ready=AsyncMock(return_value=object()),
        async_close=AsyncMock(),
        require_api_v1=Mock(),
        negotiated_api_version=1,
    )
    second_client = Mock(
        async_ready=AsyncMock(return_value=object()),
        async_close=AsyncMock(),
        require_api_v1=Mock(),
        negotiated_api_version=1,
    )
    first_broker = Mock(async_start=AsyncMock(), async_close=AsyncMock())
    second_broker = Mock(async_start=AsyncMock(), async_close=AsyncMock())
    with (
        patch(
            "custom_components.codex_bridge.BridgeApiClient",
            side_effect=[first_client, second_client],
        ) as client_class,
        patch(
            "custom_components.codex_bridge.EventBroker",
            side_effect=[first_broker, second_broker],
        ),
        patch("custom_components.codex_bridge.async_register_http_views"),
        patch("custom_components.codex_bridge.async_register_websocket_commands"),
        patch("custom_components.codex_bridge.async_register_panel", new=AsyncMock()),
        patch("custom_components.codex_bridge.async_remove_panel"),
    ):
        assert await _setup_entry(hass, entry)
        assert await async_unload_entry(hass, entry)
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_BRIDGE_TOKEN: "b" * 48}
        )
        assert await _setup_entry(hass, entry)

    assert client_class.call_args_list[0].args[2] == TOKEN
    assert client_class.call_args_list[1].args[2] == "b" * 48
    first_broker.async_start.assert_awaited_once()
    first_broker.async_close.assert_awaited_once()
    second_broker.async_start.assert_awaited_once()


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
            await _setup_entry(hass, entry)

@pytest.fixture
async def core_components(hass: HomeAssistant) -> None:
    """Initialise the exposed-entity registry the conversation dependency needs."""

    assert await async_setup_component(hass, "homeassistant", {})


@contextmanager
def _setup_boundary(client: Mock) -> Iterator[None]:
    """Replace the Bridge client and process-wide panel registration only."""

    with (
        patch("custom_components.codex_bridge.BridgeApiClient", return_value=client),
        patch("custom_components.codex_bridge.async_register_http_views"),
        patch("custom_components.codex_bridge.async_register_websocket_commands"),
        patch("custom_components.codex_bridge.async_register_panel", new=AsyncMock()),
        patch("custom_components.codex_bridge.async_remove_panel"),
        patch(
            "homeassistant.components.frontend.async_setup",
            new=AsyncMock(return_value=True),
        ),
    ):
        yield


def _app_client(*capabilities: str) -> Mock:
    async def wait_for_events(*, after: int) -> None:
        await asyncio.Event().wait()

    client = Mock()
    client.async_ready = AsyncMock(
        return_value=Mock(capabilities=("api_v1", *capabilities))
    )
    client.require_api_v1 = Mock()
    client.negotiated_api_version = 1
    client.async_get_status = AsyncMock(side_effect=BridgeApiConnectionError())
    client.async_list_threads = AsyncMock(return_value=[])
    client.async_replay_events = AsyncMock(side_effect=wait_for_events)
    client.async_wait_events = AsyncMock(side_effect=wait_for_events)
    client.async_scheduler_automations = AsyncMock(return_value={"automations": []})
    client.async_close = AsyncMock()
    return client


@pytest.mark.usefixtures("core_components")
async def test_rejected_token_during_setup_starts_reauthentication(hass):
    entry = _entry(hass)
    client = _app_client()
    client.async_ready.side_effect = BridgeApiAuthError()

    with _setup_boundary(client):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    [flow] = entry.async_get_active_flows(hass, {SOURCE_REAUTH})
    assert flow["step_id"] == "reauth_confirm"


@pytest.mark.usefixtures("core_components")
async def test_bridge_error_after_readiness_retries_setup_until_it_loads(
    hass, caplog
):
    entry = _entry(hass)
    client = _app_client("automations_v1")
    client.async_scheduler_automations.side_effect = [
        BridgeApiConnectionError(),
        {"automations": []},
    ]

    with _setup_boundary(client):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.SETUP_RETRY
        assert entry.error_reason_translation_key == "not_ready"
        assert entry.reason == "Codex Bridge is not ready yet"
        # Domain-wide views and actions cannot reach the closed runtime.
        with pytest.raises(RuntimeError):
            async_get_runtime(hass)
        client.async_close.assert_awaited_once()
        # Platforms were never forwarded, so cleanup must not try to unload them.
        assert "Ignored unload request" not in caplog.text

        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=5))
        # HA retries in a background task, beside the endless event long-poll.
        async with asyncio.timeout(5):
            while entry.state is not ConfigEntryState.LOADED:
                await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        assert client.async_scheduler_automations.await_count == 2
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.usefixtures("core_components")
async def test_token_rejected_after_readiness_starts_reauthentication(hass):
    entry = _entry(hass)
    client = _app_client("automations_v1")
    client.async_scheduler_automations.side_effect = BridgeApiAuthError()

    with _setup_boundary(client):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.error_reason_translation_key == "authentication_failed"
    assert entry.reason == (
        "Codex Bridge rejected the saved Home Assistant connection token"
    )
    assert list(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))
    with pytest.raises(RuntimeError):
        async_get_runtime(hass)
    client.async_close.assert_awaited_once()


@pytest.mark.usefixtures("core_components")
async def test_live_update_token_rejection_starts_reauthentication(hass):
    entry = _entry(hass)
    client = _app_client()
    client.async_replay_events.side_effect = BridgeApiAuthError()

    with _setup_boundary(client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        [flow] = entry.async_get_active_flows(hass, {SOURCE_REAUTH})
        assert flow["step_id"] == "reauth_confirm"
        runtime = entry.runtime_data
        assert runtime.event_broker.connection_status["state"] == "authentication_failed"
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.usefixtures("core_components")
async def test_app_reauth_restarts_live_updates_stopped_by_a_rejected_token(hass):
    entry = _entry(hass)
    client = _app_client()
    replays = 0

    async def replay(*, after: int) -> None:
        nonlocal replays
        replays += 1
        if replays == 1:
            raise BridgeApiAuthError()
        await asyncio.Event().wait()

    client.async_replay_events.side_effect = replay
    flow_client = AsyncMock()
    flow_client.negotiated_api_version = 1
    flow_client.async_ready.return_value = object()
    with (
        _setup_boundary(client),
        patch(
            "custom_components.codex_bridge.config_flow.BridgeApiClient",
            return_value=flow_client,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        stopped = entry.runtime_data.event_broker
        assert stopped.connection_status["state"] == "authentication_failed"
        [flow] = entry.async_get_active_flows(hass, {SOURCE_REAUTH})

        # The App accepts the saved token again, e.g. after a restart.
        result = await hass.config_entries.flow.async_configure(flow["flow_id"], {})
        await hass.async_block_till_done()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert entry.state is ConfigEntryState.LOADED
        restarted = entry.runtime_data.event_broker
        assert restarted is not stopped
        assert restarted.connection_status["state"] == "connecting"
        assert replays == 2
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.usefixtures("core_components")
async def test_rejection_racing_a_reload_leaves_no_reauthentication_request(hass):
    entry = _entry(hass)
    client = _app_client()
    release = asyncio.Event()
    replays = 0

    async def replay(*, after: int) -> None:
        nonlocal replays
        replays += 1
        if replays == 1:
            await release.wait()
            raise BridgeApiAuthError()
        await asyncio.Event().wait()

    client.async_replay_events.side_effect = replay
    unload_platforms = hass.config_entries.async_unload_platforms

    async def unload_while_rejected(*args, **kwargs):
        # The old token is rejected while the reload is unloading the entry.
        release.set()
        for _ in range(5):
            await asyncio.sleep(0)
        return await unload_platforms(*args, **kwargs)

    with (
        _setup_boundary(client),
        patch.object(
            hass.config_entries, "async_unload_platforms", new=unload_while_rejected
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        assert replays == 2
        assert not list(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.usefixtures("core_components")
async def test_setup_cancelled_while_forwarding_unloads_the_platforms(hass):
    entry = _entry(hass)
    client = _app_client()
    unload_platforms = AsyncMock(return_value=True)
    with (
        _setup_boundary(client),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ),
        patch.object(
            hass.config_entries, "async_unload_platforms", new=unload_platforms
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await _setup_entry(hass, entry)

    unload_platforms.assert_awaited_once_with(entry, PLATFORMS)
    with pytest.raises(RuntimeError):
        async_get_runtime(hass)
    client.async_close.assert_awaited_once()
