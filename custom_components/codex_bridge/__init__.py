from inspect import iscoroutinefunction
import logging

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType

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
    DATA_PANEL_REGISTERED,
    DATA_VIEWS_REGISTERED,
    DATA_WS_REGISTERED,
    DOMAIN,
    EVENT_CURSOR_STORAGE_VERSION,
    CONF_WEB_SEARCH_MODE,
    CONF_ASSIST_MCP_SERVERS,
)
from .account_allowance_coordinator import (
    AccountAllowanceCoordinator,
    async_delete_reauthentication_issues,
)
from .event_broker import EventBroker
from .entity import bridge_device_info
from .entity_coordinator import BridgeEntityCoordinator
from .automation_scheduler import AutomationScheduler
from .automation_notifications import AutomationNotificationCoordinator
from .question_notifications import QuestionNotificationCoordinator
from .question_notification_settings import question_notification_settings
from .http import async_register_http_views
from .ha_mcp_shortcut import HaMcpShortcut
from .assist_settings import assist_mcp_selection
from .panel import async_register_panel, async_remove_panel
from .protocol import EndpointError, validate_bridge_token, validate_bridge_url
from .runtime import (
    CodexBridgeConfigEntry,
    CodexBridgeRuntime,
    normalize_web_search_mode,
)
from .task_services import async_register_task_services
from .task_events import TaskEventForwarder
from .websocket_api import async_register_websocket_commands

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.CONVERSATION]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the task actions, which resolve the loaded entry when called."""

    async_register_task_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CodexBridgeConfigEntry) -> bool:
    domain_data = hass.data.setdefault(
        DOMAIN,
        {
            DATA_PANEL_REGISTERED: False,
            DATA_VIEWS_REGISTERED: False,
            DATA_WS_REGISTERED: False,
        },
    )
    if _other_live_runtime(hass, entry.entry_id):
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="connection_already_active"
        )

    connection_type = entry.data.get(
        CONF_CONNECTION_TYPE, CONNECTION_TYPE_EXTERNAL_LEGACY
    )
    if connection_type not in {
        CONNECTION_TYPE_EXTERNAL_LEGACY,
        CONNECTION_TYPE_SUPERVISOR,
    }:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="invalid_configuration"
        )
    try:
        bridge_url = validate_bridge_url(entry.data[CONF_BRIDGE_URL])
        bridge_token = validate_bridge_token(entry.data[CONF_BRIDGE_TOKEN])
    except (EndpointError, KeyError) as exc:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="invalid_configuration"
        ) from exc
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
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="authentication_failed"
        ) from exc
    except BridgeApiConnectionError as exc:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="cannot_connect"
        ) from exc
    except BridgeApiIncompatibleError as exc:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="incompatible_api"
        ) from exc
    except BridgeApiError as exc:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="not_ready"
        ) from exc

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
        question_notification_settings=question_notification_settings(entry.options),
        web_search_mode=normalize_web_search_mode(
            entry.options.get(CONF_WEB_SEARCH_MODE),
            connection_type=connection_type,
            capabilities=tuple(getattr(ready, "capabilities", ())),
        ),
    )
    device_info = bridge_device_info(
        hass, entry, runtime, sw_version=_ready_version(ready)
    )
    runtime.device_info = device_info
    # Saved-account devices are shown under this device, so register it first.
    runtime.device_id = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **device_info
    ).id

    @callback
    def async_start_reauth() -> None:
        # A rejection racing a reload must not outlive the reload it raced.
        if entry.state is not ConfigEntryState.UNLOAD_IN_PROGRESS:
            entry.async_start_reauth(hass)

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
            on_authentication_failed=async_start_reauth,
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
        if connection_type != CONNECTION_TYPE_EXTERNAL_LEGACY:
            runtime.question_notifications = QuestionNotificationCoordinator(
                hass, runtime,
                Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.question_notifications"),
            )
        runtime.entity_coordinator = BridgeEntityCoordinator(hass, entry, runtime)
        runtime.account_allowance_coordinator = AccountAllowanceCoordinator(
            hass, entry, runtime
        )
    entry.runtime_data = runtime
    platforms_forwarded = False
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
        if runtime.question_notifications is not None and runtime.supports_capability("interactions_v2"):
            await runtime.question_notifications.async_start()
        if runtime.entity_coordinator is not None:
            await runtime.entity_coordinator.async_refresh()
        if (
            runtime.account_allowance_coordinator is not None
            and runtime.supports_capability("account_profile_telemetry_v1")
        ):
            # An account telemetry outage must not prevent the platforms
            # loading. Retain registered profiles and retry through this coordinator.
            await runtime.account_allowance_coordinator.async_refresh()
        if runtime.entity_coordinator is not None:
            platforms_forwarded = True
            await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException as error:
        if platforms_forwarded:
            await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        await runtime.async_close()
        # The App answered readiness but failed a later setup call. Let HA
        # retry or re-authenticate rather than leave the entry in setup error.
        if isinstance(error, BridgeApiAuthError):
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="authentication_failed"
            ) from error
        if isinstance(error, BridgeApiError):
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN, translation_key="not_ready"
            ) from error
        raise

    return True


def _other_live_runtime(hass: HomeAssistant, entry_id: str) -> bool:
    """Return whether another entry still owns an open runtime.

    HA keeps runtime data after a failed or interrupted unload, so such an
    entry's broker, scheduler and notifications may still be running.
    """

    for other in hass.config_entries.async_entries(DOMAIN):
        runtime: CodexBridgeRuntime | None = getattr(other, "runtime_data", None)
        if other.entry_id != entry_id and runtime is not None and not runtime.closed:
            return True
    return False


def _ready_version(ready: object) -> str | None:
    """Return the App release, or the Bridge release for an external Bridge."""

    for field in ("app_version", "bridge_version"):
        version = getattr(ready, field, None)
        if isinstance(version, str) and version:
            return version
    return None


async def async_remove_entry(hass: HomeAssistant, entry: CodexBridgeConfigEntry) -> None:
    """Clear this entry's question notices and revoke its managed MCP grant."""

    async def clear_questions() -> None:
        # HA keeps runtime data only after a cancelled/failed unload. That owner
        # may still hold an admitted delivery, and HA can proceed to remove
        # without another unload, so drain it before a fresh coordinator takes
        # its saved ledger.
        runtime: CodexBridgeRuntime | None = getattr(entry, "runtime_data", None)
        if runtime is not None:
            await runtime.async_close()
        # Only after that owner stops can no refresh raise a repair again.
        async_delete_reauthentication_issues(hass, entry.entry_id)
        # Normal unload preserves delivery claims for restart. Permanent removal
        # has no runtime to reconcile them, including on an external connection.
        coordinator = QuestionNotificationCoordinator(
            hass, None,
            Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.question_notifications"),
        )
        if not await coordinator.async_permanently_remove():
            _LOGGER.warning("Question notification removal is incomplete; its cleanup ledger was retained")

    if entry.data.get(CONF_CONNECTION_TYPE) != CONNECTION_TYPE_SUPERVISOR:
        await clear_questions()
        return

    async def detach(_name: str, _selected: bool) -> None:
        return None

    # HA has already removed the entry. Revoke local authority before any
    # service or network wait, even if a previous unload was interrupted.
    local = HaMcpShortcut(
        hass, entry.entry_id, None, connection_type=CONNECTION_TYPE_SUPERVISOR,
        supports_capability=lambda _value: False, selection_callback=detach,
    )
    try:
        await local.async_remove()
    finally:
        # Revoke local authority before waiting for any notification service.
        await clear_questions()
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


async def async_unload_entry(hass: HomeAssistant, entry: CodexBridgeConfigEntry) -> bool:
    runtime = entry.runtime_data
    if runtime.entity_coordinator is not None:
        if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
            return False
    # HA discards runtime data only after this returns. A cancelled or failed
    # close keeps the draining owner, so permanent removal cannot race a
    # still-admitted timer or mobile action.
    await runtime.async_close()
    if not _other_live_runtime(hass, entry.entry_id):
        async_remove_panel(hass)
        hass.data.setdefault(DOMAIN, {})[DATA_PANEL_REGISTERED] = False

    return True
