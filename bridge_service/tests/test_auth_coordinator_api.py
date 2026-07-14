from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import codex_bridge_service.app as app_module
from codex_bridge_service.account import AppServerAccountProbe
from codex_bridge_service.app import create_app
from codex_bridge_service.codex_app_server import AppServerUnavailableError
from codex_bridge_service.models import (
    BridgeDiagnosticsRecord,
    CodexAuthStatusRecord,
    CodexModelCatalogRecord,
    RuntimeProfile,
)
from codex_bridge_service.limits import AppServerLimitsProbe


AUTHORIZATION = {
    "Authorization": "Bearer secret",
    "X-Codex-Bridge-Api": "1",
}


def _status(
    state: str = "ok",
    *,
    revision: int = 1,
    busy: bool = False,
    auth_required: bool = False,
    message: str = "ChatGPT sign-in is ready.",
    verification_uri: str | None = None,
    user_code: str | None = None,
) -> CodexAuthStatusRecord:
    return CodexAuthStatusRecord.model_validate(
        {
            "state": state,
            "revision": revision,
            "busy": busy,
            "auth_required": auth_required,
            "auth_mode": "chatgpt" if state == "ok" else None,
            "plan_type": "plus" if state == "ok" else None,
            "message": message,
            "verification_uri": verification_uri,
            "login_url": verification_uri,
            "user_code": user_code,
        }
    )


class LifecycleAppServer:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def start(self) -> None:
        self.events.append("app_server.start")

    def close(self) -> None:
        self.events.append("app_server.close")


class StructuredAuthCoordinator:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events
        self.calls: list[str] = []
        self.failures: dict[str, BaseException] = {}

    def _raise_if_scripted(self, operation: str) -> None:
        failure = self.failures.get(operation)
        if failure is not None:
            raise failure

    def start(self) -> CodexAuthStatusRecord:
        if self.events is not None:
            self.events.append("coordinator.start")
        self._raise_if_scripted("start")
        return _status()

    def close(self) -> None:
        if self.events is not None:
            self.events.append("coordinator.close")
        self._raise_if_scripted("close")

    def status(self) -> CodexAuthStatusRecord:
        self.calls.append("status")
        self._raise_if_scripted("status")
        return _status()

    def start_device_login(self) -> CodexAuthStatusRecord:
        self.calls.append("start_device_login")
        self._raise_if_scripted("start_device_login")
        return _status(
            "login_running",
            revision=2,
            busy=True,
            auth_required=True,
            message="Open the verification URL and enter the one-time code.",
            verification_uri="https://auth.openai.com/codex/device",
            user_code="ABCD-EFGH",
        )

    def cancel_login(self) -> CodexAuthStatusRecord:
        self.calls.append("cancel_login")
        self._raise_if_scripted("cancel_login")
        return _status(
            "logged_out",
            revision=3,
            auth_required=True,
            message="ChatGPT sign-in was cancelled.",
        )

    def logout(self) -> CodexAuthStatusRecord:
        self.calls.append("logout")
        self._raise_if_scripted("logout")
        return _status(
            "logged_out",
            revision=4,
            auth_required=True,
            message="Signed out of ChatGPT.",
        )


class LegacyAuthManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def status(self, *, last_error: str | None = None) -> CodexAuthStatusRecord:
        self.calls.append(("status", last_error))
        return _status()

    def start_device_login(
        self,
        *,
        force_logout: bool = False,
    ) -> CodexAuthStatusRecord:
        self.calls.append(("start_device_login", force_logout))
        return _status(
            "login_running",
            busy=True,
            auth_required=True,
            verification_uri="https://auth.openai.com/codex/device",
            user_code="ABCD-EFGH",
        )

    def logout(self) -> CodexAuthStatusRecord:
        self.calls.append(("logout", None))
        return _status("logged_out", auth_required=True)


class SafeDiagnosticsProbe:
    def probe(self) -> BridgeDiagnosticsRecord:
        return BridgeDiagnosticsRecord()


class SafeModelCatalogProbe:
    def probe(self) -> CodexModelCatalogRecord:
        return CodexModelCatalogRecord()


