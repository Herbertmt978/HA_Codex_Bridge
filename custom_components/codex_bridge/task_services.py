"""Home Assistant actions for bounded, unattended Codex tasks."""

from __future__ import annotations

from uuid import uuid4

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError, Unauthorized

from .bridge_api import BridgeApiError
from .const import CONF_ALLOW_UNATTENDED_TASK_ACTIONS, DOMAIN
from .runtime import CodexBridgeRuntime, async_get_runtime

_ID = vol.Match(r"^[a-f0-9]{32}$")
_TEXT = vol.All(str, vol.Length(min=1, max=65_536))
_TARGET = vol.All(str, vol.Length(min=1, max=128))
_MODEL = vol.All(str, vol.Length(min=1, max=160))

START_SCHEMA = vol.Schema(
    {
        vol.Required("project_id"): _TARGET,
        vol.Required("title"): vol.All(str, vol.Length(min=1, max=160)),
        vol.Required("prompt"): _TEXT,
        vol.Optional("mode", default="observe"): vol.In(
            ("observe", "edit", "full-auto")
        ),
        vol.Optional("model_override"): _MODEL,
        vol.Optional("thinking_override"): _MODEL,
        vol.Optional("task_id"): _ID,
    }
)
CONTINUE_SCHEMA = vol.Schema(
    {
        vol.Required("thread_id"): _TARGET,
        vol.Required("prompt"): _TEXT,
        vol.Optional("task_id"): _ID,
    }
)
REFERENCE_SCHEMA = vol.Schema({vol.Required("task_id"): _ID})

_ERROR_MESSAGES = {
    "authentication_required": "Codex sign-in is required before running a task.",
    "authentication_failed": "The Codex Bridge connection needs to be repaired.",
    "runtime_unavailable": "The Codex Bridge App is unavailable.",
    "app_server_unavailable": "The Codex runtime is unavailable. Try again later.",
    "runtime_closed": "The Codex runtime is restarting. Try again later.",
    "runtime_queue_full": "Codex is at capacity. Try again later.",
    "task_actions_unavailable": "Update the Codex Bridge App to use task actions.",
    "capabilities_unavailable": "The requested Codex capability is unavailable.",
    "task_target_not_found": "The selected Codex project or chat was not found.",
    "task_target_unavailable": "The selected Codex project or chat is archived or busy.",
    "task_retry_conflict": "This task ID was already used for different work.",
    "runtime_request_conflict": "This task ID was already used for different work.",
    "thread_prompt_pending": "This chat already has a Codex turn waiting to start.",
    "turn_cancelling": "This chat is cancelling its current Codex turn.",
    "runtime_idempotency_capacity": "The Codex task history is full. Remove an old chat before retrying.",
    "task_model_unavailable": "The selected model or reasoning level is unavailable.",
    "task_host_access_denied": "Unattended task actions cannot use Home Assistant OS host access.",
    "task_not_found": "The Codex task was not found.",
}


def _task_id(call: ServiceCall) -> str:
    if supplied := call.data.get("task_id"):
        return supplied
    # A script can issue several actions under one HA context. Give each call
    # its own identity; callers that need retry safety supply the original ID.
    return uuid4().hex


async def _runtime_for_action(
    hass: HomeAssistant, call: ServiceCall
) -> CodexBridgeRuntime:
    try:
        runtime = async_get_runtime(hass)
    except RuntimeError:
        raise ServiceValidationError(
            "Codex Bridge is not configured or loaded."
        ) from None
    user_id = call.context.user_id
    if user_id is None:
        entry = hass.config_entries.async_get_entry(runtime.entry_id)
        if (
            entry is None
            or entry.options.get(CONF_ALLOW_UNATTENDED_TASK_ACTIONS) is not True
        ):
            raise Unauthorized(context=call.context)
    else:
        user = await hass.auth.async_get_user(user_id)
        if user is None or not user.is_admin:
            raise Unauthorized(context=call.context, user_id=user_id)
    if not runtime.supports_capability("task_actions_v1"):
        await runtime.async_refresh_capabilities()
    if not runtime.supports_capability("task_actions_v1"):
        raise ServiceValidationError("Update the Codex Bridge App to use task actions.")
    return runtime


async def _invoke(action):
    try:
        return await action
    except BridgeApiError as error:
        raise ServiceValidationError(
            _ERROR_MESSAGES.get(
                error.code, "Codex Bridge could not process this task action."
            )
        ) from None


def async_register_task_services(hass: HomeAssistant) -> None:
    """Register process-lifetime actions so missing Apps fail clearly."""

    async def start_task(call: ServiceCall) -> dict[str, str]:
        runtime = await _runtime_for_action(hass, call)
        payload = {
            **call.data,
            "task_id": _task_id(call),
            **runtime.web_search_payload(),
        }
        return await _invoke(runtime.client.async_start_task(payload))

    async def continue_task(call: ServiceCall) -> dict[str, str]:
        runtime = await _runtime_for_action(hass, call)
        payload = {
            **call.data,
            "task_id": _task_id(call),
            **runtime.web_search_payload(),
        }
        return await _invoke(runtime.client.async_continue_task(payload))

    async def cancel_task(call: ServiceCall) -> dict[str, str]:
        runtime = await _runtime_for_action(hass, call)
        return await _invoke(runtime.client.async_cancel_task(call.data["task_id"]))

    async def get_task(call: ServiceCall) -> dict[str, str]:
        runtime = await _runtime_for_action(hass, call)
        return await _invoke(runtime.client.async_get_task(call.data["task_id"]))

    for name, handler, schema in (
        ("start_task", start_task, START_SCHEMA),
        ("continue_task", continue_task, CONTINUE_SCHEMA),
        ("cancel_task", cancel_task, REFERENCE_SCHEMA),
        ("get_task", get_task, REFERENCE_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN,
            name,
            handler,
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )
