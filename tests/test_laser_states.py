import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from src.common.settings import AppSettings, save_settings
from src.common.status import CommandCode, CommandStatus, DeviceSummaryBit, now_ms
from src.communication.plc_memory_mapping import PlcInput
from src.core.application import CoreApplication
from src.vision.vision_client import VisionSnapshot, normalize_vision_snapshot


class LaserStateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / 'global.json'
        settings = AppSettings()
        settings.laser.mode = 'gpio'
        save_settings(settings, path)
        self.app = CoreApplication(path)
        self.app.plc_input = PlcInput(run_enable=True, laser_mode=1, tracking_enable=True)
        self.app._laser_mode_previous = 1
        self.app.ptm_service.state.online = True
        self.app.ptm_service.state.last_ok_ms = now_ms()
        self.raw = {'state_profiles': {state: {'mode': 'on', 'interval_ms': 500} for state in (
            'point_irradiation', 'position_check', 'wrong_point', 'work_complete')}}
        self.app.ptm_service.state.status = {'raw': {'laser': self.raw}, 'moving': False}
        self.app.laser_service.state.online = True
        self.app.vision_service.state.online = True
        self.app.vision_snapshot = VisionSnapshot(online=True, tracker_valid=True,
            tracking_active=True, tracker_present=True, result_ready=True, position_error_mm=0.1,
            camera_required_mask=1, camera_online_mask=1, camera_valid_mask=1, production_approved=True)
        self.app._vision_target_synced = True
        self.app._active_recipe_id, self.app._active_point_id, self.app._active_target_seq = 1, 2, 3
        self.app.laser_service.command = AsyncMock(return_value={'accepted': True})
        self.clock = now_ms()

    async def asyncTearDown(self):
        await self.app.stop()
        self.directory.cleanup()

    async def update(self, stable=False):
        self.clock += 1100
        self.app.vision_service.state.last_ok_ms = self.clock
        self.app._update_workflow_laser(stable, 100, self.clock)
        if self.app._laser_state_task:
            await self.app._laser_state_task

    def state_calls(self):
        return [call.args[1] for call in self.app.laser_service.command.await_args_list if call.args[0] == 'laser.state']

    async def test_actual_position_verdict_switches_and_repeated_poll_does_not_restart_pattern(self):
        await self.update()
        await self.update()
        self.assertEqual([call['state'] for call in self.state_calls()], ['point_irradiation'])
        await self.update(stable=True)
        self.assertEqual(self.state_calls()[-1]['state'], 'position_check')
        self.app.vision_snapshot.position_error_mm = 2
        await self.update()
        self.assertEqual(self.state_calls()[-1]['state'], 'point_irradiation')
        self.app.vision_snapshot.point_match = self.match()
        self.app.vision_snapshot.tracker_valid = False
        await self.update()
        self.assertEqual(self.state_calls()[-1]['state'], 'wrong_point')
        self.app.vision_snapshot.tracker_present = False
        await self.update()
        self.assertEqual(self.state_calls()[-1]['state'], 'point_irradiation')
        self.assertEqual(self.state_calls()[-1]['recipe_id'], 1)
        self.assertEqual(self.state_calls()[-1]['point_id'], 2)

    def match(self, **overrides):
        data = dict(recipe_id=1, point_id=2, command_seq=3, matched_recipe_id=1,
                    matched_point_id=4, wrong_point=True, age_seconds=0.05)
        data.update(overrides)
        return normalize_vision_snapshot({'point_match': data}, 1).point_match

    async def test_wrong_point_requires_current_fresh_context_and_recovers(self):
        for overrides in ({'point_id': 5}, {'command_seq': 4}, {'age_seconds': 10}):
            with self.subTest(overrides=overrides):
                self.app.vision_snapshot.point_match = self.match(**overrides)
                await self.update()
                self.assertIsNone(self.app._vision_wrong_point(self.clock))
                self.assertNotEqual(self.state_calls()[-1]['state'], 'wrong_point')
        self.app.vision_snapshot.point_match = self.match()
        await self.update()
        self.assertTrue(self.app._vision_wrong_point(self.clock))
        self.assertEqual(self.state_calls()[-1]['state'], 'wrong_point')
        self.app.vision_snapshot.point_match = self.match(matched_point_id=2, wrong_point=False)
        await self.update(stable=True)
        self.assertFalse(self.app._vision_wrong_point(self.clock))
        self.assertEqual(self.state_calls()[-1]['state'], 'position_check')
        self.assertIsNone(self.app._vision_wrong_point(self.clock + 2000))

    async def test_wrong_or_missing_match_blocks_production_position_ok(self):
        self.app.settings.runtime.position_stable_ms = 0
        for match, expected in ((self.match(), False), (self.match(command_seq=9), False),
                                (self.match(wrong_point='true'), False),
                                (self.match(matched_point_id=2, wrong_point=False), True), (None, False)):
            with self.subTest(match=match):
                self.app.vision_snapshot.point_match = match
                self.app.vision_service.state.last_ok_ms = self.clock
                self.app._build_output(self.clock)
                self.assertEqual(self.app.output.flags.position_ok, expected)
                await asyncio.sleep(0)

    async def test_completion_signal_keeps_manual_relighting_inhibited(self):
        self.app._fastening_laser_inhibit = True
        self.app._laser_workflow_complete = True
        await self.update()
        self.assertEqual(self.state_calls()[-1]['state'], 'work_complete')
        policy = self.app.laser_service.command.await_args_list[-2].args[1]
        self.assertFalse(policy['manual_allowed'])
        self.assertTrue(policy['state_allowed'])
        self.assertFalse(self.app._laser_permitted())
        self.raw.update(active_state='work_complete', on=True, manual_allowed=False, power_saving=False)
        self.app.ptm_service.state.status['laser_on'] = True
        previous = self.app.laser_service.command.await_count
        self.app._update_power_save()
        await asyncio.sleep(0)
        self.assertEqual(self.app.laser_service.command.await_count, previous)
        response = await self.app.handle_tcp_command('laser.on', {})
        self.assertFalse(response.ok)
        self.assertEqual(response.error['code'], 'LASER_INTERLOCK')

    async def test_stale_inflight_state_cannot_apply_after_stop_or_target_change(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def command(name, params):
            if name == 'laser.power_save':
                entered.set()
                await release.wait()
            return {'accepted': True}

        self.app.laser_service.command.side_effect = command
        self.app._update_workflow_laser(False, 100, self.clock)
        await entered.wait()
        self.app.plc_input.laser_mode = 0
        self.app._update_workflow_laser(False, 100, self.clock + 1)
        release.set()
        await self.app._laser_state_task
        self.assertEqual(self.state_calls(), [])

    async def test_off_is_not_automatically_undone_until_state_or_profile_changes(self):
        await self.update()
        self.raw['active_state'] = None
        await self.update()
        self.assertEqual(len(self.state_calls()), 1)
        self.raw['state_profiles']['point_irradiation']['interval_ms'] = 1000
        await self.update()
        self.assertEqual(len(self.state_calls()), 2)

    async def test_legacy_backend_and_moving_target_do_not_receive_new_commands(self):
        self.raw.pop('state_profiles')
        await self.update()
        self.assertEqual(self.state_calls(), [])
        self.raw['state_profiles'] = {}
        self.app.ptm_service.state.status['moving'] = True
        await self.update()
        self.assertEqual(self.state_calls(), [])

    async def test_ptm_stop_preserves_completion_blink_even_in_dark_phase(self):
        self.app._fastening_laser_inhibit = True
        self.app._laser_workflow_complete = True
        self.raw.update(active_state='work_complete', on=False, manual_allowed=False,
                        state_allowed=True, power_saving=False)
        self.app.plc_input.force_stop = True
        self.app.ptm_service.command = AsyncMock()
        self.app._update_power_save()
        await self.app._apply_safe_stop_if_needed()
        await self.app._safe_stop_task
        self.app.ptm_service.command.assert_awaited_once_with('motion.stop', {'preserve_laser': True})
        self.app.laser_service.command.assert_not_awaited()
        self.assertTrue(self.app._laser_workflow_complete)

    async def test_manual_laser_block_reason_matches_existing_permission_gate(self):
        self.assertTrue(self.app._laser_manual_allowed())
        self.assertEqual(self.app._laser_manual_block_reason(), '')
        self.app.plc_input.force_stop = True
        self.assertTrue(self.app._laser_manual_allowed())
        self.app.plc_input.laser_mode = 0
        self.assertIn('D+1', self.app._laser_manual_block_reason())
        self.app.plc_input.laser_mode = 1
        self.app.plc_input.force_stop = False
        self.app._fastening_laser_inhibit = True
        self.assertIn('체결', self.app._laser_manual_block_reason())
        self.app._fastening_laser_inhibit = False
        self.app.alarms.set_fault_word(DeviceSummaryBit.CONTROLLER, 1)
        self.assertFalse(self.app._laser_manual_allowed())
        self.assertIn('Core 알람 1001', self.app._laser_manual_block_reason())

    async def test_startup_sync_blocks_automatic_laser_but_allows_explicit_manual_on(self):
        self.app._plc_startup_sync_required = True
        for run_enable in (False, True):
            with self.subTest(run_enable=run_enable):
                self.app.plc_input.run_enable = run_enable
                policy = self.app._laser_policy(automatic_off=False)
                self.assertTrue(policy['local_allowed'])
                self.assertTrue(policy['manual_allowed'])
                self.assertEqual(policy['manual_block_reason'], '')
                self.assertTrue(policy['automatic_off'])
                self.assertFalse(policy['state_allowed'])
                self.assertFalse(self.app._laser_permitted())
                response = await self.app.handle_tcp_command('laser.on', {})
                self.assertTrue(response.ok)
                self.assertEqual(self.app.laser_service.command.await_args.args, ('laser.on', {}))
        self.app.plc_input.laser_mode = 0
        self.assertFalse(self.app._laser_manual_allowed())
        self.assertFalse((await self.app.handle_tcp_command('laser.on', {})).ok)
        self.app.plc_input.laser_mode = 1
        self.app.alarms.set_fault_word(DeviceSummaryBit.PLC_COMMUNICATION, 1)
        self.assertFalse(self.app._laser_manual_allowed())
        self.assertIn('Core 알람', self.app._laser_manual_block_reason())

    async def test_changed_block_reason_is_sent_while_permission_remains_false(self):
        self.app.alarms.set_fault_word(DeviceSummaryBit.CONTROLLER, 1)
        self.raw.update(manual_allowed=False, manual_block_reason='old reason',
                        state_allowed=False, power_saving=False)
        self.app._update_power_save()
        await self.app._power_laser_task
        policy = self.app.laser_service.command.await_args.args[1]
        self.assertFalse(policy['manual_allowed'])
        self.assertIn('Core 알람', policy['manual_block_reason'])

    async def test_forced_modes_override_idle_sleep_and_fastening_but_obey_faults(self):
        self.app.plc_input.run_enable = False
        self.app.plc_input.force_stop = True
        self.app.power_save.sleeping = True
        self.app._fastening_laser_inhibit = True
        for mode in (0, 1, 2, 3, 4, 5, 6):
            with self.subTest(mode=mode):
                self.app.plc_input.laser_mode = mode
                self.app._update_power_save()
                await self.app._power_laser_task
                policy = self.app.laser_service.command.await_args.args[1]
                self.assertEqual(policy['plc_mode'], mode)
                self.assertEqual(policy['state_allowed'], mode in (2, 3, 4, 5))
                if mode in (2, 3, 4, 5):
                    self.assertFalse(policy['enabled'])
                    self.assertFalse(policy['automatic_off'])
                    self.assertFalse(policy['manual_allowed'])
        self.app.plc_input.laser_mode = 2
        self.app.alarms.set_fault_word(DeviceSummaryBit.CONTROLLER, 1)
        self.assertFalse(self.app._laser_policy(automatic_off=False)['state_allowed'])

    async def test_plc_laser_on_includes_dark_blink_phase_and_excludes_off_profile(self):
        for profile, expected in (('blink', True), ('on', True), ('off', False)):
            with self.subTest(profile=profile):
                self.raw.update(active_state='wrong_point', active_profile={'mode': profile})
                self.app.laser_service.state.status = {'on': False}
                self.app._build_output(self.clock)
                self.assertEqual(bool(self.app.output.to_words()[0] & (1 << 6)), expected)
        self.raw['active_state'] = None
        self.app._build_output(self.clock)
        self.assertFalse(self.app.output.flags.laser_on)

    async def test_mode_change_back_to_auto_reapplies_workflow_profile(self):
        await self.update()
        self.app.plc_input.laser_mode = 3
        await self.update()
        self.app.plc_input.laser_mode = 1
        await self.update()
        self.assertEqual(len(self.state_calls()), 2)

    async def test_forced_mode_is_not_turned_off_by_move_or_fastening(self):
        self.app.plc_input.laser_mode = 3
        self.app.command.last_seq = 7
        self.app.command.status = CommandStatus.BUSY
        self.app.ptm_service.command = AsyncMock(return_value={'motion_id': 'move-7'})
        await self.app._dispatch_plc_command(CommandCode.TARGET_APPLY_MOVE, 7, 'point.goto',
                                             {'recipe_id': 1, 'point_id': 2})
        self.assertEqual(self.app.command.status, CommandStatus.BUSY)
        await self.app._request_nutrunner_laser_off()
        self.app.laser_service.command.assert_not_awaited()

    async def test_ptm_control_priority_cannot_override_plc_off(self):
        self.raw['control_priority'] = True
        self.app.power_save.sleeping = True
        self.app._fastening_laser_inhibit = True
        self.app._nutrunner_data_hold = True
        for mode in (0, 1, 2, 3, 4, 5, 6, 65535):
            for run_enable in (False, True):
                for force_stop in (False, True):
                    with self.subTest(mode=mode, run_enable=run_enable, force_stop=force_stop):
                        self.app.plc_input.laser_mode = mode
                        self.app.plc_input.run_enable = run_enable
                        self.app.plc_input.force_stop = force_stop
                        allowed = mode in {1, 2, 3, 4, 5}
                        self.assertEqual(self.app._laser_permitted(), allowed)
                        self.assertEqual(self.app._laser_manual_allowed(), allowed)
                        policy = self.app._laser_policy(automatic_off=True)
                        self.assertEqual(policy['manual_allowed'] and policy['state_allowed'], allowed)
                        self.assertEqual(policy['automatic_off'], not allowed)
                        self.assertEqual(policy['plc_mode'], mode)

    async def test_plc_off_and_run_disable_override_local_priority_on_stop(self):
        self.raw.update(control_priority=True, active_state='wrong_point')
        self.app.plc_input.laser_mode = 0
        self.app.plc_input.run_enable = False
        self.app.ptm_service.command = AsyncMock()
        await self.app._apply_safe_stop_if_needed()
        await self.app._safe_stop_task
        self.app.ptm_service.command.assert_awaited_once_with('motion.stop')
        self.app.laser_service.command.assert_awaited_once_with(self.app.settings.laser.off_command)
        self.app.alarms.set_fault_word(DeviceSummaryBit.CONTROLLER, 1)
        policy = self.app._laser_policy(automatic_off=False)
        self.assertFalse(policy['local_allowed'] or policy['manual_allowed'] or policy['state_allowed'])
        self.assertIn('Core 알람', policy['local_block_reason'])
