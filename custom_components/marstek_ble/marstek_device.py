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
VERBOSE_LOGGER = logging.getLogger(f"{__name__}.verbose")
VERBOSE_LOGGER.propagate = False
VERBOSE_LOGGER.setLevel(logging.INFO)
DEVICE_DEBUG = logging.getLogger(f"{__name__}.device")
DEVICE_DEBUG.propagate = False
DEVICE_DEBUG.setLevel(logging.INFO)

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
    # Runtime info (0x03) - additional fields
    grid_power: float | None = None  # Backup power (signed, watts)
    solar_power: float | None = None  # Battery power (signed, watts)
    work_mode: int | None = None  # Operating mode (0-7)
    product_code: int | None = None  # Model identification
    power_rating: int | None = None  # System power capacity (watts)
    daily_energy_charged: float | None = None  # kWh
    daily_energy_discharged: float | None = None  # kWh
    monthly_energy_charged: float | None = None  # kWh
    monthly_energy_discharged: float | None = None  # kWh
    total_energy_charged: float | None = None  # kWh
    total_energy_discharged: float | None = None  # kWh

    # Device info (0x04)
    device_type: str | None = None
    device_id: str | None = None
    serial_number: str | None = None
    mac_address: str | None = None
    firmware_version: str | None = None
    hardware_version: str | None = None

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
    # BMS data (0x14) - additional fields
    bms_version: int | None = None
    voltage_limit: float | None = None  # V
    charge_current_limit: float | None = None  # A
    discharge_current_limit: float | None = None  # A
    error_code: int | None = None
    warning_code: int | None = None
    runtime_hours: float | None = None  # hours
    mosfet_temp: float | None = None  # °C
    temp_sensor_1: float | None = None  # °C
    temp_sensor_2: float | None = None  # °C
    temp_sensor_3: float | None = None  # °C
    temp_sensor_4: float | None = None  # °C

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
    ip_address: str | None = None
    gateway: str | None = None
    subnet_mask: str | None = None
    dns_server: str | None = None

    # Local API status (0x28)
    local_api_status: str | None = None

    # Internal diagnostics
    field_updates: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)

    def mark_field_update(
        self,
        field: str,
        command: int,
        *,
        timestamp: float | None = None,
        payload: bytes | None = None,
    ) -> None:
        """Record when a field was last updated and by which command."""
        ts = timestamp or time.time()
        self.field_updates[field] = {
            "command": command,
            "timestamp": ts,
            "payload_hex": payload.hex() if payload else None,
        }

    def get_field_metadata(self, field: str) -> dict[str, Any] | None:
        """Return metadata for a field including age in seconds."""
        entry = self.field_updates.get(field)
        if not entry:
            return None

        timestamp = entry.get("timestamp")
        age = time.time() - timestamp if timestamp else None
        return {
            "command": entry.get("command"),
            "command_hex": f"0x{entry['command']:02X}" if entry.get("command") is not None else None,
            "timestamp": datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
            if timestamp
            else None,
            "age_seconds": age,
            "payload_hex": entry.get("payload_hex"),
        }


