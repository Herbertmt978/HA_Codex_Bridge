"""Exercise goal fences through the existing real broker and schema-validated peer."""

import pytest

from codex_bridge_service.goals import GoalAction, GoalManager, goal_context_text
from test_runtime_broker import (
    ValidatorBackedAppServer,
    _HomeAssistantProfileStorage,
    _active_ids,
    _broker,
    _complete,
    _private_run,
    _requests,
    _restore_durable_runtime_checkpoint_after_stopping,
    _storage_and_thread,
    _wait_until,
)


def _setup(tmp_path):
    original, thread = _storage_and_thread(tmp_path)
    storage = _HomeAssistantProfileStorage(original)
    storage.goals = GoalManager(storage)
    client = ValidatorBackedAppServer()
    return storage, thread, client


def _act(storage, thread_id, action, **fields):
    revision = storage.goals.get(thread_id).revision
    return storage.goals.apply(thread_id, GoalAction(
        action=action, expected_revision=revision, client_request_id=f"goal-{action}-{revision}", **fields,
    ))


def _goal(storage, thread_id):
    _act(storage, thread_id, "create", objective="Fix the parser",
         completion_criteria=["Reject invalid input", "Keep existing data"], progress="Reproduction ready")
    _act(storage, thread_id, "resume")


def test_manual_goal_context_is_visible_exact_and_survives_cold_resume(tmp_path):
    storage, thread, client = _setup(tmp_path)
    broker = _broker(storage, client)
    try:
        _goal(storage, thread.thread_id)
        assert not client.requests  # Resume cannot invoke any native API or turn.
        snapshot = storage.goals.capture(thread.thread_id)
        accepted = broker.submit_prompt(thread.thread_id, "Check the malformed input", client_request_id="first-goal-turn")
        _, remote, turn_id = _active_ids(storage, thread.thread_id)
        assert _requests(client, "thread/start")[0]["config"]["features.goals"] is False
        exact = "Check the malformed input" + goal_context_text(snapshot)
        assert _requests(client, "turn/start")[0]["input"] == [{"type": "text", "text": exact}]
        message = next(event for event in storage.list_thread_events(thread.thread_id)
                       if event.event_type == "message.created" and event.payload.get("run_id") == accepted.run_id)
        assert message.payload["text"] == exact
        _complete(client, remote_thread_id=remote, turn_id=turn_id)
        _wait_until(lambda: _private_run(broker, accepted.run_id).status == "completed")
        assert storage.goals.get(thread.thread_id).goal.status == "active"  # Turn completion is not goal completion.
        broker.submit_prompt(thread.thread_id, "Continue deliberately", client_request_id="cold-goal-turn")
        _active_ids(storage, thread.thread_id)
        assert _requests(client, "thread/resume")[0]["config"]["features.goals"] is False
        assert "Fix the parser" in _requests(client, "turn/start")[1]["input"][0]["text"]
        assert not any(method.startswith("thread/goal/") for method, _params in client.requests)
    finally:
        broker.close()


@pytest.mark.parametrize("action", ["pause", "cancel", "complete"])
def test_goal_stop_fails_only_associated_queue_and_retains_exact_request(tmp_path, action):
    storage, thread, client = _setup(tmp_path)
    broker = _broker(storage, client)
    try:
        _goal(storage, thread.thread_id)
        current = broker.submit_prompt(thread.thread_id, "Current bounded turn", client_request_id="current")
        _, remote, turn_id = _active_ids(storage, thread.thread_id)
        queued = broker.submit_prompt(thread.thread_id, "Retain this request", client_request_id="goal-queued", follow_up_mode="queue")
        snapshot = _private_run(broker, queued.run_id).goal_context.model_copy(deep=True)
        _act(storage, thread.thread_id, action, **({"completion_confirmed": True} if action == "complete" else {}))
        assert _private_run(broker, current.run_id).status == "running"
        plain = broker.submit_prompt(thread.thread_id, "Unrelated manual work", client_request_id="plain-queued", follow_up_mode="queue")
        assert _private_run(broker, plain.run_id).goal_context is None
        _complete(client, remote_thread_id=remote, turn_id=turn_id)
        _wait_until(lambda: _private_run(broker, queued.run_id).status == "failed")
        _wait_until(lambda: len(_requests(client, "turn/start")) == 2)
        assert _requests(client, "turn/start")[1]["input"] == [{"type": "text", "text": "Unrelated manual work"}]
        assert not _requests(client, "turn/interrupt")
        history = storage.list_thread_events(thread.thread_id)
        message = next(event for event in history if event.event_type == "message.created"
                       and event.payload.get("run_id") == queued.run_id)
        assert message.payload["text"] == "Retain this request" + goal_context_text(snapshot)
        assert any(event.event_type == "run.failed" and event.payload.get("failure_type") == "stale_goal" for event in history)
        retry = broker.submit_prompt(thread.thread_id, "Retain this request", client_request_id="goal-queued", follow_up_mode="queue")
        assert retry.run_id == queued.run_id
        assert len(_requests(client, "turn/start")) == 2
    finally:
        broker.close()


