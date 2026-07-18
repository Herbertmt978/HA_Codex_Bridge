from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, RLock, Thread, current_thread
from time import monotonic
from typing import Any, Iterator, Literal, Protocol, cast
from uuid import uuid4

from .codex_app_server import (
    DEFERRED_RESPONSE,
    AppServerNotification,
    AppServerRequest,
)
from .browser_broker import BrowserBroker, BrowserInvocationContext
from .browser_contract import browser_dynamic_tool_spec
from .event_store import (
    DurableOperationTooLargeError,
    EventDraft,
    EventPayloadTooLargeError,
    EventStoreAdmissionError,
    EventStoreError,
    OutboxWrite,
    StoredEventRecord,
)
from .generated_images import validate_generated_image_result
from .models import (
    ArtifactRecord,
    InteractionResultRecord,
    PendingInteractionRecord,
    ProjectKind,
    RunMode,
    RunRecord,
    RuntimeProfile,
    ThreadRecord,
)
from .resource_limits import ResourceLimitError, ResourceLimits
from .runtime_gate import (
    RuntimeGate,
    RuntimeGateSnapshot,
    RuntimeLease,
    RuntimeLeaseCancelledError,
    RuntimeLeaseTimeoutError,
)
from .runtime_policy import (
    RuntimeProtocolMismatchError,
    approval_display,
    approval_workspace_paths,
    bounded_raw_text,
    bounded_text,
    interaction_correlation,
    mode_policy,
    normalize_workspace_path,
    question_display,
    validate_steer_result,
    validate_thread_result,
    validate_turn_result,
)
from .runtime_state import (
    RuntimeInteractionState,
    RuntimeRequestOutcome,
    RuntimeRunState,
    RuntimeStateCommitUnknownError,
    RuntimeStateCorruptError,
    RuntimeStateError,
    RuntimeStateRecord,
    RuntimeStateStore,
    runtime_fingerprint,
)
from .storage import BridgeStorage, ProjectMutationError, ThreadNotFoundError
from .workspace import WorkspaceBoundaryError


class _AppServer(Protocol):
    @property
    def generation(self) -> int: ...

    def request(
        self,
        method: str,
        params: object = None,
        *,
        timeout_seconds: float | None = None,
    ) -> object: ...

    def respond(
        self,
        request: AppServerRequest,
        *,
        result: object,
    ) -> None: ...

    def register_notification_handler(self, method: str, handler: object) -> None: ...

    def register_request_handler(self, method: str, handler: object) -> None: ...

    def discard_server_request(
        self,
        request_id: str | int,
        expected_generation: int,
    ) -> bool: ...

    def abort_generation(self, expected_generation: int) -> bool: ...


class _ImageGenerationAuthority(Protocol):
    def authorize_image_generation(self, expected_generation: int) -> int | None: ...

    def is_image_generation_authorized(
        self,
        expected_generation: int,
        expected_revision: int,
    ) -> bool: ...

    def acquire_image_generation_publication_lease(
        self,
        expected_generation: int,
        expected_revision: int,
    ) -> object | None: ...


class RuntimeBrokerError(RuntimeError):
    code = "runtime_error"
    status_code = 409
    retryable = False

    def __init__(self, message: str) -> None:
        super().__init__(message)

    def public_detail(self) -> dict[str, object]:
        return {"code": self.code, "retryable": self.retryable}


class RuntimeRequestConflictError(RuntimeBrokerError):
    code = "runtime_request_conflict"

    def __init__(self) -> None:
        super().__init__("The client request ID was already used for different input.")


class RuntimeUnavailableError(RuntimeBrokerError):
    code = "app_server_unavailable"
    status_code = 503
    retryable = True

    def __init__(self) -> None:
        super().__init__("The Codex app server is unavailable.")


class RuntimeAuthenticationRequiredError(RuntimeBrokerError):
    code = "authentication_required"

    def __init__(self) -> None:
        super().__init__(
            "ChatGPT sign-in must be verified before Codex can start a turn."
        )


class RuntimeClosedError(RuntimeBrokerError):
    code = "runtime_closed"
    status_code = 503

    def __init__(self) -> None:
        super().__init__("The Codex runtime broker is closed.")


class RuntimeSteerOutcomeUnknownError(RuntimeBrokerError):
    code = "steer_outcome_unknown"

    def __init__(self) -> None:
        super().__init__(
            "Codex may have received the follow-up; it will not be replayed."
        )


class InteractionNotFoundError(RuntimeBrokerError):
    code = "interaction_not_found"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("The Codex interaction was not found.")


class InteractionStaleError(RuntimeBrokerError):
    code = "interaction_stale"
    status_code = 410

    def __init__(self) -> None:
        super().__init__("The Codex interaction is no longer active.")


class InteractionResolvedError(RuntimeBrokerError):
    code = "interaction_already_resolved"

    def __init__(self) -> None:
        super().__init__("The Codex interaction has already been resolved.")


class InteractionOutcomeUnknownError(RuntimeBrokerError):
    code = "interaction_outcome_unknown"

    def __init__(self) -> None:
        super().__init__(
            "Codex may have received the interaction response before restart."
        )


class TurnChangedError(RuntimeBrokerError):
    code = "turn_changed"

    def __init__(self) -> None:
        super().__init__("The active Codex turn has changed.")


class TurnCancellingError(RuntimeBrokerError):
    code = "turn_cancelling"

    def __init__(self) -> None:
        super().__init__("The active Codex turn is being cancelled.")


class RuntimePromptPendingError(RuntimeBrokerError):
    code = "thread_prompt_pending"
    retryable = True

    def __init__(self) -> None:
        super().__init__("This chat already has a Codex prompt waiting to start.")


class RuntimeThreadBusyError(RuntimeBrokerError):
    code = "runtime_thread_busy"
    retryable = True

    def __init__(self) -> None:
        super().__init__(
            "A chat cannot be deleted while Codex owns an active or queued turn."
        )


class RuntimeAttachmentsUnavailableError(RuntimeBrokerError):
    code = "runtime_attachments_not_ready"

    def __init__(self) -> None:
        super().__init__(
            "Attachments require the resumable, checksum-bound runtime transport."
        )


class RuntimeStateCapacityError(RuntimeBrokerError):
    code = "runtime_idempotency_capacity"
    status_code = 503

    def __init__(self) -> None:
        super().__init__(
            "The Codex request history is full; remove an old chat before retrying."
        )


class RuntimeEventPayloadTooLargeError(RuntimeBrokerError):
    code = "runtime_event_payload_too_large"
    status_code = 413

    def __init__(self) -> None:
        super().__init__("The prompt is too large for the durable event journal.")


class _RuntimeTotalDeadlineExceeded(RuntimeError):
    pass


_TERMINAL_RUN_STATES = {"completed", "cancelled", "failed", "interrupted"}
_MANAGED_WEB_SEARCH_DEFAULT = "cached"
_LIVE_WEB_SEARCH_GUIDANCE = (
    "Application web-search policy: Live web search is available. "
    "When the request depends on current, recent, changing, scheduled, price, "
    "weather, news, rules, or other time-sensitive information, use the native "
    "web-search tool before answering unless the user explicitly asks you not to. "
    "Use the native tool rather than shell commands for network access. "
    "Do not include credentials, private workspace contents, or unrelated personal "
    "data in search queries."
)
_PENDING_INTERACTION_STATES = {"pending", "responding"}
_MAX_REQUEST_OUTCOMES = 50_000
_MAX_TERMINAL_RUNS = 1024
_MAX_TERMINAL_INTERACTIONS = 2048
_MAX_PRE_RESPONSE_CALLBACKS = 16
_SAFE_ITEM_STATUSES = frozenset(
    {"inProgress", "completed", "succeeded", "failed", "declined"}
)
_SAFE_COMMAND_ACTION_TYPES = frozenset({"read", "listFiles", "search", "unknown"})
_SAFE_WEB_SEARCH_ACTION_TYPES = frozenset({"search", "openPage", "findInPage", "other"})
_SAFE_CHANGE_KINDS = frozenset({"add", "delete", "update"})
_SAFE_COLLAB_AGENT_OPERATIONS = frozenset(
    {"spawnAgent", "sendInput", "resumeAgent", "wait", "closeAgent"}
)
_SAFE_COLLAB_AGENT_STATES = frozenset(
    {
        "pendingInit",
        "running",
        "interrupted",
        "completed",
        "errored",
        "shutdown",
        "notFound",
    }
)
_SAFE_SUB_AGENT_ACTIVITY_KINDS = frozenset({"started", "interacted", "interrupted"})
_MAX_ITEM_ACTIVITY_DURATION_MS = 86_400_000
_MAX_SAFE_AGENT_STATE_COUNT = 9_007_199_254_740_991
_BROWSER_DYNAMIC_TOOLS = frozenset(
    {
        "open",
        "navigate",
        "inspect",
        "click",
        "type",
        "select",
        "wait",
        "screenshot",
        "pdf",
        "close",
    }
)
_MAX_BROWSER_DYNAMIC_TEXT_BYTES = 32 * 1024
_MAX_BROWSER_DYNAMIC_IMAGE_URL_BYTES = 6 * 1024 * 1024
_MAX_BROWSER_DYNAMIC_ARGUMENT_BYTES = 32 * 1024
_MAX_BROWSER_TOOL_REPLAYS_PER_TURN = 128
_NOTIFICATIONS = (
    "turn/started",
    "item/agentMessage/delta",
    "item/reasoning/summaryPartAdded",
    "item/reasoning/summaryTextDelta",
    "item/reasoning/textDelta",
    "item/plan/delta",
    "turn/plan/updated",
    "turn/diff/updated",
    "item/fileChange/patchUpdated",
    "item/started",
    "item/completed",
    "turn/completed",
    "error",
    "serverRequest/resolved",
)
_REQUESTS = (
    "item/tool/call",
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
    "item/tool/requestUserInput",
    "execCommandApproval",
    "applyPatchApproval",
)


@dataclass(frozen=True, slots=True)
class _GeneratedImagePublication:
    run_id: str
    thread_id: str
    generation: int
    codex_thread_id: str
    turn_id: str
    item_id: str
    status: str | None
    result: object
    mime_type: str | None
    payload: dict[str, object]
    authorized: bool
    authority_lease: object | None


@dataclass(frozen=True, slots=True)
class _SafeFailure:
    """A fixed public failure projection for untrusted Codex error data."""

    message: str
    failure_type: str
    blocked: bool = False
    auth_required: bool = False


@dataclass(frozen=True, slots=True)
class _BrowserThreadAuthority:
    """Ephemeral proof that one fresh Codex thread received our tool namespace."""

    generation: int
    codex_thread_id: str


@dataclass(slots=True)
class _BrowserToolReplay:
    """At-most-once record for one app-server dynamic-tool callback."""

    fingerprint: str
    result: dict[str, object] | None = None


class PromptAdmission:
    """Opaque one-shot ownership of one exact prompt admission decision."""

    __slots__ = (
        "_broker",
        "_client_request_id",
        "_fingerprint",
        "_lease",
        "_replay_run",
        "_state",
        "_unattended",
        "_web_search",
    )

    def __init__(
        self,
        broker: RuntimeBroker,
        *,
        client_request_id: str,
        fingerprint: str,
        unattended: bool,
        web_search: Literal["live", "disabled"] | None,
        lease: RuntimeLease | None,
        replay_run: RunRecord | None = None,
    ) -> None:
        self._broker = broker
        self._client_request_id = client_request_id
        self._fingerprint = fingerprint
        self._unattended = unattended
        self._web_search = web_search
        self._lease = lease
        self._replay_run = replay_run
        self._state: Literal["reserved", "replay", "transferred", "released"] = (
            "replay" if replay_run is not None else "reserved"
        )

    @property
    def replay_run(self) -> RunRecord | None:
        if self._replay_run is None:
            return None
        return self._replay_run.model_copy(deep=True)

    def __repr__(self) -> str:
        return f"PromptAdmission(state={self._state!r})"


