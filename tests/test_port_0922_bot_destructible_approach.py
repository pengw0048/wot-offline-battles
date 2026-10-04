"""Bot approach probes: cold props and roofs, not native gameplay acceptance.

The small house dimensions come from the shipped Airfield catalog. Placement,
module health and native ray responses are controlled fixtures; no report pose
or Windows engine execution is claimed.
"""
import contextlib
import json
import math
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

import test_port_0922_battle_runtime as runtime_fixture
import test_port_0922_destructibles as destructible_fixture
from test_port_0922_destructibles import _Vector, _ItemMatrix, _Manager, _catalog
from gui.mods.offline_lan_0922 import destructibles_sensor as sensor

ROOT = Path(__file__).resolve().parents[1]
HOUSE = 'content/Buildings/bldAF_001_vhouse/normal/lod0/bldAF_001_vhouse1.model'


class BotDestructibleApproachTests(unittest.TestCase):
    def setUp(self):
        self.cleanup = destructible_fixture.DestructiblesCompatibilityTests()
        self.cleanup.setUp()
        self.addCleanup(self.cleanup.tearDown)
        sensor.xrange = range

    @contextlib.contextmanager
    def scene(self, house_z=5.0, health=5.0, registered=False,
              backing_z=None, support_y=0.0, unknown=False, speed_cap=20.0):
        raw = json.loads((ROOT / 'destructibles/31_airfield.json').read_text())
        resource = raw['resources'][HOUSE]
        matrix = _ItemMatrix(_Vector(0.0, 0.0, house_z))
        math_module = types.SimpleNamespace(Vector3=_Vector, Matrix=lambda value: value)
        signature = sensor._locator_signature(matrix, _Vector(), math_module, 1000)
        catalog = _catalog({HOUSE: resource}, [list(signature) + [HOUSE, None, 22, 0, 1.0]])
        catalog['map'] = '31_airfield'
        sensor.set_catalog(catalog)
        prepared = sensor._destructible_catalog
        baked = prepared['baked_instances'][(22, 0)]
        manager = _Manager()
        manager.space_id = 1
        manager.set_chunk_count(22, 1)
        descriptor = runtime_fixture._Descriptor()
        descriptor.physics['weight'] = 10000.0
        area = types.SimpleNamespace(
            g_destructiblesManager=manager,
            DESTR_TYPE_TREE=1, DESTR_TYPE_FALLING_ATOM=2,
            DESTR_TYPE_FRAGILE=3, DESTR_TYPE_STRUCTURE=4,
            DESTRUCTIBLE_MATKIND=types.SimpleNamespace(NORMAL_MIN=73, NORMAL_MAX=86),
            g_cache=types.SimpleNamespace(unitVehicleMass=10000.0,
                getDescByFilename=lambda name: {'type': 4, 'modules': {73: {'health': health, 'destrEffect': 'test'}}}
                if name.lower() == HOUSE.lower() else None))
        cache = types.SimpleNamespace(scaledDestructibleHealth=lambda scale, value: scale * value)
        authority = types.SimpleNamespace(
            is_destroyed=mock.Mock(return_value=False),
            destroy_module=mock.Mock(return_value=True),
            destroy_fragile=mock.Mock(return_value=True),
            destroy_column=mock.Mock(return_value=True),
            destroyed_keys=lambda *args: ())
        rays = []
        def collide(space, start, end, flags, keep=None):
            rays.append((start, end, keep))
            hits = []
            if keep is None or keep(5 if unknown else 73, 0, 0, 22):
                for box in baked['boxes']:
                    interval = sensor._segment_world_box_interval(start, end, box, 0.0)
                    if interval is not None:
                        at = start + (end-start).scale(interval[0])
                        normal = _Vector(0, 1, 0) if abs(start.y-end.y) > 1.0 else _Vector(0, 0, -1)
                        hits.append((interval[0], at, normal))
            if support_y is not None and abs(end.y-start.y) > 1e-8:
                t = (support_y-start.y)/(end.y-start.y)
                if 0 <= t <= 1:
                    hits.append((t, start+(end-start).scale(t), _Vector(0, 1, 0)))
            if backing_z is not None and abs(end.z-start.z) > 1e-8:
                t = (backing_z-start.z)/(end.z-start.z)
                if 0 <= t <= 1:
                    hits.append((t, start+(end-start).scale(t), _Vector(0, 0, -1)))
            if not hits:
                return None
            _, at, normal = min(hits, key=lambda entry: entry[0])
            return at, normal
        bigworld = types.SimpleNamespace(
            wg_collideSegment=collide,
            wg_getChunkDestrFilenames=mock.Mock(return_value=(HOUSE,)),
            wg_getChunkMatrix=mock.Mock(return_value=types.SimpleNamespace(translation=_Vector())),
            wg_getDestructibleMatrix=mock.Mock(return_value=matrix),
            wg_getDestructibleEffectCategory=mock.Mock(return_value=4),
            time=lambda: 10.0)
        battle = runtime_fixture.BattleRuntime(runtime_fixture._runtime())
        battle._runtime.bigworld = bigworld
        battle._runtime.math = math_module
        battle._avatar = types.SimpleNamespace(spaceID=1)
        battle._destructibles = sensor
        battle._soft_static_recast_budget = [100]
        battle._water_depth = lambda point: -1.0
        params = {'mass': 10000.0, 'speedFwd': speed_cap, 'speedBwd': speed_cap/2.0}
        fixture = types.SimpleNamespace(battle=battle, bigworld=bigworld, authority=authority,
                                       descriptor=descriptor, rays=rays, box=baked['boxes'][0])
        with mock.patch.dict(sys.modules, {'BigWorld': bigworld, 'Math': math_module,
                'AreaDestructibles': area, 'DestructiblesCache': cache}), \
                mock.patch.object(sensor, '_get_destr_authority', return_value=authority), \
                mock.patch.object(runtime_fixture.battle_runtime_module.vehicle_physics,
                                  'derive_params', return_value=params), \
                mock.patch.object(runtime_fixture.battle_runtime_module.vehicle_physics,
                                  'engine_force', return_value=20000.0):
            if registered:
                self.assertIsNotNone(sensor._stream_baked_shot_instance_1513(1, (22, 0)))
            yield fixture
        authority.destroy_module.assert_not_called()
        authority.destroy_fragile.assert_not_called()
        authority.destroy_column.assert_not_called()

    def test_stationary_far_ray_live_validates_a_cold_catalog_house(self):
        with self.scene(house_z=9.0) as f:
            start, end = _Vector(0, 0.7, 0), _Vector(0, 0.7, 14)
            hit = f.bigworld.wg_collideSegment(1, start, end, 0)
            result = sensor._catalog_soft_static_path(1, start, end, hit,
                0.0, f.descriptor, recast_budget=[24], allow_kinetic_first=True,
                kinetic_speed=20.0)
            self.assertEqual('kinetic', result)
            self.assertIn((22, 0), sensor.g_offh_destr_instances)
            self.assertGreater(f.bigworld.wg_getDestructibleMatrix.call_count, 0)

    def test_approach_does_not_mistake_crushable_airfield_roof_for_a_climb(self):
        with self.scene(registered=True) as f:
            result = f.battle._direction_probe((0, 0, 0), 0, 0, f.descriptor, 4.0)
            self.assertTrue(result['clear'], result)
            self.assertEqual(0.0, result['slope'])

    def test_cold_roof_and_front_wall_can_be_probed_before_bot_moves(self):
        with self.scene() as f:
            result = f.battle._direction_probe((0, 0, 0), 0, 0, f.descriptor, 4.0)
            self.assertTrue(result['clear'], result)
            self.assertEqual(0.0, result['slope'])

    def test_confirmed_house_is_ignored_without_consulting_drive_capability(self):
        with self.scene(registered=True, health=1000000.0) as f:
            physics = runtime_fixture.battle_runtime_module.vehicle_physics
            with mock.patch.object(physics, 'derive_params',
                                   side_effect=AssertionError('no planning physics')), \
                    mock.patch.object(sensor, '_stock_crushable_1513',
                                      side_effect=AssertionError('no planning crush law')):
                result = f.battle._direction_probe(
                    (0, 0, 0), 0, 0, f.descriptor, 4.0)
                self.assertTrue(result['clear'], result)
                self.assertEqual(0.0, result['slope'])

    def test_planning_clearance_cannot_authorize_an_uncrushable_motion_receipt(self):
        with self.scene(registered=True, health=1000000.0) as f:
            self.assertTrue(f.battle._direction_probe(
                (0, 0, 0), 0, 0, f.descriptor, 4.0)['clear'])
            self.assertFalse(f.battle._direction_world_receipt(
                (0, 0, 0), 0, 0, f.descriptor, 4.0))

    def test_navigation_support_filters_soft_roof_but_physical_support_keeps_it(self):
        with self.scene(registered=True, health=1000000.0) as f:
            top = f.battle._ground_y(0.0, 4.0, 0.0)
            self.assertGreater(top, 0.0)
            self.assertEqual(0.0, f.battle._navigation_ground(0.0, 4.0, 0.0))
            self.assertEqual(top, f.battle._ground_y(0.0, 4.0, 0.0))

    def test_real_backing_wall_survives_soft_house_filtering(self):
        with self.scene(registered=True, backing_z=3.0) as f:
            result = f.battle._direction_probe((0, 0, 0), 0, 0, f.descriptor, 4.0)
            self.assertFalse(result['clear'], result)
            self.assertTrue(result['collision'], result)
            self.assertEqual(0.0, result['slope'])

    def test_native_surface_without_matching_material_never_disappears(self):
        with self.scene(registered=True, unknown=True) as f:
            result = f.battle._direction_probe((0, 0, 0), 0, 0, f.descriptor, 4.0)
            self.assertFalse(result['clear'], result)

    def test_exhausted_contact_budget_does_not_stop_planning_through_a_cold_prop(self):
        with self.scene() as f:
            f.battle._soft_static_recast_budget[:] = [0]
            result = f.battle._direction_probe((0, 0, 0), 0, 0, f.descriptor, 4.0)
            self.assertTrue(result['clear'], result)
            self.assertFalse(result.get('deferred'), result)
            self.assertFalse(result['collision'], result)
            f.bigworld.wg_getDestructibleMatrix.assert_not_called()
            self.assertEqual([0], f.battle._soft_static_recast_budget)
            f.battle._soft_static_recast_budget[:] = [24]
            self.assertTrue(f.battle._direction_probe(
                (0, 0, 0), 0, 0, f.descriptor, 4.0)['clear'])
            f.bigworld.wg_getDestructibleMatrix.assert_not_called()
            self.assertEqual([24], f.battle._soft_static_recast_budget)

    def test_planning_does_not_spend_the_physical_contact_registration_budget(self):
        with self.scene() as f:
            f.battle._soft_static_recast_budget[:] = [1]
            result = f.battle._direction_probe((0, 0, 0), 0, 0, f.descriptor, 4.0)
            self.assertTrue(result['clear'], result)
            self.assertFalse(result.get('deferred'), result)
            self.assertFalse(result['collision'], result)
            self.assertEqual([1], f.battle._soft_static_recast_budget)
            self.assertNotIn((22, 0), getattr(sensor, 'g_offh_destr_instances', {}))
            f.bigworld.wg_getDestructibleMatrix.assert_not_called()

    def test_planning_does_not_wait_for_a_matching_live_manager(self):
        with self.scene() as f:
            manager = sys.modules['AreaDestructibles'].g_destructiblesManager
            manager.space_id = 99
            result = f.battle._direction_probe((0, 0, 0), 0, 0, f.descriptor, 4.0)
            self.assertTrue(result['clear'], result)
            self.assertFalse(result.get('deferred'), result)
            self.assertNotIn((22, 0), getattr(sensor, 'g_offh_destr_instances', {}))
            manager.space_id = 1
            self.assertTrue(f.battle._direction_probe(
                (0, 0, 0), 0, 0, f.descriptor, 4.0)['clear'])
            f.bigworld.wg_getDestructibleMatrix.assert_not_called()

    def test_native_original_surface_plans_clear_despite_a_stale_catalog_placement(self):
        with self.scene() as f:
            f.bigworld.wg_getDestructibleMatrix.return_value = _ItemMatrix(_Vector(80, 0, 5))
            result = f.battle._direction_probe((0, 0, 0), 0, 0, f.descriptor, 4.0)
            self.assertTrue(result['clear'], result)
            f.bigworld.wg_getDestructibleMatrix.assert_not_called()
            self.assertNotIn((22, 0), getattr(sensor, 'g_offh_destr_instances', {}))
            # The physical receipt still needs its own exact identity proof.
            self.assertFalse(f.battle._direction_world_receipt(
                (0, 0, 0), 0, 0, f.descriptor, 4.0))

    def test_navigation_and_approach_ignore_native_props_without_any_catalog(self):
        with self.scene() as f:
            with mock.patch.object(sensor, '_destructible_catalog', None), \
                    mock.patch.object(sensor, '_stream_baked_shot_instance_1513',
                                      side_effect=AssertionError('planning hydrated catalog')):
                f.battle._soft_static_recast_budget[:] = [0]
                self.assertEqual(0.0, f.battle._navigation_ground(0.0, 4.0, 0.0))
                result = f.battle._direction_probe((0, 0, 0), 0, 0, f.descriptor, 4.0)
                self.assertTrue(result['clear'], result)
                self.assertEqual(0.0, result['slope'])
                self.assertEqual([0], f.battle._soft_static_recast_budget)

    def test_approach_still_requires_real_support_and_acceptable_grade(self):
        for support_y in (None, -4.0, 2.0):
            with self.subTest(support_y=support_y), self.scene(support_y=support_y) as f:
                result = f.battle._direction_probe((0, 0, 0), 0, 0, f.descriptor, 4.0)
                self.assertFalse(result['clear'], result)
                if support_y is not None:
                    self.assertNotEqual(99.0, result['slope'])

    def test_ground_below_a_crushable_roof_cannot_hide_deep_water(self):
        with self.scene() as f:
            f.battle._water_depth = lambda point: 2.0 if point[2] >= 4.0 else -1.0
            result = f.battle._direction_probe((0, 0, 0), 0, 0, f.descriptor, 4.0)
            self.assertFalse(result['clear'], result)
            self.assertTrue(result['water'], result)

    def test_passive_motion_does_not_inherit_powered_roof_clearance(self):
        with self.scene() as f:
            result = f.battle._direction_probe((0, 0, 0), 0, 1, None, 4.0)
            self.assertFalse(result['clear'], result)
            self.assertGreater(result['slope'], 0.48)
            self.assertNotIn((22, 0), getattr(sensor, 'g_offh_destr_instances', {}))

    def test_reachable_roof_deck_is_not_removed_from_the_approach_sample(self):
        with self.scene(registered=True) as f:
            top = max(box[4] for box in
                sensor._destructible_catalog['resources'][HOUSE.lower()]['boxes'])
            start, end = _Vector(0, top+3, 4), _Vector(0, top-5, 4)
            hit = f.bigworld.wg_collideSegment(1, start, end, 0)
            before = len(f.rays)
            self.assertIs(hit, sensor.planning_support_below_soft_roof(
                1, start, end, hit, top+0.1, 0.0, f.descriptor,
                kinetic_speed=20.0, recast_budget=[24]))
            self.assertEqual(before, len(f.rays))

    def test_reverse_and_forward_planning_ignore_the_same_confirmed_structure(self):
        with self.scene(health=30.0, speed_cap=10.0) as f:
            forward = f.battle._direction_probe((0, 0, 0), 0, 0.0, f.descriptor, 4.0)
            reverse = f.battle._direction_probe((0, 0, 0), 0, -0.01, f.descriptor, 4.0)
            self.assertTrue(forward['clear'], forward)
            self.assertTrue(reverse['clear'], reverse)

    def test_real_driver_requests_forward_motion_before_contact_at_low_fps(self):
        from gui.mods.offline_lan_0922.ai.driver import LocalDriver
        for dt in (1.0 / 60.0, 1.0 / 15.0, 0.2):
            with self.subTest(dt=dt), self.scene() as f:
                samples = []
                def clear(yaw, maximum_distance=4.0):
                    sample = f.battle._direction_probe(
                        (0, 0, 0), yaw, 0.0, f.descriptor, maximum_distance)
                    samples.append(sample)
                    return bool(sample.get('clear') and not sample.get('deferred'))
                command = LocalDriver().drive(
                    11, 0, (0, 0, 0), 0.0, 0.0, dt,
                    (0, 0, 20), (), clear, stop_at_target=False)
                self.assertGreater(command['throttle'], 0.0, command)
                self.assertEqual(0.0, command['target_yaw'])
                self.assertTrue(samples)
                self.assertTrue(samples[0]['clear'], samples)


if __name__ == '__main__':
    unittest.main()
