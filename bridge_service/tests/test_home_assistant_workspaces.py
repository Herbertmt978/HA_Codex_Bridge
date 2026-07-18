import json
import os
from pathlib import Path
from threading import Event, Thread

import pytest
from fastapi.testclient import TestClient

from codex_bridge_service.app import create_app
from codex_bridge_service.models import ProjectKind, RunMode, RuntimeProfile
from codex_bridge_service.storage import BridgeStorage, ProjectMutationError
from codex_bridge_service.workspace import (
    WorkspaceBoundaryError,
    WorkspaceInputError,
)


def _home_assistant_storage(tmp_path) -> tuple[BridgeStorage, Path, Path]:
    if os.name == "nt":
        pytest.skip("secure Home Assistant workspace operations require POSIX dir_fd support")
    state_root = tmp_path / "private-state"
    workspace_root = tmp_path / "workspaces"
    storage = BridgeStorage(
        root_path=state_root,
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
    )
    return storage, state_root, workspace_root


def test_account_rebind_detaches_provider_when_historical_workspace_is_missing(
    tmp_path,
) -> None:
    storage, state_root, workspace_root = _home_assistant_storage(tmp_path)
    thread = storage.create_thread(title="Historical chat", mode=RunMode.FULL_AUTO)
    assert storage.bind_codex_account("a" * 64) == 0
    record = storage.load_thread(thread.thread_id)
    record.codex_thread_id = "provider-thread-account-a"
    storage.save_thread(record)
    thread_path = state_root / "threads" / f"{thread.thread_id}.json"
    before = json.loads(thread_path.read_text(encoding="utf-8"))
    (workspace_root / record.workspace_path).rmdir()

    assert storage.bind_codex_account("b" * 64) == 1
    assert storage.bind_codex_account("b" * 64) == 0

    payload = json.loads(thread_path.read_text(encoding="utf-8"))
    before.pop("_bridge_operation", None)
    payload.pop("_bridge_operation", None)
    assert payload == {**before, "codex_thread_id": None}
    binding = json.loads(
        (state_root / "account-binding.json").read_text(encoding="utf-8")
    )
    assert binding["owner_marker"] == "b" * 64


def test_home_assistant_projects_use_only_relative_portable_workspace_paths(tmp_path) -> None:
    storage, _, workspace_root = _home_assistant_storage(tmp_path)

    direct = storage.ensure_direct_project()
    imported = storage.ensure_imported_project()
    explicit = storage.create_project(name="Nested", root_path=r"teams\bridge")
    first = storage.create_project(name="Power Apps")
    second = storage.create_project(name="Power Apps")

    assert direct.root_path == "."
    assert imported.root_path == "."
    assert explicit.root_path == "teams/bridge"
    assert (workspace_root / "teams" / "bridge").is_dir()
    assert first.root_path != second.root_path
    assert "/" not in first.root_path
    assert "/" not in second.root_path
    assert (workspace_root / first.root_path).is_dir()
    assert (workspace_root / second.root_path).is_dir()
    assert str(workspace_root) not in json.dumps(
        [project.model_dump(mode="json") for project in storage.list_projects()]
    )


@pytest.mark.parametrize(
    "root_path",
    ["/config/workspaces/escape", r"C:\escape", r"\\server\share", "../escape"],
)
def test_home_assistant_projects_reject_nonrelative_workspace_paths(
    tmp_path,
    root_path: str,
) -> None:
    storage, _, _ = _home_assistant_storage(tmp_path)

    with pytest.raises(WorkspaceInputError):
        storage.create_project(name="Rejected", root_path=root_path)


def test_home_assistant_projects_reject_links_and_non_directories(tmp_path) -> None:
    storage, _, workspace_root = _home_assistant_storage(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace_root / "outside-link").symlink_to(outside, target_is_directory=True)
    (workspace_root / "regular-file").write_text("not a directory", encoding="utf-8")

    for root_path in ("outside-link", "regular-file"):
        with pytest.raises(WorkspaceBoundaryError):
            storage.create_project(name="Rejected", root_path=root_path)


