import base64
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from codex_bridge_service.models import RunMode, RuntimeProfile
from codex_bridge_service.resource_limits import ResourceLimits
from codex_bridge_service.runtime_gate import RuntimeGate, RuntimeMutationConflictError
from codex_bridge_service.terminal import TerminalError, WorkspaceTerminal


class FakeClient:
    generation = 1

    def __init__(self):
        self.ended = Event()
        self.started = Event()
        self.calls = []

    def register_notification_handler(self, method, handler):
        self.handler = handler

    def start(self):
        pass

    def request(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method == "command/exec":
            self.process_id = params["processId"]
            self.emit(b"$ ")
            self.started.set()
            assert self.ended.wait(5)
            return {"exitCode": 0}
        return {}

    def emit(self, data, generation=1, process_id=None, **extra):
        self.handler(SimpleNamespace(generation=generation, params={
            "processId": process_id or self.process_id,
            "deltaBase64": base64.b64encode(data).decode(), **extra,
        }))

    def close(self):
        self.ended.set()


def wait_for(predicate):
    deadline = monotonic() + 3
    while not predicate() and monotonic() < deadline:
        sleep(0.01)
    assert predicate()


@pytest.fixture
def terminal(tmp_path):
    reservation = Mock()
    thread = SimpleNamespace(mode=RunMode.EDIT, archived_at=None, project_id="project", workspace_path="chat")
    storage = Mock(runtime_profile=RuntimeProfile.HOME_ASSISTANT)
    storage.load_thread.return_value = thread
    storage.load_project.return_value = SimpleNamespace(archived_at=None)
    storage.resolve_workspace_path.return_value = tmp_path / "chat"
    storage.reserve_workspace_mutation.return_value = reservation
    gate = RuntimeGate(limits=ResourceLimits())
    client = FakeClient()
    manager = WorkspaceTerminal(storage, gate, lambda workspace: client, lambda: True)
    yield manager, client, gate, storage, reservation
    manager.close()


def open_terminal(terminal):
    manager, client, *_ = terminal
    opened = manager.open("chat", 80, 20)
    assert client.started.wait(1)
    return opened["session_id"]


def test_shell_is_bound_to_workspace_and_excludes_other_mutations(terminal):
    manager, client, gate, storage, reservation = terminal
    session = open_terminal(terminal)
    params = client.calls[0][1]
    assert params["command"] == ["/usr/local/bin/python", "-I", "-m", "codex_bridge_service.terminal_shell"]
    assert params["cwd"] == str(storage.resolve_workspace_path.return_value)
    assert "sandboxPolicy" not in params  # Retain the managed minimal-read profile.
    assert params["tty"] is True and params["timeoutMs"] == 1_800_000
    with pytest.raises(RuntimeMutationConflictError):
        gate.acquire_auth_mutation()
    with pytest.raises(RuntimeMutationConflictError):
        gate.reserve_prompt(client_request_id="other")
    with pytest.raises(TerminalError):
        manager.open("other-chat", 80, 20)
    manager.close_session(session, "chat")
    wait_for(lambda: not gate.snapshot().config_mutation_active)
    reservation.release.assert_called_once()


def test_stale_output_and_wrong_chat_cannot_cross_sessions(terminal):
    manager, client, *_ = terminal
    session = open_terminal(terminal)
    client.emit(b"stale", generation=0)
    client.emit(b"wrong", process_id="other")
    client.emit(b"current")
    result = manager.read(session, "chat", 0)
    assert b"".join(base64.b64decode(c["data"]) for c in result["chunks"]) == b"$ current"
    with pytest.raises(TerminalError):
        manager.read(session, "other-chat", 0)
    assert "workspace" not in result


def test_input_retries_are_idempotent_and_ordered(terminal):
    manager, client, *_ = terminal
    session = open_terminal(terminal)
    manager.write(session, "chat", "echo hello\r", 1)
    manager.write(session, "chat", "echo hello\r", 1)
    assert len([m for m, _ in client.calls if m == "command/exec/write"]) == 1
    with pytest.raises(TerminalError):
        manager.write(session, "chat", "must not run", 3)
    with pytest.raises(TerminalError):
        manager.write(session, "chat", "é" * 16384, 2)
    manager.resize(session, "chat", 100, 30)
    assert client.calls[-1] == ("command/exec/resize", {"processId": session, "size": {"cols": 100, "rows": 30}})


def test_observe_archive_and_missing_attestation_fail_closed(terminal):
    manager, client, gate, storage, _ = terminal
    for mode, archived in [(RunMode.OBSERVE, None), (RunMode.EDIT, "yesterday")]:
        storage.load_thread.return_value.mode = mode
        storage.load_thread.return_value.archived_at = archived
        with pytest.raises(TerminalError):
            manager.open("chat", 80, 20)
        assert not gate.snapshot().config_mutation_active
    manager.ready = lambda: False
    with pytest.raises(TerminalError):
        manager.open("chat", 80, 20)
    assert not client.calls


def test_output_cap_and_idle_lease_close_runtime(terminal):
    manager, client, gate, *_ = terminal
    session = open_terminal(terminal)
    manager.maximum_bytes = 20
    client.emit(b"x" * 21)
    wait_for(lambda: client.ended.is_set())
    wait_for(lambda: not gate.snapshot().config_mutation_active)
    assert manager.read(session, "chat", 0)["state"] == "closed"


def test_abandoned_page_expires_without_a_close_request(terminal):
    manager, client, gate, *_ = terminal
    manager.idle_seconds = 0.01
    open_terminal(terminal)
    wait_for(lambda: client.ended.is_set())
    wait_for(lambda: not gate.snapshot().config_mutation_active)


def test_quota_breach_closes_shell(terminal):
    manager, client, gate, storage, _ = terminal
    storage.observe_workspace_growth.side_effect = RuntimeError("full")
    open_terminal(terminal)
    wait_for(lambda: client.ended.is_set())
    wait_for(lambda: not gate.snapshot().config_mutation_active)


@pytest.mark.parametrize("change", ["observe", "archive", "project_archive", "deleted", "moved"])
def test_access_changes_close_terminal_before_further_input(terminal, change):
    manager, client, gate, storage, _ = terminal
    session = open_terminal(terminal)
    if change == "observe":
        storage.load_thread.return_value.mode = RunMode.OBSERVE
    elif change == "archive":
        storage.load_thread.return_value.archived_at = "now"
    elif change == "project_archive":
        storage.load_project.return_value.archived_at = "now"
    elif change == "deleted":
        storage.load_thread.side_effect = FileNotFoundError()
    else:
        storage.resolve_workspace_path.return_value /= "different"
    with pytest.raises(TerminalError):
        manager.write(session, "chat", "touch forbidden\r", 1)
    assert not any(method == "command/exec/write" for method, _ in client.calls)
    wait_for(lambda: not gate.snapshot().config_mutation_active)


def test_access_changes_stop_existing_command_without_input(terminal):
    manager, client, gate, storage, _ = terminal
    open_terminal(terminal)
    storage.load_thread.return_value.mode = RunMode.OBSERVE
    wait_for(lambda: client.ended.is_set())
    wait_for(lambda: not gate.snapshot().config_mutation_active)


def test_runtime_lease_is_held_until_process_cleanup_finishes(terminal):
    manager, client, gate, *_ = terminal
    session = open_terminal(terminal)
    closing = Event()
    allow_cleanup = Event()
    original_close = client.close

    def slow_close():
        original_close()  # The pending request fails before descendants exit.
        closing.set()
        assert allow_cleanup.wait(3)

    client.close = slow_close
    worker = Thread(target=manager.close_session, args=(session, "chat"))
    worker.start()
    try:
        assert closing.wait(1)
        sleep(0.05)
        assert gate.snapshot().config_mutation_active
    finally:
        allow_cleanup.set()
        worker.join(2)
    wait_for(lambda: not gate.snapshot().config_mutation_active)


def test_http_terminal_authentication_and_fixed_runtime_options():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from codex_bridge_service.routes.terminal import router

    app = FastAPI()
    app.state.auth_token = "test-terminal-token"
    app.state.workspace_terminal = Mock()
    app.state.workspace_terminal.open.return_value = {"state": "starting"}
    app.include_router(router)
    with TestClient(app) as http:
        body = {"thread_id": "chat"}
        assert http.post("/terminal/open", json=body).status_code == 401
        headers = {"Authorization": "Bearer test-terminal-token", "X-Codex-Bridge-Api": "1"}
        for field in ("command", "cwd", "sandboxPolicy", "env"):
            assert http.post("/terminal/open", headers=headers, json={**body, field: "/"}).status_code == 422
        app.state.workspace_terminal.open.assert_not_called()
        assert http.post("/terminal/open", headers=headers, json=body).status_code == 200
        app.state.workspace_terminal.open.assert_called_once_with("chat", 80, 20)
