import json

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth import require_bridge_token
from ..models import ThreadEventRecord
from ..storage import ThreadNotFoundError
from ..workspace import WorkspaceBoundaryError, WorkspaceNotFoundError

router = APIRouter()


@router.get("/threads/{thread_id}/events")
def stream_thread_events(
    thread_id: str,
    request: Request,
    after: int | None = None,
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    try:
        request.app.state.storage.load_thread(thread_id)
        events = request.app.state.storage.list_thread_events(thread_id, after=after)
    except ThreadNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="thread not found",
        ) from exc
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workspace path not found") from exc
    except WorkspaceBoundaryError as exc:
        raise HTTPException(status_code=400, detail="invalid workspace path") from exc

    def emit() -> str:
        for event in events:
            yield f"id: {event.sequence}\n"
            yield f"event: {event.event_type}\n"
            yield f"data: {json.dumps(event.model_dump())}\n\n"

    return StreamingResponse(emit(), media_type="text/event-stream")


@router.get("/threads/{thread_id}/events/replay", response_model=list[ThreadEventRecord])
def replay_thread_events(
    thread_id: str,
    request: Request,
    after: int | None = None,
    authorization: str | None = Header(default=None),
) -> list[ThreadEventRecord]:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    try:
        request.app.state.storage.load_thread(thread_id)
        return request.app.state.storage.list_thread_events(thread_id, after=after)
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workspace path not found") from exc
    except WorkspaceBoundaryError as exc:
        raise HTTPException(status_code=400, detail="invalid workspace path") from exc