def test_home_assistant_browse_and_folder_creation_are_relative_and_confined(tmp_path) -> None:
    storage, _, workspace_root = _home_assistant_storage(tmp_path)

    created = storage.create_folder(parent_path=".", folder_name="teams")
    nested = storage.create_folder(parent_path="teams", folder_name="bridge")
    root_browse = storage.browse_paths()
    nested_browse = storage.browse_paths("teams")

    assert created.path == "teams"
    assert nested.path == "teams/bridge"
    assert root_browse.path == "."
    assert root_browse.parent_path is None
    assert [entry.path for entry in root_browse.directories] == ["teams"]
    assert nested_browse.path == "teams"
    assert nested_browse.parent_path == "."
    assert [entry.path for entry in nested_browse.directories] == ["teams/bridge"]
    assert (workspace_root / "teams" / "bridge").is_dir()

    for rejected in ("/config", "../outside", r"C:\outside"):
        with pytest.raises(WorkspaceBoundaryError):
            storage.browse_paths(rejected)


def test_home_assistant_threads_and_events_keep_workspace_paths_relative(tmp_path) -> None:
    storage, _, workspace_root = _home_assistant_storage(tmp_path)
    project = storage.create_project(name="Bridge", root_path="projects/bridge")

    project_thread = storage.create_thread(
        title="Project chat",
        mode=RunMode.EDIT,
        project_id=project.project_id,
    )
    direct_thread = storage.create_thread(title="Direct chat", mode=RunMode.FULL_AUTO)
    event = storage.list_thread_events(direct_thread.thread_id)[0]

    assert project_thread.workspace_path == "projects/bridge"
    assert project_thread.project_root_path == "projects/bridge"
    assert direct_thread.project_kind is ProjectKind.DIRECT
    assert direct_thread.workspace_path.startswith("ws_")
    assert "/" not in direct_thread.workspace_path
    assert direct_thread.project_root_path == "."
    assert event.payload["workspace_id"] == direct_thread.workspace_id
    assert "workspace_path" not in event.payload
    assert str(workspace_root) not in json.dumps(event.payload)
    assert storage.resolve_workspace_path(project_thread.workspace_path) == (
        workspace_root / "projects" / "bridge"
    )
    assert storage.resolve_workspace_path(direct_thread.workspace_path) == (
        workspace_root / direct_thread.workspace_path
    )


def test_home_assistant_project_root_update_succeeds_before_chats_exist(tmp_path) -> None:
    storage, _, workspace_root = _home_assistant_storage(tmp_path)
    project = storage.create_project(name="Bridge", root_path="projects/bridge-old")

    updated = storage.update_project(project.project_id, root_path="projects/bridge-new")

    assert updated.root_path == "projects/bridge-new"
    assert (workspace_root / "projects" / "bridge-new").is_dir()


def test_home_assistant_project_root_update_rejects_existing_chats(tmp_path) -> None:
    storage, _, workspace_root = _home_assistant_storage(tmp_path)
    project = storage.create_project(name="Bridge", root_path="projects/bridge-old")
    first = storage.create_thread(
        title="First chat",
        mode=RunMode.EDIT,
        project_id=project.project_id,
    )
    second = storage.create_thread(
        title="Second chat",
        mode=RunMode.FULL_AUTO,
        project_id=project.project_id,
    )
    project_path = storage.projects_dir / f"{project.project_id}.json"
    first_path = storage.threads_dir / f"{first.thread_id}.json"
    second_path = storage.threads_dir / f"{second.thread_id}.json"
    before = (project_path.read_bytes(), first_path.read_bytes(), second_path.read_bytes())

    with pytest.raises(ProjectMutationError, match="after chats are created"):
        storage.update_project(project.project_id, root_path="projects/bridge-new")

    assert storage.load_project(project.project_id).root_path == "projects/bridge-old"
    assert storage.get_thread(first.thread_id).workspace_path == "projects/bridge-old"
    assert storage.get_thread(second.thread_id).workspace_path == "projects/bridge-old"
    assert not (workspace_root / "projects" / "bridge-new").exists()
    assert (project_path.read_bytes(), first_path.read_bytes(), second_path.read_bytes()) == before


