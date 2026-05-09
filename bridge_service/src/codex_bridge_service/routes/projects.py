from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, field_validator

from ..auth import require_bridge_token
from ..models import DEFAULT_MODEL, DEFAULT_THINKING_LEVEL, PathBrowseEntryRecord, PathBrowseRecord, ProjectRecord
from ..storage import ProjectMutationError, ProjectNotFoundError

router = APIRouter()


class CreateProjectRequest(BaseModel):
    name: str
    root_path: str
    default_model: str = DEFAULT_MODEL
    default_thinking_level: str = DEFAULT_THINKING_LEVEL

    @field_validator("name", "root_path")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    root_path: str | None = None
    default_model: str | None = None
    default_thinking_level: str | None = None


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
        expected_token=request.app.state.auth_token,
    )
    return request.app.state.storage.list_projects()


@router.post("/projects", response_model=ProjectRecord, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: CreateProjectRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> ProjectRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    return request.app.state.storage.create_project(
        name=payload.name,
        root_path=payload.root_path,
        default_model=payload.default_model,
        default_thinking_level=payload.default_thinking_level,
    )


@router.patch("/projects/{project_id}", response_model=ProjectRecord)
def update_project(
    project_id: str,
    payload: UpdateProjectRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> ProjectRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.storage.update_project(
            project_id,
            name=payload.name,
            root_path=payload.root_path,
            default_model=payload.default_model,
            default_thinking_level=payload.default_thinking_level,
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
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
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.storage.archive_project(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
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
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.storage.restore_project(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
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
        expected_token=request.app.state.auth_token,
    )
    try:
        request.app.state.storage.delete_project(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
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
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.storage.browse_paths(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="path not found") from exc


@router.post("/projects/folders", response_model=PathBrowseEntryRecord, status_code=status.HTTP_201_CREATED)
def create_project_folder(
    payload: CreateFolderRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> PathBrowseEntryRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    return request.app.state.storage.create_folder(
        parent_path=payload.parent_path,
        folder_name=payload.folder_name,
    )
