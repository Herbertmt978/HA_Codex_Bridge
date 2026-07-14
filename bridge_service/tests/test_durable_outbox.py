import json
import os
from pathlib import Path
import sqlite3

import pytest

from codex_bridge_service.event_store import (
    BridgeEventStore,
    DurableOperationTooLargeError,
    DurableOutbox,
    EventDraft,
    EventPayloadTooLargeError,
    EventStoreCapacityError,
    InjectedOutboxCrash,
    OperationKeyExpiredError,
    OutboxStateConflictError,
    OutboxWrite,
)


def test_outbox_validates_event_payload_before_preparing_recovery_state(
    tmp_path: Path,
) -> None:
    store = BridgeEventStore(
        tmp_path / "events.sqlite3",
        max_event_payload_bytes=64,
    )
    outbox = DurableOutbox(store, state_root=tmp_path / "state")
    try:
        with pytest.raises(EventPayloadTooLargeError):
            outbox.commit_operation(
                operation_id="oversized-event",
                writes=(),
                events=(
                    EventDraft(
                        scope="runtime",
                        event_type="runtime.oversized",
                        payload={"text": "x" * 128},
                    ),
                ),
            )

        assert outbox.pending_count() == 0
    finally:
        store.close()


def test_outbox_bounds_event_count_and_operation_bytes_before_prepare(
    tmp_path: Path,
) -> None:
    store = BridgeEventStore(tmp_path / "events.sqlite3")
    outbox = DurableOutbox(
        store,
        state_root=tmp_path / "state",
        max_operation_events=1,
        max_operation_bytes=512,
    )
    event = EventDraft(
        scope="runtime",
        event_type="runtime.status",
        payload={"status": "healthy"},
    )
    try:
        with pytest.raises(DurableOperationTooLargeError):
            outbox.commit_operation(
                operation_id="too-many-events",
                writes=(),
                events=(event, event),
            )
        with pytest.raises(DurableOperationTooLargeError):
            outbox.commit_operation(
                operation_id="too-many-bytes",
                writes=(
                    OutboxWrite(
                        relative_path="state.json",
                        state_revision=1,
                        state_payload={"text": "x" * 1024},
                    ),
                ),
                events=(),
            )

        assert outbox.pending_count() == 0
    finally:
        store.close()


def test_outbox_maps_sqlite_capacity_failure_without_publishing_state(
    tmp_path: Path,
) -> None:
    store = BridgeEventStore(
        tmp_path / "events.sqlite3",
        max_journal_bytes=1024 * 1024,
    )
    state_root = tmp_path / "state"
    outbox = DurableOutbox(
        store,
        state_root=state_root,
        max_operation_bytes=2 * 1024 * 1024,
    )
    try:
        with pytest.raises(EventStoreCapacityError):
            outbox.commit_operation(
                operation_id="journal-capacity",
                writes=(
                    OutboxWrite(
                        relative_path="large.json",
                        state_revision=1,
                        state_payload={"text": "x" * (900 * 1024)},
                    ),
                ),
                events=(),
            )

        assert not (state_root / "large.json").exists()
    finally:
        store.close()


def test_outbox_retry_is_idempotent_when_capacity_rejects_new_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = BridgeEventStore(tmp_path / "events.sqlite3")
    state_root = tmp_path / "state"
    outbox = DurableOutbox(store, state_root=state_root)
    event = EventDraft(
        scope="runtime",
        event_type="runtime.status",
        payload={"status": "ready"},
        timestamp="2026-07-13T12:00:00Z",
    )
    try:
        first = outbox.commit_json(
            operation_id="capacity-retry",
            relative_path="runtime-state.json",
            state_revision=1,
            state_payload={"status": "ready"},
            event=event,
        )

        def full(_connection=None) -> None:
            raise EventStoreCapacityError("full")

        monkeypatch.setattr(store, "_require_journal_capacity", full)

        assert outbox.commit_json(
            operation_id="capacity-retry",
            relative_path="runtime-state.json",
            state_revision=1,
            state_payload={"status": "ready"},
            event=event,
        ) == first
        with pytest.raises(EventStoreCapacityError):
            outbox.commit_json(
                operation_id="capacity-new",
                relative_path="new-state.json",
                state_revision=1,
                state_payload={"status": "blocked"},
                event=EventDraft(
                    scope="runtime",
                    event_type="runtime.status",
                    payload={"status": "blocked"},
                    timestamp="2026-07-13T12:00:01Z",
                ),
            )

        assert outbox.pending_count() == 0
        assert len(store.replay(after_cursor=0).events) == 1
        assert not (state_root / "new-state.json").exists()
    finally:
        store.close()


