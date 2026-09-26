"""HA entity projections and registry lifecycle."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.setup import async_setup_component
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.codex_bridge import async_setup_entry, async_unload_entry
from custom_components.codex_bridge.const import (
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    CONF_CONNECTION_TYPE,
    CONNECTION_TYPE_SUPERVISOR,
    DOMAIN,
)
from custom_components.codex_bridge.entity_coordinator import BridgeEntityCoordinator, project_status
from custom_components.codex_bridge.event_broker import EventBroker
from custom_components.codex_bridge.runtime import async_get_runtime

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def _setup_entry(hass, entry):
    async with entry.setup_lock:
        return await async_setup_entry(hass, entry)


NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)


def _status(*, at: datetime = NOW, auth_at: datetime | None = None, limits_at: datetime | None = None):
    return {
        "auth": {
            "state": "ok", "auth_mode": "chatgpt", "auth_required": False,
            "updated_at": (auth_at or at - timedelta(minutes=10)).isoformat(),
        },
        "account": {"account_id": "private-account", "email": "private@example.invalid"},
        "limits": {
            "available": True,
            "five_hour_enabled": True,
            "updated_at": (limits_at or at - timedelta(minutes=1)).isoformat(),
            "primary": {"used_percent": 27.456, "window_minutes": 300, "resets_at": 1780000000},
            "secondary": {"used_percent": 80, "window_minutes": 10080, "resets_at": 1780500000},
            "reset_credits": {"secret": "never an entity attribute"},
        },
    }


def test_projection_only_keeps_safe_fresh_values():
    projected = project_status(
        _status(), [{"status": "running", "title": "private prompt"}],
        now=NOW, last_outcome="completed",
    )
    assert projected.task_running is True
    assert projected.authenticated is True
    assert projected.five_hour_used == 27.5
    assert projected.weekly_used == 80
    assert projected.five_hour_enabled is True
    assert projected.five_hour_reset == datetime.fromtimestamp(1780000000, UTC)
    assert "private" not in repr(projected)


def test_auth_change_and_old_or_missing_usage_clear_values():
    switched = project_status(
        _status(auth_at=NOW, limits_at=NOW - timedelta(minutes=1)),
        [], now=NOW, last_outcome=None,
    )
    assert switched.usage_available is False
    assert switched.five_hour_used is None
    assert switched.five_hour_enabled is None
    stale = project_status(
        _status(limits_at=NOW - timedelta(minutes=20)),
        [], now=NOW, last_outcome=None,
    )
    assert stale.weekly_reset is None
    assert stale.five_hour_enabled is None
    missing = _status()
    missing["limits"]["primary"] = {"used_percent": 0, "window_minutes": None}
    missing["limits"]["secondary"] = {"used_percent": float("nan"), "window_minutes": 10080}
    projected = project_status(missing, [], now=NOW, last_outcome=None)
    assert projected.five_hour_used is None
    assert projected.weekly_used is None
    assert projected.five_hour_enabled is None


def _weekly_status(*, at: datetime = NOW):
    status = _status(at=at)
    status["limits"]["primary"] = None
    status["limits"]["five_hour_enabled"] = False
    return status


@pytest.mark.parametrize("case", [
    "old_bridge", "string_flag", "numeric_flag", "contradictory_flag",
    "malformed_primary", "malformed_weekly", "stale", "account_change",
])
def test_disabled_allowance_requires_a_fresh_consistent_explicit_flag(case):
    status = _weekly_status()
    if case == "old_bridge":
        status["limits"].pop("five_hour_enabled")
    elif case == "string_flag":
        status["limits"]["five_hour_enabled"] = "false"
    elif case == "numeric_flag":
        status["limits"]["five_hour_enabled"] = 0
    elif case == "contradictory_flag":
        status["limits"]["five_hour_enabled"] = True
    elif case == "malformed_primary":
        status["limits"]["primary"] = {}
    elif case == "malformed_weekly":
        status["limits"]["secondary"]["used_percent"] = float("nan")
    elif case == "stale":
        status["limits"]["updated_at"] = (NOW - timedelta(minutes=20)).isoformat()
    elif case == "account_change":
        status["account"]["updated_at"] = NOW.isoformat()
    projected = project_status(status, [], now=NOW, last_outcome=None)
    assert projected.five_hour_enabled is None


def test_weekly_only_account_has_an_explicit_disabled_five_hour_allowance():
    projected = project_status(_weekly_status(), [], now=NOW, last_outcome=None)
    assert projected.five_hour_enabled is False
    assert projected.five_hour_used is None
    assert projected.weekly_used == 80


async def test_event_during_refresh_triggers_follow_up(hass):
    entered = asyncio.Event()
    release = asyncio.Event()
    client = Mock()
    client.async_get_status = AsyncMock(side_effect=lambda: _status(at=datetime.now(UTC)))

    async def list_threads():
        if client.async_list_threads.await_count == 1:
            entered.set()
            await release.wait()
            return [{"status": "running"}]
        return []

    client.async_list_threads = AsyncMock(side_effect=list_threads)
    broker = EventBroker(AsyncMock(), initial_cursor=0)
    coordinator = BridgeEntityCoordinator(hass, Mock(client=client, event_broker=broker))

    async def emit(cursor, event_type):
        await broker._consume({
            "events": [{
                "cursor": cursor, "event_id": f"evt_{cursor}", "scope": "thread",
                "thread_id": "thr_1", "event_type": event_type,
                "payload": {}, "timestamp": NOW.isoformat(),
            }],
            "next_cursor": cursor, "minimum_cursor": 0,
            "has_more": False, "heartbeat": False,
        })

    try:
        await emit(1, "run.started")
        await asyncio.wait_for(entered.wait(), 2)
        await emit(2, "run.completed")
        release.set()
        await asyncio.wait_for(coordinator._refresh_task, 2)
        assert client.async_list_threads.await_count == 2
        assert coordinator.data.task_running is False
        assert coordinator.data.last_outcome == "completed"
    finally:
        release.set()
        await coordinator.async_close()
        await broker.async_close()


async def test_auth_change_clears_usage_while_the_new_snapshot_is_pending(hass):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def status_after_switch():
        entered.set()
        await release.wait()
        return _weekly_status(at=datetime.now(UTC))

    client = Mock()
    client.async_get_status = AsyncMock(side_effect=status_after_switch)
    client.async_list_threads = AsyncMock(return_value=[])
    broker = EventBroker(AsyncMock(), initial_cursor=0)
    coordinator = BridgeEntityCoordinator(hass, Mock(client=client, event_broker=broker))
    coordinator.async_set_updated_data(project_status(
        _status(at=datetime.now(UTC)), [], now=datetime.now(UTC), last_outcome="completed",
    ))
    assert coordinator.data.usage_available is True

    try:
        await broker._consume({
            "events": [{
                "cursor": 1, "event_id": "evt_auth", "scope": "auth",
                "event_type": "auth.status_changed", "payload": {}, "timestamp": NOW.isoformat(),
            }],
            "next_cursor": 1, "minimum_cursor": 0,
            "has_more": False, "heartbeat": False,
        })
        await asyncio.wait_for(entered.wait(), 2)
        assert coordinator.data.usage_available is False
        assert coordinator.data.last_outcome is None
        assert coordinator.data.five_hour_enabled is None
        assert coordinator.data.five_hour_used is None
        assert coordinator.data.five_hour_reset is None
        assert coordinator.data.weekly_used is None
        assert coordinator.data.weekly_reset is None

        release.set()
        await asyncio.wait_for(coordinator._refresh_task, 2)
        assert coordinator.data.usage_available is True
        assert coordinator.data.five_hour_enabled is False
        assert coordinator.data.weekly_used == 80
    finally:
        release.set()
        await coordinator.async_close()
        await broker.async_close()


async def test_registry_ids_and_availability_survive_reload(hass):
    # The conversation dependency expects HA's exposed-entity registry, which
    # production Core initialises before custom integrations are set up.
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(
        domain=DOMAIN, title="Codex Bridge App", source="hassio",
        data={
            CONF_BRIDGE_URL: "http://127.0.0.1:8766",
            CONF_BRIDGE_TOKEN: "a" * 48,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_SUPERVISOR,
        },
        unique_id="bridge-test-instance",
    )
    entry.add_to_hass(hass)
    client = Mock()
    client.async_ready = AsyncMock(return_value=Mock(capabilities=("api_v1",)))
    client.require_api_v1 = Mock()
    client.negotiated_api_version = 1
    client.async_get_status = AsyncMock(return_value=_status(at=datetime.now(UTC)))
    client.async_list_threads = AsyncMock(return_value=[])
    client.async_replay_events = AsyncMock(return_value={
        "events": [], "next_cursor": 0, "minimum_cursor": 0,
        "has_more": False, "heartbeat": True,
    })
    async def wait_events(*, after):
        await asyncio.Event().wait()

    client.async_wait_events = AsyncMock(side_effect=wait_events)
    client.async_close = AsyncMock()

    with (
        patch("custom_components.codex_bridge.BridgeApiClient", return_value=client),
        patch("custom_components.codex_bridge.async_register_http_views"),
        patch("custom_components.codex_bridge.async_register_websocket_commands"),
        patch("custom_components.codex_bridge.async_register_panel", new=AsyncMock()),
        patch("custom_components.codex_bridge.async_remove_panel"),
        patch("homeassistant.components.frontend.async_setup", new=AsyncMock(return_value=True)),
    ):
        assert await _setup_entry(hass, entry)
        registry = er.async_get(hass)
        first = {
            (item.domain, item.unique_id): item.entity_id
            for item in registry.entities.values()
            if item.config_entry_id == entry.entry_id
        }
        assert len(first) == 8
        assert hass.states.get(first[("binary_sensor", f"{entry.entry_id}_connection")]).state == "on"
        assert hass.states.get(first[("sensor", f"{entry.entry_id}_five_hour_used")]).state == "27.5"

        client.async_list_threads.return_value = [{"status": "running", "title": "private prompt"}]
        broker = async_get_runtime(hass).event_broker
        await broker._consume({
            "events": [{
                "cursor": 1, "event_id": "evt_1", "scope": "thread",
                "thread_id": "thr_1", "event_type": "run.started",
                "payload": {}, "timestamp": NOW.isoformat(),
            }],
            "next_cursor": 1, "minimum_cursor": 0, "has_more": False, "heartbeat": False,
        })
        await hass.async_block_till_done()
        assert hass.states.get(first[("binary_sensor", f"{entry.entry_id}_task_running")]).state == "on"
        client.async_list_threads.return_value = [{"status": "idle"}]
        await broker._consume({
            "events": [{
                "cursor": 2, "event_id": "evt_2", "scope": "thread",
                "thread_id": "thr_1", "event_type": "run.completed",
                "payload": {}, "timestamp": NOW.isoformat(),
            }],
            "next_cursor": 2, "minimum_cursor": 0, "has_more": False, "heartbeat": False,
        })
        await hass.async_block_till_done()
        assert hass.states.get(first[("binary_sensor", f"{entry.entry_id}_task_running")]).state == "off"
        assert hass.states.get(first[("sensor", f"{entry.entry_id}_last_outcome")]).state == "completed"

        await broker._consume({
            "events": [{
                "cursor": 3, "event_id": "evt_3", "scope": "auth",
                "event_type": "auth.status_changed", "payload": {},
                "timestamp": NOW.isoformat(),
            }],
            "next_cursor": 3, "minimum_cursor": 0, "has_more": False, "heartbeat": False,
        })
        await hass.async_block_till_done()
        assert hass.states.get(first[("sensor", f"{entry.entry_id}_last_outcome")]).state == "unavailable"

        # A lost App makes the connection off and the remaining entities unavailable.
        from custom_components.codex_bridge.bridge_api import BridgeApiConnectionError
        client.async_get_status.side_effect = BridgeApiConnectionError()
        await async_get_runtime(hass).entity_coordinator.async_refresh()
        assert hass.states.get(first[("binary_sensor", f"{entry.entry_id}_connection")]).state == "off"
        assert hass.states.get(first[("binary_sensor", f"{entry.entry_id}_task_running")]).state == "unavailable"

        assert await async_unload_entry(hass, entry)
        client.async_get_status.side_effect = None
        assert await _setup_entry(hass, entry)
        second = {
            (item.domain, item.unique_id): item.entity_id
            for item in registry.entities.values()
            if item.config_entry_id == entry.entry_id
        }
        assert second == first
        assert await async_unload_entry(hass, entry)


@asynccontextmanager
async def _loaded_usage_entities(hass, *, weekly_only=False, customise=None):
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(
        domain=DOMAIN, title="Codex Bridge App", source="hassio",
        data={
            CONF_BRIDGE_URL: "http://127.0.0.1:8766",
            CONF_BRIDGE_TOKEN: "a" * 48,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_SUPERVISOR,
        },
        unique_id="bridge-usage-visibility-test",
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    if customise is not None:
        customise(registry, entry)
    client = Mock()
    client.async_ready = AsyncMock(return_value=Mock(capabilities=("api_v1",)))
    client.require_api_v1 = Mock()
    client.negotiated_api_version = 1
    status = _weekly_status if weekly_only else _status
    client.async_get_status = AsyncMock(return_value=status(at=datetime.now(UTC)))
    client.async_list_threads = AsyncMock(return_value=[])
    client.async_replay_events = AsyncMock(return_value={
        "events": [], "next_cursor": 0, "minimum_cursor": 0,
        "has_more": False, "heartbeat": True,
    })

    async def wait_events(*, after):
        await asyncio.Event().wait()

    client.async_wait_events = AsyncMock(side_effect=wait_events)
    client.async_close = AsyncMock()
    with (
        patch("custom_components.codex_bridge.BridgeApiClient", return_value=client),
        patch("custom_components.codex_bridge.async_register_http_views"),
        patch("custom_components.codex_bridge.async_register_websocket_commands"),
        patch("custom_components.codex_bridge.async_register_panel", new=AsyncMock()),
        patch("custom_components.codex_bridge.async_remove_panel"),
        patch("homeassistant.components.frontend.async_setup", new=AsyncMock(return_value=True)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        ids = {
            key: registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}")
            for key in ("five_hour_used", "five_hour_reset", "weekly_used", "weekly_reset")
        }
        try:
            yield entry, client, registry, ids
        finally:
            assert await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


@pytest.mark.parametrize("weekly_only", [False, True])
async def test_five_hour_visibility_tracks_allowance_without_replacing_entities(hass, weekly_only):
    async with _loaded_usage_entities(hass, weekly_only=weekly_only) as (entry, client, registry, ids):
        unrelated = registry.async_get_or_create("sensor", DOMAIN, "unrelated-allowance")
        client.async_get_status.return_value = _weekly_status(at=datetime.now(UTC))
        coordinator = async_get_runtime(hass).entity_coordinator
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        for key in ("five_hour_used", "five_hour_reset"):
            item = registry.async_get(ids[key])
            assert item.hidden_by is er.RegistryEntryHider.INTEGRATION
            assert item.disabled_by is None
            assert item.unique_id == f"{entry.entry_id}_{key}"
            assert hass.states.get(ids[key]).state == "unavailable"
        assert registry.async_get(ids["weekly_used"]).hidden_by is None
        assert float(hass.states.get(ids["weekly_used"]).state) == 80
        assert registry.async_get(unrelated.entity_id).hidden_by is None

        # A transient or older Bridge response does not undo the confirmed policy.
        unknown = _weekly_status(at=datetime.now(UTC))
        unknown["limits"].pop("five_hour_enabled")
        client.async_get_status.return_value = unknown
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert registry.async_get(ids["five_hour_used"]).hidden_by is er.RegistryEntryHider.INTEGRATION

        # A limited-account switch restores the same entries.
        client.async_get_status.return_value = _status(at=datetime.now(UTC))
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        for key in ("five_hour_used", "five_hour_reset"):
            assert registry.async_get(ids[key]).hidden_by is None
            assert registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}") == ids[key]
        assert hass.states.get(ids["five_hour_used"]).state == "27.5"


async def test_allowance_visibility_preserves_user_hidden_and_disabled_choices(hass):
    def customise(registry, entry):
        usage = registry.async_get_or_create(
            "sensor", DOMAIN, f"{entry.entry_id}_five_hour_used", config_entry=entry,
            hidden_by=er.RegistryEntryHider.USER,
        )
        reset = registry.async_get_or_create(
            "sensor", DOMAIN, f"{entry.entry_id}_five_hour_reset", config_entry=entry,
            disabled_by=er.RegistryEntryDisabler.USER,
        )
        registry.async_update_entity(usage.entity_id, name="My five-hour diagnostic")
        registry.async_update_entity_options(reset.entity_id, "sensor", {"precision": 1})

    async with _loaded_usage_entities(hass, customise=customise) as (_, client, registry, ids):
        for status in (_weekly_status, _status):
            client.async_get_status.return_value = status(at=datetime.now(UTC))
            await async_get_runtime(hass).entity_coordinator.async_refresh()
            await hass.async_block_till_done()
            assert registry.async_get(ids["five_hour_used"]).hidden_by is er.RegistryEntryHider.USER
            assert registry.async_get(ids["five_hour_used"]).name == "My five-hour diagnostic"
            assert registry.async_get(ids["five_hour_reset"]).disabled_by is er.RegistryEntryDisabler.USER
            assert registry.async_get(ids["five_hour_reset"]).options["sensor"] == {"precision": 1}


async def test_manual_unhide_persists_across_updates_reload_and_listener_cleanup(hass):
    async with _loaded_usage_entities(hass, weekly_only=True) as (entry, client, registry, ids):
        loaded_listener_count = hass.bus.async_listeners().get(er.EVENT_ENTITY_REGISTRY_UPDATED, 0)
        registry.async_update_entity_options(ids["five_hour_used"], DOMAIN, {"retained_option": True})
        registry.async_update_entity(ids["five_hour_used"], hidden_by=None)
        await hass.async_block_till_done()
        assert registry.async_get(ids["five_hour_used"]).options[DOMAIN] == {
            "retained_option": True, "five_hour_visibility": "manual",
        }
        await async_get_runtime(hass).entity_coordinator.async_refresh()
        await hass.async_block_till_done()
        assert registry.async_get(ids["five_hour_used"]).hidden_by is None

        old_coordinator = async_get_runtime(hass).entity_coordinator
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        unloaded_listener_count = hass.bus.async_listeners().get(er.EVENT_ENTITY_REGISTRY_UPDATED, 0)
        assert unloaded_listener_count < loaded_listener_count
        # An unhide while unloaded must also survive the next setup.
        registry.async_update_entity(ids["five_hour_reset"], hidden_by=None)
        await hass.async_block_till_done()
        old_coordinator.async_set_updated_data(project_status(
            _weekly_status(at=datetime.now(UTC)), [], now=datetime.now(UTC), last_outcome=None,
        ))
        await hass.async_block_till_done()
        assert registry.async_get(ids["five_hour_reset"]).hidden_by is None

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.bus.async_listeners().get(er.EVENT_ENTITY_REGISTRY_UPDATED, 0) == loaded_listener_count
        for key in ("five_hour_used", "five_hour_reset"):
            assert registry.async_get(ids[key]).hidden_by is None
            assert registry.async_get(ids[key]).options[DOMAIN]["five_hour_visibility"] == "manual"
            assert registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}") == ids[key]
        client.async_get_status.return_value = _status(at=datetime.now(UTC))
        await async_get_runtime(hass).entity_coordinator.async_refresh()
        await hass.async_block_till_done()
        assert registry.async_get(ids["five_hour_used"]).options[DOMAIN]["five_hour_visibility"] == "manual"
