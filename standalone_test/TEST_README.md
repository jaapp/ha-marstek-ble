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
- ✅ **Statistics mode** - Measures command response times with P95/P99 percentiles
- ✅ **Sequential vs Parallel** - Test command execution modes to identify BLE contention
- ✅ **ESPHome Proxy support** - Test via Bluetooth proxy to simulate HA behavior
- ✅ **Persistent connections** - Maintains connections like Home Assistant does
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
   python3 -m pip install -r test_requirements.txt
   ```

   Or manually:
   ```bash
   python3 -m pip install bleak bleak-retry-connector aioesphomeapi
   ```

   **Note:** Use `python3 -m pip` instead of just `pip` to ensure packages are installed to the correct Python interpreter.

   **Note:** `aioesphomeapi` is only required if you want to test via ESPHome Bluetooth Proxy.

3. **Make the script executable (optional):**
   ```bash
   chmod +x test_marstek_standalone.py
   ```

## Usage

### Quick Test (All Modes)

To quickly test all argument combinations and validate the test script:

```bash
./test_all_modes.sh
```

This runs 8 tests covering all combinations of:
- Mode: normal vs stats
- Execution: sequential vs parallel
- Connection: direct BLE vs ESPHome proxy

### Basic Usage (Normal Mode)

Read sensor values once and display in a readable format:

```bash
# Sequential (default)
python3 test_marstek_standalone.py

# Parallel (read from all devices simultaneously)
python3 test_marstek_standalone.py --parallel
```

### Statistics Mode (Recommended)

Measure command response times to optimize polling intervals:

```bash
# Sequential commands (default - no BLE contention)
python3 test_marstek_standalone.py --stats

# Parallel commands (test if contention causes issues)
python3 test_marstek_standalone.py --stats --parallel

# More iterations for better statistics
python3 test_marstek_standalone.py --stats --iterations 20
```

**Statistics mode output:**
- Min/Max/Avg response times per command
- P50/P95/P99 percentiles (use P95 for timeout recommendations)
- Success rate per command
- Recommended delays based on measurements

### ESPHome Bluetooth Proxy Testing

Test via ESPHome proxy to simulate real Home Assistant behavior:

```bash
# Sequential via proxy
python3 test_marstek_standalone.py --stats \
  --proxy 192.168.7.44 \
  --proxy-key "your-base64-key"

# Parallel via proxy (tests HA's current behavior)
python3 test_marstek_standalone.py --stats --parallel \
  --proxy 192.168.7.44 \
  --proxy-key "your-base64-key"
```

**Note:** Proxy adds ~50-200ms latency to all operations. Stats will show this overhead.

### Connection Management

**The script ALWAYS uses persistent connections (like HA):**
- Connects to ALL devices at startup
- Maintains connections during all iterations
- Disconnects only at the end

### Sequential vs Parallel

**Sequential (default):**
- Sends commands to Device 1, waits for responses
- Then Device 2, waits for responses
- No BLE radio contention
- More reliable

**Parallel (`--parallel`):**
- Sends commands to ALL devices simultaneously
- May have BLE radio contention (dropped notifications)
- Tests HA's current behavior

### Filter by Device

```bash
# By BLE MAC address
python3 test_marstek_standalone.py --stats --address AA:BB:CC:DD:EE:FF

# By device name
python3 test_marstek_standalone.py --stats --name MST_ACCP_1234
```

### Enable Debug Logging

```bash
python3 test_marstek_standalone.py --stats --debug
```

## Output

### Stats Mode Output

Statistics mode shows command response time measurements:

```
📊 STATS MODE: Running 10 iterations (SEQUENTIAL, direct BLE)
Connection Management: PERSISTENT (connect all devices once)
Command Execution: SEQUENTIAL (no contention)

Phase 1: Connecting to all devices...
  • Connecting to MST_ACCP_d7c4... ✓
  • Connecting to MST_ACCP_92f6... ✓
✓ Connected to 2 device(s)

Phase 2: Running 10 iterations (SEQUENTIAL commands)...

[Iteration 1/10]
  • MST_ACCP_d7c4... ✓
  • MST_ACCP_92f6... ✓
...

Phase 3: Disconnecting all devices...
  • Disconnected MST_ACCP_d7c4
  • Disconnected MST_ACCP_92f6

========================================================================================================================
COMMAND RESPONSE STATISTICS (20 total samples)
========================================================================================================================

