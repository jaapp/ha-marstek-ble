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
sys.path.insert(0, 'custom_components/marstek_ble')
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
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
_LOGGER = logging.getLogger(__name__)


class MarstekTester:
    """Test harness for Marstek BLE device."""

    def __init__(self, device_address: Optional[str] = None, device_name: Optional[str] = None):
        """Initialize the tester.

        Args:
            device_address: BLE MAC address (optional, will scan if not provided)
            device_name: Device name to search for (optional)
        """
        self.device_address = device_address
        self.device_name = device_name
        self.ble_device: Optional[BLEDevice] = None
        self.marstek_device: Optional[MarstekBLEDevice] = None
        self.data = MarstekData()

    async def discover_device(self) -> bool:
        """Discover the Marstek device via BLE scanning.

        Returns:
            True if device found, False otherwise
        """
        _LOGGER.info("Scanning for Marstek devices...")

        def match_device(device: BLEDevice, adv_data) -> bool:
            """Check if device matches our criteria."""
            if self.device_address:
                return device.address.upper() == self.device_address.upper()
            if self.device_name:
                return device.name and device.name.startswith(self.device_name)
            # Default: match any Marstek device
            return device.name and any(device.name.startswith(prefix) for prefix in DEVICE_PREFIXES)

        try:
            # Scan for 10 seconds
            devices = await BleakScanner.discover(timeout=10.0, return_adv=True)

            for device, adv_data in devices.values():
                if match_device(device, adv_data):
                    self.ble_device = device
                    _LOGGER.info(f"Found Marstek device: {device.name} ({device.address})")
                    _LOGGER.info(f"  RSSI: {adv_data.rssi} dBm")
                    return True

            _LOGGER.error("No Marstek device found")
            if not self.device_address and not self.device_name:
                _LOGGER.info("Available devices:")
                for device, adv_data in devices.values():
                    if device.name:
                        _LOGGER.info(f"  - {device.name} ({device.address})")
            return False

        except Exception as e:
            _LOGGER.error(f"Error during device discovery: {e}")
            return False

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
        if not self.ble_device:
            _LOGGER.error("No BLE device available. Run discover_device() first.")
            return False

        try:
            _LOGGER.info(f"Connecting to {self.ble_device.name}...")

            # Create Marstek device using the integration's BLE handler
            self.marstek_device = MarstekBLEDevice(
                ble_device=self.ble_device,
                device_name=self.ble_device.name or "Unknown",
                notification_callback=self._handle_notification
            )

            _LOGGER.info("Connection established")
            return True

        except Exception as e:
            _LOGGER.error(f"Failed to connect: {e}")
            return False

    async def read_all_data(self) -> bool:
        """Read all sensor data from the device.

        Returns:
            True if successful, False otherwise
        """
        if not self.marstek_device:
            _LOGGER.error("Device not connected")
            return False

        try:
            _LOGGER.info("Reading device data...")

            # Read basic device info
            _LOGGER.info("  Reading device info...")
            await self.marstek_device.send_command(CMD_DEVICE_INFO)
            await asyncio.sleep(0.3)

            # Read runtime info
            _LOGGER.info("  Reading runtime info...")
            await self.marstek_device.send_command(CMD_RUNTIME_INFO)
            await asyncio.sleep(0.3)

            # Read BMS data (battery info)
            _LOGGER.info("  Reading BMS data...")
            await self.marstek_device.send_command(CMD_BMS_DATA)
            await asyncio.sleep(0.3)

            # Read system data
            _LOGGER.info("  Reading system data...")
            await self.marstek_device.send_command(CMD_SYSTEM_DATA)
            await asyncio.sleep(0.3)

            # Read WiFi SSID
            _LOGGER.info("  Reading WiFi SSID...")
            await self.marstek_device.send_command(CMD_WIFI_SSID)
            await asyncio.sleep(0.3)

            # Read config data
            _LOGGER.info("  Reading config data...")
            await self.marstek_device.send_command(CMD_CONFIG_DATA)
            await asyncio.sleep(0.3)

            # Read timer info
            _LOGGER.info("  Reading timer info...")
            await self.marstek_device.send_command(CMD_TIMER_INFO)
            await asyncio.sleep(0.3)

            # Read CT polling rate
            _LOGGER.info("  Reading CT polling rate...")
            await self.marstek_device.send_command(CMD_CT_POLLING_RATE)
            await asyncio.sleep(0.3)

            # Read meter IP
            _LOGGER.info("  Reading meter IP...")
            await self.marstek_device.send_command(CMD_METER_IP, b"\x0B")
            await asyncio.sleep(0.3)

            # Read network info
            _LOGGER.info("  Reading network info...")
            await self.marstek_device.send_command(CMD_NETWORK_INFO)
            await asyncio.sleep(0.3)

            # Read local API status
            _LOGGER.info("  Reading local API status...")
            await self.marstek_device.send_command(CMD_LOCAL_API_STATUS)
            await asyncio.sleep(0.3)

            _LOGGER.info("Data reading complete")
            return True

        except Exception as e:
            _LOGGER.error(f"Error reading data: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        if self.marstek_device:
            _LOGGER.info("Disconnecting...")
            await self.marstek_device.disconnect()

    def print_results(self) -> None:
        """Print the collected data in a readable format."""
        print("\n" + "=" * 80)
        print("MARSTEK BATTERY SENSOR - TEST RESULTS")
        print("=" * 80)
        print(f"Timestamp: {datetime.now().isoformat()}")

        if self.ble_device:
            print(f"\n--- Connection Info ---")
            print(f"Device Name: {self.ble_device.name}")
            print(f"BLE Address: {self.ble_device.address}")

        # Device Info
        if any([self.data.device_type, self.data.device_id, self.data.mac_address, self.data.firmware_version]):
            print(f"\n--- Device Info ---")
            if self.data.device_type:
                print(f"Type: {self.data.device_type}")
            if self.data.device_id:
                print(f"ID: {self.data.device_id}")
            if self.data.mac_address:
                print(f"MAC Address: {self.data.mac_address}")
            if self.data.firmware_version:
                print(f"Firmware: {self.data.firmware_version}")

        # Battery/BMS Data
        if any([self.data.battery_soc, self.data.battery_voltage, self.data.battery_current]):
            print(f"\n--- Battery Status ---")
            if self.data.battery_soc is not None:
                print(f"State of Charge (SOC): {self.data.battery_soc:.1f}%")
            if self.data.battery_soh is not None:
                print(f"State of Health (SOH): {self.data.battery_soh:.1f}%")
            if self.data.battery_voltage is not None:
                print(f"Voltage: {self.data.battery_voltage:.2f} V")
            if self.data.battery_current is not None:
                print(f"Current: {self.data.battery_current:.2f} A")
                if self.data.battery_voltage is not None:
                    power = self.data.battery_voltage * self.data.battery_current
                    print(f"Power: {power:.1f} W")
            if self.data.battery_temp is not None:
                print(f"Temperature: {self.data.battery_temp:.1f} °C")
            if self.data.design_capacity is not None:
                print(f"Design Capacity: {self.data.design_capacity:.0f} Wh")
                if self.data.battery_soc is not None:
                    remaining = self.data.design_capacity * self.data.battery_soc / 100
                    print(f"Remaining Capacity: {remaining:.0f} Wh")

        # Cell Voltages
        if any(v is not None for v in self.data.cell_voltages):
            print(f"\n--- Cell Voltages ---")
            for i, voltage in enumerate(self.data.cell_voltages, 1):
                if voltage is not None and voltage > 0:
                    print(f"Cell {i:2d}: {voltage:.3f} V")

        # Runtime Info
        if any([self.data.out1_power, self.data.temp_low, self.data.temp_high]):
            print(f"\n--- Runtime Info ---")
            if self.data.out1_power is not None:
                print(f"Output 1 Power: {self.data.out1_power:.1f} W")
            if self.data.out1_active is not None:
                print(f"Output 1 Active: {self.data.out1_active}")
            if self.data.temp_low is not None:
                print(f"Temperature Low: {self.data.temp_low:.1f} °C")
            if self.data.temp_high is not None:
                print(f"Temperature High: {self.data.temp_high:.1f} °C")
            if self.data.extern1_connected is not None:
                print(f"External 1 Connected: {self.data.extern1_connected}")

        # Network/Connectivity
        if any([self.data.wifi_connected, self.data.mqtt_connected, self.data.wifi_ssid]):
            print(f"\n--- Network Status ---")
            if self.data.wifi_connected is not None:
                print(f"WiFi Connected: {self.data.wifi_connected}")
            if self.data.wifi_ssid:
                print(f"WiFi SSID: {self.data.wifi_ssid}")
            if self.data.mqtt_connected is not None:
                print(f"MQTT Connected: {self.data.mqtt_connected}")
            if self.data.network_info:
                print(f"Network Info: {self.data.network_info}")
            if self.data.meter_ip:
                print(f"Meter IP: {self.data.meter_ip}")

        # Timer/Adaptive Mode
        if any([self.data.adaptive_mode_enabled, self.data.smart_meter_connected]):
            print(f"\n--- Adaptive Mode ---")
            if self.data.adaptive_mode_enabled is not None:
                print(f"Enabled: {self.data.adaptive_mode_enabled}")
            if self.data.smart_meter_connected is not None:
                print(f"Smart Meter Connected: {self.data.smart_meter_connected}")
            if self.data.adaptive_power_out is not None:
                print(f"Adaptive Power Out: {self.data.adaptive_power_out:.1f} W")

        # System Data
        if any([self.data.system_status, self.data.config_mode]):
            print(f"\n--- System Configuration ---")
            if self.data.system_status is not None:
                print(f"System Status: 0x{self.data.system_status:02X}")
            if self.data.config_mode is not None:
                print(f"Config Mode: 0x{self.data.config_mode:02X}")
            if self.data.config_status is not None:
                print(f"Config Status: {self.data.config_status}")
            if self.data.ct_polling_rate is not None:
                rates = ["Fastest", "Medium", "Slowest"]
                rate_name = rates[self.data.ct_polling_rate] if self.data.ct_polling_rate < 3 else f"Unknown ({self.data.ct_polling_rate})"
                print(f"CT Polling Rate: {rate_name}")
            if self.data.local_api_status:
                print(f"Local API: {self.data.local_api_status}")

        # Diagnostics
        if self.marstek_device:
            print(f"\n--- BLE Diagnostics ---")
            diag = self.marstek_device.get_diagnostics()
            overall = diag.get("overall", {})
            print(f"Commands Sent: {overall.get('total_sent', 0)}")
            print(f"Success Rate: {overall.get('success_rate', 0):.1f}%")
            print(f"Connected: {diag.get('connected', False)}")

            if overall.get('last_error'):
                print(f"Last Error: {overall['last_error']}")

        print("\n" + "=" * 80)


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Test Marstek BLE battery sensor outside Home Assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-discover and connect to any Marstek device
  python test_marstek_standalone.py

  # Connect to specific device by address
  python test_marstek_standalone.py --address AA:BB:CC:DD:EE:FF

  # Connect to device by name prefix
  python test_marstek_standalone.py --name MST_ACCP_1234

  # Enable debug logging
  python test_marstek_standalone.py --debug
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

    # Create tester
    tester = MarstekTester(device_address=args.address, device_name=args.name)

    try:
        # Discover device
        if not await tester.discover_device():
            print("\nERROR: Could not find Marstek device")
            print("Make sure:")
            print("  1. The device is powered on and within range")
            print("  2. Bluetooth is enabled on your Mac")
            print("  3. The device is not connected to Home Assistant or other apps")
            return 1

        # Connect
        if not await tester.connect():
            print("\nERROR: Could not connect to device")
            return 1

        # Read all data
        if not await tester.read_all_data():
            print("\nERROR: Could not read data from device")
            return 1

        # Print results
        tester.print_results()

        return 0

    except KeyboardInterrupt:
        print("\nInterrupted by user")
        return 130
    except Exception as e:
        _LOGGER.exception("Unexpected error")
        print(f"\nERROR: {e}")
        return 1
    finally:
        # Always disconnect
        await tester.disconnect()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
