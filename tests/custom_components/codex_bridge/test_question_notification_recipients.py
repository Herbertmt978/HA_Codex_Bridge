"""Recipient authority comes from Core registrations, not names or action data."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.config_entries import ConfigEntryState
import pytest

from custom_components.codex_bridge.question_notification_recipients import (
    async_resolve_question_recipients,
)


def _fixture(monkeypatch):
    entry = SimpleNamespace(
        entry_id="registered_phone",
        domain="mobile_app",
        disabled_by=None,
        state=ConfigEntryState.LOADED,
        title="Phone",
        data={
            "webhook_id": "private-registration-locator",
            "device_id": "native-device",
            "user_id": "admin-user",
            "os_name": "Android",
        },
    )
    device = SimpleNamespace(
        id="registry-device",
        disabled_by=None,
        config_entries={entry.entry_id},
        name_by_user="My phone",
        name="Phone",
    )
    registry = SimpleNamespace(async_get_device=lambda **_kwargs: device)
    monkeypatch.setattr(
        "custom_components.codex_bridge.question_notification_recipients.dr.async_get",
        lambda _hass: registry,
    )
    user = SimpleNamespace(is_active=True, is_admin=True)
    targets = {"mobile_app_my_phone": "private-registration-locator"}
    hass = SimpleNamespace(
        data={
            "mobile_app": {
                "config_entries": {"private-registration-locator": entry},
                "notify": SimpleNamespace(registered_targets=targets),
            }
        },
        config_entries=SimpleNamespace(async_get_entry=lambda _entry_id: entry),
        services=SimpleNamespace(
            has_service=lambda domain, name: domain == "notify" and name in targets
        ),
        auth=SimpleNamespace(async_get_user=AsyncMock(return_value=user)),
    )
    return hass, entry, device, user, targets


async def test_exact_registered_target_and_admin_are_required(monkeypatch):
    hass, entry, _device, _user, targets = _fixture(monkeypatch)
    recipients = await async_resolve_question_recipients(hass, [entry.entry_id])
    assert len(recipients) == 1
    recipient = recipients[0]
    assert recipient.service == "mobile_app_my_phone"
    assert recipient.device_id == "registry-device"
    assert recipient.user_id == "admin-user"
    assert recipient.inline_reply is True
    assert "private-registration-locator" not in repr(recipient)
    targets["mobile_app_my_phone"] = "different-registration"
    assert await async_resolve_question_recipients(hass, [entry.entry_id]) == ()


@pytest.mark.parametrize(
    "revocation",
    [
        "user_inactive",
        "user_not_admin",
        "device_disabled",
        "registration_disabled",
        "registration_unloaded",
        "device_not_registered",
        "entry_replaced",
    ],
)
async def test_revoked_or_unverified_binding_is_not_a_recipient(
    monkeypatch, revocation
):
    hass, entry, device, user, _targets = _fixture(monkeypatch)
    if revocation == "user_inactive":
        user.is_active = False
    elif revocation == "user_not_admin":
        user.is_admin = False
    elif revocation == "device_disabled":
        device.disabled_by = "user"
    elif revocation == "registration_disabled":
        entry.disabled_by = "user"
    elif revocation == "registration_unloaded":
        entry.state = ConfigEntryState.NOT_LOADED
    elif revocation == "device_not_registered":
        device.config_entries = set()
    else:
        hass.config_entries.async_get_entry = lambda _entry_id: None
    assert await async_resolve_question_recipients(hass, [entry.entry_id]) == ()


async def test_unknown_phone_platform_has_open_chat_only(monkeypatch):
    hass, entry, _device, _user, _targets = _fixture(monkeypatch)
    entry.data["os_name"] = "Unverified platform"
    (recipient,) = await async_resolve_question_recipients(hass, [entry.entry_id])
    assert recipient.inline_reply is False


async def test_ambiguous_target_and_invalid_selection_fail_closed(monkeypatch):
    hass, entry, _device, _user, targets = _fixture(monkeypatch)
    targets["mobile_app_alias"] = "private-registration-locator"
    assert await async_resolve_question_recipients(hass, [entry.entry_id]) == ()
    assert await async_resolve_question_recipients(hass, [entry.entry_id] * 2) == ()
    assert (
        await async_resolve_question_recipients(hass, ["arbitrary.notify.service"])
        == ()
    )
