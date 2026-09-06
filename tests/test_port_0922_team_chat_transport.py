import json
from pathlib import Path
import socket
import sys
import threading
import unittest


TEST_ROOT = Path(__file__).resolve().parent
SERVER_ROOT = TEST_ROOT.parent / 'server'
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(SERVER_ROOT))

import bot_chat  # noqa: E402
import lan_battle_server as server_module  # noqa: E402
from lan_battle_server import (  # noqa: E402
    BattleState, CLIENT_BUILD_0922, ClientHandler, ThreadedTCPServer,
)
from effective_params_fixture import effective_params  # noqa: E402


class _Peer(object):
    def __init__(self, address):
        self.socket = socket.create_connection(address, timeout=2.0)
        self.socket.settimeout(2.0)
        self.stream = self.socket.makefile('rwb')

    def send(self, message):
        payload = json.dumps(
            message, ensure_ascii=False, separators=(',', ':')) + '\n'
        self.stream.write(payload.encode('utf-8'))
        self.stream.flush()

    def receive_until_all(self, predicates, limit=64):
        matched = [False] * len(predicates)
        messages = []
        for _unused in range(limit):
            line = self.stream.readline()
            if not line:
                raise AssertionError('connection closed before message barrier')
            message = json.loads(line.decode('utf-8'))
            messages.append(message)
            for index, predicate in enumerate(predicates):
                if not matched[index] and predicate(message):
                    matched[index] = True
            if all(matched):
                return messages
        raise AssertionError('message barrier was not reached: %r' % messages)

    def receive_until(self, predicate, limit=64):
        return self.receive_until_all((predicate,), limit=limit)

    def close(self):
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.stream.close()
        except OSError:
            pass
        try:
            self.socket.close()
        except OSError:
            pass


def _player_hello(name, account_key, team):
    return {
        'type': 'hello',
        'protocol': server_module.PROTOCOL_VERSION,
        'client_build': CLIENT_BUILD_0922,
        'capabilities': list(server_module.MODERN_CLIENT_REQUIRED_CAPABILITIES),
        'name': name,
        'account_key': account_key,
        'requested_team': team,
        'vehicle': 'ussr:R11_MS-1',
        'max_health': 90,
        'vehicle_compact_descr': 'dGVzdA==',
        'effective_params': effective_params(),
    }


def _type(kind):
    return lambda message: message.get('type') == kind


def _chat(kind, issuer_id, sequence):
    return lambda message: (
        message.get('type') == kind and
        message.get('issuer_id') == issuer_id and
        message.get('chat_seq') == sequence)


def _chat_ack(sequence):
    return lambda message: (
        message.get('type') == 'team_chat_ack' and
        message.get('chat_seq') == sequence)


def _command(kind, issuer_id, sequence):
    return lambda message: (
        message.get('type') == kind and
        message.get('issuer_id') == issuer_id and
        message.get('command_seq') == sequence)


def _command_ack(sequence):
    return lambda message: (
        message.get('type') == 'team_command_ack' and
        message.get('command_seq') == sequence)


