from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request
from pydantic import ValidationError

from ..auth import require_bridge_token
from ..models import (
    InteractionAnswerRequest,
    InteractionDecisionRequest,
    InteractionResultRecord,
    PendingInteractionCollectionRecord,
    PendingInteractionRecord,
)

router = APIRouter()


@router.get(
    "/interactions/pending",
    response_model=PendingInteractionCollectionRecord,
    response_model_exclude_unset=True,
)
def list_pending_interactions(
    request: Request,
    thread_id: str | None = Query(default=None, min_length=1, max_length=128),
    authorization: str | None = Header(default=None),
) -> PendingInteractionCollectionRecord:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    broker = request.app.state.runner
    interactions = broker.list_pending_interactions(thread_id=thread_id)
    try:
        items = [PendingInteractionRecord.model_validate(item) for item in interactions]
    except ValidationError:
        raise _projection_error() from None
    return PendingInteractionCollectionRecord(
        items=items,
        count=len(items),
        thread_id=thread_id,
    )


@router.post(
    "/interactions/{interaction_id}/decision",
    response_model=InteractionResultRecord,
)
def decide_interaction(
    interaction_id: Annotated[str, Path(min_length=1, max_length=128)],
    payload: InteractionDecisionRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> InteractionResultRecord:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    result = request.app.state.runner.decide_approval(
        interaction_id,
        thread_id=payload.thread_id,
        run_id=payload.run_id,
        turn_id=payload.turn_id,
        item_id=payload.item_id,
        decision=payload.decision,
        client_request_id=payload.client_request_id,
    )
    try:
        return InteractionResultRecord.model_validate(result)
    except ValidationError:
        raise _projection_error() from None


@router.post(
    "/interactions/{interaction_id}/answer",
    response_model=InteractionResultRecord,
)
def answer_interaction(
    interaction_id: Annotated[str, Path(min_length=1, max_length=128)],
    payload: InteractionAnswerRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> InteractionResultRecord:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    result = request.app.state.runner.answer_user_input(
        interaction_id,
        thread_id=payload.thread_id,
        run_id=payload.run_id,
        turn_id=payload.turn_id,
        item_id=payload.item_id,
        answers=[answer.model_dump() for answer in payload.answers],
        client_request_id=payload.client_request_id,
    )
    try:
        return InteractionResultRecord.model_validate(result)
    except ValidationError:
        raise _projection_error() from None


def _projection_error() -> HTTPException:
    return HTTPException(
        status_code=500,
        detail={"code": "runtime_projection_invalid", "retryable": False},
    )
