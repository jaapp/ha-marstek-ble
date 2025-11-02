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

1. Add this repository as a custom repository in HACS
2. Search for "Marstek BLE" in HACS integrations
3. Click Install
4. Restart Home Assistant

### Manual Installation

1. Copy `custom_components/marstek_ble` to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Setup

1. Ensure your Marstek battery's Bluetooth is enabled
2. Go to **Settings** → **Devices & Services**
3. Click **Add Integration**
4. Search for "Marstek BLE"
5. Select your battery from the discovered devices
6. Click **Submit**

Repeat for each battery you want to add.

## Supported Devices

- Marstek Venus E (tested)
- Other Marstek models with BLE starting with "MST" (untested)

## BLE Proxy Setup

To extend Bluetooth range, set up an [ESPHome BLE Proxy](https://esphome.io/components/bluetooth_proxy/):

1. Flash an ESP32 device with ESPHome
2. Add the bluetooth_proxy component
3. Add to Home Assistant
4. Your Marstek batteries will automatically use the proxy when needed

## Troubleshooting

- **Device not discovered**: Ensure Bluetooth is enabled on your HA server and the battery is within range
- **Connection issues**: Check battery is not connected to another device (ESPHome gateway, mobile app)
- **Entities unavailable**: Connection may be lost; entities will restore when reconnected

## Attribution

Based on reverse engineering work from:
- [esphome-b2500](https://github.com/tomquist/esphome-b2500) by @tomquist
- [hm2500pub](https://github.com/noone2k/hm2500pub) by @noone2k
- [marstek-venus-monitor](https://github.com/rweijnen/marstek-venus-monitor) by @rweijnen

## License

MIT

## Disclaimer

This is experimental software created through reverse engineering. Not affiliated with Marstek Energy. Use at your own risk.
