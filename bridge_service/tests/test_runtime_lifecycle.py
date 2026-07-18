from __future__ import annotations

import threading
import time
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from codex_bridge_service.app import create_app
from codex_bridge_service.automations import AutomationValidationError
from codex_bridge_service.build_info import BuildInfo
from codex_bridge_service.codex_app_server import (
    AppServerNotification,
    AppServerUnavailableError,
)
from codex_bridge_service.limits import AppServerLimitsProbe
from codex_bridge_service.model_catalog import AppServerModelCatalogProbe
from codex_bridge_service.models import CodexAuthStatusRecord, RunMode, RuntimeProfile
from codex_bridge_service.runtime_broker import RuntimeAuthenticationRequiredError


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _wait_until(predicate, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met before the timeout")


class _SharedClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.requests: list[tuple[str, Any]] = []
        self.ready = True
        self.generation = 1
        self.server_version: str | None = None
        self.authenticated = True
        self.account_email = "first-account@example.test"
        self.fail_catalogue = False
        self.model = "gpt-5.6-sol"
        self.effort = "ultra"
        self.limit_used_percent = 10.0
        self.turn_in_progress = False
        self.fail_mcp_cleanup = False
        self.mcp_masked = True
        self.mcp_activation_calls = 0
        self.user_mcp_servers: dict[str, object] = {
            "stale": {"url": "https://stale-mcp.example/stream"}
        }
        self._thread_number = 0
        self._turn_number = 0
        self.notification_handlers: dict[str, Any] = {}

    def start(self) -> None:
        self.calls.append("start")

    def close(self) -> None:
        self.calls.append("close")

    def register_notification_handler(self, method, handler) -> None:
        self.notification_handlers[method] = handler

    def register_request_handler(self, _method, _handler) -> None:
        pass

    def abort_generation(self, expected_generation: int) -> bool:
        if expected_generation != self.generation:
            return False
        self.generation += 1
        return True

    def request(self, method, params=None, **_kwargs):
        self.calls.append(method)
        self.requests.append((method, deepcopy(params)))
        if method == "config/read":
            if self.fail_catalogue and params != {"includeLayers": True}:
                raise RuntimeError(
                    "Bearer private-secret C:\\Users\\owner\\.codex\\auth.json"
                )
            return {
                "config": {
                    "model": self.model,
                    "model_reasoning_effort": self.effort,
                    "mcp_servers": (
                        {} if self.mcp_masked else deepcopy(self.user_mcp_servers)
                    ),
                    "features": {"plugins": True},
                },
                "layers": [
                    {
                        "name": {
                            "type": "user",
                            "file": "/data/codex-home/config.toml",
                        },
                        "version": "user-v1",
                        "config": {
                            "mcp_servers": deepcopy(self.user_mcp_servers),
                            "features": {"plugins": True},
                        },
                    }
                ],
                "origins": {},
            }
        if method == "config/batchWrite":
            if self.fail_mcp_cleanup:
                raise RuntimeError("Bearer private-secret cleanup failure")
            edits = params["edits"]
            if edits[0]["keyPath"] == "mcp_servers":
                value = edits[0]["value"]
                self.user_mcp_servers = {} if value is None else deepcopy(value)
            return {
                "status": "ok",
                "version": "user-v2",
                "filePath": "/data/codex-home/config.toml",
            }
        if method == "config/mcpServer/reload":
            return {}
        if method == "model/list":
            if params and params.get("cursor") == "next":
                return {
                    "data": [
                        {
                            "model": "gpt-5.4-mini",
                            "displayName": "GPT-5.4 mini",
                            "supportedReasoningEfforts": ["medium"],
                        }
                    ]
                }
            return {
                "data": [
                    {
                        "model": self.model,
                        "displayName": self.model,
                        "supportedReasoningEfforts": [self.effort],
                        "defaultReasoningEffort": self.effort,
                    }
                ],
                "nextCursor": "next",
            }
        if method == "account/read":
            return {
                "account": (
                    {
                        "type": "chatgpt",
                        "email": self.account_email,
                        "planType": "plus",
                    }
                    if self.authenticated
                    else None
                )
            }
        if method == "account/rateLimits/read":
            return {
                "rateLimits": {
                    "primary": {"usedPercent": self.limit_used_percent},
                    "rateLimitReachedType": None,
                }
            }
        if method in {"thread/start", "thread/resume"}:
            self._thread_number += 1
            thread_id = (
                params["threadId"]
                if method == "thread/resume"
                else f"codex-thread-{self._thread_number}"
            )
            permission_profile = params["config"]["default_permissions"]
            sandbox = (
                {"type": "readOnly", "networkAccess": False}
                if permission_profile == "ha_observe"
                else {
                    "type": "workspaceWrite",
                    "networkAccess": False,
                    "writableRoots": [params["cwd"]],
                    "excludeSlashTmp": True,
                    "excludeTmpdirEnvVar": True,
                }
            )
            return {
                "thread": {
                    "id": thread_id,
                    "preview": "",
                    "ephemeral": False,
                    "modelProvider": "openai",
                    "createdAt": 1_783_936_800,
                    "updatedAt": 1_783_936_800,
                    "status": {"type": "idle"},
                    "cwd": params["cwd"],
                    "cliVersion": "0.144.3",
                    "source": "appServer",
                    "turns": [],
                    "sessionId": f"session-{thread_id}",
                },
                "model": params["model"],
                "modelProvider": "openai",
                "cwd": params["cwd"],
                "approvalPolicy": params["approvalPolicy"],
                "approvalsReviewer": params["approvalsReviewer"],
                "sandbox": sandbox,
                "activePermissionProfile": {
                    "id": permission_profile,
                    "extends": None,
                },
            }
        if method == "turn/start":
            self._turn_number += 1
            return {
                "turn": {
                    "id": f"codex-turn-{self._turn_number}",
                    "items": [],
                    "status": ("inProgress" if self.turn_in_progress else "completed"),
                }
            }
        if method == "turn/interrupt":
            return {}
        raise AssertionError(f"unexpected shared-client request: {method}")

    def activate_validated_mcp_config(self) -> None:
        self.mcp_activation_calls += 1
        self.mcp_masked = False


class _Auth:
    def __init__(
        self,
        *,
        required: bool = False,
        state: str = "ok",
        events: list[str] | None = None,
        fail_start: bool = False,
    ) -> None:
        self.required = required
        self.state = state
        self.events = events
        self.fail_start = fail_start

    def start(self) -> None:
        if self.events is not None:
            self.events.append("auth.start")
        if self.fail_start:
            raise RuntimeError("auth startup")

    def close(self) -> None:
        if self.events is not None:
            self.events.append("auth.close")

    def status(self) -> CodexAuthStatusRecord:
        return CodexAuthStatusRecord(
            state=self.state,
            auth_required=self.required,
        )


class _LifecycleClient(_SharedClient):
    def __init__(self, events: list[str], *, fail_start: bool = False) -> None:
        super().__init__()
        self.events = events
        self.fail_start = fail_start

    def start(self) -> None:
        self.events.append("client.start")
        if self.fail_start:
            raise RuntimeError("client startup")

    def close(self) -> None:
        self.events.append("client.close")


class _UnavailableClient(_SharedClient):
    def __init__(self) -> None:
        super().__init__()
        self.ready = False

    def start(self) -> None:
        self.calls.append("start")
        raise AppServerUnavailableError()


class _Runner:
    def __init__(
        self,
        events: list[str],
        *,
        fail_start: bool = False,
    ) -> None:
        self.events = events
        self.fail_start = fail_start

    def start(self) -> None:
        self.events.append("runner.start")
        if self.fail_start:
            raise RuntimeError("runner startup")

    def close(self) -> None:
        self.events.append("runner.close")


class _RestartProjectionRunner:
    """Model one recovered run publishing its terminal thread projection."""

    def __init__(self, storage, thread_id: str, provider_thread_id: str) -> None:
        self.storage = storage
        self.thread_id = thread_id
        self.provider_thread_id = provider_thread_id

    def start(self) -> None:
        record = self.storage.load_thread(self.thread_id)
        record.codex_thread_id = self.provider_thread_id
        record.status = "error"
        record.last_error = "The Codex runtime restarted before the turn completed."
        self.storage.save_thread(record)

    def close(self) -> None:
        pass


def _ha_app(tmp_path: Path, client: _SharedClient, **kwargs):
    return create_app(
        root_path=tmp_path / "data",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=_workspace(tmp_path),
        app_server_factory=lambda: client,
        sandbox_ready=True,
        initialize_special_projects=True,
        **kwargs,
    )


def _seed_blocked_thread(app, *, name: str):
    storage = app.state.storage
    original_profile = storage.runtime_profile
    assert storage.workspace_root is not None
    workspace_name = name.lower().replace(" ", "-")
    try:
        # These tests assert the pre-run readiness gate. On Windows, secure HA
        # dir_fd workspace mutation is intentionally unavailable, so seed an
        # inert record through the legacy storage path and restore HA mode
        # before exercising the endpoint.
        storage.runtime_profile = RuntimeProfile.EXTERNAL_LEGACY
        project = storage.create_project(
            name=name,
            root_path=str(storage.workspace_root / workspace_name),
        )
        thread = storage.create_thread(
            title=name,
            mode=RunMode.EDIT,
            project_id=project.project_id,
        )
        # HOME_ASSISTANT records are portable paths beneath workspace_root.
        # Normalize the temporary legacy seed before restoring the real profile.
        project.root_path = workspace_name
        thread.workspace_path = workspace_name
        storage.save_project(project)
        storage.save_thread(thread)
        return thread
    finally:
        storage.runtime_profile = original_profile


def test_main_ha_composition_defers_catalogue_and_turns_to_shared_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    monkeypatch.setenv("CODEX_BRIDGE_AUTH_TOKEN", "x" * 32)
    monkeypatch.setenv("CODEX_BRIDGE_ROOT_PATH", str(tmp_path / "data"))
    monkeypatch.setenv("CODEX_BRIDGE_RUNTIME_PROFILE", "home_assistant")
    monkeypatch.setenv("CODEX_BRIDGE_WORKSPACE_ROOT", str(workspace_root))
    main_module = importlib.import_module("codex_bridge_service.main")
    captured: dict[str, Any] = {}
    sentinel = object()

    def capture_create_app(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(main_module, "create_app", capture_create_app)

    assert main_module.build_app() is sentinel
    assert captured["runtime_profile"] is RuntimeProfile.HOME_ASSISTANT
    assert captured["model_catalog_probe"] is None
    assert captured["limits_probe"] is None
    assert captured["account_probe"] is None
    assert captured["runner_factory"] is None
    assert captured["enable_mcp"] is False
    assert captured["model_discovery_timeout_seconds"] == 10.0
    assert captured["model_cache_ttl_seconds"] == 600.0


def test_ha_startup_rebinds_only_provider_threads_when_account_changes(
    tmp_path: Path,
) -> None:
    client = _SharedClient()
    first_app = _ha_app(tmp_path, client)
    thread = _seed_blocked_thread(first_app, name="Account-neutral history")
    record = first_app.state.storage.load_thread(thread.thread_id)
    record.codex_thread_id = "provider-thread-before-owner-tracking"
    first_app.state.storage.save_thread(record)

    with TestClient(first_app):
        migrated = first_app.state.storage.load_thread(thread.thread_id)

    assert migrated.thread_id == thread.thread_id
    assert migrated.codex_thread_id is None

    migrated.codex_thread_id = "provider-thread-current-account"
    first_app.state.storage.save_thread(migrated)
    same_account_app = _ha_app(tmp_path, client)
    with TestClient(same_account_app):
        unchanged = same_account_app.state.storage.load_thread(thread.thread_id)

    assert unchanged.codex_thread_id == "provider-thread-current-account"

    client.account_email = "second-account@example.test"
    changed_account_app = _ha_app(tmp_path, client)
    with TestClient(changed_account_app):
        rebound = changed_account_app.state.storage.load_thread(thread.thread_id)

    assert rebound.thread_id == thread.thread_id
    assert rebound.codex_thread_id is None


def test_changed_account_detaches_provider_projection_restored_during_startup(
    tmp_path: Path,
) -> None:
    client = _SharedClient()
    first_app = _ha_app(tmp_path, client)
    thread = _seed_blocked_thread(first_app, name="Interrupted account history")

    with TestClient(first_app):
        pass

    client.account_email = "second-account@example.test"
    stale_provider_thread_id = "provider-thread-from-first-account"
    changed_account_app = _ha_app(
        tmp_path,
        client,
        runner_factory=lambda storage: _RestartProjectionRunner(
            storage,
            thread.thread_id,
            stale_provider_thread_id,
        ),
    )

    with TestClient(changed_account_app):
        rebound = changed_account_app.state.storage.load_thread(thread.thread_id)

    assert rebound.thread_id == thread.thread_id
    assert rebound.codex_thread_id is None
    assert rebound.status == "idle"
    assert rebound.last_error is None


@pytest.mark.skipif(
    os.name == "nt",
    reason="secure Home Assistant workspace turns require POSIX dir_fd support",
)
def test_ha_lifecycle_uses_one_shared_client_for_catalogue_account_limits_and_turns(
    tmp_path: Path,
) -> None:
    client = _SharedClient()
    app = _ha_app(tmp_path, client)
    project = app.state.storage.create_project(name="Shared runtime")
    thread = app.state.storage.create_thread(
        title="Shared turn",
        mode=RunMode.EDIT,
        project_id=project.project_id,
    )

    with TestClient(app) as http:
        assert app.state.codex_app_server is client
        assert app.state.runner.app_server is client
        assert app.state.model_catalog_probe.probe().default_model == client.model
        assert app.state.account_probe.probe().auth_mode == "chatgpt"
        assert app.state.storage.limits_probe.probe() is not None

        response = http.post(
            f"/threads/{thread.thread_id}/prompts",
            headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"},
            json={"prompt": "Use the shared runtime", "client_request_id": "shared-1"},
        )
        assert response.status_code == 202
        _wait_until(lambda: "turn/start" in client.calls)

    assert client.calls[0] == "start"
    assert client.calls[-1] == "close"
    assert {
        "config/read",
        "model/list",
        "account/read",
        "account/rateLimits/read",
        "thread/start",
        "turn/start",
    } <= set(client.calls)
    thread_request = next(
        params for method, params in client.requests if method == "thread/start"
    )
    turn_request = next(
        params for method, params in client.requests if method == "turn/start"
    )
    assert "sandbox" not in thread_request
    # The broker explicitly resets the managed web-search setting on every
    # thread start.  This prevents a prior live/disabled override from
    # persisting in Codex's sticky thread configuration when the next turn
    # does not specify an override.
    assert thread_request["config"] == {
        "default_permissions": "ha_bridge",
        "web_search": "cached",
    }
    assert "sandboxPolicy" not in turn_request
    _wait_until(
        lambda: (
            not any(
                thread.name.startswith("CodexRuntime-")
                for thread in threading.enumerate()
            )
        )
    )


def test_shared_catalogue_paginates_and_invalidates_on_generation_change(
    tmp_path: Path,
) -> None:
    client = _SharedClient()
    app = _ha_app(tmp_path, client)
    probe = app.state.model_catalog_probe

    first = probe.probe()
    model_calls = client.calls.count("model/list")

    assert {item.model for item in first.models} == {
        "gpt-5.6-sol",
        "gpt-5.4-mini",
    }
    assert probe.probe() is first
    assert client.calls.count("model/list") == model_calls

    client.generation = 2
    assert probe.probe() is not first
    assert client.calls.count("model/list") == model_calls * 2


def test_shared_catalogue_can_be_invalidated_without_generation_change() -> None:
    client = _SharedClient()
    probe = AppServerModelCatalogProbe(client, cache_ttl_seconds=600)

    first = probe.probe()
    model_calls = client.calls.count("model/list")
    client.model = "gpt-5.6-terra"
    client.effort = "max"

    probe.invalidate()
    refreshed = probe.probe()

    assert refreshed is not first
    assert refreshed.default_model == "gpt-5.6-terra"
    assert refreshed.default_thinking_level == "max"
    assert client.calls.count("model/list") == model_calls * 2


def test_account_entitlement_change_invalidates_shared_catalogue(
    tmp_path: Path,
) -> None:
    client = _SharedClient()
    client.authenticated = False
    client.model = "gpt-5.5"
    client.effort = "medium"
    app = _ha_app(tmp_path, client)

    with TestClient(app):
        probe = app.state.model_catalog_probe
        signed_out_catalogue = probe.probe()
        model_calls = client.calls.count("model/list")
        client.authenticated = True
        client.model = "gpt-5.6-terra"
        client.effort = "max"
        notification = AppServerNotification(
            method="account/updated",
            params={"authMode": "chatgpt", "planType": "pro"},
            generation=client.generation,
        )

        client.notification_handlers["account/updated"](notification)
        refreshed = probe.probe()
        client.notification_handlers["account/updated"](notification)
        cached = probe.probe()

    assert signed_out_catalogue.default_model == "gpt-5.5"
    assert refreshed.default_model == "gpt-5.6-terra"
    assert refreshed.default_thinking_level == "max"
    assert cached is refreshed
    assert client.calls.count("model/list") == model_calls * 2


def test_first_status_after_auth_recovery_uses_the_refreshed_catalogue(
    tmp_path: Path,
) -> None:
    client = _SharedClient()
    client.model = "gpt-5.5"
    client.effort = "medium"
    probe = AppServerModelCatalogProbe(client, cache_ttl_seconds=600)
    cached = probe.probe()

    class RecoveringAuth(_Auth):
        def status(self) -> CodexAuthStatusRecord:
            client.model = "gpt-5.6-terra"
            client.effort = "max"
            probe.invalidate()
            return CodexAuthStatusRecord(
                state="ok",
                auth_required=False,
                auth_mode="chatgpt",
                plan_type="pro",
            )

    app = _ha_app(
        tmp_path,
        client,
        model_catalog_probe=probe,
        auth_coordinator_factory=lambda _client: RecoveringAuth(),
    )

    with TestClient(app) as http:
        response = http.get(
            "/status",
            headers={
                "Authorization": "Bearer secret",
                "X-Codex-Bridge-Api": "1",
            },
        )
        direct = app.state.storage.load_project("prj_direct")

    assert cached.default_model == "gpt-5.5"
    assert response.status_code == 200
    assert response.json()["model_catalog"]["default_model"] == "gpt-5.6-terra"
    assert response.json()["model_catalog"]["default_thinking_level"] == "max"
    assert direct.default_model == "gpt-5.6-terra"
    assert direct.default_thinking_level == "max"


def test_shared_catalogue_redacts_transport_failures(tmp_path: Path) -> None:
    client = _SharedClient()
    client.fail_catalogue = True
    app = _ha_app(tmp_path, client)

    catalogue = app.state.model_catalog_probe.probe()
    serialized = catalogue.model_dump_json()

    assert catalogue.source == "fallback"
    assert catalogue.stale is True
    assert "private-secret" not in serialized
    assert "owner" not in serialized
    assert "auth.json" not in serialized


def test_shared_catalogue_does_not_hide_programming_errors(tmp_path: Path) -> None:
    class BrokenClient(_SharedClient):
        def request(self, method, params=None, **kwargs):
            if method == "config/read":
                raise AssertionError("broken adapter contract")
            return super().request(method, params, **kwargs)

    app = _ha_app(tmp_path, BrokenClient())

    with pytest.raises(AssertionError, match="broken adapter contract"):
        app.state.model_catalog_probe.probe()


def test_rate_limit_cache_invalidates_with_app_server_generation() -> None:
    client = _SharedClient()
    probe = AppServerLimitsProbe(client, min_fetch_interval_seconds=600)

    first = probe.probe()
    client.limit_used_percent = 80.0
    cached = probe.probe()
    client.generation = 2
    refreshed = probe.probe()

    assert first is not None and first.primary is not None
    assert cached is not None and cached.primary is not None
    assert refreshed is not None and refreshed.primary is not None
    assert first.primary.used_percent == 10.0
    assert cached.primary.used_percent == 10.0
    assert refreshed.primary.used_percent == 80.0


@pytest.mark.parametrize(
    ("ready", "sandbox_ready", "runtime_version", "build_version", "reason"),
    [
        (False, True, None, None, "runtime_unavailable"),
        (True, False, None, None, "sandbox_unavailable"),
        (True, True, "9.9.9", "0.144.3", "runtime_version_mismatch"),
    ],
)
def test_ha_readiness_fatal_causes_are_redacted_and_block_prompts(
    tmp_path: Path,
    ready: bool,
    sandbox_ready: bool,
    runtime_version: str | None,
    build_version: str | None,
    reason: str,
) -> None:
    client = _SharedClient()
    client.ready = ready
    client.server_version = runtime_version
    app = create_app(
        root_path=tmp_path / "data",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=_workspace(tmp_path),
        app_server_factory=lambda: client,
        sandbox_ready=sandbox_ready,
        build_info=BuildInfo(codex_version=build_version),
    )
    thread = _seed_blocked_thread(app, name="Fatal gate")
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}

    with TestClient(app) as http:
        readiness = http.get("/ready", headers=headers)
        prompt = http.post(
            f"/threads/{thread.thread_id}/prompts",
            headers=headers,
            json={"prompt": "must not run", "client_request_id": "fatal-1"},
        )

    assert readiness.status_code == 200
    assert readiness.json()["readiness"] == {
        "state": "fatal",
        "reasons": [reason],
    }
    assert prompt.status_code == 503
    assert "9.9.9" not in readiness.text
    assert "9.9.9" not in prompt.text
    assert "turn/start" not in client.calls


def test_ha_readiness_reports_ready_auth_required_and_degraded_catalogue(
    tmp_path: Path,
) -> None:
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}

    ready_client = _SharedClient()
    ready_app = _ha_app(tmp_path / "ready", ready_client)
    with TestClient(ready_app) as http:
        assert http.get("/ready", headers=headers).json()["readiness"] == {
            "state": "ready",
            "reasons": [],
        }

    auth_client = _SharedClient()
    auth_app = _ha_app(
        tmp_path / "auth",
        auth_client,
        auth_coordinator_factory=lambda _client: _Auth(required=True),
    )
    with TestClient(auth_app) as http:
        assert http.get("/ready", headers=headers).json()["readiness"] == {
            "state": "auth_required",
            "reasons": ["authentication_required"],
        }

    degraded_client = _SharedClient()
    degraded_client.fail_catalogue = True
    degraded_app = _ha_app(tmp_path / "degraded", degraded_client)
    with TestClient(degraded_app) as http:
        assert http.get("/ready", headers=headers).json()["readiness"] == {
            "state": "degraded_catalogue",
            "reasons": ["catalogue_stale"],
        }


