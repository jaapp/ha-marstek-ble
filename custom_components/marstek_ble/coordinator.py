"""Data coordinator for Marstek BLE integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

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
from .marstek_device import MarstekBLEDevice, MarstekData, MarstekProtocol

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

        # Create persistent device object for command sending (SwitchBot pattern)
        self.device = MarstekBLEDevice(
            ble_device=device,
            device_name=device_name,
            ble_device_callback=lambda: bluetooth.async_ble_device_from_address(
                self.hass, address, connectable=True
            ),
            notification_callback=self._handle_notification,
        )

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

        # Use persistent device object for polling (SwitchBot pattern)
        # The device manages its own connection lifecycle

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
            await self.device.send_command(CMD_RUNTIME_INFO)
            await asyncio.sleep(0.3)

            # BMS data
            await self.device.send_command(CMD_BMS_DATA)
            await asyncio.sleep(0.3)

        except Exception as e:
            _LOGGER.warning("Error polling fast data: %s", e)

    async def _poll_medium(self) -> None:
        """Poll medium-update data (system, WiFi, config, etc)."""
        try:
            # System data
            await self.device.send_command(CMD_SYSTEM_DATA)
            await asyncio.sleep(0.3)

            # WiFi SSID
            await self.device.send_command(CMD_WIFI_SSID)
            await asyncio.sleep(0.3)

            # Config data
            await self.device.send_command(CMD_CONFIG_DATA)
            await asyncio.sleep(0.3)

            # CT polling rate
            await self.device.send_command(CMD_CT_POLLING_RATE)
            await asyncio.sleep(0.3)

            # Local API status
            await self.device.send_command(CMD_LOCAL_API_STATUS)
            await asyncio.sleep(0.3)

            # Meter IP
            await self.device.send_command(CMD_METER_IP, b"\x0B")
            await asyncio.sleep(0.3)

            # Network info
            await self.device.send_command(CMD_NETWORK_INFO)
            await asyncio.sleep(0.3)

        except Exception as e:
            _LOGGER.warning("Error polling medium data: %s", e)

    async def _poll_slow(self) -> None:
        """Poll slow-update data (timer info, logs)."""
        try:
            # Timer info
            await self.device.send_command(CMD_TIMER_INFO)
            await asyncio.sleep(0.3)

            # Logs
            await self.device.send_command(CMD_LOGS)
            await asyncio.sleep(0.3)

        except Exception as e:
            _LOGGER.warning("Error polling slow data: %s", e)

    def _handle_notification(self, sender: int, data: bytearray) -> None:
        """Handle notification from device."""
        _LOGGER.debug("Received notification: %s", bytes(data).hex())
        self._protocol.parse_notification(bytes(data), self.data)
        self.async_set_updated_data(self.data)

    @property
    def last_update_success(self) -> bool:
        """Return if last update was successful.

        Maps last_poll_successful from ActiveBluetoothDataUpdateCoordinator
        to last_update_success expected by CoordinatorEntity.
        """
        return self.last_poll_successful

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
