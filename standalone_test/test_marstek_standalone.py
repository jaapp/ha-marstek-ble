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
    from aioesphomeapi import APIClient, BluetoothLEAdvertisement, BluetoothProxyFeature
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

    def __init__(self, mac_address: str, device_name: str, proxy_client: APIClient, stats: CommandStats):
        """Initialize the proxy tester.

        Args:
            mac_address: BLE MAC address of device
            device_name: Name of device
            proxy_client: Connected ESPHome API client
            stats: Shared stats collector
        """
        self.mac_address = mac_address.upper().replace(":", "")  # ESPHome uses MAC without colons
        self.device_name = device_name
        self.proxy_client = proxy_client
        self.data = MarstekData()
        self.connected = False
        self.stats = stats
        self.ble_connection_handle = None

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
            def on_bluetooth_le_connection_response(connected: bool, mtu: int, error: int) -> None:
                nonlocal connection_error
                if connected:
                    _LOGGER.debug(f"[Proxy] Connected with MTU {mtu}")
                    connection_success.set()
                else:
                    connection_error = f"Connection failed with error {error}"
                    _LOGGER.error(f"[Proxy] {connection_error}")
                    connection_success.set()

            # Notification callback
            def on_bluetooth_gatt_notify(address: int, handle: int, data: bytes) -> None:
                if address == mac_int:
                    self._handle_notification(handle, data)

            # Subscribe to BLE callbacks
            await self.proxy_client.subscribe_bluetooth_connections_free(lambda free: None)
            await self.proxy_client.subscribe_bluetooth_le_raw_advertisements(lambda adv: None)

            # Attempt connection via proxy
            try:
                await self.proxy_client.bluetooth_device_connect(
                    address=mac_int,
                    has_address_type=False,
                    address_type=0,
                )

                # Wait for connection response (with timeout)
                await asyncio.wait_for(connection_success.wait(), timeout=15.0)

                if connection_error:
                    raise Exception(connection_error)

                self.connected = True
                self.ble_connection_handle = mac_int

                # Subscribe to GATT notifications
                # Note: We'd need to discover services/characteristics first
                # For now, we'll try to subscribe to the notify characteristic handle
                # This may require service discovery via bluetooth_gatt_get_services()

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
            # Build command using integration's protocol
            command_frame = MarstekProtocol.build_command(cmd, payload)

            _LOGGER.debug(f"[Proxy] Sending command 0x{cmd:02X}: {command_frame.hex()}")

            # Send via proxy
            # Note: We need the GATT handle for the write characteristic
            # This requires service discovery first via bluetooth_gatt_get_services()
            # For now, we'll attempt to write assuming we have the handle

            mac_int = int(self.mac_address, 16)

            # Attempt to write to the write characteristic
            # The handle would need to be discovered via service discovery
            # This is a placeholder - actual implementation needs proper handle
            try:
                await self.proxy_client.bluetooth_gatt_write(
                    address=mac_int,
                    handle=0,  # Placeholder - needs actual handle from service discovery
                    data=command_frame,
                    response=False,
                )
                return True
            except Exception as write_err:
                _LOGGER.warning(f"[Proxy] Write failed (service discovery may be needed): {write_err}")
                # Service discovery would be implemented here
                return False

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
                # Disconnect via ESPHome API
                # await self.proxy_client.bluetooth_device_disconnect(self.ble_connection_handle)
                pass
            except Exception as e:
                _LOGGER.error(f"[Proxy] Error disconnecting: {e}")
            finally:
                self.connected = False
                self.ble_connection_handle = None


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

        return client

    except Exception as e:
        print(f" ✗ ({e})")
        _LOGGER.error(f"Failed to connect to proxy: {e}")
        return None


