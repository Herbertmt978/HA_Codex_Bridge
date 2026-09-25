"""Connection changes preserve configuration and exclude stale or active work."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codex_bridge_service.mcp_manager import (
    McpConflictError, McpManager, McpRecoveryRequiredError, McpValidationError,
    McpUnavailableError, McpServerDefinition,
)
from codex_bridge_service.mcp_stdio_adapter import StdioMcpAdapter
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
        self.tools_error = None
        self.tools = {"echo": {"description": "Read a value", "annotations": {"readOnlyHint": True}},
                      "erase": {"description": "Delete a value", "annotations": {"destructiveHint": True}}}
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
            config = self.servers["vendor"]
            tools = deepcopy(self.tools) if config.get("enabled", True) else {}
            if "enabled_tools" in config:
                tools = {name: item for name, item in tools.items() if name in config["enabled_tools"]}
            return {"data": [{"name": "vendor", "tools": tools, "toolsError": self.tools_error, "resources": []}]}
        raise AssertionError(method)


def manager_for(client, marker_path=None):
    gate = RuntimeGate(limits=ResourceLimits())
    return McpManager(client, gate, enabled=True, resolver=lambda _: (), discovery_marker=marker_path), gate


class StdioAdapterFixture:
    """Private binding stub; tests the manager's native configuration contract."""

    def __init__(self):
        self.record = None
        self.allowed_tools = ()
        self.active = False
        self.port = 8767

    def add(self, name, package_id, revision):
        assert self.record is None
        self.record = (name, package_id, revision)

    def metadata(self, name):
        assert self.record is not None and self.record[0] == name
        return {
            "package_id": self.record[1],
            "revision": self.record[2],
            "allowed_tools": list(self.allowed_tools),
        }

    def native_config(self, name, *, active=False):
        assert self.record is not None and self.record[0] == name
        self.active = active
        return {
            "url": f"http://127.0.0.1:{self.port}/mcp/{name}",
            "http_headers": {"x-codex-stdio-mcp": "private-token"},
        }

    def original_binding(self, name, value, *, effective=False):
        if self.record is None or self.record[0] != name:
            return False
        if effective:
            value = {
                key: item for key, item in value.items()
                if key not in {"enabled", "environment_id", "tool_timeout_sec"}
            }
        return value == self.native_config(name, active=self.active)

    def set_tools(self, name, selected):
        assert self.record is not None and self.record[0] == name
        self.allowed_tools = selected

    def deactivate(self, name):
        self.active = False

    def remove(self, name):
        self.deactivate(name)
        self.record = None

    def discover_tools(self, name):
        assert self.record is not None and self.record[0] == name
        return [{"name": "get_current_time", "inputSchema": {"type": "object"}}]


def test_stdio_connection_starts_paused_and_exposes_no_private_binding():
    client = NativeConfig()
    adapter = StdioAdapterFixture()
    gate = RuntimeGate(limits=ResourceLimits())
    manager = McpManager(client, gate, enabled=True, resolver=lambda _: (),
                         stdio_adapter=adapter)
    created = manager.create_stdio_server(name="clock", package_id="bridge-time",
        revision="1.0.0", acknowledged=True)
    assert created["enabled"] is False
    assert client.servers["clock"]["enabled_tools"] == []
    assert client.servers["clock"]["enabled"] is False
    assert "private-token" not in str(created)
    listing = manager.list_servers()
    clock = next(row for row in listing if row["name"] == "clock")
    assert clock["transport"] == "stdio"
    assert clock["network"] == "none"
    assert "127.0.0.1" not in str(clock)
    tools = manager.list_server_tools("clock")
    assert tools["catalogue_available"] is True
    assert tools["tools"][0]["name"] == "get_current_time"
    assert "127.0.0.1" not in str(tools)
    assert not adapter.active
    selected = manager.set_server_tools(
        "clock", enabled_tools=["get_current_time"],
        revision=tools["revision"], catalogue_revision=tools["catalogue_revision"],
    )
    assert adapter.allowed_tools == ("get_current_time",)
    assert not adapter.active
    resumed = manager.set_server_enabled(
        "clock", enabled=True, revision=selected["revision"],
    )
    assert resumed["enabled"] is True
    assert adapter.active
    manager.remove_server("clock")
    assert adapter.record is None


def test_stdio_create_route_requires_bridge_auth_and_explicit_review():
    native = NativeConfig()
    adapter = StdioAdapterFixture()
    manager = McpManager(native, RuntimeGate(limits=ResourceLimits()), enabled=True,
                         resolver=lambda _: (), stdio_adapter=adapter)
    app = FastAPI()
    app.include_router(router)
    app.state.auth_token = "synthetic-bridge-token"
    app.state.storage = SimpleNamespace(runtime_profile="external_legacy")
    app.state.mcp_manager = manager
    client = TestClient(app)
    payload = {"name": "clock", "package_id": "bridge-time",
               "revision": "1.0.0", "acknowledged": True}
    assert client.post("/mcp/stdio/servers", json=payload).status_code == 401
    assert adapter.record is None
    headers = {"Authorization": "Bearer synthetic-bridge-token"}
    assert client.post("/mcp/stdio/servers", headers=headers,
                       json={**payload, "acknowledged": False}).status_code == 400
    assert adapter.record is None
    created = client.post("/mcp/stdio/servers", headers=headers, json=payload)
    assert created.status_code == 201
    assert created.json()["enabled"] is False
    assert "private-token" not in created.text


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


