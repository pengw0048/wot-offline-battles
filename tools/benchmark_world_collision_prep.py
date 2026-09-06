# -*- coding: utf-8 -*-
"""Replay one deterministic world-collision workload through every backend.

This fixture is the local, reproducible half of the native compute experiment.
It drives the real ``world_collision`` sweep with a synthetic scene and the
real diagnostic observer, so the stage names match the hidden worker's own
capture output.

It measures Python cost only.  The stand-in ``Vector3`` and segment query are
pure Python, so engine object construction and the exact native ray are
cheaper here than in the client.  Local numbers therefore bound the removable
interpreter work; they are not a Windows performance result.

Run with the pinned CPython 2.7.18 build for the runtime family the client
uses, or with Python 3 for a quick check:

    PYTHONDONTWRITEBYTECODE=1 python2.7 tools/benchmark_world_collision_prep.py
"""

from __future__ import print_function

import argparse
import json
import math
import os
import sys
import time
import timeit
import types


REPOSITORY_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT_SCRIPTS = os.path.join(
    REPOSITORY_ROOT, 'src', 'res', 'scripts', 'client')
if CLIENT_SCRIPTS not in sys.path:
    sys.path.insert(0, CLIENT_SCRIPTS)


def _publish_client_packages():
    """Make ``gui.mods`` importable, as the client itself provides it.

    The repository ships no ``__init__.py`` above the mod package, because
    #1513 owns those packages. Python 2 will not treat the directories as
    packages without them, so publish the two parents directly.
    """
    parent = ''
    path = CLIENT_SCRIPTS
    for name in ('gui', 'mods'):
        path = os.path.join(path, name)
        full_name = name if not parent else parent + '.' + name
        if full_name not in sys.modules:
            package = types.ModuleType(full_name)
            package.__path__ = [path]
            sys.modules[full_name] = package
        parent = full_name


_publish_client_packages()

# A shared build box runs other work, so process CPU time is the default
# metric: it excludes another process's contention while still counting every
# instruction this sweep executes.
try:
    _CPU_CLOCK = time.process_time
except AttributeError:
    _CPU_CLOCK = time.clock
_CLOCK = _CPU_CLOCK

BIN_METRES = 8.0
WALLS = (
    # (minimum x, maximum x, z, height)
    (-40.0, 40.0, 37.0, 3.0),
    (12.0, 18.0, 9.0, 1.4),
)


class Vector3(object):
    """Minimal stand-in for the engine vector used by the sweep."""

    __slots__ = ('x', 'y', 'z')

    def __init__(self, x=0.0, y=0.0, z=0.0):
        if not isinstance(x, (int, float)):
            x, y, z = x.x, x.y, x.z
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __add__(self, other):
        return Vector3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other):
        return Vector3(self.x - other.x, self.y - other.y, self.z - other.z)

    @property
    def length(self):
        return math.sqrt(
            self.x * self.x + self.y * self.y + self.z * self.z)

    def scale(self, value):
        return Vector3(self.x * value, self.y * value, self.z * value)

    def normalise(self):
        length = self.length
        if length:
            self.x /= length
            self.y /= length
            self.z /= length


def terrain_height(x, z):
    """Return a deterministic, cheap, continuous-enough ground height."""
    cell_x = int(math.floor(x / 4.0))
    cell_z = int(math.floor(z / 4.0))
    ripple = ((cell_x * 37 + cell_z * 17) % 5) * 0.12
    return 0.06 * x + 0.04 * z + ripple


class Scene(object):
    """A pure-Python segment query with the #1513 result and filter shape."""

    def __init__(self):
        self.ground_rays = 0
        self.horizontal_rays = 0
        self.filter_calls = 0

    def collide(self, unused_space, start, end, unused_mask,
                collision_filter=None):
        if abs(start.x - end.x) < 1.0e-9 and abs(start.z - end.z) < 1.0e-9:
            self.ground_rays += 1
            height = terrain_height(start.x, start.z)
            if height > start.y or height < end.y:
                return None
            if collision_filter is not None:
                self.filter_calls += 1
                if not collision_filter(0, 0, -1, -1):
                    return None
            return (Vector3(start.x, height, start.z),
                    Vector3(0.0, 1.0, 0.0), 0)
        self.horizontal_rays += 1
        for minimum_x, maximum_x, wall_z, height in WALLS:
            if start.y > height:
                continue
            if (start.z - wall_z) * (end.z - wall_z) > 0.0:
                continue
            span = end.z - start.z
            if abs(span) < 1.0e-9:
                continue
            fraction = (wall_z - start.z) / span
            hit_x = start.x + (end.x - start.x) * fraction
            if not minimum_x <= hit_x <= maximum_x:
                continue
            if collision_filter is not None:
                self.filter_calls += 1
                if not collision_filter(0, 0, -1, -1):
                    continue
            return (Vector3(hit_x, start.y + (end.y - start.y) * fraction,
                            wall_z),
                    Vector3(0.0, 0.0, -1.0), 0)
        return None


class HitTester(object):
    def __init__(self, bbox):
        self.bbox = bbox


