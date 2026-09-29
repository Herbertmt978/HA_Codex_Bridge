from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codex_bridge_service.child_agents import ChildAgents, ChildAgentError, ChildOwner
from codex_bridge_service.codex_app_server_contract import (
    AppServerProtocolValidator, load_bundled_protocol_contract,
)
from codex_bridge_service.routes.child_agents import router

PARENT = "0199a000-0000-7000-8000-000000000001"
CHILD = "0199a000-0000-7000-8000-000000000002"
SIBLING = "0199a000-0000-7000-8000-000000000003"
OTHER = "0199a000-0000-7000-8000-000000000004"
TURN = "0199a000-0000-7000-8000-000000000005"
VALIDATOR = AppServerProtocolValidator(load_bundled_protocol_contract())


def owner():
    return ChildOwner("parent", "run", PARENT, "/config/workspaces/project", "a" * 64, 1, True)


def spawn(native=CHILD, *, status="running", item_id="spawn", message=None):
    return {
        "id": item_id, "type": "collabAgentToolCall", "tool": "spawnAgent",
        "status": "completed", "senderThreadId": PARENT,
        "receiverThreadIds": [native], "prompt": "Review the tests",
        "agentsStates": {native: {"status": status, "message": message}},
    }


def thread(native=CHILD, parent=PARENT, cwd="/config/workspaces/project", status="inProgress", turn=TURN):
    return {"thread": {
        "id": native, "parentThreadId": parent, "cwd": cwd,
        "source": {"subAgent": {"thread_spawn": {"parent_thread_id": parent, "depth": 1}}},
        "agentNickname": "Reviewer", "agentRole": "review",
        "cliVersion": VALIDATOR.contract.codex_version.removeprefix("codex-cli "),
        "createdAt": 1, "updatedAt": 2,
        "ephemeral": False, "modelProvider": "openai", "preview": "Review the tests",
        "projectId": None, "sessionId": "session", "status": {"type": "active", "activeFlags": []},
        "turns": [{"id": turn, "status": status, "items": []}],
    }}


class Runtime:
    generation = 1
    ready = True

    def __init__(self):
        self.calls = []
        self.responses = {CHILD: thread(), SIBLING: thread(SIBLING), OTHER: thread(OTHER)}
        self.fail_stop = False

    def request(self, method, params, **kwargs):
        VALIDATOR.validate_client_request({"id": len(self.calls) + 1, "method": method, "params": params})
        self.calls.append((method, deepcopy(params)))
        if method == "thread/read":
            response = deepcopy(self.responses[params["threadId"]])
            VALIDATOR.validate_client_response(method, result=response)
            return response
        if method == "turn/interrupt":
            if self.fail_stop:
                raise RuntimeError("private runtime failure")
            return {}
        raise AssertionError("No child creation or parent control is permitted")


def service(tmp_path):
    runtime = Runtime()
    agents = ChildAgents(tmp_path, runtime)
    agents.observe(owner(), spawn())
    return agents, runtime, agents.list("parent", owner())["children"][0]["child_id"]


def test_actual_schema_identity_and_targeted_stop_preserve_siblings(tmp_path):
    agents, runtime, child_id = service(tmp_path)
    agents.observe(owner(), spawn(SIBLING, item_id="spawn-sibling"))
    row = agents.refresh(owner(), child_id)
    assert row["can_stop"] and not row["can_follow_up"]
    result = agents.stop(owner(), child_id, row["revision"], "stop-one")
    assert result["outcome"] == "accepted"
    assert [call for call in runtime.calls if call[0] == "turn/interrupt"] == [
        ("turn/interrupt", {"threadId": CHILD, "turnId": TURN}),
    ]
    assert len(agents.list("parent", owner())["children"]) == 2
    assert not any(params["threadId"] in {PARENT, SIBLING} for _, params in runtime.calls)
    assert agents.stop(owner(), child_id, row["revision"], "stop-one")["outcome"] == "accepted"
    assert len(runtime.calls) == 3  # two verified reads and one exact child interrupt


@pytest.mark.parametrize("change", [
    {"thread_id": "other-parent"}, {"run_id": "other-run"}, {"workspace": "/other"},
    {"account": "b" * 64}, {"account": None}, {"generation": 2}, {"active": False},
])
def test_controls_reject_other_owner_before_runtime_call(tmp_path, change):
    agents, runtime, child_id = service(tmp_path)
    row = agents.refresh(owner(), child_id)
    runtime.calls.clear()
    with pytest.raises(ChildAgentError):
        agents.stop(replace(owner(), **change), child_id, row["revision"], "request")
    assert runtime.calls == []


