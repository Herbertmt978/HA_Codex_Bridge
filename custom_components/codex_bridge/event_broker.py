"""One bounded, config-entry-owned consumer of the Bridge v1 event stream."""

from __future__ import annotations

import asyncio
import json
import math
from collections import deque
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from .bridge_api import (
    BridgeApiAuthError,
    BridgeApiClient,
    BridgeApiConnectionError,
    BridgeApiError,
    BridgeApiGoneError,
)
from .const import BRIDGE_EVENT_CURSOR_MAX
from .protocol import EndpointError, ProblemRecord, validate_bridge_identifier


EVENT_SCOPES = frozenset({"auth", "runtime", "thread"})
MAX_BATCH_EVENTS = 256
MAX_SUBSCRIBERS = 32
MAX_HISTORY_BYTES = 8 * 1024 * 1024
MAX_SUBSCRIBER_BYTES = 8 * 1024 * 1024
_SAFE_PAYLOAD_NODES = 1024
_CLOSE = object()

TaskFactory = Callable[
    [Coroutine[Any, Any, None], str],
    asyncio.Task[None],
]


class CursorStore(Protocol):
    """Small persistence seam; HA's Store deliberately satisfies this shape."""

    async def async_load(self) -> Mapping[str, object] | None: ...

    async def async_save(self, data: Mapping[str, object]) -> None: ...


def _default_task_factory(
    target: Coroutine[Any, Any, None], name: str
) -> asyncio.Task[None]:
    return asyncio.create_task(target, name=name)


def default_reconnect_delay(attempt: int) -> float:
    """Return a capped exponential delay without unbounded integer growth."""

    bounded_attempt = max(1, min(attempt, 7))
    return min(30.0, 0.5 * (2 ** (bounded_attempt - 1)))


def _safe_payload(
    value: object,
    *,
    depth: int = 0,
    budget: list[int] | None = None,
) -> Any:
    """Keep event data useful while bounding data supplied by the private App."""

    if budget is None:
        budget = [_SAFE_PAYLOAD_NODES]
    if depth > 4 or budget[0] <= 0:
        return None
    budget[0] -= 1
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        return value if -(2**63) <= value <= 2**63 - 1 else None
    if type(value) is float:
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:4096]
    if isinstance(value, Mapping):
        return {
            key[:128]: _safe_payload(item, depth=depth + 1, budget=budget)
            for key, item in list(value.items())[:64]
            if isinstance(key, str)
        }
    if isinstance(value, list):
        return [
            _safe_payload(item, depth=depth + 1, budget=budget)
            for item in value[:64]
        ]
    return None


