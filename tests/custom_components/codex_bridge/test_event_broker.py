"""Tests for the single-consumer Bridge event broker."""

from __future__ import annotations

import asyncio
from collections import deque
from unittest.mock import AsyncMock

import pytest

from custom_components.codex_bridge.bridge_api import (
    BridgeApiAuthError,
    BridgeApiConnectionError,
    BridgeApiError,
    BridgeApiGoneError,
)
from custom_components.codex_bridge.event_broker import (
    EventBroker,
    default_reconnect_delay,
)
from custom_components.codex_bridge.protocol import ProblemRecord


def _batch(
    *events: dict,
    next_cursor: int | None = None,
    minimum_cursor: int = 0,
    has_more: bool = False,
    heartbeat: bool = False,
) -> dict:
    if next_cursor is None:
        next_cursor = events[-1]["cursor"] if events else 0
    return {
        "events": list(events),
        "next_cursor": next_cursor,
        "minimum_cursor": minimum_cursor,
        "has_more": has_more,
        "heartbeat": heartbeat,
    }


def _event(
    cursor: int,
    *,
    scope: str = "thread",
    thread_id: str | None = "thr_1",
    event_type: str | None = None,
) -> dict:
    return {
        "cursor": cursor,
        "event_id": f"evt_{cursor}",
        "scope": scope,
        "thread_id": thread_id,
        "event_type": event_type or f"{scope}.updated",
        "payload": {"cursor": cursor},
        "timestamp": "2026-07-13T12:00:00Z",
    }


class _ScriptedClient:
    def __init__(self, *, replay=(), wait=()) -> None:
        self.replay = deque(replay)
        self.wait = deque(wait)
        self.replay_after: list[int] = []
        self.wait_after: list[int] = []
        self.blocked = asyncio.Event()

    async def _next(self, queue: deque, *, after: int):
        if queue:
            value = queue.popleft()
            if isinstance(value, BaseException):
                raise value
            return value
        self.blocked.set()
        await asyncio.Event().wait()

    async def async_replay_events(self, *, after: int):
        self.replay_after.append(after)
        return await self._next(self.replay, after=after)

    async def async_wait_events(self, *, after: int):
        self.wait_after.append(after)
        return await self._next(self.wait, after=after)


async def test_two_subscribers_share_one_upstream_and_receive_scoped_exactly_once_events() -> None:
    client = _ScriptedClient(
        replay=[
            _batch(
                _event(1, scope="auth", thread_id=None),
                _event(2, scope="runtime", thread_id=None),
                _event(3, thread_id="thr_1"),
                _event(4, thread_id="thr_2"),
            )
        ],
        wait=[_batch(_event(4, thread_id="thr_2"), _event(5, thread_id="thr_1"))],
    )
    broker = EventBroker(client, initial_cursor=0, reconnect_delay=lambda _: 0)
    auth = broker.subscribe(after=0, scopes={"auth"})
    thread = broker.subscribe(after=0, scopes={"thread"}, thread_ids={"thr_1"})

    await broker.async_start()
    assert (await auth.get())["event"].cursor == 1
    assert (await thread.get())["event"].cursor == 3
    assert (await thread.get())["event"].cursor == 5
    await asyncio.wait_for(client.blocked.wait(), 1)
    await broker.async_close()

    assert client.replay_after == [0]
    assert client.wait_after == [4, 5]


async def test_late_subscriber_replays_bounded_history_before_live_events() -> None:
    broker = EventBroker(AsyncMock(), initial_cursor=0)
    await broker._consume(_batch(_event(1), _event(2), _event(3)))

    subscription = broker.subscribe(after=1, scopes={"thread"})

    assert [(await subscription.get())["event"].cursor for _ in range(2)] == [2, 3]
    await broker.async_close()


