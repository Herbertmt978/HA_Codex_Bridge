from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any

from codex_bridge_service.codex_app_server import DEFERRED_RESPONSE
from codex_bridge_service.runtime_broker import RuntimeBrokerError
from test_runtime_broker import (
    ValidatorBackedAppServer,
    _active_ids,
    _broker,
    _storage_and_thread,
    _wait_until,
)


def _question_params(remote_thread_id: str, turn_id: str) -> dict[str, object]:
    return {
        "threadId": remote_thread_id,
        "turnId": turn_id,
        "itemId": "optional-question-race",
        "isBlocking": False,
        "questions": [
            {
                "id": "preference",
                "header": "Preference",
                "question": "What would you prefer?",
                "options": [],
                "isOther": True,
                "isSecret": False,
            }
        ],
    }


def test_optional_expiry_during_user_response_does_not_send_empty_answer(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(
        storage,
        client,
        interaction_timeout_seconds=30.0,
        turn_timeout_seconds=60.0,
    )
    response_entered = Event()
    allow_response = Event()
    original_respond = client.respond

    def paused_respond(request: Any, *, result: object, error: object = None) -> None:
        if response_entered.is_set():
            original_respond(request, result=result, error=error)  # type: ignore[arg-type]
            return
        response_entered.set()
        if not allow_response.wait(timeout=5):
            raise TimeoutError("test did not release the app-server response")
        original_respond(request, result=result, error=error)  # type: ignore[arg-type]

    client.respond = paused_respond  # type: ignore[method-assign]
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Answer an optional question",
            client_request_id="optional-expiry-race-first",
        )
        _wait_until(lambda: any(name == "turn/start" for name, _ in client.requests))
        _, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        request_id = "provider-optional-expiry-race"
        assert client.emit_request(
            "item/tool/requestUserInput",
            _question_params(remote_thread_id, turn_id),
            request_id=request_id,
        ) is DEFERRED_RESPONSE
        pending = broker.pending_interactions(thread.thread_id)
        assert len(pending) == 1
        interaction_id = pending[0].interaction_id

        broker.submit_prompt(
            thread.thread_id,
            "Keep this queued run after optional expiry",
            client_request_id="optional-expiry-race-queued",
            follow_up_mode="queue",
        )

        with ThreadPoolExecutor(max_workers=1) as pool:
            answer = pool.submit(
                broker.answer_user_input,
                interaction_id,
                thread_id=thread.thread_id,
                answers={"preference": ["Use the existing wording"]},
                client_request_id="optional-expiry-race-answer",
            )
            assert response_entered.wait(timeout=5)

            try:
                with broker._lock:
                    interaction = broker._state.interactions[interaction_id]
                    assert interaction.status == "responding"
                    interaction.expires_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
                    broker._expire_due_interactions_locked()
                    assert interaction.status == "outcome_unknown"
                    assert not client.responses

                    queued = next(
                        run
                        for run in broker._state.runs.values()
                        if run.client_request_id == "optional-expiry-race-queued"
                    )
                    assert queued.status == "queued"

                assert client.aborted_generations == []
                assert client.discarded == [(request_id, client.generation)]
            finally:
                allow_response.set()
            try:
                answer.result(timeout=5)
            except RuntimeBrokerError:
                pass
            else:
                raise AssertionError("the expired in-flight answer should be outcome-unknown")

        assert len(client.responses) == 1
        assert client.responses[0][1] == {
            "answers": {"preference": {"answers": ["Use the existing wording"]}}
        }
        assert client.aborted_generations == []
        with broker._lock:
            queued = next(
                run
                for run in broker._state.runs.values()
                if run.client_request_id == "optional-expiry-race-queued"
            )
            assert queued.status == "queued"
    finally:
        allow_response.set()
        broker.close()
