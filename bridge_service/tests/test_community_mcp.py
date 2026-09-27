"""Community quick connect reuses approved bindings without expanding authority."""

from dataclasses import replace
import os
from unittest.mock import Mock

import pytest

from codex_bridge_service.mcp_manager import McpManager, CommunityMcpBindingConflictError, McpServerDefinition, McpValidationError
from codex_bridge_service.mcp_relay import McpRelay
from test_mcp_manager import _manager, _config

NAME = "ha-community-123456789abc"
URL = "http://ha.local:9583/private_synthetic-fixture"


def definition(name="manual-home", **kwargs):
    return McpServerDefinition(name, URL, local=True, relayed=True, **kwargs)


def test_status_reuses_exact_endpoint_and_preserves_paused_policy():
    manager, client, _ = _manager(_config())
    manager._relay = Mock(local_enabled=True)
    original = definition(enabled=False, enabled_tools=("clock",))
    manager._read_definitions = Mock(return_value=({original.name: original}, "version"))
    assert manager.community_connection(name=NAME, url=URL) == {
        "state": "paused", "server_name": "manual-home", "reused": True}
    assert client.calls == []
    assert original.enabled_tools == ("clock",)


def test_connect_reuses_existing_without_writes_and_preserves_all_policy():
    manager, client, gate = _manager()
    manager._relay = Mock(local_enabled=True)
    original = definition(enabled=False)
    manager._read_definitions = Mock(return_value=({original.name: original}, "version"))
    assert manager.community_connection(name=NAME, url=URL, connect=True, acknowledged=True) == {
        "state": "paused", "server_name": "manual-home", "reused": True}
    assert not client.calls and not manager._relay.add.called
    assert all(lease.released for lease in gate.leases)


@pytest.mark.parametrize("definitions", [
    {NAME: definition(NAME, enabled=False)},
    {"one": definition("one"), "two": definition("two")},
    {NAME: replace(definition(NAME), local=False)},
])
def test_changed_path_or_ambiguous_bindings_are_refused(definitions):
    manager, _, _ = _manager()
    manager._relay = Mock(local_enabled=True)
    manager._read_definitions = Mock(return_value=(definitions, "version"))
    url = URL + "-rotated" if NAME in definitions else URL
    with pytest.raises(CommunityMcpBindingConflictError):
        manager.community_connection(name=NAME, url=url, connect=True, acknowledged=True)
    manager._relay.add.assert_not_called()


@pytest.mark.parametrize("fields", [{}, {"connect": True}, {"acknowledged": True}, {"connect": 1, "acknowledged": True}])
def test_consent_and_read_mode_validation(fields):
    manager, _, _ = _manager()
    manager._relay = Mock(local_enabled=True)
    manager._read_definitions = Mock(return_value=({}, "version"))
    if fields == {}:
        assert manager.community_connection(name=NAME, url=URL)["state"] == "not_connected"
    else:
        with pytest.raises(McpValidationError):
            manager.community_connection(name=NAME, url=URL, **fields)


def test_new_connection_uses_existing_pinning_and_deny_all():
    manager, _, _ = _manager()
    manager._relay = Mock(local_enabled=True)
    create = Mock(return_value={"name": NAME})
    manager.create_server = create
    assert manager.community_connection(name=NAME, url=URL, connect=True, acknowledged=True) == {
        "state": "configured", "server_name": NAME, "reused": False}
    create.assert_called_once_with(name=NAME, url=URL, local=True, local_acknowledged=True,
                                  require_tool_selection=True, reuse_local_connection=True)


@pytest.mark.parametrize("url", ["http://localhost:9583/private_fixture", "http://8.8.8.8:9583/private_fixture", URL + "?token=fixture"])
def test_unsafe_destination_does_not_create(url):
    manager, _, _ = _manager()
    manager._relay = Mock(local_enabled=True)
    with pytest.raises(McpValidationError):
        manager.community_connection(name=NAME, url=url, connect=True, acknowledged=True)


def test_ordinary_name_cannot_claim_community_identity():
    manager, _, _ = _manager()
    manager._relay = Mock(local_enabled=True)
    with pytest.raises(McpValidationError):
        manager.community_connection(name="manual-home", url=URL)


def test_match_does_not_reuse_different_authentication():
    assert McpManager._community_match({"home": definition("home", auth_mode="bearer", credential_configured=True)}, NAME, URL) is None


def test_rotated_path_of_reused_manual_connection_requires_removal():
    manager, _, _ = _manager()
    manager._relay = Mock(local_enabled=True)
    manager._read_definitions = Mock(return_value=({"manual-home": definition()}, "version"))
    for connect in (False, True):
        with pytest.raises(CommunityMcpBindingConflictError):
            manager.community_connection(name=NAME, url=URL + "-rotated", connect=connect, acknowledged=connect)
    manager._relay.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="Private local relay storage requires Linux")
