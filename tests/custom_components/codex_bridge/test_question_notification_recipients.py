"""Recipient authority comes from Core registrations, not names or action data."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    MockUser,
    mock_integration,
)

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
        config_entry_id=entry.entry_id,
        name_by_user="My phone",
        name="Phone",
    )

    def _device_by_identifier(identifier, config_entry_id):
        if (
            identifier == ("mobile_app", "native-device")
            and config_entry_id == device.config_entry_id
        ):
            return device
        return None

    registry = SimpleNamespace(async_get_device_by_identifier=_device_by_identifier)
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
        device.config_entry_id = "another-entry"
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


async def test_device_lookup_is_scoped_to_the_registration(
    hass: HomeAssistant,
    hass_admin_user: MockUser,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Another entry's device with the same identifier is neither chosen nor warned on."""
    caplog.set_level(logging.WARNING)
    # Stand in for Core's mobile_app so teardown can unload the loaded entry.
    mock_integration(
        hass, MockModule("mobile_app", async_unload_entry=AsyncMock(return_value=True))
    )
    other = MockConfigEntry(domain="mobile_app", entry_id="other_phone")
    other.add_to_hass(hass)
    entry = MockConfigEntry(
        domain="mobile_app",
        entry_id="registered_phone",
        title="Phone",
        data={
            "webhook_id": "private-registration-locator",
            "device_id": "native-device",
            "user_id": hass_admin_user.id,
            "os_name": "Android",
        },
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    registry = dr.async_get(hass)
    # Register the other entry's device first so an unscoped lookup would find it.
    registry.async_get_or_create(
        config_entry_id=other.entry_id,
        identifiers={("mobile_app", "native-device")},
        name="Stale phone",
    )
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("mobile_app", "native-device")},
        name="Phone",
    )
    targets = {"mobile_app_phone": "private-registration-locator"}
    hass.data["mobile_app"] = {
        "config_entries": {"private-registration-locator": entry},
        "notify": SimpleNamespace(registered_targets=targets),
    }
    hass.services.async_register("notify", "mobile_app_phone", AsyncMock())

    (recipient,) = await async_resolve_question_recipients(hass, [entry.entry_id])

    assert recipient.device_id == device.id
    assert recipient.label == "Phone"
    assert "device_registry.async_get_device" not in caplog.text

    registry.async_remove_device(device.id)
    assert await async_resolve_question_recipients(hass, [entry.entry_id]) == ()
