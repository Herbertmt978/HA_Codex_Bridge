from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import isfinite
from threading import Condition, Lock
from time import monotonic
from typing import Literal

from .resource_limits import ResourceLimits

LeaseState = Literal[
    "active",
    "queued",
    "released",
    "cancelled",
    "timed_out",
    "closed",
]


class RuntimeGateError(RuntimeError):
    pass


class RuntimeGateClosedError(RuntimeGateError):
    def __init__(self) -> None:
        super().__init__("The Codex runtime gate is closed.")


class RuntimeQueueFullError(RuntimeGateError):
    def __init__(self) -> None:
        super().__init__("The Codex prompt queue is full.")


class RuntimeMutationConflictError(RuntimeGateError):
    def __init__(self) -> None:
        super().__init__("Codex authentication and runs cannot change concurrently.")


class RuntimeLeaseCancelledError(RuntimeGateError):
    def __init__(self) -> None:
        super().__init__("The queued Codex prompt was cancelled.")


class RuntimeLeaseTimeoutError(RuntimeGateError):
    def __init__(self) -> None:
        super().__init__("The queued Codex prompt timed out.")


@dataclass(frozen=True, slots=True)
class RuntimeGateSnapshot:
    active_turns: int
    queued_prompts: int
    auth_mutation_active: bool
    closed: bool


class RuntimeLease:
    def __init__(
        self,
        gate: RuntimeGate,
        *,
        kind: Literal["prompt", "auth"],
        state: LeaseState,
        owner_key: str | None = None,
    ) -> None:
        self._gate = gate
        self._kind = kind
        self._state = state
        self._owner_key = owner_key

    @property
    def state(self) -> LeaseState:
        with self._gate._condition:
            return self._state

    def wait_until_active(self, *, timeout_seconds: float) -> RuntimeLease:
        timeout = _nonnegative_timeout(timeout_seconds)
        deadline = monotonic() + timeout
        with self._gate._condition:
            while True:
                if self._state == "active":
                    return self
                if self._state == "cancelled":
                    raise RuntimeLeaseCancelledError()
                if self._state == "timed_out":
                    raise RuntimeLeaseTimeoutError()
                if self._state == "closed":
                    raise RuntimeGateClosedError()
                if self._state == "released":
                    raise RuntimeLeaseCancelledError()
                remaining = deadline - monotonic()
                if remaining <= 0:
                    self._gate._finish_locked(self, "timed_out")
                    raise RuntimeLeaseTimeoutError()
                self._gate._condition.wait(remaining)

    def release(self) -> None:
        self._gate._finish(self, "released")

    def cancel(self) -> None:
        # An active turn remains the global owner until Codex confirms its
        # terminal notification (or the owning generation is aborted).  A
        # cancellation request alone must never promote another turn.
        with self._gate._condition:
            if self._state == "active":
                return
        self._gate._finish(self, "cancelled")


class RuntimeGate:
    """Atomic owner of HA prompt capacity and authentication exclusion."""

    def __init__(self, *, limits: ResourceLimits) -> None:
        if not isinstance(limits, ResourceLimits):
            raise TypeError("runtime gate requires immutable resource limits")
        self.limits = limits
        self._condition = Condition(Lock())
        self._active_prompts = 0
        self._prompt_leases: dict[str, RuntimeLease] = {}
        self._queue: deque[RuntimeLease] = deque()
        self._auth_lease: RuntimeLease | None = None
        self._closed = False

    def reserve_prompt(self, *, client_request_id: str) -> RuntimeLease:
        owner_key = _owner_key(client_request_id)
        with self._condition:
            if self._closed:
                raise RuntimeGateClosedError()
            if self._auth_lease is not None:
                raise RuntimeMutationConflictError()
            existing = self._prompt_leases.get(owner_key)
            if existing is not None:
                return existing
            if self._active_prompts < self.limits.max_active_turns:
                state: LeaseState = "active"
                self._active_prompts += 1
            else:
                if len(self._queue) >= self.limits.max_queued_prompts:
                    raise RuntimeQueueFullError()
                state = "queued"
            lease = RuntimeLease(
                self,
                kind="prompt",
                state=state,
                owner_key=owner_key,
            )
            self._prompt_leases[owner_key] = lease
            if state == "queued":
                self._queue.append(lease)
            return lease

    def acquire_auth_mutation(self) -> RuntimeLease:
        with self._condition:
            if self._closed:
                raise RuntimeGateClosedError()
            if self._auth_lease is not None or self._active_prompts > 0 or self._queue:
                raise RuntimeMutationConflictError()
            lease = RuntimeLease(self, kind="auth", state="active")
            self._auth_lease = lease
            return lease

    def snapshot(self) -> RuntimeGateSnapshot:
        with self._condition:
            return RuntimeGateSnapshot(
                active_turns=self._active_prompts,
                queued_prompts=len(self._queue),
                auth_mutation_active=self._auth_lease is not None,
                closed=self._closed,
            )

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            while self._queue:
                lease = self._queue.popleft()
                lease._state = "closed"
                if lease._owner_key is not None:
                    self._prompt_leases.pop(lease._owner_key, None)
            self._condition.notify_all()

    def _finish(self, lease: RuntimeLease, terminal_state: LeaseState) -> None:
        with self._condition:
            self._finish_locked(lease, terminal_state)

    def _finish_locked(
        self,
        lease: RuntimeLease,
        terminal_state: LeaseState,
    ) -> None:
        if terminal_state not in {"released", "cancelled", "timed_out"}:
            raise ValueError("invalid runtime lease terminal state")
        if lease._state in {"released", "cancelled", "timed_out", "closed"}:
            return
        previous = lease._state
        lease._state = terminal_state
        if lease._kind == "auth":
            if self._auth_lease is lease:
                self._auth_lease = None
        else:
            if lease._owner_key is not None:
                self._prompt_leases.pop(lease._owner_key, None)
            if previous == "active":
                self._active_prompts -= 1
            else:
                try:
                    self._queue.remove(lease)
                except ValueError:
                    pass
            self._promote_locked()
        self._condition.notify_all()

    def _promote_locked(self) -> None:
        if self._closed or self._auth_lease is not None:
            return
        while self._queue and self._active_prompts < self.limits.max_active_turns:
            lease = self._queue.popleft()
            if lease._state != "queued":
                continue
            lease._state = "active"
            self._active_prompts += 1


def _owner_key(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("client request id must be a nonblank trimmed string")
    if len(value.encode("utf-8")) > 256:
        raise ValueError("client request id exceeds its limit")
    return value


def _nonnegative_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("runtime lease timeout must be nonnegative")
    timeout = float(value)
    if not isfinite(timeout) or timeout < 0:
        raise ValueError("runtime lease timeout must be nonnegative")
    return timeout
