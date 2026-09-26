"""Isolation and admission regressions for explicitly selected Assist MCPs."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codex_bridge_service.assist_mcp import isolation_config, private_execution_directory, require_assist_layers
from codex_bridge_service.models import PublicThreadRecord, RunMode
from codex_bridge_service.routes import task_actions
from codex_bridge_service.storage import BridgeStorage
from codex_bridge_service.runtime_gate import RuntimeGateClosedError, RuntimeMutationConflictError
from codex_bridge_service.runtime_state import RuntimeStateError

from test_runtime_broker import (
    ValidatorBackedAppServer, _HomeAssistantProfileStorage, _active_ids,
    _broker, _complete, _requests, _wait_until,
)


def test_selected_assist_mcp_is_private_immutable_and_cold_resumed(tmp_path: Path, monkeypatch) -> None:
    storage = BridgeStorage(root_path=tmp_path / "state")
    project = storage.create_project(
        name="Assist", root_path=str(tmp_path / "workspace"),
        default_model="gpt-5.6-codex", default_thinking_level="high",
    )
    storage = _HomeAssistantProfileStorage(storage)
    peer = ValidatorBackedAppServer()
    peer.thread_unload_delay_seconds = 0
    tool_names = ["lights", "scripts"]
    calls = []
    unload_leases = []

    def current_config(workspace, selected, *, execution_cwd):
        calls.append((workspace, selected))
        return isolation_config({
            "home": {"enabled": True, "url": "https://mcp.vendor.example/mcp", "enabled_tools": list(tool_names)},
            "other": {"enabled": False},
        }, workspace, execution_cwd=execution_cwd)

    manager = SimpleNamespace(enabled=True, assist_thread_config=current_config)
    broker = _broker(storage, peer, mcp_manager=manager)
    native_request = peer.request

    def request_with_lease(method, params=None, **kwargs):
        if method == "thread/unsubscribe":
            unload_leases.append(broker.gate.snapshot().active_turns)
        return native_request(method, params, **kwargs)

    monkeypatch.setattr(peer, "request", request_with_lease)
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
        "title": "Assist", "prompt": "Lights", "assist": True,
        "mode": "observe", "web_search": "disabled", "assist_mcp_servers": ["home"],
    }
    try:
        first = client.post("/task-actions/start", headers=headers, json=payload)
        assert first.status_code == 202, first.text
        thread_id = first.json()["thread_id"]
        _wait_until(lambda: len(_requests(peer, "turn/start")) == 1)
        saved = storage.load_thread(thread_id)
        assert saved.assist_mcp_servers == ["home"]
        assert "assist_mcp_servers" not in PublicThreadRecord.from_thread_view(storage.get_thread(thread_id)).model_dump()
        configured = _requests(peer, "thread/start")[0]["config"]
        private_cwd = str(storage.root.resolve() / "assist-runtime")
        assert _requests(peer, "thread/start")[0]["cwd"] == private_cwd
        assert _requests(peer, "turn/start")[0]["cwd"] == private_cwd
        assert "sandboxPolicy" not in _requests(peer, "turn/start")[0]
        assert configured["project_doc_max_bytes"] == 0
        assert configured["permissions"]["ha_observe"]["filesystem"][project.root_path] == "read"
        assert configured["mcp_servers"]["other"] == {"enabled": False}
        assert configured["mcp_servers"]["home"]["enabled_tools"] == ["lights", "scripts"]
        with pytest.raises(RuntimeMutationConflictError):
            broker.gate.acquire_config_mutation()
        _, remote_id, turn_id = _active_ids(storage, thread_id)
        _complete(peer, remote_thread_id=remote_id, turn_id=turn_id)
        _wait_until(lambda: broker.get_task_action_run("a" * 32).status == "completed")
        assert _requests(peer, "thread/unsubscribe")
        assert broker.gate.snapshot().active_turns == 0
        assert client.post("/task-actions/start", headers=headers, json={**payload, "assist_mcp_servers": []}).status_code == 409
        continuation = {
            "task_id": "b" * 32, "thread_id": thread_id, "prompt": "Lights again",
            "assist": True, "web_search": "disabled", "assist_mcp_servers": ["home"],
        }
        assert client.post("/task-actions/continue", headers=headers, json={**continuation, "assist_mcp_servers": []}).json()["detail"]["code"] == "assist_selection_changed"
        tool_names[:] = ["lights"]
        peer.script("thread/unsubscribe", {"status": "unsubscribed"}, {"status": "notLoaded"})
        second = client.post("/task-actions/continue", headers=headers, json=continuation)
        assert second.status_code == 202, second.text
        _wait_until(lambda: len(_requests(peer, "turn/start")) == 2)
        assert _requests(peer, "turn/start")[1]["cwd"] == private_cwd
        assert "sandboxPolicy" not in _requests(peer, "turn/start")[1]
        methods = [name for name, _ in peer.requests]
        resume_index = methods.index("thread/resume")
        assert methods[resume_index - 2:resume_index] == ["thread/unsubscribe", "thread/unsubscribe"]
        assert _requests(peer, "thread/resume")[0]["config"]["mcp_servers"]["home"]["enabled_tools"] == ["lights"]
        assert len(calls) == 2
        assert unload_leases and all(value == 1 for value in unload_leases)
    finally:
        broker.close()


@pytest.mark.parametrize("selection", [["home", "home"], ["http://local/mcp"], ["HOME"], [1], ["x"] * 33])
def test_task_selection_validation_and_ordinary_action_boundary(selection: list) -> None:
    with pytest.raises(ValueError):
        task_actions.StartTaskRequest(
            task_id="a" * 32, project_id="project", title="Assist", prompt="Hello",
            assist=True, assist_mcp_servers=selection,
        )


def test_normal_task_cannot_request_assist_mcp_policy() -> None:
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        task_actions._require_assist_safety(
            None, assist=False, mode=RunMode.OBSERVE,
            web_search="disabled", assist_mcp_servers=[],
        )
    assert error.value.status_code == 422


@pytest.mark.parametrize("layers", [
    None, {}, [{"name": {"type": "future"}, "config": {}}],
    [{"name": {"type": []}, "config": {}}],
    [{"name": {"type": "user"}, "config": []}],
    [{"name": {"type": "user"}, "config": {}, "disabledReason": False}],
    [{"name": {"type": "legacyManagedConfigTomlFromFile"}, "config": {"projects": {"root": {"trust_level": "trusted"}}}}],
    [{"name": {"type": "legacyManagedConfigTomlFromMdm"}, "config": {"features": {"plugins": True}}}],
])
def test_unknown_or_higher_priority_configuration_cannot_override_assist(layers: object) -> None:
    with pytest.raises(ValueError):
        require_assist_layers({"layers": layers})


def test_disabled_and_empty_managed_layers_are_harmless() -> None:
    require_assist_layers({"layers": [
        {"name": {"type": "legacyManagedConfigTomlFromFile"}, "config": {}},
        {"name": {"type": "legacyManagedConfigTomlFromMdm"}, "config": {"features": {"plugins": True}}, "disabledReason": "disabled"},
    ]})


def test_private_execution_directory_is_empty_and_separate(tmp_path: Path) -> None:
    root, workspace = tmp_path / "private", tmp_path / "workspace"
    root.mkdir()
    workspace.mkdir()
    cwd = private_execution_directory(root, workspace)
    assert cwd == root / "assist-runtime"
    assert not list(cwd.iterdir())
    assert private_execution_directory(root, workspace) == cwd
    (cwd / "unexpected").touch()
    with pytest.raises(Exception):
        private_execution_directory(root, workspace)
    with pytest.raises(Exception):
        private_execution_directory(workspace, workspace / "nested")


@pytest.mark.parametrize("failure", ["timeout", "invalid-policy"])
def test_unacknowledged_assist_thread_start_cannot_free_live_native_session(
    tmp_path: Path, monkeypatch, failure: str,
) -> None:
    storage = BridgeStorage(root_path=tmp_path / "state")
    project = storage.create_project(name="Assist", root_path=str(tmp_path / "workspace"))
    thread = storage.create_task_thread(
        action_id="a" * 32, fingerprint="f" * 64, title="Assist",
        project_id=project.project_id, mode=RunMode.OBSERVE,
        assist_origin=True, assist_mcp_servers=["home"],
    )
    peer = ValidatorBackedAppServer()
    peer.thread_unload_delay_seconds = 0
    manager = SimpleNamespace(enabled=True, assist_thread_config=lambda workspace, _, **kwargs: isolation_config({}, workspace, **kwargs))
    broker = _broker(_HomeAssistantProfileStorage(storage), peer, mcp_manager=manager)
    abort_leases = []
    native_request, native_abort = peer.request, peer.abort_generation

    def request(method, params=None, **kwargs):
        result = native_request(method, params, **kwargs)
        if method == "thread/start":
            if failure == "timeout":
                raise TimeoutError("Synthetic lost acknowledgement")
            result["approvalPolicy"] = "never"
        return result

    def abort(generation):
        abort_leases.append(broker.gate.snapshot().active_turns)
        return native_abort(generation)

    monkeypatch.setattr(peer, "request", request)
    monkeypatch.setattr(peer, "abort_generation", abort)
    try:
        broker.submit_prompt(
            thread.thread_id, "Hello", client_request_id="ha-action:" + "a" * 32,
            unattended=True, assist=True, web_search="disabled",
        )
        _wait_until(lambda: broker.get_task_action_run("a" * 32).status == "failed")
        assert len(_requests(peer, "thread/start")) == 1
        assert not _requests(peer, "turn/start")
        assert peer.aborted_generations == [1]
        assert abort_leases == [1]
        assert not broker._assist_loading_runs
        assert broker.gate.snapshot().active_turns == 0
    finally:
        broker.close()


@pytest.mark.parametrize("abort_result", [True, False, "error"])
def test_uncertain_assist_unload_aborts_before_freeing_admission(
    tmp_path: Path, monkeypatch, abort_result: object,
) -> None:
    storage = BridgeStorage(root_path=tmp_path / "state")
    project = storage.create_project(name="Assist", root_path=str(tmp_path / "workspace"))
    thread = storage.create_task_thread(
        action_id="a" * 32, fingerprint="f" * 64, title="Assist",
        project_id=project.project_id, mode=RunMode.OBSERVE,
        assist_origin=True, assist_mcp_servers=[],
    )
    peer = ValidatorBackedAppServer()
    peer.thread_unload_delay_seconds = 0
    manager = SimpleNamespace(enabled=True, assist_thread_config=lambda workspace, _, **kwargs: isolation_config({}, workspace, **kwargs))
    broker = _broker(_HomeAssistantProfileStorage(storage), peer, mcp_manager=manager)
    abort_leases = []
    native_abort = peer.abort_generation

    def abort(generation):
        abort_leases.append(broker.gate.snapshot().active_turns)
        if abort_result == "error":
            raise RuntimeError("Private native shutdown failure")
        return native_abort(generation) if abort_result else False

    monkeypatch.setattr(peer, "abort_generation", abort)
    try:
        run = broker.submit_prompt(
            thread.thread_id, "Hello", client_request_id="ha-action:" + "a" * 32,
            unattended=True, assist=True, web_search="disabled",
        )
        _, remote_id, turn_id = _active_ids(storage, thread.thread_id)
        peer.script("thread/unsubscribe", RuntimeError("Private native unload failure"))
        _complete(peer, remote_thread_id=remote_id, turn_id=turn_id)
        _wait_until(lambda: broker.get_task_action_run("a" * 32).status == "completed")
        assert abort_leases == [1]
        assert broker.gate.snapshot().active_turns == 0
        assert broker.get_task_action_run("a" * 32).run_id == run.run_id
        if abort_result is True:
            assert peer.aborted_generations == [1]
            assert not broker.gate.snapshot().closed
        else:
            assert broker.gate.snapshot().closed
            with pytest.raises(RuntimeGateClosedError):
                broker.gate.acquire_config_mutation()
            with pytest.raises(RuntimeGateClosedError):
                broker.gate.reserve_prompt(client_request_id="next")
    finally:
        monkeypatch.setattr(peer, "abort_generation", native_abort)
        broker.close()


@pytest.mark.parametrize("abort_result", [True, False, "error"])
def test_fatal_store_failure_closes_admission_before_assist_teardown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, abort_result: object,
) -> None:
    storage = BridgeStorage(root_path=tmp_path / "state")
    project = storage.create_project(name="Assist", root_path=str(tmp_path / "workspace"))
    thread = storage.create_task_thread(
        action_id="a" * 32, fingerprint="f" * 64, title="Assist",
        project_id=project.project_id, mode=RunMode.OBSERVE,
        assist_origin=True, assist_mcp_servers=["home"],
    )
    peer = ValidatorBackedAppServer()
    peer.thread_unload_delay_seconds = 0
    manager = SimpleNamespace(
        enabled=True,
        assist_thread_config=lambda workspace, _, **kwargs: isolation_config(
            {}, workspace, **kwargs,
        ),
    )
    broker = _broker(_HomeAssistantProfileStorage(storage), peer, mcp_manager=manager)
    native_save, native_abort = broker._store.save, peer.abort_generation
    failures = []
    abort_snapshots = []

    def fail_after_native_start(state):
        if any(
            run.codex_thread_id is not None and run.status == "starting"
            for run in state.runs.values()
        ):
            failures.append(True)
            raise RuntimeStateError("Synthetic private state failure")
        return native_save(state)

    def abort(generation):
        abort_snapshots.append(broker.gate.snapshot())
        if abort_result == "error":
            raise RuntimeError("Synthetic native teardown failure")
        return native_abort(generation) if abort_result else False

    monkeypatch.setattr(broker._store, "save", fail_after_native_start)
    monkeypatch.setattr(peer, "abort_generation", abort)
    try:
        broker.submit_prompt(
            thread.thread_id, "Hello", client_request_id="ha-action:" + "a" * 32,
            unattended=True, assist=True, web_search="disabled",
        )
        _wait_until(lambda: broker.get_task_action_run("a" * 32).status == "interrupted")
        assert failures == [True]
        assert len(_requests(peer, "thread/start")) == 1
        assert not _requests(peer, "turn/start")
        assert len(abort_snapshots) == 1
        assert abort_snapshots[0].active_turns == 1
        assert abort_snapshots[0].closed
        assert broker.gate.snapshot().active_turns == 0
        assert broker.gate.snapshot().closed
        assert peer.aborted_generations == ([1] if abort_result is True else [])
        with pytest.raises(RuntimeGateClosedError):
            broker.gate.acquire_config_mutation()
        with pytest.raises(RuntimeGateClosedError):
            broker.gate.acquire_auth_mutation()
        with pytest.raises(RuntimeGateClosedError):
            broker.gate.reserve_prompt(client_request_id="next")
    finally:
        monkeypatch.setattr(peer, "abort_generation", native_abort)
        broker.close()
