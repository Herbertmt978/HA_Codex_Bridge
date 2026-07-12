import json
import time
from threading import Event

import pytest
from fastapi.testclient import TestClient

from codex_bridge_service.app import create_app
from codex_bridge_service.models import (
    DEFAULT_MODEL,
    DEFAULT_THINKING_LEVEL,
    BridgeDiagnosticsRecord,
    CodexAccountRecord,
    CodexAuthStatusRecord,
    CodexModelCatalogRecord,
    CodexModelRecord,
    RunMode,
    RunRecord,
)
from codex_bridge_service.runner import BridgeRunner
from codex_bridge_service.storage import BridgeStorage


class FakeRunner:
    def __init__(self, storage) -> None:
        self.storage = storage
        self.calls: list[tuple[str, str]] = []

    def submit_prompt(self, thread_id: str, prompt: str) -> RunRecord:
        self.calls.append((thread_id, prompt))
        record = self.storage.load_thread(thread_id)
        record.status = "running"
        record.active_run_id = "run_fake123"
        self.storage.save_thread(record)
        self.storage.append_thread_event(
            thread_id=thread_id,
            event_type="message.created",
            payload={
                "run_id": "run_fake123",
                "role": "user",
                "text": prompt,
            },
        )
        return RunRecord(
            run_id="run_fake123",
            thread_id=thread_id,
            status="running",
        )

    def cancel_run(self, thread_id: str) -> RunRecord:
        record = self.storage.load_thread(thread_id)
        run_id = record.active_run_id or "run_fake123"
        record.status = "idle"
        record.active_run_id = None
        record.last_error = "Run cancelled"
        self.storage.save_thread(record)
        self.storage.append_thread_event(
            thread_id=thread_id,
            event_type="run.cancelled",
            payload={"run_id": run_id},
        )
        return RunRecord(run_id=run_id, thread_id=thread_id, status="cancelled")


class FakeAccountProbe:
    def probe(self) -> CodexAccountRecord:
        return CodexAccountRecord(
            available=True,
            auth_mode="chatgpt",
            email="person@example.com",
            name="Person Example",
            account_id="acc_123",
            plan_type="pro",
            organization_title="Personal",
            updated_at="2026-05-09T10:00:00Z",
        )


class FakeDiagnosticsProbe:
    def probe(self) -> BridgeDiagnosticsRecord:
        return BridgeDiagnosticsRecord(
            bridge_version="0.4.test",
            last_error="failed to connect to websocket: HTTP error: 401 Unauthorized",
        )


class FakeModelCatalogProbe:
    def probe(self, *, refresh_stale: bool = False) -> CodexModelCatalogRecord:
        return CodexModelCatalogRecord(
            source="codex-app-server",
            models=[
                CodexModelRecord(
                    model="gpt-5.6-sol",
                    display_name="GPT-5.6-Sol",
                    is_default=True,
                    default_thinking_level="medium",
                    thinking_levels=["low", "medium", "high", "xhigh", "max", "ultra"],
                    input_modalities=["text", "image"],
                ),
                CodexModelRecord(
                    model="gpt-5.6-luna",
                    display_name="GPT-5.6-Luna",
                    default_thinking_level="medium",
                    thinking_levels=["low", "medium", "high", "xhigh", "max"],
                    input_modalities=["text", "image"],
                ),
                CodexModelRecord(
                    model="gpt-5.4-mini",
                    display_name="GPT-5.4-Mini",
                    default_thinking_level="medium",
                    thinking_levels=["low", "medium", "high"],
                    input_modalities=["text", "image"],
                ),
            ],
            default_model="gpt-5.6-sol",
            default_thinking_level="ultra",
            configured_model="gpt-5.6-sol",
            configured_thinking_level="ultra",
            refreshed_at="2026-07-12T00:00:00Z",
        )


class FakeFallbackModelCatalogProbe:
    def probe(self, *, refresh_stale: bool = False) -> CodexModelCatalogRecord:
        return CodexModelCatalogRecord(
            source="fallback",
            models=[
                CodexModelRecord(
                    model=DEFAULT_MODEL,
                    display_name=DEFAULT_MODEL,
                    is_default=True,
                    default_thinking_level=DEFAULT_THINKING_LEVEL,
                    thinking_levels=[DEFAULT_THINKING_LEVEL],
                )
            ],
            default_model=DEFAULT_MODEL,
            default_thinking_level=DEFAULT_THINKING_LEVEL,
            stale=True,
            error="temporary startup discovery failure",
        )


class RecoveringModelCatalogProbe:
    def __init__(self, *, fallback_calls: int = 3) -> None:
        self.calls = 0
        self.fallback_calls = fallback_calls
        self.refresh_stale_requests: list[bool] = []

    def probe(self, *, refresh_stale: bool = False) -> CodexModelCatalogRecord:
        self.calls += 1
        self.refresh_stale_requests.append(refresh_stale)
        if self.calls <= self.fallback_calls:
            return FakeFallbackModelCatalogProbe().probe()
        return FakeModelCatalogProbe().probe()


