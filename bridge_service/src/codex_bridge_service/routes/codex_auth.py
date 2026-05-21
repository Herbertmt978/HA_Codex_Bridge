from fastapi import APIRouter, Header, Request, status
from pydantic import BaseModel

from ..auth import require_bridge_token
from ..models import CodexAuthStatusRecord

router = APIRouter()


class DeviceLoginRequest(BaseModel):
    force_logout: bool = True


@router.get("/auth/status", response_model=CodexAuthStatusRecord)
def get_auth_status(
    request: Request,
    authorization: str | None = Header(default=None),
) -> CodexAuthStatusRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    diagnostics = (
        request.app.state.diagnostics_probe.probe()
        if getattr(request.app.state, "diagnostics_probe", None) is not None
        else None
    )
    return request.app.state.auth_manager.status(
        last_error=diagnostics.last_error if diagnostics is not None else None
    )


@router.post(
    "/auth/device-login",
    response_model=CodexAuthStatusRecord,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_device_login(
    payload: DeviceLoginRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> CodexAuthStatusRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    return request.app.state.auth_manager.start_device_login(force_logout=payload.force_logout)


@router.post("/auth/logout", response_model=CodexAuthStatusRecord)
def logout(
    request: Request,
    authorization: str | None = Header(default=None),
) -> CodexAuthStatusRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    return request.app.state.auth_manager.logout()
