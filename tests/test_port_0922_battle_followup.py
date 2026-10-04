import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/res/scripts/client'))
sys.path.insert(0, str(ROOT / 'server'))
from gui.mods.offline_lan_0922 import battle_shell_tooltip, destructible_layout
from gui.mods.offline_lan_0922 import destructibles_sensor as sensor
from gui.mods.offline_lan_0922.lan_client import LANClient
from gui.mods.offline_lan_0922.authority_worker import AuthorityWorkerLANClient
from gui.mods.offline_lan_0922.account_rpc import postbattle_store
from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime
from lan_battle_server import BattleState
from test_port_0922_postbattle import _receipt, _packed_vehicle
from test_port_0922_battle_runtime import _runtime


class FollowupTests(unittest.TestCase):
    def test_receipt_projects_received_breakdown_to_native_result(self):
        receipt = _receipt()
        receipt['stats'].update(BattleState._receipt_statistics({
            'hits_received': 7, 'piercings_received': 2,
            'no_damage_direct_hits_received': 4, 'explosion_hits_received': 3}))
        for row in receipt.get('public_results', ()):
            if row.get('actor_kind') == 'player' and row.get('actor_id') == receipt['player_id']:
                row['stats'] = dict(receipt['stats'])
        full = _packed_vehicle(receipt)
        self.assertEqual([7, 2, 4, 3], [full[name] for name in (
            'directHitsReceived', 'piercingsReceived',
            'noDamageDirectHitsReceived', 'explosionHitsReceived')])

    def test_tooltip_uses_hovered_shell_and_installed_gun(self):
        a, b = [types.SimpleNamespace(compactDescr=n) for n in (1, 2)]
        vehicle = types.SimpleNamespace(gun=types.SimpleNamespace(shots=[
            {'shell': a, 'speed': 760}, {'shell': b, 'speed': 1120}]))
        text = '{HEADER}HE{/HEADER}\n/{BODY}damage 500{/BODY}'
        result = battle_shell_tooltip.append_speed(text, b, vehicle)
        self.assertIn('1120', result)
        self.assertNotIn('760', result)
        self.assertIn('damage 500', result)
        self.assertEqual(1, result.count('{BODY}'))
        self.assertEqual(text, battle_shell_tooltip.append_speed(
            text, types.SimpleNamespace(compactDescr=3), vehicle))
        self.assertIsInstance(battle_shell_tooltip.append_speed(
            text.encode(), a, vehicle), bytes)

    def test_spg_direct_damage_uses_penetration_voice_and_keeps_physical_result(self):
        for tag, shell, damage, result, explosion in (
                ('SPG', 'HIGH_EXPLOSIVE', 80, 1, False),
                ('SPG', 'HIGH_EXPLOSIVE', 0, 1, False),
                ('SPG', 'HIGH_EXPLOSIVE', 80, 2, False),
                ('AT-SPG', 'HIGH_EXPLOSIVE', 80, 1, False),
                ('SPG', 'ARMOR_PIERCING', 80, 1, False)):
            runtime = _runtime()
            battle = BattleRuntime(runtime)
            battle._avatar = runtime.bigworld.avatar
            battle._avatar.playerVehicleID = 10
            battle._synchronise_player_identity(10)
            battle._server_entity = lambda unused: types.SimpleNamespace(
                typeDescriptor=types.SimpleNamespace(type=types.SimpleNamespace(tags=(tag,))))
            runtime.constants.SHELL_TYPES_INDICES = {'HIGH_EXPLOSIVE': 1, 'ARMOR_PIERCING': 2}
            battle._feedback_shell_fields = lambda *unused: (runtime.constants.SHELL_TYPES_INDICES[shell], False)
            event = {'kind': 'bot_hit', 'damage': damage, 'shot_result': result,
                     'source': 'shot', 'attack_reason': 0}
            battle._present_combat_feedback(event,
                {'engine_id': 11, 'local': False, 'kind': 'bot', 'state': {'team': 2}},
                {'engine_id': 10, 'local': True, 'kind': 'player', 'state': {'team': 1}})
            flags = battle._avatar.shot_results[0][0] >> 32
            self.assertEqual(explosion, bool(flags & runtime.constants.VEHICLE_HIT_FLAGS.ATTACK_IS_EXTERNAL_EXPLOSION))
            self.assertEqual(result, event['shot_result'])

    def test_ping_requires_worker_main_loop_and_rejects_old_authority(self):
        state = BattleState()
        worker_messages, replies = [], []
        worker = types.SimpleNamespace(connected=True, offer_reliable=lambda m: worker_messages.append(m) or True)
        player = types.SimpleNamespace(player_id=1, connected=True, offer_reliable=lambda m: replies.append(m) or True)
        state.players[1] = player
        state.simulation_worker = worker
        self.assertTrue(state.request_worker_ping(player, {'seq': 1, 'client_time': 10.0}))
        self.assertEqual([], replies)
        client = AuthorityWorkerLANClient('localhost', 28782)
        client._send = lambda message: state.resolve_worker_ping(worker, message)
        client._handle_message(worker_messages.pop())
        self.assertEqual('worker_pong', replies[0]['type'])
        self.assertEqual(10.0, replies[0]['client_time'])
        self.assertTrue(state.request_worker_ping(player, {'seq': 2, 'client_time': 11.0}))
        state.authority_epoch += 1
        client._handle_message(worker_messages.pop())
        self.assertEqual(1, len(replies))

    def test_worker_ping_reports_simulation_wait_and_stale_response(self):
        client = LANClient('localhost', 28782, 'Tester', 'ussr:R11_MS-1')
        client.connected = True
        client.round_id, client.authority_epoch = 1, 2
        client._worker_ping_started = 10.0
        client._handle_message({'type': 'worker_pong', 'round_id': 1,
            'authority_epoch': 2, 'seq': 1, 'client_time': 10.0,
            'frame_ms': 100.0, '_client_received_time': 10.240})
        self.assertEqual((100, False), client.worker_ping_display(10.25))
        self.assertAlmostEqual(240.0, client.worker_rtt_ms)
        self.assertEqual((999, True), client.worker_ping_display(14.0))


