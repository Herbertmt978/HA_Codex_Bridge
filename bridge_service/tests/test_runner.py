import json
import subprocess
import time

from codex_bridge_service.models import DEFAULT_MODEL, DEFAULT_THINKING_LEVEL, RunMode
from codex_bridge_service.runner import BridgeRunner
from codex_bridge_service.storage import BridgeStorage


FAKE_CODEX = """
import json
import os
import sys

argv = sys.argv[1:]
prompt = argv[argv.index("--json") - 1] if "--json" in argv else ""
argv_path = os.environ.get("FAKE_CODEX_ARGV_PATH")
if argv_path:
    with open(argv_path, "w", encoding="utf-8") as stream:
        json.dump(argv, stream)

artifact_name = os.environ.get("FAKE_CODEX_ARTIFACT_NAME")
if artifact_name:
    with open(os.path.join(os.getcwd(), artifact_name), "w", encoding="utf-8") as stream:
        stream.write(f"artifact for {prompt}")

session_id = os.environ.get("FAKE_CODEX_SESSION_ID", "019e08fb-92dc-7920-88f3-9fc949d1aef8")
print(json.dumps({"type": "thread.started", "thread_id": session_id}), flush=True)
print(json.dumps({"type": "turn.started"}), flush=True)
print(json.dumps({"type": "token_count", "rate_limits": {"primary": {"used_percent": 10.0, "window_minutes": 300, "resets_at": 1778302800}, "secondary": {"used_percent": 40.0, "window_minutes": 10080, "resets_at": 1778907600}, "credits": None, "plan_type": "team"}}), flush=True)
print(json.dumps({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": f"Echo: {prompt}"}}), flush=True)
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 11, "output_tokens": 7}}), flush=True)
"""


def _wait_for_idle(storage: BridgeStorage, thread_id: str) -> None:
    deadline = time.time() + 5
    while time.time() < deadline:
        if storage.load_thread(thread_id).status != "running":
            return
        time.sleep(0.05)
    raise AssertionError("thread did not return to idle in time")


def _create_project_thread(storage: BridgeStorage, tmp_path, *, model_override=None, thinking_override=None):
    project = storage.create_project(
        name="Runner",
        root_path=str(tmp_path / "runner-project"),
        default_model=DEFAULT_MODEL,
        default_thinking_level=DEFAULT_THINKING_LEVEL,
    )
    return storage.create_thread(
        title="Runner",
        project_id=project.project_id,
        mode=RunMode.FULL_AUTO,
        model_override=model_override,
        thinking_override=thinking_override,
    )


def test_runner_executes_initial_prompt_collects_artifacts_binds_session_and_updates_limits(
    tmp_path,
    monkeypatch,
) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    thread = _create_project_thread(storage, tmp_path)
    storage.attach_file(
        thread_id=thread.thread_id,
        filename="notes.txt",
        mime_type="text/plain",
        content=b"hello",
    )

    script_path = tmp_path / "fake_codex.py"
    argv_path = tmp_path / "argv-initial.json"
    script_path.write_text(FAKE_CODEX, encoding="utf-8")

    monkeypatch.setenv("FAKE_CODEX_ARGV_PATH", str(argv_path))
    monkeypatch.setenv("FAKE_CODEX_ARTIFACT_NAME", "report.md")

    runner = BridgeRunner(storage=storage, codex_command=str(script_path))
    run = runner.submit_prompt(thread.thread_id, "Summarise the upload")
    _wait_for_idle(storage, thread.thread_id)

    saved = storage.load_thread(thread.thread_id)
    events = storage.list_thread_events(thread.thread_id)
    argv = json.loads(argv_path.read_text(encoding="utf-8"))
    limits = storage.get_limits_status()

    assert run.thread_id == thread.thread_id
    assert saved.status == "idle"
    assert saved.codex_session_id == "019e08fb-92dc-7920-88f3-9fc949d1aef8"
    assert saved.active_run_id is None
    assert saved.last_error is None
    assert any(artifact.filename == "report.md" for artifact in saved.artifacts)
    assert any(event.event_type == "message.created" for event in events)
    assert any(event.event_type == "message.completed" for event in events)
    assert any(event.event_type == "artifact.added" for event in events)
    assert argv[0] == "exec"
    assert "--json" in argv
    assert "-C" in argv
    assert "--add-dir" in argv
    assert str(tmp_path / "uploads" / thread.thread_id) in argv
    assert "-m" in argv
    assert DEFAULT_MODEL in argv
    assert "model_reasoning_effort=medium" in " ".join(argv)
    assert limits.available is True
    assert limits.primary is not None
    assert limits.primary.remaining_percent == 90.0
    assert limits.secondary is not None
    assert limits.secondary.remaining_percent == 60.0


