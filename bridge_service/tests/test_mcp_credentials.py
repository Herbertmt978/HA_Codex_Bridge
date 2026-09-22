import json

import pytest

from codex_bridge_service.mcp_credentials import CredentialRedactor, parse_credential
from codex_bridge_service.mcp_local_policy import LocalMcpError, checked_addresses


@pytest.mark.parametrize("payload", [
    {"mode": []}, {"mode": "bearer", "token": "sensitive\r\nHost: other"},
    {"mode": "bearer", "token": " private "}, {"mode": "bearer", "token": "Bearer sensitive"},
    {"mode": "bearer", "token": {"password": "sensitive"}},
    {"mode": "none", "token": "sensitive"},
    {"mode": "headers", "headers": [{"name": "X-Key", "value": "sensitive"}, {"name": "x-key", "value": "other"}]},
    {"mode": "headers", "headers": []},
    {"mode": "headers", "headers": [{"name": "X-Key", "value": "sensitive\x00"}]},
    {"mode": "headers", "headers": [{"name": "X-Key", "value": "s" * 4097}]},
])
def test_malformed_authentication_has_fixed_error(payload):
    with pytest.raises(LocalMcpError) as caught:
        parse_credential(payload)
    assert str(caught.value) == "Local MCP connection is unavailable or invalid"
    assert "sensitive" not in repr(caught.value)


@pytest.mark.parametrize("name", ["Authorization", "HOST", "Cookie", "Connection", "Keep-Alive", "Transfer-Encoding", "Content-Length", "X-Forwarded-For", "Proxy-Authorization", "Mcp-Session-Id", "X-Codex-Local-Mcp", "Sec-Fetch-Site", "Forwarded", "Accept", "X-Key\r\nHost"])
def test_routing_and_reserved_headers_cannot_be_credentials(name):
    with pytest.raises(LocalMcpError):
        parse_credential({"mode": "headers", "headers": [{"name": name, "value": "sensitive"}]})


@pytest.mark.parametrize("mode", ["bearer", "headers"])
def test_storage_round_trip_is_write_only_in_representations(mode):
    payload = {"mode": mode, "token": "synthetic-secret"} if mode == "bearer" else {"mode": mode, "headers": [{"name": "X-Api-Key", "value": "synthetic-secret"}]}
    credential = parse_credential(payload)
    assert "synthetic-secret" not in repr(credential)
    assert parse_credential(json.loads(json.dumps(credential.stored())), stored=True) == credential


def test_streaming_redaction_handles_every_split_and_json_escaping():
    credential = parse_credential({"mode": "headers", "headers": [{"name": "X-Api-Key", "value": 'synthetic-"secret\\value'}]})
    value = credential.headers[0][1]
    for body in [value.encode(), json.dumps({"echo": value}).encode(), b'event: message\ndata: ' + value.encode() + b'\n\n']:
        for index in range(len(body) + 1):
            redactor = CredentialRedactor(credential)
            output = redactor.feed(body[:index]) + redactor.feed(body[index:]) + redactor.feed(b"", final=True)
            assert value.encode() not in output
            assert json.dumps(value)[1:-1].encode() not in output
            assert b"[redacted]" in output


def test_bearer_raw_token_and_full_header_are_redacted_without_delaying_unrelated_sse():
    credential = parse_credential({"mode": "bearer", "token": "synthetic-secret"})
    redactor = CredentialRedactor(credential)
    assert redactor.feed(b'event: message\ndata: {}\n\n') == b'event: message\ndata: {}\n\n'
    assert redactor.feed(b'Bearer synthetic-secret synthetic-secret', final=True) == b'[redacted] [redacted]'


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["169.254.169.254"], ["8.8.8.8", "192.168.1.2"], ["224.0.0.1"], ["::ffff:8.8.8.8"], []])
def test_public_credential_addresses_fail_closed(addresses):
    with pytest.raises(LocalMcpError):
        checked_addresses(addresses, local=False)


def test_public_credential_addresses_allow_public_v4_v6():
    assert checked_addresses(["8.8.8.8", "2606:4700:4700::1111"], local=False) == ("2606:4700:4700::1111", "8.8.8.8")
