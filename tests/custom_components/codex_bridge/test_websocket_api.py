"""Safe Home Assistant WebSocket contracts for the v1 event broker."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from custom_components.codex_bridge.bridge_api import BridgeApiError, BridgeApiGoneError
from custom_components.codex_bridge.const import DATA_ENTRIES, DOMAIN
from custom_components.codex_bridge.event_broker import EventBroker, EventRecord
from custom_components.codex_bridge.runtime import CodexBridgeRuntime
from custom_components.codex_bridge.protocol import ProblemRecord
from custom_components.codex_bridge.websocket_api import (
    ws_answer_interaction,
    ws_get_config,
    ws_get_event_status,
    ws_get_events,
    ws_get_status,
    ws_subscribe_events,
    ws_unsubscribe_events,
)


def _event(
    cursor: int,
    *,
    scope: str = "auth",
    thread_id: str | None = None,
) -> EventRecord:
    return EventRecord.from_payload(
        {
            "cursor": cursor,
            "event_id": f"evt_{cursor}",
            "scope": scope,
            "thread_id": thread_id,
            "event_type": f"{scope}.updated",
            "payload": {"state": "pending"},
            "timestamp": "2026-07-13T12:00:00Z",
        }
    )


class _Connection:
    def __init__(self) -> None:
        self.subscriptions = {}
        self.results: list[tuple[int, object]] = []
        self.errors: list[tuple[int, str, str]] = []
        self.events: list[tuple[int, object]] = []

    def send_result(self, message_id: int, result=None) -> None:
        self.results.append((message_id, result))

    def send_error(self, message_id: int, code: str, message: str) -> None:
        self.errors.append((message_id, code, message))

    def send_event(self, message_id: int, event: object) -> None:
        self.events.append((message_id, event))


class _Hass:
    def __init__(self, runtime: CodexBridgeRuntime) -> None:
        self.data = {DOMAIN: {DATA_ENTRIES: {runtime.entry_id: runtime}}}
        self.tasks: list[asyncio.Task[None]] = []

    def async_create_task(self, target) -> asyncio.Task[None]:
        task = asyncio.create_task(target)
        self.tasks.append(task)
        return task

    def async_create_background_task(
        self, target, _name, *, eager_start=True
    ) -> asyncio.Task[None]:
        return self.async_create_task(target)

    async def finish(self) -> None:
        await asyncio.gather(*self.tasks, return_exceptions=True)


def _runtime(*, queue_size: int = 256) -> tuple[CodexBridgeRuntime, EventBroker]:
    client = AsyncMock()
    broker = EventBroker(client, initial_cursor=0, queue_size=queue_size)
    return (
        CodexBridgeRuntime(
            "entry", "Codex", client, "supervisor", "a" * 32, 1, broker
        ),
        broker,
    )


async def test_v1_subscription_acknowledges_and_forwards_auth_without_a_chat() -> None:
    runtime, broker = _runtime()
    hass = _Hass(runtime)
    connection = _Connection()

    ws_subscribe_events(
        hass,
        connection,
        {
            "id": 5,
            "type": f"{DOMAIN}/subscribe_events",
            "after": 0,
            "scopes": ["auth"],
        },
    )
    await asyncio.sleep(0)
    broker._publish_event(_event(1))
    await asyncio.sleep(0)
    connection.subscriptions[5]()
    await hass.finish()

    assert connection.results == [(5, {"subscription_id": 5, "api_version": 1})]
    assert connection.events == [
        (
            5,
            {
                "type": "event",
                "event": {
                    "cursor": 1,
                    "event_id": "evt_1",
                    "scope": "auth",
                    "thread_id": None,
                    "event_type": "auth.updated",
                    "payload": {"state": "pending"},
                    "timestamp": "2026-07-13T12:00:00Z",
                },
            },
        )
    ]


async def test_singular_thread_filter_preserves_the_retiring_panel_contract() -> None:
    runtime, broker = _runtime()
    hass = _Hass(runtime)
    connection = _Connection()

    ws_subscribe_events(
        hass,
        connection,
        {
            "id": 6,
            "type": f"{DOMAIN}/subscribe_events",
            "after": 0,
            "thread_id": "thr_1",
        },
    )
    await asyncio.sleep(0)
    broker._publish_event(_event(1))
    broker._publish_event(_event(2, scope="thread", thread_id="thr_2"))
    broker._publish_event(_event(3, scope="thread", thread_id="thr_1"))
    await asyncio.sleep(0)
    connection.subscriptions[6]()
    await hass.finish()

    assert connection.events == [
        (
            6,
            {
                "event_id": "evt_3",
                "sequence": 3,
                "event_type": "thread.updated",
                "payload": {"state": "pending"},
                "timestamp": "2026-07-13T12:00:00Z",
            },
        )
    ]


async def test_v1_slow_websocket_receives_explicit_snapshot_signal() -> None:
    runtime, broker = _runtime(queue_size=1)
    hass = _Hass(runtime)
    connection = _Connection()

    ws_subscribe_events(
        hass,
        connection,
        {"id": 7, "type": f"{DOMAIN}/subscribe_events", "after": 0},
    )
    await asyncio.sleep(0)
    broker._publish_event(_event(1))
    broker._cursor = 2
    broker._publish_event(_event(2))
    await asyncio.sleep(0)
    await hass.finish()

    assert connection.events == [
        (
            7,
            {
                "type": "snapshot_required",
                "cursor": 2,
                "reason": "subscriber_overflow",
                "scope": "global",
            },
        )
    ]


async def test_legacy_panel_receives_cursor_advancing_snapshot_after_overflow() -> None:
    runtime, broker = _runtime(queue_size=1)
    hass = _Hass(runtime)
    connection = _Connection()

    ws_subscribe_events(
        hass,
        connection,
        {
            "id": 18,
            "type": f"{DOMAIN}/subscribe_events",
            "after": 0,
            "thread_id": "thr_1",
        },
    )
    await asyncio.sleep(0)
    broker._publish_event(_event(1, scope="thread", thread_id="thr_1"))
    broker._cursor = 2
    broker._publish_event(_event(2, scope="thread", thread_id="thr_1"))
    await asyncio.sleep(0)
    await hass.finish()

    assert connection.events == [
        (
            18,
            {
                "event_id": "snapshot_2",
                "sequence": 2,
                "event_type": "bridge.snapshot_required",
                "payload": {
                    "code": "snapshot_required",
                    "scope": "thread",
                    "thread_id": "thr_1",
                },
                "timestamp": "",
            },
        )
    ]


async def test_legacy_panel_receives_safe_error_when_broker_stops() -> None:
    runtime, broker = _runtime()
    hass = _Hass(runtime)
    connection = _Connection()

    ws_subscribe_events(
        hass,
        connection,
        {
            "id": 19,
            "type": f"{DOMAIN}/subscribe_events",
            "after": 0,
            "thread_id": "thr_1",
        },
    )
    await asyncio.sleep(0)
    broker._set_status(
        "authentication_failed", phase="stopped", retry_count=0, notify=True
    )
    await asyncio.sleep(0)
    await hass.finish()

    assert connection.events == [
        (
            19,
            {
                "event_type": "bridge.error",
                "payload": {
                    "code": "authentication_failed",
                    "error": "Bridge live updates stopped; polling will retry.",
                },
            },
        )
    ]


async def test_v1_get_events_returns_validated_batch_and_filters() -> None:
    runtime, _broker = _runtime()
    runtime.client.async_replay_events = AsyncMock(
        return_value={
            "events": [_event(1, scope="thread", thread_id="thr_1").as_dict()],
            "next_cursor": 1,
            "minimum_cursor": 0,
            "has_more": False,
            "heartbeat": False,
        }
    )
    hass = _Hass(runtime)
    connection = _Connection()

    ws_get_events(
        hass,
        connection,
        {
            "id": 8,
            "type": f"{DOMAIN}/get_events",
            "after": 0,
            "scopes": ["thread"],
            "thread_ids": ["thr_1"],
        },
    )
    await hass.finish()

    runtime.client.async_replay_events.assert_awaited_once_with(
        after=0, scopes=frozenset({"thread"}), thread_ids=frozenset({"thr_1"})
    )
    assert connection.results[0][1]["events"][0]["cursor"] == 1


async def test_singular_get_events_returns_legacy_list_for_current_panel() -> None:
    runtime, _broker = _runtime()
    runtime.client.async_replay_events = AsyncMock(
        return_value={
            "events": [_event(2, scope="thread", thread_id="thr_1").as_dict()],
            "next_cursor": 2,
            "minimum_cursor": 0,
            "has_more": False,
            "heartbeat": False,
        }
    )
    hass = _Hass(runtime)
    connection = _Connection()

    ws_get_events(
        hass,
        connection,
        {
            "id": 9,
            "type": f"{DOMAIN}/get_events",
            "after": 0,
            "thread_id": "thr_1",
        },
    )
    await hass.finish()

    assert connection.results[0][1] == [
        {
            "event_id": "evt_2",
            "sequence": 2,
            "event_type": "thread.updated",
            "payload": {"state": "pending"},
            "timestamp": "2026-07-13T12:00:00Z",
        }
    ]


async def test_event_status_and_config_never_expose_private_origin_or_errors() -> None:
    runtime, broker = _runtime()
    runtime.client.base_url = "http://secret.internal:8766"
    broker._status = {"state": "reconnecting", "phase": "wait", "retry_count": 2}
    hass = _Hass(runtime)
    connection = _Connection()

    ws_get_config(hass, connection, {"id": 10, "type": f"{DOMAIN}/get_config"})
    ws_get_event_status(
        hass, connection, {"id": 11, "type": f"{DOMAIN}/get_event_status"}
    )
    await hass.finish()

    assert connection.results == [
        (10, {"panel_title": "Codex", "connection_type": "supervisor", "api_version": 1}),
        (
            11,
            {"state": "reconnecting", "phase": "wait", "retry_count": 2, "cursor": 0},
        ),
    ]
    assert "secret.internal" not in repr(connection.results)


async def test_upstream_exception_details_are_not_sent_to_browser() -> None:
    runtime, _broker = _runtime()
    runtime.client.async_get_status = AsyncMock(
        side_effect=BridgeApiError("private-token-sentinel")
    )
    hass = _Hass(runtime)
    connection = _Connection()

    ws_get_status(hass, connection, {"id": 12, "type": f"{DOMAIN}/get_status"})
    await hass.finish()

    assert connection.errors == [(12, "bridge_error", "Bridge request failed")]
    assert "private-token-sentinel" not in repr(connection.errors)


async def test_answer_interaction_forwards_exact_bounded_values_contract() -> None:
    runtime, _broker = _runtime()
    runtime.client.async_answer_interaction = AsyncMock(return_value={"status": "answered"})
    hass = _Hass(runtime)
    connection = _Connection()
    message = {
        "id": 13,
        "type": f"{DOMAIN}/answer_interaction",
        "interaction_id": "int_1",
        "thread_id": "thr_1",
        "run_id": "run_1",
        "turn_id": "turn_1",
        "item_id": "item_1",
        "answers": [{"question_id": "question_1", "values": ["yes"]}],
        "client_request_id": "answer-1",
    }

    ws_answer_interaction(hass, connection, message)
    await hass.finish()

    runtime.client.async_answer_interaction.assert_awaited_once_with(
        "int_1",
        thread_id="thr_1",
        run_id="run_1",
        turn_id="turn_1",
        item_id="item_1",
        answers=[{"question_id": "question_1", "values": ["yes"]}],
        client_request_id="answer-1",
    )


async def test_explicit_unsubscribe_invokes_and_removes_subscription_callback() -> None:
    runtime, _broker = _runtime()
    hass = _Hass(runtime)
    connection = _Connection()
    called = 0

    def unsubscribe() -> None:
        nonlocal called
        called += 1

    connection.subscriptions[44] = unsubscribe
    ws_unsubscribe_events(
        hass,
        connection,
        {
            "id": 14,
            "type": f"{DOMAIN}/unsubscribe_events",
            "subscription_id": 44,
        },
    )
    await hass.finish()

    assert called == 1
    assert 44 not in connection.subscriptions
    assert connection.results == [(14, {"unsubscribed": True})]


async def test_compacted_journal_returns_snapshot_for_v1_and_advances_legacy_panel() -> None:
    runtime, _broker = _runtime()
    problem = ProblemRecord(
        status=410,
        code="event_cursor_expired",
        retryable=False,
        minimum_cursor=7,
        snapshot_required=True,
        snapshot_cursor=11,
        scope="thread",
        thread_id="thr_1",
    )
    runtime.client.async_replay_events = AsyncMock(
        side_effect=[
            BridgeApiGoneError(problem=problem),
            BridgeApiGoneError(problem=problem),
        ]
    )
    hass = _Hass(runtime)
    connection = _Connection()

    ws_get_events(
        hass,
        connection,
        {
            "id": 15,
            "type": f"{DOMAIN}/get_events",
            "after": 0,
            "scopes": ["thread"],
            "thread_ids": ["thr_1"],
        },
    )
    ws_get_events(
        hass,
        connection,
        {
            "id": 16,
            "type": f"{DOMAIN}/get_events",
            "after": 0,
            "thread_id": "thr_1",
        },
    )
    await hass.finish()

    assert connection.results[0][1]["snapshot_required"] == {
        "cursor": 11,
        "minimum_cursor": 7,
        "scope": "thread",
        "thread_id": "thr_1",
    }
    assert connection.results[1][1] == [
        {
            "event_id": "snapshot_11",
            "sequence": 11,
            "event_type": "bridge.snapshot_required",
            "payload": {
                "code": "snapshot_required",
                "scope": "thread",
                "thread_id": "thr_1",
            },
            "timestamp": "",
        }
    ]


async def test_thread_ids_without_thread_scope_are_rejected_before_subscribing() -> None:
    runtime, broker = _runtime()
    hass = _Hass(runtime)
    connection = _Connection()

    ws_subscribe_events(
        hass,
        connection,
        {
            "id": 17,
            "type": f"{DOMAIN}/subscribe_events",
            "after": 0,
            "scopes": ["auth"],
            "thread_ids": ["thr_1"],
        },
    )
    await hass.finish()

    assert connection.errors == [
        (17, "invalid_event_filter", "Event subscription is invalid")
    ]
    assert not broker._subscribers
