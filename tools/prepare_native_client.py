#!/usr/bin/env python3
"""Connect the independent client to an owned, unmodified #1513 installation."""

import argparse
import json
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

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
    # The application owns its entry point and entity selection. Every stock
    # script and data reference then resolves through the installed packages.
    for path in [package / 'res'] + originals + [package / 'engine-res']:
        ET.SubElement(paths, 'Path').text = path.as_posix()
    atomic_write(package / 'paths.xml', ET.tostring(root, encoding='utf-8',
                                                  xml_declaration=True))
    record = {
        'client_version': identity['version'], 'client_build': identity['build'],
        'original_client': str(client),
        'resource_paths': [str(path) for path in originals],
        'resources_copied': False,
        'original_executable_used_at_runtime': False,
    }
    atomic_write(package / 'original-client.json',
                 (json.dumps(record, indent=2) + '\n').encode('utf-8'))
    print('Connected %d original resource paths without copying assets.' % len(originals))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-root', type=Path, required=True)
    parser.add_argument('--package-root', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.client_root, args.package_root)


if __name__ == '__main__':
    main()
