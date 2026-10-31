"""Privacy-preserving Codex outcome and usage sensors."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .account_allowance_coordinator import MAX_USAGE_AGE, AccountAllowance, AccountAllowanceCoordinator, AccountAllowanceSnapshot
from .entity import BridgeEntity
from .const import DOMAIN
from .entity_coordinator import TERMINAL_OUTCOMES
from .runtime import CodexBridgeConfigEntry

# Two coordinators refresh every entity; these entities make no requests.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CodexBridgeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    coordinator = runtime.entity_coordinator
    if coordinator is None:
        return
    async_add_entities(
        [
            BridgeLastOutcomeSensor(coordinator, runtime, "last_outcome"),
            BridgeUsageSensor(coordinator, runtime, "five_hour_used"),
            BridgeUsageSensor(coordinator, runtime, "weekly_used"),
            BridgeResetSensor(coordinator, runtime, "five_hour_reset"),
            BridgeResetSensor(coordinator, runtime, "weekly_reset"),
        ]
    )
    allowance_coordinator = runtime.account_allowance_coordinator
    if allowance_coordinator is None:
        return
    manager = AccountAllowanceEntityManager(
        hass, entry, runtime, allowance_coordinator, async_add_entities
    )
    allowance_coordinator.manager = manager
    await manager.async_sync()
    manager.remove_listener = allowance_coordinator.async_add_listener(
        manager.coordinator_updated
    )


class BridgeLastOutcomeSensor(BridgeEntity, SensorEntity):
    """Report the latest terminal run outcome; unknown until one is observed."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(TERMINAL_OUTCOMES)

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        return data.last_outcome if data is not None else None


