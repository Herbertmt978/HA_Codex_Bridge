"""Private bounded usage history; Home Assistant remains the browser boundary."""

from fastapi import APIRouter, Header, HTTPException, Query, Request

from ..auth import require_bridge_token
from ..storage import ProjectNotFoundError, ThreadNotFoundError
from ..usage_history import UsageHistoryError

router = APIRouter()


@router.get("/usage")
def get_usage(request: Request, authorization: str | None = Header(default=None),
              thread_id: str | None = Query(default=None, max_length=128),
              project_id: str | None = Query(default=None, max_length=128)) -> dict:
    require_bridge_token(authorization=authorization, request=request,
                         expected_token=request.app.state.auth_token)
    broker = request.app.state.runner
    if "usage_history_v1" not in request.app.state.feature_capabilities:
        raise HTTPException(422, detail={"code": "capabilities_unavailable"})
    try:
        if thread_id is not None:
            thread = request.app.state.storage.load_thread(thread_id)
            if project_id is not None and thread.project_id != project_id:
                raise ThreadNotFoundError(thread_id)
        if project_id is not None:
            request.app.state.storage.load_project(project_id)
    except (ThreadNotFoundError, ProjectNotFoundError):
        raise HTTPException(404, detail={"code": "usage_target_not_found"}) from None
    try:
        view = broker.get_usage_history(thread_id=thread_id, project_id=project_id)
    except UsageHistoryError:
        raise HTTPException(503, detail={"code": "usage_history_unavailable", "retryable": True}) from None
    # Deleted chats must not reappear through retained private accounting rows.
    items = []
    for item in view["items"]:
        try:
            request.app.state.storage.load_thread(item["thread_id"])
        except ThreadNotFoundError:
            continue
        items.append(item)
    view["items"] = items
    known = [item["reported_tokens"] for item in items if item["reported_tokens"] is not None]
    view["known_reported_tokens"] = sum(known) if known else None
    view["coverage"] = "reported" if items and all(item["coverage"] == "reported" for item in items) else "partial" if known else "not_reported"
    return view
