#!/usr/bin/env python3
"""Measure the complete existing Bot workload with opt-in native A*.

Examples:
  python3 tools/ffi_experiment/benchmark_workload.py --backend python --output /tmp/a.json
  python3 tools/ffi_experiment/benchmark_workload.py --backend native --module /tmp/build/offline_astar_native.so --output /tmp/b.json

The output contains full state/event/query evidence for comparison, including
per-frame navigation queue progress. No real Windows native cost is simulated.
Use independent processes and alternate backend order for timing evidence.
"""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import benchmark_bot_workload as workload
from navigation_adapter import Backend


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('python', 'native'), required=True)
    parser.add_argument('--module')
    parser.add_argument('--map', default='59_asia_great_wall')
    parser.add_argument('--scenario', choices=('navigation', 'combat'), default='combat')
    parser.add_argument('--seconds', type=float, default=30.0)
    parser.add_argument('--fps', type=float, default=15.0)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.backend == 'native' and not args.module:
        parser.error('--module is required for native')
    random.seed(17)
    backend = None
    messages, progress = [], []
    with contextlib.redirect_stdout(io.StringIO()):
        runtime, ground = workload.make_runtime(ROOT, args.map, args.scenario)
        from gui.mods.offline_lan_0922.ai import navigation
        init_started = time.process_time()
        if args.backend == 'native':
            backend = Backend(args.module).install(navigation)
            backend.graph(runtime.navigator.grid, navigation)
        init_seconds = time.process_time() - init_started
        query_context = (workload.combat_native_queries(runtime, ground)
                         if args.scenario == 'combat' else contextlib.nullcontext([]))
        try:
            with query_context as queries:
                started = time.process_time()
                for frame in range(int(round(args.seconds * args.fps))):
                    outgoing = runtime.update(1.0 / args.fps, 100.0 + (frame + 1) / args.fps)
                    messages.extend(outgoing)
                    for message in outgoing:
                        for launch in message.get('launches', ()):
                            runtime.ack_projectile_launch(launch['id'], launch['fire_seq'])
                    nav = runtime.navigator
                    progress.append({
                        'pending': sorted(repr(key) for key in nav.searches),
                        'next': repr(nav.search_next_key),
                        'credit': nav.search_credit, 'budget': nav.search_frame_budget,
                        'completed': nav.search_completed, 'failed': nav.search_failed,
                        'last_frames': sorted((repr(key), search.last_frame)
                                              for key, search in nav.searches.items()),
                        'paths': sorted((repr(key), path) for key, path in nav.paths.items()),
                    })
                cpu_seconds = time.process_time() - started
            counts = ({'calls': backend.calls, 'expansions': backend.expansions,
                       'completed': backend.completed} if backend else {})
            snapshot = {'messages': messages, 'native_queries': queries,
                        'probes': runtime.probe_totals(), 'decisions': runtime._decision_counts,
                        'navigation': progress}
        finally:
            if backend is not None:
                backend.close()
    report = {
        'backend': args.backend, 'cpu_seconds': cpu_seconds,
        'initialization_cpu_seconds': init_seconds, 'native_counts': counts,
        'python': sys.version, 'scenario': args.scenario, 'map': args.map,
        'seconds': args.seconds, 'fps': args.fps,
        'real_native_timing': False, 'projectile_terminals_simulated': False,
        'snapshot': snapshot,
    }
    args.output.write_text(json.dumps(report, sort_keys=True))
    print(json.dumps({key: value for key, value in report.items() if key != 'snapshot'}, sort_keys=True))


if __name__ == '__main__':
    main()
