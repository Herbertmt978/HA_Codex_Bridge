"""Strict validation for provider-generated raster image results."""

from __future__ import annotations

import base64
import struct
import zlib

from .workspace import WorkspaceInputError

_GENERATED_IMAGE_MAX_BYTES = 25 * 1024 * 1024
_GENERATED_IMAGE_MAX_DIMENSION = 8192
_GENERATED_IMAGE_MAX_PIXELS = 16 * 1024 * 1024
_GENERATED_IMAGE_MAX_CHUNKS = 4096
_GENERATED_IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)
_JPEG_SUPPORTED_SOF_MARKERS = frozenset({0xC0, 0xC1, 0xC2})


def validate_generated_image_result(
    result: object,
    declared_mime_type: object = None,
) -> tuple[str, bytes]:
    """Decode and validate one bounded PNG, JPEG, or WebP result."""

    encoded, mime_type = _encoded_result(result, declared_mime_type)
    try:
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError, base64.binascii.Error):
        raise WorkspaceInputError() from None
    if not raw or len(raw) > _GENERATED_IMAGE_MAX_BYTES:
        raise WorkspaceInputError()

    if raw.startswith(_PNG_SIGNATURE):
        detected_mime = "image/png"
        width, height = _png_dimensions(raw)
    elif raw.startswith(b"\xff\xd8"):
        detected_mime = "image/jpeg"
        width, height = _jpeg_dimensions(raw)
    elif len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        detected_mime = "image/webp"
        width, height = _webp_dimensions(raw)
    else:
        raise WorkspaceInputError()
    if mime_type is not None and mime_type != detected_mime:
        raise WorkspaceInputError()
    _validate_dimensions(width, height)
    return detected_mime, raw


def _encoded_result(
    result: object,
    declared_mime_type: object,
) -> tuple[str, str | None]:
    if not isinstance(result, str) or not result:
        raise WorkspaceInputError()
    encoded = result
    mime_type: str | None = None
    if result.startswith("data:"):
        header, separator, encoded = result.partition(",")
        prefix = header[5:]
        if separator != "," or not prefix.endswith(";base64"):
            raise WorkspaceInputError()
        mime_type = prefix[:-7].lower()
    declared: str | None = None
    if isinstance(declared_mime_type, str):
        declared = declared_mime_type.split(";", 1)[0].strip().lower()
        if declared not in _GENERATED_IMAGE_MIME_TYPES:
            raise WorkspaceInputError()
    if mime_type is not None:
        if mime_type not in _GENERATED_IMAGE_MIME_TYPES:
            raise WorkspaceInputError()
        if declared is not None and declared != mime_type:
            raise WorkspaceInputError()
    else:
        mime_type = declared
    maximum_encoded = ((_GENERATED_IMAGE_MAX_BYTES + 2) * 4 // 3) + 8
    if not encoded or len(encoded) > maximum_encoded:
        raise WorkspaceInputError()
    return encoded, mime_type


def _validate_dimensions(width: int, height: int) -> None:
    if (
        width < 1
        or height < 1
        or width > _GENERATED_IMAGE_MAX_DIMENSION
        or height > _GENERATED_IMAGE_MAX_DIMENSION
        or width * height > _GENERATED_IMAGE_MAX_PIXELS
    ):
        raise WorkspaceInputError()


def _png_dimensions(raw: bytes) -> tuple[int, int]:
    offset = len(_PNG_SIGNATURE)
    chunks = 0
    width = height = 0
    saw_ihdr = False
    saw_idat = False
    saw_plte = False
    idat_finished = False
    bit_depth = colour_type = interlace = 0
    idat_payloads: list[bytes] = []
    while offset < len(raw):
        chunks += 1
        if chunks > _GENERATED_IMAGE_MAX_CHUNKS or len(raw) - offset < 12:
            raise WorkspaceInputError()
        length = int.from_bytes(raw[offset : offset + 4], "big")
        kind = raw[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(raw):
            raise WorkspaceInputError()
        payload = raw[offset + 8 : offset + 8 + length]
        checksum = int.from_bytes(raw[offset + 8 + length : end], "big")
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != checksum:
            raise WorkspaceInputError()
        offset = end

        if not saw_ihdr:
            if kind != b"IHDR" or length != 13:
                raise WorkspaceInputError()
            width, height, bit_depth, colour_type, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", payload)
            )
            valid_depths = {
                0: {1, 2, 4, 8, 16},
                2: {8, 16},
                3: {1, 2, 4, 8},
                4: {8, 16},
                6: {8, 16},
            }
            if (
                bit_depth not in valid_depths.get(colour_type, set())
                or compression != 0
                or filtering != 0
                or interlace not in {0, 1}
            ):
                raise WorkspaceInputError()
            _validate_dimensions(width, height)
            saw_ihdr = True
            continue
        if kind == b"IHDR" or kind in {b"acTL", b"fcTL", b"fdAT"}:
            raise WorkspaceInputError()
        if kind == b"IDAT":
            if idat_finished or length == 0:
                raise WorkspaceInputError()
            saw_idat = True
            idat_payloads.append(payload)
            continue
        if saw_idat:
            idat_finished = True
        if kind == b"PLTE":
            if saw_plte or saw_idat or length == 0 or length % 3 or length > 768:
                raise WorkspaceInputError()
            saw_plte = True
            continue
        if kind == b"IEND":
            if (
                length != 0
                or not saw_idat
                or offset != len(raw)
                or (colour_type == 3 and not saw_plte)
            ):
                raise WorkspaceInputError()
            _validate_png_data(
                idat_payloads,
                _png_decoded_bytes(
                    width,
                    height,
                    bit_depth=bit_depth,
                    colour_type=colour_type,
                    interlace=interlace,
                ),
            )
            return width, height
        if kind and 65 <= kind[0] <= 90:
            raise WorkspaceInputError()
    raise WorkspaceInputError()


def _png_decoded_bytes(
    width: int,
    height: int,
    *,
    bit_depth: int,
    colour_type: int,
    interlace: int,
) -> int:
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour_type]
    bits_per_pixel = channels * bit_depth

    def pass_bytes(
        start_x: int,
        start_y: int,
        step_x: int,
        step_y: int,
    ) -> int:
        if width <= start_x or height <= start_y:
            return 0
        pass_width = (width - start_x + step_x - 1) // step_x
        pass_height = (height - start_y + step_y - 1) // step_y
        row_bytes = (pass_width * bits_per_pixel + 7) // 8
        return pass_height * (1 + row_bytes)

    if interlace == 0:
        return pass_bytes(0, 0, 1, 1)
    return sum(
        pass_bytes(*adam7_pass)
        for adam7_pass in (
            (0, 0, 8, 8),
            (4, 0, 8, 8),
            (0, 4, 4, 8),
            (2, 0, 4, 4),
            (0, 2, 2, 4),
            (1, 0, 2, 2),
            (0, 1, 1, 2),
        )
    )


