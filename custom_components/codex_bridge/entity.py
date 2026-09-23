"""Shared registry identity for Codex Bridge status entities."""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .entity_coordinator import BridgeEntityCoordinator
from .runtime import CodexBridgeRuntime


class BridgeEntity(CoordinatorEntity[BridgeEntityCoordinator]):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: BridgeEntityCoordinator, runtime: CodexBridgeRuntime, key: str
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{runtime.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.discovery_uuid or runtime.entry_id)},
            name=runtime.title,
            manufacturer="Codex Bridge",
        )
