#!/usr/bin/env python3
"""Inventory reconstructed internal-module coverage against a scripts.pkg.

This is a read-only resource audit, not client identity verification or a
collision-mesh audit. Run inspect_client.py on a complete client first when
one is available. No inferred layout is generated for an uncovered vehicle.
"""

import argparse
import base64
from collections import Counter
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'res' / 'scripts' / 'client'))
from gui.mods.offline_lan_0922 import internal_hit_layouts
from gui.mods.offline_lan_0922 import vehicle_blacklist
from packed_xml import read_packed_xml, TYPE_COMPRESSED_STRING, TYPE_ELEMENT


def _text(value):
    if value.value_type == TYPE_COMPRESSED_STRING:
        return base64.b64encode(value.value).decode('ascii')
    return value.value.decode('ascii')


def scan(package_path):
    rows = []
    with zipfile.ZipFile(package_path) as archive:
        listings = sorted(name for name in archive.namelist()
                          if name.startswith('scripts/item_defs/vehicles/')
                          and name.endswith('/list.xml')
                          and name.count('/') == 4)
        for listing in listings:
            nation = listing.split('/')[-2]
            directory = listing.rsplit('/', 1)[0]
            catalog = read_packed_xml(archive.read(listing))
            for raw_name, unused in catalog.children:
                name = raw_name.decode('ascii')
                if ':' in name:
                    continue
                vehicle = nation + ':' + name
                definition = read_packed_xml(archive.read(
                    directory + '/' + name + '.xml'))
                crew = next(value for key, value in definition.children
                            if key == b'crew')
                if crew.value_type != TYPE_ELEMENT:
                    raise ValueError('invalid crew section: ' + vehicle)
                roles = tuple((key.decode('ascii'),) + tuple(_text(value).split())
                              for key, value in crew.value.children)
                profile_key, compiled = internal_hit_layouts._compiled_profile(vehicle)
                profile = internal_hit_layouts._profile_record(compiled)
                if profile is None:
                    status = 'missing_profile'
                elif profile['crew_roles'] != roles:
                    status = 'crew_mismatch'
                else:
                    status = 'profile_matched'
                rows.append({
                    'vehicle': vehicle,
                    'status': status,
                    'profile_key': profile_key,
                    'crew_roles': roles,
                    'blacklisted_resources': vehicle_blacklist.is_unusable(vehicle),
                })
    return {
        'scope': 'listed vehicle descriptors and reconstructed profile bindings only',
        'collision_geometry_verified': False,
        'summary': dict(Counter(row['status'] for row in rows)),
        'vehicles': rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scripts_package', help='Path to the client scripts.pkg')
    args = parser.parse_args()
    print(json.dumps(scan(args.scripts_package), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
