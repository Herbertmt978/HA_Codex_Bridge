from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HassJob, HassJobType, HomeAssistant

from custom_components.codex_bridge.automation_scheduler import AutomationScheduler
from custom_components.codex_bridge.bridge_api import (
    BridgeApiConnectionError,
    BridgeApiError,
)
from custom_components.codex_bridge.const import CONNECTION_TYPE_EXTERNAL_LEGACY


class _Hass:
    def __init__(self) -> None:
        self.config = SimpleNamespace(time_zone="Europe/London")


async def test_scheduler_registers_a_coroutine_callback_ha_dispatches(
    monkeypatch, tmp_path
):
    callbacks = []

    def track(_hass, action, due):
        callbacks.append((action, due))
        return lambda: None

    monkeypatch.setattr(
        "custom_components.codex_bridge.automation_scheduler.async_track_point_in_time",
        track,
    )
    client = AsyncMock()
    client.async_scheduler_automations.side_effect = [
        {
            "automations": [
                {
                    "automation_id": "aut_1",
                    "revision": 4,
                    "next_run_at": "2026-07-15T10:00:00Z",
                }
            ]
        },
        {"automations": []},
    ]
    hass = HomeAssistant(str(tmp_path))
    scheduler = AutomationScheduler(hass, client, "supervisor")

    try:
        await scheduler.async_start()
        action, due = callbacks[0]

        async def returns_coroutine(_when: datetime) -> None:
            return None

        assert (
            HassJob(lambda when: returns_coroutine(when)).job_type
            is HassJobType.Executor
        )

        job = HassJob(action)
        assert job.job_type is HassJobType.Coroutinefunction
        task = hass.async_run_hass_job(job, due)
        assert task is not None
        await task

        client.async_claim_automation_run.assert_awaited_once_with(
            "aut_1",
            due_at="2026-07-15T10:00:00Z",
            idempotency_key="automation:aut_1:4:2026-07-15T10:00:00Z",
            expected_revision=4,
        )
        assert client.async_scheduler_automations.await_count == 2
    finally:
        await scheduler.async_close()
        await hass.async_stop(force=True)


async def test_scheduler_forwards_native_web_search_only_when_configured(monkeypatch):
    callbacks = []

    def track(_hass, action, due):
        callbacks.append((action, due))
        return lambda: None

    monkeypatch.setattr(
        "custom_components.codex_bridge.automation_scheduler.async_track_point_in_time",
        track,
    )
    client = AsyncMock()
    client.async_scheduler_automations.side_effect = [
        {
            "automations": [
                {
                    "automation_id": "aut_1",
                    "revision": 4,
                    "next_run_at": "2026-07-15T10:00:00Z",
                }
            ]
        },
        {"automations": []},
    ]
    scheduler = AutomationScheduler(
        _Hass(), client, "supervisor", web_search_mode="disabled"
    )

    await scheduler.async_start()
    action, due = callbacks[0]
    await action(due)

    client.async_claim_automation_run.assert_awaited_once_with(
        "aut_1",
        due_at="2026-07-15T10:00:00Z",
        idempotency_key="automation:aut_1:4:2026-07-15T10:00:00Z",
        expected_revision=4,
        web_search="disabled",
    )
    await scheduler.async_close()


async def test_scheduler_is_disabled_for_external_legacy_without_contacting_bridge():
    client = AsyncMock()
    scheduler = AutomationScheduler(_Hass(), client, CONNECTION_TYPE_EXTERNAL_LEGACY)

    await scheduler.async_start()
    await scheduler.async_close()

    client.async_scheduler_automations.assert_not_awaited()


async def test_scheduler_uses_ha_timezone_and_cancels_replaced_callbacks(monkeypatch):
    cancelled: list[str] = []

    def track(_hass, _action, _due):
        token = f"timer-{len(cancelled)}"
        return lambda: cancelled.append(token)

    monkeypatch.setattr(
        "custom_components.codex_bridge.automation_scheduler.async_track_point_in_time",
        track,
    )
    client = AsyncMock()
    client.async_scheduler_automations.return_value = {
        "automations": [
            {
                "automation_id": "aut_1",
                "revision": 1,
                "next_run_at": "2030-07-15T10:00:00Z",
            }
        ]
    }
    scheduler = AutomationScheduler(_Hass(), client, "supervisor")

    await scheduler.async_start()
    await scheduler.async_refresh()
    await scheduler.async_close()

    assert scheduler.timezone.key == "Europe/London"
    assert len(cancelled) == 2


async def test_scheduler_bounds_bridge_unavailable_retries_and_cancels_them_on_close(
    monkeypatch,
):
    callbacks = []
    retries = []
    retry_cancelled: list[bool] = []

    def track(_hass, action, due):
        callbacks.append((action, due))
        return lambda: None

    def call_later(_hass, delay, action):
        retries.append((delay, action))
        return lambda: retry_cancelled.append(True)

    monkeypatch.setattr(
        "custom_components.codex_bridge.automation_scheduler.async_track_point_in_time",
        track,
    )
    monkeypatch.setattr(
        "custom_components.codex_bridge.automation_scheduler.async_call_later",
        call_later,
    )
    client = AsyncMock()
    client.async_scheduler_automations.return_value = {
        "automations": [
            {
                "automation_id": "aut_1",
                "revision": 1,
                "next_run_at": "2030-07-15T10:00:00Z",
            }
        ]
    }
    client.async_claim_automation_run.side_effect = BridgeApiConnectionError()
    scheduler = AutomationScheduler(_Hass(), client, "supervisor")

    await scheduler.async_start()
    action, due = callbacks[0]
    await action(due)
    await scheduler.async_close()

    assert retries[0][0] == 15
    assert retry_cancelled == [True]


