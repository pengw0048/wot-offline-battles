"""Cold engine ownership and frame snapshots for the native sight frontier."""
import functools

from . import native_math, spotting
from .native_visibility import NativeVisibility, _pose
from .collision_flags import SIGHT_SKIP_FLAGS
from .worker_diagnostics import count as combat_count

try:
    _COUNTER_TYPES = (int, long)
except NameError:
    _COUNTER_TYPES = (int,)


def _requests(service):
    try:
        row = service.backend.vis_frontier_snapshot(service.context)
        if (not isinstance(row, (tuple, list)) or len(row) != 8 or
                not isinstance(row[7], _COUNTER_TYPES) or row[7] < 0):
            raise ValueError('native sight counter shape')
        return row[7]
    except Exception:
        try:
            combat_count('frontier_visibility_counters_unavailable')
        except Exception:
            pass
        return None


class Frontier(object):
    def __init__(self, owner, control, service):
        self.owner = owner
        self.control = control
        self.service = service
        self.avatar = owner._avatar
        self.bots = owner._bots
        self.owner_token = (owner._generation, self.bots.round_id,
                            self.avatar.spaceID)
        self.actors = {}
        self.next_identity = 0
        # The shared BattleRuntime service survives NativeControl detach.
        # Unknown is distinct from zero: the next successful read establishes
        # a baseline and cannot report a preceding control owner's requests.
        self.reported_requests = _requests(service)
        self.binding = None
        self.engine = None
        self.report = None

    def _actor(self, key):
        control = self.control
        source = (control.runtime.states.get(key[1]) if key[0] == 1 else
                  control._sources.get(key))
        target = control._templates.get(key)
        if source is None:
            return None
        name = '%s:%d' % ('bot' if key[0] == 1 else 'player', key[1])
        record = self.owner._records.get(name)
        entity = self.owner._server_entity(record['engine_id']) if record else None
        descriptor = getattr(entity, 'typeDescriptor', None)
        identity = (self.avatar, record, entity, descriptor)
        old = self.actors.get(key)
        if old is not None and all(a is b for a, b in zip(old[0], identity)):
            serial = old[1]
        else:
            self.next_identity += 1
            serial = self.next_identity
        return (identity, serial,
                (key, serial, self.service._checkpoints(descriptor),
                 _pose(source), _pose(source if target is None else target),
                 target is not None))

    def bind(self):
        owner = self.owner
        if (owner._avatar is not self.avatar or owner._bots is not self.bots or
                (owner._generation, self.bots.round_id, self.avatar.spaceID) !=
                self.owner_token or self.service.closed):
            raise RuntimeError('native sight frontier owner expired')
        self.service._sync_foliage()
        actors = {}
        for key in self.control._sources:
            row = self._actor(key)
            if row is not None:
                actors[key] = row
        phase = 0  # Production reads the exact clock only when admitting a job.
        if not self.service.backend.vis_frontier_frame(
                self.service.context, tuple(row[2] for row in actors.values()),
                phase, self.control._now):
            raise RuntimeError('native sight frame was not accepted')
        # Native cancellation acknowledges replacement before old strong owners
        # are released. No Python object is retained by a background job.
        self.actors = actors
        self.service.frontier_active = True
        from .native_engine_query import EngineQuery
        if self.engine is None:
            self.engine = EngineQuery(owner, self.service.backend,
                                      ray_label='native.sight.ray',
                                      skip_flags=SIGHT_SKIP_FLAGS)
        # Each pair failure is contained by the native control SightReply. A
        # retained diagnostic exception must not poison the next frame.
        self.engine._error = None
        report = getattr(owner._destructibles, 'report_sight_contact', None)
        self.report = report if callable(report) else None
        self.binding = (self.service.context, self.engine.capabilities, None,
                        functools.partial(self.engine._invoke, self._report) if self.report is not None else None,
                        self.avatar.spaceID, None,
                        functools.partial(self.engine._invoke, self._query_inputs),
                        spotting.SIGHT_END_TOLERANCE)
        return self.binding

    def _query_inputs(self):
        # Preserve the old per-pair order and filter cache's own expiry. An
        # earlier pair can change the live broken/foliage ledger in this call.
        self.engine._live()
        collision_filter = self.owner._sight_collision_filter()
        milliseconds = self.owner._turret_server_time_ms()
        self.service._sync_foliage()
        self.engine._live()
        return collision_filter, milliseconds

    def _report(self, *args):
        self.engine._live()
        result = self.report(*args)
        self.engine._live()
        return result

    def update(self, key):
        # Earlier actors can topple foliage within the same simulation frame.
        # Contacts for this source must see that accepted dirty footprint.
        self.service._sync_foliage()
        row = self._actor(key)
        if row is None:
            if not self.service.backend.vis_frontier_actor(
                    self.service.context, (key, 0, None, None, None, False)):
                raise RuntimeError('native sight actor removal was not accepted')
            self.actors.pop(key, None)
            return
        if not self.service.backend.vis_frontier_actor(self.service.context, row[2]):
            raise RuntimeError('native sight actor was not accepted')
        self.actors[key] = row