def test_progress_update_preserves_accepted_queue_context_and_edited_prompt(tmp_path):
    storage, thread, client = _setup(tmp_path)
    broker = _broker(storage, client)
    try:
        _goal(storage, thread.thread_id)
        broker.submit_prompt(thread.thread_id, "Current", client_request_id="progress-current")
        _, remote, turn_id = _active_ids(storage, thread.thread_id)
        queued = broker.submit_prompt(thread.thread_id, "Next", client_request_id="progress-queue", follow_up_mode="queue")
        snapshot = _private_run(broker, queued.run_id).goal_context.model_copy(deep=True)
        _act(storage, thread.thread_id, "progress", progress="New notes for later turns")
        broker.update_queued_prompt(thread.thread_id, queued.run_id, "Edited deliberate prompt", expected_revision=1)
        _complete(client, remote_thread_id=remote, turn_id=turn_id)
        _wait_until(lambda: len(_requests(client, "turn/start")) == 2)
        sent = _requests(client, "turn/start")[1]["input"][0]["text"]
        assert sent == "Edited deliberate prompt" + goal_context_text(snapshot)
        assert "New notes for later turns" not in sent
    finally:
        broker.close()


def test_pause_resume_epoch_blocks_cold_recovered_goal_queue_and_idempotent_retry(tmp_path):
    storage, thread, client = _setup(tmp_path)
    owner_marker = "a" * 64
    storage.bind_codex_account(owner_marker)
    first = _broker(storage, client, provider_account_owner_marker=storage.codex_account_owner_marker)
    _goal(storage, thread.thread_id)
    first.submit_prompt(thread.thread_id, "In flight before restart", client_request_id="restart-current")
    _active_ids(storage, thread.thread_id)
    queued = first.submit_prompt(thread.thread_id, "Original accepted request", client_request_id="restart-queued", follow_up_mode="queue")
    accepted_snapshot = _private_run(first, queued.run_id).goal_context
    assert _private_run(first, queued.run_id).account_owner_marker == owner_marker
    _restore_durable_runtime_checkpoint_after_stopping(first, storage)
    _act(storage, thread.thread_id, "pause")
    _act(storage, thread.thread_id, "resume")
    replacement = ValidatorBackedAppServer()
    recovered = _broker(storage, replacement, defer_recovered_queued_runs=True,
                        provider_account_owner_marker=storage.codex_account_owner_marker)
    try:
        restored = _private_run(recovered, queued.run_id)
        assert restored.status == "queued"
        assert restored.account_owner_marker == owner_marker
        assert restored.goal_context == accepted_snapshot
        assert isinstance(restored.goal_context.completion_criteria, tuple)
        assert not replacement.requests
        recovered.resume_recovered_queued_runs()
        _wait_until(lambda: _private_run(recovered, queued.run_id).status == "failed")
        assert not _requests(replacement, "turn/start")
        assert not _requests(replacement, "thread/start")
        assert not _requests(replacement, "thread/resume")
        events = storage.list_thread_events(thread.thread_id)
        assert any(event.event_type == "run.failed" and event.payload.get("run_id") == queued.run_id
                   and event.payload.get("failure_type") == "stale_goal" for event in events)
        accepted_message = next(event for event in events if event.event_type == "message.created"
                                and event.payload.get("run_id") == queued.run_id)
        assert accepted_message.payload["text"] == "Original accepted request" + goal_context_text(accepted_snapshot)
        retry = recovered.submit_prompt(thread.thread_id, "Original accepted request", client_request_id="restart-queued", follow_up_mode="queue")
        assert retry.run_id == queued.run_id
        assert retry.status == "failed"
        assert not replacement.requests
    finally:
        recovered.close()


def test_unattended_manual_submission_never_inherits_goal(tmp_path):
    storage, thread, client = _setup(tmp_path)
    broker = _broker(storage, client)
    try:
        _goal(storage, thread.thread_id)
        accepted = broker.submit_prompt(thread.thread_id, "Scheduled request", client_request_id="unattended", unattended=True)
        _active_ids(storage, thread.thread_id)
        assert _private_run(broker, accepted.run_id).goal_context is None
        assert _requests(client, "thread/start")[0]["config"]["features.goals"] is False
        assert _requests(client, "turn/start")[0]["input"] == [{"type": "text", "text": "Scheduled request"}]
    finally:
        broker.close()
