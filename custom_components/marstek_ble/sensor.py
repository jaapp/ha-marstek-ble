"""Sensor platform for Marstek BLE integration."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
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
    """Set up Marstek BLE sensors from a config entry."""
    coordinator: MarstekDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        # Battery sensors
        MarstekSensor(
            coordinator,
            entry,
            "battery_voltage",
            "Battery Voltage",
            lambda data: data.battery_voltage,
            UnitOfElectricPotential.VOLT,
            SensorDeviceClass.VOLTAGE,
            SensorStateClass.MEASUREMENT,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "battery_current",
            "Battery Current",
            lambda data: data.battery_current,
            UnitOfElectricCurrent.AMPERE,
            SensorDeviceClass.CURRENT,
            SensorStateClass.MEASUREMENT,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "battery_soc",
            "Battery SOC",
            lambda data: data.battery_soc,
            PERCENTAGE,
            SensorDeviceClass.BATTERY,
            SensorStateClass.MEASUREMENT,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "battery_soh",
            "Battery SOH",
            lambda data: data.battery_soh,
            PERCENTAGE,
            None,
            SensorStateClass.MEASUREMENT,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "battery_temp",
            "Battery Temperature",
            lambda data: data.battery_temp,
            UnitOfTemperature.CELSIUS,
            SensorDeviceClass.TEMPERATURE,
            SensorStateClass.MEASUREMENT,
        ),
        # Power sensors
        MarstekSensor(
            coordinator,
            entry,
            "battery_power",
            "Battery Power",
            lambda data: (
                data.battery_voltage * data.battery_current
                if data.battery_voltage is not None and data.battery_current is not None
                else None
            ),
            UnitOfPower.WATT,
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "battery_power_in",
            "Battery Power In",
            lambda data: (
                max(0, data.battery_voltage * data.battery_current)
                if data.battery_voltage is not None and data.battery_current is not None
                else None
            ),
            UnitOfPower.WATT,
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "battery_power_out",
            "Battery Power Out",
            lambda data: (
                max(0, -(data.battery_voltage * data.battery_current))
                if data.battery_voltage is not None and data.battery_current is not None
                else None
            ),
            UnitOfPower.WATT,
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "out1_power",
            "Output 1 Power",
            lambda data: data.out1_power,
            UnitOfPower.WATT,
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "adaptive_power_out",
            "Adaptive Power Out",
            lambda data: data.adaptive_power_out,
            UnitOfPower.WATT,
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
        ),
        # Energy sensors
        MarstekSensor(
            coordinator,
            entry,
            "design_capacity",
            "Design Capacity",
            lambda data: data.design_capacity,
            UnitOfEnergy.WATT_HOUR,
            SensorDeviceClass.ENERGY,
            SensorStateClass.MEASUREMENT,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "remaining_capacity",
            "Remaining Capacity",
            lambda data: (
                (data.battery_soc / 100.0) * data.design_capacity
                if data.battery_soc is not None and data.design_capacity is not None
                else None
            ),
            UnitOfEnergy.WATT_HOUR,
            SensorDeviceClass.ENERGY_STORAGE,
            SensorStateClass.MEASUREMENT,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "available_capacity",
            "Available Capacity",
            lambda data: (
                ((100.0 - data.battery_soc) / 100.0) * data.design_capacity
                if data.battery_soc is not None and data.design_capacity is not None
                else None
            ),
            UnitOfEnergy.WATT_HOUR,
            SensorDeviceClass.ENERGY_STORAGE,
            SensorStateClass.MEASUREMENT,
        ),
        # Temperature sensors
        MarstekSensor(
            coordinator,
            entry,
            "temp_low",
            "Temperature Low",
            lambda data: data.temp_low,
            UnitOfTemperature.CELSIUS,
            SensorDeviceClass.TEMPERATURE,
            SensorStateClass.MEASUREMENT,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "temp_high",
            "Temperature High",
            lambda data: data.temp_high,
            UnitOfTemperature.CELSIUS,
            SensorDeviceClass.TEMPERATURE,
            SensorStateClass.MEASUREMENT,
        ),
        # Diagnostic sensors
        MarstekSensor(
            coordinator,
            entry,
            "system_status",
            "System Status",
            lambda data: data.system_status,
            None,
            None,
            SensorStateClass.MEASUREMENT,
            entity_category="diagnostic",
        ),
        MarstekSensor(
            coordinator,
            entry,
            "config_mode",
            "Config Mode",
            lambda data: data.config_mode,
            None,
            None,
            SensorStateClass.MEASUREMENT,
            entity_category="diagnostic",
        ),
        MarstekSensor(
            coordinator,
            entry,
            "ct_polling_rate",
            "CT Polling Rate",
            lambda data: data.ct_polling_rate,
            None,
            None,
            SensorStateClass.MEASUREMENT,
            entity_category="diagnostic",
        ),
    ]

    # Add cell voltage sensors
    for i in range(16):
        entities.append(
            MarstekSensor(
                coordinator,
                entry,
                f"cell_{i + 1}_voltage",
                f"Cell {i + 1} Voltage",
                lambda data, idx=i: (
                    data.cell_voltages[idx] if data.cell_voltages and idx < len(data.cell_voltages) else None
                ),
                UnitOfElectricPotential.VOLT,
                SensorDeviceClass.VOLTAGE,
                SensorStateClass.MEASUREMENT,
                entity_category="diagnostic",
            )
        )

    # Add text sensors
    entities.extend(
        [
            MarstekTextSensor(
                coordinator,
                entry,
                "battery_state",
                "Battery State",
                lambda data: (
                    "charging"
                    if data.battery_voltage is not None
                    and data.battery_current is not None
                    and data.battery_voltage * data.battery_current > 5
                    else "discharging"
                    if data.battery_voltage is not None
                    and data.battery_current is not None
                    and data.battery_voltage * data.battery_current < -5
                    else "inactive"
                ),
            ),
            MarstekTextSensor(
                coordinator,
                entry,
                "device_type",
                "Device Type",
                lambda data: data.device_type,
                entity_category="diagnostic",
            ),
            MarstekTextSensor(
                coordinator,
                entry,
                "device_id",
                "Device ID",
                lambda data: data.device_id,
                entity_category="diagnostic",
            ),
            MarstekTextSensor(
                coordinator,
                entry,
                "mac_address",
                "MAC Address",
                lambda data: data.mac_address,
                entity_category="diagnostic",
            ),
            MarstekTextSensor(
                coordinator,
                entry,
                "firmware_version",
                "Firmware Version",
                lambda data: data.firmware_version,
                entity_category="diagnostic",
            ),
            MarstekTextSensor(
                coordinator,
                entry,
                "wifi_ssid",
                "WiFi SSID",
                lambda data: data.wifi_ssid,
                entity_category="diagnostic",
            ),
            MarstekTextSensor(
                coordinator,
                entry,
                "network_info",
                "Network Info",
                lambda data: data.network_info,
                entity_category="diagnostic",
            ),
            MarstekTextSensor(
                coordinator,
                entry,
                "meter_ip",
                "Meter IP",
                lambda data: data.meter_ip,
                entity_category="diagnostic",
            ),
            MarstekTextSensor(
                coordinator,
                entry,
                "local_api_status",
                "Local API Status",
                lambda data: data.local_api_status,
                entity_category="diagnostic",
            ),
        ]
    )

    async_add_entities(entities)


class MarstekSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Marstek sensor."""

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
        value_fn,
        unit: str | None,
        device_class: SensorDeviceClass | None,
        state_class: SensorStateClass | None,
        entity_category: str | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._value_fn = value_fn
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_entity_category = entity_category
        self._attr_unique_id = f"{entry.entry_id}_{key}"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._value_fn(self.coordinator.data)

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self.coordinator._device.address)},
            "connections": {(CONNECTION_BLUETOOTH, self.coordinator._device.address)},
        }


class MarstekTextSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Marstek text sensor."""

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
        value_fn,
        entity_category: str | None = None,
    ) -> None:
        """Initialize the text sensor."""
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._value_fn = value_fn
        self._attr_entity_category = entity_category
        self._attr_unique_id = f"{entry.entry_id}_{key}"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        value = self._value_fn(self.coordinator.data)
        return str(value) if value is not None else None

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self.coordinator._device.address)},
            "connections": {(CONNECTION_BLUETOOTH, self.coordinator._device.address)},
        }
