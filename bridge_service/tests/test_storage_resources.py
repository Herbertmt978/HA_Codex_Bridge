import errno
import os
import stat
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from codex_bridge_service.app import create_app
from codex_bridge_service.models import RunMode, RuntimeProfile
from codex_bridge_service.resource_limits import (
    QuotaExceededError,
    ReservationConflictError,
    ResourceLimits,
)
from codex_bridge_service.storage import BridgeStorage
from codex_bridge_service.workspace import WorkspaceEscapeError, WorkspaceTypeError

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="Home Assistant quota enforcement requires POSIX descriptor operations",
)


def _limits(**changes) -> ResourceLimits:
    return replace(
        ResourceLimits(),
        minimum_free_bytes=0,
        minimum_free_fraction=0,
        **changes,
    )


def _home_assistant_thread(
    tmp_path: Path,
    limits: ResourceLimits,
) -> tuple[BridgeStorage, object, Path]:
    state_root = tmp_path / "data" / "bridge"
    workspace_root = tmp_path / "config" / "workspaces"
    storage = BridgeStorage(
        root_path=state_root,
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
        resource_limits=limits,
    )
    project = storage.create_project(name="Quota", root_path="projects/quota")
    thread = storage.create_thread(
        title="Quota",
        project_id=project.project_id,
        mode=RunMode.EDIT,
    )
    workspace = workspace_root.joinpath(*thread.workspace_path.split("/"))
    return storage, thread, workspace


def test_external_legacy_profile_does_not_activate_home_assistant_quotas(tmp_path) -> None:
    storage = BridgeStorage(root_path=tmp_path / "legacy")

    assert storage.resource_limits is None
    assert storage.quota_manager is None
    assert storage.transient_quota_manager is None


def test_home_assistant_unknown_length_upload_stops_before_file_limit(tmp_path) -> None:
    storage, thread, _workspace = _home_assistant_thread(
        tmp_path,
        _limits(max_upload_file_bytes=4, max_private_bytes=20),
    )

    with pytest.raises(QuotaExceededError) as error:
        storage.attach_file(
            thread_id=thread.thread_id,
            filename="large.bin",
            mime_type="application/octet-stream",
            content=BytesIO(b"12345"),
        )

    assert error.value.resource == "private"
    assert storage.load_thread(thread.thread_id).attachments == []
    assert storage._home_assistant_uploads_boundary().walk_regular_files(".") == ()
    assert storage.quota_manager.active_reservations == 0


def test_home_assistant_exact_upload_limit_publishes_and_commits(tmp_path) -> None:
    storage, thread, _workspace = _home_assistant_thread(
        tmp_path,
        _limits(max_upload_file_bytes=4, max_private_bytes=20),
    )

    attachment = storage.attach_file(
        thread_id=thread.thread_id,
        filename="exact.bin",
        mime_type="application/octet-stream",
        content=BytesIO(b"1234"),
    )

    assert attachment.size_bytes == 4
    assert storage.quota_manager.active_reservations == 0


def _zip_payload(entries) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return output.getvalue()


def test_uploaded_zip_manifest_is_validated_before_publication(tmp_path) -> None:
    limits = _limits(
        max_upload_file_bytes=100_000,
        max_private_bytes=200_000,
        max_archive_expansion_ratio=2,
    )
    storage, thread, _workspace = _home_assistant_thread(tmp_path, limits)
    ratio_bomb = b"MZ" + (b"X" * 32) + _zip_payload(
        [("zeros.bin", b"0" * 10_000)]
    )

    with pytest.raises(QuotaExceededError) as error:
        storage.attach_file(
            thread_id=thread.thread_id,
            filename="innocent-name.bin",
            mime_type="application/octet-stream",
            content=BytesIO(ratio_bomb),
        )

    assert error.value.resource == "archive_ratio"
    assert storage.load_thread(thread.thread_id).attachments == []
    assert storage._home_assistant_uploads_boundary().walk_regular_files(".") == ()
    assert storage.quota_manager.active_reservations == 0

    with pytest.raises(QuotaExceededError):
        storage.attach_file(
            thread_id=thread.thread_id,
            filename="declared.zip",
            mime_type="application/octet-stream",
            content=BytesIO(b"not a zip"),
        )


