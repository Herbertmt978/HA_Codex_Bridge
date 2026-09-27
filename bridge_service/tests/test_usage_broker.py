import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from codex_bridge_service.routes import usage
from codex_bridge_service.routes.prompts import PromptRequest
from codex_bridge_service.runtime_broker import RuntimeRequestConflictError, RuntimeUnavailableError

from test_runtime_broker import (
    ValidatorBackedAppServer, _active_ids, _broker, _new_thread, _requests,
    _storage_and_thread, _turn, _wait_until,
)


def _snapshot(count):
    counters = {"totalTokens": count, "inputTokens": count, "outputTokens": 0,
                "cachedInputTokens": 0, "reasoningOutputTokens": 0}
    return {"total": counters, "last": counters, "modelContextWindow": 1000}


def test_immediate_account_capture_history_replay_and_switch(tmp_path):
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    owner = ["a" * 64]
    broker = _broker(storage, client, provider_account_owner_marker=lambda: owner[0])
    try:
        first = broker.submit_prompt(thread.thread_id, "First", client_request_id="first")
        _wait_until(lambda: storage.load_thread(thread.thread_id).active_turn_id is not None)
        _, native, turn = _active_ids(storage, thread.thread_id)
        assert broker._state.runs[first.run_id].account_owner_marker == owner[0]
        for _ in range(2):
            client.emit_notification("thread/tokenUsage/updated", {
                "threadId": native, "turnId": turn, "tokenUsage": _snapshot(100),
            })
        assert broker.get_usage_history(thread_id=thread.thread_id)["known_reported_tokens"] == 100
        client.emit_notification("turn/completed", {"threadId": native, "turn": _turn(turn, status="completed")})
        owner[0] = "b" * 64
        other = _new_thread(storage, tmp_path, name="Other")
        second = broker.submit_prompt(other.thread_id, "Second", client_request_id="second")
        _wait_until(lambda: storage.load_thread(other.thread_id).active_turn_id is not None)
        _, native_two, turn_two = _active_ids(storage, other.thread_id)
        client.emit_notification("thread/tokenUsage/updated", {
            "threadId": native_two, "turnId": turn_two, "tokenUsage": _snapshot(50),
        })
        assert broker._state.runs[second.run_id].account_owner_marker == owner[0]
        items = broker.get_usage_history()["items"]
        assert {item["account_group"] for item in items} == {1, 2}
        assert broker.get_usage_history(project_id=thread.project_id)["known_reported_tokens"] == 100
        assert "a" * 64 not in json.dumps(items)
    finally:
        broker.close()


def test_duration_stop_keeps_partial_output_other_chat_and_queues(tmp_path, monkeypatch):
    storage, thread = _storage_and_thread(tmp_path)
    other = _new_thread(storage, tmp_path, name="Other")
    client = ValidatorBackedAppServer()
    # The existing broker admits exactly one active turn. Other-chat work must
    # survive in its normal FIFO queue and start after this exact turn stops.
    broker = _broker(storage, client, turn_timeout_seconds=10, cancel_grace_seconds=0.1,
                     provider_account_owner_marker=lambda: "a" * 64)
    original = client.request
    def request(method, params, **kwargs):
        result = original(method, params, **kwargs)
        if method == "turn/interrupt":
            client.emit_notification("turn/completed", {
                "threadId": params["threadId"], "turn": _turn(params["turnId"], status="interrupted"),
            })
        return result
    monkeypatch.setattr(client, "request", request)
    try:
        first = broker.submit_prompt(thread.thread_id, "Bounded", client_request_id="bounded", max_duration_seconds=1)
        _wait_until(lambda: storage.load_thread(thread.thread_id).active_turn_id is not None)
        _, native, turn = _active_ids(storage, thread.thread_id)
        second = broker.submit_prompt(other.thread_id, "Other work", client_request_id="other")
        assert broker._state.runs[second.run_id].status == "queued"
        queued = broker.submit_prompt(thread.thread_id, "Deliberately queued", client_request_id="queued", follow_up_mode="queue")
        client.emit_notification("item/agentMessage/delta", {
            "threadId": native, "turnId": turn, "itemId": "partial", "delta": "Retained partial output",
        })
        _wait_until(lambda: broker._state.runs[first.run_id].status == "cancelled", timeout=3)
        _wait_until(lambda: storage.load_thread(other.thread_id).active_turn_id is not None)
        assert broker._state.runs[first.run_id].stop_reason == "elapsed_time_limit"
        assert broker._state.runs[second.run_id].status == "running"
        assert broker._state.runs[queued.run_id].status not in {"cancelled", "interrupted", "failed"}
        assert client.aborted_generations == []
        assert _requests(client, "turn/interrupt") == [{"threadId": native, "turnId": turn}]
        assert any(event.payload.get("text") == "Retained partial output"
                   for event in storage.list_thread_events(thread.thread_id))
        replay = broker.submit_prompt(thread.thread_id, "Bounded", client_request_id="bounded", max_duration_seconds=1)
        assert replay.run_id == first.run_id
        assert broker.cancel_run(thread.thread_id, run_id=first.run_id, budget_stop=True).status == "cancelled"
        assert len(_requests(client, "turn/interrupt")) == 1
        with pytest.raises(RuntimeRequestConflictError):
            broker.submit_prompt(thread.thread_id, "Bounded", client_request_id="bounded", max_duration_seconds=2)
        history = {item["run_id"]: item for item in broker.get_usage_history(thread_id=thread.thread_id)["items"]}
        assert history[first.run_id]["budget_stop_state"] == "finished"
    finally:
        broker.close()


