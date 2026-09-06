import json
from pathlib import Path
import sys
import unittest


PORT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORT_ROOT / 'server'))

from server_bot_ai import BotPlanner, _order_signature  # noqa: E402
from lan_battle_server import (  # noqa: E402
    BOT_PLANNER_INTERVAL_TICKS,
    CLIENT_BUILD_0922,
    PREBATTLE_SECONDS,
    SIMULATION_WORKER_AUTHORITY_ID,
    TICK_HZ,
    BattleState,
    Player,
    SimulationWorker,
)
from gui.mods.offline_lan_0922.bot_runtime import (  # noqa: E402
    _overlay_live_target_pose,
)


def _profile(class_tag='SPG', roles=None):
    return {
        'class_tag': class_tag,
        'speed': 12.0,
        'dominant_role': ('artillery' if class_tag == 'SPG' else 'support'),
        'desired_range': 650.0 if class_tag == 'SPG' else 180.0,
        'fire_range': 1250.0 if class_tag == 'SPG' else 520.0,
        'roles': dict(roles or {}),
        'shells': [],
    }


def _route(route_id, points, capacity=None, class_weights=None,
           role_weights=None):
    result = {
        'id': route_id,
        'waypoints': [
            {'x': float(x), 'y': 0.0, 'z': float(z), 'hold': bool(hold)}
            for x, z, hold in points
        ],
    }
    if capacity is not None:
        result['capacity'] = int(capacity)
    if class_weights is not None:
        result['class_weights'] = dict(class_weights)
    if role_weights is not None:
        result['role_weights'] = dict(role_weights)
    return result


def _bot(bot_id, team, slot, route, class_tag='SPG', roles=None):
    return {
        'id': bot_id,
        'team': team,
        'slot': slot,
        'health': 1000,
        'profile': _profile(class_tag, roles),
        'route': route,
    }


def _state(bot_id, team, x, z):
    return {
        'id': bot_id,
        'team': team,
        'alive': True,
        'world_pose': True,
        'x': float(x),
        'y': 0.0,
        'z': float(z),
        'yaw': 0.0,
        'health': 1000,
        'max_health': 1000,
        'fire_seq': 0,
        'shell_index': 0,
        'next_shell_index': 0,
        'ammo_remaining': [20],
        'ammo_reload_pending': False,
        'reload_time': 0.0,
        'reload_duration': 20.0,
        'clip': 1,
        'clip_size': 1,
        'burst_active': False,
        'critical': {},
    }


def _weapon_state(raw, reload_time=0.0, clip=1, clip_size=1,
                  ammunition=(20,), reload_pending=False, fire_seq=0,
                  burst_active=False):
    raw.update({
        'fire_seq': int(fire_seq),
        'shell_index': 0,
        'next_shell_index': 0,
        'ammo_remaining': list(ammunition),
        'ammo_reload_pending': bool(reload_pending),
        'reload_time': float(reload_time),
        'reload_duration': 20.0,
        'clip': int(clip),
        'clip_size': int(clip_size),
        'burst_active': bool(burst_active),
    })
    return raw


def _contact(target_id, x, z, observers, class_tag='mediumTank'):
    return {
        'observing_team': 1,
        'target_kind': 'human',
        'target_id': target_id,
        'target_team': 2,
        'visible': True,
        'shootable_by_bot_ids': list(observers),
        'x': float(x),
        'y': 0.0,
        'z': float(z),
        'health': 1000,
        'max_health': 1000,
        'class_tag': class_tag,
    }


def _cover_report(bot_id, target_id, candidate_id='rock', x=12.0,
                  z=0.0):
    return {
        'bot_id': bot_id,
        'target_kind': 'human',
        'target_id': target_id,
        'candidates': [{
            'id': candidate_id,
            'position': {'x': float(x), 'y': 0.0, 'z': float(z)},
            'peek_position': {
                'x': float(x) + 6.0, 'y': 0.0, 'z': float(z) + 4.0,
            },
            'travel_distance': abs(float(x)),
            'route_alignment': 0.8,
            'enemy_occlusion': 0.9,
            'exposure': 0.1,
            'slope': 1.0,
            'water': 0.0,
            'ally_congestion': 0.0,
            'peek_feasible': True,
            'escape_feasible': True,
        }],
    }


def _capture_defense():
    return {
        'capture_bases': {
            '1': [{'id': '1:0', 'x': 0.0, 'y': 0.0, 'z': -100.0}],
            '2': [{'id': '2:0', 'x': 123.5, 'y': 0.0, 'z': 456.25}],
        },
    }


class _CountingPlanner(object):
    def __init__(self, state):
        self.state = state
        self.build_ticks = []
        self.reset_count = 0

    def build_orders(self, *unused_args, **unused_kwargs):
        self.build_ticks.append(self.state.tick)
        return {"revision": len(self.build_ticks), "orders": []}

    def reset(self):
        self.reset_count += 1


