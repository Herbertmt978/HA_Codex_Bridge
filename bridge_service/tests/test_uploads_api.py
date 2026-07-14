import hashlib
import json
import os
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codex_bridge_service.app import create_app
from codex_bridge_service.models import RunMode, RuntimeProfile
from codex_bridge_service.models import AttachmentRecord
from codex_bridge_service.resource_limits import QuotaExceededError, ResourceLimits
from codex_bridge_service.storage import BridgeStorage
from codex_bridge_service.storage import UploadConflictError
from codex_bridge_service.storage import UploadNotFoundError
from codex_bridge_service.workspace import WorkspaceNotFoundError


def test_resumable_attachment_record_exposes_sha256() -> None:
    attachment = AttachmentRecord(
        attachment_id="att_upload",
        filename="notes.txt",
        mime_type="text/plain",
        stored_path="thr_upload/notes.txt",
        size_bytes=3,
        sha256="a" * 64,
    )
    assert attachment.model_dump()["sha256"] == "a" * 64


def _app_thread(tmp_path: Path):
    app = create_app(
        root_path=tmp_path / "data" / "bridge",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=tmp_path / "config" / "workspaces",
        runner_factory=lambda _storage: object(),
    )
    storage = app.state.storage
    project = storage.create_project(name="Uploads", root_path="projects/uploads")
    thread = storage.create_thread(title="Uploads", project_id=project.project_id, mode=RunMode.EDIT)
    return app, storage, thread


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_resumable_upload_publishes_one_checksum_verified_attachment(tmp_path) -> None:
    app, storage, thread = _app_thread(tmp_path)
    payload = b"resumable upload bytes"
    digest = hashlib.sha256(payload).hexdigest()
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}

    created = client.post(
        f"/threads/{thread.thread_id}/uploads",
        headers=headers,
        json={"filename": "notes.txt", "mime_type": "text/plain", "size_bytes": len(payload), "sha256": digest},
    )
    assert created.status_code == 201
    session = created.json()
    assert session["chunk_size"] == 8 * 1024 * 1024
    assert session["received_indices"] == []

    chunk_headers = headers | {
        "Upload-Offset": "0",
        "Content-Length": str(len(payload)),
        "X-Chunk-SHA256": digest,
    }
    uploaded = client.put(
        f"/threads/{thread.thread_id}/uploads/{session['upload_id']}/chunks/0",
        headers=chunk_headers,
        content=payload,
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["received_indices"] == [0]
    retry = client.put(
        f"/threads/{thread.thread_id}/uploads/{session['upload_id']}/chunks/0",
        headers=chunk_headers,
        content=payload,
    )
    assert retry.status_code == 200

    completed = client.post(
        f"/threads/{thread.thread_id}/uploads/{session['upload_id']}/complete",
        headers=headers,
    )
    assert completed.status_code == 201
    attachment = completed.json()
    assert attachment["sha256"] == digest
    assert attachment["size_bytes"] == len(payload)
    assert client.post(
        f"/threads/{thread.thread_id}/uploads/{session['upload_id']}/complete",
        headers=headers,
    ).json()["attachment_id"] == attachment["attachment_id"]
    assert len(storage.load_thread(thread.thread_id).attachments) == 1


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_resumable_upload_rejects_conflicting_chunk_and_wrong_thread(tmp_path) -> None:
    app, storage, thread = _app_thread(tmp_path)
    other = storage.create_thread(title="Other", project_id=thread.project_id, mode=RunMode.EDIT)
    payload = b"one"
    digest = hashlib.sha256(payload).hexdigest()
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}
    session = client.post(
        f"/threads/{thread.thread_id}/uploads", headers=headers,
        json={"filename": "one.txt", "size_bytes": 3, "sha256": digest},
    ).json()
    upload_headers = headers | {"Upload-Offset": "0", "Content-Length": "3", "X-Chunk-SHA256": digest}
    assert client.put(f"/threads/{thread.thread_id}/uploads/{session['upload_id']}/chunks/0", headers=upload_headers, content=payload).status_code == 200
    assert client.put(f"/threads/{thread.thread_id}/uploads/{session['upload_id']}/chunks/0", headers=upload_headers | {"X-Chunk-SHA256": hashlib.sha256(b"two").hexdigest()}, content=b"two").status_code == 409
    assert client.get(f"/threads/{other.thread_id}/uploads/{session['upload_id']}", headers=headers).status_code == 404


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_resumable_upload_rejects_a_gap_and_cancel_is_idempotent(tmp_path) -> None:
    app, _, thread = _app_thread(tmp_path)
    payload = b"x" * (8 * 1024 * 1024 + 1)
    digest = hashlib.sha256(payload).hexdigest()
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}
    session = client.post(
        f"/threads/{thread.thread_id}/uploads", headers=headers,
        json={"filename": "large.bin", "size_bytes": len(payload), "sha256": digest},
    ).json()
    second = payload[8 * 1024 * 1024 :]
    response = client.put(
        f"/threads/{thread.thread_id}/uploads/{session['upload_id']}/chunks/1",
        headers=headers | {
            "Upload-Offset": str(8 * 1024 * 1024),
            "Content-Length": "1",
            "X-Chunk-SHA256": hashlib.sha256(second).hexdigest(),
        },
        content=second,
    )
    assert response.status_code == 409
    cancelled = client.delete(f"/threads/{thread.thread_id}/uploads/{session['upload_id']}", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert client.delete(f"/threads/{thread.thread_id}/uploads/{session['upload_id']}", headers=headers).status_code == 200


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_resumable_upload_status_survives_storage_restart(tmp_path) -> None:
    app, storage, thread = _app_thread(tmp_path)
    payload = b"resume"
    digest = hashlib.sha256(payload).hexdigest()
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}
    session = client.post(
        f"/threads/{thread.thread_id}/uploads", headers=headers,
        json={"filename": "resume.txt", "size_bytes": len(payload), "sha256": digest},
    ).json()
    client.put(
        f"/threads/{thread.thread_id}/uploads/{session['upload_id']}/chunks/0",
        headers=headers | {"Upload-Offset": "0", "Content-Length": str(len(payload)), "X-Chunk-SHA256": digest},
        content=payload,
    ).raise_for_status()
    restarted = BridgeStorage(
        root_path=storage.root,
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=storage.workspace_root,
    )
    state = restarted.get_upload_session(thread_id=thread.thread_id, upload_id=session["upload_id"])
    assert state["received_indices"] == [0]


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_resumable_upload_rejects_a_manifest_missing_immutable_relative_path(tmp_path) -> None:
    app, storage, thread = _app_thread(tmp_path)
    payload = b"manifest"
    digest = hashlib.sha256(payload).hexdigest()
    session = storage.create_upload_session(
        thread_id=thread.thread_id,
        filename="manifest.txt",
        size_bytes=len(payload),
        sha256=digest,
    )
    locator = storage._upload_session_path(session["upload_id"])
    boundary = storage._home_assistant_uploads_boundary()
    with boundary.open_regular_file(locator) as stream:
        manifest = json.loads(stream.read())
    manifest.pop("relative_path")
    boundary.atomic_write_bytes(locator, json.dumps(manifest).encode())

    with pytest.raises(UploadNotFoundError):
        storage.get_upload_session(thread_id=thread.thread_id, upload_id=session["upload_id"])


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_resumable_upload_rejects_short_bad_hash_and_corrupt_retry(tmp_path) -> None:
    app, storage, thread = _app_thread(tmp_path)
    content = b"three"
    digest = hashlib.sha256(content).hexdigest()
    client = TestClient(app)
    auth = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}

    def create() -> dict[str, object]:
        return client.post(
            f"/threads/{thread.thread_id}/uploads", headers=auth,
            json={"filename": "three.txt", "size_bytes": len(content), "sha256": digest},
        ).json()

    short = create()
    assert client.put(
        f"/threads/{thread.thread_id}/uploads/{short['upload_id']}/chunks/0",
        headers=auth | {"Upload-Offset": "0", "Content-Length": str(len(content)), "X-Chunk-SHA256": digest},
        content=b"two",
    ).status_code == 400
    assert storage.get_upload_session(thread_id=thread.thread_id, upload_id=short["upload_id"])["received_indices"] == []

    bad_hash = create()
    assert client.put(
        f"/threads/{thread.thread_id}/uploads/{bad_hash['upload_id']}/chunks/0",
        headers=auth | {"Upload-Offset": "0", "Content-Length": str(len(content)), "X-Chunk-SHA256": "0" * 64},
        content=content,
    ).status_code == 400

    session = create()
    headers = auth | {"Upload-Offset": "0", "Content-Length": str(len(content)), "X-Chunk-SHA256": digest}
    assert client.put(f"/threads/{thread.thread_id}/uploads/{session['upload_id']}/chunks/0", headers=headers, content=content).status_code == 200
    boundary = storage._home_assistant_uploads_boundary()
    boundary.atomic_write_bytes(f".sessions/{session['upload_id']}/0.chunk", b"wrong")
    assert client.put(f"/threads/{thread.thread_id}/uploads/{session['upload_id']}/chunks/0", headers=headers, content=content).status_code == 409


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_cancel_retry_reconciles_payload_after_tombstone_write(tmp_path, monkeypatch) -> None:
    app, storage, thread = _app_thread(tmp_path)
    payload = b"cancel"
    digest = hashlib.sha256(payload).hexdigest()
    session = storage.create_upload_session(thread_id=thread.thread_id, filename="cancel.txt", size_bytes=len(payload), sha256=digest)
    writer = storage.begin_upload_chunk(thread_id=thread.thread_id, upload_id=session["upload_id"], index=0, offset=0, content_length=len(payload), sha256=digest)
    assert not isinstance(writer, dict)
    writer.write(payload)
    writer.finish()
    original = storage._clear_upload_payload_locked
    monkeypatch.setattr(storage, "_clear_upload_payload_locked", lambda *_args: (_ for _ in ()).throw(OSError("cleanup")))
    with pytest.raises(OSError, match="cleanup"):
        storage.cancel_upload_session(thread_id=thread.thread_id, upload_id=session["upload_id"])
    assert storage.get_upload_session(thread_id=thread.thread_id, upload_id=session["upload_id"])["status"] == "cancelled"
    monkeypatch.setattr(storage, "_clear_upload_payload_locked", original)
    assert storage.cancel_upload_session(thread_id=thread.thread_id, upload_id=session["upload_id"])["received_indices"] == []


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_resumable_upload_rejects_invalid_declared_zip_before_publication(tmp_path) -> None:
    app, storage, thread = _app_thread(tmp_path)
    payload = b"not a zip"
    digest = hashlib.sha256(payload).hexdigest()
    session = storage.create_upload_session(thread_id=thread.thread_id, filename="unsafe.zip", size_bytes=len(payload), sha256=digest)
    writer = storage.begin_upload_chunk(thread_id=thread.thread_id, upload_id=session["upload_id"], index=0, offset=0, content_length=len(payload), sha256=digest)
    assert not isinstance(writer, dict)
    writer.write(payload)
    writer.finish()

    with pytest.raises(Exception):
        storage.complete_upload_session(thread_id=thread.thread_id, upload_id=session["upload_id"])
    assert storage.load_thread(thread.thread_id).attachments == []


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_complete_recovers_a_published_file_when_chunks_are_gone_after_metadata_crash(tmp_path, monkeypatch) -> None:
    app, storage, thread = _app_thread(tmp_path)
    payload = b"recover published upload"
    digest = hashlib.sha256(payload).hexdigest()
    session = storage.create_upload_session(
        thread_id=thread.thread_id,
        filename="recover.txt",
        size_bytes=len(payload),
        sha256=digest,
    )
    writer = storage.begin_upload_chunk(
        thread_id=thread.thread_id,
        upload_id=session["upload_id"],
        index=0,
        offset=0,
        content_length=len(payload),
        sha256=digest,
    )
    assert not isinstance(writer, dict)
    writer.write(payload)
    writer.finish()

    original_save = storage._save_thread_with_events
    monkeypatch.setattr(
        storage,
        "_save_thread_with_events",
        lambda *_args: (_ for _ in ()).throw(OSError("crash after final publish")),
    )
    with pytest.raises(OSError, match="final publish"):
        storage.complete_upload_session(thread_id=thread.thread_id, upload_id=session["upload_id"])
    monkeypatch.setattr(storage, "_save_thread_with_events", original_save)

    boundary = storage._home_assistant_uploads_boundary()
    boundary.unlink_regular_file(
        f".sessions/{session['upload_id']}/0.chunk",
        missing_ok=True,
    )
    completed = storage.complete_upload_session(
        thread_id=thread.thread_id,
        upload_id=session["upload_id"],
    )

    assert completed.sha256 == digest
    assert storage.get_upload_session(
        thread_id=thread.thread_id, upload_id=session["upload_id"]
    )["status"] == "completed"


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_complete_retry_cleans_chunks_after_outbox_commit_failure(tmp_path, monkeypatch) -> None:
    app, storage, thread = _app_thread(tmp_path)
    payload = b"recover committed attachment"
    digest = hashlib.sha256(payload).hexdigest()
    session = storage.create_upload_session(
        thread_id=thread.thread_id,
        filename="cleanup.txt",
        size_bytes=len(payload),
        sha256=digest,
    )
    writer = storage.begin_upload_chunk(
        thread_id=thread.thread_id,
        upload_id=session["upload_id"],
        index=0,
        offset=0,
        content_length=len(payload),
        sha256=digest,
    )
    assert not isinstance(writer, dict)
    writer.write(payload)
    writer.finish()

    original_cleanup = storage._clear_upload_payload_locked
    monkeypatch.setattr(
        storage,
        "_clear_upload_payload_locked",
        lambda *_args: (_ for _ in ()).throw(OSError("cleanup interrupted")),
    )
    with pytest.raises(OSError, match="cleanup interrupted"):
        storage.complete_upload_session(thread_id=thread.thread_id, upload_id=session["upload_id"])
    monkeypatch.setattr(storage, "_clear_upload_payload_locked", original_cleanup)

    attachment = storage.complete_upload_session(
        thread_id=thread.thread_id,
        upload_id=session["upload_id"],
    )
    assert attachment.sha256 == digest
    assert storage._home_assistant_uploads_boundary().walk_regular_files(
        f".sessions/{session['upload_id']}"
    ) == ()


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_complete_rejects_an_existing_attachment_with_different_identity(tmp_path) -> None:
    app, storage, thread = _app_thread(tmp_path)
    payload = b"trusted"
    digest = hashlib.sha256(payload).hexdigest()
    session = storage.create_upload_session(
        thread_id=thread.thread_id,
        filename="trusted.txt",
        size_bytes=len(payload),
        sha256=digest,
    )
    writer = storage.begin_upload_chunk(
        thread_id=thread.thread_id,
        upload_id=session["upload_id"],
        index=0,
        offset=0,
        content_length=len(payload),
        sha256=digest,
    )
    assert not isinstance(writer, dict)
    writer.write(payload)
    writer.finish()

    unrelated = storage.attach_file(
        thread_id=thread.thread_id,
        filename="different.txt",
        mime_type="text/plain",
        content=b"different",
    )
    thread_payload = json.loads(
        storage._thread_path(thread.thread_id).read_text(encoding="utf-8")
    )
    thread_payload["attachments"] = [
        unrelated.model_copy(
            update={"attachment_id": f"att_{session['upload_id']}"}
        ).model_dump(mode="json")
    ]
    storage._thread_path(thread.thread_id).write_text(
        json.dumps(thread_payload), encoding="utf-8"
    )

    with pytest.raises(UploadConflictError):
        storage.complete_upload_session(
            thread_id=thread.thread_id,
            upload_id=session["upload_id"],
        )


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_upload_session_rejects_unknown_manifest_fields(tmp_path) -> None:
    app, storage, thread = _app_thread(tmp_path)
    payload = b"manifest"
    digest = hashlib.sha256(payload).hexdigest()
    session = storage.create_upload_session(
        thread_id=thread.thread_id,
        filename="manifest.txt",
        size_bytes=len(payload),
        sha256=digest,
    )
    boundary = storage._home_assistant_uploads_boundary()
    with boundary.open_regular_file(storage._upload_session_path(session["upload_id"])) as stream:
        manifest = json.loads(stream.read())
    manifest["unbounded_private_data"] = "must not be accepted"
    boundary.atomic_write_bytes(
        storage._upload_session_path(session["upload_id"]),
        json.dumps(manifest).encode(),
    )

    with pytest.raises(UploadNotFoundError):
        storage.get_upload_session(thread_id=thread.thread_id, upload_id=session["upload_id"])


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_upload_session_rejects_unknown_nested_attachment_fields(tmp_path) -> None:
    _app, storage, thread = _app_thread(tmp_path)
    payload = b"nested manifest"
    session = storage.create_upload_session(
        thread_id=thread.thread_id,
        filename="nested.txt",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    boundary = storage._home_assistant_uploads_boundary()
    manifest_path = storage._upload_session_path(session["upload_id"])
    with boundary.open_regular_file(manifest_path) as stream:
        manifest = json.loads(stream.read())
    manifest["status"] = "publishing"
    attachment = storage._upload_attachment_from_payload(manifest).model_dump(
        mode="json"
    )
    attachment["unbounded_private_data"] = "must not be accepted"
    manifest["attachment"] = attachment
    boundary.atomic_write_bytes(manifest_path, json.dumps(manifest).encode())

    with pytest.raises(UploadNotFoundError):
        storage.get_upload_session(
            thread_id=thread.thread_id,
            upload_id=session["upload_id"],
        )


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_upload_create_rejects_mismatched_relative_basename(tmp_path) -> None:
    app, _, thread = _app_thread(tmp_path)
    response = TestClient(app).post(
        f"/threads/{thread.thread_id}/uploads",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"},
        json={
            "filename": "notes.txt",
            "relative_path": "docs/other.txt",
            "size_bytes": 1,
            "sha256": hashlib.sha256(b"x").hexdigest(),
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "invalid upload"}


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_upload_create_maps_the_file_limit_to_typed_413(tmp_path) -> None:
    app = create_app(
        root_path=tmp_path / "data" / "bridge",
        auth_token="secret",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=tmp_path / "config" / "workspaces",
        resource_limits=ResourceLimits(max_upload_file_bytes=4),
        runner_factory=lambda _storage: object(),
    )
    storage = app.state.storage
    project = storage.create_project(name="Limit", root_path="projects/limit")
    thread = storage.create_thread(
        title="Limit", project_id=project.project_id, mode=RunMode.EDIT
    )
    response = TestClient(app).post(
        f"/threads/{thread.thread_id}/uploads",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"},
        json={
            "filename": "large.bin",
            "size_bytes": 5,
            "sha256": hashlib.sha256(b"12345").hexdigest(),
        },
    )

    assert response.status_code == 413
    assert response.json()["detail"] == {
        "code": "quota_exceeded",
        "resource": "upload_file",
        "retryable": False,
    }


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_duplicate_chunk_retry_must_still_match_the_declared_digest(tmp_path) -> None:
    app, _, thread = _app_thread(tmp_path)
    payload = b"trusted"
    digest = hashlib.sha256(payload).hexdigest()
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"}
    session = client.post(
        f"/threads/{thread.thread_id}/uploads",
        headers=headers,
        json={"filename": "retry.txt", "size_bytes": len(payload), "sha256": digest},
    ).json()
    chunk_headers = headers | {
        "Upload-Offset": "0",
        "Content-Length": str(len(payload)),
        "X-Chunk-SHA256": digest,
    }
    assert client.put(
        f"/threads/{thread.thread_id}/uploads/{session['upload_id']}/chunks/0",
        headers=chunk_headers,
        content=payload,
    ).status_code == 200
    retry = client.put(
        f"/threads/{thread.thread_id}/uploads/{session['upload_id']}/chunks/0",
        headers=chunk_headers,
        content=b"hostile",
    )

    assert retry.status_code == 400


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_complete_rolls_back_the_exact_published_file_when_quota_commit_fails(
    tmp_path, monkeypatch
) -> None:
    app, storage, thread = _app_thread(tmp_path)
    payload = b"rollback"
    digest = hashlib.sha256(payload).hexdigest()
    session = storage.create_upload_session(
        thread_id=thread.thread_id,
        filename="rollback.txt",
        size_bytes=len(payload),
        sha256=digest,
    )
    writer = storage.begin_upload_chunk(
        thread_id=thread.thread_id,
        upload_id=session["upload_id"],
        index=0,
        offset=0,
        content_length=len(payload),
        sha256=digest,
    )
    assert not isinstance(writer, dict)
    writer.write(payload)
    writer.finish()

    def fail_commit(self, *, persisted_bytes=None):
        raise QuotaExceededError("private")

    monkeypatch.setattr(
        "codex_bridge_service.storage.QuotaReservation.commit", fail_commit
    )
    with pytest.raises(QuotaExceededError):
        storage.complete_upload_session(
            thread_id=thread.thread_id,
            upload_id=session["upload_id"],
        )

    recovered = storage.get_upload_session(
        thread_id=thread.thread_id, upload_id=session["upload_id"]
    )
    assert recovered["status"] == "active"
    attachment = storage._upload_attachment_from_payload(recovered)
    with pytest.raises(WorkspaceNotFoundError):
        storage._home_assistant_uploads_boundary().open_regular_file(
            attachment.stored_path
        )


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_cancel_reconciles_a_durably_committed_attachment_after_final_manifest_crash(
    tmp_path, monkeypatch
) -> None:
    _app, storage, thread = _app_thread(tmp_path)
    payload = b"committed before manifest completion"
    digest = hashlib.sha256(payload).hexdigest()
    session = storage.create_upload_session(
        thread_id=thread.thread_id,
        filename="committed.txt",
        size_bytes=len(payload),
        sha256=digest,
    )
    writer = storage.begin_upload_chunk(
        thread_id=thread.thread_id,
        upload_id=session["upload_id"],
        index=0,
        offset=0,
        content_length=len(payload),
        sha256=digest,
    )
    assert not isinstance(writer, dict)
    writer.write(payload)
    writer.finish()

    original_write = storage._write_upload_session_locked

    def crash_before_completed_tombstone(manifest):
        if manifest.get("status") == "completed":
            raise OSError("process died before completed manifest")
        return original_write(manifest)

    monkeypatch.setattr(storage, "_write_upload_session_locked", crash_before_completed_tombstone)
    with pytest.raises(OSError, match="completed manifest"):
        storage.complete_upload_session(
            thread_id=thread.thread_id, upload_id=session["upload_id"]
        )
    monkeypatch.setattr(storage, "_write_upload_session_locked", original_write)

    cancelled = storage.cancel_upload_session(
        thread_id=thread.thread_id, upload_id=session["upload_id"]
    )

    assert cancelled["status"] == "completed"
    attachment = storage.load_thread(thread.thread_id).attachments[0]
    assert attachment.sha256 == digest
    with storage._home_assistant_uploads_boundary().open_regular_file(
        attachment.stored_path
    ) as stream:
        assert stream.read() == payload


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_upload_creation_cannot_leave_a_session_after_thread_deletion_race(
    tmp_path, monkeypatch
) -> None:
    _app, storage, thread = _app_thread(tmp_path)
    entered_load = threading.Event()
    allow_load = threading.Event()
    deletion_done = threading.Event()
    original_load = storage.load_thread
    creation_errors: list[BaseException] = []

    def delayed_load(thread_id):
        record = original_load(thread_id)
        if thread_id == thread.thread_id and not entered_load.is_set():
            entered_load.set()
            assert allow_load.wait(timeout=3)
        return record

    monkeypatch.setattr(storage, "load_thread", delayed_load)

    def create() -> None:
        try:
            storage.create_upload_session(
                thread_id=thread.thread_id,
                filename="race.txt",
                size_bytes=1,
                sha256=hashlib.sha256(b"x").hexdigest(),
            )
        except BaseException as exc:
            creation_errors.append(exc)

    creator = threading.Thread(target=create)
    creator.start()
    assert entered_load.wait(timeout=3)

    def delete() -> None:
        storage.delete_thread(thread.thread_id)
        deletion_done.set()

    deleter = threading.Thread(target=delete)
    deleter.start()
    # With the upload lock held around validation, deletion remains queued;
    # the previous implementation completed here and stranded a manifest.
    assert not deletion_done.wait(timeout=0.1)
    allow_load.set()
    creator.join(timeout=3)
    deleter.join(timeout=3)
    assert not creator.is_alive()
    assert deletion_done.is_set()
    assert not creation_errors
    assert storage._home_assistant_uploads_boundary().walk_regular_files(".sessions") == ()


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_upload_session_metadata_body_is_rejected_before_json_parsing(tmp_path) -> None:
    app, _storage, thread = _app_thread(tmp_path)
    response = TestClient(app).post(
        f"/threads/{thread.thread_id}/uploads",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"},
        content=(b'{"filename":"' + b"x" * (64 * 1024) + b'"}'),
    )

    assert response.status_code == 413
    assert response.json()["detail"]["resource"] == "upload_request"


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_upload_session_rejects_unbounded_relative_path_depth(tmp_path) -> None:
    app, _storage, thread = _app_thread(tmp_path)
    response = TestClient(app).post(
        f"/threads/{thread.thread_id}/uploads",
        headers={"Authorization": "Bearer secret", "X-Codex-Bridge-Api": "1"},
        json={
            "filename": "notes.txt",
            "relative_path": "/".join(["nested"] * 16 + ["notes.txt"]),
            "size_bytes": 1,
            "sha256": hashlib.sha256(b"x").hexdigest(),
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "invalid upload"}


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_cancel_cleans_a_recoverable_session_owned_final_assembly(tmp_path, monkeypatch) -> None:
    _app, storage, thread = _app_thread(tmp_path)
    payload = b"recoverable final assembly"
    digest = hashlib.sha256(payload).hexdigest()
    session = storage.create_upload_session(
        thread_id=thread.thread_id,
        filename="assembly.txt",
        size_bytes=len(payload),
        sha256=digest,
    )
    writer = storage.begin_upload_chunk(
        thread_id=thread.thread_id,
        upload_id=session["upload_id"],
        index=0,
        offset=0,
        content_length=len(payload),
        sha256=digest,
    )
    assert not isinstance(writer, dict)
    writer.write(payload)
    writer.finish()

    boundary = storage._home_assistant_uploads_boundary()
    recorded: list[str] = []
    original_create = boundary.create_file_exclusive

    def record_stage(relative):
        recorded.append(str(relative))
        return original_create(relative)

    monkeypatch.setattr(boundary, "create_file_exclusive", record_stage)
    monkeypatch.setattr(
        storage,
        "_validate_uploaded_archive_if_present",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        storage.complete_upload_session(
            thread_id=thread.thread_id, upload_id=session["upload_id"]
        )

    stage = f".sessions/{session['upload_id']}/assembly.part"
    assert stage in recorded
    # Model a process death after the part has been written but before its
    # normal exception cleanup: cancellation can locate it by session alone.
    boundary.atomic_write_bytes(stage, payload)
    storage.cancel_upload_session(thread_id=thread.thread_id, upload_id=session["upload_id"])
    assert boundary.walk_regular_files(f".sessions/{session['upload_id']}") == ()


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_chunk_manifest_write_failure_rolls_back_exact_published_chunk_and_reservation(
    tmp_path, monkeypatch
) -> None:
    _app, storage, thread = _app_thread(tmp_path)
    payload = b"chunk manifest failure"
    digest = hashlib.sha256(payload).hexdigest()
    session = storage.create_upload_session(
        thread_id=thread.thread_id,
        filename="manifest-failure.txt",
        size_bytes=len(payload),
        sha256=digest,
    )
    writer = storage.begin_upload_chunk(
        thread_id=thread.thread_id,
        upload_id=session["upload_id"],
        index=0,
        offset=0,
        content_length=len(payload),
        sha256=digest,
    )
    assert not isinstance(writer, dict)
    writer.write(payload)

    original_write = storage._write_upload_session_locked
    monkeypatch.setattr(
        storage,
        "_write_upload_session_locked",
        lambda manifest: (
            (_ for _ in ()).throw(OSError("manifest write failed"))
            if manifest.get("received")
            else original_write(manifest)
        ),
    )
    with pytest.raises(OSError, match="manifest write failed"):
        writer.finish()

    boundary = storage._home_assistant_uploads_boundary()
    assert boundary.walk_regular_files(f".sessions/{session['upload_id']}") == ()
    assert storage.get_upload_session(
        thread_id=thread.thread_id, upload_id=session["upload_id"]
    )["received_indices"] == []
    assert storage._disk_quota().active_reservations == 0


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_failed_session_creation_and_thread_deletion_leave_no_orphan_session_directories(
    tmp_path, monkeypatch
) -> None:
    _app, storage, thread = _app_thread(tmp_path)
    boundary = storage._home_assistant_uploads_boundary()
    original_write = storage._write_upload_session_locked
    monkeypatch.setattr(
        storage,
        "_write_upload_session_locked",
        lambda _manifest: (_ for _ in ()).throw(OSError("session write failed")),
    )
    for _ in range(3):
        with pytest.raises(OSError, match="session write failed"):
            storage.create_upload_session(
                thread_id=thread.thread_id,
                filename="failed.txt",
                size_bytes=1,
                sha256=hashlib.sha256(b"x").hexdigest(),
            )
    assert boundary.list_directories(".sessions") == ()

    monkeypatch.setattr(storage, "_write_upload_session_locked", original_write)
    session = storage.create_upload_session(
        thread_id=thread.thread_id,
        filename="delete.txt",
        size_bytes=1,
        sha256=hashlib.sha256(b"x").hexdigest(),
    )
    assert boundary.list_directories(".sessions") == (
        f".sessions/{session['upload_id']}",
    )
    storage.delete_thread(thread.thread_id)
    assert boundary.list_directories(".sessions") == ()


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_cancel_cleans_completed_tombstone_payload_after_cleanup_crash(
    tmp_path, monkeypatch
) -> None:
    _app, storage, thread = _app_thread(tmp_path)
    payload = b"completed tombstone cleanup"
    digest = hashlib.sha256(payload).hexdigest()
    session = storage.create_upload_session(
        thread_id=thread.thread_id,
        filename="completed-cleanup.txt",
        size_bytes=len(payload),
        sha256=digest,
    )
    writer = storage.begin_upload_chunk(
        thread_id=thread.thread_id,
        upload_id=session["upload_id"],
        index=0,
        offset=0,
        content_length=len(payload),
        sha256=digest,
    )
    assert not isinstance(writer, dict)
    writer.write(payload)
    writer.finish()

    original_cleanup = storage._clear_upload_payload_locked
    monkeypatch.setattr(
        storage,
        "_clear_upload_payload_locked",
        lambda *_args: (_ for _ in ()).throw(OSError("cleanup crashed")),
    )
    with pytest.raises(OSError, match="cleanup crashed"):
        storage.complete_upload_session(
            thread_id=thread.thread_id, upload_id=session["upload_id"]
        )
    monkeypatch.setattr(storage, "_clear_upload_payload_locked", original_cleanup)

    recovered = storage.cancel_upload_session(
        thread_id=thread.thread_id, upload_id=session["upload_id"]
    )
    assert recovered["status"] == "completed"
    assert recovered["received_indices"] == []
    assert len(storage.load_thread(thread.thread_id).attachments) == 1
    assert storage._home_assistant_uploads_boundary().walk_regular_files(
        f".sessions/{session['upload_id']}"
    ) == ()


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_session_reaper_preserves_an_in_flight_chunk_part(tmp_path) -> None:
    _app, storage, thread = _app_thread(tmp_path)
    payload = b"still streaming while another session is created"
    digest = hashlib.sha256(payload).hexdigest()
    session = storage.create_upload_session(
        thread_id=thread.thread_id,
        filename="streaming.txt",
        size_bytes=len(payload),
        sha256=digest,
    )
    writer = storage.begin_upload_chunk(
        thread_id=thread.thread_id,
        upload_id=session["upload_id"],
        index=0,
        offset=0,
        content_length=len(payload),
        sha256=digest,
    )
    assert not isinstance(writer, dict)
    writer.write(payload)

    storage.create_upload_session(
        thread_id=thread.thread_id,
        filename="parallel.txt",
        size_bytes=1,
        sha256=hashlib.sha256(b"x").hexdigest(),
    )

    completed = writer.finish()
    assert completed["received_indices"] == [0]


@pytest.mark.skipif(os.name == "nt", reason="secure Home Assistant upload operations require POSIX dir_fd support")
def test_session_reaper_recounts_manifests_after_pruning(tmp_path, monkeypatch) -> None:
    _app, storage, thread = _app_thread(tmp_path)
    monkeypatch.setattr("codex_bridge_service.storage._UPLOAD_SESSION_LIMIT", 3)
    monkeypatch.setattr(
        "codex_bridge_service.storage._UPLOAD_TERMINAL_SESSION_LIMIT", 3
    )
    digest = hashlib.sha256(b"x").hexdigest()
    for index in range(3):
        session = storage.create_upload_session(
            thread_id=thread.thread_id,
            filename=f"terminal-{index}.txt",
            size_bytes=1,
            sha256=digest,
        )
        storage.cancel_upload_session(
            thread_id=thread.thread_id,
            upload_id=session["upload_id"],
        )

    monkeypatch.setattr(
        "codex_bridge_service.storage._UPLOAD_TERMINAL_SESSION_LIMIT", 1
    )
    created = storage.create_upload_session(
        thread_id=thread.thread_id,
        filename="after-prune.txt",
        size_bytes=1,
        sha256=digest,
    )

    assert created["status"] == "active"
    manifests = [
        locator
        for locator in storage._home_assistant_uploads_boundary().walk_regular_files(
            ".sessions"
        )
        if locator.endswith(".json")
    ]
    assert len(manifests) == 2