def test_home_assistant_project_root_update_rejects_an_active_chat(tmp_path) -> None:
    storage, _, workspace_root = _home_assistant_storage(tmp_path)
    project = storage.create_project(name="Bridge", root_path="projects/bridge-old")
    thread = storage.create_thread(
        title="Active chat",
        mode=RunMode.FULL_AUTO,
        project_id=project.project_id,
    )
    record = storage.load_thread(thread.thread_id)
    record.status = "running"
    record.active_run_id = "run_active"
    storage.save_thread(record)

    with pytest.raises(ProjectMutationError, match="after chats are created"):
        storage.update_project(project.project_id, root_path="projects/bridge-new")

    assert storage.load_thread(thread.thread_id).status == "running"
    assert storage.load_project(project.project_id).root_path == "projects/bridge-old"
    assert not (workspace_root / "projects" / "bridge-new").exists()


def test_home_assistant_project_update_and_chat_creation_are_linearized(
    tmp_path,
    monkeypatch,
) -> None:
    storage, _, _ = _home_assistant_storage(tmp_path)
    project = storage.create_project(name="Bridge", root_path="projects/bridge-old")
    preflight_entered = Event()
    release_preflight = Event()
    creator_finished = Event()
    original_preflight = storage._preflight_project_threads
    results: dict[str, object] = {}

    def blocked_preflight(project_record):
        preflight_entered.set()
        assert release_preflight.wait(timeout=5)
        return original_preflight(project_record)

    def update_workspace() -> None:
        try:
            results["project"] = storage.update_project(
                project.project_id,
                root_path="projects/bridge-new",
            )
        except Exception as exc:  # pragma: no cover - asserted through results
            results["update_error"] = exc

    def create_chat() -> None:
        try:
            results["thread"] = storage.create_thread(
                title="Concurrent chat",
                mode=RunMode.EDIT,
                project_id=project.project_id,
            )
        except Exception as exc:  # pragma: no cover - asserted through results
            results["create_error"] = exc
        finally:
            creator_finished.set()

    monkeypatch.setattr(storage, "_preflight_project_threads", blocked_preflight)
    updater = Thread(target=update_workspace)
    creator = Thread(target=create_chat)
    updater.start()
    assert preflight_entered.wait(timeout=5)
    creator.start()
    assert not creator_finished.wait(timeout=0.1)
    release_preflight.set()
    updater.join(timeout=5)
    creator.join(timeout=5)

    assert not updater.is_alive()
    assert not creator.is_alive()
    assert "update_error" not in results
    assert "create_error" not in results
    assert results["project"].root_path == "projects/bridge-new"
    assert results["thread"].workspace_path == "projects/bridge-new"
    assert storage.get_thread(results["thread"].thread_id).workspace_path == (
        "projects/bridge-new"
    )


def test_home_assistant_project_root_update_preflights_threads_before_mutation(tmp_path) -> None:
    storage, _, workspace_root = _home_assistant_storage(tmp_path)
    project = storage.create_project(name="Bridge", root_path="projects/bridge-old")
    thread = storage.create_thread(
        title="Tampered chat",
        mode=RunMode.EDIT,
        project_id=project.project_id,
    )
    storage.create_folder(parent_path="projects", folder_name="other")
    project_path = storage.projects_dir / f"{project.project_id}.json"
    thread_path = storage.threads_dir / f"{thread.thread_id}.json"
    project_before = project_path.read_bytes()
    thread_payload = json.loads(thread_path.read_text(encoding="utf-8"))
    thread_payload["workspace_path"] = "projects/other"
    thread_path.write_text(json.dumps(thread_payload), encoding="utf-8")
    tampered_before = thread_path.read_bytes()

    with pytest.raises(WorkspaceBoundaryError):
        storage.update_project(project.project_id, root_path="projects/bridge-new")

    assert project_path.read_bytes() == project_before
    assert thread_path.read_bytes() == tampered_before
    assert not (workspace_root / "projects" / "bridge-new").exists()


def test_home_assistant_rejects_tampered_persisted_project_and_thread_paths(tmp_path) -> None:
    storage, _, workspace_root = _home_assistant_storage(tmp_path)
    project = storage.create_project(name="Bridge", root_path="projects/bridge")
    thread = storage.create_thread(
        title="Bridge chat",
        mode=RunMode.EDIT,
        project_id=project.project_id,
    )
    project_path = storage.projects_dir / f"{project.project_id}.json"
    thread_path = storage.threads_dir / f"{thread.thread_id}.json"

    project_payload = json.loads(project_path.read_text(encoding="utf-8"))
    project_payload["root_path"] = str(workspace_root)
    project_path.write_text(json.dumps(project_payload), encoding="utf-8")
    with pytest.raises(WorkspaceBoundaryError):
        storage.load_project(project.project_id)

    project_payload["root_path"] = "projects/bridge"
    project_path.write_text(json.dumps(project_payload), encoding="utf-8")
    thread_payload = json.loads(thread_path.read_text(encoding="utf-8"))
    thread_payload["workspace_path"] = str(workspace_root)
    thread_path.write_text(json.dumps(thread_payload), encoding="utf-8")
    with pytest.raises(WorkspaceBoundaryError):
        storage.get_thread(thread.thread_id)


