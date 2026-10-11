"""Timed and permanent authored waypoint orders through the real server."""
import copy
import unittest

from test_port_0922_bot_tactics_editor import cfg, planning, profile
from test_port_0922_server_bot_ai import BotPlanner, _bot, _route, _state


class AuthoredRouteWaitTests(unittest.TestCase):
    def setup_route(self, class_tag='heavyTank', seconds=30):
        raw = profile()
        route = raw['maps']['08_ruinberg']['routes'][0]
        route.update(classes=[class_tag], points=[
            [0, 0, 1, seconds], [100, 0, 1, -1]])
        planner = BotPlanner()
        planner.tactics = cfg.canonical(raw)
        planner.tactics_map = '08_ruinberg'
        wire = planning.route_value(route)
        self.assertTrue(all(len(point) == 3 for point in wire['waypoints']))
        manifest = [_bot(11, 1, 0, _route(wire['id'], wire['waypoints']), class_tag)]
        return planner, manifest

    def order(self, planner, manifest, x, now):
        return planner.build_orders(
            manifest, [_state(11, 1, x, 0)], [], now)['orders'][0]

    def test_wait_starts_at_arrival_then_moves_to_permanent_hold(self):
        planner, manifest = self.setup_route()
        approaching = self.order(planner, manifest, -100, 1)
        self.assertEqual(0, approaching['route_index'])
        arrived = self.order(planner, manifest, 0, 10)
        self.assertEqual(('hold', 0.0), (
            arrived['combat_mode'], arrived['throttle_override']))
        self.assertEqual(0, self.order(planner, manifest, 0, 39.9)['route_index'])
        released = self.order(planner, manifest, 0, 40)
        self.assertEqual(1, released['route_index'])
        self.assertEqual(100, released['move_position']['x'])
        self.assertIsNone(released['throttle_override'])
        self.assertEqual('hold', self.order(planner, manifest, 100, 50)['combat_mode'])
        self.assertEqual('hold', self.order(planner, manifest, 100, 5000)['combat_mode'])

    def test_spg_follows_authored_route_instead_of_returning_to_initial_position(self):
        planner, manifest = self.setup_route('SPG', 5)
        self.order(planner, manifest, 0, 1)
        released = self.order(planner, manifest, 0, 6)
        self.assertEqual(('route', 100), (
            released['combat_mode'], released['move_position']['x']))
        self.assertIsNone(released['throttle_override'])

    def test_a_passed_corridor_cannot_skip_the_opening_parking_instruction(self):
        planner, manifest = self.setup_route()
        passed = self.order(planner, manifest, 50, 1)
        self.assertEqual(0, passed['route_index'])
        self.assertEqual(0, passed['move_position']['x'])
        self.assertIsNone(passed['throttle_override'])

    def test_condition_roundtrip_and_invalid_waits(self):
        raw = profile()
        raw['maps']['08_ruinberg']['routes'][0]['points'][0].append(12.5)
        self.assertEqual(raw, cfg.canonical(raw))
        for seconds in (float('nan'), float('inf'), -0.5, -2, 3601, True):
            changed = copy.deepcopy(raw)
            changed['maps']['08_ruinberg']['routes'][0]['points'][0][3] = seconds
            with self.subTest(seconds=seconds), self.assertRaises(cfg.TacticsError):
                cfg.canonical(changed)


if __name__ == '__main__':
    unittest.main()