class FakeAuthManager:
    def __init__(self) -> None:
        self.started = False
        self.logged_out = False

    def status(self, *, last_error: str | None = None) -> CodexAuthStatusRecord:
        if last_error:
            return CodexAuthStatusRecord(
                state="expired",
                auth_required=True,
                message="Codex login expired on the VM. Start a new VM sign-in from Home Assistant.",
            )
        return CodexAuthStatusRecord(state="ok", message="Codex login is ready.")

    def start_device_login(self, *, force_logout: bool = True) -> CodexAuthStatusRecord:
        self.started = True
        return CodexAuthStatusRecord(
            state="login_running",
            auth_required=True,
            message="Open the verification URL and enter the code.",
            verification_uri="https://chatgpt.com/activate",
            user_code="ABCD-EFGH",
        )

    def logout(self) -> CodexAuthStatusRecord:
        self.logged_out = True
        return CodexAuthStatusRecord(state="logged_out", auth_required=True, message="Logged out.")


def test_health_project_create_and_status_require_token(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        account_probe=FakeAccountProbe(),
        model_catalog_probe=FakeModelCatalogProbe(),
    )
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 401
    assert client.post("/projects", json={"name": "No token", "root_path": str(tmp_path / "nope")}).status_code == 401
    assert client.get("/status").status_code == 401

    response = client.post(
        "/projects",
        headers={"Authorization": "Bearer secret"},
        json={
            "name": "With token",
            "root_path": str(tmp_path / "projects" / "with-token"),
            "default_model": DEFAULT_MODEL,
            "default_thinking_level": DEFAULT_THINKING_LEVEL,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    saved_path = tmp_path / "projects" / f"{payload['project_id']}.json"
    saved_payload = json.loads(saved_path.read_text(encoding="utf-8"))

    assert payload["name"] == "With token"
    assert saved_path.exists()
    assert saved_payload["project_id"] == payload["project_id"]
    assert saved_payload["root_path"] == payload["root_path"]

    ready_response = client.get(
        "/ready",
        headers={"Authorization": "Bearer secret"},
    )
    assert ready_response.status_code == 200
    assert ready_response.json() == {"status": "ok"}

    status_response = client.get(
        "/status",
        headers={"Authorization": "Bearer secret"},
    )

    assert status_response.status_code == 200
    status = status_response.json()
    assert status["models"] == ["gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.4-mini"]
    assert status["thinking_levels"] == ["low", "medium", "high", "xhigh", "max", "ultra"]
    assert status["model_catalog"]["default_model"] == "gpt-5.6-sol"
    assert status["model_catalog"]["configured_thinking_level"] == "ultra"
    assert status["account"]["email"] == "person@example.com"
    assert status["account"]["plan_type"] == "pro"


def test_project_without_explicit_settings_uses_configured_codex_defaults(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        model_catalog_probe=FakeModelCatalogProbe(),
    )
    client = TestClient(app)

    response = client.post(
        "/projects",
        headers={"Authorization": "Bearer secret"},
        json={"name": "Latest Codex defaults"},
    )

    assert response.status_code == 201
    assert response.json()["default_model"] == "gpt-5.6-sol"
    assert response.json()["default_thinking_level"] == "ultra"


def test_startup_does_not_seed_special_projects_from_fallback_catalog(tmp_path) -> None:
    create_app(
        root_path=tmp_path,
        auth_token="secret",
        model_catalog_probe=FakeFallbackModelCatalogProbe(),
        initialize_special_projects=True,
    )

    assert not (tmp_path / "projects" / "prj_direct.json").exists()


def test_startup_seeds_special_projects_from_fresh_catalog(tmp_path) -> None:
    create_app(
        root_path=tmp_path,
        auth_token="secret",
        model_catalog_probe=FakeModelCatalogProbe(),
        initialize_special_projects=True,
    )

    direct = json.loads(
        (tmp_path / "projects" / "prj_direct.json").read_text(encoding="utf-8")
    )
    assert direct["default_model"] == "gpt-5.6-sol"
    assert direct["default_thinking_level"] == "ultra"


def test_special_project_provisional_defaults_recover_with_fresh_catalog(tmp_path) -> None:
    probe = RecoveringModelCatalogProbe()
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        model_catalog_probe=probe,
        initialize_special_projects=True,
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}

    stale_direct = client.get("/projects", headers=headers).json()[0]
    stale_thread = client.post(
        "/threads",
        headers=headers,
        json={"title": "Created during discovery outage"},
    ).json()
    fresh_direct = client.get("/projects", headers=headers).json()[0]
    preserved_thread = client.get(
        f"/threads/{stale_thread['thread_id']}",
        headers=headers,
    ).json()

    assert stale_direct["default_model"] == DEFAULT_MODEL
    assert stale_direct["default_thinking_level"] == DEFAULT_THINKING_LEVEL
    assert stale_direct["defaults_origin"] == "fallback"
    assert fresh_direct["default_model"] == "gpt-5.6-sol"
    assert fresh_direct["default_thinking_level"] == "ultra"
    assert fresh_direct["defaults_origin"] == "codex"
    assert preserved_thread["model_override"] == DEFAULT_MODEL
    assert preserved_thread["thinking_override"] == DEFAULT_THINKING_LEVEL
    assert preserved_thread["effective_model"] == DEFAULT_MODEL
    assert preserved_thread["effective_thinking_level"] == DEFAULT_THINKING_LEVEL
    assert probe.refresh_stale_requests == [False, False, True, False]


@pytest.mark.parametrize(
    ("project_id", "ensure_method"),
    [
        ("prj_direct", "ensure_direct_project"),
        ("prj_imported", "ensure_imported_project"),
    ],
    ids=("direct", "imported"),
)
def test_existing_special_project_reconciles_before_first_post_recovery_chat(
    tmp_path,
    project_id: str,
    ensure_method: str,
) -> None:
    initial_storage = BridgeStorage(root_path=tmp_path)
    special_project = getattr(initial_storage, ensure_method)(
        default_model=DEFAULT_MODEL,
        default_thinking_level=DEFAULT_THINKING_LEVEL,
        defaults_provisional=True,
    )
    stale_thread = initial_storage.create_thread(
        title="Created during discovery outage",
        project_id=special_project.project_id,
        mode=RunMode.FULL_AUTO,
    )
    probe = RecoveringModelCatalogProbe(fallback_calls=1)
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        model_catalog_probe=probe,
        initialize_special_projects=True,
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}

    recovered_response = client.post(
        "/threads",
        headers=headers,
        json={
            "title": "First chat after recovery",
            "project_id": project_id,
        },
    )
    assert recovered_response.status_code == 201
    recovered_thread = recovered_response.json()
    preserved_thread = client.get(
        f"/threads/{stale_thread.thread_id}",
        headers=headers,
    ).json()
    special_project = json.loads(
        (tmp_path / "projects" / f"{project_id}.json").read_text(encoding="utf-8")
    )

    assert probe.calls == 2
    assert probe.refresh_stale_requests == [False, True]
    assert special_project["default_model"] == "gpt-5.6-sol"
    assert special_project["default_thinking_level"] == "ultra"
    assert special_project["defaults_origin"] == "codex"
    assert recovered_thread["model_override"] is None
    assert recovered_thread["thinking_override"] is None
    assert recovered_thread["effective_model"] == "gpt-5.6-sol"
    assert recovered_thread["effective_thinking_level"] == "ultra"
    assert preserved_thread["model_override"] == DEFAULT_MODEL
    assert preserved_thread["thinking_override"] == DEFAULT_THINKING_LEVEL


