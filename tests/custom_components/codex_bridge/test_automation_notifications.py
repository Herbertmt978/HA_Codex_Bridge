import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.codex_bridge.automation_notifications import (
    AutomationNotificationCoordinator,
)


class _Store:
    def __init__(self):
        self.value = None

    async def async_load(self):
        return self.value

    async def async_save(self, value):
        self.value = {"receipts": dict(value["receipts"])}


class _Services:
    def __init__(self, available):
        self.available = set(available)
        self.calls = []

    def has_service(self, domain, name):
        return (domain, name) in self.available

    async def async_call(self, domain, name, data, **kwargs):
        self.calls.append((domain, name, data))


def _settings(policy="all", persistent=True, targets=None, preview=False):
    return {
        "policy": policy,
        "persistent": persistent,
        "mobile_targets": targets or [],
        "preview": preview,
    }


def _run(status="completed", notifications=None):
    return {
        "automation_run_id": "autrun_" + "a" * 32,
        "status": status,
        "thread_id": "thread_test",
        "bridge_run_id": "run_test",
        "created_at": "2026-09-25T08:00:00Z",
        "notifications_revision": 1,
        "notifications": notifications or _settings(targets=["mobile_app_test_phone"]),
    }


def _fixture(monkeypatch, definitions, runs, *, services=None, store=None):
    monkeypatch.setattr(
        "custom_components.codex_bridge.automation_notifications.async_track_time_interval",
        lambda *_args: lambda: None,
    )
    client = SimpleNamespace(
        async_list_automations=AsyncMock(return_value=definitions),
        async_list_automation_runs=AsyncMock(return_value=runs),
        async_automation_run_preview=AsyncMock(return_value=None),
    )
    runtime = SimpleNamespace(
        client=client,
        supports_capability=lambda value: value == "automation_notifications_v1",
    )
    services = services or _Services(
        {("notify", "mobile_app_test_phone"), ("persistent_notification", "create")}
    )
    return (
        AutomationNotificationCoordinator(
            SimpleNamespace(services=services), runtime, store or _Store()
        ),
        client,
        services,
    )


async def test_delivery_is_opt_in_selected_and_durable_across_restart(monkeypatch):
    definition = {
        "automation_id": "aut_one",
        "name": "Morning check",
        "notifications_revision": 1,
        "notifications": _settings(targets=["mobile_app_test_phone"]),
    }
    store = _Store()
    first, _client, services = _fixture(
        monkeypatch, [definition], [_run()], store=store
    )
    await first.async_start()
    await first.async_start()
    await first.async_refresh()
    assert [(domain, name) for domain, name, _ in services.calls] == [
        ("persistent_notification", "create"),
        ("notify", "mobile_app_test_phone"),
    ]
    assert services.calls[1][2]["data"]["url"] == "/codex-bridge?thread=thread_test"
    assert (
        services.calls[1][2]["data"]["clickAction"]
        == "/codex-bridge?thread=thread_test"
    )
    assert services.calls[0][2]["title"] == "Codex Bridge"
    assert "Morning check" not in services.calls[0][2]["message"]
    await first.async_close()
    second, _client, _services = _fixture(
        monkeypatch, [definition], [_run()], services=services, store=store
    )
    await second.async_start()
    assert len(services.calls) == 2
    await second.async_close()


@pytest.mark.parametrize(
    "status,policy,expected",
    [
        ("completed", "attention", 0),
        ("completed", "all", 1),
        ("failed", "attention", 1),
        ("skipped_misfire", "attention", 1),
        ("cancelled", "attention", 0),
        ("cancelled", "all", 1),
    ],
)
async def test_policy_covers_terminal_and_skipped_outcomes(
    monkeypatch, status, policy, expected
):
    definition = {
        "automation_id": "aut_one",
        "name": "Morning check",
        "notifications_revision": 1,
        "notifications": _settings(policy=policy, targets=[]),
    }
    coordinator, _client, services = _fixture(
        monkeypatch, [definition], [_run(status, _settings(policy=policy, targets=[]))]
    )
    await coordinator.async_start()
    assert len(services.calls) == expected
    await coordinator.async_close()


async def test_current_off_mutes_and_missing_mobile_does_not_retry(monkeypatch):
    definition = {
        "automation_id": "aut_one",
        "name": "Morning check",
        "notifications_revision": 1,
        "notifications": _settings(policy="off", targets=["mobile_app_test_phone"]),
    }
    coordinator, _client, services = _fixture(monkeypatch, [definition], [_run()])
    await coordinator.async_start()
    assert services.calls == []
    await coordinator.async_close()
    definition["notifications"] = _settings(
        persistent=False, targets=["mobile_app_test_phone"]
    )
    unavailable = _Services(set())
    store = _Store()
    coordinator, _client, _services = _fixture(
        monkeypatch, [definition], [_run()], services=unavailable, store=store
    )
    await coordinator.async_start()
    await coordinator.async_refresh()
    assert unavailable.calls == []
    assert len(store.value["receipts"]) == 1
    await coordinator.async_close()


