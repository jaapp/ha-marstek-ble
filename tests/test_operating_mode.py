"""Isolated selector tests: python -m unittest discover -s tests.

Uses the real selector AST with HA doubles, not a live HA/BLE environment.
Captured payloads are from the user's Venus E v3 mode tests, 2026-10-11.
"""
import ast
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock


class CoordinatorEntity:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, coordinator):
        self.coordinator = coordinator

    def async_write_ha_state(self):
        pass


class SelectEntity:
    pass


class HomeAssistantError(Exception):
    pass


SOURCE = Path(__file__).resolve().parents[1] / "custom_components/marstek_ble/select.py"
tree = ast.parse(SOURCE.read_text())
names = {"_reported_operating_mode", "MarstekOperatingModeSelect"}
constants = {"MODE_STALE_AFTER_SECONDS", "OPERATING_MODE_OPTIONS"}
nodes = [n for n in tree.body if (isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names) or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in n.targets))]
module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes], type_ignores=[])
ns = dict(CoordinatorEntity=CoordinatorEntity, SelectEntity=SelectEntity,
          MarstekDataUpdateCoordinator=object, HomeAssistantError=HomeAssistantError,
          EntityCategory=SimpleNamespace(CONFIG="config"), CMD_SYSTEM_DATA=0x0D,
          CMD_AUTO_MODE=0x0E, CMD_WORK_MODE=0x09, _LOGGER=logging.getLogger(__name__))
exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), ns)
decode, ModeSelect = ns["_reported_operating_mode"], ns["MarstekOperatingModeSelect"]


class Data:
    def __init__(self, mode=0, age=0, timestamp="before", length=19):
        payload = bytearray(length)
        if length > 17:
            payload[17] = mode
        self.meta = dict(command=0x0D, age_seconds=age, timestamp=timestamp, payload_hex=payload.hex())
        self.work_mode = 2

    def get_field_metadata(self, field):
        assert field == "system_status"
        return self.meta


def entity(data):
    return ModeSelect(SimpleNamespace(data=data, device=SimpleNamespace(send_command=AsyncMock(return_value=True))), SimpleNamespace(entry_id="test"))


class DecodeTests(unittest.TestCase):
    def test_modes(self):
        for mode, expected in [(0, "Self-Consumption"), (1, "Manual"), (5, "AI Optimization")]:
            self.assertEqual(decode(Data(mode)), expected)

    def test_recorded_payloads(self):
        for payload, expected in [
            ("01f4010c090401fd0008010000dd00d9000000", "Self-Consumption"),
            ("01f40102090000fa0007010000dd00d9000100", "Manual"),
            ("01f4010409fb00fc0006010000dd00d9000500", "AI Optimization"),
        ]:
            data = Data()
            data.meta["payload_hex"] = payload
            self.assertEqual(decode(data), expected)

    def test_unknown_values(self):
        for mode in [2, 3, 4, 6, 255]:
            self.assertIsNone(decode(Data(mode)))

    def test_missing_data(self):
        self.assertIsNone(decode(None))
        data = Data()
        data.meta = None
        self.assertIsNone(decode(data))

    def test_short_payload(self):
        for length in [0, 11, 17, 18]:
            self.assertIsNone(decode(Data(length=length)))

    def test_extended_payload(self):
        self.assertEqual(decode(Data(1, length=30)), "Manual")

    def test_invalid_metadata(self):
        for key, value in [("command", 3), ("age_seconds", None), ("age_seconds", -1), ("payload_hex", "zz"), ("payload_hex", None)]:
            data = Data()
            data.meta[key] = value
            self.assertIsNone(decode(data))

    def test_stale_data(self):
        self.assertEqual(decode(Data(age=600)), "Self-Consumption")
        self.assertIsNone(decode(Data(age=601)))

    def test_external_changes(self):
        select = entity(Data(0))
        for mode, expected in [(0, "Self-Consumption"), (1, "Manual"), (5, "AI Optimization"), (2, None)]:
            select.coordinator.data = Data(mode)
            self.assertEqual(select.current_option, expected)

    def test_option_order(self):
        self.assertEqual(entity(Data())._attr_options, ["Self-Consumption", "AI Optimization", "Manual"])

    def test_runtime_mode_not_used(self):
        data = Data(1)
        for runtime_mode in range(8):
            data.work_mode = runtime_mode
            self.assertEqual(decode(data), "Manual")


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_write_failure(self):
        select = entity(Data(0))
        select.coordinator.device.send_command.return_value = False
        with self.assertRaisesRegex(HomeAssistantError, "Failed to send"):
            await select.async_select_option("Manual")
        self.assertEqual(select.current_option, "Self-Consumption")
        select.coordinator.device.send_command.assert_awaited_once_with(0x09, b"\x01")

    async def test_unsupported_modes(self):
        for mode in ["AI Optimization", "invalid"]:
            select = entity(Data())
            with self.assertRaises(HomeAssistantError):
                await select.async_select_option(mode)
            select.coordinator.device.send_command.assert_not_awaited()

    async def test_missing_readback(self):
        select = entity(Data())
        select.coordinator.device.send_command.side_effect = [True, False]
        with self.assertRaisesRegex(HomeAssistantError, "fresh readback"):
            await select.async_select_option("Manual")
        self.assertEqual(select.current_option, "Self-Consumption")

    async def test_old_matching_readback(self):
        select = entity(Data(1))
        with self.assertRaisesRegex(HomeAssistantError, "fresh readback"):
            await select.async_select_option("Manual")

    async def test_fresh_matching_readback(self):
        for option, mode, command in [("Manual", 1, 0x09), ("Self-Consumption", 0, 0x0E)]:
            select = entity(Data(5))
            calls = []
            async def send(cmd, payload=b""):
                calls.append((cmd, payload))
                if cmd == 0x0D:
                    select.coordinator.data = Data(mode, timestamp="after")
                return True
            select.coordinator.device.send_command.side_effect = send
            await select.async_select_option(option)
            self.assertEqual(calls, [(command, b"\x01"), (0x0D, b"")])
            self.assertEqual(select.current_option, option)

    async def test_fresh_mismatched_readback(self):
        select = entity(Data())
        async def send(cmd, payload=b""):
            if cmd == 0x0D:
                select.coordinator.data = Data(5, timestamp="after")
            return True
        select.coordinator.device.send_command.side_effect = send
        with self.assertRaisesRegex(HomeAssistantError, "not confirmed"):
            await select.async_select_option("Manual")
        self.assertEqual(select.current_option, "AI Optimization")

    async def test_short_fresh_readback(self):
        select = entity(Data())
        async def send(cmd, payload=b""):
            if cmd == 0x0D:
                select.coordinator.data = Data(1, timestamp="after", length=11)
            return True
        select.coordinator.device.send_command.side_effect = send
        with self.assertRaisesRegex(HomeAssistantError, "not confirmed"):
            await select.async_select_option("Manual")
        self.assertIsNone(select.current_option)


if __name__ == "__main__":
    unittest.main()