def test_ordinary_project_thread_does_not_probe_catalog_without_overrides(tmp_path) -> None:
    probe = RecoveringModelCatalogProbe(fallback_calls=0)
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        model_catalog_probe=probe,
    )
    project = app.state.storage.create_project(name="Ordinary project")
    client = TestClient(app)

    response = client.post(
        "/threads",
        headers={"Authorization": "Bearer secret"},
        json={"title": "No catalogue needed", "project_id": project.project_id},
    )

    assert response.status_code == 201
    assert probe.calls == 0


def test_legacy_special_defaults_defer_until_catalog_recovers(tmp_path) -> None:
    initial_storage = BridgeStorage(root_path=tmp_path)
    direct = initial_storage.ensure_direct_project()
    existing_thread = initial_storage.create_thread(
        title="Existing 0.5.0 chat",
        project_id=direct.project_id,
        mode=RunMode.FULL_AUTO,
    )
    project_path = tmp_path / "projects" / "prj_direct.json"
    legacy_payload = json.loads(project_path.read_text(encoding="utf-8"))
    legacy_payload.pop("defaults_origin")
    project_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        model_catalog_probe=RecoveringModelCatalogProbe(fallback_calls=1),
        initialize_special_projects=True,
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}

    recovered_direct = client.get("/projects", headers=headers).json()[0]
    preserved_thread = client.get(
        f"/threads/{existing_thread.thread_id}",
        headers=headers,
    ).json()

    assert recovered_direct["default_model"] == "gpt-5.6-sol"
    assert recovered_direct["default_thinking_level"] == "ultra"
    assert recovered_direct["defaults_origin"] == "codex"
    assert preserved_thread["model_override"] == DEFAULT_MODEL
    assert preserved_thread["thinking_override"] == DEFAULT_THINKING_LEVEL
    assert preserved_thread["effective_model"] == DEFAULT_MODEL
    assert preserved_thread["effective_thinking_level"] == DEFAULT_THINKING_LEVEL