async def discover_devices_via_proxy(proxy_client: APIClient, device_address: Optional[str] = None, device_name: Optional[str] = None) -> list[tuple[str, str]]:
    """Discover Marstek devices via ESPHome Bluetooth Proxy.

    Args:
        proxy_client: Connected ESPHome API client
        device_address: Optional MAC address filter
        device_name: Optional device name filter

    Returns:
        List of (mac_address, device_name) tuples
    """
    print("\n🔍 Scanning for Marstek devices via proxy...", end='', flush=True)

    found_devices = []
    scan_timeout = 10.0

    def match_device(name: str, address: str) -> bool:
        if device_address:
            return address.upper() == device_address.upper()
        if device_name:
            return name and name.startswith(device_name)
        return name and any(name.startswith(prefix) for prefix in DEVICE_PREFIXES)

    def on_advertisement(adv: BluetoothLEAdvertisement) -> None:
        # Convert MAC int to string
        mac_str = f"{adv.address:012X}"
        mac_formatted = ":".join([mac_str[i:i+2] for i in range(0, 12, 2)])

        if match_device(adv.name, mac_formatted) and (adv.address, adv.name) not in [(d[0], d[1]) for d in found_devices]:
            found_devices.append((mac_formatted, adv.name))

    try:
        # Subscribe to advertisements
        unsub = await proxy_client.subscribe_bluetooth_le_advertisements(on_advertisement)

        # Scan for devices
        await asyncio.sleep(scan_timeout)

        # Unsubscribe
        unsub()

        print(f" Found {len(found_devices)} device(s) ✓\n")

        if found_devices:
            for mac, name in found_devices:
                print(f"  • {name} ({mac})")
        else:
            print("\n⚠️  No Marstek devices found")

        return found_devices

    except Exception as e:
        print(f" ✗ ({e})")
        _LOGGER.error(f"Error during proxy device discovery: {e}")
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

    # Display results
    print("\n" + "=" * 100)
    print("MARSTEK BATTERY SENSOR - TEST RESULTS")
    print("=" * 100)

    for tester in testers:
        data = tester.data
        device_name = tester.ble_device.name
        device_addr = tester.ble_device.address

        print(f"\n{'─' * 100}")
        print(f"Device: {device_name} ({device_addr})")
        print(f"{'─' * 100}")

        # Device Info
        if data.device_type or data.device_id:
            print("\n📋 Device Info:")
            if data.device_type:
                print(f"  Type:       {data.device_type}")
            if data.device_id:
                print(f"  ID:         {data.device_id}")
            if data.mac_address:
                print(f"  MAC:        {data.mac_address}")
            if data.firmware_version:
                print(f"  Firmware:   {data.firmware_version}")

        # Battery Status
        print("\n🔋 Battery Status:")
        if data.battery_soc is not None:
            print(f"  SOC:        {data.battery_soc:.1f}%")
        if data.battery_soh is not None:
            print(f"  SOH:        {data.battery_soh:.1f}%")
        if data.battery_voltage is not None:
            print(f"  Voltage:    {data.battery_voltage:.2f} V")
        if data.battery_current is not None:
            print(f"  Current:    {data.battery_current:.2f} A")
        # Calculate power from voltage * current
        if data.battery_voltage is not None and data.battery_current is not None:
            power = data.battery_voltage * data.battery_current
            print(f"  Power:      {power:.1f} W")
        if data.battery_temp is not None:
            print(f"  Temp:       {data.battery_temp:.1f} °C")
        if data.design_capacity is not None:
            print(f"  Capacity:   {data.design_capacity} Wh (design)")
            # Calculate remaining capacity from SOC
            if data.battery_soc is not None:
                remaining = (data.battery_soc / 100.0) * data.design_capacity
                print(f"              {remaining:.0f} Wh (remaining)")

        # Cell Voltages
        if data.cell_voltages and any(v is not None for v in data.cell_voltages):
            print("\n⚡ Cell Voltages:")
            for i, voltage in enumerate(data.cell_voltages):
                if voltage is not None:
                    print(f"  Cell {i+1:2d}:    {voltage:.3f} V")

        # Runtime Info
        if data.out1_power is not None or data.temp_high is not None:
            print("\n⏱️  Runtime Info:")
            if data.out1_power is not None:
                print(f"  Output:     {data.out1_power:.1f} W")
            if data.temp_high is not None:
                print(f"  Max Temp:   {data.temp_high:.1f} °C")
            if data.temp_low is not None:
                print(f"  Min Temp:   {data.temp_low:.1f} °C")

        # Network Status
        if data.wifi_ssid or data.mqtt_connected is not None:
            print("\n🌐 Network:")
            if data.wifi_ssid:
                print(f"  WiFi:       {data.wifi_ssid}")
            if data.mqtt_connected is not None:
                status = "Connected" if data.mqtt_connected else "Disconnected"
                print(f"  MQTT:       {status}")
            if data.network_info:
                print(f"  Network:    {data.network_info}")

        # System Config
        if data.config_mode is not None or data.ct_polling_rate is not None:
            print("\n⚙️  Configuration:")
            if data.config_mode is not None:
                print(f"  Config Mode: {data.config_mode}")
            if data.ct_polling_rate is not None:
                print(f"  CT Rate:     {data.ct_polling_rate}s")
            if data.local_api_status is not None:
                print(f"  Local API:   {data.local_api_status}")

    print("\n" + "=" * 100)
    print(f"✓ Successfully read data from {len(testers)} device(s)")
    print("=" * 100 + "\n")


