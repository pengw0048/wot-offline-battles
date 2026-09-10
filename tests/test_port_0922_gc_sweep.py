"""Round-boundary collection and a bounded update-fixture regression."""

import gc
import os
import sys
import types
from pathlib import Path
import unittest
from unittest import mock

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
        self.assertIn('unreachable=%d' % collected, text)
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
        sys.stdout = _Sink()
        self.assertEqual(-1, gc_sweep.sweep('round_end', 1))
        self.assertIn('PYSWEEP phase=round_end round=1 error=collection_failed',
                      sys.stdout.text)

    def test_sweep_preserves_an_existing_saveall_session(self):
        existing = {'owned_by_debugger': True}
        garbage = [existing]
        collect = mock.Mock(side_effect=lambda: garbage.append({}) or 1)
        collector = types.SimpleNamespace(
            DEBUG_SAVEALL=gc.DEBUG_SAVEALL, garbage=garbage,
            get_debug=lambda: gc.DEBUG_SAVEALL, collect=collect,
            isenabled=lambda: False)
        sys.stdout = _Sink()
        with mock.patch.object(gc_sweep, 'gc', collector):
            self.assertEqual(-1, gc_sweep.sweep('round_end', 2))
        collect.assert_not_called()
        self.assertEqual([existing], garbage)
        self.assertIn('skipped=debug_saveall', sys.stdout.text)


class BotUpdateCollectionRegressionTest(unittest.TestCase):
    """Check unreachable garbage left by one controlled update fixture.

    The Python 3 fixture exercises 200 stationary updates with four Bots and a
    human player on the non-fixed-control path while retaining the runtime.
    A zero result excludes neither live reference cycles nor behavior absent
    from this fake scene, including native calls and worker publication. It
    does not rule out the production update path.

    Run it in a subprocess because BotRuntime changes process-global logging
    state that unrelated loadout tests also exercise.
    """

    DRIVER = r"""
import contextlib, gc, io, sys
import test_port_0922_bot_runtime as driver
import effective_params_fixture as fixture

case = driver.BotRuntimeTests('run')
case.setUp()
runtime = case._roster_runtime()
player = fixture.wire_player(5)
player.setdefault('team', 1)
player.setdefault('position', [0.0, 0.0, 0.0])
player.setdefault('yaw', 0.0)

gc.collect()
gc.disable()
sink = io.StringIO()
with contextlib.redirect_stdout(sink):
    for tick in range(200):
        runtime.update(0.05, 1.0 + tick * 0.05, [player])
cycles = gc.collect()
sys.stderr.write('CYCLES=%d\n' % cycles)
"""

    def test_two_hundred_fixture_updates_leave_no_unreachable_objects(self):
        import subprocess

        root = Path(__file__).resolve().parents[1]
        environment = dict(os.environ)
        environment['PYTHONDONTWRITEBYTECODE'] = '1'
        environment['PYTHONPATH'] = os.pathsep.join((
            str(root / 'src' / 'res' / 'scripts' / 'client'),
            str(root / 'tests'),
        ))
        finished = subprocess.run(
            [sys.executable, '-c', self.DRIVER],
            cwd=str(root), env=environment, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=300)
        report = finished.stderr.decode('utf-8', 'replace')
        self.assertEqual(
            0, finished.returncode,
            'the Bot update driver failed:\n%s' % report[-2000:])
        marker = [line for line in report.splitlines()
                  if line.startswith('CYCLES=')]
        self.assertTrue(marker, 'driver reported no cycle count:\n%s'
                        % report[-2000:])
        cycles = int(marker[-1].split('=', 1)[1])
        self.assertEqual(
            0, cycles,
            'the controlled update fixture left %d unreachable objects' % cycles)


if __name__ == '__main__':
    unittest.main()