def test_project_with_only_model_uses_that_models_default_thinking_level(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        model_catalog_probe=FakeModelCatalogProbe(),
    )
    client = TestClient(app)

    response = client.post(
        "/projects",
        headers={"Authorization": "Bearer secret"},
        json={"name": "Mini project", "default_model": "gpt-5.4-mini"},
    )

    assert response.status_code == 201
    assert response.json()["default_model"] == "gpt-5.4-mini"
    assert response.json()["default_thinking_level"] == "medium"


def test_project_rejects_reasoning_level_not_supported_by_model(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        model_catalog_probe=FakeModelCatalogProbe(),
    )
    client = TestClient(app)

    response = client.post(
        "/projects",
        headers={"Authorization": "Bearer secret"},
        json={
            "name": "Invalid mini project",
            "default_model": "gpt-5.4-mini",
            "default_thinking_level": "ultra",
        },
    )

    assert response.status_code == 400
    assert "not supported" in response.json()["detail"]


def test_project_rejects_blank_model_defaults(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        model_catalog_probe=FakeModelCatalogProbe(),
    )
    client = TestClient(app)

    response = client.post(
        "/projects",
        headers={"Authorization": "Bearer secret"},
        json={"name": "Blank model", "default_model": ""},
    )

    assert response.status_code == 422


def test_project_model_update_repairs_incompatible_existing_thinking_level(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        model_catalog_probe=FakeModelCatalogProbe(),
    )
    client = TestClient(app)
    created = client.post(
        "/projects",
        headers={"Authorization": "Bearer secret"},
        json={
            "name": "Switch models",
            "default_model": "gpt-5.6-sol",
            "default_thinking_level": "ultra",
        },
    )

    response = client.patch(
        f"/projects/{created.json()['project_id']}",
        headers={"Authorization": "Bearer secret"},
        json={"default_model": "gpt-5.4-mini"},
    )

    assert response.status_code == 200
    assert response.json()["default_model"] == "gpt-5.4-mini"
    assert response.json()["default_thinking_level"] == "medium"


def test_project_update_rejects_blank_model(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        model_catalog_probe=FakeModelCatalogProbe(),
    )
    client = TestClient(app)
    created = client.post(
        "/projects",
        headers={"Authorization": "Bearer secret"},
        json={"name": "Blank update"},
    )

    response = client.patch(
        f"/projects/{created.json()['project_id']}",
        headers={"Authorization": "Bearer secret"},
        json={"default_model": "   "},
    )

    assert response.status_code == 422


def test_thread_create_repairs_inherited_effort_for_explicit_model(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        model_catalog_probe=FakeModelCatalogProbe(),
    )
    client = TestClient(app)
    project = client.post(
        "/projects",
        headers={"Authorization": "Bearer secret"},
        json={
            "name": "Thread settings",
            "default_model": "gpt-5.6-sol",
            "default_thinking_level": "ultra",
        },
    ).json()

    response = client.post(
        "/threads",
        headers={"Authorization": "Bearer secret"},
        json={
            "title": "Luna thread",
            "project_id": project["project_id"],
            "mode": "full-auto",
            "model_override": "gpt-5.6-luna",
        },
    )

    assert response.status_code == 201
    assert response.json()["model_override"] == "gpt-5.6-luna"
    assert response.json()["thinking_override"] == "medium"
    assert response.json()["effective_thinking_level"] == "medium"


def test_thread_update_repairs_or_rejects_incompatible_model_effort_pair(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        model_catalog_probe=FakeModelCatalogProbe(),
    )
    client = TestClient(app)
    thread = client.post(
        "/threads",
        headers={"Authorization": "Bearer secret"},
        json={"title": "Switch thread", "mode": "full-auto"},
    ).json()

    repaired = client.patch(
        f"/threads/{thread['thread_id']}",
        headers={"Authorization": "Bearer secret"},
        json={"model_override": "gpt-5.6-luna"},
    )
    rejected = client.patch(
        f"/threads/{thread['thread_id']}",
        headers={"Authorization": "Bearer secret"},
        json={
            "model_override": "gpt-5.6-luna",
            "thinking_override": "ultra",
        },
    )

    assert repaired.status_code == 200
    assert repaired.json()["thinking_override"] == "medium"
    assert rejected.status_code == 400
    assert "not supported" in rejected.json()["detail"]


