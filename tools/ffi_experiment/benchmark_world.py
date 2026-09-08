#!/usr/bin/env python
"""Compare a complete recorded world-collision computation in Python and C++.

The native case executes all already transferred tapes in one dispatch.
Engine/destructible outcomes are frozen inputs, not eliminated live work.
"""
from __future__ import print_function
import argparse
import json
import time

from navigation_adapter import Backend
from portable_workload import load_fixture
from world_trace import Replay, register

CLOCK = time.process_time if hasattr(time, 'process_time') else time.clock


def median(values):
    values = sorted(values)
    return values[len(values)//2] if len(values)%2 else (values[len(values)//2-1]+values[len(values)//2])*0.5


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--trace', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--loops', type=int, default=5)
    parser.add_argument('--native-loops', type=int, default=500,
                        help='amortize clock resolution; results are normalized per corpus pass')
    args = parser.parse_args()
    load_fixture(args.fixture)['fixtures']._load()
    with open(args.trace) as stream:
        traces = json.load(stream)
    backend = Backend(args.module)
    replay = Replay()
    try:
        started = CLOCK()
        for trace in traces:
            register(backend, trace)
        transfer = CLOCK()-started
        for trace in traces:
            replay.run(trace)
        # Equality has been established for every request and terminal result.
        # Keep native tape validation enabled; Python timing excludes only
        # the extra diagnostic construction/comparison of each request list.
        replay.verify = False
        expected = sum(trace['status'] for trace in traces)
        records = []
        for repeat in range(args.rounds):
            order = ('python','native') if repeat%2==0 else ('native','python')
            for kind in order:
                started = CLOCK()
                loops = args.native_loops if kind == 'native' else args.loops
                if kind == 'native':
                    native_result = backend.call([404,loops])
                    total = native_result[0]
                    if int(native_result[1]) != len(traces):
                        raise AssertionError('native corpus size differs')
                else:
                    total = 0
                    for unused in range(loops):
                        for trace in traces:
                            total += replay.run(trace)
                elapsed = CLOCK()-started
                if total != expected*loops:
                    raise AssertionError('computation result differs')
                records.append(dict(backend=kind,cpu_seconds=elapsed/loops,
                                    total_cpu_seconds=elapsed,loops=loops,round=repeat))
                print('%d %s %.6f' % (repeat,kind,elapsed))
        medians = dict((kind,median([r['cpu_seconds'] for r in records if r['backend']==kind])) for kind in ('python','native'))
        result = dict(records=records,median_cpu_seconds=medians,
                      speedup=medians['python']/medians['native'],
                      reduction_percent=100*(1-medians['native']/medians['python']),
                      traces=len(traces),queries=sum(len(t['queries']) for t in traces),
                      python_loops=args.loops,native_loops=args.native_loops,
                      native_transfer_cpu_seconds=transfer,
                      exact_query_and_result_parity=True,
                      limitations=['Recorded engine/destruction answers; no real native cost',
                                   'Native persistent tapes and one bulk call; not a live adapter speedup',
                                   'Descriptor extents are preprojected for both variants',
                                   'Python reference uses host Math.Vector3 fakes, not Windows engine objects'])
        with open(args.output,'w') as stream:
            json.dump(result,stream,indent=2,sort_keys=True)
        print(json.dumps(result,sort_keys=True))
    finally:
        replay.close()
        backend.close()


if __name__ == '__main__':
    main()
