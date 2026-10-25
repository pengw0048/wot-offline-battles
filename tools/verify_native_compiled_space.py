#!/usr/bin/env python3
"""Compare native #1513 compiled-space metadata against the Python reader.

Client resources are read without starting the game. Each space.bin is copied
to a temporary file for the standalone CLI. This checks the BWTB directory and
the decoded BWT2 prefix, not geometry, rendering, collision or resource loading.
"""

import argparse
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import zipfile

from inspect_client import inspect_client
from space_bin_0922 import CompiledSpace


def expected_metadata(payload):
    """Use the existing independent reader, preserving authored record order."""
    space = CompiledSpace(payload)
    terrain = space.terrain_info_0920()
    return {
        'format': 'wot-standalone-space-metadata',
        'version': struct.unpack_from('<I', payload, 4)[0],
        'sections': [
            {'name': section.name, 'version': section.version,
             'offset': section.offset, 'size': section.size}
            for section in space.sections.values()
        ],
        'terrain': {
            'chunk_size': terrain.chunk_size,
            'bounds': list(terrain.bounds),
            'texture_ids': list(struct.unpack_from('<3I', space.section_data('BWT2'), 24)),
            'blocks': [list(block) for block in terrain.chunks],
            'decoded_bytes': 44 + 8 * len(terrain.chunks),
        },
    }


def compare_metadata(actual, expected, field='metadata'):
    """Compare all decoded fields; extra CLI metadata does not imply coverage."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            raise ValueError('%s is not an object' % field)
        for key, value in expected.items():
            if key not in actual:
                raise ValueError('%s.%s is missing' % (field, key))
            compare_metadata(actual[key], value, '%s.%s' % (field, key))
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise ValueError('%s has the wrong array shape' % field)
        for index, value in enumerate(expected):
            compare_metadata(actual[index], value, '%s[%d]' % (field, index))
    elif isinstance(expected, float):
        # The CLI emits enough decimal digits to round-trip float32. Comparing
        # Python doubles directly would reject correct nonintegral float32 data.
        try:
            matches = (type(actual) in (int, float) and math.isfinite(actual)
                       and struct.pack('<f', actual) == struct.pack('<f', expected))
        except (OverflowError, struct.error):
            matches = False
        if not matches:
            raise ValueError('%s differs: expected %r, got %r' % (field, expected, actual))
    elif type(actual) is not type(expected) or actual != expected:
        raise ValueError('%s differs: expected %r, got %r' % (field, expected, actual))


def verify(client_root, native_core):
    identity = inspect_client(client_root)
    client_root = Path(identity['clientRoot'])
    native_core = Path(native_core).expanduser().resolve()
    if not native_core.is_file():
        raise ValueError('native core executable does not exist: %s' % native_core)

    totals = {'packages_scanned': 0, 'scenes_compared': 0,
              'sections_compared': 0, 'terrain_blocks_compared': 0}
    packages = client_root / 'res' / 'packages'
    with tempfile.TemporaryDirectory(prefix='wot-native-space-') as temporary:
        extracted = Path(temporary) / 'space.bin'
        for package in sorted(packages.glob('*.pkg')):
            if not package.name[0].isdigit():
                continue
            totals['packages_scanned'] += 1
            with zipfile.ZipFile(package) as archive:
                for member in sorted(archive.infolist(), key=lambda item: item.filename):
                    if not member.filename.endswith('/space.bin') or member.is_dir():
                        continue
                    label = '%s:%s' % (package.name, member.filename)
                    try:
                        payload = archive.read(member)
                        expected = expected_metadata(payload)
                        extracted.write_bytes(payload)
                        process = subprocess.run(
                            [str(native_core), 'inspect-space', str(extracted)],
                            capture_output=True, text=True, check=False, timeout=60)
                        if process.returncode:
                            raise ValueError('native core exited %d: %s' %
                                             (process.returncode, process.stderr.strip()))
                        compare_metadata(json.loads(process.stdout), expected)
                    except (OSError, ValueError, struct.error, subprocess.TimeoutExpired,
                            zipfile.BadZipFile) as error:
                        raise ValueError('%s: %s' % (label, error)) from error
                    totals['scenes_compared'] += 1
                    totals['sections_compared'] += len(expected['sections'])
                    totals['terrain_blocks_compared'] += len(expected['terrain']['blocks'])
    if not totals['scenes_compared']:
        raise ValueError('no compiled scenes were found in the numbered client packages')
    return {
        'format': 'wot-native-compiled-space-verification',
        'client_version': identity['version'],
        'client_build': identity['build'],
        'native_core': str(native_core),
        **totals,
        'verified': 'BWTB directory and BWT2 terrain metadata prefix',
        'unverified': ['remaining BWT2 data', 'geometry', 'resource references',
                       'collision', 'rendering', 'Windows gameplay'],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-root', required=True, type=Path)
    parser.add_argument('--native-core', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = verify(args.client_root, args.native_core)
    except (OSError, ValueError, struct.error, zipfile.BadZipFile) as error:
        parser.exit(1, 'Compiled-space verification failed: %s\n' % error)
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
