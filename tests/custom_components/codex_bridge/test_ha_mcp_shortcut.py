"""An HA shortcut must never renew authority or overwrite a changed MCP binding."""

from copy import deepcopy
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant

from custom_components.codex_bridge.bridge_api import BridgeApiError
from custom_components.codex_bridge.const import CONNECTION_TYPE_SUPERVISOR
from custom_components.codex_bridge.ha_mcp_shortcut import (
    ACCESS_TOKEN_EXPIRATION,
    FIXED_URL,
    HaMcpShortcut,
    HaMcpShortcutError,
    _fingerprint,
    _parse_record,
)


class MemoryStore:
    """Retain exact journal snapshots without accepting a token secret."""

    def __init__(self):
        self.saved = None
        self.history = []

    async def async_load(self):
        return deepcopy(self.saved)

    async def async_save(self, data):
        self.saved = deepcopy(data)
        self.history.append(deepcopy(data))


@pytest.fixture
def shortcut():
    user = SimpleNamespace(id="owned-user", is_active=True, is_admin=True, refresh_tokens={})
    auth = Mock()
    auth.async_get_user = AsyncMock(side_effect=lambda owner_id: user if owner_id == user.id else None)
    tokens = {}

    async def create(owner, **kwargs):
        token = SimpleNamespace(id="owned-refresh", user=owner, expire_at=None, **kwargs)
        tokens[token.id] = token
        owner.refresh_tokens[token.id] = token
        return token

    def remove(token):
        tokens.pop(token.id, None)
        user.refresh_tokens.pop(token.id, None)

    auth.async_create_refresh_token = AsyncMock(side_effect=create)
    auth.async_get_refresh_token.side_effect = tokens.get
    auth.async_remove_refresh_token.side_effect = remove
    auth.async_create_access_token.side_effect = ["synthetic.jwt.initial", "synthetic.jwt.rotated", "synthetic.jwt.next"]
    hass = Mock()
    hass.state = CoreState.running
    hass.is_running = HomeAssistant.is_running.__get__(
        SimpleNamespace(state=hass.state), HomeAssistant,
    )
    hass.auth = auth
    hass.config_entries.async_loaded_entries.return_value = [SimpleNamespace(domain="mcp_server")]
    client = Mock()
    client.async_add_mcp = AsyncMock(return_value={"name": "managed"})
    client.async_replace_mcp_credential = AsyncMock(return_value={})
    client.async_remove_managed_mcp = AsyncMock(return_value=None)
    client.async_list_mcp = AsyncMock(return_value=[])
    select = AsyncMock()
    store = MemoryStore()
    support = Mock(return_value=True)
    with (
        patch("custom_components.codex_bridge.ha_mcp_shortcut.Store", return_value=store),
        patch("custom_components.codex_bridge.ha_mcp_shortcut.llm.async_get_apis", return_value=[SimpleNamespace(id="assist")]),
        patch("custom_components.codex_bridge.ha_mcp_shortcut.async_track_time_interval"),
    ):
        helper = HaMcpShortcut(hass, "entry-owned", client,
            connection_type=CONNECTION_TYPE_SUPERVISOR, supports_capability=support,
            selection_callback=select)
        yield SimpleNamespace(helper=helper, hass=hass, auth=auth, client=client,
                              user=user, store=store, select=select, support=support, tokens=tokens)


async def _connect(case):
    result = await case.helper.async_connect(case.user, acknowledged=True)
    assert result["state"] == "configured"
    assert result["available"] is False
    return result["server_name"]


async def test_connect_requires_real_active_admin_and_explicit_consent(shortcut):
    case = shortcut
    impostor = SimpleNamespace(id=case.user.id, is_active=True, is_admin=True)
    for user, acknowledged in [(case.user, False), (None, True), (impostor, True)]:
        with pytest.raises(HaMcpShortcutError):
            await case.helper.async_connect(user, acknowledged=acknowledged)
    for attribute in ("is_active", "is_admin"):
        setattr(case.user, attribute, False)
        with pytest.raises(HaMcpShortcutError):
            await _connect(case)
        setattr(case.user, attribute, True)
    case.auth.async_create_refresh_token.assert_not_called()
    case.client.async_add_mcp.assert_not_called()