def test_startup_rebinds_saved_stdio_selection_to_current_private_port():
    adapter = StdioMcpAdapter(lambda *_: None, package_verifier=lambda *_: object())
    adapter.port = 8767
    adapter.add("clock", "bridge-time", "1.0.0")
    old = adapter.native_config("clock")
    client = NativeConfig()
    client.servers = {"clock": {
        **old, "url": "http://127.0.0.1:9531/mcp/clock",
        "enabled": False, "enabled_tools": [],
    }}
    client.masked = True
    gate = RuntimeGate(limits=ResourceLimits())
    manager = McpManager(client, gate, enabled=True, resolver=lambda _: (),
                         stdio_adapter=adapter)
    manager.sanitize_startup_servers()
    assert client.servers["clock"]["url"] == old["url"]
    assert client.servers["clock"]["enabled"] is False
    assert client.servers["clock"]["enabled_tools"] == []


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


def test_tool_policy_survives_restart_and_new_or_renamed_tools_stay_blocked(tmp_path):
    native = NativeConfig()
    marker = tmp_path / "mcp-tool-discovery.pending"
    manager, _ = manager_for(native, marker)
    inventory = manager.list_server_tools("vendor")
    assert inventory["mode"] == "all"
    assert inventory["tools"][0]["destructive"] is False
    assert inventory["tools"][1]["destructive"] is True
    saved = manager.set_server_tools("vendor", enabled_tools=["echo"],
        revision=inventory["revision"], catalogue_revision=inventory["catalogue_revision"])
    assert native.servers["vendor"]["enabled_tools"] == ["echo"]
    assert saved["tool_policy"] == "selected"
    native.tools = {"echo_renamed": {}, "new_tool": {}}
    native.masked = True
    restarted, _ = manager_for(native, marker)
    restarted.sanitize_startup_servers()
    restarted.activate_validated_mcp_config()
    current = restarted.list_server_tools("vendor")
    assert current["enabled_tools"] == ["echo"]
    assert current["stale_tools"] == ["echo"]
    assert {tool["name"] for tool in current["tools"]} == {"echo_renamed", "new_tool"}
    assert native.servers["vendor"]["enabled_tools"] == ["echo"]
    assert not marker.exists()


def test_tool_policy_rejects_stale_catalogue_and_changes_during_work():
    native = NativeConfig()
    manager, gate = manager_for(native)
    inventory = manager.list_server_tools("vendor")
    native.tools["new"] = {}
    refreshed = manager.list_server_tools("vendor")
    with pytest.raises(McpConflictError):
        manager.set_server_tools("vendor", enabled_tools=["echo"],
            revision=inventory["revision"], catalogue_revision=inventory["catalogue_revision"])
    current = refreshed
    lease = gate.reserve_prompt(client_request_id="tool-policy-fixture")
    try:
        with pytest.raises(McpConflictError):
            manager.set_server_tools("vendor", enabled_tools=["echo"],
                revision=current["revision"], catalogue_revision=current["catalogue_revision"])
    finally:
        lease.release()
    assert not native.writes
    with pytest.raises(McpValidationError):
        manager.set_server_tools("vendor", enabled_tools=["unknown"],
            revision=current["revision"], catalogue_revision=current["catalogue_revision"])


def test_tool_catalogue_is_bounded_and_untrusted_text_is_data():
    native = NativeConfig()
    native.tools = {f"tool_{index}": {"description": "<img onerror=alert(1)>" * 40}
                    for index in range(600)}
    native.tools["tool_000"] = {"name": "different"}
    manager, _ = manager_for(native)
    inventory = manager.list_server_tools("vendor")
    assert inventory["catalogue_truncated"] is True
    assert len(inventory["tools"]) <= 512
    assert all(len(tool["description"]) <= 512 for tool in inventory["tools"])
    assert all(tool["name"] != "tool_000" for tool in inventory["tools"])
    with pytest.raises(McpValidationError):
        manager.set_server_tools("vendor", enabled_tools=["echo", "echo"],
            revision=inventory["revision"], catalogue_revision=inventory["catalogue_revision"])

    native.tools = {"safe": {}, "bad": {"name": "different"}}
    incomplete = manager.list_server_tools("vendor")
    assert incomplete["catalogue_truncated"] is True
    assert [tool["name"] for tool in incomplete["tools"]] == ["safe"]


