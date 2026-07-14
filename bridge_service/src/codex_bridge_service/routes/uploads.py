"""HA-only resumable attachment transport.

The multipart attachment endpoint remains the v0 compatibility route; this
router consumes a raw bounded request stream and persists only private session
metadata between requests.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..auth import require_bridge_token
from ..models import AttachmentRecord
from ..resource_limits import QuotaExceededError, ReservationConflictError
from ..storage import (
    ThreadNotFoundError,
    UploadConflictError,
    UploadNotFoundError,
    UploadValidationError,
)

router = APIRouter()


class UploadCreateRequest(BaseModel):
    filename: str
    mime_type: str = "application/octet-stream"
    relative_path: str | None = None
    size_bytes: int = Field(gt=0)
    sha256: str


def _require_token(request: Request, authorization: str | None) -> None:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )


def _upload_http_error(error: Exception) -> HTTPException:
    if isinstance(error, ThreadNotFoundError | UploadNotFoundError):
        return HTTPException(status_code=404, detail="upload not found")
    if isinstance(error, UploadConflictError):
        return HTTPException(status_code=409, detail="upload conflict")
    if isinstance(error, ReservationConflictError):
        return HTTPException(
            status_code=409,
            detail={
                "code": "reservation_conflict",
                "resource": error.resource,
                "retryable": True,
            },
        )
    if isinstance(error, QuotaExceededError):
        return HTTPException(
            status_code=413,
            detail={
                "code": "quota_exceeded",
                "resource": error.resource,
                "retryable": False,
            },
        )
    if isinstance(error, UploadValidationError):
        return HTTPException(status_code=400, detail="invalid upload")
    raise error


@router.post("/threads/{thread_id}/uploads", status_code=status.HTTP_201_CREATED)
def create_upload(
    thread_id: str,
    body: UploadCreateRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    _require_token(request, authorization)
    try:
        return request.app.state.storage.create_upload_session(thread_id=thread_id, **body.model_dump())
    except (ThreadNotFoundError, UploadNotFoundError, UploadConflictError, UploadValidationError, QuotaExceededError, ReservationConflictError) as error:
        raise _upload_http_error(error) from error


@router.get("/threads/{thread_id}/uploads/{upload_id}")
def get_upload(
    thread_id: str,
    upload_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    _require_token(request, authorization)
    try:
        return request.app.state.storage.get_upload_session(thread_id=thread_id, upload_id=upload_id)
    except (ThreadNotFoundError, UploadNotFoundError, UploadConflictError, UploadValidationError, QuotaExceededError, ReservationConflictError) as error:
        raise _upload_http_error(error) from error


@router.put("/threads/{thread_id}/uploads/{upload_id}/chunks/{index}")
async def put_upload_chunk(
    thread_id: str,
    upload_id: str,
    index: int,
    request: Request,
    upload_offset: str | None = Header(default=None, alias="Upload-Offset"),
    content_length: str | None = Header(default=None, alias="Content-Length"),
    chunk_sha256: str | None = Header(default=None, alias="X-Chunk-SHA256"),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    _require_token(request, authorization)
    try:
        offset = int(upload_offset) if upload_offset is not None else -1
        length = int(content_length) if content_length is not None else -1
        if length < 0 or chunk_sha256 is None:
            raise UploadValidationError("headers")
        # Request.stream is deliberately used rather than UploadFile/body.
        # The sink is a descriptor-rooted private .part file, never an HA Core
        # buffer or a process-global temporary file.
        staged = request.app.state.storage.begin_upload_chunk(
            thread_id=thread_id, upload_id=upload_id, index=index,
            offset=offset, content_length=length, sha256=chunk_sha256,
        )
        if isinstance(staged, dict):
            await _consume_duplicate_chunk(
                request,
                content_length=length,
                sha256=chunk_sha256,
            )
            return staged
        try:
            async for block in request.stream():
                staged.write(block)
            return staged.finish()
        except BaseException:
            staged.abort()
            raise
    except (ValueError, ThreadNotFoundError, UploadNotFoundError, UploadConflictError, UploadValidationError, QuotaExceededError, ReservationConflictError) as error:
        if isinstance(error, ValueError) and not isinstance(error, (UploadConflictError, UploadValidationError)):
            error = UploadValidationError("headers")
        raise _upload_http_error(error) from error


@router.post("/threads/{thread_id}/uploads/{upload_id}/complete", response_model=AttachmentRecord, status_code=status.HTTP_201_CREATED)
def complete_upload(
    thread_id: str,
    upload_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> AttachmentRecord:
    _require_token(request, authorization)
    try:
        return request.app.state.storage.complete_upload_session(thread_id=thread_id, upload_id=upload_id)
    except (ThreadNotFoundError, UploadNotFoundError, UploadConflictError, UploadValidationError, QuotaExceededError, ReservationConflictError) as error:
        raise _upload_http_error(error) from error


@router.delete("/threads/{thread_id}/uploads/{upload_id}")
def cancel_upload(
    thread_id: str,
    upload_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    _require_token(request, authorization)
    try:
        return request.app.state.storage.cancel_upload_session(thread_id=thread_id, upload_id=upload_id)
    except (ThreadNotFoundError, UploadNotFoundError, UploadConflictError, UploadValidationError, QuotaExceededError, ReservationConflictError) as error:
        raise _upload_http_error(error) from error


async def _consume_duplicate_chunk(
    request: Request,
    *,
    content_length: int,
    sha256: str,
) -> None:
    """Drain and verify an idempotent retry before reusing its prior result."""

    digest = hashlib.sha256()
    received = 0
    async for block in request.stream():
        if not isinstance(block, bytes) or received + len(block) > content_length:
            raise UploadValidationError("content_length")
        digest.update(block)
        received += len(block)
    if received != content_length or digest.hexdigest() != sha256:
        raise UploadValidationError("chunk_sha256")
