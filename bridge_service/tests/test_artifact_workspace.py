import json
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from codex_bridge_service.app import create_app
from codex_bridge_service.models import (
    ArtifactRecord,
    ArtifactSource,
    RunMode,
    RuntimeProfile,
)
from codex_bridge_service.storage import BridgeStorage
from codex_bridge_service.workspace import (
    WorkspaceBoundaryError,
    WorkspaceEscapeError,
    WorkspaceExistsError,
    WorkspaceInputError,
    WorkspaceTypeError,
)


pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="secure Home Assistant artifact operations require POSIX dir_fd support",
)


def _home_assistant_thread(tmp_path: Path):
    state_root = tmp_path / "data" / "bridge"
    workspace_root = tmp_path / "config" / "workspaces"
    storage = BridgeStorage(
        root_path=state_root,
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
    )
    project = storage.create_project(name="Artifacts", root_path="projects/artifacts")
    thread = storage.create_thread(
        title="Artifacts",
        project_id=project.project_id,
        mode=RunMode.EDIT,
    )
    workspace = workspace_root.joinpath(*thread.workspace_path.split("/"))
    return storage, thread, state_root, workspace_root, workspace


def _home_assistant_app(tmp_path: Path):
    state_root = tmp_path / "data" / "bridge"
    workspace_root = tmp_path / "config" / "workspaces"
    app = create_app(
        root_path=state_root,
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=workspace_root,
        runner_factory=lambda _storage: object(),
    )
    storage = app.state.storage
    project = storage.create_project(name="API", root_path="projects/api")
    thread = storage.create_thread(
        title="API",
        project_id=project.project_id,
        mode=RunMode.EDIT,
    )
    workspace = workspace_root.joinpath(*thread.workspace_path.split("/"))
    return app, storage, thread, state_root, workspace_root, workspace


def test_home_assistant_sync_persists_only_owned_relative_artifact_locators(tmp_path) -> None:
    storage, thread, state_root, workspace_root, workspace = _home_assistant_thread(tmp_path)
    target = workspace / "reports" / "summary.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Summary\n", encoding="utf-8")

    artifacts = storage.sync_thread_artifacts(thread.thread_id)

    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.source is ArtifactSource.WORKSPACE
    assert artifact.filename == "summary.md"
    assert artifact.relative_path == "reports/summary.md"
    assert artifact.stored_path == f"{thread.workspace_path}/reports/summary.md"
    assert artifact.size_bytes == len(b"# Summary\n")
    persisted = storage._thread_path(thread.thread_id).read_text(encoding="utf-8")
    event = storage.list_thread_events(thread.thread_id)[-1]
    serialized = json.dumps(
        {"artifact": artifact.model_dump(), "event": event.model_dump()}
    ) + persisted
    assert event.event_type == "artifact.added"
    assert event.payload["source"] == "workspace"
    assert str(state_root) not in serialized
    assert str(workspace_root) not in serialized
    assert "/data/" not in serialized
    assert "/config/" not in serialized


@pytest.mark.parametrize(
    ("source", "stored_path", "relative_path", "expected_error"),
    [
        (
            "workspace",
            "/config/workspaces/projects/artifacts/report.md",
            "report.md",
            WorkspaceInputError,
        ),
        (
            "workspace",
            "C:/workspaces/projects/artifacts/report.md",
            "report.md",
            WorkspaceInputError,
        ),
        ("workspace", "projects/artifacts/../report.md", "../report.md", WorkspaceInputError),
        ("workspace", "projects/other/report.md", "report.md", WorkspaceEscapeError),
        (
            "workspace_archive",
            "projects/artifacts/report.md",
            "report.md",
            WorkspaceEscapeError,
        ),
    ],
)
def test_home_assistant_load_rejects_tampered_artifact_ownership(
    tmp_path,
    source: str,
    stored_path: str,
    relative_path: str,
    expected_error: type[WorkspaceBoundaryError],
) -> None:
    storage, thread, _, _, _ = _home_assistant_thread(tmp_path)
    target = storage._thread_path(thread.thread_id)
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["artifacts"] = [
        {
            "artifact_id": "art_tampered",
            "filename": "report.md",
            "mime_type": "text/markdown",
            "source": source,
            "stored_path": stored_path,
            "relative_path": relative_path,
            "size_bytes": 1,
        }
    ]
    target.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(expected_error) as error:
        storage.load_thread(thread.thread_id)

    assert "report.md" not in str(error.value)
    assert str(storage.root) not in str(error.value)


