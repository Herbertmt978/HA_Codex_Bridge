from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile, status

from ..auth import require_bridge_token
from ..models import AttachmentRecord
from ..storage import ThreadNotFoundError

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
    authorization: str | None = Header(default=None),
) -> AttachmentRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.storage.attach_file(
            thread_id=thread_id,
            filename=file.filename or "",
            mime_type=file.content_type or "application/octet-stream",
            content=await file.read(),
        )
    except ThreadNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="thread not found",
        ) from exc
