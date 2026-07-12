import os
import traceback
from pathlib import Path

import pytest

from codex_bridge_service import workspace
from codex_bridge_service.workspace import (
    WorkspaceBoundary,
    WorkspaceEscapeError,
    WorkspaceExistsError,
    WorkspaceInputError,
    WorkspaceNotFoundError,
    WorkspaceTypeError,
    WorkspaceUnsupportedError,
)


def _symlink_or_skip(target: Path, link: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symbolic links are unavailable: {type(exc).__name__}")


def test_boundary_requires_an_explicit_absolute_existing_root(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(WorkspaceInputError) as relative_error:
        WorkspaceBoundary("workspace")
    with pytest.raises(WorkspaceInputError):
        WorkspaceBoundary("~/workspace")
    with pytest.raises(WorkspaceNotFoundError):
        WorkspaceBoundary(tmp_path / "missing")

    assert "workspace" not in str(relative_error.value)
    assert str(tmp_path) not in repr(relative_error.value)


@pytest.mark.skipif(os.name == "nt", reason="secure dir_fd creation is unavailable")
def test_boundary_creates_a_root_only_when_explicitly_requested(tmp_path) -> None:
    root = tmp_path / "new" / "workspace"

    boundary = WorkspaceBoundary(root, create=True)

    assert root.is_dir()
    assert boundary.root == root.resolve()
    assert str(root) not in repr(boundary)


@pytest.mark.parametrize(
    "unsafe",
    (
        "",
        "   ",
        "/absolute",
        "//server/share",
        r"C:\absolute",
        r"C:/absolute",
        r"C:drive-relative",
        r"\\server\share\file",
        r"\\?\C:\device\file",
        r"\\.\PhysicalDrive0",
        r"\rooted-on-current-drive",
        "../outside",
        "inside/../outside",
        "inside/./file",
        "inside//file",
        "inside\\\\file",
        "inside\\mixed/file",
        "inside/",
        "inside\\",
        "nul\x00byte",
        "line\nbreak",
        "control\x1fcharacter",
        "delete\x7fcharacter",
        "c1\x85character",
        "bidi\u202ename",
        "zero\u200bwidth",
        "surrogate\ud800name",
        "file:stream",
        "safe?.txt",
        "a*b",
        'quote"name',
        "<name>",
        "a|b",
        "trailing-dot.",
        "trailing-space ",
    ),
)
def test_normalize_rejects_ambiguous_or_escaping_names(tmp_path, unsafe: str) -> None:
    boundary = WorkspaceBoundary(tmp_path)

    with pytest.raises(WorkspaceInputError) as error:
        boundary.normalize(unsafe)

    assert error.value.code == "invalid_relative_path"
    if unsafe:
        assert unsafe not in str(error.value)
    assert str(tmp_path) not in repr(error.value)


def test_normalize_returns_one_portable_public_form(tmp_path) -> None:
    boundary = WorkspaceBoundary(tmp_path)

    assert boundary.normalize("project/source/main.py") == "project/source/main.py"
    assert boundary.normalize(r"project\source\main.py") == "project/source/main.py"
    assert boundary.normalize(".", allow_root=True) == "."
    with pytest.raises(WorkspaceInputError):
        boundary.normalize(".")


@pytest.mark.parametrize(
    "reserved",
    (
        ".",
        "..",
        "NUL",
        "con.txt",
        "COM1",
        "lpt9.log",
        "CONIN$",
        "conout$.txt",
        "COM¹",
        "lpt².txt",
        "com³.log",
    ),
)
def test_normalize_rejects_reserved_components(tmp_path, reserved: str) -> None:
    boundary = WorkspaceBoundary(tmp_path)

    with pytest.raises(WorkspaceInputError):
        boundary.normalize(f"safe/{reserved}")


def test_resolve_and_relative_path_round_trip_without_leaking_the_root(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    file_path = root / "project" / "notes.txt"
    file_path.parent.mkdir()
    file_path.write_text("notes", encoding="utf-8")
    boundary = WorkspaceBoundary(root)

    assert boundary.resolve_relative("project/notes.txt", must_exist=True, kind="file") == file_path
    assert boundary.relative_from_path(file_path) == "project/notes.txt"
    assert boundary.relative_from_path(root) == "."

    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    with pytest.raises(WorkspaceEscapeError) as error:
        boundary.relative_from_path(outside)
    assert str(root) not in str(error.value)
    assert str(outside) not in repr(error.value)


def test_resolve_rejects_missing_or_wrong_kind_with_typed_redacted_errors(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "folder").mkdir()
    boundary = WorkspaceBoundary(root)

    with pytest.raises(WorkspaceNotFoundError) as missing:
        boundary.resolve_relative("missing.txt", must_exist=True)
    with pytest.raises(WorkspaceTypeError) as wrong_kind:
        boundary.resolve_relative("folder", must_exist=True, kind="file")

    assert missing.value.code == "not_found"
    assert wrong_kind.value.code == "wrong_type"
    assert "missing.txt" not in str(missing.value)
    assert str(root) not in repr(wrong_kind.value)


def test_existing_parent_and_final_symlink_swaps_fail_closed(tmp_path) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    boundary = WorkspaceBoundary(root)

    parent = root / "project"
    parent.mkdir()
    parent.rmdir()
    _symlink_or_skip(outside, parent, directory=True)

    with pytest.raises(WorkspaceEscapeError):
        boundary.resolve_relative("project/secret.txt", must_exist=True)
    with pytest.raises(WorkspaceEscapeError):
        boundary.open_regular_file("project/secret.txt")
    with pytest.raises(WorkspaceEscapeError):
        boundary.create_file_exclusive("project/new.txt")

    parent.unlink()
    parent.mkdir()
    final = parent / "secret.txt"
    _symlink_or_skip(outside / "secret.txt", final)

    with pytest.raises(WorkspaceEscapeError):
        boundary.resolve_relative("project/secret.txt", must_exist=True)
    with pytest.raises(WorkspaceEscapeError):
        boundary.open_regular_file("project/secret.txt")


@pytest.mark.skipif(os.name == "nt", reason="secure dir_fd operations are unavailable")
def test_regular_file_parent_reports_wrong_type_not_escape(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "not-a-directory").write_text("file", encoding="utf-8")
    boundary = WorkspaceBoundary(root)

    with pytest.raises(WorkspaceTypeError):
        boundary.open_regular_file("not-a-directory/child.txt")
    with pytest.raises(WorkspaceTypeError):
        boundary.create_file_exclusive("not-a-directory/child.txt")


def test_dangling_and_internal_symlinks_are_rejected_uniformly(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "real").mkdir()
    (root / "real" / "file.txt").write_text("safe", encoding="utf-8")
    _symlink_or_skip(root / "missing", root / "dangling")
    _symlink_or_skip(root / "real", root / "internal", directory=True)
    boundary = WorkspaceBoundary(root)

    for relative in ("dangling", "internal/file.txt"):
        with pytest.raises(WorkspaceEscapeError):
            boundary.resolve_relative(relative)


@pytest.mark.skipif(os.name == "nt", reason="secure dir_fd operations are unavailable")
def test_create_directory_and_browse_return_only_public_real_directories(tmp_path) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    boundary = WorkspaceBoundary(root)

    assert boundary.create_directory("projects/alpha/source") == "projects/alpha/source"
    (root / "projects" / "beta").mkdir()
    (root / "projects" / "readme.txt").write_text("not a directory", encoding="utf-8")
    _symlink_or_skip(outside, root / "projects" / "linked", directory=True)

    assert boundary.list_directories("projects") == (
        "projects/alpha",
        "projects/beta",
    )


@pytest.mark.skipif(os.name == "nt", reason="secure dir_fd operations are unavailable")
def test_walk_regular_files_does_not_follow_links_or_include_special_entries(tmp_path) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "alpha" / "nested").mkdir(parents=True)
    (root / "alpha" / "a.txt").write_text("a", encoding="utf-8")
    (root / "alpha" / "nested" / "b.txt").write_text("b", encoding="utf-8")
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    _symlink_or_skip(outside, root / "alpha" / "linked-dir", directory=True)
    _symlink_or_skip(outside / "secret.txt", root / "alpha" / "linked-file")
    boundary = WorkspaceBoundary(root)

    assert boundary.walk_regular_files("alpha") == (
        "alpha/a.txt",
        "alpha/nested/b.txt",
    )


@pytest.mark.skipif(os.name == "nt", reason="secure dir_fd operations are unavailable")
def test_open_regular_file_reads_bytes_and_rejects_directories(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "project").mkdir()
    (root / "project" / "notes.txt").write_bytes(b"safe notes")
    boundary = WorkspaceBoundary(root)

    with boundary.open_regular_file("project/notes.txt") as stream:
        assert stream.read() == b"safe notes"
    with pytest.raises(WorkspaceTypeError):
        boundary.open_regular_file("project")


@pytest.mark.skipif(os.name == "nt", reason="secure dir_fd operations are unavailable")
def test_create_file_is_exclusive_and_never_overwrites_a_collision(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "project").mkdir()
    existing = root / "project" / "notes.txt"
    existing.write_bytes(b"original")
    boundary = WorkspaceBoundary(root)

    with pytest.raises(WorkspaceExistsError) as error:
        boundary.create_file_exclusive("project/notes.txt")
    assert existing.read_bytes() == b"original"
    assert error.value.code == "already_exists"

    with boundary.create_file_exclusive("project/new.txt") as stream:
        stream.write(b"new")
    assert (root / "project" / "new.txt").read_bytes() == b"new"


@pytest.mark.skipif(os.name == "nt", reason="POSIX special-file facilities are unavailable")
def test_special_files_are_not_enumerated_or_opened(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "regular.txt").write_text("safe", encoding="utf-8")
    os.mkfifo(root / "pipe")
    boundary = WorkspaceBoundary(root)

    assert boundary.walk_regular_files() == ("regular.txt",)
    with pytest.raises(WorkspaceTypeError):
        boundary.resolve_relative("pipe", must_exist=True)
    with pytest.raises(WorkspaceTypeError):
        boundary.open_regular_file("pipe")


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor semantics are unavailable")
def test_root_creation_does_not_follow_a_symlinked_ancestor(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises((WorkspaceEscapeError, WorkspaceTypeError)):
        WorkspaceBoundary(linked_parent / "created-outside", create=True)

    assert not (outside / "created-outside").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor semantics are unavailable")
def test_directory_listing_stays_on_open_descriptor_during_path_swap(
    tmp_path,
    monkeypatch,
) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    base = root / "base"
    moved = root / "moved"
    (base / "safe").mkdir(parents=True)
    (outside / "secret").mkdir(parents=True)
    boundary = WorkspaceBoundary(root)
    real_scandir = os.scandir
    swapped = False

    def swap_then_scan(path):
        nonlocal swapped
        if isinstance(path, int) and not swapped:
            base.rename(moved)
            base.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_scandir(path)

    monkeypatch.setattr(workspace.os, "scandir", swap_then_scan)

    assert boundary.list_directories("base") == ("base/safe",)


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor semantics are unavailable")
def test_recursive_walk_fails_closed_when_child_is_swapped_for_symlink(
    tmp_path,
    monkeypatch,
) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    nested = root / "base" / "nested"
    moved = root / "base" / "moved"
    nested.mkdir(parents=True)
    (nested / "safe.txt").write_text("safe", encoding="utf-8")
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    boundary = WorkspaceBoundary(root)
    real_open = os.open
    swapped = False

    def swap_then_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "nested" and kwargs.get("dir_fd") is not None and not swapped:
            nested.rename(moved)
            nested.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(workspace.os, "open", swap_then_open)

    with pytest.raises((WorkspaceEscapeError, WorkspaceTypeError)):
        boundary.walk_regular_files("base")


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO facilities are unavailable")
def test_raced_fifo_open_is_nonblocking_and_rejected(tmp_path, monkeypatch) -> None:
    root = tmp_path / "workspace"
    project = root / "project"
    project.mkdir(parents=True)
    target = project / "notes.txt"
    target.write_text("safe", encoding="utf-8")
    boundary = WorkspaceBoundary(root)
    real_open = os.open
    swapped = False

    def swap_then_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "notes.txt" and kwargs.get("dir_fd") is not None and not swapped:
            target.unlink()
            os.mkfifo(target)
            swapped = True
            assert flags & os.O_NONBLOCK
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(workspace.os, "open", swap_then_open)

    with pytest.raises(WorkspaceTypeError):
        boundary.open_regular_file("project/notes.txt")


def test_platform_errors_never_cross_the_public_boundary(tmp_path, monkeypatch) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    boundary = WorkspaceBoundary(root)

    def fail_lstat(self: Path):
        raise OSError(f"private platform detail at {self}")

    monkeypatch.setattr(Path, "lstat", fail_lstat)

    with pytest.raises(WorkspaceTypeError) as error:
        boundary.resolve_relative("safe.txt")
    assert "private platform detail" not in str(error.value)
    assert str(root) not in repr(error.value)
    assert error.value.__cause__ is None
    rendered = "".join(traceback.format_exception(error.value))
    assert "private platform detail" not in rendered
    assert str(root) not in rendered


def test_secure_operations_fail_closed_when_required_primitives_are_unavailable(
    tmp_path,
    monkeypatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "project").mkdir()
    (root / "project" / "notes.txt").write_text("safe", encoding="utf-8")
    boundary = WorkspaceBoundary(root)
    monkeypatch.setattr(workspace, "_secure_dir_fd_available", lambda: False)

    with pytest.raises(WorkspaceUnsupportedError):
        WorkspaceBoundary(tmp_path / "new-root", create=True)
    operations = (
        lambda: boundary.create_directory("new"),
        lambda: boundary.list_directories(),
        lambda: boundary.walk_regular_files(),
        lambda: boundary.open_regular_file("project/notes.txt"),
        lambda: boundary.create_file_exclusive("project/new.txt"),
    )
    for operation in operations:
        with pytest.raises(WorkspaceUnsupportedError) as error:
            operation()
        assert error.value.code == "secure_operations_unavailable"


def test_secure_capability_requires_no_follow_and_directory_flags(monkeypatch) -> None:
    monkeypatch.setattr(workspace, "_HAS_DIR_FD_PRIMITIVES", True)
    monkeypatch.setattr(workspace.os, "O_NOFOLLOW", 0, raising=False)
    assert workspace._secure_dir_fd_available() is False

    monkeypatch.setattr(workspace.os, "O_NOFOLLOW", 1, raising=False)
    monkeypatch.setattr(workspace.os, "O_DIRECTORY", 0, raising=False)
    assert workspace._secure_dir_fd_available() is False
