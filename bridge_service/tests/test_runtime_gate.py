from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from threading import Barrier, Event, Lock
from typing import Any

import pytest

from codex_bridge_service.auth_coordinator import CodexAuthCoordinator
from codex_bridge_service.codex_app_server import AppServerNotification
from codex_bridge_service.resource_limits import ResourceLimits
from codex_bridge_service.runtime_gate import (
    RuntimeGate,
    RuntimeGateClosedError,
    RuntimeLease,
    RuntimeLeaseCancelledError,
    RuntimeLeaseTimeoutError,
    RuntimeMutationConflictError,
    RuntimeQueueFullError,
)


def _gate(*, active: int = 1, queued: int = 8) -> RuntimeGate:
    return RuntimeGate(
        limits=ResourceLimits(
            max_active_turns=active,
            max_queued_prompts=queued,
        )
    )


def _assert_counts(
    gate: RuntimeGate,
    *,
    active: int,
    queued: int,
    auth: bool = False,
    closed: bool = False,
) -> None:
    snapshot = gate.snapshot()
    assert snapshot.active_turns == active
    assert snapshot.queued_prompts == queued
    assert snapshot.auth_mutation_active is auth
    assert snapshot.closed is closed
    assert snapshot.active_turns >= 0
    assert snapshot.queued_prompts >= 0


def test_default_gate_allows_one_active_turn_and_queues_the_next_prompt() -> None:
    gate = _gate()

    first = gate.reserve_prompt(client_request_id="request-1")
    second = gate.reserve_prompt(client_request_id="request-2")

    assert first.state == "active"
    assert second.state == "queued"
    assert first.wait_until_active(timeout_seconds=0) is first
    _assert_counts(gate, active=1, queued=1)

    first.release()
    second.release()


def test_active_turn_limit_is_consumed_from_the_immutable_resource_limits() -> None:
    gate = _gate(active=2)

    first = gate.reserve_prompt(client_request_id="request-1")
    second = gate.reserve_prompt(client_request_id="request-2")
    third = gate.reserve_prompt(client_request_id="request-3")

    assert (first.state, second.state, third.state) == (
        "active",
        "active",
        "queued",
    )
    _assert_counts(gate, active=2, queued=1)

    first.release()
    assert third.wait_until_active(timeout_seconds=1) is third
    _assert_counts(gate, active=2, queued=0)
    second.release()
    third.release()


def test_only_eight_prompt_reservations_may_wait_by_default() -> None:
    gate = _gate()
    active = gate.reserve_prompt(client_request_id="active")
    queued = [
        gate.reserve_prompt(client_request_id=f"queued-{index}") for index in range(8)
    ]

    with pytest.raises(RuntimeQueueFullError):
        gate.reserve_prompt(client_request_id="queue-overflow")

    _assert_counts(gate, active=1, queued=8)
    active.release()
    for lease in queued:
        lease.release()


def test_queued_prompts_are_promoted_in_fifo_order() -> None:
    gate = _gate()
    first = gate.reserve_prompt(client_request_id="first")
    second = gate.reserve_prompt(client_request_id="second")
    third = gate.reserve_prompt(client_request_id="third")

    first.release()
    assert second.wait_until_active(timeout_seconds=1) is second
    assert third.state == "queued"

    second.release()
    assert third.wait_until_active(timeout_seconds=1) is third
    third.release()


def test_concurrent_duplicate_prompt_reservations_share_one_live_lease() -> None:
    gate = _gate()
    barrier = Barrier(12)

    def reserve() -> RuntimeLease:
        barrier.wait()
        return gate.reserve_prompt(client_request_id="stable-request-id")

    with ThreadPoolExecutor(max_workers=12) as executor:
        leases = list(executor.map(lambda _index: reserve(), range(12)))

    assert all(lease is leases[0] for lease in leases)
    _assert_counts(gate, active=1, queued=0)
    leases[0].release()


