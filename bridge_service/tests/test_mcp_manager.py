from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, replace
import os
from threading import Event, Thread
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codex_bridge_service.mcp_manager import (
    McpConflictError,
    McpManager,
    McpServerDefinition,
    McpProtocolError,
    McpUnavailableError,
    McpValidationError,
)
from codex_bridge_service.routes.mcp import router
from codex_bridge_service.mcp_relay import McpRelay


@dataclass(frozen=True, slots=True)
class Call:
    method: str
    params: object


class AppServerDouble:
    def __init__(self, *responses: object) -> None:
        self.responses = deque(responses)
        self.calls: list[Call] = []
        self.notifications: dict[str, object] = {}
        self.requests: dict[str, object] = {}

    def request(self, method: str, params: object = None, **_kwargs: object) -> object:
        self.calls.append(Call(method, deepcopy(params)))
        if not self.responses:
            raise AssertionError(f"unexpected request: {method}")
        response = self.responses.popleft()
        if isinstance(response, BaseException):
            raise response
        return deepcopy(response)

    def register_notification_handler(self, method: str, handler: object) -> None:
        self.notifications[method] = handler

    def register_request_handler(self, method: str, handler: object) -> None:
        self.requests[method] = handler


class AppServerWithoutRequestHandler(AppServerDouble):
    register_request_handler = None


class LayeredConfigAppServer(AppServerDouble):
    """Stateful native config with a disabled-process effective override."""

    def __init__(self) -> None:
        super().__init__()
        self.override_mcp = True
        self.version = "user-v1"
        self.user_config: dict[str, object] = {
            "mcp_servers": {"stale": {"url": "https://stale-mcp.example/stream"}},
            "features": {"plugins": True},
        }

    def request(self, method: str, params: object = None, **_kwargs: object) -> object:
        self.calls.append(Call(method, deepcopy(params)))
        if method == "config/read":
            effective = deepcopy(self.user_config)
            if self.override_mcp:
                effective["mcp_servers"] = {}
            return {
                "config": effective,
                "layers": [
                    {
                        "name": {
                            "type": "user",
                            "file": "/data/codex-home/config.toml",
                        },
                        "version": self.version,
                        "config": deepcopy(self.user_config),
                    }
                ],
                "origins": {},
            }
        if method == "config/batchWrite":
            assert isinstance(params, dict)
            assert params["expectedVersion"] == self.version
            assert params["edits"] == [
                {
                    "keyPath": "mcp_servers",
                    "mergeStrategy": "replace",
                    "value": None,
                }
            ]
            self.user_config.pop("mcp_servers", None)
            self.version = "user-v2"
            return {
                "status": "ok",
                "version": self.version,
                "filePath": "/data/codex-home/config.toml",
            }
        if method == "config/mcpServer/reload":
            return {}
        if method == "mcpServerStatus/list":
            return {"data": []}
        raise AssertionError(f"unexpected request: {method}")


