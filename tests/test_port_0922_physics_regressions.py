"""Runtime regressions from 0.9.2 airborne and hydraulic reports."""
import math
import io
import json
import types
import unittest
from unittest import mock

import test_port_0922_battle_runtime as runtime_fixture
from test_port_0922_siege_braking import local_battle
from test_port_0922_turret_hydraulics import RigidMatrix
from gui.mods.offline_lan_0922 import vehicle_physics


class EvaluatedMatrix(RigidMatrix):
    def setIdentity(self):
        self.setRotateYPR((0, 0, 0))
        self.translation = runtime_fixture._Vector()

    def set(self, other):
        snapshot = EvaluatedMatrix(other)
        self.axes = snapshot.axes
        self.translation = snapshot.translation


class AirborneSteeringTests(unittest.TestCase):
    def flight(self, turn, omega=0.0):
        battle, entity = local_battle('sweden:S22_Strv_S1', 0, 8.0, omega)
        battle._local_airborne = True
        battle._local_suspension_disabled = True
        battle._sender.turn = turn
        battle._sender.forward = 1.0
        battle._local_position = (0.0, 50.0, 0.0)
        battle._update_local_presentation = lambda entity, dt: battle._vector(
            battle._local_position)
        battle._report_local_hydraulic_motion = mock.Mock()
        # There is no ground or another tank anywhere in this flight.
        battle._resolve_local_tank_contacts = lambda entity, position, yaw, dt: position
        for unused in range(30):
            battle._drive_local_step(1.0 / 30.0)
        return battle

    def test_a_and_d_cannot_turn_a_tank_in_free_flight(self):
        for turn in (-1.0, 0.0, 1.0):
            with self.subTest(turn=turn):
                battle = self.flight(turn)
                self.assertEqual(0.0, battle._local_yaw)
                self.assertEqual(0.0, battle._local_turn_speed)
                self.assertAlmostEqual(0.0, battle._local_position[0])
                self.assertAlmostEqual(8.0, battle._local_position[2])

    def test_takeoff_rotation_carries_without_bending_world_trajectory(self):
        for turn in (-1.0, 0.0, 1.0):
            with self.subTest(turn=turn):
                battle = self.flight(turn, omega=0.5)
                self.assertAlmostEqual(0.5, battle._local_yaw)
                self.assertAlmostEqual(0.5, battle._local_turn_speed)
                self.assertAlmostEqual(0.0, battle._local_position[0])
                self.assertAlmostEqual(8.0, battle._local_position[2])
                sine, cosine = math.sin(battle._local_yaw), math.cos(battle._local_yaw)
                self.assertAlmostEqual(0.0, sine * battle._local_speed + battle._local_air_lateral[0])
                self.assertAlmostEqual(8.0, cosine * battle._local_speed + battle._local_air_lateral[1])

    def test_rebase_conserves_forward_and_sideways_momentum(self):
        for old_yaw, new_yaw in ((0.0, 0.4), (1.4, -2.7), (-0.8, 2.0)):
            speed, lateral = vehicle_physics.rebase_airborne_velocity(
                -6.0, (3.0, -2.0), old_yaw, new_yaw)
            self.assertAlmostEqual(-6.0 * math.sin(old_yaw) + 3.0,
                                   speed * math.sin(new_yaw) + lateral[0])
            self.assertAlmostEqual(-6.0 * math.cos(old_yaw) - 2.0,
                                   speed * math.cos(new_yaw) + lateral[1])

    def test_oververtical_pitch_chart_change_keeps_world_momentum(self):
        for pitch in (1.7, -1.7):
            battle, entity = local_battle('sweden:S22_Strv_S1', 0, 8.0)
            battle._local_airborne = True
            battle._local_suspension_disabled = True
            battle._sender.turn = 0.0
            battle._local_position = (0.0, 50.0, 0.0)
            def tumble(entity, position, yaw, dt):
                battle._local_pitch = pitch
                return position
            battle._update_vertical_motion = tumble
            battle._update_local_presentation = lambda entity, dt: battle._vector(battle._local_position)
            battle._resolve_local_tank_contacts = lambda entity, position, yaw, dt: position
            battle._report_local_hydraulic_motion = mock.Mock()
            battle._drive_local_step(1.0 / 30.0)
            world_x = math.sin(battle._local_yaw) * battle._local_speed + battle._local_air_lateral[0]
            world_z = math.cos(battle._local_yaw) * battle._local_speed + battle._local_air_lateral[1]
            self.assertAlmostEqual(0.0, world_x)
            self.assertAlmostEqual(8.0, world_z)

    def test_lateral_airborne_wall_impact_discards_only_closing_velocity(self):
        for method in ('_apply_slope_slide', '_apply_suspension_slope_slide'):
            battle = runtime_fixture.BattleRuntime(runtime_fixture._runtime())
            battle._local_airborne = True
            battle._local_air_lateral = (3.0, 4.0)
            battle._local_vertical_speed = -1.0
            battle._local_motion_soft_block = False
            battle._local_world_collision_trace = {'normal': (-1.0, 0.0, 0.0)}
            battle._motion_is_clear = lambda *args, **kwargs: False
            result = getattr(battle, method)((0.0, 20.0, 0.0), 0.0, 0.1, object())
            self.assertEqual((0.0, 20.0, 0.0), result)
            self.assertEqual((0.0, 4.0), battle._local_air_lateral)
            self.assertEqual(-1.0, battle._local_vertical_speed)