def test_cancel_wakes_a_queued_waiter_and_removes_its_reservation() -> None:
    gate = _gate()
    active = gate.reserve_prompt(client_request_id="active")
    queued = gate.reserve_prompt(client_request_id="queued")
    waiting = Event()

    def wait_for_turn() -> None:
        waiting.set()
        with pytest.raises(RuntimeLeaseCancelledError):
            queued.wait_until_active(timeout_seconds=5)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(wait_for_turn)
        assert waiting.wait(1)
        queued.cancel()
        future.result(timeout=1)

    assert queued.state == "cancelled"
    _assert_counts(gate, active=1, queued=0)
    active.release()


def test_cancelling_active_lease_does_not_promote_until_terminal_release() -> None:
    gate = _gate()
    active = gate.reserve_prompt(client_request_id="active")
    queued = gate.reserve_prompt(client_request_id="queued")

    active.cancel()

    assert active.state == "active"
    assert queued.state == "queued"
    _assert_counts(gate, active=1, queued=1)

    active.release()
    assert queued.wait_until_active(timeout_seconds=1) is queued
    queued.release()


def test_wait_timeout_atomically_releases_the_queue_reservation() -> None:
    gate = _gate()
    active = gate.reserve_prompt(client_request_id="active")
    queued = gate.reserve_prompt(client_request_id="queued")

    with pytest.raises(RuntimeLeaseTimeoutError):
        queued.wait_until_active(timeout_seconds=0.01)

    assert queued.state == "timed_out"
    _assert_counts(gate, active=1, queued=0)
    active.release()


def test_auth_mutation_conflicts_with_active_and_queued_prompt_owners() -> None:
    gate = _gate()
    active = gate.reserve_prompt(client_request_id="active")
    queued = gate.reserve_prompt(client_request_id="queued")

    with pytest.raises(RuntimeMutationConflictError):
        gate.acquire_auth_mutation()

    _assert_counts(gate, active=1, queued=1)
    queued.release()
    active.release()


def test_new_prompt_fails_clearly_while_authentication_is_changing() -> None:
    gate = _gate()
    auth = gate.acquire_auth_mutation()

    with pytest.raises(RuntimeMutationConflictError):
        gate.reserve_prompt(client_request_id="blocked-by-auth")

    _assert_counts(gate, active=0, queued=0, auth=True)
    auth.release()


def test_auth_and_prompt_race_has_exactly_one_owner_without_toctou() -> None:
    gate = _gate()
    barrier = Barrier(2)

    def reserve_prompt() -> RuntimeLease | RuntimeMutationConflictError:
        barrier.wait()
        try:
            return gate.reserve_prompt(client_request_id="racing-prompt")
        except RuntimeMutationConflictError as error:
            return error

    def reserve_auth() -> RuntimeLease | RuntimeMutationConflictError:
        barrier.wait()
        try:
            return gate.acquire_auth_mutation()
        except RuntimeMutationConflictError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        prompt_future = executor.submit(reserve_prompt)
        auth_future = executor.submit(reserve_auth)
        outcomes = (prompt_future.result(), auth_future.result())

    owners = [outcome for outcome in outcomes if isinstance(outcome, RuntimeLease)]
    conflicts = [
        outcome
        for outcome in outcomes
        if isinstance(outcome, RuntimeMutationConflictError)
    ]
    assert len(owners) == 1
    assert len(conflicts) == 1
    snapshot = gate.snapshot()
    assert (snapshot.active_turns == 1) ^ snapshot.auth_mutation_active
    assert snapshot.queued_prompts == 0
    owners[0].release()


def test_close_rejects_new_owners_and_wakes_queued_waiters() -> None:
    gate = _gate()
    active = gate.reserve_prompt(client_request_id="active")
    queued = gate.reserve_prompt(client_request_id="queued")
    waiting = Event()

    def wait_for_turn() -> None:
        waiting.set()
        with pytest.raises(RuntimeGateClosedError):
            queued.wait_until_active(timeout_seconds=5)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(wait_for_turn)
        assert waiting.wait(1)
        gate.close()
        future.result(timeout=1)

    with pytest.raises(RuntimeGateClosedError):
        gate.reserve_prompt(client_request_id="after-close")
    with pytest.raises(RuntimeGateClosedError):
        gate.acquire_auth_mutation()
    _assert_counts(gate, active=1, queued=0, closed=True)
    active.release()
    _assert_counts(gate, active=0, queued=0, closed=True)


