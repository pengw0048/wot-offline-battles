import base64
import json
import pickle
import sys
import tempfile
from pathlib import Path
import unittest

import bot_state_rows
from unittest import mock
import zlib


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'res' / 'scripts' / 'client'))
sys.path.insert(0, str(ROOT / 'server'))

from gui.mods.offline_lan_0922.account_rpc import commands, data, requests
from gui.mods.offline_lan_0922.account_rpc import postbattle_store
from lan_battle_server import (
    BattleState, CLIENT_BUILD_0922, DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
    EFFECTIVE_PARAMS_CAPABILITY,
    HUMAN_RAM_TIMELINE_CAPABILITY, MAX_RESULT_RECEIPTS,
    PLAYER_ENVIRONMENT_CAPABILITY, PLAYER_FIRE_INTENT_CAPABILITY,
    PROJECTILE_CAPABILITY,
    RAM_CONTACT_LEDGER_CAPABILITY, RICOCHET_CONTINUATION_CAPABILITY, Player)
from effective_params_fixture import effective_params
import lan_battle_server as lan_server_module
from offline_rewards import compute_offline_rewards
from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime
from gui.mods.offline_lan_0922 import lan_client as lan_client_module
from gui.mods.offline_lan_0922.lan_client import LANClient


class _Socket(object):
    def __init__(self):
        self.payloads = []

    def sendall(self, unused_payload):
        self.payloads.append(unused_payload)


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


def _account_receipts(state, account_key):
    return state._result_receipts_for_account(account_key)


