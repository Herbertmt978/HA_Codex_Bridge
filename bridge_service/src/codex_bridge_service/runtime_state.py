from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .event_store import (
    DurableOutbox,
    DurableOperationTooLargeError,
    EventDraft,
    EventPayloadTooLargeError,
    EventStoreAdmissionError,
    EventStoreError,
    OutboxWrite,
    StoredEventRecord,
)
from .models import InteractionDisplayRecord, RunMode

RunStatus = Literal[
    "queued",
    "starting",
    "running",
    "cancelling",
    "completed",
    "cancelled",
    "failed",
    "interrupted",
]
InteractionStatus = Literal[
    "pending",
    "responding",
    "accepted",
    "declined",
    "cancelled",
    "answered",
    "expired",
    "outcome_unknown",
]
RUNTIME_STATE_SCHEMA_VERSION = 1
MAX_RUNTIME_STATE_BYTES = 64 * 1024 * 1024


def runtime_fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class RuntimeStateError(RuntimeError):
    pass


class RuntimeStateCommitUnknownError(RuntimeStateError):
    """A prepared state/event operation may still be recovered on restart."""


class RuntimeStateCorruptError(RuntimeStateError):
    def __init__(self) -> None:
        super().__init__("The private Codex runtime state is invalid.")


class RuntimeStateVersionError(RuntimeStateError):
    def __init__(self) -> None:
        super().__init__("The private Codex runtime state version is unsupported.")


class RuntimeRunState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=128)
    client_request_id: str = Field(min_length=1, max_length=256)
    thread_id: str = Field(min_length=1, max_length=128)
    unattended: bool = False
    prompt: str | None = Field(default=None, max_length=1024 * 1024, repr=False)
    prompt_fingerprint: str = Field(min_length=64, max_length=64)
    mode: RunMode
    model: str = Field(min_length=1, max_length=128)
    effort: str = Field(min_length=1, max_length=32)
    workspace_path: str = Field(min_length=1, max_length=512)
    attachment_ids: list[str] = Field(default_factory=list, max_length=256)
    attachment_manifest_fingerprint: str = Field(min_length=64, max_length=64)
    codex_thread_id: str | None = Field(default=None, max_length=256)
    codex_turn_id: str | None = Field(default=None, max_length=256)
    turn_start_dispatched: bool = False
    generation: int | None = Field(default=None, ge=1)
    status: RunStatus
    created_at: str = Field(min_length=1, max_length=64)
    total_deadline_at: str | None = Field(default=None, max_length=64)
    started_at: str | None = Field(default=None, max_length=64)
    last_activity_at: str = Field(min_length=1, max_length=64)
    cancellation_requested_at: str | None = Field(default=None, max_length=64)
    terminal_message: str | None = Field(default=None, max_length=256)
    emitted_signatures: list[str] = Field(default_factory=list, max_length=2048)
    completed_item_ids: list[str] = Field(default_factory=list, max_length=4096)


class RuntimeInteractionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interaction_id: str = Field(min_length=1, max_length=128)
    kind: Literal[
        "command_approval",
        "file_change_approval",
        "user_input",
    ]
    thread_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    codex_thread_id: str = Field(min_length=1, max_length=256)
    turn_id: str = Field(min_length=1, max_length=256)
    item_id: str = Field(min_length=1, max_length=256)
    generation: int = Field(ge=1)
    app_request_id: str | int = Field(repr=False)
    status: InteractionStatus = "pending"
    display: InteractionDisplayRecord | None = None
    allowed_actions: list[Literal["accept", "decline", "cancel", "answer"]] = Field(
        max_length=4
    )
    created_at: str = Field(min_length=1, max_length=64)
    expires_at: str = Field(min_length=1, max_length=64)
    event_id: int = Field(default=0, ge=0)
    response_client_request_id: str | None = Field(default=None, max_length=256)
    response_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)


class RuntimeRequestOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=128)
    thread_id: str = Field(min_length=1, max_length=128)
    kind: Literal["prompt", "steer"]
    unattended: bool = False
    fingerprint: str = Field(min_length=64, max_length=64)
    status: Literal["accepted", "uncertain"] = "accepted"
    run_status: RunStatus


class RuntimeStateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = RUNTIME_STATE_SCHEMA_VERSION
    revision: int = Field(default=0, ge=0)
    observed_app_server_generation: int = Field(default=0, ge=0)
    runs: dict[str, RuntimeRunState] = Field(default_factory=dict)
    interactions: dict[str, RuntimeInteractionState] = Field(default_factory=dict)
    request_idempotency: dict[str, RuntimeRequestOutcome] = Field(default_factory=dict)


