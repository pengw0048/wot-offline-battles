"""Lifetime records feed the exact #1513 statistics and vehicle-list blocks."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/res/scripts/client'))
from gui.mods.offline_lan_0922.account_rpc import data


class Dossier:
    def __init__(self, compact):
        self.blocks = {}

    def __getitem__(self, name):
        return self.blocks.setdefault(name, {})

    def makeCompDescr(self):
        return self.blocks


class CareerDossierTests(unittest.TestCase):
    def setUp(self):
        self.progress = {'vehicles': {
            'ussr:R11_MS-1': {
                'battles': 3, 'wins': 1, 'losses': 1,
                'xp': 1200, 'originalXP': 600, 'damage': 1800, 'kills': 3,
                'shots': 12, 'directHits': 9, 'piercings': 7,
                'damageReceived': 500, 'hitsReceived': 4,
                'damageBlockedByArmor': 200, 'potentialDamageReceived': 700,
                'damageAssistedTrack': 40, 'damageAssistedRadio': 80,
                'spotted': 6, 'survivedBattles': 2, 'winAndSurvived': 1,
                'maxXP': 700, 'maxDamage': 1000, 'maxFrags': 2,
                'creationTime': 100, 'lastBattleTime': 300, 'changeTime': 3},
            'germany:G04_PzVI_Tiger_I': {
                'battles': 1, 'wins': 1, 'losses': 0,
                'xp': 900, 'originalXP': 450, 'damage': 1200, 'kills': 1,
                'shots': 4, 'directHits': 3, 'piercings': 2,
                'maxXP': 900, 'maxDamage': 1200, 'maxFrags': 1,
                'creationTime': 400, 'lastBattleTime': 400, 'changeTime': 4}}}
        self.resolve = lambda name: 101 if name.startswith('ussr:') else 202

    def test_account_without_medals_has_lifetime_totals_and_vehicle_list(self):
        result = data.account_dossier(self.progress, Dossier, self.resolve)
        stats = result['a15x15']
        self.assertEqual((4, 2, 1), tuple(stats[name] for name in (
            'battlesCount', 'wins', 'losses')))
        # Stock computes the draw and average values from these counters.
        self.assertEqual(1, stats['battlesCount'] - stats['wins'] - stats['losses'])
        self.assertEqual(750, stats['damageDealt'] / stats['battlesCount'])
        self.assertEqual(525, stats['xp'] / stats['battlesCount'])
        self.assertEqual(1, stats['frags'] / stats['battlesCount'])
        self.assertEqual(.75, stats['directHits'] / stats['shots'])
        self.assertEqual(1050, result['a15x15_2']['originalXP'])
        self.assertEqual({101: (3, 1, 1200), 202: (1, 1, 900)},
                         result['a15x15Cut'])
        self.assertEqual({'maxXP': 900, 'maxXPVehicle': 202,
                          'maxDamage': 1200, 'maxDamageVehicle': 202,
                          'maxFrags': 2, 'maxFragsVehicle': 101},
                         result['max15x15'])
        self.assertEqual({'creationTime': 100, 'lastBattleTime': 400},
                         result['total'])

    def test_vehicle_dossier_keeps_native_maxima_and_receiving_counters(self):
        unused, rows = data.dossiers(
            postbattle_progress=self.progress, dossier_factory=Dossier,
            vehicle_type_resolver=self.resolve)
        result = dict((cd, value) for cd, unused, value in rows)[101]
        self.assertEqual(1, result['a15x15']['winAndSurvived'])
        self.assertEqual(4, result['a15x15_2']['directHitsReceived'])
        self.assertEqual(700, result['a15x15_2']['potentialDamageReceived'])
        self.assertEqual({'maxXP': 700, 'maxDamage': 1000, 'maxFrags': 2},
                         result['max15x15'])
        self.assertEqual(600, result['a15x15_2']['originalXP'])
        self.assertNotIn('critsReceived', result['a15x15_2'])

    def test_account_ignores_stale_duplicate_account_counters(self):
        self.progress.update(battles=9000, damage=9000, xp=9000)
        result = data.account_dossier(self.progress, Dossier, self.resolve)
        self.assertEqual(4, result['a15x15']['battlesCount'])
        self.assertEqual(3000, result['a15x15']['damageDealt'])

    def test_no_battles_or_medals_keeps_empty_native_descriptor(self):
        self.assertEqual('', data.account_dossier({}, Dossier, self.resolve))

    def test_schema_upgrade_refreshes_persisted_cache_without_another_battle(self):
        version, rows = data.dossiers(
            1, 99, self.progress, Dossier, self.resolve)
        self.assertEqual(2, version)
        self.assertEqual(2, len(rows))
        self.assertEqual((2, []), data.dossiers(
            version, 4, self.progress, Dossier, self.resolve))


if __name__ == '__main__':
    unittest.main()
