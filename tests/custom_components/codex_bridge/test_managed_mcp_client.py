"""Negotiated ownership guards must never reach an older App."""

from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.codex_bridge.bridge_api import BridgeApiClient, BridgeApiCapabilityError


def client(capabilities):
    result = BridgeApiClient(Mock(), "http://127.0.0.1:8766", "fixture-bridge-token-0123456789abcdef")
    result._api_version = 1
    result._capabilities = frozenset(capabilities)
    result._async_json = AsyncMock(return_value={})
    result._async_no_content = AsyncMock()
    return result


BINDING = {"expected_url": "http://homeassistant:8123/api/mcp/assist", "expected_token_sha256": "a" * 64}
CAPABILITIES = {"mcp_admin_v1", "mcp_credentials_v1", "mcp_credential_binding_v1"}


@pytest.mark.asyncio
async def test_old_app_preserves_manual_rotation_but_refuses_managed_guards():
    bridge = client(CAPABILITIES - {"mcp_credential_binding_v1"})
    authentication = {"mode": "bearer", "token": "synthetic-test-only"}
    await bridge.async_replace_mcp_credential("manual", {"authentication": authentication, "auth_acknowledged": True})
    bridge._async_json.reset_mock()
    with pytest.raises(BridgeApiCapabilityError):
        await bridge.async_replace_mcp_credential("owned", {**BINDING, "authentication": authentication, "auth_acknowledged": True})
    with pytest.raises(BridgeApiCapabilityError):
        await bridge.async_remove_managed_mcp("owned", BINDING)
    bridge._async_json.assert_not_called()
    bridge._async_no_content.assert_not_called()


@pytest.mark.asyncio
async def test_guarded_remove_keeps_binding_in_private_http_body():
    bridge = client(CAPABILITIES)
    await bridge.async_remove_managed_mcp("owned", BINDING)
    bridge._async_no_content.assert_awaited_once_with(
        "POST", "/mcp/servers/owned/managed/remove", json_body=BINDING, expected_status={204},
    )
    assert "homeassistant" not in bridge._async_no_content.call_args.args[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"expected_url": BINDING["expected_url"]}, {**BINDING, "token": "synthetic-test-only"}])
async def test_managed_removal_requires_exact_guard_pair(payload):
    bridge = client(CAPABILITIES)
    with pytest.raises(ValueError):
        await bridge.async_remove_managed_mcp("owned", payload)
    bridge._async_no_content.assert_not_called()
