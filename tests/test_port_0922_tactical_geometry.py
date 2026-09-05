import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/res/scripts/client'))
from gui.mods.offline_lan_0922.ai import tactical_geometry as geometry
from gui.mods.offline_lan_0922 import bot_runtime
import test_port_0922_battle_runtime as fixtures


class TacticalGeometryTests(unittest.TestCase):
    def scene(self):
        return fixtures.BattleRuntimeContractTests()._bot_lane_scene()

    def test_exposure_distinguishes_hull_down_from_side_crossfire(self):
        unused, unused_battle, source, unused_target, descriptor = self.scene()
        samples = geometry.body_samples(descriptor)
        source['position'] = (0.0, 0.0, 0.0)
        front = (0.0, 2.5, 100.0)
        side = (100.0, 2.5, 0.0)
        clear = lambda origin, point: origin == side or point[1] > 1.8
        covered = geometry.exposure(samples, source, [front], clear)
        self.assertGreater(covered, 0.0)
        self.assertLess(covered, 1.0)
        self.assertEqual(1.0, geometry.exposure(samples, source, [front, side], clear))

    def test_budget_exhaustion_never_becomes_safe_cover(self):
        unused, unused_battle, source, unused_target, descriptor = self.scene()
        budget = geometry.RayBudget(lambda *unused: False, 1)
        with self.assertRaises(geometry.ProbeBudgetExhausted):
            geometry.exposure(geometry.body_samples(descriptor), source,
                              [(0.0, 2.0, 100.0)], budget.clear)
        self.assertEqual(0, budget.remaining)

    def test_search_covers_both_flanks_and_scales_with_vehicle(self):
        small = geometry.search_offsets(2.0, 4.0, 0)
        large = geometry.search_offsets(4.0, 8.0, 0)
        self.assertEqual(17, len(small))
        self.assertTrue(any(value[1] < -1.0 for value in small))
        self.assertTrue(any(value[1] > 1.0 for value in small))
        self.assertEqual(small[1:], geometry.search_offsets(2, 4, 1)[:-1])
        self.assertEqual(tuple(v * 2 for v in small[1]), large[1])

    def test_incoming_probe_uses_observed_enemy_origin_not_live_entity_pose(self):
        runtime, battle, source, target, unused = self.scene()
        rays = []
        runtime.bigworld.wg_collideSegment = lambda space, start, end, mask: (
            rays.append((tuple(start), tuple(end))) or None)
        runtime.bigworld.entities[20].position = fixtures._Vector(900, 900, 900)
        self.assertTrue(battle._bot_incoming_lane(source, target))
        self.assertLessEqual(len(rays), 2)
        self.assertAlmostEqual(100.0, rays[0][0][2])
        self.assertAlmostEqual(0.0, rays[0][1][2])
        target['position'] = (50.0, 0.0, 80.0)
        self.assertTrue(battle._bot_incoming_lane(source, target))
        self.assertAlmostEqual(50.0, rays[-1][0][0])
        runtime.bigworld.entities[20].isStarted = False
        self.assertIsNone(battle._bot_incoming_lane(source, target))

    def test_nominal_damage_selects_better_clear_part_with_existing_ray_bound(self):
        runtime, battle, source, target, unused = self.scene()
        rays = []
        runtime.bigworld.wg_collideSegment = lambda *args: rays.append(args) or None
        battle._bot_aim_damage_score = lambda d, e, s, t, o, p: 100.0 if p[1] > 1.8 else 0.0
        self.assertTrue(battle._bot_firing_lane(source, target))
        self.assertEqual(2, len(rays))
        self.assertGreater(battle._bot_aim_point(source, target)['aim_position'][1], 1.8)

    def test_nominal_damage_does_not_sample_projectile_rng_or_live_angles(self):
        runtime, battle, source, target, descriptor = self.scene()
        for name in ('chassis', 'hull', 'turret', 'gun'):
            getattr(descriptor, name).hitTester.localHitTest = lambda *unused: ()
        source_descriptor = runtime.bigworld.entities[10].typeDescriptor
        collisions = [types.SimpleNamespace(dist=100.0,
            hitAngleCos=1.0, matInfo={'armor': 20.0, 'vehicleDamageFactor': 1.0},
            compName='hull')]
        with mock.patch.object(fixtures.battle_runtime_module, 'collide_vehicle_at_matrix', return_value=collisions) as collide:
            with mock.patch('random.uniform', side_effect=AssertionError('uniform RNG')), \
                    mock.patch.object(
                        fixtures.battle_runtime_module.combat_rules.random,
                        'gauss', side_effect=AssertionError('gaussian RNG')):
                scores = []
                for kind in ('ARMOR_PIERCING', 'ARMOR_PIERCING_CR',
                             'HOLLOW_CHARGE', 'HIGH_EXPLOSIVE'):
                    source_descriptor.gun.shots[0].shell.kind = kind
                    scores.append(battle._bot_aim_damage_score(
                        source_descriptor, {'descriptor': descriptor},
                        source, target, (0.0, 2.0, 0.0),
                        (0.0, 1.2, 100.0)))
        self.assertTrue(all(score is not None and score > 0.0
                            for score in scores))
        self.assertAlmostEqual(100.0, collide.call_args[0][1].translation.z)

    def test_cover_job_counts_ground_and_water_queries_and_requires_return_path(self):
        runtime, battle, source, target, unused = self.scene()
        source['team'] = 1
        battle._bots = types.SimpleNamespace(_visible_target_poses={})
        calls = []
        def ground(x, z, hint):
            calls.append('ground')
            return 0.0
        def water(point):
            calls.append('water')
            return -1.0
        def ray(start, end):
            calls.append('ray')
            return end[2] > 90.0 or abs(end[0]) > 2.0
        battle._cover_ground = ground
        battle._water_depth = water
        battle._tactical_ray_clear = ray
        candidates = battle._sample_bot_cover(source, target, (0, 0, 50), (), lambda *unused: True)
        self.assertTrue(candidates)
        self.assertLessEqual(len(calls), 40)
        self.assertIn('ground', calls)
        self.assertIn('water', calls)
        self.assertTrue(any(value['exposure'] > 0.12 for value in candidates))
        calls[:] = []
        battle._records['bot:11']['cover_search_phase'] = 0
        def no_return(start, end):
            return not (abs(start[0]) > 2.0 and abs(end[0]) < 1.0)
        self.assertFalse(battle._sample_bot_cover(source, target, (0, 0, 50), (), no_return))
        self.assertLessEqual(len(calls), 40)

    def test_incoming_reuses_enemy_receipt_only_for_a_fresh_local_observation(self):
        bots = bot_runtime.BotRuntime(1)
        bots.states = {11: {'id': 11, 'team': 1, 'alive': True},
                       21: {'id': 21, 'team': 2, 'alive': True}}
        target = {'team': 2, 'x': 0, 'y': 0, 'z': 100}
        key = (1, 'bot', 21)
        bots._visible_target_poses[key] = dict(target)
        bots._team_spot_time_left = lambda *unused: 5.0
        aggregate = {key: [True, {11}, dict(target), set(), {11}]}
        bots._shot_los_cache[(11, 'bot', 21)] = (1.0, True)
        self.assertEqual([], bots._pack_observations(aggregate, 1.0)[0]['threatened_bot_ids'])
        bots._shot_los_cache[(21, 'bot', 11)] = (1.0, True)
        self.assertEqual([11], bots._pack_observations(aggregate, 1.0)[0]['threatened_bot_ids'])
        aggregate[key][4] = set()
        self.assertEqual([], bots._pack_observations(aggregate, 1.0)[0]['threatened_bot_ids'])
        aggregate[key][4] = {11}
        self.assertEqual([], bots._pack_observations(aggregate, 3.0)[0]['threatened_bot_ids'])

    def test_cover_identity_survives_server_order_admission(self):
        bots = bot_runtime.BotRuntime(1)
        order = {'id': 11, 'team': 1, 'cover_id': '11:-2:7:-1',
                 'move_position': {'x': 0, 'y': 0, 'z': 12}}
        self.assertTrue(bots._apply_orders({'bot_order_revision': 1, 'bot_orders': [order]}))
        self.assertEqual(order['cover_id'], bots._server_orders[11]['cover_id'])

    def test_radio_cell_uses_dry_baked_ground_instead_of_blocked_centre(self):
        from gui.mods.offline_lan_0922.ai.navigation import TerrainGrid
        from test_port_0922_ai import BotAiPortTests
        graph = BotAiPortTests._baked_graph(9, 9, blocked=((4, 4),))
        grid = TerrainGrid(None, baked_graph=graph)
        bots = bot_runtime.BotRuntime(1)
        bots.states[11] = {'id': 11}
        goal = grid.point_for((4, 4), 900.0)
        first, last = grid.point_for((2, 2), 0), grid.point_for((6, 6), 0)
        strategic = {'team_command_id': '1:1:1', 'move_area_bounds':
                     (first[0], first[2], last[0], last[2])}
        resolved = bots._radio_ground_goal(11, (0, 0, 0), goal, strategic, grid)
        self.assertIsNotNone(resolved)
        self.assertNotEqual(grid.cell_for(goal), grid.cell_for(resolved))
        self.assertEqual(0.0, resolved[1])
        self.assertTrue(first[0] <= resolved[0] <= last[0])


if __name__ == '__main__':
    unittest.main()
