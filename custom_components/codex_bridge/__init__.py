from inspect import iscoroutinefunction

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .bridge_api import (
    BridgeApiAuthError,
    BridgeApiConnectionError,
    BridgeApiError,
    BridgeApiIncompatibleError,
    BridgeApiClient,
)
from .const import (
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    CONF_CONNECTION_TYPE,
    CONF_DISCOVERY_UUID,
    CONNECTION_TYPE_EXTERNAL_LEGACY,
    CONNECTION_TYPE_SUPERVISOR,
    DATA_ENTRIES,
    DATA_PANEL_REGISTERED,
    DATA_VIEWS_REGISTERED,
    DATA_WS_REGISTERED,
    DOMAIN,
    EVENT_CURSOR_STORAGE_VERSION,
)
from .event_broker import EventBroker
from .automation_scheduler import AutomationScheduler
from .http import async_register_http_views
from .panel import async_register_panel, async_remove_panel
from .protocol import EndpointError, validate_bridge_token, validate_bridge_url
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
    if domain_data[DATA_ENTRIES] and entry.entry_id not in domain_data[DATA_ENTRIES]:
        raise ConfigEntryNotReady("another Codex Bridge connection is already active")

    connection_type = entry.data.get(
        CONF_CONNECTION_TYPE, CONNECTION_TYPE_EXTERNAL_LEGACY
    )
    if connection_type not in {
        CONNECTION_TYPE_EXTERNAL_LEGACY,
        CONNECTION_TYPE_SUPERVISOR,
    }:
        raise ConfigEntryNotReady("bridge configuration is invalid")
    try:
        bridge_url = validate_bridge_url(entry.data[CONF_BRIDGE_URL])
        bridge_token = validate_bridge_token(entry.data[CONF_BRIDGE_TOKEN])
    except (EndpointError, KeyError) as exc:
        raise ConfigEntryNotReady("bridge configuration is invalid") from exc
    client = BridgeApiClient(
        async_get_clientsession(hass),
        bridge_url,
        bridge_token,
        allow_legacy_v0=connection_type == CONNECTION_TYPE_EXTERNAL_LEGACY,
    )
    try:
        ready = await client.async_ready()
        if connection_type == CONNECTION_TYPE_SUPERVISOR:
            client.require_api_v1()
        else:
            client.require_legacy_v0()
    except BridgeApiAuthError as exc:
        raise ConfigEntryAuthFailed("bridge token rejected") from exc
    except BridgeApiConnectionError as exc:
        raise ConfigEntryNotReady("bridge service is unavailable") from exc
    except BridgeApiIncompatibleError as exc:
        raise ConfigEntryNotReady("bridge service API is incompatible") from exc
    except BridgeApiError as exc:
        raise ConfigEntryNotReady("bridge service is not ready") from exc

    runtime = CodexBridgeRuntime(
        entry_id=entry.entry_id,
        title=entry.title,
        client=client,
        connection_type=connection_type,
        discovery_uuid=entry.data.get(CONF_DISCOVERY_UUID),
        api_version=client.negotiated_api_version or 0,
        capabilities=tuple(getattr(ready, "capabilities", ())),
    )
    if runtime.api_version == 1:
        runtime.event_broker = EventBroker(
            client,
            store=Store(
                hass,
                EVENT_CURSOR_STORAGE_VERSION,
                f"{DOMAIN}.{entry.entry_id}.event_cursor",
            ),
            task_factory=lambda target, name: entry.async_create_background_task(
                hass, target, name
            ),
        )
        if (
            connection_type != CONNECTION_TYPE_EXTERNAL_LEGACY
            and runtime.supports_capability("automations_v1")
            and iscoroutinefunction(
                getattr(client, "async_scheduler_automations", None)
            )
        ):
            runtime.automation_scheduler = AutomationScheduler(
                hass, client, connection_type
            )
    domain_data[DATA_ENTRIES][entry.entry_id] = runtime
    try:
        if not domain_data[DATA_VIEWS_REGISTERED]:
            async_register_http_views(hass)
            domain_data[DATA_VIEWS_REGISTERED] = True

        if not domain_data[DATA_WS_REGISTERED]:
            async_register_websocket_commands(hass)
            domain_data[DATA_WS_REGISTERED] = True

        if not domain_data[DATA_PANEL_REGISTERED]:
            await async_register_panel(hass, entry.title)
            domain_data[DATA_PANEL_REGISTERED] = True
        if runtime.event_broker is not None:
            await runtime.event_broker.async_start()
        if runtime.automation_scheduler is not None:
            await runtime.automation_scheduler.async_start()
    except BaseException:
        domain_data[DATA_ENTRIES].pop(entry.entry_id, None)
        await runtime.async_close()
        raise

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    domain_data = hass.data.get(DOMAIN)
    if not domain_data:
        return True

    runtime = domain_data[DATA_ENTRIES].pop(entry.entry_id, None)
    if runtime is not None:
        await runtime.async_close()
    if not domain_data[DATA_ENTRIES]:
        async_remove_panel(hass)
        domain_data[DATA_PANEL_REGISTERED] = False

    return True
