"""Connection, authentication and running-task state for Home Assistant."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
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
            BridgeConnectionSensor(coordinator, runtime, "connection"),
            BridgeAuthenticatedSensor(coordinator, runtime, "authenticated"),
            BridgeTaskRunningSensor(coordinator, runtime, "task_running"),
        ]
    )


class BridgeConnectionSensor(BridgeEntity, BinarySensorEntity):
    _attr_name = "Connection"
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
    _attr_name = "ChatGPT connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return data.authenticated if data is not None else None


class BridgeTaskRunningSensor(BridgeEntity, BinarySensorEntity):
    _attr_name = "Task running"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return data.task_running if data is not None else None
