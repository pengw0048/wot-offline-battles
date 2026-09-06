from pathlib import Path
import random
import sys
import unittest


TEST_ROOT = Path(__file__).resolve().parent
SERVER_ROOT = TEST_ROOT.parent / 'server'
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(SERVER_ROOT))

import bot_chat  # noqa: E402
from bot_chat import (  # noqa: E402
    ADDRESS_CALLSIGN, ADDRESS_CELL, ADDRESS_CLASS, ADDRESS_NONE,
    ADDRESS_THREAD, ADDRESS_VEHICLE, BotChatDirector, MAX_CONVERSATION_HOPS,
    PERSONA_MECHANIC, PERSONA_SCOUT, PERSONA_SLACKER, PERSONA_TACTICAL,
    TEAM_BUDGET_LINES, TRIGGER_DOWN, TRIGGER_HOP, TRIGGER_KILL,
    TRIGGER_REPLY, clamp_chat_text, persona_for_callsign, vehicle_token,
)


class _ScriptedRandom(object):
    """A random source with no randomness, so a rule is provable."""

    def __init__(self, value=0.0, choice_index=0):
        self.value = value
        self.choice_index = choice_index

    def random(self):
        return self.value

    def uniform(self, low, high):
        return low

    def choice(self, sequence):
        sequence = list(sequence)
        return sequence[self.choice_index % len(sequence)]


class _CountingBackend(object):
    """Emit a distinct line every time so the repeat filter is not the subject."""

    def __init__(self):
        self.calls = []
        self.count = 0

    def compose(self, request):
        self.count += 1
        self.calls.append(request)
        return 'line-%d' % self.count


class _FailingBackend(object):
    def compose(self, request):
        raise RuntimeError('backend is unavailable')


def _bot(bot_id, name, vehicle, vehicle_class, x=0.0, z=0.0, team=1,
         alive=True):
    return {'id': bot_id, 'team': team, 'name': name, 'vehicle': vehicle,
            'vehicle_class': vehicle_class, 'alive': alive, 'x': x, 'z': z,
            'hp': 400, 'max_hp': 400, 'combat_mode': 'route'}


def _snapshot(bots, spotted=(), speaker_x=0.0, speaker_z=0.0,
              arena_bounds=None):
    return {
        'bots': list(bots),
        'speaker': {'name': 'Peng', 'x': speaker_x, 'z': speaker_z,
                    'spotted': set(spotted)},
        'arena_bounds': arena_bounds,
    }


def _roster():
    return [
        _bot(1, '今天不加班', 'ussr:R04_T-34', 'mediumTank', 10.0, 10.0),
        _bot(2, '北方孤狼', 'germany:G54_E-50', 'heavyTank', 50.0, 50.0),
        _bot(3, '草丛观察员', 'ussr:R11_MS-1', 'lightTank', 5.0, 5.0),
        _bot(4, '履带又掉了', 'ussr:R04_T-34', 'mediumTank', 90.0, 90.0),
    ]


def _director(value=0.0, choice_index=0, backend=None):
    director = BotChatDirector(_ScriptedRandom(value, choice_index),
                               tick_hz=30.0,
                               backend=backend or _CountingBackend())
    director.reset_round(7)
    return director


def _drain(director, snapshot, start=0, stop=600):
    published = []
    for tick in range(start, stop):
        published.extend(director.tick(tick, snapshot))
    return published


class VehicleTokenTest(unittest.TestCase):
    def test_strips_nation_and_internal_index(self):
        self.assertEqual(vehicle_token('ussr:R04_T-34'), 't34')
        self.assertEqual(vehicle_token('germany:G54_E-50'), 'e50')
        self.assertEqual(vehicle_token('ussr:R11_MS-1'), 'ms1')

    def test_tolerates_missing_nation_and_index(self):
        self.assertEqual(vehicle_token('T-34'), 't34')
        self.assertEqual(vehicle_token(''), '')
        self.assertEqual(vehicle_token(None), '')


