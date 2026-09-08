#!/usr/bin/env python
"""Differential native navigation guards, hazards, smoothing and recovery."""
from __future__ import print_function

import argparse
import random

from check_parity import nav, random_graph, ground_for
from navigation_adapter import Backend
from navigation_flow_adapter import NativeGrid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--cases', type=int, default=150)
    args = parser.parse_args()
    backend = Backend(args.module)
    checks = 0
    try:
        for seed in range(args.cases):
            rng = random.Random(seed)
            graph = random_graph(rng)
            original = nav.TerrainGrid(ground_for(graph), baked_graph=graph)
            original.bounds = (-20.0, -25.0, 27.0, 21.0) if seed % 2 else None
            original._failed_edges = {((0, 0), (1, 0)): (2.0, 240.0)}
            original._static_hull_edges = {((1, 1), (2, 1)): 240.0}
            native = NativeGrid(backend, original, nav)
            def check(name, *values):
                expected = getattr(original, name)(*values)
                actual = getattr(native, name)(*values)
                if expected != actual:
                    raise AssertionError((seed, name, values, expected, actual))
            for unused in range(20):
                points = [original.point_for((rng.randrange(-2, 15), rng.randrange(-2, 13)),
                                             rng.uniform(-2.0, 2.0)) for i in range(5)]
                a, b = points[:2]
                yaw, now = rng.uniform(-3.14, 3.14), rng.choice((0.0, 1.0, 2.0, 5.0))
                # Time advances independently in each owner's failure map.
                for name, values in (
                    ('_ground', (a[0], a[2], a[1])),
                    ('segment_clear', (a, b)),
                    ('dry_segment_clear', (a, b, now)),
                    ('segment_has_baked_hazard', (a, b, 4)),
                    ('segment_has_motion_hazard', (a, b, 3)),
                    ('point_has_baked_hazard', (a, 7)),
                    ('baked_hazard_near', (a, 2)),
                    ('near_baked_navigation', (a, 2)),
                    ('hull_pose_clear', (a, yaw, 3.5, 1.7)),
                    ('local_corridor', (a,)),
                    ('segment_penalty', (a, b, now)),
                    ('_baked_segment_cells', (a, b, True)),
                    ('_baked_segment_cells', (a, b, False)),
                    ('baked_hazard_cells', (a, b, 4)),
                    ('_smooth', (tuple(points), now, bool(seed % 2), original._static_hull_edges)),
                    ('safe_local_target', (a, b, now, points[2:], -1.0, None, 0.4)),
                    ('path_has_edge_penalty', (points, original._static_hull_edges)),
                    ('_baked_clearance_exposure', (points,)),
                    ('shortcut_preserves_climb_approach', (points, 0, 4)),
                    ('live_shortcut_preserves_climb_approach', (a, points, 1, 4)),
                    ('shortcut_preserves_baked_clearance', (points, 0, 4)),
                    ('path_has_penalty', (points, now)),
                    ('path_crosses_static_hull', (points,)),
                    ('_edge_keys_for_segment', (a, b)),
                    ('_baked_corridor', (a, b)),
                ):
                    check(name, *values)
                    checks += 1
            native.close()
        print('Exact native baked geometry/recovery results: %d checks across %d maps.' % (checks, args.cases))
    finally:
        backend.close()


if __name__ == '__main__':
    main()
