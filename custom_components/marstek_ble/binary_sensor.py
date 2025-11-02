"""Binary sensor platform for Marstek BLE integration."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MarstekDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Marstek BLE binary sensors from a config entry."""
    coordinator: MarstekDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        MarstekBinarySensor(
            coordinator,
            entry,
            "ble_connected",
            "BLE Connected",
            lambda data: coordinator.client and coordinator.client.is_connected if coordinator.client else False,
            BinarySensorDeviceClass.CONNECTIVITY,
            entity_category="diagnostic",
        ),
        MarstekBinarySensor(
            coordinator,
            entry,
            "wifi_connected",
            "WiFi Connected",
            lambda data: data.wifi_connected,
            BinarySensorDeviceClass.CONNECTIVITY,
            entity_category="diagnostic",
        ),
        MarstekBinarySensor(
            coordinator,
            entry,
            "mqtt_connected",
            "MQTT Connected",
            lambda data: data.mqtt_connected,
            BinarySensorDeviceClass.CONNECTIVITY,
            entity_category="diagnostic",
        ),
        MarstekBinarySensor(
            coordinator,
            entry,
            "out1_active",
            "Output 1 Active",
            lambda data: data.out1_active,
            BinarySensorDeviceClass.POWER,
            entity_category="diagnostic",
        ),
        MarstekBinarySensor(
            coordinator,
            entry,
            "extern1_connected",
            "External 1 Connected",
            lambda data: data.extern1_connected,
            BinarySensorDeviceClass.CONNECTIVITY,
        ),
        MarstekBinarySensor(
            coordinator,
            entry,
            "smart_meter_connected",
            "Smart Meter Connected",
            lambda data: data.smart_meter_connected,
            BinarySensorDeviceClass.CONNECTIVITY,
            entity_category="diagnostic",
        ),
        MarstekBinarySensor(
            coordinator,
            entry,
            "adaptive_mode_status",
            "Adaptive Mode",
            lambda data: data.adaptive_mode_enabled,
        ),
    ]

    async_add_entities(entities)


class MarstekBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of a Marstek binary sensor."""

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
        value_fn,
        device_class: BinarySensorDeviceClass | None = None,
        entity_category: str | None = None,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._value_fn = value_fn
        self._attr_device_class = device_class
        self._attr_entity_category = entity_category
        self._attr_unique_id = f"{entry.entry_id}_{key}"

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        return self._value_fn(self.coordinator.data)

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self.coordinator._device.address)},
            "connections": {(CONNECTION_BLUETOOTH, self.coordinator._device.address)},
        }
