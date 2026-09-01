#!/usr/bin/env python3
"""Compare render-frame and production fixed-cadence bot simulation costs.

The render-frame case forces the production update body due on every frame;
the fixed case uses the hidden worker's explicit control scheduler. Both runs
use the same deterministic 29-bot roster, probes, descriptors and commands.
"""

import argparse
from pathlib import Path
import statistics
import sys
import time


PORT_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = PORT_ROOT / 'tests'
sys.path.insert(0, str(TEST_ROOT))

import test_port_0922_bot_runtime as fixtures


def _command():
    return {
        'target_yaw': 0.0, 'throttle': 1.0, 'turn': 0.0,
        'shell_index': 0, 'fire_allowed': False, 'target_id': None,
        'fire_range': 0.0, 'combat_mode': 'route',
        'aim_position': (0.0, 1.0, 200.0),
        'face_position': (0.0, 1.0, 200.0),
        'move_position': (0.0, 0.0, 200.0),
        'recovery_mode': 'drive', 'movement_intent': True,
    }


def _runtime(visible=True, control_seconds=None):
    module = fixtures._load()
    command = _command()
    runtime = module.BotRuntime(
        1,
        descriptor_resolver=lambda unused: fixtures._combat_descriptor(),
        adapter_factory=lambda *unused, **kwargs: fixtures._FixedAdapter(
            command),
        direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
        visibility_probe=lambda *unused: bool(visible),
        firing_lane_probe=lambda *unused: True,
        ground_probe=lambda *unused: 0.0,
        physics_ground_probe=lambda *unused: 0.0,
        spawn_resolver=fixtures._spawn_resolver,
        baked_graph=fixtures._graph(),
        control_seconds=control_seconds)
    roster = []
    for index in range(29):
        team = 1 if index < 15 else 2
        roster.append({
            'id': 11 + index, 'team': team,
            'slot': index if team == 1 else index - 15,
            'name': 'Benchmark-%02d' % index,
        })
    runtime.battle_start({
        'round_id': 1, 'map': '01_karelia',
        'bot_authority_id': 1, 'bots': roster,
    })
    return runtime


def _run(seconds, fps, render_frame_baseline, control_hz, visible=True):
    control_seconds = (None if render_frame_baseline else
                       1.0 / float(control_hz))
    runtime = _runtime(
        visible=visible, control_seconds=control_seconds)
    frame_count = int(round(float(seconds) * float(fps)))
    dt = 1.0 / float(fps)
    simulation_calls = 0
    probe_rows = []
    callback_times = []
    previous_probes = runtime.probe_totals()
    started = time.perf_counter()
    for frame in range(frame_count):
        now = 100.0 + (frame + 1) * dt
        if render_frame_baseline:
            runtime._next_publication = now
        callback_started = time.perf_counter()
        outgoing = runtime.update(dt, now)
        callback_times.append(time.perf_counter() - callback_started)
        simulation_calls += int(any(
            message.get('type') == 'bot_state' for message in outgoing))
        current_probes = runtime.probe_totals()
        probe_rows.append(tuple(
            current_probes[index] - previous_probes[index]
            for index in range(len(current_probes))))
        previous_probes = current_probes
    elapsed = time.perf_counter() - started
    return (simulation_calls, elapsed, runtime.probe_totals(), probe_rows,
            callback_times, runtime.diagnostic_totals())


def _percentile(values, fraction):
    values = sorted(float(value) for value in values)
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    rank = min(1.0, max(0.0, float(fraction))) * (len(values) - 1)
    lower = int(rank)
    upper = min(len(values) - 1, lower + 1)
    weight = rank - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def _distribution(values):
    return tuple(_percentile(values, fraction) * 1000.0
                 for fraction in (0.50, 0.95, 0.99, 1.0))


def _probe_maxima(rows):
    if not rows:
        return (0,) * len(fixtures._load().PROBE_KINDS)
    return tuple(max(row[index] for row in rows)
                 for index in range(len(rows[0])))


def _publication_cost(states, projector, iterations, legacy_double_copy):
    started = time.perf_counter()
    for unused_iteration in range(iterations):
        source = ([dict(state) for state in states]
                  if legacy_double_copy else states)
        projected = [projector(state) for state in source]
        if any(state is None for state in projected):
            raise RuntimeError('publication projection failed')
    return time.perf_counter() - started


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=5.0)
    parser.add_argument('--fps', type=int, default=60)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument(
        '--control-hz', type=float, default=10.0,
        help='fixed hidden-worker control rate (production default: 10)')
    parser.add_argument(
        '--blocked-visibility', action='store_true',
        help='make every native visibility ray report static cover')
    args = parser.parse_args()
    if (args.seconds <= 0.0 or args.fps <= 0 or args.repeats <= 0 or
            args.control_hz <= 0.0):
        parser.error('seconds, fps, repeats and control-hz must be positive')

    fixed_times = []
    baseline_times = []
    fixed_calls = baseline_calls = None
    for unused_repeat in range(args.repeats):
        (baseline_calls, baseline_time, baseline_probes,
         baseline_probe_rows, baseline_callback_times,
         baseline_diagnostics) = _run(
            args.seconds, args.fps, True, args.control_hz,
            visible=not args.blocked_visibility)
        (fixed_calls, fixed_time, fixed_probes,
         fixed_probe_rows, fixed_callback_times, fixed_diagnostics) = _run(
            args.seconds, args.fps, False, args.control_hz,
            visible=not args.blocked_visibility)
        baseline_times.append(baseline_time)
        fixed_times.append(fixed_time)

    baseline_median = statistics.median(baseline_times)
    fixed_median = statistics.median(fixed_times)
    reduction = (1.0 - float(fixed_calls) / float(baseline_calls)) * 100.0
    speedup = baseline_median / fixed_median
    sample = _runtime(visible=not args.blocked_visibility)
    runtime_module = sys.modules[sample.__class__.__module__]
    sample_publication = sample.update(1.0 / args.fps, 100.0)[0]
    sample_states = list(sample.states.values())
    projector = runtime_module.lan_client.project_bot_state
    internal_fields = sum(len(state) for state in sample_states)
    wire_fields = sum(len(state) for state in sample_publication['bots'])
    field_reduction = (
        1.0 - float(wire_fields) / float(internal_fields)) * 100.0
    projection_iterations = 1000
    legacy_projection = statistics.median([
        _publication_cost(
            sample_states, projector,
            projection_iterations, True)
        for unused_repeat in range(args.repeats)
    ])
    once_projection = statistics.median([
        _publication_cost(
            sample_states, projector,
            projection_iterations, False)
        for unused_repeat in range(args.repeats)
    ])
    print('fps=%d control_hz=%.3f bots=29 seconds=%.3f frames=%d repeats=%d' % (
        args.fps, args.control_hz, args.seconds,
        int(round(args.seconds * args.fps)), args.repeats))
    print('render_frame_baseline calls=%d median_ms=%.3f' % (
        baseline_calls, baseline_median * 1000.0))
    print('fixed_control calls=%d median_ms=%.3f' % (
        fixed_calls, fixed_median * 1000.0))
    print('render_frame_logical_probes %s' % dict(zip(
        runtime_module.PROBE_KINDS, baseline_probes)))
    print('fixed_control_logical_probes %s' % dict(zip(
        runtime_module.PROBE_KINDS, fixed_probes)))
    print('render_frame_logical_probe_max_per_callback %s' % dict(zip(
        runtime_module.PROBE_KINDS, _probe_maxima(baseline_probe_rows))))
    print('fixed_control_logical_probe_max_per_callback %s' % dict(zip(
        runtime_module.PROBE_KINDS, _probe_maxima(fixed_probe_rows))))
    print('render_frame_callback_ms_p50_p95_p99_max %.3f/%.3f/%.3f/%.3f' %
          _distribution(baseline_callback_times))
    print('fixed_control_callback_ms_p50_p95_p99_max %.3f/%.3f/%.3f/%.3f' %
          _distribution(fixed_callback_times))
    print('fixed_control_visibility_scheduler %s' % dict(
        (name, fixed_diagnostics.get(name)) for name in (
            'visibility_queue_depth', 'visibility_queue_max_depth',
            'visibility_oldest_stale_age_ms',
            'visibility_oldest_stale_max_age_ms',
            'visibility_admitted', 'visibility_completed',
            'visibility_deferred', 'visibility_selected_services',
            'visibility_fire_services', 'visibility_new_services',
            'visibility_ordinary_services')))
    print('fixed_control_supplemental_lane_scheduler %s' % dict(
        (name, fixed_diagnostics.get(name)) for name in (
            'shot_lane_pending_pairs', 'shot_lane_pending_max_pairs',
            'shot_lane_oldest_due_age_ms',
            'shot_lane_oldest_due_max_age_ms',
            'shot_lane_completed_pairs',
            'shot_lane_budget_deferred_attempts')))
    print('call_reduction=%.1f%% elapsed_speedup=%.3fx' % (
        reduction, speedup))
    print('publication_fields internal=%d wire=%d reduction=%.1f%%' % (
        internal_fields, wire_fields, field_reduction))
    print('publication_projection iterations=%d legacy_double_ms=%.3f '
          'once_ms=%.3f speedup=%.3fx' % (
              projection_iterations, legacy_projection * 1000.0,
              once_projection * 1000.0,
              legacy_projection / once_projection))


if __name__ == '__main__':
    main()
