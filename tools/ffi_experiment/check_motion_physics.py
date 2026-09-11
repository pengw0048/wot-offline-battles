#!/usr/bin/env python
"""Exact copied-physics behavior across descriptors, terrain, slopes and tuning."""
from __future__ import print_function

import argparse
import random

from navigation_adapter import Backend
from portable_workload import load_fixture
from motion_adapter import Physics, CONSTANTS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--cases', type=int, default=10000)
    args = parser.parse_args()
    fixtures = load_fixture(args.fixture)
    rt = fixtures['fixtures']._load()
    module = rt.vehicle_physics
    backend = Backend(args.module)
    rng = random.Random(62771513)
    saved = dict((name, getattr(module, name)) for name in CONSTANTS)
    checks = 0
    try:
        for variant in range(3):
            if variant:
                for name in module._TUNABLE.values():
                    if name in saved:
                        setattr(module, name, saved[name] * rng.uniform(0.75, 1.25))
                module.GRAVITY = module.G * module.GRAVITY_FACTOR
            physics = Physics(backend, module)
            params = dict(mass=14000.0 + variant * 22000, powerW=440000.0,
                          nativePowerRatio=0.7 + variant * 0.15, specificFriction=0.6867,
                          brakeDecel=module.COHESION * module.GRAVITY, speedFwd=16.0,
                          speedBwd=6.0, rotSpd=0.6, terrainResist=(1.0, 1.3, 2.1))
            rows = [[] for unused in range(5)]
            expected = [[] for unused in range(5)]
            for index in range(args.cases):
                v = rng.choice((0.0, rng.uniform(-20, 25)))
                throttle = rng.choice((-1.0, 0.0, 0.3, 1.0))
                steering, airborne, handbrake = (bool(rng.randrange(2)) for unused in range(3))
                pitch, dt, terrain = rng.uniform(-1.2, 1.2), rng.choice((0.01, 1.0/60, 0.05, 0.2)), rng.randrange(3)
                args0 = (v, throttle, steering, pitch, dt, airborne, terrain, handbrake)
                rows[0].append(args0)
                expected[0].append(module.longitudinal_step(params, *args0))
                args1 = (rng.uniform(-1, 1), rng.uniform(-1, 1), v, dt, terrain, throttle)
                rows[1].append(args1)
                expected[1].append(module.traverse_step(params, *args1))
                yaw, slide = rng.uniform(-4, 4), bool(index % 2)
                rows[2].append((v, dt, steering, slide, yaw))
                expected[2].append(module.hard_contact_step(v, dt, steering, yaw if slide else None))
                rows[3].append((v, pitch, dt))
                expected[3].append(module.ground_follow_gap(v, pitch, dt))
                current, tangent = rng.uniform(0, 12), rng.uniform(0, 2.0)
                rows[4].append((current, tangent, dt))
                expected[4].append(module.slope_slide_speed(current, tangent, dt))
            for kind in range(5):
                actual = physics.batch(params, kind, rows[kind])
                for index, (first, second) in enumerate(zip(expected[kind], actual)):
                    if first != second:
                        raise AssertionError((variant, kind, index, rows[kind][index], first, second))
                    checks += 1
        print('Exact copied physics: %d results across descriptor, slope, braking, terrain, airborne and tuning variants.' % checks)
    finally:
        for name, value in saved.items():
            setattr(module, name, value)
        backend.close()


if __name__ == '__main__':
    main()
