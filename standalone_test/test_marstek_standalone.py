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

# Setup logging
logging.basicConfig(
    level=logging.WARNING,  # Default to WARNING to reduce noise
    format='%(message)s'
)
_LOGGER = logging.getLogger(__name__)


class MarstekTester:
    """Test harness for Marstek BLE device."""

    def __init__(self, ble_device: BLEDevice):
        """Initialize the tester.

        Args:
            ble_device: The BLE device object
        """
        self.ble_device = ble_device
        self.marstek_device: Optional[MarstekBLEDevice] = None
        self.data = MarstekData()
        self.connected = False

    def _handle_notification(self, sender: int, data: bytearray) -> None:
        """Handle BLE notifications from the device."""
        raw_data = bytes(data)
        _LOGGER.debug(f"Received notification: {raw_data.hex()}")

        # Parse using the integration's protocol handler
        result = MarstekProtocol.parse_notification(raw_data, self.data)

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
            print(f"  • Connecting to {self.ble_device.name}...", end='', flush=True)

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

    async def read_all_data(self) -> bool:
        """Read all sensor data from the device.

        Returns:
            True if successful, False otherwise
        """
        if not self.marstek_device:
            print(f"  • {self.ble_device.name}: Not connected ✗")
            return False

        try:
            device_short_name = self.ble_device.name[:20] if self.ble_device.name else "Unknown"

            # Read basic device info
            print(f"  • {device_short_name}: Device info...", end='', flush=True)
            await self.marstek_device.send_command(CMD_DEVICE_INFO)
            await asyncio.sleep(0.3)
            print(" ✓")

            # Read runtime info
            print(f"  • {device_short_name}: Runtime info...", end='', flush=True)
            await self.marstek_device.send_command(CMD_RUNTIME_INFO)
            await asyncio.sleep(0.3)
            print(" ✓")

            # Read BMS data (battery info)
            print(f"  • {device_short_name}: BMS data...", end='', flush=True)
            await self.marstek_device.send_command(CMD_BMS_DATA)
            await asyncio.sleep(0.3)
            print(" ✓")

            # Read system data
            print(f"  • {device_short_name}: System data...", end='', flush=True)
            await self.marstek_device.send_command(CMD_SYSTEM_DATA)
            await asyncio.sleep(0.3)
            print(" ✓")

            # Read WiFi SSID
            print(f"  • {device_short_name}: WiFi SSID...", end='', flush=True)
            await self.marstek_device.send_command(CMD_WIFI_SSID)
            await asyncio.sleep(0.3)
            print(" ✓")

            # Read config data
            print(f"  • {device_short_name}: Config data...", end='', flush=True)
            await self.marstek_device.send_command(CMD_CONFIG_DATA)
            await asyncio.sleep(0.3)
            print(" ✓")

            # Read timer info
            print(f"  • {device_short_name}: Timer info...", end='', flush=True)
            await self.marstek_device.send_command(CMD_TIMER_INFO)
            await asyncio.sleep(0.3)
            print(" ✓")

            # Read CT polling rate
            print(f"  • {device_short_name}: CT polling...", end='', flush=True)
            await self.marstek_device.send_command(CMD_CT_POLLING_RATE)
            await asyncio.sleep(0.3)
            print(" ✓")

            # Read meter IP
            print(f"  • {device_short_name}: Meter IP...", end='', flush=True)
            await self.marstek_device.send_command(CMD_METER_IP, b"\x0B")
            await asyncio.sleep(0.3)
            print(" ✓")

            # Read network info
            print(f"  • {device_short_name}: Network info...", end='', flush=True)
            await self.marstek_device.send_command(CMD_NETWORK_INFO)
            await asyncio.sleep(0.3)
            print(" ✓")

            # Read local API status
            print(f"  • {device_short_name}: Local API...", end='', flush=True)
            await self.marstek_device.send_command(CMD_LOCAL_API_STATUS)
            await asyncio.sleep(0.3)
            print(" ✓")

            return True

        except Exception as e:
            print(f" ✗")
            _LOGGER.error(f"Error reading data: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        if self.marstek_device:
            await self.marstek_device.disconnect()
            self.connected = False


def format_value(value: any, unit: str = "", decimals: int = 1) -> str:
    """Format a value for display in the table."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        if decimals == 0:
            return f"{value:.0f}{unit}"
        elif decimals == 1:
            return f"{value:.1f}{unit}"
        elif decimals == 2:
            return f"{value:.2f}{unit}"
        elif decimals == 3:
            return f"{value:.3f}{unit}"
    return str(value)


def print_table(testers: list[MarstekTester]) -> None:
    """Print results in tabular format with devices as columns."""
    if not testers:
        return

    print("\n" + "=" * 120)
    print("MARSTEK BATTERY SENSOR - TEST RESULTS")
    print("=" * 120)
    print(f"Timestamp: {datetime.now().isoformat()}\n")

    # Prepare column headers (device names)
    device_names = []
    for tester in testers:
        name = tester.ble_device.name if tester.ble_device.name else tester.ble_device.address
        device_names.append(name[:15])  # Truncate long names

    # Column widths
    label_width = 30
    col_width = 18

    # Helper function to print a row
    def print_row(label: str, values: list[str], separator: str = "│"):
        label_part = label.ljust(label_width)
        cols = separator.join(v.rjust(col_width) for v in values)
        print(f"{label_part} {separator} {cols}")

    def print_separator():
        print("─" * label_width + "─┼─" + "─┼─".join("─" * col_width for _ in testers))

    # Header
    print_row("", device_names)
    print_separator()

    # Connection Info
    addresses = [t.ble_device.address for t in testers]
    print_row("BLE Address", addresses)

    connected_status = [format_value(t.connected) for t in testers]
    print_row("Connected", connected_status)
    print_separator()

    # Device Info
    print("\n--- Device Information ---")
    print_separator()
    device_types = [format_value(t.data.device_type) for t in testers]
    print_row("Device Type", device_types)

    device_ids = [format_value(t.data.device_id) for t in testers]
    print_row("Device ID", device_ids)

    firmware = [format_value(t.data.firmware_version) for t in testers]
    print_row("Firmware", firmware)
    print_separator()

    # Battery Status
    print("\n--- Battery Status ---")
    print_separator()
    soc = [format_value(t.data.battery_soc, "%", 1) for t in testers]
    print_row("State of Charge (SOC)", soc)

    soh = [format_value(t.data.battery_soh, "%", 1) for t in testers]
    print_row("State of Health (SOH)", soh)

    voltage = [format_value(t.data.battery_voltage, " V", 2) for t in testers]
    print_row("Voltage", voltage)

    current = [format_value(t.data.battery_current, " A", 2) for t in testers]
    print_row("Current", current)

    # Calculate power
    power_values = []
    for t in testers:
        if t.data.battery_voltage is not None and t.data.battery_current is not None:
            power = t.data.battery_voltage * t.data.battery_current
            power_values.append(format_value(power, " W", 1))
        else:
            power_values.append("-")
    print_row("Power", power_values)

    temp = [format_value(t.data.battery_temp, " °C", 1) for t in testers]
    print_row("Temperature", temp)

    capacity = [format_value(t.data.design_capacity, " Wh", 0) for t in testers]
    print_row("Design Capacity", capacity)

    # Calculate remaining capacity
    remaining_values = []
    for t in testers:
        if t.data.design_capacity is not None and t.data.battery_soc is not None:
            remaining = t.data.design_capacity * t.data.battery_soc / 100
            remaining_values.append(format_value(remaining, " Wh", 0))
        else:
            remaining_values.append("-")
    print_row("Remaining Capacity", remaining_values)
    print_separator()

    # Cell Voltages (only show cells that have data)
    any_cell_data = any(
        any(v is not None and v > 0 for v in t.data.cell_voltages)
        for t in testers
    )

    if any_cell_data:
        print("\n--- Cell Voltages ---")
        print_separator()
        for i in range(16):
            # Check if any device has data for this cell
            has_data = any(
                t.data.cell_voltages[i] is not None and t.data.cell_voltages[i] > 0
                for t in testers
            )
            if has_data:
                cell_values = [format_value(t.data.cell_voltages[i], " V", 3) for t in testers]
                print_row(f"Cell {i+1:2d}", cell_values)
        print_separator()

    # Runtime Info
    print("\n--- Runtime Info ---")
    print_separator()
    out_power = [format_value(t.data.out1_power, " W", 1) for t in testers]
    print_row("Output 1 Power", out_power)

    out_active = [format_value(t.data.out1_active) for t in testers]
    print_row("Output 1 Active", out_active)

    temp_low = [format_value(t.data.temp_low, " °C", 1) for t in testers]
    print_row("Temperature Low", temp_low)

    temp_high = [format_value(t.data.temp_high, " °C", 1) for t in testers]
    print_row("Temperature High", temp_high)

    extern = [format_value(t.data.extern1_connected) for t in testers]
    print_row("External Connected", extern)
    print_separator()

    # Network Status
    print("\n--- Network Status ---")
    print_separator()
    wifi = [format_value(t.data.wifi_connected) for t in testers]
    print_row("WiFi Connected", wifi)

    ssid = [format_value(t.data.wifi_ssid) for t in testers]
    print_row("WiFi SSID", ssid)

    mqtt = [format_value(t.data.mqtt_connected) for t in testers]
    print_row("MQTT Connected", mqtt)
    print_separator()

    # Adaptive Mode
    print("\n--- Adaptive Mode ---")
    print_separator()
    adaptive = [format_value(t.data.adaptive_mode_enabled) for t in testers]
    print_row("Adaptive Mode", adaptive)

    smart_meter = [format_value(t.data.smart_meter_connected) for t in testers]
    print_row("Smart Meter", smart_meter)

    adaptive_power = [format_value(t.data.adaptive_power_out, " W", 1) for t in testers]
    print_row("Adaptive Power Out", adaptive_power)
    print_separator()

    # BLE Diagnostics
    print("\n--- BLE Diagnostics ---")
    print_separator()

    cmd_sent = []
    success_rate = []
    for t in testers:
        if t.marstek_device:
            diag = t.marstek_device.get_diagnostics()
            overall = diag.get("overall", {})
            cmd_sent.append(str(overall.get('total_sent', 0)))
            success_rate.append(f"{overall.get('success_rate', 0):.1f}%")
        else:
            cmd_sent.append("-")
            success_rate.append("-")

    print_row("Commands Sent", cmd_sent)
    print_row("Success Rate", success_rate)
    print_separator()

    print("\n" + "=" * 120)


async def discover_devices(device_address: Optional[str] = None, device_name: Optional[str] = None) -> list[BLEDevice]:
    """Discover Marstek devices via BLE scanning.

    Args:
        device_address: Specific BLE MAC address to find (optional)
        device_name: Device name prefix to find (optional)

    Returns:
        List of discovered BLE devices
    """
    print("\n🔍 Scanning for Marstek devices...", end='', flush=True)

    def match_device(device: BLEDevice) -> bool:
        """Check if device matches our criteria."""
        if device_address:
            return device.address.upper() == device_address.upper()
        if device_name:
            return device.name and device.name.startswith(device_name)
        # Default: match any Marstek device
        return device.name and any(device.name.startswith(prefix) for prefix in DEVICE_PREFIXES)

    try:
        # Scan for 10 seconds
        devices = await BleakScanner.discover(timeout=10.0, return_adv=True)

        found_devices = []
        for device, adv_data in devices.values():
            if match_device(device):
                found_devices.append(device)

        print(f" Found {len(found_devices)} device(s) ✓\n")

        if found_devices:
            for device in found_devices:
                print(f"  • {device.name} ({device.address})")
        else:
            print("\n⚠️  No Marstek devices found")
            if not device_address and not device_name:
                print("\nAvailable BLE devices:")
                for device, adv_data in devices.values():
                    if device.name:
                        print(f"  • {device.name} ({device.address})")

        return found_devices

    except Exception as e:
        print(f" ✗ ({e})")
        _LOGGER.error(f"Error during device discovery: {e}")
        return []


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Test Marstek BLE battery sensor outside Home Assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-discover and test all Marstek devices
  python3 test_marstek_standalone.py

  # Connect to specific device by address
  python3 test_marstek_standalone.py --address AA:BB:CC:DD:EE:FF

  # Connect to device by name prefix
  python3 test_marstek_standalone.py --name MST_ACCP_1234

  # Enable debug logging
  python3 test_marstek_standalone.py --debug
        """
    )
    parser.add_argument(
        "--address",
        help="BLE MAC address of the device (e.g., AA:BB:CC:DD:EE:FF)"
    )
    parser.add_argument(
        "--name",
        help="Device name or prefix (e.g., MST_ACCP_1234)"
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

    try:
        # Discover devices
        devices = await discover_devices(device_address=args.address, device_name=args.name)

        if not devices:
            print("\n❌ ERROR: Could not find any Marstek devices")
            print("\nMake sure:")
            print("  1. The device is powered on and within range")
            print("  2. Bluetooth is enabled on your Mac")
            print("  3. The device is not connected to Home Assistant or other apps")
            return 1

        print(f"\n📡 Connecting to {len(devices)} device(s)...\n")

        # Create testers for all devices
        testers = [MarstekTester(device) for device in devices]

        # Connect to all devices
        connect_tasks = [tester.connect() for tester in testers]
        connect_results = await asyncio.gather(*connect_tasks, return_exceptions=True)

        # Filter out failed connections
        connected_testers = [
            tester for tester, result in zip(testers, connect_results)
            if result is True
        ]

        if not connected_testers:
            print("\n❌ ERROR: Could not connect to any devices")
            return 1

        print(f"\n📊 Reading data from {len(connected_testers)} device(s)...\n")

        # Read data from all connected devices
        read_tasks = [tester.read_all_data() for tester in connected_testers]
        await asyncio.gather(*read_tasks, return_exceptions=True)

        # Print results in table format
        print_table(connected_testers)

        return 0

    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        return 130
    except Exception as e:
        _LOGGER.exception("Unexpected error")
        print(f"\n❌ ERROR: {e}")
        return 1
    finally:
        # Disconnect from all devices
        if 'testers' in locals():
            print("\n🔌 Disconnecting from devices...")
            disconnect_tasks = [tester.disconnect() for tester in testers]
            await asyncio.gather(*disconnect_tasks, return_exceptions=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
