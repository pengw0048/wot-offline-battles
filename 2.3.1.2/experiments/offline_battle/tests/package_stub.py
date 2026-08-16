"""Load package modules under the client import path the sources use.

The sources import each other as `gui.mods.offline_battle_2312.<name>`,
which only exists inside the client. Tests register the real files under
that path so an import in one module reaches the sibling being tested.
"""
import importlib
import sys
import types
from pathlib import Path

PACKAGE = 'gui.mods.offline_battle_2312'
ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods' /
          'offline_battle_2312')


def _package():
    for name in ('gui', 'gui.mods', PACKAGE):
        sys.modules.setdefault(name, types.ModuleType(name))
    # A __path__ makes the stub a package, so a source file importing a
    # sibling resolves it through the normal machinery.
    sys.modules[PACKAGE].__path__ = [str(SOURCE)]
    return sys.modules[PACKAGE]


def load(name):
    """Import one package module, registering it for its siblings."""
    _package()
    return importlib.import_module(PACKAGE + '.' + name)


def stub(name, **attributes):
    """Register a stand-in for a sibling a test does not exercise."""
    full = PACKAGE + '.' + name
    module = types.ModuleType(full)
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules[full] = module
    setattr(_package(), name, module)
    return module
