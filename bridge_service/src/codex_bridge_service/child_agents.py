"""Private, bounded projection of genuine runtime children.

The broker supplies an authorised parent scope while holding its lifecycle lock.
This module never submits parent prompts, creates children or aborts a runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .activity_display import command_preview

MAX_CHILDREN = 256
MAX_PARENT_CHILDREN = 64
MAX_STATE_BYTES = 4 * 1024 * 1024
TERMINAL = frozenset({"completed", "errored", "shutdown", "notFound", "interrupted"})
ChildStatus = Literal[
    "pendingInit", "running", "interrupted", "completed", "errored", "shutdown", "notFound"
]


class ChildAgentError(RuntimeError):
    """Fixed public errors; never reflect a native error or identifier."""

    def __init__(self, code: str = "subagents_unavailable") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ChildOwner:
    thread_id: str
    run_id: str
    native_parent: str
    workspace: str
    account: str | None
    generation: int
    active: bool


class ChildRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    child_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    thread_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    native_parent: str = Field(min_length=1, max_length=256, repr=False)
    native_child: str = Field(min_length=1, max_length=256, repr=False)
    workspace: str = Field(min_length=1, max_length=4096, repr=False)
    account: str | None = Field(default=None, max_length=128, repr=False)
    generation: int = Field(ge=1)
    epoch: str = Field(pattern=r"^[a-f0-9]{32}$", repr=False)
    revision: int = Field(default=1, ge=1)
    status: ChildStatus = "pendingInit"
    task: str | None = Field(default=None, max_length=2000)
    result: str | None = Field(default=None, max_length=2000)
    label: str | None = Field(default=None, max_length=2000)
    turn_id: str | None = Field(default=None, max_length=256, repr=False)
    verified: bool = False
    stop_pending: bool = False
    updated_at: str = Field(max_length=64)
    # Only signatures of bounded projected values; no raw provider content.
    seen: list[str] = Field(default_factory=list, max_length=32)


class StopReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    thread_id: str = Field(min_length=1, max_length=128)
    outcome: Literal["accepted", "unknown"]


class ChildState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    children: list[ChildRecord] = Field(default_factory=list, max_length=MAX_CHILDREN)
    stops: dict[str, StopReceipt] = Field(default_factory=dict, max_length=256)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(value: object) -> str | None:
    if (not isinstance(value, str) or not value or len(value) > 256
            or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        return None
    return value


def _display(value: object) -> str | None:
    # Reuse the existing conservative credential/bidi filter. Plain text only.
    preview = command_preview(value)
    return preview.encode("utf-8")[:2000].decode("utf-8", errors="ignore") if preview else None


class ChildAgents:
    def __init__(self, state_root: Path, app_server: Any, *, timeout: float = 5,
                 request: Callable[..., Any] | None = None,
                 active_authority: Callable[[ChildOwner], bool] | None = None) -> None:
        self.path = state_root / "child-agents.sqlite3"
        self.app_server = app_server
        self.timeout = timeout
        self._request = request or self._direct_request
        self._active_authority = active_authority or (
            lambda owner: owner.active and app_server.ready
            and app_server.generation == owner.generation
        )
        self.epoch = uuid4().hex
        self.available = True
        self.state = ChildState()
        try:
            if self.path.is_symlink():
                raise OSError("unsafe private state")
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.path, flags, 0o600)
            with os.fdopen(fd, "rb") as stream:
                if (not stat.S_ISREG(os.fstat(stream.fileno()).st_mode)
                        or os.fstat(stream.fileno()).st_size > 8 * 1024 * 1024):
                    raise OSError("invalid private state")
            with self._connection() as connection:
                connection.execute("CREATE TABLE IF NOT EXISTS child_state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
                saved = connection.execute("SELECT payload FROM child_state WHERE id=1").fetchone()
                if saved is not None:
                    if len(saved[0].encode("utf-8")) > MAX_STATE_BYTES:
                        raise ValueError("invalid private state")
                    self.state = ChildState.model_validate_json(saved[0])
        except (OSError, ValueError, ValidationError, sqlite3.Error):
            self.available = False

    def _direct_request(self, method: str, params: dict[str, Any], *,
                        owner: ChildOwner, expected_revision: int) -> Any:
        return self.app_server.request(method, params, timeout_seconds=self.timeout)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if self.path.is_symlink():
            raise ChildAgentError()
        connection = sqlite3.connect(self.path, timeout=2)
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            connection.execute(f"PRAGMA max_page_count={8 * 1024 * 1024 // page_size}")
            with connection:
                yield connection
        finally:
            connection.close()

    def _save(self) -> None:
        if not self.available:
            raise ChildAgentError()
        raw = self.state.model_dump_json().encode("utf-8")
        if len(raw) > MAX_STATE_BYTES or self.path.is_symlink():
            self.available = False
            raise ChildAgentError()
        try:
            with self._connection() as connection:
                connection.execute("INSERT INTO child_state(id,payload) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (raw.decode("utf-8"),))
        except (OSError, sqlite3.Error):
            self.available = False
            raise ChildAgentError() from None

    def observe(self, owner: ChildOwner, item: dict[str, Any]) -> None:
        """Called only for a correlated completed parent item, never child prose."""
        if (not self.available or item.get("type") != "collabAgentToolCall"
                or item.get("senderThreadId") != owner.native_parent
                or item.get("status") != "completed" or not _id(item.get("id"))):
            return
        receivers = item.get("receiverThreadIds")
        states = item.get("agentsStates")
        if not isinstance(receivers, list) or not isinstance(states, dict):
            return
        changed = False
        for native in receivers[:MAX_PARENT_CHILDREN]:
            if not _id(native) or native == owner.native_parent:
                continue
            row = next((r for r in self.state.children
                        if r.native_child == native and r.thread_id == owner.thread_id), None)
            if row is None:
                if any(r.native_child == native for r in self.state.children):
                    continue
                if item.get("tool") != "spawnAgent":
                    continue
                # Never evict active rows to accept an unbounded native fan-out.
                if sum(r.thread_id == owner.thread_id for r in self.state.children) >= MAX_PARENT_CHILDREN:
                    continue
                if len(self.state.children) >= MAX_CHILDREN:
                    old = next((r for r in self.state.children if r.status in TERMINAL), None)
                    if old is None:
                        continue
                    self.state.children.remove(old)
                row = ChildRecord(
                    child_id=uuid4().hex, thread_id=owner.thread_id, run_id=owner.run_id,
                    native_parent=owner.native_parent, native_child=native,
                    workspace=owner.workspace, account=owner.account,
                    generation=owner.generation, epoch=self.epoch, updated_at=_now(),
                )
                self.state.children.append(row)
                changed = True
            if (row.native_parent != owner.native_parent or row.workspace != owner.workspace
                    or row.account != owner.account):
                continue
            state = states.get(native)
            status = state.get("status") if isinstance(state, dict) else None
            if not isinstance(status, str) or status not in {"pendingInit", "running", *TERMINAL}:
                continue
            task = _display(item.get("prompt")) if item.get("tool") == "spawnAgent" else row.task
            result = _display(state.get("message")) if status in TERMINAL else None
            signature = hashlib.sha256(json.dumps(
                [item["id"], status, task, result], ensure_ascii=False,
            ).encode()).hexdigest()
            if signature in row.seen:
                continue
            # Late/replayed non-terminal snapshots cannot resurrect a ended task.
            if row.status in TERMINAL:
                if (row.run_id != owner.run_id or row.generation != owner.generation
                        or row.epoch != self.epoch):
                    # Fresh correlated parent evidence may rebind inspection,
                    # while preserving terminal labels until a native read.
                    row.run_id = owner.run_id
                    row.generation = owner.generation
                    row.epoch = self.epoch
                    row.verified = False
                    row.turn_id = None
                    row.revision += 1
                    changed = True
                continue
            row.seen = [*row.seen[-31:], signature]
            row.run_id = owner.run_id
            row.generation = owner.generation
            row.epoch = self.epoch
            row.status = status
            row.task = task or row.task
            row.result = result or row.result
            row.verified = False
            row.turn_id = None
            row.revision += 1
            row.updated_at = _now()
            changed = True
        if changed:
            self._save()

    def _matches(self, row: ChildRecord, owner: ChildOwner) -> bool:
        return (row.thread_id == owner.thread_id and row.run_id == owner.run_id
                and row.native_parent == owner.native_parent and row.workspace == owner.workspace
                and row.account is not None and row.account == owner.account
                and row.generation == owner.generation and row.epoch == self.epoch)

    def _row(self, owner: ChildOwner, child_id: str) -> ChildRecord:
        if not self.available:
            raise ChildAgentError()
        row = next((r for r in self.state.children
                    if r.child_id == child_id and r.thread_id == owner.thread_id), None)
        if row is None:
            raise ChildAgentError("child_not_found")
        return row

    def public(self, row: ChildRecord, owner: ChildOwner | None) -> dict[str, Any]:
        current = (owner is not None and self._matches(row, owner)
                   and self.app_server.generation == owner.generation and self.app_server.ready)
        stale = not (current and row.verified and (owner.active or row.status in TERMINAL))
        can_stop = bool(current and owner.active and row.verified and row.turn_id
                        and row.status == "running" and not row.stop_pending)
        return {
            "child_id": row.child_id, "revision": row.revision,
            "label": row.label or "Subagent", "task": row.task, "result": row.result,
            "status": row.status, "stale": stale, "updated_at": row.updated_at,
            "stop_pending": row.stop_pending, "can_stop": can_stop,
            "can_follow_up": False,
            "follow_up_reason": "The pinned runtime does not support verified direct child follow-up.",
        }

    def list(self, thread_id: str, owner: ChildOwner | None) -> dict[str, Any]:
        if not self.available:
            raise ChildAgentError()
        return {"children": [self.public(row, owner) for row in self.state.children
                             if row.thread_id == thread_id],
                "limit": MAX_PARENT_CHILDREN}

    def purge(self, thread_ids: set[str]) -> None:
        """Called by the broker's existing authorised chat/project deletion."""
        if not self.available:
            raise ChildAgentError()
        remaining = [row for row in self.state.children if row.thread_id not in thread_ids]
        receipts = {key: value for key, value in self.state.stops.items()
                    if value.thread_id not in thread_ids}
        if len(remaining) == len(self.state.children) and len(receipts) == len(self.state.stops):
            return
        self.state.children = remaining
        self.state.stops = receipts
        self._save()

    def refresh(self, owner: ChildOwner, child_id: str) -> dict[str, Any]:
        row = self._row(owner, child_id)
        # A restart is always stale; revalidation is possible only under the same
        # live authoritative parent run/account/generation, never an arbitrary ID.
        if not self._matches(row, owner):
            raise ChildAgentError("child_stale")
        row.verified = False
        row.turn_id = None
        try:
            response = self._request(
                "thread/read", {"threadId": row.native_child, "includeTurns": True},
                owner=owner, expected_revision=row.revision,
            )
            if self.app_server.generation != owner.generation or not self.app_server.ready:
                raise ChildAgentError("child_stale")
            thread = response.get("thread") if isinstance(response, dict) else None
            if not isinstance(thread, dict):
                raise ChildAgentError()
            source = thread.get("source")
            subagent = source.get("subAgent") if isinstance(source, dict) else None
            spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
            source_parent = spawn.get("parent_thread_id") if isinstance(spawn, dict) else None
            explicit_parent = thread.get("parentThreadId")
            if (thread.get("id") != row.native_child or thread.get("cwd") != owner.workspace
                    or not isinstance(spawn, dict) or source_parent != owner.native_parent
                    or (explicit_parent or source_parent) != owner.native_parent
                    or (source_parent is not None and source_parent != owner.native_parent)
                    or (explicit_parent is not None and explicit_parent != owner.native_parent)):
                raise ChildAgentError("child_ownership_conflict")
            turns = thread.get("turns")
            if not isinstance(turns, list) or len(turns) > 4096:
                raise ChildAgentError()
            active = [t for t in turns if isinstance(t, dict) and t.get("status") == "inProgress"]
            if len(active) > 1:
                raise ChildAgentError()
            row.label = _display(thread.get("agentNickname")) or _display(thread.get("agentRole"))
            if active:
                row.turn_id = _id(active[0].get("id"))
                if row.turn_id is None:
                    raise ChildAgentError()
                row.status = "running"
            elif turns:
                last = turns[-1]
                status = last.get("status") if isinstance(last, dict) else None
                mapped = {"completed": "completed", "failed": "errored", "interrupted": "interrupted"}
                if isinstance(status, str) and status in mapped:
                    row.status = mapped[status]
                # Only public assistant final messages; never reasoning/tool items.
                items = last.get("items") if isinstance(last, dict) else None
                if isinstance(items, list):
                    finals = [_display(i.get("text")) for i in items[-256:]
                              if isinstance(i, dict) and i.get("type") == "agentMessage"
                              and i.get("phase") == "final_answer"]
                    row.result = next((s for s in reversed(finals) if s), row.result)
            row.verified = True
            if row.status in TERMINAL:
                row.stop_pending = False
        except ChildAgentError:
            row.revision += 1
            self._save()
            raise
        except Exception:
            row.revision += 1
            self._save()
            raise ChildAgentError() from None
        row.revision += 1
        row.updated_at = _now()
        self._save()
        return self.public(row, owner)

    def stop(self, owner: ChildOwner, child_id: str, revision: int,
             client_request_id: str) -> dict[str, Any]:
        row = self._row(owner, child_id)
        if not _id(client_request_id):
            raise ChildAgentError("invalid_child_request")
        key = hashlib.sha256(json.dumps([owner.thread_id, client_request_id]).encode()).hexdigest()
        fingerprint = hashlib.sha256(json.dumps([child_id, revision]).encode()).hexdigest()
        receipt = self.state.stops.get(key)
        if receipt is not None:
            if receipt.fingerprint != fingerprint:
                raise ChildAgentError("child_request_conflict")
            return {"outcome": receipt.outcome, "child": self.public(row, owner)}
        if not self._matches(row, owner) or not owner.active:
            raise ChildAgentError("child_stale")
        if row.revision != revision:
            raise ChildAgentError("child_revision_conflict")
        expected_turn = row.turn_id
        if not self.public(row, owner)["can_stop"]:
            raise ChildAgentError("child_stop_unavailable")
        self.refresh(owner, child_id)
        if not self._active_authority(owner):
            raise ChildAgentError("child_stale")
        # Do not stop a new task if the old child completed while the user clicked.
        if row.turn_id != expected_turn or not self.public(row, owner)["can_stop"]:
            raise ChildAgentError("child_turn_changed")
        if len(self.state.stops) >= 256:
            expired = next((key for key, value in self.state.stops.items()
                            if value.outcome == "accepted"), None)
            if expired is None:
                raise ChildAgentError("child_request_capacity")
            del self.state.stops[expired]
        # Persist before dispatch. Uncertain outcomes are never blindly replayed.
        self.state.stops[key] = StopReceipt(fingerprint=fingerprint, thread_id=owner.thread_id,
                                          outcome="unknown")
        row.stop_pending = True
        row.revision += 1
        self._save()
        try:
            self._request(
                "turn/interrupt", {"threadId": row.native_child, "turnId": expected_turn},
                owner=owner, expected_revision=row.revision,
            )
        except Exception:
            return {"outcome": "unknown", "code": "child_control_uncertain", "child": self.public(row, owner)}
        self.state.stops[key].outcome = "accepted"
        self._save()
        return {"outcome": "accepted", "child": self.public(row, owner)}

    def follow_up(self, owner: ChildOwner, child_id: str) -> None:
        self._row(owner, child_id)
        # No runtime request, no parent prompt, no implicit child creation.
        raise ChildAgentError("child_follow_up_unavailable")