class ServerBotTacticsTests(unittest.TestCase):
    def setUp(self):
        self.route = _route('lane', [
            (0, -100, False), (0, 100, False), (0, 500, False),
        ])
        self.manifest = [_bot(
            11, 1, 0, self.route, 'mediumTank', {'support': 1.0})]
        self.manifest[0]['profile'].update({
            'armor': 120.0,
            'dominant_role': 'support',
            'desired_range': 180.0,
            'fire_range': 520.0,
        })
        self.states = [_state(11, 1, 0, 0)]

    def _report(self, planner, contacts):
        players = [
            {'id': raw['target_id'], 'team': 2, 'alive': True}
            for raw in contacts
        ]
        self.assertEqual(len(contacts), planner.report_contacts(
            contacts, planner.known_targets(self.states, players), 1.0))
        return players

    def test_order_signature_ignores_live_target_pose_before_fire_permission(self):
        order = {
            'id': 11, 'target_kind': 'human', 'target_id': 2,
            'fire_allowed': False, 'shell_index': 0,
            'combat_mode': 'advance_contact',
            'aim_position': {'x': 0.0, 'y': 1.0, 'z': 80.0},
            'face_position': {'x': 0.0, 'y': 1.0, 'z': 80.0},
            'move_position': {'x': 0.0, 'y': 1.0, 'z': 80.0},
        }
        moved = dict(order)
        moved.update({
            'aim_position': {'x': 20.0, 'y': 1.0, 'z': 70.0},
            'face_position': {'x': 20.0, 'y': 1.0, 'z': 70.0},
            'move_position': {'x': 20.0, 'y': 1.0, 'z': 70.0},
        })

        self.assertEqual(_order_signature(order), _order_signature(moved))
        for field, value in (
                ('target_id', 3),
                ('fire_allowed', True),
                ('shell_index', 1),
                ('combat_mode', 'engage')):
            changed = dict(moved)
            changed[field] = value
            with self.subTest(field=field):
                self.assertNotEqual(
                    _order_signature(order), _order_signature(changed))

    def test_recent_attacker_preempts_target_lease_and_withdraws(self):
        planner = BotPlanner()
        contacts = [
            _contact(2, 0, 260, [11]),
            _contact(3, 0, 35, [11]),
        ]
        players = self._report(planner, contacts)
        first = planner.build_orders(
            self.manifest, self.states, players, 1.0)['orders'][0]
        self.assertEqual(3, first['target_id'])

        self.assertTrue(planner.report_damage(
            11, 'player', 2, 240, 1.1))
        reaction = planner.build_orders(
            self.manifest, self.states, players, 1.1)['orders'][0]

        self.assertEqual(2, reaction['target_id'])
        self.assertEqual('under_fire_withdraw', reaction['combat_mode'])
        self.assertEqual({'x': 0.0, 'y': 0.0, 'z': -100.0},
                         reaction['move_position'])

    def test_recent_hit_holds_cover_until_exposure_window_expires(self):
        planner = BotPlanner()
        contacts = [_contact(2, 0, 260, [11])]
        players = self._report(planner, contacts)
        known_bots = planner.known_bots(self.manifest, self.states)
        known_targets = planner.known_targets(self.states, players)
        self.assertEqual(1, planner.report_affordances([{
            'bot_id': 11,
            'target_kind': 'human',
            'target_id': 2,
            'candidates': [{
                'id': 'rock',
                'position': {'x': 12.0, 'y': 0.0, 'z': 0.0},
                'peek_position': {'x': 18.0, 'y': 0.0, 'z': 4.0},
                'travel_distance': 12.0,
                'route_alignment': 0.8,
                'enemy_occlusion': 0.9,
                'exposure': 0.1,
                'slope': 1.0,
                'water': 0.0,
                'ally_congestion': 0.0,
                'peek_feasible': True,
                'escape_feasible': True,
            }],
        }], known_bots, known_targets, 1.0))
        planner.report_damage(11, 'player', 2, 200, 1.1)

        approach = planner.build_orders(
            self.manifest, self.states, players, 1.1)['orders'][0]
        self.assertEqual('take_cover', approach['combat_mode'])
        self.states[0]['x'] = 12.0
        holding = planner.build_orders(
            self.manifest, self.states, players, 1.2)['orders'][0]
        self.assertEqual('cover_hold', holding['combat_mode'])
        still_holding = planner.build_orders(
            self.manifest, self.states, players, 5.0)['orders'][0]
        self.assertEqual('cover_hold', still_holding['combat_mode'])
        peeking = planner.build_orders(
            self.manifest, self.states, players, 7.2)['orders'][0]
        self.assertEqual('cover_peek', peeking['combat_mode'])

    def test_low_health_vehicle_retreats_without_waiting_for_a_hit(self):
        planner = BotPlanner()
        contacts = [_contact(2, 0, 150, [11])]
        players = self._report(planner, contacts)
        self.states[0]['health'] = 200

        order = planner.build_orders(
            self.manifest, self.states, players, 1.0)['orders'][0]

        self.assertEqual('low_health_retreat', order['combat_mode'])
        self.assertEqual({'x': 0.0, 'y': 0.0, 'z': -100.0},
                         order['move_position'])

    def test_low_health_retreat_reaches_a_defensive_terminal(self):
        planner = BotPlanner()
        contact = _contact(2, 0, 150, [11])
        players = self._report(planner, [contact])
        self.states[0]['health'] = 200

        retreat = planner.build_orders(
            self.manifest, self.states, players, 1.0)['orders'][0]
        self.states[0].update(retreat['move_position'])
        self.assertEqual(1, planner.report_contacts(
            [contact], planner.known_targets(self.states, players), 2.0))

        holding = planner.build_orders(
            self.manifest, self.states, players, 2.0)['orders'][0]

        self.assertEqual('low_health_retreat', holding['combat_mode'])
        self.assertEqual('low_health_defend', holding['tactical_phase'])
        self.assertEqual(0.0, holding['throttle_override'])
        self.assertEqual(retreat['move_position'], holding['move_position'])
        self.assertTrue(holding['stable_hull_face'])

    def test_pending_shooter_refresh_preserves_movement_but_not_fire(self):
        planner = BotPlanner()
        contact = _contact(2, 0, 150, [11])
        players = self._report(planner, [contact])
        first = planner.build_orders(
            self.manifest, self.states, players, 1.0)['orders'][0]
        self.assertTrue(first['fire_allowed'])

        pending = _contact(2, 0, 150, [])
        self.assertEqual(1, planner.report_contacts(
            [pending], planner.known_targets(self.states, players), 1.5))
        leased = planner.build_orders(
            self.manifest, self.states, players, 1.5)['orders'][0]

        self.assertEqual(2, leased['target_id'])
        self.assertEqual(first['combat_mode'], leased['combat_mode'])
        self.assertEqual(first['move_position'], leased['move_position'])
        self.assertFalse(leased['fire_allowed'])

        self.assertEqual(1, planner.report_contacts(
            [pending], planner.known_targets(self.states, players), 3.1))
        expired = planner.build_orders(
            self.manifest, self.states, players, 3.1)['orders'][0]
        self.assertIsNone(expired['target_id'])
        self.assertEqual('route', expired['combat_mode'])

    def test_ready_shooter_preempts_a_reloading_target_lease(self):
        planner = BotPlanner()
        manifest = [
            _bot(11, 1, 0, self.route, 'mediumTank'),
            _bot(12, 1, 1, self.route, 'mediumTank'),
        ]
        states = [
            _weapon_state(_state(11, 1, 0, 0)),
            _weapon_state(_state(12, 1, 20, 0)),
        ]
        contact = _contact(2, 0, 240, [11, 12])
        contact.update({'health': 500, 'max_health': 1000})
        players = self._report(planner, [contact])

        first = dict((order['id'], order) for order in
                     planner.build_orders(
                         manifest, states, players, 1.0)['orders'])
        self.assertEqual(2, first[11]['target_id'])
        self.assertIsNone(first[12]['target_id'])

        states[0].update({
            'reload_time': 8.0,
            'ammo_reload_pending': True,
        })
        ready_only = _contact(2, 0, 240, [12])
        ready_only.update({'health': 500, 'max_health': 1000})
        self.assertEqual(1, planner.report_contacts(
            [ready_only], planner.known_targets(states, players), 1.5))
        reallocated = dict((order['id'], order) for order in
                           planner.build_orders(
                               manifest, states, players, 1.5)['orders'])

        self.assertEqual(2, reallocated[11]['target_id'])
        self.assertFalse(reallocated[11]['fire_allowed'])
        self.assertEqual(first[11]['combat_mode'],
                         reallocated[11]['combat_mode'])
        self.assertEqual(first[11]['move_position'],
                         reallocated[11]['move_position'])
        self.assertEqual(2, reallocated[12]['target_id'])
        self.assertTrue(reallocated[12]['fire_allowed'])

    def test_unavailable_weapons_do_not_consume_focus_capacity(self):
        unavailable_states = (
            (True, _weapon_state(
                _state(11, 1, 0, 0), reload_time=8.0,
                reload_pending=True)),
            (True, _weapon_state(
                _state(11, 1, 0, 0), reload_time=20.0,
                clip=0, clip_size=3, reload_pending=True)),
            (False, _weapon_state(
                _state(11, 1, 0, 0), ammunition=(0,))),
            (True, _weapon_state(_state(11, 1, 0, 0))),
        )
        unavailable_states[-1][1]['critical'] = {
            'destroyed': ['gunHealth'],
        }

        for keeps_movement_target, unavailable in unavailable_states:
            with self.subTest(state=unavailable):
                planner = BotPlanner()
                manifest = [
                    _bot(11, 1, 0, self.route, 'mediumTank'),
                    _bot(12, 1, 1, self.route, 'mediumTank'),
                ]
                states = [
                    unavailable,
                    _weapon_state(_state(12, 1, 20, 0)),
                ]
                contact = _contact(2, 0, 240, [11, 12])
                contact.update({'health': 500, 'max_health': 1000})
                players = self._report(planner, [contact])

                orders = dict((order['id'], order) for order in
                              planner.build_orders(
                                  manifest, states, players, 1.0)['orders'])

                if keeps_movement_target:
                    self.assertEqual(2, orders[11]['target_id'])
                    self.assertFalse(orders[11]['fire_allowed'])
                else:
                    self.assertIsNone(orders[11]['target_id'])
                    self.assertEqual('route', orders[11]['combat_mode'])
                    self.assertNotEqual(
                        {'x': 0.0, 'y': 0.0, 'z': 0.0},
                        orders[11]['move_position'])
                self.assertEqual(2, orders[12]['target_id'])
                self.assertTrue(orders[12]['fire_allowed'])

    def test_reloading_vehicle_uses_a_new_close_threat_for_withdrawal(self):
        planner = BotPlanner()
        states = [_weapon_state(
            _state(11, 1, 0, 0), reload_time=8.0,
            reload_pending=True)]
        contact = _contact(2, 0, 20, [11])
        players = self._report(planner, [contact])

        order = planner.build_orders(
            self.manifest, states, players, 1.0)['orders'][0]

        self.assertEqual(2, order['target_id'])
        self.assertEqual('withdraw', order['combat_mode'])
        self.assertFalse(order['fire_allowed'])

    def test_exhausted_weapon_releases_an_old_lease(self):
        planner = BotPlanner()
        manifest = [
            _bot(11, 1, 0, self.route, 'mediumTank'),
            _bot(12, 1, 1, self.route, 'mediumTank'),
        ]
        states = [
            _weapon_state(_state(11, 1, 0, 0)),
            _weapon_state(_state(12, 1, 20, 0)),
        ]
        contact = _contact(2, 0, 240, [11, 12])
        contact.update({'health': 500, 'max_health': 1000})
        players = self._report(planner, [contact])
        first = dict((order['id'], order) for order in
                     planner.build_orders(
                         manifest, states, players, 1.0)['orders'])
        self.assertEqual(2, first[11]['target_id'])

        states[0].update({
            'ammo_remaining': [0],
            'clip': 0,
        })
        ready_only = _contact(2, 0, 240, [12])
        ready_only.update({'health': 500, 'max_health': 1000})
        self.assertEqual(1, planner.report_contacts(
            [ready_only], planner.known_targets(states, players), 1.5))
        orders = dict((order['id'], order) for order in
                      planner.build_orders(
                          manifest, states, players, 1.5)['orders'])

        self.assertIsNone(orders[11]['target_id'])
        self.assertNotIn(11, planner._target_assignments)
        self.assertEqual(2, orders[12]['target_id'])
        self.assertTrue(orders[12]['fire_allowed'])

    def test_cover_movement_lease_does_not_reserve_a_firing_slot(self):
        planner = BotPlanner()
        manifest = [
            _bot(11, 1, 0, self.route, 'mediumTank'),
            _bot(12, 1, 1, self.route, 'mediumTank'),
        ]
        states = [
            _weapon_state(_state(11, 1, 0, 0)),
            _weapon_state(_state(12, 1, 20, 0)),
        ]
        initial_contact = _contact(2, 0, 150, [11])
        initial_contact.update({'health': 500, 'max_health': 1000})
        players = self._report(planner, [initial_contact])
        self.assertEqual(1, planner.report_affordances(
            [_cover_report(11, 2)], planner.known_bots(manifest, states),
            planner.known_targets(states, players), 1.0))
        approach = dict((order['id'], order) for order in
                        planner.build_orders(
                            manifest, states, players, 1.0)['orders'])
        self.assertEqual('take_cover', approach[11]['combat_mode'])
        states[0].update(approach[11]['move_position'])

        ready_only = _contact(2, 0, 150, [12])
        ready_only.update({'health': 500, 'max_health': 1000})
        self.assertEqual(1, planner.report_contacts(
            [ready_only], planner.known_targets(states, players), 2.0))
        holding = dict((order['id'], order) for order in
                       planner.build_orders(
                           manifest, states, players, 2.0)['orders'])

        self.assertEqual('cover_hold', holding[11]['combat_mode'])
        self.assertFalse(holding[11]['fire_allowed'])
        self.assertEqual(2, holding[12]['target_id'])
        self.assertTrue(holding[12]['fire_allowed'])

        peeking = dict((order['id'], order) for order in
                       planner.build_orders(
                           manifest, states, players, 4.0)['orders'])
        self.assertEqual('cover_peek', peeking[11]['combat_mode'])
        self.assertFalse(peeking[11]['fire_allowed'])
        self.assertEqual(2, peeking[12]['target_id'])
        self.assertTrue(peeking[12]['fire_allowed'])

    def test_destroyed_gun_keeps_its_movement_lease_until_repaired(self):
        planner = BotPlanner()
        manifest = [
            _bot(11, 1, 0, self.route, 'mediumTank'),
            _bot(12, 1, 1, self.route, 'mediumTank'),
        ]
        states = [
            _weapon_state(_state(11, 1, 0, 0)),
            _weapon_state(_state(12, 1, 20, 0)),
        ]
        contact = _contact(2, 0, 240, [11, 12])
        contact.update({'health': 500, 'max_health': 1000})
        players = self._report(planner, [contact])
        first = dict((order['id'], order) for order in
                     planner.build_orders(
                         manifest, states, players, 1.0)['orders'])
        self.assertEqual(2, first[11]['target_id'])

        states[0]['critical'] = {'destroyed': ['gunHealth']}
        self.assertEqual(1, planner.report_contacts(
            [contact], planner.known_targets(states, players), 1.5))
        destroyed = dict((order['id'], order) for order in
                         planner.build_orders(
                             manifest, states, players, 1.5)['orders'])
        self.assertEqual(2, destroyed[11]['target_id'])
        self.assertFalse(destroyed[11]['fire_allowed'])
        self.assertEqual(first[11]['move_position'],
                         destroyed[11]['move_position'])
        self.assertEqual(2, destroyed[12]['target_id'])
        self.assertTrue(destroyed[12]['fire_allowed'])

        states[0]['critical'] = {'destroyed': []}
        self.assertEqual(1, planner.report_contacts(
            [contact], planner.known_targets(states, players), 19.6))
        repaired = dict((order['id'], order) for order in
                        planner.build_orders(
                            manifest, states, players, 19.6)['orders'])
        self.assertEqual(2, repaired[11]['target_id'])
        self.assertTrue(repaired[11]['fire_allowed'])
        self.assertIsNone(repaired[12]['target_id'])

    def test_active_burst_keeps_focus_while_reload_is_pending(self):
        planner = BotPlanner()
        manifest = [
            _bot(11, 1, 0, self.route, 'mediumTank'),
            _bot(12, 1, 1, self.route, 'mediumTank'),
        ]
        states = [
            _weapon_state(
                _state(11, 1, 0, 0), reload_time=1.0,
                clip=2, clip_size=3, reload_pending=True,
                fire_seq=1, burst_active=True),
            _weapon_state(_state(12, 1, 20, 0)),
        ]
        contact = _contact(2, 0, 240, [11, 12])
        contact.update({'health': 500, 'max_health': 1000})
        players = self._report(planner, [contact])

        orders = dict((order['id'], order) for order in
                      planner.build_orders(
                          manifest, states, players, 1.0)['orders'])

        self.assertEqual(2, orders[11]['target_id'])
        self.assertTrue(orders[11]['fire_allowed'])
        self.assertIsNone(orders[12]['target_id'])

    def test_minor_answerable_hit_does_not_force_withdrawal(self):
        planner = BotPlanner()
        self.states[0] = _weapon_state(self.states[0])
        contact = _contact(2, 0, 150, [11])
        players = self._report(planner, [contact])
        self.assertTrue(planner.report_damage(
            11, 'player', 2, 1, 1.1))

        order = planner.build_orders(
            self.manifest, self.states, players, 1.1)['orders'][0]

        self.assertEqual(2, order['target_id'])
        self.assertTrue(order['fire_allowed'])
        self.assertNotIn(order['combat_mode'], (
            'under_fire_withdraw', 'under_fire_hold'))

    def test_minor_unanswerable_hit_uses_nearby_support(self):
        contact = _contact(2, 0, 50, [12])
        players = [{'id': 2, 'team': 2, 'alive': True}]

        unsupported = BotPlanner()
        solo_states = [_weapon_state(_state(11, 1, 0, 0))]
        self.assertEqual(1, unsupported.report_contacts(
            [contact], unsupported.known_targets(solo_states, players), 1.0))
        self.assertTrue(unsupported.report_damage(
            11, 'player', 2, 1, 1.1))
        solo = unsupported.build_orders(
            self.manifest, solo_states, players, 1.1)['orders'][0]
        self.assertEqual('under_fire_withdraw', solo['combat_mode'])

        supported = BotPlanner()
        manifest = [
            _bot(11, 1, 0, self.route, 'mediumTank'),
            _bot(12, 1, 1, self.route, 'mediumTank'),
        ]
        states = [
            _weapon_state(_state(11, 1, 0, 0)),
            _weapon_state(_state(12, 1, 0, 0)),
        ]
        self.assertEqual(1, supported.report_contacts(
            [contact], supported.known_targets(states, players), 1.0))
        self.assertTrue(supported.report_damage(
            11, 'player', 2, 1, 1.1))
        orders = dict((order['id'], order) for order in
                      supported.build_orders(
                          manifest, states, players, 1.1)['orders'])

        self.assertEqual('route', orders[11]['combat_mode'])
        self.assertEqual(2, orders[11]['target_id'])
        self.assertFalse(orders[11]['fire_allowed'])
        self.assertEqual(2, orders[12]['target_id'])
        self.assertTrue(orders[12]['fire_allowed'])

        for unavailable in ('no_lane', 'empty', 'destroyed'):
            with self.subTest(unavailable=unavailable):
                planner = BotPlanner()
                ally = _weapon_state(_state(12, 1, 0, 0))
                shooters = [12]
                if unavailable == 'no_lane':
                    shooters = []
                elif unavailable == 'empty':
                    ally.update({'ammo_remaining': [0], 'clip': 0})
                else:
                    ally['critical'] = {'destroyed': ['gunHealth']}
                blocked_contact = _contact(2, 0, 50, shooters)
                blocked_states = [
                    _weapon_state(_state(11, 1, 0, 0)), ally,
                ]
                self.assertEqual(1, planner.report_contacts(
                    [blocked_contact], planner.known_targets(
                        blocked_states, players), 1.0))
                self.assertTrue(planner.report_damage(
                    11, 'player', 2, 1, 1.1))

                blocked = dict((order['id'], order) for order in
                               planner.build_orders(
                                   manifest, blocked_states, players,
                                   1.1)['orders'])

                self.assertEqual(
                    'under_fire_withdraw', blocked[11]['combat_mode'])

    def test_dead_recent_attacker_is_cleared_before_ordering(self):
        planner = BotPlanner()
        contact = _contact(2, 0, 150, [11])
        players = self._report(planner, [contact])
        self.assertTrue(planner.report_damage(
            11, 'player', 2, 240, 1.1))
        players[0]['alive'] = False

        order = planner.build_orders(
            self.manifest, self.states, players, 1.2)['orders'][0]

        self.assertNotIn(11, planner._recent_hits)
        self.assertIsNone(order['target_id'])
        self.assertEqual('route', order['combat_mode'])

    def test_cover_waits_for_reload_and_completes_the_exposed_magazine(self):
        planner = BotPlanner()
        self.states[0] = _weapon_state(
            self.states[0], clip=3, clip_size=3)
        contact = _contact(2, 0, 150, [11])
        players = self._report(planner, [contact])
        known_bots = planner.known_bots(self.manifest, self.states)
        known_targets = planner.known_targets(self.states, players)
        self.assertEqual(1, planner.report_affordances(
            [_cover_report(11, 2)], known_bots, known_targets, 1.0))

        approach = planner.build_orders(
            self.manifest, self.states, players, 1.0)['orders'][0]
        self.assertEqual('take_cover', approach['combat_mode'])
        self.states[0].update(approach['move_position'])
        self.states[0].update({
            'reload_time': 10.0,
            'ammo_reload_pending': True,
        })
        holding = planner.build_orders(
            self.manifest, self.states, players, 2.0)['orders'][0]
        self.assertEqual('cover_hold', holding['combat_mode'])

        self.states[0]['reload_time'] = 8.0
        blocked_contact = _contact(2, 0, 150, [])
        self.assertEqual(1, planner.report_contacts(
            [blocked_contact], planner.known_targets(
                self.states, players), 4.0))
        blocked = planner.build_orders(
            self.manifest, self.states, players, 4.0)['orders'][0]
        self.assertEqual('cover_hold', blocked['combat_mode'])
        self.assertFalse(blocked['fire_allowed'])

        self.states[0].update({
            'reload_time': 0.0,
            'ammo_reload_pending': False,
        })
        peeking = planner.build_orders(
            self.manifest, self.states, players, 4.1)['orders'][0]
        self.assertEqual('cover_peek', peeking['combat_mode'])
        self.assertFalse(peeking['fire_allowed'])
        self.states[0].update(peeking['move_position'])
        at_peek = planner.build_orders(
            self.manifest, self.states, players, 4.2)['orders'][0]
        self.assertEqual('cover_peek', at_peek['combat_mode'])

        self.states[0].update({
            'fire_seq': 1,
            'clip': 2,
            'ammo_remaining': [19],
            'reload_time': 0.8,
            'reload_duration': 1.0,
            'ammo_reload_pending': True,
        })
        followup = planner.build_orders(
            self.manifest, self.states, players, 4.3)['orders'][0]
        self.assertEqual('cover_peek', followup['combat_mode'])

        self.states[0].update({
            'fire_seq': 2,
            'clip': 1,
            'ammo_remaining': [18],
            'reload_time': 0.8,
            'reload_duration': 1.0,
            'ammo_reload_pending': True,
            'burst_active': True,
        })
        bursting = planner.build_orders(
            self.manifest, self.states, players, 4.4)['orders'][0]
        self.assertEqual('cover_peek', bursting['combat_mode'])

        self.states[0].update({
            'fire_seq': 3,
            'clip': 0,
            'ammo_remaining': [17],
            'reload_time': 20.0,
            'reload_duration': 20.0,
            'burst_active': False,
        })
        returning = planner.build_orders(
            self.manifest, self.states, players, 4.5)['orders'][0]
        self.assertEqual('cover_return', returning['combat_mode'])

    def test_cover_lease_outlives_full_roster_refresh_cycle(self):
        planner = BotPlanner()
        contact = _contact(2, 0, 150, [11])
        players = self._report(planner, [contact])
        known_bots = planner.known_bots(self.manifest, self.states)
        known_targets = planner.known_targets(self.states, players)
        self.assertEqual(1, planner.report_affordances(
            [_cover_report(11, 2)], known_bots, known_targets, 1.0))
        approach = planner.build_orders(
            self.manifest, self.states, players, 1.0)['orders'][0]
        self.states[0].update(approach['move_position'])
        holding = planner.build_orders(
            self.manifest, self.states, players, 2.0)['orders'][0]
        self.assertEqual('cover_hold', holding['combat_mode'])

        self.assertEqual(1, planner.report_contacts(
            [contact], planner.known_targets(self.states, players), 11.5))
        refreshed = planner.build_orders(
            self.manifest, self.states, players, 11.5)['orders'][0]

        self.assertEqual('rock', refreshed['cover_id'])
        self.assertIn(refreshed['combat_mode'], (
            'cover_hold', 'cover_peek', 'cover_return'))

    def test_unreachable_cover_candidate_has_a_bounded_exit(self):
        planner = BotPlanner()
        contact = _contact(2, 0, 150, [11])
        players = self._report(planner, [contact])
        known_bots = planner.known_bots(self.manifest, self.states)
        known_targets = planner.known_targets(self.states, players)
        self.assertEqual(1, planner.report_affordances(
            [_cover_report(11, 2)], known_bots, known_targets, 1.0))
        first = planner.build_orders(
            self.manifest, self.states, players, 1.0)['orders'][0]
        self.assertEqual('take_cover', first['combat_mode'])

        self.assertEqual(1, planner.report_contacts(
            [contact], planner.known_targets(self.states, players), 9.2))
        terminal = planner.build_orders(
            self.manifest, self.states, players, 9.2)['orders'][0]

        self.assertNotIn(terminal['combat_mode'], (
            'take_cover', 'cover_hold', 'cover_peek', 'cover_return'))
        self.assertIn('rock', planner._cover_failures[11])

    def test_contact_free_capturer_follows_lane_before_enemy_base(self):
        planner = BotPlanner()
        defense = _capture_defense()

        approach = planner.build_orders(
            self.manifest, self.states, [], 1.0, defense)['orders'][0]

        self.assertEqual('route', approach['combat_mode'])
        self.assertEqual({'x': 0.0, 'y': 0.0, 'z': 100.0},
                         approach['move_position'])
        self.assertNotIn('capture_base_id', approach)

        self.states[0]['z'] = 100.0
        order = planner.build_orders(
            self.manifest, self.states, [], 2.0, defense)['orders'][0]

        self.assertEqual('base_capture', order['combat_mode'])
        self.assertEqual('2:0', order['capture_base_id'])
        self.assertEqual({'x': 123.5, 'y': 0.0, 'z': 456.25},
                         order['move_position'])
        self.assertEqual(order['move_position'], order['aim_position'])
        self.assertEqual(order['move_position'], order['face_position'])

    def test_capture_squad_is_capped_while_other_bots_keep_routes(self):
        planner = BotPlanner()
        manifest = [
            _bot(100 + index, 1, index, self.route, 'mediumTank')
            for index in range(15)
        ]
        states = [
            _state(bot['id'], 1, 0, index * 2)
            for index, bot in enumerate(manifest)
        ]

        approach = planner.build_orders(
            manifest, states, [], 1.0, _capture_defense())['orders']
        selected_ids = set(planner._base_capture[1]['bot_ids'])
        self.assertEqual(3, len(selected_ids))
        self.assertFalse(any(order['combat_mode'] == 'base_capture'
                             for order in approach))

        for state in states:
            state['z'] = 100.0
        orders = planner.build_orders(
            manifest, states, [], 2.0, _capture_defense())['orders']
        capture = [order for order in orders
                   if order['combat_mode'] == 'base_capture']
        screen = [order for order in orders
                  if order['combat_mode'] == 'base_screen']

        self.assertEqual(3, len(capture))
        self.assertEqual(selected_ids, {order['id'] for order in capture})
        self.assertEqual(12, len(screen))
        self.assertTrue(all(order['route_id'] == 'lane' for order in screen))
        self.assertTrue(all('capture_base_id' not in order
                            for order in screen))

    def test_capture_screen_does_not_stop_at_an_intermediate_waypoint(self):
        planner = BotPlanner()
        route = _route('long-lane', [
            (0, -100, False), (0, 0, False), (0, 100, False),
            (0, 200, False), (0, 300, False), (0, 500, False),
        ])
        manifest = [
            _bot(401 + index, 1, index, route, 'mediumTank')
            for index in range(4)
        ]
        states = [
            _state(401, 1, 0, 300),
            _state(402, 1, 0, 300),
            _state(403, 1, 0, 300),
            _state(404, 1, 0, 50),
        ]
        defense = _capture_defense()

        first = dict((order['id'], order) for order in
                     planner.build_orders(
                         manifest, states, [], 1.0, defense)['orders'])
        self.assertEqual('route', first[404]['combat_mode'])
        self.assertEqual(2, first[404]['route_index'])

        # The route advances at 13 metres, while screen arrival uses 15. A
        # vehicle inside that two-metre band at an intermediate point must
        # keep driving instead of accepting a permanent intentional hold.
        states[3]['z'] = 86.0
        intermediate = dict((order['id'], order) for order in
                            planner.build_orders(
                                manifest, states, [], 2.0,
                                defense)['orders'])
        self.assertEqual('route', intermediate[404]['combat_mode'])
        self.assertEqual(2, intermediate[404]['route_index'])
        self.assertEqual(100.0, intermediate[404]['move_position']['z'])
        self.assertIsNone(intermediate[404]['throttle_override'])

        states[3]['z'] = 100.0
        planner.build_orders(manifest, states, [], 3.0, defense)
        states[3]['z'] = 200.0
        planner.build_orders(manifest, states, [], 4.0, defense)
        states[3]['z'] = 286.0
        staged = dict((order['id'], order) for order in
                      planner.build_orders(
                          manifest, states, [], 5.0, defense)['orders'])
        self.assertEqual('base_screen', staged[404]['combat_mode'])
        self.assertEqual(4, staged[404]['route_index'])
        self.assertEqual(0.0, staged[404]['throttle_override'])

    def test_malinovka_shared_connector_advances_through_forward_corridor(self):
        graph = json.loads(
            (PORT_ROOT / 'navgraphs' / '02_malinovka.json').read_text())
        baked = next(
            route for route in graph['routes']['2']
            if route['id'] == 'central_field')
        route = _route(
            baked['id'],
            [(point[0], point[1], point[2])
             for point in baked['waypoints']])
        bot = _bot(24, 2, 0, route, 'AT-SPG')
        bot['state'] = _state(24, 2, -360.68, 104.85)
        planner = BotPlanner()
        planner._route_states[24] = {
            'index': 1,
            'route_id': 'central_field',
            'join_index': 1,
            'join_anchor': {'x': -374.0, 'y': 0.0, 'z': 110.0},
        }

        route_id, index, point, anchor, route_join = planner._route(bot, 1.0)

        self.assertEqual('central_field', route_id)
        self.assertEqual(3, index)
        self.assertEqual({'x': -270.0, 'y': 0.0, 'z': 174.0}, point)
        self.assertEqual({'x': -362.0, 'y': 0.0, 'z': 106.0}, anchor)
        self.assertFalse(route_join)

    def _malinovka_east_hill_bot(self, speed):
        """The shipped 02_malinovka team-1 slot 2 line-up and its lane.

        That slot parks at (70, -366) facing -0.73 rad while its authored first
        connector lies 184 m away at 1.83 rad off the hull - one of 246 shipped
        spawn slots, across 33 of the 41 baked maps, where the rear-facing
        filter discarded the reviewed egress.
        """
        graph = json.loads(
            (PORT_ROOT / 'navgraphs' / '02_malinovka.json').read_text())
        baked = next(
            route for route in graph['routes']['1']
            if route['id'] == 'east_hill_loop')
        route = _route(
            baked['id'],
            [(point[0], point[1], point[2])
             for point in baked['waypoints']])
        bot = _bot(24, 1, 2, route, 'mediumTank')
        bot['state'] = _state(24, 1, 70.0, -366.0)
        bot['state']['yaw'] = -0.7306
        bot['state']['speed'] = speed
        return bot

    def test_parked_spawn_yaw_keeps_the_authored_first_connector(self):
        """A parking orientation is not a travel direction."""
        planner = BotPlanner()

        route_id, index, point, unused_anchor, route_join = planner._route(
            self._malinovka_east_hill_bot(0.0), 1.0)

        self.assertEqual('east_hill_loop', route_id)
        self.assertEqual(1, index)
        self.assertEqual({'x': 234.0, 'y': 0.0, 'z': -282.0}, point)
        self.assertTrue(route_join)

    def test_a_moving_hull_still_skips_a_rear_facing_connector(self):
        """Once under way the hull yaw is a real travel direction again."""
        planner = BotPlanner()

        route_id, index, point, unused_anchor, unused_join = planner._route(
            self._malinovka_east_hill_bot(6.0), 1.0)

        self.assertEqual('east_hill_loop', route_id)
        self.assertEqual(3, index)
        self.assertEqual({'x': 430.0, 'y': 0.0, 'z': -98.0}, point)

    def test_reverse_recovery_keeps_the_nearest_connector_after_reset(self):
        """Negative speed is recovery, not a strategic travel heading."""
        planner = BotPlanner()
        bot = self._malinovka_east_hill_bot(6.0)
        unused_route, first_index, unused_point, unused_anchor, unused_join = \
            planner._route(bot, 1.0)
        self.assertEqual(3, first_index)

        planner._route_states.pop(bot['id'])
        bot['state']['speed'] = -6.0
        route_id, index, point, unused_anchor, route_join = planner._route(
            bot, 2.0)

        self.assertEqual('east_hill_loop', route_id)
        self.assertEqual(1, index)
        self.assertEqual({'x': 234.0, 'y': 0.0, 'z': -282.0}, point)
        self.assertTrue(route_join)

    def test_route_corridor_does_not_accept_lateral_or_distant_bypasses(self):
        route = _route('bounded-corridor', [
            (0, -20, False), (0, 0, False), (12, 0, False),
            (80, 80, False),
        ])
        planner = BotPlanner()

        for bot_id, x, z in ((25, 8.0, 13.1), (26, 40.0, 0.0)):
            bot = _bot(bot_id, 1, 0, route, 'mediumTank')
            bot['state'] = _state(bot_id, 1, x, z)
            planner._route_states[bot_id] = {
                'index': 1,
                'route_id': 'bounded-corridor',
                'join_index': 1,
                'join_anchor': {'x': 0.0, 'y': 0.0, 'z': -20.0},
            }

            unused_route_id, index, point, unused_anchor, unused_join = (
                planner._route(bot, 1.0))

            self.assertEqual(1, index)
            self.assertEqual({'x': 0.0, 'y': 0.0, 'z': 0.0}, point)

    def test_spgs_yield_capture_slots_to_regular_vehicles(self):
        planner = BotPlanner()
        manifest = [
            _bot(201, 1, 0, self.route, 'mediumTank'),
            _bot(202, 1, 1, self.route, 'heavyTank'),
            _bot(203, 1, 2, self.route, 'SPG'),
            _bot(204, 1, 3, self.route, 'SPG'),
            _bot(205, 1, 4, self.route, 'SPG'),
        ]
        states = [_state(bot['id'], 1, 0, 0) for bot in manifest]

        planner.build_orders(
            manifest, states, [], 1.0, _capture_defense())['orders']
        capture_ids = set(planner._base_capture[1]['bot_ids'])

        self.assertEqual({201, 202}, capture_ids)

    def test_capture_squad_is_stable_across_a_contact_cycle(self):
        planner = BotPlanner()
        manifest = [
            _bot(300 + index, 1, index, self.route, 'mediumTank')
            for index in range(5)
        ]
        states = [
            _state(bot['id'], 1, index * 25, 0)
            for index, bot in enumerate(manifest)
        ]
        defense = _capture_defense()
        planner.build_orders(manifest, states, [], 1.0, defense)
        initial_ids = set(planner._base_capture[1]['bot_ids'])
        self.assertEqual(3, len(initial_ids))

        contact = _contact(900, 0, 400, [])
        players = [{'id': 900, 'team': 2, 'alive': True}]
        self.assertEqual(1, planner.report_contacts(
            [contact], planner.known_targets(states, players), 2.0))
        engaged = planner.build_orders(
            manifest, states, players, 2.0, defense)['orders']
        self.assertFalse(any(order['combat_mode'] == 'base_capture'
                             for order in engaged))

        for state in states:
            state['z'] = (-900.0 if state['id'] in initial_ids else 450.0)
        planner.build_orders(manifest, states, players, 10.1, defense)
        resumed_ids = set(planner._base_capture[1]['bot_ids'])
        self.assertEqual(initial_ids, resumed_ids)

    def test_capture_squad_replaces_only_a_dead_member(self):
        planner = BotPlanner()
        manifest = [
            _bot(400 + index, 1, index, self.route, 'mediumTank')
            for index in range(5)
        ]
        states = [
            _state(bot['id'], 1, index * 20, 0)
            for index, bot in enumerate(manifest)
        ]
        defense = _capture_defense()
        planner.build_orders(manifest, states, [], 1.0, defense)
        initial_ids = set(planner._base_capture[1]['bot_ids'])
        lost_id = min(initial_ids)
        for state in states:
            if state['id'] == lost_id:
                state['alive'] = False
                break

        planner.build_orders(manifest, states, [], 2.0, defense)
        updated_ids = set(planner._base_capture[1]['bot_ids'])

        self.assertEqual(3, len(updated_ids))
        self.assertEqual(initial_ids - {lost_id},
                         updated_ids.intersection(initial_ids))
        self.assertNotIn(lost_id, updated_ids)

    def test_known_enemy_keeps_the_route_instead_of_starting_base_capture(self):
        planner = BotPlanner()
        contact = _contact(2, 0, 400, [])
        players = self._report(planner, [contact])
        defense = _capture_defense()

        order = planner.build_orders(
            self.manifest, self.states, players, 1.0, defense)['orders'][0]

        self.assertEqual('route', order['combat_mode'])
        self.assertNotIn('capture_base_id', order)

    def test_two_wide_enemy_lanes_trigger_crossfire_withdrawal(self):
        planner = BotPlanner()
        contacts = [
            _contact(2, -130, 130, [11]),
            _contact(3, 130, 130, [11]),
        ]
        for contact in contacts:
            contact['threatened_bot_ids'] = [11]
        players = self._report(planner, contacts)

        order = planner.build_orders(
            self.manifest, self.states, players, 1.0)['orders'][0]

        live_bot = planner._alive_bots(self.manifest, self.states)[0]
        self.assertGreaterEqual(planner._crossfire_risk(
            live_bot, list(planner._contacts[1].values())), 0.35)
        self.assertEqual('crossfire_withdraw', order['combat_mode'])

    def test_outgoing_firing_lanes_do_not_claim_incoming_crossfire(self):
        planner = BotPlanner()
        contacts = [
            _contact(2, -130, 130, [11]),
            _contact(3, 130, 130, [11]),
        ]
        players = self._report(planner, contacts)

        order = planner.build_orders(
            self.manifest, self.states, players, 1.0)['orders'][0]
        live_bot = planner._alive_bots(self.manifest, self.states)[0]

        self.assertIsNone(planner._crossfire_risk(
            live_bot, list(planner._contacts[1].values())))
        self.assertNotIn(order['combat_mode'], (
            'crossfire_withdraw', 'crossfire_hold'))

    def test_support_requires_a_ready_weapon_and_current_firing_lane(self):
        contact = _contact(2, 0, 380, [12], 'heavyTank')
        player = {'id': 2, 'team': 2, 'alive': True}
        manifest = [
            _bot(12, 1, 0, self.route, 'mediumTank', {'support': 1.0}),
            _bot(13, 1, 1, self.route, 'mediumTank', {'support': 1.0}),
        ]
        for bot in manifest:
            bot['profile'].update({
                'dominant_role': 'support', 'desired_range': 200.0,
                'fire_range': 520.0,
            })

        for reason in ('no_lane', 'reloading'):
            with self.subTest(reason=reason):
                planner = BotPlanner()
                states = [
                    _weapon_state(_state(12, 1, 0, 0)),
                    _weapon_state(_state(13, 1, 5, 0)),
                ]
                sample = dict(contact)
                if reason == 'reloading':
                    sample['shootable_by_bot_ids'] = [12, 13]
                    states[1].update({
                        'reload_time': 8.0,
                        'ammo_reload_pending': True,
                    })
                self.assertEqual(1, planner.report_contacts(
                    [sample], planner.known_targets(states, [player]), 1.0))

                orders = dict((value['id'], value) for value in
                              planner.build_orders(
                                  manifest, states, [player], 1.0)['orders'])

                self.assertEqual('support_hold', orders[12]['combat_mode'])

    def test_focus_capacity_uses_selected_shell_damage_with_a_spare_shot(self):
        planner = BotPlanner()
        manifest = [
            _bot(bot_id, 1, bot_id, self.route, 'mediumTank')
            for bot_id in (11, 12, 13, 14)
        ]
        for bot in manifest:
            bot['profile']['shells'] = [{
                'index': 0,
                'kind': 'ARMOR_PIERCING',
                'penetration': 200.0,
                'damage': 800.0,
            }]
        states = [
            _weapon_state(_state(bot['id'], 1, 0, 0))
            for bot in manifest
        ]
        contact = _contact(2, 0, 200, [11, 12, 13, 14])
        contact.update({
            'health': 1600,
            'max_health': 2000,
            'armor': 100.0,
        })
        player = {'id': 2, 'team': 2, 'alive': True}
        self.assertEqual(1, planner.report_contacts(
            [contact], planner.known_targets(states, [player]), 1.0))

        orders = planner.build_orders(
            manifest, states, [player], 1.0)['orders']

        self.assertEqual(3, sum(order['target_id'] == 2
                                for order in orders))

    def test_flanker_uses_ally_geometry_and_leaves_one_shooter_holding(self):
        planner = BotPlanner()
        manifest = [
            _bot(12, 1, 0, self.route, 'mediumTank', {
                'support': 1.0, 'flanker': 0.1,
            }),
            _bot(14, 1, 1, self.route, 'mediumTank', {
                'support': 0.1, 'flanker': 1.0,
            }),
        ]
        for bot in manifest:
            bot['profile'].update({
                'dominant_role': 'support', 'desired_range': 160.0,
                'fire_range': 520.0,
            })
        states = [
            _weapon_state(_state(12, 1, -80, -180)),
            _weapon_state(_state(14, 1, 0, -230)),
        ]
        contact = _contact(2, 0, 0, [12, 14])
        contact.update({'health': 2000, 'max_health': 2000})
        player = {'id': 2, 'team': 2, 'alive': True}
        self.assertEqual(1, planner.report_contacts(
            [contact], planner.known_targets(states, [player]), 1.0))

        orders = dict((order['id'], order) for order in
                      planner.build_orders(
                          manifest, states, [player], 1.0)['orders'])

        self.assertEqual('flank', orders[14]['combat_mode'])
        self.assertGreater(orders[14]['move_position']['x'], 0.0)
        self.assertNotEqual('flank', orders[12]['combat_mode'])
        self.assertTrue(orders[12]['fire_allowed'])

    def test_radio_enemy_focus_still_requires_the_recipient_firing_lane(self):
        planner = BotPlanner()
        contacts = [
            _contact(2, 0, 35, [11]),
            _contact(3, 0, 180, [11]),
        ]
        players = self._report(planner, contacts)
        team_order = {
            'command_id': '1:1:1',
            'command': 'ATTACKENEMY',
            'team': 1,
            'issuer_id': 1,
            'issued_tick': 100,
            'expires_tick': 200,
            'recipient_bot_ids': [11],
            'target_kind': 'player',
            'target_id': 3,
        }

        commanded = planner.build_orders(
            self.manifest, self.states, players, 1.0,
            team_orders=[team_order])['orders'][0]
        self.assertEqual(3, commanded['target_id'])
        self.assertEqual('ATTACKENEMY', commanded['team_command'])

        blocked = dict(contacts[1])
        blocked['shootable_by_bot_ids'] = []
        self.assertEqual(1, planner.report_contacts(
            [blocked], planner.known_targets(self.states, players), 1.1))
        fallback = planner.build_orders(
            self.manifest, self.states, players, 1.1,
            team_orders=[team_order])['orders'][0]

        self.assertEqual(2, fallback['target_id'])

    def test_radio_movement_orders_use_validated_live_positions(self):
        issuer = {
            'id': 1, 'team': 1, 'alive': True, 'world_pose': True,
            'x': 45.0, 'y': 2.0, 'z': 60.0,
        }
        base_order = {
            'command_id': '1:1:1',
            'team': 1,
            'issuer_id': 1,
            'issued_tick': 100,
            'expires_tick': 200,
            'recipient_bot_ids': [11],
        }
        cases = (
            ('FOLLOWME', 'team_follow',
             {'x': 45.0, 'y': 2.0, 'z': 42.0}),
            ('STOP', 'team_stop',
             {'x': 0.0, 'y': 0.0, 'z': 0.0}),
            ('TURNBACK', 'team_turnback',
             {'x': 0.0, 'y': 0.0, 'z': 0.0}),
        )

        for command, mode, point in cases:
            with self.subTest(command=command):
                planner = BotPlanner()
                team_order = dict(base_order, command=command)
                order = planner.build_orders(
                    self.manifest, self.states, [issuer], 1.0,
                    team_orders=[team_order])['orders'][0]

                self.assertEqual(mode, order['combat_mode'])
                self.assertEqual(point, order['move_position'])

    def test_radio_order_cannot_cross_team_or_invalid_tick_window(self):
        invalid_orders = [{
            'command_id': '1:1:1',
            'command': 'STOP',
            'team': team,
            'issuer_id': 1,
            'issued_tick': issued,
            'expires_tick': expires,
            'recipient_bot_ids': [11],
        } for team, issued, expires in ((2, 100, 200), (1, 200, 200))]

        order = BotPlanner().build_orders(
            self.manifest, self.states, [], 1.0,
            team_orders=invalid_orders)['orders'][0]

        self.assertEqual('route', order['combat_mode'])
        self.assertNotIn('team_command', order)

    def test_radio_back_to_base_uses_the_canonical_capture_base(self):
        team_order = {
            'command_id': '1:1:1',
            'command': 'BACKTOBASE',
            'team': 1,
            'issuer_id': 1,
            'issued_tick': 100,
            'expires_tick': 200,
            'recipient_bot_ids': [11],
        }
        defense = {
            'capture_bases': {
                '1': [{'id': '1:0', 'x': -25.0, 'y': 0.0, 'z': -450.0}],
            },
        }

        order = BotPlanner().build_orders(
            self.manifest, self.states, [], 1.0, defense,
            team_orders=[team_order])['orders'][0]

        self.assertEqual('team_back_to_base', order['combat_mode'])
        self.assertEqual(
            {'x': -25.0, 'y': 0.0, 'z': -450.0},
            order['move_position'])

    def test_radio_cell_order_is_forwarded_without_inventing_a_world_point(self):
        team_order = {
            'command_id': '1:1:1',
            'command': 'ATTENTIONTOCELL',
            'team': 1,
            'issuer_id': 1,
            'issued_tick': 100,
            'expires_tick': 200,
            'recipient_bot_ids': [11],
            'cell_index': 38,
        }

        order = BotPlanner().build_orders(
            self.manifest, self.states, [], 1.0,
            team_orders=[team_order])['orders'][0]

        self.assertEqual('ATTENTIONTOCELL', order['team_command'])
        self.assertEqual(38, order['team_command_cell_index'])
        self.assertEqual('route', order['combat_mode'])

    def test_radio_cell_order_moves_to_the_proved_grid_cell_center(self):
        team_order = {
            'command_id': '1:1:1',
            'command': 'ATTENTIONTOCELL',
            'team': 1,
            'issuer_id': 1,
            'issued_tick': 100,
            'expires_tick': 200,
            'recipient_bot_ids': [11],
            'cell_index': 38,
        }
        defense = {'arena_bounds': [-500.0, -500.0, 500.0, 500.0]}

        order = BotPlanner().build_orders(
            self.manifest, self.states, [], 1.0, defense,
            team_orders=[team_order])['orders'][0]

        self.assertEqual('team_attention_cell', order['combat_mode'])
        self.assertEqual(
            {'x': -150.0, 'y': 0.0, 'z': -350.0},
            order['move_position'])
        self.assertEqual(
            [-200.0, -400.0, -100.0, -300.0],
            order['move_area_bounds'])

    def test_radio_movement_order_replaces_nonurgent_combat_movement(self):
        team_order = {
            'command_id': '1:1:1',
            'command': 'BACKTOBASE',
            'team': 1,
            'issuer_id': 1,
            'issued_tick': 100,
            'expires_tick': 200,
            'recipient_bot_ids': [11],
        }
        defense = {
            'capture_bases': {
                '1': [{'id': '1:0', 'x': -25.0, 'y': 0.0, 'z': -450.0}],
            },
        }
        bot = {
            'id': 11, 'team': 1,
            'state': {'x': 0.0, 'y': 0.0, 'z': 0.0},
        }
        nonurgent_modes = (
            'engage', 'advance_contact', 'support_hold', 'flank',
            'take_cover', 'cover_hold', 'cover_peek', 'cover_return',
            'withdraw',
        )

        for mode in nonurgent_modes:
            with self.subTest(mode=mode):
                order = {
                    'combat_mode': mode,
                    'route_anchor': {'x': 0.0, 'y': 0.0, 'z': -100.0},
                    'move_position': {'x': 12.0, 'y': 0.0, 'z': 0.0},
                    'face_position': {'x': 18.0, 'y': 0.0, 'z': 4.0},
                    'throttle_override': 0.0,
                }

                BotPlanner._apply_team_order(
                    order, bot, team_order, [], defense)

                self.assertEqual('team_back_to_base', order['combat_mode'])
                self.assertEqual(
                    {'x': -25.0, 'y': 0.0, 'z': -450.0},
                    order['move_position'])
                self.assertIsNone(order['throttle_override'])

    def test_radio_does_not_override_local_survival(self):
        planner = BotPlanner()
        contact = _contact(2, 0, 150, [11])
        players = self._report(planner, [contact])
        self.states[0]['health'] = 100
        team_order = {
            'command_id': '1:1:1',
            'command': 'STOP',
            'team': 1,
            'issuer_id': 1,
            'issued_tick': 100,
            'expires_tick': 200,
            'recipient_bot_ids': [11],
        }

        order = planner.build_orders(
            self.manifest, self.states, players, 1.0,
            team_orders=[team_order])['orders'][0]

        self.assertEqual('low_health_retreat', order['combat_mode'])
        self.assertEqual('STOP', order['team_command'])

    def test_radio_does_not_override_an_urgent_base_responder(self):
        defense = {
            'bases': {'1': [
                {'id': '1:0', 'x': 0.0, 'y': 0.0, 'z': -100.0},
            ]},
            'states': {'1': {
                'points': 20, 'time_left': 60.0,
                'invaders': 1, 'stopped': False,
            }},
            'contributors': {'1': []},
            'capture_bases': {'1': [
                {'id': '1:0', 'x': 0.0, 'y': 0.0, 'z': -100.0},
            ]},
        }
        team_order = {
            'command_id': '1:1:1',
            'command': 'STOP',
            'team': 1,
            'issuer_id': 1,
            'issued_tick': 100,
            'expires_tick': 200,
            'recipient_bot_ids': [11],
        }

        order = BotPlanner().build_orders(
            self.manifest, self.states, [], 1.0, defense,
            team_orders=[team_order])['orders'][0]

        self.assertEqual('base_defense', order['combat_mode'])
        self.assertEqual('STOP', order['team_command'])

    def test_nearby_ally_changes_cautious_support_advance_score(self):
        contact = _contact(2, 0, 380, [12, 13], 'heavyTank')
        player = {'id': 2, 'team': 2, 'alive': True}
        cautious = _bot(
            12, 1, 0, self.route, 'mediumTank', {'support': 1.0})
        cautious['profile'].update({
            'dominant_role': 'support', 'desired_range': 200.0,
            'fire_range': 520.0,
        })
        cautious_state = _state(12, 1, 0, 0)

        solo = BotPlanner()
        self.assertEqual(1, solo.report_contacts(
            [contact], solo.known_targets([cautious_state], [player]), 1.0))
        solo_order = solo.build_orders(
            [cautious], [cautious_state], [player], 1.0)['orders'][0]
        self.assertEqual('support_hold', solo_order['combat_mode'])

        ally = _bot(13, 1, 1, self.route, 'mediumTank', {'support': 1.0})
        ally_state = _state(13, 1, 5, 0)
        supported = BotPlanner()
        self.assertEqual(1, supported.report_contacts(
            [contact], supported.known_targets(
                [cautious_state, ally_state], [player]), 1.0))
        orders = dict((value['id'], value) for value in
                      supported.build_orders(
                          [cautious, ally], [cautious_state, ally_state],
                          [player], 1.0)['orders'])
        self.assertEqual('advance_contact', orders[12]['combat_mode'])
        live_bots = supported._alive_bots(
            [cautious, ally], [cautious_state, ally_state])
        self.assertGreater(supported._ally_support_score(
            live_bots[0], live_bots,
            supported._contacts[1][('human', 2)]), 0.5)

    def test_support_vehicle_advances_until_inside_its_fire_range(self):
        planner = BotPlanner()
        cautious = _bot(
            12, 1, 0, self.route, 'mediumTank', {'support': 1.0})
        cautious['profile'].update({
            'armor': 120.0,
            'dominant_role': 'support',
            'desired_range': 200.0,
            'fire_range': 340.0,
        })
        state = _state(12, 1, 0, 0)
        contact = _contact(2, 0, 395, [12], 'heavyTank')
        player = {'id': 2, 'team': 2, 'alive': True}
        self.assertEqual(1, planner.report_contacts(
            [contact], planner.known_targets([state], [player]), 1.0))

        order = planner.build_orders(
            [cautious], [state], [player], 1.0)['orders'][0]

        self.assertEqual('advance_contact', order['combat_mode'])
        self.assertGreater(order['throttle_override'], 0.0)
        self.assertFalse(order['fire_allowed'])

    def test_range_hysteresis_keeps_mode_and_hull_anchor_stable(self):
        planner = BotPlanner()
        cautious = _bot(
            12, 1, 0, self.route, 'mediumTank', {'support': 1.0})
        cautious['profile'].update({
            'armor': 120.0,
            'dominant_role': 'support',
            'desired_range': 200.0,
            'fire_range': 520.0,
        })
        state = _state(12, 1, 0, 0)
        player = {'id': 2, 'team': 2, 'alive': True}
        far_limit = 200.0 * (
            1.08 + planner._personality(12)['caution'] * 0.18)

        def order_at(now, distance):
            contact = _contact(2, 0, distance, [12], 'heavyTank')
            self.assertEqual(1, planner.report_contacts(
                [contact], planner.known_targets([state], [player]), now))
            return planner.build_orders(
                [cautious], [state], [player], now)['orders'][0]

        outside = order_at(1.0, far_limit + 0.5)
        inside_band = order_at(2.1, far_limit - 0.5)
        inside = order_at(3.2, far_limit - 25.0)
        outside_band = order_at(4.3, far_limit + 0.5)

        self.assertEqual('support_hold', outside['combat_mode'])
        self.assertEqual('support_hold', inside_band['combat_mode'])
        self.assertEqual('engage', inside['combat_mode'])
        self.assertEqual('engage', outside_band['combat_mode'])
        self.assertEqual(outside['move_position'], inside['move_position'])
        self.assertEqual(outside['face_position'], inside['face_position'])
        self.assertTrue(outside['stable_hull_face'])
        self.assertTrue(inside['stable_hull_face'])

    def test_stationary_armored_turreted_vehicle_angles_without_moving(self):
        planner = BotPlanner()
        contacts = [_contact(2, 0, 150, [11])]
        players = self._report(planner, contacts)

        order = planner.build_orders(
            self.manifest, self.states, players, 1.0)['orders'][0]

        self.assertEqual('engage', order['combat_mode'])
        self.assertEqual(0.0, order['throttle_override'])
        self.assertGreaterEqual(abs(order['hull_angle_degrees']), 12.0)
        self.assertLessEqual(abs(order['hull_angle_degrees']), 30.0)
        self.assertNotEqual(order['aim_position'], order['face_position'])

        moved_target = {
            'alive': True, 'visible': True,
            'position': (30.0, 0.0, 170.0),
        }
        live = _overlay_live_target_pose(
            order, moved_target, (0.0, 0.0, 0.0))
        self.assertEqual(moved_target['position'], live['aim_position'])
        self.assertNotEqual(live['aim_position'], live['face_position'])
        with self.assertRaises(ValueError):
            _overlay_live_target_pose(
                dict(order, hull_angle_degrees=90.0), moved_target,
                (0.0, 0.0, 0.0))

        td_manifest = [_bot(
            21, 1, 0, self.route, 'AT-SPG', {'brawler': 1.0})]
        td_manifest[0]['profile'].update({
            'armor': 240.0, 'dominant_role': 'brawler',
            'desired_range': 180.0, 'fire_range': 520.0,
        })
        td_state = [_state(21, 1, 0, 0)]
        td_contact = [_contact(2, 0, 150, [21])]
        td_planner = BotPlanner()
        self.assertEqual(1, td_planner.report_contacts(
            td_contact, td_planner.known_targets(td_state, players), 1.0))
        td_order = td_planner.build_orders(
            td_manifest, td_state, players, 1.0)['orders'][0]
        self.assertNotIn('hull_angle_degrees', td_order)
        self.assertEqual(td_order['aim_position'], td_order['face_position'])

    def test_server_damage_accounting_forwards_hostile_bot_threat(self):
        clock = lambda: 12.5
        state = BattleState(clock=clock)
        state.players[1] = Player(
            1, None, ('test', 0), team=2, connected=True)
        state.bot_states[11] = {'id': 11, 'team': 1}
        received = []
        state.bot_planner.report_damage = lambda *values: received.append(
            values) or True

        state._record_damage(
            ('player', 1), ('bot', 11), 175, {})

        self.assertEqual([(11, 'player', 1, 175, 12.5)], received)

    def test_global_planning_runs_at_one_hz_while_worker_snapshots_stay_30_hz(self):
        state = BattleState()
        state.client_build = CLIENT_BUILD_0922
        state.phase = 'battle'
        state.bot_authority_id = SIMULATION_WORKER_AUTHORITY_ID
        planner = _CountingPlanner(state)
        state.bot_planner = planner
        snapshots = []
        worker = SimulationWorker(None, ('test', 0))
        worker.offer_reliable = (
            lambda message: snapshots.append(message) or True)
        worker.offer_snapshot = (
            lambda message: snapshots.append(message) or True)
        state.simulation_worker = worker

        for unused in range(BOT_PLANNER_INTERVAL_TICKS + 1):
            state.tick_once(1.0 / TICK_HZ)

        self.assertEqual([1, 31], planner.build_ticks)
        self.assertEqual(BOT_PLANNER_INTERVAL_TICKS + 1, state.tick)
        self.assertEqual(
            list(range(1, BOT_PLANNER_INTERVAL_TICKS + 2)),
            [message['server_tick'] for message in snapshots])

    def test_first_live_tick_replans_immediately_after_round_reset(self):
        state = BattleState()
        state.client_build = CLIENT_BUILD_0922
        state.phase = 'battle'
        planner = _CountingPlanner(state)
        state.bot_planner = planner

        state.tick_once(1.0 / TICK_HZ)
        for unused in range(BOT_PLANNER_INTERVAL_TICKS - 1):
            state.tick_once(1.0 / TICK_HZ)
        self.assertEqual([1], planner.build_ticks)

        state._reset_round()
        state.phase = 'battle'
        state.tick_once(1.0 / TICK_HZ)

        self.assertEqual(1, planner.reset_count)
        self.assertEqual([1, 1], planner.build_ticks)

    def test_observation_and_damage_memory_survive_skipped_plan_ticks(self):
        state = BattleState()
        state.client_build = CLIENT_BUILD_0922
        state.phase = 'battle'
        prebattle_ticks = int(round(PREBATTLE_SECONDS * TICK_HZ))
        state.tick = prebattle_ticks
        state.bot_authority_id = SIMULATION_WORKER_AUTHORITY_ID
        state.bot_manifest_authority_id = SIMULATION_WORKER_AUTHORITY_ID
        state.bot_manifest = list(self.manifest)
        state.bot_states = {11: dict(self.states[0])}
        target = Player(
            2, None, ('test', 0), team=2, connected=True,
            participating=True)
        target.offer_reliable = lambda unused_message: True
        target.offer_snapshot = lambda unused_message: True
        state.players[2] = target
        build_ticks = []
        build_orders = state.bot_planner.build_orders

        def record_build(*args, **kwargs):
            build_ticks.append(state.tick)
            return build_orders(*args, **kwargs)

        state.bot_planner.build_orders = record_build
        state.tick_once(1.0 / TICK_HZ)
        relay = state.update_bot_observation(
            SIMULATION_WORKER_AUTHORITY_ID, {
                'type': 'bot_observation', 'round_id': state.round_id,
                'contacts': [{
                    'observing_team': 1, 'target_kind': 'human',
                    'target_id': 2, 'target_team': 2,
                    'visible': True, 'fresh': True, 'time_left': 10.0,
                    'visible_by_bot_ids': [11],
                    'visible_by_player_ids': [],
                    'shootable_by_bot_ids': [11],
                    'x': 0.0, 'y': 0.0, 'z': 150.0,
                    'health': 1000, 'max_health': 1000,
                }],
                'affordances': [],
            })
        state._record_damage(
            ('player', 2), ('bot', 11), 75, {})

        self.assertIsInstance(relay, dict)
        self.assertIn(('human', 2), state.bot_planner._contacts[1])
        self.assertEqual(
            ('human', 2),
            state.bot_planner._recent_hits[11]['attacker'])
        for unused in range(BOT_PLANNER_INTERVAL_TICKS - 1):
            state.tick_once(1.0 / TICK_HZ)
        self.assertEqual([prebattle_ticks + 1], build_ticks)

        state.tick_once(1.0 / TICK_HZ)

        self.assertEqual(
            [prebattle_ticks + 1,
             prebattle_ticks + 1 + BOT_PLANNER_INTERVAL_TICKS],
            build_ticks)
        order = next(
            order for order in state.bot_orders['orders']
            if order['id'] == 11)
        self.assertEqual(2, order['target_id'])


