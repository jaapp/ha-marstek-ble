"""Button platform for Marstek BLE integration."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CMD_AC_POWER,
    CMD_POWER_MODE,
    CMD_REBOOT,
    CMD_TOTAL_POWER,
    DOMAIN,
)
from .coordinator import MarstekDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Marstek BLE buttons from a config entry."""
    coordinator: MarstekDataUpdateCoordinator = entry.runtime_data

    entities = [
        MarstekButton(
            coordinator,
            entry,
            "reboot",
            "Reboot",
            CMD_REBOOT,
            b"",
        ),
        MarstekButton(
            coordinator,
            entry,
            "set_800w_mode",
            "Set 800W Mode",
            CMD_POWER_MODE,
            b"\x20\x03",  # 800W
        ),
        MarstekButton(
            coordinator,
            entry,
            "set_2500w_mode",
            "Set 2500W Mode",
            CMD_POWER_MODE,
            b"\xC4\x09",  # 2500W
        ),
        MarstekButton(
            coordinator,
            entry,
            "set_ac_power_2500w",
            "Set AC Power 2500W",
            CMD_AC_POWER,
            b"\xC4\x09",  # 2500W
        ),
        MarstekButton(
            coordinator,
            entry,
            "set_total_power_2500w",
            "Set Total Power 2500W",
            CMD_TOTAL_POWER,
            b"\xC4\x09",  # 2500W
        ),
    ]

    async_add_entities(entities)


class MarstekButton(CoordinatorEntity, ButtonEntity):
    """Representation of a Marstek button."""

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
        cmd: int,
        payload: bytes,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_has_entity_name = True
        self._cmd = cmd
        self._payload = payload
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_unique_id = f"{entry.entry_id}_{key}"

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.debug("Button pressed: %s (cmd 0x%02X)", self._attr_name, self._cmd)
        await self.coordinator.device.send_command(self._cmd, self._payload)

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.ble_device.address)},
            "connections": {(CONNECTION_BLUETOOTH, self.coordinator.ble_device.address)},
            "name": self.coordinator.device_name,
            "manufacturer": "Marstek",
            "model": "Venus E",
        }
