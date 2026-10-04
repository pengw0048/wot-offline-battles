"""Mode-independent powered contact, without changing realised Siege motion."""
import copy
import io
import json
import sys
import types
import unittest
from unittest import mock

import test_port_0922_battle_runtime as runtime_fixture
import test_port_0922_destructibles as sensor_fixture
from gui.mods.offline_lan_0922 import battle_runtime, destructibles_sensor


BattleRuntime = battle_runtime.BattleRuntime


def descriptor_pair():
    travel = runtime_fixture._Descriptor('sweden:S22_Strv_S1')
    travel.isPitchHullAimingAvailable = True
    travel.physics['speedLimits'] = (13.889, 12.5001)
    siege = copy.deepcopy(travel)
    siege.physics['speedLimits'] = (2.22224, 2.22224)
    composite = copy.deepcopy(siege)
    composite.hasSiegeMode = True
    composite.defaultVehicleDescr = travel
    composite.siegeVehicleDescr = siege
    return composite, travel, siege


def active_physics():
    physics = runtime_fixture._effective_params_snapshot()['physics']
    physics.update(speedFwd=2.22224, speedBwd=2.22224,
                   rotSpd=0.4537856055185257)
    return physics


def battle_fixture():
    runtime = runtime_fixture._runtime()
    battle = BattleRuntime(runtime)
    battle._avatar = runtime.bigworld.avatar
    battle._local_physics = active_physics()
    battle._local_support_rise_blocked = False
    composite, travel, siege = descriptor_pair()
    battle._local_descriptor = composite
    # The native mode callback may expose the plain active child, so the
    # visible owner must retain its original mounted composite as evidence.
    entity = runtime_fixture._Vehicle(10, siege, runtime_fixture._Vector(),
                                     (0, 0, 0), {'health': 1000})
    return battle, entity, composite, travel, siege


