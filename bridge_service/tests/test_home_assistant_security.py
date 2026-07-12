import ast
import json
from pathlib import Path


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
            if isinstance(item, ast.AsyncFunctionDef) and item.name in {"get", "post"}:
                protected_methods[f"{node.name}.{item.name}"] = ast.unparse(item.body[0])

    assert protected_methods == {
        "CodexBridgeAttachmentUploadView.post": "_require_admin(request)",
        "CodexBridgeArtifactDownloadView.get": "_require_admin(request)",
    }


def test_home_assistant_setup_uses_protected_side_effect_free_readiness_check() -> None:
    for filename in ("__init__.py", "config_flow.py"):
        source = (COMPONENT_ROOT / filename).read_text(encoding="utf-8")
        assert "await client.async_ready()" in source
        assert "await client.async_get_status()" not in source


def test_hacs_manifest_contains_required_repository_metadata() -> None:
    manifest = json.loads((COMPONENT_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["documentation"] == "https://github.com/Herbertmt978/ha-codex-bridge#readme"
    assert manifest["issue_tracker"] == "https://github.com/Herbertmt978/ha-codex-bridge/issues"
    assert manifest["codeowners"] == ["@Herbertmt978"]