def test_disguised_zip_bomb_with_trailing_data_is_rejected(tmp_path) -> None:
    storage, thread, _workspace = _home_assistant_thread(
        tmp_path,
        _limits(
            max_upload_file_bytes=100_000,
            max_private_bytes=200_000,
            max_archive_expansion_ratio=2,
        ),
    )
    payload = _zip_payload([("zeros.bin", b"0" * 10_000)]) + b"TRAILING-DATA"

    with pytest.raises(QuotaExceededError) as error:
        storage.attach_file(
            thread_id=thread.thread_id,
            filename="innocent-name.bin",
            mime_type="application/octet-stream",
            content=BytesIO(payload),
        )

    assert error.value.resource == "archive_ratio"
    assert storage.load_thread(thread.thread_id).attachments == []
    assert storage._home_assistant_uploads_boundary().walk_regular_files(".") == ()
    assert storage.quota_manager.active_reservations == 0


def test_uploaded_zip_rejects_special_members_and_accepts_safe_archive(tmp_path) -> None:
    storage, thread, _workspace = _home_assistant_thread(
        tmp_path,
        _limits(max_upload_file_bytes=100_000, max_private_bytes=200_000),
    )
    symlink = zipfile.ZipInfo("link")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    unsafe = _zip_payload([(symlink, b"target")])

    with pytest.raises(QuotaExceededError):
        storage.attach_file(
            thread_id=thread.thread_id,
            filename="unsafe.zip",
            mime_type="application/zip",
            content=BytesIO(unsafe),
        )

    safe = _zip_payload([("folder/readme.txt", b"safe")])
    attachment = storage.attach_file(
        thread_id=thread.thread_id,
        filename="safe.zip",
        mime_type="application/zip",
        content=BytesIO(safe),
    )
    assert attachment.size_bytes == len(safe)


def test_concurrent_uploads_cannot_double_spend_private_capacity(tmp_path) -> None:
    storage, thread, _workspace = _home_assistant_thread(
        tmp_path,
        _limits(max_upload_file_bytes=10, max_private_bytes=10),
    )
    barrier = Barrier(2)

    class RacingStream:
        def __init__(self, content: bytes) -> None:
            self.content = content
            self.read_once = False

        def read(self, _size: int) -> bytes:
            if self.read_once:
                return b""
            self.read_once = True
            barrier.wait(timeout=5)
            return self.content

    def upload(index: int):
        try:
            return storage.attach_file(
                thread_id=thread.thread_id,
                filename=f"race-{index}.bin",
                mime_type="application/octet-stream",
                content=RacingStream(b"123456"),
            )
        except QuotaExceededError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(upload, range(2)))

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, QuotaExceededError) for result in results) == 1
    assert len(storage.load_thread(thread.thread_id).attachments) == 1
    assert len(storage._home_assistant_uploads_boundary().walk_regular_files(".")) == 1
    assert storage.quota_manager.active_reservations == 0


