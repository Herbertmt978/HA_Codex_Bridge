from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.codex_bridge.bridge_api import BridgeApiConnectionError
from custom_components.codex_bridge.runtime import CodexBridgeRuntime


def _runtime(*, mode: str = "live") -> CodexBridgeRuntime:
    client = AsyncMock()
    client.negotiated_api_version = 1
    return CodexBridgeRuntime(
        entry_id="entry",
        title="Codex",
        client=client,
        connection_type="supervisor",
        discovery_uuid="a" * 32,
        api_version=1,
        web_search_mode=mode,
    )


async def test_capability_refresh_preserves_explicit_disabled_preference() -> None:
    runtime = _runtime(mode="disabled")
    runtime.client.async_refresh_ready.return_value = SimpleNamespace(
        capabilities=("api_v1", "web_search_v1")
    )
    runtime.automation_scheduler = SimpleNamespace(web_search_mode=None)

    assert await runtime.async_refresh_capabilities(force=True)

    assert runtime.web_search_payload() == {"web_search": "disabled"}
    assert runtime.automation_scheduler.web_search_mode == "disabled"


async def test_capability_refresh_removes_stale_provider_features_fail_closed() -> None:
    runtime = _runtime()
    runtime.capabilities = ("api_v1", "web_search_v1", "image_generation_v1")
    runtime.client.async_refresh_ready.return_value = SimpleNamespace(
        capabilities=("api_v1",)
    )
    runtime.automation_scheduler = SimpleNamespace(web_search_mode="live")

    assert await runtime.async_refresh_capabilities(force=True)

    assert runtime.capabilities == ("api_v1",)
    assert runtime.web_search_payload() == {}
    assert runtime.automation_scheduler.web_search_mode is None


async def test_capability_refresh_failure_keeps_last_known_runtime_state() -> None:
    runtime = _runtime()
    runtime.capabilities = ("api_v1", "web_search_v1")
    runtime.client.async_refresh_ready.side_effect = BridgeApiConnectionError()

    assert not await runtime.async_refresh_capabilities(force=True)

    assert runtime.capabilities == ("api_v1", "web_search_v1")
    assert runtime.web_search_payload() == {"web_search": "live"}


async def test_capability_refresh_starts_new_notification_support() -> None:
    runtime = _runtime()
    runtime.automation_notifications = SimpleNamespace(async_start=AsyncMock())
    runtime.client.async_refresh_ready.return_value = SimpleNamespace(
        capabilities=("api_v1", "automation_notifications_v1")
    )

    assert await runtime.async_refresh_capabilities(force=True)

    runtime.automation_notifications.async_start.assert_awaited_once()


async def test_question_notifications_reconcile_removed_capability() -> None:
    runtime = _runtime()
    runtime.capabilities = ("api_v1", "interactions_v2")
    runtime.question_notifications = SimpleNamespace(
        async_start=AsyncMock(), async_refresh=AsyncMock(),
    )
    runtime.client.async_refresh_ready.return_value = SimpleNamespace(capabilities=("api_v1",))

    assert await runtime.async_refresh_capabilities(force=True)

    runtime.question_notifications.async_start.assert_not_awaited()
    runtime.question_notifications.async_refresh.assert_awaited_once()


async def test_question_notifications_start_when_new_capability_is_advertised() -> None:
    runtime = _runtime()
    runtime.question_notifications = SimpleNamespace(async_start=AsyncMock(), async_refresh=AsyncMock())
    runtime.client.async_refresh_ready.return_value = SimpleNamespace(capabilities=("api_v1", "interactions_v2"))

    assert await runtime.async_refresh_capabilities(force=True)

    runtime.question_notifications.async_start.assert_awaited_once()


async def test_question_listeners_close_before_stream_and_client() -> None:
    runtime = _runtime()
    closed = []

    async def close_question():
        closed.append("questions")

    async def close_stream():
        closed.append("stream")

    async def close_client():
        closed.append("client")

    runtime.question_notifications = SimpleNamespace(async_close=close_question)
    runtime.event_broker = SimpleNamespace(async_close=close_stream)
    runtime.client.async_close = close_client
    await runtime.async_close()

    assert closed == ["questions", "stream", "client"]
