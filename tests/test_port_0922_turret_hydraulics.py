import math
import types
import unittest
from unittest import mock

from test_port_0922_battle_runtime import (
    BattleRuntime, _Descriptor, _MatrixInverse, _MatrixProduct, _Vector, _runtime)
from test_port_0922_turret_obstacles import BoxTester, descriptor, row
from gui.mods.offline_lan_0922 import shot_geometry
from gui.mods.offline_lan_0922.entities.detached_turret import DetachedTurretObstacles


class RigidMatrix:
    """Evaluate the same row-vector provider products used by the local body."""

    def __init__(self, source=None):
        self.setRotateYPR((0, 0, 0))
        self.translation = _Vector()
        if isinstance(source, _MatrixProduct):
            first, second = RigidMatrix(source.a), RigidMatrix(source.b)
            self.axes = [second.rotate(axis) for axis in first.axes]
            point = second.rotate(_xyz(first.translation))
            self.translation = _Vector(*(point[i] + _xyz(second.translation)[i]
                                         for i in range(3)))
        elif isinstance(source, _MatrixInverse):
            original = RigidMatrix(source.source)
            self.axes = [tuple(original.axes[j][i] for j in range(3))
                         for i in range(3)]
            self.translation = _Vector(*self.rotate(
                tuple(-value for value in _xyz(original.translation))))
        elif source is not None:
            self.axes = list(source.axes)
            self.translation = _Vector(source.translation)

    def setRotateYPR(self, angles):
        self.axes = [shot_geometry.transform_vehicle_vector(axis, *angles)
                     for axis in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]

    def rotate(self, vector):
        return tuple(sum(vector[j] * self.axes[j][i] for j in range(3))
                     for i in range(3))

    @property
    def yaw(self):
        return math.atan2(self.axes[2][0], self.axes[2][2])

    @property
    def pitch(self):
        return math.asin(max(-1, min(1, -self.axes[2][1])))

    @property
    def roll(self):
        return math.atan2(self.axes[0][1], self.axes[1][1])


def _xyz(vector):
    return vector.x, vector.y, vector.z


