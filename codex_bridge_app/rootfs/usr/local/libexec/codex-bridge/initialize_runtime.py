#!/usr/bin/env python3
"""Create the App's private runtime state without following symlinks."""

from __future__ import annotations

import grp
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import sys
import tomllib


DIRECTORY_FLAGS = (
    os.O_RDONLY
    | os.O_DIRECTORY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
FILE_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
REPAIR_FILE_FLAGS = FILE_FLAGS | getattr(os, "O_NONBLOCK", 0)
TOKEN_PATTERN = re.compile(rb"[A-Za-z0-9_-]{64}")
RESTORED_OWNER = (0, 0)
RESTORE_MAXIMUM_DEPTH = 256
RESTORE_MAXIMUM_ENTRIES = 250_000
APP_OWNED_TREES = (
    Path("/data/bridge"),
    Path("/data/codex-home"),
    Path("/config/workspaces"),
)
BRIDGE_TOKEN_PATH = Path("/data/bridge-token")
CODEX_CONFIG_PATH = Path("/data/codex-home/config.toml")
APP_OWNED_PRIVATE_FILES = (
    (BRIDGE_TOKEN_PATH, 0o600),
    (CODEX_CONFIG_PATH, 0o600),
)
CONFIG_PAYLOAD = b"""cli_auth_credentials_store = "file"
default_permissions = "ha_bridge"

[permissions.ha_observe]
description = "Home Assistant read-only workspace sandbox"

[permissions.ha_observe.filesystem]
":minimal" = "read"

[permissions.ha_observe.filesystem.":workspace_roots"]
"." = "read"

[permissions.ha_observe.network]
enabled = false
allow_local_binding = false
allow_upstream_proxy = false

[permissions.ha_bridge]
description = "Home Assistant workspace-only sandbox"

[permissions.ha_bridge.filesystem]
":minimal" = "read"

[permissions.ha_bridge.filesystem.":workspace_roots"]
"." = "write"
".codex" = "write"
".git" = "write"
".agents" = "write"
".cursor" = "write"
".vscode" = "write"

[permissions.ha_bridge.network]
enabled = false
allow_local_binding = false
allow_upstream_proxy = false
"""

_MANAGED_PERMISSION_PROFILES = frozenset({"ha_observe", "ha_bridge"})


def _managed_config_is_safe(payload: bytes) -> bool:
    """Validate the security-owned settings without rejecting Codex extensions.

    Codex owns the MCP, plugin, marketplace, and skill tables in this file.  The
    App owns credential storage and both permission profiles.  Parsing the TOML
    is important: a textual line-presence check cannot prove which table a key
    belongs to and does not reject duplicate or otherwise invalid TOML.
    """

    try:
        config = tomllib.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    if config.get("cli_auth_credentials_store") != "file":
        return False
    if config.get("default_permissions") != "ha_bridge":
        return False
    permissions = config.get("permissions")
    if (
        not isinstance(permissions, dict)
        or set(permissions) != _MANAGED_PERMISSION_PROFILES
    ):
        return False
    expected = {
        "ha_observe": {
            "description": "Home Assistant read-only workspace sandbox",
            "filesystem": {
                ":minimal": "read",
                ":workspace_roots": {".": "read"},
            },
            "network": {
                "enabled": False,
                "allow_local_binding": False,
                "allow_upstream_proxy": False,
            },
        },
        "ha_bridge": {
            "description": "Home Assistant workspace-only sandbox",
            "filesystem": {
                ":minimal": "read",
                ":workspace_roots": {
                    ".": "write",
                    ".codex": "write",
                    ".git": "write",
                    ".agents": "write",
                    ".cursor": "write",
                    ".vscode": "write",
                },
            },
            "network": {
                "enabled": False,
                "allow_local_binding": False,
                "allow_upstream_proxy": False,
            },
        },
    }
    return permissions == expected


class BootstrapError(RuntimeError):
    """Persistent App state is unsafe or cannot be initialized."""


def _same_inode(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        first.st_dev == second.st_dev
        and first.st_ino == second.st_ino
        and stat.S_IFMT(first.st_mode) == stat.S_IFMT(second.st_mode)
    )


def _owner_needs_restore(
    metadata: os.stat_result, *, uid: int, gid: int
) -> bool:
    owner = (metadata.st_uid, metadata.st_gid)
    if owner == (uid, gid):
        return False
    if owner != RESTORED_OWNER:
        raise BootstrapError("private runtime entry has an unexpected owner")
    return True


def _restore_descriptor_owner(
    descriptor: int,
    metadata: os.stat_result,
    *,
    uid: int,
    gid: int,
    regular_file: bool,
) -> None:
    if not _owner_needs_restore(metadata, uid=uid, gid=gid):
        return
    if regular_file and metadata.st_nlink != 1:
        raise BootstrapError("restored private runtime file has unsafe hard links")
    mode = stat.S_IMODE(metadata.st_mode)
    os.fchown(descriptor, uid, gid)
    # chown may clear set-ID mode bits, so retain the restored ordinary mode.
    os.fchmod(descriptor, mode)
    verified = os.fstat(descriptor)
    if (
        not _same_inode(metadata, verified)
        or verified.st_uid != uid
        or verified.st_gid != gid
        or stat.S_IMODE(verified.st_mode) != mode
    ):
        raise BootstrapError("private runtime ownership repair could not be verified")


def _restore_regular_owner(
    parent_descriptor: int,
    name: str,
    expected: os.stat_result,
    *,
    root_device: int,
    uid: int,
    gid: int,
) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            name, REPAIR_FILE_FLAGS, dir_fd=parent_descriptor
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_dev != root_device
            or not _same_inode(expected, metadata)
        ):
            raise BootstrapError("private runtime file changed during restore repair")
        _restore_descriptor_owner(
            descriptor,
            metadata,
            uid=uid,
            gid=gid,
            regular_file=True,
        )
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _restore_directory_contents(
    descriptor: int, *, root_device: int, uid: int, gid: int
) -> None:
    visited = 0
    stack = [(descriptor, os.scandir(descriptor), False, 0)]
    try:
        while stack:
            current_descriptor, entries, owned_descriptor, depth = stack[-1]
            try:
                entry = next(entries)
            except StopIteration:
                entries.close()
                stack.pop()
                if owned_descriptor:
                    os.close(current_descriptor)
                continue

            visited += 1
            if visited > RESTORE_MAXIMUM_ENTRIES:
                raise BootstrapError("private runtime tree exceeds its entry limit")
            metadata = os.stat(
                entry.name,
                dir_fd=current_descriptor,
                follow_symlinks=False,
            )
            if metadata.st_dev != root_device:
                raise BootstrapError("private runtime tree crosses a filesystem boundary")
            if stat.S_ISLNK(metadata.st_mode):
                # Symlink ownership is irrelevant inside these private parent
                # directories.  Leaving it untouched avoids any target race.
                continue
            if stat.S_ISREG(metadata.st_mode):
                _restore_regular_owner(
                    current_descriptor,
                    entry.name,
                    metadata,
                    root_device=root_device,
                    uid=uid,
                    gid=gid,
                )
                continue
            if stat.S_ISDIR(metadata.st_mode):
                child_depth = depth + 1
                if child_depth > RESTORE_MAXIMUM_DEPTH:
                    raise BootstrapError("private runtime tree exceeds its depth limit")
                _owner_needs_restore(metadata, uid=uid, gid=gid)
                child_descriptor = -1
                try:
                    child_descriptor = os.open(
                        entry.name,
                        DIRECTORY_FLAGS,
                        dir_fd=current_descriptor,
                    )
                    opened = os.fstat(child_descriptor)
                    if (
                        not stat.S_ISDIR(opened.st_mode)
                        or opened.st_dev != root_device
                        or not _same_inode(metadata, opened)
                    ):
                        raise BootstrapError(
                            "private runtime directory changed during restore repair"
                        )
                    _restore_descriptor_owner(
                        child_descriptor,
                        opened,
                        uid=uid,
                        gid=gid,
                        regular_file=False,
                    )
                    child_entries = os.scandir(child_descriptor)
                    stack.append(
                        (
                            child_descriptor,
                            child_entries,
                            True,
                            child_depth,
                        )
                    )
                    child_descriptor = -1
                finally:
                    if child_descriptor >= 0:
                        os.close(child_descriptor)
                continue
            if _owner_needs_restore(metadata, uid=uid, gid=gid):
                raise BootstrapError("restored private runtime entry has an unsafe type")
    finally:
        while stack:
            current_descriptor, entries, owned_descriptor, _depth = stack.pop()
            entries.close()
            if owned_descriptor:
                os.close(current_descriptor)


