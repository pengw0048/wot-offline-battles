from __future__ import print_function

"""Playable #1513 battle runtime built on stock Avatar and Vehicle entities."""

import base64
import bisect
import collections
import copy
import math
import os
import random
import sys
import time
import traceback

from gui.mods.offline_lan_0922.ai import maps as tactical_maps
from gui.mods.offline_lan_0922.ai import planner as bot_planner
from gui.mods.offline_lan_0922.ai.cover import score_candidates
from gui.mods.offline_lan_0922.artillery_controller import \
    ArtilleryController
from gui.mods.offline_lan_0922.authority_worker_probe import \
    AuthorityWorkerProbe, write_probe_record
from gui.mods.offline_lan_0922.battle_feedback import (
    SixthSenseController, VehicleStatePresenter)
from gui.mods.offline_lan_0922.bot_runtime import (
    BOT_WATER_AVOID_DEPTH, BotRuntime, PROBE_KINDS,
    WORKER_CONTROL_SECONDS)
from gui.mods.offline_lan_0922.entities.avatar_server import AvatarServerBridge
from gui.mods.offline_lan_0922.entities.bigworld_binding import \
    BigWorldVehicleBinding
from gui.mods.offline_lan_0922.entities.native_remote_vehicle import \
    NativeRemoteVehicleFactory, set_draw_visibility
from gui.mods.offline_lan_0922.entities.remote_vehicle import (
    RemoteVehicleFactory, _collide_vehicle_evidence_at_matrix,
    _component_aim_angles, _pose_components, collide_vehicle_at_matrix,
    encode_damage_sticker, pose_animation_writes,
    reset_pose_animation_writes)
from gui.mods.offline_lan_0922.entities.runtime import EntityPropertyBuilder
from gui.mods.offline_lan_0922.projectile_manager import InFlightProjectiles
from gui.mods.offline_lan_0922.projectile_runtime import (
    PROJECTILE_BROADPHASE_RADIUS, PROJECTILE_MAX_SUBSTEP_SECONDS, lerp3,
    ideal_reflection_velocity,
    point_in_expanded_segment_bounds, point_segment_distance_sq,
    projectile_range_distance, trajectory_position)
from gui.mods.offline_lan_0922.snapshot_sync import SnapshotSync
from gui.mods.offline_lan_0922.spawn_planner import SpawnPlanner
from gui.mods.offline_lan_0922 import (
    ballistics, combat_rules, critical_damage, descriptor_donation,
    destructibles_compat, effective_params, equipment_mechanics, gun_mechanics,
    hull_aiming, lan_client as lan_protocol,
    loadout as loadout_law, prebaked_destructibles, prebaked_foliage,
    prebaked_navigation, native_mapping_mask, shot_geometry, spotting,
    tank_collision,
    vehicle_blacklist, vehicle_configuration, vehicle_physics,
    world_collision)


# BigWorld callbacks run on rendered frames.  The mature 0.8.2 battle asks for
# the next frame explicitly; a positive 60 Hz delay can skip rendered frames
# and makes copied local physics, authority bots and remote interpolation step
# even while the renderer itself reports a healthy frame rate.
FRAME_SECONDS = 0.0
# Runtime profiling is observational. Its process-time clock never becomes
# simulation time and never feeds the fixed-control or A* schedulers.
PERFORMANCE_DIAGNOSTICS = True
WORKER_NATIVE_PROBE_SECONDS = 5.0
# Keep enough timing evidence for user-submitted logs without displacing
# lifecycle failures and tracebacks with a twelve-line report every five
# seconds. One half-minute window still catches sustained worker jitter.
DIAGNOSTIC_INITIAL_WINDOW_SECONDS = 5.0
DIAGNOSTIC_WINDOW_SECONDS = 30.0
DIAGNOSTIC_TOP_FRAMES = 3
# A normal 30-second window at 60-120 FPS fits entirely. Pathological high
# frame rates retain only the latest bounded sample set while exact maxima and
# threshold counts still cover the complete window.
DIAGNOSTIC_PERCENTILE_SAMPLES = 4096
_WORKER_DIAGNOSTIC_FIELDS = (
    'alive_bot_ticks', 'visibility_queue_depth',
    'visibility_queue_max_depth', 'visibility_oldest_stale_age_ms',
    'visibility_oldest_stale_max_age_ms', 'visibility_admitted',
    'visibility_completed', 'visibility_deferred',
    'visibility_selected_services', 'visibility_fire_services',
    'visibility_new_services', 'visibility_ordinary_services',
    'shot_lane_pending_pairs', 'shot_lane_pending_max_pairs',
    'shot_lane_oldest_due_age_ms', 'shot_lane_oldest_due_max_age_ms',
    'shot_lane_completed_pairs',
    'shot_lane_budget_deferred_attempts')
AMMO_SECONDS = 0.10
NETWORK_INPUT_SECONDS = 1.0 / 30.0
RPM_PRESENTATION_SECONDS = 0.10
SPOTTING_UPDATE_SECONDS = 0.10
SPOTTING_PROBE_SECONDS = 0.50
SPOTTING_PHASE_BUCKETS = 5
FALLEN_TREE_FOLIAGE_REFRESH_SECONDS = 0.10
FALLEN_TREE_FOLIAGE_STABLE_READS = 3
# Stock client code can republish the server half of a space visibility mask
# after the local map has entered the battle.  Read it infrequently and only
# write when it no longer selects this arena's gameplay.
SPACE_VISIBILITY_CHECK_SECONDS = 0.50
OPTIONAL_WARNING_TEXT_LIMIT = 160
DESTRUCTIBLE_UNAVAILABLE_REASON_LIMIT = 160

class _LiveSpaceVisibilityPending(Exception):
    """The mapped native space has not reached BigWorld.spaces yet."""


class _MutedShootingExtra(object):
    """Keep stock shot bookkeeping while suppressing one cosmetic extra."""

    def __init__(self, original):
        self._original = original

    def startFor(self, unused_vehicle, unused_burst_count):
        return None

    def stopFor(self, unused_vehicle):
        return None

    def __getattr__(self, name):
        return getattr(self._original, name)


# AvatarInputHandler._Targeting gives the native BigWorld.target these exact
# values.  The manual target adapter applies the static-world mouse-ray gate
# separately; the physical gun line is still irrelevant to an outline.
TARGET_SELECTION_FOV_DEGREES = 1.0
TARGET_DESELECTION_FOV_DEGREES = 80.0
TARGET_MAX_DISTANCE = 710.0
TARGET_OUTLINE_SECONDS = 0.05
# Bot tree/column enumeration is a proximity sensor, not presentation work.
# The sensor looks 6 m ahead plus the admitted hull extent, while copied bot
# speed is capped at 35 m/s.  Recheck within 0.10 s or 3 m of realised travel,
# whichever comes first, so no moving hull can skip the contact volume.
BOT_DESTRUCTIBLE_SECONDS = 0.10
BOT_DESTRUCTIBLE_TRAVEL_METRES = 3.0
BOT_SOFT_RECAST_BUDGET = 24
# A bot already beyond the shared baked/native limit may still choose an
# escape corridor below; otherwise the first wet sample traps it.
BOT_WATER_ESCAPE_DEEPEN_EPSILON = 0.10
CRITICAL_REPAIR_NETWORK_SECONDS = 1.0
# tankmen.xml commander_expert.delay in the pinned #1513 client.
EXPERT_DEVICE_DELAY_SECONDS = 4.0
PROJECTILE_PROGRESS_SECONDS = 0.10
PROJECTILE_MAX_TIME_MS = 20000
PROJECTILE_MAX_ACTIVE = 128
PROJECTILE_CHORDS_PER_FRAME = 32
PROJECTILE_MAX_CHORDS_PER_FRAME = 256
# A rotating target traces a curve in component-local space. Subdivide until
# every frozen component query spans at most one degree; reject pathological
# motion rather than silently treating one midpoint matrix as exact.
PROJECTILE_POSE_MAX_ANGLE_STEP = math.pi / 180.0
PROJECTILE_POSE_MAX_SWEEP_STEPS = 16
# Historic collision poses repeat across adjacent chords and simultaneous
# shots. Keep the synchronous advance cache bounded for the 32-bit worker.
PROJECTILE_POSE_CACHE_ENTRIES = 4096
# Broad-phase buckets contain the complete target-position envelope for one
# synchronous projectile advance.  The 64 m cell size comes from the
# contributed Alpha 6 implementation; unlike that implementation, the live
# index covers the oldest active cursor instead of assuming at most 0.5 s of
# projectile debt.
PROJECTILE_SPATIAL_CELL_METRES = 64.0
PROJECTILE_SPATIAL_MAX_TARGET_CELLS = 256
PROJECTILE_SPATIAL_MAX_TOTAL_CELLS = 8192
PROJECTILE_SPATIAL_MAX_QUERY_CELLS = 256
PROJECTILE_SPATIAL_EPSILON = 1.0e-6
# Size the fair global budget for the observed low-FPS boundary without ever
# exceeding the previous release's 256-chord hard cap.
PROJECTILE_SUSTAIN_SECONDS = 1.0 / 15.0
ARTILLERY_ARC_RAYS_PER_FRAME = 4
STANDARD_GAMEPLAY = 'ctf'
PREBATTLE_SECONDS = 15.0
BATTLE_SECONDS = 900.0
BOT_SPAWN_SECONDS = 0.30
BOT_MANIFEST_RETRY_SECONDS = 0.25
PLAYER_ENVIRONMENT_SECONDS = 0.3
MAX_PENDING_LANDING_IMPACTS = 32
_SHOT_EVENT_KINDS = ('shot', 'bot_shot')
_PROJECTILE_POSE_CACHE_MISS = object()
# Ordered kinds that carry no shot or combat contract.  An unknown kind still
# fails the round closed: the stream also carries health and kills, and a
# silently skipped authority event would desynchronise the battle.
_SIMPLE_EVENT_KINDS = (
    'authority', 'bot_manifest', 'vehicle_statistics', 'destructible',
    'projectile_ricochet', 'projectile_impact', 'battle_result', 'assist',
    'stun')
_COMBAT_EVENT_KINDS = (
    'health', 'hit', 'bot_hit', 'bot_human_hit', 'bot_bot_hit')
_SHOT_OCCLUSION_EPSILON = 1.0e-3
# physics_shared.TRACK_SCROLL_LIMITS: the exact #1513 belt-speed wire range.
TRACK_SCROLL_LIMITS = (-15.0, 30.0)
# Metres of view-range change worth another syncVehicleAttrs push.
VISION_PUBLISH_EPSILON = 0.5
# Seconds between two bot-track diagnostic lines.
TRACK_REPORT_SECONDS = 5.0
# The stock track controller advances at 20 Hz.  Remote hull interpolation
# remains render-rate; only the native belt feed is bounded to that cadence.
REMOTE_TRACK_PRESENTATION_SECONDS = 1.0 / 20.0
# Maximum vertical disagreement between the suspension probes before they are
# treated as different terrain layers rather than one drivable plane.
GROUND_PLANE_EPSILON = 0.35
# Above the old copied visual limit, smoothing itself can push a hull through
# a real steep slope.  Continuous terrain past this angle uses its raw pose.
GROUND_RAW_TILT_RADIANS = 0.61
# Give the pose animation a little longer than the measured gap so it is
# still interpolating when the next pose lands.
POSE_RELAX_STRETCH = 1.35
# PyTrackScroll zeroes both belts while engineMode[0] is at most 1.
ENGINE_MODE_OFF = 0
ENGINE_MODE_IDLE = 1
ENGINE_MODE_RUNNING = 2
# The stock #1513 descriptor converts XML movement bloom to per-m/s and
# per-rad/s factors. Feed those raw factors to PlayerAvatar unchanged: the
# native gun rotator owns the one dispersion state shared by HUD and shots.

# Exact #1513 ``Avatar._MOVEMENT_FLAGS`` values.  PlayerAvatar owns the R/F
# state machine and native cruise HUD; the local server only has to preserve
# the throttle encoded in each ``vehicle_moveWith(flags)`` mailbox call.
_MOVEMENT_FORWARD = 1
_MOVEMENT_BACKWARD = 2
_MOVEMENT_ROTATE_LEFT = 4
_MOVEMENT_ROTATE_RIGHT = 8
_MOVEMENT_CRUISE_CONTROL50 = 16
_MOVEMENT_CRUISE_CONTROL25 = 32
# VehicleGunRotator.__isOutOfLimits uses this exact #1513 angular epsilon
# when deciding whether a limited-traverse gun is already on either stop.
GUN_TRAVERSE_LIMIT_EPSILON = 1.0e-5
# Deadbands that decide whether a bot counts as moving or turning.
BOT_MOVING_SPEED = 0.05
BOT_TURNING_RATE = 0.02
_CRUISE_MODE_THROTTLE = {
    -2: -1.0,
    -1: -0.5,
    0: 0.0,
    1: 0.25,
    2: 0.5,
    3: 1.0,
}


def _monotonic_time():
    """Use the same non-adjustable clock domain as LANClient deadlines."""
    function = getattr(time, 'monotonic', None)
    if callable(function):
        return float(function())
    return float(time.clock())


# Ordered events arrive in order, so remembering this many recent ids rejects
# every realistic redelivery without growing for the whole round.
EVENT_ID_MEMORY = 8192

_PROFILE_CLOCK = getattr(time, 'perf_counter', None)
if not callable(_PROFILE_CLOCK):
    _PROFILE_CLOCK = time.clock


class _RecentIdSet(object):
    """Membership test over the most recent ids only.

    An ordered LAN event id is ``round:tick:ordinal`` and arrives in order, so
    an unbounded dedup set grows for the whole round on a client that already
    runs against a 2 GB address space.
    """

    def __init__(self, limit=EVENT_ID_MEMORY):
        self._limit = max(1, int(limit))
        self._ids = set()
        self._order = collections.deque()

    def add(self, value):
        if value in self._ids:
            return False
        self._ids.add(value)
        self._order.append(value)
        while len(self._order) > self._limit:
            self._ids.discard(self._order.popleft())
        return True

    def __contains__(self, value):
        return value in self._ids

    def __len__(self):
        return len(self._ids)


def _underlying_function(value):
    """Return a bound method's function so two bindings compare equal."""
    return getattr(value, 'im_func', getattr(value, '__func__', value))


def _format_xyz(value):
    """Render a Vector3 or a 3-sequence compactly for a diagnostic line."""
    try:
        x, y, z = _xyz(value)
        return '(%.1f, %.1f, %.1f)' % (x, y, z)
    except Exception:
        return repr(value)


_PORT_PACKAGE = 'gui.mods.offline_lan_0922'
# Sizing these adds nothing and walking a string character by character is slow.
_ATOMIC_TYPES = (bool, float, complex, bytes, bytearray)
try:
    _INTEGER_TYPES = (int, long)
    _STRING_TYPES = (basestring,)
    _ATOMIC_TYPES += (int, long, str, unicode)
except NameError:
    _INTEGER_TYPES = (int,)
    _STRING_TYPES = (str,)
    _ATOMIC_TYPES += (int, str)


def _deep_size(value, seen=None):
    """Approximate retained bytes, counting each object once.

    ``seen`` is shared across a whole ranking so an object reachable from two
    roots is charged to the first one only.  Instances are walked through
    ``__dict__``, because most of this port's state hides behind objects
    rather than behind bare containers.
    """
    if value is None:
        return 0
    if seen is None:
        seen = set()
    pending = [value]
    total = 0
    while pending:
        item = pending.pop()
        if item is None:
            continue
        marker = id(item)
        if marker in seen:
            continue
        seen.add(marker)
        try:
            total += sys.getsizeof(item, 64)
        except Exception:
            total += 64
        if isinstance(item, _ATOMIC_TYPES):
            continue
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
            continue
        if isinstance(item, (list, tuple, set, frozenset)):
            pending.extend(item)
            continue
        if not _is_port_object(item):
            continue
        members = getattr(item, '__dict__', None)
        if isinstance(members, dict):
            pending.append(members)
    return total


def _release_layout_caches():
    """Drop the geometry caches, which are module state and outlive a round."""
    released = False
    for module_name, releases in (
            ('internal_hit_layouts', ('clear_cache',
                                      'clear_runtime_evidence')),
            ('internal_geometry', ('clear_cache',))):
        module = sys.modules.get('%s.%s' % (_PORT_PACKAGE, module_name))
        if module is None:
            continue
        released = True
        for name in releases:
            release = getattr(module, name, None)
            if callable(release):
                try:
                    release()
                except Exception:
                    continue
    return released


def _is_port_object(value):
    """True for an instance of one of this port's own classes.

    The walk stops at anything else on purpose.  A BigWorld entity, a native
    model or a client module would drag the whole engine into the ranking, and
    touching a native attribute merely to size it is not worth the risk.
    """
    try:
        origin = getattr(type(value), '__module__', '')
    except Exception:
        return False
    return isinstance(origin, str) and origin.startswith(_PORT_PACKAGE)


_FRAME_STAGE_NAMES = (
    'house', 'sync', 'critical', 'drown', 'prewarm', 'transition', 'local',
    'outline', 'bots_update', 'bot_present', 'bot_events', 'spot', 'lock',
    'schedule', 'diag_emit')
_PROJECTILE_METRIC_NAMES = (
    'active', 'chords', 'debt', 'advance', 'terminals', 'scans',
    'candidates')


class _FrameDiagnostics(object):
    """Correlate one callback's work with the following render interval."""

    def __init__(self, clock=None, writer=None,
                 window_seconds=DIAGNOSTIC_WINDOW_SECONDS,
                 initial_window_seconds=None):
        self._clock = clock or _PROFILE_CLOCK
        self._writer = writer or sys.stdout.write
        self._steady_window_seconds = max(0.25, float(window_seconds))
        self._initial_window_seconds = max(0.25, float(
            self._steady_window_seconds if initial_window_seconds is None
            else initial_window_seconds))
        self.enabled = True
        self.reset()

    def reset(self):
        self._pending = None
        self._frame_id = 0
        self._window_id = 0
        self._window_seconds = self._initial_window_seconds
        self._last_snapshot = {}
        self._reset_window()

    def _reset_window(self):
        self._samples = 0
        self._window_elapsed = 0.0
        self._gap_sum = 0.0
        self._gap_max = 0.0
        self._raw_sum = 0.0
        self._raw_max = 0.0
        self._exec_sum = 0.0
        self._exec_max = 0.0
        self._outside_sum = 0.0
        self._outside_max = 0.0
        self._gap_samples = collections.deque(
            maxlen=DIAGNOSTIC_PERCENTILE_SAMPLES)
        self._exec_samples = collections.deque(
            maxlen=DIAGNOSTIC_PERCENTILE_SAMPLES)
        self._outside_samples = collections.deque(
            maxlen=DIAGNOSTIC_PERCENTILE_SAMPLES)
        self._distribution_samples = 0
        self._offframe_sum = 0.0
        self._offframe_max = 0.0
        self._load_busiest = ()
        self._collections = {}
        self._worker_runtime = {}
        self._stage_sums = dict((name, 0.0)
                                for name in _FRAME_STAGE_NAMES)
        self._stage_maxima = dict((name, 0.0)
                                  for name in _FRAME_STAGE_NAMES)
        self._probe_sums = dict((name, 0) for name in PROBE_KINDS)
        self._probe_maxima = dict((name, 0) for name in PROBE_KINDS)
        self._probe_duration_sums = dict(
            (name, 0.0) for name in PROBE_KINDS)
        self._probe_duration_maxima = dict(
            (name, 0.0) for name in PROBE_KINDS)
        self._projectile_sums = dict(
            (name, 0.0) for name in _PROJECTILE_METRIC_NAMES)
        self._projectile_maxima = dict(
            (name, 0.0) for name in _PROJECTILE_METRIC_NAMES)
        self._slow = []
        self._over_50 = 0
        self._over_67 = 0
        self._over_100 = 0
        self._sim_caps = 0
        self._clock_regressions = 0
        self._authority_frames = 0
        self._last_context = {}
        self._emit_due = False

    def _disable(self):
        self.enabled = False
        self._pending = None
        self._slow = []

    def begin(self, entry_wall, raw_dt, offframe=0.0):
        """Seal the previous callback using this callback's entry interval.

        ``offframe`` is the time this port's other scheduled callbacks spent
        inside that gap, so ``outside`` isolates work this port does not run.
        """
        self._frame_id += 1
        frame_id = self._frame_id
        if not self.enabled:
            return frame_id
        try:
            pending = self._pending
            if pending is not None:
                wall_gap = float(entry_wall) - pending['entry_wall']
                if wall_gap < 0.0:
                    wall_gap = 0.0
                    self._clock_regressions += 1
                observed_raw = float(raw_dt)
                if observed_raw < 0.0:
                    self._clock_regressions += 1
                off = max(0.0, float(offframe))
                row = dict(pending)
                row.update({
                    'next': frame_id,
                    'wall_gap': wall_gap,
                    'raw_dt': observed_raw,
                    'offframe': off,
                    'outside': max(0.0, wall_gap - pending['exec'] - off),
                    'bw_minus_wall': observed_raw - wall_gap,
                })
                self._add(row)
            return frame_id
        except Exception:
            self._disable()
            return frame_id

    def _add(self, row):
        self._samples += 1
        gap = row['wall_gap']
        raw_dt = row['raw_dt']
        execution = row['exec']
        outside = row['outside']
        self._window_elapsed += gap
        self._gap_sum += gap
        self._gap_max = max(self._gap_max, gap)
        self._raw_sum += raw_dt
        self._raw_max = max(self._raw_max, raw_dt)
        self._exec_sum += execution
        self._exec_max = max(self._exec_max, execution)
        self._outside_sum += outside
        self._outside_max = max(self._outside_max, outside)
        self._gap_samples.append(gap)
        self._exec_samples.append(execution)
        self._outside_samples.append(outside)
        self._distribution_samples += 1
        offframe = row.get('offframe', 0.0)
        self._offframe_sum += offframe
        self._offframe_max = max(self._offframe_max, offframe)
        if gap >= 0.050:
            self._over_50 += 1
        if gap >= 0.067:
            self._over_67 += 1
        if gap >= 0.100:
            self._over_100 += 1
        if raw_dt > 0.100:
            self._sim_caps += 1
        if row.get('context', {}).get('role') == 'authority':
            self._authority_frames += 1
        for name in _FRAME_STAGE_NAMES:
            value = max(0.0, float(row['stages'].get(name, 0.0)))
            self._stage_sums[name] += value
            self._stage_maxima[name] = max(
                self._stage_maxima[name], value)
        for name in PROBE_KINDS:
            value = max(0, int(row['probes'].get(name, 0)))
            self._probe_sums[name] += value
            self._probe_maxima[name] = max(
                self._probe_maxima[name], value)
            duration = max(
                0.0, float(row.get('probe_durations', {}).get(name, 0.0)))
            self._probe_duration_sums[name] += duration
            self._probe_duration_maxima[name] = max(
                self._probe_duration_maxima[name], duration)
        projectile = row.get('projectile') or {}
        for name in _PROJECTILE_METRIC_NAMES:
            value = max(0.0, float(projectile.get(name, 0.0)))
            self._projectile_sums[name] += value
            self._projectile_maxima[name] = max(
                self._projectile_maxima[name], value)
        self._last_context = dict(row.get('context') or {})
        score = (gap, execution)
        inserted = False
        for index, existing in enumerate(self._slow):
            if score > (existing['wall_gap'], existing['exec']):
                self._slow.insert(index, row)
                inserted = True
                break
        if not inserted:
            self._slow.append(row)
        if len(self._slow) > DIAGNOSTIC_TOP_FRAMES:
            del self._slow[DIAGNOSTIC_TOP_FRAMES:]
        if self._window_elapsed >= self._window_seconds:
            self._emit_due = True

    def emit_due(self):
        """Whether the next end() closes the window."""
        return bool(self.enabled and self._emit_due and self._samples)

    def note_collections(self, counts):
        """Record the per-round collection sizes for this window."""
        if not self.enabled or not isinstance(counts, dict):
            return False
        self._collections = dict(
            (str(name), int(value)) for name, value in counts.items())
        return True

    def note_bot_load(self, report):
        """Record the busiest bot planners of this window."""
        if not self.enabled or not isinstance(report, dict):
            return False
        self._load_busiest = tuple(report.get('busiest') or ())
        return True

    def note_worker_runtime(self, report):
        """Record one low-frequency fixed-control/presentation snapshot."""
        if not self.enabled or not isinstance(report, dict):
            return False
        self._worker_runtime = dict(report)
        return True

    @staticmethod
    def _milliseconds(value):
        return max(0.0, float(value)) * 1000.0

    @staticmethod
    def _percentile(sorted_values, fraction):
        if not sorted_values:
            return 0.0
        if len(sorted_values) == 1:
            return float(sorted_values[0])
        rank = max(0.0, min(1.0, float(fraction))) * (
            len(sorted_values) - 1)
        lower = int(math.floor(rank))
        upper = int(math.ceil(rank))
        if lower == upper:
            return float(sorted_values[lower])
        weight = rank - lower
        return (float(sorted_values[lower]) * (1.0 - weight) +
                float(sorted_values[upper]) * weight)

    def _distribution(self, samples, exact_maximum):
        values = sorted(samples)
        return {
            'p50': self._milliseconds(self._percentile(values, 0.50)),
            'p95': self._milliseconds(self._percentile(values, 0.95)),
            'p99': self._milliseconds(self._percentile(values, 0.99)),
            'max': self._milliseconds(exact_maximum),
        }

    def snapshot(self):
        """Return the most recently completed bounded performance window."""
        return dict(self._last_snapshot)

    def _format(self):
        samples = max(1, self._samples)
        elapsed = max(1e-9, self._window_elapsed)
        context = self._last_context
        self._window_id += 1
        gap_distribution = self._distribution(
            self._gap_samples, self._gap_max)
        exec_distribution = self._distribution(
            self._exec_samples, self._exec_max)
        outside_distribution = self._distribution(
            self._outside_samples, self._outside_max)
        stage_snapshot = {}
        for name in _FRAME_STAGE_NAMES:
            stage_snapshot[name] = {
                'avg_ms': self._milliseconds(
                    self._stage_sums[name] / samples),
                'max_ms': self._milliseconds(self._stage_maxima[name]),
            }
        probe_snapshot = {}
        for name in PROBE_KINDS:
            probe_snapshot[name] = {
                'logical_count': self._probe_sums[name],
                'logical_hz': self._probe_sums[name] / elapsed,
                'logical_per_frame_avg': (
                    float(self._probe_sums[name]) / samples),
                'logical_per_frame_max': self._probe_maxima[name],
                'timed_ms_total': self._milliseconds(
                    self._probe_duration_sums[name]),
                'timed_ms_per_second': self._milliseconds(
                    self._probe_duration_sums[name] / elapsed),
                'timed_ms_per_frame_avg': self._milliseconds(
                    self._probe_duration_sums[name] / samples),
                'timed_ms_per_frame_max': self._milliseconds(
                    self._probe_duration_maxima[name]),
            }
        self._last_snapshot = {
            'schema': 1,
            'window': self._window_id,
            'round': context.get('round', '-'),
            'map': context.get('map', '-'),
            'phase': context.get('phase', '-'),
            'role': context.get('role', '-'),
            'probe_timing': context.get('probe_timing', 'off'),
            'samples': self._samples,
            'seconds': self._window_elapsed,
            'render_callback_fps': self._samples / elapsed,
            'distribution_samples': len(self._gap_samples),
            'distribution_dropped': max(
                0, self._distribution_samples - len(self._gap_samples)),
            'frame_interval_ms': gap_distribution,
            'python_callback_ms': exec_distribution,
            'outside_callback_ms': outside_distribution,
            'python_stages_ms': stage_snapshot,
            # One logical probe can contain several native calls. The current
            # Python boundary cannot truthfully derive raw call/primitives.
            'raw_native_calls_measured': False,
            'logical_native_probes': probe_snapshot,
            'worker_runtime': dict(self._worker_runtime),
            'over_50_67_100': (
                self._over_50, self._over_67, self._over_100),
            'simulation_caps': self._sim_caps,
            'clock_regressions': self._clock_regressions,
        }
        prefix = '[Offline LAN 0.9.22] PERF '
        lines = [
            (prefix +
             'summary v=3 window=%d round=%s map=%s phase=%s role=%s '
             'probe_timing=%s '
             'samples=%d seconds=%.3f fps=%.2f authority_frames=%d '
             'gap_ms_avg_max=%.3f/%.3f raw_dt_ms_avg_max=%.3f/%.3f '
             'exec_ms_avg_max=%.3f/%.3f offframe_ms_avg_max=%.3f/%.3f '
             'outside_ms_avg_max=%.3f/%.3f '
             'gap_ms_p50_p95_p99_max=%.3f/%.3f/%.3f/%.3f '
             'python_ms_p50_p95_p99_max=%.3f/%.3f/%.3f/%.3f '
             'outside_ms_p50_p95_p99_max=%.3f/%.3f/%.3f/%.3f '
             'distribution_kept_dropped=%d/%d '
             'over_50_67_100=%d/%d/%d sim_caps=%d clock_regress=%d\n') % (
                 self._window_id, context.get('round', '-'),
                 context.get('map', '-'), context.get('phase', '-'),
                 context.get('role', '-'), context.get('probe_timing', 'off'),
                 self._samples, self._window_elapsed,
                 self._samples / elapsed, self._authority_frames,
                 self._milliseconds(self._gap_sum / samples),
                 self._milliseconds(self._gap_max),
                 self._milliseconds(self._raw_sum / samples),
                 self._milliseconds(self._raw_max),
                 self._milliseconds(self._exec_sum / samples),
                 self._milliseconds(self._exec_max),
                 self._milliseconds(self._offframe_sum / samples),
                 self._milliseconds(self._offframe_max),
                 self._milliseconds(self._outside_sum / samples),
                 self._milliseconds(self._outside_max),
                 gap_distribution['p50'], gap_distribution['p95'],
                 gap_distribution['p99'], gap_distribution['max'],
                 exec_distribution['p50'], exec_distribution['p95'],
                 exec_distribution['p99'], exec_distribution['max'],
                 outside_distribution['p50'],
                 outside_distribution['p95'],
                 outside_distribution['p99'],
                 outside_distribution['max'],
                 len(self._gap_samples), max(
                     0, self._distribution_samples -
                     len(self._gap_samples)),
                 self._over_50, self._over_67, self._over_100,
                 self._sim_caps, self._clock_regressions),
        ]
        lines.append(prefix + 'bot_planners ' + (
            ' '.join('%d=%d' % (bot_id, count)
                     for bot_id, count in self._load_busiest) or 'none') +
            '\n')
        lines.append(prefix + 'collections ' + (
            ' '.join('%s=%d' % (name, self._collections[name])
                     for name in sorted(self._collections)) or 'none') +
            '\n')
        control = self._worker_runtime.get('control') or {}
        presentation = self._worker_runtime.get('presentation') or {}
        bot_diagnostics = self._worker_runtime.get('bot_diagnostics') or {}
        lines.append(
            (prefix +
             'worker control_steps=%s catchup=%s debt_callbacks=%s '
             'control_debt_ms=%s max_control_step_ms=%s '
             'astar_pending=%s astar_credit=%s astar_budget_remaining=%s '
             'astar_budget_exhausted=%s astar_completed=%s astar_failed=%s '
             'visibility_queue_depth_max=%s/%s '
             'visibility_oldest_ms_max=%s/%s '
             'shot_lane_pending_max=%s/%s '
             'shot_lane_oldest_due_ms_max=%s/%s '
             'shot_lane_completed_deferred=%s/%s '
             'pose_writes_skips=%s/%s aim_writes_skips=%s/%s\n') % (
                 control.get('control_steps'),
                 control.get('catchup_callbacks'),
                 control.get('debt_callbacks'),
                 control.get('control_debt_ms'),
                 control.get('max_control_step_ms'),
                 control.get('astar_pending'),
                 control.get('astar_credit'),
                 control.get('astar_budget_remaining'),
                 control.get('astar_budget_exhausted_callbacks'),
                 control.get('astar_completed'),
                 control.get('astar_failed'),
                 bot_diagnostics.get('visibility_queue_depth'),
                 bot_diagnostics.get('visibility_queue_max_depth'),
                 bot_diagnostics.get('visibility_oldest_stale_age_ms'),
                 bot_diagnostics.get(
                     'visibility_oldest_stale_max_age_ms'),
                 bot_diagnostics.get('shot_lane_pending_pairs'),
                 bot_diagnostics.get('shot_lane_pending_max_pairs'),
                 bot_diagnostics.get('shot_lane_oldest_due_age_ms'),
                 bot_diagnostics.get(
                     'shot_lane_oldest_due_max_age_ms'),
                 bot_diagnostics.get('shot_lane_completed_pairs'),
                 bot_diagnostics.get(
                     'shot_lane_budget_deferred_attempts'),
                 presentation.get('pose_writes'),
                 presentation.get('pose_skips'),
                 presentation.get('aim_writes'),
                 presentation.get('aim_skips')))
        stage_values = []
        for name in _FRAME_STAGE_NAMES:
            stage_values.append('%s=%.3f/%.3f' % (
                name,
                self._milliseconds(self._stage_sums[name] / samples),
                self._milliseconds(self._stage_maxima[name])))
        lines.append(prefix + 'stages_ms_avg_max ' +
                     ' '.join(stage_values) + '\n')
        probe_values = []
        for name in PROBE_KINDS:
            probe_values.append('%s=%.2f/%d' % (
                name, float(self._probe_sums[name]) / samples,
                self._probe_maxima[name]))
        lines.append(prefix + 'logical_probes_avg_max ' +
                     ' '.join(probe_values) + '\n')
        probe_rate_values = []
        for name in PROBE_KINDS:
            probe_rate_values.append('%s=%.2f/%d' % (
                name, self._probe_sums[name] / elapsed,
                self._probe_sums[name]))
        lines.append(prefix + 'logical_probes_hz_total ' +
                     ' '.join(probe_rate_values) + '\n')
        probe_duration_values = []
        for name in PROBE_KINDS:
            probe_duration_values.append('%s=%.3f/%.3f' % (
                name,
                self._milliseconds(
                    self._probe_duration_sums[name] / samples),
                self._milliseconds(self._probe_duration_maxima[name])))
        lines.append(prefix + 'logical_probe_ms_avg_max ' +
                     ' '.join(probe_duration_values) + '\n')
        probe_duration_rate_values = []
        for name in PROBE_KINDS:
            probe_duration_rate_values.append('%s=%.3f/%.3f' % (
                name,
                self._milliseconds(
                    self._probe_duration_sums[name] / elapsed),
                self._milliseconds(self._probe_duration_sums[name])))
        lines.append(prefix + 'logical_probe_ms_per_s_total ' +
                     ' '.join(probe_duration_rate_values) + '\n')
        lines.append(
            (prefix +
             'projectile_avg_max active=%.2f/%.0f chords=%.2f/%.0f '
             'debt_ms=%.3f/%.3f advance_ms=%.3f/%.3f '
             'terminal=%.2f/%.0f scans=%.2f/%.0f '
             'candidates=%.2f/%.0f\n') % (
                 self._projectile_sums['active'] / samples,
                 self._projectile_maxima['active'],
                 self._projectile_sums['chords'] / samples,
                 self._projectile_maxima['chords'],
                 self._milliseconds(
                     self._projectile_sums['debt'] / samples),
                 self._milliseconds(self._projectile_maxima['debt']),
                 self._milliseconds(
                     self._projectile_sums['advance'] / samples),
                 self._milliseconds(self._projectile_maxima['advance']),
                 self._projectile_sums['terminals'] / samples,
                 self._projectile_maxima['terminals'],
                 self._projectile_sums['scans'] / samples,
                 self._projectile_maxima['scans'],
                 self._projectile_sums['candidates'] / samples,
                 self._projectile_maxima['candidates']))
        for rank, row in enumerate(self._slow, 1):
            stages = row['stages']
            probes = row['probes']
            probe_durations = row.get('probe_durations', {})
            projectile = row.get('projectile') or {}
            context = row.get('context') or {}
            lines.append(
                (prefix +
                 'slow rank=%d cause=%d next=%d gap_ms=%.3f '
                 'raw_dt_ms=%.3f bw_minus_wall_ms=%.3f '
                 'prev_exec_ms=%.3f outside_ms=%.3f '
                 'cause_tick_ms=%.3f cause_motion_ms=%.3f '
                 'pose_step_m=%.4f speed_mps=%.3f camera_mps=%.3f '
                 'airborne=%d grind=%d bots=%d outgoing=%d '
                 'transition=%d prev_emit=%d '
                 'projectile=%s stages_ms=%s logical_probes=%s '
                 'logical_probe_ms=%s\n') % (
                     rank, row['cause'], row['next'],
                     self._milliseconds(row['wall_gap']),
                     row['raw_dt'] * 1000.0,
                     row['bw_minus_wall'] * 1000.0,
                     self._milliseconds(row['exec']),
                     self._milliseconds(row['outside']),
                     self._milliseconds(row['tick_dt']),
                     self._milliseconds(row['motion_dt']),
                     float(context.get('pose_step', 0.0)),
                     float(context.get('speed', 0.0)),
                     float(context.get('camera_speed', 0.0)),
                     int(bool(context.get('airborne'))),
                     int(context.get('grind', 0)),
                     int(context.get('bot_count', 0)),
                     int(context.get('outgoing_count', 0)),
                     int(bool(context.get('transitioned'))),
                     int(bool(row.get('emitted'))),
                     ('active:%d,chords:%d,debt_ms:%.3f,'
                      'advance_ms:%.3f,terminal:%d,scans:%d,'
                      'candidates:%d') % (
                          int(projectile.get('active', 0)),
                          int(projectile.get('chords', 0)),
                          self._milliseconds(projectile.get('debt', 0.0)),
                          self._milliseconds(
                              projectile.get('advance', 0.0)),
                          int(projectile.get('terminals', 0)),
                          int(projectile.get('scans', 0)),
                          int(projectile.get('candidates', 0))),
                     ','.join('%s:%.3f' % (
                         name, self._milliseconds(stages.get(name, 0.0)))
                              for name in _FRAME_STAGE_NAMES),
                     ','.join('%s:%d' % (
                         name, int(probes.get(name, 0)))
                              for name in PROBE_KINDS),
                     ','.join('%s:%.3f' % (
                         name, self._milliseconds(
                             probe_durations.get(name, 0.0)))
                              for name in PROBE_KINDS)))
        return ''.join(lines)

    def finish(self, frame_id, entry_wall, tick_dt, motion_dt, stages,
               probes, context, probe_durations=None, projectile=None):
        if not self.enabled:
            return
        try:
            stages = dict(stages or {})
            probes = dict(probes or {})
            emitted = False
            emit_seconds = 0.0
            if self._emit_due and self._samples:
                emit_start = self._clock()
                payload = self._format()
                self._writer(payload)
                emit_seconds = max(0.0, self._clock() - emit_start)
                emitted = True
                self._window_seconds = self._steady_window_seconds
                self._reset_window()
            stages['diag_emit'] = emit_seconds
            end_wall = self._clock()
            self._pending = {
                'cause': int(frame_id), 'entry_wall': float(entry_wall),
                'exec': max(0.0, end_wall - float(entry_wall)),
                'tick_dt': max(0.0, float(tick_dt)),
                'motion_dt': max(0.0, float(motion_dt)),
                'stages': stages, 'probes': probes,
                'probe_durations': dict(probe_durations or {}),
                'projectile': dict(projectile or {}),
                'context': dict(context or {}), 'emitted': emitted,
            }
        except Exception:
            self._disable()


def _number(value, default=0.0):
    try:
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return float(default)
        return value
    except (TypeError, ValueError):
        return float(default)


def _angle_delta(current, target):
    return (target - current + math.pi) % (2.0 * math.pi) - math.pi


class _ProjectileCollisionAppearance(object):
    """Aim matrices frozen with one historical collision pose."""

    def __init__(self, math_module, descriptor, turret_yaw, gun_pitch):
        turret_yaw, gun_pitch = _component_aim_angles(
            descriptor, turret_yaw, gun_pitch)
        self.turretMatrix = math_module.Matrix()
        self.turretMatrix.setRotateYPR((float(turret_yaw), 0.0, 0.0))
        self.gunMatrix = math_module.Matrix()
        self.gunMatrix.setRotateYPR((0.0, float(gun_pitch), 0.0))


class _ProjectileCollisionTarget(object):
    """Read-only target view shared by armour and critical-hit geometry."""

    def __init__(self, source, descriptor, matrix, position, appearance,
                 math_module):
        self._source = source
        self.typeDescriptor = descriptor
        self.matrix = matrix
        self.position = position
        self.appearance = appearance
        self._math = math_module

    def __getattr__(self, name):
        return getattr(self._source, name)

    def getComponents(self):
        return _pose_components(self, self._math)


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _drowning_sensor_thresholds(descriptor):
    """Mirror the descriptor points passed to #1513's WaterSensor."""
    chassis = _field(descriptor, 'chassis')
    hull = _field(descriptor, 'hull')
    carrying_point = _field(chassis, 'topRightCarryingPoint')
    carrying = (_xyz(carrying_point)[1]
                if carrying_point is not None else 0.5)
    hull_position = _field(chassis, 'hullPosition')
    hull_height = (_xyz(hull_position)[1]
                   if hull_position is not None else 0.6)
    turret_positions = _field(hull, 'turretPositions', ()) or ()
    turret_height = (_xyz(turret_positions[0])[1]
                     if turret_positions else 1.0)
    caution = max(0.0, carrying)
    return caution, max(caution, hull_height + turret_height)


def _drowning_level(descriptor, depth):
    caution, danger = _drowning_sensor_thresholds(descriptor)
    if _number(depth, -1.0) > danger:
        return 2
    if _number(depth, -1.0) > caution:
        return 1
    return 0


def _xyz(value):
    if isinstance(value, dict):
        return (_number(value.get('x')), _number(value.get('y')),
                _number(value.get('z')))
    try:
        return (_number(value[0]), _number(value[1]), _number(value[2]))
    except (TypeError, IndexError):
        return (_number(getattr(value, 'x', 0.0)),
                _number(getattr(value, 'y', 0.0)),
                _number(getattr(value, 'z', 0.0)))


def _format_xyz(value):
    return '(%.2f, %.2f, %.2f)' % _xyz(value)


def _format_axes(matrix):
    """Return one matrix's three basis lengths, which read as its scale."""
    axis = getattr(matrix, 'applyToAxis', None)
    if not callable(axis):
        return 'unreadable'
    lengths = []
    for index in range(3):
        try:
            lengths.append(_number(axis(index).length))
        except Exception:
            return 'unreadable'
    return '%.3f/%.3f/%.3f' % tuple(lengths)


def _spotting_observer(observer):
    """Accept both the three-field and the five-field observer tuple."""
    values = tuple(observer)
    if len(values) >= 5:
        return values[:5]
    return values[0], values[1], values[2], 0.0, False


def _distance_2d(first, second):
    dx = float(first[0]) - float(second[0])
    dz = float(first[2]) - float(second[2])
    return math.sqrt(dx * dx + dz * dz)


def _engine_rotation(yaw, pitch=0.0, roll=0.0):
    """Return BigWorld's rotation vector in roll, pitch, yaw order."""
    return (float(roll), float(pitch), float(yaw))


def _movement_throttle(flags):
    """Decode #1513's direction and native R/F preset flags."""
    flags = int(flags)
    if flags & _MOVEMENT_FORWARD:
        direction = 1.0
    elif flags & _MOVEMENT_BACKWARD:
        direction = -1.0
    else:
        return 0.0
    if flags & _MOVEMENT_CRUISE_CONTROL25:
        return direction * 0.25
    if flags & _MOVEMENT_CRUISE_CONTROL50:
        return direction * 0.5
    return direction


def _load_runtime():
    import AccountCommands
    import AreaDestructibles
    import ArenaType
    import AvatarInputHandler
    import BattleFeedbackCommon
    import BigWorld
    import DataLinks
    import DestructiblesCache
    import Math
    import Vehicular
    import constants
    import game
    import nations
    from Avatar import ClientVisibilityFlags
    from OfflineMapCreator import g_offlineMapCreator
    from AvatarInputHandler import aih_constants
    from AvatarInputHandler import gun_marker_ctrl
    from helpers import EffectMaterialCalculation
    import material_kinds
    from gun_rotation_shared import encodeGunAngles
    from projectile_trajectory import getShotAngles
    from gui.app_loader import g_appLoader
    from gui.app_loader.settings import GUI_GLOBAL_SPACE_ID
    from gui.battle_control.battle_constants import FEEDBACK_EVENT_ID
    from gui.battle_control.battle_constants import VEHICLE_VIEW_STATE
    from gui.mods.offline_lan_0922.compat import g_compatibility
    from gui.shared.utils import HangarSpace
    from items import vehicles
    from vehicle_systems import camouflages
    from vehicle_systems import model_assembler

    class Runtime(object):
        pass

    runtime = Runtime()
    runtime.account_commands = AccountCommands
    runtime.area_destructibles = AreaDestructibles
    runtime.aih_constants = aih_constants
    runtime.avatar_input_handler = AvatarInputHandler
    runtime.gun_marker_ctrl = gun_marker_ctrl
    runtime.app_loader = g_appLoader
    runtime.arena_cache = ArenaType.g_cache
    runtime.arena_visibility_mask = ArenaType.getVisibilityMask
    runtime.bigworld = BigWorld
    runtime.client_visibility_flags = ClientVisibilityFlags
    runtime.battle_feedback_common = BattleFeedbackCommon
    runtime.compatibility = g_compatibility
    runtime.constants = constants
    runtime.data_links = DataLinks
    runtime.destructibles_cache = DestructiblesCache
    runtime.vehicular = Vehicular
    runtime.effect_material_calculation = EffectMaterialCalculation
    runtime.material_kinds = material_kinds
    runtime.encode_gun_angles = encodeGunAngles
    runtime.get_shot_angles = getShotAngles
    runtime.game = game
    runtime.gui_global_space_id = GUI_GLOBAL_SPACE_ID
    runtime.hangar_space = HangarSpace
    runtime.math = Math
    runtime.camouflages = camouflages
    runtime.model_assembler = model_assembler
    runtime.nations = nations
    runtime.offline_map_creator = g_offlineMapCreator
    runtime.call_with_standard_gameplay_mask = \
        native_mapping_mask.call_with_standard_gameplay_mask
    runtime.vehicles = vehicles
    runtime.feedback_event_id = FEEDBACK_EVENT_ID
    runtime.vehicle_view_state = VEHICLE_VIEW_STATE
    return runtime


def _selected_vehicle_has_sixth_sense():
    """Read the selected #1513 crew before the lobby Account is retired."""
    try:
        from CurrentVehicle import g_currentVehicle
        item = getattr(g_currentVehicle, 'item', None)
        for entry in (getattr(item, 'crew', ()) or ()):
            tankman = (entry[1] if isinstance(entry, tuple) and
                       len(entry) == 2 else entry)
            if tankman is None:
                continue
            skills = getattr(tankman, 'skills', None)
            if skills is None:
                skills = getattr(
                    getattr(tankman, 'descriptor', None), 'skills', ())
            for skill in (skills or ()):
                name = str(getattr(skill, 'name', skill)).lower()
                if 'sixthsense' in name:
                    return True
    except Exception:
        pass
    return False


def _crew_has_finished_skill(crew, wanted):
    """Return whether one mounted crewman has a finished active perk."""
    wanted = str(wanted).lower()
    for entry in (crew or ()):
        member = (entry[1] if isinstance(entry, tuple) and len(entry) == 2
                  else entry)
        if member is None:
            continue
        skills = getattr(member, 'skills', None)
        if skills is None:
            skills = getattr(
                getattr(member, 'descriptor', None), 'skills', ())
        for skill in (skills or ()):
            if str(getattr(skill, 'name', skill)).lower() != wanted:
                continue
            if not bool(getattr(skill, 'isActive', True)):
                continue
            try:
                level = float(getattr(skill, 'level', 100.0))
            except (TypeError, ValueError):
                level = 0.0
            if level >= 100.0:
                return True
    return False


class _LANInputSender(object):

    def __init__(self, owner):
        self.owner = owner
        self.forward = 0.0
        self.turn = 0.0
        self.aim_yaw = 0.0
        self.gun_pitch = 0.0
        self.aim_pitch = 0.0
        self.aim_point = None
        self.handbrake = False

    def align_aim(self, turret_yaw=0.0, gun_pitch=0.0):
        """Seed the world-space LAN aim from the attached native gun."""
        unused_position, vehicle_yaw = self.owner.local_pose()
        self.aim_yaw = float(vehicle_yaw) + float(turret_yaw)
        self.gun_pitch = float(gun_pitch)
        self.aim_pitch = (
            float(getattr(self.owner, '_local_pitch', 0.0)) +
            float(getattr(self.owner, '_local_siege_aim_pitch', 0.0)) +
            self.gun_pitch)
        self.aim_point = None
        return True

    def send_avatar_input(self, vehicle_id, kind, payload):
        payload = payload if isinstance(payload, dict) else {}
        if kind == 'move':
            flags = int(payload.get('flags', 0))
            self.forward = _movement_throttle(flags)
            self.turn = 1.0 if flags & 8 else (-1.0 if flags & 4 else 0.0)
            self.handbrake = bool(flags & 64)
            return self.send_current()
        if kind == 'cruise':
            mode = int(payload.get('mode', 0))
            self.forward = _CRUISE_MODE_THROTTLE.get(mode, 0.0)
            return self.send_current()
        if kind in ('track_world', 'track_relative'):
            self._track(payload.get('point'), kind == 'track_relative')
            # The retail cell echoes an accepted packed gun angle.  Without
            # that sample #1513's VehicleGunRotator compares every client
            # step with the spawn-time zero angle and snaps the turret back
            # toward the hull.  This trusted-client server boundary echoes
            # the rotator's current, speed-limited angle before its next step.
            self.owner._echo_local_gun_angles()
            return self.send_current()
        if kind == 'stop_tracking':
            unused_position, vehicle_yaw = self.owner.local_pose()
            turret_yaw = _number(payload.get('turret_yaw'))
            gun_pitch = _number(payload.get('gun_pitch'))
            self.aim_yaw = vehicle_yaw + turret_yaw
            self.gun_pitch = gun_pitch
            self.aim_pitch = (
                float(getattr(self.owner, '_local_pitch', 0.0)) +
                float(getattr(
                    self.owner, '_local_siege_aim_pitch', 0.0)) +
                gun_pitch)
            self.aim_point = None
            self.owner._echo_local_gun_angles(turret_yaw, gun_pitch)
            return self.send_current()
        if kind == 'shoot':
            return self.owner.shoot(self.aim_yaw, self.gun_pitch)
        if kind == 'development':
            return True
        return False

    def reject_native_shot_wait(self):
        """Cancel #1513's post-mailbox wait for a rejected local trigger."""
        return self.owner._defer_cancel_native_shot_wait()

    def change_vehicle_setting(self, vehicle_id, code, value):
        return self.owner.change_vehicle_setting(code, value)

    def _track(self, point, relative=False):
        target = _xyz(point)
        if relative:
            dx, dy, dz = target
            origin = self.owner.local_stabilised_position()
            self.aim_point = tuple(
                origin[index] + target[index] for index in range(3))
        else:
            position, unused_yaw = self.owner.local_pose()
            dx = target[0] - position[0]
            dy = target[1] - position[1]
            dz = target[2] - position[2]
            self.aim_point = target
        horizontal = math.sqrt(dx * dx + dz * dz)
        self.aim_yaw = math.atan2(dx, dz)
        # Exact #1513 stores the rendered gun angle as negative-up.  The
        # relative tracking point uses normal world coordinates (positive Y
        # is up), so its geometric elevation must be inverted before it is
        # echoed through VehicleGunRotator or donated to the worker.
        self.gun_pitch = -math.atan2(dy, max(horizontal, 0.001))
        self.aim_pitch = self.gun_pitch

    def send_current(self, siege_enabled=None):
        position, yaw = self.owner.local_pose()
        ram_contacts_getter = getattr(
            self.owner, 'local_ram_contacts', None)
        ram_contacts = (ram_contacts_getter()
                        if callable(ram_contacts_getter) else None)
        destructible_contacts_getter = getattr(
            self.owner, 'local_destructible_contacts', None)
        destructible_contacts = (
            destructible_contacts_getter()
            if callable(destructible_contacts_getter) else None)
        keyword_args = {
            'speed': getattr(self.owner, '_local_speed', 0.0),
            'pitch': getattr(self.owner, '_local_pitch', 0.0),
            'roll': getattr(self.owner, '_local_roll', 0.0),
            'ram_contacts': ram_contacts,
            'destructible_contacts': destructible_contacts,
        }
        up_cosine = getattr(self.owner, '_local_surface_up_cosine', None)
        if up_cosine is None:
            up_cosine = math.cos(keyword_args['pitch']) * math.cos(
                keyword_args['roll'])
        keyword_args['up_cosine'] = max(
            -1.0, min(1.0, float(up_cosine)))
        gun_state = getattr(self.owner, '_gun_state', None)
        if (gun_state is not None and
                getattr(self.owner, '_gun_last_tick', None) is not None):
            # Every ordered input is a final client gun checkpoint, not a
            # sample from the preceding 100 ms HUD publication.  Consume the
            # complete wall-clock gap here so a delayed callback cannot donate
            # a stale reload and leave elapsed time for a future frame.
            advance = getattr(self.owner, '_advance_local_gun_to', None)
            server = getattr(self.owner, '_server', None)
            resolve = getattr(self.owner, '_server_entity', None)
            if (callable(advance) and server is not None and
                    callable(resolve)):
                gun_state = advance(resolve(server.vehicle_id))
        if gun_state is not None:
            keyword_args['shell_index'] = int(gun_state.shot_index)
            pending_shell = gun_state.pending_index
            keyword_args['next_shell_index'] = int(
                gun_state.shot_index if pending_shell is None
                else pending_shell)
            keyword_args['shell_change_pending'] = bool(
                pending_shell is not None)
            keyword_args['gun_checkpoint'] = {
                'reload_time': float(gun_state.reload_time),
                'reload_duration': float(gun_state.reload_duration),
                'clip': int(gun_state.clip),
                'clip_size': int(gun_state.clip_size),
                'dispersion': float(gun_state.dispersion),
            }
        estimator = getattr(self.owner, '_estimated_motion_time_us', None)
        pose_time = (estimator(self.owner._clock())
                     if callable(estimator) else None)
        if pose_time is not None:
            keyword_args['pose_time_us'] = pose_time
        if siege_enabled is not None:
            keyword_args['siege_enabled'] = bool(siege_enabled)
        result = self.owner.client.send_input(
            self.forward, self.turn, self.aim_yaw, self.gun_pitch,
            position, yaw, **keyword_args)
        if result:
            enqueued = getattr(
                self.owner, '_ram_contacts_enqueued', None)
            if callable(enqueued):
                enqueued()
            enqueued = getattr(
                self.owner, '_destructible_contacts_enqueued', None)
            if destructible_contacts and callable(enqueued):
                enqueued()
            report_getter = getattr(
                self.owner, 'local_damage_report', None)
            report = (report_getter()
                      if callable(report_getter) else None)
            repair_sender = getattr(
                self.owner.client, 'send_track_repair', None)
            if (isinstance(report, dict) and report.get('tracks') and
                    callable(repair_sender)):
                repair_sender(
                    report['tracks'],
                    report.get('critical_base_revision'),
                    report.get('critical_seq'))
        return result


class BattleRuntime(object):
    """Own map, real Vehicle entities, snapshot smoothing and authority bots."""

    def __init__(self, runtime=None):
        self._runtime = runtime
        self._config = None
        self._worker_mode = False
        self._start_message = None
        self.client = None
        self.state = 'idle'
        self.error = None
        self._generation = 0
        self._callback_id = None
        self._ammo_callback_id = None
        self._callback_token = None
        self._ammo_callback_token = None
        self._lobby_restore_callback_id = None
        self._lobby_restore_token = None
        self._retired_native_owners = []
        self._deadline = 0.0
        self._vehicle_ready_deadline = 0.0
        self._map_create_attempted = False
        self._lobby_retire_started = False
        self._app_loader_guard = None
        self._damage_info_failure_reported = False
        self._optional_failures_reported = set()
        self._disabled_optional_features = set()
        self._avatar = None
        self._binding = None
        self._server = None
        self._remote_factory = None
        self._descriptor_cache = {}
        self._prepared_vehicle_names = []
        self._unusable_vehicles_reported = set()
        self._sender = None
        self._sync = None
        self._bots = None
        self._next_bot_manifest_retry = 0.0
        self._bot_manifest_retry_deadline = 0.0
        self._bot_manifest_retry_identity = None
        self._worker_probe = None
        self._worker_probe_attempted = False
        self._worker_frame_callbacks = 0
        self._worker_probe_authority_callbacks = 0
        self._worker_probe_bot_generated = 0
        self._worker_probe_bot_enqueued = 0
        self._worker_probe_bot_send_failed = 0
        self._worker_probe_bot_count = 0
        self._worker_probe_simulation_caps = 0
        self._worker_probe_control_steps = 0
        self._worker_probe_catchup_callbacks = 0
        self._worker_probe_control_debt_callbacks = 0
        self._worker_probe_max_control_step = 0.0
        self._worker_probe_control_debt = 0.0
        self._worker_probe_max_control_debt = 0.0
        self._worker_probe_astar_budget_exhausted = 0
        self._worker_probe_astar_max_pending = 0
        self._authority_pose_writes = 0
        self._authority_pose_skips = 0
        self._authority_aim_writes = 0
        self._authority_aim_skips = 0
        self._frame_diagnostics = (
            _FrameDiagnostics(
                initial_window_seconds=DIAGNOSTIC_INITIAL_WINDOW_SECONDS)
            if PERFORMANCE_DIAGNOSTICS else None)
        self._sixth_sense = None
        self._has_sixth_sense = False
        self._has_expert = False
        self._has_deadeye = False
        self._expert_visibility_enabled = False
        self._expert_target_id = 0
        self._expert_target_due = 0.0
        self._expert_target_signature = None
        self._records = {}
        self._records_revision = 0
        self._last_snapshot = None
        self._last_frame_time = None
        self._standard_space_visibility = None
        self._next_space_visibility_check = 0.0
        self._space_visibility_warning_reported = False
        if self._frame_diagnostics is not None:
            self._frame_diagnostics.enabled = True
            self._frame_diagnostics.reset()
        self._local_position = (0.0, 0.0, 0.0)
        self._local_yaw = 0.0
        self._last_health = {}
        self._client_ready_received = False
        self._local_descriptor = None
        self._bot_fire_seen = {}
        self._bot_fire_confirmations = {}
        self._bot_launch_payloads = {}
        self._bot_destructible_samples = {}
        self._player_tree_destructible_samples = {}
        self._bot_pose_times = {}
        self._bot_yaw_rates = {}
        self._track_report_time = None
        self._local_speed = 0.0
        self._local_turn_speed = 0.0
        self._local_drive_turn = 0.0
        self._local_siege_pending = None
        self._local_push_x = 0.0
        self._local_push_z = 0.0
        self._local_ram_cooldowns = {}
        self._local_ram_contacts = frozenset()
        self._local_ram_seq = 0
        self._local_ram_receipt = None
        self._local_ram_receipts = collections.OrderedDict()
        self._local_ram_admitted_seq = 0
        self._native_ram_contact_hook = None
        self._native_ram_contact_proofs = collections.OrderedDict()
        self._native_ram_contact_failures = set()
        self._native_ram_event_seq = 0
        self._local_ram_episode_contacts = frozenset()
        self._local_ram_profile_cache = None
        self._remote_ram_profile_cache = {}
        self._local_destructible_contact_seq = 0
        self._local_destructible_contacts = collections.OrderedDict()
        self._local_destructible_safe_poses = collections.OrderedDict()
        self._ram_bot_history = {}
        self._ram_bot_history_order = []
        self._ram_bot_history_times = {}
        self._ram_bot_history_index = {}
        self._ram_bot_lookup_cache = {}
        self._local_physics = None
        self._local_pitch = 0.0
        self._local_roll = 0.0
        self._local_matrix = None
        self._local_pose_matrix = None
        self._local_stabilised_matrix = None
        self._local_stabilised_snapshot = None
        self._local_steady_rotation_matrix = None
        self._local_siege_body_matrix = None
        self._local_siege_stabilised_matrix = None
        self._local_siege_ground_matrix = None
        self._local_siege_flat_body_matrix = None
        self._local_siege_aim_matrix = None
        self._local_siege_aim_world_matrix = None
        self._local_siege_aim_pitch = 0.0
        self._local_model = None
        self._local_native_matrix = None
        self._local_native_stabilised_matrix = None
        self._local_camera_velocity = None
        self._local_engine_mode = None
        self._spectated_engine_id = None
        self._local_grind = 0
        self._local_motion_soft_block = False
        self._local_motion_cap_crushed = False
        self._local_motion_kinds = '-'
        self._local_motion_status = 'clear'
        self._bot_motion_kinds = {}
        self._crush_reports = 0
        self._next_crush_report = {}
        self._destructible_verdict_reports = 0
        self._soft_static_recast_budget = [BOT_SOFT_RECAST_BUDGET]
        self._local_vertical_speed = 0.0
        self._local_airborne = False
        self._local_fall_armed = False
        self._local_last_pitch = 0.0
        self._local_drive_pitch_history = None
        self._local_smooth_drive_pitch = 0.0
        self._local_slide_speed = 0.0
        self._local_downhill = (0.0, 0.0, 0.0)
        self._local_slope_tangent = 0.0
        self._local_ground_plane = None
        self._local_surface_up_cosine = None
        self._local_air_lateral = (0.0, 0.0)
        self._pending_landing_impacts = []
        self._input_accumulator = 0.0
        self._gun_state = None
        self._gun_last_tick = None
        self._player_authority_guns = {}
        self._player_fire_intents = collections.OrderedDict()
        self._player_fire_intent_history = collections.OrderedDict()
        self._player_fire_launch_pending = {}
        self._local_fire_intent = None
        self._ammo_signature = None
        self._targeting_signature = None
        self._reload_event = None
        self._equipment_state = None
        self._equipment_signature = None
        self._equipment_revision = -1
        self._local_loadout_cache = None
        self._garage_loadout = None
        self._offframe_seconds = 0.0
        self._effect_reports = 0
        self._decal_probe = None
        self._spotted_signature = None
        self._local_spotting_cache = None
        self._local_factors_cache = None
        self._remote_spotting_cache = {}
        self._local_still_since = None
        self._published_vision_radius = None
        self._published_still_devices = {}
        self._vision_feed_failed = False
        self._reported_crew_impaired = None
        self._battle_result = None
        self._round_finished_notified = False
        self._on_local_leave = None
        self._battle_live = True
        self._prebattle_deadline = None
        self._pending_bot_creates = {}
        self._pending_bot_create_order = []
        self._last_bot_create_team = None
        self._bots_ready_reported = False
        self._next_bot_create_time = 0.0
        self._arena_type = None
        self._arena_bounds = None
        self._spawn_planner = None
        self._navigation_graph = None
        self._grounded_bot_ids = set()
        self._bot_vehicle_assignments = {}
        self._spawn_cache = {}
        self._rules_state = {'bases': {}}
        self._ready_sent = False
        self._destructibles = None
        self._local_damage_report = None
        self._local_critical_base_revision = 0
        self._local_critical_server_revision = 0
        self._local_critical_next_seq = 0
        self._local_critical_owned = False
        self._accepted_event_ids = _RecentIdSet()
        self._applied_event_ids = _RecentIdSet()
        # Retain the old name as a read-only compatibility view for audits;
        # an event is "seen" only after its native presentation was applied.
        self._seen_event_ids = self._applied_event_ids
        self._event_journal = []
        self._local_last_attacker = None
        self._next_critical_report_time = 0.0
        self._last_presented_rpm = None
        self._next_rpm_time = 0.0
        self._drown_check = 0.0
        self._drown_time = 0.0
        self._drown_level = 0
        self._drown_started = None
        self._player_environment_check = 0.0
        self._player_environment_seq = 0
        self._overturn_check = 0.0
        self._overturn_time = 0.0
        self._overturn_level = 0
        self._overturn_started = None
        self._outlined_engine_id = None
        self._outlined_entity = None
        self._outlined_vehicle = None
        self._outlined_model = None
        self._outline_blocked = False
        self._edge_reports = 0
        self._target_reports = 0
        self._next_outline_time = 0.0
        self._next_compound_report = 0.0
        self._compound_reports = 0
        self._compound_report_signature = None
        self._mouse_target_matrix = None
        self._outline_report = None
        self._outline_logged_report = None
        self._next_outline_report = 0.0
        self._next_spotting_time = 0.0
        self._foliage = None
        self._next_fallen_tree_foliage_refresh = 0.0
        self._fallen_tree_foliage_seen_bodies = set()
        self._fallen_tree_foliage_stable = {}
        self._projectiles = None
        self._projectile_meta = {}
        self._projectile_visual_meta = {}
        self._projectile_terminal_data = {}
        self._projectile_target_positions = {}
        self._projectile_position_history = []
        self._projectile_historic_pose_cache = None
        self._projectile_spatial_bins = None
        self._projectile_spatial_fallback_keys = frozenset()
        self._projectile_spatial_records_container = None
        self._projectile_spatial_records_revision = None
        self._projectile_spatial_records = {}
        self._projectile_spatial_order = {}
        self._projectile_spatial_floor = None
        self._projectile_spatial_ceiling = None
        self._projectile_lineage = set()
        self._projectile_epoch = None
        self._projectile_server_time_ms = None
        self._projectile_server_local_time = None
        self._pose_motion_time_us = None
        self._pose_motion_local_time = None
        self._projectile_revision = -1
        self._next_projectile_progress_time = 0.0
        self._projectile_frame_start = 0.0
        self._projectile_frame_end = 0.0
        self._projectile_destructible_context = None
        self._projectile_perf = {}
        self._projectile_scan_count = 0
        self._projectile_candidate_count = 0
        self._artillery = None

    def start(self, config, message=None, lan_client=None,
              on_local_leave=None):
        if self.state not in ('idle', 'stopped', 'failed'):
            return False
        if self._lobby_restore_token is not None:
            return False
        if lan_client is None:
            raise ValueError('LAN client is required')
        self._runtime = self._runtime or _load_runtime()
        self._config = dict(config or {})
        self._worker_mode = bool(self._config.get('worker_mode', False))
        if self._worker_mode:
            self._config['native_remote_vehicles'] = False
            self._config['bot_track_animation'] = False
        self._worker_probe = None
        self._worker_probe_attempted = False
        self._worker_frame_callbacks = 0
        self._worker_probe_authority_callbacks = 0
        self._worker_probe_bot_generated = 0
        self._worker_probe_bot_enqueued = 0
        self._worker_probe_bot_send_failed = 0
        self._worker_probe_bot_count = 0
        self._worker_probe_simulation_caps = 0
        self._worker_probe_control_steps = 0
        self._worker_probe_catchup_callbacks = 0
        self._worker_probe_control_debt_callbacks = 0
        self._worker_probe_max_control_step = 0.0
        self._worker_probe_control_debt = 0.0
        self._worker_probe_max_control_debt = 0.0
        self._worker_probe_astar_budget_exhausted = 0
        self._worker_probe_astar_max_pending = 0
        self._authority_pose_writes = 0
        self._authority_pose_skips = 0
        self._authority_aim_writes = 0
        self._authority_aim_skips = 0
        self._next_bot_manifest_retry = 0.0
        self._bot_manifest_retry_deadline = 0.0
        self._bot_manifest_retry_identity = None
        area_destructibles = getattr(
            self._runtime, 'area_destructibles', None)
        destructibles_cache = getattr(
            self._runtime, 'destructibles_cache', None)
        debug_logging = bool(self._config.get('debug_logging', False))
        if area_destructibles is not None and destructibles_cache is not None:
            destructibles_compat.install(
                area_destructibles, destructibles_cache)
            from gui.mods.offline_lan_0922 import destructibles_sensor
            destructibles_sensor.set_diagnostics(debug_logging)
            self._destructibles = destructibles_sensor
        else:
            # Pure-logic tests inject no engine modules.  Production runtime
            # construction above always supplies both exact #1513 modules.
            self._destructibles = None
        # Copy the 0.8.2 ordering: tuning must be applied before either the
        # player or bot descriptor-derived physics parameters are created.
        vehicle_physics.apply_tuning(self._config.get('physics_tuning'))
        combat_rules.apply_he_tuning(self._config.get('he_tuning'))
        self._start_message = dict(message or {})
        self.client = lan_client
        self._damage_info_failure_reported = False
        self._optional_failures_reported = set()
        self._disabled_optional_features = set()
        self._sixth_sense = None
        self._has_sixth_sense = (
            False if self._worker_mode else
            _selected_vehicle_has_sixth_sense())
        self._has_expert = False
        self._has_deadeye = False
        self._expert_visibility_enabled = False
        self._expert_target_id = 0
        self._expert_target_due = 0.0
        self._expert_target_signature = None
        self._last_snapshot = None
        self._last_frame_time = None
        self._standard_space_visibility = None
        self._next_space_visibility_check = 0.0
        self._space_visibility_warning_reported = False
        if self._frame_diagnostics is not None:
            self._frame_diagnostics.enabled = True
            self._frame_diagnostics.reset()
        self._last_health = {}
        self._client_ready_received = False
        self._local_descriptor = None
        self._bot_fire_seen = {}
        self._bot_fire_confirmations = {}
        self._bot_launch_payloads = {}
        self._bot_destructible_samples = {}
        self._player_tree_destructible_samples = {}
        self._bot_pose_times = {}
        self._bot_yaw_rates = {}
        self._track_report_time = None
        self._local_speed = 0.0
        self._local_turn_speed = 0.0
        self._local_drive_turn = 0.0
        self._local_siege_pending = None
        self._local_push_x = 0.0
        self._local_push_z = 0.0
        self._local_ram_cooldowns = {}
        self._local_ram_contacts = frozenset()
        self._local_ram_seq = 0
        self._local_ram_receipt = None
        self._local_ram_receipts = collections.OrderedDict()
        self._local_ram_admitted_seq = 0
        self._local_ram_episode_contacts = frozenset()
        self._local_ram_profile_cache = None
        self._remote_ram_profile_cache = {}
        self._local_destructible_contact_seq = 0
        self._local_destructible_contacts = collections.OrderedDict()
        self._local_destructible_safe_poses = collections.OrderedDict()
        self._ram_bot_history = {}
        self._ram_bot_history_order = []
        self._ram_bot_history_times = {}
        self._ram_bot_history_index = {}
        self._ram_bot_lookup_cache = {}
        self._local_physics = None
        self._local_pitch = 0.0
        self._local_roll = 0.0
        self._local_matrix = None
        self._local_pose_matrix = None
        self._local_stabilised_matrix = None
        self._local_stabilised_snapshot = None
        self._local_steady_rotation_matrix = None
        self._local_siege_body_matrix = None
        self._local_siege_stabilised_matrix = None
        self._local_siege_ground_matrix = None
        self._local_siege_flat_body_matrix = None
        self._local_siege_aim_matrix = None
        self._local_siege_aim_world_matrix = None
        self._local_siege_aim_pitch = 0.0
        self._local_model = None
        self._local_native_matrix = None
        self._local_native_stabilised_matrix = None
        self._local_camera_velocity = None
        self._local_engine_mode = None
        self._spectated_engine_id = None
        self._local_grind = 0
        self._local_motion_soft_block = False
        self._local_motion_cap_crushed = False
        self._local_motion_kinds = '-'
        self._local_motion_status = 'clear'
        self._bot_motion_kinds = {}
        self._crush_reports = 0
        self._next_crush_report = {}
        self._destructible_verdict_reports = 0
        self._soft_static_recast_budget = [BOT_SOFT_RECAST_BUDGET]
        self._local_vertical_speed = 0.0
        self._local_airborne = False
        self._local_fall_armed = False
        self._local_last_pitch = 0.0
        self._local_drive_pitch_history = None
        self._local_smooth_drive_pitch = 0.0
        self._local_slide_speed = 0.0
        self._local_downhill = (0.0, 0.0, 0.0)
        self._local_slope_tangent = 0.0
        self._local_ground_plane = None
        self._local_surface_up_cosine = None
        self._local_air_lateral = (0.0, 0.0)
        self._pending_landing_impacts = []
        self._input_accumulator = 0.0
        self._gun_state = None
        self._gun_last_tick = None
        self._player_authority_guns = {}
        self._player_fire_intents = collections.OrderedDict()
        self._player_fire_intent_history = collections.OrderedDict()
        self._player_fire_launch_pending = {}
        self._local_fire_intent = None
        self._ammo_signature = None
        self._targeting_signature = None
        self._reload_event = None
        self._equipment_state = None
        self._equipment_signature = None
        self._equipment_revision = -1
        self._local_loadout_cache = None
        self._garage_loadout = None
        self._offframe_seconds = 0.0
        self._effect_reports = 0
        self._spotted_signature = None
        self._local_spotting_cache = None
        self._local_factors_cache = None
        self._remote_spotting_cache = {}
        self._local_ram_profile_cache = None
        self._remote_ram_profile_cache = {}
        self._local_still_since = None
        self._published_vision_radius = None
        self._published_still_devices = {}
        self._vision_feed_failed = False
        self._reported_crew_impaired = None
        self._battle_result = self._start_message.get('battle_result')
        self._round_finished_notified = False
        self._on_local_leave = on_local_leave
        self._battle_live = False
        self._prebattle_deadline = None
        self._pending_bot_creates = {}
        self._pending_bot_create_order = []
        self._last_bot_create_team = None
        self._bots_ready_reported = False
        self._next_bot_create_time = 0.0
        self._navigation_graph = None
        self._grounded_bot_ids = set()
        self._bot_vehicle_assignments = {}
        self._spawn_cache = {}
        self._rules_state = {'bases': {}}
        self._ready_sent = False
        self._lobby_retire_started = False
        self._local_damage_report = None
        self._local_critical_base_revision = 0
        self._local_critical_server_revision = 0
        self._local_critical_next_seq = 0
        self._local_critical_owned = False
        self._accepted_event_ids = _RecentIdSet()
        self._applied_event_ids = _RecentIdSet()
        self._seen_event_ids = self._applied_event_ids
        self._event_journal = []
        self._local_last_attacker = None
        self._next_critical_report_time = 0.0
        self._last_presented_rpm = None
        self._next_rpm_time = 0.0
        self._drown_check = 0.0
        self._drown_time = 0.0
        self._drown_level = 0
        self._drown_started = None
        self._player_environment_check = 0.0
        self._player_environment_seq = 0
        self._overturn_check = 0.0
        self._overturn_time = 0.0
        self._overturn_level = 0
        self._overturn_started = None
        self._next_spotting_time = 0.0
        self._foliage = None
        self._next_fallen_tree_foliage_refresh = 0.0
        self._fallen_tree_foliage_seen_bodies = set()
        self._fallen_tree_foliage_stable = {}
        projectile_now = self._clock()
        self._projectiles = InFlightProjectiles(
            maximum_active=PROJECTILE_MAX_ACTIVE,
            initial_time=projectile_now)
        self._projectile_meta = {}
        self._projectile_visual_meta = {}
        self._projectile_terminal_data = {}
        self._projectile_target_positions = {}
        self._projectile_position_history = []
        self._projectile_historic_pose_cache = None
        self._projectile_spatial_bins = None
        self._projectile_spatial_fallback_keys = frozenset()
        self._projectile_spatial_records_container = None
        self._projectile_spatial_records_revision = None
        self._projectile_spatial_records = {}
        self._projectile_spatial_order = {}
        self._projectile_spatial_floor = None
        self._projectile_spatial_ceiling = None
        self._projectile_lineage = set()
        self._projectile_epoch = None
        self._projectile_server_time_ms = None
        self._projectile_server_local_time = None
        self._pose_motion_time_us = None
        self._pose_motion_local_time = None
        self._projectile_revision = -1
        self._next_projectile_progress_time = projectile_now
        self._projectile_frame_start = projectile_now
        self._projectile_frame_end = projectile_now
        self._projectile_destructible_context = None
        self._projectile_perf = {}
        self._projectile_scan_count = 0
        self._projectile_candidate_count = 0
        self._artillery = ArtilleryController(
            origin_resolver=self._bot_artillery_planning_origin)
        self._generation += 1
        self._deadline = self._clock() + float(
            self._config.get('startupTimeoutSeconds', 30.0))
        self._vehicle_ready_deadline = 0.0
        self.state = 'creating_map'
        self.error = None
        try:
            arena_type = self._standard_arena(self._config.get('map'))
            if arena_type is None:
                raise RuntimeError('standard arena definition is unavailable')
            self._arena_type = arena_type
            self._arena_bounds = self._arena_bounds_from_type(arena_type)
            graph_loader = getattr(
                self._runtime, 'navigation_graph_loader',
                prebaked_navigation.load_graph)
            self._navigation_graph = graph_loader(self._config.get('map'))
            map_name = prebaked_navigation._short_map_name(
                self._config.get('map'))
            if (map_name in prebaked_navigation.SUPPORTED_MAPS and
                    self._navigation_graph is None):
                raise RuntimeError(
                    'validated navigation graph is unavailable for %s' %
                    map_name)
            foliage_loader = getattr(
                self._runtime, 'foliage_loader',
                prebaked_foliage.load_foliage)
            try:
                self._foliage = foliage_loader(self._config.get('map'))
            except Exception as error:
                self._foliage = None
                self._warn_optional_failure(
                    'foliage camouflage', error)
            if self._destructibles is not None:
                catalog_loader = getattr(
                    self._runtime, 'destructible_catalog_loader',
                    prebaked_destructibles.load_catalog)
                try:
                    destructible_catalog = catalog_loader(
                        self._config.get('map'))
                    if destructible_catalog is None:
                        raise RuntimeError(
                            'validated destructible catalog is unavailable '
                            'for %s' % map_name)
                    self._destructibles.set_catalog(destructible_catalog)
                except Exception as error:
                    self._disable_destructibles_for_round(error)
            self._observe_destructibles_disabled(self._start_message)
            self._spawn_planner = SpawnPlanner(
                arena_type,
                tactical_maps.get_tactical_map(self._config['map']),
                self._navigation_graph)
            constants = self._runtime.constants
            local_identity = self._local_state()
            self._runtime.compatibility.set_battle_network_client(self.client)
            self._runtime.compatibility.configure_battle(
                getattr(constants.ARENA_GUI_TYPE, 'RANDOM', 0),
                getattr(constants.ARENA_BONUS_TYPE, 'REGULAR', 0),
                local_identity.get('name', self.client.name),
                int(local_identity.get('team', self.client.team)),
                arena_type_id=getattr(arena_type, 'id', 0))
            lobby_boundary = self._preflight_lobby_retirement()
            garage_loadout = self._garage_loadout_snapshot()
            self._has_expert = (
                not self._worker_mode and _crew_has_finished_skill(
                    garage_loadout.get('crew'), 'commander_expert'))
            self._has_deadeye = (
                not self._worker_mode and _crew_has_finished_skill(
                    garage_loadout.get('crew'), 'gunner_sniper'))
            self._install_battle_gui_guard()
            self._enter_battle_loading()
            self._retire_lobby_entities(lobby_boundary)
            # OfflineMapCreator.create() catches some native setup failures and
            # only calls cancel(), which resets ids but does not clear the
            # partially-created Avatar or space.  Remember the attempt before
            # entering stock code so every exit can run its stronger destroy()
            # rollback, even when Active() is already false afterward.
            self._map_create_attempted = True
            self._create_native_battle_map(self._config['map'])
            if not self._runtime.offline_map_creator.Active():
                raise RuntimeError('stock OfflineMapCreator rejected the map')
            self._avatar = self._runtime.bigworld.player()
            if self._avatar is None:
                raise RuntimeError('stock OfflineMapCreator created no Avatar')
            self._configure_standard_space_visibility()
            if not getattr(
                    self._avatar, '_offlineLANInitComplete', False):
                raise RuntimeError(
                    'stock OfflineMapCreator returned a partial Avatar')
            if not getattr(
                    self._avatar, '_offlineLANPlayerReady', False):
                raise RuntimeError(
                    'stock OfflineMapCreator did not promote its Avatar')
            self._install_native_ram_contact_hook()
            if self._destructibles is not None:
                self._destructibles.reset(self._avatar.spaceID)
                self._destructibles.set_event_sink(
                    self._report_destructible)
                if 'destructibles' in self._start_message:
                    self._apply_destructible_state(
                        self._start_message.get('destructibles'))
            # From this point onward every stock Avatar branch must see a real
            # battle, not the viewer mode used by OfflineMapCreator.  destroy()
            # does not require Active(), so it still owns the exact space ids.
            self._runtime.offline_map_creator.SetActive(False)
            # Arena metadata exists while geometry and Vehicle prerequisites
            # are still loading in a normal battle.  Publishing it now gives
            # ArenaDataProvider a player id before a fast space-complete
            # callback can request the final battle page.
            self._create_entities()
            return self.state != 'failed'
        except Exception as error:
            self._fail(error)
            return False

    def lobby_restore_pending(self):
        """Return whether native teardown is waiting for its GUI boundary."""
        return self._lobby_restore_token is not None

    def _preflight_lobby_retirement(self):
        """Validate destructive lobby boundaries before changing GUI state."""
        clear = getattr(
            self._runtime.bigworld, 'clearEntitiesAndSpaces', None)
        if not callable(clear):
            raise RuntimeError(
                'BigWorld.clearEntitiesAndSpaces is unavailable')
        hangar_space = getattr(
            self._runtime.hangar_space, 'g_hangarSpace', None)
        if hangar_space is None:
            raise RuntimeError('hangar space owner is unavailable')
        if not (bool(getattr(hangar_space, 'inited', False)) and
                bool(getattr(hangar_space, 'spaceInited', False))):
            raise RuntimeError(
                'hangar space is not ready for battle transition')
        return clear, hangar_space

    def _retire_lobby_entities(self, boundary):
        """Cross the same Account-to-Avatar boundary as the #1513 observer.

        BigWorld cannot safely promote a client-only Avatar while the lobby
        Account and hangar space are still alive.  The public 0.9.22 observer
        clears them before creating its Avatar; retaining the Account here can
        terminate the native process before Python gets a traceback.
        """
        clear, hangar_space = boundary
        # PlayerAccount.onBecomeNonPlayer owns the complete stock transition:
        # it first detaches ChatManager and all account helpers, then its
        # personality event destroys current/preview vehicles and HangarSpace.
        # Clearing only HangarSpace leaves zombie references to the Account
        # after BigWorld empties the PyEntity dictionary.
        self._lobby_retire_started = True
        if not self._runtime.compatibility.retire_current_player():
            raise RuntimeError('lobby Account retirement did not run')
        if (bool(getattr(hangar_space, 'inited', False)) or
                bool(getattr(hangar_space, 'spaceInited', False))):
            raise RuntimeError(
                'Account retirement did not destroy the hangar space')

        # Keep Account.g_accountRepository alive deliberately.  Exact #1513
        # PlayerAvatar.__init__ reuses its syncData, intUserSettings and
        # prebattleInvitations; the public observer creates that repository
        # when necessary instead of deleting it during this transition.
        clear()
        try:
            player = self._runtime.bigworld.player()
        except ReferenceError:
            player = None
        if player is not None:
            raise RuntimeError('lobby Account survived battle transition')

    def _actual_gui_space_id(self):
        """Read the accepted AppLoader state, not its optimistic context."""
        state = getattr(
            self._runtime.app_loader, '_AppLoader__state', None)
        get_space_id = getattr(state, 'getSpaceID', None)
        if not callable(get_space_id):
            raise RuntimeError('actual battle GUI state is unavailable')
        return get_space_id()

    def _enter_battle_loading(self):
        """Dispose the live lobby before retiring its Account owner."""
        space_ids = self._runtime.gui_global_space_id
        app_loader = self._runtime.app_loader
        if self._actual_gui_space_id() != space_ids.LOBBY:
            raise RuntimeError('battle GUI is not in the lobby state')
        if not app_loader.showBattleLoading():
            raise RuntimeError('battle loading GUI transition was rejected')
        if self._actual_gui_space_id() != space_ids.BATTLE_LOADING:
            raise RuntimeError('battle loading GUI transition did not finish')

    def _create_native_battle_map(self, map_name):
        """Use stock map bookkeeping without starting its viewer UI.

        OfflineMapCreator is a map-viewer helper: it opens the battle page
        before loading, then replaces the battle camera and leaves the GUI
        visibility watcher disabled.  The LAN runtime intentionally starts the
        normal PlayerAvatar battle session, whose ArenaLoadController owns the
        eventual battle page.  Both viewer-only steps are suppressed here.
        The stock helper still owns space creation, geometry mapping, Avatar
        properties and teardown ids.
        """
        creator = self._runtime.offline_map_creator
        setup_name = '_OfflineMapCreator__setupCamera'
        original_setup = getattr(creator, setup_name, None)
        if not callable(original_setup):
            raise RuntimeError(
                'OfflineMapCreator viewer-camera boundary is unavailable')
        creator_dict = getattr(creator, '__dict__', {})
        had_instance_setup = setup_name in creator_dict
        original_instance_setup = creator_dict.get(setup_name)

        app_loader = self._runtime.app_loader
        page_name = 'showBattlePage'
        original_show_page = getattr(app_loader, page_name, None)
        if not callable(original_show_page):
            raise RuntimeError(
                'OfflineMapCreator battle-page boundary is unavailable')
        # Exact _AppLoader uses __slots__, so its instance cannot be patched.
        # Patch the defining class for this synchronous create() window.  Read
        # and restore the raw class attribute to avoid Python 2 bound-method
        # wrappers and never overwrite another patch installed meanwhile.
        loader_type = type(app_loader)
        loader_dict = getattr(loader_type, '__dict__', {})
        had_class_show_page = page_name in loader_dict
        original_class_show_page = loader_dict.get(page_name)

        bigworld = self._runtime.bigworld
        game_module = self._runtime.game

        mapping_name = 'addSpaceGeometryMapping'
        bigworld_dict = getattr(bigworld, '__dict__', {})
        had_instance_mapping = mapping_name in bigworld_dict
        original_instance_mapping = bigworld_dict.get(mapping_name)
        original_add_mapping = getattr(bigworld, mapping_name, None)
        if not callable(original_add_mapping):
            raise RuntimeError(
                'BigWorld.addSpaceGeometryMapping boundary is unavailable')

        original_abort = getattr(game_module, 'abort', None)
        if not callable(original_abort):
            raise RuntimeError('game.abort boundary is unavailable')

        def defer_battle_page(unused_app_loader):
            return None

        def finish_battle_map_setup():
            # OfflineMapCreator disables this watcher before mapping.  Keep it
            # disabled while native static-scene handlers consume the selected
            # gameplay mapping, then restore normal battle GUI visibility at
            # the stock setup-camera boundary without the viewer camera.
            set_watcher = getattr(bigworld, 'setWatcher', None)
            if callable(set_watcher):
                set_watcher('Visibility/GUI', True)

        def reject_game_abort(*unused_args, **unused_kwargs):
            raise RuntimeError(
                'native Avatar requested game.abort during battle start')

        def add_standard_space_geometry(space_id, *args, **kwargs):
            self._prepare_standard_mapping_visibility(space_id)
            call_with_mask = getattr(
                self._runtime, 'call_with_standard_gameplay_mask', None)
            if not callable(call_with_mask):
                raise RuntimeError(
                    '#1513 native mapping-mask boundary is unavailable')
            return call_with_mask(
                original_add_mapping, (space_id,) + args, kwargs)

        setattr(loader_type, page_name, defer_battle_page)
        try:
            game_module.abort = reject_game_abort
            setattr(bigworld, mapping_name, add_standard_space_geometry)
            setattr(creator, setup_name, finish_battle_map_setup)
            try:
                creator.create(map_name)
            finally:
                current_add_mapping = getattr(
                    bigworld, '__dict__', {}).get(mapping_name)
                if current_add_mapping is add_standard_space_geometry:
                    if had_instance_mapping:
                        setattr(
                            bigworld, mapping_name,
                            original_instance_mapping)
                    else:
                        try:
                            delattr(bigworld, mapping_name)
                        except AttributeError:
                            pass
                current_setup = getattr(
                    creator, '__dict__', {}).get(setup_name)
                if current_setup is finish_battle_map_setup:
                    if had_instance_setup:
                        setattr(
                            creator, setup_name, original_instance_setup)
                    else:
                        try:
                            delattr(creator, setup_name)
                        except AttributeError:
                            pass
        finally:
            if getattr(game_module, 'abort', None) is reject_game_abort:
                game_module.abort = original_abort
            current_show_page = getattr(
                loader_type, '__dict__', {}).get(page_name)
            if current_show_page is defer_battle_page:
                if had_class_show_page:
                    setattr(
                        loader_type, page_name, original_class_show_page)
                else:
                    try:
                        delattr(loader_type, page_name)
                    except AttributeError:
                        pass

    def _install_battle_gui_guard(self):
        """Keep exact #1513 GUI transitions ordered for this local round.

        Space loading and arena-roster polling run on separate callbacks.  The
        stock server makes their ordering deterministic; this client-only
        runtime must tolerate either callback arriving first without allowing
        Lobby -> Battle or a late Battle -> BattleLoading regression.
        """
        if self._app_loader_guard is not None:
            return
        app_loader = self._runtime.app_loader
        loader_type = type(app_loader)
        loader_dict = getattr(loader_type, '__dict__', {})
        original_loading = loader_dict.get('showBattleLoading')
        original_page = loader_dict.get('showBattlePage')
        space_ids = getattr(self._runtime, 'gui_global_space_id', None)
        if (not callable(original_loading) or not callable(original_page) or
                space_ids is None):
            raise RuntimeError('battle GUI state boundaries are unavailable')
        lobby_id = space_ids.LOBBY
        loading_id = space_ids.BATTLE_LOADING
        battle_id = space_ids.BATTLE

        def actual_space_id(loader):
            if loader is not app_loader:
                state = getattr(loader, '_AppLoader__state', None)
                get_state_space_id = getattr(state, 'getSpaceID', None)
                if not callable(get_state_space_id):
                    raise RuntimeError(
                        'actual battle GUI state is unavailable')
                return get_state_space_id()
            # Exact #1513 getSpaceID() returns __ctx.guiSpaceID.  changeSpace()
            # writes that requested id *before* asking the current state to
            # accept it, so a rejected transition leaves the public value
            # polluted.  The state object is the authoritative boundary.
            return self._actual_gui_space_id()

        if actual_space_id(app_loader) != lobby_id:
            raise RuntimeError('battle GUI is not in the lobby state')

        def ordered_loading(loader):
            if loader is not app_loader:
                return original_loading(loader)
            if actual_space_id(loader) != lobby_id:
                return None
            result = original_loading(loader)
            if (not result or
                    actual_space_id(loader) != loading_id):
                return None
            return result

        def ordered_page(loader):
            if loader is not app_loader:
                return original_page(loader)
            current = actual_space_id(loader)
            if current == battle_id:
                return None
            if current == lobby_id:
                if not ordered_loading(loader):
                    return None
                current = actual_space_id(loader)
            # Never hand an illegal transition to Scaleform.  The startup
            # timeout will recover the lobby if the native loading state could
            # not be established.
            if current != loading_id:
                return None
            result = original_page(loader)
            if (not result or
                    actual_space_id(loader) != battle_id):
                return None
            return result

        loader_type.showBattleLoading = ordered_loading
        loader_type.showBattlePage = ordered_page
        self._app_loader_guard = {
            'type': loader_type,
            'loading_original': original_loading,
            'loading_wrapper': ordered_loading,
            'page_original': original_page,
            'page_wrapper': ordered_page,
        }

    def _restore_battle_gui_guard(self):
        guard = self._app_loader_guard
        self._app_loader_guard = None
        if guard is None:
            return
        loader_type = guard['type']
        loader_dict = getattr(loader_type, '__dict__', {})
        if (loader_dict.get('showBattleLoading') is
                guard['loading_wrapper']):
            loader_type.showBattleLoading = guard['loading_original']
        if loader_dict.get('showBattlePage') is guard['page_wrapper']:
            loader_type.showBattlePage = guard['page_original']

    def _standard_arena(self, map_name):
        wanted = tactical_maps.normalize_map_name(map_name)
        for unused_id, arena_type in self._runtime.arena_cache.items():
            geometry = tactical_maps.normalize_map_name(
                getattr(arena_type, 'geometryName', None))
            if (geometry == wanted and
                    getattr(arena_type, 'gameplayName', None) ==
                    STANDARD_GAMEPLAY):
                return arena_type
        return None

    @staticmethod
    def _arena_bounds_from_type(arena_type):
        """Read #1513's official red-border rectangle once per battle."""
        bounding_box = getattr(arena_type, 'boundingBox', None)
        try:
            bottom_left, upper_right = bounding_box[0], bounding_box[1]

            def coordinates(point):
                try:
                    return float(point[0]), float(point[1])
                except (AttributeError, IndexError, TypeError):
                    return float(point.x), float(point.y)

            minimum_x, minimum_z = coordinates(bottom_left)
            maximum_x, maximum_z = coordinates(upper_right)
            values = (minimum_x, minimum_z, maximum_x, maximum_z)
            if (any(math.isnan(value) or math.isinf(value)
                    for value in values) or
                    minimum_x >= maximum_x or minimum_z >= maximum_z):
                return None
            return values
        except (AttributeError, IndexError, TypeError, ValueError,
                OverflowError):
            # The pinned client always publishes this field. A malformed
            # optional test/future arena must not turn map creation into a
            # system-error draw merely because the extra safety rail cannot
            # be installed.
            return None

    def _configure_standard_space_visibility(self, space_id=None):
        """Best-effort maintenance of the mapped typed gameplay bit."""
        try:
            return self._apply_standard_space_visibility(space_id)
        except _LiveSpaceVisibilityPending:
            # The mapping itself already received the selected bit.  The
            # periodic guard can finish the typed write when exact #1513
            # publishes this client-only space through BigWorld.spaces.
            return None
        except Exception as error:
            # Visibility only filters map decoration such as inactive bases.
            # A missing client-only space contract must never discard an
            # otherwise playable battle.
            self._standard_space_visibility = None
            self._warn_standard_space_visibility(error)
            return None

    def _standard_visibility_contract(self):
        """Return the selected gameplay bit and exact client/server masks."""
        visibility = getattr(
            self._runtime, 'client_visibility_flags', None)
        gameplay_mask = getattr(
            self._runtime, 'arena_visibility_mask', None)
        if visibility is None or not callable(gameplay_mask):
            raise RuntimeError(
                '#1513 space visibility boundary is unavailable')
        try:
            # These are unsigned 32-bit masks.  CLIENT_MASK is a Python long
            # in the 32-bit #1513 client and cannot be narrowed through int().
            client_bits = visibility.CLIENT_MASK
            server_bits = visibility.SERVER_MASK
            gameplay_id = int(self._arena_type.gameplayID)
            selected_bit = gameplay_mask(gameplay_id)
        except (AttributeError, TypeError, ValueError, OverflowError):
            raise RuntimeError(
                '#1513 space visibility contract is invalid')
        if (client_bits & server_bits or
                (client_bits | server_bits) != 0xffffffff or
                selected_bit <= 0 or selected_bit & (selected_bit - 1) or
                selected_bit & ~server_bits):
            raise RuntimeError(
                '#1513 gameplay visibility mask is invalid')
        return selected_bit, client_bits, server_bits

    def _prepare_standard_mapping_visibility(self, space_id):
        """Latch the CTF bit before the exact native mapping call."""
        selected_bit, client_bits, server_bits = \
            self._standard_visibility_contract()
        # This runtime exposes only standard CTF arenas.  The exact #1513
        # native helper narrows the hard-coded all-bits mapping mask to this
        # one wire bit; reject any build/data drift before touching code.
        if selected_bit != 0x00000001:
            raise RuntimeError(
                '#1513 standard gameplay visibility bit is not 0x1')
        self._standard_space_visibility = (
            space_id, selected_bit, client_bits, server_bits)
        self._next_space_visibility_check = (
            self._clock() + SPACE_VISIBILITY_CHECK_SECONDS)
        return selected_bit

    def _apply_standard_space_visibility(self, space_id=None):
        """Maintain the live typed property after geometry is mapped."""
        selected_bit, client_bits, server_bits = \
            self._standard_visibility_contract()
        if space_id is None:
            space_id = self._avatar.spaceID
        self._standard_space_visibility = (
            space_id, selected_bit, client_bits, server_bits)
        self._next_space_visibility_check = (
            self._clock() + SPACE_VISIBILITY_CHECK_SECONDS)
        space = self._live_standard_space(space_id)
        actual = space.itemsVisibilityMask
        if actual != selected_bit:
            space.itemsVisibilityMask = selected_bit
            actual = space.itemsVisibilityMask
        if actual != selected_bit:
            raise RuntimeError(
                '#1513 typed gameplay visibility mask was not applied: '
                'expected=0x%x actual=%r' % (selected_bit, actual))
        return selected_bit

    def _live_standard_space(self, space_id):
        """Return mapped space data, or defer while native publication lags."""
        spaces = getattr(self._runtime.bigworld, 'spaces', None)
        if spaces is None:
            raise RuntimeError(
                '#1513 live space visibility data is unavailable')
        try:
            return spaces[space_id]
        except KeyError:
            # Exact #1513 raises ``No space(<id>) exists.`` during the short
            # interval between geometry mapping and PySpaces publication.
            raise _LiveSpaceVisibilityPending()

    def _maintain_standard_space_visibility(self, now):
        """Restore a gameplay bit if later stock code widens the server mask."""
        boundary = self._standard_space_visibility
        if boundary is None or now < self._next_space_visibility_check:
            return False
        self._next_space_visibility_check = (
            now + SPACE_VISIBILITY_CHECK_SECONDS)
        space_id, selected_bit, client_bits, server_bits = boundary
        try:
            space = self._live_standard_space(space_id)
            current = space.itemsVisibilityMask
            if current & server_bits == selected_bit:
                return False
            # Preserve any live client-only flags while replacing only the
            # server gameplay selection that controls bases and capture zones.
            corrected = (current & client_bits) | selected_bit
            space.itemsVisibilityMask = corrected
            actual = space.itemsVisibilityMask
            if actual != corrected:
                raise RuntimeError(
                    '#1513 typed gameplay visibility mask was not applied: '
                    'expected=0x%x actual=%r' % (corrected, actual))
        except _LiveSpaceVisibilityPending:
            return False
        except Exception as error:
            self._standard_space_visibility = None
            self._warn_standard_space_visibility(error)
            return False
        return True

    def _warn_standard_space_visibility(self, error):
        if self._space_visibility_warning_reported:
            return
        self._space_visibility_warning_reported = True
        self._warn_optional_failure('map visibility filtering', error)

    @staticmethod
    def _bounded_failure_reason(error, limit=OPTIONAL_WARNING_TEXT_LIMIT):
        """Return one single-line diagnostic safe for logs and the wire."""
        try:
            message = str(error)
        except Exception:
            message = error.__class__.__name__
        message = ' '.join(message.split())
        if not message:
            message = error.__class__.__name__
        return message[:max(1, int(limit))]

    def _optional_feature_enabled(self, feature):
        disabled = getattr(self, '_disabled_optional_features', None)
        if disabled is None:
            disabled = set()
            self._disabled_optional_features = disabled
        return str(feature) not in disabled

    def _warn_optional_failure(self, feature, error, disable=True):
        """Log one bounded warning per feature and round."""
        feature = str(feature)
        disabled = getattr(self, '_disabled_optional_features', None)
        if disabled is None:
            disabled = set()
            self._disabled_optional_features = disabled
        if disable:
            disabled.add(feature)
        reported = getattr(self, '_optional_failures_reported', None)
        if reported is None:
            reported = set()
            self._optional_failures_reported = reported
        if feature in reported:
            return False
        reported.add(feature)
        outcome = ('disabled for this round' if disable else
                   'degraded for this round')
        sys.stdout.write(
            '[Offline LAN 0.9.22] optional %s %s: %s\n' % (
                feature, outcome, self._bounded_failure_reason(error)))
        return True

    def _run_optional_feature(self, feature, callback, args=(),
                              on_error=None):
        """Run one presentation boundary without widening frame failure."""
        if not self._optional_feature_enabled(feature):
            return False
        try:
            return callback(*args)
        except Exception as error:
            if callable(on_error):
                try:
                    on_error()
                except Exception as cleanup_error:
                    error = RuntimeError(
                        '%s; disable cleanup failed: %s' % (
                            error, cleanup_error))
            self._warn_optional_failure(feature, error)
            return False

    def _disable_standard_space_visibility(self):
        self._standard_space_visibility = None
        return True

    def _disable_destructibles_for_round(self, error):
        sensor = self._destructibles
        self._destructibles = None
        if sensor is not None:
            for name, args in (
                    ('set_event_sink', (None,)),
                    ('reset', ()),
                    ('set_catalog', (None,))):
                callback = getattr(sensor, name, None)
                if not callable(callback):
                    continue
                try:
                    callback(*args)
                except Exception:
                    pass
        self._warn_optional_failure('destructible interactions', error)
        return True

    def _observe_destructibles_disabled(self, message):
        """Apply the server's round-wide optional-feature decision once."""
        if (not isinstance(message, dict) or
                message.get('destructibles_disabled') is not True):
            return False
        if self._start_message is None:
            self._start_message = {}
        self._start_message['destructibles_disabled'] = True
        reason = self._bounded_failure_reason(
            message.get('destructibles_disabled_reason') or
            'server disabled destructible interactions')
        self._start_message['destructibles_disabled_reason'] = reason
        if self._destructibles is not None:
            self._disable_destructibles_for_round(RuntimeError(
                'LAN server disabled destructible interactions: %s' %
                reason))
        return True

    def _clock(self):
        function = getattr(self._runtime.bigworld, 'time', None)
        if callable(function):
            try:
                return float(function())
            except Exception:
                pass
        return time.time()

    def _server_clock(self):
        """Return the clock used by exact #1513 countdown consumers."""
        function = getattr(self._runtime.bigworld, 'serverTime', None)
        if callable(function):
            try:
                return float(function())
            except Exception:
                pass
        return self._clock()

    def _server_entity(self, entity_id):
        """Resolve authority state without widening the stock AOI view."""
        if self._remote_factory is not None:
            entity = self._remote_factory.get(entity_id)
            if entity is not None:
                return entity
        return self._runtime.bigworld.entity(entity_id)

    def _attack_reason(self, member, fallback):
        """Resolve exact #1513 ATTACK_REASON indices without guessing."""
        constants = self._runtime.constants
        group = getattr(constants, 'ATTACK_REASON', None)
        indices = getattr(constants, 'ATTACK_REASON_INDICES', {})
        name = getattr(group, member, None)
        try:
            return int(indices[name])
        except (KeyError, TypeError, ValueError):
            return int(fallback)

    def _schedule(self, delay, function, ammo=False):
        generation = self._generation
        if ammo and self._ammo_callback_id is not None:
            try:
                self._runtime.bigworld.cancelCallback(
                    self._ammo_callback_id)
            except Exception:
                pass
            self._ammo_callback_id = None
            self._ammo_callback_token = None
        token = object()
        if ammo:
            self._ammo_callback_token = token
        else:
            self._callback_token = token
        measured = _underlying_function(function) is not _underlying_function(
            self._frame)

        def invoke():
            if ammo:
                if self._ammo_callback_token is token:
                    self._ammo_callback_token = None
                    self._ammo_callback_id = None
            else:
                if self._callback_token is token:
                    self._callback_token = None
                    self._callback_id = None
            if generation != self._generation:
                return
            if not measured:
                function()
                return
            started = _PROFILE_CLOCK()
            try:
                function()
            finally:
                self._offframe_seconds += _PROFILE_CLOCK() - started

        try:
            callback_id = self._runtime.bigworld.callback(delay, invoke)
        except Exception:
            if ammo and self._ammo_callback_token is token:
                self._ammo_callback_token = None
            elif not ammo and self._callback_token is token:
                self._callback_token = None
            raise
        if ammo:
            if self._ammo_callback_token is token:
                self._ammo_callback_id = callback_id
        else:
            if self._callback_token is token:
                self._callback_id = callback_id

    def _local_battle_descriptor(self, vehicle_name):
        """Return the player's own descriptor with the garage fitting on it.

        A descriptor built from the type name alone carries the stock modules
        and no optional devices, so the battle would measure a different tank
        from the one the garage panel measures.
        """
        vehicles = self._runtime.vehicles
        fitting = self._garage_loadout_snapshot()['fitting']
        if fitting is not None and fitting[1] == vehicle_name:
            try:
                return vehicles.VehicleDescr(compactDescr=fitting[0])
            except Exception as error:
                raise RuntimeError(
                    'the mounted vehicle descriptor is unreadable: %s' %
                    error)
        if not getattr(self, '_worker_mode', False):
            raise RuntimeError(
                'the mounted vehicle descriptor does not match %s' %
                vehicle_name)
        return vehicles.VehicleDescr(typeName=vehicle_name)

    def _create_entities(self):
        try:
            self.state = 'loading_entities'
            self._vehicle_ready_deadline = 0.0
            if not self._worker_mode:
                self._install_decal_probe()
            local = self._local_state()
            descriptor = self._local_battle_descriptor(
                local.get('vehicle', self._config['vehicle']))
            self._binding = BigWorldVehicleBinding(
                self._runtime.bigworld, self._avatar,
                self._runtime.constants, self._runtime.vehicles.VehicleDescr,
                self._runtime.encode_gun_angles,
                outfit_provider=lambda unused_descriptor: (
                    self._garage_loadout_snapshot().get('outfit') or ''),
                authority_entity_resolver=self._server_entity)
            factory_type = (NativeRemoteVehicleFactory
                            if self._config.get(
                                'native_remote_vehicles', True)
                            else RemoteVehicleFactory)
            factory_kwargs = {
                'camouflages': getattr(self._runtime, 'camouflages', None),
                'vehicular': getattr(self._runtime, 'vehicular', None),
                'data_links': getattr(self._runtime, 'data_links', None),
                'enable_track_animation': self._config.get(
                    'bot_track_animation', False),
                # The visible client warms destroyed part resources before
                # battle; the hidden worker never draws a wreck and retains
                # its live collision compound instead.
                'prewarm_wreck_resources': not self._worker_mode}
            if factory_type is NativeRemoteVehicleFactory:
                factory_kwargs.update({
                    'binding': self._binding,
                    'compatibility': self._runtime.compatibility,
                    # SnapshotSync already supplies every guest pose on each
                    # render frame. Authority Bots opt into interpolation per
                    # entity after creation, so remote humans and live
                    # authority handoffs never retain the wrong provider.
                    'interpolate_motion': False,
                    # Visible remote tanks retain their established model/gun
                    # presentation. Only the hidden worker needs native
                    # hydraulic matrices as authoritative collision geometry.
                    'authority_geometry': self._worker_mode})
                sys.stdout.write(
                    '[Offline LAN 0.9.22] native remote Vehicle presentation '
                    'enabled; copied LAN physics remains authoritative\n')
            self._remote_factory = factory_type(
                self._runtime.bigworld, self._runtime.math,
                self._runtime.model_assembler, self._avatar.spaceID,
                **factory_kwargs)
            self._remote_factory.prepare_descriptor(descriptor)
            builder = EntityPropertyBuilder(
                BigWorldVehicleBinding.PROPERTY_NAMES)
            self._sender = _LANInputSender(self)
            position, yaw = self._state_world_pose(local)
            self._local_position = position
            self._local_yaw = yaw
            self._local_descriptor = descriptor
            # Resolve the complete line-up while BattleLoading is still up.
            # Every unique destroyed-model prerequisite is submitted now in
            # this one startup callback; bot presentation staggering is a
            # separate later phase and never throttles this prewarm.
            lineup_ready = self._prepare_bot_vehicle_assignments(descriptor)
            if self._start_message.get('bot_lineup') and not lineup_ready:
                raise RuntimeError(
                    'the exact Bot lineup is not available in this client')
            prewarm_enabled = getattr(
                self._remote_factory, 'prewarm_wrecks_enabled', None)
            if callable(prewarm_enabled) and prewarm_enabled():
                for vehicle_name in sorted(set(
                        self._bot_vehicle_assignments.values())):
                    self._resolve_descriptor(vehicle_name)
            commands = self._runtime.account_commands
            self._server = AvatarServerBridge(
                self._avatar, self._binding, builder, self._sender,
                account_commands=(commands.CMD_GET_AVATAR_SYNC,
                                  commands.CMD_ADD_INT_USER_SETTINGS,
                                  commands.CMD_DEL_INT_USER_SETTINGS),
                on_account_int_command=(
                    self._runtime.compatibility.dispatch_account_int_command),
                on_ready=self._on_client_ready,
                on_leave=self._defer_avatar_leave,
                on_vehicle_enter=self._prepare_local_presentation,
                on_viewpoint_switch=self._switch_postmortem_viewpoint,
                on_monitor_vehicle_devices=(
                    self.monitor_vehicle_damaged_devices),
                # ClientArena starts in WAITING.  Keep that stock state while
                # bot presentations finish, then publish PREBATTLE exactly
                # once from on_battle_live after the shared ready barrier.
                initial_period=None)
            self._runtime.compatibility.attach_avatar_server(
                self._avatar, self._server)
            properties = self._binding.properties_from_compact_descr(
                descriptor.makeCompactDescr(), int(local.get('team', 1)),
                local.get('name', self._config.get('name', 'Player')))
            properties['health'] = max(1, min(
                int(local.get('health', descriptor.maxHealth)),
                int(descriptor.maxHealth)))
            snapshot = {
                'properties': properties,
                'position': self._vector(position),
                'rotation': _engine_rotation(yaw),
                'period': 'battle',
            }
            vehicle_id = self._server.addVehicleToArena(snapshot)
            self._synchronise_player_identity(vehicle_id)
            self._invalidate_native_arena_info()
            local_key = 'player:%s' % self.client.player_id
            self._records[local_key] = {
                'engine_id': vehicle_id, 'state': dict(local),
                'kind': 'player', 'network_id': self.client.player_id,
                'local': True, 'ready': False,
                'shot_penalty_until': 0.0}
            self._records_revision += 1
            self._schedule(0.0, self._wait_for_client_ready)
        except Exception as error:
            self._fail(error)

    def _invalidate_native_arena_info(self):
        """Start stock BattleLoading after player id and roster are present."""
        provider = getattr(self._avatar, 'guiSessionProvider', None)
        shared = getattr(provider, 'shared', None)
        arena_load = getattr(shared, 'arenaLoad', None)
        invalidate = getattr(arena_load, 'invalidateArenaInfo', None)
        if not callable(invalidate):
            raise RuntimeError('native arena-load controller is unavailable')
        invalidate()

    def _synchronise_player_identity(self, expected_vehicle_id):
        """Refresh ArenaDP before marker plugins cache the local vehicle id."""
        expected_vehicle_id = int(expected_vehicle_id)
        if expected_vehicle_id <= 0:
            raise RuntimeError('#1513 player vehicle identity is invalid')
        get_player = getattr(self._runtime.bigworld, 'player', None)
        if not callable(get_player):
            raise RuntimeError('#1513 BigWorld player API is unavailable')
        current_player = get_player()
        if current_player is not self._avatar:
            raise RuntimeError(
                '#1513 BigWorld player changed before ArenaDP refresh')
        avatar_vehicle_id = int(getattr(current_player, 'playerVehicleID', 0))
        if avatar_vehicle_id != expected_vehicle_id:
            raise RuntimeError(
                '#1513 Avatar player identity mismatch before ArenaDP '
                'refresh: expected=%s avatar=%s' % (
                    expected_vehicle_id, avatar_vehicle_id))
        avatar_team = int(getattr(current_player, 'team', 0))
        if avatar_team not in (1, 2):
            raise RuntimeError(
                '#1513 Avatar team is invalid before ArenaDP refresh: '
                'team=%s' % avatar_team)
        provider = getattr(self._avatar, 'guiSessionProvider', None)
        get_arena_dp = getattr(provider, 'getArenaDP', None)
        if not callable(get_arena_dp):
            raise RuntimeError('#1513 ArenaDP provider is unavailable')
        arena_dp = get_arena_dp()
        required = getattr(arena_dp, 'isRequiredDataExists', None)
        get_player_vehicle_id = getattr(
            arena_dp, 'getPlayerVehicleID', None)
        if not callable(required) or not callable(get_player_vehicle_id):
            raise RuntimeError('#1513 ArenaDP player identity API is unavailable')
        # #1513 initializes ArenaDP before the local Vehicle exists, so its
        # cached player id is the integer 0.  getPlayerVehicleID(True) only
        # refreshes a None cache and therefore cannot repair that state.
        # isRequiredDataExists() is the stock boundary which treats 0 as
        # incomplete and re-reads the already-validated Avatar identity.
        if not required():
            raise RuntimeError('#1513 ArenaDP player identity is incomplete')
        refreshed_vehicle_id = int(get_player_vehicle_id(False))
        if refreshed_vehicle_id != expected_vehicle_id:
            raise RuntimeError(
                '#1513 ArenaDP player identity refresh mismatch: '
                'expected=%s arenaDP=%s' % (
                    expected_vehicle_id, refreshed_vehicle_id))
        self._runtime.compatibility.synchronise_vehicle_marker_identity(
            expected_vehicle_id)
        return self._assert_player_identity(expected_vehicle_id)

    def _assert_player_identity(self, expected_vehicle_id):
        """Reject any drift that would relabel player damage as ally damage."""
        expected_vehicle_id = int(expected_vehicle_id)
        avatar_vehicle_id = int(getattr(self._avatar, 'playerVehicleID', 0))
        provider = getattr(self._avatar, 'guiSessionProvider', None)
        get_arena_dp = getattr(provider, 'getArenaDP', None)
        if not callable(get_arena_dp):
            raise RuntimeError('#1513 ArenaDP provider is unavailable')
        arena_dp = get_arena_dp()
        get_player_vehicle_id = getattr(
            arena_dp, 'getPlayerVehicleID', None)
        if not callable(get_player_vehicle_id):
            raise RuntimeError('#1513 ArenaDP player identity API is unavailable')
        arena_vehicle_id = int(get_player_vehicle_id(False))
        if (avatar_vehicle_id != expected_vehicle_id or
                arena_vehicle_id != expected_vehicle_id):
            raise RuntimeError(
                '#1513 player identity mismatch: expected=%s avatar=%s '
                'arenaDP=%s' % (
                    expected_vehicle_id, avatar_vehicle_id,
                    arena_vehicle_id))
        self._runtime.compatibility.assert_vehicle_marker_identity(
            expected_vehicle_id)
        return True

    def _wait_for_client_ready(self):
        if self.state != 'loading_entities':
            return
        try:
            if float(self._runtime.bigworld.spaceLoadStatus()) < 1.0:
                if self._clock() >= self._deadline:
                    self._fail(RuntimeError('map loading timed out'))
                    return
                self._schedule(0.05, self._wait_for_client_ready)
                return
            if self._vehicle_ready_deadline <= 0.0:
                self._vehicle_ready_deadline = self._clock() + float(
                    self._config.get('startupTimeoutSeconds', 30.0))
            self._server.flushClientReady()
            if self._client_ready_received:
                self._finish_entity_startup()
                return
        except Exception as error:
            self._fail(error)
            return
        if self._clock() >= self._vehicle_ready_deadline:
            self._fail(RuntimeError(
                'player Vehicle did not enter world before startup timeout'))
            return
        self._schedule(0.05, self._wait_for_client_ready)

    def _finish_entity_startup(self):
        try:
            if self.state != 'loading_entities':
                return
            if not self._wreck_prewarm_ready_for_startup():
                self._schedule(0.05, self._finish_entity_startup)
                return
            descriptor = self._local_descriptor
            if descriptor is None:
                raise RuntimeError('player Vehicle descriptor is unavailable')
            local_key = 'player:%s' % self.client.player_id
            record = self._records.get(local_key)
            if record is None:
                raise RuntimeError('player Vehicle record is unavailable')
            # Exact #1513 reapplies ClientVisibilityFlags late in
            # PlayerAvatar.__onInitStepCompleted.  Cross that stock boundary
            # before restoring the server-selected gameplay bit for this
            # client-only space.
            self._configure_standard_space_visibility()
            record['ready'] = True
            self._attach_local_presentation()
            if not self._worker_mode:
                self._runtime.compatibility.set_control_mode_listener(
                    self._on_control_mode_changed)
                self._gun_state = gun_mechanics.GunState(
                    descriptor, self._local_loadout(descriptor),
                    ammo_layout=self._local_ammo_layout())
                self._log_local_ammo(self._gun_state)
                self._log_effective_parameters(descriptor)
                self._gun_last_tick = self._clock()
            self._sync = SnapshotSync(
                self.client.player_id, on_event=self._apply_sync_event,
                clock=self._clock, pose_safe=self._baked_pose_safe)
            # ``battle_start.bots`` is only a roster reservation.  It has no
            # world pose yet.  Registering those identities here used to make
            # an empty snapshot received during map loading tombstone all 29
            # bots; later canonical states were then intentionally ignored as
            # attempts to resurrect dead entities.  Seed only humans and let
            # the authority manifest / first canonical snapshot create bots.
            initial_manifest = dict(self._start_message)
            initial_manifest['bots'] = []
            self._sync.manifest(initial_manifest)
            latest_snapshot = getattr(self.client, 'last_snapshot', None)
            if (isinstance(latest_snapshot, dict) and
                    latest_snapshot.get('round_id') ==
                    self._start_message.get('round_id')):
                self._last_snapshot = dict(latest_snapshot)
            if self._last_snapshot is not None:
                self._restore_local_equipment_snapshot(
                    self._last_snapshot, present=True)
                self._sync.snapshot(self._last_snapshot)
            self._bots = BotRuntime(
                self.client.player_id,
                descriptor_resolver=self._resolve_descriptor,
                player_descriptor_resolver=self._resolve_player_descriptor,
                direction_probe=self._direction_probe,
                vehicle_selector=self._select_bot_vehicle,
                visibility_probe=self._bot_visibility,
                firing_lane_probe=self._bot_firing_lane,
                friendly_lane_probe=self._bot_friendly_firing_lane,
                direct_launch_origin_probe=self._bot_direct_launch_origin,
                ballistic_solution_probe=self._bot_ballistic_solution,
                artillery_launch_probe=self._bot_artillery_launch,
                artillery_friendly_lane_probe=(
                    self._bot_artillery_friendly_lane),
                artillery_launch_cancel=self._bot_artillery_cancel,
                spawn_resolver=self._formation_pose,
                ground_probe=self._navigation_ground,
                physics_ground_probe=self._ground_y,
                obstacle_probe=self._navigation_obstacle,
                bounds=getattr(self._spawn_planner, 'bounds', None),
                cover_probe=self._sample_bot_cover,
                motion_resolver=self._resolve_bot_motion,
                motion_report=self._report_bot_destructible_contact,
                world_receipt_probe=self._direction_world_receipt,
                water_depth_probe=self._water_depth,
                ram_contact_probe=self._bot_ram_contact_armor,
                bot_equipment_resolver=(
                    self._default_bot_equipment_contracts),
                baked_graph=self._navigation_graph,
                # Keep the mature 0.8.2 authority model: the copied physics
                # integrator owns bot poses and the engine interpolates those
                # poses for presentation.  A remote #1513 Vehicle has no
                # retail server stream, so treating its WGVehiclePhysics as
                # authoritative leaves movement inputs without pose samples.
                # Keep logical probe counts for frame correlation, but do not
                # read a high-resolution clock around every native query. The
                # two clock calls are diagnostic work on the render thread and
                # cannot affect probe order, results, deadlines or budgets.
                # The hidden worker enables them only for its first five
                # authoritative seconds so we can separate native query time
                # from pure Python without permanently lowering its cadence.
                native_motion=False,
                probe_clock=(_PROFILE_CLOCK if self._worker_mode else None),
                probe_timing_seconds=(
                    WORKER_NATIVE_PROBE_SECONDS if self._worker_mode else 0.0),
                control_seconds=(
                    WORKER_CONTROL_SECONDS if self._worker_mode else None))
            self._bots.debug_logging = bool(
                self._config.get('debug_logging', False))
            # Sampled here, not before BotRuntime exists: the bot, navigator
            # and planner structures are most of what this port holds.
            reset_pose_animation_writes()
            self._report_memory('battle_start')
            provider = getattr(self._avatar, 'guiSessionProvider', None)
            vehicle_view_state = getattr(
                self._runtime, 'vehicle_view_state', None)
            if (not self._worker_mode and provider is not None and
                    vehicle_view_state is not None):
                self._sixth_sense = SixthSenseController(
                    self._runtime.bigworld.callback,
                    self._runtime.bigworld.cancelCallback,
                    lambda: self._generation,
                    lambda: self._has_sixth_sense,
                    lambda: (self.local_health() or 0) > 0,
                    lambda: self.state == 'running' and self._battle_live,
                    VehicleStatePresenter(provider, vehicle_view_state))
            bot_start_message = dict(self._start_message or {})
            if (self._worker_mode and
                    lan_protocol.HUMAN_RAM_TIMELINE_CAPABILITY in
                    getattr(self.client, 'capabilities', ()) and
                    lan_protocol.HUMAN_RAM_TIMELINE_CAPABILITY in
                    getattr(self.client, 'server_capabilities', ())):
                bot_start_message['human_ram_timeline'] = True
            for outgoing in self._bots.battle_start(bot_start_message):
                # The authority already owns the exact bot poses it is about
                # to publish.  Materialize that canonical lineup locally now,
                # like 0.8.2 does, instead of waiting for a server echo.  Do
                # not register it in SnapshotSync until the server echoes the
                # canonical lineup: an in-flight empty snapshot between send
                # and echo must not tombstone the local manifest.
                if outgoing.get('type') == 'bot_manifest':
                    for state in outgoing.get('bots') or ():
                        if isinstance(state, dict) and state.get('id') is not None:
                            self._queue_bot_create({
                                'type': 'create',
                                'entity': 'bot:%s' % state['id'],
                                'kind': 'bot', 'id': state['id'],
                                'state': state})
                if outgoing.get('type') == 'bot_manifest':
                    self._enqueue_bot_manifest(outgoing)
                else:
                    self._send_bot_message(outgoing)
            if self._last_snapshot is not None:
                self._bots.apply_snapshot(self._last_snapshot)
                self._remember_ram_bot_snapshot(self._last_snapshot)
            self.state = 'running'
            if not self._worker_mode:
                self._bind_local_arcade_camera()
                self._run_optional_feature(
                    'engine RPM presentation', self._publish_rpm,
                    (self._clock(), True))
            self._last_frame_time = self._clock()
            if not self._worker_mode:
                self._ammo_tick()
            if self.state != 'running':
                return
            if self._battle_result is not None:
                self._apply_battle_result(self._battle_result)
            if self.state != 'running':
                return
            ready = getattr(self.client, 'send_battle_ready', None)
            if not callable(ready):
                # Engine-free contract tests and non-LAN harnesses have no
                # socket load barrier. Preserve the copied local countdown.
                self.on_battle_live({
                    'countdown_seconds': self._prebattle_seconds(),
                    'battle_duration_seconds': self._battle_seconds(),
                })
            self._schedule(FRAME_SECONDS, self._frame)
        except Exception as error:
            self._fail(error)

    def _wreck_prewarm_ready_for_startup(self):
        """Keep the client in BattleLoading until raw wreck assets settle."""
        pending = getattr(
            self._remote_factory, 'wreck_prewarm_pending_count', None)
        if not callable(pending) or pending() <= 0:
            return True
        deadline = float(self._vehicle_ready_deadline or 0.0)
        if deadline <= 0.0 or self._clock() < deadline:
            return False
        abandon = getattr(
            self._remote_factory, 'abandon_pending_wreck_prewarm', None)
        if callable(abandon):
            abandon()
        return True

    def _local_state(self):
        for value in self._start_message.get('players') or ():
            if value.get('id') == self.client.player_id:
                return dict(value)
        result = {
            'id': self.client.player_id, 'name': self.client.name,
            'vehicle': self.client.vehicle, 'team': self.client.team,
            'slot': self.client.slot, 'health': self.client.max_health,
            'max_health': self.client.max_health, 'alive': True}
        return result

    def _prebattle_seconds(self):
        return max(0.0, _number(
            self._config.get('prebattleCountdownSeconds',
                             PREBATTLE_SECONDS), PREBATTLE_SECONDS))

    def _battle_seconds(self):
        return max(1.0, _number(
            self._config.get('battleDurationSeconds', BATTLE_SECONDS),
            BATTLE_SECONDS))

    def _prebattle_transition_ready(self, now):
        """Keep worker combat behind the server's accepted live boundary."""
        if (self._prebattle_deadline is None or
                float(now) < float(self._prebattle_deadline)):
            return False
        if not self._worker_mode:
            return True
        # The visible client may project the countdown from receipt time for a
        # smooth HUD.  A worker must wait for the first server snapshot whose
        # timing phase is actually ``battle``; otherwise RTT estimation can
        # make its first bot_state/fire/ram edge arrive one tick too early and
        # be rejected as combat_closed.
        return getattr(self.client, 'combat_phase', None) == 'battle'

    def _begin_battle(self):
        if self._battle_live:
            return False
        duration = self._battle_seconds()
        deadline = getattr(self.client, 'combat_end_deadline', None)
        if deadline is not None:
            duration = max(0.1, float(deadline) - _monotonic_time())
        if not self._worker_mode:
            self._binding.arena_period('battle', duration)
        self._battle_live = True
        # Publish one fresh live set even when it matches the prebattle state.
        self._spotted_signature = None
        self._next_spotting_time = 0.0
        # The countdown froze gun laying and firing; the battle releases both.
        self._set_gun_locked(False)
        self._prebattle_deadline = None
        self._last_frame_time = self._clock()
        if self._gun_state is not None and self._server is not None:
            self._publish_reload_event(
                self._gun_state.reload_time,
                self._gun_state.reload_duration, force=True)
        return True

    def on_battle_live(self, message):
        """Start the one server-owned countdown after every map is ready."""
        if (self.state != 'running' or self._battle_live or
                self._prebattle_deadline is not None):
            return False
        self._observe_destructibles_disabled(message)
        countdown = max(0.0, _number(
            (message or {}).get('countdown_seconds'),
            self._prebattle_seconds()))
        duration = max(1.0, _number(
            (message or {}).get('battle_duration_seconds'),
            self._battle_seconds()))
        deadline = getattr(self.client, 'combat_deadline', None)
        if deadline is not None:
            countdown = max(0.0, float(deadline) - _monotonic_time())
        network_duration = getattr(self.client, 'combat_duration', None)
        if network_duration is not None:
            duration = max(1.0, float(network_duration))
        self._config['battleDurationSeconds'] = duration
        if not self._worker_mode:
            self._binding.arena_period('prebattle', countdown)
            self._reset_prebattle_native_visuals()
            self._show_prebattle_crosshair()
        self._prebattle_deadline = self._clock() + countdown
        self._last_frame_time = self._clock()
        if (countdown <= 0.0 and
                self._prebattle_transition_ready(self._clock())):
            self._begin_battle()
        return True

    def _reset_prebattle_native_visuals(self):
        """Close stock's pre-attach visual race before countdown rendering.

        All remote entities are ready at the server-owned countdown barrier.
        Native ``Vehicle.startVisual`` has therefore finished and can no
        longer overwrite these gates.  Enemies are forced out of both the
        world and marker/minimap presentation, while friendly minimap entries
        are rebound once to the LAN matrix installed after startVisual.
        """
        if (self._worker_mode or self._remote_factory is None or
                self.client is None):
            return False
        local_team = int(getattr(self.client, 'team', 1))
        changed = False
        for record in self._records.values():
            if (record.get('local') or not record.get('native_remote') or
                    not record.get('ready') or record.get('tombstone')):
                continue
            vehicle = self._remote_factory.get(record['engine_id'])
            if vehicle is None or getattr(vehicle, 'model', None) is None:
                raise RuntimeError(
                    'prebattle native vehicle presentation is unavailable')
            state = record.get('state') or {}
            if int(state.get('team', local_team)) == local_team:
                if not record.get('native_minimap_rebound'):
                    self._binding.refresh_vehicle_minimap(
                        record['engine_id'])
                    record['native_minimap_rebound'] = True
                    changed = True
                continue
            record['spot_visible'] = False
            record['spot_marker_visible'] = False
            record['spot_until'] = 0.0
            record['radio_spot_until'] = 0.0
            record['direct_spot_visible'] = False
            vehicle._spot_visible = False
            vehicle._offlineNativeDrawVisible = False
            set_draw_visibility(vehicle, False)
            vehicle.targetCaps = []
            # Runtime state may say it already stopped the marker while a
            # later stock visual callback registered it again.  At this final
            # ready barrier, stop the real adaptor unconditionally.
            self._binding.stop_vehicle_visual(
                record['engine_id'], False)
            self._store_remote_visual_components(record, False, False)
            vehicle._offlineNativeMarkerVisible = False
            changed = True
        return changed

    def _show_prebattle_crosshair(self):
        """Draw the aiming reticle during our own countdown.

        ``AvatarInputHandler.__onArenaStarted`` only raises
        ``GUN_MARKER_FLAG.CONTROL_ENABLED`` for ``ARENA_PERIOD.BATTLE``, and
        ``VehicleGunRotator.start`` refuses while ``Avatar.isOnArena`` is
        false, so a stock PREBATTLE has no reticle at all.  The player still
        aims during this countdown, so raise both gates now.  Movement and
        firing stay frozen by the runtime's own prebattle gate.
        """
        if self._worker_mode:
            return False
        handler = getattr(self._avatar, 'inputHandler', None)
        rotator = getattr(self._avatar, 'gunRotator', None)
        if handler is None or rotator is None:
            return False
        control = getattr(handler, '_AvatarInputHandler__curCtrl', None)
        set_flag = getattr(control, 'setGunMarkerFlag', None)
        constants_module = getattr(
            self._runtime, 'aih_constants', None)
        flags = getattr(constants_module, 'GUN_MARKER_FLAG', None)
        if (not callable(set_flag) or flags is None or
                not hasattr(flags, 'CONTROL_ENABLED')):
            raise RuntimeError('#1513 gun-marker control gate is unavailable')
        setattr(self._avatar, '_PlayerAvatar__isOnArena', True)
        set_flag(True, flags.CONTROL_ENABLED)
        marker_module = getattr(self._runtime, 'gun_marker_ctrl', None)
        show_client = getattr(handler, 'showGunMarker', None)
        show_server = getattr(handler, 'showGunMarker2', None)
        use_client = getattr(marker_module, 'useClientGunMarker', None)
        use_server = getattr(marker_module, 'useServerGunMarker', None)
        if not all(callable(value) for value in (
                show_client, show_server, use_client, use_server)):
            raise RuntimeError('#1513 gun-marker boundary is unavailable')
        show_server(use_server())
        show_client(use_client())
        rotator.start()
        # Starting the rotator needs isOnArena, and that flag is also what
        # PlayerAvatar.shoot checks first.  Retail's second gate is
        # ``isGunLocked``: shoot returns at it with the 'gun_locked' error and
        # the rotator stops laying.  Raise it directly rather than through
        # ``set_isGunLocked``, whose own handler would also force an SPG out
        # of strategic view back into arcade.
        self._set_gun_locked(True)
        return True

    def _set_gun_locked(self, locked):
        """Freeze or release gun laying and firing without changing camera."""
        if self._worker_mode:
            return False
        avatar = self._avatar
        if avatar is None:
            return False
        rotator = getattr(avatar, 'gunRotator', None)
        lock = getattr(rotator, 'lock', None)
        if not callable(lock):
            raise RuntimeError('#1513 gun lock boundary is unavailable')
        avatar.isGunLocked = bool(locked)
        lock(bool(locked))
        return True

    def _bind_local_arcade_camera(self):
        """Bind the initial arcade camera and every aiming provider."""
        handler = getattr(self._avatar, 'inputHandler', None)
        if handler is None:
            raise RuntimeError('native input handler is unavailable')
        modes = getattr(self._runtime.avatar_input_handler, '_CTRL_MODE', None)
        if modes is None:
            raise RuntimeError('#1513 control-mode constants are unavailable')
        current = getattr(handler, '_AvatarInputHandler__ctrlModeName', None)
        if current != modes.ARCADE:
            raise RuntimeError('initial #1513 control mode is not arcade')
        self._bind_local_control_sources(handler, current)
        control = getattr(handler, '_AvatarInputHandler__curCtrl', None)
        camera = getattr(control, 'camera', None)
        align_camera = getattr(camera, 'setToVehicleDirection', None)
        if not callable(align_camera):
            raise RuntimeError(
                '#1513 arcade camera direction boundary is unavailable')
        # AvatarInputHandler creates ArcadeCamera before the client-only
        # Vehicle exists.  Its initial yaw therefore comes from the identity
        # target matrix.  Rebinding vehicleMProv above preserves that stale
        # yaw; use the stock public reset after the live matrix is attached.
        align_camera()
        rotator = getattr(self._avatar, 'gunRotator', None)
        reset_rotator = getattr(rotator, 'reset', None)
        if not callable(reset_rotator):
            raise RuntimeError(
                '#1513 gun-direction reset boundary is unavailable')
        # Vehicle.getAimParams reads the appearance turret/gun matrices, not
        # the packed server echo.  Those matrices can still contain a loading
        # angle when the first targeting tick runs.  Exact #1513's public
        # VehicleGunRotator.reset() clears both angles and both matrices
        # without restarting its timer, marker lifecycle or sound objects.
        reset_rotator()
        self._echo_local_gun_angles(0.0, 0.0)
        align_sender = getattr(self._sender, 'align_aim', None)
        if not callable(align_sender):
            raise RuntimeError('player LAN aim sender is unavailable')
        align_sender(0.0, 0.0)
        return True

    def _on_control_mode_changed(self, handler, mode):
        """Verify the new control captured its canonical pose."""
        # AvatarInputHandler.onControlModeChanged calls
        # _Targeting.onRecreateDevice, which clears BigWorld.target; the engine
        # then reaches targetBlur and removes the previous edge.
        self._clear_target_outline()
        if self.state != 'running' or self._local_matrix is None:
            return False
        modes = getattr(self._runtime.avatar_input_handler, '_CTRL_MODE', None)
        if modes is None:
            raise RuntimeError('#1513 control-mode constants are unavailable')
        if mode == modes.POSTMORTEM:
            result = self._assert_postmortem_control_sources(handler)
            if self._server is not None:
                self._spectated_engine_id = int(self._server.vehicle_id)
            return result
        result = self._assert_local_control_sources(handler, mode)
        # Strategic view is allowed to draw team-spotted artillery targets
        # outside the ordinary 565 m vehicle AOI.  Re-evaluate the retained
        # spotting memory on the next frame instead of leaving a distant
        # target hidden for the remainder of the current 0.10 s HUD period.
        self._next_spotting_time = 0.0
        return result

    def _spectator_record(self, engine_id, allow_self=False):
        """Resolve one server-valid postmortem vehicle target."""
        try:
            engine_id = int(engine_id)
        except (TypeError, ValueError, OverflowError):
            return None, None
        for record in self._records.values():
            if int(record.get('engine_id', 0) or 0) != engine_id:
                continue
            if (record.get('tombstone') or not record.get('ready') or
                    int((record.get('state') or {}).get('team', 0) or 0) !=
                    int(getattr(self.client, 'team', 0) or 0)):
                return None, None
            entity = self._server_entity(engine_id)
            if entity is None or getattr(entity, 'matrix', None) is None:
                return None, None
            if record.get('local'):
                if not allow_self:
                    return None, None
            elif not self._record_alive(record, entity):
                return None, None
            return record, entity
        return None, None

    def _switch_postmortem_viewpoint(self, is_viewpoint, engine_id):
        """Perform #1513's server reattach and client callback transaction."""
        if self.state != 'running' or is_viewpoint:
            return False
        handler = getattr(self._avatar, 'inputHandler', None)
        modes = getattr(self._runtime.avatar_input_handler, '_CTRL_MODE', None)
        if handler is None or modes is None:
            return False
        if (getattr(handler, '_AvatarInputHandler__ctrlModeName', None) !=
                modes.POSTMORTEM):
            return False
        control = getattr(handler, '_AvatarInputHandler__curCtrl', None)
        if (control is None or
                getattr(control, 'curPostmortemDelay', None) is not None):
            return False
        local_id = (int(self._server.vehicle_id)
                    if self._server is not None else 0)
        record, entity = self._spectator_record(
            engine_id, allow_self=int(engine_id) == local_id)
        if record is None:
            return False

        matrices = getattr(self._avatar, 'consistentMatrices', None)
        attached = getattr(matrices, 'attachedVehicleMatrix', None)
        setter = getattr(matrices, '_ConsistentMatrices__setTarget', None)
        camera = getattr(control, '_PostMortemControlMode__cam', None)
        callback = getattr(self._avatar, 'onSwitchViewpoint', None)
        attach_vehicle = getattr(
            self._runtime.compatibility, 'set_postmortem_vehicle', None)
        if (attached is None or not callable(setter) or camera is None or
                not callable(callback) or not callable(attach_vehicle)):
            return False
        try:
            previous_target = attached.target
            previous_camera = camera.vehicleMProv
        except AttributeError:
            return False

        target_matrix = (self._local_body_pose() if record.get('local')
                         else entity.matrix)
        if target_matrix is None:
            return False
        previous_vehicle_id = None
        try:
            # A retail cell attachment changes Avatar.vehicle first.  The
            # client-created LAN entities have no cell relationship, so copy
            # its exact Python-visible result before invoking the stock client
            # callback: live attached matrix, then postmortem camera provider.
            if not record.get('local'):
                entity._postmortem_visible = True
            # Exact #1513 exposes ``BigWorld.entities`` as ``PyEntities``.
            # It has no membership or iterator slot, so ``id in entities``
            # raises TypeError.  The public lookup is the authoritative
            # identity check and is also what the stock callback uses.
            if self._runtime.bigworld.entity(int(engine_id)) is not entity:
                raise RuntimeError(
                    '#1513 spectator entity lookup was rejected')
            previous_vehicle_id = attach_vehicle(int(engine_id))
            setter(target_matrix, False)
            if attached.target is not target_matrix:
                raise RuntimeError(
                    '#1513 spectator matrix attachment was rejected')
            camera.vehicleMProv = attached
            if camera.vehicleMProv is not attached:
                raise RuntimeError(
                    '#1513 spectator camera attachment was rejected')
            position = self._runtime.math.Vector3(0.0, 0.0, 0.0)
            callback(int(engine_id), position)
        except Exception:
            try:
                if previous_vehicle_id is not None:
                    attach_vehicle(previous_vehicle_id)
                setter(previous_target, False)
                camera.vehicleMProv = previous_camera
            except Exception:
                pass
            if not record.get('local'):
                entity._postmortem_visible = False
            raise
        previous_id = self._spectated_engine_id
        if previous_id is not None and int(previous_id) != int(engine_id):
            previous = self._server_entity(previous_id)
            if previous is not None and bool(getattr(
                    previous, '_offlineLANPresentation', False)):
                previous._postmortem_visible = False
        self._spectated_engine_id = int(engine_id)
        return True

    def _fallback_postmortem_viewpoint(self, excluded_engine_id):
        """Move off a dead/removed observed ally, preferring the nearest."""
        if self._spectated_engine_id != int(excluded_engine_id):
            return False
        origin = self._local_position
        candidates = []
        for record in self._records.values():
            engine_id = int(record.get('engine_id', 0) or 0)
            if not engine_id or engine_id == int(excluded_engine_id):
                continue
            valid, entity = self._spectator_record(engine_id)
            if valid is None:
                continue
            position = _xyz(entity.position)
            distance = ((position[0] - origin[0]) ** 2 +
                        (position[2] - origin[2]) ** 2)
            candidates.append((distance, engine_id))
        candidates.sort()
        for unused_distance, engine_id in candidates:
            if self._switch_postmortem_viewpoint(False, engine_id):
                return True
        local_id = (int(self._server.vehicle_id)
                    if self._server is not None else 0)
        if local_id and local_id != int(excluded_engine_id):
            return self._switch_postmortem_viewpoint(False, local_id)
        self._release_postmortem_visibility()
        return False

    def _release_postmortem_visibility(self):
        engine_id = self._spectated_engine_id
        self._spectated_engine_id = None
        self._runtime.compatibility.clear_postmortem_vehicle()
        if engine_id is None:
            return False
        entity = self._server_entity(engine_id)
        if (entity is None or not bool(getattr(
                entity, '_offlineLANPresentation', False))):
            return False
        entity._postmortem_visible = False
        return True

    def _assert_postmortem_control_sources(self, handler):
        """Verify the exact stock death-camera provider selected at enable.

        ``PostMortemControlMode.enable`` first binds the attached matrix.  If
        postmortem delay is active, its synchronous ``start()`` then moves the
        same camera before the control-mode callback returns.  Exact #1513
        selects the still-registered player ``Vehicle.matrix`` or, after that
        entity has left the registry, the steady calculator output.  Mirror
        that branch and keep every selected provider on the copied live pose.
        """
        matrices = getattr(self._avatar, 'consistentMatrices', None)
        attached = getattr(matrices, 'attachedVehicleMatrix', None)
        if attached is None:
            raise RuntimeError(
                '#1513 attached vehicle matrix provider is unavailable')
        try:
            attached_target = attached.target
        except AttributeError:
            raise RuntimeError(
                '#1513 attached vehicle matrix target is unavailable')
        expected_target = self._local_body_pose()
        if (self._spectated_engine_id is not None and self._server is not None
                and int(self._spectated_engine_id) !=
                int(self._server.vehicle_id)):
            record, vehicle = self._spectator_record(
                self._spectated_engine_id)
            if record is not None:
                expected_target = vehicle.matrix
        if attached_target is not expected_target:
            raise RuntimeError(
                '#1513 postmortem attached provider captured a stale '
                'vehicle pose')
        control = getattr(handler, '_AvatarInputHandler__curCtrl', None)
        camera = getattr(control, '_PostMortemControlMode__cam', None)
        if camera is None:
            raise RuntimeError('#1513 postmortem camera is unavailable')
        delay = getattr(control, 'curPostmortemDelay', None)
        expected_camera = attached
        if delay is not None:
            entity_lookup = getattr(self._runtime.bigworld, 'entity', None)
            if not callable(entity_lookup):
                raise RuntimeError(
                    '#1513 postmortem vehicle lookup is unavailable')
            vehicle = entity_lookup(self._avatar.playerVehicleID)
            if vehicle is not None:
                expected_camera = getattr(vehicle, 'matrix', None)
                if expected_camera is not self._local_body_pose():
                    raise RuntimeError(
                        '#1513 postmortem vehicle captured a stale vehicle '
                        'pose')
            else:
                calculator = getattr(
                    handler, 'steadyVehicleMatrixCalculator', None)
                expected_camera = getattr(calculator, 'outputMProv', None)
                if expected_camera is None:
                    raise RuntimeError(
                        '#1513 postmortem delay matrix provider is unavailable')
                if (getattr(expected_camera, 'rotationSrc', None) is not
                        self._local_steady_rotation() or
                        getattr(expected_camera, 'translationSrc', None) is not
                        self._local_stabilised_pose()):
                    raise RuntimeError(
                        '#1513 postmortem delay captured a stale vehicle pose')
            if expected_camera is None:
                raise RuntimeError(
                    '#1513 postmortem delay matrix provider is unavailable')
        try:
            camera_matrix = camera.vehicleMProv
        except AttributeError:
            raise RuntimeError(
                '#1513 postmortem camera has no vehicle matrix provider')
        if camera_matrix is not expected_camera:
            raise RuntimeError(
                '#1513 postmortem camera captured a stale vehicle pose')
        return True

    def _assert_local_control_sources(self, handler, mode):
        """Reject a camera transition that captured a stale native filter."""
        calculator = getattr(
            handler, 'steadyVehicleMatrixCalculator', None)
        if calculator is None:
            raise RuntimeError(
                '#1513 steady vehicle matrix calculator is unavailable')
        output = getattr(
            calculator,
            '_SteadyVehicleMatrixCalculator__outputMProv', None)
        stabilised = getattr(
            calculator,
            '_SteadyVehicleMatrixCalculator__stabilisedMProv', None)
        if output is None or stabilised is None:
            raise RuntimeError(
                '#1513 steady vehicle matrix providers are unavailable')
        if (output.rotationSrc is not self._local_steady_rotation() or
                output.translationSrc is not self._local_stabilised_pose() or
                stabilised.target is not self._local_stabilised_pose()):
            raise RuntimeError(
                '#1513 control mode captured a stale vehicle pose')
        modes = getattr(self._runtime.avatar_input_handler, '_CTRL_MODE', None)
        if modes is None:
            raise RuntimeError('#1513 control-mode constants are unavailable')
        if mode != modes.ARCADE:
            return True
        control = getattr(handler, '_AvatarInputHandler__curCtrl', None)
        camera = getattr(control, 'camera', None)
        if camera is None:
            raise RuntimeError('native current camera is unavailable')
        try:
            camera_matrix = camera.vehicleMProv
        except AttributeError:
            raise RuntimeError(
                'initial #1513 camera has no vehicle matrix provider')
        if camera_matrix is not self._local_body_pose():
            raise RuntimeError(
                '#1513 arcade camera captured a stale vehicle pose')
        return True

    def _bind_local_control_sources(self, handler, mode):
        """Make arcade/sniper aiming consume the copied live vehicle pose.

        Exact #1513 calls ``SteadyVehicleMatrixCalculator.relinkSources`` at
        the beginning of every control-mode change.  That method reads the
        retail ``WGVehicleFilter.stabilisedMatrix`` and
        ``groundPlacingMatrixFiltered``; a client-only Vehicle never receives
        the server samples that would move those providers beyond spawn.
        The compatibility layer replaces the native relink boundary before a
        stock transition enables its new control. This method establishes the
        initial provider graph and the post-transition listener only verifies
        that the same graph survived.
        """
        if self._local_matrix is None:
            raise RuntimeError('player control bind requires a live pose')
        calculator = getattr(
            handler, 'steadyVehicleMatrixCalculator', None)
        if calculator is None:
            raise RuntimeError(
                '#1513 steady vehicle matrix calculator is unavailable')
        output = getattr(
            calculator,
            '_SteadyVehicleMatrixCalculator__outputMProv', None)
        stabilised = getattr(
            calculator,
            '_SteadyVehicleMatrixCalculator__stabilisedMProv', None)
        if output is None or stabilised is None:
            raise RuntimeError(
                '#1513 steady vehicle matrix providers are unavailable')
        steady_rotation = self._local_steady_rotation()
        stabilised_pose = self._local_stabilised_pose()
        output.rotationSrc = steady_rotation
        output.translationSrc = stabilised_pose
        stabilised.target = stabilised_pose
        if (output.rotationSrc is not steady_rotation or
                output.translationSrc is not stabilised_pose or
                stabilised.target is not stabilised_pose):
            raise RuntimeError(
                '#1513 steady vehicle matrix providers rejected live pose')

        modes = getattr(self._runtime.avatar_input_handler, '_CTRL_MODE', None)
        if modes is None:
            raise RuntimeError('#1513 control-mode constants are unavailable')
        if mode != modes.ARCADE:
            # Sniper aiming consumes the steady calculator above. Other stock
            # modes own different cameras and do not expose ArcadeCamera's
            # writable vehicleMProv property.
            return True
        control = getattr(handler, '_AvatarInputHandler__curCtrl', None)
        if control is None:
            raise RuntimeError('native current control mode is unavailable')
        camera = getattr(control, 'camera', None)
        if camera is None:
            raise RuntimeError('native current camera is unavailable')
        try:
            previous = camera.vehicleMProv
        except AttributeError:
            raise RuntimeError(
                'initial #1513 camera has no vehicle matrix provider')
        body_pose = self._local_body_pose()
        camera.vehicleMProv = body_pose
        if camera.vehicleMProv is not body_pose:
            # The exact #1513 getter unwraps the translation-only provider and
            # returns its source.  An identity mismatch therefore means the
            # native setter did not accept the copied live matrix.
            camera.vehicleMProv = previous
            raise RuntimeError(
                'native arcade camera rejected the player pose provider')
        return True

    def local_health(self):
        if self._server is None:
            return None
        entity = self._server_entity(self._server.vehicle_id)
        if entity is None:
            return None
        try:
            return max(0, int(entity.health))
        except (TypeError, ValueError, AttributeError):
            return None

    def _simulated_rpm_and_gear(self):
        """Copy #1513's three-gear simulated engine law for local physics."""
        descriptor = self._local_descriptor
        if descriptor is None:
            raise RuntimeError('player descriptor is unavailable for RPM')
        physics = _field(descriptor, 'physics', {})
        limits = tuple(_field(physics, 'speedLimits', ()) or ())
        if len(limits) < 2:
            raise RuntimeError('#1513 vehicle speed limits are unavailable')
        speed_range = (abs(float(limits[0])) + abs(float(limits[1]))) / 3.0
        if speed_range <= 0.0:
            raise RuntimeError('#1513 vehicle speed range is invalid')
        speed = abs(float(self._local_speed))
        if speed < 0.05:
            return 0.0, 0
        gear = math.ceil(
            math.floor(speed * 50.0) / 50.0 / speed_range)
        gear = max(1.0, gear)
        rpm = abs(1.0 + (speed - gear * speed_range) / speed_range)
        # The exact UINT64 field reserves 0.0..1.2 for normal and excess RPM.
        # _RpmStateHandler owns the later 0.3 display shift; DetailedEngineState
        # consumes this unshifted value directly for the player's engine sound.
        return max(0.0, min(1.2, rpm)), min(255, int(gear))

    def _publish_rpm(self, now, force=False):
        if not force and now < self._next_rpm_time:
            return False
        value, gear = self._simulated_rpm_and_gear()
        self._next_rpm_time = now + RPM_PRESENTATION_SECONDS
        left_scroll = 0.0
        right_scroll = 0.0
        if self._local_physics is not None:
            left_scroll, right_scroll = vehicle_physics.track_scroll(
                self._local_physics, self._local_speed,
                self._local_turn_speed)
            minimum, maximum = TRACK_SCROLL_LIMITS
            left_scroll = max(minimum, min(maximum, left_scroll))
            right_scroll = max(minimum, min(maximum, right_scroll))
        payload = (
            self._local_yaw, self._local_pitch, self._local_roll,
            left_scroll, right_scroll, value, gear)
        if not force and payload == self._last_presented_rpm:
            return False
        publish = getattr(self._binding, 'avatar_aux_physics', None)
        if not callable(publish):
            raise RuntimeError(
                '#1513 own-vehicle auxiliary physics boundary is unavailable')
        publish(*payload)
        self._last_presented_rpm = payload
        return True

    def local_damage_report(self):
        return self._local_damage_report

    def acknowledge_local_damage_report(self, base_revision, ack_seq,
                                        server_revision):
        """Retire only a checkpoint canonically acknowledged by the server."""
        base_revision = max(0, int(base_revision))
        ack_seq = max(0, int(ack_seq))
        server_revision = max(0, int(server_revision))
        if server_revision < self._local_critical_server_revision:
            return False
        self._local_critical_server_revision = server_revision
        if base_revision != self._local_critical_base_revision:
            self._local_critical_base_revision = base_revision
            self._local_critical_next_seq = 0
            self._local_critical_owned = False
            self._local_damage_report = None
            return False
        report = self._local_damage_report
        if (report is not None and
                ack_seq >= int(report.get('critical_seq', 0))):
            repair_complete = bool(report.get('tracks')) and all(
                row.get('state') == 'critical'
                for row in report.get('tracks') or ())
            self._local_damage_report = None
            self._local_critical_owned = not repair_complete
            return True
        return False

    def _queue_local_track_repair(self, critical):
        """Queue only monotonic repair facts for a canonically damaged track."""
        if (not isinstance(critical, dict) or
                self._local_critical_base_revision <= 0):
            return None
        repaired = set()
        for event in critical.get('events') or ():
            if (isinstance(event, dict) and
                    event.get('kind') == 'device' and
                    event.get('name') in (
                        'leftTrackHealth', 'rightTrackHealth') and
                    event.get('old_state') == 'destroyed' and
                    event.get('state') == 'critical' and
                    event.get('cause') == 'repair'):
                repaired.add(event.get('name'))
        destroyed = set(critical.get('destroyed') or ())
        rows = []
        for raw in critical.get('devices') or ():
            if not isinstance(raw, dict):
                continue
            name = raw.get('name')
            if (name not in ('leftTrackHealth', 'rightTrackHealth') or
                    (name not in destroyed and name not in repaired)):
                continue
            state = raw.get('state')
            if state not in ('destroyed', 'critical'):
                continue
            try:
                hp = max(0.0, float(raw.get('hp')))
                maximum = max(1.0, float(raw.get('max_hp')))
            except (TypeError, ValueError, OverflowError):
                continue
            if state == 'destroyed' and hp <= 0.001:
                continue
            rows.append({
                'name': name,
                'hp': round(min(hp, maximum), 3),
                'max_hp': round(maximum, 3),
                'state': state,
            })
        rows.sort(key=lambda row: row['name'])
        if not rows:
            return None
        report = dict(self._local_damage_report or {})
        if rows != report.get('tracks'):
            self._local_critical_next_seq += 1
        report = {
            'tracks': rows,
            'critical_base_revision': self._local_critical_base_revision,
            'critical_seq': self._local_critical_next_seq,
        }
        self._local_damage_report = report
        self._local_critical_owned = True
        return report

    def _queue_local_damage_report(self, critical=None, reason=None,
                                   display_health=None,
                                   attribute_attacker=True):
        # These hazard callbacks may still drive immediate native
        # presentation, but a visible #1513 process never opens a canonical
        # damage lineage.  Do not discard a separately validated track-repair
        # checkpoint while an unrelated local presentation callback runs.
        if not (isinstance(self._local_damage_report, dict) and
                self._local_damage_report.get('tracks')):
            self._local_damage_report = None
            self._local_critical_owned = False
        return None

    def _resolve_descriptor(self, vehicle_name):
        """Return one shared descriptor per vehicle type for this round.

        The factory pins every descriptor it prepares, and nothing in this port
        writes to one, so building a second copy per bot only doubled the
        retained descriptors and their native BSP testers.
        """
        cached = self._descriptor_cache.get(vehicle_name)
        if cached is not None:
            return cached
        failure = None
        for candidate in self._descriptor_candidates(vehicle_name):
            try:
                prepared = self._prepare_vehicle_descriptor(candidate)
            except Exception as error:
                failure = error
                self._report_unusable_vehicle(candidate, error)
                continue
            self._descriptor_cache[vehicle_name] = prepared
            if candidate not in self._prepared_vehicle_names:
                self._prepared_vehicle_names.append(candidate)
            return prepared
        raise RuntimeError(
            '#1513 vehicle %r has no loadable substitute: %s' %
            (vehicle_name, failure))

    def _resolve_player_descriptor(self, state):
        """Prepare the exact mounted human descriptor donated at join time."""
        encoded = lan_protocol._canonical_vehicle_compact_descr(
            state.get('vehicle_compact_descr'))
        if encoded is None:
            raise RuntimeError(
                'player mounted vehicle descriptor is unavailable')
        key = ('player', encoded)
        cached = self._descriptor_cache.get(key)
        if cached is not None:
            return cached
        try:
            raw = base64.b64decode(encoded.encode('ascii'))
            descriptor = self._runtime.vehicles.VehicleDescr(
                compactDescr=raw)
        except Exception as error:
            raise RuntimeError(
                'player mounted vehicle descriptor is unreadable: %s' %
                error)
        expected = str(state.get('vehicle') or '')
        actual = str(getattr(descriptor.type, 'name', '') or '')
        if not expected or actual != expected:
            raise RuntimeError(
                'player mounted vehicle descriptor type mismatch')
        if self._remote_factory is None:
            raise RuntimeError(
                '#1513 vehicle descriptor geometry owner is unavailable')
        prepared = self._remote_factory.prepare_descriptor(descriptor)
        self._descriptor_cache[key] = prepared
        return prepared

    @staticmethod
    def _player_effective_snapshot(state):
        """Return the exact client-computed parameters for one human tank."""
        snapshot = effective_params.canonical(
            (state or {}).get('effective_params'))
        if snapshot is None:
            raise RuntimeError(
                'player effective vehicle parameters are unavailable')
        return snapshot

    def _prepare_vehicle_descriptor(self, vehicle_name):
        descriptor = self._runtime.vehicles.VehicleDescr(
            typeName=vehicle_name)
        vehicle_configuration.install_top_modules(descriptor)
        compact_descr = descriptor.makeCompactDescr()
        descriptor = self._runtime.vehicles.VehicleDescr(
            compactDescr=compact_descr)
        if str(getattr(descriptor.type, 'name', '') or '') != vehicle_name:
            raise RuntimeError(
                '#1513 top vehicle descriptor type mismatch for %s' %
                vehicle_name)
        if self._remote_factory is None:
            raise RuntimeError(
                '#1513 vehicle descriptor geometry owner is unavailable')
        return self._remote_factory.prepare_descriptor(descriptor)

    def _descriptor_candidates(self, vehicle_name):
        """Yield the requested vehicle, then this round's proven substitutes.

        The baked blacklist keeps unloadable types out of the lineup already.
        This is the safety net for a type it does not cover: the slot keeps a
        tank instead of the round failing.
        """
        offered = []
        for name in ([vehicle_name] + list(self._prepared_vehicle_names) +
                     [self._config.get('vehicle')]):
            if name and name not in offered:
                offered.append(name)
                yield name

    def _report_unusable_vehicle(self, vehicle_name, error):
        if vehicle_name in self._unusable_vehicles_reported:
            return
        self._unusable_vehicles_reported.add(vehicle_name)
        sys.stdout.write(
            '[Offline LAN 0.9.22] vehicle %s cannot be loaded, substituting: '
            '%s\n' % (vehicle_name, error))

    def _select_bot_vehicle(self, raw):
        requested = raw.get('vehicle')
        if requested:
            return requested
        return self._bot_vehicle_assignments.get(
            (int(raw.get('team', 1)), int(raw.get('slot', 0))),
            self._config['vehicle'])

    @staticmethod
    def _vehicle_excluded(entry):
        name = _field(entry, 'name')
        if vehicle_blacklist.is_unusable(name):
            return True
        return not vehicle_configuration.is_standard_battle_vehicle(entry)

    @staticmethod
    def _vehicle_class_order(entry):
        tags = _field(entry, 'tags', ()) or ()
        for tag, order in (('heavyTank', 0), ('mediumTank', 1),
                           ('AT-SPG', 2), ('lightTank', 3), ('SPG', 4)):
            if tag in tags:
                return order
        return 1

    @staticmethod
    def _vehicle_profile(entry):
        """Convert a #1513 vehicle item or descriptor type to AI data."""
        return {
            'name': str(_field(entry, 'name', '')),
            'level': int(_field(entry, 'level', 1) or 1),
            'tags': _field(entry, 'tags', ()) or (),
        }

    def _prepare_bot_vehicle_assignments(self, player_descriptor):
        """Build the mature mirrored 0.8.2 line-up afresh per battle.

        There is deliberately no process-wide vehicle pool.  The selected
        battle tiers and role template are shared by both teams, humans remove
        their matching slots, and bots fill the remainder from the complete
        eligible #1513 vehicle catalog.  Every process derives the same local
        random stream from the server roster; otherwise the hidden worker and
        visible client pre-load different tanks before the canonical manifest
        arrives and the real line-up is loaded again during the countdown.
        """
        try:
            planning_descriptor = player_descriptor
            server_players = []
            for raw in self._start_message.get('players') or ():
                if not isinstance(raw, dict):
                    continue
                try:
                    player_id = int(raw.get('id'))
                    if player_id <= 0:
                        continue
                except (TypeError, ValueError, OverflowError):
                    continue
                server_players.append((player_id, raw))
            server_players.sort(key=lambda value: value[0])
            if server_players:
                # The off-map worker Avatar is only an engine loading carrier.
                # Visible LAN clients must use this same canonical anchor too;
                # anchoring each process to its own selected tank gives every
                # client a different speculative roster in a mixed-tier room.
                anchor_id, anchor = server_players[0]
                vehicle_name = anchor.get('vehicle')
                if vehicle_name and not (
                        not self._worker_mode and
                        anchor_id == getattr(
                            self.client, 'player_id', None)):
                    planning_descriptor = self._resolve_descriptor(
                        vehicle_name)
            player_profile = self._vehicle_profile(
                planning_descriptor.type)
            tier = int(player_profile['level'])
            tier_mode = bot_planner.normalize_bot_tier_mode(
                self._start_message.get('bot_tier_mode'))
            all_candidates = []
            for nation in self._runtime.nations.AVAILABLE_NAMES:
                nation_id = self._runtime.nations.INDICES[nation]
                values = self._runtime.vehicles.g_list.getList(nation_id)
                iterator = getattr(values, 'itervalues', None)
                entries = iterator() if callable(iterator) else values.values()
                for entry in entries:
                    if not self._vehicle_excluded(entry):
                        all_candidates.append(self._vehicle_profile(entry))
            candidates = [
                candidate for candidate in all_candidates
                if bot_planner.vehicle_in_bot_tier_mode(
                    tier, candidate['level'], tier_mode)
            ]
            if not candidates:
                return False
            candidates.sort(key=lambda value: (
                int(value.get('level', 0)),
                self._vehicle_class_order(value),
                str(value.get('name', ''))))

            seed_players = ';'.join(
                '%d,%s,%s,%s' % (
                    player_id, raw.get('team', ''), raw.get('slot', ''),
                    raw.get('vehicle', ''))
                for player_id, raw in server_players)
            seed_bots = ';'.join(
                '%s,%s,%s' % (
                    raw.get('id', ''), raw.get('team', ''),
                    raw.get('slot', ''))
                for raw in sorted(
                    (value for value in
                     (self._start_message.get('bots') or ())
                     if isinstance(value, dict)),
                    key=lambda value: (
                        int(value.get('team', 0)),
                        int(value.get('slot', 0)),
                        int(value.get('id', 0)))))
            lineup_random = random.Random(bot_planner.stable_seed(
                'battle-lineup-v1', self._start_message.get('round_id'),
                self._start_message.get('map'), seed_players, seed_bots))

            roster = self._start_message.get('bots') or ()
            bots_by_team = dict((team, sorted(
                (raw for raw in roster if isinstance(raw, dict) and
                 int(raw.get('team', 0)) == team),
                key=lambda raw: int(raw.get('slot', 0))))
                for team in (1, 2))
            humans_by_team = {1: [], 2: []}
            for raw in self._start_message.get('players') or ():
                if not isinstance(raw, dict):
                    continue
                if self._worker_mode:
                    try:
                        if int(raw.get('id')) <= 0:
                            continue
                    except (TypeError, ValueError, OverflowError):
                        continue
                team = int(raw.get('team', 0) or 0)
                if team not in humans_by_team:
                    continue
                try:
                    if (not self._worker_mode and raw.get('id') == getattr(
                            self.client, 'player_id', None)):
                        descriptor = player_descriptor
                    else:
                        descriptor = self._resolve_descriptor(
                            raw.get('vehicle'))
                    humans_by_team[team].append(
                        self._vehicle_profile(descriptor.type))
                except Exception:
                    pass
            if not humans_by_team[1] and not humans_by_team[2]:
                team = int(getattr(self.client, 'team', 1) or 1)
                humans_by_team[1 if team != 2 else 2].append(player_profile)

            available_tiers = sorted(set(
                int(candidate['level']) for candidate in candidates))
            match_tiers = list(bot_planner.bot_match_tiers(
                tier, tier_mode, lineup_random.random(),
                lineup_random.random(), available_tiers))
            for profiles in humans_by_team.values():
                for profile in profiles:
                    if profile['level'] not in match_tiers:
                        match_tiers.append(profile['level'])
                    if not any(
                            candidate['level'] == profile['level'] and
                            bot_planner.vehicle_match_class(candidate) ==
                            bot_planner.vehicle_match_class(profile)
                            for candidate in candidates):
                        candidates.append(profile)
            match_tiers = tuple(sorted(set(match_tiers)))
            team_size = max(
                len(humans_by_team[team]) + len(bots_by_team[team])
                for team in (1, 2))
            requirements = bot_planner.shared_human_requirements(
                humans_by_team)
            template = bot_planner.build_match_template(
                candidates, team_size, player_profile, match_tiers,
                lineup_random, requirements)

            assignments = {}
            for team in (1, 2):
                team_bots = bots_by_team[team]
                picked = bot_planner.remaining_match_template(
                    template, humans_by_team[team])
                if len(picked) < len(team_bots):
                    picked = bot_planner.select_bot_lineup(
                        picked or candidates, len(team_bots), 1, candidates)
                picked = list(picked[:len(team_bots)])
                lineup_random.shuffle(picked)
                picked.sort(key=self._vehicle_class_order)
                for raw, entry in zip(team_bots, picked):
                    assignments[(team, int(raw.get('slot', 0)))] = \
                        entry['name']
            allowed_names = set(
                candidate['name'] for candidate in all_candidates)
            for raw in self._start_message.get('bot_lineup') or ():
                if not isinstance(raw, dict):
                    self._bot_vehicle_assignments = {}
                    return False
                try:
                    team = int(raw.get('team'))
                    slot = int(raw.get('slot'))
                except (TypeError, ValueError, OverflowError):
                    self._bot_vehicle_assignments = {}
                    return False
                vehicle = raw.get('vehicle')
                if (team not in (1, 2) or not 0 <= slot < 15 or
                        vehicle not in allowed_names):
                    self._bot_vehicle_assignments = {}
                    return False
                if (team, slot) in assignments:
                    assignments[(team, slot)] = vehicle
            self._bot_vehicle_assignments = assignments
            return True
        except Exception:
            # The local tank remains a valid descriptor fallback. The complete
            # roster table itself is a #1513 retail API and is ABI-audited.
            self._bot_vehicle_assignments = {}
            return False

    def _formation_pose(self, team, slot):
        key = (int(team), int(slot))
        cached = self._spawn_cache.get(key)
        if cached is not None:
            return cached
        if self._spawn_planner is None:
            self._spawn_planner = SpawnPlanner(
                self._arena_type,
                tactical_maps.get_tactical_map(self._config['map']),
                self._navigation_graph)
        result = self._spawn_planner.pose(key[0], key[1])
        self._spawn_cache[key] = result
        return result

    def _state_world_pose(self, state):
        if bool(state.get('world_pose', False)):
            position = (_number(state.get('x')), _number(state.get('y')),
                        _number(state.get('z')))
            yaw = _number(state.get('yaw'))
        else:
            return self._formation_pose(
                int(state.get('team', 1)), int(state.get('slot', 0)))
        ground = self._ground_y(
            position[0], position[2], position[1],
            allow_wide=self._navigation_graph is None)
        if ground is not None:
            position = (position[0], ground, position[2])
        return position, yaw

    def _vector(self, position):
        return self._runtime.math.Vector3(
            float(position[0]), float(position[1]), float(position[2]))

    def _ground_filter(self, x, z):
        """Build the #1513 fifth-argument filter for this ground column.

        Retail keeps a breakable destructible out of the vehicle's collision,
        so a crushed fence must not carry the suspension or the drive slope
        while its model waits for the hiding callback.
        """
        probe = getattr(
            self._destructibles, 'ground_collision_filter', None)
        if not callable(probe):
            return None
        ground_filter = probe(x, z)
        return ground_filter if callable(ground_filter) else None

    def _collide_down(self, start, end, ground_filter):
        """Vertical probe that skips the skin of an already broken item."""
        if ground_filter is None:
            return self._runtime.bigworld.wg_collideSegment(
                self._avatar.spaceID, start, end, 128)
        return self._runtime.bigworld.wg_collideSegment(
            self._avatar.spaceID, start, end, 128, ground_filter)

    def _ground_y(self, x, z, hint=0.0, allow_wide=False):
        """Use the 0.8.2 near-hull probe so roofs do not become terrain."""
        ground_filter = self._ground_filter(x, z)
        try:
            hit = self._collide_down(
                self._vector((x, hint + 8.0, z)),
                self._vector((x, hint - 30.0, z)), ground_filter)
            if hit is not None:
                value = float(hit[0].y)
                if -14.0 < value - float(hint) < 6.0:
                    return value
        except Exception:
            pass
        if not allow_wide:
            return None
        from_y = max(1000.0, hint + 50.0)
        height = None
        for unused_layer in range(4):
            hit = self._collide_down(
                self._vector((x, from_y, z)),
                self._vector((x, -1000.0, z)), ground_filter)
            if hit is None:
                return None
            height = float(hit[0].y)
            below = self._collide_down(
                self._vector((x, height - 0.4, z)),
                self._vector((x, -1000.0, z)), ground_filter)
            if below is None or height - float(below[0].y) < 2.5:
                return height
            from_y = height - 0.4
        return height

    def _navigation_ground(self, x, z, hint_y=0.0):
        """Copy the 0.8.2 same-layer graph probe, including ford depth."""
        probe_top = float(hint_y) + 8.0
        probe_bottom = float(hint_y) - 18.0
        ground_filter = self._ground_filter(x, z)
        for unused_layer in range(3):
            try:
                hit = self._collide_down(
                    self._vector((x, probe_top, z)),
                    self._vector((x, probe_bottom, z)), ground_filter)
            except Exception:
                return None
            if hit is None:
                return None
            height = float(hit[0].y)
            if height <= float(hint_y) + 4.5:
                if self._water_depth((x, height, z)) > \
                        BOT_WATER_AVOID_DEPTH:
                    return None
                return height
            probe_top = height - 0.35
        return None

    def _baked_pose_safe(self, position):
        """Apply the validated map's fatal-hazard mask to prediction only."""
        return prebaked_navigation.pose_is_safe(
            self._navigation_graph, position, shoulder_cells=0)

    def _arena_pose_violations(self, entity, position, yaw):
        """Return chassis-corner overflow past each official map edge."""
        bounds = self._arena_bounds
        if bounds is None:
            return (0.0, 0.0, 0.0, 0.0)
        half_width, half_length = self._collision_shape(
            entity.typeDescriptor)[:2]
        sine = abs(math.sin(float(yaw)))
        cosine = abs(math.cos(float(yaw)))
        # These are the axis projections of all four chassis OBB corners.
        extent_x = cosine * half_width + sine * half_length
        extent_z = sine * half_width + cosine * half_length
        minimum_x = float(bounds[0]) + extent_x
        minimum_z = float(bounds[1]) + extent_z
        maximum_x = float(bounds[2]) - extent_x
        maximum_z = float(bounds[3]) - extent_z
        x, unused_y, z = _xyz(position)
        return (
            max(0.0, minimum_x - x),
            max(0.0, x - maximum_x),
            max(0.0, minimum_z - z),
            max(0.0, z - maximum_z),
        )

    def _arena_pose_is_outside(self, entity, position, yaw):
        return any(value > 0.00001 for value in
                   self._arena_pose_violations(entity, position, yaw))

    def _arena_motion_is_clear(self, entity, position, travel_yaw,
                               speed, dt, hull_yaw=None):
        """Keep every chassis corner inside, but let stale poses recover."""
        if self._arena_bounds is None:
            return True
        if hull_yaw is None:
            hull_yaw = travel_yaw
        x, y, z = _xyz(position)
        distance = float(speed) * max(0.0, float(dt))
        candidate = (
            x + math.sin(float(travel_yaw)) * distance,
            y,
            z + math.cos(float(travel_yaw)) * distance)
        before = self._arena_pose_violations(
            entity, position, hull_yaw)
        after = self._arena_pose_violations(
            entity, candidate, hull_yaw)
        # A legal pose cannot cross the red line. If an old state is already
        # outside, accept only axes that hold or reduce every edge overflow;
        # this avoids a teleport while still allowing the player to drive in.
        return all(after[index] <= before[index] + 0.00001
                   for index in range(4))

    def _arena_rotation_is_clear(self, entity, position, yaw,
                                 candidate_yaw):
        """Apply the same no-worsening rule when only the chassis turns."""
        if self._arena_bounds is None:
            return True
        before = self._arena_pose_violations(entity, position, yaw)
        after = self._arena_pose_violations(
            entity, position, candidate_yaw)
        return all(after[index] <= before[index] + 0.00001
                   for index in range(4))

    def _direction_probe(self, position, yaw, speed=0.0,
                         descriptor=None, maximum_distance=None):
        """Copy the 0.8.2 dual-height, three-lane hull corridor probe."""
        x, y, z = _xyz(position)
        current_water = self._water_depth((x, y, z))
        wet_escape = current_water > BOT_WATER_AVOID_DEPTH
        far_distance = 20.0 if abs(float(speed or 0.0)) > 5.0 else 15.0
        if maximum_distance is not None:
            try:
                requested_distance = float(maximum_distance)
            except (TypeError, ValueError):
                return {'clear': False, 'collision': True,
                        'water': False, 'slope': 0.0}
            if (requested_distance != requested_distance or
                    abs(requested_distance) == float('inf')):
                return {'clear': False, 'collision': True,
                        'water': False, 'slope': 0.0}
            far_distance = min(
                far_distance, max(0.5, requested_distance))
        near_distance = min(8.0, far_distance)
        previous_y = y
        previous_distance = 0.0
        sine = math.sin(float(yaw))
        cosine = math.cos(float(yaw))
        lateral_x = cosine
        lateral_z = -sine
        # Keep the sign of the steepest sampled grade.  ``longitudinal_step``
        # needs it to distinguish climbing from descending; taking ``abs``
        # here made every clear descent behave like an uphill pull.
        maximum_slope = 0.0
        signed_speed = float(speed or 0.0)
        planned_impact_speed = abs(signed_speed)
        deferred = False
        planning_params = None
        if descriptor is not None:
            try:
                planning_params = vehicle_physics.derive_params(descriptor)
            except (AttributeError, KeyError, TypeError, ValueError):
                raise RuntimeError(
                    'bot destructible planning speed is unavailable')
        for height, distance in (
                (0.7, near_distance), (1.5, far_distance)):
            nx = x + sine * distance
            nz = z + cosine * distance
            run = distance - previous_distance
            probe_up = max(4.5, run * 0.52)
            probe_down = max(5.0, run * 0.45)
            try:
                ground = self._runtime.bigworld.wg_collideSegment(
                    self._avatar.spaceID,
                    self._vector((nx, previous_y + probe_up, nz)),
                    self._vector((nx, previous_y - probe_down, nz)), 128)
            except Exception:
                return {'clear': False, 'collision': True,
                        'water': False, 'slope': 99.0}
            if ground is None:
                return {'clear': False, 'collision': False,
                        'water': False, 'slope': 99.0}
            next_y = float(ground[0].y)
            water_depth = self._water_depth((nx, next_y, nz))
            if (wet_escape and
                    water_depth > current_water +
                    BOT_WATER_ESCAPE_DEEPEN_EPSILON):
                return {'clear': False, 'collision': False,
                        'water': True, 'slope': 0.0}
            if not wet_escape and water_depth > BOT_WATER_AVOID_DEPTH:
                return {'clear': False, 'collision': False,
                        'water': True, 'slope': 0.0}
            delta = next_y - previous_y
            slope = delta / max(0.1, run)
            if abs(slope) > abs(maximum_slope):
                maximum_slope = slope
            if delta > run * 0.48 or delta < -run * 0.38:
                return {'clear': False, 'collision': False,
                        'water': False, 'slope': slope}
            for offset in (-2.2, 0.0, 2.2):
                ray_start = self._vector((
                    x + lateral_x * offset, y + height,
                    z + lateral_z * offset))
                ray_end = self._vector((
                    nx + lateral_x * offset, next_y + height,
                    nz + lateral_z * offset))
                try:
                    collision = self._runtime.bigworld.wg_collideSegment(
                        self._avatar.spaceID, ray_start, ray_end, 128)
                except Exception:
                    collision = True
                if collision is not None:
                    ray_impact_speed = planned_impact_speed
                    if planning_params is not None:
                        try:
                            hull_bbox = self._destructibles._vehicle_hull_bbox(
                                descriptor)
                            minimum, maximum = hull_bbox[:2]
                            reversing = signed_speed < 0.0
                            hull_reach = max(
                                0.0,
                                (-float(minimum[2]) if reversing else
                                 float(maximum[2])))
                            hit_distance = max(
                                0.0,
                                (collision[0] - ray_start).length - hull_reach)
                            # Use the current copied traction law to estimate
                            # only the speed reachable before this far hit. The
                            # actual hull contact still owns the retail gate.
                            drive_sign = -1.0 if reversing else 1.0
                            acceleration = abs(
                                vehicle_physics.engine_force(
                                    planning_params,
                                    drive_sign * max(
                                        planned_impact_speed, 0.1),
                                    drive_sign, 0.0)) / max(
                                        planning_params['mass'], 1.0)
                            speed_limit = float(planning_params[
                                'speedBwd' if reversing else 'speedFwd'])
                            ray_impact_speed = min(
                                speed_limit,
                                math.sqrt(planned_impact_speed ** 2 +
                                          2.0 * acceleration * hit_distance))
                        except (AttributeError, KeyError, TypeError,
                                ValueError, ZeroDivisionError, RuntimeError):
                            return {'clear': False, 'collision': True,
                                    'water': False, 'slope': slope}
                    if descriptor is not None and self._destructibles is not None:
                        kinetic_speed = None
                        if planning_params is not None:
                            kinetic_speed = float(planning_params[
                                'speedBwd' if signed_speed < 0.0 else
                                'speedFwd'])
                        soft_status = (
                            self._destructibles._catalog_soft_static_path(
                                self._avatar.spaceID, ray_start, ray_end,
                                collision, ray_impact_speed, descriptor,
                                recast_budget=
                                self._soft_static_recast_budget,
                                allow_kinetic_first=True,
                                kinetic_speed=kinetic_speed))
                        if soft_status is True:
                            continue
                        if soft_status == 'kinetic':
                            # Planning may approach a contact that this vehicle can
                            # crush at its directional speed cap. The commit-side
                            # native ray and exact hull contact still own destruction.
                            continue
                        if soft_status == 'deferred':
                            # Budget exhaustion is not evidence of a wall. Keep
                            # checking the remaining lanes so any directly
                            # proved backing wall can still win this sample.
                            deferred = True
                            continue
                    return {'clear': False, 'collision': True,
                            'water': False, 'slope': slope}
            previous_y = next_y
            previous_distance = distance
        result = {'clear': True, 'collision': False,
                  'water': False, 'slope': maximum_slope}
        if deferred:
            result['deferred'] = True
        return result

    def _direction_world_receipt(self, position, travel_yaw, signed_speed,
                                 descriptor, maximum_distance=None):
        """Prove the exact flat-ground 3x3 hull corridor without mutation."""
        try:
            planning_params = vehicle_physics.derive_params(descriptor)
            bbox = self._destructibles._vehicle_hull_bbox(descriptor)
            minimum, maximum = bbox[:2]
            half_width = max(
                abs(float(minimum[0])), abs(float(maximum[0]))) - 0.1
            leading = (-float(minimum[2]) if signed_speed < 0.0 else
                       float(maximum[2]))
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            return None
        if half_width <= 0.0 or leading <= 0.0:
            return None
        x, y, z = _xyz(position)
        sine = math.sin(float(travel_yaw))
        cosine = math.cos(float(travel_yaw))
        lateral_x = cosine
        lateral_z = -sine
        # This is a containment proof, not a planning distance.  Fifteen metres
        # covers the hull, the <=3.5 m cache drift and an ordinary rendered
        # frame at the copied speed limit. A local turn may shorten the receipt;
        # once it no longer contains the hull, motion falls back to the
        # authoritative world sweep.
        proof_distance = 15.0
        if maximum_distance is not None:
            try:
                requested_distance = float(maximum_distance)
            except (TypeError, ValueError):
                return False
            if (requested_distance != requested_distance or
                    abs(requested_distance) == float('inf')):
                return False
            proof_distance = min(
                proof_distance, max(0.5, requested_distance))
        planned_impact_speed = abs(float(signed_speed or 0.0))
        cap_speed = None
        if planning_params is not None:
            cap_speed = float(planning_params[
                'speedBwd' if signed_speed < 0.0 else 'speedFwd'])
        for offset in (-half_width, 0.0, half_width):
            sx = x + lateral_x * offset - sine * 0.5
            sz = z + lateral_z * offset - cosine * 0.5
            ex = x + lateral_x * offset + sine * proof_distance
            ez = z + lateral_z * offset + cosine * proof_distance
            for height in (0.6, 1.1, 1.6):
                ray_start = self._vector((sx, y + height, sz))
                ray_end = self._vector((ex, y + height, ez))
                try:
                    collision = self._runtime.bigworld.wg_collideSegment(
                        self._avatar.spaceID, ray_start, ray_end, 128)
                except Exception:
                    return False
                if collision is None:
                    continue
                soft_status = self._destructibles._catalog_soft_static_path(
                    self._avatar.spaceID, ray_start, ray_end, collision,
                    planned_impact_speed, descriptor,
                    recast_budget=self._soft_static_recast_budget,
                    allow_kinetic_first=True, kinetic_speed=cap_speed)
                if soft_status in (True, 'kinetic'):
                    continue
                if soft_status == 'deferred':
                    return 'deferred'
                return False
        return {
            'distance': proof_distance,
            'half_width': half_width,
            'leading': leading,
            'origin': (x, y, z),
            'yaw': float(travel_yaw),
            'direction': (-1 if signed_speed < 0.0 else 1),
        }

    def _navigation_obstacle(self, start, end, half_width):
        """Exact 0.8.2 coarse graph sweep through the #1513 collision API."""
        dx = float(end[0]) - float(start[0])
        dz = float(end[2]) - float(start[2])
        length = math.sqrt(dx * dx + dz * dz)
        if length < 0.1:
            return False
        lateral_x, lateral_z = dz / length, -dx / length
        for offset in (-float(half_width), 0.0, float(half_width)):
            ray_start = self._vector((
                float(start[0]) + lateral_x * offset,
                float(start[1]) + 0.9,
                float(start[2]) + lateral_z * offset))
            ray_end = self._vector((
                float(end[0]) + lateral_x * offset,
                float(end[1]) + 0.9,
                float(end[2]) + lateral_z * offset))
            if self._runtime.bigworld.wg_collideSegment(
                    self._avatar.spaceID, ray_start, ray_end, 128) is not None:
                return True
        return False

    def _water_depth(self, point):
        collide = getattr(self._runtime.bigworld, 'wg_collideWater', None)
        if not callable(collide):
            return -1.0
        try:
            value = collide(
                self._vector((point[0], point[1] + 20.0, point[2])),
                self._vector((point[0], point[1] - 5.0, point[2])), False)
        except Exception:
            return -1.0
        if value is None or value < 0.0:
            return -1.0
        return 20.0 - float(value)

    def _native_drowning_level(self, entity):
        """Read the local #1513 vehicle's assembled water sensor."""
        appearance = getattr(entity, 'appearance', None)
        try:
            if (appearance is None or
                    getattr(appearance, 'waterSensor', None) is None):
                return None
            if bool(appearance.isUnderwater):
                return 2
            if bool(appearance.isInWater):
                return 1
            return 0
        except Exception:
            # The sensor is native and can disappear during a model refresh.
            # The point probe below remains valid while it is being rebuilt.
            return None

    def _barrel_under_water(self, point):
        """Mirror #1513's positive-distance barrel water gate."""
        collide = getattr(self._runtime.bigworld, 'wg_collideWater', None)
        if not callable(collide):
            return True
        try:
            start = self._vector(point)
            end = self._vector((
                float(point[0]), float(point[1]) + 0.1,
                float(point[2])))
            value = collide(start, end, False)
            return value is not None and float(value) > 0.0
        except Exception:
            return True

    @staticmethod
    def _bot_barrel_point(source, descriptor):
        if not isinstance(source, dict):
            return None
        try:
            yaw = float(source.get('yaw', 0.0) or 0.0)
            turret_yaw = (float(source.get('turret_yaw'))
                          if 'turret_yaw' in source else
                          ((float(source.get('aim_yaw', yaw) or yaw) - yaw +
                            math.pi) % (2.0 * math.pi)) - math.pi)
            return shot_geometry.barrel_world_point(
                descriptor,
                (float(source.get('x')), float(source.get('y')),
                 float(source.get('z'))),
                yaw, float(source.get('pitch', 0.0) or 0.0),
                float(source.get('roll', 0.0) or 0.0),
                turret_yaw,
                float(source.get('gun_pitch', 0.0) or 0.0))
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    def _player_barrel_under_water(self, entity):
        rotator = getattr(self._avatar, 'gunRotator', None)
        if rotator is None:
            return True
        try:
            barrel = shot_geometry.barrel_world_point(
                entity.typeDescriptor,
                tuple(float(value) for value in self._local_position),
                float(self._local_yaw), float(self._local_pitch),
                float(self._local_roll), float(rotator.turretYaw),
                float(rotator.gunPitch))
        except (AttributeError, KeyError, TypeError, ValueError,
                OverflowError):
            return True
        return self._barrel_under_water(barrel)

    def _present_drowning_level(self, level, now):
        status_group = getattr(
            self._runtime.constants, 'VEHICLE_MISC_STATUS', None)
        levels = getattr(self._runtime.constants, 'DROWN_WARNING_LEVEL', None)
        callback = getattr(self._avatar, 'updateVehicleMiscStatus', None)
        if (status_group is None or levels is None or
                not callable(callback) or self._server is None):
            return False
        status = status_group.VEHICLE_DROWN_WARNING
        if level == 2:
            warning_level = levels.DANGER
            started = (self._drown_started if self._drown_started is not None
                       else self._server_clock())
            period = (float(started), 10.0)
        elif level == 1:
            warning_level = levels.CAUTION
            period = (0.0, 0.0)
        else:
            warning_level = levels.SAFE
            period = (0.0, 0.0)
        callback(
            self._server.vehicle_id, int(status), int(warning_level), period)
        return True

    def _tick_drowning(self, dt, now):
        """Present native water warnings without deciding vehicle damage."""
        if dt <= 0.0 or self._server is None:
            return False
        record = self._records.get('player:%s' % self.client.player_id)
        if record is None:
            return False
        authoritative = record.get('state') or {}
        if (not bool(authoritative.get('alive', True)) or
                _number(authoritative.get('health', 1.0)) <= 0.0):
            return False
        entity = self._server_entity(record['engine_id'])
        if (entity is None or
                _number(getattr(entity, 'health', 0.0)) <= 0.0):
            return False
        self._drown_check += dt
        if self._drown_check < 0.3:
            return False
        # A slow frame is still elapsed battle time.  Sampling the current
        # native water state at this edge may reduce temporal resolution, but
        # silently dropping everything beyond 0.5 s makes the warning clock
        # permanently late after every hitch.
        elapsed = self._drown_check
        self._drown_check = 0.0
        level = self._native_drowning_level(entity)
        if level is None:
            depth = self._water_depth(self.local_pose()[0])
            level = _drowning_level(
                getattr(entity, 'typeDescriptor', None), depth)
        if level == 2:
            if self._drown_level != 2:
                self._drown_started = self._server_clock()
            self._drown_time += elapsed
        elif level == 1:
            self._drown_time = 0.0
            self._drown_started = None
        else:
            self._drown_time = 0.0
            self._drown_started = None
        self._avatar._offh_drowning = level == 2
        entity._offh_drowning = level == 2
        changed = level != self._drown_level
        if changed:
            self._drown_level = level
            self._present_drowning_level(level, now)
        # The visible process owns only this native UI state. BattleState
        # applies the ten-second law after a hidden-worker observation; no
        # local record, HP or critical state changes here.
        return changed

    def _publish_player_environment(self, dt, now):
        """Send native-world water observations from the hidden worker."""
        if (not self._worker_mode or dt <= 0.0 or
                self.client is None or
                not self.client.is_bot_authority()):
            return False
        self._player_environment_check += float(dt)
        if self._player_environment_check < PLAYER_ENVIRONMENT_SECONDS:
            return False
        # This is a sampling cadence, not permission to erase elapsed time.
        # Keep the fractional remainder so a slow callback does not shift all
        # later observations permanently behind the battle clock.
        self._player_environment_check %= PLAYER_ENVIRONMENT_SECONDS
        observations = []
        for key in sorted(self._records):
            record = self._records[key]
            if (record.get('kind') != 'player' or record.get('local') or
                    int(record.get('network_id', 0) or 0) <= 0):
                continue
            state = record.get('state') or {}
            if (not bool(state.get('alive', True)) or
                    _number(state.get('health', 0.0)) <= 0.0):
                continue
            entity = self._server_entity(record['engine_id'])
            if entity is None:
                continue
            level = self._native_drowning_level(entity)
            if level is None:
                position = _xyz(getattr(
                    entity, 'position', self._record_position(record)))
                level = _drowning_level(
                    getattr(entity, 'typeDescriptor', None),
                    self._water_depth(position))
            try:
                input_seq = max(0, int(state.get('input_seq', 0) or 0))
            except (TypeError, ValueError):
                input_seq = 0
            observation = {
                'player_id': int(record['network_id']),
                'input_seq': input_seq,
                'level': int(level),
            }
            if int(level) == 2:
                try:
                    # The worker has the native descriptor and crew roster.
                    # Publish a proposal for the server to admit only after
                    # its authoritative continuous countdown completes.
                    critical = critical_damage.propose_drowning(entity)
                except Exception:
                    critical = None
                if isinstance(critical, dict):
                    observation['drowning_critical'] = critical
            observations.append(observation)
        sender = getattr(self.client, 'send_player_environment', None)
        if not callable(sender):
            return False
        sequence = self._player_environment_seq + 1
        if not sender(observations, sequence):
            return False
        self._player_environment_seq = sequence
        return True

    def _present_overturn_level(self, level):
        status_group = getattr(
            self._runtime.constants, 'VEHICLE_MISC_STATUS', None)
        levels = getattr(
            self._runtime.constants, 'OVERTURN_WARNING_LEVEL', None)
        callback = getattr(self._avatar, 'updateVehicleMiscStatus', None)
        if (status_group is None or levels is None or
                not hasattr(status_group, 'VEHICLE_IS_OVERTURNED') or
                not callable(callback) or self._server is None):
            return False
        if level == 2:
            warning_level = levels.DANGER
            started = (self._overturn_started
                       if self._overturn_started is not None
                       else self._server_clock())
            period = (float(started), 30.0)
        elif level == 1:
            warning_level = levels.CAUTION
            period = (0.0, 0.0)
        else:
            warning_level = levels.SAFE
            period = (0.0, 0.0)
        callback(
            self._server.vehicle_id,
            int(status_group.VEHICLE_IS_OVERTURNED),
            int(warning_level), period)
        return True

    def _tick_overturn(self, dt, now):
        """Present #1513 overturn warning without deciding canonical death."""
        if dt <= 0.0 or self._server is None:
            return False
        record = self._records.get('player:%s' % self.client.player_id)
        if record is None:
            return False
        authoritative = record.get('state') or {}
        if (not bool(authoritative.get('alive', True)) or
                _number(authoritative.get('health', 1.0)) <= 0.0):
            return False
        entity = self._server_entity(record['engine_id'])
        if entity is None or _number(getattr(entity, 'health', 0.0)) <= 0.0:
            return False
        condition = getattr(
            self._runtime.constants, 'OVERTURN_CONDITION', None)
        ignore_delay = _number(
            getattr(condition, 'IGNOR_DELAY', 0.1), 0.1)
        warning_cosine = _number(
            getattr(condition, 'WARNING_COSINE',
                    math.cos(math.radians(70.0))),
            math.cos(math.radians(70.0)))
        onboard_cosine = _number(
            getattr(condition, 'ONBOARD_COSINE',
                    math.cos(math.radians(80.0))),
            math.cos(math.radians(80.0)))
        up_cosine = self._local_surface_up_cosine
        if up_cosine is None:
            up_cosine = math.cos(float(self._local_pitch)) * math.cos(
                float(self._local_roll))
        level = vehicle_physics.overturn_level_from_up_cosine(
            up_cosine, warning_cosine, onboard_cosine)
        if level == 0:
            self._overturn_check = 0.0
            self._overturn_time = 0.0
            self._overturn_started = None
            entity._offh_overturned = False
            self._avatar._offh_overturned = False
            if self._overturn_level != 0:
                self._overturn_level = 0
                self._present_overturn_level(0)
            return False
        self._overturn_check += float(dt)
        if self._overturn_check + 0.000001 < max(0.0, ignore_delay):
            return False
        if level != self._overturn_level:
            self._overturn_level = level
            self._overturn_time = 0.0
            self._overturn_started = (
                self._server_clock() if level == 2 else None)
            self._present_overturn_level(level)
        entity._offh_overturned = level == 2
        self._avatar._offh_overturned = level == 2
        if level != 2:
            self._overturn_time = 0.0
            self._overturn_started = None
            return False
        self._overturn_time = min(
            30.0, self._overturn_time + float(dt))
        return False

    def _has_los(self, observer, target):
        start = self._vector((observer[0], observer[1] + 2.5,
                              observer[2]))
        for height in (1.5, 2.2):
            end = self._vector((target[0], target[1] + height, target[2]))
            if self._runtime.bigworld.wg_collideSegment(
                    self._avatar.spaceID, start, end, 128) is None:
                return True
        return False

    def _cover_ground(self, x, z, hint_y):
        return self._ground_y(x, z, hint_y)

    def _cover_slope(self, point):
        maximum = 0.0
        for offset_x, offset_z in ((2.5, 0.0), (-2.5, 0.0),
                                   (0.0, 2.5), (0.0, -2.5)):
            height = self._cover_ground(
                point[0] + offset_x, point[2] + offset_z, point[1])
            if height is None:
                return 90.0
            maximum = max(maximum, math.degrees(math.atan2(
                abs(height - point[1]), 2.5)))
        return maximum

    def _sample_bot_cover(self, source, target, route_position,
                          ally_positions, segment_clear):
        """Port the 0.8.2 four-point cover fan through #1513 ray probes."""
        current = _xyz(source)
        target_position = target.get('position') or _xyz(target)
        dx = current[0] - float(target_position[0])
        dz = current[2] - float(target_position[2])
        length = math.sqrt(dx * dx + dz * dz)
        if length < 2.0 or not callable(segment_clear):
            return ()
        away_x, away_z = dx / length, dz / length
        right_x, right_z = away_z, -away_x
        route = _xyz(route_position)
        route_dx, route_dz = route[0] - current[0], route[2] - current[2]
        route_length = math.sqrt(route_dx * route_dx + route_dz * route_dz)
        candidates = []
        for away, lateral in ((0.0, 0.0), (14.0, 0.0),
                              (10.0, 13.0), (10.0, -13.0)):
            x = current[0] + away_x * away + right_x * lateral
            z = current[2] + away_z * away + right_z * lateral
            ground = self._cover_ground(x, z, current[1])
            if ground is None:
                continue
            point = (x, ground, z)
            water_depth = self._water_depth(point)
            if (water_depth > BOT_WATER_AVOID_DEPTH or
                    not segment_clear(current, point)):
                continue
            occluded = not self._has_los(point, target_position)
            if not occluded:
                continue
            slope = self._cover_slope(point)
            if slope > 24.0:
                continue
            peek = None
            for side in (-1.0, 1.0):
                peek_x = point[0] + right_x * side * 6.5 - away_x * 2.0
                peek_z = point[2] + right_z * side * 6.5 - away_z * 2.0
                peek_y = self._cover_ground(peek_x, peek_z, point[1])
                if peek_y is None:
                    continue
                peek_point = (peek_x, peek_y, peek_z)
                if (self._water_depth(peek_point) <=
                        BOT_WATER_AVOID_DEPTH and
                        segment_clear(point, peek_point) and
                        self._has_los(peek_point, target_position)):
                    peek = peek_point
                    break
            move_dx, move_dz = point[0] - current[0], point[2] - current[2]
            move_length = math.sqrt(move_dx * move_dx + move_dz * move_dz)
            alignment = 0.5
            if move_length > 0.1 and route_length > 0.1:
                dot = ((move_dx / move_length) * (route_dx / route_length) +
                       (move_dz / move_length) * (route_dz / route_length))
                alignment = max(0.0, min(1.0, (dot + 1.0) * 0.5))
            nearby = sum(1 for ally in (ally_positions or ())
                         if 0.5 < _distance_2d(point, ally) < 13.0)
            candidate = {
                'id': '%s:%d:%d' % (
                    source.get('id'), int(round(point[0] / 4.0)),
                    int(round(point[2] / 4.0))),
                'position': point,
                'travel_distance': _distance_2d(point, current),
                'route_alignment': alignment,
                'enemy_occlusion': 1.0,
                'exposure': 0.12,
                'slope': slope,
                'water': max(0.0, min(1.0, water_depth)),
                'ally_congestion': max(0.0, min(1.0, nearby / 3.0)),
                'peek_feasible': peek is not None,
                'escape_feasible': True,
            }
            if peek is not None:
                candidate['peek_position'] = peek
            candidates.append(candidate)
        ranked = score_candidates(candidates)
        for candidate in ranked:
            for key in ('breakdown', 'reasons', 'rank', 'score'):
                candidate.pop(key, None)
        return tuple(ranked)

    def local_pose(self):
        # The copied 0.8.2 integrator owns this pose.  #1513's stock camera,
        # gun and collision consumers see the same value through the narrow
        # compatibility overlay installed at the native model boundary.
        return self._local_position, self._local_yaw

    def local_stabilised_position(self):
        """Return the exact origin used by #1513's relative gun mailbox."""
        matrix = self._runtime.math.Matrix(self._local_stabilised_pose())
        return _xyz(matrix.translation)

    def _echo_local_gun_angles(self, turret_yaw=None, gun_pitch=None):
        """Publish #1513's current native rotator angle as the server echo."""
        if self._server is None or self._binding is None:
            raise RuntimeError('player gun-angle echo is not attached')
        rotator = getattr(self._avatar, 'gunRotator', None)
        if rotator is None:
            raise RuntimeError('#1513 gun rotator is unavailable')
        if turret_yaw is None:
            try:
                turret_yaw = float(rotator.turretYaw)
            except (AttributeError, TypeError, ValueError):
                raise RuntimeError(
                    '#1513 turret yaw is unavailable for server echo')
        if gun_pitch is None:
            try:
                gun_pitch = float(rotator.gunPitch)
            except (AttributeError, TypeError, ValueError):
                raise RuntimeError(
                    '#1513 gun pitch is unavailable for server echo')
        hull_yaw = float(self._local_yaw)
        self._binding.update_vehicle_aim(
            self._server.vehicle_id, hull_yaw,
            hull_yaw + float(turret_yaw), float(gun_pitch))
        return True

    def _local_body_pose(self):
        return self._local_pose_matrix or self._local_matrix

    def _local_stabilised_pose(self):
        return self._local_stabilised_snapshot or self._local_body_pose()

    def _local_steady_rotation(self):
        return self._local_steady_rotation_matrix or self._local_body_pose()

    def _matrix_product(self, first, second=None):
        product_type = getattr(self._runtime.math, 'MatrixProduct', None)
        if not callable(product_type):
            raise RuntimeError('#1513 Math.MatrixProduct is unavailable')
        product = product_type()
        product.a = first
        if second is not None:
            product.b = second
        return product

    def _prepare_local_siege_pose(self, entity, native_filter,
                                  native_stabilised):
        """Transplant native hydraulic matrices onto the copied world pose."""
        self._local_pose_matrix = self._local_matrix
        self._local_stabilised_matrix = self._local_matrix
        self._local_steady_rotation_matrix = self._local_matrix
        descriptor = getattr(entity, 'typeDescriptor', None)
        if not bool(getattr(descriptor, 'hasSiegeMode', False)):
            return False
        inverse_type = getattr(self._runtime.math, 'MatrixInverse', None)
        if not callable(inverse_type):
            raise RuntimeError('#1513 Math.MatrixInverse is unavailable')
        native_body = getattr(native_filter, 'bodyMatrix', None)
        native_ground = getattr(native_filter, 'groundPlacingMatrix', None)
        native_ground_filtered = getattr(
            native_filter, 'groundPlacingMatrixFiltered', None)
        if (native_body is None or native_ground is None or
                native_ground_filtered is None or
                native_stabilised is None):
            raise RuntimeError(
                '#1513 hydraulic vehicle matrices are unavailable')

        # BigWorld uses row vectors. Exact #1513 Vehicle.getComponents()
        # relates body and chassis as body * inverse(ground). Strip the stale
        # client-only entity world pose with that same native relation, then
        # apply it to the copied terrain pose. The filtered ground retains
        # its distinct stock camera role.
        inverse_ground = inverse_type(native_ground)

        aim_matrix = self._runtime.math.Matrix()
        aim_matrix.setIdentity()
        self._local_siege_aim_matrix = aim_matrix
        self._local_siege_aim_world_matrix = self._matrix_product(
            aim_matrix, self._local_matrix)
        self._local_siege_aim_pitch = 0.0

        body_relative = self._matrix_product(native_body, inverse_ground)
        self._local_siege_flat_body_matrix = self._matrix_product(
            body_relative, self._local_matrix)

        def transplant(source):
            relative = self._matrix_product(source, inverse_ground)
            return self._matrix_product(
                relative, self._local_siege_aim_world_matrix)

        self._local_siege_body_matrix = transplant(native_body)
        # A fixed-turret #1513 gun derives its marker and current shot ray
        # from ``filter.interpolateStabilisedMatrix()``, while the rendered
        # barrel inherits ``compoundModel.matrix``.  A client-created local
        # vehicle has no cell filter keeping those two native providers in
        # lock-step.  Use the copied hydraulic body for both consumers so the
        # visible barrel, client marker, server-marker echo and fire intent
        # all share the same pose authority.
        self._local_siege_stabilised_matrix = (
            self._local_siege_body_matrix)
        self._local_siege_ground_matrix = transplant(
            native_ground_filtered)
        self._local_pose_matrix = self._matrix_product(self._local_matrix)
        self._local_stabilised_matrix = self._matrix_product(
            self._local_matrix)
        self._local_stabilised_snapshot = self._runtime.math.Matrix(
            self._local_stabilised_matrix)
        self._local_steady_rotation_matrix = self._matrix_product(
            self._local_matrix)
        return True

    def _refresh_local_stabilised_snapshot(self):
        if self._local_stabilised_snapshot is None:
            return False
        self._local_stabilised_snapshot.set(
            self._local_stabilised_matrix)
        return True

    def _select_local_siege_pose(self, entity, enabled):
        # ``Vehicle.onSiegeStateUpdated`` can synchronously replace the
        # active composite descriptor with its ordinary travel descriptor.
        # That child descriptor need not carry ``hasSiegeMode`` even though
        # this local vehicle already owns prepared hydraulic providers.  The
        # final DISABLED edge must still select the plain copied pose; gating
        # it on the *new* descriptor leaves the old hydraulic body attached
        # and makes the fixed gun look vertically locked after exit.
        if (self._local_siege_body_matrix is None or
                self._local_siege_stabilised_matrix is None or
                self._local_siege_ground_matrix is None):
            return False
        body = (self._local_siege_body_matrix
                if enabled else self._local_matrix)
        stabilised = body
        ground = (self._local_siege_ground_matrix
                  if enabled else self._local_matrix)
        self._local_pose_matrix.a = body
        self._local_stabilised_matrix.a = stabilised
        self._local_steady_rotation_matrix.a = ground
        if (self._local_pose_matrix.a is not body or
                self._local_stabilised_matrix.a is not stabilised or
                self._local_steady_rotation_matrix.a is not ground):
            raise RuntimeError('#1513 hydraulic pose selector was rejected')
        self._refresh_local_stabilised_snapshot()
        return True

    def _update_local_hull_aiming(self, entity, elapsed):
        """Pitch the copied Siege pose without entering native physics.

        Alpha 6 called ``WGVehicleFilter.getVehiclePhysics()`` here. That
        native object is not valid for a client-created local vehicle and can
        terminate #1513. The descriptor already exposes the same hydraulic
        limits, so apply the correction to the copied matrix provider instead.
        Gun travel is resolved from the active descriptor; the current native
        gun angle is deliberately not a second feedback authority.
        """
        matrix = self._local_siege_aim_matrix
        descriptor = getattr(entity, 'typeDescriptor', None)
        if matrix is None or not bool(
                getattr(descriptor, 'hasSiegeMode', False)):
            return False
        states = self._runtime.constants.VEHICLE_SIEGE_STATE
        siege_state = getattr(entity, 'siegeState', states.DISABLED)
        desired = 0.0
        speed = 0.0
        active = siege_state == states.ENABLED
        try:
            params = hull_aiming.pitch_params(descriptor)
            if params is None:
                raise ValueError('hydraulic pitch parameters are unavailable')
            speed = params['speed']
            if (not params['isAvailable'] or
                    (active and not params['isEnabled'])):
                raise ValueError('hydraulic pitch is not enabled')
            if active:
                if self._sender is None:
                    raise ValueError('hydraulic aim source is unavailable')
                gun_minimum, gun_maximum = (
                    hull_aiming.absolute_pitch_limits(descriptor))
                aim_point = getattr(self._sender, 'aim_point', None)
                get_shot_angles = getattr(
                    self._runtime, 'get_shot_angles', None)
                if (aim_point is not None and callable(get_shot_angles) and
                        self._local_siege_flat_body_matrix is not None):
                    rotator = getattr(self._avatar, 'gunRotator', None)
                    if rotator is None:
                        raise ValueError('native gun rotator is unavailable')
                    unused_yaw, desired_pitch = get_shot_angles(
                        descriptor,
                        self._runtime.math.Matrix(
                            self._local_siege_flat_body_matrix),
                        (float(rotator.turretYaw),
                         float(rotator.gunPitch)),
                        self._vector(aim_point))
                    desired, unused_reachable = (
                        hull_aiming.minimal_correction(
                            float(desired_pitch), gun_minimum, gun_maximum,
                            params['minimum'], params['maximum']))
                else:
                    desired_pitch = float(getattr(
                        self._sender, 'aim_pitch', self._sender.gun_pitch))
                    desired, unused_reachable = (
                        hull_aiming.world_target_correction(
                            desired_pitch, float(self._local_pitch),
                            gun_minimum, gun_maximum,
                            params['minimum'], params['maximum']))
            self._local_siege_aim_pitch = hull_aiming.slew(
                self._local_siege_aim_pitch, desired, speed, elapsed)
        except (AttributeError, TypeError, ValueError, OverflowError):
            # A malformed or stale descriptor degrades to the flat copied
            # pose. It must not terminate a round or call unsafe native state.
            self._local_siege_aim_pitch = 0.0
            active = False
        matrix.setRotateYPR((0.0, self._local_siege_aim_pitch, 0.0))
        return active

    def _prepare_local_presentation(self, entity):
        """Publish one canonical pose before stock local-vehicle startup."""
        if self._local_matrix is not None:
            raise RuntimeError('player pose was prepared more than once')
        native_attribute = getattr(
            self._runtime.compatibility, 'native_vehicle_attribute', None)
        if not callable(native_attribute):
            raise RuntimeError('native Vehicle matrix boundary is unavailable')
        native_matrix = native_attribute(entity, 'matrix')
        native_filter = native_attribute(entity, 'filter')
        matrix = self._runtime.math.Matrix()
        matrix.setRotateYPR((self._local_yaw, 0.0, 0.0))
        position = self._vector(self._local_position)
        matrix.translation = position
        zero_motion = self._vector((0.0, 0.0, 0.0))
        native_stabilised = getattr(
            native_filter, 'stabilisedMatrix',
            native_matrix)
        self._local_matrix = matrix
        self._local_native_matrix = native_matrix
        self._local_native_stabilised_matrix = native_stabilised
        # The exact #1513 WGVehicleFilter creates its hydraulic providers
        # later in Vehicle.onEnterWorld than PlayerAvatar.vehicle_onEnterWorld.
        # This callback runs from that inner Avatar boundary, so publish the
        # copied base pose now and transplant the hydraulic providers from
        # _attach_local_presentation after the native lifecycle completes.
        self._local_pose_matrix = self._local_matrix
        self._local_stabilised_matrix = self._local_matrix
        self._local_steady_rotation_matrix = self._local_matrix
        self._runtime.compatibility.set_vehicle_pose_overlay(
            entity, position, self._local_yaw, self._local_body_pose(),
            self._local_speed, self._local_turn_speed,
            zero_motion, zero_motion,
            steady_rotation_matrix=self._local_steady_rotation(),
            stabilised_matrix=self._local_stabilised_pose())
        self._local_camera_velocity = zero_motion
        self._local_physics = vehicle_physics.derive_params(
            entity.typeDescriptor,
            self._local_factors(entity.typeDescriptor))
        return True

    def _attach_local_presentation(self):
        """Bind the prepared pose to the exact #1513 model/providers."""
        entity = self._server_entity(self._server.vehicle_id)
        if entity is None:
            raise RuntimeError('player Vehicle is unavailable for presentation')
        if self._local_matrix is None:
            # Engine-free contract tests call this boundary directly. The
            # production path prepares it from vehicle_onEnterWorld before
            # AvatarInputHandler.start() can capture any native provider.
            self._prepare_local_presentation(entity)
        descriptor = getattr(entity, 'typeDescriptor', None)
        if (bool(getattr(descriptor, 'hasSiegeMode', False)) and
                self._local_siege_body_matrix is None):
            native_attribute = getattr(
                self._runtime.compatibility,
                'native_vehicle_attribute', None)
            if not callable(native_attribute):
                raise RuntimeError(
                    'native Vehicle filter boundary is unavailable')
            native_filter = native_attribute(entity, 'filter')
            native_stabilised = getattr(
                native_filter, 'stabilisedMatrix', None)
            self._prepare_local_siege_pose(
                entity, native_filter, native_stabilised)
            self._local_native_stabilised_matrix = native_stabilised
            zero_motion = (self._local_camera_velocity or
                           self._vector((0.0, 0.0, 0.0)))
            self._runtime.compatibility.set_vehicle_pose_overlay(
                entity, self._vector(self._local_position), self._local_yaw,
                self._local_body_pose(), self._local_speed,
                self._local_turn_speed, zero_motion, zero_motion,
                steady_rotation_matrix=self._local_steady_rotation(),
                stabilised_matrix=self._local_stabilised_pose())
        model = getattr(entity, 'model', None)
        if model is None:
            raise RuntimeError('player compound model is unavailable')
        model.matrix = self._local_body_pose()
        self._runtime.compatibility.bind_vehicle_pose_sources(
            self._avatar, entity)
        self._local_model = model
        return True

    def _update_local_presentation(self, entity, dt=0.0):
        if self._local_matrix is None or self._local_model is None:
            raise RuntimeError('player presentation is not attached')
        previous_yaw = float(getattr(
            self._local_matrix, 'yaw', self._local_yaw))
        previous_position = _xyz(self._local_matrix.translation)
        position = self._vector(self._local_position)
        dt = max(0.0, float(dt))
        previous_velocity = _xyz(self._local_camera_velocity)
        if dt > 0.0:
            current_position = _xyz(position)
            velocity_tuple = tuple(
                (current_position[index] - previous_position[index]) / dt
                for index in range(3))
            acceleration_tuple = tuple(
                (velocity_tuple[index] - previous_velocity[index]) / dt
                for index in range(3))
        else:
            velocity_tuple = previous_velocity
            acceleration_tuple = (0.0, 0.0, 0.0)
        velocity = self._vector(velocity_tuple)
        acceleration = self._vector(acceleration_tuple)
        self._local_matrix.setRotateYPR((
            self._local_yaw, self._local_pitch, self._local_roll))
        self._local_matrix.translation = position
        self._update_local_hull_aiming(entity, dt)
        self._refresh_local_stabilised_snapshot()
        # Exact #1513's CompoundAppearance.__linkCompound rebinds
        # ``compoundModel.matrix`` from Vehicle.matrix after every model
        # refresh.  Mutate the persistent provider only; even reading and
        # comparing a native PyCompoundModel provider every render frame can
        # create a fresh Python wrapper and spuriously relink the hierarchy.
        self._runtime.compatibility.set_vehicle_pose_overlay(
            entity, position, self._local_yaw, self._local_body_pose(),
            self._local_speed, self._local_turn_speed,
            velocity, acceleration,
            steady_rotation_matrix=self._local_steady_rotation(),
            stabilised_matrix=self._local_stabilised_pose())
        self._reset_full_turret_sniper_aim(previous_yaw)
        self._local_camera_velocity = velocity
        self._run_optional_feature(
            'local track animation', self._update_local_tracks, (entity,))
        return position

    def _reset_full_turret_sniper_aim(self, previous_yaw):
        """Keep #1513's world aim stable across a copied hull-yaw step."""
        yaw_delta = ((float(self._local_yaw) - float(previous_yaw) + math.pi) %
                     (2.0 * math.pi) - math.pi)
        if abs(yaw_delta) <= 0.000001:
            return False
        handler = getattr(self._avatar, 'inputHandler', None)
        modes = getattr(self._runtime.avatar_input_handler, '_CTRL_MODE', None)
        if (handler is None or modes is None or
                getattr(handler, '_AvatarInputHandler__ctrlModeName', None) !=
                modes.SNIPER):
            return False
        descriptor = self._local_descriptor
        if descriptor is None and self._server is not None:
            entity = self._server_entity(self._server.vehicle_id)
            descriptor = getattr(entity, 'typeDescriptor', None)
        gun = _field(descriptor, 'gun')
        # Exact #1513 SniperControlMode enables horizontal stabilization only
        # for a fully rotating turret. Limited-traverse and turretless guns
        # intentionally remain hull-relative and must keep following the hull.
        if gun is None or _field(gun, 'turretYawLimits') is not None:
            return False
        control = getattr(handler, '_AvatarInputHandler__curCtrl', None)
        camera = getattr(control, 'camera', None)
        aiming_system = getattr(camera, 'aimingSystem', None)
        reset = getattr(aiming_system, 'resetIdealDirection', None)
        if not callable(reset):
            raise RuntimeError(
                '#1513 sniper world-aim reset boundary is unavailable')
        # SniperAimingSystem retains mouse-owned worldYaw/worldPitch. Rebase its
        # ideal turret angle after our out-of-filter hull update so the next
        # native camera tick does not integrate that yaw into the world aim.
        reset()
        return True

    def _local_engine_mode_value(self, alive):
        """Return the exact #1513 ``(power, movementFlags)`` engine mode."""
        forward = _number(getattr(self._sender, 'forward', 0.0))
        # The retail cell contributes limited-traverse autorotation even when
        # A/D is idle.  Drive native track animation from the effective turn
        # consumed by copied physics, not from keyboard state alone.
        turn = _number(self._local_drive_turn)
        flags = 0
        if forward > 0.0:
            flags |= _MOVEMENT_FORWARD
        elif forward < 0.0:
            flags |= _MOVEMENT_BACKWARD
        if turn < 0.0:
            flags |= _MOVEMENT_ROTATE_LEFT
        elif turn > 0.0:
            flags |= _MOVEMENT_ROTATE_RIGHT
        if not alive:
            return (ENGINE_MODE_OFF, 0)
        if flags or abs(self._local_speed) > 0.05:
            return (ENGINE_MODE_RUNNING, flags)
        return (ENGINE_MODE_IDLE, flags)

    def _update_local_tracks(self, entity):
        """Feed the native track, wheel, spline and trace animation.

        Retail drives this from the cell-owned
        ``Avatar.ownVehicleAuxPhysicsData``.  The copied local physics now
        publishes that property for engine sound, while this direct
        ``updateTracksScroll`` path keeps the established track animation
        boundary.  Its native tick pins both belts to zero while
        ``engineMode[0]`` is at most 1.
        """
        appearance = getattr(entity, 'appearance', None)
        update_scroll = getattr(appearance, 'updateTracksScroll', None)
        change_mode = getattr(appearance, 'changeEngineMode', None)
        if not callable(update_scroll) or not callable(change_mode):
            raise RuntimeError('#1513 track animation boundary is unavailable')
        is_alive = getattr(entity, 'isAlive', None)
        alive = bool(is_alive() if callable(is_alive) else is_alive)
        mode = self._local_engine_mode_value(alive)
        if mode != self._local_engine_mode:
            entity.engineMode = mode
            change_mode(mode, True)
            self._local_engine_mode = mode
        if not alive:
            return False
        params = self._local_physics
        if params is None:
            return False
        left, right = vehicle_physics.track_scroll(
            params, self._local_speed, self._local_turn_speed)
        minimum, maximum = TRACK_SCROLL_LIMITS
        update_scroll(
            max(minimum, min(maximum, left)),
            max(minimum, min(maximum, right)))
        return True

    def _detach_local_presentation(self):
        if self._server is None:
            return False
        entity = self._server_entity(self._server.vehicle_id)
        if entity is None:
            return False
        self._sync_fire_effect(entity, False)
        if self._local_model is not None and self._local_native_matrix is not None:
            self._local_model.matrix = self._local_native_matrix
        clear = getattr(
            self._runtime.compatibility, 'clear_vehicle_pose_overlay', None)
        if not callable(clear) or not clear(entity):
            raise RuntimeError('player pose overlay did not clear')
        self._runtime.compatibility.restore_vehicle_pose_sources(
            self._avatar, entity, self._local_native_matrix,
            self._local_native_stabilised_matrix)
        self._local_matrix = None
        self._local_pose_matrix = None
        self._local_stabilised_matrix = None
        self._local_stabilised_snapshot = None
        self._local_steady_rotation_matrix = None
        self._local_siege_body_matrix = None
        self._local_siege_stabilised_matrix = None
        self._local_siege_ground_matrix = None
        self._local_siege_flat_body_matrix = None
        self._local_siege_aim_matrix = None
        self._local_siege_aim_world_matrix = None
        self._local_siege_aim_pitch = 0.0
        self._local_model = None
        self._local_native_matrix = None
        self._local_native_stabilised_matrix = None
        self._local_camera_velocity = None
        self._local_engine_mode = None
        return True

    def _on_client_ready(self):
        self._client_ready_received = True
        self._run_optional_feature(
            'Expert damaged-device presentation',
            self._enable_expert_visibility,
            on_error=self._disable_expert_presentation)
        if self.state == 'running':
            self._sender.send_current()
            if self._ammo_callback_token is None:
                self._ammo_tick()

    def _enable_expert_visibility(self):
        """Enable #1513's native target-device monitor for Expert."""
        if (not self._has_expert or self._expert_visibility_enabled or
                self._worker_mode or self._avatar is None or
                self._server is None):
            return False
        statuses = getattr(
            self._runtime.constants, 'VEHICLE_MISC_STATUS', None)
        status = getattr(
            statuses, 'OTHER_VEHICLE_DAMAGED_DEVICES_VISIBLE', None)
        callback = getattr(self._avatar, 'updateVehicleMiscStatus', None)
        if status is None or not callable(callback):
            raise RuntimeError(
                '#1513 Expert visibility boundary is unavailable')
        # PlayerAvatar reads floatArgs[0] before dispatching the status even
        # though this particular branch only consumes intArg.
        callback(self._server.vehicle_id, int(status), 1, (0.0,))
        self._expert_visibility_enabled = True
        return True

    def _hide_expert_devices(self, vehicle_id):
        provider = getattr(self._avatar, 'guiSessionProvider', None)
        shared = getattr(provider, 'shared', None)
        feedback = getattr(shared, 'feedback', None)
        callback = getattr(feedback, 'hideVehicleDamagedDevices', None)
        if callable(callback):
            callback(int(vehicle_id or 0))
            return True
        return False

    def _disable_expert_presentation(self):
        previous = self._expert_target_id
        self._has_expert = False
        self._expert_visibility_enabled = False
        self._expert_target_id = 0
        self._expert_target_due = 0.0
        self._expert_target_signature = None
        if previous:
            try:
                self._hide_expert_devices(previous)
            except Exception:
                pass
        return True

    def monitor_vehicle_damaged_devices(self, vehicle_id):
        """Own the cell half of #1513's Expert target-monitor mailbox."""
        try:
            vehicle_id = int(vehicle_id)
        except (TypeError, ValueError, OverflowError):
            return False
        previous = self._expert_target_id
        if vehicle_id <= 0:
            self._expert_target_id = 0
            self._expert_target_due = 0.0
            self._expert_target_signature = None
            if previous:
                self._hide_expert_devices(previous)
            return True
        if not self._has_expert or self.state not in (
                'creating_map', 'running'):
            return False
        record = None
        for candidate in self._records.values():
            if int(candidate.get('engine_id', 0) or 0) == vehicle_id:
                record = candidate
                break
        if (record is None or record.get('local') or
                record.get('tombstone')):
            return False
        state = record.get('state') or {}
        if int(state.get('team', 0) or 0) == int(self.client.team):
            return False
        if previous == vehicle_id:
            return True
        if previous:
            self._hide_expert_devices(previous)
        self._expert_target_id = vehicle_id
        self._expert_target_due = (
            self._clock() + EXPERT_DEVICE_DELAY_SECONDS)
        self._expert_target_signature = None
        return True

    @staticmethod
    def _expert_extra_index(descriptor, name):
        selected = None
        extras_dict = getattr(descriptor, 'extrasDict', None)
        if extras_dict is not None:
            selected = extras_dict.get(str(name))
        extras = getattr(descriptor, 'extras', None)
        iterator = (extras.items() if hasattr(extras, 'items') else
                    enumerate(extras or ()))
        for index, extra in iterator:
            if (extra is selected or
                    str(getattr(extra, 'name', '')) == str(name)):
                return int(index)
        return None

    def _tick_expert_target(self, now):
        """Publish canonical module phases after Expert's four-second delay."""
        vehicle_id = int(self._expert_target_id or 0)
        if (not self._has_expert or vehicle_id <= 0 or
                float(now) < self._expert_target_due):
            return False
        record = None
        for candidate in self._records.values():
            if int(candidate.get('engine_id', 0) or 0) == vehicle_id:
                record = candidate
                break
        entity = self._server_entity(vehicle_id)
        if (record is None or record.get('local') or
                record.get('tombstone') or entity is None or
                not self._record_alive(record, entity)):
            self.monitor_vehicle_damaged_devices(0)
            return False
        critical = (record.get('critical_state') or
                    (record.get('state') or {}).get('critical') or {})
        damaged = []
        destroyed = []
        destroyed_names = set(str(name) for name in
                              critical.get('destroyed') or ())
        for device in critical.get('devices') or ():
            if not isinstance(device, dict):
                continue
            name = str(device.get('name', ''))
            phase = str(device.get('state', ''))
            index = self._expert_extra_index(entity.typeDescriptor, name)
            if index is None:
                continue
            if phase == 'destroyed' or name in destroyed_names:
                destroyed.append(index)
            elif phase == 'critical':
                damaged.append(index)
        if bool(critical.get('fire')):
            fire_index = self._expert_extra_index(
                entity.typeDescriptor, 'fire')
            if fire_index is not None:
                damaged.append(fire_index)
        signature = (tuple(sorted(set(damaged))),
                     tuple(sorted(set(destroyed))))
        if signature == self._expert_target_signature:
            return False
        callback = getattr(
            self._avatar, 'showOtherVehicleDamagedDevices', None)
        if not callable(callback):
            raise RuntimeError(
                '#1513 Expert damaged-device callback is unavailable')
        callback(vehicle_id, signature[0], signature[1])
        if self._expert_target_id == vehicle_id:
            self._expert_target_signature = signature
        return True

    @staticmethod
    def _equipment_kind(descriptor):
        """Classify a consumable by its own tags, falling back to its name."""
        tags = getattr(descriptor, 'tags', ()) or ()
        try:
            tags = set(str(tag).lower() for tag in tags)
        except TypeError:
            tags = set()
        name = str(getattr(descriptor, 'name', '') or '').lower()
        for kind in ('repairkit', 'medkit'):
            if kind in tags or kind in name:
                return kind
        if 'extinguisher' in name or any(
                'extinguisher' in tag for tag in tags):
            return 'extinguisher'
        if 'removedrpmlimiter' in name:
            return 'rpm_limiter'
        # Food is a mounted consumable even though it has no activation
        # action.  Publish it to the stock ammo panel while loadout.py owns
        # its passive crew bonus.
        ration_markers = (
            'ration', 'chocolate', 'cola', 'coffee', 'pudding',
            'stimulator', 'buchty', 'onigiri', 'gulaschkanone')
        if any(marker in name for marker in ration_markers):
            return 'passive'
        return None

    def _default_equipments(self):
        """Resolve the consumables the player actually mounted in the garage.

        Immutable effects and mutable charge/cooldown state share one strict
        descriptor projection.  An empty garage slot contributes nothing, so
        a vehicle with no consumables really carries none.
        """
        try:
            values = self._runtime.vehicles.g_cache.equipments().values()
        except Exception:
            return []
        by_name = {}
        by_compact_descr = {}
        for descriptor in values:
            name = str(getattr(descriptor, 'name', '') or '').lower()
            if name:
                by_name[name] = descriptor
            try:
                by_compact_descr[int(descriptor.compactDescr)] = descriptor
            except (TypeError, ValueError, AttributeError):
                continue

        mounted = self._local_mounted_equipments()
        if mounted is not None:
            selected = []
            for compact_descr in mounted:
                descriptor = by_compact_descr.get(compact_descr)
                if descriptor is None:
                    continue
                selected.append(descriptor)
        else:
            # No garage item, for example a test or a direct battle start.
            selected = []
            for name in (
                    'smallrepairkit', 'smallmedkit', 'handextinguishers'):
                descriptor = by_name.get(name)
                if descriptor is not None:
                    selected.append(descriptor)

        result = []
        for descriptor in selected:
            try:
                contract = equipment_mechanics.project_equipment(descriptor)
            except (TypeError, ValueError, IndexError, AttributeError):
                continue
            if contract['id'] <= 0 or contract['compactDescr'] <= 0:
                continue
            result.append(equipment_mechanics.EquipmentState(contract))
        return result

    def _default_bot_equipment_contracts(self):
        """Resolve the fixed bot loadout from this exact #1513 item cache."""
        cache = self._runtime.vehicles.g_cache
        # Engine-free tests expose only the cache surfaces they exercise.
        # The real #1513 cache always owns both lookup methods below.
        if (not callable(getattr(cache, 'equipmentIDs', None)) or
                not callable(getattr(cache, 'equipments', None))):
            return ()
        return tuple(equipment_mechanics.default_bot_consumables(
            cache))

    def _restore_local_equipment_snapshot(self, snapshot, present=False):
        """Restore the visible replica from one complete canonical ledger."""
        if not isinstance(snapshot, dict) or self.client is None:
            return False
        local = next((
            value for value in (snapshot.get('players') or ())
            if isinstance(value, dict) and
            int(value.get('id', 0) or 0) == int(self.client.player_id)), None)
        if local is None or 'equipment_states' not in local:
            return False
        try:
            revision = int(local.get('equipment_revision'))
            if revision < 0 or revision < self._equipment_revision:
                raise ValueError('player equipment revision regressed')
            states = equipment_mechanics.restore_equipment_states(
                local.get('equipment_states'), now=self._clock())
        except (TypeError, ValueError, OverflowError):
            raise RuntimeError('canonical player equipment snapshot is invalid')
        self._equipment_state = states
        self._equipment_revision = revision
        if (present and not self._worker_mode and
                self._avatar is not None and self._server is not None):
            self._present_equipments(self._clock())
        return True

    def _garage_item(self):
        """Return the lobby's current vehicle item, or None outside a garage.

        The mounted consumables and the crew live on the garage item, not on
        the battle descriptor, exactly as in the 0.8.2 law.
        """
        try:
            from CurrentVehicle import g_currentVehicle
        except ImportError:
            return None
        try:
            if not g_currentVehicle.isPresent():
                return None
            return g_currentVehicle.item
        except Exception:
            return None

    def _garage_loadout_snapshot(self):
        """Copy every garage read a battle needs, before the lobby retires.

        ``retire_current_player`` destroys the lobby Account, so
        ``g_currentVehicle`` stops answering once the battle Avatar exists.
        #1513 ``gui_items.Vehicle`` carries ``Shell`` items with ``intCD`` and
        ``count``, and a ``VehicleEquipment`` whose regular consumables read
        back an empty slot as the caller's default.
        """
        if self._garage_loadout is not None:
            return self._garage_loadout
        item = self._garage_item()
        consumables = getattr(
            getattr(item, 'equipment', None), 'regularConsumables', None)
        shells = None if item is None else {}
        if shells is not None:
            for shell in (getattr(item, 'shells', None) or ()):
                try:
                    shells[int(shell.intCD)] = max(0, int(shell.count))
                except (AttributeError, TypeError, ValueError):
                    continue
        equipment_ids = None
        if consumables is not None:
            equipment_ids = []
            for compact_descr in consumables.getIntCDs(0):
                try:
                    equipment_ids.append(int(compact_descr or 0))
                except (TypeError, ValueError):
                    equipment_ids.append(0)
        self._garage_loadout = {
            'shells': shells,
            'equipment_ids': equipment_ids,
            'equipments': (() if consumables is None else
                           tuple(consumables.getInstalledItems())),
            'crew': tuple(getattr(item, 'crew', None) or ()),
            'camouflage_id': self._garage_camouflage_id(item),
            'outfit': self._garage_outfit(item),
            'fitting': self._garage_fitting(item),
        }
        return self._garage_loadout

    def _garage_outfit(self, item):
        """Return the selected vehicle's native outfit for this arena season.

        InventoryRequester has already parsed our OUTFITS record into the
        stock ``gui_items.Vehicle``.  Reading it back through ``getOutfit``
        therefore preserves style expansion, enablement and season choice;
        no battle-side customization binary is assembled here.
        """
        if item is None or self._arena_type is None:
            return ''
        try:
            from items.components.c11n_constants import SeasonType
            season = SeasonType.fromArenaKind(
                self._arena_type.vehicleCamouflageKind)
            outfit = item.getOutfit(season)
            if outfit is None:
                return ''
            compact_descr = getattr(outfit, 'strCompactDescr', None)
            if compact_descr is not None:
                return compact_descr
            maker = getattr(outfit, 'makeCompDescr', None)
            return maker() if callable(maker) else ''
        except Exception as error:
            sys.stdout.write(
                '[Offline LAN 0.9.22] the garage outfit is unavailable: %s\n'
                % error)
            return ''

    def _arena_outfit_season(self):
        if self._arena_type is None:
            return None
        try:
            from items.components.c11n_constants import SeasonType
            return int(SeasonType.fromArenaKind(
                self._arena_type.vehicleCamouflageKind))
        except Exception:
            return None

    def _remote_outfit(self, state, kind):
        """Decode only this remote human's server-published seasonal outfit."""
        if kind != 'player':
            return ''
        season = self._arena_outfit_season()
        outfits = state.get('outfits')
        if season is None or not isinstance(outfits, dict):
            return ''
        encoded = outfits.get(str(season))
        if not encoded:
            return ''
        try:
            raw = base64.b64decode(encoded.encode('ascii'))
            if (not raw or len(raw) > 64 * 1024 or
                    base64.b64encode(raw).decode('ascii') != encoded):
                return ''
            return raw
        except Exception:
            return ''

    @staticmethod
    def _garage_fitting(item):
        """The mounted compact descriptor, or None outside a garage.

        #1513 builds ``gui_items.Vehicle.descriptor`` from the account's own
        ``strCompactDescr``, so this carries the fitted modules, optional
        devices and camouflage the garage panel measures.
        """
        descriptor = getattr(item, 'descriptor', None)
        maker = getattr(descriptor, 'makeCompactDescr', None)
        if not callable(maker):
            return None
        try:
            return maker(), str(descriptor.type.name)
        except Exception:
            return None

    @staticmethod
    def _garage_camouflage_id(item):
        """The paint id ``getClientInvisibility`` passes to the base law."""
        reader = getattr(item, 'getBonusCamo', None)
        if not callable(reader):
            return None
        try:
            camouflage = reader()
        except Exception:
            return None
        return None if camouflage is None else getattr(camouflage, 'id', None)

    def _local_ammo_layout(self):
        """Return the player's mounted ``{shellCompactDescr: count}`` layout.

        ``None`` means there is no garage item.  An explicit empty mapping is
        preserved as an empty ammunition loadout and must never synthesize
        shells the client did not provide.
        """
        shells = self._garage_loadout_snapshot()['shells']
        return None if shells is None else dict(shells)

    def _log_effective_parameters(self, descriptor):
        """Print the values this battle actually uses for the player's tank.

        These are the numbers to compare against the garage panel: a crew or
        equipment bonus the garage shows and the battle ignores shows up here
        as a difference, not as a feeling.
        """
        state = self._gun_state
        profile = self._spotting_profile(descriptor, local=True)
        loadout = self._local_loadout(descriptor)
        snapshot = self._garage_loadout_snapshot()
        # computeBaseInvisibility returns (moving, still), in that order.
        moving, still = self._base_invisibility(
            descriptor, profile, snapshot['camouflage_id'])
        moving_add, moving_mult = profile['invisibility_moving']
        still_add, still_mult = profile['invisibility_still']
        shot_factor = self._shot_invisibility_factor(descriptor)
        physics = vehicle_physics.derive_params(
            descriptor, self._local_factors(descriptor))
        gun_factors = _field(_field(descriptor, 'gun', {}),
                             'shotDispersionFactors', {}) or {}
        chassis_factors = _field(_field(descriptor, 'chassis', {}),
                                 'shotDispersionFactors', (0.0, 0.0)) or (
                                     0.0, 0.0)
        sys.stdout.write(
            '[Offline LAN 0.9.22] PARAMS source=%s view=%.1f '
            'view_still=%.1f binoc=%.3f binoc_delay=%.1fs '
            'conceal_move=%.2f%% conceal_still=%.2f%% at_shot=%.3f '
            'reload=%.2fs aim=%.2fs disp=%.4f disp_move=%.3f '
            'disp_rot=%.3f disp_turret=%.3f disp_shot=%.3f '
            'turret_deg=%.2f gun_deg=%.2f hull_deg=%.2f '
            'terrain=%s power_hp=%.0f speed=%.1f/%.1f repair=%.3f '
            'big_kit=%s radio=%.0f\n' % (
                'client-factors' if loadout['from_client_factors']
                else 'fallback',
                self._vision_radius(descriptor, local=True),
                self._vision_radius(
                    descriptor, local=True,
                    still_seconds=profile['binocular_delay']),
                profile['binocular_factor'],
                profile['binocular_delay'],
                ((moving + moving_add) * moving_mult) * 100.0,
                ((still + still_add) * still_mult) * 100.0, shot_factor,
                state.reload, state.aim_time, state.base_dispersion,
                _number(chassis_factors[0]), _number(chassis_factors[1]),
                _number(_field(gun_factors, 'turretRotation', 0.0)),
                _number(_field(gun_factors, 'afterShot', 0.0)),
                math.degrees(
                    _number(_field(_field(descriptor, 'turret', {}),
                                   'rotationSpeed', 0.0)) *
                    loadout['crew_factor']),
                math.degrees(
                    _number(_field(_field(descriptor, 'gun', {}),
                                   'rotationSpeed', 0.0)) *
                    loadout['gun_rotation_factor']),
                math.degrees(physics['rotSpd']),
                ','.join('%.3f' % value for value in physics['terrainResist']),
                physics['powerW'] / 735.49875,
                physics['speedFwd'] * 3.6, physics['speedBwd'] * 3.6,
                loadout['repair_factor'], loadout['has_big_kit'],
                _number(_field(_field(descriptor, 'radio', {}),
                               'distance', 0.0)) * loadout['radio_factor']))
        sys.stdout.write(
            '[Offline LAN 0.9.22] PARAMS crew=%.1f/%.1f recon=%.1f '
            'camo_crew=%.1f camo_factor=%.4f vision_factor=%.4f '
            'net=%.3f paint=%s rammer=%s vents=%s brothers=%s '
            'rations=%s\n' % (
                loadout['crew_level'], loadout['effective_crew_level'],
                profile['recon_level'], profile['camouflage_level'],
                profile['camouflage_factor'], profile['vision_factor'],
                still_add, snapshot['camouflage_id'],
                loadout['has_rammer'], loadout['has_ventilation'],
                loadout['has_brotherhood'], loadout['has_rations']))
        return True

    def _log_local_ammo(self, state):
        """Print the shell counts this battle starts with, once per round."""
        layout = self._local_ammo_layout()
        counts = []
        loaded = None
        for index, shot in enumerate(state.shots):
            shell = _field(shot, 'shell', {})
            compact_descr = int(_field(shell, 'compactDescr', 0))
            quantity = state.ammo[index] if index < len(state.ammo) else 0
            counts.append('%d:%d' % (compact_descr, quantity))
            if index == state.shot_index:
                loaded = compact_descr
        sys.stdout.write(
            '[Offline LAN 0.9.22] battle ammo garage=%s carried=%s '
            'first_loaded=%s\n' % (
                'unknown' if layout is None else
                ','.join('%d:%d' % (key, layout[key])
                         for key in sorted(layout)),
                ','.join(counts), loaded))

    def _local_mounted_equipments(self):
        """Return the mounted consumable compact descriptors, zeros included.

        An empty slot stays a zero so the battle really carries no consumable
        there, instead of the previous hardcoded three-kit default.
        """
        return self._garage_loadout_snapshot()['equipment_ids']

    def _local_loadout(self, descriptor):
        """Build the passive modifier bundle for the player's own vehicle.

        Optional devices come from the battle descriptor, which #1513 builds
        from the account's mounted compact descriptor.  Consumables and crew
        skills come from the captured garage snapshot.
        """
        if self._local_loadout_cache is not None:
            return self._local_loadout_cache
        snapshot = self._garage_loadout_snapshot()
        crew = snapshot['crew']
        self._local_loadout_cache = loadout_law.modifiers(
            descriptor, snapshot['equipments'],
            loadout_law.crew_skill_names(crew) if crew else None,
            factors=self._local_factors(descriptor))
        return self._local_loadout_cache

    def _equipment_stages(self):
        stages = getattr(self._runtime.constants, 'EQUIPMENT_STAGES', None)
        if stages is None:
            raise RuntimeError('#1513 equipment stages are unavailable')
        return stages

    def _equipment_echo(self, equipment, now):
        """Return the exact ``(quantity, stage, timeRemaining)`` echo.

        ``Avatar.updateVehicleAmmo`` forwards its fourth argument as the
        equipment STAGE, not a clip count, so a consumable published with
        stage 0 (``NOT_RUNNING``) never becomes usable again.
        """
        stages = self._equipment_stages()
        if equipment.uses_left == 0:
            return 0, int(stages.EXHAUSTED), 0
        if (equipment.contract.get('kind') == 'rpm_limiter' and
                equipment.active):
            # #1513's trigger item interprets PREPARING with zero remaining
            # time as the toggled-on state and sends the raw equipment id to
            # deactivate it on the next click.
            return 1, int(stages.PREPARING), 0
        remaining = float(equipment.ready_at) - float(now)
        if remaining > 0.0:
            return 1, int(stages.COOLDOWN), int(math.ceil(remaining))
        return 1, int(stages.READY), 0

    def _present_equipments(self, now=None):
        if self._equipment_state is None:
            return False
        if now is None:
            now = self._clock()
        for equipment in self._equipment_state:
            quantity, stage, remaining = self._equipment_echo(equipment, now)
            self._avatar.updateVehicleAmmo(
                self._server.vehicle_id,
                equipment.contract['compactDescr'],
                quantity, stage, remaining)
        self._equipment_signature = tuple(
            self._equipment_echo(equipment, now)
            for equipment in self._equipment_state)
        return True

    def _tick_equipment_cooldowns(self, now):
        """Republish a consumable the moment its cooldown expires."""
        if not self._equipment_state:
            return False
        signature = tuple(
            self._equipment_echo(equipment, now)
            for equipment in self._equipment_state)
        if signature == self._equipment_signature:
            return False
        self._equipment_signature = signature
        self._present_equipments(now)
        return True

    @staticmethod
    def _critical_name_from_extra_index(descriptor, extra_index):
        extras = getattr(descriptor, 'extras', None)
        if hasattr(extras, 'items'):
            iterator = extras.items()
        else:
            try:
                iterator = enumerate(extras or ())
            except Exception:
                iterator = ()
        for index, extra in iterator:
            try:
                matches = int(index) == int(extra_index)
            except (TypeError, ValueError):
                matches = False
            if not matches:
                continue
            name = str(getattr(extra, 'name', '') or '')
            return name
        return None

    def _activate_equipment(self, activation_code):
        try:
            activation_code = int(activation_code)
        except (TypeError, ValueError):
            return False
        if self._equipment_state is None:
            return False
        equipment_id = activation_code & 65535
        extra_index = max(0, activation_code >> 16)
        equipment = next((value for value in self._equipment_state
                          if value.contract['id'] == equipment_id), None)
        if equipment is None:
            return False
        record = self._records.get('player:%s' % self.client.player_id)
        if record is None or self._server is None:
            return False
        entity = self._server_entity(self._server.vehicle_id)
        if entity is None or entity.typeDescriptor is None:
            return False
        equipment_kind = equipment.contract['kind']
        repair_all = bool(equipment.contract.get('repairAll', False))
        selected = None
        if (not repair_all and
                equipment_kind in ('repairkit', 'medkit')):
            selected = self._critical_name_from_extra_index(
                entity.typeDescriptor, extra_index)
        if (equipment_kind == 'medkit' and selected is not None and
                selected.endswith('Health')):
            selected = selected[:-6]
        sender = getattr(self.client, 'send_equipment_intent', None)
        if not callable(sender):
            return False
        sequence = sender(
            equipment_id,
            activation_code=activation_code,
            selected=None if equipment_kind == 'rpm_limiter' else selected,
            requested_active=(bool(extra_index)
                              if equipment_kind == 'rpm_limiter' else None))
        return sequence is not None

    def _active_engine_power_factor(self):
        return max(0.0, _number(
            equipment_mechanics.passive_effects(
                self._equipment_state or ()).get(
                    'enginePowerFactor'), 1.0))

    def _install_critical_equipment_effects(self, record, entity):
        """Bind exact target-owned consumable factors to a crit proposal."""
        if record is None or entity is None:
            return False
        state = record.get('state') or {}
        if record.get('local') and self._equipment_state is not None:
            equipments = self._equipment_state
        else:
            snapshots = state.get('equipment_states') or ()
            equipments = [
                value.get('equipment') for value in snapshots
                if isinstance(value, dict) and
                isinstance(value.get('equipment'), dict)]
        passives = equipment_mechanics.passive_effects(equipments)
        entity._fire_starting_chance_factor = max(0.0, _number(
            passives.get('fireStartingChanceFactor'), 1.0))
        entity._medkit_bonus_value = max(0.0, _number(
            passives.get('medkitBonusValue'), 0.0))
        return True

    def _tick_rpm_limiter(self, record, entity, dt, now):
        """Removed RPM Limiter damage is advanced only by BattleState."""
        return False

    def _publish_reload_event(self, time_left, base_time, force=False):
        """Send one #1513 reload edge and let the stock HUD interpolate it."""
        if self._server is None:
            return False
        event = (max(0.0, float(time_left)),
                 max(0.0, float(base_time)))
        if not force and self._reload_event == event:
            return False
        self._avatar.updateVehicleGunReloadTime(
            self._server.vehicle_id, event[0], event[1])
        self._reload_event = event
        return True

    def _publish_ammo_state(self, state, force=False):
        """Publish shell counts only when the copied gun state changes."""
        signature = (
            int(state.shot_index), tuple(int(value) for value in state.ammo),
            int(state.clip))
        if not force and signature == self._ammo_signature:
            return False
        current_shell = None
        for index, shot in enumerate(state.shots):
            shell = _field(shot, 'shell', {})
            compact = _field(shell, 'compactDescr', 0)
            quantity = state.ammo[index]
            quantity_in_clip = state.clip if index == state.shot_index else 0
            self._avatar.updateVehicleAmmo(
                self._server.vehicle_id, int(compact),
                max(0, min(quantity, 65535)),
                max(0, min(quantity_in_clip, 255)), 0)
            if index == state.shot_index:
                current_shell = compact
        if current_shell is not None:
            self._avatar.updateVehicleSetting(
                self._server.vehicle_id,
                self._runtime.constants.VEHICLE_SETTING.CURRENT_SHELLS,
                current_shell)
        self._present_equipments()
        self._ammo_signature = signature
        return True

    def _publish_targeting_info(self, entity=None, state=None):
        """Initialise #1513 gun-rotator parameters without ticking ammo."""
        if entity is None:
            if self._server is None:
                raise RuntimeError('player Vehicle server identity is unavailable')
            entity = self._server_entity(self._server.vehicle_id)
        if entity is None or entity.typeDescriptor is None:
            raise RuntimeError('player Vehicle descriptor is unavailable')
        descriptor = entity.typeDescriptor
        turret = descriptor.turret
        gun = descriptor.gun
        if state is None:
            state = self._gun_state
        if state is None:
            raise RuntimeError('player gun state is unavailable')
        chassis_factors = _field(
            _field(descriptor, 'chassis', {}),
            'shotDispersionFactors', (0.0, 0.0))
        gun_factors = _field(gun, 'shotDispersionFactors', {}) or {}
        try:
            movement_factor = float(chassis_factors[0])
            rotation_factor = float(chassis_factors[1])
        except (TypeError, ValueError, IndexError):
            movement_factor = 0.0
            rotation_factor = 0.0
        turret_factor = _number(
            _field(gun_factors, 'turretRotation', 0.0), 0.0)
        base_dispersion = _number(
            _field(gun, 'shotDispersionAngle', 0.0), 0.0)
        if base_dispersion <= 0.0:
            raise RuntimeError('#1513 gun descriptor has no dispersion angle')
        shot_multiplier = (
            state.base_dispersion / base_dispersion *
            critical_damage.stat_factor(entity, 'dispersion'))
        aiming_time = (
            state.aim_time *
            critical_damage.stat_factor(entity, 'aim_time'))
        # updateTargetingInfo takes the FINAL speeds; #1513 multiplies the
        # descriptor value by the gunner factor before sending them, so a
        # trained crew and a mounted ventilation both belong here.
        gunner_factor = max(
            0.0, _number(state.loadout.get('crew_factor'), 1.0))
        gun_factor = max(
            0.0, _number(state.loadout.get('gun_rotation_factor'),
                         gunner_factor))
        turret_speed = (
            _number(turret.rotationSpeed) * gunner_factor *
            critical_damage.stat_factor(entity, 'turret_speed'))
        targeting_signature = (
            turret_speed,
            _number(gun.rotationSpeed) * gun_factor, shot_multiplier,
            turret_factor, movement_factor, rotation_factor, aiming_time)
        if targeting_signature == self._targeting_signature:
            return False
        turret_yaw, gun_pitch = entity.getAimParams()
        self._avatar.updateTargetingInfo(
            turret_yaw, gun_pitch, targeting_signature[0],
            targeting_signature[1], targeting_signature[2],
            targeting_signature[3], targeting_signature[4],
            targeting_signature[5], targeting_signature[6])
        self._targeting_signature = targeting_signature
        return True

    @staticmethod
    def _rescale_current_reload(state, reload_factor):
        """Preserve completed reload progress when its live factor changes."""
        if (state is None or state.reload_time <= 0.0 or
                int(state.clip) > 0):
            return False
        previous_duration = max(0.0, float(state.reload_duration))
        if previous_duration <= 0.0:
            return False
        next_duration = max(
            0.0, float(state.reload) * max(0.0, float(reload_factor)))
        if abs(next_duration - previous_duration) <= 1.0e-9:
            return False
        remaining_fraction = max(
            0.0, min(1.0, float(state.reload_time) / previous_duration))
        state.reload_duration = next_duration
        state.reload_time = next_duration * remaining_fraction
        return True

    def _advance_local_gun_to(self, entity, now=None):
        """Advance the presented gun to one exact wall-clock edge.

        The stock HUD counts down continuously between our 100 ms ammo
        publications.  A trigger must first consume that same elapsed time or
        it can reject a round which the native HUD already presents as ready.
        """
        if entity is None or entity.typeDescriptor is None:
            raise RuntimeError('player Vehicle descriptor is unavailable')
        descriptor = entity.typeDescriptor
        state = self._gun_state
        if state is None:
            state = gun_mechanics.GunState(
                descriptor, self._local_loadout(descriptor),
                ammo_layout=self._local_ammo_layout())
            self._gun_state = state
        now = self._clock() if now is None else float(now)
        if self._gun_last_tick is None:
            self._gun_last_tick = now
        dt = max(0.0, now - self._gun_last_tick)
        self._gun_last_tick = now
        reload_rescaled = self._rescale_current_reload(
            state, critical_damage.stat_factor(entity, 'reload'))
        previous_reload = state.reload_time
        state.tick(
            dt, self._battle_live, self._local_speed,
            self._local_turn_speed, 0.0, descriptor,
            dispersion_factor=critical_damage.stat_factor(
                entity, 'dispersion'),
            aim_time_factor=critical_damage.stat_factor(
                entity, 'aim_time'))
        self._report_crew_penalty(entity)
        self._publish_ammo_state(state)
        self._tick_equipment_cooldowns(now)
        if not self._battle_live:
            self._publish_reload_event(0.0, state.reload_duration)
        elif reload_rescaled:
            self._publish_reload_event(
                state.reload_time, state.reload_duration, force=True)
        elif self._reload_event is None:
            self._publish_reload_event(
                state.reload_time, state.reload_duration)
        elif previous_reload > 0.0 and state.reload_time <= 0.0:
            self._publish_reload_event(
                0.0, state.reload_duration, force=True)
        # #1513 updateTargetingInfo is a server-parameter update, not a
        # per-frame reticle publisher.  Publish only when descriptor, crew or
        # module parameters change; the stock rotator owns convergence.
        self._publish_targeting_info(entity, state)
        return state

    def _ammo_tick(self):
        if self.state != 'running' or self._server is None:
            return
        try:
            entity = self._server_entity(self._server.vehicle_id)
            self._advance_local_gun_to(entity)
            self._run_optional_feature(
                'server gun-marker presentation',
                self._sync_local_server_marker)
        except Exception as error:
            self._fail(error)
            return
        self._schedule(AMMO_SECONDS, self._ammo_tick, ammo=True)

    def _report_crew_penalty(self, entity):
        """Log the player's crew and module factors once per crew change."""
        impaired = frozenset(getattr(entity, '_crew_impaired', None) or ())
        if impaired == self._reported_crew_impaired:
            return False
        self._reported_crew_impaired = impaired
        sys.stdout.write(
            '[Offline LAN 0.9.22] CREW out=%s reload=%.3f aim=%.3f '
            'disp=%.3f turret=%.3f mobility=%.3f vision=%.3f\n' % (
                ','.join(sorted(impaired)) or '-',
                critical_damage.stat_factor(entity, 'reload'),
                critical_damage.stat_factor(entity, 'aim_time'),
                critical_damage.stat_factor(entity, 'dispersion'),
                critical_damage.stat_factor(entity, 'turret_speed'),
                critical_damage.stat_factor(entity, 'mobility'),
                critical_damage.stat_factor(entity, 'vision')))
        return True

    def _roll_loader_intuition(self):
        """Roll the finished ``loader_intuition`` perk for one shell swap.

        The #1513 skill text stacks two loaders, so each finished perk rolls
        its own ``INTUITION_CHANCE``.
        """
        chances = loadout_law.intuition_chances(
            self._garage_loadout_snapshot()['crew'])
        for unused_index in range(chances):
            if random.random() < loadout_law.INTUITION_CHANCE:
                return True
        return False

    def _present_loader_intuition(self):
        """Play the stock intuition notification for an instant shell swap."""
        status_group = getattr(
            self._runtime.constants, 'VEHICLE_MISC_STATUS', None)
        callback = getattr(self._avatar, 'updateVehicleMiscStatus', None)
        if (status_group is None or not callable(callback) or
                self._server is None):
            return False
        status = getattr(status_group, 'LOADER_INTUITION_WAS_USED', None)
        if status is None:
            return False
        try:
            # #1513 indexes floatArgs[0] before it dispatches this status.
            callback(self._server.vehicle_id, status, 0, (0.0,))
        except Exception as error:
            # The shell transaction is authoritative. A stock presentation
            # failure must not strand the new round behind a failed HUD call.
            sys.stdout.write(
                '[Offline LAN 0.9.22] loader intuition notification '
                'failed: %s\n' % error)
            return False
        return True

    def _reload_partial_clip_now(self, state):
        previous_reload = state.reload_time
        previous_duration = state.reload_duration
        if not state.reload_partial_clip():
            return False
        if previous_reload > 0.0:
            self._publish_reload_event(
                0.0, previous_duration, force=True)
        self._publish_ammo_state(state, force=True)
        self._publish_reload_event(
            state.reload_time, state.reload_duration, force=True)
        return True

    def _publish_loaded_shell_change(
            self, state, previous_reload, previous_duration):
        # #1513 ReloadingTimeState retains its original _startTime while
        # actualTime stays positive. A shell switch during an active reload is
        # a new cycle. Close that old cycle before CURRENT_SHELLS is
        # republished, then start the new shell's cycle. This is the event order
        # consumed by both stock HUD subscribers and prevents their -0.01
        # sentinel from becoming the lasting reload value for the new shell.
        if previous_reload > 0.0:
            self._publish_reload_event(
                0.0, previous_duration, force=True)
        self._publish_ammo_state(state, force=True)
        self._publish_reload_event(
            state.reload_time, state.reload_duration, force=True)
        self._sender.send_current()

    def _switch_current_shell(self, state, index):
        previous_reload = state.reload_time
        previous_duration = state.reload_duration
        instant = self._roll_loader_intuition()
        changed = state.sync_shell_index(index, instant=instant)
        if not changed:
            return False
        self._publish_loaded_shell_change(
            state, previous_reload, previous_duration)
        if instant:
            self._present_loader_intuition()
        return True

    def change_vehicle_setting(self, code, value):
        settings = self._runtime.constants.VEHICLE_SETTING
        if code == getattr(settings, 'SIEGE_MODE_ENABLED', None):
            if (not isinstance(value, _INTEGER_TYPES) or
                    int(value) not in (0, 1)):
                return False
            if self._server is None:
                return False
            entity = self._server_entity(self._server.vehicle_id)
            descriptor = getattr(entity, 'typeDescriptor', None)
            if not bool(getattr(descriptor, 'hasSiegeMode', False)):
                return False
            if not self._sender.send_current(siege_enabled=bool(value)):
                return False
            request_seq = getattr(self.client, '_input_seq', None)
            if (isinstance(request_seq, bool) or
                    not isinstance(request_seq, _INTEGER_TYPES) or
                    request_seq <= 0):
                request_seq = None
            # The server echo can be one or more snapshots behind this
            # request. Keep the drivetrain locked from the successful send
            # edge until a stable snapshot acknowledges this exact input.
            self._local_siege_pending = (bool(value), request_seq)
            return True
        if code == getattr(settings, 'ACTIVATE_EQUIPMENT', None):
            return self._activate_equipment(value)
        partial_clip = getattr(settings, 'RELOAD_PARTIAL_CLIP', None)
        if code == partial_clip:
            if self._gun_state is None:
                return False
            state = self._gun_state
            pending_fire = self._local_fire_intent
            if isinstance(pending_fire, dict):
                if state.clip_size <= 1 or not state.shots:
                    return False
                pending_fire['deferred_partial_clip_reload'] = True
                return True
            return self._reload_partial_clip_now(state)
        current_shells = getattr(settings, 'CURRENT_SHELLS', None)
        next_shells = getattr(settings, 'NEXT_SHELLS', None)
        if code not in (current_shells, next_shells) or self._gun_state is None:
            return False
        state = self._gun_state
        for index, shot in enumerate(state.shots):
            shell = _field(shot, 'shell', {})
            if int(_field(shell, 'compactDescr', 0)) != int(value):
                continue
            previous_reload = state.reload_time
            previous_duration = state.reload_duration
            previous_selection = (
                int(state.shot_index), state.pending_index)
            if code == current_shells:
                pending_fire = self._local_fire_intent
                if isinstance(pending_fire, dict):
                    # The physical round was frozen at the trigger edge. Keep
                    # it loaded until that shot is accepted or rejected; the
                    # requested shell can still be queued for the shot boundary.
                    state.request_shell_index(index)
                    pending_fire['deferred_current_shell_index'] = int(index)
                    if previous_selection != (
                            int(state.shot_index), state.pending_index):
                        self._sender.send_current()
                    return True
                self._switch_current_shell(state, index)
                return True
            changed = state.request_shell_index(index)
            if changed:
                self._publish_loaded_shell_change(
                    state, previous_reload, previous_duration)
            elif previous_selection != (
                    int(state.shot_index), state.pending_index):
                # NEXT_SHELLS changes no loaded-round HUD state, but it is a
                # real ordered player input. Donate it now so the hidden gun
                # authority promotes the same shell at the canonical shot
                # boundary instead of silently loading the old type again.
                self._sender.send_current()
            # The stock ammo panel already blinks a queued shell locally.
            return True
        return False

    def on_snapshot(self, message):
        if self.state in ('failed', 'stopped', 'leaving'):
            return False
        previous_snapshot = self._last_snapshot
        try:
            self._last_snapshot = dict(message or {})
            self._restore_local_equipment_snapshot(
                self._last_snapshot, present=True)
            self._ack_local_ram_contacts(self._last_snapshot)
            self._ack_local_destructible_contacts(self._last_snapshot)
            self._observe_destructibles_disabled(self._last_snapshot)
            self._observe_projectile_message(self._last_snapshot)
            self._reconcile_projectile_snapshot(self._last_snapshot)
            if 'rules' in self._last_snapshot:
                self._apply_rules(self._last_snapshot.get('rules'))
            if self._last_snapshot.get('battle_result') is not None:
                self._apply_battle_result(
                    self._last_snapshot['battle_result'])
            if 'destructibles' in self._last_snapshot:
                self._apply_destructible_state(
                    self._last_snapshot.get('destructibles'))
            if self._bots is not None:
                if 'bot_authority_id' in self._last_snapshot:
                    self._reconcile_bot_authority(
                        self._last_snapshot.get('bot_authority_id'))
                self._bots.apply_snapshot(self._last_snapshot)
                self._remember_ram_bot_snapshot(self._last_snapshot)
            if self._sync is not None:
                self._sync.snapshot(message)
            return True
        except Exception as error:
            self._last_snapshot = previous_snapshot
            self._ignore_live_payload('snapshot', error, message)
            return False

    def _ignore_live_payload(self, kind, error, message=None):
        """Contain one recoverable live payload without ending the round."""
        reason = self._bounded_failure_reason(error)
        callback = getattr(
            getattr(self, 'client', None), '_ignore_runtime_payload', None)
        if callable(callback):
            try:
                if callback(kind, reason, message):
                    return True
            except Exception:
                pass
        self._warn_optional_failure(
            'recoverable %s payload' % kind, error, disable=False)
        return True

    def _remember_ram_bot_snapshot(self, snapshot):
        """Retain the canonical bot bodies referenced by player contacts."""
        if not isinstance(snapshot, dict) or self._bots is None:
            return False
        try:
            revision = int(snapshot.get('bot_state_revision'))
            sample_time_us = int(snapshot.get('bot_state_time_us'))
        except (TypeError, ValueError, OverflowError):
            return False
        if revision < 0 or sample_time_us < 0:
            return False
        states = {}
        current = getattr(self._bots, 'states', {}) or {}
        for raw in snapshot.get('bots') or ():
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            try:
                bot_id = int(raw['id'])
            except (TypeError, ValueError, OverflowError):
                continue
            # Dynamic pose fields must come from the exact wire revision the
            # player collided with.  An authority runtime may already have
            # integrated its local ``states`` beyond this snapshot; only use
            # that newer state to fill descriptor-derived static fields.
            state = {}
            current_state = current.get(bot_id)
            if isinstance(current_state, dict):
                for name in ('mass', 'collision_shape', 'ram_profile',
                             'vehicle', 'team'):
                    if name in current_state:
                        state[name] = current_state[name]
            state.update(raw)
            states[bot_id] = state
        previous_states = self._ram_bot_history.get(revision)
        previous_time_us = self._ram_bot_history_times.get(revision)
        if isinstance(previous_states, dict):
            self._remove_ram_bot_history_index(
                revision, previous_time_us, previous_states)
        if revision not in self._ram_bot_history:
            self._ram_bot_history_order.append(revision)
        self._ram_bot_history[revision] = states
        self._ram_bot_history_times[revision] = sample_time_us
        sample_key = (sample_time_us, revision)
        for bot_id in states:
            bisect.insort_right(
                self._ram_bot_history_index.setdefault(bot_id, []),
                sample_key)
        # A later sample can provide a missing right bracket or a better
        # velocity neighbour for an exact-time receipt. Invalidate resolved
        # lookups once per history advance, rather than rebuilding and sorting
        # the complete 512-revision timeline for every render-frame retry.
        self._ram_bot_lookup_cache.clear()
        # The server admits receipts up to 255 revisions behind its current
        # state. Keep transport headroom on both sides of a skipped revision
        # so a coalesced snapshot can still provide the later bracket.
        while len(self._ram_bot_history_order) > 512:
            expired = self._ram_bot_history_order.pop(0)
            expired_states = self._ram_bot_history.pop(expired, None)
            expired_time_us = self._ram_bot_history_times.pop(expired, None)
            if isinstance(expired_states, dict):
                self._remove_ram_bot_history_index(
                    expired, expired_time_us, expired_states)
        return True

    def _remove_ram_bot_history_index(self, revision, sample_time_us,
                                      states):
        """Remove one wire revision from each bot's ordered RAM timeline."""
        if sample_time_us is None:
            return
        sample_key = (sample_time_us, revision)
        for bot_id in states:
            timeline = self._ram_bot_history_index.get(bot_id)
            if not timeline:
                continue
            index = bisect.bisect_left(timeline, sample_key)
            if index < len(timeline) and timeline[index] == sample_key:
                timeline.pop(index)
            if not timeline:
                self._ram_bot_history_index.pop(bot_id, None)

    def _ram_bot_state_at(self, bot_id, revision, sample_time_us):
        """Interpolate one bot from the exact wire samples a player saw."""
        try:
            bot_id = int(bot_id)
            revision = int(revision)
            sample_time_us = int(sample_time_us)
        except (TypeError, ValueError, OverflowError):
            return None
        cache_key = (bot_id, revision, sample_time_us)
        if cache_key in self._ram_bot_lookup_cache:
            cached = self._ram_bot_lookup_cache[cache_key]
            return None if cached is None else dict(cached)
        timeline = self._ram_bot_history_index.get(bot_id)
        if not timeline:
            self._ram_bot_lookup_cache[cache_key] = None
            return None
        target = (sample_time_us, revision)
        left_index = bisect.bisect_right(timeline, target) - 1
        right_index = bisect.bisect_left(timeline, target)
        if left_index < 0 or right_index >= len(timeline):
            self._ram_bot_lookup_cache[cache_key] = None
            return None
        left_time, left_revision = timeline[left_index]
        right_time, right_revision = timeline[right_index]
        # Revisions and their presentation times are both monotonic on the
        # wire. Fail closed if a malformed timeline violates that contract;
        # do not turn a hostile ordering into a linear render-frame scan.
        if (left_time > sample_time_us or left_revision > revision or
                right_time < sample_time_us or right_revision < revision):
            self._ram_bot_lookup_cache[cache_key] = None
            return None
        left_state = self._ram_bot_history.get(
            left_revision, {}).get(bot_id)
        right_state = self._ram_bot_history.get(
            right_revision, {}).get(bot_id)
        if not isinstance(left_state, dict) or not isinstance(
                right_state, dict):
            self._ram_bot_lookup_cache[cache_key] = None
            return None
        if left_time == right_time:
            result = dict(left_state)
            result['ram_vx'] = 0.0
            result['ram_vy'] = 0.0
            result['ram_vz'] = 0.0
            if len(timeline) >= 2:
                before_index, after_index = (
                    (left_index - 1, left_index) if left_index > 0
                    else (left_index, left_index + 1))
                before_time, before_revision = timeline[before_index]
                after_time, after_revision = timeline[after_index]
                before_state = self._ram_bot_history.get(
                    before_revision, {}).get(bot_id)
                after_state = self._ram_bot_history.get(
                    after_revision, {}).get(bot_id)
                span = float(after_time - before_time) / 1000000.0
                if (span > 0.0 and isinstance(before_state, dict) and
                        isinstance(after_state, dict)):
                    result['ram_vx'] = (
                        _number(after_state.get('x')) -
                        _number(before_state.get('x'))) / span
                    result['ram_vy'] = (
                        _number(after_state.get('y')) -
                        _number(before_state.get('y'))) / span
                    result['ram_vz'] = (
                        _number(after_state.get('z')) -
                        _number(before_state.get('z'))) / span
            self._ram_bot_lookup_cache[cache_key] = dict(result)
            return result
        span_us = float(right_time - left_time)
        if span_us <= 0.0:
            self._ram_bot_lookup_cache[cache_key] = None
            return None
        progress = max(0.0, min(
            (sample_time_us - left_time) / span_us, 1.0))
        result = dict(left_state)
        for name in ('x', 'y', 'z', 'pitch', 'roll', 'aim_yaw',
                     'gun_pitch'):
            if name in left_state and name in right_state:
                result[name] = (_number(left_state.get(name)) +
                                (_number(right_state.get(name)) -
                                 _number(left_state.get(name))) * progress)
        if 'yaw' in left_state and 'yaw' in right_state:
            result['yaw'] = (_number(left_state.get('yaw')) +
                             _angle_delta(
                                 _number(left_state.get('yaw')),
                                 _number(right_state.get('yaw'))) * progress)
        if progress >= 1.0:
            result['alive'] = bool(right_state.get('alive', True))
        result['ram_vx'] = (
            _number(right_state.get('x')) -
            _number(left_state.get('x'))) * 1000000.0 / span_us
        result['ram_vy'] = (
            _number(right_state.get('y')) -
            _number(left_state.get('y'))) * 1000000.0 / span_us
        result['ram_vz'] = (
            _number(right_state.get('z')) -
            _number(left_state.get('z'))) * 1000000.0 / span_us
        self._ram_bot_lookup_cache[cache_key] = dict(result)
        return result

    def _ram_bot_revision_at(self, bot_id, sample_time_us):
        """Return the wire revision that right-brackets a presented Bot."""
        try:
            bot_id = int(bot_id)
            sample_time_us = int(sample_time_us)
        except (TypeError, ValueError, OverflowError):
            return None
        timeline = self._ram_bot_history_index.get(bot_id)
        if not timeline:
            return None
        right_index = bisect.bisect_left(timeline, (sample_time_us, -1))
        if right_index >= len(timeline):
            return None
        if right_index > 0 or timeline[right_index][0] == sample_time_us:
            return int(timeline[right_index][1])
        return None

    def on_roster(self, message):
        """Apply authority changes that can arrive before live snapshots.

        The server does not tick snapshots while #1513 clients are behind the
        native entity-load barrier, so a loading-phase roster is the only
        durable authority update channel. A round that loses its worker is
        ended by the server; this client never takes the bot
        simulation over.
        """
        if self.state in ('failed', 'stopped', 'leaving'):
            return False
        message = message if isinstance(message, dict) else {}
        round_id = message.get('round_id')
        if (self._start_message is None or
                round_id != self._start_message.get('round_id')):
            return False
        if 'bot_authority_id' not in message:
            return False
        self._observe_projectile_message(message)
        player_id = message.get('bot_authority_id')
        self._start_message['bot_authority_id'] = player_id
        self._observe_destructibles_disabled(message)
        if self._bots is None:
            return True
        return self._reconcile_bot_authority(player_id)

    def on_bot_observation(self, message):
        """Consume one server-admitted observation without relaying it."""
        if self.state != 'running':
            return False
        message = message if isinstance(message, dict) else {}
        if (self._start_message is None or
                message.get('round_id') !=
                self._start_message.get('round_id')):
            return False
        try:
            now = self._clock()
            team_changed = self._apply_team_observation(message, now)
            enemy_changed = self._observe_local_vehicle(message, now)
            return bool(team_changed or enemy_changed)
        except Exception as error:
            self._fail(error)
            return False

    def _reconcile_bot_authority(self, player_id):
        """Recover authority changes even if the one-shot event was missed."""
        if (self._bots is None or
                getattr(self._bots, 'authority_id', None) == player_id):
            return False
        # Arc jobs and completed launch receipts are native-world proofs made
        # by one authority.  They must never survive an ownership handoff,
        # regardless of whether this client gains or loses simulation duty.
        if self._artillery is not None:
            self._artillery.reset()
        self._bot_fire_seen = {}
        self._bot_fire_confirmations = {}
        self._bot_launch_payloads = {}
        start = dict(self._start_message or {})
        start['bot_authority_id'] = player_id
        if (self._worker_mode and
                lan_protocol.HUMAN_RAM_TIMELINE_CAPABILITY in
                getattr(self.client, 'capabilities', ()) and
                lan_protocol.HUMAN_RAM_TIMELINE_CAPABILITY in
                getattr(self.client, 'server_capabilities', ())):
            start['human_ram_timeline'] = True
        snapshot = self._last_snapshot or {}
        if not self._worker_mode:
            # A visible client tracks the infrastructure lineage only. It
            # must never build a takeover manifest or publish bot messages,
            # even if a malformed server announces an ownership change.
            if self._bots.battle_start(start):
                raise RuntimeError(
                    'visible client attempted to become bot authority')
            return True
        manifest = snapshot.get(
            'bot_manifest', start.get('bot_manifest', [])) or []
        live_by_id = {}
        for raw in snapshot.get('bots') or ():
            if isinstance(raw, dict) and raw.get('id') is not None:
                live_by_id[int(raw['id'])] = raw
        # The server manifest intentionally owns identity/profile/route while
        # snapshot.bots owns the canonical live pose and fire sequence.  Merge
        # both before promoting a new authority; using the manifest alone
        # respawned every bot at its formation slot during failover.
        takeover = []
        for raw in manifest:
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            merged = dict(raw)
            merged.update(live_by_id.get(int(raw['id']), {}))
            takeover.append(merged)
        if not takeover:
            takeover = [dict(raw) for raw in snapshot.get('bots') or ()
                        if isinstance(raw, dict)]
        start['bot_manifest'] = takeover
        if snapshot.get('battle_result') is not None:
            start['battle_result'] = snapshot.get('battle_result')
        for outgoing in self._bots.battle_start(start):
            if outgoing.get('type') == 'bot_manifest':
                self._enqueue_bot_manifest(outgoing)
            else:
                self._send_bot_message(outgoing)
        self._set_bot_presentation_interpolation(player_id)
        if self._bots.is_authority():
            for state in snapshot.get('bots') or ():
                try:
                    self._bot_fire_seen[int(state['id'])] = max(
                        0, int(state.get('fire_seq', 0)))
                except (KeyError, TypeError, ValueError):
                    continue
        return True

    def on_fire_intent(self, message):
        """Queue one server-admitted trigger for worker-side resolution."""
        if not self._worker_mode or self.state != 'running':
            return False
        required = {
            'type', 'round_id', 'authority_epoch', 'player_id', 'intent_seq',
            'shot_seq', 'input_seq', 'pose_time_us', 'shell_index',
            'next_shell_index', 'shell_change_pending',
            'gun_checkpoint_seq', 'gun_checkpoint',
            'aim_yaw', 'gun_pitch', 'x', 'y', 'z', 'yaw', 'pitch', 'roll',
            'speed', 'shot_origin', 'shot_direction', 'dispersion_angle'}
        transport_fields = {
            '_client_received_time', '_client_dispatch_delay'}
        if (not isinstance(message, dict) or
                not required.issubset(message) or
                not set(message).issubset(required | transport_fields)):
            raise RuntimeError('worker fire intent is malformed')
        try:
            player_id = int(message['player_id'])
            intent_seq = int(message['intent_seq'])
            shot_seq = int(message['shot_seq'])
            input_seq = int(message['input_seq'])
            pose_time_us = int(message['pose_time_us'])
            shell_index = int(message['shell_index'])
            next_shell_index = int(message['next_shell_index'])
            gun_checkpoint_seq = int(message['gun_checkpoint_seq'])
            authority_epoch = int(message['authority_epoch'])
            aim_yaw = float(message['aim_yaw'])
            gun_pitch = float(message['gun_pitch'])
            values = tuple(float(message[name]) for name in (
                'x', 'y', 'z', 'yaw', 'pitch', 'roll', 'speed',
                'dispersion_angle'))
            shot_origin = tuple(float(value) for value in
                                message['shot_origin'])
            shot_direction = tuple(float(value) for value in
                                   message['shot_direction'])
            gun_checkpoint = lan_protocol._canonical_human_gun_checkpoint(
                message['gun_checkpoint'])
        except (TypeError, ValueError, OverflowError):
            raise RuntimeError('worker fire intent has invalid values')
        values += (aim_yaw, gun_pitch) + shot_origin + shot_direction
        direction_length = math.sqrt(sum(
            value * value for value in shot_direction))
        if (message.get('round_id') != (self._start_message or {}).get(
                'round_id') or
                authority_epoch != int(self.client.authority_epoch) or
                player_id <= 0 or intent_seq <= 0 or shot_seq <= 0 or
                input_seq <= 0 or pose_time_us < 0 or
                gun_checkpoint_seq != input_seq or
                gun_checkpoint is None or
                not 0 <= shell_index <= 9 or
                not 0 <= next_shell_index <= 9 or
                not isinstance(message['shell_change_pending'], bool) or
                (not message['shell_change_pending'] and
                 next_shell_index != shell_index) or
                not isinstance(message['shot_origin'], list) or
                len(message['shot_origin']) != 3 or
                not isinstance(message['shot_direction'], list) or
                len(message['shot_direction']) != 3 or
                not 0.999 <= direction_length <= 1.001 or
                not 0.0 <= float(message['dispersion_angle']) <= 0.5 or
                any(math.isnan(value) or math.isinf(value)
                    for value in values)):
            raise RuntimeError('worker fire intent violates its contract')
        # The LAN receive thread decorates every frame with its local receipt
        # time.  That transport-only value is neither part of the server's
        # admitted fire identity nor stable across an exact retry.
        frozen = dict((name, message[name]) for name in required)
        key = (player_id, intent_seq)
        previous = self._player_fire_intents.get(
            key, self._player_fire_intent_history.get(key))
        if previous is not None:
            if previous != frozen:
                raise RuntimeError('worker fire intent identity conflict')
            return True
        if any(int(value.get('player_id', 0)) == player_id
               for value in self._player_fire_intents.values()):
            raise RuntimeError('worker received overlapping fire intents')
        self._player_fire_intents[key] = frozen
        return True

    def on_player_destructible_contact(self, message):
        """Resolve one server-admitted player hull contact immediately."""
        if not self._worker_mode or self.state != 'running':
            return False
        required = {
            'type', 'protocol', 'round_id', 'authority_epoch', 'player'}
        transport_fields = {
            '_client_received_time', '_client_dispatch_delay'}
        if (not isinstance(message, dict) or
                not required.issubset(message) or
                not set(message).issubset(required | transport_fields)):
            raise RuntimeError(
                'worker player destructible contact is malformed')
        player = message.get('player')
        if (not isinstance(player, dict) or set(player) != {
                'id', 'vehicle', 'vehicle_compact_descr',
                'effective_params', 'destructible_contacts'} or
                self._player_effective_snapshot(player) is None or
                not isinstance(player.get('destructible_contacts'), list) or
                len(player['destructible_contacts']) != 1):
            raise RuntimeError(
                'worker player destructible contact body is malformed')
        try:
            authority_epoch = int(message['authority_epoch'])
            player_id = int(player['id'])
        except (TypeError, ValueError, OverflowError):
            raise RuntimeError(
                'worker player destructible contact has invalid values')
        if (message.get('round_id') != (self._start_message or {}).get(
                'round_id') or
                authority_epoch != int(self.client.authority_epoch) or
                player_id <= 0):
            raise RuntimeError(
                'worker player destructible contact violates its contract')
        return bool(self._resolve_player_destructible_contacts(
            [dict(player)], self._clock()))

    def on_player_destructible_contact_result(self, message):
        """Correct one visible prediction after the worker rejects it."""
        if self._worker_mode or not isinstance(message, dict):
            return False
        required = {
            'type', 'round_id', 'contact_seq', 'accepted'}
        pose_fields = {'x', 'y', 'z', 'yaw'}
        transport_fields = {
            '_client_received_time', '_client_dispatch_delay'}
        fields = set(message) - transport_fields
        if (fields not in (required, required | pose_fields) or
                message.get('type') !=
                'player_destructible_contact_result' or
                message.get('round_id') !=
                (self._start_message or {}).get('round_id') or
                message.get('accepted') is not False):
            return False
        try:
            sequence = int(message.get('contact_seq'))
        except (TypeError, ValueError, OverflowError):
            return False
        if sequence <= 0:
            return False
        server_pose = None
        if pose_fields.issubset(message):
            try:
                values = tuple(float(message[name]) for name in (
                    'x', 'y', 'z', 'yaw'))
            except (TypeError, ValueError, OverflowError):
                return False
            if any(math.isnan(value) or math.isinf(value)
                   for value in values):
                return False
            server_pose = (values[:3], values[3])
        changed = self._apply_local_destructible_rejection(
            sequence, server_pose)
        for seq in list(self._local_destructible_contacts):
            if seq <= sequence:
                self._local_destructible_contacts.pop(seq, None)
                self._local_destructible_safe_poses.pop(seq, None)
                changed = True
        return changed

    def on_fire_intent_result(self, message):
        """Release the matching visible or worker trigger after rejection."""
        if not isinstance(message, dict):
            return False
        try:
            sequence = int(message.get('intent_seq'))
        except (TypeError, ValueError, OverflowError):
            return False
        if self._worker_mode:
            try:
                player_id = int(message.get('player_id'))
            except (TypeError, ValueError, OverflowError):
                return False
            pending = self._player_fire_launch_pending.get(player_id)
            if (message.get('round_id') !=
                    (self._start_message or {}).get('round_id') or
                    message.get('accepted') is not False or
                    not isinstance(pending, dict) or
                    sequence != int(pending.get('intent_seq', 0))):
                return False
            self._player_fire_launch_pending.pop(player_id, None)
            return True
        pending = self._local_fire_intent
        if (message.get('round_id') != (self._start_message or {}).get(
                'round_id') or message.get('accepted') is not False or
                not isinstance(pending, dict) or
                sequence != int(pending.get('intent_seq', 0))):
            return False
        reason = str(message.get('reason', 'rejected') or 'rejected')
        sys.stdout.write(
            '[Offline LAN 0.9.22] FIRE INTENT rejected intent=%d reason=%s\n'
            % (sequence, reason))
        deferred_shell = pending.get('deferred_current_shell_index')
        deferred_partial_reload = bool(
            pending.get('deferred_partial_clip_reload'))
        self._local_fire_intent = None
        self._cancel_native_shot_wait()
        if deferred_shell is not None and self._gun_state is not None:
            self._switch_current_shell(self._gun_state, deferred_shell)
        if deferred_partial_reload and self._gun_state is not None:
            self._reload_partial_clip_now(self._gun_state)
        return True

    def _cancel_native_shot_wait(self):
        """Close exact #1513's trigger acknowledgement handshake."""
        cancel = getattr(self._avatar, 'cancelWaitingForShot', None)
        if not callable(cancel):
            raise RuntimeError(
                '#1513 shot acknowledgement cancel boundary is unavailable')
        cancel()
        return True

    def _defer_cancel_native_shot_wait(self):
        """Cancel a synchronous rejection after PlayerAvatar starts waiting."""
        generation = self._generation

        def cancel_after_mailbox_returns():
            if generation != self._generation:
                return
            # A duplicate trigger while one canonical launch is pending must
            # not cancel the acknowledgement wait owned by the accepted shot.
            if isinstance(self._local_fire_intent, dict):
                return
            try:
                self._cancel_native_shot_wait()
            except Exception as error:
                self._fail(error)

        self._runtime.bigworld.callback(0.0, cancel_after_mailbox_returns)
        return True

    def _bot_authority_is_local(self, player_id):
        local_id = getattr(self.client, 'player_id', None)
        if player_id is None or local_id is None:
            return False
        try:
            return int(player_id) == int(local_id)
        except (TypeError, ValueError, OverflowError):
            raise RuntimeError('bot authority identity is invalid')

    def _set_bot_presentation_interpolation(self, player_id):
        """Match native pose providers to the current bot authority."""
        if self._remote_factory is None:
            return False
        setter = getattr(
            self._remote_factory, 'set_entity_interpolate_motion', None)
        if not callable(setter):
            return False
        enabled = self._bot_authority_is_local(player_id)
        changed = False
        for record in self._records.values():
            if (record.get('kind') != 'bot' or
                    not record.get('native_remote') or
                    record.get('tombstone')):
                continue
            entity_changed = setter(record['engine_id'], enabled)
            if not entity_changed:
                continue
            record.pop('track_pose_sample', None)
            unused_marker, minimap_started = \
                self._remote_visual_components(record)
            if record.get('ready') and minimap_started:
                self._binding.refresh_vehicle_minimap(record['engine_id'])
            changed = True
        return changed

    def on_events(self, message):
        if self.state in ('failed', 'stopped', 'leaving'):
            return False
        try:
            self._observe_destructibles_disabled(message)
            self._observe_projectile_message(message or {})
        except Exception as error:
            self._ignore_live_payload('events', error, message)
            return False
        for raw_event in (message or {}).get('events') or ():
            event_id = None
            try:
                if not isinstance(raw_event, dict):
                    raise RuntimeError('ordered LAN event is malformed')
                event = dict(raw_event)
                event_id = event.get('event_id')
                if not event_id:
                    raise RuntimeError('ordered LAN event has no event_id')
                event_id = str(event_id)
                if event_id in self._accepted_event_ids:
                    continue
                self._prepare_ordered_event(event)
                self._accepted_event_ids.add(event_id)
                self._event_journal.append(event)
            except Exception as error:
                if event_id:
                    event_id = str(event_id)
                    self._accepted_event_ids.add(event_id)
                    self._applied_event_ids.add(event_id)
                self._ignore_live_payload('events', error, message)
        self._drain_event_journal()
        return True

    @staticmethod
    def _event_entity_key(event, role):
        if role == 'attacker':
            if event.get('attacker_bot') is not None:
                return 'bot:%s' % event.get('attacker_bot')
            if event.get('attacker') is not None:
                return 'player:%s' % event.get('attacker')
            return None
        if event.get('target_bot') is not None:
            return 'bot:%s' % event.get('target_bot')
        if event.get('target') is not None:
            return 'player:%s' % event.get('target')
        return None

    def _known_event_state(self, key):
        record = self._records.get(key)
        if record is not None:
            return record, record.get('state') or {}
        pending = self._pending_bot_creates.get(key)
        if pending is not None:
            return pending, pending.get('state') or {}
        raise RuntimeError('ordered LAN event references unknown entity %s' %
                           key)

    def _merge_shot_event_state(self, event):
        key = self._event_entity_key(event, 'attacker')
        if key is None:
            raise RuntimeError('ordered shot event has no attacker')
        holder, state = self._known_event_state(key)
        deadline = self._clock() + spotting.SHOT_CAMOUFLAGE_SECONDS
        if holder is self._records.get(key):
            holder['shot_penalty_until'] = deadline
        else:
            state = dict(state)
            state['shot_penalty_until'] = deadline
            holder['state'] = state

    def _missing_projectile_attacker_allowed(self, event):
        """Allow canonical delayed damage to outlive its shooter entity."""
        if (event.get('source') == 'fire' and
                event.get('kind') in (
                    'hit', 'bot_hit', 'bot_human_hit', 'bot_bot_hit')):
            return True
        if (event.get('source') != 'shot' or
                event.get('kind') not in (
                    'hit', 'bot_hit', 'bot_human_hit', 'bot_bot_hit')):
            return False
        projectile_id = event.get('projectile_id')
        return (projectile_id is not None and
                str(projectile_id) in self._projectile_lineage)

    def _combat_event_state(self, event, state, target_key):
        if 'health' not in event:
            raise RuntimeError('ordered combat event has no health')
        if 'death_reason' not in event:
            raise RuntimeError('ordered combat event has no death_reason')
        state = dict(state or {})
        health = max(0, int(event.get('health', 0)))
        state['health'] = health
        state['alive'] = health > 0 and not bool(event.get('dead', False))
        # Canonical shot/fire/ram events normally omit ``display_health``.
        # Do not retain the preceding snapshot's value: that would make the
        # next snapshot look like another health edge and replay it without
        # the event's attacker.  Exceptional preserved-hull deaths carry an
        # explicit display value and continue to win here.
        state['display_health'] = max(
            0, int(event.get('display_health', health)))
        try:
            death_reason = int(event['death_reason'])
        except (TypeError, ValueError):
            raise RuntimeError('ordered combat event has invalid death_reason')
        if death_reason < 0:
            raise RuntimeError('ordered combat event has invalid death_reason')
        if state['alive'] and death_reason != 0:
            raise RuntimeError(
                'nonfatal combat event has nonzero death_reason')
        state['death_reason'] = death_reason
        attacker_key = self._event_entity_key(event, 'attacker')
        if attacker_key is not None:
            attacker_kind, attacker_id = attacker_key.split(':', 1)
            state['death_attacker_kind'] = attacker_kind
            state['death_attacker_id'] = int(attacker_id)
        critical = event.get('critical')
        if isinstance(critical, dict) and target_key.startswith('bot:'):
            state['critical'] = self._critical_state(critical)
        for name in ('critical_revision', 'critical_base_revision',
                     'critical_ack_seq'):
            if name in event:
                state[name] = event[name]
        for name in ('combat_revision', 'combat_base_revision',
                     'combat_ack_seq'):
            if name in event:
                state[name] = event[name]
        return state

    def _merge_combat_event_state(self, event):
        target_key = self._event_entity_key(event, 'target')
        if target_key is None:
            raise RuntimeError('ordered combat event has no target')
        holder, state = self._known_event_state(target_key)
        attacker_key = self._event_entity_key(event, 'attacker')
        if attacker_key is not None:
            try:
                self._known_event_state(attacker_key)
            except RuntimeError:
                if not self._missing_projectile_attacker_allowed(event):
                    raise
        holder['state'] = self._combat_event_state(
            event, state, target_key)

    @staticmethod
    def _stun_entity_key(event):
        kind = event.get('target_kind')
        target = event.get('target_id')
        if (kind not in ('player', 'bot') or isinstance(target, bool) or
                not isinstance(target, _INTEGER_TYPES) or target <= 0):
            raise RuntimeError('stun event has an invalid target identity')
        return '%s:%d' % (kind, int(target))

    def _merge_stun_event_state(self, event):
        target_key = self._stun_entity_key(event)
        holder, state = self._known_event_state(target_key)
        active = event.get('active')
        end = event.get('stun_end_server_time_ms')
        if not isinstance(active, bool):
            raise RuntimeError('stun event has an invalid active state')
        if (isinstance(end, bool) or
                not isinstance(end, _INTEGER_TYPES) or end < 0 or
                active != (end > 0)):
            raise RuntimeError('stun event has an invalid end time')
        state = dict(state or {})
        state['stun_end_server_time_ms'] = int(end)
        if active:
            attacker_kind = event.get('attacker_kind')
            attacker_id = event.get('attacker_id')
            if attacker_kind not in ('player', 'bot'):
                raise RuntimeError(
                    'stun event has an invalid attacker identity')
            if (isinstance(attacker_id, bool) or
                    not isinstance(attacker_id, _INTEGER_TYPES) or
                    attacker_id <= 0):
                raise RuntimeError(
                    'stun event has an invalid attacker identity')
            state['stun_attacker_kind'] = attacker_kind
            state['stun_attacker_id'] = int(attacker_id)
        else:
            state['stun_attacker_kind'] = ''
            state['stun_attacker_id'] = 0
        holder['state'] = state

    def _prepare_ordered_event(self, event):
        kind = event.get('kind')
        if kind in _SHOT_EVENT_KINDS:
            self._merge_shot_event_state(event)
            normalized = self._projectile_wire_meta(event)
            if normalized is not None:
                self._projectile_lineage.add(normalized['projectile_id'])
        elif kind in _COMBAT_EVENT_KINDS:
            self._validate_combat_event_contract(event)
            self._merge_combat_event_state(event)
        elif kind == 'stun':
            self._merge_stun_event_state(event)
        elif kind not in _SIMPLE_EVENT_KINDS:
            raise RuntimeError(
                'ordered LAN event kind is unsupported: %s' % kind)

    @staticmethod
    def _record_is_event_ready(record):
        if record is None:
            return False
        if not record.get('ready', True):
            return False
        if (record.get('presentation') and
                not record.get('arena_added', False) and
                not record.get('simulation_entity', False)):
            return False
        return True

    def _event_is_ready(self, event):
        kind = event.get('kind')
        if kind in _SHOT_EVENT_KINDS:
            key = self._event_entity_key(event, 'attacker')
            record = self._records.get(key)
            if record is None and key not in self._pending_bot_creates:
                raise RuntimeError(
                    'ordered LAN event lost entity %s before apply' % key)
            return self._record_is_event_ready(record)
        if kind in _COMBAT_EVENT_KINDS:
            target_key = self._event_entity_key(event, 'target')
            target_record = self._records.get(target_key)
            if (target_record is None and
                    target_key not in self._pending_bot_creates):
                raise RuntimeError(
                    'ordered LAN event lost entity %s before apply' %
                    target_key)
            if not self._record_is_event_ready(target_record):
                return False
            attacker_key = self._event_entity_key(event, 'attacker')
            if attacker_key is None:
                return True
            attacker_record = self._records.get(attacker_key)
            if (attacker_record is None and
                    attacker_key not in self._pending_bot_creates):
                if self._missing_projectile_attacker_allowed(event):
                    return True
                raise RuntimeError(
                    'ordered LAN event lost entity %s before apply' %
                    attacker_key)
            return self._record_is_event_ready(attacker_record)
        if kind == 'stun':
            target_key = self._stun_entity_key(event)
            target_record = self._records.get(target_key)
            if (target_record is None and
                    target_key not in self._pending_bot_creates):
                raise RuntimeError(
                    'ordered LAN event lost entity %s before apply' %
                    target_key)
            return self._record_is_event_ready(target_record)
        return True

    _ASSIST_EVENT_TYPES = {
        'radio': 'RADIO_ASSIST',
        'track': 'TRACK_ASSIST',
        'stun': 'STUN_ASSIST',
    }

    def _apply_assist_event(self, event):
        """Feed one server-attributed assist to the stock damage log.

        ``PlayerAvatar.onBattleEvents`` forwards only the controlled vehicle's
        own events, so publish nothing unless this client is the assister.
        """
        if self._worker_mode:
            return False
        assister = self._records.get(self._assist_entity_key(event, 'assister'))
        if assister is None or not assister.get('local'):
            return False
        target = self._records.get(self._assist_entity_key(event, 'target'))
        if target is None:
            raise RuntimeError('assist event has no known target')
        name = self._ASSIST_EVENT_TYPES.get(event.get('category'))
        if name is None:
            raise RuntimeError(
                'assist category is unsupported: %s' % event.get('category'))
        feedback_common = getattr(
            self._runtime, 'battle_feedback_common', None)
        event_types = getattr(feedback_common, 'BATTLE_EVENT_TYPE', None)
        if event_types is None:
            raise RuntimeError('#1513 battle feedback constants are unavailable')
        callback = getattr(self._avatar, 'onBattleEvents', None)
        if not callable(callback):
            raise RuntimeError(
                '#1513 battle-event feedback boundary is unavailable')
        damage = max(0, int(event.get('damage', 0) or 0))
        callback([{
            'eventType': int(getattr(event_types, name)),
            'targetID': int(target['engine_id']), 'count': 1,
            'details': int(event_types.packDamage(
                damage, self._attack_reason('SHOT', 0)))}])
        return True

    @staticmethod
    def _assist_entity_key(event, role):
        """Resolve one ``<role>_kind``/``<role>_id`` pair to a record key."""
        kind = event.get(role + '_kind')
        actor = event.get(role + '_id')
        if kind not in ('player', 'bot') or actor is None:
            raise RuntimeError(
                'assist event has an invalid %s identity' % role)
        return '%s:%s' % (kind, actor)

    def _apply_ordered_event(self, event):
        kind = event.get('kind')
        if kind == 'authority':
            self._set_projectile_epoch(
                event.get('authority_epoch'), self._clock())
            if self._bots is not None:
                changed = self._reconcile_bot_authority(
                    event.get('player_id'))
                if changed and self._last_snapshot is not None:
                    self._bots.apply_snapshot(self._last_snapshot)
        elif kind in _SHOT_EVENT_KINDS:
            self._show_shot(event, update_state=False)
            self._accept_projectile_event(event)
        elif kind in _COMBAT_EVENT_KINDS:
            self._apply_combat_event(event, update_state=False)
        elif kind == 'vehicle_statistics':
            self._apply_vehicle_statistics_event(event)
        elif kind == 'assist':
            self._apply_assist_event(event)
        elif kind == 'stun':
            target = self._records.get(self._stun_entity_key(event))
            if target is None:
                raise RuntimeError('stun event has no ready target')
            self._apply_stun_state(target, target.get('state') or {})
        elif kind == 'destructible':
            self._apply_destructible_event(event)
        elif kind == 'projectile_ricochet':
            self._apply_projectile_ricochet_event(event)
        elif kind == 'projectile_impact':
            self._apply_projectile_terminal_event(event)
        elif kind == 'battle_result':
            self._apply_battle_result(event)
        elif kind == 'bot_manifest':
            # Durable bot identities arrive in the same tick's snapshot.  The
            # event is an explicit ordering marker and has no native effect.
            pass
        else:
            raise RuntimeError(
                'ordered LAN event kind is unsupported: %s' % kind)

    def _collection_counts(self):
        """Return the per-round collection sizes a leak would grow.

        The client runs against a 32-bit address-space ceiling, so every
        structure that lives for the whole round is reported once per window.
        """
        counts = {
            'journal': len(self._event_journal),
            'accepted_ids': len(self._accepted_event_ids),
            'applied_ids': len(self._applied_event_ids),
            'records': len(self._records),
            'records_dead': sum(
                1 for record in self._records.values()
                if not (record.get('state') or {}).get('alive', True)),
            'health': len(self._last_health),
            'pending_bots': len(self._pending_bot_creates),
            'grounded_bots': len(self._grounded_bot_ids),
            'bot_assignments': len(self._bot_vehicle_assignments),
            'bot_fire_seen': len(self._bot_fire_seen),
            'bot_destr_samples': len(self._bot_destructible_samples),
            'player_tree_destr_samples': len(
                self._player_tree_destructible_samples),
        }
        try:
            counts['projectiles'] = len(self._projectiles)
        except (AttributeError, TypeError):
            counts['projectiles'] = 0
        registry_counts = getattr(
            self._destructibles, 'registry_counts', None)
        if callable(registry_counts):
            for name, value in registry_counts().items():
                counts['destr_' + name] = value
        bot_states = getattr(self._bots, 'states', None)
        try:
            counts['bot_states'] = len(bot_states)
        except TypeError:
            counts['bot_states'] = 0
        counts['pose_keyframes'] = pose_animation_writes()
        return counts

    _MEASURED_STRUCTURES = (
        ('navgraph', '_navigation_graph'),
        ('foliage', '_foliage'),
        ('records', '_records'),
        ('journal', '_event_journal'),
        ('accepted_ids', '_accepted_event_ids'),
        ('applied_ids', '_applied_event_ids'),
        ('last_snapshot', '_last_snapshot'),
        ('start_message', '_start_message'),
        ('health', '_last_health'),
        ('bot_destr_samples', '_bot_destructible_samples'),
        ('player_tree_destr_samples',
         '_player_tree_destructible_samples'),
        ('spawn_planner', '_spawn_planner'),
        ('projectiles', '_projectiles'),
        ('projectile_meta', '_projectile_meta'),
        ('projectile_visual', '_projectile_visual_meta'),
        ('projectile_lineage', '_projectile_lineage'),
        ('bot_assignments', '_bot_vehicle_assignments'),
        ('spotting_cache', '_remote_spotting_cache'),
        ('frame_diag', '_frame_diagnostics'),
    )

    _MEASURED_BOT_STRUCTURES = (
        ('bot_states', 'states'),
        ('bot_decisions', '_decision_cache'),
        ('bot_descriptors', '_descriptors'),
        ('bot_visibility', '_visibility_cache'),
        ('bot_shot_los', '_shot_los_cache'),
        ('bot_gun_states', '_gun_states'),
        ('bot_ammo_states', '_ammo_states'),
        ('bot_physics', '_physics_params'),
        ('bot_motion_probe', '_motion_probe_cache'),
        ('bot_server_orders', '_server_orders'),
        ('bot_spot_profiles', '_spotting_profiles'),
        ('bot_cover_queue', '_cover_queue'),
        ('bot_receipts', '_world_receipt_waiting'),
        ('bot_debt', '_integration_debt'),
    )

    # The navigator and its terrain grid hold the port's second-largest set of
    # caches and were entirely absent from the first baseline.
    _MEASURED_NAVIGATOR_STRUCTURES = (
        ('nav_paths', 'paths'),
        ('nav_searches', 'searches'),
        ('nav_bot_states', 'bot_states'),
    )

    _MEASURED_NAV_GRID_STRUCTURES = (
        ('nav_edge_cache', '_edge_cache'),
        ('nav_segment_cache', '_segment_cache'),
        ('nav_ground_cache', '_ground_cache'),
        ('nav_failed_edges', '_failed_edges'),
    )

    _MEASURED_DIRECTOR_STRUCTURES = (
        ('ai_agents', 'agents'),
        ('ai_contacts', 'contacts'),
        ('ai_map_data', 'map_data'),
    )

    _MEASURED_DESTRUCTIBLE_GLOBALS = (
        ('destr_catalog', '_destructible_catalog'),
        ('destr_tree_state', 'g_offh_tree_state'),
        ('destr_instances', 'g_offh_destr_instances'),
        ('destr_contact_bins', 'g_offh_destr_contact_bins'),
        ('destr_pending', 'g_offh_destr_pending'),
        ('destr_falling', 'g_offh_destr_falling_active'),
        ('destr_seen', 'g_offh_destr_seen'),
        ('destr_chunks', 'g_offh_destr_chunks'),
    )

    _MEASURED_REMOTE_STRUCTURES = (
        ('remote_vehicles', '_vehicles'),
        ('remote_descriptors', '_descriptors'),
        ('remote_hit_testers', '_hit_testers'),
    )

    def _measured_module_structures(self):
        """Module caches that outlive a round, so a leak shows across rounds."""
        rows = []
        for module_name, attribute, label in (
                ('internal_hit_layouts', '_LAYOUT_CACHE', 'hit_layout_cache'),
                ('internal_hit_layouts', '_RUNTIME_VERIFICATION',
                 'hit_layout_evidence'),
                ('internal_layout_profiles', 'PROFILES', 'layout_profiles'),
                ('internal_geometry', '_PROBE_CACHE', 'geometry_probes'),
                ('tank_collision', '_SHAPE_CACHE', 'chassis_shapes')):
            module = sys.modules.get('%s.%s' % (_PORT_PACKAGE, module_name))
            if module is not None:
                rows.append((label, getattr(module, attribute, None)))
        maps = sys.modules.get('%s.ai.maps' % _PORT_PACKAGE)
        if maps is not None:
            rows.append(('ai_tactical_maps', getattr(maps, 'TACTICAL_MAPS', None)))
        return rows

    def _memory_rows(self):
        """Every resident structure this port owns, as (label, object) pairs."""
        rows = [(name, getattr(self, attribute, None))
                for name, attribute in self._MEASURED_STRUCTURES]
        bots = self._bots
        rows.extend((name, getattr(bots, attribute, None))
                    for name, attribute in self._MEASURED_BOT_STRUCTURES)
        navigator = getattr(bots, 'navigator', None)
        rows.extend((name, getattr(navigator, attribute, None))
                    for name, attribute in self._MEASURED_NAVIGATOR_STRUCTURES)
        rows.extend((name, getattr(getattr(navigator, 'grid', None),
                                   attribute, None))
                    for name, attribute in self._MEASURED_NAV_GRID_STRUCTURES)
        director = getattr(getattr(bots, 'adapter', None), 'director', None)
        rows.extend((name, getattr(director, attribute, None))
                    for name, attribute in self._MEASURED_DIRECTOR_STRUCTURES)
        rows.extend((name, getattr(self._destructibles, attribute, None))
                    for name, attribute
                    in self._MEASURED_DESTRUCTIBLE_GLOBALS)
        rows.extend((name, getattr(self._remote_factory, attribute, None))
                    for name, attribute in self._MEASURED_REMOTE_STRUCTURES)
        rows.extend(self._measured_module_structures())
        return rows

    def _report_memory(self, moment):
        """Rank the port's resident structures by retained bytes, once.

        The client is 32-bit and already runs near its address-space ceiling,
        so a baseline needs sizes, not just counts.  One ``seen`` set spans the
        whole ranking, so a structure reachable from two roots is charged once
        and the total stays a real total.  Native memory is invisible here: the
        native counters are printed beside the total instead.
        """
        # ``_deep_size`` deliberately walks every port-owned container.  Its
        # temporary ``seen`` set and work list can themselves be sizeable at
        # bots-ready/round-end, exactly when the 32-bit client is retaining a
        # complete arena and every remote model.  Memory diagnostics are an
        # opt-in troubleshooting tool; the normal game must not create that
        # extra peak merely to print a report the player did not request.
        if not bool((self._config or {}).get('debug_logging', False)):
            return False
        seen = set()
        sizes = []
        for name, value in self._memory_rows():
            if value is None:
                continue
            try:
                size = _deep_size(value, seen)
            except Exception:
                continue
            if size:
                sizes.append((size, name))
        sizes.sort(reverse=True)
        total = sum(size for size, unused in sizes)
        sys.stdout.write(
            '[Offline LAN 0.9.22] MEM %s total_kb=%d rows=%d %s\n' % (
                moment, total // 1024, len(sizes),
                ' '.join('%s=%dk' % (name, size // 1024)
                         for size, name in sizes[:24])))
        vehicles = getattr(self._remote_factory, '_vehicles', None) or {}
        sys.stdout.write(
            '[Offline LAN 0.9.22] MEM %s native poses=%d vehicles=%d '
            'models=%d descriptors=%d testers=%d\n' % (
                moment, pose_animation_writes(), len(vehicles),
                sum(1 for vehicle in vehicles.values()
                    if getattr(vehicle, 'model', None) is not None),
                len(getattr(self._remote_factory, '_descriptors', ()) or ()),
                len(getattr(self._remote_factory, '_hit_testers', ()) or ())))
        return True

    def _drain_event_journal(self):
        while self._event_journal:
            event = self._event_journal[0]
            try:
                ready = self._event_is_ready(event)
            except Exception as error:
                self._ignore_live_payload('events', error, {
                    'round_id': (self._start_message or {}).get('round_id'),
                    'event_id': event.get('event_id'),
                    'event_kind': event.get('kind'),
                })
                event_id = str(event['event_id'])
                self._applied_event_ids.add(event_id)
                self._event_journal.pop(0)
                continue
            if not ready:
                return False
            try:
                self._apply_ordered_event(event)
            except Exception as error:
                self._ignore_live_payload('events', error, {
                    'round_id': (self._start_message or {}).get('round_id'),
                    'event_id': event.get('event_id'),
                    'event_kind': event.get('kind'),
                })
            event_id = str(event['event_id'])
            self._applied_event_ids.add(event_id)
            self._event_journal.pop(0)
        return True

    def _pending_combat_for_record(self, record):
        for event in self._event_journal:
            if (event.get('kind') in _COMBAT_EVENT_KINDS and
                    self._records.get(
                        self._event_entity_key(event, 'target')) is record):
                return True
        return False

    def _pending_event_references(self, key):
        for event in self._event_journal:
            if (self._event_entity_key(event, 'target') == key or
                    self._event_entity_key(event, 'attacker') == key):
                return True
        return False

    def _report_destructible(self, event):
        context = self._projectile_destructible_context
        if context is not None:
            projectile_id = context
            meta = self._projectile_meta.get(projectile_id)
            if meta is None or not isinstance(event, dict) or \
                    event.get('is_shot') is not True:
                return False
            frozen = dict(event)
            key = (
                frozen.get('destructible_kind'), frozen.get('chunk_id'),
                frozen.get('item_index'), frozen.get('mat_kind'))
            pending = meta.setdefault('destructibles_pending', [])
            if key not in set(
                    (value.get('destructible_kind'), value.get('chunk_id'),
                     value.get('item_index'), value.get('mat_kind'))
                    for value in pending):
                if len(pending) >= 64:
                    raise RuntimeError(
                        'projectile destructible receipt limit exceeded')
                pending.append(frozen)
            return True
        if self.client is None:
            raise RuntimeError('LAN client is unavailable for destructible')
        sender = getattr(self.client, 'send_destructible', None)
        if not callable(sender):
            raise RuntimeError(
                'LAN client has no destructible report boundary')
        return bool(sender(event))

    def _apply_destructible_state(self, events):
        if not isinstance(events, (list, tuple)):
            raise RuntimeError('canonical destructible state is malformed')
        changed = False
        for event in events:
            if not isinstance(event, dict):
                raise RuntimeError(
                    'canonical destructible event is malformed')
            changed = self._apply_destructible_event(event) or changed
        return changed

    def _apply_destructible_event(self, event):
        if self._destructibles is None:
            if ((self._start_message or {}).get(
                    'destructibles_disabled') is True or
                    not self._optional_feature_enabled(
                        'destructible interactions')):
                return False
            raise RuntimeError('#1513 destructible runtime is unavailable')
        from gui.mods.offline_lan_0922 import destructibles_authority
        kind = str(event.get('destructible_kind', ''))
        if kind not in ('tree', 'column', 'fragile', 'module'):
            raise RuntimeError('canonical destructible kind is invalid')
        try:
            chunk_id = int(event['chunk_id'])
            item_index = int(event['item_index'])
            x = float(event['x'])
            y = float(event['y'])
            z = float(event['z'])
            fall_yaw = float(event.get('fall_yaw', 0.0))
            speed = float(event.get('speed', 0.0))
        except (KeyError, TypeError, ValueError, OverflowError):
            raise RuntimeError('canonical destructible payload is invalid')
        for value in (x, y, z, fall_yaw, speed):
            if math.isnan(value) or math.isinf(value):
                raise RuntimeError(
                    'canonical destructible payload is non-finite')
        mat_kind = event.get('mat_kind')
        if mat_kind is not None:
            try:
                mat_kind = int(mat_kind)
            except (TypeError, ValueError, OverflowError):
                raise RuntimeError(
                    'canonical destructible material is invalid')
        if kind == 'module' and mat_kind is None:
            raise RuntimeError(
                'canonical destructible module has no material')
        is_shot = event.get('is_shot')
        if not isinstance(is_shot, bool):
            raise RuntimeError(
                'canonical destructible shot flag is invalid')
        is_isolated = getattr(
            self._destructibles, 'is_isolated_1513', None)
        if callable(is_isolated) and is_isolated(chunk_id, item_index):
            self._clear_local_destructible_prediction(
                ((chunk_id, item_index, mat_kind),))
            # Runtime validation already logged and quarantined this native
            # identity. Never re-enter it through canonical event replay.
            return False
        self._clear_local_destructible_prediction(
            ((chunk_id, item_index, mat_kind),))
        validate_tree = getattr(
            self._destructibles, 'validate_tree_identity_1513', None)
        if (kind == 'tree' and callable(validate_tree) and
                not validate_tree(
                    self._avatar.spaceID, chunk_id, item_index)):
            # The native object remains solid.  A nullable tree identity is a
            # local streamed-data boundary, not a fatal LAN protocol failure.
            return False
        already_destroyed = destructibles_authority.is_destroyed(
            chunk_id, item_index, mat_kind)
        position = self._vector((x, y, z))
        space_id = self._avatar.spaceID
        applied = False
        if not already_destroyed:
            if kind == 'tree':
                applied = destructibles_authority.destroy_tree(
                    space_id, chunk_id, item_index,
                    fall_yaw, speed, position)
            elif kind == 'column':
                applied = destructibles_authority.destroy_column(
                    space_id, chunk_id, item_index,
                    fall_yaw, speed, position)
            elif kind == 'fragile':
                applied = destructibles_authority.destroy_fragile(
                    space_id, chunk_id, item_index, position, is_shot)
            else:
                applied = destructibles_authority.destroy_module(
                    space_id, chunk_id, item_index,
                    mat_kind, position, is_shot)
            if (not applied and not destructibles_authority.is_destroyed(
                    chunk_id, item_index, mat_kind)):
                if (kind == 'tree' and callable(validate_tree) and
                        not validate_tree(
                            space_id, chunk_id, item_index)):
                    return False
                raise RuntimeError(
                    '#1513 failed to apply canonical destructible event')
        foliage_changed = False
        if kind == 'tree':
            foliage_changed = self._activate_fallen_tree_foliage(
                chunk_id, item_index)
        if already_destroyed:
            return foliage_changed
        note_destroyed = getattr(
            self._destructibles, 'note_destroyed', None)
        if callable(note_destroyed):
            note_destroyed(
                kind, chunk_id, item_index, mat_kind, self._clock())
        return True

    def _apply_vehicle_statistics_event(self, event):
        actor_kind = event.get('actor_kind')
        try:
            actor_id = int(event.get('actor_id'))
        except (TypeError, ValueError):
            return False
        record = self._records.get('%s:%s' % (actor_kind, actor_id))
        if record is None:
            return False
        state = dict(record.get('state') or {})
        state['frags'] = int(event.get('frags', state.get('frags', 0)))
        state['team_killer'] = bool(event.get(
            'team_killer', state.get('team_killer', False)))
        record['state'] = state
        return self._apply_vehicle_statistics(record, state)

    def _record_position(self, record):
        if record.get('local'):
            return tuple(self._local_position)
        state = record.get('state') or {}
        if all(name in state for name in ('x', 'y', 'z')):
            return (_number(state['x']), _number(state['y']),
                    _number(state['z']))
        entity = self._server_entity(record['engine_id'])
        if entity is None:
            raise RuntimeError('combat presentation entity is unavailable')
        return _xyz(entity.position)

    def _event_shell(self, attacker_record, event):
        entity = self._server_entity(attacker_record['engine_id'])
        if entity is None or entity.typeDescriptor is None:
            raise RuntimeError('combat attacker descriptor is unavailable')
        gun = _field(entity.typeDescriptor, 'gun', {})
        shots = tuple(_field(gun, 'shots', ()) or ())
        if not shots:
            raise RuntimeError('combat attacker has no shell descriptors')
        index = max(0, min(
            int(event.get('shell_index', 0) or 0), len(shots) - 1))
        shot = shots[index]
        shell = _field(shot, 'shell', None)
        if shell is None:
            raise RuntimeError('combat attacker shell is unavailable')
        return shot, shell

    @staticmethod
    def _critical_hit_mask(critical):
        """Pack #1513's device/destroyed/crew hit-direction bit fields."""
        devices = {
            'engineHealth': 0, 'ammoBayHealth': 1,
            'fuelTankHealth': 2, 'radioHealth': 3,
            'leftTrackHealth': 4, 'rightTrackHealth': 4,
            'gunHealth': 5, 'turretRotatorHealth': 6,
            'surveyingDeviceHealth': 7,
        }
        crew = {
            'commander': 0, 'driver': 1, 'radioman': 2,
            'gunner': 3, 'loader': 4,
        }
        result = 0
        for critical_event in (critical or {}).get('events') or ():
            if critical_event.get('cause', 'shot') != 'shot':
                continue
            kind = critical_event.get('kind')
            state = critical_event.get('state')
            name = str(critical_event.get('name', ''))
            if kind == 'device' and name in devices:
                if state == 'critical':
                    result |= 1 << devices[name]
                elif state == 'destroyed':
                    result |= 1 << (12 + devices[name])
            elif kind == 'crew' and state == 'destroyed':
                role = name.rstrip('0123456789')
                if role in crew:
                    result |= 1 << (24 + crew[role])
        return result

    def _present_damage_sticker(self, event, target_record):
        """Add one server-admitted direct hit to the stock sticker owner."""
        if (self._worker_mode or 'damage_sticker' not in event or
                event.get('splash', False) or
                not self._optional_feature_enabled(
                    'projectile damage stickers')):
            return False
        target = self._server_entity(target_record.get('engine_id'))
        if (target is None or not getattr(target, 'isStarted', False) or
                getattr(target, 'typeDescriptor', None) is None):
            return False
        try:
            from VehicleEffects import DamageFromShotDecoder
            code = event['damage_sticker']
            component_name, sticker_id, segment_start, segment_end = \
                DamageFromShotDecoder.decodeSegment(
                    code, target.typeDescriptor)
            add_sticker = getattr(
                getattr(target, 'appearance', None),
                'addDamageSticker', None)
            if (not component_name or segment_start is None or
                    segment_end is None or segment_start == segment_end or
                    not callable(add_sticker)):
                raise RuntimeError(
                    '#1513 damage-sticker boundary is unavailable')
            add_sticker(
                code, component_name, sticker_id,
                segment_start, segment_end)
        except Exception as error:
            self._warn_optional_failure(
                'projectile damage stickers', error, disable=False)
            return False
        return True

    def _present_combat_hit(self, event, target_record, attacker_record,
                            attacker_id):
        """Port the mature 0.8.2 hit feedback through exact #1513 APIs."""
        if self._worker_mode:
            return False
        if (self._combat_event_source(event) != 'shot' or
                event.get('kind') not in (
                    'hit', 'bot_hit', 'bot_human_hit', 'bot_bot_hit')):
            return False
        if not event.get('world_pose'):
            raise RuntimeError('shot hit event has no world impact pose')
        shot, shell = self._event_shell(attacker_record, event)
        attacker_position = self._record_position(attacker_record)
        target_position = self._record_position(target_record)
        impact_position = (
            _number(event.get('x')), _number(event.get('y')),
            _number(event.get('z')))
        direction_origin = (
            impact_position if event.get('splash', False)
            else attacker_position)
        direction = self._vector((
            target_position[0] - direction_origin[0],
            target_position[1] - direction_origin[1],
            target_position[2] - direction_origin[2]))
        if direction.length <= 0.001:
            # A legal HE blast can damage its own shooter.  Entity centres are
            # then identical, and an explosion exactly at the centre has no
            # unique horizontal hit direction.  Keep the canonical damage and
            # use a harmless presentation normal instead of aborting the
            # already-completed battle while its event journal drains.
            direction = self._vector((0.0, 1.0, 0.0))
        direction.normalise()
        damage = max(0, int(event.get('damage', 0) or 0))
        shot_result = max(0, min(int(event.get('shot_result', 2)), 2))
        same_vehicle = (
            attacker_record is target_record or
            (attacker_record.get('kind'), attacker_record.get('network_id')) ==
            (target_record.get('kind'), target_record.get('network_id')))
        if target_record.get('local') and not same_vehicle:
            # Preserve the empirically-correct 0.8.2 UI convention: the hit
            # indicator points from the player back toward the attacker.
            hit_yaw = math.atan2(
                -(attacker_position[0] - target_position[0]),
                -(attacker_position[2] - target_position[2]))
            self._avatar.showOwnVehicleHitDirection(
                hit_yaw, int(attacker_id or 0), damage,
                self._critical_hit_mask(event.get('critical')),
                damage <= 0, combat_rules.is_he(shot),
                int(target_record['engine_id']))

        # An armour effect belongs to the visible world model.  Team/radio
        # knowledge may retain a minimap marker beyond the 565 m world AOI,
        # but it must not flash an effect at the hidden model's position.
        if (not target_record.get('local') and
                not bool(target_record.get('spot_visible', True))):
            return False
        if not self._projectile_cosmetic_allowed(
                event, self._projectile_visual_meta):
            return False

        effects_index = _field(shell, 'effectsIndex', None)
        if effects_index is None:
            raise RuntimeError('combat shell effects index is unavailable')
        effects_descr = self._runtime.vehicles.g_cache.shotEffects[
            effects_index]
        # Retail presents an HE near-miss through
        # Vehicle.showDamageFromExplosion/armorSplashHit.  Direct hits keep
        # the three protocol outcomes: ricochet, resisted and pierced.
        effect_group = ('armorSplashHit' if event.get('splash', False) else
                        ('armorRicochet', 'armorResisted', 'armorHit')[
                            shot_result])
        stages, effects, unused = effects_descr[effect_group]
        hit_position = self._vector(impact_position)
        terrain_effects = getattr(self._avatar, 'terrainEffects', None)
        add_effect = getattr(terrain_effects, 'addNew', None)
        if not callable(add_effect):
            raise RuntimeError('#1513 terrain hit-effects boundary is unavailable')
        if not self._optional_feature_enabled(
                'projectile impact presentation'):
            return False
        self._report_effect(
            'armour_hit', effect_group, effects_index,
            (_number(event.get('x')), _number(event.get('y')),
             _number(event.get('z'))), direction)
        try:
            add_effect(
                hit_position, effects, stages, None, dir=direction,
                start=hit_position - direction.scale(0.4),
                end=hit_position + direction.scale(0.4),
                showShockWave=bool(target_record.get('local')),
                showFlashBang=bool(target_record.get('local')))
        except Exception as error:
            self._warn_optional_failure(
                'projectile impact presentation', error)
            return False
        return True

    _DECAL_REPORT_LIMIT = 32

    def _install_decal_probe(self):
        """Log the first ground decals this round paints, and who painted them.

        A large black wedge has been seen on open terrain in three battles.
        Every other candidate is ruled out, so this names the exact caller and
        corners of any decal that is too large or degenerate.
        """
        bigworld = self._runtime.bigworld
        original = getattr(bigworld, 'wg_addDecal', None)
        if not callable(original) or self._decal_probe is not None:
            return False
        reports = [0]

        def wg_addDecal(group, start, end, size, yaw, *textures):
            if reports[0] < self._DECAL_REPORT_LIMIT:
                reports[0] += 1
                try:
                    frame = sys._getframe(1)
                    caller = '%s:%d' % (frame.f_code.co_filename,
                                        frame.f_lineno)
                except Exception:
                    caller = 'unknown'
                sys.stdout.write(
                    '[Offline LAN 0.9.22] DECAL group=%s start=%s end=%s '
                    'size=%s yaw=%s from=%s\n' % (
                        group, tuple(start), tuple(end), tuple(size), yaw,
                        caller))
            return original(group, start, end, size, yaw, *textures)

        bigworld.wg_addDecal = wg_addDecal
        self._decal_probe = (original, wg_addDecal)
        return True

    def _remove_decal_probe(self):
        probe = self._decal_probe
        self._decal_probe = None
        if probe is None:
            return False
        bigworld = self._runtime.bigworld
        if getattr(bigworld, 'wg_addDecal', None) is probe[1]:
            bigworld.wg_addDecal = probe[0]
        return True

    _EFFECT_REPORT_LIMIT = 12

    def _report_effect(self, kind, material, effects_index, where, direction):
        """Log the first few visual effects a round plays, then stop.

        A black wedge over the terrain has been seen twice; a mis-specified
        effect material or a bad transform is the leading candidate.
        """
        if self._effect_reports >= self._EFFECT_REPORT_LIMIT:
            return False
        self._effect_reports += 1
        sys.stdout.write(
            '[Offline LAN 0.9.22] EFFECT %s material=%r index=%r at=%s '
            'dir=%s\n' % (
                kind, material, effects_index,
                _format_xyz(where), _format_xyz(direction)))
        return True

    @staticmethod
    def _combat_record_team(record):
        state = record.get('state') or {}
        if 'team' not in state:
            raise RuntimeError('combat feedback record has no team')
        try:
            team = int(state['team'])
        except (TypeError, ValueError):
            raise RuntimeError('combat feedback record has invalid team')
        if team <= 0:
            raise RuntimeError('combat feedback record has invalid team')
        return team

    @staticmethod
    def _combat_target_is_spotted(record):
        """Return whether team knowledge may disclose combat feedback.

        ``spot_visible`` is only the 565 m world-model gate.  A radio-spotted
        target outside that AOI still has a marker and must keep ordinary hit
        feedback, so the marker/team-knowledge gate owns this decision.
        """
        return bool(record.get(
            'spot_marker_visible', record.get('spot_visible', True)))

    def _is_blind_local_attack(self, target_record, attacker_record):
        """Keep a local hit on an unspotted enemy presentation-silent."""
        if (not attacker_record.get('local') or target_record.get('local') or
                self._combat_record_team(target_record) ==
                self._combat_record_team(attacker_record)):
            return False
        return not self._combat_target_is_spotted(target_record)

    @staticmethod
    def _combat_event_source(event):
        if 'source' not in event:
            raise RuntimeError('ordered combat event has no source')
        source = event['source']
        if source not in (
                'shot', 'fire', 'ram', 'client_simulation',
                'player_left', 'environment'):
            raise RuntimeError(
                'ordered combat event has invalid source: %s' % source)
        return source

    def _combat_attack_reason(self, event):
        source = self._combat_event_source(event)
        if source == 'player_left':
            if ('attack_reason' not in event or
                    event['attack_reason'] is not None):
                raise RuntimeError(
                    'player_left event must have null attack_reason')
            if ('death_reason' not in event or
                    event['death_reason'] != 0):
                raise RuntimeError(
                    'player_left event must have zero death_reason')
            if ('attacker' in event or 'attacker_bot' in event):
                raise RuntimeError(
                    'player_left event must not have an attacker')
            return None
        if 'attack_reason' not in event:
            raise RuntimeError('ordered combat event has no attack_reason')
        try:
            reason_id = int(event['attack_reason'])
        except (TypeError, ValueError):
            raise RuntimeError('ordered combat event has invalid attack_reason')
        if reason_id < 0:
            raise RuntimeError('ordered combat event has invalid attack_reason')
        expected = {
            'shot': self._attack_reason('SHOT', 0),
            'fire': self._attack_reason('FIRE', 1),
            'ram': self._attack_reason('RAM', 2),
        }
        if source == 'client_simulation':
            return reason_id
        if source == 'environment':
            dead = bool(event.get('dead', False))
            world_collision = self._attack_reason('WORLD_COLLISION', 3)
            drowning = self._attack_reason('DROWNING', 5)
            overturn = self._attack_reason('OVERTURN', 7)
            valid = (
                (reason_id == world_collision and
                 event.get('death_reason') ==
                 (world_collision if dead else 0)) or
                (reason_id in (drowning, overturn) and dead and
                 event.get('death_reason') == reason_id))
            if not valid:
                raise RuntimeError(
                    'environment event has invalid cause')
            return reason_id
        if reason_id != expected[source]:
            raise RuntimeError(
                'ordered combat event attack_reason does not match source: '
                '%s != %s' % (reason_id, expected[source]))
        return reason_id

    def _validate_combat_event_contract(self, event):
        source = self._combat_event_source(event)
        attack_reason = self._combat_attack_reason(event)
        kind = event.get('kind')
        valid_kinds = {
            'shot': ('hit', 'bot_hit', 'bot_human_hit', 'bot_bot_hit'),
            'fire': ('hit', 'bot_hit', 'bot_human_hit', 'bot_bot_hit'),
            'ram': ('hit', 'bot_hit', 'bot_human_hit', 'bot_bot_hit'),
            'client_simulation': ('health',),
            'player_left': ('health',),
            'environment': ('health',),
        }
        if kind not in valid_kinds[source]:
            raise RuntimeError(
                'ordered combat event source %s does not allow kind %s' %
                (source, kind))
        blocked_damage = event.get('blocked_damage', 0)
        if (isinstance(blocked_damage, bool) or
                not isinstance(blocked_damage, _INTEGER_TYPES) or
                not 0 <= blocked_damage <= 5000):
            raise RuntimeError(
                'ordered combat event has invalid blocked_damage')
        if (blocked_damage and
                (source != 'shot' or bool(event.get('splash', False)) or
                 int(event.get('shot_result', 2)) == 2)):
            raise RuntimeError(
                'ordered combat event has inconsistent blocked_damage')
        if 'damage_sticker' in event:
            damage_sticker = event.get('damage_sticker')
            if (source != 'shot' or bool(event.get('splash', False)) or
                    isinstance(damage_sticker, bool) or
                    not isinstance(damage_sticker, _INTEGER_TYPES) or
                    not 0 <= damage_sticker <=
                    lan_protocol.MAX_PROJECTILE_DAMAGE_STICKER):
                raise RuntimeError(
                    'ordered combat event has invalid damage_sticker')
        attacker_key = self._event_entity_key(event, 'attacker')
        if source in ('shot', 'fire', 'ram') and attacker_key is None:
            raise RuntimeError(
                'ordered %s combat event has no attacker' % source)
        if source in ('client_simulation', 'player_left', 'environment') and \
                attacker_key is not None:
            raise RuntimeError(
                'ordered %s combat event must not have an attacker' % source)
        return source, attack_reason

    def _present_combat_feedback(self, event, target_record,
                                 attacker_record, reason_id=None):
        """Feed accepted server combat through stock #1513 feedback RPCs."""
        if self._worker_mode:
            return False
        if self._is_blind_local_attack(target_record, attacker_record):
            # showShotResults owns commander hit voices; onBattleEvents owns
            # damage/critical/kill ribbons and counters.  Neither may disclose
            # an enemy which this team has not spotted.
            return False
        feedback_common = getattr(
            self._runtime, 'battle_feedback_common', None)
        event_types = getattr(feedback_common, 'BATTLE_EVENT_TYPE', None)
        if event_types is None:
            raise RuntimeError('#1513 battle feedback constants are unavailable')
        damage = max(0, int(event.get('damage', 0) or 0))
        if reason_id is None:
            reason_id = self._combat_attack_reason(event)
        critical = event.get('critical')
        critical_count = len((critical or {}).get('events') or ())
        if attacker_record.get('local'):
            self._assert_player_identity(attacker_record['engine_id'])
        target_team = self._combat_record_team(target_record)
        attacker_team = self._combat_record_team(attacker_record)
        enemy = target_team != attacker_team
        if attacker_record.get('local') and enemy:
            assert_damage_type = getattr(
                self._runtime.compatibility,
                'assert_vehicle_marker_damage_type', None)
            if not callable(assert_damage_type):
                raise RuntimeError(
                    '#1513 vehicle-marker damage boundary is unavailable')
            assert_damage_type(
                self._avatar, int(attacker_record['engine_id']))
        output = []
        if attacker_record.get('local') and enemy:
            target_id = int(target_record['engine_id'])
            if damage > 0:
                output.append({
                    'eventType': int(event_types.DAMAGE),
                    'targetID': target_id, 'count': 1,
                    'details': int(event_types.packDamage(
                        damage, reason_id))})
            if critical_count > 0:
                output.append({
                    'eventType': int(event_types.CRIT),
                    'targetID': target_id, 'count': 1,
                    'details': int(event_types.packCrits(
                        critical_count, reason_id))})
            if bool(event.get('dead')):
                output.append({
                    'eventType': int(event_types.KILL),
                    'targetID': target_id, 'count': 1, 'details': 0})
        if attacker_record.get('local'):
            target_id = int(target_record['engine_id'])
            if (self._combat_event_source(event) == 'shot' and
                    event.get('kind') in (
                        'hit', 'bot_hit', 'bot_human_hit',
                        'bot_bot_hit')):
                flags_type = getattr(
                    self._runtime.constants, 'VEHICLE_HIT_FLAGS', None)
                if flags_type is None:
                    raise RuntimeError(
                        '#1513 VEHICLE_HIT_FLAGS are unavailable')
                explosion = bool(event.get('splash'))
                if explosion:
                    flags = int(flags_type.ATTACK_IS_EXTERNAL_EXPLOSION)
                    flags |= int(
                        flags_type.
                        MATERIAL_WITH_POSITIVE_DF_PIERCED_BY_EXPLOSION)
                    critical_cause = 'explosion'
                    device_flag = int(
                        flags_type.DEVICE_DAMAGED_BY_EXPLOSION)
                    chassis_flag = int(
                        flags_type.CHASSIS_DAMAGED_BY_EXPLOSION)
                    gun_flag = int(
                        flags_type.GUN_DAMAGED_BY_EXPLOSION)
                else:
                    flags = int(flags_type.ATTACK_IS_DIRECT_PROJECTILE)
                    shot_result = max(
                        0, min(int(event.get('shot_result', 2)), 2))
                    if shot_result == 2:
                        flags |= int(
                            flags_type.
                            MATERIAL_WITH_POSITIVE_DF_PIERCED_BY_PROJECTILE)
                    elif shot_result == 1:
                        flags |= int(
                            flags_type.
                            MATERIAL_WITH_POSITIVE_DF_NOT_PIERCED_BY_PROJECTILE)
                    else:
                        flags |= int(flags_type.RICOCHET)
                    critical_cause = 'shot'
                    device_flag = int(
                        flags_type.DEVICE_DAMAGED_BY_PROJECTILE)
                    chassis_flag = int(
                        flags_type.CHASSIS_DAMAGED_BY_PROJECTILE)
                    gun_flag = int(
                        flags_type.GUN_DAMAGED_BY_PROJECTILE)
                for critical_event in (
                        (critical or {}).get('events') or ()):
                    if critical_event.get(
                            'cause', 'shot') != critical_cause:
                        continue
                    kind = critical_event.get('kind')
                    state = critical_event.get('state')
                    if kind == 'fire' and bool(state):
                        flags |= int(flags_type.FIRE_STARTED)
                    elif (kind == 'device' and
                          state in ('critical', 'destroyed')):
                        name = str(critical_event.get('name', ''))
                        flags |= device_flag
                        if name in ('leftTrackHealth', 'rightTrackHealth'):
                            flags |= chassis_flag
                        elif name == 'gunHealth':
                            flags |= gun_flag
                if bool(event.get('dead')):
                    flags |= int(flags_type.VEHICLE_KILLED)
                callback = getattr(self._avatar, 'showShotResults', None)
                if not callable(callback):
                    raise RuntimeError(
                        '#1513 shot-result feedback boundary is unavailable')
                callback([(flags << 32) | target_id])
        if target_record.get('local') and enemy:
            attacker_id = int(attacker_record['engine_id'])
            blocked_damage = max(
                0, int(event.get('blocked_damage', 0) or 0))
            if blocked_damage > 0:
                output.append({
                    'eventType': int(event_types.TANKING),
                    'targetID': attacker_id, 'count': 1,
                    'details': int(event_types.packDamage(
                        blocked_damage, reason_id))})
            if damage > 0:
                output.append({
                    'eventType': int(event_types.RECEIVED_DAMAGE),
                    'targetID': attacker_id, 'count': 1,
                    'details': int(event_types.packDamage(
                        damage, reason_id))})
            if critical_count > 0:
                output.append({
                    'eventType': int(event_types.RECEIVED_CRIT),
                    'targetID': attacker_id, 'count': 1,
                    'details': int(event_types.packCrits(
                        critical_count, reason_id))})
        if output:
            callback = getattr(self._avatar, 'onBattleEvents', None)
            if not callable(callback):
                raise RuntimeError(
                    '#1513 battle-event feedback boundary is unavailable')
            callback(output)
        return bool(output)

    def _apply_combat_event(self, event, update_state=True):
        source, attack_reason = self._validate_combat_event_contract(event)
        target_key = self._event_entity_key(event, 'target')
        if target_key is None:
            raise RuntimeError('ordered combat event has no target')
        record = self._records.get(target_key)
        if record is None:
            raise RuntimeError(
                'ordered combat event target is unavailable: %s' %
                target_key)
        latest_state = record.get('state') or {}
        state = self._combat_event_state(event, latest_state, target_key)
        if update_state:
            record['state'] = state
        attacker = event.get('attacker_bot')
        attacker_kind = 'bot'
        if attacker is None:
            attacker = event.get('attacker')
            attacker_kind = 'player'
        attacker_record = self._records.get(
            '%s:%s' % (attacker_kind, attacker))
        if attacker is not None and attacker_record is None:
            if not self._missing_projectile_attacker_allowed(event):
                raise RuntimeError(
                    'ordered combat event attacker is unavailable: %s:%s' %
                    (attacker_kind, attacker))
        attacker_id = (attacker_record.get('engine_id')
                       if attacker_record is not None else 0)
        entity = self._server_entity(record['engine_id'])
        if entity is None:
            raise RuntimeError(
                'ordered combat event target has no native entity: %s' %
                target_key)
        if (attacker_record is not None and
                self._server_entity(attacker_record['engine_id']) is None):
            raise RuntimeError(
                'ordered combat event attacker has no native entity: %s:%s' %
                (attacker_kind, attacker))
        self._present_damage_sticker(event, record)
        blind_local_attack = bool(
            attacker_record is not None and
            self._is_blind_local_attack(record, attacker_record))
        dead_target = (
            _number(state.get('health', 0.0)) <= 0.0 or
            not bool(state.get('alive', True)))
        if (entity is not None and attacker is not None and
                not record.get('local')):
            entity.last_killer_id = int(attacker_id or 0)
        if record.get('local') and attacker is not None:
            self._local_last_attacker = (attacker_kind, int(attacker))
        if source == 'player_left' and attacker_record is not None:
            raise RuntimeError('player_left event has an attacker')
        if attacker_record is not None:
            self._present_combat_hit(
                event, record, attacker_record, attacker_id)
            self._present_combat_feedback(
                event, record, attacker_record, attack_reason)
        critical = event.get('critical')
        if isinstance(critical, dict):
            canonical = self._critical_state(critical)
            should_apply = self._reconcile_critical_authority(
                record, event, canonical)
            if (entity is not None and should_apply and
                    canonical != record.get('critical_state')):
                events = critical_damage.apply_payload(entity, critical)
                state['critical'] = canonical
                record['critical_state'] = canonical
                self._present_critical(record, events, attacker_id)
        if 'display_health' in event:
            state['display_health'] = max(
                0, int(event.get('display_health', state['health'])))
        death_reason = int(event['death_reason'])
        force_health_cause = (
            max(0, int(event.get('damage', 0) or 0)) > 0 or
            bool(event.get('dead', False)))
        self._apply_health(
            record, state, attacker_id, death_reason,
            force_cause=force_health_cause,
            attack_reason_id=(0 if attack_reason is None else attack_reason),
            suppress_combat_presentation=(
                blind_local_attack and not dead_target))
        if not update_state:
            record['state'] = latest_state
        return True

    @staticmethod
    def _critical_state(payload):
        if not isinstance(payload, dict):
            return None
        result = dict(payload)
        result['events'] = []
        return result

    @staticmethod
    def _critical_proposal_contract(record, critical, hull_damage,
                                    critical_delta):
        """Bind a firing-client proposal to the last canonical target state."""
        if not isinstance(critical, dict):
            return {}
        if not isinstance(critical_delta, dict):
            raise RuntimeError('#1513 critical proposal has no damage delta')
        state = record.get('state') or {}
        if record.get('kind') == 'bot':
            base_name = 'combat_base_revision'
            ack_name = 'combat_ack_seq'
        elif record.get('kind') == 'player':
            base_name = 'critical_base_revision'
            ack_name = 'critical_ack_seq'
        else:
            raise RuntimeError('critical target kind is invalid')
        values = {}
        for wire_name, state_name in (
                ('critical_target_base_revision', base_name),
                ('critical_target_ack_seq', ack_name)):
            raw = state.get(state_name)
            try:
                parsed = int(raw)
                exact = float(raw) == parsed
            except (TypeError, ValueError, OverflowError):
                exact = False
                parsed = -1
            if isinstance(raw, bool) or not exact or parsed < 0:
                raise RuntimeError(
                    '#1513 critical target has no exact %s' % state_name)
            values[wire_name] = parsed
        values['hull_damage'] = hull_damage
        values['critical_delta'] = critical_delta
        return values

    def _reconcile_critical_authority(self, record, source, canonical=None):
        if record.get('kind') != 'player':
            return True
        required = ('critical_revision', 'critical_base_revision',
                    'critical_ack_seq')
        if not all(name in source for name in required):
            raise RuntimeError(
                '#1513 player critical state has no revision contract')
        revision = max(0, int(source['critical_revision']))
        base_revision = max(0, int(source['critical_base_revision']))
        ack_seq = max(0, int(source['critical_ack_seq']))
        previous_revision = int(record.get('critical_revision', -1))
        if revision < previous_revision:
            return False
        record['critical_revision'] = revision
        record['critical_base_revision'] = base_revision
        record['critical_ack_seq'] = ack_seq
        if record.get('local'):
            pending = self._local_damage_report
            acknowledged = self.acknowledge_local_damage_report(
                base_revision, ack_seq, revision)
            if (acknowledged and self._local_critical_owned and
                    isinstance(pending, dict) and
                    isinstance(canonical, dict)):
                pending_tracks = set(
                    str(row.get('name'))
                    for row in pending.get('tracks') or ()
                    if isinstance(row, dict))
                canonical_states = {
                    str(row.get('name')): str(row.get('state'))
                    for row in canonical.get('devices') or ()
                    if isinstance(row, dict)}
                destroyed = set(
                    str(name) for name in canonical.get('destroyed') or ())
                if (pending_tracks and all(
                        canonical_states.get(name) in ('normal', 'critical')
                        and name not in destroyed
                        for name in pending_tracks)):
                    self._local_critical_owned = False
            if (self._local_critical_owned and
                    base_revision == self._local_critical_base_revision):
                return False
        return revision > previous_revision

    def _apply_critical_state(self, record, payload, authority=None):
        canonical = self._critical_state(payload)
        if canonical is None:
            return False
        if authority is not None:
            should_apply = self._reconcile_critical_authority(
                record, authority, canonical)
            if not should_apply:
                if record.get('local') and record.get('critical_state'):
                    state = dict(record.get('state') or {})
                    state['critical'] = record['critical_state']
                    record['state'] = state
                return False
        if canonical == record.get('critical_state'):
            return False
        entity = self._server_entity(record['engine_id'])
        if entity is None:
            return False
        events = critical_damage.apply_payload(entity, canonical)
        record['critical_state'] = canonical
        state = dict(record.get('state') or {})
        state['critical'] = canonical
        record['state'] = state
        if not self._worker_mode:
            self._present_critical(record, events, 0)
        return True

    def _apply_vehicle_statistics(self, record, state):
        """Feed server-owned frags/team-killer state to stock ClientArena."""
        if self._worker_mode:
            return False
        try:
            frags = int(state.get('frags', 0))
        except (TypeError, ValueError):
            frags = 0
        changed = False
        if record.get('presented_frags') != frags:
            self._binding.arena_vehicle_statistics(
                record['engine_id'], frags)
            record['presented_frags'] = frags
            changed = True
        team_killer = bool(state.get('team_killer', False))
        if team_killer and not record.get('presented_team_killer'):
            self._binding.arena_team_killer(record['engine_id'])
            record['presented_team_killer'] = True
            changed = True
        return changed

    def _death_attacker_engine_id(self, state):
        """Resolve the durable server killer before a death snapshot wins."""
        kind = state.get('death_attacker_kind')
        try:
            network_id = int(state.get('death_attacker_id', 0))
        except (TypeError, ValueError):
            return 0
        record = self._records.get('%s:%s' % (kind, network_id))
        return int(record.get('engine_id', 0)) if record is not None else 0

    @staticmethod
    def _critical_extra_index(descriptor, name):
        """Resolve the exact descriptor extra index used by #1513 Avatar."""
        extra_name = str(name)
        if not extra_name.endswith('Health'):
            extra_name += 'Health'
        selected = None
        extras_dict = getattr(descriptor, 'extrasDict', None)
        if extras_dict is not None:
            selected = extras_dict.get(extra_name)
        extras = getattr(descriptor, 'extras', None)
        if hasattr(extras, 'items'):
            iterator = extras.items()
        else:
            iterator = enumerate(extras or ())
        for index, extra in iterator:
            if (extra is selected or
                    str(getattr(extra, 'name', '')) == extra_name):
                return int(index)
        selected_index = int(getattr(selected, 'index', 0) or 0)
        if selected_index <= 0:
            raise RuntimeError(
                '#1513 descriptor has no critical extra: %s' % extra_name)
        return selected_index

    def _sync_fire_effect(self, entity, burning=None):
        """Match the stock #1513 fire extra to the copied burning state."""
        descriptor = getattr(entity, 'typeDescriptor', None)
        extras = getattr(descriptor, 'extrasDict', None)
        extra = extras.get('fire') if extras is not None else None
        if extra is None:
            return False
        if burning is None:
            burning = getattr(entity, 'is_on_fire', False)
        burning = bool(burning)
        if burning == bool(extra.isRunningFor(entity)):
            return False
        if not burning:
            extra.stopFor(entity)
            return True
        appearance = getattr(entity, 'appearance', None)
        if getattr(appearance, 'compoundModel', None) is None:
            return False
        extra.startFor(entity)
        return True

    def _present_critical(self, record, events, attacker_id):
        """Map copied state transitions to audited stock #1513 UI callbacks."""
        if (self._worker_mode or
                (not record.get('local') and
                 not bool(record.get('spot_visible', True)))):
            return False
        entity = self._server_entity(record['engine_id'])
        if entity is None or entity.typeDescriptor is None:
            return False
        self._sync_fire_effect(entity)
        if not events:
            return False
        shown = False
        for event in events:
            if (event.get('kind') == 'ammo_rack' and
                    event.get('state') == 'destroyed'):
                callback = getattr(entity, 'showAmmoBayEffect', None)
                if not callable(callback):
                    raise RuntimeError(
                        '#1513 ammo-bay effect boundary is unavailable')
                modes = getattr(
                    self._runtime.constants, 'AMMOBAY_DESTRUCTION_MODE', None)
                if modes is None or not hasattr(modes, 'HE_DETONATION'):
                    raise RuntimeError(
                        '#1513 ammo-bay destruction mode is unavailable')
                callback(int(modes.HE_DETONATION), 0.0, 0.0)
                shown = True
        if (not record.get('local') or self._avatar is None or
                not self._damage_info_is_serviceable()):
            return shown
        indices = getattr(self._runtime.constants,
                          'DAMAGE_INFO_INDICES', {})
        suffixes = {
            'fire': '_AT_FIRE',
            'ramming': '_AT_RAMMING',
            'world_collision': '_AT_WORLD_COLLISION',
            'drowning': '_AT_DROWNING',
        }
        for event in events:
            kind = event.get('kind')
            state = event.get('state')
            cause = event.get('cause', 'shot')
            extra_index = 0
            if kind == 'device':
                extra_index = self._critical_extra_index(
                    entity.typeDescriptor, event.get('name'))
                if extra_index <= 0:
                    raise RuntimeError(
                        '#1513 device critical extra is invalid')
                if cause == 'repair':
                    code = ('DEVICE_REPAIRED' if state == 'normal' else
                            'DEVICE_REPAIRED_TO_CRITICAL')
                else:
                    base = ('DEVICE_DESTROYED' if state == 'destroyed' else
                            'DEVICE_CRITICAL')
                    code = base + suffixes.get(cause, '_AT_SHOT')
            elif kind == 'crew':
                extra_index = self._critical_extra_index(
                    entity.typeDescriptor, event.get('name'))
                if extra_index <= 0:
                    raise RuntimeError(
                        '#1513 crew critical extra is invalid')
                if state == 'normal':
                    code = 'TANKMAN_RESTORED'
                elif cause in ('world_collision', 'drowning'):
                    code = 'TANKMAN_HIT' + suffixes[cause]
                elif cause == 'fire':
                    code = 'TANKMAN_HIT'
                else:
                    code = 'TANKMAN_HIT_AT_SHOT'
            elif kind == 'fire':
                if bool(state):
                    code = ('DEVICE_STARTED_FIRE_AT_RAMMING'
                            if cause == 'ramming' else
                            'DEVICE_STARTED_FIRE_AT_SHOT')
                else:
                    code = 'FIRE_STOPPED'
            elif kind == 'ammo_rack':
                continue
            else:
                continue
            damage_index = indices.get(code)
            if damage_index is None:
                raise RuntimeError(
                    '#1513 damage-info index is unavailable: %s' % code)
            if self._show_damage_info(
                    record['engine_id'], int(damage_index), extra_index,
                    int(attacker_id or 0)):
                shown = True
        return shown

    def _damage_info_is_serviceable(self):
        """Whether #1513 can still service a damage-info notification.

        ``PlayerAvatar.showVehicleDamageInfo`` is a server-to-client entity
        method with no guards of its own.  It dereferences the shared message
        and vehicle-state controllers, and it repaints the damage panel; both
        are gone once the session stops or the battle app is destroyed.
        """
        provider = getattr(self._avatar, 'guiSessionProvider', None)
        shared = getattr(provider, 'shared', None)
        if shared is None:
            return False
        if (getattr(shared, 'messages', None) is None or
                getattr(shared, 'vehicleState', None) is None):
            return False
        if getattr(self._avatar, 'vehicleTypeDescriptor', None) is None:
            return False
        app_loader = getattr(self._runtime, 'app_loader', None)
        get_battle_app = getattr(app_loader, 'getDefBattleApp', None)
        if callable(get_battle_app) and get_battle_app() is None:
            return False
        return True

    def _show_damage_info(self, engine_id, damage_index, extra_index,
                          attacker_id):
        """Publish one stock damage-info notification, never fatally."""
        if not self._optional_feature_enabled('damage-info presentation'):
            return False
        try:
            codes = getattr(self._runtime.constants, 'DAMAGE_INFO_CODES', ())
            extras = getattr(
                self._avatar.vehicleTypeDescriptor, 'extras', ())
            if not 0 <= damage_index < len(codes):
                raise RuntimeError(
                    '#1513 damage-info index is out of range: %d' %
                    damage_index)
            if not 0 <= extra_index < len(extras):
                raise RuntimeError(
                    '#1513 damage-info extra index is out of range: %d' %
                    extra_index)
            self._avatar.showVehicleDamageInfo(
                int(engine_id), damage_index, extra_index,
                int(attacker_id), 0)
        except Exception as error:
            # A repaint failure is presentation, not authority.  Ending the
            # round over it loses the whole battle.
            self._damage_info_failure_reported = True
            self._warn_optional_failure(
                'damage-info presentation', error)
            return False
        return True

    @staticmethod
    def _tick_local_track_repair(entity, dt, loadout):
        """Advance only the existing owner-CAS track repair checkpoint."""
        before = critical_damage._state(entity)
        devices = getattr(entity, 'devices_hp', None) or {}
        destroyed = set(getattr(entity, '_destroyed_devices', None) or ())
        critical = set(getattr(entity, '_critical_devices', None) or ())
        changed = False
        for name in ('leftTrackHealth', 'rightTrackHealth'):
            if name not in destroyed or name not in devices:
                continue
            cap = critical_damage._device_damage.device_regen_hp(
                entity.typeDescriptor, name)
            if cap is None or devices[name] >= cap:
                continue
            repaired = critical_damage._device_damage.repair_step_hp(
                devices[name], name, entity.typeDescriptor, dt,
                has_big_repairkit=bool(loadout['has_big_kit']),
                repair_factor=loadout['repair_factor'])
            if repaired <= devices[name]:
                continue
            devices[name] = repaired
            changed = True
            if repaired >= cap:
                destroyed.discard(name)
                critical.add(name)
        if not changed:
            return None
        entity.devices_hp = devices
        entity._destroyed_devices = destroyed
        entity._critical_devices = critical
        critical_damage._refresh_mobility_flags(entity)
        after = critical_damage._state(entity)
        # The owner-CAS echo is suppressed, so this local edge is the only
        # chance to restore the stock crashed-track model.
        critical_damage._sync_crashed_track(entity, before, after)
        return critical_damage._payload(
            before, after,
            entity.typeDescriptor, 'repair')

    def _tick_critical_states(self, dt):
        """Present canonical state while retaining owner-CAS track repair."""
        if dt <= 0.0:
            return
        record = self._records.get('player:%s' % self.client.player_id)
        if record is None:
            return
        entity = self._server_entity(record['engine_id'])
        if entity is None or entity.typeDescriptor is None:
            return
        if not self._record_alive(record, entity):
            return
        if not hasattr(entity, 'maxHealth'):
            entity.maxHealth = int(entity.typeDescriptor.maxHealth)
        now = self._clock()
        loadout = self._local_loadout(entity.typeDescriptor)
        payload = self._tick_local_track_repair(
            entity, dt, loadout)
        if payload is not None:
            record['critical_state'] = self._critical_state(payload)
            state = dict(record.get('state') or {})
            state['critical'] = record['critical_state']
            record['state'] = state
            # Match the mature 0.8.2 lifecycle: close the repair timer before
            # publishing the repaired-device transition.
            self._present_repair_progress(entity)
            self._present_critical(
                record, payload.get('events'), record['engine_id'])
            if (payload.get('events') or
                    now >= self._next_critical_report_time):
                self._queue_local_track_repair(payload)
                self._next_critical_report_time = (
                    now + CRITICAL_REPAIR_NETWORK_SECONDS)
        self._tick_equipment_cooldowns(now)
        self._present_repair_progress(entity)

    def _present_repair_progress(self, entity):
        status = getattr(
            getattr(self._runtime.constants, 'VEHICLE_MISC_STATUS', None),
            'DESTROYED_DEVICE_IS_REPAIRING', None)
        callback = getattr(self._avatar, 'updateVehicleMiscStatus', None)
        if status is None or not callable(callback):
            return False
        destroyed = getattr(entity, '_destroyed_devices', None) or ()
        hp_map = getattr(entity, 'devices_hp', None) or {}
        cache = getattr(entity, '_offline_lan_repair_progress', None)
        if cache is None:
            cache = {}
            entity._offline_lan_repair_progress = cache
        loadout = self._local_loadout(entity.typeDescriptor)
        for name in tuple(destroyed):
            if name in critical_damage._device_damage.NO_REPAIR_PROGRESS_DEVICES:
                continue
            cap = critical_damage._device_damage.device_regen_hp(
                entity.typeDescriptor, name)
            if not cap:
                continue
            hp = max(0.0, min(float(hp_map.get(name, 0.0)), float(cap)))
            progress = max(0, min(int(round(100.0 * hp / cap)), 100))
            if cache.get(name) == progress:
                continue
            cache[name] = progress
            extra_index = self._critical_extra_index(
                entity.typeDescriptor, name)
            if extra_index <= 0:
                continue
            seconds = critical_damage._device_damage.repair_seconds(
                name, entity.typeDescriptor,
                has_big_repairkit=bool(loadout['has_big_kit']),
                repair_factor=loadout['repair_factor'])
            seconds_left = max(0.0, seconds * (1.0 - hp / cap))
            callback(entity.id, int(status),
                     int(extra_index) | (progress << 8),
                     (seconds_left,))
        for name in tuple(cache):
            if name not in destroyed:
                extra_index = self._critical_extra_index(
                    entity.typeDescriptor, name)
                if extra_index <= 0:
                    raise RuntimeError(
                        '#1513 repaired device has no extra index: %s' %
                        name)
                callback(entity.id, int(status), int(extra_index), (0.0,))
                cache.pop(name, None)
        return True

    def _apply_battle_result(self, result):
        if not isinstance(result, dict):
            return False
        self._battle_result = dict(result)
        if self._worker_mode:
            # Stop authority simulation immediately. Native teardown waits for
            # the ordered waiting roster, but no bot/projectile work should run
            # during the server's short result-publication window.
            self._battle_live = False
            self._round_finished_notified = True
            self._report_memory('round_end')
            return True
        if (self._round_finished_notified or self._avatar is None or
                self.state != 'running'):
            return False
        finish_reason = getattr(self._runtime.constants, 'FINISH_REASON', None)
        if finish_reason is None:
            raise RuntimeError('FINISH_REASON is unavailable')
        reason_name = str(result.get('reason', '')).lower()
        if ('eliminat' in reason_name or 'exterminat' in reason_name or
                reason_name == 'team_eliminated'):
            reason = finish_reason.EXTERMINATION
        elif 'base' in reason_name or 'captur' in reason_name:
            reason = finish_reason.BASE
        elif 'timeout' in reason_name or 'time_out' in reason_name:
            reason = finish_reason.TIMEOUT
        else:
            reason = getattr(
                finish_reason, 'FAILURE', getattr(finish_reason, 'UNKNOWN', 4))
        callback = getattr(self._avatar, 'onRoundFinished', None)
        if not callable(callback):
            raise RuntimeError('Avatar.onRoundFinished is unavailable')
        base_team = max(0, min(int(result.get('base_team', 0)), 2))
        if base_team in (1, 2):
            captured = getattr(self._avatar.arena, 'onTeamBaseCaptured', None)
            if callable(captured):
                captured(base_team, 0)
        callback(max(0, min(int(result.get('winner', 0)), 2)), reason)
        self._round_finished_notified = True
        self._report_memory('round_end')
        return True

    def _apply_rules(self, rules):
        incoming = (rules or {}).get('bases') or {}
        arena = getattr(self._avatar, 'arena', None)
        callback = getattr(arena, 'onTeamBasePointsUpdate', None)
        if not self._worker_mode and not callable(callback):
            return False
        changed = False
        stored = self._rules_state.setdefault('bases', {})
        for team in (1, 2):
            raw = incoming.get(str(team), incoming.get(team, {})) or {}
            current = {
                'points': max(0, min(int(raw.get('points', 0)), 100)),
                'time_left': max(
                    0.0, _number(raw.get('time_left'))),
                'invaders': max(
                    0, int(_number(raw.get('invaders')))),
                'stopped': bool(raw.get('stopped', False)),
            }
            if stored.get(str(team)) != current:
                stored[str(team)] = current
                if not self._worker_mode:
                    callback(
                        team, 0, current['points'],
                        current['time_left'], current['invaders'],
                        current['stopped'])
                changed = True
        return changed

    def _accept_player_fire_commit(self, event, record):
        if event.get('shooter_kind') != 'player':
            return False
        try:
            player_id = int(event.get('shooter_id'))
            intent_seq = int(event.get('fire_intent_seq'))
            input_seq = int(event.get('fire_input_seq'))
            shot_seq = int(event.get('shot_seq'))
            shell_index = int(event.get('shell_index'))
        except (TypeError, ValueError, OverflowError):
            raise RuntimeError(
                'canonical player shot has no fire-intent identity')
        if self._worker_mode:
            pending = self._player_fire_launch_pending.get(player_id)
            gun = self._player_authority_guns.get(player_id)
            if (not isinstance(pending, dict) or gun is None or
                    intent_seq != int(pending.get('intent_seq', 0)) or
                    input_seq != int(pending.get('input_seq', 0)) or
                    shot_seq != int(pending.get('shot_seq', 0))):
                raise RuntimeError(
                    'canonical player shot does not acknowledge worker intent')
            entity = self._server_entity(record.get('engine_id'))
            reload_factor = (1.0 if entity is None else
                             critical_damage.stat_factor(entity, 'reload'))
            if (shell_index != gun.shot_index or
                    not gun.commit_fire(reload_factor)):
                raise RuntimeError(
                    'canonical player shot violates worker gun state')
            self._player_fire_launch_pending.pop(player_id, None)
            return True
        if not record.get('local'):
            return False
        pending = self._local_fire_intent
        if (not isinstance(pending, dict) or
                intent_seq != int(pending.get('intent_seq', 0)) or
                input_seq != int(pending.get('input_seq', 0))):
            raise RuntimeError(
                'canonical local shot does not acknowledge its trigger')
        gun = self._gun_state
        if gun is not None:
            if shell_index != gun.shot_index or not gun.commit_fire(
                    critical_damage.stat_factor(
                        self._server_entity(record['engine_id']), 'reload')):
                raise RuntimeError(
                    'canonical local shot violates presented gun state')
            if pending.get('deferred_partial_clip_reload'):
                gun.reload_partial_clip()
            self._publish_ammo_state(gun, force=True)
            self._publish_reload_event(
                gun.reload_time, gun.reload_duration, force=True)
            if self._sender is not None:
                self._sender.send_current()
        self._local_fire_intent = None
        return True

    def _admit_projectile_visual(self, attacker_id, projectile_id, now):
        """Ask the presenter for cosmetic capacity, never authority capacity."""
        if projectile_id is None or self._remote_factory is None:
            return True
        callback = getattr(
            self._remote_factory, 'admit_projectile_visual', None)
        if not callable(callback):
            # Compatibility factories used by older local tools have no
            # budget seam; preserving their existing presentation is safe.
            return True
        try:
            return bool(callback(attacker_id, projectile_id, now))
        except Exception as error:
            self._warn_optional_failure(
                'projectile visual admission', error)
            return False

    @staticmethod
    def _projectile_cosmetic_allowed(event, visual_meta):
        projectile_id = (event.get('projectile_id')
                         if isinstance(event, dict) else None)
        if projectile_id is None:
            return True
        visual = visual_meta.get(str(projectile_id))
        return visual is None or bool(visual.get('admitted', True))

    def _show_local_shot_without_extra(self, entity, burst_count):
        """Advance stock local shot state without starting another effect."""
        descriptor = getattr(entity, 'typeDescriptor', None)
        extras = getattr(descriptor, 'extrasDict', None)
        try:
            original = extras.get('shoot')
        except AttributeError:
            original = None
        if original is None:
            # No shoot extra means the stock call itself has no cosmetic work
            # for us to suppress, while its dispersion handshake still matters.
            return entity.showShooting(burst_count, False)
        try:
            original.stopFor(entity)
        except Exception as error:
            self._warn_optional_failure(
                'shot muzzle retirement', error, disable=False)
        muted = _MutedShootingExtra(original)
        try:
            extras['shoot'] = muted
        except Exception:
            # A non-mutable descriptor cannot be safely intercepted. Preserve
            # stock shot convergence rather than leaving gameplay state stale.
            return entity.showShooting(burst_count, False)
        try:
            return entity.showShooting(burst_count, False)
        finally:
            extras['shoot'] = original

    def _show_shot(self, event, update_state=True):
        key = self._event_entity_key(event, 'attacker')
        if key is None:
            raise RuntimeError('ordered shot event has no attacker')
        record = self._records.get(key)
        if record is None:
            raise RuntimeError(
                'ordered shot event attacker is unavailable: %s' % key)
        if update_state:
            record['shot_penalty_until'] = (
                self._clock() + spotting.SHOT_CAMOUFLAGE_SECONDS)
        self._accept_player_fire_commit(event, record)
        if self._worker_mode:
            return True
        entity = self._server_entity(record['engine_id'])
        if entity is None:
            raise RuntimeError(
                'ordered shot event attacker has no native entity: %s' % key)
        transient_names = []
        try:
            normalized = None
            visual_admitted = True
            try:
                burst_index = int(event.get('burst_index', 0))
            except (TypeError, ValueError, OverflowError):
                burst_index = 0
            entity._offlineLANShotIndex = max(
                0, int(event.get('shell_index', 0) or 0))
            if ('shot_yaw' in event and 'shot_pitch' in event and
                    bool(getattr(
                        entity, '_offlineLANPresentation', False))):
                entity._offlineLANShotYaw = _number(
                    event.get('shot_yaw'))
                entity._offlineLANShotPitch = _number(
                    event.get('shot_pitch'))
                transient_names.extend((
                    '_offlineLANShotYaw', '_offlineLANShotPitch'))
            canonical = all(name in event for name in (
                'origin', 'velocity', 'gravity', 'maxDistance'))
            if canonical:
                normalized = self._projectile_wire_meta(event)
                if normalized is not None:
                    self._install_projectile_meta(normalized)
                    burst_index = normalized['burst_index']
                projectile_id = event.get('projectile_id')
                origin = event.get('origin')
                velocity = event.get('velocity')
                gravity = _number(event.get('gravity'))
                visual_admitted = self._admit_projectile_visual(
                    entity.id, projectile_id, self._clock())
                # Present from the last server-committed collision cursor,
                # not from an extrapolated wall-clock age.  The hidden
                # worker can still be resolving later chords when this launch
                # reaches the visible client.  Fast-forwarding the native
                # mover beyond that cursor lets its cosmetic simulator pass a
                # tank (or strike the world) before the canonical terminal
                # arrives a few frames later.
                elapsed = self._projectile_visual_age(normalized)
                reference_origin = trajectory_position(
                    origin, velocity, (0.0, -gravity, 0.0), elapsed)
                reference_velocity = (
                    float(velocity[0]),
                    float(velocity[1]) - gravity * elapsed,
                    float(velocity[2]))
                if projectile_id is not None:
                    self._projectile_visual_meta[str(projectile_id)] = {
                        'origin': tuple(float(value) for value in origin),
                        'velocity': tuple(float(value) for value in velocity),
                        'gravity': gravity,
                        'admitted': visual_admitted,
                    }
                for name, value in (
                        ('_offlineLANShotOrigin', origin),
                        ('_offlineLANShotVelocity', velocity),
                        ('_offlineLANShotGravity', gravity),
                        ('_offlineLANShotMaxDistance',
                         event.get('maxDistance')),
                        ('_offlineLANProjectileID', projectile_id),
                        ('_offlineLANShotReferenceOrigin', reference_origin),
                        ('_offlineLANShotReferenceVelocity',
                         reference_velocity)):
                    setattr(entity, name, value)
                    transient_names.append(name)
                # RemoteVehicle.showShooting delegates to the same factory
                # presenter and consumes the transient canonical values.  The
                # stock local Vehicle has no such delegate, so launch its
                # authoritative tracer explicitly from the event instead of
                # reconstructing it from a later muzzle pose.
                if (visual_admitted and
                        (not bool(getattr(
                            entity, '_offlineLANPresentation', False)) or
                         burst_index > 0 or
                         not self._optional_feature_enabled(
                             'shot muzzle presentation')) and
                        self._remote_factory is not None):
                    self._run_optional_feature(
                        'projectile visual launch',
                        self._remote_factory.play_projectile_tracer,
                        args=(
                            entity.typeDescriptor,
                            entity._offlineLANShotIndex,
                            origin, velocity, gravity,
                            event.get('maxDistance'), entity.id,
                            projectile_id, reference_origin,
                            reference_velocity))
            if burst_index == 0:
                # One native call owns the grouped muzzle effect and local
                # waiting-for-shot handshake.  Later physical rounds already
                # received their explicit canonical tracer above.
                burst = _field(entity.typeDescriptor.gun, 'burst', (1,))
                try:
                    descriptor_count = int(burst[0])
                except (TypeError, ValueError, IndexError):
                    descriptor_count = 1
                burst_count = (normalized.get('burst_count')
                               if normalized is not None else
                               event.get('burst_count', descriptor_count))
                try:
                    burst_count = int(burst_count)
                except (TypeError, ValueError, OverflowError):
                    burst_count = 1
                burst_count = max(1, burst_count)
                if (visual_admitted and self._optional_feature_enabled(
                        'shot muzzle presentation')):
                    self._run_optional_feature(
                        'shot muzzle presentation', entity.showShooting,
                        args=(burst_count, False))
                elif record.get('local'):
                    self._run_optional_feature(
                        'local shot convergence',
                        self._show_local_shot_without_extra,
                        args=(entity, burst_count))
        finally:
            for name in transient_names:
                try:
                    delattr(entity, name)
                except Exception:
                    pass
        return True

    def _projectile_is_authority(self):
        checker = getattr(self.client, 'is_bot_authority', None)
        if not callable(checker):
            return False
        try:
            return bool(checker())
        except Exception:
            return False

    def _set_projectile_epoch(self, value, now):
        try:
            epoch = int(value)
            if isinstance(value, bool) or epoch < 0:
                return False
        except (TypeError, ValueError, OverflowError):
            return False
        if self._projectile_epoch == epoch:
            return True
        self._projectile_epoch = epoch
        self._projectile_meta = {}
        self._projectile_terminal_data = {}
        # An epoch change elects a new simulator inside the same battle. Keep
        # observational target history across that handoff: restored shells
        # resume from their last checked cursor, which can predate election.
        # Battle start/stop owns the full history reset.
        if self._projectiles is not None:
            reset_at = max(float(now), self._projectiles.now)
            if not self._projectiles.reset(reset_at):
                raise RuntimeError('projectile authority reset failed')
        self._next_projectile_progress_time = float(now)
        return True

    def _observe_projectile_message(self, message):
        """Anchor round-relative server time in this process clock domain."""
        if not isinstance(message, dict):
            return False
        now = self._clock()
        received = message.get('_client_received_time')
        local_anchor = now
        if received is not None:
            try:
                received = float(received)
                if not math.isnan(received) and not math.isinf(received):
                    # The network thread timestamps receipt with a monotonic
                    # clock.  Project its queueing delay into BigWorld time so
                    # a render stall cannot make every in-flight shell young.
                    process_now = getattr(time, 'monotonic', None)
                    if callable(process_now):
                        process_now = float(process_now())
                    else:
                        process_now = float(time.clock())
                    lag = max(0.0, min(60.0, process_now - received))
                    local_anchor = max(0.0, now - lag)
            except (TypeError, ValueError, OverflowError):
                return False
        if 'server_time_ms' in message:
            server_time = message.get('server_time_ms')
            try:
                server_time = int(server_time)
            except (TypeError, ValueError, OverflowError):
                return False
            if server_time < 0:
                return False
            if (self._projectile_server_time_ms is None or
                    server_time >= self._projectile_server_time_ms):
                one_way = 0.0
                try:
                    rtt_ms = getattr(self.client, 'minimum_rtt_ms', None)
                    if rtt_ms is None:
                        rtt_ms = getattr(self.client, 'rtt_ms', None)
                    if rtt_ms is not None:
                        one_way = max(
                            0.0, min(0.25, float(rtt_ms) / 2000.0))
                except (TypeError, ValueError, OverflowError):
                    one_way = 0.0
                self._projectile_server_time_ms = server_time
                self._projectile_server_local_time = max(
                    0.0, local_anchor - one_way)
        if 'motion_time_us' in message:
            try:
                motion_time_us = int(message.get('motion_time_us'))
            except (TypeError, ValueError, OverflowError):
                return False
            if motion_time_us < 0:
                return False
            one_way = 0.0
            try:
                rtt_ms = getattr(self.client, 'minimum_rtt_ms', None)
                if rtt_ms is None:
                    rtt_ms = getattr(self.client, 'rtt_ms', None)
                if rtt_ms is not None:
                    one_way = max(
                        0.0, min(0.25, float(rtt_ms) / 2000.0))
            except (TypeError, ValueError, OverflowError):
                one_way = 0.0
            if (self._pose_motion_time_us is None or
                    motion_time_us >= self._pose_motion_time_us):
                self._pose_motion_time_us = motion_time_us
                self._pose_motion_local_time = max(
                    0.0, local_anchor - one_way)
        epoch = message.get(
            'authority_epoch', getattr(self.client, 'authority_epoch', None))
        if epoch is not None:
            self._set_projectile_epoch(epoch, now)
        return True

    def _projectile_estimated_server_time(self, now):
        if (self._projectile_server_time_ms is None or
                self._projectile_server_local_time is None):
            return None
        elapsed = max(
            0.0, float(now) - float(self._projectile_server_local_time))
        return int(self._projectile_server_time_ms + elapsed * 1000.0)

    def _estimated_motion_time_us(self, now):
        """Map one local pose sample onto the server's monotonic timeline."""
        if (self._pose_motion_time_us is None or
                self._pose_motion_local_time is None):
            return None
        elapsed = max(
            0.0, float(now) - float(self._pose_motion_local_time))
        return int(self._pose_motion_time_us + elapsed * 1000000.0)

    def _projectile_local_launch_time(self, launch_server_time_ms, now):
        estimated = self._projectile_estimated_server_time(now)
        if estimated is None:
            return min(float(now), self._projectiles.now)
        age = max(
            0.0, float(estimated - int(launch_server_time_ms)) / 1000.0)
        return max(
            0.0, min(self._projectiles.now, float(now) - age))

    @staticmethod
    def _projectile_visual_age(raw):
        """Return only the server-confirmed age of a visual segment.

        Native ``ProjectileMover`` advances independently after ``add``.  Its
        reference point therefore starts at the durable collision cursor, not
        at an estimated current server time that can be ahead of the worker's
        terminal receipt.
        """
        if not isinstance(raw, dict):
            return 0.0
        segment_start = raw.get('segment_start_time_ms', 0)
        checked_through = raw.get(
            'base_checked_ms', raw.get('checked_through_ms', segment_start))
        max_time_ms = raw.get('max_time_ms', PROJECTILE_MAX_TIME_MS)
        try:
            segment_start = int(segment_start)
            checked_through = int(checked_through)
            maximum = max(
                0.0, float(int(max_time_ms) - segment_start) / 1000.0)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        return max(
            0.0, min(
                maximum,
                float(checked_through - segment_start) / 1000.0))

    @staticmethod
    def _projectile_wire_meta(raw):
        """Normalize the canonical-event and snapshot launch spellings."""
        if not isinstance(raw, dict):
            return None
        maximum = raw.get('max_distance', raw.get('maxDistance'))
        shooter_kind = raw.get('shooter_kind')
        shooter_id = raw.get('shooter_id')
        if shooter_kind is None:
            if raw.get('attacker_bot') is not None:
                shooter_kind = 'bot'
                shooter_id = raw.get('attacker_bot')
            elif raw.get('attacker') is not None:
                shooter_kind = 'player'
                shooter_id = raw.get('attacker')
        required = (
            'projectile_id', 'source_vehicle', 'shot_seq',
            'burst_group_seq', 'burst_index', 'burst_count',
            'shell_index', 'origin',
            'velocity', 'gravity', 'max_time_ms', 'is_he',
            'splash_radius', 'penetration_factor', 'launch_server_time_ms',
            'source_shot', 'range_origin', 'segment_origin',
            'segment_velocity', 'segment_start_time_ms', 'ricochet_count',
            'base_penetration_multiplier')
        if (maximum is None or shooter_kind is None or shooter_id is None or
                any(name not in raw for name in required)):
            return None
        try:
            origin = tuple(float(value) for value in raw['origin'])
            velocity = tuple(float(value) for value in raw['velocity'])
            range_origin = tuple(
                float(value) for value in raw['range_origin'])
            segment_origin = tuple(
                float(value) for value in raw['segment_origin'])
            segment_velocity = tuple(
                float(value) for value in raw['segment_velocity'])
            if (len(origin) != 3 or len(velocity) != 3 or
                    len(range_origin) != 3 or len(segment_origin) != 3 or
                    len(segment_velocity) != 3):
                return None
            gravity = float(raw['gravity'])
            maximum = float(maximum)
            max_time_ms = int(raw['max_time_ms'])
            projectile_id = str(raw['projectile_id'])
            source_vehicle = str(raw['source_vehicle'])
            shooter_kind = str(shooter_kind)
            shooter_id = int(shooter_id)
            shot_seq = int(raw['shot_seq'])
            burst_group = lan_protocol._strict_burst_group(
                shot_seq, raw['burst_group_seq'], raw['burst_index'],
                raw['burst_count'])
            shell_index = int(raw['shell_index'])
            launch_server_time = int(raw['launch_server_time_ms'])
            segment_start_time = int(raw['segment_start_time_ms'])
            ricochet_count = int(raw['ricochet_count'])
            base_multiplier = float(raw['base_penetration_multiplier'])
            splash_radius = float(raw['splash_radius'])
            penetration_factor = float(raw['penetration_factor'])
            source_shot = lan_protocol._strict_projectile_source_shot(
                raw['source_shot'])
        except (TypeError, ValueError, OverflowError):
            return None
        values = (origin + velocity + range_origin + segment_origin +
                  segment_velocity + (
                      gravity, maximum, splash_radius, penetration_factor,
                      base_multiplier))
        if (not projectile_id or not source_vehicle or
                len(source_vehicle) > 128 or
                shooter_kind not in ('player', 'bot') or
                shooter_id <= 0 or shot_seq <= 0 or
                burst_group is None or
                shell_index < 0 or shell_index > 9 or
                gravity <= 0.0 or maximum <= 0.0 or
                max_time_ms <= 0 or max_time_ms > PROJECTILE_MAX_TIME_MS or
                launch_server_time < 0 or segment_start_time < 0 or
                segment_start_time > max_time_ms or
                (ricochet_count == 1 and
                 segment_start_time >= max_time_ms) or
                ricochet_count not in (0, 1) or
                splash_radius < 0.0 or
                penetration_factor < 0.0 or
                not isinstance(raw['is_he'], bool) or
                not lan_protocol._projectile_source_shot_matches_launch(
                    source_shot, velocity, gravity, maximum,
                    raw['is_he'], splash_radius) or
                any(math.isnan(value) or math.isinf(value)
                    for value in values)):
            return None
        shell_kind = (source_shot.get('shell') or {}).get('kind')
        expected_multiplier = (
            1.0 if ricochet_count == 0 else
            combat_rules.first_ricochet_penetration_multiplier(shell_kind))
        if (expected_multiplier is None or
                base_multiplier != expected_multiplier or
                (ricochet_count == 0 and
                 (segment_start_time != 0 or segment_origin != origin or
                  segment_velocity != velocity))):
            return None
        base_checked_ms = max(
            0, int(raw.get('checked_through_ms', 0) or 0))
        if base_checked_ms < segment_start_time:
            return None
        result = {
            'projectile_id': projectile_id,
            'shooter_kind': shooter_kind,
            'shooter_id': shooter_id,
            'source_vehicle': source_vehicle,
            'source_shot': source_shot,
            'shot_seq': shot_seq,
            'burst_group_seq': burst_group[0],
            'burst_index': burst_group[1],
            'burst_count': burst_group[2],
            'shell_index': shell_index,
            'origin': origin,
            'velocity': velocity,
            'range_origin': range_origin,
            'segment_origin': segment_origin,
            'segment_velocity': segment_velocity,
            'segment_start_time_ms': segment_start_time,
            'ricochet_count': ricochet_count,
            'base_penetration_multiplier': base_multiplier,
            'gravity': gravity,
            'max_distance': maximum,
            'max_time_ms': max_time_ms,
            'is_he': bool(raw['is_he']),
            'splash_radius': splash_radius,
            'penetration_factor': penetration_factor,
            'launch_server_time_ms': launch_server_time,
            'base_checked_ms': base_checked_ms,
            'checked_distance': max(
                0.0, _number(raw.get('checked_distance'), 0.0)),
            'piercing_loss': max(
                0.0, _number(raw.get('piercing_loss'), 0.0)),
        }
        if shooter_kind == 'player':
            try:
                fire_intent_seq = int(raw['fire_intent_seq'])
                fire_input_seq = int(raw['fire_input_seq'])
            except (KeyError, TypeError, ValueError, OverflowError):
                return None
            if fire_intent_seq <= 0 or fire_input_seq <= 0:
                return None
            result['fire_intent_seq'] = fire_intent_seq
            result['fire_input_seq'] = fire_input_seq
        elif 'fire_intent_seq' in raw or 'fire_input_seq' in raw:
            return None
        return result

    def _install_projectile_meta(self, normalized):
        projectile_id = normalized['projectile_id']
        meta = self._projectile_meta.get(projectile_id)
        if meta is None:
            meta = dict(normalized)
            meta['manager_key'] = (
                projectile_id if normalized['ricochet_count'] == 0 else
                (projectile_id, normalized['ricochet_count']))
            meta['destructibles_pending'] = []
            self._projectile_meta[projectile_id] = meta
        else:
            # Launch fields are immutable; only the server-acknowledged cursor
            # and accumulated penetration state may advance in snapshots.
            current_count = int(meta.get('ricochet_count', 0))
            meta.setdefault(
                'manager_key', projectile_id if current_count == 0 else
                (projectile_id, current_count))
            frozen = (
                'shooter_kind', 'shooter_id', 'source_vehicle',
                'source_shot', 'shot_seq', 'shell_index',
                'burst_group_seq', 'burst_index', 'burst_count',
                'origin', 'velocity', 'range_origin',
                'gravity', 'max_distance',
                'max_time_ms', 'is_he', 'splash_radius',
                'penetration_factor', 'launch_server_time_ms',
                'fire_intent_seq', 'fire_input_seq')
            if any(meta.get(name) != normalized.get(name)
                   for name in frozen):
                raise RuntimeError('canonical projectile launch changed')
            incoming_count = normalized['ricochet_count']
            if incoming_count < current_count or incoming_count > (
                    current_count + 1):
                raise RuntimeError('canonical projectile segment regressed')
            segment_fields = (
                'segment_origin', 'segment_velocity',
                'segment_start_time_ms', 'base_penetration_multiplier')
            if incoming_count == current_count:
                if any(meta.get(name) != normalized.get(name)
                       for name in segment_fields):
                    raise RuntimeError('canonical projectile segment changed')
            else:
                old_manager_key = meta.get('manager_key', projectile_id)
                if self._projectiles is not None:
                    self._projectiles.remove(old_manager_key)
                for name in segment_fields:
                    meta[name] = normalized[name]
                meta['ricochet_count'] = incoming_count
                meta['manager_key'] = (projectile_id, incoming_count)
                meta['awaiting_ricochet'] = False
                meta['pending_ricochet'] = None
                meta['destructibles_pending'] = []
            if (normalized['base_checked_ms'] >=
                    meta.get('base_checked_ms', 0)):
                meta['base_checked_ms'] = normalized['base_checked_ms']
                meta['acked_distance'] = normalized['checked_distance']
                meta['acked_piercing_loss'] = normalized['piercing_loss']
                pending = meta.get('progress_pending')
                if (pending is not None and
                        normalized['base_checked_ms'] >=
                        pending['checked_through_ms']):
                    meta['progress_pending'] = None
                active = (self._projectiles is not None and
                          self._projectiles.contains(meta['manager_key']))
                if (not active and
                        meta.get('pending_resolution') is None and
                        meta.get('pending_ricochet') is None and
                        not meta.get('awaiting_ricochet') and
                        not meta.get('awaiting_resolution')):
                    meta['checked_distance'] = normalized[
                        'checked_distance']
                    meta['piercing_loss'] = normalized['piercing_loss']
        self._confirm_bot_projectile_launch(normalized)
        return meta

    def _accept_projectile_event(self, event):
        """Register one server-admitted launch on the elected simulator."""
        if not self._projectile_is_authority() or self._projectiles is None:
            return False
        epoch = event.get(
            'authority_epoch', getattr(self.client, 'authority_epoch', None))
        if not self._set_projectile_epoch(epoch, self._clock()):
            return False
        normalized = self._projectile_wire_meta(event)
        if normalized is None:
            raise RuntimeError('canonical projectile event is malformed')
        meta = self._install_projectile_meta(normalized)
        projectile_id = normalized['projectile_id']
        manager_key = meta['manager_key']
        if self._projectiles.contains(manager_key):
            return True
        now = self._clock()
        launch_time = self._projectile_local_launch_time(
            normalized['launch_server_time_ms'] +
            normalized['segment_start_time_ms'], now)
        payload = {
                'shooter_kind': normalized['shooter_kind'],
                'shooter_id': normalized['shooter_id'],
                'shot_seq': normalized['shot_seq'],
                'shell_index': normalized['shell_index'],
                'range_origin': normalized['range_origin'],
                'base_penetration_multiplier': normalized[
                    'base_penetration_multiplier'],
                'ricochet_count': normalized['ricochet_count'],
                'segment_start_time_ms': normalized[
                    'segment_start_time_ms'],
            }
        if normalized['ricochet_count'] == 0:
            accepted = self._projectiles.launch(
                manager_key, normalized['segment_origin'],
                normalized['segment_velocity'],
                (0.0, -normalized['gravity'], 0.0), launch_time,
                float(normalized['max_time_ms']) / 1000.0,
                normalized['max_distance'], payload=payload)
        else:
            cursor_time = min(
                self._projectiles.now,
                launch_time + max(
                    0, normalized['base_checked_ms'] -
                    normalized['segment_start_time_ms']) / 1000.0)
            accepted = self._projectiles.restore({
                'key': manager_key,
                'start': normalized['segment_origin'],
                'velocity': normalized['segment_velocity'],
                'gravity': (0.0, -normalized['gravity'], 0.0),
                'launch_time': launch_time,
                'max_time': max(
                    0.001, float(
                        normalized['max_time_ms'] -
                        normalized['segment_start_time_ms']) / 1000.0),
                'max_distance': normalized['max_distance'],
                'payload': payload,
                'cursor_time': max(launch_time, cursor_time),
                'distance': normalized['checked_distance'],
            })
        if not accepted:
            self._projectile_meta.pop(projectile_id, None)
            raise RuntimeError('canonical projectile launch was not admitted')
        if not self._projectile_position_history:
            poses = self._projectile_record_poses()
            self._sample_projectile_positions(now, poses)
            self._projectile_target_positions = dict(
                (key, _xyz(pose)) for key, pose in poses.items())
        meta['awaiting_resolution'] = False
        meta['awaiting_ricochet'] = False
        return True

    def _reconcile_projectile_snapshot(self, message):
        """Restore the authoritative cursor without rescanning elapsed time."""
        if self._projectiles is None or not isinstance(message, dict):
            return False
        rows = message.get('projectiles')
        if not isinstance(rows, (list, tuple)):
            return False
        now = self._clock()
        active_ids = set()
        for raw in rows:
            normalized = self._projectile_wire_meta(raw)
            if normalized is None:
                raise RuntimeError('active projectile snapshot is malformed')
            projectile_id = normalized['projectile_id']
            active_ids.add(projectile_id)
            self._install_projectile_meta(normalized)
            self._ensure_projectile_visual(normalized, now)
        if not self._projectile_is_authority():
            return True
        for raw in rows:
            normalized = self._projectile_wire_meta(raw)
            projectile_id = normalized['projectile_id']
            meta = self._install_projectile_meta(normalized)
            if self._projectiles.contains(meta['manager_key']):
                continue
            if meta.get('pending_ricochet') is not None:
                # The unchanged first-segment snapshot proves that the server
                # has not committed this exact ricochet yet. Retry its frozen
                # CAS payload without recomputing impact or destructibles.
                meta['awaiting_ricochet'] = False
                self._submit_projectile_ricochet(meta)
                continue
            if meta.get('pending_resolution') is not None:
                # Presence in the next authoritative snapshot means the
                # server has not committed this exact terminal yet.  Retry
                # the frozen proposal once for this snapshot; its idempotent
                # request fingerprint makes a delayed duplicate harmless.
                meta['awaiting_resolution'] = False
                self._submit_projectile_resolution(meta)
                continue
            source = self._projectile_source_entity(meta)
            source_descriptor = (getattr(source, 'typeDescriptor', None)
                                 if source is not None else None)
            if source_descriptor is None:
                source_descriptor = self._projectile_source_descriptor(meta)
            if source_descriptor is None:
                # A takeover snapshot can overtake delayed native entity
                # materialization.  The ledger freezes source_vehicle so a
                # shooter that disconnected after firing remains resolvable;
                # only wait when even that canonical descriptor is unavailable.
                continue
            launch_time = self._projectile_local_launch_time(
                normalized['launch_server_time_ms'] +
                normalized['segment_start_time_ms'], now)
            cursor_time = min(
                self._projectiles.now,
                launch_time + max(
                    0, normalized['base_checked_ms'] -
                    normalized['segment_start_time_ms']) / 1000.0)
            restored = self._projectiles.restore({
                'key': meta['manager_key'],
                'start': normalized['segment_origin'],
                'velocity': normalized['segment_velocity'],
                'gravity': (0.0, -normalized['gravity'], 0.0),
                'launch_time': launch_time,
                'max_time': max(
                    0.001, float(
                        normalized['max_time_ms'] -
                        normalized['segment_start_time_ms']) / 1000.0),
                'max_distance': normalized['max_distance'],
                'payload': {
                    'shooter_kind': normalized['shooter_kind'],
                    'shooter_id': normalized['shooter_id'],
                    'shot_seq': normalized['shot_seq'],
                    'shell_index': normalized['shell_index'],
                    'range_origin': normalized['range_origin'],
                    'base_penetration_multiplier': normalized[
                        'base_penetration_multiplier'],
                    'ricochet_count': normalized['ricochet_count'],
                    'segment_start_time_ms': normalized[
                        'segment_start_time_ms'],
                },
                'cursor_time': max(launch_time, cursor_time),
                'distance': normalized['checked_distance'],
            })
            if not restored:
                raise RuntimeError('active projectile restore failed')
        for projectile_id, meta in tuple(self._projectile_meta.items()):
            if (projectile_id not in active_ids and
                    (meta.get('awaiting_resolution') or
                     meta.get('pending_resolution') is not None or
                     meta.get('awaiting_ricochet') or
                     meta.get('pending_ricochet') is not None)):
                self._projectile_meta.pop(projectile_id, None)
                self._projectile_terminal_data.pop(projectile_id, None)
        try:
            revision = int(message.get('projectile_revision', -1))
        except (TypeError, ValueError, OverflowError):
            revision = -1
        self._projectile_revision = max(
            self._projectile_revision, revision)
        return True

    def _apply_projectile_ricochet_event(self, event):
        """Replace the first segment only after the server commits its CAS."""
        normalized = self._projectile_wire_meta(event)
        if normalized is None or normalized['ricochet_count'] != 1:
            raise RuntimeError('canonical projectile ricochet is malformed')
        projectile_id = normalized['projectile_id']
        meta = self._projectile_meta.get(projectile_id)
        if meta is None:
            raise RuntimeError('canonical projectile ricochet lost its launch')
        if int(meta.get('ricochet_count', 0)) == 0:
            meta['hit_vehicle'] = True
            self._stop_projectile_visual(projectile_id, event)
        meta = self._install_projectile_meta(normalized)
        meta['awaiting_ricochet'] = False
        meta['pending_ricochet'] = None
        now = self._clock()
        self._ensure_projectile_visual(normalized, now)
        if self._projectile_is_authority():
            self._accept_projectile_event(event)
        return True

    def _apply_projectile_terminal_event(self, event):
        projectile_id = event.get('projectile_id')
        if projectile_id is None:
            raise RuntimeError('projectile terminal event has no id')
        projectile_id = str(projectile_id)
        meta = self._projectile_meta.get(projectile_id)
        if meta is not None and isinstance(event.get('hit_vehicle'), bool):
            meta['hit_vehicle'] = event['hit_vehicle']
            visual = self._projectile_visual_meta.get(projectile_id)
            try:
                elapsed = max(
                    0.0, (float(event.get('resolved_time_ms')) -
                          float(meta.get('segment_start_time_ms', 0))) /
                    1000.0)
            except (TypeError, ValueError, OverflowError):
                elapsed = None
            if visual is not None and elapsed is not None:
                meta['terminal_velocity'] = (
                    visual['velocity'][0],
                    visual['velocity'][1] - visual['gravity'] * elapsed,
                    visual['velocity'][2])
        if isinstance(event.get('wreck_hit'), dict):
            self._present_projectile_wreck_hit(projectile_id, event)
        self._stop_projectile_visual(projectile_id, event)
        if self._projectiles is not None:
            manager_key = (meta.get('manager_key') if meta is not None else
                           projectile_id)
            self._projectiles.remove(manager_key)
        self._projectile_meta.pop(projectile_id, None)
        self._projectile_terminal_data.pop(projectile_id, None)
        return True

    def _present_projectile_wreck_hit(self, projectile_id, event):
        """Play the stock armour-hit family on one visible retained wreck."""
        if self._worker_mode:
            return False
        visual = self._projectile_visual_meta.get(projectile_id)
        if visual is not None and not bool(visual.get('admitted', True)):
            return False
        wreck_hit = event.get('wreck_hit')
        if (not isinstance(wreck_hit, dict) or
                set(wreck_hit) != {'target_kind', 'target_id'}):
            return False
        target_kind = wreck_hit.get('target_kind')
        try:
            target_id = int(wreck_hit.get('target_id'))
        except (TypeError, ValueError, OverflowError):
            return False
        if target_kind not in ('player', 'bot') or target_id <= 0:
            return False
        target_record = self._records.get(
            '%s:%s' % (target_kind, target_id))
        if target_record is None:
            return False
        target = self._server_entity(target_record.get('engine_id'))
        if (target is None or not getattr(target, 'isStarted', False) or
                self._record_alive(target_record, target) or
                (not target_record.get('local') and
                 not target_record.get('spot_visible', True))):
            return False
        meta = self._projectile_meta.get(projectile_id)
        if meta is None:
            return False
        shot = self._projectile_shot(meta)
        shell = _field(shot, 'shell', None)
        effects_index = _field(shell, 'effectsIndex', None)
        if effects_index is None:
            return False
        try:
            effects_descr = self._runtime.vehicles.g_cache.shotEffects[
                int(effects_index)]
            stages, effects, unused = effects_descr['armorHit']
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            return False
        impact = event.get('impact')
        if not isinstance(impact, (list, tuple)) or len(impact) < 3:
            return False
        direction_value = meta.get('terminal_velocity')
        if not direction_value:
            visual = self._projectile_visual_meta.get(projectile_id)
            direction_value = visual.get('velocity') if visual else None
        if not direction_value:
            source = self._projectile_source_entity(meta)
            source_position = getattr(source, 'position', None)
            if source_position is not None:
                direction_value = tuple(
                    _number(impact[index]) - _number(source_position[index])
                    for index in range(3))
        if not direction_value:
            return False
        direction = self._vector(_xyz(direction_value))
        if direction.length <= 0.0:
            return False
        direction.normalise()
        hit_position = self._vector(_xyz(impact))
        terrain_effects = getattr(self._avatar, 'terrainEffects', None)
        add_effect = getattr(terrain_effects, 'addNew', None)
        if (not callable(add_effect) or
                not self._optional_feature_enabled(
                    'projectile impact presentation')):
            return False
        self._report_effect(
            'wreck_hit', 'armorHit', effects_index, impact, direction)
        try:
            add_effect(
                hit_position, effects, stages, None, dir=direction,
                start=hit_position - direction.scale(0.4),
                end=hit_position + direction.scale(0.4),
                showShockWave=False, showFlashBang=False)
        except Exception as error:
            self._warn_optional_failure(
                'projectile impact presentation', error)
            return False
        return True

    def _ensure_projectile_visual(self, normalized, now):
        """Ensure late joiners and delayed snapshots see the live tracer."""
        if self._worker_mode:
            return False
        if self._remote_factory is None or not isinstance(normalized, dict):
            return False
        descriptor = self._projectile_source_descriptor(normalized)
        if descriptor is None:
            return False
        existing_visual = self._projectile_visual_meta.get(
            normalized['projectile_id'])
        if (existing_visual is not None and
                int(existing_visual.get('ricochet_count', 0)) !=
                normalized['ricochet_count']):
            meta = self._projectile_meta.get(normalized['projectile_id'])
            if meta is not None:
                meta['hit_vehicle'] = True
            self._stop_projectile_visual(
                normalized['projectile_id'], {
                    'impact': list(normalized['segment_origin']),
                    'resolved_time_ms': normalized[
                        'segment_start_time_ms'],
                })
        elapsed = self._projectile_visual_age(normalized)
        gravity = normalized['gravity']
        reference_origin = trajectory_position(
            normalized['segment_origin'], normalized['segment_velocity'],
            (0.0, -gravity, 0.0), elapsed)
        reference_velocity = (
            normalized['segment_velocity'][0],
            normalized['segment_velocity'][1] - gravity * elapsed,
            normalized['segment_velocity'][2])
        self._projectile_visual_meta[normalized['projectile_id']] = {
            'origin': tuple(normalized['segment_origin']),
            'velocity': tuple(normalized['segment_velocity']),
            'gravity': gravity,
            'segment_start_time_ms': normalized['segment_start_time_ms'],
            'ricochet_count': normalized['ricochet_count'],
        }
        record = self._records.get('%s:%s' % (
            normalized['shooter_kind'], normalized['shooter_id']))
        attacker_id = int(record.get('engine_id', 0) or 0) \
            if record is not None else 0
        if attacker_id <= 0:
            # ProjectileMover only uses the attacker id for presentation
            # attribution.  A disconnected shooter must not erase a live
            # projectile restored from the durable snapshot.
            attacker_id = int(normalized['shooter_id'])
        admitted = (bool(existing_visual.get('admitted', True))
                    if existing_visual is not None else
                    self._admit_projectile_visual(
                        attacker_id, normalized['projectile_id'], now))
        self._projectile_visual_meta[normalized['projectile_id']][
            'admitted'] = admitted
        if not admitted or not self._optional_feature_enabled(
                'projectile visual launch'):
            return False
        try:
            return bool(self._remote_factory.play_projectile_tracer(
                descriptor, normalized['shell_index'],
                normalized['segment_origin'],
                normalized['segment_velocity'], gravity, max(
                    0.001, normalized['max_distance'] -
                    normalized['checked_distance']),
                attacker_id, normalized['projectile_id'], reference_origin,
                reference_velocity,
                is_ricochet=bool(normalized['ricochet_count'])))
        except Exception as error:
            self._warn_optional_failure(
                'projectile visual launch', error)
            return False

    def _projectile_explosion(self, projectile_id, impact):
        """Return ``(effectsDescr, effectMaterial, velocity)`` for a world hit.

        Returns None for a vehicle terminal and whenever the verdict is not
        ours to make, because an explosion added on top of the armour-hit
        effect would be a visible regression while a missing one is not.
        """
        meta = self._projectile_meta.get(projectile_id)
        if meta is None or meta.get('hit_vehicle') is not False:
            return None
        visual = self._projectile_visual_meta.get(projectile_id)
        if visual is not None and not bool(visual.get('admitted', True)):
            return None
        shot = self._projectile_shot(meta)
        shell = _field(shot, 'shell', None)
        effects_index = _field(shell, 'effectsIndex', None)
        if effects_index is None:
            return None
        try:
            effects_descr = self._runtime.vehicles.g_cache.shotEffects[
                int(effects_index)]
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            return None
        material = self._surface_effect_material(impact)
        if material is None:
            return None
        velocity = meta.get('terminal_velocity')
        if not velocity or len(tuple(velocity)) < 3:
            visual = self._projectile_visual_meta.get(projectile_id)
            velocity = visual.get('velocity') if visual else None
        if not velocity:
            return None
        # __addExplosionEffect keys the effect at position +/- velocityDir,
        # so a raw muzzle velocity stretched it over a kilometre of terrain.
        direction = self._vector(_xyz(velocity))
        if direction.length <= 0.0:
            return None
        direction.normalise()
        self._report_effect(
            'world_explosion', material, effects_index, impact, direction)
        return (effects_descr, material, direction)

    def _surface_effect_material(self, impact):
        """Resolve the impact surface to one ``EFFECT_MATERIALS`` name.

        ``ProjectileMover.explode`` indexes the effect descriptor with
        ``effectMaterial + 'Hit'``, so a wrong name raises instead of drawing.
        """
        calculation = getattr(
            self._runtime, 'effect_material_calculation', None)
        materials = getattr(self._runtime, 'material_kinds', None)
        if calculation is None or materials is None:
            return None
        try:
            surface = calculation.calcSurfaceMaterialNearPoint(
                self._vector(_xyz(impact)), self._vector((0.0, 1.0, 0.0)),
                self._avatar.spaceID)
            index = surface.effectIdx
            if index is None:
                return None
            return materials.EFFECT_MATERIALS[int(index)]
        except Exception:
            return None

    def _stop_projectile_visual(self, projectile_id, event):
        if self._worker_mode:
            self._projectile_visual_meta.pop(projectile_id, None)
            return False
        if self._remote_factory is None:
            return False
        impact = event.get('impact') if isinstance(event, dict) else None
        if impact is None:
            meta = self._projectile_meta.get(projectile_id)
            manager_key = (meta.get('manager_key') if meta is not None else
                           projectile_id)
            state = (self._projectiles.get(manager_key)
                     if self._projectiles is not None else None)
            impact = state.get('position') if state is not None else None
        if impact is None:
            visual = self._projectile_visual_meta.get(projectile_id)
            try:
                elapsed = max(
                    0.0, (float(event.get('resolved_time_ms')) -
                          float(visual.get(
                              'segment_start_time_ms', 0))) / 1000.0)
            except (AttributeError, TypeError, ValueError, OverflowError):
                elapsed = None
            if visual is not None and elapsed is not None:
                impact = trajectory_position(
                    visual['origin'], visual['velocity'],
                    (0.0, -visual['gravity'], 0.0), elapsed)
        if impact is None:
            return False
        try:
            stopped = bool(self._remote_factory.stop_projectile_tracer(
                projectile_id, impact,
                explosion=self._projectile_explosion(
                    projectile_id, impact)))
        except Exception as error:
            # Terminal authority has already been applied.  A native cosmetic
            # retirement failure must not poison the ordered event journal.
            self._warn_optional_failure(
                'projectile visual retirement', error, disable=False)
            stopped = False
        self._projectile_visual_meta.pop(projectile_id, None)
        return stopped

    def _projectile_record_positions(self):
        result = {}
        for key, record in tuple(self._records.items()):
            if record.get('tombstone'):
                continue
            if self._worker_mode and record.get('local'):
                # player:-1 is a private native-space carrier, never a
                # projectile broadphase or collision target.
                continue
            entity = self._server_entity(record.get('engine_id'))
            if entity is None or not getattr(entity, 'isStarted', False):
                continue
            if record.get('local'):
                result[key] = tuple(self._local_position)
            else:
                result[key] = _xyz(getattr(
                    entity, 'position', record.get('state', {})))
        return result

    def _projectile_vehicle_matrices(self, record, vehicle,
                                     ground_matrix=None):
        """Return body/chassis poses matching the projectile timeline.

        A hidden worker interpolates native Bot compounds for presentation,
        but ``_projectile_record_positions`` intentionally records the
        canonical copied-physics position.  Mixing that position with the
        render-blended matrix makes broad phase and exact hit testing disagree
        for a moving target. Use the factory's unblended hydraulic body and
        ground matrices on the worker and retain the visible provider
        everywhere else.
        """
        canonical = vehicle.matrix if ground_matrix is None else ground_matrix
        if (self._worker_mode and record.get('native_remote') and
                self._remote_factory is not None):
            getter = getattr(
                self._remote_factory, 'projectile_collision_matrices', None)
            if callable(getter):
                matrices = getter(record.get('engine_id'), ground_matrix)
                if matrices is not None:
                    return matrices
            getter = getattr(
                self._remote_factory, 'projectile_collision_matrix', None)
            if callable(getter):
                body = getter(record.get('engine_id'))
                if body is not None:
                    return body, canonical
        return canonical, canonical

    def _projectile_vehicle_matrix(self, record, vehicle):
        """Compatibility seam returning only the authority body pose."""
        return self._projectile_vehicle_matrices(record, vehicle)[0]

    @staticmethod
    def _projectile_plain_pose(position, state=None):
        state = state if isinstance(state, dict) else {}
        yaw = _number(state.get('yaw'))
        aim_yaw = _number(state.get('aim_yaw'), yaw)
        turret_yaw = _number(
            state.get('turret_yaw'), _angle_delta(yaw, aim_yaw))
        return {
            'x': _number(position[0]), 'y': _number(position[1]),
            'z': _number(position[2]), 'yaw': yaw,
            'pitch': _number(state.get('pitch')),
            'roll': _number(state.get('roll')),
            'turret_yaw': turret_yaw,
            'gun_pitch': _number(state.get('gun_pitch')),
            'siege_state': int(state.get('siege_state', 0) or 0),
        }

    def _projectile_record_pose(self, key, record, position):
        """Freeze one coherent hull, aim and mode sample for collision."""
        state = record.get('state') or {}
        source = record.get('projectile_collision_pose')
        if not isinstance(source, dict):
            source = state
        source_position = position
        if all(name in source for name in ('x', 'y', 'z')):
            source_position = _xyz(source)
        pose = self._projectile_plain_pose(source_position, source)
        entity = self._server_entity(record.get('engine_id'))
        if entity is None:
            return None
        if record.get('local'):
            pose.update({
                'x': _number(self._local_position[0]),
                'y': _number(self._local_position[1]),
                'z': _number(self._local_position[2]),
                'yaw': _number(self._local_yaw),
                'pitch': _number(self._local_pitch),
                'roll': _number(self._local_roll),
            })
        aim = getattr(entity, 'getAimParams', None)
        if (not isinstance(record.get('projectile_collision_pose'), dict) and
                callable(aim)):
            try:
                turret_yaw, gun_pitch = aim()
                pose['turret_yaw'] = _number(turret_yaw)
                pose['gun_pitch'] = _number(gun_pitch)
            except Exception:
                pass
        return pose

    def _projectile_record_poses(self):
        positions = self._projectile_record_positions()
        result = {}
        for key, position in positions.items():
            record = self._records.get(key)
            if record is None:
                continue
            pose = self._projectile_record_pose(key, record, position)
            if pose is not None:
                result[key] = pose
        return result

    def _sample_projectile_positions(self, now, poses):
        """Keep atomic collision-pose history for projectile catch-up."""
        frozen = {}
        for key, pose in (poses or {}).items():
            if isinstance(pose, dict):
                frozen[key] = dict(pose)
            else:
                frozen[key] = self._projectile_plain_pose(pose)
        sample = (float(now), frozen)
        # Keep both known endpoints across a delayed render callback. Historic
        # queries interpolate only inside that covered interval and still reject
        # pre-history or future extrapolation, so a long frame cannot silently
        # retire an otherwise valid projectile.
        if (self._projectile_position_history and
                abs(self._projectile_position_history[-1][0] -
                    sample[0]) <= 1.0e-9):
            self._projectile_position_history[-1] = sample
        else:
            self._projectile_position_history.append(sample)
        # Twenty seconds is the protocol lifetime.  The small extra margin
        # retains the left interpolation endpoint during a delayed frame.
        floor = float(now) - PROJECTILE_MAX_TIME_MS / 1000.0 - 1.0
        while (len(self._projectile_position_history) > 2 and
               self._projectile_position_history[1][0] < floor):
            self._projectile_position_history.pop(0)

    def _projectile_historic_pose(self, key, absolute_time):
        """Interpolate one covered pose; never invent pre-history state."""
        wanted = float(absolute_time)
        cache = self._projectile_historic_pose_cache
        cache_key = (key, wanted)
        if cache is not None:
            cached = cache.get(cache_key, _PROJECTILE_POSE_CACHE_MISS)
            if cached is not _PROJECTILE_POSE_CACHE_MISS:
                return dict(cached) if cached is not None else None
        result = self._projectile_historic_pose_uncached(key, wanted)
        if cache is not None and len(cache) < PROJECTILE_POSE_CACHE_ENTRIES:
            cache[cache_key] = dict(result) if result is not None else None
        return result

    def _projectile_historic_pose_uncached(self, key, wanted):
        """Compute one detached historic pose from the current history."""
        history = self._projectile_position_history
        if not history:
            return None
        first_time, first_poses = history[0]
        if wanted < first_time - 1.0e-9:
            return None
        if wanted <= first_time + 1.0e-9:
            pose = first_poses.get(key)
            return dict(pose) if pose is not None else None
        low = 1
        high = len(history)
        while low < high:
            middle = (low + high) // 2
            if wanted > history[middle][0] + 1.0e-9:
                low = middle + 1
            else:
                high = middle
        if low >= len(history):
            return None
        right_time, right_poses = history[low]
        left_time, left_poses = history[low - 1]
        left = left_poses.get(key)
        right = right_poses.get(key)
        if left is None or right is None:
            return None
        if (wanted >= right_time - 1.0e-9 or
                right_time <= left_time + 1.0e-9):
            return dict(right)
        progress = max(0.0, min(
            (wanted - left_time) / (right_time - left_time), 1.0))
        result = dict(left)
        for name in ('x', 'y', 'z'):
            result[name] = (_number(left.get(name)) +
                            (_number(right.get(name)) -
                             _number(left.get(name))) * progress)
        for name in ('yaw', 'pitch', 'roll', 'turret_yaw', 'gun_pitch'):
            result[name] = (_number(left.get(name)) + _angle_delta(
                _number(left.get(name)),
                _number(right.get(name))) * progress)
        # Discrete descriptor state changes at its recorded sample, never
        # halfway through the preceding interval.
        result['siege_state'] = left.get('siege_state', 0)
        return result

    def _prune_projectile_position_history(self, states=None):
        if not self._projectile_position_history or self._projectiles is None:
            return
        if states is None:
            states = self._projectiles.snapshot()
        if not states:
            # `_sample_projectile_positions` already owns the exact 21-second
            # lifetime cap. An idle worker needs pre-launch history for a
            # delayed canonical shot. Collapse only a truly inactive observer.
            if (not self._projectile_is_authority() and
                    not self._projectile_visual_meta):
                self._projectile_position_history = \
                    self._projectile_position_history[-1:]
            return
        floor = min(float(state['cursor_time']) for state in states)
        while (len(self._projectile_position_history) > 2 and
               self._projectile_position_history[1][0] <= floor):
            self._projectile_position_history.pop(0)

    def _clear_projectile_spatial_index(self):
        self._projectile_spatial_bins = None
        self._projectile_spatial_fallback_keys = frozenset()
        self._projectile_spatial_records_container = None
        self._projectile_spatial_records_revision = None
        self._projectile_spatial_records = {}
        self._projectile_spatial_order = {}
        self._projectile_spatial_floor = None
        self._projectile_spatial_ceiling = None

    @staticmethod
    def _projectile_spatial_cell(value):
        coordinate = float(value)
        if coordinate != coordinate or abs(coordinate) == float('inf'):
            raise ValueError('projectile spatial coordinate is not finite')
        return int(math.floor(
            coordinate / PROJECTILE_SPATIAL_CELL_METRES))

    def _build_projectile_spatial_bins(self, states, now,
                                       maximum_chords=None):
        """Index a conservative target envelope for one synchronous advance.

        The oldest active projectile cursor may lag the render frame by much
        more than one ordinary physics step.  Index every retained target pose
        across that complete interval so delayed chords cannot lose a target
        which has since moved to another cell.  Any incomplete or excessive
        envelope remains an explicit full-scan candidate.
        """
        self._clear_projectile_spatial_index()
        if not states:
            return False
        try:
            floor = min(float(state['cursor_time']) for state in states)
            ceiling = float(now)
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
        history = self._projectile_position_history
        items = tuple(self._records.items())
        keys = tuple(
            key for key, record in items
            if not (self._worker_mode and record.get('local')))
        estimated_chords = None
        if maximum_chords is not None:
            estimated_chords = 0
            try:
                for state in states:
                    remaining = max(
                        0.0, ceiling - float(state['cursor_time']))
                    estimated_chords += int(math.ceil(max(
                        0.0, remaining / PROJECTILE_MAX_SUBSTEP_SECONDS -
                        1.0e-12)))
                estimated_chords = min(
                    max(0, int(maximum_chords)), estimated_chords)
            except (KeyError, TypeError, ValueError, OverflowError):
                return False
            upper_build_pose_reads = len(keys) * (len(history) + 2)
            full_record_scans = estimated_chords * len(items)
            if (estimated_chords <= 0 or
                    upper_build_pose_reads >= full_record_scans):
                return False
        previous_time = None
        middle_samples = []
        try:
            for sample_time, poses in history:
                sample_time = float(sample_time)
                if (sample_time != sample_time or
                        abs(sample_time) == float('inf') or
                        (previous_time is not None and
                         sample_time < previous_time - 1.0e-9)):
                    return False
                previous_time = sample_time
                if (sample_time > floor + 1.0e-9 and
                        sample_time < ceiling - 1.0e-9):
                    middle_samples.append((sample_time, poses))
        except (TypeError, ValueError, OverflowError):
            return False
        if (not history or floor != floor or ceiling != ceiling or
                abs(floor) == float('inf') or
                abs(ceiling) == float('inf') or
                floor > ceiling + 1.0e-9 or
                floor < float(history[0][0]) - 1.0e-9 or
                ceiling > float(history[-1][0]) + 1.0e-9):
            return False
        if maximum_chords is not None:
            build_pose_reads = len(keys) * (len(middle_samples) + 2)
            full_record_scans = estimated_chords * len(items)
            if (estimated_chords <= 0 or
                    build_pose_reads >= full_record_scans):
                return False
        bounds = {}
        fallback = set()
        try:
            for key in keys:
                first = self._projectile_historic_pose(key, floor)
                last = self._projectile_historic_pose(key, ceiling)
                if first is None or last is None:
                    fallback.add(key)
                    continue
                first_position = _xyz(first)
                last_position = _xyz(last)
                values = (
                    float(first_position[0]), float(first_position[2]),
                    float(last_position[0]), float(last_position[2]))
                if any(value != value or abs(value) == float('inf')
                       for value in values):
                    fallback.add(key)
                    continue
                bounds[key] = [
                    min(values[0], values[2]),
                    min(values[1], values[3]),
                    max(values[0], values[2]),
                    max(values[1], values[3]),
                ]
            for unused_sample_time, poses in middle_samples:
                for key in keys:
                    if key in fallback:
                        continue
                    pose = poses.get(key)
                    if pose is None:
                        fallback.add(key)
                        bounds.pop(key, None)
                        continue
                    position = _xyz(pose)
                    x = float(position[0])
                    z = float(position[2])
                    if (x != x or z != z or abs(x) == float('inf') or
                            abs(z) == float('inf')):
                        fallback.add(key)
                        bounds.pop(key, None)
                        continue
                    target_bounds = bounds[key]
                    target_bounds[0] = min(target_bounds[0], x)
                    target_bounds[1] = min(target_bounds[1], z)
                    target_bounds[2] = max(target_bounds[2], x)
                    target_bounds[3] = max(target_bounds[3], z)
        except (AttributeError, KeyError, TypeError, ValueError,
                OverflowError):
            return False
        bins = {}
        entries = 0
        padding = (PROJECTILE_BROADPHASE_RADIUS +
                   PROJECTILE_SPATIAL_EPSILON)
        for key, target_bounds in bounds.items():
            try:
                minimum_x = self._projectile_spatial_cell(
                    target_bounds[0] - padding)
                minimum_z = self._projectile_spatial_cell(
                    target_bounds[1] - padding)
                maximum_x = self._projectile_spatial_cell(
                    target_bounds[2] + padding)
                maximum_z = self._projectile_spatial_cell(
                    target_bounds[3] + padding)
                cell_count = ((maximum_x - minimum_x + 1) *
                              (maximum_z - minimum_z + 1))
            except (TypeError, ValueError, OverflowError):
                fallback.add(key)
                continue
            if (cell_count <= 0 or
                    cell_count > PROJECTILE_SPATIAL_MAX_TARGET_CELLS):
                fallback.add(key)
                continue
            entries += cell_count
            if entries > PROJECTILE_SPATIAL_MAX_TOTAL_CELLS:
                self._clear_projectile_spatial_index()
                return False
            for cell_x in range(minimum_x, maximum_x + 1):
                for cell_z in range(minimum_z, maximum_z + 1):
                    bins.setdefault((cell_x, cell_z), set()).add(key)
        self._projectile_spatial_bins = bins
        self._projectile_spatial_fallback_keys = frozenset(fallback)
        self._projectile_spatial_records_container = self._records
        self._projectile_spatial_records_revision = self._records_revision
        self._projectile_spatial_records = dict(items)
        self._projectile_spatial_order = dict(
            (key, index) for index, (key, unused_record) in enumerate(items))
        self._projectile_spatial_floor = floor
        self._projectile_spatial_ceiling = ceiling
        return True

    def _projectile_chord_records(self, start, end,
                                   absolute_start, absolute_end):
        """Return a conservative spatial candidate set or every record."""
        bins = self._projectile_spatial_bins
        if bins is None:
            return tuple(self._records.items())
        if (self._records is not self._projectile_spatial_records_container or
                self._records_revision !=
                self._projectile_spatial_records_revision):
            return tuple(self._records.items())
        try:
            chord_start = float(absolute_start)
            chord_end = float(absolute_end)
            if (chord_start != chord_start or chord_end != chord_end or
                    abs(chord_start) == float('inf') or
                    abs(chord_end) == float('inf') or
                    chord_start > chord_end + 1.0e-9 or
                    chord_start < self._projectile_spatial_floor - 1.0e-9 or
                    chord_end > self._projectile_spatial_ceiling + 1.0e-9):
                return tuple(self._records.items())
            minimum_x = self._projectile_spatial_cell(
                min(float(start[0]), float(end[0])))
            minimum_z = self._projectile_spatial_cell(
                min(float(start[2]), float(end[2])))
            maximum_x = self._projectile_spatial_cell(
                max(float(start[0]), float(end[0])))
            maximum_z = self._projectile_spatial_cell(
                max(float(start[2]), float(end[2])))
            cell_count = ((maximum_x - minimum_x + 1) *
                          (maximum_z - minimum_z + 1))
            if (cell_count <= 0 or
                    cell_count > PROJECTILE_SPATIAL_MAX_QUERY_CELLS):
                return tuple(self._records.items())
        except (TypeError, ValueError, OverflowError):
            return tuple(self._records.items())
        keys = set(self._projectile_spatial_fallback_keys)
        for cell_x in range(minimum_x, maximum_x + 1):
            for cell_z in range(minimum_z, maximum_z + 1):
                keys.update(bins.get((cell_x, cell_z), ()))
        order = self._projectile_spatial_order
        records = self._projectile_spatial_records
        ordered = sorted(
            (key for key in keys if key in records),
            key=lambda key: order[key])
        return tuple((key, records[key]) for key in ordered)

    def _advance_projectiles(self, now):
        self._projectile_perf = {}
        self._projectile_scan_count = 0
        self._projectile_candidate_count = 0
        if self._projectiles is None:
            return False
        self._flush_pending_projectile_resolutions()
        previous = self._projectile_target_positions
        states = self._projectiles.snapshot()
        current_poses = self._projectile_record_poses()
        current = dict(
            (key, _xyz(pose)) for key, pose in current_poses.items())
        if (len(self._projectiles) or self._projectile_visual_meta or
                self._projectile_is_authority()):
            self._sample_projectile_positions(now, current_poses)
            self._prune_projectile_position_history(states)
        if not self._projectile_is_authority():
            self._projectile_target_positions = current
            return False
        self._projectile_previous_positions = previous
        self._projectile_current_positions = current
        self._projectile_frame_start = self._projectiles.now
        self._projectile_frame_end = max(
            self._projectile_frame_start, float(now))
        active = len(states)
        sustainable = self._projectiles.sustainable_chord_budget(
            PROJECTILE_SUSTAIN_SECONDS)
        chord_budget = min(
            PROJECTILE_MAX_CHORDS_PER_FRAME,
            max(PROJECTILE_CHORDS_PER_FRAME, active * 2, sustainable))
        advance_start = _PROFILE_CLOCK()
        self._projectile_historic_pose_cache = {}
        try:
            self._build_projectile_spatial_bins(
                states, now, maximum_chords=chord_budget)
            advanced = self._projectiles.advance(
                now, self._projectile_chord, self._projectile_terminal,
                maximum_chords=chord_budget)
        finally:
            self._clear_projectile_spatial_index()
            self._projectile_historic_pose_cache = None
        advance_seconds = max(0.0, _PROFILE_CLOCK() - advance_start)
        metrics = self._projectiles.last_advance_metrics()
        self._projectile_perf = {
            'active': metrics.get('active', active),
            'chords': metrics.get('chords', 0),
            'debt': metrics.get('debt_after', 0.0),
            'advance': advance_seconds,
            'terminals': metrics.get('terminals', 0),
            'scans': self._projectile_scan_count,
            'candidates': self._projectile_candidate_count,
        }
        self._prune_projectile_position_history()
        self._projectile_target_positions = current
        if now >= self._next_projectile_progress_time:
            self._next_projectile_progress_time = (
                now + PROJECTILE_PROGRESS_SECONDS)
            self._publish_projectile_progress()
        return advanced

    def _projectile_descriptor_at_pose(self, target, pose):
        descriptor = getattr(target, 'typeDescriptor', None)
        if descriptor is None or not bool(getattr(
                descriptor, 'hasSiegeMode', False)):
            return descriptor
        default = getattr(descriptor, 'defaultVehicleDescr', None)
        siege = getattr(descriptor, 'siegeVehicleDescr', None)
        if default is None or siege is None:
            return descriptor
        siege_states = getattr(
            getattr(self._runtime, 'constants', None),
            'VEHICLE_SIEGE_STATE', None)
        enabled = getattr(siege_states, 'ENABLED', 2)
        return siege if int(pose.get('siege_state', 0)) == enabled else default

    @staticmethod
    def _projectile_pitch_hull_aiming(descriptor):
        return bool(descriptor is not None and getattr(
            descriptor, 'isPitchHullAimingAvailable', False))

    @staticmethod
    def _projectile_pose_interval_travel(first, second):
        """Return a conservative composed-component angular travel bound."""
        hull = sum(abs(_angle_delta(
            _number(first.get(name)), _number(second.get(name))))
            for name in ('yaw', 'pitch', 'roll'))
        turret = hull + abs(_angle_delta(
            _number(first.get('turret_yaw')),
            _number(second.get('turret_yaw'))))
        gun = turret + abs(_angle_delta(
            _number(first.get('gun_pitch')),
            _number(second.get('gun_pitch'))))
        return max(hull, turret, gun)

    def _projectile_pose_sweep_fractions(self, key, absolute_start,
                                         absolute_end):
        """Split at recorded samples, then enforce the component angle cap."""
        start = float(absolute_start)
        end = float(absolute_end)
        duration = end - start
        if duration <= 1.0e-12:
            return (0.0, 1.0)
        history = self._projectile_position_history
        boundaries = [start]
        low = 0
        high = len(history)
        while low < high:
            middle = (low + high) // 2
            if history[middle][0] <= start + 1.0e-9:
                low = middle + 1
            else:
                high = middle
        index = low
        while index < len(history):
            sample_time = float(history[index][0])
            if sample_time >= end - 1.0e-9:
                break
            boundaries.append(sample_time)
            index += 1
        boundaries.append(end)
        intervals = []
        total_travel = 0.0
        siege_boundaries = set()
        for index in range(len(boundaries) - 1):
            interval_start = boundaries[index]
            interval_end = boundaries[index + 1]
            first = self._projectile_historic_pose(key, interval_start)
            second = self._projectile_historic_pose(key, interval_end)
            if first is None or second is None:
                return None
            travel = self._projectile_pose_interval_travel(first, second)
            intervals.append((interval_start, interval_end, travel))
            total_travel += travel
            if (index < len(boundaries) - 2 and
                    int(first.get('siege_state', 0)) !=
                    int(second.get('siege_state', 0))):
                siege_boundaries.add(interval_end)
        angular_steps = max(1, int(math.ceil(
            total_travel / PROJECTILE_POSE_MAX_ANGLE_STEP - 1.0e-12)))
        if angular_steps > PROJECTILE_POSE_MAX_SWEEP_STEPS:
            return None
        absolute_boundaries = set(siege_boundaries)
        if total_travel > 1.0e-12:
            threshold = PROJECTILE_POSE_MAX_ANGLE_STEP
            travelled = 0.0
            for interval_start, interval_end, travel in intervals:
                if travel <= 1.0e-12:
                    continue
                while (threshold < total_travel - 1.0e-12 and
                       threshold <= travelled + travel + 1.0e-12):
                    ratio = max(0.0, min(
                        1.0, (threshold - travelled) / travel))
                    absolute_boundaries.add(
                        interval_start +
                        (interval_end - interval_start) * ratio)
                    threshold += PROJECTILE_POSE_MAX_ANGLE_STEP
                travelled += travel
        fractions = [0.0]
        for absolute in sorted(absolute_boundaries):
            fraction = (absolute - start) / duration
            if fraction > 1.0e-9 and fraction < 1.0 - 1.0e-9:
                fractions.append(fraction)
        fractions.append(1.0)
        if len(fractions) - 1 > PROJECTILE_POSE_MAX_SWEEP_STEPS:
            return None
        return tuple(fractions)

    def _projectile_frozen_target(self, target, pose):
        """Build one target view whose outer and inner geometry agree."""
        descriptor = self._projectile_descriptor_at_pose(target, pose)
        matrix_factory = getattr(self._runtime.math, 'Matrix', None)
        if descriptor is None or not callable(matrix_factory):
            return None
        matrix = matrix_factory()
        matrix.setRotateYPR((
            _number(pose.get('yaw')),
            _number(pose.get('pitch')),
            _number(pose.get('roll'))))
        position = self._vector(_xyz(pose))
        matrix.translation = position
        appearance = _ProjectileCollisionAppearance(
            self._runtime.math, descriptor, pose.get('turret_yaw', 0.0),
            pose.get('gun_pitch', 0.0))
        return _ProjectileCollisionTarget(
            target, descriptor, matrix, position, appearance,
            self._runtime.math)

    def _projectile_vehicle_collisions(self, record, target, start, end,
                                       pose=None):
        """Return retail ABI collisions plus private world-normal evidence."""
        if pose is not None:
            descriptor = self._projectile_descriptor_at_pose(target, pose)
            if self._projectile_pitch_hull_aiming(descriptor):
                # #1513 uses separate bodyMatrix and groundPlacingMatrix bases
                # for these vehicles. The LAN pose has neither correction, so
                # reject this sample instead of fabricating a single-matrix
                # Strv pose or falling back to mixed live render state.
                record['projectile_collision_pose_boundary'] = \
                    'pitch_hull_body_ground_unavailable'
                return None, None
            if bool(getattr(target, 'isTurretDetached', False)):
                # The 0.9.22 LAN pose contract has no historical attachment
                # field. A currently detached turret is therefore not safe to
                # reinterpret at an older projectile cursor.
                record['projectile_collision_pose_boundary'] = \
                    'turret_attachment_history_unavailable'
                return None, None
            else:
                record.pop('projectile_collision_pose_boundary', None)
                frozen_target = self._projectile_frozen_target(target, pose)
                if frozen_target is not None:
                    evidence = tuple(_collide_vehicle_evidence_at_matrix(
                        frozen_target, frozen_target.matrix, start, end,
                        self._runtime.math) or ())
                    return (tuple(item.collision for item in evidence), evidence)
            # A historical query may never borrow a live render matrix or live
            # aim as a fallback: that would combine two different instants in
            # one armour sample.
            record['projectile_collision_pose_boundary'] = \
                'historic_component_matrix_unavailable'
            return None, None
        body_matrix = None
        chassis_matrix = None
        if record.get('local') and self._local_matrix is not None:
            body_matrix = self._local_body_pose()
            chassis_matrix = self._local_matrix
        elif record.get('native_remote'):
            body_matrix, chassis_matrix = \
                self._projectile_vehicle_matrices(record, target)
        else:
            body_matrix = getattr(target, 'matrix', None)
        if body_matrix is not None:
            evidence = tuple(_collide_vehicle_evidence_at_matrix(
                target, body_matrix, start, end, self._runtime.math,
                chassis_matrix=chassis_matrix) or ())
            return (tuple(item.collision for item in evidence), evidence)
        return (tuple(target.collideSegmentExt(start, end) or ()), ())

    def _projectile_chord(self, state, start, end,
                          absolute_start, absolute_end):
        manager_key = state.get('key')
        projectile_id = (manager_key[0]
                         if isinstance(manager_key, tuple) else manager_key)
        meta = self._projectile_meta.get(projectile_id)
        if meta is None:
            return {'reason': 'callback_error', 'fraction': 0.0}
        chord_length = math.sqrt(sum(
            (float(end[index]) - float(start[index])) ** 2
            for index in range(3)))
        if chord_length <= 0.000001:
            return None
        history = self._projectile_position_history
        history_covers_chord = bool(
            history and
            float(absolute_start) >= history[0][0] - 1.0e-9 and
            float(absolute_end) <= history[-1][0] + 1.0e-9)
        direction_tuple = tuple(
            (float(end[index]) - float(start[index])) / chord_length
            for index in range(3))
        direction = self._vector(direction_tuple)
        source_key = '%s:%s' % (
            meta['shooter_kind'], meta['shooter_id'])
        nearest_key = None
        nearest_collisions = None
        nearest_evidence = None
        nearest_fraction = 1.0
        nearest_query = None
        nearest_collision_pose = None
        broadphase_sq = PROJECTILE_BROADPHASE_RADIUS ** 2
        for key, record in self._projectile_chord_records(
                start, end, absolute_start, absolute_end):
            self._projectile_scan_count += 1
            if key == source_key or record.get('tombstone'):
                continue
            if not record.get('ready'):
                continue
            if self._worker_mode and record.get('local'):
                continue
            if not history_covers_chord:
                # Static scenery can still be resolved without vehicle pose
                # history. A live vehicle target cannot: retire the projectile
                # instead of silently treating that target as absent.
                target = self._server_entity(record.get('engine_id'))
                if target is None or not getattr(target, 'isStarted', False):
                    continue
                record['projectile_collision_pose_boundary'] = \
                    'historic_pose_unavailable'
                return {'reason': 'callback_error', 'fraction': 0.0}
            target_at_start = self._projectile_historic_pose(
                key, absolute_start)
            target_at_end = self._projectile_historic_pose(
                key, absolute_end)
            target_at_contact = self._projectile_historic_pose(
                key, (float(absolute_start) + float(absolute_end)) * 0.5)
            if (target_at_start is None or target_at_end is None or
                    target_at_contact is None):
                target = self._server_entity(record.get('engine_id'))
                if target is None or not getattr(target, 'isStarted', False):
                    continue
                record['projectile_collision_pose_boundary'] = \
                    'historic_pose_unavailable'
                return {'reason': 'callback_error', 'fraction': 0.0}
            # This conservative coarse pass avoids constructing native target
            # objects for distant records. The exact pass below repeats it for
            # every angularly bounded subsegment.
            contact_position = _xyz(target_at_contact)
            start_position = _xyz(target_at_start)
            end_position = _xyz(target_at_end)
            adjusted_start = tuple(
                float(start[index]) + float(contact_position[index]) -
                float(start_position[index]) for index in range(3))
            adjusted_end = tuple(
                float(end[index]) + float(contact_position[index]) -
                float(end_position[index]) for index in range(3))
            if not point_in_expanded_segment_bounds(
                    contact_position, adjusted_start, adjusted_end,
                    PROJECTILE_BROADPHASE_RADIUS):
                continue
            if point_segment_distance_sq(
                    contact_position, adjusted_start,
                    adjusted_end) > broadphase_sq:
                continue
            target = self._server_entity(record.get('engine_id'))
            if target is None or not getattr(target, 'isStarted', False):
                continue
            descriptor = self._projectile_descriptor_at_pose(
                target, target_at_contact)
            unsupported_boundary = None
            if self._projectile_pitch_hull_aiming(descriptor):
                unsupported_boundary = \
                    'pitch_hull_body_ground_unavailable_live_fallback'
            elif bool(getattr(target, 'isTurretDetached', False)):
                unsupported_boundary = \
                    'turret_attachment_history_unavailable_live_fallback'
            if unsupported_boundary is not None:
                # Preserve the pre-history-pose gameplay path for unsupported
                # #1513 contracts. Its live orientation is imperfect, but it
                # keeps these vehicles hittable while explicitly recording
                # the body/ground or attachment evidence boundary.
                record['projectile_collision_pose_boundary'] = \
                    unsupported_boundary
                current_position = self._projectile_current_positions.get(key)
                if current_position is None:
                    current_position = _xyz(getattr(
                        target, 'position', target_at_contact))
                adjusted_start = tuple(
                    float(start[index]) + float(current_position[index]) -
                    float(start_position[index]) for index in range(3))
                adjusted_end = tuple(
                    float(end[index]) + float(current_position[index]) -
                    float(end_position[index]) for index in range(3))
                self._projectile_candidate_count += 1
                query_start = self._vector(adjusted_start)
                query_end = self._vector(adjusted_end)
                collisions, evidence = self._projectile_vehicle_collisions(
                    record, target, query_start, query_end)
                if not collisions:
                    continue
                collisions = tuple(collisions)
                nearest = min(collisions, key=lambda item: float(item.dist))
                query_length = (query_end - query_start).length
                if query_length <= 0.000001:
                    continue
                fraction = max(
                    0.0, min(1.0, float(nearest.dist) / query_length))
                if fraction < nearest_fraction:
                    nearest_key = key
                    nearest_collisions = collisions
                    nearest_evidence = evidence
                    nearest_fraction = fraction
                    nearest_query = (query_start, query_end)
                    nearest_collision_pose = None
                continue
            sweep_fractions = self._projectile_pose_sweep_fractions(
                key, absolute_start, absolute_end)
            if sweep_fractions is None:
                record['projectile_collision_pose_boundary'] = \
                    'angular_sweep_limit_exceeded'
                return {'reason': 'callback_error', 'fraction': 0.0}
            candidate_segments = []
            for segment_index in range(len(sweep_fractions) - 1):
                start_fraction = sweep_fractions[segment_index]
                end_fraction = sweep_fractions[segment_index + 1]
                segment_absolute_start = (
                    float(absolute_start) +
                    (float(absolute_end) - float(absolute_start)) *
                    start_fraction)
                segment_absolute_end = (
                    float(absolute_start) +
                    (float(absolute_end) - float(absolute_start)) *
                    end_fraction)
                segment_absolute_contact = (
                    segment_absolute_start + segment_absolute_end) * 0.5
                segment_target_start = self._projectile_historic_pose(
                    key, segment_absolute_start)
                segment_target_end = self._projectile_historic_pose(
                    key, segment_absolute_end)
                segment_target_contact = self._projectile_historic_pose(
                    key, segment_absolute_contact)
                if (segment_target_start is None or
                        segment_target_end is None or
                        segment_target_contact is None):
                    record['projectile_collision_pose_boundary'] = \
                        'historic_pose_unavailable'
                    return {'reason': 'callback_error', 'fraction': 0.0}
                projectile_start = lerp3(start, end, start_fraction)
                projectile_end = lerp3(start, end, end_fraction)
                segment_contact_position = _xyz(segment_target_contact)
                segment_start_position = _xyz(segment_target_start)
                segment_end_position = _xyz(segment_target_end)
                segment_adjusted_start = tuple(
                    float(projectile_start[index]) +
                    float(segment_contact_position[index]) -
                    float(segment_start_position[index])
                    for index in range(3))
                segment_adjusted_end = tuple(
                    float(projectile_end[index]) +
                    float(segment_contact_position[index]) -
                    float(segment_end_position[index])
                    for index in range(3))
                if not point_in_expanded_segment_bounds(
                        segment_contact_position, segment_adjusted_start,
                        segment_adjusted_end, PROJECTILE_BROADPHASE_RADIUS):
                    continue
                if point_segment_distance_sq(
                        segment_contact_position, segment_adjusted_start,
                        segment_adjusted_end) > broadphase_sq:
                    continue
                candidate_segments.append((
                    start_fraction, end_fraction,
                    segment_adjusted_start, segment_adjusted_end,
                    segment_target_contact))
            if not candidate_segments:
                continue
            self._projectile_candidate_count += 1
            for (segment_start_fraction, segment_end_fraction,
                 segment_start, segment_end,
                 segment_collision_pose) in candidate_segments:
                query_start = self._vector(segment_start)
                query_end = self._vector(segment_end)
                collisions, evidence = self._projectile_vehicle_collisions(
                    record, target, query_start, query_end,
                    segment_collision_pose)
                if collisions is None:
                    return {'reason': 'callback_error', 'fraction': 0.0}
                if not collisions:
                    continue
                collisions = tuple(collisions)
                nearest = min(collisions, key=lambda item: float(item.dist))
                query_length = (query_end - query_start).length
                if query_length <= 0.000001:
                    continue
                local_fraction = max(
                    0.0, min(1.0, float(nearest.dist) / query_length))
                fraction = (segment_start_fraction +
                            (segment_end_fraction - segment_start_fraction) *
                            local_fraction)
                if fraction < nearest_fraction:
                    nearest_key = key
                    nearest_collisions = collisions
                    nearest_evidence = evidence
                    nearest_fraction = fraction
                    nearest_query = (query_start, query_end)
                    nearest_collision_pose = segment_collision_pose

        scene_end_tuple = lerp3(start, end, nearest_fraction)
        if self._projectile_destructible_context is not None:
            raise RuntimeError('nested projectile destructible context')
        self._projectile_destructible_context = projectile_id
        try:
            scene = self._resolve_shot_scene(
                self._vector(start), self._vector(scene_end_tuple), direction,
                self._projectile_shot(meta),
                penetration_factor=meta.get('penetration_factor'),
                initial_piercing_loss=meta.get('piercing_loss', 0.0),
                projectile_state=state)
        finally:
            self._projectile_destructible_context = None
        meta['piercing_loss'] = scene['piercing_loss']
        meta['penetration_factor'] = scene.get(
            'penetration_factor', meta.get('penetration_factor'))
        world_distance = scene['world_distance']
        cap_distance = chord_length * nearest_fraction
        world_blocks = (
            world_distance < 99999.0 and
            (nearest_key is None or
             bool(scene.get('stopped_by_destructible')) or
             cap_distance >
             world_distance + _SHOT_OCCLUSION_EPSILON))
        if world_blocks:
            fraction = max(
                0.0, min(1.0, world_distance / chord_length))
            self._projectile_terminal_data[projectile_id] = {
                'impact': lerp3(start, end, fraction),
                'target_key': None,
                'collisions': None,
                'query': None,
                'piercing_loss': meta['piercing_loss'],
                'penetration_factor': meta.get('penetration_factor'),
            }
            return {'reason': 'impact', 'fraction': fraction}
        if nearest_key is not None:
            self._projectile_terminal_data[projectile_id] = {
                'impact': lerp3(start, end, nearest_fraction),
                'target_key': nearest_key,
                'collisions': nearest_collisions,
                'collision_evidence': nearest_evidence,
                'query': nearest_query,
                'collision_pose': nearest_collision_pose,
                'piercing_loss': meta['piercing_loss'],
                'penetration_factor': meta.get('penetration_factor'),
            }
            return {'reason': 'impact', 'fraction': nearest_fraction}
        return None

    def _projectile_shot(self, meta):
        frozen = meta.get('source_shot')
        source = self._projectile_source_entity(meta)
        descriptor = (getattr(source, 'typeDescriptor', None)
                      if source is not None else None)
        if descriptor is None:
            descriptor = self._projectile_source_descriptor(meta)
        descriptor_shot = (self._descriptor_shot(
            descriptor, meta.get('shell_index'))
            if descriptor is not None else None)
        if not isinstance(frozen, dict):
            return descriptor_shot or {}
        # Physical values are immutable launch evidence.  Retain only
        # descriptor-owned cosmetic keys so stock tracers/explosions still
        # render when a disconnected shooter's fitted gun is unavailable.
        result = dict(frozen)
        shell = dict(frozen.get('shell') or {})
        descriptor_shell = _field(descriptor_shot, 'shell', None)
        for name in ('compactDescr', 'effectsIndex', 'isTracer'):
            value = _field(descriptor_shell, name, None)
            if value is not None:
                shell[name] = value
        result['shell'] = shell
        return result

    @staticmethod
    def _vehicle_trace(shot, query_start, query_end, collisions):
        """Cap vehicle material/module tracing at ten shell calibres.

        The historical limit starts at the first vehicle material, including
        tracks and spaced armour.  Keeping the original query origin lets the
        native collision distances and reconstructed internal hit boxes share
        one physical distance axis.
        """
        collisions = tuple(collisions or ())
        delta = query_end - query_start
        length = float(delta.length)
        if not collisions or length <= 0.000001:
            return collisions, query_start, query_start
        try:
            first = min(float(collision.dist) for collision in collisions)
        except (AttributeError, TypeError, ValueError):
            raise TypeError('#1513 collision contains an invalid distance')
        first = max(0.0, min(length, first))
        legacy = combat_rules.legacy_shot(shot)
        caliber = _number((legacy.get('shell') or {}).get('caliber'), 0.0)
        trace_distance = first + max(0.0, caliber) / 100.0
        limited = tuple(
            collision for collision in collisions
            if float(collision.dist) <= trace_distance + 0.000001)
        direction = delta
        direction.normalise()
        return (limited, query_start,
                query_start + direction.scale(trace_distance))

    def _projectile_source_descriptor(self, meta):
        descriptor = meta.get('source_descriptor')
        if descriptor is not None:
            return descriptor
        vehicle = meta.get('source_vehicle')
        if not vehicle:
            return None
        try:
            descriptor = self._resolve_descriptor(vehicle)
        except Exception:
            return None
        meta['source_descriptor'] = descriptor
        return descriptor

    def _projectile_source_entity(self, meta):
        key = '%s:%s' % (meta.get('shooter_kind'), meta.get('shooter_id'))
        record = self._records.get(key)
        if record is None:
            return None
        return self._server_entity(record.get('engine_id'))

    def _projectile_damage_sticker(self, record, target, shot, start, end,
                                   collisions, result, historic=False):
        """Encode one direct hit against the exact sampled component pose."""
        try:
            shell = _field(shot, 'shell', None)
            effects_index = _field(shell, 'effectsIndex', None)
            effects_descr = self._runtime.vehicles.g_cache.shotEffects[
                effects_index]
            target_stickers = _field(
                effects_descr, 'targetStickers', {}) or {}
            sticker_key = ('armorPierced' if int(result) == 2 else
                           'armorResisted')
            sticker_id = _field(target_stickers, sticker_key, None)
            if (isinstance(sticker_id, bool) or
                    not isinstance(sticker_id, _INTEGER_TYPES) or
                    not 0 <= sticker_id <= 255):
                return None
            nearest = min(collisions, key=lambda item: float(item.dist))
            component_name = nearest.compName
            chassis_matrix = None
            if historic:
                body_matrix = getattr(target, 'matrix', None)
            elif record.get('local') and self._local_matrix is not None:
                body_matrix = self._local_body_pose()
                chassis_matrix = self._local_matrix
            elif record.get('native_remote'):
                body_matrix, chassis_matrix = \
                    self._projectile_vehicle_matrices(record, target)
            else:
                body_matrix = getattr(target, 'matrix', None)
            encoded = encode_damage_sticker(
                target, body_matrix, start, end, component_name,
                sticker_id, self._runtime.math,
                chassis_matrix=chassis_matrix)
            if encoded is None:
                return None
            from VehicleEffects import DamageFromShotDecoder
            decoded_component, decoded_sticker, decoded_start, decoded_end = \
                DamageFromShotDecoder.decodeSegment(
                    encoded, target.typeDescriptor)
            component = getattr(
                target.typeDescriptor, decoded_component, None)
            tester = _field(component, 'hitTester', None)
            local_hit_test = getattr(tester, 'localHitTest', None)
            if (decoded_sticker != sticker_id or
                    decoded_start is None or decoded_end is None or
                    decoded_start == decoded_end or
                    not callable(local_hit_test) or
                    not local_hit_test(decoded_start, decoded_end)):
                return None
            return encoded
        except Exception:
            # A missing cosmetic contract must never discard admitted damage
            # or turn an otherwise terminal projectile into an expiration.
            return None

    def _projectile_effect(self, record, damage, result, impact,
                           critical, hull_damage, critical_delta,
                           target_position=None, damage_sticker=None):
        target_kind = record.get('kind')
        if target_kind == 'human':
            target_kind = 'player'
        if target_kind not in ('player', 'bot'):
            raise RuntimeError('projectile target kind is invalid')
        effect = {
            'target_kind': target_kind,
            'target_id': int(record.get('network_id')),
            'damage': max(0, int(damage or 0)),
            'shot_result': max(0, min(2, int(result or 0))),
            'x': float(impact[0]), 'y': float(impact[1]),
            'z': float(impact[2]),
        }
        if target_position is not None:
            effect.update({
                'target_x': float(target_position[0]),
                'target_y': float(target_position[1]),
                'target_z': float(target_position[2]),
            })
        if damage_sticker is not None:
            effect['damage_sticker'] = damage_sticker
        if isinstance(critical, dict):
            effect['critical'] = critical
            effect.update(self._critical_proposal_contract(
                record, critical, hull_damage, critical_delta))
        return effect

    def _projectile_direct_effect(self, meta, state, terminal_data):
        target_key = terminal_data.get('target_key')
        record = self._records.get(target_key)
        source = self._projectile_source_entity(meta)
        shot = self._projectile_shot(meta)
        if record is None or not shot:
            return None
        target = self._server_entity(record.get('engine_id'))
        collisions = terminal_data.get('collisions')
        collision_evidence = tuple(
            terminal_data.get('collision_evidence') or ())
        query = terminal_data.get('query')
        if (target is None or not collisions or query is None or
                not self._record_alive(record, target)):
            return None
        critical_target = target
        collision_pose = terminal_data.get('collision_pose')
        if isinstance(collision_pose, dict):
            critical_target = self._projectile_frozen_target(
                target, collision_pose)
            if critical_target is None:
                return None
        collisions, trace_start, trace_end = self._vehicle_trace(
            shot, query[0], query[1], collisions)
        if not combat_rules.is_he(shot):
            original_length = float((query[1] - query[0]).length)
            trace_length = float((trace_end - trace_start).length)
            if trace_length > original_length + 0.000001:
                extended, extended_evidence = (
                    self._projectile_vehicle_collisions(
                        record, target, query[0], trace_end,
                        collision_pose))
                if extended:
                    collisions, trace_start, trace_end = self._vehicle_trace(
                        shot, query[0], trace_end, tuple(extended))
                    collision_evidence = tuple(extended_evidence)
        factor = terminal_data.get('penetration_factor')
        range_distance = projectile_range_distance(
            state, terminal_data['impact'])
        contact = combat_rules.resolve_armor_contact(
            shot, range_distance, collisions,
            pierce_loss=terminal_data.get('piercing_loss', 0.0),
            penetration_factor=factor,
            base_penetration_multiplier=meta.get(
                'base_penetration_multiplier', 1.0))
        result = 1 if contact is None else contact['result']
        terminal_data['armor_contact'] = contact
        terminal_data['world_normal'] = None
        if contact is not None and collision_evidence:
            matching = []
            for evidence in collision_evidence:
                collision = evidence.collision
                if (collision.compName == contact.get('component') and
                        abs(float(collision.dist) -
                            float(contact.get('distance', 0.0))) <= 1.0e-5 and
                        evidence.worldNormal is not None):
                    matching.append(evidence)
            if matching:
                terminal_data['world_normal'] = _xyz(
                    matching[0].worldNormal)
        armor = combat_rules.he_nominal_armor(
            collisions, getattr(critical_target, 'typeDescriptor', None))
        damage_sticker = self._projectile_damage_sticker(
            record, critical_target, shot, trace_start, trace_end,
            collisions, result,
            historic=isinstance(collision_pose, dict))
        damage = combat_rules.damage(shot, result, armor)
        hull_damage = damage
        legacy_shell = combat_rules.legacy_shot(shot).get('shell') or {}
        attacker_id = int(getattr(
            source, 'id', meta.get('shooter_id', 0)))
        deadeye = bool(_field(shot, 'deadeye', False))
        layers = combat_rules.collision_layers(collisions)
        critical = None
        critical_delta = {}
        critical_impact = self._vector(terminal_data['impact'])
        query_delta = query[1] - query[0]
        query_length = float(query_delta.length)
        if query_length > 0.000001:
            contact_distance = None
            if contact is not None:
                contact_distance = contact.get('distance')
            if contact_distance is None:
                contact_distance = min(
                    float(collision.dist) for collision in collisions)
            contact_distance = max(
                0.0, min(query_length, float(contact_distance)))
            query_delta.normalise()
            critical_impact = query[0] + query_delta.scale(contact_distance)
        if int(result) == 0:
            damage = 0
            hull_damage = 0
        self._install_critical_equipment_effects(record, critical_target)
        if int(result) != 0 and combat_rules.is_he(shot):
            damage, critical, critical_delta = (
                critical_damage.propose_explosion(
                    critical_target, layers, critical_impact,
                    trace_end - trace_start, damage, legacy_shell,
                    attacker_id, deadeye=deadeye, with_delta=True))
        elif int(result) != 0:
            damage, critical, critical_delta = critical_damage.propose_direct(
                critical_target, layers, trace_start, trace_end, damage,
                legacy_shell, attacker_id, penetrated=int(result) == 2,
                deadeye=deadeye, with_delta=True)
        critical = self._critical_with_crew_roster(
            critical_target, critical)
        return self._projectile_effect(
            record, damage, result, terminal_data['impact'],
            critical, hull_damage, critical_delta,
            damage_sticker=damage_sticker)

    def _projectile_splash_effects(self, meta, impact, direct_key):
        source = self._projectile_source_entity(meta)
        shot = self._projectile_shot(meta)
        if not shot:
            return []
        radius = combat_rules.he_radius(shot)
        if radius <= 0.0:
            return []
        burst = self._vector(impact)
        legacy_shell = combat_rules.legacy_shot(shot).get('shell') or {}
        effects = []
        for key, record in tuple(self._records.items()):
            if key == direct_key or record.get('tombstone'):
                continue
            if self._worker_mode and record.get('local'):
                continue
            target = self._server_entity(record.get('engine_id'))
            if (target is None or target.typeDescriptor is None or
                    not getattr(target, 'isStarted', False) or
                    not self._record_alive(record, target)):
                continue
            position = _xyz(getattr(
                target, 'position', record.get('state', {})))
            delta = tuple(position[index] - impact[index]
                          for index in range(3))
            distance = math.sqrt(sum(value * value for value in delta))
            if distance > radius:
                continue
            aim = self._vector((
                position[0], position[1] + 1.0, position[2]))
            try:
                if record.get('native_remote'):
                    body_matrix, chassis_matrix = \
                        self._projectile_vehicle_matrices(record, target)
                    collisions = tuple(collide_vehicle_at_matrix(
                        target, body_matrix, burst, aim,
                        self._runtime.math,
                        chassis_matrix=chassis_matrix) or ())
                else:
                    collisions = tuple(
                        target.collideSegmentExt(burst, aim) or ())
                nominal = combat_rules.he_nominal_armor(
                    collisions, target.typeDescriptor)
            except Exception:
                collisions = ()
                nominal = combat_rules.he_hull_armor(
                    target.typeDescriptor)
            damage = combat_rules.he_splash_damage(
                shot, nominal, distance / radius)
            if damage <= 0:
                continue
            hull_damage = damage
            self._install_critical_equipment_effects(record, target)
            damage, critical, critical_delta = (
                critical_damage.propose_explosion(
                    target, combat_rules.collision_layers(collisions),
                    burst, aim - burst, damage, legacy_shell,
                    int(getattr(source, 'id', meta.get('shooter_id', 0))),
                    deadeye=bool(_field(shot, 'deadeye', False)),
                    with_delta=True))
            critical = self._critical_with_crew_roster(target, critical)
            effects.append(self._projectile_effect(
                record, damage, 2, impact, critical, hull_damage,
                critical_delta, position))
            if len(effects) >= 30:
                break
        return effects

    def _projectile_terminal(self, state, terminal):
        manager_key = state.get('key')
        projectile_id = (manager_key[0]
                         if isinstance(manager_key, tuple) else manager_key)
        meta = self._projectile_meta.get(projectile_id)
        if meta is None:
            return False
        data = self._projectile_terminal_data.pop(projectile_id, None)
        reason = terminal.get('reason')
        outcome = ('impact' if reason == 'impact' and data is not None else
                   'miss' if reason == 'max_distance' else 'expired')
        impact = tuple(state.get('position') or meta.get('origin'))
        direct = None
        splash = []
        try:
            if outcome == 'impact':
                impact = tuple(data.get('impact', impact))
                direct = self._projectile_direct_effect(meta, state, data)
                if meta.get('is_he'):
                    splash = self._projectile_splash_effects(
                        meta, impact, data.get('target_key'))
        except Exception:
            # A malformed native collision/proposal cannot be allowed to
            # damage a different target. Retire the ledger entry without
            # effects so another authority never replays the same chord.
            outcome = 'expired'
            direct = None
            splash = []
        hit_vehicle = bool(
            outcome == 'impact' and data is not None and
            data.get('target_key') is not None)
        elapsed = max(0.0, float(state.get('elapsed', 0.0)))
        initial_velocity = tuple(state.get('velocity') or ())
        gravity = tuple(state.get('gravity') or ())
        incoming = None
        if len(initial_velocity) == 3 and len(gravity) == 3:
            incoming = tuple(
                float(initial_velocity[index]) +
                float(gravity[index]) * elapsed
                for index in range(3))
        ricochet = None
        if (outcome == 'impact' and hit_vehicle and direct is not None and
                int(direct.get('shot_result', -1)) == 0 and
                int(meta.get('ricochet_count', 0)) == 0 and
                self._projectile_elapsed_ms(meta, state) <
                int(meta.get('max_time_ms', 0)) and
                float(state.get('distance', 0.0)) <
                float(meta.get('max_distance', 0.0)) and
                data.get('world_normal') is not None):
            shot = self._projectile_shot(meta)
            shell_kind = _field(_field(shot, 'shell', {}), 'kind', None)
            multiplier = combat_rules.first_ricochet_penetration_multiplier(
                shell_kind)
            if incoming is not None:
                reflected = ideal_reflection_velocity(
                    incoming, data['world_normal'])
            else:
                reflected = None
            if multiplier is not None and reflected is not None:
                speed = math.sqrt(sum(value * value for value in reflected))
                if 0.000001 < speed <= lan_protocol.MAX_PROJECTILE_VELOCITY:
                    direction = tuple(value / speed for value in reflected)
                    segment_origin = tuple(
                        impact[index] + direction[index] * 0.002
                        for index in range(3))
                    ricochet = {
                        'state': state,
                        'impact': impact,
                        'segment_origin': segment_origin,
                        'segment_velocity': reflected,
                        'base_penetration_multiplier': multiplier,
                        'direct': direct,
                    }
        wreck_hit = None
        if hit_vehicle and direct is None:
            target_record = self._records.get(data.get('target_key'))
            target = (self._server_entity(target_record.get('engine_id'))
                      if target_record is not None else None)
            if (target_record is not None and
                    not self._record_alive(target_record, target)):
                target_kind = target_record.get('kind')
                if target_kind == 'human':
                    target_kind = 'player'
                if target_kind in ('player', 'bot'):
                    wreck_hit = {
                        'target_kind': target_kind,
                        'target_id': int(target_record.get('network_id')),
                    }
        pending = {
            'state': state, 'outcome': outcome, 'impact': impact,
            'direct': direct, 'splash': splash,
            'hit_vehicle': hit_vehicle, 'wreck_hit': wreck_hit,
        }
        # Retail plays a ground explosion only for a terminal on the world; a
        # vehicle terminal shows the armour-hit family instead.  Record the
        # verdict now; only a retained wreck additionally carries its bounded
        # presentation identity, never a damage proposal.
        meta['hit_vehicle'] = hit_vehicle
        meta['terminal_velocity'] = tuple(
            incoming if incoming is not None else
            state.get('velocity') or ())
        if ricochet is not None:
            meta['pending_ricochet'] = ricochet
            return self._submit_projectile_ricochet(meta)
        meta['pending_resolution'] = pending
        return self._submit_projectile_resolution(meta)

    @staticmethod
    def _projectile_elapsed_ms(meta, state):
        return max(
            int(meta.get('base_checked_ms', 0)),
            int(meta.get('segment_start_time_ms', 0)) +
            int(round(float(state.get('elapsed', 0.0)) * 1000.0)))

    def _submit_projectile_ricochet(self, meta):
        pending = meta.get('pending_ricochet')
        if (pending is None or meta.get('progress_pending') is not None or
                meta.get('awaiting_ricochet') or
                not self._projectile_is_authority()):
            return False
        sender = getattr(self.client, 'send_projectile_ricochet', None)
        if not callable(sender):
            return False
        wire = pending.get('wire')
        if wire is None:
            state = pending['state']
            wire = {
                'authority_epoch': self._projectile_epoch,
                'projectile_id': meta['projectile_id'],
                'base_checked_ms': int(meta.get('base_checked_ms', 0)),
                'resolved_time_ms': self._projectile_elapsed_ms(meta, state),
                'impact': list(pending['impact']),
                'segment_origin': list(pending['segment_origin']),
                'segment_velocity': list(pending['segment_velocity']),
                'base_penetration_multiplier': pending[
                    'base_penetration_multiplier'],
                'direct': copy.deepcopy(pending['direct']),
                'checked_distance': float(state.get('distance', 0.0)),
                'piercing_loss': float(meta.get('piercing_loss', 0.0)),
                'penetration_factor': float(
                    meta.get('penetration_factor', 1.0)),
                'destructibles': copy.deepcopy(
                    list(meta.get('destructibles_pending', ()))),
            }
            pending['wire'] = wire
        sent = sender(
            wire['authority_epoch'], wire['projectile_id'],
            wire['base_checked_ms'], wire['resolved_time_ms'],
            wire['impact'], wire['segment_origin'], wire['segment_velocity'],
            wire['base_penetration_multiplier'], wire['direct'],
            checked_distance=wire['checked_distance'],
            piercing_loss=wire['piercing_loss'],
            penetration_factor=wire['penetration_factor'],
            destructibles=wire['destructibles'])
        if sent:
            meta['awaiting_ricochet'] = True
        return bool(sent)

    def _submit_projectile_resolution(self, meta):
        pending = meta.get('pending_resolution')
        if (pending is None or meta.get('progress_pending') is not None or
                meta.get('awaiting_resolution') or
                not self._projectile_is_authority()):
            return False
        sender = getattr(self.client, 'send_projectile_resolve', None)
        if not callable(sender):
            return False
        wire = pending.get('wire')
        if wire is None:
            state = pending['state']
            base_checked_ms = int(meta.get('base_checked_ms', 0))
            elapsed_ms = self._projectile_elapsed_ms(meta, state)
            wire = {
                'authority_epoch': self._projectile_epoch,
                'projectile_id': meta['projectile_id'],
                'base_checked_ms': base_checked_ms,
                'outcome': pending['outcome'],
                'resolved_time_ms': elapsed_ms,
                'impact': (list(pending['impact'])
                           if pending['outcome'] == 'impact' else None),
                'direct': copy.deepcopy(pending['direct']),
                'splash': copy.deepcopy(pending['splash']),
                'checked_distance': float(state.get('distance', 0.0)),
                'piercing_loss': float(meta.get('piercing_loss', 0.0)),
                'penetration_factor': float(
                    meta.get('penetration_factor', 1.0)),
                'hit_vehicle': bool(pending.get('hit_vehicle')),
                'wreck_hit': copy.deepcopy(pending.get('wreck_hit')),
                'destructibles': copy.deepcopy(
                    list(meta.get('destructibles_pending', ()))),
            }
            pending['wire'] = wire
        sent = sender(
            wire['authority_epoch'], wire['projectile_id'],
            wire['base_checked_ms'], wire['outcome'],
            wire['resolved_time_ms'], wire['impact'], wire['direct'],
            wire['splash'], checked_distance=wire['checked_distance'],
            piercing_loss=wire['piercing_loss'],
            penetration_factor=wire['penetration_factor'],
            hit_vehicle=wire['hit_vehicle'], wreck_hit=wire['wreck_hit'],
            destructibles=wire['destructibles'])
        if sent:
            meta['awaiting_resolution'] = True
        return bool(sent)

    def _flush_pending_projectile_resolutions(self):
        if not self._projectile_is_authority():
            return False
        changed = False
        for meta in tuple(self._projectile_meta.values()):
            if (meta.get('pending_resolution') is not None and
                    not meta.get('awaiting_resolution')):
                changed = self._submit_projectile_resolution(meta) or changed
            if (meta.get('pending_ricochet') is not None and
                    not meta.get('awaiting_ricochet')):
                changed = self._submit_projectile_ricochet(meta) or changed
        return changed

    def _publish_projectile_progress(self):
        if not self._projectile_is_authority():
            return False
        sender = getattr(self.client, 'send_projectile_progress', None)
        if not callable(sender):
            return False
        cursors = []
        active_ids = set()
        for state in self._projectiles.snapshot():
            manager_key = state.get('key')
            projectile_id = (manager_key[0]
                             if isinstance(manager_key, tuple) else
                             manager_key)
            meta = self._projectile_meta.get(projectile_id)
            if meta is None:
                continue
            active_ids.add(meta['projectile_id'])
            pending = meta.get('progress_pending')
            if pending is not None:
                cursors.append(dict(pending))
                continue
            base_checked = int(meta.get('base_checked_ms', 0))
            checked = self._projectile_elapsed_ms(meta, state)
            cursors.append({
                'projectile_id': meta['projectile_id'],
                'base_checked_ms': base_checked,
                'checked_through_ms': min(
                    meta['max_time_ms'], checked),
                'checked_distance': float(state.get('distance', 0.0)),
                'piercing_loss': float(meta.get('piercing_loss', 0.0)),
                'penetration_factor': float(
                    meta.get('penetration_factor', 1.0)),
                'destructibles': [dict(value) for value in
                                  meta.get('destructibles_pending', ())],
            })
        # A projectile can reach its terminal while its preceding cursor is
        # still awaiting a canonical snapshot acknowledgement. Keep retrying
        # that exact CAS proposal even though the trajectory manager has
        # retired the projectile; resolution is submitted only after the
        # server echoes this base.
        for projectile_id, meta in tuple(self._projectile_meta.items()):
            pending = meta.get('progress_pending')
            if pending is not None and projectile_id not in active_ids:
                cursors.append(dict(pending))
        sent = False
        for index in range(0, len(cursors), 30):
            batch = cursors[index:index + 30]
            accepted = bool(sender(self._projectile_epoch, batch))
            if accepted:
                for cursor in batch:
                    meta = self._projectile_meta.get(
                        cursor['projectile_id'])
                    if meta is None:
                        continue
                    if meta.get('progress_pending') is None:
                        meta['progress_pending'] = dict(cursor)
                        meta['destructibles_pending'] = []
            sent = accepted or sent
        return sent

    def _decorate_ram_contacts(self, state):
        """Attach exact historical bot bodies to every pending receipt."""
        state = dict(state or {})
        contacts = state.get('ram_contacts')
        if not isinstance(contacts, list):
            legacy = state.get('ram_contact')
            contacts = [legacy] if isinstance(legacy, dict) else []
        decorated = []
        for raw_receipt in contacts:
            if not isinstance(raw_receipt, dict):
                continue
            receipt = dict(raw_receipt)
            try:
                revision = int(receipt.get('bot_state_revision'))
                bot_id = int(receipt.get('bot_id'))
                presentation_time_us = int(
                    receipt.get('presentation_time_us'))
            except (TypeError, ValueError, OverflowError):
                revision = bot_id = presentation_time_us = None
            bot_state = self._ram_bot_state_at(
                bot_id, revision, presentation_time_us)
            if bot_state is not None:
                receipt['_ram_contact_bot_state'] = dict(bot_state)
            decorated.append(receipt)
        state['ram_contacts'] = decorated
        return state

    def _authority_players(self):
        """Give local bot authority only real human world poses.

        A newly joined server player carries a formation placeholder until
        its first client pose reaches the server.  The authority already owns
        this client's render-frame pose, so replace its stale snapshot entry;
        omit other humans until their explicit ``world_pose`` sample arrives.
        """
        snapshot_players = (
            (self._last_snapshot or {}).get('players', ()) or ())
        if self.client is None:
            return list(snapshot_players)
        if self._worker_mode:
            players = []
            for raw in snapshot_players:
                if not isinstance(raw, dict):
                    continue
                if raw.get('participating') is not True:
                    continue
                try:
                    player_id = int(raw.get('id'))
                except (TypeError, ValueError, OverflowError):
                    continue
                # id=-1 is the private native-space carrier injected only on
                # this worker. It is never a combat target or server player.
                if player_id <= 0 or not bool(
                        raw.get('world_pose', False)):
                    continue
                players.append(self._decorate_ram_contacts(raw))
            return players
        local_id = int(self.client.player_id)
        players = []
        local_found = False
        for raw in snapshot_players:
            if not isinstance(raw, dict) or raw.get('id') is None:
                continue
            if raw.get('participating') is not True:
                continue
            state = dict(raw)
            if int(state['id']) != local_id:
                if not bool(state.get('world_pose', False)):
                    continue
                players.append(self._decorate_ram_contacts(state))
                continue
            local_found = True
            players.append(self._decorate_ram_contacts(
                self._live_local_player_state(state)))
        if not local_found:
            players.append(self._decorate_ram_contacts(
                self._live_local_player_state(self._local_state())))
        return players

    def _resolve_player_destructible_contacts(self, players, now):
        """Re-run player hull proposals in the hidden native authority."""
        if not self._worker_mode or self._destructibles is None:
            return 0
        sender = getattr(
            self.client, 'send_player_destructible_contact_result', None)
        if not callable(sender):
            raise RuntimeError(
                'worker destructible contact result boundary is unavailable')
        resolved = 0
        for state in players or ():
            contacts = state.get('destructible_contacts')
            if not isinstance(contacts, list) or not contacts:
                continue
            contact = contacts[0]
            if not isinstance(contact, dict):
                continue
            token = self._destructible_contact_token(contact.get('token'))
            try:
                player_id = int(state.get('id'))
                seq = int(contact.get('seq'))
                speed = float(contact.get('speed'))
                dt = float(contact.get('dt'))
                forward = float(contact.get('forward'))
                position = (
                    float(contact.get('x')), float(contact.get('y')),
                    float(contact.get('z')))
                yaw = float(contact.get('yaw'))
            except (TypeError, ValueError, OverflowError):
                continue
            if (token is None or player_id <= 0 or seq <= 0 or
                    not 0.0 < dt <= 0.1 or forward * speed <= 0.0):
                continue
            descriptor = self._resolve_player_descriptor(state)
            params = self._player_effective_snapshot(state)['physics']
            limit_name = 'speedBwd' if speed < 0.0 else 'speedFwd'
            kinetic_speed = (-float(params[limit_name]) if speed < 0.0 else
                             float(params[limit_name]))
            proposal = self._destructibles._catalog_motion_proposal(
                self._avatar.spaceID, self._vector(position), yaw, speed,
                descriptor, now, dt=dt, kinetic_speed=kinetic_speed)
            actual_token = (
                self._destructible_contact_token(proposal.get('token'))
                if isinstance(proposal, dict) else None)
            from gui.mods.offline_lan_0922 import destructibles_authority
            requested = set(token)
            unresolved = set(row for row in requested
                if not destructibles_authority.is_destroyed(*row))
            world_status = world_collision.check_horizontal_collision(
                self._runtime.bigworld, self._runtime.math,
                self._avatar.spaceID, self._vector(position), yaw, speed,
                descriptor, False, dt, True, True, kinetic_speed,
                commit_enabled=False)
            if isinstance(world_status, bool):
                world_status = 'hard' if world_status else 'clear'
            # The visible endpoint streams only the identities intersecting
            # its current hull bins.  The hidden worker can already have an
            # adjacent tile from the same fence/prop cluster registered, so
            # its exact native proposal may legitimately contain a strict
            # superset.  The worker remains authoritative: every identity the
            # visible endpoint requested must be present in its exact contact,
            # and any hard member makes the whole proposal non-crushable.
            proposal_status = (
                proposal.get('status')
                if isinstance(proposal, dict) else None)
            accepted = bool(
                world_status in ('clear', 'kinetic') and (
                    (not unresolved and
                     proposal_status in ('clear', 'crushed')) or
                    (proposal_status == 'crushed' and
                     actual_token is not None and
                     unresolved.issubset(set(actual_token)))))
            commit_status = None
            if accepted and bool(proposal.get('requires_commit', False)):
                committed = self._destructibles._catalog_motion_blocked(
                    self._avatar.spaceID, self._vector(position), yaw,
                    speed, descriptor, now, dt=dt,
                    kinetic_speed=kinetic_speed, return_detail=True,
                    kinetic_commit=True, commit_enabled=True)
                committed_token = (
                    self._destructible_contact_token(
                        committed.get('token'))
                    if isinstance(committed, dict) else None)
                commit_status = (
                    committed.get('status')
                    if isinstance(committed, dict) else 'invalid')
                accepted = bool(
                    isinstance(committed, dict) and
                    committed.get('status') == 'crushed' and
                    committed_token is not None and
                    unresolved.issubset(set(committed_token)))
            self._report_destructible_verdict(
                'worker', seq, accepted, token, actual_token,
                world_status, commit_status)
            if sender(
                    player_id, seq, accepted,
                    [list(row) for row in token]):
                resolved += 1
        return resolved

    def _live_local_player_state(self, state):
        """Overlay the copied local integrator on one protocol player row."""
        result = dict(state or {})
        position, yaw = self.local_pose()
        result.update({
            'id': int(self.client.player_id),
            'x': float(position[0]), 'y': float(position[1]),
            'z': float(position[2]), 'yaw': float(yaw),
            'speed': float(self._local_speed), 'world_pose': True,
        })
        if self._sender is not None:
            result['aim_yaw'] = float(self._sender.aim_yaw)
            result['gun_pitch'] = float(self._sender.gun_pitch)
        camouflage_id = self._garage_loadout_snapshot().get('camouflage_id')
        if camouflage_id is not None:
            result['camouflage_id'] = camouflage_id
        return result

    def _record_worker_control_diagnostics(self, sample_time_before):
        """Observe fixed-control and A* debt without feeding either scheduler."""
        if not self._worker_mode or self._bots is None:
            return False
        try:
            sample_time_after = int(getattr(
                self._bots, '_sample_time_us'))
            sample_time_before = int(sample_time_before)
        except (AttributeError, TypeError, ValueError, OverflowError):
            sample_time_after = sample_time_before = 0
        advanced = max(0, sample_time_after - sample_time_before)
        try:
            control_steps = max(0, int(getattr(
                self._bots, '_last_update_control_steps')))
        except (AttributeError, TypeError, ValueError, OverflowError):
            control_steps = 1 if advanced > 0 else 0
        try:
            max_control_step = max(0.0, float(getattr(
                self._bots, '_last_update_max_control_step')))
        except (AttributeError, TypeError, ValueError, OverflowError):
            max_control_step = advanced / 1000000.0
        if math.isnan(max_control_step) or math.isinf(max_control_step):
            max_control_step = advanced / 1000000.0
        debt = max(0.0, _number(getattr(
            self._bots, '_accumulator', 0.0)))
        self._worker_probe_control_debt = debt
        self._worker_probe_max_control_debt = max(
            self._worker_probe_max_control_debt, debt)
        if advanced > 0:
            if control_steps <= 0:
                control_steps = 1
            self._worker_probe_control_steps += control_steps
            self._worker_probe_max_control_step = max(
                self._worker_probe_max_control_step, max_control_step)
            control_seconds = max(0.0, _number(getattr(
                self._bots, '_control_seconds', 0.0)))
            if (control_steps > 1 or
                    advanced / 1000000.0 > control_seconds + 1.0e-9):
                self._worker_probe_catchup_callbacks += 1
            if debt + 1.0e-9 >= control_seconds > 0.0:
                self._worker_probe_control_debt_callbacks += 1
        navigator = getattr(self._bots, 'navigator', None)
        searches = getattr(navigator, 'searches', None)
        pending = len(searches) if isinstance(searches, dict) else 0
        self._worker_probe_astar_max_pending = max(
            self._worker_probe_astar_max_pending, pending)
        if advanced > 0 and pending > 0:
            try:
                remaining = int(getattr(
                    navigator, 'search_frame_budget'))
            except (AttributeError, TypeError, ValueError, OverflowError):
                remaining = None
            if remaining is not None and remaining <= 0:
                self._worker_probe_astar_budget_exhausted += 1
        return advanced > 0

    def _worker_diagnostic_totals(self):
        """Project only bounded non-negative Bot diagnostic integers."""
        provider = getattr(self._bots, 'diagnostic_totals', None)
        try:
            raw = provider() if callable(provider) else {}
        except Exception:
            raw = {}
        result = {}
        if isinstance(raw, dict):
            for name in _WORKER_DIAGNOSTIC_FIELDS:
                value = raw.get(name)
                if (isinstance(value, bool) or
                        not isinstance(value, _INTEGER_TYPES) or value < 0):
                    continue
                result[name] = int(value)
        return result

    def _worker_control_snapshot(self):
        bots = self._bots
        navigator = getattr(bots, 'navigator', None)
        searches = getattr(navigator, 'searches', None)
        pending = len(searches) if isinstance(searches, dict) else None

        def finite_attribute(owner, name):
            if owner is None or not hasattr(owner, name):
                return None
            value = _number(getattr(owner, name), float('nan'))
            return value if not math.isnan(value) and not math.isinf(
                value) else None

        return {
            'control_steps': self._worker_probe_control_steps,
            'catchup_callbacks': self._worker_probe_catchup_callbacks,
            'debt_callbacks': self._worker_probe_control_debt_callbacks,
            'max_control_step_ms': (
                self._worker_probe_max_control_step * 1000.0),
            'control_debt_ms': self._worker_probe_control_debt * 1000.0,
            'max_control_debt_ms': (
                self._worker_probe_max_control_debt * 1000.0),
            'astar_pending': pending,
            'astar_credit': finite_attribute(navigator, 'search_credit'),
            'astar_budget_remaining': finite_attribute(
                navigator, 'search_frame_budget'),
            'astar_completed': finite_attribute(
                navigator, 'search_completed'),
            'astar_failed': finite_attribute(navigator, 'search_failed'),
            'astar_budget_exhausted_callbacks': (
                self._worker_probe_astar_budget_exhausted),
            'astar_max_pending': self._worker_probe_astar_max_pending,
        }

    def _authority_worker_probe_sample(self):
        totals = None
        provider = getattr(self._bots, 'probe_totals', None)
        if callable(provider):
            try:
                totals = provider()
            except Exception:
                totals = None
        probes = {}
        if isinstance(totals, (list, tuple)):
            for index, name in enumerate(PROBE_KINDS):
                if index >= len(totals):
                    break
                try:
                    probes[name] = int(totals[index])
                except (TypeError, ValueError, OverflowError):
                    continue
        duration_totals = None
        duration_provider = getattr(
            self._bots, 'probe_duration_totals', None)
        if callable(duration_provider):
            try:
                duration_totals = duration_provider()
            except Exception:
                duration_totals = None
        probe_seconds = {}
        if isinstance(duration_totals, (list, tuple)):
            for index, name in enumerate(PROBE_KINDS):
                if index >= len(duration_totals):
                    break
                try:
                    value = float(duration_totals[index])
                    if value >= 0.0 and not math.isnan(value) and not math.isinf(
                            value):
                        probe_seconds[name] = value
                except (TypeError, ValueError, OverflowError):
                    continue
        probe_timing = 'off'
        timing_provider = getattr(self._bots, 'probe_timing_state', None)
        if callable(timing_provider):
            try:
                probe_timing = str(timing_provider())
            except Exception:
                probe_timing = 'failed'
        diagnostic_totals = self._worker_diagnostic_totals()
        snapshot = self._last_snapshot or {}
        frame_performance = {}
        frame_provider = getattr(self._frame_diagnostics, 'snapshot', None)
        if callable(frame_provider):
            try:
                frame_performance = frame_provider()
            except Exception:
                frame_performance = {}
        return {
            'round_finished': self._battle_result is not None,
            'frame_callbacks': self._worker_frame_callbacks,
            'authority_callbacks': self._worker_probe_authority_callbacks,
            'bot_state_generated': self._worker_probe_bot_generated,
            'bot_state_enqueued': self._worker_probe_bot_enqueued,
            'bot_state_send_failed': self._worker_probe_bot_send_failed,
            'bot_state_revision': snapshot.get('bot_state_revision'),
            'bot_probes': probes,
            'bot_probe_seconds': probe_seconds,
            'probe_timing': probe_timing,
            'bot_count': self._worker_probe_bot_count,
            'simulation_caps': self._worker_probe_simulation_caps,
            'alive_bot_ticks': diagnostic_totals.get('alive_bot_ticks'),
            'bot_diagnostics': diagnostic_totals,
            'control': self._worker_control_snapshot(),
            'presentation': {
                'pose_writes': self._authority_pose_writes,
                'pose_skips': self._authority_pose_skips,
                'aim_writes': self._authority_aim_writes,
                'aim_skips': self._authority_aim_skips,
            },
            'frame_performance': frame_performance,
        }

    def authority_worker_ready_for_draw_off(self):
        """Return true only after every native simulation model is ready.

        Bot compounds are intentionally created over several callbacks. The
        exact client has not proved that background model completion continues
        after world drawing is disabled, so keep drawing enabled through that
        short load and acquire draw-off only after every live record entered
        the native space.
        """
        if not self._worker_mode or self.state != 'running':
            return False
        if self._pending_bot_create_order or self._pending_bot_creates:
            return False
        for record in self._records.values():
            if not record.get('tombstone') and not record.get('ready'):
                return False
        return bool(self._records)

    def _advance_authority_worker_probe(self):
        """Advance the opt-in probe without making diagnostics authoritative."""
        if self._worker_mode:
            # Dedicated workers remain draw-disabled for the whole round. The
            # legacy diagnostic intentionally toggles draw/window stages and
            # must not alter this process' lifecycle.
            return False
        settings = (self._config or {}).get('authority_worker_probe') or {}
        enabled = bool(isinstance(settings, dict) and
                       settings.get('enabled', False))
        checker = getattr(self._bots, 'is_authority', None)
        authority = False
        if callable(checker):
            try:
                authority = bool(checker())
            except Exception:
                authority = False
        probe = self._worker_probe
        if probe is not None:
            if (probe.active and
                    (not enabled or not self._battle_live or not authority)):
                reason = ('authority_lost' if not authority else
                          'probe_disabled' if not enabled else
                          'battle_not_live')
                probe.stop(reason)
                return False
            if not probe.active:
                return False
            try:
                self._worker_probe_authority_callbacks += 1
                probe.tick()
            except Exception as error:
                # A measurement must never terminate or alter the round.
                try:
                    probe.stop('probe_error')
                except Exception:
                    pass
                write_probe_record({
                    'schema': 1,
                    'probe': 'authority_worker',
                    'event': 'probe_error',
                    'process_id': os.getpid(),
                    'round_id': (self._start_message or {}).get('round_id'),
                    'message': str(error),
                })
                return False
            return True
        if (self._worker_probe_attempted or not enabled or
                not self._battle_live or not authority or
                self._worker_probe_bot_count <= 0):
            return False
        self._worker_probe_attempted = True
        try:
            seconds = float(settings.get('stageSeconds', 15.0))
            probe = AuthorityWorkerProbe(
                self._runtime.bigworld,
                self._authority_worker_probe_sample,
                stage_seconds=seconds,
                context={
                    'process_id': os.getpid(),
                    'round_id': (self._start_message or {}).get('round_id'),
                    'map': (self._config or {}).get('map'),
                    'player_id': getattr(self.client, 'player_id', None),
            })
            self._worker_probe = probe
            if not probe.start():
                return False
            self._worker_probe_authority_callbacks += 1
            probe.tick()
            return True
        except Exception as error:
            try:
                if probe is not None:
                    probe.stop('start_failed')
            except Exception:
                pass
            write_probe_record({
                'schema': 1,
                'probe': 'authority_worker',
                'event': 'probe_error',
                'process_id': os.getpid(),
                'round_id': (self._start_message or {}).get('round_id'),
                'message': str(error),
            })
            return False

    def _stop_authority_worker_probe(self, reason):
        probe = self._worker_probe
        if probe is None or probe.finished:
            return False
        return probe.stop(reason)

    def _frame(self):
        if self.state != 'running':
            return
        if self._worker_mode:
            self._worker_frame_callbacks += 1
        diagnostics = self._frame_diagnostics
        profiling = diagnostics is not None and diagnostics.enabled
        entry_wall = _PROFILE_CLOCK() if profiling else 0.0
        now = self._clock()
        frame_start = self._last_frame_time
        raw_dt = (0.0 if frame_start is None else
                  now - frame_start)
        rule_dt = max(0.0, raw_dt)
        if ((self._worker_mode or self._worker_probe is not None) and
                raw_dt > 0.1000001):
            self._worker_probe_simulation_caps += 1
        # Never discard elapsed time. Rule clocks consume the complete wall
        # delta, and _drive_local consumes the same delta in stable <=100 ms
        # substeps before this callback returns. A one-second hitch therefore
        # produces the state one second later instead of a truncated step or a
        # simulation debt which leaks into later render frames.
        dt = rule_dt
        tick_dt = rule_dt
        self._last_frame_time = now
        # Direction probes may recast through proved soft OBBs, but those
        # native queries share one hard frame budget across all 29 Bots.
        self._soft_static_recast_budget[0] = BOT_SOFT_RECAST_BUDGET
        offframe = self._offframe_seconds
        self._offframe_seconds = 0.0
        self._effect_reports = 0
        self._spotted_signature = None
        frame_id = (diagnostics.begin(entry_wall, raw_dt, offframe)
                    if profiling else 0)
        stages = {}
        probes = dict((name, 0) for name in PROBE_KINDS)
        probe_durations = dict((name, 0.0) for name in PROBE_KINDS)
        pose_before = tuple(self._local_position)
        transitioned = False
        outgoing_messages = ()
        bot_count = 0
        projectile_perf = {}
        boundary = entry_wall
        try:
            self._run_optional_feature(
                'map visibility filtering',
                self._maintain_standard_space_visibility, (now,),
                self._disable_standard_space_visibility)
            self._flush_pending_bot_create(now)
            self._flush_pending_entities(now)
            self._drain_event_journal()
            self._run_optional_feature(
                'foliage camouflage',
                self._refresh_fallen_tree_foliage, (now,))
            self._retry_bot_manifest(now)
            self._maybe_send_battle_ready()
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['house'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if self._sync is not None:
                self._sync.advance(now)
            if self._battle_live and self._worker_mode:
                self._advance_player_fire_authority(rule_dt, now)
                self._publish_player_environment(rule_dt, now)
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['sync'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if self._battle_live and not self._worker_mode:
                self._tick_critical_states(rule_dt)
            if not self._worker_mode:
                self._run_optional_feature(
                    'Expert damaged-device presentation',
                    self._tick_expert_target, (now,),
                    self._disable_expert_presentation)
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['critical'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if self._battle_live and not self._worker_mode:
                self._tick_drowning(rule_dt, now)
                self._tick_overturn(rule_dt, now)
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['drown'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if (not self._battle_live and
                    self._prebattle_deadline is not None and
                    self._bots is not None):
                prewarm = getattr(
                    self._bots, 'prewarm_world_receipts', None)
                if callable(prewarm):
                    before_prewarm = None
                    before_prewarm_durations = None
                    probe_totals = getattr(self._bots, 'probe_totals', None)
                    probe_duration_totals = getattr(
                        self._bots, 'probe_duration_totals', None)
                    try:
                        if profiling and callable(probe_totals):
                            before_prewarm = probe_totals()
                        if profiling and callable(probe_duration_totals):
                            before_prewarm_durations = \
                                probe_duration_totals()
                        prewarm(now)
                        if profiling and before_prewarm is not None:
                            after_prewarm = probe_totals()
                            if (len(before_prewarm) >= len(PROBE_KINDS) and
                                    len(after_prewarm) >= len(PROBE_KINDS)):
                                for index, name in enumerate(PROBE_KINDS):
                                    probes[name] += max(
                                        0, int(after_prewarm[index]) -
                                        int(before_prewarm[index]))
                        if (profiling and
                                before_prewarm_durations is not None):
                            after_prewarm_durations = \
                                probe_duration_totals()
                            if (len(before_prewarm_durations) >=
                                    len(PROBE_KINDS) and
                                    len(after_prewarm_durations) >=
                                    len(PROBE_KINDS)):
                                for index, name in enumerate(PROBE_KINDS):
                                    probe_durations[name] += max(
                                        0.0,
                                        float(after_prewarm_durations[index]) -
                                        float(before_prewarm_durations[index]))
                    except Exception:
                        # Countdown prewarming is an optimisation.  A broken
                        # callback must restore the unchanged live fail-closed
                        # path, not prevent the battle from starting.
                        pass
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['prewarm'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if (not self._battle_live and
                    self._prebattle_transition_ready(now)):
                live_edge = float(self._prebattle_deadline)
                self._begin_battle()
                # A delayed callback may straddle 00:00.  Countdown time is
                # not battle time, but every second after the exact deadline
                # is: consume that suffix now instead of deleting the entire
                # frame just because the transition happened inside it.
                interval_start = (float(now) if frame_start is None else
                                  float(frame_start))
                live_dt = max(
                    0.0, float(now) - max(interval_start, live_edge))
                dt = live_dt
                rule_dt = live_dt
                transitioned = True
                # The rule phases above deliberately stayed behind the live
                # gate while this callback still represented PREBATTLE.  If
                # the callback crossed 00:00, run each of those phases once
                # with the complete live suffix now.  Do not defer that time
                # to another render callback and do not include countdown
                # time in battle rules.
                if self._worker_mode:
                    self._advance_player_fire_authority(rule_dt, now)
                    self._publish_player_environment(rule_dt, now)
                else:
                    self._tick_critical_states(rule_dt)
                    self._tick_drowning(rule_dt, now)
                    self._tick_overturn(rule_dt, now)
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['transition'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if self._battle_live and not self._worker_mode:
                self._drive_local(dt)
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['local'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if self._battle_live and not self._worker_mode:
                self._run_optional_feature(
                    'target outline', self._update_target_outline, (now,),
                    self._disable_target_outline_presentation)
                self._run_optional_feature(
                    'compound diagnostics', self._report_local_compound,
                    (now,))
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['outline'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if self._battle_live and self._bots is not None:
                if self._worker_mode:
                    self._worker_probe_authority_callbacks += 1
                self._advance_artillery_arcs(now)
                players = self._authority_players()
                if self._worker_mode:
                    self._scan_authority_player_trees(players, now)
                    self._resolve_player_destructible_contacts(players, now)
                probe_totals = getattr(self._bots, 'probe_totals', None)
                probe_duration_totals = getattr(
                    self._bots, 'probe_duration_totals', None)
                before_probes = None
                before_probe_durations = None
                if profiling and callable(probe_totals):
                    try:
                        before_probes = probe_totals()
                    except Exception:
                        before_probes = None
                if profiling and callable(probe_duration_totals):
                    try:
                        before_probe_durations = probe_duration_totals()
                    except Exception:
                        before_probe_durations = None
                set_camera = getattr(
                    self._bots, 'set_camera_position', None)
                if callable(set_camera):
                    # A worker has no presentation camera. Using its off-map
                    # dummy as one would lower update detail for distant bots
                    # and make worker authority behave unlike player authority.
                    set_camera(
                        None if self._worker_mode else self._local_position)
                control_sample_before = getattr(
                    self._bots, '_sample_time_us', None)
                outgoing_messages = self._bots.update(
                    rule_dt, now, players=players)
                if self._worker_mode:
                    self._record_worker_control_diagnostics(
                        control_sample_before)
                after_probes = None
                after_probe_durations = None
                if profiling and callable(probe_totals):
                    try:
                        after_probes = probe_totals()
                    except Exception:
                        after_probes = None
                if profiling and callable(probe_duration_totals):
                    try:
                        after_probe_durations = probe_duration_totals()
                    except Exception:
                        after_probe_durations = None
                if (isinstance(before_probes, (list, tuple)) and
                        isinstance(after_probes, (list, tuple)) and
                        len(before_probes) == len(PROBE_KINDS) and
                        len(after_probes) == len(PROBE_KINDS)):
                    try:
                        for index, name in enumerate(PROBE_KINDS):
                            probes[name] += max(
                                0, int(after_probes[index]) -
                                int(before_probes[index]))
                    except (TypeError, ValueError, OverflowError):
                        probes = dict((name, 0) for name in PROBE_KINDS)
                if (isinstance(before_probe_durations, (list, tuple)) and
                        isinstance(after_probe_durations, (list, tuple)) and
                        len(before_probe_durations) == len(PROBE_KINDS) and
                        len(after_probe_durations) == len(PROBE_KINDS)):
                    try:
                        for index, name in enumerate(PROBE_KINDS):
                            probe_durations[name] += max(
                                0.0, float(after_probe_durations[index]) -
                                float(before_probe_durations[index]))
                    except (TypeError, ValueError, OverflowError):
                        probe_durations = dict(
                            (name, 0.0) for name in PROBE_KINDS)
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['bots_update'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if self._battle_live and self._bots is not None:
                presentation_states = getattr(
                    self._bots, 'presentation_states', None)
                if not callable(presentation_states):
                    raise RuntimeError(
                        'authority bot presentation boundary is unavailable')
                # Pull the accepted authority pose on every render callback,
                # while BotRuntime advances only when its production fixed-
                # control scheduler consumes elapsed time. RemoteVehicle's
                # MatrixAnimation interpolates between changed poses; exact
                # duplicate render pulls do not require native rewrites.
                states = presentation_states(now)
                bot_count = len(states)
                self._apply_authority_bot_poses(states)
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['bot_present'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if self._battle_live and self._bots is not None:
                for outgoing in outgoing_messages:
                    is_bot_state = outgoing.get('type') == 'bot_state'
                    if is_bot_state:
                        self._worker_probe_bot_generated += 1
                    accepted = self._enqueue_bot_message(outgoing)
                    if is_bot_state:
                        if accepted:
                            self._worker_probe_bot_enqueued += 1
                        else:
                            self._worker_probe_bot_send_failed += 1
            if (self._battle_live and
                    (self._projectile_is_authority() or
                     self._projectile_visual_meta)):
                self._advance_projectiles(now)
                projectile_perf = dict(self._projectile_perf)
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['bot_events'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if not self._worker_mode:
                if self._battle_live:
                    self._update_spotting(now)
                elif self._prebattle_deadline is not None:
                    # The minimap view circle is live during the countdown, but
                    # enemy spotting and its LAN report stay behind the battle
                    # gate.  This also lets still devices arm before 00:00.
                    self._update_spotting(now, hud_only=True)
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['spot'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
            if self._battle_live and not self._worker_mode:
                validate_lock = getattr(
                    self._runtime.compatibility,
                    'validate_target_lock', None)
                if not callable(validate_lock):
                    raise RuntimeError(
                        '#1513 target-lock lifecycle boundary is unavailable')
                validate_lock(self._avatar)
            self._worker_probe_bot_count = bot_count
            self._advance_authority_worker_probe()
            if profiling:
                next_boundary = _PROFILE_CLOCK()
                stages['lock'] = max(0.0, next_boundary - boundary)
                boundary = next_boundary
        except Exception as error:
            self._fail(error)
            return
        schedule_start = _PROFILE_CLOCK() if profiling else 0.0
        self._schedule(FRAME_SECONDS, self._frame)
        if profiling:
            schedule_end = _PROFILE_CLOCK()
            stages['schedule'] = max(0.0, schedule_end - schedule_start)
            camera_velocity = _xyz(self._local_camera_velocity)
            camera_speed = math.sqrt(
                camera_velocity[0] * camera_velocity[0] +
                camera_velocity[1] * camera_velocity[1] +
                camera_velocity[2] * camera_velocity[2])
            pose_step = _distance_2d(pose_before, self._local_position)
            authority = False
            is_authority = getattr(self._bots, 'is_authority', None)
            if callable(is_authority):
                try:
                    authority = bool(is_authority())
                except Exception:
                    authority = False
            probe_timing = 'off'
            probe_timing_state = getattr(
                self._bots, 'probe_timing_state', None)
            if callable(probe_timing_state):
                try:
                    probe_timing = str(probe_timing_state())
                except Exception:
                    probe_timing = 'failed'
            emit_due = getattr(diagnostics, 'emit_due', None)
            if callable(emit_due) and emit_due():
                load_report = getattr(self._bots, 'load_report', None)
                if callable(load_report):
                    try:
                        diagnostics.note_bot_load(load_report())
                    except Exception:
                        pass
                try:
                    diagnostics.note_collections(self._collection_counts())
                except Exception:
                    pass
                note_worker_runtime = getattr(
                    diagnostics, 'note_worker_runtime', None)
                if self._worker_mode and callable(note_worker_runtime):
                    try:
                        note_worker_runtime({
                            'control': self._worker_control_snapshot(),
                            'bot_diagnostics': (
                                self._worker_diagnostic_totals()),
                            'presentation': {
                                'pose_writes': self._authority_pose_writes,
                                'pose_skips': self._authority_pose_skips,
                                'aim_writes': self._authority_aim_writes,
                                'aim_skips': self._authority_aim_skips,
                            },
                        })
                    except Exception:
                        pass
            diagnostics.finish(
                frame_id, entry_wall, tick_dt, dt, stages, probes, {
                    'round': (self._start_message or {}).get('round_id', '-'),
                    'map': (self._config or {}).get('map', '-'),
                    'phase': 'live' if self._battle_live else 'prebattle',
                    'role': ('worker' if self._worker_mode else
                             ('authority' if authority else 'guest')),
                    'probe_timing': probe_timing,
                    'bot_count': bot_count,
                    'outgoing_count': len(outgoing_messages),
                    'pose_step': pose_step,
                    'speed': float(self._local_speed),
                    'camera_speed': camera_speed,
                    'airborne': bool(self._local_airborne),
                    'grind': int(self._local_grind),
                    'transitioned': transitioned,
                }, probe_durations=probe_durations,
                projectile=projectile_perf)

    def _mutable_shot_ray(self):
        """Copy #1513's native gun ray before normalising or scattering it."""
        gun_rotator = getattr(self._avatar, 'gunRotator', None)
        get_shot = getattr(gun_rotator, 'getCurShotPosition', None)
        if not callable(get_shot):
            raise RuntimeError('#1513 gun shot-position provider is unavailable')
        native_start, native_direction = get_shot()
        start = self._vector(_xyz(native_start))
        direction = self._vector(_xyz(native_direction))
        direction.normalise()
        if direction.length <= 0.0:
            raise RuntimeError('#1513 gun shot direction is empty')
        return start, direction

    def _native_dispersion_angle(self):
        """Read the exact angle currently presented by #1513's gun rotator.

        The pinned Avatar already computes movement, traverse, turret and
        post-shot bloom in ``getOwnVehicleShotDispersionAngle``.  Replacing
        that method with the 0.8.2 shadow state produced a second, divergent
        reticle.  The read-only rotator property is the single source shared
        by the stock marker and the trusted-client shot ray.
        """
        gun_rotator = getattr(self._avatar, 'gunRotator', None)
        if gun_rotator is None:
            raise RuntimeError('#1513 gun rotator is unavailable')
        try:
            angle = float(gun_rotator.dispersionAngle)
        except (AttributeError, TypeError, ValueError):
            raise RuntimeError(
                '#1513 gun rotator dispersion angle is unavailable')
        if math.isnan(angle) or math.isinf(angle) or angle < 0.0:
            raise RuntimeError('#1513 gun rotator dispersion angle is invalid')
        return angle

    def _sync_local_server_marker(self):
        """Echo the trusted client marker into #1513's server-aim channel.

        The 0.8.2 offline battle refreshes both gun-marker channels from the
        same current dispersion angle.  #1513 shows a second marker when the
        user's server-aim setting is enabled, but a real cell normally feeds
        that marker through ``VehicleGunRotator.setShotPosition``.  The local
        cell has no independent simulation, so echo the trusted native ray and
        angle instead of leaving the second marker at its initial size.  Keep
        PREBATTLE on the stock frozen boundary and begin this echo only after
        the native BATTLE transition starts the rotator.
        """
        if not self._battle_live:
            return False
        gun_rotator = getattr(self._avatar, 'gunRotator', None)
        if (gun_rotator is None or
                not bool(getattr(gun_rotator, 'showServerMarker', False))):
            return False
        get_shot = getattr(gun_rotator, 'getCurShotPosition', None)
        update_marker = getattr(self._avatar, 'updateGunMarker', None)
        if not callable(get_shot) or not callable(update_marker):
            raise RuntimeError(
                '#1513 server gun-marker boundary is unavailable')
        shot_position, shot_vector = get_shot()
        update_marker(
            self._server.vehicle_id, shot_position, shot_vector,
            self._native_dispersion_angle())
        return True

    def _mouse_targeting_ray(self):
        """Copy the ray #1513 gives to ``BigWorld.target.source``.

        ``AvatarInputHandler._Targeting`` builds the native target from the
        mouse matrix, so the cursor selects the outlined vehicle in every
        control mode.  ``bwdeprecations`` renamed the factory, and only the
        current name is a native symbol of ``WorldOfTanks.exe``.
        """
        provider = self._mouse_target_matrix
        if provider is None:
            factory = getattr(
                self._runtime.bigworld, 'MouseTargetingMatrix',
                getattr(
                    self._runtime.bigworld, 'MouseTargettingMatrix', None))
            if not callable(factory):
                raise RuntimeError(
                    '#1513 mouse targeting matrix is unavailable')
            provider = factory()
            self._mouse_target_matrix = provider
        matrix = self._runtime.math.Matrix(provider)
        start = self._vector(_xyz(matrix.applyToOrigin()))
        direction = self._vector(_xyz(matrix.applyToAxis(2)))
        direction.normalise()
        if direction.length <= 0.0:
            raise RuntimeError('#1513 mouse targeting ray is empty')
        return start, direction

    def _wreck_blocks_target_outline(self, start, end, target_depth):
        """Return whether a retained wreck owns the nearer cursor hit."""
        for record in self._records.values():
            if (record.get('local') or record.get('tombstone') or
                    not record.get('ready')):
                continue
            vehicle = self._server_entity(record.get('engine_id'))
            if (vehicle is None or
                    not getattr(vehicle, 'isStarted', False) or
                    self._record_alive(record, vehicle)):
                continue
            if record.get('native_remote'):
                collisions = collide_vehicle_at_matrix(
                    vehicle, vehicle.matrix, start, end,
                    self._runtime.math)
            else:
                collide = getattr(vehicle, 'collideSegmentExt', None)
                collisions = collide(start, end) if callable(collide) else ()
            if (collisions and min(float(item.dist) for item in collisions) +
                    _SHOT_OCCLUSION_EPSILON < target_depth):
                return True
        return False

    def _update_target_outline(self, now):
        """Outline the vehicle the cursor ray actually strikes.

        Retail reaches ``Vehicle.drawEdge`` from ``PlayerAvatar.targetFocus``,
        which the engine raises for the entity its own cursor-driven targeting
        selects.  #1513 pairs selectionFovDegrees=1.0 with
        skeletonCheckEnabled=True, so the cone only nominates candidates and
        the model itself decides, on every pass.  The gun line is unrelated,
        but static scenery between the mouse ray and that exact model hit owns
        the nearer collision.  SpeedTree foliage is handled by the separate
        foliage map and does not appear in this mask-128 static-world ray.
        """
        if now < self._next_outline_time or self._outline_blocked:
            return
        self._next_outline_time = now + TARGET_OUTLINE_SECONDS
        if self._remote_factory is None:
            self._clear_target_outline()
            return
        start, direction = self._mouse_targeting_ray()
        end = start + direction.scale(TARGET_MAX_DISTANCE)
        selection_angle = TARGET_SELECTION_FOV_DEGREES * 0.5
        deselection_angle = TARGET_DESELECTION_FOV_DEGREES * 0.5
        held_id = self._outlined_engine_id
        held_seen = False
        held_reason = None
        chosen = None
        chosen_depth = None
        miss = None
        decline = None
        for record in self._records.values():
            if record.get('local'):
                continue
            engine_id = record.get('engine_id')
            held = held_id is not None and engine_id == held_id
            held_seen = held_seen or held
            vehicle = None
            distance = 0.0
            reason = None
            if not record.get('ready') or record.get('tombstone'):
                reason = 'is not ready'
            elif not record.get('spot_visible', True):
                reason = 'is not spotted'
            else:
                vehicle = self._server_entity(engine_id)
                if (vehicle is None or
                        (not record.get('native_remote') and
                         getattr(vehicle, 'bw_entity', None) is None)):
                    reason = 'has no visual entity'
                elif not vehicle.isAlive():
                    reason = 'is destroyed'
                else:
                    offset = self._vector(_xyz(vehicle.position)) - start
                    distance = offset.length
                    if distance > TARGET_MAX_DISTANCE:
                        reason = 'is past %.0f m' % TARGET_MAX_DISTANCE
            if reason is not None:
                if held:
                    held_reason = reason
                decline = decline or (engine_id, reason)
                continue
            bearing = 0.0
            if distance > 0.0:
                cosine = min(1.0, max(-1.0, (
                    offset.x * direction.x + offset.y * direction.y +
                    offset.z * direction.z) / distance))
                bearing = math.degrees(math.acos(cosine))
            # The bounding box circumscribes the silhouette, so this cone only
            # narrows how many exact tests run.  It never rejects a real hit.
            angle = max(0.0, bearing -
                        self._target_angular_radius(vehicle, distance))
            depth = None
            if angle <= deselection_angle:
                if record.get('native_remote'):
                    collisions = collide_vehicle_at_matrix(
                        vehicle, vehicle.matrix, start, end,
                        self._runtime.math)
                    if collisions:
                        depth = min(float(item.dist) for item in collisions)
                else:
                    collide = getattr(vehicle, 'collideSegmentExt', None)
                    if callable(collide):
                        collisions = collide(start, end)
                        if collisions:
                            depth = min(
                                float(item.dist) for item in collisions)
                    elif bearing - self._target_angular_radius(
                        vehicle, distance, tight=True) <= selection_angle:
                        depth = distance
            if depth is None:
                if held:
                    held_reason = 'is not under the cursor'
                if miss is None or angle < miss[0]:
                    miss = (angle, engine_id, distance)
                continue
            if chosen_depth is None or depth < chosen_depth:
                chosen_depth = depth
                chosen = engine_id
        if chosen is not None and chosen_depth is not None:
            target_end = start + direction.scale(chosen_depth)
            world_hit = self._runtime.bigworld.wg_collideSegment(
                self._avatar.spaceID, start, target_end, 128)
            if (world_hit is not None and
                    (world_hit[0] - start).length +
                    _SHOT_OCCLUSION_EPSILON < chosen_depth):
                blocked_id = chosen
                reason = 'is behind scenery'
                if held_id == blocked_id:
                    held_reason = reason
                decline = (blocked_id, reason)
                chosen = None
                chosen_depth = None
            elif self._wreck_blocks_target_outline(
                    start, target_end, chosen_depth):
                blocked_id = chosen
                reason = 'is behind a wreck'
                if held_id == blocked_id:
                    held_reason = reason
                decline = (blocked_id, reason)
                chosen = None
                chosen_depth = None
        # Retail drops the target when it stops being eligible, and a vehicle
        # the round no longer records at all can never be kept.
        if held_id is not None and chosen != held_id and held_reason is None:
            held_reason = ('left the record set' if not held_seen
                           else 'is behind a nearer vehicle')
        dropped = None
        if held_id is not None and chosen != held_id:
            dropped = (held_id, held_reason)
        self._report_target_outline(now, chosen, miss, decline, dropped)
        if chosen == held_id:
            return
        if not self._clear_target_outline():
            return
        if chosen is None:
            return
        vehicle = self._remote_factory.get(chosen)
        native_remote = bool(getattr(
            vehicle, '_offlineNativeRemote', False))
        visual_entity = vehicle if native_remote else getattr(
            vehicle, 'bw_entity', None)
        if vehicle is None or visual_entity is None:
            raise RuntimeError('outlined remote vehicle has no visual entity')
        color = 2 if int(vehicle.team) == int(self.client.team) else 1
        add_edge = getattr(
            self._runtime.bigworld, 'wgAddEdgeDetectEntity', None)
        if not callable(add_edge):
            raise RuntimeError('#1513 edge-detect add boundary is unavailable')
        add_edge(visual_entity, color, 0, False)
        self._report_edge('add id=%s colour=%d' % (chosen, color))
        # Record the exact entity and compound the engine keyed the edge on.
        # An untracked registration is never removed.
        self._outlined_engine_id = chosen
        self._outlined_entity = visual_entity
        self._outlined_vehicle = vehicle
        self._outlined_model = vehicle.model
        set_candidate = getattr(
            self._runtime.compatibility, 'set_target_lock_candidate', None)
        if not callable(set_candidate):
            raise RuntimeError(
                '#1513 target-lock candidate boundary is unavailable')
        set_candidate(vehicle)
        self.monitor_vehicle_damaged_devices(chosen)

    def _target_angular_radius(self, vehicle, distance, tight=False):
        """The half-angle this vehicle's own hull subtends at this range.

        The default circumscribes the hull; ``tight`` inscribes it.
        """
        if distance <= 0.0:
            return 180.0
        hit_tester = _field(
            _field(getattr(vehicle, 'typeDescriptor', None), 'hull', {}),
            'hitTester', None)
        bbox = getattr(hit_tester, 'bbox', None)
        try:
            length = abs(float(bbox[0][2])) + abs(float(bbox[1][2]))
            width = abs(float(bbox[0][0])) + abs(float(bbox[1][0]))
        except (TypeError, IndexError, ValueError):
            length, width = 6.0, 3.0
        length = max(3.0, length)
        width = max(2.0, width)
        radius = (0.5 * width if tight
                  else 0.5 * math.sqrt(length ** 2 + width ** 2))
        return math.degrees(math.atan2(radius, distance))

    _CRUSH_REPORT_LIMIT = 80
    _CRUSH_REPORT_SECONDS = 0.1
    _CRUSH_DIAGNOSTICS = False
    _DESTRUCTIBLE_VERDICT_REPORT_LIMIT = 24
    _BOT_CONTACT_PATHS = {
        'clear': 'advance',
        'crushed': 'advance',
        'soft': 'soft_hold',
        'cap_crushed': 'cap_hold',
        'hard': 'brake',
    }

    def _report_destructible_contact(self, who, kinds, status, path,
                                     before, after, now, extra=''):
        """Name the item and the code path that changed a contact speed."""
        if not self._CRUSH_DIAGNOSTICS:
            return False
        if not kinds or kinds == '-':
            return False
        if self._crush_reports >= self._CRUSH_REPORT_LIMIT:
            return False
        if now < self._next_crush_report.get(who, 0.0):
            return False
        self._next_crush_report[who] = now + self._CRUSH_REPORT_SECONDS
        self._crush_reports += 1
        sys.stdout.write(
            '[Offline LAN 0.9.22] CRUSH who=%s kind=%s status=%s path=%s '
            'v0=%.2f v1=%.2f%s\n' % (
                who, kinds, status, path, float(before), float(after), extra))
        return True

    def _report_destructible_verdict(self, stage, sequence, accepted,
                                     requested=None, actual=None,
                                     world_status=None,
                                     commit_status=None):
        """Keep a bounded audit trail for rare authority corrections."""
        if (self._destructible_verdict_reports >=
                self._DESTRUCTIBLE_VERDICT_REPORT_LIMIT):
            return False
        self._destructible_verdict_reports += 1
        sys.stdout.write(
            '[Offline LAN 0.9.22] DESTRUCTIBLE stage=%s seq=%d '
            'accepted=%d requested=%r actual=%r world=%s commit=%s\n' % (
                str(stage), int(sequence), int(bool(accepted)),
                requested, actual, str(world_status or '-'),
                str(commit_status or '-')))
        return True

    def _report_local_contact_tick(self, path, before, pitch, rise):
        """Close the tick with the drive slope, the hull rise and the skips.

        A crushed item must cost neither speed nor height, so the same line
        carries the contact seam's answer and what the ground probes did.
        """
        if not self._CRUSH_DIAGNOSTICS:
            return False
        reader = getattr(
            self._destructibles, 'take_ground_skip_count', None)
        skips = int(_number(reader())) if callable(reader) else 0
        kinds = self._local_motion_kinds
        if kinds == '-' and skips:
            kinds = 'ground'
        if (path in (None, 'advance') and not skips and
                self._local_motion_status != 'crushed'):
            return False
        return self._report_destructible_contact(
            'local', kinds, self._local_motion_status, path or 'still',
            before, self._local_speed, self._clock(),
            ' pitch=%.3f dy=%+.3f skip=%d' % (
                float(pitch), float(rise), int(skips)))

    def _report_bot_destructible_contact(self, bot_id, status, before, after):
        """Bot-side seam for the same contact-speed diagnostic."""
        if not self._CRUSH_DIAGNOSTICS:
            return False
        if status == 'clear':
            return False
        return self._report_destructible_contact(
            'bot:%s' % int(bot_id),
            self._bot_motion_kinds.get(int(bot_id), '-'), status,
            self._BOT_CONTACT_PATHS.get(status, status),
            before, after, self._clock())

    _EDGE_REPORT_LIMIT = 24
    _TARGET_REPORT_LIMIT = 24
    _TARGET_REPORT_SECONDS = 5.0

    def _report_edge(self, message):
        """Pair every edge-detect add with its removal in the log."""
        if self._edge_reports >= self._EDGE_REPORT_LIMIT:
            return False
        self._edge_reports += 1
        sys.stdout.write('[Offline LAN 0.9.22] EDGE %s\n' % message)
        return True

    _COMPOUND_REPORT_LIMIT = 8
    _COMPOUND_REPORT_SECONDS = 5.0

    def _report_local_compound(self, now):
        """Name a degenerate transform under the player's own compound.

        The ambient-occlusion decals and the ground splodge hang off that
        compound and project onto the terrain through this provider.
        """
        matrix = self._local_body_pose()
        if matrix is None:
            return False
        target = getattr(self._runtime.bigworld, 'target', None)
        axes = _format_axes(matrix)
        targeting = (
            getattr(target, 'isEnabled', None),
            getattr(target, 'isFull', None),
            getattr(target, 'selectionFovDegrees', None),
            getattr(target, 'maxDistance', None),
            getattr(target, 'skeletonCheckEnabled', None))
        signature = (axes,) + tuple(repr(value) for value in targeting)
        if (signature == self._compound_report_signature or
                self._compound_reports >= self._COMPOUND_REPORT_LIMIT or
                now < self._next_compound_report):
            return False
        self._compound_report_signature = signature
        self._next_compound_report = now + self._COMPOUND_REPORT_SECONDS
        self._compound_reports += 1
        if self._compound_reports == 1:
            self._report_local_decals()
        sys.stdout.write(
            '[Offline LAN 0.9.22] COMPOUND at=%s axes=%s\n' % (
                _format_xyz(matrix.translation), axes))
        # PyTarget.entity dereferences the picked entity with no null check.
        # Calling the object is the guarded read #1513 itself uses.
        entity = target() if callable(target) else None
        sys.stdout.write(
            '[Offline LAN 0.9.22] TARGETING enabled=%s full=%s fov=%s max=%s '
            'skeleton=%s entity=%s\n' % (
                targeting[0], targeting[1], targeting[2], targeting[3],
                targeting[4],
                getattr(entity, 'id', None)))
        return True

    def _report_local_decals(self):
        """Report the exact decal transforms the player's tank was built on."""
        entity = (self._server_entity(self._server.vehicle_id)
                  if self._server is not None else None)
        descriptor = getattr(entity, 'typeDescriptor', None)
        if descriptor is None:
            return False
        for part_name in ('chassis', 'hull', 'turret'):
            part = getattr(descriptor, part_name, None)
            decals = getattr(part, 'AODecals', None) or ()
            for index, transform in enumerate(decals):
                sys.stdout.write(
                    '[Offline LAN 0.9.22] AODECAL %s[%d] at=%s axes=%s\n' % (
                        part_name, index, _format_xyz(
                            getattr(transform, 'translation', None)),
                        _format_axes(transform)))
        chassis = getattr(descriptor, 'chassis', None)
        appearance = getattr(entity, 'appearance', None)
        sys.stdout.write(
            '[Offline LAN 0.9.22] AODECAL hullPosition=%s splodge=%s\n' % (
                _format_xyz(getattr(chassis, 'hullPosition', None)),
                getattr(appearance, '_CompoundAppearance__splodge',
                        None) is not None))
        return True

    def _report_target_outline(self, now, chosen, miss, decline, dropped):
        """Keep a bounded sample of changing outline decisions."""
        if chosen is not None:
            message = 'outlined id=%s' % chosen
        elif dropped is not None:
            message = 'none: dropped id=%s, it %s' % dropped
        elif miss is not None:
            message = (
                'none: id=%s is %.1f deg off the cursor at %.0f m'
                % (miss[1], miss[0], miss[2]))
        elif decline is not None:
            message = 'none: id=%s %s' % decline
        else:
            message = 'none: no remote vehicle to consider'
        self._outline_report = message
        if (message == self._outline_logged_report or
                now < self._next_outline_report or
                self._target_reports >= self._TARGET_REPORT_LIMIT):
            return
        self._outline_logged_report = message
        self._next_outline_report = now + self._TARGET_REPORT_SECONDS
        self._target_reports += 1
        sys.stdout.write('[Offline LAN 0.9.22] TARGET %s\n' % message)

    def _clear_target_outline(self):
        """Remove the one edge this port owns.

        ``wgDelEdgeDetectEntity`` resolves the drawer key from the entity's
        current compound, so a removal issued after that compound changed
        deletes nothing and leaves an entry no later call can reach.  Treat
        that state as a lifecycle error; disabling later outlines cannot make
        the already-stale native entry safe.
        """
        entity = self._outlined_entity
        vehicle = self._outlined_vehicle
        model = self._outlined_model
        engine_id = self._outlined_engine_id
        self._outlined_engine_id = None
        self._outlined_entity = None
        self._outlined_vehicle = None
        self._outlined_model = None
        if engine_id is not None:
            self.monitor_vehicle_damaged_devices(0)
        if entity is None and engine_id is None:
            return True
        set_candidate = getattr(
            self._runtime.compatibility, 'set_target_lock_candidate', None)
        if not callable(set_candidate):
            raise RuntimeError(
                '#1513 target-lock candidate boundary is unavailable')
        set_candidate(None)
        if entity is None:
            raise RuntimeError(
                'outlined vehicle %s lost its entity before edge removal' %
                engine_id)
        visual_entity = (vehicle if bool(getattr(
            vehicle, '_offlineNativeRemote', False)) else getattr(
                vehicle, 'bw_entity', None))
        if (vehicle is None or model is None or
                visual_entity is not entity or
                getattr(vehicle, 'model', None) is not model or
                getattr(entity, 'model', None) is None):
            raise RuntimeError(
                'outlined vehicle %s changed its compound before edge '
                'removal' % engine_id)
        remove_edge = getattr(
            self._runtime.bigworld, 'wgDelEdgeDetectEntity', None)
        if not callable(remove_edge):
            raise RuntimeError(
                '#1513 edge-detect remove boundary is unavailable')
        remove_edge(entity)
        self._report_edge('del id=%s' % engine_id)
        return True

    def _disable_target_outline_presentation(self):
        """Best-effort release of presentation state after an outline fault."""
        try:
            self._clear_target_outline()
        except Exception:
            # _clear_target_outline clears its Python ownership fields before
            # crossing either optional native boundary.  Never retry a stale
            # entity or compound after one of those boundaries rejects it.
            pass
        self._outline_blocked = True
        return True

    def _apply_team_observation(self, message, now):
        """Apply server-validated team radio spotting to presentation.

        The hidden worker performs native LOS for both bot and player
        observers; the server validates and relays one canonical team view
        here.  The worker owns the relative visibility clock, while the local
        565 m presentation AOI remains stock client behaviour.
        """
        if message.get('type') != 'bot_observation' or self.client is None:
            return False
        local_team = int(self.client.team)
        now = float(now)
        deadlines = {}
        for contact in message.get('contacts') or ():
            if (not isinstance(contact, dict) or
                    int(contact.get('observing_team', 0)) != local_team):
                continue
            kind = contact.get('target_kind')
            record_kind = 'player' if kind == 'human' else kind
            if record_kind not in ('player', 'bot'):
                continue
            try:
                target_id = int(contact.get('target_id'))
            except (TypeError, ValueError):
                continue
            try:
                time_left = float(contact.get('time_left'))
            except (TypeError, ValueError, OverflowError):
                raise ValueError('team spot memory time is invalid')
            if (math.isnan(time_left) or math.isinf(time_left) or
                    time_left < 0.0 or
                    time_left > spotting.DESIGNATED_SPOT_MEMORY_SECONDS or
                    bool(contact.get('visible')) != (time_left > 0.0)):
                raise ValueError('team spot memory time is invalid')
            deadlines[(record_kind, target_id)] = now + time_left

        # This is a complete worker-owned relative snapshot.  Replace its
        # radio clock, including zero/absent contacts, so a remembered
        # positive can never renew itself at each relay.
        for record in self._records.values():
            if (record.get('local') or
                    int((record.get('state') or {}).get('team', 0)) ==
                    local_team):
                continue
            key = (record.get('kind'), int(record.get('network_id', 0)))
            record['radio_spot_until'] = float(deadlines.get(key, 0.0))

        changed = False
        for record in self._records.values():
            state = record.get('state') or {}
            if (record.get('local') or not record.get('presentation') or
                    not record.get('ready') or record.get('tombstone') or
                    int(state.get('team', 0)) == local_team):
                continue
            entity = self._server_entity(record['engine_id'])
            if entity is None:
                continue
            remembered = now < max(
                float(record.get('spot_until', 0.0)),
                float(record.get('radio_spot_until', 0.0)))
            previous = (
                bool(record.get('spot_visible', False)),
                bool(record.get(
                    'spot_marker_visible',
                    record.get('spot_visible', False))))
            visible = self._apply_spot_presentation(
                record, entity, remembered)
            if visible != previous:
                changed = True
        return changed

    def _observe_local_vehicle(self, message, now):
        """Feed authority visibility into the native #1513 Sixth Sense HUD."""
        if (self._sixth_sense is None or
                message.get('type') != 'bot_observation'):
            return False
        local_id = int(self.client.player_id)
        local_team = int(self.client.team)
        visible = any(
            contact.get('target_kind') == 'human' and
            int(contact.get('target_id', -1)) == local_id and
            int(contact.get('observing_team', 0)) != local_team and
            bool(contact.get('fresh'))
            for contact in (message.get('contacts') or ())
            if isinstance(contact, dict))
        self._sixth_sense.observe(visible, now)
        return visible

    def _maybe_send_battle_ready(self):
        """Open the shared countdown after the complete line-up has entered.

        Bot presentation remains staggered to keep one 32-bit render callback
        from constructing 29 HD compounds.  It now finishes behind the stock
        BattleLoading screen instead of spending the first countdown seconds
        loading the line-up that will shortly begin moving.
        """
        if self._ready_sent or self._battle_live:
            return False
        expected_players = len(self._start_message.get('players') or ())
        player_records = [record for record in self._records.values()
                          if record.get('kind') == 'player' and
                          not record.get('tombstone')]
        if (len(player_records) != expected_players or
                any(not record.get('ready') for record in player_records)):
            return False
        expected_bots = len(self._start_message.get('bots') or ())
        bot_records = [record for record in self._records.values()
                       if record.get('kind') == 'bot' and
                       not record.get('tombstone')]
        if (len(bot_records) != expected_bots or
                self._pending_bot_create_order or
                self._pending_bot_creates or
                any(not record.get('ready') for record in bot_records)):
            return False
        ready = getattr(self.client, 'send_battle_ready', None)
        if not callable(ready):
            return False
        bases = getattr(self._spawn_planner, 'bases', None)
        if not ready(bases):
            raise RuntimeError('LAN server did not accept battle readiness')
        self._ready_sent = True
        return True

    def _sample_ground_plane(self, position, yaw, descriptor=None):
        """Fit one continuous terrain plane under the local suspension."""
        length = 5.0
        width = 3.0
        try:
            hit_tester = _field(_field(descriptor, 'hull', {}),
                                'hitTester', None)
            bbox = getattr(hit_tester, 'bbox', None)
            length = max(3.0, abs(float(bbox[0][2])) +
                         abs(float(bbox[1][2])))
            width = max(2.0, abs(float(bbox[0][0])) +
                        abs(float(bbox[1][0])))
        except (TypeError, ValueError, IndexError, AttributeError):
            pass
        half_length = length * 0.5
        half_width = width * 0.5
        sin_yaw, cos_yaw = math.sin(yaw), math.cos(yaw)
        front_y = self._ground_y(
            position[0] + sin_yaw * half_length,
            position[2] + cos_yaw * half_length, position[1])
        rear_y = self._ground_y(
            position[0] - sin_yaw * half_length,
            position[2] - cos_yaw * half_length, position[1])
        right_y = self._ground_y(
            position[0] + cos_yaw * half_width,
            position[2] - sin_yaw * half_width, position[1])
        left_y = self._ground_y(
            position[0] - cos_yaw * half_width,
            position[2] + sin_yaw * half_width, position[1])
        center_y = self._ground_y(
            position[0], position[2], position[1])
        if None in (front_y, rear_y, right_y, left_y, center_y):
            return None
        long_mid = (front_y + rear_y) * 0.5
        side_mid = (right_y + left_y) * 0.5
        if (abs(long_mid - side_mid) > GROUND_PLANE_EPSILON or
                abs(center_y - long_mid) > GROUND_PLANE_EPSILON or
                abs(center_y - side_mid) > GROUND_PLANE_EPSILON):
            return None
        height_forward = (front_y - rear_y) / length
        height_right = (right_y - left_y) / width
        gradient_x = (height_forward * sin_yaw +
                      height_right * cos_yaw)
        gradient_z = (height_forward * cos_yaw -
                      height_right * sin_yaw)
        slope_tangent = math.sqrt(
            height_forward * height_forward +
            height_right * height_right)
        downhill_x = -gradient_x
        downhill_z = -gradient_z
        downhill_length = math.sqrt(
            downhill_x * downhill_x + downhill_z * downhill_z)
        if downhill_length > 0.001:
            downhill_x /= downhill_length
            downhill_z /= downhill_length
        else:
            downhill_x = downhill_z = 0.0
        return {
            'center_y': center_y,
            'gradient_x': gradient_x,
            'gradient_z': gradient_z,
            'pitch': -math.atan2(front_y - rear_y, length),
            # BigWorld applies YPR as yaw, pitch and then roll.  Forward
            # pitch therefore shortens the horizontal right axis used by the
            # final roll; dividing by its plane length keeps the displayed
            # hull normal identical to the fitted terrain normal.
            'roll': math.atan2(
                height_right, math.sqrt(
                    1.0 + height_forward * height_forward)),
            'slope_tangent': slope_tangent,
            'up_cosine': 1.0 / math.sqrt(
                1.0 + slope_tangent * slope_tangent),
            'downhill': (downhill_x, 0.0, downhill_z),
        }

    def _commit_ground_plane(self, plane, force_raw=False):
        """Publish one accepted terrain plane to pose and slide physics."""
        pitch = float(plane['pitch'])
        roll = float(plane['roll'])
        if force_raw:
            self._local_pitch = pitch
            self._local_roll = roll
        else:
            self._local_pitch += (pitch - self._local_pitch) * 0.5
            self._local_roll += (roll - self._local_roll) * 0.5
        self._local_downhill = tuple(plane['downhill'])
        self._local_slope_tangent = float(plane['slope_tangent'])
        self._local_surface_up_cosine = float(plane['up_cosine'])
        self._local_ground_plane = plane
        return self._local_pitch

    def _ground_pitch(self, position, yaw, descriptor=None):
        """Sample one continuous four-point suspension pose."""
        plane = self._sample_ground_plane(position, yaw, descriptor)
        if plane is None:
            self._local_downhill = (0.0, 0.0, 0.0)
            self._local_slope_tangent = 0.0
            self._local_ground_plane = None
            self._local_surface_up_cosine = None
            return self._local_pitch
        force_raw = (
            math.atan(float(plane['slope_tangent'])) >
            GROUND_RAW_TILT_RADIANS)
        return self._commit_ground_plane(plane, force_raw=force_raw)

    def _drive_pitch(self, position, yaw):
        """Copy the 0.8.2 close-range drive slope probe exactly.

        This is deliberately separate from the four-point visual hull pose.
        The drive law skips bridge decks above the hull and clamps walls and
        cliff faces before their gradient reaches longitudinal physics.
        """
        sine, cosine = math.sin(yaw), math.cos(yaw)
        distance = 2.0
        wall_rise = distance * 1.43

        def ground_y(x, z):
            start_y = position[1] + 15.0
            ground_filter = self._ground_filter(x, z)
            for unused in range(3):
                try:
                    collision = self._collide_down(
                        self._vector((x, start_y, z)),
                        self._vector((x, position[1] - 60.0, z)),
                        ground_filter)
                except Exception:
                    return None
                if collision is None:
                    return None
                value = float(collision[0].y)
                if value > position[1] + 3.5:
                    start_y = value - 0.5
                    continue
                return value
            return None

        front = ground_y(
            position[0] + sine * distance,
            position[2] + cosine * distance)
        rear = ground_y(
            position[0] - sine * distance,
            position[2] - cosine * distance)
        if front is None or rear is None:
            return 0.0
        front_delta = max(
            -wall_rise, min(wall_rise, front - position[1]))
        rear_delta = max(
            -wall_rise, min(wall_rise, rear - position[1]))
        pitch = -math.atan2(
            front_delta - rear_delta, 2.0 * distance)
        return max(-0.96, min(0.96, pitch))

    def _smoothed_drive_pitch(self, position, yaw):
        raw = self._drive_pitch(position, yaw)
        history = self._local_drive_pitch_history
        if history is None:
            history = [raw] * 5
            self._local_drive_pitch_history = history
        history.append(raw)
        del history[:-5]
        median = sorted(history)[2]
        previous = self._local_smooth_drive_pitch
        pitch = previous + (median - previous) * 0.5
        self._local_smooth_drive_pitch = pitch
        self._local_last_pitch = pitch
        return pitch

    def _motion_is_clear(self, entity, position, yaw, speed, dt,
                         allow_crush_drive=False, hull_yaw=None):
        """Thin tuple-to-Vector adapter around the copied 0.8.2 probe."""
        if getattr(self, '_local_destructible_send_failed', False):
            return False
        self._local_motion_soft_block = False
        self._local_motion_cap_crushed = False
        self._local_motion_kinds = '-'
        self._local_motion_status = 'clear'
        if not self._arena_motion_is_clear(
                entity, position, yaw, speed, dt, hull_yaw=hull_yaw):
            self._local_motion_kinds = 'arena'
            self._local_motion_status = 'hard'
            return False
        # Ram separation, airborne carry and wall deflection can translate the
        # tank across its heading.  Keep the native corridor aligned with that
        # travel while retaining the real chassis orientation and footprint.
        world_hull_yaw = yaw if hull_yaw is None else hull_yaw
        world_motion_yaw = (None if hull_yaw is None else
                            yaw if speed >= 0.0 else yaw + math.pi)
        destructible_motion = ({}
                               if world_motion_yaw is None else
                               {'motion_yaw': world_motion_yaw})
        kinetic_speed = None
        if allow_crush_drive:
            params = self._local_physics
            if not isinstance(params, dict):
                raise RuntimeError(
                    'player effective physics parameters are unavailable')
            limit_name = 'speedBwd' if speed < 0.0 else 'speedFwd'
            kinetic_speed = (-float(params[limit_name]) if speed < 0.0 else
                             float(params[limit_name]))
        if self._destructibles is not None and kinetic_speed is not None:
            proposer = getattr(
                self._destructibles, '_catalog_motion_proposal', None)
            proposal = (proposer(
                self._avatar.spaceID, self._vector(position), world_hull_yaw,
                speed, entity.typeDescriptor, self._clock(),
                dt=dt, kinetic_speed=kinetic_speed,
                **destructible_motion)
                if callable(proposer) else None)
            # Lightweight injected adapters predating the proposal seam keep
            # using the read-only catalog path below. Production's pinned
            # sensor always returns the typed proposal dictionary.
            if (isinstance(proposal, dict) and
                    bool(proposal.get('requires_commit', False))):
                self._local_motion_kinds = str(
                    proposal.get('kinds', '-'))
                self._local_motion_status = 'kinetic'
                token = self._destructible_contact_token(
                    proposal.get('token'))
                committer = getattr(
                    self._destructibles, 'commit_local_prediction', None)
                predictor = getattr(
                    self._destructibles, 'begin_local_prediction', None)
                if token is not None and callable(committer):
                    predicted = bool(committer(
                        self._avatar.spaceID, token,
                        self._vector(position), yaw, speed))
                else:
                    predicted = bool(
                        token is not None and callable(predictor) and
                        predictor(token))
                world_status = world_collision.check_horizontal_collision(
                    self._runtime.bigworld, self._runtime.math,
                    self._avatar.spaceID, self._vector(position),
                    world_hull_yaw, speed,
                    entity.typeDescriptor, self._local_airborne, dt, True,
                    True, kinetic_speed, commit_enabled=False,
                    motion_yaw=world_motion_yaw)
                if isinstance(world_status, bool):
                    world_status = 'hard' if world_status else 'clear'
                if world_status not in ('clear', 'kinetic'):
                    if predicted:
                        self._clear_local_destructible_prediction(token)
                    self._local_motion_status = 'hard'
                    return False
                previous_seq = self._local_destructible_contact_seq
                if not self._queue_local_destructible_contact(
                        proposal, position, yaw, speed, dt):
                    if predicted:
                        self._clear_local_destructible_prediction(token)
                    return False
                if self._local_destructible_contact_seq != previous_seq:
                    sender = getattr(self._sender, 'send_current', None)
                    if not callable(sender) or not sender():
                        failed_seq = self._local_destructible_contact_seq
                        self._local_destructible_contacts.pop(
                            failed_seq, None)
                        self._local_destructible_safe_poses.pop(
                            failed_seq, None)
                        self._local_destructible_contact_seq = previous_seq
                        if predicted:
                            self._clear_local_destructible_prediction(token)
                        self._local_destructible_send_failed = True
                        return False
                    # The pre-advance pose and proposal now precede every
                    # resulting pose in the transport FIFO.  Treat this as
                    # this frame's periodic input too; otherwise a long frame
                    # immediately emits a redundant post-advance sample.
                    self._local_input_sent_during_drive = True
                # This exact local proof owns movement prediction only. Keep
                # advancing the copied vehicle pose while the server relays it
                # to the worker; the worker remains the sole owner of the
                # irreversible map mutation and its canonical LAN event.
                return True
        world_status = world_collision.check_horizontal_collision(
            self._runtime.bigworld, self._runtime.math,
            self._avatar.spaceID, self._vector(position),
            world_hull_yaw, speed,
            entity.typeDescriptor, self._local_airborne, dt, True,
            bool(kinetic_speed is not None), kinetic_speed,
            commit_enabled=False, motion_yaw=world_motion_yaw)
        if isinstance(world_status, bool):
            world_status = 'hard' if world_status else 'clear'
        if world_status == 'hard':
            if self._destructibles is not None:
                if self._destructibles._catalog_pending_at_hull(
                        self._vector(position), world_hull_yaw, speed,
                        entity.typeDescriptor, self._clock(), dt,
                        **destructible_motion):
                    self._local_motion_soft_block = True
                    self._local_motion_kinds = 'broken'
                elif self._destructibles._catalog_hull_contact(
                        self._vector(position), world_hull_yaw, speed,
                        entity.typeDescriptor, dt, **destructible_motion):
                    self._local_motion_kinds = 'world'
            self._local_motion_status = 'hard'
            return False
        if self._destructibles is None:
            return world_status == 'clear'
        detail = self._destructibles._catalog_motion_blocked(
            self._avatar.spaceID, self._vector(position), world_hull_yaw,
            speed, entity.typeDescriptor, self._clock(),
            dt=dt, kinetic_speed=kinetic_speed,
            return_detail=True,
            kinetic_commit=False, commit_enabled=False,
            **destructible_motion)
        # Keep injected legacy test/adaptor seams fail-closed.  Production's
        # exact #1513 sensor always returns the typed receipt above.
        if isinstance(detail, bool):
            detail = {'status': 'hard' if detail else 'clear'}
        elif isinstance(detail, str):
            detail = {'status': detail}
        if not isinstance(detail, dict):
            raise RuntimeError(
                'local motion resolver detail is unavailable')
        status = detail.get('status')
        if status not in ('clear', 'crushed', 'soft', 'hard', 'approach'):
            raise RuntimeError(
                'local motion resolver returned an invalid status')
        self._local_motion_kinds = str(detail.get('kinds', '-'))
        self._local_motion_status = status
        if status == 'hard':
            return False
        used_kinetic_speed = bool(detail.get('used_kinetic_speed', False))
        accepted_now = bool(detail.get('accepted_now', False))
        if used_kinetic_speed and not (
                accepted_now and status == 'crushed' and
                detail.get('token')):
            raise RuntimeError(
                'local cap-crush receipt is inconsistent')
        if accepted_now and status in ('clear', 'approach', 'soft'):
            raise RuntimeError(
                'local contact receipt is inconsistent')
        if status == 'approach':
            status = 'clear'
        if accepted_now and used_kinetic_speed:
            self._local_motion_cap_crushed = True
            return False
        if status == 'soft':
            self._local_motion_soft_block = True
        return status in ('clear', 'crushed')

    def _resolve_bot_motion(self, bot_id, position, yaw, speed,
                            descriptor, dt, now, commit_enabled=True):
        """Resolve Bot contact: static world first, then exact catalog."""
        pos = self._vector(position)
        bot_state = getattr(self._bots, 'states', {}).get(int(bot_id), {})
        airborne = bool(bot_state.get('airborne', False))
        movement_dir = int(_number(bot_state.get('movement_dir')))
        rotation_dir = int(_number(bot_state.get('rotation_dir')))
        turn_speed = _number(getattr(
            self._bots, '_turn_speeds', {}).get(int(bot_id), 0.0))
        # Reuse a contained exact receipt when available. While its bounded
        # optimisation queue is pending, the complete dual-height, three-lane
        # native corridor from this same authority tick is sufficient; queue
        # pressure is not a physical collision and must not freeze the pose.
        corridor_reusable = getattr(
            self._bots, 'motion_world_corridor_reusable', None)
        if not callable(corridor_reusable):
            corridor_reusable = getattr(
                self._bots, 'motion_world_receipt_reusable', None)
        travel_yaw = (float(yaw) if speed >= 0.0 else
                      float(yaw) + math.pi)
        if (self._destructibles is not None and not airborne and
                movement_dir * float(speed) > 0.0 and rotation_dir == 0 and
                abs(turn_speed) <= 0.01 and callable(corridor_reusable) and
                corridor_reusable(
                    bot_id, position, travel_yaw, speed, now, dt) and
                not self._destructibles._catalog_hull_contact(
                    pos, yaw, speed, descriptor, dt)):
            return 'clear'
        allow_crush_drive = (
            not airborne and
            movement_dir * float(speed) > 0.0)
        kinetic_speed = None
        if allow_crush_drive:
            params = vehicle_physics.derive_params(descriptor)
            limit_name = 'speedBwd' if speed < 0.0 else 'speedFwd'
            kinetic_speed = (-float(params[limit_name]) if speed < 0.0 else
                             float(params[limit_name]))
        world_status = world_collision.check_horizontal_collision(
            self._runtime.bigworld, self._runtime.math,
            self._avatar.spaceID, pos, yaw, speed, descriptor, airborne, dt,
            True, allow_crush_drive, kinetic_speed,
            commit_enabled=commit_enabled)
        if isinstance(world_status, bool):
            world_status = 'hard' if world_status else 'clear'
        self._bot_motion_kinds[int(bot_id)] = '-'
        if world_status == 'hard':
            if (self._destructibles is not None and
                    self._destructibles._catalog_pending_at_hull(
                        pos, yaw, speed, descriptor, now, dt)):
                self._bot_motion_kinds[int(bot_id)] = 'broken'
                return 'soft'
            return 'hard'
        if self._destructibles is None:
            return 'clear' if world_status == 'clear' else 'hard'
        if airborne:
            return 'clear' if world_status == 'clear' else 'hard'
        # A native kinetic result is only a forward candidate.  Always hand it
        # to the catalog's exact contact seam; its ``approach`` result keeps a
        # nearby but non-contact prop clear without granting a destroy.
        detail = self._destructibles._catalog_motion_blocked(
            self._avatar.spaceID, pos, yaw, speed, descriptor, now,
            dt=dt, kinetic_speed=kinetic_speed, return_detail=True,
            kinetic_commit=allow_crush_drive and commit_enabled,
            commit_enabled=commit_enabled)
        if isinstance(detail, bool):
            detail = {'status': 'hard' if detail else 'clear'}
        elif isinstance(detail, str):
            detail = {'status': detail}
        if not isinstance(detail, dict):
            raise RuntimeError('bot motion resolver detail is unavailable')
        status = detail.get('status')
        if status not in ('clear', 'crushed', 'soft', 'hard', 'approach'):
            raise RuntimeError(
                'bot motion resolver returned an invalid status')
        self._bot_motion_kinds[int(bot_id)] = str(detail.get('kinds', '-'))
        if status == 'hard':
            return 'hard'
        used_kinetic_speed = bool(detail.get('used_kinetic_speed', False))
        accepted_now = bool(detail.get('accepted_now', False))
        if used_kinetic_speed and not (
                accepted_now and status == 'crushed' and
                detail.get('token')):
            raise RuntimeError('bot cap-crush receipt is inconsistent')
        if accepted_now and status in ('clear', 'approach', 'soft'):
            raise RuntimeError('bot contact receipt is inconsistent')
        if status == 'approach':
            return 'clear'
        if accepted_now and used_kinetic_speed:
            return 'cap_crushed'
        return status

    @staticmethod
    def _collision_shape(descriptor):
        """Return the current 0.8.2 chassis hit-tester body."""
        return tank_collision.chassis_shape(descriptor)

    def _install_native_ram_contact_hook(self):
        """Observe #1513's real vehicle-collision callback without replacing it."""
        if self._worker_mode or self._native_ram_contact_hook is not None:
            return False
        avatar_type = type(self._avatar)
        method_name = 'handleVehicleCollidedVehicle'
        raw_original = getattr(avatar_type, '__dict__', {}).get(method_name)
        if not callable(raw_original):
            raise RuntimeError(
                '#1513 PlayerAvatar collision callback is unavailable')
        owner = self

        def observe_after_native(avatar, veh_a, veh_b, hit_point,
                                 contact_time):
            result = raw_original(
                avatar, veh_a, veh_b, hit_point, contact_time)
            if avatar is owner._avatar:
                try:
                    owner._observe_native_ram_contact(
                        veh_a, veh_b, hit_point, contact_time)
                except Exception as error:
                    owner._warn_optional_failure(
                        'native ram contact proof', error)
            return result

        setattr(avatar_type, method_name, observe_after_native)
        self._native_ram_contact_hook = (
            avatar_type, method_name, raw_original, observe_after_native)
        return True

    def _restore_native_ram_contact_hook(self):
        hook = self._native_ram_contact_hook
        self._native_ram_contact_hook = None
        if hook is None:
            return False
        avatar_type, method_name, original, replacement = hook
        if getattr(avatar_type, '__dict__', {}).get(method_name) is replacement:
            setattr(avatar_type, method_name, original)
        return True

    def _native_ram_bot_record(self, vehicle):
        try:
            engine_id = int(vehicle.id)
        except (AttributeError, TypeError, ValueError, OverflowError):
            return None
        for record in self._records.values():
            if (record.get('kind') == 'bot' and record.get('ready') and
                    int(record.get('engine_id', 0)) == engine_id):
                return record
        return None

    @staticmethod
    def _validated_ram_contact_normal(contact):
        if not isinstance(contact, (list, tuple)) or len(contact) < 2:
            return None
        try:
            normal_x = float(contact[0])
            normal_z = float(contact[1])
        except (TypeError, ValueError, OverflowError):
            return None
        length = math.sqrt(normal_x * normal_x + normal_z * normal_z)
        if (math.isnan(length) or math.isinf(length) or
                length <= 0.000001):
            return None
        return normal_x / length, normal_z / length

    def _native_ram_vehicle_armor(self, vehicle, matrix, hit_point,
                                  inward_normal, chassis_matrix=None):
        """Return structural armour from a contact-normal probe."""
        descriptor = getattr(vehicle, 'typeDescriptor', None)
        if descriptor is None or matrix is None:
            return None
        inward_normal = self._validated_ram_contact_normal(inward_normal)
        if inward_normal is None:
            return None
        hit = _xyz(hit_point)
        center = _xyz(getattr(
            matrix, 'translation', getattr(vehicle, 'position', None)))
        shape = self._collision_shape(descriptor)
        reach = math.sqrt(shape[0] * shape[0] + shape[1] * shape[1]) + 1.0
        center_depth = ((center[0] - hit[0]) * inward_normal[0] +
                        (center[2] - hit[2]) * inward_normal[1])
        if math.isnan(center_depth) or math.isinf(center_depth):
            return None
        # Enter from the contacted side and stop on the centre plane along
        # this normal. Continuing through the far half could mistake a remote
        # plate for structure behind a near-side track or skirt.
        if center_depth < -1.0e-6:
            return None
        center_depth = max(0.0, center_depth)
        start = self._vector((
            hit[0] - inward_normal[0] * reach,
            hit[1],
            hit[2] - inward_normal[1] * reach))
        end = self._vector((
            hit[0] + inward_normal[0] * center_depth,
            hit[1],
            hit[2] + inward_normal[1] * center_depth))
        collisions = collide_vehicle_at_matrix(
            vehicle, matrix, start, end, self._runtime.math,
            chassis_matrix=chassis_matrix)
        if not collisions:
            return None
        for collision in sorted(
                collisions, key=lambda item: float(item.dist)):
            material = getattr(collision, 'matInfo', None)
            try:
                armor = float(getattr(material, 'armor'))
                damage_factor = float(getattr(
                    material, 'vehicleDamageFactor'))
            except (AttributeError, TypeError, ValueError, OverflowError):
                return None
            if (math.isnan(armor) or math.isinf(armor) or armor <= 0.0 or
                    math.isnan(damage_factor) or math.isinf(damage_factor)):
                continue
            if damage_factor <= 0.0:
                continue
            return {'armor': armor, 'screened': False}
        return None

    def _ram_contact_armor_status(self, first, second, contact):
        """Classify one native contact probe without folding transient state."""
        if not self._worker_mode:
            return 'pending', None
        vehicles = []
        records = []
        for body in (first, second):
            try:
                body_kind = str(body.get('kind', 'bot'))
                network_id = int(body.get('network_id', body['id']))
            except (KeyError, TypeError, ValueError, OverflowError):
                return 'invalid', None
            if body_kind not in ('bot', 'player') or network_id <= 0:
                return 'invalid', None
            record = self._records.get('%s:%s' % (body_kind, network_id))
            if (record is None or record.get('kind') != body_kind or
                    not record.get('ready') or record.get('tombstone')):
                return 'pending', None
            vehicle = self._server_entity(record.get('engine_id'))
            if (vehicle is None or not getattr(vehicle, 'isStarted', False) or
                    getattr(vehicle, 'typeDescriptor', None) is None):
                return 'pending', None
            vehicles.append(vehicle)
            records.append(record)
        if int(first['id']) == int(second['id']):
            return 'invalid', None
        if not tank_collision.vertical_overlap(
                first.get('y'), first['shape'],
                second.get('y'), second['shape']):
            return 'invalid', None
        overlap_point = self._ram_obb_overlap_point(first, second)
        if overlap_point is None:
            return 'invalid', None
        contact_normal = self._validated_ram_contact_normal(contact)
        if contact_normal is None:
            return 'invalid', None
        low = max(
            float(first['y']) + float(first['shape'][2]),
            float(second['y']) + float(second['shape'][2]))
        high = min(
            float(first['y']) + float(first['shape'][3]),
            float(second['y']) + float(second['shape'][3]))
        if high <= low:
            return 'invalid', None
        hit_point = self._vector((
            overlap_point[0], (low + high) * 0.5, overlap_point[1]))
        plates = []
        for index, (body, vehicle, record) in enumerate(zip(
                (first, second), vehicles, records)):
            ground_matrix = self._ram_pose_matrix(
                (body['x'], body['y'], body['z']), body['yaw'],
                _number(body.get('pitch')), _number(body.get('roll')))
            matrix, chassis_matrix = self._projectile_vehicle_matrices(
                record, vehicle, ground_matrix=ground_matrix)
            inward_normal = (contact_normal if index == 0 else
                              (-contact_normal[0], -contact_normal[1]))
            plate = self._native_ram_vehicle_armor(
                vehicle, matrix, hit_point, inward_normal,
                chassis_matrix=chassis_matrix)
            if plate is None:
                # At this point both exact entities and their contact geometry
                # are ready. None now means the native ray found no supported
                # contact layer, rather than an asynchronous startup failure.
                return 'unavailable', None
            plates.append(float(plate['armor']))
        return 'available', tuple(plates)

    def _bot_ram_contact_armor(self, first, second, contact):
        """Probe both real #1513 hit testers at one worker-owned contact."""
        status, armors = self._ram_contact_armor_status(
            first, second, contact)
        return armors if status == 'available' else None

    @staticmethod
    def _validated_human_ram_probe_body(raw):
        allowed = set((
            'id', 'vehicle', 'x', 'y', 'z', 'yaw', 'pitch', 'roll',
            'shape'))
        if not isinstance(raw, dict) or set(raw) != allowed:
            raise RuntimeError('worker human ram probe body is invalid')
        player_id = lan_protocol._exact_int(raw.get('id'))
        vehicle = raw.get('vehicle')
        if (player_id is None or not 0 < player_id <= 2147483647 or
                not isinstance(vehicle, _STRING_TYPES) or
                not vehicle or len(vehicle) > 80 or
                not isinstance(raw.get('shape'), (list, tuple)) or
                len(raw['shape']) != 4):
            raise RuntimeError('worker human ram probe body is invalid')
        try:
            values = dict((name, float(raw[name])) for name in (
                'x', 'y', 'z', 'yaw', 'pitch', 'roll'))
            shape = tuple(float(value) for value in raw['shape'])
        except (KeyError, TypeError, ValueError, OverflowError):
            raise RuntimeError('worker human ram probe body is invalid')
        if (any(math.isnan(value) or math.isinf(value)
                for value in list(values.values()) + list(shape)) or
                any(abs(values[name]) > 5000.0 for name in ('x', 'y', 'z')) or
                not 0.5 <= shape[0] <= 20.0 or
                not 0.75 <= shape[1] <= 30.0 or
                not -20.0 <= shape[2] < shape[3] <= 30.0):
            raise RuntimeError('worker human ram probe body is invalid')
        return {
            'id': player_id, 'kind': 'player', 'network_id': player_id,
            'vehicle': str(vehicle), 'alive': True,
            'x': values['x'], 'y': values['y'], 'z': values['z'],
            'yaw': values['yaw'], 'pitch': values['pitch'],
            'roll': values['roll'], 'shape': shape,
        }

    def _human_ram_armor_results(self):
        """Probe each exact server substep with the worker's native entities."""
        if not self._worker_mode:
            return []
        raw_requests = (self._last_snapshot or {}).get('human_ram_probes', ())
        if (not isinstance(raw_requests, (list, tuple)) or
                len(raw_requests) > 64):
            raise RuntimeError('worker human ram probe batch is invalid')
        results = []
        seen = set()
        for raw in raw_requests:
            if not isinstance(raw, dict) or set(raw) != set((
                    'seq', 'contact_normal', 'first', 'second')):
                raise RuntimeError('worker human ram probe is invalid')
            sequence = lan_protocol._exact_int(raw.get('seq'))
            if (sequence is None or not 0 < sequence <= 2147483647 or
                    sequence in seen):
                raise RuntimeError('worker human ram probe is invalid')
            seen.add(sequence)
            contact_normal = self._validated_ram_contact_normal(
                raw.get('contact_normal'))
            if contact_normal is None:
                raise RuntimeError('worker human ram probe is invalid')
            first = self._validated_human_ram_probe_body(raw.get('first'))
            second = self._validated_human_ram_probe_body(raw.get('second'))
            if first['id'] >= second['id']:
                raise RuntimeError('worker human ram probe order is invalid')
            if (contact_normal[0] * (first['x'] - second['x']) +
                    contact_normal[1] *
                    (first['z'] - second['z'])) <= 1.0e-6:
                raise RuntimeError('worker human ram probe is invalid')
            ready = True
            for body in (first, second):
                record = self._records.get('player:%s' % body['network_id'])
                if (record is None or not record.get('ready') or
                        record.get('tombstone')):
                    ready = False
                    break
                state = record.get('state') or {}
                if (record.get('kind') != 'player' or
                        int(record.get('network_id', 0)) != body['id'] or
                        str(state.get('vehicle') or '') != body['vehicle']):
                    raise RuntimeError(
                        'worker human ram probe identity is invalid')
                entity = self._server_entity(record.get('engine_id'))
                if (entity is None or not getattr(entity, 'isStarted', False) or
                        getattr(entity, 'typeDescriptor', None) is None):
                    ready = False
                    break
            if not ready:
                # Entity startup is asynchronous. Omit this response so the
                # server retains and republishes the exact pending substep.
                continue
            status, armors = self._ram_contact_armor_status(
                first, second, contact_normal)
            if status == 'pending':
                continue
            if status == 'invalid':
                raise RuntimeError(
                    'worker human ram probe geometry is invalid')
            result = {
                'seq': sequence, 'first_id': first['id'],
                'second_id': second['id'], 'available': status == 'available',
            }
            if status == 'available':
                result['armor_first'] = float(armors[0])
                result['armor_second'] = float(armors[1])
            results.append(result)
        return results

    @staticmethod
    def _native_ram_velocity(vehicle):
        velocity = getattr(getattr(vehicle, 'filter', None), 'velocity', None)
        if velocity is None:
            return None
        values = _xyz(velocity)
        if any(math.isnan(value) or math.isinf(value) for value in values):
            return None
        return values

    def _ram_pose_matrix(self, position, yaw, pitch=0.0, roll=0.0):
        matrix = self._runtime.math.Matrix()
        matrix.setRotateYPR((float(yaw), float(pitch), float(roll)))
        matrix.translation = self._vector(position)
        return matrix

    def _queue_ram_contact_proof(self, record, local_vehicle, bot_vehicle,
                                 hit_point, player_velocity, bot_velocity,
                                 contact_time_us, own_pose=None,
                                 bot_pose=None, player_ram_profile=None,
                                 contact_normal=None):
        """Queue one immutable contact episode without applying HP locally."""
        if len(self._native_ram_contact_proofs) >= 16:
            return False
        bot_id = int(record['network_id'])
        if own_pose is None:
            own_pose = (
                self._local_position[0], self._local_position[1],
                self._local_position[2], self._local_yaw,
                self._local_pitch, self._local_roll)
        if bot_pose is None:
            bot_position = _xyz(getattr(
                bot_vehicle, 'position', (0.0, 0.0, 0.0)))
            bot_matrix = getattr(bot_vehicle, 'matrix', None)
            bot_pose = (
                bot_position[0], bot_position[1], bot_position[2],
                float(getattr(bot_matrix, 'yaw', 0.0)), 0.0, 0.0)
        if player_ram_profile is None:
            player_ram_profile = self._ram_profile(
                local_vehicle.typeDescriptor, local=True)
        if contact_normal is None:
            player_shape = self._collision_shape(local_vehicle.typeDescriptor)
            bot_shape = self._collision_shape(bot_vehicle.typeDescriptor)
            contact = tank_collision.obb_impact_contact(
                own_pose[0], own_pose[2], own_pose[3], player_shape,
                (player_velocity[0], player_velocity[2]),
                bot_pose[0], bot_pose[2], bot_pose[3], bot_shape,
                (bot_velocity[0], bot_velocity[2]))
            contact_normal = self._validated_ram_contact_normal(contact)
        else:
            contact_normal = self._validated_ram_contact_normal(
                contact_normal)
        player_spall = float(player_ram_profile['spall_coefficient'])
        player_bonus = float(player_ram_profile['ramming_bonus'])
        proof = {
            'bot_id': bot_id,
            'record': record,
            'local_vehicle': local_vehicle,
            'bot_vehicle': bot_vehicle,
            'hit_point': _xyz(hit_point),
            'native_contact_time_us': int(contact_time_us),
            'x': float(own_pose[0]), 'y': float(own_pose[1]),
            'z': float(own_pose[2]), 'yaw': float(own_pose[3]),
            'pitch': float(own_pose[4]), 'roll': float(own_pose[5]),
            'local_matrix': self._ram_pose_matrix(
                own_pose[:3], own_pose[3], own_pose[4], own_pose[5]),
            'bot_matrix': self._ram_pose_matrix(
                bot_pose[:3], bot_pose[3], bot_pose[4], bot_pose[5]),
            'contact_normal': contact_normal,
            'contact_spall_player': player_spall,
            'contact_bonus_player': player_bonus,
            'vx': float(player_velocity[0]),
            'vy': float(player_velocity[1]),
            'vz': float(player_velocity[2]),
            'bot_vx': float(bot_velocity[0]),
            'bot_vy': float(bot_velocity[1]),
            'bot_vz': float(bot_velocity[2]),
            'attempts': 0,
        }
        self._native_ram_event_seq += 1
        event_seq = self._native_ram_event_seq
        proof['event_seq'] = event_seq
        self._native_ram_contact_proofs[event_seq] = proof
        return self._retry_native_ram_contact_proof(event_seq)

    def _observe_native_ram_contact(self, veh_a, veh_b, hit_point,
                                    contact_time):
        """Freeze one native callback for immediate or next-frame proof."""
        local_id = int(self._server.vehicle_id)
        local = veh_a if int(getattr(veh_a, 'id', 0)) == local_id else (
            veh_b if int(getattr(veh_b, 'id', 0)) == local_id else None)
        if local is None:
            return False
        other = veh_b if local is veh_a else veh_a
        record = self._native_ram_bot_record(other)
        if record is None:
            return False
        record_team = int(_number(
            (record.get('state') or {}).get('team')))
        local_team = int(_number(getattr(self.client, 'team', 0)))
        if record_team in (1, 2) and record_team == local_team:
            return False
        bot_id = int(record['network_id'])
        if bot_id in self._local_ram_episode_contacts:
            return False
        try:
            contact_time = float(contact_time)
        except (TypeError, ValueError, OverflowError):
            return False
        if math.isnan(contact_time) or math.isinf(contact_time):
            return False
        velocity = self._native_ram_velocity(local)
        bot_velocity = self._native_ram_velocity(other)
        if velocity is None or bot_velocity is None:
            return False
        estimated = self._estimated_motion_time_us(self._clock())
        if estimated is None:
            return False
        queued = self._queue_ram_contact_proof(
            record, local, other, hit_point,
            velocity, bot_velocity, estimated)
        if queued or any(
                proof.get('bot_id') == bot_id for proof in
                self._native_ram_contact_proofs.values()):
            self._local_ram_episode_contacts = frozenset(
                set(self._local_ram_episode_contacts) | {bot_id})
        return queued

    def _retry_native_ram_contact_proof(self, event_seq):
        proof = self._native_ram_contact_proofs.get(event_seq)
        if proof is None:
            return False
        bot_id = proof['bot_id']
        proof['attempts'] += 1
        record = proof['record']
        presentation_time_us = record.get('presentation_time_us')
        revision = self._ram_bot_revision_at(bot_id, presentation_time_us)
        local_matrix = proof['local_matrix']
        bot_matrix = proof['bot_matrix']
        contact_normal = proof.get('contact_normal')
        if contact_normal is None:
            player_plate = bot_plate = None
        else:
            player_plate = self._native_ram_vehicle_armor(
                proof['local_vehicle'], local_matrix, proof['hit_point'],
                contact_normal)
            bot_plate = self._native_ram_vehicle_armor(
                proof['bot_vehicle'], bot_matrix, proof['hit_point'],
                (-contact_normal[0], -contact_normal[1]))
        if (revision is None or presentation_time_us is None or
                player_plate is None or bot_plate is None):
            if proof['attempts'] < 2:
                return False
            self._native_ram_contact_proofs.pop(event_seq, None)
            signature = (bot_id, player_plate is None, bot_plate is None)
            if signature not in self._native_ram_contact_failures:
                self._native_ram_contact_failures.add(signature)
                sys.stdout.write(
                    '[Offline LAN 0.9.22] RAM native contact unsupported '
                    'bot_id=%d player_plate=%s bot_plate=%s\n' % (
                        bot_id, player_plate, bot_plate))
            return False
        if len(self._local_ram_receipts) >= 16:
            return False
        self._local_ram_seq += 1
        hit = proof['hit_point']
        player_armor = player_plate['armor']
        bot_armor = bot_plate['armor']
        receipt = {
            'seq': self._local_ram_seq,
            'bot_id': bot_id,
            'bot_state_revision': int(revision),
            'presentation_time_us': int(presentation_time_us),
            'native_contact_time_us': int(
                proof['native_contact_time_us']),
            'contact_x': float(hit[0]),
            'contact_y': float(hit[1]),
            'contact_z': float(hit[2]),
            'contact_normal_x': float(contact_normal[0]),
            'contact_normal_z': float(contact_normal[1]),
            'contact_armor_player': float(player_armor),
            'contact_armor_bot': float(bot_armor),
            'contact_screened_player': bool(player_plate['screened']),
            'contact_screened_bot': bool(bot_plate['screened']),
            'contact_spall_player': proof['contact_spall_player'],
            'contact_bonus_player': proof['contact_bonus_player'],
            'x': proof['x'], 'y': proof['y'], 'z': proof['z'],
            'yaw': proof['yaw'], 'pitch': proof['pitch'],
            'roll': proof['roll'], 'vx': proof['vx'], 'vy': proof['vy'],
            'vz': proof['vz'],
            'bot_vx': proof['bot_vx'], 'bot_vy': proof['bot_vy'],
            'bot_vz': proof['bot_vz'],
        }
        self._local_ram_receipt = receipt
        self._local_ram_receipts[self._local_ram_seq] = dict(receipt)
        self._native_ram_contact_proofs.pop(event_seq, None)
        sys.stdout.write(
            '[Offline LAN 0.9.22] RAM native contact accepted '
            'bot_id=%d player_armor=%.3f bot_armor=%.3f\n' % (
                bot_id, player_armor, bot_armor))
        return True

    def _retry_native_ram_contact_proofs(self):
        for event_seq in tuple(self._native_ram_contact_proofs):
            self._retry_native_ram_contact_proof(event_seq)

    @staticmethod
    def _ram_obb_vertices(body):
        shape = body['shape']
        yaw = float(body['yaw'])
        sine = math.sin(yaw)
        cosine = math.cos(yaw)
        right_x, right_z = cosine, -sine
        forward_x, forward_z = sine, cosine
        center_x, center_z = float(body['x']), float(body['z'])
        half_width, half_length = float(shape[0]), float(shape[1])
        return [
            (center_x + sx * half_width * right_x +
             sz * half_length * forward_x,
             center_z + sx * half_width * right_z +
             sz * half_length * forward_z)
            for sx, sz in ((-1.0, -1.0), (1.0, -1.0),
                           (1.0, 1.0), (-1.0, 1.0))]

    @classmethod
    def _ram_obb_overlap_point(cls, body_a, body_b):
        """Return a point inside the exact convex overlap of two OBBs."""
        polygon = cls._ram_obb_vertices(body_a)
        clip = cls._ram_obb_vertices(body_b)
        for index in range(4):
            start = clip[index]
            end = clip[(index + 1) % 4]
            edge_x, edge_z = end[0] - start[0], end[1] - start[1]

            def inside(point):
                return (edge_x * (point[1] - start[1]) -
                        edge_z * (point[0] - start[0])) >= -1.0e-7

            def intersection(first, second):
                segment_x = second[0] - first[0]
                segment_z = second[1] - first[1]
                denominator = segment_x * edge_z - segment_z * edge_x
                if abs(denominator) <= 1.0e-12:
                    return second
                ratio = ((start[0] - first[0]) * edge_z -
                         (start[1] - first[1]) * edge_x) / denominator
                return (first[0] + ratio * segment_x,
                        first[1] + ratio * segment_z)

            output = []
            if not polygon:
                return None
            previous = polygon[-1]
            previous_inside = inside(previous)
            for current in polygon:
                current_inside = inside(current)
                if current_inside != previous_inside:
                    output.append(intersection(previous, current))
                if current_inside:
                    output.append(current)
                previous = current
                previous_inside = current_inside
            polygon = output
        if not polygon:
            return None
        count = float(len(polygon))
        return (sum(point[0] for point in polygon) / count,
                sum(point[1] for point in polygon) / count)

    def _poll_local_ram_contact_episodes(self, entity, own, others):
        """Turn exact OBB compression episodes into immutable RAM proofs.

        Synthetic remote Vehicles do not participate in BigWorld's native
        vehicle-collision callback. The copied collision solver is therefore
        the authoritative contact fact for player-bot physics. It supplies
        only geometry: plate thickness still comes from each live descriptor
        hit tester in ``_retry_native_ram_contact_proof``.
        """
        previous = set(self._local_ram_episode_contacts)
        overlapping = set()
        closing_gaps = set()
        newly_armed = set()
        for other in others:
            if (not other.get('alive', True) or
                    other.get('kind') != 'bot'):
                continue
            own_team = int(_number(own.get('team')))
            other_team = int(_number(other.get('team')))
            if own_team in (1, 2) and own_team == other_team:
                continue
            bot_id = int(other['network_id'])
            own_center_y = float(own['y']) + (
                float(own['shape'][2]) +
                float(own['shape'][3])) * 0.5
            other_center_y = float(other['y']) + (
                float(other['shape'][2]) +
                float(other['shape'][3])) * 0.5
            center_delta = (
                float(own['x']) - float(other['x']),
                own_center_y - other_center_y,
                float(own['z']) - float(other['z']))
            center_distance_squared = sum(
                value * value for value in center_delta)
            if center_distance_squared <= 0.0:
                # A coincident synthetic spawn has no geometrically provable
                # approach side. Keep an existing episode armed and do not
                # invent another HP event from the ambiguous pose.
                if bot_id in previous:
                    closing_gaps.add(bot_id)
                continue
            relative_velocity = (
                own['vx'] - other['vx'],
                own.get('vy', 0.0) - other.get('vy', 0.0),
                own['vz'] - other['vz'])
            # Use the real approach direction between the two hull centres.
            # The SAT minimum-translation axis is for separation only: after
            # a delayed snapshot leaves a deep front-to-rear overlap, that
            # axis can rotate sideways (or vertical) even though the tanks
            # are still closing along their travel direction.
            relative_normal = sum(
                relative_velocity[index] * center_delta[index]
                for index in range(3)) / math.sqrt(
                    center_distance_squared)
            vertical = tank_collision.vertical_overlap(
                own.get('y'), own['shape'],
                other.get('y'), other['shape'])
            contact = (tank_collision.obb_contact(
                own['x'], own['z'], own['yaw'], own['shape'],
                other['x'], other['z'], other['yaw'], other['shape'])
                       if vertical else None)
            if contact is None:
                # Interpolated remote poses can flicker just outside the OBB
                # for one frame while the same pair is still moving together.
                # That is not physical separation and must not turn sustained
                # pressure into another HP event.  A contact episode is only
                # released after the clear hulls are no longer approaching.
                if bot_id in previous and relative_normal < 0.0:
                    closing_gaps.add(bot_id)
                continue
            overlapping.add(bot_id)
            impact_contact = tank_collision.obb_impact_contact(
                own['x'], own['z'], own['yaw'], own['shape'],
                (own['vx'], own['vz']),
                other['x'], other['z'], other['yaw'], other['shape'],
                (other['vx'], other['vz']))
            if impact_contact is None:
                continue
            overlap_point = self._ram_obb_overlap_point(own, other)
            if overlap_point is None:
                continue
            low = max(
                float(own['y']) + float(own['shape'][2]),
                float(other['y']) + float(other['shape'][2]))
            high = min(
                float(own['y']) + float(own['shape'][3]),
                float(other['y']) + float(other['shape'][3]))
            if relative_normal >= 0.0 or bot_id in previous:
                continue
            hit_point = self._vector((
                overlap_point[0],
                (low + high) * 0.5,
                overlap_point[1]))
            estimated = self._estimated_motion_time_us(self._clock())
            record = other.get('_record')
            bot_vehicle = other.get('_vehicle')
            if (estimated is None or record is None or bot_vehicle is None):
                continue
            queued = self._queue_ram_contact_proof(
                record, entity, bot_vehicle, hit_point,
                (own['vx'], own.get('vy', 0.0), own['vz']),
                (other['vx'], other.get('vy', 0.0), other['vz']),
                estimated,
                own_pose=(own['x'], own['y'], own['z'], own['yaw'],
                          self._local_pitch, self._local_roll),
                bot_pose=(other['x'], other['y'], other['z'], other['yaw'],
                          _number(other.get('pitch')),
                          _number(other.get('roll'))),
                player_ram_profile=own['ram_profile'],
                contact_normal=impact_contact[:2])
            # Queue admission, including one next-frame plate retry, owns the
            # episode. A sustained overlap must never generate another HP
            # proposal merely because rendering/polling continues.
            if queued or any(
                    proof.get('bot_id') == bot_id for proof in
                    self._native_ram_contact_proofs.values()):
                newly_armed.add(bot_id)
        self._local_ram_episode_contacts = frozenset(
            (previous & (overlapping | closing_gaps)) | newly_armed)
        return bool(newly_armed)

    def _contact_tanks(self):
        """Return current non-local chassis bodies for 0.8.2 contact physics."""
        result = []
        bot_states = getattr(self._bots, 'states', {}) if self._bots else {}
        for record in self._records.values():
            if (record.get('local') or record.get('tombstone') or
                    not record.get('ready')):
                continue
            state = record.get('state') or {}
            if record.get('kind') == 'bot':
                state = bot_states.get(record.get('network_id'), state)
                presented_pose = record.get('presented_pose')
                if isinstance(presented_pose, dict):
                    state = dict(state)
                    state.update(presented_pose)
                presentation_time_us = record.get('presentation_time_us')
                revision = self._ram_bot_revision_at(
                    record.get('network_id'), presentation_time_us)
                historical = (self._ram_bot_state_at(
                    record.get('network_id'), revision,
                    presentation_time_us) if revision is not None else None)
                if isinstance(historical, dict):
                    state = dict(state)
                    for name in ('ram_vx', 'ram_vy', 'ram_vz'):
                        if name in historical:
                            state[name] = historical[name]
            alive = bool(state.get('alive', True))
            remote = self._server_entity(record['engine_id'])
            descriptor = getattr(remote, 'typeDescriptor', None)
            yaw = _number(state.get('yaw'))
            speed = _number(state.get('speed')) if alive else 0.0
            player_effective = None
            if record.get('kind') == 'player':
                player_effective = self._player_effective_snapshot(state)
            mass = (player_effective['physics']['mass']
                    if player_effective is not None else state.get('mass'))
            if (mass is None and descriptor is not None and
                    record.get('kind') != 'player'):
                mass = vehicle_physics.derive_params(descriptor).get('mass')
            shape = state.get('collision_shape')
            if shape is None:
                shape = self._collision_shape(descriptor)
            result.append({
                'id': 1000000 + int(record.get('engine_id', 0)),
                'network_id': int(record.get('network_id', 0)),
                'engine_id': int(record.get('engine_id', 0)),
                'kind': record.get('kind'),
                '_record': record,
                '_vehicle': remote,
                'presentation_time_us': record.get(
                    'presentation_time_us'),
                'alive': alive,
                'team': int(_number(state.get('team'))),
                # Apply the local body's reciprocal e=0 response for every
                # live Bot.  The authority receipt applies the Bot's half at
                # the same presented contact and skips that pair in its
                # current-frame detector.  Leaving teammates as correction-
                # only keeps the player at full speed after a ram, so it
                # immediately catches and damages the same Bot again.
                'impulse': True,
                'x': _number(state.get('x')),
                'y': _number(state.get('y')),
                'z': _number(state.get('z')),
                'yaw': yaw,
                'mass': _number(mass, 25000.0),
                'shape': shape,
                'ram_profile': (
                    dict(player_effective['ramming'])
                    if player_effective is not None else
                    self._ram_profile(descriptor)),
                'vx': _number(
                    state.get('ram_vx'), math.sin(yaw) * speed),
                'vy': _number(state.get(
                    'ram_vy', state.get('vertical_speed'))),
                'vz': _number(
                    state.get('ram_vz'), math.cos(yaw) * speed),
                'pitch': _number(state.get('pitch')),
                'roll': _number(state.get('roll')),
            })
        return result

    def _resolve_local_tank_contacts(self, entity, position, yaw, dt):
        """Apply chassis OBB separation without pushing a tank into walls."""
        self._retry_native_ram_contact_proofs()
        others = self._contact_tanks()
        own_mass = _number(
            (self._local_physics or {}).get('mass'), 25000.0)
        own = {
            'id': -1,
            'alive': True,
            'team': int(_number(getattr(self.client, 'team', 0))),
            'x': position[0], 'y': position[1], 'z': position[2],
            'yaw': yaw, 'mass': own_mass,
            'shape': self._collision_shape(entity.typeDescriptor),
            'ram_profile': self._ram_profile(
                entity.typeDescriptor, local=True),
            'vx': math.sin(yaw) * self._local_speed + self._local_push_x,
            'vy': self._local_vertical_speed,
            'vz': math.cos(yaw) * self._local_speed + self._local_push_z,
        }
        self._poll_local_ram_contact_episodes(entity, own, others)
        now = self._clock()
        contact = tank_collision.resolve_tank(
            own, others, now=now,
            ram_cooldowns=self._local_ram_cooldowns,
            active_ram_contacts=self._local_ram_contacts)
        self._local_ram_cooldowns = contact['cooldowns']
        self._local_ram_contacts = contact['contacts']
        delta_x, delta_z = contact['delta_velocity']
        forward_impulse = (delta_x * math.sin(yaw) +
                           delta_z * math.cos(yaw))
        applied_forward = 0.0
        if forward_impulse * self._local_speed < 0.0:
            applied_forward = (
                -self._local_speed if
                abs(forward_impulse) >= abs(self._local_speed)
                else forward_impulse)
            self._local_speed += applied_forward
        push_x = (self._local_push_x + delta_x -
                  applied_forward * math.sin(yaw))
        push_z = (self._local_push_z + delta_z -
                  applied_forward * math.cos(yaw))
        correction_x, correction_z = contact['correction']
        move_x = correction_x + push_x * dt
        move_z = correction_z + push_z * dt
        distance = math.sqrt(move_x * move_x + move_z * move_z)
        if distance > 0.0001:
            contact_yaw = math.atan2(move_x, move_z)
            contact_speed = distance / max(float(dt), 1.0 / 120.0)
            candidate = (position[0] + move_x, position[1],
                         position[2] + move_z)
            arena_recovery = self._arena_pose_is_outside(
                entity, position, yaw)
            if (not self._motion_is_clear(
                    entity, position, contact_yaw, contact_speed, dt,
                    hull_yaw=yaw) or
                    (not self._baked_pose_safe(candidate) and
                     not arena_recovery)):
                push_x = 0.0
                push_z = 0.0
            else:
                position = candidate
        # Preserve the existing 0.90-per-60-Hz-tick damping in real time.
        # Applying 0.90 once per rendered frame made a lateral shove last
        # several times longer at the 20-30 FPS rates this client commonly
        # reaches, which is why a teammate could slide the player so far.
        push_decay = 0.90 ** (max(0.0, float(dt)) * 60.0)
        self._local_push_x = push_x * push_decay
        self._local_push_z = push_z * push_decay
        return position

    def local_ram_contact(self):
        """Return the latest pre-separation contact proof for server relay."""
        if not isinstance(self._local_ram_receipt, dict):
            return None
        return dict(self._local_ram_receipt)

    def local_ram_contacts(self):
        """Return every proof not yet admitted by the server."""
        return [
            dict(value) for seq, value in self._local_ram_receipts.items()
            if seq > self._local_ram_admitted_seq]

    def _ram_contacts_enqueued(self):
        """Require durable server acknowledgement for every contact proof."""
        capabilities = getattr(self.client, 'server_capabilities', ()) or ()
        if lan_protocol.RAM_CONTACT_LEDGER_CAPABILITY not in capabilities:
            raise RuntimeError('server lost the RAM contact ledger contract')
        return False

    def _ack_local_ram_contacts(self, snapshot):
        if self.client is None or not isinstance(snapshot, dict):
            return False
        local_id = int(self.client.player_id)
        admitted = None
        resolved = None
        for raw in snapshot.get('players') or ():
            if not isinstance(raw, dict):
                continue
            try:
                if int(raw.get('id')) != local_id:
                    continue
                admitted = int(raw.get('ram_contact_admitted_seq', 0))
                resolved = int(raw.get('ram_contact_resolved_seq', 0))
            except (TypeError, ValueError, OverflowError):
                admitted = None
                resolved = None
            break
        if (admitted is None or resolved is None or admitted < 0 or
                resolved < 0 or resolved > admitted):
            return False
        changed = admitted > self._local_ram_admitted_seq
        self._local_ram_admitted_seq = max(
            self._local_ram_admitted_seq, admitted)
        for seq in list(self._local_ram_receipts):
            if seq <= resolved:
                self._local_ram_receipts.pop(seq, None)
                changed = True
        self._local_ram_receipt = (
            dict(next(reversed(self._local_ram_receipts.values())))
            if self._local_ram_receipts else None)
        return changed

    @staticmethod
    def _destructible_contact_token(value):
        """Canonicalise one exact catalog identity set for the LAN ledger."""
        if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 16:
            return None
        result = set()
        for raw in value:
            if not isinstance(raw, (list, tuple)) or len(raw) != 3:
                return None
            try:
                chunk_id = int(raw[0])
                item_index = int(raw[1])
                mat_kind = None if raw[2] is None else int(raw[2])
            except (TypeError, ValueError, OverflowError):
                return None
            if chunk_id < 0 or item_index < 0:
                return None
            result.add((chunk_id, item_index, mat_kind))
        return tuple(sorted(
            result, key=lambda row: (
                row[0], row[1], -1 if row[2] is None else row[2])))

    def _queue_local_destructible_contact(
            self, detail, position, yaw, speed, dt):
        """Retain one read-only hull contact until worker resolution."""
        if (not isinstance(detail, dict) or
                not bool(detail.get('requires_commit', False))):
            return False
        token = self._destructible_contact_token(detail.get('token'))
        if token is None:
            return False
        for pending in self._local_destructible_contacts.values():
            if self._destructible_contact_token(
                    pending.get('token')) == token:
                return True
        if len(self._local_destructible_contacts) >= 16:
            return False
        try:
            contact_speed = float(speed)
            contact_dt = float(dt)
        except (TypeError, ValueError, OverflowError):
            return False
        if (math.isnan(contact_speed) or math.isinf(contact_speed) or
                math.isnan(contact_dt) or math.isinf(contact_dt) or
                contact_dt <= 0.0 or contact_dt > 0.1):
            return False
        self._local_destructible_contact_seq += 1
        seq = self._local_destructible_contact_seq
        self._local_destructible_contacts[seq] = {
            'seq': seq,
            'x': round(float(position[0]), 4),
            'y': round(float(position[1]), 4),
            'z': round(float(position[2]), 4),
            'yaw': round(float(yaw), 5),
            'speed': round(max(-200.0, min(200.0, contact_speed)), 4),
            'dt': round(contact_dt, 6),
            'token': [list(row) for row in token],
        }
        self._local_destructible_safe_poses[seq] = (
            tuple(float(position[index]) for index in range(3)),
            float(yaw))
        return True

    def local_destructible_contacts(self):
        """Return every hull-sweep proposal awaiting a worker verdict."""
        return [dict(value)
                for value in self._local_destructible_contacts.values()]

    def _destructible_contacts_enqueued(self):
        capabilities = getattr(self.client, 'server_capabilities', ()) or ()
        if lan_protocol.HUMAN_RAM_TIMELINE_CAPABILITY not in capabilities:
            raise RuntimeError(
                'server lost the player pose timeline contract')
        return False

    def _ack_local_destructible_contacts(self, snapshot):
        if self.client is None or not isinstance(snapshot, dict):
            return False
        local_id = int(self.client.player_id)
        resolved = None
        rejected = ()
        for raw in snapshot.get('players') or ():
            if not isinstance(raw, dict):
                continue
            try:
                if int(raw.get('id')) != local_id:
                    continue
                resolved = int(raw.get(
                    'destructible_contact_resolved_seq', 0))
                raw_rejected = raw.get(
                    'destructible_contact_rejected_seqs', ())
                if (not isinstance(raw_rejected, (list, tuple)) or
                        len(raw_rejected) > 16):
                    return False
                rejected = tuple(int(value) for value in raw_rejected)
            except (TypeError, ValueError, OverflowError):
                resolved = None
            break
        if resolved is None:
            return False
        changed = False
        rollback = [
            seq for seq in self._local_destructible_contacts
            if (seq <= resolved and seq in rejected and
                seq in self._local_destructible_safe_poses)]
        if rollback:
            changed = self._apply_local_destructible_rejection(
                min(rollback)) or changed
        for seq in list(self._local_destructible_contacts):
            if seq <= resolved:
                self._local_destructible_contacts.pop(seq, None)
                self._local_destructible_safe_poses.pop(seq, None)
                changed = True
        return changed

    def _apply_local_destructible_rejection(
            self, sequence, server_pose=None):
        """Retire a late worker disagreement without rewinding movement.

        The visible endpoint already committed an exact native crush and
        recast the same ray for a backing wall before it advanced.  The worker
        receives that proof later and may no longer find the now-broken skin at
        its replay pose.  Such a disagreement must not resurrect collision or
        pull the player's vehicle backwards.
        """
        if (sequence not in self._local_destructible_contacts or
                sequence not in self._local_destructible_safe_poses):
            return False
        for seq, pending in self._local_destructible_contacts.items():
            if seq >= sequence:
                self._clear_local_destructible_prediction(
                    self._destructible_contact_token(pending.get('token')))
        self._report_destructible_verdict(
            'visible_kept', sequence, False,
            self._destructible_contact_token(
                self._local_destructible_contacts[sequence].get('token')))
        for seq in list(self._local_destructible_safe_poses):
            if seq >= sequence:
                self._local_destructible_safe_poses.pop(seq, None)
        return True

    def _clear_local_destructible_prediction(self, token):
        """Release a visible-only collision bypass after a terminal verdict."""
        clearer = getattr(
            self._destructibles, 'clear_local_prediction', None)
        if callable(clearer):
            return bool(clearer(token))
        return False

    def _terrain_support(self, position, yaw, descriptor=None,
                         maximum_y=None):
        """Copy 0.8.2 layered front/centre/back support probes.

        ``maximum_y`` asks the vertical ray to look below an upper hit which
        is too tall to be support.  This is essential around trenches, wagon
        decks and low ruins: horizontal hull rays still own the real wall,
        while a harmless overhead/top face must not replace the floor and
        trap the vehicle in an endless support rollback.
        """
        half_length = 2.5
        try:
            hit_tester = _field(
                _field(descriptor, 'hull', {}), 'hitTester', None)
            bbox = getattr(hit_tester, 'bbox', None)
            half_length = max(1.5, abs(float(bbox[1][2])))
        except (TypeError, ValueError, IndexError, AttributeError):
            pass
        sine, cosine = math.sin(yaw), math.cos(yaw)
        highest = None
        centre = None
        for distance in (half_length, 0.0, -half_length):
            x = position[0] + sine * distance
            z = position[2] + cosine * distance
            ray_end = self._vector((x, -1000.0, z))
            ray_start = self._vector((x, position[1] + 2.0, z))
            ground_filter = self._ground_filter(x, z)
            value = None
            for unused_layer in range(4):
                try:
                    hit = self._collide_down(
                        ray_start, ray_end, ground_filter)
                except Exception:
                    hit = None
                if hit is None:
                    break
                candidate = float(hit[0].y)
                above_limit = (maximum_y is not None and
                               candidate > float(maximum_y))
                ground_facing = True
                try:
                    ground_facing = float(hit[1].y) > 0.5
                except (AttributeError, IndexError, TypeError, ValueError):
                    # Engine-free compatibility probes historically supplied
                    # only the hit point. Production #1513 always supplies the
                    # normal, so this fallback cannot turn a live wall into
                    # support.
                    ground_facing = maximum_y is None
                if not above_limit and ground_facing:
                    value = candidate
                    break
                next_y = candidate - 0.05
                if next_y <= float(ray_end.y) + 0.01:
                    break
                ray_start = self._vector((x, next_y, z))
            if value is None:
                continue
            if highest is None or value > highest:
                highest = value
            if distance == 0.0:
                centre = value
        return highest, centre

    def _apply_fall_damage(self, entity, impact_speed):
        """Queue a physical impact observation without mutating canonical HP."""
        maximum = max(1, int(getattr(
            entity.typeDescriptor, 'maxHealth', getattr(entity, 'health', 1))))
        damage = vehicle_physics.fall_damage(maximum, impact_speed)
        if damage <= 0:
            return 0
        impact_speed = min(
            lan_protocol.PLAYER_LANDING_MAX_IMPACT_SPEED,
            abs(float(impact_speed)))
        if len(self._pending_landing_impacts) >= \
                MAX_PENDING_LANDING_IMPACTS:
            raise RuntimeError('landing observation queue exceeded limit')
        self._pending_landing_impacts.append(impact_speed)
        return damage

    def _flush_landing_observation(self):
        """Bind a queued landing to the next admitted local pose sample."""
        if not self._pending_landing_impacts or self._sender is None:
            return False
        impact_speed = self._pending_landing_impacts[0]
        sender = getattr(self._sender, 'send_current', None)
        publish = getattr(self.client, 'send_landing_observation', None)
        if (not callable(sender) or not callable(publish) or
                not sender() or not publish(impact_speed)):
            return False
        del self._pending_landing_impacts[0]
        return True

    def _apply_landing_impact(self, entity, vertical_speed):
        """Copy combined vertical/lateral impact and retained landing skid."""
        lateral_x, lateral_z = self._local_air_lateral
        lateral_speed = math.sqrt(
            lateral_x * lateral_x + lateral_z * lateral_z)
        if lateral_speed > 0.01:
            self._local_slide_speed = max(
                self._local_slide_speed, lateral_speed)
        self._local_air_lateral = (0.0, 0.0)
        impact_speed = math.sqrt(
            vertical_speed * vertical_speed +
            lateral_speed * lateral_speed)
        return self._apply_fall_damage(entity, impact_speed)

    def _update_vertical_motion(self, entity, position, yaw, dt):
        """Copy vertical motion while rejecting false raised support."""
        self._local_support_rise_blocked = False
        highest, centre = self._terrain_support(
            position, yaw, entity.typeDescriptor)
        # Front/rear hits keep a hull supported across a narrow ditch, but the
        # real distance to that support decides whether it can still be
        # followed.
        ground = centre if centre is not None else highest
        if ground is not None:
            snap_gap = vehicle_physics.ground_follow_gap(
                self._local_speed, self._local_last_pitch, dt)
            max_climb = max(0.6, abs(self._local_speed) * dt * 2.5)
            com_gap = position[1] - ground
            land_y = ground if centre is None else centre
            if not self._local_fall_armed:
                position = (position[0], land_y, position[2])
                self._local_vertical_speed = 0.0
                self._local_airborne = False
                self._local_fall_armed = True
            else:
                if tank_collision.support_rise_is_obstacle(
                        position[1], centre, max_climb):
                    # The first ray may have met the top edge of a trench,
                    # wagon deck or low ruin. Re-probe below the exact per-tick
                    # climb limit, as the mature 0.8.2 path did. Static
                    # horizontal hull collision remains authoritative for an
                    # actual wall.
                    maximum_support_y = (
                        float(position[1]) +
                        min(max(0.0, float(max_climb)), 0.85) + 0.02)
                    lower_highest, lower_centre = self._terrain_support(
                        position, yaw, entity.typeDescriptor,
                        maximum_y=maximum_support_y)
                    lower_ground = (
                        lower_centre if lower_centre is not None
                        else lower_highest)
                    if (lower_ground is None or
                            float(lower_ground) > maximum_support_y):
                        tick_pose = getattr(
                            self, '_local_support_tick_pose', None)
                        if tick_pose is not None:
                            position = tuple(tick_pose)
                        self._local_vertical_speed = 0.0
                        self._local_airborne = False
                        self._local_support_rise_blocked = True
                        return position
                    highest, centre = lower_highest, lower_centre
                    ground = lower_ground
                    com_gap = position[1] - ground
                    land_y = ground if centre is None else centre
                if (position[1] <= ground or
                        (com_gap <= snap_gap and
                         not self._local_airborne)):
                    if (self._local_airborne and
                            self._local_vertical_speed < 0.0):
                        self._apply_landing_impact(
                            entity, abs(self._local_vertical_speed))
                    if position[1] < ground:
                        rise = ground - position[1]
                        next_y = position[1] + min(rise, max_climb)
                    else:
                        next_y = position[1] + (
                            ground - position[1]) * min(1.0, dt * 15.0)
                        next_y = min(next_y, ground + 0.12)
                    position = (position[0], next_y, position[2])
                    self._local_vertical_speed = 0.0
                    self._local_airborne = False
                    self._local_fall_armed = True
                else:
                    if not self._local_airborne:
                        self._local_vertical_speed = (
                            vehicle_physics.launch_vertical_speed(
                                self._local_speed,
                                self._local_last_pitch))
                    self._local_airborne = True
                    substeps = min(8, max(
                        1, int(abs(self._local_vertical_speed * dt) /
                               0.5) + 1))
                    sub_dt = dt / float(substeps)
                    next_y = position[1]
                    for unused_step in range(substeps):
                        self._local_vertical_speed -= (
                            vehicle_physics.GRAVITY * sub_dt)
                        next_y += self._local_vertical_speed * sub_dt
                        if next_y <= land_y:
                            next_y = land_y
                            self._apply_landing_impact(
                                entity, abs(self._local_vertical_speed))
                            self._local_vertical_speed = 0.0
                            self._local_airborne = False
                            self._local_fall_armed = True
                            break
                    position = (position[0], next_y, position[2])
        elif self._local_fall_armed:
            if not self._local_airborne:
                self._local_vertical_speed = (
                    vehicle_physics.launch_vertical_speed(
                        self._local_speed, self._local_last_pitch))
            self._local_airborne = True
            self._local_vertical_speed -= vehicle_physics.GRAVITY * dt
            position = (position[0],
                        position[1] + self._local_vertical_speed * dt,
                        position[2])
        else:
            # The first streamed terrain hit owns spawn placement.  Never turn
            # the temporary y=100 fallback into a damaging free fall.
            self._local_vertical_speed = 0.0
            self._local_airborne = False
        return position

    def _apply_slope_slide(self, position, yaw, dt, entity=None):
        """Copy 0.8.2 cross-heading slope slip and airborne carry."""
        if self._local_airborne:
            self._local_slide_speed = 0.0
            lateral_x, lateral_z = self._local_air_lateral
            if abs(lateral_x) > 0.0001 or abs(lateral_z) > 0.0001:
                next_position = (
                    position[0] + lateral_x * dt, position[1],
                    position[2] + lateral_z * dt)
                lateral_speed = math.sqrt(
                    lateral_x * lateral_x + lateral_z * lateral_z)
                lateral_yaw = math.atan2(lateral_x, lateral_z)
                if (entity is None or self._motion_is_clear(
                        entity, position, lateral_yaw, lateral_speed, dt,
                        hull_yaw=yaw)):
                    position = next_position
                self._local_air_lateral = (
                    lateral_x * 0.995, lateral_z * 0.995)
            return position
        self._local_slide_speed = vehicle_physics.slope_slide_speed(
            self._local_slide_speed, self._local_slope_tangent, dt)
        cross_x, cross_z = math.cos(yaw), -math.sin(yaw)
        slide_dot = (self._local_downhill[0] * cross_x +
                     self._local_downhill[2] * cross_z)
        slide_x, slide_z = cross_x * slide_dot, cross_z * slide_dot
        self._local_air_lateral = (
            slide_x * self._local_slide_speed,
            slide_z * self._local_slide_speed)
        if (self._local_slide_speed <= 0.01 or
                (abs(slide_x) <= 0.0001 and abs(slide_z) <= 0.0001)):
            return position
        next_x = position[0] + slide_x * self._local_slide_speed * dt
        next_z = position[2] + slide_z * self._local_slide_speed * dt
        lateral_speed = abs(slide_dot) * self._local_slide_speed
        lateral_yaw = math.atan2(slide_x, slide_z)
        if (entity is not None and not self._motion_is_clear(
                entity, position, lateral_yaw, lateral_speed, dt,
                hull_yaw=yaw)):
            if not self._local_motion_soft_block:
                self._local_slide_speed = 0.0
                self._local_air_lateral = (0.0, 0.0)
            return position
        old_plane = self._local_ground_plane
        if old_plane is None:
            return position
        descriptor = getattr(entity, 'typeDescriptor', None)
        dx = next_x - position[0]
        dz = next_z - position[2]
        expected_y = (float(old_plane['center_y']) +
                      float(old_plane['gradient_x']) * dx +
                      float(old_plane['gradient_z']) * dz)
        slide_pitch = math.atan(
            self._local_slope_tangent * abs(slide_dot))
        follow_gap = vehicle_physics.ground_follow_gap(
            lateral_speed, slide_pitch, dt)
        candidate = self._sample_ground_plane(
            (next_x, position[1], next_z), yaw, descriptor)
        if candidate is None:
            # A missing centre plane is ambiguous: a narrow ditch can still
            # have front/rear track support. Reuse the established multi-probe
            # support seam instead of turning one missing point into free fall.
            highest, centre = self._terrain_support(
                (next_x, position[1], next_z), yaw, descriptor)
            support = centre if centre is not None else highest
            if (support is not None and
                    support > expected_y + GROUND_PLANE_EPSILON):
                return position
            if (support is not None and
                    expected_y - support <= follow_gap):
                bridged = dict(old_plane)
                bridged['center_y'] = expected_y
                self._commit_ground_plane(bridged, force_raw=True)
                self._local_vertical_speed = 0.0
                self._local_airborne = False
                return (next_x, expected_y, next_z)
        else:
            candidate_y = float(candidate['center_y'])
            disagreement = candidate_y - expected_y
            if abs(disagreement) <= GROUND_PLANE_EPSILON:
                self._commit_ground_plane(candidate, force_raw=True)
                self._local_vertical_speed = 0.0
                self._local_airborne = False
                return (next_x, candidate_y, next_z)
            if disagreement >= -follow_gap:
                # Preserve the current fail-closed response to an upper layer or
                # a small discontinuity that is not one drivable ground plane.
                return position
        # No nearby multi-point support remains. Commit the clear lateral motion
        # and start the same semi-implicit ballistic phase as forward travel.
        self._local_ground_plane = None
        self._local_surface_up_cosine = None
        self._local_vertical_speed = -vehicle_physics.GRAVITY * dt
        self._local_airborne = True
        return (next_x,
                position[1] + self._local_vertical_speed * dt,
                next_z)

    def _local_autorotation_turn(self, entity, turn, drive_intent=0.0,
                                 tracks_blocked=False):
        """Apply #1513's limited-traverse autorotation to copied physics.

        The stock input handler owns whether autorotation is enabled in the
        current control mode.  VehicleGunRotator keeps sending the unclamped
        mouse target to the cell while it clamps the rendered gun to the
        installed ``gun.turretYawLimits``.  A retail cell turns the hull; our
        local cell must feed that same binary direction into its sole pose
        integrator.  The descriptor, native gun rotator and copied traverse
        physics continue to own the arc, gun speed and resulting dispersion.
        """
        turn = float(turn)
        if turn != 0.0:
            return turn
        # Retail autorotation is an idle arcade-mode convenience.  Any live
        # drive command owns the hull even when the vehicle is physically
        # blocked and its measured speed is zero.  ``forward`` also carries
        # the native R/F cruise presets, so this covers both keyboard drive
        # and cruise without inferring motion from speed.
        if float(drive_intent) != 0.0:
            return turn
        # CMD_BLOCK_TRACKS is independent from the persistent autorotation
        # setting.  Holding Space does not clear that setting, but the retail
        # cell must not turn the locked tracks on its behalf.
        if bool(tracks_blocked):
            return turn
        handler = getattr(self._avatar, 'inputHandler', None)
        get_autorotation = getattr(handler, 'getAutorotation', None)
        if not callable(get_autorotation) or not get_autorotation():
            return turn
        descriptor = getattr(entity, 'typeDescriptor', None)
        gun = _field(descriptor, 'gun')
        limits = _field(gun, 'turretYawLimits')
        # Exact #1513 uses None for a fully rotating turret.  Do not infer a
        # traverse arc from vehicle tags or the separate turret descriptor.
        if limits is None:
            return turn
        try:
            minimum = float(limits[0])
            maximum = float(limits[1])
        except (AttributeError, IndexError, TypeError, ValueError):
            raise RuntimeError(
                '#1513 installed gun traverse limits are invalid')
        if (math.isnan(minimum) or math.isinf(minimum) or
                math.isnan(maximum) or math.isinf(maximum) or
                minimum > maximum):
            raise RuntimeError(
                '#1513 installed gun traverse limits are invalid')
        aim_yaw = float(getattr(self._sender, 'aim_yaw', self._local_yaw))
        relative_yaw = ((aim_yaw - float(self._local_yaw) + math.pi) %
                        (2.0 * math.pi) - math.pi)
        autorotation_turn = 0.0
        if relative_yaw < minimum - GUN_TRAVERSE_LIMIT_EPSILON:
            autorotation_turn = -1.0
        elif relative_yaw > maximum + GUN_TRAVERSE_LIMIT_EPSILON:
            autorotation_turn = 1.0
        if autorotation_turn:
            return autorotation_turn
        return turn

    def _local_siege_drive_locked(self, entity):
        """Return whether Siege transition owns the local drivetrain."""
        descriptor = getattr(entity, 'typeDescriptor', None)
        if not bool(getattr(descriptor, 'hasSiegeMode', False)):
            return False
        if self._local_siege_pending is not None:
            return True
        states = self._runtime.constants.VEHICLE_SIEGE_STATE
        return getattr(entity, 'siegeState', states.DISABLED) in (
            states.SWITCHING_ON, states.SWITCHING_OFF)

    def _drive_local(self, elapsed):
        """Advance local copied physics through all elapsed battle time."""
        if self._sender is None or self._server is None:
            return
        elapsed = max(0.0, float(elapsed))
        self._local_input_sent_during_drive = False
        self._local_destructible_send_failed = False
        remaining = elapsed
        stopped = False
        if remaining <= 0.0:
            stopped = bool(self._drive_local_step(0.0))
        while remaining > 0.0000001 and not stopped:
            step = min(0.1, remaining)
            stopped = bool(self._drive_local_step(step))
            remaining = max(0.0, remaining - step)
        if stopped:
            if self._pending_landing_impacts:
                self._flush_landing_observation()
            elif (self._local_damage_report is not None or
                    self._drown_level == 2 or self._overturn_level == 2):
                self._sender.send_current()
            return
        if self._local_input_sent_during_drive:
            self._input_accumulator = 0.0
        else:
            self._input_accumulator += elapsed
        if self._pending_landing_impacts:
            if self._flush_landing_observation():
                self._input_accumulator %= NETWORK_INPUT_SECONDS
        elif (not self._local_input_sent_during_drive and
                self._input_accumulator >= NETWORK_INPUT_SECONDS):
            # Publish only the final integrated pose. Physics still consumes
            # every substep, but a slow render callback must not burst stale
            # intermediate network samples.
            self._input_accumulator %= NETWORK_INPUT_SECONDS
            self._sender.send_current()

    def _drive_local_step(self, dt):
        if self._sender is None or self._server is None:
            return
        entity = self._server_entity(self._server.vehicle_id)
        if entity is None or entity.typeDescriptor is None:
            return
        is_alive = getattr(entity, 'isAlive', None)
        stopped = (self._battle_result is not None or
                   self._overturn_level == 2 or
                   (callable(is_alive) and not is_alive()) or
                   (not callable(is_alive) and
                    (_number(getattr(entity, 'health', 0.0)) <= 0.0 or
                     not bool(getattr(entity, 'isCrewActive', True)))))
        if stopped:
            dt = max(0.0, min(float(dt), 0.1))
            self._local_speed = 0.0
            self._local_turn_speed = 0.0
            self._local_drive_turn = 0.0
            self._sender.forward = 0.0
            self._sender.turn = 0.0
            vehicle_filter = getattr(entity, 'filter', None)
            stop_input = getattr(vehicle_filter, 'notifyInputKeysDown', None)
            if callable(stop_input):
                stop_input(0, 0)
            if (self._local_matrix is not None and
                    self._local_model is not None):
                self._update_local_presentation(entity, dt)
            return True

        if self._local_physics is None:
            raise RuntimeError('player physics was not initialized')
        dt = max(0.0, min(float(dt), 0.1))
        position = self._local_position
        tick_pose = position
        yaw = self._local_yaw
        contact_path = None
        reader = getattr(self._destructibles, 'take_ground_skip_count', None)
        if callable(reader):
            reader()
        slope_pitch = (0.0 if self._local_airborne else
                       self._smoothed_drive_pitch(position, yaw))
        siege_drive_locked = self._local_siege_drive_locked(entity)
        throttle = 0.0 if siege_drive_locked else self._sender.forward
        turn = (0.0 if siege_drive_locked else
                self._local_autorotation_turn(
                    entity, self._sender.turn, throttle,
                    tracks_blocked=self._sender.handbrake))
        is_tracked = bool(getattr(entity, 'is_tracked', False))
        is_engine_dead = bool(getattr(entity, 'is_engine_dead', False))
        if is_tracked or is_engine_dead:
            throttle = 0.0
        elif throttle != 0.0:
            throttle *= critical_damage.stat_factor(entity, 'mobility')
        # A thrown track is physically locked and must brake through the same
        # grip-limited path as the handbrake.  A dead engine only removes drive
        # torque, so existing momentum continues to coast.
        handbrake = (bool(self._sender.handbrake) or is_tracked or
                     siege_drive_locked)
        previous_speed = self._local_speed
        if siege_drive_locked:
            # Freeze only powered longitudinal/traverse motion. Gravity,
            # cross-slope slip, tank separation and destructible contact keep
            # running through the remainder of this physics frame.
            self._local_speed = 0.0
            self._local_turn_speed = 0.0
            stop_input = getattr(
                getattr(entity, 'filter', None),
                'notifyInputKeysDown', None)
            if callable(stop_input):
                stop_input(0, 0)
        else:
            drive_physics = self._local_physics
            power_factor = self._active_engine_power_factor()
            if power_factor != 1.0:
                drive_physics = dict(drive_physics)
                drive_physics['powerW'] *= power_factor
            self._local_speed = vehicle_physics.longitudinal_step(
                drive_physics, self._local_speed,
                throttle, turn != 0.0,
                slope_pitch, dt, self._local_airborne, 0,
                handbrake)

        if abs(self._local_speed) > 0.0001 and dt > 0.0:
            if self._motion_is_clear(
                    entity, position, yaw, self._local_speed, dt,
                    allow_crush_drive=(throttle * self._local_speed > 0.0 and
                                       not handbrake)):
                position = (
                    position[0] + math.sin(yaw) * self._local_speed * dt,
                    position[1],
                    position[2] + math.cos(yaw) * self._local_speed * dt)
                self._local_grind = max(0, self._local_grind - 1)
                contact_path = 'advance'
            elif not self._local_airborne:
                if self._local_motion_cap_crushed:
                    # The speed cap only proves that this vehicle may crush the
                    # exact item.  It is never copied vehicle momentum.  Keep
                    # this tick outside the accepted native skin and restore
                    # the real speed from before the longitudinal integration.
                    self._local_speed = previous_speed
                    self._local_grind = 1
                    contact_path = 'cap_hold'
                elif self._local_motion_soft_block:
                    # #1513 hides fragile/module geometry asynchronously.  Keep
                    # the pose outside its still-native skin, but retain impact
                    # momentum; the next clear tick advances normally and a
                    # newly exposed backing wall still uses the hard response.
                    self._local_grind = 1
                    contact_path = 'soft_hold'
                    self._report_destructible_verdict(
                        'visible_soft_hold', 0, False,
                        world_status=self._local_motion_status)
                else:
                    deflected = False
                    for slide_yaw in \
                            vehicle_physics.hard_contact_candidate_yaws(yaw):
                        if self._motion_is_clear(
                                entity, position, slide_yaw,
                                self._local_speed, dt, hull_yaw=yaw):
                            slide_speed, slide_x, slide_z = \
                                vehicle_physics.hard_contact_step(
                                    self._local_speed, dt,
                                    grinding=self._local_grind > 0,
                                    slide_yaw=slide_yaw)
                            position = (
                                position[0] + slide_x,
                                position[1],
                                position[2] + slide_z)
                            self._local_speed = slide_speed
                            self._local_grind = (
                                vehicle_physics.HARD_CONTACT_GRIND_TICKS)
                            deflected = True
                            contact_path = 'deflect'
                            break
                    if not deflected:
                        self._local_speed = \
                            vehicle_physics.hard_contact_step(
                                self._local_speed, dt)[0]
                        self._local_grind = (
                            vehicle_physics.HARD_CONTACT_GRIND_TICKS)
                        contact_path = 'brake'

        if siege_drive_locked or is_tracked or is_engine_dead:
            turn = 0.0
            self._local_turn_speed = 0.0
        self._local_drive_turn = turn
        if not siege_drive_locked:
            self._local_turn_speed = vehicle_physics.traverse_step(
                self._local_physics, self._local_turn_speed,
                turn, self._local_speed, dt,
                drive_intent=throttle)
            self._local_turn_speed *= critical_damage.stat_factor(
                entity, 'traverse')
        candidate_yaw = yaw + self._local_turn_speed * dt
        while candidate_yaw > math.pi:
            candidate_yaw -= 2.0 * math.pi
        while candidate_yaw < -math.pi:
            candidate_yaw += 2.0 * math.pi
        if self._arena_rotation_is_clear(
                entity, position, yaw, candidate_yaw):
            yaw = candidate_yaw
        else:
            # The rectangular red border behaves as a hard chassis contact:
            # keep this tick's last legal pose and remove angular momentum.
            self._local_turn_speed = 0.0
            self._local_motion_kinds = 'arena'
            self._local_motion_status = 'hard'
            contact_path = contact_path or 'arena_turn'
        self._local_support_rise_blocked = False
        self._local_support_tick_pose = tick_pose
        try:
            position = self._update_vertical_motion(
                entity, position, yaw, dt)
        finally:
            self._local_support_tick_pose = None
        support_blocked = self._local_support_rise_blocked
        if support_blocked:
            self._local_speed *= 0.35 ** (dt * 60.0)
            if abs(self._local_speed) < 0.05:
                self._local_speed = 0.0
            self._local_grind = 4
            contact_path = 'support'
        self._ground_pitch(position, yaw, entity.typeDescriptor)
        position = self._apply_slope_slide(position, yaw, dt, entity)
        position = self._resolve_local_tank_contacts(
            entity, position, yaw, dt)
        self._report_local_contact_tick(
            contact_path, previous_speed, slope_pitch,
            position[1] - tick_pose[1])
        self._local_position, self._local_yaw = position, yaw
        presentation_position = self._update_local_presentation(entity, dt)
        self._avatar.updateOwnVehiclePosition(
            presentation_position,
            self._vector(_engine_rotation(yaw)),
            self._local_speed, self._local_turn_speed)
        # Engine-free movement harnesses have no #1513 Entity binding.  A live
        # battle installs it before the first copied-physics frame.
        if self._binding is not None:
            self._run_optional_feature(
                'engine RPM presentation', self._publish_rpm,
                (self._clock(),))
        return False

    def _bot_destructible_scan_due(self, state, now):
        """Rate-limit proximity enumeration without skipping hull travel."""
        bot_id = int(state['id'])
        position = (_number(state.get('x')), _number(state.get('y')),
                    _number(state.get('z')))
        previous = self._bot_destructible_samples.get(bot_id)
        if previous is not None:
            deadline, sampled_position = previous
            # A stopped Bot may be waiting for a blank native slot/catalog OBB
            # to stream.  Keep a low-frequency phase even without hull travel;
            # registration is read-only for type1/type2 catalog objects.
            if (float(now) < float(deadline) and
                    _distance_2d(position, sampled_position) <
                    BOT_DESTRUCTIBLE_TRAVEL_METRES):
                return False
            interval = (0.50 if abs(_number(state.get('speed'))) < 1.0
                        else BOT_DESTRUCTIBLE_SECONDS)
            deadline = float(now) + interval
        else:
            # Schedule the first enumeration instead of making all 29 bots
            # scan on the materialisation frame.  The 6 m forward sensor and
            # 3 m travel trigger keep the hull inside its contact volume while
            # this phase elapses.  The +1 caps the largest phase at 0.10 s.
            phase = (((abs(bot_id) * 17 + 5 * 11) % 29) + 1) / 29.0
            deadline = float(now) + BOT_DESTRUCTIBLE_SECONDS * phase
            self._bot_destructible_samples[bot_id] = (
                deadline, position)
            return False
        self._bot_destructible_samples[bot_id] = (deadline, position)
        return True

    def _player_tree_destructible_scan_due(self, state, now):
        """Rate-limit hidden-worker tree scans for one human vehicle."""
        player_id = int(state['id'])
        position = (_number(state.get('x')), _number(state.get('y')),
                    _number(state.get('z')))
        previous = self._player_tree_destructible_samples.get(player_id)
        if previous is not None:
            deadline, sampled_position = previous
            if (float(now) < float(deadline) and
                    _distance_2d(position, sampled_position) <
                    BOT_DESTRUCTIBLE_TRAVEL_METRES):
                return False
            interval = (0.50 if abs(_number(state.get('speed'))) < 1.0
                        else BOT_DESTRUCTIBLE_SECONDS)
            deadline = float(now) + interval
        else:
            phase = (((abs(player_id) * 19 + 7 * 11) % 29) + 1) / 29.0
            deadline = float(now) + BOT_DESTRUCTIBLE_SECONDS * phase
            self._player_tree_destructible_samples[player_id] = (
                deadline, position)
            return False
        self._player_tree_destructible_samples[player_id] = (
            deadline, position)
        return True

    def _scan_authority_player_trees(self, states, now):
        """Resolve human/tree contacts in the hidden native authority.

        The #1513 hull collision probe does not report tree materials.  Bots
        already use the native chunk enumerator below; human world poses must
        cross the same worker-owned seam so visible clients never mutate the
        shared map directly.
        """
        if (not self._worker_mode or self._destructibles is None or
                self.client is None or
                not self.client.is_bot_authority()):
            return 0
        scanned = 0
        for state in states or ():
            if (not isinstance(state, dict) or state.get('id') is None or
                    not bool(state.get('world_pose', False)) or
                    not bool(state.get('alive', True)) or
                    not self._player_tree_destructible_scan_due(state, now)):
                continue
            descriptor = self._resolve_player_descriptor(state)
            self._destructibles._fell_trees_near(
                self._avatar.spaceID,
                self._vector((_number(state.get('x')),
                              _number(state.get('y')),
                              _number(state.get('z')))),
                _number(state.get('yaw')), _number(state.get('speed')),
                descriptor)
            scanned += 1
        return scanned

    def _bot_pose_relax(self, state, pose, now):
        """Return how long the compound should take to reach this pose.

        The clock runs between two poses that actually DIFFER, not between
        frames.  A bot below the render rate republishes the same pose for
        several frames; timing those would re-key the animation to where it
        already is, hold still, and then jump when the integration finally
        steps, which is what reads as a stutter.  The animation is also given
        slightly longer than the measured gap so it is still interpolating
        when the next pose lands.
        """
        key = state.get('id')
        yaw = _number(state.get('yaw'))
        previous = self._bot_pose_times.get(key)
        if previous is not None and previous[2] == pose:
            return None
        self._bot_pose_times[key] = (now, yaw, pose)
        if previous is None:
            self._bot_yaw_rates[key] = 0.0
            return None
        elapsed = min(0.5, float(now) - float(previous[0]))
        if elapsed <= 0.0:
            self._bot_yaw_rates[key] = 0.0
            return None
        turned = (yaw - float(previous[1]) + math.pi) % (
            2.0 * math.pi) - math.pi
        self._bot_yaw_rates[key] = turned / elapsed
        return elapsed * POSE_RELAX_STRETCH

    def _authority_presentation_lifecycle(self, record, bot_id):
        """Fence unchanged-pose caches to one live actor/entity generation."""
        lifecycle = (
            int(self._generation), int(bot_id),
            int(record.get('engine_id', 0)), id(record))
        if record.get('_authority_presentation_lifecycle') != lifecycle:
            record['_authority_presentation_lifecycle'] = lifecycle
            record.pop('_authority_pose_signature', None)
            record.pop('_authority_aim_signature', None)
            self._bot_pose_times.pop(bot_id, None)
            self._bot_yaw_rates.pop(bot_id, None)
        return lifecycle

    @staticmethod
    def _clear_authority_presentation_signatures(record):
        if isinstance(record, dict):
            record.pop('_authority_presentation_lifecycle', None)
            record.pop('_authority_pose_signature', None)
            record.pop('_authority_aim_signature', None)

    def _apply_authority_bot_poses(self, states):
        """Present copied 0.8.2 bot poses through the remote filter."""
        applied = False
        now = self._clock()
        try:
            control_pose_epoch = int(getattr(
                self._bots, '_sample_time_us'))
        except (AttributeError, TypeError, ValueError, OverflowError):
            control_pose_epoch = None
        for state in states:
            if not isinstance(state, dict) or state.get('id') is None:
                continue
            record = self._records.get('bot:%s' % state['id'])
            if record is None:
                continue
            if not record.get('ready'):
                self._clear_authority_presentation_signatures(record)
                continue
            bot_id = int(state['id'])
            engine_id = int(record['engine_id'])
            lifecycle = self._authority_presentation_lifecycle(
                record, bot_id)
            x = _number(state.get('x'))
            y = _number(state.get('y'))
            z = _number(state.get('z'))
            position = self._vector((
                x, y, z))
            yaw = _number(state.get('yaw'))
            if (self._worker_mode and self._destructibles is not None and
                    self._bot_destructible_scan_due(state, now)):
                entity = self._server_entity(engine_id)
                if entity is None:
                    raise RuntimeError(
                        'authority bot presentation entity is unavailable')
                descriptor = getattr(entity, 'typeDescriptor', None)
                if descriptor is None:
                    raise RuntimeError(
                        'authority bot destructible descriptor is unavailable')
                self._destructibles._fell_trees_near(
                    self._avatar.spaceID, position, yaw,
                    _number(state.get('speed')), descriptor)
            rotation = _engine_rotation(
                yaw, _number(state.get('pitch')), _number(state.get('roll')))
            relax_time = self._bot_pose_relax(
                state, (tuple(position), rotation), now)
            speed = _number(state.get('speed'))
            motion_alive = (
                bool(state.get('alive', True)) and
                _number(state.get('health', 1), 1.0) > 0.0)
            motion_active = motion_alive and (
                speed != 0.0 or
                any(_number(state.get(name)) != 0.0 for name in (
                    'movement_dir', 'rotation_dir', 'vertical_speed',
                    'slide_speed', 'push_x', 'push_z', 'air_lateral_x',
                    'air_lateral_z')) or
                bool(state.get('airborne', False)))
            pose_signature = (
                lifecycle, x, y, z, rotation,
                # NativeRemoteVehicle derives its motion overlay from pose
                # deltas. An exact speed edge with an unchanged final pose
                # must still settle or resume that overlay once. While any
                # canonical motion remains active, replay one identical pose
                # per control epoch so a physically blocked Bot publishes
                # zero native velocity instead of retaining its prior delta.
                speed,
                control_pose_epoch if motion_active else None,
                motion_alive,
                int(state.get('siege_state', 0) or 0))
            if record.get('_authority_pose_signature') == pose_signature:
                self._authority_pose_skips += 1
            else:
                self._binding.set_vehicle_pose(
                    engine_id, position, rotation,
                    relax_time=relax_time, now=now)
                if not motion_active:
                    # Remote presentation derives velocity from successive pose
                    # writes.  A stop sample can also advance to its final pose,
                    # so clear that derived delta now without re-keying the hull
                    # animation or waiting for a duplicate render callback.
                    self._binding.settle_vehicle_motion(engine_id, now=now)
                record['_authority_pose_signature'] = pose_signature
                self._authority_pose_writes += 1
            aim_yaw = _number(state.get('aim_yaw', yaw))
            gun_pitch = _number(state.get('gun_pitch'))
            # Hydraulic body matrices are live providers. Replaying an
            # identical setHullAimingAnglesDelta input every render callback
            # does not advance them; current hull pitch and Siege state below
            # fence every input that can change that value.
            aim_signature = (
                lifecycle, yaw, aim_yaw, gun_pitch,
                rotation[1], int(state.get('siege_state', 0) or 0))
            if record.get('_authority_aim_signature') == aim_signature:
                self._authority_aim_skips += 1
            else:
                self._binding.update_vehicle_aim(
                    engine_id, yaw, aim_yaw, gun_pitch)
                record['_authority_aim_signature'] = aim_signature
                self._authority_aim_writes += 1
            record['projectile_collision_pose'] = \
                self._projectile_plain_pose((x, y, z), state)
            self._run_optional_feature(
                'bot track animation', self._update_bot_tracks,
                (record, state, now))
            applied = True
        return applied

    def _bot_track_params(self, record, entity):
        params = record.get('track_params')
        if params is None:
            params = vehicle_physics.derive_params(entity.typeDescriptor)
            record['track_params'] = params
        return params

    def _remember_remote_track_turn(self, record, yaw, now):
        """Measure guest-side belt turning from the interpolated hull pose."""
        previous = record.get('track_pose_sample')
        turn = 0.0
        if previous is not None:
            elapsed = max(0.0, float(now) - float(previous[0]))
            if elapsed > 0.0:
                turned = (float(yaw) - float(previous[1]) + math.pi) % (
                    2.0 * math.pi) - math.pi
                turn = turned / elapsed
        record['track_pose_sample'] = (float(now), float(yaw))
        return turn

    def _bot_engine_mode(self, alive, speed, turn):
        """Return the exact #1513 ``(power, movementFlags)`` for one bot.

        A bot that turns in place has no forward speed, and the native tick
        pins both belts to zero while the power is at most
        ``ENGINE_MODE_IDLE``, so the turn rate has to raise the power too.
        """
        if not alive:
            return (ENGINE_MODE_OFF, 0)
        flags = 0
        if speed > BOT_MOVING_SPEED:
            flags |= _MOVEMENT_FORWARD
        elif speed < -BOT_MOVING_SPEED:
            flags |= _MOVEMENT_BACKWARD
        if turn < -BOT_TURNING_RATE:
            flags |= _MOVEMENT_ROTATE_LEFT
        elif turn > BOT_TURNING_RATE:
            flags |= _MOVEMENT_ROTATE_RIGHT
        if flags:
            return (ENGINE_MODE_RUNNING, flags)
        return (ENGINE_MODE_IDLE, 0)

    def _update_bot_tracks(self, record, state, now, turn_override=None):
        """Drive one bot's belts from its authority speed and turn rate."""
        # The hidden authority needs native hull and gun poses for collision
        # and projectile resolution, but belt scroll and engine mode are
        # presentation/audio state owned exclusively by the visible client.
        if self._worker_mode or self._remote_factory is None:
            return False
        if turn_override is not None:
            # Guest poses arrive once per rendered frame, while the stock
            # PyTrackScroll controller itself advances at 20 Hz.  Carry the
            # deadline instead of using ``now + interval`` so 30/40/60/120 Hz
            # renderers all average the same feed cadence without touching
            # the render-rate hull interpolation below.
            now = float(now)
            next_update = record.get('_remote_track_next_update')
            if (next_update is not None and
                    now + 1.0e-9 < float(next_update)):
                return False
            if next_update is None:
                next_update = now
            elapsed = max(0.0, now - float(next_update))
            periods = max(
                1, int(elapsed / REMOTE_TRACK_PRESENTATION_SECONDS) + 1)
            record['_remote_track_next_update'] = (
                float(next_update) +
                periods * REMOTE_TRACK_PRESENTATION_SECONDS)
        vehicle = self._remote_factory.get(record['engine_id'])
        if vehicle is None:
            return False
        alive = bool(state.get('alive', True)) and int(
            state.get('health', 1) or 0) > 0
        speed = _number(state.get('speed'))
        turn = _number(
            self._bot_yaw_rates.get(state.get('id'))
            if turn_override is None else turn_override)
        mode = self._bot_engine_mode(alive, speed, turn)
        left, right = vehicle_physics.track_scroll(
            self._bot_track_params(record, vehicle), speed, turn)
        minimum, maximum = TRACK_SCROLL_LIMITS
        updated = vehicle.update_tracks(
            max(minimum, min(maximum, left)),
            max(minimum, min(maximum, right)), mode)
        self._report_bot_tracks(vehicle, left, right, mode, now)
        return bool(updated)

    def _report_bot_tracks(self, vehicle, left, right, mode, now):
        """Log what the scroll controller actually holds.

        ``leftContact``/``rightContact`` still reading the constructor's
        ``True`` and ``leftScroll``/``rightScroll`` still reading ``0.0`` mean
        the controller's 20 Hz updater never ran, which is what a filter with
        no owning entity looks like from Python.
        """
        if self._track_report_time is not None and (
                now - self._track_report_time) < TRACK_REPORT_SECONDS:
            return False
        self._track_report_time = now
        sys.stdout.write(
            '[Offline LAN 0.9.22] bot tracks id=%s mode=%r fed=(%.3f, %.3f) '
            'scroll=%r error=%r\n' % (
                vehicle.bw_entity_id, mode, left, right,
                vehicle.track_scroll_readback(),
                self._remote_factory.track_animation_error))
        return True

    def _bot_visibility(self, source, target, fired_recently=False):
        source_position = _xyz(source)
        target_position = target.get('position') or _xyz(target)
        start = self._vector((source_position[0], source_position[1] + 2.0,
                              source_position[2]))
        end = self._vector((target_position[0], target_position[1] + 1.5,
                            target_position[2]))
        hit = self._runtime.bigworld.wg_collideSegment(
            self._avatar.spaceID, start, end, 128)
        line_of_sight = bool(
            hit is None or
            (hit[0] - start).length + 1.5 >= (end - start).length)
        foliage_bonus = 0.0
        if line_of_sight and self._foliage is not None:
            foliage_bonus = self._foliage_camouflage_bonus(
                source_position, target_position, fired_recently)
        return {
            'line_of_sight': line_of_sight,
            'foliage_bonus': foliage_bonus,
        }

    def _bot_firing_lane(self, source, target):
        """Probe static space between, rather than inside, two vehicle hulls."""
        profile = source.get('profile')
        profile = profile if isinstance(profile, dict) else {}
        if str(profile.get('class_tag') or '') == 'SPG':
            if self._artillery is None or self._bots is None:
                return False
            descriptor = self._bots._descriptors.get(int(source.get('id')))
            shell_index = max(0, int(source.get('shell_index', 0) or 0))
            ready, solution = self._artillery.request(
                source, target, descriptor, shell_index, self._clock())
            return bool(ready and solution is not None)
        source_position = _xyz(source)
        target_position = target.get('position') or _xyz(target)
        dx = target_position[0] - source_position[0]
        dz = target_position[2] - source_position[2]
        distance = math.sqrt(dx * dx + dz * dz)
        # Keep a short but real world segment between close hulls. Treating the
        # absence of the default eight-metre middle section as clear let tanks
        # on opposite sides of a thin wall enter engage/hold and fire forever.
        clearance = min(4.0, max(0.0, (distance - 0.75) * 0.5))
        for target_height in (1.5, 2.2):
            segment = bot_planner.trimmed_sight_segment(
                source_position, target_position, 2.5, target_height,
                clearance, clearance)
            if segment is None:
                return False
            if not segment:
                return False
            start, end = segment
            hit = self._runtime.bigworld.wg_collideSegment(
                self._avatar.spaceID,
                self._vector(start), self._vector(end), 128)
            if hit is None:
                return True
        return False

    def _bot_friendly_path_verdict(
            self, source, path, splash_radius=0.0):
        """Test live allied hulls against one frozen physical shell path."""
        try:
            source_id = int(source.get('id'))
            source_team = int(source.get('team'))
            points = tuple(tuple(float(value) for value in point[:3])
                           for point in path)
            splash_radius = float(splash_radius)
        except (AttributeError, TypeError, ValueError, IndexError,
                OverflowError):
            return {'clear': False}
        if (len(points) < 2 or splash_radius < 0.0 or
                math.isnan(splash_radius) or math.isinf(splash_radius) or
                any(math.isnan(value) or math.isinf(value)
                    for point in points for value in point)):
            return {'clear': False}
        terminal = points[-1]
        broadphase_sq = PROJECTILE_BROADPHASE_RADIUS ** 2
        for record in self._records.values():
            if record.get('tombstone') or not record.get('ready'):
                continue
            if self._worker_mode and record.get('local'):
                continue
            if record.get('kind') == 'bot':
                try:
                    if int(record.get('network_id')) == source_id:
                        continue
                except (TypeError, ValueError):
                    continue
            state = record.get('state') or {}
            try:
                if int(state.get('team')) != source_team:
                    continue
            except (TypeError, ValueError):
                continue
            vehicle = self._server_entity(record.get('engine_id'))
            if (vehicle is None or not getattr(vehicle, 'isStarted', False) or
                    not self._record_alive(record, vehicle)):
                continue
            position = (tuple(self._local_position)
                        if record.get('local') else
                        _xyz(getattr(vehicle, 'position', state)))
            blocked = bool(
                splash_radius > 0.0 and
                sum((position[index] - terminal[index]) ** 2
                    for index in range(3)) <= splash_radius ** 2)
            if not blocked:
                for first, second in zip(points, points[1:]):
                    if (not point_in_expanded_segment_bounds(
                            position, first, second,
                            PROJECTILE_BROADPHASE_RADIUS) or
                            point_segment_distance_sq(
                                position, first, second) > broadphase_sq):
                        continue
                    start = self._vector(first)
                    end = self._vector(second)
                    try:
                        if (record.get('local') and
                                self._local_matrix is not None):
                            collisions = collide_vehicle_at_matrix(
                                vehicle, self._local_body_pose(), start, end,
                                self._runtime.math,
                                chassis_matrix=self._local_matrix)
                        elif record.get('native_remote'):
                            body_matrix, chassis_matrix = \
                                self._projectile_vehicle_matrices(
                                    record, vehicle)
                            collisions = collide_vehicle_at_matrix(
                                vehicle, body_matrix, start, end,
                                self._runtime.math,
                                chassis_matrix=chassis_matrix)
                        else:
                            collide = getattr(
                                vehicle, 'collideSegmentExt', None)
                            collisions = (collide(start, end)
                                          if callable(collide) else ())
                    except Exception:
                        return {'clear': False}
                    if collisions:
                        blocked = True
                        break
            if not blocked:
                continue
            try:
                shape = tank_collision.chassis_shape(
                    vehicle.typeDescriptor)
                blocker_radius = math.hypot(shape[0], shape[1])
            except Exception:
                fallback = tank_collision.DEFAULT_SHAPE
                blocker_radius = math.hypot(fallback[0], fallback[1])
            return {
                'clear': False,
                'blocker_kind': record.get('kind'),
                'blocker_id': record.get('network_id'),
                'blocker_team': source_team,
                'blocker_position': position,
                'blocker_radius': blocker_radius,
            }
        return {'clear': True}

    def _bot_friendly_firing_lane(
            self, source, unused_target, descriptor, shell_index, launch):
        """Reject allies on the exact frozen direct-shell parabola."""
        try:
            source_id = int(source.get('id'))
            fire_seq = int(launch.get('fire_seq'))
            launch_shell_index = int(launch.get('shell_index'))
            shot_yaw = float(launch.get('shot_yaw'))
            shot_pitch = float(launch.get('shot_pitch'))
            flight_time = float(launch.get('flight_time'))
            origin = tuple(float(launch['origin'][index])
                           for index in range(3))
        except (AttributeError, KeyError, TypeError, ValueError, IndexError,
                OverflowError):
            return {'clear': False}
        if (fire_seq != int(source.get('fire_seq', 0)) + 1 or
                launch_shell_index != int(shell_index) or
                flight_time <= 0.0 or
                flight_time > ballistics.PROJECTILE_MAX_FLIGHT_SECONDS or
                any(math.isnan(value) or math.isinf(value) for value in (
                    shot_yaw, shot_pitch, flight_time) + origin)):
            return {'clear': False}
        try:
            shot = self._descriptor_shot(descriptor, shell_index)
            speed = float(_field(shot, 'speed'))
            gravity = abs(float(_field(shot, 'gravity')))
            maximum = float(_field(shot, 'maxDistance'))
            splash_radius = float(combat_rules.he_radius(shot))
        except (AttributeError, TypeError, ValueError, IndexError,
                OverflowError):
            return {'clear': False}
        if (speed <= 0.0 or gravity <= 0.0 or maximum <= 0.0 or
                speed * flight_time > maximum + 1e-6):
            return {'clear': False}
        path = ballistics.ballistic_path(
            # Protocol shot pitch is positive-up; the pure helper follows the
            # rendered BigWorld negative-is-up convention.
            origin, shot_yaw, -shot_pitch, speed, gravity, flight_time,
            PROJECTILE_MAX_SUBSTEP_SECONDS)
        return self._bot_friendly_path_verdict(
            source, path, splash_radius)

    def _bot_direct_launch_origin(
            self, source, unused_descriptor, unused_shell_index,
            unused_fire_seq, unused_shot_yaw, unused_shot_pitch,
            unused_flight_time):
        """Freeze one direct shell at its exact logical/native muzzle pose."""
        try:
            source_id = int(source.get('id'))
        except (AttributeError, TypeError, ValueError, OverflowError):
            return None
        barrel = self._bot_barrel_point(source, unused_descriptor)
        if barrel is None or self._barrel_under_water(barrel):
            return None
        source_record = self._records.get('bot:%s' % source_id)
        if source_record is None:
            return None
        source_entity = self._server_entity(source_record.get('engine_id'))
        if (source_entity is None or
                not getattr(source_entity, 'isStarted', False)):
            return None
        try:
            gun_node = source_entity.model.node('HP_gunFire')
            native = _xyz(self._runtime.math.Matrix(gun_node).translation)
        except Exception:
            return None
        presented = source_record.get('projectile_collision_pose')
        if isinstance(presented, dict):
            logical_pose = self._projectile_plain_pose((
                _number(source.get('x')), _number(source.get('y')),
                _number(source.get('z'))), source)
            names = ('x', 'y', 'z', 'yaw', 'pitch', 'roll',
                     'turret_yaw', 'gun_pitch')
            if any(abs(_number(logical_pose.get(name)) -
                       _number(presented.get(name))) > 1.0e-7
                   for name in names):
                # A stalled callback can simulate several physical edges before
                # the hidden native compound is presented.  The pure #1513
                # barrel transform binds each edge to its own logical pose;
                # the native node remains the exact path for an aligned pose.
                return tuple(barrel)
        return native

    def _bot_artillery_planning_origin(self, source, descriptor):
        """Read the same native muzzle used by the final SPG proof."""
        return self._bot_direct_launch_origin(
            source, descriptor, 0, 0, 0.0, 0.0, 0.0)

    def _bot_ballistic_solution(self, source, target, descriptor,
                                shell_index, now):
        """Return only a completed SPG arc; pending/blocked stays unshootable."""
        if self._artillery is None or target is None:
            return None
        return self._artillery.solution(
            source, target, descriptor, shell_index, now)

    def _bot_artillery_launch(
            self, source, target, descriptor, shell_index, fire_seq,
            shot_yaw, shot_pitch, flight_time, now):
        """Prove the exact dispersed SPG path from the live muzzle node."""
        if (self._artillery is None or not isinstance(source, dict) or
                not isinstance(target, dict)):
            return None
        try:
            bot_id = int(source.get('id'))
        except (TypeError, ValueError, OverflowError):
            return None
        record = self._records.get('bot:%s' % bot_id)
        if record is None:
            return None
        entity = self._server_entity(record.get('engine_id'))
        if (entity is None or not getattr(entity, 'isStarted', False) or
                getattr(entity, 'typeDescriptor', None) is None):
            return None
        try:
            gun_node = entity.model.node('HP_gunFire')
            origin = _xyz(self._runtime.math.Matrix(gun_node).translation)
        except Exception:
            # A logical pose is not a muzzle proof.  SPGs wait until the
            # native model exposes the exact launch transform.
            return None
        barrel = self._bot_barrel_point(source, descriptor)
        if barrel is None or self._barrel_under_water(barrel):
            return None
        ready, receipt = self._artillery.request_launch(
            source, target, descriptor, int(shell_index), int(fire_seq),
            origin, float(shot_yaw), float(shot_pitch),
            float(flight_time), float(now))
        return receipt if ready and isinstance(receipt, dict) else None

    def _bot_artillery_friendly_lane(
            self, source, unused_target, descriptor, shell_index, receipt):
        """Reject allies intersecting the proved SPG path or HE terminal."""
        try:
            raw_path = receipt.get('path')
            if not isinstance(raw_path, (list, tuple)) or len(raw_path) < 2:
                return {'clear': False}
            path = []
            for raw in raw_path:
                point = tuple(float(raw[index]) for index in range(3))
                if any(math.isnan(value) or math.isinf(value)
                       for value in point):
                    return {'clear': False}
                path.append(point)
        except (AttributeError, TypeError, ValueError, IndexError,
                OverflowError):
            return {'clear': False}
        try:
            shot = self._descriptor_shot(descriptor, shell_index)
            splash_radius = float(combat_rules.he_radius(shot))
            if (math.isnan(splash_radius) or math.isinf(splash_radius) or
                    splash_radius < 0.0):
                return {'clear': False}
        except (AttributeError, TypeError, ValueError, IndexError,
                OverflowError):
            return {'clear': False}
        return self._bot_friendly_path_verdict(
            source, path, splash_radius)

    def _bot_artillery_cancel(self, source):
        """Discard bounded arc work for a cancelled frozen SPG intent."""
        if self._artillery is None or not isinstance(source, dict):
            return False
        return bool(self._artillery.cancel_launch(source))

    def _artillery_arc_probe(self, start, end):
        """Return the native world hit point, or None for one clear chord."""
        hit = self._runtime.bigworld.wg_collideSegment(
            self._avatar.spaceID, self._vector(start), self._vector(end), 128)
        if hit is None:
            return None
        try:
            return _xyz(hit[0])
        except Exception:
            return False

    def _advance_artillery_arcs(self, now):
        if self._artillery is None:
            return 0
        return self._artillery.advance(
            now, ARTILLERY_ARC_RAYS_PER_FRAME,
            self._artillery_arc_probe)

    def _send_bot_message(self, message):
        kind = message.get('type')
        if kind == 'bot_manifest':
            profiles = message.get('player_collision_profiles')
            if profiles is None:
                return self.client.send_bot_manifest(message.get('bots'))
            return self.client.send_bot_manifest(
                message.get('bots'), profiles)
        if kind == 'bot_state':
            human_ram_armors = message.get('human_ram_armors')
            if human_ram_armors is None and self._worker_mode:
                human_ram_armors = self._human_ram_armor_results()
            state_kwargs = {}
            if 'sample_time_us' in message:
                state_kwargs['sample_time_us'] = message.get('sample_time_us')
                state_kwargs['source_batch_horizon_us'] = message.get(
                    'source_batch_horizon_us')
            if human_ram_armors is not None:
                state_kwargs['human_ram_armors'] = human_ram_armors
            projected_sender = getattr(
                self.client, 'send_projected_bot_state', None)
            if callable(projected_sender):
                return projected_sender(message.get('bots'), **state_kwargs)
            return self.client.send_bot_state(
                message.get('bots'), **state_kwargs)
        if kind == 'bot_observation':
            return self.client.send_bot_observation(
                message.get('contacts'), message.get('affordances'))
        if kind == 'bot_human_hit':
            return self.client.send_bot_human_hit(
                message.get('attacker_bot'), message.get('target'),
                message.get('shot_seq'), message.get('damage'),
                message.get('shot_result'), message.get('impact_position'))
        if kind == 'bot_ram':
            return self.client.send_bot_ram(
                message.get('bot_id'), message.get('target_kind'),
                message.get('target_id'), message.get('ram_seq'),
                message.get('damage_to_bot'),
                message.get('damage_to_target'),
                message.get('ram_contact_player_id'),
                message.get('ram_contact_seq'))
        if kind == 'rules_state':
            rules = message.get('rules') or {}
            return self.client.send_rules_state(rules.get('bases'))
        if kind == 'battle_result':
            return self.client.send_battle_result(
                message.get('winner'), message.get('reason'),
                message.get('base_team'))
        return False

    def _enqueue_bot_manifest(self, message, now=None):
        """Commit one frozen manifest only after local transport enqueue."""
        if (not isinstance(message, dict) or
                message.get('type') != 'bot_manifest' or
                self._bots is None):
            return False
        provider = getattr(self._bots, 'pending_manifest', None)
        pending = provider() if callable(provider) else None
        if pending is None or pending != message:
            return False
        if now is None:
            now = self._clock()
        now = float(now)
        retry_identity = (
            self._generation,
            (self._start_message or {}).get('round_id'),
            getattr(self._bots, 'authority_id', None),
            getattr(self.client, 'authority_epoch', None))
        if (self._bot_manifest_retry_identity == retry_identity and
                self._bot_manifest_retry_deadline > 0.0 and
                now + 1.0e-9 >= self._bot_manifest_retry_deadline):
            self._discard_pending_bot_manifest()
            raise RuntimeError('worker bot manifest enqueue timed out')
        if (self._bot_manifest_retry_identity == retry_identity and
                self._next_bot_manifest_retry > 0.0 and
                now + 1.0e-9 < self._next_bot_manifest_retry):
            return False
        accepted = bool(self._send_bot_message(message))
        if not accepted:
            self._next_bot_manifest_retry = (
                now + BOT_MANIFEST_RETRY_SECONDS)
            if self._bot_manifest_retry_identity != retry_identity:
                self._bot_manifest_retry_deadline = (
                    now + float((self._config or {}).get(
                        'startupTimeoutSeconds', 30.0)))
            self._bot_manifest_retry_identity = retry_identity
            return False
        marker = getattr(self._bots, 'mark_manifest_enqueued', None)
        if not callable(marker) or not marker(message):
            raise RuntimeError(
                'enqueued bot manifest does not match the pending payload')
        self._next_bot_manifest_retry = 0.0
        self._bot_manifest_retry_deadline = 0.0
        self._bot_manifest_retry_identity = None
        return True

    def _retry_bot_manifest(self, now):
        """Retry the current authority tenure's manifest at bounded cadence."""
        if (not self._worker_mode or self.state != 'running' or
                self._bots is None):
            return False
        if (self._battle_result is not None or
                getattr(self._bots, 'finished', False)):
            self._discard_pending_bot_manifest()
            return False
        start_round = (self._start_message or {}).get('round_id')
        current_identity = (
            self._generation, start_round,
            getattr(self._bots, 'authority_id', None),
            getattr(self.client, 'authority_epoch', None))
        if (self._bot_manifest_retry_identity is not None and
                self._bot_manifest_retry_identity != current_identity):
            self._discard_pending_bot_manifest()
            return False
        if getattr(self._bots, 'round_id', None) != start_round:
            self._discard_pending_bot_manifest()
            return False
        client_round = getattr(self.client, 'round_id', start_round)
        if client_round is not None and client_round != start_round:
            self._discard_pending_bot_manifest()
            return False
        authority_check = getattr(self.client, 'is_bot_authority', None)
        if callable(authority_check) and not authority_check():
            self._discard_pending_bot_manifest()
            return False
        provider = getattr(self._bots, 'pending_manifest', None)
        pending = provider() if callable(provider) else None
        if pending is None:
            self._next_bot_manifest_retry = 0.0
            self._bot_manifest_retry_deadline = 0.0
            self._bot_manifest_retry_identity = None
            return False
        now = float(now)
        if (self._bot_manifest_retry_deadline > 0.0 and
                now + 1.0e-9 >= self._bot_manifest_retry_deadline):
            self._discard_pending_bot_manifest()
            raise RuntimeError('worker bot manifest enqueue timed out')
        if (self._next_bot_manifest_retry > 0.0 and
                now + 1.0e-9 < self._next_bot_manifest_retry):
            return False
        return self._enqueue_bot_manifest(pending, now=now)

    def _discard_pending_bot_manifest(self):
        discard = getattr(self._bots, 'discard_pending_manifest', None)
        if callable(discard):
            discard()
        self._next_bot_manifest_retry = 0.0
        self._bot_manifest_retry_deadline = 0.0
        self._bot_manifest_retry_identity = None

    def _enqueue_bot_message(self, message):
        """Join one state enqueue with ordered physical-launch progress."""
        accepted = self._send_bot_message(message)
        if accepted:
            self._resolve_bot_fire(message)
        return accepted

    def _resolve_bot_fire(self, message):
        if message.get('type') != 'bot_state':
            return False
        launches = message.get('launches') or ()
        blocked_bots = set()
        next_sequence = {}
        complete = True
        for state in launches:
            try:
                bot_id = int(state['id'])
                fire_seq = int(state['fire_seq'])
            except (AttributeError, KeyError, OverflowError,
                    TypeError, ValueError):
                raise RuntimeError(
                    'bot projectile outbox identity is invalid')
            if bot_id in blocked_bots:
                continue
            confirmed = self._bot_fire_seen.get(bot_id, 0)
            if fire_seq <= confirmed:
                continue
            expected = next_sequence.get(bot_id, confirmed + 1)
            if (fire_seq != expected or
                    not self._launch_bot_projectile(state, fire_seq)):
                # Retry the same frozen payload on the next Bot publication.
                # A later round never overtakes its unconfirmed predecessor.
                blocked_bots.add(bot_id)
                complete = False
                continue
            next_sequence[bot_id] = fire_seq + 1
        return complete

    def _confirm_bot_projectile_launch(self, normalized):
        """Ack only a server-admitted Bot launch, preserving Bot order."""
        if (not isinstance(normalized, dict) or
                normalized.get('shooter_kind') != 'bot' or
                self._bots is None or not self._bots.is_authority()):
            return False
        try:
            bot_id = int(normalized['shooter_id'])
            shot_seq = int(normalized['shot_seq'])
        except (KeyError, TypeError, ValueError, OverflowError):
            raise RuntimeError('canonical bot projectile identity is invalid')
        confirmed = self._bot_fire_seen.get(bot_id, 0)
        previous_confirmed = confirmed
        if shot_seq <= confirmed:
            return True
        pending = self._bot_fire_confirmations.setdefault(bot_id, set())
        pending.add(shot_seq)
        acknowledge = getattr(
            self._bots, 'ack_projectile_launch', None)
        if not callable(acknowledge):
            raise RuntimeError(
                'bot projectile outbox acknowledgement is unavailable')
        while confirmed + 1 in pending:
            next_seq = confirmed + 1
            if not acknowledge(bot_id, next_seq):
                raise RuntimeError(
                    'canonical bot projectile acknowledgement is out of order')
            pending.remove(next_seq)
            self._bot_launch_payloads.pop((bot_id, next_seq), None)
            confirmed = next_seq
        if confirmed > previous_confirmed:
            self._bot_fire_seen[bot_id] = confirmed
        if not pending:
            self._bot_fire_confirmations.pop(bot_id, None)
        return True

    def _remember_player_fire_intent(self, key, intent):
        frozen = dict(intent)
        previous = self._player_fire_intent_history.get(key)
        if previous is not None and previous != frozen:
            raise RuntimeError('worker fire intent history conflict')
        self._player_fire_intent_history[key] = frozen
        while len(self._player_fire_intent_history) > 64:
            self._player_fire_intent_history.popitem(last=False)

    def _reject_player_fire_intent(self, key, reason):
        intent = self._player_fire_intents.get(key)
        sender = getattr(self.client, 'send_fire_intent_result', None)
        if (intent is None or not callable(sender) or
                not sender(intent['player_id'], intent['intent_seq'], reason)):
            raise RuntimeError(
                'worker could not publish a fire-intent rejection')
        gun = self._player_authority_guns.get(int(intent['player_id']))
        remaining = (-1.0 if gun is None else float(gun.reload_time))
        sys.stdout.write(
            '[Offline LAN 0.9.22] WORKER FIRE rejected player=%d intent=%d '
            'reason=%s reload=%.3f\n' % (
                int(intent['player_id']), int(intent['intent_seq']),
                str(reason), remaining))
        self._remember_player_fire_intent(key, intent)
        self._player_fire_intents.pop(key, None)
        return True

    @staticmethod
    def _apply_player_gun_checkpoint(gun, intent):
        """Apply the exact visible gun edge without advancing a second clock."""
        checkpoint = lan_protocol._canonical_human_gun_checkpoint(
            intent.get('gun_checkpoint'))
        try:
            input_seq = int(intent['input_seq'])
            checkpoint_seq = int(intent['gun_checkpoint_seq'])
            shell_index = int(intent['shell_index'])
            next_shell_index = int(intent['next_shell_index'])
        except (KeyError, TypeError, ValueError, OverflowError):
            raise RuntimeError('worker player gun checkpoint is invalid')
        previous_seq = int(getattr(gun, '_client_checkpoint_seq', 0))
        if (checkpoint is None or checkpoint_seq != input_seq or
                input_seq <= previous_seq or
                checkpoint['clip_size'] != int(gun.clip_size) or
                not 0 <= shell_index < len(gun.shots) or
                not 0 <= next_shell_index < len(gun.shots) or
                shell_index >= len(gun.ammo) or
                checkpoint['clip'] > int(gun.ammo[shell_index]) or
                not isinstance(intent.get('shell_change_pending'), bool) or
                (not intent['shell_change_pending'] and
                 next_shell_index != shell_index) or
                (intent['shell_change_pending'] and
                 (next_shell_index == shell_index or
                  next_shell_index >= len(gun.ammo) or
                  int(gun.ammo[next_shell_index]) <= 0))):
            raise RuntimeError('worker player gun checkpoint is invalid')
        gun.shot_index = shell_index
        gun.pending_index = (
            next_shell_index if intent['shell_change_pending'] else None)
        gun.reload_time = float(checkpoint['reload_time'])
        gun.reload_duration = float(checkpoint['reload_duration'])
        gun.clip = int(checkpoint['clip'])
        gun.dispersion = float(checkpoint['dispersion'])
        gun.load_started = True
        gun._client_checkpoint_seq = input_seq
        return gun.can_fire(True)

    def _advance_player_fire_authority(self, dt, now):
        """Resolve visible triggers from their input-bound client gun edge."""
        if not self._worker_mode or not self._projectile_is_authority():
            return False
        live_players = set()
        for record in tuple(self._records.values()):
            if record.get('kind') != 'player':
                continue
            try:
                player_id = int(record.get('network_id'))
            except (TypeError, ValueError, OverflowError):
                continue
            if player_id <= 0 or record.get('tombstone'):
                continue
            entity = self._server_entity(record.get('engine_id'))
            if (entity is None or not getattr(entity, 'isStarted', False) or
                    getattr(entity, 'typeDescriptor', None) is None):
                continue
            live_players.add(player_id)
            state = record.get('state') or {}
            effective = effective_params.canonical(
                state.get('effective_params'))
            if effective is None:
                raise RuntimeError(
                    'worker player effective parameters are unavailable')
            descriptor = entity.typeDescriptor
            gun = self._player_authority_guns.get(player_id)
            if gun is None:
                ammo_layout = {}
                for row in effective['ammo']:
                    if not isinstance(row, (list, tuple)) or len(row) != 2:
                        raise RuntimeError(
                            'worker player ammunition snapshot is invalid')
                    ammo_layout[int(row[0])] = int(row[1])
                gun = gun_mechanics.GunState(
                    descriptor, effective['loadout'])
                gun.bind_client_contract(effective['gun'], ammo_layout)
                gun._effective_params = effective
                self._player_authority_guns[player_id] = gun
            else:
                if getattr(gun, '_effective_params', None) != effective:
                    raise RuntimeError(
                        'worker player effective parameters changed in battle')
        for player_id in tuple(self._player_authority_guns):
            if player_id not in live_players:
                self._player_authority_guns.pop(player_id, None)

        for key, intent in tuple(self._player_fire_intents.items()):
            player_id = int(intent['player_id'])
            if player_id in self._player_fire_launch_pending:
                continue
            record = self._records.get('player:%s' % player_id)
            if record is None or record.get('tombstone'):
                self._reject_player_fire_intent(key, 'player_unavailable')
                continue
            entity = self._server_entity(record.get('engine_id'))
            if (entity is None or not getattr(entity, 'isStarted', False) or
                    getattr(entity, 'typeDescriptor', None) is None):
                continue
            state = record.get('state') or {}
            if not bool(state.get('alive', True)):
                self._reject_player_fire_intent(key, 'player_dead')
                continue
            gun = self._player_authority_guns.get(player_id)
            if gun is None:
                continue
            effective = gun._effective_params
            shell_index = int(intent['shell_index'])
            if not self._apply_player_gun_checkpoint(gun, intent):
                self._reject_player_fire_intent(key, 'gun_not_ready')
                continue
            try:
                source_shot = effective['gun']['shots'][
                    shell_index]['source_shot']
                speed = float(source_shot['speed'])
                gravity = float(source_shot['gravity'])
                maximum = float(source_shot['maxDistance'])
            except (IndexError, KeyError, TypeError, ValueError):
                raise RuntimeError(
                    'worker player mounted shot contract is invalid')
            if speed <= 0.0 or gravity <= 0.0 or maximum <= 0.0:
                raise RuntimeError(
                    'worker player gun has invalid projectile parameters')
            try:
                origin = tuple(float(value) for value in
                               intent['shot_origin'])
                direction = self._vector(tuple(
                    float(value) for value in intent['shot_direction']))
                dispersion_angle = float(intent['dispersion_angle'])
            except (KeyError, TypeError, ValueError, OverflowError):
                raise RuntimeError(
                    'worker player trigger ray is unavailable')
            direction.normalise()
            if direction.length <= 0.0:
                raise RuntimeError('worker player muzzle direction is empty')
            gun.scatter(
                direction,
                bool(self._config and self._config.get(
                    'perfect_accuracy', False)),
                dispersion_angle=dispersion_angle)
            velocity = tuple(
                value * speed for value in _xyz(direction))
            shot_seq = int(intent['shot_seq'])
            is_he = combat_rules.is_he(source_shot)
            accepted = self.client.send_projectile_launch(
                'player', player_id, shot_seq, shell_index,
                list(origin), list(velocity), gravity, maximum,
                PROJECTILE_MAX_TIME_MS, is_he,
                combat_rules.he_radius(source_shot) if is_he else 0.0,
                authority_epoch=self.client.authority_epoch,
                penetration_factor=combat_rules.sample_penetration_factor(),
                source_shot=source_shot,
                fire_intent_seq=int(intent['intent_seq']),
                fire_input_seq=int(intent['input_seq']))
            if accepted != shot_seq:
                raise RuntimeError(
                    'worker could not publish a canonical player launch')
            self._player_fire_launch_pending[player_id] = {
                'intent_seq': int(intent['intent_seq']),
                'input_seq': int(intent['input_seq']),
                'shot_seq': shot_seq, 'sent_at': float(now),
            }
            self._remember_player_fire_intent(key, intent)
            self._player_fire_intents.pop(key, None)
        return True

    def _launch_bot_projectile(self, state, shot_seq):
        """Publish one Bot launch; damage waits for the canonical projectile."""
        try:
            bot_id = int(state.get('id'))
            shot_seq = int(shot_seq)
            shot_yaw = float(state.get('shot_yaw'))
            shot_pitch = float(state.get('shot_pitch'))
            launch_time_us = int(state['launch_time_us'])
            launch_pose = tuple(float(value)
                                for value in state['launch_pose'])
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
        if (launch_time_us < 0 or len(launch_pose) != 6 or
                any(math.isnan(value) or math.isinf(value)
                    for value in launch_pose)):
            return False
        sender = getattr(self.client, 'send_projectile_launch', None)
        if not callable(sender):
            return False
        frozen = self._bot_launch_payloads.get((bot_id, shot_seq))
        if frozen is not None:
            accepted = sender(*frozen[0], **frozen[1])
            return accepted == shot_seq
        source_record = self._records.get('bot:%s' % bot_id)
        if source_record is None:
            return False
        source = self._server_entity(source_record['engine_id'])
        if (source is None or source.typeDescriptor is None or
                not getattr(source, 'isStarted', False)):
            return False
        shot = self._descriptor_shot(
            source.typeDescriptor, state.get('shell_index'))
        speed = _number(_field(shot, 'speed'), -1.0)
        gravity = _number(_field(shot, 'gravity'), -1.0)
        maximum = _number(_field(shot, 'maxDistance'), -1.0)
        if speed <= 0.0 or gravity <= 0.0 or maximum <= 0.0:
            return False
        profile = state.get('profile')
        profile = profile if isinstance(profile, dict) else {}
        class_tag = state.get('class_tag', profile.get('class_tag'))
        is_spg = str(class_tag or '') == 'SPG'
        max_time_ms = PROJECTILE_MAX_TIME_MS
        if is_spg:
            proof_key = state.get('shot_proof_key')
            try:
                origin = tuple(float(value)
                               for value in state['shot_origin'])
                velocity = tuple(float(value)
                                 for value in state['shot_velocity'])
                receipt_gravity = float(state['shot_gravity'])
                receipt_maximum = float(state['shot_max_distance'])
                max_time_ms = int(state['shot_max_time_ms'])
                proof_origin = tuple(float(value)
                                     for value in proof_key[6])
                proof_flight = float(proof_key[12])
                if (len(origin) != 3 or len(velocity) != 3 or
                        not isinstance(proof_key, (list, tuple)) or
                        len(proof_key) < 13):
                    return False
                proof_values = (
                    proof_key[0], int(proof_key[1]), int(proof_key[4]),
                    int(proof_key[5]), float(proof_key[7]),
                    float(proof_key[8]), float(proof_key[9]),
                    float(proof_key[10]), float(proof_key[11]))
            except (KeyError, TypeError, ValueError, IndexError,
                    OverflowError):
                return False
            values = origin + velocity + (
                receipt_gravity, receipt_maximum, proof_flight)
            horizontal = math.cos(shot_pitch)
            expected_velocity = (
                math.sin(shot_yaw) * horizontal * speed,
                math.sin(shot_pitch) * speed,
                math.cos(shot_yaw) * horizontal * speed)
            if (any(math.isnan(value) or math.isinf(value)
                    for value in values) or
                    proof_values[0] != 'launch' or
                    proof_values[1] != bot_id or
                    proof_values[2] != max(
                        0, int(state.get('shell_index', 0) or 0)) or
                    proof_values[3] != int(shot_seq) or
                    proof_values[4] != shot_yaw or
                    proof_values[5] != shot_pitch or
                    proof_values[6] != speed or
                    proof_values[7] != gravity or
                    proof_values[8] != maximum or
                    proof_origin != origin or
                    receipt_gravity != gravity or
                    receipt_maximum != maximum or
                    max_time_ms <= 0 or
                    max_time_ms > PROJECTILE_MAX_TIME_MS or
                    proof_flight <= 0.0 or
                    proof_flight * 1000.0 > max_time_ms + 1e-6 or
                    any(abs(velocity[index] - expected_velocity[index]) >
                        1e-7 for index in range(3))):
                return False
        else:
            try:
                origin = tuple(float(value) for value in state['shot_origin'])
            except (KeyError, TypeError, ValueError, OverflowError):
                return False
            if (len(origin) != 3 or
                    any(math.isnan(value) or math.isinf(value)
                        for value in origin)):
                return False
            horizontal = math.cos(shot_pitch)
            direction = (
                math.sin(shot_yaw) * horizontal,
                math.sin(shot_pitch),
                math.cos(shot_yaw) * horizontal)
            length = math.sqrt(sum(component * component
                                   for component in direction))
            if length <= 0.000001:
                return False
            velocity = tuple(
                component * speed / length for component in direction)
        is_he = combat_rules.is_he(shot)
        args = (
            'bot', bot_id, shot_seq,
            max(0, int(state.get('shell_index', 0) or 0)),
            list(origin), list(velocity), gravity, maximum,
            max_time_ms, is_he,
            combat_rules.he_radius(shot) if is_he else 0.0)
        kwargs = {
            'authority_epoch': getattr(
                self.client, 'authority_epoch', None),
            'penetration_factor':
                combat_rules.sample_penetration_factor(),
            'source_shot': descriptor_donation.project_shot(shot),
            'burst_group_seq': state.get('burst_group_seq'),
            'burst_index': state.get('burst_index'),
            'burst_count': state.get('burst_count'),
            'launch_time_us': launch_time_us,
            'launch_pose': launch_pose,
        }
        self._bot_launch_payloads[(bot_id, shot_seq)] = (args, kwargs)
        accepted = sender(*args, **kwargs)
        return accepted == shot_seq

    def _resolve_bot_shot(self, state, shot_seq):
        try:
            bot_id = int(state.get('id'))
            target_kind = state.get('target_kind')
            target_id = int(state.get('target_id'))
        except (TypeError, ValueError):
            return False
        source_record = self._records.get('bot:%s' % bot_id)
        record_kind = 'player' if target_kind == 'human' else target_kind
        target_record = self._records.get('%s:%s' % (record_kind, target_id))
        if source_record is None or target_record is None:
            return False
        source = self._server_entity(source_record['engine_id'])
        target = self._server_entity(target_record['engine_id'])
        if (source is None or target is None or
                source.typeDescriptor is None or
                not getattr(target, 'isStarted', False)):
            return False
        source_position = _xyz(getattr(source, 'position', state))
        target_position = _xyz(getattr(
            target, 'position', target_record.get('state', {})))
        gun_node = source.model.node('HP_gunFire')
        start = self._vector(
            self._runtime.math.Matrix(gun_node).translation)
        destination = self._vector((
            target_position[0], target_position[1] + 1.2,
            target_position[2]))
        target_direction = destination - start
        target_distance = target_direction.length
        if target_distance <= 0.01:
            return False
        shot = self._descriptor_shot(
            source.typeDescriptor, state.get('shell_index'))
        maximum = max(0.01, _number(
            _field(shot, 'maxDistance', 5000.0), 5000.0))
        if 'shot_yaw' in state and 'shot_pitch' in state:
            shot_yaw = _number(state.get('shot_yaw'))
            shot_pitch = _number(state.get('shot_pitch'))
            horizontal = math.cos(shot_pitch)
            direction = self._vector((
                math.sin(shot_yaw) * horizontal,
                math.sin(shot_pitch),
                math.cos(shot_yaw) * horizontal))
            direction.normalise()
        else:
            # Compatibility fallback for recorded v5 fixtures and an authority
            # takeover snapshot created before shot angles were published.
            direction = target_direction
            direction.normalise()
        end = start + direction.scale(maximum)
        hit_record = None
        target_collisions = None
        distance = 999999.0
        for record in self._records.values():
            if record is source_record:
                continue
            candidate = self._server_entity(record['engine_id'])
            if candidate is None or not getattr(candidate, 'isStarted', False):
                continue
            if (record.get('local') and self._local_matrix is not None):
                candidate_collisions = collide_vehicle_at_matrix(
                    candidate, self._local_body_pose(), start, end,
                    self._runtime.math,
                    chassis_matrix=self._local_matrix)
            elif record.get('native_remote'):
                body_matrix, chassis_matrix = \
                    self._projectile_vehicle_matrices(record, candidate)
                candidate_collisions = collide_vehicle_at_matrix(
                    candidate, body_matrix, start, end,
                    self._runtime.math, chassis_matrix=chassis_matrix)
            else:
                candidate_collisions = candidate.collideSegmentExt(start, end)
            if not candidate_collisions:
                continue
            nearest = min(candidate_collisions,
                          key=lambda item: float(item.dist))
            if float(nearest.dist) < distance:
                hit_record = record
                target_collisions = tuple(candidate_collisions)
                distance = float(nearest.dist)
        scene_end = end
        if hit_record is not None and target_collisions is not None:
            scene_end = start + direction.scale(
                max(0.0, min(maximum, distance)))
        scene = self._resolve_shot_scene(
            start, scene_end, direction, shot)
        penetration_factor = scene.get('penetration_factor')
        world_distance = scene['world_distance']
        if hit_record is None or target_collisions is None:
            if (combat_rules.is_he(shot) and
                    world_distance < maximum):
                self._he_splash(
                    start + direction.scale(world_distance), shot, shot_seq,
                    None, 'bot', bot_id, source_record['engine_id'])
            return False
        if (scene.get('stopped_by_destructible') or
                distance > world_distance + _SHOT_OCCLUSION_EPSILON):
            if combat_rules.is_he(shot):
                self._he_splash(
                    start + direction.scale(world_distance), shot, shot_seq,
                    None, 'bot', bot_id, source_record['engine_id'])
            return False
        if penetration_factor is None:
            penetration_factor = combat_rules.sample_penetration_factor()
        hit_entity = self._server_entity(hit_record['engine_id'])
        target_collisions, trace_start, trace_end = self._vehicle_trace(
            shot, start, end, target_collisions)
        damage, result = self._shell_damage(
            source.typeDescriptor, target_collisions, distance,
            shell_index=state.get('shell_index'),
            pierce_loss=scene['piercing_loss'],
            penetration_factor=penetration_factor,
            target_descriptor=getattr(hit_entity, 'typeDescriptor', None))
        impact = start + direction.scale(distance)
        hull_damage = damage
        self._install_critical_equipment_effects(hit_record, hit_entity)
        damage, critical, critical_delta = self._critical_hit(
            hit_entity, source.typeDescriptor, target_collisions,
            trace_start, trace_end,
            damage, result, source.id, state.get('shell_index'),
            burst_position=impact, deadeye=False)
        critical_contract = self._critical_proposal_contract(
            hit_record, critical, hull_damage, critical_delta)
        sent = False
        if hit_record.get('kind') == 'bot':
            sent = self.client.send_bot_bot_hit(
                bot_id, hit_record['network_id'], shot_seq,
                damage, result, _xyz(impact), critical,
                **critical_contract)
        elif hit_record.get('kind') == 'player':
            sent = self.client.send_bot_human_hit(
                bot_id, hit_record['network_id'], shot_seq,
                damage, result, _xyz(impact), critical,
                **critical_contract)
        if combat_rules.is_he(shot):
            self._he_splash(
                impact, shot, shot_seq, hit_record, 'bot', bot_id,
                source_record['engine_id'])
        return sent

    def _apply_sync_event(self, event):
        if self.state in ('failed', 'stopped', 'leaving'):
            return
        kind = event.get('type')
        if kind == 'create':
            if event.get('kind') == 'bot':
                self._queue_bot_create(event)
            else:
                self._create_remote(event)
        elif kind == 'update':
            if (event.get('kind') == 'bot' and
                    event.get('entity') not in self._records):
                self._queue_bot_create(event)
            else:
                if (event.get('kind') == 'bot' and
                        self._bots is not None and
                        self._bots.is_authority()):
                    # The copied 0.8.2 authority has already presented this
                    # bot's newest local pose.  A server snapshot is its older
                    # network echo; applying both alternately makes the hull
                    # visibly yaw left/right while it drives forward.
                    event = dict(event)
                    event.pop('pose', None)
                self._update_entity(event)
        elif kind == 'destroy':
            self._destroy_entity(event)

    def _queue_bot_create(self, event):
        """Coalesce one bot until its staggered native createEntity call.

        The 0.8.2 implementation deliberately spreads the line-up over time.
        Creating 29 HD Vehicle entities and their model prerequisites in one
        BigWorld callback is both visibly janky and unsafe in this 32-bit
        client.  Keep the newest snapshot pose while preserving roster order.
        """
        key = event.get('entity')
        if not key or key in self._records:
            return False
        state = dict(event.get('state') or {})
        pose = event.get('pose')
        if isinstance(pose, dict):
            for name in ('x', 'y', 'z', 'yaw', 'pitch', 'roll',
                         'aim_yaw', 'turret_yaw', 'gun_pitch',
                         'siege_state'):
                if name in pose:
                    state[name] = pose[name]
        pending = self._pending_bot_creates.get(key)
        if pending is None:
            pending = {
                'type': 'create', 'entity': key, 'kind': 'bot',
                'id': event.get('id'), 'state': state,
                # A later fatal event must not create an already-dead native
                # Vehicle.  The stock arena must first observe the live
                # roster entry, then consume the journaled death transition.
                'initial_state': dict(state)}
            self._pending_bot_creates[key] = pending
            self._pending_bot_create_order.append(key)
        else:
            pending['state'].update(state)
        return True

    def _flush_pending_bot_create(self, now):
        if (not self._pending_bot_create_order or
                now < self._next_bot_create_time):
            return False
        key = self._pending_bot_create_order[0]
        # Alternate teams so both bases materialize together instead of one
        # full lineup appearing before the other.
        if self._last_bot_create_team is not None:
            for candidate in self._pending_bot_create_order:
                event = self._pending_bot_creates.get(candidate)
                team = ((event or {}).get('state') or {}).get('team')
                if (team is not None and
                        int(team) != self._last_bot_create_team):
                    key = candidate
                    break
        self._pending_bot_create_order.remove(key)
        event = self._pending_bot_creates.pop(key, None)
        self._next_bot_create_time = now + BOT_SPAWN_SECONDS
        if event is None or key in self._records:
            return False
        self._create_remote(event)
        created = key in self._records
        if created:
            team = (event.get('state') or {}).get('team')
            if team is not None:
                self._last_bot_create_team = int(team)
        if created and not self._pending_bot_create_order and not (
                self._bots_ready_reported):
            # battle_start runs before the first bot is queued, so the native
            # counters only mean something once the whole roster exists.
            self._bots_ready_reported = True
            self._report_memory('bots_ready')
        return created

    def _create_remote(self, event):
        key = event.get('entity')
        if key in self._records:
            return
        state = dict(event.get('state') or {})
        initial_state = dict(event.get('initial_state') or state)
        if not all(name in state for name in ('team', 'slot')):
            return
        if event.get('kind') == 'bot' and not all(
                name in state for name in ('x', 'z')):
            return
        if event.get('kind') == 'player' and (
                self._worker_mode or state.get('vehicle_compact_descr')):
            descriptor = self._resolve_player_descriptor(state)
        else:
            descriptor = self._resolve_descriptor(
                state.get('vehicle', self._config['vehicle']))
        properties = self._binding.properties_from_compact_descr(
            descriptor.makeCompactDescr(), int(state.get('team', 1)),
            state.get('name', 'Vehicle'))
        # BigWorldVehicleBinding's provider is deliberately local-only.  A
        # remote human receives its own validated LAN outfit; bots have no
        # garage owner and always receive the stock empty descriptor.
        properties['publicInfo']['outfit'] = self._remote_outfit(
            state, event.get('kind'))
        properties['health'] = max(0, min(
            int(initial_state.get('health', descriptor.maxHealth)),
            int(descriptor.maxHealth)))
        position, yaw = self._state_world_pose(state)
        if self._remote_factory is None:
            raise RuntimeError('remote vehicle factory is unavailable')
        engine_id = self._remote_factory.create(
            descriptor, properties, self._vector(position),
            _engine_rotation(yaw))
        if engine_id is None:
            raise RuntimeError('remote presentation returned no vehicle id')
        interpolation_setter = getattr(
            self._remote_factory, 'set_entity_interpolate_motion', None)
        if callable(interpolation_setter):
            interpolate_motion = (
                event.get('kind') == 'bot' and
                self._bot_authority_is_local(
                    (self._start_message or {}).get(
                        'bot_authority_id')))
            interpolation_setter(engine_id, interpolate_motion)
        self._records[key] = {
            'engine_id': engine_id, 'state': state,
            'kind': event.get('kind'), 'network_id': event.get('id'),
            'local': False, 'presentation': True, 'ready': False,
            'arena_added': bool(getattr(
                self._remote_factory, 'native_entities', False)),
            'native_remote': bool(getattr(
                self._remote_factory, 'native_entities', False)),
            'properties': properties,
            'spot_visible': bot_planner.bot_initially_visible(
                int(state.get('team', 1)),
                int(getattr(self.client, 'team', 1)), True),
            'spot_marker_visible': bot_planner.bot_initially_visible(
                int(state.get('team', 1)),
                int(getattr(self.client, 'team', 1)), True),
            'spot_until': 0.0, 'radio_spot_until': 0.0,
            'spot_next': 0.0,
            'shot_penalty_until': float(
                state.get('shot_penalty_until', 0.0) or 0.0),
            'ready_deadline': self._clock() + float(
                self._config.get('startupTimeoutSeconds', 30.0))}
        self._records_revision += 1
        seed_pose = dict(state)
        seed_pose.update({
            'x': position[0], 'y': position[1], 'z': position[2],
            'yaw': yaw})
        self._records[key]['projectile_collision_pose'] = \
            self._projectile_plain_pose(position, seed_pose)
        self._materialize_record(self._records[key])

    def _update_entity(self, event):
        record = self._records.get(event.get('entity'))
        if record is not None and record.get('tombstone'):
            return
        if record is None:
            state = event.get('state') or {}
            self._create_remote({
                'type': 'create', 'entity': event.get('entity'),
                'kind': event.get('kind'), 'id': event.get('id'),
                'state': state})
            record = self._records.get(event.get('entity'))
            if record is None:
                return
        pose = event.get('pose')
        incoming_state = event.get('state') or {}
        if event.get('interpolated') and not incoming_state:
            # SnapshotSync emits one pose-only sample per rendered frame.
            # Ready entities do not need state cloning, visibility, health,
            # critical, siege or event-journal reconciliation for that sample.
            # An authority echo whose pose was stripped above is a complete
            # no-op; a not-yet-ready entity still falls through so its newest
            # pose is coalesced until onEnterWorld completes.
            if pose is None:
                return
            if record.get('ready'):
                if (record.get('kind') == 'bot' and
                        event.get('presentation_time_us') is not None):
                    record['presented_pose'] = dict(pose)
                    record['presentation_time_us'] = int(
                        event.get('presentation_time_us'))
                self._apply_record_pose(record, pose)
                return
        state = dict(record.get('state') or {})
        incoming = dict(incoming_state)
        state.update(incoming)
        record['state'] = state
        if pose is not None:
            record['pending_pose'] = dict(pose)
            if (record.get('kind') == 'bot' and
                    event.get('presentation_time_us') is not None):
                record['presented_pose'] = dict(pose)
                record['presentation_time_us'] = int(
                    event.get('presentation_time_us'))
        self._materialize_record(record)

    def _materialize_record(self, record):
        if record.get('ready'):
            ready = True
        elif record.get('presentation'):
            error = self._remote_factory.error(record['engine_id'])
            if error is not None:
                raise RuntimeError(
                    'remote vehicle %s failed: %s' % (
                        record['engine_id'], error))
            ready = self._remote_factory.is_ready(record['engine_id'])
            if not ready:
                return False
            record['ready'] = True
        else:
            status = ('completed', None)
            status_getter = getattr(self._server, 'vehicleEnterStatus', None)
            if callable(status_getter):
                status = status_getter(record['engine_id'])
            if status[0] == 'failed':
                raise RuntimeError('Vehicle %s enter failed: %s' % (
                    record['engine_id'], status[1]))
            ready = (status[0] == 'completed' and
                     self._binding.is_vehicle_ready(record['engine_id']))
            if not ready:
                return False
            record['ready'] = True
        if record.get('presentation'):
            # ArenaVehiclesPlugin decides whether a roster entry is already
            # in AOI by reading BigWorld.entities during VEHICLE_ADDED. Set
            # the spotting gate before that event, otherwise every enemy is
            # permanently introduced on the minimap at battle load.
            vehicle = self._remote_factory.get(record['engine_id'])
            if vehicle is None or vehicle.model is None:
                raise RuntimeError('remote vehicle has no ready presentation')
            if not record.get('presentation_initialized'):
                initially_visible = bool(record.get('spot_visible', True))
                vehicle._spot_visible = initially_visible
                if self._worker_mode:
                    # Keep the assembled compound, muzzle nodes and hit tester
                    # for authority simulation. Do not register markers,
                    # target caps, battle UI or shot/sound presentation in the
                    # hidden worker.
                    record['simulation_entity'] = True
                elif record.get('native_remote'):
                    vehicle._offlineNativeDrawVisible = initially_visible
                    set_draw_visibility(vehicle, initially_visible)
                    vehicle.targetCaps = [1] if initially_visible else []
                    # The compatibility enter-world gate may already have
                    # stopped an enemy marker before this asynchronous ready
                    # boundary.
                    record['visual_started'] = bool(getattr(
                        vehicle, '_offlineNativeMarkerVisible',
                        initially_visible))
                    record['world_marker_started'] = bool(
                        record['visual_started'])
                    record['minimap_started'] = bool(
                        record['visual_started'])
                    vehicle._offlineNativeMarkerVisible = bool(
                        record['world_marker_started'])
                else:
                    vehicle.appearance.changeVisibility(initially_visible)
                record['presentation_initialized'] = True
        if (not self._worker_mode and record.get('presentation') and
                not record.get('arena_added')):
            self._binding.arena_vehicle_added(record['engine_id'], {
                'properties': record['properties'],
                'team_killer': bool(
                    (record.get('state') or {}).get('team_killer', False))})
            record['arena_added'] = True
        if (not self._worker_mode and record.get('presentation') and
                not record.get('native_remote') and
                record.get('arena_added') and
                record.get('spot_visible', True) and
                not record.get('visual_started')):
            self._binding.start_vehicle_visual(record['engine_id'], True)
            record['visual_started'] = True
            record['world_marker_started'] = True
            record['minimap_started'] = True
            if not self._record_alive(record, vehicle):
                self._present_vehicle_dead(record, True)
        if record.get('presentation') and not self._worker_mode:
            self._set_record_spot_visibility(
                record, record.get('spot_visible', True),
                record.get(
                    'spot_marker_visible',
                    record.get('spot_visible', True)))
        pose = record.pop('pending_pose', None)
        if pose is not None:
            self._apply_record_pose(record, pose)
        state = record.get('state') or {}
        self._apply_siege_state(record, state)
        self._apply_vehicle_statistics(record, state)
        # Arena registration and the native Vehicle are now both complete.
        # Consume any ordered one-shot feedback before snapshot reconciliation
        # can make the same health/critical signature look already presented.
        self._drain_event_journal()
        state = record.get('state') or {}
        if self._pending_combat_for_record(record):
            return True
        self._apply_stun_state(record, state)
        critical = state.get('critical')
        if isinstance(critical, dict):
            self._apply_critical_state(record, critical, state)
        elif (record.get('kind') == 'player' and
              all(name in state for name in (
                  'critical_revision', 'critical_base_revision',
                  'critical_ack_seq'))):
            self._reconcile_critical_authority(record, state)
        self._apply_health(
            record, state, self._death_attacker_engine_id(state),
            max(0, int(state.get('death_reason', 0) or 0)))
        return True

    def _apply_stun_state(self, record, state):
        """Present the exact #1513 ``stunInfo`` absolute-time contract."""
        end = state.get('stun_end_server_time_ms', 0)
        if (isinstance(end, bool) or
                not isinstance(end, _INTEGER_TYPES) or end < 0):
            raise RuntimeError('LAN snapshot has an invalid stun end time')
        attacker_kind = state.get('stun_attacker_kind', '')
        attacker_id = state.get('stun_attacker_id', 0)
        if end:
            if (attacker_kind not in ('player', 'bot') or
                    isinstance(attacker_id, bool) or
                    not isinstance(attacker_id, _INTEGER_TYPES) or
                    attacker_id <= 0):
                raise RuntimeError(
                    'LAN snapshot has an invalid stun attacker')
        elif attacker_kind or attacker_id:
            raise RuntimeError('cleared LAN stun retains an attacker')
        signature = (int(end), str(attacker_kind), int(attacker_id))
        # Vehicle construction already seeds stunInfo=0. Keep this additive
        # snapshot field compatible with older peers and avoid replaying an
        # initial no-op clear through the stock feedback adaptor.
        if record.get('presented_stun_state') is None and not end:
            record['presented_stun_state'] = signature
            return False
        if record.get('presented_stun_state') == signature:
            return False
        estimated = self._projectile_estimated_server_time(self._clock())
        if end and estimated is None:
            raise RuntimeError('LAN stun has no server-time anchor')
        remaining = (max(0.0, (float(end) - float(estimated)) / 1000.0)
                     if end else 0.0)
        entity = self._server_entity(record['engine_id'])
        if entity is None:
            raise RuntimeError('stunned LAN vehicle is unavailable')
        previous = getattr(entity, 'stunInfo', 0.0)
        entity.stunInfo = (
            self._server_clock() + remaining if remaining > 0.0 else 0.0)
        if not self._worker_mode:
            native_callback = getattr(entity, 'set_stunInfo', None)
            if callable(native_callback):
                # Stock Vehicle.updateStunInfo owns both attached-player HUD
                # state and remote feedback in #1513.
                native_callback(previous)
            elif record.get('local'):
                self._avatar.guiSessionProvider.invalidateVehicleState(
                    self._runtime.vehicle_view_state.STUN, remaining)
            else:
                feedback = self._avatar.guiSessionProvider.shared.feedback
                callback = getattr(feedback, 'invalidateStun', None)
                if not callable(callback):
                    raise RuntimeError(
                        '#1513 remote stun feedback boundary is unavailable')
                callback(record['engine_id'], remaining)
        record['presented_stun_state'] = signature
        return True

    def _apply_siege_state(self, record, state):
        """Apply a server-owned Siege transition through #1513's callback."""
        siege_states = self._runtime.constants.VEHICLE_SIEGE_STATE
        disabled = siege_states.DISABLED
        siege_state = state.get('siege_state', disabled)
        time_left_ms = state.get('siege_time_left_ms', 0)
        if (isinstance(siege_state, bool) or
                not isinstance(siege_state, _INTEGER_TYPES)):
            raise RuntimeError('LAN snapshot has an invalid siege state')
        allowed = (disabled, siege_states.SWITCHING_ON,
                   siege_states.ENABLED, siege_states.SWITCHING_OFF)
        if siege_state not in allowed:
            raise RuntimeError('LAN snapshot has an unsupported siege state')
        if (isinstance(time_left_ms, bool) or
                not isinstance(time_left_ms, _INTEGER_TYPES) or
                time_left_ms < 0):
            raise RuntimeError(
                'LAN snapshot has an invalid siege transition time')
        switching = siege_state in (
            siege_states.SWITCHING_ON, siege_states.SWITCHING_OFF)
        if ((switching and time_left_ms <= 0) or
                (not switching and time_left_ms != 0)):
            raise RuntimeError(
                'LAN snapshot has an inconsistent siege transition')
        if record.get('local') and self._local_siege_pending is not None:
            unused_enabled, request_seq = self._local_siege_pending
            acknowledged = request_seq is None
            snapshot_seq = state.get('input_seq')
            if (not isinstance(snapshot_seq, bool) and
                    isinstance(snapshot_seq, _INTEGER_TYPES)):
                acknowledged = bool(
                    request_seq is None or snapshot_seq >= request_seq)
            # A switching echo keeps the pending edge latched. A stable echo
            # at or beyond the request sequence either confirms the target or
            # explicitly reflects a server rejection (for example, a dead
            # engine); both outcomes release local controls safely.
            if acknowledged and not switching:
                self._local_siege_pending = None
        if record.get('presented_siege_state') == siege_state:
            return False
        # Vehicle construction already seeds DISABLED.  Skipping that first
        # no-op also keeps an additive snapshot field compatible with records
        # created by older peers and authority-only test doubles.
        if (record.get('presented_siege_state') is None and
                siege_state == disabled):
            record['presented_siege_state'] = disabled
            return False
        entity = self._server_entity(record['engine_id'])
        descriptor = getattr(entity, 'typeDescriptor', None)
        if descriptor is None:
            raise RuntimeError('Siege vehicle descriptor is unavailable')
        if (not bool(getattr(descriptor, 'hasSiegeMode', False)) and
                siege_state != disabled):
            raise RuntimeError(
                'Vehicle without Siege mode received an active state')
        if not bool(getattr(descriptor, 'hasSiegeMode', False)):
            record['presented_siege_state'] = disabled
            return False
        self._binding.update_vehicle_siege_state(
            record['engine_id'], siege_state,
            float(time_left_ms) / 1000.0)
        record['presented_siege_state'] = siege_state
        if (self._worker_mode and record.get('native_remote') and
                self._remote_factory is not None):
            updater = getattr(
                self._remote_factory, 'update_entity_siege_pose', None)
            if callable(updater):
                updater(record['engine_id'])
        if record.get('local') and self._local_matrix is not None:
            self._select_local_siege_pose(
                entity, siege_state in (
                    siege_states.ENABLED, siege_states.SWITCHING_OFF))
        if record.get('local') and self._gun_state is not None:
            self._gun_state.adopt_descriptor(entity.typeDescriptor)
            self._targeting_signature = None
        if record.get('local') and self._local_physics is not None:
            self._local_physics = vehicle_physics.derive_params(
                entity.typeDescriptor,
                self._local_factors(entity.typeDescriptor))
        if record.get('local') and self._local_matrix is not None:
            self._update_local_hull_aiming(entity, 0.0)
        return True

    def _apply_record_pose(self, record, pose):
        state = record.get('state') or {}
        yaw = _number(pose.get('yaw'))
        if record.get('local'):
            # A snapshot is a delayed echo of the local native physics sample.
            # #1513 exposes no legal pose setter for a client-created Vehicle,
            # so reconciliation remains a server/rules concern rather than
            # rewinding the live C++ object.
            return
        else:
            now = self._clock()
            x = _number(pose.get('x'))
            y = _number(pose.get('y'))
            z = _number(pose.get('z'))
            pitch = _number(pose.get('pitch'))
            roll = _number(pose.get('roll'))
            aim_yaw = _number(pose.get('aim_yaw', yaw))
            gun_pitch = _number(pose.get('gun_pitch'))
            collision_pose = self._projectile_plain_pose(
                (x, y, z), pose)
            collision_pose['siege_state'] = int(
                pose.get('siege_state', state.get('siege_state', 0)) or 0)
            record['projectile_collision_pose'] = collision_pose
            alive = bool(state.get('alive', True)) and int(
                state.get('health', 1) or 0) > 0
            signature = (x, y, z, yaw, pitch, roll, aim_yaw, gun_pitch)
            if signature == record.get('_remote_pose_signature'):
                motion_intended = bool(
                    abs(_number(state.get('speed'))) > BOT_MOVING_SPEED or
                    abs(_number(state.get('movement_dir'))) > 0.5 or
                    abs(_number(state.get('rotation_dir'))) > 0.5)
                if (record.get('native_remote') and not motion_intended and
                        record.get('_remote_motion_settled_signature') !=
                        signature):
                    vehicle = self._remote_factory.get(record['engine_id'])
                    settle = getattr(vehicle, 'settle_motion', None)
                    if callable(settle):
                        settled = self._run_optional_feature(
                            'remote motion presentation', settle, (now,))
                        if settled:
                            record['_remote_motion_settled_signature'] = \
                                signature
                if record.get('kind') in ('bot', 'player'):
                    # Keep the zero-turn sample fresh so the first real pivot
                    # after a long stop is measured over one render interval,
                    # not diluted across the whole stationary period.
                    turn = self._remember_remote_track_turn(record, yaw, now)
                    track_signature = (
                        _number(state.get('speed')), alive, turn)
                    if (record.get('_remote_track_pending') or
                            track_signature != record.get(
                                '_remote_track_state_signature')):
                        updated = self._run_optional_feature(
                            'remote track animation', self._update_bot_tracks,
                            (record, state, now, turn))
                        record['_remote_track_pending'] = not updated
                        if updated:
                            record['_remote_track_state_signature'] = \
                                track_signature
                return False
            self._binding.set_vehicle_pose(
                record['engine_id'], self._vector((x, y, z)),
                _engine_rotation(yaw, pitch, roll),
                now=now)
            self._binding.update_vehicle_aim(
                record['engine_id'], yaw, aim_yaw, gun_pitch)
            record['_remote_pose_signature'] = signature
            record.pop('_remote_motion_settled_signature', None)
            if record.get('kind') in ('bot', 'player'):
                turn = self._remember_remote_track_turn(record, yaw, now)
                track_signature = (
                    _number(state.get('speed')), alive, turn)
                updated = self._run_optional_feature(
                    'remote track animation', self._update_bot_tracks,
                    (record, state, now, turn))
                record['_remote_track_pending'] = not updated
                if updated:
                    record['_remote_track_state_signature'] = track_signature
            return True

    def _flush_pending_entities(self, now):
        for unused_key, record in list(self._records.items()):
            if record.get('tombstone'):
                self._flush_tombstone(record)
                continue
            if record.get('ready'):
                continue
            if self._materialize_record(record):
                continue
            deadline = record.get('ready_deadline')
            if deadline is not None and now >= deadline:
                raise RuntimeError(
                    'Vehicle %s did not enter world before timeout' %
                    record['engine_id'])

    def _flush_tombstone(self, record):
        """Destroy a remote Vehicle that entered after its network removal."""
        if record.get('presentation'):
            if self._remote_factory is not None:
                if self._outlined_engine_id == record.get('engine_id'):
                    self._clear_target_outline()
                if not record.get('native_remote'):
                    self._stop_remote_visual(record)
                self._remote_factory.destroy(record['engine_id'])
            record['visible_destroy_requested'] = True
            return
        if record.get('visible_destroy_requested'):
            return
        try:
            entity = self._server_entity(record['engine_id'])
        except ReferenceError:
            entity = None
        if entity is None:
            return
        try:
            self._binding.arena_vehicle_removed(record['engine_id'])
        finally:
            self._binding.destroy_entity(record['engine_id'])
        record['visible_destroy_requested'] = True

    def _set_record_spot_visibility(self, record, visible,
                                    marker_visible=None):
        """Apply independent model and team-knowledge presentation gates.

        A destroyed vehicle is a world object, not a spotting target. Its
        wreck remains enabled for the stock renderer in every camera mode;
        native distance and frustum culling still own whether it is drawn.
        """
        visible = bool(visible)
        if marker_visible is None:
            marker_visible = record.pop(
                '_spot_marker_transition', visible)
        marker_visible = bool(marker_visible)
        record['spot_visible'] = visible
        record['spot_marker_visible'] = marker_visible
        if not record.get('presentation') or not record.get('ready'):
            return visible
        vehicle = self._remote_factory.get(record['engine_id'])
        if vehicle is None or vehicle.model is None:
            raise RuntimeError('spotted remote vehicle has no model')
        vehicle._spot_visible = visible
        alive = self._record_alive(record, vehicle)
        draw_vehicle = visible or not alive
        signature = (visible, marker_visible, draw_vehicle, alive)
        if signature != record.get('_spot_presentation_signature'):
            if record.get('native_remote'):
                vehicle._offlineNativeDrawVisible = draw_vehicle
                set_draw_visibility(vehicle, draw_vehicle)
                vehicle.targetCaps = [1] if visible and alive else []
            else:
                vehicle.appearance.changeVisibility(draw_vehicle)
            if draw_vehicle:
                # A fire transition received while this enemy was hidden had
                # no drawable compound. Reconcile it on the presentation edge,
                # not on every unrelated state or pose update.
                self._sync_fire_effect(vehicle)
            record['_spot_presentation_signature'] = signature
        self._sync_remote_visual_components(
            record, vehicle, marker_visible, visible)
        if (marker_visible and
                record.get('deferred_health_presentation', False)):
            provider = getattr(self._avatar, 'guiSessionProvider', None)
            present_health = getattr(provider, 'setVehicleHealth', None)
            if not callable(present_health):
                raise RuntimeError(
                    '#1513 remote vehicle health presenter is unavailable')
            state = record.get('state') or {}
            health = max(0, int(state.get(
                'display_health', state.get('health', 0)) or 0))
            present_health(False, record['engine_id'], health, 0, 0)
            record.pop('deferred_health_presentation', None)
        return visible

    @staticmethod
    def _remote_visual_components(record):
        """Return the separately tracked #1513 marker/minimap lifetimes."""
        if ('world_marker_started' not in record and
                'minimap_started' not in record):
            legacy = bool(record.get('visual_started', False))
            return legacy, legacy
        return (bool(record.get('world_marker_started', False)),
                bool(record.get('minimap_started', False)))

    @staticmethod
    def _store_remote_visual_components(record, world_marker, minimap):
        record['world_marker_started'] = bool(world_marker)
        record['minimap_started'] = bool(minimap)
        # Retain the historic aggregate for teardown and older harnesses.
        record['visual_started'] = bool(world_marker or minimap)

    def _sync_remote_visual_components(
            self, record, vehicle, team_visible, world_visible):
        """Keep the 3D marker inside AOI while retaining team minimap data.

        Exact #1513 ``BattleFeedbackAdaptor.startVehicleVisual`` emits two
        independent signals: ``onVehicleMarkerAdded`` and
        ``onMinimapVehicleAdded``.  A synthetic entity never leaves the
        native AOI, so the LAN runtime must split those signals itself when a
        team-spotted vehicle moves outside the circular vehicle AOI.
        """
        world_started, minimap_started = \
            self._remote_visual_components(record)
        team_visible = bool(team_visible)
        world_visible = bool(world_visible and team_visible)
        entity_id = int(record['engine_id'])
        if (not world_visible and
                self._outlined_engine_id == entity_id):
            self._clear_target_outline()
        try:
            if team_visible:
                if (world_visible and not world_started and
                        not minimap_started):
                    self._binding.start_vehicle_visual(entity_id, True)
                    world_started = True
                    minimap_started = True
                    if not self._record_alive(record, vehicle):
                        self._present_vehicle_dead(record, True)
                else:
                    if not minimap_started:
                        self._binding.start_vehicle_minimap(entity_id)
                        minimap_started = True
                    if world_visible and not world_started:
                        self._binding.start_vehicle_marker(entity_id)
                        world_started = True
                        if not self._record_alive(record, vehicle):
                            self._present_vehicle_dead(record, True)
                    elif not world_visible and world_started:
                        self._binding.stop_vehicle_marker(entity_id)
                        world_started = False
            elif world_started and minimap_started:
                self._binding.stop_vehicle_visual(entity_id, False)
                world_started = False
                minimap_started = False
            else:
                if world_started:
                    # A marker without a minimap can exist only after a
                    # partial callback failure.  The complete stock stop also
                    # clears BattleFeedbackAdaptor's visible-vehicle set.
                    self._binding.stop_vehicle_visual(entity_id, False)
                    world_started = False
                elif minimap_started:
                    self._binding.stop_vehicle_minimap(entity_id)
                    minimap_started = False
        finally:
            # Each flag advances only after its exact native signal returns.
            # Preserve partial progress so a caller can clean up or retry the
            # remaining component without replaying a completed signal.
            self._store_remote_visual_components(
                record, world_started, minimap_started)
            record['spot_world_marker_visible'] = bool(world_started)
            if record.get('native_remote'):
                vehicle._offlineNativeMarkerVisible = bool(world_started)
        return bool(world_started or minimap_started)

    def _strategic_spg_view_active(self):
        """Whether this client currently owns the stock SPG overhead camera."""
        descriptor = self._local_descriptor
        tags = _field(_field(descriptor, 'type', {}), 'tags', ()) or ()
        if 'SPG' not in tags:
            return False
        handler = getattr(self._avatar, 'inputHandler', None)
        modes = getattr(self._runtime.avatar_input_handler, '_CTRL_MODE', None)
        if handler is None or modes is None:
            return False
        strategic = getattr(modes, 'STRATEGIC', 'strategic')
        return getattr(
            handler, '_AvatarInputHandler__ctrlModeName', None) == strategic

    def _spot_presentation_visibility(
            self, entity, remembered, was_model_visible=False):
        """Return ``(model, team knowledge)`` for one spotted enemy.

        The minimap follows team spotting memory.  The ordinary world model
        and 3D marker remain bounded by the 565 m entity AOI, except that an
        SPG in strategic view must be able to aim at every team-spotted target
        in its shell range.  Exact #1513 keeps an already-present entity for
        the additional five-metre ``CIRCULAR_AOI_MARGIN`` to prevent boundary
        flicker.
        """
        remembered = bool(remembered)
        aoi_radius = spotting.VEHICLE_AOI_RADIUS
        if was_model_visible:
            aoi_radius += spotting.VEHICLE_AOI_HYSTERESIS_MARGIN
        within_aoi = _distance_2d(
            self._local_position, _xyz(entity.position)) <= aoi_radius
        model_visible = remembered and (
            within_aoi or self._strategic_spg_view_active())
        return model_visible, remembered

    def _apply_spot_presentation(self, record, entity, remembered):
        """Publish a split spotting state through the legacy two-arg seam."""
        model_visible, marker_visible = self._spot_presentation_visibility(
            entity, remembered,
            was_model_visible=bool(record.get('spot_visible', False)))
        # A number of engine-free harnesses replace the historic two-argument
        # method.  Store the marker edge first so those focused harnesses stay
        # useful while production consumes both states below.
        record['spot_marker_visible'] = marker_visible
        record['_spot_marker_transition'] = marker_visible
        try:
            self._set_record_spot_visibility(record, model_visible)
        finally:
            record.pop('_spot_marker_transition', None)
        return model_visible, marker_visible

    def _present_direct_spot(self, record):
        """Publish the one stock ribbon and sound for a first direct spot."""
        if record.get('spot_feedback_sent'):
            return False
        feedback_common = getattr(
            self._runtime, 'battle_feedback_common', None)
        event_types = getattr(feedback_common, 'BATTLE_EVENT_TYPE', None)
        if event_types is None:
            raise RuntimeError(
                '#1513 spotting feedback constants are unavailable')
        pack_visibility = getattr(event_types, 'packVisibility', None)
        if not callable(pack_visibility):
            raise RuntimeError(
                '#1513 visibility feedback packer is unavailable')
        callback = getattr(self._avatar, 'onBattleEvents', None)
        if not callable(callback):
            raise RuntimeError(
                '#1513 battle-event feedback boundary is unavailable')
        target_id = int(record['engine_id'])
        callback([
            {
                'eventType': int(event_types.SPOTTED),
                'targetID': target_id, 'count': 1, 'details': 0,
            },
            {
                'eventType': int(event_types.TARGET_VISIBILITY),
                'targetID': target_id, 'count': 1,
                'details': int(pack_visibility(True, True)),
            },
        ])
        record['spot_feedback_sent'] = True
        return True

    @staticmethod
    def _record_alive(record, entity):
        state = record.get('state') or {}
        if 'alive' in state:
            return bool(state.get('alive')) and int(
                state.get('health', 1) or 0) > 0
        alive = getattr(entity, 'isAlive', None)
        return bool(alive() if callable(alive) else alive)

    def _spotting_profile(self, descriptor, local=False):
        """Return the device and crew spotting inputs for one descriptor.

        Both sides read the same ``factors`` dictionary the garage panel
        reads; only the crew behind it differs, because a bot has the default
        crew instead of the player's own.
        """
        if local:
            if self._local_spotting_cache is None:
                snapshot = self._garage_loadout_snapshot()
                crew = snapshot['crew'] or None
                self._local_spotting_cache = loadout_law.spotting_profile(
                    descriptor, crew,
                    level_increase=loadout_law.crew_level_increase(
                        descriptor, snapshot['equipments'],
                        loadout_law.crew_skill_names(crew) if crew else None),
                    factors=self._local_factors(descriptor))
            return self._local_spotting_cache
        # Every non-local vehicle carries the default crew, so its profile
        # depends only on the vehicle type and what is mounted on it.
        key = (_field(descriptor, 'name', ''),
               loadout_law.device_names(descriptor))
        profile = self._remote_spotting_cache.get(key)
        if profile is None:
            profile = loadout_law.spotting_profile(
                descriptor, None,
                level_increase=loadout_law.crew_level_increase(descriptor),
                factors=loadout_law.attribute_factors(descriptor))
            self._remote_spotting_cache[key] = profile
        return profile

    def _local_factors(self, descriptor):
        """Cache the player's own #1513 attribute factors for this round."""
        if self._local_factors_cache is None:
            snapshot = self._garage_loadout_snapshot()
            # updateAttrFactorsWithSplit treats every supplied equipment as
            # active.  That is correct for passive fuel and food, but would
            # leave the trigger-only Removed RPM Limiter permanently on.
            # Until its activation and engine-damage lifecycle is modelled,
            # omit that one item rather than granting a silent 10% power buff.
            equipments = tuple(
                equipment for equipment in snapshot['equipments']
                if not any(
                    'removedrpmlimiter' in name for name in
                    loadout_law.equipment_names((equipment,))))
            self._local_factors_cache = loadout_law.attribute_factors(
                descriptor, snapshot['crew'] or None,
                equipments) or False
        return self._local_factors_cache or None

    def _ram_profile(self, descriptor, local=False):
        """Return the #1513 ram inputs for one mounted descriptor."""
        if local:
            if self._local_ram_profile_cache is None:
                snapshot = self._garage_loadout_snapshot()
                bonus = loadout_law.ramming_bonus(snapshot.get('crew'))
                self._local_ram_profile_cache = (
                    tank_collision.descriptor_ram_profile(
                        descriptor, bonus))
            return self._local_ram_profile_cache
        key = (_field(descriptor, 'name', ''),
               loadout_law.device_names(descriptor))
        profile = self._remote_ram_profile_cache.get(key)
        if profile is None:
            # Non-local vehicles use the default untrained crew.  Their
            # descriptor still carries any mounted Spall Liner and its weight.
            profile = tank_collision.descriptor_ram_profile(descriptor)
            self._remote_ram_profile_cache[key] = profile
        return profile

    def _vision_radius(self, descriptor, entity=None, still_seconds=0.0,
                       local=False):
        turret = _field(descriptor, 'turret', {})
        misc = _field(descriptor, 'miscAttrs', {})
        damage_factor = 1.0
        if entity is not None:
            damage_factor = critical_damage._device_damage.\
                clamp_vision_factor(
                    critical_damage.stat_factor(entity, 'vision'))
        profile = self._spotting_profile(descriptor, local)
        return spotting.effective_view_range(
            _field(turret, 'circularVisionRadius', 400.0),
            misc_factor=(
                _field(misc, 'circularVisionRadiusFactor', 1.0) *
                damage_factor),
            crew_factor=profile['vision_factor'],
            binocular_factor=profile['binocular_factor'],
            binocular_active=(
                profile['has_binoculars'] and
                loadout_law.still_device_active(
                    still_seconds, profile['binocular_delay'])))

    @staticmethod
    def _base_invisibility(descriptor, profile, camouflage_id=None):
        """#1513 ``computeBaseInvisibility``, returned as ``(moving, still)``."""
        crew_factor = profile['camouflage_factor']
        calculator = getattr(descriptor, 'computeBaseInvisibility', None)
        if callable(calculator):
            try:
                values = calculator(crew_factor, camouflage_id)
                if isinstance(values, (list, tuple)) and len(values) >= 2:
                    return (_number(values[0]), _number(values[1]))
            except Exception:
                pass
        vehicle_type = _field(descriptor, 'type', {})
        values = _field(vehicle_type, 'invisibility', (0.0, 0.0))
        if not isinstance(values, (list, tuple)) or len(values) < 2:
            values = (0.0, 0.0)
        misc = _field(descriptor, 'miscAttrs', {})
        return spotting.base_camouflage(
            values[0], values[1], crew_factor=crew_factor,
            invisibility_factor=_field(misc, 'invisibilityFactor', 1.0))

    @staticmethod
    def _invisibility_aspect(profile, moving, still_device_ready):
        """Pick the stationary aspect only once the net has really settled."""
        if moving or (profile['has_camouflage_net'] and
                      not still_device_ready):
            return profile['invisibility_moving']
        return profile['invisibility_still']

    @staticmethod
    def _shot_invisibility_factor(descriptor):
        gun = _field(descriptor, 'gun', {})
        return spotting.clamp(
            _field(gun, 'invisibilityFactorAtShot', 1.0), 0.0, 1.0)

    def _foliage_camouflage_bonus(self, observer, target, fired_recently):
        if (self._foliage is None or not self._optional_feature_enabled(
                'foliage camouflage')):
            return 0.0
        try:
            return self._foliage.camouflage_bonus(
                observer, target, fired_recently)
        except Exception as error:
            self._foliage = None
            self._warn_optional_failure('foliage camouflage', error)
            return 0.0

    def _activate_fallen_tree_foliage(self, chunk_id, item_index):
        if (self._foliage is None or not self._optional_feature_enabled(
                'foliage camouflage')):
            return False
        activate = getattr(self._foliage, 'activate_fallen_tree', None)
        if not callable(activate):
            return False
        try:
            changed = bool(activate(chunk_id, item_index))
            if changed:
                self._next_fallen_tree_foliage_refresh = 0.0
                identity = (int(chunk_id), int(item_index))
                self._fallen_tree_foliage_seen_bodies.discard(identity)
                self._fallen_tree_foliage_stable.pop(identity, None)
            return changed
        except Exception as error:
            self._foliage = None
            self._warn_optional_failure('foliage camouflage', error)
            return False

    def _fallen_tree_body_identities(self):
        area_destructibles = getattr(
            self._runtime, 'area_destructibles', None)
        animator = getattr(
            area_destructibles, 'g_destructiblesAnimator', None)
        bodies = getattr(
            animator, '_DestructiblesAnimator__bodies', None)
        if not isinstance(bodies, (list, tuple)):
            return None
        identities = set()
        for body in bodies:
            if not isinstance(body, dict):
                continue
            try:
                if int(body.get('spaceID')) != int(self._avatar.spaceID):
                    continue
                identities.add((
                    int(body['chunkID']), int(body['destrIndex'])))
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        return identities

    def _native_fallen_tree_pose(self, chunk_id, item_index):
        profile_reader = getattr(
            self._foliage, 'fallen_tree_profile', None)
        if not callable(profile_reader):
            return None
        profile = profile_reader(chunk_id, item_index)
        if profile is None:
            return None
        center, half_sizes = profile
        bigworld = self._runtime.bigworld
        math_module = self._runtime.math
        chunk_matrix = bigworld.wg_getChunkMatrix(
            self._avatar.spaceID, int(chunk_id))
        chunk_translation = getattr(chunk_matrix, 'translation', None)
        if chunk_translation is None:
            return None
        matrix = math_module.Matrix(bigworld.wg_getDestructibleMatrix(
            self._avatar.spaceID, int(chunk_id), int(item_index)))
        local_center = self._vector(center)
        transformed_center = matrix.applyPoint(local_center)
        world_center = (
            float(chunk_translation.x + transformed_center.x),
            float(chunk_translation.y + transformed_center.y),
            float(chunk_translation.z + transformed_center.z),
        )
        half_axes = []
        for axis in (
                (half_sizes[0], 0.0, 0.0),
                (0.0, half_sizes[1], 0.0),
                (0.0, 0.0, half_sizes[2])):
            vector = matrix.applyVector(self._vector(axis))
            half_axes.append((
                float(vector.x), float(vector.y), float(vector.z)))
        return world_center, tuple(half_axes)

    def _refresh_fallen_tree_foliage(self, now, force=False):
        if self._foliage is None:
            return False
        pending_reader = getattr(
            self._foliage, 'refreshing_fallen_tree_wires', None)
        update = getattr(self._foliage, 'update_fallen_tree_pose', None)
        settle = getattr(self._foliage, 'settle_fallen_tree', None)
        if not (callable(pending_reader) and callable(update) and
                callable(settle)):
            return False
        pending = tuple(pending_reader())
        if not pending:
            return False
        now = float(now)
        if (not force and
                now + 1.0e-9 < self._next_fallen_tree_foliage_refresh):
            return False
        self._next_fallen_tree_foliage_refresh = (
            now + FALLEN_TREE_FOLIAGE_REFRESH_SECONDS)
        body_identities = self._fallen_tree_body_identities()
        manager = getattr(
            getattr(self._runtime, 'area_destructibles', None),
            'g_destructiblesManager', None)
        force_no_animation = bool(getattr(
            manager, 'forceNoAnimation', False))
        waiting = getattr(
            manager, '_DestructiblesManager__destructiblesWaitDestroy', {})
        loaded = getattr(
            manager, '_DestructiblesManager__loadedChunkIDs', None)
        changed = False
        for identity in pending:
            chunk_id, item_index = identity
            if (isinstance(waiting, dict) and
                    int(chunk_id) in waiting):
                self._fallen_tree_foliage_stable.pop(identity, None)
                continue
            if loaded is not None:
                try:
                    chunk_loaded = int(chunk_id) in loaded
                except Exception:
                    chunk_loaded = False
                if not chunk_loaded:
                    self._fallen_tree_foliage_stable.pop(identity, None)
                    continue
            if (body_identities is not None and
                    identity in self._fallen_tree_foliage_seen_bodies and
                    identity not in body_identities):
                settle(chunk_id, item_index)
                self._fallen_tree_foliage_seen_bodies.discard(identity)
                self._fallen_tree_foliage_stable.pop(identity, None)
                continue
            try:
                pose = self._native_fallen_tree_pose(
                    chunk_id, item_index)
            except Exception:
                # A canonical snapshot can precede this chunk's native stream.
                # Keep the dormant profile pending and retry after it loads.
                continue
            if pose is None:
                continue
            changed = bool(update(
                chunk_id, item_index, pose[0], pose[1])) or changed
            signature = tuple(round(value, 5) for value in (
                tuple(pose[0]) +
                tuple(component for axis in pose[1]
                      for component in axis)))
            if (body_identities is not None and
                    identity in body_identities):
                self._fallen_tree_foliage_seen_bodies.add(identity)
                self._fallen_tree_foliage_stable.pop(identity, None)
                continue
            if (identity in self._fallen_tree_foliage_seen_bodies or
                    force_no_animation):
                settle(chunk_id, item_index)
                self._fallen_tree_foliage_seen_bodies.discard(identity)
                self._fallen_tree_foliage_stable.pop(identity, None)
                continue
            previous = self._fallen_tree_foliage_stable.get(identity)
            stable_reads = (
                previous[1] + 1
                if previous is not None and previous[0] == signature else 1)
            self._fallen_tree_foliage_stable[identity] = (
                signature, stable_reads)
            if stable_reads >= FALLEN_TREE_FOLIAGE_STABLE_READS:
                settle(chunk_id, item_index)
                self._fallen_tree_foliage_stable.pop(identity, None)
        return changed

    def _spot_line_of_sight(self, observer, target, target_descriptor,
                            target_moving=False, fired_recently=False,
                            target_still_seconds=0.0,
                            target_effective=None):
        (observer_position, observer_descriptor, observer_entity,
         observer_still_seconds, observer_is_local) = _spotting_observer(
            observer)
        distance = _distance_2d(observer_position, target)
        if distance <= spotting.PROXIMITY_SPOT_DISTANCE:
            return True
        if distance > spotting.MAX_SPOT_DISTANCE:
            return False
        for target_height in (1.5, 2.2):
            segment = bot_planner.trimmed_sight_segment(
                observer_position, target, 2.5, target_height)
            if segment is None:
                has_line_of_sight = True
                break
            if not segment:
                continue
            start, end = segment
            hit = self._runtime.bigworld.wg_collideSegment(
                self._avatar.spaceID,
                self._vector(start), self._vector(end), 128)
            if hit is None:
                has_line_of_sight = True
                break
        else:
            has_line_of_sight = False
        if not has_line_of_sight:
            return False
        foliage_bonus = self._foliage_camouflage_bonus(
            observer_position, target, fired_recently)
        if target_effective is None:
            target_profile = self._spotting_profile(target_descriptor)
            base_invisibility = self._base_invisibility(
                target_descriptor, target_profile)
            shot_factor = self._shot_invisibility_factor(target_descriptor)
        else:
            target_effective = effective_params.canonical(target_effective)
            if target_effective is None:
                raise RuntimeError(
                    'target effective spotting parameters are unavailable')
            target_profile = target_effective['spotting']
            camouflage = target_effective['camouflage']
            base_invisibility = (
                camouflage['base_moving'], camouflage['base_still'])
            shot_factor = camouflage['shot_factor']
        additive, multiplier = self._invisibility_aspect(
            target_profile, target_moving,
            loadout_law.still_device_active(
                target_still_seconds,
                target_profile['camouflage_net_delay']))
        camouflage = spotting.effective_camouflage(
            base_invisibility,
            moving=target_moving, additive=additive, multiplier=multiplier,
            shot_factor=shot_factor,
            fired_recently=fired_recently,
            foliage_bonus=foliage_bonus)
        return spotting.is_detected(
            distance, self._vision_radius(
                observer_descriptor, observer_entity,
                still_seconds=observer_still_seconds,
                local=observer_is_local), camouflage,
            has_line_of_sight=True)

    def _spotting_observers(self):
        """Return only this client's direct human observer.

        Friendly bots are evaluated once by the elected authority, and every
        other human publishes that client's own direct spotted set.  Their
        server-merged radio view arrives through ``on_bot_observation``;
        tracing all friendly vehicles again here multiplied the same native
        LOS work on every connected client.
        """
        local_entity = None
        if self._server is not None:
            local_entity = self._server_entity(self._server.vehicle_id)
        if local_entity is None:
            raise RuntimeError('local spotting observer is unavailable')
        local_record = self._records.get(
            'player:%s' % self.client.player_id)
        direct_observer = None
        if self._record_alive(local_record or {}, local_entity):
            now = self._clock()
            if abs(self._local_speed) > spotting.MOVING_SPEED_EPSILON:
                self._local_still_since = None
            elif self._local_still_since is None:
                self._local_still_since = now
            still_seconds = (
                0.0 if self._local_still_since is None
                else max(0.0, now - self._local_still_since))
            direct_observer = (
                self._local_position, self._local_descriptor, local_entity,
                still_seconds, True)
            self._publish_local_vision_state(local_entity, still_seconds)
        return (direct_observer,)

    def _publish_local_vision_state(self, entity, still_seconds):
        """Publish the player's live view range and still-device state.

        Retail feeds both of these from the cell: ``syncVehicleAttrs`` carries
        the effective ``circularVisionRadius`` that the minimap view circle
        draws, and ``updateVehicleOptionalDeviceStatus`` lights one optional
        device slot in the consumables panel.  Both are presentation only, so
        a stock panel failure disables the feed instead of ending the round.
        """
        descriptor = self._local_descriptor
        if (self._vision_feed_failed or self._avatar is None or
                descriptor is None):
            return False
        try:
            radius = self._vision_radius(
                descriptor, entity=entity, still_seconds=still_seconds,
                local=True)
            if (self._published_vision_radius is None or
                    abs(radius - self._published_vision_radius) >
                    VISION_PUBLISH_EPSILON):
                self._avatar.syncVehicleAttrs(
                    {'circularVisionRadius': radius})
                self._published_vision_radius = radius
            self._publish_optional_devices(descriptor, still_seconds)
        except Exception as error:
            self._vision_feed_failed = True
            self._warn_optional_failure('battle HUD vision feed', error)
            return False
        return True

    def _publish_optional_devices(self, descriptor, still_seconds):
        """Light the mounted stationary optional devices in the battle panel.

        #1513 announces a device on its first status and only updates it
        afterwards, and ``ConsumablesPanel.__genNextIdx`` asserts once the two
        optional-device slots are taken.  Only ``camouflageNet`` and
        ``stereoscope`` carry ``activateWhenStillSec``, so publishing exactly
        those matches retail and cannot exhaust the panel.
        """
        update = getattr(
            self._avatar, 'updateVehicleOptionalDeviceStatus', None)
        if not callable(update):
            return False
        vehicle_id = self._avatar.playerVehicleID
        for device in (_field(descriptor, 'optionalDevices', ()) or ()):
            identity = getattr(device, 'id', None)
            if not isinstance(identity, tuple) or len(identity) != 2:
                continue
            delay = _number(getattr(device, 'activateWhenStillSec', 0.0))
            if delay <= 0.0:
                continue
            device_id = int(identity[1])
            active = loadout_law.still_device_active(still_seconds, delay)
            if self._published_still_devices.get(device_id) is active:
                continue
            self._published_still_devices[device_id] = active
            update(vehicle_id, device_id, active)
        return True

    def _record_still_seconds(self, record):
        """How long this record has been stationary, for the still devices."""
        state = record.get('state') or {}
        now = self._clock()
        if abs(_number(state.get('speed'))) > spotting.MOVING_SPEED_EPSILON:
            record['still_since'] = None
            return 0.0
        since = record.get('still_since')
        if since is None:
            record['still_since'] = now
            return 0.0
        return max(0.0, now - float(since))

    @staticmethod
    def _spotting_probe_phase(record):
        """Spread native LOS work across the five 0.10-second frames."""
        identity = record.get('network_id')
        if identity is None:
            identity = record.get('engine_id', 0)
        try:
            identity = abs(int(identity))
        except (TypeError, ValueError, OverflowError):
            identity = 0
        return ((identity * 17) % SPOTTING_PHASE_BUCKETS) * \
            SPOTTING_UPDATE_SECONDS

    @classmethod
    def _spotting_probe_due(cls, record, now):
        """Retain one 2 Hz deadline without synchronising late frames."""
        deadline = float(record.get('spot_next', 0.0) or 0.0)
        if deadline <= 0.0:
            cycle = math.floor(float(now) / SPOTTING_PROBE_SECONDS)
            deadline = (cycle * SPOTTING_PROBE_SECONDS +
                        cls._spotting_probe_phase(record))
            if deadline + 1e-9 < float(now):
                deadline += SPOTTING_PROBE_SECONDS
            record['spot_next'] = deadline
        if float(now) + 1e-9 < deadline:
            return False
        elapsed = max(0.0, float(now) - deadline)
        intervals = int(math.floor(
            (elapsed + 1e-9) / SPOTTING_PROBE_SECONDS)) + 1
        record['spot_next'] = (
            deadline + intervals * SPOTTING_PROBE_SECONDS)
        return True

    def _update_spotting(self, now, hud_only=False):
        """Refresh the local vision HUD, then apply live spotting when allowed."""
        if now < self._next_spotting_time:
            return False
        if self._next_spotting_time <= 0.0:
            self._next_spotting_time = now
        elapsed = max(0.0, float(now) - self._next_spotting_time)
        intervals = int(math.floor(
            (elapsed + 1e-9) / SPOTTING_UPDATE_SECONDS)) + 1
        self._next_spotting_time += intervals * SPOTTING_UPDATE_SECONDS
        observers = self._spotting_observers()
        if hud_only:
            return False
        changed = False
        spotted_records = []
        local_team = int(self.client.team)
        for record in self._records.values():
            state = record.get('state') or {}
            if (record.get('local') or not record.get('presentation') or
                    not record.get('ready') or record.get('tombstone')):
                continue
            entity = self._server_entity(record['engine_id'])
            if entity is None:
                continue
            if int(state.get('team', 0)) == local_team:
                # Synthetic allies never leave BigWorld.entities, so enforce
                # the stock #1513 vehicle AOI here as well.  Team knowledge
                # remains permanent: only the world model and 3D marker leave
                # at 565 m, while the minimap entry stays available.
                previous = (
                    bool(record.get('spot_visible', True)),
                    bool(record.get(
                        'spot_marker_visible',
                        record.get('spot_visible', True))))
                visible, marker_visible = self._apply_spot_presentation(
                    record, entity, True)
                if (visible, marker_visible) != previous:
                    changed = True
                continue
            alive = self._record_alive(record, entity)
            direct_seen = False
            if alive and self._spotting_probe_due(record, now):
                target_effective = (
                    self._player_effective_snapshot(state)
                    if record.get('kind') == 'player' else None)
                target = _xyz(entity.position)
                target_moving = abs(_number(
                    state.get('speed'), self._local_speed
                    if record.get('local') else 0.0)) > (
                        spotting.MOVING_SPEED_EPSILON)
                fired_recently = now < float(
                    record.get('shot_penalty_until', 0.0))
                target_still = self._record_still_seconds(record)
                direct_seen = (
                    observers[0] is not None and
                    self._spot_line_of_sight(
                        observers[0], target, entity.typeDescriptor,
                        target_moving, fired_recently,
                        target_still_seconds=target_still,
                        target_effective=target_effective))
                seen = direct_seen or any(self._spot_line_of_sight(
                    observer, target, entity.typeDescriptor,
                    target_moving, fired_recently,
                    target_still_seconds=target_still,
                    target_effective=target_effective)
                    for observer in observers[1:])
                # A direct LOS sample owns the answer until this record's next
                # staggered sample.  Publishing only on the one 0.10-second
                # update that happened to execute the 0.50-second probe made
                # the server see a false empty report on the following frame.
                record['direct_spot_visible'] = bool(direct_seen)
                if seen:
                    record['spot_until'] = (
                        now + spotting.SPOT_MEMORY_SECONDS)
            elif not alive:
                record['direct_spot_visible'] = False
            # A destroyed vehicle stops earning new spots but keeps the memory
            # it already has, so its marker survives long enough to show the
            # destroyed style instead of vanishing the frame it dies.
            remembered = now < max(
                float(record.get('spot_until', 0.0)),
                float(record.get('radio_spot_until', 0.0)))
            # Team memory owns the marker.  The ordinary 565 m vehicle AOI
            # owns the model except while an SPG is using strategic view.
            previous = (
                bool(record.get('spot_visible', False)),
                bool(record.get(
                    'spot_marker_visible',
                    record.get('spot_visible', False))))
            visible, marker_visible = self._apply_spot_presentation(
                record, entity, remembered)
            if (visible, marker_visible) != previous:
                changed = True
            if visible and not previous[0] and direct_seen:
                self._run_optional_feature(
                    'spotting feedback', self._present_direct_spot,
                    (record,))
            if visible and bool(record.get('direct_spot_visible', False)):
                spotted_records.append(record)
        self._publish_spotted_targets(spotted_records)
        return changed

    def _publish_spotted_targets(self, records):
        """Retain local presentation state without publishing a verdict."""
        targets = []
        for record in records:
            kind = record.get('kind')
            actor = record.get('network_id')
            if kind not in ('player', 'bot') or actor is None:
                continue
            targets.append(
                {'target_kind': kind, 'target_id': int(actor)})
        signature = tuple(sorted(
            (entry['target_kind'], entry['target_id']) for entry in targets))
        if signature == self._spotted_signature:
            return False
        self._spotted_signature = signature
        return False

    def _release_target_lock(self, engine_id):
        """Drop a lock on a vehicle that just died, before it is re-presented."""
        release = getattr(
            self._runtime.compatibility, 'release_target_lock', None)
        if not callable(release) or self._avatar is None:
            return False
        return bool(release(self._avatar, engine_id))

    def _present_vehicle_dead(self, record, immediate):
        """Mirror ``Vehicle.__onVehicleDeath`` so the marker takes its dead
        style.  ``immediate`` is False for a vehicle that just died and True
        for one that was already dead when its visual started."""
        if (record.get('local') or record.get('native_remote') or
                not record.get('presentation')):
            return False
        provider = getattr(self._avatar, 'guiSessionProvider', None)
        shared = getattr(provider, 'shared', None)
        feedback = getattr(shared, 'feedback', None)
        if feedback is None:
            return False
        set_state = getattr(feedback, 'setVehicleState', None)
        if not callable(set_state):
            raise RuntimeError(
                '#1513 vehicle-marker death boundary is unavailable')
        set_state(int(record['engine_id']),
                  self._runtime.feedback_event_id.VEHICLE_DEAD,
                  bool(immediate))
        return True

    def _stop_remote_visual(self, record):
        if self._outlined_engine_id == record.get('engine_id'):
            self._clear_target_outline()
        world_started, minimap_started = \
            self._remote_visual_components(record)
        if not world_started and not minimap_started:
            return False
        entity_id = int(record['engine_id'])
        if world_started and minimap_started:
            self._binding.stop_vehicle_visual(entity_id, False)
        elif world_started:
            self._binding.stop_vehicle_visual(entity_id, False)
        else:
            self._binding.stop_vehicle_minimap(entity_id)
        self._store_remote_visual_components(record, False, False)
        if record.get('native_remote'):
            vehicle = self._remote_factory.get(entity_id)
            if vehicle is not None:
                vehicle._offlineNativeMarkerVisible = False
        return True

    def _finalize_dead_remote_wreck(self, record, entity):
        """Expose one finalized corpse without re-entering spotting.

        Native model replacement may finish after the health callback. Store
        the desired draw state on the entity first so the model-changed hook
        applies the same visible wreck state when that late callback arrives.
        """
        record.pop('wreck_known', None)
        record.pop('deferred_health_presentation', None)
        factory = self._remote_factory
        if factory is None:
            return False
        getter = getattr(factory, 'get', None)
        if not callable(getter):
            return False
        vehicle = getter(record['engine_id']) or entity
        if vehicle is None:
            return False
        if record.get('native_remote'):
            vehicle._offlineNativeDrawVisible = True
            vehicle.targetCaps = []
        if getattr(vehicle, 'model', None) is None:
            record.pop('_spot_presentation_signature', None)
            return False
        try:
            return self._set_record_spot_visibility(
                record, bool(record.get('spot_visible', False)),
                bool(record.get(
                    'spot_marker_visible',
                    record.get('spot_visible', False))))
        except Exception as error:
            # The durable death is already committed. A model/marker failure
            # may degrade one presentation edge, but must never abort a round.
            record.pop('_spot_presentation_signature', None)
            self._warn_optional_failure(
                'dead wreck presentation', error, disable=False)
            return False

    def _apply_health(self, record, state, attacker_id=0, reason_id=None,
                      force_cause=False, attack_reason_id=None,
                      suppress_combat_presentation=False):
        if 'health' not in state:
            return
        requested_presentation_suppression = bool(
            suppress_combat_presentation and not record.get('local'))
        health = max(0, int(state.get('health', 0)))
        if reason_id is None:
            reason_id = max(0, int(state.get('death_reason', 0) or 0))
        else:
            reason_id = max(0, int(reason_id))
        if attack_reason_id is None:
            attack_reason_id = reason_id
        else:
            attack_reason_id = max(0, int(attack_reason_id))
        engine_id = record['engine_id']
        display_health = max(
            0, int(state.get('display_health', health) or 0))
        crew_active = bool(state.get('alive', health > 0)) and health > 0
        dead = health <= 0 or not crew_active
        crew_knockout = health > 0 and not crew_active
        # Blind non-lethal hits stay private, but death is public authority:
        # native shutdown, the wreck, the kill and statistics form one edge.
        suppress_combat_presentation = bool(
            requested_presentation_suppression and not dead)
        # ``attacker_id`` and ``attack_reason_id`` are one-shot presentation
        # causes, not snapshot state.  Ordered combat events force their
        # native notification; a following cause-free snapshot must not look
        # like a new health transition and overwrite FROM_PLAYER colouring.
        signature = (
            health, display_health, crew_active, int(reason_id))
        previous_signature = self._last_health.get(engine_id)
        durable_changed = previous_signature != signature
        previous_dead = bool(
            previous_signature is not None and
            (previous_signature[0] <= 0 or not previous_signature[2]))
        if previous_dead and dead:
            # Replayed combat events and late snapshots may repeat a terminal
            # state. Keep the durable signature current without replaying
            # native death callbacks, effects, markers or kill notifications.
            self._last_health[engine_id] = signature
            return
        if not durable_changed and not force_cause:
            return
        entity = self._server_entity(engine_id)
        if entity is None:
            return
        self._last_health[engine_id] = signature
        previous = getattr(entity, 'health', health)
        if (dead and not previous_dead and
                self._outlined_engine_id == engine_id):
            # Exact #1513's native onHealthChanged/set_isCrewActive death
            # path may replace the CompoundAppearance model.  EdgeDrawer keys
            # the outline by that compound, so release it before either stock
            # callback can retire the key.
            self._clear_target_outline()
        if dead and not previous_dead and not crew_knockout:
            fire_reason = self._attack_reason('FIRE', 1)
            if reason_id == fire_reason:
                death_cause = 'fire'
            elif reason_id == self._attack_reason('RAM', 2):
                death_cause = 'ramming'
            elif reason_id == self._attack_reason('WORLD_COLLISION', 3):
                death_cause = 'world_collision'
            elif reason_id == self._attack_reason('DROWNING', 5):
                death_cause = 'drowning'
            elif reason_id == self._attack_reason('OVERTURN', 7):
                death_cause = 'overturn'
            else:
                death_cause = 'shot'
            death_payload = critical_damage.apply_death(
                entity, death_cause)
            if death_payload is not None:
                canonical = self._critical_state(death_payload)
                state['critical'] = canonical
                record['critical_state'] = canonical
                record['state'] = state
                # #1513's native health transition owns the terminal damage
                # panel state (DESTROYED or CREW_DEACTIVATED).  The canonical
                # all-module/all-crew payload is durable authority state, not a
                # burst of new device-hit notifications; replaying it through
                # showVehicleDamageInfo feeds terminal device updates outside
                # the stock death-panel lifecycle and Flash rejects the call.
                # Stop the native fire extra, but leave the death HUD to the
                # stock Vehicle/PlayerAvatar consumer below.
                if (not self._worker_mode and
                        not suppress_combat_presentation):
                    self._sync_fire_effect(entity)
                if record.get('local'):
                    self._queue_local_damage_report(
                        critical=death_payload,
                        attribute_attacker=death_cause not in (
                            'drowning', 'world_collision', 'overturn'))
        preserve_inactive_hull = dead and display_health > 0
        native_health = display_health if preserve_inactive_hull else health
        if self._worker_mode:
            entity.health = native_health
            notifier = getattr(entity, 'set_health', None)
            if callable(notifier):
                notifier(previous)
            previous_crew_active = getattr(
                entity, 'isCrewActive', crew_active)
            entity.isCrewActive = crew_active
            crew_notifier = getattr(entity, 'set_isCrewActive', None)
            if callable(crew_notifier):
                crew_notifier(previous_crew_active)
            retain_wreck = getattr(entity, 'retain_wreck_model', None)
            if (record.get('presentation') and dead and
                    callable(retain_wreck)):
                # The worker never draws this compound, but finalizing it
                # still stops live extras and native track motion without
                # loading a second model.
                retain_wreck()
            return
        entity.health = native_health
        health_changed = getattr(entity, 'onHealthChanged', None)
        if (not suppress_combat_presentation and
                callable(health_changed)):
            health_changed(
                native_health, int(attacker_id), int(attack_reason_id))
        elif not record.get('native_remote'):
            # Synthetic remotes need their local alive bit refreshed.  A
            # stock Vehicle property callback is deliberately skipped here:
            # it owns the same death/effect presentation we are suppressing.
            notifier = getattr(entity, 'set_health', None)
            if callable(notifier):
                notifier(previous)
        previous_crew_active = getattr(entity, 'isCrewActive', crew_active)
        entity.isCrewActive = crew_active
        if (previous_crew_active != crew_active and
                (not suppress_combat_presentation or
                 not record.get('native_remote'))):
            crew_notifier = getattr(entity, 'set_isCrewActive', None)
            if callable(crew_notifier):
                crew_notifier(previous_crew_active)
        if (record.get('presentation') and
                not suppress_combat_presentation):
            # Exact #1513's ``set_isCrewActive`` republishes remote health
            # without an attacker.  Keep the causal update last so a local
            # hit or kill remains classified as FROM_PLAYER.
            provider = getattr(self._avatar, 'guiSessionProvider', None)
            present_health = getattr(provider, 'setVehicleHealth', None)
            if not callable(present_health):
                raise RuntimeError(
                    '#1513 remote vehicle health presenter is unavailable')
            present_health(
                False, engine_id, native_health,
                int(attacker_id), int(attack_reason_id))
            record.pop('deferred_health_presentation', None)
        elif record.get('presentation'):
            # Reconcile the current bar without an attacker cause if this
            # target is spotted later.  Until then, no floating damage text is
            # allowed to disclose the blind hit.
            record['deferred_health_presentation'] = True
        # Vehicle.onHealthChanged and Vehicle.set_isCrewActive both reach
        # __onVehicleDeath from the synced entity properties, after the health
        # presentation.
        entity_alive = getattr(entity, 'isAlive', None)
        if not (entity_alive() if callable(entity_alive) else entity_alive):
            # A dead vehicle cannot remain the live target even though its
            # existing compound stays in place as wreck cover.
            if self._outlined_engine_id == engine_id:
                self._clear_target_outline()
            if not suppress_combat_presentation:
                self._release_target_lock(engine_id)
                self._present_vehicle_dead(record, False)
        if record.get('local'):
            if dead:
                self._local_speed = 0.0
                if self._sender is not None:
                    self._sender.forward = 0.0
                    self._sender.turn = 0.0
            self._avatar.updateVehicleHealth(
                engine_id, display_health, int(reason_id),
                crew_active, False)
        if not previous_dead and dead:
            if not suppress_combat_presentation:
                killed = getattr(self._binding, 'arena_vehicle_killed', None)
                if callable(killed):
                    killed(engine_id, int(attacker_id), int(reason_id))
                if not record.get('local'):
                    self._fallback_postmortem_viewpoint(engine_id)
        if (not previous_dead and dead and record.get('presentation') and
                self._remote_factory is not None):
            try:
                self._remote_factory.request_wreck(engine_id)
            except Exception as error:
                self._warn_optional_failure(
                    'dead wreck replacement', error, disable=False)
            self._finalize_dead_remote_wreck(record, entity)

    def _destroy_entity(self, event):
        record = self._records.get(event.get('entity'))
        if record is not None and record.get('local'):
            state = dict(record.get('state') or {})
            state.update(event.get('state') or {})
            state['health'] = 0
            state['alive'] = False
            record['state'] = state
            self._materialize_record(record)
            return
        if record is None:
            key = event.get('entity')
            pending = self._pending_bot_creates.get(key)
            if pending is not None and event.get('keep_corpse'):
                state = dict(pending.get('state') or {})
                state.update(event.get('state') or {})
                state['health'] = 0
                state['alive'] = False
                pending['state'] = state
                return
            if pending is not None:
                if self._pending_event_references(key):
                    raise RuntimeError(
                        'pending entity %s was removed before its ordered '
                        'event applied' % key)
                self._pending_bot_creates.pop(key, None)
                try:
                    self._pending_bot_create_order.remove(key)
                except ValueError:
                    pass
            return
        if not self._worker_mode:
            self._fallback_postmortem_viewpoint(record['engine_id'])
        if event.get('keep_corpse'):
            state = dict(record.get('state') or {})
            state.update(event.get('state') or {})
            state['health'] = 0
            state['alive'] = False
            record['state'] = state
            self._materialize_record(record)
            return
        if record.get('presentation'):
            if self._outlined_engine_id == record.get('engine_id'):
                self._clear_target_outline()
            self._records.pop(event.get('entity'), None)
            self._records_revision += 1
            if record.get('arena_added'):
                self._binding.arena_vehicle_removed(record['engine_id'])
            if self._remote_factory is not None:
                if not record.get('native_remote'):
                    self._stop_remote_visual(record)
                self._remote_factory.destroy(record['engine_id'])
            return
        if record.get('ready'):
            self._records.pop(event.get('entity'), None)
            self._records_revision += 1
            forget = getattr(self._server, 'forgetVehicleEnter', None)
            if callable(forget):
                forget(record['engine_id'])
            try:
                visible = self._server_entity(
                    record['engine_id']) is not None
            except ReferenceError:
                visible = False
            if visible:
                try:
                    self._binding.arena_vehicle_removed(record['engine_id'])
                finally:
                    self._binding.destroy_entity(record['engine_id'])
        else:
            # Never pass a pending id to BigWorld.destroyEntity.  The #1513
            # native registry does not own it yet; a second destroy after the
            # delayed onEnterWorld can cross the C++ boundary twice.  Keep a
            # tombstone and destroy exactly once when the id becomes visible.
            record['tombstone'] = True
            record.pop('pending_pose', None)
            try:
                visible = self._server_entity(
                    record['engine_id']) is not None
            except ReferenceError:
                visible = False
            record['visible_destroy_requested'] = False
            if visible:
                self._flush_tombstone(record)

    def _reject_local_fire(self, reason):
        state = self._gun_state
        remaining = (-1.0 if state is None else float(state.reload_time))
        sys.stdout.write(
            '[Offline LAN 0.9.22] LOCAL FIRE rejected reason=%s '
            'reload=%.3f\n' % (str(reason), remaining))
        return False

    def shoot(self, aim_yaw, gun_pitch):
        if (self.state != 'running' or not self._battle_live or
                self._battle_result is not None or
                self._drown_level == 2 or self._overturn_level == 2):
            return self._reject_local_fire('battle_not_live')
        if self._server is None:
            return self._reject_local_fire('server_unavailable')
        entity = self._server_entity(self._server.vehicle_id)
        if entity is None or entity.typeDescriptor is None:
            return self._reject_local_fire('vehicle_unavailable')
        if self._player_barrel_under_water(entity):
            return self._reject_local_fire('barrel_under_water')
        siege_states = self._runtime.constants.VEHICLE_SIEGE_STATE
        if getattr(entity, 'siegeState', siege_states.DISABLED) in (
                siege_states.SWITCHING_ON,
                siege_states.SWITCHING_OFF):
            return self._reject_local_fire('siege_switching')
        is_alive = getattr(entity, 'isAlive', None)
        if ((callable(is_alive) and not is_alive()) or
                (not callable(is_alive) and
                 (_number(getattr(entity, 'health', 0.0)) <= 0.0 or
                  not bool(getattr(entity, 'isCrewActive', True))))):
            return self._reject_local_fire('player_dead')
        if getattr(entity, 'is_gun_destroyed', False):
            return self._reject_local_fire('gun_destroyed')
        now = self._clock()
        # Close the 100 ms HUD/state race at the exact trigger edge.
        state = self._advance_local_gun_to(entity, now)
        if not state.can_fire(self._battle_live):
            return self._reject_local_fire('gun_not_ready')
        if isinstance(self._local_fire_intent, dict):
            return self._reject_local_fire('intent_pending')
        shell_index = state.shot_index
        sender = getattr(self.client, 'send_fire_intent', None)
        if not callable(sender) or self._sender is None:
            return self._reject_local_fire('sender_unavailable')
        # The tracking mailbox carries the desired target angle, while the
        # stock rotator may still be moving toward it.  Freeze the exact
        # native barrel angle visible at the trigger edge; the immediately
        # following input is what the server binds to this fire intent.
        rotator = getattr(self._avatar, 'gunRotator', None)
        try:
            turret_yaw = float(rotator.turretYaw)
            gun_pitch = float(rotator.gunPitch)
        except (AttributeError, TypeError, ValueError):
            return self._reject_local_fire('gun_angle_unavailable')
        unused_position, hull_yaw = self.local_pose()
        aim_yaw = float(hull_yaw) + turret_yaw
        self._sender.aim_yaw = aim_yaw
        self._sender.gun_pitch = gun_pitch
        try:
            shot_origin, shot_direction = self._mutable_shot_ray()
            dispersion_angle = self._native_dispersion_angle()
        except RuntimeError:
            return self._reject_local_fire('shot_ray_unavailable')
        if not self._sender.send_current():
            return self._reject_local_fire('input_send_failed')
        intent_seq = sender(
            shell_index, list(_xyz(shot_origin)),
            list(_xyz(shot_direction)), dispersion_angle)
        if not intent_seq:
            return self._reject_local_fire('intent_send_failed')
        self._local_fire_intent = {
            'intent_seq': int(intent_seq),
            'input_seq': int(getattr(self.client, '_input_seq', 0)),
            'sent_at': float(now),
        }
        return True

    def _resolve_hit(self, shot_seq, aim_yaw, gun_pitch, shell_index=None,
                     dispersion_angle=None):
        entity = self._server_entity(self._server.vehicle_id)
        if entity is None or entity.typeDescriptor is None:
            return
        start, direction = self._mutable_shot_ray()
        if self._gun_state is not None:
            if dispersion_angle is None:
                dispersion_angle = self._native_dispersion_angle()
            self._gun_state.scatter(
                direction,
                bool(self._config and self._config.get(
                    'perfect_accuracy', False)),
                dispersion_angle=dispersion_angle)
        end = start + direction.scale(5000.0)
        shot = self._descriptor_shot(entity.typeDescriptor, shell_index)
        target_record = None
        target_collisions = None
        distance = 999999.0
        for record in self._records.values():
            if record.get('local'):
                continue
            target = self._server_entity(record['engine_id'])
            if (target is None or not getattr(target, 'isStarted', False) or
                    not self._record_alive(record, target) or
                    (record.get('presentation') and
                     not bool(record.get('spot_visible', False)))):
                continue
            if record.get('native_remote'):
                result = collide_vehicle_at_matrix(
                    target, target.matrix, start, end,
                    self._runtime.math)
            else:
                result = target.collideSegmentExt(start, end)
            if not result:
                continue
            nearest = min(result, key=lambda item: float(item.dist))
            if nearest.dist < distance:
                distance = float(nearest.dist)
                target_record = record
                target_collisions = tuple(result)
        # Destructible submission mutates the native scene immediately.  First
        # resolve the nearest vehicle without applying damage, then cap the
        # world/destructible ray at that vehicle.  A prop behind the target
        # must not be destroyed before the existing world/vehicle ordering
        # chooses which surface the shell actually reaches.
        scene_end = end
        if target_record is not None and target_collisions is not None:
            scene_end = start + direction.scale(
                max(0.0, min(5000.0, distance)))
        scene = self._resolve_shot_scene(
            start, scene_end, direction, shot)
        penetration_factor = scene.get('penetration_factor')
        world_distance = scene['world_distance']
        if (target_record is None or target_collisions is None or
                scene.get('stopped_by_destructible') or
                distance > world_distance + _SHOT_OCCLUSION_EPSILON):
            if (combat_rules.is_he(shot) and
                    world_distance < 4999.5):
                self._he_splash(
                    start + direction.scale(world_distance), shot, shot_seq,
                    None, 'player', self.client.player_id,
                    self._server.vehicle_id)
            return
        if penetration_factor is None:
            penetration_factor = combat_rules.sample_penetration_factor()
        target = self._server_entity(target_record['engine_id'])
        target_collisions, trace_start, trace_end = self._vehicle_trace(
            shot, start, end, target_collisions)
        damage, result = self._shell_damage(
            entity.typeDescriptor, target_collisions, distance,
            shell_index=shell_index,
            pierce_loss=scene['piercing_loss'],
            penetration_factor=penetration_factor,
            target_descriptor=getattr(target, 'typeDescriptor', None))
        impact = start + direction.scale(distance)
        hull_damage = damage
        self._install_critical_equipment_effects(target_record, target)
        damage, critical, critical_delta = self._critical_hit(
            target, entity.typeDescriptor, target_collisions,
            trace_start, trace_end,
            damage, result, entity.id, shell_index,
            burst_position=impact, deadeye=self._has_deadeye)
        critical_contract = self._critical_proposal_contract(
            target_record, critical, hull_damage, critical_delta)
        if target_record.get('kind') == 'bot':
            self.client.send_bot_hit(
                target_record['network_id'], shot_seq, damage, result,
                _xyz(impact), critical, **critical_contract)
        else:
            self.client.send_hit(
                target_record['network_id'], shot_seq, damage, result,
                shell_index or 0,
                _xyz(impact), critical, **critical_contract)
        if combat_rules.is_he(shot):
            self._he_splash(
                impact, shot, shot_seq, target_record, 'player',
                self.client.player_id, self._server.vehicle_id)

    @staticmethod
    def _descriptor_shot(descriptor, shell_index=None):
        shots = tuple(descriptor.gun.shots or ())
        if shell_index is None:
            shell_index = getattr(descriptor, 'activeGunShotIndex', 0)
        index = max(0, min(int(shell_index), max(0, len(shots) - 1)))
        return shots[index] if shots else {}

    def _resolve_shot_scene(self, start, end, direction, shot,
                            penetration_factor=None,
                            initial_piercing_loss=0.0,
                            distance_offset=0.0,
                            projectile_state=None):
        """Traverse exact destructibles in order before the capped endpoint."""
        maximum = (end - start).length
        initial_piercing_loss = max(
            0.0, _number(initial_piercing_loss, 0.0))
        distance_offset = max(0.0, _number(distance_offset, 0.0))
        if self._destructibles is None:
            hit = self._runtime.bigworld.wg_collideSegment(
                self._avatar.spaceID, start, end, 128)
            return {
                'world_distance': ((hit[0] - start).length
                                   if hit is not None else 999999.0),
                'piercing_loss': initial_piercing_loss,
                'stopped_by_destructible': False,
                'penetration_factor': penetration_factor,
            }
        travelled = 0.0
        piercing_loss = initial_piercing_loss
        for unused_index in range(64):
            cursor = start + direction.scale(travelled)
            result = self._destructibles.shot_world_distance(
                self._runtime.bigworld, self._avatar.spaceID,
                cursor, end, direction, shot)
            if not isinstance(result, dict):
                raise RuntimeError(
                    '#1513 destructible shot result must be a dictionary')
            added_loss = max(0.0, _number(
                result.get('piercing_loss'), 0.0))
            piercing_loss += added_loss
            if added_loss > 0.0 and penetration_factor is None:
                penetration_factor = (
                    combat_rules.sample_penetration_factor())
            # Range falloff is evaluated where the shell enters/hits the
            # destructible.  ``continue_from`` is the proved OBB exit and is
            # used only to advance the next ray; using it here would charge a
            # thick object extra range before applying its fixed 25 mm loss.
            loss_distance = result.get('loss_distance')
            if loss_distance is None:
                loss_distance = result.get('continue_from')
            obstacle_distance = travelled + max(
                0.0, _number(loss_distance, 0.0))
            piercing_distance = distance_offset + obstacle_distance
            if projectile_state is not None:
                piercing_distance = projectile_range_distance(
                    projectile_state,
                    start + direction.scale(obstacle_distance))
            if (result.get('continue_from') is not None and
                    penetration_factor is not None and
                    combat_rules.sampled_piercing(
                        shot, piercing_distance,
                        penetration_factor,
                        piercing_loss) < 1.0):
                return {
                    'world_distance': obstacle_distance,
                    'piercing_loss': piercing_loss,
                    'stopped_by_destructible': True,
                    'penetration_factor': penetration_factor,
                }
            stop_distance = result.get('stop_distance')
            if stop_distance is not None:
                return {
                    'world_distance': travelled + max(
                        0.0, _number(stop_distance)),
                    'piercing_loss': piercing_loss,
                    'stopped_by_destructible': bool(
                        result.get('stopped_by_destructible')),
                    'penetration_factor': penetration_factor,
                }
            advance = result.get('continue_from')
            if advance is None:
                world_distance = _number(
                    result.get('world_distance'), 999999.0)
                return {
                    'world_distance': (travelled + world_distance
                                       if world_distance < 99999.0
                                       else 999999.0),
                    'piercing_loss': piercing_loss,
                    'stopped_by_destructible': False,
                    'penetration_factor': penetration_factor,
                }
            advance = _number(advance)
            if advance <= 0.0:
                raise RuntimeError(
                    '#1513 destructible shot traversal did not advance')
            travelled += advance
            if travelled >= maximum:
                return {'world_distance': 999999.0,
                        'piercing_loss': piercing_loss,
                        'stopped_by_destructible': False,
                        'penetration_factor': penetration_factor}
        raise RuntimeError('#1513 destructible shot traversal exceeded 64 hits')

    def _he_splash(self, burst_position, shot, shot_seq, direct_record,
                   attacker_kind, attacker_id, attacker_engine_id):
        """Port 0.8.2 `_offh_he_splash` through #1513 Vehicle rays."""
        radius = combat_rules.he_radius(shot)
        if radius <= 0.0 or burst_position is None:
            return 0
        hit_count = 0
        legacy_shell = combat_rules.legacy_shot(shot).get('shell') or {}
        for record in tuple(self._records.values()):
            if record is direct_record or record.get('tombstone'):
                continue
            if self._worker_mode and record.get('local'):
                continue
            target = self._server_entity(record['engine_id'])
            if (target is None or target.typeDescriptor is None or
                    not getattr(target, 'isStarted', False) or
                    _number(getattr(target, 'health', 0.0)) <= 0.0):
                continue
            position = _xyz(getattr(target, 'position', record.get('state', {})))
            dx = position[0] - burst_position.x
            dy = position[1] - burst_position.y
            dz = position[2] - burst_position.z
            distance = math.sqrt(dx * dx + dy * dy + dz * dz)
            if distance > radius:
                continue
            aim = self._vector((position[0], position[1] + 1.0,
                                position[2]))
            collisions = ()
            try:
                if record.get('native_remote'):
                    body_matrix, chassis_matrix = \
                        self._projectile_vehicle_matrices(record, target)
                    collisions = tuple(collide_vehicle_at_matrix(
                        target, body_matrix, burst_position, aim,
                        self._runtime.math,
                        chassis_matrix=chassis_matrix) or ())
                else:
                    collisions = tuple(
                        target.collideSegmentExt(burst_position, aim) or ())
                nominal = combat_rules.he_nominal_armor(
                    collisions, target.typeDescriptor)
            except Exception:
                collisions = ()
                nominal = combat_rules.he_hull_armor(target.typeDescriptor)
            damage = combat_rules.he_splash_damage(
                shot, nominal, distance / radius)
            if damage <= 0:
                continue
            hull_damage = damage
            self._install_critical_equipment_effects(record, target)
            damage, critical, critical_delta = (
                critical_damage.propose_explosion(
                    target, combat_rules.collision_layers(collisions),
                    burst_position, aim - burst_position, damage,
                    legacy_shell, attacker_engine_id, deadeye=False,
                    with_delta=True))
            critical = self._critical_with_crew_roster(target, critical)
            if self._send_splash_hit(
                    record, attacker_kind, attacker_id, shot_seq, damage,
                    hull_damage, burst_position, critical, critical_delta):
                hit_count += 1
        return hit_count

    def _send_splash_hit(self, target_record, attacker_kind, attacker_id,
                         shot_seq, damage, hull_damage, burst_position,
                         critical, critical_delta):
        impact = _xyz(burst_position)
        critical_contract = self._critical_proposal_contract(
            target_record, critical, hull_damage, critical_delta)
        if attacker_kind == 'player':
            if target_record.get('kind') == 'bot':
                return self.client.send_bot_hit(
                    target_record['network_id'], shot_seq, damage, 2,
                    impact, critical, splash=True, **critical_contract)
            return self.client.send_hit(
                target_record['network_id'], shot_seq, damage, 2,
                self._gun_state.shot_index if self._gun_state else 0,
                impact, critical, splash=True, **critical_contract)
        if target_record.get('kind') == 'bot':
            return self.client.send_bot_bot_hit(
                attacker_id, target_record['network_id'], shot_seq,
                damage, 2, impact, critical, splash=True,
                **critical_contract)
        return self.client.send_bot_human_hit(
            attacker_id, target_record['network_id'], shot_seq,
            damage, 2, impact, critical, splash=True,
            **critical_contract)

    def _critical_hit(self, target, source_descriptor, collisions,
                      start, end, damage, result, attacker_id,
                      shell_index=None, burst_position=None,
                      deadeye=False):
        """Adapt #1513 collision objects to the copied 0.8.2 crit loop."""
        if target is None or getattr(target, 'typeDescriptor', None) is None:
            return damage, None, None
        shots = tuple(source_descriptor.gun.shots or ())
        if shell_index is None:
            shell_index = getattr(source_descriptor, 'activeGunShotIndex', 0)
        index = max(0, min(int(shell_index), max(0, len(shots) - 1)))
        shot = shots[index] if shots else {}
        shell = (combat_rules.legacy_shot(shot).get('shell') or {})
        layers = combat_rules.collision_layers(collisions)
        if combat_rules.is_he(shot):
            damage, critical, critical_delta = (
                critical_damage.propose_explosion(
                    target, layers,
                    burst_position if burst_position is not None else start,
                    end - start, damage, shell, attacker_id,
                    deadeye=deadeye, with_delta=True))
        else:
            damage, critical, critical_delta = critical_damage.propose_direct(
                target, layers, start, end, damage, shell, attacker_id,
                penetrated=int(result) == 2, deadeye=deadeye,
                with_delta=True)
        return (damage, self._critical_with_crew_roster(target, critical),
                critical_delta)

    @staticmethod
    def _descriptor_crew_roster(descriptor):
        """Return #1513 crew health-instance names without a fallback crew.

        ``VehicleDescr.type.crewRoles`` has one role tuple per physical
        crewman.  The client health extras number only gunner, loader and
        radioman instances; commander and driver keep their bare names.  A
        missing descriptor must stay unknown here: the generic fallback used
        for cosmetic critical effects is not evidence that every real crewman
        is knocked out.
        """
        roles = getattr(getattr(descriptor, 'type', None), 'crewRoles', None)
        if not isinstance(roles, (list, tuple)) or not roles:
            return ()
        counters = {'gunner': 1, 'loader': 1, 'radioman': 1}
        allowed = frozenset(
            ('commander', 'driver', 'gunner', 'loader', 'radioman'))
        roster = []
        for crewman_roles in roles:
            if (not isinstance(crewman_roles, (list, tuple)) or
                    not crewman_roles):
                return ()
            main_role = str(crewman_roles[0])
            if main_role not in allowed:
                return ()
            if main_role in counters:
                name = main_role + str(counters[main_role])
                counters[main_role] += 1
            else:
                name = main_role
            if name in roster:
                return ()
            roster.append(name)
        return tuple(roster)

    @classmethod
    def _critical_with_crew_roster(cls, target, critical):
        """Bind a critical proposal to the target's exact physical crew."""
        if not isinstance(critical, dict):
            return critical
        roster = cls._descriptor_crew_roster(
            getattr(target, 'typeDescriptor', None))
        if not roster:
            return critical
        result = dict(critical)
        result['crew_roster'] = list(roster)
        return result

    def _shell_damage(self, descriptor, collisions, distance,
                      shell_index=None, pierce_loss=0.0,
                      penetration_factor=None, target_descriptor=None):
        shots = tuple(descriptor.gun.shots or ())
        if shell_index is None:
            shell_index = getattr(descriptor, 'activeGunShotIndex', 0)
        index = max(0, min(int(shell_index),
                           max(0, len(shots) - 1)))
        shot = shots[index] if shots else {}
        contact = combat_rules.resolve_armor_contact(
            shot, distance, collisions, pierce_loss=pierce_loss,
            penetration_factor=penetration_factor)
        # A trace with no terminal contact remains a non-penetration. A
        # preserved external contact may instead carry the exact ricochet
        # result, and HE still detonates on the part it reached.
        result = 1 if contact is None else contact['result']
        armor = combat_rules.he_nominal_armor(collisions, target_descriptor)
        return combat_rules.damage(shot, result, armor), result

    def _defer_avatar_leave(self):
        """Finish the native leaveArena stack before retiring its Avatar."""
        generation = self._generation
        server = self._server
        on_local_leave = self._on_local_leave

        # Stock starts constructing the Hangar before the next BigWorld
        # callback runs.  Stop every battle callback and release the native
        # Vehicle presentation while its Avatar, GUI and space are still the
        # active owners.  Full Avatar/space retirement must remain deferred so
        # it never destroys the mailbox which is executing leaveArena().
        self.state = 'leaving'
        self._battle_live = False
        self._prebattle_deadline = None
        self._cancel_callbacks()
        try:
            self._quiesce_native_presentations()
        except Exception as error:
            # A recoverable presentation cleanup failure must not escape the
            # fake mailbox and cancel the deferred Avatar retirement.  Native
            # access violations still terminate at their exact call site;
            # Python failures remain visible in the client log.
            sys.stdout.write(
                '[Offline LAN 0.9.22] leave presentation cleanup failed: '
                '%s\n' % error)

        def leave_after_mailbox_returns():
            if (generation == self._generation and
                    server is self._server):
                if callable(on_local_leave):
                    on_local_leave()
                else:
                    self.stop(show_login=False)

        self._runtime.bigworld.callback(0.0, leave_after_mailbox_returns)

    def stop(self, show_login=False, restore_account=True):
        if self.state in ('idle', 'stopped'):
            if not restore_account and self._lobby_restore_token is not None:
                self._cancel_deferred_lobby_restore()
            return
        if self.state == 'failed' and self._lobby_restore_token is not None:
            # _fail() has already performed cleanup.  Preserve its one queued
            # restore instead of tearing the same native owners down again;
            # global shutdown can still cancel that pending reconstruction.
            if not restore_account:
                self._cancel_deferred_lobby_restore()
            self.state = 'stopped'
            return
        self._generation += 1
        self._cancel_callbacks()
        self._retain_native_teardown_owners()
        cleanup_error = None
        try:
            self._cleanup()
        except Exception as error:
            cleanup_error = error
        if cleanup_error is not None:
            self.state = 'stopped'
            self._retired_native_owners = []
            raise cleanup_error
        self.state = 'stopped'
        if restore_account:
            # Do not construct Account/Hangar on the callback which just
            # destroyed battle GUI, Avatar entities and their client space.
            # #1513 drains native GUI/material updates at its callback/frame
            # boundary; crossing one callback prevents the new Hangar from
            # reusing a SimpleGUIComponent still referenced by the old render
            # queue.  Keep the Python owners alive through that hand-off too.
            self._schedule_lobby_restore()
        else:
            self._retired_native_owners = []

    def _native_teardown_owners(self):
        """Snapshot Python owners whose native retirement may finish later."""
        owners = []
        for value in (
                self._avatar, self._binding, self._server,
                self._remote_factory, self._local_model, self._local_matrix,
                self._outlined_entity, self._outlined_vehicle,
                self._outlined_model, self._projectiles, self._sixth_sense,
                self._destructibles):
            if value is not None:
                owners.append(value)
        if self._records:
            owners.append(tuple(self._records.values()))
        factory = self._remote_factory
        if factory is not None:
            # Both presentation factories remove these entries immediately
            # after destroyEntity().  Retain the exact wrappers/descriptors
            # until the engine has crossed its native update boundary.
            for name in ('_vehicles', '_states', '_descriptors',
                         '_hit_testers'):
                values = getattr(factory, name, None)
                if isinstance(values, dict) and values:
                    owners.append(tuple(values.values()))
        return owners

    def _retain_native_teardown_owners(self):
        owners = self._native_teardown_owners()
        if owners:
            self._retired_native_owners.extend(owners)
        return len(owners)

    def _schedule_lobby_restore(self, on_complete=None):
        token = object()
        self._lobby_restore_token = token
        sys.stdout.write(
            '[Offline LAN 0.9.22] battle teardown complete; deferring '
            'lobby Account restore\n')

        def restore_after_native_boundary():
            if self._lobby_restore_token is not token:
                return
            self._lobby_restore_token = None
            self._lobby_restore_callback_id = None
            lobby_restored = False
            try:
                # A LAN transport failure is not a WoT account disconnect.
                # Account.showGUI owns the native showLobby transition; do not
                # call g_appLoader separately or duplicate it.
                self._runtime.compatibility.restore_lobby_account()
            except Exception as error:
                restore_error = 'lobby restore failed: %s' % error
                if self.error:
                    self.error = '%s; %s' % (self.error, restore_error)
                else:
                    self.error = restore_error
                sys.stdout.write(
                    '[Offline LAN 0.9.22] deferred lobby restore failed: '
                    '%s\n' % error)
                try:
                    self._runtime.compatibility.disconnect()
                except Exception as disconnect_error:
                    sys.stdout.write(
                        '[Offline LAN 0.9.22] offline disconnect after lobby '
                        'restore failure failed: %s\n' % disconnect_error)
            else:
                lobby_restored = True
                sys.stdout.write(
                    '[Offline LAN 0.9.22] deferred lobby Account restored\n')
            self._retired_native_owners = []
            if callable(on_complete):
                try:
                    on_complete(lobby_restored)
                except Exception as error:
                    # Failure reporting is downstream of native recovery and
                    # must never escape into BigWorld's callback dispatcher.
                    sys.stdout.write(
                        '[Offline LAN 0.9.22] deferred lobby completion '
                        'failed: %s\n' % error)

        try:
            callback_id = self._runtime.bigworld.callback(
                0.0, restore_after_native_boundary)
        except Exception:
            self._lobby_restore_token = None
            self._retired_native_owners = []
            raise
        if self._lobby_restore_token is token:
            self._lobby_restore_callback_id = callback_id

    def _cancel_deferred_lobby_restore(self):
        callback_id = self._lobby_restore_callback_id
        self._lobby_restore_callback_id = None
        self._lobby_restore_token = None
        if callback_id is not None:
            try:
                self._runtime.bigworld.cancelCallback(callback_id)
            except Exception:
                pass
        self._retired_native_owners = []

    def _cancel_callbacks(self):
        self._callback_token = None
        self._ammo_callback_token = None
        for callback_id in (self._callback_id, self._ammo_callback_id):
            if callback_id is not None:
                try:
                    self._runtime.bigworld.cancelCallback(callback_id)
                except Exception:
                    pass
        self._callback_id = None
        self._ammo_callback_id = None

    def _quiesce_native_presentations(self):
        """Release battle-scoped native visuals before Hangar takes over."""
        cleanup_error = None
        try:
            self._release_postmortem_visibility()
        except Exception as error:
            cleanup_error = error
        if self._local_matrix is not None or self._local_model is not None:
            try:
                self._detach_local_presentation()
            except Exception as error:
                if cleanup_error is None:
                    cleanup_error = error
        # Remote presentations are separate native Vehicle entities.  Their
        # filters, track controllers and marker adaptors become unsafe as soon
        # as the Hangar app starts replacing the battle app, so close them at
        # the synchronous leaveArena boundary rather than during late Account
        # reconstruction.
        if self._remote_factory is not None:
            engine_active = self._remote_factory.engine_active()
            if engine_active:
                try:
                    self._clear_target_outline()
                except Exception as error:
                    if cleanup_error is None:
                        cleanup_error = error
            else:
                self._outlined_engine_id = None
                self._outlined_entity = None
                self._outlined_vehicle = None
                self._outlined_model = None
            if engine_active:
                for record in tuple(self._records.values()):
                    if not record.get('presentation'):
                        continue
                    try:
                        self._stop_remote_visual(record)
                    except Exception as error:
                        if cleanup_error is None:
                            cleanup_error = error
            try:
                self._remote_factory.destroy_all()
            except Exception as error:
                if cleanup_error is None:
                    cleanup_error = error
            self._remote_factory = None
        if cleanup_error is not None:
            raise cleanup_error

    def _cleanup(self):
        cleanup_error = None
        try:
            self._restore_native_ram_contact_hook()
        except Exception as error:
            cleanup_error = error
        self._native_ram_contact_proofs.clear()
        self._native_ram_contact_failures.clear()
        try:
            self._stop_authority_worker_probe('battle_cleanup')
        except Exception as error:
            cleanup_error = error
        try:
            self._remove_decal_probe()
        except Exception as error:
            cleanup_error = error
        if self._projectiles is not None:
            try:
                self._projectiles.reset(max(
                    self._projectiles.now, self._clock()))
            except Exception as error:
                cleanup_error = error
        self._projectiles = None
        self._projectile_meta = {}
        self._projectile_visual_meta = {}
        self._projectile_terminal_data = {}
        self._projectile_target_positions = {}
        self._projectile_position_history = []
        self._projectile_historic_pose_cache = None
        self._projectile_spatial_bins = None
        self._projectile_spatial_fallback_keys = frozenset()
        self._projectile_spatial_records_container = None
        self._projectile_spatial_records_revision = None
        self._projectile_spatial_records = {}
        self._projectile_spatial_order = {}
        self._projectile_spatial_floor = None
        self._projectile_spatial_ceiling = None
        self._projectile_lineage = set()
        if self._artillery is not None:
            self._artillery.reset()
        self._artillery = None
        try:
            self._runtime.compatibility.set_control_mode_listener(None)
        except Exception as error:
            cleanup_error = error
        if self._sixth_sense is not None:
            try:
                self._sixth_sense.reset()
            except Exception as error:
                cleanup_error = error
            self._sixth_sense = None
        try:
            self._quiesce_native_presentations()
        except Exception as error:
            if cleanup_error is None:
                cleanup_error = error
        self._descriptor_cache = {}
        self._prepared_vehicle_names = []
        self._unusable_vehicles_reported = set()
        self._records = {}
        self._records_revision += 1
        _release_layout_caches()
        if self._map_create_attempted:
            creator = self._runtime.offline_map_creator
            retained_space_id = getattr(
                creator, '_OfflineMapCreator__spaceId', 0)
            retained_mapping_id = getattr(
                creator, '_OfflineMapCreator__spaceMappingId', 0)
            try:
                self._runtime.compatibility.retire_current_player()
            except Exception as error:
                cleanup_error = error
            # Native retirement and stock map ownership are independent
            # cleanup boundaries.  A partial onBecomeNonPlayer failure must
            # not prevent OfflineMapCreator from releasing its entity, space,
            # mapping and camera ids.
            try:
                creator.destroy()
            except Exception as error:
                if cleanup_error is None:
                    cleanup_error = error
            space_error = self._release_retained_client_space(
                retained_space_id, retained_mapping_id)
            if space_error is not None and cleanup_error is None:
                cleanup_error = space_error
            player, player_error = self._read_engine_player()
            if player_error is not None and cleanup_error is None:
                cleanup_error = player_error
            if player is not None:
                # Exact OfflineMapCreator.destroy() catches its own teardown
                # exception and calls cancel(), losing the ids while a zombie
                # Avatar may remain.  Retry the engine-owned clear directly
                # and verify the ownership boundary before restoring Account.
                clear_error = self._force_clear_engine_player(
                    'stock map teardown retained the Avatar')
                if clear_error is not None and cleanup_error is None:
                    cleanup_error = clear_error
        elif self._lobby_retire_started:
            # HangarSpace.destroy() is itself a destructive boundary.  A
            # later failure in the engine-wide clear must not leave the old
            # Account alive: restore_lobby_account() would treat it as valid
            # and skip rebuilding the now-destroyed HangarSpace.
            cleanup_error = self._force_clear_engine_player(
                'lobby teardown retained the Account')
        try:
            self._runtime.compatibility.deactivate_map()
        except Exception as error:
            if cleanup_error is None:
                cleanup_error = error
        try:
            self._restore_battle_gui_guard()
        except Exception as error:
            if cleanup_error is None:
                cleanup_error = error
        if self._destructibles is not None:
            try:
                self._destructibles.set_event_sink(None)
                self._destructibles.reset()
                self._destructibles.set_catalog(None)
            except Exception as error:
                if cleanup_error is None:
                    cleanup_error = error
        self._map_create_attempted = False
        self._lobby_retire_started = False
        self._mouse_target_matrix = None
        self._outline_report = None
        self._outline_logged_report = None
        self._outlined_entity = None
        self._outlined_vehicle = None
        self._outlined_model = None
        self._outline_blocked = False
        self._edge_reports = 0
        self._target_reports = 0
        self._next_outline_report = 0.0
        self._next_compound_report = 0.0
        self._compound_reports = 0
        self._compound_report_signature = None
        self._avatar = None
        self._standard_space_visibility = None
        self._next_space_visibility_check = 0.0
        self._space_visibility_warning_reported = False
        self._binding = None
        self._server = None
        self._remote_factory = None
        self._sender = None
        self._sync = None
        self._bots = None
        self._next_bot_manifest_retry = 0.0
        self._bot_manifest_retry_deadline = 0.0
        self._bot_manifest_retry_identity = None
        self._worker_probe = None
        self._worker_probe_attempted = False
        self._worker_frame_callbacks = 0
        self._worker_probe_authority_callbacks = 0
        self._worker_probe_bot_generated = 0
        self._worker_probe_bot_enqueued = 0
        self._worker_probe_bot_send_failed = 0
        self._worker_probe_bot_count = 0
        self._worker_probe_simulation_caps = 0
        self._worker_probe_control_steps = 0
        self._worker_probe_catchup_callbacks = 0
        self._worker_probe_control_debt_callbacks = 0
        self._worker_probe_max_control_step = 0.0
        self._worker_probe_control_debt = 0.0
        self._worker_probe_max_control_debt = 0.0
        self._worker_probe_astar_budget_exhausted = 0
        self._worker_probe_astar_max_pending = 0
        self._authority_pose_writes = 0
        self._authority_pose_skips = 0
        self._authority_aim_writes = 0
        self._authority_aim_skips = 0
        if self._frame_diagnostics is not None:
            self._frame_diagnostics.reset()
        self._has_sixth_sense = False
        self._has_expert = False
        self._has_deadeye = False
        self._expert_visibility_enabled = False
        self._expert_target_id = 0
        self._expert_target_due = 0.0
        self._expert_target_signature = None
        self._last_snapshot = None
        self._last_frame_time = None
        self._last_health = {}
        self._client_ready_received = False
        self._local_descriptor = None
        self._vehicle_ready_deadline = 0.0
        self._bot_fire_seen = {}
        self._bot_fire_confirmations = {}
        self._bot_launch_payloads = {}
        self._bot_destructible_samples = {}
        self._player_tree_destructible_samples = {}
        self._bot_pose_times = {}
        self._bot_yaw_rates = {}
        self._track_report_time = None
        self._local_speed = 0.0
        self._local_turn_speed = 0.0
        self._local_drive_turn = 0.0
        self._local_siege_pending = None
        self._local_push_x = 0.0
        self._local_push_z = 0.0
        self._local_physics = None
        self._local_matrix = None
        self._local_pose_matrix = None
        self._local_stabilised_matrix = None
        self._local_stabilised_snapshot = None
        self._local_steady_rotation_matrix = None
        self._local_siege_body_matrix = None
        self._local_siege_stabilised_matrix = None
        self._local_siege_ground_matrix = None
        self._local_siege_flat_body_matrix = None
        self._local_siege_aim_matrix = None
        self._local_siege_aim_world_matrix = None
        self._local_siege_aim_pitch = 0.0
        self._local_model = None
        self._local_native_matrix = None
        self._local_native_stabilised_matrix = None
        self._local_camera_velocity = None
        self._local_engine_mode = None
        self._spectated_engine_id = None
        self._local_grind = 0
        self._local_vertical_speed = 0.0
        self._local_airborne = False
        self._local_fall_armed = False
        self._local_last_pitch = 0.0
        self._local_drive_pitch_history = None
        self._local_smooth_drive_pitch = 0.0
        self._local_slide_speed = 0.0
        self._local_downhill = (0.0, 0.0, 0.0)
        self._local_slope_tangent = 0.0
        self._local_ground_plane = None
        self._local_surface_up_cosine = None
        self._local_air_lateral = (0.0, 0.0)
        self._pending_landing_impacts = []
        self._local_pitch = 0.0
        self._local_roll = 0.0
        self._input_accumulator = 0.0
        self._gun_state = None
        self._gun_last_tick = None
        self._player_authority_guns = {}
        self._player_fire_intents = collections.OrderedDict()
        self._player_fire_intent_history = collections.OrderedDict()
        self._player_fire_launch_pending = {}
        self._local_fire_intent = None
        self._ammo_signature = None
        self._targeting_signature = None
        self._equipment_state = None
        self._equipment_signature = None
        self._equipment_revision = -1
        self._local_loadout_cache = None
        self._garage_loadout = None
        self._offframe_seconds = 0.0
        self._effect_reports = 0
        self._spotted_signature = None
        self._local_spotting_cache = None
        self._local_factors_cache = None
        self._remote_spotting_cache = {}
        self._local_still_since = None
        self._published_vision_radius = None
        self._published_still_devices = {}
        self._vision_feed_failed = False
        self._reported_crew_impaired = None
        self._battle_result = None
        self._round_finished_notified = False
        self._on_local_leave = None
        self._arena_type = None
        self._arena_bounds = None
        self._spawn_planner = None
        self._navigation_graph = None
        self._grounded_bot_ids = set()
        self._bot_vehicle_assignments = {}
        self._rules_state = {'bases': {}}
        self._destructibles = None
        self._local_damage_report = None
        self._local_critical_base_revision = 0
        self._local_critical_server_revision = 0
        self._local_critical_next_seq = 0
        self._local_critical_owned = False
        self._accepted_event_ids = _RecentIdSet()
        self._applied_event_ids = _RecentIdSet()
        self._seen_event_ids = self._applied_event_ids
        self._event_journal = []
        self._local_last_attacker = None
        self._next_critical_report_time = 0.0
        self._last_presented_rpm = None
        self._next_rpm_time = 0.0
        self._drown_check = 0.0
        self._drown_time = 0.0
        self._drown_level = 0
        self._drown_started = None
        self._player_environment_check = 0.0
        self._player_environment_seq = 0
        self._overturn_check = 0.0
        self._overturn_time = 0.0
        self._overturn_level = 0
        self._overturn_started = None
        self._battle_live = False
        self._prebattle_deadline = None
        self._next_spotting_time = 0.0
        self._foliage = None
        self._next_fallen_tree_foliage_refresh = 0.0
        self._fallen_tree_foliage_seen_bodies = set()
        self._fallen_tree_foliage_stable = {}
        if cleanup_error is not None:
            raise cleanup_error

    def _release_retained_client_space(self, space_id, mapping_id=0):
        """Close the exact #1513 space even if stock destroy lost its id."""
        try:
            space_id = int(space_id or 0)
            mapping_id = int(mapping_id or 0)
        except (TypeError, ValueError):
            return RuntimeError('stock map teardown exposed invalid space ids')
        if space_id <= 0:
            return None
        is_client_space = getattr(
            self._runtime.bigworld, 'isClientSpace', None)
        if not callable(is_client_space):
            return None
        try:
            retained = bool(is_client_space(space_id))
        except Exception as error:
            return error
        if not retained:
            return None
        first_error = None
        if mapping_id > 0:
            remove_mapping = getattr(
                self._runtime.bigworld, 'delSpaceGeometryMapping', None)
            if callable(remove_mapping):
                try:
                    remove_mapping(space_id, mapping_id)
                except Exception as error:
                    first_error = error
        for name in ('clearSpace', 'releaseSpace'):
            function = getattr(self._runtime.bigworld, name, None)
            if not callable(function):
                if first_error is None:
                    first_error = RuntimeError(
                        'BigWorld.%s is unavailable' % name)
                continue
            try:
                function(space_id)
            except Exception as error:
                if first_error is None:
                    first_error = error
        try:
            retained = bool(is_client_space(space_id))
        except Exception as error:
            if first_error is None:
                first_error = error
            retained = True
        if retained and first_error is None:
            first_error = RuntimeError(
                'stock map teardown retained client space %s' % space_id)
        return first_error

    def _read_engine_player(self):
        try:
            return self._runtime.bigworld.player(), None
        except ReferenceError:
            return None, None
        except Exception as error:
            return None, error

    def _force_clear_engine_player(self, retained_message):
        first_error = None
        found_clear = False
        player = None
        try:
            self._runtime.compatibility.retire_current_player()
        except Exception as error:
            first_error = error
        for name in ('clearEntitiesAndSpaces', 'clearAllSpaces'):
            clear = getattr(self._runtime.bigworld, name, None)
            if not callable(clear):
                continue
            found_clear = True
            succeeded = False
            try:
                clear()
                succeeded = True
            except Exception as error:
                if first_error is None:
                    first_error = error
            player, player_error = self._read_engine_player()
            if player_error is not None and first_error is None:
                first_error = player_error
            if succeeded and player_error is None and player is None:
                return first_error
        if not found_clear:
            return RuntimeError('no engine entity-clear boundary is available')
        if player is not None:
            return RuntimeError(retained_message)
        return first_error

    def _fail(self, error):
        active_traceback = None
        if sys.exc_info()[0] is not None:
            active_traceback = traceback.format_exc()
        self.error = str(error)
        self._generation += 1
        self._cancel_callbacks()
        self._retain_native_teardown_owners()
        cleanup_error = None
        try:
            self._cleanup()
        except Exception as cleanup_failure:
            cleanup_error = cleanup_failure
            self.error = '%s; cleanup failed: %s' % (
                self.error, cleanup_failure)
        self.state = 'failed'
        # Asynchronous map/entity failures happen after OfflineMapCreator has
        # replaced the lobby Account.  Recover across the same native callback
        # boundary as a normal round exit; constructing Hangar in this cleanup
        # callback can reuse a battle GUI component still queued for update.
        if cleanup_error is None:
            try:
                self._schedule_lobby_restore(
                    lambda lobby_restored: self._report_failure(
                        lobby_restored, active_traceback))
                return
            except Exception as schedule_failure:
                self.error = '%s; lobby restore scheduling failed: %s' % (
                    self.error, schedule_failure)
        self._retired_native_owners = []
        # A failed cleanup/schedule cannot remain LOGGED_ON without a valid
        # Account or Avatar.  LANSession owns only its socket/picker and must
        # not recurse into this native runtime boundary.
        try:
            self._runtime.compatibility.disconnect()
        except Exception as disconnect_failure:
            self.error = '%s; offline disconnect failed: %s' % (
                self.error, disconnect_failure)
        self._report_failure(False, active_traceback)

    def _report_failure(self, lobby_restored, active_traceback):
        callback = getattr(self.client, 'on_event', None)
        if callable(callback):
            try:
                callback('battle_failed', {
                    'message': self.error,
                    'round_id': (self._start_message or {}).get('round_id'),
                    'lobby_restored': lobby_restored,
                })
            except Exception:
                # A recovery notification is not allowed to replace the first
                # native failure or escape into the LAN poll callback.
                pass
        sys.stdout.write('[Offline LAN 0.9.22] battle failed: %s\n' %
                         self.error)
        if active_traceback is not None:
            sys.stdout.write(
                '[Offline LAN 0.9.22] battle traceback:\n%s' %
                active_traceback)


g_battle_runtime = BattleRuntime()
