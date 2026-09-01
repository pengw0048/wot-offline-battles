from __future__ import print_function

"""Engine-free translation of LAN v5 snapshots into entity lifecycle data."""

import math


PREDICTION_SECONDS = 0.05
# BotRuntime keeps every copied-physics slice at or below 0.2 seconds after a
# delayed callback. Keep the derived diagnostic velocity on that same span;
# timed presentation below interpolates confirmed samples and does not use it.
MAX_TIMED_PREDICTION_SECONDS = 0.2
MAX_INITIAL_TIMED_INTERVAL_US = int(
    MAX_TIMED_PREDICTION_SECONDS * 1000000.0)
# With the QPC worker clock fixed, unloaded authority gaps are normally
# 40-50 ms and top out around 66 ms. Start with 90 ms of confirmed history;
# the adaptive observed-delay term can still rise to roughly 99 ms for that
# worst gap plus one 30 Hz snapshot exposure. A fully engaged 22-bot worker
# can later produce isolated 100-200 ms gaps, which the material-growth gate
# below absorbs when observed. Regular traffic still settles near 60-90 ms
# instead of retaining the old 110-170 ms startup cushion.
# Authority, hit resolution, local-player input and the confirmed-only
# presentation cursor are unchanged.
INITIAL_TIMED_DELAY_US = 90000.0
MIN_TIMED_DELAY_US = 60000.0
TIMED_DELAY_DECAY_RATIO = 0.005
# Ignore sub-frame changes in the measured target after warm-up.  Expanding a
# confirmed-only cursor cannot rewind it, so a tiny increase would itself add
# one visible hold.  Material producer stalls still grow the buffer on the
# first packet that proves the old high-water mark was insufficient.
TIMED_DELAY_GROW_DEADBAND_US = 15000.0
# A producer interval above 100 ms is a real authority stall, not ordinary
# 30 Hz snapshot exposure.  Grow even when the hold that just occurred has
# already raised the measured output latency close to the new ideal value.
TIMED_DELAY_GROW_STALL_US = 100000.0
# Build the confirmed-history high-water mark from the first few live source
# intervals before playback has consumed the initial cushion.  This avoids a
# series of early buffer underruns while preserving the steady-state latency
# curve for remote vehicles after warm-up.
TIMED_WARMUP_INTERVALS = 3
TIMED_WARMUP_HEADROOM_RATIO = 1.0 / 3.0
TIMED_WARMUP_MAX_HEADROOM_US = 20000.0
SNAP_DISTANCE = 25.0
MAX_VELOCITY = 80.0
MAX_MOTION_TIME_US = 10000000000000000


def _number(value, default=0.0):
    try:
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return float(default)
        return value
    except (TypeError, ValueError):
        return float(default)


def _exact_time_us(value):
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
        if float(value) != parsed or not 0 <= parsed <= MAX_MOTION_TIME_US:
            return None
        return parsed
    except (TypeError, ValueError, OverflowError):
        return None


def _angle_delta(current, target):
    """Return the shortest signed delta between two yaw angles."""
    return (target - current + math.pi) % (2.0 * math.pi) - math.pi


def _pose(state):
    if not isinstance(state, dict):
        return None
    if not all(key in state for key in ('x', 'y', 'z')):
        return None
    return {
        'x': _number(state['x']), 'y': _number(state['y']),
        'z': _number(state['z']), 'yaw': _number(state.get('yaw')),
        'pitch': _number(state.get('pitch')),
        'roll': _number(state.get('roll')),
        'aim_yaw': _number(state.get('aim_yaw', state.get('yaw'))),
        'gun_pitch': _number(state.get('gun_pitch')),
    }


def _entity_key(kind, state):
    if not isinstance(state, dict) or state.get('id') is None:
        return None
    return '%s:%s' % (kind, state['id'])


def _copy_state(state):
    return dict(state) if isinstance(state, dict) else {}


