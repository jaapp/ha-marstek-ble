"""Select platform for Marstek BLE integration."""
from __future__ import annotations

import logging
from typing import Any, Callable

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CMD_AUTO_MODE,
    CMD_CHARGE_MODE,
    CMD_CT_POLLING_RATE_WRITE,
    CMD_SYSTEM_DATA,
    CMD_WORK_MODE,
    DOMAIN,
)
from .coordinator import MarstekDataUpdateCoordinator
from .marstek_device import MarstekData

_LOGGER = logging.getLogger(__name__)

SelectValueFn = Callable[[MarstekData], Any | None]
MODE_STALE_AFTER_SECONDS = 10 * 60
OPERATING_MODE_OPTIONS = {0: "Self-Consumption", 1: "AI Optimization", 2: "Manual"}


def _reported_operating_mode(data: MarstekData | None) -> str | None:
    """Decode user mode from the validated 0x0D system-info payload.

    DeveloperModeInfo in rweijnen/marstek-venus-monitor decodes byte 17
    of its 19-byte payload as user mode (0=self-consumption, 1=AI, 2=manual).
    The existing parser retains this payload in system_status metadata.
    Runtime work_mode is a different enum; never infer user mode from it.
    """
    if data is None:
        return None
    meta = data.get_field_metadata("system_status")
    if not meta or meta.get("command") != CMD_SYSTEM_DATA:
        return None
    age = meta.get("age_seconds")
    if age is None or age < 0 or age > MODE_STALE_AFTER_SECONDS:
        return None
    payload_hex = meta.get("payload_hex")
    if not isinstance(payload_hex, str):
        return None
    try:
        payload = bytes.fromhex(payload_hex)
    except ValueError:
        return None
    if len(payload) < 19:
        return None
    return OPERATING_MODE_OPTIONS.get(payload[17])


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Marstek BLE selects from a config entry."""
    coordinator: MarstekDataUpdateCoordinator = entry.runtime_data

    entities = [
        MarstekOperatingModeSelect(coordinator, entry),
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


class MarstekOperatingModeSelect(
    CoordinatorEntity[MarstekDataUpdateCoordinator],
    SelectEntity,
):
    """Operating mode backed by device readback, not the last request.

    AI Optimization is reported when selected externally, but writing it
    over BLE remains unsupported, matching the upstream main branch.
    """

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the operating mode select."""
        super().__init__(coordinator)
        self._attr_name = "Operating Mode"
        self._attr_has_entity_name = True
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_unique_id = f"{entry.entry_id}_operating_mode"
        self._attr_options = list(OPERATING_MODE_OPTIONS.values())

    @property
    def current_option(self) -> str | None:
        """Return the latest reported mode, including external changes."""
        return _reported_operating_mode(self.coordinator.data)

    async def async_select_option(self, option: str) -> None:
        """Send a mode request and verify it using fresh system info."""
        mode_commands = {
            "Self-Consumption": (CMD_AUTO_MODE, b"\x01"),
            "Manual": (CMD_WORK_MODE, b"\x01"),
        }
        if option not in mode_commands:
            raise HomeAssistantError(
                f"Cannot select {option!r} over BLE; only Self-Consumption "
                "and Manual writes are supported. AI Optimization is readback-only."
            )

        cmd, payload = mode_commands[option]
        if not await self.coordinator.device.send_command(cmd, payload):
            raise HomeAssistantError(f"Failed to send operating-mode request for {option}")

        # Do not change the state optimistically or reuse a pre-write report.
        data = self.coordinator.data
        previous = data.get_field_metadata("system_status") if data is not None else None
        previous_timestamp = previous.get("timestamp") if previous else None
        readback_ok = await self.coordinator.device.send_command(CMD_SYSTEM_DATA)
        data = self.coordinator.data
        report = data.get_field_metadata("system_status") if data is not None else None
        self.async_write_ha_state()
        if (
            not readback_ok
            or not report
            or not report.get("timestamp")
            or report["timestamp"] == previous_timestamp
        ):
            raise HomeAssistantError(
                f"Mode request for {option} was sent, but fresh readback was not received; "
                "the battery may have changed mode."
            )
        actual = self.current_option
        if actual != option:
            raise HomeAssistantError(
                f"Mode request for {option} was sent, but readback reports {actual!r}; "
                "the requested mode is not confirmed."
            )
        _LOGGER.info("Device readback confirmed operating mode %s", actual)

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
