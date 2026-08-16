import importlib.util
import math
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODS = ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods'
PACKAGE = MODS / 'offline_battle_2312'

for _name in ('gui', 'gui.mods', 'gui.mods.offline_battle_2312'):
    sys.modules.setdefault(_name, types.ModuleType(_name))
_projectiles = types.ModuleType('gui.mods.offline_battle_2312.projectiles')
sys.modules['gui.mods.offline_battle_2312.projectiles'] = _projectiles

_spec = importlib.util.spec_from_file_location(
    'offline_battle_enemy_ai', PACKAGE / 'enemy_ai.py')
enemy_ai = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(enemy_ai)


class AimTests(unittest.TestCase):
    def test_a_target_straight_ahead_needs_no_turret_turn(self):
        yaw, pitch = enemy_ai.aim_angles((0.0, 0.0, 0.0), 0.0,
                                         (0.0, 0.0, 100.0))
        self.assertAlmostEqual(yaw, 0.0)
        self.assertAlmostEqual(pitch, 0.0)

    def test_the_turret_angle_is_relative_to_the_hull(self):
        yaw, _pitch = enemy_ai.aim_angles((0.0, 0.0, 0.0), math.pi / 2.0,
                                          (0.0, 0.0, 100.0))
        self.assertAlmostEqual(yaw, -math.pi / 2.0)

    def test_a_target_to_the_right_turns_the_turret_right(self):
        yaw, _pitch = enemy_ai.aim_angles((0.0, 0.0, 0.0), 0.0,
                                          (100.0, 0.0, 0.0))
        self.assertAlmostEqual(yaw, math.pi / 2.0)

    def test_the_turret_angle_stays_inside_one_turn(self):
        yaw, _pitch = enemy_ai.aim_angles((0.0, 0.0, 0.0), -3.0,
                                          (0.0, 0.0, -100.0))
        self.assertLessEqual(abs(yaw), math.pi)

    def test_a_target_below_gives_a_negative_pitch(self):
        _yaw, pitch = enemy_ai.aim_angles((0.0, 10.0, 0.0), 0.0,
                                          (0.0, 0.0, 100.0))
        self.assertLess(pitch, 0.0)

    def test_a_target_above_gives_a_positive_pitch(self):
        _yaw, pitch = enemy_ai.aim_angles((0.0, 0.0, 0.0), 0.0,
                                          (0.0, 10.0, 100.0))
        self.assertGreater(pitch, 0.0)


class PitchLimitTests(unittest.TestCase):
    def test_a_pitch_below_the_limit_is_clamped(self):
        self.assertAlmostEqual(enemy_ai.clamp_pitch(-1.0, (-0.1, 0.3)), -0.1)

    def test_a_pitch_above_the_limit_is_clamped(self):
        self.assertAlmostEqual(enemy_ai.clamp_pitch(1.0, (-0.1, 0.3)), 0.3)

    def test_a_pitch_inside_the_limits_is_kept(self):
        self.assertAlmostEqual(enemy_ai.clamp_pitch(0.2, (-0.1, 0.3)), 0.2)

    def test_missing_limits_leave_the_pitch_alone(self):
        self.assertAlmostEqual(enemy_ai.clamp_pitch(0.2, None), 0.2)


if __name__ == '__main__':
    unittest.main()
