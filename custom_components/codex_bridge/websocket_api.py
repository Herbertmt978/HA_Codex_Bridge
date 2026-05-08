from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from .bridge_api import BridgeApiAuthError, BridgeApiConnectionError, BridgeApiError
from .const import DOMAIN
from .runtime import async_get_runtime


def async_register_websocket_commands(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_get_config)
    websocket_api.async_register_command(hass, ws_list_threads)
    websocket_api.async_register_command(hass, ws_get_thread)
    websocket_api.async_register_command(hass, ws_create_thread)
    websocket_api.async_register_command(hass, ws_send_prompt)
    websocket_api.async_register_command(hass, ws_get_events)
    websocket_api.async_register_command(hass, ws_list_artifacts)


async def _async_handle(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    handler,
) -> None:
    try:
        runtime = async_get_runtime(hass)
        result = await handler(runtime.client)
    except RuntimeError as exc:
        connection.send_error(msg["id"], "not_configured", str(exc))
    except BridgeApiAuthError as exc:
        connection.send_error(msg["id"], "invalid_auth", str(exc))
    except BridgeApiConnectionError as exc:
        connection.send_error(msg["id"], "cannot_connect", str(exc))
    except BridgeApiError as exc:
        connection.send_error(msg["id"], "bridge_error", str(exc))
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/get_config"})
@websocket_api.async_response
async def ws_get_config(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    async def _handler(client) -> dict[str, Any]:
        runtime = async_get_runtime(hass)
        return {
            "panel_title": runtime.title,
            "bridge_url": client.base_url,
        }

    await _async_handle(hass, connection, msg, _handler)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/list_threads"})
@websocket_api.async_response
async def ws_list_threads(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_list_threads())


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/get_thread",
        vol.Required("thread_id"): str,
    }
)
@websocket_api.async_response
async def ws_get_thread(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_get_thread(msg["thread_id"]),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/create_thread",
        vol.Required("title"): str,
        vol.Optional("mode", default="full-auto"): vol.In(["observe", "edit", "full-auto"]),
    }
)
@websocket_api.async_response
async def ws_create_thread(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_create_thread(msg["title"], msg["mode"]),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/send_prompt",
        vol.Required("thread_id"): str,
        vol.Required("prompt"): str,
    }
)
@websocket_api.async_response
async def ws_send_prompt(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_send_prompt(msg["thread_id"], msg["prompt"]),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/get_events",
        vol.Required("thread_id"): str,
        vol.Optional("after", default=0): vol.Coerce(int),
    }
)
@websocket_api.async_response
async def ws_get_events(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_get_events(msg["thread_id"], msg["after"]),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/list_artifacts",
        vol.Required("thread_id"): str,
    }
)
@websocket_api.async_response
async def ws_list_artifacts(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_list_artifacts(msg["thread_id"]),
    )
