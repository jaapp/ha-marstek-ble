#!/usr/bin/env python3
"""
Standalone test script for Marstek BLE battery sensor.
This script runs outside Home Assistant and reuses the integration's BLE protocol logic.

Usage:
    python test_marstek_standalone.py [--address AA:BB:CC:DD:EE:FF] [--name MST_ACCP_XXXX]

Requirements:
    pip install bleak bleak-retry-connector
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Optional

from bleak import BleakScanner
from bleak.backends.device import BLEDevice

try:
    from aioesphomeapi import (
        APIClient,
        BluetoothLEAdvertisement,
        BluetoothLERawAdvertisementsResponse,
        BluetoothProxyFeature,
        BluetoothScannerMode,
    )
    PROXY_AVAILABLE = True
except ImportError:
    PROXY_AVAILABLE = False

# Import the integration's BLE logic
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'custom_components', 'marstek_ble'))
from marstek_device import MarstekBLEDevice, MarstekData, MarstekProtocol

# Constants from the integration
DEVICE_PREFIXES = ("MST_ACCP_", "MST_VNSE3_")
CMD_RUNTIME_INFO = 0x03
CMD_DEVICE_INFO = 0x04
CMD_WIFI_SSID = 0x08
CMD_SYSTEM_DATA = 0x0D
CMD_TIMER_INFO = 0x13
CMD_BMS_DATA = 0x14
CMD_CONFIG_DATA = 0x1A
CMD_CT_POLLING_RATE = 0x22
CMD_METER_IP = 0x21
CMD_NETWORK_INFO = 0x24
CMD_LOCAL_API_STATUS = 0x28

# Command names for display
COMMAND_NAMES = {
    0x03: "Runtime Info",
    0x04: "Device Info",
    0x08: "WiFi SSID",
    0x0D: "System Data",
    0x13: "Timer Info",
    0x14: "BMS Data",
    0x1A: "Config Data",
    0x21: "Meter IP",
    0x22: "CT Polling",
    0x24: "Network Info",
    0x28: "Local API",
}

# Critical commands that need fastest updates (power monitoring)
CRITICAL_COMMANDS = [CMD_RUNTIME_INFO, CMD_BMS_DATA]

# Setup logging
logging.basicConfig(
    level=logging.WARNING,  # Default to WARNING to reduce noise
    format='%(message)s'
)
_LOGGER = logging.getLogger(__name__)


class CommandStats:
    """Track command response statistics."""

    def __init__(self):
        self.response_times = defaultdict(list)  # cmd -> list of response times (ms)
        self.failures = defaultdict(int)  # cmd -> count of no-response
        self.successes = defaultdict(int)  # cmd -> count of responses

    def record_response(self, cmd: int, response_time_ms: float):
        """Record a successful response."""
        self.response_times[cmd].append(response_time_ms)
        self.successes[cmd] += 1

    def record_failure(self, cmd: int):
        """Record a failed response (no notification)."""
        self.failures[cmd] += 1

    def get_percentile(self, cmd: int, percentile: float) -> Optional[float]:
        """Get percentile response time for a command."""
        times = self.response_times.get(cmd, [])
        if not times:
            return None
        return statistics.quantiles(times, n=100)[int(percentile) - 1] if len(times) > 1 else times[0]

    def get_stats(self, cmd: int) -> dict:
        """Get statistics for a command."""
        times = self.response_times.get(cmd, [])
        total = self.successes[cmd] + self.failures[cmd]
        success_rate = (self.successes[cmd] / total * 100) if total > 0 else 0

        if not times:
            return {
                "count": total,
                "success_rate": success_rate,
                "min": None,
                "max": None,
                "avg": None,
                "p50": None,
                "p95": None,
                "p99": None,
            }

        return {
            "count": total,
            "success_rate": success_rate,
            "min": min(times),
            "max": max(times),
            "avg": statistics.mean(times),
            "p50": self.get_percentile(cmd, 50),
            "p95": self.get_percentile(cmd, 95),
            "p99": self.get_percentile(cmd, 99),
        }


class MarstekTester:
    """Test harness for Marstek BLE device."""

    def __init__(self, ble_device: BLEDevice, stats: CommandStats):
        """Initialize the tester.

        Args:
            ble_device: The BLE device object
            stats: Shared stats collector
        """
        self.ble_device = ble_device
        self.marstek_device: Optional[MarstekBLEDevice] = None
        self.data = MarstekData()
        self.connected = False
        self.stats = stats

        # Track command timing and responses for this run
        self.command_responses = {}  # cmd -> bool (got response)
        self.command_start_times = {}  # cmd -> start timestamp
        self.command_response_times = {}  # cmd -> response time (when notification received)

    def _handle_notification(self, sender: int, data: bytearray) -> None:
        """Handle BLE notifications from the device."""
        raw_data = bytes(data)
        _LOGGER.debug(f"Received notification: {raw_data.hex()}")

        # Parse using the integration's protocol handler
        result = MarstekProtocol.parse_notification(raw_data, self.data)

        # Record notification (fixes the tracking bug!)
        if self.marstek_device:
            self.marstek_device.record_notification(sender, raw_data, result)

        # Track response time for this notification (for stats mode)
        # Extract command from raw data like integration does
        if result and len(raw_data) > 3:
            cmd = raw_data[3]  # Command is at byte 3
            if cmd in self.command_start_times:
                # Calculate response time
                response_time_ms = (time.time() - self.command_start_times[cmd]) * 1000
                self.command_response_times[cmd] = response_time_ms
                _LOGGER.debug(f"Command 0x{cmd:02X} responded in {response_time_ms:.0f}ms")

        if result:
            _LOGGER.debug("Notification parsed successfully")
        else:
            _LOGGER.debug("Failed to parse notification")

    def reset_iteration_tracking(self) -> None:
        """Reset tracking for a new iteration (keeps connection alive)."""
        self.command_responses = {}
        self.command_start_times = {}
        self.command_response_times = {}

    async def connect(self) -> bool:
        """Connect to the device.

        Returns:
            True if connected successfully, False otherwise
        """
        try:
            device_short_name = self.ble_device.name[:20] if self.ble_device.name else "Unknown"
            print(f"  • Connecting to {device_short_name}...", end='', flush=True)

            # Create Marstek device using the integration's BLE handler
            self.marstek_device = MarstekBLEDevice(
                ble_device=self.ble_device,
                device_name=self.ble_device.name or "Unknown",
                notification_callback=self._handle_notification
            )

            self.connected = True
            print(" ✓")
            return True

        except Exception as e:
            print(f" ✗ ({e})")
            _LOGGER.error(f"Failed to connect: {e}")
            return False

    async def read_all_data(self, fast_delay: float = 0.1, slow_delay: float = 0.3) -> bool:
        """Read all sensor data using integration's approach (for regular mode).

        This matches the integration's _send_and_sleep() logic exactly:
        - Send command via send_command()
        - Sleep for delay
        - No timing tracking

        Args:
            fast_delay: Delay for critical commands (default 0.1s, matches HA)
            slow_delay: Delay for other commands (default 0.3s, matches HA)

        Returns:
            True if successful, False otherwise
        """
        if not self.marstek_device:
            return False

        try:
            # Helper matching integration's _send_and_sleep()
            async def send_and_sleep(cmd: int, payload: bytes = b"", delay: float = 0.3):
                if not await self.marstek_device.send_command(cmd, payload):
                    _LOGGER.warning(f"Failed to send command 0x{cmd:02X}")
                if delay:
                    await asyncio.sleep(delay)

            # Send commands exactly like coordinator does
            await send_and_sleep(CMD_DEVICE_INFO, delay=slow_delay)
            await send_and_sleep(CMD_RUNTIME_INFO, delay=fast_delay)
            await send_and_sleep(CMD_BMS_DATA, delay=fast_delay)
            await send_and_sleep(CMD_SYSTEM_DATA, delay=slow_delay)
            await send_and_sleep(CMD_WIFI_SSID, delay=slow_delay)
            await send_and_sleep(CMD_CONFIG_DATA, delay=slow_delay)
            await send_and_sleep(CMD_TIMER_INFO, delay=slow_delay)
            await send_and_sleep(CMD_CT_POLLING_RATE, delay=slow_delay)
            await send_and_sleep(CMD_METER_IP, payload=b"\x0B", delay=slow_delay)
            await send_and_sleep(CMD_NETWORK_INFO, delay=slow_delay)
            await send_and_sleep(CMD_LOCAL_API_STATUS, delay=slow_delay)

            return True

        except Exception as e:
            _LOGGER.error(f"Error reading data: {e}")
            return False

    async def read_all_data_with_timing(self, fast_delay: float = 0.1, slow_delay: float = 0.3) -> bool:
        """Read all sensor data and track response times (for stats mode).

        Args:
            fast_delay: Delay for critical commands (default 0.1s, matches HA)
            slow_delay: Delay for other commands (default 0.3s, matches HA)

        Returns:
            True if successful, False otherwise
        """
        if not self.marstek_device:
            return False

        try:
            # Helper to send command and track timing
            async def send_and_time(cmd: int, payload: bytes = b"", delay: float = 0.3):
                # Record start time
                self.command_start_times[cmd] = time.time()

                # Send command
                success = await self.marstek_device.send_command(cmd, payload)

                # Wait for response window
                await asyncio.sleep(delay)

                return success

            # Send all commands with timing
            await send_and_time(CMD_DEVICE_INFO, delay=slow_delay)
            await send_and_time(CMD_RUNTIME_INFO, delay=fast_delay)
            await send_and_time(CMD_BMS_DATA, delay=fast_delay)
            await send_and_time(CMD_SYSTEM_DATA, delay=slow_delay)
            await send_and_time(CMD_WIFI_SSID, delay=slow_delay)
            await send_and_time(CMD_CONFIG_DATA, delay=slow_delay)
            await send_and_time(CMD_TIMER_INFO, delay=slow_delay)
            await send_and_time(CMD_CT_POLLING_RATE, delay=slow_delay)
            await send_and_time(CMD_METER_IP, payload=b"\x0B", delay=slow_delay)
            await send_and_time(CMD_NETWORK_INFO, delay=slow_delay)
            await send_and_time(CMD_LOCAL_API_STATUS, delay=slow_delay)

            return True

        except Exception as e:
            _LOGGER.error(f"Error reading data: {e}")
            return False

    def analyze_responses(self) -> None:
        """Analyze which commands got responses and record timing stats."""
        # Check each command we sent
        for cmd in [CMD_DEVICE_INFO, CMD_RUNTIME_INFO, CMD_BMS_DATA, CMD_SYSTEM_DATA,
                    CMD_WIFI_SSID, CMD_CONFIG_DATA, CMD_TIMER_INFO, CMD_CT_POLLING_RATE,
                    CMD_METER_IP, CMD_NETWORK_INFO, CMD_LOCAL_API_STATUS]:

            # Check if we got a response (notification arrived)
            if cmd in self.command_response_times:
                # We got a response - record the timing
                response_time_ms = self.command_response_times[cmd]
                self.stats.record_response(cmd, response_time_ms)
                self.command_responses[cmd] = True
            else:
                # No response received
                self.stats.record_failure(cmd)
                self.command_responses[cmd] = False

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        if self.marstek_device:
            await self.marstek_device.disconnect()
            self.connected = False


class ProxyMarstekTester:
    """Test harness for Marstek BLE device via ESPHome Bluetooth Proxy."""

    # Marstek BLE UUIDs (from integration)
    WRITE_CHAR_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
    NOTIFY_CHAR_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"

    def __init__(self, mac_address: str, device_name: str, address_type: int, proxy_client: APIClient, proxy_features: int, stats: CommandStats):
        """Initialize the proxy tester.

        Args:
            mac_address: BLE MAC address of device
            device_name: Name of device
            address_type: BLE address type (0=public, 1=random)
            proxy_client: Connected ESPHome API client
            proxy_features: Bluetooth proxy feature flags
            stats: Shared stats collector
        """
        self.mac_address = mac_address.upper().replace(":", "")  # ESPHome uses MAC without colons
        self.device_name = device_name
        self.address_type = address_type
        self.proxy_client = proxy_client
        self.proxy_features = proxy_features
        self.data = MarstekData()
        self.connected = False
        self.stats = stats
        self.ble_connection_handle = None

        # GATT handles (discovered during connection)
        self.write_handle = None
        self.notify_handle = None

        # Track command timing and responses
        self.command_responses = {}
        self.command_start_times = {}
        self.command_response_times = {}  # cmd -> response time (when notification received)

    def _handle_notification(self, handle: int, data: bytes) -> None:
        """Handle BLE notifications from the device via proxy."""
        _LOGGER.debug(f"[Proxy] Received notification: {data.hex()}")

        # Parse using the integration's protocol handler
        result = MarstekProtocol.parse_notification(data, self.data)

        # Track response time for this notification (for stats mode)
        # Extract command from raw data like integration does
        if result and len(data) > 3:
            cmd = data[3]  # Command is at byte 3
            if cmd in self.command_start_times:
                # Calculate response time
                response_time_ms = (time.time() - self.command_start_times[cmd]) * 1000
                self.command_response_times[cmd] = response_time_ms
                _LOGGER.debug(f"[Proxy] Command 0x{cmd:02X} responded in {response_time_ms:.0f}ms")

        if result:
            _LOGGER.debug("[Proxy] Notification parsed successfully")
        else:
            _LOGGER.debug("[Proxy] Failed to parse notification")

    def reset_iteration_tracking(self) -> None:
        """Reset tracking for a new iteration (keeps connection alive)."""
        self.command_responses = {}
        self.command_start_times = {}
        self.command_response_times = {}

    async def connect(self) -> bool:
        """Connect to the device via ESPHome proxy.

        Returns:
            True if connected successfully, False otherwise
        """
        try:
            device_short_name = self.device_name[:20]
            print(f"  • Connecting to {device_short_name} via proxy...", end='', flush=True)

            # Convert MAC address to int for ESPHome API (it uses MAC as uint64)
            mac_int = int(self.mac_address, 16)

            # Connection state
            connection_success = asyncio.Event()
            connection_error = None

            # Connection callback
            def on_bluetooth_connection_state(connected: bool, mtu: int, error: int) -> None:
                nonlocal connection_error
                if connected:
                    _LOGGER.debug(f"[Proxy] Connected with MTU {mtu}")
                    connection_success.set()
                else:
                    connection_error = f"Connection failed with error {error}"
                    _LOGGER.error(f"[Proxy] {connection_error}")
                    connection_success.set()

            # Notification callback
            def on_gatt_notify(handle: int, data: bytearray) -> None:
                self._handle_notification(handle, bytes(data))

            # Attempt connection via proxy (connection callback passed directly)
            try:
                await self.proxy_client.bluetooth_device_connect(
                    address=mac_int,
                    on_bluetooth_connection_state=on_bluetooth_connection_state,
                    feature_flags=self.proxy_features,
                    address_type=self.address_type,
                )

                # Wait for connection response (with timeout)
                await asyncio.wait_for(connection_success.wait(), timeout=15.0)

                if connection_error:
                    raise Exception(connection_error)

                self.connected = True
                self.ble_connection_handle = mac_int

                # Discover services to find GATT handles
                _LOGGER.debug(f"[Proxy] Discovering services...")
                services = await self.proxy_client.bluetooth_gatt_get_services(mac_int)

                # Find handles for write and notify characteristics
                for service in services.services:
                    for characteristic in service.characteristics:
                        # ESPHome returns UUID as integer, convert to standard UUID string
                        # Handle both 16-bit (0xff01) and 32-bit (0x0000ff01) formats
                        uuid_int = characteristic.uuid & 0xFFFFFFFF  # Ensure 32-bit
                        uuid_str = f"{uuid_int:08x}-0000-1000-8000-00805f9b34fb"

                        _LOGGER.debug(f"[Proxy] Found characteristic: UUID={uuid_str}, handle={characteristic.handle}")

                        if uuid_str == self.WRITE_CHAR_UUID:
                            self.write_handle = characteristic.handle
                            _LOGGER.debug(f"[Proxy] Found write characteristic at handle {self.write_handle}")
                        elif uuid_str == self.NOTIFY_CHAR_UUID:
                            self.notify_handle = characteristic.handle
                            _LOGGER.debug(f"[Proxy] Found notify characteristic at handle {self.notify_handle}")

                if not self.write_handle or not self.notify_handle:
                    raise Exception(f"Failed to find required characteristics (write={self.write_handle}, notify={self.notify_handle})")

                # Subscribe to GATT notifications on the notify characteristic
                _LOGGER.debug(f"[Proxy] Subscribing to notifications on handle {self.notify_handle}")
                await self.proxy_client.bluetooth_gatt_start_notify(
                    address=mac_int,
                    handle=self.notify_handle,
                    on_bluetooth_gatt_notify=on_gatt_notify,
                )

                print(" ✓")
                return True

            except asyncio.TimeoutError:
                raise Exception("Connection timeout")

        except Exception as e:
            print(f" ✗ ({e})")
            _LOGGER.error(f"[Proxy] Failed to connect: {e}")
            return False

    async def read_all_data(self, fast_delay: float = 0.1, slow_delay: float = 0.3) -> bool:
        """Read all sensor data using integration's approach (for regular mode via proxy).

        This matches the integration's _send_and_sleep() logic exactly:
        - Send command via send_command_via_proxy()
        - Sleep for delay
        - No timing tracking

        Args:
            fast_delay: Delay for critical commands (default 0.1s, matches HA)
            slow_delay: Delay for other commands (default 0.3s, matches HA)

        Returns:
            True if successful, False otherwise
        """
        if not self.connected:
            return False

        try:
            # Helper matching integration's _send_and_sleep()
            async def send_and_sleep(cmd: int, payload: bytes = b"", delay: float = 0.3):
                if not await self.send_command_via_proxy(cmd, payload):
                    _LOGGER.warning(f"[Proxy] Failed to send command 0x{cmd:02X}")
                if delay:
                    await asyncio.sleep(delay)

            # Send commands exactly like coordinator does
            await send_and_sleep(CMD_DEVICE_INFO, delay=slow_delay)
            await send_and_sleep(CMD_RUNTIME_INFO, delay=fast_delay)
            await send_and_sleep(CMD_BMS_DATA, delay=fast_delay)
            await send_and_sleep(CMD_SYSTEM_DATA, delay=slow_delay)
            await send_and_sleep(CMD_WIFI_SSID, delay=slow_delay)
            await send_and_sleep(CMD_CONFIG_DATA, delay=slow_delay)
            await send_and_sleep(CMD_TIMER_INFO, delay=slow_delay)
            await send_and_sleep(CMD_CT_POLLING_RATE, delay=slow_delay)
            await send_and_sleep(CMD_METER_IP, payload=b"\x0B", delay=slow_delay)
            await send_and_sleep(CMD_NETWORK_INFO, delay=slow_delay)
            await send_and_sleep(CMD_LOCAL_API_STATUS, delay=slow_delay)

            return True

        except Exception as e:
            _LOGGER.error(f"[Proxy] Error reading data: {e}")
            return False

    async def send_command_via_proxy(self, cmd: int, payload: bytes = b"") -> bool:
        """Send a command to the device via ESPHome proxy.

        Args:
            cmd: Command byte
            payload: Optional payload

        Returns:
            True if command sent successfully
        """
        try:
            if not self.write_handle:
                _LOGGER.error("[Proxy] Write handle not discovered yet")
                return False

            # Build command using integration's protocol
            command_frame = MarstekProtocol.build_command(cmd, payload)

            _LOGGER.debug(f"[Proxy] Sending command 0x{cmd:02X}: {command_frame.hex()}")

            # Send via proxy using discovered write handle
            mac_int = int(self.mac_address, 16)

            await self.proxy_client.bluetooth_gatt_write(
                address=mac_int,
                handle=self.write_handle,
                data=command_frame,
                response=False,
            )
            return True

        except Exception as e:
            _LOGGER.error(f"[Proxy] Error sending command 0x{cmd:02X}: {e}")
            return False

    async def read_all_data_with_timing(self, fast_delay: float = 0.1, slow_delay: float = 0.3) -> bool:
        """Read all sensor data via proxy and track response times.

        Args:
            fast_delay: Delay for critical commands
            slow_delay: Delay for other commands

        Returns:
            True if successful, False otherwise
        """
        if not self.connected:
            return False

        try:
            async def send_and_time(cmd: int, payload: bytes = b"", delay: float = 0.3):
                self.command_start_times[cmd] = time.time()
                success = await self.send_command_via_proxy(cmd, payload)
                await asyncio.sleep(delay)
                return success

            # Send all commands with timing
            await send_and_time(CMD_DEVICE_INFO, delay=slow_delay)
            await send_and_time(CMD_RUNTIME_INFO, delay=fast_delay)
            await send_and_time(CMD_BMS_DATA, delay=fast_delay)
            await send_and_time(CMD_SYSTEM_DATA, delay=slow_delay)
            await send_and_time(CMD_WIFI_SSID, delay=slow_delay)
            await send_and_time(CMD_CONFIG_DATA, delay=slow_delay)
            await send_and_time(CMD_TIMER_INFO, delay=slow_delay)
            await send_and_time(CMD_CT_POLLING_RATE, delay=slow_delay)
            await send_and_time(CMD_METER_IP, payload=b"\x0B", delay=slow_delay)
            await send_and_time(CMD_NETWORK_INFO, delay=slow_delay)
            await send_and_time(CMD_LOCAL_API_STATUS, delay=slow_delay)

            return True

        except Exception as e:
            _LOGGER.error(f"[Proxy] Error reading data: {e}")
            return False

    def analyze_responses(self) -> None:
        """Analyze which commands got responses and record timing stats."""
        # Check each command we sent
        for cmd in [CMD_DEVICE_INFO, CMD_RUNTIME_INFO, CMD_BMS_DATA, CMD_SYSTEM_DATA,
                    CMD_WIFI_SSID, CMD_CONFIG_DATA, CMD_TIMER_INFO, CMD_CT_POLLING_RATE,
                    CMD_METER_IP, CMD_NETWORK_INFO, CMD_LOCAL_API_STATUS]:

            # Check if we got a response (notification arrived)
            if cmd in self.command_response_times:
                # We got a response - record the timing
                response_time_ms = self.command_response_times[cmd]
                self.stats.record_response(cmd, response_time_ms)
                self.command_responses[cmd] = True
            else:
                # No response received
                self.stats.record_failure(cmd)
                self.command_responses[cmd] = False

    async def disconnect(self) -> None:
        """Disconnect from the device via proxy."""
        if self.connected and self.ble_connection_handle:
            try:
                # Stop notifications first
                if self.notify_handle:
                    _LOGGER.debug(f"[Proxy] Stopping notifications on handle {self.notify_handle}")
                    await self.proxy_client.bluetooth_gatt_stop_notify(self.ble_connection_handle, self.notify_handle)

                # Disconnect via ESPHome API
                _LOGGER.debug(f"[Proxy] Disconnecting device")
                await self.proxy_client.bluetooth_device_disconnect(self.ble_connection_handle)
            except Exception as e:
                _LOGGER.error(f"[Proxy] Error disconnecting: {e}")
            finally:
                self.connected = False
                self.ble_connection_handle = None
                self.write_handle = None
                self.notify_handle = None


def print_stats_table(stats: CommandStats, iterations: int):
    """Print statistics table."""
    print("\n" + "=" * 120)
    print(f"COMMAND RESPONSE STATISTICS ({iterations} iterations)")
    print("=" * 120)
    print()

    # Table header
    print(f"{'Command':<20} {'Success':<10} {'Min':<10} {'Avg':<10} {'P50':<10} {'P95':<10} {'P99':<10} {'Max':<10} {'Recommend':<15}")
    print("─" * 120)

    # Sort by command type (critical first)
    commands = sorted(COMMAND_NAMES.keys(), key=lambda c: (c not in CRITICAL_COMMANDS, c))

    for cmd in commands:
        name = COMMAND_NAMES[cmd]
        cmd_stats = stats.get_stats(cmd)

        # Format values
        success_rate = f"{cmd_stats['success_rate']:.1f}%"
        min_val = f"{cmd_stats['min']:.0f}ms" if cmd_stats['min'] else "-"
        avg_val = f"{cmd_stats['avg']:.0f}ms" if cmd_stats['avg'] else "-"
        p50_val = f"{cmd_stats['p50']:.0f}ms" if cmd_stats['p50'] else "-"
        p95_val = f"{cmd_stats['p95']:.0f}ms" if cmd_stats['p95'] else "-"
        p99_val = f"{cmd_stats['p99']:.0f}ms" if cmd_stats['p99'] else "-"
        max_val = f"{cmd_stats['max']:.0f}ms" if cmd_stats['max'] else "-"

        # Recommend delay based on p95
        if cmd_stats['p95']:
            if cmd_stats['p95'] < 150:
                recommend = "0.1s (Fast) ⚡"
            elif cmd_stats['p95'] < 250:
                recommend = "0.2s (Medium)"
            elif cmd_stats['p95'] < 350:
                recommend = "0.3s (Current)"
            else:
                recommend = f"0.{int(cmd_stats['p95']/100)}s (Slow)"
        else:
            recommend = "NO RESPONSE"

        # Mark critical commands
        marker = " ⚡" if cmd in CRITICAL_COMMANDS else ""
        display_name = f"{name} (0x{cmd:02X}){marker}"

        print(f"{display_name:<20} {success_rate:<10} {min_val:<10} {avg_val:<10} {p50_val:<10} {p95_val:<10} {p99_val:<10} {max_val:<10} {recommend:<15}")

    print("─" * 120)
    print("\n⚡ = Critical command (power monitoring) - needs fastest updates")
    print("\nRecommendations based on P95 (95th percentile):")
    print("  • < 150ms → Use 0.1s delay (aggressive, real-time)")
    print("  • < 250ms → Use 0.2s delay (balanced)")
    print("  • < 350ms → Use 0.3s delay (conservative, current HA)")
    print("  • > 350ms → Use 0.4s+ delay (very slow device)")
    print()
    print("NOTE: These timings are for DIRECT BLE.")
    print("ESPHome Bluetooth Proxy adds ~50-200ms latency!")
    print("Add extra margin for proxy: Fast→0.2s, Medium→0.3s, Current→0.4s")
    print("=" * 120)


async def connect_to_proxy(proxy_host: str, proxy_key: str) -> Optional[APIClient]:
    """Connect to ESPHome Bluetooth Proxy.

    Args:
        proxy_host: IP address or hostname of the ESPHome device
        proxy_key: API encryption key

    Returns:
        Connected APIClient or None if connection failed
    """
    if not PROXY_AVAILABLE:
        print("\n❌ ERROR: aioesphomeapi not installed. Install with: pip install aioesphomeapi")
        return None

    try:
        print(f"\n🔌 Connecting to ESPHome proxy at {proxy_host}...", end='', flush=True)

        client = APIClient(
            address=proxy_host,
            port=6053,
            password=None,
            noise_psk=proxy_key,
        )

        await client.connect(login=True)

        # Check if proxy supports Bluetooth
        device_info = await client.device_info()
        print(f" ✓\n  • Device: {device_info.name}")
        print(f"  • Version: {device_info.esphome_version}")

        # Store feature flags for later use
        _LOGGER.debug(f"[Proxy] Bluetooth proxy features: {device_info.bluetooth_proxy_feature_flags}")

        return client, device_info.bluetooth_proxy_feature_flags

    except Exception as e:
        print(f" ✗ ({e})")
        _LOGGER.error(f"Failed to connect to proxy: {e}")
        return None


async def discover_devices_via_proxy(proxy_client: APIClient, device_address: Optional[str] = None, device_name: Optional[str] = None) -> list[tuple[str, str, int]]:
    """Discover Marstek devices via ESPHome Bluetooth Proxy.

    Args:
        proxy_client: Connected ESPHome API client
        device_address: Optional MAC address filter
        device_name: Optional device name filter

    Returns:
        List of (mac_address, device_name, address_type) tuples
    """
    print("\n🔍 Scanning for Marstek devices via proxy...", end='', flush=True)

    found_devices = []
    warned_devices = set()  # Track devices we've already warned about
    total_advertisements = 0
    scan_timeout = 10.0

    def match_device(name: str, address: str) -> bool:
        if device_address:
            return address.upper() == device_address.upper()
        if device_name:
            return name and name.startswith(device_name)
        return name and any(name.startswith(prefix) for prefix in DEVICE_PREFIXES)

    def parse_name_from_adv_data(data: bytes) -> Optional[str]:
        """Parse device name from BLE advertisement data."""
        # BLE advertisement format: [len][type][data]...
        # Type 0x08 = Shortened Local Name, 0x09 = Complete Local Name
        i = 0
        while i < len(data):
            if i + 1 >= len(data):
                break
            length = data[i]
            if length == 0 or i + length >= len(data):
                break
            ad_type = data[i + 1]
            if ad_type in (0x08, 0x09):  # Local name
                try:
                    name = data[i + 2:i + 1 + length].decode('utf-8')
                    return name
                except:
                    pass
            i += 1 + length
        return None

    def on_raw_advertisements(resp: BluetoothLERawAdvertisementsResponse) -> None:
        nonlocal total_advertisements

        for adv in resp.advertisements:
            total_advertisements += 1

            # Convert MAC int to string
            mac_str = f"{adv.address:012X}"
            mac_formatted = ":".join([mac_str[i:i+2] for i in range(0, 12, 2)])

            # Parse name from advertisement data
            name = parse_name_from_adv_data(adv.data)

            # Get address type (0=public, 1=random)
            address_type = getattr(adv, 'address_type', 0)  # Default to 0 (public) if not present

            _LOGGER.debug(f"[Proxy] Advertisement: name={name}, mac={mac_formatted}, address_type={address_type}, rssi={adv.rssi}")

            if match_device(name, mac_formatted):
                # Check if we already found this device (by MAC only, ignore name/address_type changes)
                if not any(d[0] == mac_formatted for d in found_devices):
                    _LOGGER.info(f"[Proxy] Found matching device: {name} ({mac_formatted}) address_type={address_type}")
                    found_devices.append((mac_formatted, name, address_type))
            elif name and "MST" in name.upper():
                # Log any Marstek-looking devices even if they don't match our filter (only once per device)
                if mac_formatted not in warned_devices:
                    _LOGGER.warning(f"[Proxy] Found Marstek-like device but didn't match filter: {name} ({mac_formatted})")
                    warned_devices.add(mac_formatted)

    try:
        # Set scanner to ACTIVE mode to receive advertisements
        _LOGGER.debug("[Proxy] Setting scanner to ACTIVE mode...")
        proxy_client.bluetooth_scanner_set_mode(BluetoothScannerMode.ACTIVE)

        # Subscribe to raw advertisements
        _LOGGER.debug("[Proxy] Subscribing to raw BLE advertisements...")
        unsub = proxy_client.subscribe_bluetooth_le_raw_advertisements(on_raw_advertisements)
        _LOGGER.debug("[Proxy] Subscription active, waiting for advertisements...")

        # Scan for devices
        await asyncio.sleep(scan_timeout)

        # Unsubscribe and restore scanner to PASSIVE mode
        _LOGGER.debug(f"[Proxy] Scan complete. Received {total_advertisements} total advertisements, found {len(found_devices)} matching devices. Unsubscribing...")
        unsub()

        _LOGGER.debug("[Proxy] Restoring scanner to PASSIVE mode...")
        proxy_client.bluetooth_scanner_set_mode(BluetoothScannerMode.PASSIVE)

        print(f" Found {len(found_devices)} device(s) ✓\n")

        if found_devices:
            for mac, name, addr_type in found_devices:
                print(f"  • {name} ({mac})")
        else:
            print("\n⚠️  No Marstek devices found")
            if total_advertisements == 0:
                print("⚠️  WARNING: No BLE advertisements received at all!")
                print("   This may indicate:")
                print("   - ESPHome proxy is not scanning/forwarding advertisements")
                print("   - Network connectivity issues")
                print("   - ESPHome bluetooth_proxy component not properly configured")
                print(f"\n   Try running with --debug flag to see detailed logging")
            else:
                print(f"   Received {total_advertisements} advertisements but none matched Marstek devices")
                print(f"   Looking for devices starting with: {', '.join(DEVICE_PREFIXES)}")
                print(f"\n   Try running with --debug flag to see all advertisements")

        return found_devices

    except Exception as e:
        print(f" ✗ ({e})")
        _LOGGER.error(f"Error during proxy device discovery: {e}")
        # Restore scanner to PASSIVE mode even on error
        try:
            proxy_client.bluetooth_scanner_set_mode(BluetoothScannerMode.PASSIVE)
        except:
            pass
        return []


async def discover_devices(device_address: Optional[str] = None, device_name: Optional[str] = None) -> list[BLEDevice]:
    """Discover Marstek devices via BLE scanning."""
    print("\n🔍 Scanning for Marstek devices...", end='', flush=True)

    def match_device(device: BLEDevice) -> bool:
        if device_address:
            return device.address.upper() == device_address.upper()
        if device_name:
            return device.name and device.name.startswith(device_name)
        return device.name and any(device.name.startswith(prefix) for prefix in DEVICE_PREFIXES)

    try:
        devices = await BleakScanner.discover(timeout=10.0, return_adv=True)
        found_devices = [device for device, _ in devices.values() if match_device(device)]

        print(f" Found {len(found_devices)} device(s) ✓\n")

        if found_devices:
            for device in found_devices:
                print(f"  • {device.name} ({device.address})")
        else:
            print("\n⚠️  No Marstek devices found")

        return found_devices

    except Exception as e:
        print(f" ✗ ({e})")
        _LOGGER.error(f"Error during device discovery: {e}")
        return []


async def run_regular_mode(devices: list[BLEDevice], parallel: bool = False):
    """Run regular test mode - read sensor values once and display.

    Uses PERSISTENT connections - connects to ALL devices upfront,
    reads data, then disconnects.

    Args:
        devices: List of BLE devices to test
        parallel: If True, read from all devices simultaneously
                 If False, read from devices sequentially
    """
    mode = "PARALLEL" if parallel else "SEQUENTIAL"
    print(f"\n📡 Reading sensor values from {len(devices)} device(s) ({mode})...\n")

    # PHASE 1: Connect to ALL devices
    print("Connecting to devices...")
    testers = []
    for device in devices:
        tester = MarstekTester(device, CommandStats())  # Stats not used in regular mode
        if await tester.connect():
            testers.append(tester)
        else:
            print(f"⚠️  Failed to connect to {device.name}, skipping...")

    if not testers:
        print("❌ No devices connected successfully")
        return

    print(f"✓ Connected to {len(testers)} device(s)\n")

    try:
        # PHASE 2: Read data
        print("Reading sensor data...\n")

        if parallel:
            # Parallel mode: Read from all devices simultaneously
            async def read_device(tester):
                await tester.read_all_data()  # Uses integration's approach
                await asyncio.sleep(1.0)  # Settling time

            await asyncio.gather(*[read_device(t) for t in testers])
        else:
            # Sequential mode: Read from devices one at a time
            for tester in testers:
                device_name = tester.ble_device.name[:20]
                print(f"  • Reading {device_name}...", end='', flush=True)
                await tester.read_all_data()  # Uses integration's approach
                await asyncio.sleep(1.0)  # Settling time
                print(" ✓")

    finally:
        # PHASE 3: Disconnect
        print("\nDisconnecting...")
        for tester in testers:
            await tester.disconnect()

    # Display results in TABULAR format (devices as columns, values as rows)
    print("\n" + "=" * 120)
    print("MARSTEK BATTERY SENSOR - TEST RESULTS")
    print("=" * 120)

    # Determine column widths
    device_names = [t.ble_device.name for t in testers]
    max_name_width = max(len(name) for name in device_names)
    col_width = max(18, min(max_name_width + 2, 40))

    # Header row
    header = f"{'Metric':<30}"
    for name in device_names:
        header += f"{name:<{col_width}}"
    print("\n" + header)
    print("─" * (30 + col_width * len(testers)))

    # Helper to format row
    def print_row(label, values, unit=""):
        row = f"{label:<30}"
        for val in values:
            if val is not None:
                formatted = f"{val}{unit}"
                row += f"{formatted:<{col_width}}"
            else:
                row += f"{'-':<{col_width}}"
        print(row)

    # Device Info section
    print("\n📋 Device Info:")
    print_row("  Type", [t.data.device_type for t in testers])
    print_row("  Firmware", [t.data.firmware_version for t in testers])

    # Battery Status section
    print("\n🔋 Battery Status:")
    print_row("  SOC", [f"{t.data.battery_soc:.1f}%" if t.data.battery_soc is not None else None for t in testers])
    print_row("  SOH", [f"{t.data.battery_soh:.1f}%" if t.data.battery_soh is not None else None for t in testers])
    print_row("  Voltage", [f"{t.data.battery_voltage:.2f}V" if t.data.battery_voltage is not None else None for t in testers])
    print_row("  Current", [f"{t.data.battery_current:.2f}A" if t.data.battery_current is not None else None for t in testers])

    # Calculate and show power
    powers = []
    for t in testers:
        if t.data.battery_voltage is not None and t.data.battery_current is not None:
            powers.append(f"{t.data.battery_voltage * t.data.battery_current:.1f}W")
        else:
            powers.append(None)
    print_row("  Power", powers)

    print_row("  Temperature", [f"{t.data.battery_temp:.1f}°C" if t.data.battery_temp is not None else None for t in testers])
    print_row("  Design Capacity", [f"{t.data.design_capacity}Wh" if t.data.design_capacity is not None else None for t in testers])

    # Calculate and show remaining capacity
    remaining = []
    for t in testers:
        if t.data.battery_soc is not None and t.data.design_capacity is not None:
            rem = (t.data.battery_soc / 100.0) * t.data.design_capacity
            remaining.append(f"{rem:.0f}Wh")
        else:
            remaining.append(None)
    print_row("  Remaining Capacity", remaining)

    # Runtime Info section
    print("\n⏱️  Runtime Info:")
    print_row("  Output Power", [f"{t.data.out1_power:.1f}W" if t.data.out1_power is not None else None for t in testers])
    print_row("  Max Temp", [f"{t.data.temp_high:.1f}°C" if t.data.temp_high is not None else None for t in testers])
    print_row("  Min Temp", [f"{t.data.temp_low:.1f}°C" if t.data.temp_low is not None else None for t in testers])

    # Network Status section
    print("\n🌐 Network:")
    print_row("  WiFi SSID", [t.data.wifi_ssid for t in testers])
    print_row("  MQTT", ["Connected" if t.data.mqtt_connected else "Disconnected" if t.data.mqtt_connected is not None else None for t in testers])

    # Configuration section
    print("\n⚙️  Configuration:")
    print_row("  Config Mode", [str(t.data.config_mode) if t.data.config_mode is not None else None for t in testers])
    print_row("  CT Polling Rate", [f"{t.data.ct_polling_rate}s" if t.data.ct_polling_rate is not None else None for t in testers])
    print_row("  Local API", [t.data.local_api_status for t in testers])

    # Cell voltages section (show first 8 cells if any)
    any_cell_voltages = any(t.data.cell_voltages and any(v is not None for v in t.data.cell_voltages) for t in testers)
    if any_cell_voltages:
        print("\n⚡ Cell Voltages:")
        for i in range(8):  # Show first 8 cells
            cell_values = []
            for t in testers:
                if t.data.cell_voltages and len(t.data.cell_voltages) > i and t.data.cell_voltages[i] is not None:
                    cell_values.append(f"{t.data.cell_voltages[i]:.3f}V")
                else:
                    cell_values.append(None)
            if any(v is not None for v in cell_values):
                print_row(f"  Cell {i+1}", cell_values)

    print("\n" + "=" * 120)
    print(f"✓ Successfully read data from {len(testers)} device(s)")
    print("=" * 120 + "\n")


async def run_regular_mode_via_proxy(proxy_client: APIClient, proxy_features: int, devices: list[tuple[str, str, int]], parallel: bool = False):
    """Run regular test mode via ESPHome Bluetooth Proxy - read sensor values once.

    Args:
        proxy_client: Connected ESPHome API client
        proxy_features: Bluetooth proxy feature flags
        devices: List of (mac_address, device_name, address_type) tuples
        parallel: If True, read from all devices simultaneously
                 If False, read from devices sequentially
    """
    mode = "PARALLEL" if parallel else "SEQUENTIAL"
    print(f"\n📡 Reading sensor values from {len(devices)} device(s) via proxy ({mode})...\n")

    # PHASE 1: Connect to ALL devices
    print("Connecting to devices via proxy...")
    testers = []
    for mac_address, device_name, address_type in devices:
        tester = ProxyMarstekTester(mac_address, device_name, address_type, proxy_client, proxy_features, CommandStats())
        if await tester.connect():
            testers.append(tester)
        else:
            print(f"⚠️  Failed to connect to {device_name}, skipping...")

    if not testers:
        print("❌ No devices connected successfully")
        return

    print(f"✓ Connected to {len(testers)} device(s)\n")

    try:
        # PHASE 2: Read data
        print("Reading sensor data...\n")

        if parallel:
            # Parallel mode: Read from all devices simultaneously
            async def read_device(tester):
                await tester.read_all_data()  # Uses integration's approach
                await asyncio.sleep(1.0)  # Settling time

            await asyncio.gather(*[read_device(t) for t in testers])
        else:
            # Sequential mode: Read from devices one at a time
            for tester in testers:
                device_name = tester.device_name[:20]
                print(f"  • Reading {device_name}...", end='', flush=True)
                await tester.read_all_data()  # Uses integration's approach
                await asyncio.sleep(1.0)  # Settling time
                print(" ✓")

    finally:
        # PHASE 3: Disconnect
        print("\nDisconnecting...")
        for tester in testers:
            await tester.disconnect()

    # Display results in TABULAR format (devices as columns, values as rows)
    print("\n" + "=" * 120)
    print("MARSTEK BATTERY SENSOR - TEST RESULTS (VIA ESPHOME PROXY)")
    print("=" * 120)

    # Determine column widths
    device_names = [t.device_name for t in testers]
    max_name_width = max(len(name) for name in device_names)
    col_width = max(18, min(max_name_width + 2, 40))

    # Header row
    header = f"{'Metric':<30}"
    for name in device_names:
        header += f"{name:<{col_width}}"
    print("\n" + header)
    print("─" * (30 + col_width * len(testers)))

    # Helper to format row
    def print_row(label, values, unit=""):
        row = f"{label:<30}"
        for val in values:
            if val is not None:
                formatted = f"{val}{unit}"
                row += f"{formatted:<{col_width}}"
            else:
                row += f"{'-':<{col_width}}"
        print(row)

    # Device Info section
    print("\n📋 Device Info:")
    print_row("  Type", [t.data.device_type for t in testers])
    print_row("  Firmware", [t.data.firmware_version for t in testers])

    # Battery Status section
    print("\n🔋 Battery Status:")
    print_row("  SOC", [f"{t.data.battery_soc:.1f}%" if t.data.battery_soc is not None else None for t in testers])
    print_row("  SOH", [f"{t.data.battery_soh:.1f}%" if t.data.battery_soh is not None else None for t in testers])
    print_row("  Voltage", [f"{t.data.battery_voltage:.2f}V" if t.data.battery_voltage is not None else None for t in testers])
    print_row("  Current", [f"{t.data.battery_current:.2f}A" if t.data.battery_current is not None else None for t in testers])

    # Calculate and show power
    powers = []
    for t in testers:
        if t.data.battery_voltage is not None and t.data.battery_current is not None:
            powers.append(f"{t.data.battery_voltage * t.data.battery_current:.1f}W")
        else:
            powers.append(None)
    print_row("  Power", powers)

    print_row("  Temperature", [f"{t.data.battery_temp:.1f}°C" if t.data.battery_temp is not None else None for t in testers])
    print_row("  Design Capacity", [f"{t.data.design_capacity}Wh" if t.data.design_capacity is not None else None for t in testers])

    # Calculate and show remaining capacity
    remaining = []
    for t in testers:
        if t.data.battery_soc is not None and t.data.design_capacity is not None:
            rem = (t.data.battery_soc / 100.0) * t.data.design_capacity
            remaining.append(f"{rem:.0f}Wh")
        else:
            remaining.append(None)
    print_row("  Remaining Capacity", remaining)

    # Runtime Info section
    print("\n⏱️  Runtime Info:")
    print_row("  Output Power", [f"{t.data.out1_power:.1f}W" if t.data.out1_power is not None else None for t in testers])
    print_row("  Max Temp", [f"{t.data.temp_high:.1f}°C" if t.data.temp_high is not None else None for t in testers])
    print_row("  Min Temp", [f"{t.data.temp_low:.1f}°C" if t.data.temp_low is not None else None for t in testers])

    # Network Status section
    print("\n🌐 Network:")
    print_row("  WiFi SSID", [t.data.wifi_ssid for t in testers])
    print_row("  MQTT", ["Connected" if t.data.mqtt_connected else "Disconnected" if t.data.mqtt_connected is not None else None for t in testers])

    # Configuration section
    print("\n⚙️  Configuration:")
    print_row("  Config Mode", [str(t.data.config_mode) if t.data.config_mode is not None else None for t in testers])
    print_row("  CT Polling Rate", [f"{t.data.ct_polling_rate}s" if t.data.ct_polling_rate is not None else None for t in testers])
    print_row("  Local API", [t.data.local_api_status for t in testers])

    # Cell voltages section (show first 8 cells if any)
    any_cell_voltages = any(t.data.cell_voltages and any(v is not None for v in t.data.cell_voltages) for t in testers)
    if any_cell_voltages:
        print("\n⚡ Cell Voltages:")
        for i in range(8):  # Show first 8 cells
            cell_values = []
            for t in testers:
                if t.data.cell_voltages and len(t.data.cell_voltages) > i and t.data.cell_voltages[i] is not None:
                    cell_values.append(f"{t.data.cell_voltages[i]:.3f}V")
                else:
                    cell_values.append(None)
            if any(v is not None for v in cell_values):
                print_row(f"  Cell {i+1}", cell_values)

    print("\n" + "=" * 120)
    print(f"✓ Successfully read data from {len(testers)} device(s) via proxy")
    print("=" * 120 + "\n")


async def run_stats_mode(devices: list[BLEDevice], iterations: int = 10, parallel: bool = False):
    """Run statistics collection mode with local BLE.

    Uses PERSISTENT connections (like HA does) - connects to ALL devices upfront,
    runs iterations, then disconnects all at the end.

    Args:
        devices: List of BLE devices to test
        iterations: Number of test iterations
        parallel: If True, send commands to all devices simultaneously (BLE contention risk)
                 If False, send commands sequentially (one device at a time)
    """
    mode = "PARALLEL" if parallel else "SEQUENTIAL"
    print(f"\n📊 STATS MODE: Running {iterations} iterations ({mode}, direct BLE)")
    print(f"Connection Management: PERSISTENT (connect all devices once)")
    print(f"Command Execution: {mode} {'(may have BLE contention!)' if parallel else '(no contention)'}")
    print(f"This will take ~{iterations * 5} seconds...\n")

    stats = CommandStats()

    # PHASE 1: Connect to ALL devices
    print("Phase 1: Connecting to all devices...")
    testers = []
    for device in devices:
        tester = MarstekTester(device, stats)
        if await tester.connect():
            testers.append(tester)
        else:
            print(f"⚠️  Failed to connect to {device.name}, skipping...")

    if not testers:
        print("❌ No devices connected successfully")
        return

    print(f"✓ Connected to {len(testers)} device(s)\n")

    try:
        # PHASE 2: Run iterations (sequential or parallel)
        print(f"Phase 2: Running {iterations} iterations ({mode} commands)...")

        for i in range(iterations):
            print(f"\n[Iteration {i+1}/{iterations}]")

            if parallel:
                # Parallel mode: Send commands to all devices simultaneously
                async def test_device(tester):
                    tester.reset_iteration_tracking()
                    await tester.read_all_data_with_timing()
                    await asyncio.sleep(1.0)  # Settling time
                    tester.analyze_responses()

                # Run all devices in parallel
                await asyncio.gather(*[test_device(t) for t in testers])
                print("  ✓ All devices completed")

            else:
                # Sequential mode: Send commands to devices one at a time
                for tester in testers:
                    device_name = tester.ble_device.name[:20]
                    print(f"  • {device_name}...", end='', flush=True)

                    tester.reset_iteration_tracking()
                    await tester.read_all_data_with_timing()
                    await asyncio.sleep(1.0)  # Settling time
                    tester.analyze_responses()

                    print(" ✓")

            # Brief pause between iterations
            await asyncio.sleep(0.5)

    finally:
        # PHASE 3: Disconnect all devices
        print("\nPhase 3: Disconnecting all devices...")
        for tester in testers:
            await tester.disconnect()
            print(f"  • Disconnected {tester.ble_device.name}")

    # Print statistics
    print()
    print_stats_table(stats, iterations * len(testers))


async def run_stats_mode_via_proxy(proxy_client: APIClient, proxy_features: int, devices: list[tuple[str, str, int]], iterations: int = 10, parallel: bool = False):
    """Run statistics collection mode via ESPHome Bluetooth Proxy.

    Uses PERSISTENT connections (like HA does) - connects to ALL devices upfront,
    runs iterations, then disconnects all at the end.

    Args:
        proxy_client: Connected ESPHome API client
        proxy_features: Bluetooth proxy feature flags
        devices: List of (mac_address, device_name, address_type) tuples
        iterations: Number of test iterations
        parallel: If True, send commands to all devices simultaneously (BLE contention risk)
                 If False, send commands sequentially (one device at a time)
    """
    mode = "PARALLEL" if parallel else "SEQUENTIAL"
    print(f"\n📊 STATS MODE: Running {iterations} iterations ({mode}, via ESPHome proxy)")
    print(f"Connection Management: PERSISTENT (connect all devices once)")
    print(f"Command Execution: {mode} {'(may have BLE contention!)' if parallel else '(no contention)'}")
    print(f"⚠️  NOTE: Proxy adds ~50-200ms latency to all operations!")
    print(f"This will take ~{iterations * 5} seconds...\n")

    stats = CommandStats()

    # PHASE 1: Connect to ALL devices
    print("Phase 1: Connecting to all devices via proxy...")
    testers = []
    for mac_address, device_name, address_type in devices:
        tester = ProxyMarstekTester(mac_address, device_name, address_type, proxy_client, proxy_features, stats)
        if await tester.connect():
            testers.append(tester)
        else:
            print(f"⚠️  Failed to connect to {device_name}, skipping...")

    if not testers:
        print("❌ No devices connected successfully")
        return

    print(f"✓ Connected to {len(testers)} device(s)\n")

    try:
        # PHASE 2: Run iterations (sequential or parallel)
        print(f"Phase 2: Running {iterations} iterations ({mode} commands)...")

        for i in range(iterations):
            print(f"\n[Iteration {i+1}/{iterations}]")

            if parallel:
                # Parallel mode: Send commands to all devices simultaneously
                async def test_device(tester):
                    tester.reset_iteration_tracking()
                    await tester.read_all_data_with_timing()
                    await asyncio.sleep(1.0)  # Settling time
                    tester.analyze_responses()

                # Run all devices in parallel
                await asyncio.gather(*[test_device(t) for t in testers])
                print("  ✓ All devices completed")

            else:
                # Sequential mode: Send commands to devices one at a time
                for tester in testers:
                    device_name = tester.device_name[:20]
                    print(f"  • {device_name}...", end='', flush=True)

                    tester.reset_iteration_tracking()
                    await tester.read_all_data_with_timing()
                    await asyncio.sleep(1.0)  # Settling time
                    tester.analyze_responses()

                    print(" ✓")

            # Brief pause between iterations
            await asyncio.sleep(0.5)

    finally:
        # PHASE 3: Disconnect all devices
        print("\nPhase 3: Disconnecting all devices...")
        for tester in testers:
            await tester.disconnect()
            print(f"  • Disconnected {tester.device_name}")

    # Print statistics (with proxy note)
    print()
    print("\n" + "=" * 120)
    print(f"COMMAND RESPONSE STATISTICS ({iterations * len(testers)} total samples) - VIA ESPHOME PROXY")
    print("=" * 120)
    print_stats_table(stats, iterations * len(testers))


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Test Marstek BLE battery sensor outside Home Assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test devices SEQUENTIALLY (default - no BLE contention)
  python3 test_marstek_standalone.py --stats

  # Test devices in PARALLEL (commands to all devices at once - may have contention)
  python3 test_marstek_standalone.py --stats --parallel

  # Test via ESPHOME BLUETOOTH PROXY
  python3 test_marstek_standalone.py --stats \\
    --proxy 192.168.7.44 \\
    --proxy-key "istH+Pnjbxgury0LoTU4UBzqchEbp70upkgwQHb9bBQ="

  # More iterations for better statistics
  python3 test_marstek_standalone.py --stats --iterations 20

  # Connect to specific device
  python3 test_marstek_standalone.py --address AA:BB:CC:DD:EE:FF --stats

Connection Management:
  - ALWAYS maintains persistent connections to ALL devices (like HA does)
  - Connects to all devices upfront, disconnects at the end
  - Never reconnects between iterations

Sequential vs Parallel (--parallel flag):
  - SEQUENTIAL (default): Send commands to Device 1, wait, then Device 2, etc.
    → No BLE radio contention, more reliable
  - PARALLEL: Send commands to all devices simultaneously
    → May have BLE contention, tests HA's current behavior

PROXY MODE:
  Use --proxy to test via ESPHome Bluetooth Proxy.
  Simulates real Home Assistant behavior including proxy latency (~50-200ms).
        """
    )
    parser.add_argument(
        "--address",
        help="BLE MAC address of the device"
    )
    parser.add_argument(
        "--name",
        help="Device name or prefix"
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Send commands to all devices simultaneously (may have BLE contention). Default is sequential (one device at a time)."
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Run statistics mode (multiple iterations to measure response times)"
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=10,
        help="Number of iterations for stats mode (default: 10)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )
    parser.add_argument(
        "--proxy",
        help="ESPHome Bluetooth Proxy IP address (e.g., 192.168.7.44)"
    )
    parser.add_argument(
        "--proxy-key",
        help="ESPHome API encryption key (base64 encoded)"
    )
    args = parser.parse_args()

    # Validate proxy arguments
    if args.proxy and not args.proxy_key:
        print("❌ ERROR: --proxy-key is required when using --proxy")
        return 1
    if args.proxy_key and not args.proxy:
        print("❌ ERROR: --proxy is required when using --proxy-key")
        return 1

    # Set log level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger("bleak").setLevel(logging.DEBUG)
        logging.getLogger("marstek_device").setLevel(logging.DEBUG)
    else:
        logging.getLogger("marstek_device").setLevel(logging.ERROR)

    try:
        # Proxy mode
        if args.proxy:
            result = await connect_to_proxy(args.proxy, args.proxy_key)
            if not result:
                return 1

            proxy_client, proxy_features = result

            try:
                # Discover devices via proxy
                proxy_devices = await discover_devices_via_proxy(proxy_client, device_address=args.address, device_name=args.name)

                if not proxy_devices:
                    print("\n❌ ERROR: Could not find any Marstek devices via proxy")
                    return 1

                # Stats mode via proxy
                if args.stats:
                    await run_stats_mode_via_proxy(proxy_client, proxy_features, proxy_devices, args.iterations, args.parallel)
                    return 0

                # Regular test mode
                await run_regular_mode_via_proxy(proxy_client, proxy_features, proxy_devices, args.parallel)
                return 0

            finally:
                # Disconnect from proxy
                await proxy_client.disconnect()

        # Local BLE mode
        else:
            # Discover devices
            devices = await discover_devices(device_address=args.address, device_name=args.name)

            if not devices:
                print("\n❌ ERROR: Could not find any Marstek devices")
                return 1

            # Stats mode
            if args.stats:
                await run_stats_mode(devices, args.iterations, args.parallel)
                return 0

            # Regular test mode
            await run_regular_mode(devices, args.parallel)
            return 0

    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        return 130
    except Exception as e:
        _LOGGER.exception("Unexpected error")
        print(f"\n❌ ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
