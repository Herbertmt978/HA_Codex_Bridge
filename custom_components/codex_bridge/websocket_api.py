import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import probatio

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.components.websocket_api.decorators import (
    async_response,
    require_admin,
    websocket_command,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import VolDictType

from .bridge_api import (
    BridgeApiAuthError,
    BridgeApiClient,
    BridgeApiConnectionError,
    BridgeApiError,
    BridgeApiGoneError,
)
from .const import BRIDGE_EVENT_CURSOR_MAX, DOMAIN
from .event_broker import EventBatch, EventRecord
from .protocol import EndpointError
from .runtime import CodexBridgeRuntime, async_get_runtime

_INTERACTION_ERROR_MESSAGES = {
    "mcp_request_invalid": "Review the MCP answer and try again",
    "interaction_already_resolved": "This Codex request was already resolved",
    "interaction_kind_mismatch": "This response does not match the Codex request",
    "interaction_not_found": "This Codex request is no longer available",
    "interaction_outcome_unknown": "The response outcome could not be confirmed",
    "interaction_stale": "This Codex request is no longer active",
    "interaction_thread_mismatch": "This Codex request belongs to another chat",
    "runtime_request_conflict": "This response conflicts with an earlier request",
    "turn_changed": "The active Codex turn has changed",
}
_ARTIFACT_ERROR_MESSAGES = {
    "reservation_conflict": "Workspace files are temporarily unavailable while Codex is working",
}
_FEATURE_ERROR_MESSAGES = {
    "subagents_unavailable": "Individual subagents are unavailable in this runtime",
    "child_not_found": "This child is no longer retained in this chat",
    "child_stale": "This child's ownership or runtime changed. Refresh and verify its status",
    "child_ownership_conflict": "The runtime could not verify this child's parent and workspace",
    "child_revision_conflict": "This child changed. Refresh and verify its status before stopping it",
    "child_stop_unavailable": "This child has no verified running turn to stop",
    "child_turn_changed": "This child's active turn changed. Verify its status before stopping it",
    "child_request_capacity": "Child control history is full. Existing uncertain outcomes must be resolved first",
    "child_request_conflict": "This action conflicts with an earlier child request",
    "invalid_child_request": "Review the child action and try again",
    "child_follow_up_unavailable": "Direct child follow-up is unavailable in this runtime",
    "child_control_uncertain": "The stop outcome could not be confirmed. Verify this child's status",
    "terminal_unavailable": "Terminal unavailable. Close any running terminal or Codex turn, and select an editable chat.",
    "capability_unavailable": "This App version does not support this feature. Update it and try again",
    "host_access_unavailable": "Host access is unavailable or consent changed. Open Settings to review it.",
    "goal_revision_conflict": "The goal changed; refresh before trying again",
    "goal_conflict": "Pause before editing; finished goals need a new goal",
    "goals_unavailable": "This App cannot manage goals for this chat",
    "goal_invalid": "Review the goal text and completion confirmation",
    "goal_chat_not_found": "The chat or project no longer exists",
    "goal_context_stale": "The accepted goal changed; review it and send the retained prompt again",
    "stale_context": "Workspace context changed. Refresh or remove it, inspect the selection and send again.",
    "workspace_context_not_found": "The workspace file is no longer available. Choose another file or remove its context.",
    "unsafe_workspace_context_entry": "Choose an available file inside this chat's workspace.",
    "workspace_context_unavailable": "Workspace context is unavailable. Check the App and refresh the selection.",
    "workspace_context_limit_exceeded": "Workspace context is too large. Choose fewer files or a smaller excerpt.",
    "workspace_context_not_text": "Choose a supported text file for workspace context.",
    "workspace_context_range_unavailable": "The selected lines are unavailable. Review the inclusive line range.",
    "elapsed_time_limit_invalid": "Choose a shorter elapsed-time limit or remove it and send again.",
    "automation_conflict": "The automation is busy or must be paused first",
    "automation_invalid": "The automation settings are invalid",
    "automation_not_found": "The automation no longer exists",
    "automation_revision_conflict": "The automation changed; refresh and try again",
    "capabilities_conflict": "Codex is busy; try this change after the current run finishes",
    "capabilities_invalid": "The capability settings are invalid",
    "capabilities_unavailable": "Codex capabilities are temporarily unavailable",
    "navigation_revision_conflict": "This chat changed; refresh and try again",
    "section_revision_conflict": "This section changed; refresh and try again",
    "chat_section_conflict": "The section could not be changed. Refresh and try again",
    "chat_section_not_found": "The selected section no longer exists",
    "chat_operations_unavailable": "This App version does not support chat organisation. Update it and try again",
    "runtime_thread_operation_conflict": "This chat or destination changed; refresh and try again",
    "runtime_thread_operation_unknown": "Codex could not confirm the chat operation. Refresh before retrying",
    "thread_has_scheduled_automation": "Retarget or remove scheduled runs before moving this chat",
    "agents_unavailable": "The selected AGENTS.md file is unavailable",
    "mcp_config_conflict": "Codex is busy or the MCP configuration changed",
    "mcp_restart_required": "MCP configuration could not be restored. Restart the Codex Bridge App before continuing",
    "mcp_disabled": "Enable MCP in the Codex Bridge App configuration and restart",
    "mcp_elicitation_unavailable": "MCP configuration is unavailable until server prompts can be safely declined",
    "mcp_request_invalid": "The MCP server settings are invalid",
    "mcp_runtime_invalid": "Codex returned an invalid MCP response",
    "mcp_server_not_found": "The MCP server no longer exists",
    "mcp_unavailable": "MCP configuration is temporarily unavailable",
    "mcp_local_disabled": "Enable local MCP connections in the App configuration and restart it",
    "mcp_stdio_disabled": "Enable isolated stdio MCP in the Codex Bridge App and restart it",
    "mcp_stdio_unavailable": "Isolated stdio MCP is unavailable. Check the App's worker status and restart it if needed",
    "mcp_package_invalid": "This package could not be verified. Refresh the catalogue or update the App",
    "mcp_package_not_found": "This package revision is not in the installed App. Refresh the catalogue",
    "reset_credit_unavailable": "That reset credit is unavailable. Refresh usage and check again.",
    "account_profile_invalid": "Review the saved account and try again",
    "account_profile_reauthentication_required": "This saved account needs a fresh sign-in",
    "account_profiles_unavailable": "Saved accounts are unavailable in this App version",
    "git_base_ref_required": "Choose a base branch before reviewing branch changes",
    "git_state_changed": "Git changed while you were opening the diff. Refresh the file list and try again",
    "git_unavailable": "Git review is unavailable in this App",
    "chat_context_changed": "Previous chat context changed. Refresh and review it before sending.",
    "chat_context_deleted": "The source chat was deleted. Remove its context.",
    "chat_context_expired": "The source message is no longer retained. Choose another message or remove it.",
    "chat_context_inaccessible": "The source or destination is inaccessible. Review access or remove the context.",
    "chat_context_unavailable": "Previous chat context is unavailable in this App.",
    "chat_context_destination_unavailable": "Choose a different ordinary destination chat.",
    "chat_context_empty": "This chat has no retained public messages.",
    "chat_context_limit_exceeded": "Context is too large. Choose fewer items or a smaller excerpt.",
    "chat_context_range_unavailable": "The selected excerpt is no longer available. Review its range.",
    "collaboration_mode_requires_queue": "Choose Queue to change Plan or Execute mode after the active response",
    "web_search_requires_queue": "Choose Queue or wait for the active response to finish to change web search",
    "queued_prompt_not_found": "This message has already started or was removed. Refresh the queue.",
    "queued_prompt_revision_conflict": "This queued message changed. Refresh the queue before saving again.",
    "client_request_id_required": "The queued message could not be confirmed. Retry from the chat.",
}


def async_register_websocket_commands(hass: HomeAssistant) -> None:
    commands = (
        ws_get_config,
        ws_get_status,
        ws_consume_reset_credit,
        ws_get_event_status,
        ws_get_auth_status,
        ws_start_auth_login,
        ws_cancel_auth_login,
        ws_logout_auth,
        ws_list_account_profiles,
        ws_account_profile_details,
        ws_save_account_profile,
        ws_switch_account_profile,
        ws_prepare_new_account_login,
        ws_remove_account_profile,
        ws_list_projects,
        ws_create_project,
        ws_update_project,
        ws_archive_project,
        ws_restore_project,
        ws_delete_project,
        ws_browse_paths,
        ws_create_folder,
        ws_list_threads,
        ws_search_transcript,
        ws_search_conversation,
        ws_attention_inbox,
        ws_get_goal,
        ws_goal_action,
        ws_child_agents,
        ws_child_agent_action,
        ws_usage_history,
        ws_workspace_context,
        ws_chat_context,
        ws_read_chat_context,
        ws_read_workspace_context,
        ws_get_transcript_message,
        ws_git_review,
        ws_git_context,
        ws_list_chat_sections,
        ws_create_chat_section,
        ws_update_chat_section,
        ws_delete_chat_section,
        ws_get_thread,
        ws_create_thread,
        ws_update_thread,
        ws_archive_thread,
        ws_restore_thread,
        ws_delete_thread,
        ws_fork_thread,
        ws_move_thread_project,
        ws_send_prompt,
        ws_prompt_queue,
        ws_update_queued_prompt,
        ws_cancel_queued_prompt,
        ws_cancel_run,
        ws_get_events,
        ws_subscribe_events,
        ws_unsubscribe_events,
        ws_list_pending_interactions,
        ws_decide_interaction,
        ws_answer_interaction,
        ws_answer_mcp_form,
        ws_list_artifacts,
        ws_preview_artifact,
        ws_create_workspace_archive,
        ws_host_access,
        ws_enable_host_access,
        ws_revoke_host_access,
        ws_list_automations,
        ws_get_automation,
        ws_preview_automation_schedule,
        ws_create_automation,
        ws_update_automation,
        ws_pause_automation,
        ws_resume_automation,
        ws_delete_automation,
        ws_run_automation,
        ws_list_automation_runs,
        ws_list_skills,
        ws_set_skill,
        ws_create_skill,
        ws_delete_skill,
        ws_list_plugins,
        ws_install_plugin,
        ws_uninstall_plugin,
        ws_list_marketplaces,
        ws_add_marketplace,
        ws_remove_marketplace,
        ws_upgrade_marketplace,
        ws_list_mcp,
        ws_list_stdio_packages,
        ws_add_stdio_mcp,
        ws_update_stdio_mcp,
        ws_rollback_stdio_mcp,
        ws_list_mcp_tools,
        ws_set_mcp_tools,
        ws_lock_down_mcp_tools,
        ws_add_mcp,
        ws_remove_mcp,
        ws_login_mcp,
        ws_get_agents,
        ws_update_agents,
        ws_delete_agents,
        ws_terminal,
    )
    for command in commands:
        websocket_api.async_register_command(hass, require_admin(command))


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/terminal",
    probatio.Required("operation"): probatio.In({"open", "read", "write", "resize", "close"}),
    probatio.Required("thread_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    probatio.Optional("session_id"): probatio.All(str, probatio.Length(min=32, max=32)),
    probatio.Optional("cols"): probatio.All(int, probatio.Range(min=20, max=300)),
    probatio.Optional("rows"): probatio.All(int, probatio.Range(min=2, max=100)),
    probatio.Optional("after"): probatio.All(int, probatio.Range(min=0, max=9_007_199_254_740_991)),
    probatio.Optional("sequence"): probatio.All(int, probatio.Range(min=1, max=9_007_199_254_740_991)),
    probatio.Optional("data"): probatio.All(str, probatio.Length(max=16 * 1024)),
})
@async_response
async def ws_terminal(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    payload = {key: value for key, value in msg.items() if key not in {"id", "type", "operation"}}
    await _async_handle(hass, connection, msg,
                        lambda client: client.async_terminal(msg["operation"], payload))


async def _async_handle(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
    handler: Callable[[BridgeApiClient], Awaitable[Any]] | None,
    *,
    safe_error_messages: dict[str, str] | None = None,
    after_success: Callable[[CodexBridgeRuntime], Awaitable[None]] | None = None,
    runtime_handler: Callable[[CodexBridgeRuntime], Awaitable[Any]] | None = None,
) -> None:
    try:
        runtime = async_get_runtime(hass)
        if runtime_handler is not None:
            result = await runtime_handler(runtime)
        else:
            # Every command passes one of the two handlers.
            assert handler is not None
            result = await handler(runtime.client)
    except BridgeApiAuthError:
        connection.send_error(msg["id"], "invalid_auth", "Bridge authentication failed")
    except BridgeApiConnectionError:
        connection.send_error(msg["id"], "cannot_connect", "Bridge is unavailable")
    except EndpointError:
        connection.send_error(msg["id"], "bridge_error", "Bridge response is invalid")
    except BridgeApiError as err:
        messages = {
            **_FEATURE_ERROR_MESSAGES,
            **(safe_error_messages or {}),
        }
        if err.code in messages:
            connection.send_error(msg["id"], err.code, messages[err.code])
        else:
            connection.send_error(msg["id"], "bridge_error", "Bridge request failed")
    except RuntimeError:
        connection.send_error(
            msg["id"], "not_configured", "Codex Bridge is not configured"
        )
    except ValueError:
        connection.send_error(msg["id"], "invalid_request", "Request is invalid")
    else:
        if after_success is not None:
            await after_success(runtime)
        connection.send_result(msg["id"], result)


@websocket_command({probatio.Required("type"): f"{DOMAIN}/get_config"})
@async_response
async def ws_get_config(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    async def _handler(runtime: CodexBridgeRuntime) -> dict[str, Any]:
        return {
            "panel_title": runtime.title,
            "connection_type": runtime.connection_type,
            "api_version": runtime.api_version,
            "capabilities": list(runtime.capabilities),
            "web_search_mode": runtime.web_search_mode,
        }

    await _async_handle(hass, connection, msg, None, runtime_handler=_handler)


@websocket_command({probatio.Required("type"): f"{DOMAIN}/get_status"})
@async_response
async def ws_get_status(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    async def _handler(runtime: CodexBridgeRuntime) -> dict[str, Any]:
        status = await runtime.client.async_get_status()
        await runtime.async_refresh_capabilities(
            force=runtime.capability_refresh_is_urgent(status)
        )
        return status

    await _async_handle(hass, connection, msg, None, runtime_handler=_handler)


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/consume_reset_credit",
    probatio.Required("credit_id"): probatio.Match(r"^[\x21-\x7e]{1,256}$"),
    probatio.Required("idempotency_key"): probatio.Match(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$"),
})
@async_response
async def ws_consume_reset_credit(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass, connection, msg,
        lambda client: client.async_consume_reset_credit(
            msg["credit_id"], msg["idempotency_key"],
        ),
    )


@websocket_command({probatio.Required("type"): f"{DOMAIN}/get_event_status"})
@async_response
async def ws_get_event_status(
    hass: HomeAssistant,
    connection: ActiveConnection,
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


@websocket_command({probatio.Required("type"): f"{DOMAIN}/get_auth_status"})
@async_response
async def ws_get_auth_status(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass, connection, msg, lambda client: client.async_get_auth_status()
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/start_auth_login",
        probatio.Optional("force_logout", default=False): bool,
    }
)
@async_response
async def ws_start_auth_login(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_start_auth_login(msg.get("force_logout", False)),
    )


@websocket_command({probatio.Required("type"): f"{DOMAIN}/cancel_auth_login"})
@async_response
async def ws_cancel_auth_login(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass, connection, msg, lambda client: client.async_cancel_auth_login()
    )


@websocket_command({probatio.Required("type"): f"{DOMAIN}/logout_auth"})
@async_response
async def ws_logout_auth(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass, connection, msg, lambda client: client.async_logout_auth()
    )


@websocket_command({probatio.Required("type"): f"{DOMAIN}/list_account_profiles"})
@async_response
async def ws_list_account_profiles(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass, connection, msg, lambda client: client.async_list_account_profiles()
    )


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/save_account_profile",
    probatio.Required("label"): probatio.All(str, probatio.Length(min=1, max=60)),
})
@async_response
async def ws_save_account_profile(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass, connection, msg,
        lambda client: client.async_save_account_profile(msg["label"]),
    )


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/switch_account_profile",
    probatio.Required("profile_id"): probatio.Match(r"^[a-f0-9]{32}$"),
})
@async_response
async def ws_switch_account_profile(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass, connection, msg,
        lambda client: client.async_switch_account_profile(msg["profile_id"]),
    )


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/account_profile_details",
    probatio.Required("profile_id"): probatio.Match(r"^[a-f0-9]{32}$"),
})
@async_response
async def ws_account_profile_details(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass, connection, msg,
        lambda client: client.async_account_profile_details(msg["profile_id"]),
    )


