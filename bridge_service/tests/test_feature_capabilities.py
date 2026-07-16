from types import SimpleNamespace

from fastapi.testclient import TestClient

from codex_bridge_service.app import create_app
from codex_bridge_service.models import CodexAccountRecord, CodexModelCatalogRecord, RunMode


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
