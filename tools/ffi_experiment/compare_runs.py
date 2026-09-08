#!/usr/bin/env python3
"""Run sequential, rotating-order workload comparisons and check all outputs.

Every run has its own process and is compared with the unmodified Python
implementation's complete snapshot. Timing excludes process/fixture startup;
native map transfer is reported separately and included in the cold total.
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', required=True)
    parser.add_argument('--module', required=True)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rounds', type=int, default=7)
    parser.add_argument('--map', default='59_asia_great_wall')
    parser.add_argument('--scenario', default='combat')
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--fps', type=float, default=15)
    parser.add_argument('--include-step', action='store_true')
    parser.add_argument('--include-astar-control', action='store_true',
                        help='also measure native A* without the extra components')
    parser.add_argument('--stage-timing', action='store_true')
    parser.add_argument('--components', default='')
    parser.add_argument('--control-components', default='',
                        help='also run a native variant with this component subset')
    args = parser.parse_args()
    if args.include_astar_control and not args.components:
        parser.error('--include-astar-control requires --components')
    args.output.mkdir(parents=True, exist_ok=False)
    backends = ['python', 'native'] + (['native-step'] if args.include_step else [])
    if args.include_astar_control:
        backends.append('native-astar')
    if args.control_components:
        backends.append('native-control')
    reference = None
    waiting = []
    records = []
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    for repeat in range(args.rounds):
        order = backends[repeat % len(backends):] + backends[:repeat % len(backends)]
        for backend in order:
            output = args.output / ('%02d-%s.json' % (repeat, backend))
            command = [args.python, str(HERE / 'portable_workload.py'),
                       '--fixture', args.fixture, '--backend',
                       'native' if backend in ('native-astar', 'native-control') else backend,
                       '--module', args.module, '--map', args.map,
                       '--scenario', args.scenario, '--seconds', str(args.seconds),
                       '--fps', str(args.fps), '--output', str(output)]
            if args.stage_timing:
                command.append('--stage-timing')
            if backend == 'native-control':
                command.extend(('--components', args.control_components))
            elif backend not in ('python', 'native-astar') and args.components:
                command.extend(('--components', args.components))
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            report = json.loads(output.read_text())
            report['backend'] = backend
            snapshot = report.pop('snapshot')
            if backend == 'python' and reference is None:
                reference = snapshot
                for previous in waiting:
                    if previous != reference:
                        raise RuntimeError('pre-reference candidate changed behavior')
                waiting.clear()
            if reference is None:
                waiting.append(snapshot)
            elif snapshot != reference:
                for key in reference:
                    if snapshot[key] != reference[key]:
                        raise RuntimeError('%s changed %s' % (output.name, key))
                raise RuntimeError('%s changed snapshot keys' % output.name)
            report['round'] = repeat
            report['order'] = order.index(backend)
            report['cold_cpu_seconds'] = report['cpu_seconds'] + report['initialization_cpu_seconds']
            records.append(report)
            print('%02d %-11s cpu=%.6f init=%.6f parity=equal' % (
                repeat, backend, report['cpu_seconds'], report['initialization_cpu_seconds']), flush=True)
    summaries = {}
    for backend in backends:
        rows = [row for row in records if row['backend'] == backend]
        summaries[backend] = {
            'median_cpu_seconds': statistics.median(row['cpu_seconds'] for row in rows),
            'min_cpu_seconds': min(row['cpu_seconds'] for row in rows),
            'max_cpu_seconds': max(row['cpu_seconds'] for row in rows),
            'median_cold_cpu_seconds': statistics.median(row['cold_cpu_seconds'] for row in rows),
            'median_initialization_cpu_seconds': statistics.median(row['initialization_cpu_seconds'] for row in rows),
        }
    original = summaries['python']['median_cpu_seconds']
    for backend in backends:
        summaries[backend]['cpu_reduction_percent'] = 100 * (1 - summaries[backend]['median_cpu_seconds'] / original)
    result = dict(records=records, summary=summaries, snapshot_parity=True,
                  reference='unmodified production Python code',
                  limitations=['Linux host CPU timings, not #1513 Windows frame pacing',
                               'Synthetic 29-Bot scene and deterministic native-query fakes',
                               'Projectile launches acknowledged; terminals are not simulated'])
    (args.output / 'summary.json').write_text(json.dumps(result, indent=2, sort_keys=True))
    print(json.dumps(summaries, indent=2, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
