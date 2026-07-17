"""Connection-time public-network policy for the App-owned browser worker.

URL validation is only a preflight.  This module validates every DNS answer
and connects the browser proxy to the exact numeric socket address that was
checked, so a later DNS rebind cannot redirect that connection into Home
Assistant, Supervisor, link-local metadata, or another private service.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
import ipaddress
import re
import socket
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from .browser_contract import BrowserContractError, normalize_public_url


MAX_DNS_ANSWERS = 16
_WEB_PORTS = frozenset({80, 443})
_HTTP_METHOD = re.compile(rb"[A-Z]{1,16}\Z")
_HEADER_NAME = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}\Z")
_HOP_BY_HOP_HEADERS = frozenset(
    {
        b"connection",
        b"keep-alive",
        b"proxy-authenticate",
        b"proxy-authorization",
        b"proxy-connection",
        b"te",
        b"trailer",
        b"transfer-encoding",
        b"upgrade",
    }
)


class BrowserEgressError(ConnectionError):
    """A browser connection was denied or could not be opened safely."""


@dataclass(frozen=True, slots=True)
class ResolvedEndpoint:
    family: int
    sock_type: int
    protocol: int
    address: str
    port: int
    sockaddr: tuple[Any, ...]


def _canonical_public_host(host: object, port: object) -> tuple[str, int]:
    if not isinstance(host, str) or not host or type(port) is not int or port not in _WEB_PORTS:
        raise BrowserEgressError("browser destination is not allowed")
    display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    scheme = "https" if port == 443 else "http"
    try:
        normalized = normalize_public_url(f"{scheme}://{display_host}:{port}/")
    except BrowserContractError:
        raise BrowserEgressError("browser destination is not allowed") from None
    canonical = urlsplit(normalized).hostname
    if not canonical:
        raise BrowserEgressError("browser destination is not allowed")
    return canonical, port


def validate_resolved_endpoints(
    host: object,
    port: object,
    records: Sequence[tuple[object, ...]],
) -> tuple[ResolvedEndpoint, ...]:
    """Validate a bounded, single-resolution result without selecting around risk."""

    canonical_host, canonical_port = _canonical_public_host(host, port)
    del canonical_host  # The caller retains the name for logging-free TLS use.
    if not isinstance(records, Sequence) or not 1 <= len(records) <= MAX_DNS_ANSWERS:
        raise BrowserEgressError("browser destination is not allowed")

    endpoints: list[ResolvedEndpoint] = []
    seen: set[tuple[int, tuple[Any, ...]]] = set()
    for record in records:
        if not isinstance(record, tuple) or len(record) != 5:
            raise BrowserEgressError("browser destination is not allowed")
        family, sock_type, protocol, canonical_name, sockaddr = record
        if (
            family not in {socket.AF_INET, socket.AF_INET6}
            or sock_type != socket.SOCK_STREAM
            or protocol not in {0, socket.IPPROTO_TCP}
            or not isinstance(canonical_name, str)
            or not isinstance(sockaddr, tuple)
        ):
            raise BrowserEgressError("browser destination is not allowed")
        expected_length = 2 if family == socket.AF_INET else 4
        if len(sockaddr) != expected_length:
            raise BrowserEgressError("browser destination is not allowed")
        address_value, resolved_port = sockaddr[:2]
        if not isinstance(address_value, str) or resolved_port != canonical_port:
            raise BrowserEgressError("browser destination is not allowed")
        if family == socket.AF_INET6 and (sockaddr[2] != 0 or sockaddr[3] != 0):
            raise BrowserEgressError("browser destination is not allowed")
        try:
            address = ipaddress.ip_address(address_value)
        except ValueError:
            raise BrowserEgressError("browser destination is not allowed") from None
        if (
            (family == socket.AF_INET and address.version != 4)
            or (family == socket.AF_INET6 and address.version != 6)
            or not address.is_global
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_private
            or address.is_reserved
            or address.is_unspecified
        ):
            # Reject the whole answer set. Selecting only a public peer from a
            # mixed public/private response would make DNS rebinding possible.
            raise BrowserEgressError("browser destination is not allowed")
        numeric_sockaddr = (
            (address.compressed, canonical_port)
            if address.version == 4
            else (address.compressed, canonical_port, 0, 0)
        )
        identity = (int(family), numeric_sockaddr)
        if identity in seen:
            continue
        seen.add(identity)
        endpoints.append(
            ResolvedEndpoint(
                family=int(family),
                sock_type=int(sock_type),
                protocol=socket.IPPROTO_TCP,
                address=address.compressed,
                port=canonical_port,
                sockaddr=numeric_sockaddr,
            )
        )
    if not endpoints:
        raise BrowserEgressError("browser destination is not allowed")
    return tuple(endpoints)


Resolver = Callable[..., Awaitable[Sequence[tuple[object, ...]]]]
SocketFactory = Callable[[int, int, int], socket.socket]


class PinnedEndpointConnector:
    """Open a raw TCP tunnel to only the checked numeric endpoint.

    HTTPS is deliberately *not* terminated here. Chromium sends CONNECT to the
    policy proxy and performs TLS, SNI, and certificate verification itself
    inside this DNS-pinned tunnel.
    """

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        socket_factory: SocketFactory = socket.socket,
        connect_timeout_seconds: float = 10.0,
    ) -> None:
        if not 0 < connect_timeout_seconds <= 30:
            raise ValueError("browser connection timeout is invalid")
        self._resolver = resolver
        self._socket_factory = socket_factory
        self._connect_timeout_seconds = float(connect_timeout_seconds)

    async def connect(self, host: object, port: object) -> tuple[Any, Any]:
        canonical_host, canonical_port = _canonical_public_host(host, port)
        resolver = self._resolver or self._default_resolver
        try:
            records = await resolver(
                canonical_host,
                canonical_port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
                flags=0,
            )
        except (OSError, UnicodeError):
            raise BrowserEgressError("browser destination could not be resolved safely") from None
        endpoints = validate_resolved_endpoints(canonical_host, canonical_port, records)

        for endpoint in endpoints:
            stream_socket = self._socket_factory(
                endpoint.family,
                endpoint.sock_type,
                endpoint.protocol,
            )
            stream_socket.setblocking(False)
            try:
                await asyncio.wait_for(
                    self._sock_connect(stream_socket, endpoint.sockaddr),
                    timeout=self._connect_timeout_seconds,
                )
                return await self._wrap_socket(sock=stream_socket)
            except (OSError, TimeoutError):
                stream_socket.close()
                continue
            except BaseException:
                stream_socket.close()
                raise
        raise BrowserEgressError("browser destination could not be reached safely")

    async def _default_resolver(
        self, host: str, port: int, **kwargs: object
    ) -> Sequence[tuple[object, ...]]:
        return await asyncio.get_running_loop().getaddrinfo(host, port, **kwargs)

    async def _sock_connect(
        self, stream_socket: socket.socket, sockaddr: tuple[object, ...]
    ) -> None:
        await asyncio.get_running_loop().sock_connect(stream_socket, sockaddr)

    async def _wrap_socket(self, *, sock: socket.socket) -> tuple[Any, Any]:
        return await asyncio.open_connection(sock=sock)


class _EndpointConnector(Protocol):
    async def connect(self, host: object, port: object) -> tuple[Any, Any]: ...


class BrowserPolicyProxy:
    """Loopback-only HTTP/CONNECT proxy with per-connection destination checks."""

    def __init__(
        self,
        *,
        connector: _EndpointConnector | None = None,
        max_header_bytes: int = 32 * 1024,
        max_request_body_bytes: int = 8 * 1024 * 1024,
        max_tunnel_bytes: int = 32 * 1024 * 1024,
        idle_timeout_seconds: float = 30.0,
    ) -> None:
        if (
            type(max_header_bytes) is not int
            or not 1024 <= max_header_bytes <= 128 * 1024
            or type(max_request_body_bytes) is not int
            or not 0 <= max_request_body_bytes <= 16 * 1024 * 1024
            or type(max_tunnel_bytes) is not int
            or not 1024 <= max_tunnel_bytes <= 128 * 1024 * 1024
            or not 1 <= idle_timeout_seconds <= 120
        ):
            raise ValueError("browser proxy limits are invalid")
        self._connector = connector or PinnedEndpointConnector()
        self._max_header_bytes = max_header_bytes
        self._max_request_body_bytes = max_request_body_bytes
        self._max_tunnel_bytes = max_tunnel_bytes
        self._idle_timeout_seconds = float(idle_timeout_seconds)
        self._server: asyncio.AbstractServer | None = None

    @property
    def address(self) -> tuple[str, int]:
        server = self._server
        if server is None or not server.sockets:
            raise RuntimeError("browser policy proxy is not running")
        host, port = server.sockets[0].getsockname()[:2]
        if host != "127.0.0.1" or type(port) is not int:
            raise RuntimeError("browser policy proxy address is invalid")
        return host, port

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_client,
            host="127.0.0.1",
            port=0,
            limit=self._max_header_bytes + 1,
            start_serving=True,
        )

    async def close(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.close()
        await server.wait_closed()

    async def _handle_client(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        response_started = False
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            raw_header = await asyncio.wait_for(
                client_reader.readuntil(b"\r\n\r\n"),
                timeout=self._idle_timeout_seconds,
            )
            if len(raw_header) > self._max_header_bytes:
                raise BrowserEgressError("browser proxy request is invalid")
            method, target, version, headers = _parse_proxy_header(raw_header)
            if method == b"CONNECT":
                host, port = _connect_authority(target)
                if _content_length(headers) != 0:
                    raise BrowserEgressError("browser proxy request is invalid")
                upstream_reader, upstream_writer = await self._connector.connect(
                    host, port
                )
                client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await client_writer.drain()
                response_started = True
                await self._relay_bidirectional(
                    client_reader,
                    client_writer,
                    upstream_reader,
                    upstream_writer,
                )
                return
            await self._forward_http(
                method,
                target,
                version,
                headers,
                client_reader,
                client_writer,
            )
            response_started = True
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            BrowserContractError,
            BrowserEgressError,
            OSError,
            TimeoutError,
            UnicodeError,
            ValueError,
        ):
            if not response_started:
                await _write_proxy_error(client_writer, 403, "Forbidden")
        finally:
            if upstream_writer is not None:
                upstream_writer.close()
                try:
                    await upstream_writer.wait_closed()
                except OSError:
                    pass
            client_writer.close()
            try:
                await client_writer.wait_closed()
            except OSError:
                pass

    async def _forward_http(
        self,
        method: bytes,
        target: bytes,
        version: bytes,
        headers: tuple[tuple[bytes, bytes], ...],
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        if method == b"CONNECT" or _HTTP_METHOD.fullmatch(method) is None:
            raise BrowserEgressError("browser proxy request is invalid")
        try:
            target_text = target.decode("ascii")
        except UnicodeDecodeError:
            raise BrowserEgressError("browser proxy request is invalid") from None
        parsed_target = urlsplit(target_text)
        if parsed_target.scheme.lower() != "http":
            # HTTPS must use CONNECT so Chromium retains TLS/SNI/certificate
            # ownership instead of trusting the proxy with plaintext.
            raise BrowserEgressError("browser proxy request is invalid")
        normalized = normalize_public_url(target_text)
        parsed = urlsplit(normalized)
        if parsed.scheme != "http" or parsed.hostname is None:
            raise BrowserEgressError("browser proxy request is invalid")
        port = parsed.port or 80
        content_length = _content_length(headers)
        if content_length > self._max_request_body_bytes:
            raise BrowserEgressError("browser proxy request is invalid")
        upstream_reader, upstream_writer = await self._connector.connect(
            parsed.hostname,
            port,
        )
        try:
            path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            authority = parsed.hostname
            if ":" in authority:
                authority = f"[{authority}]"
            if port != 80:
                authority = f"{authority}:{port}"
            forwarded = [method + b" " + path.encode("ascii") + b" " + version]
            for name, value in headers:
                lowered = name.lower()
                if lowered == b"host" or lowered in _HOP_BY_HOP_HEADERS:
                    continue
                forwarded.append(name + b": " + value)
            forwarded.extend(
                (
                    b"Host: " + authority.encode("ascii"),
                    b"Connection: close",
                    b"",
                    b"",
                )
            )
            upstream_writer.write(b"\r\n".join(forwarded))
            if content_length:
                body = await asyncio.wait_for(
                    client_reader.readexactly(content_length),
                    timeout=self._idle_timeout_seconds,
                )
                upstream_writer.write(body)
            await upstream_writer.drain()
            await self._relay_one_way(upstream_reader, client_writer)
        finally:
            upstream_writer.close()
            try:
                await upstream_writer.wait_closed()
            except OSError:
                pass

    async def _relay_bidirectional(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
    ) -> None:
        tasks = (
            asyncio.create_task(self._relay_one_way(client_reader, upstream_writer)),
            asyncio.create_task(self._relay_one_way(upstream_reader, client_writer)),
        )
        # A clean EOF in one direction is a TCP half-close, not permission to
        # truncate the other direction.  Wait for both sides unless one relay
        # actually fails; each read retains the bounded idle timeout.
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()

    async def _relay_one_way(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        total = 0
        while True:
            data = await asyncio.wait_for(
                reader.read(64 * 1024),
                timeout=self._idle_timeout_seconds,
            )
            if not data:
                try:
                    if writer.can_write_eof():
                        writer.write_eof()
                        await writer.drain()
                except (AttributeError, NotImplementedError, OSError):
                    pass
                return
            total += len(data)
            if total > self._max_tunnel_bytes:
                raise BrowserEgressError("browser proxy transfer limit exceeded")
            writer.write(data)
            await writer.drain()


def _parse_proxy_header(
    raw: bytes,
) -> tuple[bytes, bytes, bytes, tuple[tuple[bytes, bytes], ...]]:
    if not raw.endswith(b"\r\n\r\n") or b"\x00" in raw:
        raise BrowserEgressError("browser proxy request is invalid")
    lines = raw[:-4].split(b"\r\n")
    if not lines or len(lines) > 128:
        raise BrowserEgressError("browser proxy request is invalid")
    parts = lines[0].split(b" ")
    if len(parts) != 3 or parts[2] not in {b"HTTP/1.0", b"HTTP/1.1"}:
        raise BrowserEgressError("browser proxy request is invalid")
    method, target, version = parts
    if _HTTP_METHOD.fullmatch(method) is None or not 1 <= len(target) <= 4096:
        raise BrowserEgressError("browser proxy request is invalid")
    headers: list[tuple[bytes, bytes]] = []
    for line in lines[1:]:
        name, separator, value = line.partition(b":")
        value = value.strip(b" \t")
        if (
            separator != b":"
            or _HEADER_NAME.fullmatch(name) is None
            or any(byte < 32 and byte != 9 for byte in value)
            or 127 in value
        ):
            raise BrowserEgressError("browser proxy request is invalid")
        headers.append((name, value))
    return method, target, version, tuple(headers)


def _content_length(headers: tuple[tuple[bytes, bytes], ...]) -> int:
    values = [value for name, value in headers if name.lower() == b"content-length"]
    if len(values) > 1:
        raise BrowserEgressError("browser proxy request is invalid")
    if any(name.lower() == b"transfer-encoding" for name, _value in headers):
        raise BrowserEgressError("browser proxy request is invalid")
    if not values:
        return 0
    try:
        text = values[0].decode("ascii")
        value = int(text, 10)
    except (UnicodeDecodeError, ValueError):
        raise BrowserEgressError("browser proxy request is invalid") from None
    if value < 0 or str(value) != text:
        raise BrowserEgressError("browser proxy request is invalid")
    return value


def _connect_authority(target: bytes) -> tuple[str, int]:
    try:
        value = target.decode("ascii")
        parsed = urlsplit(f"//{value}")
        port = parsed.port
    except (UnicodeDecodeError, ValueError):
        raise BrowserEgressError("browser proxy request is invalid") from None
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or port not in _WEB_PORTS
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise BrowserEgressError("browser proxy request is invalid")
    return _canonical_public_host(parsed.hostname, port)


async def _write_proxy_error(
    writer: asyncio.StreamWriter, status: int, reason: str
) -> None:
    try:
        payload = (
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Length: 0\r\nConnection: close\r\n\r\n"
        ).encode("ascii")
        writer.write(payload)
        await writer.drain()
    except OSError:
        return
