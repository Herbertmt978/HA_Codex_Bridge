"""Connection diagnostics that never copy credentials, endpoints or account identity."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .assist_settings import assist_mcp_selection
from .const import (
    CONF_ALLOW_UNATTENDED_TASK_ACTIONS,
    CONF_ASSIST_ALLOW_VOICE,
    CONF_ASSIST_ENABLED,
    CONF_ASSIST_INSTRUCTIONS,
    CONF_ASSIST_MCP_SERVERS,
    CONF_ASSIST_PROJECT_ID,
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
)
from .host_access import HOST_WORKER_KEY, SUPERVISOR_SLUG_KEY
from .runtime import CodexBridgeConfigEntry

# The payload is built from an allow-list, so none of these keys is copied.
# Redaction is a backstop in case a later field nests one of them.
TO_REDACT = {
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    HOST_WORKER_KEY,
    "account",
    "email",
    "host",
    "label",
    "profile_id",
    "token",
    "url",
}


def _failure(error: BaseException | None) -> str | None:
    """Name the failure and its cause without copying a message."""

    if error is None:
        return None
    cause = error.__cause__
    if cause is None:
        return type(error).__name__
    return f"{type(error).__name__} ({type(cause).__name__})"


def _coordinator(coordinator: DataUpdateCoordinator[Any] | None) -> dict[str, Any] | None:
    if coordinator is None:
        return None
    interval = coordinator.update_interval
    return {
        "last_update_success": coordinator.last_update_success,
        "last_failure": _failure(coordinator.last_exception),
        "update_interval_seconds": interval.total_seconds() if interval else None,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CodexBridgeConfigEntry
) -> dict[str, Any]:
    """Return connection, stream, coordinator and per-account health."""

    runtime = entry.runtime_data
    options = entry.options
    notifications = runtime.question_notification_settings
    mcp_servers = assist_mcp_selection(options.get(CONF_ASSIST_MCP_SERVERS, []))
    device = runtime.device_info or {}

    status = None
    if runtime.entity_coordinator is not None and runtime.entity_coordinator.data:
        snapshot = runtime.entity_coordinator.data
        status = {
            "authenticated": snapshot.authenticated,
            "task_running": snapshot.task_running,
            "last_outcome": snapshot.last_outcome,
            "usage_available": snapshot.usage_available,
            "five_hour_enabled": snapshot.five_hour_enabled,
        }

    accounts = None
    allowances = runtime.account_allowance_coordinator
    if allowances is not None and allowances.data is not None:
        now = datetime.now(UTC)
        accounts = {
            "inventory_complete": allowances.data.inventory_complete,
            "refreshed_at": allowances.data.refreshed_at.isoformat(),
            # Profile IDs and labels identify a person's accounts; an index
            # is enough to compare one account's flags across downloads.
            "profiles": [
                {
                    "index": index,
                    "active": profile.active,
                    "status": profile.status,
                    "reauthentication_required": profile.reauthentication_required,
                    "five_hour_enabled": profile.five_hour_enabled,
                    "windows": sorted(profile.windows),
                    "reset_credits_reported": profile.available_resets is not None,
                    "expiry_complete": profile.expiry_complete,
                    "data_age_seconds": (
                        int((now - profile.updated_at).total_seconds())
                        if profile.updated_at is not None
                        else None
                    ),
                }
                for index, profile in enumerate(allowances.data.profiles.values())
            ],
        }

    diagnostics = {
        "entry": {
            "connection_type": runtime.connection_type,
            "supervisor_slug": entry.data.get(SUPERVISOR_SLUG_KEY),
            "host_access_paired": isinstance(entry.data.get(HOST_WORKER_KEY), dict),
        },
        "options": {
            "web_search_mode": runtime.web_search_mode,
            "unattended_task_actions": options.get(CONF_ALLOW_UNATTENDED_TASK_ACTIONS) is True,
            "assist_enabled": options.get(CONF_ASSIST_ENABLED) is True,
            "assist_project_selected": bool(options.get(CONF_ASSIST_PROJECT_ID)),
            "assist_voice_without_user": options.get(CONF_ASSIST_ALLOW_VOICE) is True,
            "assist_instructions_set": bool(options.get(CONF_ASSIST_INSTRUCTIONS)),
            "assist_mcp_server_count": len(mcp_servers) if mcp_servers is not None else None,
            "question_notifications": {
                "enabled": notifications["enabled"],
                "persistent": notifications["persistent"],
                "preview": notifications["preview"],
                "mobile_target_count": len(notifications["mobile_targets"]),
            },
        },
        "bridge": {
            "api_version": runtime.api_version,
            "software_version": device.get("sw_version"),
            "capabilities": sorted(runtime.capabilities),
        },
        "event_stream": (
            dict(runtime.event_broker.connection_status)
            if runtime.event_broker is not None
            else None
        ),
        "coordinators": {
            "status": _coordinator(runtime.entity_coordinator),
            "account_allowances": _coordinator(allowances),
        },
        "status": status,
        "accounts": accounts,
    }
    return async_redact_data(diagnostics, TO_REDACT)
