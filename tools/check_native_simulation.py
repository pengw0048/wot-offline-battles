"""Check shared native lifetimes and Python teardown with the real bridge.

Usage: python2.7 tools/check_native_simulation.py EXTENSION [CLIENT_SCRIPTS]
Stage-specific checks exercise gameplay laws and engine callback reentry.
These checks cover the shared handle and partial-construction boundaries.
"""
from __future__ import print_function

import imp
import json
import os
import sys
import types

sys.dont_write_bytecode = True


def main():
    source = (sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'src', 'res', 'scripts', 'client'))
    package_path = os.path.join(source, 'gui', 'mods', 'offline_lan_0922')
    for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        package = types.ModuleType(name)
        package.__path__ = [package_path]
        sys.modules[name] = package
    from gui.mods.offline_lan_0922 import native_simulation
    backend = imp.load_dynamic('offline_math_batch_native', sys.argv[1])

    assert backend.sim_open(-1, 1) is None
    assert backend.sim_open(1, -1) is None
    first = backend.sim_open(71, 3)
    assert first > 0
    assert backend.sim_lifetime(first) == (71, 3)
    assert backend.sim_close(first) == 1
    assert backend.sim_close(first) == 1
    assert backend.sim_lifetime(first) is None
    second = backend.sim_open(71, 3)
    assert second != first
    assert backend.sim_lifetime(second) == (71, 3)
    assert backend.sim_weapon_pending(first) is None
    assert backend.sim_weapon_pending(second) == ()
    backend.sim_close(second)

    class Runtime(object):
        round_id = 71
        finished = False
        authority = True
        fail_stage = None
        fail_detach = None
        navigator = object()

        def __init__(self):
            self.events = []
            self.aliases = set()

        def is_authority(self):
            return self.authority

    class RecordingBackend(object):
        def __init__(self):
            self.handles = []

        def sim_open(self, *args):
            handle = backend.sim_open(*args)
            self.handles.append(handle)
            return handle

        def __getattr__(self, name):
            return getattr(backend, name)

    def stage_class(label):
        class Stage(object):
            @classmethod
            def install(cls, runtime, selected, handle):
                return cls(runtime, selected, handle)

            def __init__(self, runtime, selected, handle):
                self.runtime, self.backend, self.handle = runtime, selected, handle
                runtime.events.append('construct:' + label)
                if runtime.fail_stage == label:
                    raise ValueError('construction boundary')
                runtime.aliases.add(label)

            def install_all(self):
                self.runtime.events.append('install:' + label)
                if self.runtime.fail_stage == 'install':
                    raise ValueError('installation boundary')

            def detach(self):
                assert self.backend.sim_lifetime(self.handle) == (71, 9)
                self.runtime.events.append('detach:' + label)
                self.runtime.aliases.discard(label)
                if self.runtime.fail_detach == label:
                    raise ValueError('detach boundary')
        return Stage

    names = [('native_navigation_core', 'NativeNavigationCore', 'navigation'),
             ('native_control_core', 'NativeControl', 'control'),
             ('native_motion_core', 'NativeMotion', 'motion'),
             ('native_weapons', 'NativeWeapons', 'weapons')]
    for module_name, class_name, label in names:
        name = 'gui.mods.offline_lan_0922.' + module_name
        module = types.ModuleType(name)
        setattr(module, class_name, stage_class(label))
        sys.modules[name] = module

    for failure in ('navigation', 'control', 'motion', 'weapons', 'install'):
        runtime = Runtime()
        runtime.fail_stage = failure
        recording = RecordingBackend()
        try:
            native_simulation.NativeSimulation(runtime, recording, 9)
        except ValueError:
            pass
        else:
            raise AssertionError('partial constructor unexpectedly succeeded')
        assert not runtime.aliases, (failure, runtime.events)
        assert backend.sim_lifetime(recording.handles[0]) is None

    for failure in (None, 'weapons', 'motion', 'control', 'navigation'):
        runtime = Runtime()
        recording = RecordingBackend()
        owner = native_simulation.NativeSimulation(runtime, recording, 9)
        assert owner.matches(runtime)
        runtime.round_id = 72
        assert not owner.matches(runtime)
        runtime.round_id = 71
        runtime.authority = False
        assert not owner.matches(runtime)
        runtime.authority = True
        runtime.finished = True
        assert not owner.matches(runtime)
        runtime.finished = False
        runtime.fail_detach = failure
        try:
            owner.close()
        except ValueError:
            assert failure is not None
        else:
            assert failure is None
        owner.close()
        assert not runtime.aliases
        assert not owner.matches(runtime)
        assert runtime.events[-4:] == [
            'detach:weapons', 'detach:motion', 'detach:control',
            'detach:navigation']
        assert backend.sim_lifetime(recording.handles[0]) is None

    print(json.dumps(dict(result='pass', native_handles='unique and retired',
                          partial_constructions=5, teardown_cases=5,
                          python=sys.version.split()[0])))


if __name__ == '__main__':
    main()
