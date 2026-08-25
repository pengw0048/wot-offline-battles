import base64
import json
import pickle
import sys
import tempfile
from pathlib import Path
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'))

from gui.mods.offline_lan_0922.account_rpc import commands, data, requests
from gui.mods.offline_lan_0922.account_rpc import postbattle_store
from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime
from gui.mods.offline_lan_0922 import lan_client as lan_client_module
from gui.mods.offline_lan_0922.lan_client import LANClient


class _Packer(object):
    def __init__(self, name, calls):
        self.name = name
        self.calls = calls

    def pack(self, value):
        self.calls.append((self.name, dict(value)))
        return [self.name, dict(value)]


class _Packers(object):
    def __init__(self):
        self.calls = []
        for name in ('AVATAR_FULL_RESULTS', 'VEH_FULL_RESULTS',
                     'COMMON_RESULTS', 'PLAYER_INFO', 'VEH_PUBLIC_RESULTS',
                     'AVATAR_PUBLIC_RESULTS'):
            setattr(self, name, _Packer(name, self.calls))


class _ReplayConnector(object):
    def __init__(self, unused_packer, values):
        self.values = values


class _Replay(object):
    def __init__(self, connector, recordName=None, startRecordName=None):
        self.connector = connector
        self.record_name = recordName
        self.start_name = startRecordName

    def pack(self):
        return ('SET:%s:%s' % (
            self.record_name, self.connector.values[self.start_name]
        )).encode('ascii')


class _InteractionDetails(object):
    instances = []

    def __init__(self, unique_ids, values):
        self.rows = {}
        self.initial = (list(unique_ids), list(values))
        self.__class__.instances.append(self)

    def __getitem__(self, unique_id):
        return self.rows.setdefault(unique_id, {})

    def pack(self):
        return b'exact-1513-interaction-details'


def _receipt(account_key='account-key-123456'):
    receipt = {
        'receipt_id': 'server:7:1', 'arena_unique_id': (7 << 32) | 1,
        'round_id': 7, 'player_id': 1, 'account_key': account_key,
        'player_name': 'Alice', 'vehicle': 'ussr:R11_MS-1',
        'team': 1, 'winner': 1, 'map': '01_karelia',
        'finish_reason': 1, 'death_reason': -1, 'duration': 120,
        'premature_leave': True,
        'stats': {
            'shots': 8, 'direct_hits': 6, 'piercings': 4,
            'damage': 900, 'damage_received': 300, 'damage_blocked': 100,
            'assist_track': 80, 'assist_radio': 40, 'assist_stun': 0,
            'kills': 2, 'spotted': 1, 'capture_points': 5,
            'dropped_capture_points': 0,
        },
        'rewards': {
            'credits': 4200, 'xp': 600, 'free_xp': 30,
            'repair_cost': 0, 'ammo_cost': 0,
        },
    }
    receipt['public_results'] = [{
        'actor_kind': 'player', 'actor_id': 1, 'name': 'Alice',
        'vehicle': 'ussr:R11_MS-1', 'team': 1, 'health': 100,
        'death_reason': -1, 'killer_kind': '', 'killer_id': 0,
        'is_team_killer': False, 'xp': 600,
        'stats': dict(receipt['stats']),
    }]
    return receipt


def _interaction(target_kind='bot', target_id=17, **updates):
    value = dict(
        (name, minimum if name == 'death_reason' else 0)
        for name, unused_native, minimum, unused_maximum in
        postbattle_store.INTERACTION_FIELDS)
    value.update({
        'target_kind': target_kind, 'target_id': target_id,
    })
    value.update(updates)
    return value


