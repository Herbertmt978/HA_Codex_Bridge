from inspect import iscoroutinefunction

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.dispatcher import async_dispatcher_send

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
    CONF_WEB_SEARCH_MODE,
    CONF_ASSIST_MCP_SERVERS,
)
from .event_broker import EventBroker
from .entity_coordinator import BridgeEntityCoordinator
from .automation_scheduler import AutomationScheduler
from .automation_notifications import AutomationNotificationCoordinator
from .http import async_register_http_views
from .ha_mcp_shortcut import HaMcpShortcut
from .assist_settings import assist_mcp_selection
from .panel import async_register_panel, async_remove_panel
from .protocol import EndpointError, validate_bridge_token, validate_bridge_url
from .runtime import CodexBridgeRuntime, normalize_web_search_mode
from .task_services import async_register_task_services
from .task_events import TaskEventForwarder
from .websocket_api import async_register_websocket_commands

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.CONVERSATION]


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

    from .host_access import HOST_WORKER_KEY
    if (
        connection_type == CONNECTION_TYPE_SUPERVISOR
        and "host_access_v1" in getattr(ready, "capabilities", ())
        and isinstance(entry.data.get(HOST_WORKER_KEY), dict)
    ):
        try:
            await client.async_pair_host_worker(entry.data[HOST_WORKER_KEY])
        except BridgeApiError:
            # An optional companion must not stop ordinary chats from loading.
            # Settings reports its availability; pairing never enables access.
            pass

    runtime = CodexBridgeRuntime(
        entry_id=entry.entry_id,
        title=entry.title,
        client=client,
        connection_type=connection_type,
        discovery_uuid=entry.data.get(CONF_DISCOVERY_UUID),
        api_version=client.negotiated_api_version or 0,
        capabilities=tuple(getattr(ready, "capabilities", ())),
        web_search_mode=normalize_web_search_mode(
            entry.options.get(CONF_WEB_SEARCH_MODE),
            connection_type=connection_type,
            capabilities=tuple(getattr(ready, "capabilities", ())),
        ),
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
        # Capabilities can refresh after setup when an App is upgraded.
        runtime.task_event_forwarder = TaskEventForwarder(
            hass,
            runtime.event_broker,
            Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.task_event_cursor"),
        )
        if (
            connection_type != CONNECTION_TYPE_EXTERNAL_LEGACY
            and runtime.supports_capability("automations_v1")
            and iscoroutinefunction(
                getattr(client, "async_scheduler_automations", None)
            )
        ):
            runtime.automation_scheduler = AutomationScheduler(
                hass,
                client,
                connection_type,
                web_search_mode=(
                    runtime.web_search_mode
                    if runtime.supports_capability("web_search_v1")
                    else None
                ),
            )
        if connection_type != CONNECTION_TYPE_EXTERNAL_LEGACY:
            runtime.automation_notifications = AutomationNotificationCoordinator(
                hass,
                runtime,
                Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.automation_notifications"),
            )
        runtime.entity_coordinator = BridgeEntityCoordinator(hass, runtime)
    domain_data[DATA_ENTRIES][entry.entry_id] = runtime
    try:
        if connection_type == CONNECTION_TYPE_SUPERVISOR:
            async def select_owned_mcp(name: str, selected: bool) -> None:
                previous = assist_mcp_selection(entry.options.get(CONF_ASSIST_MCP_SERVERS, []))
                if previous is None:
                    raise ValueError("Assist MCP selection is invalid")
                names = set(previous)
                if selected:
                    if len(names) >= 32 and name not in names:
                        raise ValueError("Assist MCP selection is full")
                    names.add(name)
                else:
                    names.discard(name)
                hass.config_entries.async_update_entry(
                    entry, options={**entry.options, CONF_ASSIST_MCP_SERVERS: sorted(names)}
                )
                async_dispatcher_send(hass, f"{DOMAIN}.{entry.entry_id}.assist_settings")

            runtime.ha_mcp_shortcut = HaMcpShortcut(
                hass, entry.entry_id, client, connection_type=connection_type,
                supports_capability=runtime.supports_capability,
                selection_callback=select_owned_mcp,
            )
            await runtime.ha_mcp_shortcut.async_setup()
        if not hass.services.has_service(DOMAIN, "start_task"):
            async_register_task_services(hass)
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
            if runtime.task_event_forwarder is not None:
                await runtime.task_event_forwarder.async_start()
            await runtime.event_broker.async_start()
        if runtime.automation_scheduler is not None:
            await runtime.automation_scheduler.async_start()
        if (
            runtime.automation_notifications is not None
            and runtime.supports_capability("automation_notifications_v1")
        ):
            await runtime.automation_notifications.async_start()
        if runtime.entity_coordinator is not None:
            await runtime.entity_coordinator.async_refresh()
            await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        if runtime.entity_coordinator is not None:
            await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        domain_data[DATA_ENTRIES].pop(entry.entry_id, None)
        await runtime.async_close()
        raise

    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Revoke only this entry's managed Home Assistant MCP authorisation."""
    if entry.data.get(CONF_CONNECTION_TYPE) != CONNECTION_TYPE_SUPERVISOR:
        return

    async def detach(_name: str, _selected: bool) -> None:
        return None

    # HA has already removed the entry and closed its runtime. Revoke local
    # authority before any network wait, even if App discovery is cancelled.
    local = HaMcpShortcut(
        hass, entry.entry_id, None, connection_type=CONNECTION_TYPE_SUPERVISOR,
        supports_capability=lambda _value: False, selection_callback=detach,
    )
    await local.async_remove()
    try:
        client = BridgeApiClient(
            async_get_clientsession(hass), entry.data[CONF_BRIDGE_URL],
            entry.data[CONF_BRIDGE_TOKEN],
        )
    except (BridgeApiError, KeyError):
        return
    capabilities: tuple[str, ...] = ()
    try:
        ready = await client.async_ready()
        capabilities = tuple(ready.capabilities)
    except BridgeApiError:
        pass

    shortcut = HaMcpShortcut(
        hass, entry.entry_id, client, connection_type=CONNECTION_TYPE_SUPERVISOR,
        supports_capability=lambda value: value in capabilities,
        selection_callback=detach,
    )
    try:
        await shortcut.async_remove()
    finally:
        close = getattr(client, "async_close", None)
        if close is not None:
            await close()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    domain_data = hass.data.get(DOMAIN)
    if not domain_data:
        return True

    runtime = domain_data[DATA_ENTRIES].get(entry.entry_id)
    if runtime is not None and runtime.entity_coordinator is not None:
        if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
            return False
    runtime = domain_data[DATA_ENTRIES].pop(entry.entry_id, None)
    if runtime is not None:
        await runtime.async_close()
    if not domain_data[DATA_ENTRIES]:
        async_remove_panel(hass)
        domain_data[DATA_PANEL_REGISTERED] = False

    return True
