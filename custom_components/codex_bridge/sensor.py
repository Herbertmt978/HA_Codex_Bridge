"""Privacy-preserving Codex outcome and usage sensors."""

from __future__ import annotations

from collections import deque
from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import BridgeEntity
from .const import DOMAIN
from .runtime import async_get_runtime


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime = async_get_runtime(hass)
    coordinator = runtime.entity_coordinator
    if coordinator is None:
        return
    async_add_entities(
        [
            BridgeLastOutcomeSensor(coordinator, runtime, "last_outcome"),
            BridgeUsageSensor(coordinator, runtime, "five_hour_used", "5-hour usage"),
            BridgeUsageSensor(coordinator, runtime, "weekly_used", "Weekly usage"),
            BridgeResetSensor(coordinator, runtime, "five_hour_reset", "5-hour reset"),
            BridgeResetSensor(coordinator, runtime, "weekly_reset", "Weekly reset"),
        ]
    )


class BridgeLastOutcomeSensor(BridgeEntity, SensorEntity):
    _attr_name = "Last task outcome"

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.last_outcome is not None

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        return data.last_outcome if data is not None else None


class _BridgeAllowanceSensor(BridgeEntity, SensorEntity):
    """Reversibly hide unused five-hour diagnostics without disabling them."""

    def __init__(self, coordinator, runtime, key: str, name: str) -> None:
        super().__init__(coordinator, runtime, key)
        self._key = key
        self._attr_name = name
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
    def available(self) -> bool:
        data = self.coordinator.data
        return super().available and data is not None and getattr(data, self._key) is not None

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        return getattr(data, self._key) if data is not None else None


class BridgeResetSensor(_BridgeAllowanceSensor):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return super().available and data is not None and getattr(data, self._key) is not None

    @property
    def native_value(self) -> datetime | None:
        data = self.coordinator.data
        return getattr(data, self._key) if data is not None else None
