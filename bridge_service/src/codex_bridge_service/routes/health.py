from fastapi import APIRouter, Header, Request

from ..auth import require_bridge_token

router = APIRouter()


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def readiness_check(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    return {"status": "ok"}
