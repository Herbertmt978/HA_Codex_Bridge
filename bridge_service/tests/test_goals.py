from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from threading import Event, RLock
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from codex_bridge_service.event_store import BridgeEventStore, DurableOutbox, InjectedOutboxCrash, OutboxStateConflictError
from codex_bridge_service.goals import (
    MAX_GOAL_ACTIONS,
    MAX_GOAL_HISTORY,
    GoalAction,
    GoalError,
    GoalManager,
    GoalRevisionConflict,
    GoalSnapshotStale,
    GoalUnavailable,
    goal_context_text,
)
from codex_bridge_service.models import RuntimeProfile
from codex_bridge_service.routes import goals
from codex_bridge_service.storage import ThreadNotFoundError


class _Storage:
    runtime_profile = RuntimeProfile.HOME_ASSISTANT

    def __init__(self, root: Path):
        self.root = root
        self._thread_mutation_lock = RLock()
        self.event_store = BridgeEventStore(root / "events.sqlite3")
        self.durable_outbox = DurableOutbox(self.event_store, state_root=root)
        self.threads = {
            name: SimpleNamespace(thread_id=name, assist_origin=False, project_id="project-one",
                                  archived_at=None, workspace_path=f"workspaces/{name}")
            for name in ("chat-one", "chat-two")
        }
        self.project = SimpleNamespace(archived_at=None)
        self.goals = GoalManager(self)

    def load_thread(self, thread_id):
        if thread_id not in self.threads:
            raise ThreadNotFoundError(thread_id)
        return self.threads[thread_id]

    def load_project(self, _project_id):
        return self.project


@pytest.fixture
def storage(tmp_path):
    result = _Storage(tmp_path / "state")
    yield result
    result.event_store.close()


def _action(manager, action, *, thread_id="chat-one", **fields):
    revision = manager.get(thread_id).revision
    return manager.apply(thread_id, GoalAction(action=action, expected_revision=revision,
                                              client_request_id=f"{thread_id}-{action}-{revision}", **fields))


def _create(manager, **fields):
    return _action(manager, "create", objective="Repair the importer",
                   completion_criteria=["The malformed input is rejected", "Existing data is retained"], **fields)


def test_fresh_goal_storage_reads_empty_then_creates_and_reopens(storage):
    manager = storage.goals
    assert not (storage.root / "goals").exists()
    assert manager.get("chat-one").model_dump() == {
        "revision": 0, "goal": None, "history": [], "continuation": "manual_turns_only",
    }
    assert manager.capture("chat-one") is None
    created = _create(manager)
    assert created.goal.status == "paused"
    assert (storage.root / "goals" / "chat-one.json").is_file()
    assert GoalManager(storage).get("chat-one") == created
    assert manager.get("chat-two").goal is None


@pytest.mark.skipif(os.name == "nt", reason="Linux no-follow descriptor confinement")
@pytest.mark.parametrize("location", ["parent", "file", "dangling_file"])
def test_linux_goal_reads_reject_symlinks_without_following_or_creating_targets(storage, location):
    alternate = storage.root / "alternate"
    alternate.mkdir()
    sentinel = alternate / "chat-one.json"
    sentinel.write_text("private unchanged sentinel", encoding="utf-8")
    if location == "parent":
        (storage.root / "goals").symlink_to(alternate, target_is_directory=True)
        target = sentinel
    else:
        (storage.root / "goals").mkdir()
        target = sentinel if location == "file" else alternate / "missing.json"
        (storage.root / "goals" / "chat-one.json").symlink_to(target)
    with pytest.raises((OutboxStateConflictError, GoalUnavailable)):
        storage.goals.get("chat-one")
    with pytest.raises((OutboxStateConflictError, GoalUnavailable)):
        storage.goals.apply("chat-one", GoalAction(action="create", expected_revision=0,
            client_request_id="refuse-symlink", objective="Reviewed goal", completion_criteria=["Retain confinement"]))
    assert sentinel.read_text(encoding="utf-8") == "private unchanged sentinel"
    if location == "dangling_file":
        assert not target.exists()