def bind_visibility(control):
    """Bind only the actual BattleRuntime async spotting owner."""
    probe = control.runtime.visibility_async_probe
    owner = getattr(probe, 'im_self', getattr(probe, '__self__', None))
    function = getattr(probe, 'im_func', getattr(probe, '__func__', None))
    if (owner is None or function is None or
            function.__name__ != '_bot_visibility_async' or
            function.__module__ != 'gui.mods.offline_lan_0922.battle_runtime'):
        return None
    enabled = bool(owner._foliage is not None and
                   owner._optional_feature_enabled('foliage camouflage'))
    token = (owner._generation, owner._bots.round_id, owner._avatar.spaceID)
    service = getattr(owner, '_native_visibility', None)
    if service is not None and (service.foliage is not owner._foliage or
            service.enabled != enabled or
            getattr(owner, '_native_visibility_owner', None) != token):
        service.close()
        service = None
    if service is None:
        service = NativeVisibility.create(owner._foliage, enabled)
        owner._native_visibility = service
        owner._native_visibility_owner = token
    if service is None:
        raise RuntimeError('native sight frontier unavailable')
    if not all(callable(getattr(service.backend, name, None)) for name in
               ('vis_frontier_frame', 'vis_frontier_actor', 'vis_frontier_snapshot')):
        raise RuntimeError('native sight frontier method is missing')
    frontier = getattr(control, '_visibility_frontier', None)
    if (frontier is None or frontier.service is not service or
            frontier.owner is not owner):
        frontier = Frontier(owner, control, service)
        control._visibility_frontier = frontier
    return frontier.bind()


def update_visibility(control, key):
    frontier = getattr(control, '_visibility_frontier', None)
    if frontier is not None:
        frontier.update(key)


def close_visibility(control):
    """Cancel this control owner's jobs without closing BattleRuntime's map."""
    frontier = getattr(control, '_visibility_frontier', None)
    control._visibility_frontier = None
    control._sight_binding = None
    if frontier is None:
        return
    try:
        if not frontier.service.closed:
            if not frontier.service.backend.vis_frontier_frame(
                    frontier.service.context, (), 0, control._now):
                raise RuntimeError('native sight detach was not accepted')
    finally:
        frontier.binding = None
        frontier.engine = None
        frontier.report = None
        frontier.actors.clear()


def flush_visibility(control):
    """Report admitted probe attempts once, without inventing Python duration."""
    frontier = getattr(control, '_visibility_frontier', None)
    if frontier is None or frontier.service.closed:
        return 0
    requests = _requests(frontier.service)
    if requests is None:
        return 0
    previous = frontier.reported_requests
    frontier.reported_requests = requests
    return 0 if previous is None else max(0, requests - previous)