class DestructibleLayoutTests(unittest.TestCase):
    def tearDown(self):
        sensor.set_catalog(None)

    def test_murovanka_live_slot_eight_reindexes_fence_and_following_tree(self):
        catalog = json.loads((ROOT / 'destructibles/11_murovanka.json').read_text())
        sensor.set_catalog(catalog)
        prepared = sensor._destructible_catalog
        index = prepared['authored_placement_index']
        fence = prepared['baked_instances'][(32124, 7)]
        match = destructible_layout.match_placement(index, 32124, fence['signature'], 'fragile')
        self.assertEqual((32124, 7), match[0])
        matches = {}
        for records in (prepared['baked_instances'], prepared['tree_instances']):
            for wire, record in records.items():
                if wire[0] == 32124:
                    # Synthetic live matrix responses reproduce the report's
                    # retained empty slot; each transform is proved separately.
                    item = wire[1] + (wire[1] >= 7)
                    matches[item] = destructible_layout.match_placement(
                        index, 32124, record['signature'], record['kind'])
        self.assertTrue(all(matches.values()))
        entry = {'fingerprint': (max(matches) + 1, ()), 'placement_matches': matches,
                 'layout_key': (1, 32124)}
        sensor.g_offh_destr_instances = {(32124, 7): {'bin_keys': ((1, 2),)}}
        sensor.g_offh_destr_contact_bins = {(1, 2): {(32124, 7)}}
        mapping = sensor._commit_proved_chunk_layout_1513(entry, 32124)
        self.assertIn('gaf001_WoodFence', mapping[8])
        self.assertEqual((32124, 8), prepared['instances'][fence['signature']]['wire'])
        self.assertTrue(any((32124, 8) in wires for wires in prepared['baked_shot_bins'].values()))
        self.assertIn((32124, 7), prepared['excluded_instances'])
        self.assertEqual(fence, prepared['baked_instances'][(32124, 8)])
        self.assertGreater(len([wire for wire in prepared['tree_instances'] if wire[0] == 32124]), 0)
        self.assertNotIn((32124, 7), sensor.g_offh_destr_instances)
        self.assertNotIn((1, 2), sensor.g_offh_destr_contact_bins)
        sensor._invalidate_chunk_native_names_1513(32124)
        self.assertNotIn((1, 32124), sensor.g_offh_destr_proved_layouts)
        # A later load can use another slot layout. Rebuild broad-phase
        # indices from the immutable authored wires, never the prior remap.
        self.assertTrue(sensor._request_layout_repair_1513((32124, 9), fence['signature']))
        entry = {'fingerprint': (max(matches) + 2, ()),
                 'placement_matches': dict((item + (item >= 8), match)
                     for item, match in matches.items()), 'layout_key': (1, 32124)}
        sensor._commit_proved_chunk_layout_1513(entry, 32124)
        self.assertEqual(fence, prepared['baked_instances'][(32124, 9)])
        fence_bins = [key for key, wires in prepared['authored_baked_shot_bins'].items()
                      if (32124, 7) in wires]
        self.assertTrue(fence_bins)
        self.assertTrue(all((32124, 9) in prepared['baked_shot_bins'][key] for key in fence_bins))

    def test_layout_never_matches_wrong_type_nearby_or_ambiguous_geometry(self):
        sig = (0, 0, 0, 1000, 0, 0, 0, 1000, 0, 0, 0, 1000)
        record = {'signature': sig, 'kind': 'tree'}
        index = destructible_layout.placement_index({}, {(1, 1): record})
        self.assertIsNone(destructible_layout.match_placement(index, 1, sig, 'fragile'))
        self.assertIsNone(destructible_layout.match_placement(index, 2, sig, 'tree'))
        self.assertIsNone(destructible_layout.match_placement(index, 1, (10,) + sig[1:], 'tree'))
        index = destructible_layout.placement_index({}, {(1, 1): record, (1, 2): record})
        self.assertIsNone(destructible_layout.match_placement(index, 1, sig, 'tree'))

    def test_incremental_native_scan_recovers_reported_shift_before_names_are_used(self):
        from test_port_0922_destructibles import _Vector
        catalog = json.loads((ROOT / 'destructibles/11_murovanka.json').read_text())
        sensor.set_catalog(catalog)
        prepared = sensor._destructible_catalog
        rows = {}
        by_name = {}
        categories = {'tree': 0, 'falling': 1, 'fragile': 2, 'structure': 3}
        for group in ('baked_instances', 'tree_instances'):
            for wire, record in prepared[group].items():
                if wire[0] == 32124:
                    rows[wire[1] + (wire[1] >= 7)] = record
                    by_name[record['descriptor_filename']] = {'type': categories[record['kind']]}
        class Matrix:
            def __init__(self, signature):
                self.signature = signature
            def applyVector(self, point):
                s = self.signature
                return _Vector(*[sum(s[3 + axis * 3 + row] * value
                    for axis, value in enumerate((point.x, point.y, point.z))) / 1000.
                    for row in range(3)])
            def applyPoint(self, point):
                return self.applyVector(point) + _Vector(*[x / 1000. for x in self.signature[:3]])
        native = types.SimpleNamespace(
            now=0., time=lambda: native.now,
            wg_getChunkMatrix=lambda *args: types.SimpleNamespace(translation=_Vector()),
            wg_getDestructibleMatrix=lambda space, chunk, item: Matrix(rows[item]['signature']),
            wg_getDestructibleEffectCategory=lambda space, chunk, item, material:
                categories[rows[item]['kind']] if item in rows else -1)
        area = types.SimpleNamespace(DESTR_TYPE_TREE=0, DESTR_TYPE_FALLING_ATOM=1,
            DESTR_TYPE_FRAGILE=2, DESTR_TYPE_STRUCTURE=3,
            g_cache=types.SimpleNamespace(getDescByFilename=lambda name: by_name.get(name)))
        names = [rows[item]['descriptor_filename'] for item in sorted(rows)]
        with mock.patch.dict(sys.modules, {'Math': types.SimpleNamespace(Matrix=lambda x: x, Vector3=_Vector)}):
            # Enter through the real first-use name scan. A manual mismatch
            # trigger hid the production deadlock: name alignment quarantined
            # shifted slots before the proximity matrix pass could reach them.
            self.assertFalse(sensor._layout_repair_pending_1513(32124))
            for tick in range(10):
                native.now += .04
                mapping, status = sensor._chunk_native_names_1513(
                    native, area, 1, 32124, max(rows) + 1, names)
                if status != 'pending_alignment':
                    break
        self.assertEqual('exact', status)
        self.assertIn('gaf001_WoodFence', mapping[8])
        self.assertNotIn(7, mapping)
        self.assertEqual(rows[35]['descriptor_filename'], mapping[35])
