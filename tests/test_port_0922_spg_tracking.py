"""SPG target/driver/arc feedback regressions, not native-map acceptance."""
import contextlib
import copy
import io
import math
from pathlib import Path
import sys
import unittest
from unittest import mock

import test_port_0922_bot_runtime as harness
from test_port_0922_server_bot_ai import _bot, _contact, _route, _state
from server_bot_ai import BotPlanner


class SPGTrackingTests(unittest.TestCase):
    def setUp(self):
        self.saved = {k: v for k, v in sys.modules.items()
                      if k == 'gui' or k.startswith('gui.')}
        self.runtime_module = harness._load()
        self.old_factors = self.runtime_module.loadout.attribute_factors
        self.runtime_module.loadout.attribute_factors = harness._plain_attribute_factors
        from gui.mods.offline_lan_0922.artillery_controller import ArtilleryController
        self.controller_type = ArtilleryController
        self.route = _route('rear', [(0, -30, False), (0, 0, False), (0, 200, False)])

    def tearDown(self):
        self.runtime_module.loadout.attribute_factors = self.old_factors
        for key in list(sys.modules):
            if key == 'gui' or key.startswith('gui.'):
                sys.modules.pop(key, None)
        sys.modules.update(self.saved)

    def _order(self, planner, contact, now, class_tag='SPG'):
        manifest = [_bot(11, 1, 0, self.route, class_tag)]
        states = [_state(11, 1, 0, 0)]
        enemy = {'id': 2, 'team': 2, 'alive': True}
        planner.report_contacts([contact], planner.known_targets(states, [enemy]), now)
        return planner.build_orders(manifest, states, [enemy], now)['orders'][0]

    def test_received_contact_can_be_tracked_before_muzzle_arc_exists(self):
        planner = BotPlanner()
        for now in (1.0, 3.1, 20.0):
            order = self._order(planner, _contact(2, 200, 0, []), now)
            self.assertEqual(2, order['target_id'])
            self.assertTrue(order['fire_allowed'])  # attempt; exact proof is local
            self.assertEqual({'x': 200., 'y': 0., 'z': 0.}, order['face_position'])

    def test_non_spg_does_not_acquire_an_unproved_direct_lane(self):
        order = self._order(BotPlanner(), _contact(2, 200, 0, []), 1.0, 'mediumTank')
        self.assertIsNone(order['target_id'])
        self.assertFalse(order['fire_allowed'])

    def test_explicit_radio_disconnect_never_grants_artillery_target(self):
        contact = dict(_contact(2, 200, 0, []), radio_recipients=[])
        order = self._order(BotPlanner(), contact, 1.0)
        self.assertIsNone(order['target_id'])
        self.assertFalse(order['fire_allowed'])

    def test_failed_arc_does_not_reserve_a_ready_shooter_focus_slot(self):
        planner = BotPlanner()
        with mock.patch.object(planner, '_reserve_focus', wraps=planner._reserve_focus) as reserve:
            order = self._order(planner, _contact(2, 200, 0, []), 1.0)
        self.assertEqual(2, order['target_id'])
        self.assertEqual(0, reserve.call_count)

    def _exercise(self, blocked=False, seconds=40):
        # Real BotPlanner -> real BotAdapter/LocalDriver -> BotRuntime -> both
        # ArtilleryController queues. Only engine geometry and descriptors are
        # simulated; no injected fire_allowed=True or fabricated launch receipt.
        graph = harness._flat_open_graph()
        graph['routes']['1'] = ({'id': 'rear', 'waypoints': (
            (0., -30., False), (0., 0., False), (0., 40., False), (0., 200., False))},)
        descriptor = harness._combat_descriptor(
            reload_time=15, clip=(1, 0), turret_yaw_limits=(-.15, .15),
            turret_speed=.3, gun_speed=.3, dispersion=.03, max_ammo=30)
        descriptor.chassis.rotationSpeed = .25
        descriptor.gun.pitchLimits = {'absolute': (-.9, .15)}
        descriptor.gun.shots = ({'shell': {'effectsIndex': 0}, 'speed': 425.,
                                'gravity': 143., 'maxDistance': 10000.},)
        controller = self.controller_type()
        clock = [0.]
        calls = []

        def lane(source, target):
            ready, solution = controller.request(source, target, descriptor, 0, clock[0])
            return ready and solution is not None

        def launch(source, target, desc, shell, seq, yaw, pitch, flight, now):
            ready, receipt = controller.request_launch(
                source, target, desc, shell, seq,
                (source['x'], source['y']+1.5, source['z']), yaw, pitch, flight, now)
            if ready and receipt is not None:
                calls.append(receipt)
            return receipt if ready else None

        def world(start, end):
            # An impenetrable close wall, with no alternative route in the
            # blocked test. All native world probes retain their failure gate.
            if blocked and start[0] <= 8. <= end[0] and end[0] > start[0]:
                factor = (8.-start[0])/(end[0]-start[0])
                return tuple(start[i] + (end[i]-start[i])*factor for i in range(3))
            return None

        runtime = self.runtime_module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            direction_probe=lambda *args: {'clear': True, 'slope': 0.},
            visibility_probe=lambda *args: True,
            firing_lane_probe=lane, ballistic_solution_probe=controller.solution,
            artillery_launch_probe=launch, artillery_launch_cancel=controller.cancel_launch,
            artillery_friendly_lane_probe=lambda *args: True,
            ground_probe=lambda *args: 0., physics_ground_probe=lambda *args: 0.,
            spawn_resolver=lambda *args: ((0., 0., 0.), 0.), baked_graph=graph)
        runtime.battle_start({
            'round_id': 7, 'map': '01_karelia', 'bot_authority_id': 1,
            'bot_skill_mode': 'brutal', 'bots': [{
                'id': 11, 'team': 1, 'slot': 0, 'vehicle': 'test:SPG', 'name': 'SPG',
                'profile': {'class_tag': 'SPG', 'speed': 12., 'dominant_role': 'artillery',
                            'desired_range': 650., 'fire_range': 1250., 'shells': []}}]})
        player = harness._admit_player({'id': 2, 'team': 2, 'alive': True,
                                       'x': 200., 'y': 0., 'z': 0., 'yaw': 0., 'speed': 0.})
        manifest = [runtime._manifest_entry(runtime.states[11])]
        planner = BotPlanner()
        records = []
        with contextlib.redirect_stdout(io.StringIO()):
            for frame in range(1, int(seconds*20)+1):
                now = clock[0] = frame/20.
                self.assertLessEqual(controller.advance(now, 4, world), 4)
                state = runtime.states[11]
                if frame % 2 == 0:
                    ready, solution = controller.request(
                        state, {'kind': 'human', 'network_id': 2,
                                'position': (200., 0., 0.), 'speed': 0.}, descriptor, 0, now)
                    observation = _contact(2, 200., 0., [11] if ready and solution else [])
                    planner.report_contacts([observation], planner.known_targets([state], [player]), now)
                    orders = planner.build_orders(manifest, [state], [player], now)
                    runtime._apply_orders({'bot_orders': orders['orders'],
                                           'bot_order_revision': orders['revision']})
                runtime.update(.05, now, players=[player])
                if frame % 20 == 0:
                    records.append((now, runtime.states[11]['fire_seq'],
                                    runtime.states[11]['yaw'],
                                    runtime._server_orders.get(11, {}).get('target_id')))
        return runtime, controller, calls, records

    def test_full_target_driver_and_both_arc_queues_fire_at_side_contact(self):
        runtime, controller, receipts, records = self._exercise(seconds=40)
        self.assertGreaterEqual(runtime.states[11]['fire_seq'], 2)
        self.assertTrue(receipts)
        self.assertTrue(all(row[3] == 2 for row in records))
        self.assertAlmostEqual(math.pi/2, runtime.states[11]['yaw'], places=2)
        self.assertTrue(all('proof_key' in row for row in receipts))

    def test_target_permission_does_not_fire_through_confirmed_wall(self):
        runtime, controller, receipts, records = self._exercise(blocked=True, seconds=25)
        self.assertEqual(0, runtime.states[11]['fire_seq'])
        self.assertEqual([], receipts)
        self.assertTrue(all(row[3] == 2 for row in records))
        self.assertFalse(runtime._artillery_intents)

    def _position_fixture(self):
        runtime = self.runtime_module.BotRuntime(1)
        state = {'id': 11, 'x': 0., 'y': 0., 'z': 0., 'yaw': 0., 'speed': 0.,
                 'profile': {'class_tag': 'SPG'},
                 'route': {'waypoints': ((0., -30., False), (0., 0., False), (0., 200., False))},
                 '_spg_obstruction': {'stamp': ((0., 0., 0.), 0., ('human', 2)),
                                      'since': 1., 'last': 5.}}
        order = {'combat_mode': 'artillery_hold', 'move_position': (0., 0., 0.),
                 'target_id': 1000002, 'target_kind': 'human', 'fire_allowed': True}
        targets = {1000002: {'id': 1000002, 'kind': 'human', 'network_id': 2,
                            'position': (200., 0., 0.)}}
        return runtime, state, order, targets

    def test_near_wall_relocation_uses_rear_waypoint_without_moving_pose(self):
        runtime, state, order, targets = self._position_fixture()
        result = runtime._artillery_position_order(state, order, targets, 5.)
        self.assertEqual((0., 0., -30.), result['move_position'])
        self.assertEqual('artillery_relocate', result['combat_mode'])
        self.assertIsNone(result['target_id'])
        self.assertFalse(result['fire_allowed'])
        self.assertEqual((0., 0., 0.), tuple(state[k] for k in ('x', 'y', 'z')))
        # Once reached, do not return to the old obstructed server anchor.
        state['z'] = -30.
        held = runtime._artillery_position_order(state, order, targets, 20.)
        self.assertEqual('artillery_hold', held['combat_mode'])
        self.assertEqual((0., 0., -30.), held['move_position'])
        self.assertEqual(1000002, held['target_id'])

    def test_missing_or_stale_evidence_never_moves_an_artillery_hold(self):
        for change in ('absent', 'stale', 'moving', 'pose_changed'):
            runtime, state, order, targets = self._position_fixture()
            if change == 'absent': state.pop('_spg_obstruction')
            elif change == 'stale': state['_spg_obstruction']['last'] = 0.
            elif change == 'moving': state['speed'] = 3.
            else: state['x'] = 1.
            self.assertEqual(order, runtime._artillery_position_order(state, order, targets, 5.))

    def test_base_defense_preempts_a_local_relocation(self):
        runtime, state, order, targets = self._position_fixture()
        runtime._artillery_position_order(state, order, targets, 5.)
        defense = dict(order, combat_mode='base_defense')
        self.assertEqual(defense, runtime._artillery_position_order(state, defense, targets, 6.))
        self.assertNotIn('_spg_position', state)

    def test_no_route_does_not_invent_a_destination_or_teleport(self):
        runtime, state, order, targets = self._position_fixture()
        state['route'] = {}
        self.assertEqual(order, runtime._artillery_position_order(state, order, targets, 5.))
        self.assertEqual('no_safe_rear_waypoint', state['_spg_position_event'])

    def test_arc_status_distinguishes_close_world_hit_and_missing_proof(self):
        from gui.mods.offline_lan_0922.artillery_arc_queue import ArcProbeQueue
        q = ArcProbeQueue()
        candidate = {'arc': 'low', 'path': ((0., 1., 0.), (20., 2., 0.), (200., 1., 0.))}
        q.request('wall', [candidate], (200., 1., 0.), 0.)
        self.assertEqual('pending', q.status('wall', 0.)['state'])
        self.assertEqual(1, q.advance(.1, 4, lambda a, b: (8., 1.4, 0.)))
        status = q.status('wall', .1)
        self.assertEqual('world_blocked', status['reason'])
        self.assertTrue(status['local_blockage'])
        self.assertEqual((8., 1.4, 0.), status['blocks'][0]['hit'])
        self.assertEqual('missing', q.status('other', .1)['state'])
        q.reset()
        self.assertFalse(q.details)

    def test_invalid_or_distant_world_probe_is_not_a_local_relocation_trigger(self):
        from gui.mods.offline_lan_0922.artillery_arc_queue import ArcProbeQueue
        for hit in (False, (80., 2., 0.)):
            q = ArcProbeQueue()
            q.request('wall', [{'arc': 'low', 'path': ((0., 1., 0.), (100., 2., 0.), (200., 1., 0.))}],
                      (200., 1., 0.), 0.)
            q.advance(.1, 4, lambda a, b: hit)
            self.assertFalse(q.status('wall', .1)['local_blockage'])

    def test_contact_loss_revokes_an_existing_artillery_track(self):
        planner = BotPlanner()
        first = self._order(planner, _contact(2, 200, 0, []), 1.)
        self.assertEqual(2, first['target_id'])
        lost = dict(_contact(2, 200, 0, []), visible=False, radio_recipients=[])
        second = self._order(planner, lost, 1.1)
        self.assertIsNone(second['target_id'])
        self.assertFalse(second['fire_allowed'])

    def test_gate_diagnostics_include_why_and_cannot_invoke_world_probe(self):
        runtime, state, order, targets = self._position_fixture()
        state.update(vehicle='test:SPG', shell_index=0, fire_seq=0, gun_aligned=False)
        native_calls = []
        runtime.firing_lane_probe = lambda *args: native_calls.append(args)
        runtime.artillery_status_probe = lambda *args: {
            'planning': {'state': 'pending', 'chord': 2, 'chords': 20},
            'launch': {'state': 'missing'}}
        gun = mock.Mock(); gun.ready.return_value = True
        ammo = mock.Mock(); ammo.can_fire.return_value = True
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            for now in (1., 1.1, 1.2):
                runtime._record_artillery_gate(
                    state, order, targets[1000002], None, gun, ammo, 1., False, True, True, now, None)
        self.assertEqual([], native_calls)
        self.assertEqual(1, output.getvalue().count('[SPG FIRE GATE]'))
        self.assertIn('planning_pending', output.getvalue())
        self.assertNotIn('_spg_position', state)
