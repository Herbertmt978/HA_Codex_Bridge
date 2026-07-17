from __future__ import annotations

import base64
import os
import struct
import zlib

import pytest

from codex_bridge_service.models import ArtifactSource, RunMode, RuntimeProfile
from codex_bridge_service.storage import BridgeStorage
from codex_bridge_service.workspace import WorkspaceInputError

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="secure Home Assistant artifact operations require POSIX dir_fd support",
)


def _png() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", checksum)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + b"".join(
        (
            chunk(b"IHDR", ihdr),
            chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00")),
            chunk(b"IEND", b""),
        )
    )


def _storage_and_thread(tmp_path):
    storage = BridgeStorage(
        tmp_path / "state",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=tmp_path / "workspaces",
    )
    project = storage.create_project(
        name="Browser",
        root_path="projects/browser",
    )
    thread = storage.create_thread(
        title="Browser",
        project_id=project.project_id,
        mode=RunMode.EDIT,
    )
    return storage, thread


@pytest.mark.parametrize(
    ("kind", "mime_type", "content", "extension"),
    [
        ("screenshot", "image/png", _png(), ".png"),
        (
            "pdf",
            "application/pdf",
            b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n",
            ".pdf",
        ),
    ],
)
def test_browser_artifact_round_trip_is_private_typed_and_idempotent(
    tmp_path,
    kind: str,
    mime_type: str,
    content: bytes,
    extension: str,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)

    first = storage.save_browser_artifact(
        thread_id=thread.thread_id,
        kind=kind,
        mime_type=mime_type,
        content=content,
    )
    replay = storage.save_browser_artifact(
        thread_id=thread.thread_id,
        kind=kind,
        mime_type=mime_type,
        content=content,
    )

    assert replay == first
    assert first.source is ArtifactSource.BROWSER_CAPTURE
    assert first.mime_type == mime_type
    assert first.filename.endswith(extension)
    assert first.stored_path.startswith(f"{thread.thread_id}/browser/")
    artifact, stream, size = storage.open_artifact(
        thread.thread_id,
        first.artifact_id,
    )
    with stream:
        assert stream.read() == content
    assert artifact == first
    assert size == len(content)

    storage.delete_thread(thread.thread_id)
    assert not (tmp_path / "state" / "artifacts" / thread.thread_id).exists()


@pytest.mark.parametrize(
    ("kind", "mime_type", "content"),
    [
        ("screenshot", "image/png", b"not-an-image"),
        ("screenshot", "application/pdf", b"%PDF-1.7\n%%EOF\n"),
        ("pdf", "application/pdf", b"%PDF-1.7\nmissing-eof"),
        (
            "pdf",
            "application/pdf",
            b"%PDF-1.7\n<< /JavaScript 2 0 R >>\n%%EOF\n",
        ),
        ("pdf", "image/png", _png()),
    ],
)
def test_browser_artifact_rejects_mismatched_or_unsafe_content_before_write(
    tmp_path,
    kind: str,
    mime_type: str,
    content: bytes,
) -> None:
    storage, thread = _storage_and_thread(tmp_path)

    with pytest.raises(WorkspaceInputError):
        storage.save_browser_artifact(
            thread_id=thread.thread_id,
            kind=kind,
            mime_type=mime_type,
            content=content,
        )

    assert storage.load_thread(thread.thread_id).artifacts == []


def test_browser_artifact_does_not_accept_base64_or_mutable_buffers(tmp_path) -> None:
    storage, thread = _storage_and_thread(tmp_path)

    for content in (base64.b64encode(_png()).decode("ascii"), bytearray(_png())):
        with pytest.raises(WorkspaceInputError):
            storage.save_browser_artifact(
                thread_id=thread.thread_id,
                kind="screenshot",
                mime_type="image/png",
                content=content,
            )
