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
