from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from src.common.settings import AppSettings, save_settings
from src.common.status import CommandResult, CommandStatus
from src.communication.json_tcp import JsonTcpError
from src.communication.plc_memory_mapping import PlcInput
from src.core.application import CoreApplication, LOGGER


class CommandDiagnosticsTest(unittest.IsolatedAsyncioTestCase):
    def make_app(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "global.json"
        save_settings(AppSettings(), path)
        app = CoreApplication(path, managed_worker=True)
        app._update_vision_work = Mock()
        app.ptm_service.state.online = True
        app.ptm_service.state.status = {"connected": True, "ready": True, "homed": True}
        app.ptm_service.command = AsyncMock()
        app.vision_service.command = AsyncMock()
        return app

    async def test_unknown_command_logs_once_without_changing_duplicate_handling(self):
        app = self.make_app()
        app.plc_input = PlcInput(command_code=65535, command_seq=0)
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            await app._handle_plc_command()
            state = app.command.to_dict()
            await app._handle_plc_command()
            app.plc_input.command_code = 0
            await app._handle_plc_command()
        self.assertEqual(app.command.to_dict(), state)
        self.assertEqual(app.command.status, CommandStatus.REJECTED)
        self.assertEqual(app.command.result, CommandResult.INVALID_COMMAND)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("code=65535 seq=0 reason=unsupported_command", logs.output[0])
        self.assertIsNone(app._pending_request)
        app.ptm_service.command.assert_not_awaited()
        app.vision_service.command.assert_not_awaited()

    async def test_invalid_motor_map_and_missing_ids_keep_existing_results(self):
        for code, recipe, point, bad_map, result in (
            (3, 1, 1, True, CommandResult.INVALID_COMMAND),
            (1, 0, 1, False, CommandResult.RECIPE_NOT_FOUND),
            (1, 1, 0, False, CommandResult.POINT_NOT_FOUND),
        ):
            with self.subTest(code=code, recipe=recipe, point=point):
                app = self.make_app()
                app.plc_input = PlcInput(run_enable=True, command_code=code, command_seq=42,
                                         req_recipe_id=recipe, req_point_id=point)
                if bad_map:
                    app.settings.command_map[str(code)]["command"] = "missing.command"
                with self.assertLogs(LOGGER, level="WARNING") as logs:
                    await app._handle_plc_command()
                self.assertEqual(app.command.status, CommandStatus.REJECTED)
                self.assertEqual(app.command.result, result)
                self.assertEqual(app.command.message, "")
                self.assertIn("seq=42", logs.output[0])
                self.assertIsNone(app._pending_request)
                app.ptm_service.command.assert_not_awaited()

    async def test_vision_rejection_diagnostics_keep_id_validation_and_map_results(self):
        for code in (5, 6):
            for recipe, point, bad_map, result, reason in (
                (0, 7, False, CommandResult.RECIPE_NOT_FOUND, "invalid_recipe_id"),
                (65536, 7, False, CommandResult.RECIPE_NOT_FOUND, "invalid_recipe_id"),
                (2, 0, False, CommandResult.POINT_NOT_FOUND, "invalid_point_id"),
                (2, 65536, False, CommandResult.POINT_NOT_FOUND, "invalid_point_id"),
                (2, 7, True, CommandResult.INVALID_COMMAND, "command_map_mismatch"),
            ):
                with self.subTest(code=code, recipe=recipe, point=point, bad_map=bad_map):
                    app = self.make_app()
                    app.plc_input = PlcInput(req_recipe_id=recipe, req_point_id=point)
                    if bad_map:
                        app.settings.command_map[str(code)]["command"] = "missing.command"
                    with self.assertLogs(LOGGER, level="WARNING") as logs:
                        app._prepare_nutrunner_request(code, 19)
                    self.assertEqual(app.command.status, CommandStatus.REJECTED)
                    self.assertEqual(app.command.result, result)
                    self.assertIn(f"code={code} seq=19", logs.output[0])
                    self.assertIn(reason, logs.output[0])
                    self.assertIsNone(app._pending_request)
                    app.vision_service.command.assert_not_awaited()

    async def test_dispatch_error_keeps_result_and_records_request_context(self):
        app = self.make_app()
        app.command.last_seq = 23
        app.command.status = CommandStatus.BUSY
        app.vision_service.request_nutrunner_data = AsyncMock(
            side_effect=JsonTcpError("unknown Vision item", code="RECIPE_NOT_FOUND"))
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            await app._dispatch_plc_command(5, 23, "vision.result", {"recipe_id": 2, "point_id": 7, "target_seq": 22})
        self.assertEqual(app.command.status, CommandStatus.REJECTED)
        self.assertEqual(app.command.result, CommandResult.RECIPE_NOT_FOUND)
        self.assertEqual(app.command.message, "unknown Vision item")
        self.assertIn("code=5 seq=23 command=vision.result recipe_id=2 point_id=7", logs.output[0])
        self.assertIn("error_code=RECIPE_NOT_FOUND", logs.output[0])
        self.assertEqual(app.alarms.fault_summary(), 0)


if __name__ == "__main__":
    unittest.main()
