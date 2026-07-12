import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .bridge_api import BridgeApiAuthError, BridgeApiConnectionError, BridgeApiClient, BridgeApiError
from .const import (
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    CONF_PANEL_TITLE,
    DEFAULT_BRIDGE_URL,
    DEFAULT_PANEL_TITLE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class CodexBridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()

            client = BridgeApiClient(
                async_get_clientsession(self.hass),
                user_input[CONF_BRIDGE_URL],
                user_input[CONF_BRIDGE_TOKEN],
            )
            try:
                await client.async_ready()
            except BridgeApiAuthError:
                errors["base"] = "invalid_auth"
            except BridgeApiConnectionError:
                errors["base"] = "cannot_connect"
            except BridgeApiError:
                _LOGGER.exception("Unexpected bridge error during setup")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_PANEL_TITLE],
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BRIDGE_URL, default=DEFAULT_BRIDGE_URL): str,
                    vol.Required(CONF_BRIDGE_TOKEN): str,
                    vol.Required(CONF_PANEL_TITLE, default=DEFAULT_PANEL_TITLE): str,
                }
            ),
            errors=errors,
        )
