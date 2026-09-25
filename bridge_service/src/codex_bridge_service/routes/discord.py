"""Authenticated, write-only Discord administration for the HA App."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..auth import require_bridge_token
from ..discord_channel import DiscordChannelError, DiscordChannelManager

router = APIRouter()


class GuildRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    guild_id: str = Field(min_length=17, max_length=20)
    channel_ids: list[str] = Field(max_length=32)
    user_ids: list[str] = Field(max_length=32)


class DiscordConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool
    dm_user_ids: list[str] = Field(max_length=32)
    guilds: list[GuildRule] = Field(max_length=16)
    bot_token: str | None = Field(
        default=None, min_length=30, max_length=200, repr=False
    )


def _manager(request: Request, authorization: str | None) -> DiscordChannelManager:
    require_bridge_token(
        authorization=authorization,
        request=request,
        expected_token=request.app.state.auth_token,
    )
    manager = getattr(request.app.state, "discord_channel", None)
    if not isinstance(manager, DiscordChannelManager):
        raise HTTPException(503, detail={"code": "discord_unavailable"})
    return manager


@router.get("/discord/config")
def get_config(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    return _manager(request, authorization).status()


@router.put("/discord/config")
async def put_config(
    payload: DiscordConfiguration,
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    manager = _manager(request, authorization)
    try:
        return await manager.configure(
            payload.model_dump(exclude={"bot_token"}), payload.bot_token
        )
    except DiscordChannelError:
        raise HTTPException(400, detail={"code": "discord_config_invalid"}) from None


@router.post("/discord/revoke")
async def revoke(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    return await _manager(request, authorization).revoke()