@pytest.mark.parametrize("condition", ["external", "capability", "unloaded", "multiple", "no_assist"])
async def test_unavailable_environment_creates_no_authority(shortcut, condition):
    case = shortcut
    if condition == "external":
        case.helper.connection_type = "external"
    elif condition == "capability":
        case.support.side_effect = lambda value: value != "mcp_credential_binding_v1"
    elif condition == "unloaded":
        case.hass.config_entries.async_loaded_entries.return_value = []
    elif condition == "multiple":
        case.hass.config_entries.async_loaded_entries.return_value = [Mock(), Mock()]
    else:
        with patch("custom_components.codex_bridge.ha_mcp_shortcut.llm.async_get_apis", return_value=[]):
            with pytest.raises(HaMcpShortcutError):
                await _connect(case)
        return
    with pytest.raises(HaMcpShortcutError):
        await _connect(case)
    case.auth.async_create_refresh_token.assert_not_called()


async def test_connect_journals_before_native_call_and_hides_secrets(shortcut):
    case = shortcut

    async def create(payload):
        assert case.store.saved["record"]["state"] == "creating"
        assert case.store.saved["record"]["fingerprint"] == _fingerprint(payload["authentication"]["token"])
        assert "synthetic.jwt.initial" not in repr(case.store.saved)
        return {"name": payload["name"]}

    case.client.async_add_mcp.side_effect = create
    name = await _connect(case)
    payload = case.client.async_add_mcp.call_args.args[0]
    assert name.startswith("ha-assist-") and len(name) == 22
    assert payload == {"name": name, "url": FIXED_URL, "local": True,
                       "local_acknowledged": True, "authentication": {"mode": "bearer", "token": "synthetic.jwt.initial"},
                       "auth_acknowledged": True, "require_tool_selection": True}
    kwargs = case.auth.async_create_refresh_token.call_args.kwargs
    assert kwargs["token_type"] == "normal"
    assert kwargs["access_token_expiration"] == timedelta(hours=8) == ACCESS_TOKEN_EXPIRATION
    case.select.assert_awaited_once_with(name, True)
    assert case.store.saved["record"]["state"] == "connected"
    assert "synthetic.jwt.initial" not in repr(case.helper)


async def test_reload_and_close_preserve_grant_without_minting(shortcut):
    case = shortcut
    await _connect(case)
    saved = deepcopy(case.store.saved)
    case.auth.async_create_access_token.reset_mock()
    case.auth.async_create_refresh_token.reset_mock()
    case.select.reset_mock()
    case.client.async_add_mcp.reset_mock()
    await case.helper.async_close()
    case.helper._closed = False
    await case.helper.async_setup()
    case.auth.async_create_access_token.assert_not_called()
    case.auth.async_create_refresh_token.assert_not_called()
    case.auth.async_remove_refresh_token.assert_not_called()
    case.client.async_add_mcp.assert_not_called()
    case.select.assert_not_called()
    assert case.store.saved == saved


async def test_rotation_is_bound_to_old_secret_and_never_enables_server(shortcut):
    case = shortcut
    name = await _connect(case)
    await case.helper.async_refresh()
    assert case.client.async_replace_mcp_credential.call_args.args == (name, {
        "authentication": {"mode": "bearer", "token": "synthetic.jwt.rotated"},
        "auth_acknowledged": True, "expected_url": FIXED_URL,
        "expected_token_sha256": _fingerprint("synthetic.jwt.initial"),
    })
    assert case.store.saved["record"]["fingerprint"] == _fingerprint("synthetic.jwt.rotated")
    assert case.store.saved["record"]["pending_fingerprint"] is None
    assert "synthetic.jwt.rotated" not in repr(case.store.history)
    case.client.async_manage_mcp.assert_not_called()
    assert case.auth.async_create_refresh_token.await_count == 1