def test_home_assistant_rejects_a_project_directory_replaced_by_a_symlink(tmp_path) -> None:
    storage, _, workspace_root = _home_assistant_storage(tmp_path)
    project = storage.create_project(name="Bridge", root_path="projects/bridge")
    project_root = workspace_root / "projects" / "bridge"
    project_root.rmdir()
    outside = tmp_path / "outside-project"
    outside.mkdir()
    project_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceBoundaryError):
        storage.load_project(project.project_id)


def test_home_assistant_rejects_a_tampered_direct_chat_workspace_id(tmp_path) -> None:
    storage, _, _ = _home_assistant_storage(tmp_path)
    storage.create_folder(parent_path=".", folder_name="projects")
    storage.create_folder(parent_path="projects", folder_name="other")
    thread = storage.create_thread(title="Direct chat", mode=RunMode.FULL_AUTO)
    thread_path = storage.threads_dir / f"{thread.thread_id}.json"
    payload = json.loads(thread_path.read_text(encoding="utf-8"))
    payload["workspace_id"] = "projects/other"
    payload["workspace_path"] = "projects/other"
    thread_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(WorkspaceBoundaryError):
        storage.get_thread(thread.thread_id)


def test_home_assistant_api_rejects_tampered_project_chat_id_without_path_leak(tmp_path) -> None:
    if os.name == "nt":
        pytest.skip("secure Home Assistant workspace operations require POSIX dir_fd support")
    app = create_app(
        root_path=tmp_path / "data" / "bridge",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=tmp_path / "config" / "workspaces",
    )
    storage = app.state.storage
    project = storage.create_project(name="Bridge", root_path="projects/bridge")
    thread = storage.create_thread(
        title="Project chat",
        mode=RunMode.EDIT,
        project_id=project.project_id,
    )
    thread_path = storage.threads_dir / f"{thread.thread_id}.json"
    payload = json.loads(thread_path.read_text(encoding="utf-8"))
    private_id = "/data/private-token"
    payload["workspace_id"] = private_id
    thread_path.write_text(json.dumps(payload), encoding="utf-8")
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}

    responses = [
        client.get("/threads", headers=headers),
        client.get(f"/threads/{thread.thread_id}", headers=headers),
        client.get(f"/threads/{thread.thread_id}/events", headers=headers),
        client.get(f"/threads/{thread.thread_id}/events/replay", headers=headers),
    ]

    for response in responses:
        assert response.status_code == 400
        assert response.json() == {"detail": "invalid workspace path"}
        assert private_id not in response.text


def test_home_assistant_event_routes_redact_missing_workspace_errors(tmp_path) -> None:
    if os.name == "nt":
        pytest.skip("secure Home Assistant workspace operations require POSIX dir_fd support")
    workspace_root = tmp_path / "config" / "workspaces"
    app = create_app(
        root_path=tmp_path / "data" / "bridge",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
    )
    storage = app.state.storage
    project = storage.create_project(name="Bridge", root_path="projects/bridge")
    thread = storage.create_thread(
        title="Project chat",
        mode=RunMode.EDIT,
        project_id=project.project_id,
    )
    (workspace_root / "projects" / "bridge").rmdir()
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}

    for suffix in ("events", "events/replay"):
        response = client.get(f"/threads/{thread.thread_id}/{suffix}", headers=headers)
        assert response.status_code == 404
        assert response.json() == {"detail": "workspace path not found"}
        assert str(workspace_root) not in response.text


