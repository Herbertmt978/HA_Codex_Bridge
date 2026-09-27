"""Question delivery claims survive reload and are cleaned on entry deletion."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers.storage import Store
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.codex_bridge.const import (
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    CONF_CONNECTION_TYPE,
    CONF_QUESTION_NOTIFICATIONS_ENABLED,
    CONF_QUESTION_NOTIFICATIONS_TARGETS,
    CONNECTION_TYPE_EXTERNAL_LEGACY,
    CONNECTION_TYPE_SUPERVISOR,
    DATA_ENTRIES,
    DOMAIN,
)
from custom_components.codex_bridge.question_notification_recipients import (
    QuestionNotificationRecipient,
)
from custom_components.codex_bridge.question_notifications import QuestionNotificationCoordinator

from test_question_notifications import _interaction

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def _setup_connection(hass, monkeypatch, connection_type):
    """Replace only network/UI boundaries; use HA's real entry manager and stores."""
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(
        version=1, minor_version=1, domain=DOMAIN, title="Test Bridge",
        data={
            CONF_CONNECTION_TYPE: connection_type,
            CONF_BRIDGE_URL: "http://127.0.0.1:8766",
            CONF_BRIDGE_TOKEN: "a" * 48,
        },
        options={
            CONF_QUESTION_NOTIFICATIONS_ENABLED: True,
            CONF_QUESTION_NOTIFICATIONS_TARGETS: ["test-phone"],
        },
    )
    entry.add_to_hass(hass)
    client = Mock()
    client.negotiated_api_version = 1 if connection_type == CONNECTION_TYPE_SUPERVISOR else 0
    client.async_ready = AsyncMock(return_value=SimpleNamespace(capabilities=("api_v1", "interactions_v2")))
    client.async_close = AsyncMock()
    client.async_get_status = AsyncMock(return_value={})
    client.async_list_threads = AsyncMock(return_value=[])
    client.async_list_pending_interactions = AsyncMock(return_value={"items": [_interaction()]})
    client.async_replay_events = AsyncMock(return_value={
        "events": [], "next_cursor": 0, "minimum_cursor": 0,
        "has_more": False, "heartbeat": True,
    })

    async def wait_events(*, after):
        await asyncio.Event().wait()

    client.async_wait_events = AsyncMock(side_effect=wait_events)
    monkeypatch.setattr("custom_components.codex_bridge.BridgeApiClient", Mock(return_value=client))
    monkeypatch.setattr("custom_components.codex_bridge.async_register_http_views", Mock())
    monkeypatch.setattr("custom_components.codex_bridge.async_register_websocket_commands", Mock())
    monkeypatch.setattr("custom_components.codex_bridge.async_register_panel", AsyncMock())
    monkeypatch.setattr("custom_components.codex_bridge.async_remove_panel", Mock())
    monkeypatch.setattr("homeassistant.components.frontend.async_setup", AsyncMock(return_value=True))
    recipient = QuestionNotificationRecipient(
        registration_id="test-phone", device_id="test-device", user_id="test-admin",
        service="mobile_app_test_phone", label="Test phone", inline_reply=True,
    )
    monkeypatch.setattr(
        "custom_components.codex_bridge.question_notifications.async_resolve_question_recipients",
        AsyncMock(return_value=(recipient,)),
    )
    calls = []
    fail_clear = [False]

    async def service_call(call):
        if fail_clear[0] and (call.service == "dismiss" or call.data.get("message") == "clear_notification"):
            raise RuntimeError("synthetic delivery failure")
        calls.append((call.domain, call.service, dict(call.data)))

    hass.services.async_register("notify", "mobile_app_test_phone", service_call)
    hass.services.async_register("persistent_notification", "create", service_call)
    hass.services.async_register("persistent_notification", "dismiss", service_call)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.question_notifications")
    if connection_type == CONNECTION_TYPE_EXTERNAL_LEGACY:
        # Exercise an existing ledger even when the loaded compatibility runtime
        # has no question coordinator or current notification capability.
        runtime = SimpleNamespace(
            client=client, event_broker=None,
            question_notification_settings={
                "enabled": True, "persistent": True, "preview": False, "mobile_targets": ["test-phone"],
            },
            supports_capability=lambda capability: capability == "interactions_v2",
        )
        owner = QuestionNotificationCoordinator(hass, runtime, store)
        await owner.async_start()
        await owner.async_close()
    assert len(calls) == 2
    assert len((await store.async_load())["interactions"]) == 1
    return entry, store, calls, fail_clear


