"""One ephemeral, administrator-operated terminal in an attested HA workspace."""

from __future__ import annotations

import base64
import binascii
from collections import deque
from dataclasses import dataclass, field
from threading import RLock, Thread
from time import monotonic
from typing import Any, Callable
from uuid import uuid4

from .models import RunMode, RuntimeProfile


class TerminalError(RuntimeError):
    code = "terminal_unavailable"


@dataclass
class _Session:
    session_id: str
    thread_id: str
    client: Any
    lease: Any
    reservation: Any
    workspace: str
    state: str = "starting"
    sequence: int = 0
    input_sequence: int = 0
    size_bytes: int = 0
    total_bytes: int = 0
    chunks: deque = field(default_factory=deque)
    last_seen: float = field(default_factory=monotonic)
    exit_code: int | None = None
    message: str = "Starting workspace terminal"
    generation: int = 0


class WorkspaceTerminal:
    """Keep shell bytes out of chat history and close the dedicated runtime on exit.

    A config lease excludes Codex turns, account changes and workspace mutation
    while the administrator owns the shell. The regular quota reservation tracks
    writes. No request can supply a cwd, command, environment or sandbox policy.
    """

    def __init__(self, storage, gate, client_factory: Callable, ready: Callable,
                 *, idle_seconds: float = 30, maximum_bytes: int = 8 * 1024 * 1024):
        self.storage = storage
        self.gate = gate
        self.client_factory = client_factory
        self.ready = ready
        self.idle_seconds = idle_seconds
        self.maximum_bytes = maximum_bytes
        self._lock = RLock()
        self._operation = RLock()
        self._session: _Session | None = None
        self._closed = False

    def open(self, thread_id: str, cols: int, rows: int) -> dict:
        self._size(cols, rows)
        with self._operation:
            if self._closed or not self.ready() or self.storage.runtime_profile is not RuntimeProfile.HOME_ASSISTANT:
                raise TerminalError()
            with self._lock:
                if self._session and self._session.state in {"starting", "running", "closing"}:
                    raise TerminalError("Close the current terminal first.")
            lease = self.gate.acquire_config_mutation()
            reservation = None
            client = None
            try:
                record = self.storage.load_thread(thread_id)
                project = self.storage.load_project(record.project_id)
                if record.archived_at or project.archived_at or record.mode is RunMode.OBSERVE:
                    raise TerminalError("Select an editable, unarchived chat.")
                workspace = str(self.storage.resolve_workspace_path(record.workspace_path))
                reservation = self.storage.reserve_workspace_mutation()
                client = self.client_factory(workspace)
                session = _Session(uuid4().hex, thread_id, client, lease, reservation, workspace)
                client.register_notification_handler("command/exec/outputDelta", lambda notification: self._output(session, notification))
                client.start()
                session.generation = client.generation
                with self._lock:
                    self._session = session
                Thread(target=self._run, args=(session, cols, rows), daemon=True, name="BridgeWorkspaceTerminal").start()
                Thread(target=self._watch, args=(session,), daemon=True, name="BridgeTerminalWatch").start()
                return self.read(session.session_id, thread_id, 0)
            except BaseException:
                if client is not None:
                    client.close()
                if reservation is not None:
                    reservation.release()
                lease.release()
                raise

    def _run(self, session: _Session, cols: int, rows: int) -> None:
        try:
            # Omit legacy sandboxPolicy: it would replace the managed minimal-read
            # profile. The App's immutable requirements select ha_bridge and reject
            # built-in broad-read / unrestricted profiles. The dedicated client's
            # startup directory, not this request's cwd, supplies workspace roots.
            result = session.client.request("command/exec", {
                "command": ["/usr/local/bin/python", "-I", "-m", "codex_bridge_service.terminal_shell"], "cwd": session.workspace,
                "processId": session.session_id, "tty": True,
                "size": {"cols": cols, "rows": rows},
                "timeoutMs": 1_800_000, "outputBytesCap": self.maximum_bytes,
                "env": {"TERM": "xterm-256color", "HISTFILE": "/dev/null", "ENV": None, "BASH_ENV": None},
            }, timeout_seconds=1810)
            with self._lock:
                code = result.get("exitCode") if isinstance(result, dict) else None
                session.exit_code = code if type(code) is int else None
                if session.state != "closing":
                    session.message = "Terminal exited"
        except Exception:
            with self._lock:
                if session.state != "closing":
                    session.message = "Terminal stopped; open a new terminal to continue"
        finally:
            # Closing this dedicated protocol connection terminates its PTY and
            # descendants. Never release mutation ownership before it has closed.
            with self._operation:
                session.client.close()
                session.reservation.release()
                session.lease.release()
                with self._lock:
                    session.state = "closed"

    def _output(self, session: _Session, notification) -> None:
        params = notification.params
        if (notification.generation != session.generation or not isinstance(params, dict)
                or params.get("processId") != session.session_id):
            return
        encoded = params.get("deltaBase64")
        if not isinstance(encoded, str) or len(encoded) > 256 * 1024:
            self._stop_async(session, "Terminal output limit reached")
            return
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            self._stop_async(session, "Terminal returned invalid output")
            return
        with self._lock:
            if session.state not in {"starting", "running"}:
                return
            session.state = "running"
            session.message = "Workspace terminal"
            for offset in range(0, len(data), 16 * 1024):
                chunk = data[offset:offset + 16 * 1024]
                session.sequence += 1
                session.chunks.append((session.sequence, base64.b64encode(chunk).decode("ascii"), len(chunk)))
                session.size_bytes += len(chunk)
                session.total_bytes += len(chunk)
            while session.size_bytes > 256 * 1024:
                session.size_bytes -= session.chunks.popleft()[2]
            exceeded = session.total_bytes > self.maximum_bytes or params.get("capReached") is True
        if exceeded:
            self._stop_async(session, "Terminal output limit reached")

    def _watch(self, session: _Session) -> None:
        from time import sleep

        while True:
            sleep(1)
            with self._lock:
                if session.state == "closed":
                    return
                expired = monotonic() - session.last_seen > self.idle_seconds
            if expired:
                self._stop(session, "Terminal closed after disconnection")
                return
            if not self._editable(session):
                self._stop(session, "Terminal closed because workspace access changed")
                return
            try:
                self.storage.observe_workspace_growth(session.reservation)
            except Exception:
                self._stop(session, "Workspace storage limit reached")
                return

    def _editable(self, session: _Session) -> bool:
        try:
            record = self.storage.load_thread(session.thread_id)
            project = self.storage.load_project(record.project_id)
            return (
                not record.archived_at and not project.archived_at
                and record.mode is not RunMode.OBSERVE
                and str(self.storage.resolve_workspace_path(record.workspace_path)) == session.workspace
            )
        except Exception:
            return False

    def _get(self, session_id: str, thread_id: str) -> _Session:
        session = self._session
        if session is None or session.session_id != session_id or session.thread_id != thread_id:
            raise TerminalError("Terminal no longer available")
        return session

    def read(self, session_id: str, thread_id: str, after: int) -> dict:
        if type(after) is not int or after < 0:
            raise TerminalError()
        with self._lock:
            session = self._get(session_id, thread_id)
            session.last_seen = monotonic()
            chunks = [(seq, data) for seq, data, _ in session.chunks if seq > after][:4]
            return {
                "session_id": session.session_id, "state": session.state,
                "chunks": [{"sequence": seq, "data": data} for seq, data in chunks],
                "cursor": chunks[-1][0] if chunks else after,
                "truncated": bool(session.chunks and after < session.chunks[0][0] - 1),
                "has_more": bool(chunks and chunks[-1][0] < session.sequence),
                "exit_code": session.exit_code, "message": session.message,
            }

    def write(self, session_id: str, thread_id: str, data: str, sequence: int) -> dict:
        if not isinstance(data, str) or len(data.encode("utf-8")) > 16 * 1024 or type(sequence) is not int or sequence < 1:
            raise TerminalError()
        with self._operation:
            with self._lock:
                session = self._get(session_id, thread_id)
                if session.state != "running":
                    raise TerminalError("Terminal is not running")
            if not self._editable(session):
                self._stop(session, "Terminal closed because workspace access changed")
                raise TerminalError()
            with self._lock:
                if sequence <= session.input_sequence:
                    return {"accepted": True}
                if sequence != session.input_sequence + 1:
                    raise TerminalError("Terminal input is out of order")
                # Ambiguous writes must not be replayed. A failed request closes
                # the terminal instead of risking duplicate command execution.
                session.input_sequence = sequence
                session.last_seen = monotonic()
            try:
                session.client.request("command/exec/write", {"processId": session.session_id, "deltaBase64": base64.b64encode(data.encode()).decode()}, timeout_seconds=5)
            except Exception:
                self._stop(session, "Terminal input could not be confirmed")
                raise TerminalError() from None
            return {"accepted": True}

    @staticmethod
    def _size(cols: int, rows: int) -> None:
        if type(cols) is not int or type(rows) is not int or not 20 <= cols <= 300 or not 2 <= rows <= 100:
            raise TerminalError("Invalid terminal dimensions")

    def resize(self, session_id: str, thread_id: str, cols: int, rows: int) -> dict:
        self._size(cols, rows)
        with self._operation:
            with self._lock:
                session = self._get(session_id, thread_id)
                if session.state != "running":
                    raise TerminalError()
            session.client.request("command/exec/resize", {"processId": session.session_id, "size": {"cols": cols, "rows": rows}}, timeout_seconds=5)
        return {"resized": True}

    def _stop_async(self, session: _Session, message: str) -> None:
        Thread(target=self._stop, args=(session, message), daemon=True).start()

    def _stop(self, session: _Session, message: str = "Terminal closed") -> None:
        with self._operation:
            with self._lock:
                if session.state in {"closing", "closed"}:
                    return
                session.state = "closing"
                session.message = message
            session.client.close()

    def close_session(self, session_id: str, thread_id: str) -> dict:
        with self._lock:
            session = self._get(session_id, thread_id)
        self._stop(session)
        return {"closed": True}

    def close(self) -> None:
        with self._operation:
            self._closed = True
            with self._lock:
                session = self._session
            if session is not None:
                self._stop(session)