class HydraulicPoseAuthorityTests(unittest.TestCase):
    def test_unsynchronised_native_providers_cannot_become_orbit_offsets(self):
        runtime = runtime_fixture._runtime()
        runtime.math.Matrix = EvaluatedMatrix
        battle = runtime_fixture.BattleRuntime(runtime)
        battle._local_matrix = EvaluatedMatrix()
        battle._local_matrix.setRotateYPR((0.7, -0.1, 0.2))
        battle._local_matrix.translation = runtime_fixture._Vector(230, 17, -190)
        td = runtime_fixture._Descriptor('sweden:S22_Strv_S1')
        td.hasSiegeMode = True
        entity = types.SimpleNamespace(typeDescriptor=td)
        # This filter has never received cell updates: startup providers may
        # remain on different world origins. Compute real matrix products so
        # that retaining their relative offset reproduces the old orbit.
        native_filter = types.SimpleNamespace(
            bodyMatrix=EvaluatedMatrix(), groundPlacingMatrix=EvaluatedMatrix(),
            groundPlacingMatrixFiltered=EvaluatedMatrix())
        native_filter.bodyMatrix.translation = runtime_fixture._Vector(-300, 100, 270)
        native_filter.groundPlacingMatrix.translation = runtime_fixture._Vector(10, -12, 20)
        battle._prepare_local_siege_pose(entity, native_filter, EvaluatedMatrix())
        self.assertTrue(battle._select_local_siege_pose(entity, True))
        for yaw in (0.0, 1.0, 2.0, -2.0):
            battle._local_matrix.setRotateYPR((yaw, -0.1, 0.2))
            body = EvaluatedMatrix(battle._local_body_pose())
            self.assertEqual((230.0, 17.0, -190.0), tuple(body.translation))
            self.assertAlmostEqual(yaw, body.yaw)
            ground = EvaluatedMatrix(battle._local_steady_rotation())
            self.assertEqual(tuple(body.translation), tuple(ground.translation))
        # Hydraulic pitch belongs to the body, not to contacted terrain.
        battle._local_siege_aim_matrix.setRotateYPR((0, 0.15, 0))
        ground = EvaluatedMatrix(battle._local_steady_rotation())
        self.assertAlmostEqual(-0.1, ground.pitch)
        self.assertAlmostEqual(0.2, ground.roll)


