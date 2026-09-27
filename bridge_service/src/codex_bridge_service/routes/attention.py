"""Bounded operational attention projection for Home Assistant chats."""

from __future__ import annotations

import sqlite3
from itertools import islice
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from ..auth import require_bridge_token
from ..event_store import EventStoreError
from ..models import PendingInteractionRecord, RuntimeProfile
from ..runtime_broker import RuntimeBrokerError
from ..storage import ThreadNotFoundError
from ..workspace import WorkspaceBoundaryError

router = APIRouter()

_SOURCE_LIMIT = 200
_MAX_RESPONSE_ITEMS = 400


class AttentionItem(BaseModel):
    """Safe navigation row; contains no prompt, tool output or private URL."""

    attention_id: str = Field(min_length=1, max_length=512)
    kind: Literal["interaction", "failure", "review"]
    thread_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    project_name: str = Field(min_length=1, max_length=160)
    chat_title: str = Field(min_length=1, max_length=256)
    label: Literal[
        "Approval needed",
        "Response needed",
        "Service response needed",
        "Service connection needs attention",
        "Run failed",
        "Completed turn ready to review",
    ]
    interaction_kind: Literal[
        "command_approval", "file_change_approval", "user_input", "mcp_form", "mcp_url"
    ] | None = None
    timestamp: str | None = Field(default=None, max_length=64)
    expires_at: str | None = Field(default=None, max_length=64)
    archived: bool = False


class AttentionInbox(BaseModel):
    items: list[AttentionItem] = Field(max_length=_MAX_RESPONSE_ITEMS)
    truncated: bool


def _interaction_label(kind: str) -> str:
    return {
        "command_approval": "Approval needed",
        "file_change_approval": "Approval needed",
        "user_input": "Response needed",
        "mcp_form": "Service response needed",
        "mcp_url": "Service connection needs attention",
    }[kind]


def _thread_labels(storage, thread_id: str, cache: dict[str, tuple]) -> tuple | None:
    """Resolve human labels and archive state only for bounded candidate rows."""
    if thread_id in cache:
        return cache[thread_id]
    try:
        thread = storage.get_thread(thread_id)
        if not thread.project_id:
            return None
        project = storage.load_project(thread.project_id)
    except (ThreadNotFoundError, FileNotFoundError, WorkspaceBoundaryError):
        # Deletion purges the event thread and removes its record. A pending
        # broker entry can race that deletion, so never emit an orphan row.
        return None
    labels = (
        thread.project_id,
        project.name,
        thread.title,
        bool(thread.archived_at or project.archived_at),
    )
    cache[thread_id] = labels
    return labels


@router.get("/attention", response_model=AttentionInbox)
def get_attention_inbox(
    request: Request,
    authorization: str | None = Header(default=None),
) -> AttentionInbox:
    """Project current interactions and latest run outcomes across projects.

    Archived chats/projects remain visible and are marked `archived`; this
    avoids hiding unresolved broker items or completed turns merely because a
    chat was archived. Opening or reading a chat does not mutate inbox state.
    """
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    storage = request.app.state.storage
    broker = request.app.state.runner
    if (
        storage.runtime_profile is not RuntimeProfile.HOME_ASSISTANT
        or not callable(getattr(broker, "list_pending_interactions", None))
    ):
        raise HTTPException(
            status_code=503,
            detail={"code": "attention_unavailable", "retryable": True},
        )

    try:
        # Exactly one global broker read; never fan out pending queries by chat.
        raw_interactions = broker.list_pending_interactions()
        bounded_interactions = list(islice(iter(raw_interactions), _SOURCE_LIMIT + 1))
        lifecycle = storage.event_store.attention_lifecycle(limit=_SOURCE_LIMIT)
    except (RuntimeBrokerError, EventStoreError, sqlite3.Error, TypeError):
        raise HTTPException(
            status_code=503,
            detail={"code": "attention_unavailable", "retryable": True},
        ) from None

    truncated = lifecycle.has_more
    interactions: list[PendingInteractionRecord] = []
    if len(bounded_interactions) > _SOURCE_LIMIT:
        truncated = True
        bounded_interactions = bounded_interactions[:_SOURCE_LIMIT]
    for raw in bounded_interactions:
        raw_status = raw.get("status") if isinstance(raw, dict) else getattr(raw, "status", None)
        if raw_status != "pending":
            continue
        try:
            interaction = PendingInteractionRecord.model_validate(raw)
        except (ValidationError, TypeError, ValueError):
            raise HTTPException(
                status_code=503,
                detail={"code": "attention_unavailable", "retryable": True},
            ) from None
        if interaction.status != "pending":
            continue
        interactions.append(interaction)
    interactions.sort(key=lambda item: (item.event_id, item.interaction_id))

    rows: list[tuple[int, AttentionItem]] = []
    labels_cache: dict[str, tuple] = {}
    for interaction in interactions:
        labels = _thread_labels(storage, interaction.thread_id, labels_cache)
        if labels is None:
            continue
        project_id, project_name, chat_title, archived = labels
        rows.append(
            (
                interaction.event_id,
                AttentionItem(
                    attention_id=(
                        f"interaction:{interaction.thread_id}:{interaction.interaction_id}"
                    ),
                    kind="interaction",
                    thread_id=interaction.thread_id,
                    project_id=project_id,
                    project_name=project_name,
                    chat_title=chat_title,
                    label=_interaction_label(interaction.kind),
                    interaction_kind=interaction.kind,
                    expires_at=interaction.expires_at,
                    archived=archived,
                ),
            )
        )

    for event in lifecycle.items:
        labels = _thread_labels(storage, event.thread_id, labels_cache)
        if labels is None:
            continue
        project_id, project_name, chat_title, archived = labels
        ready = event.event_type == "run.completed"
        rows.append(
            (
                event.cursor,
                AttentionItem(
                    attention_id=(
                        f"run:{event.thread_id}:{event.run_id}:"
                        f"{'review' if ready else 'failure'}"
                    ),
                    kind="review" if ready else "failure",
                    thread_id=event.thread_id,
                    project_id=project_id,
                    project_name=project_name,
                    chat_title=chat_title,
                    label=(
                        "Completed turn ready to review" if ready else "Run failed"
                    ),
                    timestamp=event.timestamp,
                    archived=archived,
                ),
            )
        )

    rows.sort(key=lambda item: item[0], reverse=True)
    if len(rows) > _MAX_RESPONSE_ITEMS:
        truncated = True
        rows = rows[:_MAX_RESPONSE_ITEMS]
    return AttentionInbox(
        items=[item for _order, item in rows],
        truncated=truncated,
    )
