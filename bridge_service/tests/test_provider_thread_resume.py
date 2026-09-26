"""Retained provider threads are cold-loaded before policy-sensitive resumes."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from threading import Event
from typing import Any

import pytest

from codex_bridge_service.codex_app_server import AppServerRemoteError
from codex_bridge_service.models import RunMode
from test_runtime_broker import (
    ValidatorBackedAppServer,
    _active_ids,
    _broker,
    _complete,
    _requests,
    _storage_and_thread,
    _wait_until,
)


class PinnedResumePeer(ValidatorBackedAppServer):
    """Model a hot provider thread that ignores resume configuration overrides."""

    def __init__(self) -> None:
        super().__init__()
        self.enable_experimental_api = True
        self.supports_collaboration_mode = True
        self.loaded_settings: dict[str, Any] | None = None
        self.provider_thread_id: str | None = None
        self.turn_history: list[dict[str, Any]] = []
        self.resume_history: list[list[dict[str, Any]]] = []
        self.unsubscribe_responses: deque[Any] = deque()
        self.permanent_not_subscribed = False
        self.mismatch_next_resume: str | None = None
        self.block_next_unsubscribe = False
        self.unsubscribe_entered = Event()
        self.release_unsubscribe = Event()

    def _default_result(self, method: str, params: Any) -> dict[str, Any]:
        if method == "thread/unsubscribe":
            if self.unsubscribe_responses:
                result = self.unsubscribe_responses.popleft()
                if isinstance(result, dict) and result.get("status") == "notLoaded":
                    self.loaded_settings = None
                return result
            if self.permanent_not_subscribed:
                return {"status": "notSubscribed"}
            if self.loaded_settings is None:
                return {"status": "notLoaded"}
            if not getattr(self, "_unsubscribe_announced", False):
                self._unsubscribe_announced = True
                return {"status": "unsubscribed"}
            self.loaded_settings = None
            self._unsubscribe_announced = False
            return {"status": "notLoaded"}

        if method in {"thread/start", "thread/resume"}:
            is_resume = method == "thread/resume"
            hot_resume = is_resume and self.loaded_settings is not None
            if not hot_resume:
                self.loaded_settings = deepcopy(params)
            effective = deepcopy(self.loaded_settings if hot_resume else params)
            if is_resume:
                effective["threadId"] = params["threadId"]
                self.resume_history.append(deepcopy(self.turn_history))
            result = super()._default_result(method, effective)
            if method == "thread/start":
                self.provider_thread_id = result["thread"]["id"]
            result["thread"]["turns"] = deepcopy(self.turn_history)
            if is_resume and self.mismatch_next_resume:
                mismatch, self.mismatch_next_resume = self.mismatch_next_resume, None
                if mismatch == "model":
                    result["model"] = "gpt-5.6-mismatched"
                elif mismatch == "profile":
                    result["activePermissionProfile"]["id"] = "ha_observe"
                elif mismatch == "identity":
                    result["thread"]["id"] = "different-provider-thread"
            return result

        if method == "turn/start":
            result = super()._default_result(method, params)
            self.turn_history.append(deepcopy(result["turn"]))
            return result
        return super()._default_result(method, params)

    def request(self, method: str, params: Any = None, *, timeout_seconds=None):
        result = super().request(method, params, timeout_seconds=timeout_seconds)
        if method == "thread/unsubscribe" and self.block_next_unsubscribe:
            self.block_next_unsubscribe = False
            self.unsubscribe_entered.set()
            if not self.release_unsubscribe.wait(2):
                raise AssertionError("test did not release provider detach")
        return result

    def abort_generation(self, expected_generation: int) -> bool:
        aborted = super().abort_generation(expected_generation)
        if aborted:
            self.release_unsubscribe.set()
        return aborted

    def emit_notification(self, method: str, params: Any, *, generation=None) -> None:
        if method == "turn/completed":
            turn_id = params.get("turn", {}).get("id")
            for turn in self.turn_history:
                if turn["id"] == turn_id:
                    turn.update(deepcopy(params["turn"]))
        super().emit_notification(method, params, generation=generation)


def _client_request(index: int) -> str:
    return f"provider-resume-{index:04d}"


def _submit_and_complete(broker, peer, storage, thread, *, collaboration_mode: str, index: int):
    run = broker.submit_prompt(
        thread.thread_id,
        f"Run {index} in {collaboration_mode} mode",
        client_request_id=_client_request(index),
        collaboration_mode=collaboration_mode,
    )
    _wait_until(lambda: len(_requests(peer, "turn/start")) >= index)
    run_id, provider_id, turn_id = _active_ids(storage, thread.thread_id)
    _complete(peer, remote_thread_id=provider_id, turn_id=turn_id)
    _wait_until(lambda: any(
        event.event_type == "run.completed" and event.payload.get("run_id") == run_id
        for event in storage.list_thread_events(thread.thread_id)
    ))
    return run, run_id, provider_id


def _start_broker(tmp_path, *, mode: RunMode = RunMode.EDIT):
    storage, thread = _storage_and_thread(tmp_path, mode=mode)
    peer = PinnedResumePeer()
    broker = _broker(storage, peer, turn_timeout_seconds=2.0)
    # Detach polling remains bounded in malformed/unsubscribe-failure cases.
    broker.control_request_timeout_seconds = 0.18
    return storage, thread, peer, broker


def _wait_run_terminal(storage, thread_id: str, run_id: str) -> str:
    def status() -> str | None:
        for event in storage.list_thread_events(thread_id):
            if event.payload.get("run_id") == run_id and event.event_type in {
                "run.completed", "run.cancelled", "run.failed", "run.interrupted",
            }:
                return event.event_type
        return None

    _wait_until(lambda: status() is not None, timeout=3.0, message="run did not reach a terminal state")
    result = status()
    assert result is not None
    return result


@pytest.mark.parametrize(
    ("first_mode", "second_mode", "first_profile", "second_profile"),
    [
        ("plan", "default", "ha_observe", "ha_bridge"),
        ("default", "plan", "ha_bridge", "ha_observe"),
    ],
)
def test_policy_mode_switch_cold_resumes_same_provider_thread_and_history(
    tmp_path, first_mode, second_mode, first_profile, second_profile,
):
    storage, thread, peer, broker = _start_broker(tmp_path)
    try:
        _first, _first_run, provider_id = _submit_and_complete(
            broker, peer, storage, thread, collaboration_mode=first_mode, index=1,
        )
        assert peer.loaded_settings["config"]["default_permissions"] == first_profile

        second = broker.submit_prompt(
            thread.thread_id,
            f"Continue in {second_mode} mode",
            client_request_id=_client_request(2),
            collaboration_mode=second_mode,
        )
        _wait_until(lambda: len(_requests(peer, "turn/start")) == 2)
        _run_id, resumed_provider_id, _turn_id = _active_ids(storage, thread.thread_id)

        starts = _requests(peer, "thread/start")
        resumes = _requests(peer, "thread/resume")
        turns = _requests(peer, "turn/start")
        assert len(starts) == 1
        assert len(resumes) == 1
        assert resumes[0]["threadId"] == provider_id == resumed_provider_id
        assert peer.resume_history == [[peer.turn_history[0]]]
        assert peer.loaded_settings["config"]["default_permissions"] == second_profile
        assert turns[1]["collaborationMode"]["mode"] == second_mode
        assert turns[1]["sandboxPolicy"]["type"] == (
            "readOnly" if second_mode == "plan" else "workspaceWrite"
        )

        _complete(peer, remote_thread_id=provider_id, turn_id=_turn_id)
        _wait_until(lambda: _wait_run_terminal(storage, thread.thread_id, second.run_id) == "run.completed")
    finally:
        broker.close()


@pytest.mark.parametrize("changed_setting", ["model", "web_search"])
def test_same_mode_resume_applies_updated_model_or_web_search(tmp_path, changed_setting):
    storage, thread, peer, broker = _start_broker(tmp_path)
    try:
        _submit_and_complete(
            broker, peer, storage, thread, collaboration_mode="default", index=1,
        )
        prompt_options: dict[str, Any] = {}
        if changed_setting == "model":
            storage.update_thread(thread.thread_id, model_override="gpt-5.6")
        else:
            prompt_options["web_search"] = "live"

        second = broker.submit_prompt(
            thread.thread_id,
            "Use the updated thread settings",
            client_request_id=_client_request(2),
            collaboration_mode="default",
            **prompt_options,
        )
        _wait_until(lambda: len(_requests(peer, "turn/start")) == 2)
        resume = _requests(peer, "thread/resume")[0]
        turn = _requests(peer, "turn/start")[1]
        if changed_setting == "model":
            assert resume["model"] == turn["model"] == "gpt-5.6"
        else:
            assert resume["config"]["web_search"] == "live"
            assert turn["collaborationMode"]["settings"]["developer_instructions"] is None
        _run_id, provider_id, turn_id = _active_ids(storage, thread.thread_id)
        _complete(peer, remote_thread_id=provider_id, turn_id=turn_id)
        _wait_until(lambda: _wait_run_terminal(storage, thread.thread_id, second.run_id) == "run.completed")
    finally:
        broker.close()


@pytest.mark.parametrize("response_kind", ["malformed", "non_string", "extra_fields", "unknown", "permanent_not_subscribed", "rpc_error"])
def test_uncertain_provider_detach_rejects_resume_before_turn_start(tmp_path, response_kind):
    storage, thread, peer, broker = _start_broker(tmp_path)
    try:
        _submit_and_complete(
            broker, peer, storage, thread, collaboration_mode="plan", index=1,
        )
        if response_kind == "malformed":
            peer.unsubscribe_responses.append({})
        elif response_kind == "non_string":
            peer.unsubscribe_responses.append({"status": {"unverified": "notLoaded"}})
        elif response_kind == "extra_fields":
            peer.unsubscribe_responses.append({"status": "notLoaded", "unexpected": True})
        elif response_kind == "unknown":
            peer.unsubscribe_responses.append({"status": "futureStatus"})
        elif response_kind == "permanent_not_subscribed":
            peer.permanent_not_subscribed = True
        else:
            peer.unsubscribe_responses.append(AppServerRemoteError(
                method="thread/unsubscribe", code=-32603,
            ))

        second = broker.submit_prompt(
            thread.thread_id,
            "Do not start unless detach is verified",
            client_request_id=_client_request(2),
            collaboration_mode="default",
        )
        assert _wait_run_terminal(storage, thread.thread_id, second.run_id) in {
            "run.failed", "run.interrupted",
        }
        assert len(_requests(peer, "turn/start")) == 1
        assert len(_requests(peer, "thread/resume")) == 0
    finally:
        broker.close()


@pytest.mark.parametrize("mismatch", ["profile", "model", "identity"])
def test_mismatched_resume_response_is_rejected_before_turn_start(tmp_path, mismatch):
    storage, thread, peer, broker = _start_broker(tmp_path)
    try:
        _submit_and_complete(
            broker, peer, storage, thread, collaboration_mode="plan", index=1,
        )
        peer.mismatch_next_resume = mismatch
        second = broker.submit_prompt(
            thread.thread_id,
            "Reject an unverified provider configuration",
            client_request_id=_client_request(2),
            collaboration_mode="default",
        )
        assert _wait_run_terminal(storage, thread.thread_id, second.run_id) == "run.failed"
        assert len(_requests(peer, "thread/resume")) == 1
        assert len(_requests(peer, "turn/start")) == 1
    finally:
        broker.close()


@pytest.mark.parametrize("race", ["generation", "cancel"])
def test_generation_or_cancellation_race_during_detach_never_starts_turn(tmp_path, race):
    storage, thread, peer, broker = _start_broker(tmp_path)
    try:
        _submit_and_complete(
            broker, peer, storage, thread, collaboration_mode="plan", index=1,
        )
        peer.block_next_unsubscribe = True
        second = broker.submit_prompt(
            thread.thread_id,
            "Detach race must not dispatch the turn",
            client_request_id=_client_request(2),
            collaboration_mode="default",
        )
        assert peer.unsubscribe_entered.wait(2)
        assert len(_requests(peer, "turn/start")) == 1
        if race == "generation":
            assert peer.abort_generation(1)
            assert _wait_run_terminal(storage, thread.thread_id, second.run_id) in {
                "run.failed", "run.interrupted",
            }
        else:
            cancelled = broker.cancel_run(thread.thread_id, run_id=second.run_id)
            assert cancelled.status == "cancelled"
            assert _wait_run_terminal(storage, thread.thread_id, second.run_id) == "run.cancelled"
        assert len(_requests(peer, "turn/start")) == 1
        assert len(_requests(peer, "thread/resume")) == 0
    finally:
        peer.release_unsubscribe.set()
        broker.close()