def test_thread_update_preserves_unknown_future_model_pair(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        model_catalog_probe=FakeModelCatalogProbe(),
    )
    client = TestClient(app)
    thread = client.post(
        "/threads",
        headers={"Authorization": "Bearer secret"},
        json={"title": "Future model", "mode": "full-auto"},
    ).json()

    response = client.patch(
        f"/threads/{thread['thread_id']}",
        headers={"Authorization": "Bearer secret"},
        json={
            "model_override": "gpt-future-codex",
            "thinking_override": "future-effort",
        },
    )

    assert response.status_code == 200
    assert response.json()["model_override"] == "gpt-future-codex"
    assert response.json()["thinking_override"] == "future-effort"


def test_fresh_direct_chat_uses_configured_codex_defaults(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        model_catalog_probe=FakeModelCatalogProbe(),
    )
    client = TestClient(app)

    response = client.post(
        "/threads",
        headers={"Authorization": "Bearer secret"},
        json={"title": "Direct with current defaults"},
    )

    assert response.status_code == 201
    assert response.json()["effective_model"] == "gpt-5.6-sol"
    assert response.json()["effective_thinking_level"] == "ultra"


def test_status_surfaces_codex_auth_expired_state(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        diagnostics_probe=FakeDiagnosticsProbe(),
        model_catalog_probe=FakeModelCatalogProbe(),
        auth_manager=FakeAuthManager(),
    )
    client = TestClient(app)

    response = client.get("/status", headers={"Authorization": "Bearer secret"})

    assert response.status_code == 200
    assert response.json()["auth"]["state"] == "expired"
    assert response.json()["auth"]["auth_required"] is True
    assert "login expired" in response.json()["auth"]["message"]


def test_auth_routes_start_device_login_and_logout_require_token(tmp_path) -> None:
    auth_manager = FakeAuthManager()
    app = create_app(root_path=tmp_path, auth_token="secret", auth_manager=auth_manager)
    client = TestClient(app)

    assert client.post("/auth/device-login", json={"force_logout": True}).status_code == 401

    start_response = client.post(
        "/auth/device-login",
        headers={"Authorization": "Bearer secret"},
        json={"force_logout": True},
    )
    logout_response = client.post("/auth/logout", headers={"Authorization": "Bearer secret"})

    assert start_response.status_code == 202
    assert start_response.json()["state"] == "login_running"
    assert start_response.json()["user_code"] == "ABCD-EFGH"
    assert logout_response.status_code == 200
    assert logout_response.json()["state"] == "logged_out"
    assert auth_manager.started is True
    assert auth_manager.logged_out is True


