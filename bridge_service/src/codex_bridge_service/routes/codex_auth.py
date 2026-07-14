from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from ..auth import require_bridge_token
from ..auth_coordinator import (
    AuthCoordinatorClosedError,
    AuthOperationConflictError,
)
from ..codex_app_server import CodexAppServerError
from ..models import CodexAuthStatusRecord

router = APIRouter()


class DeviceLoginRequest(BaseModel):
    force_logout: bool = False


@router.get("/auth/status", response_model=CodexAuthStatusRecord)
def get_auth_status(
    request: Request,
    authorization: str | None = Header(default=None),
) -> CodexAuthStatusRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    coordinator = getattr(request.app.state, "auth_coordinator", None)
    if coordinator is not None:
        return _invoke_structured_auth(coordinator.status)
    diagnostics = request.app.state.diagnostics_probe.probe()
    return request.app.state.auth_manager.status(last_error=diagnostics.last_error)


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
    coordinator = getattr(request.app.state, "auth_coordinator", None)
    if coordinator is not None:
        return _invoke_structured_auth(coordinator.start_device_login)
    return request.app.state.auth_manager.start_device_login(
        force_logout=payload.force_logout
    )


@router.post(
    "/auth/device-login/cancel",
    response_model=CodexAuthStatusRecord,
)
def cancel_device_login(
    request: Request,
    authorization: str | None = Header(default=None),
) -> CodexAuthStatusRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    coordinator = getattr(request.app.state, "auth_coordinator", None)
    if coordinator is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "auth_cancel_unsupported",
                "retryable": False,
            },
        )
    return _invoke_structured_auth(coordinator.cancel_login)


@router.post("/auth/logout", response_model=CodexAuthStatusRecord)
def logout(
    request: Request,
    authorization: str | None = Header(default=None),
) -> CodexAuthStatusRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    coordinator = getattr(request.app.state, "auth_coordinator", None)
    if coordinator is not None:
        return _invoke_structured_auth(coordinator.logout)
    return request.app.state.auth_manager.logout()


def _invoke_structured_auth(
    operation: Callable[[], CodexAuthStatusRecord],
) -> CodexAuthStatusRecord:
    try:
        return operation()
    except AuthOperationConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "auth_operation_conflict",
                "retryable": True,
            },
        ) from None
    except (AuthCoordinatorClosedError, CodexAppServerError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "auth_unavailable",
                "retryable": True,
            },
        ) from None