@pytest.mark.skipif(os.name == "nt", reason="descriptor-rooted Linux write contract")
def test_outbox_creates_missing_nested_state_directories_posix(
    tmp_path: Path,
) -> None:
    store = BridgeEventStore(tmp_path / "events.sqlite3")
    state_root = tmp_path / "state"
    outbox = DurableOutbox(store, state_root=state_root)
    try:
        outbox.commit_json(
            operation_id="nested-state",
            relative_path="threads/2026/07/runtime-state.json",
            state_revision=1,
            state_payload={"status": "ready"},
            event=EventDraft(
                scope="runtime",
                event_type="runtime.status",
                payload={"status": "ready"},
                timestamp="2026-07-13T12:00:00Z",
            ),
        )

        nested = state_root / "threads" / "2026" / "07"
        assert nested.is_dir()
        assert (nested / "runtime-state.json").is_file()
    finally:
        store.close()


@pytest.mark.parametrize(
    "crash_point",
    [
        "after_outbox_commit",
        "after_state_replace",
        "before_event_append",
    ],
)
def test_reconcile_finishes_each_injected_commit_crash_exactly_once(
    tmp_path: Path,
    crash_point: str,
) -> None:
    database = tmp_path / "events.sqlite3"
    state_root = tmp_path / "state"
    state_root.mkdir()
    fired = False

    def inject(point: str) -> None:
        nonlocal fired
        if point == crash_point and not fired:
            fired = True
            raise InjectedOutboxCrash(point)

    event_store = BridgeEventStore(database)
    outbox = DurableOutbox(
        event_store,
        state_root=state_root,
        failure_injector=inject,
    )

    with pytest.raises(InjectedOutboxCrash, match=crash_point):
        outbox.commit_json(
            operation_id="op-runtime-1",
            relative_path="runtime-state.json",
            state_revision=1,
            state_payload={"status": "running"},
            event=EventDraft(
                scope="runtime",
                event_type="runtime.state_changed",
                payload={"revision": 1},
            ),
        )

    restarted_store = BridgeEventStore(database)
    restarted_outbox = DurableOutbox(restarted_store, state_root=state_root)
    assert restarted_outbox.reconcile() == 1
    assert restarted_outbox.reconcile() == 0

    saved = json.loads((state_root / "runtime-state.json").read_text("utf-8"))
    assert saved["_bridge_operation"] == {
        "operation_id": "op-runtime-1",
        "revision": 1,
    }
    assert saved["status"] == "running"

    batch = restarted_store.replay(after=0)
    assert [event.event_type for event in batch.events] == ["runtime.state_changed"]
    assert batch.events[0].operation_id == "op-runtime-1"
    assert restarted_outbox.pending_count() == 0


def test_newer_state_revision_reconciles_older_post_state_operation_first(
    tmp_path: Path,
) -> None:
    database = tmp_path / "events.sqlite3"
    state_root = tmp_path / "state"
    fired = False

    def crash_before_first_event(point: str) -> None:
        nonlocal fired
        if point == "before_event_append" and not fired:
            fired = True
            raise InjectedOutboxCrash(point)

    store = BridgeEventStore(database)
    outbox = DurableOutbox(
        store,
        state_root=state_root,
        failure_injector=crash_before_first_event,
    )
    with pytest.raises(InjectedOutboxCrash, match="before_event_append"):
        outbox.commit_json(
            operation_id="ordered-revision-1",
            relative_path="runtime-state.json",
            state_revision=1,
            state_payload={"revision": 1},
            event=EventDraft(
                scope="runtime",
                event_type="runtime.changed",
                payload={"revision": 1},
            ),
        )

    second = outbox.commit_json(
        operation_id="ordered-revision-2",
        relative_path="runtime-state.json",
        state_revision=2,
        state_payload={"revision": 2},
        event=EventDraft(
            scope="runtime",
            event_type="runtime.changed",
            payload={"revision": 2},
        ),
    )

    assert len(second) == 1
    assert outbox.pending_count() == 0
    saved = json.loads(
        (state_root / "runtime-state.json").read_text(encoding="utf-8")
    )
    assert saved["_bridge_operation"] == {
        "operation_id": "ordered-revision-2",
        "revision": 2,
    }
    assert [event.payload["revision"] for event in store.replay(after=0).events] == [
        1,
        2,
    ]

    store.close()
    restarted_store = BridgeEventStore(database)
    restarted = DurableOutbox(restarted_store, state_root=state_root)
    try:
        assert restarted.reconcile() == 0
    finally:
        restarted_store.close()


