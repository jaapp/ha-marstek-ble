"""Diagnostics support for the Marstek BLE integration."""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from importlib import resources
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from .const import DOMAIN
from .coordinator import MarstekDataUpdateCoordinator

TO_REDACT = {
    "wifi_ssid",
    "wifi_name",
    "meter_ip",
    "network_info",
    "mac_address",
    "device_id",
}


def _load_manifest_version() -> str | None:
    """Return the version from manifest.json if available."""
    try:
        manifest_text = resources.files(__package__).joinpath("manifest.json").read_text(
            encoding="utf-8"
        )
    except (FileNotFoundError, IsADirectoryError):
        return None
    except Exception:
        return None

    try:
        manifest = json.loads(manifest_text)
    except json.JSONDecodeError:
        return None

    version = manifest.get("version")
    return str(version) if version is not None else None


def _dataclass_to_dict(data: Any) -> Any:
    """Convert dataclass instances into dictionaries."""
    if is_dataclass(data):
        return asdict(data)
    return data


def _coordinator_diagnostics(coordinator: MarstekDataUpdateCoordinator) -> dict[str, Any]:
    """Build a diagnostics snapshot from the coordinator."""
    device_diag = coordinator.device.get_diagnostics()
    update_interval = (
        coordinator.update_interval.total_seconds()
        if coordinator.update_interval
        else None
    )

    coordinator_state = {
        "device_name": coordinator.device_name,
        "bluetooth_address": coordinator.ble_device.address if coordinator.ble_device else None,
        "ready": coordinator._ready_event.is_set(),  # pylint: disable=protected-access
        "was_unavailable": coordinator._was_unavailable,  # pylint: disable=protected-access
        "last_poll_successful": coordinator.last_poll_successful,
        "polling": {
            "configured_fast_interval_seconds": coordinator._poll_interval,  # pylint: disable=protected-access
            "configured_medium_interval_seconds": coordinator._medium_poll_interval,  # pylint: disable=protected-access
            "active_update_interval_seconds": update_interval,
            "fast_poll_count": coordinator._fast_poll_count,  # pylint: disable=protected-access
            "medium_poll_count": coordinator._medium_poll_count,  # pylint: disable=protected-access
            "medium_poll_cycle": coordinator._medium_poll_cycle,  # pylint: disable=protected-access
        },
        "device_connected": device_diag.get("connected"),
        "coordinator_data": _dataclass_to_dict(coordinator.data),
        "device_diagnostics": device_diag,
    }

    return coordinator_state


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: MarstekDataUpdateCoordinator | None = entry.runtime_data
    if coordinator is None:
        coordinator = (
            hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("coordinator")
        )

    if coordinator is None:
        return {"error": "coordinator_not_available"}

    manifest_version = _load_manifest_version()

    diagnostics: dict[str, Any] = {
        "environment": {
            "home_assistant_version": HA_VERSION,
            "integration_version": manifest_version,
        },
        "config_entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "source": entry.source,
            "unique_id": entry.unique_id,
            "minor_version": entry.minor_version,
            "version": entry.version,
            "data": entry.data,
            "options": entry.options,
        },
        "coordinator": _coordinator_diagnostics(coordinator),
    }

    return async_redact_data(diagnostics, TO_REDACT)