def _restore_tree_owner(path: Path, *, uid: int, gid: int) -> None:
    parent_descriptor = os.open(path.parent, DIRECTORY_FLAGS)
    descriptor = -1
    try:
        expected = os.stat(
            path.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        descriptor = os.open(path.name, DIRECTORY_FLAGS, dir_fd=parent_descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or not _same_inode(expected, metadata):
            raise BootstrapError("private runtime tree changed during restore repair")
        _owner_needs_restore(metadata, uid=uid, gid=gid)
        _restore_directory_contents(
            descriptor,
            root_device=metadata.st_dev,
            uid=uid,
            gid=gid,
        )
        _restore_descriptor_owner(
            descriptor,
            metadata,
            uid=uid,
            gid=gid,
            regular_file=False,
        )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)


def _restore_private_file(
    parent: Path,
    name: str,
    *,
    mode: int,
    uid: int,
    gid: int,
) -> None:
    parent_descriptor = os.open(parent, DIRECTORY_FLAGS)
    descriptor = -1
    try:
        descriptor = os.open(name, REPAIR_FILE_FLAGS, dir_fd=parent_descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise BootstrapError("restored private runtime file is unsafe")
        if _owner_needs_restore(metadata, uid=uid, gid=gid):
            os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, mode)
        verified = os.fstat(descriptor)
        if (
            not _same_inode(metadata, verified)
            or verified.st_uid != uid
            or verified.st_gid != gid
            or stat.S_IMODE(verified.st_mode) != mode
        ):
            raise BootstrapError("private runtime file repair could not be verified")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)


