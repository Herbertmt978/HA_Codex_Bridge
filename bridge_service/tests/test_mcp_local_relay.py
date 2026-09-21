from __future__ import annotations

import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
import ssl
import subprocess
from threading import Thread

import httpx
import pytest
import pytest_asyncio

from codex_bridge_service.mcp_local_policy import LocalMcpError, private_address
from codex_bridge_service.mcp_local_relay import LocalMcpRelay, RELAY_HEADER

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.name != "posix", reason="Private no-follow storage requires Linux")]


@pytest.fixture
def lan_address():
    # UDP connect chooses a route without sending a packet. Tests then contact
    # only this machine's disposable listener, never the route destination.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("192.0.2.1", 9))
        return private_address(sock.getsockname()[0])


@contextmanager
def upstream(address, *, tls=None, status=200, body=b'{"result":"ok"}', content_type="application/json"):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append((self.path, dict(self.headers), self.rfile.read(int(self.headers.get("Content-Length", 0)))))
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Location", "http://169.254.169.254/metadata")
            self.send_header("Set-Cookie", "private=secret")
            self.send_header("WWW-Authenticate", "Bearer private")
            self.send_header("Mcp-Session-Id", "test-session")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer((address, 0), Handler)
    if tls:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest_asyncio.fixture
async def relay(tmp_path, lan_address):
    instance = LocalMcpRelay(tmp_path / "private", resolver=lambda _: (lan_address,))
    await instance.start()
    try:
        yield instance
    finally:
        await instance.close()


async def post(binding, **kwargs):
    async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
        return await client.post(binding["url"], headers=binding["http_headers"], json={"test": True}, **kwargs)


async def test_exact_destination_header_isolation_and_no_url_logs(relay, lan_address, caplog, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    with upstream(lan_address) as (port, calls):
        relay.add("home", f"http://ha.local:{port}/private-test-path")
        binding = relay.native_config("home")
        async with httpx.AsyncClient(trust_env=False) as client:
            result = await client.post(binding["url"], json={"test": True}, headers={
                **binding["http_headers"], "Authorization": "Bearer never-forward",
                "X-Forwarded-Host": "unexpected", "Mcp-Protocol-Version": "2025-03-26",
            })
        assert result.status_code == 200
        assert result.json() == {"result": "ok"}
        assert result.headers["mcp-session-id"] == "test-session"
        assert not {"location", "set-cookie", "www-authenticate"} & result.headers.keys()
        path, headers, _ = calls[0]
        headers = {key.lower(): value for key, value in headers.items()}
        assert path == "/private-test-path"
        assert headers["host"] == f"ha.local:{port}"
        assert headers["mcp-protocol-version"] == "2025-03-26"
        assert not {RELAY_HEADER.lower(), "authorization", "x-forwarded-host"} & headers.keys()
        assert "private-test-path" not in caplog.text
        assert binding["http_headers"][RELAY_HEADER] not in caplog.text


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 500])
async def test_redirects_and_auth_challenges_are_not_forwarded(relay, lan_address, status):
    with upstream(lan_address, status=status) as (port, calls):
        relay.add("home", f"http://ha.local:{port}/mcp")
        result = await post(relay.native_config("home"))
        assert result.status_code == 502
        assert len(calls) == 1
        assert "metadata" not in result.text
        assert "location" not in result.headers


async def test_rebinding_fails_before_any_connection(relay, lan_address):
    with upstream(lan_address) as (port, calls):
        relay.add("home", f"http://ha.local:{port}/mcp")
        binding = relay.native_config("home")
        relay._resolver = lambda _: ("192.168.254.250",)
        assert (await post(binding)).status_code == 502
        relay._resolver = lambda _: (lan_address, "8.8.8.8")
        assert (await post(binding)).status_code == 502
        assert calls == []


@pytest.mark.parametrize("status", [404, 405])
async def test_protocol_session_expiry_is_preserved_without_provider_body(relay, lan_address, status):
    with upstream(lan_address, status=status, body=b"private error") as (port, _):
        relay.add("home", f"http://ha.local:{port}/mcp")
        response = await post(relay.native_config("home"))
        assert response.status_code == status
        assert "private error" not in response.text


async def test_sse_response_is_streamed(relay, lan_address):
    body = b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'
    with upstream(lan_address, content_type="text/event-stream", body=body) as (port, _):
        relay.add("home", f"http://ha.local:{port}/mcp")
        response = await post(relay.native_config("home"))
        assert response.status_code == 200
        assert response.content == body


async def test_unavailable_endpoint_returns_only_fixed_error(relay, lan_address):
    with upstream(lan_address) as (port, _):
        pass
    relay.add("home", f"http://ha.local:{port}/private-path")
    response = await post(relay.native_config("home"))
    assert response.status_code == 502
    assert response.text == "Local MCP request unavailable"


async def test_stream_limit_terminates_incomplete_response(relay, monkeypatch):
    import codex_bridge_service.mcp_local_relay as module
    monkeypatch.setattr(module, "_MAX_RESPONSE", 16)
    class OversizedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"a" * 8
            yield b"b" * 16
    class Transport:
        async def handle_async_request(self, request):
            return httpx.Response(200, headers={"Content-Type":"text/event-stream"}, stream=OversizedStream())
        async def aclose(self):
            pass
    await relay._transport.aclose()
    relay._transport = Transport()
    relay.add("home", "http://ha.local/mcp")
    with pytest.raises(httpx.RemoteProtocolError):
        await post(relay.native_config("home"))


