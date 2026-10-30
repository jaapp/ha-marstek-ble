"""Focused Local API control tests; run with Home Assistant dependencies installed."""
import asyncio
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError

from custom_components.marstek_ble import PLATFORMS
from custom_components.marstek_ble.config_flow import MarstekBLEOptionsFlow
from custom_components.marstek_ble.const import CMD_LOCAL_API_STATUS, DOMAIN
from custom_components.marstek_ble.coordinator import MarstekDataUpdateCoordinator
from custom_components.marstek_ble.marstek_device import MarstekData, MarstekProtocol
from custom_components.marstek_ble.number import MarstekLocalApiPort
from custom_components.marstek_ble.switch import MarstekLocalApiSwitch


class FakeDevice:
    def __init__(self):
        self.calls = []
        self.last_response = None
        self.enabled = True
        self.port = 30000
        self.reply = True
        self.apply = True
        self.transport_result = True
        self.empty_disable = False
        self.corrupt = False

    async def send_command(self, command, payload=b""):
        assert command == CMD_LOCAL_API_STATUS
        assert len(payload) == 3, "Never send an empty 0x28 status query"
        self.calls.append(payload)
        enabled, port = struct.unpack("<BH", payload)
        if self.apply:
            self.enabled, self.port = bool(enabled), port
        if self.reply:
            body = struct.pack("<BH", self.enabled, self.port)
            if self.empty_disable and not self.enabled:
                body = b""
            frame = bytearray(MarstekProtocol.build_command(command, body))
            if self.corrupt:
                frame[-1] ^= 1
            self.last_response = bytes(frame)
        return self.transport_result


class LocalAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.coordinator = object.__new__(MarstekDataUpdateCoordinator)
        self.coordinator.data = MarstekData()
        self.coordinator._poll_lock = asyncio.Lock()
        self.coordinator.async_update_listeners = Mock()
        self.coordinator.ble_device = SimpleNamespace(address="AA:BB:CC:DD:EE:FF")
        self.coordinator.config_entry = SimpleNamespace(entry_id="test", options={})
        self.coordinator.hass = SimpleNamespace(
            config_entries=SimpleNamespace(
                async_update_entry=lambda entry, options: setattr(entry, "options", options)
            )
        )
        self.device = self.coordinator.device = FakeDevice()

    async def test_polling_options_preserve_configured_api_port(self):
        await self.coordinator.async_set_local_api(port=30001)
        entry = self.coordinator.config_entry
        entry.options.update({"poll_interval": 1, "medium_poll_interval": 60})
        flow = MarstekBLEOptionsFlow(entry)
        result = await flow.async_step_init(
            {"poll_interval": 5, "medium_poll_interval": 120}
        )
        self.assertEqual(
            result["data"],
            {"local_api_port": 30001, "poll_interval": 5, "medium_poll_interval": 120},
        )
        entry.options = result["data"]
        await self.coordinator.async_set_local_api(enabled=True)
        self.assertEqual(self.device.calls, [b"\x01\x31\x75"])

    async def test_enable_payload_little_endian(self):
        await self.coordinator.async_set_local_api(enabled=True)
        self.assertEqual(self.device.calls, [b"\x01\x30\x75"])
        self.assertEqual(self.coordinator.local_api_settings, (True, 30000))

    async def test_disable_payload(self):
        await self.coordinator.async_set_local_api(enabled=False)
        self.assertEqual(self.device.calls, [b"\x00\x30\x75"])
        self.assertEqual(self.coordinator.local_api_settings, (False, 30000))

    async def test_port_edit_preserves_disabled(self):
        await self.coordinator.async_set_local_api(enabled=False)
        await self.coordinator.async_set_local_api(port=30001)
        self.assertEqual(self.device.calls[-1], b"\x00\x31\x75")
        self.assertEqual(self.coordinator.local_api_settings, (False, 30001))

    async def test_port_edit_preserves_enabled(self):
        await self.coordinator.async_set_local_api(enabled=True)
        await self.coordinator.async_set_local_api(port=30001)
        self.assertEqual(self.device.calls[-1], b"\x01\x31\x75")

    async def test_unknown_state_port_is_draft_only(self):
        self.assertIsNone(self.coordinator.local_api_settings)
        await self.coordinator.async_set_local_api(port=30001)
        self.assertEqual(self.device.calls, [])
        self.assertEqual(self.coordinator.local_api_port, 30001)
        await self.coordinator.async_set_local_api(enabled=True)
        self.assertEqual(self.device.calls, [b"\x01\x31\x75"])

    async def test_port_bounds_and_integer_validation(self):
        await self.coordinator.async_set_local_api(enabled=True)
        for port in (1, 65535):
            await self.coordinator.async_set_local_api(port=port)
            self.assertEqual(self.coordinator.local_api_settings, (True, port))
        count = len(self.device.calls)
        for port in (0, 65536, 1.5, True):
            with self.assertRaises(HomeAssistantError):
                await self.coordinator.async_set_local_api(port=port)
        self.assertEqual(len(self.device.calls), count)

    async def test_stale_cached_ack_is_rejected(self):
        await self.coordinator.async_set_local_api(enabled=True)
        self.device.reply = False
        with self.assertRaises(HomeAssistantError):
            await self.coordinator.async_set_local_api(enabled=False)
        self.assertIsNone(self.coordinator.local_api_settings)

    async def test_mismatched_ack_rejected(self):
        self.device.apply = False
        with self.assertRaises(HomeAssistantError):
            await self.coordinator.async_set_local_api(enabled=False)
        self.assertIsNone(self.coordinator.local_api_settings)

    async def test_corrupt_checksum_rejected(self):
        self.device.corrupt = True
        with self.assertRaises(HomeAssistantError):
            await self.coordinator.async_set_local_api(enabled=True)
        self.assertIsNone(self.coordinator.local_api_settings)

    async def test_false_transport_result_with_valid_ack(self):
        self.device.transport_result = False
        await self.coordinator.async_set_local_api(enabled=False)
        self.assertEqual(self.coordinator.local_api_settings, (False, 30000))

    async def test_empty_disable_ack_and_empty_enable_rejected(self):
        self.device.empty_disable = True
        await self.coordinator.async_set_local_api(enabled=False)
        self.assertEqual(self.coordinator.local_api_settings, (False, 30000))
        self.device.apply = False
        with self.assertRaises(HomeAssistantError):
            await self.coordinator.async_set_local_api(enabled=True)
        self.assertIsNone(self.coordinator.local_api_settings)

    async def test_entities_platform_and_assumptions(self):
        entry = SimpleNamespace(entry_id="test")
        switch = MarstekLocalApiSwitch(self.coordinator, entry)
        number = MarstekLocalApiPort(self.coordinator, entry)
        self.assertIsNone(switch.is_on)
        self.assertTrue(switch.assumed_state)
        self.assertTrue(number.assumed_state)
        self.assertIn("Unknown", switch.extra_state_attributes["state_source"])
        self.assertEqual(number.native_value, 30000)
        self.assertEqual(switch.unique_id, "test_local_api")
        self.assertEqual(number.unique_id, "test_local_api_port")
        self.assertEqual(switch.device_info["identifiers"], {(DOMAIN, "AA:BB:CC:DD:EE:FF")})
        self.assertEqual(number.device_info["identifiers"], {(DOMAIN, "AA:BB:CC:DD:EE:FF")})
        self.assertIn(Platform.NUMBER, PLATFORMS)
        await number.async_set_native_value(30001)
        self.assertEqual(self.device.calls, [])
        await switch.async_turn_on()
        self.assertTrue(switch.is_on)
        await number.async_set_native_value(30002.0)
        with self.assertRaises(HomeAssistantError):
            await number.async_set_native_value(30002.5)

    async def test_malformed_parser_and_poll_serialization(self):
        for payload in (b"", b"\x01\x30", b"\x02\x30\x75", b"\x01\x00\x00", b"\x01\x30\x75\x00"):
            self.assertFalse(MarstekProtocol._parse_local_api_status(payload, self.coordinator.data, 123.0))
        self.assertTrue(MarstekProtocol._parse_local_api_status(b"\x01\x30\x75", self.coordinator.data, 123.0))
        self.assertIsNone(self.coordinator.local_api_settings)
        frame = bytearray(MarstekProtocol.build_command(CMD_LOCAL_API_STATUS, b"\x01\x30\x75"))
        frame[-1] ^= 1
        self.assertFalse(MarstekProtocol.parse_notification(bytes(frame), self.coordinator.data))
        await self.coordinator._poll_lock.acquire()
        task = asyncio.create_task(self.coordinator.async_set_local_api(enabled=True))
        await asyncio.sleep(0)
        self.assertEqual(self.device.calls, [])
        self.coordinator._poll_lock.release()
        await task
        self.assertEqual(self.device.calls, [b"\x01\x30\x75"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