class ServerBotArtilleryTests(unittest.TestCase):
    def test_spg_keeps_a_client_proved_target_beyond_direct_fire_range(self):
        planner = BotPlanner()
        route = _route('field', [
            (0, 0, False), (0, 60, False), (0, 1200, False),
        ])
        manifest = [_bot(11, 1, 0, route)]
        states = [_state(11, 1, 0, 60)]
        enemy = {'id': 2, 'team': 2, 'alive': True}
        self.assertEqual(1, planner.report_contacts([{
            'observing_team': 1,
            'target_kind': 'human',
            'target_id': 2,
            'target_team': 2,
            'visible': True,
            'shootable_by_bot_ids': [11],
            'x': 0.0,
            'y': 0.0,
            'z': 1050.0,
            'health': 1000,
            'max_health': 1000,
        }], planner.known_targets(states, [enemy]), 1.0))

        order = planner.build_orders(
            manifest, states, [enemy], 1.0)['orders'][0]

        self.assertEqual(2, order['target_id'])
        self.assertTrue(order['fire_allowed'])
        self.assertEqual('artillery_hold', order['combat_mode'])
        self.assertEqual(1250.0, order['fire_range'])

    def test_mirrored_spgs_choose_stable_rear_non_hold_anchors(self):
        planner = BotPlanner()
        manifest = [
            _bot(11, 1, 0, _route('field', [
                (0, 0, False), (0, 60, False),
                (0, 160, True), (0, 600, False),
            ])),
            _bot(26, 2, 0, _route('field', [
                (0, 600, False), (0, 540, False),
                (0, 440, True), (0, 0, False),
            ])),
        ]
        states = [_state(11, 1, 0, 0), _state(26, 2, 0, 600)]

        deploying = dict((order['id'], order) for order in
                         planner.build_orders(
                             manifest, states, [], 1.0)['orders'])

        self.assertEqual('artillery_deploy', deploying[11]['combat_mode'])
        self.assertEqual('artillery_deploy', deploying[26]['combat_mode'])
        self.assertEqual(1, deploying[11]['route_index'])
        self.assertEqual(1, deploying[26]['route_index'])
        self.assertEqual(60.0, deploying[11]['move_position']['z'])
        self.assertEqual(540.0, deploying[26]['move_position']['z'])
        self.assertEqual(
            600.0,
            deploying[11]['move_position']['z'] +
            deploying[26]['move_position']['z'])
        self.assertFalse(manifest[0]['route']['waypoints'][1]['hold'])
        self.assertFalse(manifest[1]['route']['waypoints'][1]['hold'])

        states[0]['z'] = 60.0
        states[1]['z'] = 540.0
        holding = dict((order['id'], order) for order in
                       planner.build_orders(
                           manifest, states, [], 2.0)['orders'])

        self.assertEqual('artillery_hold', holding[11]['combat_mode'])
        self.assertEqual('artillery_hold', holding[26]['combat_mode'])
        self.assertEqual(0.0, holding[11]['throttle_override'])
        self.assertEqual(0.0, holding[26]['throttle_override'])
        self.assertEqual(deploying[11]['move_position'],
                         holding[11]['move_position'])
        self.assertEqual(deploying[26]['move_position'],
                         holding[26]['move_position'])

    def test_target_does_not_pull_spg_off_anchor(self):
        planner = BotPlanner()
        route = _route('field', [
            (0, 0, False), (0, 60, False),
            (0, 160, False), (0, 600, False),
        ])
        manifest = [_bot(11, 1, 0, route)]
        states = [_state(11, 1, 0, 60)]
        enemy = {'id': 2, 'team': 2, 'alive': True}
        self.assertEqual(1, planner.report_contacts([{
            'observing_team': 1,
            'target_kind': 'human',
            'target_id': 2,
            'target_team': 2,
            'visible': True,
            'shootable_by_bot_ids': [11],
            'x': 0.0,
            'y': 0.0,
            'z': 500.0,
            'health': 1000,
            'max_health': 1000,
        }], planner.known_targets(states, [enemy]), 1.0))

        order = planner.build_orders(
            manifest, states, [enemy], 1.0)['orders'][0]

        self.assertEqual(2, order['target_id'])
        self.assertTrue(order['fire_allowed'])
        self.assertEqual('artillery_hold', order['combat_mode'])
        self.assertEqual({'x': 0.0, 'y': 0.0, 'z': 60.0},
                         order['move_position'])
        self.assertNotIn(order['combat_mode'], (
            'advance_contact', 'take_cover', 'cover_hold',
            'cover_peek', 'cover_return', 'flank'))

    def test_base_defense_preempts_artillery_hold(self):
        planner = BotPlanner()
        manifest = [_bot(11, 1, 0, _route('field', [
            (0, 0, False), (0, 60, False), (0, 600, False),
        ]))]
        states = [_state(11, 1, 0, 60)]
        defense = {
            'bases': {'1': [
                {'id': '1:0', 'x': 0.0, 'y': 0.0, 'z': 0.0},
            ]},
            'states': {'1': {
                'points': 20,
                'time_left': 30.0,
                'invaders': 1,
                'stopped': False,
            }},
            'contributors': {'1': []},
        }

        order = planner.build_orders(
            manifest, states, [], 1.0, defense)['orders'][0]

        self.assertEqual('base_defense', order['combat_mode'])
        self.assertEqual('1:0', order['defense_base_id'])
        self.assertEqual({'x': 0.0, 'y': 0.0, 'z': 0.0},
                         order['move_position'])

    def test_base_defense_releases_one_excess_responder_per_delay(self):
        planner = BotPlanner()
        route = _route('defense-lane', [
            (0, -100, False), (0, 100, False), (0, 500, False),
        ])
        manifest = [
            _bot(11 + index, 1, index, route, 'mediumTank')
            for index in range(4)
        ]
        states = [
            _state(bot['id'], 1, index * 20, 0)
            for index, bot in enumerate(manifest)
        ]
        defense = {
            'bases': {'1': [
                {'id': '1:0', 'x': 0.0, 'y': 0.0, 'z': -100.0},
            ]},
            'states': {'1': {
                'points': 20,
                'time_left': 60.0,
                'invaders': 3,
                'stopped': False,
            }},
            'contributors': {'1': []},
        }

        first = planner.build_orders(
            manifest, states, [], 1.0, defense)['orders']
        first_ids = {order['id'] for order in first
                     if order['combat_mode'] == 'base_defense'}
        self.assertEqual(3, len(first_ids))

        defense['states']['1']['invaders'] = 1
        delayed = planner.build_orders(
            manifest, states, [], 2.0, defense)['orders']
        delayed_ids = {order['id'] for order in delayed
                       if order['combat_mode'] == 'base_defense'}
        self.assertEqual(first_ids, delayed_ids)

        reduced_once = planner.build_orders(
            manifest, states, [], 5.1, defense)['orders']
        reduced_once_ids = {order['id'] for order in reduced_once
                            if order['combat_mode'] == 'base_defense'}
        self.assertEqual(2, len(reduced_once_ids))
        self.assertTrue(reduced_once_ids < first_ids)

        reduced_twice = planner.build_orders(
            manifest, states, [], 8.2, defense)['orders']
        reduced_twice_ids = {order['id'] for order in reduced_twice
                             if order['combat_mode'] == 'base_defense'}
        self.assertEqual(1, len(reduced_twice_ids))
        self.assertTrue(reduced_twice_ids < reduced_once_ids)

    def test_base_defense_keeps_an_arrived_responder_firing_on_capturer(self):
        planner = BotPlanner()
        route = _route('defense-lane', [
            (0, -100, False), (0, 100, False), (0, 500, False),
        ])
        manifest = [
            _bot(11 + index, 1, index, route, 'mediumTank')
            for index in range(3)
        ]
        states = [
            _weapon_state(_state(bot['id'], 1, 0, -100))
            for bot in manifest
        ]
        defense = {
            'bases': {'1': [
                {'id': '1:0', 'x': 0.0, 'y': 0.0, 'z': -100.0},
            ]},
            'states': {'1': {
                'points': 20,
                'time_left': 60.0,
                'invaders': 2,
                'stopped': False,
            }},
            'contributors': {'1': [
                {'kind': 'human', 'id': 2},
            ]},
        }
        initial = planner.build_orders(
            manifest, states, [], 1.0, defense)['orders']
        initial_ids = {order['id'] for order in initial
                       if order['combat_mode'] == 'base_defense'}
        self.assertEqual({11, 12}, initial_ids)

        player = {'id': 2, 'team': 2, 'alive': True}
        contact = _contact(2, 0, -80, [11])
        self.assertEqual(1, planner.report_contacts(
            [contact], planner.known_targets(states, [player]), 2.0))
        defense['states']['1']['invaders'] = 1
        planner.build_orders(manifest, states, [player], 2.0, defense)
        reduced = planner.build_orders(
            manifest, states, [player], 5.1, defense)['orders']
        retained_ids = {order['id'] for order in reduced
                        if order['combat_mode'] == 'base_defense'}

        self.assertEqual({11}, retained_ids)
        retained = next(order for order in reduced if order['id'] == 11)
        self.assertEqual(2, retained['target_id'])
        self.assertTrue(retained['fire_allowed'])

    def test_spg_is_never_a_pressured_route_donor(self):
        planner = BotPlanner()
        route_a = _route('a', [
            (-100, 0, False), (-100, 80, False),
            (-100, 500, False),
        ])
        route_b = _route('b', [
            (100, 0, False), (100, 80, False),
            (100, 500, False),
        ])
        manifest = [
            _bot(11, 1, 0, route_a, 'SPG', {
                'support': 1.0, 'flanker': 1.0, 'scout': 1.0,
            }),
            _bot(12, 1, 1, route_a, 'mediumTank', {
                'support': 0.0, 'flanker': 0.0, 'scout': 0.0,
                'brawler': 1.0,
            }),
            _bot(13, 1, 2, route_b, 'mediumTank', {
                'support': 0.5,
            }),
            _bot(14, 1, 3, route_a, 'mediumTank', {
                'support': 1.0, 'flanker': 1.0,
            }),
        ]
        states = [
            _state(11, 1, -100, 0),
            _state(12, 1, -100, 0),
            _state(13, 1, 100, 0),
            _state(14, 1, -100, 0),
        ]
        enemy = {'id': 2, 'team': 2, 'alive': True}
        self.assertEqual(1, planner.report_contacts([{
            'observing_team': 1,
            'target_kind': 'human',
            'target_id': 2,
            'target_team': 2,
            'visible': True,
            'shootable_by_bot_ids': [],
            'x': 100.0,
            'y': 0.0,
            'z': 250.0,
            'health': 1000,
            'max_health': 1000,
        }], planner.known_targets(states, [enemy]), 1.0))

        orders = dict((order['id'], order) for order in
                      planner.build_orders(
                          manifest, states, [enemy], 1.0)['orders'])

        self.assertEqual('a', orders[11]['route_id'])
        self.assertEqual('b', orders[13]['route_id'])
        self.assertEqual(1, sum(
            orders[bot_id]['route_id'] == 'b' for bot_id in (12, 14)))

    def test_immobile_line_holders_count_but_cannot_be_route_donors(self):
        source = _route('source', [
            (-100, 0, False), (-100, 100, False), (-100, 500, False),
        ])
        target = _route('target', [
            (100, 0, False), (100, 100, False), (100, 500, False),
        ])
        enemy = {'id': 2, 'team': 2, 'alive': True}
        conditions = (
            ('no_pose', {'world_pose': False}, [11], None),
            ('engine', {'critical': {'destroyed': ['engineHealth']}},
             [11], 12),
            ('left_track', {
                'critical': {'destroyed': ['leftTrackHealth']}},
             [11], 12),
            ('right_track', {
                'critical': {'destroyed': ['rightTrackHealth']}},
             [11], 12),
            ('damaged_engine', {'critical': {
                'destroyed': [],
                'devices': [{
                    'name': 'engineHealth', 'state': 'critical',
                }],
            }}, [11], 11),
            ('damaged_track', {'critical': {
                'destroyed': [],
                'devices': [{
                    'name': 'rightTrackHealth', 'state': 'critical',
                }],
            }}, [11], 11),
            ('track_without_lane', {
                'critical': {'destroyed': ['leftTrackHealth']}},
             [], None),
        )

        for unused_name, state_change, observers, donor_id in conditions:
            with self.subTest(condition=unused_name):
                planner = BotPlanner()
                manifest = [
                    _bot(11, 1, 0, source, 'mediumTank', {
                        'support': 1.0, 'flanker': 1.0,
                    }),
                    _bot(12, 1, 1, source, 'heavyTank', {
                        'support': 0.0, 'flanker': 0.0,
                        'brawler': 1.0,
                    }),
                    _bot(13, 1, 2, target, 'mediumTank', {
                        'support': 0.5,
                    }),
                ]
                states = [
                    _weapon_state(_state(11, 1, -100, 0)),
                    _weapon_state(_state(12, 1, -100, 0)),
                    _weapon_state(_state(13, 1, 100, 0)),
                ]
                states[0].update(state_change)
                contact = _contact(2, 100, 250, observers)
                self.assertEqual(1, planner.report_contacts(
                    [contact], planner.known_targets(states, [enemy]), 1.0))

                orders = dict((order['id'], order) for order in
                              planner.build_orders(
                                  manifest, states, [enemy], 1.0)['orders'])

                self.assertEqual(
                    'target' if donor_id == 11 else 'source',
                    orders[11]['route_id'])
                self.assertEqual(
                    'target' if donor_id == 12 else 'source',
                    orders[12]['route_id'])

    def test_rebalance_keeps_scouts_off_an_incompatible_heavy_lane(self):
        planner = BotPlanner()
        middle = _route('middle', [
            (0, 0, False), (0, 100, False), (0, 500, False),
        ], capacity=4,
            class_weights={'lightTank': 1.0, 'heavyTank': 0.02},
            role_weights={'scout': 1.0})
        heavy = _route('heavy', [
            (100, 0, False), (100, 100, False), (100, 500, False),
        ], capacity=6,
            class_weights={'lightTank': 0.12, 'heavyTank': 1.0},
            role_weights={'brawler': 1.0})
        manifest = [
            _bot(20 + value, 1, value, middle, 'lightTank', {'scout': 1.0})
            for value in range(4)
        ] + [
            _bot(30 + value, 1, 4 + value, heavy, 'heavyTank',
                 {'brawler': 1.0})
            for value in range(5)
        ]
        states = [
            _state(bot['id'], 1,
                   0 if bot['profile']['class_tag'] == 'lightTank' else 100,
                   0)
            for bot in manifest
        ]
        bots = planner._alive_bots(manifest, states)
        contacts = [{
            'position': {'x': 100.0, 'y': 0.0, 'z': 250.0},
            'health': 1000.0,
            'max_health': 1000.0,
        } for unused in range(4)]

        planner._rebalance_routes(1, bots, contacts, 1.0)

        self.assertTrue(all(
            planner._route_assignments[bot_id]['route']['id'] == 'middle'
            for bot_id in range(20, 24)))

    def test_spg_does_not_fill_frontline_capacity_during_rebalance(self):
        planner = BotPlanner()
        source = _route('source', [
            (-100, 0, False), (-100, 100, False), (-100, 500, False),
        ], capacity=4)
        target = _route('target', [
            (100, 0, False), (100, 100, False), (100, 500, False),
        ], capacity=1)
        manifest = [
            _bot(41, 1, 0, source, 'mediumTank', {'support': 1.0}),
            _bot(42, 1, 1, source, 'mediumTank', {'support': 0.9}),
            _bot(43, 1, 2, target, 'SPG', {'artillery': 1.0}),
        ]
        states = [
            _state(41, 1, -100, 0),
            _state(42, 1, -100, 0),
            _state(43, 1, 100, 0),
        ]
        bots = planner._alive_bots(manifest, states)
        contacts = [{
            'position': {'x': 100.0, 'y': 0.0, 'z': 250.0},
            'health': 1000.0,
            'max_health': 1000.0,
        }]

        planner._rebalance_routes(1, bots, contacts, 1.0)
        donor_id = next(
            bot_id for bot_id in (41, 42)
            if planner._route_assignments[bot_id]['route']['id'] == 'target')
        donor = next(bot for bot in bots if bot['id'] == donor_id)
        planner._route(donor, 1.1)
        planner._route_states[donor_id]['index'] = 1
        planner._rebalance_routes(1, bots, contacts, 5.0)
        planner._rebalance_routes(1, bots, contacts, 9.0)

        assigned = dict((bot_id, value['route']['id'])
                        for bot_id, value in
                        planner._route_assignments.items())
        self.assertEqual('target', assigned[43])
        self.assertEqual(1, sum(
            assigned[bot_id] == 'target' for bot_id in (41, 42)))
        self.assertEqual(1, planner._route_states[donor_id]['index'])
        self.assertGreater(
            planner._route_assignments[donor_id]['until'], 9.0)