async def test_delivery_error_does_not_log_private_payload_or_retry(
    monkeypatch, caplog
):
    settings = _settings(persistent=False, targets=["mobile_app_test_phone"])
    definition = {
        "automation_id": "aut_one",
        "name": "Private task title",
        "notifications_revision": 1,
        "notifications": settings,
    }
    services = _Services({("notify", "mobile_app_test_phone")})
    services.async_call = AsyncMock(
        side_effect=RuntimeError("private result echoed by notification service")
    )
    store = _Store()
    coordinator, _client, _services = _fixture(
        monkeypatch,
        [definition],
        [_run(notifications=settings)],
        services=services,
        store=store,
    )
    await coordinator.async_start()
    await coordinator.async_refresh()
    assert services.async_call.await_count == 1
    assert len(store.value["receipts"]) == 1
    assert "Private task title" not in caplog.text
    assert "private result" not in caplog.text
    assert "mobile_app_test_phone" not in caplog.text
    await coordinator.async_close()


async def test_unload_stops_an_in_flight_notification_refresh(monkeypatch):
    definition = {
        "automation_id": "aut_one",
        "name": "Morning check",
        "notifications_revision": 1,
        "notifications": _settings(targets=["mobile_app_test_phone"]),
    }
    coordinator, client, services = _fixture(monkeypatch, [definition], [_run()])
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_definitions():
        started.set()
        await release.wait()
        return [definition]

    client.async_list_automations.side_effect = delayed_definitions
    refresh = asyncio.create_task(coordinator.async_refresh())
    await started.wait()
    await coordinator.async_close()
    release.set()
    await refresh
    assert services.calls == []
    client.async_list_automation_runs.assert_not_awaited()


async def test_unload_during_receipt_save_does_not_deliver(monkeypatch):
    settings = _settings(persistent=False, targets=["mobile_app_test_phone"])
    definition = {
        "automation_id": "aut_one",
        "name": "Morning check",
        "notifications_revision": 1,
        "notifications": settings,
    }
    store = _Store()
    save_started = asyncio.Event()
    release_save = asyncio.Event()

    async def delayed_save(value):
        save_started.set()
        await release_save.wait()
        await _Store.async_save(store, value)

    store.async_save = delayed_save
    coordinator, _client, services = _fixture(
        monkeypatch, [definition], [_run(notifications=settings)], store=store
    )
    refresh = asyncio.create_task(coordinator.async_refresh())
    await save_started.wait()
    await coordinator.async_close()
    release_save.set()
    await refresh
    assert services.calls == []


async def test_preview_is_brief_opt_in_and_scoped_to_run(monkeypatch):
    settings = _settings(targets=["mobile_app_test_phone"], preview=True)
    definition = {
        "automation_id": "aut_one",
        "name": "Morning check",
        "notifications_revision": 1,
        "notifications": settings,
    }
    coordinator, client, services = _fixture(
        monkeypatch, [definition], [_run(notifications=settings)]
    )
    client.async_automation_run_preview.return_value = "Finished the report."
    await coordinator.async_start()
    assert "Finished the report" not in services.calls[0][2]["message"]
    assert "Finished the report" in services.calls[1][2]["message"]
    client.async_automation_run_preview.assert_awaited_once_with(
        "aut_one", _run()["automation_run_id"]
    )
    await coordinator.async_refresh()
    assert client.async_automation_run_preview.await_count == 1
    await coordinator.async_close()


async def test_notification_setting_change_does_not_alert_for_older_run(monkeypatch):
    definition = {
        "automation_id": "aut_one",
        "name": "Morning check",
        "notifications_revision": 2,
        "notifications": _settings(targets=[]),
    }
    coordinator, _client, services = _fixture(
        monkeypatch, [definition], [_run(notifications=_settings(targets=[]))]
    )
    await coordinator.async_start()
    assert services.calls == []
    await coordinator.async_close()


async def test_real_ha_services_receive_only_selected_disposable_recipients(
    hass, monkeypatch
):
    """Exercise HA's service registry and dispatch rather than a fake service object."""
    monkeypatch.setattr(
        "custom_components.codex_bridge.automation_notifications.async_track_time_interval",
        lambda *_args: lambda: None,
    )
    calls = []

    async def record(call):
        calls.append((call.domain, call.service, dict(call.data)))

    hass.services.async_register("persistent_notification", "create", record)
    hass.services.async_register("notify", "mobile_app_test_phone", record)
    hass.services.async_register("notify", "mobile_app_other_phone", record)
    definition = {
        "automation_id": "aut_one",
        "name": "Morning check",
        "notifications_revision": 1,
        "notifications": _settings(targets=["mobile_app_test_phone"]),
    }
    client = SimpleNamespace(
        async_list_automations=AsyncMock(return_value=[definition]),
        async_list_automation_runs=AsyncMock(return_value=[_run()]),
        async_automation_run_preview=AsyncMock(return_value=None),
    )
    runtime = SimpleNamespace(
        client=client,
        supports_capability=lambda value: value == "automation_notifications_v1",
    )
    coordinator = AutomationNotificationCoordinator(hass, runtime, _Store())
    try:
        await coordinator.async_start()
        assert [(domain, service) for domain, service, _ in calls] == [
            ("persistent_notification", "create"),
            ("notify", "mobile_app_test_phone"),
        ]
        assert calls[0][2]["title"] == "Codex Bridge"
        assert "Morning check" not in calls[0][2]["message"]
        assert calls[1][2]["title"] == "Codex Bridge: Morning check"
    finally:
        await coordinator.async_close()