def test_goal_lifecycle_cold_readback_fences_old_activation_and_resume_replay(storage):
    manager = storage.goals
    paused = _create(manager, progress="Reproduction recorded")
    assert paused.goal.status == "paused"
    assert manager.capture("chat-one") is None
    _action(manager, "resume")
    snapshot = manager.capture("chat-one")
    _action(manager, "progress", progress="Fix checked locally")
    manager.validate_snapshot("chat-one", snapshot)
    assert snapshot.progress == "Reproduction recorded"
    reopened = GoalManager(storage)
    assert reopened.get("chat-one").goal.progress == "Fix checked locally"
    assert reopened.get("chat-one").goal.completion_criteria == paused.goal.completion_criteria
    _action(manager, "pause")
    with pytest.raises(GoalSnapshotStale):
        reopened.validate_snapshot("chat-one", snapshot)
    resume = GoalAction(action="resume", expected_revision=4, client_request_id="resume-once")
    manager.apply("chat-one", resume)
    with pytest.raises(GoalSnapshotStale):
        manager.validate_snapshot("chat-one", snapshot)
    _action(manager, "cancel")
    assert reopened.apply("chat-one", resume).goal.status == "cancelled"
    with pytest.raises(GoalError):
        _action(manager, "resume")
    assert reopened.get("chat-two").goal is None


def test_accepted_snapshot_is_frozen_and_criteria_do_not_alias_state(storage):
    manager = storage.goals
    created = _create(manager)
    _action(manager, "resume")
    snapshot = manager.capture("chat-one")
    expected = tuple(created.goal.completion_criteria)
    created.goal.completion_criteria[0] = "Changed returned readback"
    assert snapshot.completion_criteria == expected
    with pytest.raises(ValidationError):
        snapshot.objective = "Changed accepted objective"
    with pytest.raises(TypeError):
        snapshot.completion_criteria[0] = "Changed accepted criterion"
    restored = type(snapshot).model_validate_json(snapshot.model_dump_json())
    assert restored.completion_criteria == expected
    assert isinstance(restored.completion_criteria, tuple)


def test_explicit_completion_and_paused_edit_are_required(storage):
    manager = storage.goals
    _create(manager)
    _action(manager, "resume")
    with pytest.raises(GoalError, match="Pause"):
        _action(manager, "edit", objective="A different objective")
    # Transcript text and native reported completion do not own goal completion.
    storage.event_store.append(scope="thread", thread_id="chat-one", event_type="message.completed",
                               payload={"role": "assistant", "text": "The goal is complete"}, operation_key="native-prose")
    assert manager.get("chat-one").goal.status == "active"
    with pytest.raises(ValidationError):
        GoalAction(action="complete", expected_revision=2, client_request_id="complete-without-confirmation")
    completed = _action(manager, "complete", completion_confirmed=True)
    assert completed.goal.status == "completed"
    assert manager.capture("chat-one") is None
    _create(manager)
    assert manager.get("chat-one").history[0].status == "completed"


def test_revision_concurrency_and_reused_action_identity_do_not_overwrite(storage):
    manager = storage.goals
    _create(manager)
    actions = [GoalAction(action="progress", expected_revision=1,
                          client_request_id=f"concurrent-{index}", progress=f"Update {index}") for index in range(2)]
    def update(action):
        try:
            return manager.apply("chat-one", action)
        except GoalRevisionConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(update, actions))
    assert sum(result is not None for result in outcomes) == 1
    winner = next(action for action in actions if action.progress == manager.get("chat-one").goal.progress)
    with pytest.raises(GoalRevisionConflict):
        manager.apply("chat-one", winner.model_copy(update={"progress": "Forged new content"}))


def test_goal_history_receipts_and_utf8_are_bounded(storage):
    manager = storage.goals
    _create(manager)
    for index in range(MAX_GOAL_ACTIONS + 1):
        _action(manager, "progress", progress=str(index))
    assert len(manager._load("chat-one").actions) == MAX_GOAL_ACTIONS
    for _index in range(MAX_GOAL_HISTORY + 2):
        _action(manager, "cancel")
        _create(manager)
    assert len(manager.get("chat-one").history) == MAX_GOAL_HISTORY
    with pytest.raises(ValidationError):
        _action(manager, "progress", progress="😀" * 8192)
    with pytest.raises(ValidationError):
        _action(manager, "edit", completion_criteria=["  "])


