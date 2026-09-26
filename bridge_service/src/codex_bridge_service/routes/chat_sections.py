from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from ..auth import require_bridge_token
from ..models import ChatSectionRecord
from ..storage import ChatNavigationRevisionConflict

router = APIRouter()


class ChatSectionCollection(BaseModel):
    sections: list[ChatSectionRecord]


class CreateChatSectionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=320)


class UpdateChatSectionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=320)
    revision: int = Field(strict=True, ge=1)


def _require_chat_operations(request: Request) -> None:
    if "chat_operations_v1" not in request.app.state.feature_capabilities:
        raise HTTPException(status_code=409, detail={"code": "chat_operations_unavailable"})


@router.get("/chat-sections", response_model=ChatSectionCollection)
def list_chat_sections(
    request: Request,
    authorization: str | None = Header(default=None),
) -> ChatSectionCollection:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    _require_chat_operations(request)
    try:
        return ChatSectionCollection(sections=request.app.state.storage.list_chat_sections())
    except ValueError as exc:
        raise HTTPException(status_code=503, detail={"code": "chat_section_store_unavailable"}) from exc


@router.post(
    "/chat-sections",
    response_model=ChatSectionRecord,
    status_code=status.HTTP_201_CREATED,
)
def create_chat_section(
    payload: CreateChatSectionRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> ChatSectionRecord:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    _require_chat_operations(request)
    try:
        return request.app.state.storage.create_chat_section(payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "chat_section_conflict"}) from exc


@router.patch("/chat-sections/{section_id}", response_model=ChatSectionRecord)
def update_chat_section(
    section_id: str,
    payload: UpdateChatSectionRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> ChatSectionRecord:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    _require_chat_operations(request)
    try:
        return request.app.state.storage.update_chat_section(
            section_id, payload.name, expected_revision=payload.revision
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"code": "chat_section_not_found"}) from exc
    except ChatNavigationRevisionConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "section_revision_conflict"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "chat_section_conflict"}) from exc


@router.delete("/chat-sections/{section_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chat_section(
    section_id: str,
    request: Request,
    revision: int = Query(ge=1),
    authorization: str | None = Header(default=None),
) -> Response:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    _require_chat_operations(request)
    try:
        request.app.state.storage.delete_chat_section(
            section_id, expected_revision=revision
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"code": "chat_section_not_found"}) from exc
    except ChatNavigationRevisionConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "section_revision_conflict"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "chat_section_conflict"}) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
