import math
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import package_stub

package_stub.load('entity_setup')
package_stub.load('motion')
package_stub.load('suspension')
# The same inert destructibles stand-in test_world_collision installs, so
# world_collision binds identical fakes whichever test module loads first.
package_stub.stub('destructibles_sensor',
                  _catalog_soft_static_path=lambda *a, **k: False,
                  _diagnostic_static_recast_1513=lambda *a, **k: None,
                  _try_destroy_solid_hit=lambda *a, **k: False,
                  _vehicle_hull_bbox=lambda descriptor: (
                      (-1.4, -0.5, -3.0), (1.4, 1.2, 3.2), 0))
package_stub.load('world_collision')
package_stub.load('engine_shim')
bot_control = package_stub.load('bot_control')

# Ground probes need an engine; rebind only this module's view of them.
bot_control.suspension = types.SimpleNamespace(
    hull_span=lambda descriptor: (5.0, 2.4),
    smooth=lambda previous, target: target,
    median_pitch=lambda history, raw: raw,
    drive_pitch=lambda space_id, position, yaw: 0.0,
    ground_y=lambda space_id, x, z, hint: None,
    settle=lambda body_y, ground, speed, dt: ground)


def follower_state(bot_id=5, team=2, yaw=0.0, speed=2.0):
    return {'id': bot_id, 'team': team, 'position': (0.0, 0.0, 0.0),
            'yaw': yaw, 'speed': speed, 'half_length': 1.5,
            'half_width': 1.0}


def neighbour(offset_z, other_id=3, team=2, yaw=0.0):
    return {'id': other_id, 'team': team, 'position': (0.0, 0.0, offset_z),
            'yaw': yaw, 'velocity': (0.0, 0.0, 0.0),
            'half_length': 1.5, 'half_width': 1.0}


class TrafficThrottleTests(unittest.TestCase):
    def test_a_follower_stops_behind_the_vehicle_ahead(self):
        throttle, waiting = bot_control.traffic_throttle(
            follower_state(), {'throttle': 0.8}, [neighbour(4.0)])
        self.assertEqual(throttle, 0.0)
        self.assertTrue(waiting)

    def test_the_other_team_is_not_traffic(self):
        throttle, waiting = bot_control.traffic_throttle(
            follower_state(), {'throttle': 0.8}, [neighbour(4.0, team=1)])
        self.assertEqual(throttle, 0.8)
        self.assertFalse(waiting)

    def test_the_lower_id_has_right_of_way_at_a_crossing(self):
        crossing = neighbour(4.0, other_id=7, yaw=2.0)
        throttle, waiting = bot_control.traffic_throttle(
            follower_state(bot_id=1), {'throttle': 0.8}, [crossing])
        self.assertEqual(throttle, 0.8)
        self.assertFalse(waiting)

    def test_every_bot_yields_to_a_human(self):
        human = neighbour(4.0, yaw=2.0,
                          other_id=bot_control.HUMAN_TARGET_ID_BASE + 1)
        throttle, waiting = bot_control.traffic_throttle(
            follower_state(bot_id=1), {'throttle': 0.8}, [human])
        self.assertEqual(throttle, 0.0)
        self.assertTrue(waiting)

    def test_reverse_and_idle_are_never_throttled(self):
        for value in (0.0, -0.6):
            throttle, waiting = bot_control.traffic_throttle(
                follower_state(), {'throttle': value}, [neighbour(4.0)])
            self.assertEqual(throttle, value)
            self.assertFalse(waiting)


class FakeDriver(object):
    def __init__(self):
        self.failures = []
        self.waits = []

    def remember_failure(self, bot_id, yaw):
        self.failures.append((bot_id, yaw))

    def wait_for_traffic(self, bot_id):
        self.waits.append(bot_id)


class FakeAdapter(object):
    def __init__(self):
        self.driver = FakeDriver()


class FakeForce(object):
    def __init__(self):
        self.poses = {}

    def health(self, vehicle_id):
        return 100

    def set_pose(self, vehicle_id, pose, velocity=None):
        self.poses[vehicle_id] = (pose, velocity)


