import importlib.util
import math
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODS = ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods'
PACKAGE = MODS / 'offline_battle_2312'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


combat_rules = _load('offline_battle_combat_rules',
                     PACKAGE / 'combat_rules.py')
for _name in ('gui', 'gui.mods', 'gui.mods.offline_battle_2312'):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules['gui.mods.offline_battle_2312.combat_rules'] = combat_rules
damage = _load('offline_battle_damage', PACKAGE / 'damage.py')


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


class _Shell(object):
    kind = 'ARMOR_PIERCING'
    caliber = 45
    damage = (50.0, 40.0)
    explosionRadius = 0.0


class _Shot(object):
    shell = _Shell()
    piercingPower = (51.0, 40.0)
    maxDistance = 720.0


def _median(low, high):
    return (low + high) * 0.5


class PenetrationTests(unittest.TestCase):
    def test_a_thin_plate_square_on_is_pierced(self):
        result, _effective, _pierce = combat_rules.penetration(
            _Shot(), 100.0, 20.0, 1.0, random_uniform=lambda a, b: 1.0)
        self.assertEqual(result, damage.PIERCED)

    def test_a_thick_plate_is_not_pierced(self):
        result, _effective, _pierce = combat_rules.penetration(
            _Shot(), 100.0, 200.0, 1.0, random_uniform=lambda a, b: 1.0)
        self.assertEqual(result, damage.NOT_PIERCED)

    def test_a_very_shallow_angle_ricochets(self):
        result, _effective, _pierce = combat_rules.penetration(
            _Shot(), 100.0, 40.0, math.cos(math.radians(80.0)),
            random_uniform=lambda a, b: 1.0)
        self.assertEqual(result, damage.RICOCHET)

    def test_penetration_falls_off_with_range(self):
        near = combat_rules.range_piercing(_Shot(), 100.0)
        far = combat_rules.range_piercing(_Shot(), 720.0)
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
