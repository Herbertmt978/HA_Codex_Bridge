"""The stdio HTTP facade is private, session-scoped and tool-deny-by-default."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading

import httpx
import pytest
from codex_bridge_service import mcp_stdio_adapter as adapter_module
from codex_bridge_service.mcp_stdio_adapter import HEADER, StdioAdapterError, StdioMcpAdapter

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX worker pipes")


class _Worker:
    def __init__(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-c", """
import json,sys
for line in sys.stdin:
    request=json.loads(line)
    if 'id' not in request: continue
    method=request['method']
    if method=='initialize':
        result={'protocolVersion':'2025-03-26','capabilities':{'tools':{}},'serverInfo':{'name':'test-time','version':'1'}}
    elif method=='tools/list':
        result={'tools':[{'name':'get_time','description':'Current time','inputSchema':{'type':'object'}}]}
    elif method=='tools/call':
        result={'content':[{'type':'text','text':'12:00'}]}
    else:
        result={}
    print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)
"""], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=True,
        )

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
        self.process.communicate(timeout=2)


def _adapter() -> tuple[StdioMcpAdapter, dict[str, str]]:
    adapter = StdioMcpAdapter(lambda package, revision: _Worker(),
                              package_verifier=lambda package, revision: object())
    adapter.port = 8767
    adapter.add("time", "time", "1")
    config = adapter.native_config("time", active=True)
    return adapter, {HEADER: config["http_headers"][HEADER]}


@pytest.mark.asyncio
async def test_private_session_and_default_closed_tools() -> None:
    adapter, auth = _adapter()
    transport = httpx.ASGITransport(app=adapter, client=("127.0.0.1", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8767") as client:
        initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
        assert (await client.post("/mcp/time", json=initialize)).status_code == 403
        response = await client.post("/mcp/time", headers=auth, json=initialize)
        assert response.status_code == 200
        session = response.headers["mcp-session-id"]
        with_session = {**auth, "mcp-session-id": session}
        listing = await client.post("/mcp/time", headers=with_session,
                                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert listing.status_code == 200
        assert listing.json()["result"]["tools"] == []
        denied = await client.post("/mcp/time", headers=with_session,
                                   json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_time"}})
        assert denied.status_code == 403
        assert (await client.get("/mcp/time", headers=with_session)).status_code == 405
        assert (await client.post("/mcp/time", headers={**with_session, "origin": "http://evil.test"},
                                  json={"jsonrpc": "2.0", "id": 4, "method": "tools/list"})).status_code == 403
        adapter.set_tools("time", ("get_time",))
        assert (await client.post("/mcp/time", headers=with_session,
                                  json={"jsonrpc": "2.0", "id": 5, "method": "tools/list"})).status_code == 404
        response = await client.post("/mcp/time", headers=auth, json=initialize)
        new_session = {**auth, "mcp-session-id": response.headers["mcp-session-id"]}
        listed = await client.post("/mcp/time", headers=new_session,
                                   json={"jsonrpc": "2.0", "id": 5, "method": "tools/list"})
        assert listed.json()["result"]["tools"][0]["name"] == "get_time"
        allowed = await client.post("/mcp/time", headers=new_session,
                                    json={"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "get_time"}})
        assert allowed.status_code == 200
        assert allowed.json()["result"]["content"][0]["text"] == "12:00"
        assert (await client.delete("/mcp/time", headers=new_session)).status_code == 204
        assert (await client.post("/mcp/time", headers=new_session,
                                  json={"jsonrpc": "2.0", "id": 7, "method": "tools/list"})).status_code == 404


@pytest.mark.asyncio
async def test_concurrent_initialise_starts_only_one_worker() -> None:
    started = threading.Event()
    proceed = threading.Event()
    launches = 0

    def slow_factory(_package: str, _revision: str) -> _Worker:
        nonlocal launches
        launches += 1
        started.set()
        if not proceed.wait(5):
            raise RuntimeError("timed out waiting in fixture")
        return _Worker()

    adapter = StdioMcpAdapter(slow_factory, package_verifier=lambda *_: object())
    adapter.port = 8767
    adapter.add("time", "time", "1")
    config = adapter.native_config("time", active=True)
    auth = {HEADER: config["http_headers"][HEADER]}
    transport = httpx.ASGITransport(app=adapter, client=("127.0.0.1", 12345))
    initialise = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                             "clientInfo": {"name": "test", "version": "1"}}}
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8767") as client:
        first = asyncio.create_task(client.post("/mcp/time", headers=auth, json=initialise))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            second = await client.post("/mcp/time", headers=auth, json=initialise)
            assert second.status_code != 200
            assert launches == 1
        finally:
            proceed.set()
        accepted = await first
        assert accepted.status_code == 200
        assert launches == 1
        adapter.deactivate("time")


@pytest.mark.asyncio
async def test_restart_recognises_previous_private_port(tmp_path) -> None:
    adapter = StdioMcpAdapter(lambda *_: _Worker(),
                              package_verifier=lambda *_: object(), root=tmp_path / "private")
    await adapter.start()
    adapter.add("time", "time", "1")
    old = adapter.native_config("time")
    await adapter.close()

    restarted = StdioMcpAdapter(lambda *_: _Worker(),
                                package_verifier=lambda *_: object(), root=tmp_path / "private")
    await restarted.start()
    try:
        assert restarted.original_binding("time", old)
        if restarted.port != adapter.port:
            assert not restarted.original_binding("time", old, effective=True)
        fresh = restarted.native_config("time")
        assert fresh["http_headers"] == old["http_headers"]
        assert fresh["url"] == f"http://127.0.0.1:{restarted.port}/mcp/time"
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_unsafe_registry_does_not_escape_startup(tmp_path) -> None:
    private = tmp_path / "private"
    private.mkdir()
    (private / "stdio-servers.json").symlink_to(tmp_path / "elsewhere")
    adapter = StdioMcpAdapter(lambda *_: _Worker(),
                              package_verifier=lambda *_: object(), root=private)
    with pytest.raises(StdioAdapterError):
        await adapter.start()


def test_discovery_normalises_worker_failure() -> None:
    def unavailable(_package: str, _revision: str) -> _Worker:
        raise OSError("private worker detail")

    adapter = StdioMcpAdapter(unavailable, package_verifier=lambda *_: object())
    adapter.port = 8767
    adapter.add("time", "time", "1")
    with pytest.raises(StdioAdapterError) as error:
        adapter.discover_tools("time")
    assert "private worker detail" not in str(error.value)


@pytest.mark.asyncio
async def test_late_worker_holds_reservation_until_reaped(monkeypatch) -> None:
    started = threading.Event()
    proceed = threading.Event()
    reaped = threading.Event()
    launches = 0

    def slow_factory(_package: str, _revision: str) -> _Worker:
        nonlocal launches
        launches += 1
        started.set()
        assert proceed.wait(5)
        worker = _Worker()
        original_close = worker.close

        def close() -> None:
            original_close()
            reaped.set()

        worker.close = close
        return worker

    async def force_start_timeout(tasks, *, timeout):
        return set(), tasks

    adapter = StdioMcpAdapter(slow_factory, package_verifier=lambda *_: object())
    adapter.port = 8767
    adapter.add("time", "time", "1")
    config = adapter.native_config("time", active=True)
    auth = {HEADER: config["http_headers"][HEADER]}
    transport = httpx.ASGITransport(app=adapter, client=("127.0.0.1", 12345))
    initialise = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                             "clientInfo": {"name": "test", "version": "1"}}}
    monkeypatch.setattr(adapter_module.asyncio, "wait", force_start_timeout)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8767") as client:
        assert (await client.post("/mcp/time", headers=auth, json=initialise)).status_code == 503
        assert await asyncio.to_thread(started.wait, 2)
        assert (await client.post("/mcp/time", headers=auth, json=initialise)).status_code != 200
        assert launches == 1
        proceed.set()
        assert await asyncio.to_thread(reaped.wait, 3)
        assert adapter._reserved is False
