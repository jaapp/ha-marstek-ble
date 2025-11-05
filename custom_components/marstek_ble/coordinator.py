"""Data coordinator for Marstek BLE integration."""
from __future__ import annotations

import asyncio
import logging
import math
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
    DEFAULT_POLL_INTERVAL,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
    SERVICE_UUID,
    UPDATE_INTERVAL_MEDIUM,
    UPDATE_INTERVAL_SLOW,
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
        poll_interval: int = DEFAULT_POLL_INTERVAL,
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
        self._poll_interval = self._sanitize_poll_interval(poll_interval)
        self._medium_poll_cycle = 1
        self._slow_poll_cycle = 1
        self._update_poll_schedule()

        # Create persistent device object for command sending (SwitchBot pattern)
        self.device = MarstekBLEDevice(
            ble_device=device,
            device_name=device_name,
            ble_device_callback=lambda: bluetooth.async_ble_device_from_address(
                self.hass, address, connectable=True
            ),
            notification_callback=self._handle_notification,
        )

    async def _send_and_sleep(
        self, command: int, payload: bytes = b"", delay: float = 0.3
    ) -> None:
        """Send a command and optionally wait for a response window."""
        if not await self.device.send_command(command, payload):
            raise BleakError(f"Failed to send command 0x{command:02X}")
        if delay:
            await asyncio.sleep(delay)

    def _sanitize_poll_interval(self, poll_interval: int) -> int:
        """Clamp the polling interval to supported bounds."""
        if poll_interval < MIN_POLL_INTERVAL:
            _LOGGER.debug(
                "Requested poll interval %s below minimum; clamping to %s",
                poll_interval,
                MIN_POLL_INTERVAL,
            )
            return MIN_POLL_INTERVAL
        if poll_interval > MAX_POLL_INTERVAL:
            _LOGGER.debug(
                "Requested poll interval %s above maximum; clamping to %s",
                poll_interval,
                MAX_POLL_INTERVAL,
            )
            return MAX_POLL_INTERVAL
        return poll_interval

    def _update_poll_schedule(self) -> None:
        """Recalculate polling schedule derived from the fast interval."""
        self.update_interval = timedelta(seconds=self._poll_interval)
        self._medium_poll_cycle = max(
            1, math.ceil(UPDATE_INTERVAL_MEDIUM / self._poll_interval)
        )
        self._slow_poll_cycle = max(
            1, math.ceil(UPDATE_INTERVAL_SLOW / self._poll_interval)
        )
        _LOGGER.debug(
            "Polling schedule updated: fast=%ss, medium every %s updates, slow every %s updates",
            self._poll_interval,
            self._medium_poll_cycle,
            self._slow_poll_cycle,
        )

    def set_poll_interval(self, poll_interval: int) -> None:
        """Update the polling interval."""
        sanitized = self._sanitize_poll_interval(poll_interval)
        if sanitized == self._poll_interval:
            return

        _LOGGER.info(
            "Updating polling interval for %s from %ss to %ss",
            self.device_name,
            self._poll_interval,
            sanitized,
        )
        self._poll_interval = sanitized
        self._fast_poll_count = 0
        self._medium_poll_count = 0
        self._slow_poll_count = 0
        self._update_poll_schedule()

    @callback
    def _needs_poll(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        seconds_since_last_poll: float | None,
    ) -> bool:
        """Determine if polling is needed."""
        # Only poll if we have a connectable device
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, service_info.device.address, connectable=True
        )
        needs_poll = bool(ble_device)

        _LOGGER.debug(
            "_needs_poll called: ble_device=%s, seconds_since_last_poll=%s, needs_poll=%s",
            ble_device is not None, seconds_since_last_poll, needs_poll
        )

        return needs_poll

    async def _async_update(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> MarstekData:
        """Poll the device for data."""
        _LOGGER.debug(
            "_async_update called - Updating Marstek data from %s",
            service_info.device.address,
        )

        # Update BLE device reference
        self.ble_device = service_info.device

        # Use persistent device object for polling (SwitchBot pattern)
        # The device manages its own connection lifecycle

        # Fast poll (runs at the configured base interval)
        await self._poll_fast()
        self._fast_poll_count += 1

        # Medium poll (~every UPDATE_INTERVAL_MEDIUM seconds)
        if self._fast_poll_count % self._medium_poll_cycle == 0:
            await self._poll_medium()
            self._medium_poll_count += 1

        # Slow poll (~every UPDATE_INTERVAL_SLOW seconds)
        if self._fast_poll_count % self._slow_poll_cycle == 0:
            await self._poll_slow()
            self._slow_poll_count += 1

        # Return the current data snapshot so ActiveBluetoothDataUpdateCoordinator
        # retains the populated MarstekData instance.
        return self.data

    async def _poll_fast(self) -> None:
        """Poll fast-update data (runtime info, BMS)."""
        _LOGGER.debug(
            "Polling fast data - coordinator.data before: battery_voltage=%s, battery_soc=%s",
            self.data.battery_voltage,
            self.data.battery_soc,
        )

        # Runtime info
        await self._send_and_sleep(CMD_RUNTIME_INFO)

        # BMS data
        await self._send_and_sleep(CMD_BMS_DATA)

        _LOGGER.debug(
            "Polling fast data - coordinator.data after: battery_voltage=%s, battery_soc=%s",
            self.data.battery_voltage,
            self.data.battery_soc,
        )

    async def _poll_medium(self) -> None:
        """Poll medium-update data (system, WiFi, config, etc)."""
        # System data
        await self._send_and_sleep(CMD_SYSTEM_DATA)

        # WiFi SSID
        await self._send_and_sleep(CMD_WIFI_SSID)

        # Config data
        await self._send_and_sleep(CMD_CONFIG_DATA)

        # CT polling rate
        await self._send_and_sleep(CMD_CT_POLLING_RATE)

        # Local API status
        await self._send_and_sleep(CMD_LOCAL_API_STATUS)

        # Meter IP
        await self._send_and_sleep(CMD_METER_IP, b"\x0B")

        # Network info
        await self._send_and_sleep(CMD_NETWORK_INFO)

    async def _poll_slow(self) -> None:
        """Poll slow-update data (timer info, logs)."""
        # Timer info
        await self._send_and_sleep(CMD_TIMER_INFO)

        # Logs
        await self._send_and_sleep(CMD_LOGS)

    def _handle_notification(self, sender: int, data: bytearray) -> None:
        """Handle notification from device."""
        raw_data = bytes(data)
        _LOGGER.debug("Received notification from sender %s: %s", sender, raw_data.hex())
        _LOGGER.debug(
            "Data before parsing: battery_voltage=%s, battery_soc=%s",
            self.data.battery_voltage,
            self.data.battery_soc,
        )

        result = self._protocol.parse_notification(raw_data, self.data)
        self.device.record_notification(sender, raw_data, result)

        _LOGGER.debug("Parse result: %s, data after parsing: battery_voltage=%s, battery_soc=%s",
                     result, self.data.battery_voltage, self.data.battery_soc)

        if result:
            # Entities listen for coordinator updates; notify only when parsing succeeded.
            self.async_update_listeners()

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
        _LOGGER.debug("_async_handle_bluetooth_event called: device=%s, change=%s",
                     service_info.device.address, change)

        self.ble_device = service_info.device

        # Mark device as ready when we receive advertisements
        if not self._ready_event.is_set():
            self._ready_event.set()
            _LOGGER.info("Device %s marked as ready", self.device_name)

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
