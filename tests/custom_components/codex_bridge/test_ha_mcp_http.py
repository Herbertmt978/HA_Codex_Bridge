"""Exercise the HA administrator boundary without minting a real credential."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import Unauthorized

from custom_components.codex_bridge.http import CodexBridgeHaMcpView
from custom_components.codex_bridge.ha_mcp_shortcut import HaMcpShortcutError


class Request(dict):
    content_type = "application/json"

    def __init__(self, user, payload=None, *, raw=None):
        super().__init__(hass_user=user)
        self.raw = raw if raw is not None else json.dumps(payload).encode()
        self.content = self

    async def iter_chunked(self, size):
        for offset in range(0, len(self.raw), size):
            yield self.raw[offset:offset + size]


@pytest.fixture
def view():
    public = {"state": "paused", "code": "paused", "available": False,
              "configured": True, "server_name": "ha-assist-0123456789ab",
              "requires_tool_selection": True}
    shortcut = SimpleNamespace(**{
        method: AsyncMock(return_value={**public, "token": "synthetic-private-secret"})
        for method in ("async_status", "async_connect", "async_refresh", "async_disconnect")
    })
    runtime = SimpleNamespace(ha_mcp_shortcut=shortcut, async_refresh_capabilities=AsyncMock())
    with patch("custom_components.codex_bridge.http.async_get_runtime", return_value=runtime):
        yield SimpleNamespace(view=CodexBridgeHaMcpView(None), runtime=runtime,
                              helper=shortcut, public=public,
                              user=SimpleNamespace(is_admin=True, is_active=True))


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [None, SimpleNamespace(is_admin=False)])
async def test_anonymous_and_non_admin_requests_cannot_read_or_change_grant(view, user):
    for method in (view.view.get, view.view.post):
        with pytest.raises(Unauthorized):
            await method(Request(user, {"operation": "connect", "acknowledged": True}))
    view.helper.async_connect.assert_not_called()
    view.helper.async_status.assert_not_called()
    view.runtime.async_refresh_capabilities.assert_not_called()


@pytest.mark.asyncio
async def test_connect_uses_authenticated_user_and_never_reflects_private_metadata(view):
    response = await view.view.post(Request(view.user, {"operation": "connect", "acknowledged": True}))
    assert response.status == 200
    assert json.loads(response.text) == view.public
    assert response.headers["Cache-Control"] == "no-store"
    view.helper.async_connect.assert_awaited_once_with(view.user, acknowledged=True)
    assert "synthetic-private-secret" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"operation": "connect"}, {"operation": "connect", "acknowledged": 1},
    {"operation": "connect", "acknowledged": True, "user_id": "other-user"},
    {"operation": "connect", "acknowledged": True, "url": "http://other-home/mcp"},
    {"operation": "disconnect", "token": "synthetic-private-secret"},
    {"operation": ["connect"]}, [],
])
async def test_unknown_identity_destination_and_credential_fields_are_rejected(view, payload):
    response = await view.view.post(Request(view.user, payload))
    assert response.status == 400
    assert json.loads(response.text) == {"code": "mcp_request_invalid"}
    view.helper.async_connect.assert_not_called()
    view.helper.async_disconnect.assert_not_called()


@pytest.mark.asyncio
async def test_oversize_request_and_wrong_content_type_do_not_mint_authority(view):
    request = Request(view.user, raw=b"x" * 1025)
    assert (await view.view.post(request)).status == 400
    request = Request(view.user, {"operation": "connect", "acknowledged": True})
    request.content_type = "text/plain"
    assert (await view.view.post(request)).status == 400
    view.helper.async_connect.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["disconnect", "refresh"])
async def test_existing_grant_actions_do_not_reauthorise(view, operation):
    response = await view.view.post(Request(view.user, {"operation": operation}))
    assert response.status == 200
    getattr(view.helper, f"async_{operation}").assert_awaited_once_with()
    view.helper.async_connect.assert_not_called()


@pytest.mark.asyncio
async def test_unavailable_and_fixed_helper_errors_have_no_private_response(view):
    view.helper.async_status.side_effect = HaMcpShortcutError("retry")
    response = await view.view.get(Request(view.user))
    assert response.status == 409
    assert json.loads(response.text) == {"code": "retry"}
    view.runtime.ha_mcp_shortcut = None
    response = await view.view.get(Request(view.user))
    assert response.status == 409
    assert json.loads(response.text) == {"code": "unavailable"}


@pytest.mark.asyncio
async def test_invalid_managed_status_cannot_publish_a_foreign_server_or_secret(view):
    view.helper.async_status.return_value = {**view.public, "server_name": "manual-server"}
    response = await view.view.get(Request(view.user))
    assert response.status == 409
    assert json.loads(response.text) == {"code": "unavailable"}