@pytest.mark.parametrize(
    ("auth_state", "auth_required"),
    [
        ("ok", True),
        ("checking", False),
        ("logout_running", False),
    ],
)
def test_native_broker_final_admission_uses_authoritative_ready_auth_status(
    tmp_path: Path,
    auth_state: str,
    auth_required: bool,
) -> None:
    client = _SharedClient()
    auth = _Auth(required=auth_required, state=auth_state)
    app = _ha_app(
        tmp_path,
        client,
        auth_coordinator_factory=lambda _client: auth,
    )
    thread = _seed_blocked_thread(app, name="Auth fence")

    with TestClient(app):
        with pytest.raises(RuntimeAuthenticationRequiredError):
            app.state.runner.submit_prompt(
                thread.thread_id,
                "Must not reach Codex",
                client_request_id="auth-fence-direct",
            )

    assert "thread/start" not in client.calls
    assert "thread/resume" not in client.calls
    assert "turn/start" not in client.calls
    assert app.state.storage.load_thread(thread.thread_id).status == "idle"


def test_account_update_blocks_broker_and_automation_before_local_mutation(
    tmp_path: Path,
) -> None:
    class BlockingAccountClient(_SharedClient):
        def __init__(self) -> None:
            super().__init__()
            self.block_account_read = False
            self.account_read_entered = threading.Event()
            self.account_read_release = threading.Event()

        def request(self, method, params=None, **kwargs):
            if method == "account/read" and self.block_account_read:
                self.block_account_read = False
                self.account_read_entered.set()
                if not self.account_read_release.wait(10):
                    raise AssertionError("blocked account/read was not released")
            return super().request(method, params, **kwargs)

    client = BlockingAccountClient()
    app = _ha_app(tmp_path, client)
    thread = _seed_blocked_thread(app, name="Account update fence")
    errors: list[BaseException] = []

    with TestClient(app):
        client.account_email = "second-account@example.test"
        client.block_account_read = True
        notification = AppServerNotification(
            method="account/updated",
            params={"authMode": "chatgpt", "planType": "pro"},
            generation=client.generation,
        )

        def emit_update() -> None:
            try:
                client.notification_handlers["account/updated"](notification)
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        worker = threading.Thread(target=emit_update, daemon=True)
        worker.start()
        assert client.account_read_entered.wait(3)
        checking = app.state.auth_coordinator.status()
        assert checking.state == "checking"
        assert checking.auth_required is True

        provider_calls = client.calls.count("thread/start") + client.calls.count(
            "thread/resume"
        )
        with pytest.raises(RuntimeAuthenticationRequiredError):
            app.state.runner.submit_prompt(
                thread.thread_id,
                "Must not reach Codex during account reconciliation",
                client_request_id="account-update-fence",
            )

        automation = app.state.automations.create(
            {
                "name": "Account update automation fence",
                "prompt": "Must not mutate the target while auth is checking.",
                "target": {
                    "kind": "continue_thread",
                    "thread_id": thread.thread_id,
                },
                "mode": "observe",
                "schedule": {
                    "kind": "rrule",
                    "rule": "RRULE:FREQ=DAILY",
                    "start_at": "2026-07-18T01:00:00Z",
                    "timezone": "UTC",
                },
            }
        )
        claim = app.state.automations.run_now(automation["automation_id"])
        with pytest.raises(AutomationValidationError, match="not ready"):
            app.state.automation_dispatch(claim)

        unchanged = app.state.storage.load_thread(thread.thread_id)
        assert unchanged.mode is RunMode.EDIT
        assert unchanged.status == "idle"
        assert client.calls.count("thread/start") + client.calls.count(
            "thread/resume"
        ) == provider_calls
        assert "turn/start" not in client.calls

        client.account_read_release.set()
        worker.join(3)
        assert not worker.is_alive()
        assert errors == []
        assert app.state.auth_coordinator.status().state == "ok"


