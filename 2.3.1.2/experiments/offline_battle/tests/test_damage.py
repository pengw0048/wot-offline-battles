import math
import types
import unittest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import package_stub

combat_rules = package_stub.load('combat_rules')
damage = package_stub.load('damage')


class _MatInfo(object):
    def __init__(self, armor, vehicle_damage_factor=1.0):
        self.armor = armor
        self.vehicleDamageFactor = vehicle_damage_factor


class _Collision(object):
    def __init__(self, dist, hit_angle_cos, mat_info, comp_name='hull'):
        self.dist = dist
        self.hitAngleCos = hit_angle_cos
        self.matInfo = mat_info
        self.compName = comp_name


class _ShellType(object):
    def __init__(self, name, explosion_radius=0.0):
        self.name = name
        self.explosionRadius = explosion_radius


class _Shell(object):
    """Shaped like a 2.3.1.2 Shell item."""

    def __init__(self, kind='ARMOR_PIERCING'):
        self.type = _ShellType(kind)
        self.caliber = 45
        self.armorDamage = (50.0, 40.0)
        self.deviceDamage = (25.0, 20.0)

    @property
    def kind(self):
        return self.type.name


class _Shot(object):
    def __init__(self, kind='ARMOR_PIERCING'):
        self.shell = _Shell(kind)
        self.piercingPower = (51.0, 40.0)
        self.maxDistance = 720.0


def _median(low, high):
    return (low + high) * 0.5


class LegacyShotTests(unittest.TestCase):
    def test_the_armour_damage_becomes_the_damage_the_law_reads(self):
        converted = damage.legacy_shot(_Shot())
        self.assertEqual(converted['shell']['damage'], (50.0, 40.0))
        self.assertEqual(converted['shell']['kind'], 'ARMOR_PIERCING')
        self.assertEqual(converted['piercingPower'], (51.0, 40.0))

    def test_every_high_explosive_name_reaches_the_he_branch(self):
        for kind in damage.HIGH_EXPLOSIVE_KINDS:
            converted = damage.legacy_shot(_Shot(kind))
            self.assertEqual(converted['shell']['kind'], 'HIGH_EXPLOSIVE')
            self.assertTrue(combat_rules.is_he(converted))

    def test_a_pierced_hit_rolls_real_damage(self):
        collisions = [_Collision(1.0, 1.0, _MatInfo(16.0))]
        _result, points = damage.resolve(_Shot(), 100.0, collisions,
                                         random_uniform=_median)
        self.assertEqual(points, 50)


class PenetrationTests(unittest.TestCase):
    def test_a_thin_plate_square_on_is_pierced(self):
        result, _effective, _pierce = combat_rules.penetration(
            damage.legacy_shot(_Shot()), 100.0, 20.0, 1.0,
            random_uniform=lambda a, b: 1.0)
        self.assertEqual(result, damage.PIERCED)

    def test_a_thick_plate_is_not_pierced(self):
        result, _effective, _pierce = combat_rules.penetration(
            damage.legacy_shot(_Shot()), 100.0, 200.0, 1.0,
            random_uniform=lambda a, b: 1.0)
        self.assertEqual(result, damage.NOT_PIERCED)

    def test_a_very_shallow_angle_ricochets(self):
        result, _effective, _pierce = combat_rules.penetration(
            damage.legacy_shot(_Shot()), 100.0, 40.0,
            math.cos(math.radians(80.0)), random_uniform=lambda a, b: 1.0)
        self.assertEqual(result, damage.RICOCHET)

    def test_penetration_falls_off_with_range(self):
        shot = damage.legacy_shot(_Shot())
        near = combat_rules.range_piercing(shot, 100.0)
        far = combat_rules.range_piercing(shot, 720.0)
        self.assertGreater(near, far)


