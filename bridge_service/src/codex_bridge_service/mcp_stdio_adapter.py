"""Private Streamable-HTTP facade for approved isolated stdio MCP workers.

Native Codex sees only this authenticated loopback listener. This adapter is
never an arbitrary command runner: a fixed worker factory verifies a package
revision and proves its independent sandbox before returning a pipe.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import re
import secrets
import socket
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit

import uvicorn

from .mcp_stdio_protocol import (
    MAX_MESSAGE,
    StdioPipe,
    StdioProtocolError,
    WorkerProcess,
)
from .workspace import WorkspaceBoundary, WorkspaceBoundaryError, WorkspaceNotFoundError

HEADER = "x-codex-stdio-mcp"
_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z", re.ASCII)
_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z", re.ASCII)
_TOKEN = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_SESSION_IDLE = 120
_MAX_HTTP_BODY = MAX_MESSAGE - 1
_MAX_REGISTRY = 128 * 1024


class StdioAdapterError(RuntimeError):
    """An isolated worker or private transport is unavailable."""


@dataclass(frozen=True, slots=True)
class StdioRecord:
    package_id: str
    revision: str
    token: str
    allowed_tools: tuple[str, ...] = ()


@dataclass(slots=True)
class _Session:
    name: str
    pipe: StdioPipe
    last_used: float


class StdioMcpAdapter:
    def __init__(self, worker_factory: Callable[[str, str], WorkerProcess], *,
                 package_verifier: Callable[[str, str], object] | None = None,
                 root: Path | None = None) -> None:
        self._worker_factory = worker_factory
        self._package_verifier = package_verifier
        self._root = root
        self._boundary: WorkspaceBoundary | None = None
        self._records: dict[str, StdioRecord] = {}
        self._active: set[str] = set()
        self._sessions: dict[str, _Session] = {}
        self._reserved = False
        self._lock = RLock()
        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task | None = None
        self._reaper_task: asyncio.Task | None = None
        self._socket: socket.socket | None = None
        self.port = 0

    async def start(self) -> None:
        if self._server is not None:
            raise StdioAdapterError()
        try:
            if self._root is not None:
                self._boundary = WorkspaceBoundary(self._root, create=True)
                self._load()
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.bind(("127.0.0.1", 0))
            self._socket.listen(4)
            self._socket.setblocking(False)
            self.port = self._socket.getsockname()[1]
            self._server = uvicorn.Server(uvicorn.Config(
                self, host="127.0.0.1", port=self.port, lifespan="off",
                access_log=False, log_config=None, log_level=None,
                limit_concurrency=8, timeout_keep_alive=5, timeout_graceful_shutdown=2,
            ))
            self._server.capture_signals = _no_signals
            self._server_task = asyncio.create_task(self._server.serve(sockets=[self._socket]))
            async with asyncio.timeout(5):
                while not self._server.started:
                    if self._server_task.done():
                        raise StdioAdapterError()
                    await asyncio.sleep(0.01)
            self._reaper_task = asyncio.create_task(self._reap_idle_sessions())
        except (OSError, TimeoutError, RuntimeError, ValueError, WorkspaceBoundaryError):
            await self.close()
            raise StdioAdapterError() from None

    async def close(self) -> None:
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            await asyncio.gather(self._reaper_task, return_exceptions=True)
            self._reaper_task = None
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._active.clear()
        for session in sessions:
            await asyncio.to_thread(session.pipe.close)
        if self._server is not None:
            self._server.should_exit = True
        if self._server_task is not None:
            await asyncio.gather(self._server_task, return_exceptions=True)
        if self._socket is not None:
            self._socket.close()
        self._server = None
        self._server_task = None
        self._socket = None
        self.port = 0
        if self._boundary is not None:
            self._boundary.close()
            self._boundary = None

    async def _reap_idle_sessions(self) -> None:
        while True:
            await asyncio.sleep(5)
            now = time.monotonic()
            with self._lock:
                expired = [
                    self._sessions.pop(session_id)
                    for session_id, session in tuple(self._sessions.items())
                    if now - session.last_used > _SESSION_IDLE
                ]
            for session in expired:
                await asyncio.to_thread(session.pipe.close)

    def _load(self) -> None:
        assert self._boundary is not None
        try:
            with self._boundary.open_regular_file("stdio-servers.json") as source:
                raw = source.read(_MAX_REGISTRY + 1)
        except WorkspaceNotFoundError:
            self._records = {}
            return
        except (OSError, WorkspaceBoundaryError):
            raise StdioAdapterError() from None
        if len(raw) > _MAX_REGISTRY:
            raise StdioAdapterError()
        try:
            value = json.loads(raw)
            if (not isinstance(value, dict) or set(value) != {"version", "servers"}
                    or value["version"] != 1 or not isinstance(value["servers"], dict)
                    or len(value["servers"]) > 32):
                raise ValueError()
            records: dict[str, StdioRecord] = {}
            for name, item in value["servers"].items():
                if (not isinstance(name, str) or not _NAME.fullmatch(name)
                        or not isinstance(item, dict)
                        or set(item) != {"package_id", "revision", "token", "allowed_tools"}
                        or not isinstance(item["package_id"], str)
                        or not _ID.fullmatch(item["package_id"])
                        or not isinstance(item["revision"], str)
                        or not item["revision"] or len(item["revision"].encode()) > 96
                        or not isinstance(item["token"], str)
                        or not _TOKEN.fullmatch(item["token"])
                        or not isinstance(item["allowed_tools"], list)
                        or len(item["allowed_tools"]) > 512
                        or any(not isinstance(tool, str) or not tool or len(tool.encode()) > 256
                                   for tool in item["allowed_tools"])):
                    raise ValueError()
                records[name] = StdioRecord(item["package_id"], item["revision"],
                                            item["token"], tuple(item["allowed_tools"]))
        except (UnicodeError, ValueError, TypeError):
            raise StdioAdapterError() from None
        self._records = records

    def _save(self, records: dict[str, StdioRecord]) -> None:
        if self._boundary is None:
            return
        raw = json.dumps({"version": 1, "servers": {
            name: {"package_id": item.package_id, "revision": item.revision,
                   "token": item.token, "allowed_tools": list(item.allowed_tools)}
            for name, item in records.items()
        }}, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if len(raw) > _MAX_REGISTRY:
            raise StdioAdapterError()
        try:
            self._boundary.atomic_write_bytes("stdio-servers.json", raw)
        except (OSError, WorkspaceBoundaryError):
            raise StdioAdapterError() from None

    def add(self, name: str, package_id: str, revision: str) -> None:
        if (not _NAME.fullmatch(name) or not _ID.fullmatch(package_id)
                or not revision or len(revision.encode()) > 96 or not self.port):
            raise StdioAdapterError()
        self._verify_package(package_id, revision)
        with self._lock:
            if name in self._records or len(self._records) >= 32:
                raise StdioAdapterError()
            updated = {**self._records, name: StdioRecord(package_id, revision, secrets.token_hex(32))}
            self._save(updated)
            self._records = updated

    def metadata(self, name: str) -> dict[str, object]:
        with self._lock:
            record = self._records[name]
            return {"package_id": record.package_id, "revision": record.revision,
                    "allowed_tools": list(record.allowed_tools)}

    def set_tools(self, name: str, selected: tuple[str, ...]) -> None:
        if len(selected) > 512 or any(not isinstance(tool, str) or not tool or len(tool.encode()) > 256 for tool in selected):
            raise StdioAdapterError()
        with self._lock:
            old = self._records[name]
            self._stop_sessions(name)
            updated = {**self._records,
                       name: StdioRecord(old.package_id, old.revision, old.token, selected)}
            self._save(updated)
            self._records = updated

    def replace_package(self, name: str, revision: str) -> None:
        with self._lock:
            old = self._records.get(name)
            if old is None or name in self._active:
                raise StdioAdapterError()
            self._verify_package(old.package_id, revision)
            self._stop_sessions(name)
            updated = {**self._records, name: StdioRecord(old.package_id, revision, old.token)}
            self._save(updated)
            self._records = updated

    def native_config(self, name: str, *, active: bool = False) -> dict[str, object]:
        with self._lock:
            if not self.port or name not in self._records:
                raise StdioAdapterError()
            if active:
                record = self._records[name]
                self._verify_package(record.package_id, record.revision)
                self._active.add(name)
            else:
                self.deactivate(name)
            record = self._records[name]
            return {"url": f"http://127.0.0.1:{self.port}/mcp/{name}",
                    "http_headers": {HEADER: record.token}}

    def _verify_package(self, package_id: str, revision: str) -> object:
        if self._package_verifier is None:
            raise StdioAdapterError()
        try:
            return self._package_verifier(package_id, revision)
        except Exception:  # noqa: BLE001 - verification failure always blocks activation
            raise StdioAdapterError() from None

    def discover_tools(self, name: str) -> list[dict[str, object]]:
        """Discover with an isolated, temporary worker, never a native grant."""
        with self._lock:
            record = self._records.get(name)
            if record is None or self._reserved or self._sessions:
                raise StdioAdapterError()
            self._reserved = True
        pipe: StdioPipe | None = None
        try:
            self._verify_package(record.package_id, record.revision)
            pipe = StdioPipe(self._worker_factory(record.package_id, record.revision))
            result = pipe.transact({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                    "params": {"protocolVersion": "2025-03-26",
                                               "capabilities": {},
                                               "clientInfo": {"name": "Codex Bridge", "version": "1"}}},
                                   timeout_seconds=10)
            if not isinstance(result, dict) or not isinstance(result.get("result"), dict):
                raise StdioAdapterError()
            pipe.transact({"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout_seconds=5)
            listing = pipe.transact({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, timeout_seconds=10)
            tools = listing.get("result", {}).get("tools") if isinstance(listing, dict) else None
            if not isinstance(tools, list) or len(tools) > 512:
                raise StdioAdapterError()
            discovered: list[dict[str, object]] = []
            for tool in tools:
                if (not isinstance(tool, dict) or not isinstance(tool.get("name"), str)
                        or not tool["name"] or len(tool["name"].encode()) > 256):
                    raise StdioAdapterError()
                discovered.append(tool)
            return discovered
        except Exception:  # noqa: BLE001 - untrusted worker failures are unavailable, never a 500
            raise StdioAdapterError() from None
        finally:
            if pipe is not None:
                pipe.close()
            with self._lock:
                self._reserved = False

    def original_binding(self, name: str, value: object, *, effective: bool = False) -> bool:
        if not isinstance(value, dict):
            return False
        if effective:
            defaults = {"enabled": True, "environment_id": "local", "tool_timeout_sec": None}
            for key, expected in defaults.items():
                if key in value and (type(value[key]) is not type(expected) or value[key] != expected):
                    return False
            value = {key: item for key, item in value.items() if key not in defaults}
        with self._lock:
            record = self._records.get(name)
            if record is None or set(value) != {"url", "http_headers"}:
                return False
            url = value["url"]
            if not isinstance(url, str) or value["http_headers"] != {HEADER: record.token}:
                return False
            try:
                parsed = urlsplit(url)
                return (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                        and parsed.port is not None and (not effective or parsed.port == self.port)
                        and parsed.path == f"/mcp/{name}"
                        and not parsed.username and not parsed.password and not parsed.query
                        and not parsed.fragment)
            except (ValueError, TypeError):
                return False

    def deactivate(self, name: str) -> None:
        with self._lock:
            self._active.discard(name)
            self._stop_sessions(name)

    def remove(self, name: str) -> None:
        with self._lock:
            self.deactivate(name)
            updated = {key: item for key, item in self._records.items() if key != name}
            self._save(updated)
            self._records = updated

    def retain(self, names: set[str]) -> None:
        with self._lock:
            for name in set(self._records) - names:
                self.deactivate(name)
            updated = {key: item for key, item in self._records.items() if key in names}
            if updated != self._records:
                self._save(updated)
                self._records = updated

    def _stop_sessions(self, name: str) -> None:
        for session_id, session in tuple(self._sessions.items()):
            if session.name == name:
                self._sessions.pop(session_id, None)
                session.pipe.close()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return
        name = scope.get("path", "").removeprefix("/mcp/")
        headers = _headers(scope)
        with self._lock:
            record = self._records.get(name)
            admitted = (scope.get("client", (None,))[0] == "127.0.0.1"
                and scope.get("path") == f"/mcp/{name}" and not scope.get("query_string")
                and headers.get("host") == f"127.0.0.1:{self.port}"
                and "origin" not in headers and "cookie" not in headers
                and record is not None and name in self._active
                and hmac.compare_digest(headers.get(HEADER, ""), record.token))
        if not admitted:
            return await _reply(send, 403)
        method = scope["method"]
        if method == "GET":
            return await _reply(send, 405)
        if method == "DELETE":
            session_id = headers.get("mcp-session-id", "")
            with self._lock:
                session = self._sessions.pop(session_id, None)
            if session is None or session.name != name:
                return await _reply(send, 404)
            await asyncio.to_thread(session.pipe.close)
            return await _reply(send, 204)
        if method != "POST":
            return await _reply(send, 405)
        if headers.get("content-type", "").split(";")[0] != "application/json":
            return await _reply(send, 415)
        raw = bytearray()
        try:
            async with asyncio.timeout(10):
                while True:
                    part = await receive()
                    if part["type"] == "http.disconnect":
                        return
                    raw.extend(part.get("body", b""))
                    if len(raw) > _MAX_HTTP_BODY:
                        return await _reply(send, 413)
                    if not part.get("more_body", False):
                        break
            message = json.loads(raw)
        except (TimeoutError, ValueError, UnicodeError):
            return await _reply(send, 400)
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return await _reply(send, 400)
        session_id = headers.get("mcp-session-id", "")
        is_init = message.get("method") == "initialize" and "id" in message
        with self._lock:
            self._expire_idle()
            if is_init and not session_id and not self._sessions and not self._reserved:
                session_id = secrets.token_urlsafe(32)
                create = True
                self._reserved = True
            else:
                create = False
            session = self._sessions.get(session_id)
            if session is not None and session.name != name:
                session = None
        if create:
            pending: asyncio.Task | None = None
            pipe: StdioPipe | None = None
            late_start = False

            def reap_late(task: asyncio.Task) -> None:
                try:
                    _close_late_worker(task)
                finally:
                    with self._lock:
                        self._reserved = False

            try:
                pending = asyncio.create_task(asyncio.to_thread(
                    self._worker_factory, record.package_id, record.revision))
                finished, _ = await asyncio.wait({pending}, timeout=10)
                if not finished:
                    late_start = True
                    pending.add_done_callback(reap_late)
                    return await _reply(send, 503)
                worker = pending.result()
                pipe = StdioPipe(worker)
                session = _Session(name, pipe, time.monotonic())
                with self._lock:
                    rejected = (name not in self._active or self._records.get(name) != record
                                or bool(self._sessions))
                    if not rejected:
                        self._sessions[session_id] = session
                        pipe = None
                if rejected:
                    await asyncio.to_thread(pipe.close)
                    return await _reply(send, 429)
            except asyncio.CancelledError:
                if pipe is not None:
                    pipe.close()
                elif pending is not None:
                    if pending.done():
                        _close_late_worker(pending)
                    else:
                        late_start = True
                        pending.add_done_callback(reap_late)
                raise
            except Exception:  # noqa: BLE001 - an untrusted worker start must fail closed
                if pipe is not None:
                    pipe.close()
                return await _reply(send, 503)
            finally:
                if not late_start:
                    with self._lock:
                        self._reserved = False
        if session is None:
            return await _reply(send, 404 if session_id else 400)
        if message.get("method") == "tools/call":
            params = message.get("params")
            tool = params.get("name") if isinstance(params, dict) else None
            if tool not in record.allowed_tools:
                return await _reply(send, 403)
        elif message.get("method") not in {"initialize", "notifications/initialized", "notifications/cancelled", "ping", "tools/list"}:
            return await _reply(send, 403)
        try:
            result = await asyncio.to_thread(session.pipe.transact, message, timeout_seconds=30)
        except StdioProtocolError:
            with self._lock:
                self._sessions.pop(session_id, None)
            return await _reply(send, 502)
        session.last_used = time.monotonic()
        if result is None:
            if message.get("method") == "notifications/cancelled":
                with self._lock:
                    self._sessions.pop(session_id, None)
                await asyncio.to_thread(session.pipe.close)
            return await _reply(send, 202)
        if message.get("method") == "tools/list" and isinstance(result, dict):
            body = result.get("result")
            tools = body.get("tools") if isinstance(body, dict) else None
            if not isinstance(tools, list):
                return await _reply(send, 502)
            result = {**result, "result": {**body, "tools": [
                tool for tool in tools
                if isinstance(tool, dict) and tool.get("name") in record.allowed_tools
            ]}}
        return await _reply(send, 200, result, session_id=session_id if create else None)

    def _expire_idle(self) -> None:
        now = time.monotonic()
        for session_id, session in tuple(self._sessions.items()):
            if now - session.last_used > _SESSION_IDLE:
                self._sessions.pop(session_id, None)
                session.pipe.close()


def _headers(scope) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in scope.get("headers", []):
        try:
            name = key.decode("ascii").lower()
            if name in result:
                return {}
            result[name] = value.decode("ascii")
        except UnicodeDecodeError:
            return {}
    return result


def _close_late_worker(task: asyncio.Task) -> None:
    try:
        worker = task.result()
    except Exception:  # noqa: BLE001 - late worker launch must never escape cleanup
        return
    worker.close()


async def _reply(send, status: int, payload: object = None, *, session_id: str | None = None) -> None:
    headers = [(b"cache-control", b"no-store")]
    body = b""
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_MESSAGE:
            status, body = 502, b""
        else:
            headers.append((b"content-type", b"application/json"))
    if session_id is not None and status == 200:
        headers.append((b"mcp-session-id", session_id.encode("ascii")))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


@contextmanager
def _no_signals():
    yield
