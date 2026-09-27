"""Resolve Companion recipients from Core-owned registrations, never event claims."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
import re

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr

_SERVICE = re.compile(r"mobile_app_[a-z0-9_]{1,100}\Z")
_ENTRY_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


@dataclass(frozen=True, slots=True)
class QuestionNotificationRecipient:
    """Public binding only; no webhook ID, push credential or device-supplied identity."""

    registration_id: str
    device_id: str
    user_id: str
    service: str
    label: str
    inline_reply: bool


async def async_resolve_question_recipients(
    hass: HomeAssistant,
    registration_ids: list[str] | None = None,
) -> tuple[QuestionNotificationRecipient, ...]:
    """Fail closed if the registration, device, user or exact target is uncertain.

    Core's mobile_app notify owner maps each registered service to its webhook
    registration. Read that mapping locally; do not infer it from a slug or copy
    its private registration locator into options, receipts or notification data.
    """

    if registration_ids is not None and (
        not isinstance(registration_ids, list)
        or len(registration_ids) > 8
        or any(
            not isinstance(item, str) or not _ENTRY_ID.fullmatch(item)
            for item in registration_ids
        )
        or len(set(registration_ids)) != len(registration_ids)
    ):
        return ()
    mobile = hass.data.get("mobile_app")
    if not isinstance(mobile, Mapping):
        return ()
    entries = mobile.get("config_entries")
    notify = mobile.get("notify")
    targets = getattr(notify, "registered_targets", None)
    if (
        not isinstance(entries, Mapping)
        or len(entries) > 256
        or not isinstance(targets, Mapping)
    ):
        return ()
    registry = dr.async_get(hass)
    recipients: list[QuestionNotificationRecipient] = []
    for locator, entry in entries.items():
        registration_id = getattr(entry, "entry_id", None)
        data = getattr(entry, "data", None)
        if (
            not isinstance(registration_id, str)
            or not _ENTRY_ID.fullmatch(registration_id)
            or getattr(entry, "domain", None) != "mobile_app"
            or getattr(entry, "disabled_by", None) is not None
            or getattr(entry, "state", None) is not ConfigEntryState.LOADED
            or hass.config_entries.async_get_entry(registration_id) is not entry
            or not isinstance(data, Mapping)
            or data.get("webhook_id") != locator
        ):
            continue
        raw_device_id, user_id = data.get("device_id"), data.get("user_id")
        if (
            not isinstance(raw_device_id, str)
            or not raw_device_id
            or not isinstance(user_id, str)
        ):
            continue
        device = registry.async_get_device(identifiers={("mobile_app", raw_device_id)})
        if (
            device is None
            or device.disabled_by is not None
            or registration_id not in device.config_entries
        ):
            continue
        user = await hass.auth.async_get_user(user_id)
        if user is None or not user.is_active or not user.is_admin:
            continue
        services = [
            name
            for name, target in targets.items()
            if target == locator
            and isinstance(name, str)
            and _SERVICE.fullmatch(name)
            and hass.services.has_service("notify", name)
        ]
        if len(services) != 1:
            continue
        name = device.name_by_user or device.name or entry.title
        label = (
            name
            if isinstance(name, str) and 1 <= len(name) <= 128 and name.isprintable()
            else "Companion device"
        )
        recipients.append(
            QuestionNotificationRecipient(
                registration_id=registration_id,
                device_id=device.id,
                user_id=user_id,
                service=services[0],
                label=label,
                inline_reply=isinstance(data.get("os_name"), str)
                and data["os_name"] in {"Android", "iOS"},
            )
        )
    # Duplicate registry identifiers or notify routes cannot establish a unique
    # destination, including when only one of the duplicate entries is selected.
    device_counts = Counter(item.device_id for item in recipients)
    service_counts = Counter(item.service for item in recipients)
    return tuple(
        item
        for item in recipients
        if device_counts[item.device_id] == 1
        and service_counts[item.service] == 1
        and (registration_ids is None or item.registration_id in registration_ids)
    )
