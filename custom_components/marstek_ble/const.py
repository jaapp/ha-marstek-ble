"""Constants for the Marstek BLE integration."""

DOMAIN = "marstek_ble"

CONF_POLL_INTERVAL = "poll_interval"

# BLE Service and Characteristic UUIDs
SERVICE_UUID = "0000ff00-0000-1000-8000-00805f9b34fb"
CHAR_WRITE_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
CHAR_NOTIFY_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"

# Device name prefixes for discovery
# MST_ACCP_ = Hardware v2 (Venus E)
# MST_VNSE3_ = Hardware v3
DEVICE_PREFIXES = ("MST_ACCP_", "MST_VNSE3_")

# Update intervals (seconds)
UPDATE_INTERVAL_FAST = 10  # Runtime info, BMS data
UPDATE_INTERVAL_MEDIUM = 60  # System data, WiFi, config
UPDATE_INTERVAL_SLOW = 300  # Timer info, logs

DEFAULT_POLL_INTERVAL = UPDATE_INTERVAL_FAST
MIN_POLL_INTERVAL = 5
MAX_POLL_INTERVAL = 60

# Command codes
CMD_RUNTIME_INFO = 0x03
CMD_DEVICE_INFO = 0x04
CMD_EPS_MODE = 0x05
CMD_AC_INPUT = 0x06
CMD_GENERATOR = 0x07
CMD_WIFI_SSID = 0x08
CMD_BUZZER = 0x09
# Note: 0x0D is dual-purpose - reads system data, writes charge mode
CMD_CHARGE_MODE = 0x0D
CMD_SYSTEM_DATA = 0x0D
CMD_OUTPUT_CONTROL = 0x0E
CMD_ADAPTIVE_MODE = 0x11
CMD_TIMER_INFO = 0x13
CMD_BMS_DATA = 0x14
CMD_POWER_MODE = 0x15
CMD_AC_POWER = 0x16
CMD_TOTAL_POWER = 0x17
CMD_CONFIG_DATA = 0x1A
CMD_LOGS = 0x1C
CMD_CT_POLLING_RATE_WRITE = 0x20
CMD_CT_POLLING_RATE = 0x22
CMD_METER_IP = 0x21
CMD_NETWORK_INFO = 0x24
CMD_REBOOT = 0x25
CMD_LOCAL_API_STATUS = 0x28

# Frame structure
FRAME_START = 0x73
FRAME_TYPE = 0x23

# Logging / diagnostics
TURBO_LOG_MODE = True

COMMAND_NAMES: dict[int, str] = {
    CMD_RUNTIME_INFO: "runtime_info",
    CMD_DEVICE_INFO: "device_info",
    CMD_EPS_MODE: "eps_mode",
    CMD_AC_INPUT: "ac_input",
    CMD_GENERATOR: "generator",
    CMD_WIFI_SSID: "wifi_ssid",
    CMD_BUZZER: "buzzer",
    CMD_CHARGE_MODE: "charge_mode",
    CMD_SYSTEM_DATA: "system_data",
    CMD_OUTPUT_CONTROL: "output_control",
    CMD_ADAPTIVE_MODE: "adaptive_mode",
    CMD_TIMER_INFO: "timer_info",
    CMD_BMS_DATA: "bms_data",
    CMD_POWER_MODE: "power_mode",
    CMD_AC_POWER: "ac_power",
    CMD_TOTAL_POWER: "total_power",
    CMD_CONFIG_DATA: "config_data",
    CMD_LOGS: "logs",
    CMD_CT_POLLING_RATE_WRITE: "ct_polling_rate_write",
    CMD_CT_POLLING_RATE: "ct_polling_rate",
    CMD_METER_IP: "meter_ip",
    CMD_NETWORK_INFO: "network_info",
    CMD_REBOOT: "reboot",
    CMD_LOCAL_API_STATUS: "local_api_status",
}