class MarstekProtocol:
    """Marstek BLE protocol handler."""

    @staticmethod
    def _track_field(
        device_data: MarstekData,
        field: str,
        command: int,
        timestamp: float,
        payload: bytes | None = None,
    ) -> None:
        """Mark a field as updated by a specific command."""
        device_data.mark_field_update(field, command, timestamp=timestamp, payload=payload)

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
        timestamp = time.time()

        _LOGGER.debug("Parsing cmd 0x%02X, payload length %d", cmd, payload_len)

        try:
            if cmd == 0x03:  # Runtime info
                return MarstekProtocol._parse_runtime_info(payload, device_data, timestamp)
            elif cmd == 0x04:  # Device info
                return MarstekProtocol._parse_device_info(payload, device_data, timestamp)
            elif cmd == 0x08:  # WiFi SSID
                return MarstekProtocol._parse_wifi_ssid(payload, device_data, timestamp)
            elif cmd == 0x0D:  # System data
                return MarstekProtocol._parse_system_data(payload, device_data, timestamp)
            elif cmd == 0x13:  # Timer info
                return MarstekProtocol._parse_timer_info(payload, device_data, timestamp)
            elif cmd == 0x14:  # BMS data
                return MarstekProtocol._parse_bms_data(payload, device_data, timestamp)
            elif cmd == 0x1A:  # Config data
                return MarstekProtocol._parse_config_data(payload, device_data, timestamp)
            elif cmd == 0x21:  # Meter IP
                return MarstekProtocol._parse_meter_ip(payload, device_data, timestamp)
            elif cmd == 0x22:  # CT polling rate
                return MarstekProtocol._parse_ct_polling_rate(payload, device_data, timestamp)
            elif cmd == 0x24:  # Network info
                return MarstekProtocol._parse_network_info(payload, device_data, timestamp)
            elif cmd == 0x28:  # Local API status
                return MarstekProtocol._parse_local_api_status(payload, device_data, timestamp)
            else:
                _LOGGER.debug("Unhandled cmd 0x%02X", cmd)
                return False

        except Exception as e:
            _LOGGER.exception("Error parsing cmd 0x%02X: %s", cmd, e)
            return False

    @staticmethod
    def _parse_runtime_info(
        payload: bytes, device_data: MarstekData, timestamp: float
    ) -> bool:
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
                    MarstekProtocol._track_field(
                        device_data, "wifi_connected", 0x03, timestamp, payload
                    )
                    MarstekProtocol._track_field(
                        device_data, "mqtt_connected", 0x03, timestamp, payload
                    )
                if len(payload) >= 17:
                    device_data.out1_active = payload[16] != 0
                    MarstekProtocol._track_field(
                        device_data, "out1_active", 0x03, timestamp, payload
                    )
                if len(payload) >= 22:
                    device_data.out1_power = float(struct.unpack("<H", payload[20:22])[0])
                    MarstekProtocol._track_field(
                        device_data, "out1_power", 0x03, timestamp, payload
                    )
                if len(payload) >= 29:
                    device_data.extern1_connected = payload[28] != 0
                    MarstekProtocol._track_field(
                        device_data, "extern1_connected", 0x03, timestamp, payload
                    )
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

        # Parse additional fields if payload is long enough (100+ bytes)
        if len(payload) >= 100:
            # Grid/backup power (signed) at offset 0x00
            device_data.grid_power = float(struct.unpack("<h", payload[0:2])[0])
            # Solar/battery power (signed) at offset 0x02
            device_data.solar_power = float(struct.unpack("<h", payload[2:4])[0])
            # Work mode at offset 0x04
            device_data.work_mode = int(payload[4])
            # Product code at offset 0x0C
            device_data.product_code = int(struct.unpack("<H", payload[12:14])[0])
            # Daily charge at offset 0x0E (÷100 for kWh)
            device_data.daily_energy_charged = struct.unpack("<I", payload[14:18])[0] / 100.0
            # Monthly charge at offset 0x12 (÷1000 for kWh)
            device_data.monthly_energy_charged = struct.unpack("<I", payload[18:22])[0] / 1000.0
            # Daily discharge at offset 0x16 (÷100 for kWh)
            device_data.daily_energy_discharged = struct.unpack("<I", payload[22:26])[0] / 100.0
            # Monthly discharge at offset 0x1A (÷100 for kWh)
            device_data.monthly_energy_discharged = struct.unpack("<I", payload[26:30])[0] / 100.0
            # Total charge at offset 0x29 (÷100 for kWh)
            device_data.total_energy_charged = struct.unpack("<I", payload[41:45])[0] / 100.0
            # Total discharge at offset 0x2D (÷100 for kWh)
            device_data.total_energy_discharged = struct.unpack("<I", payload[45:49])[0] / 100.0
            # Power rating at offset 0x4A
            device_data.power_rating = int(struct.unpack("<H", payload[74:76])[0])

            for field in (
                "grid_power",
                "solar_power",
                "work_mode",
                "product_code",
                "daily_energy_charged",
                "monthly_energy_charged",
                "daily_energy_discharged",
                "monthly_energy_discharged",
                "total_energy_charged",
                "total_energy_discharged",
                "power_rating",
            ):
                MarstekProtocol._track_field(
                    device_data, field, 0x03, timestamp, payload
                )

        for field in (
            "out1_power",
            "temp_low",
            "temp_high",
            "wifi_connected",
            "mqtt_connected",
            "out1_active",
            "extern1_connected",
        ):
            MarstekProtocol._track_field(
                device_data, field, 0x03, timestamp, payload
            )

        _LOGGER.debug(
            "Runtime data parsed (cmd=0x03): power=%sW wifi=%s mqtt=%s out1_active=%s temp_low/high=%s/%s grid=%sW solar=%sW",
            device_data.out1_power,
            device_data.wifi_connected,
            device_data.mqtt_connected,
            device_data.out1_active,
            device_data.temp_low,
            device_data.temp_high,
            device_data.grid_power,
            device_data.solar_power,
        )

        return True

    @staticmethod
    def _parse_device_info(
        payload: bytes, device_data: MarstekData, timestamp: float
    ) -> bool:
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
                elif key == "sn":
                    device_data.serial_number = value
                elif key == "mac":
                    device_data.mac_address = value
                elif key in ("dev_ver", "fc_ver", "fw"):
                    device_data.firmware_version = value
                elif key == "hw":
                    device_data.hardware_version = value

            for field in ("device_type", "device_id", "serial_number", "mac_address", "firmware_version", "hardware_version"):
                if getattr(device_data, field) is not None:
                    MarstekProtocol._track_field(
                        device_data, field, 0x04, timestamp, payload
                    )

            return True
        except Exception as e:
            _LOGGER.exception("Error parsing device info: %s", e)
            return False

    @staticmethod
    def _parse_wifi_ssid(
        payload: bytes, device_data: MarstekData, timestamp: float
    ) -> bool:
        """Parse WiFi SSID (0x08)."""
        try:
            device_data.wifi_ssid = payload.decode("ascii", errors="ignore").strip()
            MarstekProtocol._track_field(
                device_data, "wifi_ssid", 0x08, timestamp, payload
            )
            return True
        except Exception:
            return False

    @staticmethod
    def _parse_system_data(
        payload: bytes, device_data: MarstekData, timestamp: float
    ) -> bool:
        """Parse system data (0x0D)."""
        if len(payload) < 11:
            return False

        device_data.system_status = payload[0]
        device_data.system_value_1 = struct.unpack("<H", payload[1:3])[0]
        device_data.system_value_2 = struct.unpack("<H", payload[3:5])[0]
        device_data.system_value_3 = struct.unpack("<H", payload[5:7])[0]
        device_data.system_value_4 = struct.unpack("<H", payload[7:9])[0]
        device_data.system_value_5 = struct.unpack("<H", payload[9:11])[0]

        for field in (
            "system_status",
            "system_value_1",
            "system_value_2",
            "system_value_3",
            "system_value_4",
            "system_value_5",
        ):
            MarstekProtocol._track_field(
                device_data, field, 0x0D, timestamp, payload
            )

        return True

    @staticmethod
    def _parse_timer_info(
        payload: bytes, device_data: MarstekData, timestamp: float
    ) -> bool:
        """Parse timer info (0x13)."""
        if len(payload) < 45:
            return False

        device_data.adaptive_mode_enabled = payload[0] != 0
        device_data.smart_meter_connected = payload[37] != 0
        device_data.adaptive_power_out = float(struct.unpack("<H", payload[38:40])[0])

        for field in (
            "adaptive_mode_enabled",
            "smart_meter_connected",
            "adaptive_power_out",
        ):
            MarstekProtocol._track_field(
                device_data, field, 0x13, timestamp, payload
            )

        return True

    @staticmethod
    def _parse_bms_data(
        payload: bytes, device_data: MarstekData, timestamp: float
    ) -> bool:
        """Parse BMS data (0x14)."""
        if len(payload) < 80:
            return False

        # Parse BMS version
        device_data.bms_version = int(struct.unpack("<H", payload[0:2])[0])
        # Parse voltage and current limits
        device_data.voltage_limit = struct.unpack("<H", payload[2:4])[0] / 10.0
        device_data.charge_current_limit = struct.unpack("<H", payload[4:6])[0] / 10.0
        device_data.discharge_current_limit = struct.unpack("<h", payload[6:8])[0] / 10.0
        # Parse SOC, SOH, capacity
        device_data.battery_soc = float(struct.unpack("<H", payload[8:10])[0])
        device_data.battery_soh = float(struct.unpack("<H", payload[10:12])[0])
        device_data.design_capacity = float(struct.unpack("<H", payload[12:14])[0])
        # Parse voltage, current, temperature
        device_data.battery_voltage = struct.unpack("<H", payload[14:16])[0] / 100.0
        device_data.battery_current = struct.unpack("<h", payload[16:18])[0] / 10.0
        device_data.battery_temp = float(struct.unpack("<H", payload[18:20])[0])
        # Parse error and warning codes
        device_data.error_code = int(struct.unpack("<H", payload[26:28])[0])
        device_data.warning_code = int(struct.unpack("<I", payload[28:32])[0])
        # Parse runtime (convert from ms to hours)
        runtime_ms = struct.unpack("<I", payload[32:36])[0]
        device_data.runtime_hours = runtime_ms / 3600000.0
        # Parse MOSFET temperature
        device_data.mosfet_temp = float(struct.unpack("<H", payload[38:40])[0])
        # Parse temperature sensors 1-4
        device_data.temp_sensor_1 = float(struct.unpack("<H", payload[40:42])[0])
        device_data.temp_sensor_2 = float(struct.unpack("<H", payload[42:44])[0])
        device_data.temp_sensor_3 = float(struct.unpack("<H", payload[44:46])[0])
        device_data.temp_sensor_4 = float(struct.unpack("<H", payload[46:48])[0])

        # Parse cell voltages (16 cells starting at offset 48)
        for i in range(16):
            offset = 48 + i * 2
            if offset + 1 < len(payload):
                cell_voltage = struct.unpack("<H", payload[offset:offset + 2])[0] / 1000.0
                device_data.cell_voltages[i] = cell_voltage
                MarstekProtocol._track_field(
                    device_data, f"cell_{i + 1}_voltage", 0x14, timestamp, payload
                )

        for field in (
            "bms_version",
            "voltage_limit",
            "charge_current_limit",
            "discharge_current_limit",
            "battery_soc",
            "battery_soh",
            "design_capacity",
            "battery_voltage",
            "battery_current",
            "battery_temp",
            "error_code",
            "warning_code",
            "runtime_hours",
            "mosfet_temp",
            "temp_sensor_1",
            "temp_sensor_2",
            "temp_sensor_3",
            "temp_sensor_4",
        ):
            MarstekProtocol._track_field(
                device_data, field, 0x14, timestamp, payload
            )

        cells = [v for v in device_data.cell_voltages if v is not None]
        cell_min = min(cells) if cells else None
        cell_max = max(cells) if cells else None
        cell_avg = sum(cells) / len(cells) if cells else None
        _LOGGER.debug(
            "BMS parsed (cmd=0x14): V=%sV I=%sA SOC=%s%% SOH=%s%% cells(min/max/avg)=%s/%s/%s runtime=%sh",
            device_data.battery_voltage,
            device_data.battery_current,
            device_data.battery_soc,
            device_data.battery_soh,
            cell_min,
            cell_max,
            cell_avg,
            device_data.runtime_hours,
        )

        return True

    @staticmethod
    def _parse_config_data(
        payload: bytes, device_data: MarstekData, timestamp: float
    ) -> bool:
        """Parse config data (0x1A)."""
        if len(payload) < 17:
            return False

        device_data.config_mode = payload[0]
        device_data.config_status = struct.unpack("<b", payload[4:5])[0]
        device_data.config_value = payload[16]

        for field in ("config_mode", "config_status", "config_value"):
            MarstekProtocol._track_field(
                device_data, field, 0x1A, timestamp, payload
            )

        return True

    @staticmethod
    def _parse_meter_ip(
        payload: bytes, device_data: MarstekData, timestamp: float
    ) -> bool:
        """Parse meter IP (0x21)."""
        try:
            # Check if all 0xFF (not set)
            if all(b == 0xFF for b in payload):
                device_data.meter_ip = "(not set)"
            else:
                device_data.meter_ip = payload.decode("ascii", errors="ignore").strip("\x00")

            MarstekProtocol._track_field(
                device_data, "meter_ip", 0x21, timestamp, payload
            )

            return True
        except Exception:
            return False

    @staticmethod
    def _parse_ct_polling_rate(
        payload: bytes, device_data: MarstekData, timestamp: float
    ) -> bool:
        """Parse CT polling rate (0x22)."""
        if len(payload) < 1:
            return False

        device_data.ct_polling_rate = int(payload[0])
        MarstekProtocol._track_field(
            device_data, "ct_polling_rate", 0x22, timestamp, payload
        )
        return True

    @staticmethod
    def _parse_network_info(
        payload: bytes, device_data: MarstekData, timestamp: float
    ) -> bool:
        """Parse network info (0x24).

        Format: "ip:192.168.20.82,gate:192.168.20.1,mask:255.255.255.0,dns:192.168.20.1"
        """
        try:
            network_str = payload.decode("ascii", errors="ignore").strip()
            device_data.network_info = network_str

            # Parse individual fields from comma-delimited string
            if network_str:
                pairs = network_str.split(",")
                for pair in pairs:
                    if ":" not in pair:
                        continue

                    key, value = pair.split(":", 1)
                    key = key.strip()
                    value = value.strip()

                    if key == "ip":
                        device_data.ip_address = value
                    elif key in ("gate", "gateway"):
                        device_data.gateway = value
                    elif key == "mask":
                        device_data.subnet_mask = value
                    elif key == "dns":
                        device_data.dns_server = value

            for field in ("network_info", "ip_address", "gateway", "subnet_mask", "dns_server"):
                if getattr(device_data, field) is not None:
                    MarstekProtocol._track_field(
                        device_data, field, 0x24, timestamp, payload
                    )

            return True
        except Exception:
            return False

    @staticmethod
    def _parse_local_api_status(
        payload: bytes, device_data: MarstekData, timestamp: float
    ) -> bool:
        """Parse local API status (0x28)."""
        if len(payload) < 3:
            return False

        enabled = "enabled" if payload[0] == 1 else "disabled"
        port = struct.unpack("<H", payload[1:3])[0]
        device_data.local_api_status = f"{enabled}/{port}"

        MarstekProtocol._track_field(
            device_data, "local_api_status", 0x28, timestamp, payload
        )

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
        self._last_command_time: float | None = None
        self._last_command_error: str | None = None
        # Response waiting mechanism (like Venus Monitor)
        self._pending_command: int | None = None
        self._response_event: asyncio.Event | None = None
        self._response_data: bytes | None = None

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
                refreshed_device = self._ble_device_callback()
                if refreshed_device is not None:
                    self._ble_device = refreshed_device
                else:
                    _LOGGER.debug(
                        "%s: ble_device_callback returned None; reusing last known device %s",
                        self._device_name,
                        self._ble_device.address if self._ble_device else "unknown",
                    )

            if self._ble_device is None:
                raise BleakError(
                    f"{self._device_name}: No connectable BLE device available to establish connection"
                )

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
        _LOGGER.debug(
            "%s: Scheduled inactivity disconnect in 30s (last_command_age=%.1fs)",
            self._device_name,
            time.time() - self._last_command_time if self._last_command_time else -1,
        )

    async def _execute_disconnect(self) -> None:
        """Execute disconnection."""
        async with self._connect_lock:
            if self._client and self._client.is_connected:
                last_age = (
                    time.time() - self._last_command_time
                    if self._last_command_time
                    else None
                )
                _LOGGER.debug(
                    "%s: Disconnecting due to inactivity (last_command_age=%s)",
                    self._device_name,
                    f"{last_age:.1f}s" if last_age is not None else "unknown",
                )
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
                start_time = time.monotonic()
                wall_time = time.time()
                response_received = False
                try:
                    await self._ensure_connected()

                    # Setup response waiting (Venus Monitor pattern)
                    self._pending_command = cmd
                    self._response_event = asyncio.Event()
                    self._response_data = None

                    DEVICE_DEBUG.debug(
                        "%s: Sending command 0x%02X (attempt %d/%d)",
                        self._device_name,
                        cmd,
                        attempt + 1,
                        retry,
                    )

                    await self._client.write_gatt_char(CHAR_WRITE_UUID, command_data)
                    self._last_command_time = wall_time
                    VERBOSE_LOGGER.debug(
                        "%s TX (addr=%s handle=%s) cmd=0x%02X payload=%s",
                        self._device_name,
                        self.address,
                        CHAR_WRITE_UUID,
                        cmd,
                        payload.hex(),
                    )

                    self._reset_disconnect_timer()

                    # Wait for response (timeout 2000ms like Venus Monitor)
                    try:
                        await asyncio.wait_for(self._response_event.wait(), timeout=2.0)
                        response_received = True
                        _LOGGER.debug(
                            "%s: Command 0x%02X sent and response received",
                            self._device_name,
                            cmd
                        )
                    except asyncio.TimeoutError:
                        _LOGGER.warning(
                            "%s: Timeout waiting for response to command 0x%02X",
                            self._device_name,
                            cmd
                        )
                    finally:
                        # Clear waiting state
                        self._pending_command = None
                        self._response_event = None

                    duration = time.monotonic() - start_time
                    self._record_command_result(
                        cmd=cmd,
                        frame=command_data,
                        attempts=attempts_made,
                        success=response_received,
                        error=None if response_received else "no_response",
                    )
                    _LOGGER.debug(
                        "%s: Command 0x%02X %s in %.3fs after %d attempt(s)",
                        self._device_name,
                        cmd,
                        "succeeded" if response_received else "had no response",
                        duration,
                        attempts_made,
                    )
                    return response_received

                except (BleakError, TimeoutError) as ex:
                    last_error = str(ex)
                    duration = time.monotonic() - start_time
                    _LOGGER.warning(
                        "%s: Failed to send command 0x%02X (attempt %d/%d, %.3fs): %s",
                        self._device_name,
                        cmd,
                        attempt + 1,
                        retry,
                        duration,
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
                "response": "received" if success else "no_response" if error == "no_response" else "error",
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
            VERBOSE_LOGGER.debug(
                "%s RX (addr=%s sender=%s) cmd=0x%02X payload=%s parsed=%s",
                self._device_name,
                self.address,
                sender,
                command,
                payload.hex(),
                parsed,
            )
            stats = self._command_stats[command]
            stats["last_notification"] = timestamp
            stats["last_notification_hex"] = data.hex()

            # Signal response received if waiting (Venus Monitor pattern)
            if self._pending_command == command and self._response_event:
                self._response_data = data
                self._response_event.set()
        else:
            VERBOSE_LOGGER.debug(
                "%s RX (addr=%s sender=%s) cmd=unknown frame=%s parsed=%s",
                self._device_name,
                self.address,
                sender,
                data.hex(),
                parsed,
            )

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