def test_crash_reconciles_goal_and_event_together(storage):
    manager = storage.goals
    def crash(point):
        if point == "after_state_replace":
            raise InjectedOutboxCrash()
    storage.durable_outbox.failure_injector = crash
    with pytest.raises(InjectedOutboxCrash):
        _create(manager)
    storage.durable_outbox.failure_injector = None
    assert GoalManager(storage).get("chat-one").goal.objective == "Repair the importer"
    events = storage.event_store.replay_thread("chat-one")
    assert len(events) == 1
    assert events[0].event_type == "goal.updated"
    assert events[0].payload == {"revision": 1, "status": "paused"}


def test_goal_scope_archive_corruption_and_deletion_fail_closed(storage):
    manager = storage.goals
    _create(manager)
    _action(manager, "resume")
    snapshot = manager.capture("chat-one")
    storage.threads["chat-one"].workspace_path = "workspaces/another"
    with pytest.raises(GoalSnapshotStale):
        manager.validate_snapshot("chat-one", snapshot)
    storage.threads["chat-one"].assist_origin = True
    with pytest.raises(GoalUnavailable):
        manager.get("chat-one")
    storage.threads["chat-one"].assist_origin = False
    storage.project.archived_at = "2026-09-27T09:00:00Z"
    with pytest.raises(GoalError):
        _action(manager, "pause")
    storage.project.archived_at = None
    manager.delete_thread("chat-one")
    assert manager.get("chat-one").goal is None
    (storage.root / "goals" / "chat-one.json").write_text("[]", encoding="utf-8")
    with pytest.raises(GoalUnavailable):
        manager.get("chat-one")


def test_pause_is_serialised_against_the_accepted_dispatch_boundary(storage):
    manager = storage.goals
    _create(manager)
    _action(manager, "resume")
    snapshot = manager.capture("chat-one")
    attempted = Event()
    def pause():
        attempted.set()
        return _action(manager, "pause")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with manager.dispatch_guard("chat-one", snapshot):
            result = pool.submit(pause)
            assert attempted.wait(timeout=2)
            assert not result.done()
        assert result.result(timeout=2).goal.status == "paused"
    with pytest.raises(GoalSnapshotStale):
        with manager.dispatch_guard("chat-one", snapshot):
            pytest.fail("a stale snapshot must not reach the dispatch boundary")


def test_context_is_visible_user_data_and_cannot_configure_permissions(storage):
    _create(storage.goals)
    _action(storage.goals, "resume")
    text = goal_context_text(storage.goals.capture("chat-one"))
    assert "user-provided task data" in text
    assert "grants no permissions" in text
    assert "Repair the importer" in text
    with pytest.raises(ValidationError):
        GoalAction(action="resume", expected_revision=2, client_request_id="escalation", mode="full_auto")


def test_public_routes_auth_capability_validation_and_no_runtime_start(storage):
    app = FastAPI()
    app.state.storage = storage
    app.state.auth_token = "secret"
    app.state.feature_capabilities = ("durable_goals_v1",)
    app.include_router(goals.router)
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}
    with TestClient(app) as client:
        assert client.get("/threads/chat-one/goal").status_code == 401
        assert client.get("/threads/unknown/goal", headers=headers).status_code == 404
        payload = {"action": "create", "expected_revision": 0, "client_request_id": "create-public",
                   "objective": "Reviewed objective", "completion_criteria": ["Verified by user"]}
        created = client.post("/threads/chat-one/goal/actions", headers=headers, json=payload)
        assert created.status_code == 200
        assert created.json()["goal"]["status"] == "paused"
        assert client.post("/threads/chat-one/goal/actions", headers=headers, json={
            "action": "resume", "expected_revision": 1, "client_request_id": "resume-public",
        }).json()["goal"]["status"] == "active"
        invalid = client.post("/threads/chat-one/goal/actions", headers=headers, json={
            "action": "complete", "expected_revision": 2, "client_request_id": "unconfirmed",
        })
        assert invalid.status_code == 422
        assert invalid.json()["detail"]["code"] == "goal_invalid"
        assert client.post("/threads/chat-one/goal/actions", headers=headers, json=payload).json()["revision"] == 2
        app.state.feature_capabilities = ()
        assert client.get("/threads/chat-one/goal", headers=headers).status_code == 503
    # No runner was installed: create/resume/complete/cancel cannot submit work.
    assert not hasattr(app.state, "runner")
