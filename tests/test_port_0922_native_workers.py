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

    def test_fire_actor_and_foliage_changes_reject_ready_results(self):
        self.request(1.)
        self.backend.finish_visibility(1)
        self.assertIsNone(self.request(1.2, fire_sequence=1))
        self.backend.finish_visibility(2)
        self.assertIsNone(self.request(1.4, fire_sequence=1, identities=(object(), object())))
        self.backend.finish_visibility(3)
        self.foliage.native_revision += 1
        self.assertIsNone(self.request(1.6, fire_sequence=1))
        self.assertEqual(self.queries, [])
        self.assertEqual(self.backend.cancelled, [1, 2, 3])

    def test_same_fire_sequence_state_change_rejects_old_camouflage(self):
        recent = list(self.detection)
        recent[4] = True
        self.request(1., fire_sequence=1, detection=tuple(recent))
        self.backend.finish_visibility(1)
        self.assertIsNone(self.request(1.2, fire_sequence=1))
        self.assertEqual(self.queries, [])

    def test_continually_requested_sample_expires_before_native_queries(self):
        self.request(1.)
        self.request(1.3)
        self.backend.finish_visibility(1)
        self.assertIsNone(self.request(1.75))
        self.assertEqual(self.queries, [])
        self.assertEqual(self.backend.cancelled, [1])

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