async def test_real_private_registry_pins_destination_and_native_config_never_contains_secret(tmp_path):
    relay = McpRelay(tmp_path / "private", resolver=lambda _: ("192.168.1.20",))
    await relay.start()
    try:
        manager, client, gate = _manager(_config(), {"status": "ok", "version": "user-v2"}, {})
        manager._relay = relay
        result = manager.community_connection(name=NAME, url=URL, connect=True, acknowledged=True)
        assert result == {"state": "configured", "server_name": NAME, "reused": False}
        native = client.calls[1].params["edits"][0]["value"]
        assert native["enabled_tools"] == []
        assert native["url"].startswith("http://127.0.0.1:")
        assert "private_synthetic-fixture" not in str(client.calls) + str(result)
        assert relay.original_url(NAME, {key: native[key] for key in ("url", "http_headers")}) == URL
        assert all(lease.released for lease in gate.leases)
    finally:
        await relay.close()


def test_authenticated_route_reports_binding_conflict_without_private_destination():
    from fastapi.testclient import TestClient
    from test_mcp_manager import _route_app

    manager, _, _ = _manager()
    manager._relay = Mock(local_enabled=True)
    manager._read_definitions = Mock(return_value=({"manual-home": definition()}, "version"))
    client = TestClient(_route_app(manager))
    payload = {"name": NAME, "url": URL + "-rotated", "connect": True, "acknowledged": True}
    assert client.post("/mcp/community/connection", json=payload).status_code == 401
    response = client.post("/mcp/community/connection",
        headers={"Authorization": "Bearer bridge-token"}, json=payload)
    assert response.status_code == 409
    assert response.json() == {"detail": {
        "code": "community_mcp_connection_changed", "retryable": False}}
    assert "private_synthetic" not in response.text


def test_rotated_host_of_reused_manual_connection_requires_removal():
    manager, _, _ = _manager()
    manager._relay = Mock(local_enabled=True)
    manager._read_definitions = Mock(return_value=({"manual-home": definition()}, "version"))
    with pytest.raises(CommunityMcpBindingConflictError):
        manager.community_connection(name=NAME, url=URL.replace("ha.local", "other-ha.local"),
                                     connect=True, acknowledged=True)
    manager._relay.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="Private relay storage requires Linux")
@pytest.mark.parametrize("community", [True, False])
async def test_committed_create_reload_failure_blocks_status_retry_and_runtime_until_restart(tmp_path, community):
    from copy import deepcopy
    from aiohttp import ClientSession
    from codex_bridge_service.mcp_manager import McpRecoveryRequiredError
    from codex_bridge_service.runtime_gate import RuntimeGate
    from codex_bridge_service.resource_limits import ResourceLimits
    from test_mcp_management import NativeConfig

    relay = McpRelay(tmp_path / "private", resolver=lambda _: ("192.168.1.20",))
    await relay.start()
    try:
        native = NativeConfig()
        original = deepcopy(native.servers)
        gate = RuntimeGate(limits=ResourceLimits())
        manager = McpManager(native, gate, enabled=True, relay=relay,
                             resolver=lambda _: ("93.184.216.34",))
        native.reload_failures = 1
        def connect():
            if community:
                return manager.community_connection(name=NAME, url=URL, connect=True, acknowledged=True)
            return manager.create_server(name=NAME, url=URL, local=True,
                                         local_acknowledged=True, require_tool_selection=True)
        with pytest.raises(McpRecoveryRequiredError):
            connect()
        assert native.servers["vendor"] == original["vendor"]
        saved = native.servers[NAME]
        assert saved["enabled_tools"] == []
        assert len(native.writes) == 1
        assert gate.snapshot().closed and not gate.snapshot().config_mutation_active
        assert not manager.is_active_server(NAME)
        binding = {key: saved[key] for key in ("url", "http_headers")}
        assert relay.original_url(NAME, binding) == URL
        async with ClientSession() as session:
            response = await session.post(saved["url"], headers=saved["http_headers"], json={})
            assert response.status == 403
        for operation in (connect, lambda: manager.community_connection(name=NAME, url=URL), manager.list_servers):
            with pytest.raises(McpRecoveryRequiredError):
                operation()
        assert len(native.writes) == 1
        # A new manager after App restart reconciles the retained deny-all config.
        restarted = McpManager(native, RuntimeGate(limits=ResourceLimits()), enabled=True, relay=relay,
                               resolver=lambda _: ("93.184.216.34",))
        assert restarted.community_connection(name=NAME, url=URL) == {
            "state": "configured", "server_name": NAME, "reused": True}
        assert native.servers[NAME]["enabled_tools"] == []
    finally:
        await relay.close()