def _secure_directory(path: Path, *, mode: int, uid: int, gid: int) -> None:
    parent_descriptor = os.open(path.parent, DIRECTORY_FLAGS)
    descriptor = -1
    try:
        try:
            os.mkdir(path.name, mode, dir_fd=parent_descriptor)
        except FileExistsError:
            pass
        descriptor = os.open(path.name, DIRECTORY_FLAGS, dir_fd=parent_descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise BootstrapError("private runtime path is not a directory")
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, mode)
        metadata = os.fstat(descriptor)
        if (
            metadata.st_uid != uid
            or metadata.st_gid != gid
            or stat.S_IMODE(metadata.st_mode) != mode
        ):
            raise BootstrapError("private runtime directory permissions are unsafe")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)


def _atomic_write(
    parent: Path,
    name: str,
    payload: bytes,
    *,
    mode: int,
    uid: int,
    gid: int,
) -> None:
    parent_descriptor = os.open(parent, DIRECTORY_FLAGS)
    temporary_name = f".{name}.tmp-{secrets.token_hex(8)}"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            mode,
            dir_fd=parent_descriptor,
        )
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise BootstrapError("private runtime file write failed")
            view = view[written:]
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temporary_name,
            name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        os.fsync(parent_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass
        os.close(parent_descriptor)


def _read_private_file(
    parent: Path,
    name: str,
    *,
    mode: int,
    uid: int,
    gid: int,
    maximum: int,
) -> bytes:
    parent_descriptor = os.open(parent, DIRECTORY_FLAGS)
    descriptor = -1
    try:
        descriptor = os.open(name, FILE_FLAGS, dir_fd=parent_descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != uid
            or metadata.st_gid != gid
            or stat.S_IMODE(metadata.st_mode) != mode
            or metadata.st_size <= 0
            or metadata.st_size > maximum
        ):
            raise BootstrapError("private runtime file permissions are unsafe")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise BootstrapError("private runtime file changed while reading")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise BootstrapError("private runtime file exceeded its recorded size")
        return b"".join(chunks)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)