class AddressResolutionTest(unittest.TestCase):
    def setUp(self):
        self.director = _director()
        self.snapshot = _snapshot(_roster())

    def _resolve(self, text, snapshot=None):
        return self.director.resolve_address(
            text, 1, snapshot or self.snapshot)

    def test_players_name_the_vehicle_not_the_callsign(self):
        for text in ('那个t34 过来', '那个T-34过来', 't34 别走', 'T 34 顶住'):
            address = self._resolve(text)
            self.assertEqual(address['kind'], ADDRESS_VEHICLE, text)
            self.assertEqual(address['bot_ids'], [1], text)

    def test_a_longer_designation_does_not_match_a_shorter_one(self):
        # ``T-34`` must not answer for ``T-34-85``: the folded token would be
        # a prefix of it, which is exactly the ambiguity players complain of.
        snapshot = _snapshot([_bot(1, '今天不加班', 'ussr:R04_T-34',
                                   'mediumTank')])
        self.assertEqual(self._resolve('t3485 上', snapshot)['kind'],
                         ADDRESS_NONE)

    def test_callsign_shorthand_resolves(self):
        for text in ('孤狼 顶一下', '北方孤狼 顶一下', '北方 顶一下'):
            address = self._resolve(text)
            self.assertEqual(address['kind'], ADDRESS_CALLSIGN, text)
            self.assertEqual(address['bot_ids'], [2], text)

    def test_class_words_address_a_squad(self):
        address = self._resolve('轻坦回家')
        self.assertEqual(address['kind'], ADDRESS_CLASS)
        self.assertEqual(address['bot_ids'], [3])

    def test_class_word_without_that_class_present_addresses_nobody(self):
        self.assertEqual(self._resolve('火炮打一下')['kind'], ADDRESS_NONE)

    def test_two_of_one_model_prefer_the_vehicle_the_player_can_see(self):
        spotted = _snapshot(_roster(), spotted=(4,))
        self.assertEqual(self._resolve('那个t34 别冲', spotted)['bot_ids'], [4])

    def test_two_of_one_model_otherwise_prefer_the_nearest(self):
        self.assertEqual(self._resolve('那个t34 别冲')['bot_ids'], [1])

    def test_grid_cell_addresses_the_bot_standing_in_it(self):
        # The stock minimap puts A1 at the top left, so row 1 is the highest
        # z and the Bot parked near the origin is in A10, not A1.
        bounds = (0.0, 0.0, 100.0, 100.0)
        roster = _roster() + [_bot(5, '山口守门员', 'usa:A20_M4_Sherman',
                                   'mediumTank', 5.0, 95.0)]
        snapshot = _snapshot(roster, arena_bounds=bounds)
        top_left = self._resolve('a1 有人吗', snapshot)
        self.assertEqual(top_left['kind'], ADDRESS_CELL)
        self.assertEqual(top_left['bot_ids'], [5])
        bottom_left = self._resolve('a10 有人吗', snapshot)
        self.assertEqual(bottom_left['kind'], ADDRESS_CELL)
        self.assertEqual(bottom_left['bot_ids'], [3])

    def test_an_empty_grid_cell_addresses_nobody(self):
        bounds = (0.0, 0.0, 100.0, 100.0)
        snapshot = _snapshot(_roster(), arena_bounds=bounds)
        self.assertEqual(self._resolve('j5 有人吗', snapshot)['kind'],
                         ADDRESS_NONE)

    def test_grid_cell_without_arena_bounds_addresses_nobody(self):
        self.assertEqual(self._resolve('a1 有人吗')['kind'], ADDRESS_NONE)

    def test_open_remark_addresses_nobody(self):
        self.assertEqual(self._resolve('这局有点难打')['kind'], ADDRESS_NONE)

    def test_no_living_teammate_addresses_nobody(self):
        dead = [dict(bot, alive=False) for bot in _roster()]
        self.assertEqual(self._resolve('那个t34 过来', _snapshot(dead)),
                         {'kind': ADDRESS_NONE, 'bot_ids': []})

    def test_the_other_team_is_never_addressed(self):
        snapshot = _snapshot([_bot(9, '北方孤狼', 'ussr:R04_T-34',
                                   'mediumTank', team=2)])
        self.assertEqual(self._resolve('那个t34 过来', snapshot)['kind'],
                         ADDRESS_NONE)


class PersonaTest(unittest.TestCase):
    def test_shipped_callsigns_carry_their_own_voice(self):
        self.assertEqual(persona_for_callsign('今天不加班'), PERSONA_SLACKER)
        self.assertEqual(persona_for_callsign('履带又掉了'), PERSONA_MECHANIC)
        self.assertEqual(persona_for_callsign('草丛观察员'), PERSONA_SCOUT)
        self.assertEqual(persona_for_callsign('北方孤狼'), PERSONA_TACTICAL)

    def test_an_unclassified_callsign_still_gets_a_stable_voice(self):
        first = persona_for_callsign('七号车组')
        self.assertIn(first, bot_chat.PERSONAS)
        self.assertEqual(first, persona_for_callsign('七号车组'))

    def test_a_bot_keeps_one_persona_for_the_round(self):
        director = _director()
        first = director.persona(1, '今天不加班')
        self.assertEqual(first, director.persona(1, '完全换了一个名字'))


class ReplyAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.snapshot = _snapshot(_roster())

    def test_an_addressed_bot_answers(self):
        director = _director()
        director.observe_player_line(0, 1, '那个t34 过来', self.snapshot)
        published = _drain(director, self.snapshot)
        self.assertEqual(published[0]['bot_id'], 1)
        self.assertLessEqual(len(published), 1 + MAX_CONVERSATION_HOPS)

    def test_a_squad_call_may_answer_with_two_voices(self):
        roster = _roster() + [_bot(5, '我先探个路', 'ussr:R11_MS-1',
                                   'lightTank', 8.0, 8.0)]
        snapshot = _snapshot(roster)
        director = _director()
        director.observe_player_line(0, 1, '轻坦回家', snapshot)
        published = _drain(director, snapshot)
        self.assertEqual(sorted(line['bot_id'] for line in published
                                if line['bot_id'] in (3, 5)), [3, 5])

    def test_an_open_remark_usually_still_gets_an_answer(self):
        director = _director()
        director.observe_player_line(0, 1, '这局打得有点难受', self.snapshot)
        self.assertTrue(_drain(director, self.snapshot))

    def test_silence_stays_legal(self):
        director = _director(value=1.0)
        director.observe_player_line(0, 1, '这局打得有点难受', self.snapshot)
        self.assertEqual(_drain(director, self.snapshot), [])

    def test_a_reply_is_delayed_rather_than_instant(self):
        director = _director()
        director.observe_player_line(0, 1, '那个t34 过来', self.snapshot)
        self.assertEqual(director.tick(0, self.snapshot), [])

    def test_a_dead_bot_does_not_answer(self):
        roster = _roster()
        roster[0]['alive'] = False
        snapshot = _snapshot(roster)
        director = _director()
        director.observe_player_line(0, 1, '孤狼 顶一下', snapshot)
        roster[1]['alive'] = False
        self.assertEqual(_drain(director, snapshot), [])


class ConversationContinuityTest(unittest.TestCase):
    def setUp(self):
        self.snapshot = _snapshot(_roster())

    def test_an_unaddressed_follow_up_stays_with_who_was_addressed(self):
        # Other Bots chime in on top of the answer.  The player is still
        # talking to the teammate they named.
        director = _director()
        director.observe_player_line(0, 1, '孤狼 顶一下', self.snapshot)
        published = _drain(director, self.snapshot, 0, 400)
        self.assertGreater(len(published), 1)
        address = director.resolve_address('你到了吗', 1, self.snapshot)
        self.assertEqual(address['kind'], ADDRESS_THREAD)
        self.assertEqual(address['bot_ids'], [2])

    def test_a_thread_falls_back_when_the_addressed_bot_dies(self):
        director = _director()
        director.observe_player_line(0, 1, '孤狼 顶一下', self.snapshot)
        _drain(director, self.snapshot, 0, 400)
        roster = [dict(bot, alive=bot['id'] != 2) for bot in _roster()]
        address = director.resolve_address('你到了吗', 1, _snapshot(roster))
        self.assertEqual(address['kind'], ADDRESS_THREAD)
        self.assertNotEqual(address['bot_ids'], [2])

    def test_a_thread_expires_after_silence(self):
        director = _director()
        director.observe_player_line(0, 1, '孤狼 顶一下', self.snapshot)
        _drain(director, self.snapshot, 0, 200)
        silence = int(bot_chat.THREAD_SILENCE_SECONDS * 30.0) + 400
        _drain(director, self.snapshot, 200, silence)
        self.assertEqual(
            director.resolve_address('你到了吗', 1, self.snapshot)['kind'],
            ADDRESS_NONE)

    def test_bots_answer_each_other_but_the_chain_terminates(self):
        director = _director()
        director.observe_player_line(0, 1, '那个t34 过来', self.snapshot)
        published = _drain(director, self.snapshot, 0, 1200)
        hops = [line for line in published
                if line['bot_id'] != 1]
        self.assertTrue(hops, 'expected at least one Bot to answer another')
        self.assertLessEqual(len(hops), MAX_CONVERSATION_HOPS)

    def test_a_named_reply_addresses_the_player_back(self):
        backend = _CountingBackend()
        director = _director(backend=backend)
        director.observe_player_line(0, 1, '那个t34 过来', self.snapshot)
        _drain(director, self.snapshot)
        reply = backend.calls[0]
        self.assertEqual(reply['trigger'], TRIGGER_REPLY)
        self.assertEqual(reply['address_kind'], ADDRESS_VEHICLE)
        self.assertEqual(reply['address_prefix'], 'Peng')

    def test_a_hop_reply_does_not_address_the_player(self):
        backend = _CountingBackend()
        director = _director(backend=backend)
        director.observe_player_line(0, 1, '那个t34 过来', self.snapshot)
        _drain(director, self.snapshot, 0, 1200)
        hop_calls = [call for call in backend.calls
                     if call['trigger'] == TRIGGER_HOP]
        self.assertTrue(hop_calls)
        self.assertIsNone(hop_calls[0]['address_prefix'])

    def test_the_backend_sees_the_rolling_transcript(self):
        backend = _CountingBackend()
        director = _director(backend=backend)
        director.observe_player_line(0, 1, '那个t34 过来', self.snapshot)
        _drain(director, self.snapshot)
        recent = backend.calls[0]['recent']
        self.assertEqual(recent[-1], {'name': 'Peng', 'text': '那个t34 过来'})


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self.snapshot = _snapshot(
            [_bot(index, '车组%d' % index, 'ussr:R04_T-34', 'mediumTank',
                  float(index), float(index))
             for index in range(1, 12)])

    def test_a_team_cannot_flood_the_channel(self):
        director = _director()
        for index in range(1, 12):
            director.observe_player_line(index, 1, '有人吗？', self.snapshot)
        published = _drain(director, self.snapshot, 0, 300)
        self.assertLessEqual(len(published), TEAM_BUDGET_LINES)

    def test_one_bot_does_not_answer_twice_in_a_row(self):
        director = _director()
        director.observe_player_line(0, 1, '车组1 在吗', self.snapshot)
        director.observe_player_line(5, 1, '车组1 还在吗', self.snapshot)
        published = _drain(director, self.snapshot, 0, 200)
        speakers = [line['bot_id'] for line in published]
        self.assertEqual(len(speakers), len(set(speakers)))