class _BridgeAllowanceSensor(BridgeEntity, SensorEntity):
    """Reversibly hide unused five-hour diagnostics without disabling them."""

    def __init__(self, coordinator, runtime, key: str) -> None:
        super().__init__(coordinator, runtime, key)
        self._key = key
        self._visibility_entry_id = runtime.entry_id
        self._visibility_changes: deque[er.RegistryEntryHider | None] = deque()
        if key in ("five_hour_used", "five_hour_reset"):
            data = coordinator.data
            self._attr_entity_registry_visible_default = (
                data is None or data.five_hour_enabled is not False
            )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._key not in ("five_hour_used", "five_hour_reset"):
            return
        self.async_on_remove(self.hass.bus.async_listen(
            er.EVENT_ENTITY_REGISTRY_UPDATED, self._visibility_registry_updated,
        ))
        self._reconcile_visibility()

    @callback
    def _visibility_registry_updated(self, event: Event) -> None:
        if (
            event.data.get("action") != "update"
            or event.data.get("entity_id") != self.entity_id
            or "hidden_by" not in event.data.get("changes", {})
        ):
            return
        old_hidden_by = event.data["changes"]["hidden_by"]
        if self._visibility_changes and old_hidden_by == self._visibility_changes[0]:
            self._visibility_changes.popleft()
            return
        # A registry change outside this owner is an explicit user choice.
        registry = er.async_get(self.hass)
        entry = registry.async_get(self.entity_id)
        if entry is not None:
            self._save_visibility_policy(registry, entry, "manual")

    @callback
    def _save_visibility_policy(self, registry, entry, policy: str) -> None:
        options = dict(entry.options.get(DOMAIN, {}))
        if options.get("five_hour_visibility") != policy:
            options["five_hour_visibility"] = policy
            registry.async_update_entity_options(self.entity_id, DOMAIN, options)

    @callback
    def _reconcile_visibility(self) -> None:
        if self._key not in ("five_hour_used", "five_hour_reset"):
            return
        registry = er.async_get(self.hass)
        entry = registry.async_get(self.entity_id)
        if (
            entry is None
            or entry.config_entry_id != self._visibility_entry_id
            or entry.disabled_by is not None
            or entry.hidden_by is er.RegistryEntryHider.USER
        ):
            return
        policy = entry.options.get(DOMAIN, {}).get("five_hour_visibility")
        if policy == "manual":
            return
        if policy == "automatic_hidden" and entry.hidden_by is None:
            # Also retain an unhide performed while the Integration was unloaded.
            self._save_visibility_policy(registry, entry, "manual")
            return
        data = self.coordinator.data
        enabled = data.five_hour_enabled if data is not None else None
        if enabled is None or not self.coordinator.last_update_success:
            return
        hidden_by = None if enabled else er.RegistryEntryHider.INTEGRATION
        if entry.hidden_by != hidden_by:
            self._visibility_changes.append(entry.hidden_by)
            entry = registry.async_update_entity(self.entity_id, hidden_by=hidden_by)
        self._save_visibility_policy(
            registry, entry, "automatic_visible" if enabled else "automatic_hidden",
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self._reconcile_visibility()
        super()._handle_coordinator_update()


class BridgeUsageSensor(_BridgeAllowanceSensor):
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        return getattr(data, self._key) if data is not None else None


class BridgeResetSensor(_BridgeAllowanceSensor):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> datetime | None:
        data = self.coordinator.data
        return getattr(data, self._key) if data is not None else None


# Registry keys and their entity translation keys. The keys stay stable because
# they form part of each entity's unique ID.
_ACCOUNT_SENSOR_KEYS = {
    "status": "allowance_status",
    "five_hour_used": "five_hour_used",
    "five_hour_remaining": "five_hour_remaining",
    "five_hour_reset": "five_hour_reset",
    "weekly_used": "weekly_used",
    "weekly_remaining": "weekly_remaining",
    "weekly_reset": "weekly_reset",
    "reset_credits": "reset_credits",
    "next_reset_expiry": "next_reset_expiry",
}
_ACCOUNT_FIVE_HOUR_KEYS = {"five_hour_used", "five_hour_remaining", "five_hour_reset"}
# The App's "unavailable" profile status means it has no usage for that account.
# It is a known status, not HA's unavailable state.
_ACCOUNT_STATUS_STATES = {
    "available": "available",
    "stale": "stale",
    "unavailable": "usage_unavailable",
    "reauthentication_required": "reauthentication_required",
}


def account_device_identifier(entry_id: str, profile_id: str) -> tuple[str, str]:
    return (DOMAIN, f"{entry_id}_profile_{profile_id}")


class AccountAllowanceSensor(CoordinatorEntity[AccountAllowanceCoordinator], SensorEntity):
    """One stable, privacy-safe sensor for a saved account profile."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, runtime, profile_id: str, key: str) -> None:
        super().__init__(coordinator)
        self._runtime = runtime
        self._profile_id = profile_id
        self._key = key
        self._attr_unique_id = f"{runtime.entry_id}_profile_allowance_{profile_id}_{key}"
        self._attr_translation_key = _ACCOUNT_SENSOR_KEYS[key]
        profile = self._profile
        # Each saved account is its own service device, named by its label.
        self._attr_device_info = DeviceInfo(
            identifiers={account_device_identifier(runtime.entry_id, profile_id)},
            name=profile.label if profile is not None else "Saved account",
            manufacturer="Codex Bridge",
            model="Saved ChatGPT account",
            entry_type=DeviceEntryType.SERVICE,
        )
        self._visibility_entry_id = runtime.entry_id
        self._visibility_changes: deque[er.RegistryEntryHider | None] = deque()
        if key == "status":
            self._attr_state_class = None
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(_ACCOUNT_STATUS_STATES.values())
        if key.endswith("_used") or key.endswith("_remaining"):
            self._attr_native_unit_of_measurement = PERCENTAGE
        if key.endswith("_reset") or key == "next_reset_expiry":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
            self._attr_state_class = None
        if key in _ACCOUNT_FIVE_HOUR_KEYS:
            self._attr_entity_registry_visible_default = not (
                profile is not None and profile.status == "available" and profile.five_hour_enabled is False
            )

    @property
    def _profile(self) -> AccountAllowance | None:
        data: AccountAllowanceSnapshot | None = self.coordinator.data
        return data.profiles.get(self._profile_id) if data is not None else None

    @property
    def telemetry_status(self) -> str | None:
        profile = self._profile
        if profile is None:
            return None
        if profile.reauthentication_required:
            return "reauthentication_required"
        if profile.status == "available" and (
            profile.updated_at is None
            or not 0 <= (datetime.now(UTC) - profile.updated_at).total_seconds() <= MAX_USAGE_AGE.total_seconds()
        ):
            return "stale"
        return _ACCOUNT_STATUS_STATES[profile.status]

    @property
    def available(self) -> bool:
        # The App answered. A stale, signed-out or unreported account has an
        # unknown value rather than an unavailable entity.
        return super().available and self._profile is not None

    def _value(self, profile: AccountAllowance) -> str | float | int | datetime | None:
        windows = profile.windows
        if self._key == "status":
            return self.telemetry_status
        if self.telemetry_status != "available":
            return None
        if self._key in _ACCOUNT_FIVE_HOUR_KEYS and profile.five_hour_enabled is False:
            return None
        if self._key == "five_hour_used":
            return windows.get("5 hours").used_percent if windows.get("5 hours") else None
        if self._key == "five_hour_remaining":
            return windows.get("5 hours").remaining_percent if windows.get("5 hours") else None
        if self._key == "five_hour_reset":
            return windows.get("5 hours").resets_at if windows.get("5 hours") else None
        if self._key == "weekly_used":
            return windows.get("Weekly").used_percent if windows.get("Weekly") else None
        if self._key == "weekly_remaining":
            return windows.get("Weekly").remaining_percent if windows.get("Weekly") else None
        if self._key == "weekly_reset":
            return windows.get("Weekly").resets_at if windows.get("Weekly") else None
        if self._key == "reset_credits":
            return profile.available_resets
        if self._key == "next_reset_expiry":
            return profile.next_reset_expiry
        return None

    @property
    def native_value(self):
        profile = self._profile
        return self._value(profile) if profile is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        profile = self._profile
        if profile is None:
            return None
        age = None
        if profile.updated_at is not None:
            age = max(0, int((datetime.now(UTC) - profile.updated_at).total_seconds()))
        attributes: dict[str, object] = {
            "account": profile.label,
            "active_account": profile.active,
            "telemetry_status": self.telemetry_status,
            "last_refresh": self.coordinator.data.refreshed_at.isoformat(),
            "reauthentication_required": profile.reauthentication_required,
            "last_updated": profile.updated_at.isoformat() if profile.updated_at else None,
            "data_age_seconds": age,
        }
        if self._key == "next_reset_expiry":
            attributes["expiry_complete"] = profile.expiry_complete
        if self._key in _ACCOUNT_FIVE_HOUR_KEYS:
            attributes["five_hour_enabled"] = profile.five_hour_enabled
        if self._key == "reset_credits":
            attributes["expiry_complete"] = profile.expiry_complete
            attributes["next_known_expiry"] = (
                profile.next_reset_expiry.isoformat() if profile.next_reset_expiry else None
            )
        return attributes

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._key not in _ACCOUNT_FIVE_HOUR_KEYS:
            return
        self.async_on_remove(self.hass.bus.async_listen(
            er.EVENT_ENTITY_REGISTRY_UPDATED, self._visibility_registry_updated,
        ))
        self._reconcile_visibility()

    @callback
    def _visibility_registry_updated(self, event: Event) -> None:
        if (
            event.data.get("action") != "update"
            or event.data.get("entity_id") != self.entity_id
            or "hidden_by" not in event.data.get("changes", {})
        ):
            return
        old_hidden_by = event.data["changes"]["hidden_by"]
        if self._visibility_changes and old_hidden_by == self._visibility_changes[0]:
            self._visibility_changes.popleft()
            return
        registry = er.async_get(self.hass)
        entry = registry.async_get(self.entity_id)
        if entry is not None:
            self._save_visibility_policy(registry, entry, "manual")

    @callback
    def _save_visibility_policy(self, registry, entry, policy: str) -> None:
        options = dict(entry.options.get(DOMAIN, {}))
        if options.get("five_hour_visibility") != policy:
            options["five_hour_visibility"] = policy
            registry.async_update_entity_options(self.entity_id, DOMAIN, options)

    @callback
    def _reconcile_visibility(self) -> None:
        if self._key not in _ACCOUNT_FIVE_HOUR_KEYS:
            return
        registry = er.async_get(self.hass)
        entry = registry.async_get(self.entity_id)
        if (
            entry is None
            or entry.config_entry_id != self._visibility_entry_id
            or entry.disabled_by is not None
            or entry.hidden_by is er.RegistryEntryHider.USER
        ):
            return
        policy = entry.options.get(DOMAIN, {}).get("five_hour_visibility")
        if policy == "manual":
            return
        if policy == "automatic_hidden" and entry.hidden_by is None:
            self._save_visibility_policy(registry, entry, "manual")
            return
        profile = self._profile
        if (
            profile is None
            or not self.coordinator.last_update_success
            or self.telemetry_status != "available"
            or profile.five_hour_enabled is None
        ):
            return
        hidden_by = None if profile.five_hour_enabled else er.RegistryEntryHider.INTEGRATION
        if entry.hidden_by != hidden_by:
            self._visibility_changes.append(entry.hidden_by)
            entry = registry.async_update_entity(self.entity_id, hidden_by=hidden_by)
        self._save_visibility_policy(
            registry, entry, "automatic_visible" if profile.five_hour_enabled else "automatic_hidden",
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self._reconcile_visibility()
        super()._handle_coordinator_update()


class AccountAllowanceEntityManager:
    """Dynamically add profiles and remove them only after a complete inventory."""

    def __init__(self, hass, entry, runtime, coordinator, async_add_entities) -> None:
        self.hass = hass
        self.entry = entry
        self.runtime = runtime
        self.coordinator = coordinator
        self.async_add_entities = async_add_entities
        self._entities: dict[str, list[AccountAllowanceSensor]] = {}
        self._sync_task = None
        self.remove_listener = None

    def coordinator_updated(self) -> None:
        if not self.coordinator.last_update_success:
            return
        if self._sync_task is None or self._sync_task.done():
            self._sync_task = self.hass.async_create_task(self.async_sync())

    async def async_sync(self) -> None:
        data: AccountAllowanceSnapshot | None = self.coordinator.data
        registry = er.async_get(self.hass)
        registered = self._registered_profile_ids(registry)
        complete = data is not None and data.inventory_complete and self.coordinator.last_update_success
        current = set(data.profiles) if complete else registered | set(self._entities)
        if complete:
            # Registry removal owns platform teardown, including disabled entities.
            # Do not add removed profiles on reload or remove unattached objects.
            prefix = f"{self.entry.entry_id}_profile_allowance_"
            expected = {
                f"{prefix}{profile_id}_{key}"
                for profile_id in current for key in _ACCOUNT_SENSOR_KEYS
            }
            for item in tuple(registry.entities.values()):
                if (
                    item.config_entry_id == self.entry.entry_id
                    and item.domain == "sensor"
                    and item.unique_id.startswith(prefix)
                    and item.unique_id not in expected
                ):
                    registry.async_remove(item.entity_id)
            for profile_id in set(self._entities) - current:
                self._entities.pop(profile_id)
            self._remove_stale_account_devices(current)
        if data is not None and self.coordinator.last_update_success:
            self._rename_account_devices(data)
        add: list[AccountAllowanceSensor] = []
        for profile_id in sorted(current - set(self._entities)):
            sensors = [
                AccountAllowanceSensor(self.coordinator, self.runtime, profile_id, key)
                for key in _ACCOUNT_SENSOR_KEYS
            ]
            self._entities[profile_id] = sensors
            add.extend(sensors)
        if add:
            self.async_add_entities(add)

    async def async_close(self) -> None:
        if self.remove_listener is not None:
            self.remove_listener()
            self.remove_listener = None
        task = self._sync_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @callback
    def _rename_account_devices(self, data: AccountAllowanceSnapshot) -> None:
        """Follow a label change; a name chosen in HA still takes precedence."""

        registry = dr.async_get(self.hass)
        for profile_id, profile in data.profiles.items():
            device = registry.async_get_device(
                identifiers={account_device_identifier(self.entry.entry_id, profile_id)}
            )
            if device is not None and device.name != profile.label:
                registry.async_update_device(device.id, name=profile.label)

    @callback
    def _remove_stale_account_devices(self, current: set[str]) -> None:
        """Detach accounts that a complete inventory no longer lists."""

        registry = dr.async_get(self.hass)
        expected = {
            account_device_identifier(self.entry.entry_id, profile_id)
            for profile_id in current
        }
        prefix = f"{self.entry.entry_id}_profile_"
        for device in dr.async_entries_for_config_entry(registry, self.entry.entry_id):
            accounts = {
                identifier for identifier in device.identifiers
                if identifier[0] == DOMAIN and identifier[1].startswith(prefix)
            }
            if accounts and not accounts & expected:
                registry.async_update_device(
                    device.id, remove_config_entry_id=self.entry.entry_id
                )

    def _registered_profile_ids(self, registry) -> set[str]:
        prefix = f"{self.entry.entry_id}_profile_allowance_"
        ids = set()
        for item in registry.entities.values():
            if item.config_entry_id != self.entry.entry_id or item.domain != "sensor":
                continue
            if not item.unique_id.startswith(prefix):
                continue
            for key in _ACCOUNT_SENSOR_KEYS:
                suffix = f"_{key}"
                identity = item.unique_id[len(prefix):]
                if identity.endswith(suffix) and identity[:-len(suffix)]:
                    ids.add(identity[:-len(suffix)])
                    break
        return ids
