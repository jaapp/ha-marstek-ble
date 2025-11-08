# Marstek BLE Proxy Verification Script

This directory now contains the ESPHome-proxy based verification script that lives in
`marstek_basic_info.py`. Unlike the previous macOS/Bleak harness, this tool talks to the
same ESPHome Bluetooth proxy that Home Assistant uses so you can quickly confirm whether
HA is even receiving connectable advertisements and basic telemetry.

## Requirements

- Python 3.11+ (3.10 works as well but match your HA environment when possible)
- Access to the ESPHome Bluetooth proxy that is forwarding your Marstek batteries
- The proxy's API noise key (the same base64 value configured in ESPHome)

Install the single dependency from this directory:

```bash
cd standalone_test
python3 -m pip install -r requirements.txt
```

> Tip: export `PIP_REQUIRE_VIRTUALENV=true` and create a venv if you do not want global
> installs: `python3 -m venv .venv && source .venv/bin/activate`.

## Basic Usage

```bash
python3 marstek_basic_info.py \
  --host 192.168.7.44 \
  --noise-psk "base64-noise-key" \
  --target MST_d7c4
```

The script will:

1. Connect to the proxy via `aioesphomeapi`
2. Request active BLE scanning (or passive if you set `--scan-mode passive`)
3. Locate targets either by exact advertisement name (`--target`) or by `--name-prefix`
4. Establish BLE tunnels through the proxy, send the Marstek/HM 0x04 "Device Info" command
5. Print a per-device summary plus a table that mirrors what Home Assistant should see

## Discovering Devices

- Provide `--target` multiple times to query specific names or MACs
- If you omit `--target`, the script uses `--name-prefix` (default `MST_`) and reads up to
  `--max-devices` advertisements before attempting connections
- Set `--case-sensitive-prefix` if you care about the exact casing of advertisement names

You can also inspect the proxy's advertisements for troubleshooting:

```bash
python3 marstek_basic_info.py --log-advertisements 5 --log-level DEBUG
```

## Timeouts and Retries

- `--scan-timeout` controls how long we wait to resolve the requested devices
- `--connect-timeout` is applied to each BLE tunnel establishment
- `--command-timeout` bounds the wait time for the 0x04 reply once connected

Raising the connect timeout is handy when HA logs `Failed to establish an encrypted
connection to the Bluetooth proxy`.

## Proxy Configuration Notes

- The noise PSK can be pulled from ESPHome logs or `secrets.yaml`; alternatively set the
  `MARSTEK_PROXY_NOISE_PSK` environment variable and skip `--noise-psk`
- Use `--port` if your proxy does not listen on the default 6053
- `--scan-mode passive` mirrors HA's default behavior if you want to reproduce it exactly

## Output Interpretation

At the end of a run you will see:

- A device-by-device block that contains the raw decoded key/value pairs returned by
  command 0x04
- A consolidated "Battery Summary" table showing name, MAC, RSSI, state, firmware, etc.
- Any failures are reported inline with a status dict (e.g., timeouts, API connection
  problems, or BLE tunnels that never formed)

If the script itself fails to locate or connect to the battery, Home Assistant will do
no better—use this as the first line of debugging when HA stops talking to your proxy.

## Advanced Debugging Flags

- `--log-scanner-state` prints scanner state changes from the proxy so you can confirm
  whether active scans were accepted.
- `--raw-advertisements` subscribes to raw BLE advertisements instead of ESPHome's decoded
  view; combine with `--log-advertisements -1` to capture everything the proxy sees.
- `--log-level DEBUG` enables verbose logging from `aioesphomeapi` plus this script's
  tracing of connection attempts and frame parsing.
