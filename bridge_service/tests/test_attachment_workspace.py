import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Barrier, Event

import pytest
from fastapi.testclient import TestClient

from codex_bridge_service.app import create_app
from codex_bridge_service.models import RunMode, RunRecord, RuntimeProfile, ThreadRecord
from codex_bridge_service.storage import BridgeStorage
from codex_bridge_service.workspace import (
    WorkspaceBoundaryError,
    WorkspaceEscapeError,
    WorkspaceInputError,
    WorkspaceTypeError,
)
from legacy_runner_harness import legacy_ha_runner


pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="secure Home Assistant attachment operations require POSIX dir_fd support",
)


def _home_assistant_thread(tmp_path: Path):
    state_root = tmp_path / "data" / "bridge"
    workspace_root = tmp_path / "config" / "workspaces"
    storage = BridgeStorage(
        root_path=state_root,
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
    )
    project = storage.create_project(name="Uploads", root_path="projects/uploads")
    thread = storage.create_thread(
        title="Uploads",
        project_id=project.project_id,
        mode=RunMode.EDIT,
    )
    return storage, thread, state_root, workspace_root


def test_home_assistant_attachment_is_persisted_as_owned_relative_locators(
    tmp_path,
) -> None:
    storage, thread, state_root, workspace_root = _home_assistant_thread(tmp_path)

    attachment = storage.attach_file(
        thread_id=thread.thread_id,
        filename="Module1.bas",
        mime_type="text/plain",
        content=BytesIO(b'Attribute VB_Name = "Module1"'),
        relative_path="src/vba/Module1.bas",
    )

    expected_relative = "src/vba/Module1.bas"
    expected_stored = f"{thread.thread_id}/{expected_relative}"
    persisted = json.loads(
        storage._thread_path(thread.thread_id).read_text(encoding="utf-8")
    )
    event = storage.list_thread_events(thread.thread_id)[-1]
    serialized_public_data = json.dumps(
        {
            "attachment": attachment.model_dump(),
            "thread": persisted,
            "event": event.model_dump(),
        }
    )

    assert attachment.filename == "Module1.bas"
    assert attachment.relative_path == expected_relative
    assert attachment.stored_path == expected_stored
    assert (
        storage.uploads_dir / expected_stored
    ).read_bytes() == b'Attribute VB_Name = "Module1"'
    assert str(state_root) not in serialized_public_data
    assert str(workspace_root) not in serialized_public_data
    assert "/data/" not in serialized_public_data
    assert "/config/" not in serialized_public_data


@pytest.mark.parametrize(
    ("stored_path", "relative_path", "expected_error"),
    [
        ("/data/bridge/uploads/thr_owned/notes.txt", "notes.txt", WorkspaceInputError),
        ("C:/data/uploads/thr_owned/notes.txt", "notes.txt", WorkspaceInputError),
        ("//server/share/notes.txt", "notes.txt", WorkspaceInputError),
        ("thr_owned/../notes.txt", "../notes.txt", WorkspaceInputError),
        ("thr_other/notes.txt", "notes.txt", WorkspaceEscapeError),
    ],
)
def test_home_assistant_load_rejects_unowned_attachment_locators(
    tmp_path,
    stored_path: str,
    relative_path: str,
    expected_error: type[WorkspaceBoundaryError],
) -> None:
    storage, thread, _, _ = _home_assistant_thread(tmp_path)
    target = storage._thread_path(thread.thread_id)
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["attachments"] = [
        {
            "attachment_id": "att_tampered",
            "filename": "notes.txt",
            "mime_type": "text/plain",
            "stored_path": stored_path.replace("thr_owned", thread.thread_id),
            "relative_path": relative_path,
            "size_bytes": 1,
        }
    ]
    if "thr_other" in stored_path:
        payload["attachments"][0]["stored_path"] = "thr_other/notes.txt"
    target.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(expected_error) as error:
        storage.load_thread(thread.thread_id)

    assert str(storage.root) not in str(error.value)
    assert "notes.txt" not in str(error.value)