class ServerHumanRamNormalTests(unittest.TestCase):
    @staticmethod
    def _body(player_id, z):
        return {
            'id': player_id, 'vehicle': 'ussr:R11_MS-1',
            'x': 0.0, 'y': 0.0, 'z': float(z), 'yaw': 0.0,
            'pitch': 0.0, 'roll': 0.0,
            'shape': (1.5, 3.5, -0.8, 2.0),
        }

    def test_worker_probe_freezes_first_impact_normal(self):
        state = BattleState()

        request = state._queue_human_ram_probe(
            (1, 2), self._body(1, 0.0), self._body(2, 0.5),
            123000, (0.0, -2.0, 6.5))

        self.assertEqual([0.0, -1.0], request['contact_normal'])
        self.assertEqual([0.0, -1.0],
                         state._human_ram_probe_snapshot()[0][
                             'contact_normal'])

    def test_worker_probe_rejects_a_normal_facing_away_from_first_body(self):
        state = BattleState()

        request = state._queue_human_ram_probe(
            (1, 2), self._body(1, 0.0), self._body(2, 0.5),
            123000, (0.0, 1.0, 6.5))

        self.assertIsNone(request)

    def test_player_receipt_preserves_frozen_first_impact_normal(self):
        state = BattleState()
        state.bot_states[11] = {'id': 11}
        state.bot_state_revision = 1
        state.bot_state_time_us = 123000
        state.human_collision_profiles[1] = {
            'shape': (1.5, 3.5, -0.8, 2.0),
            'ram_profile': {
                'spall_coefficient': 1.0, 'ramming_bonus': 0.0},
        }
        player = Player(
            1, object(), ('127.0.0.1', 1), team=1, slot=0)
        raw = {
            'seq': 1, 'bot_id': 11, 'bot_state_revision': 1,
            'presentation_time_us': 123000,
            'native_contact_time_us': 123000,
            'contact_x': 0.0, 'contact_y': 0.0, 'contact_z': 3.0,
            'contact_normal_x': 0.0, 'contact_normal_z': -1.0,
            'contact_armor_player': 80.0, 'contact_armor_bot': 100.0,
            'contact_spall_player': 1.0, 'contact_bonus_player': 0.0,
            'contact_screened_player': False,
            'contact_screened_bot': False,
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'pitch': 0.0, 'roll': 0.0,
            'vx': 0.0, 'vy': 0.0, 'vz': 10.0,
            'bot_vx': 0.0, 'bot_vy': 0.0, 'bot_vz': 0.0,
        }

        normalized, reason = state._validate_ram_contact(player, raw)

        self.assertIsNone(reason)
        self.assertEqual((0.0, -1.0), (
            normalized['contact_normal_x'],
            normalized['contact_normal_z']))

    def test_player_receipt_allows_one_frame_contact_point_pose_skew(self):
        state = BattleState()
        state.bot_states[11] = {'id': 11}
        state.bot_state_revision = 1
        state.bot_state_time_us = 123000
        state.human_collision_profiles[1] = {
            'shape': (1.5, 3.5, -0.8, 2.0),
            'ram_profile': {
                'spall_coefficient': 1.0, 'ramming_bonus': 0.0},
        }
        player = Player(
            1, object(), ('127.0.0.1', 1), team=1, slot=0)
        raw = {
            'seq': 1, 'bot_id': 11, 'bot_state_revision': 1,
            'presentation_time_us': 123000,
            'native_contact_time_us': 123000,
            'contact_x': 0.0, 'contact_y': 0.0, 'contact_z': 4.0,
            'contact_normal_x': 0.0, 'contact_normal_z': -1.0,
            'contact_armor_player': 80.0, 'contact_armor_bot': 100.0,
            'contact_spall_player': 1.0, 'contact_bonus_player': 0.0,
            'contact_screened_player': False,
            'contact_screened_bot': False,
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'pitch': 0.0, 'roll': 0.0,
            'vx': 0.0, 'vy': 0.0, 'vz': 10.0,
            'bot_vx': 0.0, 'bot_vy': 0.0, 'bot_vz': 0.0,
        }

        normalized, reason = state._validate_ram_contact(player, raw)
        self.assertIsNone(reason)
        self.assertIsNotNone(normalized)

        remote = dict(raw, contact_z=4.5)
        normalized, reason = state._validate_ram_contact(player, remote)
        self.assertIsNone(normalized)
        self.assertEqual('contact_outside_player_body', reason)

    def test_player_receipt_rejects_flipped_or_perpendicular_normal(self):
        state = BattleState()
        state.bot_states[11] = {'id': 11}
        state.bot_state_revision = 1
        state.bot_state_time_us = 123000
        state.human_collision_profiles[1] = {
            'shape': (1.5, 3.5, -0.8, 2.0),
            'ram_profile': {
                'spall_coefficient': 1.0, 'ramming_bonus': 0.0},
        }
        player = Player(
            1, object(), ('127.0.0.1', 1), team=1, slot=0)
        raw = {
            'seq': 1, 'bot_id': 11, 'bot_state_revision': 1,
            'presentation_time_us': 123000,
            'native_contact_time_us': 123000,
            'contact_x': 0.0, 'contact_y': 0.0, 'contact_z': 3.0,
            'contact_normal_x': 0.0, 'contact_normal_z': -1.0,
            'contact_armor_player': 80.0, 'contact_armor_bot': 100.0,
            'contact_spall_player': 1.0, 'contact_bonus_player': 0.0,
            'contact_screened_player': False,
            'contact_screened_bot': False,
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'pitch': 0.0, 'roll': 0.0,
            'vx': 0.0, 'vy': 0.0, 'vz': 10.0,
            'bot_vx': 0.0, 'bot_vy': 0.0, 'bot_vz': 0.0,
        }

        for normal in ((0.0, 1.0), (1.0, 0.0)):
            candidate = dict(
                raw, contact_normal_x=normal[0],
                contact_normal_z=normal[1])
            normalized, reason = state._validate_ram_contact(
                player, candidate)

            self.assertIsNone(normalized)
            self.assertEqual('contact_normal_mismatch', reason)


if __name__ == '__main__':
    unittest.main()
