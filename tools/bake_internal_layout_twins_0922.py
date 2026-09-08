#!/usr/bin/env python3
"""Bake the table of #1513 vehicles that share a profiled vehicle's interior.

``internal_layout_profiles`` stores an interior layout for 251 vehicle
identities.  The pinned client lists 680, and many of the rest are not new
tanks: they are ``_IGR``, ``_bot``, ``_bootcamp``, ``_training``, ``_fallout``
and ``_CN`` catalogue entries, or renames, that point at the very same
``collision_client`` models and carry the very same crew roster as a vehicle
that already has a layout.  Without an entry they build no interior geometry
at all, so their ammunition rack, engine, fuel tank and crew cannot be hit.

A listed vehicle without a layout inherits another listed vehicle's layout
when all three of these hold:

1. the set of ``collision_client`` model directories referenced by its item
   definition is identical;
2. the crew roster -- the role tuples in slot order -- is identical;
3. exactly one profiled vehicle satisfies 1 and 2.

That is sufficient because a profile stores normalized fractions per parent
component and ``internal_geometry.fit_target`` resolves them against the
mounted component's own collision bounds.  Two definitions that share the
model directories and the roster describe one physical tank; offering a
different installable turret or gun does not change where the interior sits
inside whichever component is actually mounted.  Rule 3 rejects a definition
that borrows a whole unrelated vehicle's art, such as ``usa:A08_T23``, which
matches both ``germany:G79_Pz_IV_AusfGH`` and ``usa:A15_T57``.

No interior geometry is authored here.  A twin receives the donor's zones and
the donor's own recorded confidence, nothing more.

    python3 tools/bake_internal_layout_twins_0922.py "$WOT_0922_CLIENT"
"""

import base64
import os
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from packed_xml import read_packed_xml, TYPE_COMPRESSED_STRING, TYPE_ELEMENT

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src' / 'res' / 'scripts' / 'client'))
from gui.mods.offline_lan_0922 import internal_hit_layouts


TARGET_VERSION = '0.9.22.0.1'
TARGET_BUILD = '1513'
SCRIPTS_PACKAGE = 'res/packages/scripts.pkg'
COLLISION_SEGMENT = '/collision_client/'
OUTPUT_PATH = (ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods' /
               'offline_lan_0922' / 'internal_layout_twins.py')

HEADER = '''# -*- coding: utf-8 -*-
"""Generated table of #1513 vehicles that share a profiled vehicle's interior.

Do not edit by hand.  Run
``tools/bake_internal_layout_twins_0922.py "$WOT_0922_CLIENT"`` to regenerate
it against the pinned client.

Each entry maps a listed vehicle with no interior layout of its own to the one
profiled vehicle that references the same ``collision_client`` model
directories and carries the same crew roster: the same physical tank under a
second catalogue identity.  The donor's layout, and the donor's own recorded
confidence, apply unchanged.  No geometry is authored here.
"""

CLIENT_VERSION = %(version)r
CLIENT_BUILD = %(build)r
CATALOGUE_SIZE = %(catalogue_size)d
PROFILED_COUNT = %(profiled)d
TWIN_COUNT = %(twins)d

PROFILE_TWINS_0922 = {
'''

FOOTER = '''}
'''


def _client_identity(client_root):
    text = (client_root / 'version.xml').read_text(encoding='utf-8')
    match = re.search(r'v\.([^\s]+)\s+#(\d+)', text)
    if not match:
        raise SystemExit('unrecognized version.xml value')
    if match.group(1) != TARGET_VERSION or match.group(2) != TARGET_BUILD:
        raise SystemExit('client is not %s #%s' % (TARGET_VERSION, TARGET_BUILD))
    return match.group(1), match.group(2)


def _text(value):
    if value.value_type == TYPE_COMPRESSED_STRING:
        return base64.b64encode(value.value).decode('ascii')
    return value.value.decode('ascii')


def _section(node, name):
    for key, value in node.children:
        if key == name:
            return value
    return None


def _collision_directories(definition):
    """Every directory holding a client collision model this vehicle mounts."""
    directories = set()

    def walk(node):
        for key, value in node.children:
            if value.value_type == TYPE_ELEMENT:
                walk(value.value)
            elif key == b'collisionModelClient':
                path = _text(value)
                if COLLISION_SEGMENT not in path:
                    raise SystemExit('unexpected collision path: ' + path)
                directories.add(path.split(COLLISION_SEGMENT, 1)[0])
    walk(definition)
    return frozenset(directories)


def _crew_roster(definition, vehicle):
    crew = _section(definition, b'crew')
    if crew is None or crew.value_type != TYPE_ELEMENT:
        raise SystemExit('invalid crew section: ' + vehicle)
    return tuple((key.decode('ascii'),) + tuple(_text(value).split())
                 for key, value in crew.value.children)