def _validate_png_data(payloads: list[bytes], expected_bytes: int) -> None:
    decompressor = zlib.decompressobj()
    decoded = 0
    try:
        for payload in payloads:
            pending = payload
            while pending:
                previous_size = len(pending)
                output = decompressor.decompress(
                    pending,
                    max(1, expected_bytes - decoded + 1),
                )
                decoded += len(output)
                if decoded > expected_bytes:
                    raise WorkspaceInputError()
                pending = decompressor.unconsumed_tail
                if pending and len(pending) >= previous_size and not output:
                    raise WorkspaceInputError()
        output = decompressor.flush(max(1, expected_bytes - decoded + 1))
    except zlib.error:
        raise WorkspaceInputError() from None
    decoded += len(output)
    if (
        decoded != expected_bytes
        or not decompressor.eof
        or decompressor.unused_data
        or decompressor.unconsumed_tail
    ):
        raise WorkspaceInputError()


def _jpeg_dimensions(raw: bytes) -> tuple[int, int]:
    offset = 2
    chunks = 0
    dimensions: tuple[int, int] | None = None
    frame_components = 0
    saw_scan = False
    while offset < len(raw):
        if raw[offset] != 0xFF:
            raise WorkspaceInputError()
        while offset < len(raw) and raw[offset] == 0xFF:
            offset += 1
        if offset >= len(raw):
            raise WorkspaceInputError()
        marker = raw[offset]
        offset += 1
        if marker == 0xD9:
            if dimensions is None or not saw_scan or offset != len(raw):
                raise WorkspaceInputError()
            return dimensions
        if marker in {0x00, 0x01, 0xD8} or 0xD0 <= marker <= 0xD7:
            raise WorkspaceInputError()
        chunks += 1
        if chunks > _GENERATED_IMAGE_MAX_CHUNKS or offset + 2 > len(raw):
            raise WorkspaceInputError()
        length = int.from_bytes(raw[offset : offset + 2], "big")
        if length < 2 or offset + length > len(raw):
            raise WorkspaceInputError()
        payload = raw[offset + 2 : offset + length]
        offset += length

        if marker in _JPEG_SOF_MARKERS:
            if marker not in _JPEG_SUPPORTED_SOF_MARKERS or dimensions is not None:
                raise WorkspaceInputError()
            if len(payload) < 9:
                raise WorkspaceInputError()
            precision = payload[0]
            height = int.from_bytes(payload[1:3], "big")
            width = int.from_bytes(payload[3:5], "big")
            components = payload[5]
            if precision not in {8, 12} or not 1 <= components <= 4:
                raise WorkspaceInputError()
            if len(payload) != 6 + (3 * components):
                raise WorkspaceInputError()
            _validate_dimensions(width, height)
            dimensions = (width, height)
            frame_components = components
        elif marker == 0xE2 and payload.startswith(b"MPF\x00"):
            raise WorkspaceInputError()
        elif marker == 0xDA:
            if dimensions is None or len(payload) < 6:
                raise WorkspaceInputError()
            scan_components = payload[0]
            if (
                not 1 <= scan_components <= frame_components
                or len(payload) != 1 + (2 * scan_components) + 3
            ):
                raise WorkspaceInputError()
            saw_scan = True
            while offset < len(raw):
                marker_start = raw.find(b"\xff", offset)
                if marker_start < 0:
                    raise WorkspaceInputError()
                offset = marker_start + 1
                while offset < len(raw) and raw[offset] == 0xFF:
                    offset += 1
                if offset >= len(raw):
                    raise WorkspaceInputError()
                scan_marker = raw[offset]
                offset += 1
                if scan_marker == 0x00 or 0xD0 <= scan_marker <= 0xD7:
                    continue
                if scan_marker == 0xD9:
                    if offset != len(raw):
                        raise WorkspaceInputError()
                    return dimensions
                offset = marker_start
                break
    raise WorkspaceInputError()


