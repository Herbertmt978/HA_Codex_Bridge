"""Durable, manual-turn goals. This owner never schedules or starts model work."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Literal
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .event_store import EventDraft, OutboxWrite
from .models import RuntimeProfile

MAX_GOAL_BYTES = 32 * 1024
MAX_GOAL_STATE_BYTES = 512 * 1024
MAX_GOAL_HISTORY = 8
MAX_GOAL_ACTIONS = 64
GoalStatus = Literal["active", "paused", "completed", "cancelled"]


class GoalError(ValueError):
    code = "goal_conflict"


class GoalRevisionConflict(GoalError):
    code = "goal_revision_conflict"


class GoalUnavailable(GoalError):
    code = "goals_unavailable"


class GoalSnapshotStale(GoalError):
    code = "goal_context_stale"


class GoalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal_id: str = Field(pattern=r"^goal_[a-f0-9]{32}$")
    objective: str = Field(min_length=1, max_length=4096, strict=True)
    completion_criteria: list[str] = Field(min_length=1, max_length=16)
    progress: str = Field(default="", max_length=8192, strict=True)
    status: GoalStatus = "paused"
    activation_revision: int = Field(ge=1, strict=True)
    created_at: str
    updated_at: str

    @field_validator("objective")
    @classmethod
    def objective_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("objective must not be blank")
        return value

    @field_validator("completion_criteria")
    @classmethod
    def bounded_criteria(cls, values: list[str]) -> list[str]:
        if any(not isinstance(value, str) or not value.strip() or len(value) > 1024 for value in values):
            raise ValueError("completion criteria must be non-blank, bounded text")
        return values

    @model_validator(mode="after")
    def byte_bound(self) -> "GoalRecord":
        if len(json.dumps(self.model_dump(include=set(GoalRecord.model_fields)), ensure_ascii=False).encode("utf-8")) > MAX_GOAL_BYTES:
            raise ValueError("goal exceeds its text limit")
        return self


class GoalSnapshot(GoalRecord):
    """Immutable accepted context, including its activation fence and workspace."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    completion_criteria: tuple[str, ...] = Field(min_length=1, max_length=16)
    status: Literal["active"] = "active"
    workspace_path: str = Field(min_length=1, max_length=4096)
    project_id: str = Field(min_length=1, max_length=128)


class GoalView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(default=0, ge=0, strict=True)
    goal: GoalRecord | None = None
    history: list[GoalRecord] = Field(default_factory=list, max_length=MAX_GOAL_HISTORY)
    continuation: Literal["manual_turns_only"] = "manual_turns_only"


class GoalAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["create", "edit", "progress", "pause", "resume", "complete", "cancel"]
    expected_revision: int = Field(ge=0, strict=True)
    client_request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    objective: str | None = Field(default=None, min_length=1, max_length=4096, strict=True)
    completion_criteria: list[str] | None = Field(default=None, min_length=1, max_length=16)
    progress: str | None = Field(default=None, max_length=8192, strict=True)
    completion_confirmed: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def action_fields(self) -> "GoalAction":
        if self.action == "create" and (self.objective is None or self.completion_criteria is None):
            raise ValueError("create requires an objective and completion criteria")
        if self.action == "edit" and self.objective is None and self.completion_criteria is None:
            raise ValueError("edit requires an objective or completion criteria")
        if self.action not in {"create", "edit"} and (self.objective is not None or self.completion_criteria is not None):
            raise ValueError("this action cannot change the objective or completion criteria")
        if self.action == "progress" and self.progress is None:
            raise ValueError("progress requires text")
        if self.action not in {"create", "progress"} and self.progress is not None:
            raise ValueError("this action cannot change progress")
        if self.action == "complete" and not self.completion_confirmed:
            raise ValueError("completion requires explicit user confirmation")
        if self.action != "complete" and self.completion_confirmed:
            raise ValueError("completion confirmation applies only to complete")
        return self


class _ActionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: str
    fingerprint: str


class _GoalState(GoalView):
    schema_version: Literal[1] = 1
    actions: list[_ActionReceipt] = Field(default_factory=list, max_length=MAX_GOAL_ACTIONS)


