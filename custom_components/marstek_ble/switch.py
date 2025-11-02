"""Switch platform for Marstek BLE integration."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CMD_AC_INPUT,
    CMD_ADAPTIVE_MODE,
    CMD_BUZZER,
    CMD_EPS_MODE,
    CMD_GENERATOR,
    CMD_OUTPUT_CONTROL,
    DOMAIN,
)
from .coordinator import MarstekDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Marstek BLE switches from a config entry."""
    coordinator: MarstekDataUpdateCoordinator = entry.runtime_data

    entities = [
        MarstekSwitch(
            coordinator,
            entry,
            "out1_control",
            "Output 1 Control",
            CMD_OUTPUT_CONTROL,
        ),
        MarstekSwitch(
            coordinator,
            entry,
            "eps_mode",
            "EPS Mode",
            CMD_EPS_MODE,
        ),
        MarstekSwitch(
            coordinator,
            entry,
            "adaptive_mode",
            "Adaptive Mode",
            CMD_ADAPTIVE_MODE,
        ),
        MarstekSwitch(
            coordinator,
            entry,
            "ac_input",
            "AC Input",
            CMD_AC_INPUT,
        ),
        MarstekSwitch(
            coordinator,
            entry,
            "generator",
            "Generator",
            CMD_GENERATOR,
        ),
        MarstekSwitch(
            coordinator,
            entry,
            "buzzer",
            "Buzzer",
            CMD_BUZZER,
        ),
    ]

    async_add_entities(entities)


class MarstekSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of a Marstek switch."""

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
        cmd: int,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._cmd = cmd
        self._attr_entity_category = "config"
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._is_on = False

    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""
        return self._is_on

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the switch on."""
        _LOGGER.debug("Turning on: %s (cmd 0x%02X)", self._attr_name, self._cmd)

        if not self.coordinator.client or not self.coordinator.client.is_connected:
            _LOGGER.warning("Cannot send command: device not connected")
            return

        await self.coordinator._write_command(self._cmd, b"\x01")
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the switch off."""
        _LOGGER.debug("Turning off: %s (cmd 0x%02X)", self._attr_name, self._cmd)

        if not self.coordinator.client or not self.coordinator.client.is_connected:
            _LOGGER.warning("Cannot send command: device not connected")
            return

        await self.coordinator._write_command(self._cmd, b"\x00")
        self._is_on = False
        self.async_write_ha_state()

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.ble_device.address)},
            "connections": {(CONNECTION_BLUETOOTH, self.coordinator.ble_device.address)},
        }
