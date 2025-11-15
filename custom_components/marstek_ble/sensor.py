"""Sensor platform for Marstek BLE integration."""
from __future__ import annotations

import asyncio
import logging

from datetime import timedelta

from homeassistant.components.integration.const import METHOD_TRAPEZOIDAL
from homeassistant.components.integration.sensor import IntegrationSensor
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.utility_meter.const import (
    CONF_TARIFF_ENTITY,
    DAILY,
    DATA_TARIFF_SENSORS,
    DATA_UTILITY,
)
from homeassistant.components.utility_meter.sensor import UtilityMeterSensor
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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import DOMAIN
from .coordinator import MarstekDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)
VERBOSE_LOGGER = logging.getLogger(f"{__name__}.verbose")
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
                "meter_ip",
                "Meter IP",
                lambda data: data.meter_ip,
                entity_category=EntityCategory.DIAGNOSTIC,
            ),
        ]
    )

    async_add_entities(entities)

    async def _async_setup_energy_helpers() -> None:
        """Create integration and daily utility meter sensors once base sensors are registered."""
        try:
            entity_registry = er.async_get(hass)

            def _resolve_entity_id(key: str) -> str | None:
                return entity_registry.async_get_entity_id(
                    "sensor", DOMAIN, f"{entry.entry_id}_{key}"
                )

            _LOGGER.debug(
                "Energy helper starting for %s (entry_id=%s)",
                coordinator.device_name,
                entry.entry_id,
            )

            max_attempts = 6
            delay = 5

            power_in_entity_id: str | None = None
            power_out_entity_id: str | None = None

            for attempt in range(1, max_attempts + 1):
                await hass.async_block_till_done()
                power_in_entity_id = _resolve_entity_id("battery_power_in")
                power_out_entity_id = _resolve_entity_id("battery_power_out")

                _LOGGER.debug(
                    "Energy helper attempt %s/%s for %s: in=%s out=%s",
                    attempt,
                    max_attempts,
                    coordinator.device_name,
                    power_in_entity_id,
                    power_out_entity_id,
                )

                if power_in_entity_id and power_out_entity_id:
                    break

                if attempt < max_attempts:
                    await asyncio.sleep(delay)
                else:
                    _LOGGER.warning(
                        "Energy helper setup failed to resolve power entities for %s; giving up",
                        coordinator.device_name,
                    )
                    return

            # Fallback: if still missing (edge case), construct expected entity_ids from slugified device name
            if not power_in_entity_id:
                power_in_entity_id = f"sensor.{slugify(coordinator.device_name)}_battery_power_in"
            if not power_out_entity_id:
                power_out_entity_id = f"sensor.{slugify(coordinator.device_name)}_battery_power_out"

            _LOGGER.debug(
                "Energy helper resolved power entities for %s: in=%s out=%s",
                coordinator.device_name,
                power_in_entity_id,
                power_out_entity_id,
            )

            energy_entities: list[IntegrationSensor] = [
                IntegrationSensor(
                    hass,
                    integration_method=METHOD_TRAPEZOIDAL,
                    name=f"{coordinator.device_name} Battery Energy In",
                    round_digits=3,
                    source_entity=power_in_entity_id,
                    unique_id=f"{entry.entry_id}_battery_energy_in",
                    unit_prefix=None,
                    unit_time=UnitOfTime.HOURS,
                    max_sub_interval=None,
                ),
                IntegrationSensor(
                    hass,
                    integration_method=METHOD_TRAPEZOIDAL,
                    name=f"{coordinator.device_name} Battery Energy Out",
                    round_digits=3,
                    source_entity=power_out_entity_id,
                    unique_id=f"{entry.entry_id}_battery_energy_out",
                    unit_prefix=None,
                    unit_time=UnitOfTime.HOURS,
                    max_sub_interval=None,
                ),
            ]

            async_add_entities(energy_entities)
            await hass.async_block_till_done()

            energy_in_entity_id = _resolve_entity_id("battery_energy_in")
            energy_out_entity_id = _resolve_entity_id("battery_energy_out")

            utility_entities: list[UtilityMeterSensor] = []
            utility_data = hass.data.setdefault(DATA_UTILITY, {})

            for slug, source_entity_id, name in [
                ("daily_battery_energy_in", energy_in_entity_id, f"{coordinator.device_name} Daily Battery Energy In"),
                ("daily_battery_energy_out", energy_out_entity_id, f"{coordinator.device_name} Daily Battery Energy Out"),
            ]:
                if not source_entity_id:
                    _LOGGER.warning(
                        "Unable to resolve integration sensor entity for %s; skipping %s",
                        slug,
                        name,
                    )
                    continue

                parent_meter = f"{entry.entry_id}_{slug}_utility"
                utility_data[parent_meter] = {
                    DATA_TARIFF_SENSORS: [],
                    CONF_TARIFF_ENTITY: None,
                }

                meter = UtilityMeterSensor(
                    hass,
                    cron_pattern=None,
                    delta_values=False,
                    meter_offset=timedelta(0),
                    meter_type=DAILY,
                    name=name,
                    net_consumption=False,
                    parent_meter=parent_meter,
                    periodically_resetting=False,
                    source_entity=source_entity_id,
                    tariff_entity=None,
                    tariff=None,
                    unique_id=f"{entry.entry_id}_{slug}",
                    sensor_always_available=False,
                )

                utility_data[parent_meter][DATA_TARIFF_SENSORS].append(meter)
                utility_entities.append(meter)

            if utility_entities:
                async_add_entities(utility_entities)
                _LOGGER.debug(
                    "Energy helper created %d utility meter entities for %s",
                    len(utility_entities),
                    coordinator.device_name,
                )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.exception(
                "Energy helper failed for %s (entry_id=%s): %s",
                coordinator.device_name,
                entry.entry_id,
                exc,
            )

    await _async_setup_energy_helpers()


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
