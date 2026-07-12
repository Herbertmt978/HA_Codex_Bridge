import os

import pytest

from codex_bridge_service.app import create_app
from codex_bridge_service.models import RuntimeProfile
from codex_bridge_service.storage import BridgeStorage


def test_external_legacy_storage_keeps_existing_root_layout(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)

    assert storage.runtime_profile is RuntimeProfile.EXTERNAL_LEGACY
    assert storage.workspace_boundary is None
    assert storage.workspace_root is None
    assert storage.workspaces_dir == tmp_path / "workspaces"
    assert storage.project_workspaces_dir == tmp_path / "project-workspaces"


def test_home_assistant_storage_separates_private_state_and_workspace_roots(tmp_path) -> None:
    state_root = tmp_path / "private-state"
    workspace_root = tmp_path / "public-workspaces"
    workspace_root.mkdir()

    storage = BridgeStorage(
        root_path=state_root,
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
    )

    assert storage.runtime_profile is RuntimeProfile.HOME_ASSISTANT
    assert storage.workspace_root == workspace_root.resolve()
    assert storage.workspace_boundary is not None
    assert storage.workspace_boundary.root == workspace_root.resolve()
    assert storage.projects_dir.parent == state_root
    assert storage.threads_dir.parent == state_root
    assert storage.uploads_dir.parent == state_root
    assert storage.artifacts_dir.parent == state_root
    assert not (workspace_root / "projects").exists()
    assert not (workspace_root / "threads").exists()
    assert not (workspace_root / "uploads").exists()
    assert not (workspace_root / "artifacts").exists()


def test_home_assistant_storage_rejects_missing_or_shared_workspace_root(tmp_path) -> None:
    private_marker = tmp_path / "private-marker"

    with pytest.raises(ValueError) as missing:
        BridgeStorage(
            root_path=private_marker,
            runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        )
    assert str(private_marker) not in str(missing.value)

    private_marker.mkdir()
    with pytest.raises(ValueError) as shared:
        BridgeStorage(
            root_path=private_marker,
            runtime_profile=RuntimeProfile.HOME_ASSISTANT,
            workspace_root=private_marker,
        )
    assert str(private_marker) not in str(shared.value)

    with pytest.raises(ValueError):
        BridgeStorage(
            root_path=private_marker,
            runtime_profile=RuntimeProfile.HOME_ASSISTANT,
            workspace_root=private_marker / "nested-workspaces",
        )
    with pytest.raises(ValueError):
        BridgeStorage(
            root_path=private_marker / "nested-state",
            runtime_profile=RuntimeProfile.HOME_ASSISTANT,
            workspace_root=private_marker,
        )


@pytest.mark.skipif(os.name == "nt", reason="secure dir_fd root creation is unavailable")
def test_home_assistant_storage_securely_creates_missing_workspace_root(tmp_path) -> None:
    workspace_root = tmp_path / "new" / "workspaces"

    storage = BridgeStorage(
        root_path=tmp_path / "state",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
    )

    assert workspace_root.is_dir()
    assert storage.workspace_boundary is not None


def test_create_app_passes_runtime_profile_to_storage(tmp_path) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    app = create_app(
        root_path=tmp_path / "state",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
    )

    assert app.state.storage.runtime_profile is RuntimeProfile.HOME_ASSISTANT
    assert app.state.storage.workspace_root == workspace_root.resolve()
