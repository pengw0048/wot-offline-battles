import math
import types
import unittest
from unittest import mock

from test_port_0922_world_collision import (
    _Vector, _Strict1513Component, _miss_mat_info_1513)
from gui.mods.offline_lan_0922 import world_collision, battle_runtime


def vehicle_physics_for_test():
    # Approximate the reported FV4005 contact state only to exercise the
    # shared track-budget law; exact descriptor values are not required for
    # the zero-vs-positive traverse distinction.
    return {
        'mass': 51410.0, 'powerW': 850000.0, 'nativePowerRatio': 1.0,
        'speedFwd': 9.7223, 'speedBwd': 3.33336, 'rotSpd': math.radians(30.0),
        'terrainResist': (1.0, 1.1, 2.0), 'specificFriction': 0.6867,
        'brakeDecel': 10.0,
    }


class MobilityRecovery096Tests(unittest.TestCase):
    def test_reported_destination_perimeter_bank_is_direction_invariant(self):
        # 2026-09-30 01:48:59 Bot 25: this exact support profile was sampled
        # across the translated hull perimeter, perpendicular to actual travel.
        # Its polygon winding made every segment an "uphill" sample even though
        # reversing the same edge proves one continuous descending bank.
        heights = (
            -13.072770118713379, -11.809005737304688,
            -10.329644203186035, -8.880050659179688,
            -7.427590847015381, -6.3954057693481445,
            -5.468088626861572)
        segment = math.hypot(
            76.59859466552734 - 81.5457992553711,
            83.35201263427734 - 86.79166412353516) / 6.0
        self.assertFalse(world_collision._drivable_ground_profile(
            heights, segment))
        self.assertTrue(world_collision._drivable_ground_profile(
            tuple(reversed(heights)), segment))
        self.assertTrue(world_collision._drivable_perimeter_ground_profile(
            heights, segment))
        self.assertTrue(world_collision._drivable_perimeter_ground_profile(
            tuple(reversed(heights)), segment))

    def test_cross_body_destination_edge_uses_ground_not_travel_grade(self):
        descriptor = _Strict1513Component(hull=_Strict1513Component(
            hitTester=_Strict1513Component(bbox=(
                (-1.600409984588623, -1.0, -3.0441699028015137),
                (1.600409984588623, 2.0, 2.9812769889831543), None))))
        motion_yaw = -0.6075454690885841
        hull_yaw = 0.9632508577063125
        heights = [
            -13.072770118713379, -11.809005737304688,
            -10.329644203186035, -8.880050659179688,
            -7.427590847015381, -6.3954057693481445,
            -5.468088626861572]
        hit_once = [False]
        motion = (math.sin(motion_yaw), math.cos(motion_yaw))

        def horizontal(space, start, end, collision_filter=None,
                       departing_contact=None):
            dx, dz = end.x - start.x, end.z - start.z
            length = math.hypot(dx, dz)
            if length <= 1.0e-9:
                return None
            # Select only the translated destination edge whose XZ direction
            # is perpendicular to actual travel.  Corner trajectories remain
            # untouched, as do every upper-ray recast after this first hit.
            dot = (dx * motion[0] + dz * motion[1]) / length
            if not hit_once[0] and abs(dot) <= 1.0e-5:
                hit_once[0] = True
                return (start + (end - start).scale(0.35),
                        _Vector(0.35031795501708984,
                                0.467840313911438,
                                0.811420202255249), 0)
            return None

        scene = types.SimpleNamespace(
            wg_collideSegment=lambda *unused: None,
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        math_module = types.SimpleNamespace(Vector3=_Vector)
        with mock.patch.object(world_collision, '_collide_horizontal',
                               side_effect=horizontal), \
                mock.patch.object(world_collision, '_ground_profile',
                                  return_value=(heights, 1.0)), \
                mock.patch.object(world_collision,
                                  '_hit_matches_ground_profile',
                                  return_value=True), \
                mock.patch.object(world_collision,
                                  '_hit_matches_exact_ground_top',
                                  return_value=True):
            result = world_collision.check_horizontal_collision(
                scene, math_module, 1,
                _Vector(81.19792175292969, -8.494466781616211,
                        84.1795425415039),
                hull_yaw, 2.158047702536823, descriptor, False,
                0.1199951171875, return_status=True,
                motion_yaw=motion_yaw,
                pitch=0.8439014823647435,
                roll=0.5194584588132508)
        self.assertTrue(hit_once[0])
        self.assertEqual('clear', result)

    def test_vehicle_contact_releases_clipped_drive_budget_for_traverse(self):
        # The FV4005 report records non-zero internal road speed with zero
        # realised travel while W/S+A/D is held against another tank.  The
        # vehicle-contact translation fraction is therefore the evidence for
        # how much of that drive may consume the contact traverse budget.
        command = 1.0
        self.assertEqual(1.0, battle_runtime._contact_traverse_drive_intent(
            command, 1.0))
        self.assertEqual(0.0, battle_runtime._contact_traverse_drive_intent(
            command, 0.0))
        self.assertAlmostEqual(0.35,
            battle_runtime._contact_traverse_drive_intent(command, 0.35))

        params = dict(vehicle_physics_for_test())
        blocked = battle_runtime._contact_traverse_drive_intent(1.0, 0.0)
        omega, torque = battle_runtime.vehicle_physics.contact_traverse(
            params, 1.65, 2.7, 1.0, 0.014, blocked, -0.415)
        self.assertNotEqual(0.0, omega)
        self.assertGreater(torque, 0.0)

    def test_track_pivot_releases_existing_plane_only_in_recovery_direction(self):
        bbox = ((-1.5, -0.5, -3.0), (1.5, 1.5, 3.0), None)
        normal_angle = -0.837758040957278
        normal = _Vector(math.cos(normal_angle), 0.0,
                         math.sin(normal_angle))
        collision = (_Vector(0.0, 0.5, 0.0), normal)
        away = battle_runtime._rotation_departing_contact(
            (0.0, 0.0, 0.0), bbox, 0.0, 0.1,
            pivot_offset=1.2)
        deeper = battle_runtime._rotation_departing_contact(
            (0.0, 0.0, 0.0), bbox, 0.0, -0.1,
            pivot_offset=1.2)
        self.assertTrue(away(collision))
        self.assertFalse(deeper(collision))


if __name__ == '__main__':
    unittest.main()