@pytest.mark.parametrize("change", [
    {"id": SIBLING}, {"parentThreadId": OTHER}, {"cwd": "/other/workspace"},
    {"source": {"subAgent": {"thread_spawn": {"parent_thread_id": OTHER, "depth": 1}}}},
    {"source": "appServer"},
])
def test_read_reconciles_native_identity_parent_and_workspace(tmp_path, change):
    agents, runtime, child_id = service(tmp_path)
    runtime.responses[CHILD]["thread"].update(change)
    with pytest.raises(ChildAgentError, match="ownership"):
        agents.refresh(owner(), child_id)
    assert not agents.list("parent", owner())["children"][0]["can_stop"]
    assert not any(call[0] == "turn/interrupt" for call in runtime.calls)


def test_no_counts_unknown_state_or_nonspawn_identity_can_invent_child(tmp_path):
    agents = ChildAgents(tmp_path, Runtime())
    fake = spawn()
    fake["senderThreadId"] = OTHER
    agents.observe(owner(), fake)
    fake = spawn()
    fake["tool"] = "listAgents"
    agents.observe(owner(), fake)
    agents.observe(owner(), {"type": "subAgentActivity", "kind": "started", "agentThreadId": CHILD})
    assert agents.list("parent", owner())["children"] == []


def test_replay_reordering_completion_and_restart_labels(tmp_path):
    agents, runtime, child_id = service(tmp_path)
    agents.observe(owner(), spawn(status="completed", item_id="wait", message="Tests passed"))
    before = agents.list("parent", owner())["children"][0]
    agents.observe(owner(), spawn(item_id="late-running"))
    agents.observe(owner(), spawn(status="completed", item_id="wait", message="Tests passed"))
    assert agents.list("parent", owner())["children"][0] == before
    restarted = ChildAgents(tmp_path, runtime)
    retained = restarted.list("parent", owner())["children"][0]
    assert retained["child_id"] == child_id
    assert retained["status"] == "completed" and retained["result"] == "Tests passed"
    assert retained["stale"] and not retained["can_stop"]
    with pytest.raises(ChildAgentError, match="stale"):
        restarted.refresh(owner(), child_id)
    # Only fresh broker-correlated parent evidence permits inspection again.
    restarted.observe(owner(), spawn(item_id="fresh-parent-reference"))
    assert restarted.list("parent", owner())["children"][0]["status"] == "completed"
    runtime.responses[CHILD] = thread(status="completed")
    assert not restarted.refresh(owner(), child_id)["stale"]


def test_completion_or_new_turn_race_never_interrupts_new_task(tmp_path):
    agents, runtime, child_id = service(tmp_path)
    row = agents.refresh(owner(), child_id)
    runtime.responses[CHILD] = thread(status="completed")
    with pytest.raises(ChildAgentError, match="turn_changed"):
        agents.stop(owner(), child_id, row["revision"], "stop-completed")
    assert not any(call[0] == "turn/interrupt" for call in runtime.calls)


def test_new_active_turn_and_generation_change_never_reuse_old_authority(tmp_path):
    agents, runtime, child_id = service(tmp_path)
    row = agents.refresh(owner(), child_id)
    runtime.responses[CHILD] = thread(turn="0199a000-0000-7000-8000-000000000006")
    with pytest.raises(ChildAgentError, match="turn_changed"):
        agents.stop(owner(), child_id, row["revision"], "stop-new-task")
    assert not any(call[0] == "turn/interrupt" for call in runtime.calls)
    runtime.generation = 2
    assert agents.list("parent", owner())["children"][0]["stale"]
    assert not agents.list("parent", owner())["children"][0]["can_stop"]


def test_retention_bounds_and_cross_parent_duplicate_identity(tmp_path):
    agents, runtime, child_id = service(tmp_path)
    agents.observe(replace(owner(), thread_id="other-parent"), spawn())
    assert agents.list("other-parent", None)["children"] == []
    for number in range(1, 70):
        native = f"0199a000-0000-7000-8001-{number:012d}"
        agents.observe(owner(), spawn(native, item_id=f"spawn-{number}"))
    assert len(agents.list("parent", owner())["children"]) == 64
    assert agents.path.stat().st_size <= 8 * 1024 * 1024
    assert len(runtime.calls) == 0
    assert child_id == agents.list("parent", owner())["children"][0]["child_id"]


