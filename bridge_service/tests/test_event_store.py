import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest

from codex_bridge_service.event_store import (
    BridgeEventStore,
    DurableOutbox,
    EventDraft,
    EventStoreAdmissionError,
    EventCursorExpiredError,
    EventPayloadTooLargeError,
    EventStoreCapacityError,
    EventStoreError,
    EventWaitCapacityError,
    OperationKeyExpiredError,
    OperationKeyConflictError,
    ThreadEventSequenceExpiredError,
)


@pytest.fixture
def event_store_factory(tmp_path):
    stores: list[BridgeEventStore] = []

    def create(name: str = "events.sqlite3", **kwargs: object) -> BridgeEventStore:
        store = BridgeEventStore(tmp_path / name, **kwargs)
        stores.append(store)
        return store

    yield create

    for store in reversed(stores):
        store.close()


def test_append_assigns_one_global_monotonic_cursor_across_scopes(
    event_store_factory,
) -> None:
    store = event_store_factory()

    records = [
        store.append(
            operation_key="auth:1",
            scope="auth",
            event_type="auth.updated",
            payload={"revision": 1},
        ),
        store.append(
            operation_key="thread-a:1",
            scope="thread",
            thread_id="thr_a",
            event_type="message.created",
            payload={"text": "hello"},
        ),
        store.append(
            operation_key="runtime:1",
            scope="runtime",
            event_type="run.queued",
            payload={"run_id": "run_1"},
        ),
    ]

    assert [record.cursor for record in records] == [1, 2, 3]
    assert [event.cursor for event in store.replay(after_cursor=0).events] == [
        1,
        2,
        3,
    ]


def test_public_event_projection_is_applied_before_persistence(
    event_store_factory,
    tmp_path,
) -> None:
    store = event_store_factory()
    private_root = str(tmp_path / "private-root")
    records = [
        store.append(
            operation_key="auth:projection",
            scope="auth",
            event_type="auth.status_changed",
            payload={
                "revision": 2,
                "state": "login_running",
                "busy": True,
                "auth_required": True,
                "auth_mode": "chatgpt",
                "plan_type": "pro",
                "updated_at": "2026-07-13T12:00:00Z",
                "message": "private auth details",
                "verification_uri": "https://example.invalid/device",
                "login_url": "https://example.invalid/device",
                "user_code": "SECRET-CODE",
                "output_tail": ["SECRET-CODE"],
            },
        ),
        store.append(
            operation_key="thread:created:projection",
            scope="thread",
            thread_id="thr_projection",
            event_type="thread.created",
            payload={
                "title": "Projection",
                "workspace_id": "ws_projection",
                "workspace_path": private_root,
            },
        ),
        store.append(
            operation_key="thread:attachment:projection",
            scope="thread",
            thread_id="thr_projection",
            event_type="attachment.added",
            payload={
                "attachment_id": "att_projection",
                "filename": "notes.txt",
                "relative_path": "notes.txt",
                "stored_path": private_root,
                "size_bytes": 4,
                "sha256": "a" * 64,
            },
        ),
        store.append(
            operation_key="thread:artifact:projection",
            scope="thread",
            thread_id="thr_projection",
            event_type="artifact.added",
            payload={
                "artifact_id": "art_projection",
                "filename": "output.zip",
                "relative_path": "output.zip",
                "stored_path": private_root,
                "size_bytes": 4,
            },
        ),
        store.append(
            operation_key="thread:codex-event:projection",
            scope="thread",
            thread_id="thr_projection",
            event_type="codex.event",
            payload={
                "run_id": "run_projection",
                "provider_event_type": "tool.progress",
                "event": {
                    "cwd": private_root,
                    "prompt": "private provider prompt",
                },
            },
        ),
    ]

    with sqlite3.connect(store.path) as connection:
        raw_payloads = [
            row[0]
            for row in connection.execute(
                "SELECT payload_json FROM events ORDER BY cursor"
            )
        ]

    replayed = store.replay(after_cursor=0).events
    assert [record.payload for record in replayed] == [
        {
            "revision": 2,
            "state": "login_running",
            "busy": True,
            "auth_required": True,
            "auth_mode": "chatgpt",
            "plan_type": "pro",
            "updated_at": "2026-07-13T12:00:00Z",
        },
        {
            "title": "Projection",
            "workspace_id": "ws_projection",
        },
        {
            "attachment_id": "att_projection",
            "filename": "notes.txt",
            "relative_path": "notes.txt",
            "size_bytes": 4,
            "sha256": "a" * 64,
        },
        {
            "artifact_id": "art_projection",
            "filename": "output.zip",
            "relative_path": "output.zip",
            "size_bytes": 4,
        },
        {
            "run_id": "run_projection",
            "provider_event_type": "tool.progress",
        },
    ]
    assert private_root not in "\n".join(raw_payloads)
    assert "SECRET-CODE" not in "\n".join(raw_payloads)
    assert all(
        field not in payload
        for payload in (record.payload for record in records)
        for field in (
            "message",
            "verification_uri",
            "login_url",
            "user_code",
            "output_tail",
            "workspace_path",
            "stored_path",
            "event",
        )
    )


