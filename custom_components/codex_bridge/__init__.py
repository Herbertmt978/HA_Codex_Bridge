from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .bridge_api import BridgeApiAuthError, BridgeApiConnectionError, BridgeApiClient
from .const import (
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    DATA_ENTRIES,
    DATA_PANEL_REGISTERED,
    DATA_VIEWS_REGISTERED,
    DATA_WS_REGISTERED,
    DOMAIN,
)
from .http import async_register_http_views
from .panel import async_register_panel, async_remove_panel
from .runtime import CodexBridgeRuntime
from .websocket_api import async_register_websocket_commands

PLATFORMS: list[Platform] = []


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    domain_data = hass.data.setdefault(
        DOMAIN,
        {
            DATA_ENTRIES: {},
            DATA_PANEL_REGISTERED: False,
            DATA_VIEWS_REGISTERED: False,
            DATA_WS_REGISTERED: False,
        },
    )

    client = BridgeApiClient(
        async_get_clientsession(hass),
        entry.data[CONF_BRIDGE_URL],
        entry.data[CONF_BRIDGE_TOKEN],
    )
    try:
        await client.async_health()
    except BridgeApiAuthError as exc:
        raise ConfigEntryAuthFailed("bridge token rejected") from exc
    except BridgeApiConnectionError as exc:
        raise ConfigEntryNotReady("bridge service is unavailable") from exc

    runtime = CodexBridgeRuntime(
        entry_id=entry.entry_id,
        title=entry.title,
        client=client,
    )
    domain_data[DATA_ENTRIES][entry.entry_id] = runtime

    if not domain_data[DATA_VIEWS_REGISTERED]:
        async_register_http_views(hass)
        domain_data[DATA_VIEWS_REGISTERED] = True

    if not domain_data[DATA_WS_REGISTERED]:
        async_register_websocket_commands(hass)
        domain_data[DATA_WS_REGISTERED] = True

    if not domain_data[DATA_PANEL_REGISTERED]:
        await async_register_panel(hass, entry.title)
        domain_data[DATA_PANEL_REGISTERED] = True

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    domain_data = hass.data.get(DOMAIN)
    if not domain_data:
        return True

    domain_data[DATA_ENTRIES].pop(entry.entry_id, None)
    if not domain_data[DATA_ENTRIES]:
        async_remove_panel(hass)
        hass.data.pop(DOMAIN, None)

    return True