async def test_scheduler_retries_a_retryable_bridge_problem_without_spinning(
    monkeypatch,
):
    callbacks = []
    retries = []

    def track(_hass, action, due):
        callbacks.append((action, due))
        return lambda: None

    def call_later(_hass, delay, action):
        retries.append((delay, action))
        return lambda: None

    monkeypatch.setattr(
        "custom_components.codex_bridge.automation_scheduler.async_track_point_in_time",
        track,
    )
    monkeypatch.setattr(
        "custom_components.codex_bridge.automation_scheduler.async_call_later",
        call_later,
    )
    client = AsyncMock()
    client.async_scheduler_automations.return_value = {
        "automations": [
            {
                "automation_id": "aut_1",
                "revision": 1,
                "next_run_at": "2030-07-15T10:00:00Z",
            }
        ]
    }
    client.async_claim_automation_run.side_effect = BridgeApiError(
        "bridge_busy", retryable=True
    )
    scheduler = AutomationScheduler(_Hass(), client, "supervisor")

    await scheduler.async_start()
    action, due = callbacks[0]
    await action(due)

    assert retries[0][0] == 15
    assert client.async_scheduler_automations.await_count == 1


async def test_expired_retry_schedules_one_bounded_reconciliation_refresh(
    monkeypatch,
):
    scheduled = []
    point_callbacks = []

    def call_later(_hass, delay, action):
        scheduled.append((delay, action))
        return lambda: None

    def track(_hass, action, due):
        point_callbacks.append((action, due))
        return lambda: None

    monkeypatch.setattr(
        "custom_components.codex_bridge.automation_scheduler.async_call_later",
        call_later,
    )
    monkeypatch.setattr(
        "custom_components.codex_bridge.automation_scheduler.async_track_point_in_time",
        track,
    )
    client = AsyncMock()
    client.async_scheduler_automations.return_value = {
        "automations": [
            {
                "automation_id": "aut_future",
                "revision": 2,
                "next_run_at": "2030-07-16T10:00:00Z",
            }
        ]
    }
    scheduler = AutomationScheduler(_Hass(), client, "supervisor")
    expired = datetime(2000, 1, 1, tzinfo=UTC)

    scheduler._schedule_retry("aut_old", 1, expired)
    scheduler._schedule_retry("aut_old", 1, expired)

    assert len(scheduled) == 1
    delay, reconcile = scheduled[0]
    assert delay == scheduler._grace.total_seconds()
    await reconcile(expired)
    client.async_scheduler_automations.assert_awaited_once()
    assert len(point_callbacks) == 1


async def test_reconciliation_rearms_once_after_an_invalid_refresh(monkeypatch):
    scheduled = []

    def call_later(_hass, delay, action):
        scheduled.append((delay, action))
        return lambda: None

    monkeypatch.setattr(
        "custom_components.codex_bridge.automation_scheduler.async_call_later",
        call_later,
    )
    client = AsyncMock()
    client.async_scheduler_automations.side_effect = [
        ValueError("invalid snapshot"),
        {"automations": []},
    ]
    scheduler = AutomationScheduler(_Hass(), client, "supervisor")

    scheduler.schedule_reconciliation()
    await scheduled[0][1](datetime.now(UTC))

    assert len(scheduled) == 2
    assert scheduled[0][0] == scheduler._grace.total_seconds()
    assert scheduled[1][0] == scheduler._grace.total_seconds()
    await scheduled[1][1](datetime.now(UTC))
    assert len(scheduled) == 2


@pytest.mark.parametrize(
    "refresh_error",
    [BridgeApiError("bridge_busy", retryable=True), ValueError("invalid snapshot")],
)
async def test_successful_claim_refresh_failure_arms_reconciliation(
    monkeypatch,
    refresh_error: Exception,
):
    points = []
    scheduled = []

    def track(_hass, action, due):
        points.append((action, due))
        return lambda: None

    def call_later(_hass, delay, action):
        scheduled.append((delay, action))
        return lambda: None

    monkeypatch.setattr(
        "custom_components.codex_bridge.automation_scheduler.async_track_point_in_time",
        track,
    )
    monkeypatch.setattr(
        "custom_components.codex_bridge.automation_scheduler.async_call_later",
        call_later,
    )
    client = AsyncMock()
    client.async_scheduler_automations.side_effect = [
        {
            "automations": [
                {
                    "automation_id": "aut_1",
                    "revision": 1,
                    "next_run_at": "2030-07-15T10:00:00Z",
                }
            ]
        },
        refresh_error,
        {"automations": []},
    ]
    scheduler = AutomationScheduler(_Hass(), client, "supervisor")

    await scheduler.async_start()
    action, due = points[0]
    await action(due)

    assert len(scheduled) == 1
    assert scheduled[0][0] == scheduler._grace.total_seconds()
    await scheduled[0][1](datetime.now(UTC))
    assert len(scheduled) == 1
