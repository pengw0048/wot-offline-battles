"""Ownership contracts for the complete native sight frame frontier."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)),
                             'src', 'res', 'scripts', 'client'))
if sys.version_info[0] < 3:
    package_path = os.path.join(sys.path[0], 'gui', 'mods', 'offline_lan_0922')
    for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        package = types.ModuleType(name)
        package.__path__ = [package_path]
        sys.modules[name] = package
from gui.mods.offline_lan_0922 import battle_visibility as subject


class Box(object):
    def __init__(self, **values):
        self.__dict__.update(values)


class Backend(object):
    def __init__(self):
        self.frames = []
        self.actors = []
        self.accept = True
        self.requests = 0
        self.on_actor = None

    def vis_frontier_frame(self, context, actors, phase, now):
        self.frames.append((context, actors, phase, now))
        return self.accept

    def vis_frontier_actor(self, context, actor):
        if self.on_actor:
            self.on_actor()
        self.actors.append((context, actor))
        return self.accept

    def vis_frontier_snapshot(self, context):
        return (1, 0, 0, 1, .1, 0., (0,) * 7, self.requests)


class Engine(object):
    creations = 0
    def __init__(self, *args, **kw):
        Engine.creations += 1
        self.capabilities = ('engine',)
        self._error = None

    def _invoke(self, function, *args):
        return function(*args)

    def _live(self):
        pass


class Owner(object):
    def _bot_visibility_async(self, *args):
        raise AssertionError('legacy pair callback must not be called')

    def _optional_feature_enabled(self, name):
        return False

    def _server_entity(self, identity):
        return self.entities.get(identity)

    def _sight_collision_filter(self):
        return self.filter

    def _turret_server_time_ms(self):
        return 4200

# Model the exact production bound-method recognition, without importing the
# engine runtime into this focused, engine-free owner contract.
getattr(Owner._bot_visibility_async, 'im_func', Owner._bot_visibility_async).__module__ = 'gui.mods.offline_lan_0922.battle_runtime'


class VisibilityFrameTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.service = Box(backend=self.backend, context=77, closed=False,
                           foliage=None, enabled=False)
        self.service._sync_foliage = lambda: None
        self.service._checkpoints = lambda descriptor: ((), None, None)
        self.owner = Owner()
        self.owner._avatar = Box(spaceID=9)
        self.owner._bots = Box(round_id=3)
        self.owner._generation = 4
        self.owner._foliage = None
        self.owner._destructibles = None
        self.owner.filter = object()
        self.owner._records = {'bot:1': {'engine_id': 11}, 'bot:2': {'engine_id': 12}}
        self.owner.entities = {11: Box(typeDescriptor=object()), 12: Box(typeDescriptor=object())}
        self.owner._native_visibility = self.service
        self.owner._native_visibility_owner = (4, 3, 9)
        self.source = dict(id=1, position=(3., 0., 0.), yaw=.2)
        self.target = dict(id=2, position=(90., 0., 0.), yaw=.4)
        self.runtime = Box(visibility_async_probe=self.owner._bot_visibility_async,
                           states={1: self.source, 2: self.target})
        self.control = Box(runtime=self.runtime, _sources={(1, 1): self.source,
                            (1, 2): self.target}, _templates={(1, 1): self.source,
                            (1, 2): dict(self.target, position=(80., 0., 0.))}, _now=1.)
        self.module_name = 'gui.mods.offline_lan_0922.native_engine_query'
        self.old_module = sys.modules.get(self.module_name)
        fake = types.ModuleType(self.module_name)
        fake.EngineQuery = Engine
        sys.modules[self.module_name] = fake
        Engine.creations = 0

    def tearDown(self):
        if self.old_module is None:
            sys.modules.pop(self.module_name, None)
        else:
            sys.modules[self.module_name] = self.old_module

    def bind(self):
        self.control._sight_binding = subject.bind_visibility(self.control)
        return self.control._visibility_frontier

    def test_frame_has_live_observer_and_exact_target_template_pose(self):
        self.bind()
        rows = dict((row[0], row) for row in self.backend.frames[-1][1])
        self.assertEqual(rows[(1, 2)][3][0], (90., 0., 0.))
        self.assertEqual(rows[(1, 2)][4][0], (80., 0., 0.))
        self.assertEqual(self.backend.frames[-1][2:], (0, 1.))

    def test_same_owners_keep_serial_and_engine_capabilities(self):
        frontier = self.bind()
        old = frontier.actors[(1, 1)][1]
        self.bind()
        self.assertEqual(frontier.actors[(1, 1)][1], old)
        self.assertEqual(Engine.creations, 1)

    def test_replacement_releases_identity_only_after_native_ack(self):
        frontier = self.bind()
        old = frontier.actors[(1, 1)]
        self.owner.entities[11] = Box(typeDescriptor=object())
        self.backend.on_actor = lambda: self.assertIs(frontier.actors[(1, 1)], old)
        subject.update_visibility(self.control, (1, 1))
        self.assertGreater(frontier.actors[(1, 1)][1], old[1])

    def test_rejected_actor_update_preserves_old_strong_identity(self):
        frontier = self.bind()
        old = frontier.actors[(1, 1)]
        self.backend.accept = False
        with self.assertRaises(RuntimeError):
            subject.update_visibility(self.control, (1, 1))
        self.assertIs(frontier.actors[(1, 1)], old)

    def test_disappearance_removes_after_ack(self):
        frontier = self.bind()
        del self.runtime.states[1]
        subject.update_visibility(self.control, (1, 1))
        self.assertEqual(self.backend.actors[-1][1], ((1, 1), 0, None, None, None, False))
        self.assertNotIn((1, 1), frontier.actors)

    def test_dead_last_effort_source_is_not_reinstalled_as_target(self):
        frontier = self.bind()
        del self.control._templates[(1, 1)]
        subject.update_visibility(self.control, (1, 1))
        row = self.backend.actors[-1][1]
        self.assertEqual(row[3][0], self.source['position'])
        self.assertFalse(row[5])
        self.assertIn((1, 1), frontier.actors)

    def test_detach_is_idempotent_and_does_not_close_shared_service(self):
        frontier = self.bind()
        subject.close_visibility(self.control)
        self.assertEqual(self.backend.frames[-1][1], ())
        self.assertFalse(self.service.closed)
        self.assertEqual(frontier.actors, {})
        self.assertIsNone(self.control._sight_binding)
        count = len(self.backend.frames)
        subject.close_visibility(self.control)
        self.assertEqual(len(self.backend.frames), count)

    def test_expired_avatar_owner_is_rejected(self):
        frontier = self.bind()
        self.owner._avatar = Box(spaceID=9)
        with self.assertRaises(RuntimeError):
            frontier.bind()

    def test_custom_bound_probe_retains_legacy_contract_seam(self):
        class Other(object):
            def probe(self):
                pass
        self.runtime.visibility_async_probe = Other().probe
        self.assertIsNone(subject.bind_visibility(self.control))

    def test_requests_are_reported_once_and_service_replacement_resets_delta(self):
        self.bind()
        self.backend.requests = 7
        self.assertEqual(subject.flush_visibility(self.control), 7)
        self.assertEqual(subject.flush_visibility(self.control), 0)
        self.backend.requests = 10
        self.assertEqual(subject.flush_visibility(self.control), 3)
        subject.close_visibility(self.control)
        self.assertEqual(subject.flush_visibility(self.control), 0)

    def test_new_control_shared_service_does_not_repeat_previous_requests(self):
        self.bind()
        self.backend.requests = 7
        self.assertEqual(subject.flush_visibility(self.control), 7)
        subject.close_visibility(self.control)
        self.bind()
        self.assertEqual(subject.flush_visibility(self.control), 0)
        self.backend.requests = 9
        self.assertEqual(subject.flush_visibility(self.control), 2)

    def test_failed_counter_read_does_not_change_jobs_and_recovers_delta(self):
        self.bind()
        self.backend.requests = 3
        self.assertEqual(subject.flush_visibility(self.control), 3)
        original = self.backend.vis_frontier_snapshot
        self.backend.vis_frontier_snapshot = lambda context: None
        self.backend.requests = 6
        self.assertEqual(subject.flush_visibility(self.control), 0)
        def failure(context):
            raise ValueError('diagnostic snapshot failure')
        self.backend.vis_frontier_snapshot = failure
        self.assertEqual(subject.flush_visibility(self.control), 0)
        self.backend.vis_frontier_snapshot = original
        self.assertEqual(subject.flush_visibility(self.control), 3)
        self.assertEqual(subject.flush_visibility(self.control), 0)
        self.assertEqual(len(self.backend.frames), 1)

    def test_unknown_initial_counter_establishes_baseline_without_double_count(self):
        original = self.backend.vis_frontier_snapshot
        self.backend.vis_frontier_snapshot = lambda context: None
        self.bind()
        self.backend.requests = 9
        self.backend.vis_frontier_snapshot = original
        self.assertEqual(subject.flush_visibility(self.control), 0)
        self.backend.requests = 10
        self.assertEqual(subject.flush_visibility(self.control), 1)

    def test_same_frame_actor_update_syncs_foliage_before_native_update(self):
        self.bind()
        events = []
        self.service._sync_foliage = lambda: events.append('foliage')
        self.backend.on_actor = lambda: events.append('actor')
        subject.update_visibility(self.control, (1, 1))
        self.assertEqual(events, ['foliage', 'actor'])

    def test_new_job_clock_syncs_foliage_after_clock_evaluation(self):
        frontier = self.bind()
        events = []
        self.owner._turret_server_time_ms = lambda: events.append('clock') or 4200
        self.service._sync_foliage = lambda: events.append('foliage')
        self.owner._sight_collision_filter = lambda: events.append('filter') or self.owner.filter
        self.assertEqual(frontier.binding[6](), (self.owner.filter, 4200))
        self.assertEqual(events, ['filter', 'clock', 'foliage'])

    def test_each_pair_sync_capability_sees_prior_pair_foliage_change(self):
        frontier = self.bind()
        revisions = [0]
        seen = []
        self.service._sync_foliage = lambda: seen.append(revisions[0])
        frontier.binding[6]()
        revisions[0] = 1  # Earlier pair's real engine/contact callback.
        frontier.binding[6]()
        self.assertEqual(seen, [0, 1])

    def test_snapshot_merges_legacy_and_frontier_without_losing_owner_work(self):
        from gui.mods.offline_lan_0922.native_visibility import NativeVisibility
        self.service.frontier_active = True
        self.service.submitted, self.service.completed, self.service.cancelled = 3, 2, 1
        self.service.jobs = {'legacy': object()}
        self.service.worker_seconds, self.service.max_completion_age = .5, .1
        self.service.cancellation_reasons = {'identity': 1, 'custom': 2}
        self.backend.vis_frontier_snapshot = lambda context: (5, 4, 3, 1, .7, .3,
                                                              (1, 2, 0, 0, 0, 0, 0), 9)
        instance = NativeVisibility.__new__(NativeVisibility)
        instance.__dict__.update(self.service.__dict__)
        result = instance.snapshot()
        self.assertEqual((result['submitted'], result['completed'], result['cancelled'],
                          result['pending'], result['frontier_requests']), (8, 6, 4, 2, 9))
        self.assertAlmostEqual(result['worker_seconds'], 1.2)
        self.assertEqual(result['max_completion_age'], .3)
        self.assertEqual(result['cancellation_reasons'], {'identity': 2, 'fire': 2, 'custom': 2})


if __name__ == '__main__':
    unittest.main()
