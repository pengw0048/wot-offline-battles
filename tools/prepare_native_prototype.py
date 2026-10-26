#!/usr/bin/env python3
"""Prepare and run the Karelia/T-34 prototype from an owned #1513 client."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


PREPARATION_VERSION = 2
ASSEMBLY = 'standalone/vehicles/ussr_R04_T-34/assembly.xml'
RESOURCE_PATHS = 'prepared/vehicle/res;prepared/terrain/res;res;engine-res'
CLIENT_ARGUMENTS = [
    '-noConversion', '--res', RESOURCE_PATHS,
    '--space', 'spaces/01_karelia',
    '--assembly', ASSEMBLY,
    '--model-position', '-386,55.637216,-386',
    '--target-assembly', ASSEMBLY,
    '--target-position', '-386,54.057328,-356',
]


def check_package(package):
    for resource in ('res/resources.xml',
                     'engine-res/shaders/std_effects/skinned_effect_include.fxh'):
        if not (package / resource).is_file():
            raise ValueError('Prototype package is missing ' + resource)


def completed(prepared):
    marker = prepared / 'complete.json'
    try:
        version = json.loads(marker.read_text(encoding='utf-8')).get('version')
    except (OSError, ValueError):
        return False
    return version == PREPARATION_VERSION and all(
        (prepared / resource).is_file() for resource in (
            'terrain/res/spaces/01_karelia/space.settings',
            'terrain/res/standalone/engine_config.xml',
            'vehicle/res/' + ASSEMBLY,
            'vehicle/res/shaders/std_effects/skinned_effect_include.fxh'))


def prepare(package, client_root):
    check_package(package)
    prepared = package / 'prepared'
    if completed(prepared):
        print('Reusing completed prototype resources: %s' % prepared, flush=True)
        return prepared
    if prepared.exists():
        raise ValueError('Existing prepared directory is incomplete or has an old '
                         'version. Move it aside before preparing again: %s' % prepared)

    if client_root is None:
        value = input('Path to the Chinese HD 0.9.22.0.1 #1513 client: ').strip()
        if not value:
            raise ValueError('A client directory is required for first preparation.')
        client_root = Path(value.strip('"'))
    client_root = client_root.expanduser().resolve()

    # Load the shipped tools only when extraction is needed. Neither the
    # original executable nor its Python runtime is used to prepare resources.
    from prepare_native_scene import prepare as prepare_scene
    from prepare_native_vehicle import prepare as prepare_vehicle

    staging = Path(tempfile.mkdtemp(prefix='.preparing-', dir=str(package)))
    try:
        print('Preparing Karelia terrain from the local client...', flush=True)
        prepare_scene(client_root, '01_karelia', staging / 'terrain')
        print('Preparing the mounted stock T-34...', flush=True)
        prepare_vehicle(client_root, 'ussr:R04_T-34', staging / 'vehicle',
                        package / 'engine-res')
        # Reports are inspection metadata, but keep their final paths useful
        # after the complete staging directory is renamed into place.
        for kind, report_name in (('terrain', 'scene.json'), ('vehicle', 'vehicle.json')):
            report_path = staging / kind / report_name
            report = json.loads(report_path.read_text(encoding='utf-8'))
            report['resource_paths'] = [str(prepared / kind / 'res')]
            report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        (staging / 'complete.json').write_text(json.dumps({
            'version': PREPARATION_VERSION,
            'client_version': '0.9.22.0.1',
            'client_build': '1513',
            'map': '01_karelia',
            'vehicle': 'ussr:R04_T-34',
        }, indent=2) + '\n', encoding='utf-8')
        # An interrupted extraction leaves no directory that can be mistaken
        # for a completed preparation. Existing output is never overwritten.
        staging.rename(prepared)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print('Prototype resources prepared: %s' % prepared, flush=True)
    return prepared


def write_runner(package):
    runner = package / 'Run-Karelia.cmd'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='ascii', newline='',
                                         prefix='.Run-Karelia-', suffix='.cmd',
                                         dir=str(package), delete=False) as output:
            temporary = Path(output.name)
            output.write('@echo off\r\ncd /d "%~dp0"\r\n')
            output.write('bwclient_h.exe ' +
                         subprocess.list2cmdline(CLIENT_ARGUMENTS) + '\r\n')
        temporary.replace(runner)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return runner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-root', type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--client-root', type=Path,
                        default=os.environ.get('WOT_0922_CLIENT'),
                        help='Owned Chinese HD #1513 client; prompted on first run if omitted')
    parser.add_argument('--prepare-only', action='store_true',
                        help='Prepare resources and the runner without starting the client')
    args = parser.parse_args()
    package = args.package_root.expanduser().resolve()
    try:
        if not args.prepare_only and not (package / 'bwclient_h.exe').is_file():
            raise ValueError('Prototype package is missing bwclient_h.exe')
        prepare(package, args.client_root)
        runner = write_runner(package)
        print('Run the prepared scene with %s' % runner, flush=True)
        if args.prepare_only:
            return 0
        if os.name != 'nt':
            raise ValueError('Launching the prototype requires Windows; use --prepare-only here.')
        return subprocess.call([str(package / 'bwclient_h.exe')] + CLIENT_ARGUMENTS,
                               cwd=str(package))
    except (OSError, ValueError, EOFError) as error:
        print('Prototype preparation failed: %s' % error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
