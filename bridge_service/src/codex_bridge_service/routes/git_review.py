"""Read-only, workspace-confined Git review endpoints."""

from __future__ import annotations

import os
import hashlib
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path, PurePosixPath
from typing import Iterator, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..auth import require_bridge_token
from ..storage import ThreadNotFoundError
from ..workspace import WorkspaceBoundaryError, WorkspaceNotFoundError

router = APIRouter()

Scope = Literal["unstaged", "staged", "commit", "branch"]
_MAX_FILES = 200
_MAX_PATCH_BYTES = 48 * 1024
_MAX_DIFF_TIMEOUT = 20
_MAX_LIST_BYTES = 1024 * 1024
_MAX_METADATA_BYTES = 256 * 1024 * 1024
_MAX_METADATA_FILES = 50_000
_MAX_WORKTREE_HASH_BYTES = 256 * 1024 * 1024
_MAX_REF_INDEX_BYTES = 16 * 1024 * 1024
_GIT_DIR: ContextVar[Path | None] = ContextVar("git_review_dir", default=None)
_GIT_DEADLINE: ContextVar[float | None] = ContextVar("git_review_deadline", default=None)
_WORKSPACE_FD: ContextVar[int | None] = ContextVar("git_review_workspace_fd", default=None)
_WORKTREE_HASH_BYTES: ContextVar[int] = ContextVar("git_review_hash_bytes", default=0)


class GitReviewFile(BaseModel):
    path: str
    status: str
    additions: int | None = None
    deletions: int | None = None
    binary: bool = False
    large: bool = False
    patch: str | None = None
    patch_truncated: bool = False


class GitReviewResponse(BaseModel):
    scope: Scope
    base_ref: str | None = None
    head_ref: str | None = None
    files: list[GitReviewFile] = Field(default_factory=list)
    files_truncated: bool = False
    patch_available: bool = True
    state_token: str


class GitReviewError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


def _git(workspace: Path, *args: str, timeout: int = 3) -> bytes:
    command = _git_prefix(workspace) + ["-C", _git_worktree_path(workspace), *args]
    raw, truncated = _read_bounded_process(
        command, workspace, max_bytes=_MAX_LIST_BYTES, timeout=timeout
    )
    if truncated:
        raise GitReviewError("git_output_too_large")
    return raw


def _git_environment() -> dict[str, str]:
    """Discard inherited Git overrides and isolate all non-repository config."""
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "GIT_ATTR_NOSYSTEM": "1",
        }
    )
    return env


def _git_prefix(workspace: Path) -> list[str]:
    """Use the request's private sanitized Git metadata snapshot."""
    git_dir = _GIT_DIR.get()
    if git_dir is None:
        raise GitReviewError("git_snapshot_unavailable")
    return [
        "git", "--no-pager", f"--git-dir={git_dir}",
        f"--work-tree={_git_worktree_path(workspace)}", "-c", "core.fsmonitor=false",
        "-c", "core.untrackedCache=false", "-c", f"core.hooksPath={os.devnull}",
        "-c", f"core.attributesFile={os.devnull}",
    ]


def _git_worktree_path(workspace: Path) -> str:
    descriptor = _WORKSPACE_FD.get()
    if descriptor is not None:
        return f"/proc/self/fd/{descriptor}"
    return str(workspace)


