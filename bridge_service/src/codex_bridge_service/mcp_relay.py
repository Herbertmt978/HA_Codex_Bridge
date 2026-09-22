"""Private, bounded relay for local and statically authenticated MCP endpoints.

Native Codex receives only a loopback URL and generated capability header. The
Original URLs and upstream credentials never enter native diagnostics.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
import hmac
import json
from pathlib import Path
import re
import secrets
import socket
from threading import BoundedSemaphore, RLock
from urllib.parse import urlsplit

import httpx
import uvicorn

from .mcp_local_policy import (
    LocalMcpError, canonical_local_url, checked_addresses, pinned_address, resolve_addresses,
)
from .mcp_credentials import CredentialRedactor, McpCredential, parse_credential
from .workspace import WorkspaceBoundary, WorkspaceNotFoundError

RELAY_HEADER = "X-Codex-Local-Mcp"
_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z", re.ASCII)
_TOKEN = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_MAX_BODY = 1024 * 1024
_MAX_RESPONSE = 8 * 1024 * 1024
_MAX_REGISTRY = 512 * 1024
_REQUEST_HEADERS = {"accept", "content-type", "mcp-session-id", "mcp-protocol-version", "last-event-id"}
_RESPONSE_HEADERS = {b"content-type", b"mcp-session-id", b"mcp-protocol-version"}


@dataclass(frozen=True)
class McpRecord:
    url: str = field(repr=False)
    addresses: tuple[str, ...]
    token: str = field(repr=False)
    local: bool = True
    credential: McpCredential = field(default_factory=McpCredential, repr=False)


class McpRelay:
    def __init__(self, root: Path, *, resolver: Callable[[str], tuple[str, ...]] = resolve_addresses, local_enabled: bool = True) -> None:
        self._root = root
        self._resolver = resolver
        self.local_enabled = local_enabled
        self._boundary: WorkspaceBoundary | None = None
        self._records: dict[str, McpRecord] = {}
        self._active: set[str] = set()
        self._lock = RLock()
        self._tasks: dict[str, set[asyncio.Task]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task | None = None
        self._socket: socket.socket | None = None
        self._transport: httpx.AsyncHTTPTransport | None = None
        self._resolver_pool: ThreadPoolExecutor | None = None
        self._dns_slots = BoundedSemaphore(4)
        self.port = 0

    async def start(self) -> None:
        if self._server is not None:
            raise LocalMcpError()
        try:
            self._boundary = WorkspaceBoundary(self._root, create=True)
            self._load()
            self._loop = asyncio.get_running_loop()
            self._resolver_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="local-mcp-dns")
            self._transport = httpx.AsyncHTTPTransport(
                verify=True, trust_env=False, retries=0,
                limits=httpx.Limits(max_connections=16, max_keepalive_connections=0),
            )
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.bind(("127.0.0.1", 0))
            self._socket.listen(32)
            self._socket.setblocking(False)
            self.port = self._socket.getsockname()[1]
            self._server = uvicorn.Server(uvicorn.Config(
                self, host="127.0.0.1", port=self.port, lifespan="off",
                access_log=False, log_config=None, log_level=None,
                limit_concurrency=32, timeout_keep_alive=5, timeout_graceful_shutdown=2,
            ))
            # This subordinate listener must not install process signal handlers.
            self._server.capture_signals = _no_signals
            self._server_task = asyncio.create_task(self._server.serve(sockets=[self._socket]))
            async with asyncio.timeout(5):
                while not self._server.started:
                    if self._server_task.done():
                        raise LocalMcpError()
                    await asyncio.sleep(0.01)
        except Exception:
            await self.close()
            raise LocalMcpError() from None

    async def close(self) -> None:
        with self._lock:
            self._active.clear()
            tasks = [task for group in self._tasks.values() for task in group]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._server is not None:
            self._server.should_exit = True
        if self._server_task is not None:
            await asyncio.gather(self._server_task, return_exceptions=True)
        if self._transport is not None:
            await self._transport.aclose()
        if self._socket is not None:
            self._socket.close()
        if self._boundary is not None:
            self._boundary.close()
        if self._resolver_pool is not None:
            self._resolver_pool.shutdown(wait=False, cancel_futures=True)
            self._resolver_pool = None
        self.port = 0
        self._server = None

    def _load(self) -> None:
        assert self._boundary is not None
        self._records = {}
        try:
            with self._boundary.open_regular_file("servers.json") as source:
                raw = source.read(_MAX_REGISTRY + 1)
        except WorkspaceNotFoundError:
            return
        if len(raw) > _MAX_REGISTRY:
            raise LocalMcpError()
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {"version", "servers"} or type(value["version"]) is not int or value["version"] not in {1, 2}:
            raise LocalMcpError()
        records = value["servers"]
        if not isinstance(records, dict) or len(records) > 32:
            raise LocalMcpError()
        for name, item in records.items():
            if (not _NAME.fullmatch(name) or not isinstance(item, dict)
                    or set(item) != ({"url", "addresses", "token"} if value["version"] == 1 else {"url", "addresses", "token", "local", "credential"})
                    or not isinstance(item["token"], str) or not _TOKEN.fullmatch(item["token"])):
                raise LocalMcpError()
            local = item.get("local", True)
            if type(local) is not bool:
                raise LocalMcpError()
            credential = parse_credential(item["credential"], stored=True) if value["version"] == 2 else McpCredential()
            if not local and credential.mode == "none":
                raise LocalMcpError()
            self._records[name] = McpRecord(canonical_local_url(item["url"], local=local), checked_addresses(item["addresses"], local=local), item["token"], local, credential)

    def _save(self, records: Mapping[str, McpRecord]) -> None:
        if self._boundary is None:
            raise LocalMcpError()
        payload = {"version": 2, "servers": {
            name: {"url": item.url, "addresses": item.addresses, "token": item.token, "local": item.local, "credential": item.credential.stored()}
            for name, item in records.items()
        }}
        raw = json.dumps(payload, ensure_ascii=True).encode()
        if len(raw) > _MAX_REGISTRY:
            raise LocalMcpError()
        self._boundary.atomic_write_bytes("servers.json", raw)

    def add(self, name: str, url: object, *, local: bool = True, credential: McpCredential = McpCredential()) -> str:
        if not _NAME.fullmatch(name) or not self.port:
            raise LocalMcpError()
        if (local and not self.local_enabled) or (not local and credential.mode == "none"):
            raise LocalMcpError()
        canonical = canonical_local_url(url, local=local)
        try:
            addresses = checked_addresses(self._resolve(urlsplit(canonical).hostname or "").result(timeout=5), local=local)
        except Exception:
            raise LocalMcpError() from None
        with self._lock:
            if name in self._records or len(self._records) >= 32:
                raise LocalMcpError()
            item = McpRecord(canonical, addresses, secrets.token_hex(32), local, credential)
            updated = {**self._records, name: item}
            self._save(updated)
            self._records = updated
        return canonical

    def metadata(self, name: str) -> dict[str, object]:
        with self._lock:
            record = self._records[name]
            return {"local": record.local, "auth": record.credential.mode,
                    "credential_configured": record.credential.configured}

    def replace_credential(self, name: str, credential: McpCredential | None) -> None:
        with self._lock:
            old = self._records[name]
            if old.credential.mode == "none":
                raise LocalMcpError()
            self._active.discard(name)
            if self._loop is not None:
                for task in tuple(self._tasks.get(name, ())):
                    self._loop.call_soon_threadsafe(task.cancel)
            updated = {**self._records, name: McpRecord(old.url, old.addresses, old.token, old.local,
                credential if credential is not None else McpCredential(old.credential.mode))}
            # Persistence failure leaves this connection revoked in memory.
            self._save(updated)
            self._records = updated

    def _resolve(self, host: str):
        # Timed-out OS DNS calls cannot be killed. Keep their slots occupied
        # until they actually finish, so repeated timeouts cannot queue work.
        if self._resolver_pool is None or not self._dns_slots.acquire(blocking=False):
            raise LocalMcpError()
        try:
            future = self._resolver_pool.submit(self._resolver, host)
        except Exception:
            self._dns_slots.release()
            raise LocalMcpError() from None
        future.add_done_callback(lambda _: self._dns_slots.release())
        return future

    def native_config(self, name: str) -> dict[str, object]:
        with self._lock:
            if not self.port or name not in self._records:
                raise LocalMcpError()
            if self._records[name].local and not self.local_enabled:
                raise LocalMcpError()
            self._active.add(name)
            return {"url": f"http://127.0.0.1:{self.port}/mcp/{name}",
                    "http_headers": {RELAY_HEADER: self._records[name].token}}

    def original_url(self, name: str, value: object, *, effective: bool = False) -> str | None:
        if effective and isinstance(value, Mapping):
            # config/read expands these defaults in the effective projection.
            # Persisted user-layer bindings must still have exactly our two keys.
            defaults = {"enabled": True, "environment_id": "local", "tool_timeout_sec": None}
            for key, expected in defaults.items():
                if key in value and (type(value[key]) is not type(expected) or value[key] != expected):
                    return None
            value = {key: item for key, item in value.items() if key not in defaults}
        with self._lock:
            record = self._records.get(name)
            if record is not None and record.local and not self.local_enabled:
                return None
            if record is None or not isinstance(value, Mapping) or set(value) != {"url", "http_headers"}:
                return None
            if not isinstance(value["url"], str) or value["http_headers"] != {RELAY_HEADER: record.token}:
                return None
            try:
                parsed = urlsplit(value["url"])
                if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                        or not parsed.port or parsed.username or parsed.password
                        or parsed.path != f"/mcp/{name}" or parsed.query or parsed.fragment):
                    return None
            except (ValueError, TypeError):
                return None
            return record.url

    def retain(self, names: set[str]) -> None:
        with self._lock:
            removed = set(self._records) - names
            if removed:
                updated = {name: value for name, value in self._records.items() if name in names}
                # Revoke memory before disk persistence, including on disk failure.
                self._active.difference_update(removed)
                for name in removed:
                    if self._loop is not None:
                        for task in tuple(self._tasks.get(name, ())):
                            self._loop.call_soon_threadsafe(task.cancel)
                self._save(updated)
                self._records = updated

    def remove(self, name: str) -> None:
        with self._lock:
            self.retain(set(self._records) - {name})

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return
        response = None
        disconnected = None
        started = False
        admitted = False
        name = scope.get("path", "").removeprefix("/mcp/")
        task = asyncio.current_task()
        try:
            headers = httpx.Headers(scope["headers"])
            if (scope.get("client", (None,))[0] != "127.0.0.1"
                    or scope.get("path") != f"/mcp/{name}" or scope.get("query_string")
                    or headers.get("host") != f"127.0.0.1:{self.port}"
                    or "origin" in headers or "cookie" in headers):
                await _error(send, 403)
                return
            with self._lock:
                record = self._records.get(name)
                if (record is None or name not in self._active
                        or not hmac.compare_digest(headers.get(RELAY_HEADER, ""), record.token)):
                    record = None
                elif (sum(len(group) for group in self._tasks.values()) >= 16
                        or len(self._tasks.get(name, ())) >= 4):
                    pass
                else:
                    self._tasks.setdefault(name, set()).add(task)
                    admitted = True
            if record is None:
                await _error(send, 403)
                return
            if not admitted:
                await _error(send, 429)
                return
            if record.credential.mode != "none" and not record.credential.configured:
                await _error(send, 401)
                return
            if scope["method"] not in {"POST", "GET", "DELETE"}:
                await _error(send, 405)
                return
            async with asyncio.timeout(300):
                body = bytearray()
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > _MAX_BODY:
                        await _error(send, 413)
                        return
                    if not message.get("more_body", False):
                        break
                if scope["method"] == "POST" and headers.get("content-type", "").split(";")[0] != "application/json":
                    await _error(send, 415)
                    return
                disconnected = asyncio.create_task(_watch_disconnect(receive, task))
                target = httpx.URL(record.url)
                answers = await asyncio.wait_for(asyncio.wrap_future(self._resolve(target.host)), timeout=5)
                address = pinned_address(answers, record.addresses, local=record.local)
                upstream_headers = {key: value for key, value in headers.items() if key.lower() in _REQUEST_HEADERS}
                upstream_headers["host"] = target.netloc.decode("ascii")
                upstream_headers.update(record.credential.headers)
                request = httpx.Request(
                    scope["method"], target.copy_with(host=address),
                    headers=upstream_headers, content=bytes(body),
                    extensions={"sni_hostname": target.host,
                                "timeout": {"connect": 5, "read": 60, "write": 10, "pool": 5}},
                )
                assert self._transport is not None
                # Use the transport directly: no redirects, cookies, environment
                # proxy discovery or httpx.Client request-URL logging.
                response = await self._transport.handle_async_request(request)
                if not 200 <= response.status_code < 300:
                    # MCP uses 404 to expire a session and trigger a fresh
                    # initialise request. Authentication challenges stay blocked.
                    allowed = {404, 405} | ({401, 403} if record.credential.mode != "none" else set())
                    await _error(send, response.status_code if response.status_code in allowed else 502)
                    return
                content_type = response.headers.get("content-type", "").split(";")[0]
                if response.status_code not in {202, 204} and content_type not in {"application/json", "text/event-stream"}:
                    raise LocalMcpError()
                length = response.headers.get("content-length")
                if length is not None and (not length.isdigit() or int(length) > _MAX_RESPONSE):
                    raise LocalMcpError()
                redactor = CredentialRedactor(record.credential)
                safe_headers = [(key, CredentialRedactor(record.credential).feed(value, final=True))
                                for key, value in response.headers.raw if key.lower() in _RESPONSE_HEADERS]
                await send({"type": "http.response.start", "status": response.status_code,
                            "headers": safe_headers})
                started = True
                size = 0
                async for chunk in response.aiter_raw():
                    size += len(chunk)
                    if size > _MAX_RESPONSE:
                        raise LocalMcpError()
                    await send({"type": "http.response.body", "body": redactor.feed(chunk), "more_body": True})
                await send({"type": "http.response.body", "body": redactor.feed(b"", final=True), "more_body": False})
        except asyncio.CancelledError:
            # Revocation and client disconnect are expected lifecycle events.
            # Keep incomplete streams incomplete without logging a traceback.
            if not started:
                await _error(send, 502)
        except Exception:
            if not started:
                await _error(send, 502)
            # If headers were sent, leave the response incomplete. Uvicorn closes
            # the connection; never manufacture a successful truncated response.
        finally:
            if disconnected is not None:
                disconnected.cancel()
                await asyncio.gather(disconnected, return_exceptions=True)
            if response is not None:
                with suppress(Exception, asyncio.CancelledError):
                    await response.aclose()
            if admitted:
                with self._lock:
                    self._tasks.get(name, set()).discard(task)


async def _error(send, status: int) -> None:
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain"), (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": b"Local MCP request unavailable"})


async def _watch_disconnect(receive, task) -> None:
    while True:
        if (await receive())["type"] == "http.disconnect":
            task.cancel()
            return


@contextmanager
def _no_signals():
    yield
