"""The Marstek BLE integration."""
from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, UPDATE_INTERVAL_FAST
from .coordinator import MarstekDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.SELECT,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Marstek BLE from a config entry."""
    _LOGGER.debug("Setting up Marstek BLE entry: %s", entry.data)

    address = entry.data[CONF_ADDRESS]

    # Get BLE device
    ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if not ble_device:
        _LOGGER.error("Could not find Marstek device with address %s", address)
        return False

    # Create coordinator
    coordinator = MarstekDataUpdateCoordinator(
        hass=hass,
        logger=_LOGGER,
        address=address,
        device=ble_device,
    )

    # Activate coordinator polling
    await coordinator.async_start()

    # Start coordinator
    await coordinator.async_start_notify()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Register device
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, address)},
        identifiers={(DOMAIN, address)},
        name=entry.data.get("name", f"Marstek Battery {address}"),
        manufacturer="Marstek",
        model="Venus E",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Marstek BLE entry: %s", entry.data)

    coordinator: MarstekDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_stop()
    await coordinator.async_stop_notify()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
