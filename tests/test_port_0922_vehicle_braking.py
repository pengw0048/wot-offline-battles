"""Bots request brakes without weakening the physical push solver."""
import math
import unittest

from test_port_0922_traffic import body, command
from gui.mods.offline_lan_0922.ai.traffic import TrafficCoordinator
from gui.mods.offline_lan_0922 import vehicle_physics


class VehicleBrakingTests(unittest.TestCase):
    def setUp(self):
        self.traffic = TrafficCoordinator()

    def safe(self, own, peers, order=None, coast=0., now=0.):
        return self.traffic.safe_controls(
            own, order or command(), peers, now, lambda: coast)

    def test_same_heading_player_bot_and_enemy_do_not_receive_continuous_drive(self):
        for peer_id, team in ((29, 1), (100001, 1), (100002, 2)):
            for mode in ('route', 'advance', 'advance_contact', 'brawl'):
                own = body(25, 0., 0., speed=0.)
                peer = body(peer_id, 0., 7., speed=0., team=team)
                for now in (0., 1.5, 2., 10., 30.):
                    stopped = self.safe(
                        own, [peer], dict(command(), combat_mode=mode), now=now)
                    self.assertEqual(0., stopped['throttle'])
                    self.assertTrue(stopped['brake'])
                self.assertEqual(1., self.safe(own, [])['throttle'])

    def test_leader_parallel_side_contact_and_reverse_escape_keep_progress(self):
        own = body(25, 0., 0., speed=0.)
        for peer in (body(29, 0., -7.), body(29, 3., 0.), body(29, 3.2, 0.)):
            self.assertEqual(1., self.safe(own, [peer])['throttle'])
        reverse = dict(command(), throttle=-.72)
        self.assertEqual(-.72, self.safe(own, [body(29, 0., 7.)], reverse)['throttle'])
        self.assertEqual(0., self.safe(own, [body(29, 0., -7.)], reverse)['throttle'])

    def test_geometry_and_relative_velocity_allow_a_clearing_vehicle(self):
        own = body(25, 0., 0., speed=5.)
        self.assertEqual(1., self.safe(own, [body(29, 0., 8., speed=8.)], coast=2.)['throttle'])
        self.assertEqual(0., self.safe(own, [body(29, 0., 8., speed=0.)], coast=2.)['throttle'])
        own['shape'] = (1.5, 3.5, -.8, 2.)
        upper = dict(body(29, 0., 8., speed=0.), position=(0., 6., 8.),
                     shape=(1.5, 3.5, -.8, 2.))
        self.assertEqual(1., self.safe(own, [upper], coast=2.)['throttle'])

    def test_turning_into_a_side_hull_is_not_a_second_source_of_push(self):
        own = body(25, 0., 0., speed=0.)
        order = dict(command(), throttle=0., turn=1.)
        self.assertEqual(0., self.safe(own, [body(29, 3., 0., speed=0.)], order)['turn'])
        self.assertEqual(1., self.safe(own, [], order)['turn'])

    def test_reverse_steering_checks_the_actual_opposite_yaw(self):
        own = body(25, 0., 0., speed=0.)
        peer = body(29, 3.1, 5., speed=0.)
        forward = dict(command(), throttle=.7, turn=1.)
        reverse = dict(command(), throttle=-.7, turn=1.)
        self.assertEqual(0., self.safe(own, [peer], forward)['turn'])
        self.assertEqual(1., self.safe(own, [peer], reverse)['turn'])

    def test_crossing_player_brakes_even_without_a_cooperative_order(self):
        own = body(25, 0., 0., speed=8.)
        peer = body(100001, -5., 6., yaw=math.pi / 2., speed=6.)
        self.assertEqual(0., self.safe(own, [peer], coast=5.)['throttle'])
        self.assertEqual({}, self.traffic._orders)

    def test_copied_braking_law_stops_before_a_stationary_player(self):
        from gui.mods.offline_lan_0922.bot_runtime import BotRuntime
        params = dict(vehicle_physics._DEFAULTS)
        own, peer = body(25, 0., 0., speed=8.), body(100001, 0., 25., speed=0.)
        speed, z = 8., 0.
        brakes = 0
        for frame in range(1, 451):
            coast = BotRuntime._traffic_stopping_distance(speed, params)
            order = self.safe(own, [peer], coast=coast, now=frame / 30.)
            brakes += order['throttle'] == 0.
            speed = vehicle_physics.longitudinal_step(
                params, speed, order['throttle'], False, 0., 1. / 30.,
                handbrake=order.get('brake', False))
            z += speed / 30.
            own.update(position=(0., 0., z), velocity=(0., 0., speed))
            self.assertLess(z, 18.)
        self.assertGreater(z, 15.)
        self.assertGreater(brakes, 200)
        self.assertLess(speed, .05)

    def test_braking_distance_keeps_reverse_slope_direction(self):
        from gui.mods.offline_lan_0922.bot_runtime import BotRuntime
        params = dict(vehicle_physics._DEFAULTS)
        # Isolate grade direction; both runs start inside their own speed cap.
        params['speedBwd'] = params['speedFwd']
        distance = BotRuntime._traffic_stopping_distance
        forward_downhill = distance(8., params, .15)
        reverse_uphill = distance(-8., params, .15)
        self.assertGreater(forward_downhill, reverse_uphill)
        self.assertAlmostEqual(forward_downhill, distance(-8., params, -.15))
        self.assertAlmostEqual(reverse_uphill, distance(8., params, -.15))

    def test_stopped_follower_can_request_parked_clearance_before_contact(self):
        from test_port_0922_traffic_jam import hold
        own, peer = body(25, 0., 0., speed=0.), body(29, 0., 7.1, speed=0.)
        clear = lambda *args: True
        for now in (0., 1., 2.):
            raw = self.traffic.adjust(25, own, command(), [peer], now, clear)
            self.safe(own, [peer], raw, now=now)
            result = self.traffic.adjust(
                29, peer, dict(hold(peer), brake=True), [own], now, clear)
        self.assertEqual('friendly_yield', result['traffic_mode'])
        self.assertGreater(result['throttle'], 0.)
        self.assertFalse(result['brake'])


