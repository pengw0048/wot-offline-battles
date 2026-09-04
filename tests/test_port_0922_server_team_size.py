from pathlib import Path
import sys
import unittest
from unittest import mock


SERVER_ROOT = Path(__file__).resolve().parents[1] / 'server'
sys.path.insert(0, str(SERVER_ROOT))

from lan_battle_server import (  # noqa: E402
    BattleState, BOT_CALLSIGNS_0922, CLIENT_BUILD_0922,
    DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
    HUMAN_RAM_TIMELINE_CAPABILITY, PROJECTILE_CAPABILITY,
    MODERN_VISIBLE_MESSAGE_TYPES, PLAYER_ENVIRONMENT_CAPABILITY,
    EFFECTIVE_PARAMS_CAPABILITY,
    PLAYER_FIRE_INTENT_CAPABILITY,
    RAM_CONTACT_LEDGER_CAPABILITY,
    RICOCHET_CONTINUATION_CAPABILITY,
    SIMULATION_WORKER_CAPABILITY,
)
from effective_params_fixture import effective_params


class _Connection(object):
    def __init__(self):
        self.messages = []

    def sendall(self, payload):
        self.messages.append(payload)


def _hello(index, team=None):
    result = {
        'client_build': CLIENT_BUILD_0922,
        'capabilities': [
            PROJECTILE_CAPABILITY, DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
            HUMAN_RAM_TIMELINE_CAPABILITY, RAM_CONTACT_LEDGER_CAPABILITY,
            PLAYER_FIRE_INTENT_CAPABILITY,
            PLAYER_ENVIRONMENT_CAPABILITY,
            EFFECTIVE_PARAMS_CAPABILITY,
            RICOCHET_CONTINUATION_CAPABILITY],
        'name': 'Player-%d' % index,
        'vehicle': 'ussr:R11_MS-1',
        'max_health': 90,
        'vehicle_compact_descr': 'dGVzdA==',
        'effective_params': effective_params(),
    }
    if team is not None:
        result['requested_team'] = team
    return result


def _attach_worker(state):
    worker, error = state.add_simulation_worker(
        _Connection(), ('127.0.0.1', 2000), {
            'role': 'simulation_worker',
            'client_build': CLIENT_BUILD_0922,
            'capabilities': [
                PROJECTILE_CAPABILITY,
                DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
                SIMULATION_WORKER_CAPABILITY,
                HUMAN_RAM_TIMELINE_CAPABILITY,
                RAM_CONTACT_LEDGER_CAPABILITY,
                PLAYER_FIRE_INTENT_CAPABILITY,
                PLAYER_ENVIRONMENT_CAPABILITY,
                EFFECTIVE_PARAMS_CAPABILITY,
                RICOCHET_CONTINUATION_CAPABILITY,
            ],
        })
    if error is not None:
        raise AssertionError(error)
    return worker