@websocket_command({probatio.Required("type"): f"{DOMAIN}/prepare_new_account_login"})
@async_response
async def ws_prepare_new_account_login(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass, connection, msg,
        lambda client: client.async_prepare_new_account_login(),
    )


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/remove_account_profile",
    probatio.Required("profile_id"): probatio.Match(r"^[a-f0-9]{32}$"),
})
@async_response
async def ws_remove_account_profile(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass, connection, msg,
        lambda client: client.async_remove_account_profile(msg["profile_id"]),
    )


@websocket_command({probatio.Required("type"): f"{DOMAIN}/list_projects"})
@async_response
async def ws_list_projects(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass, connection, msg, lambda client: client.async_list_projects()
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/create_project",
        probatio.Required("name"): str,
        probatio.Optional("root_path"): probatio.Any(None, str),
        probatio.Optional("default_model"): str,
        probatio.Optional("default_thinking_level"): str,
    }
)
@async_response
async def ws_create_project(
    hass: HomeAssistant,
    connection: ActiveConnection,
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


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/update_project",
        probatio.Required("project_id"): str,
        probatio.Optional("name"): probatio.Any(None, str),
        probatio.Optional("root_path"): probatio.Any(None, str),
        probatio.Optional("default_model"): probatio.Any(None, str),
        probatio.Optional("default_thinking_level"): probatio.Any(None, str),
    }
)
@async_response
async def ws_update_project(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    updates: dict[str, Any] = {
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


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/archive_project",
        probatio.Required("project_id"): str,
    }
)
@async_response
async def ws_archive_project(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_archive_project(msg["project_id"]),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/restore_project",
        probatio.Required("project_id"): str,
    }
)
@async_response
async def ws_restore_project(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_restore_project(msg["project_id"]),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/delete_project",
        probatio.Required("project_id"): str,
    }
)
@async_response
async def ws_delete_project(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_delete_project(msg["project_id"]),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/browse_paths",
        probatio.Optional("path"): probatio.Any(None, str),
    }
)
@async_response
async def ws_browse_paths(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_browse_paths(msg.get("path")),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/create_folder",
        probatio.Required("parent_path"): str,
        probatio.Required("folder_name"): str,
    }
)
@async_response
async def ws_create_folder(
    hass: HomeAssistant,
    connection: ActiveConnection,
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


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/list_threads",
        probatio.Optional("include_archived", default=False): bool,
    }
)
@async_response
async def ws_list_threads(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_list_threads(msg["include_archived"]),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/search_transcript",
        probatio.Required("query"): probatio.All(str, probatio.Length(min=1, max=256)),
        probatio.Optional("include_archived", default=False): bool,
        probatio.Optional("limit", default=50): probatio.All(int, probatio.Range(min=1, max=100)),
        probatio.Optional("before_cursor"): probatio.All(int, probatio.Range(min=1, max=9_007_199_254_740_991)),
    }
)
@async_response
async def ws_search_transcript(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_search_transcript(
            msg["query"],
            include_archived=msg["include_archived"],
            limit=msg["limit"],
            before_cursor=msg.get("before_cursor"),
        ),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/get_transcript_message",
        probatio.Required("thread_id"): probatio.All(str, probatio.Length(min=1, max=128)),
        probatio.Required("sequence"): probatio.All(int, probatio.Range(min=1, max=9_007_199_254_740_991)),
    }
)
@async_response
async def ws_get_transcript_message(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_get_transcript_message(
            msg["thread_id"], msg["sequence"]
        ),
    )


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/git_context",
    probatio.Required("thread_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    probatio.Optional("base_ref"): probatio.All(str, probatio.Length(min=1, max=256)),
})
@async_response
async def ws_git_context(
    hass: HomeAssistant, connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass, connection, msg,
        lambda client: client.async_git_context(msg["thread_id"], base_ref=msg.get("base_ref")),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/git_review",
        probatio.Required("thread_id"): probatio.All(str, probatio.Length(min=1, max=128)),
        probatio.Required("scope"): probatio.In(["unstaged", "staged", "commit", "branch"]),
        probatio.Optional("base_ref"): probatio.All(str, probatio.Length(min=1, max=256)),
        probatio.Optional("commit_ref"): probatio.All(str, probatio.Length(min=1, max=256)),
        probatio.Optional("path"): probatio.All(str, probatio.Length(min=1, max=2048)),
        probatio.Optional("expected_state_token"): probatio.Match(r"^[a-f0-9]{64}$"),
    }
)
@async_response
async def ws_git_review(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_git_review(
            msg["thread_id"],
            scope=msg["scope"],
            base_ref=msg.get("base_ref"),
            commit_ref=msg.get("commit_ref"),
            path=msg.get("path"),
            expected_state_token=msg.get("expected_state_token"),
        ),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/get_thread",
        probatio.Required("thread_id"): str,
    }
)
@async_response
async def ws_get_thread(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_get_thread(msg["thread_id"]),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/create_thread",
        probatio.Required("title"): str,
        probatio.Optional("project_id"): probatio.Any(None, str),
        probatio.Optional("mode", default="full-auto"): probatio.In(
            ["observe", "edit", "full-auto", "haos-full-access"]
        ),
        probatio.Optional("host_access_grant"): probatio.Any(None, probatio.Match(r"^[a-f0-9]{32}$")),
        probatio.Optional("model_override"): probatio.Any(None, str),
        probatio.Optional("thinking_override"): probatio.Any(None, str),
    }
)
@async_response
async def ws_create_thread(
    hass: HomeAssistant,
    connection: ActiveConnection,
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
            **({"host_access_grant": msg["host_access_grant"]} if "host_access_grant" in msg else {}),
        ),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/update_thread",
        probatio.Required("thread_id"): str,
        probatio.Optional("title"): probatio.Any(None, str),
        probatio.Optional("mode"): probatio.In(["observe", "edit", "full-auto", "haos-full-access"]),
        probatio.Optional("host_access_grant"): probatio.Any(None, probatio.Match(r"^[a-f0-9]{32}$")),
        probatio.Optional("model_override"): probatio.Any(None, str),
        probatio.Optional("thinking_override"): probatio.Any(None, str),
        probatio.Optional("pinned"): bool,
        probatio.Optional("unread"): bool,
        probatio.Optional("section_id"): probatio.Any(None, probatio.All(str, probatio.Length(min=1, max=128))),
        probatio.Optional("navigation_revision"): probatio.All(int, probatio.Range(min=1)),
    }
)
@async_response
async def ws_update_thread(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    updates: dict[str, Any] = {
        key: msg[key]
        for key in ("title", "mode", "model_override", "thinking_override", "host_access_grant", "pinned", "unread", "section_id", "navigation_revision")
        if key in msg
    }
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_update_thread(msg["thread_id"], updates),
    )


