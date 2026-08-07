"""Linux-root regression tests for Supervisor cold-restore metadata repair."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import stat
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
INITIALIZER = (
    ROOT
    / "codex_bridge_app"
    / "rootfs"
    / "usr"
    / "local"
    / "libexec"
    / "codex-bridge"
    / "initialize_runtime.py"
)
TARGET_UID = 12345
TARGET_GID = 12345

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() != 0,
    reason="real restored root ownership requires a Linux root test process",
)


def _load_initializer():
    spec = importlib.util.spec_from_file_location("app_restore_initializer", INITIALIZER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, payload: bytes, mode: int) -> None:
    path.write_bytes(payload)
    path.chmod(mode)


def _owner(path: Path, *, follow_symlinks: bool = True) -> tuple[int, int]:
    metadata = path.stat() if follow_symlinks else path.lstat()
    return metadata.st_uid, metadata.st_gid


def test_cold_restore_repairs_only_app_owned_state_and_preserves_bytes(
    tmp_path: Path,
) -> None:
    module = _load_initializer()
    data = tmp_path / "data"
    config = tmp_path / "config"
    bridge = data / "bridge"
    codex_home = data / "codex-home"
    workspaces = config / "workspaces"
    bridge_nested = bridge / "projects"
    codex_nested = codex_home / "sessions"
    workspace_nested = workspaces / "project"
    for path in (
        bridge_nested,
        codex_nested,
        workspace_nested,
    ):
        path.mkdir(parents=True)

    bridge_file = bridge_nested / "state.json"
    codex_file = codex_nested / "session.jsonl"
    workspace_file = workspace_nested / "tool.sh"
    codex_config = codex_home / "config.toml"
    _write(bridge_file, b'{"state":"preserved"}\n', 0o640)
    _write(codex_file, b'{"session":"preserved"}\n', 0o600)
    _write(workspace_file, b"#!/bin/sh\nexit 0\n", 0o750)
    _write(codex_config, b"preserved_config = true\n", 0o644)
    bridge_nested.chmod(0o750)
    codex_nested.chmod(0o700)
    workspace_nested.chmod(0o710)

    token = data / "bridge-token"
    _write(token, b"a" * 64, 0o644)
    discovery = data / "bridge-discovery-uuid"
    options = data / "options.json"
    _write(discovery, b"root-owned-control\n", 0o600)
    _write(options, b"{}\n", 0o600)

    outside_file = tmp_path / "outside.txt"
    outside_directory = tmp_path / "outside-directory"
    outside_directory.mkdir()
    _write(outside_file, b"outside-file\n", 0o640)
    outside_nested = outside_directory / "nested.txt"
    _write(outside_nested, b"outside-directory\n", 0o640)
    file_link = bridge / "outside-file-link"
    directory_link = codex_home / "outside-directory-link"
    file_link.symlink_to(outside_file)
    directory_link.symlink_to(outside_directory, target_is_directory=True)

    preserved_modes = {
        path: stat.S_IMODE(path.stat().st_mode)
        for path in (
            bridge,
            bridge_nested,
            bridge_file,
            codex_home,
            codex_nested,
            codex_file,
            workspaces,
            workspace_nested,
            workspace_file,
        )
    }
    preserved_bytes = {
        path: path.read_bytes()
        for path in (bridge_file, codex_file, workspace_file, codex_config)
    }

    assert tuple(map(str, module.APP_OWNED_TREES)) == (
        "/data/bridge",
        "/data/codex-home",
        "/config/workspaces",
    )
    assert tuple(
        (str(path), mode) for path, mode in module.APP_OWNED_PRIVATE_FILES
    ) == (
        ("/data/bridge-token", 0o600),
        ("/data/codex-home/config.toml", 0o600),
    )
    module.APP_OWNED_TREES = (bridge, codex_home, workspaces)
    module.APP_OWNED_PRIVATE_FILES = ((token, 0o600), (codex_config, 0o600))
    module._restore_app_state(uid=TARGET_UID, gid=TARGET_GID)

    for path, mode in preserved_modes.items():
        assert _owner(path) == (TARGET_UID, TARGET_GID)
        assert stat.S_IMODE(path.stat().st_mode) == mode
    for path, payload in preserved_bytes.items():
        assert path.read_bytes() == payload
    assert token.read_bytes() == b"a" * 64
    assert _owner(token) == (TARGET_UID, TARGET_GID)
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert codex_config.read_bytes() == b"preserved_config = true\n"
    assert _owner(codex_config) == (TARGET_UID, TARGET_GID)
    assert stat.S_IMODE(codex_config.stat().st_mode) == 0o600

    assert _owner(discovery) == (0, 0)
    assert _owner(options) == (0, 0)
    assert _owner(file_link, follow_symlinks=False) == (0, 0)
    assert _owner(directory_link, follow_symlinks=False) == (0, 0)
    assert _owner(outside_file) == (0, 0)
    assert _owner(outside_nested) == (0, 0)
    assert outside_file.read_bytes() == b"outside-file\n"
    assert outside_nested.read_bytes() == b"outside-directory\n"

    # A completed repair is safe to repeat and does not require a marker.
    module._restore_app_state(uid=TARGET_UID, gid=TARGET_GID)
    assert token.read_bytes() == b"a" * 64
    assert outside_file.read_bytes() == b"outside-file\n"


def test_restore_rejects_a_mismatched_hardlink_without_chowning_its_alias(
    tmp_path: Path,
) -> None:
    module = _load_initializer()
    tree = tmp_path / "tree"
    tree.mkdir()
    outside = tmp_path / "outside"
    _write(outside, b"shared inode\n", 0o640)
    os.link(outside, tree / "alias")

    with pytest.raises(module.BootstrapError, match="unsafe hard links"):
        module._restore_tree_owner(tree, uid=TARGET_UID, gid=TARGET_GID)

    assert _owner(outside) == (0, 0)
    assert outside.read_bytes() == b"shared inode\n"


def test_restore_accepts_an_already_canonical_hardlink(tmp_path: Path) -> None:
    module = _load_initializer()
    tree = tmp_path / "tree"
    tree.mkdir()
    outside = tmp_path / "outside"
    _write(outside, b"canonical shared inode\n", 0o640)
    alias = tree / "alias"
    os.link(outside, alias)
    os.chown(outside, TARGET_UID, TARGET_GID)

    module._restore_tree_owner(tree, uid=TARGET_UID, gid=TARGET_GID)

    assert _owner(tree) == (TARGET_UID, TARGET_GID)
    assert _owner(alias) == (TARGET_UID, TARGET_GID)
    assert outside.read_bytes() == b"canonical shared inode\n"


@pytest.mark.parametrize("unsafe_kind", ["fifo", "foreign-owner"])
def test_restore_fails_closed_for_an_unsafe_restored_entry(
    tmp_path: Path, unsafe_kind: str
) -> None:
    module = _load_initializer()
    tree = tmp_path / "tree"
    tree.mkdir()
    unsafe = tree / "unsafe"
    if unsafe_kind == "fifo":
        os.mkfifo(unsafe)
        expected = "unsafe type"
    else:
        _write(unsafe, b"foreign\n", 0o600)
        os.chown(unsafe, TARGET_UID + 1, TARGET_GID + 1)
        expected = "unexpected owner"

    with pytest.raises(module.BootstrapError, match=expected):
        module._restore_tree_owner(tree, uid=TARGET_UID, gid=TARGET_GID)


def test_restore_detects_a_directory_replacement_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_initializer()
    tree = tmp_path / "tree"
    nested = tree / "nested"
    nested.mkdir(parents=True)
    moved = tree / "moved"
    original_open = module.os.open
    replaced = False

    def racing_open(path, flags, *args, dir_fd=None, **kwargs):
        nonlocal replaced
        if path == "nested" and dir_fd is not None and not replaced:
            replaced = True
            nested.rename(moved)
            nested.mkdir()
        return original_open(path, flags, *args, dir_fd=dir_fd, **kwargs)

    monkeypatch.setattr(module.os, "open", racing_open)

    with pytest.raises(module.BootstrapError, match="changed during restore repair"):
        module._restore_tree_owner(tree, uid=TARGET_UID, gid=TARGET_GID)

    assert _owner(moved) == (0, 0)
