#!/usr/bin/env python3
"""Quick test script to read Marstek Venus E device info via an ESPHome BLE proxy.

This script connects to the ESPHome Bluetooth proxy using aioesphomeapi, establishes
direct BLE sessions with the batteries, sends the Marstek/HM 0x04 "Device Info"
command (documented in https://github.com/rweijnen/marstek-venus-monitor), and
prints the key/value response for each device that responds.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

from aioesphomeapi import APIClient
from aioesphomeapi.ble_defs import BLEConnectionError, ESP_CONNECTION_ERROR_DESCRIPTION
from aioesphomeapi.core import (
    APIConnectionError,
    BluetoothConnectionDroppedError,
    TimeoutAPIError,
    to_human_readable_address,
)
from aioesphomeapi.model import (
    APIVersion,
    BluetoothLEAdvertisement,
    BluetoothScannerMode,
    BluetoothScannerStateResponse as BluetoothScannerStateResponseModel,
    DeviceInfo,
)


START_BYTE = 0x73
IDENTIFIER_BYTE = 0x23
SERVICE_UUID = "0000ff00-0000-1000-8000-00805f9b34fb"
TX_CHAR_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
RX_CHAR_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"
DEFAULT_PROXY_HOST = "192.168.7.44"
DEFAULT_PROXY_PORT = 6053
DEFAULT_NOISE_PSK = "istH+Pnjbxgury0LoTU4UBzqchEbp70upkgwQHb9bBQ="
DEFAULT_NAME_PREFIX = "MST_"
MAC_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}$")
ADDRESS_TYPE_LABELS = {0: "public", 1: "random"}


class FrameBuffer:
    """Accumulator for Marstek protocol frames (0x73 ... len ... checksum)."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes | bytearray) -> list[bytes]:
        frames: list[bytes] = []
        self._buffer.extend(chunk)
        while True:
            if len(self._buffer) < 2:
                break
            if self._buffer[0] != START_BYTE:
                # Drop noise until we see the next frame start byte
                self._buffer.pop(0)
                continue
            frame_len = self._buffer[1]
            if frame_len <= 0:
                self._buffer.pop(0)
                continue
            if len(self._buffer) < frame_len:
                break
            frame = bytes(self._buffer[:frame_len])
            del self._buffer[:frame_len]
            frames.append(frame)
        return frames


@dataclass
class TargetSpec:
    """User provided target specification (exact MAC or advertisement name)."""

    label: str
    mac_int: Optional[int] = None
    name_upper: Optional[str] = None

    @classmethod
    def from_string(cls, raw: str) -> "TargetSpec":
        raw = raw.strip()
        if not raw:
            raise ValueError("Target identifiers cannot be blank")
        if MAC_PATTERN.fullmatch(raw):
            mac = int(raw.replace(":", "").replace("-", ""), 16)
            return cls(label=raw, mac_int=mac)
        # Allow decimal or hex numeric addresses (esp32 reports MAC as uint64)
        if raw.startswith(("0x", "0X")):
            try:
                mac = int(raw, 16)
            except ValueError:
                pass
            else:
                return cls(label=to_human_readable_address(mac), mac_int=mac)
        if raw.isdigit():
            mac = int(raw, 10)
            return cls(label=to_human_readable_address(mac), mac_int=mac)
        return cls(label=raw, name_upper=raw.upper())

    def matches(self, advertisement: BluetoothLEAdvertisement) -> bool:
        return self.matches_simple(advertisement.address, advertisement.name or "")

    def matches_simple(self, address: int, name: str) -> bool:
        if self.mac_int is not None and address != self.mac_int:
            return False
        if self.name_upper is not None:
            adv_name = (name or "").strip().upper()
            if adv_name != self.name_upper:
                return False
        return True


@dataclass
class ResolvedDevice:
    """Details for a device discovered via BLE advertisements."""

    label: str
    address: int
    address_type: int
    rssi: int
    name: str

    @property
    def address_human(self) -> str:
        return to_human_readable_address(self.address)

    @property
    def address_type_label(self) -> str:
        return ADDRESS_TYPE_LABELS.get(self.address_type, f"unknown({self.address_type})")


@dataclass(frozen=True)
class CommandSpec:
    key: str
    command_id: int
    name: str
    parser: Callable[[bytes], Dict[str, Any]]
    timeout: Optional[float] = None


class CommandType:
    RUNTIME_INFO = 0x03
    DEVICE_INFO = 0x04
    WIFI_INFO = 0x08
    SYSTEM_DATA = 0x0D
    BLE_EVENT_LOG = 0x13
    BMS_DATA = 0x14
    HM_SUMMARY = 0x1A
    HM_EVENT_LOG = 0x1C
    METER_IP = 0x21
    NETWORK_INFO = 0x24

