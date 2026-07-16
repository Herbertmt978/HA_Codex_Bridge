"""Focused tests for the App release projection synchronizer."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "sync_app_release.py"
APP = ROOT / "codex_bridge_app"


def _canonical_versions(root: Path = ROOT) -> tuple[str, str, str]:
    config = (root / "codex_bridge_app/config.yaml").read_text(encoding="utf-8")
    app_match = re.search(
        r"(?m)^version\s*:\s*[\"']?([^\"'\s#]+)", config
    )
    assert app_match is not None
    lock = json.loads(
        (root / "codex_bridge_app/codex-release.json").read_text(encoding="utf-8")
    )
    bridge = tomllib.loads(
        (root / "bridge_service/pyproject.toml").read_text(encoding="utf-8")
    )
    return app_match.group(1), lock["release"]["version"], bridge["project"]["version"]


def _next_patch(version: str) -> str:
    major, minor, patch = version.split(".")
    return f"{major}.{minor}.{int(patch) + 1}"


def _script_module():
    spec = importlib.util.spec_from_file_location("sync_app_release_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fixture(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    app = root / "codex_bridge_app"
    for relative in (
        "config.yaml",
        "codex-release.json",
        "Dockerfile",
        "CHANGELOG.md",
        "rootfs/etc/s6-overlay/s6-rc.d/codex-bridge/run",
    ):
        source = APP / relative
        target = app / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    bridge_project = root / "bridge_service" / "pyproject.toml"
    bridge_project.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "bridge_service" / "pyproject.toml", bridge_project)
    return root


def _run(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_real_repository_release_projections_are_current() -> None:
    result = _run(ROOT, "--check")
    assert result.returncode == 0, result.stderr
    assert "0.5.3" not in SCRIPT.read_text(encoding="utf-8")


def test_bump_patch_updates_only_managed_app_projection_files(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    app_version, codex_version, bridge_version = _canonical_versions()
    next_app_version = _next_patch(app_version)
    untouched = {
        "package.json": (ROOT / "package.json").read_bytes(),
        "bridge_version": (
            ROOT / "bridge_service/src/codex_bridge_service/build_info.py"
        ).read_bytes(),
    }

    result = _run(root, "--bump-patch")
    assert result.returncode == 0, result.stderr
    assert _run(root, "--check").returncode == 0

    config = (root / "codex_bridge_app/config.yaml").read_text(encoding="utf-8")
    dockerfile = (root / "codex_bridge_app/Dockerfile").read_text(encoding="utf-8")
    run = (
        root / "codex_bridge_app/rootfs/etc/s6-overlay/s6-rc.d/codex-bridge/run"
    ).read_text(encoding="utf-8")
    changelog = (root / "codex_bridge_app/CHANGELOG.md").read_text(encoding="utf-8")
    assert f'version: "{next_app_version}"' in config
    assert f'io.hass.version="{next_app_version}"' in dockerfile
    assert f'CODEX_BRIDGE_APP_VERSION="{next_app_version}"' in dockerfile
    assert f"CODEX_BRIDGE_APP_VERSION={next_app_version}" in run
    assert changelog.index(f"## {next_app_version}") < changelog.index(
        f"## {app_version}"
    )
    assert f"`{codex_version}`" in changelog
    assert "- Bundles the Sigstore-verified Codex runtime" in changelog
    assert "Updates the Sigstore-verified bundled Codex runtime" not in changelog
    assert f'CODEX_BRIDGE_VERSION="{bridge_version}"' in dockerfile
    assert f"CODEX_BRIDGE_VERSION={bridge_version}" in run
    assert (ROOT / "package.json").read_bytes() == untouched["package.json"]
    assert (
        ROOT / "bridge_service/src/codex_bridge_service/build_info.py"
    ).read_bytes() == untouched["bridge_version"]


def test_check_fails_on_projection_drift_without_writing(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    app_version, _codex_version, _bridge_version = _canonical_versions()
    path = root / "codex_bridge_app/Dockerfile"
    original = path.read_bytes()
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            f'io.hass.version="{app_version}"', 'io.hass.version="0.7.9"'
        ),
        encoding="utf-8",
    )

    result = _run(root, "--check")
    assert result.returncode != 0
    assert "drift" in result.stderr
    assert path.read_bytes() != original


def test_check_fails_when_bundled_bridge_version_drifts(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    _app_version, _codex_version, bridge_version = _canonical_versions()
    project = root / "bridge_service" / "pyproject.toml"
    project.write_text(
        project.read_text(encoding="utf-8").replace(
            f'version = "{bridge_version}"', 'version = "0.6.3"'
        ),
        encoding="utf-8",
    )

    result = _run(root, "--check")
    assert result.returncode != 0
    assert "bridge version" in result.stderr.lower()


def test_malformed_or_ambiguous_sources_are_rejected(tmp_path: Path) -> None:
    module = _script_module()
    root = _fixture(tmp_path)
    config = root / "codex_bridge_app/config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8") + 'version: "0.6.0"\n', encoding="utf-8"
    )
    with pytest.raises(module.ReleaseSyncError, match="duplicate|exactly one"):
        module.synchronize(root, mode="check")

    root = _fixture(tmp_path / "prerelease")
    app_version, _codex_version, _bridge_version = _canonical_versions()
    config = root / "codex_bridge_app/config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            f'version: "{app_version}"', f'version: "{app_version}-rc.1"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(module.ReleaseSyncError, match="semver"):
        module.synchronize(root, mode="check")

    root = _fixture(tmp_path / "noncanonical")
    lock = root / "codex_bridge_app/codex-release.json"
    lock.write_bytes(lock.read_bytes().replace(b"\n", b"\r\n"))
    with pytest.raises(module.ReleaseSyncError, match="LF|canonical"):
        module.synchronize(root, mode="check")


def test_symlinked_managed_file_is_rejected(tmp_path: Path) -> None:
    module = _script_module()
    root = _fixture(tmp_path)
    target = root / "codex_bridge_app/Dockerfile"
    backup = root / "codex_bridge_app/Dockerfile.real"
    target.rename(backup)
    try:
        target.symlink_to(backup)
    except (OSError, NotImplementedError):
        backup.rename(target)
        pytest.skip("symlink creation is unavailable on this platform")
    with pytest.raises(module.ReleaseSyncError, match="symlink"):
        module.synchronize(root, mode="check")
