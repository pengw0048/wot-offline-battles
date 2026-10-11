"""Wreck push terminal reasons and movement beyond the original death pose."""
import contextlib
import io
import json
import unittest

import test_port_0922_bot_runtime as bots


class WreckPushDiagnosticTests(unittest.TestCase):
    setUp = bots.ShovedWreckTests.setUp
    tearDown = bots.ShovedWreckTests.tearDown
    _runtime = bots.ShovedWreckTests._runtime
    _wreck = bots.ShovedWreckTests._wreck

    @staticmethod
    def records(output):
        return [json.loads(line.split('WRECK PUSH ', 1)[1])
                for line in output.getvalue().splitlines()
                if 'WRECK PUSH ' in line]

    def test_repeated_push_uses_current_pose_and_stops_at_a_new_world_wall(self):
        runtime = self._runtime()
        state = self._wreck(runtime)
        starts = []
        wall_z = 20.0

        def clear(position, yaw, speed, target, distance, width):
            starts.append(position)
            return position[2] + distance < wall_z

        runtime._clear = clear
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            for unused in range(120):
                runtime._apply_wreck_contact_response(state, {
                    'delta_velocity': (0.0, 2.0),
                    'correction': (0.0, 0.05)}, 0.1)
        self.assertGreater(state['z'], 10.0)
        self.assertLess(state['z'], wall_z)
        self.assertGreater(max(pos[2] for pos in starts), 10.0)
        reasons = {row['reason'] for row in self.records(output)}
        self.assertIn('moved', reasons)
        self.assertIn('horizontal_block', reasons)
        # Every declined attempt keeps the actual last pose, rather than
        # checking a permanently cached death/spawn origin.
        self.assertEqual(starts[-1], (state['x'], state['y'], state['z']))

    def test_world_block_and_support_failure_have_distinct_terminal_records(self):
        cases = ((False, 0.0, 'horizontal_block'),
                 (True, None, 'missing_support'),
                 (True, -40.0, 'support_step'))
        for clear, ground, expected in cases:
            with self.subTest(reason=expected):
                runtime = self._runtime(clear=clear, ground=ground)
                state = self._wreck(runtime)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    moved = runtime._apply_wreck_contact_response(state, {
                        'delta_velocity': (0.0, 2.0),
                        'correction': (0.0, 0.05)}, 0.1)
                self.assertFalse(moved)
                row, = self.records(output)
                self.assertEqual(expected, row['reason'])
                self.assertEqual(row['before'], row['after'])
                self.assertEqual([0.0, 0.0], row['remaining_push'])

    def test_track_hold_is_bounded_and_does_not_move_the_wreck(self):
        runtime = self._runtime()
        state = self._wreck(runtime)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            for unused in range(300):
                runtime._apply_wreck_contact_response(state, {
                    'delta_velocity': (0.0, 0.001),
                    'correction': (0.0, 0.01)}, 1.0 / 30.0)
        rows = self.records(output)
        self.assertGreaterEqual(len(rows), 4)
        self.assertLessEqual(len(rows), 6)
        self.assertEqual({'track_hold'}, {row['reason'] for row in rows})
        self.assertEqual(0.0, state['z'])


class SustainedWreckDriveTests(unittest.TestCase):
    setUp = bots.WreckPushMassTests.setUp
    tearDown = bots.WreckPushMassTests.tearDown
    _travel = bots.WreckPushMassTests._travel

    def test_powered_contact_continues_after_the_first_ten_metres(self):
        with contextlib.redirect_stdout(io.StringIO()):
            first_six_seconds = self._travel(68000.0, 1050.0, 32000.0, ticks=180)
            twelve_seconds = self._travel(68000.0, 1050.0, 32000.0, ticks=360)
        self.assertGreater(first_six_seconds, 10.0)
        self.assertGreater(twelve_seconds, first_six_seconds + 10.0)
