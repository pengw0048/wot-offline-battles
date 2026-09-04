from __future__ import print_function

import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CLIENT = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT))

from gui.mods.offline_lan_0922 import burst_mechanics
from gui.mods.offline_lan_0922 import gun_mechanics


class BurstMechanicsTests(unittest.TestCase):

    @staticmethod
    def _gun(count=3, interval=0.1):
        return types.SimpleNamespace(
            burst=(count, interval),
            shotDispersionFactors={
                'afterShot': 4.0,
                'afterShotInBurst': 1.25,
            })

    def test_descriptor_count_is_clamped_by_ammo_and_loaded_clip(self):
        self.assertEqual(
            (2, 0.1),
            burst_mechanics.planned_count(self._gun(5), 7, 2))
        self.assertEqual(
            (0, 0.1),
            burst_mechanics.planned_count(self._gun(5), 0, 5))

    def test_every_crossed_interval_produces_a_unique_physical_sequence(self):
        clock = burst_mechanics.BurstClock()
        self.assertTrue(clock.start(41, 5, 0.1, 2))

        first = clock.advance(0.0)
        crossed = clock.advance(0.205)

        self.assertEqual([41], [edge['shot_seq'] for edge in first])
        self.assertEqual([42, 43], [edge['shot_seq'] for edge in crossed])
        self.assertEqual([1, 2], [edge['burst_index'] for edge in crossed])
        self.assertEqual([0.1, 0.2], [
            round(edge['due_offset'], 6) for edge in crossed])
        self.assertAlmostEqual(0.095, clock.time_left)
        self.assertTrue(clock.active)

    def test_last_round_finishes_the_group_at_the_exact_cadence(self):
        clock = burst_mechanics.BurstClock()
        clock.start(9, 3, 0.1, 0)

        edges = clock.advance(0.2)

        self.assertEqual([9, 10, 11], [edge['shot_seq'] for edge in edges])
        self.assertFalse(clock.active)
        self.assertTrue(edges[-1]['final'])
        self.assertEqual(0.0, clock.time_left)

    def test_wire_snapshot_preserves_only_the_unlaunched_tail(self):
        source = burst_mechanics.BurstClock()
        source.start(100, 5, 0.1, 1)
        source.advance(0.22)
        wire = {}
        source.publish(wire)

        restored = burst_mechanics.BurstClock()
        self.assertTrue(restored.restore(wire, 102))

        self.assertEqual((), restored.advance(0.079))
        self.assertEqual(
            [103],
            [edge['shot_seq'] for edge in restored.advance(0.001)])

    def test_cancelled_tail_keeps_the_group_contract_already_on_the_wire(self):
        clock = burst_mechanics.BurstClock()
        clock.start(20, 5, 0.1, 0)
        clock.advance(0.0)

        self.assertTrue(clock.cancel(1))
        wire = {}
        clock.publish(wire)

        self.assertFalse(wire['burst_active'])
        self.assertEqual(20, wire['burst_group_seq'])
        self.assertEqual(5, wire['burst_count'])
        self.assertEqual(1, wire['burst_next_index'])
        restored = burst_mechanics.BurstClock()
        self.assertTrue(restored.restore(wire, 20))
        self.assertEqual((), restored.advance(1.0))

    def test_incomplete_or_sequence_inconsistent_wire_state_is_rejected(self):
        clock = burst_mechanics.BurstClock()
        with self.assertRaises(ValueError):
            clock.restore({'burst_active': True}, 1)

        source = burst_mechanics.BurstClock()
        source.start(20, 3, 0.1, 0)
        source.advance(0.0)
        wire = {}
        source.publish(wire)
        with self.assertRaises(ValueError):
            clock.restore(wire, 99)

    def test_intra_and_final_rounds_use_distinct_bloom_terms(self):
        gun = self._gun()
        self.assertEqual(
            1.25, burst_mechanics.after_shot_factor(gun, False))
        self.assertEqual(
            4.0, burst_mechanics.after_shot_factor(gun, True))

    def test_multi_round_zero_interval_is_not_silently_accepted(self):
        with self.assertRaises(ValueError):
            burst_mechanics.descriptor_burst(self._gun(3, 0.0))

    def test_player_gun_debits_each_round_and_reloads_only_after_final(self):
        descriptor = types.SimpleNamespace(
            gun=types.SimpleNamespace(
                shots=({'shell': {'compactDescr': 7}},),
                shotDispersionAngle=0.1,
                shotDispersionFactors={
                    'afterShot': 4.0, 'afterShotInBurst': 1.0,
                    'turretRotation': 0.0},
                aimingTime=1.0, reloadTime=4.0, clip=(5, 2.0),
                burst=(3, 0.1), maxAmmo=10),
            chassis={'shotDispersionFactors': (0.0, 0.0)},
            turret={'maxAmmo': 10}, maxAmmo=10,
            activeGunShotIndex=0)
        state = gun_mechanics.GunState(
            descriptor, ammo_layout={7: 10})
        state.clip = 5
        state.reload_time = 0.0
        state.reload_duration = 4.0

        self.assertTrue(state.begin_burst(3))
        self.assertTrue(state.commit_burst_round(False))
        first_dispersion = state.dispersion
        self.assertEqual(9, state.ammo[0])
        self.assertEqual(4, state.clip)
        self.assertEqual(0.0, state.reload_time)
        self.assertFalse(state.can_fire())

        self.assertTrue(state.commit_burst_round(False))
        self.assertGreater(state.dispersion, first_dispersion)
        self.assertEqual(0.0, state.reload_time)
        self.assertTrue(state.commit_burst_round(True))
        self.assertEqual(7, state.ammo[0])
        self.assertEqual(2, state.clip)
        self.assertEqual(state.clip_reload, state.reload_time)


if __name__ == '__main__':
    unittest.main()