@pytest.mark.parametrize("connection_type", [CONNECTION_TYPE_SUPERVISOR, CONNECTION_TYPE_EXTERNAL_LEGACY])
async def test_public_entry_reload_retains_claims_and_removal_clears_notices(hass, monkeypatch, connection_type):
    entry, store, calls, _fail_clear = await _setup_connection(hass, monkeypatch, connection_type)
    original = await store.async_load()
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert await store.async_load() == original
    assert len(calls) == 2  # The retained claims must not deliver twice on reload.
    assert await hass.config_entries.async_remove(entry.entry_id) == {"require_restart": False}
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    assert entry.entry_id not in hass.data[DOMAIN][DATA_ENTRIES]
    # A fresh owner must read the actual saved result, rather than the old
    # Store instance's cached load from before entry removal.
    saved = await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.question_notifications").async_load()
    assert saved == {"interactions": []}
    assert [(domain, service) for domain, service, _data in calls[2:]] == [
        ("notify", "mobile_app_test_phone"), ("persistent_notification", "dismiss"),
    ]
    assert calls[2][2] == {
        "message": "clear_notification", "data": {"tag": "codex_bridge_question_question-1"},
    }
    assert calls[3][2] == {"notification_id": "codex_bridge_question_question-1"}


@pytest.mark.parametrize("connection_type", [CONNECTION_TYPE_SUPERVISOR, CONNECTION_TYPE_EXTERNAL_LEGACY])
async def test_public_entry_removal_retains_cleanup_evidence_when_service_fails(
    hass, monkeypatch, caplog, connection_type,
):
    entry, store, _calls, fail_clear = await _setup_connection(hass, monkeypatch, connection_type)
    original = await store.async_load()
    fail_clear[0] = True
    assert await hass.config_entries.async_remove(entry.entry_id) == {"require_restart": False}
    assert hass.config_entries.async_get_entry(entry.entry_id) is None
    saved = await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.question_notifications").async_load()
    assert saved == original
    assert "Question notification removal is incomplete" in caplog.text


async def _inflight_notification(hass, monkeypatch, *, source="timer"):
    entry, store, calls, _fail_clear = await _setup_connection(hass, monkeypatch, CONNECTION_TYPE_SUPERVISOR)
    runtime = hass.data[DOMAIN][DATA_ENTRIES][entry.entry_id]
    owner = runtime.question_notifications
    started, release = asyncio.Event(), asyncio.Event()
    second = {**_interaction(), "interaction_id": "question-2", "thread_id": "thread-2"}
    runtime.client.async_list_pending_interactions.return_value = {"items": [_interaction(), second]}

    async def block_send(call):
        if call.data.get("message") != "clear_notification":
            started.set()
            await release.wait()
        calls.append((call.domain, call.service, dict(call.data)))

    hass.services.async_register("notify", "mobile_app_test_phone", block_send)
    if source == "timer":
        # Advance HA's real interval handles and let its background job admit
        # the delivery. No timer callback or entry-manager method is mocked.
        async_fire_time_changed(hass, datetime.now(UTC) + timedelta(seconds=31))
    else:
        await owner._on_broker_event(SimpleNamespace(event_type="interaction.created"))
    await asyncio.wait_for(started.wait(), timeout=2)
    assert owner._lock.locked()
    return entry, store, calls, owner, release


async def _wait_for_close_fence(owner):
    while not owner._closed:
        await asyncio.sleep(0)


