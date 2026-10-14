"""PLC -> real Core -> loopback JSON Lines -> Core -> PLC word assertions."""
from __future__ import annotations

import asyncio
import copy
import unittest
from unittest.mock import AsyncMock, patch

from src.common.settings import settings_from_dict
from src.common.status import CommandCode, CommandResult, CommandStatus, DeviceSummaryBit, now_ms
from src.communication.json_tcp import JsonTcpError
from src.vision.vision_client import normalize_nutrunner_result
from tests.support import CoreTestMixin, measurement


class CoreVisionContractTests(CoreTestMixin, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.core = self.make_core()

    async def responder(self, request, writer):
        self.assertEqual(request["command"], "vision.result")
        self.assertEqual(set(request["params"]), {"recipe_id", "point_id", "command_seq"})
        return {"id": request["id"], "ok": True, "result": measurement(request["params"])}

    def assert_plc(self, words, status, result, seq=105, code=5):
        self.assertEqual(words[12:16], [code, seq, int(status), int(result)])
        self.assertFalse(words[0] & (1 << 5))  # Terminal responses release COMMAND_BUSY.

    async def test_supported_data_completes_without_creating_position_permit(self):
        peer = await self.connect_peer(self.responder)
        words = await self.submit()
        self.assert_plc(words, CommandStatus.COMPLETE, CommandResult.OK)
        self.assertEqual(peer.requests[0]["params"], {"recipe_id": 3, "point_id": 25, "command_seq": 105})
        self.assertTrue(self.core.nutrunner_result["data_valid"])
        self.assertEqual(self.core._active_recipe_id, 0)
        self.assertFalse(words[1] & ((1 << 12) | (1 << 13) | (1 << 14)))

    async def test_unsupported_result_rejects_with_null_measurements(self):
        async def reply(request, writer):
            return {"id": request["id"], "ok": True, "result": measurement(
                request["params"], supported=False, data_valid=False)}
        await self.connect_peer(reply)
        self.core.nutrunner_result = {"data_valid": True, "old": True}
        words = await self.submit()
        self.assert_plc(words, CommandStatus.REJECTED, CommandResult.TRACKING_UNAVAILABLE)
        self.assertIs(self.core.nutrunner_result["supported"], False)
        self.assertIsNone(self.core.nutrunner_result["tightening_angle_deg"])
        self.assertNotIn("old", self.core.nutrunner_result)

    async def test_camera_offline_does_not_block_recipe_contract_query(self):
        async def reply(request, writer):
            return {"id": request["id"], "ok": True, "result": measurement(
                request["params"], supported=False, data_valid=False)}
        peer = await self.connect_peer(reply)
        self.core.vision_service.state.online = False
        words = await self.submit()
        self.assert_plc(words, CommandStatus.REJECTED, CommandResult.TRACKING_UNAVAILABLE)
        self.assertEqual(len(peer.requests), 1)
        self.assertIs(self.core.nutrunner_result["supported"], False)
        self.assertFalse(self.core.vision_service.state.online)

    async def test_missing_recipe_point_and_other_vision_errors(self):
        for index, (error, status, result) in enumerate([
            ("RECIPE_NOT_FOUND", CommandStatus.REJECTED, CommandResult.RECIPE_NOT_FOUND),
            ("POINT_NOT_FOUND", CommandStatus.REJECTED, CommandResult.POINT_NOT_FOUND),
            ("INVALID_PARAMS", CommandStatus.ERROR, CommandResult.TRACKING_UNAVAILABLE),
            ("UNKNOWN_COMMAND", CommandStatus.ERROR, CommandResult.TRACKING_UNAVAILABLE),
            ("TRACKING_UNAVAILABLE", CommandStatus.ERROR, CommandResult.TRACKING_UNAVAILABLE),
        ]):
            with self.subTest(error=error):
                async def reply(request, writer):
                    return {"id": request["id"], "ok": False, "error": {"code": error, "message": "test"}}
                await self.connect_peer(reply)
                self.core.nutrunner_result = {"old": True}
                words = await self.submit(seq=100 + index)
                self.assert_plc(words, status, result, seq=100 + index)
                self.assertIsNone(self.core.nutrunner_result)

    async def test_supported_but_invalid_data_rejects(self):
        async def reply(request, writer):
            return {"id": request["id"], "ok": True, "result": measurement(request["params"], data_valid=False)}
        await self.connect_peer(reply)
        words = await self.submit()
        self.assert_plc(words, CommandStatus.REJECTED, CommandResult.TRACKING_UNAVAILABLE)
        self.assertTrue(self.core.nutrunner_result["supported"])
        self.assertFalse(self.core.nutrunner_result["data_valid"])

    async def test_mismatched_result_identifiers_fail_closed(self):
        for index, key in enumerate(("recipe_id", "point_id", "command_seq")):
            with self.subTest(key=key):
                async def reply(request, writer):
                    result = measurement(request["params"])
                    result[key] += 1
                    return {"id": request["id"], "ok": True, "result": result}
                await self.connect_peer(reply)
                words = await self.submit(seq=110 + index)
                self.assert_plc(words, CommandStatus.ERROR, CommandResult.TRACKING_UNAVAILABLE, seq=110 + index)
                self.assertIsNone(self.core.nutrunner_result)

    async def test_timeout_clears_result_and_releases_busy(self):
        async def silent(request, writer):
            await asyncio.Event().wait()
        await self.connect_peer(silent)
        self.core.nutrunner_result = {"old": True}
        words = await self.submit()
        self.assert_plc(words, CommandStatus.ERROR, CommandResult.TRACKING_UNAVAILABLE)
        self.assertIn("timeout", self.core.command.message)
        self.assertIsNone(self.core.nutrunner_result)

    async def test_same_sequence_is_not_redispatched_and_wraparound_is_valid(self):
        peer = await self.connect_peer(self.responder)
        for seq in (65535, 65535, 0, 0, 1):
            await self.submit(seq=seq)
        self.assertEqual([r["params"]["command_seq"] for r in peer.requests], [65535, 0, 1])

    async def test_invalid_ids_disabled_and_legacy_map_do_not_send(self):
        peer = await self.connect_peer(self.responder)
        for index, (recipe, point, result) in enumerate([
            (0, 25, CommandResult.RECIPE_NOT_FOUND),
            (65536, 25, CommandResult.RECIPE_NOT_FOUND),
            (3, 0, CommandResult.POINT_NOT_FOUND),
            (3, 65536, CommandResult.POINT_NOT_FOUND),
        ]):
            words = await self.submit(recipe=recipe, point=point, seq=index)
            self.assert_plc(words, CommandStatus.REJECTED, result, seq=index)
        self.core.vision_service.state.enabled = False
        self.assert_plc(await self.submit(seq=4), CommandStatus.REJECTED, CommandResult.TRACKING_UNAVAILABLE, seq=4)
        self.core.vision_service.state.enabled = True
        self.core.settings.command_map["5"] = {"target": "core", "command": "reload_config"}
        self.assert_plc(await self.submit(seq=5), CommandStatus.REJECTED, CommandResult.INVALID_COMMAND, seq=5)
        self.assertEqual(peer.requests, [])

    async def test_bypass_completes_without_synthesizing_measurements(self):
        peer = await self.connect_peer(self.responder)
        self.core.vision_service.state.online = False
        self.core.settings.vision.bypass = True
        words = await self.submit()
        self.assert_plc(words, CommandStatus.COMPLETE, CommandResult.OK)
        self.assertTrue(words[40] & (1 << 1))
        self.assertIsNone(self.core.nutrunner_result)
        self.assertEqual(peer.requests, [])
        self.assertFalse(words[1] & ((1 << 12) | (1 << 13) | (1 << 14)))

    async def test_force_stop_and_run_disable_reject_before_send(self):
        peer = await self.connect_peer(self.responder)
        for seq, run, stop, result in [
            (1, True, True, CommandResult.FORCE_STOP_ACTIVE),
            (2, False, False, CommandResult.SYSTEM_NOT_READY),
        ]:
            self.assert_plc(await self.submit(seq=seq, run=run, stop=stop), CommandStatus.REJECTED, result, seq=seq)
        self.assertEqual(peer.requests, [])

    async def test_current_bypass_flags_do_not_represent_valid_measurements(self):
        # The 2026-10-13 master permits bypass process flags without a camera.
        self.core.vision_service.state.online = False
        self.core.settings.vision.bypass = True
        await self.submit()
        self.core.plc_input.tracking_enable = True
        self.core._build_output(10000)
        words = self.core.output.to_words()
        self.assertTrue(words[40] & (1 << 1))
        self.assertTrue(words[1] & (1 << 0))  # CAMERA_READY is a bypass process flag.
        self.assertTrue(words[1] & (1 << 13))
        self.assertTrue(words[1] & (1 << 14))
        self.assertFalse(self.core.vision_snapshot.tracker_valid)
        self.assertIsNone(self.core.nutrunner_result)

    async def test_unrelated_fault_does_not_abort_read_only_query(self):
        self.core.alarms.set_fault_word(DeviceSummaryBit.PAN_TILT_MOTOR, 1 << 6)
        await self.connect_peer(self.responder)
        self.assert_plc(await self.submit(), CommandStatus.COMPLETE, CommandResult.OK)
        self.assertTrue(self.core.alarms.fault_summary())

    async def test_aborted_or_superseded_response_does_not_overwrite_command(self):
        release = asyncio.Event()
        self.core.vision_service.request_nutrunner_data = AsyncMock(side_effect=lambda *a: None)
        async def delayed(*args):
            await release.wait()
            return measurement({"recipe_id": 3, "point_id": 25, "command_seq": 105})
        self.core.vision_service.request_nutrunner_data.side_effect = delayed
        self.core.command.last_seq = 105
        self.core.command.status = CommandStatus.BUSY
        task = asyncio.create_task(self.core._dispatch_plc_command(5, 105, "vision.result", {"recipe_id": 3, "point_id": 25}))
        await asyncio.sleep(0)
        self.core.command.last_seq = 106
        self.core.command.status = CommandStatus.ERROR
        self.core.command.result = CommandResult.FORCE_STOP_ACTIVE
        release.set()
        await task
        self.assertIsNone(self.core.nutrunner_result)
        self.assertEqual(self.core.command.result, CommandResult.FORCE_STOP_ACTIVE)

    async def test_safe_stop_cancels_pending_query(self):
        entered = asyncio.Event()
        async def wait(*args):
            entered.set()
            await asyncio.Event().wait()
        self.core.vision_service.request_nutrunner_data = AsyncMock(side_effect=wait)
        self.core.plc_input.run_enable = True
        self.core.plc_input.command_code = 5
        self.core.plc_input.command_seq = 105
        self.core.plc_input.req_recipe_id = 3
        self.core.plc_input.req_point_id = 25
        await self.core._handle_plc_command()
        await self.core._handle_plc_command()
        task = self.core._command_task
        await entered.wait()
        self.core.plc_input.force_stop = True
        self.core._perform_safe_stop = AsyncMock(return_value=True)
        await self.core._apply_safe_stop_if_needed()
        await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(task.cancelled())
        self.assertEqual(self.core.command.status, CommandStatus.ERROR)
        self.assertEqual(self.core.command.result, CommandResult.FORCE_STOP_ACTIVE)
        self.assertIsNone(self.core.nutrunner_result)

    async def test_legacy_motion_home_reset_and_reserved_codes(self):
        self.core.ptm_service.command = AsyncMock(return_value={"motion_id": "move-test"})
        for code in (1, 2):
            motion_id = f"move-test-{code}"
            self.core.ptm_service.command.return_value = {"motion_id": motion_id}
            words = await self.submit(code=code, seq=code)
            self.assertEqual(words[14], int(CommandStatus.BUSY))
            call = self.core.ptm_service.command.call_args
            self.assertEqual(call.args[0], "point.goto" if code == 1 else "home.start")
            self.core.ptm_service.state.last_ok_ms = now_ms() + 1
            self.core.ptm_service.state.status["raw"] = {"motion": {"completed_id": motion_id}}
            self.core._progress_motion_command(now_ms())
            self.assertEqual(self.core.command.status, CommandStatus.COMPLETE)
        self.core.alarms.set_fault_word(DeviceSummaryBit.RECIPE, 1)
        self.core.alarms.set_fault_word(DeviceSummaryBit.RECIPE, 0)
        self.assert_plc(await self.submit(code=3, seq=3, run=False, stop=True), CommandStatus.COMPLETE, CommandResult.OK, seq=3, code=3)
        self.assertFalse(self.core.alarms.latched_faults)
        for code in (4, 6, 65535):
            self.assert_plc(await self.submit(code=code, seq=code), CommandStatus.REJECTED, CommandResult.INVALID_COMMAND, seq=code, code=code)

    async def test_json_reload_config_is_independent_of_plc_code_five(self):
        self.core._replace_settings = AsyncMock()
        response = await self.core.handle_tcp_command("reload_config", {})
        self.assertTrue(response.ok)
        self.core._replace_settings.assert_awaited_once()
        self.assertEqual(self.core.command.last_code, 0)


class EnumAndResultValidationTests(unittest.TestCase):
    def test_plc_command_names_have_unique_wire_values(self):
        members = list(CommandCode.__members__.values())
        self.assertEqual(len(members), len(set(members)))
        self.assertEqual(CommandCode(5).name, "NUTRUNNER_DATA_REQUEST")

    def test_older_config_gains_code_five_without_changing_codes_one_to_four(self):
        settings = settings_from_dict({"vision": {"enabled": False}, "command_map": {"1": {"params": {"extra": "kept"}}}})
        self.assertEqual(settings.command_map["5"]["command"], "vision.result")
        self.assertEqual(settings.command_map["1"]["params"]["extra"], "kept")
        self.assertEqual([settings.command_map[str(i)]["command"] for i in (1, 2, 3, 4)], ["point.goto", "home.start", "alarm.reset", "controller.restart_request"])

    def test_measurement_validation_rejects_nonfinite_bool_missing_and_wrong_ids(self):
        params = {"recipe_id": 3, "point_id": 25, "command_seq": 105}
        good = measurement(params)
        mutations = [("recipe_id", True), ("command_seq", 106), ("supported", "true"),
                     ("supported", False), ("data_valid", 1), ("timestamp_ms", 0),
                     ("timestamp_ms", True), ("coordinate_frame", " "),
                     ("tightening_angle_deg", None), ("tightening_angle_deg", True),
                     ("tightening_angle_deg", float("nan")), ("inclination_deg", float("inf")),
                     ("fastening_coordinate_mm", {"x": 1, "y": 2}),
                     ("fastening_coordinate_mm", {"x": False, "y": 2, "z": 3})]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                invalid = copy.deepcopy(good)
                invalid[key] = value
                with self.assertRaises(JsonTcpError):
                    normalize_nutrunner_result(invalid, 3, 25, 105)

    def test_invalid_data_never_retains_numbers_or_timestamps(self):
        data = measurement({"recipe_id": 3, "point_id": 25, "command_seq": 0})
        data.update(supported=False, data_valid=False)
        result = normalize_nutrunner_result(data, 3, 25, 0)
        self.assertIsNone(result["timestamp_ms"])
        self.assertIsNone(result["tightening_angle_deg"])
        self.assertEqual(result["fastening_coordinate_mm"], {"x": None, "y": None, "z": None})
