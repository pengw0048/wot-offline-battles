"""The per-round Python census that settles leak-versus-cache.

The 2026-09-09/10 dumps proved CPython's arenas were 0.6% of the crashed
worker's private commit, so Python did not exhaust the address space.  They
could not prove Python was not leaking: a heap that grew from 5 MiB to 25 MiB
over seven rounds has quintupled, and one snapshot at death shows no rate.
"""

import gc
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLIENT_ROOT = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_ROOT))

from gui.mods.offline_lan_0922 import python_heap


class _Leaked(object):
    pass


class PythonHeapTest(unittest.TestCase):
    def test_a_growing_type_names_itself_at_the_top(self):
        # This is the whole point: a type whose count climbs with the round
        # number is a leak with an owner.
        before = dict(python_heap.snapshot()['types']).get('_Leaked', 0)
        held = [_Leaked() for unused in range(5000)]
        after = python_heap.snapshot()
        # Assert on the per-type count, not on the global object total: the
        # total moves under any concurrently running test, while the count of
        # one named type is exactly the signal a leak hunt needs.
        top = dict(after['types'])
        self.assertIn('_Leaked', top)
        self.assertEqual(5000, top['_Leaked'] - before)
        del held
        self.assertNotIn('_Leaked', dict(python_heap.snapshot()['types']))

    def test_the_census_does_not_count_its_own_working_list(self):
        # gc.get_objects() allocates a list of every tracked object. If that
        # list were still referenced when the total is taken, every reading
        # would be inflated by the act of measuring.
        first = python_heap.snapshot()['objects']
        second = python_heap.snapshot()['objects']
        self.assertLess(abs(second - first), 50)

    def test_the_census_never_collects(self):
        # Collecting first would measure a heap the game never experiences.
        collected = []
        original = gc.collect
        gc.collect = lambda *args: collected.append(args) or 0
        try:
            python_heap.snapshot()
        finally:
            gc.collect = original
        self.assertEqual([], collected)

    def test_the_line_carries_the_gc_state_and_the_histogram(self):
        line = python_heap.format_line('round_end', 7)
        self.assertIn('PYHEAP phase=round_end round=7', line)
        self.assertIn('objects=', line)
        self.assertIn('gc_counts=', line)
        self.assertIn('gc_thresholds=', line)
        self.assertIn('gc_enabled=', line)
        self.assertIn('gc_garbage=', line)
        self.assertIn('top=', line)

    def test_uncollectable_objects_are_reported(self):
        # A non-zero count here is its own bug, and it is the shape a cycle
        # through a native object takes.
        self.assertEqual(len(gc.garbage), python_heap.snapshot()['garbage'])

    def test_a_pathological_heap_reports_a_count_without_a_histogram(self):
        original = python_heap.MAX_CENSUS_OBJECTS
        python_heap.MAX_CENSUS_OBJECTS = 1
        self.addCleanup(setattr, python_heap, 'MAX_CENSUS_OBJECTS', original)
        state = python_heap.snapshot()
        self.assertGreater(state['objects'], 1)
        self.assertIsNone(state['types'])
        self.assertIn('top=-', python_heap.format_line('round_start', 1))

    def test_the_histogram_is_bounded(self):
        state = python_heap.snapshot()
        self.assertLessEqual(len(state['types']), python_heap.TOP_TYPES)

    def test_logging_never_raises_into_the_round(self):
        original = python_heap.gc.get_objects
        python_heap.gc.get_objects = lambda: (_ for _ in ()).throw(
            RuntimeError('no heap'))
        self.addCleanup(
            setattr, python_heap.gc, 'get_objects', original)
        self.assertIsNone(python_heap.snapshot())
        self.assertIsNone(python_heap.format_line('round_start', 1))
        python_heap.log('round_start', 1)


if __name__ == '__main__':
    unittest.main()


