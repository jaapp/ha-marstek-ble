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
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MarstekDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)
VERBOSE_LOGGER = logging.getLogger(f"{__name__}.verbose")
# Keep verbose logs isolated unless explicitly enabled via logger config.
VERBOSE_LOGGER.propagate = False
VERBOSE_LOGGER.setLevel(logging.INFO)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Marstek BLE sensors from a config entry."""
    coordinator: MarstekDataUpdateCoordinator = entry.runtime_data

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
            suggested_display_precision=2,
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
            "grid_power",
            "Grid Power",
            lambda data: data.grid_power,
            UnitOfPower.WATT,
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "solar_power",
            "Solar Power",
            lambda data: data.solar_power,
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
        # Energy sensors
        MarstekSensor(
            coordinator,
            entry,
            "daily_energy_charged",
            "Daily Energy Charged",
            lambda data: data.daily_energy_charged,
            UnitOfEnergy.KILO_WATT_HOUR,
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL_INCREASING,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "daily_energy_discharged",
            "Daily Energy Discharged",
            lambda data: data.daily_energy_discharged,
            UnitOfEnergy.KILO_WATT_HOUR,
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL_INCREASING,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "monthly_energy_charged",
            "Monthly Energy Charged",
            lambda data: data.monthly_energy_charged,
            UnitOfEnergy.KILO_WATT_HOUR,
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL_INCREASING,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "monthly_energy_discharged",
            "Monthly Energy Discharged",
            lambda data: data.monthly_energy_discharged,
            UnitOfEnergy.KILO_WATT_HOUR,
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL_INCREASING,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "total_energy_charged",
            "Total Energy Charged",
            lambda data: data.total_energy_charged,
            UnitOfEnergy.KILO_WATT_HOUR,
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL_INCREASING,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "total_energy_discharged",
            "Total Energy Discharged",
            lambda data: data.total_energy_discharged,
            UnitOfEnergy.KILO_WATT_HOUR,
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL_INCREASING,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "design_capacity",
            "Design Capacity",
            lambda data: data.design_capacity,
            UnitOfEnergy.WATT_HOUR,
            SensorDeviceClass.ENERGY,
            None,
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
        MarstekSensor(
            coordinator,
            entry,
            "mosfet_temp",
            "MOSFET Temperature",
            lambda data: data.mosfet_temp,
            UnitOfTemperature.CELSIUS,
            SensorDeviceClass.TEMPERATURE,
            SensorStateClass.MEASUREMENT,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "temp_sensor_1",
            "Temperature Sensor 1",
            lambda data: data.temp_sensor_1,
            UnitOfTemperature.CELSIUS,
            SensorDeviceClass.TEMPERATURE,
            SensorStateClass.MEASUREMENT,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "temp_sensor_2",
            "Temperature Sensor 2",
            lambda data: data.temp_sensor_2,
            UnitOfTemperature.CELSIUS,
            SensorDeviceClass.TEMPERATURE,
            SensorStateClass.MEASUREMENT,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "temp_sensor_3",
            "Temperature Sensor 3",
            lambda data: data.temp_sensor_3,
            UnitOfTemperature.CELSIUS,
            SensorDeviceClass.TEMPERATURE,
            SensorStateClass.MEASUREMENT,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "temp_sensor_4",
            "Temperature Sensor 4",
            lambda data: data.temp_sensor_4,
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
            entity_category=EntityCategory.DIAGNOSTIC,
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
            entity_category=EntityCategory.DIAGNOSTIC,
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
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "work_mode",
            "Work Mode",
            lambda data: data.work_mode,
            None,
            None,
            SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "product_code",
            "Product Code",
            lambda data: data.product_code,
            None,
            None,
            SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "power_rating",
            "Power Rating",
            lambda data: data.power_rating,
            UnitOfPower.WATT,
            SensorDeviceClass.POWER,
            None,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "bms_version",
            "BMS Version",
            lambda data: data.bms_version,
            None,
            None,
            None,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "voltage_limit",
            "Voltage Limit",
            lambda data: data.voltage_limit,
            UnitOfElectricPotential.VOLT,
            SensorDeviceClass.VOLTAGE,
            None,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "charge_current_limit",
            "Charge Current Limit",
            lambda data: data.charge_current_limit,
            UnitOfElectricCurrent.AMPERE,
            SensorDeviceClass.CURRENT,
            None,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "discharge_current_limit",
            "Discharge Current Limit",
            lambda data: data.discharge_current_limit,
            UnitOfElectricCurrent.AMPERE,
            SensorDeviceClass.CURRENT,
            None,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "error_code",
            "Error Code",
            lambda data: data.error_code,
            None,
            None,
            None,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "warning_code",
            "Warning Code",
            lambda data: data.warning_code,
            None,
            None,
            None,
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        MarstekSensor(
            coordinator,
            entry,
            "runtime_hours",
            "Runtime",
            lambda data: data.runtime_hours,
            UnitOfTime.HOURS,
            SensorDeviceClass.DURATION,
            SensorStateClass.TOTAL_INCREASING,
            entity_category=EntityCategory.DIAGNOSTIC,
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
                entity_category=EntityCategory.DIAGNOSTIC,
                suggested_display_precision=2,
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
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
            MarstekTextSensor(
                coordinator,
                entry,
                "device_id",
                "Device ID",
                lambda data: data.device_id,
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
            MarstekTextSensor(
                coordinator,
                entry,
                "serial_number",
                "Serial Number",
                lambda data: data.serial_number,
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
            MarstekTextSensor(
                coordinator,
                entry,
                "mac_address",
                "MAC Address",
                lambda data: data.mac_address,
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
            MarstekTextSensor(
                coordinator,
                entry,
                "firmware_version",
                "Firmware Version",
                lambda data: data.firmware_version,
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
            MarstekTextSensor(
                coordinator,
                entry,
                "hardware_version",
                "Hardware Version",
                lambda data: data.hardware_version,
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
            MarstekTextSensor(
                coordinator,
                entry,
                "wifi_ssid",
                "WiFi SSID",
                lambda data: data.wifi_ssid,
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
            MarstekTextSensor(
                coordinator,
                entry,
                "network_info",
                "Network Info",
                lambda data: data.network_info,
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
            MarstekTextSensor(
                coordinator,
                entry,
                "ip_address",
                "IP Address",
                lambda data: data.ip_address,
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
            MarstekTextSensor(
                coordinator,
                entry,
                "gateway",
                "Gateway",
                lambda data: data.gateway,
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
            MarstekTextSensor(
                coordinator,
                entry,
                "subnet_mask",
                "Subnet Mask",
                lambda data: data.subnet_mask,
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
            MarstekTextSensor(
                coordinator,
                entry,
                "dns_server",
                "DNS Server",
                lambda data: data.dns_server,
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
            MarstekTextSensor(
                coordinator,
                entry,
                "meter_ip",
                "Meter IP",
                lambda data: data.meter_ip,
                entity_category=EntityCategory.DIAGNOSTIC,
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
        entity_category: EntityCategory | None = None,
        suggested_display_precision: int | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_has_entity_name = True
        self._value_fn = value_fn
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_entity_category = entity_category
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        if suggested_display_precision is not None:
            self._attr_suggested_display_precision = suggested_display_precision

    def _handle_coordinator_update(self) -> None:
        """Handle updated data with telemetry for debugging staleness."""
        value = self.native_value
        meta = self.coordinator.data.get_field_metadata(self._key)
        VERBOSE_LOGGER.debug(
            "[%s/%s] Sensor update %s=%s (source=%s ts=%s age=%.1fs payload=%s)",
            self.coordinator.device_name,
            self.coordinator.address,
            self._key,
            value,
            meta.get("command_hex") if meta else "unknown",
            meta.get("timestamp") if meta else "unknown",
            meta.get("age_seconds", -1) if meta else -1,
            meta.get("payload_hex") if meta else "unknown",
        )
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available and self.coordinator.data is not None

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._value_fn(self.coordinator.data)

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.ble_device.address)},
            "connections": {(CONNECTION_BLUETOOTH, self.coordinator.ble_device.address)},
            "name": self.coordinator.device_name,
            "manufacturer": "Marstek",
            "model": "Venus E",
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
        entity_category: EntityCategory | None = None,
    ) -> None:
        """Initialize the text sensor."""
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_has_entity_name = True
        self._value_fn = value_fn
        self._attr_entity_category = entity_category
        self._attr_unique_id = f"{entry.entry_id}_{key}"

    def _handle_coordinator_update(self) -> None:
        """Handle updated data with telemetry for debugging staleness."""
        value = self.native_value
        meta = self.coordinator.data.get_field_metadata(self._key)
        VERBOSE_LOGGER.debug(
            "[%s/%s] Sensor update %s=%s (source=%s ts=%s age=%.1fs payload=%s)",
            self.coordinator.device_name,
            self.coordinator.address,
            self._key,
            value,
            meta.get("command_hex") if meta else "unknown",
            meta.get("timestamp") if meta else "unknown",
            meta.get("age_seconds", -1) if meta else -1,
            meta.get("payload_hex") if meta else "unknown",
        )
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available and self.coordinator.data is not None

    @property
    def native_value(self):
        """Return the state of the sensor."""
        value = self._value_fn(self.coordinator.data)
        return str(value) if value is not None else None

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.ble_device.address)},
            "connections": {(CONNECTION_BLUETOOTH, self.coordinator.ble_device.address)},
            "name": self.coordinator.device_name,
            "manufacturer": "Marstek",
            "model": "Venus E",
        }
