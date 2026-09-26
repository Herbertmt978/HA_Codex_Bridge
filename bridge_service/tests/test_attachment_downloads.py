"""Downloads expose only confined, retained uploads through sealed snapshots."""

import os
import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from codex_bridge_service.app import create_app
from codex_bridge_service.feature_capabilities import readiness_capabilities
from codex_bridge_service.models import AttachmentRecord, RunMode, RuntimeProfile

HEADERS = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}
POSIX = pytest.mark.skipif(os.name == "nt", reason="sealed uploads require POSIX dir_fd")


def _uploaded(tmp_path):
    app = create_app(
        root_path=tmp_path / "data" / "bridge",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=tmp_path / "config" / "workspaces",
        runner_factory=lambda _storage: object(),
    )
    storage = app.state.storage
    project = storage.create_project(name="Images", root_path="images")
    thread = storage.create_thread(title="Images", project_id=project.project_id, mode=RunMode.EDIT)
    content = b"\x89PNG\r\n\x1a\nimage-content"
    client = TestClient(app)
    digest = hashlib.sha256(content).hexdigest()
    session = client.post(
        f"/threads/{thread.thread_id}/uploads", headers=HEADERS,
        json={"filename": "image.png", "mime_type": "image/png", "size_bytes": len(content), "sha256": digest},
    )
    assert session.status_code == 201
    upload_id = session.json()["upload_id"]
    assert client.put(
        f"/threads/{thread.thread_id}/uploads/{upload_id}/chunks/0",
        headers=HEADERS | {"Upload-Offset": "0", "Content-Length": str(len(content)), "X-Chunk-SHA256": digest},
        content=content,
    ).status_code == 200
    completed = client.post(f"/threads/{thread.thread_id}/uploads/{upload_id}/complete", headers=HEADERS)
    assert completed.status_code == 201
    attachment = AttachmentRecord.model_validate(completed.json())
    path = f"/threads/{thread.thread_id}/attachments/{attachment.attachment_id}"
    return app, storage, thread, attachment, content, path


def test_download_capability_requires_home_assistant_profile():
    for profile, expected in ((RuntimeProfile.HOME_ASSISTANT, True), (RuntimeProfile.EXTERNAL_LEGACY, False)):
        state = SimpleNamespace(storage=SimpleNamespace(runtime_profile=profile), feature_capabilities=("api_v1",))
        assert ("attachment_downloads" in readiness_capabilities(state)) is expected


def test_external_attachment_route_never_reads_an_arbitrary_path(tmp_path):
    app = create_app(root_path=tmp_path, auth_token="secret")
    response = TestClient(app).get("/threads/thr_unknown/attachments/att_unknown", headers=HEADERS)
    assert response.status_code == 404
    assert response.json() == {"detail": "attachment unavailable"}


@POSIX
def test_attachment_full_and_range_download_safe_headers_and_association(tmp_path):
    app, storage, thread, attachment, content, path = _uploaded(tmp_path)
    client = TestClient(app)
    assert client.get(path).status_code == 401
    full = client.get(path, headers=HEADERS)
    assert full.status_code == 200
    assert full.content == content
    assert full.headers["content-type"] == "application/octet-stream"
    assert full.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in full.headers["cache-control"]
    assert "image.png" in full.headers["content-disposition"]
    assert str(tmp_path) not in str(full.headers)
    partial = client.get(path, headers=HEADERS | {"Range": "bytes=0-7"})
    assert partial.status_code == 206
    assert partial.content == content[:8]
    assert partial.headers["content-range"] == f"bytes 0-7/{len(content)}"
    stale = client.get(path, headers=HEADERS | {"Range": "bytes=0-7", "If-Range": '"stale"'})
    assert stale.status_code == 200 and stale.content == content
    invalid = client.get(path, headers=HEADERS | {"Range": "bytes=0-1,4-5"})
    assert invalid.status_code == 416
    assert invalid.content == b""
    other = storage.create_thread(title="Other", project_id=thread.project_id, mode=RunMode.EDIT)
    wrong = client.get(f"/threads/{other.thread_id}/attachments/{attachment.attachment_id}", headers=HEADERS)
    assert wrong.status_code == 404


@POSIX
@pytest.mark.parametrize("tamper", ["symlink", "parent_symlink", "content", "size", "metadata"])
def test_attachment_download_fails_closed_on_tampering(tmp_path, tamper):
    app, storage, thread, attachment, content, path = _uploaded(tmp_path)
    stored = storage.uploads_dir / attachment.stored_path
    private = tmp_path / "outside-private.png"
    private.write_bytes(b"PRIVATE_SENTINEL")
    if tamper == "symlink":
        stored.unlink()
        stored.symlink_to(private)
    elif tamper == "parent_symlink":
        stored.parent.rename(stored.parent.with_name(stored.parent.name + "-old"))
        stored.parent.symlink_to(private.parent, target_is_directory=True)
    elif tamper in {"size", "content"}:
        stored.write_bytes(b"x" * (len(content) + (1 if tamper == "size" else 0)))
    else:
        record = storage.load_thread(thread.thread_id)
        record.attachments[0].stored_path = "../outside-private.png"
        storage._thread_path(thread.thread_id).write_text(record.model_dump_json())
    response = TestClient(app).get(path, headers=HEADERS)
    assert response.status_code == 400
    assert "PRIVATE_SENTINEL" not in response.text
    assert str(tmp_path) not in response.text
    assert storage._transient_quota().active_reservations == 0


@POSIX
def test_attachment_snapshot_immutable_and_quota_released(tmp_path):
    _app, storage, thread, attachment, content, _path = _uploaded(tmp_path)
    _record, stream, size = storage.open_attachment(thread.thread_id, attachment.attachment_id)
    stored = storage.uploads_dir / attachment.stored_path
    stored.write_bytes(b"changed-after-open")
    try:
        assert size == len(content)
        assert stream.read() == content
        assert storage._transient_quota().active_reservations == 1
    finally:
        stream.close()
    assert storage._transient_quota().active_reservations == 0


@POSIX
def test_attachment_snapshot_size_ceiling(tmp_path):
    app, storage, _thread, _attachment, _content, path = _uploaded(tmp_path)
    storage.resource_limits = replace(storage._resource_limits(), max_transient_snapshot_bytes=1)
    response = TestClient(app).get(path, headers=HEADERS)
    assert response.status_code == 413
    assert storage._transient_quota().active_reservations == 0
