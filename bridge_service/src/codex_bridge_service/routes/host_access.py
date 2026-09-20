"""HA administrator control of the optional private host companion."""

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import Field

from ..auth import require_bridge_token
from ..host_access import HostAccessError, HostAccessManager, HostPairing
from ..host_access_contract import HostContract

router = APIRouter()


class EnableHostRequest(HostContract):
    scope_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    acknowledged: bool


def manager_for(request: Request, authorization: str | None) -> HostAccessManager:
    require_bridge_token(
        authorization=authorization, request=request,
        expected_token=request.app.state.auth_token,
    )
    manager = getattr(request.app.state, "host_access", None)
    if manager is None:
        raise HTTPException(status_code=409, detail="Host Access is unavailable")
    return manager


def validate_host_selection(request: Request, mode, grant_id: str | None) -> None:
    if getattr(mode, "value", mode) != "haos-full-access":
        if grant_id is not None:
            raise HTTPException(status_code=400, detail="Host grant requires host access mode")
        return
    manager = getattr(request.app.state, "host_access", None)
    if manager is None:
        raise HostAccessError("Host Access is unavailable on this Bridge.")
    manager.validate_selection(grant_id)


@router.get("/host-access")
def status(request: Request, authorization: str | None = Header(default=None)):
    return manager_for(request, authorization).status()


@router.put("/host-access/worker")
def pair(payload: HostPairing, request: Request, authorization: str | None = Header(default=None)):
    manager = manager_for(request, authorization)
    manager.pair(payload)
    return manager.status()


@router.post("/host-access/enable")
def enable(payload: EnableHostRequest, request: Request, authorization: str | None = Header(default=None)):
    return manager_for(request, authorization).enable(payload.scope_revision, payload.acknowledged)


@router.post("/host-access/revoke")
def revoke(request: Request, authorization: str | None = Header(default=None)):
    result = manager_for(request, authorization).revoke()
    # The grant is removed before asking the runtime to stop active host turns.
    # A worker outage cannot keep local admission open.
    cancel = getattr(request.app.state.runner, "cancel_host_runs", None)
    if callable(cancel):
        cancel()
    return result