def test_upload_write_failure_removes_partial_file_and_releases_quota(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, _workspace = _home_assistant_thread(
        tmp_path,
        _limits(max_upload_file_bytes=10, max_private_bytes=10),
    )
    original_create = storage.uploads_boundary.create_file_exclusive

    class FailAfterPartialWrite:
        def __init__(self, output) -> None:
            self.output = output
            self.calls = 0

        def write(self, content):
            self.calls += 1
            if self.calls == 1:
                self.output.write(content[:1])
                return 1
            raise OSError("write failed")

        def __getattr__(self, name):
            return getattr(self.output, name)

    def create_failing_output(relative):
        return FailAfterPartialWrite(original_create(relative))

    monkeypatch.setattr(
        storage.uploads_boundary,
        "create_file_exclusive",
        create_failing_output,
    )
    with pytest.raises(OSError, match="write failed"):
        storage.attach_file(
            thread_id=thread.thread_id,
            filename="partial.bin",
            mime_type="application/octet-stream",
            content=BytesIO(b"123"),
        )

    assert storage._home_assistant_uploads_boundary().walk_regular_files(".") == ()
    assert storage.quota_manager.active_reservations == 0


def test_upload_metadata_failure_removes_completed_file_and_allows_retry(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, _workspace = _home_assistant_thread(
        tmp_path,
        _limits(max_upload_file_bytes=10, max_private_bytes=10),
    )
    original_commit = storage._commit_prepared_thread_with_events_locked

    def fail_commit(_record, _events):
        raise RuntimeError("metadata failed")

    monkeypatch.setattr(
        storage,
        "_commit_prepared_thread_with_events_locked",
        fail_commit,
    )
    with pytest.raises(RuntimeError, match="metadata failed"):
        storage.attach_file(
            thread_id=thread.thread_id,
            filename="metadata.bin",
            mime_type="application/octet-stream",
            content=BytesIO(b"123"),
        )

    assert storage._home_assistant_uploads_boundary().walk_regular_files(".") == ()
    assert storage.quota_manager.active_reservations == 0
    monkeypatch.setattr(
        storage,
        "_commit_prepared_thread_with_events_locked",
        original_commit,
    )
    attachment = storage.attach_file(
        thread_id=thread.thread_id,
        filename="retry.bin",
        mime_type="application/octet-stream",
        content=BytesIO(b"123"),
    )
    assert attachment.size_bytes == 3


def test_home_assistant_upload_preserves_free_space_floor(tmp_path) -> None:
    storage, thread, _workspace = _home_assistant_thread(
        tmp_path,
        replace(
            _limits(max_upload_file_bytes=10, max_private_bytes=10),
            minimum_free_bytes=(1 << 63) - 1,
        ),
    )

    with pytest.raises(QuotaExceededError):
        storage.attach_file(
            thread_id=thread.thread_id,
            filename="blocked.bin",
            mime_type="application/octet-stream",
            content=BytesIO(b"1"),
        )

    assert storage._home_assistant_uploads_boundary().walk_regular_files(".") == ()


def test_workspace_quota_blocks_artifact_scan_without_publication(tmp_path) -> None:
    storage, thread, workspace = _home_assistant_thread(
        tmp_path,
        _limits(max_workspace_bytes=5),
    )
    (workspace / "large.bin").write_bytes(b"123456")

    with pytest.raises(QuotaExceededError) as error:
        storage.sync_thread_artifacts(thread.thread_id)

    assert error.value.resource == "workspace"
    assert storage.load_thread(thread.thread_id).artifacts == []


def test_aggregate_workspace_quota_does_not_block_artifact_scan(
    tmp_path,
) -> None:
    storage, _thread, _workspace = _home_assistant_thread(
        tmp_path,
        _limits(max_workspace_bytes=5),
    )
    thread = storage.create_thread(title="Primary quota", mode=RunMode.EDIT)
    peer = storage.create_thread(
        title="Peer quota",
        mode=RunMode.EDIT,
    )
    workspace_root = storage.workspace_root
    assert workspace_root is not None
    workspace = workspace_root.joinpath(*thread.workspace_path.split("/"))
    peer_workspace = workspace_root.joinpath(*peer.workspace_path.split("/"))
    (workspace / "report.pdf").write_bytes(b"123")
    (peer_workspace / "peer.pdf").write_bytes(b"456")

    artifacts = storage.sync_thread_artifacts(thread.thread_id)

    assert [artifact.filename for artifact in artifacts] == ["report.pdf"]

    with pytest.raises(QuotaExceededError) as error:
        storage.reserve_workspace_mutation()

    assert error.value.resource == "workspace"


def test_aggregate_workspace_quota_does_not_block_archive(tmp_path) -> None:
    storage, _thread, _workspace = _home_assistant_thread(
        tmp_path,
        _limits(max_workspace_bytes=5),
    )
    thread = storage.create_thread(title="Primary quota", mode=RunMode.EDIT)
    peer = storage.create_thread(
        title="Peer quota",
        mode=RunMode.EDIT,
    )
    workspace_root = storage.workspace_root
    assert workspace_root is not None
    workspace = workspace_root.joinpath(*thread.workspace_path.split("/"))
    peer_workspace = workspace_root.joinpath(*peer.workspace_path.split("/"))
    (workspace / "report.pdf").write_bytes(b"123")
    (peer_workspace / "peer.pdf").write_bytes(b"456")

    artifact = storage.create_workspace_archive(thread.thread_id)

    assert artifact.filename.endswith(".zip")
    assert artifact.size_bytes is not None and artifact.size_bytes > 0


def test_read_paths_ignore_unmeasurable_aggregate_root_but_mutations_fail_closed(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, workspace = _home_assistant_thread(tmp_path, _limits())
    skill = workspace / ".agents" / "skills" / "example" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Example\n", encoding="utf-8")
    (workspace / "report.pdf").write_bytes(b"%PDF-1.7\n")
    boundary = storage._home_assistant_boundary()

    def aggregate_boundary_error(relative, *args, **kwargs):
        assert relative == "."
        raise WorkspaceEscapeError()

    monkeypatch.setattr(boundary, "measure_regular_files", aggregate_boundary_error)

    artifacts = storage.sync_thread_artifacts(thread.thread_id)

    assert sorted(artifact.relative_path for artifact in artifacts) == [
        ".agents/skills/example/SKILL.md",
        "report.pdf",
    ]
    archive = storage.create_workspace_archive(thread.thread_id)

    assert archive.filename.endswith(".zip")

    with pytest.raises(ReservationConflictError) as error:
        storage.reserve_workspace_mutation()

    assert error.value.resource == "filesystem_scan"


def test_real_unopenable_stale_self_test_peer_does_not_block_pdf_read_paths(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "config" / "workspaces"
    app = create_app(
        root_path=tmp_path / "data" / "bridge",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
        resource_limits=_limits(),
        runner_factory=lambda _storage: object(),
    )
    project = app.state.storage.create_project(
        name="PDF boundary",
        root_path="projects/pdf-boundary",
    )
    thread = app.state.storage.create_thread(
        title="PDF boundary",
        project_id=project.project_id,
        mode=RunMode.EDIT,
    )
    workspace = workspace_root.joinpath(*thread.workspace_path.split("/"))
    (workspace / "report.pdf").write_bytes(b"%PDF-1.7\n")
    stale = workspace_root / (
        ".sandbox-self-test-0123456789abcdef0123456789abcdef"
    )
    stale.mkdir(mode=0o700)
    stale.chmod(0)
    original_open = os.open
    denied_attempts = 0

    def deny_stale_descriptor(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal denied_attempts
        if path == stale.name and dir_fd is not None:
            denied_attempts += 1
            raise PermissionError(errno.EACCES, "stale probe is unreadable", path)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", deny_stale_descriptor)
    headers = {
        "Authorization": "Bearer secret",
        "X-Codex-Bridge-Api": "1",
    }
    try:
        with pytest.raises(WorkspaceTypeError):
            app.state.storage._home_assistant_boundary().measure_regular_files(".")

        assert denied_attempts == 1

        client = TestClient(app)
        listed = client.get(
            f"/threads/{thread.thread_id}/artifacts",
            headers=headers,
        )

        assert listed.status_code == 200
        assert [
            (item["filename"], item["relative_path"])
            for item in listed.json()
        ] == [("report.pdf", "report.pdf")]

        archived = client.post(
            f"/threads/{thread.thread_id}/artifacts/workspace-archive",
            headers=headers,
        )

        assert archived.status_code == 201
        archive_payload = archived.json()
        assert archive_payload["filename"].endswith(".zip")
        with app.state.storage.artifacts_boundary.open_regular_file(
            archive_payload["stored_path"]
        ) as stream:
            with zipfile.ZipFile(stream) as archive:
                assert archive.namelist() == ["workspace/report.pdf"]
                assert archive.read("workspace/report.pdf") == b"%PDF-1.7\n"
        assert denied_attempts == 1
    finally:
        stale.chmod(0o700)


def test_selected_workspace_unsafe_entry_still_blocks_read_paths(
    tmp_path,
) -> None:
    storage, thread, workspace = _home_assistant_thread(tmp_path, _limits())
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.7\n")
    (workspace / "unsafe.pdf").symlink_to(outside)

    with pytest.raises(WorkspaceEscapeError):
        storage.sync_thread_artifacts(thread.thread_id)

    with pytest.raises(WorkspaceEscapeError):
        storage.create_workspace_archive(thread.thread_id)


def test_artifact_sync_uses_bounded_manifest_when_workspace_ledger_check_conflicts(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, workspace = _home_assistant_thread(tmp_path, _limits())
    (workspace / "report.pdf").write_bytes(b"%PDF-1.7\n")
    quota_manager = storage.quota_manager
    assert quota_manager is not None

    def conflict(_pool: str, *, additional_bytes: int = 0) -> None:
        raise ReservationConflictError("workspace")

    monkeypatch.setattr(quota_manager, "check", conflict)

    artifacts = storage.sync_thread_artifacts(thread.thread_id)

    assert [(artifact.filename, artifact.size_bytes) for artifact in artifacts] == [
        ("report.pdf", 9)
    ]


def test_workspace_archive_uses_its_bounded_manifest_when_workspace_ledger_check_conflicts(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, workspace = _home_assistant_thread(tmp_path, _limits())
    (workspace / "report.pdf").write_bytes(b"%PDF-1.7\n")
    quota_manager = storage.quota_manager
    assert quota_manager is not None

    def conflict(_pool: str, *, additional_bytes: int = 0) -> None:
        raise ReservationConflictError("workspace")

    monkeypatch.setattr(quota_manager, "check", conflict)

    artifact = storage.create_workspace_archive(thread.thread_id)

    assert artifact.filename.endswith(".zip")
    assert artifact.size_bytes is not None and artifact.size_bytes > 0


def test_artifact_publication_never_exceeds_download_snapshot_ceiling(tmp_path) -> None:
    storage, thread, workspace = _home_assistant_thread(
        tmp_path,
        _limits(max_transient_snapshot_bytes=5),
    )
    (workspace / "large.bin").write_bytes(b"123456")

    with pytest.raises(QuotaExceededError) as error:
        storage.sync_thread_artifacts(thread.thread_id)

    assert error.value.resource == "artifact_snapshot"
    assert storage.load_thread(thread.thread_id).artifacts == []


def test_artifact_sync_rejects_growth_between_scan_and_publication(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, workspace = _home_assistant_thread(tmp_path, _limits())
    target = workspace / "mutable.bin"
    target.write_bytes(b"123")
    original_stat = storage.workspace_boundary.regular_file_stat
    calls = 0

    def stat_then_grow(relative):
        nonlocal calls
        result = original_stat(relative)
        calls += 1
        if calls == 1:
            target.write_bytes(b"123456")
        return result

    monkeypatch.setattr(
        storage.workspace_boundary,
        "regular_file_stat",
        stat_then_grow,
    )
    with pytest.raises(WorkspaceEscapeError):
        storage.sync_thread_artifacts(thread.thread_id)

    assert storage.load_thread(thread.thread_id).artifacts == []


def test_workspace_growth_observer_is_ready_for_runtime_watchdog(tmp_path) -> None:
    storage, _thread, workspace = _home_assistant_thread(
        tmp_path,
        _limits(max_workspace_bytes=5),
    )
    reservation = storage.reserve_workspace_mutation()
    (workspace / "large.bin").write_bytes(b"123456")

    with pytest.raises(QuotaExceededError):
        storage.observe_workspace_growth(reservation)

    reservation.release()


def test_attachment_snapshots_hold_and_release_aggregate_transient_quota(tmp_path) -> None:
    storage, thread, _workspace = _home_assistant_thread(
        tmp_path,
        _limits(
            max_upload_file_bytes=10,
            max_private_bytes=20,
            max_transient_snapshot_bytes=5,
        ),
    )
    storage.attach_file(
        thread_id=thread.thread_id,
        filename="one.bin",
        mime_type="application/octet-stream",
        content=BytesIO(b"123"),
    )
    storage.attach_file(
        thread_id=thread.thread_id,
        filename="two.bin",
        mime_type="application/octet-stream",
        content=BytesIO(b"456"),
    )

    with pytest.raises(QuotaExceededError):
        storage.lease_run_attachments(storage.load_thread(thread.thread_id))

    assert storage.transient_quota_manager.active_reservations == 0


def test_artifact_snapshot_reservation_lives_until_download_stream_closes(tmp_path) -> None:
    storage, thread, workspace = _home_assistant_thread(
        tmp_path,
        _limits(max_transient_snapshot_bytes=5),
    )
    (workspace / "artifact.bin").write_bytes(b"123")
    artifact = storage.sync_thread_artifacts(thread.thread_id)[0]

    _record, stream, size_bytes = storage.open_artifact(
        thread.thread_id,
        artifact.artifact_id,
    )

    assert size_bytes == 3
    assert storage.transient_quota_manager.active_reservations == 1
    assert stream.read() == b"123"
    stream.close()
    assert storage.transient_quota_manager.active_reservations == 0


def test_concurrent_artifact_snapshots_share_transient_capacity(tmp_path) -> None:
    storage, thread, workspace = _home_assistant_thread(
        tmp_path,
        _limits(max_transient_snapshot_bytes=5),
    )
    (workspace / "artifact.bin").write_bytes(b"123")
    artifact = storage.sync_thread_artifacts(thread.thread_id)[0]
    _record, first_stream, _size = storage.open_artifact(
        thread.thread_id,
        artifact.artifact_id,
    )

    with pytest.raises(QuotaExceededError):
        storage.open_artifact(thread.thread_id, artifact.artifact_id)

    first_stream.close()
    _record, retry_stream, _size = storage.open_artifact(
        thread.thread_id,
        artifact.artifact_id,
    )
    retry_stream.close()
    assert storage.transient_quota_manager.active_reservations == 0


def test_crash_residue_is_counted_and_release_allows_retry(tmp_path) -> None:
    state_root = tmp_path / "data" / "bridge"
    residue = state_root / "uploads" / "orphan" / "partial.bin"
    residue.parent.mkdir(parents=True)
    residue.write_bytes(b"123456")
    workspace_root = tmp_path / "config" / "workspaces"
    storage = BridgeStorage(
        root_path=state_root,
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
        resource_limits=_limits(max_upload_file_bytes=5, max_private_bytes=5),
    )
    project = storage.create_project(name="Recovery", root_path="projects/recovery")
    thread = storage.create_thread(
        title="Recovery",
        project_id=project.project_id,
        mode=RunMode.EDIT,
    )

    with pytest.raises(QuotaExceededError):
        storage.attach_file(
            thread_id=thread.thread_id,
            filename="blocked.bin",
            mime_type="application/octet-stream",
            content=BytesIO(b"1"),
        )

    storage._home_assistant_uploads_boundary().unlink_regular_file(
        "orphan/partial.bin"
    )
    attachment = storage.attach_file(
        thread_id=thread.thread_id,
        filename="retry.bin",
        mime_type="application/octet-stream",
        content=BytesIO(b"1"),
    )
    assert attachment.size_bytes == 1


def test_archive_entry_and_expanded_limits_fail_before_output_creation(tmp_path) -> None:
    storage, thread, workspace = _home_assistant_thread(
        tmp_path / "entries",
        _limits(max_archive_entries=1),
    )
    (workspace / "one.txt").write_text("one", encoding="utf-8")
    (workspace / "two.txt").write_text("two", encoding="utf-8")

    with pytest.raises(QuotaExceededError) as entries_error:
        storage.create_workspace_archive(thread.thread_id)

    assert entries_error.value.resource == "archive_entries"
    assert storage._home_assistant_artifacts_boundary().walk_regular_files(".") == ()

    storage, thread, workspace = _home_assistant_thread(
        tmp_path / "expanded",
        _limits(max_archive_expanded_bytes=5),
    )
    (workspace / "large.txt").write_bytes(b"123456")

    with pytest.raises(QuotaExceededError) as expanded_error:
        storage.create_workspace_archive(thread.thread_id)

    assert expanded_error.value.resource == "archive_expanded"
    assert storage._home_assistant_artifacts_boundary().walk_regular_files(".") == ()


def test_archive_output_quota_failure_removes_partial_file_and_reservation(tmp_path) -> None:
    storage, thread, workspace = _home_assistant_thread(
        tmp_path,
        _limits(max_private_bytes=5, max_archive_expanded_bytes=100),
    )
    (workspace / "payload.txt").write_bytes(b"123456")

    with pytest.raises(QuotaExceededError):
        storage.create_workspace_archive(thread.thread_id)

    assert storage._home_assistant_artifacts_boundary().walk_regular_files(".") == ()
    assert storage.load_thread(thread.thread_id).artifacts == []
    assert storage.quota_manager.active_reservations == 0


def test_artifact_list_ignores_unmeasurable_aggregate_root(
    tmp_path,
    monkeypatch,
) -> None:
    app = create_app(
        root_path=tmp_path / "data" / "bridge",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=tmp_path / "config" / "workspaces",
        resource_limits=_limits(),
        runner_factory=lambda _storage: object(),
    )
    project = app.state.storage.create_project(name="API", root_path="projects/api")
    thread = app.state.storage.create_thread(
        title="API",
        project_id=project.project_id,
        mode=RunMode.EDIT,
    )

    def boundary_error(*_args, **_kwargs):
        raise WorkspaceEscapeError()

    monkeypatch.setattr(
        app.state.storage._home_assistant_boundary(),
        "measure_regular_files",
        boundary_error,
    )

    response = TestClient(app).get(
        f"/threads/{thread.thread_id}/artifacts",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"},
    )

    assert response.status_code == 200
    assert response.json() == []


def test_resource_errors_have_typed_http_responses(tmp_path, monkeypatch) -> None:
    state_root = tmp_path / "data" / "bridge"
    workspace_root = tmp_path / "config" / "workspaces"
    app = create_app(
        root_path=state_root,
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
        resource_limits=_limits(max_upload_file_bytes=1, max_private_bytes=10),
        runner_factory=lambda _storage: object(),
    )
    project = app.state.storage.create_project(name="API", root_path="projects/api")
    thread = app.state.storage.create_thread(
        title="API",
        project_id=project.project_id,
        mode=RunMode.EDIT,
    )
    client = TestClient(app)
    upload_request = {
        "filename": "quota.bin",
        "size_bytes": 1,
        "sha256": "6b86b273ff34fce19d6b804eff5a3f5747ada4eaa22f1d49c01e52ddb7875b4b",
    }

    response = client.post(
        f"/threads/{thread.thread_id}/uploads",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"},
        json=upload_request,
    )

    assert response.status_code == 413
    assert response.json() == {
        "detail": {
            "code": "quota_exceeded",
            "resource": "private",
            "retryable": False,
        }
    }

    def conflict(**_kwargs):
        raise ReservationConflictError("private")

    monkeypatch.setattr(app.state.storage, "create_upload_session", conflict)
    response = client.post(
        f"/threads/{thread.thread_id}/uploads",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"},
        json=upload_request,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "reservation_conflict",
        "resource": "private",
        "retryable": True,
    }
