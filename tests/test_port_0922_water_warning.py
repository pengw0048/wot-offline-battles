"""Retail-observed half-hull caution and hull-top drowning thresholds."""
import math
import types
import unittest
from unittest import mock

import test_port_0922_battle_runtime as fixture
from test_port_0922_turret_hydraulics import RigidMatrix


class WaterWarningTests(unittest.TestCase):
    def setUp(self):
        self.battle = fixture.BattleRuntime(fixture._runtime())
        self.entity = fixture._Vehicle(
            10, fixture._Descriptor(), fixture._Vector(), (0, 0, 0),
            {'health': 500})
        self.geometry = fixture.battle_runtime_module.water_geometry

    def _surface(self, height):
        self.battle._water_depth = mock.Mock(
            side_effect=lambda point: height - point[1])

    def _level(self, position=(0.0, 0.0, 0.0), yaw=0.0,
               pitch=0.0, roll=0.0):
        return self.battle._vehicle_drowning_level(
            self.entity, position, yaw, pitch, roll)

    def test_warning_and_danger_use_mounted_hull_half_height_and_top(self):
        # Hull-local y [-0.2, 1.4] mounted at 0.6: bottom 0.4, midpoint
        # 1.2, top 2.0. Neither the origin nor turret mount is a threshold.
        for surface, level in ((-0.1, 0), (0.4, 0), (1.19999, 0),
                               (1.2, 0), (1.20001, 1), (1.8, 1),
                               (2.0, 1), (2.00001, 2)):
            with self.subTest(surface=surface):
                self._surface(surface)
                self.assertEqual(level, self._level())

    def test_each_vehicle_uses_its_own_hull_height(self):
        self._surface(1.3)
        for top, expected in ((0.6, 2), (1.4, 1), (3.4, 0)):
            with self.subTest(hull_top=top):
                self.entity.typeDescriptor.hull.hitTester.bbox = (
                    (-1.7, -0.2, -3.5), (1.7, top, 3.5), None)
                self.assertEqual(expected, self._level())

    def test_native_splash_and_underwater_flags_do_not_override_hull_geometry(self):
        self.entity.appearance.waterSensor = object()
        for native in (False, True):
            self.entity.appearance.isInWater = native
            self.entity.appearance.isUnderwater = native
            for surface, expected in ((0.5, 0), (1.5, 1), (2.1, 2)):
                self._surface(surface)
                self.assertEqual(expected, self._level())

    def test_track_footprint_and_turret_mount_cannot_change_water_height(self):
        descriptor = self.entity.typeDescriptor
        descriptor.chassis.topRightCarryingPoint = (1.5, 99.0)
        descriptor.hull.turretPositions = ((0.0, 50.0, 0.0),)
        self._surface(2.1)
        self.assertEqual(2, self._level())
        self.assertAlmostEqual(0.4, self.battle._water_depth.call_args[0][0][1])

    def test_all_bbox_corners_contribute_after_pitch_roll_and_yaw(self):
        descriptor = self.entity.typeDescriptor
        descriptor.chassis.hullPosition = (1.0, 0.6, 2.0)
        descriptor.hull.hitTester.bbox = (
            (-1.0, -0.6, -2.0), (3.0, 1.4, 4.0), None)
        sample, height = self.geometry.hull_sample(
            descriptor, (10.0, 20.0, 30.0),
            math.pi / 2, math.pi / 2, math.pi / 2)
        for actual, expected in zip(sample, (12.0, 14.0, 31.0)):
            self.assertAlmostEqual(expected, actual)
        self.assertAlmostEqual(6.0, height)
        for surface, expected in ((16.9, 0), (17.1, 1), (20.1, 2)):
            self._surface(surface)
            self.assertEqual(expected, self._level(
                (10.0, 20.0, 30.0), math.pi / 2, math.pi / 2, math.pi / 2))

    def test_pitched_long_hull_does_not_drown_when_only_its_mount_is_underwater(self):
        self._surface(0.1)
        self.assertEqual(1, self._level(pitch=math.pi / 2))
        self._surface(3.50001)
        self.assertEqual(2, self._level(pitch=math.pi / 2))

    def test_inverted_hull_can_drown_below_the_chassis_origin(self):
        # Inverted hull occupies y [-2.0, -0.4], wholly below its origin.
        self._surface(-0.3)
        self.assertEqual(2, self._level(roll=math.pi))
        self._surface(-1.5)
        self.assertEqual(0, self._level(roll=math.pi))

    def test_missing_or_unloaded_geometry_has_no_guessed_height(self):
        self.battle._water_depth = mock.Mock()
        for bbox in (None, (), ((0, 0, 0), (0, 1, 1)),
                     ((0, 0, 0), (1, float('nan'), 1)),
                     ((0, 0, 0), (1, float('inf'), 1))):
            self.entity.typeDescriptor.hull.hitTester.bbox = bbox
            self.assertIsNone(self._level())
        self.entity.typeDescriptor.hull = types.SimpleNamespace()
        self.assertIsNone(self._level())
        self.battle._water_depth.assert_not_called()

    def test_nonfinite_pose_cannot_start_a_countdown(self):
        self.battle._water_depth = mock.Mock()
        self.assertIsNone(self._level(pitch=float('nan')))
        self.assertIsNone(self._level(position=(0, float('inf'), 0)))
        self.battle._water_depth.assert_not_called()

    def test_worker_uses_current_pose_instead_of_spawn_or_native_water_flags(self):
        runtime = self.battle._runtime
        runtime.bigworld.entities[10] = self.entity
        sender = mock.Mock(return_value=True)
        self.battle.client = types.SimpleNamespace(
            is_bot_authority=lambda: True, send_player_environment=sender)
        self.battle._worker_mode = True
        self.battle._records = {'player:1': {
            'engine_id': 10, 'network_id': 1, 'kind': 'player', 'local': False,
            'state': {'health': 500, 'alive': True, 'input_seq': 12,
                      'x': 10.0, 'y': 20.0, 'z': 30.0,
                      'yaw': 0.0, 'pitch': math.pi / 2, 'roll': 0.0}}}
        self.entity.appearance.waterSensor = object()
        self.entity.appearance.isUnderwater = True
        self._surface(20.1)
        self.assertTrue(self.battle._publish_player_environment(0.3, 1.0))
        point = self.battle._water_depth.call_args[0][0]
        for actual, expected in zip(point, (10.0, 16.5, 31.2)):
            self.assertAlmostEqual(expected, actual)
        sender.assert_called_once_with([
            {'player_id': 1, 'input_seq': 12, 'level': 1}], 1)

    def _hydraulic_body(self):
        self.battle._runtime.math.Matrix = RigidMatrix
        ground = RigidMatrix()
        ground.setRotateYPR((0.4, 0.1, 0.2))
        ground.translation = fixture._Vector(10.0, 20.0, 30.0)
        aim = RigidMatrix()
        aim.setRotateYPR((0.0, 0.15, 0.0))
        aim.translation = fixture._Vector(
            fixture.battle_runtime_module.hull_aiming.correction_translation(
                0.15, 2.5))
        body = RigidMatrix(self.battle._matrix_product(aim, ground))
        corners = []
        for x in (-1.7, 1.7):
            for y in (0.4, 2.0):
                for z in (-3.5, 3.5):
                    rotated = body.rotate((x, y, z))
                    corners.append(tuple(rotated[index] + body.translation[index]
                                         for index in range(3)))
        bottom, top = min(row[1] for row in corners), max(row[1] for row in corners)
        centre = tuple(sum(row[index] for row in corners) / 8.0 for index in range(3))
        return ground, aim, body, (centre[0], bottom, centre[2]), top

    def test_local_hydraulic_body_angle_and_pivot_set_the_water_planes(self):
        ground, aim, body, expected_bottom, top = self._hydraulic_body()
        self.battle._local_pose_matrix = self.battle._matrix_product(aim, ground)
        # These fields must not be added a second time after the body matrix.
        self.battle._local_siege_aim_pitch = 0.15
        self.battle._local_siege_aim_center_z = 2.5
        self.assertNotAlmostEqual(ground.translation.y, body.translation.y)
        for surface, expected in (((expected_bottom[1] + top) * 0.5 + 0.01, 1),
                                  (top + 0.01, 2)):
            self._surface(surface)
            self.assertEqual(expected, self.battle._vehicle_drowning_level(
                self.entity, (10, 20, 30), 0.4, 0.1, 0.2, {'local': True}))
            actual_bottom = self.battle._water_depth.call_args[0][0]
            for actual, wanted in zip(actual_bottom, expected_bottom):
                self.assertAlmostEqual(wanted, actual)

    def test_worker_and_bot_reuse_unblended_hydraulic_collision_pose(self):
        ground, aim, body, expected_bottom, top = self._hydraulic_body()
        copied_grounds = []
        def collision_matrices(entity_id, canonical_ground):
            self.assertEqual(10, entity_id)
            copied_grounds.append(canonical_ground)
            return self.battle._matrix_product(aim, canonical_ground), canonical_ground
        self.battle._remote_factory = types.SimpleNamespace(
            get=lambda unused_id: self.entity,
            projectile_collision_matrices=collision_matrices)
        self.battle._worker_mode = True
        state = {'id': 11, 'health': 500, 'alive': True, 'input_seq': 12,
                 'x': 10.0, 'y': 20.0, 'z': 30.0,
                 'yaw': 0.4, 'pitch': 0.1, 'roll': 0.2}
        self.battle._records = {
            'player:1': {'engine_id': 10, 'network_id': 1, 'kind': 'player',
                         'native_remote': True, 'state': state},
            'bot:11': {'engine_id': 10, 'network_id': 11, 'kind': 'bot',
                       'native_remote': True, 'state': state}}
        sender = mock.Mock(return_value=True)
        self.battle.client = types.SimpleNamespace(
            is_bot_authority=lambda: True, send_player_environment=sender)
        self._surface(top + 0.01)
        self.assertTrue(self.battle._publish_player_environment(0.3, 1.0))
        sender.assert_called_once_with([
            {'player_id': 1, 'input_seq': 12, 'level': 2}], 1)
        bot = fixture.battle_runtime_module.BotRuntime(
            1, water_depth_probe=self.battle._water_depth,
            water_hull_pose=self.battle._bot_water_hull_pose)
        bot._descriptors[11] = self.entity.typeDescriptor
        self.assertFalse(bot._advance_bot_drowning(state, 0.3))
        self.assertTrue(state['_drowning'])
        self.assertEqual(2, len(copied_grounds))
        for matrix in copied_grounds:
            self.assertEqual((10.0, 20.0, 30.0), tuple(matrix.translation))
            self.assertAlmostEqual(0.1, matrix.pitch)
        for call in self.battle._water_depth.call_args_list:
            for actual, wanted in zip(call[0][0], expected_bottom):
                self.assertAlmostEqual(wanted, actual)

    def test_missing_body_provider_retains_known_pose_without_double_hydraulics(self):
        state = {'id': 11, 'x': 10.0, 'y': 20.0, 'z': 30.0,
                 'yaw': 0.4, 'pitch': 0.25, 'roll': 0.2,
                 'terrain_pitch': 0.1, 'suspension_pitch': 0.15}
        self.assertEqual(((10.0, 20.0, 30.0), 0.4, 0.25, 0.2),
                         self.battle._bot_water_hull_pose(state))
        self.battle._worker_mode = True
        self.battle._projectile_vehicle_matrices = mock.Mock(
            side_effect=RuntimeError('model provider rebuilding'))
        self.assertEqual(((10, 20, 30), 0.4, 0.25, 0.2),
                         self.battle._drowning_hull_pose(
                             self.entity, (10, 20, 30), 0.4, 0.25, 0.2,
                             {'native_remote': True}))

    def test_worker_missing_hull_does_not_send_a_drowning_verdict(self):
        self.battle._runtime.bigworld.entities[10] = self.entity
        self.entity.typeDescriptor.hull.hitTester.bbox = None
        sender = mock.Mock(return_value=True)
        self.battle.client = types.SimpleNamespace(
            is_bot_authority=lambda: True, send_player_environment=sender)
        self.battle._worker_mode = True
        self.battle._records = {'player:1': {
            'engine_id': 10, 'network_id': 1, 'kind': 'player', 'local': False,
            'state': {'health': 500, 'alive': True, 'input_seq': 12}}}
        self.battle._water_depth = mock.Mock()
        self.assertTrue(self.battle._publish_player_environment(0.3, 1.0))
        sender.assert_called_once_with([], 1)
        self.battle._water_depth.assert_not_called()


if __name__ == '__main__':
    unittest.main()