class Hull(object):
    def __init__(self, hit_tester):
        self.hitTester = hit_tester


class Descriptor(object):
    def __init__(self, hull):
        self.hull = hull


def build_descriptor():
    """The hull bbox is read by index, exactly as the client descriptor is."""
    bbox = ((-1.6, -0.5, -3.2), (1.6, 1.6, 3.4))
    return Descriptor(Hull(HitTester(bbox)))


def build_scenarios():
    """Cover level, pitched, rolled, reverse, turning and airborne sweeps."""
    scenarios = []
    for index in range(24):
        angle = index * 0.2617993877991494
        pos = Vector3(3.0 + 0.7 * index, 1.4 + 0.02 * index, 4.0 + 1.3 * index)
        scenarios.append({
            'pos': pos,
            'yaw': angle,
            'vel': 9.0 if index % 4 else -4.5,
            'dt': 0.04,
            'airborne': index % 8 == 7,
            'pitch': 0.0 if index % 3 else 0.11,
            'roll': 0.0 if index % 5 else -0.07,
            'motion_yaw': None if index % 2 else angle + 0.35,
        })
    return scenarios


def install_destructibles(destructibles_sensor):
    """Give the collision filters real catalog state to walk."""
    if sys.version_info[0] >= 3:
        # The client module is Python 2.7 source; the fixture supplies the
        # builtin it expects when the benchmark runs on Python 3.
        destructibles_sensor.xrange = range
    filename = 'content/environment/test/normal/lod0/prop.model'
    destructibles_sensor.set_catalog({
        'format': 'offline-lan-0922-destructible-catalog',
        'version': 1,
        'game_version': '0.9.22',
        'map': 'benchmark',
        'locator_quantization': 1000,
        'resources': {
            filename: {
                'kind': 'fragile',
                'boxes': [[-0.4, -0.2, -0.5, 0.4, 1.5, 0.5, None]],
            },
        },
    })
    bins = {}
    for index in range(40):
        x = 4.0 + 0.9 * index
        z = 6.0 + 1.2 * index
        key = (int(math.floor(x / BIN_METRES)), int(math.floor(z / BIN_METRES)))
        bins.setdefault(key, []).append((22, 37 + index))
    destructibles_sensor.g_offh_destr_contact_bins = bins


def install_engine_modules():
    """Publish the destructible engine modules the solid path consults."""
    area = types.ModuleType('AreaDestructibles')
    area.g_destructiblesManager = object()
    area.DESTR_TYPE_TREE = 1
    area.DESTR_TYPE_FALLING_ATOM = 2
    area.DESTR_TYPE_FRAGILE = 3
    area.DESTR_TYPE_STRUCTURE = 4

    class _Cache(object):
        unitVehicleMass = 10000.0

        @staticmethod
        def getDescByFilename(unused_filename):
            return None

    area.g_cache = _Cache()
    cache = types.ModuleType('DestructiblesCache')
    cache.scaledDestructibleHealth = lambda scale, health: scale * health
    sys.modules['AreaDestructibles'] = area
    sys.modules['DestructiblesCache'] = cache


def run_workload(world_collision, bigworld, math_module, scenarios,
                 descriptor, repeats):
    results = []
    for unused_repeat in range(repeats):
        for scenario in scenarios:
            results.append(world_collision.check_horizontal_collision(
                bigworld, math_module, 1, scenario['pos'], scenario['yaw'],
                scenario['vel'], descriptor, scenario['airborne'],
                scenario['dt'], True, False, None, True,
                scenario['motion_yaw'], scenario['pitch'], scenario['roll']))
    return results


def native_loader(path):
    """Load the host build of the extension for a local comparison.

    The packaged extension is an exact-build #1513 sidecar and cannot run
    here.  The host build performs the identical computation through the
    identical buffer contract, so it measures the computation and the
    crossing, not the exact-client loader.
    """
    def load():
        import imp
        from gui.mods.offline_lan_0922 import native_compute_bridge
        module = imp.load_dynamic(
            native_compute_bridge.NATIVE_MODULE_NAME, path)
        return native_compute_bridge.validate(module)
    return load


