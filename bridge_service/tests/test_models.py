from codex_bridge_service.models import (
    ArtifactRecord,
    AttachmentRecord,
    BridgeDiagnosticsRecord,
    CodexAccountRecord,
    DiagnosticToolRecord,
    LimitsStatusRecord,
    LimitsWindowRecord,
    ProjectRecord,
    RunRecord,
    RunMode,
    ThreadEventRecord,
    ThreadRecord,
    ThreadViewRecord,
)


def test_thread_record_round_trips() -> None:
    record = ThreadRecord(
        thread_id="thr_123",
        project_id="prj_123",
        title="First thread",
        workspace_id="ws_123",
        workspace_path="C:/CodexHA/projects/project-a",
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
        model_override=None,
        thinking_override=None,
    )

    payload = record.model_dump()
    restored = ThreadRecord.model_validate(payload)

    assert restored.thread_id == "thr_123"
    assert restored.project_id == "prj_123"
    assert restored.title == "First thread"
    assert restored.workspace_id == "ws_123"
    assert restored.workspace_path == "C:/CodexHA/projects/project-a"
    assert restored.status == "idle"
    assert restored.mode is RunMode.FULL_AUTO
    assert restored.codex_session_id == "019e08fb-92dc-7920-88f3-9fc949d1aef8"
    assert restored.active_run_id is None
    assert restored.last_error is None
    assert restored.model_override is None
    assert restored.thinking_override is None
    assert len(restored.attachments) == 1
    assert restored.attachments[0].filename == "notes.txt"
    assert restored.attachments[0].attachment_id == "att_1"
    assert restored.artifacts == []


def test_thread_view_record_round_trips_effective_settings() -> None:
    record = ThreadViewRecord(
        thread_id="thr_456",
        project_id="prj_456",
        project_name="Artifact thread",
        project_root_path="C:/CodexHA/projects/artifacts",
        title="Artifact thread",
        workspace_id="ws_456",
        workspace_path="C:/CodexHA/projects/artifacts",
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
        model_override="gpt-5.5",
        thinking_override="high",
        default_model="gpt-5.4",
        default_thinking_level="medium",
        effective_model="gpt-5.5",
        effective_thinking_level="high",
    )

    payload = record.model_dump()
    restored = ThreadViewRecord.model_validate(payload)

    assert payload["mode"] == "edit"
    assert restored.project_name == "Artifact thread"
    assert restored.project_root_path == "C:/CodexHA/projects/artifacts"
    assert restored.default_model == "gpt-5.4"
    assert restored.default_thinking_level == "medium"
    assert restored.effective_model == "gpt-5.5"
    assert restored.effective_thinking_level == "high"
    assert restored.artifacts[0].artifact_id == "art_1"


def test_project_record_and_limits_round_trip() -> None:
    project = ProjectRecord(
        project_id="prj_1",
        name="Home Assistant",
        root_path="C:/Projects/HomeAssistant",
        default_model="gpt-5.4",
        default_thinking_level="medium",
        created_at="2026-05-08T18:40:00Z",
        updated_at="2026-05-08T18:40:00Z",
    )
    limits = LimitsStatusRecord(
        available=True,
        blocked=False,
        message=None,
        primary=LimitsWindowRecord(
            used_percent=12.5,
            remaining_percent=87.5,
            window_minutes=300,
            resets_at=1778302800,
        ),
        secondary=LimitsWindowRecord(
            used_percent=44.0,
            remaining_percent=56.0,
            window_minutes=10080,
            resets_at=1778907600,
        ),
        credits=None,
        plan_type="team",
        updated_at="2026-05-08T18:45:00Z",
    )

    assert ProjectRecord.model_validate(project.model_dump()).root_path == "C:/Projects/HomeAssistant"
    restored_limits = LimitsStatusRecord.model_validate(limits.model_dump())
    assert restored_limits.available is True
    assert restored_limits.primary.remaining_percent == 87.5
    assert restored_limits.secondary.window_minutes == 10080


def test_codex_account_record_round_trips_safe_profile_fields() -> None:
    account = CodexAccountRecord(
        available=True,
        auth_mode="chatgpt",
        email="person@example.com",
        name="Person Example",
        account_id="acc_123",
        user_id="user_123",
        plan_type="pro",
        organization_id="org_123",
        organization_title="Personal",
        updated_at="2026-05-09T10:00:00Z",
    )

    restored = CodexAccountRecord.model_validate(account.model_dump())

    assert restored.available is True
    assert restored.email == "person@example.com"
    assert restored.plan_type == "pro"


def test_bridge_diagnostics_record_round_trips() -> None:
    diagnostics = BridgeDiagnosticsRecord(
        bridge_version="0.4.12",
        git_commit="abc1234",
        python_version="3.12.10",
        service_uptime_seconds=12.5,
        last_error="none",
        tools=[
            DiagnosticToolRecord(
                name="python",
                available=True,
                path="C:/Python/python.exe",
                version="Python 3.12.10",
            )
        ],
    )

    restored = BridgeDiagnosticsRecord.model_validate(diagnostics.model_dump())

    assert restored.bridge_version == "0.4.12"
    assert restored.tools[0].available is True


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
