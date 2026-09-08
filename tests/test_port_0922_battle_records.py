"""Durable lifetime records use battle facts, not spendable garage balances."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_port_0922_postbattle import _receipt, postbattle_store


def receipt_for(store, round_id, start_time, winner=1, alive=True,
                vehicle='ussr:R11_MS-1', xp=600, **statistics):
    receipt = _receipt(store.account_key)
    receipt.update({
        'receipt_id': 'records:%d:1' % round_id,
        'arena_unique_id': (round_id << 32) | start_time,
        'round_id': round_id, 'vehicle': vehicle, 'winner': winner,
        'death_reason': -1 if alive else 0, 'premature_leave': False,
    })
    receipt['stats'].update(statistics)
    receipt['rewards']['xp'] = xp
    receipt['public_results'][0].update({
        'vehicle': vehicle, 'death_reason': receipt['death_reason'],
        'health': 100 if alive else 0, 'xp': xp,
        'stats': dict(receipt['stats']),
    })
    return receipt


class BattleRecordTests(unittest.TestCase):
    def test_vehicle_records_accumulate_and_keep_distinct_single_battle_bests(self):
        store = postbattle_store.PostBattleStore(path=None)
        first = receipt_for(store, 1, 100, xp=800, damage=900, kills=1,
                            hits_received=3, potential_damage_received=700,
                            crits_received=2)
        second = receipt_for(store, 2, 200, winner=2, alive=False, xp=500,
                             damage=1500, kills=3, hits_received=6,
                             potential_damage_received=1300,
                             crits_received=4)
        third = receipt_for(store, 3, 300, winner=0, xp=400, damage=300,
                            kills=0, hits_received=1,
                            potential_damage_received=200,
                            crits_received=1)
        for receipt in (second, third, first):
            self.assertTrue(store.accept(receipt))
        row = store.progress()['vehicles'][first['vehicle']]
        self.assertEqual(3, row['battles'])
        self.assertEqual((1, 1, 1),
                         (row['wins'], row['losses'], row['draws']))
        self.assertEqual(2, row['survivedBattles'])
        self.assertEqual(1, row['winAndSurvived'])
        self.assertEqual(1700, row['xp'])
        self.assertEqual(2700, row['damage'])
        self.assertEqual(4, row['kills'])
        self.assertEqual((800, 1500, 3),
                         (row['maxXP'], row['maxDamage'], row['maxFrags']))
        self.assertEqual(10, row['hitsReceived'])
        self.assertEqual(2200, row['potentialDamageReceived'])
        self.assertEqual(7, row['critsReceived'])
        self.assertEqual(100, row['creationTime'])
        self.assertEqual(300, row['lastBattleTime'])
        self.assertEqual(3, row['changeTime'])

    def test_premature_winning_departure_is_not_a_surviving_victory(self):
        store = postbattle_store.PostBattleStore(path=None)
        receipt = receipt_for(store, 1, 100)
        receipt['premature_leave'] = True
        store.accept(receipt)
        row = store.progress()['vehicles'][receipt['vehicle']]
        self.assertEqual(1, row['wins'])
        self.assertEqual(0, row['winAndSurvived'])
        self.assertEqual(0, row['survivedBattles'])

    def test_maximum_xp_and_notification_follow_the_awarded_transaction(self):
        store = postbattle_store.PostBattleStore(path=None)
        store.set_progress_applier(lambda unused: {
            'accelerated': True,
            'awarded': {'credits': 8400, 'xp': 1200, 'free_xp': 60},
        })
        receipt = receipt_for(store, 1, 100)
        store.accept(receipt)
        row = store.progress()['vehicles'][receipt['vehicle']]
        self.assertEqual(1200, row['maxXP'])
        self.assertEqual(1200, row['xp'])
        with mock.patch.object(postbattle_store,
                               '_vehicle_type_compact_descr', return_value=1), \
                mock.patch.object(postbattle_store, '_arena_type_id',
                                  return_value=1):
            message = store.service_message_data(receipt['arena_unique_id'])
        self.assertEqual(1200, message['xp'])
        self.assertEqual(8400, message['credits'])

    def test_records_survive_ack_restart_and_failed_write_retry_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'postbattle_state.json')
            store = postbattle_store.PostBattleStore(path=path)
            first = receipt_for(store, 1, 100)
            store.accept(first)
            store.acknowledge(first['arena_unique_id'])
            saved = store.progress()
            store = postbattle_store.PostBattleStore(path=path)
            self.assertEqual(saved, store.progress())
            self.assertFalse(store.accept(first))
            second = receipt_for(store, 2, 200, winner=0, damage=1400)
            with mock.patch.object(postbattle_store.port_config, 'write_json',
                                   side_effect=OSError('disk unavailable')):
                with self.assertRaises(OSError):
                    store.accept(second)
            self.assertEqual(saved, store.progress())
            self.assertTrue(store.accept(second))
            restarted = postbattle_store.PostBattleStore(path=path)
            self.assertFalse(restarted.accept(second))
            row = restarted.progress()['vehicles'][first['vehicle']]
            self.assertEqual(2, row['battles'])
            self.assertEqual(1, row['draws'])
            self.assertEqual(1400, row['maxDamage'])
            self.assertEqual(200, row['lastBattleTime'])

    def test_older_totals_remain_exact_without_inventing_discarded_bests(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'postbattle_state.json')
            store = postbattle_store.PostBattleStore(path=path)
            receipt = receipt_for(store, 1, 100)
            store.accept(receipt)
            store.acknowledge(receipt['arena_unique_id'])
            value = json.loads(Path(path).read_text())
            old_row = {
                'battles': 5, 'wins': 2, 'losses': 2, 'xp': 7000,
                'damage': 9000, 'kills': 20, 'shots': 50,
                'directHits': 30, 'survivedBattles': 3,
            }
            value['progress']['vehicles'][receipt['vehicle']] = old_row
            value['progress']['battles'] = 5
            Path(path).write_text(json.dumps(value))
            restarted = postbattle_store.PostBattleStore(path=path)
            row = restarted.progress()['vehicles'][receipt['vehicle']]
            for key, expected in old_row.items():
                self.assertEqual(expected, row[key])
            self.assertEqual(0, row.get('maxXP', 0))
            self.assertEqual(0, row.get('maxDamage', 0))
            self.assertEqual(0, row.get('maxFrags', 0))
            self.assertEqual(0, row.get('creationTime', 0))

            next_receipt = receipt_for(restarted, 2, 200, winner=2,
                                       damage=1500, kills=1, xp=800)
            restarted.accept(next_receipt)
            row = restarted.progress()['vehicles'][receipt['vehicle']]
            self.assertEqual((800, 1500, 1),
                             (row['maxXP'], row['maxDamage'], row['maxFrags']))
            self.assertEqual(1, row['draws'])

    def test_pending_receipt_can_restore_its_known_bests_without_reapplying_xp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'postbattle_state.json')
            store = postbattle_store.PostBattleStore(path=path)
            receipt = receipt_for(store, 1, 100)
            store.accept(receipt)
            value = json.loads(Path(path).read_text())
            row = value['progress']['vehicles'][receipt['vehicle']]
            for key in ('maxXP', 'maxDamage', 'maxFrags',
                        'creationTime', 'lastBattleTime'):
                row.pop(key, None)
            original_xp = row['xp']
            Path(path).write_text(json.dumps(value))
            restarted = postbattle_store.PostBattleStore(path=path)
            row = restarted.progress()['vehicles'][receipt['vehicle']]
            self.assertEqual(original_xp, row['xp'])
            self.assertEqual((600, 900, 2),
                             (row['maxXP'], row['maxDamage'], row['maxFrags']))
            self.assertEqual((100, 100),
                             (row['creationTime'], row['lastBattleTime']))
            self.assertFalse(restarted.accept(receipt))


if __name__ == '__main__':
    unittest.main()