class LocalGunFrameAutorotationTests(unittest.TestCase):
    def test_rolled_hull_turns_for_actual_gun_limit_not_world_azimuth(self):
        runtime = runtime_fixture._runtime()
        battle = runtime_fixture.BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.inputHandler.getAutorotation = lambda: True
        descriptor = runtime_fixture._Descriptor('sweden:S22_Strv_S1')
        descriptor.gun.turretYawLimits = (-0.01, 0.01)
        entity = types.SimpleNamespace(typeDescriptor=descriptor)
        battle._local_matrix = runtime_fixture._Matrix()
        battle._local_matrix.setRotateYPR((0.0, 0.1, 0.4))
        battle._sender = types.SimpleNamespace(aim_yaw=0.0, aim_point=(0.0, 25.0, 100.0))
        # World azimuth is zero, but side-roll moves the elevated target out
        # of the fixed gun's local arc. The exact native solver owns this.
        runtime.get_shot_angles = mock.Mock(return_value=(0.08, -0.2))
        self.assertEqual(1.0, battle._local_autorotation_turn(entity, 0.0))
        args = runtime.get_shot_angles.call_args.args
        self.assertIs(descriptor, args[0])
        self.assertAlmostEqual(0.4, args[1].roll)
        runtime.get_shot_angles.return_value = (-0.08, -0.2)
        self.assertEqual(-1.0, battle._local_autorotation_turn(entity, 0.0))
        # X/sniper locking and a live drive command still retain precedence.
        battle._avatar.inputHandler.getAutorotation = lambda: False
        self.assertEqual(0.0, battle._local_autorotation_turn(entity, 0.0))
        battle._avatar.inputHandler.getAutorotation = lambda: True
        self.assertEqual(0.0, battle._local_autorotation_turn(entity, 0.0, 1.0))


class HydraulicPivotTests(unittest.TestCase):
    def test_rendered_body_and_turret_obstacle_preview_share_fixed_pivot(self):
        runtime = runtime_fixture._runtime()
        runtime.math.Matrix = EvaluatedMatrix
        battle = runtime_fixture.BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._local_position = (20.0, 3.0, -15.0)
        battle._local_yaw = 0.5
        battle._local_pitch = -0.1
        battle._local_roll = 0.2
        battle._local_matrix = EvaluatedMatrix()
        battle._local_matrix.setRotateYPR((0.5, -0.1, 0.2))
        battle._local_matrix.translation = battle._vector(battle._local_position)
        td = runtime_fixture._Descriptor('sweden:S22_Strv_S1')
        td.hasSiegeMode = True
        td.gun.pitchLimits = {'absolute': (0.0, 0.0)}
        td.type.hullAimingParams = {'pitch': {
            'isAvailable': True, 'isEnabled': True,
            'wheelCorrectionCenterZ': 1.7,
            'wheelsCorrectionSpeed': 0.3,
            'wheelsCorrectionAngles': {'pitchMin': -0.2, 'pitchMax': 0.2}}}
        entity = types.SimpleNamespace(typeDescriptor=td, siegeState=2)
        battle._sender = types.SimpleNamespace(aim_pitch=0.1, gun_pitch=0.0)
        battle._prepare_local_siege_pose(entity, None, None)
        battle._select_local_siege_pose(entity, True)
        battle._detached_turret_obstacles = types.SimpleNamespace(
            active=lambda: 1, sweep_blocks=mock.Mock(return_value=False))
        battle._turret_server_time_ms = lambda: 0
        self.assertTrue(battle._update_local_hull_aiming(entity, 1.0))
        body = EvaluatedMatrix(battle._local_body_pose())
        pivot = (0.0, 0.0, 1.7)
        before = battle._local_matrix.rotate(pivot)
        after = body.rotate(pivot)
        for index in range(3):
            self.assertAlmostEqual(before[index] + battle._local_position[index],
                                   after[index] + tuple(body.translation)[index])
        preview = battle._local_turret_pose(battle._local_position, 0.5, -0.1, 0.2)
        self.assertEqual(tuple(body.translation), tuple(preview['hull'][key]
                                                       for key in ('x', 'y', 'z')))
        self.assertAlmostEqual(body.pitch, preview['hull']['pitch'])
        self.assertAlmostEqual(body.roll, preview['hull']['roll'])


