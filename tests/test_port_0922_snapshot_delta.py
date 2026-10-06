import copy
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_ROOT = ROOT / 'src/res/scripts/client'
CODEC_PATH = CLIENT_ROOT / 'gui/mods/offline_lan_0922/snapshot_delta.py'
sys.path.insert(0, str(CLIENT_ROOT))
from gui.mods.offline_lan_0922 import snapshot_delta as codec


def snapshot(**updates):
    value = {
        'type': 'snapshot', 'round_id': 1, 'authority_epoch': 2,
        'map': '01_karelia', 'bot_authority_id': -1,
        'server_tick': 4, 'server_time_ms': 400,
        'players': [{'id': 1, 'x': 1.23456789, 'alive': True,
                     'state': {'health': 100, 'damaged': ['gun']}}],
        'bots': [{'id': 7, 'x': 20.0, 'ammo': [3, 2],
                  'equipment_states': [{'equipment': 0, 'usesLeft': 1}]}],
        'bot_equipment_contracts': [{'name': 'repair', 'tags': ['equipment']}],
        'projectiles': [], 'bot_orders': [], 'destructibles': [],
    }
    value.update(updates)
    return value


def transmit(wire):
    return json.loads(json.dumps(wire))


class SnapshotDeltaTests(unittest.TestCase):
    def test_full_delta_forced_full_preserves_complete_snapshots(self):
        sender = receiver = None
        frames = [snapshot(), snapshot(server_tick=5), snapshot(server_tick=6)]
        frames[1]['players'][0]['x'] = -7.987654321
        frames[1]['bots'][0]['ammo'] = [2, 2]
        for sequence, frame in enumerate(frames, 1):
            wire, sender = codec.encode(frame, sender, sequence,
                                        force_full=sequence == 3)
            restored, receiver = codec.decode(transmit(wire), receiver)
            self.assertEqual(frame, restored)
            self.assertEqual(sequence == 2, wire['snapshot_delta'])
            self.assertNotIn('snapshot_seq', restored)

    def test_actor_add_remove_and_field_clear_delete_are_distinct(self):
        first = snapshot()
        wire, sender = codec.encode(first, None, 1)
        unused, receiver = codec.decode(wire, None)
        second = snapshot(players=[{'id': 2, 'name': 'New player'}])
        second['bots'][0].pop('x')
        second['bots'][0]['ammo'] = []
        second['bots'][0]['equipment_states'] = None
        second['bots'][0]['empty'] = {}
        second['bots'].append({'id': 9, 'x': 0.0})
        wire, unused = codec.encode(second, sender, 2)
        self.assertEqual([1], wire['players']['remove'])
        self.assertEqual([{'id': 2, 'name': 'New player'}],
                         wire['players']['add'])
        patch = wire['bots']['update'][0]
        self.assertEqual(['x'], patch['remove'])
        self.assertEqual({'ammo': [], 'equipment_states': None, 'empty': {}},
                         patch['set'])
        restored, unused = codec.decode(transmit(wire), receiver)
        self.assertEqual(second, restored)

    def test_unchanged_rows_have_empty_patches_and_other_sections_do_not_inherit(self):
        first = snapshot()
        wire, sender = codec.encode(first, None, 1)
        unused, receiver = codec.decode(wire, None)
        second = snapshot(server_tick=5)
        del second['bot_orders']
        del second['destructibles']
        second['projectiles'] = [{'projectile_id': 'player:1:1'}]
        second['bot_equipment_contracts'] = [{'name': 'another'}]
        wire, unused = codec.encode(second, sender, 2)
        self.assertEqual({}, wire['players'])
        self.assertEqual({}, wire['bots'])
        self.assertEqual(second['bot_equipment_contracts'],
                         wire['bot_equipment_contracts'])
        restored, unused = codec.decode(wire, receiver)
        self.assertEqual(second, restored)
        self.assertNotIn('bot_orders', restored)

    def test_nested_values_replace_whole_fields_without_quantization(self):
        first = snapshot()
        wire, sender = codec.encode(first, None, 1)
        unused, receiver = codec.decode(wire, None)
        second = snapshot()
        second['players'][0]['state'] = {'health': 0}
        second['players'][0]['alive'] = 1
        second['bots'][0]['x'] = 20
        wire, unused = codec.encode(second, sender, 2)
        self.assertEqual({'health': 0},
                         wire['players']['update'][0]['set']['state'])
        restored, unused = codec.decode(transmit(wire), receiver)
        self.assertIs(type(restored['players'][0]['alive']), int)
        self.assertIs(type(restored['bots'][0]['x']), int)
        self.assertEqual(second, restored)

    def test_lineage_change_rebases_full_and_cannot_patch_old_round(self):
        first = snapshot()
        wire, sender = codec.encode(first, None, 1)
        unused, receiver = codec.decode(wire, None)
        for key, value in (('round_id', 2), ('authority_epoch', 3),
                           ('map', '07_lakeville'), ('bot_authority_id', -2),
                           ('bot_authority_id', None),
                           ('bot_manifest_revision', 1)):
            with self.subTest(key=key):
                second = snapshot(**{key: value})
                wire, unused = codec.encode(second, sender, 2)
                self.assertFalse(wire['snapshot_delta'])
                restored, unused = codec.decode(wire, receiver)
                self.assertEqual(second, restored)
                wire['snapshot_delta'] = True
                wire['snapshot_base_seq'] = 1
                wire['players'] = wire['bots'] = {}
                with self.assertRaises(codec.SnapshotDeltaError):
                    codec.decode(wire, receiver)

    def test_worker_loss_full_then_delta_preserves_absent_authority(self):
        wire, sender = codec.encode(snapshot(), None, 1)
        unused, receiver = codec.decode(wire, None)
        terminal = snapshot(bot_authority_id=None, battle_result={'winner': 1})
        wire, sender = codec.encode(terminal, sender, 2)
        self.assertFalse(wire['snapshot_delta'])
        restored, receiver = codec.decode(wire, receiver)
        self.assertEqual(terminal, restored)
        later = dict(terminal, server_tick=5)
        wire, unused = codec.encode(later, sender, 3)
        self.assertTrue(wire['snapshot_delta'])
        restored, unused = codec.decode(wire, receiver)
        self.assertEqual(later, restored)

    def test_signed_zero_changes_are_not_lost_in_scalar_or_nested_fields(self):
        first = snapshot()
        first['bots'][0].update(x=0.0, pose=[0.0])
        wire, sender = codec.encode(first, None, 1)
        unused, receiver = codec.decode(wire, None)
        second = copy.deepcopy(first)
        second['bots'][0].update(x=-0.0, pose=[-0.0])
        wire, unused = codec.encode(second, sender, 2)
        restored, unused = codec.decode(transmit(wire), receiver)
        self.assertEqual(-1.0, math.copysign(1.0, restored['bots'][0]['x']))
        self.assertEqual(-1.0, math.copysign(1.0, restored['bots'][0]['pose'][0]))

    def test_reordered_actors_use_full_to_preserve_list_order(self):
        first = snapshot(bots=[{'id': 7}, {'id': 8}])
        wire, sender = codec.encode(first, None, 1)
        unused, receiver = codec.decode(wire, None)
        for rows in ([{'id': 8}, {'id': 7}],
                     [{'id': 6}, {'id': 7}, {'id': 8}]):
            second = snapshot(bots=rows)
            wire, unused = codec.encode(second, sender, 2)
            self.assertFalse(wire['snapshot_delta'])
            restored, unused = codec.decode(wire, receiver)
            self.assertEqual(second, restored)

    def test_bad_patch_does_not_mutate_the_previous_baseline(self):
        first = snapshot()
        wire, sender = codec.encode(first, None, 1)
        unused, receiver = codec.decode(wire, None)
        second = snapshot()
        second['players'][0]['state'] = {'health': 90}
        valid, unused = codec.encode(second, sender, 2)
        failures = [
            {'snapshot_base_seq': 5},
            {'snapshot_seq': 1},
            {'snapshot_base_seq': True},
            {'bots': {'remove': [99]}},
            {'bots': {'add': [{'id': 7}]}},
            {'bots': {'add': [{'id': 9}, {'id': 9}]}},
            {'bots': {'update': [{'id': 7, 'set': {'id': 9}}]}},
            {'bots': {'update': [{'id': 7, 'remove': ['missing']}]}},
            {'bots': {'update': [{'id': 7, 'set': {'x': 1}, 'remove': ['x']}]}},
            {'bots': {'remove': [7], 'update': [{'id': 7, 'set': {'x': 1}}]}},
            {'bots': {'update': [{'id': 7}, {'id': 7}]}},
            {'bots': {'other': []}},
        ]
        for change in failures:
            damaged = copy.deepcopy(valid)
            damaged.update(change)
            with self.subTest(change=change):
                with self.assertRaises(codec.SnapshotDeltaError):
                    codec.decode(damaged, receiver)
                restored, unused = codec.decode(valid, receiver)
                self.assertEqual(second, restored)
        with self.assertRaises(codec.SnapshotDeltaError):
            codec.decode(valid, None)

    def test_mutable_inputs_outputs_and_successive_baselines_do_not_alias(self):
        original = snapshot()
        candidate = copy.deepcopy(original)
        wire, sender = codec.encode(candidate, None, 1)
        candidate['players'][0]['state']['health'] = -1
        self.assertEqual(original['players'], wire['players'])
        full, receiver = codec.decode(wire, None)
        full['players'][0]['state']['health'] = -2
        wire['players'][0]['state']['health'] = -3
        second = snapshot()
        second['bots'][0]['ammo'] = [2, 2]
        delta, sender_next = codec.encode(second, sender, 2)
        restored, receiver_next = codec.decode(delta, receiver)
        self.assertEqual(second, restored)
        restored['players'][0]['state']['health'] = -4
        delta['bots']['update'][0]['set']['ammo'][0] = -5
        second['bots'][0]['ammo'][0] = -6
        third = snapshot()
        third['bots'][0]['ammo'] = [2, 2]
        delta, unused = codec.encode(third, sender_next, 3)
        self.assertEqual({}, delta['bots'])
        restored, unused = codec.decode(delta, receiver_next)
        self.assertEqual(third, restored)
        # Reusing either old baseline still reconstructs its own next state.
        delta, unused = codec.encode(original, sender, 4)
        self.assertEqual({}, delta['players'])
        self.assertEqual({}, delta['bots'])
        restored, unused = codec.decode(delta, receiver)
        self.assertEqual(original, restored)

    def test_plain_complete_snapshot_clears_transport_baseline(self):
        original = snapshot()
        wire, sender = codec.encode(original, None, 1)
        unused, receiver = codec.decode(wire, None)
        restored, next_baseline = codec.decode(original, receiver)
        self.assertEqual(original, restored)
        self.assertIsNone(next_baseline)
        restored['players'][0]['state']['health'] = 0
        self.assertEqual(100, original['players'][0]['state']['health'])
        for metadata in ({'snapshot_seq': 2}, {'snapshot_delta': False},
                         {'snapshot_base_seq': 1}):
            with self.assertRaises(codec.SnapshotDeltaError):
                codec.decode(dict(original, **metadata), receiver)

    def test_json_clone_preserves_scalars_and_normalizes_tuple_containers(self):
        original = snapshot()
        original['players'][0]['plain_json'] = (
            {'text': '\u5766\u514b', 'none': None, 'flag': False,
             'large_int': 2 ** 80, 'real': 1.2345678901234567},
            [-0.0, 1, 'text'])
        expected = transmit(original)
        wire, sender = codec.encode(original, None, 1)
        full, receiver = codec.decode(transmit(wire), None)
        self.assertEqual(expected, full)
        self.assertIsInstance(full['players'][0]['plain_json'], list)
        original['players'][0]['plain_json'][0]['text'] = 'mutated'
        full['players'][0]['plain_json'][1][0] = 0.0
        wire, unused = codec.encode(expected, sender, 2)
        self.assertEqual({}, wire['players'])
        full, unused = codec.decode(wire, receiver)
        self.assertEqual(expected, full)
        self.assertEqual(-1.0, math.copysign(
            1.0, full['players'][0]['plain_json'][1][0]))

    def test_non_json_mutables_fail_locally_without_altering_raw_baseline(self):
        original = snapshot()
        wire, sender = codec.encode(original, None, 1)
        unused, receiver = codec.decode(wire, None)
        for unsupported in ({1, 2}, bytearray(b'mutable')):
            broken = snapshot()
            broken['bots'][0]['bad'] = unsupported
            with self.assertRaises(codec.SnapshotDeltaError):
                codec.encode(broken, sender, 2)
            patch = dict(wire, snapshot_delta=True, snapshot_seq=2,
                         snapshot_base_seq=1, players={}, bots={
                             'update': [{'id': 7, 'set': {'bad': unsupported}}]})
            with self.assertRaises(codec.SnapshotDeltaError):
                codec.decode(patch, receiver)
        valid, unused = codec.encode(original, sender, 2)
        full, unused = codec.decode(valid, receiver)
        self.assertEqual(original, full)

    def test_full_rows_and_sequences_require_unambiguous_transport_identity(self):
        for rows in ([{'id': 1}, {'id': 1}], [{'id': True}], [{}]):
            with self.assertRaises(codec.SnapshotDeltaError):
                codec.encode(snapshot(players=rows), None, 1)
        for sequence in (0, -1, True, 1.0, '1'):
            with self.assertRaises(codec.SnapshotDeltaError):
                codec.encode(snapshot(), None, sequence)
        wire, baseline = codec.encode(snapshot(), None, 2)
        with self.assertRaises(codec.SnapshotDeltaError):
            codec.encode(snapshot(), baseline, 2, force_full=True)
        wire['snapshot_base_seq'] = 1
        with self.assertRaises(codec.SnapshotDeltaError):
            codec.decode(wire, None)

    def test_python27_and_python3_have_lossless_matching_wire_results(self):
        interpreter = os.environ.get('WOT_0922_PY27') or 'python2.7'
        frames = [snapshot(), snapshot(server_tick=5), snapshot(round_id=2)]
        frames[1]['bots'][0]['ammo'] = []
        frames[1]['players'][0]['name'] = '\u5766\u514b'
        frames[1]['players'][0]['state'] = None
        frames[1]['players'][0]['plain_json'] = [2 ** 80, None, False, 1.23456789]
        frames[1]['bots'][0]['x'] = -0.0
        frames[2]['players'] = []
        program = (
            'import imp, json, sys\n'
            'codec = imp.load_source("snapshot_delta_shared", %r)\n'
            'frames = json.loads(sys.stdin.read())\n'
            'sender = receiver = None\n'
            'results = []\n'
            'for sequence, frame in enumerate(frames, 1):\n'
            '    wire, sender = codec.encode(frame, sender, sequence)\n'
            '    full, receiver = codec.decode(json.loads(json.dumps(wire)), receiver)\n'
            '    results.append([wire, full])\n'
            'json.dump(results, sys.stdout)\n'
        ) % str(CODEC_PATH)
        try:
            child = subprocess.Popen(
                [interpreter, '-B', '-c', program], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as error:
            raise unittest.SkipTest('CPython 2.7 unavailable: %s' % error)
        stdout, stderr = child.communicate(json.dumps(frames).encode('utf-8'))
        self.assertEqual(0, child.returncode, stderr.decode('utf-8'))
        expected, sender, receiver = [], None, None
        for sequence, frame in enumerate(frames, 1):
            wire, sender = codec.encode(frame, sender, sequence)
            full, receiver = codec.decode(transmit(wire), receiver)
            self.assertEqual(frame, full)
            expected.append([wire, full])
        self.assertEqual(expected, json.loads(stdout.decode('utf-8')))


if __name__ == '__main__':
    unittest.main()