async def test_busy_rotation_retains_old_expiry_and_existing_authority(shortcut):
    case = shortcut
    await _connect(case)
    old = deepcopy(case.store.saved)
    case.client.async_replace_mcp_credential.side_effect = BridgeApiError(code="mcp_config_conflict")
    result = await case.helper.async_refresh()
    assert result["state"] == "retry" and result["available"] is False
    assert case.store.saved == old
    case.auth.async_remove_refresh_token.assert_not_called()
    case.client.async_remove_managed_mcp.assert_not_called()


async def test_changed_binding_revokes_own_token_and_preserves_changed_server(shortcut):
    case = shortcut
    name = await _connect(case)
    case.client.async_replace_mcp_credential.side_effect = BridgeApiError(code="mcp_credential_binding_conflict")
    case.client.async_remove_managed_mcp.side_effect = BridgeApiError(code="mcp_credential_binding_conflict")
    with pytest.raises(HaMcpShortcutError, match="administrator authorisation"):
        await case.helper.async_refresh()
    case.auth.async_remove_refresh_token.assert_called_once()
    case.select.assert_any_await(name, False)
    assert case.store.saved == {"record": None}
    case.client.async_remove_mcp.assert_not_called()


@pytest.mark.parametrize("invalid", ["inactive", "demoted", "revoked", "expired", "unloaded"])
async def test_invalid_owner_or_integration_revokes_and_detaches(shortcut, invalid):
    case = shortcut
    name = await _connect(case)
    if invalid == "inactive":
        case.user.is_active = False
    elif invalid == "demoted":
        case.user.is_admin = False
    elif invalid == "revoked":
        case.tokens.clear()
    elif invalid == "expired":
        case.tokens["owned-refresh"].expire_at = 1
    else:
        case.hass.config_entries.async_loaded_entries.return_value = []
    status = await case.helper.async_status()
    assert status["available"] is False and status["state"] == "reauthorise"
    case.select.assert_any_await(name, False)
    assert case.store.saved == {"record": None}


async def test_expired_access_is_unavailable_but_existing_grant_can_refresh(shortcut):
    case = shortcut
    await _connect(case)
    case.helper._record["access_expires_at"] = 1
    assert (await case.helper.async_status())["state"] == "expired"
    await case.helper.async_refresh()
    assert case.auth.async_create_refresh_token.await_count == 1
    assert case.helper._record["access_expires_at"] > 1


async def test_status_requires_native_ready_and_explicit_tools(shortcut):
    case = shortcut
    name = await _connect(case)
    case.client.async_list_mcp.return_value = [{"name": name, "enabled": False}]
    assert (await case.helper.async_status())["state"] == "paused"
    row = {"name": name, "enabled": True, "startup": "ready", "credential_configured": True,
           "tool_policy": "selected", "tool_count": 1}
    case.client.async_list_mcp.return_value = [row]
    assert (await case.helper.async_status())["available"] is True
    for key, value in [("startup", "unknown"), ("tool_policy", "all"), ("tool_count", 0), ("credential_configured", False), ("status_unavailable", True)]:
        case.client.async_list_mcp.return_value = [{**row, key: value}]
        assert (await case.helper.async_status())["available"] is False


async def test_failed_native_create_recovery_removes_only_exact_owned_binding(shortcut):
    case = shortcut
    case.client.async_add_mcp.side_effect = RuntimeError("synthetic.jwt.initial must never escape")
    with pytest.raises(HaMcpShortcutError) as raised:
        await _connect(case)
    assert "synthetic.jwt" not in str(raised.value) + repr(raised.value)
    assert raised.value.__cause__ is None
    case.auth.async_remove_refresh_token.assert_called_once()
    args = case.client.async_remove_managed_mcp.call_args.args
    assert args[1] == {"expected_url": FIXED_URL, "expected_token_sha256": _fingerprint("synthetic.jwt.initial")}
    case.client.async_remove_mcp.assert_not_called()


