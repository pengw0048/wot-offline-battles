from pathlib import Path
import math
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = (
    ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import tank_collision


def _tank(tank_id, x, z, mass=25000.0, vx=0.0, vz=0.0, y=0.0,
          yaw=0.0, shape=(1.5, 3.5, -0.8, 2.0),
          contact_armor=100.0, spall=1.0, ramming_bonus=0.0,
          team=None):
    result = {
        'id': tank_id,
        'x': x,
        'y': y,
        'z': z,
        'yaw': yaw,
        'mass': mass,
        'vx': vx,
        'vz': vz,
        'shape': shape,
        'contact_armor': contact_armor,
        'ram_profile': {
            'spall_coefficient': spall,
            'ramming_bonus': ramming_bonus,
        },
        'alive': True,
    }
    if team is not None:
        result['team'] = team
    return result


class _Vector(object):

    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]


class _HitTester(object):

    def __init__(self, minimum, maximum):
        # Exact #1513 bbox exposes min, max and a third derived value.
        self.bbox = (minimum, maximum, None)


class _UnloadedHitTester(object):

    def __init__(self):
        self.bbox = None
        self.load_calls = 0

    def loadBspModel(self):
        self.load_calls += 1


class _Strict1513Component(object):
    """Attribute-only stand-in for #1513's ``NoLegacyStuff`` mixin."""

    def __init__(self, **values):
        self.__dict__.update(values)

    def _forbidden(self, *unused_args, **unused_kwargs):
        raise AssertionError('Operation is not allowed')

    get = _forbidden
    __contains__ = _forbidden
    __getitem__ = _forbidden
    __iter__ = _forbidden
    items = _forbidden
    keys = _forbidden
    values = _forbidden


class _Descriptor(object):

    def __init__(self):
        self.chassis = {
            'hitTester': _HitTester(
                _Vector(-1.7, -0.6, -3.2),
                _Vector(1.7, 0.8, 3.2)),
            'hullPosition': _Vector(0.0, 0.7, 0.0),
        }
        self.hull = {
            'hitTester': _HitTester(
                _Vector(-1.2, -0.2, -2.0),
                _Vector(1.2, 1.5, 2.0)),
        }


