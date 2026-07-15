from fastapi import APIRouter, Header, Request

from ..auth import require_bridge_token
from ..models import (
    BridgeDiagnosticsRecord,
    BridgeStatusRecord,
    CodexAccountRecord,
    CodexAuthStatusRecord,
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
        request=request,
        expected_token=request.app.state.auth_token,
    )
    diagnostics = (
        request.app.state.diagnostics_probe.probe()
        if getattr(request.app.state, "diagnostics_probe", None) is not None
        else BridgeDiagnosticsRecord()
    )
    auth = _auth_status(request, diagnostics.last_error)
    model_catalog = request.app.state.model_catalog_probe.probe()
    request.app.state.storage.reconcile_special_projects(
        default_model=model_catalog.default_model,
        default_thinking_level=model_catalog.default_thinking_level,
        defaults_provisional=model_catalog.stale,
    )
    thinking_levels = list(
        dict.fromkeys(
            level
            for model in model_catalog.models
            for level in model.thinking_levels
        )
    ) or list(SUPPORTED_THINKING_LEVELS)
    return BridgeStatusRecord(
        models=[model.model for model in model_catalog.models],
        thinking_levels=thinking_levels,
        model_catalog=model_catalog,
        limits=request.app.state.storage.get_limits_status(refresh=True),
        account=(
            request.app.state.account_probe.probe()
            if getattr(request.app.state, "account_probe", None) is not None
            else CodexAccountRecord()
        ),
        auth=auth,
        diagnostics=diagnostics,
    )


def _auth_status(request: Request, last_error: str | None) -> CodexAuthStatusRecord:
    coordinator = getattr(request.app.state, "auth_coordinator", None)
    if coordinator is not None:
        return coordinator.status()
    manager = getattr(request.app.state, "auth_manager", None)
    if manager is not None:
        return manager.status(last_error=last_error)
    return CodexAuthStatusRecord()
