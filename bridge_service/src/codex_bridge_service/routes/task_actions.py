"""Private, idempotent admission for native Home Assistant task actions."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from ..auth import require_bridge_token
from ..feature_capabilities import supports_web_search
from ..model_catalog import ModelCatalogError
from ..models import RunMode, RuntimeProfile
from ..readiness import evaluate_readiness
from ..runtime_broker import RuntimeBroker, TurnChangedError
from ..storage import (
    ProjectMutationError,
    ProjectNotFoundError,
    TaskActionConflictError,
    ThreadNotFoundError,
)

router = APIRouter()

_IDENTIFIER = r"^[a-zA-Z0-9_-]{1,128}$"
_ACTION_ID = r"^[a-f0-9]{32}$"


class StartTaskRequest(BaseModel):
    task_id: str = Field(pattern=_ACTION_ID)
    project_id: str = Field(pattern=_IDENTIFIER)
    title: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=65_536)
    mode: Literal["observe", "edit", "full-auto"] = "observe"
    model_override: str | None = Field(default=None, max_length=160)
    thinking_override: str | None = Field(default=None, max_length=160)
    web_search: Literal["live", "disabled"] | None = None

    @field_validator("title", "prompt", "model_override", "thinking_override")
    @classmethod
    def nonblank(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.strip() or len(value.encode("utf-8")) > 65_536
        ):
            raise ValueError("task action text is invalid")
        return value


class ContinueTaskRequest(BaseModel):
    task_id: str = Field(pattern=_ACTION_ID)
    thread_id: str = Field(pattern=_IDENTIFIER)
    prompt: str = Field(min_length=1, max_length=65_536)
    web_search: Literal["live", "disabled"] | None = None

    @field_validator("prompt")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip() or len(value.encode("utf-8")) > 65_536:
            raise ValueError("task action prompt is invalid")
        return value


def _authorize(request: Request, authorization: str | None) -> RuntimeBroker:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    storage = request.app.state.storage
    runner = request.app.state.runner
    if storage.runtime_profile is not RuntimeProfile.HOME_ASSISTANT or not isinstance(
        runner, RuntimeBroker
    ):
        raise HTTPException(
            503, detail={"code": "task_actions_unavailable", "retryable": True}
        )
    return runner


def _require_ready(request: Request, web_search: str | None) -> None:
    readiness = evaluate_readiness(request.app.state, include_catalogue=False)
    if readiness.state == "auth_required":
        raise HTTPException(
            409, detail={"code": "authentication_required", "retryable": False}
        )
    if readiness.state == "fatal":
        raise HTTPException(
            503, detail={"code": "runtime_unavailable", "retryable": True}
        )
    if web_search is not None and not supports_web_search(request.app.state):
        raise HTTPException(
            422, detail={"code": "capabilities_unavailable", "retryable": False}
        )


def _record(task_id: str, run) -> dict[str, str]:
    return {
        "task_id": task_id,
        "thread_id": run.thread_id,
        "run_id": run.run_id,
        "status": run.status,
    }


def _accepted(request: Request, task_id: str, run) -> dict[str, str]:
    request.app.state.storage.event_store.append(
        operation_key=f"task:{task_id}:accepted",
        scope="thread",
        thread_id=run.thread_id,
        event_type="task.accepted",
        payload={"task_id": task_id, "run_id": run.run_id, "status": "accepted"},
    )
    return _record(task_id, run)


def _target_error(error: Exception) -> HTTPException:
    if isinstance(error, (ProjectNotFoundError, ThreadNotFoundError)):
        return HTTPException(
            404, detail={"code": "task_target_not_found", "retryable": False}
        )
    return HTTPException(
        409, detail={"code": "task_target_unavailable", "retryable": False}
    )


@router.post("/task-actions/start", status_code=status.HTTP_202_ACCEPTED)
def start_task(
    payload: StartTaskRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    runner = _authorize(request, authorization)
    storage = request.app.state.storage
    fingerprint = hashlib.sha256(
        json.dumps(
            payload.model_dump(exclude={"task_id"}),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    previous = runner.get_task_action_run(payload.task_id)
    if previous is not None:
        try:
            thread = storage.load_thread(previous.thread_id)
        except ThreadNotFoundError as error:
            raise _target_error(error) from None
        if (
            thread.thread_id != f"thr_task_{payload.task_id}"
            or thread.task_action_fingerprint != fingerprint
        ):
            raise HTTPException(
                409, detail={"code": "task_retry_conflict", "retryable": False}
            )
        return _accepted(request, payload.task_id, previous)

    _require_ready(request, payload.web_search)
    try:
        project = storage.load_project(payload.project_id)
        if project.archived_at is not None:
            raise ProjectMutationError("task project is archived")
    except (ProjectNotFoundError, ProjectMutationError) as error:
        raise _target_error(error) from None

    # Explicit model/effort choices must be in the installed provider catalogue.
    if payload.model_override is not None or payload.thinking_override is not None:
        try:
            catalogue = request.app.state.model_catalog_probe.probe()
        except ModelCatalogError:
            raise HTTPException(
                503, detail={"code": "runtime_unavailable", "retryable": True}
            ) from None
        if catalogue.stale:
            raise HTTPException(
                503, detail={"code": "runtime_unavailable", "retryable": True}
            )
        model = payload.model_override or project.default_model
        supported = next(
            (item for item in catalogue.models if item.model == model), None
        )
        effective_thinking = payload.thinking_override or project.default_thinking_level
        if supported is None or effective_thinking not in supported.thinking_levels:
            raise HTTPException(
                422, detail={"code": "task_model_unavailable", "retryable": False}
            )

    request_id = f"ha-action:{payload.task_id}"
    with runner.admit_prompt(
        payload.prompt,
        client_request_id=request_id,
        unattended=True,
        web_search=payload.web_search,
    ) as admission:
        if admission.replay_run is not None:
            run = admission.replay_run
            try:
                thread = storage.load_thread(run.thread_id)
            except ThreadNotFoundError as error:
                raise _target_error(error) from None
            if (
                thread.thread_id != f"thr_task_{payload.task_id}"
                or thread.task_action_fingerprint != fingerprint
            ):
                raise HTTPException(
                    409, detail={"code": "task_retry_conflict", "retryable": False}
                )
        else:
            try:
                with storage.prepare_task_thread(
                    action_id=payload.task_id,
                    fingerprint=fingerprint,
                    title=payload.title,
                    project_id=payload.project_id,
                    mode=RunMode(payload.mode),
                    model_override=payload.model_override,
                    thinking_override=payload.thinking_override,
                ) as thread:
                    run = runner.submit_prompt(
                        thread.thread_id,
                        payload.prompt,
                        client_request_id=request_id,
                        unattended=True,
                        web_search=payload.web_search,
                        admission=admission,
                    )
            except (ProjectNotFoundError, ProjectMutationError) as error:
                raise _target_error(error) from None
            except TaskActionConflictError:
                raise HTTPException(
                    409, detail={"code": "task_retry_conflict", "retryable": False}
                ) from None
    return _accepted(request, payload.task_id, run)


@router.post("/task-actions/continue", status_code=status.HTTP_202_ACCEPTED)
def continue_task(
    payload: ContinueTaskRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    runner = _authorize(request, authorization)
    if runner.get_task_action_run(payload.task_id) is None:
        _require_ready(request, payload.web_search)
    storage = request.app.state.storage
    request_id = f"ha-action:{payload.task_id}"
    with runner.admit_prompt(
        payload.prompt,
        client_request_id=request_id,
        unattended=True,
        web_search=payload.web_search,
    ) as admission:
        if admission.replay_run is not None:
            run = admission.replay_run
            if run.thread_id != payload.thread_id:
                raise HTTPException(
                    409, detail={"code": "task_retry_conflict", "retryable": False}
                )
        else:
            try:
                with storage.reserve_task_thread(payload.thread_id) as thread:
                    if thread.mode is RunMode.HAOS_FULL_ACCESS:
                        raise HTTPException(
                            409,
                            detail={
                                "code": "task_host_access_denied",
                                "retryable": False,
                            },
                        )
                    run = runner.submit_prompt(
                        thread.thread_id,
                        payload.prompt,
                        client_request_id=request_id,
                        unattended=True,
                        web_search=payload.web_search,
                        admission=admission,
                    )
            except (
                ProjectNotFoundError,
                ThreadNotFoundError,
                ProjectMutationError,
            ) as error:
                raise _target_error(error) from None
    return _accepted(request, payload.task_id, run)


@router.get("/task-actions/{task_id}")
def get_task(
    task_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    runner = _authorize(request, authorization)
    if len(task_id) != 32 or any(char not in "0123456789abcdef" for char in task_id):
        raise HTTPException(422, detail={"code": "task_id_invalid", "retryable": False})
    run = runner.get_task_action_run(task_id)
    if run is None:
        raise HTTPException(404, detail={"code": "task_not_found", "retryable": False})
    return _record(task_id, run)


@router.post("/task-actions/{task_id}/cancel")
def cancel_task(
    task_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    runner = _authorize(request, authorization)
    if len(task_id) != 32 or any(char not in "0123456789abcdef" for char in task_id):
        raise HTTPException(422, detail={"code": "task_id_invalid", "retryable": False})
    run = runner.get_task_action_run(task_id)
    if run is None:
        raise HTTPException(404, detail={"code": "task_not_found", "retryable": False})
    if run.status in {"completed", "failed", "cancelled", "interrupted"}:
        return _record(task_id, run)
    try:
        cancelled = runner.cancel_run(run.thread_id, run_id=run.run_id)
    except TurnChangedError:
        current = runner.get_task_action_run(task_id)
        if current is None or current.status not in {
            "completed",
            "failed",
            "cancelled",
            "interrupted",
        }:
            raise
        cancelled = current
    return _record(task_id, cancelled)
