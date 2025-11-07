# Marstek BLE Standalone Test Script

This test script allows you to read sensor values from your Marstek battery **outside of Home Assistant**, directly on macOS using your Mac's Bluetooth radio.

## Purpose

Test the BLE protocol implementation independently to determine if issues are:
- Related to the integration's BLE protocol implementation
- Related to the Home Assistant environment
- Hardware/firmware specific

## Features

- ✅ Uses the **same BLE protocol logic** as the Home Assistant integration
- ✅ Runs standalone on macOS (no Home Assistant required)
- ✅ Auto-discovers Marstek devices via BLE scanning
- ✅ Reads all sensor data (battery, runtime, network, etc.)
- ✅ Displays diagnostics and connection statistics
- ✅ Detailed logging for troubleshooting

## Requirements

- macOS with Bluetooth
- Python 3.10 or later
- The Marstek battery within Bluetooth range

## Installation

1. **Navigate to the standalone_test directory:**
   ```bash
   cd standalone_test
   ```

2. **Install Python dependencies:**
   ```bash
   pip install -r test_requirements.txt
   ```

   Or manually:
   ```bash
   pip install bleak bleak-retry-connector
   ```

3. **Make the script executable (optional):**
   ```bash
   chmod +x test_marstek_standalone.py
   ```

## Usage

### Basic Usage (Auto-discover)

The simplest way - automatically finds and connects to any Marstek device:

```bash
python test_marstek_standalone.py
```

### Connect by Device Address

If you know the BLE MAC address:

```bash
python test_marstek_standalone.py --address AA:BB:CC:DD:EE:FF
```

### Connect by Device Name

If you know the device name or prefix:

```bash
python test_marstek_standalone.py --name MST_ACCP_1234
```

### Enable Debug Logging

For troubleshooting BLE communication issues:

```bash
python test_marstek_standalone.py --debug
```

## Output

The script will display:

1. **Connection Info** - Device name and BLE address
2. **Device Info** - Type, ID, MAC, firmware version
3. **Battery Status** - SOC, voltage, current, power, temperature, capacity
4. **Cell Voltages** - Individual cell voltages (up to 16 cells)
5. **Runtime Info** - Output power, temperature ranges, connection status
6. **Network Status** - WiFi, MQTT, network info
7. **Adaptive Mode** - Smart meter and adaptive power settings
8. **System Configuration** - Config mode, CT polling rate, local API
9. **BLE Diagnostics** - Command success rates, connection status

## Example Output

```
================================================================================
MARSTEK BATTERY SENSOR - TEST RESULTS
================================================================================
Timestamp: 2025-01-15T10:30:45.123456

--- Connection Info ---
Device Name: MST_ACCP_1234
BLE Address: AA:BB:CC:DD:EE:FF

--- Device Info ---
Type: HMG-50
ID: 1234567890
MAC Address: AA:BB:CC:DD:EE:FF
Firmware: 1.0.4

--- Battery Status ---
State of Charge (SOC): 85.0%
State of Health (SOH): 100.0%
Voltage: 51.20 V
Current: 2.5 A
Power: 128.0 W
Temperature: 25.0 °C
Design Capacity: 5120 Wh
Remaining Capacity: 4352 Wh

--- Cell Voltages ---
Cell  1: 3.200 V
Cell  2: 3.201 V
...

--- BLE Diagnostics ---
Commands Sent: 11
Success Rate: 100.0%
Connected: True
================================================================================
```

## Troubleshooting

### Device Not Found

If the script can't find your device:

1. **Check device is powered on** and within Bluetooth range
2. **Enable Bluetooth** on your Mac (System Settings > Bluetooth)
3. **Disconnect from Home Assistant** - the device can only maintain one connection
4. **Run with debug logging:**
   ```bash
   python test_marstek_standalone.py --debug
   ```
5. **List available devices** - the script will show all discovered BLE devices if no Marstek device is found

### Connection Fails

If discovery works but connection fails:

1. **Close other Bluetooth apps** that might be using the device
2. **Restart the battery** - power cycle it
3. **Move closer** to the device
4. **Check macOS Bluetooth permissions** - the script needs permission to access Bluetooth
5. **Try specifying the address directly:**
   ```bash
   python test_marstek_standalone.py --address AA:BB:CC:DD:EE:FF
   ```

### Partial Data

If some sensor values are missing:

- This is expected - not all commands work on all firmware versions
- The integration handles this gracefully
- Check the debug logs to see which commands are responding

### Permission Denied on macOS

If you get permission errors:

```bash
# Grant terminal Bluetooth access in:
# System Settings > Privacy & Security > Bluetooth
```

You may need to grant Terminal (or your Python IDE) permission to use Bluetooth.

## Understanding the Results

### Success Rate

- **100%** = All commands sent and acknowledged
- **90-99%** = Some timeouts but mostly working
- **<90%** = Communication issues

### Common Issues to Diagnose

1. **Connection drops** - Check BLE diagnostics for connection stability
2. **Missing data** - Some firmware versions don't respond to all commands
3. **Timeout errors** - Device may be out of range or experiencing interference
4. **Parse errors** - Protocol mismatch (report these as issues)

## Comparing with Home Assistant

To determine if issues are HA-related:

1. **Run this script** and note the success rate and data quality
2. **Check HA logs** for the same metrics
3. **Compare results:**
   - ✅ Script works, HA fails → HA environment issue
   - ✅ Both fail → Protocol/hardware issue
   - ✅ Both work → No issues (check HA configuration)

## How It Works

The script:

1. **Scans** for BLE devices matching Marstek name prefixes
2. **Connects** using the same `MarstekBLEDevice` class as the integration
3. **Sends commands** using the `MarstekProtocol` class
4. **Receives notifications** via BLE characteristic `0000ff02-...`
5. **Parses responses** using the integration's protocol parser
6. **Displays results** in a human-readable format

All the BLE protocol logic is **identical** to the Home Assistant integration - it's imported directly from `custom_components/marstek_ble/marstek_device.py`.

## Files Used

- `standalone_test/test_marstek_standalone.py` - Main test script
- `custom_components/marstek_ble/marstek_device.py` - BLE protocol (imported from parent directory)
- `standalone_test/test_requirements.txt` - Python dependencies

## Support

If you find issues:

1. Run with `--debug` flag
2. Capture the full output
3. Report on GitHub: https://github.com/jaapp/ha-marstek-ble/issues
4. Include:
   - macOS version
   - Python version
   - Device model (HMG-50, Venus E, etc.)
   - Firmware version
   - Debug logs

## License

Same as the main integration (see repository root LICENSE file).
