import os

import pytest

from codex_bridge_service.app import create_app
from codex_bridge_service.automations import AutomationStore
from codex_bridge_service.capabilities import CapabilitiesManager
from codex_bridge_service.mcp_manager import McpManager
from codex_bridge_service.models import RuntimeProfile
from codex_bridge_service.resource_limits import ResourceLimits
from codex_bridge_service.routes.agents import WorkspaceAgentsManager
from codex_bridge_service.runner import BridgeRunner
from codex_bridge_service.storage import BridgeStorage


class _ReadyBrowserBroker:
    ready = True

    def set_artifact_sink(self, _sink) -> None:
        pass

    def close_owner(self, _owner) -> None:
        pass

    def close(self) -> None:
        pass


def _registered_paths(app) -> set[str]:
    return set(app.openapi()["paths"])


def test_external_legacy_storage_keeps_existing_root_layout(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path)

    assert storage.runtime_profile is RuntimeProfile.EXTERNAL_LEGACY
    assert storage.workspace_boundary is None
    assert storage.workspace_root is None
    assert storage.workspaces_dir == tmp_path / "workspaces"
    assert storage.project_workspaces_dir == tmp_path / "project-workspaces"


def test_home_assistant_storage_separates_private_state_and_workspace_roots(
    tmp_path,
) -> None:
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
    assert storage.logs_dir.parent == state_root
    assert not (workspace_root / "projects").exists()
    assert not (workspace_root / "threads").exists()
    assert not (workspace_root / "uploads").exists()
    assert not (workspace_root / "artifacts").exists()
    assert not (workspace_root / "logs").exists()


def test_home_assistant_storage_rejects_missing_or_shared_workspace_root(
    tmp_path,
) -> None:
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


@pytest.mark.parametrize("workspace_root", ["relative/workspaces", "~/workspaces"])
def test_invalid_home_assistant_workspace_root_has_no_private_state_side_effect(
    tmp_path,
    workspace_root: str,
) -> None:
    state_root = tmp_path / "state-must-not-exist"

    with pytest.raises(ValueError):
        BridgeStorage(
            root_path=state_root,
            runtime_profile=RuntimeProfile.HOME_ASSISTANT,
            workspace_root=workspace_root,
        )

    assert not state_root.exists()


def test_home_assistant_storage_rejects_symlink_alias_of_private_state(
    tmp_path,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    workspace_alias = tmp_path / "workspace-alias"
    try:
        workspace_alias.symlink_to(state_root, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symbolic links are unavailable: {type(exc).__name__}")

    with pytest.raises(ValueError):
        BridgeStorage(
            root_path=state_root,
            runtime_profile=RuntimeProfile.HOME_ASSISTANT,
            workspace_root=workspace_alias,
        )


@pytest.mark.skipif(
    os.name != "nt", reason="Windows case-insensitive paths are unavailable"
)
def test_home_assistant_storage_rejects_case_alias_of_private_state(tmp_path) -> None:
    state_root = tmp_path / "MixedCaseState"
    state_root.mkdir()

    with pytest.raises(ValueError):
        BridgeStorage(
            root_path=state_root,
            runtime_profile=RuntimeProfile.HOME_ASSISTANT,
            workspace_root=str(state_root).swapcase(),
        )


@pytest.mark.skipif(
    os.name == "nt", reason="secure dir_fd root creation is unavailable"
)
def test_home_assistant_storage_securely_creates_missing_workspace_root(
    tmp_path,
) -> None:
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


def test_home_assistant_profile_wires_admin_capability_surfaces(tmp_path) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()

    app = create_app(
        root_path=tmp_path / "state",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
        codex_home=codex_home,
    )

    assert isinstance(app.state.automations, AutomationStore)
    assert isinstance(app.state.capabilities_manager, CapabilitiesManager)
    assert (
        app.state.runner._image_generation_authority
        is app.state.capabilities_manager
    )
    assert isinstance(app.state.agents_manager, WorkspaceAgentsManager)
    assert isinstance(app.state.mcp_manager, McpManager)
    assert app.state.mcp_manager.enabled is False
    assert app.state.feature_capabilities == (
        "api_v1",
        "legacy_v0",
        "interactions_v2",
        "automations_v1",
        "skills_v1",
        "plugins_v1",
        "agents_v1",
    )
    paths = _registered_paths(app)
    assert {
        "/automations",
        "/capabilities/skills",
        "/agents/global",
        "/interactions/pending",
        "/mcp/servers",
    } <= paths

    opted_in = create_app(
        root_path=tmp_path / "opted-in-state",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
        codex_home=codex_home,
        enable_mcp=True,
    )
    assert opted_in.state.mcp_manager.enabled is True
    assert "mcp_admin_v1" in opted_in.state.feature_capabilities

    external = create_app(root_path=tmp_path / "external", auth_token="secret")
    external_paths = _registered_paths(external)
    assert external.state.automations is None
    assert external.state.capabilities_manager is None
    assert external.state.agents_manager is None
    assert external.state.mcp_manager is None
    assert external.state.feature_capabilities == (
        "api_v1",
        "legacy_v0",
    )
    assert "/automations" not in external_paths
    assert "/interactions/pending" not in external_paths


def test_home_assistant_enables_dynamic_browser_only_for_a_ready_injected_broker(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    codex_home = tmp_path / "codex-home"
    workspace_root.mkdir()
    codex_home.mkdir()

    app = create_app(
        root_path=tmp_path / "state",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
        codex_home=codex_home,
        browser_broker=_ReadyBrowserBroker(),
    )

    assert app.state.codex_app_server.enable_experimental_api is True
    assert app.state.browser_broker is not None
    assert "browser_v1" in app.state.feature_capabilities
    assert app.state.runner._browser_dynamic_tools_enabled is True


def test_home_assistant_profile_rejects_legacy_exec_runner_before_composition(
    tmp_path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    with pytest.raises(ValueError, match="unavailable in the Home Assistant"):
        create_app(
            root_path=tmp_path / "state",
            auth_token="secret",
            runtime_profile=RuntimeProfile.HOME_ASSISTANT,
            workspace_root=workspace_root,
            runner_factory=lambda storage: BridgeRunner(storage),
        )


def test_home_assistant_profile_rejects_multiple_active_turns(tmp_path) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    with pytest.raises(ValueError, match="exactly one active turn"):
        create_app(
            root_path=tmp_path / "state",
            auth_token="secret",
            runtime_profile=RuntimeProfile.HOME_ASSISTANT,
            workspace_root=workspace_root,
            resource_limits=ResourceLimits(max_active_turns=2),
        )
