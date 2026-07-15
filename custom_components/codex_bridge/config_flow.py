import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .bridge_api import (
    BridgeApiAuthError,
    BridgeApiConnectionError,
    BridgeApiError,
    BridgeApiIncompatibleError,
    BridgeApiClient,
)
from .const import (
    API_CURRENT,
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    CONF_CONNECTION_TYPE,
    CONF_DISCOVERY_UUID,
    CONNECTION_TYPE_EXTERNAL_LEGACY,
    CONNECTION_TYPE_SUPERVISOR,
    DEFAULT_BRIDGE_URL,
    DISCOVERY_SERVICE,
    DISCOVERY_SOURCE,
    DOMAIN,
)
from .protocol import (
    ApiIncompatibleError as ProtocolApiIncompatibleError,
    DiscoveryRecord,
    EndpointError,
    validate_bridge_token,
    validate_bridge_url,
)


def _safe_title(value: object) -> str:
    """Accept a short display name while keeping Supervisor data out of logs."""

    if isinstance(value, str):
        value = value.strip()
    if (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and all(character.isprintable() for character in value)
    ):
        return value
    return "Codex Bridge"


def _entry_data(
    *,
    bridge_url: str,
    bridge_token: str,
    connection_type: str,
    discovery_uuid: str | None = None,
) -> dict[str, str]:
    data = {
        CONF_BRIDGE_URL: validate_bridge_url(bridge_url),
        CONF_BRIDGE_TOKEN: validate_bridge_token(bridge_token),
        CONF_CONNECTION_TYPE: connection_type,
    }
    if discovery_uuid is not None:
        data[CONF_DISCOVERY_UUID] = discovery_uuid
    return data


class CodexBridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 1

    _hassio_discovery: DiscoveryRecord | None = None
    _hassio_title = "Codex Bridge"
    _hassio_replaced_entry: config_entries.ConfigEntry | None = None
    _hassio_error: str | None = None

    async def _async_hassio_ready_error(self, discovery: DiscoveryRecord) -> str | None:
        """Return a stable error code after authenticated v1 readiness validation."""

        try:
            client = BridgeApiClient(
                async_get_clientsession(self.hass),
                discovery.base_url,
                discovery.token,
            )
            await client.async_ready(discovery=discovery)
            if client.negotiated_api_version != API_CURRENT:
                raise BridgeApiIncompatibleError()
        except BridgeApiAuthError:
            return "invalid_auth"
        except BridgeApiConnectionError:
            return "cannot_connect"
        except BridgeApiIncompatibleError:
            return "incompatible_api"
        except BridgeApiError:
            return "unknown"
        return None

    async def async_step_user(self, user_input=None):
        """Guide manual users to the App, leaving legacy Bridge setup explicit."""

        return self.async_show_menu(step_id="user", menu_options=["app", "external"])

    async def async_step_app(self, user_input=None):
        """Explain that Supervisor discovery owns App credentials and endpoint data."""

        return self.async_show_form(
            step_id="app",
            data_schema=vol.Schema({}),
            errors={"base": "app_not_discovered"} if user_input is not None else {},
        )

    async def async_step_external(self, user_input=None):
        """Allow only the deliberately constrained legacy external transport."""

        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        errors = {}

        if user_input is not None:
            try:
                bridge_url = validate_bridge_url(user_input[CONF_BRIDGE_URL])
                bridge_token = validate_bridge_token(user_input[CONF_BRIDGE_TOKEN])
                client = BridgeApiClient(
                    async_get_clientsession(self.hass),
                    bridge_url,
                    bridge_token,
                    allow_legacy_v0=True,
                )
                await client.async_ready()
                if client.negotiated_api_version != 0:
                    raise BridgeApiIncompatibleError()
            except EndpointError:
                errors["base"] = "invalid_endpoint"
            except BridgeApiIncompatibleError:
                errors["base"] = "incompatible_api"
            except BridgeApiAuthError:
                errors["base"] = "invalid_auth"
            except BridgeApiConnectionError:
                errors["base"] = "cannot_connect"
            except BridgeApiError:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(f"{DOMAIN}:external")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="External Codex Bridge",
                    data=_entry_data(
                        bridge_url=bridge_url,
                        bridge_token=bridge_token,
                        connection_type=CONNECTION_TYPE_EXTERNAL_LEGACY,
                    ),
                )

        return self.async_show_form(
            step_id="external",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BRIDGE_URL, default=DEFAULT_BRIDGE_URL): str,
                    vol.Required(CONF_BRIDGE_TOKEN): str,
                }
            ),
            errors=errors,
        )

    async def async_step_hassio(self, discovery_info: HassioServiceInfo):
        """Validate Supervisor discovery, then establish a v1-only App entry."""

        self._hassio_error = None
        self._hassio_replaced_entry = None
        payload = dict(discovery_info.config)
        payload.update(
            {
                "source": DISCOVERY_SOURCE,
                "service": DISCOVERY_SERVICE,
                "slug": discovery_info.slug,
                "uuid": discovery_info.uuid,
            }
        )
        try:
            discovery = DiscoveryRecord.from_payload(payload)
        except EndpointError:
            return self.async_abort(reason="invalid_discovery")
        except ProtocolApiIncompatibleError:
            return self.async_abort(reason="incompatible_api")
        error = await self._async_hassio_ready_error(discovery)
        if error and error != "cannot_connect":
            return self.async_abort(reason=error)

        data = _entry_data(
            bridge_url=discovery.base_url,
            bridge_token=discovery.token,
            connection_type=CONNECTION_TYPE_SUPERVISOR,
            discovery_uuid=discovery.uuid,
        )
        title = _safe_title(discovery_info.name)
        existing_entry = await self.async_set_unique_id(discovery.uuid)
        if existing_entry is not None and error is None:
            return self.async_update_reload_and_abort(
                existing_entry,
                title=title,
                data=data,
                reason="reconfigure_successful",
                reload_even_if_entry_is_unchanged=False,
            )
        if existing_entry is not None:
            self._hassio_replaced_entry = existing_entry

        entries = self._async_current_entries()
        if entries and existing_entry is None:
            if (
                len(entries) != 1
                or entries[0].data.get(
                    CONF_CONNECTION_TYPE, CONNECTION_TYPE_EXTERNAL_LEGACY
                )
                != CONNECTION_TYPE_EXTERNAL_LEGACY
            ):
                return self.async_abort(reason="already_configured")
            self._hassio_replaced_entry = entries[0]

        self._hassio_discovery = discovery
        self._hassio_title = title
        self._hassio_error = error
        self._set_confirm_only()
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(self, user_input=None):
        """Require an administrator to confirm a newly discovered App."""

        discovery = self._hassio_discovery
        if discovery is None:
            return self.async_abort(reason="invalid_discovery")
        if user_input is None:
            return self.async_show_form(
                step_id="hassio_confirm",
                data_schema=vol.Schema({}),
                description_placeholders={"name": self._hassio_title},
                errors=(
                    {"base": self._hassio_error}
                    if self._hassio_error is not None
                    else {}
                ),
            )

        if error := await self._async_hassio_ready_error(discovery):
            if error == "cannot_connect":
                self._hassio_error = error
                return self.async_show_form(
                    step_id="hassio_confirm",
                    data_schema=vol.Schema({}),
                    description_placeholders={"name": self._hassio_title},
                    errors={"base": error},
                )
            return self.async_abort(reason=error)

        self._hassio_error = None
        data = _entry_data(
            bridge_url=discovery.base_url,
            bridge_token=discovery.token,
            connection_type=CONNECTION_TYPE_SUPERVISOR,
            discovery_uuid=discovery.uuid,
        )
        if self._hassio_replaced_entry is not None:
            return self.async_update_reload_and_abort(
                self._hassio_replaced_entry,
                unique_id=discovery.uuid,
                title=self._hassio_title,
                data=data,
                reason="reconfigure_successful",
            )
        return self.async_create_entry(title=self._hassio_title, data=data)