class RuntimeVehicleBrakingTests(unittest.TestCase):
    from test_port_0922_separation_progress import SeparationProgressTests as _fixture
    setUp = _fixture.setUp
    tearDown = _fixture.tearDown

    def _route_runtime(self):
        from test_port_0922_separation_progress import _flat_graph, runtime_fixtures
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: runtime_fixtures._combat_descriptor(),
            direction_probe=lambda *unused: dict(clear=True, collision=False, slope=0.),
            ground_probe=lambda *unused: 0., physics_ground_probe=lambda *unused: 0.,
            spawn_resolver=lambda team, slot: ((0., 0., 0.), 0.),
            baked_graph=_flat_graph(), control_seconds=.1,
            visibility_probe=lambda *unused: False, firing_lane_probe=lambda *unused: False)
        runtime.battle_start(dict(
            round_id=1, map='01_karelia', bot_authority_id=1,
            bots=[dict(id=25, team=1, slot=0, name='Fixture', vehicle='fake',
                       profile=dict(class_tag='heavyTank', dominant_role='brawler',
                                    roles=dict(brawler=1.), shells=[],
                                    desired_range=200, fire_range=500))]))
        runtime.adapter.navigation_target = lambda bot_id, position, target, strategic, state: target
        runtime._apply_orders(dict(bot_order_revision=1, bot_orders=[
            dict(id=25, move_position=(0., 0., 30.), face_position=(0., 0., 30.),
                 combat_mode='route', fire_allowed=False)]))
        return runtime

    def test_new_player_blocker_is_checked_before_cached_decision_expires(self):
        runtime = self._route_runtime()
        runtime.update(.1, .1)
        count = runtime._decision_counts[25]
        own = runtime.states[25]
        peer = body(100001, own['x'], own['z'] + own['half_length'] + 3.5, speed=0.)
        runtime.update(.1, .2, neighbours=[peer])
        self.assertEqual(count, runtime._decision_counts[25])
        self.assertTrue(own['traffic_braking'])
        self.assertEqual(0, own['movement_dir'])

    def test_route_steers_around_a_stationary_player_without_pushing(self):
        from gui.mods.offline_lan_0922 import tank_collision
        for fps in (15, 24):
            runtime = self._route_runtime()
            own = runtime.states[25]
            peer = body(100001, 0., 12., speed=0.)
            braking, maximum_side = 0, 0.
            for frame in range(1, fps * 30 + 1):
                runtime.update(1. / fps, frame / float(fps), neighbours=[peer])
                braking += own.get('traffic_braking', False)
                maximum_side = max(maximum_side, abs(own['x']))
                overlap = tank_collision._obb_overlap(
                    own['x'], own['z'], own['yaw'], own['collision_shape'],
                    0., 12., 0., (1.5, 3.5))
                self.assertLessEqual(overlap[2], .011)
            self.assertGreater(braking, 0)
            self.assertGreater(maximum_side, 3.)
            self.assertGreater(own['z'], 20.)

    def test_two_adjacent_parked_vehicles_do_not_create_an_alternating_blockage(self):
        from gui.mods.offline_lan_0922 import tank_collision
        runtime = self._route_runtime()
        own = runtime.states[25]
        peers = [body(100001, -2., 12., speed=0.),
                 body(100002, 2., 12., speed=0.)]
        for frame in range(1, 721):
            runtime.update(1. / 24., frame / 24., neighbours=peers)
            for peer in peers:
                overlap = tank_collision._obb_overlap(
                    own['x'], own['z'], own['yaw'], own['collision_shape'],
                    peer['position'][0], 12., 0., (1.5, 3.5))
                self.assertLessEqual(overlap[2], .011)
        self.assertGreater(own['z'], 20.)


if __name__ == '__main__':
    unittest.main()
