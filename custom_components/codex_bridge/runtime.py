import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from homeassistant.core import HomeAssistant

from .bridge_api import BridgeApiClient, BridgeApiError
from .const import (
    CONNECTION_TYPE_SUPERVISOR,
    DATA_ENTRIES,
    DOMAIN,
    WEB_SEARCH_CAPABILITY,
    WEB_SEARCH_MODE_DISABLED,
    WEB_SEARCH_MODE_LIVE,
)
from .event_broker import EventBroker
from .automation_scheduler import AutomationScheduler


_CAPABILITY_REFRESH_INTERVAL_SECONDS = 5.0


@dataclass(slots=True)
class CodexBridgeRuntime:
    entry_id: str
    title: str
    client: BridgeApiClient
    connection_type: str
    discovery_uuid: str | None
    api_version: int
    event_broker: EventBroker | None = None
    automation_scheduler: AutomationScheduler | None = None
    capabilities: tuple[str, ...] = ()
    web_search_mode: str = WEB_SEARCH_MODE_DISABLED
    _capability_refresh_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        init=False,
        repr=False,
    )
    _capability_refreshed_at: float = field(default=0.0, init=False, repr=False)
    _capability_auth_ready: bool | None = field(default=None, init=False, repr=False)

    def supports_capability(self, capability: str) -> bool:
        return capability in self.capabilities

    def web_search_payload(self) -> dict[str, str]:
        """Return the capability-gated Bridge field for a prompt-producing call."""

        if self.supports_capability(WEB_SEARCH_CAPABILITY):
            return {"web_search": self.web_search_mode}
        return {}

    async def async_refresh_capabilities(self, *, force: bool = False) -> bool:
        """Recover provider-backed capabilities after ChatGPT authentication."""

        if self.connection_type != CONNECTION_TYPE_SUPERVISOR:
            return False
        now = monotonic()
        if (
            not force
            and self._capability_refreshed_at
            and now - self._capability_refreshed_at
            < _CAPABILITY_REFRESH_INTERVAL_SECONDS
        ):
            return False
        async with self._capability_refresh_lock:
            now = monotonic()
            if (
                not force
                and self._capability_refreshed_at
                and now - self._capability_refreshed_at
                < _CAPABILITY_REFRESH_INTERVAL_SECONDS
            ):
                return False
            try:
                ready = await self.client.async_refresh_ready()
            except BridgeApiError:
                return False
            self.capabilities = tuple(ready.capabilities)
            self.api_version = self.client.negotiated_api_version or self.api_version
            self._capability_refreshed_at = monotonic()
            if self.automation_scheduler is not None:
                self.automation_scheduler.web_search_mode = (
                    self.web_search_mode
                    if self.supports_capability(WEB_SEARCH_CAPABILITY)
                    else None
                )
            return True

    def capability_refresh_is_urgent(self, status: object) -> bool:
        """Detect the one status transition that must bypass refresh throttling."""

        if not isinstance(status, Mapping):
            return False
        auth = status.get("auth")
        if not isinstance(auth, Mapping):
            return False
        authenticated = (
            auth.get("state") == "ok"
            and auth.get("auth_mode") == "chatgpt"
            and auth.get("auth_required") is False
        )
        was_authenticated = self._capability_auth_ready
        self._capability_auth_ready = authenticated
        return authenticated and was_authenticated is not True

    async def async_close(self) -> None:
        """Provide a lifecycle seam without taking ownership of HA's session."""

        try:
            if self.automation_scheduler is not None:
                await self.automation_scheduler.async_close()
            if self.event_broker is not None:
                await self.event_broker.async_close()
        finally:
            close = getattr(self.client, "async_close", None)
            if close is not None:
                result: Any = close()
                if hasattr(result, "__await__"):
                    await result


def async_get_runtime(hass: HomeAssistant) -> CodexBridgeRuntime:
    domain_data = hass.data.get(DOMAIN)
    if not domain_data or not domain_data[DATA_ENTRIES]:
        raise RuntimeError("Codex Bridge is not configured")
    return next(iter(domain_data[DATA_ENTRIES].values()))


def normalize_web_search_mode(
    value: object,
    *,
    connection_type: str,
    capabilities: tuple[str, ...],
) -> str:
    """Constrain the preference while allowing post-login capability recovery."""

    if connection_type != CONNECTION_TYPE_SUPERVISOR:
        return WEB_SEARCH_MODE_DISABLED
    if value is None or value == WEB_SEARCH_MODE_LIVE:
        return WEB_SEARCH_MODE_LIVE
    return WEB_SEARCH_MODE_DISABLED
