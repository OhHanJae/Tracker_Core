import tempfile
import unittest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from src.common.settings import AppSettings, save_settings
from src.common.status import CommandCode, CommandResult, CommandStatus, ControllerState, DeviceSummaryBit, now_ms
from src.communication.plc_memory_mapping import PlcInput
from src.core.application import CoreApplication


class OperationModeTests(unittest.IsolatedAsyncioTestCase):
    def app(self, mode='testing'):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'global.json'
        settings = AppSettings()
        settings.laser.mode = 'gpio'
        settings.paths.logs_dir = str(Path(folder.name) / 'logs')
        save_settings(settings, path)
        app = CoreApplication(path, managed_worker=True)
        app._update_vision_work = Mock()
        app.plc_input = PlcInput(run_enable=False, laser_mode=1)
        app._plc_startup_sync_required = True
        app.ptm_service.state.online = app.laser_service.state.online = True
        app.ptm_service.state.last_ok_ms = now_ms()
        app.ptm_service.state.status = {'connected': True, 'ready': True, 'homed': True,
            'moving': False, 'laser_on': False, 'raw': {
                'motion': {'state': 'completed'},
                'laser': {'on': False, 'manual_override': False, 'control_priority': False},
                'operation': {'mode': mode, 'test_active': mode == 'testing',
                              'manual_active': mode == 'manual', 'lease_remaining_ms': 5000}}}
        app.ptm_service.command = AsyncMock(return_value={'accepted': True})
        app.laser_service.command = AsyncMock(return_value={'accepted': True})
        self.addAsyncCleanup(app.stop)
        return app

    async def test_testing_covers_movement_pulse_and_gap_without_idle_stop_or_policy_writes(self):
        app = self.app()
        app._perform_safe_stop = AsyncMock(return_value=True)
        for phase in ('tracking', 'completed', 'off_gap'):
            with self.subTest(phase=phase):
                raw = app.ptm_service.state.status['raw']
                raw['motion'] = {'state': phase, 'id': 'test' if phase == 'tracking' else None}
                raw['laser'].update(on=phase == 'completed', manual_override=phase == 'completed')
                app.ptm_service.state.status['moving'] = phase == 'tracking'
                app.laser_service.state.status = {'on': True}
                app._update_power_save()
                await app._apply_safe_stop_if_needed()
                self.assertIsNone(app._power_laser_task)
                self.assertIsNone(app._safe_stop_task)
                self.assertFalse(app.power_save.sleeping)
        app._perform_safe_stop.assert_not_awaited()
        app.laser_service.command.assert_not_awaited()

    async def test_testing_rejects_plc_actions_before_side_effects_even_during_startup(self):
        for code in (1, 2, 4, 5, 6):
            with self.subTest(code=code):
                app = self.app()
                app.plc_input.command_code, app.plc_input.command_seq = code, 10
                app.plc_input.req_recipe_id = app.plc_input.req_point_id = 1
                app._fastening_laser_inhibit = app._nutrunner_data_hold = False
                await app._handle_plc_command()
                self.assertEqual(app.command.status, CommandStatus.REJECTED)
                self.assertEqual(app.command.result, CommandResult.COMMAND_BUSY)
                self.assertIsNone(app._pending_request)
                self.assertFalse(app._fastening_laser_inhibit or app._nutrunner_data_hold)
                app.ptm_service.command.assert_not_awaited()
                app.laser_service.command.assert_not_awaited()

    async def test_test_acquisition_cancels_queued_plc_action_and_defeats_late_dispatch(self):
        app = self.app()
        app.command.last_code, app.command.last_seq, app.command.status = 1, 10, CommandStatus.RECEIVED
        app._pending_request = (1, 10, 'point.goto', {'recipe_id': 1, 'point_id': 1})
        await app._handle_plc_command()
        self.assertIsNone(app._pending_request)
        self.assertEqual(app.command.result, CommandResult.COMMAND_BUSY)
        app.command.status = CommandStatus.BUSY
        await app._dispatch_plc_command(1, 10, 'point.goto', {'recipe_id': 1, 'point_id': 1})
        self.assertEqual(app.command.status, CommandStatus.REJECTED)
        app.ptm_service.command.assert_not_awaited()
        app.laser_service.command.assert_not_awaited()

    async def test_test_keeps_effective_auto_for_valid_plc_modes_but_not_forced_off(self):
        app = self.app()
        for mode in range(1, 6):
            app.plc_input.laser_mode = mode
            self.assertEqual(app._effective_laser_mode(), 1)
            self.assertEqual(app._laser_policy(automatic_off=True)['plc_mode'], 1)
            self.assertTrue(app._laser_manual_allowed())
            self.assertFalse(app._laser_signaling_permitted())
        app.plc_input.laser_mode = 0
        self.assertEqual(app._effective_laser_mode(), 0)
        self.assertFalse(app._laser_manual_allowed())

    async def test_actual_interlocks_still_stop_testing(self):
        for reason in ('force_stop', 'fault', 'restart', 'laser_off'):
            with self.subTest(reason=reason):
                app = self.app()
                app._perform_safe_stop = AsyncMock(return_value=True)
                if reason == 'force_stop':
                    app.plc_input.force_stop = True
                elif reason == 'fault':
                    app.alarms.set_fault_word(DeviceSummaryBit.CONTROLLER, 1)
                elif reason == 'restart':
                    app._controller_restart_request = {'requested_ms': now_ms()}
                else:
                    app.plc_input.laser_mode = 0
                await app._apply_safe_stop_if_needed()
                await app._safe_stop_task
                app._perform_safe_stop.assert_awaited_once_with()

    async def test_testing_and_manual_states_preserve_existing_values_and_plc_word(self):
        self.assertEqual(int(ControllerState.TEACH_CALIBRATION), 8)
        for mode, state in (('testing', ControllerState.TESTING), ('manual', ControllerState.MANUAL)):
            with self.subTest(mode=mode):
                app = self.app(mode)
                app._build_output(now_ms())
                self.assertEqual(app.output.controller_state, state)
                self.assertEqual(app.output.to_words()[2], 9 if mode == 'testing' else 10)
                app.plc_input.force_stop = True
                app._build_output(now_ms())
                self.assertEqual(app.output.controller_state, ControllerState.STOPPED)
                app.alarms.set_fault_word(DeviceSummaryBit.CONTROLLER, 1)
                app._build_output(now_ms())
                self.assertEqual(app.output.controller_state, ControllerState.FAULT)

    async def test_stale_or_bypassed_session_never_exempts_normal_stop(self):
        for reason in ('stale', 'offline', 'bypass'):
            with self.subTest(reason=reason):
                app = self.app()
                if reason == 'stale':
                    app.ptm_service.state.last_ok_ms -= 5001
                elif reason == 'offline':
                    app.ptm_service.state.online = False
                else:
                    app.settings.motor.bypass = True
                self.assertFalse(app._ptm_test_active())
                app._build_output(now_ms())
                self.assertNotEqual(app.output.controller_state, ControllerState.TESTING)

    async def test_valid_remaining_test_lease_survives_a_slow_inflight_status_poll(self):
        app = self.app()
        app.ptm_service.state.last_ok_ms = now_ms() - 1200
        operation = app.ptm_service.state.status['raw']['operation']
        operation['lease_remaining_ms'] = 4800
        self.assertTrue(app._ptm_test_active())
        app._perform_safe_stop = AsyncMock(return_value=True)
        await app._apply_safe_stop_if_needed()
        self.assertIsNone(app._safe_stop_task)
        app._build_output(now_ms())
        self.assertEqual(app.output.controller_state, ControllerState.TESTING)
        operation['lease_remaining_ms'] = 1000
        self.assertFalse(app._ptm_test_active())
        operation['lease_remaining_ms'] = 4800
        app.ptm_service.state.last_error = 'status query failed'
        self.assertFalse(app._ptm_test_active())

    async def test_core_manual_actions_are_busy_but_reads_stop_and_alarm_reset_remain_available(self):
        app = self.app()
        for command, params in (('laser.on', {}), ('controller.restart_request', {}),
                ('process.restart', {'process_name': 'ptm_server'}), ('update_config', {}),
                ('device.command', {'device': 'ptm', 'command': 'motion.jog', 'params': {'pan': 'right'}})):
            response = await app.handle_tcp_command(command, params)
            self.assertFalse(response.ok)
            self.assertEqual(response.error['code'], 'BUSY')
        self.assertTrue((await app.handle_tcp_command('laser.status', {})).ok)
        self.assertTrue((await app.handle_tcp_command('motion.stop', {})).ok)
        self.assertTrue((await app.handle_tcp_command('alarm.reset', {})).ok)

    async def test_plc_error_reset_does_not_cancel_test_pulse(self):
        app = self.app()
        app.ptm_service.state.status['raw']['laser']['manual_override'] = True
        app.plc_input.command_code, app.plc_input.command_seq = CommandCode.ERROR_RESET, 10
        await app._handle_plc_command()
        await app._handle_plc_command()
        await app._command_task
        self.assertEqual(app.command.status, CommandStatus.COMPLETE)
        app.laser_service.command.assert_not_awaited()

    async def test_explicit_manual_absolute_and_relative_moves_survive_idle_stop(self):
        for command in ('motion.absolute', 'motion.relative'):
            with self.subTest(command=command):
                app = self.app('manual')
                app._safe_stop_applied = True
                app.ptm_service.state.status['moving'] = True
                app.ptm_service.state.status['raw']['motion'] = {
                    'state': 'tracking', 'command': command, 'automatic': False, 'id': 'manual-move'}
                app._perform_safe_stop = AsyncMock(return_value=True)
                await app._apply_safe_stop_if_needed()
                self.assertIsNone(app._safe_stop_task)
                app._build_output(now_ms())
                self.assertEqual(app.output.controller_state, ControllerState.MANUAL)

    async def test_core_reservation_closes_start_before_first_poll_for_all_plc_actions(self):
        app = self.app('idle')
        begin = await app.handle_tcp_command('operation.test.begin', {'test_token': 'owner', 'lease_ms': 5000})
        self.assertTrue(begin.ok)
        self.assertTrue(app._ptm_test_active())
        for code in (1, 2, 4, 5, 6):
            app.plc_input.command_code, app.plc_input.command_seq = code, code + 100
            await app._handle_plc_command()
            self.assertEqual(app.command.status, CommandStatus.REJECTED)
            self.assertEqual(app.command.result, CommandResult.COMMAND_BUSY)
            self.assertIsNone(app._controller_restart_request)
            self.assertIsNone(app._pending_request)
        app.ptm_service.command.assert_not_awaited()
        app.laser_service.command.assert_not_awaited()
        self.assertTrue((await app.handle_tcp_command('operation.test.renew', {'test_token': 'owner'})).ok)
        self.assertFalse((await app.handle_tcp_command('operation.test.end', {'test_token': 'other'})).ok)
        self.assertTrue(app._ptm_test_active())
        self.assertTrue((await app.handle_tcp_command('operation.test.end', {'test_token': 'owner'})).ok)
        self.assertFalse(app._ptm_test_active())

    async def test_core_begin_rejects_queued_command_and_inflight_hardware_writes(self):
        for pending in ('command', 'laser_off'):
            with self.subTest(pending=pending):
                app = self.app('idle')
                if pending == 'command':
                    app.command.status = CommandStatus.RECEIVED
                else:
                    task = asyncio.create_task(asyncio.Event().wait())
                    app._vision_laser_off_task = task
                    self.addCleanup(task.cancel)
                response = await app.handle_tcp_command('operation.test.begin', {'test_token': 'owner'})
                self.assertFalse(response.ok)
                self.assertEqual(response.error['code'], 'BUSY')
                self.assertIsNone(app._ptm_test_reservation)

    async def test_prestart_idle_response_cannot_clear_reservation_but_post_test_idle_does(self):
        app = self.app('idle')
        self.assertTrue((await app.handle_tcp_command('operation.test.begin', {'test_token': 'owner'})).ok)
        app.ptm_service.state.ok_count += 1
        self.assertTrue(app._ptm_test_active())
        operation = app.ptm_service.state.status['raw']['operation']
        operation.update(mode='testing', test_active=True)
        app.ptm_service.state.ok_count += 1
        self.assertTrue(app._ptm_test_active())
        operation.update(mode='idle', test_active=False)
        app.ptm_service.state.ok_count += 1
        self.assertFalse(app._ptm_test_active())
        self.assertIsNone(app._ptm_test_reservation)

    async def test_expired_or_confirmed_offline_core_reservation_releases_lock(self):
        for reason in ('expired', 'offline'):
            with self.subTest(reason=reason):
                app = self.app('idle')
                self.assertTrue((await app.handle_tcp_command('operation.test.begin', {'test_token': 'owner'})).ok)
                if reason == 'expired':
                    app._ptm_test_reservation['expires_ms'] = now_ms() - 1
                else:
                    app.ptm_service.state.online = False
                    app.ptm_service.state.error_count += 1
                self.assertFalse(app._ptm_test_active())
                self.assertFalse((await app.handle_tcp_command('operation.test.renew', {'test_token': 'owner'})).ok)

