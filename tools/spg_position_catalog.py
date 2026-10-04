#!/usr/bin/env python3
"""Validate/export the source-backed SPG area library. No network at runtime."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PREFIX = ROOT / 'src/res/scripts/client'
sys.path.insert(0, str(PREFIX))
from gui.mods.offline_lan_0922 import spg_positions

SOURCE = ROOT / 'spg_positions/positions_0922.json'
OUTPUT = PREFIX / 'gui/mods/offline_lan_0922/spg_position_data.py'


def generated(text):
    # Do not embed executable input or unterminated string literals.
    data = json.loads(text)
    spg_positions.validate_catalog(data)
    canonical = json.dumps(data, ensure_ascii=True, sort_keys=True, indent=2) + '\n'
    if "'''" in canonical:
        raise ValueError('catalog text contains a reserved string delimiter')
    return ('from __future__ import absolute_import\n\n'
            '"""Generated from spg_positions/positions_0922.json; do not edit by hand."""\n\n'
            'import json\n\nCATALOG = json.loads(r\'\'\'\n' + canonical + "'''\n)\n")


def audit_graphs(data):
    report = {'catalog': data['revision'], 'game_version': data['game_version'],
              'native_gameplay_tested': False, 'maps': []}
    for path in sorted((ROOT / 'navgraphs').glob('*.json')):
        if path.name == 'manifest.json':
            continue  # Resource index, not another playable map.
        if path.stem not in data['maps']:
            report['maps'].append({'map': path.stem, 'status': 'source_not_catalogued'})
            continue
        graph = json.loads(path.read_text())
        states = []
        # Clearly labelled generic footprint, not an asserted real FV3805.
        for team in (1, 2):
            for slot in range(3):
                x, y, z, yaw = graph['spawn_formations'][str(team)][slot]
                states.append({'id': team * 10 + slot, 'team': team,
                               'vehicle': 'audit:generic_SPG', 'x': x, 'y': y, 'z': z,
                               'yaw': yaw, 'profile': {'class_tag': 'SPG'},
                               'collision_shape': (1.8, 4.0, -0.8, 2.0)})
        plans, outcomes = spg_positions.assign_initial_positions(path.stem, graph, states, catalog=data)
        report['maps'].append({'map': path.stem, 'status': 'catalogued',
                              'fixture_half_width': 1.8, 'fixture_half_length': 4.0,
                              'outcomes': outcomes, 'plans': plans,
                              'note': 'Baked grid/corridor test; no native firing arc or gameplay acceptance.'})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--generate', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--audit', type=Path, help='Write all-map coverage and real-graph fixture results')
    args = parser.parse_args()
    text = args.source.read_text(encoding='utf8')
    output = generated(text)
    if args.generate:
        OUTPUT.write_text(output, encoding='utf8')
    if args.check:
        if OUTPUT.read_text(encoding='utf8') != output:
            raise SystemExit('Bundled SPG data differs; run --generate')
    if args.audit:
        args.audit.parent.mkdir(parents=True, exist_ok=True)
        args.audit.write_text(json.dumps(audit_graphs(json.loads(text)), indent=2, sort_keys=True) + '\n')
    print('PASS catalog schema/provenance and generated data:', len(json.loads(text)['maps']), 'maps')


if __name__ == '__main__':
    main()
