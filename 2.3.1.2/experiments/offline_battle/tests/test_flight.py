import math
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import package_stub

package_stub.load('combat_rules')
damage = package_stub.load('damage')
# An earlier test module may have parked an empty projectiles stand-in;
# flight binds the real one at import.
for _name in ('gui.mods.offline_battle_2312.projectile_runtime',
              'gui.mods.offline_battle_2312.projectiles',
              'gui.mods.offline_battle_2312.flight'):
    sys.modules.pop(_name, None)
package_stub.load('projectile_runtime')
package_stub.load('projectiles')
flight = package_stub.load('flight')


class _Vec(object):
    def __init__(self, *args):
        if len(args) == 1:
            source = args[0]
            args = ((source.x, source.y, source.z)
                    if hasattr(source, 'x') else tuple(source))
        self.x, self.y, self.z = (float(v) for v in args)

    def __sub__(self, other):
        return _Vec(self.x - other.x, self.y - other.y, self.z - other.z)

    @property
    def length(self):
        return math.sqrt(self.x * self.x + self.y * self.y +
                         self.z * self.z)


class _Terrain(object):
    def __init__(self, point, mat_kind):
        self.closestPoint = _Vec(point)
        self.matKind = mat_kind


class _Target(object):
    def __init__(self, dist):
        self.id = 7
        self._dist = dist

    def collideSegmentExt(self, start, end):
        return [damage.SegmentCollisionResultExt(self._dist, 1.0, None,
                                                 'hull')]


class _BigWorldStub(types.ModuleType):
    def __init__(self):
        types.ModuleType.__init__(self, 'BigWorld')
        self.now = 0.0
        self.terrain = None

    def time(self):
        return self.now

    def callback(self, delay, fn):
        return 1

    def cancelCallback(self, token):
        pass

    def wg_collideSegment(self, space_id, head, tail, mask):
        return self.terrain


class FlightChordTests(unittest.TestCase):
    def setUp(self):
        self._saved = {name: sys.modules.get(name)
                       for name in ('Math', 'BigWorld')}
        math_stub = types.ModuleType('Math')
        math_stub.Vector3 = _Vec
        sys.modules['Math'] = math_stub
        self.bigworld = _BigWorldStub()
        sys.modules['BigWorld'] = self.bigworld
        self.deck = flight.FlightDeck(1, lambda message: None)
        self.deck._manager = object()

    def tearDown(self):
        for name, module in self._saved.items():
            if module is None:
                del sys.modules[name]
            else:
                sys.modules[name] = module

    def _state(self, key='shot'):
        return {'key': key, 'launch_time': 0.0, 'distance': 5.0,
                'position': (9.0, 1.0, 0.0), 'elapsed': 0.4}

    def test_a_vehicle_in_the_chord_terminates_it(self):
        target = _Target(2.0)
        self.deck._meta['shot'] = (lambda: [target], None)
        result = self.deck._chord(self._state(), (0.0, 1.0, 0.0),
                                  (10.0, 1.0, 0.0), 0.4, 0.5)
        self.assertEqual(result['reason'], 'vehicle')
        self.assertAlmostEqual(result['fraction'], 0.2)
        hit = self.deck._hits['shot']
        self.assertIs(hit.vehicle, target)
        self.assertAlmostEqual(hit.travelled, 7.0)

    def test_nearer_terrain_wins_over_the_vehicle(self):
        self.bigworld.terrain = _Terrain((1.0, 1.0, 0.0), 3)
        self.deck._meta['shot'] = (lambda: [_Target(2.0)], None)
        result = self.deck._chord(self._state(), (0.0, 1.0, 0.0),
                                  (10.0, 1.0, 0.0), 0.4, 0.5)
        self.assertEqual(result['reason'], 'terrain')
        self.assertEqual(self.deck._hits['shot'].mat_kind, 3)

    def test_an_empty_chord_continues(self):
        self.deck._meta['shot'] = (lambda: [], None)
        result = self.deck._chord(self._state(), (0.0, 1.0, 0.0),
                                  (10.0, 1.0, 0.0), 0.4, 0.5)
        self.assertIsNone(result)

    def test_the_terminal_falls_back_to_the_cursor_point(self):
        seen = []
        self.deck._meta['shot'] = (None, lambda hit, reason:
                                   seen.append((hit, reason)))
        self.deck._terminal(self._state(), {'reason': 'max_time'})
        hit, reason = seen[0]
        self.assertEqual(reason, 'max_time')
        self.assertEqual((hit.point.x, hit.point.z), (9.0, 0.0))
        self.assertIsNone(hit.vehicle)


if __name__ == '__main__':
    unittest.main()