async def test_prepare_crash_recovers_only_journal_owner_client_and_generated_name(shortcut):
    case = shortcut
    await _connect(case)
    own = case.tokens["owned-refresh"]
    other = SimpleNamespace(**{**vars(own), "id": "unrelated", "client_name": "Unrelated sign-in"})
    case.user.refresh_tokens[other.id] = other
    case.store.saved["record"].update(state="preparing", token_id=None, fingerprint=None, access_expires_at=0)
    await case.helper.async_setup()
    case.auth.async_remove_refresh_token.assert_called_once_with(own)
    assert case.user.refresh_tokens == {"unrelated": other}
    case.auth.async_create_refresh_token.assert_awaited_once()
    case.client.async_remove_managed_mcp.assert_not_called()


async def test_rotation_crash_checks_pending_then_old_fingerprint(shortcut):
    case = shortcut
    name = await _connect(case)
    pending = _fingerprint("synthetic.jwt.pending")
    case.store.saved["record"].update(state="rotating", pending_fingerprint=pending, pending_expires_at=2000000000)
    case.client.async_remove_managed_mcp.side_effect = [BridgeApiError(code="mcp_credential_binding_conflict"), None]
    await case.helper.async_setup()
    assert [call.args for call in case.client.async_remove_managed_mcp.await_args_list] == [
        (name, {"expected_url": FIXED_URL, "expected_token_sha256": pending}),
        (name, {"expected_url": FIXED_URL, "expected_token_sha256": _fingerprint("synthetic.jwt.initial")}),
    ]
    assert case.store.saved == {"record": None}


async def test_busy_disconnect_revokes_immediately_and_retains_retry_journal(shortcut):
    case = shortcut
    await _connect(case)
    case.client.async_remove_managed_mcp.side_effect = BridgeApiError(code="mcp_config_conflict")
    result = await case.helper.async_disconnect()
    assert result["state"] == "cleanup_pending"
    assert case.tokens == {}
    assert case.store.saved["record"]["state"] == "disconnecting"
    case.client.async_remove_managed_mcp.side_effect = None
    await case.helper.async_disconnect()
    assert case.store.saved == {"record": None}


async def test_store_failure_during_connect_still_revokes_minted_token(shortcut):
    case = shortcut
    original_save = case.store.async_save
    count = 0

    async def fail_second(data):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("private store unavailable")
        await original_save(data)

    case.store.async_save = fail_second
    with pytest.raises(HaMcpShortcutError):
        await _connect(case)
    assert case.tokens == {}
    case.client.async_add_mcp.assert_not_called()


async def test_invalid_private_journal_fails_closed_without_touching_accounts(shortcut):
    case = shortcut
    case.store.saved = {"record": {"token": "synthetic.secret"}}
    await case.helper.async_setup()
    assert (await case.helper.async_status())["state"] == "invalid_journal"
    with pytest.raises(HaMcpShortcutError):
        await _connect(case)
    case.auth.async_create_refresh_token.assert_not_called()
    case.auth.async_remove_refresh_token.assert_not_called()


@pytest.mark.parametrize("expiry", [True, float("nan"), float("inf"), -1, 32503680001])
async def test_unbounded_expiry_is_rejected_in_journal(shortcut, expiry):
    await _connect(shortcut)
    record = deepcopy(shortcut.store.saved)
    record["record"]["access_expires_at"] = expiry
    with pytest.raises(ValueError):
        _parse_record(record)


async def test_fresh_remove_loads_owned_journal_without_starting_or_minting(shortcut):
    case = shortcut
    await _connect(case)
    case.helper._record = None
    case.helper._loaded = False
    case.auth.async_create_refresh_token.reset_mock()
    case.auth.async_create_access_token.reset_mock()
    await case.helper.async_remove()
    assert case.tokens == {} and case.store.saved == {"record": None}
    case.auth.async_create_refresh_token.assert_not_called()
    case.auth.async_create_access_token.assert_not_called()
    assert case.helper._remove_timer is None


async def test_connection_cannot_overwrite_an_existing_unloaded_grant(shortcut):
    case = shortcut
    await _connect(case)
    saved = deepcopy(case.store.saved)
    case.helper._record = None
    case.helper._loaded = False
    with pytest.raises(HaMcpShortcutError):
        await _connect(case)
    assert case.auth.async_create_refresh_token.await_count == 1
    assert case.store.saved == saved


async def test_hourly_tick_does_not_create_new_authority(shortcut):
    case = shortcut
    await case.helper._tick(None)
    case.auth.async_create_refresh_token.assert_not_called()
    case.auth.async_create_access_token.assert_not_called()
    case.client.async_add_mcp.assert_not_called()
    await _connect(case)
    await case.helper._tick(None)
    assert case.auth.async_create_refresh_token.await_count == 1
    assert case.client.async_replace_mcp_credential.await_count == 1


async def test_revocation_callback_detaches_owned_selection_and_close_stops_it(shortcut):
    case = shortcut
    name = await _connect(case)
    tasks = []
    case.hass.async_create_task.side_effect = lambda coroutine: tasks.append(asyncio.create_task(coroutine))
    case.tokens.clear()
    case.helper._revoked()
    await asyncio.gather(*tasks)
    case.select.assert_any_await(name, False)
    assert case.store.saved == {"record": None}
    await case.helper.async_close()
    case.hass.async_create_task.reset_mock()
    case.helper._revoked()
    case.hass.async_create_task.assert_not_called()


async def test_selection_failure_rolls_back_own_server_and_token(shortcut):
    case = shortcut
    case.select.side_effect = [RuntimeError("selection unavailable"), None]
    with pytest.raises(HaMcpShortcutError):
        await _connect(case)
    assert case.tokens == {}
    case.client.async_remove_managed_mcp.assert_awaited_once()
    assert case.store.saved == {"record": None}


async def test_pending_cleanup_survives_missing_binding_capability(shortcut):
    case = shortcut
    await _connect(case)
    case.support.side_effect = lambda name: name != "mcp_credential_binding_v1"
    result = await case.helper.async_disconnect()
    assert result["state"] == "cleanup_pending" and case.tokens == {}
    case.client.async_remove_managed_mcp.assert_not_called()
    case.support.side_effect = None
    await case.helper.async_disconnect()
    assert case.store.saved == {"record": None}


@pytest.mark.parametrize("rows", [None, {"name": "bad"}, [None] * 129])
async def test_malformed_native_status_does_not_report_ready(shortcut, rows):
    await _connect(shortcut)
    shortcut.client.async_list_mcp.return_value = rows
    assert (await shortcut.helper.async_status())["available"] is False


async def _restore_during_mcp_startup(case, *, startup_state=CoreState.not_running):
    """Restore an existing grant while the optional MCP entry is still loading."""
    name = await _connect(case)
    await case.helper.async_close()
    case.helper = HaMcpShortcut(
        case.hass, "entry-owned", case.client,
        connection_type=CONNECTION_TYPE_SUPERVISOR,
        supports_capability=case.support, selection_callback=case.select,
    )
    case.hass.state = startup_state
    case.hass.is_running = HomeAssistant.is_running.__get__(
        SimpleNamespace(state=startup_state), HomeAssistant,
    )
    case.hass.config_entries.async_entries.return_value = [SimpleNamespace(domain="mcp_server")]
    case.hass.config_entries.async_loaded_entries.return_value = []
    remove_listener = Mock()
    case.hass.bus.async_listen_once.return_value = remove_listener
    for method in (
        case.auth.async_create_refresh_token, case.auth.async_create_access_token,
        case.auth.async_remove_refresh_token, case.client.async_add_mcp,
        case.client.async_replace_mcp_credential, case.client.async_remove_managed_mcp,
        case.select,
    ):
        method.reset_mock()
    saved = deepcopy(case.store.saved)
    await case.helper.async_setup()
    case.hass.bus.async_listen_once.assert_called_once()
    event, callback = case.hass.bus.async_listen_once.call_args.args
    assert event == EVENT_HOMEASSISTANT_STARTED
    return name, saved, callback, remove_listener