def _latest_receipt(state, account_key):
    return _account_receipts(state, account_key)[-1]


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

    def test_offline_reward_is_monotone_and_has_documented_boundaries(self):
        base = compute_offline_rewards({}, False, True)
        damage = compute_offline_rewards({'damage_dealt': 1000}, False, True)
        win = compute_offline_rewards({'damage_dealt': 1000}, True, True)
        self.assertGreater(damage['xp'], base['xp'])
        self.assertGreater(damage['credits'], base['credits'])
        self.assertEqual(damage['xp'] * 3 // 2, win['xp'])
        self.assertEqual(win['xp'] * 5 // 100, win['free_xp'])
        self.assertEqual(0, win['repair_cost'])
        self.assertEqual(0, win['ammo_cost'])

        kills = compute_offline_rewards({'kills': 3}, False, True)
        self.assertGreater(kills['xp'], base['xp'])
        self.assertEqual(base['credits'], kills['credits'])
        tier_three_loss = compute_offline_rewards({}, False, True, 3)
        tier_three_win = compute_offline_rewards({}, True, True, 3)
        self.assertEqual(3000, tier_three_loss['credits'])
        self.assertEqual(3000 * 185 // 100,
                         tier_three_win['credits'])

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
            # An acknowledged result stays re-openable for the rest of this
            # session: #1513 confirms with 1501 as soon as it has cached the
            # result, and the notification list can request 1500 again.
            self.assertEqual(receipt['arena_unique_id'],
                             restarted.latest_archived_arena())
            original_vehicle = postbattle_store._vehicle_type_compact_descr
            original_arena = postbattle_store._arena_type_id
            try:
                postbattle_store._vehicle_type_compact_descr = (
                    lambda unused: 50001)
                postbattle_store._arena_type_id = lambda unused: 70001
                self.assertIsNotNone(restarted.result(
                    receipt['arena_unique_id'], packers=_Packers(),
                    replay_types=(_Replay, _ReplayConnector)))
                service_data = restarted.service_message_data(
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
            self.assertTrue(restarted.acknowledge(receipt['arena_unique_id']))
            self.assertFalse(restarted.accept(receipt))

    def test_an_acknowledged_result_is_not_replayed_after_a_restart(self):
        """Only an unacknowledged receipt is persisted in full.

        Re-opening an old result after a restart is deliberately not
        supported, so an archived row keeps its identity and drops its body.
        Applying it twice would double the credits, so the identity has to be
        durable, and the progress it produced has to survive too.
        """
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'postbattle_state.json')
            store = postbattle_store.PostBattleStore(path=path)
            receipt = _receipt(store.account_key)
            self.assertTrue(store.accept(receipt))
            self.assertTrue(store.acknowledge(receipt['arena_unique_id']))
            settled = store.progress()

            restarted = postbattle_store.PostBattleStore(path=path)

            self.assertEqual(settled, restarted.progress())
            self.assertEqual(1, restarted.progress()['battles'])
            self.assertEqual(4200, restarted.progress()['credits'])
            # The retried delivery an unlucky ACK produces is still applied
            # exactly once, and the client still acknowledges it.
            self.assertFalse(restarted.accept(receipt))
            self.assertEqual(settled, restarted.progress())
            # The body is gone, so nothing claims to be replayable.
            self.assertIsNone(restarted.latest_archived_arena())
            self.assertIsNone(restarted.result(receipt['arena_unique_id']))
            self.assertIsNone(restarted.service_message_data(
                receipt['arena_unique_id']))
            self.assertFalse(restarted.should_show_immediately(
                receipt['arena_unique_id']))

    def test_the_terminal_write_does_not_carry_the_archived_bodies(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'postbattle_state.json')
            store = postbattle_store.PostBattleStore(path=path)
            for index in range(4):
                row = _receipt(store.account_key)
                row['receipt_id'] = 'server:%d:1' % (200 + index)
                row['arena_unique_id'] = ((200 + index) << 32) | 1
                self.assertTrue(store.accept(row))
                self.assertTrue(store.acknowledge(row['arena_unique_id']))
            live = _receipt(store.account_key)
            live['receipt_id'] = 'server:300:1'
            live['arena_unique_id'] = (300 << 32) | 1
            self.assertTrue(store.accept(live))

            saved = json.loads(Path(path).read_text(encoding='utf-8'))

            self.assertEqual(
                [{'receipt_id': 'server:%d:1' % (200 + index),
                  'arena_unique_id': ((200 + index) << 32) | 1}
                 for index in range(4)],
                saved['history'])
            # The unacknowledged receipt is the only body on disk, because
            # losing it would lose the settlement itself.
            self.assertEqual(1, len(saved['pending']))
            self.assertEqual('server:300:1', saved['pending'][0]['receipt_id'])
            self.assertIn('public_results', saved['pending'][0])

    def test_an_older_full_bodied_archive_still_loads(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'postbattle_state.json')
            store = postbattle_store.PostBattleStore(path=path)
            receipt = postbattle_store._receipt(_receipt(store.account_key))
            legacy = {
                'schema': postbattle_store.SCHEMA,
                'accountKey': store.account_key,
                'pending': [],
                'history': [receipt],
                'progress': postbattle_store.PostBattleStore._empty_progress(),
            }
            Path(path).write_text(json.dumps(legacy), encoding='utf-8')

            reloaded = postbattle_store.PostBattleStore(path=path)

            self.assertEqual(receipt['arena_unique_id'],
                             reloaded.latest_archived_arena())
            self.assertFalse(reloaded.accept(_receipt(store.account_key)))

    def test_terminal_receipt_does_not_re_copy_the_archived_history(self):
        """The victory instant must not cost time per archived receipt.

        The server ships settlement inside the terminal round barrier, so
        ``accept`` runs in a BigWorld callback the moment the last enemy
        dies.  Neither ``accept`` nor ``acknowledge`` edits an archived or
        pending receipt, so the rollback state must reference those rows
        rather than rebuild them.
        """
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'postbattle_state.json')
            store = postbattle_store.PostBattleStore(path=path)
            archived = []
            for index in range(8):
                row = _receipt(store.account_key)
                row['receipt_id'] = 'server:%d:1' % (100 + index)
                row['arena_unique_id'] = ((100 + index) << 32) | 1
                archived.append(postbattle_store._receipt(row))
            store._history = list(archived)
            pending = postbattle_store._receipt(_receipt(store.account_key))
            store._pending[str(pending['arena_unique_id'])] = pending

            rollback = store._snapshot()

            self.assertEqual(len(archived), len(rollback['history']))
            for index, row in enumerate(archived):
                self.assertIs(row, rollback['history'][index])
            self.assertIs(
                pending,
                rollback['pending'][str(pending['arena_unique_id'])])
            # The counters ``_apply_progress`` edits in place still need a
            # real copy, including the nested per-vehicle row.
            store._progress['vehicles']['ussr:R11_MS-1'] = {'battles': 3}
            rollback = store._snapshot()
            store._progress['vehicles']['ussr:R11_MS-1']['battles'] = 9
            self.assertEqual(
                3, rollback['progress']['vehicles']['ussr:R11_MS-1'][
                    'battles'])

    def test_a_failed_durable_write_restores_every_container(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'postbattle_state.json')
            store = postbattle_store.PostBattleStore(path=path)
            first = _receipt(store.account_key)
            self.assertTrue(store.accept(first))
            self.assertTrue(store.acknowledge(first['arena_unique_id']))
            settled_progress = store.progress()
            settled_history = list(store._history)

            second = _receipt(store.account_key)
            second['receipt_id'] = 'server:8:1'
            second['arena_unique_id'] = (8 << 32) | 1
            with mock.patch.object(
                    postbattle_store.port_config, 'write_json',
                    side_effect=OSError('disk unavailable')):
                self.assertRaises(OSError, store.accept, second)
            self.assertEqual(settled_progress, store.progress())
            self.assertEqual([], store.pending_arenas())
            self.assertEqual(settled_history, store._history)

            # The same rollback must survive an acknowledge failure, which is
            # the only transaction that appends to the archived history.
            self.assertTrue(store.accept(second))
            with mock.patch.object(
                    postbattle_store.port_config, 'write_json',
                    side_effect=OSError('disk unavailable')):
                self.assertRaises(
                    OSError, store.acknowledge, second['arena_unique_id'])
            self.assertEqual([second['arena_unique_id']],
                             store.pending_arenas())
            self.assertEqual(settled_history, store._history)

    def test_a_saturated_history_still_rolls_back_its_dropped_head(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'postbattle_state.json')
            store = postbattle_store.PostBattleStore(path=path)
            saturated = []
            for index in range(postbattle_store.MAX_HISTORY):
                row = _receipt(store.account_key)
                row['receipt_id'] = 'server:%d:1' % (1000 + index)
                row['arena_unique_id'] = ((1000 + index) << 32) | 1
                saturated.append(postbattle_store._receipt(row))
            store._history = list(saturated)
            pending = _receipt(store.account_key)
            pending['receipt_id'] = 'server:9:1'
            pending['arena_unique_id'] = (9 << 32) | 1
            self.assertTrue(store.accept(pending))

            with mock.patch.object(
                    postbattle_store.port_config, 'write_json',
                    side_effect=OSError('disk unavailable')):
                self.assertRaises(
                    OSError, store.acknowledge, pending['arena_unique_id'])

            # The trim drops the oldest row; a rollback must put it back.
            self.assertEqual(len(saturated), len(store._history))
            self.assertIs(saturated[0], store._history[0])
            self.assertEqual([pending['arena_unique_id']],
                             store.pending_arenas())

    def test_the_durable_state_file_is_written_for_the_ports_own_reader(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'postbattle_state.json')
            store = postbattle_store.PostBattleStore(path=path)
            receipt = _receipt(store.account_key)
            self.assertTrue(store.accept(receipt))

            text = Path(path).read_text(encoding='utf-8')
            # The embedded 2.7 runtime only uses its C JSON encoder without
            # sort_keys and indent, so this cache must carry neither.
            self.assertEqual(1, text.count('\n'))
            self.assertNotIn(', ', text)
            self.assertNotIn(': ', text)
            reloaded = json.loads(text)
            self.assertEqual(postbattle_store.SCHEMA, reloaded['schema'])
            restarted = postbattle_store.PostBattleStore(path=path)
            self.assertEqual([receipt['arena_unique_id']],
                             restarted.pending_arenas())
            self.assertEqual(store.progress(), restarted.progress())

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

    def test_blocked_damage_reaches_the_native_result_and_dossier(self):
        state = BattleState(map_name='01_karelia', team_size=1)
        state.client_build = CLIENT_BUILD_0922
        state.phase = 'battle'
        first = Player(1, _Socket(), ('127.0.0.1', 1), name='Alice',
                       vehicle='ussr:R11_MS-1', team=1,
                       account_key='a' * 32)
        second = Player(2, _Socket(), ('127.0.0.1', 2), name='Bob',
                        vehicle='germany:G04_PzVI_Tiger_I', team=2,
                        account_key='b' * 32)
        state.players = {1: first, 2: second}
        state._freeze_round_participants((first, second))
        # One enemy shot that the armour stopped, as the projectile ledger
        # records it on the vehicle that bounced it.
        state._statistics_row('player', 2)['damage_blocked'] = 420

        self.assertTrue(state._finish_battle(1, 'team_eliminated'))

        receipt = _latest_receipt(state, second.account_key)
        self.assertEqual(420, receipt['stats']['damage_blocked'])
        rows = dict(((row['actor_kind'], row['actor_id']), row)
                    for row in receipt['public_results'])
        self.assertEqual(
            420, rows['player', 2]['stats']['damage_blocked'])

        packers = _Packers()
        original_vehicle = postbattle_store._vehicle_type_compact_descr
        original_arena = postbattle_store._arena_type_id
        try:
            postbattle_store._vehicle_type_compact_descr = lambda unused: 50001
            postbattle_store._arena_type_id = lambda unused: 70001
            postbattle_store.pack_battle_result(
                receipt, packers=packers,
                replay_types=(_Replay, _ReplayConnector))
        finally:
            postbattle_store._vehicle_type_compact_descr = original_vehicle
            postbattle_store._arena_type_id = original_arena
        vehicle_fields = dict(packers.calls)['VEH_FULL_RESULTS']
        self.assertEqual(420, vehicle_fields['damageBlockedByArmor'])

        store = postbattle_store.PostBattleStore(path=None)
        receipt['account_key'] = store.account_key
        self.assertTrue(store.accept(receipt))
        self.assertEqual(
            420, store.progress()['vehicles'][
                receipt['vehicle']]['damageBlockedByArmor'])

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
        self.assertEqual((60002, b'Atlas-17'), bot_state_rows.bots(common)[3])

    def test_server_receipt_survives_graceful_early_leave_and_is_idempotent(self):
        state = BattleState(map_name='01_karelia')
        state.client_build = CLIENT_BUILD_0922
        state.phase = 'battle'
        state.round_id = 7
        player = Player(1, _Socket(), ('127.0.0.1', 1), name='Alice',
                        vehicle='ussr:R11_MS-1', team=1, account_key='a' * 32)
        player.participating = False
        state.players[1] = player
        state._statistics_row('player', 1)['damage_dealt'] = 900
        self.assertTrue(state._finish_battle(1, 'elimination'))
        receipt = _latest_receipt(state, player.account_key)
        self.assertTrue(receipt['premature_leave'])
        self.assertEqual(state.round_start_time,
                         receipt['arena_unique_id'] & 0xffffffff)
        self.assertFalse(state._finish_battle(1, 'duplicate'))
        self.assertEqual(receipt, _latest_receipt(state, player.account_key))

    def test_server_receipt_reuses_complete_round_roster_and_statistics(self):
        state = BattleState(map_name='01_karelia', team_size=2)
        state.client_build = CLIENT_BUILD_0922
        state.phase = 'battle'
        first = Player(1, _Socket(), ('127.0.0.1', 1), name='Alice',
                       vehicle='ussr:R11_MS-1', team=1,
                       account_key='a' * 32)
        second = Player(2, _Socket(), ('127.0.0.1', 2), name='Bob',
                        vehicle='germany:G04_PzVI_Tiger_I', team=2,
                        account_key='b' * 32)
        state.players = {1: first, 2: second}
        state._freeze_round_participants((first, second))
        state.bot_manifest = [
            {'id': 2, 'team': 1, 'slot': 1, 'name': 'Atlas-12',
             'vehicle': 'ussr:R11_MS-1', 'health': 700,
             'max_health': 1000},
            {'id': 17, 'team': 2, 'slot': 1, 'name': 'Bison-17',
             'vehicle': 'germany:G04_PzVI_Tiger_I', 'health': 0,
             'max_health': 1000},
        ]
        state.bot_states = {
            2: dict(state.bot_manifest[0], alive=True, death_reason=0),
            17: dict(state.bot_manifest[1], alive=False, death_reason=0,
                     death_attacker_kind='player', death_attacker_id=1),
        }
        state._statistics_row('player', 1).update({
            'shots_fired': 8, 'shots_hit': 6, 'shots_penetrated': 4,
            'damage_dealt': 900, 'kills': 1})
        state._statistics_row('player', 2).update({
            'shots_fired': 4, 'shots_hit': 2, 'damage_dealt': 250})
        state._statistics_row('bot', 2).update({
            'shots_fired': 3, 'shots_hit': 1, 'damage_dealt': 120})
        state._statistics_row('bot', 17).update({
            'shots_fired': 5, 'shots_hit': 4, 'damage_dealt': 500})

        self.assertTrue(state._finish_battle(1, 'team_eliminated'))

        first_receipt = _latest_receipt(state, first.account_key)
        second_receipt = _latest_receipt(state, second.account_key)
        self.assertEqual(first_receipt['public_results'],
                         second_receipt['public_results'])
        rows = dict(((row['actor_kind'], row['actor_id']), row)
                    for row in first_receipt['public_results'])
        self.assertEqual({('player', 1), ('player', 2),
                          ('bot', 2), ('bot', 17)}, set(rows))
        self.assertEqual(900, rows['player', 1]['stats']['damage'])
        self.assertEqual(120, rows['bot', 2]['stats']['damage'])
        self.assertEqual('Bison-17', rows['bot', 17]['name'])
        self.assertEqual('germany:G04_PzVI_Tiger_I',
                         rows['bot', 17]['vehicle'])
        self.assertEqual(0, rows['bot', 17]['death_reason'])
        self.assertEqual(('player', 1), (
            rows['bot', 17]['killer_kind'],
            rows['bot', 17]['killer_id']))

    def test_unacked_server_receipts_recover_atomically_and_ack_per_account(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'server_receipts.json')
            state = BattleState(
                map_name='01_karelia', receipt_state_path=path)
            state.client_build = CLIENT_BUILD_0922
            state.phase = 'battle'
            first = Player(1, _Socket(), ('127.0.0.1', 1), name='Alice',
                           team=1, account_key='a' * 32)
            second = Player(2, _Socket(), ('127.0.0.1', 2), name='Bob',
                            team=2, account_key='b' * 32)
            state.players = {1: first, 2: second}
            state._freeze_round_participants((first, second))
            state._record_damage(
                ('player', 1), ('player', 2), 240, {})
            state._increment_interaction(
                ('player', 1), ('player', 2), 'direct_hits')
            state._increment_interaction(
                ('player', 1), ('player', 2), 'piercings')
            self.assertTrue(state._finish_battle(1, 'elimination'))
            first_id = _latest_receipt(
                state, first.account_key)['receipt_id']
            second_id = _latest_receipt(
                state, second.account_key)['receipt_id']

            restarted = BattleState(
                map_name='01_karelia', receipt_state_path=path)
            first_receipt = _latest_receipt(
                restarted, first.account_key)
            self.assertEqual(1, len(first_receipt['interactions']))
            self.assertEqual(240, first_receipt[
                'interactions'][0]['damage'])
            self.assertEqual(1, first_receipt[
                'interactions'][0]['direct_hits'])
            self.assertEqual(1, first_receipt[
                'interactions'][0]['piercings'])
            self.assertEqual(
                [first.account_key, second.account_key],
                [receipt['account_key']
                 for receipt in restarted.result_receipts.values()])
            first_rejoined = Player(
                10, _Socket(), ('127.0.0.1', 10),
                account_key=first.account_key)
            second_rejoined = Player(
                11, _Socket(), ('127.0.0.1', 11),
                account_key=second.account_key)
            restarted.players = {10: first_rejoined, 11: second_rejoined}
            self.assertFalse(restarted.acknowledge_result_receipt(
                10, {'receipt_id': second_id}))
            with mock.patch.object(
                    lan_server_module, '_write_json_atomic',
                    side_effect=OSError('disk unavailable')):
                self.assertFalse(restarted.acknowledge_result_receipt(
                    10, {'receipt_id': first_id}))
            self.assertEqual(1, len(_account_receipts(
                restarted, first.account_key)))
            self.assertTrue(restarted.acknowledge_result_receipt(
                10, {'receipt_id': first_id}))

            after_first_ack = BattleState(
                map_name='01_karelia', receipt_state_path=path)
            self.assertEqual(
                [second.account_key],
                [receipt['account_key']
                 for receipt in after_first_ack.result_receipts.values()])
            after_first_ack.players = {11: second_rejoined}
            self.assertTrue(after_first_ack.acknowledge_result_receipt(
                11, {'receipt_id': second_id}))
            fully_acked = BattleState(
                map_name='01_karelia', receipt_state_path=path)
            self.assertEqual([], list(fully_acked.result_receipts))

    def test_same_account_multi_arena_backlog_survives_restart_in_order(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'server_receipts.json')
            state = BattleState(
                map_name='01_karelia', receipt_state_path=path)
            account_key = 'a' * 32
            player = Player(1, _Socket(), ('127.0.0.1', 1), name='Alice',
                            team=1, account_key=account_key)
            state.players = {1: player}
            state.client_build = CLIENT_BUILD_0922
            state.phase = 'battle'
            self.assertTrue(state._finish_battle(1, 'elimination'))
            first_id = _latest_receipt(
                state, account_key)['receipt_id']
            state._reset_round()
            state.client_build = CLIENT_BUILD_0922
            state.phase = 'battle'
            state.round_start_time += 1
            self.assertTrue(state._finish_battle(2, 'elimination'))
            second_id = _latest_receipt(
                state, account_key)['receipt_id']

            restarted = BattleState(
                map_name='01_karelia', receipt_state_path=path)
            self.assertEqual(
                [first_id, second_id],
                [receipt['receipt_id'] for receipt in
                 _account_receipts(restarted, account_key)])
            rejoined = Player(
                9, _Socket(), ('127.0.0.1', 9), account_key=account_key)
            restarted.players = {9: rejoined}
            self.assertTrue(restarted._deliver_result_receipt(rejoined))
            self.assertEqual(first_id, json.loads(
                rejoined.conn.payloads[-1].decode('utf-8'))['receipt_id'])
            self.assertTrue(restarted.acknowledge_result_receipt(
                9, {'receipt_id': first_id}))
            self.assertTrue(restarted._deliver_result_receipt(rejoined))
            self.assertEqual(second_id, json.loads(
                rejoined.conn.payloads[-1].decode('utf-8'))['receipt_id'])

    def test_dead_player_and_native_finish_reason_reach_receipt(self):
        state = BattleState(map_name='01_karelia')
        state.client_build = CLIENT_BUILD_0922
        state.phase = 'battle'
        player = Player(1, _Socket(), ('127.0.0.1', 1), name='Alice',
                        team=1, account_key='a' * 32)
        player.alive = False
        player.death_reason = 2
        state.players[1] = player
        state._finish_battle(2, 'base captured', 1)
        receipt = _latest_receipt(state, player.account_key)
        self.assertEqual(2, receipt['finish_reason'])
        self.assertEqual(2, receipt['death_reason'])

    def test_disconnected_participant_keeps_receipt_for_waiting_reconnect(self):
        state = BattleState(map_name='01_karelia')
        state.client_build = CLIENT_BUILD_0922
        state.phase = 'battle'
        first = Player(1, _Socket(), ('127.0.0.1', 1), name='A', team=1,
                       account_key='a' * 32)
        second = Player(2, _Socket(), ('127.0.0.1', 2), name='B', team=2,
                        account_key='b' * 32)
        state.players = {1: first, 2: second}
        state._freeze_round_participants((first, second))
        state.remove_player(first.player_id)
        state._finish_battle(2, 'team_eliminated')
        original = _latest_receipt(state, first.account_key)
        self.assertTrue(original['premature_leave'])

        state._reset_round()
        rejoined, error = state.add_player(
            _Socket(), ('127.0.0.1', 3), {
                'client_build': CLIENT_BUILD_0922,
                'capabilities': [
                    PROJECTILE_CAPABILITY,
                    DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
                    RAM_CONTACT_LEDGER_CAPABILITY,
                    HUMAN_RAM_TIMELINE_CAPABILITY,
                    PLAYER_FIRE_INTENT_CAPABILITY,
                    PLAYER_ENVIRONMENT_CAPABILITY,
                    EFFECTIVE_PARAMS_CAPABILITY,
                    RICOCHET_CONTINUATION_CAPABILITY],
                'account_key': first.account_key,
                'name': 'A', 'vehicle': first.vehicle,
                'max_health': first.max_health, 'outfits': {},
                'vehicle_compact_descr': 'dGVzdA==',
                'effective_params': effective_params(),
            })
        self.assertIsNone(error)
        self.assertEqual(original,
                         _latest_receipt(state, rejoined.account_key))

    def test_alt_f4_during_a_round_still_settles_on_the_next_launch(self):
        """A player who kills the client mid-round is settled by the server.

        ``remove_player`` resolves an abandoned round from canonical bot
        state, and the receipt reaches disk before it can be broadcast.  The
        client is already gone, so nothing acknowledges it; a later server
        process must still hand it to the same account, and the client must
        apply it exactly once.
        """
        with tempfile.TemporaryDirectory() as folder:
            ledger = str(Path(folder) / 'unacked_battle_receipts.json')
            store_path = str(Path(folder) / 'postbattle_state.json')
            account_key = 'a' * 32

            state = BattleState(map_name='01_karelia', team_size=1,
                                receipt_state_path=ledger)
            state.client_build = CLIENT_BUILD_0922
            state.phase = 'battle'
            player = Player(1, _Socket(), ('127.0.0.1', 1), name='Alice',
                            vehicle='ussr:R11_MS-1', team=1,
                            account_key=account_key)
            player.max_health = 1000
            player.health = 1000
            state.players = {1: player}
            state._freeze_round_participants((player,))
            state.bot_manifest = [{
                'id': 1, 'team': 2, 'slot': 0, 'name': 'Bot-1',
                'vehicle': 'germany:G04_PzVI_Tiger_I',
                'health': 900, 'max_health': 1500}]
            state.bot_states = {1: dict(state.bot_manifest[0], alive=True)}

            # Alt+F4 drops the connection; the server owns the outcome.
            state.remove_player(player.player_id)

            self.assertIsNotNone(state.battle_result)
            minted = _latest_receipt(state, account_key)
            self.assertTrue(minted['premature_leave'])
            # The ledger is on disk before anything could have been sent.
            self.assertTrue(Path(ledger).is_file())

            # Next launch: a fresh server process recovers the ledger and the
            # welcome path hands the receipt to the reconnecting account.
            relaunched = BattleState(map_name='01_karelia', team_size=1,
                                     receipt_state_path=ledger)
            self.assertEqual(
                minted['receipt_id'],
                _latest_receipt(relaunched, account_key)['receipt_id'])
            socket = _Socket()
            rejoined = Player(1, socket, ('127.0.0.1', 9),
                              account_key=account_key)
            relaunched.players = {1: rejoined}
            self.assertTrue(relaunched._deliver_result_receipt(rejoined))
            delivered = json.loads(socket.payloads[-1].decode('utf-8'))
            self.assertEqual('battle_receipt', delivered['type'])
            self.assertEqual(minted['receipt_id'], delivered['receipt_id'])

            # The client settles it and acknowledges once.
            client = postbattle_store.PostBattleStore(path=store_path)
            client._account_key = account_key
            self.assertTrue(client.accept(delivered))
            self.assertEqual(1, client.progress()['battles'])
            self.assertEqual([minted['arena_unique_id']],
                             client.pending_arenas())
            # A premature leave never steals focus with the results window.
            self.assertFalse(client.should_show_immediately(
                minted['arena_unique_id']))
            self.assertTrue(relaunched.acknowledge_result_receipt(
                rejoined.player_id, {'receipt_id': minted['receipt_id']}))
            self.assertEqual([], _account_receipts(relaunched, account_key))
            # A retried delivery after that cannot double the settlement.
            self.assertFalse(client.accept(delivered))
            self.assertEqual(1, client.progress()['battles'])

    def test_only_ten_results_stay_reopenable_within_one_session(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'postbattle_state.json')
            store = postbattle_store.PostBattleStore(path=path)
            arenas = []
            for index in range(postbattle_store.MAX_HISTORY + 3):
                row = _receipt(store.account_key)
                row['receipt_id'] = 'server:%d:1' % (400 + index)
                row['arena_unique_id'] = ((400 + index) << 32) | 1
                row['premature_leave'] = False
                arenas.append(row['arena_unique_id'])
                self.assertTrue(store.accept(row))
                self.assertTrue(store.acknowledge(row['arena_unique_id']))

            self.assertEqual(postbattle_store.MAX_HISTORY,
                             len(store._history))
            self.assertEqual(arenas[-1], store.latest_archived_arena())
            # ``should_show_immediately`` answers from the archived body, so
            # it is true only while the result can still be served.
            for arena in arenas[-postbattle_store.MAX_HISTORY:]:
                self.assertTrue(store.should_show_immediately(arena))
            for arena in arenas[:-postbattle_store.MAX_HISTORY]:
                self.assertFalse(store.should_show_immediately(arena))
            # Every battle still counted exactly once towards progress.
            self.assertEqual(len(arenas), store.progress()['battles'])

    def test_two_live_players_cannot_share_one_receipt_identity(self):
        state = BattleState(map_name='01_karelia')
        account_key = 'a' * 32
        hello = {
            'client_build': CLIENT_BUILD_0922,
            'capabilities': [
                PROJECTILE_CAPABILITY,
                DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
                RAM_CONTACT_LEDGER_CAPABILITY,
                HUMAN_RAM_TIMELINE_CAPABILITY,
                PLAYER_FIRE_INTENT_CAPABILITY,
                PLAYER_ENVIRONMENT_CAPABILITY,
                EFFECTIVE_PARAMS_CAPABILITY,
                RICOCHET_CONTINUATION_CAPABILITY],
            'account_key': account_key,
            'name': 'A', 'vehicle': 'ussr:R11_MS-1',
            'max_health': 100, 'outfits': {},
            'vehicle_compact_descr': 'dGVzdA==',
            'effective_params': effective_params(),
        }
        first, error = state.add_player(
            _Socket(), ('127.0.0.1', 1), hello)
        self.assertIsNotNone(first)
        self.assertIsNone(error)

        second, error = state.add_player(
            _Socket(), ('127.0.0.1', 2), dict(hello, name='B'))
        self.assertIsNone(second)
        self.assertEqual('duplicate_account_key', error)

        state.remove_player(first.player_id)
        rejoined, error = state.add_player(
            _Socket(), ('127.0.0.1', 3), dict(hello, name='B'))
        self.assertIsNotNone(rejoined)
        self.assertIsNone(error)

    def test_arena_id_is_shared_by_round_time_based_and_unique_next_round(self):
        state = BattleState(map_name='01_karelia')
        state.client_build = CLIENT_BUILD_0922
        state.phase = 'battle'
        first = Player(1, _Socket(), ('127.0.0.1', 1), name='A', team=1,
                       account_key='a' * 32)
        second = Player(2, _Socket(), ('127.0.0.1', 2), name='B', team=2,
                        account_key='b' * 32)
        state.players = {1: first, 2: second}
        state._finish_battle(1, 'elimination')
        first_arena = _latest_receipt(state, first.account_key)[
            'arena_unique_id']
        self.assertEqual(first_arena, _latest_receipt(
            state, second.account_key)['arena_unique_id'])
        self.assertGreater(first_arena & 0xffffffff, 1500000000)
        state._reset_round()
        state.client_build = CLIENT_BUILD_0922
        state.phase = 'battle'
        state.round_start_time += 1
        state._finish_battle(2, 'elimination')
        next_arena = _latest_receipt(state, first.account_key)[
            'arena_unique_id']
        self.assertNotEqual(first_arena, next_arena)
        self.assertEqual(2, len(_account_receipts(
            state, first.account_key)))
        self.assertEqual(2, len(_account_receipts(
            state, second.account_key)))

    def test_two_humans_keep_distinct_season_outfits_and_bots_are_empty(self):
        state = BattleState(map_name='01_karelia')
        first_raw = b'first-winter-outfit'
        second_raw = b'second-winter-outfit'
        first = Player(1, _Socket(), ('127.0.0.1', 1), name='A',
                       account_key='a' * 32,
                       outfits={'2': base64.b64encode(first_raw).decode()})
        second = Player(2, _Socket(), ('127.0.0.1', 2), name='B',
                        account_key='b' * 32,
                        outfits={'2': base64.b64encode(second_raw).decode()})
        first_public = state._public_player(first)
        second_public = state._public_player(second)
        self.assertNotEqual(first_public['outfits'], second_public['outfits'])

        battle = BattleRuntime(object())
        battle._arena_outfit_season = lambda: 2
        self.assertEqual(first_raw,
                         battle._remote_outfit(first_public, 'player'))
        self.assertEqual(second_raw,
                         battle._remote_outfit(second_public, 'player'))
        self.assertEqual('', battle._remote_outfit(first_public, 'bot'))
        self.assertEqual('', battle._remote_outfit({}, 'player'))

        lean = state._public_player(first, include_outfits=False)
        self.assertNotIn('outfits', lean)
        client = LANClient('127.0.0.1', 28782, 'A', first.vehicle)
        client._remember_player_outfits((first_public, second_public))
        inherited = client._remember_player_outfits((lean,))[0]
        self.assertEqual(first_public['outfits'], inherited['outfits'])

        state.client_build = CLIENT_BUILD_0922
        state.phase = 'battle'
        state.players = {first.player_id: first}
        first.conn.payloads[:] = []
        state.tick_once(1.0 / 30.0)
        snapshot = json.loads(first.conn.payloads[-1].decode('utf-8'))
        self.assertEqual('snapshot', snapshot['type'])
        self.assertNotIn('outfits', snapshot['players'][0])

    def test_result_receipt_is_sent_once_per_connection_and_on_reconnect(self):
        state = BattleState(map_name='01_karelia')
        account_key = 'a' * 32
        receipt = _receipt(account_key)
        receipt['type'] = 'battle_receipt'
        receipt['protocol'] = 5
        state.result_receipts[receipt['receipt_id']] = receipt
        second_receipt = json.loads(json.dumps(receipt))
        second_receipt['receipt_id'] = 'server:8:1'
        second_receipt['round_id'] = 8
        second_receipt['arena_unique_id'] += 1
        state.result_receipts[second_receipt['receipt_id']] = second_receipt

        socket_one = _Socket()
        player_one = Player(1, socket_one, ('127.0.0.1', 1),
                            account_key=account_key)
        state.players[player_one.player_id] = player_one
        self.assertTrue(state._deliver_result_receipt(player_one))
        self.assertTrue(state._deliver_result_receipt(player_one))
        self.assertEqual(1, len(socket_one.payloads))
        self.assertTrue(state.acknowledge_result_receipt(
            player_one.player_id, {'receipt_id': receipt['receipt_id']}))
        self.assertEqual(2, len(socket_one.payloads))
        self.assertEqual(second_receipt['receipt_id'], json.loads(
            socket_one.payloads[-1].decode('utf-8'))['receipt_id'])
        self.assertTrue(state._deliver_result_receipt(player_one))
        self.assertEqual(2, len(socket_one.payloads))
        self.assertEqual(second_receipt['receipt_id'], json.loads(
            socket_one.payloads[-1].decode('utf-8'))['receipt_id'])

        socket_two = _Socket()
        player_two = Player(2, socket_two, ('127.0.0.1', 2),
                            account_key=account_key)
        self.assertTrue(state._deliver_result_receipt(player_two))
        self.assertEqual(1, len(socket_two.payloads))

    def test_terminal_tick_queues_current_receipt_before_result_state(self):
        state = BattleState(map_name='01_karelia')
        state.client_build = CLIENT_BUILD_0922
        state.phase = 'battle'
        account_key = 'a' * 32
        transport = _Socket()
        player = Player(
            1, transport, ('127.0.0.1', 1), name='Alice',
            vehicle='ussr:R11_MS-1', team=1, account_key=account_key)
        state.players = {player.player_id: player}

        old_receipt = _receipt(account_key)
        old_receipt.update({
            'type': 'battle_receipt', 'protocol': 5,
            'receipt_id': 'server:0:1', 'round_id': 0,
        })
        state.result_receipts[old_receipt['receipt_id']] = old_receipt
        self.assertTrue(state._deliver_result_receipt(player))
        self.assertEqual(old_receipt['receipt_id'], json.loads(
            transport.payloads[-1].decode('utf-8'))['receipt_id'])
        transport.payloads[:] = []

        self.assertTrue(state._finish_battle(1, 'elimination'))
        current_receipt = _latest_receipt(state, account_key)
        state.tick_once(1.0 / 30.0)

        messages = [json.loads(payload.decode('utf-8'))
                    for payload in transport.payloads]
        self.assertEqual(
            ['battle_receipt', 'events', 'snapshot'],
            [message['type'] for message in messages[:3]])
        self.assertEqual(current_receipt['receipt_id'],
                         messages[0]['receipt_id'])
        self.assertEqual(state.round_id, messages[0]['round_id'])
        self.assertTrue(any(
            event['kind'] == 'battle_result'
            for event in messages[1]['events']))
        self.assertIsNotNone(messages[2]['battle_result'])

        reconnect = Player(
            2, _Socket(), ('127.0.0.1', 2), account_key=account_key)
        self.assertTrue(state._deliver_result_receipt(reconnect))
        self.assertEqual(old_receipt['receipt_id'], json.loads(
            reconnect.conn.payloads[-1].decode('utf-8'))['receipt_id'])

    def test_server_receipt_history_is_bounded_across_account_churn(self):
        state = BattleState(map_name='01_karelia')
        state.client_build = CLIENT_BUILD_0922
        state.phase = 'battle'
        for index in range(MAX_RESULT_RECEIPTS + 1):
            account_key = 'account-%03d' % index
            state.players = {index + 1: Player(
                index + 1, _Socket(), ('127.0.0.1', index + 1),
                account_key=account_key)}
            state.battle_result = None
            state._finish_battle(1, 'elimination')
        self.assertEqual(MAX_RESULT_RECEIPTS,
                         len(state.result_receipts))
        accounts = [receipt['account_key']
                    for receipt in state.result_receipts.values()]
        self.assertNotIn('account-000', accounts)
        self.assertIn('account-%03d' % MAX_RESULT_RECEIPTS, accounts)


if __name__ == '__main__':
    unittest.main()