class StartupSanitizingAppServer(AppServerDouble):
    """Native config fixture that models the masked bootstrap generation."""

    def __init__(self) -> None:
        super().__init__()
        self.override_mcp = True
        self.version = "user-v1"
        self.user_config: dict[str, object] = {
            "mcp_servers": {
                "safe": {
                    "url": "https://mcp.vendor.example/stream",
                    "oauth_client_id": "public-client",
                    "oauth_resource": "https://api.vendor.example",
                },
                "stdio": {"command": "sh", "args": ["-c", "id"]},
                "bearer": {
                    "url": "https://mcp.bearer.example/stream",
                    "bearer_token_env_var": "TOKEN",
                },
                "private": {"url": "https://localhost/mcp"},
            },
            "features": {"plugins": True},
        }
        self.non_user_mcp_servers: object | None = None
        self.profile_mcp_servers: object | None = None
        self.activation_calls = 0

    def request(self, method: str, params: object = None, **_kwargs: object) -> object:
        self.calls.append(Call(method, deepcopy(params)))
        if method == "config/read":
            effective = deepcopy(self.user_config)
            if self.override_mcp:
                # Match native Codex's recursive table merge, not a fictitious
                # empty-root replacement.
                for value in effective["mcp_servers"].values():
                    value["enabled"] = False
            layers: list[dict[str, object]] = [
                {
                    "name": {
                        "type": "user",
                        "file": "/data/codex-home/config.toml",
                    },
                    "version": self.version,
                    "config": deepcopy(self.user_config),
                }
            ]
            if self.non_user_mcp_servers is not None:
                layers.insert(
                    0,
                    {
                        "name": {"type": "system", "file": "/etc/codex.toml"},
                        "version": "system-v1",
                        "config": {"mcp_servers": deepcopy(self.non_user_mcp_servers)},
                    },
                )
            if self.profile_mcp_servers is not None:
                layers.append(
                    {
                        "name": {
                            "type": "user",
                            "file": "/data/codex-home/config.toml",
                            "profile": "restricted",
                        },
                        "version": "profile-v1",
                        "config": {"mcp_servers": deepcopy(self.profile_mcp_servers)},
                    }
                )
            return {
                "config": effective,
                "layers": layers,
                "origins": {},
            }
        if method == "config/batchWrite":
            assert params == {
                "edits": [
                    {
                        "keyPath": "mcp_servers",
                        "mergeStrategy": "replace",
                        "value": {
                            "safe": {
                                "url": "https://mcp.vendor.example/stream",
                                "oauth_client_id": "public-client",
                                "oauth_resource": "https://api.vendor.example",
                            }
                        },
                    }
                ],
                "expectedVersion": "user-v1",
                "reloadUserConfig": True,
            }
            self.user_config["mcp_servers"] = deepcopy(params["edits"][0]["value"])
            self.version = "user-v2"
            return {
                "status": "ok",
                "version": self.version,
                "filePath": "/data/codex-home/config.toml",
            }
        if method == "config/mcpServer/reload":
            return {}
        if method == "mcpServerStatus/list":
            return {"data": []}
        raise AssertionError(f"unexpected request: {method}")

    def activate_validated_mcp_config(self) -> None:
        self.activation_calls += 1
        self.override_mcp = False


class Lease:
    def __init__(self) -> None:
        self.released = False

    def release(self) -> None:
        self.released = True


class GateDouble:
    def __init__(self, state: str = "idle") -> None:
        self.state = state
        self.leases: list[Lease] = []

    def acquire_config_mutation(self) -> Lease:
        if self.state != "idle":
            raise RuntimeError(self.state)
        lease = Lease()
        self.leases.append(lease)
        return lease


def _config(
    servers: dict[str, object] | None = None,
    *,
    version: str = "user-v1",
) -> dict[str, object]:
    return {
        "config": {"mcp_servers": servers or {}},
        "layers": [
            {
                "name": {"type": "user", "file": "/data/codex-home/config.toml"},
                "version": version,
                "config": {},
            }
        ],
        "origins": {},
    }


def _manager(
    *responses: object,
    state: str = "idle",
    resolver=None,
    enabled: bool = True,
) -> tuple[McpManager, AppServerDouble, GateDouble]:
    client = AppServerDouble(*responses)
    gate = GateDouble(state)
    options = {"enabled": enabled}
    if resolver is not None:
        options["resolver"] = resolver
    return McpManager(client, gate, **options), client, gate


def test_create_uses_native_cas_write_then_reload_and_releases_gate() -> None:
    manager, client, gate = _manager(
        _config(),
        {
            "status": "ok",
            "version": "user-v2",
            "filePath": "/data/codex-home/config.toml",
        },
        {},
    )

    result = manager.create_server(
        name="vendor_mcp",
        url="https://mcp.vendor.example/stream",
        oauth_client_id="public-client",
        oauth_resource="https://api.vendor.example",
    )

    assert result == {
        "name": "vendor_mcp",
        "enabled": True,
        "transport": "streamable_http",
        "network": "public",
        "endpoint": "https://mcp.vendor.example/stream",
        "auth": "oauth",
        "startup": "starting",
        "tool_count": 0,
        "resource_count": 0,
    }
    assert [(call.method, call.params) for call in client.calls] == [
        ("config/read", {"includeLayers": True}),
        (
            "config/batchWrite",
            {
                "edits": [
                    {
                        "keyPath": "mcp_servers.vendor_mcp",
                        "mergeStrategy": "replace",
                        "value": {
                            "url": "https://mcp.vendor.example/stream",
                            "oauth_client_id": "public-client",
                            "oauth_resource": "https://api.vendor.example",
                        },
                    }
                ],
                "expectedVersion": "user-v1",
                "reloadUserConfig": True,
            },
        ),
        ("config/mcpServer/reload", None),
    ]
    assert len(gate.leases) == 1 and gate.leases[0].released is True
    assert manager.is_active_server("vendor_mcp") is True
    assert manager.is_active_server("other_server") is False
    callback_finished = Event()
    with manager._lock:
        callback = Thread(
            target=lambda: (manager.is_active_server("vendor_mcp"), callback_finished.set()),
            daemon=True,
        )
        callback.start()
        assert callback_finished.wait(2), "MCP callback must not wait on a config mutation"
    callback.join(timeout=2)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="Private local MCP storage requires Linux")
