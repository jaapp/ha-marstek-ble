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

    def _handle_notification(self, sender: int, data: bytearray) -> None:
        """Handle BLE notifications from the device."""
        raw_data = bytes(data)
        _LOGGER.debug(f"Received notification: {raw_data.hex()}")

        # Parse using the integration's protocol handler
        result = MarstekProtocol.parse_notification(raw_data, self.data)

        # Record notification (fixes the tracking bug!)
        if self.marstek_device:
            self.marstek_device.record_notification(sender, raw_data, result)

        if result:
            _LOGGER.debug("Notification parsed successfully")
        else:
            _LOGGER.debug("Failed to parse notification")

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

    async def read_all_data_with_timing(self, fast_delay: float = 0.1, slow_delay: float = 0.3) -> bool:
        """Read all sensor data and track response times.

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
        if not self.marstek_device:
            return

        diag = self.marstek_device.get_diagnostics()
        cmd_stats_dict = diag.get("command_stats", {})

        # Check each command
        for cmd in [CMD_DEVICE_INFO, CMD_RUNTIME_INFO, CMD_BMS_DATA, CMD_SYSTEM_DATA,
                    CMD_WIFI_SSID, CMD_CONFIG_DATA, CMD_TIMER_INFO, CMD_CT_POLLING_RATE,
                    CMD_METER_IP, CMD_NETWORK_INFO, CMD_LOCAL_API_STATUS]:

            cmd_hex = f"0x{cmd:02X}"
            cmd_stat = cmd_stats_dict.get(cmd_hex, {})

            # Check if we got notification
            last_notification_time = cmd_stat.get("last_notification")
            got_response = last_notification_time is not None

            self.command_responses[cmd] = got_response

            # Calculate response time if we got one
            if got_response and cmd in self.command_start_times:
                # Parse ISO timestamp
                try:
                    from datetime import datetime as dt
                    notif_time = dt.fromisoformat(last_notification_time.replace('Z', '+00:00'))
                    start_time = self.command_start_times[cmd]
                    response_time_ms = (notif_time.timestamp() - start_time) * 1000
                    self.stats.record_response(cmd, response_time_ms)
                except Exception as e:
                    _LOGGER.debug(f"Error calculating response time for 0x{cmd:02X}: {e}")
                    self.stats.record_failure(cmd)
            else:
                self.stats.record_failure(cmd)

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        if self.marstek_device:
            await self.marstek_device.disconnect()
            self.connected = False


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


async def run_stats_mode(devices: list[BLEDevice], iterations: int = 10):
    """Run statistics collection mode."""
    print(f"\n📊 STATS MODE: Running {iterations} iterations (sequential)")
    print(f"This will take ~{iterations * 5} seconds...\n")

    stats = CommandStats()

    for i in range(iterations):
        print(f"\n[Iteration {i+1}/{iterations}]")

        # Test each device sequentially
        for device in devices:
            tester = MarstekTester(device, stats)

            if await tester.connect():
                await tester.read_all_data_with_timing()

                # Settling time for late notifications
                await asyncio.sleep(1.0)

                # Analyze responses
                tester.analyze_responses()

                await tester.disconnect()

                # Brief pause between devices
                await asyncio.sleep(0.5)

    # Print statistics
    print_stats_table(stats, iterations)


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Test Marstek BLE battery sensor outside Home Assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test devices sequentially (DEFAULT - most reliable)
  python3 test_marstek_standalone.py

  # Test devices in parallel (like HA does - may have contention)
  python3 test_marstek_standalone.py --parallel

  # Run statistics mode (10 iterations, measures response times)
  python3 test_marstek_standalone.py --stats

  # Run more iterations for better statistics
  python3 test_marstek_standalone.py --stats --iterations 20

  # Connect to specific device
  python3 test_marstek_standalone.py --address AA:BB:CC:DD:EE:FF

Note: Sequential mode is DEFAULT because it's more reliable.
Parallel mode may have BLE contention issues with multiple devices.

IMPORTANT: If using ESPHome Bluetooth Proxy, add 50-200ms to all timings!
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
        help="Test devices in parallel (may have BLE contention)"
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
    args = parser.parse_args()

    # Set log level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger("bleak").setLevel(logging.DEBUG)
        logging.getLogger("marstek_device").setLevel(logging.DEBUG)
    else:
        logging.getLogger("marstek_device").setLevel(logging.ERROR)

    try:
        # Discover devices
        devices = await discover_devices(device_address=args.address, device_name=args.name)

        if not devices:
            print("\n❌ ERROR: Could not find any Marstek devices")
            return 1

        # Stats mode
        if args.stats:
            await run_stats_mode(devices, args.iterations)
            return 0

        # Regular test mode
        mode = "in parallel" if args.parallel else "sequentially"
        print(f"\n📡 Testing {len(devices)} device(s) {mode}...\n")

        # For now, just print message that regular mode will be implemented
        print("Regular test mode output to be implemented...")
        print("Use --stats mode to measure response times.")

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