def test_home_assistant_load_rejects_symlink_and_special_attachment_entries(
    tmp_path,
) -> None:
    storage, thread, _, _ = _home_assistant_thread(tmp_path)
    thread_dir = storage.uploads_dir / thread.thread_id
    thread_dir.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (thread_dir / "link.txt").symlink_to(outside)
    (thread_dir / "pipe").parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(thread_dir / "pipe")

    for filename, expected_error in (
        ("link.txt", WorkspaceEscapeError),
        ("pipe", WorkspaceTypeError),
    ):
        target = storage._thread_path(thread.thread_id)
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["attachments"] = [
            {
                "attachment_id": "att_tampered",
                "filename": filename,
                "mime_type": "application/octet-stream",
                "stored_path": f"{thread.thread_id}/{filename}",
                "relative_path": filename,
            }
        ]
        target.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(expected_error):
            storage.load_thread(thread.thread_id)


class _BrokenStream:
    def __init__(self) -> None:
        self._reads = 0

    def read(self, _size: int) -> bytes:
        self._reads += 1
        if self._reads == 1:
            return b"partial"
        raise OSError("upload interrupted")


def test_home_assistant_failed_stream_removes_partial_attachment(tmp_path) -> None:
    storage, thread, _, _ = _home_assistant_thread(tmp_path)

    with pytest.raises(OSError, match="upload interrupted"):
        storage.attach_file(
            thread_id=thread.thread_id,
            filename="partial.txt",
            mime_type="text/plain",
            content=_BrokenStream(),
        )

    assert list((storage.uploads_dir / thread.thread_id).iterdir()) == []
    raw = ThreadRecord.model_validate_json(
        storage._thread_path(thread.thread_id).read_text(encoding="utf-8")
    )
    assert raw.attachments == []


def test_home_assistant_collision_uses_exclusive_create_without_overwrite(
    tmp_path,
) -> None:
    storage, thread, _, _ = _home_assistant_thread(tmp_path)

    first = storage.attach_file(
        thread_id=thread.thread_id,
        filename="notes.txt",
        mime_type="text/plain",
        content=b"first",
    )
    second = storage.attach_file(
        thread_id=thread.thread_id,
        filename="notes.txt",
        mime_type="text/plain",
        content=b"second",
    )

    assert first.relative_path == "notes.txt"
    assert second.relative_path is not None
    assert second.relative_path.startswith("notes-")
    assert second.relative_path.endswith(".txt")
    assert (storage.uploads_dir / first.stored_path).read_bytes() == b"first"
    assert (storage.uploads_dir / second.stored_path).read_bytes() == b"second"


class _BarrierStream:
    def __init__(self, barrier: Barrier, content: bytes) -> None:
        self._barrier = barrier
        self._content = content
        self._read = False

    def read(self, _size: int) -> bytes:
        if self._read:
            return b""
        self._read = True
        self._barrier.wait(timeout=5)
        return self._content