class RuntimeStateStore:
    """Atomic private persistence for the global app-server runtime owner."""

    def __init__(
        self,
        state_root: Path | str,
        *,
        durable_outbox: DurableOutbox | None = None,
    ) -> None:
        self.path = Path(state_root) / "runtime-state.json"
        self._durable_outbox = durable_outbox
        self._lock = Lock()

    def load(self) -> RuntimeStateRecord:
        with self._lock:
            try:
                if self.path.stat().st_size > MAX_RUNTIME_STATE_BYTES:
                    raise RuntimeStateCorruptError()
                payload = self.path.read_bytes()
            except FileNotFoundError:
                return RuntimeStateRecord()
            except OSError:
                raise RuntimeStateCorruptError() from None
            try:
                raw = json.loads(payload)
                if not isinstance(raw, dict):
                    raise RuntimeStateCorruptError()
                marker = raw.pop("_bridge_operation", None)
                if marker is not None:
                    if (
                        not isinstance(marker, dict)
                        or set(marker) != {"operation_id", "revision"}
                        or not isinstance(marker.get("operation_id"), str)
                        or not marker["operation_id"]
                        or type(marker.get("revision")) is not int
                        or marker["revision"] < 1
                        or marker["revision"] != raw.get("revision")
                    ):
                        raise RuntimeStateCorruptError()
                # Pre-release Task 7 state had the same v1 shape without an
                # explicit marker. This is the sole legacy migration; future
                # versions must add a deliberate migration here.
                raw.setdefault("schema_version", RUNTIME_STATE_SCHEMA_VERSION)
                if raw.get("schema_version") != RUNTIME_STATE_SCHEMA_VERSION:
                    raise RuntimeStateVersionError()
                request_outcomes = raw.get("request_idempotency")
                runs = raw.get("runs")
                if isinstance(request_outcomes, dict) and isinstance(runs, dict):
                    for run in runs.values():
                        if not isinstance(run, dict):
                            raise RuntimeStateCorruptError()
                        prompt = run.get("prompt")
                        if "prompt_fingerprint" not in run and isinstance(prompt, str):
                            run["prompt_fingerprint"] = runtime_fingerprint(prompt)
                        run.setdefault("attachment_ids", [])
                        run.setdefault(
                            "attachment_manifest_fingerprint",
                            runtime_fingerprint([]),
                        )
                        run.setdefault("turn_start_dispatched", False)
                        run.setdefault("total_deadline_at", None)
                    for request_id, value in tuple(request_outcomes.items()):
                        if isinstance(value, dict) and "run_status" not in value:
                            run = runs.get(value.get("run_id"))
                            if not isinstance(run, dict):
                                raise RuntimeStateCorruptError()
                            value["run_status"] = run.get("status")
                        if not isinstance(value, str):
                            continue
                        run = runs.get(value)
                        if not isinstance(run, dict):
                            raise RuntimeStateCorruptError()
                        fingerprint = run.get("prompt_fingerprint")
                        if not isinstance(fingerprint, str):
                            prompt = run.get("prompt")
                            if not isinstance(prompt, str):
                                raise RuntimeStateCorruptError()
                            fingerprint = runtime_fingerprint(prompt)
                            run["prompt_fingerprint"] = fingerprint
                        request_outcomes[request_id] = {
                            "run_id": value,
                            "thread_id": run.get("thread_id"),
                            "kind": "prompt",
                            "fingerprint": fingerprint,
                            "status": "accepted",
                            "run_status": run.get("status"),
                        }
                return RuntimeStateRecord.model_validate(raw)
            except (json.JSONDecodeError, UnicodeDecodeError, ValidationError):
                raise RuntimeStateCorruptError() from None

    def quarantine_corrupt(self) -> Path | None:
        """Move one unreadable v1 checkpoint aside without exposing its contents."""
        with self._lock:
            if not self.path.exists():
                return None
            target = self.path.with_name(f"runtime-state.corrupt.{uuid4().hex}.json")
            try:
                os.replace(self.path, target)
                if os.name != "nt":
                    directory = os.open(self.path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
            except OSError:
                raise RuntimeStateError(
                    "The invalid Codex runtime state could not be quarantined."
                ) from None
            return target

    def save(self, state: RuntimeStateRecord) -> None:
        _validated, _state_payload, payload = self._validated_payload(state)
        self._atomic_save(payload)

    def save_with_events(
        self,
        state: RuntimeStateRecord,
        *,
        events: tuple[EventDraft, ...] = (),
        additional_writes: tuple[OutboxWrite, ...] = (),
    ) -> tuple[StoredEventRecord, ...]:
        validated, state_payload, _payload = self._validated_payload(state)
        if not events and not additional_writes:
            self.save(validated)
            return ()
        if self._durable_outbox is None:
            self.save(validated)
            raise RuntimeStateError("Durable runtime events require an event outbox.")
        if validated.revision < 1:
            raise RuntimeStateError(
                "The private Codex runtime state revision is invalid."
            )
        with self._lock:
            try:
                return self._durable_outbox.commit_operation(
                    operation_id=(f"runtime-state:{validated.revision}:{uuid4().hex}"),
                    writes=(
                        OutboxWrite(
                            relative_path=self.path.name,
                            state_revision=validated.revision,
                            state_payload=state_payload,
                        ),
                        *additional_writes,
                    ),
                    events=events,
                )
            except (
                DurableOperationTooLargeError,
                EventPayloadTooLargeError,
                EventStoreAdmissionError,
            ):
                # These are admission failures raised before canonical state
                # is replaced. Callers may safely roll back and return the
                # bounded resource response.
                raise
            except EventStoreError:
                raise RuntimeStateCommitUnknownError(
                    "The private Codex runtime state could not be saved."
                ) from None

    def _validated_payload(
        self,
        state: RuntimeStateRecord,
    ) -> tuple[RuntimeStateRecord, dict[str, object], bytes]:
        try:
            validated = RuntimeStateRecord.model_validate(
                state.model_dump(mode="python")
            )
            state_payload = validated.model_dump(mode="json")
            payload = json.dumps(
                state_payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, ValidationError):
            raise RuntimeStateError(
                "The private Codex runtime state could not be validated."
            ) from None
        if len(payload) > MAX_RUNTIME_STATE_BYTES:
            raise RuntimeStateError(
                "The private Codex runtime state exceeds its limit."
            )
        return validated, state_payload, payload

    def _atomic_save(self, payload: bytes) -> None:
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        with self._lock:
            try:
                descriptor = os.open(
                    temporary,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                if os.name != "nt":
                    directory = os.open(self.path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
            except OSError:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                raise RuntimeStateError(
                    "The private Codex runtime state could not be saved."
                ) from None
