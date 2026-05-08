from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, field_validator

from ..auth import require_bridge_token
from ..models import RunRecord
from ..runner import ThreadBusyError
from ..storage import ThreadNotFoundError

router = APIRouter()


class PromptRequest(BaseModel):
    prompt: str

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value


@router.post(
    "/threads/{thread_id}/prompts",
    response_model=RunRecord,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_prompt(
    thread_id: str,
    payload: PromptRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> RunRecord:
    require_bridge_token(
        authorization=authorization,
        expected_token=request.app.state.auth_token,
    )
    try:
        return request.app.state.runner.submit_prompt(thread_id, payload.prompt)
    except ThreadBusyError as exc:
        raise HTTPException(status_code=409, detail="thread already running") from exc
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail="thread not found") from exc
