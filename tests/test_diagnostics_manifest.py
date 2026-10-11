"""Isolated diagnostics tests: python -m unittest discover -s tests.

Runs the real helper and diagnostics handler AST with HA doubles and a real
worker thread. Does not replace a full Home Assistant integration test.
"""
import ast
import asyncio
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

SOURCE = Path(__file__).resolve().parents[1] / "custom_components/marstek_ble/diagnostics.py"
tree = ast.parse(SOURCE.read_text())
names = {"_load_manifest_version", "async_get_config_entry_diagnostics"}
nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes], type_ignores=[])
CODE = compile(ast.fix_missing_locations(module), str(SOURCE), "exec")


class ManifestDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.loop_thread = threading.get_ident()
        self.observed = []
        self.text = '{"version": "0.4.0-rc3"}'
        self.read_error = None

        def resource_files(package):
            self.observed.append(("resource_lookup", threading.get_ident()))
            self.assertEqual(package, "custom_components.marstek_ble")
            return SimpleNamespace(joinpath=lambda name: SimpleNamespace(read_text=read_text))

        def read_text(encoding):
            self.observed.append(("read_text", threading.get_ident()))
            self.assertEqual(encoding, "utf-8")
            if self.read_error:
                raise self.read_error
            return self.text

        def loads(text):
            self.observed.append(("json_parse", threading.get_ident()))
            return json.loads(text)

        self.redactor = Mock(side_effect=lambda data, keys: data)
        self.ns = {
            "__package__": "custom_components.marstek_ble",
            "resources": SimpleNamespace(files=resource_files),
            "json": SimpleNamespace(loads=loads, JSONDecodeError=json.JSONDecodeError),
            "DOMAIN": "marstek_ble", "HA_VERSION": "test-ha",
            "TO_REDACT": {"wifi_ssid", "wifi_name", "meter_ip", "network_info", "mac_address", "device_id"},
            "_coordinator_diagnostics": lambda coordinator: {"marker": "unchanged"},
            "async_redact_data": self.redactor,
        }
        exec(CODE, self.ns)
        self.hass = SimpleNamespace(data={}, async_add_executor_job=AsyncMock(side_effect=asyncio.to_thread))
        self.entry = SimpleNamespace(runtime_data=object(), entry_id="entry", title="Battery", source="user",
                                     unique_id="id", minor_version=1, version=1, data={}, options={})

    async def read_diagnostics(self):
        return await self.ns["async_get_config_entry_diagnostics"](self.hass, self.entry)

    def assert_worker_only(self):
        self.assertTrue(self.observed)
        for operation, thread in self.observed:
            self.assertNotEqual(thread, self.loop_thread, operation)
        self.hass.async_add_executor_job.assert_awaited_once_with(self.ns["_load_manifest_version"])

    async def test_all_manifest_operations_run_in_executor(self):
        result = await self.read_diagnostics()
        self.assertEqual(result["environment"]["integration_version"], "0.4.0-rc3")
        self.assertEqual([op for op, _ in self.observed], ["resource_lookup", "read_text", "json_parse"])
        self.assert_worker_only()

    async def test_missing_manifest(self):
        self.read_error = FileNotFoundError()
        result = await self.read_diagnostics()
        self.assertIsNone(result["environment"]["integration_version"])
        self.assert_worker_only()

    async def test_unreadable_manifest(self):
        self.read_error = PermissionError()
        result = await self.read_diagnostics()
        self.assertIsNone(result["environment"]["integration_version"])
        self.assert_worker_only()

    async def test_invalid_json(self):
        self.text = "not json"
        result = await self.read_diagnostics()
        self.assertIsNone(result["environment"]["integration_version"])
        self.assert_worker_only()

    async def test_missing_version(self):
        self.text = "{}"
        result = await self.read_diagnostics()
        self.assertIsNone(result["environment"]["integration_version"])
        self.assert_worker_only()

    async def test_numeric_version_preserves_conversion(self):
        self.text = '{"version": 123}'
        result = await self.read_diagnostics()
        self.assertEqual(result["environment"]["integration_version"], "123")
        self.assert_worker_only()

    async def test_missing_coordinator_does_not_read_manifest(self):
        self.entry.runtime_data = None
        self.assertEqual(await self.read_diagnostics(), {"error": "coordinator_not_available"})
        self.hass.async_add_executor_job.assert_not_awaited()
        self.assertEqual(self.observed, [])

    async def test_fallback_coordinator(self):
        self.entry.runtime_data = None
        self.hass.data = {"marstek_ble": {"entry": {"coordinator": object()}}}
        self.assertIn("environment", await self.read_diagnostics())
        self.assert_worker_only()

    async def test_output_and_redaction_path_preserved(self):
        result = await self.read_diagnostics()
        self.assertEqual(result["coordinator"], {"marker": "unchanged"})
        self.assertEqual(result["config_entry"]["entry_id"], "entry")
        self.redactor.assert_called_once_with(result, self.ns["TO_REDACT"])


if __name__ == "__main__":
    unittest.main()
