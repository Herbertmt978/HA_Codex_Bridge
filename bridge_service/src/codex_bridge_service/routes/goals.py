"""Administrator-authenticated manual goal controls; these never submit a turn."""

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import ValidationError

from ..auth import require_bridge_token
from ..event_store import EventStoreError
from ..goals import GoalAction, GoalError, GoalManager, GoalUnavailable, GoalView
from ..storage import ProjectNotFoundError, ThreadNotFoundError
from ..workspace import WorkspaceBoundaryError

class _GoalRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded_validation(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                # Do not reflect arbitrary objective text in validation errors.
                raise HTTPException(status_code=422, detail={"code": "goal_invalid", "retryable": False}) from None

        return bounded_validation


router = APIRouter(route_class=_GoalRoute)


def _manager(request: Request, authorization: str | None) -> GoalManager:
    require_bridge_token(authorization=authorization, request=request, expected_token=request.app.state.auth_token)
    manager = getattr(request.app.state.storage, "goals", None)
    if "durable_goals_v1" not in getattr(request.app.state, "feature_capabilities", ()) or not isinstance(manager, GoalManager):
        raise HTTPException(status_code=503, detail={"code": "goals_unavailable", "retryable": False})
    return manager


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, (ThreadNotFoundError, ProjectNotFoundError)):
        return HTTPException(status_code=404, detail={"code": "goal_chat_not_found"})
    if isinstance(exc, GoalError):
        return HTTPException(status_code=503 if isinstance(exc, GoalUnavailable) else 409,
                             detail={"code": exc.code, "retryable": False})
    if isinstance(exc, ValidationError):
        return HTTPException(status_code=422, detail={"code": "goal_invalid", "retryable": False})
    return HTTPException(status_code=503, detail={"code": "goals_unavailable", "retryable": False})


@router.get("/threads/{thread_id}/goal", response_model=GoalView)
def get_goal(thread_id: str, request: Request, authorization: str | None = Header(default=None)) -> GoalView:
    manager = _manager(request, authorization)
    try:
        return manager.get(thread_id)
    except (GoalError, ThreadNotFoundError, ProjectNotFoundError, EventStoreError, WorkspaceBoundaryError, OSError) as exc:
        raise _error(exc) from None


@router.post("/threads/{thread_id}/goal/actions", response_model=GoalView)
def act_on_goal(thread_id: str, payload: GoalAction, request: Request,
                authorization: str | None = Header(default=None)) -> GoalView:
    manager = _manager(request, authorization)
    try:
        return manager.apply(thread_id, payload)
    except (GoalError, ValidationError, ThreadNotFoundError, ProjectNotFoundError,
            EventStoreError, WorkspaceBoundaryError, OSError) as exc:
        raise _error(exc) from None
