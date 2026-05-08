from codex_bridge_service.models import (
    ArtifactRecord,
    AttachmentRecord,
    RunRecord,
    RunMode,
    ThreadEventRecord,
    ThreadRecord,
)


def test_thread_record_round_trips() -> None:
    record = ThreadRecord(
        thread_id="thr_123",
        title="First thread",
        workspace_id="ws_123",
        workspace_path="C:/CodexHA/workspaces/ws_123",
        status="idle",
        mode=RunMode.FULL_AUTO,
        codex_session_id="019e08fb-92dc-7920-88f3-9fc949d1aef8",
        active_run_id=None,
        last_error=None,
        attachments=[
            AttachmentRecord(
                attachment_id="att_1",
                filename="notes.txt",
                mime_type="text/plain",
                stored_path="C:/CodexHA/uploads/thr_123/notes.txt",
            )
        ],
        artifacts=[],
    )

    payload = record.model_dump()
    restored = ThreadRecord.model_validate(payload)

    assert restored.thread_id == "thr_123"
    assert restored.title == "First thread"
    assert restored.workspace_id == "ws_123"
    assert restored.workspace_path == "C:/CodexHA/workspaces/ws_123"
    assert restored.status == "idle"
    assert restored.mode is RunMode.FULL_AUTO
    assert restored.codex_session_id == "019e08fb-92dc-7920-88f3-9fc949d1aef8"
    assert restored.active_run_id is None
    assert restored.last_error is None
    assert len(restored.attachments) == 1
    assert restored.attachments[0].filename == "notes.txt"
    assert restored.attachments[0].attachment_id == "att_1"
    assert restored.artifacts == []


def test_thread_record_round_trips_nested_artifacts() -> None:
    record = ThreadRecord(
        thread_id="thr_456",
        title="Artifact thread",
        workspace_id="ws_456",
        workspace_path="C:/CodexHA/workspaces/ws_456",
        status="running",
        mode=RunMode.EDIT,
        codex_session_id="019e08fc-b413-7003-b0d5-fcb0bf29a11c",
        active_run_id="run_123",
        last_error="tool failed",
        attachments=[
            AttachmentRecord(
                attachment_id="att_2",
                filename="diagram.png",
                mime_type="image/png",
                stored_path="C:/CodexHA/uploads/thr_456/diagram.png",
            )
        ],
        artifacts=[
            ArtifactRecord(
                artifact_id="art_1",
                filename="report.md",
                mime_type="text/markdown",
                stored_path="C:/CodexHA/artifacts/thr_456/report.md",
            )
        ],
    )

    payload = record.model_dump()

    assert payload["mode"] == "edit"
    assert payload["attachments"][0]["stored_path"] == "C:/CodexHA/uploads/thr_456/diagram.png"
    assert payload["artifacts"][0]["artifact_id"] == "art_1"

    restored = ThreadRecord.model_validate(payload)

    assert restored.mode is RunMode.EDIT
    assert restored.codex_session_id == "019e08fc-b413-7003-b0d5-fcb0bf29a11c"
    assert restored.active_run_id == "run_123"
    assert restored.last_error == "tool failed"
    assert restored.attachments[0].mime_type == "image/png"
    assert restored.artifacts[0].artifact_id == "art_1"
    assert restored.artifacts[0].filename == "report.md"
    assert restored.artifacts[0].stored_path == "C:/CodexHA/artifacts/thr_456/report.md"


def test_thread_event_record_round_trips_payload() -> None:
    record = ThreadEventRecord(
        event_id="evt_1",
        thread_id="thr_123",
        sequence=2,
        event_type="attachment.added",
        payload={
            "attachment_id": "att_1",
            "filename": "notes.txt",
        },
        timestamp="2026-05-08T18:40:00Z",
    )

    payload = record.model_dump()
    restored = ThreadEventRecord.model_validate(payload)

    assert restored.event_id == "evt_1"
    assert restored.thread_id == "thr_123"
    assert restored.sequence == 2
    assert restored.event_type == "attachment.added"
    assert restored.payload["filename"] == "notes.txt"
    assert restored.timestamp == "2026-05-08T18:40:00Z"


def test_run_record_round_trips() -> None:
    record = RunRecord(
        run_id="run_123",
        thread_id="thr_123",
        status="running",
    )

    payload = record.model_dump()
    restored = RunRecord.model_validate(payload)

    assert restored.run_id == "run_123"
    assert restored.thread_id == "thr_123"
    assert restored.status == "running"
