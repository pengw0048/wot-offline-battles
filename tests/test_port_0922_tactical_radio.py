import importlib.util
from pathlib import Path
import pickle
import struct
import sys
import types
import unittest
from unittest import mock
import zlib


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import tactical_radio
from gui.mods.offline_lan_0922.account_rpc.server import FakeServer
from gui.mods.offline_lan_0922.entities.avatar_server import AvatarServerBridge


def _args(int32_arg=0, int64_arg=0, float_arg=0.0,
          str_arg1='', str_arg2=''):
    return {
        'int32Arg1': int32_arg,
        'int64Arg1': int64_arg,
        'floatArg1': float_arg,
        'strArg1': str_arg1,
        'strArg2': str_arg2,
    }


class _Avatar(object):
    def __init__(self):
        self.playerVehicleID = 101
        self.team = 1
        self.arena = types.SimpleNamespace(vehicles={
            101: {
                'team': 1, 'isAlive': True, 'accountDBID': 9001},
            102: {
                'team': 1, 'isAlive': True, 'accountDBID': 9002},
            103: {
                'team': 1, 'isAlive': True, 'accountDBID': 99002},
            104: {
                'team': 1, 'isAlive': False, 'accountDBID': 99003},
            201: {
                'team': 2, 'isAlive': True, 'accountDBID': 9003},
            202: {
                'team': 2, 'isAlive': False, 'accountDBID': 9004},
        })
        self.chat2 = []

    def messenger_onActionByServer_chat2(self, action_id, request_id, args):
        self.chat2.append((action_id, request_id, args))


class _FailingAvatar(_Avatar):
    def __init__(self):
        super().__init__()
        self.fail_once = set()

    def messenger_onActionByServer_chat2(self, action_id, request_id, args):
        super().messenger_onActionByServer_chat2(
            action_id, request_id, args)
        if action_id in self.fail_once:
            self.fail_once.remove(action_id)
            raise RuntimeError('stock channel listener failed')


class _Sender(object):
    def __init__(self):
        self.requests = []
        self.next_sequence = 40
        self.chat_requests = []
        self.next_chat_sequence = 70

    def send_team_command(self, request):
        self.requests.append(request)
        self.next_sequence += 1
        return self.next_sequence

    def send_team_chat(self, request):
        self.chat_requests.append(request)
        self.next_chat_sequence += 1
        return self.next_chat_sequence