@dataclass(frozen=True, slots=True)
class EventRecord:
    """Validated, bounded event forwarded to trusted HA WebSocket clients."""

    cursor: int
    event_id: str
    scope: str
    thread_id: str | None
    event_type: str
    payload: Mapping[str, Any]
    timestamp: str
    estimated_bytes: int = field(repr=False, compare=False)

    @classmethod
    def from_payload(cls, value: object) -> "EventRecord":
        if not isinstance(value, Mapping):
            raise EndpointError("event_invalid")
        cursor = value.get("cursor")
        event_id = value.get("event_id")
        scope = value.get("scope")
        thread_id = value.get("thread_id")
        event_type = value.get("event_type")
        timestamp = value.get("timestamp")
        if (
            type(cursor) is not int
            or not 1 <= cursor <= BRIDGE_EVENT_CURSOR_MAX
            or not isinstance(scope, str)
            or scope not in EVENT_SCOPES
            or not isinstance(event_type, str)
            or not 1 <= len(event_type) <= 128
            or any(
                not (character.isalnum() or character in "._-")
                for character in event_type
            )
            or not isinstance(timestamp, str)
            or not 1 <= len(timestamp) <= 64
        ):
            raise EndpointError("event_invalid")
        try:
            event_id = validate_bridge_identifier(event_id)
            if scope == "thread":
                thread_id = validate_bridge_identifier(thread_id)
            elif thread_id is not None:
                raise EndpointError("event_invalid")
        except EndpointError:
            raise EndpointError("event_invalid") from None
        payload = _safe_payload(value.get("payload", {}))
        if not isinstance(payload, Mapping):
            payload = {}
        estimated_bytes = (
            len(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            + len(event_id)
            + len(event_type)
            + len(timestamp)
            + (len(thread_id) if thread_id is not None else 0)
            + 128
        )
        return cls(
            cursor,
            event_id,
            scope,
            thread_id,
            event_type,
            payload,
            timestamp,
            estimated_bytes,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "cursor": self.cursor,
            "event_id": self.event_id,
            "scope": self.scope,
            "thread_id": self.thread_id,
            "event_type": self.event_type,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True, slots=True)
class EventBatch:
    """Validated global event-journal page."""

    events: tuple[EventRecord, ...]
    next_cursor: int
    minimum_cursor: int
    has_more: bool
    heartbeat: bool

    @classmethod
    def from_payload(cls, value: object) -> "EventBatch":
        if not isinstance(value, Mapping):
            raise EndpointError("event_batch_invalid")
        raw_events = value.get("events")
        next_cursor = value.get("next_cursor")
        minimum_cursor = value.get("minimum_cursor")
        has_more = value.get("has_more", False)
        heartbeat = value.get("heartbeat", False)
        if (
            not isinstance(raw_events, list)
            or len(raw_events) > MAX_BATCH_EVENTS
            or type(next_cursor) is not int
            or not 0 <= next_cursor <= BRIDGE_EVENT_CURSOR_MAX
            or type(minimum_cursor) is not int
            or not 0 <= minimum_cursor <= BRIDGE_EVENT_CURSOR_MAX
            or minimum_cursor > next_cursor
            or type(has_more) is not bool
            or type(heartbeat) is not bool
        ):
            raise EndpointError("event_batch_invalid")
        events = tuple(EventRecord.from_payload(event) for event in raw_events)
        if any(
            current.cursor <= prior.cursor for prior, current in zip(events, events[1:])
        ):
            raise EndpointError("event_batch_invalid")
        if events and (
            next_cursor < events[-1].cursor or events[0].cursor <= minimum_cursor
        ):
            raise EndpointError("event_batch_invalid")
        if heartbeat and (events or has_more):
            raise EndpointError("event_batch_invalid")
        return cls(events, next_cursor, minimum_cursor, has_more, heartbeat)


class EventSubscription:
    """A bounded, filterable subscription owned by one WebSocket connection."""

    def __init__(
        self,
        broker: "EventBroker",
        queue: asyncio.Queue[tuple[dict[str, Any], int] | object],
        *,
        after: int,
        scopes: frozenset[str] | None,
        thread_ids: frozenset[str] | None,
        maximum_bytes: int,
    ) -> None:
        self._broker = broker
        self._queue = queue
        self._after = after
        self._scopes = scopes
        self._thread_ids = thread_ids
        self._maximum_bytes = maximum_bytes
        self._queued_bytes = 0
        self._close_after_delivery = False
        self.closed = False

    def matches(self, event: EventRecord) -> bool:
        return (
            event.cursor > self._after
            and (self._scopes is None or event.scope in self._scopes)
            and (
                self._thread_ids is None
                or event.scope != "thread"
                or event.thread_id in self._thread_ids
            )
        )

    async def get(self) -> dict[str, Any]:
        if self.closed:
            raise asyncio.CancelledError
        item = await self._queue.get()
        if item is _CLOSE:
            self.closed = True
            raise asyncio.CancelledError
        assert isinstance(item, tuple)
        envelope, size = item
        self._queued_bytes = max(0, self._queued_bytes - size)
        if self._close_after_delivery:
            self._close_after_delivery = False
            self.closed = True
        return envelope

    @staticmethod
    def _envelope_size(envelope: dict[str, Any]) -> int:
        event = envelope.get("event")
        if isinstance(event, EventRecord):
            return event.estimated_bytes + 64
        return 512

    def _drain(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()
        self._queued_bytes = 0

    def _put(self, envelope: dict[str, Any]) -> bool:
        size = self._envelope_size(envelope)
        if (
            self.closed
            or self._close_after_delivery
            or self._queue.full()
            or self._queued_bytes + size > self._maximum_bytes
        ):
            return False
        self._queue.put_nowait((envelope, size))
        self._queued_bytes += size
        return True

    def _replace_then_close(self, envelope: dict[str, Any]) -> None:
        if self.closed or self._close_after_delivery:
            return
        self._broker._remove_subscription(self)
        self._drain()
        size = self._envelope_size(envelope)
        self._queue.put_nowait((envelope, size))
        self._queued_bytes = size
        self._close_after_delivery = True

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._close_after_delivery = False
        self._broker._remove_subscription(self)
        self._drain()
        self._queue.put_nowait(_CLOSE)


class EventBroker:
    """Serial replay-then-wait v1 stream with bounded fan-out and recovery."""

    def __init__(
        self,
        client: BridgeApiClient,
        *,
        store: CursorStore | None = None,
        initial_cursor: int | None = None,
        queue_size: int = MAX_BATCH_EVENTS,
        history_size: int = MAX_BATCH_EVENTS,
        queue_bytes: int = MAX_SUBSCRIBER_BYTES,
        history_bytes: int = MAX_HISTORY_BYTES,
        reconnect_delay: Callable[[int], float] | None = None,
        task_factory: TaskFactory | None = None,
    ) -> None:
        if initial_cursor is not None and (
            type(initial_cursor) is not int
            or not 0 <= initial_cursor <= BRIDGE_EVENT_CURSOR_MAX
        ):
            raise ValueError("initial cursor is invalid")
        self._client = client
        self._store = store
        self._cursor = initial_cursor if initial_cursor is not None else 0
        self._persisted_cursor = self._cursor if initial_cursor is not None else None
        self._load_cursor = initial_cursor is None
        self._queue_size = max(1, min(queue_size, MAX_BATCH_EVENTS))
        self._history_size = max(1, min(history_size, 1024))
        self._history_max_bytes = max(1024, min(history_bytes, 64 * 1024 * 1024))
        self._queue_max_bytes = max(1024, min(queue_bytes, 64 * 1024 * 1024))
        self._history: deque[EventRecord] = deque()
        self._history_bytes = 0
        self._reconnect_delay = reconnect_delay or default_reconnect_delay
        self._task_factory = task_factory or _default_task_factory
        self._subscribers: set[EventSubscription] = set()
        self._task: asyncio.Task[None] | None = None
        self._starting = False
        self._start_complete = asyncio.Event()
        self._start_complete.set()
        self._closed = False
        self._status: dict[str, object] = {
            "state": "idle",
            "phase": "idle",
            "retry_count": 0,
        }

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def connection_status(self) -> Mapping[str, object]:
        return {**self._status, "cursor": self._cursor}

    async def async_start(self) -> None:
        if self._closed or self._task is not None:
            return
        if self._starting:
            await self._start_complete.wait()
            return
        self._starting = True
        self._start_complete.clear()
        try:
            if self._load_cursor and self._store is not None:
                try:
                    saved = await self._store.async_load()
                    cursor = saved.get("cursor") if isinstance(saved, Mapping) else None
                    if type(cursor) is int and 0 <= cursor <= BRIDGE_EVENT_CURSOR_MAX:
                        self._cursor = cursor
                        self._persisted_cursor = cursor
                except Exception:  # Store errors must not prevent a Bridge reconnect.
                    pass
            if self._closed or self._task is not None:
                return
            self._set_status("connecting", phase="replay", retry_count=0)
            target = self._run()
            try:
                self._task = self._task_factory(
                    target, "codex_bridge_event_broker"
                )
            except BaseException:
                target.close()
                self._set_status("failed", phase="stopped", retry_count=0)
                raise
        finally:
            self._starting = False
            self._start_complete.set()

    async def async_close(self) -> None:
        self._closed = True
        if self._starting:
            await self._start_complete.wait()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._persist_cursor(force=True)
        for subscription in tuple(self._subscribers):
            subscription.close()
        self._set_status("stopped", phase="stopped", retry_count=0)

    def subscribe(
        self,
        *,
        after: int = 0,
        scopes: set[str] | frozenset[str] | None = None,
        thread_ids: set[str] | frozenset[str] | None = None,
    ) -> EventSubscription:
        if self._closed:
            raise RuntimeError("broker is stopped")
        if type(after) is not int or not 0 <= after <= BRIDGE_EVENT_CURSOR_MAX:
            raise ValueError("after is invalid")
        normalized_scopes = None if scopes is None else frozenset(scopes)
        if normalized_scopes is not None and (
            not normalized_scopes or not normalized_scopes <= EVENT_SCOPES
        ):
            raise ValueError("scope is invalid")
        normalized_threads = None
        if thread_ids is not None:
            normalized_threads = frozenset(
                validate_bridge_identifier(item) for item in thread_ids
            )
            if (
                not normalized_threads
                or len(normalized_threads) > 64
                or (normalized_scopes is not None and "thread" not in normalized_scopes)
            ):
                raise ValueError("thread filter is invalid")
        if len(self._subscribers) >= MAX_SUBSCRIBERS:
            raise RuntimeError("subscription capacity exhausted")
        subscription = EventSubscription(
            self,
            asyncio.Queue(maxsize=self._queue_size),
            after=after,
            scopes=normalized_scopes,
            thread_ids=normalized_threads,
            maximum_bytes=self._queue_max_bytes,
        )
        self._subscribers.add(subscription)
        self._replay_history(subscription)
        return subscription

    def _replay_history(self, subscription: EventSubscription) -> None:
        if subscription._after >= self._cursor:
            return
        if (
            not self._history
            or self._history[-1].cursor != self._cursor
            or subscription._after < self._history[0].cursor - 1
        ):
            subscription._replace_then_close(
                self._snapshot_envelope(subscription, reason="cursor_gap")
            )
            return
        matching = [event for event in self._history if subscription.matches(event)]
        if len(matching) > self._queue_size:
            subscription._replace_then_close(
                self._snapshot_envelope(subscription, reason="subscriber_overflow")
            )
            return
        for event in matching:
            if not subscription._put({"type": "event", "event": event}):
                subscription._replace_then_close(
                    self._snapshot_envelope(
                        subscription, reason="subscriber_overflow"
                    )
                )
                break

    def _clear_history(self) -> None:
        self._history.clear()
        self._history_bytes = 0

    def _append_history(self, event: EventRecord) -> None:
        if event.estimated_bytes > self._history_max_bytes:
            self._clear_history()
            return
        while self._history and (
            len(self._history) >= self._history_size
            or self._history_bytes + event.estimated_bytes > self._history_max_bytes
        ):
            self._history_bytes -= self._history.popleft().estimated_bytes
        self._history.append(event)
        self._history_bytes += event.estimated_bytes

    def _snapshot_envelope(
        self,
        subscription: EventSubscription | None = None,
        *,
        reason: str,
        problem: ProblemRecord | None = None,
    ) -> dict[str, Any]:
        envelope: dict[str, Any] = {
            "type": "snapshot_required",
            "cursor": self._cursor,
            "reason": reason,
        }
        if problem is not None and problem.scope is not None:
            envelope["scope"] = problem.scope
            if problem.thread_id is not None:
                envelope["thread_id"] = problem.thread_id
            return envelope
        if subscription is not None:
            if subscription._scopes is not None and len(subscription._scopes) == 1:
                envelope["scope"] = next(iter(subscription._scopes))
            else:
                envelope["scope"] = "global"
            if (
                envelope["scope"] == "thread"
                and subscription._thread_ids is not None
                and len(subscription._thread_ids) == 1
            ):
                envelope["thread_id"] = next(iter(subscription._thread_ids))
        else:
            envelope["scope"] = "global"
        return envelope

    def _remove_subscription(self, subscription: EventSubscription) -> None:
        self._subscribers.discard(subscription)

    async def _persist_cursor(self, *, force: bool = False) -> None:
        if (
            self._store is not None
            and (force or self._persisted_cursor != self._cursor)
        ):
            try:
                await self._store.async_save({"cursor": self._cursor})
                self._persisted_cursor = self._cursor
            except Exception:
                pass

    def _publish_event(self, event: EventRecord) -> None:
        envelope = {"type": "event", "event": event}
        for subscription in tuple(self._subscribers):
            if subscription.closed or not subscription.matches(event):
                continue
            if not subscription._put(envelope):
                subscription._replace_then_close(
                    self._snapshot_envelope(
                        subscription, reason="subscriber_overflow"
                    )
                )

    def _publish_envelope(self, envelope: dict[str, Any]) -> None:
        for subscription in tuple(self._subscribers):
            if subscription.closed:
                continue
            if not subscription._put(envelope):
                subscription._replace_then_close(
                    self._snapshot_envelope(
                        subscription, reason="subscriber_overflow"
                    )
                )

    def _publish_snapshot(self, envelope: dict[str, Any]) -> None:
        for subscription in tuple(self._subscribers):
            subscription._replace_then_close(dict(envelope))

    async def _consume(self, payload: object) -> bool:
        batch = EventBatch.from_payload(payload)
        fresh_events = tuple(
            event for event in batch.events if event.cursor > self._cursor
        )
        if (
            self._cursor < batch.minimum_cursor
            or (
                fresh_events
                and fresh_events[0].cursor > self._cursor + 1
            )
            or (
                not fresh_events
                and batch.next_cursor > self._cursor
            )
        ):
            self._cursor = max(
                self._cursor, batch.minimum_cursor, batch.next_cursor
            )
            self._clear_history()
            await self._persist_cursor()
            self._publish_snapshot(
                self._snapshot_envelope(reason="cursor_gap")
            )
            return batch.has_more
        for event in fresh_events:
            if event.cursor > self._cursor + 1:
                self._cursor = max(self._cursor, batch.next_cursor)
                self._clear_history()
                await self._persist_cursor()
                self._publish_snapshot(
                    self._snapshot_envelope(reason="cursor_gap")
                )
                return batch.has_more
            self._cursor = event.cursor
            self._append_history(event)
            self._publish_event(event)
        self._cursor = max(self._cursor, batch.next_cursor)
        await self._persist_cursor()
        if batch.heartbeat:
            self._publish_envelope({"type": "heartbeat", "cursor": self._cursor})
        return batch.has_more

    async def _recover_snapshot(self, problem: ProblemRecord | None) -> None:
        if (
            problem is None
            or not problem.snapshot_required
            or problem.snapshot_cursor is None
        ):
            raise BridgeApiGoneError(problem=problem)
        self._cursor = max(
            self._cursor,
            problem.snapshot_cursor,
            problem.minimum_cursor or 0,
        )
        self._clear_history()
        await self._persist_cursor()
        self._publish_snapshot(
            self._snapshot_envelope(reason="journal_compacted", problem=problem)
        )

    def _set_status(
        self,
        state: str,
        *,
        phase: str,
        retry_count: int,
        notify: bool = False,
    ) -> None:
        status = {
            "state": state,
            "phase": phase,
            "retry_count": min(max(retry_count, 0), 16),
        }
        changed = status != self._status
        self._status = status
        if notify and changed:
            envelope = {
                "type": "stream_status",
                **status,
                "cursor": self._cursor,
            }
            if state in {
                "authentication_failed",
                "failed",
                "protocol_error",
                "upstream_error",
            }:
                self._publish_snapshot(envelope)
            else:
                self._publish_envelope(envelope)

    async def _run(self) -> None:
        retry = 0
        replay = True
        while not self._closed:
            phase = "replay" if replay else "wait"
            try:
                if replay:
                    payload = await self._client.async_replay_events(after=self._cursor)
                else:
                    payload = await self._client.async_wait_events(after=self._cursor)
                replay = await self._consume(payload)
                self._set_status(
                    "connected", phase="replay" if replay else "wait", retry_count=0,
                    notify=retry > 0,
                )
                retry = 0
                # A mocked or immediately-resolved wait must not monopolise HA's loop.
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                raise
            except BridgeApiGoneError as error:
                try:
                    await self._recover_snapshot(error.problem)
                except BridgeApiError:
                    self._set_status(
                        "protocol_error", phase="stopped", retry_count=retry, notify=True
                    )
                    return
                replay = True
                retry = 0
            except BridgeApiAuthError:
                self._set_status(
                    "authentication_failed", phase="stopped", retry_count=retry, notify=True
                )
                return
            except BridgeApiConnectionError:
                retry += 1
                replay = True
                self._set_status(
                    "reconnecting", phase=phase, retry_count=retry, notify=True
                )
                await asyncio.sleep(self._reconnect_delay(retry))
            except BridgeApiError as error:
                if not error.retryable:
                    self._set_status(
                        "upstream_error", phase="stopped", retry_count=retry, notify=True
                    )
                    return
                retry += 1
                replay = True
                self._set_status(
                    "reconnecting", phase=phase, retry_count=retry, notify=True
                )
                await asyncio.sleep(self._reconnect_delay(retry))
            except EndpointError:
                self._set_status(
                    "protocol_error", phase="stopped", retry_count=retry, notify=True
                )
                return
            except Exception:
                self._set_status(
                    "failed", phase="stopped", retry_count=retry, notify=True
                )
                return
