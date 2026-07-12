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
    authorization: str | None = Header(default=None),
) -> Response:
    require_bridge_token(
        authorization=authorization,
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

        headers = {
            "Cache-Control": "private, no-store",
            "Content-Disposition": (
                "attachment; filename*=UTF-8''"
                f"{quote(artifact.filename, safe='')}"
            ),
            "Content-Length": str(size_bytes),
            "X-Content-Type-Options": "nosniff",
        }
        return StreamingResponse(
            _stream_and_close(stream),
            media_type="application/octet-stream",
            headers=headers,
            background=BackgroundTask(stream.close),
        )

    try:
        artifact = storage.get_artifact(thread_id, artifact_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc

    return FileResponse(
        path=artifact.stored_path,
        media_type=artifact.mime_type,
        filename=Path(artifact.stored_path).name,
    )


def _stream_and_close(stream: BinaryIO) -> Iterator[bytes]:
    try:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        stream.close()
