from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse

from ..auth import require_bridge_token
from ..models import ArtifactRecord
from ..storage import ThreadNotFoundError

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


@router.get("/threads/{thread_id}/artifacts/{artifact_id}")
def download_artifact(
    thread_id: str,
    artifact_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> FileResponse:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    try:
        artifact = request.app.state.storage.get_artifact(thread_id, artifact_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc

    return FileResponse(
        path=artifact.stored_path,
        media_type=artifact.mime_type,
        filename=Path(artifact.stored_path).name,
    )
