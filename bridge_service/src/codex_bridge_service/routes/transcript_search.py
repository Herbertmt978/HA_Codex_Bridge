"""Bounded search over the public, durable transcript event projection."""

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..auth import require_bridge_token
from ..storage import ThreadNotFoundError
from ..workspace import WorkspaceBoundaryError, WorkspaceNotFoundError

router = APIRouter()


class TranscriptSearchResult(BaseModel):
    thread_id: str
    title: str
    archived_at: str | None
    role: str
    sequence: int
    excerpt: str = Field(max_length=320)


class TranscriptSearchResponse(BaseModel):
    results: list[TranscriptSearchResult]
    has_more: bool
    next_cursor: int | None
    complete: bool
    oldest_indexed_cursor: int | None
    maximum_text_bytes: int
    maximum_messages: int


class TranscriptMessage(BaseModel):
    thread_id: str
    sequence: int
    role: str
    text: str
    timestamp: str


def _excerpt(text: str, query: str, *, maximum: int = 240) -> str:
    folded = text.casefold()
    start = folded.find(query.casefold())
    if start < 0:
        return text[:maximum]
    left = max(0, start - maximum // 3)
    right = min(len(text), left + maximum)
    left = max(0, right - maximum)
    excerpt = text[left:right].strip()
    if left:
        excerpt = "…" + excerpt
    if right < len(text):
        excerpt += "…"
    return excerpt


@router.get("/search/transcript", response_model=TranscriptSearchResponse)
def search_transcript(
    request: Request,
    q: str = Query(min_length=1, max_length=256),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
    before_cursor: int | None = Query(default=None, ge=1, le=9_007_199_254_740_991),
    authorization: str | None = Header(default=None),
) -> TranscriptSearchResponse:
    """Search only user/assistant message text from the durable event log."""
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    query = q.strip()
    if not query:
        raise HTTPException(status_code=422, detail="query must not be blank")

    storage = request.app.state.storage
    try:
        # Current thread metadata supplies the archive filter and prevents any
        # result for a chat removed concurrently with a previous page.
        threads = storage.list_threads(include_archived=include_archived)
        thread_by_id = {thread.thread_id: thread for thread in threads}
        messages = storage.event_store.search_transcript_messages(
            query=query,
            thread_ids=tuple(thread_by_id),
            before_cursor=before_cursor,
            limit=limit + 1,
        )
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workspace path not found") from exc
    except WorkspaceBoundaryError as exc:
        raise HTTPException(status_code=400, detail="invalid workspace path") from exc
    has_more = len(messages) > limit
    page = messages[:limit]
    results = [
        TranscriptSearchResult(
            thread_id=message["thread_id"],
            title=thread_by_id[message["thread_id"]].title,
            archived_at=thread_by_id[message["thread_id"]].archived_at,
            role=message["role"],
            sequence=message["scope_sequence"],
            excerpt=_excerpt(message["text"], query),
        )
        for message in page
        if message["thread_id"] in thread_by_id
    ]
    return TranscriptSearchResponse(
        results=results,
        has_more=has_more,
        next_cursor=page[-1]["cursor"] if has_more and page else None,
        **storage.event_store.transcript_index_status(),
    )


@router.get(
    "/threads/{thread_id}/transcript/{sequence}", response_model=TranscriptMessage
)
def get_transcript_message(
    thread_id: str,
    sequence: int,
    request: Request,
    authorization: str | None = Header(default=None),
) -> TranscriptMessage:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    try:
        request.app.state.storage.load_thread(thread_id)
        message = request.app.state.storage.event_store.get_transcript_message(
            thread_id, sequence
        )
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workspace path not found") from exc
    except WorkspaceBoundaryError as exc:
        raise HTTPException(status_code=400, detail="invalid workspace path") from exc
    if message is None:
        raise HTTPException(status_code=404, detail="transcript message not found")
    return TranscriptMessage(
        thread_id=message["thread_id"],
        sequence=message["scope_sequence"],
        role=message["role"],
        text=message["text"],
        timestamp=message["timestamp"],
    )