def create_command_frame(command: int, payload: bytes | bytearray | None = None) -> bytes:
    payload_bytes = bytes(payload or b"")
    total_length = len(payload_bytes) + 5  # 0x73, len, 0x23, cmd, checksum
    frame = bytearray([START_BYTE, total_length, IDENTIFIER_BYTE, command])
    frame.extend(payload_bytes)
    checksum = 0
    for byte in frame:
        checksum ^= byte
    frame.append(checksum)
    return bytes(frame)


def parse_frame(frame: bytes) -> tuple[int, bytes]:
    if len(frame) < 5:
        raise ValueError(f"Frame too short ({len(frame)} bytes)")
    if frame[0] != START_BYTE:
        raise ValueError("Frame missing start byte 0x73")
    expected_len = frame[1]
    if expected_len != len(frame):
        raise ValueError(f"Length mismatch (header {expected_len} vs {len(frame)})")
    checksum = 0
    for byte in frame[:-1]:
        checksum ^= byte
    if checksum != frame[-1]:
        raise ValueError("Checksum mismatch")
    if frame[2] != IDENTIFIER_BYTE:
        raise ValueError(f"Unexpected identifier byte 0x{frame[2]:02X}")
    command = frame[3]
    payload = frame[4:-1]
    return command, payload


def connection_error_to_text(error: int) -> str:
    if error in ESP_CONNECTION_ERROR_DESCRIPTION:
        return ESP_CONNECTION_ERROR_DESCRIPTION[BLEConnectionError(error)]
    return f"0x{error:02X}"


async def discover_devices(
    client: APIClient,
    targets: list[TargetSpec],
    scan_timeout: float,
    auto_prefix: str,
    auto_limit: int,
    log_adv_limit: int,
    case_sensitive_prefix: bool,
    use_raw_ads: bool,
    keep_subscription: bool,
) -> tuple[list[ResolvedDevice], Optional[Callable[[], None]]]:
    """Subscribe to BLE advertisements until the requested devices are seen.

    Returns the matched devices along with the unsubscribe callback (when the caller
    asks to keep the subscription active). The caller is responsible for invoking the
    unsubscribe function once all BLE activity is finished.
    """

    loop = asyncio.get_running_loop()
    event = asyncio.Event()
    resolved: dict[str, ResolvedDevice] = {}
    auto_found: list[ResolvedDevice] = []
    seen_auto_addresses: set[int] = set()
    adv_log_count = 0
    prefix_source = auto_prefix or ""
    auto_prefix_compare = prefix_source if case_sensitive_prefix else prefix_source.upper()

    def _maybe_done() -> bool:
        if targets:
            return len(resolved) == len(targets)
        return len(auto_found) >= auto_limit

    def log_advertisement(
        address: int,
        address_type: int,
        rssi: int,
        adv_name: str,
        service_uuids: list[str] | None,
        raw_data: bytes | None,
    ) -> None:
        nonlocal adv_log_count
        if not log_adv_limit:
            return
        if log_adv_limit > 0 and adv_log_count >= log_adv_limit:
            return
        adv_log_count += 1
        uuid_text = ",".join(service_uuids or []) if service_uuids else "<none>"
        extra = f" raw={raw_data.hex()}" if raw_data else ""
        logging.info(
            "BLE ADV #%d: addr=%s (%s) RSSI=%d name='%s' uuids=%s%s",
            adv_log_count,
            to_human_readable_address(address),
            ADDRESS_TYPE_LABELS.get(address_type, address_type),
            rssi,
            adv_name,
            uuid_text,
            extra,
        )

    def process_advertisement(
        address: int,
        address_type: int,
        rssi: int,
        adv_name: str,
        service_uuids: list[str] | None = None,
        raw_data: bytes | None = None,
    ) -> None:
        log_advertisement(address, address_type, rssi, adv_name, service_uuids, raw_data)

        updated = False
        if targets:
            for spec in targets:
                if spec.label in resolved:
                    continue
                if spec.matches_simple(address, adv_name):
                    resolved[spec.label] = ResolvedDevice(
                        label=spec.label,
                        address=address,
                        address_type=address_type,
                        rssi=rssi,
                        name=adv_name or spec.label,
                    )
                    logging.info(
                        "Matched %s at %s (%s, RSSI %d)",
                        spec.label,
                        resolved[spec.label].address_human,
                        adv_name or "unnamed",
                        rssi,
                    )
                    updated = True
        else:
            adv_name_display = adv_name.strip()
            adv_name_compare = (
                adv_name_display if case_sensitive_prefix else adv_name_display.upper()
            )
            if auto_prefix_compare and not adv_name_compare.startswith(auto_prefix_compare):
                return
            if address in seen_auto_addresses:
                return
            seen_auto_addresses.add(address)
            label = adv_name_display or to_human_readable_address(address)
            auto_found.append(
                ResolvedDevice(
                    label=label,
                    address=address,
                    address_type=address_type,
                    rssi=rssi,
                    name=label,
                )
            )
            logging.info(
                "Auto-discovered %s at %s (%s RSSI %d)",
                label,
                auto_found[-1].address_human,
                auto_found[-1].address_type_label,
                rssi,
            )
        if updated:
            event.set()

    def _handle_adv(advertisement: BluetoothLEAdvertisement) -> None:
        process_advertisement(
            advertisement.address,
            getattr(advertisement, "address_type", 0),
            advertisement.rssi,
            advertisement.name or "",
            advertisement.service_uuids or [],
            None,
        )

    def decode_name_from_raw(data: bytes) -> str:
        idx = 0
        while idx < len(data):
            length = data[idx]
            if length == 0:
                break
            if idx + length >= len(data):
                break
            field_type = data[idx + 1]
            field_value = data[idx + 2 : idx + 1 + length]
            if field_type in (0x08, 0x09):  # short or complete name
                try:
                    return field_value.decode("utf-8", errors="ignore")
                except Exception:
                    return ""
            idx += 1 + length
        return ""

    def _handle_raw(msg) -> None:
        for adv in msg.advertisements:
            data_bytes = bytes(adv.data)
            process_advertisement(
                adv.address,
                getattr(adv, "address_type", 0),
                adv.rssi,
                decode_name_from_raw(data_bytes),
                None,
                data_bytes,
            )

    if use_raw_ads:
        unsub = client.subscribe_bluetooth_le_raw_advertisements(_handle_raw)
    else:
        unsub = client.subscribe_bluetooth_le_advertisements(_handle_adv)
    try:
        async with asyncio.timeout(scan_timeout):
            while not _maybe_done():
                event.clear()
                await event.wait()
    except asyncio.TimeoutError:
        logging.warning("Timed out waiting for BLE advertisements after %.1fs", scan_timeout)
    finally:
        if not keep_subscription:
            unsub()

    if targets:
        missing = [spec.label for spec in targets if spec.label not in resolved]
        if missing:
            raise RuntimeError(f"Did not see advertisements for: {', '.join(missing)}")
        devices = [resolved[spec.label] for spec in targets]
        return (devices, unsub if keep_subscription else None)

    if not auto_found:
        raise RuntimeError(
            f"No BLE devices with prefix '{auto_prefix}' were seen during the {scan_timeout:.1f}s scan window"
        )
    if len(auto_found) < auto_limit:
        logging.warning(
            "Only discovered %d/%d devices with prefix '%s'; proceeding with the ones that responded",
            len(auto_found),
            auto_limit,
            auto_prefix,
        )
    devices = auto_found[:auto_limit]
    return (devices, unsub if keep_subscription else None)


