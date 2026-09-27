"""Bounded reported-usage accounting; no provider polling or execution owner.

The broker supplies verified identities and native turn correlation. This ledger
never treats the last-response snapshot as a delta, or reported tokens as billing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .workspace import WorkspaceBoundary, WorkspaceBoundaryError, WorkspaceNotFoundError

MAX_RUNS = 1024
MAX_SCOPES = 2048
MAX_BYTES = 4 * 1024 * 1024
MAX_COUNTER = 9_007_199_254_740_991
TERMINAL = frozenset({"completed", "cancelled", "failed", "interrupted"})


class UsageHistoryError(RuntimeError):
    """Private persistence or correlation failure; do not expose raw contents."""


class DurationBudget(BaseModel):
    """Elapsed execution limit, independent of lagged token telemetry."""

    model_config = ConfigDict(extra="forbid")
    max_duration_seconds: int = Field(strict=True, ge=1, le=86_400)

    def deadline(self, started_at: str) -> str:
        return (_time(started_at) + timedelta(seconds=self.max_duration_seconds)).isoformat()

    def reached(self, elapsed_seconds: float) -> bool:
        return elapsed_seconds >= self.max_duration_seconds


class TokenBreakdown(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    total_tokens: int = Field(alias="totalTokens", strict=True, ge=0, le=MAX_COUNTER)
    input_tokens: int = Field(alias="inputTokens", strict=True, ge=0, le=MAX_COUNTER)
    cached_input_tokens: int = Field(alias="cachedInputTokens", strict=True, ge=0, le=MAX_COUNTER)
    output_tokens: int = Field(alias="outputTokens", strict=True, ge=0, le=MAX_COUNTER)
    reasoning_output_tokens: int = Field(alias="reasoningOutputTokens", strict=True, ge=0, le=MAX_COUNTER)
    cache_write_input_tokens: int = Field(default=0, alias="cacheWriteInputTokens", strict=True, ge=0, le=MAX_COUNTER)


class UsageRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1, max_length=128)
    thread_id: str = Field(min_length=1, max_length=128)
    project_id: str | None = Field(default=None, max_length=128)
    # Private verified marker, not a selectable/bound saved-account runtime.
    account_marker: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$", repr=False)
    account_label: str = Field(default="Account not reported", max_length=160)
    native_thread_id: str = Field(min_length=1, max_length=256, repr=False)
    native_turn_id: str = Field(min_length=1, max_length=256, repr=False)
    started_at: str = Field(max_length=64)
    finished_at: str | None = Field(default=None, max_length=64)
    status: Literal["running", "completed", "cancelled", "failed", "interrupted"] = "running"
    reported_tokens: int | None = Field(default=None, strict=True, ge=0, le=MAX_COUNTER)
    reported_thread_tokens: int | None = Field(default=None, strict=True, ge=0, le=MAX_COUNTER)
    coverage: Literal["not_reported", "partial", "reported"] = "not_reported"
    observed_at: str | None = Field(default=None, max_length=64)
    budget: DurationBudget | None = None
    budget_stop_requested_at: str | None = Field(default=None, max_length=64)

    @field_validator("started_at", "finished_at", "observed_at", "budget_stop_requested_at")
    @classmethod
    def valid_time(cls, value: str | None) -> str | None:
        if value is not None:
            _time(value)
        return value


class _Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    watermark: TokenBreakdown | None = None
    continuous: bool = False


class _State(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    runs: dict[str, UsageRun] = Field(default_factory=dict)
    scopes: dict[str, _Scope] = Field(default_factory=dict)
    evicted_runs: int = Field(default=0, ge=0)
    accounts: dict[str, int] = Field(default_factory=dict)


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("usage timestamps require a time zone")
    return result.astimezone(UTC)


def _scope_key(run: UsageRun) -> str:
    # Unknown identities are isolated per run, never an account aggregate.
    return f"{run.account_marker or run.run_id}:{run.native_thread_id}"


class UsageHistory:
    """Single broker-owned ledger with atomic, bounded private persistence.

    Mutations are copy-on-write: failed writes do not advance memory watermarks.
    Native IDs, provider markers and prompts never enter the public projection.
    """

    def __init__(self, root: Path | str, *, max_runs: int = MAX_RUNS) -> None:
        if not 1 <= max_runs <= MAX_RUNS:
            raise ValueError("invalid usage retention")
        self._max_runs = max_runs
        self._boundary = WorkspaceBoundary(root, create=True)
        self._lock = RLock()
        try:
            with self._boundary.open_regular_file("usage-history.json") as stream:
                payload = stream.read(MAX_BYTES + 1)
            if len(payload) > MAX_BYTES:
                raise UsageHistoryError("Usage history exceeds its limit.")
            self._state = _State.model_validate_json(payload)
            if len(self._state.runs) > MAX_RUNS or len(self._state.scopes) > MAX_SCOPES or len(self._state.accounts) > MAX_SCOPES:
                raise UsageHistoryError("Usage history exceeds its limit.")
        except WorkspaceNotFoundError:
            self._state = _State()
        except UsageHistoryError:
            self._boundary.close()
            raise
        except (ValidationError, WorkspaceBoundaryError, OSError, ValueError):
            self._boundary.close()
            raise UsageHistoryError("Usage history could not be read.") from None

    def _commit(self, state: _State) -> None:
        terminal = sorted(
            (row for row in state.runs.values() if row.status in TERMINAL),
            key=lambda row: (_time(row.started_at), row.run_id),
        )
        while len(state.runs) > self._max_runs and terminal:
            del state.runs[terminal.pop(0).run_id]
            state.evicted_runs += 1
        if len(state.runs) > self._max_runs:
            raise UsageHistoryError("Usage history has too many active runs.")
        # Old scope eviction makes the next resumed meter partial, never zero.
        for key in list(state.scopes):
            if len(state.scopes) <= MAX_SCOPES:
                break
            if state.scopes[key].run_id not in state.runs:
                del state.scopes[key]
        if len(state.scopes) > MAX_SCOPES:
            raise UsageHistoryError("Usage history has too many active meters.")
        payload = state.model_dump_json().encode("utf-8")
        if len(payload) > MAX_BYTES:
            raise UsageHistoryError("Usage history exceeds its limit.")
        self._boundary.atomic_write_bytes("usage-history.json", payload)
        self._state = state

    def start(self, run: UsageRun, *, fresh_native_thread: bool = False) -> None:
        _time(run.started_at)
        with self._lock:
            existing = self._state.runs.get(run.run_id)
            if existing is not None:
                # Replayed admission cannot replace identity or reset a budget.
                if any(getattr(existing, key) != getattr(run, key) for key in (
                    "thread_id", "project_id", "account_marker", "native_thread_id",
                    "native_turn_id", "started_at", "budget",
                )):
                    raise UsageHistoryError("Usage run identity changed.")
                return
            state = self._state.model_copy(deep=True)
            state.runs[run.run_id] = run.model_copy(deep=True)
            if run.account_marker is not None:
                if run.account_marker not in state.accounts:
                    if len(state.accounts) >= MAX_SCOPES:
                        raise UsageHistoryError("Usage history has too many account identities.")
                    state.accounts[run.account_marker] = len(state.accounts) + 1
                if run.account_label == "Account not reported":
                    state.runs[run.run_id].account_label = f"Account {state.accounts[run.account_marker]}"
            key = _scope_key(run)
            previous = state.scopes.get(key)
            if previous and previous.run_id in state.runs:
                if state.runs[previous.run_id].status not in TERMINAL:
                    raise UsageHistoryError("Usage meter already has an active owner.")
            if fresh_native_thread and previous is not None:
                raise UsageHistoryError("Existing native thread cannot start at zero.")
            state.scopes[key] = _Scope(
                run_id=run.run_id,
                watermark=(previous.watermark if previous else None),
                continuous=bool(fresh_native_thread and run.account_marker),
            )
            if fresh_native_thread:
                state.scopes[key].watermark = TokenBreakdown(
                    totalTokens=0, inputTokens=0, cachedInputTokens=0,
                    outputTokens=0, reasoningOutputTokens=0,
                )
            self._commit(state)

    def observe(self, run_id: str, *, native_thread_id: str, native_turn_id: str,
                token_usage: object, observed_at: str) -> bool:
        """Accept a correlated cumulative snapshot, never sum `last` readings."""
        _time(observed_at)
        with self._lock:
            run = self._state.runs.get(run_id)
            if run is None or (run.native_thread_id, run.native_turn_id) != (
                native_thread_id, native_turn_id,
            ):
                return False
            scope = self._state.scopes.get(_scope_key(run))
            if scope is None or scope.run_id != run_id:
                # Historical replay cannot advance a newer turn's watermark.
                return False
            state = self._state.model_copy(deep=True)
            row = state.runs[run_id]
            meter = state.scopes[_scope_key(run)]
            try:
                if not isinstance(token_usage, dict):
                    raise ValueError()
                total = TokenBreakdown.model_validate(token_usage.get("total"))
            except (ValidationError, ValueError, TypeError):
                row.coverage = "partial" if row.reported_tokens is not None else "not_reported"
                meter.continuous = False
                self._commit(state)
                return False
            if row.observed_at and _time(observed_at) < _time(row.observed_at):
                return False
            previous = meter.watermark
            row.reported_thread_tokens = total.total_tokens
            if total.total_tokens != total.input_tokens + total.output_tokens:
                # Native context-fill snapshots can synthesise only totalTokens.
                # Retain the labelled snapshot, never claim measured consumption.
                meter.continuous = False
                row.coverage = "partial"
                self._commit(state)
                return False
            if previous is not None:
                if total == previous:
                    if row.reported_tokens is None and meter.continuous and total.total_tokens == 0:
                        row.reported_tokens = 0
                        row.observed_at = observed_at
                        row.coverage = "reported"
                        self._commit(state)
                        return True
                    return False
                if any(getattr(total, key) < getattr(previous, key)
                       for key in TokenBreakdown.model_fields):
                    # Could be reorder, reset or context synthesis: no invented delta.
                    row.coverage = "partial" if row.reported_tokens is not None else "not_reported"
                    meter.continuous = False
                    self._commit(state)
                    return False
                delta = total.total_tokens - previous.total_tokens
                row.reported_tokens = (row.reported_tokens or 0) + delta
                if row.reported_tokens > MAX_COUNTER:
                    raise UsageHistoryError("Usage counter exceeds its limit.")
            # The first resumed snapshot establishes a baseline, not zero usage.
            meter.watermark = total
            row.observed_at = observed_at
            row.coverage = ("reported" if meter.continuous else "partial") if row.reported_tokens is not None else "not_reported"
            self._commit(state)
            return True

    def finish(self, run_id: str, *, status: str, finished_at: str) -> None:
        if status not in TERMINAL:
            raise ValueError("invalid usage terminal state")
        _time(finished_at)
        with self._lock:
            run = self._state.runs.get(run_id)
            if run is None or run.status in TERMINAL:
                return
            if _time(finished_at) < _time(run.started_at):
                raise ValueError("usage finish precedes start")
            state = self._state.model_copy(deep=True)
            state.runs[run_id].status = status
            state.runs[run_id].finished_at = finished_at
            self._commit(state)

    def request_budget_stop(self, run_id: str, *, elapsed_seconds: float,
                            requested_at: str) -> bool:
        """Durably latch one stop intent; the broker must perform the stop.

        The broker recovers an existing intent after restart. This method never
        starts a timer, cancels siblings, clears queues or retries provider work.
        """
        _time(requested_at)
        with self._lock:
            row = self._state.runs.get(run_id)
            if row is None or row.status in TERMINAL or row.budget is None:
                return False
            if row.budget_stop_requested_at or not row.budget.reached(elapsed_seconds):
                return False
            state = self._state.model_copy(deep=True)
            state.runs[run_id].budget_stop_requested_at = requested_at
            self._commit(state)
            return True

    def pending_budget_stops(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(row.run_id for row in self._state.runs.values()
                         if row.budget_stop_requested_at and row.status not in TERMINAL)

    def view(self, *, thread_id: str | None = None,
             project_id: str | None = None) -> dict[str, object]:
        with self._lock:
            rows = sorted((row for row in self._state.runs.values()
                           if (thread_id is None or row.thread_id == thread_id)
                           and (project_id is None or row.project_id == project_id)),
                          key=lambda row: (_time(row.started_at), row.run_id), reverse=True)
            items = []
            for row in rows:
                item = row.model_dump(exclude={"account_marker", "native_thread_id", "native_turn_id"})
                item["duration_seconds"] = (
                    (_time(row.finished_at) - _time(row.started_at)).total_seconds()
                    if row.finished_at else None
                )
                item["account_group"] = self._state.accounts.get(row.account_marker)
                items.append(item)
            # A known subtotal is not a complete total when any meter is missing.
            known = [row.reported_tokens for row in rows if row.reported_tokens is not None]
            return {
                "items": items, "known_reported_tokens": sum(known) if known else None,
                "coverage": "reported" if rows and all(row.coverage == "reported" for row in rows) else "partial" if known else "not_reported",
                "retention_limit": self._max_runs, "evicted_runs": self._state.evicted_runs,
                "oldest_started_at": rows[-1].started_at if rows else None,
                "billing": "not_reported", "token_limit_supported": False,
            }

    def close(self) -> None:
        self._boundary.close()