def test_pending_outbox_survives_metadata_pruning_and_crash_recovery(
    tmp_path: Path,
) -> None:
    database = tmp_path / "events.sqlite3"
    state_root = tmp_path / "state"
    state_root.mkdir()

    def crash_after_prepare(point: str) -> None:
        if point == "after_outbox_commit":
            raise InjectedOutboxCrash(point)

    store = BridgeEventStore(database, max_orphaned_metadata_rows=1)
    outbox = DurableOutbox(
        store,
        state_root=state_root,
        failure_injector=crash_after_prepare,
    )
    with pytest.raises(InjectedOutboxCrash):
        outbox.commit_json(
            operation_id="pending-metadata-prune",
            relative_path="runtime-state.json",
            state_revision=1,
            state_payload={"status": "running"},
            event=EventDraft(
                scope="runtime",
                event_type="runtime.changed",
                payload={"revision": 1},
            ),
        )

    store.compact(scope="runtime", through_cursor=0, snapshot_cursor=0)
    assert outbox.pending_count() == 1

    restarted_store = BridgeEventStore(database, max_orphaned_metadata_rows=1)
    restarted_outbox = DurableOutbox(restarted_store, state_root=state_root)
    assert restarted_outbox.reconcile() == 1
    assert restarted_outbox.pending_count() == 0
    assert [event.operation_id for event in restarted_store.replay(after=0).events] == [
        "pending-metadata-prune"
    ]


def test_recent_compacted_outbox_operation_stays_complete_and_idempotent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "events.sqlite3"
    state_root = tmp_path / "state"
    store = BridgeEventStore(
        database,
        max_events_per_non_thread_scope=1,
        max_orphaned_metadata_rows=1,
    )
    outbox = DurableOutbox(store, state_root=state_root)

    first = outbox.commit_json(
        operation_id="outbox-expired",
        relative_path="runtime-state.json",
        state_revision=1,
        state_payload={"revision": 1},
        event=EventDraft(
            scope="runtime",
            event_type="runtime.changed",
            payload={"revision": 1},
        ),
    )
    recent_arguments = {
        "operation_id": "outbox-recent",
        "relative_path": "runtime-state.json",
        "state_revision": 2,
        "state_payload": {"revision": 2},
        "event": EventDraft(
            scope="runtime",
            event_type="runtime.changed",
            payload={"revision": 2},
        ),
    }
    recent = outbox.commit_json(**recent_arguments)
    outbox.commit_json(
        operation_id="outbox-current",
        relative_path="runtime-state.json",
        state_revision=3,
        state_payload={"revision": 3},
        event=EventDraft(
            scope="runtime",
            event_type="runtime.changed",
            payload={"revision": 3},
        ),
    )

    assert outbox.commit_json(**recent_arguments) == recent
    with pytest.raises(OperationKeyExpiredError):
        outbox.commit_json(
            operation_id="outbox-expired",
            relative_path="runtime-state.json",
            state_revision=1,
            state_payload={"revision": 1},
            event=EventDraft(
                scope="runtime",
                event_type="runtime.changed",
                payload={"revision": 1},
            ),
        )
    assert first[0].operation_id == "outbox-expired"


def test_state_only_outbox_operations_are_bounded_without_a_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "events.sqlite3"
    state_root = tmp_path / "state"
    store = BridgeEventStore(database, max_orphaned_metadata_rows=1)
    outbox = DurableOutbox(store, state_root=state_root)

    outbox.commit_operation(
        operation_id="state-only-oldest",
        writes=(
            OutboxWrite(
                relative_path="runtime-state.json",
                state_revision=1,
                state_payload={"revision": 1},
            ),
        ),
        events=(),
    )
    outbox.commit_operation(
        operation_id="state-only-recent",
        writes=(
            OutboxWrite(
                relative_path="runtime-state.json",
                state_revision=2,
                state_payload={"revision": 2},
            ),
        ),
        events=(),
    )

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT operation_id FROM outbox_operations ORDER BY operation_id"
        ).fetchall()
    assert rows == [("state-only-recent",)]
    with pytest.raises(OperationKeyExpiredError):
        outbox.commit_operation(
            operation_id="state-only-oldest",
            writes=(
                OutboxWrite(
                    relative_path="runtime-state.json",
                    state_revision=1,
                    state_payload={"revision": 1},
                ),
            ),
            events=(),
        )


