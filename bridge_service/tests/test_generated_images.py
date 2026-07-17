import base64
import importlib.util
import os
import struct
import zlib

import pytest

from codex_bridge_service.storage import _decode_generated_image
from codex_bridge_service.workspace import WorkspaceInputError


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def test_generated_image_raster_validator_is_an_independent_boundary() -> None:
    assert importlib.util.find_spec(
        "codex_bridge_service.generated_images"
    ) is not None


def _png(
    width: int,
    height: int,
    *,
    animated: bool = False,
    decoded_scanlines: bytes = b"\x00\x00\x00\x00\x00",
) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    chunks = [chunk(b"IHDR", ihdr)]
    if animated:
        chunks.append(chunk(b"acTL", struct.pack(">II", 1, 0)))
    chunks.extend(
        [
            chunk(b"IDAT", zlib.compress(decoded_scanlines)),
            chunk(b"IEND", b""),
        ]
    )
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)


def _jpeg(width: int, height: int, *, mpf: bool = False) -> bytes:
    def segment(marker: int, payload: bytes) -> bytes:
        return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload

    content = bytearray(
        base64.b64decode(
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsL"
            "DBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/"
            "wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAAB//EABQQAQAAAAAAAA"
            "AAAAAAAAAAAAD/2gAIAQEAAD8AAH//2Q=="
        )
    )
    frame = content.index(b"\xff\xc0")
    content[frame + 5 : frame + 7] = height.to_bytes(2, "big")
    content[frame + 7 : frame + 9] = width.to_bytes(2, "big")
    if mpf:
        content[2:2] = segment(0xE2, b"MPF\x00private-multi-picture")
    return bytes(content)


def _webp_lossless(width: int, height: int) -> bytes:
    packed = (width - 1) | ((height - 1) << 14)
    content = bytearray(
        base64.b64decode("UklGRiAAAABXRUJQVlA4TBQAAAAvAAAAAAdQgVQIIAAKmv7HiIj+Bw==")
    )
    payload = content.index(b"VP8L") + 8
    content[payload + 1 : payload + 5] = packed.to_bytes(4, "little")
    return bytes(content)


def _animated_webp(width: int = 1, height: int = 1) -> bytes:
    dimensions = (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(
        3, "little"
    )
    payload = b"\x02\x00\x00\x00" + dimensions
    chunk = b"VP8X" + struct.pack("<I", len(payload)) + payload
    body = b"WEBP" + chunk
    return b"RIFF" + struct.pack("<I", len(body)) + body


@pytest.mark.parametrize(
    ("mime_type", "content"),
    [
        ("image/png", _png(1, 1)),
        ("image/jpeg", _jpeg(1, 1)),
        ("image/webp", _webp_lossless(1, 1)),
    ],
)
def test_generated_raster_validation_accepts_bounded_single_frame_images(
    mime_type: str,
    content: bytes,
) -> None:
    from codex_bridge_service.generated_images import validate_generated_image_result

    assert validate_generated_image_result(_b64(content), mime_type) == (
        mime_type,
        content,
    )
    assert validate_generated_image_result(
        f"data:{mime_type};base64,{_b64(content)}"
    ) == (mime_type, content)


@pytest.mark.parametrize(
    ("mime_type", "content"),
    [
        ("image/png", _png(20_000, 1)),
        ("image/jpeg", _jpeg(20_000, 1)),
        ("image/webp", _webp_lossless(16_384, 16_384)),
    ],
)
def test_generated_raster_validation_rejects_excess_dimensions_or_pixels(
    mime_type: str,
    content: bytes,
) -> None:
    from codex_bridge_service.generated_images import validate_generated_image_result

    with pytest.raises(WorkspaceInputError):
        validate_generated_image_result(_b64(content), mime_type)


@pytest.mark.parametrize(
    ("mime_type", "content"),
    [
        ("image/png", _png(1, 1, animated=True)),
        ("image/jpeg", _jpeg(1, 1, mpf=True)),
        ("image/webp", _animated_webp()),
    ],
)
def test_generated_raster_validation_rejects_animated_or_multiframe_images(
    mime_type: str,
    content: bytes,
) -> None:
    from codex_bridge_service.generated_images import validate_generated_image_result

    with pytest.raises(WorkspaceInputError):
        validate_generated_image_result(_b64(content), mime_type)


@pytest.mark.parametrize(
    ("mime_type", "content"),
    [
        ("image/png", _png(1, 1)[:-12]),
        ("image/jpeg", _jpeg(1, 1)[:-2]),
        ("image/webp", _webp_lossless(1, 1)[:-1]),
    ],
)
def test_generated_raster_validation_rejects_truncated_containers(
    mime_type: str,
    content: bytes,
) -> None:
    from codex_bridge_service.generated_images import validate_generated_image_result

    with pytest.raises(WorkspaceInputError):
        validate_generated_image_result(_b64(content), mime_type)


def test_generated_raster_validation_rejects_png_decompression_bomb() -> None:
    from codex_bridge_service.generated_images import validate_generated_image_result

    content = _png(1, 1, decoded_scanlines=b"\x00" * (1024 * 1024))
    with pytest.raises(WorkspaceInputError):
        validate_generated_image_result(_b64(content), "image/png")


@pytest.mark.parametrize(
    ("result", "declared_mime_type"),
    [
        ("https://provider.example/private-image.png", "image/png"),
        ("data:image/svg+xml;base64,PHN2Zy8+", None),
        ("data:text/html;base64,PGh0bWw+PC9odG1sPg==", None),
        (f"data:image/png;base64,{_b64(_png(1, 1))}", "image/jpeg"),
    ],
)
def test_generated_raster_validation_rejects_external_or_active_content(
    result: str,
    declared_mime_type: str | None,
) -> None:
    from codex_bridge_service.generated_images import validate_generated_image_result

    with pytest.raises(WorkspaceInputError):
        validate_generated_image_result(result, declared_mime_type)


def test_generated_raster_validation_rejects_reserved_webp_version_bits() -> None:
    from codex_bridge_service.generated_images import validate_generated_image_result

    content = bytearray(_webp_lossless(1, 1))
    payload = content.index(b"VP8L") + 8
    packed = int.from_bytes(content[payload + 1 : payload + 5], "little")
    content[payload + 1 : payload + 5] = (packed | (1 << 29)).to_bytes(4, "little")
    with pytest.raises(WorkspaceInputError):
        validate_generated_image_result(_b64(bytes(content)), "image/webp")


def test_generated_raster_validation_rejects_invalid_jpeg_scan_components() -> None:
    from codex_bridge_service.generated_images import validate_generated_image_result

    content = bytearray(_jpeg(1, 1))
    scan = content.index(b"\xff\xda")
    content[scan + 4] = 5
    with pytest.raises(WorkspaceInputError):
        validate_generated_image_result(_b64(bytes(content)), "image/jpeg")


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
    content = _png(1, 1)
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
def test_generated_image_storage_revalidates_direct_provider_input(tmp_path) -> None:
    """Storage remains a strict boundary when callers bypass the broker."""

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

    with pytest.raises(WorkspaceInputError):
        storage.save_generated_image(
            thread_id=thread.thread_id,
            item_id="invalid-direct-image",
            result=_b64(b"\x89PNG\r\n\x1a\nnot-a-png"),
            mime_type="image/png",
        )
    assert storage.load_thread(thread.thread_id).artifacts == []


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
    content = _png(1, 1)
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
