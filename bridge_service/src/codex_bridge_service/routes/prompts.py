from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from ..auth import require_bridge_token
from ..feature_capabilities import supports_web_search
from ..models import QueuedPromptRecord, RunRecord
from ..runner import NoActiveRunError, ThreadBusyError
from ..runtime_broker import (
    RuntimeCollaborationModeConflictError,
    RuntimeCollaborationModeUnavailableError,
    QueuedPromptNotFoundError,
    QueuedPromptRevisionConflictError,
)
from ..readiness import evaluate_readiness
from ..storage import ThreadNotFoundError

router = APIRouter()


class PromptRequest(BaseModel):
    prompt: str
    client_request_id: str | None = Field(default=None, min_length=1, max_length=256)
    web_search: Literal["live", "disabled"] | None = None
    follow_up_mode: Literal["queue", "steer"] | None = None
    collaboration_mode: Literal["default", "plan"] | None = None

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
        if (
            value != value.strip()
            or len(value.encode("utf-8")) > 256
            or value.startswith("ha-action:")
        ):
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
    if payload.web_search is not None and not supports_web_search(request.app.state):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "capabilities_unavailable", "retryable": False},
        )
    if payload.follow_up_mode == "queue" and payload.client_request_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "client_request_id_required",
                "retryable": False,
            },
        )
    if payload.follow_up_mode is not None and "prompt_queue_v1" not in getattr(
        request.app.state, "feature_capabilities", ()
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "capabilities_unavailable",
                "capability": "prompt_queue_v1",
                "retryable": False,
            },
        )
    if payload.collaboration_mode is not None and (
        getattr(request.app.state.runner, "supports_plan_mode", False) is not True
        or "plan_mode_v1"
        not in getattr(request.app.state, "feature_capabilities", ())
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "capabilities_unavailable",
                "capability": "plan_mode_v1",
                "reason": (
                    "The active Codex app-server runtime does not advertise "
                    "native collaboration modes."
                ),
                "retryable": False,
            },
        )
    try:
        if request.app.state.storage.runtime_profile.value == "home_assistant":
            return request.app.state.runner.submit_prompt(
                thread_id,
                payload.prompt,
                client_request_id=payload.client_request_id,
                web_search=payload.web_search,
                follow_up_mode=(payload.follow_up_mode or "auto"),
                collaboration_mode=payload.collaboration_mode,
            )
        return request.app.state.runner.submit_prompt(thread_id, payload.prompt)
    except ThreadBusyError as exc:
        raise HTTPException(status_code=409, detail="thread already running") from exc
    except RuntimeCollaborationModeConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.public_detail()) from exc
    except RuntimeCollaborationModeUnavailableError as exc:
        raise HTTPException(status_code=422, detail=exc.public_detail()) from exc
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc


@router.get(
    "/threads/{thread_id}/queue",
    response_model=list[QueuedPromptRecord],
)
def list_queued_prompts(
    thread_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> list[QueuedPromptRecord]:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    runner = request.app.state.runner
    method = getattr(runner, "list_queued_prompts", None)
    if not callable(method):
        raise HTTPException(
            status_code=503,
            detail={"code": "capabilities_unavailable", "retryable": False},
        )
    try:
        return method(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc


class QueuedPromptUpdateRequest(BaseModel):
    prompt: str
    expected_revision: int = Field(ge=1, strict=True)

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        if len(value.encode("utf-8")) > 1024 * 1024:
            raise ValueError("prompt exceeds its limit")
        return value


@router.patch(
    "/threads/{thread_id}/queue/{run_id}",
    response_model=QueuedPromptRecord,
)
def update_queued_prompt(
    thread_id: str,
    run_id: str,
    payload: QueuedPromptUpdateRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> QueuedPromptRecord:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.runner.update_queued_prompt(
            thread_id,
            run_id,
            payload.prompt,
            expected_revision=payload.expected_revision,
        )
    except QueuedPromptNotFoundError as exc:
        raise HTTPException(status_code=409, detail=exc.public_detail()) from exc
    except QueuedPromptRevisionConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.public_detail()) from exc
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc


@router.delete(
    "/threads/{thread_id}/queue/{run_id}",
    response_model=RunRecord,
)
def cancel_queued_prompt(
    thread_id: str,
    run_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> RunRecord:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.runner.cancel_queued_prompt(thread_id, run_id)
    except QueuedPromptNotFoundError as exc:
        raise HTTPException(status_code=409, detail=exc.public_detail()) from exc
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
