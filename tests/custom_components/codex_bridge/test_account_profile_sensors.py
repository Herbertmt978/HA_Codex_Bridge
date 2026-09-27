"""Saved-account allowance sensors use stable HA registry identities."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.setup import async_setup_component
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.codex_bridge.account_allowance_coordinator import (
    AccountAllowanceCoordinator,
    project_account_telemetry,
    safe_profile_label,
)
from custom_components.codex_bridge.bridge_api import BridgeApiConnectionError
from custom_components.codex_bridge.const import (
    CONF_BRIDGE_TOKEN, CONF_BRIDGE_URL, CONF_CONNECTION_TYPE,
    CONNECTION_TYPE_SUPERVISOR, DOMAIN,
)
from custom_components.codex_bridge.runtime import async_get_runtime

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)
PROFILE_A = "a" * 32
PROFILE_B = "b" * 32


def _row(profile_id: str, label: str, *, active: bool, remaining: int, reset: int):
    return {
        "id": profile_id,
        "label": label,
        "active": active,
        "status": "available",
        "updated_at": NOW.isoformat(),
        "windows": [
            {"name": "5 hours", "window_minutes": 300,
             "remaining_percent": remaining, "resets_at": reset},
            {"name": "Weekly", "window_minutes": 10080,
             "remaining_percent": remaining - 10, "resets_at": reset + 86400},
        ],
        "available_resets": 0,
        "next_reset_expiry": None,
        "expiry_complete": True,
        "five_hour_enabled": True,
    }


def test_projection_keeps_unknown_distinct_from_zero_and_rejects_wrong_windows():
    row = _row(PROFILE_A, "A", active=True, remaining=100, reset=2_000_000_000)
    row["windows"][1]["window_minutes"] = 60
    row["available_resets"] = None
    data = project_account_telemetry({"inventory_complete": True, "profiles": [row]}, now=NOW)
    profile = data.profiles[PROFILE_A]
    assert profile.windows["5 hours"].remaining_percent == 100
    assert profile.windows["5 hours"].used_percent == 0
    assert "Weekly" not in profile.windows
    assert profile.available_resets is None
    assert profile.next_reset_expiry is None
    assert profile.expiry_complete is False
    assert profile.windows["5 hours"].resets_at == datetime.fromtimestamp(2_000_000_000, UTC)


@pytest.mark.parametrize("value", [-1, 101, float("nan"), "0", True])
def test_invalid_percent_is_unknown(value):
    row = _row(PROFILE_A, "A", active=True, remaining=50, reset=2_000_000_000)
    row["windows"][0]["remaining_percent"] = value
    profile = project_account_telemetry(
        {"inventory_complete": True, "profiles": [row]}, now=NOW,
    ).profiles[PROFILE_A]
    assert profile.windows["5 hours"].remaining_percent is None


def test_stale_and_unaware_time_are_not_fresh():
    row = _row(PROFILE_A, "A", active=True, remaining=50, reset=2_000_000_000)
    row["updated_at"] = (NOW - timedelta(minutes=16)).isoformat()
    row["windows"][0]["resets_at"] = "2026-09-27T13:00:00"
    profile = project_account_telemetry(
        {"inventory_complete": True, "profiles": [row]}, now=NOW,
    ).profiles[PROFILE_A]
    assert profile.status == "stale"
    assert profile.windows["5 hours"].resets_at is None


def test_label_redaction_and_reauthentication_status_are_explicit():
    assert "private@example.invalid" not in safe_profile_label("Work private@example.invalid")
    row = _row(PROFILE_A, "Work", active=False, remaining=20, reset=2_000_000_000)
    row["status"] = "reauthentication_required"
    row["reauthentication_required"] = True
    profile = project_account_telemetry(
        {"inventory_complete": True, "profiles": [row]}, now=NOW,
    ).profiles[PROFILE_A]
    assert profile.status == "reauthentication_required"
    assert profile.reauthentication_required is True


async def test_legacy_bridge_coordinator_does_not_call_unsupported_endpoint(hass):
    class Runtime:
        client = Mock()
        def supports_capability(self, _name):
            return False

    runtime = Runtime()
    runtime.client.async_account_profile_telemetry = AsyncMock()
    coordinator = AccountAllowanceCoordinator(hass, runtime)
    await coordinator.async_refresh()
    assert coordinator.last_update_success is False
    runtime.client.async_account_profile_telemetry.assert_not_called()
    await coordinator.async_close()


@asynccontextmanager
async def _loaded_account_entities(hass, *, fail_initial=False, capability=True, customise=None):
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(
        domain=DOMAIN, title="Codex Bridge", source="hassio",
        data={
            CONF_BRIDGE_URL: "http://127.0.0.1:8766",
            CONF_BRIDGE_TOKEN: "a" * 48,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_SUPERVISOR,
        },
        unique_id="bridge-account-allowance-test",
    )
    entry.add_to_hass(hass)
    at = datetime.now(UTC)
    payload = {"inventory_complete": True, "profiles": [
        _row(PROFILE_A, "Primary", active=True, remaining=70, reset=2_000_000_000),
        _row(PROFILE_B, "Spare", active=False, remaining=25, reset=2_000_100_000),
    ]}
    for row in payload["profiles"]:
        row["updated_at"] = at.isoformat()
    client = Mock()
    client.async_ready = AsyncMock(return_value=Mock(capabilities=(
        "api_v1", "account_profile_telemetry_v1",
    )))
    client.require_api_v1 = Mock()
    client.negotiated_api_version = 1
    client.async_get_status = AsyncMock(return_value={
        "auth": {"state": "ok", "auth_mode": "chatgpt", "auth_required": False,
                 "updated_at": at.isoformat()},
        "account": {"account_id": "private", "email": "private@example.invalid"},
        "limits": {"available": False, "updated_at": at.isoformat()},
    })
    client.async_list_threads = AsyncMock(return_value=[])
    client.async_replay_events = AsyncMock(return_value={
        "events": [], "next_cursor": 0, "minimum_cursor": 0,
        "has_more": False, "heartbeat": True,
    })
    async def wait_events(*, after):
        await asyncio.Event().wait()
    client.async_wait_events = AsyncMock(side_effect=wait_events)
    client.async_account_profile_telemetry = AsyncMock(side_effect=lambda: payload)
    client.async_close = AsyncMock()
    if fail_initial:
        client.async_account_profile_telemetry.side_effect = BridgeApiConnectionError()
    if not capability:
        client.async_ready.return_value.capabilities = ("api_v1",)
    if customise:
        customise(er.async_get(hass), entry)

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
        coordinator = hass.data[DOMAIN]["account_allowance_coordinators"][entry.entry_id]
        try:
            yield entry, client, payload, coordinator, er.async_get(hass)
        finally:
            assert await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


async def test_account_entities_follow_complete_inventory_and_keep_stable_registry_ids(hass):
    """Exercise real Integration setup, coordinator, sensor platform and registry."""
    async with _loaded_account_entities(hass) as (entry, client, payload, coordinator, registry):
        assert "account_profile_telemetry_v1" in async_get_runtime(hass).capabilities
        assert client.async_account_profile_telemetry.await_count == 1
        assert coordinator.last_update_success, repr(coordinator.last_exception)
        def account_registry_ids():
            return {
                item.unique_id: item.entity_id for item in registry.entities.values()
                if item.config_entry_id == entry.entry_id
                and "_profile_allowance_" in item.unique_id
            }
        first = account_registry_ids()
        assert len(first) == 18
        first_entity = first[f"{entry.entry_id}_profile_allowance_{PROFILE_A}_five_hour_used"]
        second_entity = first[f"{entry.entry_id}_profile_allowance_{PROFILE_B}_weekly_reset"]
        assert hass.states.get(first_entity).state == "30.0"
        assert hass.states.get(second_entity).state == datetime.fromtimestamp(
            2_000_100_000 + 86400, UTC,
        ).isoformat()

        # A switch and renamed profile update values/names without replacing ids.
        updated = datetime.now(UTC)
        payload["profiles"][0]["active"] = False
        payload["profiles"][0]["label"] = "Renamed Primary"
        payload["profiles"][1]["active"] = True
        payload["profiles"][1]["windows"][0]["remaining_percent"] = 10
        for row in payload["profiles"]:
            row["updated_at"] = updated.isoformat()
        coordinator.async_set_updated_data(project_account_telemetry(payload, now=updated))
        await hass.async_block_till_done()
        assert account_registry_ids() == first
        assert hass.states.get(first_entity).attributes["friendly_name"].endswith(
            "Renamed Primary 5-hour usage",
        )
        assert hass.states.get(first_entity).state == "30.0"

        # Partial inventory retains both accounts; complete removal removes only
        # the removed account's entities and leaves the remaining identity intact.
        payload["inventory_complete"] = False
        payload["profiles"] = []
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert account_registry_ids() == first
        payload["inventory_complete"] = True
        payload["profiles"] = [_row(PROFILE_A, "Renamed Primary", active=True,
                                    remaining=40, reset=2_000_000_000)]
        payload["profiles"][0]["updated_at"] = datetime.now(UTC).isoformat()
        coordinator.async_set_updated_data(project_account_telemetry(payload))
        await hass.async_block_till_done()
        assert len(account_registry_ids()) == 9
        assert f"{entry.entry_id}_profile_allowance_{PROFILE_B}_weekly_reset" not in account_registry_ids()
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        payload["profiles"][0]["updated_at"] = datetime.now(UTC).isoformat()
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
        assert account_registry_ids() == {
            unique_id: entity_id for unique_id, entity_id in first.items()
            if f"_profile_allowance_{PROFILE_A}_" in unique_id
        }


async def test_initial_outage_keeps_registered_entities_and_recovers(hass):
    def customise(registry, entry):
        item = registry.async_get_or_create(
            "sensor", DOMAIN, f"{entry.entry_id}_profile_allowance_{PROFILE_A}_weekly_used",
            config_entry=entry, suggested_object_id="custom_account_usage",
        )
        registry.async_update_entity(item.entity_id, name="Custom account")
    async with _loaded_account_entities(hass, fail_initial=True, customise=customise) as (
        entry, client, payload, coordinator, registry,
    ):
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_profile_allowance_{PROFILE_A}_weekly_used",
        )
        assert hass.states.get(entity_id).state == "unavailable"
        assert not coordinator.last_update_success
        client.async_account_profile_telemetry.side_effect = lambda: payload
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == "40.0"
        assert registry.async_get(entity_id).name == "Custom account"


async def test_old_capability_creates_no_accounts_and_never_reads_endpoint(hass):
    async with _loaded_account_entities(hass, capability=False) as (entry, client, _, _, registry):
        client.async_account_profile_telemetry.assert_not_called()
        assert not any("_profile_allowance_" in item.unique_id for item in registry.entities.values())
        assert registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_weekly_used")


async def test_dynamic_add_and_removed_disabled_account_on_reload(hass):
    async with _loaded_account_entities(hass) as (entry, client, payload, coordinator, registry):
        def ids():
            return {item.unique_id: item.entity_id for item in registry.entities.values()
                    if item.config_entry_id == entry.entry_id and "_profile_allowance_" in item.unique_id}
        first = ids()
        third = _row("c" * 32, "Third", active=False, remaining=99, reset=2_000_200_000)
        third["updated_at"] = datetime.now(UTC).isoformat()
        payload["profiles"].insert(0, third)
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert len(ids()) == 27
        assert all(ids()[key] == value for key, value in first.items())
        disabled_id = first[f"{entry.entry_id}_profile_allowance_{PROFILE_B}_weekly_used"]
        registry.async_update_entity(disabled_id, disabled_by=er.RegistryEntryDisabler.USER)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        payload["profiles"] = [row for row in payload["profiles"] if row["id"] != PROFILE_B]
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert len(ids()) == 18
        assert registry.async_get(disabled_id) is None
        assert hass.states.get(disabled_id) is None
        assert all(ids()[key] == value for key, value in first.items() if PROFILE_A in key)


async def test_five_hour_visibility_and_manual_choices_survive_reload(hass):
    async with _loaded_account_entities(hass) as (entry, client, payload, coordinator, registry):
        def entity(key):
            return registry.async_get_entity_id(
                "sensor", DOMAIN, f"{entry.entry_id}_profile_allowance_{PROFILE_A}_{key}",
            )
        usage, remaining, reset = (entity(key) for key in ("five_hour_used", "five_hour_remaining", "five_hour_reset"))
        row = payload["profiles"][0]
        row["five_hour_enabled"] = False
        row["status"] = "stale"
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert registry.async_get(usage).hidden_by is None
        assert hass.states.get(usage).state == "unavailable"
        row["status"] = "available"
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert registry.async_get(usage).hidden_by is er.RegistryEntryHider.INTEGRATION
        assert hass.states.get(usage).state == "unavailable"  # retained window is disabled
        registry.async_update_entity(usage, hidden_by=None, name="My allowance")
        registry.async_update_entity(remaining, hidden_by=er.RegistryEntryHider.USER)
        registry.async_update_entity(reset, disabled_by=er.RegistryEntryDisabler.USER)
        await hass.async_block_till_done()
        for enabled in (True, False):
            row["five_hour_enabled"] = enabled
            await coordinator.async_refresh()
            await hass.async_block_till_done()
            assert registry.async_get(usage).hidden_by is None
            assert registry.async_get(remaining).hidden_by is er.RegistryEntryHider.USER
            assert registry.async_get(reset).disabled_by is er.RegistryEntryDisabler.USER
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert registry.async_get(usage).hidden_by is None
        assert registry.async_get(usage).name == "My allowance"
        assert registry.async_get(remaining).hidden_by is er.RegistryEntryHider.USER
        assert registry.async_get(reset).disabled_by is er.RegistryEntryDisabler.USER


async def test_entity_states_report_unknown_credits_expiry_staleness_and_outage(hass):
    async with _loaded_account_entities(hass) as (entry, client, payload, coordinator, registry):
        def state(key):
            return hass.states.get(registry.async_get_entity_id(
                "sensor", DOMAIN, f"{entry.entry_id}_profile_allowance_{PROFILE_A}_{key}",
            ))
        row = payload["profiles"][0]
        assert state("reset_credits").state == "0"
        row["available_resets"] = None
        row["windows"][0]["resets_at"] = None
        await coordinator.async_refresh()
        assert state("reset_credits").state == "unavailable"
        assert state("five_hour_reset").state == "unavailable"
        row["available_resets"] = 2
        row["next_reset_expiry"] = "2030-01-01T12:00:00+02:00"
        row["expiry_complete"] = False
        await coordinator.async_refresh()
        assert state("reset_credits").state == "2"
        assert state("next_reset_expiry").state == "2030-01-01T10:00:00+00:00"
        assert state("next_reset_expiry").attributes["expiry_complete"] is False
        row["updated_at"] = (datetime.now(UTC) - timedelta(minutes=16)).isoformat()
        await coordinator.async_refresh()
        assert state("status").state == "stale"
        assert state("weekly_used").state == "unavailable"
        assert state("status").attributes["data_age_seconds"] >= 960
        row["updated_at"] = datetime.now(UTC).isoformat()
        row["reauthentication_required"] = True
        await coordinator.async_refresh()
        assert state("status").state == "reauthentication_required"
        assert state("weekly_used").state == "unavailable"
        client.async_account_profile_telemetry.side_effect = BridgeApiConnectionError()
        await coordinator.async_refresh()
        assert state("status").state == "unavailable"
        assert state("status").attributes["source_available"] is False
        assert state("status").attributes["telemetry_status"] == "unavailable"


@pytest.mark.parametrize("payload", [
    {"inventory_complete": False, "profiles": []},
    {"inventory_complete": True, "profiles": [None]},
    {"inventory_complete": True, "profiles": [
        _row(PROFILE_A, "A", active=True, remaining=50, reset=2_000_000_000),
        _row(PROFILE_A, "A", active=False, remaining=50, reset=2_000_000_000),
    ]},
    {"inventory_complete": True, "profiles": [
        _row(PROFILE_A, "A", active=True, remaining=50, reset=2_000_000_000),
        _row(PROFILE_B, "B", active=True, remaining=50, reset=2_000_000_000),
    ]},
])
def test_invalid_inventory_cannot_confirm_account_removal(payload):
    with pytest.raises(ValueError):
        project_account_telemetry(payload, now=NOW)


@pytest.mark.parametrize("value", [-1, 10001, True, "0"])
def test_invalid_credit_counts_are_unknown(value):
    row = _row(PROFILE_A, "A", active=True, remaining=50, reset=2_000_000_000)
    row["available_resets"] = value
    row["next_reset_expiry"] = 2_000_000_000
    profile = project_account_telemetry({"inventory_complete": True, "profiles": [row]}, now=NOW).profiles[PROFILE_A]
    assert profile.available_resets is None
    assert profile.next_reset_expiry is None
    assert profile.expiry_complete is False
