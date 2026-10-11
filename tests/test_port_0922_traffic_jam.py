"""A stationary firing hold must not permanently plug friendly traffic."""
import math
import collections
import unittest
from unittest import mock

from test_port_0922_traffic import body, command
from gui.mods.offline_lan_0922.ai.traffic import (
    TrafficCoordinator, PARKED_JAM_SECONDS, PARKED_YIELD_SECONDS,
)
from gui.mods.offline_lan_0922.ai.driver import LocalDriver, combat_hull_aim
from gui.mods.offline_lan_0922.ai.adapter import BotAdapter
from gui.mods.offline_lan_0922 import vehicle_physics


def hold(own):
    return dict(command(own['yaw']), throttle=0., recovery_mode='arrived',
                movement_intent=False, combat_mode='artillery_hold',
                fire_allowed=True, target_id=99)


class ParkedTrafficTests(unittest.TestCase):
    def setUp(self):
        self.traffic = TrafficCoordinator()
        self.mover = body(25, 0., 0., speed=0.)
        self.parked = body(29, 0., 7., speed=0.)
        self.move = dict(command(), movement_intent=True)

    def tick(self, now, clear=lambda *args: True, extras=(), order=None):
        self.traffic.adjust(25, self.mover, self.move, [self.parked], now, clear)
        return self.traffic.adjust(
            29, self.parked, order or hold(self.parked),
            [self.mover] + list(extras), now, clear)

    def prime(self, clear=lambda *args: True, extras=()):
        self.assertEqual(hold(self.parked), self.tick(0., clear, extras))
        return self.tick(PARKED_JAM_SECONDS, clear, extras)

    def test_parked_artillery_makes_actual_forward_progress_then_resumes_aiming(self):
        first = self.prime()
        self.assertGreater(first['throttle'], 0.)
        self.assertEqual('friendly_yield', first['recovery_mode'])
        self.assertFalse(first['fire_allowed'])
        self.assertEqual(99, first['target_id'])
        position = self.parked['position'][2]
        speed = 0.
        modes = []
        for step in range(1, 91):
            order = self.tick(PARKED_JAM_SECONDS + step / 30.)
            modes.append(order.get('traffic_mode'))
            speed = vehicle_physics.longitudinal_step(
                dict(vehicle_physics._DEFAULTS), speed, order['throttle'],
                order['turn'], 0., 1. / 30.)
            position += speed / 30.
            self.parked.update(position=(0., 0., position), velocity=(0., 0., speed))
        self.assertGreater(position, 7.45)
        self.assertIn('friendly_yield', modes)
        self.assertIsNone(modes[-1])
        self.assertEqual(hold(self.parked), self.tick(5.))

    def test_stationary_episode_has_one_fixed_deadline(self):
        self.prime()
        for now in (2., 3., 4., 5.):
            self.assertEqual('friendly_yield', self.tick(now)['traffic_mode'])
        for now in (PARKED_JAM_SECONDS + PARKED_YIELD_SECONDS, 10., 30.):
            self.assertEqual(hold(self.parked), self.tick(now))
        # Only physical separation, not another decision refresh, rearms it.
        self.mover['position'] = (0., 0., -20.)
        self.tick(31.)
        self.mover['position'] = (0., 0., 0.)
        self.assertEqual(hold(self.parked), self.tick(32.))
        self.assertEqual('friendly_yield', self.tick(34.)['traffic_mode'])

    def test_checked_reverse_clears_a_parked_hull_behind_requester(self):
        self.parked['position'] = (0., 0., -7.)
        self.move.update(throttle=0., recovery_mode='blocked', reverse_blocked_by=29)
        order = self.prime()
        self.assertLess(order['throttle'], 0.)
        self.assertEqual(0., order['turn'])

    def test_explicit_rear_sweep_request_can_clear_a_hull_before_contact(self):
        self.parked['position'] = (0., 0., -10.)
        self.move.update(throttle=0., recovery_mode='blocked', reverse_blocked_by=29)
        self.assertLess(self.prime()['throttle'], 0.)

    def test_explicit_driver_blocker_request_also_clears_a_stalled_route_tank(self):
        self.parked['position'] = (0., 0., -7.)
        route = dict(command(), movement_intent=True)
        self.move.update(throttle=0., recovery_mode='blocked', reverse_blocked_by=29)
        order = self.tick(0., order=route)
        self.assertEqual('friendly_yield', order['traffic_mode'])
        self.assertLess(order['throttle'], 0.)
        # Ordinary route/recovery changes cannot reset the finite manoeuvre.
        for now in (1., 2., 3.):
            route['recovery_mode'] = 'reverse_turn' if now == 2. else 'drive'
            self.assertEqual('friendly_yield', self.tick(now, order=route)['traffic_mode'])
        for now in (PARKED_YIELD_SECONDS, 8., 12.):
            self.assertEqual(route, self.tick(now, order=route))

    def test_boxed_driver_requests_both_proved_vehicle_exits(self):
        driver = LocalDriver(stuck_seconds=0.4)
        neighbours = (body(29, 0., 8., speed=0.),
                      body(19, 0., -8., speed=0.))
        for frame in range(20):
            order = driver.drive(
                25, 9, (0., 0., 0.), 0., 0., .1, (40., 0., 0.),
                neighbours, lambda *args: True,
                pose_clear=lambda yaw: False)
            if order.get('reverse_blocked_by') is not None:
                break
        self.assertEqual('blocked', order['recovery_mode'])
        self.assertEqual((29, 19), (order.get('forward_blocked_by'),
                                   order.get('reverse_blocked_by')))
        self.assertEqual((0., 0.), (order['throttle'], order['turn']))

    def test_forward_request_survives_safe_hold_and_expires_after_separation(self):
        self.parked['position'] = (0., 0., 8.)
        rear = body(19, 0., -8., speed=0.)
        self.move.update(throttle=0., brake=True, recovery_mode='blocked',
                         forward_blocked_by=29, reverse_blocked_by=19)
        peers = [self.parked, rear]
        self.traffic.adjust(25, self.mover, self.move, peers, 0., lambda *args: True)
        safe = self.traffic.safe_controls(self.mover, self.move, peers, 0., lambda: 0.)
        self.assertEqual((0., 0.), (safe['throttle'], safe['turn']))
        self.assertEqual(29, self.traffic._orders[25][1]['forward_blocked_by'])
        route = dict(command(), movement_intent=True)
        yielding = self.traffic.adjust(29, self.parked, route, [self.mover, rear],
                                       .1, lambda *args: True)
        self.assertEqual('friendly_yield', yielding['recovery_mode'])
        self.assertGreater(yielding['throttle'], 0.)
        self.parked['position'] = (0., 0., 30.)
        self.traffic.safe_controls(self.mover, self.move, peers, .2, lambda: 0.)
        self.assertNotIn('forward_blocked_by', self.traffic._orders[25][1])
        self.assertEqual(route, self.traffic.adjust(
            29, self.parked, route, [self.mover, rear], .2, lambda *args: True))

    def test_route_pair_without_proved_reverse_request_keeps_its_own_controls(self):
        route = dict(command(), movement_intent=True)
        for now in (0., 2., 5.):
            self.assertEqual(route, self.tick(now, order=route))

    def test_two_blocked_route_tanks_cannot_yield_to_each_other_simultaneously(self):
        self.parked['position'] = (0., 0., -7.)
        self.move.update(throttle=0., recovery_mode='blocked', reverse_blocked_by=29)
        route = dict(command(), throttle=0., movement_intent=True,
                     recovery_mode='blocked', reverse_blocked_by=25)
        first = self.tick(0., order=route)
        self.assertEqual('friendly_yield', first['traffic_mode'])
        second = self.traffic.adjust(
            25, self.mover, self.move, [self.parked], .1, lambda *args: True)
        self.assertNotEqual('friendly_yield', second.get('traffic_mode'))

    def test_route_clearance_request_keeps_full_front_and_rear_hull_veto(self):
        self.parked['position'] = (0., 0., -7.)
        self.move.update(throttle=0., recovery_mode='blocked', reverse_blocked_by=29)
        route = dict(command(), movement_intent=True)
        rear = body(30, 0., -15., speed=0.)
        self.assertEqual(route, self.tick(0., extras=[rear], order=route))

    def test_front_and_rear_hulls_or_static_terrain_veto_the_entire_escape(self):
        forward = body(31, 0., 15., speed=0.)
        self.assertEqual(hold(self.parked), self.prime(extras=[forward]))
        self.traffic = TrafficCoordinator()
        self.assertEqual(hold(self.parked), self.prime(clear=lambda *args: False))
        # A new obstruction entering an active corridor stops the manoeuvre.
        self.traffic = TrafficCoordinator()
        self.prime()
        stopped = self.tick(2., extras=[forward])
        self.assertEqual((0., 0.), (stopped['throttle'], stopped['turn']))

    def test_two_holds_a_moving_neighbour_and_twenty_cm_gap_do_not_start_yield(self):
        self.move = hold(self.mover)
        self.assertEqual(hold(self.parked), self.prime())
        self.move = dict(command(), movement_intent=True)
        self.traffic = TrafficCoordinator()
        self.mover['velocity'] = (0., 0., 2.)
        self.assertEqual(hold(self.parked), self.prime())
        self.traffic = TrafficCoordinator()
        self.mover.update(velocity=(0., 0., 0.), position=(0., 0., -.2))
        self.assertEqual(hold(self.parked), self.prime())

    def test_dead_enemy_separate_level_and_stale_orders_cannot_request_clearance(self):
        for changes in ({'alive': False}, {'team': 2}, {'position': (0., 6., 0.)}):
            with self.subTest(changes=changes):
                self.traffic = TrafficCoordinator()
                self.mover = body(25, 0., 0., speed=0.)
                self.mover.update(changes)
                self.mover['shape'] = self.parked['shape'] = (1.5, 3.5, -.8, 2.)
                self.assertEqual(hold(self.parked), self.prime())
        self.mover = body(25, 0., 0., speed=0.)
        self.traffic = TrafficCoordinator()
        self.tick(0.)
        self.assertEqual(hold(self.parked), self.traffic.adjust(
            29, self.parked, hold(self.parked), [self.mover], 3., lambda *args: True))

    def test_side_contact_uses_safe_translation_without_rotating_or_reaiming(self):
        self.parked['position'] = (3., 0., 0.)
        order = self.prime()
        self.assertGreater(order['throttle'], 0.)
        turn, throttle, aiming = combat_hull_aim(
            0., math.pi / 2., -.1, .1, order['turn'], order['throttle'],
            order['recovery_mode'], True)
        self.assertEqual((0., order['throttle'], False), (turn, throttle, aiming))
        self.assertEqual((3., 0., 0.), self.parked['position'])

    def test_finite_yield_checks_remaining_centre_path_not_a_new_full_escape(self):
        self.parked['position'] = (3., 0., 0.)
        self.prime()
        deadline = self.traffic._parked[29]['until']
        self.parked['position'] = (3., 0., 4.)
        samples = []

        def clear_to_endpoint(unused_yaw, maximum_distance):
            samples.append(maximum_distance)
            return maximum_distance <= 3.5

        # This is advisory centre-path geometry; the runtime below separately
        # proves the full leading hull. A wall after this finite endpoint must
        # not turn a previously admitted yield into a permanent stop.
        order = self.tick(2., clear=clear_to_endpoint)
        self.assertGreater(order['throttle'], 0.)
        self.assertFalse(order['brake'])
        self.assertAlmostEqual(3.45, samples[-1])
        self.assertEqual(deadline, self.traffic._parked[29]['until'])
        blocked = self.tick(2.1, clear=lambda yaw, distance: distance < 3.)
        self.assertEqual(0., blocked['throttle'])
        self.assertTrue(blocked['brake'])

    def test_finite_yield_shortens_translation_but_preserves_full_vehicle_sweep(self):
        self.parked['position'] = (3., 0., 0.)
        self.prime()
        self.parked['position'] = (3., 0., 4.)
        # At the endpoint the yielding hull's front is at z=10.95. Both
        # blockers have centres beyond it; only the second hull intrudes.
        outside = body(31, 3., 14.5, speed=0.)
        self.assertGreater(self.tick(2., extras=[outside])['throttle'], 0.)
        inside = body(31, 3., 14., speed=0.)
        order = self.tick(2.1, extras=[inside])
        self.assertEqual(0., order['throttle'])
        self.assertTrue(order['brake'])

    def test_physical_hold_and_removed_requester_release_the_override(self):
        self.prime()
        physical = dict(hold(self.parked), recovery_mode='physical_hold')
        self.assertEqual(physical, self.tick(2., order=physical))
        self.traffic = TrafficCoordinator()
        self.prime()
        self.traffic.forget(25)
        self.assertEqual({}, self.traffic._parked)

    def test_departed_or_arrived_requester_cannot_leave_a_latched_jam(self):
        self.tick(0.)
        self.traffic.adjust(29, self.parked, hold(self.parked), [], 1., lambda *args: True)
        self.assertEqual({}, self.traffic._jams)
        self.assertEqual(hold(self.parked), self.tick(2.))
        self.assertEqual('friendly_yield', self.tick(4.)['traffic_mode'])
        self.move.update(recovery_mode='arrived', throttle=0.)
        self.assertEqual(hold(self.parked), self.tick(4.1))

    def test_adapter_keeps_both_blocked_corridor_identities(self):
        adapter = BotAdapter('test', 1)
        state = dict(id=25, team=1, slot=0, position=(0., 0., 0.),
                     yaw=0., speed=0., dt=.1, neighbours=[])
        strategic = dict(move_position=(0., 0., 40.), combat_mode='route')
        with mock.patch.object(adapter.driver, 'drive', return_value=dict(
                throttle=0., turn=0., target_yaw=0., recovery_mode='blocked',
                reverse_blocked_by=29, forward_blocked_by=19)):
            result = adapter.decide_with_order(state, strategic, lambda *args: True)
        self.assertEqual(29, result['reverse_blocked_by'])
        self.assertEqual(19, result['forward_blocked_by'])


