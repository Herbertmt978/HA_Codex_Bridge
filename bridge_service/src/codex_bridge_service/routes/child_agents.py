"""Administrator-only parent-scoped genuine child inspection and controls."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..auth import require_bridge_token
from ..child_agents import ChildAgentError
from ..storage import ThreadNotFoundError

router = APIRouter()


class ChildStopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(strict=True, ge=1)
    client_request_id: str = Field(min_length=1, max_length=256)


class ChildFollowUpRequest(ChildStopRequest):
    text: str = Field(min_length=1, max_length=2000)


def _broker(request: Request, authorization: str | None):
    require_bridge_token(authorization=authorization, request=request,
                         expected_token=request.app.state.auth_token)
    broker = request.app.state.runner
    if getattr(broker, "supports_subagents", False) is not True:
        raise HTTPException(503, detail={"code": "subagents_unavailable", "retryable": False})
    return broker


def _invoke(function, *args):
    try:
        return function(*args)
    except ThreadNotFoundError:
        raise HTTPException(404, detail={"code": "thread_not_found"}) from None
    except ChildAgentError as error:
        status = 404 if error.code == "child_not_found" else 409
        if error.code == "subagents_unavailable":
            status = 503
        raise HTTPException(status, detail={"code": error.code, "retryable": False}) from None


@router.get("/threads/{thread_id}/children")
def list_children(request: Request, thread_id: str,
                  authorization: str | None = Header(default=None)):
    return _invoke(_broker(request, authorization).list_child_agents, thread_id)


@router.post("/threads/{thread_id}/children/{child_id}/refresh")
def refresh_child(request: Request, thread_id: str, child_id: str,
                  authorization: str | None = Header(default=None)):
    return _invoke(_broker(request, authorization).refresh_child_agent, thread_id, child_id)


@router.post("/threads/{thread_id}/children/{child_id}/stop")
def stop_child(request: Request, thread_id: str, child_id: str, payload: ChildStopRequest,
               authorization: str | None = Header(default=None)):
    return _invoke(_broker(request, authorization).stop_child_agent, thread_id, child_id,
                   payload.revision, payload.client_request_id)


@router.post("/threads/{thread_id}/children/{child_id}/follow-up")
def follow_up_child(request: Request, thread_id: str, child_id: str,
                    payload: ChildFollowUpRequest,
                    authorization: str | None = Header(default=None)):
    # Verify parent-scoped identity without sending text anywhere. The pinned
    # v2 runtime owns child input; there is no safe direct follow-up contract.
    result = _invoke(_broker(request, authorization).list_child_agents, thread_id)
    if not any(row["child_id"] == child_id for row in result["children"]):
        raise HTTPException(404, detail={"code": "child_not_found"})
    raise HTTPException(409, detail={"code": "child_follow_up_unavailable", "retryable": False})
