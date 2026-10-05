"""Main-thread ownership of asynchronous native spotting computation."""
from __future__ import print_function

import sys

from gui.mods.offline_lan_0922 import native_math, shot_geometry, spotting
from gui.mods.offline_lan_0922.worker_diagnostics import count as combat_count


def _volume(row):
    if isinstance(row, dict):
        return (1, tuple(row['center']), tuple(tuple(axis) for axis in row['half_axes']),
                float(row['strength']), float(row['radius']))
    return (0, tuple(row))


def _pose(state):
    position = state.get('position') or (
        state.get('x', 0.0), state.get('y', 0.0), state.get('z', 0.0))
    yaw = float(state.get('yaw', 0.0))
    turret_yaw = state.get('turret_yaw', state.get('aim_yaw', yaw) - yaw)
    return (tuple(position), yaw, float(state.get('pitch', 0.0)),
            float(state.get('roll', 0.0)), float(turret_yaw))


class NativeVisibility(object):
    """Prepare complete pair geometry off-thread; retain ordered engine rays."""

    def __init__(self, backend, foliage, enabled):
        self.backend = backend
        self.foliage = foliage
        self.enabled = bool(enabled and foliage is not None)
        self.revision = getattr(foliage, 'native_revision', 0)
        self.context = backend.vis_open(self._map_inputs())
        if self.context is None:
            raise RuntimeError('native visibility map could not be opened')
        self.next_job_id = 0
        self.jobs = {}
        self.by_id = {}
        self.descriptors = {}
        self.last_poll = None
        self.closed = False
        self.submitted = 0
        self.completed = 0
        self.cancelled = 0
        self.worker_seconds = 0.0
        self.max_completion_age = 0.0
        self._clear_foliage_changes()
        sys.stdout.write('[Offline LAN 0.9.22] native asynchronous visibility active\n')

    @classmethod
    def create(cls, foliage, enabled):
        backend = native_math._load()
        if backend is None or not callable(getattr(backend, 'vis_open', None)):
            return None
        try:
            return cls(backend, foliage, enabled)
        except Exception as error:
            native_math._report_failure(error)
            return None

    def _map_inputs(self):
        if not self.enabled:
            return (False, 32.0, (), (), ())
        return (True, float(self.foliage.cell_size),
                tuple(_volume(row) for row in self.foliage.instances),
                tuple((cell[0], cell[1], tuple(indices))
                      for cell, indices in self.foliage.cells.items()),
                tuple(self.foliage.inactive_instances))

    def _clear_foliage_changes(self):
        if self.foliage is not None:
            self.foliage.native_dirty_instances.clear()
            self.foliage.native_dirty_cells.clear()

    def _sync_foliage(self):
        if not self.enabled:
            return
        revision = self.foliage.native_revision
        if revision == self.revision:
            return
        rows = tuple((index, _volume(self.foliage.instances[index]))
                     for index in sorted(self.foliage.native_dirty_instances))
        cells = tuple((cell[0], cell[1], tuple(self.foliage.cells.get(cell, ())))
                      for cell in self.foliage.native_dirty_cells)
        if not self.backend.vis_update(self.context, rows, cells,
                                       tuple(self.foliage.inactive_instances)):
            raise RuntimeError('native foliage update was not accepted')
        self.revision = revision
        self._clear_foliage_changes()

    def _checkpoints(self, descriptor):
        field = shot_geometry._field
        chassis = field(descriptor, 'chassis', {})
        hull = field(descriptor, 'hull', {})
        turret = field(descriptor, 'turret', {})
        ready = (field(field(hull, 'hitTester'), 'bbox') is not None,
                 field(field(turret, 'hitTester'), 'bbox') is not None)
        cached = self.descriptors.get(id(descriptor))
        if cached is not None and cached[0] is descriptor and cached[1] == ready:
            return cached[2]
        points = tuple(shot_geometry._box_point(point)
                       for point in spotting.descriptor_check_points(descriptor))
        mount = None
        try:
            hp = shot_geometry._box_point(field(chassis, 'hullPosition'))
            tp = shot_geometry._box_point(field(hull, 'turretPositions')[0])
            mount = tuple(hp[index] + tp[index] for index in range(3))
        except (TypeError, ValueError, IndexError, KeyError):
            pass
        static_yaw = field(field(descriptor, 'gun', {}), 'staticTurretYaw')
        result = (points, mount, None if static_yaw is None else float(static_yaw))
        self.descriptors[id(descriptor)] = (descriptor, ready, result)
        return result

    def _cancel(self, key):
        job = self.jobs.pop(key, None)
        if job is None:
            return
        self.by_id.pop(job['id'], None)
        self.backend.vis_cancel(self.context, (job['id'],))
        self.cancelled += 1
        combat_count('visibility_async_cancelled')

    def _poll(self, now):
        # All pair requests in a control slice share one nonblocking drain.
        if self.last_poll == float(now):
            return
        self.last_poll = float(now)
        for job_id, status, rays, worker_seconds in self.backend.vis_poll(self.context):
            key = self.by_id.get(job_id)
            job = self.jobs.get(key)
            if job is None or job['id'] != job_id:
                continue
            job['status'] = status
            job['rays'] = rays
            self.worker_seconds += float(worker_seconds)
        # Removed actors or pairs that left spotting range cannot retain an
        # unconsumed prepared result for the rest of the round.
        for key, job in tuple(self.jobs.items()):
            if float(now) - job['last_requested'] > spotting.SPOT_MEMORY_SECONDS:
                self._cancel(key)

    def request(self, key, identity, observer, target, descriptors, phase,
                detection, now, fire_sequence, query_ray):
        """Return a frozen observation, or None while its CPU stage is pending."""
        if self.closed:
            raise RuntimeError('native visibility context is closed')
        self._sync_foliage()
        self._poll(now)
        # Preserve a delayed pose sample, but never carry an old crew/module,
        # moving or firing camouflage state into a different detection state.
        token = (tuple(id(owner) for owner in identity), fire_sequence,
                 self.revision, tuple(detection[1:]))
        job = self.jobs.get(key)
        if job is not None and (job['token'] != token or
                float(now) - job['sampled_at'] >= spotting.SHOT_CAMOUFLAGE_SECONDS):
            # A CPU observation older than the shortest camouflage-effect
            # window cannot be published as a new spot. Unlike the 6 Hz cache
            # TTL, this permits one delayed callback on the loaded worker.
            self._cancel(key)
            job = None
        if job is None:
            self.next_job_id += 1
            job_id = self.next_job_id
            pair = (self._checkpoints(descriptors[0]), self._checkpoints(descriptors[1]),
                    _pose(observer), _pose(target), int(phase), tuple(detection))
            if not self.backend.vis_submit(self.context, job_id, pair):
                raise RuntimeError('native visibility job was not accepted')
            job = {'id': job_id, 'token': token, 'owners': identity,
                   'sampled_at': float(now),
                   'last_requested': float(now), 'status': 'pending', 'rays': ()}
            self.jobs[key] = job
            self.by_id[job_id] = key
            self.submitted += 1
            combat_count('visibility_async_submitted')
            return None
        job['last_requested'] = float(now)
        if job['status'] == 'pending':
            combat_count('visibility_async_pending')
            return None
        if job['status'] != 'done':
            self._cancel(key)
            return None
        clear_prefix = []
        for start, end, cover in job['rays']:
            clear = bool(query_ray(start, end))
            clear_prefix.append(clear)
            if clear and cover <= 0.0:
                break
        los, cover, camouflage, detected, threshold = self.backend.vis_reduce(
            self.context, job['id'], tuple(clear_prefix))
        self.jobs.pop(key, None)
        self.by_id.pop(job['id'], None)
        self.completed += 1
        self.max_completion_age = max(
            self.max_completion_age, max(0.0, float(now) - job['sampled_at']))
        combat_count('visibility_async_completed')
        return {'line_of_sight': bool(los), 'foliage_bonus': float(cover),
                'camouflage': float(camouflage), 'detected': bool(detected),
                'sampled_at': job['sampled_at'], 'detection_distance': float(threshold)}

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.jobs.clear()
        self.by_id.clear()
        self.descriptors.clear()
        self.backend.vis_close(self.context)

    def snapshot(self):
        return {'submitted': self.submitted, 'completed': self.completed,
                'cancelled': self.cancelled, 'pending': len(self.jobs),
                'worker_seconds': self.worker_seconds,
                'max_completion_age': self.max_completion_age}
