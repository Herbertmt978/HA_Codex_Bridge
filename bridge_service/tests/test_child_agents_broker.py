"""Schema-backed broker seam regressions, retaining aggregate event boundaries."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from codex_bridge_service.child_agents import ChildAgentError
from codex_bridge_service.codex_app_server_contract import load_bundled_protocol_contract
from codex_bridge_service.event_store import project_public_event_payload
from test_child_agents import CHILD, SIBLING, spawn, thread
from test_runtime_broker import (
    ValidatorBackedAppServer, _active_ids, _broker, _complete, _requests,
    _home_assistant_operation_thread, _storage_and_thread, _wait_until,
)


def test_correlated_parent_hook_and_single_child_control(tmp_path):
    storage, parent, _ = _home_assistant_operation_thread(tmp_path)
    runtime = ValidatorBackedAppServer()
    runtime.protocol_contract = load_bundled_protocol_contract()
    marker = "a" * 64
    broker = _broker(storage, runtime, provider_admission_check=lambda: True,
                     provider_account_owner_marker=lambda: marker)
    try:
        broker.submit_prompt(parent.thread_id, "Review tests", client_request_id="parent-start")
        _wait_until(lambda: len(_requests(runtime, "turn/start")) == 1)
        parent_run, native_parent, parent_turn = _active_ids(storage, parent.thread_id)
        assert broker._state.runs[parent_run].account_owner_marker == marker
        cwd = _requests(runtime, "thread/resume")[0]["cwd"]
        for index, native in enumerate((CHILD, SIBLING)):
            # Item IDs are public coalescing keys, distinct from native child IDs.
            item = spawn(native, item_id=f"spawn-item-{index}")
            item["senderThreadId"] = native_parent
            runtime.emit_notification("item/completed", {
                "threadId": native_parent, "turnId": parent_turn,
                "completedAtMs": 1_783_936_800_000, "item": item,
            })
        rows = broker.list_child_agents(parent.thread_id)["children"]
        assert len(rows) == 2 and all(row["stale"] for row in rows)
        response = thread(CHILD, parent=native_parent, cwd=cwd)
        runtime.script("thread/read", response, deepcopy(response))
        first = broker.refresh_child_agent(parent.thread_id, rows[0]["child_id"])
        stopped = broker.stop_child_agent(parent.thread_id, first["child_id"], first["revision"], "stop-child")
        assert stopped["outcome"] == "accepted"
        assert _requests(runtime, "turn/interrupt") == [{"threadId": CHILD, "turnId": response["thread"]["turns"][0]["id"]}]
        assert runtime.aborted_generations == []
        assert storage.load_thread(parent.thread_id).status == "running"
        events = storage.list_thread_events(parent.thread_id)
        payloads = [event.payload for event in events]
        assert not any(CHILD in str(payload) or SIBLING in str(payload) for payload in payloads)
        collab_payloads = [event.payload for event in events
                           if event.event_type == "item.completed"
                           and event.payload.get("item_type") == "collabAgentToolCall"]
        assert collab_payloads == [{
            "run_id": parent_run, "item_id": f"spawn-item-{index}",
            "item_type": "collabAgentToolCall", "status": "completed",
            "operation": "spawnAgent", "agent_state_counts": {"running": 1},
        } for index in range(2)]
        public_payloads = [project_public_event_payload(event.event_type, event.payload)
                           for event in events]
        assert not any(CHILD in str(payload) or SIBLING in str(payload)
                       for payload in public_payloads)
        marker = "b" * 64
        before = len(runtime.requests)
        with pytest.raises(ChildAgentError, match="stale"):
            broker.refresh_child_agent(parent.thread_id, rows[1]["child_id"])
        assert len(runtime.requests) == before
        marker = "a" * 64
        _complete(runtime, remote_thread_id=native_parent, turn_id=parent_turn)
        _wait_until(lambda: storage.load_thread(parent.thread_id).status == "idle")
        assert not any(row["can_stop"] for row in broker.list_child_agents(parent.thread_id)["children"])
    finally:
        broker.close()


def test_unrelated_native_notification_does_not_create_projection(tmp_path):
    storage, parent = _storage_and_thread(tmp_path)
    runtime = ValidatorBackedAppServer()
    broker = _broker(storage, runtime)
    try:
        runtime.emit_notification("item/completed", {
            "threadId": "unrelated-parent", "turnId": "unrelated-turn",
            "completedAtMs": 1_783_936_800_000, "item": spawn(),
        })
        assert broker.list_child_agents(parent.thread_id)["children"] == []
    finally:
        broker.close()


def test_parent_completes_during_stop_verification_never_dispatches_child_interrupt(tmp_path):
    storage, parent, _ = _home_assistant_operation_thread(tmp_path)
    entered = Event()
    release = Event()

    class BlockingRuntime(ValidatorBackedAppServer):
        block_read = False

        def request(self, method, params=None, *, timeout_seconds=None):
            if method == "thread/read" and self.block_read:
                entered.set()
                assert release.wait(5)
            return super().request(method, params, timeout_seconds=timeout_seconds)

    runtime = BlockingRuntime()
    runtime.protocol_contract = load_bundled_protocol_contract()
    broker = _broker(storage, runtime, provider_admission_check=lambda: True,
                     provider_account_owner_marker=lambda: "a" * 64,
                     turn_timeout_seconds=30)
    try:
        broker.submit_prompt(parent.thread_id, "Review tests", client_request_id="parent-race")
        _wait_until(lambda: len(_requests(runtime, "turn/start")) == 1)
        parent_run, native_parent, parent_turn = _active_ids(storage, parent.thread_id)
        assert broker._state.runs[parent_run].account_owner_marker == "a" * 64
        item = spawn()
        item["senderThreadId"] = native_parent
        runtime.emit_notification("item/completed", {"threadId": native_parent,
                                  "turnId": parent_turn, "completedAtMs": 1_783_936_800_000,
                                  "item": item})
        child_id = broker.list_child_agents(parent.thread_id)["children"][0]["child_id"]
        response = thread(CHILD, parent=native_parent, cwd=_requests(runtime, "thread/resume")[0]["cwd"])
        runtime.script("thread/read", response, deepcopy(response))
        row = broker.refresh_child_agent(parent.thread_id, child_id)
        runtime.block_read = True
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(broker.stop_child_agent, parent.thread_id, child_id,
                                      row["revision"], "stop-parent-race")
            assert entered.wait(3)
            _complete(runtime, remote_thread_id=native_parent, turn_id=parent_turn)
            _wait_until(lambda: storage.load_thread(parent.thread_id).status == "idle")
            release.set()
            with pytest.raises(ChildAgentError, match="stale"):
                pending.result(timeout=5)
        assert _requests(runtime, "turn/interrupt") == []
        assert runtime.aborted_generations == []
        assert broker.child_agents.state.stops == {}
    finally:
        release.set()
        broker.close()
