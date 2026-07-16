"""Authenticated API v1 routes for durable Bridge automations."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from ..auth import require_bridge_token
from ..feature_capabilities import supports_web_search
from ..automations import (
    AutomationConflictError,
    AutomationError,
    AutomationNotFoundError,
    AutomationStore,
    AutomationValidationError,
    ScheduleValidationError,
)


class CreateAutomationRequest(BaseModel):
    name: str
    prompt: str
    target: dict[str, Any]
    schedule: dict[str, Any]
    mode: Literal["observe", "edit", "full-auto"] = "observe"
    model: str | None = Field(default=None, max_length=160)
    thinking: str | None = Field(default=None, max_length=160)


class UpdateAutomationRequest(BaseModel):
    expected_revision: int
    name: str | None = None
    prompt: str | None = None
    target: dict[str, Any] | None = None
    schedule: dict[str, Any] | None = None
    mode: Literal["observe", "edit", "full-auto"] | None = None
    model: str | None = Field(default=None, max_length=160)
    thinking: str | None = Field(default=None, max_length=160)


class RevisionRequest(BaseModel):
    expected_revision: int


class ClaimRequest(BaseModel):
    source: Literal["manual", "scheduled"] = "manual"
    due_at: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=256)
    expected_revision: int | None = None
    capacity_available: bool = True
    web_search: Literal["live", "disabled"] | None = None


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/automations")
    def list_automations(
        request: Request, authorization: str | None = Header(default=None)
    ) -> list[dict[str, Any]]:
        _authorize(request, authorization)
        return _store(request).list()

    @router.post("/automations", status_code=status.HTTP_201_CREATED)
    def create_automation(
        payload: CreateAutomationRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(request, authorization)
        return _invoke(lambda: _store(request).create(payload.model_dump()))

    @router.get("/automations/scheduler")
    def scheduler_snapshot(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        _authorize(request, authorization)
        return {"automations": _invoke(lambda: _store(request).scheduler_snapshot())}

    @router.get("/automations/{automation_id}")
    def get_automation(
        automation_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(request, authorization)
        return _invoke(lambda: _store(request).get(automation_id))

    @router.patch("/automations/{automation_id}")
    def update_automation(
        automation_id: str,
        payload: UpdateAutomationRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(request, authorization)
        changes = payload.model_dump(exclude={"expected_revision"}, exclude_unset=True)
        return _invoke(
            lambda: _store(request).update(
                automation_id, changes, expected_revision=payload.expected_revision
            )
        )

    @router.post("/automations/{automation_id}/pause")
    def pause_automation(
        automation_id: str,
        payload: RevisionRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(request, authorization)
        return _invoke(
            lambda: _store(request).pause(
                automation_id, expected_revision=payload.expected_revision
            )
        )

    @router.post("/automations/{automation_id}/resume")
    def resume_automation(
        automation_id: str,
        payload: RevisionRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(request, authorization)
        return _invoke(
            lambda: _store(request).resume(
                automation_id, expected_revision=payload.expected_revision
            )
        )

    @router.delete(
        "/automations/{automation_id}", status_code=status.HTTP_204_NO_CONTENT
    )
    def delete_automation(
        automation_id: str,
        payload: RevisionRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Response:
        _authorize(request, authorization)
        _invoke(
            lambda: _store(request).delete(
                automation_id, expected_revision=payload.expected_revision
            )
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post(
        "/automations/{automation_id}/runs", status_code=status.HTTP_202_ACCEPTED
    )
    def claim_run(
        automation_id: str,
        payload: ClaimRequest,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _authorize(request, authorization)
        if payload.web_search is not None and not supports_web_search(request.app.state):
            raise _web_search_unavailable()
        store = _store(request)
        capacity_available = payload.capacity_available and _runtime_capacity_available(
            request
        )
        if payload.source == "scheduled":
            if (
                payload.due_at is None
                or payload.idempotency_key is None
                or payload.expected_revision is None
            ):
                raise _invalid()
            claim = _invoke(
                lambda: store.claim(
                    automation_id,
                    due_at=payload.due_at or "",
                    idempotency_key=payload.idempotency_key or "",
                    expected_revision=payload.expected_revision
                    if payload.expected_revision is not None
                    else -1,
                    capacity_available=capacity_available,
                    web_search=payload.web_search,
                )
            )
        else:
            claim = _invoke(
                lambda: store.run_now(
                    automation_id,
                    capacity_available=capacity_available,
                    web_search=payload.web_search,
                )
            )
        dispatcher = getattr(request.app.state, "automation_dispatch", None)
        if claim["dispatchable"] and callable(dispatcher):
            try:
                dispatcher(claim)
            except Exception:
                claim = store.complete(
                    claim["automation_run_id"],
                    status="blocked",
                    error="automation dispatcher rejected the claim",
                )
        return claim

    @router.get("/automations/{automation_id}/runs")
    def list_runs(
        automation_id: str,
        request: Request,
        limit: int = 100,
        authorization: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        _authorize(request, authorization)
        return _invoke(lambda: _store(request).list_runs(automation_id, limit=limit))

    return router


router = create_router()


def _store(request: Request) -> AutomationStore:
    store = getattr(request.app.state, "automations", None)
    if not isinstance(store, AutomationStore):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "automations_unavailable", "retryable": True},
        )
    return store


def _authorize(request: Request, authorization: str | None) -> None:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )


def _runtime_capacity_available(request: Request) -> bool:
    gate = getattr(request.app.state, "runtime_gate", None)
    if gate is None:
        return True
    try:
        snapshot = gate.snapshot()
        limits = gate.limits
        if (
            snapshot.closed
            or snapshot.auth_mutation_active
            or snapshot.config_mutation_active
        ):
            return False
        if snapshot.active_turns < limits.max_active_turns:
            return True
        return snapshot.queued_prompts < limits.max_queued_prompts
    except (AttributeError, TypeError):
        return False


def _invoke(action):
    try:
        return action()
    except AutomationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "automation_not_found", "retryable": False},
        ) from None
    except AutomationConflictError as error:
        code = (
            "automation_revision_conflict"
            if "revision" in str(error)
            else "automation_conflict"
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": code, "retryable": False},
        ) from None
    except (ScheduleValidationError, AutomationValidationError):
        raise _invalid() from None
    except AutomationError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "automation_error", "retryable": True},
        ) from None


def _invalid() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": "automation_invalid", "retryable": False},
    )


def _web_search_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": "capabilities_unavailable", "retryable": False},
    )
