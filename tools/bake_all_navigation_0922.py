#!/usr/bin/env python3
"""Atomically bake and validate all supported #1513 standard-map graphs."""

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import os
import shutil
import sys
import tempfile


TOOL_ROOT = os.path.dirname(os.path.abspath(__file__))
PORT_ROOT = os.path.dirname(TOOL_ROOT)
SCHEMA_ROOT = os.path.join(
    PORT_ROOT, 'src', 'res', 'scripts', 'client', 'gui', 'mods',
    'offline_lan_0922')
for path in (TOOL_ROOT, SCHEMA_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

import bake_navigation_0922 as baker
import navigation_graph_schema as schema


def _bake_one(client_root, output_root, map_name):
    path = os.path.join(output_root, map_name + '.json')
    graph = baker.bake_map_graph(client_root, map_name, path)
    schema.validate_graph(graph, map_name)
    with open(path, 'rb') as stream:
        digest = hashlib.sha256(stream.read()).hexdigest()
    return map_name, digest


def _write_manifest(output_root, digests):
    records = []
    for map_name in schema.SUPPORTED_MAPS:
        records.append({
            'map': map_name,
            'file': map_name + '.json',
            'sha256': digests[map_name],
        })
    manifest = {
        'format': schema.MANIFEST_FORMAT,
        'version': schema.FORMAT_VERSION,
        'game_version': schema.GAME_VERSION,
        'maps': records,
    }
    path = os.path.join(output_root, 'manifest.json')
    with open(path, 'wb') as stream:
        stream.write((json.dumps(
            manifest, indent=2, sort_keys=True) + '\n').encode('utf-8'))


def bake_all(client_root, output_root, jobs=1):
    client_root = os.path.abspath(client_root)
    output_root = os.path.abspath(output_root)
    parent = os.path.dirname(output_root)
    if not os.path.isdir(parent):
        raise ValueError('navigation output parent does not exist')
    os.makedirs(output_root, exist_ok=True)
    expected_files = set(name + '.json' for name in schema.SUPPORTED_MAPS)
    actual_files = set(
        name for name in os.listdir(output_root)
        if name.endswith('.json') and name != 'manifest.json')
    if actual_files and actual_files != expected_files:
        raise ValueError('existing navigation output set is incomplete or extra')
    jobs = max(1, min(int(jobs), len(schema.SUPPORTED_MAPS)))
    with tempfile.TemporaryDirectory(
            prefix='offline-lan-0922-nav-') as staging:
        digests = {}
        with ProcessPoolExecutor(max_workers=jobs) as executor:
            futures = {
                executor.submit(
                    _bake_one, client_root, staging, map_name): map_name
                for map_name in schema.SUPPORTED_MAPS
            }
            for future in as_completed(futures):
                submitted_map = futures[future]
                try:
                    map_name, digest = future.result()
                except Exception as error:
                    raise ValueError('%s: %s' % (submitted_map, error))
                digests[map_name] = digest
                print('baked %s %s' % (map_name, digest[:12]), flush=True)
        if set(digests) != set(schema.SUPPORTED_MAPS):
            raise ValueError('navigation batch did not produce every map')
        _write_manifest(staging, digests)
        # Publish graph files first and the checksum manifest last. A running
        # client therefore sees either the old complete batch or rejects the
        # transient checksum mismatch; it never accepts mixed data.
        for map_name in schema.SUPPORTED_MAPS:
            shutil.copy2(
                os.path.join(staging, map_name + '.json'), output_root)
        shutil.copy2(os.path.join(staging, 'manifest.json'), output_root)
    return digests


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', required=True,
                        help='Pinned #1513 client root')
    parser.add_argument('--output-dir', required=True,
                        help='Destination navgraphs directory')
    parser.add_argument('--jobs', type=int, default=1,
                        help='Parallel map bakes (default: 1)')
    args = parser.parse_args(argv)
    try:
        digests = bake_all(args.client, args.output_dir, args.jobs)
    except (OSError, ValueError, baker.CompiledSpaceError) as error:
        print('FAILED navigation batch: %s' % error, file=sys.stderr)
        return 1
    print('validated navigation batch: %d standard maps' % len(digests))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
