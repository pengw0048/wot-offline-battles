from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT))

from gui.mods.offline_lan_0922 import tank_collision
from test_port_0922_battle_runtime import BattleRuntime, _runtime, _Vector


class RamCornerContactTests(unittest.TestCase):
    """Exercise actual finite ray/plate intersections, not an always-hit fake."""

    def setUp(self):
        self.battle = BattleRuntime(_runtime())
        self.battle._collision_shape = lambda descriptor: (1.5, 3.5, 0, 2)
        self.player = self.vehicle()
        self.bot = self.vehicle()
        self.proof = dict(
            local_vehicle=self.player, bot_vehicle=self.bot,
            local_matrix=types.SimpleNamespace(translation=(0, 0, 0), pitch=0, roll=0),
            bot_matrix=types.SimpleNamespace(translation=(2.9, 0, 6.8), pitch=0, roll=0),
            hit_point=(1.45, 1.0, 3.4), contact_normal=(0, -1),
            contact_y_span=(0, 2))
        self.calls = []

    @staticmethod
    def vehicle(low=0.5, high=1.5):
        return types.SimpleNamespace(typeDescriptor=types.SimpleNamespace(
            hull=types.SimpleNamespace(hitTester=types.SimpleNamespace(
                bbox=((-1.1, low, -3.4), (1.1, high, 3.4)))),
            chassis=types.SimpleNamespace(hullPosition=(0, 0, 0))))

    def native_collision(self, vehicle, matrix, start, end, math_module,
                         chassis_matrix=None):
        start, end = tuple(start), tuple(end)
        self.calls.append((vehicle, start, end))
        bounds = vehicle.typeDescriptor.hull.hitTester.bbox
        origin = tuple(matrix.translation)
        first, last, face = 0.0, 1.0, None
        for axis in range(3):
            offset = start[axis] - origin[axis]
            travel = end[axis] - start[axis]
            if abs(travel) < 1e-10:
                if not bounds[0][axis] < offset < bounds[1][axis]:
                    return []
                continue
            enter, leave = sorted(((bounds[0][axis]-offset)/travel,
                                   (bounds[1][axis]-offset)/travel))
            if enter > first:
                first, face = enter, axis
            last = min(last, leave)
            if first > last:
                return []
        if face is None:
            return []
        # Different plate values make a minimum/primary armour substitution
        # observable. The side actually met by the corner ray is 150 mm.
        armor = (150.0, 20.0, 180.0)[face]
        return [types.SimpleNamespace(dist=first, matInfo=types.SimpleNamespace(
            armor=armor, vehicleDamageFactor=1.0))]

    def native_patch(self):
        return mock.patch(
            'gui.mods.offline_lan_0922.battle_runtime.collide_vehicle_at_matrix',
            side_effect=self.native_collision)

    def test_solid_chassis_corner_proves_actual_near_side_plates(self):
        with self.native_patch():
            with mock.patch.object(self.battle, '_ram_corner_probe_direction',
                                   return_value=None):
                self.assertIsNone(self.battle._native_ram_contact_plate_pair(self.proof)[0])
            matched, unused, unused2 = self.battle._native_ram_contact_plate_pair(self.proof)
        self.assertIsNotNone(matched)
        self.assertEqual((150.0, 150.0), (matched[0]['armor'], matched[1]['armor']))
        self.assertEqual((1.0, 1.45, 3.4), matched[2:])
        # The radial queries end at each body's centre, never the far armour.
        for actual, expected in zip(self.calls[-2][2], (0.0, 1.0, 0.0)):
            self.assertAlmostEqual(expected, actual)
        for actual, expected in zip(self.calls[-1][2], (2.9, 1.0, 6.8)):
            self.assertAlmostEqual(expected, actual)

    def test_first_corner_receipt_freezes_incoming_speed_and_impact_normal(self):
        self.battle._ram_bot_revision_at = lambda *args: 37
        record = dict(network_id=29, presentation_time_us=123000)
        with self.native_patch():
            self.assertTrue(self.battle._queue_ram_contact_proof(
                record, self.player, self.bot, _Vector(1.45, 1, 3.4),
                (0, 0, 11.1082), (0, 0, 0), 150000,
                own_pose=(0, 0, 0, 0, 0, 0), bot_pose=(2.9, 0, 6.8, 0, 0, 0),
                player_ram_profile=dict(spall_coefficient=1, ramming_bonus=0),
                contact_normal=(0, -1), contact_y_span=(0, 2)))
        receipt = self.battle.local_ram_contact()
        self.assertEqual(11.1082, receipt['vz'])
        self.assertEqual((0, -1), (receipt['contact_normal_x'], receipt['contact_normal_z']))
        self.assertEqual((123000, 37), (receipt['presentation_time_us'], receipt['bot_state_revision']))
        self.assertEqual(1, len(self.battle._local_ram_receipts))
        # Retrying the completed proof cannot duplicate the HP receipt.
        self.assertFalse(self.battle._retry_native_ram_contact_proof(1))
        self.assertEqual(1, len(self.battle._local_ram_receipts))

    def test_complete_normal_search_wins_over_earlier_corner_candidate(self):
        self.proof['hit_point'] = (0, 0.25, 3.4)
        self.proof['bot_matrix'].translation = (0, 0, 6.8)
        with self.native_patch(), mock.patch.object(
                self.battle, '_ram_corner_probe_direction',
                side_effect=AssertionError('valid normal pair must win')):
            matched, unused, unused2 = self.battle._native_ram_contact_plate_pair(self.proof)
        self.assertIsNotNone(matched)
        self.assertEqual((180.0, 180.0), (matched[0]['armor'], matched[1]['armor']))

    def test_corner_rays_cannot_mix_disjoint_hull_heights(self):
        self.proof['local_vehicle'] = self.vehicle(0.1, 0.8)
        self.proof['bot_vehicle'] = self.vehicle(1.2, 1.9)
        with self.native_patch():
            matched, first, second = self.battle._native_ram_contact_plate_pair(self.proof)
        self.assertIsNone(matched)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)

    def test_corner_direction_rejects_far_side_and_invalid_geometry(self):
        direction = self.battle._ram_corner_probe_direction
        matrix = self.proof['local_matrix']
        for point, normal in (((1, 1, 1), (0, 1)), ((0, 1, 0), (0, -1)),
                              ((float('nan'), 1, 1), (0, -1))):
            self.assertIsNone(direction(matrix, point, normal))
        self.assertIsNone(direction(object(), (1, 1, 1), (0, -1)))

    def test_empty_native_mesh_does_not_invent_corner_armour(self):
        with mock.patch('gui.mods.offline_lan_0922.battle_runtime.collide_vehicle_at_matrix',
                        return_value=[]):
            self.assertEqual((None, None, None),
                             self.battle._native_ram_contact_plate_pair(self.proof))


class RamContactFollowupTests(unittest.TestCase):

    def test_narrow_shared_hull_band_is_sampled_between_old_chassis_rays(self):
        runtime = object.__new__(BattleRuntime)
        runtime._vector = lambda value: tuple(value)
        def vehicle(bottom, top):
            return types.SimpleNamespace(typeDescriptor=types.SimpleNamespace(
                hull=types.SimpleNamespace(hitTester=types.SimpleNamespace(
                    bbox=((-1.0, bottom, -2.0), (1.0, top, 2.0)))),
                chassis=types.SimpleNamespace(hullPosition=(0.0, 0.0, 0.0))))
        player, bot = vehicle(1.01, 1.8), vehicle(0.4, 1.16)
        matrix = types.SimpleNamespace(translation=(0, 0, 0), pitch=0, roll=0)
        proof = dict(hit_point=(0, 0.9, 0), contact_y_span=(0, 1.8),
                     contact_normal=(1, 0), local_vehicle=player,
                     bot_vehicle=bot, local_matrix=matrix, bot_matrix=matrix)
        def probe(tank, unused_matrix, point, unused_normal):
            bounds = tank.typeDescriptor.hull.hitTester.bbox
            return ({'armor': 180 if tank is player else 101.6,
                     'screened': False}
                    if bounds[0][1] < point[1] < bounds[1][1] else None)
        self.assertFalse(any(1.01 < y < 1.16 for y in
                             tank_collision.ram_contact_sample_heights(0.9, (0, 1.8))))
        matched, unused, unused2 = runtime._ram_plate_pair_from_probe(proof, probe)
        self.assertIsNotNone(matched)
        self.assertTrue(1.01 < matched[2] < 1.16)

    def test_failed_native_probe_does_not_consume_the_contact_episode(self):
        runtime = BattleRuntime(_runtime())
        runtime._local_ram_episode_contacts = frozenset((11, 12))
        runtime._ram_bot_revision_at = lambda *args: 37
        runtime._native_ram_contact_plate_pair = lambda proof: (None, None, None)
        runtime._native_ram_contact_proofs[1] = dict(
            bot_id=11, attempts=1, record={}, presentation_time_us=123000,
            local_matrix=object(), bot_matrix=object(), contact_normal=(1, 0))
        self.assertFalse(runtime._retry_native_ram_contact_proof(1))
        self.assertEqual(frozenset((12,)), runtime._local_ram_episode_contacts)
        self.assertEqual({}, runtime._native_ram_contact_proofs)
        self.assertEqual({}, runtime._local_ram_receipts)

    def test_deferred_probe_keeps_the_time_of_its_frozen_geometry(self):
        runtime = BattleRuntime(_runtime())
        times = []
        runtime._ram_bot_revision_at = lambda bot, stamp: times.append(stamp)
        runtime._native_ram_contact_plate_pair = lambda proof: (None, None, None)
        runtime._native_ram_contact_proofs[1] = dict(
            bot_id=11, attempts=0, record={'presentation_time_us': 999000},
            presentation_time_us=123000, local_matrix=object(),
            bot_matrix=object(), contact_normal=(1, 0))
        self.assertFalse(runtime._retry_native_ram_contact_proof(1))
        self.assertEqual([123000], times)

    def test_shared_contact_width_recovers_a_native_plate_beside_a_track_gap(self):
        runtime = object.__new__(BattleRuntime)
        runtime._vector = lambda value: tuple(value)
        first = {'x': 0.0, 'z': 0.0, 'yaw': 0.0,
                 'shape': (1.5, 3.5, -0.8, 2.0)}
        second = {'x': 0.0, 'z': 6.5, 'yaw': 0.0,
                  'shape': (1.5, 3.5, -0.8, 2.0)}
        samples = runtime._ram_contact_xz_samples(
            first, second, (0.0, -1.0))
        self.assertEqual(3, len(samples))
        self.assertTrue(all(abs(x) < 1.5 and 3.0 < z < 3.5
                            for x, z in samples))

        def armor(self, vehicle, matrix, point, normal,
                  chassis_matrix=None):
            if vehicle == 'bot' and abs(point[0]) < 0.3:
                return None
            return {'armor': 40.0 if vehicle == 'player' else 20.0,
                    'screened': False}

        runtime._native_ram_vehicle_armor = types.MethodType(armor, runtime)
        proof = {'hit_point': (0.0, 1.0, 3.25),
                 'contact_y_span': (0.0, 2.0),
                 'contact_xz_candidates': samples,
                 'contact_normal': (0.0, -1.0),
                 'local_vehicle': 'player', 'bot_vehicle': 'bot',
                 'local_matrix': object(), 'bot_matrix': object()}
        matched, unused_first, unused_second = (
            runtime._native_ram_contact_plate_pair(proof))
        self.assertEqual((40.0, 20.0),
                         (matched[0]['armor'], matched[1]['armor']))
        self.assertGreater(abs(matched[3]), 0.3)
        self.assertTrue(3.0 < matched[4] < 3.5)

    def test_contact_height_candidates_stay_inside_shared_span_and_near_observation_first(self):
        values = tank_collision.ram_contact_sample_heights(2.0, (0.0, 4.0))
        self.assertEqual(2.0, values[0])
        self.assertTrue(all(0.0 <= value <= 4.0 for value in values))
        self.assertGreaterEqual(len(values), 5)
        self.assertEqual(len(values), len(set(round(v, 6) for v in values)))

    def test_native_pair_can_recover_structural_plates_away_from_chassis_midpoint(self):
        runtime = object.__new__(BattleRuntime)
        runtime._vector = lambda value: tuple(value)
        calls = []

        def armor(self, vehicle, matrix, hit, normal, chassis_matrix=None):
            calls.append((vehicle, float(hit[1])))
            if float(hit[1]) < 2.5:
                return None
            return {
                'armor': 150.0 if vehicle == 'player' else 76.2,
                'screened': False,
            }

        runtime._native_ram_vehicle_armor = types.MethodType(armor, runtime)
        proof = {
            'contact_normal': (1.0, 0.0),
            'hit_point': (10.0, 2.0, 20.0),
            'contact_y_span': (0.0, 4.0),
            'local_vehicle': 'player',
            'bot_vehicle': 'bot',
            'local_matrix': object(),
            'bot_matrix': object(),
        }

        matched, seen_player, seen_bot = runtime._native_ram_contact_plate_pair(
            proof)

        self.assertIsNotNone(matched)
        self.assertAlmostEqual(150.0, matched[0]['armor'])
        self.assertAlmostEqual(76.2, matched[1]['armor'], places=3)
        self.assertGreaterEqual(matched[2], 2.5)
        self.assertIsNotNone(seen_player)
        self.assertIsNotNone(seen_bot)
        self.assertIn(('player', 2.0), calls)

    def test_native_pair_never_mixes_plates_from_different_heights(self):
        runtime = object.__new__(BattleRuntime)
        runtime._vector = lambda value: tuple(value)

        def armor(self, vehicle, matrix, hit, normal, chassis_matrix=None):
            y = float(hit[1])
            if vehicle == 'player' and y < 2.0:
                return {'armor': 150.0, 'screened': False}
            if vehicle == 'bot' and y > 2.0:
                return {'armor': 76.2, 'screened': False}
            return None

        runtime._native_ram_vehicle_armor = types.MethodType(armor, runtime)
        proof = {
            'contact_normal': (1.0, 0.0),
            'hit_point': (10.0, 2.0, 20.0),
            'contact_y_span': (0.0, 4.0),
            'local_vehicle': 'player',
            'bot_vehicle': 'bot',
            'local_matrix': object(),
            'bot_matrix': object(),
        }

        matched, seen_player, seen_bot = runtime._native_ram_contact_plate_pair(
            proof)

        self.assertIsNone(matched)
        self.assertIsNotNone(seen_player)
        self.assertIsNotNone(seen_bot)

    def test_second_constraint_reads_normal_contact_velocity(self):
        tanks = [
            {'id': 1, 'vx': 10.0, 'vz': 1.0},
            {'id': 2, 'vx': -2.0, 'vz': 3.0},
        ]
        results = {
            1: {'delta_velocity': (-4.0, 2.0)},
            2: {'delta_velocity': (7.0, -1.0)},
        }

        updated = tank_collision.post_contact_velocity_bodies(tanks, results)

        self.assertEqual((6.0, 3.0), (updated[0]['vx'], updated[0]['vz']))
        self.assertEqual((5.0, 2.0), (updated[1]['vx'], updated[1]['vz']))
        self.assertEqual((10.0, 1.0), (tanks[0]['vx'], tanks[0]['vz']))

    def test_existing_ram_curve_is_not_retuned_by_this_fix(self):
        # Protect the accepted T-44/AT 7/T71-style cases: this follow-up fixes
        # contact-point proof and duplicated physical impulses, not the global
        # damage coefficient.
        self.assertAlmostEqual(0.25, tank_collision.RAM_DAMAGE_COEFFICIENT)
        damage_other, damage_self = tank_collision.ram_damage(
            5.72386, 55883.0, 100175.0, 50.8, 180.0)
        self.assertEqual((64, 191), (damage_other, damage_self))


if __name__ == '__main__':
    unittest.main()