def test_runner_resumes_existing_session_for_follow_up_prompt_and_uses_overrides(tmp_path, monkeypatch) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    thread = _create_project_thread(
        storage,
        tmp_path,
        model_override="gpt-5.5",
        thinking_override="high",
    )
    script_path = tmp_path / "fake_codex.py"
    first_argv_path = tmp_path / "argv-first.json"
    second_argv_path = tmp_path / "argv-second.json"
    script_path.write_text(FAKE_CODEX, encoding="utf-8")

    monkeypatch.setenv("FAKE_CODEX_ARGV_PATH", str(first_argv_path))
    runner = BridgeRunner(storage=storage, codex_command=str(script_path))
    runner.submit_prompt(thread.thread_id, "First prompt")
    _wait_for_idle(storage, thread.thread_id)

    monkeypatch.setenv("FAKE_CODEX_ARGV_PATH", str(second_argv_path))
    runner.submit_prompt(thread.thread_id, "Second prompt")
    _wait_for_idle(storage, thread.thread_id)

    argv = json.loads(second_argv_path.read_text(encoding="utf-8"))

    assert argv[:3] == [
        "exec",
        "resume",
        "019e08fb-92dc-7920-88f3-9fc949d1aef8",
    ]
    assert "Second prompt" in argv
    assert "-C" not in argv
    assert "-m" in argv
    assert "gpt-5.5" in argv
    assert "model_reasoning_effort=high" in " ".join(argv)


def test_runner_marks_thread_error_and_limits_blocked_after_credit_failure(tmp_path, monkeypatch) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    thread = _create_project_thread(storage, tmp_path)

    class DummyProcess:
        def __init__(self) -> None:
            self.stdout = iter([json.dumps({"type": "turn.started"}) + "\n"])
            self.stderr = iter(["Usage limit reached for codex plan\n"])

        def wait(self) -> int:
            return 1

    monkeypatch.setattr("codex_bridge_service.runner.subprocess.Popen", lambda *args, **kwargs: DummyProcess())

    runner = BridgeRunner(storage=storage, codex_command="codex")
    runner.submit_prompt(thread.thread_id, "Please keep going")
    _wait_for_idle(storage, thread.thread_id)

    saved = storage.load_thread(thread.thread_id)
    events = storage.list_thread_events(thread.thread_id)
    limits = storage.get_limits_status()

    assert saved.status == "error"
    assert saved.active_run_id is None
    assert "Usage limit reached" in (saved.last_error or "")
    assert events[-1].event_type == "run.failed"
    assert events[-1].payload["blocked"] is True
    assert limits.blocked is True
    assert "Usage limit reached" in (limits.message or "")


def test_runner_closes_stdin_for_codex_process(tmp_path, monkeypatch) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    thread = _create_project_thread(storage, tmp_path)
    captured: dict[str, object] = {}

    class DummyProcess:
        def __init__(self) -> None:
            self.stdout = iter(
                [
                    json.dumps({"type": "turn.started"}) + "\n",
                    json.dumps({"type": "turn.completed", "usage": {}}) + "\n",
                ]
            )
            self.stderr = iter([])

        def wait(self) -> int:
            return 0

    def fake_popen(*args, **kwargs):
        captured.update(kwargs)
        return DummyProcess()

    monkeypatch.setattr("codex_bridge_service.runner.subprocess.Popen", fake_popen)

    runner = BridgeRunner(storage=storage, codex_command="codex")
    runner.submit_prompt(thread.thread_id, "Check stdin handling")
    _wait_for_idle(storage, thread.thread_id)

    assert captured["stdin"] is subprocess.DEVNULL


def test_runner_can_bypass_sandbox_for_trusted_vm_exec(tmp_path, monkeypatch) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    thread = _create_project_thread(storage, tmp_path)
    script_path = tmp_path / "fake_codex.py"
    argv_path = tmp_path / "argv-bypass.json"
    script_path.write_text(FAKE_CODEX, encoding="utf-8")

    monkeypatch.setenv("FAKE_CODEX_ARGV_PATH", str(argv_path))

    runner = BridgeRunner(
        storage=storage,
        codex_command=str(script_path),
        bypass_sandbox=True,
    )
    runner.submit_prompt(thread.thread_id, "Trusted VM run")
    _wait_for_idle(storage, thread.thread_id)

    argv = json.loads(argv_path.read_text(encoding="utf-8"))

    assert "--dangerously-bypass-approvals-and-sandbox" in argv
    assert "--full-auto" not in argv
    assert "--sandbox" not in argv