def _webp_dimensions(raw: bytes) -> tuple[int, int]:
    if len(raw) < 20 or int.from_bytes(raw[4:8], "little") + 8 != len(raw):
        raise WorkspaceInputError()
    offset = 12
    chunks = 0
    canvas: tuple[int, int] | None = None
    image: tuple[int, int] | None = None
    extended = False
    while offset < len(raw):
        chunks += 1
        if chunks > _GENERATED_IMAGE_MAX_CHUNKS or len(raw) - offset < 8:
            raise WorkspaceInputError()
        kind = raw[offset : offset + 4]
        length = int.from_bytes(raw[offset + 4 : offset + 8], "little")
        payload_start = offset + 8
        payload_end = payload_start + length
        padded_end = payload_end + (length & 1)
        if padded_end > len(raw):
            raise WorkspaceInputError()
        if length & 1 and raw[payload_end] != 0:
            raise WorkspaceInputError()
        payload = raw[payload_start:payload_end]
        offset = padded_end
        if kind in {b"ANIM", b"ANMF"}:
            raise WorkspaceInputError()
        if kind == b"VP8X":
            if chunks != 1 or extended or len(payload) != 10:
                raise WorkspaceInputError()
            flags = payload[0]
            if flags & 0xC3 or payload[1:4] != b"\x00\x00\x00":
                raise WorkspaceInputError()
            width = 1 + int.from_bytes(payload[4:7], "little")
            height = 1 + int.from_bytes(payload[7:10], "little")
            _validate_dimensions(width, height)
            canvas = (width, height)
            extended = True
            continue
        if kind in {b"VP8 ", b"VP8L"}:
            if image is not None:
                raise WorkspaceInputError()
            if kind == b"VP8 ":
                if len(payload) < 10 or payload[3:6] != b"\x9d\x01\x2a":
                    raise WorkspaceInputError()
                width = int.from_bytes(payload[6:8], "little") & 0x3FFF
                height = int.from_bytes(payload[8:10], "little") & 0x3FFF
            else:
                if len(payload) < 5 or payload[0] != 0x2F:
                    raise WorkspaceInputError()
                packed = int.from_bytes(payload[1:5], "little")
                if packed >> 29:
                    raise WorkspaceInputError()
                width = 1 + (packed & 0x3FFF)
                height = 1 + ((packed >> 14) & 0x3FFF)
            _validate_dimensions(width, height)
            image = (width, height)
            continue
        if not extended:
            raise WorkspaceInputError()
    if image is None or (canvas is not None and canvas != image):
        raise WorkspaceInputError()
    return canvas or image