def test_reconcile_applies_one_multi_write_multi_event_operation_exactly_once(
    tmp_path: Path,
) -> None:
    database = tmp_path / "events.sqlite3"
    state_root = tmp_path / "state"
    state_root.mkdir()
    (state_root / "threads").mkdir()

    runtime_payload = {"status": "running", "run_id": "run_1"}
    thread_payload = {"status": "running", "active_run_id": "run_1"}
    message_payload = {"run_id": "run_1", "text": "Keep going"}
    queued_payload = {"run_id": "run_1"}

    def crash_after_prepare(point: str) -> None:
        if point == "after_outbox_commit":
            raise InjectedOutboxCrash(point)

    event_store = BridgeEventStore(database)
    outbox = DurableOutbox(
        event_store,
        state_root=state_root,
        failure_injector=crash_after_prepare,
    )
    with pytest.raises(InjectedOutboxCrash):
        outbox.commit_operation(
            operation_id="op-run-accepted",
            writes=(
                OutboxWrite(
                    relative_path="runtime-state.json",
                    state_revision=4,
                    state_payload=runtime_payload,
                ),
                OutboxWrite(
                    relative_path="threads/thr_1.json",
                    state_revision=7,
                    state_payload=thread_payload,
                ),
            ),
            events=(
                EventDraft(
                    scope="thread",
                    thread_id="thr_1",
                    event_type="message.created",
                    payload=message_payload,
                ),
                EventDraft(
                    scope="thread",
                    thread_id="thr_1",
                    event_type="run.queued",
                    payload=queued_payload,
                ),
            ),
        )

    # Recovery must use the complete durable operation, not caller-owned objects.
    runtime_payload["status"] = "caller-mutated"
    thread_payload["status"] = "caller-mutated"
    message_payload["text"] = "caller-mutated"
    queued_payload["run_id"] = "caller-mutated"

    restarted_store = BridgeEventStore(database)
    restarted = DurableOutbox(restarted_store, state_root=state_root)
    assert restarted.reconcile() == 1
    assert restarted.reconcile() == 0
    assert (
        DurableOutbox(BridgeEventStore(database), state_root=state_root).reconcile()
        == 0
    )

    runtime = json.loads(
        (state_root / "runtime-state.json").read_text(encoding="utf-8")
    )
    thread = json.loads(
        (state_root / "threads" / "thr_1.json").read_text(encoding="utf-8")
    )
    assert runtime == {
        "_bridge_operation": {
            "operation_id": "op-run-accepted",
            "revision": 4,
        },
        "status": "running",
        "run_id": "run_1",
    }
    assert thread == {
        "_bridge_operation": {
            "operation_id": "op-run-accepted",
            "revision": 7,
        },
        "status": "running",
        "active_run_id": "run_1",
    }

    batch = restarted_store.replay(after=0)
    assert [event.event_type for event in batch.events] == [
        "message.created",
        "run.queued",
    ]
    assert [event.operation_id for event in batch.events] == [
        "op-run-accepted",
        "op-run-accepted",
    ]
    assert batch.events[0].payload == {
        "run_id": "run_1",
        "text": "Keep going",
    }
    assert batch.events[1].payload == {"run_id": "run_1"}
    assert restarted.pending_count() == 0


