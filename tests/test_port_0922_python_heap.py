"""GC-tracked object trends, without total-memory or leak attribution."""

import contextlib
import gc
import io
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


class ForcedCollectTest(unittest.TestCase):
    """The experiment that separates unreachable cycles from live references.

    Report 20260910-034953: the worker went from 1.14M tracked objects on
    round 1 to 2.87M on round 6 (~+347k per round, linear) while the visible
    client on the same machine and the same six rounds stayed flat near
    1.19M. Both report `gc_enabled=0`. This experiment observes collectable
    cycles; it does not exclude reachable objects as a source of growth.
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
            # Participate in the cycle; an all-integer tuple may be untracked
            # by the collector and cannot be required in the garbage sample.
            record['rows'].append((index, index, record))

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
        self.assertEqual(3, len(lines))
        self.assertIn('PYGC phase=round_end round=6', lines[0])
        for field in ('before=', 'unreachable=', 'after=', 'net_freed=',
                      'second_pass=', 'elapsed_ms=', 'gc_enabled_after='):
            self.assertIn(field, lines[0])
        self.assertIn('PYSIG phase=round_end round=6', lines[1])
        self.assertIn('sampled=', lines[1])
        self.assertIn('shapes=', lines[1])
        self.assertIn('PYREF phase=round_end round=6', lines[2])
        self.assertIn('window=', lines[2])
        self.assertIn('holds=', lines[2])

    def test_the_edge_census_reports_both_directions_of_the_fixture_cycle(self):
        # These two shapes are evidence about the fixture's references, not
        # proof that any pair of matching shapes belongs to the same cycle.
        gc.collect()
        self._leak_cycles(2000)
        state = python_heap.collect_once()
        holds = dict(state['edges'])
        forward = [name for name in holds
                   if name.startswith('dict{') and '-> list(' in name]
        self.assertTrue(forward, 'no dict->list edge found: %r' % (holds,))
        back = [name for name in holds
                if name.startswith('list(') and '-> dict{' in name]
        self.assertTrue(back, 'no list->dict edge closing the cycle: %r'
                        % (holds,))

    def test_only_edges_inside_the_sample_window_are_reported(self):
        outsider = {'reachable_marker': True}
        member = {'name': 'member'}
        holder = [member]
        member['self'] = holder
        member['outside'] = outsider
        window, edges, ranked = python_heap.edge_census([member, holder])
        self.assertEqual(2, window)
        self.assertEqual(2, edges)
        # The caller supplies a window. Omitted targets may still belong to
        # the unreachable set, but their edges cannot be reported here.
        self.assertFalse(
            any('reachable_marker' in name for name, unused in ranked),
            repr(ranked))

    def test_the_edge_sample_is_bounded(self):
        window, unused_edges, ranked = python_heap.edge_census(
            [{} for unused in range(python_heap.EDGE_SAMPLE + 500)])
        self.assertEqual(python_heap.EDGE_SAMPLE, window)
        self.assertLessEqual(len(ranked), python_heap.TOP_EDGES)

    def test_logging_runs_the_diagnostic_and_reports_success(self):
        self.addCleanup(setattr, python_heap, 'format_collect_lines',
                        python_heap.format_collect_lines)
        calls = []
        python_heap.format_collect_lines = lambda *args: (
            calls.append(args) or ('diagnostic result',))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertTrue(python_heap.log_collect('round_end', 1))
        self.assertEqual([('round_end', 1)], calls)
        self.assertEqual('diagnostic result\n', output.getvalue())

    def test_completed_diagnostic_stays_successful_when_output_fails(self):
        self.addCleanup(setattr, python_heap, 'format_collect_lines',
                        python_heap.format_collect_lines)
        python_heap.format_collect_lines = lambda *args: ('diagnostic result',)

        class BrokenOutput(object):
            def write(self, unused):
                raise IOError('output unavailable')

        with contextlib.redirect_stdout(BrokenOutput()):
            self.assertTrue(python_heap.log_collect('round_end', 1))

    def test_missing_diagnostic_result_reports_failure(self):
        self.addCleanup(setattr, python_heap, 'format_collect_lines',
                        python_heap.format_collect_lines)
        python_heap.format_collect_lines = lambda *args: ()
        self.assertFalse(python_heap.log_collect('round_end', 1))

    def test_edge_census_keeps_self_edges_and_repeated_references(self):
        record = {}
        record['self'] = record
        holder = []
        holder.extend([holder, holder])
        window, count, ranked = python_heap.edge_census([record, holder])
        self.assertEqual(2, window)
        self.assertEqual(3, count)
        self.assertEqual(1, dict(ranked)['dict{self} -> dict{self}'])
        self.assertEqual(2, dict(ranked)['list(len=2) -> list(len=2)'])

    def test_edge_window_does_not_materialize_the_rest_of_an_iterator(self):
        consumed = []

        def source():
            for index in range(3):
                consumed.append(index)
                yield {}
            raise AssertionError('consumed beyond the requested sample')

        window, unused_count, unused_ranked = python_heap.edge_census(
            source(), sample=3)
        self.assertEqual(3, window)
        self.assertEqual([0, 1, 2], consumed)

    def test_large_containers_have_a_reference_budget(self):
        target = []
        holder = [target] * 100000
        unused_window, count, unused_ranked = python_heap.edge_census(
            [holder, target])
        self.assertEqual(32, count)

    def test_dictionary_reference_budget_includes_keys_and_values(self):
        keys = [(index,) for index in range(100)]
        values = [[] for unused in keys]
        record = dict(zip(keys, values))
        unused_window, count, ranked = python_heap.edge_census(
            [record] + keys + values)
        self.assertEqual(32, count)
        holds = dict(ranked)
        self.assertEqual(16, holds['dict(len=<=128) -> tuple(len=1)'])
        self.assertEqual(16, holds['dict(len=<=128) -> list(len=0)'])

    def test_edge_sampling_does_not_invoke_native_or_subclass_traversal(self):
        self.addCleanup(setattr, python_heap.gc, 'get_referents',
                        python_heap.gc.get_referents)
        calls = []
        python_heap.gc.get_referents = lambda item: calls.append('native') or []

        class Opaque(object):
            pass

        class CustomList(list):
            def __iter__(self):
                calls.append('subclass')
                return iter(())

        python_heap.edge_census([Opaque(), CustomList([[]])])
        self.assertEqual([], calls)

    def test_edge_iterator_failure_does_not_retain_sampled_cycles(self):
        class Cycle(object):
            pass

        def broken_referents(item):
            yield item
            raise RuntimeError('edge inspection failed')

        self.addCleanup(setattr, python_heap, '_edge_referents',
                        python_heap._edge_referents)
        python_heap._edge_referents = broken_referents
        gc.collect()
        was_enabled = gc.isenabled()
        try:
            gc.disable()
            item = Cycle()
            item.cycle = item
            del item
            self.assertIsNotNone(python_heap.collect_once())
            self.assertFalse(any(type(item) is Cycle for item in gc.get_objects()))
            self.assertFalse(gc.isenabled())
            self.assertEqual(self._debug, gc.get_debug())
        finally:
            if was_enabled:
                gc.enable()

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

    def test_preexisting_garbage_is_not_owned_by_the_probe(self):
        collector = self._install_fake_collector()
        existing = collector.garbage[0]
        state = python_heap.collect_once()
        self.assertIsNotNone(state)
        self.assertEqual([existing], collector.garbage)
        self.assertEqual(1, state['scanned'])
        self.assertEqual(2, collector.calls)

    def test_existing_saveall_session_keeps_ownership(self):
        collector = self._install_fake_collector()
        collector.debug = collector.DEBUG_SAVEALL
        existing = list(collector.garbage)
        self.assertIsNone(python_heap.collect_once())
        self.assertEqual(existing, collector.garbage)
        self.assertEqual(collector.DEBUG_SAVEALL, collector.debug)
        self.assertEqual(0, collector.calls)

    def test_sampling_failure_still_releases_owned_cycles_and_restores_flags(self):
        collector = self._install_fake_collector()
        existing = collector.garbage[0]
        original = python_heap.signature_census
        self.addCleanup(setattr, python_heap, 'signature_census', original)

        def fail(unused):
            raise RuntimeError('sampling failed')

        python_heap.signature_census = fail
        self.assertIsNone(python_heap.collect_once())
        self.assertEqual([existing], collector.garbage)
        self.assertEqual(2, collector.calls)
        self.assertEqual(0, collector.debug)

    def test_second_collection_failure_still_restores_flags(self):
        collector = self._install_fake_collector(fail_second=True)
        self.assertIsNone(python_heap.collect_once())
        self.assertEqual(0, collector.debug)

    def _install_fake_collector(self, fail_second=False):
        class Collector(object):
            DEBUG_SAVEALL = 32

            def __init__(self):
                self.debug = 0
                self.garbage = [{'preexisting': True}]
                self.calls = 0

            def get_objects(self):
                return []

            def get_debug(self):
                return self.debug

            def set_debug(self, value):
                self.debug = value

            def isenabled(self):
                return False

            def collect(self):
                self.calls += 1
                if fail_second and self.calls == 2:
                    raise RuntimeError('second collection failed')
                if self.debug & self.DEBUG_SAVEALL:
                    self.garbage.append({'new_cycle': True})
                return 1

        collector = Collector()
        self.addCleanup(setattr, python_heap, 'gc', python_heap.gc)
        python_heap.gc = collector
        return collector

    def test_signatures_never_call_user_properties_or_container_overrides(self):
        calls = []

        class Hostile(object):
            @property
            def func_code(self):
                calls.append('property')
                raise RuntimeError('must not inspect')

        class HostileDict(dict):
            def keys(self):
                calls.append('keys')
                return ['wrong']

        class HostileMeta(type):
            @property
            def __name__(cls):
                calls.append('metaclass')
                return 'wrong'

            def __eq__(cls, unused):
                calls.append('equality')
                return False

        custom_type = HostileMeta('SafeTypeName', (object,), {})
        python_heap._signature(Hostile())
        python_heap._signature(HostileDict(real=1))
        self.assertEqual('SafeTypeName', python_heap._signature(custom_type()))
        self.assertEqual([], calls)

    def test_dictionary_signatures_have_bounded_single_line_output(self):
        shape = python_heap._signature({'a\n' + 'x' * 100000: 1})
        self.assertLess(len(shape), 200)
        self.assertNotIn('\n', shape)
        # A wide dict still names itself, because that is what identifies the
        # leaked record: `dict(len=<=64)` was 84% of the sampled garbage in
        # reports 20260910-045317 and -061701 and said nothing about what it
        # was. Bounded to MAX_SIGNATURE_KEYS names plus a remainder count.
        large = python_heap._signature(dict((str(n), n) for n in range(100)))
        self.assertTrue(large.startswith('dict{'), large)
        self.assertTrue(large.endswith(',+92}'), large)
        names = large[len('dict{'):-len(',+92}')].split(',')
        self.assertEqual(python_heap.MAX_SIGNATURE_KEYS, len(names))
        self.assertLess(len(large), 200)
        self.assertNotIn('\n', large)
        # Only a mapping with no text key at all falls back to a bare length.
        self.assertEqual(
            'dict(len=<=64)',
            python_heap._signature(dict((n, n) for n in range(40))))
        # The scan is bounded, so a pathologically wide mapping is cheap.
        huge = python_heap._signature(
            dict(('k%05d' % n, n) for n in range(200000)))
        self.assertTrue(huge.startswith('dict{'), huge)
        self.assertTrue(huge.endswith(',+199992}'), huge)
        self.assertLess(len(huge), 200)

    def test_sampling_failure_does_not_keep_cycles_in_exception_frames(self):
        class Cycle(object):
            pass

        original = python_heap.signature_census
        self.addCleanup(setattr, python_heap, 'signature_census', original)

        def fail(items):
            # Keep both the sample and its last item in the raising frame.
            for item in items:
                if type(item) is Cycle:
                    raise RuntimeError('sampling failed')
            raise AssertionError('cycle was not sampled')

        gc.collect()
        was_enabled = gc.isenabled()
        try:
            gc.disable()
            item = Cycle()
            item.cycle = item
            del item
            python_heap.signature_census = fail
            self.assertIsNone(python_heap.collect_once())
            # Weakrefs can be cleared by SAVEALL's first pass before the
            # referent is freed. Inspect the tracked instance itself instead.
            self.assertFalse(any(type(item) is Cycle for item in gc.get_objects()))
            self.assertFalse(gc.isenabled())
            self.assertEqual(self._debug, gc.get_debug())
        finally:
            if was_enabled:
                gc.enable()
