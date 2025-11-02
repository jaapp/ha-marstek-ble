"""Select platform for Marstek BLE integration."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CMD_CHARGE_MODE, CMD_CT_POLLING_RATE_WRITE, DOMAIN
from .coordinator import MarstekDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Marstek BLE selects from a config entry."""
    coordinator: MarstekDataUpdateCoordinator = entry.runtime_data

    entities = [
        MarstekSelect(
            coordinator,
            entry,
            "charge_mode",
            "Charge Mode",
            CMD_CHARGE_MODE,
            {
                "Load First": b"\x01",
                "PV2 Passthrough": b"\x00",
                "Simultaneous Charge Discharge": b"\x02",
            },
        ),
        MarstekSelect(
            coordinator,
            entry,
            "ct_polling_rate",
            "CT Polling Rate",
            CMD_CT_POLLING_RATE_WRITE,
            {
                "Fastest (0)": b"\x00",
                "Medium (1)": b"\x01",
                "Slowest (2)": b"\x02",
            },
        ),
    ]

    async_add_entities(entities)


class MarstekSelect(CoordinatorEntity, SelectEntity):
    """Representation of a Marstek select."""

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
        cmd: int,
        options_map: dict[str, bytes],
    ) -> None:
        """Initialize the select."""
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._cmd = cmd
        self._options_map = options_map
        self._attr_options = list(options_map.keys())
        self._attr_entity_category = "config"
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_current_option = self._attr_options[0]

    @property
    def current_option(self) -> str | None:
        """Return the selected option."""
        return self._attr_current_option

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        _LOGGER.debug("Selecting option: %s = %s (cmd 0x%02X)", self._attr_name, option, self._cmd)

        if not self.coordinator.client or not self.coordinator.client.is_connected:
            _LOGGER.warning("Cannot send command: device not connected")
            return

        payload = self._options_map[option]
        await self.coordinator._write_command(self._cmd, payload)
        self._attr_current_option = option
        self.async_write_ha_state()

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self.coordinator._device.address)},
            "connections": {(CONNECTION_BLUETOOTH, self.coordinator._device.address)},
        }