def test_project_create_can_auto_create_workspace_from_name(tmp_path) -> None:
    app = create_app(root_path=tmp_path, auth_token="secret")
    client = TestClient(app)

    response = client.post(
        "/projects",
        headers={"Authorization": "Bearer secret"},
        json={
            "name": "Power Apps",
            "default_model": DEFAULT_MODEL,
            "default_thinking_level": DEFAULT_THINKING_LEVEL,
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["name"] == "Power Apps"
    assert payload["root_path"] == str(tmp_path / "project-workspaces" / "Power Apps")
    assert (tmp_path / "project-workspaces" / "Power Apps").is_dir()


def test_project_routes_list_browse_create_folder_and_update(tmp_path) -> None:
    app = create_app(root_path=tmp_path, auth_token="secret")
    client = TestClient(app)

    project_response = client.post(
        "/projects",
        headers={"Authorization": "Bearer secret"},
        json={
            "name": "VM Work",
            "root_path": str(tmp_path / "vm-work"),
            "default_model": DEFAULT_MODEL,
            "default_thinking_level": DEFAULT_THINKING_LEVEL,
        },
    )
    project_id = project_response.json()["project_id"]

    folder_response = client.post(
        "/projects/folders",
        headers={"Authorization": "Bearer secret"},
        json={
            "parent_path": str(tmp_path / "vm-work"),
            "folder_name": "notes",
        },
    )
    browse_response = client.get(
        f"/projects/browse?path={tmp_path / 'vm-work'}",
        headers={"Authorization": "Bearer secret"},
    )
    update_response = client.patch(
        f"/projects/{project_id}",
        headers={"Authorization": "Bearer secret"},
        json={
            "name": "VM Work Updated",
            "default_model": "gpt-5.5",
            "default_thinking_level": "high",
        },
    )
    list_response = client.get(
        "/projects",
        headers={"Authorization": "Bearer secret"},
    )

    assert folder_response.status_code == 201
    assert folder_response.json()["name"] == "notes"
    assert browse_response.status_code == 200
    assert browse_response.json()["path"] == str(tmp_path / "vm-work")
    assert browse_response.json()["directories"][0]["name"] == "notes"
    assert update_response.status_code == 200
    assert update_response.json()["name"] == "VM Work Updated"
    assert update_response.json()["default_model"] == "gpt-5.5"
    assert list_response.status_code == 200
    assert any(project["project_id"] == project_id for project in list_response.json())


def test_project_archive_restore_and_delete_routes(tmp_path) -> None:
    app = create_app(root_path=tmp_path, auth_token="secret")
    client = TestClient(app)

    project_response = client.post(
        "/projects",
        headers={"Authorization": "Bearer secret"},
        json={
            "name": "Disposable project",
            "root_path": str(tmp_path / "disposable"),
            "default_model": DEFAULT_MODEL,
            "default_thinking_level": DEFAULT_THINKING_LEVEL,
        },
    )
    project_id = project_response.json()["project_id"]
    thread_response = client.post(
        "/threads",
        headers={"Authorization": "Bearer secret"},
        json={
            "title": "Child thread",
            "project_id": project_id,
            "mode": "full-auto",
        },
    )

    archive_response = client.post(
        f"/projects/{project_id}/archive",
        headers={"Authorization": "Bearer secret"},
    )
    restore_response = client.post(
        f"/projects/{project_id}/restore",
        headers={"Authorization": "Bearer secret"},
    )
    delete_response = client.delete(
        f"/projects/{project_id}",
        headers={"Authorization": "Bearer secret"},
    )
    deleted_project_response = client.patch(
        f"/projects/{project_id}",
        headers={"Authorization": "Bearer secret"},
        json={"name": "gone"},
    )
    deleted_thread_response = client.get(
        f"/threads/{thread_response.json()['thread_id']}",
        headers={"Authorization": "Bearer secret"},
    )

    assert archive_response.status_code == 200
    assert archive_response.json()["archived_at"] is not None
    assert restore_response.status_code == 200
    assert restore_response.json()["archived_at"] is None
    assert delete_response.status_code == 204
    assert deleted_project_response.status_code == 404
    assert deleted_thread_response.status_code == 404


def test_thread_create_upload_and_event_stream_require_token_and_persist(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        runner_factory=FakeRunner,
    )
    client = TestClient(app)

    project_response = client.post(
        "/projects",
        headers={"Authorization": "Bearer secret"},
        json={
            "name": "Upload project",
            "root_path": str(tmp_path / "upload-project"),
            "default_model": DEFAULT_MODEL,
            "default_thinking_level": DEFAULT_THINKING_LEVEL,
        },
    )
    thread_response = client.post(
        "/threads",
        headers={"Authorization": "Bearer secret"},
        json={
            "title": "Upload target",
            "project_id": project_response.json()["project_id"],
            "mode": "full-auto",
        },
    )
    thread_payload = thread_response.json()
    thread_id = thread_payload["thread_id"]

    assert (
        client.post(
            f"/threads/{thread_id}/attachments",
            files={"file": ("notes.txt", b"blocked", "text/plain")},
        ).status_code
        == 401
    )
    assert client.get(f"/threads/{thread_id}/events").status_code == 401

    upload_response = client.post(
        f"/threads/{thread_id}/attachments",
        headers={"Authorization": "Bearer secret"},
        files={"file": ("../notes.txt", b"hello from api", "text/plain")},
    )

    assert upload_response.status_code == 201
    upload_payload = upload_response.json()

    saved_path = tmp_path / "threads" / f"{thread_id}.json"
    saved_payload = json.loads(saved_path.read_text(encoding="utf-8"))
    attachment_path = tmp_path / "uploads" / thread_id / "notes.txt"

    assert thread_payload["project_name"] == "Upload project"
    assert thread_payload["effective_model"] == DEFAULT_MODEL
    assert thread_payload["effective_thinking_level"] == DEFAULT_THINKING_LEVEL
    assert upload_payload["filename"] == "notes.txt"
    assert upload_payload["mime_type"] == "text/plain"
    assert upload_payload["stored_path"] == str(attachment_path)
    assert attachment_path.read_bytes() == b"hello from api"
    assert saved_payload["attachments"][0]["attachment_id"] == upload_payload["attachment_id"]
    assert saved_payload["attachments"][0]["stored_path"] == str(attachment_path)

    events_response = client.get(
        f"/threads/{thread_id}/events",
        headers={"Authorization": "Bearer secret"},
    )

    assert events_response.status_code == 200
    assert events_response.headers["content-type"].startswith("text/event-stream")
    assert "event: thread.created" in events_response.text
    assert "event: attachment.added" in events_response.text
    assert '"filename": "notes.txt"' in events_response.text

    replay_response = client.get(
        f"/threads/{thread_id}/events?after=1",
        headers={"Authorization": "Bearer secret"},
    )

    assert replay_response.status_code == 200
    assert "event: thread.created" not in replay_response.text
    assert "event: attachment.added" in replay_response.text


def test_thread_listing_update_prompt_replay_and_artifact_download_routes(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        runner_factory=FakeRunner,
    )
    client = TestClient(app)

    project_response = client.post(
        "/projects",
        headers={"Authorization": "Bearer secret"},
        json={
            "name": "Prompt project",
            "root_path": str(tmp_path / "prompt-project"),
            "default_model": DEFAULT_MODEL,
            "default_thinking_level": DEFAULT_THINKING_LEVEL,
        },
    )
    thread_response = client.post(
        "/threads",
        headers={"Authorization": "Bearer secret"},
        json={
            "title": "Prompt target",
            "project_id": project_response.json()["project_id"],
            "mode": "full-auto",
        },
    )
    thread_payload = thread_response.json()
    thread_id = thread_payload["thread_id"]

    list_response = client.get(
        "/threads",
        headers={"Authorization": "Bearer secret"},
    )
    get_response = client.get(
        f"/threads/{thread_id}",
        headers={"Authorization": "Bearer secret"},
    )
    update_response = client.patch(
        f"/threads/{thread_id}",
        headers={"Authorization": "Bearer secret"},
        json={
            "model_override": "gpt-5.5",
            "thinking_override": "high",
        },
    )

    assert list_response.status_code == 200
    assert get_response.status_code == 200
    assert update_response.status_code == 200
    assert list_response.json()[0]["thread_id"] == thread_id
    assert get_response.json()["thread_id"] == thread_id
    assert update_response.json()["effective_model"] == "gpt-5.5"
    assert update_response.json()["effective_thinking_level"] == "high"

    assert client.post(
        f"/threads/{thread_id}/prompts",
        json={"prompt": "No token"},
    ).status_code == 401

    prompt_response = client.post(
        f"/threads/{thread_id}/prompts",
        headers={"Authorization": "Bearer secret"},
        json={"prompt": "Summarise the upload"},
    )

    assert prompt_response.status_code == 202
    assert prompt_response.json() == {
        "run_id": "run_fake123",
        "thread_id": thread_id,
        "status": "running",
    }

    replay_response = client.get(
        f"/threads/{thread_id}/events/replay?after=1",
        headers={"Authorization": "Bearer secret"},
    )

    assert replay_response.status_code == 200
    assert replay_response.json()[0]["event_type"] == "thread.updated"
    assert replay_response.json()[-1]["event_type"] == "message.created"
    assert replay_response.json()[-1]["payload"]["text"] == "Summarise the upload"

    artifact_path = tmp_path / "prompt-project" / "reply.md"
    artifact_path.write_text("hello", encoding="utf-8")
    artifacts = app.state.storage.sync_thread_artifacts(thread_id)

    artifacts_response = client.get(
        f"/threads/{thread_id}/artifacts",
        headers={"Authorization": "Bearer secret"},
    )
    archive_response = client.post(
        f"/threads/{thread_id}/artifacts/workspace-archive",
        headers={"Authorization": "Bearer secret"},
    )
    download_response = client.get(
        f"/threads/{thread_id}/artifacts/{artifacts[0].artifact_id}",
        headers={"Authorization": "Bearer secret"},
    )

    assert artifacts_response.status_code == 200
    assert artifacts_response.json()[0]["filename"] == "reply.md"
    assert archive_response.status_code == 201
    assert archive_response.json()["filename"].endswith(".zip")
    assert download_response.status_code == 200
    assert download_response.text == "hello"


def test_cancel_active_run_route_marks_thread_idle(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        runner_factory=lambda storage: FakeRunner(storage),
    )
    client = TestClient(app)
    thread_response = client.post(
        "/threads",
        headers={"Authorization": "Bearer secret"},
        json={"title": "Cancelable", "mode": "full-auto"},
    )
    thread_id = thread_response.json()["thread_id"]
    prompt_response = client.post(
        f"/threads/{thread_id}/prompts",
        headers={"Authorization": "Bearer secret"},
        json={"prompt": "Keep going"},
    )

    cancel_response = client.post(
        f"/threads/{thread_id}/runs/current/cancel",
        headers={"Authorization": "Bearer secret"},
    )
    thread_after_cancel = client.get(
        f"/threads/{thread_id}",
        headers={"Authorization": "Bearer secret"},
    )
    events_response = client.get(
        f"/threads/{thread_id}/events/replay",
        headers={"Authorization": "Bearer secret"},
    )

    assert prompt_response.status_code == 202
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"
    assert thread_after_cancel.json()["status"] == "idle"
    assert events_response.json()[-1]["event_type"] == "run.cancelled"


def test_prompt_route_accepts_steer_message_while_thread_is_running(tmp_path, monkeypatch) -> None:
    class BlockingProcess:
        instances = []

        def __init__(self, command) -> None:
            self.command = command
            self.released = Event()
            self.stdout = self._stdout()
            self.stderr = iter([])
            BlockingProcess.instances.append(self)

        def _stdout(self):
            prompt = self.command[self.command.index("--json") - 1]
            yield json.dumps({"type": "thread.started", "thread_id": "019e08fb-92dc-7920-88f3-9fc949d1aef8"}) + "\n"
            yield json.dumps({"type": "turn.started"}) + "\n"
            assert self.released.wait(2), "fake codex process was not released"
            yield json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "item_1", "type": "agent_message", "text": f"Echo: {prompt}"},
                }
            ) + "\n"
            yield json.dumps({"type": "turn.completed", "usage": {}}) + "\n"

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(
        "codex_bridge_service.runner.subprocess.Popen",
        lambda command, **kwargs: BlockingProcess(command),
    )

    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        runner_factory=lambda storage: BridgeRunner(storage=storage, codex_command="codex"),
        model_catalog_probe=FakeModelCatalogProbe(),
    )
    client = TestClient(app)
    thread_response = client.post(
        "/threads",
        headers={"Authorization": "Bearer secret"},
        json={"title": "Steerable", "mode": "full-auto"},
    )
    thread_id = thread_response.json()["thread_id"]
    first_response = client.post(
        f"/threads/{thread_id}/prompts",
        headers={"Authorization": "Bearer secret"},
        json={"prompt": "Start the work"},
    )

    deadline = time.time() + 2
    while time.time() < deadline and not BlockingProcess.instances:
        time.sleep(0.02)

    steer_response = client.post(
        f"/threads/{thread_id}/prompts",
        headers={"Authorization": "Bearer secret"},
        json={"prompt": "Steer toward the smaller fix"},
    )

    assert first_response.status_code == 202
    assert steer_response.status_code == 202
    assert steer_response.json()["status"] == "queued"

    BlockingProcess.instances[0].released.set()
    deadline = time.time() + 2
    while time.time() < deadline and len(BlockingProcess.instances) < 2:
        time.sleep(0.02)
    assert len(BlockingProcess.instances) == 2
    BlockingProcess.instances[1].released.set()

    deadline = time.time() + 5
    while time.time() < deadline:
        thread_after = client.get(
            f"/threads/{thread_id}",
            headers={"Authorization": "Bearer secret"},
        ).json()
        if thread_after["status"] == "idle":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("thread did not return to idle in time")

    events_response = client.get(
        f"/threads/{thread_id}/events/replay",
        headers={"Authorization": "Bearer secret"},
    )

    assert events_response.status_code == 200
    assert any(event["event_type"] == "run.queued" for event in events_response.json())
    assert events_response.json()[-1]["event_type"] == "run.completed"


