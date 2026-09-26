from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from codex_bridge_service.app import create_app
from codex_bridge_service.models import (
    CodexAccountRecord,
    CodexAuthStatusRecord,
    CodexModelCatalogRecord,
    RunMode,
    RunRecord,
    RuntimeProfile,
)


AUTHORIZATION = {
    "Authorization": "Bearer secret",
    "X-Codex-Bridge-Api": "1",
}


class _ModelCatalogProbe:
    def probe(self, *, refresh_stale: bool = False) -> CodexModelCatalogRecord:
        del refresh_stale
        return CodexModelCatalogRecord()


class _AccountProbe:
    def probe(self) -> CodexAccountRecord:
        return CodexAccountRecord()


class _Runner:
    def __init__(self, storage) -> None:
        self.storage = storage
        self.submissions: list[object] = []

    def submit_prompt(self, *args, **kwargs):
        self.submissions.append((args, kwargs))
        raise AssertionError("unsupported web search must not dispatch")


def test_external_prompt_rejects_native_web_search_override_before_dispatch(tmp_path) -> None:
    runner: _Runner | None = None

    def runner_factory(storage):
        nonlocal runner
        runner = _Runner(storage)
        return runner

    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        runner_factory=runner_factory,
        model_catalog_probe=_ModelCatalogProbe(),
        account_probe=_AccountProbe(),
    )
    project = app.state.storage.create_project(
        name="Native search rejection",
        root_path=str(tmp_path / "workspace"),
    )
    thread = app.state.storage.create_thread(
        title="Native search rejection",
        project_id=project.project_id,
        mode=RunMode.OBSERVE,
    )

    response = TestClient(app).post(
        f"/threads/{thread.thread_id}/prompts",
        headers=AUTHORIZATION,
        json={"prompt": "Find current information", "web_search": "live"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "capabilities_unavailable",
        "retryable": False,
    }
    assert runner is not None
    assert runner.submissions == []


def test_status_projects_only_bounded_provider_capabilities(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        model_catalog_probe=_ModelCatalogProbe(),
        account_probe=_AccountProbe(),
    )
    app.state.capabilities_manager = SimpleNamespace(
        provider_capabilities=lambda: {
            "image_generation": True,
            "web_search": None,
            "namespace_tools": False,
            "private_provider_name": "must not leak",
        }
    )

    response = TestClient(app).get("/status", headers=AUTHORIZATION)

    assert response.status_code == 200
    assert response.json()["provider_capabilities"] == {
        "image_generation": True,
        "web_search": None,
        "namespace_tools": False,
    }
    assert "private_provider_name" not in response.text


def test_create_app_advertises_native_plan_when_runner_supports_it(tmp_path) -> None:
    class AppServer:
        ready = True
        server_version = None
        enable_experimental_api = True
        supports_collaboration_mode = True

    class AuthCoordinator:
        def status(self):
            return CodexAuthStatusRecord(state="ok", auth_required=False)

    class Runner:
        supports_plan_mode = True

        def submit_prompt(self, thread_id, _prompt, **kwargs):
            return RunRecord(
                run_id="plan-run",
                thread_id=thread_id,
                status="running",
                collaboration_mode=kwargs["collaboration_mode"],
            )

    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    runner = Runner()
    app = create_app(
        root_path=tmp_path / "state",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
        app_server_factory=AppServer,
        auth_coordinator_factory=lambda _client: AuthCoordinator(),
        runner_factory=lambda _storage: runner,
        sandbox_ready=True,
    )
    assert "plan_mode_v1" in app.state.feature_capabilities

    client = TestClient(app)
    readiness = client.get("/ready", headers=AUTHORIZATION)
    assert readiness.status_code == 200
    assert "plan_mode_v1" in readiness.json()["capabilities"]

    thread_id = "native-plan-thread"
    response = client.post(
        f"/threads/{thread_id}/prompts",
        headers=AUTHORIZATION,
        json={"prompt": "Review the approach", "collaboration_mode": "plan"},
    )
    assert response.status_code == 202
    assert response.json()["collaboration_mode"] == "plan"


@pytest.mark.parametrize(
    ("experimental_api", "supports_collaboration_mode"),
    [(False, True), (True, False)],
)
def test_create_app_fails_closed_when_native_plan_contract_is_missing(
    tmp_path, experimental_api: bool, supports_collaboration_mode: bool
) -> None:
    class AuthCoordinator:
        def status(self):
            return CodexAuthStatusRecord(state="ok", auth_required=False)

    class Runner:
        supports_plan_mode = True
        submitted = False

        def submit_prompt(self, *_args, **_kwargs):
            self.submitted = True
            raise AssertionError("unsupported Plan request must not dispatch")

    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    app_server = SimpleNamespace(
        ready=True,
        server_version=None,
        enable_experimental_api=experimental_api,
        supports_collaboration_mode=supports_collaboration_mode,
    )
    runner = Runner()
    app = create_app(
        root_path=tmp_path / "state",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
        app_server_factory=lambda: app_server,
        auth_coordinator_factory=lambda _client: AuthCoordinator(),
        runner_factory=lambda _storage: runner,
        sandbox_ready=True,
    )

    assert "plan_mode_v1" not in app.state.feature_capabilities
    response = TestClient(app).post(
        "/threads/native-plan-thread/prompts",
        headers=AUTHORIZATION,
        json={"prompt": "Review the approach", "collaboration_mode": "plan"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["capability"] == "plan_mode_v1"
    assert runner.submitted is False
