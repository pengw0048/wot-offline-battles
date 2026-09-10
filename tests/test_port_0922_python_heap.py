"""GC-tracked object trends, without total-memory or leak attribution."""

import gc
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLIENT_ROOT = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_ROOT))

from gui.mods.offline_lan_0922 import python_heap


class _Retained(object):
    pass


class PythonHeapTest(unittest.TestCase):
    def test_a_growing_type_names_itself_at_the_top(self):
        # A retained type should be visible for further investigation, whether
        # its retention is intentional or a leak.
        before = dict(python_heap.snapshot()['types']).get('_Retained', 0)
        held = [_Retained() for unused in range(5000)]
        after = python_heap.snapshot()
        # Assert on the per-type count, not on the global object total: the
        # total moves under any concurrently running test, while the count of
        # one named type is exactly the signal a leak hunt needs.
        top = dict(after['types'])
        self.assertIn('_Retained', top)
        self.assertEqual(5000, top['_Retained'] - before)
        del held
        self.assertNotIn('_Retained', dict(python_heap.snapshot()['types']))

    def test_repeated_census_does_not_retain_temporary_references(self):
        # Repeating the census should not itself cause sustained growth.
        first = python_heap.snapshot()['gc_tracked_objects']
        second = python_heap.snapshot()['gc_tracked_objects']
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
        self.assertIn('gc_tracked_objects=', line)
        self.assertNotIn(' objects=', line)
        self.assertIn('gc_counts=', line)
        self.assertIn('gc_thresholds=', line)
        self.assertIn('gc_enabled=', line)
        self.assertIn('gc_garbage=', line)
        self.assertIn('top=', line)

    def test_uncollectable_objects_are_reported(self):
        # The count is evidence to investigate, without assigning an owner.
        self.assertEqual(len(gc.garbage), python_heap.snapshot()['garbage'])

    def test_a_pathological_heap_reports_a_count_without_a_histogram(self):
        original = python_heap.MAX_CENSUS_OBJECTS
        python_heap.MAX_CENSUS_OBJECTS = 1
        self.addCleanup(setattr, python_heap, 'MAX_CENSUS_OBJECTS', original)
        state = python_heap.snapshot()
        self.assertGreater(state['gc_tracked_objects'], 1)
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
