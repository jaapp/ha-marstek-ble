"""Isolated selector regression tests; run python -m unittest discover -s tests.

The real selector AST is executed against lightweight HA base-class doubles.
This exercises decoding and command logic, not HA/BLE integration or hardware.
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
        self.writes = 0

    def async_write_ha_state(self):
        self.writes += 1


class SelectEntity:
    pass


class HomeAssistantError(Exception):
    pass


SOURCE = Path(__file__).resolve().parents[1] / "custom_components/marstek_ble/select.py"
tree = ast.parse(SOURCE.read_text())
names = {"_reported_operating_mode", "MarstekOperatingModeSelect"}
nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
constants = [node for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in {"MODE_STALE_AFTER_SECONDS", "OPERATING_MODE_OPTIONS"} for target in node.targets)]
module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *constants, *nodes], type_ignores=[])
namespace = {
    "CoordinatorEntity": CoordinatorEntity,
    "MarstekDataUpdateCoordinator": object,
    "SelectEntity": SelectEntity,
    "HomeAssistantError": HomeAssistantError,
    "EntityCategory": SimpleNamespace(CONFIG="config"),
    "CMD_SYSTEM_DATA": 0x0D,
    "CMD_AUTO_MODE": 0x0E,
    "CMD_WORK_MODE": 0x09,
    "_LOGGER": logging.getLogger(__name__),
}
exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), namespace)
decode = namespace["_reported_operating_mode"]
ModeSelect = namespace["MarstekOperatingModeSelect"]


class Data:
    def __init__(self, mode=0, age=0, timestamp="before", length=19):
        payload = bytearray(length)
        if length > 17:
            payload[17] = mode
        self.meta = {"command": 0x0D, "age_seconds": age, "timestamp": timestamp, "payload_hex": payload.hex()}
        self.work_mode = 2

    def get_field_metadata(self, field):
        assert field == "system_status"
        return self.meta


def entity(data):
    coordinator = SimpleNamespace(data=data, device=SimpleNamespace(send_command=AsyncMock(return_value=True)))
    return ModeSelect(coordinator, SimpleNamespace(entry_id="test"))


class DecodeTests(unittest.TestCase):
    def test_modes(self):
        for mode, expected in enumerate(["Self-Consumption", "AI Optimization", "Manual"]):
            with self.subTest(mode=mode):
                self.assertEqual(decode(Data(mode)), expected)

    def test_missing_data_and_metadata(self):
        self.assertIsNone(decode(None))
        data = Data()
        data.meta = None
        self.assertIsNone(decode(data))

    def test_short_payload(self):
        for length in [0, 11, 17, 18]:
            with self.subTest(length=length):
                self.assertIsNone(decode(Data(length=length)))

    def test_extended_payload(self):
        self.assertEqual(decode(Data(2, length=30)), "Manual")

    def test_unknown_enum(self):
        self.assertIsNone(decode(Data(255)))

    def test_invalid_metadata(self):
        for key, value in [("command", 3), ("age_seconds", None), ("age_seconds", -1), ("payload_hex", "zz"), ("payload_hex", None)]:
            with self.subTest(key=key, value=value):
                data = Data()
                data.meta[key] = value
                self.assertIsNone(decode(data))

    def test_staleness(self):
        self.assertEqual(decode(Data(age=600)), "Self-Consumption")
        self.assertIsNone(decode(Data(age=601)))

    def test_initialization_and_external_changes(self):
        select = entity(Data(0))
        self.assertEqual(select.current_option, "Self-Consumption")
        select.coordinator.data = Data(2)
        self.assertEqual(select.current_option, "Manual")
        select.coordinator.data = Data(1)
        self.assertEqual(select.current_option, "AI Optimization")
        self.assertIn(select.current_option, select._attr_options)
        select.coordinator.data = Data(255)
        self.assertIsNone(select.current_option)

    def test_runtime_mode_is_not_used(self):
        data = Data(2)
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

    async def test_unsupported_modes_do_not_write(self):
        for mode in ["AI Optimization", "invalid"]:
            select = entity(Data(0))
            with self.assertRaises(HomeAssistantError):
                await select.async_select_option(mode)
            select.coordinator.device.send_command.assert_not_awaited()

    async def test_missing_readback_does_not_set_requested_state(self):
        select = entity(Data(0))
        select.coordinator.device.send_command.side_effect = [True, False]
        with self.assertRaisesRegex(HomeAssistantError, "fresh readback"):
            await select.async_select_option("Manual")
        self.assertEqual(select.current_option, "Self-Consumption")

    async def test_old_matching_readback_is_not_confirmation(self):
        select = entity(Data(2))
        with self.assertRaisesRegex(HomeAssistantError, "fresh readback"):
            await select.async_select_option("Manual")

    async def test_fresh_readback_confirms_modes(self):
        for option, mode, command in [("Manual", 2, 0x09), ("Self-Consumption", 0, 0x0E)]:
            select = entity(Data(1))
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

    async def test_fresh_mismatched_mode_is_not_success(self):
        select = entity(Data(0))
        async def send(cmd, payload=b""):
            if cmd == 0x0D:
                select.coordinator.data = Data(1, timestamp="after")
            return True
        select.coordinator.device.send_command.side_effect = send
        with self.assertRaisesRegex(HomeAssistantError, "not confirmed"):
            await select.async_select_option("Manual")
        self.assertEqual(select.current_option, "AI Optimization")

    async def test_short_fresh_readback_is_not_confirmation(self):
        select = entity(Data(0))
        async def send(cmd, payload=b""):
            if cmd == 0x0D:
                select.coordinator.data = Data(2, timestamp="after", length=11)
            return True
        select.coordinator.device.send_command.side_effect = send
        with self.assertRaisesRegex(HomeAssistantError, "not confirmed"):
            await select.async_select_option("Manual")
        self.assertIsNone(select.current_option)


if __name__ == "__main__":
    unittest.main()