class ResolveTests(unittest.TestCase):
    def test_a_track_absorbs_the_shell(self):
        collisions = [_Collision(1.0, 1.0, _MatInfo(20.0, 0.0), 'track')]
        result, points = damage.resolve(_Shot(), 100.0, collisions,
                                        random_uniform=_median)
        self.assertIsNone(result)
        self.assertEqual(points, 0)

    def test_a_pierced_hull_takes_the_rolled_damage(self):
        collisions = [_Collision(1.0, 1.0, _MatInfo(16.0))]
        result, points = damage.resolve(_Shot(), 100.0, collisions,
                                        random_uniform=_median)
        self.assertEqual(result, damage.PIERCED)
        self.assertEqual(points, 50)

    def test_a_bounced_shell_does_no_damage(self):
        collisions = [_Collision(1.0, 1.0, _MatInfo(300.0))]
        result, points = damage.resolve(_Shot(), 100.0, collisions,
                                        random_uniform=_median)
        self.assertEqual(result, damage.NOT_PIERCED)
        self.assertEqual(points, 0)

    def test_the_track_costs_penetration_before_the_hull(self):
        bare = [_Collision(1.0, 1.0, _MatInfo(40.0))]
        behind_track = [_Collision(0.5, 1.0, _MatInfo(20.0, 0.0), 'track'),
                        _Collision(1.0, 1.0, _MatInfo(40.0))]
        self.assertEqual(damage.resolve(_Shot(), 100.0, bare,
                                        random_uniform=_median)[0],
                         damage.PIERCED)
        self.assertEqual(damage.resolve(_Shot(), 100.0, behind_track,
                                        random_uniform=_median)[0],
                         damage.NOT_PIERCED)


class HitFlagTests(unittest.TestCase):
    def test_a_penetration_reports_a_pierced_hit(self):
        flags = damage.hit_flags(damage.PIERCED, False)
        self.assertTrue(flags & damage.HIT_PIERCED)
        self.assertFalse(flags & damage.HIT_VEHICLE_KILLED)

    def test_a_kill_is_reported(self):
        self.assertTrue(damage.hit_flags(damage.PIERCED, True) &
                        damage.HIT_VEHICLE_KILLED)

    def test_a_ricochet_is_reported(self):
        self.assertTrue(damage.hit_flags(damage.RICOCHET, False) &
                        damage.HIT_RICOCHET)


class _Extra(object):
    def __init__(self, name):
        self.name = name


class _DeviceMat(object):
    def __init__(self, name, chance=1.0):
        self.armor = 20.0
        self.vehicleDamageFactor = 0.0
        self.extra = _Extra(name)
        self.chanceToHitByProjectile = chance


class ModuleHitTests(unittest.TestCase):
    def test_a_device_material_can_be_critted(self):
        collisions = [_Collision(1.0, 1.0, _DeviceMat('engineHealth'))]
        self.assertEqual(damage.module_hits(collisions, lambda a, b: 0.5),
                         ['engineHealth'])

    def test_a_failed_saving_throw_leaves_the_device_alone(self):
        collisions = [_Collision(1.0, 1.0, _DeviceMat('engineHealth', 0.2))]
        self.assertEqual(damage.module_hits(collisions, lambda a, b: 0.5), [])

    def test_plain_armour_carries_no_device(self):
        collisions = [_Collision(1.0, 1.0, _MatInfo(20.0))]
        self.assertEqual(damage.module_hits(collisions, lambda a, b: 0.0), [])

    def test_one_device_is_reported_once(self):
        collisions = [_Collision(1.0, 1.0, _DeviceMat('ammoBayHealth')),
                      _Collision(2.0, 1.0, _DeviceMat('ammoBayHealth'))]
        self.assertEqual(damage.module_hits(collisions, lambda a, b: 0.0),
                         ['ammoBayHealth'])

    def test_a_missing_material_is_skipped(self):
        collisions = [_Collision(1.0, 1.0, None)]
        self.assertEqual(damage.module_hits(collisions, lambda a, b: 0.0), [])


class LawDeviceNameTests(unittest.TestCase):
    def test_the_chassis_becomes_the_two_tracks_the_law_knows(self):
        self.assertEqual(damage.law_devices(['chassisHealth']),
                         ['leftTrackHealth', 'rightTrackHealth'])

    def test_every_other_device_keeps_its_name(self):
        self.assertEqual(damage.law_devices(['engineHealth', 'gunHealth']),
                         ['engineHealth', 'gunHealth'])

    def test_a_crew_name_passes_through(self):
        self.assertEqual(damage.law_devices(['commanderHealth']),
                         ['commanderHealth'])

    def test_no_device_is_named_twice(self):
        self.assertEqual(
            damage.law_devices(['chassisHealth', 'leftTrackHealth']),
            ['leftTrackHealth', 'rightTrackHealth'])


