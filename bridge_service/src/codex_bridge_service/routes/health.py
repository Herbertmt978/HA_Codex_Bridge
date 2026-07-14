from fastapi import APIRouter, Header, Request

from .. import __version__
from ..auth import require_bridge_token
from ..models import (
    BridgeReadinessRecord,
    ComponentVersionRecord,
    ImageBuildRecord,
    SandboxStatusRecord,
)
from ..readiness import evaluate_readiness

router = APIRouter()


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", response_model=BridgeReadinessRecord)
def readiness_check(
    request: Request,
    authorization: str | None = Header(default=None),
) -> BridgeReadinessRecord:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    build_info = request.app.state.build_info
    readiness = evaluate_readiness(request.app.state)
    return BridgeReadinessRecord(
        app=ComponentVersionRecord(version=build_info.app_version),
        bridge=ComponentVersionRecord(
            version=build_info.bridge_version or __version__,
        ),
        codex=ComponentVersionRecord(version=build_info.codex_version),
        image=ImageBuildRecord(
            revision=build_info.image_revision,
            release_lock_digest=build_info.release_lock_digest,
        ),
        architecture=build_info.architecture,
        sandbox=SandboxStatusRecord(
            contract_version=build_info.sandbox_contract_version,
            attested=request.app.state.sandbox_ready is True,
        ),
        readiness=readiness,
    )
