from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import ValidationError

from ..auth import require_bridge_token
from ..event_store import EventCursorExpiredError, EventWaitCapacityError
from ..models import EventBatchRecord, EventRecord, EventScope


DEFAULT_EVENT_WAIT_SECONDS = 20.0
MAX_EVENT_WAIT_SECONDS = 30.0
MAX_EVENT_BATCH_EVENTS = 256
MAX_EVENT_THREAD_FILTERS = 64

EventScopeQuery = Literal["auth", "runtime", "thread"]

router = APIRouter()


@router.get("/events/replay", response_model=EventBatchRecord)
def replay_events(
    request: Request,
    after: Annotated[int, Query(ge=0)] = 0,
    scope: Annotated[list[EventScopeQuery] | None, Query()] = None,
    thread_id: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=MAX_EVENT_BATCH_EVENTS)] = None,
    authorization: str | None = Header(default=None),
) -> EventBatchRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    scopes, thread_ids = _normalize_filters(scope, thread_id)
    arguments: dict[str, Any] = {
        "after_cursor": after,
        "scopes": scopes,
        "thread_ids": thread_ids,
    }
    if limit is not None:
        arguments["limit"] = limit
    try:
        batch = request.app.state.event_store.replay(**arguments)
    except EventCursorExpiredError as error:
        raise _cursor_expired(error) from None
    return _project_batch(batch)


@router.get("/events/wait", response_model=EventBatchRecord)
def wait_for_events(
    request: Request,
    after: Annotated[int, Query(ge=0)] = 0,
    scope: Annotated[list[EventScopeQuery] | None, Query()] = None,
    thread_id: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=MAX_EVENT_BATCH_EVENTS)] = None,
    timeout_seconds: Annotated[
        float,
        Query(ge=0, le=MAX_EVENT_WAIT_SECONDS),
    ] = DEFAULT_EVENT_WAIT_SECONDS,
    authorization: str | None = Header(default=None),
) -> EventBatchRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    scopes, thread_ids = _normalize_filters(scope, thread_id)
    arguments: dict[str, Any] = {
        "after_cursor": after,
        "scopes": scopes,
        "thread_ids": thread_ids,
        "timeout_seconds": timeout_seconds,
    }
    if limit is not None:
        arguments["limit"] = limit
    try:
        batch = request.app.state.event_store.wait(**arguments)
    except EventCursorExpiredError as error:
        raise _cursor_expired(error) from None
    except EventWaitCapacityError:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "event_wait_capacity_exhausted",
                "retryable": True,
            },
            headers={"Retry-After": "1"},
        ) from None
    return _project_batch(batch)


def _normalize_filters(
    scopes: list[EventScopeQuery] | None,
    thread_ids: list[str] | None,
) -> tuple[tuple[str, ...] | None, tuple[str, ...] | None]:
    if scopes is not None and not scopes:
        raise _invalid_event_filter()
    normalized_scopes = tuple(dict.fromkeys(scopes)) if scopes is not None else None
    if thread_ids is None:
        normalized_thread_ids = None
    else:
        if not thread_ids or len(thread_ids) > MAX_EVENT_THREAD_FILTERS or any(
            not value
            or len(value) > 128
            or value != value.strip()
            or any(
                ord(character) < 0x20 or ord(character) == 0x7F
                for character in value
            )
            for value in thread_ids
        ):
            raise _invalid_event_filter()
        normalized_thread_ids = tuple(dict.fromkeys(thread_ids))

    if normalized_thread_ids and (
        normalized_scopes is not None
        and EventScope.THREAD.value not in normalized_scopes
    ):
        raise _invalid_event_filter()
    return normalized_scopes, normalized_thread_ids


def _invalid_event_filter() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": "invalid_event_filter"},
    )


def _project_batch(value: object) -> EventBatchRecord:
    try:
        return EventBatchRecord(
            events=[
                _project_event(event) for event in _required_value(value, "events")
            ],
            next_cursor=_required_value(value, "next_cursor"),
            minimum_cursor=_required_value(value, "minimum_cursor"),
            has_more=_required_value(value, "has_more"),
            heartbeat=_required_value(value, "heartbeat"),
        )
    except (TypeError, ValidationError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "event_projection_invalid", "retryable": True},
        ) from None


def _project_event(value: object) -> EventRecord:
    return EventRecord(
        cursor=_required_value(value, "cursor"),
        event_id=_required_value(value, "event_id"),
        scope=_required_value(value, "scope"),
        thread_id=_required_value(value, "thread_id"),
        event_type=_required_value(value, "event_type"),
        payload=_required_value(value, "payload"),
        timestamp=_required_value(value, "timestamp"),
    )


def _required_value(value: object, field: str) -> Any:
    if isinstance(value, Mapping):
        if field not in value:
            raise ValueError("missing event projection field")
        return value[field]
    try:
        return getattr(value, field)
    except AttributeError:
        raise ValueError("missing event projection field") from None


def _cursor_expired(error: EventCursorExpiredError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "event_cursor_expired",
            "retryable": False,
            "minimum_cursor": error.minimum_cursor,
            "snapshot": {
                "required": True,
                "cursor": error.snapshot_cursor,
                "scope": error.scope,
                "thread_id": error.thread_id,
            },
        },
    )
