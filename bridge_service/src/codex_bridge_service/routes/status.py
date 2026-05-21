from fastapi import APIRouter, Header, Request

from ..auth import require_bridge_token
from ..models import (
    BridgeDiagnosticsRecord,
    BridgeStatusRecord,
    CodexAccountRecord,
    CodexAuthStatusRecord,
    SUPPORTED_MODELS,
    SUPPORTED_THINKING_LEVELS,
)

router = APIRouter()


@router.get("/status", response_model=BridgeStatusRecord)
def get_status(
    request: Request,
    authorization: str | None = Header(default=None),
) -> BridgeStatusRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    diagnostics = (
        request.app.state.diagnostics_probe.probe()
        if getattr(request.app.state, "diagnostics_probe", None) is not None
        else BridgeDiagnosticsRecord()
    )
    return BridgeStatusRecord(
        models=list(SUPPORTED_MODELS),
        thinking_levels=list(SUPPORTED_THINKING_LEVELS),
        limits=request.app.state.storage.get_limits_status(refresh=True),
        account=(
            request.app.state.account_probe.probe()
            if getattr(request.app.state, "account_probe", None) is not None
            else CodexAccountRecord()
        ),
        auth=(
            request.app.state.auth_manager.status(last_error=diagnostics.last_error)
            if getattr(request.app.state, "auth_manager", None) is not None
            else CodexAuthStatusRecord()
        ),
        diagnostics=diagnostics,
    )
