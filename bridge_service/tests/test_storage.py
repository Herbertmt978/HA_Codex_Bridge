import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Event, RLock
import zipfile

import pytest

from codex_bridge_service.limits import CodexLimitsProbe
from codex_bridge_service.models import (
    DEFAULT_MODEL,
    DEFAULT_THINKING_LEVEL,
    ProjectKind,
    RunMode,
)
from codex_bridge_service.storage import BridgeStorage, ProjectMutationError


class _ContentionTrackingRLock:
    def __init__(self) -> None:
        self._lock = RLock()
        self.contended = Event()

    def __enter__(self) -> "_ContentionTrackingRLock":
        if not self._lock.acquire(blocking=False):
            self.contended.set()
            self._lock.acquire()
        return self

    def __exit__(
        self,
        exc_type: object | None,
        exc_value: object | None,
        traceback: object | None,
    ) -> None:
        self._lock.release()


def test_create_project_persists_defaults_and_root_path(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)

    project = storage.create_project(
        name="HA Workspace",
        root_path=str(tmp_path / "vm-projects" / "ha"),
        default_model=DEFAULT_MODEL,
        default_thinking_level="medium",
    )

    saved_path = tmp_path / "projects" / f"{project.project_id}.json"
    payload = json.loads(saved_path.read_text(encoding="utf-8"))

    assert project.project_id.startswith("prj_")
    assert saved_path.exists()
    assert Path(project.root_path).exists()
    assert payload["name"] == "HA Workspace"
    assert payload["default_model"] == DEFAULT_MODEL
    assert payload["default_thinking_level"] == "medium"


def test_create_project_without_root_path_creates_named_workspace(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)

    project = storage.create_project(
        name="Power Apps",
        default_model=DEFAULT_MODEL,
        default_thinking_level="medium",
    )

    assert Path(project.root_path) == tmp_path / "project-workspaces" / "Power Apps"
    assert Path(project.root_path).is_dir()


def test_create_project_without_root_path_uses_unique_folder(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)

    first = storage.create_project(name="Power Apps")
    second = storage.create_project(name="Power Apps")

    assert Path(first.root_path) == tmp_path / "project-workspaces" / "Power Apps"
    assert Path(second.root_path) == tmp_path / "project-workspaces" / "Power Apps 2"


