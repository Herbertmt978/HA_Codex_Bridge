"""Generation- and turn-scoped broker for the private browser worker."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import re
from threading import RLock
from time import monotonic
from typing import Protocol
from uuid import uuid4

from .browser_contract import (
    BrowserContractError,
    BrowserWorkerResponse,
    parse_browser_action,
    parse_worker_response,
)


_TOOLS = frozenset(
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
_ARTIFACT_ID = re.compile(r"^art_[A-Za-z0-9_-]{1,128}$")


@dataclass(frozen=True, slots=True)
class BrowserInvocationContext:
    run_id: str
    thread_id: str
    codex_thread_id: str
    turn_id: str
    generation: int

    def __post_init__(self) -> None:
        if (
            not all(
                isinstance(value, str) and 0 < len(value) <= 256
                for value in (
                    self.run_id,
                    self.thread_id,
                    self.codex_thread_id,
                    self.turn_id,
                )
            )
            or type(self.generation) is not int
            or self.generation <= 0
        ):
            raise ValueError("browser invocation context is invalid")


class BrowserWorker(Protocol):
    def ready(self) -> bool: ...

    def execute(self, action: object, *, session_id: str) -> object: ...

    def close_session(self, session_id: str) -> None: ...


ArtifactSink = Callable[[BrowserInvocationContext, str, str, bytes], str]


@dataclass(slots=True)
class _Session:
    session_id: str
    owner: BrowserInvocationContext
    created_at: float
    last_action_at: float
    action_count: int = 0
    busy: bool = False


class BrowserBroker:
    """Own ephemeral browser sessions and translate Codex dynamic tool calls."""

    def __init__(
        self,
        worker: BrowserWorker,
        *,
        artifact_sink: ArtifactSink | None = None,
        session_ttl_seconds: float = 300.0,
        max_sessions: int = 1,
        max_actions_per_session: int = 100,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if (
            session_ttl_seconds <= 0
            or type(max_sessions) is not int
            or max_sessions <= 0
            or type(max_actions_per_session) is not int
            or max_actions_per_session <= 0
        ):
            raise ValueError("browser broker limits are invalid")
        self._worker = worker
        self._artifact_sink = artifact_sink
        self._session_ttl_seconds = float(session_ttl_seconds)
        self._max_sessions = max_sessions
        self._max_actions_per_session = max_actions_per_session
        self._clock = clock
        self._sessions: dict[str, _Session] = {}
        # The worker owns one Chromium process, not one process per session.
        # Keep its slot reserved while a cancelled/expired session is being
        # torn down so a late close can never terminate a newly opened page.
        self._tearing_down: set[str] = set()
        self._lock = RLock()
        self._closed = False

    @property
    def ready(self) -> bool:
        with self._lock:
            if self._closed:
                return False
        try:
            return self._worker.ready() is True
        except BaseException:
            return False

    def session_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._sessions))

    def invoke(
        self,
        owner: BrowserInvocationContext,
        tool: object,
        arguments: object,
    ) -> dict[str, object]:
        if not isinstance(owner, BrowserInvocationContext):
            return _failure("Browser action rejected.")
        if not isinstance(tool, str) or tool not in _TOOLS:
            return _failure("Browser action rejected.")
        if not isinstance(arguments, Mapping) or "action" in arguments:
            return _failure("Browser action rejected.")
        if not self.ready:
            return _failure("Browser worker unavailable.")
        payload = {"action": tool, **dict(arguments)}
        try:
            action = parse_browser_action(payload)
        except BrowserContractError:
            return _failure("Browser action rejected.")

        session_to_close: str | None = None
        limit_exceeded = False
        close_requested = False
        # An expired session must be terminated before another can reserve the
        # single Chromium worker.  Do that work outside the broker lock.
        while True:
            with self._lock:
                if self._closed:
                    return _failure("Browser worker unavailable.")
                expired = self._take_expired_sessions_locked(self._clock())
            if not expired:
                break
            for session_id in expired:
                self._close_worker_session(session_id)

        with self._lock:
            if self._closed:
                return _failure("Browser worker unavailable.")
            now = self._clock()
            if tool == "open":
                if self._tearing_down:
                    return _failure("Browser worker unavailable.")
                if len(self._sessions) >= self._max_sessions:
                    return _failure("Browser session limit reached.")
                session_id = f"brs_{uuid4().hex[:16]}"
                session = _Session(
                    session_id=session_id,
                    owner=owner,
                    created_at=now,
                    last_action_at=now,
                )
                self._sessions[session_id] = session
            else:
                session_id = getattr(action, "session_id", None)
                if not isinstance(session_id, str):
                    return _failure("Browser session unavailable.")
                session = self._sessions.get(session_id)
                if session is None or session.owner != owner or session.busy:
                    return _failure("Browser session unavailable.")
                if self._expired(session, now):
                    self._take_session_locked(session_id)
                    session_to_close = session_id
                elif session.action_count >= self._max_actions_per_session:
                    self._take_session_locked(session_id)
                    session_to_close = session_id
                    limit_exceeded = True
            if session_to_close is None and tool == "close":
                self._take_session_locked(session_id)
                close_requested = True
            elif session_to_close is None:
                session.busy = True
                session.action_count += 1
                session.last_action_at = now

        if session_to_close is not None:
            self._close_worker_session(session_to_close)
            return _failure(
                "Browser session limit reached."
                if limit_exceeded
                else "Browser session unavailable."
            )
        if close_requested:
            self._close_worker_session(session_id)
            return _success_text("Browser session closed.")

        # Deliberately outside ``_lock``: cancellation/terminal transitions
        # must be able to revoke the session and terminate a wedged worker.
        try:
            raw_response = self._worker.execute(action, session_id=session_id)
            response = parse_worker_response(raw_response)
            if response.session_id != session_id:
                raise BrowserContractError("browser worker response is invalid")
        except BaseException:
            if self._take_if_current(session_id, session):
                self._close_worker_session(session_id)
            return _failure("Browser worker failed safely.")

        with self._lock:
            if self._sessions.get(session_id) is not session or self._closed:
                return _failure("Browser session unavailable.")
            if response.status == "error":
                session.busy = False
                code = response.error.code if response.error is not None else "worker_failed"
                terminal = tool == "open" or code in {
                    "browser_unavailable",
                    "navigation_blocked",
                    "session_closed",
                    "session_expired",
                    "worker_failed",
                }
                if terminal:
                    self._take_session_locked(session_id)
                else:
                    return _failure(_worker_failure_message(response))
            else:
                terminal = False
            artifact_sink = self._artifact_sink
        if response.status == "error":
            if terminal:
                self._close_worker_session(session_id)
            return _failure(_worker_failure_message(response))

        try:
            # Persistence may block on private storage.  It must not prevent a
            # terminal runtime transition from revoking this browser session.
            result = self._success_result(owner, tool, response, artifact_sink)
        except BaseException:
            if self._take_if_current(session_id, session):
                self._close_worker_session(session_id)
            return _failure("Browser artifact publication failed safely.")
        with self._lock:
            if self._sessions.get(session_id) is not session or self._closed:
                return _failure("Browser session unavailable.")
            session.busy = False
        return result

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            session_ids = tuple(self._sessions)
            for session_id in session_ids:
                self._take_session_locked(session_id)
        for session_id in session_ids:
            self._close_worker_session(session_id)

    def close_owner(self, owner: BrowserInvocationContext) -> None:
        """Destroy every ephemeral session belonging to one finished turn.

        The runtime broker calls this on every terminal transition.  Equality is
        deliberately exact so a stale callback from a prior generation can
        never close (or retain) a later turn's browser session.
        """

        if not isinstance(owner, BrowserInvocationContext):
            return
        with self._lock:
            session_ids = []
            for session_id, session in tuple(self._sessions.items()):
                if session.owner == owner:
                    self._take_session_locked(session_id)
                    session_ids.append(session_id)
        for session_id in session_ids:
            self._close_worker_session(session_id)

    def set_artifact_sink(self, artifact_sink: ArtifactSink) -> None:
        """Bind the private App storage sink before browser use begins."""

        if not callable(artifact_sink):
            raise ValueError("browser artifact sink is invalid")
        with self._lock:
            if self._closed or self._sessions:
                raise RuntimeError("browser artifact sink cannot be changed")
            self._artifact_sink = artifact_sink

    def _success_result(
        self,
        owner: BrowserInvocationContext,
        tool: str,
        response: BrowserWorkerResponse,
        artifact_sink: ArtifactSink | None,
    ) -> dict[str, object]:
        content: list[dict[str, str]] = []
        if response.artifact is not None:
            if artifact_sink is None:
                raise RuntimeError("artifact sink is unavailable")
            artifact_id = artifact_sink(
                owner,
                response.artifact.kind,
                response.artifact.mime_type,
                response.artifact.data,
            )
            if not isinstance(artifact_id, str) or _ARTIFACT_ID.fullmatch(artifact_id) is None:
                raise RuntimeError("artifact sink returned an unsafe identifier")
            label = "screenshot" if response.artifact.kind == "screenshot" else "PDF"
            content.append(
                {
                    "type": "inputText",
                    "text": f"Browser {label} saved as private artifact {artifact_id}.",
                }
            )
            if response.artifact.kind == "screenshot":
                encoded = base64.b64encode(response.artifact.data).decode("ascii")
                content.append(
                    {
                        "type": "inputImage",
                        "imageUrl": f"data:{response.artifact.mime_type};base64,{encoded}",
                    }
                )
        elif response.page is not None:
            if tool == "inspect" and response.page.text:
                text = f"{response.page.title}\n\n{response.page.text}".strip()
            else:
                text = f"Browser page ready: {response.page.title}."
            content.append({"type": "inputText", "text": text})
        else:
            content.append({"type": "inputText", "text": "Browser action completed."})
        return {"success": True, "contentItems": content}

    def _expired(self, session: _Session, now: float) -> bool:
        return now - session.created_at > self._session_ttl_seconds

    def _take_expired_sessions_locked(self, now: float) -> tuple[str, ...]:
        expired: list[str] = []
        for session_id, session in tuple(self._sessions.items()):
            if self._expired(session, now):
                self._take_session_locked(session_id)
                expired.append(session_id)
        return tuple(expired)

    def _take_if_current(self, session_id: str, session: _Session) -> bool:
        with self._lock:
            if self._sessions.get(session_id) is not session:
                return False
            self._take_session_locked(session_id)
            return True

    def _take_session_locked(self, session_id: str) -> bool:
        if self._sessions.pop(session_id, None) is None:
            return False
        self._tearing_down.add(session_id)
        return True

    def _close_worker_session(self, session_id: str) -> None:
        try:
            self._worker.close_session(session_id)
        except BaseException:
            # The session is forgotten even when an already-dead worker cannot
            # acknowledge cleanup. The worker process boundary owns final
            # profile deletion and must also clean on exit.
            pass
        finally:
            with self._lock:
                self._tearing_down.discard(session_id)


def _failure(message: str) -> dict[str, object]:
    return {
        "success": False,
        "contentItems": [{"type": "inputText", "text": message}],
    }


def _success_text(message: str) -> dict[str, object]:
    return {
        "success": True,
        "contentItems": [{"type": "inputText", "text": message}],
    }


def _worker_failure_message(response: BrowserWorkerResponse) -> str:
    code = response.error.code if response.error is not None else "worker_failed"
    return {
        "browser_unavailable": "Browser worker unavailable.",
        "navigation_blocked": "Browser navigation blocked by policy.",
        "navigation_failed": "Browser navigation failed.",
        "page_timeout": "Browser action timed out.",
        "selector_not_found": "Browser element was not found.",
        "session_closed": "Browser session unavailable.",
        "session_expired": "Browser session unavailable.",
        "worker_failed": "Browser worker failed safely.",
    }.get(code, "Browser worker failed safely.")