class RuntimeBroker:
    """Durable, bounded broker for the single supervised Codex app-server."""

    def __init__(
        self,
        storage: BridgeStorage,
        app_server: _AppServer,
        runtime_gate: RuntimeGate,
        *,
        resource_limits: ResourceLimits | None = None,
        queue_wait_timeout_seconds: float | None = None,
        control_request_timeout_seconds: float = 30.0,
        watchdog_interval_seconds: float = 0.25,
        turn_timeout_seconds: float | None = None,
        cancel_grace_seconds: float | None = None,
        interaction_timeout_seconds: float | None = None,
        run_terminal_listener: Callable[[str, str, str, bool], None] | None = None,
        image_generation_authority: _ImageGenerationAuthority | None = None,
        browser_broker: BrowserBroker | None = None,
        browser_dynamic_tools_enabled: bool = False,
        provider_admission_check: Callable[[], bool] | None = None,
    ) -> None:
        if type(browser_dynamic_tools_enabled) is not bool:
            raise ValueError("browser dynamic tool state must be a boolean")
        self.storage = storage
        self.app_server = app_server
        self.gate = runtime_gate
        self.limits = resource_limits or storage.resource_limits or ResourceLimits()
        if self.limits.max_active_turns != 1:
            raise ValueError("RuntimeBroker requires exactly one active turn.")
        if runtime_gate.limits != self.limits:
            raise ValueError(
                "RuntimeBroker and runtime gate require identical resource limits."
            )
        self.queue_wait_timeout_seconds = (
            self.limits.run_total_timeout_seconds
            if queue_wait_timeout_seconds is None
            else _positive_timeout(queue_wait_timeout_seconds)
        )
        self.control_request_timeout_seconds = _positive_timeout(
            control_request_timeout_seconds
        )
        self.watchdog_interval_seconds = _positive_timeout(watchdog_interval_seconds)
        self.turn_timeout_seconds = (
            self.limits.run_total_timeout_seconds
            if turn_timeout_seconds is None
            else _positive_timeout(turn_timeout_seconds)
        )
        self.cancel_grace_seconds = (
            self.limits.cancel_grace_seconds
            if cancel_grace_seconds is None
            else _positive_timeout(cancel_grace_seconds)
        )
        self.interaction_timeout_seconds = (
            self.limits.run_idle_timeout_seconds
            if interaction_timeout_seconds is None
            else _positive_timeout(interaction_timeout_seconds)
        )
        self._run_terminal_listener = run_terminal_listener
        self._image_generation_authority = image_generation_authority
        self._browser_broker = browser_broker
        # This has no durable representation. A restored runtime cannot regain
        # access to client-owned tools that were registered on a prior Codex
        # app-server generation.
        self._browser_dynamic_tools_enabled = browser_dynamic_tools_enabled
        self._provider_admission_check = provider_admission_check
        self._browser_pending_thread_authorities: dict[
            str, _BrowserThreadAuthority
        ] = {}
        self._browser_turn_authorities: dict[str, BrowserInvocationContext] = {}
        self._browser_tool_replays: dict[str, dict[str, _BrowserToolReplay]] = {}
        self._store = RuntimeStateStore(
            storage.root,
            durable_outbox=storage.durable_outbox,
        )
        self._recovered_corrupt_state = False
        try:
            self._state = self._store.load()
        except RuntimeStateCorruptError:
            self._store.quarantine_corrupt()
            self._state = RuntimeStateRecord()
            self._recovered_corrupt_state = True
        self._lock = RLock()
        self._leases: dict[str, RuntimeLease] = {}
        self._pending_prompt_admissions: dict[str, PromptAdmission] = {}
        self._completion_events: dict[str, Event] = {}
        self._server_requests: dict[str, AppServerRequest] = {}
        self._item_paths: dict[tuple[str, str], list[str]] = {}
        self._pre_response_callbacks: dict[
            str, list[AppServerNotification | AppServerRequest]
        ] = {}
        self._pre_response_request_owners: dict[tuple[int, str | int], str] = {}
        self._callback_replays_in_progress: set[str] = set()
        self._inflight_publications: dict[str, int] = {}
        self._inflight_image_items: set[tuple[str, str]] = set()
        self._workers: set[Thread] = set()
        self._pending_artifact_reconciliations: dict[str, int] = {}
        self._artifact_reconciliation_worker: Thread | None = None
        self._activity: dict[str, float] = {}
        self._started = False
        self._closed = False
        self._fatal_error = False
        for method in _NOTIFICATIONS:
            app_server.register_notification_handler(method, self._on_notification)
        for method in _REQUESTS:
            app_server.register_request_handler(method, self._on_server_request)

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeUnavailableError()
            if self._started:
                return
            if self._recovered_corrupt_state:
                self._repair_thread_projections_after_state_reset_locked()
            generation = self.app_server.generation
            self._reconcile_generation_locked(generation, reason="bridge restarted")
            self._repair_orphaned_thread_projections_locked()
            self._started = True

    def delete_thread(self, thread_id: str) -> None:
        """Delete an idle chat and its retained private runtime history."""
        with self.storage.admit_thread_deletion(thread_id):
            with self._lock:
                self._require_started_locked()
                self._assert_threads_deletable_locked({thread_id})
                self._purge_threads_locked({thread_id})
                self.storage.delete_thread(thread_id)

    def delete_project(self, project_id: str) -> None:
        """Delete a project only when none of its chats has runtime ownership."""
        with self.storage.admit_project_deletion(project_id) as project:
            with self._lock:
                self._require_started_locked()
                if project.kind is not ProjectKind.PROJECT:
                    raise ProjectMutationError("only normal projects can be deleted")
                thread_ids = {
                    thread.thread_id
                    for thread in self.storage.list_threads(include_archived=True)
                    if thread.project_id == project_id
                }
                self._assert_threads_deletable_locked(thread_ids)
                self._purge_threads_locked(thread_ids)
                self.storage.delete_project(project_id)

    @contextmanager
    def admit_prompt(
        self,
        prompt: str,
        *,
        client_request_id: str,
        unattended: bool = False,
        web_search: Literal["live", "disabled"] | None = None,
    ) -> Iterator[PromptAdmission]:
        """Reserve prompt capacity before a caller mutates its target.

        Exact durable replays carry no live lease. Fresh admissions own one
        exact RuntimeGate lease until ``submit_prompt`` atomically transfers
        it into the accepted run, or this context releases it on every failure.
        """

        if type(unattended) is not bool:
            raise ValueError("unattended must be a boolean")
        if web_search is not None and (
            type(web_search) is not str or web_search not in {"live", "disabled"}
        ):
            raise ValueError("web_search must be live or disabled")
        normalized_prompt = _prompt(prompt)
        request_id = _identifier(
            client_request_id, limit=256, label="client request id"
        )
        _require_message_event_capacity(
            prompt=normalized_prompt,
            client_request_id=request_id,
            maximum_bytes=self.limits.max_event_payload_bytes,
        )
        fingerprint = _fingerprint(normalized_prompt)

        with self._lock:
            self._require_started_locked()
            replay = self._prompt_admission_replay_locked(
                request_id,
                fingerprint=fingerprint,
                unattended=unattended,
                web_search=web_search,
            )
            if replay is not None:
                admission = PromptAdmission(
                    self,
                    client_request_id=request_id,
                    fingerprint=fingerprint,
                    unattended=unattended,
                    web_search=web_search,
                    lease=None,
                    replay_run=replay,
                )
            else:
                self._raise_for_pending_prompt_admission_locked(
                    request_id,
                    fingerprint=fingerprint,
                    unattended=unattended,
                    web_search=web_search,
                )
                self._ensure_request_capacity_locked()
                admission = None

        if admission is None:
            # The authoritative account read may bind storage, so it must run
            # without the broker lock and before this caller mutates a target.
            if not self._provider_admission_allowed():
                raise RuntimeAuthenticationRequiredError()
            with self._lock:
                self._require_started_locked()
                replay = self._prompt_admission_replay_locked(
                    request_id,
                    fingerprint=fingerprint,
                    unattended=unattended,
                    web_search=web_search,
                )
                if replay is not None:
                    admission = PromptAdmission(
                        self,
                        client_request_id=request_id,
                        fingerprint=fingerprint,
                        unattended=unattended,
                        web_search=web_search,
                        lease=None,
                        replay_run=replay,
                    )
                else:
                    self._raise_for_pending_prompt_admission_locked(
                        request_id,
                        fingerprint=fingerprint,
                        unattended=unattended,
                        web_search=web_search,
                    )
                    self._ensure_request_capacity_locked()
                    lease = self.gate.reserve_prompt(client_request_id=request_id)
                    try:
                        # Once this lease exists, an account bind cannot race
                        # target preparation or provider-continuity capture.
                        if not self._provider_admission_allowed():
                            raise RuntimeAuthenticationRequiredError()
                        admission = PromptAdmission(
                            self,
                            client_request_id=request_id,
                            fingerprint=fingerprint,
                            unattended=unattended,
                            web_search=web_search,
                            lease=lease,
                        )
                        self._pending_prompt_admissions[request_id] = admission
                    except BaseException:
                        lease.release()
                        raise

        assert admission is not None
        try:
            yield admission
        finally:
            self._release_prompt_admission(admission)

    def submit_prompt(
        self,
        thread_id: str,
        prompt: str,
        *,
        client_request_id: str | None = None,
        unattended: bool = False,
        web_search: Literal["live", "disabled"] | None = None,
        admission: PromptAdmission | None = None,
    ) -> RunRecord:
        if type(unattended) is not bool:
            raise ValueError("unattended must be a boolean")
        if web_search is not None and (
            type(web_search) is not str or web_search not in {"live", "disabled"}
        ):
            raise ValueError("web_search must be live or disabled")
        request_id = client_request_id or f"req_{uuid4().hex}"
        prompt = _prompt(prompt)
        request_id = _identifier(request_id, limit=256, label="client request id")
        _require_message_event_capacity(
            prompt=prompt,
            client_request_id=request_id,
            maximum_bytes=self.limits.max_event_payload_bytes,
        )
        thread = self.storage.get_thread(thread_id)
        self.storage.resolve_workspace_path(thread.workspace_path)

        with self._lock:
            self._require_started_locked()
            self._validate_prompt_admission_locked(
                admission,
                request_id=request_id,
                fingerprint=_fingerprint(prompt),
                unattended=unattended,
                web_search=web_search,
            )
            if admission is None:
                self._raise_for_pending_prompt_admission_locked(
                    request_id,
                    fingerprint=_fingerprint(prompt),
                    unattended=unattended,
                    web_search=web_search,
                )
            # Broker-owned deletion holds this same lock. Revalidate here so a
            # submit that resolved metadata before deletion cannot resurrect
            # runtime ownership after the chat has been removed.
            self.storage.load_thread(thread_id)
            existing_outcome = self._state.request_idempotency.get(request_id)
            if existing_outcome is not None:
                existing = self._state.runs.get(existing_outcome.run_id)
                if (
                    existing_outcome.thread_id != thread_id
                    or existing_outcome.fingerprint != _fingerprint(prompt)
                    or existing_outcome.unattended != unattended
                    or existing_outcome.web_search != web_search
                ):
                    raise RuntimeRequestConflictError()
                if existing_outcome.status == "uncertain":
                    raise RuntimeSteerOutcomeUnknownError()
                return (
                    _run_record(existing)
                    if existing is not None
                    else _outcome_record(existing_outcome)
                )

        # Auth reconciliation can bind account ownership through storage locks
        # that broker-owned deletion acquires before ``self._lock``. Never run
        # that potentially mutating reconciliation while holding the broker
        # lock; repeat every admission-sensitive broker check after it returns.
        if admission is None:
            if not self._provider_admission_allowed():
                raise RuntimeAuthenticationRequiredError()
            thread = self.storage.get_thread(thread_id)
            self.storage.resolve_workspace_path(thread.workspace_path)

        with self._lock:
            self._require_started_locked()
            self._validate_prompt_admission_locked(
                admission,
                request_id=request_id,
                fingerprint=_fingerprint(prompt),
                unattended=unattended,
                web_search=web_search,
            )
            if admission is None:
                self._raise_for_pending_prompt_admission_locked(
                    request_id,
                    fingerprint=_fingerprint(prompt),
                    unattended=unattended,
                    web_search=web_search,
                )
            # A delete or concurrent idempotent submission can complete while
            # the authoritative auth read runs without the broker lock.
            self.storage.load_thread(thread_id)
            existing_outcome = self._state.request_idempotency.get(request_id)
            if existing_outcome is not None:
                existing = self._state.runs.get(existing_outcome.run_id)
                if (
                    existing_outcome.thread_id != thread_id
                    or existing_outcome.fingerprint != _fingerprint(prompt)
                    or existing_outcome.unattended != unattended
                    or existing_outcome.web_search != web_search
                ):
                    raise RuntimeRequestConflictError()
                if existing_outcome.status == "uncertain":
                    raise RuntimeSteerOutcomeUnknownError()
                return (
                    _run_record(existing)
                    if existing is not None
                    else _outcome_record(existing_outcome)
                )

            self._ensure_request_capacity_locked(
                reserved_request_id=(request_id if admission is not None else None)
            )

            if any(
                run.thread_id == thread_id and run.status == "cancelling"
                for run in self._state.runs.values()
            ):
                raise TurnCancellingError()

            if any(
                run.thread_id == thread_id and run.status == "queued"
                for run in self._state.runs.values()
            ):
                raise RuntimePromptPendingError()

            active = self._active_run_for_thread_locked(thread_id)
            if active is not None:
                # The authoritative account can fail closed after the
                # pre-lock check. An active run already owns a prompt lease,
                # so this final check cannot reconcile account ownership; it
                # only fences the steer before any local or provider acceptance.
                if not self._provider_admission_allowed():
                    raise RuntimeAuthenticationRequiredError()
                if unattended or active.unattended:
                    raise RuntimePromptPendingError()
                if not active.codex_thread_id or not active.codex_turn_id:
                    raise RuntimePromptPendingError()
                self._state.request_idempotency[request_id] = RuntimeRequestOutcome(
                    run_id=active.run_id,
                    thread_id=thread_id,
                    kind="steer",
                    unattended=False,
                    web_search=web_search,
                    fingerprint=_fingerprint(prompt),
                    status="uncertain",
                    run_status=active.status,
                )
                self._persist_locked()
                self._begin_publication_locked(thread_id)
                steer = (
                    active.codex_thread_id,
                    active.codex_turn_id,
                    active.run_id,
                    active.generation,
                )
            else:
                steer = None

            if steer is None:
                # Reserve prompt ownership before the final admission check and
                # provider-continuity read. The prompt lease excludes account
                # binding, so an account update cannot detach the stored Codex
                # thread after it is captured into this run.
                lease = (
                    admission._lease
                    if admission is not None
                    else self.gate.reserve_prompt(client_request_id=request_id)
                )
                if lease is None:
                    raise RuntimeRequestConflictError()
                run: RuntimeRunState | None = None
                try:
                    if admission is None and not self._provider_admission_allowed():
                        raise RuntimeAuthenticationRequiredError()
                    thread = self.storage.get_thread(thread_id)
                    self.storage.resolve_workspace_path(thread.workspace_path)
                    accepted_at = datetime.now(UTC)
                    now = accepted_at.isoformat()
                    run = RuntimeRunState(
                        run_id=f"run_{uuid4().hex[:16]}",
                        client_request_id=request_id,
                        thread_id=thread_id,
                        unattended=unattended,
                        web_search=web_search,
                        prompt=prompt,
                        prompt_fingerprint=_fingerprint(prompt),
                        mode=thread.mode,
                        model=thread.effective_model,
                        effort=thread.effective_thinking_level,
                        workspace_path=thread.workspace_path,
                        # Stored uploads are deliberately inert unless a future
                        # explicit, checksum-bound attachment-selection API opts
                        # into them.  A text-only prompt must never leak every
                        # historical upload merely because it belongs to a thread.
                        attachment_ids=[],
                        attachment_manifest_fingerprint=_attachment_manifest([]),
                        codex_thread_id=thread.codex_thread_id,
                        status="starting",
                        created_at=now,
                        total_deadline_at=(
                            accepted_at
                            + timedelta(seconds=self.limits.run_total_timeout_seconds)
                        ).isoformat(),
                        last_activity_at=now,
                    )
                    if lease.state == "queued":
                        run.status = "queued"
                    self._state.runs[run.run_id] = run
                    self._state.request_idempotency[request_id] = RuntimeRequestOutcome(
                        run_id=run.run_id,
                        thread_id=thread_id,
                        kind="prompt",
                        unattended=unattended,
                        web_search=web_search,
                        fingerprint=run.prompt_fingerprint,
                        status="accepted",
                        run_status=run.status,
                    )
                    self._leases[run.run_id] = lease
                    self._completion_events[run.run_id] = Event()
                    if admission is not None:
                        self._transfer_prompt_admission_locked(admission)
                except BaseException:
                    if run is None:
                        if admission is None:
                            lease.release()
                        else:
                            self._release_prompt_admission_locked(admission)
                    else:
                        self._rollback_submission_locked(run, lease)
                    raise
                try:
                    initial_events = [
                        EventDraft(
                            scope="thread",
                            thread_id=thread_id,
                            event_type="message.created",
                            payload={
                                "run_id": run.run_id,
                                "role": "user",
                                "text": prompt,
                                "client_request_id": request_id,
                            },
                        )
                    ]
                    if lease.state == "queued":
                        initial_events.append(
                            EventDraft(
                                scope="thread",
                                thread_id=thread_id,
                                event_type="run.queued",
                                payload={"run_id": run.run_id},
                            )
                        )
                    self._persist_locked(events=tuple(initial_events))
                    self._set_thread_projection_locked(run)
                    self._spawn_worker_locked(run.run_id)
                except RuntimeStateCommitUnknownError:
                    # The outbox may already own the accepted run and events.
                    # Fatal-state handling has released local capacity; a
                    # rollback write here could overwrite recoverable truth.
                    raise
                except BaseException:
                    self._rollback_submission_locked(run, lease)
                    raise
                return _run_record(run)

        codex_thread_id, turn_id, run_id, generation = steer
        assert codex_thread_id is not None
        try:
            try:
                result = self.app_server.request(
                    "turn/steer",
                    {
                        "threadId": codex_thread_id,
                        "expectedTurnId": turn_id,
                        "input": self._prompt_input(prompt, web_search),
                        "clientUserMessageId": request_id,
                    },
                    timeout_seconds=self.control_request_timeout_seconds,
                )
                validate_steer_result(result, turn_id)
            except Exception as exc:
                with self._lock:
                    # Serialize the generation abort with terminal persistence.
                    # Otherwise the watchdog can observe the new generation and
                    # publish run.interrupted before this path can pair it with
                    # run.steer_outcome_unknown in the same durable operation.
                    if generation is not None:
                        self.app_server.abort_generation(generation)
                    uncertain_event = EventDraft(
                        scope="thread",
                        thread_id=thread_id,
                        event_type="run.steer_outcome_unknown",
                        payload={
                            "run_id": run_id,
                            "error": "Codex may have received the follow-up.",
                        },
                    )
                    if generation is not None:
                        self._clear_queued_locked(
                            "follow-up outcome was unknown; app-server generation aborted"
                        )
                    current = self._state.runs.get(run_id)
                    if (
                        current is not None
                        and current.status not in _TERMINAL_RUN_STATES
                    ):
                        self._terminalize_locked(
                            current,
                            "interrupted",
                            "The Codex runtime restarted after a follow-up outcome became unknown.",
                            preceding_events=(uncertain_event,),
                        )
                    else:
                        self._persist_locked(events=(uncertain_event,))
                raise RuntimeSteerOutcomeUnknownError() from exc
            with self._lock:
                outcome = self._state.request_idempotency.get(request_id)
                if outcome is None or outcome.run_id != run_id:
                    raise RuntimeSteerOutcomeUnknownError()
                outcome.status = "accepted"
                current = self._state.runs.get(run_id)
                if current is not None:
                    outcome.run_status = current.status
                self._persist_locked(
                    events=(
                        EventDraft(
                            scope="thread",
                            thread_id=thread_id,
                            event_type="message.created",
                            payload={
                                "run_id": run_id,
                                "role": "user",
                                "text": prompt,
                                "client_request_id": request_id,
                                "steered": True,
                            },
                        ),
                    )
                )
                return (
                    _run_record(current)
                    if current is not None
                    else _outcome_record(outcome)
                )
        finally:
            with self._lock:
                self._finish_publication_locked(thread_id)

    def cancel_run(
        self,
        thread_id: str,
        *,
        run_id: str | None = None,
    ) -> RunRecord:
        self.storage.load_thread(thread_id)
        abort_without_turn = False
        with self._lock:
            run = self._find_cancellable_run_locked(thread_id, run_id)
            if run is None:
                raise TurnChangedError()
            if run.status == "queued":
                lease = self._leases.get(run.run_id)
                if lease is not None:
                    lease.cancel()
                self._terminalize_locked(
                    run, "cancelled", "The queued prompt was cancelled."
                )
                return _run_record(run)
            self._cancel_queued_for_thread_locked(thread_id, except_run_id=run.run_id)
            run.status = "cancelling"
            run.cancellation_requested_at = _now()
            interaction_events = self._expire_run_interactions_locked(run)
            if not run.codex_thread_id or not run.codex_turn_id:
                generation = run.generation
                self._persist_locked(events=interaction_events)
                if generation is None:
                    return _run_record(run)
                abort_without_turn = True
                codex_thread_id = None
                turn_id = None
            else:
                generation = run.generation
                codex_thread_id = run.codex_thread_id
                turn_id = run.codex_turn_id
                self._persist_locked(events=interaction_events)
            self._begin_publication_locked(thread_id)
        try:
            if abort_without_turn:
                assert generation is not None
                self.app_server.abort_generation(generation)
                with self._lock:
                    self._clear_queued_locked("cancelled start aborted the app-server")
                    current = self._state.runs.get(run.run_id)
                    if (
                        current is not None
                        and current.status not in _TERMINAL_RUN_STATES
                    ):
                        self._terminalize_locked(
                            current,
                            "cancelled",
                            "The Codex turn was cancelled before it started.",
                        )
                    return _run_record(current or run)
            assert codex_thread_id is not None
            assert turn_id is not None
            try:
                self.app_server.request(
                    "turn/interrupt",
                    {"threadId": codex_thread_id, "turnId": turn_id},
                )
            except Exception:
                if generation is not None:
                    self.app_server.abort_generation(generation)
                    with self._lock:
                        self._clear_queued_locked("app-server generation aborted")
            with self._lock:
                return _run_record(self._state.runs.get(run.run_id) or run)
        finally:
            with self._lock:
                self._finish_publication_locked(thread_id)

    def list_pending_interactions(
        self,
        *,
        thread_id: str | None = None,
    ) -> tuple[PendingInteractionRecord, ...]:
        with self._lock:
            self._expire_due_interactions_locked()
            values = [
                _public_interaction(item)
                for item in self._state.interactions.values()
                if item.status == "pending"
                and (thread_id is None or item.thread_id == thread_id)
            ]
        return tuple(
            sorted(values, key=lambda item: (item.event_id, item.interaction_id))
        )

    def pending_interactions(
        self,
        thread_id: str,
    ) -> tuple[PendingInteractionRecord, ...]:
        return self.list_pending_interactions(thread_id=thread_id)

    def decide_approval(
        self,
        interaction_id: str,
        *,
        thread_id: str,
        decision: str,
        client_request_id: str,
    ) -> InteractionResultRecord:
        if decision not in {"accept", "decline", "cancel"}:
            raise ValueError("approval decision is invalid")
        request_id = _identifier(
            client_request_id, limit=256, label="client request id"
        )
        response_fingerprint = _fingerprint(["decision", decision])
        with self._lock:
            interaction, request = self._claim_interaction_locked(
                interaction_id,
                thread_id=thread_id,
                client_request_id=request_id,
                response_fingerprint=response_fingerprint,
                expected_kinds={"command_approval", "file_change_approval"},
            )
            if interaction.status != "responding":
                return _interaction_result(interaction, request_id)
            self._begin_publication_locked(thread_id)
        try:
            try:
                self.app_server.respond(request, result={"decision": decision})
            except Exception as exc:
                with self._lock:
                    try:
                        self._mark_interaction_outcome_unknown_locked(
                            interaction_id,
                            client_request_id=request_id,
                            response_fingerprint=response_fingerprint,
                        )
                        if decision == "cancel":
                            current = self._state.interactions.get(interaction_id)
                            if current is not None:
                                self._mark_run_cancelling_for_interaction_locked(
                                    current
                                )
                    except RuntimeStateError:
                        pass
                raise InteractionOutcomeUnknownError() from exc
            with self._lock:
                interaction = self._state.interactions.get(interaction_id)
                if interaction is None:
                    raise InteractionOutcomeUnknownError()
                if interaction.status != "responding":
                    self._mark_interaction_outcome_unknown_locked(
                        interaction_id,
                        client_request_id=request_id,
                        response_fingerprint=response_fingerprint,
                    )
                    if decision == "cancel":
                        self._mark_run_cancelling_for_interaction_locked(interaction)
                    raise InteractionOutcomeUnknownError()
                terminal = {
                    "accept": "accepted",
                    "decline": "declined",
                    "cancel": "cancelled",
                }[decision]
                interaction.status = terminal  # type: ignore[assignment]
                interaction.display = None
                self._server_requests.pop(interaction_id, None)
                if decision == "cancel":
                    self._mark_run_cancelling_for_interaction_locked(interaction)
                self._compact_terminal_state_locked()
                try:
                    self._emit_interaction_resolved_locked(interaction)
                except RuntimeStateError as exc:
                    raise InteractionOutcomeUnknownError() from exc
                return _interaction_result(interaction, request_id)
        finally:
            with self._lock:
                self._finish_publication_locked(thread_id)

    def answer_user_input(
        self,
        interaction_id: str,
        *,
        thread_id: str,
        answers: list[dict[str, object]] | dict[str, list[str]],
        client_request_id: str,
    ) -> InteractionResultRecord:
        request_id = _identifier(
            client_request_id, limit=256, label="client request id"
        )
        response_fingerprint: str
        with self._lock:
            candidate = self._state.interactions.get(interaction_id)
            if candidate is None:
                raise InteractionNotFoundError()
            normalized = _normalize_answers(answers, candidate)
            response_fingerprint = _fingerprint(["answer", normalized])
            interaction, request = self._claim_interaction_locked(
                interaction_id,
                thread_id=thread_id,
                client_request_id=request_id,
                response_fingerprint=response_fingerprint,
                expected_kinds={"user_input"},
            )
            if interaction.status != "responding":
                return _interaction_result(interaction, request_id)
            result = {
                "answers": {
                    question_id: {"answers": values}
                    for question_id, values in normalized.items()
                }
            }
            self._begin_publication_locked(thread_id)
        try:
            try:
                self.app_server.respond(request, result=result)
            except Exception as exc:
                with self._lock:
                    try:
                        self._mark_interaction_outcome_unknown_locked(
                            interaction_id,
                            client_request_id=request_id,
                            response_fingerprint=response_fingerprint,
                        )
                    except RuntimeStateError:
                        pass
                raise InteractionOutcomeUnknownError() from exc
            with self._lock:
                interaction = self._state.interactions.get(interaction_id)
                if interaction is None:
                    raise InteractionOutcomeUnknownError()
                if interaction.status != "responding":
                    self._mark_interaction_outcome_unknown_locked(
                        interaction_id,
                        client_request_id=request_id,
                        response_fingerprint=response_fingerprint,
                    )
                    raise InteractionOutcomeUnknownError()
                interaction.status = "answered"
                interaction.display = None
                self._server_requests.pop(interaction_id, None)
                self._compact_terminal_state_locked()
                try:
                    self._emit_interaction_resolved_locked(interaction)
                except RuntimeStateError as exc:
                    raise InteractionOutcomeUnknownError() from exc
                return _interaction_result(interaction, request_id)
        finally:
            with self._lock:
                self._finish_publication_locked(thread_id)

    def close(self) -> None:
        browser_broker = self._browser_broker
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for admission in tuple(self._pending_prompt_admissions.values()):
                self._release_prompt_admission_locked(admission)
            generations: set[int] = set()
            for run in self._state.runs.values():
                if run.status in _TERMINAL_RUN_STATES:
                    continue
                if run.generation is not None:
                    generations.add(run.generation)
                try:
                    self._terminalize_locked(
                        run,
                        "interrupted",
                        "The Codex runtime stopped before the turn completed.",
                    )
                except RuntimeUnavailableError:
                    continue
            self._expire_all_interactions_locked()
            workers = tuple(self._workers)
        for generation in generations:
            self.app_server.abort_generation(generation)
        owner = current_thread()
        for worker in workers:
            if worker is not owner:
                worker.join(timeout=min(0.25, self.watchdog_interval_seconds * 2))
        if browser_broker is not None:
            try:
                browser_broker.close()
            except BaseException:
                pass

    def _run_worker(self, run_id: str) -> None:
        try:
            with self._lock:
                lease = self._leases.get(run_id)
                run = self._state.runs.get(run_id)
                if run is None:
                    return
                queue_wait_timeout = min(
                    self.queue_wait_timeout_seconds,
                    self._remaining_total_budget_locked(run),
                )
            if lease is None:
                return
            lease.wait_until_active(timeout_seconds=queue_wait_timeout)
            if not self._provider_admission_allowed():
                with self._lock:
                    run = self._state.runs.get(run_id)
                    if run is not None and run.status not in _TERMINAL_RUN_STATES:
                        self._terminalize_locked(
                            run,
                            "cancelled",
                            "The prompt was stopped while the ChatGPT account was changing.",
                        )
                return
            self._start_turn(run_id)
            self._watch_turn(run_id)
        except (RuntimeLeaseCancelledError, RuntimeLeaseTimeoutError):
            with self._lock:
                run = self._state.runs.get(run_id)
                if run is not None and run.status not in _TERMINAL_RUN_STATES:
                    self._terminalize_locked(
                        run, "cancelled", "The queued prompt expired."
                    )
        except _RuntimeTotalDeadlineExceeded:
            generation_to_abort: int | None = None
            with self._lock:
                expired = self._state.runs.get(run_id)
                if (
                    expired is not None
                    and expired.status not in _TERMINAL_RUN_STATES
                    and expired.turn_start_dispatched
                ):
                    generation_to_abort = expired.generation
            if generation_to_abort is not None:
                self.app_server.abort_generation(generation_to_abort)
            with self._lock:
                if generation_to_abort is not None:
                    self._clear_queued_locked("app-server generation aborted")
                run = self._state.runs.get(run_id)
                if run is not None and run.status not in _TERMINAL_RUN_STATES:
                    queued = run.status == "queued"
                    self._terminalize_locked(
                        run,
                        "cancelled" if queued else "failed",
                        (
                            "The queued prompt expired."
                            if queued
                            else "The Codex turn timed out."
                        ),
                    )
        except Exception:
            generation_to_abort: int | None = None
            with self._lock:
                failed = self._state.runs.get(run_id)
                if (
                    failed is not None
                    and failed.status not in _TERMINAL_RUN_STATES
                    and failed.turn_start_dispatched
                ):
                    generation_to_abort = failed.generation
            if generation_to_abort is not None:
                self.app_server.abort_generation(generation_to_abort)
            with self._lock:
                if generation_to_abort is not None:
                    self._clear_queued_locked(
                        "turn start outcome was unknown; app-server generation aborted"
                    )
                run = self._state.runs.get(run_id)
                if run is not None and run.status not in _TERMINAL_RUN_STATES:
                    self._terminalize_locked(
                        run, "failed", "Codex could not start the turn."
                    )
        finally:
            with self._lock:
                self._workers.discard(current_thread())

    def _provider_admission_allowed(self) -> bool:
        if self._provider_admission_check is None:
            return True
        try:
            return self._provider_admission_check() is True
        except Exception:
            return False

    def _prompt_admission_replay_locked(
        self,
        request_id: str,
        *,
        fingerprint: str,
        unattended: bool,
        web_search: Literal["live", "disabled"] | None,
    ) -> RunRecord | None:
        outcome = self._state.request_idempotency.get(request_id)
        if outcome is None:
            return None
        if (
            outcome.fingerprint != fingerprint
            or outcome.unattended != unattended
            or outcome.web_search != web_search
        ):
            raise RuntimeRequestConflictError()
        if outcome.status == "uncertain":
            raise RuntimeSteerOutcomeUnknownError()
        run = self._state.runs.get(outcome.run_id)
        return _run_record(run) if run is not None else _outcome_record(outcome)

    def _raise_for_pending_prompt_admission_locked(
        self,
        request_id: str,
        *,
        fingerprint: str,
        unattended: bool,
        web_search: Literal["live", "disabled"] | None,
    ) -> None:
        pending = self._pending_prompt_admissions.get(request_id)
        if pending is None:
            return
        if (
            pending._fingerprint != fingerprint
            or pending._unattended != unattended
            or pending._web_search != web_search
        ):
            raise RuntimeRequestConflictError()
        raise RuntimePromptPendingError()

    def _validate_prompt_admission_locked(
        self,
        admission: PromptAdmission | None,
        *,
        request_id: str,
        fingerprint: str,
        unattended: bool,
        web_search: Literal["live", "disabled"] | None,
    ) -> None:
        if admission is None:
            return
        if (
            admission._broker is not self
            or admission._state != "reserved"
            or self._pending_prompt_admissions.get(request_id) is not admission
            or admission._client_request_id != request_id
            or admission._fingerprint != fingerprint
            or admission._unattended != unattended
            or admission._web_search != web_search
            or admission._lease is None
            or admission._lease.state not in {"active", "queued"}
        ):
            raise RuntimeRequestConflictError()

    def _transfer_prompt_admission_locked(
        self,
        admission: PromptAdmission,
    ) -> None:
        if (
            admission._state != "reserved"
            or self._pending_prompt_admissions.get(admission._client_request_id)
            is not admission
        ):
            raise RuntimeRequestConflictError()
        self._pending_prompt_admissions.pop(admission._client_request_id, None)
        admission._state = "transferred"

    def _release_prompt_admission(self, admission: PromptAdmission) -> None:
        with self._lock:
            self._release_prompt_admission_locked(admission)

    def _release_prompt_admission_locked(
        self,
        admission: PromptAdmission,
    ) -> None:
        if admission._state != "reserved":
            return
        if (
            self._pending_prompt_admissions.get(admission._client_request_id)
            is admission
        ):
            self._pending_prompt_admissions.pop(admission._client_request_id, None)
        admission._state = "released"
        lease = admission._lease
        if lease is not None:
            # ``release`` is atomic for active and queued RuntimeGate leases;
            # unlike cancel it also closes an active owner that promoted while
            # cleanup raced the queue.
            lease.release()

    def _start_turn(self, run_id: str) -> None:
        with self._lock:
            run = self._state.runs[run_id]
            if run.status in _TERMINAL_RUN_STATES or self._closed:
                return
            if run.status == "cancelling":
                self._terminalize_locked(
                    run,
                    "cancelled",
                    "The queued turn was cancelled before it started.",
                )
                return
            thread_request_timeout = min(
                self.control_request_timeout_seconds,
                self._remaining_total_budget_locked(run),
            )
            run.status = "starting"
            run.started_at = _now()
            run.last_activity_at = run.started_at
            run.generation = self.app_server.generation
            generation = run.generation
            self._activity[run_id] = monotonic()
            self._persist_locked()
            thread = self.storage.get_thread(run.thread_id)
            workspace = self.storage.resolve_workspace_path(run.workspace_path)
            policy = mode_policy(run.mode, workspace)
            attachments_by_id = {
                attachment.attachment_id: attachment
                for attachment in thread.attachments
            }
            selected_attachments = [
                attachments_by_id[attachment_id]
                for attachment_id in run.attachment_ids
                if attachment_id in attachments_by_id
            ]
            if (
                len(selected_attachments) != len(run.attachment_ids)
                or _attachment_manifest(selected_attachments)
                != run.attachment_manifest_fingerprint
            ):
                raise WorkspaceBoundaryError()
            if selected_attachments:
                raise RuntimeAttachmentsUnavailableError()
            inputs = self._turn_input(run)

        image_generation_authority_revision: int | None = None
        authority = self._image_generation_authority
        if authority is not None:
            try:
                candidate_revision = authority.authorize_image_generation(
                    generation
                )
                if type(candidate_revision) is int and candidate_revision > 0:
                    image_generation_authority_revision = candidate_revision
            except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
                image_generation_authority_revision = None
        with self._lock:
            run = self._state.runs[run_id]
            if generation != self.app_server.generation:
                raise RuntimeUnavailableError()
            if run.status in _TERMINAL_RUN_STATES:
                return
            run.image_generation_authority_generation = (
                generation
                if image_generation_authority_revision is not None
                else None
            )
            run.image_generation_authority_revision = (
                image_generation_authority_revision
            )
            self._persist_locked()

        thread_config: dict[str, object] = {
            "default_permissions": policy.permission_profile,
            # Codex keeps thread configuration when resuming. Always send the
            # managed default so a prior live/disabled override cannot leak
            # into a request whose web_search is None.
            "web_search": run.web_search or _MANAGED_WEB_SEARCH_DEFAULT,
        }
        thread_params: dict[str, object] = {
            "cwd": str(workspace),
            "model": run.model,
            "approvalPolicy": policy.approval_policy,
            "approvalsReviewer": "user",
            "config": thread_config,
        }
        browser_tools_advertised = False
        if run.codex_thread_id:
            thread_params["threadId"] = run.codex_thread_id
            thread_result = self.app_server.request(
                "thread/resume",
                thread_params,
                timeout_seconds=thread_request_timeout,
            )
        else:
            thread_params["ephemeral"] = False
            # Client-owned namespace tools are attached at thread creation.
            # Codex does not retrofit them onto resumed threads, and doing so
            # would let restored durable state regain a browser authority that
            # was not recreated by this process.
            if not run.unattended and self._browser_tools_ready():
                thread_params["dynamicTools"] = [browser_dynamic_tool_spec()]
                browser_tools_advertised = True
            thread_result = self.app_server.request(
                "thread/start",
                thread_params,
                timeout_seconds=thread_request_timeout,
            )
        codex_thread_id = validate_thread_result(
            thread_result,
            expected_cwd=workspace,
            expected_model=run.model,
            policy=policy,
        )
        with self._lock:
            run = self._state.runs[run_id]
            if generation != self.app_server.generation:
                raise RuntimeUnavailableError()
            if run.status in _TERMINAL_RUN_STATES:
                return
            if run.codex_thread_id and run.codex_thread_id != codex_thread_id:
                raise RuntimeProtocolMismatchError()
            run.codex_thread_id = codex_thread_id
            if run.status == "cancelling":
                self._terminalize_locked(
                    run,
                    "cancelled",
                    "The Codex turn was cancelled before it started.",
                )
                return
            if browser_tools_advertised and self._browser_tools_ready():
                self._browser_pending_thread_authorities[run.run_id] = (
                    _BrowserThreadAuthority(
                        generation=generation,
                        codex_thread_id=codex_thread_id,
                    )
                )
            self._persist_locked()
            self._set_thread_projection_locked(run)

        with self._lock:
            run = self._state.runs[run_id]
            if run.status in _TERMINAL_RUN_STATES:
                return
            if generation != self.app_server.generation:
                raise RuntimeUnavailableError()

        with self._lock:
            run = self._state.runs[run_id]
            if run.status in _TERMINAL_RUN_STATES:
                return
            if generation != self.app_server.generation:
                raise RuntimeUnavailableError()
            turn_request_timeout = min(
                self.control_request_timeout_seconds,
                self._remaining_total_budget_locked(run),
            )
            run.turn_start_dispatched = True
            self._persist_locked()

        turn_result = self.app_server.request(
            "turn/start",
            {
                "threadId": codex_thread_id,
                "input": inputs,
                "clientUserMessageId": run.client_request_id,
                "cwd": str(workspace),
                "model": run.model,
                "effort": run.effort,
                "approvalPolicy": policy.approval_policy,
                "approvalsReviewer": "user",
            },
            timeout_seconds=turn_request_timeout,
        )
        turn_id, turn_status, turn = validate_turn_result(turn_result)
        with self._lock:
            run = self._state.runs[run_id]
            if run.status in _TERMINAL_RUN_STATES:
                return
            if generation != self.app_server.generation:
                raise RuntimeUnavailableError()
            if run.codex_turn_id and run.codex_turn_id != turn_id:
                raise RuntimeProtocolMismatchError()
            run.codex_turn_id = turn_id
            self._authorize_browser_turn_locked(run)
            buffered_callbacks = self._pre_response_callbacks.pop(run_id, [])
            if turn_status != "inProgress":
                items = turn.get("items")
                if isinstance(items, list):
                    for item in items:
                        self._emit_safe_item_locked(
                            run,
                            "item/completed",
                            {
                                "threadId": codex_thread_id,
                                "turnId": turn_id,
                                "item": item,
                            },
                        )
                self._handle_turn_completed_locked(run, turn)
            else:
                if run.status != "cancelling":
                    run.status = "running"
                run.last_activity_at = _now()
                self._activity[run_id] = monotonic()
                self._persist_locked()
                self._set_thread_projection_locked(run)
                self._emit_once_locked(
                    run,
                    "run.started",
                    {"run_id": run.run_id},
                )
            self._callback_replays_in_progress.add(run_id)

        self._replay_pre_response_callbacks(
            run_id,
            expected_turn_id=turn_id,
            callbacks=buffered_callbacks,
        )
        with self._lock:
            current = self._state.runs.get(run_id)
            if current is None or current.status in _TERMINAL_RUN_STATES:
                return
            interrupt_after_start = current.status == "cancelling"
        if interrupt_after_start:
            try:
                self.app_server.request(
                    "turn/interrupt",
                    {"threadId": codex_thread_id, "turnId": turn_id},
                )
            except Exception:
                self.app_server.abort_generation(generation)

    def _watch_turn(self, run_id: str) -> None:
        with self._lock:
            event = self._completion_events.get(run_id)
        if event is None:
            return
        started = monotonic()
        turn_deadline = started + self.turn_timeout_seconds
        while True:
            with self._lock:
                run = self._state.runs.get(run_id)
                if run is None or run.status in _TERMINAL_RUN_STATES:
                    return
                total_remaining = self._total_budget_remaining_locked(run)
            wait_seconds = max(
                0.0,
                min(
                    self.watchdog_interval_seconds,
                    turn_deadline - monotonic(),
                    total_remaining,
                ),
            )
            if event.wait(wait_seconds):
                return
            with self._lock:
                self._expire_due_interactions_locked()
                run = self._state.runs.get(run_id)
                if run is None or run.status in _TERMINAL_RUN_STATES:
                    return
                generation = run.generation
                last_activity = self._activity.get(run_id, started)
                cancelling = run.status == "cancelling"
                waiting_for_user = any(
                    interaction.run_id == run_id
                    and interaction.status in _PENDING_INTERACTION_STATES
                    for interaction in self._state.interactions.values()
                )
                cancel_at = _parse_time(run.cancellation_requested_at)
                total_timed_out = self._total_budget_remaining_locked(run) <= 0
            now = monotonic()
            if generation is not None and generation != self.app_server.generation:
                with self._lock:
                    self._reconcile_generation_locked(
                        self.app_server.generation,
                        reason="app-server generation changed",
                    )
                return
            timed_out = now >= turn_deadline or total_timed_out
            idle = (
                not waiting_for_user
                and now - last_activity >= self.limits.run_idle_timeout_seconds
            )
            cancel_expired = (
                cancelling
                and cancel_at is not None
                and datetime.now(UTC) - cancel_at
                >= timedelta(seconds=self.cancel_grace_seconds)
            )
            if generation is not None and (timed_out or idle or cancel_expired):
                self.app_server.abort_generation(generation)
                with self._lock:
                    self._clear_queued_locked("app-server generation aborted")
                    current = self._state.runs.get(run_id)
                    if (
                        current is not None
                        and current.status not in _TERMINAL_RUN_STATES
                    ):
                        status = "cancelled" if cancelling else "failed"
                        message = (
                            "The Codex turn was cancelled."
                            if cancelling
                            else "The Codex turn timed out."
                        )
                        self._terminalize_locked(current, status, message)
                return

    def _total_budget_remaining_locked(self, run: RuntimeRunState) -> float:
        deadline = _parse_time(run.total_deadline_at)
        if deadline is None:
            raise RuntimeStateError("The runtime deadline is missing or invalid.")
        return (deadline - datetime.now(UTC)).total_seconds()

    def _remaining_total_budget_locked(self, run: RuntimeRunState) -> float:
        remaining = self._total_budget_remaining_locked(run)
        if remaining <= 0:
            raise _RuntimeTotalDeadlineExceeded()
        return remaining

    def _on_notification(
        self,
        notification: AppServerNotification,
        *,
        replaying: bool = False,
    ) -> None:
        if notification.method == "serverRequest/resolved":
            if not replaying:
                with self._lock:
                    owner = self._pre_response_resolution_owner_locked(notification)
                    if owner is not None:
                        self._buffer_pre_response_callback_locked(
                            owner,
                            notification,
                        )
                        return
            self._on_server_request_resolved(notification)
            return
        params = notification.params
        if not isinstance(params, dict):
            return
        codex_thread_id = params.get("threadId")
        turn_id = params.get("turnId")
        turn = params.get("turn")
        if isinstance(turn, dict):
            turn_id = turn.get("id")
        image_publication: _GeneratedImagePublication | None = None
        with self._lock:
            run = self._correlated_run_locked(
                notification.generation,
                codex_thread_id,
                turn_id,
            )
            if run is None:
                self._buffer_pre_response_notification_locked(
                    notification,
                    codex_thread_id=codex_thread_id,
                    turn_id=turn_id,
                )
                return
            assert isinstance(turn_id, str)
            if not replaying and run.run_id in self._callback_replays_in_progress:
                self._buffer_pre_response_callback_locked(run, notification)
                return
            if notification.method == "item/completed":
                image_publication = self._begin_generated_image_publication_locked(
                    run,
                    params,
                    generation=notification.generation,
                    turn_id=turn_id,
                )
                item = params.get("item")
                if isinstance(item, dict) and item.get("type") == "imageGeneration":
                    if image_publication is None:
                        return
                else:
                    self._handle_correlated_notification_locked(
                        run, notification, turn_id
                    )
                    return
            else:
                self._handle_correlated_notification_locked(run, notification, turn_id)
                return
        assert image_publication is not None
        self._publish_generated_image_completion(image_publication)

    def _handle_correlated_notification_locked(
        self,
        run: RuntimeRunState,
        notification: AppServerNotification,
        turn_id: str,
    ) -> None:
        params = notification.params
        if not isinstance(params, dict):
            return
        run.last_activity_at = _now()
        self._activity[run.run_id] = monotonic()
        if notification.method == "turn/started":
            if run.codex_turn_id != turn_id:
                return
            if run.status != "cancelling":
                run.status = "running"
            self._persist_locked()
            self._set_thread_projection_locked(run)
            self._emit_once_locked(
                run,
                "run.started",
                {"run_id": run.run_id},
            )
            return
        if notification.method == "turn/completed":
            turn = params.get("turn")
            self._handle_turn_completed_locked(run, turn)
            return
        self._emit_runtime_notification_locked(run, notification.method, params)

    def _begin_generated_image_publication_locked(
        self,
        run: RuntimeRunState,
        params: dict[str, Any],
        *,
        generation: int,
        turn_id: str,
    ) -> _GeneratedImagePublication | None:
        item = params.get("item")
        if not isinstance(item, dict) or item.get("type") != "imageGeneration":
            return None
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id or len(item_id) > 256:
            return None
        key = (run.run_id, item_id)
        if item_id in run.completed_item_ids or key in self._inflight_image_items:
            return None
        if not isinstance(run.codex_thread_id, str):
            return None
        run.last_activity_at = _now()
        self._activity[run.run_id] = monotonic()
        payload: dict[str, object] = {
            "run_id": run.run_id,
            "item_id": item_id,
            "item_type": "imageGeneration",
        }
        payload.update(_safe_item_activity_metadata(item))
        self._inflight_image_items.add(key)
        self._begin_publication_locked(run.thread_id)
        status = item.get("status")
        if not isinstance(status, str) or len(status) > 32:
            status = None
        mime_type = item.get("mimeType", item.get("mime_type"))
        if not isinstance(mime_type, str) or len(mime_type) > 64:
            mime_type = None
        authorized = False
        authority_lease: object | None = None
        authority = self._image_generation_authority
        if (
            authority is not None
            and run.image_generation_authority_generation == generation
            and run.image_generation_authority_revision is not None
        ):
            try:
                acquire_lease = getattr(
                    authority,
                    "acquire_image_generation_publication_lease",
                    None,
                )
                if callable(acquire_lease):
                    candidate_lease = acquire_lease(
                        generation,
                        run.image_generation_authority_revision,
                    )
                    ensure_active = getattr(candidate_lease, "ensure_active", None)
                    release = getattr(candidate_lease, "release", None)
                    if callable(ensure_active) and callable(release):
                        authority_lease = candidate_lease
                        authorized = True
                else:
                    # Compatibility for constrained test/dummy authorities.
                    # Production uses the revocable lease above.
                    authorized = authority.is_image_generation_authorized(
                        generation,
                        run.image_generation_authority_revision,
                    )
            except (RuntimeError, OSError, ValueError, TypeError, AttributeError):
                authorized = False
        return _GeneratedImagePublication(
            run_id=run.run_id,
            thread_id=run.thread_id,
            generation=generation,
            codex_thread_id=run.codex_thread_id,
            turn_id=turn_id,
            item_id=item_id,
            status=status,
            result=item.get("result"),
            mime_type=mime_type,
            payload=payload,
            authorized=authorized,
            authority_lease=authority_lease,
        )

    def _publish_generated_image_completion(
        self,
        publication: _GeneratedImagePublication,
    ) -> None:
        artifact: ArtifactRecord | None = None
        rejected = (
            not publication.authorized
            or publication.status not in {"completed", "succeeded"}
        )
        failure: BaseException | None = None
        lease = publication.authority_lease
        ensure_active = getattr(lease, "ensure_active", None)
        release = getattr(lease, "release", None)
        try:
            if not rejected:
                try:
                    validate_generated_image_result(
                        publication.result,
                        publication.mime_type,
                    )
                    if callable(ensure_active):
                        ensure_active()
                    kwargs: dict[str, object] = {
                        "thread_id": publication.thread_id,
                        "item_id": publication.item_id,
                        "result": publication.result,
                        "mime_type": publication.mime_type,
                    }
                    if callable(ensure_active):
                        kwargs["persistence_guard"] = ensure_active
                    artifact = self.storage.save_generated_image(**kwargs)
                except (
                    WorkspaceBoundaryError,
                    OSError,
                    ValueError,
                    ResourceLimitError,
                    RuntimeError,
                ):
                    rejected = True
                except BaseException as exc:
                    failure = exc
        finally:
            if callable(release):
                release()

        with self._lock:
            try:
                if failure is None:
                    self._finish_generated_image_publication_locked(
                        publication,
                        artifact=artifact,
                        rejected=rejected,
                    )
            finally:
                self._inflight_image_items.discard(
                    (publication.run_id, publication.item_id)
                )
                self._finish_publication_locked(publication.thread_id)
        if failure is not None:
            raise failure

    def _finish_generated_image_publication_locked(
        self,
        publication: _GeneratedImagePublication,
        *,
        artifact: ArtifactRecord | None,
        rejected: bool,
    ) -> None:
        if self._closed or self._fatal_error:
            return
        run = self._state.runs.get(publication.run_id)
        if (
            run is None
            or run.thread_id != publication.thread_id
            or run.generation != publication.generation
            or run.codex_thread_id != publication.codex_thread_id
            or run.codex_turn_id != publication.turn_id
            or publication.item_id in run.completed_item_ids
        ):
            return
        payload = dict(publication.payload)
        if rejected or artifact is None:
            payload["status"] = "failed"
            payload["error"] = "image_result_rejected"
        else:
            payload.update(
                {
                    "artifact_id": artifact.artifact_id,
                    "mime_type": artifact.mime_type,
                    "size_bytes": artifact.size_bytes,
                }
            )
        if len(run.completed_item_ids) >= 4096:
            del run.completed_item_ids[0]
        run.completed_item_ids.append(publication.item_id)
        self._emit_once_locked(
            run,
            "item.completed",
            payload,
            source=payload,
        )

    def _buffer_pre_response_notification_locked(
        self,
        notification: AppServerNotification,
        *,
        codex_thread_id: object,
        turn_id: object,
    ) -> None:
        if not isinstance(codex_thread_id, str) or not isinstance(turn_id, str):
            return
        candidate = self._pre_response_candidate_locked(
            notification.generation,
            codex_thread_id,
        )
        if candidate is None:
            return
        self._buffer_pre_response_callback_locked(candidate, notification)

    def _pre_response_candidate_locked(
        self,
        generation: int,
        codex_thread_id: str,
    ) -> RuntimeRunState | None:
        return next(
            (
                run
                for run in self._state.runs.values()
                if run.status not in _TERMINAL_RUN_STATES
                and run.generation == generation
                and run.codex_thread_id == codex_thread_id
                and run.codex_turn_id is None
                and run.turn_start_dispatched
            ),
            None,
        )

    def _pre_response_resolution_owner_locked(
        self,
        notification: AppServerNotification,
    ) -> RuntimeRunState | None:
        params = notification.params
        if not isinstance(params, dict):
            return None
        request_id = params.get("requestId")
        codex_thread_id = params.get("threadId")
        if not isinstance(request_id, (str, int)) or not isinstance(
            codex_thread_id,
            str,
        ):
            return None
        key = (notification.generation, request_id)
        run_id = self._pre_response_request_owners.get(key)
        run = self._state.runs.get(run_id) if run_id is not None else None
        if (
            run is None
            or run.status in _TERMINAL_RUN_STATES
            or run.codex_thread_id != codex_thread_id
        ):
            if run is None or run.status in _TERMINAL_RUN_STATES:
                self._pre_response_request_owners.pop(key, None)
            return None
        return run

    def _buffer_pre_response_callback_locked(
        self,
        run: RuntimeRunState,
        callback: AppServerNotification | AppServerRequest,
    ) -> bool:
        buffered = self._pre_response_callbacks.setdefault(run.run_id, [])
        if len(buffered) < _MAX_PRE_RESPONSE_CALLBACKS:
            buffered.append(callback)
            if isinstance(callback, AppServerRequest):
                self._pre_response_request_owners[
                    (callback.generation, callback.request_id)
                ] = run.run_id
            return True
        generation = run.generation
        self._discard_pre_response_callbacks_locked(run.run_id)
        if generation is not None:
            self.app_server.abort_generation(generation)
        self._clear_queued_locked("ambiguous callback buffer overflow")
        self._terminalize_locked(
            run,
            "failed",
            "Codex sent too many callbacks before the turn was identified.",
        )
        return False

    def _discard_pre_response_callbacks_locked(self, run_id: str) -> None:
        callbacks = self._pre_response_callbacks.pop(run_id, [])
        for callback in callbacks:
            if isinstance(callback, AppServerRequest):
                self._pre_response_request_owners.pop(
                    (callback.generation, callback.request_id),
                    None,
                )
                try:
                    self.app_server.discard_server_request(
                        callback.request_id,
                        callback.generation,
                    )
                except Exception:
                    pass
        self._discard_pre_response_request_owners_locked(run_id)

    def _discard_pre_response_request_owners_locked(self, run_id: str) -> None:
        for key, owner_run_id in tuple(self._pre_response_request_owners.items()):
            if owner_run_id == run_id:
                self._pre_response_request_owners.pop(key, None)

    def _replay_pre_response_callbacks(
        self,
        run_id: str,
        *,
        expected_turn_id: str,
        callbacks: list[AppServerNotification | AppServerRequest],
    ) -> None:
        pending = callbacks
        try:
            while True:
                for callback in pending:
                    if isinstance(callback, AppServerNotification):
                        if (
                            callback.method == "serverRequest/resolved"
                            or _notification_turn_id(callback.params)
                            == expected_turn_id
                        ):
                            self._on_notification(callback, replaying=True)
                        continue
                    try:
                        result = self._on_server_request(callback, replaying=True)
                    finally:
                        with self._lock:
                            self._pre_response_request_owners.pop(
                                (callback.generation, callback.request_id),
                                None,
                            )
                    if result is DEFERRED_RESPONSE:
                        continue
                    try:
                        self.app_server.respond(callback, result=result)
                    except Exception:
                        self.app_server.abort_generation(callback.generation)
                        with self._lock:
                            self._clear_queued_locked("buffered response write failed")
                            run = self._state.runs.get(run_id)
                            if (
                                run is not None
                                and run.status not in _TERMINAL_RUN_STATES
                            ):
                                self._terminalize_locked(
                                    run,
                                    "failed",
                                    "Codex could not receive a required response.",
                                )
                        return
                with self._lock:
                    pending = self._pre_response_callbacks.pop(run_id, [])
                    if not pending:
                        self._callback_replays_in_progress.discard(run_id)
                        return
        finally:
            with self._lock:
                self._callback_replays_in_progress.discard(run_id)
                self._discard_pre_response_callbacks_locked(run_id)

    def _emit_runtime_notification_locked(
        self,
        run: RuntimeRunState,
        method: str,
        params: dict[str, Any],
    ) -> None:
        mapping = {
            "item/agentMessage/delta": ("message.delta", "delta", False),
            "item/reasoning/summaryPartAdded": (
                "reasoning.summary_part",
                None,
                True,
            ),
            "item/reasoning/summaryTextDelta": (
                "reasoning.summary_delta",
                "delta",
                False,
            ),
            "item/reasoning/textDelta": ("reasoning.delta", "delta", False),
            "item/plan/delta": ("plan.delta", "delta", False),
        }
        if method in mapping:
            event_type, field, deduplicate = mapping[method]
            payload: dict[str, object] = {"run_id": run.run_id}
            if field is not None:
                value = params.get(field)
                if not isinstance(value, str):
                    return
            for name in ("itemId", "summaryIndex", "contentIndex"):
                value = params.get(name)
                if isinstance(value, (str, int)):
                    payload[_snake(name)] = value
            if field is None:
                self._emit_once_locked(run, event_type, payload, source=params)
                return
            output_field = "text" if event_type == "message.delta" else field
            byte_offset = 0
            for chunk_index, chunk in enumerate(_raw_chunks(params[field])):
                chunk_payload = {
                    **payload,
                    output_field: chunk,
                    "chunk_index": chunk_index,
                    "byte_offset": byte_offset,
                }
                self._emit_once_locked(
                    run,
                    event_type,
                    chunk_payload,
                    source=params,
                    deduplicate=deduplicate,
                )
                byte_offset += len(chunk.encode("utf-8"))
            return
        if method == "turn/plan/updated":
            plan = params.get("plan")
            if not isinstance(plan, list) or len(plan) > 128:
                return
            safe_plan = []
            for item in plan:
                if not isinstance(item, dict):
                    return
                step = bounded_text(item.get("step"), 2048)
                status = item.get("status")
                if step is None or status not in {"pending", "inProgress", "completed"}:
                    return
                safe_plan.append({"step": step, "status": status})
            self._emit_once_locked(
                run,
                "plan.updated",
                {"run_id": run.run_id, "plan": safe_plan},
                source=params,
            )
            return
        if method == "turn/diff/updated":
            diff = bounded_raw_text(
                params.get("diff"), self.limits.max_event_payload_bytes // 2
            )
            if diff:
                self._emit_once_locked(
                    run,
                    "diff.updated",
                    {"run_id": run.run_id, "diff": diff},
                    source=params,
                )
            return
        if method == "item/fileChange/patchUpdated":
            self._emit_safe_patch_locked(run, params)
            return
        if method in {"item/started", "item/completed"}:
            self._emit_safe_item_locked(run, method, params)
            return
        if method == "error":
            self._emit_once_locked(
                run,
                "run.warning",
                {"run_id": run.run_id, "message": "Codex reported a runtime error."},
                source=params,
            )

    def _browser_tools_ready(self) -> bool:
        if not self._browser_dynamic_tools_enabled:
            return False
        broker = self._browser_broker
        if broker is None or not callable(getattr(broker, "close_owner", None)):
            return False
        try:
            return broker.ready is True
        except BaseException:
            return False

    def _authorize_browser_turn_locked(self, run: RuntimeRunState) -> None:
        pending = self._browser_pending_thread_authorities.pop(run.run_id, None)
        if (
            pending is None
            or not self._browser_tools_ready()
            or run.generation != pending.generation
            or run.codex_thread_id != pending.codex_thread_id
            or not isinstance(run.codex_turn_id, str)
        ):
            return
        self._browser_turn_authorities[run.run_id] = BrowserInvocationContext(
            run_id=run.run_id,
            thread_id=run.thread_id,
            codex_thread_id=pending.codex_thread_id,
            turn_id=run.codex_turn_id,
            generation=pending.generation,
        )
        self._browser_tool_replays[run.run_id] = {}

    def _revoke_browser_turn_locked(self, run_id: str) -> None:
        self._browser_pending_thread_authorities.pop(run_id, None)
        self._browser_tool_replays.pop(run_id, None)
        authority = self._browser_turn_authorities.pop(run_id, None)
        if authority is None:
            return
        broker = self._browser_broker
        close_owner = None if broker is None else getattr(broker, "close_owner", None)
        if not callable(close_owner):
            return
        try:
            close_owner(authority)
        except BaseException:
            # Authority is revoked even if a dead local worker cannot confirm
            # profile deletion. The worker's process lifecycle performs final
            # cleanup on exit.
            return

    def _on_server_request(
        self,
        request: AppServerRequest,
        *,
        replaying: bool = False,
    ) -> object:
        # Dynamic tool calls have their own exact correlation shape. Do this
        # before the approval parser: a browser callback has no itemId and may
        # never enter the deferred user-interaction state machine.
        if request.method == "item/tool/call":
            return self._on_browser_tool_call(request)
        if request.method in {"execCommandApproval", "applyPatchApproval"}:
            return {"decision": "denied"}
        params = request.params
        correlation = interaction_correlation(params)
        if not isinstance(params, dict) or correlation is None:
            return _automatic_denial(request.method, params)
        codex_thread_id, turn_id, item_id = correlation
        with self._lock:
            run = self._correlated_run_locked(
                request.generation,
                codex_thread_id,
                turn_id,
            )
            if run is None:
                candidate = self._pre_response_candidate_locked(
                    request.generation,
                    codex_thread_id,
                )
                if candidate is not None and self._buffer_pre_response_callback_locked(
                    candidate,
                    request,
                ):
                    return DEFERRED_RESPONSE
                return _automatic_denial(request.method, params)
            if not replaying and run.run_id in self._callback_replays_in_progress:
                if self._buffer_pre_response_callback_locked(run, request):
                    return DEFERRED_RESPONSE
                return _automatic_denial(request.method, params)
            if run.status != "running":
                return _automatic_denial(request.method, params)
            if run.unattended:
                return _automatic_denial(request.method, params)
            workspace = self.storage.resolve_workspace_path(run.workspace_path)
            if request.method == "item/permissions/requestApproval":
                return {"permissions": {}, "scope": "turn"}
            if request.method == "item/tool/requestUserInput":
                display = question_display(params)
                if display is None:
                    return _empty_answers(params)
                kind: Literal[
                    "command_approval",
                    "file_change_approval",
                    "user_input",
                ] = "user_input"
                allowed_actions: list[
                    Literal["accept", "decline", "cancel", "answer"]
                ] = ["answer"]
            else:
                if run.mode is RunMode.FULL_AUTO:
                    return _automatic_denial(request.method, params)
                projected = approval_display(
                    request.method,
                    params,
                    expected_cwd=workspace,
                )
                if projected is None:
                    return _automatic_denial(request.method, params)
                kind, display = projected
                if run.mode is RunMode.OBSERVE and kind == "file_change_approval":
                    return {"decision": "decline"}
                if kind == "file_change_approval":
                    paths = self._item_paths.get((run.run_id, item_id), [])
                    if not paths:
                        return {"decision": "decline"}
                    display.workspace_paths = paths
                else:
                    display.workspace_paths = approval_workspace_paths(
                        params,
                        workspace=workspace,
                    )
                allowed_actions = ["accept", "decline", "cancel"]
            run.last_activity_at = _now()
            self._activity[run.run_id] = monotonic()
            now = datetime.now(UTC)
            interaction = RuntimeInteractionState(
                interaction_id=f"int_{uuid4().hex[:16]}",
                kind=kind,
                thread_id=run.thread_id,
                run_id=run.run_id,
                codex_thread_id=codex_thread_id,
                turn_id=turn_id,
                item_id=item_id,
                generation=request.generation,
                app_request_id=request.request_id,
                display=display,
                allowed_actions=allowed_actions,
                created_at=now.isoformat(),
                expires_at=(
                    now + timedelta(seconds=self.interaction_timeout_seconds)
                ).isoformat(),
            )
            self._state.interactions[interaction.interaction_id] = interaction
            self._server_requests[interaction.interaction_id] = request
            events = self._persist_locked(
                events=(
                    EventDraft(
                        scope="thread",
                        thread_id=run.thread_id,
                        event_type="interaction.created",
                        payload=_public_interaction(interaction).model_dump(
                            mode="json"
                        ),
                    ),
                )
            )
            interaction.event_id = events[0].scope_sequence
            self._persist_locked()
            return DEFERRED_RESPONSE

    def _on_browser_tool_call(self, request: AppServerRequest) -> dict[str, object]:
        parsed = _browser_tool_call_params(request.params)
        if parsed is None:
            return _browser_tool_rejection()
        codex_thread_id, turn_id, call_id, tool, arguments, fingerprint = parsed
        with self._lock:
            run = self._correlated_run_locked(
                request.generation,
                codex_thread_id,
                turn_id,
            )
            authority = None if run is None else self._browser_turn_authorities.get(
                run.run_id
            )
            if (
                run is None
                or authority is None
                or authority.generation != request.generation
                or authority.codex_thread_id != codex_thread_id
                or authority.turn_id != turn_id
                or run.status != "running"
                or run.unattended
                or not self._browser_tools_ready()
            ):
                return _browser_tool_rejection()
            broker = self._browser_broker
            if broker is None:
                return _browser_tool_rejection()
            replays = self._browser_tool_replays.get(run.run_id)
            if replays is None:
                return _browser_tool_rejection()
            replay = replays.get(call_id)
            if replay is not None:
                if replay.fingerprint != fingerprint or replay.result is None:
                    return _browser_tool_rejection()
                return deepcopy(replay.result)
            if len(replays) >= _MAX_BROWSER_TOOL_REPLAYS_PER_TURN:
                return _browser_tool_rejection()
            # Reserve the call ID before releasing the runtime lock. If the
            # worker crashes after a side effect, a retry must not execute it
            # again merely because the first response was lost.
            replays[call_id] = _BrowserToolReplay(fingerprint=fingerprint)

        # Browser actions can wait for a page response. Never retain the
        # global runtime lock while the private worker is active.
        try:
            result = _safe_browser_tool_result(
                broker.invoke(authority, tool, arguments)
            )
        except BaseException:
            result = _browser_tool_rejection()

        with self._lock:
            current = self._state.runs.get(authority.run_id)
            if (
                current is None
                or current.status != "running"
                or current.unattended
                or current.generation != request.generation
                or current.codex_thread_id != codex_thread_id
                or current.codex_turn_id != turn_id
                or self._browser_turn_authorities.get(authority.run_id) != authority
            ):
                return _browser_tool_rejection()
            replay = self._browser_tool_replays.get(authority.run_id, {}).get(call_id)
            if replay is None or replay.fingerprint != fingerprint:
                return _browser_tool_rejection()
            replay.result = deepcopy(result)
            current.last_activity_at = _now()
            self._activity[current.run_id] = monotonic()
        return deepcopy(result)

    def _on_server_request_resolved(self, notification: AppServerNotification) -> None:
        params = notification.params
        if not isinstance(params, dict):
            return
        request_id = params.get("requestId")
        codex_thread_id = params.get("threadId")
        if not isinstance(request_id, (str, int)) or not isinstance(
            codex_thread_id, str
        ):
            return
        with self._lock:
            for interaction in self._state.interactions.values():
                if (
                    interaction.generation == notification.generation
                    and interaction.app_request_id == request_id
                    and interaction.codex_thread_id == codex_thread_id
                    and interaction.status in _PENDING_INTERACTION_STATES
                ):
                    interaction.status = (
                        "outcome_unknown"
                        if interaction.status == "responding"
                        else "expired"
                    )
                    interaction.display = None
                    self._server_requests.pop(interaction.interaction_id, None)
                    self.app_server.discard_server_request(
                        request_id,
                        notification.generation,
                    )
                    self._persist_locked(
                        events=(
                            EventDraft(
                                scope="thread",
                                thread_id=interaction.thread_id,
                                event_type=(
                                    "interaction.outcome_unknown"
                                    if interaction.status == "outcome_unknown"
                                    else "interaction.expired"
                                ),
                                payload={"interaction_id": interaction.interaction_id},
                            ),
                        )
                    )
                    return

    def _claim_interaction_locked(
        self,
        interaction_id: str,
        *,
        thread_id: str,
        client_request_id: str,
        response_fingerprint: str,
        expected_kinds: set[str] | None = None,
    ) -> tuple[RuntimeInteractionState, AppServerRequest]:
        interaction = self._state.interactions.get(interaction_id)
        if interaction is None:
            raise InteractionNotFoundError()
        if interaction.thread_id != thread_id:
            raise TurnChangedError()
        if expected_kinds is not None and interaction.kind not in expected_kinds:
            raise TurnChangedError()
        if interaction.response_client_request_id is not None:
            if (
                interaction.response_client_request_id == client_request_id
                and interaction.response_fingerprint == response_fingerprint
            ):
                if interaction.status in {"responding", "outcome_unknown"}:
                    raise InteractionOutcomeUnknownError()
                return interaction, self._server_requests.get(
                    interaction_id,
                    AppServerRequest(
                        request_id=interaction.app_request_id,
                        method="item/tool/requestUserInput",
                        params={},
                        generation=interaction.generation,
                    ),
                )
            if interaction.response_client_request_id == client_request_id:
                raise RuntimeRequestConflictError()
            raise InteractionResolvedError()
        run = self._state.runs.get(interaction.run_id)
        if run is None or run.status != "running":
            raise InteractionStaleError()
        self._expire_due_interactions_locked()
        if interaction.status != "pending":
            if interaction.status == "expired":
                raise InteractionStaleError()
            if interaction.status == "outcome_unknown":
                raise InteractionOutcomeUnknownError()
            raise InteractionResolvedError()
        if interaction.generation != self.app_server.generation:
            raise InteractionStaleError()
        request = self._server_requests.get(interaction_id)
        if request is None:
            raise InteractionStaleError()
        run.last_activity_at = _now()
        self._activity[run.run_id] = monotonic()
        interaction.status = "responding"
        interaction.response_client_request_id = client_request_id
        interaction.response_fingerprint = response_fingerprint
        self._persist_locked()
        return interaction, request

    def _mark_interaction_outcome_unknown_locked(
        self,
        interaction_id: str,
        *,
        client_request_id: str,
        response_fingerprint: str,
    ) -> None:
        interaction = self._state.interactions.get(interaction_id)
        if interaction is None:
            return
        if (
            interaction.response_client_request_id != client_request_id
            or interaction.response_fingerprint != response_fingerprint
        ):
            return
        interaction.status = "outcome_unknown"
        interaction.display = None
        self._server_requests.pop(interaction_id, None)
        self._compact_terminal_state_locked()
        self._persist_locked()

    def _reconcile_generation_locked(self, generation: int, *, reason: str) -> None:
        previous = self._state.observed_app_server_generation
        self._state.observed_app_server_generation = generation
        if (
            previous == generation
            and not any(
                run.status not in _TERMINAL_RUN_STATES
                for run in self._state.runs.values()
            )
            and not any(
                interaction.status in _PENDING_INTERACTION_STATES
                for interaction in self._state.interactions.values()
            )
        ):
            self._persist_locked()
            return
        interrupted = [
            run
            for run in self._state.runs.values()
            if run.status not in _TERMINAL_RUN_STATES
        ]
        self._expire_all_interactions_locked(reason=reason)
        for run in interrupted:
            was_queued = run.status == "queued"
            if run.status not in _TERMINAL_RUN_STATES:
                preceding_events = (
                    (
                        EventDraft(
                            scope="thread",
                            thread_id=run.thread_id,
                            event_type="run.queue_cleared",
                            payload={"run_id": run.run_id, "reason": reason},
                        ),
                    )
                    if was_queued
                    else ()
                )
                self._terminalize_locked(
                    run,
                    "interrupted",
                    "The Codex runtime restarted before the turn completed.",
                    preceding_events=preceding_events,
                )
        self._persist_locked()

    def _handle_turn_completed_locked(
        self,
        run: RuntimeRunState,
        turn: object,
    ) -> None:
        if not isinstance(turn, dict):
            return
        status = turn.get("status")
        if status == "completed":
            self._terminalize_locked(run, "completed", None)
        elif status == "interrupted":
            terminal = "cancelled" if run.status == "cancelling" else "interrupted"
            self._terminalize_locked(run, terminal, "The Codex turn was interrupted.")
        elif status == "failed":
            info = turn.get("error")
            classification = (
                info.get("codexErrorInfo") if isinstance(info, dict) else None
            )
            failure = _safe_failure(classification)
            self._terminalize_locked(run, "failed", failure.message, failure=failure)

    def _terminalize_locked(
        self,
        run: RuntimeRunState,
        status: str,
        message: str | None,
        *,
        failure: _SafeFailure | None = None,
        preceding_events: tuple[EventDraft, ...] = (),
    ) -> None:
        if run.status in _TERMINAL_RUN_STATES:
            return
        self._revoke_browser_turn_locked(run.run_id)
        run.status = status  # type: ignore[assignment]
        run.prompt = None
        run.terminal_message = message
        run.last_activity_at = _now()
        for outcome in self._state.request_idempotency.values():
            if outcome.run_id == run.run_id:
                outcome.run_status = run.status
        for interaction in self._state.interactions.values():
            if (
                interaction.run_id == run.run_id
                and interaction.status in _PENDING_INTERACTION_STATES
            ):
                interaction.status = (
                    "outcome_unknown"
                    if interaction.status == "responding"
                    else "expired"
                )
                interaction.display = None
                request = self._server_requests.pop(interaction.interaction_id, None)
                if request is not None:
                    self.app_server.discard_server_request(
                        request.request_id,
                        request.generation,
                    )
        event_type = {
            "completed": "run.completed",
            "cancelled": "run.cancelled",
            "interrupted": "run.interrupted",
            "failed": "run.failed",
        }[status]
        payload: dict[str, object] = {"run_id": run.run_id}
        if message:
            payload["error" if status == "failed" else "message"] = message
        if status == "failed" and failure is not None:
            payload["failure_type"] = failure.failure_type
            if failure.blocked:
                payload["blocked"] = True
            if failure.auth_required:
                payload["auth_required"] = True
        run.emitted_signatures = []
        run.completed_item_ids = []
        self._compact_terminal_state_locked()
        persistence_error: RuntimeStateError | None = None
        with self.storage._thread_mutation_lock:
            projection = self._thread_projection_record_locked(run)
            projection_payloads = (
                (
                    (
                        f"threads/{projection.thread_id}.json",
                        projection.model_dump(mode="json"),
                    ),
                )
                if projection is not None
                else ()
            )
            try:
                # Runtime ownership, the terminal UI projection, and the
                # public event recover as one operation after a crash.
                self._emit_once_locked(
                    run,
                    event_type,
                    payload,
                    preceding_events=preceding_events,
                    state_payloads=projection_payloads,
                )
            except RuntimeStateError as exc:
                persistence_error = exc
        # Capacity becomes observable as free before the terminal thread
        # projection is published. Consumers must never see a terminal run
        # while the global gate still counts it as active.
        lease = self._leases.pop(run.run_id, None)
        if lease is not None:
            lease.release()
        self._schedule_terminal_home_assistant_artifact_reconciliation_locked(
            run.thread_id
        )
        event = self._completion_events.pop(run.run_id, None)
        if event is not None:
            event.set()
        self._activity.pop(run.run_id, None)
        self._discard_pre_response_callbacks_locked(run.run_id)
        for key in [key for key in self._item_paths if key[0] == run.run_id]:
            self._item_paths.pop(key, None)
        if persistence_error is not None:
            self._fatal_error = True
            if run.generation is not None:
                self.app_server.abort_generation(run.generation)
            raise RuntimeUnavailableError() from persistence_error

        if status == "failed" and failure is not None and failure.blocked:
            try:
                self.storage.mark_limits_blocked(failure.message)
            except Exception:
                # A secondary limits projection must not erase the durable
                # terminal turn result or turn an account-limit response into
                # a raw storage error.
                pass

        if self._run_terminal_listener is not None:
            try:
                self._run_terminal_listener(
                    run.run_id,
                    status,
                    run.client_request_id,
                    run.unattended,
                )
            except Exception:
                # Automation bookkeeping must never compromise the canonical
                # runtime terminal transition or expose its private failure.
                pass

    def _schedule_terminal_home_assistant_artifact_reconciliation_locked(
        self,
        thread_id: str,
    ) -> None:
        if (
            self._closed
            or self.storage.runtime_profile is not RuntimeProfile.HOME_ASSISTANT
        ):
            return
        self._pending_artifact_reconciliations[thread_id] = (
            self._pending_artifact_reconciliations.get(thread_id, 0) + 1
        )
        if self._artifact_reconciliation_worker is not None:
            return
        worker = Thread(
            target=self._reconcile_terminal_home_assistant_artifacts,
            name="CodexArtifactReconcile",
            daemon=True,
        )
        self._artifact_reconciliation_worker = worker
        self._workers.add(worker)
        worker.start()

    def _reconcile_terminal_home_assistant_artifacts(self) -> None:
        """Persist terminal workspace outputs without changing run outcome."""
        try:
            # The caller holds this lock while terminalizing.  Cross this
            # barrier before scanning so up to 20,000 workspace entries never
            # stall prompt admission or notification handling.
            with self._lock:
                if self._closed:
                    self._pending_artifact_reconciliations.clear()
                    self._artifact_reconciliation_worker = None
                    self._workers.discard(current_thread())
                    return
            while True:
                with self._lock:
                    if self._closed:
                        self._pending_artifact_reconciliations.clear()
                        self._artifact_reconciliation_worker = None
                        self._workers.discard(current_thread())
                        return
                    try:
                        thread_id, revision = next(
                            iter(self._pending_artifact_reconciliations.items())
                        )
                    except StopIteration:
                        self._artifact_reconciliation_worker = None
                        self._workers.discard(current_thread())
                        return
                try:
                    self.storage.sync_thread_artifacts(thread_id)
                except (
                    ThreadNotFoundError,
                    WorkspaceBoundaryError,
                    ResourceLimitError,
                    OSError,
                ):
                    # The terminal event and lease release have already
                    # committed. A later artifact-list retry safely reconciles
                    # transient storage contention without turning a
                    # successful chat into a runtime connection failure.
                    pass
                with self._lock:
                    if (
                        self._pending_artifact_reconciliations.get(thread_id)
                        == revision
                    ):
                        self._pending_artifact_reconciliations.pop(thread_id, None)
        finally:
            with self._lock:
                if self._artifact_reconciliation_worker is current_thread():
                    self._artifact_reconciliation_worker = None
                self._workers.discard(current_thread())

    def _thread_projection_record_locked(
        self,
        run: RuntimeRunState,
    ) -> ThreadRecord | None:
        try:
            record = self.storage.load_thread(run.thread_id)
        except (ThreadNotFoundError, WorkspaceBoundaryError):
            return None
        record.codex_thread_id = run.codex_thread_id
        record.active_turn_id = (
            run.codex_turn_id if run.status not in _TERMINAL_RUN_STATES else None
        )
        record.pending_prompts = []
        if run.status == "queued":
            record.status = "queued"
            record.active_run_id = None
        elif run.status in {"starting", "running", "cancelling"}:
            record.status = "running"
            record.active_run_id = run.run_id
        elif run.status in {"failed", "interrupted"}:
            record.status = "error"
            record.active_run_id = None
            record.last_error = run.terminal_message or "The Codex turn failed."
        else:
            record.status = "idle"
            record.active_run_id = None
            record.last_error = None
        return record

    def _set_thread_projection_locked(self, run: RuntimeRunState) -> None:
        record = self._thread_projection_record_locked(run)
        if record is None:
            return
        self.storage.save_thread(record)

    def _emit_once_locked(
        self,
        run: RuntimeRunState,
        event_type: str,
        payload: dict[str, object],
        *,
        source: object | None = None,
        deduplicate: bool = True,
        preceding_events: tuple[EventDraft, ...] = (),
        state_payloads: tuple[tuple[str, dict[str, object]], ...] = (),
    ) -> None:
        signature: str | None = None
        if deduplicate:
            signature_payload = source if source is not None else payload
            signature = hashlib.sha256(
                json.dumps(
                    [event_type, signature_payload],
                    sort_keys=True,
                    default=str,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if signature in run.emitted_signatures:
                return
            run.emitted_signatures.append(signature)
            if len(run.emitted_signatures) > 2048:
                del run.emitted_signatures[: len(run.emitted_signatures) - 2048]
        try:
            self._persist_locked(
                events=(
                    *preceding_events,
                    EventDraft(
                        scope="thread",
                        thread_id=run.thread_id,
                        event_type=event_type,
                        payload=payload,
                    ),
                ),
                state_payloads=state_payloads,
            )
        except BaseException:
            if signature is not None and signature in run.emitted_signatures:
                run.emitted_signatures.remove(signature)
            raise

    def _emit_safe_patch_locked(
        self,
        run: RuntimeRunState,
        params: dict[str, Any],
    ) -> None:
        changes = params.get("changes")
        if not isinstance(changes, list) or len(changes) > 256:
            return
        workspace = self.storage.resolve_workspace_path(run.workspace_path)
        safe = []
        for change in changes:
            if not isinstance(change, dict):
                return
            path = normalize_workspace_path(change.get("path"), workspace)
            diff = bounded_raw_text(change.get("diff"), 64 * 1024)
            kind = change.get("kind")
            kind_type = kind.get("type") if isinstance(kind, dict) else None
            if (
                path is None
                or diff is None
                or kind_type
                not in {
                    "add",
                    "delete",
                    "update",
                }
            ):
                return
            safe_change: dict[str, object] = {
                "path": path,
                "diff": diff,
                "kind": kind_type,
            }
            move_path = kind.get("move_path") if isinstance(kind, dict) else None
            if move_path is not None:
                normalized_move = normalize_workspace_path(move_path, workspace)
                if normalized_move is None:
                    return
                safe_change["move_path"] = normalized_move
            safe.append(safe_change)
        item_id = params.get("itemId")
        if isinstance(item_id, str):
            approval_paths: list[str] = []
            for change in safe:
                for field in ("path", "move_path"):
                    path = change.get(field)
                    if isinstance(path, str) and path not in approval_paths:
                        approval_paths.append(path)
            self._item_paths[(run.run_id, item_id)] = approval_paths
        payloads = _partition_patch_payloads(
            run_id=run.run_id,
            changes=safe,
            maximum_bytes=self.limits.max_event_payload_bytes,
        )
        if not payloads:
            return
        signature = hashlib.sha256(
            json.dumps(
                ["patch.updated", params],
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if signature in run.emitted_signatures:
            return
        run.emitted_signatures.append(signature)
        try:
            self._persist_locked(
                events=tuple(
                    EventDraft(
                        scope="thread",
                        thread_id=run.thread_id,
                        event_type="patch.updated",
                        payload=payload,
                    )
                    for payload in payloads
                )
            )
        except BaseException:
            if signature in run.emitted_signatures:
                run.emitted_signatures.remove(signature)
            raise
        if len(run.emitted_signatures) > 2048:
            del run.emitted_signatures[: len(run.emitted_signatures) - 2048]

    def _emit_safe_item_locked(
        self,
        run: RuntimeRunState,
        method: str,
        params: dict[str, Any],
    ) -> None:
        item = params.get("item")
        if not isinstance(item, dict):
            return
        item_id = item.get("id")
        item_type = item.get("type")
        if not isinstance(item_id, str) or not isinstance(item_type, str):
            return
        if method == "item/completed" and item_id in run.completed_item_ids:
            return
        payload: dict[str, object] = {
            "run_id": run.run_id,
            "item_id": item_id,
            "item_type": item_type,
        }
        payload.update(_safe_item_activity_metadata(item))
        if item_type == "agentMessage":
            text = bounded_raw_text(
                item.get("text"), self.limits.max_event_payload_bytes // 2
            )
            if text is not None:
                payload["role"] = "assistant"
                payload["text"] = text
        if method == "item/completed":
            if len(run.completed_item_ids) >= 4096:
                del run.completed_item_ids[0]
            run.completed_item_ids.append(item_id)
        if method == "item/started":
            event_type = "item.started"
        elif item_type == "agentMessage":
            event_type = "message.completed"
        else:
            event_type = "item.completed"
        self._emit_once_locked(
            run,
            event_type,
            payload,
            source=payload,
        )

    def _emit_interaction_resolved_locked(
        self,
        interaction: RuntimeInteractionState,
    ) -> None:
        self._persist_locked(
            events=(
                EventDraft(
                    scope="thread",
                    thread_id=interaction.thread_id,
                    event_type="interaction.resolved",
                    payload={
                        "interaction_id": interaction.interaction_id,
                        "status": interaction.status,
                    },
                ),
            )
        )

    def _turn_input(
        self,
        run: RuntimeRunState,
    ) -> list[dict[str, object]]:
        if run.prompt is None:
            raise RuntimeStateError("The queued Codex prompt is unavailable.")
        return self._prompt_input(run.prompt, run.web_search)

    @staticmethod
    def _prompt_input(
        prompt: str,
        web_search: Literal["live", "disabled"] | None,
    ) -> list[dict[str, object]]:
        inputs: list[dict[str, object]] = []
        if web_search == "live":
            inputs.append({"type": "text", "text": _LIVE_WEB_SEARCH_GUIDANCE})
        inputs.append({"type": "text", "text": prompt})
        return inputs

    def _correlated_run_locked(
        self,
        generation: int,
        codex_thread_id: object,
        turn_id: object,
    ) -> RuntimeRunState | None:
        if not isinstance(codex_thread_id, str):
            return None
        for run in self._state.runs.values():
            if run.status in _TERMINAL_RUN_STATES:
                continue
            if run.generation != generation or run.codex_thread_id != codex_thread_id:
                continue
            if isinstance(turn_id, str) and run.codex_turn_id == turn_id:
                return run
        return None

    def _active_run_for_thread_locked(self, thread_id: str) -> RuntimeRunState | None:
        return next(
            (
                run
                for run in self._state.runs.values()
                if run.thread_id == thread_id
                and run.status in {"starting", "running", "cancelling"}
            ),
            None,
        )

    def _assert_threads_deletable_locked(self, thread_ids: set[str]) -> None:
        if not thread_ids:
            return
        has_owned_run = any(
            run.thread_id in thread_ids and run.status not in _TERMINAL_RUN_STATES
            for run in self._state.runs.values()
        )
        has_pending_interaction = any(
            interaction.thread_id in thread_ids
            and interaction.status in _PENDING_INTERACTION_STATES
            for interaction in self._state.interactions.values()
        )
        has_inflight_publication = any(
            self._inflight_publications.get(thread_id, 0) > 0
            for thread_id in thread_ids
        )
        if has_owned_run or has_pending_interaction or has_inflight_publication:
            raise RuntimeThreadBusyError()

    def _begin_publication_locked(self, thread_id: str) -> None:
        self._inflight_publications[thread_id] = (
            self._inflight_publications.get(thread_id, 0) + 1
        )

    def _finish_publication_locked(self, thread_id: str) -> None:
        owners = self._inflight_publications.get(thread_id, 0)
        if owners <= 1:
            self._inflight_publications.pop(thread_id, None)
        else:
            self._inflight_publications[thread_id] = owners - 1

    def _purge_threads_locked(self, thread_ids: set[str]) -> None:
        if not thread_ids:
            return
        run_ids = {
            run_id
            for run_id, run in self._state.runs.items()
            if run.thread_id in thread_ids
        }
        interaction_ids = {
            interaction_id
            for interaction_id, interaction in self._state.interactions.items()
            if interaction.thread_id in thread_ids or interaction.run_id in run_ids
        }
        request_ids = {
            request_id
            for request_id, outcome in self._state.request_idempotency.items()
            if outcome.thread_id in thread_ids or outcome.run_id in run_ids
        }
        if not run_ids and not interaction_ids and not request_ids:
            return

        self._state.runs = {
            run_id: run
            for run_id, run in self._state.runs.items()
            if run_id not in run_ids
        }
        self._state.interactions = {
            interaction_id: interaction
            for interaction_id, interaction in self._state.interactions.items()
            if interaction_id not in interaction_ids
        }
        self._state.request_idempotency = {
            request_id: outcome
            for request_id, outcome in self._state.request_idempotency.items()
            if request_id not in request_ids
        }
        for run_id in run_ids:
            self._revoke_browser_turn_locked(run_id)
            self._leases.pop(run_id, None)
            self._completion_events.pop(run_id, None)
            self._activity.pop(run_id, None)
            self._discard_pre_response_callbacks_locked(run_id)
        for interaction_id in interaction_ids:
            self._server_requests.pop(interaction_id, None)
        self._item_paths = {
            key: paths
            for key, paths in self._item_paths.items()
            if key[0] not in run_ids
        }
        # Persist private history removal before deleting public metadata. A
        # failed checkpoint enters the broker's fatal state and leaves the chat
        # intact; cross-file atomicity is owned by the durable journal task.
        self._persist_locked()

    def _find_cancellable_run_locked(
        self,
        thread_id: str,
        run_id: str | None,
    ) -> RuntimeRunState | None:
        candidates = [
            run
            for run in self._state.runs.values()
            if run.thread_id == thread_id and run.status not in _TERMINAL_RUN_STATES
        ]
        if run_id is not None:
            candidates = [run for run in candidates if run.run_id == run_id]
        return next(
            (run for run in reversed(candidates) if run.status != "queued"),
            candidates[-1] if candidates else None,
        )

    def _expire_run_interactions_locked(
        self,
        run: RuntimeRunState,
    ) -> tuple[EventDraft, ...]:
        events: list[EventDraft] = []
        for interaction in self._state.interactions.values():
            if (
                interaction.run_id != run.run_id
                or interaction.status not in _PENDING_INTERACTION_STATES
            ):
                continue
            request = self._server_requests.pop(interaction.interaction_id, None)
            if request is not None:
                self.app_server.discard_server_request(
                    request.request_id,
                    request.generation,
                )
            interaction.status = (
                "outcome_unknown" if interaction.status == "responding" else "expired"
            )
            interaction.display = None
            events.append(
                EventDraft(
                    scope="thread",
                    thread_id=interaction.thread_id,
                    event_type=(
                        "interaction.outcome_unknown"
                        if interaction.status == "outcome_unknown"
                        else "interaction.expired"
                    ),
                    payload={
                        "interaction_id": interaction.interaction_id,
                        "reason": "turn cancelling",
                    },
                )
            )
        return tuple(events)

    def _clear_queued_locked(self, reason: str) -> None:
        queued = [run for run in self._state.runs.values() if run.status == "queued"]
        for run in queued:
            lease = self._leases.get(run.run_id)
            if lease is not None:
                lease.cancel()
            self._terminalize_locked(
                run,
                "interrupted",
                "The Codex runtime restarted before the queued turn began.",
                preceding_events=(
                    EventDraft(
                        scope="thread",
                        thread_id=run.thread_id,
                        event_type="run.queue_cleared",
                        payload={"run_id": run.run_id, "reason": reason},
                    ),
                ),
            )

    def _cancel_queued_for_thread_locked(
        self,
        thread_id: str,
        *,
        except_run_id: str,
    ) -> None:
        queued = [
            candidate
            for candidate in self._state.runs.values()
            if candidate.thread_id == thread_id
            and candidate.run_id != except_run_id
            and candidate.status == "queued"
        ]
        for candidate in queued:
            lease = self._leases.get(candidate.run_id)
            if lease is not None:
                lease.cancel()
            self._terminalize_locked(
                candidate,
                "cancelled",
                "The queued follow-up was cleared when the turn was cancelled.",
            )

    def _expire_all_interactions_locked(
        self, *, reason: str = "runtime stopped"
    ) -> None:
        events: list[EventDraft] = []
        for interaction in self._state.interactions.values():
            if interaction.status not in _PENDING_INTERACTION_STATES:
                continue
            interaction.status = (
                "outcome_unknown" if interaction.status == "responding" else "expired"
            )
            interaction.display = None
            request = self._server_requests.pop(interaction.interaction_id, None)
            if request is not None:
                self.app_server.discard_server_request(
                    request.request_id,
                    request.generation,
                )
            events.append(
                EventDraft(
                    scope="thread",
                    thread_id=interaction.thread_id,
                    event_type=(
                        "interaction.outcome_unknown"
                        if interaction.status == "outcome_unknown"
                        else "interaction.expired"
                    ),
                    payload={
                        "interaction_id": interaction.interaction_id,
                        "reason": reason,
                    },
                )
            )
        if events:
            self._compact_terminal_state_locked()
            self._persist_locked(events=tuple(events))

    def _expire_due_interactions_locked(self) -> None:
        now = datetime.now(UTC)
        changed = False
        affected_runs: set[str] = set()
        affected_generations: set[int] = set()
        events: list[EventDraft] = []
        for interaction in self._state.interactions.values():
            if interaction.status not in _PENDING_INTERACTION_STATES:
                continue
            expires_at = _parse_time(interaction.expires_at)
            if expires_at is None or expires_at > now:
                continue
            request = self._server_requests.pop(interaction.interaction_id, None)
            if request is not None:
                self.app_server.discard_server_request(
                    request.request_id,
                    request.generation,
                )
            interaction.status = (
                "outcome_unknown" if interaction.status == "responding" else "expired"
            )
            interaction.display = None
            affected_runs.add(interaction.run_id)
            affected_generations.add(interaction.generation)
            events.append(
                EventDraft(
                    scope="thread",
                    thread_id=interaction.thread_id,
                    event_type=(
                        "interaction.outcome_unknown"
                        if interaction.status == "outcome_unknown"
                        else "interaction.expired"
                    ),
                    payload={"interaction_id": interaction.interaction_id},
                )
            )
            changed = True
        if changed:
            self._compact_terminal_state_locked()
            self._persist_locked(events=tuple(events))
            for generation in affected_generations:
                self.app_server.abort_generation(generation)
            self._clear_queued_locked("interaction timeout aborted the app-server")
            for run_id in affected_runs:
                run = self._state.runs.get(run_id)
                if run is not None and run.status not in _TERMINAL_RUN_STATES:
                    self._terminalize_locked(
                        run,
                        "failed",
                        "A Codex approval or question timed out.",
                    )

    def _mark_run_cancelling_for_interaction_locked(
        self,
        interaction: RuntimeInteractionState,
    ) -> None:
        run = self._state.runs.get(interaction.run_id)
        if run is None or run.status in _TERMINAL_RUN_STATES:
            return
        self._cancel_queued_for_thread_locked(run.thread_id, except_run_id=run.run_id)
        run.status = "cancelling"
        if run.cancellation_requested_at is None:
            run.cancellation_requested_at = _now()
        interaction_events = self._expire_run_interactions_locked(run)
        self._persist_locked(events=interaction_events)
        self._set_thread_projection_locked(run)

    def _spawn_worker_locked(self, run_id: str) -> None:
        worker = Thread(
            target=self._run_worker,
            args=(run_id,),
            name=f"CodexRuntime-{run_id[-8:]}",
            daemon=True,
        )
        self._workers.add(worker)
        worker.start()

    def _rollback_submission_locked(
        self,
        run: RuntimeRunState,
        lease: RuntimeLease,
    ) -> None:
        self._state.runs.pop(run.run_id, None)
        outcome = self._state.request_idempotency.get(run.client_request_id)
        if outcome is not None and outcome.run_id == run.run_id:
            self._state.request_idempotency.pop(run.client_request_id, None)
        self._leases.pop(run.run_id, None)
        self._completion_events.pop(run.run_id, None)
        if lease.state == "queued":
            lease.cancel()
        else:
            lease.release()
        try:
            self._persist_locked()
        except RuntimeStateError:
            pass
        try:
            record = self.storage.load_thread(run.thread_id)
            if record.active_run_id == run.run_id or record.status == "queued":
                record.active_run_id = None
                record.active_turn_id = None
                record.status = "idle"
                record.last_error = None
                self.storage.save_thread(record)
        except (ThreadNotFoundError, WorkspaceBoundaryError, OSError):
            pass

    def _ensure_request_capacity_locked(
        self,
        *,
        reserved_request_id: str | None = None,
    ) -> None:
        pending = len(self._pending_prompt_admissions)
        if (
            reserved_request_id is not None
            and self._pending_prompt_admissions.get(reserved_request_id) is not None
        ):
            pending -= 1
        if (
            len(self._state.request_idempotency) + pending
            < _MAX_REQUEST_OUTCOMES
        ):
            return
        missing_threads: set[str] = set()
        for thread_id in {
            outcome.thread_id for outcome in self._state.request_idempotency.values()
        }:
            try:
                self.storage.load_thread(thread_id)
            except ThreadNotFoundError:
                missing_threads.add(thread_id)
        if missing_threads:
            self._state.request_idempotency = {
                request_id: outcome
                for request_id, outcome in self._state.request_idempotency.items()
                if outcome.thread_id not in missing_threads
            }
            self._state.runs = {
                run_id: run
                for run_id, run in self._state.runs.items()
                if run.thread_id not in missing_threads
            }
            self._state.interactions = {
                interaction_id: interaction
                for interaction_id, interaction in self._state.interactions.items()
                if interaction.thread_id not in missing_threads
            }
            self._persist_locked()
        if (
            len(self._state.request_idempotency) + pending
            >= _MAX_REQUEST_OUTCOMES
        ):
            raise RuntimeStateCapacityError()

    def _compact_terminal_state_locked(self) -> None:
        terminal_runs = sorted(
            (
                run
                for run in self._state.runs.values()
                if run.status in _TERMINAL_RUN_STATES
            ),
            key=lambda run: (run.created_at, run.run_id),
            reverse=True,
        )
        retained_run_ids = {run.run_id for run in terminal_runs[:_MAX_TERMINAL_RUNS]}
        self._state.runs = {
            run_id: run
            for run_id, run in self._state.runs.items()
            if run.status not in _TERMINAL_RUN_STATES or run_id in retained_run_ids
        }
        terminal_interactions = sorted(
            (
                interaction
                for interaction in self._state.interactions.values()
                if interaction.status not in _PENDING_INTERACTION_STATES
            ),
            key=lambda interaction: (
                interaction.created_at,
                interaction.interaction_id,
            ),
            reverse=True,
        )
        retained_interaction_ids = {
            interaction.interaction_id
            for interaction in terminal_interactions[:_MAX_TERMINAL_INTERACTIONS]
        }
        self._state.interactions = {
            interaction_id: interaction
            for interaction_id, interaction in self._state.interactions.items()
            if interaction.status in _PENDING_INTERACTION_STATES
            or interaction_id in retained_interaction_ids
        }

    def _repair_thread_projections_after_state_reset_locked(self) -> None:
        for view in self.storage.list_threads(include_archived=True):
            if view.status not in {"queued", "running"}:
                continue
            record = self.storage.load_thread(view.thread_id)
            record.status = "error"
            record.active_run_id = None
            record.active_turn_id = None
            record.pending_prompts = []
            record.last_error = (
                "Codex runtime ownership was reset after an invalid checkpoint."
            )
            self.storage._save_thread_with_events(
                record,
                EventDraft(
                    scope="thread",
                    thread_id=record.thread_id,
                    event_type="runtime.state_recovered",
                    payload={"reason": "invalid private runtime checkpoint"},
                ),
            )
        self._recovered_corrupt_state = False

    def _repair_orphaned_thread_projections_locked(self) -> None:
        """Clear stale busy thread state without disturbing owned runtime work.

        Thread records are a public projection of the private runtime
        checkpoint.  A checkpoint can legitimately be absent after an
        interrupted first write, so startup must not leave its old projection
        permanently busy merely because there was no corrupt file to
        quarantine.  Conversely, never clear a projection belonging to a
        non-terminal run the in-memory checkpoint still owns.
        """
        owned_projections = {
            run.thread_id: (
                "queued" if run.status == "queued" else "running",
                None if run.status == "queued" else run.run_id,
                run.codex_turn_id,
                run.codex_thread_id,
            )
            for run in self._state.runs.values()
            if run.status not in _TERMINAL_RUN_STATES
        }
        self.storage.recover_orphaned_runtime_projections(
            owned_projections=owned_projections,
        )

    def _persist_locked(
        self,
        *,
        events: tuple[EventDraft, ...] = (),
        state_payloads: tuple[tuple[str, dict[str, object]], ...] = (),
    ) -> tuple[StoredEventRecord, ...]:
        self._state.revision += 1
        try:
            if events or state_payloads:
                additional_writes = tuple(
                    OutboxWrite(
                        relative_path=relative_path,
                        state_revision=(
                            self.storage.durable_outbox.next_state_revision(
                                relative_path
                            )
                        ),
                        state_payload=payload,
                    )
                    for relative_path, payload in state_payloads
                )
                if additional_writes:
                    return self._store.save_with_events(
                        self._state,
                        events=events,
                        additional_writes=additional_writes,
                    )
                return self._store.save_with_events(self._state, events=events)
            self._store.save(self._state)
            return ()
        except (
            DurableOperationTooLargeError,
            EventPayloadTooLargeError,
            EventStoreAdmissionError,
        ):
            # These failures occur before the outbox can replace canonical
            # state. Keep the broker available and let the public resource
            # handler report a safe 413/507 response.
            self._state.revision -= 1
            raise
        except EventStoreError:
            self._enter_fatal_state_locked()
            raise RuntimeStateError(
                "The private Codex runtime state could not be saved."
            ) from None
        except RuntimeStateError:
            self._enter_fatal_state_locked()
            raise

    def _enter_fatal_state_locked(self) -> None:
        if self._fatal_error:
            return
        self._fatal_error = True
        for admission in tuple(self._pending_prompt_admissions.values()):
            self._release_prompt_admission_locked(admission)
        for run_id in tuple(self._pre_response_callbacks):
            self._discard_pre_response_callbacks_locked(run_id)
        self._pre_response_request_owners.clear()
        self._callback_replays_in_progress.clear()
        for interaction in self._state.interactions.values():
            if interaction.status not in _PENDING_INTERACTION_STATES:
                continue
            interaction.status = (
                "outcome_unknown" if interaction.status == "responding" else "expired"
            )
            interaction.display = None
        for interaction_id, request in tuple(self._server_requests.items()):
            try:
                self.app_server.discard_server_request(
                    request.request_id,
                    request.generation,
                )
            except Exception:
                pass
            self._server_requests.pop(interaction_id, None)
        generations: set[int] = set()
        for run in self._state.runs.values():
            if run.status in _TERMINAL_RUN_STATES:
                continue
            self._revoke_browser_turn_locked(run.run_id)
            run.status = "interrupted"
            run.prompt = None
            run.terminal_message = "The private Codex runtime state is unavailable."
            run.last_activity_at = _now()
            if run.generation is not None:
                generations.add(run.generation)
            lease = self._leases.pop(run.run_id, None)
            if lease is not None:
                if lease.state == "queued":
                    lease.cancel()
                else:
                    lease.release()
            event = self._completion_events.pop(run.run_id, None)
            if event is not None:
                event.set()
            self._activity.pop(run.run_id, None)
            for outcome in self._state.request_idempotency.values():
                if outcome.run_id == run.run_id:
                    outcome.run_status = "interrupted"
            try:
                self._set_thread_projection_locked(run)
            except (OSError, ValueError):
                pass
        for generation in generations:
            self.app_server.abort_generation(generation)

    def _require_started_locked(self) -> None:
        if self._closed:
            raise RuntimeClosedError()
        if not self._started or self._fatal_error:
            raise RuntimeUnavailableError()

    def runtime_snapshot(self) -> RuntimeGateSnapshot:
        return self.gate.snapshot()


def _public_interaction(
    interaction: RuntimeInteractionState,
) -> PendingInteractionRecord:
    if interaction.display is None:
        raise InteractionStaleError()
    return PendingInteractionRecord(
        interaction_id=interaction.interaction_id,
        kind=interaction.kind,
        thread_id=interaction.thread_id,
        event_id=interaction.event_id,
        expires_at=interaction.expires_at,
        display=interaction.display,
        allowed_actions=interaction.allowed_actions,
    )


def _interaction_result(
    interaction: RuntimeInteractionState,
    request_id: str,
) -> InteractionResultRecord:
    if interaction.status not in {"accepted", "declined", "cancelled", "answered"}:
        raise InteractionResolvedError()
    status = cast(
        Literal["accepted", "declined", "cancelled", "answered"],
        interaction.status,
    )
    return InteractionResultRecord(
        interaction_id=interaction.interaction_id,
        thread_id=interaction.thread_id,
        status=status,
        client_request_id=request_id,
    )


def _normalize_answers(
    answers: list[dict[str, object]] | dict[str, list[str]],
    interaction: RuntimeInteractionState,
) -> dict[str, list[str]]:
    if isinstance(answers, dict):
        normalized = answers
    else:
        raw_normalized: dict[str, object] = {}
        for item in answers:
            question_id = item.get("question_id")
            values = item.get("values")
            if not isinstance(question_id, str) or question_id in raw_normalized:
                raise ValueError("question answers are invalid")
            if not isinstance(values, list):
                raise ValueError("question answers are invalid")
            raw_normalized[question_id] = values
        normalized = {}
        for question_id, values in raw_normalized.items():
            if not isinstance(values, list) or any(
                not isinstance(value, str) for value in values
            ):
                raise ValueError("question answers are invalid")
            normalized[question_id] = cast(list[str], values)
    questions = (
        {question.question_id: question for question in interaction.display.questions}
        if interaction.display is not None
        else {}
    )
    if interaction.display is not None and set(normalized) != set(questions):
        raise ValueError("question answers do not match the request")
    for question_id, values in normalized.items():
        if not isinstance(values, list) or not 1 <= len(values) <= 32:
            raise ValueError("question answers are invalid")
        if any(
            not isinstance(value, str) or not value or len(value) > 4096
            for value in values
        ):
            raise ValueError("question answers are invalid")
        question = questions.get(question_id)
        if question is None:
            continue
        if not question.multiple and len(values) != 1:
            raise ValueError("question accepts exactly one answer")
        if not question.allow_free_text:
            allowed = {option.label for option in question.options}
            if any(value not in allowed for value in values):
                raise ValueError("question answer is not an offered option")
    return normalized


def _automatic_denial(method: str, params: object) -> object:
    if method in {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }:
        return {"decision": "decline"}
    if method == "item/permissions/requestApproval":
        return {"permissions": {}, "scope": "turn"}
    if method == "item/tool/requestUserInput":
        return _empty_answers(params)
    return {"decision": "denied"}


def _empty_answers(params: object) -> dict[str, object]:
    questions = params.get("questions") if isinstance(params, dict) else None
    answers: dict[str, object] = {}
    if isinstance(questions, list):
        for question in questions[:3]:
            question_id = question.get("id") if isinstance(question, dict) else None
            if isinstance(question_id, str) and question_id:
                answers[question_id] = {"answers": []}
    return {"answers": answers}


def _browser_tool_call_params(
    params: object,
) -> tuple[str, str, str, str, dict[str, object], str] | None:
    """Validate the full client-owned browser callback envelope.

    ``item/tool/call`` is intentionally not fed through the generic approval
    correlation parser: it has no item ID, and accepting optional or unknown
    envelope fields would weaken the ownership contract at the model boundary.
    """

    if not isinstance(params, dict) or set(params) != {
        "arguments",
        "callId",
        "namespace",
        "threadId",
        "tool",
        "turnId",
    }:
        return None
    thread_id = params.get("threadId")
    turn_id = params.get("turnId")
    call_id = params.get("callId")
    namespace = params.get("namespace")
    tool = params.get("tool")
    arguments = params.get("arguments")
    identifiers = (thread_id, turn_id, call_id, tool)
    if any(
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > 256
        for value in identifiers
    ):
        return None
    if namespace != "ha_browser" or tool not in _BROWSER_DYNAMIC_TOOLS:
        return None
    if not isinstance(arguments, dict) or len(arguments) > 16:
        return None
    try:
        canonical_arguments = json.dumps(
            arguments,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return None
    if len(canonical_arguments.encode("utf-8")) > _MAX_BROWSER_DYNAMIC_ARGUMENT_BYTES:
        return None
    fingerprint = hashlib.sha256(
        f"{tool}\0{canonical_arguments}".encode("utf-8")
    ).hexdigest()
    return thread_id, turn_id, call_id, tool, dict(arguments), fingerprint


def _browser_tool_rejection() -> dict[str, object]:
    return {
        "success": False,
        "contentItems": [
            {
                "type": "inputText",
                "text": "Browser action rejected by Home Assistant policy.",
            }
        ],
    }


def _safe_browser_tool_result(value: object) -> dict[str, object]:
    """Keep a compromised helper from returning an unbounded tool payload."""

    if not isinstance(value, dict) or set(value) != {"success", "contentItems"}:
        return _browser_tool_rejection()
    success = value.get("success")
    content_items = value.get("contentItems")
    if type(success) is not bool or not isinstance(content_items, list):
        return _browser_tool_rejection()
    if not 1 <= len(content_items) <= 2:
        return _browser_tool_rejection()
    safe_items: list[dict[str, str]] = []
    for item in content_items:
        if not isinstance(item, dict):
            return _browser_tool_rejection()
        item_type = item.get("type")
        if item_type == "inputText" and set(item) == {"type", "text"}:
            text = item.get("text")
            if (
                not isinstance(text, str)
                or not text
                or len(text.encode("utf-8")) > _MAX_BROWSER_DYNAMIC_TEXT_BYTES
            ):
                return _browser_tool_rejection()
            safe_items.append({"type": "inputText", "text": text})
            continue
        if item_type == "inputImage" and set(item) == {"type", "imageUrl"}:
            image_url = item.get("imageUrl")
            if (
                not isinstance(image_url, str)
                or not image_url.startswith((
                    "data:image/png;base64,",
                    "data:image/jpeg;base64,",
                ))
            ):
                return _browser_tool_rejection()
            try:
                encoded = image_url.encode("ascii")
            except UnicodeEncodeError:
                return _browser_tool_rejection()
            if len(encoded) > _MAX_BROWSER_DYNAMIC_IMAGE_URL_BYTES:
                return _browser_tool_rejection()
            safe_items.append({"type": "inputImage", "imageUrl": image_url})
            continue
        return _browser_tool_rejection()
    return {"success": success, "contentItems": safe_items}


def _safe_failure(value: object) -> _SafeFailure:
    """Map Codex's versioned failure union onto a fixed public contract.

    Provider messages, details, HTTP codes, paths, and payload values are all
    intentionally ignored.  Codex 0.144.5 uses both string and tagged-object
    variants for this union, so recognize only known discriminants.
    """

    known = {
        "usageLimitExceeded": _SafeFailure(
            "Codex usage limits have been reached.",
            "limits.exhausted",
            blocked=True,
        ),
        "unauthorized": _SafeFailure(
            "Codex sign-in expired. Start a new sign-in from Home Assistant.",
            "auth.expired",
            auth_required=True,
        ),
        "contextWindowExceeded": _SafeFailure(
            "The Codex conversation context is full.",
            "context.window_exceeded",
        ),
        "sessionBudgetExceeded": _SafeFailure(
            "The Codex session budget has been reached.",
            "session.budget_exhausted",
        ),
        "serverOverloaded": _SafeFailure(
            "The Codex service is temporarily overloaded.",
            "service.overloaded",
        ),
        "sandboxError": _SafeFailure(
            "The Codex sandbox could not complete the turn.",
            "sandbox.error",
        ),
        "cyberPolicy": _SafeFailure(
            "Codex could not complete this request because of its safety policy.",
            "policy.cyber",
        ),
        "internalServerError": _SafeFailure(
            "The Codex service encountered an internal error.",
            "service.internal_error",
        ),
        "badRequest": _SafeFailure(
            "Codex could not process this request.",
            "request.invalid",
        ),
        "threadRollbackFailed": _SafeFailure(
            "Codex could not restore the conversation after the turn failed.",
            "thread.rollback_failed",
        ),
        "other": _SafeFailure("Codex could not complete the turn.", "run.failed"),
        "httpConnectionFailed": _SafeFailure(
            "Codex could not connect to the service.",
            "network.http_connection_failed",
        ),
        "responseStreamConnectionFailed": _SafeFailure(
            "Codex could not connect to the response stream.",
            "network.response_stream_connection_failed",
        ),
        "responseStreamDisconnected": _SafeFailure(
            "The Codex response stream disconnected before completion.",
            "network.response_stream_disconnected",
        ),
        "responseTooManyFailedAttempts": _SafeFailure(
            "Codex could not recover the response stream after several attempts.",
            "network.response_stream_retry_exhausted",
        ),
        "activeTurnNotSteerable": _SafeFailure(
            "The active Codex turn cannot accept this follow-up yet.",
            "turn.not_steerable",
        ),
    }
    if isinstance(value, str):
        return known.get(value, known["other"])
    if isinstance(value, dict):
        # `type` is retained for a safe compatibility projection of older
        # runtimes.  Current tagged unions use their sole key instead.
        kind = value.get("type")
        if isinstance(kind, str) and kind in known:
            return known[kind]
        for kind in (
            "httpConnectionFailed",
            "responseStreamConnectionFailed",
            "responseStreamDisconnected",
            "responseTooManyFailedAttempts",
            "activeTurnNotSteerable",
        ):
            if kind in value:
                return known[kind]
    return known["other"]


def _fingerprint(value: object) -> str:
    return runtime_fingerprint(value)


def _attachment_manifest(attachments: list[Any]) -> str:
    return _fingerprint(
        [
            {
                "attachment_id": attachment.attachment_id,
                "filename": attachment.filename,
                "mime_type": attachment.mime_type,
                "stored_path": attachment.stored_path,
                "relative_path": attachment.relative_path,
                "size_bytes": attachment.size_bytes,
                "sha256": attachment.sha256,
            }
            for attachment in attachments
        ]
    )


def _raw_chunks(value: object, *, max_bytes: int = 64 * 1024) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    encoded = value.encode("utf-8")
    if not encoded:
        return ("",)
    chunks: list[str] = []
    offset = 0
    while offset < len(encoded):
        end = min(len(encoded), offset + max_bytes)
        while end > offset:
            try:
                chunk = encoded[offset:end].decode("utf-8")
                break
            except UnicodeDecodeError:
                end -= 1
        if end == offset:
            raise RuntimeProtocolMismatchError()
        chunks.append(chunk)
        offset = end
    return tuple(chunks)


def _run_record(run: RuntimeRunState) -> RunRecord:
    return RunRecord(run_id=run.run_id, thread_id=run.thread_id, status=run.status)


def _outcome_record(outcome: RuntimeRequestOutcome) -> RunRecord:
    return RunRecord(
        run_id=outcome.run_id,
        thread_id=outcome.thread_id,
        status=outcome.run_status,
    )


def _prompt(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("prompt must not be blank")
    if len(value.encode("utf-8")) > 1024 * 1024:
        raise ValueError("prompt exceeds its limit")
    return value


def _require_message_event_capacity(
    *,
    prompt: str,
    client_request_id: str,
    maximum_bytes: int,
) -> None:
    # Reserve for the longest accepted runtime identifier and the steer marker.
    # This preflight runs before either a runtime lease or remote turn is mutated.
    payload = {
        "run_id": "r" * 128,
        "role": "user",
        "text": prompt,
        "client_request_id": client_request_id,
        "steered": True,
    }
    if _event_payload_bytes(payload) > maximum_bytes:
        raise RuntimeEventPayloadTooLargeError()


def _partition_patch_payloads(
    *,
    run_id: str,
    changes: list[dict[str, object]],
    maximum_bytes: int,
) -> tuple[dict[str, object], ...]:
    if not changes:
        payload = {"run_id": run_id, "changes": []}
        return (payload,) if _event_payload_bytes(payload) <= maximum_bytes else ()

    groups: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    # Use the largest possible chunk metadata while grouping so replacing it
    # with the real values can never push a serialized event over the limit.
    reserved_metadata = {"chunk_index": 255, "chunk_count": 256}
    for change in changes:
        candidate = [*current, change]
        payload = {
            "run_id": run_id,
            "changes": candidate,
            **reserved_metadata,
        }
        if _event_payload_bytes(payload) <= maximum_bytes:
            current = candidate
            continue
        if not current:
            # A protocol update that cannot fit even one bounded change is
            # ignored rather than converting a notification into fatal state.
            return ()
        groups.append(current)
        current = [change]
        payload = {
            "run_id": run_id,
            "changes": current,
            **reserved_metadata,
        }
        if _event_payload_bytes(payload) > maximum_bytes:
            return ()
    groups.append(current)

    if len(groups) == 1:
        payload = {"run_id": run_id, "changes": groups[0]}
        return (payload,) if _event_payload_bytes(payload) <= maximum_bytes else ()
    chunk_count = len(groups)
    return tuple(
        {
            "run_id": run_id,
            "changes": group,
            "chunk_index": index,
            "chunk_count": chunk_count,
        }
        for index, group in enumerate(groups)
    )


def _event_payload_bytes(payload: dict[str, object]) -> int:
    return len(
        json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _safe_item_activity_metadata(item: dict[str, Any]) -> dict[str, object]:
    """Project item lifecycle metadata without forwarding provider content.

    Thread items carry commands, paths, URLs, arguments, and output alongside
    their type.  The UI only needs bounded enum metadata to label activity, so
    deliberately omit all provider-supplied text and locators here.
    """

    metadata: dict[str, object] = {}
    status = item.get("status")
    if isinstance(status, str) and status in _SAFE_ITEM_STATUSES:
        metadata["status"] = status

    duration = item.get("durationMs")
    if type(duration) is int and 0 <= duration <= _MAX_ITEM_ACTIVITY_DURATION_MS:
        metadata["duration_ms"] = duration

    item_type = item.get("type")
    if item_type == "commandExecution":
        actions = item.get("commandActions")
        if isinstance(actions, list):
            action_types: list[str] = []
            for action in actions[:16]:
                if not isinstance(action, dict):
                    continue
                action_type = action.get("type")
                if (
                    isinstance(action_type, str)
                    and action_type in _SAFE_COMMAND_ACTION_TYPES
                    and action_type not in action_types
                ):
                    action_types.append(action_type)
            if action_types:
                metadata["action_types"] = action_types
    elif item_type == "webSearch":
        action = item.get("action")
        if isinstance(action, dict):
            action_type = action.get("type")
            if (
                isinstance(action_type, str)
                and action_type in _SAFE_WEB_SEARCH_ACTION_TYPES
            ):
                metadata["action_type"] = action_type
    elif item_type == "fileChange":
        changes = item.get("changes")
        if isinstance(changes, list):
            change_kinds: list[str] = []
            for change in changes[:256]:
                if not isinstance(change, dict):
                    continue
                kind = change.get("kind")
                change_type = kind.get("type") if isinstance(kind, dict) else None
                if (
                    isinstance(change_type, str)
                    and change_type in _SAFE_CHANGE_KINDS
                    and change_type not in change_kinds
                ):
                    change_kinds.append(change_type)
            if change_kinds:
                metadata["change_kinds"] = change_kinds
    elif item_type == "collabAgentToolCall":
        # A collab item carries agent IDs, thread IDs, prompt, model, and per-agent
        # messages. Project only its fixed operation enum and aggregate state totals.
        operation = item.get("tool")
        if (
            isinstance(operation, str)
            and operation in _SAFE_COLLAB_AGENT_OPERATIONS
        ):
            metadata["operation"] = operation
        agent_states = item.get("agentsStates")
        if isinstance(agent_states, dict):
            counts: dict[str, int] = {}
            for state in agent_states.values():
                if not isinstance(state, dict):
                    continue
                status = state.get("status")
                if (
                    isinstance(status, str)
                    and status in _SAFE_COLLAB_AGENT_STATES
                ):
                    # Keep the durable JSON payload within JavaScript's exact
                    # integer range even if an untrusted provider map is extreme.
                    counts[status] = min(
                        counts.get(status, 0) + 1,
                        _MAX_SAFE_AGENT_STATE_COUNT,
                    )
            if counts:
                metadata["agent_state_counts"] = counts
    elif item_type == "subAgentActivity":
        # agentPath and agentThreadId can identify local workspace layout or
        # internal agent topology, so retain only the protocol-defined kind.
        kind = item.get("kind")
        if (
            isinstance(kind, str)
            and kind in _SAFE_SUB_AGENT_ACTIVITY_KINDS
        ):
            metadata["kind"] = kind
    return metadata


def _identifier(value: object, *, limit: int, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > limit
    ):
        raise ValueError(f"{label} is invalid")
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _notification_turn_id(params: object) -> str | None:
    if not isinstance(params, dict):
        return None
    turn = params.get("turn")
    value = turn.get("id") if isinstance(turn, dict) else params.get("turnId")
    return value if isinstance(value, str) else None


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _snake(value: str) -> str:
    result = []
    for character in value:
        if character.isupper():
            result.append("_")
            result.append(character.lower())
        else:
            result.append(character)
    return "".join(result)


def _positive_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("runtime timeout must be positive")
    result = float(value)
    if result <= 0:
        raise ValueError("runtime timeout must be positive")
    return result