async def test_late_subscriber_gets_snapshot_when_cursor_precedes_history() -> None:
    broker = EventBroker(AsyncMock(), initial_cursor=0, history_size=2)
    await broker._consume(_batch(_event(1), _event(2), _event(3)))

    subscription = broker.subscribe(after=0)

    assert await subscription.get() == {
        "type": "snapshot_required",
        "cursor": 3,
        "reason": "cursor_gap",
        "scope": "global",
    }
    assert subscription.closed
    await broker.async_close()


async def test_thread_filter_preserves_auth_and_runtime_when_scope_is_unfiltered() -> None:
    broker = EventBroker(AsyncMock(), initial_cursor=0)
    subscription = broker.subscribe(after=0, thread_ids={"thr_1"})

    await broker._consume(
        _batch(
            _event(1, scope="auth", thread_id=None),
            _event(2, scope="runtime", thread_id=None),
            _event(3, thread_id="thr_1"),
            _event(4, thread_id="thr_2"),
        )
    )

    assert [(await subscription.get())["event"].cursor for _ in range(3)] == [1, 2, 3]
    await broker.async_close()


async def test_cursor_load_and_save_resume_the_single_global_stream() -> None:
    store = AsyncMock()
    store.async_load.return_value = {"cursor": 4, "events": "must not be loaded"}
    client = _ScriptedClient(replay=[_batch(_event(5))])
    broker = EventBroker(client, store=store)

    await broker.async_start()
    await asyncio.wait_for(client.blocked.wait(), 1)
    await broker.async_close()

    assert client.replay_after == [4]
    assert {"cursor": 5} in [call.args[0] for call in store.async_save.await_args_list]


async def test_heartbeat_is_forwarded_without_rewriting_an_unchanged_cursor() -> None:
    store = AsyncMock()
    store.async_load.return_value = {"cursor": 0}
    client = _ScriptedClient(
        replay=[_batch(next_cursor=0)],
        wait=[_batch(next_cursor=0, heartbeat=True)],
    )
    broker = EventBroker(client, store=store)
    subscription = broker.subscribe(after=0)

    await broker.async_start()
    assert await subscription.get() == {"type": "heartbeat", "cursor": 0}
    await asyncio.wait_for(client.blocked.wait(), 1)
    assert store.async_save.await_count == 0
    await broker.async_close()


async def test_remote_expired_cursor_emits_snapshot_and_never_regresses_cursor() -> None:
    problem = ProblemRecord(
        status=410,
        code="event_cursor_expired",
        retryable=False,
        minimum_cursor=2,
        snapshot_required=True,
        snapshot_cursor=4,
        scope="global",
    )
    client = _ScriptedClient(replay=[BridgeApiGoneError(problem=problem)])
    broker = EventBroker(client, initial_cursor=5, reconnect_delay=lambda _: 0)
    subscription = broker.subscribe(after=5)

    await broker.async_start()
    assert await subscription.get() == {
        "type": "snapshot_required",
        "cursor": 5,
        "reason": "journal_compacted",
        "scope": "global",
    }
    await broker.async_close()


async def test_unreported_minimum_cursor_gap_forces_snapshot_recovery() -> None:
    broker = EventBroker(AsyncMock(), initial_cursor=1)
    subscription = broker.subscribe(after=1)

    await broker._consume(_batch(next_cursor=4, minimum_cursor=4))

    assert (await subscription.get())["reason"] == "cursor_gap"
    assert subscription.closed
    assert broker.connection_status["cursor"] == 4
    await broker.async_close()


async def test_slow_subscriber_gets_resync_signal_without_stopping_fast_subscriber() -> None:
    broker = EventBroker(AsyncMock(), queue_size=1, initial_cursor=0)
    slow = broker.subscribe(after=0)
    fast = broker.subscribe(after=0)

    await broker._consume(_batch(_event(1)))
    assert (await fast.get())["event"].cursor == 1
    await broker._consume(_batch(_event(2)))

    assert await slow.get() == {
        "type": "snapshot_required",
        "cursor": 2,
        "reason": "subscriber_overflow",
        "scope": "global",
    }
    assert slow.closed
    assert (await fast.get())["event"].cursor == 2
    await broker.async_close()


