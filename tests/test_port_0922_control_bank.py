"""Elapsed banking and armed-burst service in the production update scheduler."""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from tests import test_port_0922_bot_runtime as fixtures


class ControlBankTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.BotRuntimeTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.module = self.fixture.module
        self.runtime = self.module.BotRuntime(
            1, control_seconds=self.module.WORKER_CONTROL_SECONDS,
            baked_graph=fixtures._graph(),
            descriptor_resolver=lambda unused: fixtures._combat_descriptor(),
            adapter_factory=lambda *unused: fixtures._Adapter(),
            ground_probe=lambda *unused: 0.,
            physics_ground_probe=lambda *unused: 0.,
            direction_probe=lambda *unused: {"clear": True, "slope": 0.},
            spawn_resolver=fixtures._spawn_resolver)
        self.runtime.authority_id = 1
        self.runtime.adapter = object()
        self.calls = []
        self.edges = []
        self.after_step = None

        def simulate(step, now, unused_players, unused_neighbours):
            runtime = self.runtime
            start = runtime._sample_time_us
            end = start + max(1, int(round(step * 1000000.0)))
            self.calls.append((step, now, runtime._refresh_control_this_step,
                               runtime._publish_control_this_step))
            for clock in list(runtime._burst_states.values()):
                for edge in clock.advance(step):
                    self.edges.append((start + int(round(edge['due_offset'] * 1000000.0)),
                                       edge['burst_index']))
            if self.after_step:
                self.after_step(len(self.calls))
            runtime._sample_time_us = end
            if runtime._publish_control_this_step:
                return [dict(type='bot_state', sample_time_us=end)]
            return []
        self.runtime._update_once = simulate

    def arm(self, interval, count=4, due=None):
        clock = self.module.burst_mechanics.BurstClock()
        self.assertTrue(clock.start(1, count, interval, 0))
        clock.advance(0.)
        if due is not None:
            clock.time_left = due
        self.runtime._burst_states[1] = clock
        return clock

    def test_201ms_banks_tiny_tail_and_preserves_time_and_control(self):
        messages = self.runtime.update(.201, .201)
        self.assertEqual(1, len(self.calls))
        self.assertAlmostEqual(.2, self.calls[0][0])
        self.assertAlmostEqual(.2, self.calls[0][1])
        self.assertAlmostEqual(.001, self.runtime._accumulator)
        self.assertEqual(200000, messages[-1]['sample_time_us'])
        self.assertEqual(200000, messages[-1]['source_batch_horizon_us'])
        self.runtime.update(.099, .3)
        self.assertAlmostEqual(.1, self.calls[-1][0])
        self.assertAlmostEqual(.3, self.calls[-1][1])
        self.assertEqual(300000, self.runtime._sample_time_us)
        self.assertAlmostEqual(0., self.runtime._accumulator)

    def test_continuous_201_and_299ms_conserve_elapsed_and_one_refresh_per_callback(self):
        consumed = 0.
        now = 0.
        for elapsed in [.201]*100 + [.299]*20:
            now += elapsed
            before = len(self.calls)
            messages = self.runtime.update(elapsed, now)
            current = self.calls[before:]
            self.assertEqual([True]+[False]*(len(current)-1), [c[2] for c in current])
            self.assertTrue(current[-1][3])
            self.assertTrue(all(c[0] <= .200000001 for c in current))
            consumed += sum(c[0] for c in current)
            self.assertAlmostEqual(now, consumed+self.runtime._accumulator)
            self.assertLess(self.runtime._accumulator, .1)
            self.assertEqual([self.runtime._sample_time_us]*len(messages),
                             [m['source_batch_horizon_us'] for m in messages])
        self.assertAlmostEqual(26.08, consumed+self.runtime._accumulator)

    def test_transient_cost_crossing_does_not_lock_full_roster_into_two_slices(self):
        # These injected costs test the scheduling mechanism, not game timing.
        # The legacy drain gate is the old fixed branch's while(elapsed > eps).
        bank_gate = self.runtime._bank_control_step
        outcomes = []
        for drain in (True, False):
            self.runtime._accumulator = 0.
            self.runtime._control_refresh_accumulator = 0.
            self.runtime._sample_time_us = 0
            self.calls[:] = []
            if drain:
                self.runtime._bank_control_step = lambda available: (
                    min(available, .2) if available > 1.e-12 else 0.)
            else:
                self.runtime._bank_control_step = bank_gate
            dt, now, supplied, consumed = .18, 0., 0., 0.
            after_crossing = []
            crossed = False
            for unused in range(250):
                now += dt
                supplied += dt
                before = len(self.calls)
                self.runtime.update(dt, now)
                current = self.calls[before:]
                consumed += sum(call[0] for call in current)
                self.assertAlmostEqual(supplied, consumed+self.runtime._accumulator)
                self.assertLess(self.runtime._accumulator, .100000001)
                self.assertEqual(1, sum(call[2] for call in current))
                transient = .03 if now >= 20. and not crossed else 0.
                if transient:
                    crossed = True
                elif crossed:
                    after_crossing.append(len(current))
                # Fixed control/outside cost plus a cost per full-roster slice.
                dt = .03 + .07 + .08*len(current) + transient
            outcomes.append(after_crossing[-60:])
        self.assertEqual([2]*60, outcomes[0])
        self.assertIn(1, outcomes[1])
        self.assertLess(sum(outcomes[1]), 96)

    def test_burst_only_callbacks_keep_independent_control_phase_then_resume(self):
        clock = self.arm(.02, count=16)
        refresh_times = []
        for frame in range(20):
            before = len(self.calls)
            messages = self.runtime.update(.02, (frame+1)*.02)
            current = self.calls[before:]
            refresh_times.extend(round((frame+1)*.02, 6)
                                 for call in current if call[2])
            self.assertTrue(all(call[3] for call in current))
            self.assertEqual([self.runtime._sample_time_us]*len(messages),
                             [m['source_batch_horizon_us'] for m in messages])
        self.assertFalse(clock.active)
        self.assertEqual(list(range(1, 16)), [edge[1] for edge in self.edges])
        self.assertEqual(list(range(20000, 300001, 20000)),
                         [edge[0] for edge in self.edges])
        self.assertEqual([.1, .2, .3, .4], refresh_times)
        self.assertEqual(400000, self.runtime._sample_time_us)
        self.assertAlmostEqual(0., self.runtime._control_refresh_accumulator)
        for now in (.65, .9):
            before = len(self.calls)
            self.runtime.update(.25, now)
            self.assertEqual(1, sum(call[2] for call in self.calls[before:]))
        self.assertAlmostEqual(0., self.runtime._accumulator)
        self.assertAlmostEqual(0., self.runtime._control_refresh_accumulator)

    def test_burst_due_below_control_threshold_is_serviced_at_entry(self):
        self.arm(.05, due=.05)
        messages = self.runtime.update(.07, .07)
        self.assertEqual([(50000, 1)], self.edges)
        self.assertAlmostEqual(.05, self.calls[0][0])
        self.assertFalse(self.calls[0][2])
        self.assertAlmostEqual(.02, self.runtime._accumulator)
        self.assertTrue(self.calls[-1][3])
        self.assertEqual(50000, messages[-1]['source_batch_horizon_us'])

    def test_new_burst_in_same_callback_services_due_tail_in_order(self):
        def start(count):
            if count == 1:
                self.arm(.02, count=4)
        self.after_step = start
        messages = self.runtime.update(.299, .299)
        self.assertEqual([(220000, 1), (240000, 2), (260000, 3)], self.edges)
        self.assertAlmostEqual(.039, self.runtime._accumulator)
        self.assertEqual([True, False, False, False], [c[2] for c in self.calls])
        self.assertTrue(all(c[3] for c in self.calls))
        self.assertEqual(sorted(m['sample_time_us'] for m in messages),
                         [m['sample_time_us'] for m in messages])
        self.assertEqual([260000]*len(messages),
                         [m['source_batch_horizon_us'] for m in messages])

    def test_zero_due_advances_real_burst_clock_without_spin(self):
        clock = self.arm(.02, count=3, due=0.)
        self.runtime.update(.03, .03)
        self.assertFalse(clock.active)
        self.assertEqual([1, 2], [edge[1] for edge in self.edges])
        self.assertLessEqual(len(self.calls), 3)
        self.assertLess(self.runtime._accumulator, .1)

    def test_long_elapsed_preserves_bounded_slices_and_last_publication(self):
        messages = self.runtime.update(1.099, 1.099)
        self.assertEqual(5, len(self.calls))
        self.assertAlmostEqual(1., sum(c[0] for c in self.calls))
        self.assertAlmostEqual(.099, self.runtime._accumulator)
        self.assertAlmostEqual(1., self.calls[-1][1])
        self.assertTrue(self.calls[-1][3])
        self.assertEqual([1000000]*len(messages),
                         [m['source_batch_horizon_us'] for m in messages])

    def test_close_retains_bank_and_new_round_resets_it(self):
        self.runtime.update(.201, .201)
        self.runtime.close()
        self.assertAlmostEqual(.001, self.runtime._accumulator)
        self.assertAlmostEqual(.001, self.runtime._control_refresh_accumulator)
        self.runtime.battle_start(self.fixture.start)
        self.assertEqual(0., self.runtime._accumulator)
        self.assertEqual(0., self.runtime._control_refresh_accumulator)
        self.assertEqual(0, self.runtime._sample_time_us)
        self.runtime._control_refresh_accumulator = .09
        self.runtime.authority_id = 2
        self.runtime.battle_start(self.fixture.start)
        self.assertEqual(.1, self.runtime._control_refresh_accumulator)

    def test_authority_change_refreshes_first_slice_with_carried_time_or_burst(self):
        self.runtime.battle_start(self.fixture.start)
        self.runtime.update(.09, .09)
        self.assertEqual([], self.calls)
        self.runtime.authority_id = 2
        self.runtime.battle_start(self.fixture.start)
        self.runtime.update(.01, .1)
        self.assertTrue(self.calls[-1][2])
        self.assertAlmostEqual(.1, self.calls[-1][0])
        self.runtime.authority_id = 2
        self.runtime.battle_start(self.fixture.start)
        self.arm(.02)
        self.runtime.update(.02, .12)
        self.assertTrue(self.calls[-1][2])
        # Authority handoff starts a new source sample epoch.
        self.assertEqual((20000, 1), self.edges[-1])

    def test_changed_order_refreshes_burst_slice_once_and_retains_phase(self):
        self.arm(.02, count=16)
        self.runtime.update(.1, .1)
        self.runtime.update(.02, .12)
        self.assertFalse(self.calls[-1][2])
        message = dict(bot_order_revision=1, bot_orders=[{
            'id': 11, 'move_position': (10., 0., 10.)}])
        self.runtime._apply_orders(message)
        self.assertAlmostEqual(.12, self.runtime._control_refresh_accumulator)
        self.runtime._apply_orders(message)
        self.assertAlmostEqual(.12, self.runtime._control_refresh_accumulator)
        self.runtime.update(.02, .14)
        self.assertTrue(self.calls[-1][2])
        self.assertAlmostEqual(.04, self.runtime._control_refresh_accumulator)
        self.runtime.update(.02, .16)
        self.assertFalse(self.calls[-1][2])

    def test_control_deadline_uses_callback_time_and_restores_temporary_scope(self):
        self.runtime._decision_cache[11] = (('local',), 1.1)
        observed = []

        def observe(step, now, unused_players, unused_neighbours):
            observed.append((step, now, self.runtime._control_evaluation_time,
                             self.runtime._visibility_decision_due({'id': 11}, now)))
            return []
        self.runtime._update_once = observe
        self.runtime._run_update_once(.1, 1., None, None, True, True, 1.2)
        self.assertEqual([(.1, 1., 1.2, True)], observed)
        self.assertIsNone(self.runtime._control_evaluation_time)
        self.assertFalse(self.runtime._visibility_decision_due({'id': 11}, 1.))

        def fail(*unused):
            self.assertEqual(1.2, self.runtime._control_evaluation_time)
            raise RuntimeError('local slice failure')
        self.runtime._update_once = fail
        with self.assertRaises(RuntimeError):
            self.runtime._run_update_once(.1, 1., None, None, False, False, 1.2)
        self.assertIsNone(self.runtime._control_evaluation_time)
        self.assertTrue(self.runtime._refresh_control_this_step)
        self.assertTrue(self.runtime._publish_control_this_step)


if __name__ == '__main__':
    unittest.main()
