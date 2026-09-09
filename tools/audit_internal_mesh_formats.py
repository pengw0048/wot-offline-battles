#!/usr/bin/env python3
"""Compare actual indexed surfaces across Console HKX and BigWorld resources.

Later revisions may change a model. Report every mismatch; do not treat all
similarly named models as equivalent. Uses the baker's cache and private key.
"""
import argparse
from collections import Counter
import io
import json
from pathlib import Path

import pyzipper
import hkx_module_geometry as hkx
import bw_primitives_geometry as bw
from bake_internal_layout_console_0922 import fetch, read_password

PROBE_IDS = (7169, 545, 609, 9297, 9505, 1313, 20225, 49, 6929)


def compare(cache, password):
    rows, requests = [], []
    for archive_id in PROBE_IDS:
        old = fetch(cache, 'console4.3', archive_id, requests)
        new = fetch(cache, 'console4.10', archive_id, requests)
        if old is None or new is None:
            rows.append({'id': archive_id, 'error': 'one or both source archives unavailable'})
            continue
        with pyzipper.AESZipFile(io.BytesIO(old)) as first, pyzipper.AESZipFile(io.BytesIO(new)) as second:
            first.setpassword(password)
            second.setpassword(password)
            names = {n.rsplit('/', 1)[-1].lower(): n for n in second.namelist()}
            for member in first.namelist():
                equivalent = names.get(member.rsplit('/', 1)[-1].lower().replace('.hkx', '.primitives'))
                if equivalent is None:
                    continue
                try:
                    a = hkx.module_surfaces(first.read(member))
                    b = bw.module_surfaces(second.read(equivalent))
                except Exception as error:
                    rows.append({'id': archive_id, 'member': member, 'error': str(error)})
                    continue
                for name, surface in a.items():
                    if name not in b:
                        continue
                    other = b[name]
                    mapping, errors = [], []
                    for vertex in surface['vertices']:
                        distance, index = min((sum((vertex[k]-v[k])**2 for k in range(3)), i)
                                              for i, v in enumerate(other['vertices']))
                        mapping.append(index)
                        errors.append(distance**.5)
                    expected = Counter(tuple(sorted(f)) for f in other['triangles'] if len(set(f)) == 3)
                    actual = Counter(tuple(sorted(mapping[i] for i in f)) for f in surface['triangles']
                                     if len(set(mapping[i] for i in f)) == 3)
                    rows.append({'id': archive_id, 'member': member, 'surface': name,
                        'vertices': len(surface['vertices']), 'triangles': len(surface['triangles']),
                        'vertex_error_m': max(errors), 'same_triangles': actual == expected})
    return {'scope': 'Cross-format source comparison, not PC-server/native equivalence',
        'matched_surfaces': sum(r.get('same_triangles', False) and r['vertex_error_m'] < 1e-5 for r in rows),
        'requests': requests, 'surfaces': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', required=True)
    parser.add_argument('--password-file', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = compare(Path(args.cache), read_password(args).encode('utf-8'))
    Path(args.output).write_text(json.dumps(report, indent=1, sort_keys=True)+'\n')
    print('Cross-format matched surfaces: %d' % report['matched_surfaces'])


if __name__ == '__main__':
    main()
