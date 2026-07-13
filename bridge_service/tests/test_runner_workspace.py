import json
import os
import time
from pathlib import Path

import pytest

from codex_bridge_service.models import (
    PendingPromptRecord,
    RunMode,
    RunRecord,
    RuntimeProfile,
    ThreadRecord,
)
from codex_bridge_service.runner import BridgeRunner
from codex_bridge_service.storage import BridgeStorage
from legacy_runner_harness import legacy_ha_runner


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


class _FailedProcess:
    def __init__(self, *, stdout: list[str], stderr: list[str]) -> None:
        self.stdout = iter(stdout)
        self.stderr = iter(stderr)

    def wait(self) -> int:
        return 1


def _wait_for_finished(storage: BridgeStorage, thread_id: str) -> None:
    deadline = time.time() + 5
    while time.time() < deadline:
        if storage.load_thread(thread_id).status != "running":
            return
        time.sleep(0.02)
    raise AssertionError("thread did not finish")


def _home_assistant_thread(tmp_path: Path):
    if os.name == "nt":
        pytest.skip(
            "secure Home Assistant workspace operations require POSIX dir_fd support"
        )
    state_root = tmp_path / "data" / "bridge"
    workspace_root = tmp_path / "config" / "workspaces"
    storage = BridgeStorage(
        root_path=state_root,
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
    )
    project = storage.create_project(name="Runner", root_path="projects/runner")
    thread = storage.create_thread(
        title="Runner",
        project_id=project.project_id,
        mode=RunMode.EDIT,
    )
    return storage, thread, state_root, workspace_root