@websocket_command({probatio.Required("type"): f"{DOMAIN}/list_chat_sections"})
@async_response
async def ws_list_chat_sections(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_list_chat_sections())


@websocket_command({probatio.Required("type"): f"{DOMAIN}/create_chat_section", probatio.Required("name"): probatio.All(str, probatio.Length(min=1, max=320))})
@async_response
async def ws_create_chat_section(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_create_chat_section(msg["name"]))


@websocket_command({probatio.Required("type"): f"{DOMAIN}/update_chat_section", probatio.Required("section_id"): probatio.All(str, probatio.Length(min=1, max=128)), probatio.Required("name"): probatio.All(str, probatio.Length(min=1, max=320)), probatio.Required("revision"): probatio.All(int, probatio.Range(min=1))})
@async_response
async def ws_update_chat_section(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_update_chat_section(msg["section_id"], msg["name"], msg["revision"]))


@websocket_command({probatio.Required("type"): f"{DOMAIN}/delete_chat_section", probatio.Required("section_id"): probatio.All(str, probatio.Length(min=1, max=128)), probatio.Required("revision"): probatio.All(int, probatio.Range(min=1))})
@async_response
async def ws_delete_chat_section(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_delete_chat_section(msg["section_id"], msg["revision"]))


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/archive_thread",
        probatio.Required("thread_id"): str,
    }
)
@async_response
async def ws_archive_thread(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_archive_thread(msg["thread_id"]),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/restore_thread",
        probatio.Required("thread_id"): str,
    }
)
@async_response
async def ws_restore_thread(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_restore_thread(msg["thread_id"]),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/delete_thread",
        probatio.Required("thread_id"): str,
    }
)
@async_response
async def ws_delete_thread(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_delete_thread(msg["thread_id"]),
    )


