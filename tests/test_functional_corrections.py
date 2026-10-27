import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, MagicMock

from src.common.settings import AppSettings, MotorSettings, save_settings
from src.common.status import CommandStatus, CommandResult, DeviceSummaryBit, now_ms
from src.communication.json_tcp import JsonLineServer, JsonTcpResponse, JsonTcpError
from src.communication.plc_memory_mapping import PlcInput, words_to_bytes
from src.core.application import CoreApplication
from src.devices.ptm_service import PtmService
from src.pt_motor.motor_control import MotorApiClient
from src.processes.manager import ProcessManager


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

    async def test_terminal_result_allows_staging_new_target_before_sequence_commit(self):
        for terminal in (CommandStatus.COMPLETE, CommandStatus.REJECTED, CommandStatus.ERROR):
            for old_seq, next_seq in ((100, 101), (65535, 0)):
                with self.subTest(terminal=terminal, old_seq=old_seq):
                    app = self.app()
                    app.ptm_service.state.online = True
                    app.ptm_service.state.status = {'connected': True, 'ready': True, 'homed': True}
                    app._command_seen = True
                    app._command_fingerprint = (old_seq, 2, 0, 0)
                    app.command.last_code, app.command.last_seq = 2, old_seq
                    app.command.status, app.command.result = terminal, CommandResult.COMMAND_BUSY
                    app.plc_input = PlcInput(run_enable=True, command_code=1, command_seq=old_seq,
                        req_recipe_id=2, req_point_id=8)
                    await app._handle_plc_command()
                    self.assertEqual(app.command.status, terminal)
                    self.assertIsNone(app._pending_request)
                    app.plc_input.command_seq = next_seq
                    await app._handle_plc_command()
                    self.assertEqual(app._command_protocol_error, '')
                    self.assertEqual(app.command.last_seq, next_seq)
                    self.assertEqual(app.command.status, CommandStatus.RECEIVED)
                    self.assertEqual(app._pending_request[3]['point_id'], 8)

    async def test_startup_rejection_acknowledges_new_sequence_without_replaying_after_sync(self):
        app = self.app()
        app._plc_startup_sync_required = True
        app._command_seen = True
        app._command_fingerprint = (100, 2, 0, 0)
        app.command.last_code, app.command.last_seq = 2, 100
        app.command.status, app.command.result = CommandStatus.REJECTED, CommandResult.COMMAND_BUSY
        app.plc_input = PlcInput(run_enable=True, command_code=2, command_seq=101)
        await app._handle_plc_command()
        self.assertEqual(app.command.last_seq, 101)
        self.assertEqual(app.command.status, CommandStatus.REJECTED)
        self.assertEqual(app.command.result, CommandResult.SYSTEM_NOT_READY)
        self.assertIn('startup', app.command.message.lower())
        self.assertIsNone(app._pending_request)
        app._plc_startup_sync_required = False
        await app._handle_plc_command()
        self.assertIsNone(app._pending_request)
        app.ptm_service.state.online = True
        app.ptm_service.state.status = {'connected': True, 'ready': True}
        app.plc_input.command_seq = 102
        await app._handle_plc_command()
        self.assertEqual(app.command.last_seq, 102)
        self.assertEqual(app.command.status, CommandStatus.RECEIVED)

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

    async def test_received_slot_overwrite_is_not_queued_after_original_completion(self):
        for replacement_seq in (100, 101):
            with self.subTest(replacement_seq=replacement_seq):
                app = self.app()
                app.plc_input = PlcInput(run_enable=True, command_code=2, command_seq=100)
                app.ptm_service.state.online = True
                app.ptm_service.state.status = {'connected': True, 'ready': True, 'homed': True}
                await app._handle_plc_command()
                self.assertEqual(app.command.status, CommandStatus.RECEIVED)
                async def finish(*_):
                    app.command.status = CommandStatus.COMPLETE
                app._dispatch_plc_command = AsyncMock(side_effect=finish)
                app.plc_input.command_seq = replacement_seq
                app.plc_input.req_point_id = 7
                await app._handle_plc_command()
                await app._command_task
                app._dispatch_plc_command.assert_awaited_once_with(2, 100, 'home.start', {'automatic': True})
                self.assertTrue(app._command_protocol_error)
                await app._handle_plc_command()
                self.assertIsNone(app._pending_request)
                self.assertEqual(app.command.last_seq, 100)
                app.plc_input = PlcInput(command_code=0)
                await app._handle_plc_command()
                self.assertEqual(app._command_protocol_error, '')

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

    async def test_idle_allows_identified_manual_recipe_motion_after_initial_stop(self):
        for command in ('point.goto', 'motion.goto', 'home.start'):
            with self.subTest(command=command):
                app = self.app()
                app.plc_input = PlcInput(run_enable=False, laser_mode=1)
                app._plc_startup_sync_required = True
                app._safe_stop_applied = True
                app.ptm_service.state.online = True
                app.ptm_service.state.last_ok_ms = now_ms()
                app.ptm_service.state.status = {'moving': True, 'raw': {'motion': {
                    'state': 'tracking', 'id': 'manual-1', 'automatic': False, 'command': command}}}
                app._perform_safe_stop = AsyncMock(return_value=True)
                await app._apply_safe_stop_if_needed()
                app._perform_safe_stop.assert_not_awaited()
                self.assertIsNone(app._safe_stop_task)

    async def test_manual_recipe_motion_requires_explicit_fresh_identity_and_initial_stop(self):
        for field, value in (('automatic', True), ('automatic', None), ('id', None),
                             ('command', None), ('initial_stop', False), ('force_stop', True),
                             ('fault', True), ('restart', True), ('stale', True)):
            with self.subTest(field=field, value=value):
                app = self.app()
                app.plc_input = PlcInput(run_enable=False, laser_mode=1)
                app._safe_stop_applied = True
                app.ptm_service.state.online = True
                app.ptm_service.state.last_ok_ms = now_ms()
                motion = {'state': 'tracking', 'id': 'manual-1', 'automatic': False, 'command': 'point.goto'}
                app.ptm_service.state.status = {'moving': True, 'raw': {'motion': motion}}
                if field == 'initial_stop':
                    app._safe_stop_applied = False
                elif field == 'force_stop':
                    app.plc_input.force_stop = True
                elif field == 'fault':
                    app.alarms.set_fault_word(DeviceSummaryBit.CONTROLLER, 1)
                elif field == 'restart':
                    app._controller_restart_request = {'requested_ms': now_ms()}
                elif field == 'stale':
                    app.ptm_service.state.last_ok_ms -= 1001
                else:
                    motion[field] = value
                app._perform_safe_stop = AsyncMock(return_value=True)
                await app._apply_safe_stop_if_needed()
                self.assertIsNotNone(app._safe_stop_task)
                await app._safe_stop_task
                app._perform_safe_stop.assert_awaited_once()

    async def test_previous_pulse_cache_does_not_stop_next_manual_test_point(self):
        for mode in ('ptm', 'gpio'):
            with self.subTest(mode=mode):
                app = self.app()
                app.settings.laser.mode = mode
                app.plc_input = PlcInput(run_enable=False, laser_mode=1)
                app._plc_startup_sync_required = app._safe_stop_applied = True
                app._laser_mode_previous = 1
                app._laser_priority_previous = False
                app._power_sleeping_previous = app.power_save.sleeping
                app.ptm_service.state.online = True
                app.ptm_service.state.last_ok_ms = now_ms()
                app.ptm_service.state.status = {'moving': True, 'laser_on': False, 'raw': {
                    'motion': {'state': 'tracking', 'id': 'second-point', 'automatic': False,
                               'command': 'point.goto'},
                    'laser': {'on': False, 'manual_override': False}}}
                # Laser polling still holds the preceding point's pulse snapshot.
                app.laser_service.state.online = True
                app.laser_service.state.status = {'on': True, 'manual_override': True}
                app._perform_safe_stop = AsyncMock(return_value=True)
                app._update_power_save()
                self.assertIsNone(app._power_laser_task)
                await app._apply_safe_stop_if_needed()
                self.assertIsNone(app._safe_stop_task)
                app._perform_safe_stop.assert_not_awaited()

    async def test_laser_observation_retains_real_on_and_uncertain_status_stops(self):
        for reason in ('real_on', 'tcp', 'stale', 'offline', 'unknown'):
            with self.subTest(reason=reason):
                app = self.app()
                app.settings.laser.mode = 'tcp' if reason == 'tcp' else 'gpio'
                app.plc_input = PlcInput(run_enable=False, laser_mode=1)
                app._safe_stop_applied = True
                app.ptm_service.state.online = reason != 'offline'
                app.ptm_service.state.last_ok_ms = now_ms() - (1001 if reason == 'stale' else 0)
                app.ptm_service.state.status = {'moving': False, 'laser_on': False, 'raw': {
                    'motion': {'state': 'completed'},
                    'laser': {'on': True if reason == 'real_on' else None if reason == 'unknown' else False,
                              'manual_override': False}}}
                app.laser_service.state.status = {'on': True}
                app._perform_safe_stop = AsyncMock(return_value=True)
                await app._apply_safe_stop_if_needed()
                self.assertIsNotNone(app._safe_stop_task)
                await app._safe_stop_task
                app._perform_safe_stop.assert_awaited_once()

    async def test_operator_stop_cancels_queued_plc_move_before_dispatch(self):
        for command, params in (('motion.stop', {}),
                                ('device.command', {'device': 'ptm', 'command': 'motion.stop'})):
            with self.subTest(command=command):
                app = self.app()
                app.plc_input = PlcInput(run_enable=True, laser_mode=2, command_code=1,
                                         command_seq=100, req_recipe_id=2, req_point_id=7)
                app.ptm_service.state.online = True
                app.ptm_service.state.status = {'connected': True, 'ready': True, 'homed': True}
                app.ptm_service.command = AsyncMock(return_value={'accepted': True})
                await app._handle_plc_command()
                self.assertIsNotNone(app._pending_request)
                response = await app.handle_tcp_command(command, params)
                self.assertTrue(response.ok)
                await app._handle_plc_command()
                self.assertIsNone(app._pending_request)
                self.assertIsNone(app._command_task)
                self.assertEqual(app.command.status, CommandStatus.ERROR)
                self.assertEqual(app.command.result, CommandResult.FORCE_STOP_ACTIVE)
                if command == 'motion.stop':
                    app.ptm_service.command.assert_awaited_once_with('motion.stop')
                else:
                    app.ptm_service.command.assert_awaited_once_with('motion.stop', {})

    async def test_plc_move_rejects_exclusive_web_motion_without_changing_laser(self):
        for code in (1, 2):
            for before_accept in (True, False):
                with self.subTest(code=code, before_accept=before_accept):
                    app = self.app()
                    app.plc_input = PlcInput(run_enable=True, laser_mode=1, command_code=code,
                                             command_seq=100, req_recipe_id=2, req_point_id=7)
                    app.ptm_service.state.online = True
                    app.ptm_service.state.last_ok_ms = now_ms()
                    motion = {'id': 'web-test', 'exclusive': before_accept}
                    app.ptm_service.state.status = {'connected': True, 'ready': True,
                        'homed': True, 'raw': {'motion': motion}}
                    app.ptm_service.command = AsyncMock()
                    app.laser_service.command = AsyncMock()
                    await app._handle_plc_command()
                    if not before_accept:
                        self.assertEqual(app.command.status, CommandStatus.RECEIVED)
                        motion['exclusive'] = True
                        await app._handle_plc_command()
                        await app._command_task
                    self.assertEqual(app.command.status, CommandStatus.REJECTED)
                    self.assertEqual(app.command.result, CommandResult.COMMAND_BUSY)
                    self.assertEqual(app.alarms.fault_summary(), 0)
                    app.ptm_service.command.assert_not_awaited()
                    app.laser_service.command.assert_not_awaited()

    async def test_operator_stop_cancels_inflight_plc_motion_without_motor_fault(self):
        app = self.app()
        app.plc_input = PlcInput(run_enable=True, laser_mode=2, command_code=1,
                                 command_seq=100, req_recipe_id=2, req_point_id=7)
        app.ptm_service.state.online = True
        app.ptm_service.state.status = {'connected': True, 'ready': True, 'homed': True}
        dispatched = asyncio.Event()
        async def send(command, params=None):
            if command == 'point.goto':
                dispatched.set()
                await asyncio.Event().wait()
            return {'accepted': True}
        app.ptm_service.command = AsyncMock(side_effect=send)
        await app._handle_plc_command()
        await app._handle_plc_command()
        task = app._command_task
        await asyncio.wait_for(dispatched.wait(), 1)
        self.assertTrue((await app.handle_tcp_command('motion.stop', {})).ok)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(app._active_motion)
        self.assertEqual(app.command.result, CommandResult.FORCE_STOP_ACTIVE)
        self.assertEqual(app.alarms.fault_summary(), 0)

    async def test_web_motion_starting_after_cached_status_blocks_move_preparation_atomically(self):
        app = self.app()
        app.settings.laser.mode = 'gpio'
        app.plc_input = PlcInput(run_enable=True, laser_mode=1)
        app.command.last_seq, app.command.status = 100, CommandStatus.BUSY
        app.ptm_service.command = AsyncMock()
        app.laser_service.command = AsyncMock(side_effect=JsonTcpError('manual motion is active', code='BUSY'))
        await app._dispatch_plc_command(1, 100, 'point.goto', {'recipe_id': 2, 'point_id': 7})
        app.laser_service.command.assert_awaited_once_with(app.settings.laser.off_command, {'require_idle': True})
        app.ptm_service.command.assert_not_awaited()
        self.assertEqual(app.command.status, CommandStatus.REJECTED)
        self.assertEqual(app.command.result, CommandResult.COMMAND_BUSY)
        self.assertEqual(app.alarms.fault_summary(), 0)

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

    async def test_ptm_operator_stop_is_not_latched_as_hardware_fault(self):
        for code in (1, 2):
            with self.subTest(code=code):
                app = self.app()
                app.command.status = CommandStatus.BUSY
                app._active_motion = {'id': 'move', 'code': code, 'accepted_ms': 1000}
                app.ptm_service.state.online = True
                app.ptm_service.state.last_ok_ms = 1100
                app.ptm_service.state.status = {'raw': {'motion': {
                    'failed_id': 'move', 'last_error': 'MOTION_STOPPED'}}}
                app._progress_motion_command(1100)
                self.assertEqual(app.command.status, CommandStatus.ERROR)
                self.assertEqual(app.command.result, CommandResult.FORCE_STOP_ACTIVE)
                self.assertEqual(app.alarms.fault_summary(), 0)
                self.assertIsNone(app._active_motion)


class ProcessRestartTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_restart_does_not_stop_start_or_clear_manual_stop(self):
        manager = ProcessManager({'vision': {'process_name': 'vision_server', 'enabled': False}})
        manager._stop = AsyncMock()
        manager._start = AsyncMock()
        manager.status = AsyncMock(return_value={'process_name': 'vision_server', 'enabled': False})
        manager._manually_stopped.add('vision_server')
        result = await manager.restart('vision_server')
        self.assertFalse(result['enabled'])
        manager._stop.assert_not_awaited()
        manager._start.assert_not_awaited()
        self.assertIn('vision_server', manager._manually_stopped)
        self.assertIn('skipped disabled', manager._last_actions['vision_server']['message'])

    async def test_enabled_restart_still_stops_then_starts(self):
        manager = ProcessManager({'ptm': {'process_name': 'ptm_server', 'enabled': True}})
        calls = []
        async def stop(name):
            calls.append(('stop', name))
        async def start(name):
            calls.append(('start', name))
            return {'running': True}
        manager._stop, manager._start = stop, start
        result = await manager.restart('ptm_server')
        self.assertTrue(result['running'])
        self.assertEqual(calls, [('stop', 'ptm_server'), ('start', 'ptm_server')])


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