class LocalTurretHydraulicTests(unittest.TestCase):
    def battle(self):
        runtime = _runtime()
        runtime.math.Matrix = RigidMatrix
        battle = BattleRuntime(runtime)
        battle._local_position = (0, 3.2, 0)
        battle._local_yaw = 0.4
        battle._local_pitch = 0.0
        battle._local_roll = 0.2
        battle._local_siege_aim_pitch = 0.0
        battle._local_siege_aim_matrix = RigidMatrix()
        relative = RigidMatrix()
        relative.setRotateYPR((0, 0.1, 0))
        relative.translation = _Vector(0, 0.3, 0.15)
        battle._local_siege_body_matrix = types.SimpleNamespace(a=relative)
        battle._local_pose_matrix = types.SimpleNamespace(a=battle._local_siege_body_matrix)
        battle._sender = types.SimpleNamespace(aim_pitch=0.6, gun_pitch=0.0)
        battle._turret_server_time_ms = lambda: 4000
        battle._detached_turret_obstacles = DetachedTurretObstacles(runtime.math)
        distant = row(rest=(100, 1, 100))
        distant['actor_id'] = 18
        battle._detached_turret_obstacles.add('bot:18', distant, descriptor())
        td = _Descriptor('sweden:S11_Strv_103B')
        td.hasSiegeMode = True
        td.type.hullAimingParams = {'pitch': {
            'isAvailable': True, 'isEnabled': True, 'wheelCorrectionCenterZ': 0,
            'wheelsCorrectionSpeed': 0.2,
            'wheelsCorrectionAngles': {'pitchMin': -0.2, 'pitchMax': 0.2}}}
        td.gun.pitchLimits = {'absolute': (-0.07, 0.035)}
        td.chassis.hitTester = BoxTester((-0.2, -0.2, -0.2), (0.2, 0.2, 0.2))
        td.chassis.hullPosition = _Vector()
        td.hull.hitTester = BoxTester((-0.1, -0.1, 0), (0.1, 0.1, 2))
        return battle, types.SimpleNamespace(typeDescriptor=td, siegeState=2)

    def test_native_body_height_and_rotations_gate_hydraulic_aim_before_write(self):
        battle, entity = self.battle()
        before = battle._local_turret_pose(
            battle._local_position, battle._local_yaw,
            battle._local_pitch, battle._local_roll)
        after = battle._local_turret_pose(
            battle._local_position, battle._local_yaw,
            battle._local_pitch, battle._local_roll, aim_pitch=0.2)
        self.assertNotAlmostEqual(after['hull']['pitch'], 0.2)
        self.assertNotAlmostEqual(after['hull']['y'], after['y'])
        self.assertEqual((before['pitch'], before['roll']), (0.0, 0.2))
        body = after['hull']
        tip = shot_geometry.transform_vehicle_vector(
            (0, 0, 1.95), body['yaw'], body['pitch'], body['roll'])
        contact = tuple(tip[i] + body[name]
                        for i, name in enumerate(('x', 'y', 'z')))
        obstacle_td = descriptor()
        obstacle_td.turret.hitTester = BoxTester((-0.04, -0.04, -0.04), (0.04, 0.04, 0.04))
        obstacle_td.turret.gunPosition = _Vector(10, 0, 0)
        obstacles = battle._detached_turret_obstacles
        obstacles.add('bot:17', row(rest=contact), obstacle_td)
        self.assertFalse(obstacles.sweep_blocks(before, before, entity.typeDescriptor, 4000))
        self.assertTrue(obstacles.sweep_blocks(before, after, entity.typeDescriptor, 4000))
        self.assertTrue(battle._update_local_hull_aiming(entity, 1.0))
        self.assertEqual(battle._local_siege_aim_pitch, 0.0)
        self.assertAlmostEqual(battle._local_siege_aim_matrix.pitch, 0.0)
        obstacles.clear()
        self.assertTrue(battle._update_local_hull_aiming(entity, 1.0))
        self.assertAlmostEqual(battle._local_siege_aim_pitch, 0.2)
        self.assertAlmostEqual(battle._local_siege_aim_matrix.pitch, 0.2)

    def test_translation_and_rotation_query_the_current_hydraulic_body(self):
        battle, entity = self.battle()
        battle._local_siege_aim_pitch = 0.15
        blocker = mock.Mock(return_value=True)
        battle._detached_turret_obstacles = types.SimpleNamespace(
            sweep_blocks=blocker, active=lambda: 1)
        self.assertFalse(battle._motion_is_clear(
            entity, battle._local_position, 0.4, 3.0, 0.1))
        before, after, unused_descriptor, unused_clock = blocker.call_args.args
        self.assertIn('hull', before)
        self.assertIn('hull', after)
        self.assertEqual(before['pitch'], 0.0)
        self.assertNotAlmostEqual(before['hull']['pitch'], 0.0)
        self.assertAlmostEqual(after['hull']['x'] - before['hull']['x'],
                               math.sin(0.4) * 0.3)
        self.assertFalse(battle._pose_sweep_is_clear(
            entity, battle._local_position, 0.4,
            battle._local_position, 0.5, 0.0, 0.1))
        before, after, unused_descriptor, unused_clock = blocker.call_args.args
        self.assertIn('hull', before)
        self.assertNotEqual(before['hull']['yaw'], after['hull']['yaw'])

    def test_empty_obstacle_owner_does_not_allocate_native_pose_providers(self):
        battle, entity = self.battle()
        battle._detached_turret_obstacles.clear()
        battle._runtime.math.Matrix = mock.Mock(
            side_effect=AssertionError('empty obstacles allocated native matrix'))
        pose = battle._local_turret_pose(
            battle._local_position, battle._local_yaw,
            battle._local_pitch, battle._local_roll)
        self.assertNotIn('hull', pose)
        self.assertTrue(battle._update_local_hull_aiming(entity, 1.0))
        battle._runtime.math.Matrix.assert_not_called()
