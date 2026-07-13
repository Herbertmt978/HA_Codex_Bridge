from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant

from .bridge_api import BridgeApiClient
from .const import DATA_ENTRIES, DOMAIN
from .event_broker import EventBroker


@dataclass(slots=True)
class CodexBridgeRuntime:
    entry_id: str
    title: str
    client: BridgeApiClient
    connection_type: str
    discovery_uuid: str | None
    api_version: int
    event_broker: EventBroker | None = None

    async def async_close(self) -> None:
        """Provide a lifecycle seam without taking ownership of HA's session."""

        try:
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
