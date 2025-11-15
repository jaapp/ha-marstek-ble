"""Data coordinator for Marstek BLE integration."""
from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from datetime import datetime, timedelta, timezone

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.core import CALLBACK_TYPE, CoreState, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

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
    COMMAND_NAMES,
    TURBO_LOG_MODE,
)
from .marstek_device import MarstekBLEDevice, MarstekData, MarstekProtocol

_LOGGER = logging.getLogger(__name__)
TRACE_LEVEL = logging.DEBUG


def _command_label(command: int) -> str:
    """Return a friendly label for a command byte."""
    name = COMMAND_NAMES.get(command)
    if name:
        return f"{name} (0x{command:02X})"
    return f"0x{command:02X}"


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
        self._loop = hass.loop
        self._fast_poll_count = 0
        self._medium_poll_count = 0
        self._slow_poll_count = 0
        self._ready_event = asyncio.Event()
        self._was_unavailable = True
        self._poll_interval = self._sanitize_poll_interval(poll_interval)
        self._medium_poll_cycle = 1
        self._slow_poll_cycle = 1
        self._update_poll_schedule()
        self._poll_lock = asyncio.Lock()
        self._last_update_ok = False
        self._consecutive_failures = 0
        self._self_heal_handle: CALLBACK_TYPE | None = None
        self._last_poll_monotonic: float | None = None
        self._stale_timeout = self._compute_stale_timeout()
        self._bms_poll_stride = 3
        self._bms_cycle = 0
        self._bms_jitter = random.uniform(0.2, 1.0)
        self._last_command_times_monotonic: dict[int, float] = {}
        self._last_command_times_wall: dict[int, float] = {}

        # Create persistent device object for command sending (SwitchBot pattern)
        self.device = MarstekBLEDevice(
            ble_device=device,
            device_name=device_name,
            ble_device_callback=lambda: bluetooth.async_ble_device_from_address(
                self.hass, address, connectable=True
            ),
            notification_callback=self._handle_notification,
        )
        self._trace(
            "Coordinator initialized (poll_interval=%ss, medium_cycle=%s, slow_cycle=%s)",
            self._poll_interval,
            self._medium_poll_cycle,
            self._slow_poll_cycle,
        )

    def _record_command_timestamp(self, command: int) -> None:
        """Record when we last handled data for a command."""
        now_monotonic = self._loop.time()
        now_wall = time.time()
        self._last_command_times_monotonic[command] = now_monotonic
        self._last_command_times_wall[command] = now_wall

    def get_command_age(self, command: int) -> float | None:
        """Return seconds since the last successful command notification."""
        timestamp = self._last_command_times_monotonic.get(command)
        if timestamp is None:
            return None
        return self._loop.time() - timestamp

    def get_command_wall_time(self, command: int) -> str | None:
        """Return ISO timestamp for the last successful command notification."""
        timestamp = self._last_command_times_wall.get(command)
        if timestamp is None:
            return None
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()

    def _trace(self, message: str, *args, level: int = TRACE_LEVEL) -> None:
        """Emit a turbo-trace log line."""
        _LOGGER.log(
            level,
            "[TRACE][%s/%s] " + message,
            self.device_name,
            self.address,
            *args,
        )

    async def _send_and_sleep(
        self,
        command: int,
        payload: bytes = b"",
        delay: float = 0.3,
        *,
        timeout: float | None = 5.0,
    ) -> None:
        """Send a command and optionally wait for a response window."""
        label = _command_label(command)
        payload_hex = payload.hex() if payload else "<empty>"
        self._trace(
            "Dispatching %s payload=%s delay=%s timeout=%s",
            label,
            payload_hex,
            delay,
            timeout,
        )
        if not await self.device.send_command(command, payload, timeout=timeout):
            self._trace("Command %s failed to send", label, level=logging.WARNING)
            raise BleakError(f"Failed to send command 0x{command:02X}")
        if delay:
            self._trace("Command %s complete; sleeping for %.2fs", label, delay)
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
        self._trace(
            "Polling schedule updated: fast=%ss, medium every %s, slow every %s",
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
        self._stale_timeout = self._compute_stale_timeout()
        _LOGGER.debug(
            "[%s/%s] Poll interval changed to %ss; stale timeout now %ss",
            self.device_name,
            self.address,
            self._poll_interval,
            self._stale_timeout,
        )
        self._trace(
            "Poll interval now %ss; stale timeout %ss",
            self._poll_interval,
            self._stale_timeout,
        )
        self._schedule_self_heal()

    def async_start(self) -> CALLBACK_TYPE:
        """Start coordinator listeners and boot the self-heal watchdog."""
        self._trace("Coordinator async_start invoked")
        stop_callback = super().async_start()
        self._schedule_self_heal()

        def _stop() -> None:
            self._trace("Coordinator stop requested")
            stop_callback()
            self._cancel_self_heal()

        return _stop

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
        self._trace(
            "_needs_poll -> device_present=%s seconds_since=%s result=%s",
            ble_device is not None,
            seconds_since_last_poll,
            needs_poll,
        )

        return needs_poll

    async def _async_update(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> MarstekData:
        """Poll the device for data."""
        _LOGGER.debug(
            "[%s/%s] _async_update called - Updating Marstek data from %s",
            self.device_name,
            self.address,
            service_info.device.address,
        )
        self._trace(
            "_async_update triggered by advertisement from %s",
            service_info.device.address,
        )

        # Update BLE device reference
        return await self._async_poll_cycle(
            ble_device=service_info.device,
            raise_on_error=True,
            log_prefix="bluetooth",
        )

    async def _poll_fast(self) -> None:
        """Poll fast-update data (runtime info, BMS)."""
        self._trace(
            "Starting fast poll #%s (voltage=%s soc=%s)",
            self._fast_poll_count + 1,
            self.data.battery_voltage,
            self.data.battery_soc,
        )
        _LOGGER.debug(
            "[%s/%s] Polling fast data - coordinator.data before: battery_voltage=%s, battery_soc=%s",
            self.device_name,
            self.address,
            self.data.battery_voltage,
            self.data.battery_soc,
        )

        # Runtime info - now waits for actual response (Venus Monitor pattern)
        await self._send_and_sleep(CMD_RUNTIME_INFO, delay=0.1)

        self._bms_cycle = (self._bms_cycle + 1) % self._bms_poll_stride
        if self._bms_cycle == 0:
            _LOGGER.debug(
                "[%s/%s] BMS poll triggered (stride=%s); jitter=%.2fs",
                self.device_name,
                self.address,
                self._bms_poll_stride,
                self._bms_jitter,
            )
            self._trace(
                "BMS poll triggered (stride=%s, jitter=%.2fs)",
                self._bms_poll_stride,
                self._bms_jitter,
            )
            await asyncio.sleep(self._bms_jitter)
            await self._send_and_sleep(CMD_BMS_DATA, delay=0.1, timeout=5.0)
        else:
            _LOGGER.debug(
                "[%s/%s] Skipping BMS poll this cycle (stride=%s, cycle=%s)",
                self.device_name,
                self.address,
                self._bms_poll_stride,
                self._bms_cycle,
            )
            self._trace(
                "Skipping BMS poll (stride=%s cycle=%s)",
                self._bms_poll_stride,
                self._bms_cycle,
            )

        _LOGGER.debug(
            "[%s/%s] Polling fast data - coordinator.data after: battery_voltage=%s, battery_soc=%s",
            self.device_name,
            self.address,
            self.data.battery_voltage,
            self.data.battery_soc,
        )
        self._trace(
            "Fast poll #%s finished (voltage=%s soc=%s)",
            self._fast_poll_count + 1,
            self.data.battery_voltage,
            self.data.battery_soc,
        )

    async def _poll_medium(self) -> None:
        """Poll medium-update data (system, WiFi, config, etc)."""
        self._trace("Starting medium poll #%s", self._medium_poll_count + 1)
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
        self._trace("Medium poll #%s complete", self._medium_poll_count + 1)

    async def _poll_slow(self) -> None:
        """Poll slow-update data (timer info, logs)."""
        self._trace("Starting slow poll #%s", self._slow_poll_count + 1)
        # Timer info
        await self._send_and_sleep(CMD_TIMER_INFO)

        # Logs
        await self._send_and_sleep(CMD_LOGS)
        self._trace("Slow poll #%s complete", self._slow_poll_count + 1)

    def _handle_notification(self, sender: int, data: bytearray) -> None:
        """Handle notification from device."""
        raw_data = bytes(data)
        command = raw_data[3] if len(raw_data) > 3 else None
        _LOGGER.debug(
            "[%s/%s] Received notification from sender %s: %s",
            self.device_name,
            self.address,
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
        if result and command is not None:
            self._record_command_timestamp(command)
            label = _command_label(command)
            if command == CMD_BMS_DATA:
                cells = [cell for cell in self.data.cell_voltages if cell is not None]
                cell_min = min(cells) if cells else None
                cell_max = max(cells) if cells else None
                cell_avg = sum(cells) / len(cells) if cells else None
                self._trace(
                    "BMS data applied (soc=%s voltage=%s cells=%s min=%s max=%s avg=%s)",
                    self.data.battery_soc,
                    self.data.battery_voltage,
                    len(cells),
                    cell_min,
                    cell_max,
                    cell_avg,
                )
            else:
                self._trace(
                    "Notification parsed for %s",
                    label,
                )

        _LOGGER.debug(
            "[%s/%s] Parse result: %s, data after parsing: battery_voltage=%s, battery_soc=%s",
            self.device_name,
            self.address,
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
        self._last_update_ok = False
        _LOGGER.info("Device %s is unavailable", self.device_name)
        self._trace("Device marked unavailable due to missing advertisements")

    @callback
    def _async_handle_bluetooth_event(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Handle a Bluetooth event."""
        _LOGGER.debug("_async_handle_bluetooth_event called: device=%s, change=%s",
                     service_info.device.address, change)
        self._trace(
            "Bluetooth event received from %s change=%s",
            service_info.device.address,
            change,
        )

        self.ble_device = service_info.device

        # Mark device as ready when we receive advertisements
        if not self._ready_event.is_set():
            self._ready_event.set()
            _LOGGER.info("Device %s marked as ready", self.device_name)
            self._trace("Device marked as ready from advertisement stream")

        if self._was_unavailable:
            self._was_unavailable = False
            _LOGGER.info("Device %s is online", self.device_name)
            self._trace("Device transitioned to online")

        super()._async_handle_bluetooth_event(service_info, change)

    async def async_wait_ready(self) -> bool:
        """Wait for the device to be ready."""
        import contextlib
        self._trace("Waiting for advertisement ready event")
        try:
            async with asyncio.timeout(30):
                await self._ready_event.wait()
                self._trace("Advertisement ready event received")
                return True
        except TimeoutError:
            self._trace(
                "Timed out waiting for advertisement ready event",
                level=logging.WARNING,
            )
            return False

    def _cancel_self_heal(self) -> None:
        """Cancel the self-heal watchdog."""
        if self._self_heal_handle:
            _LOGGER.debug(
                "[%s/%s] Cancelling pending self-heal callback",
                self.device_name,
                self.address,
            )
            self._trace("Cancelling pending self-heal callback")
            self._self_heal_handle()
            self._self_heal_handle = None

    def _schedule_self_heal(self) -> None:
        """Schedule or reschedule the self-heal watchdog."""
        self._cancel_self_heal()
        delay = max(self._poll_interval, 30)
        _LOGGER.debug(
            "[%s/%s] Scheduling self-heal in %ss (poll_interval=%s)",
            self.device_name,
            self.address,
            delay,
            self._poll_interval,
        )
        self._trace(
            "Scheduling self-heal in %ss (poll_interval=%s)",
            delay,
            self._poll_interval,
        )
        self._self_heal_handle = async_call_later(
            self.hass, delay, self._self_heal_callback
        )

    def _self_heal_callback(self, _now) -> None:
        """Handle watchdog tick."""
        self._self_heal_handle = None
        _LOGGER.debug(
            "[%s/%s] Self-heal watchdog fired (last_ok=%s, last_poll=%s, stale=%ss)",
            self.device_name,
            self.address,
            self._last_update_ok,
            self._last_poll_monotonic,
            self._stale_timeout,
        )
        self._trace(
            "Self-heal watchdog fired (last_ok=%s last_poll=%s stale=%ss)",
            self._last_update_ok,
            self._last_poll_monotonic,
            self._stale_timeout,
        )
        if self.hass.is_stopping:
            _LOGGER.debug(
                "[%s/%s] Skipping self-heal because hass is stopping",
                self.device_name,
                self.address,
            )
            self._trace("Skipping self-heal because hass is stopping")
            return
        should_heal = not self._last_update_ok
        now = self._loop.time()

        if self._last_poll_monotonic is None:
            should_heal = True
            self._trace("Self-heal triggered because last poll timestamp missing")
        else:
            time_since_poll = now - self._last_poll_monotonic
            if time_since_poll > self._stale_timeout:
                should_heal = True
                _LOGGER.debug(
                    "[%s/%s] Marking data stale (%.1fs since last poll, threshold=%ss)",
                    self.device_name,
                    self.address,
                    time_since_poll,
                    self._stale_timeout,
                )
                self._trace(
                    "Data marked stale (%.1fs > %ss)",
                    time_since_poll,
                    self._stale_timeout,
                )
            else:
                _LOGGER.debug(
                    "[%s/%s] Last poll %.1fs ago (<%ss) – no heal",
                    self.device_name,
                    self.address,
                    time_since_poll,
                    self._stale_timeout,
                )
                self._trace(
                    "Last poll %.1fs ago (<%ss) – no heal",
                    time_since_poll,
                    self._stale_timeout,
                )

        if not should_heal:
            self._schedule_self_heal()
            return
        _LOGGER.debug(
            "[%s/%s] Self-heal deemed necessary – scheduling task",
            self.device_name,
            self.address,
        )
        self._trace("Self-heal deemed necessary – scheduling task")

        def _dispatch() -> None:
            self.hass.async_create_task(self._async_self_heal())

        self._loop.call_soon_threadsafe(_dispatch)

    async def _async_self_heal(self) -> None:
        """Force a reconnect/poll attempt after repeated failures."""
        _LOGGER.debug(
            "[%s/%s] Starting self-heal coroutine",
            self.device_name,
            self.address,
        )
        self._trace("Starting self-heal coroutine")
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            ble_device = self.ble_device

        if ble_device is None:
            _LOGGER.debug(
                "[%s/%s] Self-heal skipped: no connectable device yet",
                self.device_name,
                self.address,
            )
            self._trace(
                "Self-heal skipped because no connectable device was found",
                level=logging.WARNING,
            )
            self._schedule_self_heal()
            return
        else:
            self._trace(
                "Self-heal using device %s",
                ble_device.address if ble_device.address else "unknown",
            )

        _LOGGER.info(
            "[%s/%s] Self-heal triggered - forcing reconnect",
            self.device_name,
            self.address,
        )
        await self._async_poll_cycle(
            ble_device=ble_device,
            raise_on_error=False,
            log_prefix="self-heal",
        )
        _LOGGER.debug(
            "[%s/%s] Self-heal poll finished (last_ok=%s)",
            self.device_name,
            self.address,
            self._last_update_ok,
        )
        self._trace("Self-heal poll finished (last_ok=%s)", self._last_update_ok)
        self._schedule_self_heal()

    async def _async_poll_cycle(
        self,
        *,
        ble_device: BLEDevice | None,
        raise_on_error: bool,
            log_prefix: str,
        ) -> MarstekData:
        """Execute one poll cycle with optional error handling."""
        async with self._poll_lock:
            _LOGGER.debug(
                "[%s/%s] Entering poll cycle (prefix=%s, fast#=%s)",
                self.device_name,
                self.address,
                log_prefix,
                self._fast_poll_count,
            )
            self._trace(
                "Entering poll cycle (prefix=%s fast=%s medium=%s slow=%s)",
                log_prefix,
                self._fast_poll_count,
                self._medium_poll_count,
                self._slow_poll_count,
            )
            if ble_device is not None:
                self.ble_device = ble_device

            try:
                _LOGGER.debug(
                    "[%s/%s] Poll cycle: running fast poll",
                    self.device_name,
                    self.address,
                )
                await self._poll_fast()
                self._fast_poll_count += 1
                _LOGGER.debug(
                    "[%s/%s] Fast poll complete (#%s total)",
                    self.device_name,
                    self.address,
                    self._fast_poll_count,
                )

                if self._fast_poll_count % self._medium_poll_cycle == 0:
                    _LOGGER.debug(
                        "[%s/%s] Poll cycle: running medium poll "
                        "(count=%s, cycle=%s)",
                        self.device_name,
                        self.address,
                        self._fast_poll_count,
                        self._medium_poll_cycle,
                    )
                    await self._poll_medium()
                    self._medium_poll_count += 1

                if self._fast_poll_count % self._slow_poll_cycle == 0:
                    _LOGGER.debug(
                        "[%s/%s] Poll cycle: running slow poll "
                        "(count=%s, cycle=%s)",
                        self.device_name,
                        self.address,
                        self._fast_poll_count,
                        self._slow_poll_cycle,
                    )
                    await self._poll_slow()
                    self._slow_poll_count += 1

                self._last_update_ok = True
                self._consecutive_failures = 0
                self._last_poll_monotonic = self._loop.time()
                _LOGGER.debug(
                    "[%s/%s] Poll cycle success (fast=%s, medium=%s, slow=%s, ok=%s)",
                    self.device_name,
                    self.address,
                    self._fast_poll_count,
                    self._medium_poll_count,
                    self._slow_poll_count,
                    self._last_update_ok,
                )
                self._trace(
                    "Poll cycle success (fast=%s medium=%s slow=%s)",
                    self._fast_poll_count,
                    self._medium_poll_count,
                    self._slow_poll_count,
                )
                return self.data

            except Exception as err:
                self._last_update_ok = False
                self._consecutive_failures += 1
                _LOGGER.debug(
                    "[%s/%s] %s poll failed: %s",
                    self.device_name,
                    self.address,
                    log_prefix,
                    err,
                    exc_info=True,
                )
                _LOGGER.debug(
                    "[%s/%s] Poll failure stats: consecutive_failures=%s",
                    self.device_name,
                    self.address,
                    self._consecutive_failures,
                )
                self._trace(
                    "Poll cycle failure (%s) consecutive_failures=%s",
                    err,
                    self._consecutive_failures,
                    level=logging.ERROR,
                )
                if raise_on_error:
                    raise
                return self.data

    def _compute_stale_timeout(self) -> float:
        """Return the threshold (in seconds) after which data is considered stale."""
        timeout = float(max(self._poll_interval * 2, 60))
        _LOGGER.debug(
            "[%s/%s] Computed stale timeout: %ss (poll_interval=%s)",
            self.device_name,
            self.address,
            timeout,
            self._poll_interval,
        )
        return timeout
