"""The Marstek BLE integration."""
from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
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

    address: str = entry.data[CONF_ADDRESS]

    # Get BLE device
    ble_device = bluetooth.async_ble_device_from_address(
        hass, address.upper(), connectable=True
    )
    if not ble_device:
        raise ConfigEntryNotReady(
            f"Could not find Marstek device with address {address}"
        )

    # Create and store coordinator
    coordinator = entry.runtime_data = MarstekDataUpdateCoordinator(
        hass=hass,
        logger=_LOGGER,
        address=address,
        device=ble_device,
        device_name=entry.data.get(CONF_NAME, entry.title),
    )

    # Start coordinator and wait for it to be ready
    entry.async_on_unload(coordinator.async_start())

    if not await coordinator.async_wait_ready():
        raise ConfigEntryNotReady(
            f"Device {address} not advertising, will retry later"
        )

    # Register device
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, address)},
        identifiers={(DOMAIN, address)},
        name=entry.data.get(CONF_NAME, entry.title),
        manufacturer="Marstek",
        model="Venus E",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Marstek BLE entry: %s", entry.data)

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