class ServerTeamSizeTests(unittest.TestCase):
    def test_0922_bot_callsigns_use_period_appropriate_chinese_names(self):
        self.assertGreaterEqual(len(BOT_CALLSIGNS_0922), 120)
        self.assertEqual(len(BOT_CALLSIGNS_0922), len(set(BOT_CALLSIGNS_0922)))
        self.assertTrue(all(any(ord(char) > 127 for char in name)
                            for name in BOT_CALLSIGNS_0922))
        self.assertTrue(all(len(name) <= 32 for name in BOT_CALLSIGNS_0922))

        state = BattleState(team_size=15)
        state.client_build = CLIENT_BUILD_0922
        roster = state._new_bot_roster()

        self.assertEqual(30, len(roster))
        self.assertEqual(30, len(set(bot['name'] for bot in roster)))
        self.assertTrue(all(any(ord(char) > 127 for char in bot['name'])
                            for bot in roster))
        self.assertTrue(all(not bot['name'][-3:-2] == '-'
                            for bot in roster))

    def test_visible_clients_may_send_team_size_requests(self):
        self.assertIn('set_team_size', MODERN_VISIBLE_MESSAGE_TYPES)

    def test_host_can_select_a_bot_tier_preset(self):
        self.assertIn('set_bot_tier_mode', MODERN_VISIBLE_MESSAGE_TYPES)
        state = BattleState()
        host, error = state.add_player(
            _Connection(), ('10.0.0.1', 1000), _hello(1))
        self.assertIsNone(error)

        self.assertEqual((True, None), state.set_bot_tier_mode(
            host.player_id, 'minus1_plus2'))
        self.assertEqual('minus1_plus2', state.bot_tier_mode)
        self.assertEqual(
            'minus1_plus2', state.lobby_message()['bot_tier_mode'])

    def test_guest_cannot_select_a_bot_tier_preset(self):
        state = BattleState()
        unused_host, error = state.add_player(
            _Connection(), ('10.0.0.1', 1000), _hello(1))
        self.assertIsNone(error)
        guest, error = state.add_player(
            _Connection(), ('10.0.0.2', 1001), _hello(2))
        self.assertIsNone(error)

        self.assertEqual((False, 'host_only'), state.set_bot_tier_mode(
            guest.player_id, 'same'))
        self.assertEqual('random', state.bot_tier_mode)

    def test_exact_lineup_requires_unique_fully_qualified_slots(self):
        state = BattleState(bot_lineup=[{
            'team': 2, 'slot': 3,
            'vehicle': 'germany:G12_Ltraktor',
        }])
        self.assertEqual([{
            'team': 2, 'slot': 3,
            'vehicle': 'germany:G12_Ltraktor',
        }], state.bot_lineup)

        with self.assertRaisesRegex(ValueError, 'lineup vehicle'):
            BattleState(bot_lineup=[{
                'team': 2, 'slot': 3, 'vehicle': 'G12_Ltraktor',
            }])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            BattleState(bot_lineup=[
                {'team': 2, 'slot': 3,
                 'vehicle': 'germany:G12_Ltraktor'},
                {'team': 2, 'slot': 3,
                 'vehicle': 'ussr:R11_MS-1'},
            ])

    def test_default_roster_still_has_fifteen_tanks_per_team(self):
        state = BattleState()

        self.assertEqual(15, state.team_size)
        self.assertEqual(30, len(state.bot_roster))

    def test_configured_roster_uses_only_the_selected_team_slots(self):
        state = BattleState(team_size=4)

        self.assertEqual(8, len(state.bot_roster))
        self.assertEqual(
            {(team, slot) for team in (1, 2) for slot in range(4)},
            {(bot['team'], bot['slot']) for bot in state.bot_roster})

    def test_humans_occupy_selected_slots_in_both_rounds(self):
        state = BattleState(team_size=4)
        _attach_worker(state)
        players = []
        for index in range(3):
            player, error = state.add_player(
                _Connection(), ('10.0.0.%d' % (index + 1), 1000 + index),
                _hello(index))
            self.assertIsNone(error)
            players.append(player)

        start, error = state.request_start(players[0].player_id)

        self.assertIsNone(error)
        self.assertEqual(4, start['team_size'])
        occupied = {(player.team, player.slot) for player in players}
        self.assertFalse(occupied & {
            (bot['team'], bot['slot']) for bot in state.bot_roster})
        self.assertEqual(8 - len(players), len(state.bot_roster))

        state._reset_round()
        self.assertEqual('waiting', state.phase)
        self.assertFalse(occupied & {
            (bot['team'], bot['slot']) for bot in state.bot_roster})
        self.assertEqual(8 - len(players), len(state.bot_roster))
        self.assertEqual(4, state.lobby_message()['team_size'])

    def test_worker_drowning_proposal_is_committed_without_descriptors(self):
        state = BattleState(team_size=1)
        _attach_worker(state)
        player, error = state.add_player(
            _Connection(), ('10.0.0.1', 1001), _hello(1))
        self.assertIsNone(error)
        player.participating = True
        state.phase = 'battle'
        state._elect_bot_authority()

        for sample in range(1, 102):
            state.tick = 450 + sample
            self.assertTrue(state.update_player_environment(-1, {
                'type': 'player_environment', 'round_id': state.round_id,
                'authority_epoch': state.authority_epoch,
                'sample_seq': sample,
                'observations': [{
                    'player_id': player.player_id, 'input_seq': 0,
                    'level': 2, 'drowning_critical': {},
                }],
            }))
            state._tick_player_drowning(0.1)

        self.assertFalse(player.alive)
        self.assertEqual(0, player.health)

    def test_humans_cannot_expand_a_selected_four_tank_team(self):
        state = BattleState(team_size=4)
        for index in range(8):
            player, error = state.add_player(
                _Connection(), ('10.0.0.%d' % (index + 1), 1000 + index),
                _hello(index))
            self.assertIsNotNone(player)
            self.assertIsNone(error)

        player, error = state.add_player(
            _Connection(), ('10.0.0.9', 1009), _hello(9))

        self.assertIsNone(player)
        self.assertEqual('team_full', error)

    def test_asymmetric_capacities_build_the_exact_two_rosters(self):
        state = BattleState(team1_size=2, team2_size=5)

        self.assertEqual({1: 2, 2: 5}, state.team_sizes)
        self.assertEqual(5, state.team_size)
        self.assertEqual(
            {(1, 0), (1, 1)} |
            {(2, slot) for slot in range(5)},
            {(bot['team'], bot['slot']) for bot in state.bot_roster})
        self.assertEqual(
            {'1': 2, '2': 5}, state.lobby_message()['team_sizes'])

    def test_explicit_team_is_authoritative_and_reports_team_full(self):
        state = BattleState(team1_size=1, team2_size=3)
        first, error = state.add_player(
            _Connection(), ('10.0.0.1', 1001), _hello(1, 1))
        self.assertIsNone(error)
        self.assertEqual(1, first.team)

        rejected, error = state.add_player(
            _Connection(), ('10.0.0.2', 1002), _hello(2, 1))

        self.assertIsNone(rejected)
        self.assertEqual('team_full', error)
        other, error = state.add_player(
            _Connection(), ('10.0.0.3', 1003), _hello(3, 2))
        self.assertIsNone(error)
        self.assertEqual(2, other.team)

    def test_waiting_player_can_switch_only_when_target_has_capacity(self):
        state = BattleState(team1_size=2, team2_size=1)
        one, error = state.add_player(
            _Connection(), ('10.0.0.1', 1001), _hello(1, 1))
        self.assertIsNone(error)
        two, error = state.add_player(
            _Connection(), ('10.0.0.2', 1002), _hello(2, 2))
        self.assertIsNone(error)

        accepted, error = state.select_team(one.player_id, 2)
        self.assertFalse(accepted)
        self.assertEqual('team_full', error)
        state.remove_player(two.player_id)
        accepted, error = state.select_team(one.player_id, 2)
        self.assertTrue(accepted)
        self.assertIsNone(error)
        self.assertEqual(2, one.team)
        self.assertEqual(0, one.slot)

    def test_host_can_resize_each_waiting_team_without_restarting(self):
        state = BattleState(team1_size=3, team2_size=4)
        _attach_worker(state)
        host, error = state.add_player(
            _Connection(), ('10.0.0.1', 1001), _hello(1, 1))
        self.assertIsNone(error)

        accepted, error = state.set_team_size(host.player_id, 1, 2)
        self.assertTrue(accepted)
        self.assertIsNone(error)
        accepted, error = state.set_team_size(host.player_id, 2, 5)

        self.assertTrue(accepted)
        self.assertIsNone(error)
        self.assertEqual({1: 2, 2: 5}, state.team_sizes)
        self.assertEqual(5, state.team_size)
        self.assertEqual(
            {'1': 2, '2': 5}, state.lobby_message()['team_sizes'])
        start, error = state.request_start(host.player_id)
        self.assertIsNone(error)
        self.assertEqual({'1': 2, '2': 5}, start['team_sizes'])
        self.assertEqual(7 - 1, len(start['bots']))

    def test_non_host_cannot_resize_a_team(self):
        state = BattleState(team_size=3)
        host, error = state.add_player(
            _Connection(), ('10.0.0.1', 1001), _hello(1, 1))
        self.assertIsNone(error)
        guest, error = state.add_player(
            _Connection(), ('10.0.0.2', 1002), _hello(2, 2))
        self.assertIsNone(error)

        accepted, error = state.set_team_size(guest.player_id, 1, 2)

        self.assertFalse(accepted)
        self.assertEqual('host_only', error)
        self.assertEqual({1: 3, 2: 3}, state.team_sizes)

    def test_team_cannot_shrink_below_its_connected_player_count(self):
        state = BattleState(team_size=4)
        host, error = state.add_player(
            _Connection(), ('10.0.0.1', 1001), _hello(1, 1))
        self.assertIsNone(error)
        second, error = state.add_player(
            _Connection(), ('10.0.0.2', 1002), _hello(2, 1))
        self.assertIsNone(error)

        accepted, error = state.set_team_size(host.player_id, 1, 1)

        self.assertFalse(accepted)
        self.assertEqual('team_occupied', error)
        self.assertEqual(4, state.team_sizes[1])

    def test_team_resize_rejects_non_integer_wire_values(self):
        state = BattleState(team_size=2)
        host, error = state.add_player(
            _Connection(), ('10.0.0.1', 1001), _hello(1, 1))
        self.assertIsNone(error)

        for team, size, code in (
                (0, 1, 'invalid_team'), (3, 1, 'invalid_team'),
                (True, 1, 'invalid_team'), (1, 0, 'invalid_size'),
                (1, 16, 'invalid_size'), (1, '2', 'invalid_size'),
                (1, True, 'invalid_size')):
            accepted, error = state.set_team_size(
                host.player_id, team, size)
            self.assertFalse(accepted)
            self.assertEqual(code, error)

    def test_invalid_team_sizes_are_rejected(self):
        for value in (0, 16, 'invalid', 1.5, True):
            with self.assertRaises((TypeError, ValueError), msg=value):
                BattleState(team_size=value)

    def test_random_start_chooses_from_the_active_client_map_pool(self):
        state = BattleState(
            map_name='01_karelia', team_size=1)
        _attach_worker(state)
        player, error = state.add_player(
            _Connection(), ('10.0.0.1', 1001), _hello(1))
        self.assertIsNone(error)

        with mock.patch(
                'lan_battle_server.random.choice',
                return_value='05_prohorovka') as choose:
            start, error = state.request_start(
                player.player_id, 'server_random')

        self.assertIsNone(error)
        self.assertEqual('05_prohorovka', state.map_name)
        self.assertEqual('05_prohorovka', start['map'])
        choose.assert_any_call(tuple(state._active_map_pool()))

    def test_unknown_start_map_remains_fail_closed(self):
        state = BattleState(
            map_name='01_karelia', team_size=1)
        _attach_worker(state)
        player, error = state.add_player(
            _Connection(), ('10.0.0.1', 1001), _hello(1))
        self.assertIsNone(error)

        start, error = state.request_start(player.player_id, '99_missing')

        self.assertIsNone(start)
        self.assertEqual('invalid_map', error)
        self.assertEqual('01_karelia', state.map_name)


if __name__ == '__main__':
    unittest.main()