def _open_workspace_directory(workspace: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(workspace.anchor, flags)
    try:
        for part in workspace.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def _private_git_snapshot(workspace: Path) -> Iterator[None]:
    """Copy bounded Git state without config, hooks, attributes or alternates."""
    deadline = time.monotonic() + _MAX_DIFF_TIMEOUT
    workspace = workspace.resolve(strict=True)
    snapshot_root = Path(tempfile.mkdtemp(prefix="codex-git-review-"))
    git_dir = snapshot_root / ".git"
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    directory_flags = flags | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    workspace_fd = None
    metadata_fd = None
    try:
        git_dir.mkdir(mode=0o700)
        if os.name == "nt":
            _copy_git_metadata_windows(workspace, git_dir, deadline)
            (git_dir / "config").write_text(
                "[core]\n\trepositoryformatversion = 0\n\tbare = false\n",
                encoding="ascii",
            )
            git_token = _GIT_DIR.set(git_dir)
            deadline_token = _GIT_DEADLINE.set(deadline)
            hash_budget_token = _WORKTREE_HASH_BYTES.set(0)
            try:
                yield
            finally:
                _WORKTREE_HASH_BYTES.reset(hash_budget_token)
                _GIT_DEADLINE.reset(deadline_token)
                _GIT_DIR.reset(git_token)
            return
        try:
            workspace_fd = _open_workspace_directory(workspace)
        except OSError as error:
            raise GitReviewError("git_workspace_unavailable") from error
        workspace_token = _WORKSPACE_FD.set(workspace_fd)
        try:
            metadata_fd = os.open(".git", directory_flags, dir_fd=workspace_fd)
        except OSError as error:
            raise GitReviewError("git_metadata_unsupported") from error
        names = {entry.name for entry in os.scandir(metadata_fd)}
        if "objects" not in names or "HEAD" not in names:
            raise GitReviewError("git_metadata_unsupported")
        if "commondir" in names or "gitdir" in names:
            raise GitReviewError("git_metadata_unsupported")
        _validate_git_format_fd(metadata_fd)
        source_state_before = _metadata_fingerprint_fd(metadata_fd, deadline)
        objects_fd = None
        try:
            objects_fd = os.open("objects", directory_flags, dir_fd=metadata_fd)
        except FileNotFoundError:
            pass
        if objects_fd is not None:
            try:
                info_fd = None
                try:
                    info_fd = os.open("info", directory_flags, dir_fd=objects_fd)
                except FileNotFoundError:
                    pass
                except OSError as error:
                    raise GitReviewError("git_metadata_unsafe") from error
                if info_fd is not None:
                    try:
                        if "alternates" in {entry.name for entry in os.scandir(info_fd)}:
                            raise GitReviewError("git_alternates_unsupported")
                    finally:
                        os.close(info_fd)
            finally:
                os.close(objects_fd)
        budget = {"bytes": 0, "files": 0}
        for filename in ("HEAD", "index", "packed-refs", "shallow"):
            _copy_metadata_file(metadata_fd, git_dir, filename, budget, deadline, optional=True)
        for dirname in ("refs", "objects"):
            _copy_metadata_tree(metadata_fd, git_dir, dirname, budget, deadline)
        source_state_after = _metadata_fingerprint_fd(metadata_fd, deadline)
        snapshot_state = _metadata_fingerprint_path(git_dir, deadline)
        if source_state_before != source_state_after or source_state_before != snapshot_state:
            raise GitReviewError("git_state_changed")
        (git_dir / "config").write_text(
            "[core]\n\trepositoryformatversion = 0\n\tbare = false\n",
            encoding="ascii",
        )
        _GIT_DIR_TOKEN = _GIT_DIR.set(git_dir)
        deadline_token = _GIT_DEADLINE.set(deadline)
        hash_budget_token = _WORKTREE_HASH_BYTES.set(0)
        try:
            yield
        finally:
            _WORKTREE_HASH_BYTES.reset(hash_budget_token)
            _GIT_DEADLINE.reset(deadline_token)
            _GIT_DIR.reset(_GIT_DIR_TOKEN)
            _WORKSPACE_FD.reset(workspace_token)
    except GitReviewError:
        raise
    except OSError as error:
        raise GitReviewError("git_metadata_unsafe") from error
    finally:
        if metadata_fd is not None:
            os.close(metadata_fd)
        if workspace_fd is not None:
            if _WORKSPACE_FD.get() == workspace_fd:
                _WORKSPACE_FD.set(None)
            os.close(workspace_fd)
        shutil.rmtree(snapshot_root, ignore_errors=True)


def _copy_git_metadata_windows(workspace: Path, git_dir: Path, deadline: float) -> None:
    source = workspace / ".git"
    info = source.lstat()
    reparse = bool(getattr(info, "st_file_attributes", 0) & 0x400)
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or reparse:
        raise GitReviewError("git_metadata_unsupported")
    _validate_git_format_path(source / "config")
    names = {entry.name for entry in source.iterdir()}
    if "objects" not in names or "HEAD" not in names or "commondir" in names:
        raise GitReviewError("git_metadata_unsupported")
    alternates = source / "objects" / "info" / "alternates"
    if alternates.exists() or alternates.is_symlink():
        raise GitReviewError("git_alternates_unsupported")
    source_state_before = _metadata_fingerprint_path(source, deadline)
    budget = {"bytes": 0, "files": 0}
    for name in ("HEAD", "index", "packed-refs", "shallow"):
        _copy_metadata_path(source, git_dir, name, budget, deadline, optional=True)
    for name in ("refs", "objects"):
        _copy_metadata_tree_path(source / name, git_dir / name, budget, deadline)
    source_state_after = _metadata_fingerprint_path(source, deadline)
    snapshot_state = _metadata_fingerprint_path(git_dir, deadline)
    if source_state_before != source_state_after or source_state_before != snapshot_state:
        raise GitReviewError("git_state_changed")


def _validate_git_format_fd(metadata_fd: int) -> None:
    try:
        descriptor = os.open("config", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=metadata_fd)
    except FileNotFoundError:
        return
    except OSError as error:
        raise GitReviewError("git_metadata_unsafe") from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
            raise GitReviewError("git_metadata_unsupported")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            _validate_git_format_bytes(stream.read(64 * 1024 + 1))
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_git_format_path(path: Path) -> None:
    try:
        before = _check_metadata_path(path, directory=False)
    except FileNotFoundError:
        return
    if before.st_size > 64 * 1024:
        raise GitReviewError("git_metadata_unsupported")
    with path.open("rb") as stream:
        after = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise GitReviewError("git_metadata_changed")
        _validate_git_format_bytes(stream.read(64 * 1024 + 1))


def _validate_git_format_bytes(raw: bytes) -> None:
    section = ""
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = re.sub(r"[\s\".]", "", line[1:-1]).casefold()
            continue
        if section != "extensions" or "=" not in line:
            continue
        key, value = (part.strip().casefold() for part in line.split("=", 1))
        if key == "objectformat" and value != "sha1":
            raise GitReviewError("git_object_format_unsupported")
        if key == "refstorage" and value != "files":
            raise GitReviewError("git_ref_storage_unsupported")


def _metadata_fingerprint_fd(root_fd: int, deadline: float) -> bytes:
    digest = hashlib.sha256()
    budget = {"bytes": 0, "files": 0}
    for name in ("HEAD", "index", "packed-refs", "shallow"):
        _hash_metadata_file_fd(root_fd, name, digest, budget, deadline, optional=True)
    try:
        refs_fd = os.open("refs", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
    except FileNotFoundError:
        digest.update(b"refs:absent")
    else:
        try:
            _hash_metadata_directory_fd(refs_fd, "refs", digest, budget, deadline)
        finally:
            os.close(refs_fd)
    return digest.digest()


def _hash_metadata_file_fd(parent_fd: int, name: str, digest, budget, deadline,
                           *, optional: bool = False, display_name: str | None = None) -> None:
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except FileNotFoundError:
        if optional:
            digest.update(f"{name}:absent".encode())
            return
        raise GitReviewError("git_metadata_unsafe") from None
    except OSError as error:
        raise GitReviewError("git_metadata_unsafe") from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise GitReviewError("git_metadata_unsafe")
        budget["files"] += 1
        if budget["files"] > _MAX_METADATA_FILES:
            raise GitReviewError("git_metadata_too_large")
        digest.update((display_name or name).encode("utf-8") + b"\0")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            while chunk := stream.read(1024 * 1024):
                budget["bytes"] += len(chunk)
                if budget["bytes"] > _MAX_REF_INDEX_BYTES:
                    raise GitReviewError("git_metadata_too_large")
                if time.monotonic() > deadline:
                    raise GitReviewError("git_timeout")
                digest.update(chunk)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _hash_metadata_directory_fd(directory_fd: int, prefix: str, digest, budget,
                                deadline: float) -> None:
    for entry in os.scandir(directory_fd):
        if time.monotonic() > deadline:
            raise GitReviewError("git_timeout")
        budget["files"] += 1
        if budget["files"] > _MAX_METADATA_FILES:
            raise GitReviewError("git_metadata_too_large")
        info = entry.stat(follow_symlinks=False)
        path = f"{prefix}/{entry.name}"
        if stat.S_ISDIR(info.st_mode):
            child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory_fd)
            try:
                _hash_metadata_directory_fd(child, path, digest, budget, deadline)
            finally:
                os.close(child)
        elif stat.S_ISREG(info.st_mode):
            _hash_metadata_file_fd(
                directory_fd, entry.name, digest, budget, deadline,
                display_name=path,
            )
        else:
            raise GitReviewError("git_metadata_unsafe")


def _metadata_fingerprint_path(root: Path, deadline: float) -> bytes:
    digest = hashlib.sha256()
    budget = {"bytes": 0, "files": 0}
    for name in ("HEAD", "index", "packed-refs", "shallow"):
        path = root / name
        try:
            before = _check_metadata_path(path, directory=False)
        except FileNotFoundError:
            digest.update(f"{name}:absent".encode())
            continue
        budget["files"] += 1
        if budget["files"] > _MAX_METADATA_FILES:
            raise GitReviewError("git_metadata_too_large")
        with path.open("rb") as stream:
            after = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise GitReviewError("git_metadata_changed")
            digest.update(name.encode("utf-8") + b"\0")
            while chunk := stream.read(1024 * 1024):
                budget["bytes"] += len(chunk)
                if budget["bytes"] > _MAX_REF_INDEX_BYTES:
                    raise GitReviewError("git_metadata_too_large")
                if time.monotonic() > deadline:
                    raise GitReviewError("git_timeout")
                digest.update(chunk)
    refs = root / "refs"
    if refs.exists():
        for path in sorted(refs.rglob("*")):
            if path.is_dir():
                continue
            before = _check_metadata_path(path, directory=False)
            budget["files"] += 1
            if budget["files"] > _MAX_METADATA_FILES:
                raise GitReviewError("git_metadata_too_large")
            with path.open("rb") as stream:
                after = os.fstat(stream.fileno())
                if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                    raise GitReviewError("git_metadata_changed")
                digest.update(path.relative_to(root).as_posix().encode() + b"\0")
                while chunk := stream.read(1024 * 1024):
                    budget["bytes"] += len(chunk)
                    if budget["bytes"] > _MAX_REF_INDEX_BYTES:
                        raise GitReviewError("git_metadata_too_large")
                    if time.monotonic() > deadline:
                        raise GitReviewError("git_timeout")
                    digest.update(chunk)
    else:
        digest.update(b"refs:absent")
    return digest.digest()


def _check_metadata_path(path: Path, *, directory: bool) -> os.stat_result:
    info = path.lstat()
    reparse = bool(getattr(info, "st_file_attributes", 0) & 0x400)
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not expected or stat.S_ISLNK(info.st_mode) or reparse:
        raise GitReviewError("git_metadata_unsafe")
    return info


def _copy_metadata_path(source: Path, target_root: Path, name: str,
                        budget: dict[str, int], deadline: float, *, optional: bool = False) -> None:
    path = source / name
    try:
        before = _check_metadata_path(path, directory=False)
    except FileNotFoundError:
        if optional:
            return
        raise GitReviewError("git_metadata_unsupported") from None
    budget["files"] += 1
    if budget["files"] > _MAX_METADATA_FILES or before.st_size > _MAX_METADATA_BYTES:
        raise GitReviewError("git_metadata_too_large")
    with path.open("rb") as stream:
        after = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise GitReviewError("git_metadata_changed")
        target = target_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            while chunk := stream.read(1024 * 1024):
                if time.monotonic() > deadline:
                    raise GitReviewError("git_timeout")
                budget["bytes"] += len(chunk)
                if budget["bytes"] > _MAX_METADATA_BYTES:
                    raise GitReviewError("git_metadata_too_large")
                output.write(chunk)


def _copy_metadata_tree_path(source: Path, target: Path,
                             budget: dict[str, int], deadline: float) -> None:
    _check_metadata_path(source, directory=True)
    budget["files"] += 1
    if budget["files"] > _MAX_METADATA_FILES:
        raise GitReviewError("git_metadata_too_large")
    target.mkdir(mode=0o700)
    for entry in source.iterdir():
        if time.monotonic() > deadline:
            raise GitReviewError("git_timeout")
        if source.name == "objects" and entry.name == "info":
            continue
        budget["files"] += 1
        if budget["files"] > _MAX_METADATA_FILES:
            raise GitReviewError("git_metadata_too_large")
        info = entry.lstat()
        if stat.S_ISDIR(info.st_mode):
            _copy_metadata_tree_path(entry, target / entry.name, budget, deadline)
        else:
            _copy_metadata_path(source, target, entry.name, budget, deadline)


def _copy_metadata_file(source_fd: int, target_root: Path, name: str,
                        budget: dict[str, int], deadline: float, *, optional: bool = False) -> None:
    try:
        fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=source_fd)
    except FileNotFoundError:
        if optional:
            return
        raise GitReviewError("git_metadata_unsupported") from None
    except OSError as error:
        raise GitReviewError("git_metadata_unsafe") from error
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise GitReviewError("git_metadata_unsafe")
        budget["files"] += 1
        if budget["files"] > _MAX_METADATA_FILES or info.st_size > _MAX_METADATA_BYTES:
            raise GitReviewError("git_metadata_too_large")
        target = target_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        remaining = _MAX_METADATA_BYTES - budget["bytes"]
        with os.fdopen(fd, "rb") as source:
            fd = -1
            with target.open("xb") as destination:
                while chunk := source.read(min(1024 * 1024, remaining + 1)):
                    if time.monotonic() > deadline:
                        raise GitReviewError("git_timeout")
                    budget["bytes"] += len(chunk)
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise GitReviewError("git_metadata_too_large")
                    destination.write(chunk)
    finally:
        if fd >= 0:
            os.close(fd)