def test_release_cancel_and_close_are_thread_safe_and_idempotent() -> None:
    gate = _gate()
    lease = gate.reserve_prompt(client_request_id="single-owner")

    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(lambda _index: lease.release(), range(64)))
        list(executor.map(lambda _index: lease.cancel(), range(64)))
        list(executor.map(lambda _index: gate.close(), range(64)))

    assert lease.state == "released"
    _assert_counts(gate, active=0, queued=0, closed=True)


def test_snapshot_exposes_counts_only_and_never_owner_identifiers() -> None:
    gate = _gate()
    secret = "client-request-private-bearer-secret"
    lease = gate.reserve_prompt(client_request_id=secret)

    snapshot = gate.snapshot()

    assert snapshot.active_turns == 1
    assert secret not in repr(snapshot)
    assert "request" not in repr(snapshot).lower()
    lease.release()


@dataclass(frozen=True, slots=True)
class _BlockedReply:
    value: Any
    entered: Event
    release: Event


class _AuthAppServer:
    def __init__(self) -> None:
        self.generation = 1
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
        del params, timeout_seconds
        with self._lock:
            if not self._replies[method]:
                raise AssertionError(f"no scripted reply for {method}")
            reply = self._replies[method].popleft()
        if isinstance(reply, _BlockedReply):
            reply.entered.set()
            if not reply.release.wait(5):
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
        self.handlers[method] = handler

    def emit(self, method: str, params: Any) -> None:
        self.handlers[method](
            AppServerNotification(
                method=method,
                params=deepcopy(params),
                generation=self.generation,
            )
        )


def _signed_out_account() -> dict[str, Any]:
    return {"account": None, "requiresOpenaiAuth": True}


def _chatgpt_account() -> dict[str, Any]:
    return {
        "account": {"type": "chatgpt", "planType": "plus"},
        "requiresOpenaiAuth": True,
    }


def test_auth_coordinator_holds_mutation_lease_until_login_and_logout_terminal() -> (
    None
):
    gate = _gate()
    client = _AuthAppServer()
    client.script("account/read", _signed_out_account())
    client.script(
        "account/login/start",
        {
            "type": "chatgptDeviceCode",
            "loginId": "login-correlation-private",
            "userCode": "ABCD-EFGH",
            "verificationUrl": "https://auth.openai.com/codex/device",
        },
    )
    client.script("account/read", _chatgpt_account())
    logout_entered = Event()
    release_logout = Event()
    client.script(
        "account/logout",
        _BlockedReply({}, logout_entered, release_logout),
    )
    client.script("account/read", _signed_out_account())
    coordinator = CodexAuthCoordinator(client, runtime_gate=gate)
    coordinator.start()

    login = coordinator.start_device_login()

    assert login.state == "login_running"
    _assert_counts(gate, active=0, queued=0, auth=True)
    with pytest.raises(RuntimeMutationConflictError):
        gate.reserve_prompt(client_request_id="run-during-login")

    client.emit(
        "account/login/completed",
        {
            "loginId": "login-correlation-private",
            "success": True,
            "error": None,
        },
    )

    assert coordinator.status().state == "ok"
    _assert_counts(gate, active=0, queued=0)

    with ThreadPoolExecutor(max_workers=1) as executor:
        logout = executor.submit(coordinator.logout)
        assert logout_entered.wait(1)
        _assert_counts(gate, active=0, queued=0, auth=True)
        release_logout.set()
        assert logout.result(timeout=1).state == "logged_out"

    _assert_counts(gate, active=0, queued=0)
