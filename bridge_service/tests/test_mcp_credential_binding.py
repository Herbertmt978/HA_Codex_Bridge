"""Managed credential ownership must survive manual MCP edits without clobbering them."""

from concurrent.futures import Future
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from codex_bridge_service.mcp_credentials import parse_credential
from codex_bridge_service.mcp_manager import (
    McpCredentialBindingConflictError,
    McpManager,
    McpValidationError,
)
from codex_bridge_service.mcp_relay import McpRecord, McpRelay

from test_mcp_manager import AppServerDouble, GateDouble, _config, _route_app


_URL = "http://ha.local/mcp"
_OLD_TOKEN = "synthetic-original-bearer"
_NEW_TOKEN = "synthetic-managed-replacement"
_MANUAL_TOKEN = "synthetic-manual-replacement"


def _fingerprint(token: str) -> str:
    return sha256(token.encode("ascii")).hexdigest()


@pytest.fixture
def binding_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Use the real relay updates/hash check, with only persistence and DNS in memory."""

    relay = McpRelay(tmp_path / "private")
    relay.port = 12345  # Configuration fixture only: no listener is started.
    relay._records["home"] = McpRecord(
        _URL, ("192.168.1.2",), "synthetic-private-relay-capability",
        credential=parse_credential({"mode": "bearer", "token": _OLD_TOKEN}),
    )
    writes = []
    monkeypatch.setattr(
        relay, "_save", lambda records, **kwargs: writes.append((dict(records), kwargs)),
    )
    relay._boundary = SimpleNamespace(atomic_write_bytes=lambda *_: None)
    resolved = Future()
    resolved.set_result(("192.168.1.2",))
    monkeypatch.setattr(relay, "_resolve", lambda _: resolved)
    native = {**relay.native_config("home", active=False), "enabled": False}
    peer, gate = AppServerDouble(), GateDouble()
    manager = McpManager(peer, gate, enabled=True, relay=relay)
    return manager, peer, gate, relay, native, writes


def _guard() -> dict[str, str]:
    return {"expected_url": _URL, "expected_token_sha256": _fingerprint(_OLD_TOKEN)}


def _assert_no_secrets(value: object) -> None:
    assert all(token not in str(value) for token in (_OLD_TOKEN, _NEW_TOKEN, _MANUAL_TOKEN))


def test_exact_binding_rotation_retires_old_removal_authority(binding_owner) -> None:
    manager, peer, gate, relay, native, writes = binding_owner
    peer.responses.extend([_config({"home": native}), {}])
    result = manager.replace_credential(
        "home", {"mode": "bearer", "token": _NEW_TOKEN}, acknowledged=True,
        **_guard(),
    )
    assert result == {"name": "home", "auth": "bearer", "credential_configured": True}
    assert relay.credential_binding_matches("home", _URL, _fingerprint(_NEW_TOKEN))
    assert not relay.credential_binding_matches("home", _URL, _fingerprint(_OLD_TOKEN))
    assert "home" not in relay._active
    peer.calls.clear()
    peer.responses.append(_config({"home": native}))
    before = len(writes)
    with pytest.raises(McpCredentialBindingConflictError):
        manager.remove_server("home", **_guard())
    assert [call.method for call in peer.calls] == ["config/read"]
    assert len(writes) == before
    assert "home" in relay._records
    peer.responses.extend([
        _config({"home": native}), {"status": "ok", "version": "user-v2"}, {},
    ])
    manager.remove_server(
        "home", expected_url=_URL, expected_token_sha256=_fingerprint(_NEW_TOKEN),
    )
    assert "home" not in relay._records
    assert peer.calls[-2].params["edits"][0]["value"] is None
    assert peer.calls[-1].method == "config/mcpServer/reload"
    assert all(lease.released for lease in gate.leases)
    _assert_no_secrets((result, peer.calls))


@pytest.mark.parametrize("operation", ["rotate", "remove"])
def test_manual_destination_change_revokes_managed_authority(binding_owner, operation) -> None:
    manager, peer, gate, relay, native, writes = binding_owner
    peer.responses.append(_config({"home": native}))
    manager.edit_server(
        "home", url="http://other.local/changed-mcp",
        revision=manager._revision("home", "user-v1"),
        endpoint_acknowledged=True, credential_action="keep",
    )
    assert relay.credential_binding_matches(
        "home", "http://other.local/changed-mcp", _fingerprint(_OLD_TOKEN),
    )
    peer.calls.clear()
    peer.responses.append(_config({"home": native}))
    record, before = relay._records["home"], len(writes)
    with pytest.raises(McpCredentialBindingConflictError):
        if operation == "rotate":
            manager.replace_credential(
                "home", {"mode": "bearer", "token": _NEW_TOKEN}, acknowledged=True,
                **_guard(),
            )
        else:
            manager.remove_server("home", **_guard())
    assert relay._records["home"] is record
    assert len(writes) == before
    assert [call.method for call in peer.calls] == ["config/read"]
    assert all(lease.released for lease in gate.leases)
    _assert_no_secrets(peer.calls)


@pytest.mark.parametrize("manual_action", ["bearer", "headers", "remove"])
@pytest.mark.parametrize("operation", ["rotate", "remove"])
def test_manual_credential_change_revokes_managed_authority(
    binding_owner, manual_action, operation,
) -> None:
    manager, peer, gate, relay, native, writes = binding_owner
    peer.responses.extend([_config({"home": native}), {}])
    if manual_action == "remove":
        manager.replace_credential("home", remove=True)
    else:
        authentication = (
            {"mode": "bearer", "token": _MANUAL_TOKEN}
            if manual_action == "bearer" else
            {"mode": "headers", "headers": [{"name": "X-Api-Key", "value": _MANUAL_TOKEN}]}
        )
        manager.replace_credential("home", authentication, acknowledged=True)
    peer.calls.clear()
    peer.responses.append(_config({"home": native}))
    record, before = relay._records["home"], len(writes)
    with pytest.raises(McpCredentialBindingConflictError):
        if operation == "rotate":
            manager.replace_credential(
                "home", {"mode": "bearer", "token": _NEW_TOKEN}, acknowledged=True,
                **_guard(),
            )
        else:
            manager.remove_server("home", **_guard())
    assert relay._records["home"] is record
    assert len(writes) == before
    assert [call.method for call in peer.calls] == ["config/read"]
    assert all(lease.released for lease in gate.leases)
    _assert_no_secrets(peer.calls)


def test_reused_name_for_public_server_is_not_removed(binding_owner) -> None:
    manager, peer, gate, relay, _, writes = binding_owner
    peer.responses.append(_config({"home": {"url": "https://mcp.vendor.example/stream"}}))
    with pytest.raises(McpCredentialBindingConflictError):
        manager.remove_server("home", **_guard())
    assert [call.method for call in peer.calls] == ["config/read"]
    assert not writes
    assert "home" in relay._records
    assert gate.leases[0].released


@pytest.mark.parametrize("guard", [
    {"expected_url": _URL},
    {"expected_token_sha256": _fingerprint(_OLD_TOKEN)},
    {"expected_url": _URL, "expected_token_sha256": "invalid"},
])
def test_partial_or_malformed_binding_cannot_remove_server(binding_owner, guard) -> None:
    manager, peer, gate, relay, native, writes = binding_owner
    peer.responses.append(_config({"home": native}))
    with pytest.raises(McpValidationError):
        manager.remove_server("home", **guard)
    assert [call.method for call in peer.calls] == ["config/read"]
    assert not writes
    assert "home" in relay._records
    assert gate.leases[0].released


@pytest.mark.parametrize("operation", ["rotate", "remove"])
def test_guarded_http_conflict_is_definitive_and_private(binding_owner, operation) -> None:
    manager, peer, _, relay, native, writes = binding_owner
    relay.replace_credential(
        "home", parse_credential({"mode": "bearer", "token": _MANUAL_TOKEN}),
    )
    peer.responses.append(_config({"home": native}))
    client = TestClient(_route_app(manager))
    if operation == "rotate":
        response = client.put(
            "/mcp/servers/home/credential",
            headers={"Authorization": "Bearer bridge-token"},
            json={
                "authentication": {"mode": "bearer", "token": _NEW_TOKEN},
                "auth_acknowledged": True, **_guard(),
            },
        )
    else:
        response = client.post(
            "/mcp/servers/home/managed/remove",
            headers={"Authorization": "Bearer bridge-token"}, json=_guard(),
        )
    assert response.status_code == 409
    assert response.json() == {
        "detail": {"code": "mcp_credential_binding_conflict", "retryable": False},
    }
    assert _URL not in response.text
    assert _fingerprint(_OLD_TOKEN) not in response.text
    _assert_no_secrets((response.text, peer.calls))
    assert len(writes) == 1
    assert relay.credential_binding_matches("home", _URL, _fingerprint(_MANUAL_TOKEN))
    assert [call.method for call in peer.calls] == ["config/read"]
