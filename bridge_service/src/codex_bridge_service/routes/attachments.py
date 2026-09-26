from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import Response

from ..auth import require_bridge_token
from ..models import AttachmentRecord, RuntimeProfile
from ..storage import ThreadNotFoundError
from ..workspace import WorkspaceBoundaryError, WorkspaceNotFoundError
from .artifacts import snapshot_download_response

router = APIRouter()
download_router = APIRouter()


@download_router.get("/threads/{thread_id}/attachments/{attachment_id}")
def download_attachment(
    thread_id: str,
    attachment_id: str,
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
    if storage.runtime_profile is not RuntimeProfile.HOME_ASSISTANT:
        raise HTTPException(status_code=404, detail="attachment unavailable")
    try:
        attachment, stream, size_bytes = storage.open_attachment(thread_id, attachment_id)
    except (ThreadNotFoundError, WorkspaceNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="attachment not found") from exc
    except WorkspaceBoundaryError as exc:
        raise HTTPException(status_code=400, detail="invalid attachment location") from exc
    return snapshot_download_response(
        stream,
        size_bytes=size_bytes,
        filename=attachment.filename,
        range_header=range_header,
        if_range=if_range,
    )


@router.post(
    "/threads/{thread_id}/attachments",
    response_model=AttachmentRecord,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    thread_id: str,
    request: Request,
    file: UploadFile = File(...),
    relative_path: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
) -> AttachmentRecord:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    try:
        await file.seek(0)
        return request.app.state.storage.attach_file(
            thread_id=thread_id,
            filename=file.filename or "",
            mime_type=file.content_type or "application/octet-stream",
            content=file.file,
            relative_path=relative_path,
        )
    except ThreadNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="thread not found",
        ) from exc
    except WorkspaceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="attachment location not found",
        ) from exc
    except WorkspaceBoundaryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid attachment location",
        ) from exc
