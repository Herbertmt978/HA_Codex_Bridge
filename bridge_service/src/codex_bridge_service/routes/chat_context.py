"""Private Bridge endpoints for deliberate destination-scoped chat context."""

from fastapi import APIRouter, Header, HTTPException, Query, Request

from ..auth import require_bridge_token
from ..event_store import EventStoreError
from ..chat_context import (
    ChatContextAttachment, ChatContextError, ChatContextSelection,
    _safe_message, access_pair, preview_chat_context,
)

router = APIRouter()


def _storage(request: Request, authorization: str | None):
    require_bridge_token(authorization=authorization, request=request, expected_token=request.app.state.auth_token)
    if "chat_context_v1" not in getattr(request.app.state, "feature_capabilities", ()):
        raise HTTPException(status_code=422, detail={"code": "capabilities_unavailable", "capability": "chat_context_v1"})
    return request.app.state.storage


def context_http_error(error: ChatContextError) -> HTTPException:
    return HTTPException(status_code=413 if error.state == "limit_exceeded" else 409, detail=error.public_detail())


@router.get("/threads/{thread_id}/chat-context/{source_thread_id}")
def list_chat_context(
    thread_id: str, source_thread_id: str, request: Request,
    limit: int = Query(default=20, ge=1, le=50),
    before_sequence: int | None = Query(default=None, ge=1, le=9_007_199_254_740_991),
    authorization: str | None = Header(default=None),
) -> dict:
    storage = _storage(request, authorization)
    try:
        _, _, source, _ = access_pair(storage, thread_id, source_thread_id)
        page, total, coverage = storage.event_store.list_conversation_messages(source_thread_id, before_sequence=before_sequence, limit=limit + 1)
        # Never return hidden/credential-bearing records even if an old index drifted.
        messages = [_safe_message(item) for item in page[:limit]]
        return {
            "title": source.title[:512], "messages": [
                {"message_sequence": item["scope_sequence"], "role": item["role"],
                 "excerpt": item["text"][:240], "characters": len(item["text"])} for item in messages
            ],
            "has_more": len(page) > limit,
            "next_before_sequence": messages[-1]["scope_sequence"] if len(page) > limit and messages else None,
            "complete": coverage["complete"], "total_messages": total,
            "coverage": "currently_retained_public_only",
        }
    except ChatContextError as error:
        raise context_http_error(error) from None
    except EventStoreError:
        raise context_http_error(ChatContextError("unavailable")) from None


@router.post("/threads/{thread_id}/chat-context/read", response_model=ChatContextAttachment)
def read_chat_context(
    thread_id: str, payload: ChatContextSelection, request: Request,
    authorization: str | None = Header(default=None),
) -> ChatContextAttachment:
    storage = _storage(request, authorization)
    try:
        return preview_chat_context(storage, thread_id, payload)
    except ChatContextError as error:
        raise context_http_error(error) from None
