"""Exercise host grants through the real broker and pinned provider validator."""

from copy import deepcopy
import json

import httpx
import pytest

from codex_bridge_service.host_access import HostAccessError, HostAccessManager, HostPairing
from codex_bridge_service.host_access_contract import HostIdentity, host_dynamic_tool_spec
from codex_bridge_service.models import RunMode
from test_runtime_broker import (
    ValidatorBackedAppServer, _active_ids, _broker, _complete, _requests, _storage_and_thread,
)


@pytest.fixture
def environment(tmp_path):
    storage, thread = _storage_and_thread(tmp_path, mode=RunMode.HAOS_FULL_ACCESS)
    identity = HostIdentity(machine_fingerprint="d" * 64, companion_id="a" * 32, hostname="ha-test", os_version="18.3")
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path == "/status":
            return httpx.Response(200, json={"ready": True, "identity": identity.model_dump(), "session_id": "b" * 32})
        if request.method == "DELETE":
            return httpx.Response(200, json={"stopped": True})
        return httpx.Response(200, json={"status": "completed", "exit_code": 0, "output": "ha-test", "truncated": False})

    manager = HostAccessManager(storage.root, transport=httpx.MockTransport(handle))
    manager.pair(HostPairing(host="172.30.33.5", token="s" * 64, companion_id="a" * 32))
    grant = manager.enable(identity.scope_revision, True)["grant_id"]
    storage.update_thread(thread.thread_id, host_access_grant=grant)
    client = ValidatorBackedAppServer()
    client.enable_experimental_api = True
    broker = _broker(storage, client, host_access=manager)
    yield storage, thread, manager, client, broker, calls
    broker.close()
    manager.close()


def start(environment, **kwargs):
    storage, thread, _, _, broker, _ = environment
    broker.submit_prompt(thread.thread_id, "Read the HAOS machine name", **kwargs)
    return _active_ids(storage, thread.thread_id)


def callback(remote_thread, turn, **changes):
    return {
        "namespace": "ha_host", "threadId": remote_thread, "turnId": turn,
        "callId": "host-call-1", "tool": "execute", "arguments": {"command": "hostname"},
        **changes,
    }


def test_host_namespace_requires_selected_mode_and_fresh_grant(environment):
    storage, thread, _, client, _, calls = environment
    _, remote_thread, turn = start(environment)
    assert _requests(client, "thread/start")[-1]["dynamicTools"] == [host_dynamic_tool_spec()]
    result = client.emit_request("item/tool/call", callback(remote_thread, turn))
    assert result["success"] is True
    assert json.loads(result["contentItems"][0]["text"])["output"] == "ha-test"
    assert len([call for call in calls if call.url.path == "/execute"]) == 1
    events = [event for event in storage.list_thread_events(thread.thread_id) if event.payload.get("item_type") == "haHostCommand"]
    assert [event.event_type for event in events] == ["item.started", "item.completed"]
    assert events[-1].payload["host_outcome"] == "completed"
    assert events[-1].payload["duration_ms"] >= 0
    assert all("command" not in event.payload and "output" not in event.payload for event in events)


def test_replay_is_at_most_once_and_changed_arguments_are_rejected(environment):
    _, _, _, client, _, calls = environment
    _, remote_thread, turn = start(environment)
    request = callback(remote_thread, turn)
    first = client.emit_request("item/tool/call", request)
    assert client.emit_request("item/tool/call", deepcopy(request)) == first
    request["arguments"] = {"command": "whoami"}
    assert client.emit_request("item/tool/call", request)["success"] is False
    assert len([call for call in calls if call.url.path == "/execute"]) == 1


@pytest.mark.parametrize("changes", [
    {"turnId": "old-turn"}, {"threadId": "other-thread"}, {"unexpected": "value"},
    {"tool": "arbitrary"}, {"arguments": {"command": "hostname", "env": {"TOKEN": "x"}}},
])
def test_unsolicited_or_malformed_host_callbacks_cannot_execute(environment, changes):
    _, _, _, client, _, calls = environment
    _, remote_thread, turn = start(environment)
    result = client.emit_request("item/tool/call", callback(remote_thread, turn, **changes))
    assert result["success"] is False
    assert not any(call.url.path == "/execute" for call in calls)


def test_native_host_session_can_resume_only_while_its_registration_is_known(environment):
    _, _, _, client, _, _ = environment
    _, remote_thread, turn = start(environment)
    _complete(client, remote_thread_id=remote_thread, turn_id=turn)
    _, resumed, next_turn = start(environment, client_request_id="next")
    assert _requests(client, "thread/resume")[-1]["threadId"] == remote_thread
    assert client.emit_request("item/tool/call", callback(resumed, next_turn))["success"] is True


def test_revocation_invalidates_saved_selection_and_active_callbacks(environment):
    _, _, manager, client, _, calls = environment
    _, remote_thread, turn = start(environment)
    manager.revoke()
    assert client.emit_request("item/tool/call", callback(remote_thread, turn))["success"] is False
    assert not any(call.url.path == "/execute" for call in calls)
    with pytest.raises(HostAccessError):
        start(environment, client_request_id="after-revoke")


def test_stop_revokes_before_provider_turn_completion(environment):
    _, thread, manager, client, broker, _ = environment
    run_id, remote_thread, turn = start(environment)
    lease = manager._leases[run_id]
    broker.cancel_run(thread.thread_id)
    assert manager.active(lease) is False
    assert client.emit_request("item/tool/call", callback(remote_thread, turn))["success"] is False


def test_unattended_host_work_needs_its_own_acknowledgement(environment):
    _, _, _, client, _, _ = environment
    with pytest.raises(HostAccessError, match="unattended"):
        start(environment, unattended=True)
    _, remote_thread, turn = start(environment, unattended=True, host_unattended_approved=True)
    assert client.emit_request("item/tool/call", callback(remote_thread, turn))["success"] is True


def test_mode_change_detaches_host_session_and_keeps_ordinary_mode_confined(environment):
    storage, thread, _, client, _, calls = environment
    _, remote_thread, turn = start(environment)
    with pytest.raises(ValueError, match="Stop"):
        storage.update_thread(thread.thread_id, mode=RunMode.FULL_AUTO)
    _complete(client, remote_thread_id=remote_thread, turn_id=turn)
    changed = storage.update_thread(thread.thread_id, mode=RunMode.FULL_AUTO)
    assert changed.codex_thread_id is None and changed.host_access_grant is None
    _, normal_thread, normal_turn = start(environment, client_request_id="workspace")
    params = _requests(client, "thread/start")[-1]
    assert "dynamicTools" not in params
    assert params["config"]["default_permissions"] == "ha_bridge"
    assert client.emit_request("item/tool/call", callback(normal_thread, normal_turn))["success"] is False
    assert not any(call.url.path == "/execute" for call in calls)
