import json
import os
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
        headers={"Authorization": "Bearer secret"},
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
        headers={"Authorization": "Bearer secret"},
    )

    assert response.status_code == 200
    assert response.content == b"trusted report"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-length"] == str(len(b"trusted report"))
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in response.headers["content-disposition"]
    assert "report.html" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "private, no-store"
    assert tracked["stream"].closed is True
    serialized = json.dumps(dict(response.headers))
    assert str(state_root) not in serialized
    assert str(workspace_root) not in serialized


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
        headers={"Authorization": "Bearer secret"},
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
        headers={"Authorization": "Bearer secret"},
    )
    assert missing.status_code == 404
    assert missing.json() == {"detail": "artifact file not found"}

    target.symlink_to(state_root / "secret")
    invalid = client.get(
        f"/threads/{thread.thread_id}/artifacts/{artifact.artifact_id}",
        headers={"Authorization": "Bearer secret"},
    )
    assert invalid.status_code == 400
    assert invalid.json() == {"detail": "invalid artifact location"}
    serialized = missing.text + invalid.text
    assert str(state_root) not in serialized
    assert str(workspace_root) not in serialized


def test_home_assistant_archive_route_fails_closed_until_secure_archive_support(tmp_path) -> None:
    app, _storage, thread, _, _, _ = _home_assistant_app(tmp_path)
    response = TestClient(app).post(
        f"/threads/{thread.thread_id}/artifacts/workspace-archive",
        headers={"Authorization": "Bearer secret"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "workspace archive unavailable"}


def test_workspace_artifact_source_is_additive_for_legacy_records() -> None:
    artifact = ArtifactRecord(
        artifact_id="art_legacy",
        filename="legacy.txt",
        mime_type="text/plain",
        stored_path="/legacy/legacy.txt",
        relative_path="legacy.txt",
    )

    assert artifact.source is ArtifactSource.WORKSPACE