class TankCollisionTests(unittest.TestCase):

    def test_pitched_body_contains_its_world_space_front_contact(self):
        body = _tank(
            1, 0.0, 0.0, shape=(1.5, 3.5, -0.8, 2.0))
        body.update(pitch=0.30, roll=-0.12)
        axes = tank_collision.pose_axes(
            body['yaw'], body['pitch'], body['roll'])
        local = (0.0, 1.0, body['shape'][1])
        point = tuple(
            body[name] + sum(
                local[row] * axes[row][index] for row in range(3))
            for index, name in enumerate(('x', 'y', 'z')))

        self.assertTrue(tank_collision.body_contains_point(body, point))
        self.assertFalse(tank_collision.body_contains_point(
            dict(body, pitch=0.0, roll=0.0), point))

    def test_spatial_index_returns_nearby_cells_without_distant_tanks(self):
        bodies = {
            1: {'position': (0.0, 0.0, 0.0)},
            2: {'position': (7.0, 0.0, 2.0)},
            3: {'position': (200.0, 0.0, 200.0)},
        }

        index = tank_collision.build_spatial_index(bodies, 12.0)
        nearby = tank_collision.nearby_ids(index, 0.0, 0.0)

        self.assertEqual({1, 2}, set(nearby))

    def test_collision_cell_size_keeps_every_overlapping_pair_nearby(self):
        shape = (2.5, 5.0, -1.0, 2.5)
        radius = math.hypot(shape[0], shape[1])
        bodies = {
            1: {'position': (0.0, 0.0, 0.0)},
            2: {'position': (8.0, 0.0, 0.0)},
        }

        index = tank_collision.build_spatial_index(
            bodies, radius * 2.0 + 4.0)

        self.assertIn(2, tank_collision.nearby_ids(index, 0.0, 0.0))

    def test_shape_uses_chassis_bbox_and_mounted_hull_height(self):
        shape = tank_collision.chassis_shape(_Descriptor())

        self.assertEqual((1.7, 3.2, -0.6, 2.2), shape)

    def test_shape_reads_native_1513_components_as_attributes(self):
        descriptor = _Strict1513Component(
            chassis=_Strict1513Component(
                hitTester=_HitTester(
                    _Vector(-1.7, -0.6, -3.2),
                    _Vector(1.7, 0.8, 3.2)),
                hullPosition=_Vector(0.0, 0.7, 0.0)),
            hull=_Strict1513Component(
                hitTester=_HitTester(
                    _Vector(-1.2, -0.2, -2.0),
                    _Vector(1.2, 1.5, 2.0))))

        shape = tank_collision.chassis_shape(descriptor)

        self.assertEqual((1.7, 3.2, -0.6, 2.2), shape)

    def test_shape_requires_owner_to_load_bbox_before_geometry_read(self):
        tester = _UnloadedHitTester()
        descriptor = _Strict1513Component(
            chassis=_Strict1513Component(hitTester=tester),
            hull=_Strict1513Component(hitTester=tester))

        with self.assertRaisesRegex(RuntimeError, 'bbox is unavailable'):
            tank_collision.chassis_shape(descriptor)

        self.assertEqual(0, tester.load_calls)

    def test_shape_rejects_missing_descriptor_instead_of_default_body(self):
        with self.assertRaisesRegex(
                RuntimeError, 'vehicle descriptor is unavailable'):
            tank_collision.chassis_shape(None)

    def test_axis_aligned_obb_reports_smallest_translation(self):
        shape = (1.5, 3.0, -0.5, 2.0)

        contact = tank_collision.obb_contact(
            0.0, 0.0, 0.0, shape,
            2.5, 0.0, 0.0, shape)

        self.assertAlmostEqual(-1.0, contact[0])
        self.assertAlmostEqual(0.0, contact[1])
        self.assertAlmostEqual(0.5, contact[2])

    def test_yaw_aware_obb_rejects_separated_rotated_boxes(self):
        shape = (0.8, 3.5, -0.5, 2.0)

        contact = tank_collision.obb_contact(
            0.0, 0.0, math.radians(45.0), shape,
            3.3, 0.0, math.radians(45.0), shape)

        self.assertIsNone(contact)

    def test_resolver_does_not_block_visually_separated_rotated_tanks(self):
        shape = (0.8, 3.5, -0.5, 2.0)
        first = _tank(
            1, 0.0, 0.0, yaw=math.radians(45.0), shape=shape)
        second = _tank(
            2, 3.3, 0.0, yaw=math.radians(45.0), shape=shape)

        result = tank_collision.resolve_tank(first, (second,))

        self.assertEqual((0.0, 0.0), result['correction'])
        self.assertEqual((0.0, 0.0), result['delta_velocity'])

    def test_rotated_corner_overlap_is_detected(self):
        shape = (1.5, 3.0, -0.5, 2.0)

        contact = tank_collision.obb_contact(
            0.0, 0.0, math.radians(25.0), shape,
            2.2, 1.0, math.radians(-20.0), shape)

        self.assertIsNotNone(contact)
        self.assertGreater(contact[2], 0.0)

    def test_vertical_overlap_uses_descriptor_intervals(self):
        lower = (1.5, 3.0, -0.5, 2.0)
        upper = (1.5, 3.0, -1.0, 1.0)

        self.assertTrue(tank_collision.vertical_overlap(
            0.0, lower, 2.9, upper))
        self.assertFalse(tank_collision.vertical_overlap(
            0.0, lower, 3.02, upper))

    def test_support_rise_respects_tick_climb_hard_cap_and_slop(self):
        self.assertFalse(tank_collision.support_rise_is_obstacle(
            0.0, 0.66, 0.65))
        self.assertTrue(tank_collision.support_rise_is_obstacle(
            0.0, 0.68, 0.65))
        self.assertFalse(tank_collision.support_rise_is_obstacle(
            0.0, 0.86, 2.5))
        self.assertTrue(tank_collision.support_rise_is_obstacle(
            0.0, 0.88, 2.5))
        self.assertFalse(tank_collision.support_rise_is_obstacle(
            0.0, None, 0.65))

    def test_pair_response_returns_mass_weighted_reciprocal_correction(self):
        response = tank_collision.pair_response(
            (1.0, 0.0, 0.6), 1.0 / 60.0, 1.0 / 20.0,
            (0.0, 0.0), (0.0, 0.0), slop=0.0, percent=1.0)

        self.assertAlmostEqual(0.15, response[0])
        self.assertAlmostEqual(-0.45, response[4])

    def test_spawn_overlap_is_separated_without_blocking_movement(self):
        first = _tank(1, 0.0, 0.0)
        second = _tank(2, 0.8, 0.0)

        result = tank_collision.resolve_tank(first, (second,))

        self.assertNotEqual((0.0, 0.0), result['correction'])
        self.assertEqual((0.0, 0.0), result['delta_velocity'])
        self.assertNotIn('blocked', result)

    def test_coincident_centres_receive_reciprocal_owner_corrections(self):
        first = _tank(1, 0.0, 0.0)
        second = _tank(2, 0.0, 0.0)

        first_result = tank_collision.resolve_tank(first, (second,))
        second_result = tank_collision.resolve_tank(second, (first,))

        self.assertGreater(first_result['correction'][0], 0.0)
        self.assertLess(second_result['correction'][0], 0.0)
        self.assertAlmostEqual(
            first_result['correction'][0],
            -second_result['correction'][0])
        self.assertEqual((0.0, 0.0), first_result['delta_velocity'])
        self.assertEqual((0.0, 0.0), second_result['delta_velocity'])

    def test_mass_weighted_separation_moves_light_tank_farther(self):
        heavy = _tank(1, 0.0, 0.0, mass=60000.0)
        light = _tank(2, 0.8, 0.0, mass=10000.0)

        heavy_result = tank_collision.resolve_tank(heavy, (light,))
        light_result = tank_collision.resolve_tank(light, (heavy,))
        heavy_distance = math.hypot(*heavy_result['correction'])
        light_distance = math.hypot(*light_result['correction'])

        self.assertGreater(light_distance, heavy_distance * 5.9)

    def test_vertical_intervals_ignore_hulls_on_different_levels(self):
        lower = _tank(1, 0.0, 0.0, y=0.0, vx=4.0)
        upper = _tank(2, 0.8, 0.0, y=3.01)

        result = tank_collision.resolve_tank(lower, (upper,), now=10.0)

        self.assertEqual((0.0, 0.0), result['correction'])
        self.assertEqual((0.0, 0.0), result['delta_velocity'])
        self.assertEqual((), result['ram_events'])

    def test_impulse_is_applied_only_when_hulls_are_approaching(self):
        # Normal points from the other hull toward self (-x).  Positive self vx
        # therefore approaches; negative self vx separates.
        approaching = _tank(1, 0.0, 0.0, vx=8.0)
        separating = _tank(1, 0.0, 0.0, vx=-8.0)
        other = _tank(2, 0.8, 0.0)

        approach_result = tank_collision.resolve_tank(approaching, (other,))
        separate_result = tank_collision.resolve_tank(separating, (other,))

        self.assertLess(approach_result['delta_velocity'][0], 0.0)
        self.assertEqual((0.0, 0.0), separate_result['delta_velocity'])

    def test_friendly_hulls_block_without_dealing_ram_damage(self):
        first = _tank(
            1, 0.0, 0.0, mass=25000.0, vx=10.0, team=1)
        teammate = _tank(
            2, 0.8, 0.0, mass=30000.0, team=1)

        result = tank_collision.resolve_tank(
            first, (teammate,), now=10.0)

        self.assertNotEqual((0.0, 0.0), result['correction'])
        self.assertNotEqual((0.0, 0.0), result['delta_velocity'])
        self.assertEqual((), result['ram_events'])
        self.assertEqual(frozenset(), result['contacts'])

    def test_enemy_hulls_still_deal_ram_damage(self):
        first = _tank(
            1, 0.0, 0.0, mass=25000.0, vx=10.0, team=1)
        enemy = _tank(
            2, 0.8, 0.0, mass=30000.0, team=2)

        result = tank_collision.resolve_tank(
            first, (enemy,), now=10.0)

        self.assertEqual(1, len(result['ram_events']))

    def test_contact_episode_blocks_replay_but_separation_rearms_immediately(self):
        heavy = _tank(9, 0.0, 0.0, mass=60000.0, vx=10.0)
        light = _tank(4, 0.8, 0.0, mass=10000.0)

        first = tank_collision.resolve_tank(heavy, (light,), now=10.0)
        event = first['ram_events'][0]

        self.assertEqual((4, 9), event['pair'])
        self.assertEqual(
            tank_collision.ram_damage(
                10.0, 60000.0, 10000.0, 100.0, 100.0),
            (event['damage_to_other'], event['damage_to_self']))
        self.assertGreater(event['damage_to_other'], event['damage_to_self'])

        cooling_down = tank_collision.resolve_tank(
            heavy, (light,), now=10.5, ram_cooldowns=first['cooldowns'],
            active_ram_contacts=first['contacts'])
        still_overlapping = tank_collision.resolve_tank(
            heavy, (light,), now=10.76, ram_cooldowns=first['cooldowns'],
            active_ram_contacts=first['contacts'])
        harmless_overlap = tank_collision.resolve_tank(
            dict(heavy, vx=2.0), (light,), now=10.8,
            ram_cooldowns=first['cooldowns'],
            active_ram_contacts=still_overlapping['contacts'])
        reaccelerated_overlap = tank_collision.resolve_tank(
            heavy, (light,), now=11.0,
            ram_cooldowns=first['cooldowns'],
            active_ram_contacts=harmless_overlap['contacts'])
        separated_light = dict(light, x=20.0)
        separated = tank_collision.resolve_tank(
            heavy, (separated_light,), now=10.05,
            ram_cooldowns=first['cooldowns'],
            active_ram_contacts=first['contacts'])
        ready_again = tank_collision.resolve_tank(
            heavy, (light,), now=10.1, ram_cooldowns=first['cooldowns'],
            active_ram_contacts=separated['contacts'])

        self.assertEqual((), cooling_down['ram_events'])
        self.assertEqual((), still_overlapping['ram_events'])
        self.assertEqual(first['contacts'], harmless_overlap['contacts'])
        self.assertEqual((), reaccelerated_overlap['ram_events'])
        self.assertEqual(frozenset(), separated['contacts'])
        self.assertEqual(1, len(ready_again['ram_events']))

    def test_harmless_touch_does_not_consume_a_later_high_speed_ram(self):
        light = _tank(4, 0.0, 0.0, mass=10000.0, vx=2.0)
        heavy = _tank(9, 0.8, 0.0, mass=60000.0)

        touching = tank_collision.resolve_tank(
            light, (heavy,), now=10.0)
        light['vx'] = 10.0
        impact = tank_collision.resolve_tank(
            light, (heavy,), now=10.1,
            ram_cooldowns=touching['cooldowns'],
            active_ram_contacts=touching['contacts'])

        self.assertEqual((), touching['ram_events'])
        self.assertEqual(frozenset(), touching['contacts'])
        self.assertEqual(1, len(impact['ram_events']))
        self.assertGreater(impact['ram_events'][0]['damage_to_other'], 0)
        self.assertGreater(impact['ram_events'][0]['damage_to_self'], 0)

    def test_light_into_heavy_diagnostic_preserves_zero_target_damage(self):
        light = _tank(4, 0.0, 0.0, mass=10000.0, vx=4.5)
        heavy = _tank(9, 0.8, 0.0, mass=60000.0)
        light['vehicle'] = 'ussr:T-50'
        heavy['vehicle'] = 'germany:Maus'

        result = tank_collision.resolve_tank(light, (heavy,), now=10.0)
        event = result['ram_events'][0]

        self.assertEqual((0, 48), (
            event['damage_to_other'], event['damage_to_self']))
        self.assertEqual(('ussr:T-50', 'germany:Maus'), (
            event['self_vehicle'], event['other_vehicle']))
        self.assertEqual((10000.0, 60000.0), (
            event['mass_self'], event['mass_other']))
        self.assertEqual(((4.5, 0.0), (0.0, 0.0)), (
            event['velocity_self'], event['velocity_other']))
        self.assertEqual((-1.0, 0.0), event['contact_normal'])
        self.assertGreater(event['contact_penetration'], 0.0)
        self.assertEqual(4.5, event['closing_speed'])

    def test_ram_uses_normal_closing_speed_not_tangential_scrape(self):
        light = _tank(
            4, 0.0, 0.0, mass=10000.0, vx=3.0, vz=30.0)
        heavy = _tank(9, 2.9, 0.0, mass=60000.0)

        result = tank_collision.resolve_tank(light, (heavy,), now=10.0)

        event = result['ram_events'][0]
        self.assertAlmostEqual(math.hypot(3.0, 30.0),
                               event['relative_speed'])
        self.assertEqual(3.0, event['closing_speed'])
        self.assertEqual(3.0, event['impact_speed'])
        self.assertEqual(
            tank_collision.ram_damage(
                3.0, 10000.0, 60000.0, 100.0, 100.0),
            (event['damage_to_other'], event['damage_to_self']))
        self.assertGreater(event['damage_to_self'], 0)

    def test_ram_ignores_vertical_relative_speed_for_hull_damage(self):
        falling = _tank(
            4, 0.0, 0.0, mass=10000.0, vx=3.0, vz=0.0)
        falling['vy'] = -12.0
        heavy = _tank(9, 0.8, 0.0, mass=60000.0)

        event = tank_collision.resolve_tank(
            falling, (heavy,), now=10.0)['ram_events'][0]

        self.assertAlmostEqual(math.hypot(3.0, 12.0),
                               event['relative_speed'])
        self.assertEqual(3.0, event['impact_speed'])
        self.assertEqual(
            tank_collision.ram_damage(
                3.0, 10000.0, 60000.0, 100.0, 100.0),
            (event['damage_to_other'], event['damage_to_self']))
        self.assertEqual(-12.0, event['velocity_y_self'])
        self.assertEqual(0.0, event['velocity_y_other'])

    def test_high_speed_side_scrape_does_not_create_a_ram_event(self):
        light = _tank(
            4, 0.0, 0.0, mass=10000.0, vx=0.2, vz=30.0)
        heavy = _tank(9, 2.99, 0.0, mass=60000.0)

        result = tank_collision.resolve_tank(light, (heavy,), now=10.0)

        self.assertEqual((), result['ram_events'])

    def test_deep_front_overlap_keeps_the_first_impact_normal(self):
        first = _tank(
            4, 0.0, 0.0, mass=25000.0, vz=10.0,
            contact_armor=None)
        second = _tank(
            9, 0.0, 0.5, mass=30000.0,
            contact_armor=None)
        probes = []

        def probe(owner, other, contact):
            probes.append((owner['id'], other['id'], contact))
            return 80.0, 100.0

        result = tank_collision.resolve_tank(
            first, (second,), now=10.0, contact_armor_probe=probe)

        self.assertEqual(1, len(probes))
        self.assertAlmostEqual(0.0, probes[0][2][0])
        self.assertAlmostEqual(-1.0, probes[0][2][1])
        self.assertEqual(1, len(result['ram_events']))
        event = result['ram_events'][0]
        self.assertEqual((0.0, -1.0), event['contact_normal'])
        self.assertEqual(10.0, event['closing_speed'])

    def test_deep_overlap_while_separating_does_not_rewind_through_tank(self):
        first = _tank(
            4, 0.0, 0.0, mass=25000.0, vz=-10.0)
        second = _tank(
            9, 0.0, 0.5, mass=30000.0)

        result = tank_collision.resolve_tank(
            first, (second,), now=10.0)

        self.assertEqual((), result['ram_events'])

    def test_head_on_diagnostic_records_both_moving_bodies(self):
        first = _tank(4, 0.0, 0.0, mass=25000.0, vx=6.0)
        second = _tank(9, 0.8, 0.0, mass=30000.0, vx=-4.0)
        first['vehicle'] = 'ussr:T-34'
        second['vehicle'] = 'germany:PzV'

        result = tank_collision.resolve_tank(first, (second,), now=10.0)
        event = result['ram_events'][0]

        self.assertEqual(10.0, event['closing_speed'])
        self.assertEqual(((6.0, 0.0), (-4.0, 0.0)), (
            event['velocity_self'], event['velocity_other']))
        self.assertEqual((first['yaw'], second['yaw']), (
            event['yaw_self'], event['yaw_other']))
        self.assertEqual((first['shape'], second['shape']), (
            event['shape_self'], event['shape_other']))
        self.assertGreater(event['damage_to_other'], 0)
        self.assertGreater(event['damage_to_self'], 0)

    def test_native_contact_armor_probe_fills_only_missing_contact_inputs(self):
        first = _tank(
            4, 0.0, 0.0, mass=25000.0, vx=10.0,
            contact_armor=None)
        second = _tank(
            9, 0.8, 0.0, mass=30000.0, contact_armor=None)
        calls = []

        def probe(owner, other, contact):
            calls.append((owner['id'], other['id'], contact))
            return 45.0, 80.0

        result = tank_collision.resolve_tank(
            first, (second,), now=10.0, contact_armor_probe=probe)

        self.assertEqual(1, len(calls))
        self.assertEqual((45.0, 80.0), (
            result['ram_events'][0]['armor_self'],
            result['ram_events'][0]['armor_other']))
        self.assertGreater(result['ram_events'][0]['damage_to_other'], 0)
        self.assertGreater(result['ram_events'][0]['damage_to_self'], 0)

    def test_documented_kinetic_formula_and_inverse_mass_distribution(self):
        heavy_owner = tank_collision.ram_damage(
            10.0, 75000.0, 25000.0, 0.0, 0.0)
        light_owner = tank_collision.ram_damage(
            10.0, 25000.0, 75000.0, 0.0, 0.0)

        self.assertEqual((468, 156), heavy_owner)
        self.assertEqual((156, 468), light_owner)
        self.assertEqual(heavy_owner, tuple(reversed(light_owner)))

    def test_documented_he_reduction_uses_1_1_and_spall_liner(self):
        damage = tank_collision.ram_damage(
            10.0, 75000.0, 25000.0,
            100.0, 200.0, spall_self=1.5, spall_other=1.0)

        self.assertEqual((413, 115), damage)

    def test_controlled_impact_modifies_final_damage_only_while_moving(self):
        stationary = tank_collision.ram_damage(
            10.0, 50000.0, 50000.0, 100.0, 100.0,
            bonus_self=0.15, moving_self=False, moving_other=False)
        moving = tank_collision.ram_damage(
            10.0, 50000.0, 50000.0, 100.0, 100.0,
            bonus_self=0.15, moving_self=True, moving_other=False)

        self.assertEqual((285, 285), stationary)
        self.assertEqual((327, 242), moving)

    def test_ram_has_no_safe_speed_cap_or_mass_ratio_clamp(self):
        low_speed = tank_collision.ram_damage(
            1.0, 25000.0, 25000.0, 0.0, 0.0)
        extreme_ratio = tank_collision.ram_damage(
            20.0, 99000.0, 1000.0, 0.0, 0.0)

        self.assertEqual((1, 1), low_speed)
        self.assertEqual((2475, 25), extreme_ratio)

    def test_temporary_final_ram_damage_coefficient(self):
        # 2026-08-26 Type 62 -> KV-2 report: retain the measured contact
        # inputs, but apply the explicitly requested 0.25 final coefficient.
        damage_to_kv2, damage_to_type62 = tank_collision.ram_damage(
            5.77736, 21300.0, 53100.0, 30.0, 75.0)

        self.assertEqual((23, 102), (
            damage_to_kv2, damage_to_type62))

    def test_type62_v_k3002db_ram_is_owner_invariant(self):
        bot_owner = tank_collision.ram_damage(
            16.66827, 37290.0, 21000.0, 100.0, 100.0)
        player_owner = tank_collision.ram_damage(
            16.66827, 21000.0, 37290.0, 100.0, 100.0)

        self.assertEqual(bot_owner, tuple(reversed(player_owner)))

    def test_missing_contact_armor_separates_without_hp_damage(self):
        first = _tank(1, 0.0, 0.0, vx=10.0, contact_armor=None)
        second = _tank(2, 0.8, 0.0)

        result = tank_collision.resolve_tank(first, (second,), now=10.0)

        self.assertNotEqual((0.0, 0.0), result['correction'])
        self.assertEqual((), result['ram_events'])
        self.assertEqual('contact_armor_unavailable',
                         result['ram_diagnostics'][0]['reason'])
        self.assertTrue(result['ram_diagnostics'][0]['missing_self'])

    def test_ram_authority_rejects_retired_feel_tuning_patterns(self):
        collision_source = Path(tank_collision.__file__).read_text()
        physics_source = (CLIENT_SCRIPTS / 'gui' / 'mods' /
                          'offline_lan_0922' /
                          'vehicle_physics.py').read_text()
        lan_source = (CLIENT_SCRIPTS / 'gui' / 'mods' /
                      'offline_lan_0922' / 'lan_client.py').read_text()
        for retired in (
                'RAM_SAFE_SPEED', 'RAM_COOLDOWN', 'ratio_to_other',
                'ratio_to_self', 'min(350',
                'RAM_ARMOR_ABSORPTION_FACTOR = 1.3'):
            self.assertNotIn(retired, collision_source)
        self.assertNotIn("'ram_safe_speed'", physics_source)
        self.assertNotIn('min(int(damage_to_bot or 0), 500)', lan_source)
        self.assertNotIn('min(int(damage_to_target or 0), 500)', lan_source)

    def test_wreck_blocks_the_mover_without_moving_or_dealing_ram_damage(self):
        mover = _tank(1, 0.0, 0.0, mass=25000.0, vx=10.0)
        wreck = _tank(2, 0.8, 0.0, mass=10000.0)
        wreck['alive'] = False
        live = _tank(2, 0.8, 0.0, mass=10000.0)

        against_wreck = tank_collision.resolve_tank(
            mover, (wreck,), now=10.0)
        against_live = tank_collision.resolve_tank(
            mover, (live,), now=10.0)

        self.assertLess(against_wreck['correction'][0], 0.0)
        self.assertLess(against_wreck['delta_velocity'][0], 0.0)
        self.assertEqual((), against_wreck['ram_events'])
        # An immovable wreck takes the whole separation, so the mover is
        # pushed out farther than the lighter living hull pushes it.
        self.assertLess(
            against_wreck['correction'][0], against_live['correction'][0])


if __name__ == '__main__':
    unittest.main()
