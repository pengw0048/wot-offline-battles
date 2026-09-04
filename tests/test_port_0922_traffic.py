import copy
import math
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'res' / 'scripts' / 'client'))
from gui.mods.offline_lan_0922.ai.traffic import (
    HEAD_ON_OFFSET, TrafficCoordinator, YIELD_SECONDS,
)
from gui.mods.offline_lan_0922 import tank_collision


def body(actor, x, z, yaw=0.0, speed=4.0, team=1):
    return {'id': actor, 'team': team, 'alive': True,
            'position': (x, 0.0, z), 'yaw': yaw,
            'velocity': (math.sin(yaw) * speed, 0.0, math.cos(yaw) * speed),
            'half_width': 1.5, 'half_length': 3.5}


def command(yaw=0.0):
    return {'throttle': 1.0, 'turn': 0.0, 'target_yaw': yaw,
            'recovery_mode': 'drive', 'combat_mode': 'route'}


class TrafficTests(unittest.TestCase):
    def setUp(self):
        self.traffic = TrafficCoordinator()

    def adjust(self, own, other, now=0.0, order=None, clear=True):
        return self.traffic.adjust(
            own['id'], own, order or command(own['yaw']), [other], now,
            lambda unused_yaw: clear)

    def test_parallel_twenty_centimetre_gap_never_changes_either_command(self):
        first, second = body(1, 0.0, 0.0), body(2, 3.2, 0.0)
        for own, other in ((first, second), (second, first)):
            self.assertEqual(command(), self.adjust(own, other))
        self.assertIsNone(tank_collision.obb_contact(
            0.0, 0.0, 0.0, (1.5, 3.5), 3.2, 0.0, 0.0, (1.5, 3.5)))

    def test_same_direction_rear_contact_does_not_brake_or_divert_leader(self):
        leader, follower = body(9, 0.0, 6.9, speed=4.0), body(1, 0.0, 0.0, speed=8.0)
        self.assertEqual(command(), self.adjust(leader, follower))
        self.assertEqual(command(), self.adjust(follower, leader))

    def test_crossing_first_front_in_junction_keeps_priority_in_both_call_orders(self):
        first, second = body(9, 0.0, -2.0), body(1, -6.0, 0.0, math.pi / 2.0)
        for ordered in ((first, second), (second, first)):
            self.traffic = TrafficCoordinator()
            results = {}
            results[ordered[0]['id']] = self.adjust(*ordered)
            results[ordered[1]['id']] = self.adjust(ordered[1], ordered[0])
            self.assertEqual(command(first['yaw']), results[first['id']])
            self.assertEqual(0.0, results[second['id']]['throttle'])
            self.assertEqual('yield', results[second['id']]['traffic_mode'])

    def test_future_crossing_uses_actual_arrival_not_lowest_id(self):
        first, second = body(9, 0.0, -5.0, speed=10.0), body(1, -10.0, 0.0, math.pi / 2.0, 10.0)
        self.assertEqual(1.0, self.adjust(first, second)['throttle'])
        self.assertEqual(0.0, self.adjust(second, first)['throttle'])

    def test_lease_survives_yielding_velocity_change_without_extending_deadline(self):
        first, second = body(9, 0.0, -2.0), body(1, -6.0, 0.0, math.pi / 2.0)
        self.adjust(second, first)
        second['velocity'] = (0.0, 0.0, 0.0)
        for now in (0.2, 0.5, 1.0):
            self.assertEqual(0.0, self.adjust(second, first, now)['throttle'])
        for now in (YIELD_SECONDS, YIELD_SECONDS + 0.1, 10.0):
            self.assertEqual(1.0, self.adjust(second, first, now)['throttle'])

    def test_winner_rear_passing_crossing_releases_wait_immediately(self):
        first, second = body(9, 0.0, -2.0), body(1, -6.0, 0.0, math.pi / 2.0)
        self.adjust(second, first)
        first['position'] = (0.0, 0.0, 6.0)
        self.assertEqual(command(second['yaw']), self.adjust(second, first, 0.5))

    def test_crossing_away_from_each_other_never_yields(self):
        first, second = body(1, 0.0, 10.0), body(2, 10.0, 0.0, math.pi / 2.0)
        self.assertEqual(command(), self.adjust(first, second))

    def test_crossing_with_disjoint_arrival_times_never_yields(self):
        first = body(1, 0.0, -15.0, speed=20.0)
        second = body(2, -5.0, 0.0, math.pi / 2.0, 20.0)
        self.assertEqual(command(), self.adjust(first, second))
        self.assertEqual(command(second['yaw']), self.adjust(second, first))

    def test_reversing_same_heading_neighbour_is_not_a_head_on_order(self):
        first, second = body(1, 0.0, 0.0), body(2, 0.0, 7.5, speed=-2.0)
        self.assertEqual(command(), self.adjust(first, second))

    def test_head_on_right_side_is_latched_in_world_space(self):
        first, second = body(1, 0.0, 0.0, speed=0.0), body(2, 0.0, 7.5, math.pi, 0.0)
        first_result = self.adjust(first, second)
        second_result = self.adjust(second, first)
        self.assertEqual(HEAD_ON_OFFSET, first_result['target_yaw'])
        self.assertEqual(math.pi + HEAD_ON_OFFSET, second_result['target_yaw'])
        self.assertGreater(first_result['turn'], 0.0)
        self.assertGreater(second_result['turn'], 0.0)
        first['yaw'] = 0.2
        for now in (0.1, 0.2, 0.3):
            self.assertEqual(HEAD_ON_OFFSET, self.adjust(first, second, now)['target_yaw'])

    def test_head_on_safe_lateral_clearance_releases_offset(self):
        first, second = body(1, 0.0, 0.0, speed=0.0), body(2, 0.0, 7.5, math.pi, 0.0)
        self.adjust(first, second)
        first['position'] = (3.2, 0.0, 0.0)
        self.assertEqual(command(), self.adjust(first, second, 0.2))

    def test_narrow_head_on_does_not_turn_into_static_obstacle_or_move_body(self):
        first, second = body(1, 0.0, 0.0, speed=0.0), body(2, 0.0, 7.5, math.pi, 0.0)
        before = copy.deepcopy((first, second))
        for own, other in ((first, second), (second, first)):
            result = self.adjust(own, other, clear=False)
            self.assertEqual((0.0, 0.0), (result['throttle'], result['turn']))
            self.assertEqual(own['yaw'], result['target_yaw'])
        self.assertEqual(before, (first, second))

    def test_real_shape_width_wins_over_generic_half_width(self):
        first, second = body(1, 0.0, 0.0, speed=0.0), body(2, 2.2, 7.5, math.pi, 0.0)
        first['shape'] = second['shape'] = (1.0, 3.5, -0.8, 2.0)
        self.assertEqual(command(), self.adjust(first, second))

    def test_unequal_vehicle_widths_keep_a_real_twenty_centimetre_gap(self):
        first, second = body(1, 0.0, 0.0), body(2, 2.9, 0.0)
        first['shape'] = (0.8, 2.5, -0.5, 1.8)
        second['shape'] = (1.9, 4.2, -0.9, 2.4)
        self.assertEqual(command(), self.adjust(first, second))
        self.assertEqual(command(), self.adjust(second, first))

    def test_vertical_levels_do_not_receive_crossing_or_head_on_orders(self):
        first, second = body(1, 0.0, 0.0), body(2, 0.0, 7.5, math.pi)
        first['shape'] = second['shape'] = (1.5, 3.5, -0.8, 2.0)
        second['position'] = (0.0, 5.0, 7.5)
        self.assertEqual(command(), self.adjust(first, second))

    def test_does_not_override_static_avoidance_recovery_stops_or_reverse(self):
        first, second = body(1, 0.0, 0.0), body(2, 0.0, 7.5, math.pi)
        for mode in ('avoid', 'blocked', 'nav_wait', 'arrived', 'reverse_turn', 'pivot_recovery'):
            order = command()
            order['recovery_mode'] = mode
            self.assertEqual(order, self.adjust(first, second, order=order))
        for throttle in (0.0, -1.0):
            order = command()
            order['throttle'] = throttle
            self.assertEqual(order, self.adjust(first, second, order=order))
        first['velocity'] = (0.0, 0.0, -1.0)
        self.assertEqual(command(), self.adjust(first, second))

    def test_dead_enemy_and_absent_peers_do_not_keep_a_lease(self):
        first, second = body(1, 0.0, 0.0), body(2, 0.0, 7.5, math.pi)
        self.adjust(first, second)
        second['alive'] = False
        self.assertEqual(command(), self.adjust(first, second))
        second['alive'], second['team'] = True, 2
        self.assertEqual(command(), self.adjust(first, second))
        second['team'] = 1
        self.adjust(first, second)
        self.traffic.forget(first['id'])
        self.assertEqual({}, self.traffic._pairs)


if __name__ == '__main__':
    unittest.main()
