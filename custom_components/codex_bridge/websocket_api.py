import asyncio
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant

from .bridge_api import (
    BridgeApiAuthError,
    BridgeApiConnectionError,
    BridgeApiError,
    BridgeApiGoneError,
)
from .const import BRIDGE_EVENT_CURSOR_MAX, DOMAIN
from .event_broker import EventBatch, EventRecord
from .protocol import EndpointError
from .runtime import async_get_runtime


def async_register_websocket_commands(hass: HomeAssistant) -> None:
    commands = (
        ws_get_config,
        ws_get_status,
        ws_get_event_status,
        ws_get_auth_status,
        ws_start_auth_login,
        ws_cancel_auth_login,
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
        ws_unsubscribe_events,
        ws_list_pending_interactions,
        ws_decide_interaction,
        ws_answer_interaction,
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
    except BridgeApiAuthError:
        connection.send_error(msg["id"], "invalid_auth", "Bridge authentication failed")
    except BridgeApiConnectionError:
        connection.send_error(msg["id"], "cannot_connect", "Bridge is unavailable")
    except EndpointError:
        connection.send_error(msg["id"], "bridge_error", "Bridge response is invalid")
    except BridgeApiError:
        connection.send_error(msg["id"], "bridge_error", "Bridge request failed")
    except RuntimeError:
        connection.send_error(
            msg["id"], "not_configured", "Codex Bridge is not configured"
        )
    except ValueError:
        connection.send_error(msg["id"], "invalid_request", "Request is invalid")
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
            "connection_type": runtime.connection_type,
            "api_version": runtime.api_version,
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


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/get_event_status"})
@websocket_api.async_response
async def ws_get_event_status(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Expose only the Integration's safe event-consumer health projection."""

    try:
        runtime = async_get_runtime(hass)
    except RuntimeError:
        connection.send_error(
            msg["id"], "not_configured", "Codex Bridge is not configured"
        )
        return
    if runtime.event_broker is None:
        connection.send_result(
            msg["id"],
            {
                "state": "legacy_polling",
                "phase": "legacy",
                "retry_count": 0,
                "cursor": 0,
            },
        )
        return
    connection.send_result(msg["id"], dict(runtime.event_broker.connection_status))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/get_auth_status"})
@websocket_api.async_response
async def ws_get_auth_status(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass, connection, msg, lambda client: client.async_get_auth_status()
    )


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


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/cancel_auth_login"})
@websocket_api.async_response
async def ws_cancel_auth_login(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass, connection, msg, lambda client: client.async_cancel_auth_login()
    )


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/logout_auth"})
@websocket_api.async_response
async def ws_logout_auth(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass, connection, msg, lambda client: client.async_logout_auth()
    )


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/list_projects"})
@websocket_api.async_response
async def ws_list_projects(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass, connection, msg, lambda client: client.async_list_projects()
    )


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
        lambda client: client.async_create_folder(
            msg["parent_path"], msg["folder_name"]
        ),
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
        vol.Optional("mode", default="full-auto"): vol.In(
            ["observe", "edit", "full-auto"]
        ),
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
        vol.Optional("client_request_id"): str,
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
        lambda client: client.async_send_prompt(
            msg["thread_id"],
            msg["prompt"],
            client_request_id=msg.get("client_request_id"),
        ),
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
        vol.Optional("after", default=0): vol.All(int, vol.Range(min=0)),
        vol.Optional("scopes"): vol.All(
            [vol.In(["auth", "runtime", "thread"])], vol.Length(min=1, max=3)
        ),
        vol.Optional("thread_ids"): vol.All([str], vol.Length(min=1, max=64)),
        # Compatibility-only input for a v0 external Bridge.
        vol.Optional("thread_id"): str,
    }
)
@websocket_api.async_response
async def ws_get_events(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    async def _handler(client) -> dict[str, Any] | list[dict[str, Any]]:
        runtime = async_get_runtime(hass)
        if runtime.api_version == 0:
            if "thread_id" not in msg:
                raise BridgeApiError("legacy_thread_required")
            return await client.async_get_events(msg["thread_id"], msg["after"])
        scopes, thread_ids, compatibility_mode = _v1_event_filters(msg)
        try:
            batch = EventBatch.from_payload(
                await client.async_replay_events(
                    after=msg["after"],
                    scopes=scopes,
                    thread_ids=thread_ids,
                )
            )
        except BridgeApiGoneError as error:
            snapshot = _snapshot_result(error)
            if compatibility_mode:
                return [_legacy_snapshot_event(snapshot)]
            return {
                "events": [],
                "next_cursor": snapshot["cursor"],
                "minimum_cursor": snapshot["minimum_cursor"],
                "has_more": False,
                "heartbeat": False,
                "snapshot_required": snapshot,
            }
        if compatibility_mode:
            return [_legacy_thread_event(event) for event in batch.events]
        return {
            "events": [event.as_dict() for event in batch.events],
            "next_cursor": batch.next_cursor,
            "minimum_cursor": batch.minimum_cursor,
            "has_more": batch.has_more,
            "heartbeat": batch.heartbeat,
        }

    await _async_handle(hass, connection, msg, _handler)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/subscribe_events",
        vol.Optional("after", default=0): vol.All(int, vol.Range(min=0)),
        vol.Optional("scopes"): vol.All(
            [vol.In(["auth", "runtime", "thread"])], vol.Length(min=1, max=3)
        ),
        vol.Optional("thread_ids"): vol.All([str], vol.Length(min=1, max=64)),
        vol.Optional("thread_id"): str,
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
    except RuntimeError:
        connection.send_error(
            msg["id"], "not_configured", "Codex Bridge is not configured"
        )
        return

    if runtime.api_version == 1:
        if runtime.event_broker is None:
            connection.send_error(
                msg["id"], "bridge_error", "Bridge event service is unavailable"
            )
            return
        try:
            scopes, thread_ids, compatibility_mode = _v1_event_filters(msg)
            subscription = runtime.event_broker.subscribe(
                after=msg["after"],
                scopes=scopes,
                thread_ids=thread_ids,
            )
        except (EndpointError, RuntimeError, ValueError):
            connection.send_error(
                msg["id"], "invalid_event_filter", "Event subscription is invalid"
            )
            return

        async def _forward_v1() -> None:
            try:
                while not subscription.closed:
                    envelope = await subscription.get()
                    event = envelope.get("event")
                    if isinstance(event, EventRecord):
                        if compatibility_mode:
                            connection.send_event(
                                msg["id"], _legacy_thread_event(event)
                            )
                        else:
                            connection.send_event(
                                msg["id"],
                                {"type": "event", "event": event.as_dict()},
                            )
                    elif compatibility_mode and envelope.get("type") == "snapshot_required":
                        connection.send_event(
                            msg["id"],
                            _legacy_snapshot_event(
                                {
                                    "cursor": envelope["cursor"],
                                    "minimum_cursor": envelope["cursor"],
                                    "scope": envelope.get("scope", "global"),
                                    **(
                                        {"thread_id": envelope["thread_id"]}
                                        if "thread_id" in envelope
                                        else {}
                                    ),
                                }
                            ),
                        )
                    elif compatibility_mode and envelope.get("type") == "stream_status":
                        connection.send_event(
                            msg["id"],
                            {
                                "event_type": "bridge.error",
                                "payload": {
                                    "code": envelope.get("state", "bridge_error"),
                                    "error": "Bridge live updates stopped; polling will retry.",
                                },
                            },
                        )
                    elif not compatibility_mode:
                        connection.send_event(msg["id"], envelope)
            except asyncio.CancelledError:
                raise
            except BridgeApiError:
                connection.send_event(
                    msg["id"], {"type": "error", "code": "bridge_error"}
                )
            finally:
                subscription.close()

        task = hass.async_create_background_task(
            _forward_v1(), "codex_bridge_websocket_events"
        )

        def _unsubscribe_v1() -> None:
            subscription.close()
            task.cancel()

        connection.subscriptions[msg["id"]] = _unsubscribe_v1
        connection.send_result(
            msg["id"], {"subscription_id": msg["id"], "api_version": 1}
        )
        return

    if "thread_id" not in msg:
        connection.send_error(
            msg["id"],
            "legacy_thread_required",
            "A thread is required for a legacy Bridge",
        )
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
        except BridgeApiAuthError:
            connection.send_event(
                msg["id"],
                {"event_type": "bridge.error", "payload": {"code": "invalid_auth"}},
            )
        except BridgeApiConnectionError:
            connection.send_event(
                msg["id"],
                {"event_type": "bridge.error", "payload": {"code": "cannot_connect"}},
            )
        except BridgeApiError:
            connection.send_event(
                msg["id"],
                {"event_type": "bridge.error", "payload": {"code": "bridge_error"}},
            )

    task = hass.async_create_background_task(
        _forward_events(), "codex_bridge_legacy_websocket_events"
    )

    def _unsubscribe() -> None:
        task.cancel()

    connection.subscriptions[msg["id"]] = _unsubscribe
    connection.send_result(msg["id"], {"subscription_id": msg["id"], "api_version": 0})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/unsubscribe_events",
        vol.Required("subscription_id"): int,
    }
)
@websocket_api.async_response
async def ws_unsubscribe_events(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    unsubscribe = connection.subscriptions.pop(msg["subscription_id"], None)
    if unsubscribe is not None:
        unsubscribe()
    connection.send_result(msg["id"], {"unsubscribed": unsubscribe is not None})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/list_pending_interactions",
        vol.Optional("thread_id"): str,
    }
)
@websocket_api.async_response
async def ws_list_pending_interactions(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_list_pending_interactions(
            thread_id=msg.get("thread_id")
        ),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/decide_interaction",
        vol.Required("interaction_id"): str,
        vol.Required("thread_id"): str,
        vol.Required("run_id"): str,
        vol.Required("turn_id"): str,
        vol.Required("item_id"): str,
        vol.Required("decision"): vol.In(["accept", "decline", "cancel"]),
        vol.Required("client_request_id"): str,
    }
)
@websocket_api.async_response
async def ws_decide_interaction(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_decide_interaction(
            msg["interaction_id"],
            **{
                key: msg[key]
                for key in (
                    "thread_id",
                    "run_id",
                    "turn_id",
                    "item_id",
                    "decision",
                    "client_request_id",
                )
            },
        ),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/answer_interaction",
        vol.Required("interaction_id"): str,
        vol.Required("thread_id"): str,
        vol.Required("run_id"): str,
        vol.Required("turn_id"): str,
        vol.Required("item_id"): str,
        vol.Required("answers"): vol.All(
            [
                {
                    vol.Required("question_id"): str,
                    vol.Required("values"): vol.All(
                        [vol.All(str, vol.Length(min=1, max=4096))],
                        vol.Length(min=1, max=32),
                    ),
                }
            ],
            vol.Length(min=1, max=32),
        ),
        vol.Required("client_request_id"): str,
    }
)
@websocket_api.async_response
async def ws_answer_interaction(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_answer_interaction(
            msg["interaction_id"],
            **{
                key: msg[key]
                for key in (
                    "thread_id",
                    "run_id",
                    "turn_id",
                    "item_id",
                    "answers",
                    "client_request_id",
                )
            },
        ),
    )


