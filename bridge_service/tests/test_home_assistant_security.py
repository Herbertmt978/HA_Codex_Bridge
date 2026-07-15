import ast
import json
from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPONENT_ROOT = REPO_ROOT / "custom_components" / "codex_bridge"


def test_all_home_assistant_websocket_commands_are_registered_admin_only() -> None:
    source = (COMPONENT_ROOT / "websocket_api.py").read_text(encoding="utf-8")

    assert "websocket_api.async_register_command(hass, websocket_api.require_admin(command))" in source


def test_panel_and_http_file_surfaces_require_home_assistant_admin() -> None:
    panel_source = (COMPONENT_ROOT / "panel.py").read_text(encoding="utf-8")
    assert "require_admin=True" in panel_source

    http_source = (COMPONENT_ROOT / "http.py").read_text(encoding="utf-8")
    tree = ast.parse(http_source)
    protected_methods = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, ast.AsyncFunctionDef) and item.name in {
                "delete",
                "get",
                "post",
                "put",
            }:
                protected_methods[f"{node.name}.{item.name}"] = ast.unparse(item.body[0])

    assert protected_methods == {
        "CodexBridgeAttachmentUploadView.post": "_require_admin(request)",
        "CodexBridgeUploadCreateView.post": "_require_admin(request)",
        "CodexBridgeUploadSessionView.get": "_require_admin(request)",
        "CodexBridgeUploadSessionView.delete": "_require_admin(request)",
        "CodexBridgeUploadChunkView.put": "_require_admin(request)",
        "CodexBridgeUploadCompleteView.post": "_require_admin(request)",
        "CodexBridgeArtifactDownloadView.get": "_require_admin(request)",
    }


def test_home_assistant_setup_uses_protected_side_effect_free_readiness_check() -> None:
    for filename in ("__init__.py", "config_flow.py"):
        source = (COMPONENT_ROOT / filename).read_text(encoding="utf-8")
        assert "await client.async_ready()" in source
        assert "await client.async_get_status()" not in source


def test_hacs_manifest_contains_required_repository_metadata() -> None:
    manifest = json.loads((COMPONENT_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["documentation"] == "https://github.com/Herbertmt978/HA_Codex_Bridge#readme"
    assert manifest["issue_tracker"] == "https://github.com/Herbertmt978/HA_Codex_Bridge/issues"
    assert manifest["codeowners"] == ["@Herbertmt978"]


def test_integration_and_panel_release_versions_stay_aligned() -> None:
    manifest_version = json.loads(
        (COMPONENT_ROOT / "manifest.json").read_text(encoding="utf-8")
    )["version"]
    package_version = json.loads(
        (REPO_ROOT / "package.json").read_text(encoding="utf-8")
    )["version"]

    constants = ast.parse((COMPONENT_ROOT / "const.py").read_text(encoding="utf-8"))
    asset_version = next(
        ast.literal_eval(node.value)
        for node in constants.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "PANEL_ASSET_VERSION"
            for target in node.targets
        )
    )

    panel_source = (COMPONENT_ROOT / "frontend" / "codex-bridge-panel.js").read_text(
        encoding="utf-8"
    )
    panel_version_match = re.search(
        r'^var PANEL_VERSION = "([^"]+)";$', panel_source, re.MULTILINE
    )
    assert panel_version_match is not None

    assert {
        manifest_version,
        package_version,
        asset_version,
        panel_version_match.group(1),
    } == {manifest_version}