class PublicationTest(unittest.TestCase):
    def setUp(self):
        self.snapshot = _snapshot(_roster())

    def test_a_backend_failure_is_contained_to_one_line(self):
        director = _director(backend=_FailingBackend())
        director.observe_player_line(0, 1, '那个t34 过来', self.snapshot)
        self.assertEqual(_drain(director, self.snapshot), [])
        director.set_backend(_CountingBackend())
        director.observe_player_line(600, 1, '孤狼 顶一下', self.snapshot)
        self.assertTrue(_drain(director, self.snapshot, 600, 800))

    def test_an_identical_line_is_not_repeated(self):
        class _Stuck(object):
            def compose(self, request):
                return '收到'

        director = _director(backend=_Stuck())
        director.observe_player_line(0, 1, '那个t34 过来', self.snapshot)
        published = _drain(director, self.snapshot, 0, 1200)
        self.assertEqual(len(published), 1)

    def test_an_event_can_start_a_line_with_nobody_speaking(self):
        director = _director()
        self.assertTrue(
            director.observe_event(0, 1, TRIGGER_KILL, 2, self.snapshot))
        published = _drain(director, self.snapshot)
        self.assertEqual(published[0]['bot_id'], 2)

    def test_an_unknown_event_is_refused(self):
        director = _director()
        self.assertFalse(
            director.observe_event(0, 1, 'celebrate', 2, self.snapshot))

    def test_a_destroyed_bot_may_still_report_its_own_death(self):
        roster = _roster()
        director = _director()
        self.assertTrue(
            director.observe_event(0, 1, TRIGGER_DOWN, 2, _snapshot(roster)))
        roster[1]['alive'] = False
        published = _drain(director, _snapshot(roster))
        self.assertEqual(published[0]['bot_id'], 2)

    def test_reset_round_clears_every_conversation(self):
        director = _director()
        director.observe_player_line(0, 1, '孤狼 顶一下', self.snapshot)
        director.reset_round(8)
        self.assertEqual(_drain(director, self.snapshot), [])
        self.assertEqual(director.recent_lines(1), [])


class ClampTest(unittest.TestCase):
    def test_whitespace_is_collapsed(self):
        self.assertEqual(clamp_chat_text('  收到   ,  这就去 '), '收到 , 这就去')

    def test_an_over_long_line_is_truncated_to_the_stock_limit(self):
        text = clamp_chat_text('好' * 400)
        self.assertEqual(len(text.encode('utf-16-le')) // 2,
                         bot_chat.MAX_CHAT_UTF16_UNITS)

    def test_empty_and_non_text_are_refused(self):
        self.assertIsNone(clamp_chat_text('   '))
        self.assertIsNone(clamp_chat_text(None))
        self.assertIsNone(clamp_chat_text(b'\xe5\xa5\xbd'))


class DeterminismTest(unittest.TestCase):
    def test_one_seed_replays_one_transcript(self):
        def transcript():
            director = BotChatDirector(random.Random(2024), tick_hz=30.0)
            director.reset_round(7)
            snapshot = _snapshot(_roster())
            director.observe_player_line(0, 1, '那个t34 过来', snapshot)
            director.observe_player_line(200, 1, '这局有点难打', snapshot)
            return [(tick, line['bot_id'], line['text'])
                    for tick in range(0, 900)
                    for line in director.tick(tick, snapshot)]

        first = transcript()
        self.assertTrue(first)
        self.assertEqual(first, transcript())


if __name__ == '__main__':
    unittest.main()