def test_initial_app_server_failure_keeps_authenticated_fatal_readiness_alive(
    tmp_path: Path,
) -> None:
    client = _UnavailableClient()
    # A partial/custom lifecycle must not override the recorded startup failure
    # by leaving a stale ready flag behind.
    client.ready = True
    app = _ha_app(tmp_path, client)

    with TestClient(app) as http:
        response = http.get(
            "/ready",
            headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"},
        )

    assert response.status_code == 200
    assert response.json()["readiness"] == {
        "state": "fatal",
        "reasons": ["runtime_unavailable"],
    }
    assert app.state.runtime_startup_failed is True
    assert client.calls == ["start", "close"]


def test_auth_required_blocks_new_turn_until_generation_reconciles(
    tmp_path: Path,
) -> None:
    client = _SharedClient()
    app = _ha_app(tmp_path, client)
    thread = _seed_blocked_thread(app, name="Authentication gate")
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}

    with TestClient(app) as http:
        initial_reads = client.calls.count("account/read")
        client.authenticated = False
        client.generation = 2
        response = http.post(
            f"/threads/{thread.thread_id}/prompts",
            headers=headers,
            json={"prompt": "must sign in", "client_request_id": "auth-1"},
        )

    assert response.status_code == 409
    assert client.calls.count("account/read") > initial_reads
    assert "turn/start" not in client.calls


