from pathlib import Path
import sys
import unittest


TEST_ROOT = Path(__file__).resolve().parent
SERVER_ROOT = TEST_ROOT.parent / 'server'
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(SERVER_ROOT))

import bot_chat  # noqa: E402
import bot_chat_llm  # noqa: E402
import lan_battle_server as server_module  # noqa: E402
from bot_chat import (  # noqa: E402
    ADDRESS_NONE, BotChatDirector, REQUEST_BASE, REQUEST_CELL, REQUEST_FOLLOW,
    REQUEST_ATTACK, REQUEST_HOLD, TRIGGER_KILL, parse_marker,
    read_request,
)
from lan_battle_server import BattleState  # noqa: E402


class _ScriptedRandom(object):
    def __init__(self, value=0.0):
        self.value = value

    def random(self):
        return self.value

    def uniform(self, low, high):
        return low

    def choice(self, sequence):
        return list(sequence)[0]


class _Backend(object):
    """Answer with a fixed line, as a model that always agrees would."""

    def __init__(self, text='收到 [GO:A3]'):
        self.text = text
        self.requests = []

    def compose(self, request):
        self.requests.append(request)
        return self.text


def _bot(bot_id=1, team=1, alive=True):
    return {'id': bot_id, 'team': team, 'name': '今天不加班',
            'vehicle': 'ussr:R04_T-34', 'vehicle_class': 'mediumTank',
            'alive': alive, 'x': 0.0, 'z': 0.0, 'hp': 400, 'max_hp': 400}


def _snapshot(bots=None, player_id=1):
    return {
        'bots': list(bots or [_bot()]),
        'speaker': {'player_id': player_id, 'name': 'Peng', 'x': 0.0,
                    'z': 0.0, 'spotted': set()},
        'arena_bounds': (0.0, 0.0, 100.0, 100.0),
    }


def _director(backend=None):
    director = BotChatDirector(_ScriptedRandom(), tick_hz=30.0,
                               backend=backend or _Backend())
    director.reset_round(1)
    return director


def _drain(director, snapshot, stop=1200):
    return [line for tick in range(stop)
            for line in director.tick(tick, snapshot)]


class ReadRequestTest(unittest.TestCase):
    def test_a_grid_square_is_a_request_to_go_there(self):
        ask = read_request('a3去两个人')
        self.assertEqual(REQUEST_CELL, ask['request'])
        self.assertEqual('A3', ask['cell'])
        self.assertEqual(20, ask['cell_index'])
        self.assertEqual('ATTENTIONTOCELL', ask['command'])

    def test_going_home_is_recognised_in_the_words_players_use(self):
        for text in ('轻坦回家', '回防', '守家', '救家', '快回来守'):
            self.assertEqual(REQUEST_BASE,
                             read_request(text)['request'], text)

    def test_following_and_holding_are_recognised(self):
        self.assertEqual(REQUEST_FOLLOW, read_request('跟着我')['request'])
        self.assertEqual(REQUEST_HOLD, read_request('孤狼别动')['request'])

    def test_the_short_words_players_actually_type_are_requests(self):
        # "冲啊" and "跟我来" went unanswered: one was not a request at all
        # and the other lost a coin toss meant for idle remarks.
        for text in ('冲啊', '冲', '压上去', '推一波', '进攻'):
            self.assertEqual(REQUEST_ATTACK,
                             read_request(text)['request'], text)

    def test_holding_wins_over_attacking_when_both_words_appear(self):
        self.assertEqual(REQUEST_HOLD, read_request('别冲')['request'])

    def test_an_attack_marker_maps_to_the_stock_command(self):
        text, marker = parse_marker('好 [ATTACK]')
        self.assertEqual('好', text)
        self.assertEqual('ATTACK', marker['command'])

    def test_ordinary_talk_asks_for_nothing(self):
        for text in ('这局有点难打', '哈哈', '打得不错'):
            self.assertIsNone(read_request(text), text)

    def test_a_grid_reference_outside_the_map_asks_for_nothing(self):
        self.assertIsNone(read_request('z9 有人吗'))