async def test_subscriber_and_replay_history_are_bounded_by_bytes_not_only_count() -> None:
    first = _event(1)
    second = _event(2)
    first["payload"] = {"text": "a" * 900}
    second["payload"] = {"text": "b" * 900}
    broker = EventBroker(
        AsyncMock(),
        initial_cursor=0,
        queue_bytes=1024,
        history_bytes=1024,
    )
    subscription = broker.subscribe(after=0)

    await broker._consume(_batch(first, second))

    assert (await subscription.get())["reason"] == "subscriber_overflow"
    late = broker.subscribe(after=0)
    assert (await late.get())["reason"] == "cursor_gap"
    await broker.async_close()


def test_default_reconnect_delay_is_exponential_and_capped() -> None:
    assert [default_reconnect_delay(attempt) for attempt in range(1, 10)] == [
        0.5,
        1.0,
        2.0,
        4.0,
        8.0,
        16.0,
        30.0,
        30.0,
        30.0,
    ]


async def test_retryable_failures_reconnect_and_expose_only_safe_status() -> None:
    delays: list[int] = []
    client = _ScriptedClient(
        replay=[BridgeApiConnectionError(), BridgeApiConnectionError(), _batch()]
    )
    broker = EventBroker(
        client,
        initial_cursor=0,
        reconnect_delay=lambda attempt: delays.append(attempt) or 0,
    )
    subscription = broker.subscribe(after=0)

    await broker.async_start()
    statuses = [await subscription.get() for _ in range(3)]
    await asyncio.wait_for(client.blocked.wait(), 1)

    assert [item["state"] for item in statuses] == [
        "reconnecting",
        "reconnecting",
        "connected",
    ]
    assert broker.connection_status == {
        "state": "connected",
        "phase": "wait",
        "retry_count": 0,
        "cursor": 0,
    }
    assert delays == [1, 2]
    await broker.async_close()


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (BridgeApiAuthError(), "authentication_failed"),
        (BridgeApiError("not_retryable"), "upstream_error"),
    ],
)
async def test_nonretryable_upstream_failures_stop_with_safe_status(error, state) -> None:
    client = _ScriptedClient(replay=[error])
    broker = EventBroker(client, initial_cursor=0)
    subscription = broker.subscribe(after=0)

    await broker.async_start()

    assert (await subscription.get())["state"] == state
    assert broker.connection_status["state"] == state
    assert client.replay_after == [0]
    await broker.async_close()


async def test_concurrent_start_and_close_cannot_spawn_a_consumer_after_unload() -> None:
    class _BlockingStore:
        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def async_load(self):
            self.entered.set()
            await self.release.wait()
            return {"cursor": 9}

        async def async_save(self, _data):
            return None

    store = _BlockingStore()
    client = AsyncMock()
    broker = EventBroker(client, store=store)
    first_start = asyncio.create_task(broker.async_start())
    await store.entered.wait()
    second_start = asyncio.create_task(broker.async_start())
    close = asyncio.create_task(broker.async_close())

    store.release.set()
    await asyncio.gather(first_start, second_start, close)

    client.async_replay_events.assert_not_awaited()
    assert broker.closed
    assert broker.connection_status["state"] == "stopped"


async def test_unsubscribe_and_close_release_waiters_and_the_single_long_poll() -> None:
    client = _ScriptedClient(replay=[_batch()])
    broker = EventBroker(client, initial_cursor=0)
    subscription = broker.subscribe(after=0)
    await broker.async_start()
    await asyncio.wait_for(client.blocked.wait(), 1)

    subscription.close()
    with pytest.raises(asyncio.CancelledError):
        await subscription.get()
    await broker.async_close()

    assert broker.closed
    assert broker.connection_status["state"] == "stopped"