class TeamChatTransportTests(unittest.TestCase):
    def setUp(self):
        self.state = BattleState(
            map_name='01_karelia', max_players=3,
            team1_size=2, team2_size=1)
        self.server = ThreadedTCPServer(
            ('127.0.0.1', 0), ClientHandler)
        self.server.game_server = type(
            'GameServer', (), {'state': self.state})()
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        self.peers = []

    def tearDown(self):
        for peer in self.peers:
            peer.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2.0)

    def _connect(self, name, account_key, team):
        peer = _Peer(self.server.server_address)
        self.peers.append(peer)
        peer.send(_player_hello(name, account_key, team))
        messages = peer.receive_until(_type('welcome'))
        welcome = next(
            message for message in messages if message.get('type') == 'welcome')
        self.assertEqual(team, welcome['team'])
        return peer, welcome['player_id']

    def _connect_teams(self):
        sender, sender_id = self._connect('Sender', 'chat_sender', 1)
        teammate, teammate_id = self._connect('Teammate', 'chat_teammate', 1)
        opponent, opponent_id = self._connect('Opponent', 'chat_opponent', 2)
        for sequence, peer in enumerate((sender, teammate, opponent), 1):
            peer.send({'type': 'ping', 'seq': sequence})
            peer.receive_until(
                lambda message, expected=sequence:
                message.get('type') == 'pong' and
                message.get('seq') == expected)
        with self.state.lock:
            self.state.phase = 'battle'
        self.assertEqual([], self.state.bot_manifest)
        return sender, sender_id, teammate, teammate_id, opponent, opponent_id

    def test_unicode_chat_reaches_sender_and_team_without_bots_or_enemy_leak(self):
        (sender, sender_id, teammate, unused_teammate_id,
         opponent, unused_opponent_id) = self._connect_teams()
        with self.state.lock:
            self.state.players[sender_id].alive = False

        text = '守住 E7! 装填中, 等等我...'
        sender.send({
            'type': 'team_chat',
            'round_id': self.state.round_id,
            'chat_seq': 1,
            'text': text,
        })
        sender_messages = sender.receive_until_all((
            _chat('team_chat', sender_id, 1), _chat_ack(1)))
        teammate_messages = teammate.receive_until(
            _chat('team_chat', sender_id, 1))

        broadcast = next(
            message for message in sender_messages
            if _chat('team_chat', sender_id, 1)(message))
        self.assertEqual({
            'type': 'team_chat',
            'protocol': server_module.PROTOCOL_VERSION,
            'round_id': self.state.round_id,
            'chat_seq': 1,
            'issuer_id': sender_id,
            'issuer_kind': 'human',
            'team': 1,
            'text': text,
        }, broadcast)
        self.assertEqual(broadcast, next(
            message for message in teammate_messages
            if _chat('team_chat', sender_id, 1)(message)))
        acknowledgement = next(
            message for message in sender_messages if _chat_ack(1)(message))
        self.assertEqual({
            'type': 'team_chat_ack',
            'protocol': server_module.PROTOCOL_VERSION,
            'round_id': self.state.round_id,
            'chat_seq': 1,
            'accepted': True,
            'code': 'accepted',
        }, acknowledgement)

        opponent.send({'type': 'ping', 'seq': 701})
        opponent_messages = opponent.receive_until(
            lambda message: message.get('type') == 'pong' and
            message.get('seq') == 701)
        self.assertFalse(any(
            _chat('team_chat', sender_id, 1)(message)
            for message in opponent_messages))

    def test_map_command_reaches_human_team_without_bots(self):
        (sender, sender_id, teammate, unused_teammate_id,
         opponent, unused_opponent_id) = self._connect_teams()
        with self.state.lock:
            self.state.players[sender_id].alive = False

        sender.send({
            'type': 'team_command',
            'round_id': self.state.round_id,
            'command_seq': 1,
            'command': 'ATTENTIONTOCELL',
            'cell_index': 42,
        })
        sender_messages = sender.receive_until_all((
            _command('team_command', sender_id, 1), _command_ack(1)))
        teammate_messages = teammate.receive_until(
            _command('team_command', sender_id, 1))

        publication = next(
            message for message in sender_messages
            if _command('team_command', sender_id, 1)(message))
        self.assertEqual(42, publication['cell_index'])
        self.assertEqual('ATTENTIONTOCELL', publication['command'])
        self.assertEqual(1, publication['team'])
        self.assertEqual(publication, next(
            message for message in teammate_messages
            if _command('team_command', sender_id, 1)(message)))
        acknowledgement = next(
            message for message in sender_messages
            if _command_ack(1)(message))
        self.assertTrue(acknowledgement['accepted'])
        self.assertEqual('accepted', acknowledgement['code'])
        self.assertEqual([], acknowledgement['recipient_bot_ids'])
        self.assertEqual(0, acknowledgement['expires_tick'])

        opponent.send({'type': 'ping', 'seq': 702})
        opponent_messages = opponent.receive_until(
            lambda message: message.get('type') == 'pong' and
            message.get('seq') == 702)
        self.assertFalse(any(
            _command('team_command', sender_id, 1)(message)
            for message in opponent_messages))

    def test_duplicate_and_wrong_shape_are_local_and_transport_survives(self):
        (sender, sender_id, teammate, teammate_id,
         unused_opponent, unused_opponent_id) = self._connect_teams()
        request = {
            'type': 'team_chat',
            'round_id': self.state.round_id,
            'chat_seq': 1,
            'text': 'First line.',
        }
        sender.send(request)
        sender.receive_until_all((
            _chat('team_chat', sender_id, 1), _chat_ack(1)))
        teammate.receive_until(_chat('team_chat', sender_id, 1))

        sender.send(request)
        duplicate_messages = sender.receive_until(_chat_ack(1))
        self.assertFalse(any(
            _chat('team_chat', sender_id, 1)(message)
            for message in duplicate_messages))
        duplicate_ack = next(
            message for message in duplicate_messages if _chat_ack(1)(message))
        self.assertTrue(duplicate_ack['accepted'])

        teammate.send({
            'type': 'team_chat',
            'round_id': self.state.round_id,
            'chat_seq': 1,
            'text': 'Publication barrier.',
        })
        teammate_messages = teammate.receive_until_all((
            _chat('team_chat', teammate_id, 1), _chat_ack(1)))
        self.assertFalse(any(
            _chat('team_chat', sender_id, 1)(message)
            for message in teammate_messages))

        sender.send({
            'type': 'team_chat',
            'round_id': self.state.round_id,
            'chat_seq': 2,
            'text': 'Bad shape.',
            'unexpected': True,
        })
        sender.send({
            'type': 'team_chat',
            'round_id': self.state.round_id,
            'chat_seq': 3,
            'text': 'Still connected.',
        })
        recovery_messages = sender.receive_until_all((
            lambda message: _chat_ack(2)(message) and
            not message.get('accepted') and
            message.get('code') == 'invalid_shape',
            _chat('team_chat', sender_id, 3),
            lambda message: _chat_ack(3)(message) and
            message.get('accepted')))
        self.assertFalse(any(
            message.get('type') == 'team_chat' and
            message.get('issuer_id') == sender_id and
            message.get('chat_seq') == 2
            for message in recovery_messages))
        self.assertTrue(self.state.players[sender_id].connected)

    def test_old_round_message_cannot_enter_the_reset_round(self):
        (sender, sender_id, teammate, unused_teammate_id,
         unused_opponent, unused_opponent_id) = self._connect_teams()
        old_round = self.state.round_id
        sender.send({
            'type': 'team_chat',
            'round_id': old_round,
            'chat_seq': 1,
            'text': 'Old accepted line.',
        })
        sender.receive_until_all((
            _chat('team_chat', sender_id, 1), _chat_ack(1)))
        teammate.receive_until(_chat('team_chat', sender_id, 1))

        with self.state.lock:
            self.state._reset_round()
            self.state.phase = 'battle'
            new_round = self.state.round_id
        self.assertEqual(old_round + 1, new_round)

        sender.send({
            'type': 'team_chat',
            'round_id': old_round,
            'chat_seq': 2,
            'text': 'Stale in the new round.',
        })
        sender.send({
            'type': 'team_chat',
            'round_id': new_round,
            'chat_seq': 1,
            'text': 'Current round barrier.',
        })
        sender_messages = sender.receive_until_all((
            lambda message: _chat('team_chat', sender_id, 1)(message) and
            message.get('round_id') == new_round,
            lambda message: _chat_ack(1)(message) and
            message.get('round_id') == new_round))
        teammate_messages = teammate.receive_until(
            lambda message: _chat('team_chat', sender_id, 1)(message) and
            message.get('round_id') == new_round)
        observed = sender_messages + teammate_messages
        self.assertFalse(any(
            message.get('type') == 'team_chat' and
            (message.get('round_id') == old_round or
             message.get('text') == 'Stale in the new round.')
            for message in observed))


