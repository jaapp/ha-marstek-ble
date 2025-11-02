"""Data coordinator for Marstek BLE integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothProcessorCoordinator,
)
from homeassistant.core import HomeAssistant

from .const import (
    CHAR_NOTIFY_UUID,
    CHAR_WRITE_UUID,
    CMD_BMS_DATA,
    CMD_CONFIG_DATA,
    CMD_CT_POLLING_RATE,
    CMD_DEVICE_INFO,
    CMD_LOCAL_API_STATUS,
    CMD_LOGS,
    CMD_METER_IP,
    CMD_NETWORK_INFO,
    CMD_RUNTIME_INFO,
    CMD_SYSTEM_DATA,
    CMD_TIMER_INFO,
    CMD_WIFI_SSID,
    SERVICE_UUID,
    UPDATE_INTERVAL_FAST,
)
from .marstek_device import MarstekData, MarstekProtocol

_LOGGER = logging.getLogger(__name__)


class MarstekDataUpdateCoordinator(ActiveBluetoothProcessorCoordinator[MarstekData]):
    """Class to manage fetching Marstek data from BLE device."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        address: str,
        device: BLEDevice,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass=hass,
            logger=logger,
            address=address,
            mode=bluetooth.BluetoothScanningMode.ACTIVE,
            connectable=True,
        )
        self._device = device
        self._protocol = MarstekProtocol()
        self.data = MarstekData()
        self._connected = False
        self._fast_poll_count = 0
        self._medium_poll_count = 0
        self._slow_poll_count = 0

    async def _async_update(self, data: MarstekData) -> MarstekData:
        """Poll the device for data."""
        _LOGGER.debug("Updating Marstek data")

        # Fast poll (every update - 10s)
        await self._poll_fast()
        self._fast_poll_count += 1

        # Medium poll (every 6th update - 60s)
        if self._fast_poll_count % 6 == 0:
            await self._poll_medium()
            self._medium_poll_count += 1

        # Slow poll (every 30th update - 5 min)
        if self._fast_poll_count % 30 == 0:
            await self._poll_slow()
            self._slow_poll_count += 1

        return self.data

    async def _poll_fast(self) -> None:
        """Poll fast-update data (runtime info, BMS)."""
        try:
            # Runtime info
            await self._write_command(CMD_RUNTIME_INFO)
            await asyncio.sleep(0.3)

            # BMS data
            await self._write_command(CMD_BMS_DATA)
            await asyncio.sleep(0.3)

        except Exception as e:
            _LOGGER.warning("Error polling fast data: %s", e)

    async def _poll_medium(self) -> None:
        """Poll medium-update data (system, WiFi, config, etc)."""
        try:
            # System data
            await self._write_command(CMD_SYSTEM_DATA)
            await asyncio.sleep(0.3)

            # WiFi SSID
            await self._write_command(CMD_WIFI_SSID)
            await asyncio.sleep(0.3)

            # Config data
            await self._write_command(CMD_CONFIG_DATA)
            await asyncio.sleep(0.3)

            # CT polling rate
            await self._write_command(CMD_CT_POLLING_RATE)
            await asyncio.sleep(0.3)

            # Local API status
            await self._write_command(CMD_LOCAL_API_STATUS)
            await asyncio.sleep(0.3)

            # Meter IP
            await self._write_command(CMD_METER_IP, b"\x0B")
            await asyncio.sleep(0.3)

            # Network info
            await self._write_command(CMD_NETWORK_INFO)
            await asyncio.sleep(0.3)

        except Exception as e:
            _LOGGER.warning("Error polling medium data: %s", e)

    async def _poll_slow(self) -> None:
        """Poll slow-update data (timer info, logs)."""
        try:
            # Timer info
            await self._write_command(CMD_TIMER_INFO)
            await asyncio.sleep(0.3)

            # Logs
            await self._write_command(CMD_LOGS)
            await asyncio.sleep(0.3)

        except Exception as e:
            _LOGGER.warning("Error polling slow data: %s", e)

    async def _write_command(self, cmd: int, payload: bytes = b"") -> None:
        """Write a command to the device."""
        if not self.client or not self.client.is_connected:
            _LOGGER.warning("Cannot write command: not connected")
            return

        command_data = self._protocol.build_command(cmd, payload)
        _LOGGER.debug("Writing command 0x%02X: %s", cmd, command_data.hex())

        try:
            await self.client.write_gatt_char(CHAR_WRITE_UUID, command_data, response=True)
        except BleakError as e:
            _LOGGER.warning("Error writing command 0x%02X: %s", cmd, e)
            raise

    def _handle_notification(self, sender: int, data: bytearray) -> None:
        """Handle notification from device."""
        _LOGGER.debug("Received notification: %s", bytes(data).hex())
        self._protocol.parse_notification(bytes(data), self.data)
        self.async_set_updated_data(self.data)

    async def async_start_notify(self) -> None:
        """Start notifications."""
        if not self.client or not self.client.is_connected:
            _LOGGER.warning("Cannot start notifications: not connected")
            return

        try:
            await self.client.start_notify(CHAR_NOTIFY_UUID, self._handle_notification)
            _LOGGER.debug("Started notifications")

            # Initial data fetch
            await asyncio.sleep(0.3)
            await self._write_command(CMD_DEVICE_INFO)
            await asyncio.sleep(0.3)
            await self._write_command(CMD_RUNTIME_INFO)
            await asyncio.sleep(0.3)
            await self._write_command(CMD_BMS_DATA)

        except Exception as e:
            _LOGGER.exception("Error starting notifications: %s", e)

    async def async_stop_notify(self) -> None:
        """Stop notifications."""
        if not self.client or not self.client.is_connected:
            return

        try:
            await self.client.stop_notify(CHAR_NOTIFY_UUID)
            _LOGGER.debug("Stopped notifications")
        except Exception as e:
            _LOGGER.warning("Error stopping notifications: %s", e)
