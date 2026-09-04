import importlib.util
import marshal
import os
from pathlib import Path
import pstats
import sys
import tempfile
import types
import unittest


PACKAGE_ROOT = (Path(__file__).resolve().parents[1] / 'src' / 'res' /
                'scripts' / 'client' / 'gui' / 'mods' / 'offline_lan_0922')


def _load():
    name = 'gui.mods.offline_lan_0922.worker_lsprof'
    for package in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        if package not in sys.modules:
            module = types.ModuleType(package)
            module.__path__ = [str(PACKAGE_ROOT)]
            sys.modules[package] = module
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        name, PACKAGE_ROOT / 'worker_lsprof.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _Code(object):
    def __init__(self, filename, line, name):
        self.co_filename = filename
        self.co_firstlineno = line
        self.co_name = name


class _Entry(object):
    def __init__(self, code, callcount, inlinetime, totaltime, calls=None,
                 reccallcount=0):
        self.code = code
        self.callcount = callcount
        self.reccallcount = reccallcount
        self.inlinetime = inlinetime
        self.totaltime = totaltime
        self.calls = calls


class _FakeProfiler(object):
    """Mimic the ``_lsprof.Profiler`` surface the worker relies on."""

    instances = []

    def __init__(self):
        self.enabled = None
        self.disabled = False
        _FakeProfiler.instances.append(self)

    def enable(self, subcalls=True, builtins=True):
        self.enabled = (subcalls, builtins)

    def disable(self):
        self.disabled = True

    def getstats(self):
        outer = _Code('C:\\game\\res\\scripts\\client\\gui\\mods\\'
                      'offline_lan_0922\\battle_runtime.py', 100, '_frame')
        inner = _Code('C:\\game\\res\\scripts\\client\\gui\\mods\\'
                      'offline_lan_0922\\bot_runtime.py', 8787,
                      '_update_once')
        sub = _Entry(inner, 30, 0.9, 0.9)
        return [
            _Entry(outer, 10, 0.1, 1.0, calls=[sub]),
            _Entry(inner, 30, 0.9, 0.9),
            _Entry("<method 'get' of 'dict' objects>", 5000, 0.2, 0.2),
        ]