def _authored_roster(vehicle):
    """The roster of this vehicle's own authored layout, or None.

    Deliberately bypasses the twin table this tool generates: reading the full
    resolution chain would count a previously baked twin as already profiled
    and re-bake an empty table.
    """
    key = internal_hit_layouts._profile_key(vehicle)
    if key is None:
        return None
    unused_key, profile = internal_hit_layouts._profile_for_key(key)
    record = internal_hit_layouts._profile_record(profile)
    return None if record is None else record['crew_roles']


def _resolved_roster(vehicle):
    """The roster of the layout the port resolves, twin table included."""
    unused_key, compiled = internal_hit_layouts._compiled_profile(vehicle)
    record = internal_hit_layouts._profile_record(compiled)
    return None if record is None else record['crew_roles']


def scan(client_root):
    package = client_root / SCRIPTS_PACKAGE
    vehicles = {}
    with zipfile.ZipFile(str(package)) as archive:
        listings = sorted(name for name in archive.namelist()
                          if name.startswith('scripts/item_defs/vehicles/')
                          and name.endswith('/list.xml')
                          and name.count('/') == 4)
        for listing in listings:
            nation = listing.split('/')[-2]
            directory = listing.rsplit('/', 1)[0]
            catalog = read_packed_xml(archive.read(listing))
            for raw_name, entry in catalog.children:
                name = raw_name.decode('ascii')
                if ':' in name or entry.value_type != TYPE_ELEMENT:
                    continue
                vehicle = nation + ':' + name
                definition = read_packed_xml(
                    archive.read(directory + '/' + name + '.xml'))
                vehicles[vehicle] = (
                    _collision_directories(definition),
                    _crew_roster(definition, vehicle))
    return vehicles


def derive(vehicles):
    profiled = {}
    unprofiled = {}
    for vehicle, (directories, roster) in vehicles.items():
        authored = _authored_roster(vehicle)
        if authored is not None and authored == roster:
            profiled[vehicle] = (directories, roster)
        else:
            unprofiled[vehicle] = (directories, roster)

    donors_by_directory = {}
    for vehicle, (directories, unused_roster) in profiled.items():
        for directory in directories:
            donors_by_directory.setdefault(directory, set()).add(vehicle)

    twins = {}
    rejected = {}
    for vehicle in sorted(unprofiled):
        directories, roster = unprofiled[vehicle]
        candidates = set()
        for directory in directories:
            candidates |= donors_by_directory.get(directory, set())
        donors = sorted(donor for donor in candidates
                        if profiled[donor][0] == directories
                        and profiled[donor][1] == roster)
        if len(donors) == 1:
            twins[vehicle] = donors[0]
        elif donors:
            rejected[vehicle] = donors
    return profiled, twins, rejected


def verify(twins, vehicles):
    """Every donor must resolve to a layout whose roster matches the twin's.

    62 of the donors are only reachable through ``PROFILE_ALIASES_0922``, so
    this exercises the whole lookup chain rather than the profile table alone.
    A donor that is later renamed out of that chain fails the bake here.
    """
    for vehicle in sorted(twins):
        donor = twins[vehicle]
        donor_roster = _authored_roster(donor)
        if donor_roster is None:
            raise SystemExit('donor resolves to no layout: %s -> %s'
                             % (vehicle, donor))
        if donor_roster != vehicles[vehicle][1]:
            raise SystemExit('donor roster differs: %s -> %s' % (vehicle, donor))
        if _authored_roster(vehicle) is not None:
            raise SystemExit('vehicle already has a layout: ' + vehicle)
        if _resolved_roster(vehicle) not in (None, vehicles[vehicle][1]):
            raise SystemExit('stale twin resolution: ' + vehicle)


def render(version, build, catalogue_size, profiled, twins):
    text = HEADER % {'version': version, 'build': build,
                     'catalogue_size': catalogue_size,
                     'profiled': profiled, 'twins': len(twins)}
    for vehicle in sorted(twins):
        text += '    %r: %r,\n' % (
            internal_hit_layouts._profile_key(vehicle),
            internal_hit_layouts._profile_key(twins[vehicle]))
    return text + FOOTER


def main():
    if len(sys.argv) not in (2, 3):
        raise SystemExit('usage: bake_internal_layout_twins_0922.py '
                         '<client root> [output path]')
    client_root = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve() if len(sys.argv) == 3 else OUTPUT_PATH
    version, build = _client_identity(client_root)
    vehicles = scan(client_root)
    profiled, twins, rejected = derive(vehicles)
    verify(twins, vehicles)
    output.write_text(
        render(version, build, len(vehicles), len(profiled), twins),
        encoding='utf-8')
    print('catalogue: %d vehicles' % len(vehicles))
    print('already profiled: %d' % len(profiled))
    print('twins: %d' % len(twins))
    for vehicle in sorted(twins):
        print('  %s <- %s' % (vehicle, twins[vehicle]))
    print('ambiguous donors, left uncovered: %d' % len(rejected))
    for vehicle in sorted(rejected):
        print('  %s <- %s' % (vehicle, ' '.join(rejected[vehicle])))
    print('still without an interior layout: %d'
          % (len(vehicles) - len(profiled) - len(twins)))
    print('written: %s' % output)


if __name__ == '__main__':
    main()