class HullLocalPointTests(unittest.TestCase):
    def test_a_point_ahead_is_positive_z(self):
        local = damage.hull_local_point((0.0, 0.0, 0.0, 0.0),
                                        (0.0, 0.0, 5.0))
        self.assertAlmostEqual(local[0], 0.0)
        self.assertAlmostEqual(local[1], 5.0)

    def test_a_point_to_the_right_is_positive_x(self):
        local = damage.hull_local_point((0.0, 0.0, 0.0, 0.0),
                                        (3.0, 0.0, 0.0))
        self.assertAlmostEqual(local[0], 3.0)
        self.assertAlmostEqual(local[1], 0.0)

    def test_the_frame_follows_the_hull_yaw(self):
        local = damage.hull_local_point((0.0, 0.0, 0.0, math.pi / 2.0),
                                        (5.0, 0.0, 0.0))
        self.assertAlmostEqual(local[0], 0.0, places=6)
        self.assertAlmostEqual(local[1], 5.0)

    def test_the_hull_origin_is_the_origin(self):
        local = damage.hull_local_point((10.0, 2.0, -4.0, 1.0),
                                        (10.0, 2.0, -4.0))
        self.assertAlmostEqual(local[0], 0.0)
        self.assertAlmostEqual(local[1], 0.0)


class _Descriptor(object):
    def __init__(self):
        self.hull = types.SimpleNamespace(
            hitTester=types.SimpleNamespace(
                bbox=((-1.0, 0.0, -2.0), (1.0, 1.0, 2.0), 0)),
            turretPositions=[(0.0, 0.5, 0.2)])
        self.type = types.SimpleNamespace(
            crewRoles=(('commander',), ('driver',), ('gunner',)))
        self.chassis = types.SimpleNamespace()

    def __getattr__(self, name):
        raise AttributeError(name)


class InteriorHitTests(unittest.TestCase):
    def test_a_turret_hit_uses_the_turret_zone(self):
        _device, zone = damage.interior_hit(_Descriptor(),
                                            damage.TURRET_PART_INDEX,
                                            (0.0, 0.0), random_roll=0.5)
        self.assertEqual(zone, 'turret')

    def test_a_hit_ahead_of_the_ring_is_the_front_compartment(self):
        _device, zone = damage.interior_hit(_Descriptor(), 1, (0.0, 1.5),
                                            random_roll=0.5)
        self.assertEqual(zone, 'hullFront')

    def test_a_hit_behind_the_ring_is_the_rear_compartment(self):
        _device, zone = damage.interior_hit(_Descriptor(), 1, (0.0, -1.5),
                                            random_roll=0.5)
        self.assertEqual(zone, 'hullRear')

    def test_a_hit_on_the_flank_is_the_sponson(self):
        _device, zone = damage.interior_hit(_Descriptor(), 1, (0.95, 0.0),
                                            random_roll=0.5)
        self.assertEqual(zone, 'hullSide')

    def test_a_penetration_names_some_device(self):
        device, _zone = damage.interior_hit(_Descriptor(), 1, (0.0, 1.5),
                                            random_roll=0.5)
        self.assertTrue(device is None or device.endswith('Health'))


class CritFlagTests(unittest.TestCase):
    def test_a_track_reports_a_chassis_crit(self):
        self.assertTrue(damage.crit_flags(['leftTrackHealth']) &
                        damage.HIT_CHASSIS_DAMAGED)

    def test_the_client_chassis_device_reports_a_chassis_crit(self):
        self.assertTrue(damage.crit_flags(['chassisHealth']) &
                        damage.HIT_CHASSIS_DAMAGED)

    def test_a_gun_reports_a_gun_crit(self):
        self.assertTrue(damage.crit_flags(['gunHealth']) &
                        damage.HIT_GUN_DAMAGED)

    def test_anything_else_reports_a_device_crit(self):
        self.assertTrue(damage.crit_flags(['engineHealth']) &
                        damage.HIT_DEVICE_DAMAGED)

    def test_no_crit_reports_nothing(self):
        self.assertEqual(damage.crit_flags([]), 0)


class _Target(object):
    def __init__(self, vehicle_id, distance):
        self.id = vehicle_id
        self._distance = distance

    def collideSegmentExt(self, start, end):
        if self._distance is None:
            return None
        return [_Collision(self._distance, 1.0, _MatInfo(20.0))]


class NearestVehicleTests(unittest.TestCase):
    def test_the_closest_vehicle_wins(self):
        found = damage.nearest_vehicle(
            [_Target(2, 30.0), _Target(3, 10.0)], None, None)
        self.assertEqual(found[0].id, 3)
        self.assertAlmostEqual(found[1], 10.0)

    def test_an_empty_field_reports_nothing(self):
        self.assertIsNone(damage.nearest_vehicle([], None, None))

    def test_a_vehicle_out_of_the_segment_is_skipped(self):
        self.assertIsNone(damage.nearest_vehicle([_Target(2, None)], None,
                                                 None))


if __name__ == '__main__':
    unittest.main()
