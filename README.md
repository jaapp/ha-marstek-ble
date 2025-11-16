# Marstek BLE Integration for Home Assistant

> **⚠️ BETA/EXPERIMENTAL RELEASE**
> This integration is currently in beta and should be considered experimental. While it is functional, you may encounter bugs or unexpected behavior. Please report any issues you find.

Home Assistant integration for Marstek Venus E energy storage systems via Bluetooth Low Energy (BLE).

## Features

- **Multi-device support**: Add multiple Marstek batteries, each as a separate device
- **Real-time monitoring**: Battery voltage, current, SOC, SOH, temperature, cell voltages
- **Power control**: Output control, EPS mode, power limits, adaptive mode
- **Energy tracking**: Integration with Home Assistant Energy Dashboard
- **BLE Proxy support**: Extend range using ESPHome BLE proxies
- **Configurable polling**: Adjust update interval to balance responsiveness and BLE traffic
- **Local operation**: No cloud connectivity required

## Installation

### Via HACS (Recommended)

1. Click this button:

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jaapp&repository=ha-marstek-ble&category=integration)

Or:

1. Open **HACS → Integrations → Custom repositories**
2. Add `https://github.com/jaapp/ha-marstek-ble` as an *Integration*
3. Install **Marstek BLE** and restart Home Assistant

### Manual Installation

1. Copy `custom_components/marstek_ble` to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Setup

1. Go to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for "Marstek BLE"
4. Select your battery from the discovered devices
5. Click **Submit**
6. (Optional) Open the integration options to tune the polling interval

Repeat for each battery you want to add.

## Polling

- **Fast (default 1s)**: Runtime info and BMS data; configurable in Options → Polling interval (clamped to 1–60s)
- **Medium (~60s by default)**: System data, WiFi SSID, config, CT polling rate, meter IP, network info, device identity, timer info, logs; runs as a multiple of the fast interval

Entity IDs use your device slug—replace `<device>` with your device name (e.g., `sensor.backup_battery_battery_voltage`):

| Entity ID (example) | Example value | Polling tier (s) |
| --- | --- | --- |
| `sensor.<device>_battery_voltage` | 48.45 V | fast (1) |
| `sensor.<device>_battery_current` | 0.0 A | fast (1) |
| `sensor.<device>_battery_soc` | 11.0 % | fast (1) |
| `sensor.<device>_battery_soh` | 99.0 % | fast (1) |
| `sensor.<device>_battery_temperature` | 16.0 °C | fast (1) |
| `sensor.<device>_battery_power` | 0.0 W | fast (1) |
| `sensor.<device>_battery_power_in` | 0 W | fast (1) |
| `sensor.<device>_battery_power_out` | 0 W | fast (1) |
| `sensor.<device>_output_1_power` | 0.0 W | fast (1) |
| `sensor.<device>_design_capacity` | 5120.0 Wh | fast (1) |
| `sensor.<device>_remaining_capacity` | 563.2 Wh | fast (1) |
| `sensor.<device>_available_capacity` | 4556.8 Wh | fast (1) |
| `sensor.<device>_temperature_low` | 0.0 °C | fast (1) |
| `sensor.<device>_temperature_high` | 0.0 °C | fast (1) |
| `sensor.<device>_cell_1_voltage` | 3.030 V | fast (1) |
| `sensor.<device>_cell_2_voltage` | 3.037 V | fast (1) |
| `sensor.<device>_cell_3_voltage` | 3.026 V | fast (1) |
| `sensor.<device>_cell_4_voltage` | 3.034 V | fast (1) |
| `sensor.<device>_cell_5_voltage` | 3.025 V | fast (1) |
| `sensor.<device>_cell_6_voltage` | 3.022 V | fast (1) |
| `sensor.<device>_cell_7_voltage` | 3.028 V | fast (1) |
| `sensor.<device>_cell_8_voltage` | 3.034 V | fast (1) |
| `sensor.<device>_cell_9_voltage` | 3.021 V | fast (1) |
| `sensor.<device>_cell_10_voltage` | 3.031 V | fast (1) |
| `sensor.<device>_cell_11_voltage` | 3.028 V | fast (1) |
| `sensor.<device>_cell_12_voltage` | 3.035 V | fast (1) |
| `sensor.<device>_cell_13_voltage` | 3.020 V | fast (1) |
| `sensor.<device>_cell_14_voltage` | 3.027 V | fast (1) |
| `sensor.<device>_cell_15_voltage` | 3.033 V | fast (1) |
| `sensor.<device>_cell_16_voltage` | 3.025 V | fast (1) |
| `sensor.<device>_battery_state` | inactive | fast (1) |
| `sensor.<device>_system_status` | 1 | medium (~60) |
| `sensor.<device>_config_mode` | 100 | medium (~60) |
| `sensor.<device>_ct_polling_rate` | 119 | medium (~60) |
| `sensor.<device>_wifi_ssid` | ExampleWiFi | medium (~60) |
| `sensor.<device>_network_info` | ip:192.168.50.120,gate:192.168.50.1,mask:255.255.255.0,dns:192.168.50.1 | medium (~60) |
| `sensor.<device>_meter_ip` | (not set) | medium (~60) |
| `sensor.<device>_device_type` | HMG-50 | medium (~60) |
| `sensor.<device>_device_id` | ABCD1234567890 | medium (~60) |
| `sensor.<device>_mac_address` | 00:11:22:33:44:55 | medium (~60) |
| `sensor.<device>_firmware_version` | 202409090159 | medium (~60) |

