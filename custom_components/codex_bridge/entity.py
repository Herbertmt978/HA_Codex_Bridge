"""Shared registry identity for Codex Bridge status entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONNECTION_TYPE_SUPERVISOR, DOMAIN
from .entity_coordinator import BridgeEntityCoordinator
from .runtime import CodexBridgeConfigEntry, CodexBridgeRuntime


def bridge_device_identifier(runtime: CodexBridgeRuntime) -> tuple[str, str]:
    return (DOMAIN, runtime.discovery_uuid or runtime.entry_id)


def bridge_device_info(
    entry: CodexBridgeConfigEntry,
    runtime: CodexBridgeRuntime,
    *,
    sw_version: str | None,
) -> DeviceInfo:
    """Describe the connected App or external Bridge as one service device."""

    return DeviceInfo(
        identifiers={bridge_device_identifier(runtime)},
        name=entry.title,
        manufacturer="Codex Bridge",
        model=(
            "Home Assistant App"
            if runtime.connection_type == CONNECTION_TYPE_SUPERVISOR
            else "External Bridge"
        ),
        sw_version=sw_version,
        entry_type=DeviceEntryType.SERVICE,
    )


class BridgeEntity(CoordinatorEntity[BridgeEntityCoordinator]):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: BridgeEntityCoordinator, runtime: CodexBridgeRuntime, key: str
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{runtime.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = runtime.device_info