class ForcedCollectTest(unittest.TestCase):
    """The experiment that separates unreachable cycles from live references.

    Report 20260910-034953: the worker went from 1.14M tracked objects on
    round 1 to 2.87M on round 6 (~+347k per round, linear) while the visible
    client on the same machine and the same six rounds stayed flat near
    1.19M.  Both report `gc_enabled=0` - the engine imports `gc` and calls
    `disable` from native code - so reference counting frees everything
    acyclic and only cycles can accumulate.
    """

    def setUp(self):
        self._debug = gc.get_debug()
        self._enabled = gc.isenabled()
        self.addCleanup(gc.set_debug, self._debug)

    def tearDown(self):
        # Nothing here may change whether the collector is running.
        self.assertEqual(self._enabled, gc.isenabled())

    def _leak_cycles(self, count):
        for index in range(count):
            record = {'id': index, 'pos': (0, 0, 0), 'yaw': 0.0, 'rows': []}
            holder = [record]
            record['self'] = holder          # the cycle
            record['rows'].append((index, index, index))

    def test_a_cyclic_leak_is_found_and_freed(self):
        gc.collect()
        self._leak_cycles(2000)
        state = python_heap.collect_once()
        self.assertGreater(state['unreachable'], 0)
        self.assertGreaterEqual(state['before'], state['after'])

    def test_the_leaked_record_is_named_by_its_keys(self):
        # "dict" is not a diagnosis. The key set is.
        gc.collect()
        self._leak_cycles(2000)
        state = python_heap.collect_once()
        shapes = dict(state['signatures'])
        named = [name for name in shapes
                 if name.startswith('dict{') and 'yaw' in name]
        self.assertTrue(named, 'no dict signature named the leaked record: %r'
                        % (shapes,))
        self.assertIn('id', named[0])
        self.assertIn('rows', named[0])

    def test_list_and_tuple_shapes_are_reported_by_length(self):
        gc.collect()
        self._leak_cycles(2000)
        shapes = dict(python_heap.collect_once()['signatures'])
        self.assertTrue(any(name.startswith('list(len=')
                            for name in shapes), repr(shapes))
        self.assertTrue(any(name.startswith('tuple(len=')
                            for name in shapes), repr(shapes))

    def test_the_debug_flags_are_restored(self):
        python_heap.collect_once()
        self.assertEqual(self._debug, gc.get_debug())

    def test_the_garbage_list_is_left_empty(self):
        # DEBUG_SAVEALL parks everything the collector found in gc.garbage.
        # Leaving it there would turn a diagnostic into a second leak.
        self._leak_cycles(500)
        python_heap.collect_once()
        self.assertEqual([], gc.garbage)

    def test_the_lines_report_the_accounting_and_the_cost(self):
        lines = python_heap.format_collect_lines('round_end', 6)
        self.assertEqual(2, len(lines))
        self.assertIn('PYGC phase=round_end round=6', lines[0])
        for field in ('before=', 'unreachable=', 'after=', 'net_freed=',
                      'second_pass=', 'elapsed_ms=', 'gc_enabled_after='):
            self.assertIn(field, lines[0])
        self.assertIn('PYSIG phase=round_end round=6', lines[1])
        self.assertIn('sampled=', lines[1])
        self.assertIn('shapes=', lines[1])

    def test_a_function_signature_carries_its_source_location(self):
        def _closure_target():
            return None

        signature = python_heap._signature(_closure_target)
        self.assertIn('test_port_0922_python_heap.py', signature)
        self.assertTrue(signature.startswith('function@'), signature)

    def test_the_signature_sample_is_bounded(self):
        scanned, ranked = python_heap.signature_census(
            [{} for unused in range(python_heap.SIGNATURE_SAMPLE + 500)])
        self.assertEqual(python_heap.SIGNATURE_SAMPLE, scanned)
        self.assertLessEqual(len(ranked), python_heap.TOP_SIGNATURES)

    def test_a_broken_gc_never_raises_into_the_round(self):
        original = python_heap.gc.get_objects
        python_heap.gc.get_objects = lambda: (_ for _ in ()).throw(
            RuntimeError('no heap'))
        self.addCleanup(setattr, python_heap.gc, 'get_objects', original)
        self.assertIsNone(python_heap.collect_once())
        self.assertEqual((), python_heap.format_collect_lines('round_end', 1))
        python_heap.log_collect('round_end', 1)