@pytest.mark.parametrize("boundary", ["unload", "remove"])
@pytest.mark.parametrize("source", ["timer", "broker"])
async def test_public_entry_drains_inflight_delivery_before_removal(hass, monkeypatch, boundary, source):
    entry, _store, calls, owner, release = await _inflight_notification(hass, monkeypatch, source=source)
    operation = hass.config_entries.async_unload if boundary == "unload" else hass.config_entries.async_remove
    removal = asyncio.create_task(operation(entry.entry_id))
    try:
        await asyncio.wait_for(_wait_for_close_fence(owner), timeout=2)
        assert not removal.done()
        assert entry.entry_id in hass.data[DOMAIN][DATA_ENTRIES]
        assert not any(call[2].get("message") == "clear_notification" for call in calls)
        release.set()
        result = await asyncio.wait_for(removal, timeout=2)
        if boundary == "unload":
            assert result is True
            assert entry.state is ConfigEntryState.NOT_LOADED
            assert not any(call[2].get("message") == "clear_notification" for call in calls)
            assert await hass.config_entries.async_remove(entry.entry_id) == {"require_restart": False}
        else:
            assert result == {"require_restart": False}
        assert entry.entry_id not in hass.data[DOMAIN][DATA_ENTRIES]
        tag = "codex_bridge_question_question-2"
        matching = [(index, call[2]["message"]) for index, call in enumerate(calls)
                    if call[0] == "notify" and call[2].get("data", {}).get("tag") == tag]
        assert [message for _index, message in matching] == ["A question needs your response.", "clear_notification"]
        saved = await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.question_notifications").async_load()
        assert saved == {"interactions": []}
        final_calls = list(calls)
        async_fire_time_changed(hass, datetime.now(UTC) + timedelta(seconds=65))
        await hass.async_block_till_done()
        assert calls == final_calls
    finally:
        release.set()
        if not removal.done():
            await asyncio.wait_for(removal, timeout=2)


async def test_cancelled_public_removal_keeps_owner_until_delivery_finishes(hass, monkeypatch):
    entry, _store, calls, owner, release = await _inflight_notification(hass, monkeypatch)
    removal = asyncio.create_task(hass.config_entries.async_remove(entry.entry_id))
    try:
        await asyncio.wait_for(_wait_for_close_fence(owner), timeout=2)
        removal.cancel()
        with pytest.raises(asyncio.CancelledError):
            await removal
        assert hass.config_entries.async_get_entry(entry.entry_id) is entry
        assert hass.data[DOMAIN][DATA_ENTRIES][entry.entry_id].question_notifications is owner
        saved = await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.question_notifications").async_load()
        assert saved["interactions"]
        retry = asyncio.create_task(hass.config_entries.async_remove(entry.entry_id))
        await asyncio.sleep(0)
        assert not retry.done()
        assert not any(call[2].get("message") == "clear_notification" for call in calls)
        release.set()
        assert await asyncio.wait_for(retry, timeout=2) == {"require_restart": False}
        assert entry.entry_id not in hass.data[DOMAIN][DATA_ENTRIES]
        assert calls[-1][1] == "dismiss"
        saved = await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.question_notifications").async_load()
        assert saved == {"interactions": []}
    finally:
        release.set()
        if not removal.done():
            await asyncio.wait_for(removal, timeout=2)


async def test_failed_public_platform_unload_still_drains_before_removal(hass, monkeypatch):
    entry, _store, calls, owner, release = await _inflight_notification(hass, monkeypatch)
    from homeassistant.components import sensor

    # Fail only the native platform callback, leaving HA's entry manager and
    # forwarding/unload machinery intact. HA removes after FAILED_UNLOAD.
    monkeypatch.setattr(sensor, "async_unload_entry", AsyncMock(return_value=False))
    assert await hass.config_entries.async_unload(entry.entry_id) is False
    assert entry.state is ConfigEntryState.FAILED_UNLOAD
    assert hass.data[DOMAIN][DATA_ENTRIES][entry.entry_id].question_notifications is owner
    removal = asyncio.create_task(hass.config_entries.async_remove(entry.entry_id))
    try:
        await asyncio.wait_for(_wait_for_close_fence(owner), timeout=2)
        assert not removal.done()
        assert not any(call[2].get("message") == "clear_notification" for call in calls)
        release.set()
        assert await asyncio.wait_for(removal, timeout=2) == {"require_restart": True}
        saved = await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.question_notifications").async_load()
        assert saved == {"interactions": []}
        assert entry.entry_id not in hass.data[DOMAIN][DATA_ENTRIES]
        matching = [call[2]["message"] for call in calls if call[0] == "notify"
                    and call[2].get("data", {}).get("tag") == "codex_bridge_question_question-2"]
        assert matching == ["A question needs your response.", "clear_notification"]
    finally:
        release.set()
        if not removal.done():
            await asyncio.wait_for(removal, timeout=2)
