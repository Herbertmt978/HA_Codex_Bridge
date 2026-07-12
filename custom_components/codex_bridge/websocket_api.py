import asyncio
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from .bridge_api import BridgeApiAuthError, BridgeApiConnectionError, BridgeApiError
from .const import DOMAIN
from .runtime import async_get_runtime


def async_register_websocket_commands(hass: HomeAssistant) -> None:
    commands = (
        ws_get_config,
        ws_get_status,
        ws_get_auth_status,
        ws_start_auth_login,
        ws_logout_auth,
        ws_list_projects,
        ws_create_project,
        ws_update_project,
        ws_archive_project,
        ws_restore_project,
        ws_delete_project,
        ws_browse_paths,
        ws_create_folder,
        ws_list_threads,
        ws_get_thread,
        ws_create_thread,
        ws_update_thread,
        ws_archive_thread,
        ws_restore_thread,
        ws_delete_thread,
        ws_send_prompt,
        ws_cancel_run,
        ws_get_events,
        ws_subscribe_events,
        ws_list_artifacts,
        ws_create_workspace_archive,
    )
    for command in commands:
        websocket_api.async_register_command(hass, websocket_api.require_admin(command))


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


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/get_auth_status"})
@websocket_api.async_response
async def ws_get_auth_status(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_get_auth_status())


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/start_auth_login",
        vol.Optional("force_logout", default=True): bool,
    }
)
@websocket_api.async_response
async def ws_start_auth_login(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_start_auth_login(msg["force_logout"]),
    )


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/logout_auth"})
@websocket_api.async_response
async def ws_logout_auth(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_logout_auth())


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
        vol.Optional("root_path"): vol.Any(None, str),
        vol.Optional("default_model"): str,
        vol.Optional("default_thinking_level"): str,
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
            msg.get("default_model"),
            msg.get("default_thinking_level"),
            msg.get("root_path"),
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
        vol.Required("type"): f"{DOMAIN}/cancel_run",
        vol.Required("thread_id"): str,
    }
)
@websocket_api.async_response
async def ws_cancel_run(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_cancel_run(msg["thread_id"]),
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
        vol.Required("type"): f"{DOMAIN}/subscribe_events",
        vol.Required("thread_id"): str,
        vol.Optional("after", default=0): vol.Coerce(int),
    }
)
@websocket_api.async_response
async def ws_subscribe_events(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    try:
        runtime = async_get_runtime(hass)
    except RuntimeError as exc:
        connection.send_error(msg["id"], "not_configured", str(exc))
        return

    thread_id = msg["thread_id"]
    after = msg["after"]

    async def _forward_events() -> None:
        nonlocal after
        try:
            while True:
                events = await runtime.client.async_get_events(thread_id, after)
                for event in events:
                    sequence = event.get("sequence")
                    if isinstance(sequence, int):
                        after = max(after, sequence)
                    connection.send_event(msg["id"], event)
                await asyncio.sleep(0.75 if events else 1.5)
        except asyncio.CancelledError:
            raise
        except BridgeApiAuthError as exc:
            connection.send_event(msg["id"], {"event_type": "bridge.error", "payload": {"error": str(exc)}})
        except BridgeApiConnectionError as exc:
            connection.send_event(msg["id"], {"event_type": "bridge.error", "payload": {"error": str(exc)}})
        except BridgeApiError as exc:
            connection.send_event(msg["id"], {"event_type": "bridge.error", "payload": {"error": str(exc)}})

    task = hass.async_create_task(_forward_events())

    def _unsubscribe() -> None:
        task.cancel()

    connection.subscriptions[msg["id"]] = _unsubscribe
    connection.send_result(msg["id"])


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
