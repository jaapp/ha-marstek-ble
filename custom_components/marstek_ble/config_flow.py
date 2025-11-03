"""Config flow for Marstek BLE integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS, CONF_NAME

from .const import (
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DEVICE_PREFIXES,
    DOMAIN,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class MarstekBLEConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Marstek BLE."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> MarstekBLEOptionsFlow:
        """Return the options flow handler."""
        return MarstekBLEOptionsFlow(config_entry)

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        _LOGGER.debug("Discovered Marstek device: %s", discovery_info)
        _LOGGER.info(
            "Discovery - Name: %s, Address: %s",
            discovery_info.name,
            discovery_info.address,
        )

        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        self._discovery_info = discovery_info

        # Set title placeholders for discovery card
        device_name = discovery_info.name or discovery_info.address
        _LOGGER.info("Setting title_placeholders: name=%s", device_name)
        self.context["title_placeholders"] = {
            "name": device_name,
        }

        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery."""
        assert self._discovery_info is not None

        if user_input is not None:
            return self.async_create_entry(
                title=self._discovery_info.name or self._discovery_info.address,
                data={
                    CONF_ADDRESS: self._discovery_info.address,
                    CONF_NAME: self._discovery_info.name or self._discovery_info.address,
                },
            )

        self._set_confirm_only()

        return self.async_show_form(
            step_id="bluetooth_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "name": self._discovery_info.name or self._discovery_info.address,
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step to pick discovered device."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            discovery_info = self._discovered_devices[address]

            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=discovery_info.name or address,
                data={
                    CONF_ADDRESS: address,
                    CONF_NAME: discovery_info.name or address,
                },
            )

        # Discover devices
        current_addresses = self._async_current_ids()

        for discovery_info in async_discovered_service_info(self.hass):
            _LOGGER.debug(
                "Checking discovered device: %s (%s)",
                discovery_info.name,
                discovery_info.address,
            )

            # Check if device is already configured
            if discovery_info.address in current_addresses:
                _LOGGER.debug("Device already configured: %s", discovery_info.name)
                continue

            # Check if device name matches battery prefixes (not CT devices)
            if not discovery_info.name or not any(
                discovery_info.name.startswith(prefix) for prefix in DEVICE_PREFIXES
            ):
                _LOGGER.debug("Device filtered out: %s", discovery_info.name)
                continue

            _LOGGER.info(
                "Adding Marstek device to selection: %s (%s)",
                discovery_info.name,
                discovery_info.address,
            )
            self._discovered_devices[discovery_info.address] = discovery_info

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=self._get_user_schema(),
        )

    def _get_user_schema(self) -> vol.Schema:
        """Get the user schema."""
        return vol.Schema(
            {
                vol.Required(CONF_ADDRESS): vol.In(
                    {
                        address: f"{info.name} ({address})"
                        for address, info in self._discovered_devices.items()
                    }
                )
            }
        )


class MarstekBLEOptionsFlow(OptionsFlow):
    """Handle options for the Marstek BLE integration."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the options step."""
        if user_input is not None:
            _LOGGER.debug("Options updated: %s", user_input)
            return self.async_create_entry(title="", data=user_input)

        current_interval = self._config_entry.options.get(
            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL, default=current_interval
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL),
                    )
                }
            ),
        )
