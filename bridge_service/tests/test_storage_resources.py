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
from codex_bridge_service.workspace import WorkspaceEscapeError

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
    original_save = storage.save_thread

    def fail_save(_record):
        raise RuntimeError("metadata failed")

    monkeypatch.setattr(storage, "save_thread", fail_save)
    with pytest.raises(RuntimeError, match="metadata failed"):
        storage.attach_file(
            thread_id=thread.thread_id,
            filename="metadata.bin",
            mime_type="application/octet-stream",
            content=BytesIO(b"123"),
        )

    assert storage._home_assistant_uploads_boundary().walk_regular_files(".") == ()
    assert storage.quota_manager.active_reservations == 0
    monkeypatch.setattr(storage, "save_thread", original_save)
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

    response = client.post(
        f"/threads/{thread.thread_id}/attachments",
        headers={"Authorization": "Bearer secret"},
        files={"file": ("too-large.bin", b"12", "application/octet-stream")},
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

    monkeypatch.setattr(app.state.storage, "attach_file", conflict)
    response = client.post(
        f"/threads/{thread.thread_id}/attachments",
        headers={"Authorization": "Bearer secret"},
        files={"file": ("retry.bin", b"1", "application/octet-stream")},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "reservation_conflict",
        "resource": "private",
        "retryable": True,
    }
