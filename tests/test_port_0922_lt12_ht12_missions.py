"""LT12/HT12 conditions use allocated assists and immutable battle loadouts."""
import base64
import copy
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

from test_port_0922_personal_campaign_battle import node
import test_port_0922_mission_events as event_fixture
import test_port_0922_mission_visibility as visibility_fixture
from test_port_0922_postbattle import (
    _latest_receipt, postbattle_store, Player, _Socket,
    lan_server_module as server, lan_client_module as client)
from gui.mods.offline_lan_0922 import personal_campaign_battle as policy
from gui.mods.offline_lan_0922 import mission_events


def _camouflage_modules():
    """Only the installed readers used to inspect frozen seasonal outfits."""
    def outfit(compact):
        slot = types.SimpleNamespace(getItem=lambda: (
            object() if compact == b'hull-camouflage' else None))
        return types.SimpleNamespace(hull=types.SimpleNamespace(
            slotFor=lambda unused_type: slot))
    return {
        'ArenaType': types.SimpleNamespace(g_cache={1: types.SimpleNamespace(
            geometryName='01_karelia', gameplayName='ctf',
            vehicleCamouflageKind=0)}),
        'items.components.c11n_constants': types.SimpleNamespace(
            SeasonType=types.SimpleNamespace(fromArenaKind=lambda unused: 1)),
        'gui.shared.gui_items': types.SimpleNamespace(
            GUI_ITEM_TYPE=types.SimpleNamespace(CAMOUFLAGE=10)),
        'gui.shared.gui_items.customization.outfit': types.SimpleNamespace(
            Outfit=outfit),
    }


