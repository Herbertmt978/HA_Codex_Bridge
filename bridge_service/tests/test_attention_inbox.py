from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
from typing import Any

from fastapi.testclient import TestClient

from codex_bridge_service.app import create_app
from codex_bridge_service.event_store import BridgeEventStore
from codex_bridge_service.models import RunMode, RuntimeProfile
from codex_bridge_service.runtime_broker import RuntimeUnavailableError


AUTHORIZATION = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}


def test_attention_lifecycle_survives_compaction_restart_and_is_removed_with_chat(tmp_path):
    database = tmp_path / "events.sqlite3"
    store = BridgeEventStore(database, max_events_per_thread=2)
    store.append(scope="thread",
        thread_id="thread-one", event_type="run.completed",
        payload={"run_id": "run-one"}, operation_key="completion",
    )
    for index in range(4):
        store.append(scope="thread",
            thread_id="thread-one", event_type="codex.event",
            payload={"text": "Tool output must not enter attention"},
            operation_key=f"tool-{index}",
        )
    assert store.attention_lifecycle().items[0].run_id == "run-one"
    store.close()
    reopened = BridgeEventStore(database, max_events_per_thread=2)
    assert reopened.attention_lifecycle().items[0].run_id == "run-one"
    reopened.append(scope="thread",
        thread_id="thread-one", event_type="run.started",
        payload={"run_id": "run-two"}, operation_key="next-run",
    )
    assert reopened.attention_lifecycle().items == ()
    reopened.append(scope="thread",
        thread_id="thread-one", event_type="run.failed",
        payload={"run_id": "run-two"}, operation_key="failed-run",
    )
    assert reopened.attention_lifecycle().items[0].event_type == "run.failed"
    reopened.purge_thread("thread-one")
    assert reopened.attention_lifecycle().items == ()
    reopened.close()


class _NoopLifecycle:
    def start(self) -> None:
        pass

    def close(self) -> None:
        pass


class _Broker:
    def __init__(self, interactions: list[dict[str, Any]] | None = None) -> None:
        self.interactions = interactions or []
        self.calls = 0
        self.error: Exception | None = None

    def list_pending_interactions(self) -> list[dict[str, Any]]:
        self.calls += 1
        if self.error:
            raise self.error
        now = datetime.now(UTC)
        return [
            item for item in self.interactions
            if item["status"] == "pending"
            and datetime.fromisoformat(item["expires_at"].replace("Z", "+00:00")) > now
        ]


def _interaction(thread_id: str, interaction_id: str = "pending-one") -> dict[str, Any]:
    return {
        "interaction_id": interaction_id,
        "kind": "command_approval",
        "thread_id": thread_id,
        "event_id": 12,
        "status": "pending",
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "display": {
            "title": "Private command title",
            "summary": "Private command summary",
            "command": "echo private-command",
            "workspace_paths": ["secret/file.txt"],
        },
        "allowed_actions": ["accept", "decline", "cancel"],
        "authorization_url": "https://private.invalid/auth?token=secret",
    }


def _app(tmp_path: Path, broker: _Broker):
    workspace = tmp_path / "workspaces"
    workspace.mkdir()
    app = create_app(
        root_path=tmp_path / "state",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace,
        app_server_factory=_NoopLifecycle,
        auth_coordinator_factory=lambda _client: _NoopLifecycle(),
        runner_factory=lambda _storage: broker,
    )
    return app


def _chat(app, project_name: str, title: str):
    storage = app.state.storage
    project = storage.create_project(name=project_name)
    thread = storage.create_thread(title=title, mode=RunMode.OBSERVE, project_id=project.project_id)
    return project, thread


def _run(app, thread_id: str, event_type: str, run_id: str, *, secret: str | None = None):
    payload: dict[str, Any] = {"run_id": run_id}
    if secret:
        payload.update({"error": secret, "tool_output": secret, "authorization_url": secret})
    return app.state.storage.event_store.append(
        operation_key=f"attention:{thread_id}:{run_id}:{event_type}",
        scope="thread",
        thread_id=thread_id,
        event_type=event_type,
        payload=payload,
        timestamp=datetime.now(UTC).isoformat(),
    )


def test_inbox_is_cross_project_and_independent_of_unread_and_open_state(tmp_path: Path) -> None:
    broker = _Broker()
    app = _app(tmp_path, broker)
    with TestClient(app) as client:
        first_project, first = _chat(app, "Project One", "First chat")
        second_project, second = _chat(app, "Project Two", "Second chat")
        _run(app, first.thread_id, "run.started", "run-1")
        _run(app, first.thread_id, "run.completed", "run-1")
        _run(app, second.thread_id, "run.started", "run-2")
        _run(app, second.thread_id, "run.failed", "run-2", secret="do-not-return-this")
        broker.interactions = [_interaction(first.thread_id)]

        initial = client.get("/attention", headers=AUTHORIZATION)
        # Reading/opening the chat changes unread state only, not inbox lifecycle.
        app.state.storage.update_thread(
            first.thread_id, unread=False,
            navigation_revision=first.navigation_revision,
        )
        opened = client.get("/attention", headers=AUTHORIZATION)

    assert initial.status_code == opened.status_code == 200
    assert broker.calls == 2  # one global call per request, with no per-chat fan-out
    rows = initial.json()["items"]
    assert {(row["project_id"], row["kind"]) for row in rows} == {
        (first_project.project_id, "interaction"),
        (first_project.project_id, "review"),
        (second_project.project_id, "failure"),
    }
    assert [row["attention_id"] for row in opened.json()["items"]] == [
        row["attention_id"] for row in rows
    ]
    assert "do-not-return-this" not in initial.text
    assert "private-command" not in initial.text
    assert "private.invalid" not in initial.text


