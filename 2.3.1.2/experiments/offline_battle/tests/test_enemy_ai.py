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
_bot_control = types.ModuleType('gui.mods.offline_battle_2312.bot_control')
_bot_control.BATTLE_SEED = 20260816
sys.modules.setdefault('gui.mods.offline_battle_2312.bot_control',
                       _bot_control)
_ai_package = types.ModuleType('gui.mods.offline_battle_2312.ai')
sys.modules.setdefault('gui.mods.offline_battle_2312.ai', _ai_package)
_driver_spec = importlib.util.spec_from_file_location(
    'gui.mods.offline_battle_2312.ai.driver', PACKAGE / 'ai' / 'driver.py')
_driver = importlib.util.module_from_spec(_driver_spec)
sys.modules.setdefault('gui.mods.offline_battle_2312.ai.driver', _driver)
if sys.modules['gui.mods.offline_battle_2312.ai.driver'] is _driver:
    _driver_spec.loader.exec_module(_driver)

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

    def test_a_target_below_raises_the_client_pitch(self):
        """The client's gun pitch runs the other way: setRotateX(+pitch)
        aims the barrel down, so a target below is a positive pitch."""
        _yaw, pitch = enemy_ai.aim_angles((0.0, 10.0, 0.0), 0.0,
                                          (0.0, 0.0, 100.0))
        self.assertGreater(pitch, 0.0)

    def test_a_target_above_lowers_the_client_pitch(self):
        _yaw, pitch = enemy_ai.aim_angles((0.0, 0.0, 0.0), 0.0,
                                          (0.0, 10.0, 100.0))
        self.assertLess(pitch, 0.0)

    def test_the_pitch_matches_the_angle_it_must_shoot_at(self):
        _yaw, pitch = enemy_ai.aim_angles((0.0, 0.0, 0.0), 0.0,
                                          (0.0, 10.0, 10.0))
        self.assertAlmostEqual(pitch, -math.pi / 4.0)


class PitchLimitTests(unittest.TestCase):
    def test_a_pitch_below_the_limit_is_clamped(self):
        self.assertAlmostEqual(enemy_ai.clamp_pitch(-1.0, (-0.1, 0.3)), -0.1)

    def test_a_pitch_above_the_limit_is_clamped(self):
        self.assertAlmostEqual(enemy_ai.clamp_pitch(1.0, (-0.1, 0.3)), 0.3)

    def test_a_pitch_inside_the_limits_is_kept(self):
        self.assertAlmostEqual(enemy_ai.clamp_pitch(0.2, (-0.1, 0.3)), 0.2)

    def test_missing_limits_leave_the_pitch_alone(self):
        self.assertAlmostEqual(enemy_ai.clamp_pitch(0.2, None), 0.2)


class DispersionTests(unittest.TestCase):
    def test_the_same_shot_scatters_the_same_way(self):
        first = enemy_ai.dispersed_angles(7, 1, 3, 0.4, -0.05, 0.004)
        again = enemy_ai.dispersed_angles(7, 1, 3, 0.4, -0.05, 0.004)
        self.assertEqual(first, again)

    def test_the_next_shot_scatters_differently(self):
        first = enemy_ai.dispersed_angles(7, 1, 3, 0.4, -0.05, 0.004)
        other = enemy_ai.dispersed_angles(7, 1, 4, 0.4, -0.05, 0.004)
        self.assertNotEqual(first, other)

    def test_the_scatter_stays_near_the_aim(self):
        yaw, pitch = enemy_ai.dispersed_angles(7, 1, 3, 0.4, -0.05, 0.004)
        self.assertLess(abs(yaw - 0.4), 0.01)
        self.assertLess(abs(pitch - -0.05), 0.01)


if __name__ == '__main__':
    unittest.main()