class BLEDeviceSession:
    """Manage a BLE connection to a single battery via the ESPHome proxy."""

    def __init__(
        self,
        api_client: APIClient,
        device: ResolvedDevice,
        ble_feature_flags: int,
        connect_timeout: float,
        command_timeout: float,
    ) -> None:
        self._api = api_client
        self._device = device
        self._ble_feature_flags = ble_feature_flags
        self._connect_timeout = connect_timeout
        self._command_timeout = command_timeout

        self._connection_unsub: Optional[Callable[[], None]] = None
        self._stop_notify: Optional[Callable[[], Coroutine[Any, Any, None]]] = None
        self._notify_remove: Optional[Callable[[], None]] = None
        self._pending: Dict[int, deque[asyncio.Future[bytes]]] = defaultdict(deque)
        self._decoder = FrameBuffer()
        self._tx_handle: Optional[int] = None
        self._rx_handle: Optional[int] = None
        self._loop = asyncio.get_running_loop()
        self._closed = False

    async def __aenter__(self) -> "BLEDeviceSession":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def connect(self) -> None:
        first_event: asyncio.Future[Tuple[bool, int]] = self._loop.create_future()

        def _on_state(connected: bool, mtu: int, error: int) -> None:
            logging.debug(
                "Connection update for %s: connected=%s mtu=%s error=%s",
                self._device.label,
                connected,
                mtu,
                error,
            )
            if not first_event.done():
                first_event.set_result((connected, error))

        self._connection_unsub = await self._api.bluetooth_device_connect(
            address=self._device.address,
            on_bluetooth_connection_state=_on_state,
            timeout=self._connect_timeout,
            feature_flags=self._ble_feature_flags,
            has_cache=False,
            address_type=self._device.address_type,
        )

        connected, error = await first_event
        if not connected:
            raise RuntimeError(
                f"BLE connection to {self._device.label} failed: {connection_error_to_text(error)}"
            )

        services = await self._api.bluetooth_gatt_get_services(self._device.address)
        target_service = next(
            (svc for svc in services.services if svc.uuid.lower() == SERVICE_UUID),
            None,
        )
        if not target_service:
            raise RuntimeError("Marstek service 0xFF00 not found in GATT table")

        tx_char = next(
            (char for char in target_service.characteristics if char.uuid.lower() == TX_CHAR_UUID),
            None,
        )
        rx_char = next(
            (char for char in target_service.characteristics if char.uuid.lower() == RX_CHAR_UUID),
            None,
        )
        if not tx_char or not rx_char:
            raise RuntimeError("Marstek characteristics FF01/FF02 not found")

        self._tx_handle = tx_char.handle
        self._rx_handle = rx_char.handle

        self._stop_notify, self._notify_remove = await self._api.bluetooth_gatt_start_notify(
            self._device.address,
            self._rx_handle,
            self._handle_notification,
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        for queue in self._pending.values():
            while queue:
                fut = queue.popleft()
                if not fut.done():
                    fut.set_exception(RuntimeError("Session closed"))
        self._pending.clear()

        if self._stop_notify:
            try:
                await self._stop_notify()
            except Exception as exc:
                logging.debug("Failed to stop notify for %s: %s", self._device.label, exc)
        if self._notify_remove:
            self._notify_remove()

        try:
            await self._api.bluetooth_device_disconnect(self._device.address)
        except TimeoutAPIError:
            logging.debug("Timeout while disconnecting %s", self._device.label)
        except BluetoothConnectionDroppedError:
            logging.debug("Connection to %s dropped before disconnect", self._device.label)
        except APIConnectionError as exc:
            logging.debug("API connection error while disconnecting %s: %s", self._device.label, exc)

        if self._connection_unsub:
            self._connection_unsub()

    def _handle_notification(self, handle: int, data: bytearray) -> None:
        for frame in self._decoder.feed(data):
            try:
                command, payload = parse_frame(frame)
            except ValueError as exc:
                logging.warning("Discarding malformed frame from %s: %s", self._device.label, exc)
                continue

            queue = self._pending.get(command)
            if queue:
                future = queue.popleft()
                if not future.done():
                    future.set_result(payload)
            else:
                logging.debug(
                    "Received unsolicited frame cmd=0x%02X (%d bytes) from %s",
                    command,
                    len(payload),
                    self._device.label,
                )

    async def send_command(self, command: int, description: str, payload: bytes | bytearray = b"", timeout: Optional[float] = None) -> bytes:
        if self._tx_handle is None:
            raise RuntimeError("TX characteristic not initialized")

        cmd_timeout = timeout or self._command_timeout
        queue = self._pending[command]
        future: asyncio.Future[bytes] = self._loop.create_future()
        queue.append(future)

        frame = create_command_frame(command, payload)
        try:
            await self._api.bluetooth_gatt_write(
                self._device.address,
                self._tx_handle,
                frame,
                response=False,
            )
            return await asyncio.wait_for(future, timeout=cmd_timeout)
        except Exception:
            if future in queue:
                queue.remove(future)
            raise

COMMAND_DELAY_SECONDS = 0.15
MAX_CONSECUTIVE_FAILURES = 3


async def collect_device_data(
    client: APIClient,
    device: ResolvedDevice,
    ble_feature_flags: int,
    connect_timeout: float,
    command_timeout: float,
) -> dict[str, Dict[str, Any]]:
    """Collect data for all safe commands from a single device."""

    session = BLEDeviceSession(
        client,
        device,
        ble_feature_flags=ble_feature_flags,
        connect_timeout=connect_timeout,
        command_timeout=command_timeout,
    )
    await session.connect()
    results: dict[str, Dict[str, Any]] = {}

    try:
        consecutive_failures = 0
        for spec in SAFE_COMMANDS:
            logging.info("↪️  %s: reading %s (0x%02X)", device.label, spec.name, spec.command_id)
            try:
                payload = await session.send_command(
                    spec.command_id,
                    spec.name,
                    timeout=spec.timeout,
                )
                parsed = spec.parser(payload)
                results[spec.key] = parsed
                logging.info("✅ %s: %s read", device.label, spec.name)
                consecutive_failures = 0
            except asyncio.TimeoutError:
                msg = f"Timeout waiting for {spec.name} response"
                logging.error("%s: %s", device.label, msg)
                results[spec.key] = {"error": msg}
                consecutive_failures += 1
            except Exception as exc:
                logging.error("%s: failed to read %s: %s", device.label, spec.name, exc)
                results[spec.key] = {"error": str(exc)}
                consecutive_failures += 1

            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logging.error(
                    "%s: aborting session after %d consecutive failures",
                    device.label,
                    consecutive_failures,
                )
                break

            await asyncio.sleep(COMMAND_DELAY_SECONDS)
        return results
    finally:
        await session.close()


def _read_le(payload: bytes, offset: int, size: int, *, signed: bool = False) -> Optional[int]:
    if offset < 0 or offset + size > len(payload):
        return None
    return int.from_bytes(payload[offset : offset + size], "little", signed=signed)


def _read_be(payload: bytes, offset: int, size: int) -> Optional[int]:
    if offset < 0 or offset + size > len(payload):
        return None
    return int.from_bytes(payload[offset : offset + size], "big")


def _read_str(payload: bytes, offset: int, length: int) -> str:
    if offset < 0 or offset + length > len(payload):
        length = max(0, len(payload) - offset)
    return payload[offset : offset + length].decode("utf-8", errors="ignore").rstrip("\x00").strip()


def _safe_timestamp(year: int, month: int, day: int, hour: int, minute: int) -> Optional[datetime]:
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def _format_dt(dt: Optional[datetime]) -> str:
    return dt.isoformat(sep=" ") if dt else "Unknown"


def parse_runtime_info(payload: bytes) -> Dict[str, Any]:
    if len(payload) < 0x68:
        raise ValueError(f"Runtime payload too short ({len(payload)} bytes)")

    def u16(offset: int) -> int:
        return _read_le(payload, offset, 2) or 0

    def s16(offset: int) -> int:
        return _read_le(payload, offset, 2, signed=True) or 0

    def u32(offset: int) -> int:
        return _read_le(payload, offset, 4) or 0

    def s32(offset: int) -> int:
        return _read_le(payload, offset, 4, signed=True) or 0

    work_mode = payload[4]
    status_b = payload[5]
    status_c = payload[6]
    status_d = payload[7]

    def work_mode_label(mode: int) -> str:
        labels = {
            0x00: "Auto",
            0x01: "Standby",
            0x02: "Charging",
            0x03: "Sell Electricity",
            0x04: "UPS/EPS",
            0x05: "Force Charge",
            0x06: "Grid Export",
            0x07: "Schedule/TOU",
        }
        return labels.get(mode, f"Unknown ({mode})")

    status_flags = {
        "p1_meter_connected": bool(status_b & 0x02),
        "eco_tracker_connected": bool(status_b & 0x04),
        "network_active": bool(status_b & 0x08),
        "work_mode_state": status_c & 0x0F,
        "data_quality_ok": bool(status_c & 0x10),
        "error_state": status_d & 0x07,
        "server_connected": bool(status_d & 0x08),
        "http_active": bool(status_d & 0x10),
        "raw": {"status_b": status_b, "status_c": status_c, "status_d": status_d},
    }

    firmware_build = _read_str(payload, 0x51, 12)
    if firmware_build and firmware_build.isdigit():
        fb = firmware_build.ljust(12, "0")[:12]
        firmware_build = f"{fb[:4]}-{fb[4:6]}-{fb[6:8]} {fb[8:10]}:{fb[10:12]}"

    return {
        "grid_power_w": s16(0x00),
        "battery_power_w": s16(0x02),
        "work_mode_id": work_mode,
        "work_mode_label": work_mode_label(work_mode),
        "status_b": status_b,
        "status_c": status_c,
        "status_d": status_d,
        "status_flags": status_flags,
        "product_code": u16(0x0C),
        "power_rating_w": u16(0x4A),
        "daily_charge_kwh": u32(0x0E) / 100,
        "monthly_charge_kwh": u32(0x12) / 1000,
        "daily_discharge_kwh": u32(0x16) / 100,
        "monthly_discharge_kwh": u32(0x1A) / 100,
        "total_charge_kwh": u32(0x29) / 100,
        "total_discharge_kwh": u32(0x2D) / 100,
        "firmware_version": f"v{payload[0x4C]}.{payload[0x4D]}",
        "build_code": _read_be(payload, 0x4E, 2) or 0,
        "firmware_build": firmware_build or "Unknown",
        "reserved_counter": u16(0x5E),
        "parallel_status": payload[0x5F],
        "generator_enabled": payload[0x60],
        "calibration_tag_1": u16(0x62),
        "calibration_tag_2": u16(0x64),
        "api_port": u16(0x66),
    }


def parse_device_info(payload: bytes) -> Dict[str, Any]:
    text = payload.rstrip(b"\x00").decode("utf-8", errors="ignore")
    info: Dict[str, Any] = {}
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        info[key.strip()] = value.strip()
    return info


def parse_wifi_info(payload: bytes) -> Dict[str, Any]:
    ssid = payload.decode("utf-8", errors="ignore").strip()
    return {"ssid": ssid or "Not connected", "connected": bool(ssid)}


def parse_system_data(payload: bytes) -> Dict[str, Any]:
    def val(offset: int) -> int:
        return _read_le(payload, offset, 2) or 0

    temps = [val(8 + 2 * i) for i in range(5)]
    return {
        "system_status": payload[0] if payload else 0,
        "line_frequency_hz": val(2),
        "ac_voltage_v": val(4),
        "reserved": val(6),
        "temperatures_c": temps,
        "work_mode": payload[18] if len(payload) > 18 else 0,
    }


def _parse_records(payload: bytes, record_size: int, parser: Callable[[bytes], Optional[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for offset in range(0, len(payload) - record_size + 1, record_size):
        chunk = payload[offset : offset + record_size]
        if all(b == 0 for b in chunk):
            continue
        parsed = parser(chunk)
        if parsed:
            records.append(parsed)
    return records


def parse_error_log(payload: bytes) -> Dict[str, Any]:
    def parser(chunk: bytes) -> Optional[Dict[str, Any]]:
        year = _read_le(chunk, 0, 2) or 0
        month = chunk[2]
        day = chunk[3]
        hour = chunk[4]
        minute = chunk[5]
        code = chunk[6]
        details = chunk[7:14]
        timestamp = _safe_timestamp(year, month, day, hour, minute)
        return {
            "timestamp": _format_dt(timestamp),
            "year": year,
            "month": month,
            "day": day,
            "hour": hour,
            "minute": minute,
            "error_code": code,
            "data": details.hex(),
        }

    record_payload = payload[14:] if len(payload) > 14 else b""
    records = _parse_records(record_payload, 14, parser)
    latest = records[-1] if records else None
    return {
        "record_count": len(records),
        "latest_error": latest or {},
    }


def parse_bms_data(payload: bytes) -> Dict[str, Any]:
    if len(payload) < 80:
        raise ValueError(f"BMS payload too short ({len(payload)} bytes)")

    def u16(offset: int) -> int:
        return _read_le(payload, offset, 2) or 0

    def s16(offset: int) -> int:
        return _read_le(payload, offset, 2, signed=True) or 0

    cell_voltages: List[str] = []
    for offset in range(48, min(len(payload), 82), 2):
        voltage = u16(offset)
        if 0 < voltage < 5000:
            cell_voltages.append(f"{voltage / 1000:.3f}")

    return {
        "bms_version": u16(0),
        "voltage_limit_v": u16(2) / 10,
        "charge_current_limit_a": u16(4) / 10,
        "discharge_current_limit_a": s16(6) / 10,
        "remaining_capacity_pct": u16(8),
        "state_of_health_pct": u16(10),
        "design_capacity_wh": u16(12),
        "voltage_v": u16(14) / 100,
        "battery_current_a": s16(16) / 10,
        "battery_temperature_c": u16(18),
        "error_code": u16(26),
        "warning_code": _read_le(payload, 28, 4) or 0,
        "runtime_ms": _read_le(payload, 32, 4) or 0,
        "mosfet_temperature_c": u16(38),
        "temperature_1_c": u16(40),
        "temperature_2_c": u16(42),
        "temperature_3_c": u16(44),
        "temperature_4_c": u16(46),
        "cell_voltages_v": cell_voltages,
    }


def parse_config_data(payload: bytes) -> Dict[str, Any]:
    result = {
        "mode": payload[0] if len(payload) > 0 else 0,
        "flags": payload[1] if len(payload) > 1 else 0,
        "config_status": payload[4] if len(payload) > 4 else 0,
        "status_bytes": payload[5:8].hex() if len(payload) >= 8 else "",
        "enable_flag_1": bool(len(payload) > 8 and payload[8]),
        "enable_flag_2": bool(len(payload) > 12 and payload[12]),
        "config_value": payload[16] if len(payload) > 16 else 0,
    }
    return result


def parse_event_log(payload: bytes) -> Dict[str, Any]:
    def parser(chunk: bytes) -> Optional[Dict[str, Any]]:
        year = _read_le(chunk, 0, 2) or 0
        month, day, hour, minute, event_type = chunk[2:7]
        code = _read_le(chunk, 7, 2) or 0
        timestamp = _safe_timestamp(year, month, day, hour, minute)
        return {
            "timestamp": _format_dt(timestamp),
            "year": year,
            "month": month,
            "day": day,
            "hour": hour,
            "minute": minute,
            "type": event_type,
            "code": code,
        }

    record_payload = payload[14:] if len(payload) > 14 else b""
    records = _parse_records(record_payload, 9, parser)
    latest = records[-1] if records else None
    return {
        "record_count": len(records),
        "latest_event": latest or {},
    }


def parse_meter_ip(payload: bytes) -> Dict[str, Any]:
    ip_bytes = payload[:16]
    if not ip_bytes:
        return {"ip_address": "Not configured", "configured": False}
    if all(b == 0 for b in ip_bytes) or all(b == 0xFF for b in ip_bytes):
        return {"ip_address": "Not configured", "configured": False}
    ip = bytes(ip_bytes).split(b"\x00", 1)[0].decode("utf-8", errors="ignore")
    return {"ip_address": ip or "Not configured", "configured": bool(ip)}


def parse_network_info(payload: bytes) -> Dict[str, Any]:
    config = payload.decode("utf-8", errors="ignore")
    result: Dict[str, Any] = {"raw": config}
    for part in config.split(","):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key in {"ip", "gate", "gateway", "mask", "dns"}:
            normalized = "gateway" if key == "gate" else key
            result[normalized] = value
    return result


SAFE_COMMANDS: List[CommandSpec] = [
    CommandSpec("runtime", CommandType.RUNTIME_INFO, "Runtime Info", parse_runtime_info),
    CommandSpec("device_info", CommandType.DEVICE_INFO, "Device Info", parse_device_info),
    CommandSpec("wifi", CommandType.WIFI_INFO, "WiFi Info", parse_wifi_info),
    CommandSpec("system_data", CommandType.SYSTEM_DATA, "System Data", parse_system_data),
    CommandSpec("error_log", CommandType.BLE_EVENT_LOG, "Error Codes", parse_error_log, timeout=20.0),
    CommandSpec("bms", CommandType.BMS_DATA, "BMS Data", parse_bms_data),
    CommandSpec("config", CommandType.HM_SUMMARY, "Config Data", parse_config_data),
    CommandSpec("event_log", CommandType.HM_EVENT_LOG, "Event Log", parse_event_log, timeout=20.0),
    CommandSpec("meter_ip", CommandType.METER_IP, "Meter IP", parse_meter_ip),
    CommandSpec("network", CommandType.NETWORK_INFO, "Network Info", parse_network_info),
]


def flatten_metrics(data: Dict[str, Any]) -> Dict[str, str]:
    flat: Dict[str, str] = {}

    def _flatten(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                key = f"{prefix}.{sub_key}" if prefix else sub_key
                _flatten(key, sub_value)
        elif isinstance(value, (list, tuple)):
            if value and isinstance(value[0], dict):
                for idx, item in enumerate(value):
                    key = f"{prefix}[{idx}]"
                    _flatten(key, item)
            else:
                flat[prefix] = ", ".join(str(item) for item in value)
        else:
            flat[prefix] = "" if value is None else str(value)

    _flatten("", data)
    return {k.lstrip("."): v for k, v in flat.items()}


def render_summary_table(device_labels: List[str], per_device_data: Dict[str, Dict[str, Any]]) -> str:
    flattened = {label: flatten_metrics(per_device_data.get(label, {})) for label in device_labels}
    row_keys: List[str] = []
    for label in device_labels:
        for key in flattened[label]:
            if key not in row_keys:
                row_keys.append(key)
    if not row_keys:
        return "No data collected."

    header = ["Metric"] + device_labels
    rows: List[List[str]] = []
    for key in row_keys:
        row = [key]
        for label in device_labels:
            row.append(flattened[label].get(key, ""))
        rows.append(row)

    widths = [len(col) for col in header]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))

    def fmt_row(values: List[str]) -> str:
        return " | ".join(val.ljust(widths[idx]) for idx, val in enumerate(values))

    separator = "-+-".join("-" * width for width in widths)
    lines = [fmt_row(header), separator]
    for row in rows:
        lines.append(fmt_row(row))
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> None:
    logging.info("Connecting to ESPHome proxy at %s:%d", args.host, args.port)
    noise_psk = args.noise_psk or os.getenv("MARSTEK_PROXY_NOISE_PSK") or DEFAULT_NOISE_PSK
    client = APIClient(
        args.host,
        args.port,
        password=None,
        noise_psk=noise_psk,
        client_info="marstek-ble-test",
    )
    await client.connect(login=True)
    scanner_state_unsub: Optional[Callable[[], None]] = None
    adv_unsub: Optional[Callable[[], None]] = None
    try:
        if args.log_scanner_state:
            def _on_scanner_state(state: BluetoothScannerStateResponseModel) -> None:
                state_name = state.state.name if state.state else "unknown"
                mode_name = state.mode.name if state.mode else "unknown"
                logging.info("Scanner state update: state=%s mode=%s", state_name, mode_name)

            scanner_state_unsub = client.subscribe_bluetooth_scanner_state(_on_scanner_state)

        device_info: DeviceInfo = await client.device_info()
        api_version = client.api_version or APIVersion(0, 0)
        logging.info(
            "Connected to %s (%s) running ESPHome %s",
            device_info.friendly_name or device_info.name or args.host,
            device_info.mac_address or "unknown mac",
            device_info.esphome_version or "unknown version",
        )
        ble_feature_flags = device_info.bluetooth_proxy_feature_flags_compat(api_version)
        if not ble_feature_flags:
            raise RuntimeError("ESPHome device does not expose Bluetooth proxy features")

        scanner_mode = (
            BluetoothScannerMode.ACTIVE if args.scan_mode == "active" else BluetoothScannerMode.PASSIVE
        )
        client.bluetooth_scanner_set_mode(scanner_mode)
        logging.info("Requested %s BLE scan mode on proxy", scanner_mode.name)

        targets = [TargetSpec.from_string(entry) for entry in args.target]
        devices, adv_unsub = await discover_devices(
            client,
            targets=targets,
            scan_timeout=args.scan_timeout,
            auto_prefix=args.name_prefix,
            auto_limit=max(1, args.max_devices),
            log_adv_limit=args.log_advertisements,
            case_sensitive_prefix=args.case_sensitive_prefix,
            use_raw_ads=args.raw_advertisements,
            keep_subscription=True,
        )
        logging.info("Will query %d device(s): %s", len(devices), ", ".join(d.label for d in devices))

        device_labels = [device.label for device in devices]
        per_device_data: Dict[str, Dict[str, Any]] = {}

        for device in devices:
            logging.info(
                "🔗 Establishing session with %s (%s, %s)",
                device.label,
                device.address_human,
                device.address_type_label,
            )
            try:
                collected = await collect_device_data(
                    client,
                    device,
                    ble_feature_flags=ble_feature_flags,
                    connect_timeout=args.connect_timeout,
                    command_timeout=args.command_timeout,
                )
                collected["device"] = {
                    "name": device.name,
                    "address": device.address_human,
                    "address_type": device.address_type_label,
                    "rssi_dbm": device.rssi,
                }
                per_device_data[device.label] = collected
            except Exception as exc:
                logging.error("Failed to collect data from %s: %s", device.label, exc)
                per_device_data[device.label] = {"status": {"error": str(exc)}}

        print("\n=== Battery Summary ===")
        print(render_summary_table(device_labels, per_device_data))
    finally:
        if adv_unsub:
            adv_unsub()
        await client.disconnect()
        if scanner_state_unsub:
            scanner_state_unsub()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch Marstek Venus E device info via ESPHome BLE proxy")
    parser.add_argument("--host", default=DEFAULT_PROXY_HOST, help="ESPHome device hostname or IP")
    parser.add_argument("--port", type=int, default=DEFAULT_PROXY_PORT, help="ESPHome API port (default 6053)")
    parser.add_argument(
        "--noise-psk",
        default=os.getenv("MARSTEK_PROXY_NOISE_PSK", DEFAULT_NOISE_PSK),
        help="ESPHome API noise encryption key (base64). Defaults to the provided proxy key or MARSTEK_PROXY_NOISE_PSK env var.",
    )
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        help="Exact advertisement name or MAC address (AA:BB:CC:DD:EE:FF). Provide once per battery.",
    )
    parser.add_argument(
        "--name-prefix",
        default=DEFAULT_NAME_PREFIX,
        help="Name prefix to auto-discover when no --target values are provided (default: %(default)s)",
    )
    parser.add_argument(
        "--max-devices",
        type=int,
        default=2,
        help="Number of devices to query when auto-discovering (default: %(default)s)",
    )
    parser.add_argument(
        "--scan-timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for BLE advertisements before giving up (default: %(default)s)",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for a BLE connection to establish (default: %(default)s)",
    )
    parser.add_argument(
        "--command-timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for the battery to reply to command 0x04 (default: %(default)s)",
    )
    parser.add_argument(
        "--scan-mode",
        choices=["passive", "active"],
        default="active",
        help="Requested BLE scan mode on the ESPHome proxy (default: %(default)s)",
    )
    parser.add_argument(
        "--case-sensitive-prefix",
        action="store_true",
        help="Treat --name-prefix as case sensitive instead of matching uppercase names.",
    )
    parser.add_argument(
        "--log-advertisements",
        type=int,
        default=0,
        help="If >0, log the first N advertisements for debugging (use -1 for unlimited).",
    )
    parser.add_argument(
        "--log-scanner-state",
        action="store_true",
        help="Log BLE scanner state updates from the ESPHome proxy.",
    )
    parser.add_argument(
        "--raw-advertisements",
        action="store_true",
        help="Subscribe to raw BLE advertisements instead of decoded ones.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        logging.warning("Interrupted, exiting.")
    except Exception as exc:
        logging.error("Failed to complete BLE query: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