### Energy dashboard sensors

The integration also exposes four helper sensors you can add directly to the Home Assistant Energy Dashboard:

- `Battery Energy In`
- `Battery Energy Out`
- `Daily Battery Energy In`
- `Daily Battery Energy Out`

## Supported Devices

- Marstek Venus E hardware v2 (`MST_ACCP_*` - tested)
- Marstek Venus E hardware v3 (`MST_VNSE3_*` - untested)

## BLE Proxy Setup

To extend Bluetooth range, set up an [ESPHome BLE Proxy](https://esphome.io/components/bluetooth_proxy/):

### Recommended Hardware

Any ESP32 device will work as a Bluetooth proxy. Popular options include:

- **ESP-WROOM-32** - Affordable general-purpose ESP32 module
- **M5Stack Atom Lite** - Compact device with built-in RGB LED for status indication
- **ESP32-DevKitC** - Development board with USB programming
- **Any ESP32-based device** with Bluetooth support

### Setup Steps

1. Flash an ESP32 device with ESPHome
2. Add the bluetooth_proxy component
3. Add to Home Assistant
4. Home Assistant will automatically route Marstek BLE traffic through the proxy when needed

## Development & Testing

### Local Testing

1. Copy `custom_components/marstek_ble/` to your HA `config/custom_components/`
2. Restart Home Assistant
3. Enable debug logging in `configuration.yaml`:
   ```yaml
   logger:
     default: info
     logs:
       custom_components.marstek_ble: debug
   ```
4. Check logs: Settings → System → Logs

### Testing Multiple Batteries

The integration supports multiple batteries. To test:
1. Ensure each battery has a unique BLE name (e.g., `MST_ACCP_5251`, `MST_ACCP_d7c4`)
2. Add each battery separately via the UI
3. Each will appear as a separate integration entry
4. Each battery gets its own set of entities

## Attribution

Based on reverse engineering work from:
- [marstek-venus-monitor](https://github.com/rweijnen/marstek-venus-monitor) by @rweijnen
- [esphome-b2500](https://github.com/tomquist/esphome-b2500) by @tomquist
- [hm2500pub](https://github.com/noone2k/hm2500pub) by @noone2k

## License

MIT

## Disclaimer

This is experimental software created through reverse engineering. Not affiliated with Marstek Energy. Use at your own risk.