class LT12HT12ConditionTests(unittest.TestCase):
    def setUp(self):
        self.definitions = json.loads((Path(__file__).parent /
            'fixtures/personal_mission_lt12_ht12_conditions_0922.json').read_text())['missions']
        self.receipt = dict(player_id=1, vehicle='ussr:medium',
            team=1, winner=1, death_reason=-1, map='01_karelia', max_health=1000,
            vehicle_compact_descr=base64.b64encode(b'camouflageNet').decode('ascii'),
            vehicle_outfits={'1': base64.b64encode(b'hull-camouflage').decode('ascii')},
            stats={'damage': 6000, 'damage_blocked': 3000, 'not_spotted': 0},
            public_results=[dict(actor_kind='bot', actor_id=2, team=2)],
            interactions=[dict(target_kind='bot', target_id=2,
                assist_radio=9000, mission_events_version=4,
                mission_events_complete=True, mission_events=[
                    ['assist_radio', 1000, 3000, True],
                    ['assist_radio', 2000, 6000, False]])])
        self.vehicles = types.SimpleNamespace(
            VehicleDescr=visibility_fixture.MissionVisibilityPolicyTests.describe)

    def check(self, definition, stage='main'):
        with mock.patch.dict(sys.modules, _camouflage_modules()):
            return policy._postbattle(
                {'children': [('conditions', node(definition[stage]))]},
                policy._Facts(self.receipt, self.vehicles))

    def test_all_four_operations_main_and_honours(self):
        for definition in self.definitions:
            for stage in ('main', 'add'):
                with self.subTest(name=definition['name'], stage=stage):
                    self.assertEqual((True, set()), self.check(definition, stage))

    def test_lt12_counts_only_hidden_allocated_damage_at_inclusive_boundary(self):
        for definition in self.definitions:
            if definition['chain'] != 1:
                continue
            threshold = (250, 750, 1500, 3000)[definition['operation'] - 1]
            event = self.receipt['interactions'][0]['mission_events'][0]
            event[2] = threshold - 1
            self.assertEqual((False, set()), self.check(definition))
            event[2] += 1
            self.assertEqual((True, set()), self.check(definition))
            # A never-spotted end counter must not turn visible shares hidden.
            self.receipt['stats']['not_spotted'] = 1
            event[3] = False
            self.assertEqual((False, set()), self.check(definition))
            event[3] = True
            self.receipt['stats']['not_spotted'] = 0

    def test_ht12_uses_configured_full_health_and_keeps_damage_requirement(self):
        self.receipt['max_health'] = 4090
        self.receipt['health'] = 10
        for definition in self.definitions:
            if definition['chain'] != 2:
                continue
            operation = definition['operation'] - 1
            threshold = 4090 * (1, 2, 3, 3)[operation]
            damage = (500, 1000, 2000, 3000)[operation]
            self.receipt['stats'].update(damage_blocked=threshold - 1, damage=damage)
            self.assertEqual((False, set()), self.check(definition))
            self.receipt['stats']['damage_blocked'] += 1
            self.assertEqual((True, set()), self.check(definition))
            self.receipt['stats']['damage'] -= 1
            self.assertEqual((False, set()), self.check(definition))

    def test_honours_survival_and_win_do_not_block_main_completion(self):
        for definition in self.definitions:
            if definition['qid'] == 12:
                continue
            self.receipt['death_reason'] = 0
            self.assertEqual((True, set()), self.check(definition))
            self.assertEqual((False, set()), self.check(definition, 'add'))
            self.receipt['death_reason'] = -1
            if definition['chain'] == 2 or definition['operation'] == 4:
                self.receipt['winner'] = 2
                self.assertEqual((False, set()), self.check(definition, 'add'))
                self.receipt['winner'] = 1

    def test_lt12_first_honours_use_frozen_net_and_map_season_hull_camouflage(self):
        definition = self.definitions[0]
        self.receipt['vehicle_outfits'] = {
            '2': base64.b64encode(b'hull-camouflage').decode('ascii')}
        self.assertEqual((True, set()), self.check(definition))
        self.assertEqual((False, set()), self.check(definition, 'add'))
        self.receipt['vehicle_outfits']['1'] = base64.b64encode(b'turret-only').decode('ascii')
        self.assertEqual((False, set()), self.check(definition, 'add'))
        self.receipt['vehicle_outfits']['1'] = base64.b64encode(b'hull-camouflage').decode('ascii')
        self.assertEqual((True, set()), self.check(definition, 'add'))
        self.receipt['vehicle_compact_descr'] = base64.b64encode(b'coatedOptics').decode('ascii')
        self.assertEqual((False, set()), self.check(definition, 'add'))

    def test_missing_old_or_incomplete_evidence_stays_unknown(self):
        original = copy.deepcopy(self.receipt)
        for mode in ('missing', 'legacy', 'truncated'):
            self.receipt = copy.deepcopy(original)
            self.receipt.pop('max_health')
            interaction = self.receipt['interactions'][0]
            if mode == 'missing':
                for field in mission_events.FIELDS:
                    interaction.pop(field)
            elif mode == 'legacy':
                interaction.update(mission_events_version=3, mission_events=[])
            else:
                interaction['mission_events_complete'] = False
            for definition in self.definitions:
                self.assertIsNone(self.check(definition)[0], (mode, definition['name']))
        self.receipt = copy.deepcopy(original)
        self.receipt.pop('vehicle_outfits')
        self.assertIsNone(self.check(self.definitions[0], 'add')[0])


