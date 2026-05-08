import json
import time

from codex_bridge_service.models import RunMode
from codex_bridge_service.runner import BridgeRunner
from codex_bridge_service.storage import BridgeStorage


FAKE_CODEX = """
import json
import os
import sys

argv = sys.argv[1:]
prompt = argv[-1]
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


def test_runner_executes_initial_prompt_collects_artifacts_and_binds_session(tmp_path, monkeypatch) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    thread = storage.create_thread(title="Runner", mode=RunMode.FULL_AUTO)
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


def test_runner_resumes_existing_session_for_follow_up_prompt(tmp_path, monkeypatch) -> None:
    storage = BridgeStorage(root_path=tmp_path)
    thread = storage.create_thread(title="Runner", mode=RunMode.FULL_AUTO)
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