def _ha_app(
    tmp_path: Path,
    *,
    app_server: LifecycleAppServer | None = None,
    coordinator: StructuredAuthCoordinator | None = None,
    coordinator_factory: Callable[[object], StructuredAuthCoordinator] | None = None,
):
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir(exist_ok=True)
    resolved_server = app_server or LifecycleAppServer([])
    resolved_coordinator = coordinator or StructuredAuthCoordinator()
    resolved_factory = coordinator_factory or (lambda _client: resolved_coordinator)
    return create_app(
        root_path=tmp_path / "state",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
        app_server_factory=lambda: resolved_server,
        auth_coordinator_factory=resolved_factory,
        runner_factory=lambda _storage: object(),
        diagnostics_probe=SafeDiagnosticsProbe(),
        model_catalog_probe=SafeModelCatalogProbe(),
    )


def test_home_assistant_owns_coordinator_on_the_single_app_server_in_lifespan_order(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    app_server = LifecycleAppServer(events)
    coordinator = StructuredAuthCoordinator(events)
    coordinator_clients: list[object] = []

    def coordinator_factory(client: object) -> StructuredAuthCoordinator:
        coordinator_clients.append(client)
        return coordinator

    app = _ha_app(
        tmp_path,
        app_server=app_server,
        coordinator=coordinator,
        coordinator_factory=coordinator_factory,
    )

    assert coordinator_clients == [app_server]
    assert app.state.codex_app_server is app_server
    assert app.state.auth_coordinator is coordinator
    assert events == []

    with TestClient(app):
        assert events == ["app_server.start", "coordinator.start"]

    assert events == [
        "app_server.start",
        "coordinator.start",
        "coordinator.close",
        "app_server.close",
    ]


def test_coordinator_start_failure_closes_both_owners_in_reverse_order(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    app_server = LifecycleAppServer(events)
    coordinator = StructuredAuthCoordinator(events)
    coordinator.failures["start"] = RuntimeError("synthetic coordinator start failure")
    app = _ha_app(tmp_path, app_server=app_server, coordinator=coordinator)

    with pytest.raises(RuntimeError, match="synthetic coordinator start failure"):
        with TestClient(app):
            pass

    assert events == [
        "app_server.start",
        "coordinator.start",
        "coordinator.close",
        "app_server.close",
    ]


def test_coordinator_close_failure_still_closes_the_app_server(tmp_path: Path) -> None:
    events: list[str] = []
    app_server = LifecycleAppServer(events)
    coordinator = StructuredAuthCoordinator(events)
    coordinator.failures["close"] = RuntimeError("synthetic coordinator close failure")
    app = _ha_app(tmp_path, app_server=app_server, coordinator=coordinator)

    with pytest.raises(RuntimeError, match="synthetic coordinator close failure"):
        with TestClient(app):
            pass

    assert events == [
        "app_server.start",
        "coordinator.start",
        "coordinator.close",
        "app_server.close",
    ]


def test_home_assistant_never_constructs_or_retains_legacy_auth_token_owners(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed: list[str] = []

    def forbidden_owner(*_args: object, **_kwargs: object) -> object:
        constructed.append("legacy_owner")
        raise AssertionError("HA must not construct a legacy credential owner")

    monkeypatch.setattr(app_module, "CodexAuthManager", forbidden_owner)
    monkeypatch.setattr(app_module, "CodexAccountProbe", forbidden_owner)
    monkeypatch.setattr(app_module, "CodexLimitsProbe", forbidden_owner)

    app = _ha_app(tmp_path)

    assert constructed == []
    assert app.state.auth_manager is None
    assert isinstance(app.state.account_probe, AppServerAccountProbe)
    assert isinstance(app.state.storage.limits_probe, AppServerLimitsProbe)
    with TestClient(app):
        pass
    assert constructed == []


def test_external_profile_keeps_injected_legacy_manager_without_structured_owners(
    tmp_path: Path,
) -> None:
    legacy = LegacyAuthManager()

    def forbidden_app_server() -> LifecycleAppServer:
        raise AssertionError("external profile must not construct an app server")

    def forbidden_coordinator(_client: object) -> StructuredAuthCoordinator:
        raise AssertionError("external profile must not construct an auth coordinator")

    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        auth_manager=legacy,
        app_server_factory=forbidden_app_server,
        auth_coordinator_factory=forbidden_coordinator,
        diagnostics_probe=SafeDiagnosticsProbe(),
    )

    assert app.state.codex_app_server is None
    assert app.state.auth_coordinator is None
    assert app.state.auth_manager is legacy

    with TestClient(app) as client:
        status_response = client.get("/auth/status", headers=AUTHORIZATION)
        login_response = client.post(
            "/auth/device-login",
            headers=AUTHORIZATION,
            json={},
        )
        logout_response = client.post("/auth/logout", headers=AUTHORIZATION)

    assert status_response.status_code == 200
    assert login_response.status_code == 202
    assert logout_response.status_code == 200
    assert legacy.calls == [
        ("status", None),
        ("start_device_login", False),
        ("logout", None),
    ]


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("get", "/auth/status", None),
        ("post", "/auth/device-login", {"force_logout": True}),
        ("post", "/auth/device-login/cancel", None),
        ("post", "/auth/logout", None),
    ],
)
def test_structured_auth_routes_require_the_bridge_token(
    tmp_path: Path,
    method: str,
    path: str,
    json: dict[str, Any] | None,
) -> None:
    app = _ha_app(tmp_path)

    with TestClient(app) as client:
        response = client.request(method, path, json=json)

    assert response.status_code == 401


