"""Search configuration belongs to a new native turn, never a steer input."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace

from codex_bridge_service.routes import prompts
from codex_bridge_service.runtime_broker import (
    RuntimeRequestConflictError,
    RuntimeWebSearchModeConflictError,
)
from codex_bridge_service.runtime_state import RuntimeStateStore
from test_provider_thread_resume import _start_broker
from test_runtime_broker import _active_ids, _complete, _requests, _wait_until


def test_http_search_conflict_is_definite_and_queue_remains_available(tmp_path, monkeypatch):
    storage, thread, peer, broker = _start_broker(tmp_path)
    app = FastAPI()
    app.include_router(prompts.router)
    app.state.auth_token = "test-secret"
    # The broker fixture uses portable storage; select the native HTTP path.
    app.state.storage = SimpleNamespace(runtime_profile=SimpleNamespace(value="home_assistant"))
    app.state.runner = broker
    app.state.feature_capabilities = ("web_search_v1", "prompt_queue_v1")
    monkeypatch.setattr(prompts, "evaluate_readiness", lambda *args, **kwargs:
                        SimpleNamespace(state="ready"))
    monkeypatch.setattr(prompts, "supports_web_search", lambda state: True)
    try:
        broker.submit_prompt(thread.thread_id, "Active", web_search="live",
                             client_request_id="search-active")
        _active_ids(storage, thread.thread_id)
        with TestClient(app) as http:
            body = {"prompt": "No search", "web_search": "disabled",
                    "client_request_id": "search-http", "follow_up_mode": "steer"}
            url = f"/threads/{thread.thread_id}/prompts"
            headers = {"Authorization": "Bearer test-secret", "X-Codex-Bridge-Api": "1"}
            refusal = http.post(url, headers=headers, json=body)
            assert refusal.status_code == 409
            assert refusal.json()["detail"] == {
                "code": "web_search_requires_queue", "retryable": False,
            }
            accepted = http.post(url, headers=headers, json={**body, "follow_up_mode": "queue"})
            assert accepted.status_code == 202
            assert accepted.json()["status"] == "queued"
            assert not _requests(peer, "turn/steer")
    finally:
        broker.close()


@pytest.mark.parametrize("follow_up", ["auto", "steer"])
@pytest.mark.parametrize(
    ("active_mode", "requested_mode"),
    [("live", "disabled"), ("disabled", "live"), (None, "disabled"),
     (None, "live"), ("live", None), ("disabled", None)],
)
def test_changed_search_mode_cannot_steer(tmp_path, follow_up, active_mode, requested_mode):
    storage, thread, peer, broker = _start_broker(tmp_path)
    try:
        broker.submit_prompt(thread.thread_id, "Active", web_search=active_mode,
                             client_request_id="search-active")
        _active_ids(storage, thread.thread_id)
        before = list(peer.requests)
        for _attempt in range(2):
            with pytest.raises(RuntimeWebSearchModeConflictError) as error:
                broker.submit_prompt(thread.thread_id, "Change search",
                                     web_search=requested_mode, follow_up_mode=follow_up,
                                     client_request_id="search-refused")
            assert error.value.public_detail() == {
                "code": "web_search_requires_queue", "retryable": False,
            }
        assert peer.requests == before
        state = RuntimeStateStore(storage.root).load()
        assert "search-refused" not in state.request_idempotency
        assert not any(event.payload.get("client_request_id") == "search-refused"
                       for event in storage.list_thread_events(thread.thread_id))
    finally:
        broker.close()


@pytest.mark.parametrize("mode", ["live", "disabled", None])
@pytest.mark.parametrize("follow_up", ["auto", "steer"])
def test_matching_search_mode_still_steers(tmp_path, mode, follow_up):
    storage, thread, peer, broker = _start_broker(tmp_path)
    try:
        active = broker.submit_prompt(thread.thread_id, "Active", web_search=mode,
                                      client_request_id="search-active")
        _active_ids(storage, thread.thread_id)
        steered = broker.submit_prompt(thread.thread_id, "Continue", web_search=mode,
                                       follow_up_mode=follow_up, client_request_id="search-steer")
        assert steered.run_id == active.run_id
        assert len(_requests(peer, "turn/steer")) == 1
        assert len(_requests(peer, "turn/start")) == 1
        assert peer.loaded_settings["config"]["web_search"] == (mode or "cached")
    finally:
        broker.close()


@pytest.mark.parametrize(
    ("first_mode", "next_mode"), [("live", "disabled"), ("disabled", "live")],
)
def test_queued_search_mode_survives_edit_and_retry_and_reaches_cold_resume(
    tmp_path, first_mode, next_mode,
):
    storage, thread, peer, broker = _start_broker(tmp_path)
    try:
        active = broker.submit_prompt(thread.thread_id, "Active", web_search=first_mode,
                                      client_request_id="search-active")
        _run_id, provider_id, turn_id = _active_ids(storage, thread.thread_id)
        initial_config = peer.loaded_settings["config"].copy()
        assert initial_config["web_search"] == first_mode
        queued = broker.submit_prompt(thread.thread_id, "Queued", web_search=next_mode,
                                      follow_up_mode="queue", client_request_id="search-queued")
        assert queued.status == "queued"
        repeated = broker.submit_prompt(thread.thread_id, "Queued", web_search=next_mode,
                                        follow_up_mode="queue", client_request_id="search-queued")
        assert repeated.run_id == queued.run_id
        with pytest.raises(RuntimeRequestConflictError):
            broker.submit_prompt(thread.thread_id, "Queued", web_search=first_mode,
                                 follow_up_mode="queue", client_request_id="search-queued")
        broker.update_queued_prompt(thread.thread_id, queued.run_id, "Edited queued",
                                    expected_revision=1)
        state = RuntimeStateStore(storage.root).load()
        assert state.runs[queued.run_id].web_search == next_mode
        assert state.runs[active.run_id].web_search == first_mode
        assert not _requests(peer, "turn/steer")
        _complete(peer, remote_thread_id=provider_id, turn_id=turn_id)
        _wait_until(lambda: len(_requests(peer, "turn/start")) == 2)
        assert len(_requests(peer, "thread/resume")) == 1
        # An already accepted retry belongs to its original run, even while
        # the next run has a different effective search mode.
        replay_active = broker.submit_prompt(
            thread.thread_id, "Active", web_search=first_mode,
            client_request_id="search-active",
        )
        assert replay_active.run_id == active.run_id
        config = peer.loaded_settings["config"]
        assert config == {**initial_config, "web_search": next_mode}
        assert _requests(peer, "thread/resume")[0]["config"]["web_search"] == next_mode
        assert _requests(peer, "turn/start")[1]["input"][-1]["text"] == "Edited queued"
        _, resumed_provider, resumed_turn = _active_ids(storage, thread.thread_id)
        _complete(peer, remote_thread_id=resumed_provider, turn_id=resumed_turn)
    finally:
        broker.close()
