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
- **Configurable polling**: Adjust fast and medium intervals to balance responsiveness and BLE traffic
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
6. (Optional) Open the integration options to tune the fast/medium polling intervals

Repeat for each battery you want to add.

## Polling

- **Fast (default 1s)**: Runtime info and BMS data; configurable in Options → Fast polling interval (clamped to 1–60s)
- **Medium (default 60s)**: System data, WiFi SSID, config, CT polling rate, meter IP, network info, device identity, timer info, logs; configurable in Options → Medium polling interval (clamped to 5–300s and not faster than the fast interval)

Medium polling is scheduled on multiples of the fast interval, so the cadence is rounded to the nearest fast tick.

Entity IDs use your device slug—replace `<device>` with your device name (e.g., `sensor.backup_battery_battery_voltage`). Values below are sample values only—private identifiers (IP, MAC, serials) are intentionally omitted. Defaults in parentheses reflect the initial configuration; both tiers can be customized in the integration options.

| Entity ID (example) | Example value | Polling tier (s) |
| --- | --- | --- |
| `sensor.<device>_battery_voltage` | 50.37 V | fast (1) |
| `sensor.<device>_battery_current` | -0.2 A | fast (1) |
| `sensor.<device>_battery_soc` | 15.0 % | fast (1) |
| `sensor.<device>_battery_soh` | 99.0 % | fast (1) |
| `sensor.<device>_battery_temperature` | 1.0 °C | fast (1) |
| `sensor.<device>_battery_power` | -10.07 W | fast (1) |
| `sensor.<device>_battery_power_in` | 0 W | fast (1) |
| `sensor.<device>_battery_power_out` | 10.07 W | fast (1) |
| `sensor.<device>_output_1_power` | 0.0 W | fast (1) |
| `sensor.<device>_design_capacity` | 5120.0 Wh | fast (1) |
| `sensor.<device>_remaining_capacity` | 768.0 Wh | fast (1) |
| `sensor.<device>_available_capacity` | 4352.0 Wh | fast (1) |
| `sensor.<device>_temperature_low` | 0.0 °C | fast (1) |
| `sensor.<device>_temperature_high` | 0.0 °C | fast (1) |
| `sensor.<device>_cell_1_voltage` | 3.148 V | fast (1) |
| `sensor.<device>_cell_2_voltage` | 3.153 V | fast (1) |
| `sensor.<device>_cell_3_voltage` | 3.146 V | fast (1) |
| `sensor.<device>_cell_4_voltage` | 3.151 V | fast (1) |
| `sensor.<device>_cell_5_voltage` | 3.148 V | fast (1) |
| `sensor.<device>_cell_6_voltage` | 3.145 V | fast (1) |
| `sensor.<device>_cell_7_voltage` | 3.148 V | fast (1) |
| `sensor.<device>_cell_8_voltage` | 3.153 V | fast (1) |
| `sensor.<device>_cell_9_voltage` | 3.144 V | fast (1) |
| `sensor.<device>_cell_10_voltage` | 3.150 V | fast (1) |
| `sensor.<device>_cell_11_voltage` | 3.148 V | fast (1) |
| `sensor.<device>_cell_12_voltage` | 3.154 V | fast (1) |
| `sensor.<device>_cell_13_voltage` | 3.143 V | fast (1) |
| `sensor.<device>_cell_14_voltage` | 3.147 V | fast (1) |
| `sensor.<device>_cell_15_voltage` | 3.151 V | fast (1) |
| `sensor.<device>_cell_16_voltage` | 3.146 V | fast (1) |
| `sensor.<device>_battery_state` | discharging | fast (1) |
| `sensor.<device>_system_status` | 1 | medium (~60) |
| `sensor.<device>_config_mode` | 100 | medium (~60) |
| `sensor.<device>_ct_polling_rate` | 119 | medium (~60) |
| `sensor.<device>_wifi_ssid` | ExampleWiFi | medium (~60) |
| `sensor.<device>_device_type` | HMG-50 | medium (~60) |
| `sensor.<device>_firmware_version` | 202409090159 | medium (~60) |
| `sensor.<device>_grid_power` | 0.0 W | medium (~60) |
| `sensor.<device>_solar_power` | 0.0 W | medium (~60) |
| `sensor.<device>_daily_energy_charged` | 0.32 kWh | medium (~60) |
| `sensor.<device>_daily_energy_discharged` | 0.48 kWh | medium (~60) |
| `sensor.<device>_monthly_energy_charged` | 39.87 kWh | medium (~60) |
| `sensor.<device>_monthly_energy_discharged` | 31.70 kWh | medium (~60) |
| `sensor.<device>_total_energy_charged` | 51.51 kWh | medium (~60) |
| `sensor.<device>_total_energy_discharged` | 41.07 kWh | medium (~60) |
| `sensor.<device>_mosfet_temperature` | 13.0 °C | medium (~60) |
| `sensor.<device>_temperature_sensor_1` | 13.0 °C | medium (~60) |
| `sensor.<device>_temperature_sensor_2` | 13.0 °C | medium (~60) |
| `sensor.<device>_temperature_sensor_3` | 13.0 °C | medium (~60) |
| `sensor.<device>_temperature_sensor_4` | 13.0 °C | medium (~60) |
| `sensor.<device>_work_mode` | 1 | medium (~60) |
| `sensor.<device>_product_code` | 154 | medium (~60) |
| `sensor.<device>_power_rating` | 800 W | medium (~60) |
| `sensor.<device>_bms_version` | 215 | medium (~60) |
| `sensor.<device>_voltage_limit` | 57.1 V | medium (~60) |
| `sensor.<device>_charge_current_limit` | 50.0 A | medium (~60) |
| `sensor.<device>_discharge_current_limit` | 90.0 A | medium (~60) |
| `sensor.<device>_error_code` | 0 | medium (~60) |
| `sensor.<device>_warning_code` | 851968 | medium (~60) |
| `sensor.<device>_runtime` | 33.78 h | medium (~60) |

### Energy dashboard sensors

Add the cumulative totals to Home Assistant's Energy Dashboard using your device slug:
- `sensor.<device>_total_energy_charged` → Battery energy in
- `sensor.<device>_total_energy_discharged` → Battery energy out

These increment-only sensors (e.g., `51.51 kWh` in, `41.07 kWh` out) are the recommended sources for long-term battery accounting.

## Mode controls

- **Self-Consumption (Auto)**: command `0x0E`; exposed as “Self-Consumption Mode On/Off” buttons. Mirrors the app’s “Self Consumption” mode.
- **Manual (Work Mode)**: command `0x09`; exposed as “Manual Mode On/Off” buttons. Mirrors the app’s “Manual” mode for manual scheduling.
- **AI Optimization / Trade**: command `0x11`; exposed as an experimental button (“Enable AI Optimization”) and reflected in the Adaptive Mode switch state.

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