def test_unconfirmed_duration_stop_does_not_abort_shared_runtime(tmp_path, monkeypatch):
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client, turn_timeout_seconds=10, cancel_grace_seconds=0.01)
    try:
        run = broker.submit_prompt(thread.thread_id, "Bounded", max_duration_seconds=1)
        _wait_until(lambda: storage.load_thread(thread.thread_id).active_turn_id is not None)
        completion = broker._completion_events[run.run_id]
        original_wait = completion.wait
        post_stop_waits = []
        def tracking_wait(timeout=None):
            if broker._state.runs[run.run_id].stop_reason == "elapsed_time_limit":
                post_stop_waits.append(timeout)
            return original_wait(timeout)
        monkeypatch.setattr(completion, "wait", tracking_wait)
        queued = broker.submit_prompt(thread.thread_id, "Queued", follow_up_mode="queue")
        _wait_until(lambda: broker._state.runs[run.run_id].budget_stop_unconfirmed, timeout=3)
        _wait_until(lambda: len(post_stop_waits) >= 5)
        assert all(timeout >= broker.watchdog_interval_seconds * 0.9
                   for timeout in post_stop_waits[:5])
        assert broker._state.runs[run.run_id].status == "cancelling"
        assert broker._state.runs[queued.run_id].status == "queued"
        assert client.aborted_generations == []
        assert broker.get_usage_history()["items"][0]["budget_stop_state"] == "unconfirmed"
        assert broker.cancel_run(thread.thread_id, run_id=run.run_id, budget_stop=True).status == "cancelling"
        assert len(_requests(client, "turn/interrupt")) == 1
        with pytest.raises(RuntimeUnavailableError) as error:
            broker.submit_prompt(thread.thread_id, "Must not bypass stop", client_request_id="blocked")
        assert error.value.code == "app_server_unavailable"
        _, native, turn = _active_ids(storage, thread.thread_id)
        client.emit_notification("turn/completed", {"threadId": native, "turn": _turn(turn, status="interrupted")})
        assert not broker._state.runs[run.run_id].budget_stop_unconfirmed
    finally:
        broker.close()


def test_duration_rejects_over_ceiling_and_fake_token_budget(tmp_path):
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        with pytest.raises(ValueError):
            broker.submit_prompt(thread.thread_id, "Too long", max_duration_seconds=6)
        assert _requests(client, "turn/start") == []
        with pytest.raises(ValidationError):
            PromptRequest(prompt="No fake meter", max_reported_tokens=100)
    finally:
        broker.close()


def test_history_api_auth_capability_missing_target_and_nullable_usage(tmp_path):
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    app = FastAPI()
    app.state.auth_token = "secret"
    app.state.storage = storage
    app.state.runner = broker
    app.state.feature_capabilities = ("usage_history_v1",)
    app.include_router(usage.router)
    try:
        broker.submit_prompt(thread.thread_id, "Unmeasured")
        _wait_until(lambda: storage.load_thread(thread.thread_id).active_turn_id is not None)
        headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}
        with TestClient(app) as http:
            assert http.get("/usage").status_code == 401
            response = http.get("/usage", params={"thread_id": thread.thread_id}, headers=headers)
            assert response.status_code == 200
            assert response.json()["known_reported_tokens"] is None
            assert response.json()["items"][0]["reported_tokens"] is None
            assert http.get("/usage?thread_id=missing", headers=headers).status_code == 404
            app.state.feature_capabilities = ()
            assert http.get("/usage", headers=headers).status_code == 422
    finally:
        broker.close()
