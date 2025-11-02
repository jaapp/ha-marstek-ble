"""Data coordinator for Marstek BLE integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.core import CoreState, HomeAssistant, callback

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


class MarstekDataUpdateCoordinator(ActiveBluetoothDataUpdateCoordinator[None]):
    """Class to manage fetching Marstek data from BLE device."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        address: str,
        device: BLEDevice,
        device_name: str,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass=hass,
            logger=logger,
            address=address,
            mode=bluetooth.BluetoothScanningMode.ACTIVE,
            needs_poll_method=self._needs_poll,
            poll_method=self._async_update,
            connectable=True,
        )
        self.update_interval = timedelta(seconds=UPDATE_INTERVAL_FAST)
        self.ble_device = device
        self.device_name = device_name
        self._protocol = MarstekProtocol
        self.data = MarstekData()
        self._connected = False
        self._fast_poll_count = 0
        self._medium_poll_count = 0
        self._slow_poll_count = 0
        self._ready_event = asyncio.Event()
        self._was_unavailable = True

    @callback
    def _needs_poll(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        seconds_since_last_poll: float | None,
    ) -> bool:
        """Determine if polling is needed."""
        # Only poll if hass is running and we have a connectable device
        return (
            self.hass.state is CoreState.running
            and bool(
                bluetooth.async_ble_device_from_address(
                    self.hass, service_info.device.address, connectable=True
                )
            )
        )

    async def _async_update(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        """Poll the device for data."""
        _LOGGER.debug("Updating Marstek data")

        # Update BLE device reference
        self.ble_device = service_info.device

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

    @callback
    def _async_handle_unavailable(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        """Handle the device going unavailable."""
        super()._async_handle_unavailable(service_info)
        self._was_unavailable = True
        _LOGGER.info("Device %s is unavailable", self.device_name)

    @callback
    def _async_handle_bluetooth_event(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Handle a Bluetooth event."""
        self.ble_device = service_info.device

        # Mark device as ready when we receive advertisements
        if not self._ready_event.is_set():
            self._ready_event.set()

        if self._was_unavailable:
            self._was_unavailable = False
            _LOGGER.info("Device %s is online", self.device_name)

        super()._async_handle_bluetooth_event(service_info, change)

    async def async_wait_ready(self) -> bool:
        """Wait for the device to be ready."""
        import contextlib
        try:
            async with asyncio.timeout(30):
                await self._ready_event.wait()
                return True
        except TimeoutError:
            return False
