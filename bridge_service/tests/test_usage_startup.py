"""Causal startup/duration regressions using the existing validated native peer."""

from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import codex_bridge_service.runtime_broker as broker_module
import codex_bridge_service.usage_history as usage_module
from codex_bridge_service.codex_app_server import AppServerTimeoutError
from codex_bridge_service.assist_mcp import isolation_config
from codex_bridge_service.models import RunMode
from codex_bridge_service.routes import usage
from codex_bridge_service.runtime_broker import RuntimeUnavailableError
from codex_bridge_service.storage import BridgeStorage
from test_runtime_broker import (
    ValidatorBackedAppServer, _HomeAssistantProfileStorage, _active_ids, _broker, _new_thread, _requests,
    _storage_and_thread, _turn, _wait_until,
)


@pytest.fixture
def elapsed_clock(monkeypatch):
    """Advance the broker clock at RPC boundaries without sleeping or resetting budgets."""
    instant = [datetime.now(UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant[0].astimezone(tz) if tz else instant[0].replace(tzinfo=None)

    monkeypatch.setattr(broker_module, "datetime", Clock)

    def advance(seconds):
        instant[0] += timedelta(seconds=seconds)

    return advance


def test_preparation_expiry_does_not_dispatch_and_preserves_queues(tmp_path, monkeypatch, elapsed_clock):
    storage, thread = _storage_and_thread(tmp_path)
    other = _new_thread(storage, tmp_path, name="Preserved")
    client = ValidatorBackedAppServer()
    prepared, release = Event(), Event()
    original = client.request
    native = []

    def request(method, params, **kwargs):
        response = original(method, params, **kwargs)
        if method == "thread/start" and not native:
            native.append(response["thread"]["id"])
            prepared.set()
            assert release.wait(2)
            elapsed_clock(2)
        return response

    monkeypatch.setattr(client, "request", request)
    broker = _broker(storage, client, turn_timeout_seconds=10)
    try:
        bounded = broker.submit_prompt(thread.thread_id, "No expired dispatch", client_request_id="expired-preparation", max_duration_seconds=1)
        assert prepared.wait(2)
        deadline = broker._state.runs[bounded.run_id].budget_deadline_at
        queued_other = broker.submit_prompt(other.thread_id, "Other work")
        queued_same = broker.submit_prompt(thread.thread_id, "Next deliberate turn", follow_up_mode="queue")
        release.set()
        _wait_until(lambda: broker._state.runs[bounded.run_id].status == "cancelled")
        assert broker._state.runs[bounded.run_id].stop_reason == "elapsed_time_limit"
        assert not broker._state.runs[bounded.run_id].turn_start_dispatched
        assert not any(item["threadId"] == native[0] for item in _requests(client, "turn/start"))
        assert _requests(client, "turn/interrupt") == []
        assert client.aborted_generations == []
        _wait_until(lambda: storage.load_thread(other.thread_id).active_turn_id is not None)
        assert broker._state.runs[queued_other.run_id].status == "running"
        assert broker._state.runs[queued_same.run_id].status == "queued"
        replay = broker.submit_prompt(thread.thread_id, "No expired dispatch", client_request_id="expired-preparation", max_duration_seconds=1)
        assert replay.run_id == bounded.run_id
        assert broker._state.runs[bounded.run_id].budget_deadline_at == deadline
    finally:
        release.set()
        broker.close()


@pytest.mark.parametrize("phase", ["before_native", "start_response", "resume_response"])
def test_assist_preparation_expiry_retains_control_bound_and_sibling_queue(tmp_path, monkeypatch, elapsed_clock, phase):
    storage = BridgeStorage(root_path=tmp_path / "state")
    project = storage.create_project(name="Assist", root_path=str(tmp_path / "workspace"))
    thread = storage.create_task_thread(
        action_id="a" * 32, fingerprint="f" * 64, title="Assist",
        project_id=project.project_id, mode=RunMode.OBSERVE,
        assist_origin=True, assist_mcp_servers=["home"],
    )
    other = _new_thread(storage, tmp_path, name="Ordinary sibling")
    client = ValidatorBackedAppServer()
    client.thread_unload_delay_seconds = 0
    prepared, release = Event(), Event()
    testing = [False]
    intercepted = []
    original = client.request

    def delay():
        prepared.set()
        assert release.wait(2)
        elapsed_clock(2)

    def config(workspace, _, **kwargs):
        if testing[0] and phase == "before_native":
            delay()
        return isolation_config({}, workspace, **kwargs)

    def request(method, params, **kwargs):
        response = original(method, params, **kwargs)
        expected_method = "thread/resume" if phase == "resume_response" else "thread/start"
        if testing[0] and phase != "before_native" and method == expected_method and not intercepted:
            intercepted.append(kwargs["timeout_seconds"])
            delay()
        return response

    monkeypatch.setattr(client, "request", request)
    manager = SimpleNamespace(enabled=True, assist_thread_config=config)
    broker = _broker(_HomeAssistantProfileStorage(storage), client, mcp_manager=manager, turn_timeout_seconds=10)
    try:
        if phase == "resume_response":
            initial = broker.submit_prompt(thread.thread_id, "Initial Assist", client_request_id="assist-initial", unattended=True, assist=True, web_search="disabled")
            _wait_until(lambda: storage.load_thread(thread.thread_id).active_turn_id is not None)
            _, native, turn = _active_ids(storage, thread.thread_id)
            client.emit_notification("turn/completed", {"threadId": native, "turn": _turn(turn, status="completed")})
            assert broker._state.runs[initial.run_id].status == "completed"
        testing[0] = True
        bounded = broker.submit_prompt(thread.thread_id, "Bounded Assist", client_request_id="assist-bounded", unattended=True, assist=True, web_search="disabled", max_duration_seconds=1)
        assert prepared.wait(2)
        queued = broker.submit_prompt(other.thread_id, "Preserve ordinary work")
        release.set()
        _wait_until(lambda: broker._state.runs[bounded.run_id].status == "cancelled")
        assert broker._state.runs[bounded.run_id].stop_reason == "elapsed_time_limit"
        assert not broker._state.runs[bounded.run_id].turn_start_dispatched
        assert not any(item.get("clientUserMessageId") == "assist-bounded" for item in _requests(client, "turn/start"))
        assert _requests(client, "turn/interrupt") == []
        assert client.aborted_generations == []
        assert not broker._assist_loading_runs
        if phase == "before_native":
            assert broker._state.runs[bounded.run_id].codex_thread_id is None
        else:
            assert intercepted[0] > 1  # preparation retains its control/global bound
            assert _requests(client, "thread/unsubscribe")
        _wait_until(lambda: storage.load_thread(other.thread_id).active_turn_id is not None)
        assert broker._state.runs[queued.run_id].status == "running"
    finally:
        release.set()
        broker.close()


@pytest.mark.parametrize("outcome", ["running", "completed", "buffered_completed", "buffered_failed", "buffered_interrupted", "stale_completed", "interrupt_failure"])
def test_delayed_authoritative_start_uses_exact_budget_stop(tmp_path, monkeypatch, elapsed_clock, outcome):
    storage, thread = _storage_and_thread(tmp_path)
    other = _new_thread(storage, tmp_path, name="Queued")
    client = ValidatorBackedAppServer()
    dispatched, release = Event(), Event()
    original = client.request
    identity = []

    def request(method, params, **kwargs):
        if method == "turn/interrupt" and outcome == "interrupt_failure":
            original(method, params, **kwargs)
            raise AppServerTimeoutError(method)
        response = original(method, params, **kwargs)
        if method == "turn/start" and not identity:
            identity.append((params["threadId"], response["turn"]["id"]))
            native, turn = identity[0]
            if outcome != "completed":
                client.emit_notification("item/agentMessage/delta", {
                    "threadId": native, "turnId": turn, "itemId": "partial", "delta": "Retained before acknowledgement",
                })
            dispatched.set()
            assert release.wait(2)
            elapsed_clock(2)
            if outcome == "completed":
                response["turn"] = _turn(turn, status="completed")
            elif outcome.startswith("buffered_") or outcome == "stale_completed":
                client.emit_notification("turn/completed", {
                    "threadId": native, "turn": _turn("historical-turn" if outcome == "stale_completed" else turn, status=outcome.removeprefix("buffered_") if outcome.startswith("buffered_") else "completed"),
                })
        elif method == "turn/interrupt":
            client.emit_notification("turn/completed", {
                "threadId": params["threadId"], "turn": _turn(params["turnId"], status="interrupted"),
            })
        return response

    monkeypatch.setattr(client, "request", request)
    broker = _broker(storage, client, turn_timeout_seconds=10, cancel_grace_seconds=0.05)
    original_replay = broker._replay_pre_response_callbacks
    replay_observations = []

    def replay(run_id, **kwargs):
        if broker._state.runs[run_id].max_duration_seconds is not None:
            replay_observations.append((broker._state.runs[run_id].stop_reason, len(_requests(client, "turn/interrupt"))))
        return original_replay(run_id, **kwargs)

    monkeypatch.setattr(broker, "_replay_pre_response_callbacks", replay)
    try:
        bounded = broker.submit_prompt(thread.thread_id, "Bounded acknowledgement", max_duration_seconds=1)
        assert dispatched.wait(2)
        deadline = broker._state.runs[bounded.run_id].budget_deadline_at
        queued = broker.submit_prompt(other.thread_id, "Preserved work")
        release.set()
        native, turn = identity[0]
        timeouts = [value for method, value in client.request_timeouts if method == "turn/start"]
        assert 0 < timeouts[0] <= 1
        if outcome == "completed" or outcome.startswith("buffered_"):
            expected = {"buffered_failed": "failed", "buffered_interrupted": "interrupted"}.get(outcome, "completed")
            _wait_until(lambda: broker._state.runs[bounded.run_id].status == expected)
            assert broker._state.runs[bounded.run_id].stop_reason is None
            assert _requests(client, "turn/interrupt") == []
        else:
            _wait_until(lambda: len(_requests(client, "turn/interrupt")) == 1)
            assert _requests(client, "turn/interrupt") == [{"threadId": native, "turnId": turn}]
            if outcome == "interrupt_failure":
                _wait_until(lambda: broker._state.runs[bounded.run_id].status == "cancelling")
                elapsed_clock(0.1)
                _wait_until(lambda: broker._state.runs[bounded.run_id].budget_stop_unconfirmed)
                assert broker._state.runs[queued.run_id].status == "queued"
                client.emit_notification("turn/completed", {"threadId": native, "turn": _turn(turn, status="interrupted")})
            _wait_until(lambda: broker._state.runs[bounded.run_id].status == "cancelled")
        assert broker._state.runs[bounded.run_id].budget_deadline_at == deadline
        expected_replay = (None, 0) if outcome == "completed" or outcome.startswith("buffered_") else ("elapsed_time_limit", 1)
        _wait_until(lambda: len(replay_observations) == 1)
        assert replay_observations == [expected_replay]
        if outcome != "completed":
            assert any(event.event_type == "message.delta" and event.payload.get("text") == "Retained before acknowledgement"
                       for event in storage.list_thread_events(thread.thread_id))
        assert client.aborted_generations == []
        _wait_until(lambda: storage.load_thread(other.thread_id).active_turn_id is not None)
        assert broker._state.runs[queued.run_id].status == "running"
    finally:
        release.set()
        broker.close()


def test_unknown_elapsed_start_fences_without_guessing_and_recovers_generation(tmp_path, monkeypatch, elapsed_clock):
    storage, thread = _storage_and_thread(tmp_path)
    other = _new_thread(storage, tmp_path, name="Other")
    client = ValidatorBackedAppServer()
    dispatched, release = Event(), Event()
    original = client.request
    timed_out = []

    def request(method, params, **kwargs):
        response = original(method, params, **kwargs)
        if method == "turn/start" and not timed_out:
            timed_out.append(True)
            # Even a matching buffered terminal ID cannot replace the missing
            # authoritative response or grant cancellation/recovery authority.
            client.emit_notification("turn/completed", {
                "threadId": params["threadId"], "turn": _turn(response["turn"]["id"], status="completed"),
            })
            dispatched.set()
            assert release.wait(2)
            elapsed_clock(2)
            raise AppServerTimeoutError(method)
        return response

    monkeypatch.setattr(client, "request", request)
    broker = _broker(storage, client, turn_timeout_seconds=10)
    try:
        bounded = broker.submit_prompt(thread.thread_id, "Unknown acknowledgement", max_duration_seconds=1)
        assert dispatched.wait(2)
        queued_other = broker.submit_prompt(other.thread_id, "Preserved other authority", follow_up_mode="queue")
        queued_same = broker.submit_prompt(thread.thread_id, "Preserved same authority", follow_up_mode="queue")
        release.set()
        _wait_until(lambda: broker._state.runs[bounded.run_id].budget_stop_unconfirmed)
        assert broker._state.runs[bounded.run_id].codex_turn_id is None
        assert broker._state.runs[bounded.run_id].status == "cancelling"
        assert broker._leases[bounded.run_id].state == "active"
        assert all(broker._state.runs[row.run_id].status == "queued" for row in (queued_other, queued_same))
        assert _requests(client, "turn/interrupt") == []
        assert len(_requests(client, "turn/start")) == 1
        assert client.aborted_generations == []
        with pytest.raises(RuntimeUnavailableError):
            broker.submit_prompt(other.thread_id, "Must remain fenced")
        # Simulate an independently confirmed native restart, not an elapsed
        # stop abort. Existing recovery releases the uncertain lease and checks
        # the undispatched queue's original authority before it can run.
        client.generation += 1
        _wait_until(lambda: broker._state.runs[bounded.run_id].status == "cancelled")
        assert not broker._state.runs[bounded.run_id].budget_stop_unconfirmed
        _wait_until(lambda: storage.load_thread(other.thread_id).active_turn_id is not None)
        assert broker._state.runs[queued_other.run_id].status == "running"
        assert broker._state.runs[queued_same.run_id].status == "queued"
        assert client.aborted_generations == []
    finally:
        release.set()
        broker.close()


@pytest.mark.parametrize("duration", [None, 1])
def test_genuine_unknown_start_failure_keeps_existing_owner(tmp_path, monkeypatch, elapsed_clock, duration):
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    original = client.request

    def request(method, params, **kwargs):
        response = original(method, params, **kwargs)
        if method == "turn/start":
            # No elapsed deadline has passed: this is a genuine lost start
            # response and must retain the pre-existing global failure owner.
            raise AppServerTimeoutError(method)
        return response

    monkeypatch.setattr(client, "request", request)
    broker = _broker(storage, client, turn_timeout_seconds=10)
    try:
        run = broker.submit_prompt(thread.thread_id, "Genuine start failure", max_duration_seconds=duration)
        _wait_until(lambda: broker._state.runs[run.run_id].status == "failed")
        assert broker._state.runs[run.run_id].stop_reason is None
        assert client.aborted_generations == [1]
        assert _requests(client, "turn/interrupt") == []
    finally:
        broker.close()


@pytest.mark.parametrize("ledger_state", ["malformed", "oversized", "non_file", "unreadable"])
def test_unavailable_ledger_preserves_evidence_chat_and_elapsed_stop(tmp_path, monkeypatch, elapsed_clock, ledger_state):
    storage, thread = _storage_and_thread(tmp_path)
    path = storage.root / "usage-history.json"
    original_bytes = b"private malformed evidence" if ledger_state == "malformed" else b" " * (usage_module.MAX_BYTES + 1) if ledger_state == "oversized" else b"private unreadable evidence"
    if ledger_state == "non_file":
        path.mkdir()
    else:
        path.write_bytes(original_bytes)
    boundaries = []
    original_boundary = usage_module.WorkspaceBoundary

    class TrackedBoundary(original_boundary):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.close_count = 0
            boundaries.append(self)

        def open_regular_file(self, *args, **kwargs):
            if ledger_state == "unreadable":
                raise PermissionError("private filesystem evidence")
            return super().open_regular_file(*args, **kwargs)

        def close(self):
            self.close_count += 1
            super().close()

    monkeypatch.setattr(usage_module, "WorkspaceBoundary", TrackedBoundary)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client, turn_timeout_seconds=10)
    try:
        assert broker.usage_history is None and broker._usage_unavailable
        assert boundaries[0].close_count == 1
        ordinary = broker.submit_prompt(thread.thread_id, "Ordinary chat still works")
        _wait_until(lambda: storage.load_thread(thread.thread_id).active_turn_id is not None)
        _, native, turn = _active_ids(storage, thread.thread_id)
        counters = {"totalTokens": 100, "inputTokens": 100, "outputTokens": 0, "cachedInputTokens": 0, "reasoningOutputTokens": 0}
        client.emit_notification("thread/tokenUsage/updated", {"threadId": native, "turnId": turn, "tokenUsage": {"total": counters, "last": counters, "modelContextWindow": 1000}})
        client.emit_notification("turn/completed", {"threadId": native, "turn": _turn(turn, status="completed")})
        assert broker._state.runs[ordinary.run_id].status == "completed"
        app = FastAPI()
        app.state.auth_token, app.state.storage, app.state.runner = "secret", storage, broker
        app.state.feature_capabilities = ("usage_history_v1",)
        app.include_router(usage.router)
        with TestClient(app) as http:
            response = http.get("/usage", headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "usage_history_unavailable"
        assert "private" not in response.text
        bounded = broker.submit_prompt(thread.thread_id, "Telemetry-independent stop", max_duration_seconds=1)
        _wait_until(lambda: storage.load_thread(thread.thread_id).active_turn_id is not None)
        _, native, turn = _active_ids(storage, thread.thread_id)
        queued = broker.submit_prompt(thread.thread_id, "Retained explicit queue", follow_up_mode="queue")
        elapsed_clock(2)
        broker._stop_expired_start(bounded.run_id)
        assert _requests(client, "turn/interrupt") == [{"threadId": native, "turnId": turn}]
        assert broker._state.runs[queued.run_id].status == "queued"
        assert client.aborted_generations == []
        client.emit_notification("turn/completed", {"threadId": native, "turn": _turn(turn, status="interrupted")})
        assert broker._state.runs[bounded.run_id].status == "cancelled"
        assert path.is_dir() if ledger_state == "non_file" else path.read_bytes() == original_bytes
    finally:
        broker.close()
    assert boundaries[0].close_count == 1