@websocket_command({probatio.Required("type"): f"{DOMAIN}/fork_thread", probatio.Required("thread_id"): str})
@async_response
async def ws_fork_thread(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_fork_thread(msg["thread_id"]),
        safe_error_messages={
            "not_found": "The requested chat or project no longer exists"
        },
    )


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/move_thread_project",
    probatio.Required("thread_id"): str,
    probatio.Required("project_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    probatio.Required("navigation_revision"): probatio.All(int, probatio.Range(min=1)),
    probatio.Optional("workspace_artifact_ids", default=[]): probatio.All(
        [probatio.All(str, probatio.Length(min=1, max=128))], probatio.Length(max=100)
    ),
})
@async_response
async def ws_move_thread_project(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_move_thread_project(
            msg["thread_id"],
            msg["project_id"],
            msg["navigation_revision"],
            msg["workspace_artifact_ids"],
        ),
        safe_error_messages={
            "not_found": "The requested chat or project no longer exists"
        },
    )


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/search_conversation",
    probatio.Required("thread_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    probatio.Required("query"): probatio.All(str, probatio.Length(min=1, max=256)),
    probatio.Optional("limit", default=50): probatio.All(int, probatio.Range(min=1, max=100)),
    probatio.Optional("before_cursor"): probatio.All(int, probatio.Range(min=1, max=9_007_199_254_740_991)),
})
@async_response
async def ws_search_conversation(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_search_conversation(
        msg["thread_id"], msg["query"], limit=msg["limit"], before_cursor=msg.get("before_cursor"),
    ))


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/get_goal",
    probatio.Required("thread_id"): probatio.Match(r"^[A-Za-z0-9_-]{1,128}$"),
})
@async_response
async def ws_get_goal(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_get_goal(msg["thread_id"]))


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/goal_action",
    probatio.Required("thread_id"): probatio.Match(r"^[A-Za-z0-9_-]{1,128}$"),
    probatio.Required("action"): probatio.In({"create", "edit", "progress", "pause", "resume", "complete", "cancel"}),
    probatio.Required("expected_revision"): probatio.All(int, probatio.Range(min=0)),
    probatio.Required("client_request_id"): probatio.Match(r"^[A-Za-z0-9_.:-]{1,128}$"),
    probatio.Optional("objective"): probatio.All(str, probatio.Length(min=1, max=4096)),
    probatio.Optional("completion_criteria"): probatio.All([probatio.All(str, probatio.Length(min=1, max=1024))], probatio.Length(min=1, max=16)),
    probatio.Optional("progress"): probatio.All(str, probatio.Length(max=8192)),
    probatio.Optional("completion_confirmed"): bool,
})
@async_response
async def ws_goal_action(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    payload = {key: value for key, value in msg.items() if key not in {"id", "type", "thread_id"}}
    await _async_handle(hass, connection, msg, lambda client: client.async_goal_action(msg["thread_id"], payload))


@websocket_command({probatio.Required("type"): f"{DOMAIN}/attention_inbox"})
@async_response
async def ws_attention_inbox(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_attention_inbox())


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/child_agents",
    probatio.Required("thread_id"): probatio.All(str, probatio.Length(min=1, max=128)),
})
@async_response
async def ws_child_agents(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_child_agents(msg["thread_id"]))


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/child_agent_action",
    probatio.Required("thread_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    probatio.Required("child_id"): probatio.Match(r"^[a-f0-9]{32}$"),
    probatio.Required("action"): probatio.In({"refresh", "stop"}),
    probatio.Optional("revision"): probatio.All(probatio.Coerce(int), probatio.Range(min=1)),
    probatio.Optional("client_request_id"): probatio.All(str, probatio.Length(min=1, max=256)),
})
@async_response
async def ws_child_agent_action(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_child_agent_action(
        msg["thread_id"], msg["child_id"], msg["action"],
        revision=msg.get("revision"), client_request_id=msg.get("client_request_id"),
    ))


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/usage_history",
    probatio.Optional("thread_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    probatio.Optional("project_id"): probatio.All(str, probatio.Length(min=1, max=128)),
})
@async_response
async def ws_usage_history(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_usage_history(
        thread_id=msg.get("thread_id"), project_id=msg.get("project_id"),
    ))


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/workspace_context",
    probatio.Required("thread_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    probatio.Optional("directory", default="."): probatio.All(str, probatio.Length(min=1, max=2048)),
})
@async_response
async def ws_workspace_context(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_workspace_context(
        msg["thread_id"], directory=msg["directory"],
    ))


