"""Authenticated Bridge routes for Codex capabilities."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator

from ..auth import require_bridge_token
from ..capabilities import (
    CapabilitiesConflictError,
    CapabilitiesError,
    CapabilitiesInvalidError,
    CapabilitiesManager,
    CapabilitiesUnavailableError,
)

router = APIRouter()


class SkillConfigRequest(BaseModel):
    workspace_path: str = Field(min_length=1, max_length=4096)
    enabled: bool
    name: str | None = Field(default=None, max_length=128)
    path: str | None = Field(default=None, max_length=4096)


class SkillCreateRequest(BaseModel):
    workspace_path: str | None = Field(default=None, min_length=1, max_length=4096)
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=4096)
    instructions: str = Field(max_length=256 * 1024)


class PluginInstallRequest(BaseModel):
    plugin_name: str = Field(min_length=1, max_length=128)
    marketplace_name: str | None = Field(default=None, max_length=128)


class MarketplaceAddRequest(BaseModel):
    source: str = Field(min_length=1, max_length=512)
    ref_name: str | None = Field(default=None, max_length=128)
    sparse_paths: list[str] | None = Field(default=None, max_length=8)

    @field_validator("sparse_paths")
    @classmethod
    def validate_sparse_paths(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if any(not item or len(item) > 256 for item in value):
            raise ValueError("sparse path is invalid")
        return value


def _manager(request: Request) -> CapabilitiesManager:
    manager = getattr(request.app.state, "capabilities_manager", None)
    if not isinstance(manager, CapabilitiesManager):
        raise CapabilitiesUnavailableError()
    return manager


def _auth(request: Request, authorization: str | None) -> None:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )


def _call(operation):
    try:
        return operation()
    except CapabilitiesInvalidError as error:
        raise HTTPException(status_code=400, detail={"code": error.code}) from error
    except CapabilitiesConflictError as error:
        raise HTTPException(status_code=409, detail={"code": error.code}) from error
    except (CapabilitiesUnavailableError, CapabilitiesError) as error:
        raise HTTPException(status_code=503, detail={"code": error.code}) from error


@router.get("/capabilities/skills")
def list_skills(
    request: Request,
    workspace_path: str = Query(min_length=1, max_length=4096),
    force_reload: bool = Query(default=False),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(request, authorization)
    return _call(
        lambda: _manager(request).list_skills(workspace_path, force_reload=force_reload)
    )


@router.patch("/capabilities/skills")
def configure_skill(
    payload: SkillConfigRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, bool]:
    _auth(request, authorization)
    return _call(
        lambda: _manager(request).set_skill(
            payload.workspace_path,
            enabled=payload.enabled,
            name=payload.name,
            relative_path=payload.path,
        )
    )


@router.post("/capabilities/skills", status_code=status.HTTP_201_CREATED)
def create_skill(
    payload: SkillCreateRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(request, authorization)
    return _call(
        lambda: _manager(request).create_skill(
            workspace_path=payload.workspace_path,
            project_id=payload.project_id,
            name=payload.name,
            description=payload.description,
            instructions=payload.instructions,
        )
    )


@router.delete("/capabilities/skills/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill(
    name: str,
    request: Request,
    workspace_path: str | None = Query(default=None, max_length=4096),
    project_id: str | None = Query(default=None, max_length=128),
    authorization: str | None = Header(default=None),
) -> None:
    _auth(request, authorization)
    _call(
        lambda: _manager(request).delete_skill(
            workspace_path=workspace_path,
            project_id=project_id,
            name=name,
        )
    )


@router.get("/capabilities/plugins")
def list_plugins(
    request: Request,
    workspace_path: str = Query(min_length=1, max_length=4096),
    installed_only: bool = Query(default=False),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(request, authorization)
    return _call(
        lambda: _manager(request).list_plugins(
            workspace_path,
            installed_only=installed_only,
        )
    )


@router.post("/capabilities/plugins/install", status_code=status.HTTP_201_CREATED)
def install_plugin(
    payload: PluginInstallRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(request, authorization)
    return _call(
        lambda: _manager(request).install_plugin(
            payload.plugin_name,
            payload.marketplace_name,
        )
    )


@router.delete(
    "/capabilities/plugins/{plugin_id}", status_code=status.HTTP_204_NO_CONTENT
)
def uninstall_plugin(
    plugin_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    _auth(request, authorization)
    _call(lambda: _manager(request).uninstall_plugin(plugin_id))


@router.get("/capabilities/marketplaces")
def list_marketplaces(
    request: Request,
    workspace_path: str = Query(default=".", min_length=1, max_length=4096),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(request, authorization)
    return _call(
        lambda: _manager(request).list_plugins(
            workspace_path,
            installed_only=False,
        )
    )


@router.post("/capabilities/marketplaces", status_code=status.HTTP_201_CREATED)
def add_marketplace(
    payload: MarketplaceAddRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(request, authorization)
    return _call(
        lambda: _manager(request).add_marketplace(
            payload.source,
            ref_name=payload.ref_name,
            sparse_paths=payload.sparse_paths,
        )
    )


@router.delete(
    "/capabilities/marketplaces/{marketplace_name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_marketplace(
    marketplace_name: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    _auth(request, authorization)
    _call(lambda: _manager(request).remove_marketplace(marketplace_name))


@router.post("/capabilities/marketplaces/{marketplace_name}/upgrade")
def upgrade_marketplace(
    marketplace_name: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _auth(request, authorization)
    return _call(lambda: _manager(request).upgrade_marketplace(marketplace_name))