def test_create_thread_persists_project_metadata_and_defaults(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    project = storage.create_project(
        name="Bridge MVP",
        root_path=str(tmp_path / "projects" / "bridge-mvp"),
        default_model=DEFAULT_MODEL,
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
    assert record.default_model == DEFAULT_MODEL
    assert record.default_thinking_level == "medium"
    assert record.effective_model == DEFAULT_MODEL
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
        default_model=DEFAULT_MODEL,
        default_thinking_level="medium",
    )

    with pytest.raises(ValueError, match="title must not be blank"):
        storage.create_thread(
            title="   ", mode=RunMode.FULL_AUTO, project_id=project.project_id
        )


def test_create_thread_without_project_uses_direct_chat_workspace(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)

    record = storage.create_thread(title="Direct chat", mode=RunMode.FULL_AUTO)

    assert record.project_name == "Direct chats"
    assert record.project_kind is ProjectKind.DIRECT
    assert Path(record.workspace_path).parent == tmp_path / "workspaces"
    assert Path(record.workspace_path).name.startswith("ws_")


def test_thread_events_can_be_read_after_sequence_without_reloading_history(
    tmp_path,
) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    record = storage.create_thread(title="Event stream", mode=RunMode.FULL_AUTO)

    storage.append_thread_event(
        thread_id=record.thread_id, event_type="run.started", payload={}
    )
    storage.append_thread_event(
        thread_id=record.thread_id,
        event_type="message.completed",
        payload={"text": "done"},
    )

    later_events = storage.list_thread_events(record.thread_id, after=1)

    assert [event.sequence for event in later_events] == [2, 3]
    assert [event.event_type for event in later_events] == [
        "run.started",
        "message.completed",
    ]


def test_attach_file_persists_content_metadata_and_event(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    project = storage.create_project(
        name="Bridge MVP",
        root_path=str(tmp_path / "projects" / "bridge-mvp"),
        default_model=DEFAULT_MODEL,
        default_thinking_level="medium",
    )
    record = storage.create_thread(
        title="Bridge MVP", mode=RunMode.FULL_AUTO, project_id=project.project_id
    )

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


def test_attach_file_accepts_stream_content_for_large_uploads(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    project = storage.create_project(
        name="Large uploads",
        root_path=str(tmp_path / "projects" / "large-uploads"),
        default_model=DEFAULT_MODEL,
        default_thinking_level="medium",
    )
    record = storage.create_thread(
        title="Large uploads", mode=RunMode.FULL_AUTO, project_id=project.project_id
    )
    payload = b"module Option Explicit\n" * 70000

    attachment = storage.attach_file(
        thread_id=record.thread_id,
        filename="vba-project.zip",
        mime_type="application/zip",
        content=BytesIO(payload),
    )

    attachment_path = tmp_path / "uploads" / record.thread_id / "vba-project.zip"

    assert attachment.filename == "vba-project.zip"
    assert attachment.stored_path == str(attachment_path)
    assert attachment_path.read_bytes() == payload


def test_attach_file_preserves_relative_path_for_folder_uploads(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    project = storage.create_project(
        name="Folder uploads",
        root_path=str(tmp_path / "projects" / "folder-uploads"),
        default_model=DEFAULT_MODEL,
        default_thinking_level="medium",
    )
    record = storage.create_thread(
        title="Folder target", mode=RunMode.FULL_AUTO, project_id=project.project_id
    )

    attachment = storage.attach_file(
        thread_id=record.thread_id,
        filename="Module1.bas",
        mime_type="text/plain",
        content=b'Attribute VB_Name = "Module1"',
        relative_path="src/vba/Module1.bas",
    )

    attachment_path = (
        tmp_path / "uploads" / record.thread_id / "src" / "vba" / "Module1.bas"
    )

    assert attachment.relative_path == "src/vba/Module1.bas"
    assert attachment.stored_path == str(attachment_path)
    assert attachment.size_bytes == len(b'Attribute VB_Name = "Module1"')
    assert (
        attachment_path.read_text(encoding="utf-8") == 'Attribute VB_Name = "Module1"'
    )


def test_list_threads_sync_artifacts_and_update_overrides(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    project = storage.create_project(
        name="Second",
        root_path=str(tmp_path / "projects" / "second"),
        default_model=DEFAULT_MODEL,
        default_thinking_level="medium",
    )
    first = storage.create_thread(
        title="First", mode=RunMode.FULL_AUTO, project_id=project.project_id
    )
    second = storage.create_thread(
        title="Second", mode=RunMode.EDIT, project_id=project.project_id
    )

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

    assert [thread.thread_id for thread in listed_threads] == [
        second.thread_id,
        first.thread_id,
    ]
    assert len(artifacts) == 1
    assert artifacts[0].filename == "report.md"
    assert artifacts[0].stored_path == str(artifact_path)
    assert resolved_second.effective_model == "gpt-5.5"
    assert resolved_second.effective_thinking_level == "high"
    assert events[-1].event_type == "artifact.added"
    assert events[-1].payload["artifact_id"] == artifacts[0].artifact_id


def test_create_workspace_archive_packages_workspace_and_uploads(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    project = storage.create_project(
        name="Archive project",
        root_path=str(tmp_path / "projects" / "archive-project"),
        default_model=DEFAULT_MODEL,
        default_thinking_level="medium",
    )
    thread = storage.create_thread(
        title="Archive target",
        mode=RunMode.FULL_AUTO,
        project_id=project.project_id,
    )
    workspace_file = Path(thread.workspace_path) / "src" / "Module1.bas"
    workspace_file.parent.mkdir(parents=True, exist_ok=True)
    workspace_file.write_text('Attribute VB_Name = "Module1"\n', encoding="utf-8")
    storage.attach_file(
        thread_id=thread.thread_id,
        filename="requirements.txt",
        mime_type="text/plain",
        content=b"openpyxl\npandas\n",
        relative_path="deps/requirements.txt",
    )

    artifact = storage.create_workspace_archive(thread.thread_id)

    assert artifact.filename.endswith(".zip")
    assert artifact.mime_type == "application/zip"
    assert Path(artifact.stored_path).exists()
    with zipfile.ZipFile(artifact.stored_path) as archive:
        names = set(archive.namelist())
        assert "workspace/src/Module1.bas" in names
        assert "uploads/deps/requirements.txt" in names


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
    imported = next(
        project for project in projects if project.project_id == record.project_id
    )

    assert record.project_name == "Imported Threads"
    assert imported.kind is ProjectKind.IMPORTED
    assert record.workspace_path == str(legacy_workspace)


def test_existing_special_projects_are_migrated_to_correct_kind(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    stale_imported = {
        "project_id": "prj_imported",
        "name": "Imported Threads",
        "root_path": str(tmp_path / "workspaces"),
        "kind": "project",
        "default_model": "gpt-5.4",
        "default_thinking_level": "medium",
        "created_at": "2026-05-09T00:00:00Z",
        "updated_at": "2026-05-09T00:00:00Z",
    }
    stale_direct = {
        "project_id": "prj_direct",
        "name": "Direct chats",
        "root_path": str(tmp_path / "workspaces"),
        "kind": "project",
        "default_model": "gpt-5.4",
        "default_thinking_level": "medium",
        "created_at": "2026-05-09T00:00:00Z",
        "updated_at": "2026-05-09T00:00:00Z",
    }
    (tmp_path / "projects" / "prj_imported.json").write_text(
        json.dumps(stale_imported), encoding="utf-8"
    )
    (tmp_path / "projects" / "prj_direct.json").write_text(
        json.dumps(stale_direct), encoding="utf-8"
    )

    imported = storage.load_project("prj_imported")
    direct = storage.load_project("prj_direct")

    assert imported.kind is ProjectKind.IMPORTED
    assert direct.kind is ProjectKind.DIRECT
    assert imported.default_model == "gpt-5.4"
    assert direct.default_model == "gpt-5.4"


def test_legacy_static_special_defaults_migrate_to_discovered_codex_defaults(
    tmp_path,
) -> None:
    initial_storage = BridgeStorage(root_path=tmp_path)
    original = initial_storage.ensure_direct_project()
    existing_thread = initial_storage.create_thread(
        title="Existing direct", mode=RunMode.FULL_AUTO
    )
    assert original.default_model == DEFAULT_MODEL
    project_path = tmp_path / "projects" / "prj_direct.json"
    legacy_payload = json.loads(project_path.read_text(encoding="utf-8"))
    legacy_payload.pop("defaults_origin")
    project_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    discovered_storage = BridgeStorage(
        root_path=tmp_path,
        special_project_defaults_provider=lambda: ("gpt-5.6-sol", "ultra", False),
    )
    discovered_storage.initialize_special_projects()
    migrated = discovered_storage.ensure_direct_project()

    assert migrated.default_model == "gpt-5.6-sol"
    assert migrated.default_thinking_level == "ultra"
    preserved = discovered_storage.get_thread(existing_thread.thread_id)
    assert preserved.model_override == DEFAULT_MODEL
    assert preserved.thinking_override == "medium"
    assert preserved.effective_model == DEFAULT_MODEL
    assert preserved.effective_thinking_level == "medium"


def test_explicit_special_project_defaults_replace_provisional_catalog_defaults(
    tmp_path,
) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    provisional = storage.ensure_direct_project(
        default_model=DEFAULT_MODEL,
        default_thinking_level=DEFAULT_THINKING_LEVEL,
        defaults_provisional=True,
    )
    assert provisional.defaults_origin.value == "fallback"

    storage.update_project(
        provisional.project_id,
        default_model="gpt-5.4",
        default_thinking_level="high",
    )
    restarted = BridgeStorage(
        root_path=tmp_path,
        special_project_defaults_provider=lambda: ("gpt-5.6-sol", "ultra", False),
    )
    restarted.initialize_special_projects()
    fresh = restarted.ensure_direct_project()

    assert fresh.default_model == "gpt-5.4"
    assert fresh.default_thinking_level == "high"
    assert fresh.defaults_origin.value == "explicit"


def test_deferred_legacy_migration_waits_for_active_thread_to_finish(tmp_path) -> None:
    initial_storage = BridgeStorage(root_path=tmp_path)
    direct = initial_storage.ensure_direct_project()
    thread = initial_storage.create_thread(
        title="Running legacy chat",
        project_id=direct.project_id,
        mode=RunMode.FULL_AUTO,
    )
    project_path = tmp_path / "projects" / "prj_direct.json"
    legacy_payload = json.loads(project_path.read_text(encoding="utf-8"))
    legacy_payload.pop("defaults_origin")
    project_path.write_text(json.dumps(legacy_payload), encoding="utf-8")
    running = initial_storage.load_thread(thread.thread_id)
    running.status = "running"
    initial_storage.save_thread(running)

    restarted = BridgeStorage(root_path=tmp_path)
    restarted.defer_special_project_migration()
    assert (
        restarted.reconcile_special_projects(
            default_model="gpt-5.6-sol",
            default_thinking_level="ultra",
            defaults_provisional=False,
        )
        is False
    )
    assert restarted.load_project(direct.project_id).default_model == DEFAULT_MODEL

    finished = restarted.load_thread(thread.thread_id)
    finished.status = "idle"
    restarted.save_thread(finished)
    assert (
        restarted.reconcile_special_projects(
            default_model="gpt-5.6-sol",
            default_thinking_level="ultra",
            defaults_provisional=False,
        )
        is True
    )
    migrated = restarted.load_project(direct.project_id)
    preserved = restarted.get_thread(thread.thread_id)
    assert migrated.default_model == "gpt-5.6-sol"
    assert migrated.default_thinking_level == "ultra"
    assert preserved.model_override == DEFAULT_MODEL
    assert preserved.thinking_override == DEFAULT_THINKING_LEVEL


def test_deferred_legacy_migration_recovers_imported_project(tmp_path) -> None:
    initial_storage = BridgeStorage(root_path=tmp_path)
    imported = initial_storage.ensure_imported_project()
    thread = initial_storage.create_thread(
        title="Imported 0.5.0 chat",
        project_id=imported.project_id,
        mode=RunMode.FULL_AUTO,
    )
    project_path = tmp_path / "projects" / "prj_imported.json"
    legacy_payload = json.loads(project_path.read_text(encoding="utf-8"))
    legacy_payload.pop("defaults_origin")
    project_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    restarted = BridgeStorage(root_path=tmp_path)
    restarted.defer_special_project_migration()
    assert restarted.reconcile_special_projects(
        default_model="gpt-5.6-sol",
        default_thinking_level="ultra",
        defaults_provisional=False,
    )

    migrated = restarted.load_project(imported.project_id)
    preserved = restarted.get_thread(thread.thread_id)
    assert migrated.default_model == "gpt-5.6-sol"
    assert migrated.default_thinking_level == "ultra"
    assert migrated.defaults_origin.value == "codex"
    assert preserved.model_override == DEFAULT_MODEL
    assert preserved.thinking_override == DEFAULT_THINKING_LEVEL


def test_empty_imported_project_migrates_to_discovered_defaults_at_startup(
    tmp_path,
) -> None:
    initial_storage = BridgeStorage(root_path=tmp_path)
    original = initial_storage.ensure_imported_project()
    assert original.default_model == DEFAULT_MODEL
    project_path = tmp_path / "projects" / "prj_imported.json"
    legacy_payload = json.loads(project_path.read_text(encoding="utf-8"))
    legacy_payload.pop("defaults_origin")
    project_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    discovered_storage = BridgeStorage(
        root_path=tmp_path,
        special_project_defaults_provider=lambda: ("gpt-5.6-sol", "ultra", False),
    )
    discovered_storage.initialize_special_projects()

    migrated = discovered_storage.load_project("prj_imported")
    assert migrated.default_model == "gpt-5.6-sol"
    assert migrated.default_thinking_level == "ultra"


def test_legacy_thread_resolution_uses_discovered_special_project_defaults(
    tmp_path,
) -> None:
    storage = BridgeStorage(
        root_path=tmp_path,
        special_project_defaults_provider=lambda: ("gpt-5.6-sol", "ultra", False),
    )
    created = storage.create_thread(title="Legacy", mode=RunMode.FULL_AUTO)
    legacy = storage.load_thread(created.thread_id)
    legacy.project_id = None
    storage.save_thread(legacy)

    resolved = storage.get_thread(created.thread_id)

    assert resolved.project_kind is ProjectKind.IMPORTED
    assert resolved.default_model == "gpt-5.6-sol"
    assert resolved.default_thinking_level == "ultra"
    assert resolved.model_override == DEFAULT_MODEL
    assert resolved.thinking_override == "medium"
    assert resolved.effective_model == DEFAULT_MODEL
    assert resolved.effective_thinking_level == "medium"


def test_archive_restore_and_delete_thread_metadata(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    thread = storage.create_thread(title="Archive me", mode=RunMode.FULL_AUTO)

    archived = storage.archive_thread(thread.thread_id)
    assert archived.archived_at is not None
    assert storage.list_threads() == []
    assert storage.list_threads(include_archived=True)[0].thread_id == thread.thread_id

    restored = storage.restore_thread(thread.thread_id)
    assert restored.archived_at is None
    assert storage.list_threads()[0].thread_id == thread.thread_id

    storage.delete_thread(thread.thread_id)
    with pytest.raises(FileNotFoundError):
        storage.load_thread(thread.thread_id)


def test_archive_restore_and_delete_project_metadata(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    project_root = tmp_path / "projects" / "archive-project"
    project = storage.create_project(
        name="Archive project",
        root_path=str(project_root),
        default_model=DEFAULT_MODEL,
        default_thinking_level="medium",
    )
    thread = storage.create_thread(
        title="Child chat",
        mode=RunMode.FULL_AUTO,
        project_id=project.project_id,
    )

    archived = storage.archive_project(project.project_id)
    assert archived.archived_at is not None
    assert storage.load_project(project.project_id).archived_at == archived.archived_at

    restored = storage.restore_project(project.project_id)
    assert restored.archived_at is None

    storage.delete_project(project.project_id)
    with pytest.raises(FileNotFoundError):
        storage.load_project(project.project_id)
    with pytest.raises(FileNotFoundError):
        storage.load_thread(thread.thread_id)
    assert project_root.exists()


@pytest.mark.parametrize("mutation", ["archive", "restore"])
def test_project_metadata_mutation_cannot_resurrect_concurrent_delete(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    project = storage.create_project(name="Project mutation race")
    if mutation == "restore":
        storage.archive_project(project.project_id)

    mutation_lock = _ContentionTrackingRLock()
    storage._project_mutation_lock = mutation_lock
    save_entered = Event()
    release_save = Event()
    original_save_project = storage.save_project

    def blocked_save_project(record) -> None:
        save_entered.set()
        assert release_save.wait(2)
        original_save_project(record)

    monkeypatch.setattr(storage, "save_project", blocked_save_project)
    mutate = (
        storage.archive_project if mutation == "archive" else storage.restore_project
    )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            mutation_future = pool.submit(mutate, project.project_id)
            assert save_entered.wait(1)
            deletion_future = pool.submit(storage.delete_project, project.project_id)
            delete_was_serialized = mutation_lock.contended.wait(1)
            release_save.set()
            mutation_future.result(timeout=2)
            deletion_future.result(timeout=2)
    finally:
        release_save.set()

    # Automation reservations can serialize deletion at the dedicated target
    # lock before the project lock becomes contended.
    assert (
        delete_was_serialized or not storage._project_path(project.project_id).exists()
    )
    assert not storage._project_path(project.project_id).exists()


@pytest.mark.parametrize("mutation", ["update", "archive", "restore"])
def test_thread_metadata_mutation_cannot_resurrect_concurrent_delete(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    thread = storage.create_thread(title="Thread mutation race", mode=RunMode.EDIT)
    if mutation == "restore":
        storage.archive_thread(thread.thread_id)

    mutation_lock = _ContentionTrackingRLock()
    storage._thread_mutation_lock = mutation_lock
    save_entered = Event()
    release_save = Event()
    original_commit = storage._commit_prepared_thread_with_events_locked

    def blocked_commit(record, events) -> None:
        save_entered.set()
        assert release_save.wait(2)
        original_commit(record, events)

    monkeypatch.setattr(
        storage,
        "_commit_prepared_thread_with_events_locked",
        blocked_commit,
    )
    mutate = {
        "update": lambda thread_id: storage.update_thread(
            thread_id,
            title="Updated during race",
        ),
        "archive": storage.archive_thread,
        "restore": storage.restore_thread,
    }[mutation]

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            mutation_future = pool.submit(mutate, thread.thread_id)
            assert save_entered.wait(1)
            deletion_future = pool.submit(storage.delete_thread, thread.thread_id)
            delete_was_serialized = mutation_lock.contended.wait(1)
            release_save.set()
            mutation_future.result(timeout=2)
            deletion_future.result(timeout=2)
    finally:
        release_save.set()

    # Automation reservations can serialize deletion at the dedicated target
    # lock before the thread lock becomes contended.
    assert delete_was_serialized or not storage._thread_path(thread.thread_id).exists()
    assert not storage._thread_path(thread.thread_id).exists()
    assert not storage._event_log_path(thread.thread_id).exists()


@pytest.mark.parametrize(
    ("target_kind", "mutation"),
    [
        ("standalone", "archive_project"),
        ("standalone", "delete_project"),
        ("continue_thread", "archive_thread"),
    ],
)
def test_automation_target_reservation_rejects_archive_delete_without_deadlock(
    tmp_path, target_kind, mutation
) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    project = storage.create_project(name="Reserved automation project")
    thread = storage.create_thread(
        title="Reserved automation thread",
        mode=RunMode.EDIT,
        project_id=project.project_id,
    )
    target = (
        {"kind": "standalone", "project_id": project.project_id}
        if target_kind == "standalone"
        else {"kind": "continue_thread", "thread_id": thread.thread_id}
    )
    target_id = project.project_id if mutation.endswith("project") else thread.thread_id
    mutate = getattr(storage, mutation)

    with storage.prepare_automation_target(
        target,
        title="Scheduled run",
        mode=RunMode.EDIT,
    ) as prepared:
        assert prepared.project_id == project.project_id
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(mutate, target_id)
            with pytest.raises(ProjectMutationError, match="reserved"):
                future.result(timeout=1)


def test_automation_target_reservation_rejects_busy_thread_without_mutating_metadata(
    tmp_path,
) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    project = storage.create_project(name="Busy automation project")
    thread = storage.create_thread(
        title="Busy automation thread", mode=RunMode.EDIT, project_id=project.project_id
    )
    record = storage.load_thread(thread.thread_id)
    record.status = "running"
    storage.save_thread(record)
    before = storage.load_thread(thread.thread_id)

    with pytest.raises(ProjectMutationError, match="busy"):
        with storage.prepare_automation_target(
            {"kind": "continue_thread", "thread_id": thread.thread_id},
            title="Should not update",
            mode=RunMode.FULL_AUTO,
            model_override="gpt-5.4-mini",
        ):
            pytest.fail("busy targets must be rejected before mutation")

    after = storage.load_thread(thread.thread_id)
    assert after.mode == before.mode
    assert after.model_override == before.model_override
    assert after.updated_at == before.updated_at


def test_automation_target_reservation_rejects_duplicate_continue_dispatch(
    tmp_path,
) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    project = storage.create_project(name="Duplicate automation project")
    thread = storage.create_thread(
        title="Duplicate automation thread",
        mode=RunMode.EDIT,
        project_id=project.project_id,
    )
    target = {"kind": "continue_thread", "thread_id": thread.thread_id}
    submissions: list[str] = []

    with storage.prepare_automation_target(
        target,
        title="First scheduled run",
        mode=RunMode.FULL_AUTO,
        model_override="gpt-5.5",
        thinking_override="high",
    ) as first:
        submissions.append(first.thread_id)
        before_duplicate = storage.load_thread(thread.thread_id)

        with pytest.raises(ProjectMutationError, match="reserved"):
            with storage.prepare_automation_target(
                target,
                title="Duplicate scheduled run",
                mode=RunMode.EDIT,
                model_override="gpt-5.4-mini",
                thinking_override="low",
            ) as duplicate:
                submissions.append(duplicate.thread_id)

        after_duplicate = storage.load_thread(thread.thread_id)
        assert after_duplicate.mode == before_duplicate.mode
        assert after_duplicate.model_override == before_duplicate.model_override
        assert after_duplicate.thinking_override == before_duplicate.thinking_override
        assert after_duplicate.updated_at == before_duplicate.updated_at

    assert submissions == [thread.thread_id]


def test_limits_probe_refreshes_saved_status(tmp_path) -> None:
    codex_home = tmp_path / ".codex"
    session_dir = codex_home / "sessions" / "2026" / "05" / "09"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_path = session_dir / "rollout-2026-05-09T10-00-00-foo.jsonl"
    session_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-05-09T09:59:00Z",
                        "type": "session_meta",
                        "payload": {},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-09T10:00:00Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "rate_limits": {
                                "primary": {
                                    "used_percent": 20.0,
                                    "window_minutes": 300,
                                    "resets_at": 1778302800,
                                },
                                "secondary": {
                                    "used_percent": 50.0,
                                    "window_minutes": 10080,
                                    "resets_at": 1778907600,
                                },
                                "plan_type": "team",
                                "credits": None,
                            },
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    storage = BridgeStorage(
        root_path=tmp_path / "bridge", limits_probe=CodexLimitsProbe(codex_home)
    )
    status = storage.get_limits_status(refresh=True)

    assert status.available is True
    assert status.primary is not None
    assert status.primary.remaining_percent == 80.0
    assert status.secondary is not None
    assert status.secondary.remaining_percent == 50.0


def test_limits_probe_uses_live_backend_snapshot_when_auth_is_available(
    tmp_path, monkeypatch
) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    token = "eyJhbGciOiJub25lIn0.eyJleHAiIjo0MTAyNDQ0ODAwfQ.sig"
    (codex_home / "auth.json").write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": token,
                    "refresh_token": "refresh",
                    "account_id": "acct_123",
                }
            }
        ),
        encoding="utf-8",
    )

    captured = {}

    def fake_fetch(url, *, headers, method="GET", body=None):
        captured["url"] = url
        captured["headers"] = headers
        return {
            "plan_type": "pro",
            "rate_limit": {
                "limit_reached": False,
                "primary_window": {
                    "used_percent": 5,
                    "limit_window_seconds": 18000,
                    "reset_at": 1778320427,
                },
                "secondary_window": {
                    "used_percent": 65,
                    "limit_window_seconds": 604800,
                    "reset_at": 1778539055,
                },
            },
            "credits": {
                "has_credits": True,
                "unlimited": False,
                "balance": 12,
            },
        }

    probe = CodexLimitsProbe(codex_home, min_fetch_interval_seconds=0)
    monkeypatch.setattr(probe, "_fetch_json", fake_fetch)

    status = probe.probe()

    assert captured["url"] == "https://chatgpt.com/backend-api/wham/usage"
    assert captured["headers"]["ChatGPT-Account-Id"] == "acct_123"
    assert status is not None
    assert status.plan_type == "pro"
    assert status.primary is not None
    assert status.primary.remaining_percent == 95.0
    assert status.secondary is not None
    assert status.secondary.remaining_percent == 35.0


def test_limits_probe_never_rotates_codex_refresh_token(tmp_path, monkeypatch) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    expired_token = "eyJhbGciOiJub25lIn0.eyJleHAiOjF9.sig"
    auth_path = codex_home / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": expired_token,
                    "refresh_token": "one-time-refresh-token",
                }
            }
        ),
        encoding="utf-8",
    )
    original_auth = auth_path.read_text(encoding="utf-8")
    fetch_calls = 0

    def fake_fetch(url, *, headers, method="GET", body=None):
        nonlocal fetch_calls
        fetch_calls += 1
        raise AssertionError(f"expired credentials must not be sent to {url}")

    probe = CodexLimitsProbe(codex_home, min_fetch_interval_seconds=45)
    monkeypatch.setattr(probe, "_fetch_json", fake_fetch)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: probe.probe(), range(2)))

    assert fetch_calls == 0
    assert results == [None, None]
    assert auth_path.read_text(encoding="utf-8") == original_auth