_NULLABLE_CONTEXT_LINE = probatio.Any(None, probatio.All(int, probatio.Range(min=1, max=9_007_199_254_740_991)))


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/read_workspace_context",
    probatio.Required("thread_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    probatio.Required("path"): probatio.All(str, probatio.Length(min=1, max=2048)),
    probatio.Optional("start_line"): _NULLABLE_CONTEXT_LINE,
    probatio.Optional("end_line"): _NULLABLE_CONTEXT_LINE,
    probatio.Optional("expected_revision"): probatio.Match(r"^[a-f0-9]{64}$"),
})
@async_response
async def ws_read_workspace_context(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_read_workspace_context(
        msg["thread_id"], path=msg["path"], start_line=msg.get("start_line"),
        end_line=msg.get("end_line"), expected_revision=msg.get("expected_revision"),
    ))


def _bounded_prompt(value: str) -> str:
    if not value.strip() or len(value.encode("utf-8")) > 1024 * 1024:
        raise probatio.Invalid("prompt is blank or exceeds its limit")
    return value


_CHAT_CONTEXT_SELECTION: VolDictType = {
    probatio.Required("source_thread_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    probatio.Optional("message_sequence"): probatio.Any(None, probatio.All(int, probatio.Range(min=1, max=9_007_199_254_740_991))),
    probatio.Optional("start_char"): probatio.Any(None, probatio.All(int, probatio.Range(min=0, max=1_048_576))),
    probatio.Optional("end_char"): probatio.Any(None, probatio.All(int, probatio.Range(min=1, max=1_048_576))),
}


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/chat_context",
    probatio.Required("thread_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    probatio.Required("source_thread_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    probatio.Optional("before_sequence"): probatio.All(int, probatio.Range(min=1, max=9_007_199_254_740_991)),
})
@async_response
async def ws_chat_context(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_chat_context(
        msg["thread_id"], source_thread_id=msg["source_thread_id"], before_sequence=msg.get("before_sequence"),
    ))


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/read_chat_context",
    probatio.Required("thread_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    **_CHAT_CONTEXT_SELECTION,
})
@async_response
async def ws_read_chat_context(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_read_chat_context(
        msg["thread_id"], **{key: msg[key] for key in ("source_thread_id", "message_sequence", "start_char", "end_char") if key in msg},
    ))


_CHAT_CONTEXT_REFERENCE = probatio.Schema({
    **_CHAT_CONTEXT_SELECTION,
    probatio.Required("content_revision"): probatio.Match(r"^[a-f0-9]{64}$"),
})


