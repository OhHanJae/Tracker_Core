import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, MagicMock

from src.common.settings import AppSettings, MotorSettings, save_settings
from src.common.status import CommandStatus, CommandResult, DeviceSummaryBit, now_ms
from src.communication.json_tcp import JsonLineServer, JsonTcpResponse
from src.communication.plc_memory_mapping import PlcInput, words_to_bytes
from src.core.application import CoreApplication
from src.devices.ptm_service import PtmService
from src.pt_motor.motor_control import MotorApiClient


class FunctionalCorrectionsTests(unittest.IsolatedAsyncioTestCase):
    def app(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'global.json'
        settings = AppSettings()
        settings.paths.logs_dir = str(Path(temp.name) / 'logs')
        save_settings(settings, path)
        app = CoreApplication(path, managed_worker=True)
        app._update_vision_work = Mock()
        self.addAsyncCleanup(app.stop)
        return app

    async def test_point_options_reach_real_motor_client_and_rejected_stop_keeps_polling(self):
        requests = []
        async def handle(command, params):
            requests.append((command, params))
            return JsonTcpResponse(True, {'motion_id': 'move-1'} if command == 'point.goto' else {'accepted': False})
        server = JsonLineServer('127.0.0.1', 0, handle)
        await server.start()
        self.addAsyncCleanup(server.stop)
        settings = MotorSettings(port=server._server.sockets[0].getsockname()[1])
        service = PtmService(settings, MotorApiClient(settings))
        options = dict(recipe_id=2, point_id=7, laser_after_move=False, laser_permit_token=0)
        await service.command('point.goto', options)
        await service.command('motion.stop', {'preserve_laser': True})
        self.assertEqual(requests, [('point.goto', options), ('motion.stop', {'preserve_laser': True})])
        self.assertEqual(service._poll_motion_id, 'move-1')
        self.assertEqual(service.state.poll_interval_ms, 50)

    async def test_data_request_keeps_plc_seq_and_queries_applied_target_seq(self):
        app = self.app()
        app.vision_service.state.enabled = app.vision_service.state.online = True
        app._active_recipe_id, app._active_point_id, app._active_target_seq = 2, 7, 100
        app._vision_target_synced = True
        app.plc_input = PlcInput(run_enable=True, command_code=5, command_seq=101, req_recipe_id=2, req_point_id=7)
        app.vision_service.request_nutrunner_data = AsyncMock(return_value={'supported': True, 'data_valid': True, 'command_seq': 100})
        app._accept_nutrunner_result = AsyncMock()
        await app._handle_plc_command()
        await app._handle_plc_command()
        await app._command_task
        app.vision_service.request_nutrunner_data.assert_awaited_once_with(2, 7, 100)
        self.assertEqual(app.command.last_seq, 101)
        self.assertEqual(app.command.status, CommandStatus.COMPLETE)
        self.assertEqual(app._accept_nutrunner_result.await_args.args[0]['command_seq'], 100)

    async def test_late_measurement_after_target_change_is_discarded(self):
        app = self.app()
        app.command.last_seq, app.command.status = 101, CommandStatus.BUSY
        app._active_recipe_id, app._active_point_id, app._active_target_seq = 2, 7, 100
        app._vision_target_synced = True
        async def respond(*_):
            app._active_point_id = 8
            return {'supported': True, 'data_valid': True}
        app.vision_service.request_nutrunner_data = respond
        app._accept_nutrunner_result = AsyncMock()
        await app._dispatch_plc_command(5, 101, 'vision.result', {'recipe_id': 2, 'point_id': 7, 'target_seq': 100})
        app._accept_nutrunner_result.assert_not_awaited()
        self.assertEqual(app.command.result, CommandResult.TRACKING_UNAVAILABLE)

    async def test_busy_slot_overwrite_never_executes_after_completion(self):
        app = self.app()
        app._command_seen = True
        app._command_fingerprint = (100, 1, 2, 7)
        app.command.last_seq, app.command.status = 100, CommandStatus.BUSY
        app.plc_input = PlcInput(run_enable=True, command_code=2, command_seq=101)
        await app._handle_plc_command()
        self.assertEqual(app.command.last_seq, 100)
        self.assertTrue(app._command_protocol_error)
        app.command.status = CommandStatus.COMPLETE
        await app._handle_plc_command()
        self.assertIsNone(app._pending_request)
        self.assertEqual(app.command.last_seq, 100)
        app.plc_input = PlcInput(command_code=0)
        await app._handle_plc_command()
        self.assertEqual(app._command_protocol_error, '')

    async def test_startup_uses_live_heartbeat_and_idle_slot_not_synthetic_defaults(self):
        app = self.app()
        app._plc_startup_sync_required = app._gateway_configured = True
        app.plc_memory = MagicMock()
        header = SimpleNamespace(status_flags=3, last_read_time_ns=1, read_sequence=2)
        app.plc_memory.header.return_value = header
        words = [0] * 100
        words[0], words[7], words[12], words[13] = 1, 1, 1, 100
        app.plc_memory.read_plc_data.return_value = words_to_bytes(words)
        app._read_plc_input(1000)
        await app._handle_plc_command()
        self.assertTrue(app._plc_startup_sync_required)
        self.assertIsNone(app._pending_request)
        words[0], words[7], words[12] = 0, 2, 0
        header.read_sequence = 4
        app.plc_memory.read_plc_data.return_value = words_to_bytes(words)
        app._read_plc_input(1100)
        self.assertFalse(app._plc_startup_sync_required)
        self.assertEqual(app.command.last_seq, 100)

    async def test_idle_and_startup_stop_once_then_allow_fresh_manual_jog(self):
        for startup, run_enable in ((False, False), (True, False), (True, True)):
            with self.subTest(startup=startup, run_enable=run_enable):
                app = self.app()
                app.plc_input = PlcInput(run_enable=run_enable, laser_mode=1)
                app._plc_startup_sync_required = startup
                app.ptm_service.state.online = True
                app.ptm_service.state.last_ok_ms = now_ms()
                app.ptm_service.state.status = {'moving': False, 'raw': {'motion': {'state': 'idle'}}}
                app._perform_safe_stop = AsyncMock(return_value=True)
                await app._apply_safe_stop_if_needed()
                await app._safe_stop_task
                await app._apply_safe_stop_if_needed()
                self.assertTrue(app._safe_stop_applied)
                app.ptm_service.state.status = {'moving': True, 'raw': {'motion': {'state': 'jog'}}}
                app._safe_stop_retry_ms = 0
                await app._apply_safe_stop_if_needed()
                self.assertIsNone(app._safe_stop_task)
                app._perform_safe_stop.assert_awaited_once_with(preserve_manual=True)

    async def test_manual_jog_still_stops_on_force_stop_fault_or_restart(self):
        for reason in ('force_stop', 'fault', 'restart'):
            with self.subTest(reason=reason):
                app = self.app()
                app.plc_input = PlcInput(run_enable=False, laser_mode=1)
                app._plc_startup_sync_required = app._safe_stop_applied = True
                app.ptm_service.state.online = True
                app.ptm_service.state.last_ok_ms = now_ms()
                app.ptm_service.state.status = {'moving': True, 'raw': {'motion': {'state': 'jog'}}}
                app._perform_safe_stop = AsyncMock(return_value=True)
                if reason == 'force_stop':
                    app.plc_input.force_stop = True
                elif reason == 'fault':
                    app.alarms.set_fault_word(DeviceSummaryBit.CONTROLLER, 1)
                else:
                    app._controller_restart_request = {'requested_ms': now_ms()}
                await app._apply_safe_stop_if_needed()
                self.assertIsNotNone(app._safe_stop_task)
                await app._safe_stop_task
                app._perform_safe_stop.assert_awaited_once()

    async def test_idle_does_not_exempt_automatic_or_stale_motor_motion(self):
        for state, age in (('tracking', 0), ('jog', 1001), (None, 0)):
            with self.subTest(state=state, age=age):
                app = self.app()
                app.plc_input = PlcInput(run_enable=False, laser_mode=1)
                app._safe_stop_applied = True
                app.ptm_service.state.online = True
                app.ptm_service.state.last_ok_ms = now_ms() - age
                app.ptm_service.state.status = {'moving': True, 'raw': {'motion': {'state': state}}}
                app._perform_safe_stop = AsyncMock(return_value=True)
                await app._apply_safe_stop_if_needed()
                self.assertIsNotNone(app._safe_stop_task)
                await app._safe_stop_task
                app._perform_safe_stop.assert_awaited_once()

    async def test_axis_errors_use_matching_fresh_ptm_feedback_and_pan_wrap(self):
        app = self.app()
        app._active_recipe_id = app._active_point_id = 1
        app._active_motion_id = 'move'
        app.ptm_service.state.online = True
        app.ptm_service.state.last_ok_ms = 1000
        raw = {'motion': {'target_position': {'pan': 359.9, 'tilt': 10}, 'target_motion_id': 'move', 'completed_id': 'move'},
               'position': {'pan': .1, 'tilt': 9.5}, 'position_age_ms': {'pan': 0, 'tilt': 0}}
        app.ptm_service.state.status = {'raw': raw}
        pan, tilt = app._ptm_target_errors(1100)
        self.assertAlmostEqual(pan, .2)
        self.assertEqual(tilt, .5)
        self.assertIsNone(app._ptm_target_errors(1501))
        raw['motion']['target_motion_id'] = 'other'
        self.assertIsNone(app._ptm_target_errors(1000))

    async def test_recipe_change_during_motion_keeps_configuration_error_result(self):
        app = self.app()
        app.command.status = CommandStatus.BUSY
        app._active_motion = {"id": "move", "code": 1, "accepted_ms": 1000}
        app.ptm_service.state.online = True
        app.ptm_service.state.last_ok_ms = 1100
        app.ptm_service.state.status = {
            "raw": {"motion": {"failed_id": "move", "last_error": "CONFIG_OR_CAL_ERROR"}}}
        app._progress_motion_command(1100)
        self.assertEqual(app.command.status, CommandStatus.ERROR)
        self.assertEqual(app.command.result, CommandResult.CONFIG_OR_CAL_ERROR)
        self.assertIsNone(app._active_motion)

    async def test_configured_laser_tolerance_and_strict_ids(self):
        self.assertEqual(PlcInput().applied_laser_tolerance(50), 50)
        client = AsyncMock(spec=MotorApiClient)
        service = PtmService(MotorSettings(), client)
        for value in (True, '2', 0, 65536):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await service.command('point.goto', {'recipe_id': value, 'point_id': 7})
        client.point_goto.assert_not_awaited()


class JsonInputBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_params_nan_and_idle_client_do_not_block_other_clients(self):
        handler = AsyncMock(return_value=JsonTcpResponse(True, {'alive': True}))
        server = JsonLineServer('127.0.0.1', 0, handler, read_timeout_s=.2, write_timeout_s=.02)
        await server.start()
        self.addAsyncCleanup(server.stop)
        port = server._server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        try:
            for payload in (b'{"command":"ping","params":false}\n', b'{"command":"ping","params":{"x":NaN}}\n'):
                writer.write(payload)
                await writer.drain()
                self.assertFalse(json.loads(await reader.readline())['ok'])
            handler.assert_not_awaited()
            writer.write(b'{"command":"ping","params":{}}\n')
            await writer.drain()
            self.assertTrue(json.loads(await reader.readline())['ok'])
            self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')
        finally:
            writer.close()
            await writer.wait_closed()
        blocked_writer = MagicMock()
        blocked_writer.drain = AsyncMock(side_effect=asyncio.Event().wait)
        with self.assertRaises(asyncio.TimeoutError):
            await server._write_http_response(blocked_writer, '200 OK', b'body', 'text/plain', 'GET')
