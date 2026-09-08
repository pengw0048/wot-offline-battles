#!/usr/bin/env python3
"""Exercise native world sweep requests against existing physical scenarios."""
import argparse
import sys
import unittest
from pathlib import Path

from navigation_adapter import Backend
from world_adapter import WorldBackend
from world_trace import Recorder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    args = parser.parse_args()
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tests'))
    import test_port_0922_world_collision as tests
    backend = Backend(args.module)
    shadow = Recorder(backend)
    # This one source test replaces the entire ground-profile computation
    # with invented heights, so its native leaf tape is intentionally absent.
    # Keep its original assertion. Other physical scenes still exercise
    # diagonal sweeps, pitched lanes, terrain profiles and exact-top checks.
    replaced = 'test_diagonal_drivable_profile_samples_the_hit_corner_segment'
    names = unittest.defaultTestLoader.getTestCaseNames(tests.WorldCollisionTests)
    try:
        suite = unittest.TestSuite(tests.WorldCollisionTests(name) for name in names if name != replaced)
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        count = len(shadow.traces)
        shadow.close()
        source_result = unittest.TextTestRunner(verbosity=1).run(
            unittest.TestSuite([tests.WorldCollisionTests(replaced)]))
        native = WorldBackend(backend)
        try:
            live_result = unittest.TextTestRunner(verbosity=1).run(
                unittest.TestSuite(tests.WorldCollisionTests(name) for name in names if name != replaced))
        finally:
            native.close()
        if not (result.wasSuccessful() and source_result.wasSuccessful() and live_result.wasSuccessful()):
            raise SystemExit(1)
        print('Exact native query/result traces: %d; %d physical tests pass both shadow and live adapters; one internal-helper mock remains a source-only test.' % (count,len(names)-1))
    finally:
        shadow.close()
        backend.close()


if __name__ == '__main__':
    main()
