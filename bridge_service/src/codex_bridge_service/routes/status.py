from fastapi import APIRouter, Header, Request

from ..auth import require_bridge_token
from ..models import BridgeStatusRecord, SUPPORTED_MODELS, SUPPORTED_THINKING_LEVELS

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
    return BridgeStatusRecord(
        models=list(SUPPORTED_MODELS),
        thinking_levels=list(SUPPORTED_THINKING_LEVELS),
        limits=request.app.state.storage.get_limits_status(refresh=True),
    )