class PostBattleContractTests(unittest.TestCase):
    def test_vehicle_dossier_uses_native_builder_and_change_time_filter(self):
        built = []

        class Dossier(object):
            def __init__(self):
                self.blocks = {'a15x15': {}, 'a15x15_2': {}}
            def __getitem__(self, name):
                return self.blocks[name]
            def makeCompDescr(self):
                return dict((name, dict(value))
                            for name, value in self.blocks.items())

        def factory(compact):
            self.assertEqual('', compact)
            dossier = Dossier()
            built.append(dossier)
            return dossier

        progress = {'vehicles': {'ussr:R11_MS-1': {
            'xp': 600, 'battles': 2, 'wins': 1, 'losses': 0,
            'damage': 900, 'kills': 2, 'shots': 8, 'directHits': 6,
            'piercings': 4, 'spotted': 1, 'damageReceived': 300,
            'damageBlockedByArmor': 100, 'damageAssistedTrack': 80,
            'damageAssistedRadio': 40, 'damageAssistedStun': 0,
            'capturePoints': 5, 'droppedCapturePoints': 2,
            'survivedBattles': 1, 'changeTime': 7}}}
        version, rows = data.dossiers(
            1, 6, progress, dossier_factory=factory,
            vehicle_type_resolver=lambda unused: 50001)
        self.assertEqual(1, version)
        self.assertEqual(50001, rows[0][0])
        self.assertEqual(7, rows[0][1])
        self.assertEqual({
            'xp': 600, 'battlesCount': 2, 'wins': 1, 'losses': 0,
            'frags': 2, 'damageDealt': 900, 'shots': 8,
            'directHits': 6, 'spotted': 1, 'damageReceived': 300,
            'capturePoints': 5, 'droppedCapturePoints': 2,
            'survivedBattles': 1}, rows[0][2]['a15x15'])
        self.assertEqual({
            'piercings': 4, 'damageBlockedByArmor': 100,
            'damageAssistedTrack': 80, 'damageAssistedRadio': 40,
            'damageAssistedStun': 0}, rows[0][2]['a15x15_2'])
        self.assertEqual((1, []), data.dossiers(
            1, 7, progress, dossier_factory=factory,
            vehicle_type_resolver=lambda unused: 50001))

    def test_draw_is_not_a_loss_and_receipt_stats_accumulate(self):
        store = postbattle_store.PostBattleStore(path=None)
        receipt = _receipt(store.account_key)
        receipt['winner'] = 0
        receipt['premature_leave'] = False
        self.assertTrue(store.accept(receipt))
        progress = store.progress()
        self.assertEqual(0, progress['wins'])
        self.assertEqual(0, progress['losses'])
        vehicle = progress['vehicles'][receipt['vehicle']]
        self.assertEqual(0, vehicle['wins'])
        self.assertEqual(0, vehicle['losses'])
        self.assertEqual(receipt['stats']['shots'], vehicle['shots'])
        self.assertEqual(receipt['stats']['piercings'], vehicle['piercings'])
        self.assertEqual(1, vehicle['survivedBattles'])

    def test_accelerated_training_diverts_vehicle_xp_once(self):
        store = postbattle_store.PostBattleStore(path=None)
        calls = []
        store.set_progress_applier(
            lambda receipt: (calls.append(receipt['receipt_id']) or
                             {'accelerated': True}))
        receipt = _receipt(store.account_key)

        self.assertTrue(store.accept(receipt))
        self.assertFalse(store.accept(receipt))

        self.assertEqual([receipt['receipt_id']], calls)
        self.assertEqual(
            0, store.progress()['vehicles'][receipt['vehicle']]['xp'])
        self.assertEqual(receipt['rewards']['credits'],
                         store.progress()['credits'])

    def test_ordinary_training_keeps_vehicle_xp(self):
        store = postbattle_store.PostBattleStore(path=None)
        store.set_progress_applier(lambda unused: {'accelerated': False})
        receipt = _receipt(store.account_key)

        self.assertTrue(store.accept(receipt))

        self.assertEqual(
            receipt['rewards']['xp'],
            store.progress()['vehicles'][receipt['vehicle']]['xp'])

    def test_premature_disconnect_does_not_count_as_survived(self):
        store = postbattle_store.PostBattleStore(path=None)
        receipt = _receipt(store.account_key)
        receipt['death_reason'] = -1
        receipt['premature_leave'] = True
        self.assertTrue(store.accept(receipt))
        vehicle = store.progress()['vehicles'][receipt['vehicle']]
        self.assertEqual(0, vehicle['survivedBattles'])

    def test_only_a_battle_watched_to_the_end_opens_results_immediately(self):
        watched = postbattle_store.PostBattleStore(path=None)
        watched_receipt = _receipt(watched.account_key)
        watched_receipt['premature_leave'] = False
        self.assertTrue(watched.accept(watched_receipt))
        self.assertTrue(watched.should_show_immediately(
            watched_receipt['arena_unique_id']))
        self.assertTrue(watched.acknowledge(
            watched_receipt['arena_unique_id']))
        self.assertTrue(watched.should_show_immediately(
            watched_receipt['arena_unique_id']))

        departed = postbattle_store.PostBattleStore(path=None)
        departed_receipt = _receipt(departed.account_key)
        departed_receipt['premature_leave'] = True
        self.assertTrue(departed.accept(departed_receipt))
        self.assertFalse(departed.should_show_immediately(
            departed_receipt['arena_unique_id']))
        self.assertFalse(departed.should_show_immediately(-1))

    def test_store_survives_restart_applies_once_and_clears_only_on_ack(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'postbattle_state.json')
            store = postbattle_store.PostBattleStore(path=path)
            receipt = _receipt(store.account_key)
            self.assertTrue(store.accept(receipt))
            self.assertFalse(store.accept(receipt))
            self.assertEqual(1, store.progress()['battles'])
            restarted = postbattle_store.PostBattleStore(path=path)
            self.assertEqual([receipt['arena_unique_id']],
                             restarted.pending_arenas())
            self.assertEqual(1, restarted.progress()['battles'])
            self.assertTrue(restarted.acknowledge(receipt['arena_unique_id']))
            self.assertEqual([], restarted.pending_arenas())
            final = postbattle_store.PostBattleStore(path=path)
            self.assertEqual(1, final.progress()['battles'])
            self.assertEqual(receipt['arena_unique_id'],
                             final.latest_archived_arena())
            original_vehicle = postbattle_store._vehicle_type_compact_descr
            original_arena = postbattle_store._arena_type_id
            try:
                postbattle_store._vehicle_type_compact_descr = (
                    lambda unused: 50001)
                postbattle_store._arena_type_id = lambda unused: 70001
                self.assertIsNotNone(final.result(
                    receipt['arena_unique_id'], packers=_Packers(),
                    replay_types=(_Replay, _ReplayConnector)))
                service_data = final.service_message_data(
                    receipt['arena_unique_id'])
                self.assertEqual({
                    'arenaTypeID', 'arenaCreateTime', 'playerVehicles',
                    'xp', 'credits', 'crystal', 'creditsToDraw',
                    'isWinner', 'team', 'winnerIfDraw', 'guiType',
                    'arenaUniqueID',
                }, set(service_data))
                self.assertEqual(receipt['arena_unique_id'],
                                 service_data['arenaUniqueID'])
                self.assertEqual({50001: {}},
                                 service_data['playerVehicles'])
            finally:
                postbattle_store._vehicle_type_compact_descr = original_vehicle
                postbattle_store._arena_type_id = original_arena
            self.assertTrue(final.acknowledge(receipt['arena_unique_id']))
            self.assertFalse(final.accept(receipt))

    def test_client_store_reloads_nonempty_interaction_details(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'postbattle_state.json')
            store = postbattle_store.PostBattleStore(path=path)
            receipt = _receipt(store.account_key)
            enemy_stats = dict((name, 0) for name in receipt['stats'])
            receipt['public_results'].append({
                'actor_kind': 'bot', 'actor_id': 17, 'name': 'Atlas-17',
                'vehicle': 'germany:G04_PzVI_Tiger_I', 'team': 2,
                'health': 100, 'death_reason': -1,
                'killer_kind': '', 'killer_id': 0,
                'is_team_killer': False, 'xp': 0, 'stats': enemy_stats,
            })
            receipt['interactions'] = [_interaction(
                spotted=1, direct_hits=2, piercings=1, damage=240)]

            self.assertTrue(store.accept(receipt))
            restarted = postbattle_store.PostBattleStore(path=path)
            persisted = restarted._pending[str(receipt['arena_unique_id'])]
            self.assertEqual(receipt['interactions'],
                             persisted['interactions'])

    def test_native_compact_contract_uses_all_six_stock_packers(self):
        packers = _Packers()
        receipt = _receipt()
        receipt['player_name'] = '玩家'
        receipt['public_results'][0]['name'] = '玩家'
        original_vehicle = postbattle_store._vehicle_type_compact_descr
        original_arena = postbattle_store._arena_type_id
        try:
            postbattle_store._vehicle_type_compact_descr = lambda unused: 50001
            postbattle_store._arena_type_id = lambda unused: 70001
            compact = postbattle_store.pack_battle_result(
                receipt, packers=packers,
                replay_types=(_Replay, _ReplayConnector))
        finally:
            postbattle_store._vehicle_type_compact_descr = original_vehicle
            postbattle_store._arena_type_id = original_arena
        self.assertEqual(4, len(compact))
        self.assertEqual(_receipt()['arena_unique_id'], compact[0])
        self.assertEqual([
            'AVATAR_FULL_RESULTS', 'VEH_FULL_RESULTS', 'PLAYER_INFO',
            'VEH_PUBLIC_RESULTS', 'AVATAR_PUBLIC_RESULTS', 'COMMON_RESULTS'],
            [name for name, unused in packers.calls])
        self.assertEqual('玩家'.encode('utf-8'),
                         packers.calls[2][1]['name'])
        avatar = pickle.loads(zlib.decompress(compact[1]))
        vehicles = pickle.loads(zlib.decompress(compact[2]))
        public = pickle.loads(zlib.decompress(compact[3]))
        self.assertEqual('AVATAR_FULL_RESULTS', avatar[0])
        self.assertEqual('VEH_FULL_RESULTS', vehicles[50001][0])
        self.assertEqual(4, len(public))
        avatar_fields = packers.calls[0][1]
        self.assertEqual(0, avatar_fields['avatarDamageDealt'])
        self.assertEqual(0, avatar_fields['avatarKills'])
        vehicle_fields = packers.calls[1][1]
        self.assertEqual(receipt['stats']['damage'],
                         vehicle_fields['damageDealt'])
        self.assertEqual(receipt['stats']['kills'], vehicle_fields['kills'])
        self.assertEqual(600, vehicle_fields['xp'])
        self.assertEqual(30, vehicle_fields['freeXP'])
        self.assertEqual(0, vehicle_fields['autoRepairCost'])
        self.assertEqual((0, 0), vehicle_fields['autoLoadCost'])
        self.assertEqual((0, 0, 0), vehicle_fields['autoEquipCost'])
        self.assertEqual(-1, vehicle_fields['deathReason'])
        for replay_name in ('creditsReplay', 'xpReplay', 'freeXPReplay',
                            'goldReplay', 'crystalReplay'):
            self.assertTrue(vehicle_fields[replay_name])

    def test_native_vehicle_details_keep_each_enemy_interaction(self):
        receipt = _receipt()
        enemy_stats = dict((name, 0) for name in receipt['stats'])
        receipt['public_results'].append({
            'actor_kind': 'bot', 'actor_id': 17, 'name': 'Atlas-17',
            'vehicle': 'germany:G04_PzVI_Tiger_I', 'team': 2,
            'health': 0, 'death_reason': 0,
            'killer_kind': 'player', 'killer_id': 1,
            'is_team_killer': False, 'xp': 220, 'stats': enemy_stats,
        })
        receipt['interactions'] = [_interaction(
            spotted=1, death_reason=0, direct_hits=4,
            piercings=3, damage=900, assist_track=120,
            target_kills=1)]
        compact_ids = {
            'ussr:R11_MS-1': 50001,
            'germany:G04_PzVI_Tiger_I': 60002,
        }
        packers = _Packers()
        _InteractionDetails.instances[:] = []
        original_vehicle = postbattle_store._vehicle_type_compact_descr
        original_arena = postbattle_store._arena_type_id
        try:
            postbattle_store._vehicle_type_compact_descr = compact_ids.get
            postbattle_store._arena_type_id = lambda unused: 70001
            postbattle_store.pack_battle_result(
                receipt, packers=packers,
                replay_types=(_Replay, _ReplayConnector),
                interaction_details_type=_InteractionDetails)
        finally:
            postbattle_store._vehicle_type_compact_descr = original_vehicle
            postbattle_store._arena_type_id = original_arena

        self.assertEqual(1, len(_InteractionDetails.instances))
        details = _InteractionDetails.instances[0]
        self.assertEqual(([], []), details.initial)
        self.assertEqual({(2, 60002)}, set(details.rows))
        target = details.rows[2, 60002]
        self.assertEqual(4, target['directHits'])
        self.assertEqual(3, target['piercings'])
        self.assertEqual(900, target['damageDealt'])
        self.assertEqual(120, target['damageAssistedTrack'])
        self.assertEqual(1, target['targetKills'])
        vehicle = next(
            value for name, value in packers.calls
            if name == 'VEH_FULL_RESULTS')
        self.assertEqual(
            b'exact-1513-interaction-details', vehicle['details'])

    def test_registered_1500_stream_and_1501_ack(self):
        class Store(object):
            def __init__(self):
                self.acked = []
            def result(self, arena):
                return ('packed', arena)
            def acknowledge(self, arena):
                self.acked.append(arena)
                return True
        store = Store()
        result = requests.dispatch(
            commands.CMD_REQ_BATTLE_RESULTS,
            {'postbattle_store': store}, (123, 0, 0))
        self.assertEqual(commands.RES_STREAM, result.result_id)
        self.assertEqual(('packed', 123), result.stream)
        ack = requests.dispatch(
            commands.CMD_BATTLE_RESULTS_RECEIVED,
            {'postbattle_store': store}, (123, 0, 0))
        self.assertEqual(commands.RES_SUCCESS, ack.result_id)
        self.assertEqual([123], store.acked)

    def test_wire_receipt_requires_complete_consistent_public_roster(self):
        receipt = _receipt()
        receipt.update({'type': 'battle_receipt', 'protocol': 5})
        self.assertTrue(lan_client_module._valid_battle_receipt(receipt))

        large_arena = json.loads(json.dumps(receipt))
        large_arena['arena_unique_id'] = (
            (0x2a2a2a2a << 32) | 0x12345678)
        self.assertGreater(large_arena['arena_unique_id'], 1 << 53)
        self.assertTrue(lan_client_module._valid_battle_receipt(large_arena))

        missing_personal = json.loads(json.dumps(receipt))
        missing_personal['public_results'][0]['actor_id'] = 2
        self.assertFalse(
            lan_client_module._valid_battle_receipt(missing_personal))
        inconsistent = json.loads(json.dumps(receipt))
        inconsistent['public_results'][0]['stats']['damage'] += 1
        self.assertFalse(lan_client_module._valid_battle_receipt(inconsistent))

        detailed = json.loads(json.dumps(receipt))
        enemy_stats = dict((name, 0) for name in receipt['stats'])
        detailed['public_results'].append({
            'actor_kind': 'bot', 'actor_id': 17, 'name': 'Atlas-17',
            'vehicle': 'germany:G04_PzVI_Tiger_I', 'team': 2,
            'health': 100, 'death_reason': -1,
            'killer_kind': '', 'killer_id': 0,
            'is_team_killer': False, 'xp': 0, 'stats': enemy_stats,
        })
        detailed['interactions'] = [_interaction(
            direct_hits=1, damage=240)]
        self.assertTrue(lan_client_module._valid_battle_receipt(detailed))
        detailed['interactions'][0]['damage'] = 65536
        self.assertFalse(lan_client_module._valid_battle_receipt(detailed))

    def test_native_public_payload_contains_both_humans_and_bots(self):
        receipt = _receipt()
        enemy_stats = dict((name, 0) for name in receipt['stats'])
        enemy_stats.update({'shots': 3, 'direct_hits': 2, 'piercings': 1,
                            'damage': 250, 'damage_received': 900,
                            'kills': 0})
        bot_stats = dict((name, 0) for name in receipt['stats'])
        bot_stats.update({'shots': 5, 'direct_hits': 4, 'piercings': 3,
                          'damage': 900, 'kills': 1})
        receipt['death_reason'] = 0
        receipt['public_results'][0].update({
            'health': 0, 'death_reason': 0,
            'killer_kind': 'bot', 'killer_id': 17,
        })
        receipt['public_results'].extend(({
            'actor_kind': 'player', 'actor_id': 2, 'name': 'Bob',
            'vehicle': 'germany:G04_PzVI_Tiger_I', 'team': 2,
            'health': 100, 'death_reason': -1,
            'killer_kind': '', 'killer_id': 0,
            'is_team_killer': False, 'xp': 220, 'stats': enemy_stats,
        }, {
            'actor_kind': 'bot', 'actor_id': 17, 'name': 'Atlas-17',
            'vehicle': 'germany:G04_PzVI_Tiger_I', 'team': 2,
            'health': 300, 'death_reason': -1,
            'killer_kind': '', 'killer_id': 0,
            'is_team_killer': False, 'xp': 510, 'stats': bot_stats,
        }))
        compact_ids = {
            'ussr:R11_MS-1': 50001,
            'germany:G04_PzVI_Tiger_I': 60002,
        }
        packers = _Packers()
        original_vehicle = postbattle_store._vehicle_type_compact_descr
        original_arena = postbattle_store._arena_type_id
        try:
            postbattle_store._vehicle_type_compact_descr = compact_ids.get
            postbattle_store._arena_type_id = lambda unused: 70001
            compact = postbattle_store.pack_battle_result(
                receipt, packers=packers,
                replay_types=(_Replay, _ReplayConnector))
        finally:
            postbattle_store._vehicle_type_compact_descr = original_vehicle
            postbattle_store._arena_type_id = original_arena

        public = pickle.loads(zlib.decompress(compact[3]))
        self.assertEqual({1, 2, 3}, set(public[1]))
        self.assertEqual({1, 2, 3}, set(public[2]))
        self.assertEqual({1, 2, 3}, set(public[3]))
        player_calls = [value for name, value in packers.calls
                        if name == 'PLAYER_INFO']
        vehicle_calls = [value for name, value in packers.calls
                         if name == 'VEH_PUBLIC_RESULTS']
        avatar_calls = [value for name, value in packers.calls
                        if name == 'AVATAR_PUBLIC_RESULTS']
        common = [value for name, value in packers.calls
                  if name == 'COMMON_RESULTS'][0]
        self.assertEqual(
            [b'Alice', b'Bob', b'Atlas-17'],
            [value['name'] for value in player_calls])
        self.assertEqual([900, 250, 900],
                         [value['damageDealt'] for value in vehicle_calls])
        self.assertEqual([2, 0, 1],
                         [value['kills'] for value in vehicle_calls])
        self.assertEqual(
            [(0, 0), (0, 0), (0, 0)],
            [(value['avatarDamageDealt'], value['avatarKills'])
             for value in avatar_calls])
        self.assertEqual(3, vehicle_calls[0]['killerID'])
        self.assertEqual((60002, b'Atlas-17'), common['bots'][3])



if __name__ == '__main__':
    unittest.main()