def test_home_assistant_auth_routes_dispatch_to_the_structured_coordinator(
    tmp_path: Path,
) -> None:
    coordinator = StructuredAuthCoordinator()
    app = _ha_app(tmp_path, coordinator=coordinator)

    with TestClient(app) as client:
        status_response = client.get("/auth/status", headers=AUTHORIZATION)
        bridge_status_response = client.get("/status", headers=AUTHORIZATION)
        forced_login = client.post(
            "/auth/device-login",
            headers=AUTHORIZATION,
            json={"force_logout": True},
        )
        default_login = client.post(
            "/auth/device-login",
            headers=AUTHORIZATION,
            json={},
        )
        unforced_login = client.post(
            "/auth/device-login",
            headers=AUTHORIZATION,
            json={"force_logout": False},
        )
        cancel_response = client.post(
            "/auth/device-login/cancel",
            headers=AUTHORIZATION,
        )
        logout_response = client.post("/auth/logout", headers=AUTHORIZATION)

    assert status_response.status_code == 200
    assert status_response.json()["state"] == "ok"
    assert bridge_status_response.status_code == 200
    assert bridge_status_response.json()["auth"] == status_response.json()
    assert forced_login.status_code == 202
    assert default_login.status_code == 202
    assert unforced_login.status_code == 202
    assert forced_login.json()["busy"] is True
    assert forced_login.json()["user_code"] == "ABCD-EFGH"
    assert cancel_response.status_code == 200
    assert cancel_response.json()["state"] == "logged_out"
    assert logout_response.status_code == 200
    assert logout_response.json()["state"] == "logged_out"
    assert coordinator.calls == [
        "status",
        "status",
        "start_device_login",
        "start_device_login",
        "start_device_login",
        "cancel_login",
        "logout",
    ]


def test_auth_operation_conflict_is_a_typed_safe_409(tmp_path: Path) -> None:
    from codex_bridge_service.auth_coordinator import AuthOperationConflictError

    raw_secret = "bearer reusable-secret belonging to private@example.test"
    coordinator = StructuredAuthCoordinator()
    coordinator.failures["logout"] = _with_secret_cause(
        AuthOperationConflictError(),
        raw_secret,
    )
    app = _ha_app(tmp_path, coordinator=coordinator)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/auth/logout", headers=AUTHORIZATION)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "auth_operation_conflict"
    assert detail["retryable"] is True
    assert raw_secret not in response.text
    assert "reusable-secret" not in response.text
    assert "private@example.test" not in response.text


@pytest.mark.parametrize(
    "failure_factory",
    [
        pytest.param(
            lambda secret: _with_secret_cause(AppServerUnavailableError(), secret),
            id="app-server-unavailable",
        ),
        pytest.param(
            lambda secret: _closed_error(secret),
            id="coordinator-closed",
        ),
    ],
)
def test_closed_or_unavailable_auth_returns_a_safe_retryable_response(
    tmp_path: Path,
    failure_factory: Callable[[str], BaseException],
) -> None:
    raw_secret = "refresh-token=reusable-secret private@example.test"
    coordinator = StructuredAuthCoordinator()
    coordinator.failures["start_device_login"] = failure_factory(raw_secret)
    app = _ha_app(tmp_path, coordinator=coordinator)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/auth/device-login",
            headers=AUTHORIZATION,
            json={"force_logout": True},
        )

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "auth_unavailable"
    assert detail["retryable"] is True
    assert raw_secret not in response.text
    assert "reusable-secret" not in response.text
    assert "private@example.test" not in response.text


def _closed_error(message: str) -> BaseException:
    from codex_bridge_service.auth_coordinator import AuthCoordinatorClosedError

    return _with_secret_cause(AuthCoordinatorClosedError(), message)


def _with_secret_cause(error: BaseException, secret: str) -> BaseException:
    error.__cause__ = RuntimeError(secret)
    return error
