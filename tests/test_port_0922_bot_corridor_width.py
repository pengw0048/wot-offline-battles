"""Vehicle-specific static corridors, separate from moving-tank traffic."""

import math
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/res/scripts/client'))

from gui.mods.offline_lan_0922 import battle_runtime


class _Vector(object):
    def __init__(self, x, y, z):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]


def _descriptor(half_width, left_width=None):
    minimum = (-(half_width if left_width is None else left_width), 0.0, -3.0)
    maximum = (half_width, 1.1, 3.0)
    return types.SimpleNamespace(
        chassis=types.SimpleNamespace(
            hitTester=types.SimpleNamespace(bbox=(minimum, maximum, None)),
            hullPosition=(0.0, 0.6, 0.0)),
        hull=types.SimpleNamespace(hitTester=types.SimpleNamespace(
            bbox=((-1.0, 0.0, -2.5), (1.0, 1.4, 2.5), None))))


class BotCorridorWidthTests(unittest.TestCase):
    def _battle(self, wall=None):
        samples = []

        def collide(unused_space, start, end, unused_mask):
            if abs(start.y - end.y) > 0.01:
                return (_Vector(end.x, 0.0, end.z),)
            samples.append((start, end))
            if wall is not None and wall(start, end):
                return (end,)
            return None

        battle = object.__new__(battle_runtime.BattleRuntime)
        battle._runtime = types.SimpleNamespace(
            math=types.SimpleNamespace(Vector3=_Vector),
            bigworld=types.SimpleNamespace(wg_collideSegment=collide))
        battle._avatar = types.SimpleNamespace(spaceID=1)
        battle._water_depth = lambda unused_position: -1.0
        battle._destructibles = None
        return battle, samples

    def test_small_chassis_can_follow_a_passable_wallside_corridor(self):
        # The exact #1513 chassis resources for AMX 13 75, T-43, and Type 62
        # fit here; the former uniform +/-2.2 m lanes reached the wall.
        for half_width in (1.247930, 1.362500, 1.402969):
            with self.subTest(half_width=half_width):
                battle, samples = self._battle(
                    lambda start, end: abs(start.x) >= 2.0)
                with mock.patch.object(
                        battle_runtime.vehicle_physics, 'derive_params',
                        return_value={}):
                    result = battle._direction_probe(
                        (0.0, 0.0, 0.0), 0.0, 0.0,
                        _descriptor(half_width), 4.0)
                self.assertTrue(result['clear'])
                self.assertEqual(6, len(samples))
                self.assertEqual(
                    {-half_width, 0.0, half_width},
                    {start.x for start, unused_end in samples})
                self.assertEqual({4.0}, {end.z for unused_start, end in samples})

    def test_widest_stock_chassis_does_not_inherit_the_old_width_cap(self):
        # J24 Mi-To 130 tons extends beyond the former 2.2 m half-width.
        battle, unused_samples = self._battle(
            lambda start, end: start.x > 2.22)
        with mock.patch.object(
                battle_runtime.vehicle_physics, 'derive_params',
                return_value={}):
            result = battle._direction_probe(
                (0.0, 0.0, 0.0), 0.0, 0.0,
                _descriptor(2.239622116), 4.0)
        self.assertFalse(result['clear'])
        self.assertTrue(result['collision'])

    def test_rotated_probe_uses_the_same_asymmetric_chassis_envelope(self):
        battle, samples = self._battle()
        with mock.patch.object(
                battle_runtime.vehicle_physics, 'derive_params',
                return_value={}):
            result = battle._direction_probe(
                (10.0, 0.0, 20.0), math.pi / 2.0, -3.0,
                _descriptor(1.4, left_width=1.9), 4.0)
        self.assertTrue(result['clear'])
        self.assertEqual({-1.9, 0.0, 1.9}, {
            round(20.0 - start.z, 6) for start, unused_end in samples})
        self.assertTrue(all(abs(end.x - 14.0) < 1e-6
                            for unused_start, end in samples))

    def test_passive_contact_uses_projected_width_without_active_crush(self):
        battle, unused_samples = self._battle(
            lambda start, end: abs(start.x) >= 2.6)
        with mock.patch.object(
                battle_runtime.vehicle_physics, 'derive_params') as derive:
            result = battle._direction_probe(
                (0.0, 0.0, 0.0), 0.0, 1.0, None, 4.0,
                corridor_half_width=3.0)
        self.assertFalse(result['clear'])
        self.assertTrue(result['collision'])
        derive.assert_not_called()

    def test_missing_admitted_body_does_not_fabricate_a_width(self):
        battle, samples = self._battle()
        descriptor = _descriptor(1.4)
        descriptor.chassis.hitTester.bbox = None
        result = battle._direction_probe(
            (0.0, 0.0, 0.0), 0.0, 0.0, descriptor, 4.0)
        self.assertFalse(result['clear'])
        self.assertTrue(result['collision'])
        self.assertEqual([], samples)


if __name__ == '__main__':
    unittest.main()