def _copy_metadata_tree(source_fd: int, target_root: Path, name: str,
                        budget: dict[str, int], deadline: float) -> None:
    try:
        root_fd = os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                          getattr(os, "O_NOFOLLOW", 0), dir_fd=source_fd)
    except OSError as error:
        raise GitReviewError("git_metadata_unsafe") from error
    target = target_root / name
    target.mkdir(mode=0o700)
    try:
        _copy_metadata_directory(root_fd, target, budget, deadline)
    finally:
        os.close(root_fd)


def _copy_metadata_directory(source_fd: int, target: Path,
                             budget: dict[str, int], deadline: float) -> None:
    for entry in os.scandir(source_fd):
        if time.monotonic() > deadline:
            raise GitReviewError("git_timeout")
        name = entry.name
        if name in {".", ".."} or "/" in name or "\\" in name:
            raise GitReviewError("git_metadata_unsafe")
        if target.name == "objects" and name == "info":
            continue
        budget["files"] += 1
        if budget["files"] > _MAX_METADATA_FILES:
            raise GitReviewError("git_metadata_too_large")
        info = entry.stat(follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            fd = os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                         getattr(os, "O_NOFOLLOW", 0), dir_fd=source_fd)
            child = target / name
            child.mkdir(mode=0o700)
            try:
                _copy_metadata_directory(fd, child, budget, deadline)
            finally:
                os.close(fd)
        elif stat.S_ISREG(info.st_mode):
            _copy_metadata_file(source_fd, target, name, budget, deadline)
        else:
            raise GitReviewError("git_metadata_unsafe")


