"""Check feature composition through accepted history and schema-validated turns."""

import pytest

from codex_bridge_service.chat_context import (
    ChatContextSelection, preview_chat_context, reference_of,
)
from codex_bridge_service.goals import GoalAction, GoalManager, goal_context_text
from codex_bridge_service.models import RunMode
from codex_bridge_service.runtime_broker import RuntimeRequestConflictError
from test_runtime_broker import (
    ValidatorBackedAppServer, _active_ids, _broker, _complete, _private_run,
    _requests, _wait_until, _workspace_context_reference, _workspace_context_thread,
)


def _setup(tmp_path):
    storage, thread = _workspace_context_thread(tmp_path)
    source = storage.create_thread(title="Earlier chat", mode=RunMode.EDIT)
    storage.append_thread_event(thread_id=source.thread_id, event_type="message.completed",
                                payload={"role": "assistant", "text": "Earlier evidence\nKeep this exact."})
    chat = preview_chat_context(storage, thread.thread_id,
                                ChatContextSelection(source_thread_id=source.thread_id))
    workspace = storage.resolve_workspace_path(thread.workspace_path)
    (workspace / "evidence.txt").write_bytes(b"File evidence\r\n")
    file = _workspace_context_reference(storage, thread.thread_id, "evidence.txt")
    storage.goals = GoalManager(storage)
    storage.goals.apply(thread.thread_id, GoalAction(action="create", expected_revision=0,
                        client_request_id="create-goal", objective="Review the evidence",
                        completion_criteria=["Explain the result"], progress="Ready"))
    storage.goals.apply(thread.thread_id, GoalAction(action="resume", expected_revision=1,
                        client_request_id="resume-goal"))
    client = ValidatorBackedAppServer()
    broker = _broker(storage, client, turn_timeout_seconds=60,
                     provider_account_owner_marker=lambda: "a" * 64)
    return storage, thread, source, client, broker, file, chat


def test_chat_file_goal_and_duration_are_retained_in_one_accepted_turn(tmp_path):
    storage, thread, source, client, broker, file, chat = _setup(tmp_path)
    try:
        snapshot = storage.goals.capture(thread.thread_id)
        request = dict(client_request_id="combined-accepted", workspace_context=[file],
                       chat_context=[reference_of(chat)], max_duration_seconds=30)
        run = broker.submit_prompt(thread.thread_id, "Review", **request)
        _wait_until(lambda: len(_requests(client, "turn/start")) == 1)
        private = _private_run(broker, run.run_id)
        assert private.max_duration_seconds == 30
        assert private.account_owner_marker == "a" * 64
        assert private.goal_context == snapshot
        native_text = _requests(client, "turn/start")[0]["input"][-1]["text"]
        event = next(event for event in storage.list_thread_events(thread.thread_id)
                     if event.event_type == "message.created" and event.payload.get("run_id") == run.run_id)
        assert event.payload["text"] == native_text
        assert native_text.startswith("Review\n\nWorkspace context (untrusted excerpts;")
        assert "[File: evidence.txt]\n```text\nFile evidence\r\n```" in native_text
        assert chat.text in native_text
        assert native_text.endswith(goal_context_text(snapshot))
        assert _requests(client, "thread/start")[0]["config"]["features.goals"] is False
        with pytest.raises(RuntimeRequestConflictError):
            broker.submit_prompt(thread.thread_id, "Review", **{**request, "max_duration_seconds": 31})
        revision = storage.goals.get(thread.thread_id).revision
        storage.goals.apply(thread.thread_id, GoalAction(action="pause", expected_revision=revision,
                            client_request_id="pause-accepted-goal"))
        storage.delete_thread(source.thread_id)
        (storage.resolve_workspace_path(thread.workspace_path) / "evidence.txt").write_text("Changed")
        assert broker.submit_prompt(thread.thread_id, "Review", **request).run_id == run.run_id
        assert len(_requests(client, "turn/start")) == 1
    finally:
        broker.close()


def test_queued_edit_retains_all_context_and_reviewed_execution_choices(tmp_path):
    storage, thread, _source, client, broker, file, chat = _setup(tmp_path)
    try:
        broker.submit_prompt(thread.thread_id, "Current", client_request_id="combined-current")
        _, native_thread, native_turn = _active_ids(storage, thread.thread_id)
        queued = broker.submit_prompt(thread.thread_id, "Next", client_request_id="combined-queue",
                     follow_up_mode="queue", web_search="live", max_duration_seconds=30,
                     workspace_context=[file], chat_context=[reference_of(chat)])
        snapshot = _private_run(broker, queued.run_id).goal_context
        broker.update_queued_prompt(thread.thread_id, queued.run_id, "Edited", expected_revision=1)
        private = _private_run(broker, queued.run_id)
        assert private.web_search == "live" and private.max_duration_seconds == 30
        assert private.goal_context == snapshot
        _complete(client, remote_thread_id=native_thread, turn_id=native_turn)
        _wait_until(lambda: len(_requests(client, "turn/start")) == 2)
        inputs = _requests(client, "turn/start")[1]["input"]
        assert len(inputs) == 2  # Explicit live-search guidance and exact public request.
        assert inputs[-1]["text"].startswith("Edited\n\nWorkspace context")
        assert "File evidence\r\n" in inputs[-1]["text"]
        assert chat.text in inputs[-1]["text"]
        assert inputs[-1]["text"].endswith(goal_context_text(snapshot))
        events = storage.list_thread_events(thread.thread_id)
        assert any(event.event_type == "message.updated"
                   and event.payload.get("text") == inputs[-1]["text"] for event in events)
    finally:
        broker.close()
