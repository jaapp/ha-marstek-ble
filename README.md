# Marstek BLE Integration for Home Assistant

Home Assistant integration for Marstek Venus E energy storage systems via Bluetooth Low Energy (BLE).

## Features

- **Multi-device support**: Add multiple Marstek batteries, each as a separate device
- **Real-time monitoring**: Battery voltage, current, SOC, SOH, temperature, cell voltages
- **Power control**: Output control, EPS mode, power limits, adaptive mode
- **Energy tracking**: Integration with Home Assistant Energy Dashboard
- **BLE Proxy support**: Extend range using ESPHome BLE proxies
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

Repeat for each battery you want to add.

## Supported Devices

- Marstek Venus E hardware v2 (`MST_ACCP_*` - tested)
- Marstek Venus E hardware v3 (`MST_VNSE3_*` - untested)

**Note:** CT devices (e.g., `MST-SMR_*`) are not batteries and will not be shown in device discovery.

## BLE Proxy Setup

To extend Bluetooth range, set up an [ESPHome BLE Proxy](https://esphome.io/components/bluetooth_proxy/):

1. Flash an ESP32 device with ESPHome
2. Add the bluetooth_proxy component
3. Add to Home Assistant
4. Your Marstek batteries will automatically use the proxy when needed

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

### BLE Proxy Testing

1. Set up an ESP32 with ESPHome BLE Proxy:
   ```yaml
   esphome:
     name: ble-proxy

   esp32:
     board: esp32dev

   bluetooth_proxy:
     active: true
   ```
2. Flash and add to Home Assistant
3. Move Marstek battery out of direct BLE range
4. Verify connection maintains through proxy

## Troubleshooting

- **Device not discovered**: Ensure Bluetooth is enabled on your HA server and the battery is within range
- **Connection issues**: Check battery is not connected to another device (ESPHome gateway, mobile app)
- **Entities unavailable**: Connection may be lost; entities will restore when reconnected

## Attribution

Based on reverse engineering work from:
- [marstek-venus-monitor](https://github.com/rweijnen/marstek-venus-monitor) by @rweijnen
- [esphome-b2500](https://github.com/tomquist/esphome-b2500) by @tomquist
- [hm2500pub](https://github.com/noone2k/hm2500pub) by @noone2k

## License

MIT

## Disclaimer

This is experimental software created through reverse engineering. Not affiliated with Marstek Energy. Use at your own risk.
