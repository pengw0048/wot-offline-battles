import math
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/res/scripts/client'))
from gui.mods.offline_lan_0922 import spotting, radio
import test_port_0922_battle_runtime as fixtures


class LegacyDetectionPointsTests(unittest.TestCase):
    def client_descriptor(self):
        # Exact #1513 client descriptors do not run the IS_CELLAPP block.
        return {
            'chassis': {'hullPosition': (0.2, 0.5, 0.1)},
            'hull': {'turretPositions': ((0, 1.5, -2),),
                     'hitTester': {'bbox': ((-1.5, 0, -3), (1.5, 2, 4), 0)}},
            'turret': {'gunPosition': (0, 0.5, 1),
                       'hitTester': {'bbox': ((-1, 0, -1), (1, 1.5, 1), 0)}},
            'gun': {'staticTurretYaw': None},
        }

    def test_client_builds_all_six_points_without_cell_only_attributes(self):
        descriptor = self.client_descriptor()
        points = spotting.descriptor_check_points(descriptor)
        self.assertEqual(((0, 3.5, 0), (0.2, 2.5, -0.9),
                          (0.2, 1.5, 4.1), (0.2, 1.5, -2.9),
                          (1.7, 2.5, 0.6), (-1.3, 2.5, 0.6)), points)

    def test_rear_turret_does_not_move_the_chassis_observer_back(self):
        descriptor = self.client_descriptor()
        pose = {'position': (100, 10, 100), 'turret_yaw': math.pi}
        static = spotting.vehicle_check_points(descriptor, pose, True, 0)[0]
        dynamic = spotting.vehicle_check_points(descriptor, pose, True, 1)[0]
        self.assertEqual((100, 13.5, 100), static)
        self.assertAlmostEqual(100.2, dynamic[0])
        self.assertAlmostEqual(12.5, dynamic[1])
        self.assertAlmostEqual(97.1, dynamic[2])

    def test_real_client_descriptor_exposes_high_point_above_ridge(self):
        runtime, battle = self.battle()
        runtime.bigworld.wg_collideSegment = lambda space, start, end, mask: (
            None if end.y >= 3.0 else (fixtures._Vector(50, 2, 0),))
        descriptor = self.client_descriptor()
        result = battle._spot_geometry({'position': (0, 0, 0)},
            {'position': (100, 0, 0)}, descriptor, descriptor)
        self.assertTrue(result['line_of_sight'])

    def descriptor(self):
        descriptor = fixtures._Descriptor()
        descriptor.visibilityCheckPoints = (
            (0, 4, 0), (0, 2, 1), (0, 1, 3), (0, 1, -3),
            (2, 2, 0), (-2, 2, 0))
        descriptor.chassis.hullPosition = (0, 0, 0)
        descriptor.hull.turretPositions = ((0, 0, 0),)
        return descriptor

    def battle(self):
        runtime = fixtures._runtime()
        battle = fixtures.BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._clock = lambda: 0.0
        return runtime, battle

    def test_exposed_tall_turret_is_detected_above_a_ridge(self):
        runtime, battle = self.battle()
        rays = []
        def ridge(space, start, end, mask, *args):
            rays.append((start, end))
            return None if end.y >= 3.0 else (fixtures._Vector(50, 2, 0),)
        runtime.bigworld.wg_collideSegment = ridge
        descriptor = self.descriptor()
        descriptor.computeBaseInvisibility = lambda *args: (0.0, 0.0)
        self.assertFalse(battle._spot_segment_clear((0, 0, 0), (100, 0, 0)))
        self.assertTrue(battle._spot_line_of_sight(
            ((0, 0, 0), descriptor, None), (100, 0, 0), descriptor))
        self.assertEqual(2, len(rays))  # Open first native checkpoint short-circuits.

    def test_blocked_pairs_have_a_six_ray_bound(self):
        runtime, battle = self.battle()
        rays = []
        def wall(*args):
            rays.append(args)
            return (fixtures._Vector(10, 1, 0),)
        runtime.bigworld.wg_collideSegment = wall
        descriptor = self.descriptor()
        result = battle._spot_geometry({'position': (0, 0, 0)},
            {'position': (100, 0, 0)}, descriptor, descriptor)
        self.assertFalse(result['line_of_sight'])
        self.assertEqual(6, len(rays))

    def test_dynamic_port_tracks_turret_and_hull_yaw(self):
        descriptor = self.descriptor()
        static = spotting.vehicle_check_points(descriptor,
            {'position': (100, 0, 0), 'yaw': math.pi / 2,
             'turret_yaw': math.pi / 2}, observer=True, phase=0)[0]
        dynamic = spotting.vehicle_check_points(descriptor,
            {'position': (100, 0, 0), 'yaw': math.pi / 2,
             'turret_yaw': math.pi / 2}, observer=True, phase=1)[0]
        self.assertEqual((100, 4, 0), static)
        self.assertAlmostEqual(100.0, dynamic[0])
        self.assertAlmostEqual(2.0, dynamic[1])
        self.assertAlmostEqual(-1.0, dynamic[2])

    def test_proxy_still_ignores_a_wall_and_445_ceiling_still_applies(self):
        runtime, battle = self.battle()
        runtime.bigworld.wg_collideSegment = lambda *args: (_ for _ in ()).throw(
            AssertionError('no geometry probe expected'))
        descriptor = self.descriptor()
        observer = ((0, 0, 0), descriptor, None)
        self.assertTrue(battle._spot_line_of_sight(observer, (50, 0, 0), descriptor))
        self.assertFalse(battle._spot_line_of_sight(observer, (446, 0, 0), descriptor))