def test_home_assistant_sync_rejects_symlinks_and_special_files(tmp_path) -> None:
    storage, thread, _, _, workspace = _home_assistant_thread(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (workspace / "link.txt").symlink_to(outside)
    os.mkfifo(workspace / "pipe")

    with pytest.raises((WorkspaceEscapeError, WorkspaceTypeError)):
        storage.sync_thread_artifacts(thread.thread_id)

    assert storage.load_thread(thread.thread_id).artifacts == []
    assert outside.read_text(encoding="utf-8") == "outside"


def test_home_assistant_load_rejects_existing_symlink_and_special_artifacts(tmp_path) -> None:
    storage, thread, _, _, workspace = _home_assistant_thread(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (workspace / "link.txt").symlink_to(outside)
    os.mkfifo(workspace / "pipe")
    target = storage._thread_path(thread.thread_id)

    for filename, expected_error in (
        ("link.txt", WorkspaceEscapeError),
        ("pipe", WorkspaceTypeError),
    ):
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["artifacts"] = [
            {
                "artifact_id": "art_tampered",
                "filename": filename,
                "mime_type": "application/octet-stream",
                "source": "workspace",
                "stored_path": f"{thread.workspace_path}/{filename}",
                "relative_path": filename,
            }
        ]
        target.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(expected_error):
            storage.load_thread(thread.thread_id)


def test_home_assistant_concurrent_sync_deduplicates_and_stale_save_preserves_artifact(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, _, _, workspace = _home_assistant_thread(tmp_path)
    target = workspace / "result.txt"
    target.write_text("result", encoding="utf-8")
    stale = storage.load_thread(thread.thread_id)
    barrier = Barrier(2)
    original_walk = storage.workspace_boundary.walk_regular_files

    def synchronized_walk(*args, **kwargs):
        discovered = original_walk(*args, **kwargs)
        barrier.wait(timeout=5)
        return discovered

    monkeypatch.setattr(storage.workspace_boundary, "walk_regular_files", synchronized_walk)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(storage.sync_thread_artifacts, thread.thread_id)
        second = executor.submit(storage.sync_thread_artifacts, thread.thread_id)
        assert len(first.result()) == 1
        assert len(second.result()) == 1

    stale.title = "Stale writer"
    storage.save_thread(stale)
    persisted = storage.load_thread(thread.thread_id)
    events = [
        event
        for event in storage.list_thread_events(thread.thread_id)
        if event.event_type == "artifact.added"
    ]
    assert persisted.title == "Stale writer"
    assert len(persisted.artifacts) == 1
    assert len(events) == 1


def test_home_assistant_download_stream_is_descriptor_pinned_and_hardened(
    tmp_path,
    monkeypatch,
) -> None:
    app, storage, thread, state_root, workspace_root, workspace = _home_assistant_app(tmp_path)
    target = workspace / "report.html"
    moved = workspace / "report-original.html"
    target.write_bytes(b"trusted report")
    artifact = storage.sync_thread_artifacts(thread.thread_id)[0]
    original_open = storage.open_artifact
    tracked: dict[str, object] = {}
    client = TestClient(app)
    listed = client.get(
        f"/threads/{thread.thread_id}/artifacts",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"},
    )
    assert listed.status_code == 200
    assert listed.json()[0]["stored_path"] == f"{thread.workspace_path}/report.html"
    assert listed.json()[0]["source"] == "workspace"
    assert str(state_root) not in listed.text
    assert str(workspace_root) not in listed.text

    def open_then_replace(thread_id: str, artifact_id: str):
        opened_artifact, stream, size = original_open(thread_id, artifact_id)
        tracked["stream"] = stream
        target.rename(moved)
        target.write_bytes(b"hostile replacement")
        return opened_artifact, stream, size

    monkeypatch.setattr(storage, "open_artifact", open_then_replace)
    response = client.get(
        f"/threads/{thread.thread_id}/artifacts/{artifact.artifact_id}",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"},
    )

    assert response.status_code == 200
    assert response.content == b"trusted report"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-length"] == str(len(b"trusted report"))
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in response.headers["content-disposition"]
    assert "report.html" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "private, no-store, no-transform"
    assert tracked["stream"].closed is True
    serialized = json.dumps(dict(response.headers))
    assert str(state_root) not in serialized
    assert str(workspace_root) not in serialized


def test_home_assistant_artifact_download_honours_a_single_if_range(tmp_path) -> None:
    app, storage, thread, _, _, workspace = _home_assistant_app(tmp_path)
    target = workspace / "report.txt"
    target.write_bytes(b"0123456789")
    artifact = storage.sync_thread_artifacts(thread.thread_id)[0]
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1", "Range": "bytes=2-5"}
    initial = client.get(f"/threads/{thread.thread_id}/artifacts/{artifact.artifact_id}", headers=headers)

    assert initial.status_code == 206
    assert initial.content == b"2345"
    assert initial.headers["content-range"] == "bytes 2-5/10"
    assert initial.headers["accept-ranges"] == "bytes"
    assert initial.headers["etag"].startswith('"')
    replay = client.get(
        f"/threads/{thread.thread_id}/artifacts/{artifact.artifact_id}",
        headers=headers | {"If-Range": initial.headers["etag"]},
    )
    assert replay.status_code == 206
    assert replay.content == b"2345"
    unsatisfiable = client.get(
        f"/threads/{thread.thread_id}/artifacts/{artifact.artifact_id}",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1", "Range": "bytes=20-"},
    )
    assert unsatisfiable.status_code == 416
    assert unsatisfiable.headers["content-range"] == "bytes */10"
    assert unsatisfiable.headers["cache-control"] == "private, no-store, no-transform"
    assert unsatisfiable.headers["x-content-type-options"] == "nosniff"
    suffix = client.get(
        f"/threads/{thread.thread_id}/artifacts/{artifact.artifact_id}",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1", "Range": "bytes=-3"},
    )
    assert suffix.status_code == 206
    assert suffix.content == b"789"
    open_ended = client.get(
        f"/threads/{thread.thread_id}/artifacts/{artifact.artifact_id}",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1", "Range": "bytes=7-"},
    )
    assert open_ended.status_code == 206
    assert open_ended.content == b"789"
    ignored = client.get(
        f"/threads/{thread.thread_id}/artifacts/{artifact.artifact_id}",
        headers=headers | {"If-Range": '"other"'},
    )
    assert ignored.status_code == 200
    assert ignored.content == b"0123456789"
    for stale_if_range in (f"W/{initial.headers['etag']}", "Sun, 06 Nov 1994 08:49:37 GMT"):
        response = client.get(
            f"/threads/{thread.thread_id}/artifacts/{artifact.artifact_id}",
            headers=headers | {"If-Range": stale_if_range},
        )
        assert response.status_code == 200
        assert response.content == b"0123456789"
    for malformed in ("bytes=0-1,3-4", "bytes=-0", "items=0-1"):
        response = client.get(
            f"/threads/{thread.thread_id}/artifacts/{artifact.artifact_id}",
            headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1", "Range": malformed},
        )
        assert response.status_code == 416
    huge = client.get(
        f"/threads/{thread.thread_id}/artifacts/{artifact.artifact_id}",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1", "Range": f"bytes={'9' * 5000}-"},
    )
    assert huge.status_code == 416


def test_home_assistant_empty_artifact_range_keeps_forced_safe_headers(tmp_path) -> None:
    app, storage, thread, _, _, workspace = _home_assistant_app(tmp_path)
    target = workspace / "empty.html"
    target.write_bytes(b"")
    artifact = storage.sync_thread_artifacts(thread.thread_id)[0]

    response = TestClient(app).get(
        f"/threads/{thread.thread_id}/artifacts/{artifact.artifact_id}",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1", "Range": "bytes=0-"},
    )

    assert response.status_code == 416
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-range"] == "bytes */0"


def test_home_assistant_artifact_hash_failure_closes_lease(tmp_path, monkeypatch) -> None:
    app, storage, thread, _, _, workspace = _home_assistant_app(tmp_path)
    target = workspace / "lease.txt"
    target.write_bytes(b"lease")
    artifact = storage.sync_thread_artifacts(thread.thread_id)[0]
    original_open = storage.open_artifact
    captured: dict[str, object] = {}

    def track_open(thread_id: str, artifact_id: str):
        opened = original_open(thread_id, artifact_id)
        captured["stream"] = opened[1]
        return opened

    monkeypatch.setattr(storage, "open_artifact", track_open)
    monkeypatch.setattr(
        "codex_bridge_service.routes.artifacts._stream_sha256_etag",
        lambda _stream: (_ for _ in ()).throw(OSError("hash failed")),
    )
    response = TestClient(app, raise_server_exceptions=False).get(
        f"/threads/{thread.thread_id}/artifacts/{artifact.artifact_id}",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"},
    )

    assert response.status_code == 500
    assert captured["stream"].closed is True


def test_bounded_artifact_iterator_never_requests_more_than_one_mebibyte() -> None:
    from codex_bridge_service.routes.artifacts import _stream_and_close

    class RecordingStream(BytesIO):
        def __init__(self) -> None:
            super().__init__(b"x" * (2 * 1024 * 1024 + 7))
            self.read_sizes: list[int] = []

        def read(self, size: int = -1) -> bytes:
            self.read_sizes.append(size)
            return super().read(size)

    stream = RecordingStream()
    assert b"".join(_stream_and_close(stream)) == b"x" * (2 * 1024 * 1024 + 7)
    assert max(stream.read_sizes) <= 1024 * 1024
    assert stream.closed is True


@pytest.mark.parametrize(
    "mutated_content",
    [b"x", b"trusted report expanded after the response snapshot"],
)
def test_home_assistant_download_snapshot_ignores_same_inode_size_changes(
    tmp_path,
    monkeypatch,
    mutated_content: bytes,
) -> None:
    app, storage, thread, _, _, workspace = _home_assistant_app(tmp_path)
    original_content = b"trusted report"
    target = workspace / "mutable.txt"
    target.write_bytes(original_content)
    artifact = storage.sync_thread_artifacts(thread.thread_id)[0]
    original_open = storage.open_artifact

    def open_then_mutate(thread_id: str, artifact_id: str):
        opened = original_open(thread_id, artifact_id)
        target.write_bytes(mutated_content)
        return opened

    monkeypatch.setattr(storage, "open_artifact", open_then_mutate)
    response = TestClient(app).get(
        f"/threads/{thread.thread_id}/artifacts/{artifact.artifact_id}",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"},
    )

    assert response.status_code == 200
    assert response.content == original_content
    assert response.headers["content-length"] == str(len(original_content))
    assert target.read_bytes() == mutated_content


def test_home_assistant_artifact_api_uses_generic_missing_and_invalid_errors(
    tmp_path,
) -> None:
    app, storage, thread, state_root, workspace_root, workspace = _home_assistant_app(tmp_path)
    target = workspace / "gone.txt"
    target.write_text("gone", encoding="utf-8")
    artifact = storage.sync_thread_artifacts(thread.thread_id)[0]
    target.unlink()
    client = TestClient(app)

    missing = client.get(
        f"/threads/{thread.thread_id}/artifacts/{artifact.artifact_id}",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"},
    )
    assert missing.status_code == 404
    assert missing.json() == {"detail": "artifact file not found"}

    target.symlink_to(state_root / "secret")
    invalid = client.get(
        f"/threads/{thread.thread_id}/artifacts/{artifact.artifact_id}",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"},
    )
    assert invalid.status_code == 400
    assert invalid.json() == {"detail": "invalid artifact location"}
    serialized = missing.text + invalid.text
    assert str(state_root) not in serialized
    assert str(workspace_root) not in serialized


def test_home_assistant_archive_route_packages_owned_sources_with_relative_metadata(
    tmp_path,
) -> None:
    app, storage, thread, state_root, workspace_root, workspace = _home_assistant_app(tmp_path)
    workspace_file = workspace / "src" / "main.py"
    workspace_file.parent.mkdir(parents=True)
    workspace_file.write_text("print('safe')\n", encoding="utf-8")
    storage.attach_file(
        thread_id=thread.thread_id,
        filename="requirements.txt",
        mime_type="text/plain",
        content=b"fastapi\n",
        relative_path="deps/requirements.txt",
    )
    client = TestClient(app)
    response = client.post(
        f"/threads/{thread.thread_id}/artifacts/workspace-archive",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["source"] == "workspace_archive"
    assert payload["relative_path"] == payload["filename"]
    assert payload["stored_path"] == f"{thread.thread_id}/{payload['filename']}"
    serialized = response.text + storage._thread_path(thread.thread_id).read_text(
        encoding="utf-8"
    )
    assert str(state_root) not in serialized
    assert str(workspace_root) not in serialized
    archive_path = storage.artifacts_dir.joinpath(*payload["stored_path"].split("/"))
    with zipfile.ZipFile(archive_path) as archive:
        assert set(archive.namelist()) == {
            "uploads/deps/requirements.txt",
            "workspace/src/main.py",
        }
        assert archive.read("workspace/src/main.py") == b"print('safe')\n"
        assert archive.read("uploads/deps/requirements.txt") == b"fastapi\n"

    downloaded = client.get(
        f"/threads/{thread.thread_id}/artifacts/{payload['artifact_id']}",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"},
    )
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"PK")
    assert downloaded.headers["content-type"] == "application/octet-stream"


def test_home_assistant_empty_archive_contains_explanatory_readme(tmp_path) -> None:
    storage, thread, _, _, _ = _home_assistant_thread(tmp_path)

    artifact = storage.create_workspace_archive(thread.thread_id)

    archive_path = storage.artifacts_dir.joinpath(*artifact.stored_path.split("/"))
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == ["README.txt"]
        assert b"did not have any workspace files" in archive.read("README.txt")


def test_home_assistant_concurrent_archives_publish_unique_complete_records(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, _, _, workspace = _home_assistant_thread(tmp_path)
    (workspace / "result.txt").write_text("result", encoding="utf-8")
    barrier = Barrier(2)
    original_walk = storage.workspace_boundary.walk_regular_files

    def synchronized_walk(*args, **kwargs):
        discovered = original_walk(*args, **kwargs)
        barrier.wait(timeout=5)
        return discovered

    monkeypatch.setattr(storage.workspace_boundary, "walk_regular_files", synchronized_walk)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(storage.create_workspace_archive, thread.thread_id)
        second_future = executor.submit(storage.create_workspace_archive, thread.thread_id)
        archives = (first_future.result(), second_future.result())

    assert len({artifact.artifact_id for artifact in archives}) == 2
    assert len({artifact.stored_path for artifact in archives}) == 2
    persisted = storage.load_thread(thread.thread_id)
    assert len(persisted.artifacts) == 2
    for artifact in archives:
        target = storage.artifacts_dir.joinpath(*artifact.stored_path.split("/"))
        with zipfile.ZipFile(target) as archive:
            assert archive.read("workspace/result.txt") == b"result"


def test_home_assistant_archive_rejects_unsafe_workspace_without_partial_output(
    tmp_path,
) -> None:
    storage, thread, _, _, workspace = _home_assistant_thread(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (workspace / "link.txt").symlink_to(outside)

    with pytest.raises(WorkspaceEscapeError):
        storage.create_workspace_archive(thread.thread_id)

    assert list(storage.artifacts_dir.rglob("*.zip")) == []
    assert storage.load_thread(thread.thread_id).artifacts == []
    assert outside.read_text(encoding="utf-8") == "outside"


def test_home_assistant_archive_copy_failure_removes_partial_output(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, _, _, workspace = _home_assistant_thread(tmp_path)
    (workspace / "first.txt").write_text("first", encoding="utf-8")
    (workspace / "second.txt").write_text("second", encoding="utf-8")
    original_copy = storage.workspace_boundary.copy_regular_file_to_anonymous_lease
    copy_count = 0

    def fail_second_copy(relative, **kwargs):
        nonlocal copy_count
        copy_count += 1
        if copy_count == 2:
            raise WorkspaceTypeError()
        return original_copy(relative, **kwargs)

    monkeypatch.setattr(
        storage.workspace_boundary,
        "copy_regular_file_to_anonymous_lease",
        fail_second_copy,
    )
    with pytest.raises(WorkspaceTypeError):
        storage.create_workspace_archive(thread.thread_id)

    assert list(storage.artifacts_dir.rglob("*.zip")) == []
    assert storage.load_thread(thread.thread_id).artifacts == []


def test_home_assistant_archive_retries_exclusive_name_collision(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, _, _, workspace = _home_assistant_thread(tmp_path)
    (workspace / "result.txt").write_text("result", encoding="utf-8")
    original_create = storage.artifacts_boundary.create_file_exclusive
    attempts = 0

    def collide_once(relative):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise WorkspaceExistsError()
        return original_create(relative)

    monkeypatch.setattr(
        storage.artifacts_boundary,
        "create_file_exclusive",
        collide_once,
    )
    artifact = storage.create_workspace_archive(thread.thread_id)

    assert attempts == 2
    assert storage.artifacts_dir.joinpath(*artifact.stored_path.split("/")).is_file()


def test_home_assistant_archive_zip_close_failure_removes_partial_output(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, _, _, workspace = _home_assistant_thread(tmp_path)
    (workspace / "result.txt").write_text("result", encoding="utf-8")
    real_zip_file = zipfile.ZipFile

    class FailingCloseZipFile(real_zip_file):
        def close(self):
            if self.fp is None:
                return super().close()
            super().close()
            raise OSError("zip close failed")

    monkeypatch.setattr(
        "codex_bridge_service.storage.zipfile.ZipFile",
        FailingCloseZipFile,
    )
    with pytest.raises(OSError, match="zip close failed"):
        storage.create_workspace_archive(thread.thread_id)

    assert list(storage.artifacts_dir.rglob("*.zip")) == []
    assert storage.load_thread(thread.thread_id).artifacts == []


def test_home_assistant_archive_output_close_failure_prevents_publication(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, _, _, workspace = _home_assistant_thread(tmp_path)
    (workspace / "result.txt").write_text("result", encoding="utf-8")
    original_create = storage.artifacts_boundary.create_file_exclusive

    class FailingCloseStream:
        def __init__(self, stream) -> None:
            self._stream = stream
            self._failed = False

        def __getattr__(self, name):
            return getattr(self._stream, name)

        def close(self):
            self._stream.close()
            if not self._failed:
                self._failed = True
                raise OSError("artifact close failed")

    def create_failing_stream(relative):
        return FailingCloseStream(original_create(relative))

    monkeypatch.setattr(
        storage.artifacts_boundary,
        "create_file_exclusive",
        create_failing_stream,
    )
    with pytest.raises(OSError, match="artifact close failed"):
        storage.create_workspace_archive(thread.thread_id)

    assert list(storage.artifacts_dir.rglob("*.zip")) == []
    assert storage.load_thread(thread.thread_id).artifacts == []
    assert all(
        event.event_type != "artifact.added"
        for event in storage.list_thread_events(thread.thread_id)
    )


def test_home_assistant_archive_identity_failure_cleans_exclusive_file(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, _, _, workspace = _home_assistant_thread(tmp_path)
    (workspace / "result.txt").write_text("result", encoding="utf-8")
    monkeypatch.setattr(
        storage.artifacts_boundary,
        "identify_open_file",
        lambda _stream: (_ for _ in ()).throw(WorkspaceTypeError()),
    )

    with pytest.raises(WorkspaceTypeError):
        storage.create_workspace_archive(thread.thread_id)

    assert list(storage.artifacts_dir.rglob("*.zip")) == []
    assert storage.load_thread(thread.thread_id).artifacts == []


def test_home_assistant_archive_metadata_failure_removes_completed_output(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, _, _, workspace = _home_assistant_thread(tmp_path)
    (workspace / "result.txt").write_text("result", encoding="utf-8")

    def fail_commit(_record, _events):
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(
        storage,
        "_commit_prepared_thread_with_events_locked",
        fail_commit,
    )
    with pytest.raises(RuntimeError, match="metadata unavailable"):
        storage.create_workspace_archive(thread.thread_id)

    assert list(storage.artifacts_dir.rglob("*.zip")) == []
    raw = json.loads(storage._thread_path(thread.thread_id).read_text(encoding="utf-8"))
    assert raw["artifacts"] == []


def test_home_assistant_archive_uses_sealed_source_snapshot_during_replacement(
    tmp_path,
    monkeypatch,
) -> None:
    storage, thread, _, _, workspace = _home_assistant_thread(tmp_path)
    target = workspace / "mutable.txt"
    target.write_bytes(b"trusted")
    original_copy = storage.workspace_boundary.copy_regular_file_to_anonymous_lease

    def copy_then_replace(relative, **kwargs):
        lease = original_copy(relative, **kwargs)
        target.write_bytes(b"hostile replacement")
        return lease

    monkeypatch.setattr(
        storage.workspace_boundary,
        "copy_regular_file_to_anonymous_lease",
        copy_then_replace,
    )
    artifact = storage.create_workspace_archive(thread.thread_id)

    archive_path = storage.artifacts_dir.joinpath(*artifact.stored_path.split("/"))
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.read("workspace/mutable.txt") == b"trusted"
    assert target.read_bytes() == b"hostile replacement"


def test_home_assistant_thread_delete_removes_published_private_archives(tmp_path) -> None:
    storage, thread, _, _, workspace = _home_assistant_thread(tmp_path)
    (workspace / "result.txt").write_text("result", encoding="utf-8")
    artifact = storage.create_workspace_archive(thread.thread_id)
    archive_path = storage.artifacts_dir.joinpath(*artifact.stored_path.split("/"))
    assert archive_path.is_file()

    storage.delete_thread(thread.thread_id)

    assert not archive_path.exists()
    assert not storage._thread_path(thread.thread_id).exists()
    assert not storage._event_log_path(thread.thread_id).exists()


def test_workspace_artifact_source_is_additive_for_legacy_records() -> None:
    artifact = ArtifactRecord(
        artifact_id="art_legacy",
        filename="legacy.txt",
        mime_type="text/plain",
        stored_path="/legacy/legacy.txt",
        relative_path="legacy.txt",
    )

    assert artifact.source is ArtifactSource.WORKSPACE
