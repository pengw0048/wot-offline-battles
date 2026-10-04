"""Reported Airfield prop chains through the planning-only native filter.

Segments and identities come from reports 153100 and 175318. Native callback
ordering is controlled: these tests prove filtering, not Windows ray traversal.
"""
import copy
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

import test_port_0922_bot_destructible_approach as approach_fixture
import test_port_0922_destructibles as fixture
from test_port_0922_destructibles import _Vector
from gui.mods.offline_lan_0922 import destructibles_sensor as sensor


class PlanningDestructibleOverlapTests(unittest.TestCase):
    # The final case is M46 Patton KR id22's three ClayFence1/1/7 identities
    # in the 17:51:05.445 report; its first actual native hit was item45.
    CASES = (
        (((31869, 135), (31869, 104)),
         (-279.5202941894531, .7200000286102295, -192.47972106933594),
         (-275.5202941894531, .8410000205039978, -188.47972106933594),
         (-279.2751770019531, .7274150252342224, -192.23460388183594)),
        (((31869, 80), (31869, 84)),
         (-275.8500061035156, .7200000286102295, -170.0),
         (-275.8500061035156, .7200000286102295, -166.0),
         (-275.8500061035156, .7200000286102295, -169.78829956054688)),
        (((31612, 45), (31612, 43), (31612, 44)),
         (-330.0, .7200000286102295, -214.0),
         (-326.0, .7200000286102295, -210.0),
         (-329.79266357421875, .7200000286102295, -213.79266357421875)),
    )

    def setUp(self):
        self.cleanup = fixture.DestructiblesCompatibilityTests()
        self.cleanup.setUp()
        self.addCleanup(self.cleanup.tearDown)
        sensor.xrange = range

    def replay(self, case=0, blocker=None, budget=0,
               catalog_state='cold', retained=False, layers=None):
        identities, raw_start, raw_end, raw_hit = self.CASES[case]
        catalog = json.loads((Path(__file__).resolve().parents[1] /
                              'destructibles/31_airfield.json').read_text())
        sensor.set_catalog(catalog)
        sensor.g_offh_destr_instances = instances = {}
        sensor.g_offh_destr_contact_bins = bins = {}
        if catalog_state == 'warm':
            for identity in identities:
                instances[identity] = copy.deepcopy(
                    sensor._destructible_catalog['baked_instances'][identity])
                sensor._index_catalog_instance_1513(bins, identity, instances[identity])
                if retained:
                    resource = sensor._destructible_catalog['resources'][
                        instances[identity]['filename']]
                    resource['retained_collision_boxes'] = frozenset((0,))
        elif catalog_state == 'missing':
            sensor.set_catalog(None)
        if layers is None:
            layers = [(73, 0, item, chunk) for chunk, item in identities]
        start, end, point = (_Vector(*v) for v in (raw_start, raw_end, raw_hit))
        hit = (point, _Vector(0, 0, -1))
        callbacks = []

        def collide(space, ray_start, ray_end, flags, keep):
            # The compatibility call must requery the complete segment once.
            self.assertIs(start, ray_start)
            self.assertIs(end, ray_end)
            for surface in list(layers) + ([] if blocker is None else [blocker]):
                kept = keep(*surface)
                callbacks.append(tuple(surface) + (kept,))
                if kept:
                    return hit
            return None

        query = mock.Mock(side_effect=collide)
        trace, recast_budget = {}, [budget]
        with mock.patch.dict(sys.modules, {
                'BigWorld': types.SimpleNamespace(wg_collideSegment=query)}), \
                mock.patch.object(sensor, '_stock_crushable_1513',
                                  side_effect=AssertionError('planning used kinetics')), \
                mock.patch.object(sensor, '_planning_catalog_candidate_1513',
                                  side_effect=AssertionError('planning used catalog identity')), \
                mock.patch.object(sensor, '_stream_baked_shot_instance_1513',
                                  side_effect=AssertionError('planning registered a prop')):
            result = sensor._catalog_soft_static_path(
                1, start, end, hit, 0.0, None, recast_budget=recast_budget,
                trace=trace, ignore_destructibles=True)
        self.assertEqual([budget], recast_budget)
        self.assertEqual(1, query.call_count)
        self.assertNotIn('objects', trace)
        return result, trace, callbacks

    def test_report_stove_fence_overlaps_do_not_need_unique_catalog_identity(self):
        for case in (0, 1):
            for catalog_state in ('cold', 'warm', 'missing'):
                with self.subTest(case=case, catalog_state=catalog_state):
                    result, trace, callbacks = self.replay(case, catalog_state=catalog_state)
                    self.assertIs(True, result)
                    self.assertEqual('clear', trace['classification'])
                    self.assertEqual('native_corridor_clear', trace['reason'])
                    self.assertTrue(all(not row[-1] for row in callbacks))

    def test_m46_report_three_clay_fences_clear_without_registration_or_budget(self):
        for catalog_state in ('cold', 'missing'):
            result, trace, callbacks = self.replay(2, catalog_state=catalog_state, budget=0)
            self.assertIs(True, result)
            self.assertEqual('native_corridor_clear', trace['reason'])
            self.assertEqual([(73, 0, item, chunk, False)
                for chunk, item in self.CASES[2][0]], callbacks)

    def test_more_than_four_layers_need_one_query_even_with_zero_budget(self):
        layers = [(71 + index % 15, 0, index, 31612) for index in range(40)]
        result, trace, callbacks = self.replay(2, layers=layers, catalog_state='missing')
        self.assertIs(True, result)
        self.assertEqual(40, len(callbacks))
        self.assertEqual('clear', trace['classification'])

    def test_backing_wall_terrain_and_replacement_survive_any_prop_chain(self):
        for material in (0, 5, 70, 86, 87, 99, 100, 107):
            with self.subTest(material=material):
                result, trace, callbacks = self.replay(
                    2, blocker=(material, 8, 45, 31612), catalog_state='missing')
                self.assertIs(False, result)
                self.assertEqual('hard', trace['classification'])
                self.assertEqual('native_hard_geometry', trace['reason'])
                self.assertTrue(callbacks[-1][-1])

    def test_catalog_retained_base_does_not_turn_original_material_into_a_wall(self):
        result, trace, unused = self.replay(catalog_state='warm', retained=True)
        self.assertIs(True, result)
        self.assertEqual('clear', trace['classification'])
        result, trace, unused = self.replay(
            catalog_state='warm', retained=True, blocker=(87, 0, 135, 31869))
        self.assertIs(False, result)
        self.assertEqual('native_hard_geometry', trace['reason'])

    def test_native_filter_requires_exact_four_integer_callback_contract(self):
        keep = sensor.prepare_navigation_collision_filter(_Vector(), _Vector(0, 0, 5))
        for material in range(71, 86):
            self.assertFalse(keep(material, 0, 999999, 0))
        malformed = [(73,), (73, 0, 1), (73, 0, 1, 2, 3),
                     (73.0, 0, 1, 2), ('73', 0, 1, 2),
                     (73, False, 1, 2), (73, 0, 1.0, 2), (73, 0, 1, None)]
        for surface in malformed:
            self.assertTrue(keep(*surface), surface)

    def test_accepted_broken_identity_cannot_hide_an_actual_hard_material(self):
        sensor.g_offh_tree_state = {'native_committed': {(77, 1)}}
        keep = sensor.prepare_navigation_collision_filter(_Vector(), _Vector(0, 0, 5))
        self.assertFalse(keep(71, 0, 1, 77))
        for material in (5, 86, 87, 100, 107):
            self.assertTrue(keep(material, 0, 1, 77), material)

    def test_planning_ignores_kinetics_but_physical_default_does_not(self):
        approach = approach_fixture.BotDestructibleApproachTests()
        approach.setUp()
        self.addCleanup(approach.doCleanups)
        with approach.scene(registered=True, health=1000000.0) as scene:
            start, end = _Vector(0, .7, 0), _Vector(0, .7, 10)
            hit = scene.bigworld.wg_collideSegment(1, start, end, 0)
            self.assertIs(False, sensor._catalog_soft_static_path(
                1, start, end, hit, 0.0, scene.descriptor))
            self.assertIs(True, sensor._catalog_soft_static_path(
                1, start, end, hit, 0.0, scene.descriptor,
                ignore_destructibles=True))

    def test_planning_ground_removes_even_low_destructible_roofs(self):
        approach = approach_fixture.BotDestructibleApproachTests()
        approach.setUp()
        self.addCleanup(approach.doCleanups)
        with approach.scene(registered=True, health=1000000.0) as scene:
            start, end = _Vector(0, 10, 4), _Vector(0, -2, 4)
            hit = scene.bigworld.wg_collideSegment(1, start, end, 0)
            support = sensor.planning_support_below_soft_roof(
                1, start, end, hit, 10.0, 0.0, None,
                recast_budget=[24], ignore_destructibles=True)
            self.assertAlmostEqual(0.0, support[0].y)

    def test_planning_ground_recast_keeps_the_felled_tree_filter(self):
        approach = approach_fixture.BotDestructibleApproachTests()
        approach.setUp()
        self.addCleanup(approach.doCleanups)
        with approach.scene(registered=True) as scene:
            sensor.g_offh_tree_state = {'native_committed': {(77, 1)}}
            original = scene.bigworld.wg_collideSegment

            def with_tree(space, start, end, flags, keep=None):
                hit = original(space, start, end, flags, keep)
                if abs(end.y - start.y) > 1e-8:
                    fraction = (2.0 - start.y) / (end.y - start.y)
                    if (0 <= fraction <= 1 and
                            (keep is None or keep(71, 0, 1, 77))):
                        point = start + (end - start).scale(fraction)
                        if hit is None or (point-start).length < (hit[0]-start).length:
                            return point, _Vector(0, 1, 0)
                return hit

            with mock.patch.object(scene.bigworld, 'wg_collideSegment',
                                   side_effect=with_tree):
                self.assertAlmostEqual(0.0, scene.battle._navigation_ground(0, 4, 0))


if __name__ == '__main__':
    unittest.main()
