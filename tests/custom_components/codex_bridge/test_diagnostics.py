"""Config entry diagnostics through Home Assistant's download endpoint."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.codex_bridge.bridge_api import BridgeApiConnectionError
from custom_components.codex_bridge.const import (
    CONF_ALLOW_UNATTENDED_TASK_ACTIONS,
    CONF_ASSIST_INSTRUCTIONS,
    CONF_ASSIST_MCP_SERVERS,
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    CONF_CONNECTION_TYPE,
    CONF_DISCOVERY_UUID,
    CONF_QUESTION_NOTIFICATIONS_ENABLED,
    CONF_QUESTION_NOTIFICATIONS_TARGETS,
    CONNECTION_TYPE_SUPERVISOR,
    DOMAIN,
)
from custom_components.codex_bridge.host_access import HOST_WORKER_KEY, SUPERVISOR_SLUG_KEY

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

TOKEN = "t0ken-" + "a" * 48
URL = "http://172.30.33.4:8766"
PROFILE_A = "a" * 32
PROFILE_B = "b" * 32
WORKER = {"host": "172.30.33.9", "port": 8767, "token": "w0rker-" + "c" * 40}


def _profile(profile_id: str, label: str, *, active: bool, at: datetime) -> dict:
    return {
        "id": profile_id,
        "label": label,
        "active": active,
        "status": "available",
        "updated_at": at.isoformat(),
        "windows": [
            {"name": "5 hours", "window_minutes": 300, "remaining_percent": 70,
             "resets_at": 2_000_000_000},
            {"name": "Weekly", "window_minutes": 10080, "remaining_percent": 40,
             "resets_at": 2_000_086_400},
        ],
        "available_resets": 0,
        "expiry_complete": True,
        "five_hour_enabled": True,
        "reauthentication_required": False,
    }


async def test_diagnostics_explain_health_without_credentials_or_accounts(
    hass, hass_client
):
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Codex Bridge",
        source="hassio",
        data={
            CONF_BRIDGE_URL: URL,
            CONF_BRIDGE_TOKEN: TOKEN,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_SUPERVISOR,
            CONF_DISCOVERY_UUID: "d" * 32,
            SUPERVISOR_SLUG_KEY: "a0d7b954_codex_bridge",
            HOST_WORKER_KEY: WORKER,
        },
        options={
            CONF_ALLOW_UNATTENDED_TASK_ACTIONS: True,
            CONF_ASSIST_INSTRUCTIONS: "Private household instructions",
            CONF_ASSIST_MCP_SERVERS: ["home-tools"],
            CONF_QUESTION_NOTIFICATIONS_ENABLED: True,
            CONF_QUESTION_NOTIFICATIONS_TARGETS: ["phone_device_id"],
        },
        unique_id="d" * 32,
    )
    entry.add_to_hass(hass)
    at = datetime.now(UTC)
    telemetry = {"inventory_complete": True, "profiles": [
        _profile(PROFILE_A, "Work private@example.invalid", active=True, at=at),
        _profile(PROFILE_B, "Home", active=False, at=at),
    ]}
    telemetry["profiles"][1]["reauthentication_required"] = True

    async def wait_events(*, after):
        await asyncio.Event().wait()

    client = Mock()
    client.negotiated_api_version = 1
    client.async_ready = AsyncMock(return_value=Mock(
        capabilities=("api_v1", "account_profile_telemetry_v1", "web_search_v1"),
        app_version="1.13.22",
    ))
    client.async_pair_host_worker = AsyncMock()
    client.async_get_status = AsyncMock(side_effect=BridgeApiConnectionError())
    client.async_list_threads = AsyncMock(return_value=[])
    client.async_replay_events = AsyncMock(side_effect=wait_events)
    client.async_account_profile_telemetry = AsyncMock(return_value=telemetry)
    client.async_close = AsyncMock()
    with (
        patch("custom_components.codex_bridge.BridgeApiClient", return_value=client),
        patch("custom_components.codex_bridge.async_register_panel", new=AsyncMock()),
        patch("custom_components.codex_bridge.async_remove_panel"),
        patch("homeassistant.components.frontend.async_setup", new=AsyncMock(return_value=True)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Fetch the complete file a user attaches, including HA's issues section.
        assert await async_setup_component(hass, "diagnostics", {})
        client_session = await hass_client()
        response = await client_session.get(f"/api/diagnostics/config_entry/{entry.entry_id}")
        assert response.status == 200
        download = await response.json()
        assert await hass.config_entries.async_unload(entry.entry_id)

    diagnostics = download["data"]
    # The signed-out saved account raised one repair, which the download lists.
    assert [issue["domain"] for issue in download["issues"]] == [DOMAIN]
    text = json.dumps(download)
    for private in (
        TOKEN, URL, "172.30.33", WORKER["token"], PROFILE_A, PROFILE_B, "private@example",
        "Work", "Private household", "home-tools", "phone_device_id", "d" * 32,
    ):
        assert private not in text

    assert diagnostics["entry"] == {
        "connection_type": "supervisor",
        "supervisor_slug": "a0d7b954_codex_bridge",
        "host_access_paired": True,
    }
    assert diagnostics["options"] == {
        "web_search_mode": "live",
        "unattended_task_actions": True,
        "assist_enabled": False,
        "assist_project_selected": False,
        "assist_voice_without_user": False,
        "assist_instructions_set": True,
        "assist_mcp_server_count": 1,
        "question_notifications": {
            "enabled": True, "persistent": True, "preview": False, "mobile_target_count": 1,
        },
    }
    assert diagnostics["bridge"] == {
        "api_version": 1,
        "software_version": "1.13.22",
        "capabilities": ["account_profile_telemetry_v1", "api_v1", "web_search_v1"],
    }
    # Replay is still waiting on the App, so the stream has not connected yet.
    assert diagnostics["event_stream"]["state"] == "connecting"
    assert set(diagnostics["event_stream"]) == {"state", "phase", "retry_count", "cursor"}
    # The status refresh failed at setup; allowance telemetry succeeded.
    assert diagnostics["coordinators"] == {
        "status": {
            "last_update_success": False,
            "last_failure": "UpdateFailed (BridgeApiConnectionError)",
            "update_interval_seconds": 60.0,
        },
        "account_allowances": {
            "last_update_success": True,
            "last_failure": None,
            "update_interval_seconds": 60.0,
        },
    }
    assert diagnostics["status"] is None
    profiles = diagnostics["accounts"]["profiles"]
    assert diagnostics["accounts"]["inventory_complete"] is True
    assert [profile["index"] for profile in profiles] == [0, 1]
    assert profiles[0]["active"] is True
    assert profiles[1]["reauthentication_required"] is True
    assert profiles[0]["windows"] == ["5 hours", "Weekly"]
    assert set(profiles[0]) == {
        "index", "active", "status", "reauthentication_required", "five_hour_enabled",
        "windows", "reset_credits_reported", "expiry_complete", "data_age_seconds",
    }