@pytest.mark.parametrize("startup_state", [CoreState.not_running, CoreState.starting])
async def test_pending_startup_preserves_existing_grant_without_renewal(shortcut, startup_state):
    case = shortcut
    name, saved, callback, _ = await _restore_during_mcp_startup(case, startup_state=startup_state)
    assert case.hass.is_running is (startup_state is CoreState.starting)
    assert (await case.helper.async_status())["state"] == "unavailable"
    with pytest.raises(HaMcpShortcutError):
        await case.helper.async_refresh()
    await case.helper._tick(None)
    assert case.store.saved == saved
    assert case.tokens
    case.auth.async_remove_refresh_token.assert_not_called()
    case.auth.async_create_refresh_token.assert_not_called()
    case.auth.async_create_access_token.assert_not_called()
    case.client.async_replace_mcp_credential.assert_not_called()

    case.hass.config_entries.async_loaded_entries.return_value = [SimpleNamespace(domain="mcp_server")]
    case.client.async_list_mcp.return_value = [{"name": name, "enabled": False}]
    case.hass.state = CoreState.running
    case.hass.is_running = True
    await callback(SimpleNamespace())
    assert case.helper._startup_settled is True
    assert case.helper._remove_start_listener is None
    assert (await case.helper.async_status())["state"] == "paused"
    assert case.store.saved == saved
    case.auth.async_remove_refresh_token.assert_not_called()
    case.auth.async_create_refresh_token.assert_not_called()
    case.auth.async_create_access_token.assert_not_called()
    case.client.async_add_mcp.assert_not_called()
    case.select.assert_not_called()


async def test_settled_failed_mcp_startup_revokes_existing_grant(shortcut):
    case = shortcut
    name, _, callback, _ = await _restore_during_mcp_startup(case)
    case.hass.state = CoreState.running
    case.hass.is_running = True
    await callback(SimpleNamespace())
    assert case.helper._startup_settled is True
    assert case.tokens == {}
    assert case.store.saved == {"record": None}
    assert (await case.helper.async_status())["state"] == "reauthorise"
    case.auth.async_remove_refresh_token.assert_called_once()
    case.select.assert_awaited_once_with(name, False)
    case.client.async_remove_managed_mcp.assert_awaited_once_with(name, {
        "expected_url": FIXED_URL,
        "expected_token_sha256": _fingerprint("synthetic.jwt.initial"),
    })
    case.auth.async_create_refresh_token.assert_not_called()
    case.auth.async_create_access_token.assert_not_called()


async def test_close_removes_pending_start_listener_and_preserves_grant(shortcut):
    case = shortcut
    _, saved, callback, remove_listener = await _restore_during_mcp_startup(case)
    await case.helper.async_close()
    await case.helper.async_close()
    remove_listener.assert_called_once_with()
    assert case.helper._remove_start_listener is None
    await callback(SimpleNamespace())  # An already dispatched event must be harmless.
    assert case.store.saved == saved
    assert case.tokens
    case.auth.async_remove_refresh_token.assert_not_called()
    case.auth.async_create_access_token.assert_not_called()
    case.client.async_list_mcp.assert_not_called()
    case.client.async_remove_managed_mcp.assert_not_called()


async def test_absent_optional_entry_does_not_defer_revocation_until_start(shortcut):
    case = shortcut
    await _connect(case)
    case.hass.is_running = False
    case.hass.state = CoreState.not_running
    case.hass.config_entries.async_loaded_entries.return_value = []
    case.hass.config_entries.async_entries.return_value = []
    await case.helper.async_setup()
    assert case.tokens == {}
    assert case.store.saved == {"record": None}
    case.hass.bus.async_listen_once.assert_not_called()
