import hashlib
import re
from pathlib import Path
from typing import BinaryIO, Iterator
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from ..auth import require_bridge_token
from ..models import ArtifactRecord, RuntimeProfile
from ..storage import ThreadNotFoundError
from ..workspace import (
    WorkspaceBoundaryError,
    WorkspaceNotFoundError,
    WorkspaceUnsupportedError,
)

router = APIRouter()


@router.get("/threads/{thread_id}/artifacts", response_model=list[ArtifactRecord])
def list_artifacts(
    thread_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> list[ArtifactRecord]:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.storage.sync_thread_artifacts(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workspace not found") from exc
    except WorkspaceBoundaryError as exc:
        raise HTTPException(
            status_code=400,
            detail="workspace contains an unsafe entry",
        ) from exc


@router.post(
    "/threads/{thread_id}/artifacts/workspace-archive",
    response_model=ArtifactRecord,
    status_code=201,
)
def create_workspace_archive(
    thread_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> ArtifactRecord:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.storage.create_workspace_archive(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc
    except WorkspaceUnsupportedError as exc:
        raise HTTPException(
            status_code=503,
            detail="workspace archive unavailable",
        ) from exc
    except WorkspaceBoundaryError as exc:
        raise HTTPException(status_code=400, detail="invalid artifact location") from exc


@router.get("/threads/{thread_id}/artifacts/{artifact_id}")
def download_artifact(
    thread_id: str,
    artifact_id: str,
    request: Request,
    range_header: str | None = Header(default=None, alias="Range"),
    if_range: str | None = Header(default=None, alias="If-Range"),
    authorization: str | None = Header(default=None),
) -> Response:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    storage = request.app.state.storage
    if storage.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
        try:
            artifact, stream, size_bytes = storage.open_artifact(thread_id, artifact_id)
        except ThreadNotFoundError as exc:
            raise HTTPException(status_code=404, detail="artifact not found") from exc
        except WorkspaceNotFoundError as exc:
            raise HTTPException(status_code=404, detail="artifact file not found") from exc
        except WorkspaceBoundaryError as exc:
            raise HTTPException(status_code=400, detail="invalid artifact location") from exc

        handed_off = False
        try:
            etag = _stream_sha256_etag(stream)
            byte_range = None
            if range_header and if_range == etag:
                byte_range = _parse_single_range(range_header, size_bytes)
            elif range_header and if_range is None:
                byte_range = _parse_single_range(range_header, size_bytes)
            if range_header and byte_range is None and (
                if_range is None or if_range == etag
            ):
                return Response(
                    status_code=416,
                    media_type="application/octet-stream",
                    headers={
                        "Content-Range": f"bytes */{size_bytes}",
                        "Accept-Ranges": "bytes",
                        "Cache-Control": "private, no-store, no-transform",
                        "Content-Disposition": _attachment_disposition(artifact.filename),
                        "ETag": etag,
                        "X-Content-Type-Options": "nosniff",
                    },
                )
            start, end = (
                byte_range if byte_range is not None else (0, size_bytes - 1)
            )
            length = end - start + 1
            headers = {
                "Accept-Ranges": "bytes",
                "Cache-Control": "private, no-store, no-transform",
                "Content-Disposition": _attachment_disposition(artifact.filename),
                "Content-Length": str(length),
                "ETag": etag,
                "X-Content-Type-Options": "nosniff",
            }
            if byte_range is not None:
                headers["Content-Range"] = f"bytes {start}-{end}/{size_bytes}"
            response = StreamingResponse(
                _stream_and_close(stream, start=start, length=length),
                media_type="application/octet-stream",
                headers=headers,
                background=BackgroundTask(stream.close),
                status_code=206 if byte_range is not None else 200,
            )
            handed_off = True
            return response
        finally:
            if not handed_off:
                stream.close()

    try:
        artifact = storage.get_artifact(thread_id, artifact_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc

    return FileResponse(
        path=artifact.stored_path,
        media_type=artifact.mime_type,
        filename=Path(artifact.stored_path).name,
    )


def _stream_sha256_etag(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while block := stream.read(1024 * 1024):
        digest.update(block)
    stream.seek(0)
    return f'"{digest.hexdigest()}"'


def _parse_single_range(value: str, size_bytes: int) -> tuple[int, int] | None:
    if not value.startswith("bytes=") or value.count(",") or size_bytes <= 0:
        return None
    spec = value[6:]
    if spec.count("-") != 1:
        return None
    first, last = spec.split("-", 1)
    if not first and not last:
        return None
    if not first:
        if not _bounded_decimal(last) or int(last) == 0:
            return None
        length = min(int(last), size_bytes)
        return size_bytes - length, size_bytes - 1
    if not _bounded_decimal(first) or (last and not _bounded_decimal(last)):
        return None
    start = int(first)
    end = int(last) if last else size_bytes - 1
    if start >= size_bytes or end < start:
        return None
    return start, min(end, size_bytes - 1)


def _bounded_decimal(value: str) -> bool:
    # Python intentionally rejects extremely long integer literals. Range
    # headers are untrusted transport metadata, so reject rather than letting
    # a parser-limit ValueError escape as a 500 response.
    return bool(value) and len(value) <= 19 and value.isdecimal()


def _attachment_disposition(filename: str) -> str:
    fallback = re.sub(r'[^A-Za-z0-9._-]', "_", filename).strip("._") or "download"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


def _stream_and_close(stream: BinaryIO, *, start: int = 0, length: int | None = None) -> Iterator[bytes]:
    try:
        stream.seek(start)
        remaining = length
        while True:
            if remaining is not None and remaining <= 0:
                break
            chunk = stream.read(min(1024 * 1024, remaining) if remaining is not None else 1024 * 1024)
            if not chunk:
                break
            if remaining is not None:
                remaining -= len(chunk)
            yield chunk
    finally:
        stream.close()