class HydraulicDiagnosticTests(unittest.TestCase):
    def test_takeoff_and_landing_keep_bounded_before_after_support_evidence(self):
        from test_port_0922_siege_contacts import battle_fixture
        battle, entity, unused, unused_travel, unused_siege = battle_fixture()
        entity.siegeState = 2
        battle._sender = types.SimpleNamespace(handbrake=False)
        battle._local_drive_throttle = 1.0
        battle._local_drive_turn = 0.0
        battle._local_speed = 2.0
        clock = [0.0]
        battle._clock = lambda: clock[0]
        output = io.StringIO()
        old_support = (1.0, 2.0, 0.0, {'center_y': 10.0})
        with mock.patch('sys.stdout', output):
            for frame in range(22):
                clock[0] = frame * 0.1
                battle._local_airborne = frame % 2 == 0
                battle._local_position = (1.0, 10.0 + frame * 0.01, 2.0)
                battle._local_legacy_support_sample = (1.0, 2.0, 0.0, {'center_y': 10.0})
                battle._report_local_hydraulic_motion(
                    entity, (1.0, 10.0, 1.8), (0.0, 0.0), 0.1,
                    2.0, 2.0, 2.0, 'advance', None,
                    origin={'airborne': not battle._local_airborne,
                            'vertical_speed': -0.1, 'support': old_support})
        lines = output.getvalue().splitlines()
        self.assertEqual(1, len(lines))
        payload = json.loads(lines[0].split('HYDRAULIC MOTION ', 1)[1])
        self.assertEqual({'takeoff': 11, 'landing': 10}, payload['transition_counts'])
        self.assertEqual(8, len(payload['transition_samples']))
        self.assertEqual(list(old_support[:3]), payload['transition_samples'][0]['support_before'][:3])
        self.assertFalse(payload['transition_samples'][0]['airborne_before'])
        self.assertTrue(payload['transition_samples'][0]['airborne'])
        self.assertAlmostEqual(0.2, payload['worst_transition']['value'])
        self.assertAlmostEqual(2.0, payload['worst_transition']['sample']['time'])


    def test_landing_then_stopping_does_not_discard_the_pending_witness(self):
        from test_port_0922_siege_contacts import battle_fixture
        battle, entity, unused, unused_travel, unused_siege = battle_fixture()
        entity.siegeState = 2
        battle._sender = types.SimpleNamespace(handbrake=False)
        battle._local_drive_throttle = 0.0
        battle._local_drive_turn = 0.0
        battle._local_speed = 0.0
        battle._local_airborne = False
        battle._local_position = (1.0, 10.0, 2.0)
        clock = [0.0]
        battle._clock = lambda: clock[0]
        output = io.StringIO()
        args = (entity, (1.0, 10.0, 2.0), (0.0, 0.0), 0.1,
                0.0, 0.0, 0.0, 'still', None)
        with mock.patch('sys.stdout', output):
            battle._report_local_hydraulic_motion(*args, origin={
                'airborne': True, 'vertical_speed': -0.1, 'support': None})
            clock[0] = 2.1
            battle._report_local_hydraulic_motion(*args, origin={
                'airborne': False, 'vertical_speed': 0.0, 'support': None})
        payload = json.loads(output.getvalue().split('HYDRAULIC MOTION ', 1)[1])
        self.assertEqual(1, payload['transition_counts']['landing'])
        self.assertEqual('landing', payload['transition_samples'][0]['edge'])


    def test_siege_edge_flushes_short_contact_window_with_original_mode(self):
        from test_port_0922_siege_contacts import battle_fixture
        battle, entity, unused, unused_travel, unused_siege = battle_fixture()
        entity.siegeState = 2
        battle._sender = types.SimpleNamespace(handbrake=False)
        battle._local_drive_throttle = 0.0
        battle._local_drive_turn = 0.0
        battle._local_speed = 0.0
        battle._local_airborne = False
        battle._local_position = (1.0, 10.0, 2.0)
        clock = [0.0]
        battle._clock = lambda: clock[0]
        output = io.StringIO()
        args = (entity, (1.0, 10.0, 2.0), (0.0, 0.0), 0.1,
                0.0, 0.0, 0.0, 'still', None)
        with mock.patch('sys.stdout', output):
            battle._report_local_hydraulic_motion(*args, origin={
                'airborne': True, 'vertical_speed': -0.1, 'support': None})
            entity.siegeState = 3
            clock[0] = 0.1
            battle._report_local_hydraulic_motion(*args, origin={
                'airborne': False, 'vertical_speed': 0.0, 'support': None})
        payload = json.loads(output.getvalue().split('HYDRAULIC MOTION ', 1)[1])
        self.assertEqual(2, payload['siege_state'])
        self.assertEqual(1, payload['transition_counts']['landing'])
