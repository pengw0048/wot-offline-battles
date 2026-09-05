import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import tactical_radio
from gui.mods.offline_lan_0922.account_rpc.server import FakeServer
from gui.mods.offline_lan_0922.entities.avatar_server import AvatarServerBridge


def _args(int32_arg=0):
    return {
        'int32Arg1': int32_arg,
        'int64Arg1': 0,
        'floatArg1': 0.0,
        'strArg1': '',
        'strArg2': '',
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


class _Sender(object):
    def __init__(self):
        self.requests = []
        self.next_sequence = 40

    def send_team_command(self, request):
        self.requests.append(request)
        self.next_sequence += 1
        return self.next_sequence


class BattleRadioAdapterTests(unittest.TestCase):
    def setUp(self):
        self.avatar = _Avatar()
        self.sender = _Sender()
        self.current = self.avatar
        self.adapter = tactical_radio.BattleRadioAdapter(
            self.avatar, self.sender, lambda: self.current)

    def test_exact_fixed_command_contract_is_pinned(self):
        self.assertEqual({
            23: ('HELPME', None),
            24: ('FOLLOWME', 'ally'),
            25: ('ATTACK', None),
            26: ('BACKTOBASE', None),
            27: ('POSITIVE', None),
            28: ('NEGATIVE', None),
            29: ('ATTENTIONTOCELL', 'cell'),
            31: ('ATTACKENEMY', 'enemy'),
            32: ('TURNBACK', 'ally'),
            33: ('HELPMEEX', 'ally'),
            34: ('SUPPORTMEWITHFIRE', 'enemy'),
            36: ('STOP', 'ally'),
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


class _UnusedBinding(object):
    pass


class _UnusedBuilder(object):
    pass


class RadioMailboxIntegrationTests(unittest.TestCase):
    def test_avatar_bridge_owns_real_battle_mailbox_and_teardown(self):
        avatar = _Avatar()
        sender = _Sender()
        bridge = AvatarServerBridge(
            avatar, _UnusedBinding(), _UnusedBuilder(), sender)

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