_WORKSPACE_CONTEXT_REFERENCE = probatio.Schema({
    probatio.Required("path"): probatio.All(str, probatio.Length(min=1, max=2048)),
    probatio.Required("content_revision"): probatio.Match(r"^[a-f0-9]{64}$"),
    probatio.Optional("start_line"): _NULLABLE_CONTEXT_LINE,
    probatio.Optional("end_line"): _NULLABLE_CONTEXT_LINE,
})


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/send_prompt",
        probatio.Required("thread_id"): str,
        probatio.Required("prompt"): probatio.All(str, probatio.Length(min=1, max=1024 * 1024), _bounded_prompt),
        probatio.Optional("client_request_id"): str,
        probatio.Optional("follow_up_mode"): probatio.In({"queue", "steer"}),
        probatio.Optional("collaboration_mode"): probatio.In({"default", "plan"}),
        probatio.Optional("web_search"): probatio.In({"live", "disabled"}),
        probatio.Optional("max_duration_seconds"): probatio.All(int, probatio.Range(min=1, max=86_400)),
        probatio.Optional("workspace_context"): probatio.All([_WORKSPACE_CONTEXT_REFERENCE], probatio.Length(min=1, max=8)),
        probatio.Optional("chat_context"): probatio.All([_CHAT_CONTEXT_REFERENCE], probatio.Length(min=1, max=8)),
    }
)
@async_response
async def ws_send_prompt(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    explicit_mode_kwargs = {
        field: msg[field]
        for field in ("follow_up_mode", "collaboration_mode", "web_search", "workspace_context", "chat_context", "max_duration_seconds")
        if msg.get(field) is not None
    }

    async def _send(runtime: CodexBridgeRuntime) -> dict[str, Any]:
        if msg.get("follow_up_mode") is not None and not runtime.supports_capability(
            "prompt_queue_v1"
        ):
            raise BridgeApiError("capabilities_unavailable")
        if msg.get("collaboration_mode") is not None and not runtime.supports_capability(
            "plan_mode_v1"
        ):
            raise BridgeApiError("capabilities_unavailable")
        if msg.get("web_search") is not None and not runtime.supports_capability(
            "web_search_v1"
        ):
            raise BridgeApiError("capabilities_unavailable")
        if msg.get("chat_context") is not None and not runtime.supports_capability("chat_context_v1"):
            raise BridgeApiError("capabilities_unavailable")
        if msg.get("workspace_context") is not None and not runtime.supports_capability("workspace_context_v1"):
            raise BridgeApiError("capabilities_unavailable")
        if msg.get("max_duration_seconds") is not None and not runtime.supports_capability("elapsed_time_limit_v1"):
            raise BridgeApiError("capabilities_unavailable")
        return await runtime.client.async_send_prompt(
            msg["thread_id"],
            msg["prompt"],
            client_request_id=msg.get("client_request_id"),
            **{**runtime.web_search_payload(), **explicit_mode_kwargs},
        )

    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_send_prompt(
            msg["thread_id"],
            msg["prompt"],
            client_request_id=msg.get("client_request_id"),
            **explicit_mode_kwargs,
        ),
        runtime_handler=_send,
        safe_error_messages={
            "assist_policy_invalid": "Messages in this conversation are managed by Assist. Continue in Assist, or start a new chat.",
        },
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/prompt_queue",
        probatio.Required("thread_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    }
)
@async_response
async def ws_prompt_queue(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_get_prompt_queue(msg["thread_id"]),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/update_queued_prompt",
        probatio.Required("thread_id"): probatio.All(str, probatio.Length(min=1, max=128)),
        probatio.Required("run_id"): probatio.All(str, probatio.Length(min=1, max=128)),
        probatio.Required("prompt"): probatio.All(str, probatio.Length(min=1, max=1024 * 1024)),
        probatio.Required("expected_revision"): probatio.All(int, probatio.Range(min=1)),
    }
)
@async_response
async def ws_update_queued_prompt(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_update_queued_prompt(
            msg["thread_id"],
            msg["run_id"],
            msg["prompt"],
            msg["expected_revision"],
        ),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/cancel_queued_prompt",
        probatio.Required("thread_id"): probatio.All(str, probatio.Length(min=1, max=128)),
        probatio.Required("run_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    }
)
@async_response
async def ws_cancel_queued_prompt(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_cancel_queued_prompt(
            msg["thread_id"], msg["run_id"]
        ),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/cancel_run",
        probatio.Required("thread_id"): str,
    }
)
@async_response
async def ws_cancel_run(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_cancel_run(msg["thread_id"]),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/get_events",
        probatio.Optional("after", default=0): probatio.All(int, probatio.Range(min=0)),
        probatio.Optional("scopes"): probatio.All(
            [probatio.In(["auth", "runtime", "thread"])], probatio.Length(min=1, max=3)
        ),
        probatio.Optional("thread_ids"): probatio.All([str], probatio.Length(min=1, max=64)),
        # Compatibility-only input for a v0 external Bridge.
        probatio.Optional("thread_id"): str,
    }
)
@async_response
async def ws_get_events(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    async def _handler(client: BridgeApiClient) -> dict[str, Any] | list[dict[str, Any]]:
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


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/subscribe_events",
        probatio.Optional("after", default=0): probatio.All(int, probatio.Range(min=0)),
        probatio.Optional("scopes"): probatio.All(
            [probatio.In(["auth", "runtime", "thread"])], probatio.Length(min=1, max=3)
        ),
        probatio.Optional("thread_ids"): probatio.All([str], probatio.Length(min=1, max=64)),
        probatio.Optional("thread_id"): str,
    }
)
@async_response
async def ws_subscribe_events(
    hass: HomeAssistant,
    connection: ActiveConnection,
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
                    elif (
                        compatibility_mode
                        and envelope.get("type") == "snapshot_required"
                    ):
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


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/unsubscribe_events",
        probatio.Required("subscription_id"): int,
    }
)
@async_response
async def ws_unsubscribe_events(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    unsubscribe = connection.subscriptions.pop(msg["subscription_id"], None)
    if unsubscribe is not None:
        unsubscribe()
    connection.send_result(msg["id"], {"unsubscribed": unsubscribe is not None})


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/list_pending_interactions",
        probatio.Optional("thread_id"): str,
    }
)
@async_response
async def ws_list_pending_interactions(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_list_pending_interactions(
            thread_id=msg.get("thread_id")
        ),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/decide_interaction",
        probatio.Required("interaction_id"): str,
        probatio.Required("thread_id"): str,
        probatio.Required("decision"): probatio.In(["accept", "decline", "cancel"]),
        probatio.Required("client_request_id"): str,
    }
)
@async_response
async def ws_decide_interaction(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
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
                    "decision",
                    "client_request_id",
                )
            },
        ),
        safe_error_messages=_INTERACTION_ERROR_MESSAGES,
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/answer_interaction",
        probatio.Required("interaction_id"): str,
        probatio.Required("thread_id"): str,
        probatio.Required("answers"): probatio.All(
            [
                {
                    probatio.Required("question_id"): str,
                    probatio.Required("values"): probatio.All(
                        [probatio.All(str, probatio.Length(min=1, max=4096))],
                        probatio.Length(min=1, max=32),
                    ),
                }
            ],
            probatio.Length(min=1, max=32),
        ),
        probatio.Required("client_request_id"): str,
    }
)
@async_response
async def ws_answer_interaction(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
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
                    "answers",
                    "client_request_id",
                )
            },
        ),
        safe_error_messages=_INTERACTION_ERROR_MESSAGES,
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/answer_mcp_form",
        probatio.Required("interaction_id"): str,
        probatio.Required("thread_id"): str,
        probatio.Required("content"): dict,
        probatio.Required("client_request_id"): str,
    }
)
@async_response
async def ws_answer_mcp_form(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_answer_mcp_form(
            msg["interaction_id"],
            thread_id=msg["thread_id"],
            content=msg["content"],
            client_request_id=msg["client_request_id"],
        ),
        safe_error_messages=_INTERACTION_ERROR_MESSAGES,
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
            problem.minimum_cursor if problem.minimum_cursor is not None else cursor
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
            **({"thread_id": snapshot["thread_id"]} if "thread_id" in snapshot else {}),
        },
        "timestamp": "",
    }


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/list_artifacts",
        probatio.Required("thread_id"): str,
    }
)
@async_response
async def ws_list_artifacts(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_list_artifacts(msg["thread_id"]),
        safe_error_messages=_ARTIFACT_ERROR_MESSAGES,
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/preview_artifact",
        probatio.Required("thread_id"): str,
        probatio.Required("artifact_id"): str,
    }
)
@async_response
async def ws_preview_artifact(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_preview_artifact(msg["thread_id"], msg["artifact_id"]),
        safe_error_messages=_ARTIFACT_ERROR_MESSAGES,
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/create_workspace_archive",
        probatio.Required("thread_id"): str,
    }
)
@async_response
async def ws_create_workspace_archive(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_create_workspace_archive(msg["thread_id"]),
    )


async def _automation_refresh(runtime: CodexBridgeRuntime) -> None:
    scheduler = getattr(runtime, "automation_scheduler", None)
    if scheduler is not None:
        try:
            await scheduler.async_refresh()
        except (BridgeApiError, ValueError):
            # The Bridge mutation succeeded; a later scheduler retry can
            # reconcile the local timer without turning it into a UI failure.
            scheduler.schedule_reconciliation()
            return