@pytest.mark.skipif(
    os.name == "nt",
    reason="secure Home Assistant workspace mutation requires POSIX dir_fd support",
)
def test_first_direct_chat_recovers_shared_catalogue_defaults(tmp_path: Path) -> None:
    client = _SharedClient()
    client.fail_catalogue = True
    app = _ha_app(tmp_path, client)
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}

    with TestClient(app) as http:
        stale_project = http.get("/projects", headers=headers).json()[0]
        client.fail_catalogue = False
        recovered = http.post(
            "/threads",
            headers=headers,
            json={
                "title": "First recovered direct chat",
                "project_id": "prj_direct",
            },
        )

    assert stale_project["defaults_origin"] == "fallback"
    assert recovered.status_code == 201
    thread = recovered.json()
    assert thread["model_override"] is None
    assert thread["thinking_override"] is None
    assert thread["effective_model"] == client.model
    assert thread["effective_thinking_level"] == client.effort
    assert app.state.storage.load_project("prj_direct").defaults_origin.value == "codex"


@pytest.mark.skipif(
    os.name == "nt",
    reason="secure Home Assistant workspace turns require POSIX dir_fd support",
)
def test_generation_change_interrupts_active_and_queued_shared_turns(
    tmp_path: Path,
) -> None:
    client = _SharedClient()
    client.turn_in_progress = True
    app = _ha_app(tmp_path, client)
    project = app.state.storage.create_project(name="Generation recovery")
    first = app.state.storage.create_thread(
        title="Active",
        mode=RunMode.EDIT,
        project_id=project.project_id,
    )
    second = app.state.storage.create_thread(
        title="Queued",
        mode=RunMode.EDIT,
        project_id=project.project_id,
    )
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}

    with TestClient(app) as http:
        active = http.post(
            f"/threads/{first.thread_id}/prompts",
            headers=headers,
            json={"prompt": "active", "client_request_id": "generation-active"},
        ).json()
        _wait_until(lambda: "turn/start" in client.calls)
        queued = http.post(
            f"/threads/{second.thread_id}/prompts",
            headers=headers,
            json={"prompt": "queued", "client_request_id": "generation-queued"},
        ).json()
        client.generation = 2
        _wait_until(
            lambda: (
                app.state.storage.load_thread(first.thread_id).status == "error"
                and app.state.storage.load_thread(second.thread_id).status == "error"
            )
        )
        assert active["run_id"] != queued["run_id"]
        assert app.state.runner.runtime_snapshot().active_turns == 0
        assert app.state.runner.runtime_snapshot().queued_prompts == 0
        event_types = {
            event.event_type
            for event in app.state.storage.list_thread_events(first.thread_id)
            + app.state.storage.list_thread_events(second.thread_id)
        }
        assert "run.interrupted" in event_types
        assert "run.queue_cleared" in event_types


