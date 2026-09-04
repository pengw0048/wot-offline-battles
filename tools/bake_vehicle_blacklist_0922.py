#!/usr/bin/env python3
"""Bake the list of #1513 vehicles whose client resources are absent.

The pinned client advertises vehicle types in ``list.xml`` whose art was never
shipped.  Loading such a type builds a valid descriptor, then fails inside
``BSPModel.loadBspModel`` and aborts the round.  This tool reads every vehicle
item definition, checks each required client resource against the exact package
members, and writes the generated blacklist module the port imports.

    python3 tools/bake_vehicle_blacklist_0922.py "$WOT_0922_CLIENT"
"""

import os
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from packed_xml import read_packed_xml, TYPE_ELEMENT


TARGET_VERSION = '0.9.22.0.1'
TARGET_BUILD = '1513'
SCRIPTS_PACKAGE = 'res/packages/scripts.pkg'
NATIONS = ('ussr', 'germany', 'usa', 'china', 'france', 'uk', 'japan',
           'czech', 'sweden', 'poland')
# The two resource families a client-only vehicle presentation needs: the BSP
# collision model every hit tester loads, and the undamaged visual model the
# compound assembler and the hangar build.
REQUIRED_SUFFIXES = (
    'hitTester/collisionModelClient',
    'models/undamaged',
)
OUTPUT_PATH = (Path(__file__).resolve().parent.parent / 'src' / 'res' /
               'scripts' / 'client' / 'gui' / 'mods' / 'offline_lan_0922' /
               'vehicle_blacklist.py')

HEADER = '''# -*- coding: utf-8 -*-
"""Generated catalogue of #1513 vehicles this client cannot load.

Do not edit by hand.  Run
``tools/bake_vehicle_blacklist_0922.py "$WOT_0922_CLIENT"`` to
regenerate it against the pinned client.

Each entry lists the exact resource paths the vehicle item definition
references and no package member provides.
"""

CLIENT_VERSION = %(version)r
CLIENT_BUILD = %(build)r
CATALOGUE_SIZE = %(catalogue_size)d

UNUSABLE_VEHICLES = {
'''

FOOTER = '''}


def is_unusable(name):
    """Return whether the pinned client lacks this vehicle's resources."""
    return str(name or '') in UNUSABLE_VEHICLES


def missing_resources(name):
    """Return the absent resource paths recorded for one vehicle name."""
    return UNUSABLE_VEHICLES.get(str(name or ''), ())
'''


def _client_identity(client_root):
    text = (client_root / 'version.xml').read_text(encoding='utf-8')
    match = re.search(r'v\.([^\s]+)\s+#(\d+)', text)
    if not match:
        raise SystemExit('unrecognized version.xml value')
    if match.group(1) != TARGET_VERSION or match.group(2) != TARGET_BUILD:
        raise SystemExit('client is not %s #%s' % (TARGET_VERSION, TARGET_BUILD))
    return match.group(1), match.group(2)


def _resource_members(client_root):
    """Every resource path the exact client can resolve, lower-cased."""
    members = set()
    packages = client_root / 'res' / 'packages'
    for package in sorted(packages.iterdir()):
        if package.suffix != '.pkg':
            continue
        with zipfile.ZipFile(str(package)) as archive:
            members.update(archive.namelist())
    resources = client_root / 'res'
    for base, unused_dirs, files in os.walk(str(resources)):
        if os.path.join('res', 'packages') in base:
            continue
        relative = os.path.relpath(base, str(resources))
        for name in files:
            members.add(os.path.join(relative, name).replace('\\', '/'))
    return set(_normalize(member) for member in members)


def _normalize(path):
    """Collapse the duplicate separators some item definitions carry."""
    return re.sub(r'/+', '/', path.strip().lstrip('/')).lower()


def _values(element, path=''):
    for name, value in element.children:
        if isinstance(name, bytes):
            name = name.decode('ascii', 'replace')
        child_path = path + '/' + name
        if value.value_type == TYPE_ELEMENT:
            for row in _values(value.value, child_path):
                yield row
        else:
            yield child_path, value.value


def _vehicle_names(archive, nation):
    listing = read_packed_xml(
        archive.read('scripts/item_defs/vehicles/%s/list.xml' % nation))
    for name, unused_value in listing.children:
        if isinstance(name, bytes):
            name = name.decode('ascii', 'replace')
        # The packed dictionary carries the XML namespace declarations too.
        if ':' in name:
            continue
        yield name


def scan(client_root):
    """Return the catalogue size and every vehicle missing a resource."""
    members = _resource_members(client_root)
    package = client_root / SCRIPTS_PACKAGE
    unusable = {}
    catalogue_size = 0
    with zipfile.ZipFile(str(package)) as archive:
        for nation in NATIONS:
            for vehicle in _vehicle_names(archive, nation):
                catalogue_size += 1
                name = '%s:%s' % (nation, vehicle)
                member = 'scripts/item_defs/vehicles/%s/%s.xml' % (
                    nation, vehicle)
                try:
                    definition = read_packed_xml(archive.read(member))
                except KeyError:
                    unusable[name] = (member,)
                    continue
                missing = set()
                for path, value in _values(definition):
                    if not isinstance(value, bytes) or not value:
                        continue
                    for suffix in REQUIRED_SUFFIXES:
                        if not path.endswith(suffix):
                            continue
                        resource = value.decode('ascii', 'replace')
                        if _normalize(resource) not in members:
                            missing.add(resource)
                if missing:
                    unusable[name] = tuple(sorted(missing))
    return catalogue_size, unusable


def render(version, build, catalogue_size, unusable):
    text = HEADER % {'version': version, 'build': build,
                     'catalogue_size': catalogue_size}
    for name in sorted(unusable):
        text += '    %r: (\n' % name
        for resource in unusable[name]:
            text += '        %r,\n' % resource
        text += '    ),\n'
    return text + FOOTER


def main():
    if len(sys.argv) != 2:
        raise SystemExit('usage: bake_vehicle_blacklist_0922.py <client root>')
    client_root = Path(sys.argv[1]).resolve()
    version, build = _client_identity(client_root)
    catalogue_size, unusable = scan(client_root)
    OUTPUT_PATH.write_text(
        render(version, build, catalogue_size, unusable), encoding='utf-8')
    print('catalogue: %d vehicles' % catalogue_size)
    print('unusable: %d vehicles' % len(unusable))
    for name in sorted(unusable):
        for resource in unusable[name]:
            print('  %s %s' % (name, resource))
    print('written: %s' % OUTPUT_PATH)


if __name__ == '__main__':
    main()