class SnapshotSync(object):
    """Keeps protocol ordering and remote smoothing outside BigWorld.

    ``on_event`` receives plain dictionaries and can be wired to a later entity
    binding.  No import in this module has an engine side effect.
    """

    def __init__(self, local_player_id=None, on_event=None, clock=None,
                 pose_safe=None):
        self.local_player_id = local_player_id
        self.on_event = on_event
        self._clock = clock
        self._pose_safe = pose_safe
        self.round_id = None
        self._last_sequence = None
        self._last_order_revision = None
        self._last_bot_state_revision = None
        self._last_motion_time_us = None
        self._last_bot_state_time_us = None
        self._timed_bot_poses = None
        self._last_timing_phase = None
        self._live_timeline_reset_pending = False
        self._entities = {}
        self._last_advance = None

    def _now(self):
        if self._clock is not None:
            return float(self._clock())
        import time
        return time.time()

    def _emit(self, event, output):
        output.append(event)
        if self.on_event is not None:
            self.on_event(event)

    def _reset_round(self, round_id):
        self.round_id = round_id
        self._last_sequence = None
        self._last_order_revision = None
        self._last_bot_state_revision = None
        self._last_motion_time_us = None
        self._last_bot_state_time_us = None
        self._timed_bot_poses = None
        self._last_timing_phase = None
        self._live_timeline_reset_pending = False
        self._entities = {}
        self._last_advance = None

    def manifest(self, message):
        """Consume a battle_start or roster message and emit missing creates."""
        message = message if isinstance(message, dict) else {}
        round_id = message.get('round_id')
        if round_id is not None and round_id != self.round_id:
            self._reset_round(round_id)
        output = []
        for kind, field in (('player', 'players'), ('bot', 'bots')):
            states = message.get(field) or []
            for state in states:
                key = _entity_key(kind, state)
                if key is None:
                    continue
                record = self._entities.get(key)
                if record is None:
                    record = {'kind': kind, 'id': state['id'], 'dead': False,
                              'current': None, 'target': None, 'velocity': (0.0, 0.0, 0.0),
                              'target_time': None,
                              'target_sample_time_us': None,
                              'motion_anchor_time_us': None,
                              'motion_anchor_local_time': None,
                              'snapshot_interval_us': None,
                              'interpolation_delay_us': None,
                              'presentation_delay_us': None,
                              'timed_warmup_intervals': 0,
                              'timed_warmup_active': False,
                              'timed_samples': [],
                              'presentation_time_us': None,
                              'timed_teleport': False,
                              'target_age': 0.0,
                              'timed_prediction': False}
                    self._entities[key] = record
                    self._emit({'type': 'create', 'entity': key, 'kind': kind,
                                'id': state['id'], 'state': _copy_state(state)}, output)
                else:
                    record['manifest'] = _copy_state(state)
        return output

    def _sequence(self, message):
        return message.get('sequence', message.get('server_tick'))

    def _set_remote_target(self, record, pose, now, sample_time_us=None,
                           sample_age=0.0, live_timeline_started=False):
        previous = record['target']
        previous_time = record['target_time']
        previous_sample_time_us = record.get('target_sample_time_us')
        velocity = (0.0, 0.0, 0.0)
        timed = sample_time_us is not None
        if (timed and previous is not None and
                previous_sample_time_us is not None and
                sample_time_us > previous_sample_time_us):
            # Use the authority receipt interval, not the tick on which a
            # 30 Hz server snapshot happened to expose the new revision.
            # Quantising an 18 Hz producer to those ticks alternates a steady
            # velocity between one- and two-tick estimates.
            delta = max(0.000001, min(
                (sample_time_us - previous_sample_time_us) / 1000000.0,
                MAX_TIMED_PREDICTION_SECONDS))
        elif (not timed and previous is not None and
              previous_time is not None and now > previous_time):
            delta = max(0.01, min(now - previous_time, 0.25))
        else:
            delta = None
        if delta is not None:
            velocity = ((pose['x'] - previous['x']) / delta,
                        (pose['y'] - previous['y']) / delta,
                        (pose['z'] - previous['z']) / delta)
            speed = math.sqrt(sum(part * part for part in velocity))
            if speed > MAX_VELOCITY:
                scale = MAX_VELOCITY / speed
                velocity = tuple(part * scale for part in velocity)
        stale_initial_anchor = bool(
            live_timeline_started and timed and previous is not None and
            previous_sample_time_us is not None and
            sample_time_us - previous_sample_time_us >
            MAX_INITIAL_TIMED_INTERVAL_US)
        timed_teleport = False
        if (timed and previous is not None and
                previous_sample_time_us is not None and
                sample_time_us > previous_sample_time_us and
                not stale_initial_anchor):
            sample_seconds = (
                sample_time_us - previous_sample_time_us) / 1000000.0
            sample_dx = pose['x'] - previous['x']
            sample_dy = pose['y'] - previous['y']
            sample_dz = pose['z'] - previous['z']
            sample_distance = math.sqrt(
                sample_dx * sample_dx + sample_dy * sample_dy +
                sample_dz * sample_dz)
            timed_teleport = bool(
                sample_distance > SNAP_DISTANCE and
                sample_distance / sample_seconds > MAX_VELOCITY)
        if timed:
            if stale_initial_anchor:
                # The first canonical bot pose is published during loading,
                # before the shared countdown.  Its next revision is the
                # first live simulation step, so the authority timestamps are
                # separated by the whole countdown even though the hull only
                # moved for one live tick.  Treat that revision as the live
                # timeline origin; replaying the intentional prebattle pause
                # as interpolation latency freezes every bot for 5-15 seconds
                # and eventually makes the 25 m teleport guard fire.
                record['interpolation_delay_us'] = None
                record['presentation_delay_us'] = None
                record['timed_warmup_intervals'] = 0
                record['timed_warmup_active'] = True
                record['timed_samples'] = []
                record['presentation_time_us'] = sample_time_us
            if previous is not None and previous_sample_time_us is not None:
                if not stale_initial_anchor:
                    record['timed_warmup_intervals'] = (
                        int(record.get('timed_warmup_intervals') or 0) + 1)
                    source_interval_us = (
                        sample_time_us - previous_sample_time_us)
                    observed_delay_us = (
                        source_interval_us +
                        (record.get('snapshot_interval_us') or 0))
                    if (record.get('timed_warmup_active') and
                            record['timed_warmup_intervals'] <=
                            TIMED_WARMUP_INTERVALS):
                        # The first intervals arrive while the initial buffer
                        # is still filling, so reserve a small measured-cadence
                        # margin now. Later outliers use their actual interval
                        # and cannot permanently inflate normal latency.
                        observed_delay_us += min(
                            TIMED_WARMUP_MAX_HEADROOM_US,
                            source_interval_us *
                            TIMED_WARMUP_HEADROOM_RATIO)
                    previous_delay_us = record.get(
                        'interpolation_delay_us')
                    if previous_delay_us is None:
                        retained_delay_us = INITIAL_TIMED_DELAY_US
                    else:
                        retained_delay_us = max(
                            MIN_TIMED_DELAY_US,
                            previous_delay_us -
                            (sample_time_us - previous_sample_time_us) *
                            TIMED_DELAY_DECAY_RATIO)
                    record['interpolation_delay_us'] = max(
                        observed_delay_us, retained_delay_us)
                    if record.get('presentation_delay_us') is None:
                        record['presentation_delay_us'] = \
                            record['interpolation_delay_us']
                    else:
                        delay_growth_us = (
                            record['interpolation_delay_us'] -
                            record['presentation_delay_us'])
                        warmup_growth = bool(
                            record.get('timed_warmup_active') and
                            record['timed_warmup_intervals'] <=
                            TIMED_WARMUP_INTERVALS)
                        material_growth = (
                            delay_growth_us >=
                            TIMED_DELAY_GROW_DEADBAND_US or
                            source_interval_us >=
                            TIMED_DELAY_GROW_STALL_US)
                        if (delay_growth_us > 0.0 and
                                (warmup_growth or material_growth)):
                            # A jitter buffer has to grow on the packet which
                            # proves its old high-water mark was too small. The
                            # former stock-style convergence was appropriate
                            # for shrinking latency, but when a loaded worker
                            # began producing 100-200 ms gaps it let confirmed
                            # playback exhaust the buffer on every later gap:
                            # stop, catch up, stop again. Growing immediately
                            # costs latency once; the existing decay path below
                            # still sheds that excess gradually after regular
                            # samples resume.
                            record['presentation_delay_us'] = \
                                record['interpolation_delay_us']
            else:
                record['interpolation_delay_us'] = None
            record['timed_samples'].append({
                'time_us': sample_time_us, 'pose': dict(pose)})
        record['target'] = pose
        record['velocity'] = velocity
        record['target_time'] = now
        record['target_sample_time_us'] = sample_time_us
        record['target_age'] = max(0.0, float(sample_age)) if timed else 0.0
        record['timed_prediction'] = timed
        record['timed_teleport'] = timed_teleport
        if record['current'] is None or stale_initial_anchor:
            record['current'] = dict(pose)
            return True
        return False

    @staticmethod
    def _start_live_timeline(record, time_us, local_time):
        """Re-anchor one confirmed countdown pose at the live phase edge."""
        pose = record.get('target') or record.get('current')
        if pose is None:
            return False
        pose = dict(pose)
        record['target'] = pose
        record['target_time'] = float(local_time)
        record['target_sample_time_us'] = int(time_us)
        record['motion_anchor_time_us'] = int(time_us)
        record['motion_anchor_local_time'] = float(local_time)
        record['snapshot_interval_us'] = None
        record['interpolation_delay_us'] = None
        record['presentation_delay_us'] = None
        record['timed_warmup_intervals'] = 0
        record['timed_warmup_active'] = True
        record['timed_samples'] = [{
            'time_us': int(time_us), 'pose': dict(pose)}]
        record['presentation_time_us'] = int(time_us)
        record['timed_teleport'] = False
        record['target_age'] = 0.0
        record['timed_prediction'] = True
        return True

    def _upsert(self, kind, state, now, output, update_remote_pose=True,
                sample_time_us=None, sample_age=0.0, motion_time_us=None,
                motion_anchor_local_time=None,
                live_timeline_started=False):
        key = _entity_key(kind, state)
        if key is None:
            return
        record = self._entities.get(key)
        if record is None:
            record = {'kind': kind, 'id': state['id'], 'dead': False,
                      'current': None, 'target': None,
                      'velocity': (0.0, 0.0, 0.0), 'target_time': None,
                      'target_sample_time_us': None,
                      'motion_anchor_time_us': None,
                      'motion_anchor_local_time': None,
                      'snapshot_interval_us': None,
                      'interpolation_delay_us': None,
                      'presentation_delay_us': None,
                      'timed_warmup_intervals': 0,
                      'timed_warmup_active': False,
                      'timed_samples': [],
                      'presentation_time_us': None,
                      'timed_teleport': False,
                      'target_age': 0.0, 'timed_prediction': False}
            self._entities[key] = record
            self._emit({'type': 'create', 'entity': key, 'kind': kind,
                        'id': state['id'], 'state': _copy_state(state)}, output)
        alive = bool(state.get('alive', True))
        pose = _pose(state)
        local = kind == 'player' and state.get('id') == self.local_player_id
        if not alive:
            if not record['dead']:
                record['dead'] = True
                if pose is not None:
                    record['current'] = dict(pose)
                self._emit({'type': 'destroy', 'entity': key, 'kind': kind,
                            'id': state['id'], 'reason': 'dead',
                            'keep_corpse': True, 'state': _copy_state(state)}, output)
            return
        if record['dead']:
            return
        if local:
            if pose is not None:
                record['current'] = dict(pose)
            self._emit({'type': 'update', 'entity': key, 'kind': kind,
                        'id': state['id'], 'state': _copy_state(state),
                        'pose': pose, 'correction': True}, output)
            return
        if sample_time_us is not None:
            previous_motion_time_us = record.get('motion_anchor_time_us')
            if (previous_motion_time_us is not None and
                    motion_time_us > previous_motion_time_us):
                record['snapshot_interval_us'] = (
                    motion_time_us - previous_motion_time_us)
            record['motion_anchor_time_us'] = motion_time_us
            record['motion_anchor_local_time'] = (
                now if motion_anchor_local_time is None else
                motion_anchor_local_time)
        update_remote_pose = bool(
            update_remote_pose or record['target'] is None)
        snapped = (self._set_remote_target(
                       record, pose, now, sample_time_us, sample_age,
                       live_timeline_started)
                   if pose is not None and update_remote_pose else False)
        target = (dict(record['target'])
                  if record['target'] is not None else None)
        # A network snapshot only changes the interpolation target.  Emitting
        # the already-drawn ``current`` pose here makes the presentation write
        # that same matrix between two render-frame ``advance`` calls.  At a
        # 20-30 Hz authority cadence those repeated writes insert a stationary
        # frame on every 30 Hz server snapshot and make otherwise smooth bots
        # visibly judder.  The first pose still has to materialise the entity;
        # after that, ``advance`` is the sole remote pose producer.
        current_pose = (dict(record['current'])
                        if snapped and record['current'] is not None else None)
        self._emit({'type': 'update', 'entity': key, 'kind': kind,
                    'id': state['id'], 'state': _copy_state(state),
                    'pose': current_pose,
                    'target': target, 'remote': True, 'snap': snapped}, output)

    def snapshot(self, message):
        """Translate one full snapshot, dropping stale rounds/sequences."""
        message = message if isinstance(message, dict) else {}
        round_id = message.get('round_id', self.round_id)
        if self.round_id is not None and round_id != self.round_id:
            return []
        sequence = self._sequence(message)
        if sequence is not None and self._last_sequence is not None and sequence <= self._last_sequence:
            return []
        update_bot_poses = True
        previous_revision = self._last_bot_state_revision
        revision = None
        if 'bot_state_revision' in message:
            raw_revision = message.get('bot_state_revision')
            try:
                revision = int(raw_revision)
                if (isinstance(raw_revision, bool) or revision < 0 or
                        float(raw_revision) != revision):
                    raise ValueError
            except (TypeError, ValueError, OverflowError):
                raise ValueError('bot_state_revision is invalid')
            if (self._last_bot_state_revision is not None and
                    revision < self._last_bot_state_revision):
                raise ValueError('bot_state_revision regressed')
            update_bot_poses = (
                previous_revision is None or
                revision > previous_revision)
        elif self._last_bot_state_revision is not None:
            raise ValueError('bot_state_revision disappeared')
        has_motion_time = 'motion_time_us' in message
        has_bot_state_time = 'bot_state_time_us' in message
        if has_motion_time != has_bot_state_time:
            raise ValueError('bot pose timing is incomplete')
        timed_bot_poses = has_motion_time
        motion_time_us = None
        bot_state_time_us = None
        if timed_bot_poses:
            motion_time_us = _exact_time_us(message.get('motion_time_us'))
            bot_state_time_us = _exact_time_us(
                message.get('bot_state_time_us'))
            if (motion_time_us is None or bot_state_time_us is None or
                    bot_state_time_us > motion_time_us):
                raise ValueError('bot pose timing is invalid')
            if (self._last_motion_time_us is not None and
                    motion_time_us < self._last_motion_time_us):
                raise ValueError('motion time regressed')
            if (self._last_bot_state_time_us is not None and
                    bot_state_time_us < self._last_bot_state_time_us):
                raise ValueError('bot state time regressed')
            if (previous_revision is not None and
                    revision == previous_revision and
                    self._last_bot_state_time_us is not None and
                    bot_state_time_us != self._last_bot_state_time_us):
                raise ValueError(
                    'unchanged bot revision changed its sample time')
            if (previous_revision is not None and
                    revision > previous_revision and
                    self._last_bot_state_time_us is not None and
                    bot_state_time_us <= self._last_bot_state_time_us):
                raise ValueError(
                    'advanced bot revision did not advance sample time')
        elif self._timed_bot_poses:
            raise ValueError('bot pose timing disappeared')

        # Commit the validated header atomically. A stale or malformed
        # snapshot must not advance one frontier and make the following good
        # snapshot look old.
        if sequence is not None:
            self._last_sequence = sequence
        if revision is not None:
            self._last_bot_state_revision = revision
        if timed_bot_poses:
            self._last_motion_time_us = motion_time_us
            self._last_bot_state_time_us = bot_state_time_us
            self._timed_bot_poses = True
        elif self._timed_bot_poses is None:
            # Old v5 servers remain readable.  A later timed snapshot may
            # upgrade this round, but timing may not disappear after that.
            self._timed_bot_poses = False
        timing = message.get('timing')
        timing_phase = (timing.get('phase')
                        if isinstance(timing, dict) else None)
        entered_battle = bool(
            timing_phase == 'battle' and
            self._last_timing_phase in ('loading', 'prebattle'))
        if entered_battle:
            # The phase transition and the first live bot revision need not be
            # published by the same server snapshot. The unchanged phase-edge
            # pose below can establish the live anchor; otherwise keep the
            # reset armed until an advanced timed sample can consume it.
            self._live_timeline_reset_pending = True
        live_timeline_started = bool(
            self._live_timeline_reset_pending and timed_bot_poses and
            update_bot_poses)
        if timing_phase in ('loading', 'prebattle', 'battle', 'finished'):
            self._last_timing_phase = timing_phase
        now = self._now()
        # LANClient receives on a socket thread but dispatches snapshots from
        # a 60 Hz BigWorld callback.  Anchor the server motion clock at the
        # actual receive instant; anchoring it at dispatch makes every varying
        # queue delay look like authority clock jitter.  Only the elapsed
        # duration crosses clock domains, never either clock's epoch.
        dispatch_delay = max(
            0.0, _number(message.get('_client_dispatch_delay'), 0.0))
        motion_anchor_local_time = now - dispatch_delay
        if (entered_battle and timed_bot_poses and not update_bot_poses):
            # The server may publish the phase edge before the first live Bot
            # revision. The unchanged canonical pose proves that the hull held
            # through this motion time, so make it the confirmed live anchor.
            # The next slow-worker sample can then form a real interpolation
            # segment instead of being mistaken for a countdown-spanning stale
            # anchor and snapping directly to its final pose.
            anchored = False
            for key, record in self._entities.items():
                if record.get('kind') != 'bot' or record.get('dead'):
                    continue
                anchored = self._start_live_timeline(
                    record, motion_time_us, motion_anchor_local_time) or anchored
            if anchored:
                self._live_timeline_reset_pending = False
                live_timeline_started = False
        output = []
        seen = set()
        for kind, field in (('player', 'players'), ('bot', 'bots')):
            for state in message.get(field) or []:
                key = _entity_key(kind, state)
                if key is not None:
                    seen.add(key)
                self._upsert(
                    kind, state, now, output,
                    update_remote_pose=(kind != 'bot' or update_bot_poses),
                    sample_time_us=(bot_state_time_us
                                    if kind == 'bot' and timed_bot_poses
                                    else None),
                    sample_age=((motion_time_us - bot_state_time_us) /
                                1000000.0
                                if kind == 'bot' and timed_bot_poses
                                else 0.0),
                    motion_time_us=(motion_time_us
                                    if kind == 'bot' and timed_bot_poses
                                    else None),
                    motion_anchor_local_time=(
                        motion_anchor_local_time
                        if kind == 'bot' and timed_bot_poses else None),
                    live_timeline_started=(
                        kind == 'bot' and live_timeline_started))
        if live_timeline_started:
            self._live_timeline_reset_pending = False
        for key, record in list(self._entities.items()):
            if key not in seen and not record['dead']:
                record['dead'] = True
                self._emit({'type': 'destroy', 'entity': key,
                            'kind': record['kind'], 'id': record['id'],
                            'reason': 'missing', 'keep_corpse': False}, output)
        revision = message.get('bot_order_revision')
        orders = message.get('bot_orders')
        if orders is not None and (self._last_order_revision is None or
                                   revision is None or revision > self._last_order_revision):
            self._last_order_revision = revision
            for order in orders:
                if isinstance(order, dict):
                    self._emit({'type': 'order', 'order': _copy_state(order),
                                'revision': revision}, output)
        return output

    def advance(self, now=None):
        """Return interpolated/predicted remote poses for one render frame."""
        now = self._now() if now is None else float(now)
        render_delta = (0.016 if self._last_advance is None else
                        max(0.0, now - self._last_advance))
        delta = max(0.001, min(render_delta, 0.1))
        self._last_advance = now
        alpha = 1.0 - math.exp(-20.0 * delta)
        output = []
        for key, record in self._entities.items():
            if record['dead'] or record['target'] is None or record['current'] is None:
                continue
            target = record['target']
            elapsed = max(0.0, now - record['target_time'])
            timed = record.get('timed_prediction')
            if timed and len(record.get('timed_samples') or ()) >= 2:
                samples = record['timed_samples']
                anchor_time_us = record.get('motion_anchor_time_us')
                anchor_local_time = record.get('motion_anchor_local_time')
                ideal_delay_us = record.get('interpolation_delay_us')
                delay_us = record.get('presentation_delay_us')
                authority_now_us = anchor_time_us + max(
                    0.0, now - anchor_local_time) * 1000000.0
                previous_presentation_time_us = record.get(
                    'presentation_time_us')
                if previous_presentation_time_us is None:
                    previous_presentation_time_us = samples[0]['time_us']
                # Holding at the newest confirmed sample during an authority
                # gap increases the effective latency even before the ideal
                # delay is recomputed. Feed that real output latency into the
                # same stock convergence law so it can later recover.
                delay_us = max(
                    delay_us,
                    authority_now_us - render_delta * 1000000.0 -
                    previous_presentation_time_us)
                delay_error_seconds = (
                    ideal_delay_us - delay_us) / 1000000.0
                latency_rate = min(
                    1.0, abs(delay_error_seconds) ** 2.0)
                if ideal_delay_us < delay_us:
                    # The stock quadratic curve becomes effectively static
                    # for small errors.  Match the jitter high-water decay so
                    # the startup cushion actually returns to its 60 ms
                    # floor, while limiting catch-up to 1.005x playback.
                    latency_rate = max(
                        latency_rate, TIMED_DELAY_DECAY_RATIO)
                delay_step_us = (
                    render_delta * latency_rate * 1000000.0)
                if ideal_delay_us > delay_us:
                    delay_us = min(
                        ideal_delay_us, delay_us + delay_step_us)
                else:
                    delay_us = max(
                        ideal_delay_us, delay_us - delay_step_us)
                record['presentation_delay_us'] = delay_us
                # Playback uses an adaptive confirmed-history high-water mark.
                # It may hold at the newest confirmed sample, but it never
                # extrapolates beyond it. A larger later interval cannot rewind
                # the presentation clock; a shorter one also cannot make the
                # clock catch up by more than the stock AvatarFilter latency
                # curve permits. Its default latency velocity is 1 and curve
                # power is 2, hence a 100 ms error catches up at only 1.01x
                # rather than jumping.
                maximum_presentation_time_us = (
                    previous_presentation_time_us +
                    render_delta * (1.0 + latency_rate) * 1000000.0)
                presentation_time_us = min(
                    samples[-1]['time_us'],
                    maximum_presentation_time_us,
                    max(authority_now_us - delay_us,
                        previous_presentation_time_us))
                record['presentation_time_us'] = presentation_time_us
                while (len(samples) > 2 and
                       presentation_time_us >= samples[1]['time_us']):
                    samples.pop(0)
                previous_sample = samples[0]
                target_sample = samples[-1]
                for index in range(1, len(samples)):
                    if presentation_time_us <= samples[index]['time_us']:
                        previous_sample = samples[index - 1]
                        target_sample = samples[index]
                        break
                previous = previous_sample['pose']
                segment_target = target_sample['pose']
                previous_time_us = previous_sample['time_us']
                sample_time_us = target_sample['time_us']
                span_us = max(1.0, sample_time_us - previous_time_us)
                progress = max(0.0, min(
                    (presentation_time_us - previous_time_us) / span_us,
                    1.0))
                desired = dict(previous)
                for axis in ('x', 'y', 'z'):
                    desired[axis] += (
                        segment_target[axis] - previous[axis]) * progress
                for axis in ('yaw', 'aim_yaw'):
                    desired[axis] += _angle_delta(
                        previous[axis], segment_target[axis]) * progress
                for axis in ('pitch', 'roll'):
                    desired[axis] += (
                        segment_target[axis] - previous[axis]) * progress
                desired['gun_pitch'] += (
                    segment_target['gun_pitch'] -
                    previous['gun_pitch']) * progress
            elif timed:
                # A late join or the first timed sample has no confirmed
                # segment to interpolate. Materialise that sample directly.
                desired = dict(target)
                if record.get('presentation_time_us') is None:
                    record['presentation_time_us'] = \
                        record.get('target_sample_time_us')
            else:
                predict = min(elapsed, PREDICTION_SECONDS)
                velocity = record['velocity']
                desired = dict(target)
                desired['x'] += velocity[0] * predict
                desired['y'] += velocity[1] * predict
                desired['z'] += velocity[2] * predict
            if (not timed and record['kind'] == 'bot' and
                    self._pose_safe is not None and
                    not self._pose_safe((desired['x'], desired['y'],
                                         desired['z']))):
                # Only legacy velocity prediction is speculative.  Timed
                # interpolation consists entirely of confirmed authority
                # samples, including a tank genuinely falling into water or
                # off a cliff, and must never be filtered by the baked graph.
                desired = dict(target)
            current = record['current']
            timed_teleport = bool(
                timed and record.get('timed_teleport', False))
            snap_target = target if timed_teleport else (
                desired if timed else target)
            target_dx = snap_target['x'] - current['x']
            target_dy = snap_target['y'] - current['y']
            target_dz = snap_target['z'] - current['z']
            if (target_dx * target_dx + target_dy * target_dy +
                    target_dz * target_dz >
                    SNAP_DISTANCE * SNAP_DISTANCE):
                current = dict(snap_target)
                if timed:
                    # A teleport establishes a new timeline origin. Retaining
                    # older delayed samples would interpolate back toward the
                    # pre-teleport path on the following render frame.
                    record['timed_samples'] = [{
                        'time_us': record['target_sample_time_us'],
                        'pose': dict(target)}]
                    record['presentation_time_us'] = (
                        record['target_sample_time_us'])
                    record['interpolation_delay_us'] = None
                    record['presentation_delay_us'] = None
                    record['timed_warmup_intervals'] = 0
                    record['timed_warmup_active'] = False
                    record['timed_teleport'] = False
                snapped = True
            elif timed:
                # Timed poses are already interpolation samples. Applying a
                # second exponential chase would add another variable delay.
                current = desired
                snapped = False
            else:
                current = dict(current)
                for axis in ('x', 'y', 'z'):
                    current[axis] += (desired[axis] - current[axis]) * alpha
                for axis in ('yaw', 'aim_yaw'):
                    current[axis] += _angle_delta(
                        current[axis], desired[axis]) * alpha
                for axis in ('pitch', 'roll'):
                    current[axis] += (
                        desired[axis] - current[axis]) * alpha
                current['gun_pitch'] += (
                    desired['gun_pitch'] - current['gun_pitch']) * alpha
                snapped = False
            record['current'] = current
            self._emit({'type': 'update', 'entity': key, 'kind': record['kind'],
                        'id': record['id'], 'pose': dict(current), 'remote': True,
                        'presentation_time_us': (
                            int(round(record['presentation_time_us']))
                            if timed and
                            record.get('presentation_time_us') is not None
                            else None),
                        'interpolated': True, 'snap': snapped}, output)
        return output
