#!/usr/bin/env python
"""Run the same existing Bot fixture on CPython 2.7 or 3.

Export the selected fixture bodies first with export_fixture.py. This runner
supplies only import/path/mock compatibility for that test scaffolding. It
executes the repository's client modules directly, without translating them.
"""
from __future__ import print_function
import argparse
import contextlib
import importlib
import io
import json
import math
import os
import random
import sys
import time
import types
from navigation_adapter import Backend

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLOCK = time.process_time if hasattr(time, 'process_time') else time.clock


class Namespace(object):
    def __init__(self, **values):
        self.__dict__.update(values)


class Path(object):
    def __init__(self, value):
        self.value = str(value)
    def __str__(self):
        return self.value
    def __truediv__(self, value):
        return Path(os.path.join(self.value, str(value)))
    __div__ = __truediv__
    def read_text(self):
        with open(self.value) as stream:
            return stream.read()


@contextlib.contextmanager
def patch_dict(target, values):
    previous = dict(target)
    target.update(values)
    try:
        yield
    finally:
        for key in values:
            if key in previous:
                target[key] = previous[key]
            else:
                target.pop(key, None)


@contextlib.contextmanager
def patch_object(target, name, return_value):
    previous = getattr(target, name)
    setattr(target, name, lambda *args, **kwargs: return_value)
    try:
        yield
    finally:
        setattr(target, name, previous)


@contextlib.contextmanager
def redirected():
    previous = sys.stdout
    # Client logging uses text writes in both interpreters.
    stream = io.BytesIO() if sys.version_info[0] == 2 else io.StringIO()
    sys.stdout = stream
    try:
        yield
    finally:
        sys.stdout = previous


def load_fixture(path):
    client = os.path.join(ROOT, 'src', 'res', 'scripts', 'client', 'gui', 'mods', 'offline_lan_0922')
    for name, directory in [('gui', client), ('gui.mods', client),
                            ('gui.mods.offline_lan_0922', client),
                            ('gui.mods.offline_lan_0922.ai', os.path.join(client, 'ai'))]:
        module = types.ModuleType(name)
        module.__path__ = [directory]
        sys.modules[name] = module
    if not hasattr(types, 'SimpleNamespace'):
        types.SimpleNamespace = Namespace
    with open(path) as stream:
        bodies = json.load(stream)
    mock = Namespace(Mock=lambda return_value: lambda *args, **kwargs: return_value,
                     patch=Namespace(dict=patch_dict, object=patch_object))
    scope = dict(math=math, types=types, sys=sys, json=json, mock=mock,
                 contextlib=contextlib)
    order = ('_Strict1513Component', '_HitTester1513', '_combat_descriptor',
             '_bot_equipment_contracts', '_Vector', '_Manager', '_catalog',
             '_empty_catalog_scan_fixture', 'crew_factors_module', 'make_runtime',
             'combat_native_queries')
    for name in order:
        code = compile(('# -*- coding: utf-8 -*-\n' + bodies[name]).encode('utf-8'), name, 'exec')
        eval(code, scope)
    fixtures = Namespace(**scope)
    fixtures._load = lambda: importlib.import_module('gui.mods.offline_lan_0922.bot_runtime')
    fixtures.DestructiblesCompatibilityTests = type('EmptyFixture', (object,), {
        '_empty_catalog_scan_fixture': scope['_empty_catalog_scan_fixture']})
    scope['fixtures'] = fixtures
    scope['destructibles_sensor'] = importlib.import_module('gui.mods.offline_lan_0922.destructibles_sensor')
    return scope


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--backend', choices=('python', 'native', 'native-step'), required=True)
    parser.add_argument('--module')
    parser.add_argument('--map', default='59_asia_great_wall')
    parser.add_argument('--scenario', choices=('navigation', 'combat'), default='combat')
    parser.add_argument('--seconds', type=float, default=30.0)
    parser.add_argument('--fps', type=float, default=15.0)
    parser.add_argument('--output', required=True)
    parser.add_argument('--stage-timing', action='store_true')
    args = parser.parse_args()
    if args.seconds <= 0 or args.fps <= 0 or (args.backend != 'python' and not args.module):
        parser.error('positive duration/cadence and a native module are required')
    backend = None
    stage_recorder = None
    messages, progress = [], []
    random.seed(17)
    with redirected():
        fixture = load_fixture(args.fixture)
        runtime, ground = fixture['make_runtime'](Path(ROOT), args.map, args.scenario)
        from gui.mods.offline_lan_0922.ai import navigation
        init_started = CLOCK()
        if args.backend != 'python':
            backend = Backend(args.module).install(navigation, batch=args.backend == 'native')
            backend.graph(runtime.navigator.grid, navigation)
        init_seconds = CLOCK() - init_started
        @contextlib.contextmanager
        def no_queries():
            yield []
        context = (fixture['combat_native_queries'](runtime, ground)
                   if args.scenario == 'combat' else no_queries())
        try:
            with context as queries:
                if args.stage_timing:
                    from stage_timing import Recorder
                    stage_recorder = Recorder(backend)
                started = CLOCK()
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
                cpu_seconds = CLOCK() - started
            counts = ({'calls': backend.calls, 'expansions': backend.expansions,
                       'completed': backend.completed} if backend else {})
            snapshot = {'messages': messages, 'native_queries': queries,
                        'probes': runtime.probe_totals(), 'decisions': runtime._decision_counts,
                        'navigation': progress}
        finally:
            if stage_recorder is not None:
                stage_recorder.close()
            if backend is not None:
                backend.close()
    report = dict(backend=args.backend, cpu_seconds=cpu_seconds,
                  initialization_cpu_seconds=init_seconds, native_counts=counts,
                  python=sys.version, scenario=args.scenario, map=args.map,
                  seconds=args.seconds, fps=args.fps,
                  real_native_timing=False, projectile_terminals_simulated=False,
                  snapshot=snapshot)
    if stage_recorder is not None:
        report['stage_timings'] = stage_recorder.rows
    with open(args.output, 'w') as stream:
        json.dump(report, stream, sort_keys=True)
    print(json.dumps(dict((key, value) for key, value in report.items() if key != 'snapshot'), sort_keys=True))


if __name__ == '__main__':
    main()
