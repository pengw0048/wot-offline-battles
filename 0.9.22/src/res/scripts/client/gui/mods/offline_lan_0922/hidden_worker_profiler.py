from __future__ import print_function

"""Low-overhead timings for explicit hidden-worker query call sites.

The pinned client exposes native functions through extension-module objects
that are neither safe nor consistently mutable.  This module deliberately
does not monkey-patch those objects.  A reviewed call site passes the original
callable here, and it is invoked exactly once.

Profiling remains dormant until ``begin_frame(True)``.  Calls made by visible
clients therefore pay only one boolean branch.  Once a hidden-worker frame has
completed, explicitly instrumented scheduled callbacks are retained in a
separate off-frame bucket and attributed to the following render interval.
"""

import math
import threading
import time

try:
    import thread as _thread_module
except ImportError:
    import _thread as _thread_module


_CLOCK = getattr(time, 'perf_counter', None)
if not callable(_CLOCK):
    _CLOCK = time.clock


NATIVE_CATEGORIES = (
    'spotting', 'firing_lane', 'cover', 'ground', 'navigation',
    'motion_receipt', 'motion_direction', 'motion_commit',
    'water', 'muzzle', 'projectile_world', 'projectile_vehicle', 'ram',
    'artillery',
    'destructible_setup', 'destructible_motion',
    'destructible_projectile', 'destructible_state',
    'destructible_tree_scan', 'foliage_dynamic', 'presentation', 'other')
PYTHON_PHASE_CATEGORIES = (
    'bot_prework', 'bot_runtime_update', 'bot_postdiag',
    'bot_message_freeze', 'bot_message_enqueue', 'bot_message_send',
    'worker_message_freeze', 'worker_message_enqueue',
    'worker_message_send',
    'bot_scheduler_control_refresh', 'bot_scheduler_catchup',
    'bot_setup', 'bot_control_observation', 'bot_selected_motion',
    'bot_motion_commit_integration', 'bot_aim_fire', 'bot_supplemental',
    'bot_tank_contacts', 'bot_vertical_ground', 'bot_publication',
    'bot_contacts_inclusive', 'bot_visibility_prepare_inclusive',
    'bot_planner_navigation_inclusive', 'bot_astar_inclusive',
    'bot_driver_inclusive',
    'projectile_history', 'projectile_step', 'projectile_world',
    'projectile_vehicle', 'projectile_destructible',
    'projectile_terminal', 'projectile_visual', 'render_sync')
PYTHON_CATEGORIES = (
    'foliage_spotting', 'foliage_dynamic', 'destructible_scan',
    'destructible_contact', 'destructible_projectile',
    'destructible_mutation', 'vehicle_collision_projectile',
    'vehicle_collision_firing_lane', 'vehicle_collision_presentation',
    'vehicle_collision_other', 'muzzle_transform') + \
    PYTHON_PHASE_CATEGORIES + ('other',)
WORK_COUNTERS = (
    'bot_control_refresh_slices', 'bot_catchup_slices',
    'bot_live_rows', 'bot_integrated_rows', 'bot_decision_due',
    'bot_candidate_attempts', 'bot_candidate_native_fallbacks',
    'bot_motion_cache_hits', 'bot_motion_cache_misses',
    'bot_receipt_requests', 'bot_receipt_attempts',
    'bot_receipt_deferred', 'bot_astar_expansions',
    'bot_contact_candidate_pairs', 'bot_contact_resolved_pairs',
    'bot_slope_samples', 'bot_observation_pairs', 'bot_cover_jobs',
    'bot_projected_rows', 'bot_publications',
    'bot_visibility_pairs', 'bot_shot_lane_pairs', 'bot_ground_probes',
    'bot_motion_direction_probes', 'bot_motion_receipt_probes',
    'bot_motion_commit_sweeps',
    'projectiles_active', 'projectile_segments', 'projectile_candidates',
    'projectile_terminals', 'bot_messages', 'bot_rows')
WORK_COUNTERS += (
    'worker_messages', 'worker_wire_bytes', 'worker_queue_depth')