@pytest.mark.parametrize("failure", ["client", "auth", "runner"])
def test_lifecycle_startup_failure_closes_owned_resources_in_reverse_order(
    tmp_path: Path,
    failure: str,
) -> None:
    events: list[str] = []
    client = _LifecycleClient(events, fail_start=failure == "client")
    auth = _Auth(events=events, fail_start=failure == "auth")
    runner = _Runner(events, fail_start=failure == "runner")
    app = create_app(
        root_path=tmp_path / "data",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=_workspace(tmp_path),
        app_server_factory=lambda: client,
        auth_coordinator_factory=lambda _client: auth,
        runner_factory=lambda _storage: runner,
        sandbox_ready=True,
    )

    with pytest.raises(RuntimeError, match="startup"):
        with TestClient(app):
            pass

    assert events[-3:] == ["auth.close", "runner.close", "client.close"]


def test_lifecycle_normal_shutdown_closes_resources_in_reverse_order(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    client = _LifecycleClient(events)
    auth = _Auth(events=events)
    runner = _Runner(events)
    app = create_app(
        root_path=tmp_path / "data",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=_workspace(tmp_path),
        app_server_factory=lambda: client,
        auth_coordinator_factory=lambda _client: auth,
        runner_factory=lambda _storage: runner,
        sandbox_ready=True,
    )

    with TestClient(app):
        pass

    assert events == [
        "client.start",
        "runner.start",
        "auth.start",
        "auth.close",
        "runner.close",
        "client.close",
    ]


def test_disabled_mcp_cleans_only_native_mcp_root_before_runtime_start(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    client = _LifecycleClient(events)
    auth = _Auth(events=events)
    runner = _Runner(events)
    app = create_app(
        root_path=tmp_path / "data",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=_workspace(tmp_path),
        app_server_factory=lambda: client,
        auth_coordinator_factory=lambda _client: auth,
        runner_factory=lambda _storage: runner,
        sandbox_ready=True,
    )

    with TestClient(app):
        assert "mcp_admin_v1" not in app.state.feature_capabilities

    assert client.requests[:3] == [
        ("config/read", {"includeLayers": True}),
        (
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
        ),
        ("config/mcpServer/reload", None),
    ]
    assert events[:3] == ["client.start", "runner.start", "auth.start"]


def test_enabled_mcp_sanitizes_before_activating_runtime_generation(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    client = _LifecycleClient(events)
    client.user_mcp_servers = {
        "safe": {"url": "https://mcp.vendor.example/stream"},
        "stdio": {"command": "sh", "args": ["-c", "id"]},
        "bearer": {
            "url": "https://mcp.bearer.example/stream",
            "bearer_token_env_var": "TOKEN",
        },
        "private": {"url": "https://localhost/mcp"},
    }
    auth = _Auth(events=events)
    runner = _Runner(events)
    app = create_app(
        root_path=tmp_path / "data",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=_workspace(tmp_path),
        app_server_factory=lambda: client,
        auth_coordinator_factory=lambda _client: auth,
        runner_factory=lambda _storage: runner,
        sandbox_ready=True,
        enable_mcp=True,
    )

    with TestClient(app):
        assert client.mcp_activation_calls == 1
        assert client.mcp_masked is False

    assert client.requests[:3] == [
        ("config/read", {"includeLayers": True}),
        (
            "config/batchWrite",
            {
                "edits": [
                    {
                        "keyPath": "mcp_servers",
                        "mergeStrategy": "replace",
                        "value": {"safe": {"url": "https://mcp.vendor.example/stream"}},
                    }
                ],
                "expectedVersion": "user-v1",
                "reloadUserConfig": True,
            },
        ),
        ("config/mcpServer/reload", None),
    ]
    assert client.user_mcp_servers == {
        "safe": {"url": "https://mcp.vendor.example/stream"}
    }
    assert events[:3] == ["client.start", "runner.start", "auth.start"]


def test_disabled_mcp_cleanup_failure_keeps_runtime_non_ready(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    client = _LifecycleClient(events)
    client.fail_mcp_cleanup = True
    auth = _Auth(events=events)
    runner = _Runner(events)
    app = create_app(
        root_path=tmp_path / "data",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=_workspace(tmp_path),
        app_server_factory=lambda: client,
        auth_coordinator_factory=lambda _client: auth,
        runner_factory=lambda _storage: runner,
        sandbox_ready=True,
    )

    with TestClient(app) as http:
        response = http.get(
            "/ready",
            headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"},
        )

    assert response.status_code == 200
    assert response.json()["readiness"] == {
        "state": "fatal",
        "reasons": ["runtime_unavailable"],
    }
    assert app.state.runtime_startup_failed is True
    assert "auth.start" not in events
    assert "runner.start" not in events


def test_enabled_mcp_sanitize_write_failure_stays_masked_and_stops_runtime(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    client = _LifecycleClient(events)
    client.fail_mcp_cleanup = True
    auth = _Auth(events=events)
    runner = _Runner(events)
    app = create_app(
        root_path=tmp_path / "data",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=_workspace(tmp_path),
        app_server_factory=lambda: client,
        auth_coordinator_factory=lambda _client: auth,
        runner_factory=lambda _storage: runner,
        sandbox_ready=True,
        enable_mcp=True,
    )

    with TestClient(app) as http:
        response = http.get(
            "/ready",
            headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"},
        )

    assert response.status_code == 200
    assert response.json()["readiness"] == {
        "state": "fatal",
        "reasons": ["runtime_unavailable"],
    }
    assert client.mcp_masked is True
    assert client.mcp_activation_calls == 0
    assert "auth.start" not in events
    assert "runner.start" not in events