def _v1_event_filters(
    msg: dict[str, Any],
) -> tuple[frozenset[str] | None, frozenset[str] | None, bool]:
    """Normalize v1 filters while preserving the pre-v1 panel during migration."""

    if "thread_id" in msg and "thread_ids" in msg:
        raise ValueError("thread filters conflict")
    compatibility_mode = "thread_id" in msg
    scopes = frozenset(msg["scopes"]) if "scopes" in msg else None
    thread_ids = (
        frozenset({msg["thread_id"]})
        if compatibility_mode
        else frozenset(msg["thread_ids"])
        if "thread_ids" in msg
        else None
    )
    if compatibility_mode and scopes is None:
        scopes = frozenset({"thread"})
    if thread_ids is not None and scopes is not None and "thread" not in scopes:
        raise ValueError("thread filters require thread scope")
    return scopes, thread_ids, compatibility_mode


def _legacy_thread_event(event: EventRecord) -> dict[str, Any]:
    """Project a v1 thread event into the retiring panel's event shape."""

    return {
        "event_id": event.event_id,
        "sequence": event.cursor,
        "event_type": event.event_type,
        "payload": dict(event.payload),
        "timestamp": event.timestamp,
    }


def _snapshot_result(error: BridgeApiGoneError) -> dict[str, Any]:
    """Project only validated snapshot guidance from a compacted journal."""

    problem = error.problem
    if (
        problem is None
        or not problem.snapshot_required
        or problem.snapshot_cursor is None
        or problem.snapshot_cursor > BRIDGE_EVENT_CURSOR_MAX
        or (problem.minimum_cursor or 0) > BRIDGE_EVENT_CURSOR_MAX
    ):
        raise error
    cursor = max(problem.snapshot_cursor, problem.minimum_cursor or 0)
    snapshot: dict[str, Any] = {
        "cursor": cursor,
        "minimum_cursor": (
            problem.minimum_cursor
            if problem.minimum_cursor is not None
            else cursor
        ),
        "scope": problem.scope or "global",
    }
    if problem.thread_id is not None:
        snapshot["thread_id"] = problem.thread_id
    return snapshot


def _legacy_snapshot_event(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Advance the retiring panel cursor without exposing an upstream error."""

    cursor = snapshot["cursor"]
    return {
        "event_id": f"snapshot_{cursor}",
        "sequence": cursor,
        "event_type": "bridge.snapshot_required",
        "payload": {
            "code": "snapshot_required",
            "scope": snapshot["scope"],
            **(
                {"thread_id": snapshot["thread_id"]}
                if "thread_id" in snapshot
                else {}
            ),
        },
        "timestamp": "",
    }


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