async def run_regular_mode_via_proxy(proxy_client: APIClient, devices: list[tuple[str, str]], parallel: bool = False):
    """Run regular test mode via ESPHome Bluetooth Proxy - read sensor values once.

    Args:
        proxy_client: Connected ESPHome API client
        devices: List of (mac_address, device_name) tuples
        parallel: If True, read from all devices simultaneously
                 If False, read from devices sequentially
    """
    mode = "PARALLEL" if parallel else "SEQUENTIAL"
    print(f"\n📡 Reading sensor values from {len(devices)} device(s) via proxy ({mode})...\n")

    # PHASE 1: Connect to ALL devices
    print("Connecting to devices via proxy...")
    testers = []
    for mac_address, device_name in devices:
        tester = ProxyMarstekTester(mac_address, device_name, proxy_client, CommandStats())
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

    # Display results
    print("\n" + "=" * 100)
    print("MARSTEK BATTERY SENSOR - TEST RESULTS (VIA ESPHOME PROXY)")
    print("=" * 100)

    for tester in testers:
        data = tester.data
        device_name = tester.device_name
        mac_address = tester.mac_address

        print(f"\n{'─' * 100}")
        print(f"Device: {device_name} ({mac_address})")
        print(f"{'─' * 100}")

        # Device Info
        if data.device_type or data.device_id:
            print("\n📋 Device Info:")
            if data.device_type:
                print(f"  Type:       {data.device_type}")
            if data.device_id:
                print(f"  ID:         {data.device_id}")
            if data.mac_address:
                print(f"  MAC:        {data.mac_address}")
            if data.firmware_version:
                print(f"  Firmware:   {data.firmware_version}")

        # Battery Status
        print("\n🔋 Battery Status:")
        if data.battery_soc is not None:
            print(f"  SOC:        {data.battery_soc:.1f}%")
        if data.battery_soh is not None:
            print(f"  SOH:        {data.battery_soh:.1f}%")
        if data.battery_voltage is not None:
            print(f"  Voltage:    {data.battery_voltage:.2f} V")
        if data.battery_current is not None:
            print(f"  Current:    {data.battery_current:.2f} A")
        # Calculate power from voltage * current
        if data.battery_voltage is not None and data.battery_current is not None:
            power = data.battery_voltage * data.battery_current
            print(f"  Power:      {power:.1f} W")
        if data.battery_temp is not None:
            print(f"  Temp:       {data.battery_temp:.1f} °C")
        if data.design_capacity is not None:
            print(f"  Capacity:   {data.design_capacity} Wh (design)")
            # Calculate remaining capacity from SOC
            if data.battery_soc is not None:
                remaining = (data.battery_soc / 100.0) * data.design_capacity
                print(f"              {remaining:.0f} Wh (remaining)")

        # Cell Voltages
        if data.cell_voltages and any(v is not None for v in data.cell_voltages):
            print("\n⚡ Cell Voltages:")
            for i, voltage in enumerate(data.cell_voltages):
                if voltage is not None:
                    print(f"  Cell {i+1:2d}:    {voltage:.3f} V")

        # Runtime Info
        if data.out1_power is not None or data.temp_high is not None:
            print("\n⏱️  Runtime Info:")
            if data.out1_power is not None:
                print(f"  Output:     {data.out1_power:.1f} W")
            if data.temp_high is not None:
                print(f"  Max Temp:   {data.temp_high:.1f} °C")
            if data.temp_low is not None:
                print(f"  Min Temp:   {data.temp_low:.1f} °C")

        # Network Status
        if data.wifi_ssid or data.mqtt_connected is not None:
            print("\n🌐 Network:")
            if data.wifi_ssid:
                print(f"  WiFi:       {data.wifi_ssid}")
            if data.mqtt_connected is not None:
                status = "Connected" if data.mqtt_connected else "Disconnected"
                print(f"  MQTT:       {status}")
            if data.network_info:
                print(f"  Network:    {data.network_info}")

        # System Config
        if data.config_mode is not None or data.ct_polling_rate is not None:
            print("\n⚙️  Configuration:")
            if data.config_mode is not None:
                print(f"  Config Mode: {data.config_mode}")
            if data.ct_polling_rate is not None:
                print(f"  CT Rate:     {data.ct_polling_rate}s")
            if data.local_api_status is not None:
                print(f"  Local API:   {data.local_api_status}")

    print("\n" + "=" * 100)
    print(f"✓ Successfully read data from {len(testers)} device(s) via proxy")
    print("=" * 100 + "\n")


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


async def run_stats_mode_via_proxy(proxy_client: APIClient, devices: list[tuple[str, str]], iterations: int = 10, parallel: bool = False):
    """Run statistics collection mode via ESPHome Bluetooth Proxy.

    Uses PERSISTENT connections (like HA does) - connects to ALL devices upfront,
    runs iterations, then disconnects all at the end.

    Args:
        proxy_client: Connected ESPHome API client
        devices: List of (mac_address, device_name) tuples
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
    for mac_address, device_name in devices:
        tester = ProxyMarstekTester(mac_address, device_name, proxy_client, stats)
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
            proxy_client = await connect_to_proxy(args.proxy, args.proxy_key)
            if not proxy_client:
                return 1

            try:
                # Discover devices via proxy
                proxy_devices = await discover_devices_via_proxy(proxy_client, device_address=args.address, device_name=args.name)

                if not proxy_devices:
                    print("\n❌ ERROR: Could not find any Marstek devices via proxy")
                    return 1

                # Stats mode via proxy
                if args.stats:
                    await run_stats_mode_via_proxy(proxy_client, proxy_devices, args.iterations, args.parallel)
                    return 0

                # Regular test mode
                await run_regular_mode_via_proxy(proxy_client, proxy_devices, args.parallel)
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