def measure(backend, repeats, world_collision, worker_diagnostics,
            native_compute, bigworld, math_module, scenarios, descriptor,
            observed=True, native_path=None):
    loader = (native_loader(native_path)
              if backend == 'native' and native_path else None)
    native_compute.select_backend(backend, loader=loader)
    if backend == 'native' and native_compute.selected_backend() != 'native':
        raise SystemExit('native backend unavailable: %s' %
                         (native_compute.status()['error'],))
    scene = bigworld.scene
    scene.ground_rays = 0
    scene.horizontal_rays = 0
    scene.filter_calls = 0

    clock = _CLOCK
    if not observed:
        # No diagnostic is bound, so every observer is a pass-through call and
        # the sweep runs exactly as it does outside a capture window.
        started = clock()
        results = run_workload(world_collision, bigworld, math_module,
                               scenarios, descriptor, repeats)
        elapsed = clock() - started
        frame = {'stages': {}, 'counts': {}}
    else:
        diagnostic = worker_diagnostics.WorkerCombatDiagnostics(
            clock, capture_seconds=3600.0)
        diagnostic.begin_frame(1, clock(), trigger='benchmark')
        started = clock()
        results = worker_diagnostics.call(
            diagnostic, 'bot.update', run_workload, world_collision, bigworld,
            math_module, scenarios, descriptor, repeats)
        elapsed = clock() - started
        frame = diagnostic.finish_frame()
    sweeps = len(results)
    return {
        'backend': native_compute.selected_backend(),
        'observed': observed,
        'sweeps': sweeps,
        'wall_ms': round(elapsed * 1000.0, 3),
        'per_sweep_us': round(1.0e6 * elapsed / max(1, sweeps), 2),
        'ground_rays': scene.ground_rays,
        'horizontal_rays': scene.horizontal_rays,
        'filter_calls': scene.filter_calls,
        'verdicts': results,
        'stages': frame['stages'],
        'counts': frame['counts'],
        'backend_counts': native_compute.status()['counts'],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=40)
    parser.add_argument(
        '--rounds', type=int, default=3,
        help='matched rounds; each round runs every backend, rotating order')
    parser.add_argument('--backends', default='python,batch,native')
    parser.add_argument('--json', default=None)
    parser.add_argument('--observer', default='both',
                        choices=('on', 'off', 'both'))
    parser.add_argument('--clock', default='cpu', choices=('cpu', 'wall'))
    parser.add_argument(
        '--native-module', default=None,
        help='host build of offline_compute_native for the native backend')
    options = parser.parse_args(argv)
    global _CLOCK
    _CLOCK = _CPU_CLOCK if options.clock == 'cpu' else timeit.default_timer

    from gui.mods.offline_lan_0922 import (
        destructibles_sensor, native_compute, world_collision,
        worker_diagnostics)

    math_module = types.ModuleType('Math')
    math_module.Vector3 = Vector3
    bigworld = types.ModuleType('BigWorld')
    scene = Scene()
    bigworld.scene = scene
    bigworld.wg_collideSegment = scene.collide
    # The synthetic scene owns no destructible material, so the solid path
    # sees the same "unknown geometry stays solid" answer a real wall gives.
    bigworld.wg_getMatInfoNearPoint = lambda *unused: (
        False, Vector3(), Vector3(), 0, '', 0, 0)
    install_destructibles(destructibles_sensor)
    install_engine_modules()

    scenarios = build_scenarios()
    descriptor = build_descriptor()
    report = {
        'interpreter': sys.version.split()[0],
        'clock': options.clock,
        'repeats': options.repeats,
        'scenarios': len(scenarios),
        'runs': [],
    }
    observers = ((True, False) if options.observer == 'both'
                 else (options.observer == 'on',))
    backends = [name.strip() for name in options.backends.split(',')
                if name.strip()]
    baseline = None
    samples = {}
    for observed in observers:
        for round_index in range(options.rounds):
            # Rotate the order every round: this box is shared, so a backend
            # must not always run in the same contention slot.
            order = backends[round_index % len(backends):] + \
                backends[:round_index % len(backends)]
            for backend in order:
                measure(backend, 1, world_collision, worker_diagnostics,
                        native_compute, bigworld, math_module, scenarios,
                        descriptor, observed, options.native_module)
                run = measure(
                    backend, options.repeats, world_collision,
                    worker_diagnostics, native_compute, bigworld, math_module,
                    scenarios, descriptor, observed, options.native_module)
                verdicts = run.pop('verdicts')
                identity = (verdicts, run['ground_rays'],
                            run['horizontal_rays'])
                if baseline is None:
                    baseline = identity
                elif identity != baseline:
                    run['parity'] = 'DIFFERENT VERDICTS OR QUERY COUNT'
                else:
                    run['parity'] = 'identical verdicts and query counts'
                run['round'] = round_index + 1
                samples.setdefault(
                    (run['backend'], observed), []).append(run)
                report['runs'].append(run)

    print('%-8s %-9s %8s %8s %8s   %s' % (
        'backend', 'observer', 'best', 'median', 'worst', 'per sweep, us'))
    for key in sorted(samples):
        values = sorted(run['per_sweep_us'] for run in samples[key])
        parities = set(run.get('parity', 'baseline') for run in samples[key])
        print('%-8s %-9s %8.2f %8.2f %8.2f   %s' % (
            key[0], 'on' if key[1] else 'off', values[0],
            values[len(values) // 2], values[-1], '; '.join(sorted(parities))))
    for key in sorted(samples):
        best = min(samples[key], key=lambda run: run['per_sweep_us'])
        if not best['stages']:
            continue
        print('best %s run, observer %s:' % (
            key[0], 'on' if key[1] else 'off'))
        for name in sorted(best['stages']):
            row = best['stages'][name]
            print('    %-26s calls=%-7d total=%9.3f ms self=%9.3f ms' % (
                name, row['calls'], row['total_ms'], row['self_ms']))
    if options.json:
        with open(options.json, 'w') as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
        print('wrote %s' % options.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