@websocket_command({probatio.Required("type"): f"{DOMAIN}/list_automations"})
@async_response
async def ws_list_automations(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass, connection, msg, lambda client: client.async_list_automations()
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/get_automation",
        probatio.Required("automation_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    }
)
@async_response
async def ws_get_automation(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_get_automation(msg["automation_id"]),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/preview_automation_schedule",
        probatio.Required("schedule"): probatio.All(dict, probatio.Length(max=16)),
    }
)
@async_response
async def ws_preview_automation_schedule(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass, connection, msg,
        lambda client: client.async_preview_automation_schedule(msg["schedule"]),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/create_automation",
        probatio.Optional("client_request_id"): probatio.Match(r"^[a-f0-9]{32}$"),
        probatio.Required("name"): probatio.All(str, probatio.Length(min=1, max=160)),
        probatio.Required("prompt"): probatio.All(str, probatio.Length(min=1, max=1_048_576)),
        probatio.Required("target"): probatio.All(dict, probatio.Length(max=16)),
        probatio.Required("schedule"): probatio.All(dict, probatio.Length(max=16)),
        probatio.Optional("mode", default="observe"): probatio.In(
            ["observe", "edit", "full-auto", "haos-full-access"]
        ),
        probatio.Optional("host_access_grant"): probatio.Any(None, probatio.Match(r"^[a-f0-9]{32}$")),
        probatio.Optional("host_unattended_approved"): bool,
        probatio.Optional("model"): probatio.Any(None, probatio.All(str, probatio.Length(min=1, max=160))),
        probatio.Optional("thinking"): probatio.Any(
            None, probatio.All(str, probatio.Length(min=1, max=160))
        ),
        probatio.Optional("notifications"): probatio.All(dict, probatio.Length(max=4)),
    }
)
@async_response
async def ws_create_automation(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    payload: dict[str, Any] = {
        key: msg[key]
        for key in ("name", "prompt", "target", "schedule", "mode", "model", "thinking", "host_access_grant", "host_unattended_approved", "client_request_id", "notifications")
        if key in msg
    }
    payload.setdefault("mode", "observe")
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_create_automation(payload),
        after_success=_automation_refresh,
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/update_automation",
        probatio.Required("automation_id"): probatio.All(str, probatio.Length(min=1, max=128)),
        probatio.Required("expected_revision"): probatio.All(int, probatio.Range(min=1)),
        probatio.Optional("name"): probatio.All(str, probatio.Length(min=1, max=160)),
        probatio.Optional("prompt"): probatio.All(str, probatio.Length(min=1, max=1_048_576)),
        probatio.Optional("target"): probatio.All(dict, probatio.Length(max=16)),
        probatio.Optional("schedule"): probatio.All(dict, probatio.Length(max=16)),
        probatio.Optional("mode"): probatio.In(["observe", "edit", "full-auto", "haos-full-access"]),
        probatio.Optional("host_access_grant"): probatio.Any(None, probatio.Match(r"^[a-f0-9]{32}$")),
        probatio.Optional("host_unattended_approved"): bool,
        probatio.Optional("model"): probatio.Any(None, probatio.All(str, probatio.Length(min=1, max=160))),
        probatio.Optional("thinking"): probatio.Any(
            None, probatio.All(str, probatio.Length(min=1, max=160))
        ),
        probatio.Optional("notifications"): probatio.All(dict, probatio.Length(max=4)),
    }
)
@async_response
async def ws_update_automation(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    payload: dict[str, Any] = {
        key: msg[key]
        for key in (
            "expected_revision",
            "name",
            "prompt",
            "target",
            "schedule",
            "mode",
            "model",
            "thinking",
            "host_access_grant",
            "host_unattended_approved",
            "notifications",
        )
        if key in msg
    }
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_update_automation(msg["automation_id"], payload),
        after_success=_automation_refresh,
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/pause_automation",
        probatio.Required("automation_id"): probatio.All(str, probatio.Length(min=1, max=128)),
        probatio.Required("expected_revision"): probatio.All(int, probatio.Range(min=1)),
    }
)
@async_response
async def ws_pause_automation(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_pause_automation(
            msg["automation_id"], msg["expected_revision"]
        ),
        after_success=_automation_refresh,
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/resume_automation",
        probatio.Required("automation_id"): probatio.All(str, probatio.Length(min=1, max=128)),
        probatio.Required("expected_revision"): probatio.All(int, probatio.Range(min=1)),
    }
)
@async_response
async def ws_resume_automation(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_resume_automation(
            msg["automation_id"], msg["expected_revision"]
        ),
        after_success=_automation_refresh,
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/delete_automation",
        probatio.Required("automation_id"): probatio.All(str, probatio.Length(min=1, max=128)),
        probatio.Required("expected_revision"): probatio.All(int, probatio.Range(min=1)),
    }
)
@async_response
async def ws_delete_automation(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_delete_automation(
            msg["automation_id"], msg["expected_revision"]
        ),
        after_success=_automation_refresh,
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/run_automation",
        probatio.Required("automation_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    }
)
@async_response
async def ws_run_automation(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_run_automation(msg["automation_id"]),
        after_success=_automation_refresh,
        runtime_handler=lambda runtime: runtime.client.async_run_automation(
            msg["automation_id"], **runtime.web_search_payload()
        ),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/list_automation_runs",
        probatio.Required("automation_id"): probatio.All(str, probatio.Length(min=1, max=128)),
        probatio.Optional("limit", default=100): probatio.All(int, probatio.Range(min=1, max=200)),
    }
)
@async_response
async def ws_list_automation_runs(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_list_automation_runs(
            msg["automation_id"], limit=msg["limit"]
        ),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/list_skills",
        probatio.Required("workspace_path"): probatio.All(str, probatio.Length(min=1, max=4096)),
        probatio.Optional("force_reload", default=False): bool,
    }
)
@async_response
async def ws_list_skills(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_list_skills(
            msg["workspace_path"], force_reload=msg["force_reload"]
        ),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/set_skill",
        probatio.Required("workspace_path"): probatio.All(str, probatio.Length(min=1, max=4096)),
        probatio.Required("enabled"): bool,
        probatio.Optional("name"): probatio.Any(None, probatio.All(str, probatio.Length(min=1, max=128))),
        probatio.Optional("path"): probatio.Any(None, probatio.All(str, probatio.Length(min=1, max=4096))),
    }
)
@async_response
async def ws_set_skill(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_set_skill(
            {
                key: msg[key]
                for key in ("workspace_path", "enabled", "name", "path")
                if key in msg
            }
        ),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/create_skill",
        probatio.Required("name"): probatio.All(str, probatio.Length(min=1, max=128)),
        probatio.Required("description"): probatio.All(str, probatio.Length(min=1, max=4096)),
        probatio.Required("instructions"): probatio.All(str, probatio.Length(max=256 * 1024)),
        probatio.Optional("workspace_path"): probatio.All(str, probatio.Length(min=1, max=4096)),
        probatio.Optional("project_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    }
)
@async_response
async def ws_create_skill(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_create_skill(
            {
                key: msg[key]
                for key in (
                    "workspace_path",
                    "project_id",
                    "name",
                    "description",
                    "instructions",
                )
                if key in msg
            }
        ),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/delete_skill",
        probatio.Required("name"): probatio.All(str, probatio.Length(min=1, max=128)),
        probatio.Optional("workspace_path"): probatio.All(str, probatio.Length(min=1, max=4096)),
        probatio.Optional("project_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    }
)
@async_response
async def ws_delete_skill(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_delete_skill(
            msg["name"],
            workspace_path=msg.get("workspace_path"),
            project_id=msg.get("project_id"),
        ),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/list_plugins",
        probatio.Required("workspace_path"): probatio.All(str, probatio.Length(min=1, max=4096)),
        probatio.Optional("installed_only", default=False): bool,
    }
)
@async_response
async def ws_list_plugins(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_list_plugins(
            msg["workspace_path"], installed_only=msg["installed_only"]
        ),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/install_plugin",
        probatio.Required("plugin_name"): probatio.All(str, probatio.Length(min=1, max=128)),
        probatio.Optional("marketplace_name"): probatio.Any(
            None, probatio.All(str, probatio.Length(min=1, max=128))
        ),
    }
)
@async_response
async def ws_install_plugin(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_install_plugin(
            msg["plugin_name"], msg.get("marketplace_name")
        ),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/uninstall_plugin",
        probatio.Required("plugin_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    }
)
@async_response
async def ws_uninstall_plugin(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_uninstall_plugin(msg["plugin_id"]),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/list_marketplaces",
        probatio.Optional("workspace_path", default="."): probatio.All(
            str, probatio.Length(min=1, max=4096)
        ),
    }
)
@async_response
async def ws_list_marketplaces(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_list_marketplaces(msg["workspace_path"]),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/add_marketplace",
        probatio.Required("source"): probatio.All(str, probatio.Length(min=1, max=512)),
        probatio.Optional("ref_name"): probatio.Any(
            None, probatio.All(str, probatio.Length(min=1, max=128))
        ),
        probatio.Optional("sparse_paths"): probatio.All(
            [probatio.All(str, probatio.Length(min=1, max=256))], probatio.Length(max=8)
        ),
    }
)
@async_response
async def ws_add_marketplace(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_add_marketplace(
            {
                key: msg[key]
                for key in ("source", "ref_name", "sparse_paths")
                if key in msg
            }
        ),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/remove_marketplace",
        probatio.Required("marketplace_name"): probatio.All(str, probatio.Length(min=1, max=128)),
    }
)
@async_response
async def ws_remove_marketplace(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_remove_marketplace(msg["marketplace_name"]),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/upgrade_marketplace",
        probatio.Required("marketplace_name"): probatio.All(str, probatio.Length(min=1, max=128)),
    }
)
@async_response
async def ws_upgrade_marketplace(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_upgrade_marketplace(msg["marketplace_name"]),
    )


