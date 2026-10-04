from __future__ import print_function
"""Radio packing and consumption against source or actual Python 2.7 payload.

The fixture sets observer geometry; real RadioNetwork, spot-lease packing and
BattleRuntime receipt handling run unmodified. Public update/server/model
integration is covered separately by test_bot080_radio_integration.py.
"""
import os
import shutil
import sys
import tempfile
import types
import unittest
import zipfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMP = None
if len(sys.argv) == 3 and sys.argv[1] == '--compiled':
    TEMP = tempfile.mkdtemp(prefix='bot080-radio-pyc-')
    with zipfile.ZipFile(sys.argv[2]) as archive:
        archive.extractall(TEMP)
    CLIENT = os.path.join(TEMP, 'res', 'scripts', 'client')
    del sys.argv[1:]
else:
    CLIENT = os.path.join(ROOT, 'src', 'res', 'scripts', 'client')
sys.path.insert(0, CLIENT)
for package, subpath in (('gui', 'gui'), ('gui.mods', 'gui/mods')):
    module = types.ModuleType(package)
    module.__path__ = [os.path.join(CLIENT, subpath)]
    sys.modules[package] = module
sys.modules['gui'].mods = sys.modules['gui.mods']
from gui.mods.offline_lan_0922.bot_runtime import BotRuntime
from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime

class Record(object):
    def __init__(self, **values):
        self.__dict__.update(values)

class RadioContractTests(unittest.TestCase):
    def setUp(self):
        self.bot = BotRuntime(1)
        self.source = {'id': 11, 'team': 1, 'kind': 'bot',
                       'x': 350.0, 'y': 0.0, 'z': 0.0}
        self.target = {'id': 12, 'network_id': 12, 'team': 2, 'kind': 'bot',
                       'x': 500.0, 'y': 0.0, 'z': 0.0,
                       'health': 1000, 'max_health': 1000}
        self.actors = {('human', 1): (1, (0, 0, 0), 200),
                       ('bot', 11): (1, (350, 0, 0), 200),
                       ('bot', 13): (1, (700, 0, 0), 200),
                       ('bot', 12): (2, (500, 0, 0), 200)}
        self.bot._radio_network.configure(self.actors, 10.0)
        self.key = (1, 'bot', 12)
        self.bot._renew_team_spot(self.key, 10.0)
        self.bot._visible_target_poses[self.key] = self.bot._target_pose_snapshot(self.target)
        self.bot._renew_observer_spot(self.source, self.target, 10.0)
        self.aggregate = {self.key: [True, {11}, dict(self.target), set(), {11}]}

    def pack(self, now=10.0):
        return self.bot._pack_observations(self.aggregate, now)[0]

    def test_combined_range_receipt_survives_production_packing_and_client(self):
        contact = self.pack()
        recipients = {(r['kind'], r['id']) for r in contact['radio_recipients']}
        self.assertIn(('human', 1), recipients)
        self.assertNotIn(('bot', 12), recipients)
        battle = BattleRuntime.__new__(BattleRuntime)
        battle.client = Record(player_id=1, team=1)
        battle._records = {
            'player:1': {'local': True, 'state': {'team': 1, 'alive': True}},
            'bot:12': {'kind': 'bot', 'network_id': 12, 'state': {'team': 2},
                       'presentation': False}}
        battle._apply_team_observation({'type': 'bot_observation', 'contacts': [contact]}, 10.0)
        self.assertEqual(20.0, battle._records['bot:12']['radio_spot_until'])

    def test_disconnected_receiver_does_not_keep_its_team_lease(self):
        self.actors[('human', 1)] = (1, (-1000, 0, 0), 200)
        self.bot._radio_network.configure(self.actors, 11.0)
        rows = self.pack(11.0)['radio_recipients']
        self.assertFalse(any(r['kind'] == 'human' and r['id'] == 1 for r in rows))
        self.assertTrue(any(r['id'] == 11 for r in rows))

    def test_radio_does_not_chain_another_receivers_knowledge(self):
        self.bot._radio_network.observations.clear()
        self.bot._renew_observer_spot(dict(self.source, id=13, x=700.0), self.target, 10.0)
        rows = self.pack()['radio_recipients']
        self.assertTrue(any(r['id'] == 11 for r in rows))
        self.assertFalse(any(r['kind'] == 'human' and r['id'] == 1 for r in rows))

    def test_expired_contacts_have_no_recipients(self):
        contact = self.pack(21.0)
        self.assertFalse(contact['visible'])
        self.assertEqual([], contact['radio_recipients'])

    def test_round_change_clears_radio_state(self):
        self.bot.battle_start({'round_id': 2, 'bot_authority_id': -1, 'bots': []})
        self.assertEqual({}, self.bot._radio_network.actors)
        self.assertEqual({}, self.bot._radio_network.observations)

if __name__ == '__main__':
    try:
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(RadioContractTests))
        sys.exit(0 if result.wasSuccessful() else 1)
    finally:
        if TEMP:
            shutil.rmtree(TEMP)
