"""Privacy-preserving Codex outcome and usage sensors."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import BridgeEntity
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


class BridgeUsageSensor(BridgeEntity, SensorEntity):
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, runtime, key: str, name: str) -> None:
        super().__init__(coordinator, runtime, key)
        self._key = key
        self._attr_name = name

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return super().available and data is not None and getattr(data, self._key) is not None

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        return getattr(data, self._key) if data is not None else None


class BridgeResetSensor(BridgeEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, runtime, key: str, name: str) -> None:
        super().__init__(coordinator, runtime, key)
        self._key = key
        self._attr_name = name

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return super().available and data is not None and getattr(data, self._key) is not None

    @property
    def native_value(self) -> datetime | None:
        data = self.coordinator.data
        return getattr(data, self._key) if data is not None else None