def make_control(clear):
    control = bot_control.BotControl.__new__(bot_control.BotControl)
    control._adapter = FakeAdapter()
    control._force = FakeForce()
    control._space_id = 0
    control._log = lambda message: None
    control._player_motion = None
    control._spotting = None
    control._bodies = {}
    control._direction_clear = lambda body: (lambda yaw: clear)
    control._scroll_tracks = lambda body: None
    return control


def make_body(yaw=0.0):
    return bot_control.BotBody(9, (10.0, 0.0, 20.0, yaw), object(), 2)


class ApplyTests(unittest.TestCase):
    def test_a_clear_command_moves_the_body(self):
        control = make_control(clear=True)
        body = make_body()
        control._apply(body, {'throttle': 1.0, 'turn': 0.0})
        control._integrate(body, 0.1)
        self.assertGreater(body.z, 20.0)
        self.assertEqual(control._adapter.driver.failures, [])
        self.assertEqual(control._force.poses[9][0], body.pose)

    def test_a_blocked_travel_direction_is_remembered(self):
        control = make_control(clear=False)
        body = make_body()
        body.speed = 3.0
        control._apply(body, {'throttle': 1.0, 'turn': 0.0})
        self.assertEqual(body.throttle, 0.0)
        self.assertFalse(body.clear)
        self.assertEqual(control._adapter.driver.failures, [(9, 0.0)])
        control._integrate(body, 0.1)
        self.assertEqual((body.x, body.z), (10.0, 20.0))
        self.assertLess(abs(body.speed), 3.0 * 0.2 + 1e-6)

    def test_reverse_probes_the_rear(self):
        control = make_control(clear=False)
        body = make_body()
        control._apply(body, {'throttle': -1.0, 'turn': 0.0})
        self.assertEqual(control._adapter.driver.failures,
                         [(9, math.pi)])

    def test_an_idle_command_does_not_probe(self):
        control = make_control(clear=False)
        body = make_body()
        control._apply(body, {'throttle': 0.0, 'turn': 0.0})
        self.assertEqual(control._adapter.driver.failures, [])
        self.assertTrue(body.clear)


class FakeVector(object):
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


class StateTests(unittest.TestCase):
    def setUp(self):
        self._math = sys.modules.get('Math')
        fake = types.ModuleType('Math')
        fake.Matrix = lambda source: source
        sys.modules['Math'] = fake

    def tearDown(self):
        if self._math is None:
            sys.modules.pop('Math', None)
        else:
            sys.modules['Math'] = self._math

    def test_the_player_rides_in_neighbours_and_contacts(self):
        control = make_control(clear=True)
        body = make_body()
        control._bodies = {9: body, 11: make_body()}
        control._bodies[11].id = 11
        player = types.SimpleNamespace(
            id=42, health=180,
            position=FakeVector(1.0, 2.0, 3.0),
            matrix=types.SimpleNamespace(yaw=0.5),
            typeDescriptor=types.SimpleNamespace(maxHealth=200))
        state = control._state(body, player, 12.5)
        self.assertEqual(state['team'], 2)
        self.assertEqual(state['health'], 100.0)
        ids = [entry['id'] for entry in state['neighbours']]
        self.assertIn(11, ids)
        self.assertIn(bot_control.HUMAN_TARGET_ID_BASE + 42, ids)
        human = [entry for entry in state['neighbours']
                 if entry['id'] > bot_control.HUMAN_TARGET_ID_BASE][0]
        self.assertEqual(human['team'], 1)
        contact = state['contacts'][0]
        self.assertEqual(contact['id'], 42)
        self.assertEqual(contact['max_health'], 200.0)

    def test_without_a_player_the_bots_still_see_each_other(self):
        control = make_control(clear=True)
        body = make_body()
        other = make_body()
        other.id = 11
        control._bodies = {9: body, 11: other}
        state = control._state(body, None, 1.0)
        self.assertEqual([entry['id'] for entry in state['neighbours']],
                         [11])
        self.assertEqual(state['contacts'], [])


if __name__ == '__main__':
    unittest.main()