def test_latest_run_replaces_completion_and_failure_without_ack_state(tmp_path: Path) -> None:
    broker = _Broker()
    app = _app(tmp_path, broker)
    with TestClient(app) as client:
        _project, thread = _chat(app, "Lifecycle", "State")
        _run(app, thread.thread_id, "run.started", "run-1")
        _run(app, thread.thread_id, "run.completed", "run-1")
        first = client.get("/attention", headers=AUTHORIZATION).json()["items"]
        assert any(item["kind"] == "review" for item in first)

        _run(app, thread.thread_id, "run.started", "run-2")
        running = client.get("/attention", headers=AUTHORIZATION).json()["items"]
        assert not any(item["kind"] == "review" for item in running)
        _run(app, thread.thread_id, "run.failed", "run-2")
        failed = client.get("/attention", headers=AUTHORIZATION).json()["items"]
        assert [item["kind"] for item in failed] == ["failure"]

        _run(app, thread.thread_id, "run.started", "run-3")
        replaced = client.get("/attention", headers=AUTHORIZATION).json()["items"]
        assert replaced == []
        _run(app, thread.thread_id, "run.completed", "run-3")
        latest = client.get("/attention", headers=AUTHORIZATION).json()["items"]
        assert [item["attention_id"] for item in latest] == [
            f"run:{thread.thread_id}:run-3:review"
        ]


def test_pending_items_disappear_only_when_broker_resolves_or_expires_them(tmp_path: Path) -> None:
    broker = _Broker()
    app = _app(tmp_path, broker)
    with TestClient(app) as client:
        _project, thread = _chat(app, "Actions", "Approval")
        item = _interaction(thread.thread_id)
        broker.interactions = [item]
        assert client.get("/attention", headers=AUTHORIZATION).json()["items"]

        # Simulate authoritative resolution. Merely opening/reading cannot ack it.
        current_thread = app.state.storage.get_thread(thread.thread_id)
        app.state.storage.update_thread(
            thread.thread_id, unread=False,
            navigation_revision=current_thread.navigation_revision,
        )
        assert client.get("/attention", headers=AUTHORIZATION).json()["items"]
        item["status"] = "accepted"
        assert client.get("/attention", headers=AUTHORIZATION).json()["items"] == []

        expired = _interaction(thread.thread_id, "expired")
        expired["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        broker.interactions = [expired]
        assert client.get("/attention", headers=AUTHORIZATION).json()["items"] == []


def test_archived_rows_remain_marked_and_deleted_threads_are_excluded(tmp_path: Path) -> None:
    broker = _Broker()
    app = _app(tmp_path, broker)
    with TestClient(app) as client:
        project, archived = _chat(app, "Archived", "Archived chat")
        _run(app, archived.thread_id, "run.started", "run-1")
        _run(app, archived.thread_id, "run.completed", "run-1")
        app.state.storage.archive_thread(archived.thread_id)
        project_rows = client.get("/attention", headers=AUTHORIZATION).json()["items"]
        assert len(project_rows) == 1 and project_rows[0]["archived"] is True

        deleted_project, deleted = _chat(app, "Deleted", "Removed chat")
        broker.interactions = [_interaction(deleted.thread_id, "orphan")]
        app.state.storage.delete_thread(deleted.thread_id)
        rows = client.get("/attention", headers=AUTHORIZATION).json()["items"]
        assert all(row["thread_id"] != deleted.thread_id for row in rows)
        assert deleted_project.project_id != project.project_id


def test_unavailable_broker_returns_fixed_safe_error(tmp_path: Path) -> None:
    broker = _Broker()
    broker.error = RuntimeUnavailableError()
    app = _app(tmp_path, broker)
    with TestClient(app) as client:
        response = client.get("/attention", headers=AUTHORIZATION)
    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "attention_unavailable", "retryable": True}
    assert "secret internal diagnostic" not in response.text


def test_lifecycle_query_is_capped_and_reports_truncation(tmp_path: Path) -> None:
    broker = _Broker()
    app = _app(tmp_path, broker)
    with TestClient(app) as client:
        for index in range(201):
            _project, thread = _chat(app, f"Project {index}", f"Chat {index}")
            _run(app, thread.thread_id, "run.started", f"run-{index}")
            _run(app, thread.thread_id, "run.completed", f"run-{index}")
        response = client.get("/attention", headers=AUTHORIZATION)
    assert response.status_code == 200
    assert len(response.json()["items"]) == 200
    assert response.json()["truncated"] is True


def test_route_requires_authentication(tmp_path: Path) -> None:
    app = _app(tmp_path, _Broker())
    with TestClient(app) as client:
        response = client.get("/attention")
    assert response.status_code == 401


def test_upgrade_seeds_latest_retained_lifecycle_without_resurrecting_cancellation(tmp_path):
    database = tmp_path / "events.sqlite3"
    store = BridgeEventStore(database)
    for thread_id, outcome in (("completed", "run.completed"), ("cancelled", "run.cancelled")):
        store.append(scope="thread", thread_id=thread_id, event_type="run.started",
                     payload={"run_id": thread_id}, operation_key=f"start-{thread_id}")
        store.append(scope="thread", thread_id=thread_id, event_type=outcome,
                     payload={"run_id": thread_id}, operation_key=f"end-{thread_id}")
    store.close()
    # Emulate the pre-projection schema while retaining the existing journal.
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE thread_run_lifecycle")
    upgraded = BridgeEventStore(database)
    assert [item.thread_id for item in upgraded.attention_lifecycle().items] == ["completed"]
    upgraded.close()
