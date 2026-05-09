from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from .bridge_api import BridgeApiAuthError, BridgeApiConnectionError, BridgeApiError
from .const import DOMAIN
from .runtime import async_get_runtime


def async_register_websocket_commands(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_get_config)
    websocket_api.async_register_command(hass, ws_get_status)
    websocket_api.async_register_command(hass, ws_list_projects)
    websocket_api.async_register_command(hass, ws_create_project)
    websocket_api.async_register_command(hass, ws_update_project)
    websocket_api.async_register_command(hass, ws_archive_project)
    websocket_api.async_register_command(hass, ws_restore_project)
    websocket_api.async_register_command(hass, ws_delete_project)
    websocket_api.async_register_command(hass, ws_browse_paths)
    websocket_api.async_register_command(hass, ws_create_folder)
    websocket_api.async_register_command(hass, ws_list_threads)
    websocket_api.async_register_command(hass, ws_get_thread)
    websocket_api.async_register_command(hass, ws_create_thread)
    websocket_api.async_register_command(hass, ws_update_thread)
    websocket_api.async_register_command(hass, ws_archive_thread)
    websocket_api.async_register_command(hass, ws_restore_thread)
    websocket_api.async_register_command(hass, ws_delete_thread)
    websocket_api.async_register_command(hass, ws_send_prompt)
    websocket_api.async_register_command(hass, ws_get_events)
    websocket_api.async_register_command(hass, ws_list_artifacts)
    websocket_api.async_register_command(hass, ws_create_workspace_archive)


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


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/get_status"})
@websocket_api.async_response
async def ws_get_status(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_get_status())


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/list_projects"})
@websocket_api.async_response
async def ws_list_projects(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_list_projects())


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/create_project",
        vol.Required("name"): str,
        vol.Required("root_path"): str,
        vol.Optional("default_model", default="gpt-5.4"): str,
        vol.Optional("default_thinking_level", default="medium"): str,
    }
)
@websocket_api.async_response
async def ws_create_project(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_create_project(
            msg["name"],
            msg["root_path"],
            msg["default_model"],
            msg["default_thinking_level"],
        ),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/update_project",
        vol.Required("project_id"): str,
        vol.Optional("name"): vol.Any(None, str),
        vol.Optional("root_path"): vol.Any(None, str),
        vol.Optional("default_model"): vol.Any(None, str),
        vol.Optional("default_thinking_level"): vol.Any(None, str),
    }
)
@websocket_api.async_response
async def ws_update_project(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    updates = {
        key: msg[key]
        for key in ("name", "root_path", "default_model", "default_thinking_level")
        if key in msg
    }
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_update_project(msg["project_id"], updates),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/archive_project",
        vol.Required("project_id"): str,
    }
)
@websocket_api.async_response
async def ws_archive_project(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_archive_project(msg["project_id"]),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/restore_project",
        vol.Required("project_id"): str,
    }
)
@websocket_api.async_response
async def ws_restore_project(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_restore_project(msg["project_id"]),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/delete_project",
        vol.Required("project_id"): str,
    }
)
@websocket_api.async_response
async def ws_delete_project(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_delete_project(msg["project_id"]),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/browse_paths",
        vol.Optional("path"): vol.Any(None, str),
    }
)
@websocket_api.async_response
async def ws_browse_paths(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_browse_paths(msg.get("path")),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/create_folder",
        vol.Required("parent_path"): str,
        vol.Required("folder_name"): str,
    }
)
@websocket_api.async_response
async def ws_create_folder(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_create_folder(msg["parent_path"], msg["folder_name"]),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/list_threads",
        vol.Optional("include_archived", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_list_threads(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_list_threads(msg["include_archived"]),
    )


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
        vol.Optional("project_id"): vol.Any(None, str),
        vol.Optional("mode", default="full-auto"): vol.In(["observe", "edit", "full-auto"]),
        vol.Optional("model_override"): vol.Any(None, str),
        vol.Optional("thinking_override"): vol.Any(None, str),
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
        lambda client: client.async_create_thread(
            msg["title"],
            msg["mode"],
            msg.get("project_id"),
            msg.get("model_override"),
            msg.get("thinking_override"),
        ),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/update_thread",
        vol.Required("thread_id"): str,
        vol.Optional("title"): vol.Any(None, str),
        vol.Optional("mode"): vol.In(["observe", "edit", "full-auto"]),
        vol.Optional("model_override"): vol.Any(None, str),
        vol.Optional("thinking_override"): vol.Any(None, str),
    }
)
@websocket_api.async_response
async def ws_update_thread(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    updates = {
        key: msg[key]
        for key in ("title", "mode", "model_override", "thinking_override")
        if key in msg
    }
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_update_thread(msg["thread_id"], updates),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/archive_thread",
        vol.Required("thread_id"): str,
    }
)
@websocket_api.async_response
async def ws_archive_thread(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_archive_thread(msg["thread_id"]),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/restore_thread",
        vol.Required("thread_id"): str,
    }
)
@websocket_api.async_response
async def ws_restore_thread(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_restore_thread(msg["thread_id"]),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/delete_thread",
        vol.Required("thread_id"): str,
    }
)
@websocket_api.async_response
async def ws_delete_thread(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_delete_thread(msg["thread_id"]),
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/create_workspace_archive",
        vol.Required("thread_id"): str,
    }
)
@websocket_api.async_response
async def ws_create_workspace_archive(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_create_workspace_archive(msg["thread_id"]),
    )
