from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from ..auth import require_bridge_token
from ..models import RunMode, ThreadViewRecord
from ..storage import ProjectNotFoundError, ThreadNotFoundError

router = APIRouter()


class CreateThreadRequest(BaseModel):
    title: str
    project_id: str | None = None
    mode: RunMode = Field(default=RunMode.FULL_AUTO)
    model_override: str | None = None
    thinking_override: str | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value


class UpdateThreadRequest(BaseModel):
    title: str | None = None
    mode: RunMode | None = None
    model_override: str | None = None
    thinking_override: str | None = None


@router.post("/threads", response_model=ThreadViewRecord, status_code=status.HTTP_201_CREATED)
def create_thread(
    payload: CreateThreadRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> ThreadViewRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.storage.create_thread(
            title=payload.title,
            project_id=payload.project_id,
            mode=payload.mode,
            model_override=payload.model_override,
            thinking_override=payload.thinking_override,
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc


@router.get("/threads", response_model=list[ThreadViewRecord])
def list_threads(
    request: Request,
    authorization: str | None = Header(default=None),
) -> list[ThreadViewRecord]:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    return request.app.state.storage.list_threads()


@router.get("/threads/{thread_id}", response_model=ThreadViewRecord)
def get_thread(
    thread_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> ThreadViewRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.storage.get_thread(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc


@router.patch("/threads/{thread_id}", response_model=ThreadViewRecord)
def update_thread(
    thread_id: str,
    payload: UpdateThreadRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> ThreadViewRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.storage.update_thread(
            thread_id,
            **payload.model_dump(exclude_unset=True),
        )
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc
