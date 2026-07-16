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


def test_validate_file_locator_allows_missing_and_accepts_existing_regular_file(tmp_path) -> None:
    root = tmp_path / "workspace"
    (root / "project").mkdir(parents=True)
    (root / "project" / "notes.txt").write_text("safe", encoding="utf-8")
    boundary = WorkspaceBoundary(root)

    assert boundary.validate_file_locator("missing/notes.txt") == "missing/notes.txt"
    assert boundary.validate_file_locator("project/notes.txt") == "project/notes.txt"
    assert (
        boundary.validate_file_locator("project/notes.txt", must_exist=True)
        == "project/notes.txt"
    )
    with pytest.raises(WorkspaceNotFoundError):
        boundary.validate_file_locator("missing/notes.txt", must_exist=True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX special-file facilities are unavailable")
def test_validate_file_locator_rejects_symlink_and_special_entries(tmp_path) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside.txt"
    root.mkdir()
    outside.write_text("outside", encoding="utf-8")
    _symlink_or_skip(outside, root / "link.txt")
    os.mkfifo(root / "pipe")
    boundary = WorkspaceBoundary(root)

    with pytest.raises(WorkspaceEscapeError):
        boundary.validate_file_locator("link.txt")
    with pytest.raises(WorkspaceTypeError):
        boundary.validate_file_locator("pipe")


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor semantics are unavailable")
def test_validate_regular_file_at_stays_anchored_to_directory_lease(tmp_path) -> None:
    root = tmp_path / "workspace"
    selected = root / "selected"
    moved = root / "selected-original"
    outside = tmp_path / "outside"
    selected.mkdir(parents=True)
    outside.mkdir()
    (selected / "safe.txt").write_text("inside", encoding="utf-8")
    (outside / "safe.txt").mkdir()
    boundary = WorkspaceBoundary(root)
    directory_fd = boundary.open_directory_fd("selected")
    try:
        selected.rename(moved)
        selected.symlink_to(outside, target_is_directory=True)

        assert boundary.validate_regular_file_at(directory_fd, "safe.txt") == "safe.txt"
    finally:
        os.close(directory_fd)


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor semantics are unavailable")
def test_unlink_regular_file_is_descriptor_rooted_and_type_checked(tmp_path) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside.txt"
    root.mkdir()
    outside.write_text("outside", encoding="utf-8")
    (root / "remove.txt").write_text("remove", encoding="utf-8")
    (root / "directory").mkdir()
    _symlink_or_skip(outside, root / "link.txt")
    os.mkfifo(root / "pipe")
    boundary = WorkspaceBoundary(root)

    boundary.unlink_regular_file("remove.txt")
    assert not (root / "remove.txt").exists()
    boundary.unlink_regular_file("missing.txt", missing_ok=True)
    with pytest.raises(WorkspaceNotFoundError):
        boundary.unlink_regular_file("missing.txt")
    with pytest.raises(WorkspaceTypeError):
        boundary.unlink_regular_file("directory")
    with pytest.raises(WorkspaceTypeError):
        boundary.unlink_regular_file("pipe")
    with pytest.raises(WorkspaceEscapeError):
        boundary.unlink_regular_file("link.txt")
    assert outside.read_text(encoding="utf-8") == "outside"


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor semantics are unavailable")
def test_missing_ok_cleanup_allows_an_absent_parent_but_remains_fail_closed(
    tmp_path,
) -> None:
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    boundary = WorkspaceBoundary(root)

    boundary.unlink_regular_file("missing/file.txt", missing_ok=True)
    boundary.remove_empty_directory("missing/child", missing_ok=True)
    with pytest.raises(WorkspaceNotFoundError):
        boundary.unlink_regular_file("missing/file.txt")
    _symlink_or_skip(outside, root / "linked", directory=True)
    with pytest.raises(WorkspaceEscapeError):
        boundary.unlink_regular_file("linked/file.txt", missing_ok=True)
    with pytest.raises(WorkspaceEscapeError):
        boundary.remove_empty_directory("linked/child", missing_ok=True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor semantics are unavailable")
def test_file_identity_rejects_replacement_and_protects_cleanup(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "upload.txt"
    moved = root / "upload-original.txt"
    boundary = WorkspaceBoundary(root)

    with boundary.create_file_exclusive("upload.txt") as stream:
        stream.write(b"uploaded")
        stream.flush()
        identity = boundary.identify_open_file(stream)
        target.rename(moved)
        target.write_bytes(b"replacement")

        with pytest.raises(WorkspaceEscapeError):
            boundary.validate_regular_file_identity("upload.txt", identity)
        with pytest.raises(WorkspaceEscapeError):
            boundary.unlink_regular_file(
                "upload.txt",
                expected_identity=identity,
            )

    assert target.read_bytes() == b"replacement"
    assert moved.read_bytes() == b"uploaded"


@pytest.mark.skipif(os.name == "nt", reason="Linux memfd facilities are unavailable")
def test_anonymous_file_lease_is_sealed_pathless_and_read_only(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    source = root / "input.txt"
    source.write_bytes(b"sealed input")
    boundary = WorkspaceBoundary(root)

    lease = boundary.copy_regular_file_to_anonymous_lease("input.txt")
    descriptor = lease.fileno()
    try:
        assert Path(lease.process_path).read_bytes() == b"sealed input"
        assert "/memfd:codex-bridge-input" in os.readlink(lease.process_path)
        assert str(root) not in os.readlink(lease.process_path)
        assert Path(f"{lease.process_path}/..").exists() is False
        with pytest.raises(OSError):
            os.write(descriptor, b"mutate")
        source.write_bytes(b"replacement")
        assert Path(lease.process_path).read_bytes() == b"sealed input"
    finally:
        lease.close()

    with pytest.raises(OSError):
        os.fstat(descriptor)


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


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor semantics are unavailable")
def test_operations_remain_anchored_when_root_ancestor_is_replaced(tmp_path) -> None:
    parent = tmp_path / "parent"
    original_root = parent / "workspace"
    moved_parent = tmp_path / "moved-parent"
    outside_parent = tmp_path / "outside-parent"
    outside_root = outside_parent / "workspace"
    (original_root / "safe-dir").mkdir(parents=True)
    (original_root / "safe.txt").write_text("inside", encoding="utf-8")
    (outside_root / "secret-dir").mkdir(parents=True)
    (outside_root / "safe.txt").write_text("outside", encoding="utf-8")
    boundary = WorkspaceBoundary(original_root)

    parent.rename(moved_parent)
    parent.symlink_to(outside_parent, target_is_directory=True)

    with boundary.open_regular_file("safe.txt") as stream:
        assert stream.read() == b"inside"
    with boundary.create_file_exclusive("created.txt") as stream:
        stream.write(b"created inside")
    assert boundary.list_directories() == ("safe-dir",)
    assert boundary.walk_regular_files() == ("created.txt", "safe.txt")
    assert (moved_parent / "workspace" / "created.txt").read_bytes() == b"created inside"
    assert not (outside_root / "created.txt").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor semantics are unavailable")
def test_directory_lease_stays_anchored_when_entry_is_replaced(tmp_path) -> None:
    root = tmp_path / "workspace"
    selected = root / "selected"
    moved = root / "selected-original"
    outside = tmp_path / "outside"
    selected.mkdir(parents=True)
    outside.mkdir()
    boundary = WorkspaceBoundary(root)
    original_inode = selected.stat().st_ino

    directory_fd = boundary.open_directory_fd("selected")
    try:
        selected.rename(moved)
        selected.symlink_to(outside, target_is_directory=True)

        assert os.fstat(directory_fd).st_ino == original_inode
        assert os.fstat(directory_fd).st_ino != outside.stat().st_ino
    finally:
        os.close(directory_fd)


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor semantics are unavailable")
def test_close_releases_root_descriptor_and_fails_future_io_closed(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    boundary = WorkspaceBoundary(root)
    root_fd = boundary._root_fd
    assert root_fd is not None

    boundary.close()

    with pytest.raises(OSError):
        os.fstat(root_fd)
    with pytest.raises(WorkspaceUnsupportedError):
        boundary.list_directories()


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


def test_real_missing_path_traceback_is_redacted(tmp_path) -> None:
    root = tmp_path / "private-workspace"
    root.mkdir()
    boundary = WorkspaceBoundary(root)
    sensitive_name = "not-for-logs.txt"

    with pytest.raises(WorkspaceNotFoundError) as error:
        boundary.resolve_relative(f"private/{sensitive_name}", must_exist=True)

    rendered = "".join(traceback.format_exception(error.value))
    assert error.value.__cause__ is None
    assert str(root) not in rendered
    assert "[Errno" not in rendered
    assert "WinError" not in rendered
    assert "FileNotFoundError" not in rendered
    assert sensitive_name not in rendered


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
        lambda: boundary.open_directory_fd("project"),
        lambda: boundary.open_regular_file("project/notes.txt"),
        lambda: boundary.create_file_exclusive("project/new.txt"),
        lambda: boundary.validate_regular_file_at(0, "notes.txt"),
        lambda: boundary.unlink_regular_file("project/notes.txt"),
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