class RuntimeTrafficJamTests(unittest.TestCase):
    # Reuse only the engine stubs / flat ground, retaining the production
    # adapter, neighbour snapshots, traffic coordinator and copied physics.
    from test_port_0922_separation_progress import SeparationProgressTests as _fixture
    setUp = _fixture.setUp
    tearDown = _fixture.tearDown

    def test_finite_yield_motion_keeps_leading_hull_and_receipt_containment(self):
        from test_port_0922_separation_progress import _flat_graph, runtime_fixtures
        for sign in (-1., 1.):
            for wall in (3.7, 4.5):
                with self.subTest(sign=sign, wall=wall):
                    receipts = []
                    probes = []

                    def direction(position, yaw, speed, descriptor, maximum_distance):
                        maximum_distance = (15. if maximum_distance is None
                                            else maximum_distance)
                        probes.append(maximum_distance)
                        clear = sign * position[2] + maximum_distance < wall
                        return dict(clear=clear, collision=not clear, slope=0.)

                    def receipt(position, yaw, speed, descriptor, maximum_distance):
                        maximum_distance = (15. if maximum_distance is None
                                            else maximum_distance)
                        if sign * position[2] + maximum_distance >= wall:
                            return False
                        value = dict(origin=position, yaw=yaw, direction=int(sign),
                                     distance=maximum_distance, leading=3.5,
                                     half_width=1.5)
                        receipts.append(value)
                        return value

                    command = dict(throttle=.65*sign, brake=False, turn=0.,
                        target_yaw=0., movement_intent=True, fire_allowed=False,
                        recovery_mode='friendly_yield', combat_mode='route',
                        move_position=(0., 0., 30.))
                    adapter = runtime_fixtures._FixedAdapter(command)
                    runtime = self.module.BotRuntime(1,
                        descriptor_resolver=lambda unused: runtime_fixtures._combat_descriptor(),
                        adapter_factory=lambda *args, **kwargs: adapter,
                        direction_probe=direction, world_receipt_probe=receipt,
                        ground_probe=lambda *unused: 0., physics_ground_probe=lambda *unused: 0.,
                        spawn_resolver=lambda *unused: ((0., 0., 0.), 0.),
                        baked_graph=_flat_graph())
                    runtime.battle_start(dict(round_id=1, map='01_karelia',
                        bot_authority_id=1, bots=[dict(id=25, team=1, slot=0,
                            name='Fixture', vehicle='fake')]))
                    # Isolate an already admitted finite manoeuvre from its
                    # requester; retain the actual motion/proof integration.
                    runtime._traffic_coordinator._parked[25] = dict(
                        origin=(0., -5.*sign), distance=5.8, sign=sign,
                        requester=17, until=4.)
                    runtime._traffic_coordinator.adjust = (
                        lambda bot_id, body, order, *unused: order)
                    previous = 0.
                    for frame in range(1, 31):
                        runtime.update(1./30., frame/30.)
                        state = runtime.states[25]
                        travelled = sign*state['z']
                        if travelled > previous + 1.e-8:
                            self.assertTrue(receipts)
                            proved = receipts[-1]
                            self.assertLessEqual(travelled + 3.5,
                                sign*proved['origin'][2] + proved['distance'])
                        previous = travelled
                    self.assertTrue(probes)
                    self.assertGreaterEqual(min(probes), 3.9)
                    if wall == 3.7:
                        self.assertAlmostEqual(0., runtime.states[25]['z'])
                    else:
                        self.assertGreater(sign*runtime.states[25]['z'], .4)
                        self.assertLess(sign*runtime.states[25]['z'] + 3.5, wall)

    def test_route_tank_and_parked_artillery_leave_the_spawn_queue(self):
        from test_port_0922_separation_progress import _flat_graph, runtime_fixtures
        runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: runtime_fixtures._combat_descriptor(),
            direction_probe=lambda *unused: dict(clear=True, collision=False, slope=0.),
            ground_probe=lambda *unused: 0., physics_ground_probe=lambda *unused: 0.,
            spawn_resolver=lambda team, slot: ((0., 0., 7. * slot), 0.),
            baked_graph=_flat_graph(), control_seconds=.1,
            visibility_probe=lambda *unused: False, firing_lane_probe=lambda *unused: False)
        runtime.battle_start(dict(
            round_id=1, map='01_karelia', bot_authority_id=1,
            bots=[dict(id=slot + 25, team=1, slot=slot, name='Fixture', vehicle='fake',
                       profile=dict(class_tag='heavyTank', dominant_role='brawler',
                                    roles=dict(brawler=1.), shells=[],
                                    desired_range=200, fire_range=500)) for slot in range(2)]))
        runtime.adapter.navigation_target = (
            lambda bot_id, position, target, strategic, state: target)
        runtime._apply_orders(dict(bot_order_revision=1, bot_orders=[
            dict(id=25, move_position=(0., 0., 30.), face_position=(0., 0., 30.),
                 combat_mode='route', fire_allowed=False),
            dict(id=26, move_position=(0., 0., 7.), face_position=(0., 0., 100.),
                 combat_mode='artillery_hold', throttle_override=0., fire_allowed=False),
        ]))
        modes = collections.Counter()
        for frame in range(1, 451):
            runtime.update(1. / 30., frame / 30.)
            modes[runtime._decision_cache.get(26, (0, 0, 0, {}))[3].get('traffic_mode')] += 1
        self.assertGreater(modes['friendly_yield'], 0)
        self.assertGreater(runtime.states[25]['z'], 10.)
        # The follower may now steer around the parked hull after it makes
        # the initial gap; the artillery only needs to move enough to clear
        # that blockage, then resume its hold.
        self.assertGreater(runtime.states[26]['z'], 7.45)
        from gui.mods.offline_lan_0922.ai.traffic import _separation
        bodies, unused_index = runtime._traffic_snapshot([])
        self.assertGreaterEqual(_separation(bodies[25], bodies[26]), -.011)
        self.assertGreater(modes[None], modes['friendly_yield'])


if __name__ == '__main__':
    unittest.main()
