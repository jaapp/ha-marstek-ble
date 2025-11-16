# Marstek BLE Entities

This page documents every entity the Marstek BLE integration exposes, grouped by capability. Values and command payloads mirror the reverse-engineered protocol documented in [marstek-venus-monitor](https://github.com/rweijnen/marstek-venus-monitor).

## Polling cadence

- Fast (default 1s): runtime info (0x03) and BMS data (0x14) such as voltage, current, SOC/SOH, temperatures, and most power/energy flows.
- Medium (default 60s): system/config/network info (0x0D/0x08/0x21/0x22/0x24) including Wi-Fi SSID, CT polling rate, meter IP, and identity.
- Both cadences are configurable in the integration options and rounded to the nearest fast tick.

## Buttons (actions)

| Friendly name (suffix) | Command/payload | Effect |
| --- | --- | --- |
| `Reboot` (`button.<device>_reboot`) | `0x25` + `""` | Reboots the inverter/BMS controller. |
| `Enable AI Optimization (Experimental)` (`button.<device>_enable_ai_mode`) | `0x11` + `0x01` | Sends the adaptive/AI/Trade mode enable command; experimental (state also exposed via the Adaptive Mode switch). |
| `Set 800W Mode` (`button.<device>_set_800w_mode`) | `0x15` + `0x20 0x03` | Writes the 800 W power-capacity setting. |
| `Set 2500W Mode` (`button.<device>_set_2500w_mode`) | `0x15` + `0xC4 0x09` | Writes the 2 500 W power-capacity setting. |
| `Set AC Power 2500W` (`button.<device>_set_ac_power_2500w`) | `0x16` + `0xC4 0x09` | Sets AC output power limit to 2 500 W. |
| `Set Total Power 2500W` (`button.<device>_set_total_power_2500w`) | `0x17` + `0xC4 0x09` | Sets the combined power limit to 2 500 W. |

These actions issue write-only commands; state is not tracked beyond the device’s acknowledgement.

## Switches (toggles)

| Friendly name (suffix) | Command | State source | Notes |
| --- | --- | --- | --- |
| `Output 1 Control` (`switch.<device>_out1_control`) | `0x0E` | From telemetry (`out1_active`) | Enables/disables AC Output 1. |
| `EPS Mode` (`switch.<device>_eps_mode`) | `0x05` | Assumed (no feedback) | Toggles EPS/backup mode; device does not report current state. |
| `Adaptive Mode` (`switch.<device>_adaptive_mode`) | `0x11` | From telemetry (`adaptive_mode_enabled`) | Enables adaptive output mode (timer info 0x13). |
| `AC Input` (`switch.<device>_ac_input`) | `0x06` | Assumed (no feedback) | Enables AC input/charging path. |
| `Generator` (`switch.<device>_generator`) | `0x07` | Assumed (no feedback) | Enables generator input path. |
| `Buzzer` (`switch.<device>_buzzer`) | `0x09` | Assumed (no feedback) | Turns the device buzzer on/off. |

Assumed switches optimistically reflect the last command because the device does not expose their state.

## Selects (dropdowns)

| Friendly name (suffix) | Options → payload/value | Command | State source |
| --- | --- | --- | --- |
| `Charge Mode` (`select.<device>_charge_mode`) | `Load First` → `0x01`/`1`; `PV2 Passthrough` → `0x00`/`0`; `Simultaneous Charge Discharge` → `0x02`/`2` | `0x0D` | Mirrors `config_mode` from system data. |
| `CT Polling Rate` (`select.<device>_ct_polling_rate`) | `Fastest (0)` → `0x00`/`0`; `Medium (1)` → `0x01`/`1`; `Slowest (2)` → `0x02`/`2` | `0x20` | Mirrors `ct_polling_rate` from 0x22. |

Selections are written immediately and reflected back on the next medium-poll response.

## Binary sensors

| Friendly name (suffix) | Class | Description |
| --- | --- | --- |
| `WiFi Connected` (`binary_sensor.<device>_wifi_connected`) | `connectivity` | Device reports Wi-Fi link up (0x03). |
| `MQTT Connected` (`binary_sensor.<device>_mqtt_connected`) | `connectivity` | Device reports its MQTT/cloud link up (0x03). |
| `Output 1 Active` (`binary_sensor.<device>_out1_active`) | `power` | Output 1 currently driving load (0x03). |
| `External 1 Connected` (`binary_sensor.<device>_extern1_connected`) | `connectivity` | External input port detected (0x03). |
| `Smart Meter Connected` (`binary_sensor.<device>_smart_meter_connected`) | `connectivity` | Paired CT/smart meter detected (0x13). |

## Sensors

### Battery & power

| Friendly name (suffix) | Unit/class | Source | Notes |
| --- | --- | --- | --- |
| `Battery Voltage` (`sensor.<device>_battery_voltage`) | V / `voltage` | 0x14 | Pack voltage. |
| `Battery Current` (`sensor.<device>_battery_current`) | A / `current` | 0x14 | Positive = charging. |
| `Battery Power` (`sensor.<device>_battery_power`) | W / `power` | Calculated | Voltage × current. |
| `Battery Power In` (`sensor.<device>_battery_power_in`) | W / `power` | Calculated | Charging power (clamped ≥0). |
| `Battery Power Out` (`sensor.<device>_battery_power_out`) | W / `power` | Calculated | Discharging power (clamped ≥0). |
| `Grid Power` (`sensor.<device>_grid_power`) | W / `power` | 0x03 | Backup/grid power (signed). |
| `Solar Power` (`sensor.<device>_solar_power`) | W / `power` | 0x03 | Solar/battery channel power (signed). |
| `Output 1 Power` (`sensor.<device>_out1_power`) | W / `power` | 0x03 | Instantaneous output power. |

### Energy counters & capacity

| Friendly name (suffix) | Unit/class | Source | Notes |
| --- | --- | --- | --- |
| `Daily Energy Charged` (`sensor.<device>_daily_energy_charged`) | kWh / `energy` (`total_increasing`) | 0x03 | Resets daily; increments while charging. |
| `Daily Energy Discharged` (`sensor.<device>_daily_energy_discharged`) | kWh / `energy` (`total_increasing`) | 0x03 | Resets daily. |
| `Monthly Energy Charged` (`sensor.<device>_monthly_energy_charged`) | kWh / `energy` (`total_increasing`) | 0x03 | Resets monthly. |
| `Monthly Energy Discharged` (`sensor.<device>_monthly_energy_discharged`) | kWh / `energy` (`total_increasing`) | 0x03 | Resets monthly. |
| `Total Energy Charged` (`sensor.<device>_total_energy_charged`) | kWh / `energy` (`total_increasing`) | 0x03 | Lifetime charge; use for Energy Dashboard “in”. |
| `Total Energy Discharged` (`sensor.<device>_total_energy_discharged`) | kWh / `energy` (`total_increasing`) | 0x03 | Lifetime discharge; use for Energy Dashboard “out”. |
| `Design Capacity` (`sensor.<device>_design_capacity`) | Wh / `energy` | 0x14 | Nominal capacity. |
| `Remaining Capacity` (`sensor.<device>_remaining_capacity`) | Wh / `energy_storage` | Calculated | `SOC × design_capacity`. |
| `Available Capacity` (`sensor.<device>_available_capacity`) | Wh / `energy_storage` | Calculated | `(100 − SOC) × design_capacity`. |

### Temperatures

| Friendly name (suffix) | Unit/class | Source | Notes |
| --- | --- | --- | --- |
| `Battery Temperature` (`sensor.<device>_battery_temp`) | °C / `temperature` | 0x14 | Pack temperature. |
| `Temperature Low` (`sensor.<device>_temp_low`) | °C / `temperature` | 0x03 | Lower observed temp. |
| `Temperature High` (`sensor.<device>_temp_high`) | °C / `temperature` | 0x03 | Upper observed temp. |
| `MOSFET Temperature` (`sensor.<device>_mosfet_temp`) | °C / `temperature` | 0x14 | Inverter MOSFET temp. |
| `Temperature Sensor 1`…`4` (`sensor.<device>_temp_sensor_<n>`) | °C / `temperature` | 0x14 | Auxiliary thermistors. |

### Diagnostics, limits, and runtime

| Friendly name (suffix) | Unit/class | Source | Notes |
| --- | --- | --- | --- |
| `System Status` (`sensor.<device>_system_status`) | number | 0x0D | Raw system status byte. |
| `Config Mode` (`sensor.<device>_config_mode`) | number | 0x1A | Mirrors charge-mode state. |
| `CT Polling Rate` (`sensor.<device>_ct_polling_rate`) | number | 0x22 | 0–2; configurable via select. |
| `Work Mode` (`sensor.<device>_work_mode`) | number | 0x03 | Operating mode (0–7). |
| `Product Code` (`sensor.<device>_product_code`) | number | 0x03 | Model identifier. |
| `Power Rating` (`sensor.<device>_power_rating`) | W / `power` | 0x03 | Rated power capacity. |
| `BMS Version` (`sensor.<device>_bms_version`) | number | 0x14 | Firmware revision of BMS. |
| `Voltage Limit` (`sensor.<device>_voltage_limit`) | V / `voltage` | 0x14 | Max charge voltage. |
| `Charge Current Limit` (`sensor.<device>_charge_current_limit`) | A / `current` | 0x14 | Max charging current. |
| `Discharge Current Limit` (`sensor.<device>_discharge_current_limit`) | A / `current` | 0x14 | Max discharge current. |
| `Error Code` (`sensor.<device>_error_code`) | number | 0x14 | Active error bitfield. |
| `Warning Code` (`sensor.<device>_warning_code`) | number | 0x14 | Active warning bitfield. |
| `Runtime` (`sensor.<device>_runtime_hours`) | h / `duration` (`total_increasing`) | 0x14 | Total runtime in hours. |

### Identity, state, and network

| Friendly name (suffix) | Type | Source | Notes |
| --- | --- | --- | --- |
| `Battery State` (`sensor.<device>_battery_state`) | text | Calculated | `charging` / `discharging` / `inactive` based on power flow. |
| `Device Type` (`sensor.<device>_device_type`) | text | 0x04 | e.g., `HMG-50`. |
| `Device ID` (`sensor.<device>_device_id`) | text | 0x04 | Device identifier string. |
| `Serial Number` (`sensor.<device>_serial_number`) | text | 0x04 | Hardware serial. |
| `MAC Address` (`sensor.<device>_mac_address`) | text | 0x04 | Bluetooth MAC address. |
| `Firmware Version` (`sensor.<device>_firmware_version`) | text | 0x04 | Firmware revision. |
| `Hardware Version` (`sensor.<device>_hardware_version`) | text | 0x04 | Hardware revision. |
| `WiFi SSID` (`sensor.<device>_wifi_ssid`) | text | 0x08 | Associated Wi‑Fi network. |
| `Network Info` (`sensor.<device>_network_info`) | text | 0x24 | Raw `ip/gate/mask/dns` string. |
| `IP Address` (`sensor.<device>_ip_address`) | text | 0x24 | Parsed from network info. |
| `Gateway` (`sensor.<device>_gateway`) | text | 0x24 | Parsed from network info. |
| `Subnet Mask` (`sensor.<device>_subnet_mask`) | text | 0x24 | Parsed from network info. |
| `DNS Server` (`sensor.<device>_dns_server`) | text | 0x24 | Parsed from network info. |
| `Meter IP` (`sensor.<device>_meter_ip`) | text | 0x21 | IP of external CT/meter (or `(not set)`). |

### Cell voltages

| Friendly name (suffix) | Unit/class | Source | Notes |
| --- | --- | --- | --- |
| `Cell 1 Voltage` … `Cell 16 Voltage` (`sensor.<device>_cell_<n>_voltage`) | V / `voltage` | 0x14 | Per-cell voltages; diagnostic category. |

These sensors are marked as diagnostic entities where appropriate so they can be hidden from the dashboard if desired.