class WorkerProfilerTests(unittest.TestCase):
    def setUp(self):
        self.module = _load()
        _FakeProfiler.instances = []
        self.lines = []
        self.clock = [1000.0]
        self.directory = tempfile.mkdtemp()

    def _profiler(self, windows=((0.5, 1.0), (3.0, 1.0))):
        return self.module.WorkerProfiler(
            self.directory, windows=windows, writer=self.lines.append,
            clock=lambda: self.clock[0], profiler_factory=_FakeProfiler,
            environ={'PROCESSOR_ARCHITECTURE': 'x86',
                     'PROCESSOR_ARCHITEW6432': 'ARM64',
                     'NUMBER_OF_PROCESSORS': '8'})

    def _advance(self, profiler, seconds, live=True, round_id=1, step=0.1):
        steps = int(round(seconds / step))
        for _ in range(steps):
            self.clock[0] += step
            profiler.tick(live, round_id)

    def test_windows_follow_the_first_live_frame_and_write_reports(self):
        profiler = self._profiler()
        self._advance(profiler, 1.0, live=False)
        self.assertFalse(profiler.active)
        self.assertEqual([], _FakeProfiler.instances)
        self._advance(profiler, 0.4)
        self.assertFalse(profiler.active)
        self._advance(profiler, 0.2)
        self.assertTrue(profiler.active)
        self.assertEqual((True, True), _FakeProfiler.instances[0].enabled)
        self._advance(profiler, 1.0)
        self.assertFalse(profiler.active)
        self.assertTrue(_FakeProfiler.instances[0].disabled)
        self._advance(profiler, 2.5)
        self.assertEqual(2, len(_FakeProfiler.instances))
        self.assertEqual(2, len(profiler.reports))
        report = Path(profiler.reports[0]).read_text(encoding='utf-8')
        self.assertIn('reason: complete', report)
        self.assertIn('window: 1', report)
        self.assertIn('processor_architew6432=ARM64', report)
        self.assertIn('offline_lan_0922/bot_runtime.py', report)
        self.assertIn('bot_runtime.py:8787(_update_once)', report)
        self.assertIn("<method 'get' of 'dict' objects>", report)
        self.assertIn('=== inline time by module', report)
        self.assertIn('frames: 10', report)
        names = sorted(os.listdir(self.directory))
        self.assertEqual([
            'offline-worker-lsprof-round1-w1.pstats',
            'offline-worker-lsprof-round1-w1.txt',
            'offline-worker-lsprof-round1-w2.pstats',
            'offline-worker-lsprof-round1-w2.txt'], names)
        joined = ''.join(self.lines)
        self.assertIn('LSPROF platform', joined)
        self.assertIn('LSPROF begin window=1', joined)
        self.assertIn('LSPROF end window=1 reason=complete', joined)
        self.assertIn('LSPROF end window=2 reason=complete', joined)
        self.assertEqual(1, joined.count('LSPROF platform'))

    def test_pstats_payload_is_loadable_and_carries_callers(self):
        payload = self.module.pstats_payload(_FakeProfiler().getstats())
        path = os.path.join(self.directory, 'sample.pstats')
        with open(path, 'wb') as stream:
            marshal.dump(payload, stream)
        stats = pstats.Stats(path)
        stats.sort_stats('tottime')
        key = ('C:\\game\\res\\scripts\\client\\gui\\mods\\offline_lan_0922'
               '\\bot_runtime.py', 8787, '_update_once')
        self.assertIn(key, stats.stats)
        primitive, total, inline, cumulative, callers = stats.stats[key]
        self.assertEqual((30, 30), (primitive, total))
        self.assertAlmostEqual(0.9, inline)
        self.assertEqual(1, len(callers))
        self.assertEqual(('~', 0, "<method 'get' of 'dict' objects>"),
                         sorted(stats.stats)[-1])

    def test_battle_end_and_round_change_close_an_open_window(self):
        profiler = self._profiler(windows=((0.0, 100.0),))
        self._advance(profiler, 0.3)
        self.assertTrue(profiler.active)
        self._advance(profiler, 0.1, live=False)
        self.assertFalse(profiler.active)
        self.assertIn('reason=battle_ended', ''.join(self.lines))
        _FakeProfiler.instances = []
        profiler = self._profiler(windows=((0.0, 100.0),))
        self._advance(profiler, 0.3, round_id=1)
        self._advance(profiler, 0.3, round_id=2)
        self.assertTrue(profiler.active)
        self.assertIn('reason=round_changed', ''.join(self.lines))
        self.assertEqual(2, len([
            instance for instance in _FakeProfiler.instances
            if instance.enabled is not None]))

    def test_profiler_failure_disables_itself_without_raising(self):
        class _Broken(_FakeProfiler):
            def enable(self, subcalls=True, builtins=True):
                raise RuntimeError('no profiler')

        profiler = self.module.WorkerProfiler(
            self.directory, windows=((0.0, 1.0),), writer=self.lines.append,
            clock=lambda: self.clock[0], profiler_factory=_Broken,
            environ={})
        self._advance(profiler, 0.5)
        self.assertFalse(profiler.active)
        self.assertIn('LSPROF disabled after error', ''.join(self.lines))
        self._advance(profiler, 5.0)
        self.assertEqual([], profiler.reports)

    def test_create_for_worker_never_raises(self):
        profiler = self.module.create_for_worker(
            output_dir=self.directory, writer=self.lines.append)
        self.assertTrue(hasattr(profiler, 'tick'))
        self.assertFalse(profiler.tick(False, None))
        disabled = self.module.DisabledProfiler()
        self.assertFalse(disabled.tick(True, 1))
        self.assertFalse(disabled.active)


if __name__ == '__main__':
    unittest.main()
