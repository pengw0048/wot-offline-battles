import math
import sys
import types
import unittest
from unittest import mock

from tests import test_port_0922_battle_runtime as fixtures
from tests import test_port_0922_he_blast_runtime as he_fixtures
from tests import test_port_0922_solid_collision_oracle as geometry
from gui.mods.offline_lan_0922 import battle_runtime as runtime_module


class DriverDisplayPoseConsumerTests(unittest.TestCase):

    @staticmethod
    def matrix(position, rotation=(0.0, 0.0, 0.0)):
        matrix = geometry.Matrix()
        matrix.setRotateYPR(rotation)
        matrix.translation = geometry.Vector(position)
        return matrix

    def battle(self):
        battle = runtime_module.BattleRuntime(fixtures._runtime())
        battle._runtime.math.Matrix = geometry.Matrix
        battle._player_driver = object()
        battle._local_position = (4.0, 2.0, 8.0)
        battle._local_yaw = 0.2
        battle._local_pitch = 0.1
        battle._local_roll = -0.05
        battle._local_matrix = self.matrix((9.0, 3.0, 12.0), (0.7, 0.3, 0.1))
        return battle

    def assertPointEqual(self, expected, actual):
        for index in range(3):
            self.assertAlmostEqual(expected[index], actual[index], places=8)

    def test_display_snapshot_does_not_change_physics_pose(self):
        battle = self.battle()
        snapshot = battle._local_display_pose()
        self.assertEqual(((9.0, 3.0, 12.0), 0.7), snapshot)
        battle._local_matrix.translation = geometry.Vector((20.0, 3.0, 12.0))
        self.assertEqual(((9.0, 3.0, 12.0), 0.7), snapshot)
        self.assertEqual(((4.0, 2.0, 8.0), 0.2), battle.local_pose())

    def test_aim_uses_display_but_sender_keeps_canonical_pose(self):
        battle = self.battle()
        battle.client = fixtures._Client()
        battle._send_driver_control = mock.Mock()
        battle._driver_pose_publication = mock.Mock(
            return_value=(battle._local_position, 123000))
        battle._driver_pose_published = mock.Mock()
        battle._echo_local_gun_angles = mock.Mock()
        sender = runtime_module._LANInputSender(battle)
        sender._track((12.0, 5.0, 16.0))
        self.assertAlmostEqual(math.atan2(3.0, 4.0), sender.aim_yaw)
        self.assertAlmostEqual(-math.atan2(2.0, 5.0), sender.gun_pitch)
        self.assertTrue(sender.send_avatar_input(10, 'stop_tracking', {
            'turret_yaw': 0.4, 'gun_pitch': -0.1}))
        self.assertAlmostEqual(1.1, sender.aim_yaw)
        self.assertAlmostEqual(0.2, sender.aim_pitch)
        message = next(row for row in battle.client.sent if row[0] == 'input')
        self.assertEqual((4.0, 2.0, 8.0), message[1][4])
        self.assertEqual(0.2, message[1][5])

    def test_shot_freezes_display_range_origin_and_native_ray_before_send(self):
        fixture = fixtures.BattleRuntimeContractTests()
        battle, unused_state, unused_settings, client, unused_record = \
            fixture._pending_fire_shell_change_battle()
        battle._runtime.math.Matrix = geometry.Matrix
        battle._local_position = (4.0, 2.0, 8.0)
        battle._local_yaw = 0.2
        battle._local_matrix = self.matrix((9.0, 3.0, 12.0), (0.7, 0.0, 0.0))
        battle._mutable_shot_ray = mock.Mock(return_value=(
            fixtures._Vector(10.0, 4.0, 13.0), fixtures._Vector(0.0, 0.0, 1.0)))
        turret_yaw = battle._avatar.gunRotator.turretYaw
        original_send = battle._sender.send_current

        def send_and_advance():
            result = original_send()
            battle._local_matrix.translation = geometry.Vector((30.0, 4.0, 30.0))
            return result

        battle._sender.send_current = send_and_advance
        self.assertTrue(battle.shoot(0.0, 0.0))
        launch = next(row for row in client.sent if row[0] == 'projectile_launch')
        self.assertEqual([10.0, 4.0, 13.0], launch[1][4])
        self.assertEqual([9.0, 3.0, 12.0], launch[2]['range_origin'])
        self.assertAlmostEqual(0.7 + turret_yaw, battle._sender.aim_yaw)
        message = next(row for row in client.sent if row[0] == 'input')
        self.assertEqual((4.0, 2.0, 8.0), message[1][4])
        self.assertEqual(0.2, message[1][5])
        battle._mutable_shot_ray.assert_called_once_with()

    def test_collision_rebases_hydraulics_without_mutating_display(self):
        battle = self.battle()
        correction = self.matrix((0.2, 0.6, -0.3), (0.0, 0.25, 0.0))
        body = geometry.Matrix(correction)
        body.postMultiply(battle._local_matrix)
        battle._local_pose_matrix = body
        displayed = body._values
        canonical = self.matrix(battle._local_position, (0.2, 0.1, -0.05))
        expected = geometry.Matrix(correction)
        expected.postMultiply(canonical)
        collision_body, chassis = battle._local_collision_matrices()
        for point in ((0.0, 0.0, 0.0), (2.0, -1.0, 3.0)):
            self.assertPointEqual(expected.applyPoint(point),
                                  collision_body.applyPoint(point))
            self.assertPointEqual(canonical.applyPoint(point),
                                  chassis.applyPoint(point))
        self.assertEqual(displayed, body._values)
        self.assertEqual(((9.0, 3.0, 12.0), 0.7), battle._local_display_pose())
        battle._player_driver = None
        self.assertEqual((body, battle._local_matrix),
                         battle._local_collision_matrices())

    def test_live_ap_he_interior_and_fire_lane_share_canonical_chassis(self):
        battle = self.battle()
        target = he_fixtures._target(position=(9.0, 3.0, 12.0))
        record = {'local': True, 'network_id': 7}
        start = battle._vector((3.0, 2.0, 8.0))
        end = battle._vector((5.0, 2.0, 8.0))
        evidence = types.SimpleNamespace(collision=object())
        with mock.patch.object(runtime_module,
                               '_collide_vehicle_evidence_at_matrix',
                               return_value=(evidence,)) as collide:
            battle._projectile_vehicle_collisions(record, target, start, end)
        self.assertPointEqual((4.0, 2.0, 8.0),
                              collide.call_args.args[1].translation)
        proxy = battle._projectile_live_critical_target(record, target)
        self.assertPointEqual((4.0, 2.0, 8.0), proxy.position)
        self.assertPointEqual((4.0, 2.0, 8.0), proxy.matrix.translation)
        with mock.patch.object(runtime_module,
                               'vehicle_blast_probe_points_at_matrix',
                               return_value=()) as probes:
            battle._projectile_he_blast_contact(
                {}, record, target, he_fixtures._shot(), (3.0, 2.0, 8.0), 100.0)
        self.assertPointEqual((4.0, 2.0, 8.0),
                              probes.call_args.args[1].translation)
        with mock.patch.object(runtime_module, 'collide_vehicle_at_matrix',
                               return_value=(object(),)) as lane:
            result = battle._bot_lane_row_contact(
                [(record, target, (4.0, 2.0, 8.0))],
                [(3.0, 2.0, 8.0), (5.0, 2.0, 8.0)])
        self.assertIs(record, result[0])
        self.assertPointEqual((4.0, 2.0, 8.0),
                              lane.call_args.args[1].translation)

    def test_sticker_encodes_hit_in_canonical_component_frame(self):
        battle = self.battle()
        battle._runtime.vehicles.g_cache.shotEffects[3]['targetStickers'] = {
            'armorPierced': 29}
        descriptor = fixtures._Descriptor()
        descriptor.hull.hitTester.localHitTest = mock.Mock(return_value=[object()])
        target = types.SimpleNamespace(typeDescriptor=descriptor)
        collision = types.SimpleNamespace(dist=1.0, compName='vehicleHull')
        start = battle._vector((4.0, 2.0, 7.0))
        end = battle._vector((4.0, 2.0, 9.0))
        decoder = types.SimpleNamespace(decodeSegment=lambda code, unused: (
            'hull', code, fixtures._Vector(0.0, 0.0, -1.0),
            fixtures._Vector(0.0, 0.0, 1.0)))
        with mock.patch.object(runtime_module, 'encode_damage_sticker',
                               return_value=29) as encode, \
                mock.patch.dict(sys.modules, {'VehicleEffects':
                                types.SimpleNamespace(DamageFromShotDecoder=decoder)}):
            result = battle._projectile_damage_sticker(
                {'local': True}, target, descriptor.gun.shots[0],
                start, end, (collision,), 2)
        self.assertEqual(29, result)
        self.assertPointEqual((4.0, 2.0, 8.0),
                              encode.call_args.args[1].translation)
        self.assertEqual(((9.0, 3.0, 12.0), 0.7), battle._local_display_pose())


if __name__ == '__main__':
    unittest.main()
