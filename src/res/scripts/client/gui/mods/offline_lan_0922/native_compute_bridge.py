# -*- coding: utf-8 -*-
"""Load and validate the exact-build sweep preparation extension.

The extension is a sidecar next to the instance-guard bridge, loaded by path
because a native extension cannot be imported out of a ``.wotmod``.  It is
refused unless it exposes the reviewed methods, agrees with this port's buffer
size, and proves the interpreter's integer and tuple layout in this process
before any preparation runs.
"""

from __future__ import print_function

import os
import sys


NATIVE_MODULE_NAME = 'offline_compute_native'
NATIVE_FILENAME = NATIVE_MODULE_NAME + '.pyd'
GAME_VERSION_DIR = '0.9.22.0.1'
REQUIRED_METHODS = ('layout_self_test', 'prepare_sweep', 'buffer_values')
SELF_TEST_ARGUMENTS = (11, 22, 33)
SELF_TEST_RESULT = 112233

_module = None


def bridge_path(executable=None):
    executable = sys.executable if executable is None else executable
    game_root = os.path.dirname(os.path.abspath(executable))
    return os.path.join(
        game_root, 'mods', GAME_VERSION_DIR, NATIVE_FILENAME)


def load(path=None, imp_module=None):
    """Return the validated extension, or raise with the exact reason."""
    global _module
    if _module is not None:
        return _module
    path = bridge_path() if path is None else path
    if not os.path.isfile(path):
        raise ImportError('native compute bridge is missing: %s' % path)
    if imp_module is None:
        import imp as imp_module
    module = imp_module.load_dynamic(NATIVE_MODULE_NAME, path)
    _module = validate(module)
    return _module


def validate(module):
    """Refuse an extension that cannot prove the contract it will be given."""
    from gui.mods.offline_lan_0922 import world_collision_prep
    for name in REQUIRED_METHODS:
        if not callable(getattr(module, name, None)):
            raise ImportError('native compute bridge is missing %s' % name)
    result = int(module.layout_self_test(*SELF_TEST_ARGUMENTS))
    if result != SELF_TEST_RESULT:
        raise ImportError(
            'native compute bridge failed its layout self-test: %d' % result)
    values = int(module.buffer_values())
    if values != world_collision_prep.BUFFER_VALUES:
        raise ImportError(
            'native compute bridge expects %d buffer values, this port '
            'prepares %d' % (values, world_collision_prep.BUFFER_VALUES))
    return module


def unload():
    """Forget the loaded extension so a later battle can select again."""
    global _module
    _module = None
    sys.modules.pop(NATIVE_MODULE_NAME, None)
