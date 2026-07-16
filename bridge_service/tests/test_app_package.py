"""Contract tests for the Home Assistant App repository metadata."""

from __future__ import annotations

from pathlib import Path
import struct

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "codex_bridge_app"
BRAND_ROOT = ROOT / "brand"
INTEGRATION_BRAND_ROOT = ROOT / "custom_components" / "codex_bridge" / "brand"


def _yaml(path: Path) -> dict[str, object]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict), f"{path} must contain a YAML mapping"
    return value


def test_repository_manifest_points_to_the_public_app_repository() -> None:
    manifest = _yaml(ROOT / "repository.yaml")

    assert manifest == {
        "name": "Home Assistant Codex Bridge",
        "url": "https://github.com/Herbertmt978/HA_Codex_Bridge",
        "maintainer": "Herbertmt978",
    }


def test_app_metadata_is_immutable_and_discovered_by_the_integration() -> None:
    config = _yaml(APP_ROOT / "config.yaml")

    assert config["name"] == "Codex Bridge"
    assert config["description"] == (
        "Run the Codex Bridge service privately inside Home Assistant."
    )
    assert config["slug"] == "codex_bridge"
    assert config["version"] == "0.7.3"
    assert config.get("startup", "application") == "application"
    assert config.get("boot", "auto") == "auto"
    assert config["init"] is False
    assert config["stage"] == "experimental"
    assert config["backup"] == "cold"
    assert config["arch"] == ["amd64"]
    assert config["image"] == "ghcr.io/herbertmt978/ha-codex-bridge-app"
    assert config["discovery"] == ["codex_bridge"]
    assert config["map"] == ["app_config:rw"]
    assert config["options"] == {"enable_mcp": False}
    assert config["schema"] == {"enable_mcp": "bool"}


@pytest.mark.parametrize("default_field", ["apparmor", "boot", "startup"])
def test_app_metadata_omits_linter_rejected_defaults(default_field: str) -> None:
    config = _yaml(APP_ROOT / "config.yaml")
    assert default_field not in config


@pytest.mark.parametrize(
    "forbidden",
    [
        "ports",
        "ports_description",
        "ingress",
        "ingress_port",
        "ingress_entry",
        "host_network",
        "host_pid",
        "host_ipc",
        "privileged",
        "full_access",
        "docker",
        "docker_api",
        "devices",
        "usb",
        "hassio_api",
        "hassio_role",
        "auth_api",
        "homeassistant_api",
        "homeassistant_config",
        "all_addon_configs",
        "share",
        "media",
        "backup_exclude",
    ],
)
def test_app_does_not_request_broad_supervisor_capabilities(forbidden: str) -> None:
    config = _yaml(APP_ROOT / "config.yaml")
    assert forbidden not in config


def test_app_package_has_no_legacy_build_file_and_contains_required_docs() -> None:
    assert not (APP_ROOT / "build.yaml").exists()
    for filename in ("README.md", "DOCS.md", "CHANGELOG.md", "apparmor.txt"):
        path = APP_ROOT / filename
        assert path.is_file()
        assert path.read_text(encoding="utf-8").strip()


def test_apparmor_exposes_only_the_dedicated_workspace_mapping() -> None:
    profile = (APP_ROOT / "apparmor.txt").read_text(encoding="utf-8")

    assert "/config/ r," in profile
    assert "/config/workspaces/ rw," in profile
    assert "/config/workspaces/** rwk," in profile
    assert "/config/**" not in profile


def test_app_branding_assets_are_present() -> None:
    expected = {"icon.png": (256, 256), "logo.png": (1024, 256)}
    for filename, dimensions in expected.items():
        source = BRAND_ROOT / filename
        payload = source.read_bytes()
        assert payload.startswith(b"\x89PNG\r\n\x1a\n")
        assert struct.unpack(">II", payload[16:24]) == dimensions
        assert (APP_ROOT / filename).read_bytes() == payload
        assert (INTEGRATION_BRAND_ROOT / filename).read_bytes() == payload

    social = (BRAND_ROOT / "social-preview.png").read_bytes()
    assert social.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", social[16:24]) == (1280, 640)
    for filename in ("icon.svg", "logo.svg", "social-preview.svg"):
        source = BRAND_ROOT / filename
        assert source.is_file()
        assert "<svg" in source.read_text(encoding="utf-8")


def test_translation_uses_the_supported_empty_shape() -> None:
    translations = _yaml(APP_ROOT / "translations" / "en.yaml")
    assert translations == {}