def _exists_nofollow(parent: Path, name: str) -> bool:
    parent_descriptor = os.open(parent, DIRECTORY_FLAGS)
    try:
        try:
            os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True
    finally:
        os.close(parent_descriptor)


def _restore_app_state(*, uid: int, gid: int) -> None:
    for path in APP_OWNED_TREES:
        _restore_tree_owner(path, uid=uid, gid=gid)
    for path, mode in APP_OWNED_PRIVATE_FILES:
        if _exists_nofollow(path.parent, path.name):
            _restore_private_file(
                path.parent,
                path.name,
                mode=mode,
                uid=uid,
                gid=gid,
            )


def initialize() -> None:
    account = pwd.getpwnam("codexbridge")
    group = grp.getgrnam("codexbridge")
    uid, gid = account.pw_uid, group.gr_gid

    _secure_directory(Path("/data"), mode=0o750, uid=0, gid=gid)
    for path in (Path("/data/bridge"), Path("/data/codex-home")):
        _secure_directory(path, mode=0o700, uid=uid, gid=gid)
    _secure_directory(Path("/config/workspaces"), mode=0o700, uid=uid, gid=gid)
    _secure_directory(Path("/tmp/codex-bridge"), mode=0o700, uid=uid, gid=gid)
    _secure_directory(Path("/run/codex-bridge"), mode=0o750, uid=0, gid=gid)

    # Supervisor restores App backup contents as root.  Reconcile only the
    # trees this App later uses as its non-root runtime; /data itself and the
    # root-owned discovery identity deliberately stay outside this allowlist.
    _restore_app_state(uid=uid, gid=gid)

    token_parent = BRIDGE_TOKEN_PATH.parent
    token_name = BRIDGE_TOKEN_PATH.name
    if not _exists_nofollow(token_parent, token_name):
        token = secrets.token_urlsafe(48).encode("ascii")
        if TOKEN_PATTERN.fullmatch(token) is None:
            raise BootstrapError("generated credential has an invalid shape")
        _atomic_write(
            token_parent,
            token_name,
            token,
            mode=0o600,
            uid=uid,
            gid=gid,
        )
    token = _read_private_file(
        token_parent,
        token_name,
        mode=0o600,
        uid=uid,
        gid=gid,
        maximum=512,
    )
    if TOKEN_PATTERN.fullmatch(token) is None:
        raise BootstrapError("stored credential has an invalid shape")

    config_parent = CODEX_CONFIG_PATH.parent
    config_name = CODEX_CONFIG_PATH.name
    if not _exists_nofollow(config_parent, config_name):
        _atomic_write(
            config_parent,
            config_name,
            CONFIG_PAYLOAD,
            mode=0o600,
            uid=uid,
            gid=gid,
        )
    config = _read_private_file(
        config_parent,
        config_name,
        mode=0o600,
        uid=uid,
        gid=gid,
        maximum=1024 * 1024,
    )
    if not _managed_config_is_safe(config):
        _atomic_write(
            config_parent,
            config_name,
            CONFIG_PAYLOAD,
            mode=0o600,
            uid=uid,
            gid=gid,
        )
        config = _read_private_file(
            config_parent,
            config_name,
            mode=0o600,
            uid=uid,
            gid=gid,
            maximum=1024 * 1024,
        )
        if not _managed_config_is_safe(config):
            raise BootstrapError("managed Codex configuration could not be verified")


def main() -> int:
    try:
        initialize()
    except Exception:
        print("Private Codex Bridge runtime initialization failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
