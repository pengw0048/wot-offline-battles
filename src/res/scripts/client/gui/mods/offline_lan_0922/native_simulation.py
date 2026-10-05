"""Own one round's persistent native simulation inside the hidden worker."""
from __future__ import print_function

import sys

from gui.mods.offline_lan_0922 import native_math


class NativeSimulation(object):
    """Share one native lifetime across navigation, control, motion and weapons.

    Python retains descriptors and main-thread engine adapters. The native
    context retains only typed values, and is retired before those adapters
    can belong to another round or authority.
    """

    def __init__(self, runtime, backend, generation):
        self.backend = backend
        self.round_id = int(runtime.round_id)
        self.generation = int(generation)
        self.handle = None
        self.navigation = None
        self.control = None
        self.motion = None
        self.weapons = None
        handle = backend.sim_open(self.round_id, self.generation)
        if handle is None or int(handle) <= 0:
            raise RuntimeError('Native simulation could not create a lifetime')
        self.handle = int(handle)
        try:
            if backend.sim_lifetime(self.handle) != (
                    self.round_id, self.generation):
                raise RuntimeError('Native simulation lifetime mismatch')
            from gui.mods.offline_lan_0922.native_control_core import (
                NativeControl)
            from gui.mods.offline_lan_0922.native_motion_core import (
                NativeMotion)
            from gui.mods.offline_lan_0922.native_weapons import NativeWeapons
            from gui.mods.offline_lan_0922.native_navigation_core import (
                NativeNavigationCore)
            if runtime.navigator is not None:
                self.navigation = NativeNavigationCore.install(
                    runtime, backend, self.handle)
            self.control = NativeControl(runtime, backend, self.handle)
            self.motion = NativeMotion(runtime, backend, self.handle)
            self.weapons = NativeWeapons(runtime, backend, self.handle)
            self.weapons.install_all()
        except Exception:
            self.close()
            raise

    @classmethod
    def create(cls, runtime, generation):
        backend = native_math._load()
        if backend is None or not hasattr(backend, 'sim_open'):
            # Engine-free reference tests deliberately run the Python laws.
            # The supported Windows package includes the matching extension;
            # an older/missing binary is an installation failure, not another
            # live simulation authority.
            if sys.platform != 'win32':
                return None
            raise RuntimeError('The matching native simulation is unavailable')
        return cls(runtime, backend, generation)

    def close(self):
        """Retire once, including after a partially constructed stage."""
        handle = self.handle
        if handle is None:
            return
        self.handle = None
        failure = None
        try:
            for stage in (self.weapons, self.motion, self.control,
                          self.navigation):
                detach = getattr(stage, 'detach', None)
                if not callable(detach):
                    continue
                try:
                    detach()
                except Exception as error:
                    if failure is None:
                        failure = error
        finally:
            self.backend.sim_close(handle)
        if failure is not None:
            raise failure

    def matches(self, runtime):
        return (self.handle is not None and
                runtime.round_id == self.round_id and
                runtime.is_authority() and not runtime.finished)
