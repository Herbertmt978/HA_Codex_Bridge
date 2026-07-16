import base64
import os

import pytest

from codex_bridge_service.storage import _decode_generated_image
from codex_bridge_service.workspace import WorkspaceInputError


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


@pytest.mark.parametrize(
    ("mime", "content"),
    [
        ("image/png", b"\x89PNG\r\n\x1a\nimage"),
        ("image/jpeg", b"\xff\xd8\xffimage"),
        ("image/webp", b"RIFF\x04\x00\x00\x00WEBPimage"),
    ],
)
def test_generated_image_decode_types_and_data_url(mime: str, content: bytes) -> None:
    assert _decode_generated_image(_b64(content), mime) == (mime, content)
    assert _decode_generated_image(_b64(content)) == (mime, content)
    assert _decode_generated_image(f"data:{mime};base64,{_b64(content)}") == (
        mime,
        content,
    )


@pytest.mark.parametrize(
    "result",
    [
        "not-base64",
        "data:image/svg+xml;base64,PHN2Zy8+",
    ],
)
def test_generated_image_rejects_invalid_encoding_and_svg(result: str) -> None:
    with pytest.raises(WorkspaceInputError):
        _decode_generated_image(result, "image/png")


def test_generated_image_rejects_mismatched_magic() -> None:
    with pytest.raises(WorkspaceInputError):
        _decode_generated_image(_b64(b"<svg></svg>"), "image/png")


def test_generated_image_rejects_oversized(monkeypatch: pytest.MonkeyPatch) -> None:
    import codex_bridge_service.storage as storage

    monkeypatch.setattr(storage, "_GENERATED_IMAGE_MAX_BYTES", 8)
    with pytest.raises(WorkspaceInputError):
        _decode_generated_image(_b64(b"\x89PNG\r\n\x1a\n123456789"), "image/png")


@pytest.mark.skipif(
    os.name == "nt",
    reason="secure Home Assistant artifact operations require POSIX dir_fd support",
)
def test_generated_image_storage_round_trip_and_idempotency(tmp_path) -> None:
    from codex_bridge_service.models import RunMode, RuntimeProfile
    from codex_bridge_service.storage import BridgeStorage

    storage = BridgeStorage(
        tmp_path / "state",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=tmp_path / "workspaces",
    )
    project = storage.create_project(name="Images", root_path="projects/images")
    thread = storage.create_thread(
        title="Images", project_id=project.project_id, mode=RunMode.EDIT
    )
    content = b"\x89PNG\r\n\x1a\nimage"
    first = storage.save_generated_image(
        thread_id=thread.thread_id,
        item_id="item-1",
        result=_b64(content),
        mime_type="image/png",
    )
    second = storage.save_generated_image(
        thread_id=thread.thread_id,
        item_id="item-1",
        result=_b64(content),
        mime_type="image/png",
    )
    assert second.artifact_id == first.artifact_id
    _artifact, stream, size = storage.open_artifact(thread.thread_id, first.artifact_id)
    with stream:
        assert stream.read() == content
    assert size == len(content)
    storage.delete_thread(thread.thread_id)
    assert not (tmp_path / "state" / "artifacts" / thread.thread_id).exists()


@pytest.mark.skipif(
    os.name == "nt",
    reason="secure Home Assistant artifact operations require POSIX dir_fd support",
)
def test_generated_image_replays_crash_orphan_and_replaces_untrusted_file(
    tmp_path,
) -> None:
    from codex_bridge_service.models import RunMode, RuntimeProfile
    from codex_bridge_service.storage import BridgeStorage

    storage = BridgeStorage(
        tmp_path / "state",
        runtime_profile=RuntimeProfile.HOME_ASSISTANT,
        workspace_root=tmp_path / "workspaces",
    )
    project = storage.create_project(name="Images", root_path="projects/images")
    thread = storage.create_thread(
        title="Images", project_id=project.project_id, mode=RunMode.EDIT
    )
    content = b"\x89PNG\r\n\x1a\nimage"
    import hashlib

    digest = hashlib.sha256(b"item-crash").hexdigest()[:24]
    locator = f"{thread.thread_id}/generated/codex-image-{digest}.png"
    boundary = storage._home_assistant_artifacts_boundary()
    boundary.create_directory(f"{thread.thread_id}/generated")
    with boundary.create_file_exclusive(locator) as orphan:
        orphan.write(content)
        orphan.flush()
        os.fsync(orphan.fileno())

    reconciled = storage.save_generated_image(
        thread_id=thread.thread_id,
        item_id="item-crash",
        result=_b64(content),
        mime_type="image/png",
    )
    assert reconciled.size_bytes == len(content)
    assert storage.save_generated_image(
        thread_id=thread.thread_id,
        item_id="item-crash",
        result=_b64(content),
        mime_type="image/png",
    ).artifact_id == reconciled.artifact_id

    replacement_item = "item-replace"
    replacement_digest = hashlib.sha256(replacement_item.encode()).hexdigest()[:24]
    replacement_locator = (
        f"{thread.thread_id}/generated/codex-image-{replacement_digest}.png"
    )
    with boundary.create_file_exclusive(replacement_locator) as arbitrary:
        arbitrary.write(b"private stale bytes")
        arbitrary.flush()
        os.fsync(arbitrary.fileno())
    replaced = storage.save_generated_image(
        thread_id=thread.thread_id,
        item_id=replacement_item,
        result=_b64(content),
        mime_type="image/png",
    )
    _artifact, stream, _size = storage.open_artifact(
        thread.thread_id, replaced.artifact_id
    )
    with stream:
        assert stream.read() == content

    storage.delete_thread(thread.thread_id)
    assert not (tmp_path / "state" / "artifacts" / thread.thread_id).exists()