class SiegeContactTests(unittest.TestCase):
    def tearDown(self):
        destructibles_sensor.set_catalog(None)

    def test_same_vehicle_keeps_forward_and_reverse_powered_contact_capacity(self):
        composite, travel, unused = descriptor_pair()
        physics = active_physics()
        before = copy.deepcopy(physics)
        for speed, expected in ((1.0, 13.889), (-1.0, -12.5001)):
            self.assertEqual(expected, BattleRuntime._destructible_drive_speed_cap(
                composite, physics, speed))
        self.assertEqual(before, physics)
        self.assertEqual((13.889, 12.5001), travel.physics['speedLimits'])

    def test_powered_health_gate_remains_strict_and_requires_real_contact(self):
        composite, unused, unused_siege = descriptor_pair()
        physics = active_physics()
        fixture = sensor_fixture.DestructiblesCompatibilityTests()
        for kind in ('fragile', 'falling', 'structure'):
            with self.subTest(kind=kind):
                spec = {'kind': kind, 'health': 5}
                if kind == 'structure':
                    spec['boxes'] = [[-.25, -.2, -.5, .25, 1.5, .5, 73]]
                # Reproduce the old mode-cap deadlock through the production
                # geometry and strict mass/health law, without a native fake
                # that always accepts a destroy. These are policy fixtures,
                # not an assertion of the reported Paris object's health.
                old, unused_authority, unused_descriptor = fixture._stationary_contact_status(
                    [dict(spec)], current_speed=1.0, kinetic_speed=physics['speedFwd'],
                    proposal_only=True)
                self.assertEqual('hard', old['status'])
                cap = BattleRuntime._destructible_drive_speed_cap(composite, physics, 1.0)
                fixed, authority, unused_descriptor = fixture._stationary_contact_status(
                    [dict(spec)], current_speed=1.0, kinetic_speed=cap,
                    proposal_only=True)
                self.assertEqual('crushed', fixed['status'])
                self.assertTrue(fixed['requires_commit'])
                self.assertEqual(((22, 37, 73 if kind == 'structure' else None),),
                                 fixed['token'])
                authority.event_sink.assert_not_called()
                wall, unused_authority, unused_descriptor = fixture._stationary_contact_status(
                    [dict(spec, health=10000)], current_speed=1.0,
                    kinetic_speed=cap, proposal_only=True)
                self.assertEqual('hard', wall['status'])
                far, unused_authority, unused_descriptor = fixture._stationary_contact_status(
                    [dict(spec, x=10.0)], current_speed=1.0,
                    kinetic_speed=cap, proposal_only=True)
                self.assertIsNone(far['token'])

    def test_ordinary_vehicle_and_disabled_limit_keep_existing_capacity(self):
        physics = active_physics()
        ordinary = runtime_fixture._Descriptor()
        self.assertEqual(2.22224, BattleRuntime._destructible_drive_speed_cap(
            ordinary, physics, 1.0))
        composite, unused, unused_siege = descriptor_pair()
        physics['speedFwd'] = 0.0
        self.assertEqual(0.0, BattleRuntime._destructible_drive_speed_cap(
            composite, physics, 1.0))

    def test_powered_column_commit_publishes_real_speed_and_exact_identity(self):
        fixture = sensor_fixture.DestructiblesCompatibilityTests()
        detail, authority, unused = fixture._stationary_contact_status(
            [{'kind': 'falling', 'health': 5}], current_speed=1.0,
            kinetic_speed=13.889, return_detail=True, kinetic_commit=True)
        self.assertEqual('crushed', detail['status'])
        self.assertTrue(detail['used_kinetic_speed'])
        self.assertEqual(((22, 37, None),), detail['token'])
        authority.destroy_column.assert_called_once()
        self.assertEqual(1.0, authority.destroy_column.call_args.args[4])
        event = authority.event_sink.call_args.args[0]
        self.assertEqual('column', event['destructible_kind'])
        self.assertEqual(1.0, event['speed'])

    def test_visible_translation_uses_retained_pair_without_accelerating_motion(self):
        battle, entity, unused, unused_travel, unused_siege = battle_fixture()
        proposal = mock.Mock(return_value={'status': 'clear', 'token': None})
        battle._destructibles = types.SimpleNamespace(
            _catalog_motion_proposal=proposal,
            _catalog_motion_blocked=mock.Mock(return_value=proposal.return_value))
        before = copy.deepcopy(battle._local_physics)
        with mock.patch.object(battle_runtime.world_collision,
                               'check_horizontal_collision', return_value='clear'):
            self.assertTrue(battle._motion_is_clear(entity, (0, 0, 0), 0, 1.0, .01,
                                                   allow_crush_drive=True))
        self.assertEqual(13.889, proposal.call_args.kwargs['kinetic_speed'])
        self.assertEqual(1.0, proposal.call_args.args[3])
        self.assertEqual(.01, proposal.call_args.kwargs['dt'])
        self.assertEqual(before, battle._local_physics)

    def test_visible_pivot_keeps_active_traverse_and_native_wall(self):
        battle, entity, unused, unused_travel, unused_siege = battle_fixture()
        battle._destructible_pose_sweep = mock.Mock(return_value={
            'status': 'clear', 'token': None})
        battle._native_world_rotation_is_clear = mock.Mock(return_value=False)
        self.assertFalse(battle._pose_sweep_is_clear(
            entity, (0, 0, 0), 0, (0, 0, 0), .004, 0.0, .01))
        call = battle._destructible_pose_sweep.call_args
        self.assertEqual(13.889, call.kwargs['drive_speed_cap'])
        self.assertEqual(active_physics()['rotSpd'], call.kwargs['rotation_speed_cap'])
        self.assertEqual(.004, call.args[3])
        battle._native_world_rotation_is_clear.assert_called_once()

    def test_bot_translation_and_pivot_use_the_same_retained_mode_pair(self):
        battle, unused_entity, unused, travel, siege = battle_fixture()
        battle._bots = types.SimpleNamespace(states={11: {'movement_dir': -1}},
                                             _descriptor_pairs={11: (travel, siege)})
        resolver = mock.Mock(return_value={'status': 'clear', 'token': None})
        battle._destructibles = types.SimpleNamespace(_catalog_motion_blocked=resolver)
        with mock.patch.object(battle_runtime.world_collision,
                               'check_horizontal_collision', return_value='clear'):
            self.assertEqual('clear', battle._resolve_bot_motion(
                11, (0, 0, 0), 0, -1.0, siege, .01, 1.0))
        self.assertEqual(-12.5001, resolver.call_args.kwargs['kinetic_speed'])
        self.assertEqual(-1.0, resolver.call_args.args[3])
        battle._destructible_pose_sweep = mock.Mock(return_value={
            'status': 'clear', 'token': None})
        battle._native_world_rotation_is_clear = mock.Mock(return_value=True)
        self.assertTrue(battle._resolve_bot_rotation(
            11, (0, 0, 0), 0, .004, siege, .01, 1.0, active_physics()['rotSpd']))
        self.assertEqual(13.889, battle._destructible_pose_sweep.call_args.kwargs['drive_speed_cap'])

    def test_worker_replays_translation_and_pivot_with_the_same_capacity(self):
        for pivot in (False, True):
            with self.subTest(pivot=pivot):
                battle, unused_entity, composite, unused_travel, unused_siege = battle_fixture()
                battle._worker_mode = True
                battle.client = runtime_fixture._Client()
                battle.client.send_player_destructible_contact_result = mock.Mock(return_value=True)
                token = ((22, 37, None),)
                proposal = mock.Mock(return_value={'status': 'crushed', 'token': token,
                                                   'requires_commit': True})
                commit = mock.Mock(return_value={'status': 'crushed', 'token': token})
                battle._destructibles = types.SimpleNamespace(
                    _catalog_motion_proposal=proposal, _catalog_motion_blocked=commit)
                battle._destructible_pose_sweep = mock.Mock(side_effect=(
                    proposal.return_value, commit.return_value))
                battle._resolve_player_descriptor = mock.Mock(return_value=composite)
                snapshot = runtime_fixture._effective_params_snapshot()
                snapshot['physics'] = active_physics()
                contact = {'seq': 3, 'x': 0.0, 'y': 0.0, 'z': 0.0,
                           'yaw': 0.0, 'speed': 0.0 if pivot else 1.0, 'dt': .01,
                           'end_x': 0.0, 'end_y': 0.0, 'end_z': 0.0 if pivot else .01,
                           'end_yaw': .004 if pivot else 0.0,
                           'forward': 0.0 if pivot else 1.0, 'token': [list(token[0])]}
                player = {'id': 2, 'vehicle': 'sweden:S22_Strv_S1',
                          'vehicle_compact_descr': 'dGVzdA==', 'effective_params': snapshot,
                          'destructible_contacts': [contact]}
                name = 'gui.mods.offline_lan_0922.destructibles_authority'
                authority = types.SimpleNamespace(is_destroyed=lambda *args: False)
                with mock.patch.dict(sys.modules, {name: authority}), mock.patch.object(
                        sys.modules['gui.mods.offline_lan_0922'], 'destructibles_authority',
                        authority, create=True):
                    self.assertEqual(1, battle._resolve_player_destructible_contacts([player], 1.0))
                calls = (battle._destructible_pose_sweep.call_args_list if pivot else
                         [proposal.call_args, commit.call_args])
                self.assertEqual(2, len(calls))
                for call in calls:
                    self.assertEqual(13.889, call.kwargs['drive_speed_cap' if pivot else 'kinetic_speed'])
                battle.client.send_player_destructible_contact_result.assert_called_once_with(
                    2, 3, True, contact['token'])

    def test_continuous_motion_report_captures_hitches_below_stall_threshold(self):
        for mode in (0, 2):
            with self.subTest(mode=mode):
                battle, entity, unused, unused_travel, unused_siege = battle_fixture()
                entity.siegeState = mode
                battle._sender = types.SimpleNamespace(handbrake=False)
                battle._local_drive_throttle = 1.0
                battle._local_drive_turn = 0.0
                battle._local_speed = 1.97
                battle._local_position = (0.0, 0.0, .02)
                battle._clock = lambda: current[0]
                current = [0.0]
                output = io.StringIO()
                with mock.patch.object(battle_runtime.sys, 'stdout', output):
                    for frame in range(202):
                        current[0] = frame * .01
                        # Every frame advances; no >0.1 m/s slowdown and no
                        # near-zero travel can trigger the old stall logger.
                        battle._report_local_hydraulic_motion(
                            entity, (0, 0, 0), (0, 0), .01,
                            2.0, 2.0, 1.97, 'advance', None)
                lines = output.getvalue().splitlines()
                self.assertEqual(1, len(lines))
                payload = json.loads(lines[0].split('HYDRAULIC MOTION ', 1)[1])
                self.assertEqual(mode, payload['siege_state'])
                self.assertEqual(201, payload['frames'])
                self.assertEqual({'advance': 201}, payload['paths'])
                self.assertAlmostEqual(.03, payload['worst']['horizontal_speed_loss']['value'])
                self.assertEqual(0, payload['worst']['settling_speed_loss']['value'])
                self.assertEqual(7, len(payload['worst']))
                self.assertEqual(1.97, battle._local_speed)
                self.assertEqual((0.0, 0.0, .02), battle._local_position)

    def test_motion_report_failure_and_mode_change_cannot_change_motion(self):
        battle, entity, unused, unused_travel, unused_siege = battle_fixture()
        battle._sender = types.SimpleNamespace(handbrake=False)
        battle._local_drive_throttle = 1.0
        battle._local_speed = 2.0
        battle._local_position = (0, 0, .02)
        current = [0.0]
        battle._clock = lambda: current[0]
        entity.siegeState = 0
        args = (entity, (0, 0, 0), (0, 0), .01, 2.0, 2.0, 2.0, 'advance', None)
        battle._report_local_hydraulic_motion(*args)
        current[0] = 1.0
        entity.siegeState = 2
        battle._report_local_hydraulic_motion(*args)
        self.assertEqual(1, battle._local_hydraulic_motion_window['frames'])
        current[0] = 3.0
        with mock.patch.object(battle_runtime.json, 'dumps', side_effect=ValueError('report failure')), \
                mock.patch.object(battle_runtime.sys, 'stdout', io.StringIO()):
            battle._report_local_hydraulic_motion(*args)
        self.assertIsNone(battle._local_hydraulic_motion_window)
        self.assertEqual(2.0, battle._local_speed)
        self.assertEqual((0, 0, .02), battle._local_position)


if __name__ == '__main__':
    unittest.main()
