import importlib.util
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODS = ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


motion = _load('offline_battle_motion',
               MODS / 'offline_battle_2312' / 'motion.py')


class _Chassis(object):
    rotationSpeed = math.radians(42.0)


class _Descriptor(object):
    """Shaped like a 2.3.1.2 VehicleDescr for an MS-1."""

    chassis = _Chassis()
    physics = {
        'weight': 5730.0,
        'enginePower': 33075.0,
        'speedLimits': (33.0 / 3.6, 12.0 / 3.6),
        'terrainResistance': (1.1, 1.4, 2.6),
        'specificFriction': 0.6867,
        'brakeForce': 0.0,
        'trackCenterOffset': 1.1,
        'minPlaneNormalY': math.cos(math.radians(25.0)),
    }


def _params():
    return motion.derive_params(_Descriptor())


def _run(throttle, seconds, params=None, slope=0.0, steering=False):
    params = params or _params()
    speed = 0.0
    step = 0.02
    for _unused in range(int(seconds / step)):
        speed = motion.longitudinal_step(params, speed, throttle, steering,
                                         slope, step)
    return speed


class DeriveParamsTests(unittest.TestCase):
    def test_reads_the_descriptor(self):
        params = _params()
        self.assertAlmostEqual(params['mass'], 5730.0)
        self.assertAlmostEqual(params['powerW'], 33075.0)
        self.assertAlmostEqual(params['speedFwd'], 33.0 / 3.6)
        self.assertAlmostEqual(params['speedBwd'], 12.0 / 3.6)
        self.assertAlmostEqual(params['rotSpd'], math.radians(42.0))

    def test_zero_brake_force_keeps_the_track_grip_fallback(self):
        params = _params()
        self.assertAlmostEqual(params['brakeDecel'],
                               motion.COHESION * motion.GRAVITY)


class LongitudinalTests(unittest.TestCase):
    def test_accelerates_forward_and_settles_under_the_speed_limit(self):
        params = _params()
        speed = _run(1, 20.0, params)
        self.assertGreater(speed, 1.0)
        self.assertLessEqual(speed, params['speedFwd'] * 1.05)

    def test_reverse_is_slower_than_forward(self):
        params = _params()
        forward = _run(1, 20.0, params)
        backward = abs(_run(-1, 20.0, params))
        self.assertGreater(forward, backward)
        self.assertLessEqual(backward, params['speedBwd'] * 1.05)

    def test_coasting_decays_to_a_stop(self):
        params = _params()
        speed = _run(1, 10.0, params)
        step = 0.02
        for _unused in range(int(20.0 / step)):
            speed = motion.longitudinal_step(params, speed, 0, False, 0.0,
                                             step)
        self.assertAlmostEqual(speed, 0.0, places=2)

    def test_a_steep_slope_defeats_the_drive(self):
        params = _params()
        gentle = _run(1, 12.0, params, slope=math.radians(-5.0))
        steep = _run(1, 12.0, params, slope=math.radians(-40.0))
        self.assertGreater(gentle, 0.5)
        self.assertLess(steep, gentle)

    def test_airborne_keeps_speed(self):
        params = _params()
        self.assertAlmostEqual(
            motion.longitudinal_step(params, 5.0, 1, False, 0.0, 0.02,
                                     airborne=True), 5.0)

    def test_parked_holds_on_a_moderate_slope(self):
        params = _params()
        speed = 0.0
        for _unused in range(200):
            speed = motion.longitudinal_step(params, speed, 0, False,
                                             math.radians(13.0), 0.02)
        self.assertEqual(speed, 0.0)

    def test_coasting_glides_then_settles_on_a_moderate_slope(self):
        params = _params()
        speed = 6.0
        for _unused in range(200):
            speed = motion.longitudinal_step(params, speed, 0, False,
                                             math.radians(13.0), 0.02)
        self.assertGreater(speed, 2.0)
        for _unused in range(800):
            speed = motion.longitudinal_step(params, speed, 0, False,
                                             math.radians(13.0), 0.02)
        self.assertEqual(speed, 0.0)

    def test_a_steep_descent_keeps_the_hull_rolling(self):
        params = _params()
        speed = 6.0
        for _unused in range(1000):
            speed = motion.longitudinal_step(params, speed, 0, False,
                                             math.radians(20.0), 0.02)
        self.assertGreater(speed, 5.0)

    def test_the_handbrake_stops_and_holds_downhill(self):
        params = _params()
        speed = 6.0
        for _unused in range(400):
            speed = motion.longitudinal_step(params, speed, 0, False,
                                             math.radians(13.0), 0.02,
                                             False, 0, True)
        self.assertEqual(speed, 0.0)


class TraverseTests(unittest.TestCase):
    def test_traverse_builds_towards_the_chassis_rate(self):
        params = _params()
        omega = 0.0
        for _unused in range(200):
            omega = motion.traverse_step(params, omega, 1, 0.0, 0.02)
        self.assertGreater(omega, 0.0)
        self.assertLessEqual(omega, params['rotSpd'] * 1.01)

    def test_no_steer_input_decays_the_rate(self):
        params = _params()
        omega = params['rotSpd']
        for _unused in range(200):
            omega = motion.traverse_step(params, omega, 0, 0.0, 0.02)
        self.assertAlmostEqual(omega, 0.0, places=3)

    def test_track_scroll_follows_motion(self):
        params = _params()
        left, right = motion.track_scroll(params, 3.0, 0.0)
        self.assertAlmostEqual(left, right)
        self.assertGreater(left, 0.0)
        left, right = motion.track_scroll(params, 0.0, 1.0)
        self.assertNotAlmostEqual(left, right)



class CopyCompletenessTests(unittest.TestCase):
    """The law is a copy; a partial copy is how the slide model went missing."""

    SOURCE = Path('/Users/peng/wot-offline-2311-probe/0.9.22/src/res/scripts/'
                  'client/gui/mods/offline_lan_0922/vehicle_physics.py')

    def _names(self, text):
        import re
        return set(re.findall(r'^def (\w+)', text, re.M))

    def _constants(self, text):
        import re
        return set(re.findall(r'^([A-Z_][A-Z_0-9]*)\s*=', text, re.M))

    def test_every_function_of_the_source_is_here(self):
        if not self.SOURCE.exists():
            self.skipTest('the 0.9.22 source is not present')
        source = self.SOURCE.read_text().expandtabs(4)
        mine = (MODS / 'offline_battle_2312' / 'motion.py').read_text()
        self.assertEqual(self._names(source) - self._names(mine), set())

    def test_every_constant_of_the_source_is_here(self):
        if not self.SOURCE.exists():
            self.skipTest('the 0.9.22 source is not present')
        source = self.SOURCE.read_text().expandtabs(4)
        mine = (MODS / 'offline_battle_2312' / 'motion.py').read_text()
        self.assertEqual(self._constants(source) - self._constants(mine),
                         set())

if __name__ == '__main__':
    unittest.main()