class MarkerTest(unittest.TestCase):
    def test_a_marker_is_removed_from_what_is_said(self):
        text, marker = parse_marker('收到, 这就去 [GO:A3]')
        self.assertEqual('收到, 这就去', text)
        self.assertEqual(REQUEST_CELL, marker['request'])
        self.assertEqual(20, marker['cell_index'])

    def test_every_marker_maps_to_a_stock_command(self):
        for token, request, command in (
                ('[BASE]', REQUEST_BASE, 'BACKTOBASE'),
                ('[FOLLOW]', REQUEST_FOLLOW, 'FOLLOWME'),
                ('[HOLD]', REQUEST_HOLD, 'STOP')):
            text, marker = parse_marker('好 %s' % token)
            self.assertEqual('好', text)
            self.assertEqual(request, marker['request'])
            self.assertEqual(command, marker['command'])

    def test_a_line_without_a_marker_is_untouched(self):
        self.assertEqual(('我不去', None), parse_marker('我不去'))

    def test_an_invalid_grid_square_is_not_a_marker(self):
        self.assertEqual(('[GO:Z9] 好', None), parse_marker('[GO:Z9] 好'))

    def test_the_marker_never_reaches_the_chat_window(self):
        self.assertNotIn('[', bot_chat_llm.sanitize_line(
            parse_marker('收到 [GO:A3]')[0]))


class DirectorOrderTest(unittest.TestCase):
    def setUp(self):
        self.snapshot = _snapshot()

    def test_an_agreement_becomes_an_order_beside_the_line(self):
        director = _director()
        director.observe_player_line(0, 1, 'a3去两个人', self.snapshot)
        published = _drain(director, self.snapshot)
        self.assertTrue(published)
        line = published[0]
        self.assertEqual('收到', line['text'])
        self.assertEqual('ATTENTIONTOCELL', line['order']['command'])
        self.assertEqual(20, line['order']['cell_index'])
        self.assertEqual(1, line['order']['asked_by'])

    def test_a_line_without_a_marker_orders_nothing(self):
        director = _director(_Backend('我在忙'))
        director.observe_player_line(0, 1, 'a3去两个人', self.snapshot)
        published = _drain(director, self.snapshot)
        self.assertNotIn('order', published[0])

    def test_a_repeated_line_agrees_to_nothing(self):
        # The line is the acknowledgement. A Bot whose line the channel
        # refused has not answered, so it has not agreed either.
        bots = [_bot(1), _bot(2)]
        bots[1]['name'] = '北方孤狼'
        snapshot = _snapshot(bots)
        director = _director()
        director.observe_player_line(0, 1, 'a3去两个人', snapshot)
        published = _drain(director, snapshot)
        self.assertEqual(1, len([line for line in published
                                 if line.get('order')]))

    def test_a_marker_nobody_asked_for_is_ignored(self):
        director = _director()
        director.observe_player_line(0, 1, '这局有点难打', self.snapshot)
        published = _drain(director, self.snapshot)
        self.assertTrue(published)
        self.assertNotIn('order', published[0])
        self.assertEqual('收到', published[0]['text'])

    def test_a_bot_cannot_agree_to_a_different_place(self):
        # The player asked for A3; the marker names B5.
        director = _director(_Backend('收到 [GO:B5]'))
        director.observe_player_line(0, 1, 'a3去两个人', self.snapshot)
        published = _drain(director, self.snapshot)
        self.assertIsNone(published[0].get('order'))

    def test_a_bot_cannot_agree_to_a_different_kind_of_thing(self):
        director = _director(_Backend('收到 [BASE]'))
        director.observe_player_line(0, 1, 'a3去两个人', self.snapshot)
        published = _drain(director, self.snapshot)
        self.assertIsNone(published[0].get('order'))

    def test_only_a_direct_answer_is_offered_the_marker(self):
        # Three Bots, because the two that answer the human are both on
        # cooldown and a third is needed for anyone to answer them.
        bots = [_bot(1), _bot(2), _bot(3)]
        bots[1]['name'] = '北方孤狼'
        bots[2]['name'] = '草丛观察员'
        snapshot = _snapshot(bots)
        # Distinct text, or the repeat filter stops the chain before a
        # Bot ever answers another Bot.
        class _Varying(_Backend):
            def compose(self, request):
                _Backend.compose(self, request)
                return '好%d' % len(self.requests)

        backend = _Varying()
        director = _director(backend)
        director.observe_player_line(0, 1, 'a3去两个人', snapshot)
        _drain(director, snapshot)
        replies = [call for call in backend.requests if call['ask']]
        hops = [call for call in backend.requests if not call['ask']]
        self.assertTrue(replies)
        self.assertTrue(hops, 'a Bot answering a Bot has nothing to agree to')

    def test_an_event_line_is_never_offered_the_marker(self):
        backend = _Backend('好')
        director = _director(backend)
        director.observe_event(0, 1, TRIGGER_KILL, 1, self.snapshot)
        _drain(director, self.snapshot)
        self.assertTrue(backend.requests)
        self.assertTrue(all(call['ask'] is None for call in backend.requests))

    def test_two_bots_agreeing_produce_two_orders(self):
        # Two Bots agreeing in identical words would be dropped by the
        # repeat filter, and a Bot that does not speak has not agreed.
        class _Varying(object):
            def __init__(self):
                self.count = 0

            def compose(self, request):
                self.count += 1
                return '收到%d [GO:A3]' % self.count

        bots = [_bot(1), _bot(2)]
        bots[1]['name'] = '北方孤狼'
        snapshot = _snapshot(bots)
        director = _director(_Varying())
        director.observe_player_line(0, 1, 'a3去两个人', snapshot)
        published = _drain(director, snapshot)
        ordered = [line for line in published if line.get('order')]
        self.assertEqual(2, len(ordered))
        self.assertEqual({1, 2}, {line['bot_id'] for line in ordered})


