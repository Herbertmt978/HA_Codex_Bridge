"""Assist step timings reach the App log without any private content."""

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from codex_bridge_service.assist_mcp import isolation_config
from codex_bridge_service.assist_timing import AssistTimer
from codex_bridge_service.routes import task_actions
from codex_bridge_service.storage import BridgeStorage

from test_runtime_broker import (
    ValidatorBackedAppServer, _HomeAssistantProfileStorage, _active_ids,
    _broker, _complete, _requests, _wait_until,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_summary_reports_step_gaps_in_order_and_skips_unreached_steps() -> None:
    clock = _Clock()
    timer = AssistTimer(clock)
    for step, at in [("worker", 100.002), ("thread", 102.5), ("started", 102.6),
                     ("answer", 104.0), ("answer", 105.0), ("completed", 105.25)]:
        clock.now = at
        timer.mark(step, latest=step == "answer")
    clock.now = 106.0
    timer.mark("worker")  # A repeated step keeps its first time.
    timer.mark("unknown")
    assert timer.summary("completed") == (
        "Assist turn completed in 5250 ms (new Codex session): worker started +2, "
        "Codex session +2498, run started +100, answer complete +2400, run recorded +250 (ms)"
    )


def test_summary_names_resumed_sessions_and_bounds_its_status_text() -> None:
    timer = AssistTimer(_Clock())
    timer.resumed = True
    assert timer.summary("token=secret") == (
        "Assist turn ended in 0 ms (resumed Codex session): no steps reached (ms)"
    )


def test_report_prints_only_the_summary(capsys) -> None:
    timer = AssistTimer(_Clock())
    timer.report("completed")
    assert capsys.readouterr().out == timer.summary("completed") + "\n"


def test_assist_runs_log_one_content_free_timing_line_and_other_runs_none(tmp_path: Path, monkeypatch) -> None:
    printed: list[str] = []
    monkeypatch.setattr(AssistTimer, "report", lambda self, status: printed.append(self.summary(status)))
    storage = BridgeStorage(root_path=tmp_path / "state")
    project = storage.create_project(
        name="Assist", root_path=str(tmp_path / "workspace"),
        default_model="gpt-5.6-codex", default_thinking_level="high",
    )
    storage = _HomeAssistantProfileStorage(storage)
    peer = ValidatorBackedAppServer()
    peer.thread_unload_delay_seconds = 0

    def current_config(workspace, selected, *, execution_cwd):
        return isolation_config({
            "home": {"enabled": True, "url": "https://mcp.vendor.example/mcp", "enabled_tools": ["lights"]},
        }, workspace, execution_cwd=execution_cwd)

    manager = SimpleNamespace(enabled=True, assist_thread_config=current_config)
    broker = _broker(storage, peer, mcp_manager=manager)
    app = FastAPI()
    app.state.auth_token = "secret"
    app.state.storage = storage
    app.state.runner = broker
    app.state.mcp_manager = manager
    app.state.feature_capabilities = ("assist_mcp_selection_v1",)
    app.include_router(task_actions.router)
    monkeypatch.setattr(task_actions, "_require_ready", lambda *_: None)
    monkeypatch.setattr(task_actions, "_require_assist_model", lambda *_: None)
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}
    payload = {
        "task_id": "a" * 32, "project_id": project.project_id,
        "title": "Assist", "prompt": "Private lights prompt", "assist": True,
        "mode": "observe", "web_search": "disabled", "assist_mcp_servers": ["home"],
    }
    try:
        started = client.post("/task-actions/start", headers=headers, json=payload)
        assert started.status_code == 202, started.text
        thread_id = started.json()["thread_id"]
        _wait_until(lambda: len(_requests(peer, "turn/start")) == 1)
        _, remote_id, turn_id = _active_ids(storage, thread_id)
        _complete(peer, remote_thread_id=remote_id, turn_id=turn_id)
        _wait_until(lambda: len(printed) == 1)
        first = printed[0]
        assert first.startswith("Assist turn completed in ")
        assert "(new Codex session)" in first
        for label in ("worker started", "isolation config", "Codex session +", "turn sent",
                      "run started", "turn completed", "session unloaded", "run recorded"):
            assert label in first, label
        run_id = broker.get_task_action_run("a" * 32).run_id
        for private in (thread_id, run_id, remote_id, turn_id, "a" * 32, "Private lights prompt",
                        project.project_id, project.root_path, "home", "vendor"):
            assert private not in first

        peer.script("thread/unsubscribe", {"status": "unsubscribed"}, {"status": "notLoaded"})
        continued = client.post("/task-actions/continue", headers=headers, json={
            "task_id": "b" * 32, "thread_id": thread_id, "prompt": "Private follow-up",
            "assist": True, "web_search": "disabled", "assist_mcp_servers": ["home"],
        })
        assert continued.status_code == 202, continued.text
        _wait_until(lambda: len(_requests(peer, "turn/start")) == 2)
        _, remote_id, turn_id = _active_ids(storage, thread_id)
        _complete(peer, remote_thread_id=remote_id, turn_id=turn_id)
        _wait_until(lambda: len(printed) == 2)
        assert "(resumed Codex session)" in printed[1]

        ordinary = client.post("/task-actions/start", headers=headers, json={
            "task_id": "c" * 32, "project_id": project.project_id, "title": "Task",
            "prompt": "Ordinary", "mode": "observe",
        })
        assert ordinary.status_code == 202, ordinary.text
        _wait_until(lambda: len(_requests(peer, "turn/start")) == 3)
        _, remote_id, turn_id = _active_ids(storage, ordinary.json()["thread_id"])
        _complete(peer, remote_thread_id=remote_id, turn_id=turn_id)
        _wait_until(lambda: broker.get_task_action_run("c" * 32).status == "completed")
        assert len(printed) == 2
        assert broker._assist_timers == {}
    finally:
        broker.close()
