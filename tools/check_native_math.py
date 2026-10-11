"""Python 2 host conformance for the direct native geometry extension.

Usage: python2.7 tools/check_native_math.py EXTENSION [CLIENT_SCRIPTS]
"""
from __future__ import print_function
import copy
import gc
import imp
import math
import os
import sys
import types
client_scripts = (sys.argv[2] if len(sys.argv) > 2 else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'src', 'res', 'scripts', 'client'))
sys.path.insert(0, client_scripts)
# Retail supplies these package parents; the source tree omits their stock
# __init__.py files. Supply only the package paths for this offline import.
for name in ('gui', 'gui.mods'):
    package = types.ModuleType(name)
    package.__path__ = [os.path.join(client_scripts, *name.split('.'))]
    sys.modules[name] = package
from gui.mods.offline_lan_0922 import tank_collision as contact
from gui.mods.offline_lan_0922 import native_math
backend = imp.load_dynamic('offline_math_batch_native', sys.argv[1])

def body(identity, x=0., z=0., **changes):
    result = dict(id=identity, x=x, y=0., z=z, yaw=0., shape=(1.5, 3.5, -.8, 2.))
    result.update(changes)
    return result

def close(actual, expected):
    if isinstance(expected, tuple):
        assert type(actual) is tuple and len(actual) == 2
        for a, e in zip(actual, expected): close(a, e)
    else:
        assert type(actual) is float and not math.isnan(actual) and not math.isinf(actual)
        assert abs(actual-expected) <= 1.e-7 * max(1., abs(expected)), (actual, expected)

owner = body(1)
scenes = [
    (owner, (8., 8.), [body(2, 3.), body(3, z=12.)]),
    (owner, (0., 20.), [body(2, z=10.)]),
    (owner, (3., 2.), [body(2, z=10., y=20.)]),
    (body(1, yaw=.4, pitch=.15, roll=-.2, shape=[1.5, 3.5, -.8, 2.]),
     [6., -3.], [body(2, 4., -4., yaw=-.3)]),
    (owner, (0., 0.), []),
]
count = 0
retained = []
for own, move, peers in scenes:
    before = copy.deepcopy((own, move, peers))
    native_math._backend = None
    native_math._attempted = True
    first = contact.translation_fraction(own, move, peers)
    expected = contact.slide_translation(own, move, peers)
    close(backend.translation_fraction(own, move, peers), first)
    close(backend.slide_translation(own, move, peers), expected)
    close(backend.slide_translation(own, move, peers, first), expected)
    retained.append(backend.slide_translation(own, move, peers))
    assert (own, move, peers) == before
    native_math._backend = backend
    close(contact.translation_fraction(own, move, peers), first)
    close(contact.slide_translation(own, move, peers), expected)
    count += 5

for peers, pivot, movement in [([body(2, 3.)], 0., (0., 0.)),
                              ([body(2, 5., 3., yaw=.3)], 1.2, (2., 1.)),
                              ([body(2, 3., y=20.)], 0., (0., 0.))]:
    args = ((0., 0., 0.), 0., .75, owner['shape'], peers, pivot, movement)
    native_math._backend = None
    expected = contact.rotation_fraction(*args)
    close(backend.rotation_fraction(*args), expected)
    native_math._backend = backend
    close(contact.rotation_fraction(*args), expected)
    count += 2

class Conversion(object):
    def __float__(self): raise AssertionError('must not call custom conversion')
key_hash = next(value for value in range(100) if value not in map(hash, owner))
class Key(object):
    def __hash__(self): return key_hash
    def __eq__(self, other): raise AssertionError('must not call custom equality')
custom_keys = body(1)
custom_keys[Key()] = 1
unsupported = [body(1, x=Conversion()), body(1, id=long(1)),
               body(1, shape=[1.]), custom_keys]
for own in unsupported:
    assert backend.translation_fraction(own, (2., 1.), []) is None
for method, args in [(backend.translation_fraction, ()),
                     (backend.slide_translation, (owner, [1.], [])),
                     (backend.rotation_fraction, ((0.,), 0., 1., owner['shape'], []))]:
    assert method(*args) is None

# Large clear travel does not impose a separate native physics work cap.
close(backend.rotation_fraction((0., 0., 0.), 0., 0., owner['shape'],
                                [], 0., (60000., 0.)), 1.)
count += 1

# Repeated native calls neither retain borrowed inputs nor overwrite old results.
def borrowed_counts():
    return [sys.getrefcount(item) for item in (owner, scenes[0][2], owner['shape'])]
gc.collect()  # Collect recursive Python reference-search closures first.
references = borrowed_counts()
saved = copy.deepcopy(retained)
for unused in range(200): backend.slide_translation(owner, (8., 8.), scenes[0][2])
gc.collect()
assert references == borrowed_counts(), (references, borrowed_counts())
assert retained == saved
assert native_math.snapshot()['fallbacks'] == 0
print('Direct native representative conformance passed: %d comparisons' % count)
