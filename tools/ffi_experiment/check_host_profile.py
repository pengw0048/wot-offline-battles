#!/usr/bin/env python
"""Check boundary accounting across borrowed callbacks, nesting and failure."""
from __future__ import print_function
import argparse
from array import array
import time

from navigation_adapter import Backend
import check_query_bridge


def rejected(callback):
    try:
        callback()
    except RuntimeError:
        return
    raise AssertionError('Profile ownership guard admitted the call')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    args = parser.parse_args()
    backend = Backend(args.module)
    module = backend.module
    clock = time.process_time if hasattr(time, 'process_time') else time.clock
    try:
        rejected(module.profile_stop)
        started = clock()
        module.profile_start()
        rejected(module.profile_start)
        # This audit includes a disjoint nested sweep, callback exceptions,
        # aliased buffers, a rejected second thread and repeated owner reuse.
        check_query_bridge.main()
        report = module.profile_stop()
        elapsed = clock() - started
        names = ('native_core', 'python_callbacks', 'callback_bridge', 'python_outer')
        assert all(report[name] > 0 for name in names), report
        total = sum(report[name] for name in names)
        assert 0 <= elapsed - total < .02, (elapsed, report)
        assert sum(row['calls'] for row in report['callbacks']) == 230, report
        assert report['clock_transitions'] > 4 * 230, report
        # Entry guards also hold inside an actual native query callback, and
        # an exception restores the outer phase before profiling is stopped.
        packet = array('d', [0]) * 16
        command = [405, 0, 0, 0, 0, 1, 1.7, 3.5, 3.5, 0, .1, 0, 0, 0, 0]
        error = RuntimeError('profile callback failure')
        def failed():
            rejected(module.profile_start)
            rejected(module.profile_stop)
            raise error
        module.profile_start()
        try:
            backend.call_sync(command, packet, failed)
        except RuntimeError as observed:
            assert observed is error
        else:
            raise AssertionError('Callback failure was lost')
        report = module.profile_stop()
        assert sum(row['calls'] for row in report['callbacks']) == 1, report
        rejected(module.profile_stop)
        module.profile_start()
        assert not module.profile_stop()['callbacks']
        print('Host CPU partition: nesting, 230 callbacks, exceptions and owner reuse passed.')
    finally:
        backend.close()


if __name__ == '__main__':
    main()
