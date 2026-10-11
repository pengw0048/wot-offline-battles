import copy
import json
import math
import os
import subprocess
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLIENT_ROOT = ROOT / 'src' / 'res' / 'scripts' / 'client'
CODEC_PATH = (CLIENT_ROOT / 'gui' / 'mods' / 'offline_lan_0922' /
              'bot_state_codec.py')
sys.path.insert(0, str(CLIENT_ROOT))

from gui.mods.offline_lan_0922 import bot_state_codec as codec


def _contract(name, kind, tags):
    return {
        'name': name, 'kind': kind, 'id': 1042, 'compactDescr': 34817,
        'tags': list(tags), 'reuseCount': 0, 'cooldownSeconds': 90.0,
        'autoactivate': kind == 'extinguisher',
        'fireStartingChanceFactor': 1.0, 'repairAll': kind != 'extinguisher',
        'bonusValue': 0.0, 'crewLevelIncrease': 0.0, 'enginePowerFactor': 1.0,
        'turretRotationSpeedFactor': 1.0, 'engineHpLossPerSecond': 0.0,
        'autoReactionSeconds': 1.5,
    }


CONTRACTS = (
    _contract('autoExtinguishers', 'extinguisher', ('deluxe', 'equipment')),
    _contract('largeMedkit', 'medkit', ('equipment', 'medkit')),
    _contract('largeRepairkit', 'repairkit', ('equipment', 'repairkit')),
)
STATIC = {'equipment_contracts': CONTRACTS}


def _equipment_snapshot(uses=1, cooldown=0.0, active=False,
                        auto=None, ai=None):
    return {
        'usesLeft': uses, 'cooldownTimeLeft': cooldown, 'active': active,
        'autoPendingElapsed': auto, 'aiPendingElapsed': ai,
    }


def _bot_state(**overrides):
    state = {
        'id': 17, 'x': 125.6312, 'y': 6.5041, 'z': -67.8842,
        'yaw': -0.77258, 'pitch': 0.01076, 'roll': -0.00412,
        'aim_yaw': -0.63482, 'gun_pitch': 0.04117, 'speed': 8.4213,
        'movement_dir': 1.0, 'rotation_dir': 0.0, 'fire_seq': 7,
        'shell_index': 0, 'next_shell_index': 0,
        'ammo_remaining': [38, 12, 6], 'ammo_reload_pending': False,
        'reload_time': 4.2317, 'reload_duration': 9.4,
        'clip': 0, 'clip_size': 1,
        'burst_active': False, 'burst_group_seq': 7, 'burst_count': 1,
        'burst_next_index': 0, 'burst_interval': 0.0, 'burst_time_left': 0.0,
        'burst_shell_index': 0,
        'siege_state': 0, 'siege_time_left_ms': 0,
        'siege_transition_total_ms': 0,
        'health': 1115, 'alive': True,
        'critical': {
            'devices': [], 'destroyed': [], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False, 'events': [],
        },
        'combat_base_revision': 12, 'combat_seq': 41,
        'combat_fire_elapsed': 0.0, 'combat_fire_timer': 0.0,
        'death_reason': 0, 'display_health': 1115, 'world_pose': True,
        'stun_end_server_time_ms': 0,
        'shot_yaw': -0.63482, 'shot_pitch': 0.04117,
        'equipment_states': [_equipment_snapshot() for _ in range(3)],
    }
    state.update(overrides)
    return state


