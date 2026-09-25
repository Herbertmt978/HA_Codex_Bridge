"""Bounded JSON-RPC pipe for an independently confined stdio MCP worker.

The HTTP adapter owns this pipe. Native Codex never receives the worker's
command, environment, file descriptors, or stdio streams.
"""

from __future__ import annotations

import json
import os
import select
import time
from queue import Empty, Full, Queue
from threading import Lock, Thread
from typing import Any, Protocol

MAX_MESSAGE = 1024 * 1024
MAX_UNSOLICITED = 32
MAX_STDERR = 16 * 1024


class StdioProtocolError(RuntimeError):
    """The worker violated the bounded stdio protocol or stopped."""


class WorkerProcess(Protocol):
    process: Any

    def close(self) -> None: ...


class StdioPipe:
    """One serial MCP session with bounded pipes and no raw diagnostic output."""

    def __init__(self, worker: WorkerProcess) -> None:
        self.worker = worker
        process = worker.process
        if process.stdin is None or process.stdout is None or process.stderr is None:
            worker.close()
            raise StdioProtocolError()
        self._messages: Queue[dict[str, object] | None] = Queue(maxsize=MAX_UNSOLICITED)
        self._closed = False
        self._lock = Lock()
        self._close_lock = Lock()
        self._stderr_bytes = 0
        self._reader = Thread(target=self._read_messages, daemon=True, name="mcp-stdio-reader")
        self._stderr_reader = Thread(target=self._drain_stderr, daemon=True, name="mcp-stdio-stderr")
        self._reader.start()
        self._stderr_reader.start()

    def _read_messages(self) -> None:
        stream = self.worker.process.stdout
        assert stream is not None
        buffer = bytearray()
        try:
            while not self._closed:
                chunk = os.read(stream.fileno(), 65536)
                if not chunk:
                    raise StdioProtocolError()
                buffer.extend(chunk)
                if len(buffer) > MAX_MESSAGE:
                    raise StdioProtocolError()
                while b"\n" in buffer:
                    line, _, rest = buffer.partition(b"\n")
                    buffer = bytearray(rest)
                    if not line or len(line) > MAX_MESSAGE:
                        raise StdioProtocolError()
                    value = json.loads(line)
                    if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
                        raise StdioProtocolError()
                    try:
                        self._messages.put_nowait(value)
                    except Full:
                        raise StdioProtocolError() from None
        except (OSError, UnicodeError, ValueError, StdioProtocolError):
            pass
        finally:
            self.close()
            try:
                self._messages.put_nowait(None)
            except Full:
                pass

    def _drain_stderr(self) -> None:
        stream = self.worker.process.stderr
        assert stream is not None
        try:
            while not self._closed:
                chunk = os.read(stream.fileno(), 4096)
                if not chunk:
                    break
                self._stderr_bytes += len(chunk)
                if self._stderr_bytes > MAX_STDERR:
                    self.close()
                    break
        except OSError:
            self.close()

    def _write(self, value: dict[str, object], deadline: float) -> None:
        stream = self.worker.process.stdin
        assert stream is not None
        try:
            payload = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8") + b"\n"
        except (TypeError, ValueError):
            raise StdioProtocolError() from None
        if len(payload) > MAX_MESSAGE:
            raise StdioProtocolError()
        offset = 0
        descriptor = stream.fileno()
        while offset < len(payload):
            if self._closed or self.worker.process.poll() is not None:
                raise StdioProtocolError()
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [descriptor], [], remaining)[1]:
                raise StdioProtocolError()
            try:
                offset += os.write(descriptor, payload[offset:])
            except OSError:
                raise StdioProtocolError() from None

    def transact(self, value: dict[str, object], *, timeout_seconds: float = 30) -> dict[str, object] | None:
        """Send one request/notification and return its matching response.

        Server-initiated requests cannot reach Codex through this adapter; they
        receive a method-not-found response so a worker cannot stall forever.
        Notifications are accepted but never forwarded across HTTP sessions.
        """

        if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 120:
            raise ValueError("invalid stdio deadline")
        if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
            raise StdioProtocolError()
        identifier = value.get("id")
        with self._lock:
            deadline = time.monotonic() + timeout_seconds
            try:
                self._write(value, deadline)
                if identifier is None:
                    return None
                unsolicited = 0
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    if self._closed and self._messages.empty():
                        break
                    try:
                        message = self._messages.get(timeout=min(remaining, 0.1))
                    except Empty:
                        continue
                    if message is None:
                        break
                    if message.get("id") == identifier and ("result" in message or "error" in message):
                        return message
                    unsolicited += 1
                    if unsolicited > MAX_UNSOLICITED:
                        break
                    if "method" in message and "id" in message:
                        self._write({"jsonrpc": "2.0", "id": message["id"],
                                     "error": {"code": -32601, "message": "Method not available"}}, deadline)
            except StdioProtocolError:
                pass
            self.close()
            raise StdioProtocolError()

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self.worker.close()