class BattleRadioAdapterTests(unittest.TestCase):
    def setUp(self):
        self.avatar = _Avatar()
        self.sender = _Sender()
        self.current = self.avatar
        self.stock_ready = []
        self.scheduled = []
        self.adapter = tactical_radio.BattleRadioAdapter(
            self.avatar, self.sender, lambda: self.current,
            users_ready_notifier=lambda: self.stock_ready.append(
                tuple(item[0] for item in self.avatar.chat2)),
            callback_scheduler=lambda delay, callback: self.scheduled.append(
                (delay, callback)))

    def test_exact_fixed_command_contract_is_pinned(self):
        self.assertEqual({
            23: ('HELPME', None),
            24: ('FOLLOWME', 'ally'),
            25: ('ATTACK', None),
            26: ('BACKTOBASE', None),
            27: ('POSITIVE', None),
            28: ('NEGATIVE', None),
            29: ('ATTENTIONTOCELL', 'cell'),
            30: ('SPG_AIM_AREA', 'aim_area'),
            31: ('ATTACKENEMY', 'enemy'),
            32: ('TURNBACK', 'ally'),
            33: ('HELPMEEX', 'ally'),
            34: ('SUPPORTMEWITHFIRE', 'enemy'),
            35: ('RELOADINGGUN', None),
            36: ('STOP', 'ally'),
            37: ('RELOADING_CASSETE', None),
            38: ('RELOADING_READY', None),
            39: ('RELOADING_READY_CASSETE', None),
            40: ('RELOADING_UNAVAILABLE', None),
        }, tactical_radio.COMMAND_SPECS)

    def test_stock_target_id_stays_explicit_until_runtime_maps_it(self):
        self.assertTrue(self.adapter.handle_client_action(
            24, 7, _args(102)))
        self.assertEqual({
            'command': 'FOLLOWME',
            'stock_action_id': 24,
            'stock_request_id': 7,
            'stock_target_id': 102,
            'target_relation': 'ally',
        }, self.sender.requests[-1])
        self.assertNotIn('target_id', self.sender.requests[-1])
        self.assertNotIn('target_kind', self.sender.requests[-1])

        self.assertTrue(self.adapter.handle_client_action(
            31, 8, _args(201)))
        self.assertEqual('enemy',
                         self.sender.requests[-1]['target_relation'])
        self.assertEqual(201,
                         self.sender.requests[-1]['stock_target_id'])

    def test_minimap_float_cell_is_normalized_like_int32_mailbox(self):
        self.assertTrue(self.adapter.handle_client_action(
            29, 9, _args(23.0)))
        self.assertEqual(23, self.sender.requests[-1]['cell_index'])
        self.assertFalse(self.adapter.handle_client_action(
            29, 10, _args(23.5)))
        self.assertFalse(self.adapter.handle_client_action(
            29, 11, _args(100)))

    def test_target_relation_and_alive_state_are_checked_before_send(self):
        self.assertFalse(self.adapter.handle_client_action(
            24, 1, _args(201)))
        self.assertFalse(self.adapter.handle_client_action(
            31, 2, _args(102)))
        self.assertFalse(self.adapter.handle_client_action(
            31, 3, _args(202)))
        self.assertFalse(self.adapter.handle_client_action(
            24, 4, _args(101)))
        self.assertEqual([], self.sender.requests)

    def test_ack_uses_account_dbid_for_stock_dispatch(self):
        self.assertTrue(self.adapter.handle_client_action(
            24, 7, _args(102)))

        self.assertTrue(self.adapter.receive_ack(41, True, [99002]))

        self.assertEqual(2, len(self.avatar.chat2))
        response, reply = self.avatar.chat2
        self.assertEqual((0, 7), response[:2])
        self.assertEqual((27, 0), reply[:2])
        self.assertEqual(99002, reply[2]['int64Arg1'])

    def test_ack_identifies_all_assigned_bots_once_and_allows_no_responder(self):
        for recipients, expected in (([], []),
                                     ([99002, 99003, 9002], [99002, 99003, 9002]),
                                     ([99002, 99002, 9003], [99002])):
            with self.subTest(recipients=recipients):
                self.assertTrue(self.adapter.handle_client_action(25, 8, _args()))
                sequence = self.sender.next_sequence
                self.avatar.chat2 = []
                self.assertTrue(self.adapter.receive_ack(sequence, True, recipients))
                self.assertEqual(expected, [reply[2]['int64Arg1']
                                           for reply in self.avatar.chat2[1:]])
                before = list(self.avatar.chat2)
                self.assertFalse(self.adapter.receive_ack(sequence, True, recipients))
                self.assertEqual(before, self.avatar.chat2)

    def test_same_team_broadcast_owns_the_original_stock_echo(self):
        self.assertTrue(self.adapter.receive_command(
            'FOLLOWME', 9001, target_id=102))
        action_id, request_id, args = self.avatar.chat2[-1]
        self.assertEqual((24, 0, 9001, 102), (
            action_id, request_id, args['int64Arg1'], args['int32Arg1']))

    def test_stock_argument_mapping_must_have_the_exact_five_keys(self):
        incomplete = _args()
        incomplete.pop('strArg2')
        self.assertFalse(self.adapter.handle_client_action(
            25, 2, incomplete))
        extended = _args()
        extended['extra'] = 1
        self.assertFalse(self.adapter.handle_client_action(
            25, 3, extended))

    def test_stock_zero_request_id_wrap_still_sends_the_command(self):
        self.assertTrue(self.adapter.handle_client_action(25, 0, _args()))
        self.assertEqual(0, self.sender.requests[-1]['stock_request_id'])
        self.assertTrue(self.adapter.receive_ack(41, True, []))
        self.assertEqual((0, 0), self.avatar.chat2[-1][:2])

    def test_dispatcher_does_not_assume_account_dbid_equals_entity_id(self):
        self.assertTrue(self.adapter.receive_command('POSITIVE', 99002))
        self.assertEqual(99002, self.avatar.chat2[-1][2]['int64Arg1'])
        self.assertFalse(self.adapter.receive_command('POSITIVE', 103))

    def test_validated_teammate_broadcast_survives_a_death_update_race(self):
        self.assertTrue(self.adapter.receive_command('POSITIVE', 99003))
        self.assertEqual(99003, self.avatar.chat2[-1][2]['int64Arg1'])

    def test_late_or_cross_avatar_ack_is_contained(self):
        self.assertTrue(self.adapter.handle_client_action(
            25, 5, _args()))
        self.current = _Avatar()
        self.assertFalse(self.adapter.receive_ack(41, True, [9002]))
        self.assertEqual([], self.avatar.chat2)
        self.current = self.avatar
        self.adapter.close()
        self.assertFalse(self.adapter.receive_ack(41, True, [9002]))
        self.assertFalse(self.adapter.handle_client_action(
            25, 6, _args()))

    def test_rejection_completes_stock_request_without_command_echo(self):
        self.assertTrue(self.adapter.handle_client_action(
            25, 12, _args()))
        self.assertTrue(self.adapter.receive_ack(41, False, []))
        self.assertEqual(1, len(self.avatar.chat2))
        action_id, request_id, args = self.avatar.chat2[0]
        self.assertEqual((1, 12, 1),
                         (action_id, request_id, args['int32Arg1']))

    def test_stock_team_chat_contract_and_normalization_are_pinned(self):
        self.assertEqual(19, tactical_radio.TEAM_CHAT_INIT_ACTION_ID)
        self.assertEqual(20, tactical_radio.TEAM_CHAT_DEINIT_ACTION_ID)
        self.assertEqual(21, tactical_radio.TEAM_CHAT_SEND_ACTION_ID)
        self.assertEqual(22, tactical_radio.TEAM_CHAT_RECEIVE_ACTION_ID)
        self.assertEqual(140, tactical_radio.MAX_TEAM_CHAT_LENGTH)
        self.assertEqual(
            'A hello 世界',
            tactical_radio.normalize_team_chat_text(
                '  Ａ\t hello   世界  '.encode('utf-8')))
        self.assertEqual(
            '<b>& raw',
            tactical_radio.normalize_team_chat_text(' <b>&  raw '))
        self.assertIsNone(
            tactical_radio.normalize_team_chat_text(b'\xff'))
        self.assertIsNone(
            tactical_radio.normalize_team_chat_text('\ud800'))
        # U+1F16A normalizes differently under the host's modern UCD and the
        # target Python 2.7 UCD.  Wire validation must preserve it unchanged.
        self.assertTrue(tactical_radio.is_valid_team_chat_text('\U0001f16a'))
        self.assertFalse(tactical_radio.is_valid_team_chat_text('   '))
        self.assertFalse(tactical_radio.is_valid_team_chat_text('\ud800'))

    def test_stock_text_limit_counts_windows_utf16_code_units(self):
        self.assertEqual(
            'a' * 138 + '\U0001f600',
            tactical_radio.normalize_team_chat_text(
                'a' * 138 + '\U0001f600'))
        self.assertIsNone(tactical_radio.normalize_team_chat_text(
            'a' * 139 + '\U0001f600'))
        self.assertEqual(
            'a' * 140,
            tactical_radio.normalize_team_chat_text('a' * 141))

    def test_team_chat_start_and_close_use_stock_arena_actions(self):
        self.assertTrue(self.adapter.start_team_chat())
        self.assertFalse(self.adapter.start_team_chat())
        self.assertEqual([(19,)], self.stock_ready)
        action_id, request_id, args = self.avatar.chat2[-1]
        self.assertEqual((19, 0), (action_id, request_id))
        self.assertEqual([], pickle.loads(zlib.decompress(args['strArg1'])))
        self.assertTrue(self.adapter.close_team_chat())
        self.assertFalse(self.adapter.close_team_chat())
        self.assertEqual((20, 0), self.avatar.chat2[-1][:2])

    def test_partial_stock_init_retains_deinit_ownership(self):
        avatar = _FailingAvatar()
        avatar.fail_once.add(19)
        adapter = tactical_radio.BattleRadioAdapter(
            avatar, self.sender, lambda: avatar,
            users_ready_notifier=lambda: None,
            callback_scheduler=lambda delay, callback: callback())
        with self.assertRaisesRegex(
                RuntimeError, 'stock channel listener failed'):
            adapter.start_team_chat()
        self.assertFalse(adapter.start_team_chat())
        self.assertTrue(adapter.close_team_chat())
        self.assertEqual([19, 20], [item[0] for item in avatar.chat2])

    def test_users_ready_failure_retains_deinit_ownership(self):
        avatar = _Avatar()

        def fail_users_ready():
            raise RuntimeError('stock users listener failed')

        adapter = tactical_radio.BattleRadioAdapter(
            avatar, self.sender, lambda: avatar,
            users_ready_notifier=fail_users_ready,
            callback_scheduler=lambda delay, callback: callback())
        with self.assertRaisesRegex(
                RuntimeError, 'stock users listener failed'):
            adapter.start_team_chat()
        self.assertFalse(adapter.start_team_chat())
        self.assertTrue(adapter.close_team_chat())
        self.assertEqual([19, 20], [item[0] for item in avatar.chat2])

    def test_partial_stock_deinit_can_be_retried(self):
        avatar = _FailingAvatar()
        adapter = tactical_radio.BattleRadioAdapter(
            avatar, self.sender, lambda: avatar,
            users_ready_notifier=lambda: None,
            callback_scheduler=lambda delay, callback: callback())
        self.assertTrue(adapter.start_team_chat())
        avatar.fail_once.add(20)
        with self.assertRaisesRegex(
                RuntimeError, 'stock channel listener failed'):
            adapter.close_team_chat()
        self.assertTrue(adapter.close_team_chat())
        self.assertEqual([19, 20, 20], [item[0] for item in avatar.chat2])

    def test_stock_team_text_is_sent_and_acknowledged_without_local_echo(self):
        self.assertTrue(self.adapter.start_team_chat())
        self.avatar.chat2 = []
        self.assertTrue(self.adapter.handle_client_action(
            21, 15, _args(str_arg1='hello  team')))
        self.assertEqual([{'text': 'hello team'}],
                         self.sender.chat_requests)
        self.assertEqual([], self.avatar.chat2)
        self.assertTrue(self.adapter.receive_chat_ack(71, True))
        self.assertEqual((0, 15), self.avatar.chat2[-1][:2])
        self.assertFalse(self.adapter.receive_chat_ack(71, True))

    def test_common_text_is_rejected_instead_of_relabelled_as_team(self):
        self.assertTrue(self.adapter.start_team_chat())
        self.avatar.chat2 = []
        self.assertFalse(self.adapter.handle_client_action(
            21, 16, _args(int32_arg=1, str_arg1='common')))
        self.assertEqual([], self.sender.chat_requests)
        self.assertEqual([], self.avatar.chat2)
        self.assertEqual(1, len(self.scheduled))
        self.assertEqual(0.0, self.scheduled[0][0])
        self.scheduled.pop(0)[1]()
        self.assertEqual((1, 16, 1), (
            self.avatar.chat2[-1][0], self.avatar.chat2[-1][1],
            self.avatar.chat2[-1][2]['int32Arg1']))

    def test_same_team_text_relay_uses_stock_inbound_filter_path(self):
        self.assertTrue(self.adapter.start_team_chat())
        self.avatar.chat2 = []
        self.assertTrue(self.adapter.receive_team_chat('<b>& raw', 99002))
        action_id, request_id, args = self.avatar.chat2[-1]
        self.assertEqual((22, 0, 0, 99002, '<b>& raw'), (
            action_id, request_id, args['int32Arg1'],
            args['int64Arg1'], args['strArg1']))
        self.assertFalse(self.adapter.receive_team_chat('enemy', 9003))
        self.assertTrue(self.adapter.receive_team_chat('  changed', 99002))

    def test_command_detail_schema_and_bounds_are_explicit(self):
        self.assertEqual({
            'SPG_AIM_AREA': (('aim_point', 'reload_time'), ()),
            'ATTACKENEMY': ((), ('reload_time',)),
            'RELOADINGGUN': (('reload_time',), ()),
            'RELOADING_CASSETE': (
                ('reload_time', 'quantity'), ()),
            'RELOADING_READY_CASSETE': (('quantity',), ()),
        }, tactical_radio.COMMAND_DETAIL_FIELDS)
        self.assertTrue(tactical_radio.validate_command_details(
            'ATTACKENEMY', {}))
        self.assertTrue(tactical_radio.validate_command_details(
            'ATTACKENEMY', {'reload_time': 0.0}))
        self.assertTrue(tactical_radio.validate_command_details(
            'RELOADING_CASSETE', {
                'reload_time': 1.0, 'quantity': 255}))
        self.assertFalse(tactical_radio.validate_command_details(
            'RELOADINGGUN', {'reload_time': 0.0}))
        self.assertFalse(tactical_radio.validate_command_details(
            'RELOADING_READY_CASSETE', {'quantity': 256}))
        self.assertFalse(tactical_radio.validate_command_details(
            'SPG_AIM_AREA', {
                'aim_point': [5001.0, 0.0, 0.0], 'reload_time': 0.0}))
        self.assertFalse(tactical_radio.validate_command_details(
            'ATTACKENEMY', {'reload_time': 10 ** 10000}))

    def test_spg_and_reload_stock_payloads_project_to_details(self):
        record = struct.pack('<fffif', 12.5, -3.0, 99.25, 47, 8.0)
        self.assertTrue(self.adapter.handle_client_action(
            30, 21, _args(str_arg1=record)))
        request = self.sender.requests[-1]
        self.assertEqual(('SPG_AIM_AREA', 47), (
            request['command'], request['cell_index']))
        self.assertEqual([12.5, -3.0, 99.25],
                         request['details']['aim_point'])
        self.assertEqual(8.0, request['details']['reload_time'])

        self.assertTrue(self.adapter.handle_client_action(
            31, 22, _args(int32_arg=201, float_arg=6.0)))
        self.assertEqual({'reload_time': 6.0},
                         self.sender.requests[-1]['details'])
        self.assertTrue(self.adapter.handle_client_action(
            35, 23, _args(float_arg=12.0)))
        self.assertTrue(self.adapter.handle_client_action(
            37, 24, _args(int32_arg=3, float_arg=11.0)))
        self.assertTrue(self.adapter.handle_client_action(
            38, 25, _args()))
        self.assertTrue(self.adapter.handle_client_action(
            39, 26, _args(int32_arg=2)))
        self.assertTrue(self.adapter.handle_client_action(
            40, 27, _args()))
        self.assertEqual([
            ('RELOADINGGUN', {'reload_time': 12.0}),
            ('RELOADING_CASSETE', {'reload_time': 11.0, 'quantity': 3}),
            ('RELOADING_READY', None),
            ('RELOADING_READY_CASSETE', {'quantity': 2}),
            ('RELOADING_UNAVAILABLE', None),
        ], [(item['command'], item.get('details'))
            for item in self.sender.requests[-5:]])

    def test_details_rebuild_exact_stock_payloads_on_receive(self):
        details = {'aim_point': [1.5, 2.5, -3.5], 'reload_time': 7.0}
        self.assertTrue(self.adapter.receive_command(
            'SPG_AIM_AREA', 99002, cell_index=64, details=details))
        action_id, unused_request_id, args = self.avatar.chat2[-1]
        self.assertEqual(30, action_id)
        self.assertEqual((1.5, 2.5, -3.5, 64, 7.0),
                         struct.unpack('<fffif', args['strArg1']))
        self.assertEqual(99002, args['int64Arg1'])

        self.assertTrue(self.adapter.receive_command(
            'RELOADING_CASSETE', 99002,
            details={'reload_time': 9.0, 'quantity': 4}))
        action_id, unused_request_id, args = self.avatar.chat2[-1]
        self.assertEqual((37, 4, 9.0), (
            action_id, args['int32Arg1'], args['floatArg1']))
        self.assertFalse(self.adapter.receive_command(
            'SPG_AIM_AREA', 99002, cell_index=64,
            details={'aim_point': [1.0, 2.0, 3.0]}))

    def test_deferred_rejection_is_fenced_by_avatar_and_close(self):
        self.assertTrue(self.adapter.start_team_chat())
        self.avatar.chat2 = []
        self.assertFalse(self.adapter.handle_client_action(
            21, 17, _args(int32_arg=1, str_arg1='common')))
        unused_delay, callback = self.scheduled.pop()
        self.current = _Avatar()
        callback()
        self.assertEqual([], self.avatar.chat2)

        self.current = self.avatar
        self.assertFalse(self.adapter.handle_client_action(
            21, 18, _args(int32_arg=1, str_arg1='common')))
        unused_delay, callback = self.scheduled.pop()
        self.adapter.close()
        self.avatar.chat2 = []
        callback()
        self.assertEqual([], self.avatar.chat2)

    def test_lan_send_failure_is_deferred_until_stock_registers_request(self):
        self.assertTrue(self.adapter.start_team_chat())
        self.avatar.chat2 = []
        self.sender.send_team_chat = lambda request: False
        self.assertFalse(self.adapter.handle_client_action(
            21, 18, _args(str_arg1='not admitted')))
        self.assertEqual([], self.avatar.chat2)
        self.assertEqual(1, len(self.scheduled))
        self.scheduled.pop()[1]()
        self.assertEqual((1, 18), self.avatar.chat2[-1][:2])

    def test_stock_users_ready_event_only_completes_ignore_rosters(self):
        received = []
        user_tag = types.SimpleNamespace(
            IGNORED='ignored', IGNORED_TMP='ignored_tmp')
        messenger = types.ModuleType('messenger')
        constants = types.ModuleType('messenger.m_constants')
        constants.USER_TAG = user_tag
        proto = types.ModuleType('messenger.proto')
        events = types.ModuleType('messenger.proto.events')
        events.g_messengerEvents = types.SimpleNamespace(
            users=types.SimpleNamespace(
                onUsersListReceived=lambda tags: received.append(tags)))
        with mock.patch.dict(sys.modules, {
                'messenger': messenger,
                'messenger.m_constants': constants,
                'messenger.proto': proto,
                'messenger.proto.events': events}):
            tactical_radio._notify_stock_ignore_lists_ready()
        self.assertEqual([{'ignored', 'ignored_tmp'}], received)


