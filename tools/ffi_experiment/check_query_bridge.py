#!/usr/bin/env python
"""Check synchronous callback failures, non-reentrancy and result ownership."""
from __future__ import print_function

import argparse
import threading
from array import array

from navigation_adapter import Backend


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    args = parser.parse_args()
    backend = Backend(args.module)
    query = array('d', [0.0] * 16)
    command = [405, 0, 0, 0, 0, 1, 1.7, 3.5, 3.5, 0, 0.1, 0, 0, 0, 0]
    counts = [0, 0]
    nested_counts = [0]
    nested_packet = array('d', [0.0] * 16)

    def nested():
        nested_counts[0] += 1
        nested_packet[:8] = array('d', [0.0] * 8)

    class Result(object):
        def __del__(self):
            counts[1] += 1

    def complete():
        counts[0] += 1
        if counts[0] == 1:
            try:
                backend.call_sync(command, query, nested)
            except RuntimeError as exc:
                assert 'status=18' in str(exc)
            else:
                raise AssertionError('aliased nested query buffer was admitted')
            failures = []
            def other_thread():
                try:
                    backend.call(command)
                except RuntimeError as exc:
                    failures.append('status=18' in str(exc))
            thread = threading.Thread(target=other_thread)
            thread.start()
            thread.join()
            assert failures == [True], failures
            backend.call_sync(command, nested_packet, nested)
        # Callback entry must not permit a reset of its active native owner.
        try:
            backend.call([0])
        except RuntimeError as exc:
            assert 'status=18' in str(exc)
        else:
            raise AssertionError('nested reset was admitted')
        query[:8] = array('d', [0.0] * 8)
        return Result()

    class EngineFailure(Exception):
        pass

    def fail():
        raise EngineFailure('original engine failure')

    try:
        alias = array('d', command + [0.0])
        try:
            backend.call_sync(alias, alias, fail)
        except RuntimeError as exc:
            assert 'status=18' in str(exc)
        else:
            raise AssertionError('overlapping owned buffers were admitted')
        for unused in range(20):
            try:
                backend.call_sync(command, query, fail)
            except EngineFailure as exc:
                assert str(exc) == 'original engine failure'
            else:
                raise AssertionError('callback exception was lost')
            # Failure releases the borrowed provider, allowing the next call.
            backend.call_sync(command, query, complete)
        assert counts[0] == 200 and counts[0] == counts[1], counts
        assert nested_counts[0] == 10, nested_counts
        backend.call([0])
        print('Callback exception identity, reset rejection, cleanup and %d result releases passed.' % counts[1])
    finally:
        backend.close()


if __name__ == '__main__':
    main()
