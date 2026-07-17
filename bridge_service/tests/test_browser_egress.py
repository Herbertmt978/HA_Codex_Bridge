from __future__ import annotations

import asyncio
import socket

import pytest

from codex_bridge_service.browser_egress import (
    BrowserEgressError,
    BrowserPolicyProxy,
    PinnedEndpointConnector,
    validate_resolved_endpoints,
)


def _record(address: str, port: int = 443) -> tuple[object, ...]:
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    sockaddr: tuple[object, ...]
    if family == socket.AF_INET6:
        sockaddr = (address, port, 0, 0)
    else:
        sockaddr = (address, port)
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)


def test_resolution_accepts_only_a_small_set_of_public_stream_endpoints() -> None:
    endpoints = validate_resolved_endpoints(
        "example.com",
        443,
        [_record("93.184.216.34"), _record("2606:2800:220:1:248:1893:25c8:1946")],
    )

    assert [endpoint.address for endpoint in endpoints] == [
        "93.184.216.34",
        "2606:2800:220:1:248:1893:25c8:1946",
    ]
    assert all(endpoint.port == 443 for endpoint in endpoints)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "192.168.50.20",
        "169.254.169.254",
        "100.64.0.1",
        "224.0.0.1",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
    ],
)
def test_resolution_rejects_every_non_global_destination(address: str) -> None:
    with pytest.raises(BrowserEgressError, match="destination is not allowed"):
        validate_resolved_endpoints("example.com", 443, [_record(address)])


def test_resolution_rejects_mixed_public_private_answers_instead_of_selecting_public() -> None:
    with pytest.raises(BrowserEgressError, match="destination is not allowed"):
        validate_resolved_endpoints(
            "example.com",
            443,
            [_record("93.184.216.34"), _record("127.0.0.1")],
        )


def test_resolution_rejects_wrong_ports_wrong_socket_types_and_answer_floods() -> None:
    with pytest.raises(BrowserEgressError):
        validate_resolved_endpoints("example.com", 443, [_record("93.184.216.34", 80)])

    datagram = list(_record("93.184.216.34"))
    datagram[1] = socket.SOCK_DGRAM
    with pytest.raises(BrowserEgressError):
        validate_resolved_endpoints("example.com", 443, [tuple(datagram)])

    with pytest.raises(BrowserEgressError):
        validate_resolved_endpoints(
            "example.com",
            443,
            [_record(f"192.0.2.{index}") for index in range(1, 18)],
        )


