"""The Marstek BLE integration."""
from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    TURBO_LOG_MODE,
)
from .coordinator import MarstekDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)
if TURBO_LOG_MODE:
    _LOGGER.setLevel(logging.DEBUG)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.SELECT,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Marstek BLE from a config entry."""
    _LOGGER.info(
        "Setting up Marstek BLE entry %s for %s (address=%s, poll_interval=%ss, turbo_log=%s)",
        entry.entry_id,
        entry.title,
        entry.data.get(CONF_ADDRESS),
        entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
        TURBO_LOG_MODE,
    )

    address: str = entry.data[CONF_ADDRESS]
    device_name: str = entry.data.get(CONF_NAME, entry.title)
    poll_interval: int = entry.options.get(
        CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
    )

    # Check for duplicate device names in other entries
    for other_entry in hass.config_entries.async_entries(DOMAIN):
        if other_entry.entry_id != entry.entry_id:
            other_name = other_entry.data.get(CONF_NAME, other_entry.title)
            other_address = other_entry.data.get(CONF_ADDRESS)
            if other_name == device_name and other_address != address:
                _LOGGER.warning(
                    "Found duplicate device name '%s': this entry uses address %s, "
                    "but another entry uses address %s. This may cause data to be "
                    "reported incorrectly. Please remove duplicate config entries.",
                    device_name,
                    address,
                    other_address,
                )

    # Get BLE device
    ble_device = bluetooth.async_ble_device_from_address(
        hass, address.upper(), connectable=True
    )
    if not ble_device:
        _LOGGER.warning(
            "Could not find Marstek device with address %s yet; deferring setup",
            address,
        )
        raise ConfigEntryNotReady(
            f"Could not find Marstek device with address {address}"
        )

    # Create and store coordinator
    coordinator = entry.runtime_data = MarstekDataUpdateCoordinator(
        hass=hass,
        logger=_LOGGER,
        address=address,
        device=ble_device,
        device_name=device_name,
        poll_interval=poll_interval,
    )

    # Start coordinator and wait for it to be ready
    entry.async_on_unload(coordinator.async_start())
    entry.async_on_unload(entry.add_update_listener(_async_handle_entry_update))

    _LOGGER.info("[%s] Waiting for Marstek device advertisements", device_name)
    if not await coordinator.async_wait_ready():
        _LOGGER.warning(
            "[%s] No advertisements received yet; coordinator will retry later",
            device_name,
        )
        raise ConfigEntryNotReady(
            f"Device {address} not advertising, will retry later"
        )
    _LOGGER.info("[%s/%s] Device advertisements detected; continuing setup", device_name, address)

    # Register device
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, address)},
        identifiers={(DOMAIN, address)},
        name=device_name,
        manufacturer="Marstek",
        model="Venus E",
    )
    _LOGGER.info(
        "[%s/%s] Device registry entry ensured",
        device_name,
        address,
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.info(
        "[%s/%s] Platforms forwarded (%s); Marstek BLE setup complete",
        device_name,
        address,
        ", ".join(platform.value for platform in PLATFORMS),
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Marstek BLE entry %s (%s)", entry.entry_id, entry.title)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        domain_data = hass.data.get(DOMAIN)
        if domain_data:
            domain_data.pop(entry.entry_id, None)
            if not domain_data:
                hass.data.pop(DOMAIN)

    return unload_ok


async def _async_handle_entry_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle updates to the config entry options."""
    coordinator: MarstekDataUpdateCoordinator | None = entry.runtime_data
    if coordinator is None:
        return

    poll_interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
    _LOGGER.info(
        "[%s/%s] Poll interval updated via options: %ss",
        coordinator.device_name,
        coordinator.address,
        poll_interval,
    )
    coordinator.set_poll_interval(poll_interval)
