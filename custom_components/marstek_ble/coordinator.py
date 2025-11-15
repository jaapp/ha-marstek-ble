"""Data coordinator for Marstek BLE integration."""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import timedelta
import time
from types import SimpleNamespace

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

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
    CMD_DEVICE_INFO,
    CMD_LOCAL_API_STATUS,
    DEFAULT_POLL_INTERVAL,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
    SERVICE_UUID,
    UPDATE_INTERVAL_MEDIUM,
    UPDATE_INTERVAL_SLOW,
)
from .marstek_device import MarstekBLEDevice, MarstekData, MarstekProtocol

_LOGGER = logging.getLogger(__name__)
VERBOSE_LOGGER = logging.getLogger(f"{__name__}.verbose")
VERBOSE_LOGGER.propagate = False
VERBOSE_LOGGER.setLevel(logging.INFO)


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
        self._last_poll_started_at: float | None = None
        self._last_poll_completed_at: float | None = None
        self._current_poll_commands: list[dict[str, object]] = []
        self._last_service_info: bluetooth.BluetoothServiceInfoBleak | None = None
        self._time_poll_unsub: asyncio.TimerHandle | None = None
        self._poll_lock = asyncio.Lock()
        self._initial_poll_done = False
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
        start = time.monotonic()
        wall_time = time.time()
        error: Exception | None = None
        success = False
        try:
            if not await self.device.send_command(command, payload):
                raise BleakError(f"Failed to send command 0x{command:02X}")
            success = True
            if delay:
                await asyncio.sleep(delay)
        except Exception as err:  # noqa: BLE001
            error = err
            raise
        finally:
            duration = time.monotonic() - start
            cmd_entry = {
                "cmd": command,
                "payload": payload.hex(),
                "success": success,
                "duration": duration,
                "wall_time": wall_time,
                "error": str(error) if error else None,
            }
            self._current_poll_commands.append(cmd_entry)
            _LOGGER.debug(
                "[%s/%s] Poll command 0x%02X %s in %.3fs (payload=%s)",
                self.device_name,
                self.address,
                command,
                "succeeded" if success else "failed",
                duration,
                payload.hex(),
            )

    async def _safe_send_and_sleep(
        self, command: int, payload: bytes = b"", delay: float = 0.3
    ) -> bool:
        """Best-effort version of _send_and_sleep that logs and continues on failure."""
        try:
            await self._send_and_sleep(command, payload, delay)
            return True
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "[%s/%s] Poll command 0x%02X failed (continuing poll): %s",
                self.device_name,
                self.address,
                command,
                err,
            )
            return False

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
        if self._time_poll_unsub:
            self._time_poll_unsub()
            self._time_poll_unsub = None
        # Start a strict time-based poll regardless of advertisements.
        self._time_poll_unsub = async_track_time_interval(
            self.hass, self._async_time_poll, timedelta(seconds=self._poll_interval)
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
        last_poll_age = (
            time.time() - self._last_poll_started_at
            if self._last_poll_started_at
            else None
        )

        _LOGGER.debug(
            "_needs_poll called: ble_device=%s, seconds_since_last_poll=%s, last_poll_age=%.1f, interval=%ss, needs_poll=%s",
            ble_device is not None,
            seconds_since_last_poll,
            last_poll_age if last_poll_age is not None else -1,
            self._poll_interval,
            needs_poll,
        )

        return needs_poll

    async def _async_update(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> MarstekData:
        """Poll the device for data."""
        async with self._poll_lock:
            return await self._async_run_poll(service_info)

    async def _async_time_poll(self, _now) -> None:
        """Time-based poll fallback when no advertisements arrive."""
        # Build a minimal service_info stand-in when we haven't seen fresh advertisements.
        service_info = self._last_service_info
        if service_info is None:
            ble_dev = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if not ble_dev:
                _LOGGER.debug(
                    "[%s/%s] Time poll skipped: no connectable BLE device available",
                    self.device_name,
                    self.address,
                )
                return
            service_info = SimpleNamespace(device=ble_dev)

        async with self._poll_lock:
            await self._async_run_poll(service_info)

    async def _async_run_poll(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> MarstekData:
        """Shared poll execution path (used by event and timer triggers)."""
        start_monotonic = time.monotonic()
        wall_start = time.time()
        self._last_poll_started_at = wall_start
        self._current_poll_commands = []
        self._last_service_info = service_info
        _LOGGER.debug(
            "[%s/%s] Poll cycle start (interval=%ss, since_last=%.1fs) from %s",
            self.device_name,
            self.address,
            self._poll_interval,
            (wall_start - self._last_poll_completed_at)
            if self._last_poll_completed_at
            else -1,
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
        if not self._initial_poll_done or self._fast_poll_count % self._medium_poll_cycle == 0:
            await self._poll_medium()
            self._medium_poll_count += 1

        # Slow poll (~every UPDATE_INTERVAL_SLOW seconds)
        if not self._initial_poll_done or self._fast_poll_count % self._slow_poll_cycle == 0:
            await self._poll_slow()
            self._slow_poll_count += 1

        # Return the current data snapshot so ActiveBluetoothDataUpdateCoordinator
        # retains the populated MarstekData instance.
        duration = time.monotonic() - start_monotonic
        self._last_poll_completed_at = time.time()
        self._initial_poll_done = True
        _LOGGER.debug(
            "[%s/%s] Poll cycle end in %.3fs (commands=%s)",
            self.device_name,
            self.address,
            duration,
            [
                f"0x{c['cmd']:02X}:{'ok' if c['success'] else 'fail'}@{c['duration']:.2f}s"
                for c in self._current_poll_commands
            ],
        )
        return self.data

    async def _poll_fast(self) -> None:
        """Poll fast-update data (runtime info, BMS)."""
        _LOGGER.debug(
            "[%s/%s] Polling fast data - coordinator.data before: battery_voltage=%s, battery_soc=%s",
            self.device_name,
            self.address,
            self.data.battery_voltage,
            self.data.battery_soc,
        )

        # Runtime info - now waits for actual response (Venus Monitor pattern)
        await self._safe_send_and_sleep(CMD_RUNTIME_INFO, delay=0.1)

        # BMS data - now waits for actual response
        await self._safe_send_and_sleep(CMD_BMS_DATA, delay=0.1)

        _LOGGER.debug(
            "[%s/%s] Polling fast data - coordinator.data after: battery_voltage=%s, battery_soc=%s",
            self.device_name,
            self.address,
            self.data.battery_voltage,
            self.data.battery_soc,
        )

    async def _poll_medium(self) -> None:
        """Poll medium-update data (system, WiFi, config, etc)."""
        # System data
        await self._safe_send_and_sleep(CMD_SYSTEM_DATA)

        # WiFi SSID
        await self._safe_send_and_sleep(CMD_WIFI_SSID)

        # Config data
        await self._safe_send_and_sleep(CMD_CONFIG_DATA)

        # CT polling rate
        await self._safe_send_and_sleep(CMD_CT_POLLING_RATE)

        # Meter IP
        await self._safe_send_and_sleep(CMD_METER_IP, b"\x0B")

        # Network info
        await self._safe_send_and_sleep(CMD_NETWORK_INFO)

    async def _poll_slow(self) -> None:
        """Poll slow-update data (timer info, logs)."""
        # Device info (identity/firmware)
        await self._safe_send_and_sleep(CMD_DEVICE_INFO)

        # Timer info
        await self._safe_send_and_sleep(CMD_TIMER_INFO)

        # Logs
        await self._safe_send_and_sleep(CMD_LOGS)

    def _handle_notification(self, sender: int, data: bytearray) -> None:
        """Handle notification from device."""
        raw_data = bytes(data)
        cmd = raw_data[3] if len(raw_data) > 3 else None
        VERBOSE_LOGGER.debug(
            "[%s/%s] Received notification cmd=%s from sender %s: %s",
            self.device_name,
            self.address,
            f"0x{cmd:02X}" if cmd is not None else "unknown",
            sender,
            raw_data.hex()
        )
        _LOGGER.debug(
            "[%s/%s] Data before parsing: battery_voltage=%s, battery_soc=%s",
            self.device_name,
            self.address,
            self.data.battery_voltage,
            self.data.battery_soc,
        )

        result = self._protocol.parse_notification(raw_data, self.data)
        self.device.record_notification(sender, raw_data, result)

        _LOGGER.debug(
            "[%s/%s] Parse result for cmd=%s: %s, data after parsing: battery_voltage=%s, battery_soc=%s",
            self.device_name,
            self.address,
            f"0x{cmd:02X}" if cmd is not None else "unknown",
            result,
            self.data.battery_voltage,
            self.data.battery_soc
        )

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