def test_home_assistant_initial_run_resolves_one_private_process_workspace(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, _, workspace_root = _home_assistant_thread(tmp_path)
    storage.attach_file(
        thread_id=thread.thread_id,
        filename="notes.txt",
        mime_type="text/plain",
        content=b"hello",
    )
    captured: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        directory_fd = kwargs["pass_fds"][0]
        captured["leased_inode"] = os.fstat(directory_fd).st_ino
        captured["cwd_inode"] = os.stat(kwargs["cwd"]).st_ino
        return _CompletedProcess()

    monkeypatch.setattr("codex_bridge_service.runner.subprocess.Popen", fake_popen)
    # Artifact confinement is the next storage slice; this test exercises only
    # the process-facing workspace boundary.
    monkeypatch.setattr(storage, "sync_thread_artifacts", lambda _thread_id: [])

    legacy_ha_runner(storage=storage).submit_prompt(
        thread.thread_id, "Inspect the upload"
    )
    _wait_for_finished(storage, thread.thread_id)

    command = captured["command"]
    trusted_workspace = captured["cwd"]
    prompt = command[command.index("--json") - 1]

    assert trusted_workspace.startswith("/proc/self/fd/")
    assert (
        captured["leased_inode"]
        == os.stat(workspace_root / "projects" / "runner").st_ino
    )
    assert captured["cwd_inode"] == captured["leased_inode"]
    assert command[command.index("-C") + 1] == trusted_workspace
    assert f"Working directory: {trusted_workspace}" in prompt
    assert storage.get_thread(thread.thread_id).workspace_path == "projects/runner"


def test_home_assistant_resumed_run_uses_absolute_cwd_without_initial_c_flag(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, _, workspace_root = _home_assistant_thread(tmp_path)
    record = storage.load_thread(thread.thread_id)
    record.codex_session_id = "session-existing"
    storage.save_thread(record)
    captured: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        directory_fd = kwargs["pass_fds"][0]
        captured["cwd_inode"] = os.stat(kwargs["cwd"]).st_ino
        captured["leased_inode"] = os.fstat(directory_fd).st_ino
        return _CompletedProcess()

    monkeypatch.setattr("codex_bridge_service.runner.subprocess.Popen", fake_popen)
    monkeypatch.setattr(storage, "sync_thread_artifacts", lambda _thread_id: [])

    legacy_ha_runner(storage=storage).submit_prompt(thread.thread_id, "Continue")
    _wait_for_finished(storage, thread.thread_id)

    command = captured["command"]
    assert captured["cwd"].startswith("/proc/self/fd/")
    assert captured["cwd_inode"] == captured["leased_inode"]
    assert (
        captured["leased_inode"]
        == os.stat(workspace_root / "projects" / "runner").st_ino
    )
    exec_index = command.index("exec")
    assert command[exec_index + 1 : exec_index + 3] == ["resume", "session-existing"]
    assert "-C" not in command
    assert storage.get_thread(thread.thread_id).workspace_path == "projects/runner"


def test_home_assistant_workspace_swap_before_popen_keeps_original_directory_inode(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, _, workspace_root = _home_assistant_thread(tmp_path)
    workspace = workspace_root / "projects" / "runner"
    original = workspace_root / "projects" / "runner-original"
    outside = tmp_path / "outside-workspace"
    outside.mkdir()
    original_inode = workspace.stat().st_ino
    outside_inode = outside.stat().st_ino
    captured: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        directory_fd = kwargs["pass_fds"][0]
        workspace.rename(original)
        workspace.symlink_to(outside, target_is_directory=True)
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        captured["leased_inode"] = os.fstat(directory_fd).st_ino
        captured["cwd_inode"] = os.stat(kwargs["cwd"]).st_ino
        captured["outside_inode"] = workspace.stat().st_ino
        workspace.unlink()
        original.rename(workspace)
        return _CompletedProcess()

    monkeypatch.setattr("codex_bridge_service.runner.subprocess.Popen", fake_popen)
    monkeypatch.setattr(storage, "sync_thread_artifacts", lambda _thread_id: [])

    run = RunRecord(run_id="run_swap", thread_id=thread.thread_id, status="running")
    persisted = storage.load_thread(thread.thread_id)
    persisted.status = "running"
    persisted.active_run_id = run.run_id
    storage.save_thread(persisted)
    view = storage.get_thread(thread.thread_id)
    legacy_ha_runner(storage=storage, recover_stale_runs=False)._run_prompt(
        view, run, "Run"
    )

    command = captured["command"]
    assert captured["cwd"].startswith("/proc/self/fd/")
    assert command[command.index("-C") + 1] == captured["cwd"]
    assert captured["leased_inode"] == original_inode
    assert captured["cwd_inode"] == original_inode
    assert captured["outside_inode"] == outside_inode
    assert captured["cwd_inode"] != outside_inode


def test_external_legacy_run_preserves_persisted_workspace_string(
    tmp_path,
    monkeypatch,
) -> None:
    storage = BridgeStorage(root_path=tmp_path / "state")
    workspace = tmp_path / "workspace"
    project = storage.create_project(name="Runner", root_path=str(workspace))
    thread = storage.create_thread(
        title="Runner",
        project_id=project.project_id,
        mode=RunMode.EDIT,
    )
    raw_workspace = f"{workspace.parent}{os.sep}.{os.sep}{workspace.name}"
    record = storage.load_thread(thread.thread_id)
    record.workspace_path = raw_workspace
    storage.save_thread(record)
    captured: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return _CompletedProcess()

    monkeypatch.setattr("codex_bridge_service.runner.subprocess.Popen", fake_popen)

    BridgeRunner(storage=storage).submit_prompt(thread.thread_id, "Continue")
    _wait_for_finished(storage, thread.thread_id)

    command = captured["command"]
    assert captured["cwd"] == raw_workspace
    assert command[command.index("-C") + 1] == raw_workspace


def test_external_legacy_missing_workspace_is_still_delegated_to_popen(
    tmp_path,
    monkeypatch,
) -> None:
    storage = BridgeStorage(root_path=tmp_path / "state")
    project = storage.create_project(
        name="Runner", root_path=str(tmp_path / "workspace")
    )
    thread = storage.create_thread(
        title="Runner",
        project_id=project.project_id,
        mode=RunMode.EDIT,
    )
    missing_workspace = str(tmp_path / "legacy-missing")
    record = storage.load_thread(thread.thread_id)
    record.workspace_path = missing_workspace
    storage.save_thread(record)
    captured: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        raise FileNotFoundError("legacy Popen failure")

    monkeypatch.setattr("codex_bridge_service.runner.subprocess.Popen", fake_popen)

    BridgeRunner(storage=storage).submit_prompt(thread.thread_id, "Continue")
    _wait_for_finished(storage, thread.thread_id)

    command = captured["command"]
    assert captured["cwd"] == missing_workspace
    assert command[command.index("-C") + 1] == missing_workspace
    assert storage.load_thread(thread.thread_id).last_error == "legacy Popen failure"


@pytest.mark.parametrize(
    "tampered_path",
    ["ABSOLUTE", "../outside", "projects/missing", "projects/file", "projects/link"],
)
def test_home_assistant_tampered_run_workspace_fails_before_popen_without_path_leak(
    tmp_path,
    monkeypatch,
    tampered_path: str,
) -> None:
    storage, thread, state_root, workspace_root = _home_assistant_thread(tmp_path)
    (workspace_root / "projects" / "file").write_text(
        "not a directory", encoding="utf-8"
    )
    outside = tmp_path / "private-outside"
    outside.mkdir()
    (workspace_root / "projects" / "link").symlink_to(outside, target_is_directory=True)

    view = storage.get_thread(thread.thread_id)
    if tampered_path == "ABSOLUTE":
        view.workspace_path = str(workspace_root / "projects" / "runner")
    else:
        view.workspace_path = tampered_path
    run = RunRecord(run_id="run_tampered", thread_id=thread.thread_id, status="running")
    persisted = storage.load_thread(thread.thread_id)
    persisted.status = "running"
    persisted.active_run_id = run.run_id
    storage.save_thread(persisted)

    popen_called = False

    def fail_if_called(*args, **kwargs):
        nonlocal popen_called
        popen_called = True
        raise AssertionError("Popen must not run for an invalid workspace")

    monkeypatch.setattr("codex_bridge_service.runner.subprocess.Popen", fail_if_called)

    legacy_ha_runner(storage=storage, recover_stale_runs=False)._run_prompt(
        view, run, "Run"
    )

    failure = storage.list_thread_events(thread.thread_id)[-1]
    serialized = json.dumps(failure.payload)
    assert popen_called is False
    assert failure.event_type == "run.failed"
    assert failure.payload["failure_type"] == "run.failed"
    assert str(state_root) not in serialized
    assert str(workspace_root) not in serialized
    assert str(outside) not in serialized
    assert "/config/" not in serialized
    assert "/data/" not in serialized


def test_home_assistant_popen_error_cannot_leak_private_workspace_paths(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, state_root, workspace_root = _home_assistant_thread(tmp_path)

    def fail_to_start(*args, **kwargs):
        raise OSError(f"could not chdir to {workspace_root}; state is {state_root}")

    monkeypatch.setattr("codex_bridge_service.runner.subprocess.Popen", fail_to_start)

    legacy_ha_runner(storage=storage).submit_prompt(thread.thread_id, "Run")
    _wait_for_finished(storage, thread.thread_id)

    failure = storage.list_thread_events(thread.thread_id)[-1]
    serialized = json.dumps(failure.payload)
    assert failure.event_type == "run.failed"
    assert failure.payload["error"] == "Codex process could not be started."
    assert str(state_root) not in serialized
    assert str(workspace_root) not in serialized


def test_home_assistant_structured_child_failure_is_classified_without_echoing_paths(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, state_root, workspace_root = _home_assistant_thread(tmp_path)
    private_message = (
        f"The model is not supported after reading {workspace_root}; state={state_root}"
    )
    process = _FailedProcess(
        stdout=[json.dumps({"type": "error", "message": private_message}) + "\n"],
        stderr=[],
    )
    monkeypatch.setattr(
        "codex_bridge_service.runner.subprocess.Popen",
        lambda *args, **kwargs: process,
    )

    legacy_ha_runner(storage=storage).submit_prompt(thread.thread_id, "Run")
    _wait_for_finished(storage, thread.thread_id)

    events = storage.list_thread_events(thread.thread_id)
    failure = events[-1]
    serialized = json.dumps([event.model_dump(mode="json") for event in events])
    assert failure.event_type == "run.failed"
    assert failure.payload == {
        "run_id": failure.payload["run_id"],
        "error": "The selected Codex model is not supported.",
        "blocked": False,
        "failure_type": "model.unsupported",
    }
    assert "raw_error" not in failure.payload
    assert str(state_root) not in serialized
    assert str(workspace_root) not in serialized


def test_home_assistant_stderr_limit_failure_uses_safe_event_and_limits_message(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, state_root, workspace_root = _home_assistant_thread(tmp_path)
    private_message = f"quota exhausted at {workspace_root}; state={state_root}\n"
    process = _FailedProcess(stdout=[], stderr=[private_message])
    monkeypatch.setattr(
        "codex_bridge_service.runner.subprocess.Popen",
        lambda *args, **kwargs: process,
    )

    legacy_ha_runner(storage=storage).submit_prompt(thread.thread_id, "Run")
    _wait_for_finished(storage, thread.thread_id)

    failure = storage.list_thread_events(thread.thread_id)[-1]
    limits = storage.get_limits_status()
    serialized = json.dumps(
        {
            "failure": failure.model_dump(mode="json"),
            "limits": limits.model_dump(mode="json"),
        }
    )
    assert failure.event_type == "run.failed"
    assert failure.payload["error"] == "Codex usage limits have been reached."
    assert failure.payload["failure_type"] == "limits.exhausted"
    assert failure.payload["blocked"] is True
    assert "raw_error" not in failure.payload
    assert limits.message == "Codex usage limits have been reached."
    assert str(state_root) not in serialized
    assert str(workspace_root) not in serialized


def test_home_assistant_workspace_deleted_after_popen_terminalizes_and_clears_queue(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, _, workspace_root = _home_assistant_thread(tmp_path)
    workspace = workspace_root / "projects" / "runner"
    run = RunRecord(run_id="run_active", thread_id=thread.thread_id, status="running")
    record = storage.load_thread(thread.thread_id)
    record.status = "running"
    record.active_run_id = run.run_id
    record.pending_prompts = [
        PendingPromptRecord(
            run_id="run_queued",
            prompt="Next",
            created_at=storage._now(),
        )
    ]
    storage.save_thread(record)
    view = storage.get_thread(thread.thread_id)

    def fake_popen(*args, **kwargs):
        workspace.rmdir()
        return _CompletedProcess()

    monkeypatch.setattr("codex_bridge_service.runner.subprocess.Popen", fake_popen)

    legacy_ha_runner(storage=storage, recover_stale_runs=False)._run_prompt(
        view, run, "Run"
    )

    raw_record = ThreadRecord.model_validate_json(
        (storage.threads_dir / f"{thread.thread_id}.json").read_text(encoding="utf-8")
    )
    events = storage.list_thread_events(thread.thread_id)
    failure = next(
        event for event in reversed(events) if event.event_type == "run.failed"
    )
    queue_cleared = events[-1]
    terminal_events = [
        event
        for event in events
        if event.event_type in {"run.completed", "run.failed"}
        and event.payload.get("run_id") == run.run_id
    ]
    assert raw_record.status == "error"
    assert raw_record.active_run_id is None
    assert raw_record.pending_prompts == []
    assert raw_record.last_error == "The workspace is unavailable."
    assert failure.payload["error"] == "The workspace is unavailable."
    assert queue_cleared.event_type == "run.queue_cleared"
    assert queue_cleared.payload == {
        "reason": "active run failed",
        "queued_count": 1,
    }
    assert [event.event_type for event in terminal_events] == ["run.failed"]


def test_home_assistant_workspace_deleted_after_sync_terminalizes_success_branch(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, _, workspace_root = _home_assistant_thread(tmp_path)
    workspace = workspace_root / "projects" / "runner"
    monkeypatch.setattr(
        "codex_bridge_service.runner.subprocess.Popen",
        lambda *args, **kwargs: _CompletedProcess(),
    )

    def delete_during_sync(_thread_id: str):
        workspace.rmdir()
        return []

    monkeypatch.setattr(storage, "sync_thread_artifacts", delete_during_sync)

    legacy_ha_runner(storage=storage).submit_prompt(thread.thread_id, "Run")
    deadline = time.time() + 5
    thread_path = storage.threads_dir / f"{thread.thread_id}.json"
    while time.time() < deadline:
        raw_record = ThreadRecord.model_validate_json(
            thread_path.read_text(encoding="utf-8")
        )
        if raw_record.status != "running":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("thread did not terminalize")

    events = storage.list_thread_events(thread.thread_id)
    failure = events[-1]
    terminal_events = [
        event
        for event in events
        if event.event_type in {"run.completed", "run.failed"}
        and event.payload.get("run_id") == failure.payload.get("run_id")
    ]
    assert raw_record.status == "error"
    assert raw_record.active_run_id is None
    assert raw_record.last_error == "The workspace is unavailable."
    assert failure.event_type == "run.failed"
    assert failure.payload["error"] == "The workspace is unavailable."
    assert [event.event_type for event in terminal_events] == ["run.failed"]


def test_home_assistant_workspace_deleted_between_final_load_and_save_terminalizes(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, _, workspace_root = _home_assistant_thread(tmp_path)
    workspace = workspace_root / "projects" / "runner"
    monkeypatch.setattr(
        "codex_bridge_service.runner.subprocess.Popen",
        lambda *args, **kwargs: _CompletedProcess(),
    )
    original_save_thread = storage.save_thread

    def delete_before_terminal_save(record: ThreadRecord) -> None:
        if record.thread_id == thread.thread_id and record.status == "idle":
            workspace.rmdir()
        original_save_thread(record)

    monkeypatch.setattr(storage, "save_thread", delete_before_terminal_save)

    legacy_ha_runner(storage=storage).submit_prompt(thread.thread_id, "Run")
    deadline = time.time() + 5
    thread_path = storage.threads_dir / f"{thread.thread_id}.json"
    while time.time() < deadline:
        raw_record = ThreadRecord.model_validate_json(
            thread_path.read_text(encoding="utf-8")
        )
        if raw_record.status != "running":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("thread did not terminalize")

    events = storage.list_thread_events(thread.thread_id)
    failure = events[-1]
    terminal_events = [
        event
        for event in events
        if event.event_type in {"run.completed", "run.failed"}
        and event.payload.get("run_id") == failure.payload.get("run_id")
    ]
    assert raw_record.status == "error"
    assert raw_record.active_run_id is None
    assert raw_record.last_error == "The workspace is unavailable."
    assert failure.event_type == "run.failed"
    assert failure.payload["error"] == "The workspace is unavailable."
    assert [event.event_type for event in terminal_events] == ["run.failed"]
