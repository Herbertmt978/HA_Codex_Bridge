"""Home Assistant actions for bounded, unattended Codex tasks."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any
from uuid import uuid4

import probatio

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, Unauthorized

from .bridge_api import BridgeApiConnectionError, BridgeApiError
from .const import CONF_ALLOW_UNATTENDED_TASK_ACTIONS, DOMAIN
from .runtime import CodexBridgeRuntime, async_get_runtime

_ID = probatio.Match(r"^[a-f0-9]{32}$")
_TEXT = probatio.All(str, probatio.Length(min=1, max=65_536))
_TARGET = probatio.All(str, probatio.Length(min=1, max=128))
_MODEL = probatio.All(str, probatio.Length(min=1, max=160))

START_SCHEMA = probatio.Schema(
    {
        probatio.Required("project_id"): _TARGET,
        probatio.Required("title"): probatio.All(str, probatio.Length(min=1, max=160)),
        probatio.Required("prompt"): _TEXT,
        probatio.Optional("mode", default="observe"): probatio.In(
            ("observe", "edit", "full-auto")
        ),
        probatio.Optional("model_override"): _MODEL,
        probatio.Optional("thinking_override"): _MODEL,
        probatio.Optional("task_id"): _ID,
    }
)
CONTINUE_SCHEMA = probatio.Schema(
    {
        probatio.Required("thread_id"): _TARGET,
        probatio.Required("prompt"): _TEXT,
        probatio.Optional("task_id"): _ID,
    }
)
REFERENCE_SCHEMA = probatio.Schema({probatio.Required("task_id"): _ID})

# Bridge problem codes that the caller can correct by changing the action's
# target or data. Their translation keys match the code.
_VALIDATION_ERRORS = frozenset({
    "capabilities_unavailable",
    "task_actions_unavailable",
    "task_target_not_found",
    "task_target_unavailable",
    "task_retry_conflict",
    "thread_prompt_pending",
    "turn_cancelling",
    "task_model_unavailable",
    "task_host_access_denied",
    "task_not_found",
})
# Problems with the App, its runtime or the connection. The action was valid
# but could not run now, so HA reports an ordinary execution failure.
_EXECUTION_ERRORS = frozenset({
    "authentication_required",
    "authentication_failed",
    "runtime_unavailable",
    "app_server_unavailable",
    "runtime_closed",
    "runtime_queue_full",
    "runtime_idempotency_capacity",
})
_TRANSLATION_KEYS = {"runtime_request_conflict": "task_retry_conflict"}


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
            translation_domain=DOMAIN, translation_key="not_loaded"
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
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="task_actions_unavailable"
        )
    return runtime


async def _invoke(action: Awaitable[dict[str, Any]]) -> ServiceResponse:
    try:
        return await action
    except BridgeApiConnectionError:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="cannot_connect"
        ) from None
    except BridgeApiError as error:
        key = _TRANSLATION_KEYS.get(error.code, error.code)
        if key in _VALIDATION_ERRORS:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key=key
            ) from None
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=key if key in _EXECUTION_ERRORS else "task_action_failed",
        ) from None


def async_register_task_services(hass: HomeAssistant) -> None:
    """Register the actions once, so a missing or unloaded App fails clearly."""

    async def start_task(call: ServiceCall) -> ServiceResponse:
        runtime = await _runtime_for_action(hass, call)
        payload = {
            **call.data,
            "task_id": _task_id(call),
            **runtime.web_search_payload(),
        }
        return await _invoke(runtime.client.async_start_task(payload))

    async def continue_task(call: ServiceCall) -> ServiceResponse:
        runtime = await _runtime_for_action(hass, call)
        payload = {
            **call.data,
            "task_id": _task_id(call),
            **runtime.web_search_payload(),
        }
        return await _invoke(runtime.client.async_continue_task(payload))

    async def cancel_task(call: ServiceCall) -> ServiceResponse:
        runtime = await _runtime_for_action(hass, call)
        return await _invoke(runtime.client.async_cancel_task(call.data["task_id"]))

    async def get_task(call: ServiceCall) -> ServiceResponse:
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