async def test_local_create_list_remove_and_oauth_boundary(tmp_path):
    relay = McpRelay(tmp_path / "private", resolver=lambda _: ("192.168.1.2",))
    await relay.start()
    try:
        manager, client, gate = _manager(_config(), {"status": "ok", "version": "user-v2"}, {})
        manager._relay = relay
        result = manager.create_server(name="home", url="http://ha.local/private-secret", local=True, local_acknowledged=True)
        assert result["endpoint"] == "http://ha.local"
        assert result["network"] == "local"
        binding = client.calls[1].params["edits"][0]["value"]
        assert "private-secret" not in str(client.calls)
        assert binding["url"].startswith("http://127.0.0.1:")
        effective = {**binding, "enabled": True, "environment_id": "local", "tool_timeout_sec": None}
        client.responses.extend([_config({"home": effective}), {"data": [{"name": "home", "authStatus": "oAuth"}]}])
        view = manager.list_servers()[0]
        assert view["auth"] == "none" and view["endpoint"] == "http://ha.local"
        client.responses.append(_config({"home": binding}))
        with pytest.raises(McpValidationError):
            manager.start_oauth_login("home")
        client.responses.extend([_config({"home": binding}), {"status": "ok", "version": "user-v2"}, {}])
        manager.remove_server("home")
        assert relay.original_url("home", binding) is None
        assert all(lease.released for lease in gate.leases)
    finally:
        await relay.close()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="Private local MCP storage requires Linux")
async def test_local_consent_and_write_failure_never_leave_active_binding(tmp_path):
    relay = McpRelay(tmp_path / "private", resolver=lambda _: ("192.168.1.2",))
    await relay.start()
    try:
        manager, client, _ = _manager(_config(), RuntimeError("private failure"))
        manager._relay = relay
        for fields in [{}, {"local_acknowledged": "true"}, {"local_acknowledged": True, "oauth_client_id": "client"}]:
            with pytest.raises(McpValidationError):
                manager.create_server(name="home", url="http://ha.local/mcp", local=True, **fields)
        assert not client.calls
        with pytest.raises(McpConflictError):
            manager.create_server(name="home", url="http://ha.local/mcp", local=True, local_acknowledged=True)
        assert not relay._active and not relay._records
    finally:
        await relay.close()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="Private local MCP storage requires Linux")
@pytest.mark.parametrize("local_enabled", [False, True])
async def test_startup_retains_only_approved_bindings_and_option_revokes_them(tmp_path, local_enabled):
    relay = McpRelay(tmp_path / "private", resolver=lambda _: ("192.168.1.2",))
    await relay.start()
    try:
        relay.add("home", "http://ha.local/private-secret")
        binding = relay.native_config("home")
        binding["url"] = "http://127.0.0.1:1/mcp/home"  # persisted previous listener
        relay.add("orphan", "http://other.local/private-secret")
        config = _config()
        config["layers"][0]["config"] = {"mcp_servers": {
            "home": binding, "direct": {"url": "http://ha.local/mcp"},
            "tampered": {**binding, "http_headers": {"X-Codex-Local-Mcp": "wrong"}},
        }}
        manager, client, _ = _manager(config, {"status": "ok", "version": "user-v2"}, {})
        manager._relay = relay if local_enabled else None
        manager.sanitize_startup_servers()
        saved = client.calls[1].params["edits"][0]["value"]
        assert set(saved) == ({"home"} if local_enabled else set())
        if local_enabled:
            assert saved["home"] == relay.native_config("home")
            assert set(relay._records) == {"home"}
        assert "private-secret" not in str(client.calls)
    finally:
        await relay.close()


def test_remove_uses_same_cas_write_reload_boundary() -> None:
    manager, client, gate = _manager(
        _config({"vendor": {"url": "https://mcp.vendor.example"}}),
        {
            "status": "ok",
            "version": "user-v2",
            "filePath": "/data/codex-home/config.toml",
        },
        {},
    )

    manager.remove_server("vendor")
    assert manager.is_active_server("vendor") is False

    assert client.calls[1].params == {
        "edits": [
            {
                "keyPath": "mcp_servers.vendor",
                "mergeStrategy": "replace",
                "value": None,
            }
        ],
        "expectedVersion": "user-v1",
        "reloadUserConfig": True,
    }
    assert client.calls[2] == Call("config/mcpServer/reload", None)
    assert gate.leases[0].released is True


def test_cas_write_failure_is_a_retryable_conflict_and_never_reloads() -> None:
    manager, client, gate = _manager(_config(), RuntimeError("provider detail"))

    with pytest.raises(McpConflictError):
        manager.create_server(name="vendor", url="https://mcp.vendor.example")

    assert [call.method for call in client.calls] == [
        "config/read",
        "config/batchWrite",
    ]
    assert gate.leases[0].released is True


@pytest.mark.parametrize("initially_enabled", [False, True])
def test_failed_config_write_restores_active_snapshot_after_rollback(
    monkeypatch: pytest.MonkeyPatch, initially_enabled: bool,
) -> None:
    manager, _client, _gate = _manager()
    previous = McpServerDefinition(
        name="vendor", url="https://mcp.vendor.example/stream", enabled=initially_enabled,
    )
    updated = replace(previous, enabled=not initially_enabled)
    manager._active_names = frozenset({"vendor"} if initially_enabled else set())
    calls = 0

    def write(**_kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise McpUnavailableError()

    def read() -> tuple[dict[str, McpServerDefinition], str]:
        manager._active_names = frozenset({"vendor"} if updated.enabled else set())
        return {"vendor": updated}, "user-v2"

    monkeypatch.setattr(manager, "_write_config_value", write)
    monkeypatch.setattr(manager, "_read_definitions", read)
    monkeypatch.setattr(manager, "_reload", lambda: None)

    with pytest.raises(McpUnavailableError):
        manager._apply_definition(previous, updated, "user-v1")
    assert calls == 2
    assert manager.is_active_server("vendor") is initially_enabled


@pytest.mark.parametrize("state", ["turn", "queued", "auth"])
def test_mutation_is_blocked_for_turn_queue_or_auth(state: str) -> None:
    manager, client, gate = _manager(state=state)

    with pytest.raises(McpConflictError):
        manager.create_server(name="vendor", url="https://mcp.vendor.example")

    assert client.calls == []
    assert gate.leases == []


@pytest.mark.parametrize(
    "url",
    [
        "http://mcp.vendor.example",
        "https://user:secret@mcp.vendor.example",
        "https://mcp.vendor.example/?token=secret",
        "https://mcp.vendor.example/#fragment",
        "https://localhost/mcp",
        "https://bridge.local/mcp",
        "https://127.0.0.1/mcp",
        "https://[::1]/mcp",
        "https://10.0.0.1/mcp",
        "https://mcp_vendor.example/mcp",
    ],
)
def test_create_rejects_unsafe_endpoint_without_touching_native_config(
    url: str,
) -> None:
    manager, client, _gate = _manager()

    with pytest.raises(McpValidationError):
        manager.create_server(name="vendor", url=url)

    assert client.calls == []


def test_existing_stdio_bearer_and_environment_config_are_never_reflected() -> None:
    manager, client, _gate = _manager(
        _config(
            {
                "safe": {"url": "https://mcp.vendor.example/path"},
                "unsafe": {
                    "command": "sh",
                    "args": ["-c", "echo secret"],
                    "env": {"TOKEN": "secret"},
                },
                "bearer": {
                    "url": "https://mcp.bearer.example",
                    "bearer_token_env_var": "TOKEN",
                },
            }
        ),
        {
            "data": [
                {
                    "name": "safe",
                    "authStatus": "oAuth",
                    "serverInfo": {
                        "title": "Safe vendor",
                        "version": "1.2.3",
                        "description": "private /data/codex-home/auth.json",
                    },
                    "tools": {"tool": {"description": "Bearer secret"}},
                    "resources": [{"uri": "file:///data/codex-home/auth.json"}],
                    "resourceTemplates": [],
                    "error": "Bearer raw-provider-secret",
                }
            ],
            "nextCursor": None,
        },
    )

    views = manager.list_servers()

    assert len(views[0].pop("revision")) == 64
    assert views == [
        {
            "name": "safe",
            "enabled": True,
            "transport": "streamable_http",
        "network": "public",
            "endpoint": "https://mcp.vendor.example/path",
            "auth": "oauth",
            "startup": "unknown",
            "tool_count": 1,
            "resource_count": 1,
            "title": "Safe vendor",
            "version": "1.2.3",
        }
    ]
    rendered = repr(views)
    assert "secret" not in rendered.lower()
    assert "/data/" not in rendered
    assert len(client.calls) == 2


def test_malformed_native_payload_is_rejected_without_reflecting_it() -> None:
    manager, _client, _gate = _manager(
        {"config": {"mcp_servers": {}}, "layers": "not-a-list"}
    )

    with pytest.raises(McpProtocolError):
        manager.list_servers()


def test_start_oauth_returns_raw_url_once_without_storing_it() -> None:
    oauth_url = "https://auth.vendor.example/authorize?state=one-time-secret"
    manager, client, gate = _manager(
        _config({"vendor": {"url": "https://mcp.vendor.example"}}),
        {"authorizationUrl": oauth_url},
    )

    assert manager.start_oauth_login("vendor") == oauth_url
    assert client.calls[-1] == Call(
        "mcpServer/oauth/login", {"name": "vendor", "timeoutSecs": 300}
    )
    assert oauth_url not in repr(manager.__dict__)
    assert gate.leases[0].released is True


@pytest.mark.parametrize(
    "authorization_url",
    [
        "http://auth.vendor.example/authorize",
        "https://auth.vendor.example/authorize#state=secret",
        "https://user:secret@auth.vendor.example/authorize",
        "https://localhost/authorize",
        "https://127.0.0.1/authorize",
    ],
)
def test_oauth_login_rejects_non_public_https_authorization_urls(
    authorization_url: str,
) -> None:
    manager, _client, gate = _manager(
        _config({"vendor": {"url": "https://mcp.vendor.example"}}),
        {"authorizationUrl": authorization_url},
    )

    with pytest.raises(McpProtocolError):
        manager.start_oauth_login("vendor")

    assert gate.leases[0].released is True


def test_callbacks_capture_only_safe_enums_and_decline_elicitations() -> None:
    manager, client, _gate = _manager()

    assert manager.elicitation_handler_registered is True
    startup = client.notifications["mcpServer/startupStatus/updated"]
    completed = client.notifications["mcpServer/oauthLogin/completed"]
    startup(
        SimpleNamespace(
            params={
                "name": "vendor",
                "status": "failed",
                "failureReason": "reauthenticationRequired",
                "error": "Bearer raw-secret /data/codex-home/auth.json",
            }
        )
    )
    completed(
        SimpleNamespace(
            params={"name": "vendor", "success": False, "error": "raw-secret"}
        )
    )

    assert manager._startup == {"vendor": ("failed", "reauthentication_required")}
    assert manager._oauth_completion == {"vendor": False}
    assert client.requests["mcpServer/elicitation/request"](object()) == {
        "action": "decline"
    }
    assert "secret" not in repr(manager._startup).lower()


def test_authenticated_create_and_oauth_are_unavailable_without_elicitation_handler() -> (
    None
):
    client = AppServerWithoutRequestHandler()
    manager = McpManager(client, GateDouble(), enabled=True)
    app = _route_app(manager)

    created = TestClient(app).post(
        "/mcp/servers",
        headers={"Authorization": "Bearer bridge-token"},
        json={"name": "vendor", "url": "https://mcp.vendor.example"},
    )
    oauth = TestClient(app).post(
        "/mcp/servers/vendor/oauth/login",
        headers={"Authorization": "Bearer bridge-token"},
    )

    assert manager.elicitation_handler_registered is False
    assert created.status_code == 503
    assert created.json() == {
        "detail": {"code": "mcp_elicitation_unavailable", "retryable": True}
    }
    assert oauth.status_code == 503
    assert oauth.json() == created.json()
    assert client.calls == []


def test_disabled_manager_blocks_authenticated_create_and_oauth_without_native_calls() -> (
    None
):
    manager, client, _gate = _manager(enabled=False)
    app = _route_app(manager)

    created = TestClient(app).post(
        "/mcp/servers",
        headers={"Authorization": "Bearer bridge-token"},
        json={"name": "vendor", "url": "https://mcp.vendor.example"},
    )
    oauth = TestClient(app).post(
        "/mcp/servers/vendor/oauth/login",
        headers={"Authorization": "Bearer bridge-token"},
    )
    removed = TestClient(app).delete(
        "/mcp/servers/vendor",
        headers={"Authorization": "Bearer bridge-token"},
    )
    listed = TestClient(app).get(
        "/mcp/servers",
        headers={"Authorization": "Bearer bridge-token"},
    )

    assert created.status_code == 503
    assert created.json() == {
        "detail": {
            "code": "mcp_disabled",
            "retryable": False,
            "message": "Enable MCP in the Codex Bridge App configuration and restart",
        }
    }
    assert oauth.status_code == 503
    assert oauth.json() == created.json()
    assert removed.status_code == 503
    assert removed.json() == created.json()
    assert listed.status_code == 503
    assert listed.json() == created.json()
    assert client.calls == []


def test_disabled_manager_removes_only_native_mcp_root_config() -> None:
    manager, client, gate = _manager(
        _config({"vendor": {"url": "https://mcp.vendor.example", "enabled": False}}),
        {
            "status": "ok",
            "version": "user-v2",
            "filePath": "/data/codex-home/config.toml",
        },
        {},
        enabled=False,
    )

    manager.disable_all_servers()

    assert client.calls[1] == Call(
        "config/batchWrite",
        {
            "edits": [
                {
                    "keyPath": "mcp_servers",
                    "mergeStrategy": "replace",
                    "value": None,
                }
            ],
            "expectedVersion": "user-v1",
            "reloadUserConfig": True,
        },
    )
    assert client.calls[2] == Call("config/mcpServer/reload", None)
    assert gate.leases[0].released is True


def test_disabled_manager_needs_no_user_layer_when_native_mcp_is_absent() -> None:
    manager, client, gate = _manager(
        {"config": {"model": "gpt-5.6-sol"}, "layers": [], "origins": {}},
        enabled=False,
    )

    manager.disable_all_servers()

    assert client.calls == [Call("config/read", {"includeLayers": True})]
    assert gate.leases[0].released is True


def test_masked_user_mcp_is_deleted_and_cannot_resurrect_when_later_enabled() -> None:
    client = LayeredConfigAppServer()
    gate = GateDouble()
    disabled = McpManager(client, gate, enabled=False)

    disabled.disable_all_servers()

    assert client.user_config == {"features": {"plugins": True}}
    assert client.calls[1].method == "config/batchWrite"
    client.override_mcp = False
    enabled = McpManager(client, gate, enabled=True)
    assert enabled.list_servers() == []


def test_enabled_startup_replaces_unsafe_user_mcp_before_activation() -> None:
    client = StartupSanitizingAppServer()
    manager = McpManager(client, GateDouble(), enabled=True)

    with pytest.raises(McpUnavailableError):
        manager.activate_validated_mcp_config()

    manager.sanitize_startup_servers()

    assert client.override_mcp is True
    assert client.user_config == {
        "mcp_servers": {
            "safe": {
                "url": "https://mcp.vendor.example/stream",
                "oauth_client_id": "public-client",
                "oauth_resource": "https://api.vendor.example",
            }
        },
        "features": {"plugins": True},
    }
    assert [call.method for call in client.calls] == [
        "config/read",
        "config/batchWrite",
        "config/mcpServer/reload",
    ]

    manager.activate_validated_mcp_config()

    assert client.activation_calls == 1
    views = manager.list_servers()
    assert len(views[0].pop("revision")) == 64
    assert views == [
        {
            "name": "safe",
            "transport": "streamable_http",
        "network": "public",
            "endpoint": "https://mcp.vendor.example/stream",
            "enabled": True,
            "auth": "unknown",
            "startup": "unknown",
            "tool_count": 0,
            "resource_count": 0,
        }
    ]


def test_enabled_activation_releases_manager_lock_before_restarting_client() -> None:
    client = StartupSanitizingAppServer()
    manager = McpManager(client, GateDouble(), enabled=True)
    manager.sanitize_startup_servers()
    probe_finished = Event()
    probe_acquired: list[bool] = []

    def activation() -> None:
        def probe() -> None:
            acquired = manager._lock.acquire(blocking=False)
            probe_acquired.append(acquired)
            if acquired:
                manager._lock.release()
            probe_finished.set()

        thread = Thread(target=probe)
        thread.start()
        thread.join(timeout=1)
        if not probe_finished.is_set() or probe_acquired != [True]:
            raise RuntimeError("manager lock held across activation")

    client.activate_validated_mcp_config = activation

    manager.activate_validated_mcp_config()

    assert probe_acquired == [True]


def test_enabled_startup_refuses_non_user_mcp_configuration() -> None:
    client = StartupSanitizingAppServer()
    client.non_user_mcp_servers = {"stdio": {"command": "sh", "args": ["-c", "id"]}}
    manager = McpManager(client, GateDouble(), enabled=True)

    with pytest.raises(McpProtocolError):
        manager.sanitize_startup_servers()
    with pytest.raises(McpUnavailableError):
        manager.activate_validated_mcp_config()

    assert client.override_mcp is True
    assert client.activation_calls == 0
    assert [call.method for call in client.calls] == ["config/read"]


def test_enabled_startup_without_a_user_layer_remains_masked() -> None:
    manager, client, _gate = _manager(
        {"config": {"mcp_servers": {}}, "layers": [], "origins": {}}
    )

    with pytest.raises(McpProtocolError):
        manager.sanitize_startup_servers()
    with pytest.raises(McpUnavailableError):
        manager.activate_validated_mcp_config()

    assert client.calls == [Call("config/read", {"includeLayers": True})]


def test_enabled_startup_requires_the_effective_mcp_root_to_be_masked() -> None:
    client = StartupSanitizingAppServer()
    client.override_mcp = False
    manager = McpManager(client, GateDouble(), enabled=True)

    with pytest.raises(McpProtocolError):
        manager.sanitize_startup_servers()
    with pytest.raises(McpUnavailableError):
        manager.activate_validated_mcp_config()

    assert client.activation_calls == 0
    assert [call.method for call in client.calls] == ["config/read"]


def test_enabled_startup_rejects_malformed_config_layers() -> None:
    manager, client, _gate = _manager(
        {"config": {"mcp_servers": {}}, "layers": [None], "origins": {}}
    )

    with pytest.raises(McpProtocolError):
        manager.sanitize_startup_servers()
    with pytest.raises(McpUnavailableError):
        manager.activate_validated_mcp_config()

    assert client.calls == [Call("config/read", {"includeLayers": True})]


def test_enabled_startup_refuses_profile_mcp_configuration() -> None:
    client = StartupSanitizingAppServer()
    client.profile_mcp_servers = {
        "profile": {"url": "https://mcp.profile.example/stream"}
    }
    manager = McpManager(client, GateDouble(), enabled=True)

    with pytest.raises(McpProtocolError):
        manager.sanitize_startup_servers()
    with pytest.raises(McpUnavailableError):
        manager.activate_validated_mcp_config()

    assert client.override_mcp is True
    assert client.activation_calls == 0
    assert [call.method for call in client.calls] == ["config/read"]


def test_mcp_endpoint_and_oauth_url_reject_private_dns_answers() -> None:
    def private_resolver(_host: str) -> tuple[str, ...]:
        return ("10.0.0.9",)

    manager, _client, _gate = _manager(_config(), resolver=private_resolver)
    with pytest.raises(McpValidationError):
        manager.create_server(name="vendor", url="https://mcp.vendor.example")

    manager, _client, _gate = _manager(
        _config({"vendor": {"url": "https://mcp.vendor.example"}}),
        {"authorizationUrl": "https://auth.vendor.example/authorize"},
        resolver=private_resolver,
    )
    with pytest.raises(McpProtocolError):
        manager.start_oauth_login("vendor")


def _route_app(manager: McpManager) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.auth_token = "bridge-token"
    app.state.storage = SimpleNamespace(runtime_profile="external_legacy")
    app.state.mcp_manager = manager
    return app


def test_route_never_leaks_invalid_endpoint_or_raw_oauth_url_in_errors() -> None:
    manager, _client, _gate = _manager()
    app = _route_app(manager)

    response = TestClient(app).post(
        "/mcp/servers",
        headers={"Authorization": "Bearer bridge-token"},
        json={"name": "vendor", "url": "https://user:secret@private.local/mcp"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": {"code": "mcp_request_invalid", "retryable": False}
    }
    assert "secret" not in response.text.lower()
    assert "private.local" not in response.text


def test_oauth_route_is_no_store_and_exposes_url_only_in_direct_response() -> None:
    raw_url = "https://auth.vendor.example/authorize?state=one-time-secret"
    manager, _client, _gate = _manager(
        _config({"vendor": {"url": "https://mcp.vendor.example"}}),
        {"authorizationUrl": raw_url},
    )
    app = _route_app(manager)

    response = TestClient(app).post(
        "/mcp/servers/vendor/oauth/login",
        headers={"Authorization": "Bearer bridge-token"},
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {"authorization_url": raw_url}
    assert raw_url not in repr(manager.__dict__)


def test_effective_public_defaults_do_not_hide_an_existing_server():
    manager, client, _gate = _manager(
        _config({"vendor": {"url": "https://mcp.vendor.example/stream", "enabled": True,
                            "environment_id": "local", "tool_timeout_sec": None}}),
        {"data": []},
    )
    assert manager.list_servers()[0]["name"] == "vendor"


def test_disabled_manager_refuses_an_unmasked_effective_server():
    manager, client, _gate = _manager(_config({"vendor": {"url": "https://mcp.vendor.example"}}), enabled=False)
    with pytest.raises(McpProtocolError):
        manager.disable_all_servers()
    assert len(client.calls) == 1


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="Private credential storage requires Linux")
async def test_static_credentials_never_enter_native_config_or_projection(tmp_path):
    relay = McpRelay(tmp_path / "private", resolver=lambda _: ("8.8.8.8",), local_enabled=False)
    await relay.start()
    secret = "synthetic-static-token"
    try:
        manager, client, gate = _manager(_config(), {"status":"ok", "version":"user-v2"}, {})
        manager._relay = relay
        created = manager.create_server(name="vendor", url="https://mcp.example.com/private-path", authentication={"mode":"bearer", "token":secret}, auth_acknowledged=True)
        assert created["auth"] == "bearer" and created["credential_configured"] is True
        assert created["network"] == "public" and created["endpoint"] == "https://mcp.example.com"
        assert secret not in str(created) and secret not in str(client.calls)
        binding = client.calls[1].params["edits"][0]["value"]
        assert "private-path" not in str(binding)
        effective = {**binding, "enabled":True, "environment_id":"local", "tool_timeout_sec":None}
        client.responses.extend([_config({"vendor":effective}), {"data":[]}])
        assert manager.list_servers()[0]["auth"] == "bearer"
        client.responses.append(_config({"vendor":effective}))
        with pytest.raises(McpValidationError):
            manager.start_oauth_login("vendor")
        client.responses.extend([_config({"vendor":effective}), {}])
        assert manager.replace_credential("vendor", {"mode":"headers", "headers":[{"name":"X-Api-Key", "value":"synthetic-replacement"}]}, acknowledged=True) == {"name":"vendor", "auth":"headers", "credential_configured":True}
        assert secret not in str(client.calls) and "synthetic-replacement" not in str(client.calls)
        client.responses.extend([_config({"vendor":effective}), {}])
        assert manager.replace_credential("vendor", remove=True)["credential_configured"] is False
        assert not relay.metadata("vendor")["credential_configured"]
        assert all(lease.released for lease in gate.leases)
    finally:
        await relay.close()


@pytest.mark.parametrize("fields", [
    {"authentication":{"mode":"bearer", "token":"synthetic-secret"}},
    {"authentication":{"mode":"bearer", "token":"synthetic-secret"}, "auth_acknowledged":True, "oauth_client_id":"client"},
    {"authentication":{"mode":"headers", "headers":[]}, "auth_acknowledged":True},
    {"auth_acknowledged":True},
])
def test_authentication_requires_explicit_nonconflicting_choice(fields):
    manager, client, _ = _manager()
    with pytest.raises(McpValidationError):
        manager.create_server(name="vendor", url="https://mcp.example.com", **fields)
    assert not client.calls
