from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from codex_bridge_service.app import _load_durable_auth_status, create_app
from codex_bridge_service.event_store import (
    BridgeEventStore,
    DurableOutbox,
    EventCursorExpiredError,
    EventDraft,
    InjectedOutboxCrash,
)
from codex_bridge_service.models import (
    DEFAULT_MODEL,
    ProjectRecord,
    RunMode,
    RuntimeProfile,
    ThreadRecord,
)
from codex_bridge_service.resource_limits import ResourceLimits
from codex_bridge_service.runtime_broker import RuntimeBroker
from codex_bridge_service.runtime_gate import RuntimeGate
from codex_bridge_service.runtime_state import (
    RuntimeRunState,
    RuntimeStateRecord,
    RuntimeStateStore,
    runtime_fingerprint,
)
from codex_bridge_service.storage import BridgeStorage


def test_durable_auth_marker_rejects_zero_outbox_revision(tmp_path: Path) -> None:
    auth_state = tmp_path / "auth-state.json"
    auth_state.write_text(
        json.dumps(
            {
                "_bridge_operation": {
                    "operation_id": "auth-state:0",
                    "revision": 0,
                },
                "revision": 0,
                "state": "unknown",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="marker is invalid"):
        _load_durable_auth_status(auth_state)


def test_pending_outbox_envelope_projects_private_event_fields(
    tmp_path: Path,
) -> None:
    database = tmp_path / "events.sqlite3"
    state_root = tmp_path / "state"
    state_root.mkdir()
    private_root = str(tmp_path / "private-root")

    def crash_after_prepare(point: str) -> None:
        if point == "after_outbox_commit":
            raise InjectedOutboxCrash(point)

    store = BridgeEventStore(database)
    outbox = DurableOutbox(
        store,
        state_root=state_root,
        failure_injector=crash_after_prepare,
    )
    try:
        with pytest.raises(InjectedOutboxCrash):
            outbox.commit_json(
                operation_id="projection-pending",
                relative_path="runtime-state.json",
                state_revision=1,
                state_payload={"status": "ready"},
                event=EventDraft(
                    scope="thread",
                    thread_id="thr_projection",
                    event_type="thread.created",
                    payload={
                        "title": "Projection",
                        "workspace_path": private_root,
                    },
                ),
            )
        with sqlite3.connect(database) as connection:
            pending_payload = connection.execute(
                "SELECT payload_json FROM outbox_operations "
                "WHERE operation_id = ?",
                ("projection-pending",),
            ).fetchone()[0]
        assert private_root not in pending_payload
        assert "workspace_path" not in pending_payload
    finally:
        store.close()

    restarted_store = BridgeEventStore(database)
    try:
        restarted_outbox = DurableOutbox(restarted_store, state_root=state_root)
        assert restarted_outbox.reconcile() == 1
        event = restarted_store.replay(after_cursor=0).events[0]
        assert event.payload == {"title": "Projection"}
    finally:
        restarted_store.close()


def _write_legacy_thread_state(state_root: Path, workspace: Path) -> str:
    thread_id = "thr_legacy"
    project_id = "prj_legacy"
    for name in ("projects", "threads", "logs"):
        (state_root / name).mkdir(parents=True, exist_ok=True)
    timestamp = "2026-07-12T10:00:00Z"
    project = ProjectRecord(
        project_id=project_id,
        name="Legacy project",
        root_path=str(workspace),
        default_model=DEFAULT_MODEL,
        default_thinking_level="medium",
        created_at=timestamp,
        updated_at=timestamp,
    )
    thread = ThreadRecord(
        thread_id=thread_id,
        project_id=project_id,
        title="Legacy thread",
        workspace_id="ws_legacy",
        workspace_path=str(workspace),
        status="idle",
        mode=RunMode.EDIT,
        created_at=timestamp,
        updated_at=timestamp,
    )
    (state_root / "projects" / f"{project_id}.json").write_text(
        project.model_dump_json(),
        encoding="utf-8",
    )
    (state_root / "threads" / f"{thread_id}.json").write_text(
        thread.model_dump_json(),
        encoding="utf-8",
    )
    legacy_events = [
        {
            "event_id": "evt_legacy_1",
            "thread_id": thread_id,
            "sequence": 1,
            "event_type": "thread.created",
            "payload": {"title": "Legacy thread"},
            "timestamp": timestamp,
        },
        {
            "event_id": "evt_legacy_2",
            "thread_id": thread_id,
            "sequence": 2,
            "event_type": "message.completed",
            "payload": {"text": "Imported once"},
            "timestamp": "2026-07-12T10:01:00Z",
        },
    ]
    (state_root / "logs" / f"{thread_id}.events.jsonl").write_text(
        "".join(f"{json.dumps(event)}\n" for event in legacy_events),
        encoding="utf-8",
    )
    return thread_id


def test_storage_imports_legacy_jsonl_once_and_continues_v0_thread_sequence(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    workspace = tmp_path / "workspace"
    state_root.mkdir()
    workspace.mkdir()
    thread_id = _write_legacy_thread_state(state_root, workspace)

    first = BridgeStorage(root_path=state_root)
    assert [event.sequence for event in first.list_thread_events(thread_id)] == [1, 2]
    first.event_store.close()

    restarted = BridgeStorage(root_path=state_root)
    imported_again = restarted.list_thread_events(thread_id)
    appended = restarted.append_thread_event(
        thread_id=thread_id,
        event_type="message.delta",
        payload={"text": "Continues at three"},
    )

    assert [event.sequence for event in imported_again] == [1, 2]
    assert appended.sequence == 3
    assert [
        (event.sequence, event.event_type)
        for event in restarted.list_thread_events(thread_id, after=1)
    ] == [
        (2, "message.completed"),
        (3, "message.delta"),
    ]
    global_events = restarted.event_store.replay(
        after_cursor=0,
        scopes=("thread",),
        thread_ids=(thread_id,),
    ).events
    assert [event.event_type for event in global_events] == [
        "thread.created",
        "message.completed",
        "message.delta",
    ]
    restarted.event_store.close()


def test_thread_metadata_and_lifecycle_event_share_one_durable_operation(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    storage = BridgeStorage(root_path=state_root)

    thread = storage.create_thread(title="Created atomically", mode=RunMode.EDIT)

    created = next(
        event
        for event in storage.event_store.replay(
            after_cursor=0,
            scopes=("thread",),
            thread_ids=(thread.thread_id,),
        ).events
        if event.event_type == "thread.created"
    )
    state = json.loads(
        storage._thread_path(thread.thread_id).read_text(encoding="utf-8")
    )
    assert created.operation_id is not None
    assert state["_bridge_operation"]["operation_id"] == created.operation_id
    assert state["title"] == "Created atomically"
    storage.event_store.close()


def test_deleting_thread_purges_its_global_journal_payloads(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    storage = BridgeStorage(root_path=state_root)
    thread = storage.create_thread(title="Delete privately", mode=RunMode.EDIT)
    storage.append_thread_event(
        thread_id=thread.thread_id,
        event_type="message.created",
        payload={"text": "secret prompt that must not remain replayable"},
    )

    storage.delete_thread(thread.thread_id)

    with pytest.raises(EventCursorExpiredError):
        storage.event_store.replay(
            after_cursor=0,
            scopes=("thread",),
            thread_ids=(thread.thread_id,),
        )
    assert storage.event_store.replay_thread(
        thread.thread_id,
        after_sequence=2,
    ) == []
    storage.event_store.close()


class _LifecycleAppServer:
    generation = 1

    def __init__(self, observed: list[str]) -> None:
        self.observed = observed

    def start(self) -> None:
        self.observed.append("app-server-started")

    def close(self) -> None:
        self.observed.append("app-server-closed")

    def register_notification_handler(self, _method: str, _handler: object) -> None:
        pass

    def request(
        self,
        method: str,
        _params: object = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        del timeout_seconds
        assert method == "account/read"
        return {"account": None, "requiresOpenaiAuth": True}


class _ObservedRunner:
    def __init__(self, storage: BridgeStorage, observed: list[str]) -> None:
        self.storage = storage
        self.observed = observed
        self._assert_reconciled()
        observed.append("runner-constructed")

    def _assert_reconciled(self) -> None:
        marker = json.loads(
            (self.storage.root / "startup-state.json").read_text(encoding="utf-8")
        )
        assert marker["_bridge_operation"] == {
            "operation_id": "op-startup-reconcile",
            "revision": 1,
        }
        events = self.storage.event_store.replay(
            after_cursor=0,
            scopes=("runtime",),
        ).events
        assert [event.event_type for event in events] == ["runtime.prepared"]

    def start(self) -> None:
        self._assert_reconciled()
        self.observed.append("runner-started")

    def close(self) -> None:
        self.observed.append("runner-closed")


def test_app_reconciles_pending_outbox_before_constructing_and_starting_runner(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    workspace_root = tmp_path / "workspaces"
    state_root.mkdir()
    workspace_root.mkdir()
    database = state_root / "events.sqlite3"

    def crash_after_prepare(point: str) -> None:
        if point == "after_outbox_commit":
            raise InjectedOutboxCrash(point)

    prepared_store = BridgeEventStore(database)
    prepared_outbox = DurableOutbox(
        prepared_store,
        state_root=state_root,
        failure_injector=crash_after_prepare,
    )
    with pytest.raises(InjectedOutboxCrash):
        prepared_outbox.commit_json(
            operation_id="op-startup-reconcile",
            relative_path="startup-state.json",
            state_revision=1,
            state_payload={"prepared": True},
            event=EventDraft(
                scope="runtime",
                event_type="runtime.prepared",
                payload={"revision": 1},
            ),
        )
    prepared_store.close()

    observed: list[str] = []
    app = create_app(
        root_path=state_root,
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
        app_server_factory=lambda: _LifecycleAppServer(observed),
        runner_factory=lambda storage: _ObservedRunner(storage, observed),
    )

    assert observed == ["runner-constructed"]
    with TestClient(app):
        assert observed[:3] == [
            "runner-constructed",
            "app-server-started",
            "runner-started",
        ]


class _InertAppServer:
    generation = 1

    def register_notification_handler(self, _method: str, _handler: object) -> None:
        pass

    def register_request_handler(self, _method: str, _handler: object) -> None:
        pass


def _runtime_run(thread: ThreadRecord) -> RuntimeRunState:
    now = datetime.now(UTC).isoformat()
    prompt = "Persist this delta once"
    return RuntimeRunState(
        run_id="run_outbox_window",
        client_request_id="request-outbox-window",
        thread_id=thread.thread_id,
        prompt=prompt,
        prompt_fingerprint=runtime_fingerprint(prompt),
        mode=thread.mode,
        model=DEFAULT_MODEL,
        effort="medium",
        workspace_path=thread.workspace_path,
        attachment_manifest_fingerprint=runtime_fingerprint([]),
        codex_thread_id="codex-thread-1",
        codex_turn_id="turn-1",
        generation=1,
        status="running",
        created_at=now,
        last_activity_at=now,
    )


def _broker(storage: BridgeStorage) -> RuntimeBroker:
    limits = ResourceLimits()
    return RuntimeBroker(
        storage,
        _InertAppServer(),  # type: ignore[arg-type]
        RuntimeGate(limits=limits),
        resource_limits=limits,
    )


def test_broker_recovers_emitted_signature_and_event_from_one_outbox_operation(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    armed = False

    def inject(point: str) -> None:
        if armed and point == "after_outbox_commit":
            raise InjectedOutboxCrash(point)

    storage = BridgeStorage(
        root_path=state_root,
        outbox_failure_injector=inject,
    )
    thread = storage.create_thread(title="Outbox window", mode=RunMode.EDIT)
    broker = _broker(storage)
    run = _runtime_run(storage.load_thread(thread.thread_id))
    initial_revision = 10
    broker._state = RuntimeStateRecord(
        revision=initial_revision,
        observed_app_server_generation=1,
        runs={run.run_id: run},
    )
    broker._store.save(broker._state)
    source: dict[str, Any] = {
        "threadId": run.codex_thread_id,
        "turnId": run.codex_turn_id,
        "delta": "one durable delta",
    }
    payload = {"run_id": run.run_id, "text": "one durable delta"}

    armed = True
    with broker._lock, pytest.raises(InjectedOutboxCrash):
        broker._emit_once_locked(
            run,
            "message.delta",
            payload,
            source=source,
        )
    storage.event_store.close()

    recovered_storage = BridgeStorage(root_path=state_root)
    recovered_broker = _broker(recovered_storage)
    recovered_state = RuntimeStateStore(state_root).load()
    recovered_run = recovered_broker._state.runs[run.run_id]
    recovered_revision = recovered_state.revision

    assert recovered_revision == initial_revision + 1
    assert recovered_run.emitted_signatures
    assert [
        event.event_type
        for event in recovered_storage.list_thread_events(thread.thread_id)
        if event.event_type == "message.delta"
    ] == ["message.delta"]

    with recovered_broker._lock:
        recovered_broker._emit_once_locked(
            recovered_run,
            "message.delta",
            payload,
            source=source,
        )

    assert RuntimeStateStore(state_root).load().revision == recovered_revision
    assert [
        event.event_type
        for event in recovered_storage.list_thread_events(thread.thread_id)
        if event.event_type == "message.delta"
    ] == ["message.delta"]
    recovered_storage.event_store.close()


def test_prompt_acceptance_pairs_runtime_state_and_initial_events_in_one_operation(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    storage = BridgeStorage(root_path=state_root)
    thread = storage.create_thread(title="Queued atomically", mode=RunMode.EDIT)
    limits = ResourceLimits()
    gate = RuntimeGate(limits=limits)
    blocker = gate.reserve_prompt(client_request_id="hold-global-runtime")
    broker = RuntimeBroker(
        storage,
        _InertAppServer(),  # type: ignore[arg-type]
        gate,
        resource_limits=limits,
    )
    broker.start()
    try:
        run = broker.submit_prompt(
            thread.thread_id,
            "Queue this durably",
            client_request_id="paired-prompt-acceptance",
        )

        assert run.status == "queued"
        initial_events = [
            event
            for event in storage.event_store.replay(
                after_cursor=0,
                scopes=("thread",),
                thread_ids=(thread.thread_id,),
            ).events
            if event.event_type in {"message.created", "run.queued"}
            and event.payload.get("run_id") == run.run_id
        ]
        assert [event.event_type for event in initial_events] == [
            "message.created",
            "run.queued",
        ]
        assert initial_events[0].operation_id is not None
        assert {event.operation_id for event in initial_events} == {
            initial_events[0].operation_id
        }
        persisted = RuntimeStateStore(state_root).load()
        assert persisted.runs[run.run_id].status == "queued"
    finally:
        broker.close()
        blocker.release()
        gate.close()
        storage.event_store.close()


def test_terminal_runtime_event_and_thread_projection_share_one_operation(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    storage = BridgeStorage(root_path=state_root)
    thread = storage.create_thread(title="Terminal projection", mode=RunMode.EDIT)
    for index in range(3):
        storage.update_thread(thread.thread_id, title=f"Terminal projection {index}")
    broker = _broker(storage)
    run = _runtime_run(storage.load_thread(thread.thread_id))
    broker._state = RuntimeStateRecord(
        revision=2,
        observed_app_server_generation=1,
        runs={run.run_id: run},
    )
    broker._store.save(broker._state)

    with broker._lock:
        broker._terminalize_locked(run, "completed", None)

    terminal_event = next(
        event
        for event in storage.event_store.replay(
            after_cursor=0,
            scopes=("thread",),
            thread_ids=(thread.thread_id,),
        ).events
        if event.event_type == "run.completed"
        and event.payload.get("run_id") == run.run_id
    )
    thread_state = json.loads(
        storage._thread_path(thread.thread_id).read_text(encoding="utf-8")
    )
    assert terminal_event.operation_id is not None
    assert thread_state["_bridge_operation"]["operation_id"] == (
        terminal_event.operation_id
    )
    assert thread_state["status"] == "idle"
    assert thread_state["active_run_id"] is None
    storage.event_store.close()
