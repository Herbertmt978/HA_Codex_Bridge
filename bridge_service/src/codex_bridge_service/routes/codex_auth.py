from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from ..auth import require_bridge_token
from ..auth_coordinator import (
    AuthCoordinatorClosedError,
    AuthOperationConflictError,
)
from ..account_profiles import (
    AccountProfileError,
    AccountProfileReauthenticationRequiredError,
)
from ..codex_app_server import CodexAppServerError
from ..models import CodexAuthStatusRecord
from ..workspace import WorkspaceBoundaryError

router = APIRouter()


class DeviceLoginRequest(BaseModel):
    force_logout: bool = False


class SaveAccountProfileRequest(BaseModel):
    label: str


class SwitchAccountProfileRequest(BaseModel):
    profile_id: str


@router.get("/auth/status", response_model=CodexAuthStatusRecord)
def get_auth_status(
    request: Request,
    authorization: str | None = Header(default=None),
) -> CodexAuthStatusRecord:
    require_bridge_token(
        authorization=authorization,
        request=request,
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
        request=request,
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
        request=request,
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
        request=request,
        expected_token=request.app.state.auth_token,
    )
    coordinator = getattr(request.app.state, "auth_coordinator", None)
    if coordinator is not None:
        return _invoke_structured_auth(coordinator.logout)
    return request.app.state.auth_manager.logout()


def _profiles(request: Request, authorization: str | None):
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    store = getattr(request.app.state, "account_profile_store", None)
    coordinator = getattr(request.app.state, "auth_coordinator", None)
    if store is None or coordinator is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "account_profiles_unavailable", "retryable": False},
        )
    return store, coordinator


@router.get("/auth/profiles")
def list_account_profiles(
    request: Request, authorization: str | None = Header(default=None)
) -> list[dict[str, object]]:
    store, _ = _profiles(request, authorization)
    return _invoke_profile_operation(store.list_profiles)


@router.post("/auth/profiles")
def save_account_profile(
    payload: SaveAccountProfileRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    store, coordinator = _profiles(request, authorization)
    return _invoke_profile_operation(lambda: coordinator.save_account_profile(store, payload.label))


@router.post("/auth/profiles/switch", response_model=CodexAuthStatusRecord)
def switch_account_profile(
    payload: SwitchAccountProfileRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> CodexAuthStatusRecord:
    store, coordinator = _profiles(request, authorization)
    return _invoke_profile_operation(
        lambda: coordinator.switch_account_profile(store, payload.profile_id)
    )


@router.post("/auth/profiles/prepare-login", response_model=CodexAuthStatusRecord)
def prepare_new_account_login(
    request: Request,
    authorization: str | None = Header(default=None),
) -> CodexAuthStatusRecord:
    store, coordinator = _profiles(request, authorization)
    return _invoke_profile_operation(lambda: coordinator.prepare_new_account_login(store))


@router.delete("/auth/profiles/{profile_id}", status_code=204)
def remove_account_profile(
    profile_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    store, coordinator = _profiles(request, authorization)
    _invoke_profile_operation(lambda: coordinator.remove_account_profile(store, profile_id))


def _invoke_profile_operation(operation):
    try:
        return operation()
    except AccountProfileReauthenticationRequiredError:
        raise HTTPException(
            status_code=409,
            detail={"code": "account_profile_reauthentication_required", "retryable": False},
        ) from None
    except AccountProfileError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "account_profile_invalid", "message": str(error), "retryable": False},
        ) from None
    except AuthOperationConflictError:
        raise HTTPException(
            status_code=409,
            detail={"code": "auth_operation_conflict", "retryable": True},
        ) from None
    except (AuthCoordinatorClosedError, CodexAppServerError, WorkspaceBoundaryError):
        raise HTTPException(
            status_code=503,
            detail={"code": "account_profiles_unavailable", "retryable": True},
        ) from None


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