def test_home_assistant_concurrent_uploads_merge_attachment_metadata(tmp_path) -> None:
    storage, thread, _, _ = _home_assistant_thread(tmp_path)
    barrier = Barrier(2)

    def upload(filename: str, content: bytes):
        return storage.attach_file(
            thread_id=thread.thread_id,
            filename=filename,
            mime_type="text/plain",
            content=_BarrierStream(barrier, content),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(upload, "first.txt", b"first")
        second_future = executor.submit(upload, "second.txt", b"second")
        uploaded = (first_future.result(), second_future.result())

    persisted = storage.load_thread(thread.thread_id)
    expected_ids = {attachment.attachment_id for attachment in uploaded}
    assert {
        attachment.attachment_id for attachment in persisted.attachments
    } == expected_ids
    assert {
        (storage.uploads_dir / attachment.stored_path).read_bytes()
        for attachment in persisted.attachments
    } == {b"first", b"second"}
    attachment_events = [
        event
        for event in storage.list_thread_events(thread.thread_id)
        if event.event_type == "attachment.added"
    ]
    assert {
        event.payload["attachment_id"] for event in attachment_events
    } == expected_ids


def test_home_assistant_stale_thread_save_preserves_published_attachment(
    tmp_path,
) -> None:
    storage, thread, _, _ = _home_assistant_thread(tmp_path)
    stale = storage.load_thread(thread.thread_id)
    attachment = storage.attach_file(
        thread_id=thread.thread_id,
        filename="input.txt",
        mime_type="text/plain",
        content=b"input",
    )

    stale.title = "Saved from stale state"
    storage.save_thread(stale)

    persisted = storage.load_thread(thread.thread_id)
    assert persisted.title == "Saved from stale state"
    assert [item.attachment_id for item in persisted.attachments] == [
        attachment.attachment_id
    ]


class _BlockingUploadStream:
    def __init__(self, started: Event, resume: Event) -> None:
        self._started = started
        self._resume = resume
        self._read = False

    def read(self, _size: int) -> bytes:
        if self._read:
            return b""
        self._read = True
        self._started.set()
        assert self._resume.wait(timeout=5)
        return b"late upload"


def test_home_assistant_delete_racing_upload_never_resurrects_thread(tmp_path) -> None:
    storage, thread, _, _ = _home_assistant_thread(tmp_path)
    started = Event()
    resume = Event()

    with ThreadPoolExecutor(max_workers=2) as executor:
        upload = executor.submit(
            storage.attach_file,
            thread_id=thread.thread_id,
            filename="late.txt",
            mime_type="text/plain",
            content=_BlockingUploadStream(started, resume),
        )
        assert started.wait(timeout=5)
        storage.delete_thread(thread.thread_id)
        resume.set()
        with pytest.raises(WorkspaceBoundaryError):
            upload.result()

    assert not storage._thread_path(thread.thread_id).exists()
    assert not (storage.uploads_dir / thread.thread_id).exists()


class _RenameReplacementStream:
    def __init__(
        self,
        target: Path,
        moved: Path,
        *,
        fail_after_replace: bool,
    ) -> None:
        self._target = target
        self._moved = moved
        self._fail_after_replace = fail_after_replace
        self._reads = 0

    def read(self, _size: int) -> bytes:
        self._reads += 1
        if self._reads == 1:
            return b"uploaded"
        self._target.rename(self._moved)
        self._target.write_bytes(b"replacement")
        if self._fail_after_replace:
            raise OSError("stream failed after replacement")
        return b""


@pytest.mark.parametrize("fail_after_replace", [False, True])
def test_home_assistant_upload_rename_race_never_publishes_or_deletes_replacement(
    tmp_path,
    fail_after_replace: bool,
) -> None:
    storage, thread, _, _ = _home_assistant_thread(tmp_path)
    target = storage.uploads_dir / thread.thread_id / "race.txt"
    moved = storage.uploads_dir / thread.thread_id / "race-original.txt"
    expected_error = OSError if fail_after_replace else WorkspaceEscapeError

    with pytest.raises(expected_error):
        storage.attach_file(
            thread_id=thread.thread_id,
            filename="race.txt",
            mime_type="text/plain",
            content=_RenameReplacementStream(
                target,
                moved,
                fail_after_replace=fail_after_replace,
            ),
        )

    assert target.read_bytes() == b"replacement"
    assert moved.read_bytes() == b"uploaded"
    assert storage.load_thread(thread.thread_id).attachments == []
    assert all(
        event.event_type != "attachment.added"
        for event in storage.list_thread_events(thread.thread_id)
    )


class _CompletedProcess:
    def __init__(self) -> None:
        self.stdout = iter(
            [
                json.dumps({"type": "turn.started"}) + "\n",
                json.dumps({"type": "turn.completed", "usage": {}}) + "\n",
            ]
        )
        self.stderr = iter(())

    def wait(self) -> int:
        return 0


def _active_run(
    storage: BridgeStorage,
    thread_id: str,
    run_id: str = "run_attachment",
) -> tuple[RunRecord, ThreadRecord]:
    run = RunRecord(run_id=run_id, thread_id=thread_id, status="running")
    record = storage.load_thread(thread_id)
    record.status = "running"
    record.active_run_id = run.run_id
    storage.save_thread(record)
    return run, record


def test_home_assistant_runner_uses_sealed_attachment_fd_without_private_path(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, state_root, workspace_root = _home_assistant_thread(tmp_path)
    attachment = storage.attach_file(
        thread_id=thread.thread_id,
        filename="Module1.bas",
        mime_type="text/plain",
        content=b"Option Explicit",
        relative_path="src/vba/Module1.bas",
    )
    run, _ = _active_run(storage, thread.thread_id)
    captured: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        workspace_fd, attachment_fd = kwargs["pass_fds"]
        attachment_path = f"/proc/self/fd/{attachment_fd}"
        captured["command"] = command
        captured["workspace_fd"] = workspace_fd
        captured["attachment_fd"] = attachment_fd
        captured["workspace_inode"] = os.fstat(workspace_fd).st_ino
        captured["cwd_inode"] = os.stat(kwargs["cwd"]).st_ino
        captured["attachment"] = Path(attachment_path).read_bytes()
        captured["attachment_path"] = attachment_path
        captured["attachment_target"] = os.readlink(attachment_path)
        with pytest.raises(OSError):
            os.write(attachment_fd, b"mutate")
        return _CompletedProcess()

    monkeypatch.setattr("codex_bridge_service.runner.subprocess.Popen", fake_popen)
    monkeypatch.setattr(storage, "sync_thread_artifacts", lambda _thread_id: [])

    legacy_ha_runner(storage=storage, recover_stale_runs=False)._run_prompt(
        storage.get_thread(thread.thread_id),
        run,
        "Inspect it",
    )

    command = captured["command"]
    prompt = command[command.index("--json") - 1]
    serialized = json.dumps(
        {
            "command": command,
            "attachment": attachment.model_dump(),
            "attachment_target": captured["attachment_target"],
        }
    )
    assert captured["attachment"] == b"Option Explicit"
    assert captured["cwd_inode"] == captured["workspace_inode"]
    assert "--add-dir" not in command
    assert captured["attachment_path"] in prompt
    assert "/memfd:codex-bridge-input" in captured["attachment_target"]
    assert Path(f"{captured['attachment_path']}/..").exists() is False
    assert attachment.stored_path == f"{thread.thread_id}/src/vba/Module1.bas"
    assert str(state_root) not in serialized
    assert str(workspace_root) not in serialized
    for descriptor in (captured["workspace_fd"], captured["attachment_fd"]):
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_home_assistant_child_receives_only_selected_sealed_attachment_fd(
    tmp_path,
) -> None:
    storage, thread, state_root, _ = _home_assistant_thread(tmp_path)
    attachment = storage.attach_file(
        thread_id=thread.thread_id,
        filename="input.txt",
        mime_type="text/plain",
        content=b"selected input",
    )
    sibling = storage.create_thread(
        title="Sibling",
        project_id=thread.project_id,
        mode=RunMode.EDIT,
    )
    sibling_attachment = storage.attach_file(
        thread_id=sibling.thread_id,
        filename="sibling-secret.txt",
        mime_type="text/plain",
        content=b"sibling secret",
    )
    record = storage.load_thread(thread.thread_id)
    leases = storage.lease_run_attachments(record)
    selected_lease = leases[attachment.attachment_id]
    workspace_fd = storage.open_workspace_directory_fd(record.workspace_path)
    process_workspace = f"/proc/self/fd/{workspace_fd}"

    private_source = storage.uploads_dir / attachment.stored_path
    private_original = private_source.with_name("input-original.txt")
    sibling_source = storage.uploads_dir / sibling_attachment.stored_path
    private_source.rename(private_original)
    private_source.symlink_to(sibling_source)
    script = r"""
import json
import os
import pathlib
import sys

attachment = pathlib.Path(sys.argv[1])
fd_targets = {}
sealed_contents = []
for entry in pathlib.Path('/proc/self/fd').iterdir():
    try:
        target = os.readlink(entry)
        fd_targets[entry.name] = target
        if '/memfd:codex-bridge-input' in target:
            sealed_contents.append(entry.read_text())
    except OSError:
        pass
write_blocked = False
try:
    with attachment.open('wb') as stream:
        stream.write(b'mutated')
except OSError:
    write_blocked = True
print(json.dumps({
    'content': attachment.read_text(),
    'file_parent_escape_exists': (attachment / '..').exists(),
    'sealed_contents': sealed_contents,
    'write_blocked': write_blocked,
    'fd_targets': fd_targets,
}))
"""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, selected_lease.process_path],
            cwd=process_workspace,
            pass_fds=(workspace_fd, selected_lease.fileno()),
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
    finally:
        os.close(workspace_fd)
        for lease in leases.values():
            lease.close()
        private_source.unlink()
        private_original.rename(private_source)

    serialized = json.dumps(payload)
    assert payload["content"] == "selected input"
    assert payload["file_parent_escape_exists"] is False
    assert payload["sealed_contents"] == ["selected input"]
    assert payload["write_blocked"] is True
    assert str(state_root) not in serialized
    assert str(storage.uploads_dir) not in serialized


def test_home_assistant_runner_fd_ignores_private_source_replacement(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, _, _ = _home_assistant_thread(tmp_path)
    attachment = storage.attach_file(
        thread_id=thread.thread_id,
        filename="input.txt",
        mime_type="text/plain",
        content=b"trusted input",
    )
    run, _ = _active_run(storage, thread.thread_id, "run_source_swap")
    source = storage.uploads_dir / attachment.stored_path
    original = source.with_name("input-original.txt")
    original_lease = storage.lease_run_attachments

    def lease_then_replace(record):
        leases = original_lease(record)
        source.rename(original)
        source.write_bytes(b"hostile input")
        return leases

    captured: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        _workspace_fd, attachment_fd = kwargs["pass_fds"]
        captured["content"] = Path(f"/proc/self/fd/{attachment_fd}").read_bytes()
        captured["command"] = command
        source.unlink()
        original.rename(source)
        return _CompletedProcess()

    monkeypatch.setattr(storage, "lease_run_attachments", lease_then_replace)
    monkeypatch.setattr("codex_bridge_service.runner.subprocess.Popen", fake_popen)
    monkeypatch.setattr(storage, "sync_thread_artifacts", lambda _thread_id: [])

    legacy_ha_runner(storage=storage, recover_stale_runs=False)._run_prompt(
        storage.get_thread(thread.thread_id),
        run,
        "Read it",
    )

    assert captured["content"] == b"trusted input"
    assert source.read_bytes() == b"trusted input"


class _GatedProcess(_CompletedProcess):
    def __init__(self, started: Event, release: Event) -> None:
        self._started = started
        self._release = release
        self._lines = iter(
            [
                json.dumps({"type": "turn.started"}) + "\n",
                json.dumps({"type": "turn.completed", "usage": {}}) + "\n",
            ]
        )
        self.stdout = self
        self.stderr = iter(())

    def __iter__(self):
        return self

    def __next__(self):
        self._started.set()
        assert self._release.wait(timeout=5)
        return next(self._lines)


def test_home_assistant_runs_are_serialized_while_inputs_share_workspace(
    tmp_path,
    monkeypatch,
) -> None:
    storage, first, _, _ = _home_assistant_thread(tmp_path)
    second = storage.create_thread(
        title="Second",
        project_id=first.project_id,
        mode=RunMode.EDIT,
    )
    storage.attach_file(
        thread_id=first.thread_id,
        filename="first.txt",
        mime_type="text/plain",
        content=b"first secret",
    )
    storage.attach_file(
        thread_id=second.thread_id,
        filename="second.txt",
        mime_type="text/plain",
        content=b"second secret",
    )
    first_run, _ = _active_run(storage, first.thread_id, "run_first_serial")
    second_run, _ = _active_run(storage, second.thread_id, "run_second_serial")
    first_started = Event()
    release_first = Event()
    second_started = Event()
    popen_calls = 0

    def fake_popen(_command, **_kwargs):
        nonlocal popen_calls
        popen_calls += 1
        if popen_calls == 1:
            return _GatedProcess(first_started, release_first)
        second_started.set()
        return _CompletedProcess()

    monkeypatch.setattr("codex_bridge_service.runner.subprocess.Popen", fake_popen)
    monkeypatch.setattr(storage, "sync_thread_artifacts", lambda _thread_id: [])
    runner = legacy_ha_runner(storage=storage, recover_stale_runs=False)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            runner._run_prompt,
            storage.get_thread(first.thread_id),
            first_run,
            "First",
        )
        assert first_started.wait(timeout=5)
        second_future = executor.submit(
            runner._run_prompt,
            storage.get_thread(second.thread_id),
            second_run,
            "Second",
        )
        assert second_started.wait(timeout=0.2) is False
        assert popen_calls == 1
        release_first.set()
        first_future.result(timeout=5)
        second_future.result(timeout=5)

    assert second_started.is_set()
    assert popen_calls == 2


def test_home_assistant_runner_without_attachments_skips_attachment_leases(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, _, _ = _home_assistant_thread(tmp_path)
    run, _ = _active_run(storage, thread.thread_id)
    captured: dict[str, object] = {}

    def fail_attachment_leases(_record):
        raise AssertionError("empty threads must not lease upload files")

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["pass_fds"] = kwargs["pass_fds"]
        return _CompletedProcess()

    monkeypatch.setattr(storage, "lease_run_attachments", fail_attachment_leases)
    monkeypatch.setattr("codex_bridge_service.runner.subprocess.Popen", fake_popen)
    monkeypatch.setattr(storage, "sync_thread_artifacts", lambda _thread_id: [])

    legacy_ha_runner(storage=storage, recover_stale_runs=False)._run_prompt(
        storage.get_thread(thread.thread_id),
        run,
        "Run",
    )

    assert len(captured["pass_fds"]) == 1
    assert "--add-dir" not in captured["command"]


def test_home_assistant_cancel_during_attachment_copy_prevents_process_start(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, _, _ = _home_assistant_thread(tmp_path)
    storage.attach_file(
        thread_id=thread.thread_id,
        filename="input.txt",
        mime_type="text/plain",
        content=b"input",
    )
    run, _ = _active_run(storage, thread.thread_id, "run_cancel_preflight")
    copy_started = Event()
    release_copy = Event()
    original_lease = storage.lease_run_attachments
    popen_called = False

    def blocking_lease(record):
        copy_started.set()
        assert release_copy.wait(timeout=5)
        return original_lease(record)

    def fail_if_called(*_args, **_kwargs):
        nonlocal popen_called
        popen_called = True
        raise AssertionError("cancelled preflight must never launch Codex")

    monkeypatch.setattr(storage, "lease_run_attachments", blocking_lease)
    monkeypatch.setattr("codex_bridge_service.runner.subprocess.Popen", fail_if_called)
    runner = legacy_ha_runner(storage=storage, recover_stale_runs=False)

    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(
            runner._run_prompt,
            storage.get_thread(thread.thread_id),
            run,
            "Read it",
        )
        assert copy_started.wait(timeout=5)
        cancelled = runner.cancel_run(thread.thread_id)
        release_copy.set()
        worker.result(timeout=5)

    assert cancelled.status == "cancelled"
    assert popen_called is False
    persisted = storage.load_thread(thread.thread_id)
    assert persisted.status == "idle"
    assert persisted.active_run_id is None


def test_home_assistant_missing_attachment_prevents_process_start(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, state_root, workspace_root = _home_assistant_thread(tmp_path)
    attachment = storage.attach_file(
        thread_id=thread.thread_id,
        filename="gone.txt",
        mime_type="text/plain",
        content=b"temporary",
    )
    storage.uploads_boundary.unlink_regular_file(attachment.stored_path)
    run, _ = _active_run(storage, thread.thread_id)
    popen_called = False

    def fail_if_called(*args, **kwargs):
        nonlocal popen_called
        popen_called = True
        raise AssertionError("Popen must not run with a missing attachment")

    monkeypatch.setattr("codex_bridge_service.runner.subprocess.Popen", fail_if_called)

    legacy_ha_runner(storage=storage, recover_stale_runs=False)._run_prompt(
        storage.get_thread(thread.thread_id),
        run,
        "Read it",
    )

    failure = storage.list_thread_events(thread.thread_id)[-1]
    serialized = json.dumps(failure.model_dump())
    assert popen_called is False
    assert failure.event_type == "run.failed"
    assert str(state_root) not in serialized
    assert str(workspace_root) not in serialized
    assert "/data/" not in serialized
    assert "/config/" not in serialized


def test_home_assistant_attachment_api_returns_only_relative_locators(tmp_path) -> None:
    state_root = tmp_path / "data" / "bridge"
    workspace_root = tmp_path / "config" / "workspaces"
    app = create_app(
        root_path=state_root,
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
        runner_factory=lambda _storage: object(),
    )
    storage = app.state.storage
    project = storage.create_project(name="API", root_path="projects/api")
    thread = storage.create_thread(
        title="API",
        project_id=project.project_id,
        mode=RunMode.EDIT,
    )
    client = TestClient(app)

    response = client.post(
        f"/threads/{thread.thread_id}/attachments",
        headers={"Authorization": "Bearer secret"},
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"relative_path": "docs/notes.txt"},
    )

    assert response.status_code == 201
    payload = response.json()
    serialized = response.text + storage._thread_path(thread.thread_id).read_text(
        encoding="utf-8"
    )
    assert payload["stored_path"] == f"{thread.thread_id}/docs/notes.txt"
    assert payload["relative_path"] == "docs/notes.txt"
    assert str(state_root) not in serialized
    assert str(workspace_root) not in serialized
    assert "/data/" not in serialized
    assert "/config/" not in serialized

    invalid = client.post(
        f"/threads/{thread.thread_id}/attachments",
        headers={"Authorization": "Bearer secret"},
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"relative_path": "../private/notes.txt"},
    )
    assert invalid.status_code == 400
    assert invalid.json() == {"detail": "invalid attachment location"}


def test_attachment_api_maps_boundary_not_found_to_generic_404(
    tmp_path, monkeypatch
) -> None:
    state_root = tmp_path / "data" / "bridge"
    workspace_root = tmp_path / "config" / "workspaces"
    app = create_app(
        root_path=state_root,
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
        runner_factory=lambda _storage: object(),
    )
    storage = app.state.storage
    project = storage.create_project(name="API", root_path="projects/api")
    thread = storage.create_thread(
        title="API",
        project_id=project.project_id,
        mode=RunMode.EDIT,
    )

    from codex_bridge_service.workspace import WorkspaceNotFoundError

    monkeypatch.setattr(
        storage,
        "attach_file",
        lambda **_kwargs: (_ for _ in ()).throw(WorkspaceNotFoundError()),
    )
    response = TestClient(app).post(
        f"/threads/{thread.thread_id}/attachments",
        headers={"Authorization": "Bearer secret"},
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "attachment location not found"}
