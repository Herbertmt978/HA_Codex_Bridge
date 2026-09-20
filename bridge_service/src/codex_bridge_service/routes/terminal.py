"""Authenticated terminal requests. The browser can never supply runtime options."""

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..auth import require_bridge_token
from ..terminal import TerminalError

router = APIRouter()


class TerminalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    thread_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    session_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    cols: int = Field(default=80, ge=20, le=300)
    rows: int = Field(default=20, ge=2, le=100)
    after: int = Field(default=0, ge=0, le=9_007_199_254_740_991)
    data: str = Field(default="", max_length=16 * 1024)
    sequence: int = Field(default=1, ge=1, le=9_007_199_254_740_991)


@router.post("/terminal/{operation}")
def terminal(operation: str, payload: TerminalRequest, request: Request,
             authorization: str | None = Header(default=None)):
    require_bridge_token(authorization=authorization, request=request,
                         expected_token=request.app.state.auth_token)
    manager = request.app.state.workspace_terminal
    if manager is None:
        raise HTTPException(status_code=409, detail={"code": "terminal_unavailable"})
    try:
        if operation == "open":
            return manager.open(payload.thread_id, payload.cols, payload.rows)
        if payload.session_id is None:
            raise TerminalError()
        args = (payload.session_id, payload.thread_id)
        if operation == "read":
            return manager.read(*args, payload.after)
        if operation == "write":
            return manager.write(*args, payload.data, payload.sequence)
        if operation == "resize":
            return manager.resize(*args, payload.cols, payload.rows)
        if operation == "close":
            return manager.close_session(*args)
        raise TerminalError()
    except TerminalError:
        raise HTTPException(status_code=409, detail={"code": "terminal_unavailable"}) from None
