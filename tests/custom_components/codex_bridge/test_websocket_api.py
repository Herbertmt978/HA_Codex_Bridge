"""Safe Home Assistant WebSocket contracts for the v1 event broker."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.codex_bridge.bridge_api import (
    BridgeApiConflictError,
    BridgeApiError,
    BridgeApiGoneError,
    BridgeApiMcpDisabledError,
)
from custom_components.codex_bridge.const import DATA_ENTRIES, DOMAIN
from custom_components.codex_bridge.event_broker import EventBroker, EventRecord
from custom_components.codex_bridge.runtime import CodexBridgeRuntime
from custom_components.codex_bridge.protocol import ProblemRecord
from custom_components.codex_bridge.websocket_api import (
    ws_answer_interaction,
    ws_decide_interaction,
    ws_get_config,
    ws_get_event_status,
    ws_get_events,
    ws_get_automation,
    ws_get_status,
    ws_create_automation,
    ws_login_mcp,
    ws_list_artifacts,
    ws_run_automation,
    ws_send_prompt,
    ws_start_auth_login,
    ws_subscribe_events,
    ws_unsubscribe_events,
)


def _event(
    cursor: int,
    *,
    scope: str = "auth",
    thread_id: str | None = None,
) -> EventRecord:
    return EventRecord.from_payload(
        {
            "cursor": cursor,
            "event_id": f"evt_{cursor}",
            "scope": scope,
            "thread_id": thread_id,
            "event_type": f"{scope}.updated",
            "payload": {"state": "pending"},
            "timestamp": "2026-07-13T12:00:00Z",
        }
    )


class _Connection:
    def __init__(self) -> None:
        self.subscriptions = {}
        self.results: list[tuple[int, object]] = []
        self.errors: list[tuple[int, str, str]] = []
        self.events: list[tuple[int, object]] = []

    def send_result(self, message_id: int, result=None) -> None:
        self.results.append((message_id, result))

    def send_error(self, message_id: int, code: str, message: str) -> None:
        self.errors.append((message_id, code, message))

    def send_event(self, message_id: int, event: object) -> None:
        self.events.append((message_id, event))


class _Hass:
    def __init__(self, runtime: CodexBridgeRuntime) -> None:
        self.data = {DOMAIN: {DATA_ENTRIES: {runtime.entry_id: runtime}}}
        self.tasks: list[asyncio.Task[None]] = []

    def async_create_task(self, target) -> asyncio.Task[None]:
        task = asyncio.create_task(target)
        self.tasks.append(task)
        return task

    def async_create_background_task(
        self, target, _name, *, eager_start=True
    ) -> asyncio.Task[None]:
        return self.async_create_task(target)

    async def finish(self) -> None:
        await asyncio.gather(*self.tasks, return_exceptions=True)


def _runtime(*, queue_size: int = 256) -> tuple[CodexBridgeRuntime, EventBroker]:
    client = AsyncMock()
    client.async_refresh_ready.return_value = SimpleNamespace(capabilities=())
    client.negotiated_api_version = 1
    broker = EventBroker(client, initial_cursor=0, queue_size=queue_size)
    return (
        CodexBridgeRuntime("entry", "Codex", client, "supervisor", "a" * 32, 1, broker),
        broker,
    )


async def test_v1_subscription_acknowledges_and_forwards_auth_without_a_chat() -> None:
    runtime, broker = _runtime()
    hass = _Hass(runtime)
    connection = _Connection()

    ws_subscribe_events(
        hass,
        connection,
        {
            "id": 5,
            "type": f"{DOMAIN}/subscribe_events",
            "after": 0,
            "scopes": ["auth"],
        },
    )
    await asyncio.sleep(0)
    broker._publish_event(_event(1))
    await asyncio.sleep(0)
    connection.subscriptions[5]()
    await hass.finish()

    assert connection.results == [(5, {"subscription_id": 5, "api_version": 1})]
    assert connection.events == [
        (
            5,
            {
                "type": "event",
                "event": {
                    "cursor": 1,
                    "event_id": "evt_1",
                    "scope": "auth",
                    "thread_id": None,
                    "event_type": "auth.updated",
                    "payload": {"state": "pending"},
                    "timestamp": "2026-07-13T12:00:00Z",
                },
            },
        )
    ]


async def test_start_auth_login_defaults_to_non_destructive_mode() -> None:
    runtime, _broker = _runtime()
    hass = _Hass(runtime)
    connection = _Connection()
    runtime.client.async_start_auth_login = AsyncMock(
        return_value={"state": "login_starting"}
    )

    ws_start_auth_login(
        hass,
        connection,
        {"id": 4, "type": f"{DOMAIN}/start_auth_login"},
    )
    await asyncio.sleep(0)

    runtime.client.async_start_auth_login.assert_awaited_once_with(False)
    assert connection.results == [(4, {"state": "login_starting"})]


async def test_web_search_mode_is_forwarded_server_side_for_prompts_and_manual_runs() -> None:
    runtime, _broker = _runtime()
    runtime.capabilities = ("web_search_v1",)
    runtime.web_search_mode = "live"
    runtime.client.async_send_prompt = AsyncMock(return_value={"run_id": "run_1"})
    runtime.client.async_run_automation = AsyncMock(
        return_value={"automation_run_id": "autrun_1"}
    )
    hass = _Hass(runtime)
    connection = _Connection()

    ws_send_prompt(
        hass,
        connection,
        {
            "id": 41,
            "type": f"{DOMAIN}/send_prompt",
            "thread_id": "thr_1",
            "prompt": "Find current information",
        },
    )
    ws_run_automation(
        hass,
        connection,
        {
            "id": 42,
            "type": f"{DOMAIN}/run_automation",
            "automation_id": "aut_1",
        },
    )
    await asyncio.sleep(0)

    runtime.client.async_send_prompt.assert_awaited_once_with(
        "thr_1",
        "Find current information",
        client_request_id=None,
        web_search="live",
    )
    runtime.client.async_run_automation.assert_awaited_once_with(
        "aut_1", web_search="live"
    )
    assert connection.results == [
        (41, {"run_id": "run_1"}),
        (42, {"automation_run_id": "autrun_1"}),
    ]


@pytest.mark.parametrize(
    ("capabilities", "mode", "expected_mode"),
    [
        (("web_search_v1",), "live", "live"),
        ((), "live", "live"),
        (("web_search_v1",), "disabled", "disabled"),
    ],
)
async def test_get_config_returns_persisted_web_search_preference(
    capabilities, mode, expected_mode
) -> None:
    runtime, _broker = _runtime()
    runtime.capabilities = capabilities
    runtime.web_search_mode = mode
    hass = _Hass(runtime)
    connection = _Connection()

    ws_get_config(
        hass,
        connection,
        {"id": 55, "type": f"{DOMAIN}/get_config"},
    )
    await asyncio.sleep(0)

    assert connection.results == [
        (
            55,
            {
                "panel_title": "Codex",
                "connection_type": "supervisor",
                "api_version": 1,
                "capabilities": list(capabilities),
                "web_search_mode": expected_mode,
            },
        )
    ]


async def test_status_poll_recovers_live_search_after_login_without_reload() -> None:
    runtime, _broker = _runtime()
    runtime.web_search_mode = "live"
    runtime.client.async_get_status.return_value = {
        "auth": {
            "state": "logged_out",
            "auth_mode": None,
            "auth_required": True,
        },
        "provider_capabilities": {"web_search": False},
    }
    runtime.client.async_refresh_ready.return_value = SimpleNamespace(
        capabilities=("api_v1", "automations_v1")
    )
    runtime.automation_scheduler = SimpleNamespace(web_search_mode=None)
    hass = _Hass(runtime)
    connection = _Connection()

    ws_get_status(hass, connection, {"id": 56, "type": f"{DOMAIN}/get_status"})
    await hass.finish()

    assert not runtime.supports_capability("web_search_v1")
    assert runtime._capability_refreshed_at > 0

    runtime.client.async_get_status.return_value = {
        "auth": {
            "state": "ok",
            "auth_mode": "chatgpt",
            "auth_required": False,
        },
        "provider_capabilities": {"web_search": True},
    }
    runtime.client.async_refresh_ready.return_value = SimpleNamespace(
        capabilities=("api_v1", "automations_v1", "web_search_v1")
    )
    runtime.client.async_send_prompt.return_value = {"run_id": "run_1"}
    runtime.client.async_run_automation.return_value = {
        "automation_run_id": "autrun_1"
    }
    ws_get_status(hass, connection, {"id": 59, "type": f"{DOMAIN}/get_status"})
    await hass.finish()

    assert runtime.supports_capability("web_search_v1")
    assert runtime.web_search_payload() == {"web_search": "live"}
    assert runtime.automation_scheduler.web_search_mode == "live"
    assert runtime.client.async_refresh_ready.await_count == 2

    ws_send_prompt(
        hass,
        connection,
        {
            "id": 57,
            "type": f"{DOMAIN}/send_prompt",
            "thread_id": "thr_1",
            "prompt": "Find today's weather",
        },
    )
    ws_run_automation(
        hass,
        connection,
        {
            "id": 58,
            "type": f"{DOMAIN}/run_automation",
            "automation_id": "aut_1",
        },
    )
    await hass.finish()

    runtime.client.async_send_prompt.assert_awaited_once_with(
        "thr_1",
        "Find today's weather",
        client_request_id=None,
        web_search="live",
    )
    runtime.client.async_run_automation.assert_awaited_once_with(
        "aut_1", web_search="live"
    )


async def test_create_automation_refreshes_the_local_scheduler() -> None:
    runtime, _broker = _runtime()
    runtime.client.async_create_automation = AsyncMock(
        return_value={"automation_id": "aut_1", "revision": 1}
    )
    refresh = AsyncMock()
    runtime.automation_scheduler = SimpleNamespace(async_refresh=refresh)
    hass = _Hass(runtime)
    connection = _Connection()

    ws_create_automation(
        hass,
        connection,
        {
            "id": 51,
            "type": f"{DOMAIN}/create_automation",
            "name": "Daily check",
            "prompt": "Check the workspace",
            "target": {"workspace_path": "C:/work"},
            "schedule": {"kind": "interval", "seconds": 3600},
        },
    )
    await asyncio.sleep(0)

    runtime.client.async_create_automation.assert_awaited_once_with(
        {
            "name": "Daily check",
            "prompt": "Check the workspace",
            "target": {"workspace_path": "C:/work"},
            "schedule": {"kind": "interval", "seconds": 3600},
            "mode": "observe",
        }
    )
    refresh.assert_awaited_once()
    assert connection.results == [(51, {"automation_id": "aut_1", "revision": 1})]


@pytest.mark.parametrize(
    "refresh_error",
    [BridgeApiError("bridge_busy", retryable=True), ValueError("invalid snapshot")],
)
async def test_automation_mutation_refresh_failure_arms_bounded_reconciliation(
    refresh_error: Exception,
) -> None:
    runtime, _broker = _runtime()
    runtime.client.async_create_automation = AsyncMock(
        return_value={"automation_id": "aut_1", "revision": 1}
    )
    refresh = AsyncMock(side_effect=refresh_error)
    reconcile = Mock()
    runtime.automation_scheduler = SimpleNamespace(
        async_refresh=refresh,
        schedule_reconciliation=reconcile,
    )
    hass = _Hass(runtime)
    connection = _Connection()

    ws_create_automation(
        hass,
        connection,
        {
            "id": 55,
            "type": f"{DOMAIN}/create_automation",
            "name": "Daily check",
            "prompt": "Check the workspace",
            "target": {"workspace_path": "C:/work"},
            "schedule": {"kind": "interval", "seconds": 3600},
        },
    )
    await asyncio.sleep(0)

    refresh.assert_awaited_once()
    reconcile.assert_called_once_with()
    assert connection.results == [(55, {"automation_id": "aut_1", "revision": 1})]


async def test_mcp_elicitation_failure_uses_a_fixed_safe_websocket_message() -> None:
    runtime, _broker = _runtime()
    runtime.client.async_login_mcp = AsyncMock(
        side_effect=BridgeApiError("mcp_elicitation_unavailable", retryable=True)
    )
    hass = _Hass(runtime)
    connection = _Connection()

    ws_login_mcp(
        hass,
        connection,
        {"id": 56, "type": f"{DOMAIN}/login_mcp", "name": "remote_mcp"},
    )
    await asyncio.sleep(0)

    assert connection.errors == [
        (
            56,
            "mcp_elicitation_unavailable",
            "MCP configuration is unavailable until server prompts can be safely declined",
        )
    ]


async def test_get_automation_forwards_the_bounded_identifier() -> None:
    runtime, _broker = _runtime()
    runtime.client.async_get_automation = AsyncMock(
        return_value={"automation_id": "aut_1", "revision": 2}
    )
    hass = _Hass(runtime)
    connection = _Connection()

    ws_get_automation(
        hass,
        connection,
        {"id": 53, "type": f"{DOMAIN}/get_automation", "automation_id": "aut_1"},
    )
    await asyncio.sleep(0)

    runtime.client.async_get_automation.assert_awaited_once_with("aut_1")
    assert connection.results == [(53, {"automation_id": "aut_1", "revision": 2})]


async def test_feature_commands_project_known_safe_problem_codes() -> None:
    runtime, _broker = _runtime()
    runtime.client.async_create_automation = AsyncMock(
        side_effect=BridgeApiConflictError(
            problem=ProblemRecord.from_payload(
                409,
                {
                    "detail": {
                        "code": "automation_revision_conflict",
                        "retryable": False,
                    }
                },
            )
        )
    )
    hass = _Hass(runtime)
    connection = _Connection()

    ws_create_automation(
        hass,
        connection,
        {
            "id": 54,
            "type": f"{DOMAIN}/create_automation",
            "name": "Changed schedule",
            "prompt": "Check the workspace",
            "target": {"kind": "standalone", "project_id": "prj_one"},
            "schedule": {"kind": "once", "at": "2026-07-15T12:00:00Z"},
        },
    )
    await hass.finish()

    assert connection.errors == [
        (
            54,
            "automation_revision_conflict",
            "The automation changed; refresh and try again",
        )
    ]


async def test_mcp_login_returns_the_one_time_url_only_in_the_direct_response() -> None:
    runtime, _broker = _runtime()
    runtime.client.async_login_mcp = AsyncMock(
        return_value={"authorization_url": "https://auth.example.invalid/one-time"}
    )
    hass = _Hass(runtime)
    connection = _Connection()

    ws_login_mcp(
        hass,
        connection,
        {"id": 52, "type": f"{DOMAIN}/login_mcp", "name": "remote_mcp"},
    )
    await asyncio.sleep(0)

    runtime.client.async_login_mcp.assert_awaited_once_with("remote_mcp")
    assert connection.results == [
        (52, {"authorization_url": "https://auth.example.invalid/one-time"})
    ]


async def test_disabled_mcp_reports_the_app_option_instead_of_an_outdated_app() -> None:
    runtime, _broker = _runtime()
    runtime.client.async_login_mcp = AsyncMock(side_effect=BridgeApiMcpDisabledError())
    hass = _Hass(runtime)
    connection = _Connection()

    ws_login_mcp(
        hass,
        connection,
        {"id": 52, "type": f"{DOMAIN}/login_mcp", "name": "remote_mcp"},
    )
    await asyncio.sleep(0)

    assert connection.errors == [
        (
            52,
            "mcp_disabled",
            "Enable MCP in the Codex Bridge App configuration and restart",
        )
    ]


async def test_singular_thread_filter_preserves_the_retiring_panel_contract() -> None:
    runtime, broker = _runtime()
    hass = _Hass(runtime)
    connection = _Connection()

    ws_subscribe_events(
        hass,
        connection,
        {
            "id": 6,
            "type": f"{DOMAIN}/subscribe_events",
            "after": 0,
            "thread_id": "thr_1",
        },
    )
    await asyncio.sleep(0)
    broker._publish_event(_event(1))
    broker._publish_event(_event(2, scope="thread", thread_id="thr_2"))
    broker._publish_event(_event(3, scope="thread", thread_id="thr_1"))
    await asyncio.sleep(0)
    connection.subscriptions[6]()
    await hass.finish()

    assert connection.events == [
        (
            6,
            {
                "event_id": "evt_3",
                "sequence": 3,
                "event_type": "thread.updated",
                "payload": {"state": "pending"},
                "timestamp": "2026-07-13T12:00:00Z",
            },
        )
    ]


async def test_v1_slow_websocket_receives_explicit_snapshot_signal() -> None:
    runtime, broker = _runtime(queue_size=1)
    hass = _Hass(runtime)
    connection = _Connection()

    ws_subscribe_events(
        hass,
        connection,
        {"id": 7, "type": f"{DOMAIN}/subscribe_events", "after": 0},
    )
    await asyncio.sleep(0)
    broker._publish_event(_event(1))
    broker._cursor = 2
    broker._publish_event(_event(2))
    await asyncio.sleep(0)
    await hass.finish()

    assert connection.events == [
        (
            7,
            {
                "type": "snapshot_required",
                "cursor": 2,
                "reason": "subscriber_overflow",
                "scope": "global",
            },
        )
    ]


async def test_legacy_panel_receives_cursor_advancing_snapshot_after_overflow() -> None:
    runtime, broker = _runtime(queue_size=1)
    hass = _Hass(runtime)
    connection = _Connection()

    ws_subscribe_events(
        hass,
        connection,
        {
            "id": 18,
            "type": f"{DOMAIN}/subscribe_events",
            "after": 0,
            "thread_id": "thr_1",
        },
    )
    await asyncio.sleep(0)
    broker._publish_event(_event(1, scope="thread", thread_id="thr_1"))
    broker._cursor = 2
    broker._publish_event(_event(2, scope="thread", thread_id="thr_1"))
    await asyncio.sleep(0)
    await hass.finish()

    assert connection.events == [
        (
            18,
            {
                "event_id": "snapshot_2",
                "sequence": 2,
                "event_type": "bridge.snapshot_required",
                "payload": {
                    "code": "snapshot_required",
                    "scope": "thread",
                    "thread_id": "thr_1",
                },
                "timestamp": "",
            },
        )
    ]


async def test_legacy_panel_receives_safe_error_when_broker_stops() -> None:
    runtime, broker = _runtime()
    hass = _Hass(runtime)
    connection = _Connection()

    ws_subscribe_events(
        hass,
        connection,
        {
            "id": 19,
            "type": f"{DOMAIN}/subscribe_events",
            "after": 0,
            "thread_id": "thr_1",
        },
    )
    await asyncio.sleep(0)
    broker._set_status(
        "authentication_failed", phase="stopped", retry_count=0, notify=True
    )
    await asyncio.sleep(0)
    await hass.finish()

    assert connection.events == [
        (
            19,
            {
                "event_type": "bridge.error",
                "payload": {
                    "code": "authentication_failed",
                    "error": "Bridge live updates stopped; polling will retry.",
                },
            },
        )
    ]


async def test_v1_get_events_returns_validated_batch_and_filters() -> None:
    runtime, _broker = _runtime()
    runtime.client.async_replay_events = AsyncMock(
        return_value={
            "events": [_event(1, scope="thread", thread_id="thr_1").as_dict()],
            "next_cursor": 1,
            "minimum_cursor": 0,
            "has_more": False,
            "heartbeat": False,
        }
    )
    hass = _Hass(runtime)
    connection = _Connection()

    ws_get_events(
        hass,
        connection,
        {
            "id": 8,
            "type": f"{DOMAIN}/get_events",
            "after": 0,
            "scopes": ["thread"],
            "thread_ids": ["thr_1"],
        },
    )
    await hass.finish()

    runtime.client.async_replay_events.assert_awaited_once_with(
        after=0, scopes=frozenset({"thread"}), thread_ids=frozenset({"thr_1"})
    )
    assert connection.results[0][1]["events"][0]["cursor"] == 1


async def test_singular_get_events_returns_legacy_list_for_current_panel() -> None:
    runtime, _broker = _runtime()
    runtime.client.async_replay_events = AsyncMock(
        return_value={
            "events": [_event(2, scope="thread", thread_id="thr_1").as_dict()],
            "next_cursor": 2,
            "minimum_cursor": 0,
            "has_more": False,
            "heartbeat": False,
        }
    )
    hass = _Hass(runtime)
    connection = _Connection()

    ws_get_events(
        hass,
        connection,
        {
            "id": 9,
            "type": f"{DOMAIN}/get_events",
            "after": 0,
            "thread_id": "thr_1",
        },
    )
    await hass.finish()

    assert connection.results[0][1] == [
        {
            "event_id": "evt_2",
            "sequence": 2,
            "event_type": "thread.updated",
            "payload": {"state": "pending"},
            "timestamp": "2026-07-13T12:00:00Z",
        }
    ]


async def test_event_status_and_config_never_expose_private_origin_or_errors() -> None:
    runtime, broker = _runtime()
    runtime.client.base_url = "http://secret.internal:8766"
    broker._status = {"state": "reconnecting", "phase": "wait", "retry_count": 2}
    hass = _Hass(runtime)
    connection = _Connection()

    ws_get_config(hass, connection, {"id": 10, "type": f"{DOMAIN}/get_config"})
    ws_get_event_status(
        hass, connection, {"id": 11, "type": f"{DOMAIN}/get_event_status"}
    )
    await hass.finish()

    assert connection.results == [
        (
            10,
            {
                "panel_title": "Codex",
                "connection_type": "supervisor",
                "api_version": 1,
                "capabilities": [],
                "web_search_mode": "disabled",
            },
        ),
        (
            11,
            {"state": "reconnecting", "phase": "wait", "retry_count": 2, "cursor": 0},
        ),
    ]
    assert "secret.internal" not in repr(connection.results)


async def test_upstream_exception_details_are_not_sent_to_browser() -> None:
    runtime, _broker = _runtime()
    runtime.client.async_get_status = AsyncMock(
        side_effect=BridgeApiError("private-token-sentinel")
    )
    hass = _Hass(runtime)
    connection = _Connection()

    ws_get_status(hass, connection, {"id": 12, "type": f"{DOMAIN}/get_status"})
    await hass.finish()

    assert connection.errors == [(12, "bridge_error", "Bridge request failed")]
    assert "private-token-sentinel" not in repr(connection.errors)


async def test_list_artifacts_exposes_only_the_safe_reservation_conflict_code() -> None:
    runtime, _broker = _runtime()
    runtime.client.async_list_artifacts = AsyncMock(
        side_effect=BridgeApiConflictError(
            problem=ProblemRecord.from_payload(
                409,
                {"detail": {"code": "reservation_conflict", "retryable": True}},
            )
        )
    )
    hass = _Hass(runtime)
    connection = _Connection()

    ws_list_artifacts(
        hass,
        connection,
        {"id": 121, "type": f"{DOMAIN}/list_artifacts", "thread_id": "thr_1"},
    )
    await hass.finish()

    assert connection.errors == [
        (
            121,
            "reservation_conflict",
            "Workspace files are temporarily unavailable while Codex is working",
        )
    ]


async def test_list_artifacts_redacts_unrecognized_busy_errors() -> None:
    runtime, _broker = _runtime()
    runtime.client.async_list_artifacts = AsyncMock(
        side_effect=BridgeApiConflictError(
            problem=ProblemRecord.from_payload(
                409,
                {"detail": {"code": "private_workspace_conflict", "retryable": True}},
            )
        )
    )
    hass = _Hass(runtime)
    connection = _Connection()

    ws_list_artifacts(
        hass,
        connection,
        {"id": 122, "type": f"{DOMAIN}/list_artifacts", "thread_id": "thr_1"},
    )
    await hass.finish()

    assert connection.errors == [(122, "bridge_error", "Bridge request failed")]
    assert "private_workspace_conflict" not in repr(connection.errors)


async def test_answer_interaction_forwards_exact_bounded_values_contract() -> None:
    runtime, _broker = _runtime()
    runtime.client.async_answer_interaction = AsyncMock(
        return_value={"status": "answered"}
    )
    hass = _Hass(runtime)
    connection = _Connection()
    message = {
        "id": 13,
        "type": f"{DOMAIN}/answer_interaction",
        "interaction_id": "int_1",
        "thread_id": "thr_1",
        "answers": [{"question_id": "question_1", "values": ["yes"]}],
        "client_request_id": "answer-1",
    }

    ws_answer_interaction(hass, connection, message)
    await hass.finish()

    runtime.client.async_answer_interaction.assert_awaited_once_with(
        "int_1",
        thread_id="thr_1",
        answers=[{"question_id": "question_1", "values": ["yes"]}],
        client_request_id="answer-1",
    )


async def test_interaction_commands_preserve_only_actionable_safe_problem_codes() -> (
    None
):
    runtime, _broker = _runtime()
    runtime.client.async_decide_interaction = AsyncMock(
        side_effect=BridgeApiGoneError(
            problem=ProblemRecord.from_payload(
                410,
                {"detail": {"code": "interaction_stale", "retryable": False}},
            )
        )
    )
    runtime.client.async_answer_interaction = AsyncMock(
        side_effect=BridgeApiConflictError(
            problem=ProblemRecord.from_payload(
                409,
                {
                    "detail": {
                        "code": "interaction_outcome_unknown",
                        "retryable": False,
                    }
                },
            )
        )
    )
    hass = _Hass(runtime)
    connection = _Connection()

    ws_decide_interaction(
        hass,
        connection,
        {
            "id": 31,
            "type": f"{DOMAIN}/decide_interaction",
            "interaction_id": "int_1",
            "thread_id": "thr_1",
            "run_id": "run_1",
            "turn_id": "turn_1",
            "item_id": "item_1",
            "decision": "decline",
            "client_request_id": "decision-1",
        },
    )
    ws_answer_interaction(
        hass,
        connection,
        {
            "id": 32,
            "type": f"{DOMAIN}/answer_interaction",
            "interaction_id": "int_2",
            "thread_id": "thr_1",
            "run_id": "run_1",
            "turn_id": "turn_1",
            "item_id": "item_2",
            "answers": [{"question_id": "question_1", "values": ["yes"]}],
            "client_request_id": "answer-1",
        },
    )
    await hass.finish()

    assert connection.errors == [
        (31, "interaction_stale", "This Codex request is no longer active"),
        (
            32,
            "interaction_outcome_unknown",
            "The response outcome could not be confirmed",
        ),
    ]


async def test_interaction_commands_still_redact_unrecognized_bridge_errors() -> None:
    runtime, _broker = _runtime()
    runtime.client.async_decide_interaction = AsyncMock(
        side_effect=BridgeApiError("private-interaction-sentinel")
    )
    hass = _Hass(runtime)
    connection = _Connection()

    ws_decide_interaction(
        hass,
        connection,
        {
            "id": 33,
            "type": f"{DOMAIN}/decide_interaction",
            "interaction_id": "int_1",
            "thread_id": "thr_1",
            "run_id": "run_1",
            "turn_id": "turn_1",
            "item_id": "item_1",
            "decision": "cancel",
            "client_request_id": "decision-2",
        },
    )
    await hass.finish()

    assert connection.errors == [(33, "bridge_error", "Bridge request failed")]
    assert "private-interaction-sentinel" not in repr(connection.errors)


async def test_explicit_unsubscribe_invokes_and_removes_subscription_callback() -> None:
    runtime, _broker = _runtime()
    hass = _Hass(runtime)
    connection = _Connection()
    called = 0

    def unsubscribe() -> None:
        nonlocal called
        called += 1

    connection.subscriptions[44] = unsubscribe
    ws_unsubscribe_events(
        hass,
        connection,
        {
            "id": 14,
            "type": f"{DOMAIN}/unsubscribe_events",
            "subscription_id": 44,
        },
    )
    await hass.finish()

    assert called == 1
    assert 44 not in connection.subscriptions
    assert connection.results == [(14, {"unsubscribed": True})]


async def test_compacted_journal_returns_snapshot_for_v1_and_advances_legacy_panel() -> (
    None
):
    runtime, _broker = _runtime()
    problem = ProblemRecord(
        status=410,
        code="event_cursor_expired",
        retryable=False,
        minimum_cursor=7,
        snapshot_required=True,
        snapshot_cursor=11,
        scope="thread",
        thread_id="thr_1",
    )
    runtime.client.async_replay_events = AsyncMock(
        side_effect=[
            BridgeApiGoneError(problem=problem),
            BridgeApiGoneError(problem=problem),
        ]
    )
    hass = _Hass(runtime)
    connection = _Connection()

    ws_get_events(
        hass,
        connection,
        {
            "id": 15,
            "type": f"{DOMAIN}/get_events",
            "after": 0,
            "scopes": ["thread"],
            "thread_ids": ["thr_1"],
        },
    )
    ws_get_events(
        hass,
        connection,
        {
            "id": 16,
            "type": f"{DOMAIN}/get_events",
            "after": 0,
            "thread_id": "thr_1",
        },
    )
    await hass.finish()

    assert connection.results[0][1]["snapshot_required"] == {
        "cursor": 11,
        "minimum_cursor": 7,
        "scope": "thread",
        "thread_id": "thr_1",
    }
    assert connection.results[1][1] == [
        {
            "event_id": "snapshot_11",
            "sequence": 11,
            "event_type": "bridge.snapshot_required",
            "payload": {
                "code": "snapshot_required",
                "scope": "thread",
                "thread_id": "thr_1",
            },
            "timestamp": "",
        }
    ]


async def test_thread_ids_without_thread_scope_are_rejected_before_subscribing() -> (
    None
):
    runtime, broker = _runtime()
    hass = _Hass(runtime)
    connection = _Connection()

    ws_subscribe_events(
        hass,
        connection,
        {
            "id": 17,
            "type": f"{DOMAIN}/subscribe_events",
            "after": 0,
            "scopes": ["auth"],
            "thread_ids": ["thr_1"],
        },
    )
    await hass.finish()

    assert connection.errors == [
        (17, "invalid_event_filter", "Event subscription is invalid")
    ]
    assert not broker._subscribers
