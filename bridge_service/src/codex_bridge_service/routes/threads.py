from fastapi import APIRouter, Header, Request, status
from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from ..auth import require_bridge_token
from ..models import RunMode, ThreadRecord
from ..storage import ThreadNotFoundError

router = APIRouter()


class CreateThreadRequest(BaseModel):
    title: str
    mode: RunMode = Field(default=RunMode.FULL_AUTO)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value


@router.post("/threads", response_model=ThreadRecord, status_code=status.HTTP_201_CREATED)
def create_thread(
    payload: CreateThreadRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> ThreadRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    return request.app.state.storage.create_thread(
        title=payload.title,
        mode=payload.mode,
    )


@router.get("/threads", response_model=list[ThreadRecord])
def list_threads(
    request: Request,
    authorization: str | None = Header(default=None),
) -> list[ThreadRecord]:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    return request.app.state.storage.list_threads()


@router.get("/threads/{thread_id}", response_model=ThreadRecord)
def get_thread(
    thread_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> ThreadRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.storage.load_thread(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc
