from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from ..auth import require_bridge_token
from ..models import RunRecord
from ..runner import NoActiveRunError, ThreadBusyError
from ..readiness import evaluate_readiness
from ..storage import ThreadNotFoundError

router = APIRouter()


class PromptRequest(BaseModel):
    prompt: str
    client_request_id: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        if len(value.encode("utf-8")) > 1024 * 1024:
            raise ValueError("prompt exceeds its limit")
        return value

    @field_validator("client_request_id")
    @classmethod
    def validate_client_request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value != value.strip() or len(value.encode("utf-8")) > 256:
            raise ValueError("client request id is invalid")
        return value


@router.post(
    "/threads/{thread_id}/prompts",
    response_model=RunRecord,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_prompt(
    thread_id: str,
    payload: PromptRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> RunRecord:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    readiness = evaluate_readiness(request.app.state, include_catalogue=False)
    if readiness.state == "fatal":
        raise HTTPException(
            status_code=503,
            detail={"code": "runtime_unavailable", "reasons": readiness.reasons},
        )
    if readiness.state == "auth_required":
        raise HTTPException(
            status_code=409,
            detail={"code": "authentication_required"},
        )
    try:
        if request.app.state.storage.runtime_profile.value == "home_assistant":
            return request.app.state.runner.submit_prompt(
                thread_id,
                payload.prompt,
                client_request_id=payload.client_request_id,
            )
        return request.app.state.runner.submit_prompt(thread_id, payload.prompt)
    except ThreadBusyError as exc:
        raise HTTPException(status_code=409, detail="thread already running") from exc
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc


@router.post(
    "/threads/{thread_id}/runs/current/cancel",
    response_model=RunRecord,
)
def cancel_active_run(
    thread_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> RunRecord:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.runner.cancel_run(thread_id)
    except NoActiveRunError as exc:
        raise HTTPException(status_code=409, detail="thread is not running") from exc
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc
