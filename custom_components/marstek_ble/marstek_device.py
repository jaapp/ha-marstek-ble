"""Marstek BLE protocol handler."""
from __future__ import annotations

import asyncio
import logging
import struct
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

_LOGGER = logging.getLogger(__name__)

# BLE UUIDs
CHAR_WRITE_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
CHAR_NOTIFY_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"


@dataclass
class MarstekData:
    """Data from Marstek device."""

    # Runtime info (0x03)
    out1_power: float | None = None
    temp_low: float | None = None
    temp_high: float | None = None
    wifi_connected: bool | None = None
    mqtt_connected: bool | None = None
    out1_active: bool | None = None
    extern1_connected: bool | None = None

    # Device info (0x04)
    device_type: str | None = None
    device_id: str | None = None
    mac_address: str | None = None
    firmware_version: str | None = None

    # WiFi SSID (0x08)
    wifi_ssid: str | None = None

    # System data (0x0D)
    system_status: int | None = None
    system_value_1: int | None = None
    system_value_2: int | None = None
    system_value_3: int | None = None
    system_value_4: int | None = None
    system_value_5: int | None = None

    # Timer info (0x13)
    adaptive_mode_enabled: bool | None = None
    smart_meter_connected: bool | None = None
    adaptive_power_out: float | None = None

    # BMS data (0x14)
    battery_soc: float | None = None
    battery_soh: float | None = None
    design_capacity: float | None = None
    battery_voltage: float | None = None
    battery_current: float | None = None
    battery_temp: float | None = None
    cell_voltages: list[float | None] = field(default_factory=lambda: [None] * 16)

    # Config data (0x1A)
    config_mode: int | None = None
    config_status: int | None = None
    config_value: int | None = None

    # Meter IP (0x21)
    meter_ip: str | None = None

    # CT polling rate (0x22)
    ct_polling_rate: int | None = None

    # Network info (0x24)
    network_info: str | None = None

    # Local API status (0x28)
    local_api_status: str | None = None


class MarstekProtocol:
    """Marstek BLE protocol handler."""

    @staticmethod
    def build_command(cmd: int, payload: bytes = b"") -> bytes:
        """Build a command frame.

        Frame structure: [0x73][len][0x23][cmd][payload...][xor]
        """
        frame = bytearray([0x73, 0x00, 0x23, cmd])
        frame.extend(payload)
        frame[1] = len(frame) + 1  # Length includes checksum

        # Calculate XOR checksum
        checksum = 0
        for byte in frame:
            checksum ^= byte
        frame.append(checksum)

        return bytes(frame)

    @staticmethod
    def parse_notification(data: bytes, device_data: MarstekData) -> bool:
        """Parse notification data and update device_data.

        Returns True if data was successfully parsed.
        """
        if len(data) < 5:
            _LOGGER.warning("Notification too short (%d bytes)", len(data))
            return False

        if data[0] != 0x73 or data[2] != 0x23:
            _LOGGER.warning("Invalid header: %02X %02X %02X", data[0], data[1], data[2])
            return False

        # Verify XOR checksum
        expected_checksum = 0
        for byte in data[:-1]:
            expected_checksum ^= byte
        if data[-1] != expected_checksum:
            _LOGGER.warning("Invalid checksum: expected 0x%02X, got 0x%02X", expected_checksum, data[-1])
            return False

        cmd = data[3]
        payload = data[4:-1]  # Exclude header and checksum
        payload_len = len(payload)

        _LOGGER.debug("Parsing cmd 0x%02X, payload length %d", cmd, payload_len)

        try:
            if cmd == 0x03:  # Runtime info
                return MarstekProtocol._parse_runtime_info(payload, device_data)
            elif cmd == 0x04:  # Device info
                return MarstekProtocol._parse_device_info(payload, device_data)
            elif cmd == 0x08:  # WiFi SSID
                return MarstekProtocol._parse_wifi_ssid(payload, device_data)
            elif cmd == 0x0D:  # System data
                return MarstekProtocol._parse_system_data(payload, device_data)
            elif cmd == 0x13:  # Timer info
                return MarstekProtocol._parse_timer_info(payload, device_data)
            elif cmd == 0x14:  # BMS data
                return MarstekProtocol._parse_bms_data(payload, device_data)
            elif cmd == 0x1A:  # Config data
                return MarstekProtocol._parse_config_data(payload, device_data)
            elif cmd == 0x21:  # Meter IP
                return MarstekProtocol._parse_meter_ip(payload, device_data)
            elif cmd == 0x22:  # CT polling rate
                return MarstekProtocol._parse_ct_polling_rate(payload, device_data)
            elif cmd == 0x24:  # Network info
                return MarstekProtocol._parse_network_info(payload, device_data)
            elif cmd == 0x28:  # Local API status
                return MarstekProtocol._parse_local_api_status(payload, device_data)
            else:
                _LOGGER.debug("Unhandled cmd 0x%02X", cmd)
                return False

        except Exception as e:
            _LOGGER.exception("Error parsing cmd 0x%02X: %s", cmd, e)
            return False

    @staticmethod
    def _parse_runtime_info(payload: bytes, device_data: MarstekData) -> bool:
        """Parse runtime info (0x03)."""
        # Support both long format (109 bytes) and short format (37 bytes)
        if len(payload) < 37:
            _LOGGER.warning("Runtime info payload too short: %d bytes", len(payload))
            return False

        # Short format (37 bytes) - older firmware or different model
        if len(payload) < 60:
            _LOGGER.debug("Parsing short runtime info format (%d bytes)", len(payload))
            # Try to extract what we can from the shorter format
            # Based on observed data, the short format has limited fields
            try:
                # These offsets are tentative and may need adjustment
                if len(payload) >= 16:
                    device_data.wifi_connected = (payload[15] & 0x01) != 0
                    device_data.mqtt_connected = (payload[15] & 0x02) != 0
                if len(payload) >= 17:
                    device_data.out1_active = payload[16] != 0
                if len(payload) >= 22:
                    device_data.out1_power = float(struct.unpack("<H", payload[20:22])[0])
                if len(payload) >= 29:
                    device_data.extern1_connected = payload[28] != 0
                return True
            except Exception as e:
                _LOGGER.warning("Error parsing short runtime info: %s", e)
                return False

        # Long format (60+ bytes) - standard firmware
        device_data.out1_power = float(struct.unpack("<H", payload[20:22])[0])
        device_data.temp_low = struct.unpack("<h", payload[33:35])[0] / 10.0
        device_data.temp_high = struct.unpack("<h", payload[35:37])[0] / 10.0
        device_data.wifi_connected = (payload[15] & 0x01) != 0
        device_data.mqtt_connected = (payload[15] & 0x02) != 0
        device_data.out1_active = payload[16] != 0
        device_data.extern1_connected = payload[28] != 0

        return True

    @staticmethod
    def _parse_device_info(payload: bytes, device_data: MarstekData) -> bool:
        """Parse device info (0x04) - ASCII key=value pairs."""
        try:
            info_str = payload.decode("ascii", errors="ignore")
            pairs = info_str.split(",")

            for pair in pairs:
                if "=" not in pair:
                    continue

                key, value = pair.split("=", 1)
                key = key.strip()
                value = value.strip()

                if key == "type":
                    device_data.device_type = value
                elif key == "id":
                    device_data.device_id = value
                elif key == "mac":
                    device_data.mac_address = value
                elif key in ("dev_ver", "fc_ver"):
                    device_data.firmware_version = value

            return True
        except Exception as e:
            _LOGGER.exception("Error parsing device info: %s", e)
            return False

    @staticmethod
    def _parse_wifi_ssid(payload: bytes, device_data: MarstekData) -> bool:
        """Parse WiFi SSID (0x08)."""
        try:
            device_data.wifi_ssid = payload.decode("ascii", errors="ignore").strip()
            return True
        except Exception:
            return False

    @staticmethod
    def _parse_system_data(payload: bytes, device_data: MarstekData) -> bool:
        """Parse system data (0x0D)."""
        if len(payload) < 11:
            return False

        device_data.system_status = payload[0]
        device_data.system_value_1 = struct.unpack("<H", payload[1:3])[0]
        device_data.system_value_2 = struct.unpack("<H", payload[3:5])[0]
        device_data.system_value_3 = struct.unpack("<H", payload[5:7])[0]
        device_data.system_value_4 = struct.unpack("<H", payload[7:9])[0]
        device_data.system_value_5 = struct.unpack("<H", payload[9:11])[0]

        return True

    @staticmethod
    def _parse_timer_info(payload: bytes, device_data: MarstekData) -> bool:
        """Parse timer info (0x13)."""
        if len(payload) < 45:
            return False

        device_data.adaptive_mode_enabled = payload[0] != 0
        device_data.smart_meter_connected = payload[37] != 0
        device_data.adaptive_power_out = float(struct.unpack("<H", payload[38:40])[0])

        return True

    @staticmethod
    def _parse_bms_data(payload: bytes, device_data: MarstekData) -> bool:
        """Parse BMS data (0x14)."""
        if len(payload) < 80:
            return False

        device_data.battery_soc = float(struct.unpack("<H", payload[8:10])[0])
        device_data.battery_soh = float(struct.unpack("<H", payload[10:12])[0])
        device_data.design_capacity = float(struct.unpack("<H", payload[12:14])[0])
        device_data.battery_voltage = struct.unpack("<H", payload[14:16])[0] / 100.0
        device_data.battery_current = struct.unpack("<h", payload[16:18])[0] / 10.0
        device_data.battery_temp = float(struct.unpack("<H", payload[40:42])[0])

        # Parse cell voltages (16 cells starting at offset 48)
        for i in range(16):
            offset = 48 + i * 2
            if offset + 1 < len(payload):
                cell_voltage = struct.unpack("<H", payload[offset:offset + 2])[0] / 1000.0
                device_data.cell_voltages[i] = cell_voltage

        return True

    @staticmethod
    def _parse_config_data(payload: bytes, device_data: MarstekData) -> bool:
        """Parse config data (0x1A)."""
        if len(payload) < 17:
            return False

        device_data.config_mode = payload[0]
        device_data.config_status = struct.unpack("<b", payload[4:5])[0]
        device_data.config_value = payload[16]

        return True

    @staticmethod
    def _parse_meter_ip(payload: bytes, device_data: MarstekData) -> bool:
        """Parse meter IP (0x21)."""
        try:
            # Check if all 0xFF (not set)
            if all(b == 0xFF for b in payload):
                device_data.meter_ip = "(not set)"
            else:
                device_data.meter_ip = payload.decode("ascii", errors="ignore").strip("\x00")

            return True
        except Exception:
            return False

    @staticmethod
    def _parse_ct_polling_rate(payload: bytes, device_data: MarstekData) -> bool:
        """Parse CT polling rate (0x22)."""
        if len(payload) < 1:
            return False

        device_data.ct_polling_rate = int(payload[0])
        return True

    @staticmethod
    def _parse_network_info(payload: bytes, device_data: MarstekData) -> bool:
        """Parse network info (0x24)."""
        try:
            device_data.network_info = payload.decode("ascii", errors="ignore").strip()
            return True
        except Exception:
            return False

    @staticmethod
    def _parse_local_api_status(payload: bytes, device_data: MarstekData) -> bool:
        """Parse local API status (0x28)."""
        if len(payload) < 3:
            return False

        enabled = "enabled" if payload[0] == 1 else "disabled"
        port = struct.unpack("<H", payload[1:3])[0]
        device_data.local_api_status = f"{enabled}/{port}"

        return True


class MarstekBLEDevice:
    """Manages BLE connection and commands for Marstek device.

    Following the SwitchBot pattern - this class handles:
    - Establishing and maintaining BLE connections
    - Sending commands to the device
    - Managing connection lifecycle
    """

    def __init__(
        self,
        ble_device: BLEDevice,
        device_name: str,
        ble_device_callback: Callable[[], BLEDevice] | None = None,
        notification_callback: Callable[[int, bytearray], None] | None = None,
    ) -> None:
        """Initialize the Marstek BLE device.

        Args:
            ble_device: The BLE device object
            device_name: Human-readable device name
            ble_device_callback: Callback to get updated BLE device (for reconnection)
            notification_callback: Callback for handling BLE notifications
        """
        self._ble_device = ble_device
        self._device_name = device_name
        self._ble_device_callback = ble_device_callback
        self._notification_callback = notification_callback
        self._client: BleakClientWithServiceCache | None = None
        self._connect_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._disconnect_timer: asyncio.TimerHandle | None = None
        self._expected_disconnect = False
        self._notifications_started = False
        self._command_history: deque[dict[str, Any]] = deque(maxlen=25)
        self._notification_history: deque[dict[str, Any]] = deque(maxlen=25)
        self._command_stats: defaultdict[int, dict[str, Any]] = defaultdict(
            lambda: {
                "sent": 0,
                "success": 0,
                "failure": 0,
                "last_success": None,
                "last_failure": None,
                "last_error": None,
                "last_notification": None,
                "last_notification_hex": None,
            }
        )
        self._total_commands_sent = 0
        self._total_commands_success = 0
        self._total_commands_failure = 0
        self._last_command_error: str | None = None

    @property
    def name(self) -> str:
        """Return the device name."""
        return self._device_name

    @property
    def address(self) -> str:
        """Return the device address."""
        return self._ble_device.address

    async def _ensure_connected(self) -> None:
        """Ensure we have an active BLE connection."""
        if self._client and self._client.is_connected:
            _LOGGER.debug("%s: Already connected", self._device_name)
            return

        async with self._connect_lock:
            # Double-check after acquiring lock
            if self._client and self._client.is_connected:
                return

            _LOGGER.debug("%s: Establishing connection", self._device_name)

            # Get fresh BLE device if callback available
            if self._ble_device_callback:
                self._ble_device = self._ble_device_callback()

            try:
                self._client = await establish_connection(
                    BleakClientWithServiceCache,
                    self._ble_device,
                    self._device_name,
                    disconnected_callback=self._on_disconnect,
                    use_services_cache=True,
                    ble_device_callback=self._ble_device_callback,
                )
                _LOGGER.debug("%s: Connected successfully", self._device_name)

                # Start notifications if callback provided
                if self._notification_callback and not self._notifications_started:
                    _LOGGER.debug("%s: Starting notifications for %s", self._device_name, CHAR_NOTIFY_UUID)
                    await self._client.start_notify(
                        CHAR_NOTIFY_UUID, self._notification_callback
                    )
                    self._notifications_started = True
                    _LOGGER.debug("%s: Notifications started successfully", self._device_name)
                else:
                    _LOGGER.debug(
                        "%s: Notifications already started or no callback (callback=%s, started=%s)",
                        self._device_name,
                        self._notification_callback is not None,
                        self._notifications_started,
                    )

            except (BleakError, TimeoutError) as ex:
                _LOGGER.warning(
                    "%s: Failed to connect: %s", self._device_name, ex
                )
                raise

    def _on_disconnect(self, client: BleakClientWithServiceCache) -> None:
        """Handle disconnection."""
        if self._expected_disconnect:
            _LOGGER.debug("%s: Expected disconnect", self._device_name)
            self._expected_disconnect = False
        else:
            _LOGGER.warning("%s: Unexpected disconnect", self._device_name)
        self._client = None
        self._notifications_started = False

    def _reset_disconnect_timer(self) -> None:
        """Reset the disconnect timer."""
        if self._disconnect_timer:
            self._disconnect_timer.cancel()

        # Disconnect after 30 seconds of inactivity
        loop = asyncio.get_event_loop()
        self._disconnect_timer = loop.call_later(
            30.0, lambda: asyncio.create_task(self._execute_disconnect())
        )

    async def _execute_disconnect(self) -> None:
        """Execute disconnection."""
        async with self._connect_lock:
            if self._client and self._client.is_connected:
                _LOGGER.debug("%s: Disconnecting due to inactivity", self._device_name)
                self._expected_disconnect = True
                await self._client.disconnect()
            self._disconnect_timer = None

    async def send_command(
        self, cmd: int, payload: bytes = b"", retry: int = 3
    ) -> bool:
        """Send a command to the device.

        Args:
            cmd: Command byte
            payload: Command payload
            retry: Number of retry attempts

        Returns:
            True if command was sent successfully
        """
        command_data = MarstekProtocol.build_command(cmd, payload)
        attempts_made = 0
        last_error: str | None = None

        async with self._operation_lock:
            for attempt in range(retry):
                attempts_made = attempt + 1
                try:
                    await self._ensure_connected()

                    _LOGGER.debug(
                        "%s: Sending command 0x%02X (attempt %d/%d): %s",
                        self._device_name,
                        cmd,
                        attempt + 1,
                        retry,
                        command_data.hex(),
                    )

                    await self._client.write_gatt_char(CHAR_WRITE_UUID, command_data)

                    self._reset_disconnect_timer()

                    _LOGGER.debug(
                        "%s: Command 0x%02X sent successfully", self._device_name, cmd
                    )
                    self._record_command_result(
                        cmd=cmd,
                        frame=command_data,
                        attempts=attempts_made,
                        success=True,
                        error=None,
                    )
                    return True

                except (BleakError, TimeoutError) as ex:
                    last_error = str(ex)
                    _LOGGER.warning(
                        "%s: Failed to send command 0x%02X (attempt %d/%d): %s",
                        self._device_name,
                        cmd,
                        attempt + 1,
                        retry,
                        ex,
                    )
                    # Force reconnect on next attempt
                    if self._client:
                        self._expected_disconnect = True
                        try:
                            await self._client.disconnect()
                        except Exception:
                            pass
                        self._client = None

                    if attempt < retry - 1:
                        await asyncio.sleep(0.5)

            _LOGGER.error(
                "%s: Failed to send command 0x%02X after %d attempts",
                self._device_name,
                cmd,
                retry,
            )
            self._record_command_result(
                cmd=cmd,
                frame=command_data,
                attempts=attempts_made,
                success=False,
                error=last_error,
            )
            return False

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        if self._disconnect_timer:
            self._disconnect_timer.cancel()
            self._disconnect_timer = None

        async with self._connect_lock:
            if self._client and self._client.is_connected:
                _LOGGER.debug("%s: Disconnecting", self._device_name)

                # Stop notifications if started
                if self._notifications_started:
                    try:
                        await self._client.stop_notify(CHAR_NOTIFY_UUID)
                        _LOGGER.debug("%s: Notifications stopped", self._device_name)
                    except Exception as ex:
                        _LOGGER.debug("%s: Error stopping notifications: %s", self._device_name, ex)
                    self._notifications_started = False

                self._expected_disconnect = True
                await self._client.disconnect()
            self._client = None

    @property
    def is_connected(self) -> bool:
        """Return whether the BLE client is currently connected."""
        return bool(self._client and self._client.is_connected)

    def _record_command_result(
        self,
        *,
        cmd: int,
        frame: bytes,
        attempts: int,
        success: bool,
        error: str | None,
    ) -> None:
        """Record diagnostic information about a command."""
        timestamp = time.time()
        payload = frame[4:-1] if len(frame) > 5 else b""

        self._command_history.append(
            {
                "timestamp": timestamp,
                "command": f"0x{cmd:02X}",
                "payload_hex": payload.hex(),
                "frame_hex": frame.hex(),
                "attempts": attempts,
                "success": success,
                "error": error,
            }
        )

        stats = self._command_stats[cmd]
        stats["sent"] += 1
        self._total_commands_sent += 1

        if success:
            stats["success"] += 1
            stats["last_success"] = timestamp
            self._total_commands_success += 1
            self._last_command_error = None
        else:
            stats["failure"] += 1
            stats["last_failure"] = timestamp
            stats["last_error"] = error
            self._total_commands_failure += 1
            self._last_command_error = error

    def record_notification(
        self, sender: int, data: bytes, parsed: bool
    ) -> None:
        """Record details of the latest notifications for diagnostics."""
        timestamp = time.time()
        command = data[3] if len(data) > 3 else None
        payload = data[4:-1] if len(data) > 5 else b""

        entry = {
            "timestamp": timestamp,
            "sender": sender,
            "command": f"0x{command:02X}" if command is not None else None,
            "frame_hex": data.hex(),
            "payload_hex": payload.hex(),
            "parsed": parsed,
        }
        self._notification_history.append(entry)

        if command is not None:
            stats = self._command_stats[command]
            stats["last_notification"] = timestamp
            stats["last_notification_hex"] = data.hex()

    @staticmethod
    def _iso_timestamp(timestamp: float | None) -> str | None:
        """Convert a timestamp to ISO format in UTC."""
        if timestamp is None:
            return None
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()

    def get_diagnostics(self) -> dict[str, Any]:
        """Return diagnostic information for this BLE device."""
        command_history = [
            {
                **{
                    k: v
                    for k, v in entry.items()
                    if k != "timestamp"
                },
                "timestamp": self._iso_timestamp(entry["timestamp"]),
            }
            for entry in list(self._command_history)
        ]

        notification_history = [
            {
                **{
                    k: v
                    for k, v in entry.items()
                    if k != "timestamp"
                },
                "timestamp": self._iso_timestamp(entry["timestamp"]),
            }
            for entry in list(self._notification_history)
        ]

        command_stats: dict[str, Any] = {}
        for cmd, stats in self._command_stats.items():
            sent = stats["sent"]
            success = stats["success"]
            success_rate = (success / sent) if sent else None
            command_stats[f"0x{cmd:02X}"] = {
                "sent": sent,
                "success": success,
                "failure": stats["failure"],
                "success_rate": round(success_rate * 100, 2) if success_rate is not None else None,
                "ratio": f"{success}/{sent}" if sent else "0/0",
                "last_success": self._iso_timestamp(stats.get("last_success")),
                "last_failure": self._iso_timestamp(stats.get("last_failure")),
                "last_notification": self._iso_timestamp(stats.get("last_notification")),
                "last_notification_hex": stats.get("last_notification_hex"),
                "last_error": stats.get("last_error"),
            }

        overall_sent = self._total_commands_sent
        overall_success = self._total_commands_success
        overall_success_rate = (
            overall_success / overall_sent if overall_sent else None
        )

        return {
            "device_name": self._device_name,
            "address": self.address,
            "connected": self.is_connected,
            "overall": {
                "total_sent": overall_sent,
                "success": overall_success,
                "failure": self._total_commands_failure,
                "success_rate": round(overall_success_rate * 100, 2)
                if overall_success_rate is not None
                else None,
                "ratio": f"{overall_success}/{overall_sent}"
                if overall_sent
                else "0/0",
                "last_error": self._last_command_error,
            },
            "recent_commands": command_history,
            "recent_notifications": notification_history,
            "command_stats": command_stats,
        }
