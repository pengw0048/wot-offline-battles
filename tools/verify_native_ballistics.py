#!/usr/bin/env python3
"""Compare the C++ ballistic API with the existing pure Python implementation.

This checks finite physical inputs with libm tolerances, not bitwise identity
or native BigWorld collision, timing, rendering, or Windows gameplay.
"""

import argparse
from collections import Counter
import importlib.util
import json
import math
from pathlib import Path
import random
import subprocess
import sys


def load_reference():
    path = (Path(__file__).resolve().parents[1] /
            'src/res/scripts/client/gui/mods/offline_lan_0922/ballistics.py')
    spec = importlib.util.spec_from_file_location('ballistics_reference', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def corpus(reference, random_count, seed):
    cases = []
    zero = (0.0, 0.0, 0.0)
    limits = (-math.pi * .5, math.pi * .5)

    def add(operation, label, *arguments):
        function = {'s': reference.ballistic_solutions,
                    'i': reference.ballistic_intercept,
                    'p': reference.ballistic_position,
                    't': reference.ballistic_path}[operation]
        cases.append((operation, label, arguments, function(*arguments)))

    for target in ((100, 0, 0), (0, 30, -100), (-100, -30, 200),
                   (.1, 0, 0), (.1000001, 0, 0), (0, 100, 0),
                   (640, 0, 0), (640.000001, 0, 0), (1000, 0, 0)):
        for speed, gravity in ((80, 10), (80, -10), (1, 10),
                               (80, .01), (1000, 10)):
            add('s', 'arcs_thresholds', zero, target, speed, gravity, *limits)
    roots = reference.ballistic_solutions(zero, (100, 0, 0), 80, 10)
    for minimum, maximum in ((-.2, 0), (-2, -1), (0, 1), (1, -1),
                             (roots[0][0] + .00005, 0),
                             (roots[0][0] + .0002, 0)):
        add('s', 'pitch_limits', zero, (100, 0, 0), 80, 10, minimum, maximum)
    for high in (False, True):
        for cap in (-1, 0, .5, 1.5, 20):
            for velocity in (zero, (30, 3, 5), (100, 0, 0)):
                add('i', 'intercept_limits', zero, (100, 0, 0), velocity,
                    80, -10, *limits, high, cap)
    # The final solve has its own lead cap, after all four iterations pass.
    aim = (100, 0, 0)
    for _ in range(4):
        previous = reference.ballistic_solutions(zero, aim, 80, 10)[0][1]
        aim = (100 + 30 * previous, 3 * previous, 5 * previous)
    final = reference.ballistic_solutions(zero, aim, 80, 10)[0][1]
    add('i', 'final_solve_cap', zero, (100, 0, 0), (30, 3, 5),
        80, 10, *limits, False, (previous + final) * .5)
    for cap in (roots[0][1] - 2e-9, roots[0][1] - .5e-9):
        add('i', 'lead_tolerance', zero, (100, 0, 0), zero,
            80, 10, *limits, False, cap)
    for time in (-1, 0, .01, 1, 20):
        for gravity in (-10, 0, 10):
            add('p', 'position_time_gravity', (12, 20, -30), 1.2, -.3, 80, gravity, time)
        for step in (-1, 0, .019, .02, .12, .25, .251, 1):
            add('t', 'path_steps', (12, 20, -30), 1.2, -.3, 80, -10, time, step)
    generator = random.Random(seed)
    for _ in range(random_count):
        start = tuple(generator.uniform(-1000, 1000) for _ in range(3))
        target = tuple(start[index] + generator.uniform(-1500, 1500)
                       for index in range(3))
        velocity = tuple(generator.uniform(-30, 30) for _ in range(3))
        speed = generator.uniform(40, 1600)
        gravity = generator.choice((-1, 1)) * generator.uniform(1, 30)
        minimum = generator.uniform(-math.pi * .5, -.1)
        maximum = generator.uniform(0, math.pi * .5)
        add('s', 'random_solutions', start, target, speed, gravity, minimum, maximum)
        add('i', 'random_intercept', start, target, velocity, speed, gravity,
            minimum, maximum, generator.choice((False, True)),
            generator.choice((0, 1, 5, 20)))
        yaw = generator.uniform(-math.pi, math.pi)
        pitch = generator.uniform(-math.pi * .5, math.pi * .5)
        time = generator.uniform(-1, 5)
        add('p', 'random_position', start, yaw, pitch, speed, gravity, time)
        add('t', 'random_path', start, yaw, pitch, speed, gravity, time,
            generator.uniform(-.1, .4))
    return cases


def flatten(values):
    for value in values:
        if isinstance(value, (tuple, list)):
            yield from flatten(value)
        else:
            yield float(value)


def equivalent(actual, expected):
    if expected is None:
        return actual is None
    if isinstance(expected, (tuple, list)):
        return (isinstance(actual, list) and len(actual) == len(expected) and
                all(equivalent(left, right) for left, right in zip(actual, expected)))
    return isinstance(actual, (int, float)) and math.isclose(
        actual, expected, rel_tol=2e-12, abs_tol=2e-9)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', required=True, help='built test_ballistics executable')
    parser.add_argument('--random-cases', type=int, default=2048)
    parser.add_argument('--seed', type=int, default=1513)
    args = parser.parse_args()
    if args.random_cases < 0:
        parser.error('--random-cases must be nonnegative')
    sys.dont_write_bytecode = True
    cases = corpus(load_reference(), args.random_cases, args.seed)
    request = ''.join(operation + ' ' + ' '.join(format(value, '.17g')
                      for value in flatten(arguments)) + '\n'
                      for operation, _, arguments, _ in cases)
    result = subprocess.run([str(Path(args.probe).resolve()), '--probe'],
                            input=request, text=True, capture_output=True, check=True)
    lines = result.stdout.splitlines()
    if len(lines) != len(cases):
        raise ValueError('expected %d results, got %d' % (len(cases), len(lines)))
    for index, ((operation, label, arguments, expected), line) in enumerate(zip(cases, lines)):
        actual = json.loads(line)
        if not equivalent(actual, expected):
            raise ValueError('row %d %s/%s: arguments=%r expected=%r actual=%r' %
                             (index, operation, label, arguments, expected, actual))
    print(json.dumps({'result': 'passed', 'cases': len(cases),
                      'categories': dict(Counter(label for _, label, _, _ in cases)),
                      'relative_tolerance': 2e-12, 'absolute_tolerance': 2e-9,
                      'reference': 'offline_lan_0922/ballistics.py'}, sort_keys=True))


if __name__ == '__main__':
    main()