class BotStateCodecTest(unittest.TestCase):

    def _full_snapshot_bot(self, bot_id=17):
        states = [dict(_equipment_snapshot(), equipment=copy.deepcopy(value))
                  for value in CONTRACTS]
        return _bot_state(id=bot_id, vehicle='ussr:R11_MS-1', team=1,
                          slot=bot_id % 15, equipment_states=states)

    def test_snapshot_table_round_trip_preserves_dynamic_ledger_and_identity(self):
        first = self._full_snapshot_bot()
        first['equipment_states'][0].update(
            usesLeft=-1, cooldownTimeLeft=13.75, active=True,
            autoPendingElapsed=0.75, aiPendingElapsed=None)
        first['equipment_states'][1].update(
            usesLeft=0, aiPendingElapsed=4.5)
        rows = [first, self._full_snapshot_bot(18)]
        before = copy.deepcopy(rows)
        projected, contracts = codec.encode_snapshot_equipment(rows)
        self.assertEqual(3, len(contracts))
        wire = json.loads(json.dumps({'bots': projected, 'table': contracts}))
        restored = [codec.decode_snapshot_equipment(row, wire['table'])
                    for row in wire['bots']]
        self.assertEqual(before, restored)
        self.assertEqual(before, rows)
        restored[0]['equipment_states'][0]['equipment']['tags'].append('new')
        restored[0]['equipment_states'][0]['equipment']['bonusValue'] = 99
        self.assertEqual(before[1], restored[1])
        self.assertEqual(CONTRACTS[0], wire['table'][0])
        self.assertEqual(before, rows)

    def test_snapshot_table_compares_full_contract_not_equipment_name(self):
        rows = [self._full_snapshot_bot(value) for value in (17, 18, 19)]
        rows[1]['equipment_states'][0]['equipment']['bonusValue'] = 0.25
        rows[2]['equipment_states'][0]['equipment']['tags'].append('special')
        projected, contracts = codec.encode_snapshot_equipment(rows)
        self.assertEqual(5, len(contracts))
        self.assertEqual(rows, [codec.decode_snapshot_equipment(row, contracts)
                               for row in projected])

    def test_snapshot_full_and_empty_rows_need_no_table(self):
        for row in (self._full_snapshot_bot(), _bot_state(equipment_states=[]),
                    {'id': 17}):
            self.assertIs(row, codec.decode_snapshot_equipment(row, None))
        self.assertEqual(([], []), codec.encode_snapshot_equipment([]))

    def test_snapshot_bad_reference_retains_only_matching_prior_contract(self):
        previous = self._full_snapshot_bot()
        rows, contracts = codec.encode_snapshot_equipment([previous])
        current = copy.deepcopy(rows[0])
        current.update(health=0, alive=False, x=17.0)
        current['equipment_states'][0].update(equipment=99, usesLeft=0,
                                              cooldownTimeLeft=90.0)
        restored = codec.decode_snapshot_equipment(current, contracts, previous)
        self.assertFalse(restored['alive'])
        self.assertEqual(0, restored['health'])
        self.assertEqual(17.0, restored['x'])
        self.assertEqual(0, restored['equipment_states'][0]['usesLeft'])
        self.assertEqual(90.0,
                         restored['equipment_states'][0]['cooldownTimeLeft'])
        self.assertEqual(CONTRACTS[0],
                         restored['equipment_states'][0]['equipment'])
        for field, value in (('id', 99), ('vehicle', 'another'), ('team', 2),
                             ('slot', 9)):
            with self.assertRaises(codec.BotStateCodecError):
                codec.decode_snapshot_equipment(
                    dict(current, **{field: value}), contracts, previous)
        with self.assertRaises(codec.BotStateCodecError):
            codec.decode_snapshot_equipment(
                dict(current, equipment_states=current['equipment_states'][:1]),
                contracts, previous)

    def test_snapshot_rejects_unavailable_or_noninteger_references_locally(self):
        rows, contracts = codec.encode_snapshot_equipment(
            [self._full_snapshot_bot()])
        for reference in (True, False, -1, 90, 0.0, '0', None):
            row = copy.deepcopy(rows[0])
            row['equipment_states'][0]['equipment'] = reference
            with self.assertRaises(codec.BotStateCodecError):
                codec.decode_snapshot_equipment(row, contracts)
        for table in (None, {}, [], [None], contracts * 31):
            with self.assertRaises(codec.BotStateCodecError):
                codec.decode_snapshot_equipment(rows[0], table)

    def test_service_brake_intent_survives_both_wire_states(self):
        for active in (True,False):
            state = _bot_state(service_brake=active)
            decoded = codec.decode_row(codec.encode_row(state), STATIC)
            self.assertIs(active, decoded['service_brake'])

    def test_round_trip_preserves_every_contract_field(self):
        state = _bot_state()
        decoded = codec.decode_row(codec.encode_row(state), STATIC)
        for name in ('id', 'fire_seq', 'shell_index', 'next_shell_index',
                     'clip', 'clip_size', 'burst_group_seq', 'burst_count',
                     'burst_next_index', 'burst_shell_index', 'siege_state',
                     'siege_time_left_ms', 'siege_transition_total_ms',
                     'health', 'display_health', 'combat_base_revision',
                     'combat_seq', 'death_reason',
                     'stun_end_server_time_ms'):
            self.assertEqual(decoded[name], state[name], name)
        for name in ('x', 'y', 'z', 'yaw', 'pitch', 'roll', 'aim_yaw',
                     'gun_pitch', 'speed', 'reload_time', 'reload_duration',
                     'burst_interval', 'burst_time_left',
                     'combat_fire_elapsed', 'combat_fire_timer',
                     'shot_yaw', 'shot_pitch'):
            self.assertAlmostEqual(decoded[name], state[name], places=5, msg=name)
        self.assertEqual(decoded['ammo_remaining'], [38, 12, 6])
        self.assertIs(decoded['alive'], True)
        self.assertIs(decoded['world_pose'], True)
        self.assertEqual(decoded['movement_dir'], 1)
        self.assertEqual(decoded['rotation_dir'], 0)

    def test_encode_is_stable_across_a_decode_round_trip(self):
        row = codec.encode_row(_bot_state())
        self.assertEqual(codec.encode_row(codec.decode_row(row, STATIC)), row)

    def test_fixed_point_matches_the_previous_rounded_contract(self):
        state = _bot_state(x=1.00005, yaw=0.123455, speed=-0.00005)
        decoded = codec.decode_row(codec.encode_row(state), STATIC)
        for name, places in (('x', 4), ('yaw', 5), ('speed', 4)):
            self.assertLessEqual(
                abs(decoded[name] - state[name]), 10.0 ** -places, name)

    def test_out_of_contract_values_are_clamped_not_rejected(self):
        state = _bot_state(x=9000.0, z=-9000.0, gun_pitch=3.0, speed=500.0,
                           pitch=-5.0)
        decoded = codec.decode_row(codec.encode_row(state), STATIC)
        self.assertEqual(decoded['x'], 2000.0)
        self.assertEqual(decoded['z'], -2000.0)
        self.assertEqual(decoded['gun_pitch'], 1.2)
        self.assertEqual(decoded['speed'], 80.0)
        self.assertAlmostEqual(decoded['pitch'], 2 * math.pi - 5.0, places=5)

    def test_shot_angles_are_an_atomic_optional_pair(self):
        state = _bot_state()
        del state['shot_yaw']
        del state['shot_pitch']
        decoded = codec.decode_row(codec.encode_row(state), STATIC)
        self.assertNotIn('shot_yaw', decoded)
        self.assertNotIn('shot_pitch', decoded)

    def test_shot_yaw_is_wrapped_into_the_canonical_turn(self):
        decoded = codec.decode_row(
            codec.encode_row(_bot_state(shot_yaw=math.pi * 3.0)), STATIC)
        self.assertAlmostEqual(decoded['shot_yaw'], -math.pi, places=4)

    def test_damaged_devices_rebuild_names_states_and_maxima(self):
        state = _bot_state(critical={
            'devices': [
                {'name': 'leftTrackHealth', 'hp': 0.0, 'max_hp': 160.0,
                 'state': 'destroyed'},
                {'name': 'engineHealth', 'hp': 88.125, 'max_hp': 160.0,
                 'state': 'critical'},
            ],
            'destroyed': ['leftTrackHealth'],
            'crew_ko': ['gunner1', 'loader2'],
            'crew_roster': ['commander', 'driver', 'gunner1', 'loader2'],
            'fire': True, 'ammo_rack_death': False, 'events': [],
        })
        critical = codec.decode_row(
            codec.encode_row(state), STATIC)['critical']
        by_name = dict((row['name'], row) for row in critical['devices'])
        self.assertEqual(sorted(by_name), ['engineHealth', 'leftTrackHealth'])
        self.assertEqual(by_name['engineHealth']['state'], 'critical')
        self.assertAlmostEqual(by_name['engineHealth']['hp'], 88.125)
        self.assertEqual(by_name['engineHealth']['max_hp'], 160.0)
        self.assertEqual(by_name['leftTrackHealth']['state'], 'destroyed')
        self.assertEqual(critical['destroyed'], ['leftTrackHealth'])
        self.assertEqual(critical['crew_ko'], ['gunner1', 'loader2'])
        self.assertIs(critical['fire'], True)
        self.assertIs(critical['ammo_rack_death'], False)
        self.assertEqual(critical['events'], [])
        self.assertEqual(
            ['commander', 'driver', 'gunner1', 'loader2'],
            critical['crew_roster'])

    def test_equipment_rejoins_the_round_contract(self):
        state = _bot_state(equipment_states=[
            _equipment_snapshot(uses=0, cooldown=12.5, active=True,
                                auto=1.25, ai=None),
            _equipment_snapshot(),
            _equipment_snapshot(uses=2),
        ])
        snapshots = codec.decode_row(
            codec.encode_row(state), STATIC)['equipment_states']
        self.assertEqual(len(snapshots), 3)
        self.assertEqual(snapshots[0]['equipment'], CONTRACTS[0])
        self.assertEqual(snapshots[0]['usesLeft'], 0)
        self.assertAlmostEqual(snapshots[0]['cooldownTimeLeft'], 12.5)
        self.assertIs(snapshots[0]['active'], True)
        self.assertAlmostEqual(snapshots[0]['autoPendingElapsed'], 1.25)
        self.assertIsNone(snapshots[0]['aiPendingElapsed'])
        self.assertEqual(snapshots[2]['usesLeft'], 2)

    def test_dead_bot_row_keeps_the_same_column_contract(self):
        state = _bot_state(alive=False, health=0, display_health=0,
                           death_reason=1, speed=0.0)
        decoded = codec.decode_row(codec.encode_row(state), STATIC)
        self.assertIs(decoded['alive'], False)
        self.assertEqual(decoded['health'], 0)
        self.assertEqual(decoded['death_reason'], 1)

    def test_absent_groups_stay_absent_so_the_server_keeps_its_state(self):
        """A row must still express "I published no burst, clip or Siege".

        The mapping form left the whole group out and the server kept the
        state it had already admitted. A positional row always has the
        columns, so the presence bits carry that distinction; without them a
        Bot with no burst clock would publish an empty magazine and its shots
        would never be admitted.
        """
        state = _bot_state()
        for name in ('burst_active', 'burst_group_seq', 'burst_count',
                     'burst_next_index', 'burst_shell_index',
                     'burst_interval', 'burst_time_left',
                     'clip', 'clip_size', 'siege_state',
                     'siege_time_left_ms', 'siege_transition_total_ms'):
            del state[name]
        decoded = codec.decode_row(codec.encode_row(state), STATIC)
        for name in ('burst_active', 'burst_group_seq', 'clip', 'clip_size',
                     'siege_state', 'siege_time_left_ms'):
            self.assertNotIn(name, decoded)
        self.assertEqual(decoded['fire_seq'], state['fire_seq'])
        self.assertEqual(decoded['health'], state['health'])

    def test_a_half_published_group_is_refused(self):
        state = _bot_state()
        del state['clip_size']
        with self.assertRaises(codec.BotStateCodecError):
            codec.encode_row(state)

    def test_truncated_and_overlong_rows_are_refused(self):
        row = codec.encode_row(_bot_state())
        with self.assertRaises(codec.BotStateCodecError):
            codec.decode_row(row[:-1], STATIC)
        with self.assertRaises(codec.BotStateCodecError):
            codec.decode_row(list(row) + [0], STATIC)

    def test_non_integer_columns_are_refused(self):
        row = codec.encode_row(_bot_state())
        row[2] = 125.6312
        with self.assertRaises(codec.BotStateCodecError):
            codec.decode_row(row, STATIC)

    def test_consumable_count_must_match_the_round_manifest(self):
        state = _bot_state(equipment_states=[_equipment_snapshot()])
        with self.assertRaises(codec.BotStateCodecError):
            codec.decode_row(codec.encode_row(state), STATIC)

    def test_a_full_lineup_publication_stays_small(self):
        rows = [codec.encode_row(_bot_state(id=index))
                for index in range(1, 30)]
        encoded = json.dumps(
            {'type': 'bot_state', 'round_id': 1, 'rows': rows},
            separators=(',', ':')).encode('utf-8')
        # The JSON object form of this publication was about 67 KB, which
        # saturated the server's worker ingress in a 15v15 round.
        self.assertLess(len(encoded), 8 * 1024)

    def test_worker_python_27_encodes_the_same_row(self):
        """The worker runs CPython 2.7; both peers must agree bit for bit."""
        interpreter = os.environ.get('WOT_0922_PY27') or 'python2.7'
        # The state travels as JSON so the 2.7 child never imports this
        # module; the shared codec is the only thing both sides load.
        # ``gui`` and ``gui.mods`` are namespace directories supplied by the
        # game package. Supply those packages so the codec's real local
        # dependencies load under 2.7 as they do inside the game.
        program = (
            'import json, os, sys, types\n'
            'root = %r\n'
            'for name in ("gui", "gui.mods"):\n'
            '    package = types.ModuleType(name)\n'
            '    package.__path__ = [os.path.join(root, *name.split("."))]\n'
            '    sys.modules[name] = package\n'
            'from gui.mods.offline_lan_0922 import bot_state_codec as codec\n'
            'state = json.loads(sys.stdin.read())\n'
            'json.dump(codec.encode_row(state), sys.stdout)\n'
        ) % (str(CLIENT_ROOT),)
        states = [_bot_state(), _bot_state(x=1.00005, yaw=0.123455,
                                          speed=-0.00005, roll=-0.000005)]
        for state in states:
            try:
                child = subprocess.Popen(
                    [interpreter, '-c', program], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except OSError as error:
                raise unittest.SkipTest(
                    'CPython 2.7 is unavailable for the worker parity '
                    'check: %s' % (error,))
            stdout, stderr = child.communicate(
                json.dumps(state).encode('utf-8'))
            self.assertEqual(child.returncode, 0, stderr.decode('utf-8'))
            self.assertEqual(
                json.loads(stdout.decode('utf-8')), codec.encode_row(state))


if __name__ == '__main__':
    unittest.main()
