import json

import pytest

from codex_bridge_service.models import RunMode
from codex_bridge_service.storage import BridgeStorage


def test_create_thread_persists_metadata(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    record = storage.create_thread(title="Bridge MVP", mode=RunMode.FULL_AUTO)
    saved_path = tmp_path / "threads" / f"{record.thread_id}.json"
    payload = json.loads(saved_path.read_text(encoding="utf-8"))
    events = storage.list_thread_events(record.thread_id)

    assert record.thread_id.startswith("thr_")
    assert saved_path.exists()
    assert (tmp_path / "workspaces" / record.workspace_id).exists()
    assert payload["thread_id"] == record.thread_id
    assert payload["workspace_id"] == record.workspace_id
    assert payload["title"] == "Bridge MVP"
    assert payload["mode"] == "full-auto"
    assert payload["status"] == "idle"
    assert payload["workspace_path"] == record.workspace_path
    assert events[0].event_type == "thread.created"
    assert events[0].sequence == 1
    assert events[0].payload["workspace_id"] == record.workspace_id


def test_create_thread_rejects_blank_title(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)

    with pytest.raises(ValueError, match="title must not be blank"):
        storage.create_thread(title="   ", mode=RunMode.FULL_AUTO)


def test_attach_file_persists_content_metadata_and_event(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    record = storage.create_thread(title="Bridge MVP", mode=RunMode.FULL_AUTO)

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


def test_list_threads_and_sync_artifacts_persist_metadata_and_events(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    first = storage.create_thread(title="First", mode=RunMode.FULL_AUTO)
    second = storage.create_thread(title="Second", mode=RunMode.EDIT)

    artifact_path = tmp_path / "workspaces" / second.workspace_id / "report.md"
    artifact_path.write_text("# Report\n", encoding="utf-8")

    artifacts = storage.sync_thread_artifacts(second.thread_id)
    listed_threads = storage.list_threads()
    events = storage.list_thread_events(second.thread_id)

    assert [thread.thread_id for thread in listed_threads] == [second.thread_id, first.thread_id]
    assert len(artifacts) == 1
    assert artifacts[0].filename == "report.md"
    assert artifacts[0].stored_path == str(artifact_path)
    assert events[-1].event_type == "artifact.added"
    assert events[-1].payload["artifact_id"] == artifacts[0].artifact_id
