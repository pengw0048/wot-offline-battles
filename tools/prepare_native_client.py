#!/usr/bin/env python3
"""Connect the independent client to an owned, unmodified #1513 installation."""

import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from inspect_client import inspect_client


def original_resource_paths(client):
    """Retain the original HD package precedence, excluding injected mods."""
    tree = ET.parse(client / 'paths.xml')
    resources = []
    package_root = (client / 'res/packages').resolve()
    resource_root = (client / 'res').resolve()
    for node in tree.findall('Paths/Path'):
        modes = {mode.strip() for mode in node.get('type', '').split(',') if mode.strip()}
        if modes and 'hd' not in modes:
            continue
        authored = (node.text or '').strip()
        if not authored:
            continue
        path = (client / authored).resolve()
        if path.suffix.lower() == '.pkg' and path.parent == package_root:
            if not path.is_file():
                raise ValueError('Missing authored HD package: ' + str(path))
            resources.append(path)
        elif path == resource_root:
            if not path.is_dir():
                raise ValueError('Missing original resource directory: ' + str(path))
            resources.append(path)
        # res_mods and wotmod search roots belong to the original application.
        # Its original content is reused; its injected runtime is not loaded.
    if package_root / 'scripts.pkg' not in resources:
        raise ValueError('Original HD resource paths do not contain scripts.pkg')
    if not resources:
        raise ValueError('Original client has no HD resource paths')
    return resources


def atomic_write(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.' + path.name + '-',
                                         dir=str(path.parent), delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


class OriginalResources:
    """Read the same ordered HD mounts as the native resource manager.

    Package filename sorting is not the installed client's resource priority.
    Loose directories and nested terrain ZIPs retain their original owners.
    """
    def __init__(self, client):
        self.paths = original_resource_paths(Path(client))
        self.index = {}
        self.directories = []
        self.archives = {}
        for priority, path in enumerate(self.paths):
            if path.is_dir():
                self.directories.append((priority, path))
                continue
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    if not name.endswith('/'):
                        self.index.setdefault(name.lower(), (priority, path, name))

    def read(self, resource):
        name = resource.replace('\\', '/').strip('/')
        if not name or '..' in name.split('/') or ':' in name:
            raise ValueError('Invalid original resource path: ' + resource)
        entry = self.index.get(name.lower())
        for priority, directory in self.directories:
            if entry is not None and priority > entry[0]:
                break
            candidate = directory / name
            if candidate.is_file():
                return candidate.read_bytes()
        if entry is not None:
            unused_priority, path, member = entry
            if path not in self.archives:
                self.archives[path] = zipfile.ZipFile(path)
            return self.archives[path].read(member)
        # Native ZipFileSystem detects nested archives by their magic rather
        # than a .zip suffix. Terrain containers use .cdata_processed.
        parts = name.split('/')
        for split in range(len(parts) - 1, 0, -1):
            container = '/'.join(parts[:split])
            if container.lower() not in self.index:
                continue
            data = self.read(container)
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                return archive.read('/'.join(parts[split:]))
        raise KeyError(name)

    def close(self):
        for archive in self.archives.values():
            archive.close()
        self.archives.clear()

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


def prepare(client, package):
    client = client.expanduser().resolve()
    package = package.expanduser().resolve()
    identity = inspect_client(client)
    for required in ('bwclient_h.exe', 'res/resources.xml', 'engine-res/resources.xml'):
        if not (package / required).is_file():
            raise ValueError('Independent client package is missing ' + required)
    originals = original_resource_paths(client)
    root = ET.Element('root')
    paths = ET.SubElement(root, 'Paths')
    # Original resources.xml, game.pyc and entities.xml own startup. The narrow
    # native-space overlay has no replacement personality or entity table.
    native_resources = package / 'native-res'
    native_resources.mkdir(exist_ok=True)
    for path in [native_resources] + originals + [package / 'res', package / 'engine-res']:
        ET.SubElement(paths, 'Path').text = path.as_posix()
    atomic_write(package / 'paths.xml', ET.tostring(root, encoding='utf-8',
                                                  xml_declaration=True))
    record = {
        'client_version': identity['version'], 'client_build': identity['build'],
        'original_client': str(client),
        'resource_paths': [str(path) for path in originals],
        'resources_copied': False,
        'original_executable_used_at_runtime': False,
        'personality': 'game',
    }
    atomic_write(package / 'original-client.json',
                 (json.dumps(record, indent=2) + '\n').encode('utf-8'))
    print('Connected %d original resource paths without copying assets.' % len(originals))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-root', type=Path, default=os.environ.get('WOT_0922_CLIENT'))
    parser.add_argument('--package-root', type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    package = args.package_root.expanduser().resolve()
    try:
        client = args.client_root
        marker = package / 'original-client.json'
        if client is None and marker.is_file():
            client = Path(json.loads(marker.read_text(encoding='utf-8'))['original_client'])
        if client is None:
            value = input('Path to the Chinese HD 0.9.22.0.1 #1513 client: ').strip().strip('"')
            if not value:
                raise ValueError('An owned #1513 client directory is required.')
            client = Path(value)
        prepare(client, package)
        if args.prepare_only:
            return 0
        if os.name != 'nt':
            raise ValueError('Launching the native client requires Windows; use --prepare-only here.')
        return subprocess.call([str(package / 'bwclient_h.exe'), '-noConversion'], cwd=str(package))
    except (OSError, ValueError, KeyError, EOFError) as error:
        print('Native client preparation failed: %s' % error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