def test_thread_archive_restore_delete_and_include_archived_routes(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        runner_factory=FakeRunner,
    )
    client = TestClient(app)

    thread_response = client.post(
        "/threads",
        headers={"Authorization": "Bearer secret"},
        json={
            "title": "Direct target",
            "mode": "full-auto",
        },
    )
    thread_id = thread_response.json()["thread_id"]

    archive_response = client.post(
        f"/threads/{thread_id}/archive",
        headers={"Authorization": "Bearer secret"},
    )
    active_response = client.get(
        "/threads",
        headers={"Authorization": "Bearer secret"},
    )
    archived_response = client.get(
        "/threads?include_archived=true",
        headers={"Authorization": "Bearer secret"},
    )

    assert archive_response.status_code == 200
    assert archive_response.json()["archived_at"] is not None
    assert active_response.json() == []
    assert archived_response.json()[0]["thread_id"] == thread_id

    restore_response = client.post(
        f"/threads/{thread_id}/restore",
        headers={"Authorization": "Bearer secret"},
    )
    delete_response = client.delete(
        f"/threads/{thread_id}",
        headers={"Authorization": "Bearer secret"},
    )
    get_deleted_response = client.get(
        f"/threads/{thread_id}",
        headers={"Authorization": "Bearer secret"},
    )

    assert restore_response.status_code == 200
    assert restore_response.json()["archived_at"] is None
    assert delete_response.status_code == 204
    assert get_deleted_response.status_code == 404


def test_thread_attachment_upload_accepts_relative_path(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path,
        auth_token="secret",
        runner_factory=FakeRunner,
    )
    client = TestClient(app)

    thread_response = client.post(
        "/threads",
        headers={"Authorization": "Bearer secret"},
        json={
            "title": "Folder upload",
            "mode": "full-auto",
        },
    )
    thread_id = thread_response.json()["thread_id"]

    upload_response = client.post(
        f"/threads/{thread_id}/attachments",
        headers={"Authorization": "Bearer secret"},
        data={"relative_path": "src/vba/Module1.bas"},
        files={"file": ("Module1.bas", b"Attribute VB_Name = \"Module1\"", "text/plain")},
    )

    assert upload_response.status_code == 201
    assert upload_response.json()["relative_path"] == "src/vba/Module1.bas"