def _contained(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


def _repo_root(workspace: Path) -> Path:
    resolved_workspace = workspace.resolve(strict=True)
    raw = _git(resolved_workspace, "rev-parse", "--show-toplevel").decode().strip()
    repo = Path(raw).resolve(strict=True)
    if not _contained(repo, resolved_workspace):
        raise GitReviewError("git_workspace_escape")
    return repo


def _resolve_commit(workspace: Path, ref: str) -> str:
    if len(ref) > 256 or "\x00" in ref:
        raise GitReviewError("git_ref_invalid")
    raw = _git(
        workspace,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"{ref}^{{commit}}",
    ).decode("ascii", errors="strict").strip()
    if len(raw) not in {40, 64} or any(char not in "0123456789abcdefABCDEF" for char in raw):
        raise GitReviewError("git_ref_invalid")
    return raw


def _safe_relative_path(value: str) -> str:
    if not value or len(value) > 2048 or "\x00" in value or "\\" in value:
        raise GitReviewError("git_path_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise GitReviewError("git_path_invalid")
    return path.as_posix()


def _reject_symlink_components(workspace: Path, relative: str) -> None:
    current = workspace
    for component in PurePosixPath(relative).parts:
        current = current / component
        try:
            if current.is_symlink():
                raise GitReviewError("git_path_symlink")
        except OSError as exc:
            raise GitReviewError("git_path_unavailable") from exc
    if not _contained(current.resolve(strict=False), workspace.resolve(strict=True)):
        raise GitReviewError("git_workspace_escape")


def _open_worktree_file(workspace: Path, relative: str) -> int:
    parts = PurePosixPath(relative).parts
    if os.name != "nt":
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root_descriptor = _WORKSPACE_FD.get()
        descriptor = (
            os.dup(root_descriptor)
            if root_descriptor is not None
            else os.open(workspace, directory_flags)
        )
        try:
            for part in parts[:-1]:
                next_descriptor = os.open(part, directory_flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
            file_descriptor = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
                dir_fd=descriptor,
            )
        finally:
            os.close(descriptor)
        if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
            os.close(file_descriptor)
            raise GitReviewError("git_path_unavailable")
        return file_descriptor

    target = workspace.joinpath(*parts)
    before = target.lstat()
    reparse = bool(getattr(before, "st_file_attributes", 0) & 0x400)
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or reparse:
        raise GitReviewError("git_path_unavailable")
    file_descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    after = os.fstat(file_descriptor)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        os.close(file_descriptor)
        raise GitReviewError("git_path_changed")
    return file_descriptor


def _name_status(workspace: Path, scope: Scope, base_ref: str | None, commit_ref: str | None) -> tuple[list[tuple[str, str]], str | None, str | None]:
    base_oid: str | None = None
    head_oid: str | None = None
    if scope == "unstaged":
        args = ("diff", "--no-ext-diff", "--no-textconv", "--name-status", "-z", "--")
        raw = _git(workspace, *args)
        entries = _parse_name_status(raw)
        untracked = _git(workspace, "ls-files", "--others", "--exclude-standard", "-z")
        for item in untracked.split(b"\0"):
            if item:
                entries.append(("A", item.decode("utf-8", errors="replace")))
    elif scope == "staged":
        raw = _git(workspace, "diff", "--cached", "--no-ext-diff", "--no-textconv", "--name-status", "-z", "--")
        entries = _parse_name_status(raw)
    elif scope == "commit":
        head_oid = _resolve_commit(workspace, commit_ref or "HEAD")
        base_oid = (
            _git(workspace, "rev-parse", "--verify", f"{head_oid}^", timeout=3)
            .decode("ascii", errors="strict").strip()
            if _has_parent(workspace, head_oid)
            else None
        )
        raw = _git(workspace, "diff-tree", "--root", "--no-commit-id", "--name-status", "-r", "-z", head_oid, "--")
        entries = _parse_name_status(raw)
    else:
        if not base_ref:
            raise GitReviewError("git_base_ref_required")
        base_oid = _resolve_commit(workspace, base_ref)
        head_oid = _resolve_commit(workspace, "HEAD")
        raw = _git(workspace, "diff", "--no-ext-diff", "--no-textconv", "--name-status", "-z", f"{base_oid}...{head_oid}", "--")
        entries = _parse_name_status(raw)
    return entries, base_oid, head_oid


def _parse_name_status(raw: bytes) -> list[tuple[str, str]]:
    parts = raw.split(b"\0")
    if parts and not parts[-1]:
        parts.pop()
    result: list[tuple[str, str]] = []
    index = 0
    while index < len(parts):
        status = parts[index].decode("ascii", errors="replace")
        index += 1
        if status.startswith(("R", "C")):
            if index + 1 >= len(parts):
                break
            # The destination is the path represented in the selected tree.
            index += 1
        if index >= len(parts):
            break
        path = parts[index].decode("utf-8", errors="replace")
        index += 1
        result.append((status[:1], path))
    return result


def _has_parent(workspace: Path, commit_oid: str) -> bool:
    try:
        _git(workspace, "rev-parse", "--verify", f"{commit_oid}^", timeout=3)
    except GitReviewError:
        return False
    return True


def _state_token(workspace: Path, scope: Scope, base_ref: str | None, commit_ref: str | None) -> str:
    digest = hashlib.sha256()
    if scope in {"unstaged", "staged"}:
        if scope == "unstaged":
            names = _git(workspace, "diff", "--name-only", "--no-ext-diff", "--no-textconv", "-z", "--")
            paths = [item.decode("utf-8", errors="replace") for item in names.split(b"\0") if item]
            untracked = _git(workspace, "ls-files", "--others", "--exclude-standard", "-z")
            paths.extend(item.decode("utf-8", errors="replace") for item in untracked.split(b"\0") if item)
            for relative in sorted(set(paths)):
                _reject_symlink_components(workspace, relative)
                descriptor = -1
                try:
                    descriptor = _open_worktree_file(workspace, relative)
                    info = os.fstat(descriptor)
                    digest.update(f"{relative}:{info.st_size}:{info.st_mtime_ns}:{info.st_mode}".encode())
                    content_hash = hashlib.sha256()
                    with os.fdopen(descriptor, "rb") as source:
                        descriptor = -1
                        while chunk := source.read(1024 * 1024):
                            deadline = _GIT_DEADLINE.get()
                            if deadline is not None and time.monotonic() > deadline:
                                raise GitReviewError("git_timeout")
                            total = _WORKTREE_HASH_BYTES.get() + len(chunk)
                            if total > _MAX_WORKTREE_HASH_BYTES:
                                raise GitReviewError("git_state_too_large")
                            _WORKTREE_HASH_BYTES.set(total)
                            content_hash.update(chunk)
                    digest.update(content_hash.digest())
                except FileNotFoundError:
                    digest.update(f"{relative}:missing".encode())
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
        else:
            raw = _git(workspace, "diff", "--cached", "--raw", "--no-ext-diff", "--no-textconv", "--")
            digest.update(raw)
    elif scope == "commit":
        digest.update(_resolve_commit(workspace, commit_ref or "HEAD").encode())
    else:
        if not base_ref:
            raise GitReviewError("git_base_ref_required")
        digest.update(_resolve_commit(workspace, base_ref).encode())
        digest.update(_resolve_commit(workspace, "HEAD").encode())
    return digest.hexdigest()


def _patch(workspace: Path, scope: Scope, relative: str, base_ref: str | None, commit_ref: str | None) -> tuple[str | None, bool, bool]:
    _reject_symlink_components(workspace, relative)
    pathspec = f":(literal){relative}"
    if scope == "unstaged":
        args = ("diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-indent-heuristic", "--unified=3", "--", pathspec)
        try:
            _git(workspace, "ls-files", "--error-unmatch", "-z", "--", pathspec, timeout=2)
            tracked = True
        except GitReviewError as error:
            if error.code == "git_unavailable":
                raise
            tracked = False
        if tracked:
            raw, truncated = _read_bounded_diff(workspace, *args)
        else:
            try:
                descriptor = _open_worktree_file(workspace, relative)
            except FileNotFoundError:
                return None, False, False
            else:
                os.close(descriptor)
            raw, truncated = _read_bounded_process(
                _git_prefix(workspace) + ["-C", _git_worktree_path(workspace), "diff", "--no-index", "--no-ext-diff", "--no-textconv", "--no-color", "--unified=3", "--", os.devnull, str(Path(_git_worktree_path(workspace)) / PurePosixPath(relative))],
                workspace,
                accepted_returncodes=frozenset({0, 1}),
            )
        patch = raw.decode("utf-8", errors="replace")
        binary = "GIT binary patch" in patch or "Binary files " in patch
        return (None if binary else patch), binary, truncated
    if scope == "staged":
        args = ("diff", "--cached", "--no-ext-diff", "--no-textconv", "--no-color", "--no-indent-heuristic", "--unified=3", "--", pathspec)
    elif scope == "commit":
        head = _resolve_commit(workspace, commit_ref or "HEAD")
        args = ("show", "--root", "--format=", "--no-ext-diff", "--no-textconv", "--no-color", "--no-indent-heuristic", "--unified=3", head, "--", pathspec)
    else:
        if not base_ref:
            raise GitReviewError("git_base_ref_required")
        base = _resolve_commit(workspace, base_ref)
        head = _resolve_commit(workspace, "HEAD")
        args = ("diff", "--no-ext-diff", "--no-textconv", "--no-color", "--no-indent-heuristic", "--unified=3", f"{base}...{head}", "--", pathspec)
    raw, truncated = _read_bounded_diff(workspace, *args)
    patch = raw.decode("utf-8", errors="replace")
    binary = "GIT binary patch" in patch or "Binary files " in patch
    return (None if binary else patch), binary, truncated


def _read_bounded_process(
    command: list[str], workspace: Path, *, max_bytes: int = _MAX_PATCH_BYTES,
    timeout: float | None = None,
    accepted_returncodes: frozenset[int] = frozenset({0}),
) -> tuple[bytes, bool]:
    env = _git_environment()
    try:
        workspace_fd = _WORKSPACE_FD.get()
        process = subprocess.Popen(
            command,
            cwd=_git_worktree_path(workspace),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            pass_fds=(workspace_fd,) if workspace_fd is not None and os.name != "nt" else (),
        )
        assert process.stdout is not None
        output = bytearray()
        state = {"truncated": False}

        def drain_stdout() -> None:
            while True:
                remaining = max_bytes + 1 - len(output)
                chunk = process.stdout.read(min(8192, remaining))
                if not chunk:
                    return
                output.extend(chunk)
                if len(output) > max_bytes:
                    state["truncated"] = True
                    process.kill()
                    return

        reader = threading.Thread(target=drain_stdout, daemon=True)
        reader.start()
        try:
            deadline = _GIT_DEADLINE.get()
            effective_timeout = timeout or (
                max(0.05, deadline - time.monotonic())
                if deadline is not None else _MAX_DIFF_TIMEOUT
            )
            if deadline is not None:
                effective_timeout = min(
                    effective_timeout, max(0.05, deadline - time.monotonic())
                )
            process.wait(timeout=effective_timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            reader.join(timeout=1)
            raise GitReviewError("git_timeout") from exc
        reader.join(timeout=1)
        truncated = bool(state["truncated"])
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        raise GitReviewError("git_timeout") from exc
    except OSError as exc:
        raise GitReviewError("git_unavailable") from exc
    if process.returncode not in accepted_returncodes and not truncated:
        raise GitReviewError("git_state_unavailable")
    return bytes(output[:max_bytes]), truncated


def _read_bounded_diff(workspace: Path, *args: str) -> tuple[bytes, bool]:
    # Git diff has no output cap. Capture one file only and cap the retained
    # payload; the command still has a strict wall-clock timeout.
    return _read_bounded_process(
        _git_prefix(workspace) + ["-C", _git_worktree_path(workspace), *args], workspace
    )


@router.get("/threads/{thread_id}/git-review", response_model=GitReviewResponse)
def get_git_review(
    thread_id: str,
    request: Request,
    scope: Scope = Query(),
    base_ref: str | None = Query(default=None, max_length=256),
    commit_ref: str | None = Query(default=None, max_length=256),
    path: str | None = Query(default=None, max_length=2048),
    expected_state_token: str | None = Query(default=None, min_length=64, max_length=64),
    authorization: str | None = Header(default=None),
) -> GitReviewResponse:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    try:
        thread = request.app.state.storage.get_thread(thread_id)
        workspace = request.app.state.storage.resolve_workspace_path(thread.workspace_path)
        with _private_git_snapshot(workspace):
            repo = _repo_root(workspace)
            state_token = _state_token(repo, scope, base_ref, commit_ref)
            if expected_state_token is not None and expected_state_token != state_token:
                raise HTTPException(status_code=409, detail={"code": "git_state_changed"})
            entries, resolved_base, resolved_head = _name_status(repo, scope, base_ref, commit_ref)
            selected_path = _safe_relative_path(path) if path is not None else None
            if selected_path is not None:
                entries = [entry for entry in entries if entry[1] == selected_path]
                if not entries:
                    raise HTTPException(status_code=404, detail="changed file not found")
            files: list[GitReviewFile] = []
            files_truncated = len(entries) > _MAX_FILES
            for status, filename in entries[:_MAX_FILES]:
                safe_path = _safe_relative_path(filename)
                record = GitReviewFile(path=safe_path, status=status)
                if selected_path is not None:
                    patch, binary, truncated = _patch(repo, scope, safe_path, base_ref, commit_ref)
                    record.patch = patch
                    record.binary = binary
                    record.large = truncated
                    record.patch_truncated = truncated
                    if binary:
                        record.patch = None
                    elif patch is not None and not truncated:
                        record.additions = sum(
                            line.startswith("+") and not line.startswith("+++")
                            for line in patch.splitlines()
                        )
                        record.deletions = sum(
                            line.startswith("-") and not line.startswith("---")
                            for line in patch.splitlines()
                        )
                files.append(record)
            if _state_token(repo, scope, base_ref, commit_ref) != state_token:
                raise HTTPException(status_code=409, detail={"code": "git_state_changed"})
            return GitReviewResponse(
                scope=scope,
                base_ref=resolved_base,
                head_ref=resolved_head,
                files=files,
                files_truncated=files_truncated,
                state_token=state_token,
            )
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workspace path not found") from exc
    except WorkspaceBoundaryError as exc:
        raise HTTPException(status_code=400, detail="invalid workspace path") from exc
    except GitReviewError as exc:
        if exc.code in {"git_state_changed", "git_metadata_changed"}:
            status_code = 409
        else:
            status_code = 503 if exc.code in {"git_unavailable", "git_timeout"} else 400
        raise HTTPException(status_code=status_code, detail={"code": exc.code}) from exc
