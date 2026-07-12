from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile, status

from ..auth import require_bridge_token
from ..models import AttachmentRecord
from ..storage import ThreadNotFoundError
from ..workspace import WorkspaceBoundaryError, WorkspaceNotFoundError

router = APIRouter()


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