class _UnusedBinding(object):
    pass


class _UnusedBuilder(object):
    pass


class RadioMailboxIntegrationTests(unittest.TestCase):
    def test_avatar_bridge_owns_real_battle_mailbox_and_teardown(self):
        avatar = _Avatar()
        sender = _Sender()
        with mock.patch.object(
                tactical_radio, '_notify_stock_ignore_lists_ready'):
            bridge = AvatarServerBridge(
                avatar, _UnusedBinding(), _UnusedBuilder(), sender)
            self.assertTrue(bridge.start_team_chat())
        self.assertTrue(bridge.messenger_onActionByClient_chat2(
            21, 19, _args(str_arg1='team text')))
        self.assertTrue(bridge.receive_team_chat_ack(71, True))
        self.assertTrue(bridge.receive_team_chat('relay', 9002))
        self.assertTrue(bridge.close_team_chat())
        self.assertTrue(bridge.messenger_onActionByClient_chat2(
            29, 20, _args(44.0)))
        self.assertEqual(44, sender.requests[-1]['cell_index'])
        self.assertFalse(bridge.destroy())
        self.assertFalse(bridge.receive_team_command_ack(41, True, [9002]))

    def test_account_mailbox_delegates_only_when_adapter_is_scoped(self):
        calls = []
        adapter = types.SimpleNamespace(
            handle_client_action=lambda *args: calls.append(args) or True)
        player = types.SimpleNamespace()
        server = FakeServer(lambda: player, callback=lambda *args: None,
                            context={'battle_radio': adapter})

        self.assertTrue(server.messenger_onActionByClient_chat2(
            25, 4, _args()))
        self.assertEqual([(25, 4, _args())], calls)


if __name__ == '__main__':
    unittest.main()
