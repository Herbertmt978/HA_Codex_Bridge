"""Authenticated HTTP facade for the constrained MCP connection manager."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict

from ..auth import require_bridge_token
from ..mcp_manager import (
    MCP_DISABLED_MESSAGE,
    McpConflictError,
    McpDisabledError,
    McpManager,
    McpManagerError,
    McpNotFoundError,
    McpUnavailableError,
    McpValidationError,
)


router = APIRouter()


class CreateMcpServerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    name: str
    url: str
    oauth_client_id: str | None = None
    oauth_resource: str | None = None


class McpOAuthLoginResponse(BaseModel):
    """One-shot response; callers must not persist the authorization URL."""

    model_config = ConfigDict(extra="forbid")

    authorization_url: str


def _manager(request: Request) -> McpManager:
    manager = getattr(request.app.state, "mcp_manager", None)
    if not isinstance(manager, McpManager):
        raise _problem(McpUnavailableError())
    return manager


def _authorize(request: Request, authorization: str | None) -> None:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )


@router.get("/mcp/servers")
def list_mcp_servers(
    request: Request,
    authorization: str | None = Header(default=None),
) -> list[dict[str, object]]:
    _authorize(request, authorization)
    try:
        return _manager(request).list_servers()
    except McpManagerError as error:
        raise _problem(error) from None


@router.post(
    "/mcp/servers",
    status_code=status.HTTP_201_CREATED,
)
def create_mcp_server(
    payload: CreateMcpServerRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    _authorize(request, authorization)
    try:
        return _manager(request).create_server(
            name=payload.name,
            url=payload.url,
            oauth_client_id=payload.oauth_client_id,
            oauth_resource=payload.oauth_resource,
        )
    except McpManagerError as error:
        raise _problem(error) from None


@router.delete("/mcp/servers/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mcp_server(
    name: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    _authorize(request, authorization)
    try:
        _manager(request).remove_server(name)
    except McpManagerError as error:
        raise _problem(error) from None


@router.post(
    "/mcp/servers/{name}/oauth/login",
    response_model=McpOAuthLoginResponse,
)
def start_mcp_oauth_login(
    name: str,
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    _authorize(request, authorization)
    try:
        authorization_url = _manager(request).start_oauth_login(name)
    except McpManagerError as error:
        raise _problem(error) from None
    # Authorization URLs commonly contain one-time OAuth state.  No caching
    # layer should retain this direct response.
    response.headers["Cache-Control"] = "no-store"
    return {"authorization_url": authorization_url}


def _problem(error: McpManagerError) -> HTTPException:
    if isinstance(error, McpValidationError):
        status_code = status.HTTP_400_BAD_REQUEST
    elif isinstance(error, McpNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, McpConflictError):
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    detail: dict[str, object] = {
        "code": error.code,
        "retryable": error.retryable,
    }
    if isinstance(error, McpDisabledError):
        detail["message"] = MCP_DISABLED_MESSAGE
    return HTTPException(status_code=status_code, detail=detail)