_NATIVE_CATEGORY_LOOKUP = dict((name, True) for name in NATIVE_CATEGORIES)
_PYTHON_CATEGORY_LOOKUP = dict((name, True) for name in PYTHON_CATEGORIES)
_WORK_COUNTER_LOOKUP = dict((name, True) for name in WORK_COUNTERS)

# A timing row is inclusive unless it belongs to one of the two explicitly
# additive groups.  ``frame_exclusive`` categories partition the reviewed
# outer Bot callback section. ``bot_step_exclusive`` categories partition one
# BotRuntime physical slice.  Every other scope can overlap one of those
# groups and must be displayed separately rather than added to it.
PYTHON_CATEGORY_SCOPES = dict(
    (name, 'inclusive_boundary') for name in PYTHON_CATEGORIES)
for _name in ('bot_prework', 'bot_runtime_update', 'bot_postdiag'):
    PYTHON_CATEGORY_SCOPES[_name] = 'frame_exclusive'
for _name in (
        'bot_setup', 'bot_control_observation', 'bot_selected_motion',
        'bot_motion_commit_integration', 'bot_aim_fire', 'bot_supplemental',
        'bot_tank_contacts', 'bot_vertical_ground', 'bot_publication'):
    PYTHON_CATEGORY_SCOPES[_name] = 'bot_step_exclusive'
for _name in ('bot_scheduler_control_refresh', 'bot_scheduler_catchup'):
    PYTHON_CATEGORY_SCOPES[_name] = 'scheduler_inclusive'
for _name in (
        'bot_contacts_inclusive', 'bot_visibility_prepare_inclusive',
        'bot_planner_navigation_inclusive', 'bot_astar_inclusive',
        'bot_driver_inclusive'):
    PYTHON_CATEGORY_SCOPES[_name] = 'nested_inclusive'
for _name in ('worker_message_freeze', 'worker_message_enqueue',
              'worker_message_send'):
    PYTHON_CATEGORY_SCOPES[_name] = 'message_nested_inclusive'
for _name in ('bot_message_freeze', 'bot_message_enqueue',
              'bot_message_send'):
    PYTHON_CATEGORY_SCOPES[_name] = 'legacy_bot_message_nested_inclusive'
PYTHON_CATEGORY_SCOPES['projectile_step'] = 'projectile_outer_inclusive'
for _name in (
        'projectile_history', 'projectile_world', 'projectile_vehicle',
        'projectile_destructible', 'projectile_terminal',
        'projectile_visual'):
    PYTHON_CATEGORY_SCOPES[_name] = 'projectile_nested_inclusive'
PYTHON_CATEGORY_SCOPES['render_sync'] = 'frame_inclusive_boundary'
TRACE_SAMPLES_PER_FRAME = 24
SLOW_TRACE_MIN_SECONDS = 0.001
REPRESENTATIVE_SEGMENT_PERIOD = 256
REPRESENTATIVE_SEGMENTS_PER_CATEGORY_PER_FRAME = 2
FULL_TIMING_SECONDS = 30.0
DORMANT_BASELINE_SECONDS = 5.0
MAX_WORK_COUNTER_VALUE = 1000000000000
WORK_COUNTER_SCOPE = 'active_hidden_worker_session_all_modes'
WORK_COUNTER_SEMANTICS = (
    'nonnegative_finite_bounded_per_interval;see_work_counter_aggregations')
WORK_COUNTER_AGGREGATIONS = dict((name, 'sum') for name in WORK_COUNTERS)
WORK_COUNTER_AGGREGATIONS['worker_queue_depth'] = 'sampled_max'
WORK_COUNTER_NOTES = {
    'bot_motion_commit_sweeps': (
        'logical_motion_resolver_requests_including_cache_fast_path;'
        'not_native_ray_count'),
    'worker_queue_depth': 'maximum_of_instrumented_queue_depth_samples',
}
PYTHON_SCOPE_SUM_RULE = (
    'sum_only_within_frame_exclusive_or_bot_step_exclusive;'
    'all_other_scopes_are_inclusive')
BOT_UPDATE_RESIDUAL_SEMANTICS = (
    'max(0,sum(scheduler_inclusive)-sum(bot_step_exclusive));'
    'nested_inclusive_is_excluded')
