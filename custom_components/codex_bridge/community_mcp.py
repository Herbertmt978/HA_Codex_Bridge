"""Administrator HTTP quick connect for an existing community Supervisor App."""

from __future__ import annotations

import asyncio
import hmac
import json
import re

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.exceptions import Unauthorized

from .bridge_api import BridgeApiError
from .community_mcp_discovery import CommunityMcpDiscoveryError, async_discover_community_mcp
from .const import CONNECTION_TYPE_SUPERVISOR
from .runtime import async_get_runtime

_STATES = {"not_connected", "not_installed", "configured", "paused", "unavailable", "ambiguous", "stopped",
           "unsupported", "endpoint_unavailable", "secret_unavailable", "connection_changed",
           "retry", "enable_mcp", "restart_required"}
_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_REVISION = re.compile(r"[a-f0-9]{64}\Z")


def _public(state: str, *, endpoint=None, server_name: str | None = None,
            reused: bool = False) -> dict:
    """Project only known non-secret discovery fields and fixed state codes."""
    return {"state": state if state in _STATES else "unavailable",
            "server_name": server_name, "reused": reused,
            "destination": endpoint.public_destination if endpoint else None,
            "version": endpoint.version if endpoint else None,
            "consent_revision": endpoint.consent_revision if endpoint else None}


class CodexBridgeCommunityMcpView(HomeAssistantView):
    url = "/api/codex_bridge/mcp/community"
    name = "api:codex_bridge:community_mcp"
    requires_auth = True

    def __init__(self, hass) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        return await self._handle(request, connect=False)

    async def post(self, request: web.Request) -> web.Response:
        return await self._handle(request, connect=True)

    async def _handle(self, request: web.Request, *, connect: bool) -> web.Response:
        user = request.get("hass_user")
        if user is None or user.is_admin is not True or user.is_active is not True:
            raise Unauthorized()
        headers = {"Cache-Control": "no-store"}
        try:
            revision = None
            if connect:
                if request.content_type != "application/json":
                    raise ValueError()
                body = bytearray()
                async with asyncio.timeout(15):
                    async for chunk in request.content.iter_chunked(512):
                        body.extend(chunk)
                        if len(body) > 512:
                            raise ValueError()
                payload = json.loads(body)
                if (not isinstance(payload, dict)
                        or set(payload) != {"acknowledged", "consent_revision"}
                        or payload["acknowledged"] is not True
                        or not isinstance(payload["consent_revision"], str)
                        or _REVISION.fullmatch(payload["consent_revision"]) is None):
                    raise ValueError()
                revision = payload["consent_revision"]
                if await self.hass.auth.async_get_user(user.id) is not user:
                    raise Unauthorized()
            runtime = async_get_runtime(self.hass)
            if runtime.connection_type != CONNECTION_TYPE_SUPERVISOR:
                return web.json_response(_public("unsupported"), headers=headers)
            await runtime.async_refresh_capabilities()
            if not runtime.supports_capability("community_mcp_quick_connect_v1"):
                return web.json_response(_public("enable_mcp"), headers=headers)
            endpoint = await async_discover_community_mcp(self.hass)
            if connect and not hmac.compare_digest(revision, endpoint.consent_revision):
                return web.json_response(_public("connection_changed", endpoint=endpoint),
                                         status=409, headers=headers)
            result = await runtime.client.async_community_mcp(
                name=endpoint.name, url=endpoint.url, connect=connect)
            if (not isinstance(result, dict) or result.get("state") not in {"not_connected", "configured", "paused"}
                    or type(result.get("reused")) is not bool
                    or (result.get("server_name") is not None and (
                        not isinstance(result["server_name"], str) or _NAME.fullmatch(result["server_name"]) is None))):
                raise ValueError()
            return web.json_response(_public(result["state"], endpoint=endpoint,
                server_name=result["server_name"], reused=result["reused"]), headers=headers)
        except CommunityMcpDiscoveryError as error:
            return web.json_response(_public(error.code), status=409 if connect else 200, headers=headers)
        except BridgeApiError as error:
            code = "restart_required" if error.code == "mcp_restart_required" else "connection_changed" if error.code == "community_mcp_connection_changed" else (
                "retry" if error.code in {"mcp_config_conflict", "runtime_mutation_conflict"} else "unavailable")
            return web.json_response(_public(code), status=409, headers=headers)
        except (ValueError, TypeError, KeyError, UnicodeError, asyncio.TimeoutError):
            return web.json_response(_public("unavailable"), status=400, headers=headers)
        except RuntimeError:
            return web.json_response(_public("unavailable"), status=503, headers=headers)
