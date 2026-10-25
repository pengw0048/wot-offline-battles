#!/usr/bin/env python3
"""Prepare an owned #1513 map as a public BigWorld terrain chunk preview.

Terrain payloads and landscape textures are extracted unchanged. This is not
a battle map: compiled statics, water, flora and the original sky presentation
are not adapted. No client files are modified or redistributed by this tool.
"""

import argparse
import copy
import io
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

from bake_navigation_0922 import PackageVFS, _legacy_baker
from inspect_client import inspect_client
from space_bin_0922 import CompiledSpace as SpaceDirectory


TOOL_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_ROOT / 'vendor'))
from wot_space_bin_utils import CompiledSpace  # noqa: E402
from wot_space_bin_utils.xml_utils.XmlUnpacker import XmlUnpacker  # noqa: E402


def write_xml(path, root):
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)


def extract_member(archive, member, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(archive.read(member))


def prepare_lighting(packages, settings, scene, scene_path):
    """Project authored forward sunlight/ambient keys into public TimeOfDay."""
    resource = settings.findtext('timeOfDay')
    if not resource:
        raise ValueError('map has no authored timeOfDay resource')
    vfs = PackageVFS(str(packages))
    try:
        source = XmlUnpacker().read(io.BytesIO(vfs.read(resource.strip())))
    finally:
        vfs.close()
    cycle = source.find('day_night_cycle')
    if cycle is None:
        raise ValueError('timeOfDay has no day_night_cycle: ' + resource)
    # #1513 stores renderer-specific directional lighting below forward/;
    # public TimeOfDay::load reads angle/lightkey directly below the cycle.
    # The unchanged root-level ambient curve and clock remain shared.
    forward = cycle.find('forward')
    directional = forward if forward is not None else cycle
    result = ET.Element('root')
    converted = ET.SubElement(result, 'day_night_cycle')
    for name in ('starttime', 'hourlength', 'sunColor', 'moonColor', 'moonAngle',
                 'ambientkey', 'fogkey'):
        for node in cycle.findall(name):
            converted.append(copy.deepcopy(node))
    for name in ('angle', 'lightkey'):
        for node in directional.findall(name):
            converted.append(copy.deepcopy(node))
    if not converted.findall('lightkey') or not converted.findall('ambientkey'):
        raise ValueError('timeOfDay lacks sunlight or ambient keys: ' + resource)
    write_xml(scene / 'native_lighting.xml', result)
    return scene_path + '/native_lighting.xml', resource.strip()


def prepare(client_root, map_name, output):
    client = inspect_client(client_root)
    packages = client_root / 'res' / 'packages'
    prefix = 'spaces/%s/' % map_name
    scene_path = 'spaces/%s' % map_name
    with zipfile.ZipFile(packages / (map_name + '.pkg')) as package:
        binary = package.read(prefix + 'space.bin')
        packed_settings = package.read(prefix + 'space.settings')
        directory = SpaceDirectory(binary)
        # Export only sections supported by this preview. Model/geometry
        # exporters need a separate processed-resource adapter, not a rename.
        compiled = CompiledSpace(io.BytesIO(binary), '0.9.22.0.1', 'RU',
                                 ['BWST', 'BWT2'])
        terrain = compiled.sections['BWT2']._data
        strings = compiled.sections['BWST']
        settings = XmlUnpacker().read(io.BytesIO(packed_settings))
        height_reader = _legacy_baker()

        output.mkdir(parents=True, exist_ok=False)
        resources = output / 'res'
        scene = resources / scene_path
        scene.mkdir(parents=True)
        # The existing exporter reads the original packed settings itself.
        (scene / 'space.settings').write_bytes(packed_settings)
        compiled.unp_for_world_editor(scene)
        exported = scene / 'unpacked_for_world_editor'
        sample_sizes = set()
        height_range = [float('inf'), float('-inf')]
        terrain_bytes = 0
        for chunk in terrain['cdatas']:
            resource = strings.get(chunk['resource_fnv'])
            if not resource.endswith('.cdata_processed/terrain2'):
                raise ValueError('unexpected #1513 terrain resource: %s' % resource)
            container = resource.rsplit('/', 1)[0]
            chunk_name = container.split('.', 1)[0]
            chunk_tree = ET.parse(exported / (chunk_name + '.chunk'))
            chunk_tree.find('terrain/resource').text = resource
            write_xml(scene / (chunk_name + '.chunk'), chunk_tree.getroot())
            with zipfile.ZipFile(io.BytesIO(package.read(prefix + container))) as nested:
                height = height_reader.HeightChunk(nested.read('terrain2/heights'))
                sample_sizes.add(height.width - height_reader.HEIGHTMAP_BORDER * 2 - 1)
                height_range[0] = min(height_range[0], height.minimum)
                height_range[1] = max(height_range[1], height.maximum)
                # Use a directory with the original processed name, so the
                # public resource manager need not recognise another ZIP suffix.
                for member in nested.infolist():
                    if member.is_dir():
                        continue
                    extract_member(nested, member.filename, scene / container / member.filename)
                    terrain_bytes += member.file_size
        if len(sample_sizes) != 1:
            raise ValueError('terrain chunks have inconsistent height map sizes')

        # Public chunk terrain requires this section; #1513 compiled spaces
        # store it in BWT2 rather than space.settings. Retain only mapped,
        # named fields and the sample size measured by the existing reader.
        preview_settings = ET.Element('root')
        preview_settings.append(settings.find('bounds'))
        ET.SubElement(preview_settings, 'chunkSize').text = str(terrain['settings']['chunk_size'])
        terrain_settings = ET.SubElement(preview_settings, 'terrain')
        ET.SubElement(terrain_settings, 'version').text = str(terrain['settings2']['terrain_version'])
        ET.SubElement(terrain_settings, 'heightMapSize').text = str(next(iter(sample_sizes)))
        ET.SubElement(terrain_settings, 'lodMapSize').text = str(terrain['settings2']['lod_map_size'])
        lighting, lighting_source = prepare_lighting(
            packages, settings, scene, scene_path)
        ET.SubElement(preview_settings, 'timeOfDay').text = lighting
        write_xml(scene / 'space.settings', preview_settings)
        original = output / 'source'
        original.mkdir()
        (original / 'space.settings').write_bytes(packed_settings)
        # These are the exporter's temporary files, all created by this run.
        for path in exported.rglob('*'):
            if path.is_file():
                path.unlink()
        for path in sorted(exported.rglob('*'), reverse=True):
            if path.is_dir():
                path.rmdir()
        exported.rmdir()

    # Include the terrain texture families, without extracting vehicles or
    # processed static geometry. Map-local textures take precedence.
    landscape_files = set()
    for package_name in ('shared_content_sandbox.pkg', 'shared_content.pkg', map_name + '.pkg'):
        with zipfile.ZipFile(packages / package_name) as package:
            for member in package.infolist():
                if member.filename.startswith('maps/landscape/') and not member.is_dir():
                    extract_member(package, member.filename, resources / member.filename)
                    landscape_files.add(member.filename)

    bootstrap = TOOL_ROOT.parent / 'native/standalone_client/bigworld/res'
    config = ET.parse(bootstrap / 'standalone/engine_config.xml')
    config.find('standalone/space').text = scene_path
    # Use the authored starting point, lifted only for the preview camera.
    start = [float(value) for value in settings.findtext('startPosition').split()]
    camera = [start[0], start[1] + 25.0, start[2]]
    config.find('standalone/cameraPosition').text = ' '.join(str(value) for value in camera)
    (resources / 'standalone').mkdir()
    write_xml(resources / 'standalone/engine_config.xml', config.getroot())

    report = {
        'scene_kind': 'terrain_preview',
        'client_version': client['version'],
        'client_build': client['build'],
        'map': map_name,
        'scene_path': scene_path,
        'resource_paths': [str(resources), str(bootstrap)],
        'requires_public_engine_runtime_resources': True,
        'terrain_chunks': len(terrain['cdatas']),
        'height_map_size': next(iter(sample_sizes)),
        'height_range': height_range,
        'terrain_payload_bytes': terrain_bytes,
        'landscape_texture_files': len(landscape_files),
        'camera_position': camera,
        'lighting_source': lighting_source,
        'lighting_adapter': 'Authored forward sunlight and shared ambient keys; public TimeOfDay schema.',
        'omitted_compiled_sections': sorted(set(directory.sections) - {'BWST', 'BWT2'}),
        'limitations': [
            'Terrain preview only; compiled statics, water, flora and the original sky are not adapted.',
            'Add public engine runtime resources after resource_paths; original client packages are not required at runtime.',
            'File preparation does not establish Windows rendering or battle-map parity.',
        ],
    }
    (output / 'scene.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-root', required=True, type=Path)
    parser.add_argument('--map', required=True, dest='map_name')
    parser.add_argument('--output', required=True, type=Path, help='new output directory')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_]+', args.map_name):
        parser.error('--map must be a map resource directory name')
    report = prepare(args.client_root.expanduser().resolve(), args.map_name,
                     args.output.expanduser().resolve())
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