def test_tool_discovery_error_is_not_an_empty_successful_catalogue(tmp_path):
    native = NativeConfig()
    native.servers["vendor"]["enabled_tools"] = ["echo"]
    native.tools_error = "private provider failure"
    manager, _ = manager_for(native, tmp_path / "mcp-tool-discovery.pending")
    assert manager.list_servers()[0]["status_unavailable"] is True
    inventory = manager.list_server_tools("vendor")
    assert inventory["catalogue_available"] is False
    assert inventory["tools"] == [] and inventory["stale_tools"] == []
    assert native.servers["vendor"]["enabled_tools"] == ["echo"]
    with pytest.raises(McpConflictError):
        manager.set_server_tools("vendor", enabled_tools=[],
            revision=inventory["revision"], catalogue_revision=inventory["catalogue_revision"])


def test_tool_policy_route_requires_admin_token_and_does_not_cache_catalogue():
    native = NativeConfig()
    manager, _ = manager_for(native)
    app = FastAPI()
    app.include_router(router)
    app.state.auth_token = "synthetic-bridge-token"
    app.state.storage = SimpleNamespace(runtime_profile="external_legacy")
    app.state.mcp_manager = manager
    client = TestClient(app)
    assert client.post("/mcp/servers/vendor/tools/discover").status_code == 401
    headers = {"Authorization": "Bearer synthetic-bridge-token"}
    response = client.post("/mcp/servers/vendor/tools/discover", headers=headers)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    payload = {"enabled_tools": [], "revision": response.json()["revision"],
               "catalogue_revision": response.json()["catalogue_revision"]}
    assert client.put("/mcp/servers/vendor/tools", json=payload).status_code == 401
    saved = client.put("/mcp/servers/vendor/tools", headers=headers, json=payload)
    assert saved.status_code == 200 and saved.headers["Cache-Control"] == "no-store"
    assert native.servers["vendor"]["enabled_tools"] == []


def test_paired_new_connection_starts_with_no_allowed_tools():
    native = NativeConfig()
    manager, _ = manager_for(native)
    created = manager.create_server(name="scoped", url="https://tools.example.com/mcp",
                                    require_tool_selection=True)
    assert created["tool_policy"] == "selected"
    assert native.servers["scoped"]["enabled_tools"] == []
    legacy = manager.create_server(name="legacy", url="https://legacy.example.com/mcp")
    assert legacy["tool_policy"] == "all"
    assert "enabled_tools" not in native.servers["legacy"]


def test_interrupted_discovery_pauses_servers_before_startup_activation(tmp_path):
    native = NativeConfig()
    native.servers["vendor"]["enabled_tools"] = ["echo"]
    native.servers["other"] = {"url": "https://other.example/stream", "enabled_tools": ["keep"]}
    native.masked = True
    marker = tmp_path / "mcp-tool-discovery.pending"
    marker.write_text(json.dumps({"version": 1, "server": "vendor", "enabled_tools": ["echo"]}))
    manager, _ = manager_for(native, marker)
    manager.sanitize_startup_servers()
    manager.activate_validated_mcp_config()
    assert native.servers["vendor"]["enabled"] is False
    assert native.servers["vendor"]["enabled_tools"] == ["echo"]
    assert native.servers["other"]["enabled"] is False
    assert native.servers["other"]["enabled_tools"] == ["keep"]
    assert not marker.exists()


def test_interrupted_unfiltered_discovery_restores_only_its_own_policy(tmp_path):
    native = NativeConfig()
    native.servers["other"] = {"url": "https://other.example/stream", "enabled_tools": ["keep"]}
    native.masked = True
    marker = tmp_path / "mcp-tool-discovery.pending"
    marker.write_text(json.dumps({"version": 1, "server": "vendor", "enabled_tools": ["echo"]}))
    manager, _ = manager_for(native, marker)
    manager.sanitize_startup_servers()
    manager.activate_validated_mcp_config()
    assert native.servers["vendor"]["enabled"] is False
    assert native.servers["vendor"]["enabled_tools"] == ["echo"]
    assert native.servers["other"]["enabled_tools"] == ["keep"]
    assert not marker.exists()


def test_damaged_discovery_marker_keeps_bootstrap_masked(tmp_path):
    native = NativeConfig()
    native.masked = True
    marker = tmp_path / "mcp-tool-discovery.pending"
    marker.write_text("incomplete")
    manager, gate = manager_for(native, marker)
    with pytest.raises(McpRecoveryRequiredError):
        manager.sanitize_startup_servers()
    assert native.masked and marker.exists() and not native.writes
    assert gate.snapshot().closed


def test_failed_catalogue_probe_restores_filter_or_blocks_all_work(tmp_path):
    native = NativeConfig()
    native.servers["vendor"]["enabled_tools"] = ["echo"]
    marker = tmp_path / "mcp-tool-discovery.pending"
    manager, gate = manager_for(native, marker)
    native.reload_failures = 1
    assert manager.list_server_tools("vendor")["catalogue_available"] is False
    assert native.servers["vendor"]["enabled_tools"] == ["echo"]
    assert not marker.exists() and not gate.snapshot().closed

    native.reload_failures = 2
    with pytest.raises(McpRecoveryRequiredError):
        manager.list_server_tools("vendor")
    assert marker.exists() and gate.snapshot().closed
    assert json.loads(marker.read_text()) == {
        "version": 1, "server": "vendor", "enabled_tools": ["echo"],
    }


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
