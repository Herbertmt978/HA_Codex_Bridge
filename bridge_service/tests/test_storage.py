import json
from pathlib import Path

import pytest

from codex_bridge_service.models import RunMode
from codex_bridge_service.storage import BridgeStorage


def test_create_project_persists_defaults_and_root_path(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)

    project = storage.create_project(
        name="HA Workspace",
        root_path=str(tmp_path / "vm-projects" / "ha"),
        default_model="gpt-5.4",
        default_thinking_level="medium",
    )

    saved_path = tmp_path / "projects" / f"{project.project_id}.json"
    payload = json.loads(saved_path.read_text(encoding="utf-8"))

    assert project.project_id.startswith("prj_")
    assert saved_path.exists()
    assert Path(project.root_path).exists()
    assert payload["name"] == "HA Workspace"
    assert payload["default_model"] == "gpt-5.4"
    assert payload["default_thinking_level"] == "medium"


def test_create_thread_persists_project_metadata_and_defaults(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    project = storage.create_project(
        name="Bridge MVP",
        root_path=str(tmp_path / "projects" / "bridge-mvp"),
        default_model="gpt-5.4",
        default_thinking_level="medium",
    )

    record = storage.create_thread(
        title="Bridge MVP",
        mode=RunMode.FULL_AUTO,
        project_id=project.project_id,
    )

    saved_path = tmp_path / "threads" / f"{record.thread_id}.json"
    payload = json.loads(saved_path.read_text(encoding="utf-8"))
    events = storage.list_thread_events(record.thread_id)

    assert record.project_id == project.project_id
    assert record.project_name == "Bridge MVP"
    assert record.workspace_path == str(tmp_path / "projects" / "bridge-mvp")
    assert record.default_model == "gpt-5.4"
    assert record.default_thinking_level == "medium"
    assert record.effective_model == "gpt-5.4"
    assert record.effective_thinking_level == "medium"
    assert payload["project_id"] == project.project_id
    assert payload["workspace_path"] == str(tmp_path / "projects" / "bridge-mvp")
    assert payload["model_override"] is None
    assert payload["thinking_override"] is None
    assert events[0].event_type == "thread.created"
    assert events[0].payload["project_id"] == project.project_id


def test_create_thread_rejects_blank_title(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    project = storage.create_project(
        name="Default",
        root_path=str(tmp_path / "projects" / "default"),
        default_model="gpt-5.4",
        default_thinking_level="medium",
    )

    with pytest.raises(ValueError, match="title must not be blank"):
        storage.create_thread(title="   ", mode=RunMode.FULL_AUTO, project_id=project.project_id)


def test_attach_file_persists_content_metadata_and_event(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    project = storage.create_project(
        name="Bridge MVP",
        root_path=str(tmp_path / "projects" / "bridge-mvp"),
        default_model="gpt-5.4",
        default_thinking_level="medium",
    )
    record = storage.create_thread(title="Bridge MVP", mode=RunMode.FULL_AUTO, project_id=project.project_id)

    attachment = storage.attach_file(
        thread_id=record.thread_id,
        filename="../notes.txt",
        mime_type="text/plain",
        content=b"hello from codex",
    )

    saved_path = tmp_path / "threads" / f"{record.thread_id}.json"
    payload = json.loads(saved_path.read_text(encoding="utf-8"))
    events = storage.list_thread_events(record.thread_id)
    attachment_path = tmp_path / "uploads" / record.thread_id / "notes.txt"

    assert attachment.filename == "notes.txt"
    assert attachment.mime_type == "text/plain"
    assert attachment.stored_path == str(attachment_path)
    assert attachment_path.read_bytes() == b"hello from codex"
    assert payload["attachments"][0]["attachment_id"] == attachment.attachment_id
    assert payload["attachments"][0]["filename"] == "notes.txt"
    assert payload["attachments"][0]["mime_type"] == "text/plain"
    assert payload["attachments"][0]["stored_path"] == str(attachment_path)
    assert [event.event_type for event in events] == [
        "thread.created",
        "attachment.added",
    ]
    assert events[1].sequence == 2
    assert events[1].payload["filename"] == "notes.txt"


def test_list_threads_sync_artifacts_and_update_overrides(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    project = storage.create_project(
        name="Second",
        root_path=str(tmp_path / "projects" / "second"),
        default_model="gpt-5.4",
        default_thinking_level="medium",
    )
    first = storage.create_thread(title="First", mode=RunMode.FULL_AUTO, project_id=project.project_id)
    second = storage.create_thread(title="Second", mode=RunMode.EDIT, project_id=project.project_id)

    artifact_path = tmp_path / "projects" / "second" / "report.md"
    artifact_path.write_text("# Report\n", encoding="utf-8")

    storage.update_thread(
        second.thread_id,
        model_override="gpt-5.5",
        thinking_override="high",
    )
    artifacts = storage.sync_thread_artifacts(second.thread_id)
    listed_threads = storage.list_threads()
    events = storage.list_thread_events(second.thread_id)
    resolved_second = storage.get_thread(second.thread_id)

    assert [thread.thread_id for thread in listed_threads] == [second.thread_id, first.thread_id]
    assert len(artifacts) == 1
    assert artifacts[0].filename == "report.md"
    assert artifacts[0].stored_path == str(artifact_path)
    assert resolved_second.effective_model == "gpt-5.5"
    assert resolved_second.effective_thinking_level == "high"
    assert events[-1].event_type == "artifact.added"
    assert events[-1].payload["artifact_id"] == artifacts[0].artifact_id


def test_legacy_thread_records_are_assigned_to_imported_project(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    legacy_workspace = tmp_path / "workspaces" / "ws_legacy"
    legacy_workspace.mkdir(parents=True, exist_ok=True)
    legacy_payload = {
        "thread_id": "thr_legacy",
        "title": "Legacy thread",
        "workspace_id": "ws_legacy",
        "workspace_path": str(legacy_workspace),
        "status": "idle",
        "mode": "full-auto",
        "codex_session_id": None,
        "active_run_id": None,
        "last_error": None,
        "attachments": [],
        "artifacts": [],
    }
    (tmp_path / "threads" / "thr_legacy.json").write_text(
        json.dumps(legacy_payload, indent=2),
        encoding="utf-8",
    )

    record = storage.get_thread("thr_legacy")
    projects = storage.list_projects()

    assert record.project_name == "Imported Threads"
    assert record.project_id == projects[0].project_id
    assert record.workspace_path == str(legacy_workspace)
