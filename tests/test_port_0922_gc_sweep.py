"""The per-round cycle collection, and proof of where the cycle is not.

BigWorld disables CPython's cyclic collector at startup, so in this runtime a
reference cycle is a permanent leak.  Report 20260910-045317 measured 348581
and 333240 unreachable objects per round in the hidden worker against 4832 and
3010 in the visible client on the same machine, and one collect per round
turned the worker's round_start census from 1.14M -> 2.87M over six rounds
into a flat 1133959 -> 1033952 -> 1044656.
"""

import gc
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLIENT_ROOT = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gui.mods.offline_lan_0922 import gc_sweep


class _Sink(object):
    def __init__(self):
        self.text = ''

    def write(self, value):
        self.text += value


class GcSweepTest(unittest.TestCase):
    def setUp(self):
        self._enabled = gc.isenabled()
        self._stdout = sys.stdout
        self.addCleanup(setattr, sys, 'stdout', self._stdout)

    def tearDown(self):
        # The engine's choice about the collector is not ours to change.
        self.assertEqual(self._enabled, gc.isenabled())

    def _leak_cycles(self, count):
        for index in range(count):
            record = {'id': index, 'rows': []}
            holder = [record]
            record['self'] = holder

    def test_a_cycle_is_collected_and_reported(self):
        gc.collect()
        self._leak_cycles(500)
        sys.stdout = _Sink()
        collected = gc_sweep.sweep('round_end', 4)
        text = sys.stdout.text
        sys.stdout = self._stdout
        self.assertGreater(collected, 0)
        self.assertIn('PYSWEEP phase=round_end round=4', text)
        self.assertIn('collected=%d' % collected, text)
        self.assertIn('elapsed_ms=', text)
        self.assertIn('gc_enabled=', text)

    def test_it_never_enables_the_collector(self):
        # Enabling it would change the engine's decision for every frame
        # rather than once per round.
        sys.stdout = _Sink()
        gc_sweep.sweep('round_end', 1)
        sys.stdout = self._stdout
        self.assertEqual(self._enabled, gc.isenabled())

    def test_a_broken_collector_never_raises_into_the_round(self):
        original = gc_sweep.gc.collect
        gc_sweep.gc.collect = lambda *args: (_ for _ in ()).throw(
            RuntimeError('no collector'))
        self.addCleanup(setattr, gc_sweep.gc, 'collect', original)
        self.assertEqual(-1, gc_sweep.sweep('round_end', 1))


class BotUpdateCreatesNoCyclesTest(unittest.TestCase):
    """Where the leak is *not*, recorded so the search does not repeat.

    200 `BotRuntime.update` calls with four Bots and a human player produce
    zero cycles, so the Bot update path is not the source of the 348581
    unreachable objects a real worker round produces.  If this ever starts
    failing, someone has just written the cycle.
    """

    def test_two_hundred_updates_create_no_reference_cycles(self):
        import test_port_0922_bot_runtime as driver
        import effective_params_fixture as fixture
        import contextlib
        import io as text_io

        case = driver.BotRuntimeTests('run')
        case.setUp()
        runtime = case._roster_runtime()
        player = fixture.wire_player(5)
        player.setdefault('team', 1)
        player.setdefault('position', [0.0, 0.0, 0.0])
        player.setdefault('yaw', 0.0)

        gc.collect()
        was_enabled = gc.isenabled()
        gc.disable()                      # match the game
        try:
            sink = text_io.StringIO()
            with contextlib.redirect_stdout(sink):
                for tick in range(200):
                    runtime.update(0.05, 1.0 + tick * 0.05, [player])
            cycles = gc.collect()
        finally:
            if was_enabled:
                gc.enable()
        self.assertEqual(
            0, cycles,
            'the Bot update path created %d cyclic objects; it used to create '
            'none, so this is a new leak' % cycles)


if __name__ == '__main__':
    unittest.main()