async def test_unauthorised_and_removed_bindings_cannot_connect(relay, lan_address):
    with upstream(lan_address) as (port, calls):
        relay.add("home", f"http://ha.local:{port}/mcp")
        binding = relay.native_config("home")
        async with httpx.AsyncClient(trust_env=False) as client:
            for suffix, headers in [
                ("", {}), ("?target=http://other", binding["http_headers"]),
                ("", {**binding["http_headers"], "Origin": "http://evil"}),
                ("", {**binding["http_headers"], "Cookie": "private=value"}),
            ]:
                result = await client.post(binding["url"] + suffix, headers=headers, json={})
                assert result.status_code == 403
        relay.remove("home")
        assert (await post(binding)).status_code == 403
        assert not calls


async def test_private_registry_restart_requires_matching_native_binding(tmp_path, lan_address):
    root = tmp_path / "private"
    first = LocalMcpRelay(root, resolver=lambda _: (lan_address,))
    await first.start()
    first.add("home", "http://ha.local/secret-path")
    binding = first.native_config("home")
    await first.close()
    assert (root / "servers.json").stat().st_mode & 0o777 == 0o600
    second = LocalMcpRelay(root, resolver=lambda _: (lan_address,))
    await second.start()
    try:
        assert second.original_url("home", binding) == "http://ha.local/secret-path"
        expanded = {**binding, "enabled": True, "environment_id": "local", "tool_timeout_sec": None}
        assert second.original_url("home", expanded) is None
        assert second.original_url("home", expanded, effective=True) == "http://ha.local/secret-path"
        for changes in [{"enabled": False}, {"enabled": 1}, {"environment_id": "other"}, {"tool_timeout_sec": 300}, {"env": {}}]:
            assert second.original_url("home", {**expanded, **changes}, effective=True) is None
        for value in [None, {**binding, "url": 42}, {**binding, "extra": True},
                      {**binding, "url": "http://127.0.0.1:1234/mcp/other"},
                      {**binding, "http_headers": {RELAY_HEADER: "wrong"}}]:
            assert second.original_url("home", value) is None
        assert not second._active
        second.retain(set())
        assert second.original_url("home", binding) is None
        assert json.loads((root / "servers.json").read_text())["servers"] == {}
    finally:
        await second.close()


async def test_registry_symlink_and_invalid_content_fail_closed(tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"version":1,"servers":{}}')
    registry = root / "servers.json"
    registry.symlink_to(outside)
    with pytest.raises(LocalMcpError):
        await LocalMcpRelay(root).start()
    registry.unlink()
    registry.write_text("private-invalid-data")
    with pytest.raises(LocalMcpError) as error:
        await LocalMcpRelay(root).start()
    assert "private-invalid-data" not in str(error.value)
    assert outside.read_text() == '{"version":1,"servers":{}}'


async def test_body_response_limits_and_mime(relay, lan_address, monkeypatch):
    import codex_bridge_service.mcp_local_relay as module
    monkeypatch.setattr(module, "_MAX_BODY", 16)
    monkeypatch.setattr(module, "_MAX_RESPONSE", 16)
    with upstream(lan_address, body=b"x" * 17) as (port, calls):
        relay.add("home", f"http://ha.local:{port}/mcp")
        binding = relay.native_config("home")
        async with httpx.AsyncClient(trust_env=False) as client:
            result = await client.post(binding["url"], headers=binding["http_headers"], content=b"x" * 17)
            assert result.status_code == 413
        assert not calls
        assert (await post(binding)).status_code == 502


async def test_https_pins_ip_but_checks_original_hostname(relay, lan_address, tmp_path):
    key, cert = tmp_path / "key.pem", tmp_path / "cert.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-keyout", str(key), "-out", str(cert), "-subj", "/CN=ha.local",
        "-addext", "subjectAltName=DNS:ha.local",
    ], check=True, capture_output=True)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, key)
    names = []
    server_context.set_servername_callback(lambda sock, name, ctx: names.append(name))
    with upstream(lan_address, tls=server_context) as (port, calls):
        relay.add("home", f"https://ha.local:{port}/private-path")
        # Production's default trust rejects the disposable self-signed CA.
        assert (await post(relay.native_config("home"))).status_code == 502
        await relay._transport.aclose()
        context = ssl.create_default_context(cafile=str(cert))
        relay._transport = httpx.AsyncHTTPTransport(verify=context, trust_env=False)
        assert (await post(relay.native_config("home"))).status_code == 200
        relay.add("wrong", f"https://wrong.local:{port}/private-path")
        assert (await post(relay.native_config("wrong"))).status_code == 502
        assert names == ["ha.local", "ha.local", "wrong.local"]
        assert len(calls) == 1


async def test_removal_cancels_inflight_and_releases_slots(relay, lan_address):
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    class WaitingTransport:
        async def handle_async_request(self, request):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        async def aclose(self):
            pass
    await relay._transport.aclose()
    relay._transport = WaitingTransport()
    relay.add("home", "http://ha.local/mcp")
    request = asyncio.create_task(post(relay.native_config("home")))
    await asyncio.wait_for(entered.wait(), 2)
    relay.remove("home")
    await asyncio.wait_for(cancelled.wait(), 2)
    result = await asyncio.gather(request, return_exceptions=True)
    assert isinstance(result[0], httpx.HTTPError) or result[0].status_code >= 400
    assert not relay._tasks.get("home")
