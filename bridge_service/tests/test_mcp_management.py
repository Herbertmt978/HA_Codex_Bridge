"""Connection changes preserve configuration and exclude stale or active work."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codex_bridge_service.mcp_manager import (
    McpConflictError, McpManager, McpRecoveryRequiredError, McpValidationError,
    McpUnavailableError, McpServerDefinition,
)
from codex_bridge_service.runtime_gate import RuntimeGate
from codex_bridge_service.resource_limits import ResourceLimits
from codex_bridge_service.routes.mcp import router


class NativeConfig:
    def __init__(self, *, enabled=True):
        self.servers = {"vendor": {"url": "https://mcp.vendor.example/stream"}}
        if not enabled:
            self.servers["vendor"]["enabled"] = False
        self.version = 1
        self.reload_failures = 0
        self.write_timeout = False
        self.masked = False
        self.status_failure = False
        self.writes = []

    def register_notification_handler(self, *_args):
        pass

    def register_request_handler(self, *_args):
        pass

    def activate_validated_mcp_config(self):
        self.masked = False

    def request(self, method, params=None, **_kwargs):
        if method == "config/read":
            servers = deepcopy(self.servers)
            for value in servers.values():
                value.setdefault("enabled", True)
                value.update(environment_id="local", tool_timeout_sec=None)
            return {"config": {"mcp_servers": {} if self.masked else servers}, "layers": [{
                "name": {"type": "user", "file": "/data/config.toml"},
                "version": f"v{self.version}", "config": {"mcp_servers": deepcopy(self.servers)},
            }]}
        if method == "config/batchWrite":
            assert params["expectedVersion"] == f"v{self.version}"
            self.writes.append(deepcopy(params))
            for edit in params["edits"]:
                if edit["keyPath"] == "mcp_servers":
                    self.servers = deepcopy(edit["value"])
                else:
                    self.servers[edit["keyPath"].split(".")[1]] = deepcopy(edit["value"])
            self.version += 1
            if self.write_timeout:
                self.write_timeout = False
                raise TimeoutError("response lost after commit")
            return {"status": "ok", "version": f"v{self.version}"}
        if method == "config/mcpServer/reload":
            if self.reload_failures:
                self.reload_failures -= 1
                raise RuntimeError("untrusted provider failure")
            return {}
        if method == "mcpServerStatus/list":
            if self.status_failure:
                raise RuntimeError("private provider detail")
            return {"data": [{"name": "vendor", "tools": {"echo": {}}, "resources": []}]}
        raise AssertionError(method)


def manager_for(client):
    gate = RuntimeGate(limits=ResourceLimits())
    return McpManager(client, gate, enabled=True, resolver=lambda _: ()), gate


def test_pause_resume_revisions_and_retained_configuration():
    client = NativeConfig()
    manager, _gate = manager_for(client)
    before = manager.list_servers()[0]
    paused = manager.set_server_enabled("vendor", enabled=False, revision=before["revision"])
    assert paused["enabled"] is False and paused["startup"] == "paused"
    assert client.servers["vendor"] == {"url": before["endpoint"], "enabled": False}
    assert manager.list_servers()[0]["tool_count"] == 0
    with pytest.raises(McpConflictError):
        manager.set_server_enabled("vendor", enabled=True, revision=before["revision"])
    resumed = manager.set_server_enabled("vendor", enabled=True, revision=paused["revision"])
    assert resumed["enabled"] is True
    assert client.servers["vendor"] == {"url": before["endpoint"]}


def test_paused_definition_survives_startup_sanitisation():
    client = NativeConfig(enabled=False)
    client.masked = True
    manager, _gate = manager_for(client)
    manager.sanitize_startup_servers()
    manager.activate_validated_mcp_config()
    assert manager.list_servers()[0]["startup"] == "paused"
    assert client.servers["vendor"]["enabled"] is False


def test_each_server_revision_uses_the_native_config_version():
    client = NativeConfig()
    client.servers["second"] = {"url": "https://second.example/tools"}
    manager, _ = manager_for(client)
    second = next(row for row in manager.list_servers() if row["name"] == "second")
    paused = manager.set_server_enabled("second", enabled=False, revision=second["revision"])
    assert paused["enabled"] is False


def test_status_failure_preserves_safe_connection_controls():
    client = NativeConfig()
    manager, _ = manager_for(client)
    client.status_failure = True
    row = manager.list_servers()[0]
    assert row["status_unavailable"] is True and row["startup"] == "unknown"
    assert "private provider" not in str(row)
    manager.set_server_enabled("vendor", enabled=False, revision=row["revision"])
    assert manager.list_servers()[0]["startup"] == "paused"


def test_active_and_queued_prompts_exclude_connection_changes():
    client = NativeConfig()
    manager, gate = manager_for(client)
    revision = manager.list_servers()[0]["revision"]
    leases = [gate.reserve_prompt(client_request_id=f"fixture-{index}")
              for index in range(gate.limits.max_active_turns + 1)]
    try:
        assert gate.snapshot().queued_prompts == 1
        with pytest.raises(McpConflictError):
            manager.set_server_enabled("vendor", enabled=False, revision=revision)
        assert not client.writes
    finally:
        for lease in leases:
            lease.release()


def test_restart_and_external_config_changes_invalidate_open_forms():
    client = NativeConfig()
    first, _ = manager_for(client)
    revision = first.list_servers()[0]["revision"]
    second, _ = manager_for(client)
    with pytest.raises(McpConflictError):
        second.set_server_enabled("vendor", enabled=False, revision=revision)
    client.version += 1
    with pytest.raises(McpConflictError):
        first.set_server_enabled("vendor", enabled=False, revision=revision)
    assert not client.writes


@pytest.mark.parametrize("failure", ["reload", "lost_write_response"])
def test_failure_restores_last_usable_config_and_releases_lease(failure):
    client = NativeConfig()
    manager, gate = manager_for(client)
    revision = manager.list_servers()[0]["revision"]
    original = deepcopy(client.servers)
    if failure == "reload":
        client.reload_failures = 1
    else:
        client.write_timeout = True
    with pytest.raises((McpUnavailableError, McpConflictError)):
        manager.set_server_enabled("vendor", enabled=False, revision=revision)
    assert client.servers == original
    assert not gate.snapshot().config_mutation_active
    assert not gate.snapshot().closed
    refreshed = manager.list_servers()[0]
    assert refreshed["enabled"] is True
    manager.set_server_enabled("vendor", enabled=False, revision=refreshed["revision"])


def test_failed_rollback_blocks_new_work_until_restart():
    client = NativeConfig()
    manager, gate = manager_for(client)
    revision = manager.list_servers()[0]["revision"]
    client.reload_failures = 2
    with pytest.raises(McpRecoveryRequiredError):
        manager.set_server_enabled("vendor", enabled=False, revision=revision)
    assert gate.snapshot().closed
    with pytest.raises(McpRecoveryRequiredError):
        manager.list_servers()


def test_destination_edit_requires_pause_and_explicit_credential_choice():
    client = NativeConfig()
    manager, _ = manager_for(client)
    revision = manager.list_servers()[0]["revision"]
    change = {"url": "https://mcp.new.example/tools", "endpoint_acknowledged": True,
              "credential_action": "keep"}
    with pytest.raises(McpConflictError):
        manager.edit_server("vendor", revision=revision, **change)
    paused = manager.set_server_enabled("vendor", enabled=False, revision=revision)
    with pytest.raises(McpValidationError):
        manager.edit_server("vendor", revision=paused["revision"], **{**change, "credential_action": None})
    edited = manager.edit_server("vendor", revision=paused["revision"], **change)
    assert edited["enabled"] is False
    assert client.servers["vendor"] == {"url": change["url"], "enabled": False}


def test_an_existing_runtime_mutation_excludes_connection_changes():
    client = NativeConfig()
    manager, gate = manager_for(client)
    revision = manager.list_servers()[0]["revision"]
    lease = gate.acquire_config_mutation()
    try:
        with pytest.raises(McpConflictError):
            manager.set_server_enabled("vendor", enabled=False, revision=revision)
        assert not client.writes
    finally:
        lease.release()


def test_connection_routes_require_auth_and_preserve_paused_state():
    native = NativeConfig()
    manager, _ = manager_for(native)
    app = FastAPI()
    app.include_router(router)
    app.state.auth_token = "synthetic-bridge-token"
    app.state.storage = SimpleNamespace(runtime_profile="external_legacy")
    app.state.mcp_manager = manager
    client = TestClient(app)
    revision = manager.list_servers()[0]["revision"]
    payload = {"enabled": False, "revision": revision}
    assert client.put("/mcp/servers/vendor/state", json=payload).status_code == 401
    assert not native.writes
    headers = {"Authorization": "Bearer synthetic-bridge-token"}
    paused = client.put("/mcp/servers/vendor/state", headers=headers, json=payload)
    assert paused.status_code == 200 and paused.headers["Cache-Control"] == "no-store"
    assert paused.json()["enabled"] is False
    assert client.put("/mcp/servers/vendor/state", headers=headers, json=payload).status_code == 409
    edit = {"revision": paused.json()["revision"], "url": "https://new.example/tools",
            "endpoint_acknowledged": True, "credential_action": "keep"}
    saved = client.put("/mcp/servers/vendor", headers=headers, json=edit)
    assert saved.status_code == 200 and saved.headers["Cache-Control"] == "no-store"
    assert saved.json()["enabled"] is False
    assert native.servers["vendor"]["url"] == edit["url"]


def test_failed_credential_reload_invalidates_destination_forms(monkeypatch):
    client = NativeConfig(enabled=False)
    manager, _ = manager_for(client)
    definition = McpServerDefinition("vendor", "https://mcp.vendor.example/stream",
        relayed=True, auth_mode="bearer", credential_configured=True, enabled=False)
    monkeypatch.setattr(manager, "_read_definitions", lambda: ({"vendor": definition}, "v1"))
    manager._relay = SimpleNamespace(replace_credential=lambda *_: None,
                                    native_config=lambda *_args, **_kwargs: {})
    revision = manager._revision("vendor", "v1")
    client.reload_failures = 1
    with pytest.raises(McpUnavailableError):
        manager.replace_credential("vendor", {"mode": "bearer", "token": "synthetic-new-token"}, acknowledged=True)
    with pytest.raises(McpConflictError):
        manager.edit_server("vendor", url="https://new.example/tools", revision=revision,
                            endpoint_acknowledged=True, credential_action="keep")
