"""Connection, authentication and running-task state for Home Assistant."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import BridgeEntity
from .runtime import CodexBridgeConfigEntry

if TYPE_CHECKING:
    from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
else:
    # Every supported release re-exports it here; HA 2026.10 moved its definition.
    from homeassistant.components.binary_sensor import BinarySensorDeviceClass

# One coordinator refreshes every entity; these entities make no requests.
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
            BridgeConnectionSensor(coordinator, runtime, "connection"),
            BridgeAuthenticatedSensor(coordinator, runtime, "authenticated"),
            BridgeTaskRunningSensor(coordinator, runtime, "task_running"),
        ]
    )


class BridgeConnectionSensor(BridgeEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        # The connection entity must report off when the App cannot be reached.
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success and self.coordinator.data is not None


class BridgeAuthenticatedSensor(BridgeEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return data.authenticated if data is not None else None


class BridgeTaskRunningSensor(BridgeEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return data.task_running if data is not None else None