PYTHON_THREAD_ATTRIBUTION = (
    'begin_frame_thread_is_render;other_threads_are_offframe')
OFFFRAME_PYTHON_TIMING_SEMANTICS = (
    'timing_at_marker_start;reported_in_completion_interval')
_CATEGORY_PHASE = dict(
    (name, (index * 13) % REPRESENTATIVE_SEGMENT_PERIOD)
    for index, name in enumerate(NATIVE_CATEGORIES))
_MISSING = object()

try:
    _INTEGER_TYPES = (int, long)
except NameError:
    _INTEGER_TYPES = (int,)


def _empty_profile(measured=False):
    return {
        'measured': bool(measured),
        'native': {},
        'python': {},
        'work': {},
        'offframe_native': {},
        'offframe_python': {},
        'offframe_work': {},
        'trace': (),
        'representative_trace': (),
        'mode': 'off',
        'clock_reads': 0,
        'clock_read_estimate_ns': None,
        'work_counters_recorded': False,
        'work_counter_scope': WORK_COUNTER_SCOPE,
        'work_counter_semantics': WORK_COUNTER_SEMANTICS,
        'work_counter_aggregations': WORK_COUNTER_AGGREGATIONS,
        'work_counter_notes': WORK_COUNTER_NOTES,
        'python_timing_semantics': 'inclusive_timing_windows_only',
        'python_category_scopes': PYTHON_CATEGORY_SCOPES,
        'python_scope_sum_rule': PYTHON_SCOPE_SUM_RULE,
        'bot_update_residual_semantics': BOT_UPDATE_RESIDUAL_SEMANTICS,
        'python_thread_attribution': PYTHON_THREAD_ATTRIBUTION,
        'offframe_python_timing_semantics': (
            OFFFRAME_PYTHON_TIMING_SEMANTICS),
        'offframe_python_measured': False,
        'coverage': 'reviewed_hidden_worker_call_sites_only',
        'filtered_segment_timing': (
            'native_boundary_including_python_filter'),
    }


def _safe_name(value, allowed):
    try:
        name = str(value)
    except Exception:
        return 'other'
    return name if name in allowed else 'other'


def _safe_api(api):
    # All API names are source constants.  Keep a defensive bound so a bad
    # diagnostic caller cannot create unbounded keys in the 32-bit process.
    try:
        api = str(api)
    except Exception:
        api = 'native'
    if not api or len(api) > 48:
        return 'native'
    return api


