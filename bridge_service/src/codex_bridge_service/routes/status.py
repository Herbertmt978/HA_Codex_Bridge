from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import require_bridge_token
from ..codex_app_server import CodexAppServerError
from ..feature_capabilities import provider_capabilities
from ..models import (
    BridgeDiagnosticsRecord,
    BridgeStatusRecord,
    CodexAccountRecord,
    CodexAuthStatusRecord,
    ProviderCapabilitiesRecord,
    SUPPORTED_THINKING_LEVELS,
)

router = APIRouter()


class ConsumeResetCreditRequest(BaseModel):
    credit_id: str = Field(pattern=r"^[\x21-\x7e]{1,256}$")
    idempotency_key: str = Field(pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")


@router.post("/account/reset-credits/consume")
def consume_reset_credit(
    payload: ConsumeResetCreditRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    require_bridge_token(
        authorization=authorization, request=request,
        expected_token=request.app.state.auth_token,
    )
    if "reset_credits_v1" not in request.app.state.feature_capabilities:
        raise HTTPException(409, detail={"code": "capability_unavailable", "retryable": False})
    client = request.app.state.codex_app_server
    try:
        response = client.request(
            "account/rateLimitResetCredit/consume",
            {"creditId": payload.credit_id, "idempotencyKey": payload.idempotency_key},
            timeout_seconds=15.0,
        )
    except CodexAppServerError:
        raise HTTPException(503, detail={"code": "reset_credit_unavailable", "retryable": True}) from None
    outcome = response.get("outcome") if isinstance(response, dict) else None
    if outcome not in {"reset", "nothingToReset", "noCredit", "alreadyRedeemed"}:
        raise HTTPException(503, detail={"code": "reset_credit_unavailable", "retryable": True})
    request.app.state.storage.limits_probe.invalidate()
    return {"outcome": outcome}


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
        provider_capabilities=ProviderCapabilitiesRecord(
            **provider_capabilities(request.app.state)
        ),
    )


def _auth_status(request: Request, last_error: str | None) -> CodexAuthStatusRecord:
    coordinator = getattr(request.app.state, "auth_coordinator", None)
    if coordinator is not None:
        return coordinator.status()
    manager = getattr(request.app.state, "auth_manager", None)
    if manager is not None:
        return manager.status(last_error=last_error)
    return CodexAuthStatusRecord()