class _ScriptedRandom(object):
    """Remove the randomness so one conversation rule is provable."""

    def random(self):
        return 0.0

    def uniform(self, low, high):
        return low

    def choice(self, sequence):
        return list(sequence)[0]


class _FixedBackend(object):
    def __init__(self, text='收到, 这就回来'):
        self.text = text

    def compose(self, request):
        return self.text


class BotTeamChatTransportTests(unittest.TestCase):
    """Cover the one wire change: a Bot as the issuer of a stock chat line."""

    def setUp(self):
        self.state = BattleState(
            map_name='01_karelia', max_players=3,
            team1_size=2, team2_size=1)
        self.server = ThreadedTCPServer(('127.0.0.1', 0), ClientHandler)
        self.server.game_server = type(
            'GameServer', (), {'state': self.state})()
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        self.peers = []

    def tearDown(self):
        for peer in self.peers:
            peer.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2.0)

    def _connect(self, name, account_key, team):
        peer = _Peer(self.server.server_address)
        self.peers.append(peer)
        peer.send(_player_hello(name, account_key, team))
        messages = peer.receive_until(_type('welcome'))
        welcome = next(message for message in messages
                       if message.get('type') == 'welcome')
        return peer, welcome['player_id']

    def _prepare(self, backend=None):
        sender, sender_id = self._connect('Sender', 'bot_chat_sender', 1)
        opponent, unused_id = self._connect('Opponent', 'bot_chat_enemy', 2)
        for sequence, peer in enumerate((sender, opponent), 1):
            peer.send({'type': 'ping', 'seq': sequence})
            peer.receive_until(
                lambda message, expected=sequence:
                message.get('type') == 'pong' and
                message.get('seq') == expected)
        with self.state.lock:
            self.state.phase = 'battle'
            self.state.bot_manifest = [{
                'id': 5, 'team': 1, 'slot': 1, 'name': '今天不加班',
                'vehicle': 'ussr:R04_T-34',
            }]
            self.state.bot_states = {5: {
                'id': 5, 'team': 1, 'alive': True, 'x': 12.0, 'z': 12.0,
                'health': 400, 'max_health': 400,
            }}
            self.state.bot_chat = bot_chat.BotChatDirector(
                _ScriptedRandom(), tick_hz=server_module.TICK_HZ,
                backend=backend or _FixedBackend())
            self.state.bot_chat.reset_round(self.state.round_id)
            self.state._next_bot_chat_tick = 0
        return sender, sender_id, opponent

    def _run_chat_ticks(self, limit=400):
        published = False
        for tick in range(limit):
            with self.state.lock:
                self.state.tick = tick
            published = self.state.publish_bot_team_chat() or published
        return published

    def test_an_addressed_bot_answers_on_the_stock_channel(self):
        sender, unused_sender_id, opponent = self._prepare()
        sender.send({'type': 'team_chat', 'round_id': self.state.round_id,
                     'chat_seq': 1, 'text': '那个t34 回来一下'})
        sender.receive_until(_chat_ack(1))
        self.assertTrue(self._run_chat_ticks())
        line = next(
            message for message in sender.receive_until(
                lambda message: message.get('type') == 'team_chat' and
                message.get('issuer_kind') == 'bot')
            if message.get('issuer_kind') == 'bot')
        self.assertEqual(line['issuer_id'], 5)
        self.assertEqual(line['team'], 1)
        self.assertEqual(line['round_id'], self.state.round_id)
        self.assertEqual(line['text'], '收到, 这就回来')
        self.assertGreaterEqual(line['chat_seq'], 1)

    def test_a_bot_line_never_reaches_the_other_team(self):
        sender, unused_sender_id, opponent = self._prepare()
        sender.send({'type': 'team_chat', 'round_id': self.state.round_id,
                     'chat_seq': 1, 'text': '那个t34 回来一下'})
        sender.receive_until(_chat_ack(1))
        self._run_chat_ticks()
        opponent.send({'type': 'ping', 'seq': 99})
        seen = opponent.receive_until(
            lambda message: message.get('type') == 'pong' and
            message.get('seq') == 99)
        self.assertFalse([message for message in seen
                          if message.get('type') == 'team_chat'])

    def test_an_unpublishable_line_is_dropped_without_a_sequence(self):
        sender, unused_sender_id, unused_opponent = self._prepare(
            backend=_FixedBackend('   '))
        sender.send({'type': 'team_chat', 'round_id': self.state.round_id,
                     'chat_seq': 1, 'text': '那个t34 回来一下'})
        sender.receive_until(_chat_ack(1))
        self.assertFalse(self._run_chat_ticks())
        with self.state.lock:
            self.assertEqual({}, self.state.bot_chat_seq)

    def test_a_finished_battle_stops_the_conversation(self):
        sender, unused_sender_id, unused_opponent = self._prepare()
        sender.send({'type': 'team_chat', 'round_id': self.state.round_id,
                     'chat_seq': 1, 'text': '那个t34 回来一下'})
        sender.receive_until(_chat_ack(1))
        with self.state.lock:
            self.state.battle_result = {'winner': 1}
        self.assertFalse(self._run_chat_ticks())


if __name__ == '__main__':
    unittest.main()
