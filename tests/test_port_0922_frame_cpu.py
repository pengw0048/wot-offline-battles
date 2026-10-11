"""Separate callback CPU from elapsed wall time without steering simulation."""
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'res' / 'scripts' / 'client'))
from gui.mods.offline_lan_0922 import native_math
from gui.mods.offline_lan_0922.battle_runtime import _FrameDiagnostics


class FrameCpuTests(unittest.TestCase):
    def test_wall_and_thread_cpu_are_reported_for_the_same_callback(self):
        wall, cpu, output = [0.0], [1.0], []
        diagnostic = _FrameDiagnostics(
            clock=lambda: wall[0], cpu_clock=lambda: cpu[0],
            writer=output.append, window_seconds=0.25)
        frame = diagnostic.begin(0.0, 0.0)
        wall[0], cpu[0] = 0.2, 1.05
        diagnostic.finish(frame, 0.0, 0.1, 0.1, {}, {}, {'role': 'worker'})
        cpu[0] = 7.0  # CPU between callbacks must not enter the prior sample.
        diagnostic.begin(0.3, 0.3)
        diagnostic.flush()
        measured = diagnostic.snapshot()['main_thread_cpu']
        self.assertEqual(measured['samples'], 1)
        self.assertAlmostEqual(measured['avg_ms'], 50.0)
        self.assertAlmostEqual(measured['matched_wall_avg_ms'], 200.0)
        self.assertIn('thread_cpu samples=1/1 cpu_ms_avg_max=50.000/50.000', output[0])
        self.assertIn('"thread_cpu_ms":50.0', output[0])

    def test_unavailable_regressing_and_coarse_clocks_do_not_hide_wall_time(self):
        for end_cpu in (None, float('nan'), float('inf'), 0.5, RuntimeError('clock')):
            wall, cpu = [0.0], [1.0]

            def read_cpu():
                if isinstance(cpu[0], Exception):
                    raise cpu[0]
                return cpu[0]

            diagnostic = _FrameDiagnostics(
                clock=lambda: wall[0], cpu_clock=read_cpu, writer=lambda value: None)
            frame = diagnostic.begin(0.0, 0.0)
            wall[0], cpu[0] = 0.01, end_cpu
            diagnostic.finish(frame, 0.0, 0.1, 0.1, {}, {}, {})
            diagnostic.begin(0.3, 0.3)
            diagnostic.flush()
            self.assertTrue(diagnostic.enabled)
            self.assertEqual(diagnostic.snapshot()['samples'], 1)
            self.assertIsNone(diagnostic.snapshot()['main_thread_cpu']['avg_ms'])
        # Retain an OS CPU tick larger than this short wall interval. Clamping
        # it to wall time would corrupt the aggregate CPU evidence.
        cpu[:] = [1.0]
        frame = diagnostic.begin(0.4, 0.1)
        wall[0], cpu[0] = 0.41, 1.016
        diagnostic.finish(frame, 0.4, 0.1, 0.1, {}, {}, {})
        self.assertAlmostEqual(diagnostic._pending['thread_cpu'], 0.016)
        self.assertAlmostEqual(diagnostic._pending['exec'], 0.01)

    def test_cpu_clock_failure_does_not_disable_native_computation(self):
        backend = types.SimpleNamespace(thread_cpu_seconds=mock.Mock(side_effect=RuntimeError('clock')))
        with mock.patch.object(native_math, '_load', return_value=backend):
            before = native_math.snapshot()
            self.assertIsNone(native_math.thread_cpu_seconds())
            self.assertEqual(before, native_math.snapshot())
        with mock.patch.object(native_math, '_load', return_value=None):
            self.assertIsNone(native_math.thread_cpu_seconds())


if __name__ == '__main__':
    unittest.main()