def goal_context_text(snapshot: GoalSnapshot) -> str:
    """User-owned context, never developer instructions or a permission grant."""
    content = json.dumps(
        {"objective": snapshot.objective, "completion_criteria": snapshot.completion_criteria,
         "progress": snapshot.progress}, ensure_ascii=False,
    )
    return (
        "\n\n[Bridge-managed goal context: user-provided task data]\n"
        "This goal applies to this deliberately submitted turn only. It grants no permissions "
        "and authorises no automatic continuation or scheduled work. Goal completion is "
        "confirmed by the user in the goal controls.\n" + content + "\n[End goal context]"
    )


class GoalManager:
    """One goal owner using the existing thread lock and crash-reconcilable outbox."""

    def __init__(self, storage) -> None:
        self.storage = storage
        # Serialise goal mutation against the precise run dispatch boundary,
        # without holding the storage lock over provider calls.
        self._dispatch_lock = RLock()

    @staticmethod
    def _path(thread_id: str) -> str:
        if not isinstance(thread_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", thread_id):
            raise GoalUnavailable("Invalid local chat.")
        return f"goals/{thread_id}.json"

    def _thread(self, thread_id: str, *, mutate: bool = False):
        self._path(thread_id)
        if self.storage.runtime_profile is not RuntimeProfile.HOME_ASSISTANT:
            raise GoalUnavailable("Goals require the managed Home Assistant runtime.")
        thread = self.storage.load_thread(thread_id)
        if thread.assist_origin or not thread.project_id:
            raise GoalUnavailable("Goals are available only in ordinary chats.")
        project = self.storage.load_project(thread.project_id)
        if mutate and (thread.archived_at or project.archived_at):
            raise GoalError("Restore the chat and project before changing its goal.")
        return thread

    def _load(self, thread_id: str) -> _GoalState:
        self.storage.durable_outbox.reconcile()
        # Reuse the outbox's Linux no-follow parent traversal. Handle the normal
        # missing file inside its scope, before it translates caller IO errors.
        with self.storage.durable_outbox._open_state_target(self._path(thread_id)) as opened:
            try:
                if opened.parent_fd is None:
                    if opened.target.is_symlink():
                        raise GoalUnavailable("Goal storage is not a regular file.")
                    with opened.target.open("rb") as source:
                        raw = source.read(MAX_GOAL_STATE_BYTES + 1)
                else:
                    descriptor = os.open(opened.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=opened.parent_fd)
                    with os.fdopen(descriptor, "rb") as source:
                        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                            raise GoalUnavailable("Goal storage is not a regular file.")
                        raw = source.read(MAX_GOAL_STATE_BYTES + 1)
            except FileNotFoundError:
                return _GoalState()
        if len(raw) > MAX_GOAL_STATE_BYTES:
            raise GoalUnavailable("Goal storage exceeds its limit.")
        try:
            value = json.loads(raw)
            value.pop("_bridge_operation", None)
            return _GoalState.model_validate(value)
        except (ValidationError, ValueError, TypeError, AttributeError):
            raise GoalUnavailable("Goal storage is invalid.") from None

    def get(self, thread_id: str) -> GoalView:
        with self.storage._thread_mutation_lock:
            self._thread(thread_id)
            state = self._load(thread_id)
            return GoalView.model_validate(state.model_dump(include=set(GoalView.model_fields)))

    def apply(self, thread_id: str, action: GoalAction) -> GoalView:
        action = GoalAction.model_validate(action)
        with self._dispatch_lock, self.storage._thread_mutation_lock:
            self._thread(thread_id, mutate=True)
            state = self._load(thread_id)
            fingerprint = hashlib.sha256(action.model_dump_json().encode("utf-8")).hexdigest()
            for receipt in state.actions:
                if receipt.client_request_id == action.client_request_id:
                    if receipt.fingerprint != fingerprint:
                        raise GoalRevisionConflict("This action identity already has different content.")
                    # A replay is current readback; never reapply an old resume.
                    return self.get(thread_id)
            if action.expected_revision != state.revision:
                raise GoalRevisionConflict("The goal changed. Refresh before trying again.")
            revision = state.revision + 1
            now = datetime.now(UTC).isoformat()
            goal = state.goal
            if action.action == "create":
                if goal is not None and goal.status not in {"completed", "cancelled"}:
                    raise GoalError("Complete or cancel the existing goal before creating another.")
                if goal is not None:
                    state.history = (state.history + [goal])[-MAX_GOAL_HISTORY:]
                goal = GoalRecord(
                    goal_id=f"goal_{uuid4().hex}", objective=action.objective,
                    completion_criteria=action.completion_criteria, progress=action.progress or "",
                    activation_revision=revision, created_at=now, updated_at=now,
                )
            else:
                if goal is None or goal.status in {"completed", "cancelled"}:
                    raise GoalError("This goal is finished. Create a new goal to continue.")
                values = goal.model_dump()
                if action.action == "edit":
                    if goal.status != "paused":
                        raise GoalError("Pause the goal before editing its objective or criteria.")
                    if action.objective is not None:
                        values["objective"] = action.objective
                    if action.completion_criteria is not None:
                        values["completion_criteria"] = action.completion_criteria
                elif action.action == "progress":
                    values["progress"] = action.progress
                else:
                    values["status"] = {
                        "resume": "active", "pause": "paused", "complete": "completed", "cancel": "cancelled",
                    }[action.action]
                    values["activation_revision"] = revision
                values["updated_at"] = now
                goal = GoalRecord.model_validate(values)
            state.goal = goal
            state.revision = revision
            state.actions = (state.actions + [_ActionReceipt(
                client_request_id=action.client_request_id, fingerprint=fingerprint,
            )])[-MAX_GOAL_ACTIONS:]
            path = self._path(thread_id)
            self.storage.durable_outbox.commit_operation(
                operation_id=f"goal:{thread_id}:{revision}:{uuid4().hex}",
                writes=(OutboxWrite(
                    relative_path=path, state_revision=self.storage.durable_outbox.next_state_revision(path),
                    state_payload=state.model_dump(mode="json"),
                ),),
                # The event has no objective/criteria/progress or provider identity.
                events=(EventDraft(scope="thread", thread_id=thread_id, event_type="goal.updated",
                                   payload={"revision": revision, "status": goal.status}),),
            )
            return self.get(thread_id)

    def capture(self, thread_id: str) -> GoalSnapshot | None:
        with self.storage._thread_mutation_lock:
            thread = self._thread(thread_id)
            goal = self._load(thread_id).goal
            if goal is None or goal.status != "active" or thread.archived_at:
                return None
            return GoalSnapshot(**goal.model_dump(), workspace_path=thread.workspace_path, project_id=thread.project_id)

    def validate_snapshot(self, thread_id: str, snapshot: GoalSnapshot) -> None:
        with self.storage._thread_mutation_lock:
            thread = self._thread(thread_id)
            current = self._load(thread_id).goal
            if (current is None or current.status != "active" or current.goal_id != snapshot.goal_id
                    or current.activation_revision != snapshot.activation_revision
                    or thread.workspace_path != snapshot.workspace_path or thread.project_id != snapshot.project_id
                    or thread.archived_at or self.storage.load_project(thread.project_id).archived_at):
                raise GoalSnapshotStale("The accepted goal changed or was paused/cancelled. Resubmit deliberately.")

    @contextmanager
    def dispatch_guard(self, thread_id: str, snapshot: GoalSnapshot | None):
        """Fence mutation until the existing run owner marks dispatch started."""
        with self._dispatch_lock:
            if snapshot is not None:
                self.validate_snapshot(thread_id, snapshot)
            yield

    def delete_thread(self, thread_id: str) -> None:
        """Called by the existing deletion owner under its thread mutation lock."""
        self.storage.durable_outbox.reconcile()
        with self.storage.durable_outbox._open_state_target(self._path(thread_id)) as opened:
            try:
                if opened.parent_fd is None:
                    if opened.target.is_symlink():
                        raise GoalUnavailable("Goal storage is not a regular file.")
                    opened.target.unlink(missing_ok=True)
                else:
                    metadata = os.stat(opened.name, dir_fd=opened.parent_fd, follow_symlinks=False)
                    if not stat.S_ISREG(metadata.st_mode):
                        raise GoalUnavailable("Goal storage is not a regular file.")
                    os.unlink(opened.name, dir_fd=opened.parent_fd)
                    os.fsync(opened.parent_fd)
            except FileNotFoundError:
                pass