class LegacyRadioNetworkTests(unittest.TestCase):
    def setUp(self):
        self.net = radio.RadioNetwork()
        self.a, self.b, self.c = ('human', 1), ('bot', 2), ('bot', 3)
        self.enemy = ('bot', 4)
        self.actors = {self.a: (1, (0, 0, 0), 200),
                       self.b: (1, (350, 0, 0), 200),
                       self.c: (1, (700, 0, 0), 200)}
        self.net.configure(self.actors, 10)

    def test_combined_ranges_share_direct_observation(self):
        self.net.observe(self.b, self.enemy, 10, 10, {'x': 400})
        self.assertEqual((10, True, {'x': 400}),
                         self.net.contact(self.a, self.enemy, 10))

    def test_contact_does_not_hop_through_an_intermediate_ally(self):
        self.net.observe(self.c, self.enemy, 10, 10, {'x': 800})
        self.assertTrue(self.net.connected(self.a, self.b))
        self.assertTrue(self.net.connected(self.b, self.c))
        self.assertTrue(self.net.contact(self.b, self.enemy, 10)[1])
        self.assertEqual((0.0, False, None), self.net.contact(self.a, self.enemy, 10))

    def test_lost_link_cannot_reuse_a_team_lease(self):
        self.net.observe(self.b, self.enemy, 10, 10, {'x': 400})
        self.assertTrue(self.net.contact(self.a, self.enemy, 10)[1])
        self.actors[self.b] = (1, (800, 0, 0), 200)
        self.net.configure(self.actors, 11)
        self.assertEqual((0.0, False, None), self.net.contact(self.a, self.enemy, 11))
        self.assertEqual(9, self.net.contact(self.b, self.enemy, 11)[0])

    def test_missing_radios_do_not_create_full_team_visibility(self):
        self.net.configure({self.a: (1, (0, 0, 0), 0),
                            self.b: (1, (0, 0, 0), 0)}, 10)
        self.net.observe(self.b, self.enemy, 10, 10, {'x': 400})
        self.assertEqual(0, self.net.contact(self.a, self.enemy, 10)[0])
        self.assertEqual(10, self.net.contact(self.b, self.enemy, 10)[0])
        self.net.configure({self.a: (1, (0, 0, 0), 700),
                            self.b: (1, (100, 0, 0), 0)}, 10)
        self.assertFalse(self.net.connected(self.a, self.b))

    def test_disconnected_observer_cannot_refresh_a_known_pose(self):
        self.net.observe(self.b, self.enemy, 10, 10, {'x': 400})
        self.net.observe(self.c, self.enemy, 12, 10, {'x': 999})
        self.assertEqual((8, False, {'x': 400}),
                         self.net.contact(self.a, self.enemy, 12))

    def test_destroyed_observer_stops_reporting(self):
        self.net.observe(self.b, self.enemy, 10, 10, {'x': 400})
        del self.actors[self.b]
        self.net.configure(self.actors, 11)
        self.assertEqual(0, self.net.contact(self.a, self.enemy, 11)[0])

    def test_other_team_cannot_receive(self):
        self.actors[self.a] = (2, (0, 0, 0), 2000)
        self.net.configure(self.actors, 10)
        self.net.observe(self.b, self.enemy, 10, 10, {})
        self.assertEqual(0, self.net.contact(self.a, self.enemy, 10)[0])

    def test_relaying_boosts_allied_range_without_relaying_contacts(self):
        self.net.configure({self.a: (1, (0, 0, 0), 100),
                            self.b: (1, (215, 0, 0), 100),
                            self.c: (1, (100, 0, 0), 100, 0.1)}, 10)
        self.assertTrue(self.net.connected(self.a, self.b))
        self.assertAlmostEqual(110.0, self.net.actors[self.a][2])
        self.assertEqual(100, self.net.actors[self.c][2])

    def test_shared_neighborhood_receipt_keeps_long_lease_and_first_tied_pose(self):
        self.net.configure({self.a: (1, (0, 0, 0), 1000),
                            self.b: (1, (10, 0, 0), 1000)}, 10)
        self.net.observe(self.a, self.enemy, 10, 15, {'x': 1})
        self.net.observe(self.b, self.enemy, 10, 10, {'x': 2})
        first = next(iter(self.net.observations))
        expected_pose = self.net.observations[first][self.enemy][2]
        self.assertEqual((15, True, expected_pose),
                         self.net.contact(self.a, self.enemy, 10))
        self.assertEqual(self.net.contact(self.a, self.enemy, 10),
                         self.net.contact(self.b, self.enemy, 10))
        self.assertEqual(1, len(self.net.receipts[self.enemy]))
        self.net.observe(self.b, self.enemy, 11, 10, {'x': 3})
        self.assertEqual((14, True, {'x': 3}),
                         self.net.contact(self.a, self.enemy, 11))

    def test_same_time_update_refreshes_winning_pose_without_global_invalidation(self):
        other_enemy = ('bot', 5)
        self.net.observe(self.b, self.enemy, 10, 10, {'x': 1})
        self.net.observe(self.b, other_enemy, 10, 10, {'x': 20})
        self.net.contact(self.a, other_enemy, 10)
        unrelated = next(iter(self.net.receipts[other_enemy].values()))
        self.net.contact(self.a, self.enemy, 10)
        self.net.observe(self.b, self.enemy, 10, 10, {'x': 2})
        self.assertEqual((10, True, {'x': 2}),
                         self.net.contact(self.a, self.enemy, 10))
        self.assertIs(unrelated, next(iter(
            self.net.receipts[other_enemy].values())))

    def test_delayed_sample_rebuilds_latest_pose_and_freshness(self):
        self.net.observe(self.a, self.enemy, 10, 10, {'x': 1})
        self.net.observe(self.b, self.enemy, 10.4, 10, {'x': 2})
        self.assertEqual((20.4 - 10.4, True, {'x': 2}),
                         self.net.contact(self.a, self.enemy, 10.4))
        self.net.observe(self.b, self.enemy, 9, 10, {'x': 3})
        self.assertEqual((20.4 - 10.4, True, {'x': 1}),
                         self.net.contact(self.a, self.enemy, 10.4))
        self.assertEqual((20.4 - 10.6, False, {'x': 1}),
                         self.net.contact(self.a, self.enemy, 10.6))

    def test_hiding_freshest_observer_retains_other_direct_sample(self):
        self.net.observe(self.a, self.enemy, 10, 15, {'x': 1})
        self.net.observe(self.b, self.enemy, 10.2, 10, {'x': 2})
        self.net.contact(self.a, self.enemy, 10.4)
        self.net.hidden(self.b, self.enemy)
        self.assertEqual((14.6, True, {'x': 2}),
                         self.net.contact(self.a, self.enemy, 10.4))
        receipt = next(iter(self.net.receipts[self.enemy].values()))
        self.net.hidden(self.b, self.enemy)
        self.assertIs(receipt, next(iter(self.net.receipts[self.enemy].values())))
        self.net.hidden(self.a, self.enemy)
        self.assertEqual((14.6, False, {'x': 2}),
                         self.net.contact(self.a, self.enemy, 10.4))

    def test_expired_lease_does_not_supply_pose_or_freshness(self):
        self.net.observe(self.a, self.enemy, 10, 15, {'x': 1})
        self.net.observe(self.b, self.enemy, 11, 0.1, {'x': 2})
        self.assertEqual((13.8, False, {'x': 1}),
                         self.net.contact(self.a, self.enemy, 11.2))
        self.assertEqual((0.0, False, None),
                         self.net.contact(self.a, self.enemy, 25))

    def test_new_observer_recomputes_equal_time_dictionary_winner(self):
        actors = dict((('bot', index), (1, (index, 0, 0), 1000))
                      for index in range(1, 40))
        self.net.configure(actors, 10)
        recipient = ('bot', 1)
        for observer in actors:
            self.net.observe(observer, self.enemy, 10, 10,
                             {'x': observer[1]})
            winner = next(iter(self.net.observations))
            self.assertEqual((10, True, {'x': winner[1]}),
                             self.net.contact(recipient, self.enemy, 10))


