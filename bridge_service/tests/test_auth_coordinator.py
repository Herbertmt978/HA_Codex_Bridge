from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from threading import Event, Lock
from typing import Any

import pytest

from codex_bridge_service.auth_coordinator import (
    AuthCoordinatorClosedError,
    AuthOperationConflictError,
    CodexAuthCoordinator,
)
from codex_bridge_service.codex_app_server import AppServerNotification
from codex_bridge_service.models import CodexAuthStatusRecord


@dataclass(frozen=True, slots=True)
class AppServerCall:
    method: str
    params: Any
    timeout_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class BlockedReply:
    value: Any
    entered: Event
    release: Event


class FakeAppServerClient:
    """Deterministic in-memory peer for coordinator state-machine tests."""

    def __init__(self, *, generation: int = 1) -> None:
        self.generation = generation
        self.calls: list[AppServerCall] = []
        self.handlers: dict[str, Callable[[AppServerNotification], None]] = {}
        self._replies: dict[str, deque[Any]] = defaultdict(deque)
        self._lock = Lock()

    def script(self, method: str, *replies: Any) -> None:
        with self._lock:
            self._replies[method].extend(replies)

    def request(
        self,
        method: str,
        params: Any = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        with self._lock:
            self.calls.append(AppServerCall(method, deepcopy(params), timeout_seconds))
            if not self._replies[method]:
                raise AssertionError(f"no scripted reply for {method}")
            reply = self._replies[method].popleft()
        if isinstance(reply, BlockedReply):
            reply.entered.set()
            if not reply.release.wait(10):
                raise AssertionError(f"blocked reply for {method} was not released")
            reply = reply.value
        if isinstance(reply, BaseException):
            raise reply
        return deepcopy(reply)

    def register_notification_handler(
        self,
        method: str,
        handler: Callable[[AppServerNotification], None],
    ) -> None:
        if method in self.handlers:
            raise AssertionError(f"duplicate handler for {method}")
        self.handlers[method] = handler

    def emit(self, method: str, params: Any, *, generation: int | None = None) -> None:
        handler = self.handlers[method]
        handler(
            AppServerNotification(
                method=method,
                params=deepcopy(params),
                generation=self.generation if generation is None else generation,
            )
        )


def _signed_out_account() -> dict[str, Any]:
    return {"account": None, "requiresOpenaiAuth": True}


def _chatgpt_account(
    *, email: str = "private-person@example.test", plan_type: str = "plus"
) -> dict[str, Any]:
    return {
        "account": {
            "type": "chatgpt",
            "email": email,
            "planType": plan_type,
        },
        "requiresOpenaiAuth": True,
    }


def _device_login(
    login_id: str = "login-1",
    *,
    user_code: str = "ABCD-EFGH",
    verification_url: str = "https://auth.openai.com/codex/device",
) -> dict[str, Any]:
    return {
        "type": "chatgptDeviceCode",
        "loginId": login_id,
        "userCode": user_code,
        "verificationUrl": verification_url,
    }


def _coordinator(
    client: FakeAppServerClient,
    states: list[Any] | None = None,
    **kwargs: Any,
) -> CodexAuthCoordinator:
    return CodexAuthCoordinator(
        client,
        state_listener=None if states is None else states.append,
        **kwargs,
    )


def _assert_monotonic(states: list[Any]) -> None:
    revisions = [state.revision for state in states]
    assert revisions == list(range(1, len(revisions) + 1))


def test_start_reads_persisted_account_and_publishes_only_safe_state() -> None:
    client = FakeAppServerClient()
    client.script("account/read", _chatgpt_account())
    states: list[Any] = []
    coordinator = _coordinator(client, states)

    status = coordinator.start()

    assert client.calls == [
        AppServerCall("account/read", {"refreshToken": False}, 5.0),
    ]
    assert set(client.handlers) == {"account/login/completed", "account/updated"}
    assert status.state == "ok"
    assert status.busy is False
    assert status.auth_required is False
    assert status.auth_mode == "chatgpt"
    assert status.plan_type == "plus"
    public = status.model_dump()
    assert "email" not in public
    assert "login_id" not in public
    assert "private-person@example.test" not in repr(status)
    assert states[-1] == status
    _assert_monotonic(states)


def test_start_retries_a_transient_account_read_failure() -> None:
    client = FakeAppServerClient()
    client.script(
        "account/read",
        RuntimeError("temporary reusable-secret"),
        _signed_out_account(),
    )
    coordinator = _coordinator(client)

    failed = coordinator.start()
    recovered = coordinator.status()

    assert failed.state == "unavailable"
    assert recovered.state == "logged_out"
    assert recovered.revision > failed.revision
    assert [call.method for call in client.calls] == ["account/read", "account/read"]


def test_concurrent_status_polls_coalesce_a_blocked_start_retry() -> None:
    entered = Event()
    release = Event()
    client = FakeAppServerClient()
    client.script(
        "account/read",
        RuntimeError("temporary failure"),
        BlockedReply(_signed_out_account(), entered, release),
    )
    coordinator = _coordinator(client)
    assert coordinator.start().state == "unavailable"

    with ThreadPoolExecutor(max_workers=1) as executor:
        retrying = executor.submit(coordinator.status)
        assert entered.wait(10)
        concurrent = coordinator.status()
        release.set()
        recovered = retrying.result(timeout=12)

    assert concurrent.state == "unavailable"
    assert recovered.state == "logged_out"
    assert [call.method for call in client.calls] == ["account/read", "account/read"]


def test_account_read_from_a_stale_app_server_generation_is_not_published() -> None:
    entered = Event()
    release = Event()
    client = FakeAppServerClient(generation=1)
    client.script(
        "account/read",
        BlockedReply(_chatgpt_account(), entered, release),
        _signed_out_account(),
    )
    coordinator = _coordinator(client)

    with ThreadPoolExecutor(max_workers=1) as executor:
        starting = executor.submit(coordinator.start)
        assert entered.wait(10)
        client.generation = 2
        release.set()
        stale = starting.result(timeout=12)

    assert stale.state == "unavailable"
    assert stale.auth_mode is None
    assert coordinator.start().state == "logged_out"


def test_device_login_uses_chatgpt_device_code_without_implicit_logout() -> None:
    client = FakeAppServerClient()
    client.script("account/read", _signed_out_account())
    client.script("account/login/start", _device_login())
    coordinator = _coordinator(client)
    coordinator.start()

    status = coordinator.start_device_login(force_logout=True)

    assert client.calls == [
        AppServerCall("account/read", {"refreshToken": False}, 5.0),
        AppServerCall("account/login/start", {"type": "chatgptDeviceCode"}),
    ]
    assert status.state == "login_running"
    assert status.busy is True
    assert status.auth_required is True
    assert status.verification_uri == "https://auth.openai.com/codex/device"
    assert status.login_url == "https://auth.openai.com/codex/device"
    assert status.user_code == "ABCD-EFGH"
    assert "login_id" not in status.model_dump()


@pytest.mark.parametrize(
    "account",
    [
        _chatgpt_account(),
        {"account": {"type": "apiKey"}, "requiresOpenaiAuth": True},
    ],
    ids=["already-signed-in", "unsupported-mode"],
)
def test_login_requires_explicit_sign_out_from_an_existing_account(
    account: dict[str, Any],
) -> None:
    client = FakeAppServerClient()
    client.script("account/read", account)
    coordinator = _coordinator(client)
    coordinator.start()

    with pytest.raises(AuthOperationConflictError):
        coordinator.start_device_login(force_logout=True)

    assert client.calls == [
        AppServerCall("account/read", {"refreshToken": False}, 5.0),
    ]


@pytest.mark.parametrize(
    "verification_url",
    [
        "http://auth.openai.com/codex/device",
        "https://user:secret@auth.openai.com/codex/device",
        "https://auth.openai.com/codex/device?token=secret",
        "https://auth.openai.com/codex/device#fragment",
        "https://auth.openai.com/codex/device\n",
        "https://evil.example/codex/device",
    ],
)
def test_device_login_rejects_unsafe_verification_urls(
    verification_url: str,
) -> None:
    client = FakeAppServerClient()
    client.script("account/read", _signed_out_account())
    client.script(
        "account/login/start",
        _device_login(verification_url=verification_url),
    )
    coordinator = _coordinator(client)
    coordinator.start()

    status = coordinator.start_device_login()

    assert status.state == "login_failed"
    assert status.verification_uri is None
    assert status.login_url is None
    assert verification_url not in repr(status)


def test_matching_generation_and_login_id_complete_login_with_final_read() -> None:
    client = FakeAppServerClient(generation=7)
    client.script(
        "account/read", _signed_out_account(), _chatgpt_account(plan_type="pro")
    )
    client.script("account/login/start", _device_login("login-correct"))
    states: list[Any] = []
    coordinator = _coordinator(client, states)
    coordinator.start()
    pending = coordinator.start_device_login()

    client.emit(
        "account/login/completed",
        {"loginId": "login-correct", "success": True, "error": None},
        generation=7,
    )

    status = coordinator.status()
    assert status.revision > pending.revision
    assert status.state == "ok"
    assert status.busy is False
    assert status.auth_required is False
    assert status.auth_mode == "chatgpt"
    assert status.plan_type == "pro"
    assert status.verification_uri is None
    assert status.login_url is None
    assert status.user_code is None
    assert client.calls[-1] == AppServerCall(
        "account/read", {"refreshToken": False}, 5.0
    )
    _assert_monotonic(states)


@pytest.mark.parametrize("login_id", [None, "missing"], ids=["null", "missing"])
def test_uncorrelated_completion_cannot_replace_the_active_login(
    login_id: str | None,
) -> None:
    client = FakeAppServerClient(generation=7)
    client.script(
        "account/read",
        _signed_out_account(),
        _signed_out_account(),
    )
    client.script(
        "account/login/start",
        _device_login("login-a", user_code="AAAA-BBBB"),
        _device_login("login-b", user_code="CCCC-DDDD"),
    )
    client.script("account/login/cancel", {})
    coordinator = _coordinator(client)
    coordinator.start()
    coordinator.start_device_login()
    coordinator.cancel_login()
    active = coordinator.start_device_login()
    payload: dict[str, Any] = {"success": True, "error": None}
    if login_id != "missing":
        payload["loginId"] = login_id

    client.emit("account/login/completed", payload, generation=7)

    status = coordinator.status()
    assert active.state == status.state == "login_running"
    assert status.revision == active.revision
    assert status.user_code == "CCCC-DDDD"


def test_status_poll_recovers_active_login_when_completion_notification_is_missed() -> None:
    client = FakeAppServerClient(generation=7)
    client.script(
        "account/read", _signed_out_account(), _chatgpt_account(plan_type="pro")
    )
    client.script("account/login/start", _device_login("login-correct"))
    coordinator = _coordinator(client, active_login_poll_interval_seconds=0)
    coordinator.start()
    pending = coordinator.start_device_login()

    recovered = coordinator.status()

    assert pending.state == "login_running"
    assert recovered.state == "ok"
    assert recovered.auth_mode == "chatgpt"
    assert recovered.plan_type == "pro"
    assert recovered.user_code is None


def test_status_poll_keeps_device_code_until_account_is_authoritatively_signed_in() -> None:
    client = FakeAppServerClient(generation=7)
    client.script(
        "account/read",
        _signed_out_account(),
        _signed_out_account(),
        _chatgpt_account(plan_type="pro"),
    )
    client.script("account/login/start", _device_login("login-correct"))
    coordinator = _coordinator(client, active_login_poll_interval_seconds=0)
    coordinator.start()
    pending = coordinator.start_device_login()

    still_pending = coordinator.status()
    recovered = coordinator.status()

    assert still_pending.state == "login_running"
    assert still_pending.revision == pending.revision
    assert still_pending.user_code == pending.user_code == "ABCD-EFGH"
    assert recovered.state == "ok"
    assert recovered.user_code is None


def test_status_poll_retries_after_a_transport_failure_without_clearing_the_code() -> None:
    client = FakeAppServerClient(generation=7)
    client.script(
        "account/read",
        _signed_out_account(),
        RuntimeError("temporary private transport failure"),
        _chatgpt_account(plan_type="pro"),
    )
    client.script("account/login/start", _device_login("login-correct"))
    coordinator = _coordinator(client, active_login_poll_interval_seconds=0)
    coordinator.start()
    pending = coordinator.start_device_login()

    failed_poll = coordinator.status()
    recovered = coordinator.status()

    assert failed_poll.state == "login_running"
    assert failed_poll.revision == pending.revision
    assert failed_poll.user_code == "ABCD-EFGH"
    assert recovered.state == "ok"
    assert recovered.user_code is None


def test_stale_generation_or_wrong_login_id_cannot_complete_active_login() -> None:
    client = FakeAppServerClient(generation=4)
    client.script("account/read", _signed_out_account(), _chatgpt_account())
    client.script("account/login/start", _device_login("active-login"))
    coordinator = _coordinator(client)
    coordinator.start()
    active = coordinator.start_device_login()

    client.emit(
        "account/login/completed",
        {"loginId": "active-login", "success": True, "error": None},
        generation=3,
    )
    client.emit(
        "account/login/completed",
        {"loginId": "other-login", "success": True, "error": None},
        generation=4,
    )

    unchanged = coordinator.status()
    assert unchanged.revision == active.revision
    assert unchanged.state == "login_running"
    assert len(client.calls) == 2

    client.emit(
        "account/login/completed",
        {"loginId": "active-login", "success": True, "error": None},
        generation=4,
    )
    assert coordinator.status().state == "ok"


def test_cancel_uses_active_login_id_then_settles_through_account_read() -> None:
    client = FakeAppServerClient()
    client.script("account/read", _signed_out_account(), _signed_out_account())
    client.script("account/login/start", _device_login("login-to-cancel"))
    client.script("account/login/cancel", {"status": "canceled"})
    coordinator = _coordinator(client)
    coordinator.start()
    coordinator.start_device_login()

    status = coordinator.cancel_login()

    assert client.calls[-2:] == [
        AppServerCall("account/login/cancel", {"loginId": "login-to-cancel"}),
        AppServerCall("account/read", {"refreshToken": False}, 5.0),
    ]
    assert status.state == "logged_out"
    assert status.busy is False
    assert status.auth_required is True
    assert status.verification_uri is None
    assert status.login_url is None
    assert status.user_code is None


def test_cancel_during_login_start_is_honored_once_login_id_arrives() -> None:
    entered = Event()
    release = Event()
    client = FakeAppServerClient()
    client.script("account/read", _signed_out_account(), _signed_out_account())
    client.script(
        "account/login/start",
        BlockedReply(_device_login("late-login-id"), entered, release),
    )
    client.script("account/login/cancel", {"status": "canceled"})
    coordinator = _coordinator(client)
    coordinator.start()

    with ThreadPoolExecutor(max_workers=1) as executor:
        starting = executor.submit(coordinator.start_device_login)
        assert entered.wait(10)

        cancelling = coordinator.cancel_login()

        assert cancelling.state == "login_canceling"
        assert cancelling.busy is True
        release.set()
        settled = starting.result(timeout=12)

    assert client.calls[-2:] == [
        AppServerCall("account/login/cancel", {"loginId": "late-login-id"}),
        AppServerCall("account/read", {"refreshToken": False}, 5.0),
    ]
    assert settled.state == "logged_out"
    assert settled.busy is False


def test_logout_is_explicit_and_verifies_sign_out_with_final_read() -> None:
    client = FakeAppServerClient()
    client.script("account/read", _chatgpt_account(), _signed_out_account())
    client.script("account/logout", {})
    coordinator = _coordinator(client)
    coordinator.start()

    status = coordinator.logout()

    assert client.calls[-2:] == [
        AppServerCall("account/logout", None),
        AppServerCall("account/read", {"refreshToken": False}, 5.0),
    ]
    assert status.state == "logged_out"
    assert status.busy is False
    assert status.auth_required is True
    assert status.auth_mode is None
    assert status.plan_type is None


def test_logout_final_read_is_authoritative_when_logout_response_is_lost() -> None:
    client = FakeAppServerClient()
    client.script("account/read", _chatgpt_account(), _signed_out_account())
    client.script("account/logout", RuntimeError("lost response reusable-secret"))
    coordinator = _coordinator(client)
    coordinator.start()

    status = coordinator.logout()

    assert status.state == "logged_out"
    assert status.auth_mode is None


@pytest.mark.parametrize(
    "final_read",
    [_chatgpt_account(), RuntimeError("account read failed reusable-secret")],
    ids=["still-signed-in", "read-unavailable"],
)
def test_failed_logout_retains_the_known_account_and_blocks_login(
    final_read: Any,
) -> None:
    client = FakeAppServerClient()
    client.script("account/read", _chatgpt_account(), final_read)
    client.script("account/logout", {})
    coordinator = _coordinator(client)
    coordinator.start()

    status = coordinator.logout()

    assert status.state == "ok"
    assert status.auth_mode == "chatgpt"
    assert status.plan_type == "plus"
    assert status.message is not None
    assert "did not complete" in status.message.lower()
    with pytest.raises(AuthOperationConflictError):
        coordinator.start_device_login()


def test_restart_reconciles_persisted_login_with_account_read() -> None:
    client = FakeAppServerClient(generation=1)
    client.script(
        "account/read", _signed_out_account(), _chatgpt_account(plan_type="team")
    )
    coordinator = _coordinator(client)
    first = coordinator.start()
    client.generation = 2

    recovered = coordinator.reconcile_after_restart()

    assert recovered.revision > first.revision
    assert recovered.state == "ok"
    assert recovered.auth_mode == "chatgpt"
    assert recovered.plan_type == "team"
    assert client.calls[-1] == AppServerCall(
        "account/read", {"refreshToken": False}, 5.0
    )


def test_status_poll_recovers_an_active_login_after_app_server_restart() -> None:
    client = FakeAppServerClient(generation=1)
    client.script("account/read", _signed_out_account(), _chatgpt_account())
    client.script("account/login/start", _device_login("stale-login"))
    coordinator = _coordinator(client)
    coordinator.start()
    active = coordinator.start_device_login()
    client.generation = 2

    recovered = coordinator.status()

    assert active.state == "login_running"
    assert recovered.state == "ok"
    assert recovered.revision > active.revision
    assert recovered.verification_uri is None
    assert recovered.user_code is None
    assert client.calls[-1] == AppServerCall(
        "account/read", {"refreshToken": False}, 5.0
    )


def test_repeated_identical_failures_each_advance_revision_and_clear_codes() -> None:
    raw_error = "expired reusable-token person@example.test"
    client = FakeAppServerClient()
    client.script("account/read", _signed_out_account())
    client.script(
        "account/login/start",
        _device_login("login-1"),
        _device_login("login-2"),
    )
    states: list[Any] = []
    coordinator = _coordinator(client, states)
    coordinator.start()

    coordinator.start_device_login()
    client.emit(
        "account/login/completed",
        {"loginId": "login-1", "success": False, "error": raw_error},
    )
    first_failure = coordinator.status()

    coordinator.start_device_login()
    client.emit(
        "account/login/completed",
        {"loginId": "login-2", "success": False, "error": raw_error},
    )
    second_failure = coordinator.status()

    assert first_failure.state == second_failure.state == "login_failed"
    assert second_failure.revision > first_failure.revision
    assert second_failure.verification_uri is None
    assert second_failure.login_url is None
    assert second_failure.user_code is None
    assert second_failure.message is not None
    assert raw_error not in second_failure.message
    assert "reusable-token" not in repr(states)
    assert "person@example.test" not in repr(states)
    _assert_monotonic(states)


def test_concurrent_auth_mutations_conflict_while_login_start_is_in_flight() -> None:
    entered = Event()
    release = Event()
    client = FakeAppServerClient()
    client.script("account/read", _signed_out_account(), _signed_out_account())
    client.script(
        "account/login/start",
        BlockedReply(_device_login("busy-login"), entered, release),
    )
    client.script("account/login/cancel", {"status": "canceled"})
    coordinator = _coordinator(client)
    coordinator.start()

    with ThreadPoolExecutor(max_workers=1) as executor:
        starting = executor.submit(coordinator.start_device_login)
        assert entered.wait(10)

        with pytest.raises(AuthOperationConflictError):
            coordinator.logout()
        with pytest.raises(AuthOperationConflictError):
            coordinator.start_device_login()

        coordinator.cancel_login()
        release.set()
        starting.result(timeout=12)


@pytest.mark.parametrize(
    "auth_mode",
    ["apikey", "personalAccessToken", "chatgptAuthTokens", "agentIdentity"],
)
def test_account_updates_block_unsupported_auth_modes(auth_mode: str) -> None:
    client = FakeAppServerClient()
    client.script("account/read", _signed_out_account())
    coordinator = _coordinator(client)
    coordinator.start()

    client.emit(
        "account/updated",
        {"authMode": auth_mode, "planType": None},
    )

    status = coordinator.status()
    assert status.state == "unsupported"
    assert status.busy is False
    assert status.auth_required is True
    assert status.auth_mode == auth_mode
    assert status.message is not None
    assert "sign out" in status.message.lower()
    assert "chatgpt" in status.message.lower()


def test_startup_api_key_account_is_normalized_as_unsupported() -> None:
    client = FakeAppServerClient()
    client.script(
        "account/read",
        {"account": {"type": "apiKey"}, "requiresOpenaiAuth": True},
    )
    coordinator = _coordinator(client)

    status = coordinator.start()

    assert status.state == "unsupported"
    assert status.auth_required is True
    assert status.auth_mode == "apikey"


def test_missing_device_authorization_reports_safe_recovery_guidance() -> None:
    secret_error = "device auth disabled; bearer reusable-secret; admin@example.test"
    client = FakeAppServerClient()
    client.script("account/read", _signed_out_account())
    client.script("account/login/start", RuntimeError(secret_error))
    coordinator = _coordinator(client)
    coordinator.start()

    status = coordinator.start_device_login()

    assert status.state == "login_failed"
    assert status.busy is False
    assert status.auth_required is True
    assert status.verification_uri is None
    assert status.login_url is None
    assert status.user_code is None
    assert status.message is not None
    assert "device" in status.message.lower()
    assert "enable" in status.message.lower()
    assert "reusable-secret" not in status.message
    assert "admin@example.test" not in status.message


def test_auth_state_changes_publish_without_chat_context_and_repeat_occurrences() -> (
    None
):
    client = FakeAppServerClient()
    client.script("account/read", _signed_out_account())
    states: list[Any] = []
    coordinator = _coordinator(client, states)
    coordinator.start()

    update = {"authMode": None, "planType": None}
    client.emit("account/updated", update)
    first_revision = coordinator.status().revision
    client.emit("account/updated", update)

    assert coordinator.status().revision == first_revision + 1
    assert all(not hasattr(state, "chat_id") for state in states)
    _assert_monotonic(states)


def test_state_listener_can_reenter_status_without_the_coordinator_lock() -> None:
    client = FakeAppServerClient()
    client.script("account/read", _signed_out_account())
    observed: list[int] = []
    holder: dict[str, CodexAuthCoordinator] = {}

    def listener(state: Any) -> None:
        observed.append(state.revision)
        assert holder["coordinator"].status().revision >= state.revision

    coordinator = CodexAuthCoordinator(client, state_listener=listener)
    holder["coordinator"] = coordinator

    status = coordinator.start()

    assert observed == [status.revision]


def test_durable_initial_auth_revision_continues_monotonically() -> None:
    client = FakeAppServerClient()
    client.script("account/read", _signed_out_account())
    states: list[CodexAuthStatusRecord] = []
    initial = CodexAuthStatusRecord(
        revision=7,
        state="unavailable",
        auth_required=True,
        message="Previous safe status.",
        updated_at="2026-07-12T10:00:00Z",
    )
    coordinator = CodexAuthCoordinator(
        client,
        initial_status=initial,
        state_listener=states.append,
    )

    status = coordinator.start()

    assert status.revision == 8
    assert [state.revision for state in states] == [8]


def test_fatal_durable_listener_failure_propagates_to_the_owner() -> None:
    client = FakeAppServerClient()
    client.script("account/read", _signed_out_account())

    def fail(_status: CodexAuthStatusRecord) -> None:
        raise RuntimeError("durable sink unavailable")

    coordinator = CodexAuthCoordinator(
        client,
        state_listener=fail,
        state_listener_fatal=True,
    )

    with pytest.raises(RuntimeError, match="durable sink unavailable"):
        coordinator.start()


def test_sparse_account_updates_preserve_auth_and_merge_plan_fields() -> None:
    client = FakeAppServerClient()
    client.script("account/read", _chatgpt_account(plan_type="plus"))
    coordinator = _coordinator(client)
    initial = coordinator.start()

    client.emit("account/updated", {})
    unchanged = coordinator.status()
    client.emit("account/updated", {"planType": "pro"})
    updated = coordinator.status()

    assert unchanged.revision == initial.revision + 1
    assert unchanged.state == "ok"
    assert unchanged.auth_mode == "chatgpt"
    assert unchanged.plan_type == "plus"
    assert updated.revision == unchanged.revision + 1
    assert updated.state == "ok"
    assert updated.auth_mode == "chatgpt"
    assert updated.plan_type == "pro"


def test_close_cancels_login_clears_code_and_ignores_late_notifications() -> None:
    client = FakeAppServerClient()
    client.script("account/read", _signed_out_account(), _signed_out_account())
    client.script("account/login/start", _device_login("closing-login"))
    client.script("account/login/cancel", {"status": "canceled"})
    coordinator = _coordinator(client)
    coordinator.start()
    coordinator.start_device_login()

    coordinator.close()

    closed = coordinator.status()
    assert client.calls[-1] == AppServerCall(
        "account/login/cancel",
        {"loginId": "closing-login"},
        2.0,
    )
    assert closed.state == "closed"
    assert closed.busy is False
    assert closed.verification_uri is None
    assert closed.login_url is None
    assert closed.user_code is None

    client.emit(
        "account/login/completed",
        {"loginId": "closing-login", "success": True, "error": None},
    )
    client.emit(
        "account/updated",
        {"authMode": "chatgpt", "planType": "pro"},
    )
    assert coordinator.status().revision == closed.revision
    assert coordinator.status().state == "closed"
    with pytest.raises(AuthCoordinatorClosedError):
        coordinator.start_device_login()
    with pytest.raises(AuthCoordinatorClosedError):
        coordinator.cancel_login()
    with pytest.raises(AuthCoordinatorClosedError):
        coordinator.logout()
    with pytest.raises(AuthCoordinatorClosedError):
        coordinator.reconcile_after_restart()
