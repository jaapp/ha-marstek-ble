"""Local API UDP port configuration for the Marstek BLE integration."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MarstekDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Register the Local API command port number entity."""
    async_add_entities([MarstekLocalApiPort(entry.runtime_data, entry)])


class MarstekLocalApiPort(CoordinatorEntity[MarstekDataUpdateCoordinator], NumberEntity):
    """Configured command port; never claim it is readback telemetry."""

    _attr_name = "Local API port"
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:ip-network"
    _attr_native_min_value = 1
    _attr_native_max_value = 65535
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_assumed_state = True
    _attr_extra_state_attributes = {
        "port_source": "Configured/assumed command port; not verified telemetry"
    }

    def __init__(self, coordinator: MarstekDataUpdateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_local_api_port"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.ble_device.address)},
            "connections": {(CONNECTION_BLUETOOTH, coordinator.ble_device.address)},
        }

    @property
    def native_value(self) -> int:
        """Return the configured command port."""
        return self.coordinator.local_api_port

    async def async_set_native_value(self, value: float) -> None:
        """Update the port without guessing or altering the enabled setting."""
        if (
            type(value) not in (int, float)
            or not 1 <= value <= 65535
            or int(value) != value
        ):
            raise HomeAssistantError("Local API port must be an integer from 1 to 65535")
        await self.coordinator.async_set_local_api(port=int(value))