class LT12HT12ReceiptTests(unittest.TestCase):
    state = event_fixture.MissionEventReceiptTests.state

    def test_shared_radio_visibility_and_frozen_loadout_survive_every_receipt_reader(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'receipts.json')
            state, scout, enemy = self.state(path)
            for player_id in (3, 4):
                state.players[player_id] = Player(player_id, _Socket(),
                    ('127.0.0.1', player_id), name='Ally%d' % player_id,
                    vehicle='ussr:R11_MS-1', team=1,
                    account_key=str(player_id) * 32)
            scout.max_health = 4090
            scout.outfits = {'1': base64.b64encode(b'hull-camouflage').decode('ascii')}
            state._freeze_round_participants(tuple(state.players.values()))
            scout.max_health, scout.outfits = 100, {}
            actor, target, shooter = ('player', 1), ('player', 2), ('player', 4)
            state.player_spotted = {1: {target}, 3: {target}, 2: {('player', 3)}}
            state._record_damage(shooter, target, 501, {})
            self.assertEqual([['assist_radio', 60000, 251, True]],
                             state._receipt_interactions(actor)[0]['mission_events'])
            self.assertEqual([['assist_radio', 60000, 250, False]],
                             state._receipt_interactions(('player', 3))[0]['mission_events'])
            # A lost direct spot remains visible while the enemy lease lives.
            state.team_lit_targets[2] = {actor: server.time.monotonic() + 10.}
            state._record_damage(shooter, target, 501, {})
            state.team_lit_targets[2] = {}
            state._record_damage(shooter, target, 501, {})
            # A shooter who sees the target earns nobody a radio assist.
            state.player_spotted[4] = {target}
            state._record_damage(shooter, target, 501, {})
            self.assertTrue(state._finish_battle(1, 'elimination'))
            receipt = _latest_receipt(state, scout.account_key)
            self.assertEqual(4090, receipt['max_health'])
            self.assertEqual({'1': base64.b64encode(b'hull-camouflage').decode('ascii')},
                             receipt['vehicle_outfits'])
            self.assertEqual(753, receipt['stats']['assist_radio'])
            self.assertEqual(502, policy._Facts(receipt, None).result(
                'damageAssistedRadioWhileInvisible'))
            self.assertTrue(client._valid_battle_receipt(receipt))
            restarted = server.BattleState(map_name='01_karelia', receipt_state_path=path)
            replay = _latest_receipt(restarted, scout.account_key)
            self.assertEqual(receipt, replay)
            stored = postbattle_store._receipt(replay)
            self.assertEqual(4090, stored['max_health'])
            self.assertEqual(receipt['vehicle_outfits'], stored['vehicle_outfits'])
            self.assertEqual(502, policy._Facts(stored, None).result(
                'damageAssistedRadioWhileInvisible'))

    def test_legacy_receipt_does_not_acquire_guessed_loadout_evidence(self):
        state, scout, unused = self.state()
        state._finish_battle(1, 'elimination')
        receipt = _latest_receipt(state, scout.account_key)
        receipt.pop('max_health')
        receipt.pop('vehicle_outfits')
        self.assertTrue(client._valid_battle_receipt(receipt))
        for result in (server._persisted_result_receipt(copy.deepcopy(receipt)),
                       postbattle_store._receipt(receipt)):
            self.assertNotIn('max_health', result)
            self.assertNotIn('vehicle_outfits', result)

    def test_invalid_loadout_and_assist_evidence_are_rejected_by_all_readers(self):
        state, scout, unused = self.state()
        state._record_mission_event(('player', 1), ('player', 2), 'assist_radio', 250, True)
        state._finish_battle(1, 'elimination')
        receipt = _latest_receipt(state, scout.account_key)
        for field, values in (('max_health', (None, True, 0, -1, 100001, 4090.0, '4090')),
                              ('vehicle_outfits', (None, [], {'1': 'bad?'}))):
            for value in values:
                bad = copy.deepcopy(receipt)
                bad[field] = value
                self.assertFalse(client._valid_battle_receipt(bad), (field, value))
                with self.assertRaises(ValueError):
                    server._persisted_result_receipt(copy.deepcopy(bad))
                with self.assertRaises(ValueError):
                    postbattle_store._receipt(bad)
        for event in (['assist_radio', 60000, 250, 1], ['assist_radio', 60000, 0, True],
                      ['assist_radio', 60000, 250.0, True], ['assist_radio', 60000, 250]):
            bad = copy.deepcopy(receipt)
            bad['interactions'][0]['mission_events'] = [event]
            self.assertFalse(client._valid_battle_receipt(bad))
            with self.assertRaises(ValueError):
                server._persisted_result_receipt(copy.deepcopy(bad))
            with self.assertRaises(ValueError):
                postbattle_store._receipt(bad)