def test_outbox_prepare_capacity_is_distinguished_before_state_publish(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = BridgeEventStore(tmp_path / "events.sqlite3")
    outbox = DurableOutbox(store, state_root=tmp_path / "state")

    def reject(_connection=None) -> None:
        raise EventStoreCapacityError("full")

    monkeypatch.setattr(store, "_require_journal_capacity", reject)
    try:
        with pytest.raises(EventStoreAdmissionError):
            outbox.commit_json(
                operation_id="admission-capacity",
                relative_path="runtime-state.json",
                state_revision=1,
                state_payload={"status": "ready"},
                event=EventDraft(
                    scope="runtime",
                    event_type="runtime.ready",
                    payload={"ready": True},
                ),
            )
        assert not (tmp_path / "state" / "runtime-state.json").exists()
        assert outbox.pending_count() == 0
    finally:
        store.close()


def test_replay_filters_auth_runtime_and_selected_thread_scopes(
    event_store_factory,
) -> None:
    store = event_store_factory()
    auth = store.append(
        operation_key="auth:1",
        scope="auth",
        event_type="auth.updated",
        payload={},
    )
    runtime = store.append(
        operation_key="runtime:1",
        scope="runtime",
        event_type="runtime.ready",
        payload={},
    )
    thread_a = store.append(
        operation_key="thread-a:1",
        scope="thread",
        thread_id="thr_a",
        event_type="message.created",
        payload={},
    )
    store.append(
        operation_key="thread-b:1",
        scope="thread",
        thread_id="thr_b",
        event_type="message.created",
        payload={},
    )

    assert store.replay(after_cursor=0, scopes=("auth",)).events == [auth]
    assert store.replay(after_cursor=0, scopes=("runtime",)).events == [runtime]
    assert store.replay(
        after_cursor=0,
        scopes=("thread",),
        thread_ids=("thr_a",),
    ).events == [thread_a]
    assert store.replay(
        after_cursor=0,
        scopes=("auth", "thread"),
        thread_ids=("thr_a",),
    ).events == [auth, thread_a]


def test_replay_batches_are_bounded_and_resumable(event_store_factory) -> None:
    store = event_store_factory(
        max_batch_events=2,
        max_batch_payload_bytes=10_000,
    )
    events = [
        store.append(
            operation_key=f"runtime:{index}",
            scope="runtime",
            event_type="runtime.event",
            payload={"index": index},
        )
        for index in range(3)
    ]

    first = store.replay(after_cursor=0)
    second = store.replay(after_cursor=first.next_cursor)

    assert first.events == events[:2]
    assert first.next_cursor == events[1].cursor
    assert first.has_more is True
    assert first.heartbeat is False
    assert second.events == events[2:]
    assert second.next_cursor == events[2].cursor
    assert second.has_more is False


def test_replay_obeys_aggregate_payload_byte_limit(event_store_factory) -> None:
    store = event_store_factory(
        max_event_payload_bytes=256,
        max_batch_events=10,
        max_batch_payload_bytes=100,
    )
    payload = {"text": "x" * 80}
    first_event = store.append(
        operation_key="runtime:1",
        scope="runtime",
        event_type="runtime.event",
        payload=payload,
    )
    store.append(
        operation_key="runtime:2",
        scope="runtime",
        event_type="runtime.event",
        payload=payload,
    )

    batch = store.replay(after_cursor=0)

    assert batch.events == [first_event]
    assert batch.has_more is True


def test_append_rejects_an_oversized_payload_without_mutating_the_journal(
    event_store_factory,
) -> None:
    store = event_store_factory(max_event_payload_bytes=64)

    with pytest.raises(EventPayloadTooLargeError):
        store.append(
            operation_key="runtime:oversized",
            scope="runtime",
            event_type="runtime.event",
            payload={"text": "x" * 128},
        )

    assert store.replay(after_cursor=0).events == []


def test_operation_key_deduplicates_identical_append_and_rejects_conflict(
    event_store_factory,
) -> None:
    store = event_store_factory()
    append = {
        "operation_key": "runtime:stable-operation",
        "scope": "runtime",
        "event_type": "runtime.ready",
        "payload": {"revision": 7},
    }

    first = store.append(**append)
    duplicate = store.append(**append)

    assert duplicate == first
    assert store.replay(after_cursor=0).events == [first]
    with pytest.raises(OperationKeyConflictError):
        store.append(
            operation_key=append["operation_key"],
            scope="runtime",
            event_type="runtime.failed",
            payload={"revision": 7},
        )


def test_operation_dedupe_ledger_survives_event_payload_compaction(
    event_store_factory,
) -> None:
    store = event_store_factory()
    append = {
        "operation_key": "thread-a:stable-operation",
        "scope": "thread",
        "thread_id": "thr_a",
        "event_type": "message.completed",
        "payload": {"text": "finished"},
    }
    original = store.append(**append)
    store.compact(
        scope="thread",
        thread_id="thr_a",
        through_cursor=original.cursor,
        snapshot_cursor=original.cursor,
    )

    assert store.append(**append) == original
    with pytest.raises(OperationKeyConflictError):
        store.append(**{**append, "payload": {"text": "different"}})


def test_automatic_compaction_prunes_old_orphan_dedupe_metadata_but_keeps_recent(
    event_store_factory,
) -> None:
    store = event_store_factory(
        max_events_per_non_thread_scope=1,
        max_orphaned_metadata_rows=1,
    )
    oldest_append = {
        "operation_key": "runtime:metadata-oldest",
        "scope": "runtime",
        "event_type": "runtime.changed",
        "payload": {"revision": 1},
    }
    recent_append = {
        "operation_key": "runtime:metadata-recent",
        "scope": "runtime",
        "event_type": "runtime.changed",
        "payload": {"revision": 2},
    }
    current_append = {
        "operation_key": "runtime:metadata-current",
        "scope": "runtime",
        "event_type": "runtime.changed",
        "payload": {"revision": 3},
    }

    store.append(**oldest_append)
    recent = store.append(**recent_append)
    store.append(**current_append)

    assert store.append(**recent_append) == recent
    with pytest.raises(OperationKeyExpiredError):
        store.append(**oldest_append)
    with sqlite3.connect(store.path) as connection:
        operation_keys = connection.execute(
            "SELECT operation_key FROM operation_ledger ORDER BY operation_key"
        ).fetchall()
        tombstones = connection.execute(
            "SELECT tombstone_key FROM operation_tombstones ORDER BY tombstone_key"
        ).fetchall()
    assert operation_keys == [
        ("runtime:metadata-current",),
        ("runtime:metadata-recent",),
    ]
    assert len(tombstones) == 1
    assert len(tombstones[0][0]) == 64


def test_metadata_tombstone_capacity_fails_closed_without_reusing_an_old_key(
    event_store_factory,
) -> None:
    store = event_store_factory(
        max_events_per_non_thread_scope=1,
        max_orphaned_metadata_rows=1,
        max_operation_tombstones=2,
    )
    appends = [
        {
            "operation_key": f"runtime:tombstone-{index}",
            "scope": "runtime",
            "event_type": "runtime.changed",
            "payload": {"revision": index},
        }
        for index in range(1, 6)
    ]

    for append in appends[:4]:
        store.append(**append)
    with pytest.raises(EventStoreCapacityError, match="idempotency tombstone"):
        store.append(**appends[4])
    with pytest.raises(OperationKeyExpiredError):
        store.append(**appends[0])

    assert [event.payload for event in store.replay(after_cursor=3).events] == [
        {"revision": 4}
    ]


def test_concurrent_sqlite_writers_keep_unique_ordered_global_cursors(tmp_path) -> None:
    database_path = tmp_path / "events.sqlite3"
    bootstrap = BridgeEventStore(database_path)
    bootstrap.close()
    barrier = Barrier(2)

    def write(prefix: str) -> list[int]:
        store = BridgeEventStore(database_path)
        try:
            barrier.wait(timeout=2)
            return [
                store.append(
                    operation_key=f"{prefix}:{index}",
                    scope="runtime",
                    event_type="runtime.event",
                    payload={"writer": prefix, "index": index},
                ).cursor
                for index in range(20)
            ]
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(write, "first")
        second = pool.submit(write, "second")
        written_cursors = first.result(timeout=5) + second.result(timeout=5)

    reader = BridgeEventStore(database_path, max_batch_events=100)
    try:
        replayed = reader.replay(after_cursor=0).events
    finally:
        reader.close()

    assert sorted(written_cursors) == list(range(1, 41))
    assert [event.cursor for event in replayed] == list(range(1, 41))


def test_store_uses_wal_and_replays_after_sqlite_restart(tmp_path) -> None:
    database_path = tmp_path / "events.sqlite3"
    first_store = BridgeEventStore(database_path)
    first = first_store.append(
        operation_key="auth:1",
        scope="auth",
        event_type="auth.updated",
        payload={"revision": 1},
    )
    first_store.close()

    with sqlite3.connect(database_path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    second_store = BridgeEventStore(database_path)
    try:
        replayed = second_store.replay(after_cursor=0).events
        second = second_store.append(
            operation_key="auth:2",
            scope="auth",
            event_type="auth.updated",
            payload={"revision": 2},
        )
    finally:
        second_store.close()

    assert journal_mode.lower() == "wal"
    assert replayed == [first]
    assert second.cursor == first.cursor + 1


def test_each_short_lived_sqlite_connection_is_explicitly_closed(
    event_store_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = event_store_factory()
    closed: list[bool] = []
    opened: list[sqlite3.Connection] = []

    class TrackingConnection(sqlite3.Connection):
        def close(self) -> None:
            closed.append(True)
            super().close()

    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(
            store.path,
            isolation_level=None,
            factory=TrackingConnection,
        )
        connection.row_factory = sqlite3.Row
        opened.append(connection)
        return connection

    monkeypatch.setattr(store, "_connect", connect)
    try:
        store.replay(after_cursor=0)
        assert closed == [True]
    finally:
        for connection in opened:
            try:
                connection.close()
            except sqlite3.ProgrammingError:
                pass


def test_wait_replays_an_event_that_already_exists(event_store_factory) -> None:
    store = event_store_factory()
    event = store.append(
        operation_key="runtime:1",
        scope="runtime",
        event_type="runtime.ready",
        payload={},
    )

    batch = store.wait(
        after_cursor=0,
        scopes=("runtime",),
        timeout_seconds=1,
    )

    assert batch.events == [event]
    assert batch.heartbeat is False


def test_append_signals_a_waiter_without_losing_the_replay_window(
    event_store_factory,
) -> None:
    store = event_store_factory()
    waiter_started = Event()

    def wait_for_event():
        waiter_started.set()
        return store.wait(
            after_cursor=0,
            scopes=("runtime",),
            timeout_seconds=1,
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(wait_for_event)
        assert waiter_started.wait(1)
        event = store.append(
            operation_key="runtime:1",
            scope="runtime",
            event_type="runtime.ready",
            payload={},
        )
        batch = pending.result(timeout=2)

    assert batch.events == [event]
    assert batch.heartbeat is False


def test_wait_timeout_returns_a_cursor_preserving_heartbeat(
    event_store_factory,
) -> None:
    store = event_store_factory()

    batch = store.wait(after_cursor=0, timeout_seconds=0.01)

    assert batch.events == []
    assert batch.next_cursor == 0
    assert batch.heartbeat is True
    assert batch.has_more is False


def test_wait_rejects_excess_concurrent_subscribers(
    event_store_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = event_store_factory(max_concurrent_waiters=1)
    entered = Event()
    original_replay = store.replay

    def observed_replay(**kwargs):
        entered.set()
        return original_replay(**kwargs)

    monkeypatch.setattr(store, "replay", observed_replay)
    with ThreadPoolExecutor(max_workers=1) as executor:
        waiting = executor.submit(store.wait, after_cursor=0, timeout_seconds=2)
        assert entered.wait(1)
        with pytest.raises(EventWaitCapacityError):
            store.wait(after_cursor=0, timeout_seconds=0)
        store.append(
            operation_key="runtime:wake-capacity-test",
            scope="runtime",
            event_type="runtime.status",
            payload={"status": "awake"},
        )
        assert waiting.result(timeout=1).events


def test_compaction_expires_only_cursors_below_the_snapshot_boundary(
    event_store_factory,
) -> None:
    store = event_store_factory()
    events = [
        store.append(
            operation_key=f"thread-a:{index}",
            scope="thread",
            thread_id="thr_a",
            event_type="message.delta",
            payload={"index": index},
        )
        for index in range(4)
    ]
    boundary = events[1].cursor

    result = store.compact(
        scope="thread",
        thread_id="thr_a",
        through_cursor=boundary,
        snapshot_cursor=boundary,
    )

    assert result.deleted_count == 2
    assert result.minimum_cursor == boundary
    assert result.snapshot_cursor == boundary
    with pytest.raises(EventCursorExpiredError) as raised:
        store.replay(
            after_cursor=0,
            scopes=("thread",),
            thread_ids=("thr_a",),
        )
    assert raised.value.requested_cursor == 0
    assert raised.value.minimum_cursor == boundary
    assert raised.value.snapshot_cursor == boundary
    resumed = store.replay(
        after_cursor=boundary,
        scopes=("thread",),
        thread_ids=("thr_a",),
    )
    assert resumed.events == events[2:]


def test_thread_retention_automatically_compacts_oldest_event_count(
    event_store_factory,
) -> None:
    store = event_store_factory(max_events_per_thread=3)
    events = [
        store.append(
            operation_key=f"thread-retained:{index}",
            scope="thread",
            thread_id="thr_retained",
            event_type="message.delta",
            payload={"index": index},
        )
        for index in range(4)
    ]

    with pytest.raises(EventCursorExpiredError) as raised:
        store.replay(
            after_cursor=0,
            scopes=("thread",),
            thread_ids=("thr_retained",),
        )

    assert raised.value.minimum_cursor == events[0].cursor
    assert raised.value.snapshot_cursor == events[-1].cursor
    retained = store.replay(
        after_cursor=events[0].cursor,
        scopes=("thread",),
        thread_ids=("thr_retained",),
    )
    assert retained.events == events[1:]


def test_v0_thread_replay_reports_an_expired_sequence_after_retention(
    event_store_factory,
) -> None:
    store = event_store_factory(max_events_per_thread=3)
    events = [
        store.append(
            operation_key=f"thread-v0:{index}",
            scope="thread",
            thread_id="thr_v0_gap",
            event_type="message.delta",
            payload={"index": index},
        )
        for index in range(4)
    ]

    with pytest.raises(ThreadEventSequenceExpiredError) as raised:
        store.replay_thread("thr_v0_gap", after_sequence=0)

    assert raised.value.requested_sequence == 0
    assert raised.value.minimum_sequence == 2
    assert raised.value.snapshot_cursor == events[-1].cursor
    assert [
        event.scope_sequence
        for event in store.replay_thread("thr_v0_gap", after_sequence=1)
    ] == [2, 3, 4]


def test_purge_thread_removes_replayable_payloads_and_advances_its_floor(
    event_store_factory,
) -> None:
    store = event_store_factory()
    secret = store.append(
        operation_key="thread-delete:secret",
        scope="thread",
        thread_id="thr_deleted",
        event_type="message.created",
        payload={"text": "private deleted prompt"},
    )
    store.append(
        operation_key="runtime:retained",
        scope="runtime",
        event_type="runtime.status",
        payload={"status": "healthy"},
    )

    result = store.purge_thread("thr_deleted")

    assert result.deleted_count == 1
    assert result.minimum_cursor == secret.cursor
    assert result.snapshot_cursor == secret.cursor
    with pytest.raises(EventCursorExpiredError):
        store.replay(
            after_cursor=0,
            scopes=("thread",),
            thread_ids=("thr_deleted",),
        )
    with sqlite3.connect(store.path) as connection:
        remaining = connection.execute(
            "SELECT payload_json FROM events WHERE thread_id = ?",
            ("thr_deleted",),
        ).fetchall()
    assert remaining == []


def test_thread_retention_automatically_compacts_oldest_payload_bytes(
    event_store_factory,
) -> None:
    store = event_store_factory(
        max_event_payload_bytes=256,
        max_thread_event_bytes=150,
    )
    events = [
        store.append(
            operation_key=f"thread-bytes:{index}",
            scope="thread",
            thread_id="thr_bytes",
            event_type="message.delta",
            payload={"text": str(index) * 80},
        )
        for index in range(3)
    ]

    with pytest.raises(EventCursorExpiredError) as raised:
        store.replay(
            after_cursor=0,
            scopes=("thread",),
            thread_ids=("thr_bytes",),
        )

    assert raised.value.minimum_cursor == events[1].cursor
    assert raised.value.snapshot_cursor == events[-1].cursor
    retained = store.replay(
        after_cursor=events[1].cursor,
        scopes=("thread",),
        thread_ids=("thr_bytes",),
    )
    assert retained.events == [events[-1]]


@pytest.mark.parametrize("scope", ["auth", "runtime"])
def test_non_thread_scopes_are_automatically_retained(
    event_store_factory,
    scope: str,
) -> None:
    store = event_store_factory(max_events_per_non_thread_scope=3)
    events = [
        store.append(
            operation_key=f"{scope}:retained:{index}",
            scope=scope,
            event_type=f"{scope}.status",
            payload={"index": index},
        )
        for index in range(4)
    ]

    with pytest.raises(EventCursorExpiredError) as raised:
        store.replay(after_cursor=0, scopes=(scope,))

    assert raised.value.minimum_cursor == events[0].cursor
    assert store.replay(
        after_cursor=events[0].cursor,
        scopes=(scope,),
    ).events == events[1:]


def test_compaction_floor_applies_only_to_the_subscribed_scopes(
    event_store_factory,
) -> None:
    store = event_store_factory()
    auth = store.append(
        operation_key="auth:compacted",
        scope="auth",
        event_type="auth.status",
        payload={"state": "old"},
    )
    runtime = store.append(
        operation_key="runtime:retained-after-auth",
        scope="runtime",
        event_type="runtime.status",
        payload={"state": "ready"},
    )
    store.compact(
        scope="auth",
        through_cursor=auth.cursor,
        snapshot_cursor=runtime.cursor,
    )

    assert store.replay(after_cursor=0, scopes=("runtime",)).events == [runtime]
    with pytest.raises(EventCursorExpiredError):
        store.replay(after_cursor=0, scopes=("auth", "runtime"))


def test_physical_journal_capacity_fails_closed_before_unbounded_growth(
    event_store_factory,
) -> None:
    limit = 1024 * 1024
    store = event_store_factory(
        max_event_payload_bytes=64 * 1024,
        max_journal_bytes=limit,
    )

    with pytest.raises(EventStoreCapacityError):
        for index in range(256):
            store.append(
                operation_key=f"runtime:capacity:{index}",
                scope="runtime",
                event_type="runtime.capacity",
                payload={"index": index, "text": "x" * (32 * 1024)},
            )

    physical_bytes = sum(
        path.stat().st_size
        for path in (
            store.path,
            store.path.with_name(f"{store.path.name}-wal"),
            store.path.with_name(f"{store.path.name}-shm"),
        )
        if path.exists()
    )
    assert physical_bytes <= limit + 256 * 1024


def test_deduped_append_remains_readable_when_new_writes_are_at_capacity(
    event_store_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = event_store_factory()
    first = store.append(
        operation_key="runtime:dedupe-before-full",
        scope="runtime",
        event_type="runtime.status",
        payload={"status": "ready"},
    )

    def full(_connection=None) -> None:
        raise EventStoreCapacityError("full")

    monkeypatch.setattr(store, "_require_journal_capacity", full)

    assert (
        store.append(
            operation_key="runtime:dedupe-before-full",
            scope="runtime",
            event_type="runtime.status",
            payload={"status": "ready"},
        )
        == first
    )
    with pytest.raises(EventStoreCapacityError):
        store.append(
            operation_key="runtime:new-while-full",
            scope="runtime",
            event_type="runtime.status",
            payload={"status": "blocked"},
        )


def test_initialization_migrates_and_prunes_orphan_metadata_without_pending_outbox(
    tmp_path,
) -> None:
    database = tmp_path / "events.sqlite3"
    bootstrap = BridgeEventStore(database)
    bootstrap.close()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE legacy_outbox_operations ("
            "operation_id TEXT PRIMARY KEY, "
            "fingerprint TEXT NOT NULL, "
            "payload_json TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "applied_at TEXT"
            ")"
        )
        connection.execute("DROP TABLE outbox_operations")
        connection.execute(
            "ALTER TABLE legacy_outbox_operations RENAME TO outbox_operations"
        )
        connection.executemany(
            "INSERT INTO operation_ledger("
            "operation_key, fingerprint, cursor, event_id, operation_id, scope, "
            "scope_id, thread_id, scope_sequence, event_type, timestamp"
            ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "legacy:oldest",
                    "fingerprint-oldest",
                    1,
                    "evt_oldest",
                    None,
                    "runtime",
                    "runtime",
                    None,
                    1,
                    "runtime.changed",
                    "2026-07-13T12:00:00Z",
                ),
                (
                    "legacy:recent",
                    "fingerprint-recent",
                    2,
                    "evt_recent",
                    None,
                    "runtime",
                    "runtime",
                    None,
                    2,
                    "runtime.changed",
                    "2026-07-13T12:00:01Z",
                ),
                (
                    "outbox:pending-operation:0",
                    "fingerprint-pending",
                    3,
                    "evt_pending",
                    "pending-operation",
                    "runtime",
                    "runtime",
                    None,
                    3,
                    "runtime.changed",
                    "2026-07-13T12:00:02Z",
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO legacy_import_ledger(legacy_key, fingerprint, operation_key) "
            "VALUES(?, ?, ?)",
            [
                ("legacy:oldest", "legacy-fingerprint-oldest", "legacy:oldest"),
                ("legacy:recent", "legacy-fingerprint-recent", "legacy:recent"),
            ],
        )
        connection.executemany(
            "INSERT INTO outbox_operations("
            "operation_id, fingerprint, payload_json, created_at, applied_at"
            ") VALUES(?, ?, ?, ?, ?)",
            [
                (
                    "applied-oldest",
                    "outbox-fingerprint-oldest",
                    "{\"private\":true}",
                    "2026-07-13T12:00:00Z",
                    "2026-07-13T12:00:01Z",
                ),
                (
                    "applied-recent",
                    "outbox-fingerprint-recent",
                    "{\"private\":true}",
                    "2026-07-13T12:00:02Z",
                    "2026-07-13T12:00:03Z",
                ),
                (
                    "pending-operation",
                    "outbox-fingerprint-pending",
                    "{\"keep\":true}",
                    "2026-07-13T12:00:04Z",
                    None,
                ),
            ],
        )

    store = BridgeEventStore(database, max_orphaned_metadata_rows=1)
    try:
        with sqlite3.connect(database) as connection:
            operation_keys = connection.execute(
                "SELECT operation_key FROM operation_ledger ORDER BY operation_key"
            ).fetchall()
            legacy_keys = connection.execute(
                "SELECT legacy_key FROM legacy_import_ledger ORDER BY legacy_key"
            ).fetchall()
            outbox_rows = connection.execute(
                "SELECT operation_id, payload_json, applied_at FROM outbox_operations "
                "ORDER BY operation_id"
            ).fetchall()
            tombstones = connection.execute(
                "SELECT tombstone_key FROM operation_tombstones ORDER BY tombstone_key"
            ).fetchall()
    finally:
        store.close()

    assert operation_keys == [
        ("legacy:recent",),
        ("outbox:pending-operation:0",),
    ]
    assert legacy_keys == [("legacy:recent",)]
    assert outbox_rows == [("pending-operation", "{\"keep\":true}", None)]
    assert len(tombstones) == 3
    assert all(len(tombstone[0]) == 64 for tombstone in tombstones)


def test_legacy_jsonl_import_is_idempotent_even_if_the_file_is_renamed(
    event_store_factory,
    tmp_path,
) -> None:
    store = event_store_factory()
    legacy_records = [
        {
            "event_id": "evt_legacy_1",
            "thread_id": "thr_legacy",
            "sequence": 1,
            "event_type": "thread.created",
            "payload": {
                "title": "Imported",
                "workspace_path": str(tmp_path / "private-root"),
            },
            "timestamp": "2026-07-12T10:00:00Z",
        },
        {
            "event_id": "evt_legacy_2",
            "thread_id": "thr_legacy",
            "sequence": 2,
            "event_type": "message.completed",
            "payload": {"text": "done"},
            "timestamp": "2026-07-12T10:01:00Z",
        },
        {
            "event_id": "evt_legacy_3",
            "thread_id": "thr_legacy",
            "sequence": 3,
            "event_type": "run.failed",
            "payload": {
                "run_id": "run_legacy",
                "failure_type": "run.failed",
                "blocked": False,
                "error": f"failed at {tmp_path / 'private-root'}",
                "raw_error": str(tmp_path / "private-root"),
            },
            "timestamp": "2026-07-12T10:02:00Z",
        },
        {
            "event_id": "evt_legacy_4",
            "thread_id": "thr_legacy",
            "sequence": 4,
            "event_type": "provider.private",
            "payload": {
                "cwd": str(tmp_path / "private-root"),
                "prompt": "private legacy prompt",
            },
            "timestamp": "2026-07-12T10:03:00Z",
        },
    ]
    legacy_path = tmp_path / "thr_legacy.events.jsonl"
    legacy_path.write_text(
        "".join(f"{json.dumps(record)}\n" for record in legacy_records),
        encoding="utf-8",
    )
    renamed_path = tmp_path / "renamed.events.jsonl"
    renamed_path.write_bytes(legacy_path.read_bytes())

    first = store.import_legacy_jsonl(legacy_path, thread_id="thr_legacy")
    second = store.import_legacy_jsonl(renamed_path, thread_id="thr_legacy")
    replayed = store.replay(
        after_cursor=0,
        scopes=("thread",),
        thread_ids=("thr_legacy",),
    ).events
    with sqlite3.connect(store.path) as connection:
        raw_payloads = [
            row[0]
            for row in connection.execute(
                "SELECT payload_json FROM events ORDER BY cursor"
            )
        ]

    assert first.scanned_count == 4
    assert first.imported_count == 4
    assert first.duplicate_count == 0
    assert second.scanned_count == 4
    assert second.imported_count == 0
    assert second.duplicate_count == 4
    assert [event.event_type for event in replayed] == [
        "thread.created",
        "message.completed",
        "run.failed",
        "legacy.event",
    ]
    assert [event.payload for event in replayed] == [
        {"title": "Imported"},
        {"text": "done"},
        {
            "run_id": "run_legacy",
            "failure_type": "run.failed",
            "blocked": False,
        },
        {"legacy_event_type": "provider.private"},
    ]
    assert str(tmp_path / "private-root") not in "\n".join(raw_payloads)
    assert "private legacy prompt" not in "\n".join(raw_payloads)


@pytest.mark.parametrize("timestamp", [None, "", 42, ["not", "a", "timestamp"]])
def test_legacy_import_rejects_non_string_or_empty_timestamps(
    event_store_factory,
    tmp_path,
    timestamp: object,
) -> None:
    store = event_store_factory()
    legacy_path = tmp_path / "malformed.events.jsonl"
    legacy_path.write_text(
        json.dumps(
            {
                "event_id": "evt_invalid_timestamp",
                "thread_id": "thr_legacy",
                "sequence": 1,
                "event_type": "thread.created",
                "payload": {},
                "timestamp": timestamp,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(EventStoreError, match="legacy event record is invalid"):
        store.import_legacy_jsonl(legacy_path, thread_id="thr_legacy")


def test_legacy_import_ledger_prevents_compacted_events_from_resurrecting(
    event_store_factory,
    tmp_path,
) -> None:
    store = event_store_factory()
    legacy_path = tmp_path / "thr_legacy.events.jsonl"
    legacy_path.write_text(
        json.dumps(
            {
                "event_id": "evt_legacy_1",
                "thread_id": "thr_legacy",
                "sequence": 1,
                "event_type": "thread.created",
                "payload": {"title": "Imported"},
                "timestamp": "2026-07-12T10:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    imported = store.import_legacy_jsonl(legacy_path, thread_id="thr_legacy")
    event = store.replay(
        after_cursor=0,
        scopes=("thread",),
        thread_ids=("thr_legacy",),
    ).events[0]
    store.compact(
        scope="thread",
        thread_id="thr_legacy",
        through_cursor=event.cursor,
        snapshot_cursor=event.cursor,
    )

    duplicate = store.import_legacy_jsonl(legacy_path, thread_id="thr_legacy")

    assert imported.imported_count == 1
    assert duplicate.imported_count == 0
    assert duplicate.duplicate_count == 1
    assert (
        store.replay(
            after_cursor=event.cursor,
            scopes=("thread",),
            thread_ids=("thr_legacy",),
        ).events
        == []
    )


def test_legacy_import_counts_an_expired_tombstone_as_a_duplicate(
    event_store_factory,
    tmp_path,
) -> None:
    store = event_store_factory(
        max_events_per_thread=1,
        max_orphaned_metadata_rows=1,
    )
    legacy_path = tmp_path / "thr_legacy.events.jsonl"
    legacy_path.write_text(
        "".join(
            json.dumps(
                {
                    "event_id": f"evt_legacy_{index}",
                    "thread_id": "thr_legacy",
                    "sequence": index,
                    "event_type": "message.completed",
                    "payload": {"index": index},
                    "timestamp": f"2026-07-13T12:00:0{index}Z",
                }
            )
            + "\n"
            for index in range(1, 4)
        ),
        encoding="utf-8",
    )

    first = store.import_legacy_jsonl(legacy_path, thread_id="thr_legacy")
    retried = store.import_legacy_jsonl(legacy_path, thread_id="thr_legacy")

    assert first.imported_count == 3
    assert retried.imported_count == 0
    assert retried.duplicate_count == 3