class AlwaysAnsweredTest(unittest.TestCase):
    """A line that asked the team for something is not an idle remark."""

    def setUp(self):
        self.snapshot = _snapshot()

    def test_a_request_is_answered_even_on_an_unlucky_roll(self):
        # value=1.0 fails every probability gate an open remark faces.
        director = BotChatDirector(_ScriptedRandom(1.0), tick_hz=30.0,
                                   backend=_Backend('收到'))
        director.reset_round(1)
        outcome = director.observe_player_line(0, 1, '跟我来', self.snapshot)
        self.assertEqual('follow', outcome['ask'])
        self.assertEqual(1, outcome['scheduled'])

    def test_an_idle_remark_still_may_go_unanswered(self):
        director = BotChatDirector(_ScriptedRandom(1.0), tick_hz=30.0,
                                   backend=_Backend('收到'))
        director.reset_round(1)
        outcome = director.observe_player_line(
            0, 1, '这局有点难打', self.snapshot)
        self.assertIsNone(outcome['ask'])
        self.assertEqual(0, outcome['scheduled'])


class ServerAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.state = BattleState(map_name='01_karelia', max_players=2,
                                 team1_size=2, team2_size=1)
        self.state.phase = 'battle'
        # Orders are fenced until the shared countdown becomes live.
        self.state.tick = int(round(
            server_module.PREBATTLE_SECONDS * server_module.TICK_HZ)) + 1
        self.state.bot_manifest = [{'id': 5, 'team': 1, 'slot': 1,
                                    'name': '今天不加班',
                                    'vehicle': 'ussr:R04_T-34'}]
        self.state.bot_states = {5: {
            'id': 5, 'team': 1, 'alive': True, 'x': 10.0, 'z': 10.0,
            'health': 400, 'max_health': 400}}
        self.player = self._player()
        self.state.players[1] = self.player

    def _player(self, team=1, alive=True, connected=True,
                participating=True):
        player = server_module.Player.__new__(server_module.Player)
        player.player_id = 1
        player.team = team
        player.alive = alive
        player.connected = connected
        player.participating = participating
        player.x = 5.0
        player.y = 0.0
        player.z = 5.0
        player.yaw = 0.0
        player.name = 'Peng'
        player.client_position = True
        return player

    def _line(self, command='ATTENTIONTOCELL', cell_index=20, asked_by=1):
        order = {'command': command, 'asked_by': asked_by}
        if cell_index is not None and command == 'ATTENTIONTOCELL':
            order['cell_index'] = cell_index
        return {'bot_id': 5, 'team': 1, 'text': '收到', 'order': order}

    def _admit(self, line=None):
        with self.state.lock:
            return self.state._admit_bot_chat_order_locked(
                line or self._line())

    def test_an_agreement_becomes_a_single_recipient_order(self):
        command_id = self._admit()
        self.assertIsNotNone(command_id)
        order = self.state.team_commands[command_id]
        self.assertEqual('ATTENTIONTOCELL', order['command'])
        self.assertEqual([5], order['recipient_bot_ids'])
        self.assertEqual(1, order['issuer_id'])
        self.assertEqual(20, order['cell_index'])
        self.assertTrue(order['from_chat'])

    def test_the_order_reaches_the_planner_projection(self):
        self._admit()
        with self.state.lock:
            active = self.state._active_team_commands_locked()
        self.assertEqual(1, len(active))
        self.assertEqual('ATTENTIONTOCELL', active[0]['command'])

    def test_a_chat_order_publishes_no_stock_terminal(self):
        sent = []
        self.player.offer_reliable = lambda message: sent.append(message)
        command_id = self._admit()
        with self.state.lock:
            self.state._publish_team_command_terminal(
                self.player, self.state.team_commands[command_id], 'expired')
        self.assertEqual([], sent)

    def test_an_unknown_command_is_refused(self):
        self.assertIsNone(self._admit(self._line(command='ATTACKENEMY')))

    def test_a_finished_battle_admits_nothing(self):
        self.state.battle_result = {'winner': 1}
        self.assertIsNone(self._admit())

    def test_a_dead_or_departed_asker_admits_nothing(self):
        for attribute in ('alive', 'connected', 'participating'):
            setattr(self.player, attribute, False)
            self.assertIsNone(self._admit(), attribute)
            setattr(self.player, attribute, True)

    def test_an_asker_on_the_other_team_admits_nothing(self):
        self.player.team = 2
        self.assertIsNone(self._admit())

    def test_an_unknown_asker_admits_nothing(self):
        self.assertIsNone(self._admit(self._line(asked_by=99)))

    def test_a_dead_bot_admits_nothing(self):
        self.state.bot_states[5]['alive'] = False
        self.assertIsNone(self._admit())

    def test_going_home_is_admitted_when_the_team_has_a_base(self):
        # Both teams must have a base before either is usable.
        self.state.capture_bases = {1: [{'x': 0.0, 'z': 0.0}],
                                    2: [{'x': 100.0, 'z': 100.0}]}
        self.assertIsNotNone(self._admit(
            self._line(command='BACKTOBASE', cell_index=None)))

    def test_going_home_needs_a_base_to_go_to(self):
        self.state.capture_bases = {}
        self.assertIsNone(self._admit(
            self._line(command='BACKTOBASE', cell_index=None)))

    def test_holding_needs_nothing_further(self):
        self.assertIsNotNone(self._admit(
            self._line(command='STOP', cell_index=None)))

    def test_a_cell_outside_the_grid_admits_nothing(self):
        self.assertIsNone(self._admit(self._line(cell_index=100)))

    def test_a_missing_cell_admits_nothing(self):
        line = self._line()
        del line['order']['cell_index']
        self.assertIsNone(self._admit(line))

    def test_a_line_with_no_order_admits_nothing(self):
        line = self._line()
        del line['order']
        self.assertIsNone(self._admit(line))


if __name__ == '__main__':
    unittest.main()
