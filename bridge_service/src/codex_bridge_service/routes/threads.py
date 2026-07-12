from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
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

    @field_validator("model_override", "thinking_override")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("value must not be blank")
        return value.strip() if value is not None else None


class UpdateThreadRequest(BaseModel):
    title: str | None = None
    mode: RunMode | None = None
    model_override: str | None = None
    thinking_override: str | None = None

    @field_validator("model_override", "thinking_override")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("value must not be blank")
        return value.strip() if value is not None else None


def _compatible_default_thinking(model_record) -> str:
    if model_record.default_thinking_level in model_record.thinking_levels:
        return model_record.default_thinking_level
    if "medium" in model_record.thinking_levels:
        return "medium"
    return model_record.thinking_levels[0]


def _repair_or_validate_model_effort(
    model_catalog,
    *,
    effective_model: str,
    effective_thinking_level: str,
    explicit_thinking_level: str | None,
) -> str | None:
    model_record = next(
        (model for model in model_catalog.models if model.model == effective_model),
        None,
    )
    if (
        model_record is None
        or not model_record.thinking_levels
        or effective_thinking_level in model_record.thinking_levels
    ):
        return None
    if explicit_thinking_level is not None:
        raise ValueError(f"{effective_thinking_level} is not supported by {effective_model}")
    return _compatible_default_thinking(model_record)


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
    needs_catalog = (
        payload.project_id is None
        or request.app.state.storage.is_special_project_id(payload.project_id)
        or payload.model_override is not None
        or payload.thinking_override is not None
    )
    model_catalog = request.app.state.model_catalog_probe.probe() if needs_catalog else None
    if model_catalog is not None:
        request.app.state.storage.reconcile_special_projects(
            default_model=model_catalog.default_model,
            default_thinking_level=model_catalog.default_thinking_level,
            defaults_provisional=model_catalog.stale,
        )
    try:
        if payload.project_id is None:
            project = request.app.state.storage.ensure_direct_project(
                default_model=model_catalog.default_model,
                default_thinking_level=model_catalog.default_thinking_level,
                defaults_provisional=model_catalog.stale,
            )
        else:
            project = request.app.state.storage.load_project(payload.project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc

    thinking_override = payload.thinking_override
    if model_catalog is not None and (
        payload.model_override is not None or payload.thinking_override is not None
    ):
        effective_model = payload.model_override or project.default_model
        effective_thinking = payload.thinking_override or project.default_thinking_level
        try:
            repaired_thinking = _repair_or_validate_model_effort(
                model_catalog,
                effective_model=effective_model,
                effective_thinking_level=effective_thinking,
                explicit_thinking_level=payload.thinking_override,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if repaired_thinking is not None:
            thinking_override = repaired_thinking

    create_kwargs: dict[str, object] = {
        "title": payload.title,
        "project_id": payload.project_id,
        "mode": payload.mode,
        "model_override": payload.model_override,
        "thinking_override": thinking_override,
    }
    if payload.project_id is None and model_catalog is not None:
        create_kwargs.update(
            direct_default_model=model_catalog.default_model,
            direct_default_thinking_level=model_catalog.default_thinking_level,
            direct_defaults_provisional=model_catalog.stale,
        )
    try:
        return request.app.state.storage.create_thread(
            **create_kwargs,
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc


@router.get("/threads", response_model=list[ThreadViewRecord])
def list_threads(
    request: Request,
    include_archived: bool = Query(default=False),
    authorization: str | None = Header(default=None),
) -> list[ThreadViewRecord]:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    return request.app.state.storage.list_threads(include_archived=include_archived)


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
        current = request.app.state.storage.get_thread(thread_id)
        updates = payload.model_dump(exclude_unset=True)
        if "model_override" in updates or "thinking_override" in updates:
            model_override = (
                updates["model_override"]
                if "model_override" in updates
                else current.model_override
            )
            thinking_override = (
                updates["thinking_override"]
                if "thinking_override" in updates
                else current.thinking_override
            )
            effective_model = model_override or current.default_model
            effective_thinking = thinking_override or current.default_thinking_level
            model_catalog = request.app.state.model_catalog_probe.probe()
            repaired_thinking = _repair_or_validate_model_effort(
                model_catalog,
                effective_model=effective_model,
                effective_thinking_level=effective_thinking,
                explicit_thinking_level=(
                    updates.get("thinking_override")
                    if "thinking_override" in updates
                    else None
                ),
            )
            if repaired_thinking is not None:
                updates["thinking_override"] = repaired_thinking
        return request.app.state.storage.update_thread(
            thread_id,
            **updates,
        )
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/threads/{thread_id}/archive", response_model=ThreadViewRecord)
def archive_thread(
    thread_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> ThreadViewRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.storage.archive_thread(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc


@router.post("/threads/{thread_id}/restore", response_model=ThreadViewRecord)
def restore_thread(
    thread_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> ThreadViewRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.storage.restore_thread(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_thread(
    thread_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> Response:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    try:
        request.app.state.storage.delete_thread(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