class HiddenWorkerProfiler(object):
    """Collect bounded call counts and wall durations for one process."""

    def __init__(self, clock=None, alternate_modes=None,
                 full_timing_seconds=FULL_TIMING_SECONDS,
                 baseline_seconds=DORMANT_BASELINE_SECONDS):
        self._clock = clock or _CLOCK
        self._state_lock = threading.RLock()
        self._calibrate_clock_reads = clock is None
        if alternate_modes is None:
            alternate_modes = clock is None
        self._alternate_modes = bool(alternate_modes)
        self._full_timing_seconds = max(
            0.001, float(full_timing_seconds))
        self._baseline_seconds = max(0.001, float(baseline_seconds))
        self._reset_state()

    def reset(self):
        try:
            self._state_lock.acquire()
            try:
                self._reset_state()
            finally:
                self._state_lock.release()
        except Exception:
            # Reset is called from battle lifecycle cleanup.  A diagnostic
            # failure must not replace that cleanup's gameplay semantics.
            pass

    def _reset_state(self):
        self.enabled = False
        self._session_active = False
        self._mode = 'off'
        self._phase_started = None
        self._frame_open = False
        self._frame_ordinal = 0
        self._frame_native = {}
        self._frame_python = {}
        self._frame_work = {}
        self._offframe_native = {}
        self._offframe_python = {}
        self._offframe_work = {}
        self._trace = []
        self._trace_floor_seconds = SLOW_TRACE_MIN_SECONDS
        self._representative_trace = []
        self._representative_frame_counts = {}
        self._segment_seen = {}
        self._context = {}
        self._category_stack = []
        self._frame_clock_reads = 0
        self._offframe_clock_reads = 0
        self._clock_read_estimate_ns = None
        self._render_thread_id = None
        # Background timing markers from a prior battle are ignored if they
        # complete after reset instead of contaminating the next room.
        self._session_token = object()

    @staticmethod
    def _thread_ident():
        try:
            return _thread_module.get_ident()
        except Exception:
            return None

    @staticmethod
    def _note(bucket, key, elapsed, failed):
        row = bucket.get(key)
        if row is None:
            row = [0, 0.0, 0]
            bucket[key] = row
        row[0] += 1
        row[1] += max(0.0, float(elapsed))
        if failed:
            row[2] += 1

    @staticmethod
    def _copy_bucket(bucket):
        return dict((key, (int(value[0]), float(value[1]), int(value[2])))
                    for key, value in bucket.items())

    @staticmethod
    def _work_value(value):
        """Return one bounded work increment without coercing arbitrary data."""
        if isinstance(value, bool):
            return None
        if isinstance(value, _INTEGER_TYPES):
            if value < 0:
                return None
            return min(value, MAX_WORK_COUNTER_VALUE)
        if not isinstance(value, float):
            return None
        if math.isnan(value) or math.isinf(value) or value < 0.0:
            return None
        return min(value, float(MAX_WORK_COUNTER_VALUE))

    @staticmethod
    def _work_merge(name, current, value):
        if WORK_COUNTER_AGGREGATIONS.get(name) == 'sampled_max':
            return max(current, value)
        if (isinstance(current, _INTEGER_TYPES) and
                isinstance(value, _INTEGER_TYPES)):
            return min(current + value, MAX_WORK_COUNTER_VALUE)
        return min(
            float(current) + float(value), float(MAX_WORK_COUNTER_VALUE))

    @staticmethod
    def _copy_work_bucket(bucket):
        result = {}
        for key, value in bucket.items():
            if isinstance(value, _INTEGER_TYPES):
                result[key] = int(value)
            else:
                result[key] = float(value)
        return result

    def begin_frame(self, active, context=None):
        """Open one render callback and retain prior off-frame work."""
        try:
            return self._begin_frame(active, context)
        except Exception:
            try:
                self.reset()
            except Exception:
                pass
            return False

    def _begin_frame(self, active, context):
        active = bool(active)
        if not active:
            # Visible clients call this on every render callback.  Once no
            # hidden-worker session is active, keep that path to one branch.
            if self._session_active or self._frame_open or self.enabled:
                self.reset()
            return False
        self._state_lock.acquire()
        try:
            return self._begin_active_frame(context)
        finally:
            self._state_lock.release()

    def _begin_active_frame(self, context):
        # If a callback escaped before end_frame(), preserve its observations
        # with the interval that is about to be sealed.  Diagnostics must not
        # retain mutable frame buckets across callbacks.
        if self._frame_open:
            for key, value in self._frame_native.items():
                row = self._offframe_native.setdefault(key, [0, 0.0, 0])
                row[0] += value[0]
                row[1] += value[1]
                row[2] += value[2]
            for key, value in self._frame_python.items():
                row = self._offframe_python.setdefault(key, [0, 0.0, 0])
                row[0] += value[0]
                row[1] += value[1]
                row[2] += value[2]
            for key, value in self._frame_work.items():
                self._offframe_work[key] = self._work_merge(
                    key,
                    self._offframe_work.get(key, 0), value)
            self._offframe_clock_reads += self._frame_clock_reads
        interval_profile = self._detach_interval_profile()
        self._session_active = True
        self._render_thread_id = self._thread_ident()
        self._frame_ordinal += 1
        if self._phase_started is None:
            self._phase_started = self._clock() if self._alternate_modes else 0.0
            self._mode = 'timing+bounded_trace'
        elif self._alternate_modes:
            now = self._clock()
            duration = (self._full_timing_seconds
                        if self._mode == 'timing+bounded_trace' else
                        self._baseline_seconds)
            if float(now) - float(self._phase_started) >= duration:
                self._mode = (
                    'wrapper_only_baseline'
                    if self._mode == 'timing+bounded_trace' else
                    'timing+bounded_trace')
                self._phase_started = now
        self.enabled = self._mode == 'timing+bounded_trace'
        if (self._clock_read_estimate_ns is None and
                self._calibrate_clock_reads and self.enabled):
            self._calibrate_clock()
        self._frame_native = {}
        self._frame_python = {}
        self._frame_work = {}
        self._trace = []
        self._trace_floor_seconds = SLOW_TRACE_MIN_SECONDS
        self._representative_trace = []
        self._representative_frame_counts = {}
        self._frame_clock_reads = 0
        self._frame_open = True
        if isinstance(context, dict):
            self._context = self._safe_context(context)
        return interval_profile

    def _detach_interval_profile(self):
        if (not self._offframe_native and not self._offframe_python and
                not self._offframe_work and
                not self._trace and not self._representative_trace and
                not self._offframe_clock_reads):
            return False
        profile = {
            'measured': bool(self.enabled),
            'offframe_native': self._copy_bucket(self._offframe_native),
            'offframe_python': self._copy_bucket(self._offframe_python),
            'work': {},
            'offframe_work': self._copy_work_bucket(self._offframe_work),
            'trace': tuple(self._trace),
            'representative_trace': tuple(self._representative_trace),
            'mode': self._mode,
            'clock_reads': int(self._offframe_clock_reads),
            'clock_read_estimate_ns': self._clock_read_estimate_ns,
            'frame_ordinal': self._frame_ordinal,
            'work_counters_recorded': True,
            'work_counter_scope': WORK_COUNTER_SCOPE,
            'work_counter_semantics': WORK_COUNTER_SEMANTICS,
            'work_counter_aggregations': WORK_COUNTER_AGGREGATIONS,
            'work_counter_notes': WORK_COUNTER_NOTES,
            'python_timing_semantics': 'inclusive_timing_windows_only',
            'python_category_scopes': PYTHON_CATEGORY_SCOPES,
            'python_scope_sum_rule': PYTHON_SCOPE_SUM_RULE,
            'bot_update_residual_semantics': (
                BOT_UPDATE_RESIDUAL_SEMANTICS),
            'python_thread_attribution': PYTHON_THREAD_ATTRIBUTION,
            'offframe_python_timing_semantics': (
                OFFFRAME_PYTHON_TIMING_SEMANTICS),
            'offframe_python_measured': bool(self._offframe_python),
            'coverage': 'reviewed_hidden_worker_call_sites_only',
            'filtered_segment_timing': (
                'native_boundary_including_python_filter'),
        }
        self._offframe_native = {}
        self._offframe_python = {}
        self._offframe_work = {}
        self._trace = []
        self._trace_floor_seconds = SLOW_TRACE_MIN_SECONDS
        self._representative_trace = []
        self._representative_frame_counts = {}
        self._offframe_clock_reads = 0
        return profile

    def end_frame(self):
        """Close the current callback and detach its immutable sample."""
        if not self._session_active:
            return _empty_profile(False)
        profile = _empty_profile(False)
        locked = False
        try:
            self._state_lock.acquire()
            locked = True
            profile = self._end_frame()
        except Exception:
            try:
                self._frame_open = False
                self._frame_native = {}
                self._frame_python = {}
                self._frame_work = {}
                self._trace = []
                self._representative_trace = []
                self._representative_frame_counts = {}
                self._frame_clock_reads = 0
            except Exception:
                pass
            profile = _empty_profile(False)
        finally:
            if locked:
                try:
                    self._state_lock.release()
                except Exception:
                    pass
        return profile

    def _end_frame(self):
        if not self._session_active:
            return _empty_profile(False)
        if not self.enabled:
            profile = _empty_profile(False)
            profile['mode'] = 'wrapper_only_baseline'
            profile['frame_ordinal'] = self._frame_ordinal
            profile['clock_read_estimate_ns'] = self._clock_read_estimate_ns
            profile['work'] = self._copy_work_bucket(self._frame_work)
            profile['work_counters_recorded'] = True
            self._frame_open = False
            self._frame_native = {}
            self._frame_python = {}
            self._frame_work = {}
            self._frame_clock_reads = 0
            return profile
        profile = {
            'measured': True,
            'native': self._copy_bucket(self._frame_native),
            'python': self._copy_bucket(self._frame_python),
            'work': self._copy_work_bucket(self._frame_work),
            'offframe_native': {},
            'offframe_python': {},
            'offframe_work': {},
            'trace': tuple(sorted(
                self._trace, key=lambda row: row.get('elapsed_ms', 0.0),
                reverse=True)),
            'representative_trace': tuple(self._representative_trace),
            'mode': self._mode,
            'clock_reads': int(self._frame_clock_reads),
            'clock_read_estimate_ns': self._clock_read_estimate_ns,
            'frame_ordinal': self._frame_ordinal,
            'work_counters_recorded': True,
            'work_counter_scope': WORK_COUNTER_SCOPE,
            'work_counter_semantics': WORK_COUNTER_SEMANTICS,
            'work_counter_aggregations': WORK_COUNTER_AGGREGATIONS,
            'work_counter_notes': WORK_COUNTER_NOTES,
            'python_timing_semantics': 'inclusive_timing_windows_only',
            'python_category_scopes': PYTHON_CATEGORY_SCOPES,
            'python_scope_sum_rule': PYTHON_SCOPE_SUM_RULE,
            'bot_update_residual_semantics': (
                BOT_UPDATE_RESIDUAL_SEMANTICS),
            'python_thread_attribution': PYTHON_THREAD_ATTRIBUTION,
            'offframe_python_timing_semantics': (
                OFFFRAME_PYTHON_TIMING_SEMANTICS),
            'offframe_python_measured': False,
            'coverage': 'reviewed_hidden_worker_call_sites_only',
            'filtered_segment_timing': (
                'native_boundary_including_python_filter'),
        }
        self._frame_native = {}
        self._frame_python = {}
        self._frame_work = {}
        self._trace = []
        self._trace_floor_seconds = SLOW_TRACE_MIN_SECONDS
        self._representative_trace = []
        self._representative_frame_counts = {}
        self._frame_clock_reads = 0
        self._frame_open = False
        return profile

    def _calibrate_clock(self):
        """Upper-bound clock reads once, including the Python loop cost."""
        try:
            reads = 64
            started = self._clock()
            for unused_index in range(reads):
                self._clock()
            elapsed = max(0.0, float(self._clock()) - float(started))
            self._clock_read_estimate_ns = elapsed * 1.0e9 / float(reads + 2)
        except Exception:
            self._clock_read_estimate_ns = None

    def _read_clock(self, owner=None):
        if owner is None:
            owner = 'frame' if self._frame_open else 'offframe'
        if owner == 'frame':
            self._frame_clock_reads += 1
        else:
            self._offframe_clock_reads += 1
        return self._clock()

    def _bucket_owner(self):
        thread_id = self._thread_ident()
        if (self._frame_open and self._render_thread_id is not None and
                thread_id == self._render_thread_id):
            return 'frame'
        return 'offframe'

    def work_add(self, name, value=1):
        """Add one bounded work-unit observation without reading the clock."""
        try:
            if not self._session_active:
                return False
            name = str(name)
            if name not in _WORK_COUNTER_LOOKUP:
                return False
            value = self._work_value(value)
            if value is None:
                return False
            if self._bucket_owner() == 'frame':
                self._frame_work[name] = self._work_merge(
                    name,
                    self._frame_work.get(name, 0), value)
                return True
            self._state_lock.acquire()
            try:
                if not self._session_active:
                    return False
                self._offframe_work[name] = self._work_merge(
                    name,
                    self._offframe_work.get(name, 0), value)
                return True
            finally:
                self._state_lock.release()
        except Exception:
            return False

    def update_context(self, context):
        try:
            if self.enabled and isinstance(context, dict):
                self._context.update(self._safe_context(context))
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _safe_context(context):
        result = {}
        for name in ('map', 'round', 'destructible_revision',
                     'foliage_revision'):
            if name not in context:
                continue
            value = context.get(name)
            if isinstance(value, (bool, int, float)) or value is None:
                result[name] = value
            else:
                try:
                    result[name] = str(value)[:96]
                except Exception:
                    result[name] = '-'
        return result

    @staticmethod
    def _vector3(value):
        try:
            return (float(value[0]), float(value[1]), float(value[2]))
        except (IndexError, TypeError, ValueError):
            try:
                return (float(value.x), float(value.y), float(value.z))
            except (AttributeError, TypeError, ValueError):
                return None

    def _trace_native(self, category, api, args, result, elapsed, failed):
        """Retain slow calls plus a periodic category-stratified sample."""
        category_name = category
        representative = False
        seen = None
        if api == 'wg_collideSegment':
            seen = self._segment_seen.get(category_name, 0) + 1
            self._segment_seen[category_name] = seen
            phase = _CATEGORY_PHASE.get(category_name, 0)
            representative = bool(
                seen == 1 or seen % REPRESENTATIVE_SEGMENT_PERIOD == phase)
            if (representative and
                    self._representative_frame_counts.get(
                        category_name, 0) >=
                    REPRESENTATIVE_SEGMENTS_PER_CATEGORY_PER_FRAME):
                representative = False
        keep_slow = bool(
            elapsed >= self._trace_floor_seconds and
            (len(self._trace) < TRACE_SAMPLES_PER_FRAME or
             elapsed > self._trace_floor_seconds))
        if not keep_slow and not representative:
            return
        row = dict(self._context)
        row.update({
            'category': category_name,
            'api': api,
            'elapsed_ms': max(0.0, float(elapsed)) * 1000.0,
            'failed': bool(failed),
            'profiler_frame': self._frame_ordinal,
        })
        if api == 'wg_collideSegment' and len(args) >= 4:
            try:
                row.update({
                    'space_id': int(args[0]),
                    'start': self._vector3(args[1]),
                    'end': self._vector3(args[2]),
                    'mask': int(args[3]),
                    # A fifth-argument Python filter closes over dynamic state;
                    # preserve the sample but do not claim it is standalone.
                    'filtered': len(args) > 4,
                    'filter_free': len(args) == 4,
                    'timing_includes_python_filter': len(args) > 4,
                    # Even a filter-free ray can hit destructibles or foliage
                    # whose dynamic event stream is outside this sample.
                    'replay_candidate': len(args) == 4,
                    'replayable': False,
                })
            except (TypeError, ValueError, OverflowError):
                row['replayable'] = False
            if result is _MISSING:
                row['result'] = {'exception': True}
            elif result is None:
                row['result'] = {'hit': False}
            else:
                hit = {'hit': True}
                try:
                    hit['point'] = self._vector3(result[0])
                except (IndexError, TypeError):
                    pass
                try:
                    hit['normal'] = self._vector3(result[1])
                except (IndexError, TypeError):
                    pass
                try:
                    material = result[2]
                    if isinstance(material, (bool, int, float)):
                        hit['material'] = material
                    else:
                        hit['material'] = str(material)[:96]
                except (IndexError, TypeError, ValueError):
                    pass
                row['result'] = hit
        else:
            row['replayable'] = False
        if keep_slow:
            self._trace.append(row)
            self._trace.sort(
                key=lambda value: value.get('elapsed_ms', 0.0),
                reverse=True)
            del self._trace[TRACE_SAMPLES_PER_FRAME:]
            if len(self._trace) >= TRACE_SAMPLES_PER_FRAME:
                self._trace_floor_seconds = max(
                    SLOW_TRACE_MIN_SECONDS,
                    self._trace[-1].get('elapsed_ms', 0.0) / 1000.0)
        if representative:
            representative_row = dict(row)
            representative_row['sample_ordinal'] = seen
            representative_row['sampling'] = 'first+periodic/%d' % \
                REPRESENTATIVE_SEGMENT_PERIOD
            self._representative_trace.append(representative_row)
            self._representative_frame_counts[category_name] = (
                self._representative_frame_counts.get(category_name, 0) + 1)

    def _native_bucket(self):
        return (self._frame_native if self._frame_open else
                self._offframe_native)

    def _python_bucket(self):
        return (self._frame_python if self._frame_open else
                self._offframe_python)

    def current_category(self, default='other'):
        if self._category_stack:
            return self._category_stack[-1]
        return _safe_name(default, _NATIVE_CATEGORY_LOOKUP)

    def category_call(self, category, function, *args, **kwargs):
        """Supply semantic context without claiming the wrapper is native."""
        if not self.enabled:
            return function(*args, **kwargs)
        self._category_stack.append(
            _safe_name(category, _NATIVE_CATEGORY_LOOKUP))
        try:
            return function(*args, **kwargs)
        finally:
            self._category_stack.pop()

    def native_call(self, category, api, function, *args, **kwargs):
        """Invoke one original native callable and record it exactly once."""
        if not self.enabled:
            return function(*args, **kwargs)
        try:
            started = self._read_clock()
        except Exception:
            # A diagnostic clock failure cannot suppress the operation being
            # observed or replace its return/exception semantics.
            return function(*args, **kwargs)
        failed = False
        result = _MISSING
        try:
            result = function(*args, **kwargs)
            return result
        except Exception:
            failed = True
            raise
        finally:
            try:
                elapsed = float(self._read_clock()) - float(started)
                if math.isnan(elapsed) or math.isinf(elapsed):
                    elapsed = 0.0
                category_name = _safe_name(
                    category, _NATIVE_CATEGORY_LOOKUP)
                api_name = _safe_api(api)
                self._note(
                    self._native_bucket(),
                    '%s.%s' % (category_name, api_name),
                    elapsed, failed)
                self._trace_native(
                    category_name, api_name, args, result, elapsed, failed)
            except Exception:
                # Diagnostics are observational and may never alter the
                # physical result or turn a local failure into a round failure.
                pass

    def python_started(self, category):
        """Return an opaque start marker for a reviewed Python hot section."""
        if not self.enabled:
            return None
        try:
            category = _safe_name(category, _PYTHON_CATEGORY_LOOKUP)
            owner = self._bucket_owner()
            if owner == 'frame':
                return (category, self._read_clock('frame'), owner,
                        self._session_token)
            self._state_lock.acquire()
            try:
                # The render callback may have changed A/B mode while this
                # background thread waited for the interval swap.
                if not self.enabled or not self._session_active:
                    return None
                return (category, self._read_clock('offframe'), owner,
                        self._session_token)
            finally:
                self._state_lock.release()
        except Exception:
            return None

    def python_finished(self, marker, failed=False):
        if marker is None:
            return False
        try:
            category, started, owner, session_token = marker
            if owner == 'frame':
                if session_token is not self._session_token:
                    return False
                elapsed = float(self._read_clock('frame')) - float(started)
                if math.isnan(elapsed) or math.isinf(elapsed):
                    elapsed = 0.0
                self._note(
                    self._frame_python, category, elapsed, bool(failed))
                return True
            self._state_lock.acquire()
            try:
                if session_token is not self._session_token:
                    return False
                elapsed = (float(self._read_clock('offframe')) -
                           float(started))
                if math.isnan(elapsed) or math.isinf(elapsed):
                    elapsed = 0.0
                self._note(
                    self._offframe_python, category, elapsed, bool(failed))
                return True
            finally:
                self._state_lock.release()
        except Exception:
            return False

    def python_call(self, category, function, *args, **kwargs):
        if not self.enabled:
            return function(*args, **kwargs)
        marker = self.python_started(category)
        failed = False
        try:
            return function(*args, **kwargs)
        except Exception:
            failed = True
            raise
        finally:
            self.python_finished(marker, failed)


_PROFILER = HiddenWorkerProfiler()


def begin_frame(active, context=None):
    return _PROFILER.begin_frame(active, context=context)


def end_frame():
    return _PROFILER.end_frame()


def update_context(context):
    return _PROFILER.update_context(context)


def reset():
    return _PROFILER.reset()


def work_add(name, value=1):
    return _PROFILER.work_add(name, value)


def native_call(category, api, function, *args, **kwargs):
    return _PROFILER.native_call(category, api, function, *args, **kwargs)


def current_category(default='other'):
    return _PROFILER.current_category(default)


def category_call(category, function, *args, **kwargs):
    return _PROFILER.category_call(
        category, function, *args, **kwargs)


def python_started(category):
    return _PROFILER.python_started(category)


def python_finished(marker, failed=False):
    return _PROFILER.python_finished(marker, failed)


def python_call(category, function, *args, **kwargs):
    return _PROFILER.python_call(category, function, *args, **kwargs)
