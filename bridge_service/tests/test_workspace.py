import os
from pathlib import Path

import pytest

from codex_bridge_service.workspace import (
    WorkspaceBoundary,
    WorkspaceEscapeError,
    WorkspaceExistsError,
    WorkspaceInputError,
    WorkspaceNotFoundError,
    WorkspaceTypeError,
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
        "file:stream",
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


@pytest.mark.parametrize("reserved", (".", "..", "NUL", "con.txt", "COM1", "lpt9.log"))
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
