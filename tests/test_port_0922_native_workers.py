"""Main-thread ownership and lifecycle of background navigation and spotting."""
from pathlib import Path
import sys
import threading
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'res' / 'scripts' / 'client'))
from gui.mods.offline_lan_0922 import native_math, native_navigation, native_visibility
from gui.mods.offline_lan_0922.ai.navigation import TerrainGrid, TerrainNavigator
from gui.mods.offline_lan_0922.bot_runtime import BotRuntime
from gui.mods.offline_lan_0922.foliage import FoliageMap


def graph():
    directions = ((-1, -1), (0, -1), (1, -1), (-1, 0),
                  (1, 0), (-1, 1), (0, 1), (1, 1))
    return dict(format='offline-lan-0922-navgraph', version=2,
                origin=(0., 0.), cell_size=4., width=5, height=3,
                heights_mm=(0,) * 15, hazards=(0,) * 15,
                links=tuple(sum(1 << index for index, (dx, dz) in enumerate(directions)
                                if 0 <= x + dx < 5 and 0 <= z + dz < 3)
                            for z in range(3) for x in range(5)),
                bounds=(0., 0., 16., 8.))


class Backend:
    def __init__(self):
        self.completed = []
        self.queries = []
        self.answers = []
        self.cancelled = []
        self.closed = []
        self.visibility = []
        self.visibility_inputs = {}
        self.reductions = []
        self.maps = []

    def nav_open(self, value):
        self.maps.append(value)
        return len(self.maps)

    def nav_submit(self, *args):
        return True

    def nav_poll(self, context):
        completed, self.completed = self.completed, []
        queries, self.queries = self.queries, []
        return completed, queries, (0, 0, 0, 0, 0, 2)

    def nav_answer(self, context, values):
        self.answers.extend(values)

    def nav_cancel(self, context, values):
        self.cancelled.extend(values)

    def nav_close(self, context):
        self.closed.append(context)

    def vis_open(self, value):
        self.maps.append(value)
        return len(self.maps)

    def vis_update(self, *args):
        return True

    def vis_submit(self, context, job_id, value):
        self.visibility_inputs[job_id] = value
        return True

    def vis_poll(self, context):
        result, self.visibility = self.visibility, []
        return result

    def vis_cancel(self, context, values):
        self.cancelled.extend(values)

    def vis_close(self, context):
        self.closed.append(context)

    def vis_reduce(self, context, job_id, prefix):
        self.reductions.append((job_id, prefix))
        return True, 0., .1, True, 360.

    def finish_visibility(self, job_id):
        self.visibility.append((job_id, 'done', (
            ((0., 2., 0.), (120., 1., 0.), .3),
            ((0., 2., 0.), (120., 2., 0.), 0.),
            ((0., 2., 0.), (120., 3., 0.), .1)), .001))


class NavigationOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.grid = TerrainGrid(lambda x, z, y: 0., baked_graph=graph())
        self.owner = native_navigation.NativeNavigation(self.grid, self.backend)
        self.addCleanup(self.owner.close)

    def submit(self):
        return self.owner.submit((0., 0., 0.), (16., 0., 8.), 1., 128, False)

    def test_corridor_oracle_known_results_share_cache_and_preserve_guards(self):
        start, end = (0., 0., 0.), (4., 0., 0.)
        for status in (0, 1):
            with self.subTest(status=status):
                self.grid._native_review_cache.clear()
                oracle = mock.Mock()
                oracle.run.return_value = (status, 10, 4)
                self.grid.native_query_oracle = oracle
                with mock.patch.object(self.grid, 'ground_probe',
                                       side_effect=AssertionError('Python replay')):
                    self.assertEqual(self.grid._native_segment_clear(start, end), bool(status))
                    self.assertEqual(self.grid._native_segment_clear(start, end), bool(status))
                    self.assertFalse(self.grid._native_segment_clear(start, (49., 0., 0.)))
                oracle.run.assert_called_once_with(start, end, 4., .48, .38)

    def test_corridor_unknown_retries_next_frame_without_caching_blocker(self):
        start, end = (0., 0., 0.), (4., 0., 0.)
        oracle = mock.Mock()
        oracle.run.side_effect = [(-1, 2, 0), (1, 10, 4)]
        self.grid.native_query_oracle = oracle
        self.grid._native_review_pending = set()
        self.assertFalse(self.grid._native_segment_clear(start, end))
        self.assertFalse(self.grid._native_segment_clear(start, end))
        self.assertEqual(oracle.run.call_count, 1)
        self.assertEqual(self.grid._native_review_cache, {})
        self.grid._native_review_pending = set()
        self.assertTrue(self.grid._native_segment_clear(start, end))
        self.assertEqual(oracle.run.call_count, 2)

    def test_corridor_unavailable_before_dispatch_retains_python_proof(self):
        oracle = mock.Mock()
        oracle.run.return_value = None
        self.grid.native_query_oracle = oracle
        columns, corridors = [], []
        self.grid.ground_probe = lambda *args: columns.append(args) or 0.
        self.grid.obstacle_probe = lambda *args: corridors.append(args) or False
        self.assertTrue(self.grid._native_segment_clear((0., 0., 0.), (4., 0., 0.)))
        self.assertEqual(len(columns), 4)
        self.assertEqual(len(corridors), 1)

    def test_corridor_dispatch_exception_never_replays_committed_queries(self):
        effects = []
        def dispatch(*unused):
            effects.append('engine query')
            raise RuntimeError('failure after dispatch')
        oracle = mock.Mock()
        oracle.run.side_effect = dispatch
        self.grid.native_query_oracle = oracle
        self.grid._native_review_pending = set()
        with mock.patch.object(self.grid, 'ground_probe',
                               side_effect=AssertionError('Python replay')):
            self.assertFalse(self.grid._native_segment_clear((0., 0., 0.), (4., 0., 0.)))
            self.assertFalse(self.grid._native_segment_clear((0., 0., 0.), (4., 0., 0.)))
        self.assertEqual(effects, ['engine query'])
        self.assertEqual(self.grid._native_review_cache, {})

    def test_queries_are_fair_bounded_and_engine_failure_is_answered(self):
        first, second = self.submit(), self.submit()
        self.backend.queries = [
            (1, first.job_id, (0., 0., 0.), (4., 0., 0.)),
            (2, first.job_id, (4., 0., 0.), (8., 0., 0.)),
            (3, second.job_id, (0., 0., 4.), (4., 0., 4.))]
        threads = []
        def probe(start, end):
            threads.append(threading.get_ident())
            if start[2] == 4.:
                raise RuntimeError('unavailable engine column')
            return True
        self.grid._native_segment_clear = probe
        self.owner.advance(1.2, 2)
        self.assertEqual(self.backend.answers, [(1, True), (3, False)])
        self.assertEqual(threads, [threading.get_ident()] * 2)
        self.owner.advance(1.4, 2)
        self.assertEqual(self.backend.answers[-1], (2, True))

    def test_proved_blocker_answers_false_instead_of_waiting_for_streaming(self):
        job = self.submit()
        start, end = (0., 0., 0.), (4., 0., 0.)
        oracle = mock.Mock()
        oracle.run.return_value = (0, 1, 1)
        self.grid.native_query_oracle = oracle
        self.grid._native_review_pending = set()
        self.backend.queries = [(1, job.job_id, start, end)]
        self.owner.advance(1.2, 384)
        self.assertEqual(self.backend.answers, [(1, False)])
        self.assertEqual(self.owner.queries, {})
        self.assertEqual(self.grid._native_review_pending, set())
        self.assertEqual(list(self.grid._native_review_cache.values()), [False])

    def test_ambiguous_python_ground_does_not_defer_as_native_unknown(self):
        for bound_oracle in (False, True):
            with self.subTest(bound_oracle=bound_oracle):
                job = self.submit()
                oracle = mock.Mock()
                oracle.run.return_value = None
                self.grid.native_query_oracle = oracle if bound_oracle else None
                self.grid.ground_probe = lambda *args: None
                self.grid._native_review_pending = set()
                self.backend.queries = [(100 + job.job_id, job.job_id,
                                         (0., 0., 0.), (4., 0., 0.))]
                self.owner.advance(1.2, 384)
                self.assertEqual(self.backend.answers[-1], (100 + job.job_id, False))
                self.assertNotIn(job.job_id, self.owner.queries)

    def test_unknown_receipt_waits_for_ready_without_answering_false(self):
        job = self.submit()
        start, end = (0., 0., 0.), (4., 0., 0.)
        oracle = mock.Mock()
        oracle.run.side_effect = [(-1, 1, 0), (1, 10, 4)]
        self.grid.native_query_oracle = oracle
        self.backend.queries = [(1, job.job_id, start, end)]
        self.grid._native_review_pending = set()
        self.owner.advance(1.2, 384)
        self.assertEqual(oracle.run.call_count, 1)
        self.assertEqual(self.backend.answers, [])
        self.assertEqual(self.grid._native_review_cache, {})
        self.assertFalse(job.done)
        self.grid._native_review_pending = set()
        self.owner.advance(1.4, 384)
        self.assertEqual(oracle.run.call_count, 2)
        self.assertEqual(self.backend.answers, [(1, True)])
        self.assertEqual(self.owner.total_queries, 1)
        self.assertEqual(self.owner.query_order, native_navigation.deque())

    def test_unknown_jobs_do_not_starve_ready_jobs_or_spin_same_callback(self):
        first, second = self.submit(), self.submit()
        unknown = ((0., 0., 0.), (4., 0., 0.))
        clear = ((0., 0., 4.), (4., 0., 4.))
        oracle = mock.Mock()
        oracle.run.side_effect = [(-1, 1, 0), (1, 10, 4)]
        self.grid.native_query_oracle = oracle
        self.grid._native_review_pending = set()
        self.backend.queries = [(1, first.job_id) + unknown,
                                (2, first.job_id) + unknown,
                                (3, second.job_id) + clear]
        self.owner.advance(1.2, 384)
        self.assertEqual(oracle.run.call_count, 2)
        self.assertEqual(self.backend.answers, [(3, True)])
        self.assertEqual(len(self.owner.queries[first.job_id]), 2)
        self.assertNotIn(second.job_id, self.owner.queries)
        self.owner.cancel((first,))
        self.grid._native_review_pending = set()
        self.owner.advance(1.4, 384)
        self.assertEqual(oracle.run.call_count, 2)
        self.assertEqual(self.backend.answers, [(3, True)])
        self.assertEqual(first.status, 'cancelled')

    def test_cancel_discards_pending_query_and_late_result(self):
        job = self.submit()
        self.owner.cancel((job,))
        self.backend.queries = [(1, job.job_id, (0., 0., 0.), (4., 0., 0.))]
        self.backend.completed = [(job.job_id, 'done', ((0., 0., 0.),), 0, 1, .001)]
        self.owner.advance(2., 10)
        self.assertEqual(job.status, 'cancelled')
        self.assertEqual(job.result, ())
        self.assertEqual(self.backend.answers, [])

    def test_close_is_idempotent_and_does_not_step_python_search(self):
        job = self.submit()
        self.assertFalse(job.step(10000))
        self.owner.close()
        self.owner.close()
        self.assertTrue(job.done)
        self.assertEqual(job.status, 'cancelled')
        self.assertEqual(self.backend.closed, [1])

    def test_wreck_invalidates_completion_before_driver_can_receive_it(self):
        with mock.patch.object(native_math, '_backend', self.backend):
            navigator = TerrainNavigator(lambda x, z, y: 0., baked_graph=graph())
        self.addCleanup(navigator.close)
        path = ((0., 0., 0.), (16., 0., 0.))
        job = native_navigation.NativeSearch(1, 0, 1.)
        job.done, job.result = True, path
        key = (('route', 'test'), (4, 0))
        navigator.searches[key] = job
        navigator.search_times[key] = 1.
        navigator.grid._static_hull_edges[((1, 0), (2, 0))] = 240.
        navigator.grid.static_hull_revision = 1
        self.assertFalse(navigator._finish_search(key, job, 1.2))
        self.assertNotIn(key, navigator.paths)
        self.assertNotIn(key, navigator.searches)


class VisibilityOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.foliage = FoliageMap(dict(instances=(), cells={}))
        self.owner = native_visibility.NativeVisibility(self.backend, self.foliage, True)
        self.addCleanup(self.owner.close)
        self.identities = (object(), object())
        self.source = dict(position=(0., 0., 0.), yaw=0.)
        self.target = dict(position=(120., 0., 0.), yaw=0.)
        self.detection = (120., 400., (.1, .2), False, False, 0., 1., .5)
        self.queries = []

    def request(self, now, fire_sequence=0, detection=None, identities=None):
        def query(start, end):
            self.queries.append((start, end))
            return len(self.queries) > 1
        return self.owner.request(('bot', 1, 'bot', 2),
            self.identities if identities is None else identities,
            self.source, self.target, ({}, {}), 0,
            self.detection if detection is None else detection,
            now, fire_sequence, query)

    def test_pending_freezes_pose_and_completion_preserves_order_and_sample_time(self):
        self.assertIsNone(self.request(1.))
        self.target['position'] = (130., 0., 0.)
        self.assertEqual(self.backend.visibility_inputs[1][3][0], (120., 0., 0.))
        self.backend.finish_visibility(1)
        result = self.request(1.296)
        self.assertEqual(result['sampled_at'], 1.)
        self.assertEqual(len(self.queries), 2)
        self.assertEqual(self.backend.reductions, [(1, (False, True))])

    def test_fire_actor_and_unknown_foliage_changes_reject_ready_results(self):
        self.request(1.)
        self.backend.finish_visibility(1)
        self.assertIsNone(self.request(1.2, fire_sequence=1))
        self.backend.finish_visibility(2)
        self.identities = (object(), object())
        self.assertIsNone(self.request(1.4, fire_sequence=1))
        self.backend.finish_visibility(3)
        self.foliage.native_revision += 1
        self.assertIsNone(self.request(1.6, fire_sequence=1))
        self.assertEqual(self.queries, [])
        self.assertEqual(self.backend.cancelled, [1, 2, 3])
        self.assertEqual(self.owner.snapshot()['cancellation_reasons'],
                         {'fire': 1, 'identity': 1, 'foliage': 1})

    def test_same_fire_sequence_state_change_rejects_old_camouflage(self):
        recent = list(self.detection)
        recent[4] = True
        self.request(1., fire_sequence=1, detection=tuple(recent))
        self.backend.finish_visibility(1)
        self.assertIsNone(self.request(1.2, fire_sequence=1))
        self.assertEqual(self.queries, [])
        self.assertEqual(self.owner.cancellation_reasons, {'detection': 1})

    def test_continually_requested_sample_expires_before_native_queries(self):
        self.request(1.)
        self.request(1.3)
        self.backend.finish_visibility(1)
        self.assertIsNone(self.request(1.75))
        self.assertEqual(self.queries, [])
        self.assertEqual(self.backend.cancelled, [1])
        self.assertEqual(self.owner.cancellation_reasons, {'age': 1})

    def update_tree(self, center):
        if (17, 9) not in self.foliage.fallen_tree_profiles:
            self.foliage.fallen_tree_profiles[(17, 9)] = ((-1.,) * 3 + (1.,) * 3, None)
            self.foliage.activate_fallen_tree(17, 9)
        return self.foliage.update_fallen_tree_pose(17, 9, center,
            ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.)))

    def test_unrelated_falling_tree_preserves_pending_then_ready_observation(self):
        self.request(1.)
        self.update_tree((1000., 0., 1000.))
        self.assertIsNone(self.request(1.2))
        self.update_tree((1001., 0., 1000.))
        self.backend.finish_visibility(1)
        result = self.request(1.4)
        self.assertEqual(result['sampled_at'], 1.)
        self.assertEqual(self.backend.cancelled, [])
        self.assertEqual(self.owner.submitted, 1)
        self.assertEqual(self.owner.completed, 1)

    def test_tree_entering_sight_cells_rejects_ready_observation(self):
        self.update_tree((1000., 0., 1000.))
        self.request(1.)
        self.backend.finish_visibility(1)
        self.update_tree((60., 0., 0.))
        self.assertIsNone(self.request(1.2))
        self.assertEqual(self.queries, [])
        self.assertEqual(self.owner.cancellation_reasons, {'foliage': 1})

    def test_tree_leaving_sight_cells_rejects_ready_observation(self):
        self.update_tree((60., 0., 0.))
        self.request(1.)
        self.update_tree((1000., 0., 1000.))
        self.assertIsNone(self.request(1.2))
        self.backend.finish_visibility(1)
        self.assertIsNone(self.request(1.4))
        self.assertEqual(self.queries, [])
        self.assertEqual(self.owner.cancellation_reasons, {'foliage': 1})

    def test_removed_standing_crown_invalidates_its_old_sight_cells(self):
        self.foliage.instances.append((60., -1., 0., 3., 1., 0., 0., 1., .2, 2.))
        self.foliage.cells[(1, 0)] = [0]
        self.foliage.fallen_tree_profiles[(17, 9)] = ((-1.,) * 3 + (1.,) * 3, 0)
        self.foliage.standing_fallen_tree_cells[0] = [(1, 0)]
        self.foliage.activate_fallen_tree(17, 9)
        self.request(1.)
        self.backend.finish_visibility(1)
        self.update_tree((1000., 0., 1000.))
        self.assertIsNone(self.request(1.2))
        self.assertEqual(self.queries, [])
        self.assertEqual(self.owner.cancellation_reasons, {'foliage': 1})

    def test_closed_cell_boundary_change_invalidates_ready_observation(self):
        self.request(1.)
        self.backend.finish_visibility(1)
        # The native supercover includes the cell below a ray on z == 0.
        self.foliage.native_revision += 1
        self.foliage.native_dirty_cells.add((1, -1))
        self.assertIsNone(self.request(1.2))
        self.assertEqual(self.queries, [])
        self.assertEqual(self.owner.cancellation_reasons, {'foliage': 1})

    def test_unrequested_cleanup_keeps_its_own_cancellation_reason(self):
        self.request(1.)
        self.owner._poll(11.1)
        self.assertEqual(self.owner.jobs, {})
        self.assertEqual(self.owner.cancellation_reasons, {'unrequested': 1})

    def test_close_cancels_context_once(self):
        self.request(1.)
        self.owner.close()
        self.owner.close()
        self.assertEqual(self.backend.closed, [1])
        self.assertEqual(self.owner.jobs, {})

    def test_rejected_foliage_update_preserves_changes_for_retry(self):
        self.foliage.native_revision += 1
        self.foliage.native_dirty_cells.add((1, 2))
        self.backend.vis_update = mock.Mock(return_value=False)
        with self.assertRaisesRegex(RuntimeError, 'not accepted'):
            self.request(1.)
        self.assertEqual(self.owner.revision, 0)
        self.assertEqual(self.foliage.native_dirty_cells, {(1, 2)})
        self.assertEqual(self.backend.visibility_inputs, {})
        self.backend.vis_update.return_value = True
        self.assertIsNone(self.request(1.2))
        self.assertEqual(self.owner.revision, 1)
        self.assertEqual(self.foliage.native_dirty_cells, set())

    def test_new_foliage_volumes_are_appended_in_index_order(self):
        # A set's iteration order cannot define append order in the native map.
        self.foliage.instances = [(0., 0., 0., 1., 1., 1., .2)] * 3
        self.foliage.native_dirty_instances = [2, 0, 1]
        self.foliage.native_revision += 1
        self.backend.vis_update = mock.Mock(return_value=True)
        self.request(1.)
        rows = self.backend.vis_update.call_args.args[1]
        self.assertEqual([row[0] for row in rows], [0, 1, 2])


