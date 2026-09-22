"""Destination policy for explicitly approved LAN/App MCP connections."""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit, urlunsplit

_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
))
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z", re.ASCII)
_RESERVED = {"localhost", "localhost.localdomain", "supervisor", "hassio",
             "supervisor.local.hass.io", "metadata.google.internal"}
_SUPERVISOR_IP = ipaddress.ip_address("172.30.32.2")


class LocalMcpError(ValueError):
    """Never retain a caller's URL, token, DNS response or provider error."""

    def __init__(self) -> None:
        super().__init__("Local MCP connection is unavailable or invalid")


def private_address(value: str) -> str:
    if not isinstance(value, str) or "%" in value:
        raise LocalMcpError()
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise LocalMcpError() from None
    if address == _SUPERVISOR_IP or getattr(address, "ipv4_mapped", None):
        raise LocalMcpError()
    if not any(address.version == network.version and address in network for network in _NETWORKS):
        raise LocalMcpError()
    return str(address)


def canonical_local_url(value: object, *, local: bool = True) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > 2048 or "\\" in value
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
        raise LocalMcpError()
    try:
        if len(value.encode("utf-8")) > 2048:
            raise LocalMcpError()
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        host.encode("ascii")
        port = parsed.port
        if (parsed.scheme not in {"http", "https"} or not host
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or "%" in parsed.netloc
                or host in _RESERVED or host.endswith(".localhost")
                or port == 0):
            raise LocalMcpError()
        if not local and (parsed.scheme != "https" or "." not in host
                          or host.endswith((".local", ".internal", ".home.arpa", ".lan", ".test", ".invalid"))):
            raise LocalMcpError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if len(host) > 253 or not all(_LABEL.fullmatch(label) for label in host.split(".")):
                raise LocalMcpError() from None
            authority = host
        else:
            if not local:
                raise LocalMcpError()
            address = private_address(host)
            authority = f"[{address}]" if ":" in address else address
        if port is not None:
            authority += f":{port}"
        return urlunsplit((parsed.scheme, authority, parsed.path or "/", "", ""))
    except (ValueError, UnicodeError):
        raise LocalMcpError() from None


def resolve_private(host: str) -> tuple[str, ...]:
    return checked_addresses(resolve_addresses(host))


def resolve_addresses(host: str) -> tuple[str, ...]:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        try:
            records = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError:
            raise LocalMcpError() from None
        answers = tuple(dict.fromkeys(record[4][0] for record in records))
    else:
        answers = (host,)
    return answers


def checked_addresses(answers: object, *, local: bool = True) -> tuple[str, ...]:
    if not isinstance(answers, (tuple, list)) or not 1 <= len(answers) <= 16:
        raise LocalMcpError()
    if not all(isinstance(value, str) for value in answers):
        raise LocalMcpError()
    if local:
        return tuple(sorted(set(private_address(value) for value in answers)))
    try:
        addresses = [ipaddress.ip_address(value) for value in answers]
        if any("%" in value for value in answers) or any(not address.is_global or address.is_multicast or getattr(address, "ipv4_mapped", None) for address in addresses):
            raise LocalMcpError()
        return tuple(sorted(set(str(address) for address in addresses)))
    except ValueError:
        raise LocalMcpError() from None


def pinned_address(answers: object, approved: tuple[str, ...], *, local: bool = True) -> str:
    current = checked_addresses(answers, local=local)
    if not set(current).issubset(approved):
        raise LocalMcpError()
    return current[0]
