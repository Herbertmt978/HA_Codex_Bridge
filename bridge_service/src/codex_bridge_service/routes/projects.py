from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, field_validator

from ..auth import require_bridge_token
from ..models import (
    PathBrowseEntryRecord,
    PathBrowseRecord,
    ProjectRecord,
    RuntimeProfile,
)
from ..runtime_broker import RuntimeUnavailableError
from ..storage import ProjectMutationError, ProjectNotFoundError
from ..workspace import WorkspaceBoundaryError, WorkspaceNotFoundError

router = APIRouter()


class CreateProjectRequest(BaseModel):
    name: str
    root_path: str | None = None
    default_model: str | None = None
    default_thinking_level: str | None = None

    @field_validator("name")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator("default_model", "default_thinking_level")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("value must not be blank")
        return value.strip() if value is not None else None


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    root_path: str | None = None
    default_model: str | None = None
    default_thinking_level: str | None = None

    @field_validator("default_model", "default_thinking_level")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("value must not be blank")
        return value.strip() if value is not None else None


class CreateFolderRequest(BaseModel):
    parent_path: str
    folder_name: str

    @field_validator("parent_path", "folder_name")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value


@router.get("/projects", response_model=list[ProjectRecord])
def list_projects(
    request: Request,
    authorization: str | None = Header(default=None),
) -> list[ProjectRecord]:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    model_catalog = request.app.state.model_catalog_probe.probe()
    try:
        request.app.state.storage.reconcile_special_projects(
            default_model=model_catalog.default_model,
            default_thinking_level=model_catalog.default_thinking_level,
            defaults_provisional=model_catalog.stale,
        )
        return request.app.state.storage.list_projects(
            default_model=model_catalog.default_model,
            default_thinking_level=model_catalog.default_thinking_level,
            defaults_provisional=model_catalog.stale,
        )
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workspace path not found") from exc
    except WorkspaceBoundaryError as exc:
        raise HTTPException(status_code=400, detail="invalid workspace path") from exc


@router.post(
    "/projects", response_model=ProjectRecord, status_code=status.HTTP_201_CREATED
)
def create_project(
    payload: CreateProjectRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> ProjectRecord:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    model_catalog = request.app.state.model_catalog_probe.probe()
    default_model = payload.default_model or model_catalog.default_model
    model_record = next(
        (model for model in model_catalog.models if model.model == default_model),
        None,
    )
    if payload.default_thinking_level is not None:
        default_thinking_level = payload.default_thinking_level
    elif payload.default_model is None:
        default_thinking_level = model_catalog.default_thinking_level
    elif model_record is not None:
        default_thinking_level = model_record.default_thinking_level
    else:
        default_thinking_level = model_catalog.default_thinking_level
    if (
        model_record is not None
        and model_record.thinking_levels
        and default_thinking_level not in model_record.thinking_levels
    ):
        raise HTTPException(
            status_code=400,
            detail=f"{default_thinking_level} is not supported by {default_model}",
        )
    try:
        return request.app.state.storage.create_project(
            name=payload.name,
            root_path=payload.root_path,
            default_model=default_model,
            default_thinking_level=default_thinking_level,
        )
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workspace path not found") from exc
    except WorkspaceBoundaryError as exc:
        raise HTTPException(status_code=400, detail="invalid workspace path") from exc


@router.patch("/projects/{project_id}", response_model=ProjectRecord)
def update_project(
    project_id: str,
    payload: UpdateProjectRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> ProjectRecord:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    try:
        current = request.app.state.storage.load_project(project_id)
        updates = payload.model_dump(exclude_unset=True)
        model_was_requested = updates.get("default_model") is not None
        thinking_was_requested = updates.get("default_thinking_level") is not None
        target_model = updates.get("default_model") or current.default_model
        target_thinking = (
            updates.get("default_thinking_level") or current.default_thinking_level
        )
        if model_was_requested or thinking_was_requested:
            model_catalog = request.app.state.model_catalog_probe.probe()
            model_record = next(
                (
                    model
                    for model in model_catalog.models
                    if model.model == target_model
                ),
                None,
            )
            if (
                model_record is not None
                and model_record.thinking_levels
                and target_thinking not in model_record.thinking_levels
            ):
                if model_was_requested and not thinking_was_requested:
                    target_thinking = model_record.default_thinking_level
                    updates["default_thinking_level"] = target_thinking
                else:
                    raise ValueError(
                        f"{target_thinking} is not supported by {target_model}"
                    )
        return request.app.state.storage.update_project(
            project_id,
            name=updates.get("name"),
            root_path=updates.get("root_path"),
            default_model=updates.get("default_model"),
            default_thinking_level=updates.get("default_thinking_level"),
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workspace path not found") from exc
    except WorkspaceBoundaryError as exc:
        raise HTTPException(status_code=400, detail="invalid workspace path") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/archive", response_model=ProjectRecord)
def archive_project(
    project_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> ProjectRecord:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.storage.archive_project(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workspace path not found") from exc
    except WorkspaceBoundaryError as exc:
        raise HTTPException(status_code=400, detail="invalid workspace path") from exc
    except ProjectMutationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/{project_id}/restore", response_model=ProjectRecord)
def restore_project(
    project_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> ProjectRecord:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.storage.restore_project(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workspace path not found") from exc
    except WorkspaceBoundaryError as exc:
        raise HTTPException(status_code=400, detail="invalid workspace path") from exc
    except ProjectMutationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    try:
        delete_with_runtime_ownership = getattr(
            request.app.state.runner,
            "delete_project",
            None,
        )
        if request.app.state.storage.runtime_profile is RuntimeProfile.HOME_ASSISTANT:
            if not callable(delete_with_runtime_ownership):
                raise RuntimeUnavailableError()
            delete_with_runtime_ownership(project_id)
        else:
            request.app.state.storage.delete_project(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workspace path not found") from exc
    except WorkspaceBoundaryError as exc:
        raise HTTPException(status_code=400, detail="invalid workspace path") from exc
    except ProjectMutationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/projects/browse", response_model=PathBrowseRecord)
def browse_project_paths(
    request: Request,
    path: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
) -> PathBrowseRecord:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.storage.browse_paths(path)
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workspace path not found") from exc
    except WorkspaceBoundaryError as exc:
        raise HTTPException(status_code=400, detail="invalid workspace path") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="path not found") from exc


@router.post(
    "/projects/folders",
    response_model=PathBrowseEntryRecord,
    status_code=status.HTTP_201_CREATED,
)
def create_project_folder(
    payload: CreateFolderRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> PathBrowseEntryRecord:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.storage.create_folder(
            parent_path=payload.parent_path,
            folder_name=payload.folder_name,
        )
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workspace path not found") from exc
    except WorkspaceBoundaryError as exc:
        raise HTTPException(status_code=400, detail="invalid workspace path") from exc