class VisibilityCallerTests(unittest.TestCase):
    def setUp(self):
        self.probe = mock.Mock(return_value=None)
        self.runtime = BotRuntime(1, visibility_async_probe=self.probe)
        self.runtime._source_view_range = lambda *args: 400.
        self.runtime._target_detection_projection = lambda *args: ((.1, .2), .5, {}, False, 0., 1.)
        self.source = dict(kind='bot', id=1, x=0., y=0., z=0.)
        self.target = dict(kind='bot', id=2, network_id=2,
                           position=(120., 0., 0.), fire_seq=0)

    def test_pending_does_not_publish_or_replace_a_cached_observation(self):
        key = ('bot', 1, 'bot', 2)
        old = (.5, True, 0)
        self.runtime._visibility_cache[key] = old
        with mock.patch.object(self.runtime, '_visibility_probe_completed') as complete:
            self.assertFalse(self.runtime._visible(self.source, self.target, 1.))
        complete.assert_not_called()
        self.assertEqual(self.runtime._visibility_cache[key], old)

    def test_completed_observation_keeps_sample_time_for_cache_and_spot_memory(self):
        self.probe.return_value = dict(detected=True, sampled_at=1.,
                                       line_of_sight=True, foliage_bonus=0.)
        self.assertTrue(self.runtime._visible(self.source, self.target, 1.296))
        sampled_at = self.runtime._visibility_sample_time(self.source, self.target, 1.296)
        self.assertEqual(sampled_at, 1.)
        key = (1, 'bot', 2)
        self.runtime._renew_team_spot(key, sampled_at)
        self.assertEqual(self.runtime._spot_until[key], 11.)

    def test_delayed_observation_does_not_renew_radio_or_team_freshness(self):
        self.probe.return_value = dict(detected=True, sampled_at=1.,
                                       line_of_sight=True, foliage_bonus=0.)
        self.assertTrue(self.runtime._visible(self.source, self.target, 1.6))
        sampled_at = self.runtime._visibility_sample_time(self.source, self.target, 1.6)
        self.runtime._renew_team_spot((1, 'bot', 2), sampled_at)
        self.runtime._renew_observer_spot(self.source, self.target, sampled_at)
        remaining, fresh, unused_pose = self.runtime._radio_network.contact(
            ('bot', 1), ('bot', 2), 1.6)
        self.assertAlmostEqual(remaining, 9.4)
        self.assertFalse(fresh)
        self.assertAlmostEqual(self.runtime._team_spot_time_left((1, 'bot', 2), 1.6), 9.4)

    def test_preparation_and_engine_queries_have_independent_bounded_slots(self):
        pending, prepared, queried = {}, [], []
        def probe(source, target, fired, now, sequence, detection):
            key = (source['id'], target['id'])
            if key not in pending:
                pending[key] = now
                prepared.append(key)
                return None
            queried.append(key)
            return dict(detected=True, sampled_at=pending.pop(key),
                        line_of_sight=True, foliage_bonus=0.)
        self.runtime.visibility_async_probe = probe
        bodies = [dict(id=index + 1, network_id=index + 1, slot=index % 2,
                       kind='bot', team=1 if index < 2 else 2, alive=True,
                       x=0. if index < 2 else 120., y=0., z=float(index),
                       position=(0. if index < 2 else 120., 0., float(index)),
                       speed=0., fire_seq=0) for index in range(4)]
        self.runtime.states = {body['id']: body for body in bodies}
        counts = []
        with mock.patch('gui.mods.offline_lan_0922.bot_runtime.MAX_VISIBILITY_PROBES_PER_FRAME', 2):
            for frame in range(6):
                before = len(prepared), len(queried)
                now = 1. + frame * .296
                self.runtime._begin_visibility_frame()
                self.runtime._prepare_visibility_frame([], now, False)
                for source in bodies:
                    for target in bodies:
                        if source['team'] != target['team']:
                            self.runtime._visible(source, target, now)
                self.runtime._finish_visibility_frame()
                counts.append((len(prepared) - before[0], len(queried) - before[1]))
        self.assertEqual(counts, [(2, 0)] + [(2, 2)] * 5)
        self.assertEqual(len(set(queried)), 8)


if __name__ == '__main__':
    unittest.main()