def test_home_assistant_api_never_returns_private_or_absolute_workspace_roots(tmp_path) -> None:
    if os.name == "nt":
        pytest.skip("secure Home Assistant workspace operations require POSIX dir_fd support")
    state_root = tmp_path / "data" / "bridge"
    workspace_root = tmp_path / "config" / "workspaces"
    app = create_app(
        root_path=state_root,
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}

    project_response = client.post(
        "/projects",
        headers=headers,
        json={"name": "Bridge", "root_path": "projects/bridge"},
    )
    project_id = project_response.json()["project_id"]
    thread_response = client.post(
        "/threads",
        headers=headers,
        json={"title": "HA chat", "project_id": project_id, "mode": "edit"},
    )
    browse_response = client.get("/projects/browse", headers=headers)
    list_response = client.get("/projects", headers=headers)

    assert project_response.status_code == 201
    assert project_response.json()["root_path"] == "projects/bridge"
    assert thread_response.status_code == 201
    assert thread_response.json()["workspace_path"] == "projects/bridge"
    assert browse_response.status_code == 200
    assert browse_response.json()["path"] == "."
    serialized = "\n".join(
        response.text
        for response in (project_response, thread_response, browse_response, list_response)
    )
    assert str(state_root) not in serialized
    assert str(workspace_root) not in serialized


def test_home_assistant_workspace_route_errors_are_generic_and_redacted(tmp_path) -> None:
    if os.name == "nt":
        pytest.skip("secure Home Assistant workspace operations require POSIX dir_fd support")
    workspace_root = tmp_path / "config" / "workspaces"
    app = create_app(
        root_path=tmp_path / "data" / "bridge",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}
    secret_path = str(tmp_path / "private-secret")

    create_response = client.post(
        "/projects",
        headers=headers,
        json={"name": "Rejected", "root_path": secret_path},
    )
    browse_response = client.get(
        "/projects/browse",
        headers=headers,
        params={"path": "missing"},
    )
    folder_response = client.post(
        "/projects/folders",
        headers=headers,
        json={"parent_path": "../escape", "folder_name": "private"},
    )

    assert create_response.status_code == 400
    assert create_response.json() == {"detail": "invalid workspace path"}
    assert browse_response.status_code == 404
    assert browse_response.json() == {"detail": "workspace path not found"}
    assert folder_response.status_code == 400
    assert folder_response.json() == {"detail": "invalid workspace path"}
    assert secret_path not in create_response.text


def test_home_assistant_project_patch_rejects_workspace_change_after_chat_creation(tmp_path) -> None:
    if os.name == "nt":
        pytest.skip("secure Home Assistant workspace operations require POSIX dir_fd support")
    state_root = tmp_path / "data" / "bridge"
    workspace_root = tmp_path / "config" / "workspaces"
    app = create_app(
        root_path=state_root,
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}
    project = app.state.storage.create_project(name="Bridge", root_path="projects/old")
    thread = app.state.storage.create_thread(
        title="Existing chat",
        mode=RunMode.EDIT,
        project_id=project.project_id,
    )

    patch_response = client.patch(
        f"/projects/{project.project_id}",
        headers=headers,
        json={"root_path": "projects/new"},
    )
    get_response = client.get(f"/threads/{thread.thread_id}", headers=headers)
    list_response = client.get("/threads", headers=headers)

    assert patch_response.status_code == 400
    assert patch_response.json() == {
        "detail": "project workspace cannot be changed after chats are created"
    }
    assert get_response.status_code == 200
    assert get_response.json()["workspace_path"] == "projects/old"
    listed = next(item for item in list_response.json() if item["thread_id"] == thread.thread_id)
    assert listed["workspace_path"] == "projects/old"
    assert not (workspace_root / "projects" / "new").exists()
    serialized = "\n".join(
        response.text for response in (patch_response, get_response, list_response)
    )
    assert str(state_root) not in serialized
    assert str(workspace_root) not in serialized


def test_external_legacy_profile_still_accepts_and_returns_absolute_roots(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path / "state")
    absolute_root = tmp_path / "legacy-project"

    project = storage.create_project(name="Legacy", root_path=str(absolute_root))
    thread = storage.create_thread(
        title="Legacy chat",
        mode=RunMode.FULL_AUTO,
        project_id=project.project_id,
    )

    assert project.root_path == str(absolute_root)
    assert thread.workspace_path == str(absolute_root)
    assert storage.resolve_workspace_path(thread.workspace_path) == absolute_root
