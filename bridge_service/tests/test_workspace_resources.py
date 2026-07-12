import os

import pytest

from codex_bridge_service.workspace import (
    WorkspaceBoundary,
    WorkspaceEscapeError,
    WorkspaceResourceLimitError,
)

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="secure descriptor-rooted resource accounting is unavailable",
)


def test_descriptor_manifest_and_usage_count_regular_files(tmp_path) -> None:
    root = tmp_path / "workspace"
    (root / "nested").mkdir(parents=True)
    (root / "alpha.txt").write_bytes(b"abc")
    (root / "nested" / "beta.bin").write_bytes(b"12345")
    boundary = WorkspaceBoundary(root)

    manifest = boundary.manifest_regular_files(".", reject_unsafe=True)
    usage = boundary.measure_regular_files(".", reject_unsafe=True)

    assert manifest.files == ("alpha.txt", "nested/beta.bin")
    assert manifest.usage.entry_count == 2
    assert manifest.usage.logical_bytes == 8
    assert manifest.usage.allocated_bytes >= 8
    assert usage == manifest.usage


def test_descriptor_scan_stops_before_entry_or_byte_limit_is_crossed(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "one.txt").write_bytes(b"123")
    (root / "two.txt").write_bytes(b"456")
    boundary = WorkspaceBoundary(root)

    with pytest.raises(WorkspaceResourceLimitError) as entries_error:
        boundary.manifest_regular_files(".", max_entries=1)
    with pytest.raises(WorkspaceResourceLimitError) as bytes_error:
        boundary.measure_regular_files(".", max_bytes=5)

    assert entries_error.value.resource == "entries"
    assert bytes_error.value.resource == "bytes"


def test_descriptor_usage_conservatively_counts_hardlink_names(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    source = root / "source.bin"
    source.write_bytes(b"1234")
    os.link(source, root / "alias.bin")
    boundary = WorkspaceBoundary(root)

    usage = boundary.measure_regular_files(".")

    assert usage.entry_count == 2
    assert usage.logical_bytes == 8


def test_filesystem_space_uses_the_held_root_descriptor(tmp_path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = WorkspaceBoundary(first_root)
    second = WorkspaceBoundary(second_root)

    first_space = first.filesystem_space()
    second_space = second.filesystem_space()

    assert first_space.filesystem_id == second_space.filesystem_id
    assert 0 <= first_space.free_bytes <= first_space.total_bytes
    assert first_space.total_bytes > 0


def test_regular_file_stat_is_descriptor_rooted_and_rejects_links(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "file.bin").write_bytes(b"12345")
    boundary = WorkspaceBoundary(root)

    file_stat = boundary.regular_file_stat("file.bin")

    assert file_stat.size_bytes == 5
    assert file_stat.allocated_bytes >= 5
    assert file_stat.identity.device >= 0
    assert file_stat.identity.inode > 0

    (root / "link.bin").symlink_to(root / "file.bin")
    with pytest.raises(WorkspaceEscapeError):
        boundary.regular_file_stat("link.bin")


def test_anonymous_snapshot_rejects_oversized_source_before_copy(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "large.bin").write_bytes(b"12345")
    boundary = WorkspaceBoundary(root)

    with pytest.raises(WorkspaceResourceLimitError) as error:
        boundary.copy_regular_file_to_anonymous_lease(
            "large.bin",
            max_bytes=4,
        )

    assert error.value.resource == "bytes"


def test_readonly_duplicate_stays_on_created_inode_after_locator_replacement(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    boundary = WorkspaceBoundary(root)
    output = boundary.create_file_exclusive("upload.bin")
    output.write(b"trusted")
    output.flush()

    with boundary.open_readonly_duplicate(output) as reader:
        (root / "upload.bin").rename(root / "moved.bin")
        (root / "upload.bin").write_bytes(b"replacement")
        assert reader.read() == b"trusted"

    output.close()