def test_connector_re_resolves_each_connection_and_pins_the_numeric_sockaddr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        answers = [
            [_record("93.184.216.34")],
            [_record("93.184.216.35")],
        ]
        resolved: list[tuple[str, int]] = []
        connected: list[tuple[object, ...]] = []

        async def resolver(host: str, port: int, **_kwargs: object) -> list[tuple[object, ...]]:
            resolved.append((host, port))
            return answers.pop(0)

        class FakeSocket:
            def __init__(self, family: int, sock_type: int, protocol: int) -> None:
                self.family = family
                self.type = sock_type
                self.proto = protocol

            def setblocking(self, _blocking: bool) -> None:
                return None

            def close(self) -> None:
                return None

        async def sock_connect(_sock: FakeSocket, sockaddr: tuple[object, ...]) -> None:
            connected.append(sockaddr)

        async def wrap_socket(*, sock: FakeSocket) -> tuple[str, FakeSocket]:
            return "stream", sock

        connector = PinnedEndpointConnector(resolver=resolver, socket_factory=FakeSocket)
        monkeypatch.setattr(connector, "_sock_connect", sock_connect)
        monkeypatch.setattr(connector, "_wrap_socket", wrap_socket)

        first = await connector.connect("example.com", 443)
        second = await connector.connect("example.com", 443)

        assert first[0] == "stream"
        assert second[0] == "stream"
        assert resolved == [("example.com", 443), ("example.com", 443)]
        assert connected == [("93.184.216.34", 443), ("93.184.216.35", 443)]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("host", "port"),
    [
        ("localhost", 443),
        ("homeassistant.local", 443),
        ("supervisor", 443),
        ("example.com", 22),
        ("127.0.0.1", 80),
    ],
)
def test_connector_rejects_local_names_addresses_and_non_web_ports_before_dns(
    host: str, port: int
) -> None:
    calls = 0

    async def resolver(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        nonlocal calls
        calls += 1
        return [_record("93.184.216.34", port)]

    connector = PinnedEndpointConnector(resolver=resolver)

    with pytest.raises(BrowserEgressError):
        asyncio.run(connector.connect(host, port))
    assert calls == 0


def test_policy_proxy_connect_tunnels_only_after_the_pinned_connector() -> None:
    async def scenario() -> None:
        upstream_calls: list[tuple[str, int]] = []

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                assert await reader.readexactly(4) == b"ping"
                writer.write(b"pong")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        upstream_port = server.sockets[0].getsockname()[1]

        class Connector:
            async def connect(self, host: object, port: object) -> tuple[object, object]:
                upstream_calls.append((str(host), int(port)))
                return await asyncio.open_connection("127.0.0.1", upstream_port)

        proxy = BrowserPolicyProxy(connector=Connector())
        await proxy.start()
        try:
            reader, writer = await asyncio.open_connection(*proxy.address)
            writer.write(
                b"CONNECT example.com:443 HTTP/1.1\r\n"
                b"Host: example.com:443\r\n\r\n"
            )
            await writer.drain()
            assert await reader.readuntil(b"\r\n\r\n") == (
                b"HTTP/1.1 200 Connection Established\r\n\r\n"
            )
            writer.write(b"ping")
            await writer.drain()
            assert await reader.readexactly(4) == b"pong"
            writer.close()
            await writer.wait_closed()
            assert upstream_calls == [("example.com", 443)]
        finally:
            await proxy.close()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_policy_proxy_connect_preserves_response_after_client_half_close() -> None:
    async def scenario() -> None:
        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                assert await reader.readexactly(4) == b"ping"
                assert await reader.read() == b""
                writer.write(b"pong-after-eof")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        upstream_port = server.sockets[0].getsockname()[1]

        class Connector:
            async def connect(self, host: object, port: object) -> tuple[object, object]:
                assert (host, port) == ("example.com", 443)
                return await asyncio.open_connection("127.0.0.1", upstream_port)

        proxy = BrowserPolicyProxy(connector=Connector())
        await proxy.start()
        try:
            reader, writer = await asyncio.open_connection(*proxy.address)
            writer.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
            await writer.drain()
            assert await reader.readuntil(b"\r\n\r\n") == (
                b"HTTP/1.1 200 Connection Established\r\n\r\n"
            )
            writer.write(b"ping")
            await writer.drain()
            writer.write_eof()
            assert await reader.readexactly(14) == b"pong-after-eof"
            assert await reader.read() == b""
            writer.close()
            await writer.wait_closed()
        finally:
            await proxy.close()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_policy_proxy_rejects_private_connect_before_resolution() -> None:
    async def scenario() -> None:
        calls = 0

        async def resolver(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
            nonlocal calls
            calls += 1
            return [_record("93.184.216.34")]

        proxy = BrowserPolicyProxy(connector=PinnedEndpointConnector(resolver=resolver))
        await proxy.start()
        try:
            reader, writer = await asyncio.open_connection(*proxy.address)
            writer.write(b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n")
            await writer.drain()
            assert (await reader.readline()).startswith(b"HTTP/1.1 403")
            writer.close()
            await writer.wait_closed()
            assert calls == 0
        finally:
            await proxy.close()

    asyncio.run(scenario())


def test_policy_proxy_forwards_plain_http_in_origin_form_and_strips_proxy_headers() -> None:
    async def scenario() -> None:
        received: list[bytes] = []

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                received.append(await reader.readuntil(b"\r\n\r\n"))
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        upstream_port = server.sockets[0].getsockname()[1]

        class Connector:
            async def connect(self, host: object, port: object) -> tuple[object, object]:
                assert (host, port) == ("example.com", 80)
                return await asyncio.open_connection("127.0.0.1", upstream_port)

        proxy = BrowserPolicyProxy(connector=Connector())
        await proxy.start()
        try:
            reader, writer = await asyncio.open_connection(*proxy.address)
            writer.write(
                b"GET http://example.com/docs?q=1 HTTP/1.1\r\n"
                b"Host: attacker.invalid\r\n"
                b"Proxy-Authorization: Basic secret\r\n"
                b"Proxy-Connection: keep-alive\r\n\r\n"
            )
            await writer.drain()
            response = await reader.read()
            assert response.endswith(b"\r\n\r\nok")
            writer.close()
            await writer.wait_closed()
            assert received == [
                b"GET /docs?q=1 HTTP/1.1\r\n"
                b"Host: example.com\r\n"
                b"Connection: close\r\n\r\n"
            ]
        finally:
            await proxy.close()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