@pytest.mark.parametrize(
    ("existing_revision", "existing_operation_id"),
    [(1, "op-divergent"), (2, "op-newer")],
    ids=["equal-revision", "newer-revision"],
)
def test_reconcile_fails_closed_before_writing_or_emitting_for_divergent_state(
    tmp_path: Path,
    existing_revision: int,
    existing_operation_id: str,
) -> None:
    database = tmp_path / "events.sqlite3"
    state_root = tmp_path / "state"
    state_root.mkdir()
    (state_root / "threads").mkdir()

    def crash_after_prepare(point: str) -> None:
        if point == "after_outbox_commit":
            raise InjectedOutboxCrash(point)

    event_store = BridgeEventStore(database)
    outbox = DurableOutbox(
        event_store,
        state_root=state_root,
        failure_injector=crash_after_prepare,
    )
    with pytest.raises(InjectedOutboxCrash):
        outbox.commit_operation(
            operation_id="op-pending",
            writes=(
                OutboxWrite(
                    relative_path="runtime-state.json",
                    state_revision=1,
                    state_payload={"status": "running"},
                ),
                OutboxWrite(
                    relative_path="threads/thr_1.json",
                    state_revision=1,
                    state_payload={"title": "pending"},
                ),
            ),
            events=(
                EventDraft(
                    scope="thread",
                    thread_id="thr_1",
                    event_type="thread.updated",
                    payload={"title": "pending"},
                ),
            ),
        )

    divergent_path = state_root / "threads" / "thr_1.json"
    divergent_path.write_text(
        json.dumps(
            {
                "_bridge_operation": {
                    "operation_id": existing_operation_id,
                    "revision": existing_revision,
                },
                "title": "must survive",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    original = divergent_path.read_bytes()

    restarted_store = BridgeEventStore(database)
    restarted = DurableOutbox(restarted_store, state_root=state_root)
    for _attempt in range(2):
        with pytest.raises(OutboxStateConflictError, match="canonical state"):
            restarted.reconcile()

    assert not (state_root / "runtime-state.json").exists()
    assert divergent_path.read_bytes() == original
    assert restarted_store.replay(after=0).events == []
    assert restarted.pending_count() == 1


def test_outbox_rejects_state_paths_outside_its_private_root(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    outbox = DurableOutbox(
        BridgeEventStore(tmp_path / "events.sqlite3"),
        state_root=state_root,
    )

    with pytest.raises(ValueError, match="state path"):
        outbox.commit_json(
            operation_id="op-escape",
            relative_path="../outside.json",
            state_revision=1,
            state_payload={"unsafe": True},
            event=EventDraft(
                scope="runtime",
                event_type="runtime.state_changed",
                payload={"revision": 1},
            ),
        )


@pytest.mark.skipif(os.name == "nt", reason="descriptor-rooted Linux write contract")
def test_outbox_rejects_parent_symlink_swap_before_state_replace(
    tmp_path: Path,
) -> None:
    database = tmp_path / "events.sqlite3"
    state_root = tmp_path / "state"
    threads = state_root / "threads"
    outside = tmp_path / "outside"
    state_root.mkdir()
    threads.mkdir()
    outside.mkdir()
    moved_threads = state_root / "threads-before-swap"

    def swap_parent(point: str) -> None:
        if point != "before_state_replace" or moved_threads.exists():
            return
        threads.rename(moved_threads)
        os.symlink(outside, threads, target_is_directory=True)

    event_store = BridgeEventStore(database)
    outbox = DurableOutbox(
        event_store,
        state_root=state_root,
        failure_injector=swap_parent,
    )
    try:
        with pytest.raises(OutboxStateConflictError):
            outbox.commit_operation(
                operation_id="parent-symlink-swap",
                writes=(
                    OutboxWrite(
                        relative_path="threads/thr_1.json",
                        state_revision=1,
                        state_payload={"title": "must stay private"},
                    ),
                ),
                events=(
                    EventDraft(
                        scope="thread",
                        thread_id="thr_1",
                        event_type="thread.updated",
                        payload={"title": "must stay private"},
                    ),
                ),
            )

        assert not (outside / "thr_1.json").exists()
        assert event_store.replay(after_cursor=0).events == []
        assert outbox.pending_count() == 1
    finally:
        if threads.is_symlink():
            threads.unlink()
        if moved_threads.exists():
            moved_threads.rename(threads)
        event_store.close()


def test_applied_outbox_scrubs_recovery_payload_and_old_retry_is_idempotent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "events.sqlite3"
    state_root = tmp_path / "state"
    state_root.mkdir()
    store = BridgeEventStore(database)
    outbox = DurableOutbox(store, state_root=state_root)
    first_arguments = {
        "operation_id": "op-state-1",
        "relative_path": "runtime-state.json",
        "state_revision": 1,
        "state_payload": {"revision": 1, "private": "do-not-retain"},
        "event": EventDraft(
            scope="runtime",
            event_type="runtime.changed",
            payload={"revision": 1},
        ),
    }

    first = outbox.commit_json(**first_arguments)
    outbox.commit_json(
        operation_id="op-state-2",
        relative_path="runtime-state.json",
        state_revision=2,
        state_payload={"revision": 2},
        event=EventDraft(
            scope="runtime",
            event_type="runtime.changed",
            payload={"revision": 2},
        ),
    )
    retried = outbox.commit_json(**first_arguments)

    assert retried == first
    current = json.loads(
        (state_root / "runtime-state.json").read_text(encoding="utf-8")
    )
    assert current["revision"] == 2
    with sqlite3.connect(database) as connection:
        applied_payloads = connection.execute(
            "SELECT payload_json FROM outbox_operations "
            "WHERE applied_at IS NOT NULL ORDER BY operation_id"
        ).fetchall()
    assert applied_payloads == [("{}",), ("{}",)]
