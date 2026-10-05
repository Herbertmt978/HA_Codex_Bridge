from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from codex_bridge_service.codex_app_server import DEFERRED_RESPONSE
from codex_bridge_service.models import RunMode
from codex_bridge_service.runtime_broker import RuntimeBrokerError
from test_runtime_broker import (
    ValidatorBackedAppServer,
    _active_ids,
    _broker,
    _complete,
    _new_thread,
    _pending_one,
    _requests,
    _storage_and_thread,
    _wait_until,
)


def _question_params(
    remote_thread_id: str,
    turn_id: str,
    *,
    item_id: str = "optional-question",
    is_blocking: bool = False,
) -> dict[str, Any]:
    return {
        "threadId": remote_thread_id,
        "turnId": turn_id,
        "itemId": item_id,
        "isBlocking": is_blocking,
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


def _emit_optional_question(
    client: ValidatorBackedAppServer,
    remote_thread_id: str,
    turn_id: str,
    *,
    item_id: str = "optional-question",
    request_id: str = "provider-optional-question",
) -> object:
    return client.emit_request(
        "item/tool/requestUserInput",
        _question_params(remote_thread_id, turn_id, item_id=item_id),
        request_id=request_id,
    )


def test_optional_question_accepts_free_text_exactly_once_and_is_thread_bound(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    other_thread = _new_thread(storage, tmp_path, name="Other question thread")
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client, interaction_timeout_seconds=5.0)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Continue and optionally ask a question",
            client_request_id="optional-free-text-run",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        assert _emit_optional_question(client, remote_thread_id, turn_id) is DEFERRED_RESPONSE
        pending = _pending_one(broker, thread.thread_id)
        assert pending["is_blocking"] is False
        assert pending["display"]["questions"][0]["allow_free_text"] is True

        with pytest.raises(RuntimeBrokerError):
            broker.answer_user_input(
                thread_id=other_thread.thread_id,
                interaction_id=pending["interaction_id"],
                answers={"preference": ["Use the existing project wording"]},
                client_request_id="optional-answer-wrong-thread",
            )

        args = {
            "thread_id": thread.thread_id,
            "interaction_id": pending["interaction_id"],
            "answers": {"preference": ["Use the existing project wording"]},
            "client_request_id": "optional-answer-once",
        }
        first = broker.answer_user_input(**args)
        duplicate = broker.answer_user_input(**args)
        assert first == duplicate
        assert first.status == "answered"
        _wait_until(lambda: len(client.responses) == 1)
        assert client.responses[0][1] == {
            "answers": {
                "preference": {"answers": ["Use the existing project wording"]}
            }
        }
    finally:
        broker.close()


def test_optional_expiry_answers_empty_without_aborting_or_clearing_queued_run(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(
        storage,
        client,
        queue_wait_timeout_seconds=5.0,
        turn_timeout_seconds=10.0,
        interaction_timeout_seconds=0.2,
    )
    try:
        first = broker.submit_prompt(
            thread.thread_id,
            "Continue after an optional question expires",
            client_request_id="optional-expiry-first",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        assert _emit_optional_question(client, remote_thread_id, turn_id) is DEFERRED_RESPONSE
        pending = _pending_one(broker, thread.thread_id)
        assert broker.submit_prompt(
            thread.thread_id,
            "Run after the first turn",
            client_request_id="optional-expiry-queued",
            follow_up_mode="queue",
        ).status == "queued"

        _wait_until(lambda: len(client.responses) == 1, timeout=2.0)
        assert client.responses[0][1] == {"answers": {"preference": {"answers": []}}}
        assert client.aborted_generations == []
        assert client.discarded == []
        assert storage.load_thread(thread.thread_id).active_run_id == first.run_id
        assert broker.runtime_snapshot().active_turns == 1
        assert broker.pending_interactions(thread.thread_id) == ()
        with broker._lock:
            assert any(
                run.client_request_id == "optional-expiry-queued"
                and run.status == "queued"
                for run in broker._state.runs.values()
            )
        assert any(
            event.event_type == "interaction.expired"
            and event.payload.get("interaction_id") == pending["interaction_id"]
            for event in storage.list_thread_events(thread.thread_id)
        )

        assert client.aborted_generations == []
    finally:
        broker.close()


def test_blocking_question_expiry_still_aborts_generation_and_queued_work(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    queued_thread = _new_thread(storage, tmp_path, name="Blocking queued")
    client = ValidatorBackedAppServer()
    broker = _broker(
        storage,
        client,
        queue_wait_timeout_seconds=5.0,
        turn_timeout_seconds=10.0,
        interaction_timeout_seconds=0.2,
    )
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Wait for a blocking answer",
            client_request_id="blocking-expiry-first",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        result = client.emit_request(
            "item/tool/requestUserInput",
            _question_params(
                remote_thread_id,
                turn_id,
                item_id="blocking-question",
                is_blocking=True,
            ),
            request_id="provider-blocking-question",
        )
        assert result is DEFERRED_RESPONSE
        assert _pending_one(broker, thread.thread_id)["is_blocking"] is True
        assert broker.submit_prompt(
            queued_thread.thread_id,
            "Must be cleared after blocking timeout",
            client_request_id="blocking-expiry-queued",
        ).status == "queued"

        _wait_until(lambda: client.aborted_generations == [1], timeout=2.0)
        _wait_until(lambda: storage.load_thread(thread.thread_id).status == "error")
        assert client.responses == []
        assert len(client.discarded) == 1
        assert len(_requests(client, "turn/start")) == 1
    finally:
        broker.close()


@pytest.mark.parametrize("resolution", ["terminal", "provider", "generation"])
def test_optional_question_becomes_stale_after_native_request_lifecycle_ends(
    tmp_path: Path,
    resolution: str,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Ask while the native turn is live",
            client_request_id=f"optional-stale-{resolution}",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _wait_until(
            lambda: any(
                event.event_type == "run.started"
                for event in storage.list_thread_events(thread.thread_id)
            ),
            timeout=5.0,
        )
        _, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        request_id = f"provider-optional-stale-{resolution}"
        assert _emit_optional_question(
            client, remote_thread_id, turn_id, request_id=request_id
        ) is DEFERRED_RESPONSE
        pending = _pending_one(broker, thread.thread_id)

        if resolution == "terminal":
            _complete(client, remote_thread_id=remote_thread_id, turn_id=turn_id)
        elif resolution == "provider":
            client.emit_notification(
                "serverRequest/resolved",
                {"requestId": request_id, "threadId": remote_thread_id},
            )
        else:
            client.generation += 1

        if resolution != "generation":
            _wait_until(lambda: broker.pending_interactions(thread.thread_id) == ())
        with pytest.raises(RuntimeBrokerError):
            broker.answer_user_input(
                thread_id=thread.thread_id,
                interaction_id=pending["interaction_id"],
                answers={"preference": ["Too late"]},
                client_request_id=f"optional-too-late-{resolution}",
            )
        assert client.responses == []
    finally:
        broker.close()


def test_optional_question_cap_and_unattended_path_fail_closed(
    tmp_path: Path,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Keep optional questions bounded",
            client_request_id="optional-cap-run",
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        _, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        for index in range(4):
            assert _emit_optional_question(
                client,
                remote_thread_id,
                turn_id,
                item_id=f"optional-cap-{index}",
                request_id=f"provider-optional-cap-{index}",
            ) is DEFERRED_RESPONSE
        assert len(broker.pending_interactions(thread.thread_id)) == 4
        fifth = client.emit_request(
            "item/tool/requestUserInput",
            _question_params(
                remote_thread_id,
                turn_id,
                item_id="optional-cap-4",
            ),
            request_id="provider-optional-cap-4",
        )
        assert fifth == {"answers": {"preference": {"answers": []}}}
        assert len(broker.pending_interactions(thread.thread_id)) == 4

        _complete(client, remote_thread_id=remote_thread_id, turn_id=turn_id)
        _wait_until(lambda: broker.runtime_snapshot().active_turns == 0)

        broker.submit_prompt(
            thread.thread_id,
            "Do not retain unattended questions",
            client_request_id="optional-unattended-run",
            unattended=True,
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 2)
        _, remote_thread_id, turn_id = _active_ids(storage, thread.thread_id)
        unattended_result = client.emit_request(
            "item/tool/requestUserInput",
            _question_params(remote_thread_id, turn_id, item_id="unattended-question"),
            request_id="provider-unattended-question",
        )
        assert unattended_result == {"answers": {"preference": {"answers": []}}}
        assert broker.pending_interactions(thread.thread_id) == ()
    finally:
        broker.close()


@pytest.mark.parametrize(
    ("unattended", "assist_origin", "expected"),
    [(False, False, True), (True, False, False), (False, True, False)],
)
def test_default_mode_question_feature_config_fails_closed_for_unattended_and_assist(
    tmp_path: Path,
    unattended: bool,
    assist_origin: bool,
    expected: bool,
) -> None:
    storage, thread = _storage_and_thread(
        tmp_path,
        mode=RunMode.OBSERVE if assist_origin else RunMode.EDIT,
    )
    if assist_origin:
        record = storage.load_thread(thread.thread_id)
        record.assist_origin = True
        storage.save_thread(record)
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client)
    try:
        broker.submit_prompt(
            thread.thread_id,
            "Check default-mode question availability",
            client_request_id=f"question-config-{unattended}-{assist_origin}",
            unattended=unattended or assist_origin,
            assist=assist_origin,
            web_search="disabled" if assist_origin else None,
        )
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        start = _requests(client, "thread/start")
        assert start
        assert start[0]["config"]["features.default_mode_request_user_input"] is expected
    finally:
        broker.close()