Command              Success    Min        Avg        P50        P95        P99        Max        Recommend
────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
Runtime Info (0x03) ⚡ 100.0%     45ms       67ms       65ms       89ms       95ms       102ms      0.1s (Fast) ⚡
BMS Data (0x14) ⚡     100.0%     52ms       71ms       68ms       92ms       98ms       105ms      0.1s (Fast) ⚡
Device Info (0x04)    100.0%     125ms      142ms      140ms      165ms      172ms      180ms      0.2s (Medium)
System Data (0x0D)    95.0%      98ms       118ms      115ms      145ms      152ms      160ms      0.2s (Medium)
WiFi SSID (0x08)      90.0%      110ms      135ms      130ms      168ms      175ms      185ms      0.2s (Medium)
...
────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

⚡ = Critical command (power monitoring) - needs fastest updates

Recommendations based on P95 (95th percentile):
  • < 150ms → Use 0.1s delay (aggressive, real-time)
  • < 250ms → Use 0.2s delay (balanced)
  • < 350ms → Use 0.3s delay (conservative, current HA)
  • > 350ms → Use 0.4s+ delay (very slow device)

NOTE: These timings are for DIRECT BLE.
ESPHome Bluetooth Proxy adds ~50-200ms latency!
Add extra margin for proxy: Fast→0.2s, Medium→0.3s, Current→0.4s
========================================================================================================================
```

### Normal Mode Output

Normal mode displays sensor values in a readable format:

```
📡 Reading sensor values from 2 device(s) (SEQUENTIAL)...

Connecting to devices...
  • Connecting to MST_ACCP_d7c4... ✓
  • Connecting to MST_ACCP_92f6... ✓
✓ Connected to 2 device(s)

Reading sensor data...
  • Reading MST_ACCP_d7c4... ✓
  • Reading MST_ACCP_92f6... ✓

Disconnecting...

====================================================================================================
MARSTEK BATTERY SENSOR - TEST RESULTS
====================================================================================================

────────────────────────────────────────────────────────────────────────────────────────────────────
Device: MST_ACCP_d7c4 (C7C929F7-DE17-5DF6-8FFE-0E74B1AFC509)
────────────────────────────────────────────────────────────────────────────────────────────────────

📋 Device Info:
  Type:       HMG-50
  ID:         1234567890
  MAC:        AA:BB:CC:DD:EE:FF
  Firmware:   1.0.4

🔋 Battery Status:
  SOC:        85.0%
  SOH:        100.0%
  Voltage:    51.20 V
  Current:    2.5 A
  Power:      128.0 W
  Temp:       25.0 °C
  Capacity:   5120 Wh (design)
              4352 Wh (remaining)

⚡ Cell Voltages:
  Cell  1:    3.200 V
  Cell  2:    3.201 V
  Cell  3:    3.199 V
  ...

⏱️  Runtime Info:
  Output:     128.0 W
  Max Temp:   26.5 °C
  Min Temp:   24.0 °C

🌐 Network:
  WiFi:       MyNetwork
  MQTT:       Connected
  IP:         192.168.1.100

⚙️  Configuration:
  Config Mode: 2
  CT Rate:     5s
  Local API:   Enabled

────────────────────────────────────────────────────────────────────────────────────────────────────
Device: MST_ACCP_92f6 (...)
────────────────────────────────────────────────────────────────────────────────────────────────────
[... device 2 data ...]

====================================================================================================
✓ Successfully read data from 2 device(s)
====================================================================================================
```

## Troubleshooting

### Device Not Found

If the script can't find your device:

1. **Check device is powered on** and within Bluetooth range
2. **Enable Bluetooth** on your Mac (System Settings > Bluetooth)
3. **Disconnect from Home Assistant** - the device can only maintain one connection
4. **Run with debug logging:**
   ```bash
   python3 test_marstek_standalone.py --debug
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
   python3 test_marstek_standalone.py --address AA:BB:CC:DD:EE:FF
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

## Test Wrapper Script

The `test_all_modes.sh` script validates the test script by running all argument combinations:

```bash
./test_all_modes.sh
```

**What it tests:**
1. Normal + Sequential + Direct BLE
2. Normal + Parallel + Direct BLE
3. Normal + Sequential + Proxy
4. Normal + Parallel + Proxy
5. Stats + Sequential + Direct BLE
6. Stats + Parallel + Direct BLE
7. Stats + Sequential + Proxy
8. Stats + Parallel + Proxy

**Configuration:**
- Uses only 2 iterations per stats test (quick validation)
- Set proxy via environment variables:
  ```bash
  PROXY_HOST=192.168.7.44 PROXY_KEY="your-key" ./test_all_modes.sh
  ```

**Output:**
- Shows pass/fail for each test
- Summary at the end
- Exits with code 0 if all pass, 1 if any fail

## Files Used

- `standalone_test/test_marstek_standalone.py` - Main test script
- `standalone_test/test_all_modes.sh` - Wrapper to test all argument combinations
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