class ClientRadioPresentationTests(unittest.TestCase):
    def battle(self):
        runtime = fixtures._runtime()
        battle = fixtures.BattleRuntime(runtime)
        battle.client = fixtures._Client()
        battle._avatar = runtime.bigworld.avatar
        battle._records = {
            'player:1': {'local': True, 'state': {'team': 1, 'alive': True}},
            'bot:9': {'kind': 'bot', 'network_id': 9, 'state': {'team': 2},
                      'presentation': False, 'spot_until': 0.0}}
        return battle

    def contact(self, **overrides):
        row = {'observing_team': 1, 'target_kind': 'bot', 'target_id': 9,
               'visible': True, 'time_left': 10.0, 'visible_by_player_ids': [],
               'visible_by_bot_ids': [11], 'radio_recipients': []}
        row.update(overrides)
        return row

    def test_disconnected_receiver_cannot_use_another_observers_lease(self):
        battle = self.battle()
        contact = self.contact(radio_recipients=[
            {'kind': 'human', 'id': 1, 'time_left': 6.0}])
        message = {'type': 'bot_observation', 'contacts': [contact]}
        battle._apply_team_observation(message, 10.0)
        self.assertEqual(16.0, battle._records['bot:9']['radio_spot_until'])
        contact['radio_recipients'] = []
        battle._apply_team_observation(message, 11.0)
        self.assertEqual(11.0, battle._records['bot:9']['radio_spot_until'])

    def test_old_message_without_radio_data_only_preserves_self_spot(self):
        battle = self.battle()
        contact = self.contact()
        del contact['radio_recipients']
        message = {'type': 'bot_observation', 'contacts': [contact]}
        battle._apply_team_observation(message, 10.0)
        self.assertEqual(10.0, battle._records['bot:9']['radio_spot_until'])
        contact['visible_by_player_ids'] = [1]
        battle._apply_team_observation(message, 11.0)
        self.assertEqual(21.0, battle._records['bot:9']['radio_spot_until'])

    def test_empty_radio_links_clear_prior_ally_minimap_knowledge(self):
        battle = self.battle()
        message = {'type': 'bot_observation', 'contacts': [],
                   'radio_links': [{'kind': 'human', 'id': 1,
                       'allies': [{'kind': 'bot', 'id': 11}]}]}
        battle._apply_team_observation(message, 10.0)
        self.assertEqual({('bot', 11)}, battle._radio_ally_contacts)
        message['radio_links'] = []
        battle._apply_team_observation(message, 11.0)
        self.assertEqual(set(), battle._radio_ally_contacts)


if __name__ == '__main__':
    unittest.main()
