"""Select platform for Marstek BLE integration."""
from __future__ import annotations

import logging
from typing import Any, Callable

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CMD_CHARGE_MODE, CMD_CT_POLLING_RATE_WRITE, DOMAIN
from .coordinator import MarstekDataUpdateCoordinator
from .marstek_device import MarstekData

_LOGGER = logging.getLogger(__name__)

SelectValueFn = Callable[[MarstekData], Any | None]


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
                "Load First": (b"\x01", 1),
                "PV2 Passthrough": (b"\x00", 0),
                "Simultaneous Charge Discharge": (b"\x02", 2),
            },
            current_value_fn=lambda data: data.config_mode,
        ),
        MarstekSelect(
            coordinator,
            entry,
            "ct_polling_rate",
            "CT Polling Rate",
            CMD_CT_POLLING_RATE_WRITE,
            {
                "Fastest (0)": (b"\x00", 0),
                "Medium (1)": (b"\x01", 1),
                "Slowest (2)": (b"\x02", 2),
            },
            current_value_fn=lambda data: data.ct_polling_rate,
        ),
    ]

    async_add_entities(entities)


class MarstekSelect(
    CoordinatorEntity[MarstekDataUpdateCoordinator],
    SelectEntity,
):
    """Representation of a Marstek select."""

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
        cmd: int,
        options_map: dict[str, tuple[bytes, Any]],
        current_value_fn: SelectValueFn | None,
    ) -> None:
        """Initialize the select."""
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_has_entity_name = True
        self._cmd = cmd
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_options = list(options_map.keys())
        self._attr_current_option: str | None = None
        self._option_payloads = {
            option: payload for option, (payload, _) in options_map.items()
        }
        self._value_to_option = {
            value: option for option, (_, value) in options_map.items() if value is not None
        }
        self._current_value_fn = current_value_fn

        self._sync_from_coordinator()

    @property
    def current_option(self) -> str | None:
        """Return the selected option."""
        return self._attr_current_option

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        _LOGGER.debug(
            "Selecting option: %s = %s (cmd 0x%02X)",
            self._attr_name,
            option,
            self._cmd,
        )
        payload = self._option_payloads[option]
        if await self.coordinator.device.send_command(self._cmd, payload):
            self._attr_current_option = option
            self.async_write_ha_state()

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

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._sync_from_coordinator()
        super()._handle_coordinator_update()

    def _sync_from_coordinator(self) -> None:
        """Set current option from coordinator data if available."""
        if not self._current_value_fn:
            return
        value = self._current_value_fn(self.coordinator.data)
        if value is None:
            return
        if (option := self._value_to_option.get(value)) is not None:
            self._attr_current_option = option
