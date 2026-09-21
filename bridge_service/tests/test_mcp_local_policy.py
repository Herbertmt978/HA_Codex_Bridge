from __future__ import annotations

import pytest

from codex_bridge_service.mcp_local_policy import (
    LocalMcpError,
    canonical_local_url,
    checked_addresses,
    pinned_address,
    resolve_private,
)


@pytest.mark.parametrize("url,expected", [
    ("http://192.168.1.2:8123/mcp", "http://192.168.1.2:8123/mcp"),
    ("https://HA-MCP.LOCAL./secret-path", "https://ha-mcp.local/secret-path"),
    ("http://abc123-ha-mcp:8099", "http://abc123-ha-mcp:8099/"),
    ("https://[fd12::7]/mcp", "https://[fd12::7]/mcp"),
])
def test_local_urls_are_canonical(url, expected):
    assert canonical_local_url(url) == expected


@pytest.mark.parametrize("url", [
    None, {}, "", " http://ha.local/mcp", "http://ha.local/mcp\n",
    "http://user:secret@ha.local/mcp", "http://ha.local/mcp?token=secret",
    "http://ha.local/mcp#fragment", "file:///mcp", "http://ha.local:0/mcp",
    "http://ha.local:99999/mcp", "http://ha.local\\@192.168.1.2/mcp",
    "http://127.0.0.1/mcp", "http://[::1]/mcp", "http://localhost/mcp",
    "http://other.localhost/mcp", "http://supervisor/mcp", "http://hassio/mcp",
    "http://172.30.32.2/mcp", "http://169.254.169.254/mcp",
    "http://metadata.google.internal/mcp", "http://8.8.8.8/mcp",
    "http://[::ffff:192.168.1.2]/mcp", "http://[fd12::1%25eth0]/mcp",
    "http://h\ud800.local/mcp", "http://ha.local/" + "x" * 2048,
])
def test_unsafe_urls_have_only_fixed_errors(url):
    with pytest.raises(LocalMcpError) as error:
        canonical_local_url(url)
    assert str(error.value) == "Local MCP connection is unavailable or invalid"


@pytest.mark.parametrize("addresses", [
    (), ("192.168.1.2", "8.8.8.8"), ("192.168.1.2", "127.0.0.1"),
    ("172.30.32.2",), ("169.254.169.254",), ("fe80::1",), ("fd12::1%eth0",),
    ("::ffff:192.168.1.2",), ("100.64.0.1",), ("0.0.0.0",), (None,),
    ("not-an-ip",), ("192.168.1.2",) * 17,
])
def test_all_dns_answers_must_be_private_and_bounded(addresses):
    with pytest.raises(LocalMcpError):
        checked_addresses(addresses)


def test_rebinding_requires_fresh_approval():
    approved = ("192.168.1.2", "fd12::2")
    assert pinned_address(("192.168.1.2",), approved) == "192.168.1.2"
    with pytest.raises(LocalMcpError):
        pinned_address(("192.168.1.3",), approved)


def test_resolution_uses_every_answer_and_fails_closed(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (None, None, None, None, ("192.168.1.2", 0)),
        (None, None, None, None, ("8.8.8.8", 0)),
    ])
    with pytest.raises(LocalMcpError):
        resolve_private("ha.local")
    def fail(*args, **kwargs):
        raise OSError("private resolver details")
    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(LocalMcpError):
        resolve_private("ha.local")
    assert resolve_private("172.30.33.5") == ("172.30.33.5",)