def test_corrupt_projection_fails_closed_without_mutating_runtime(tmp_path):
    (tmp_path / "child-agents.sqlite3").write_bytes(b"invalid database")
    runtime = Runtime()
    agents = ChildAgents(tmp_path, runtime)
    assert not agents.available
    with pytest.raises(ChildAgentError):
        agents.list("parent", owner())
    assert runtime.calls == []


def test_authorised_parent_purge_retains_other_parents_and_no_control(tmp_path):
    agents, runtime, child_id = service(tmp_path)
    row = agents.refresh(owner(), child_id)
    agents.stop(owner(), child_id, row["revision"], "stop-before-delete")
    other_owner = replace(owner(), thread_id="other-parent", native_parent=OTHER)
    item = spawn(SIBLING, item_id="other-parent-spawn")
    item["senderThreadId"] = OTHER
    agents.observe(other_owner, item)
    before = len(runtime.calls)
    agents.purge({"parent"})
    assert agents.list("parent", None)["children"] == []
    assert len(agents.list("other-parent", other_owner)["children"]) == 1
    assert agents.state.stops == {}
    assert len(runtime.calls) == before


def test_uncertain_stop_is_persisted_not_replayed_or_parent_aborted(tmp_path):
    agents, runtime, child_id = service(tmp_path)
    row = agents.refresh(owner(), child_id)
    runtime.fail_stop = True
    assert agents.stop(owner(), child_id, row["revision"], "stop-uncertain")["outcome"] == "unknown"
    count = len(runtime.calls)
    assert agents.stop(owner(), child_id, row["revision"], "stop-uncertain")["outcome"] == "unknown"
    assert len(runtime.calls) == count
    with pytest.raises(ChildAgentError, match="request_conflict"):
        agents.stop(owner(), child_id, row["revision"] + 1, "stop-uncertain")
    restored = ChildAgents(tmp_path, runtime)
    assert restored.stop(owner(), child_id, row["revision"], "stop-uncertain")["outcome"] == "unknown"
    assert len(runtime.calls) == count


def test_public_text_filter_native_identifiers_and_reasoning_not_exposed(tmp_path):
    agents, runtime, child_id = service(tmp_path)
    sensitive = spawn(status="completed", item_id="done", message="Bearer reusable-private-value")
    sensitive["prompt"] = "password=private-value"
    agents.observe(owner(), sensitive)
    runtime.responses[CHILD] = thread(status="completed")
    runtime.responses[CHILD]["thread"]["turns"][0]["items"] = [
        {"id": "thought", "type": "reasoning", "summary": [], "content": []},
        {"id": "answer", "type": "agentMessage", "text": "Tests passed", "phase": "final_answer"},
    ]
    row = agents.refresh(owner(), child_id)
    assert row["result"] == "Tests passed"
    assert "native_child" not in row and "turn_id" not in row and "account" not in row
    assert "private-value" not in str(row)


def test_followup_is_explicitly_unavailable_without_any_dispatch(tmp_path):
    agents, runtime, child_id = service(tmp_path)
    with pytest.raises(ChildAgentError, match="follow_up_unavailable"):
        agents.follow_up(owner(), child_id)
    assert runtime.calls == []


def test_parent_scoped_authenticated_route_and_unsupported_followup(tmp_path):
    agents, runtime, child_id = service(tmp_path)
    app = FastAPI()
    app.include_router(router)
    app.state.auth_token = "a" * 48
    app.state.runner = SimpleNamespace(
        supports_subagents=True,
        list_child_agents=lambda parent: agents.list(parent, owner()),
        refresh_child_agent=lambda parent, child: agents.refresh(replace(owner(), thread_id=parent), child),
        stop_child_agent=lambda parent, child, revision, request: agents.stop(
            replace(owner(), thread_id=parent), child, revision, request),
    )
    with TestClient(app) as client:
        assert client.get("/threads/parent/children").status_code == 401
        headers = {"Authorization": "Bearer " + app.state.auth_token, "X-Codex-Bridge-Api": "1"}
        assert client.get("/threads/parent/children", headers=headers).json()["children"][0]["child_id"] == child_id
        assert client.post(f"/threads/other/children/{child_id}/refresh", headers=headers).status_code == 404
        result = client.post(f"/threads/parent/children/{child_id}/follow-up", headers=headers,
                             json={"revision": 1, "client_request_id": "followup", "text": "Review this"})
        assert result.status_code == 409 and result.json()["detail"]["code"] == "child_follow_up_unavailable"
        assert runtime.calls == []