@websocket_command({probatio.Required("type"): f"{DOMAIN}/list_mcp"})
@async_response
async def ws_list_mcp(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_list_mcp())


@websocket_command({probatio.Required("type"): f"{DOMAIN}/list_stdio_packages"})
@async_response
async def ws_list_stdio_packages(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_list_stdio_packages())


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/add_stdio_mcp",
    probatio.Required("name"): probatio.All(str, probatio.Match(r"\A[a-z][a-z0-9_-]{0,63}\Z")),
    probatio.Required("package_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    probatio.Required("revision"): probatio.All(str, probatio.Length(min=1, max=128)),
    probatio.Required("acknowledged"): probatio.All(bool, probatio.In([True])),
})
@async_response
async def ws_add_stdio_mcp(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg,
                        lambda client: client.async_add_stdio_mcp({key: msg[key] for key in (
                            "name", "package_id", "revision", "acknowledged")}))


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/update_stdio_mcp",
    probatio.Required("name"): probatio.All(str, probatio.Match(r"\A[a-z][a-z0-9_-]{0,63}\Z")),
    probatio.Required("revision"): probatio.All(str, probatio.Length(min=1, max=128)),
    probatio.Required("expected_revision"): probatio.Match(r"^[a-f0-9]{64}$"),
    probatio.Required("acknowledged"): probatio.All(bool, probatio.In([True])),
})
@async_response
async def ws_update_stdio_mcp(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg,
                        lambda client: client.async_update_stdio_mcp(
                            msg["name"], {"revision": msg["revision"],
                                          "expected_revision": msg["expected_revision"],
                                          "acknowledged": True}))


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/rollback_stdio_mcp",
    probatio.Required("name"): probatio.All(str, probatio.Match(r"\A[a-z][a-z0-9_-]{0,63}\Z")),
    probatio.Required("expected_revision"): probatio.Match(r"^[a-f0-9]{64}$"),
})
@async_response
async def ws_rollback_stdio_mcp(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg,
                        lambda client: client.async_rollback_stdio_mcp(
                            msg["name"], msg["expected_revision"]))


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/list_mcp_tools",
    probatio.Required("name"): probatio.All(str, probatio.Length(min=1, max=64)),
})
@async_response
async def ws_list_mcp_tools(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg,
                        lambda client: client.async_list_mcp_tools(msg["name"]))


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/set_mcp_tools",
    probatio.Required("name"): probatio.All(str, probatio.Length(min=1, max=64)),
    probatio.Required("enabled_tools"): probatio.All(list, probatio.Length(max=512)),
    probatio.Required("revision"): probatio.All(str, probatio.Length(min=64, max=64)),
    probatio.Required("catalogue_revision"): probatio.All(str, probatio.Length(min=64, max=64)),
    probatio.Optional("approval_mode"): probatio.In(("auto", "approve")),
})
@async_response
async def ws_set_mcp_tools(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg,
                        lambda client: client.async_set_mcp_tools(msg["name"], {
                            key: msg[key] for key in ("enabled_tools", "revision", "catalogue_revision", "approval_mode")
                            if key in msg
                        }))


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/lock_down_mcp_tools",
    probatio.Required("name"): probatio.All(str, probatio.Length(min=1, max=64)),
    probatio.Required("revision"): probatio.Match(r"^[a-f0-9]{64}$"),
})
@async_response
async def ws_lock_down_mcp_tools(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg,
                        lambda client: client.async_lock_down_mcp_tools(msg["name"], msg["revision"]))


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/add_mcp",
        probatio.Optional("local"): bool,
        probatio.Optional("local_acknowledged"): bool,
        probatio.Optional("require_tool_selection"): bool,
        probatio.Required("name"): probatio.All(str, probatio.Length(min=1, max=128)),
        probatio.Required("url"): probatio.All(str, probatio.Length(min=1, max=2048)),
        probatio.Optional("oauth_client_id"): probatio.Any(
            None, probatio.All(str, probatio.Length(min=1, max=256))
        ),
        probatio.Optional("oauth_resource"): probatio.Any(
            None, probatio.All(str, probatio.Length(min=1, max=2048))
        ),
    }
)
@async_response
async def ws_add_mcp(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_add_mcp(
            {
                key: msg[key]
                for key in ("name", "url", "oauth_client_id", "oauth_resource", "local", "local_acknowledged", "require_tool_selection")
                if key in msg
            }
        ),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/remove_mcp",
        probatio.Required("name"): probatio.All(str, probatio.Length(min=1, max=128)),
    }
)
@async_response
async def ws_remove_mcp(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass, connection, msg, lambda client: client.async_remove_mcp(msg["name"])
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/login_mcp",
        probatio.Required("name"): probatio.All(str, probatio.Length(min=1, max=128)),
    }
)
@async_response
async def ws_login_mcp(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    # OAuth state remains only in this direct WebSocket response.
    await _async_handle(
        hass, connection, msg, lambda client: client.async_login_mcp(msg["name"])
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/get_agents",
        probatio.Optional("project_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    }
)
@async_response
async def ws_get_agents(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_get_agents(msg.get("project_id")),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/update_agents",
        probatio.Optional("project_id"): probatio.All(str, probatio.Length(min=1, max=128)),
        probatio.Required("content"): probatio.All(str, probatio.Length(max=256 * 1024)),
    }
)
@async_response
async def ws_update_agents(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_update_agents(
            msg.get("project_id"), msg["content"]
        ),
    )


@websocket_command(
    {
        probatio.Required("type"): f"{DOMAIN}/delete_agents",
        probatio.Optional("project_id"): probatio.All(str, probatio.Length(min=1, max=128)),
    }
)
@async_response
async def ws_delete_agents(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(
        hass,
        connection,
        msg,
        lambda client: client.async_delete_agents(msg.get("project_id")),
    )


@websocket_command({probatio.Required("type"): f"{DOMAIN}/host_access"})
@async_response
async def ws_host_access(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_host_access())


@websocket_command({
    probatio.Required("type"): f"{DOMAIN}/enable_host_access",
    probatio.Required("scope_revision"): probatio.Match(r"^[a-f0-9]{64}$"),
    probatio.Required("acknowledged"): bool,
})
@async_response
async def ws_enable_host_access(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_enable_host_access(
        msg["scope_revision"], msg["acknowledged"],
    ))


@websocket_command({probatio.Required("type"): f"{DOMAIN}/revoke_host_access"})
@async_response
async def ws_revoke_host_access(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    await _async_handle(hass, connection, msg, lambda client: client.async_revoke_host_access())
