from pathlib import Path
import json
import sys
import unittest

import bot_state_rows


PORT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORT_ROOT / 'server'))

from lan_battle_server import (  # noqa: E402
    BattleState, CLIENT_BUILD_0922, Player,
    PREBATTLE_SECONDS, TICK_HZ,
)
from gui.mods.offline_lan_0922.spawn_planner import SpawnPlanner  # noqa: E402
from effective_params_fixture import effective_params  # noqa: E402


class _Socket(object):
    def sendall(self, unused_payload):
        pass


def _player(player_id, team, x, z, world_pose=True):
    params = effective_params()
    params['critical']['devices'] = [{
        'name': 'leftTrackHealth', 'max_hp': 100.0, 'regen_hp': 70.0,
    }]
    return Player(
        player_id, _Socket(), ('127.0.0.1', player_id),
        team=team, slot=max(0, player_id - 1), x=x, z=z,
        client_position=world_pose, health=1000, max_health=1000,
        effective_params=params,
    )


class ServerCaptureTests(unittest.TestCase):
    def _state(self):
        state = BattleState(map_name='13_erlenberg')
        state.client_build = CLIENT_BUILD_0922
        state.phase = 'battle'
        state.capture_bases = {1: ((0.0, 0.0),),
                               2: ((500.0, 0.0),)}
        state.tick = int(round(PREBATTLE_SECONDS * TICK_HZ))
        return state

    @staticmethod
    def _capture_tick(state, offset=0):
        state.tick = (int(round(PREBATTLE_SECONDS * TICK_HZ)) +
                      int(offset) * int(round(TICK_HZ)))
        return state._update_capture()

    @staticmethod
    def _apply_canonical_critical(
            state, player, critical, critical_delta):
        record = {
            'projectile_id': '%d:b:11:1' % state.round_id,
            'shooter_kind': 'bot', 'shooter_id': 11,
            'shot_seq': 1, 'shell_index': 0, 'team': 1,
        }
        raw = {
            'target_kind': 'player', 'target_id': player.player_id,
            'damage': 0, 'hull_damage': 0, 'shot_result': 2,
            'x': player.x, 'y': player.y, 'z': player.z,
            'critical': critical,
            'critical_target_base_revision':
                player.critical_report_base_revision,
            'critical_target_ack_seq': player.critical_ack_seq,
            'critical_delta': critical_delta,
        }
        proposal = state._normalize_projectile_effect(
            raw, record, (player.x, player.y, player.z), False)
        state._apply_projectile_effect(record, proposal)

    def test_modern_capture_requires_exact_ready_bases(self):
        state = self._state()
        state.capture_bases = {}
        # This is the old Erlenberg tactical-route endpoint, not a retail base.
        state.players[2] = _player(2, 2, -146.2, -0.1)

        self.assertFalse(self._capture_tick(state))
        self.assertEqual(0, state.rules_state['bases']['1']['points'])
        self.assertEqual(
            {'points', 'time_left', 'invaders', 'stopped'},
            set(state.rules_state['bases']['1']))

    def test_real_erlenberg_graph_sends_packed_objective_bases(self):
        with (PORT_ROOT / 'navgraphs' / '13_erlenberg.json').open() as stream:
            graph = json.load(stream)
        planner = SpawnPlanner(navigation_graph=graph)

        accepted = BattleState._sanitize_capture_bases(planner.bases)

        self.assertEqual({
            1: [(152.892, -405.2)],
            2: [(-125.1, 402.9)],
        }, accepted)
        self.assertEqual(
            {index + 1: (tuple(point),)
             for index, point in enumerate(graph['objective_bases'])},
            planner.bases)
        self.assertNotIn('bases', self._state()._map_rule_data())

    def test_placeholder_poses_never_enter_a_capture_circle(self):
        state = self._state()
        state.players[2] = _player(2, 2, 0.0, 0.0, world_pose=False)
        state.bot_states[16] = {
            'id': 16, 'team': 2, 'alive': True, 'world_pose': False,
            'x': 0.0, 'z': 0.0,
        }

        self.assertFalse(self._capture_tick(state))
        self.assertEqual(0, state.rules_state['bases']['1']['invaders'])

        state.players[2].client_position = True
        state.bot_states[16]['world_pose'] = True
        self.assertTrue(self._capture_tick(state, 1))
        self.assertEqual(2, state.rules_state['bases']['1']['points'])
        self.assertEqual(2, state.rules_state['bases']['1']['invaders'])

    def test_defense_context_names_only_the_threatened_base(self):
        state = self._state()
        state.capture_bases = {
            1: ((0.0, 0.0), (400.0, 0.0)),
            2: ((500.0, 0.0),),
        }
        state.players[2] = _player(2, 2, 400.0, 0.0)

        self.assertTrue(self._capture_tick(state))
        context = state._bot_defense_context()

        self.assertEqual(1, context['states']['1']['invaders'])
        self.assertEqual([{
            'id': '1:1', 'x': 400.0, 'y': 0.0, 'z': 0.0,
        }], context['bases']['1'])
        self.assertEqual([{'kind': 'human', 'id': 2}],
                         context['contributors']['1'])
        self.assertEqual({
            '1': [
                {'id': '1:0', 'x': 0.0, 'y': 0.0, 'z': 0.0},
                {'id': '1:1', 'x': 400.0, 'y': 0.0, 'z': 0.0},
            ],
            '2': [
                {'id': '2:0', 'x': 500.0, 'y': 0.0, 'z': 0.0},
            ],
        }, context['capture_bases'])
        self.assertEqual((-500.0, -500.0, 500.0, 500.0),
                         context['arena_bounds'])

    def test_defense_context_omits_invalid_tactical_bounds(self):
        state = self._state()
        state._map_rule_data = lambda: {'bounds': (0.0, 0.0, float('nan'), 1.0)}

        self.assertIsNone(state._bot_defense_context()['arena_bounds'])

    def test_first_live_bot_publication_preserves_manifest_world_pose(self):
        state = self._state()
        state.players[1] = _player(1, 1, 200.0, 0.0)
        state.bot_authority_id = 1
        state.bot_roster = [{
            'id': 16, 'team': 2, 'slot': 0, 'name': 'Enemy',
        }]
        manifest_bot = {
            'id': 16, 'team': 2, 'slot': 0, 'name': 'Enemy',
            'vehicle': 'ussr:R11_MS-1',
            'health': 1000, 'max_health': 1000,
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'reload_time': 1.0, 'reload_duration': 1.0,
            # BotRuntime._manifest_entry marks its resolved spawn pose.
            'world_pose': True,
            'profile': {}, 'route': {'id': 'test', 'waypoints': []},
        }
        self.assertTrue(state.update_bot_manifest(1, {
            'round_id': state.round_id, 'bots': [manifest_bot],
        }))
        self.assertTrue(state.bot_states[16]['world_pose'])

        # Real BotRuntime bot_state messages do not repeat world_pose.  The
        # canonical server state must retain the manifest provenance bit.
        publication = {
            'id': 16, 'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'health': 1000, 'alive': True, 'fire_seq': 0,
            'reload_time': 1.0, 'reload_duration': 1.0,
            'critical': {}, 'combat_base_revision': 0, 'combat_seq': 0,
            'combat_fire_elapsed': 0.0, 'combat_fire_timer': 0.0,
            'stun_end_server_time_ms': 0,
        }
        self.assertTrue(state.update_bot_states(1, bot_state_rows.publication({
            'round_id': state.round_id, 'bots': [publication],
        })))
        self.assertTrue(state.bot_states[16]['world_pose'])
        self.assertTrue(self._capture_tick(state))
        self.assertEqual(1, state.rules_state['bases']['1']['points'])
        self.assertEqual({'bot:16': 1}, state.capture_contributors[1])

    def test_capture_circle_has_the_copied_fifty_metre_boundary(self):
        inside = self._state()
        inside.players[2] = _player(2, 2, 50.0, 0.0)
        self._capture_tick(inside)
        self.assertEqual(1, inside.rules_state['bases']['1']['points'])

        outside = self._state()
        outside.players[2] = _player(2, 2, 50.001, 0.0)
        self.assertFalse(self._capture_tick(outside))
        self.assertEqual(0, outside.rules_state['bases']['1']['points'])

    def test_owner_presence_does_not_pause_standard_ctf_capture(self):
        state = self._state()
        state.players[1] = _player(1, 1, 0.0, 0.0)
        state.players[2] = _player(2, 2, 0.0, 0.0)

        self._capture_tick(state)
        self._capture_tick(state, 1)

        base = state.rules_state['bases']['1']
        self.assertEqual(2, base['points'])
        self.assertEqual(1, base['invaders'])
        self.assertEqual(98.0, base['time_left'])
        self.assertFalse(base['stopped'])
        self.assertEqual({'human:2': 2}, state.capture_contributors[1])

    def test_leaver_drops_only_own_points_and_owner_does_not_pause(self):
        state = self._state()
        state.players[2] = _player(2, 2, 0.0, 0.0)
        state.players[3] = _player(3, 2, 1.0, 0.0)
        self._capture_tick(state)
        self._capture_tick(state, 1)
        self.assertEqual(4, state.rules_state['bases']['1']['points'])

        state.players[2].x = 60.0
        self._capture_tick(state, 2)
        self.assertEqual(3, state.rules_state['bases']['1']['points'])
        self.assertEqual({'human:3': 3}, state.capture_contributors[1])

        state.players[1] = _player(1, 1, 0.0, 0.0)
        self._capture_tick(state, 3)
        self.assertEqual(4, state.rules_state['bases']['1']['points'])
        self.assertEqual({'human:3': 4}, state.capture_contributors[1])
        self.assertFalse(state.rules_state['bases']['1']['stopped'])

    def test_bot_hit_resets_only_damaged_human_contribution(self):
        state = self._state()
        state.players[1] = _player(1, 1, 200.0, 0.0)
        state.players[2] = _player(2, 2, 0.0, 0.0)
        state.players[3] = _player(3, 2, 1.0, 0.0)
        state.bot_authority_id = 1
        state.bot_manifest_authority_id = 1
        state.bot_states[11] = {
            'id': 11, 'team': 1, 'alive': True, 'world_pose': True,
            'x': 100.0, 'y': 0.0, 'z': 0.0, 'fire_seq': 2,
            'shell_index': 0, 'health': 1000, 'max_health': 1000,
            'vehicle': 'ussr:R11_MS-1',
        }
        for offset in range(4):
            self._capture_tick(state, offset)
        self.assertEqual(8, state.rules_state['bases']['1']['points'])

        def projectile_hit(shot_seq, damage):
            state.bot_states[11]['fire_seq'] = shot_seq
            state.bot_pending_projectile_launches.add((11, shot_seq))
            launch_time_us = shot_seq * 1000
            if state.bot_launch_clock_offset_us is None:
                state.bot_launch_clock_offset_us = (
                    state._server_time_ms() * 1000 - 1000000)
            state.bot_pending_projectile_metadata[(11, shot_seq)] = {
                'burst_group_seq': shot_seq,
                'burst_index': 0, 'burst_count': 1, 'shell_index': 0,
                'sample_start_us': 0,
                'sample_end_us': launch_time_us,
                'launch_clock_offset_us': state.bot_launch_clock_offset_us,
            }
            launch = {
                'type': 'projectile_launch', 'round_id': state.round_id,
                'authority_epoch': state.authority_epoch,
                'shooter_kind': 'bot', 'shooter_id': 11,
                'shot_seq': shot_seq, 'shell_index': 0,
                'origin': [100.0, 1.0, 0.0],
                'velocity': [-100.0, 0.0, 0.0], 'gravity': 9.81,
                'max_distance': 1000.0, 'max_time_ms': 10000,
                'is_he': False, 'splash_radius': 0.0,
                'penetration_factor': 1.0,
                'launch_time_us': launch_time_us,
                'launch_pose': [100.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                'source_shot': {
                    'speed': 100.0, 'gravity': 9.81,
                    'maxDistance': 1000.0,
                    'piercingPower': [100.0, 100.0],
                    'deadeye': False,
                    'shell': {
                        'kind': 'ARMOR_PIERCING', 'caliber': 45.0,
                        'damage': [110.0, 110.0],
                        'explosionRadius': 0.0,
                    },
                },
            }
            self.assertTrue(state.launch_projectile(1, launch))
            projectile_id = '%d:b:11:%d' % (state.round_id, shot_seq)
            return state.resolve_projectile(1, {
                'type': 'projectile_resolve',
                'round_id': state.round_id,
                'authority_epoch': state.authority_epoch,
                'projectile_id': projectile_id,
                'base_checked_ms': 0, 'outcome': 'impact',
                'resolved_time_ms': 0, 'checked_distance': 100.0,
                'piercing_loss': 0.0, 'penetration_factor': 1.0,
                'impact': [0.0, 1.0, 0.0],
                'direct': {
                    'target_kind': 'player', 'target_id': 2,
                    'damage': damage, 'shot_result': 2,
                    'x': 0.0, 'y': 1.0, 'z': 0.0,
                },
                'splash': [], 'destructibles': [],
            })

        self.assertTrue(projectile_hit(1, 0))
        self.assertEqual(8, state.rules_state['bases']['1']['points'])

        self.assertTrue(projectile_hit(2, 100))
        self.assertEqual(4, state.rules_state['bases']['1']['points'])
        self.assertEqual({'human:3': 4}, state.capture_contributors[1])
        hit = next(event for event in state.pending_events
                   if event.get('kind') == 'bot_human_hit' and
                   event.get('damage') == 100)
        self.assertTrue(state._validate_combat_event_for_wire(hit))
        self.assertNotIn('capture_reset', hit)

    def test_zero_hull_damage_with_new_module_damage_resets_capture(self):
        state = self._state()
        player = _player(2, 2, 0.0, 0.0)
        state.players[2] = player
        for offset in range(3):
            self._capture_tick(state, offset)
        self.assertEqual(3, state.rules_state['bases']['1']['points'])

        critical = {
            'devices': [{
                'name': 'leftTrackHealth', 'hp': 40.0, 'max_hp': 100.0,
                'state': 'critical',
            }],
            'events': [{
                'kind': 'device', 'name': 'leftTrackHealth',
                'old_state': 'normal', 'state': 'critical', 'cause': 'shot',
            }],
        }
        self._apply_canonical_critical(state, player, critical, {
            'devices': [{
                'name': 'leftTrackHealth', 'hp_loss': 60.0,
            }],
            'crew_ko': [], 'ignite': False,
        })
        self.assertEqual(0, state.rules_state['bases']['1']['points'])
        self.assertEqual({}, state.capture_contributors[1])
        event = state.pending_events[-1]
        self.assertEqual(0, event['damage'])
        self.assertTrue(state._validate_combat_event_for_wire(event))
        self.assertNotIn('capture_reset', event)

    def test_module_repair_does_not_reset_capture(self):
        state = self._state()
        player = _player(2, 2, 0.0, 0.0)
        player.critical = {
            'devices': [{
                'name': 'leftTrackHealth', 'hp': 0.0, 'max_hp': 100.0,
                'state': 'destroyed',
            }],
            'destroyed': ['leftTrackHealth'], 'crew_ko': [], 'fire': False,
            'ammo_rack_death': False, 'events': [],
        }
        state.players[2] = player
        for offset in range(3):
            self._capture_tick(state, offset)

        self.assertTrue(state.report_track_repair(2, {
            'type': 'track_repair', 'round_id': state.round_id,
            'critical_base_revision': 0, 'repair_seq': 1,
            'tracks': [{
                'name': 'leftTrackHealth', 'hp': 70.0,
                'max_hp': 100.0, 'state': 'critical',
            }],
        }))
        self.assertEqual(3, state.rules_state['bases']['1']['points'])
        self.assertEqual({'human:2': 3}, state.capture_contributors[1])

    def test_stale_track_repair_converges_to_canonical_equipment_repair(self):
        state = self._state()
        player = _player(2, 2, 0.0, 0.0)
        player.critical = {
            'devices': [{
                'name': 'leftTrackHealth', 'hp': 20.0, 'max_hp': 100.0,
                'state': 'destroyed',
            }],
            'destroyed': ['leftTrackHealth'], 'crew_ko': [], 'fire': False,
            'ammo_rack_death': False, 'events': [],
        }
        state.players[2] = player

        def report(seq, hp, phase='destroyed', round_id=None,
                   name='leftTrackHealth'):
            return {
                'type': 'track_repair',
                'round_id': state.round_id if round_id is None else round_id,
                'critical_base_revision': 0, 'repair_seq': seq,
                'tracks': [{
                    'name': name, 'hp': hp, 'max_hp': 100.0,
                    'state': phase,
                }],
            }

        self.assertTrue(state.report_track_repair(2, report(1, 30.0)))
        repaired = {
            'devices': [{
                'name': 'leftTrackHealth', 'hp': 70.0, 'max_hp': 100.0,
                'state': 'critical',
            }],
            'destroyed': [], 'crew_ko': [], 'fire': False,
            'ammo_rack_death': False, 'events': [],
        }
        state._commit_player_critical_progress(player, repaired)
        revision = player.critical_revision

        self.assertTrue(state.report_track_repair(2, report(2, 40.0)))
        self.assertEqual(2, player.critical_ack_seq)
        self.assertGreater(player.critical_revision, revision)
        self.assertEqual(repaired, player.critical)
        published = state._public_player(player, include_outfits=False)
        self.assertEqual(2, published['critical_ack_seq'])
        self.assertEqual(repaired, published['critical'])

        # Exact retries and older accepted checkpoints are harmless no-ops;
        # same-sequence mutations and invalid identity/shape still fail closed.
        revision = player.critical_revision
        self.assertTrue(state.report_track_repair(2, report(2, 40.0)))
        self.assertTrue(state.report_track_repair(2, report(1, 30.0)))
        self.assertEqual(revision, player.critical_revision)
        self.assertFalse(state.report_track_repair(2, report(2, 41.0)))
        self.assertFalse(state.report_track_repair(
            2, report(3, 50.0, round_id=state.round_id + 1)))
        self.assertFalse(state.report_track_repair(
            2, report(3, 50.0, name='engineHealth')))
        self.assertFalse(state.report_track_repair(
            2, report(3, 50.0, name='rightTrackHealth')))

    def test_track_repair_merges_progress_beside_converged_track(self):
        state = self._state()
        player = _player(2, 2, 0.0, 0.0)
        player.critical = {
            'devices': [
                {'name': 'leftTrackHealth', 'hp': 70.0,
                 'max_hp': 100.0, 'state': 'critical'},
                {'name': 'rightTrackHealth', 'hp': 20.0,
                 'max_hp': 100.0, 'state': 'destroyed'},
            ],
            'destroyed': ['rightTrackHealth'], 'crew_ko': [], 'fire': False,
            'ammo_rack_death': False, 'events': [],
        }
        state.players[2] = player

        def report(right_hp):
            return {
                'type': 'track_repair', 'round_id': state.round_id,
                'critical_base_revision': 0, 'repair_seq': 1,
                'tracks': [
                    {'name': 'leftTrackHealth', 'hp': 40.0,
                     'max_hp': 100.0, 'state': 'destroyed'},
                    {'name': 'rightTrackHealth', 'hp': right_hp,
                     'max_hp': 100.0, 'state': 'destroyed'},
                ],
            }

        self.assertFalse(state.report_track_repair(2, report(20.0)))
        self.assertEqual(0, player.critical_ack_seq)
        self.assertTrue(state.report_track_repair(2, report(30.0)))
        devices = {row['name']: row for row in player.critical['devices']}
        self.assertEqual('critical', devices['leftTrackHealth']['state'])
        self.assertEqual(70.0, devices['leftTrackHealth']['hp'])
        self.assertEqual('destroyed', devices['rightTrackHealth']['state'])
        self.assertEqual(30.0, devices['rightTrackHealth']['hp'])
        self.assertEqual(1, player.critical_ack_seq)


if __name__ == '__main__':
    unittest.main()
