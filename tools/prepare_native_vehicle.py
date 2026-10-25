#!/usr/bin/env python3
"""Extract an owned #1513 vehicle and its resource graph for native inspection.

The output contains original geometry, decoded model/visual XML and an assembly
with descriptor-authored zero-angle component positions. Processed geometry
bytes are retained. Verified packed skin declarations and public diffuse-only materials
are adapted for the public engine; the original XML is retained separately.
No client files are modified, and extraction does not prove native rendering.
"""

import argparse
import base64
from collections import deque
import json
from pathlib import Path, PurePosixPath
import re
import struct
import xml.etree.ElementTree as ET

from bake_navigation_0922 import PackageVFS
from bw_primitives_geometry import _sections
from inspect_client import inspect_client
from packed_xml import (PACKED_XML_MAGIC, TYPE_BOOLEAN, TYPE_COMPRESSED_STRING,
                        TYPE_ELEMENT, TYPE_VECTOR, read_packed_xml)


PUBLIC_STORAGE_ADAPTERS = {
    # #1513 bytes already contain palette row offsets (three rows per bone).
    # The public shader and CPU skinner consume those same row offsets.
    'SC_UBYTE4_MULT3_REVERSE_PADDED_IIIP': 'SC_REVERSE_PADDED_III_',
    'SC_UBYTE4_REVERSE_PADDED_PWWP': 'SC_REVERSE_PADDED__WW_',
}
XML_SUFFIXES = ('.xml', '.model', '.visual', '.mfm', '.texformat', '.track')
RESOURCE_SUFFIXES = XML_SUFFIXES + ('.dds', '.tga', '.png', '.jpg', '.bmp',
                                    '.primitives', '.animation', '.fx', '.fxo')


def xml_root(data):
    """Decode floats with round-trip precision, including packed matrix rows."""
    if not data.startswith(PACKED_XML_MAGIC):
        return ET.fromstring(data)

    def populate(node, value):
        if value.value_type == TYPE_ELEMENT:
            populate(node, value.value.value)
            for name, child in value.value.children:
                populate(ET.SubElement(node, name.decode('utf-8')), child)
        elif value.value_type == TYPE_VECTOR:
            numbers = struct.unpack('<%df' % (len(value.value) // 4), value.value)
            if len(numbers) == 12:
                for row in range(4):
                    ET.SubElement(node, 'row%d' % row).text = ' '.join(
                        repr(number) for number in numbers[row * 3:row * 3 + 3])
            else:
                node.text = ' '.join(repr(number) for number in numbers)
        elif value.value_type == TYPE_BOOLEAN:
            node.text = 'true' if value.value else 'false'
        elif value.value_type == TYPE_COMPRESSED_STRING:
            node.text = base64.b64encode(value.value).decode('ascii')
        elif isinstance(value.value, bytes):
            node.text = value.value.decode('utf-8')
        else:
            node.text = str(value.value)

    packed = read_packed_xml(data)
    result = ET.Element('root')
    populate(result, packed.value)
    for name, value in packed.children:
        populate(ET.SubElement(result, name.decode('utf-8')), value)
    return result


def write_xml(path, root):
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)


def normalized(resource):
    resource = resource.strip().replace('\\', '/')
    path = PurePosixPath(resource)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('unsafe resource path: %s' % resource)
    return path.as_posix()


def vector(node, path):
    text = node.findtext(path)
    if text is None:
        raise ValueError('missing descriptor position: %s' % path)
    result = [float(number) for number in text.split()]
    if len(result) != 3:
        raise ValueError('invalid descriptor position: %s' % path)
    return result


def add(left, right):
    return [a + b for a, b in zip(left, right)]


def model_node_position(resource_root, model_resource, identifier):
    """Resolve the authored bind pose using the public engine's row matrices."""
    model = ET.parse(resource_root / model_resource).getroot()
    visual = model.findtext('nodefullVisual')
    if not visual:
        raise ValueError('gun model has no nodefullVisual: ' + model_resource)
    root = ET.parse(resource_root / (normalized(visual) + '.visual')).getroot()
    identity = [[float(row == col) for col in range(4)] for row in range(4)]
    matches = []

    def visit(node, parent):
        transform = node.find('transform')
        if transform is None:
            raise ValueError('model node has no bind transform: ' + identifier)
        local = [vector(transform, 'row%d' % row) + [float(row == 3)]
                 for row in range(4)]
        world = [[sum(local[row][k] * parent[k][col] for k in range(4))
                  for col in range(4)] for row in range(4)]
        if node.findtext('identifier') == identifier:
            matches.append(world[3][:3])
        for child in node.findall('node'):
            visit(child, world)

    for node in root.findall('node'):
        visit(node, identity)
    if len(matches) != 1:
        raise ValueError('expected one %s in %s' % (identifier, visual))
    return matches[0]


def preview_shot(vfs, resource_root, nation, gun, gun_component):
    """Export #1513 GunShot values and base reload for static firing preview.

    items/readers/gun_readers.readShot applies projectileSpeedFactor to speed
    and its square to gravity. items/vehicles._readGunLocals overrides shared
    reloadTime/maxAmmo while retaining shared shots. Crew effects are not yet
    part of this static assembly preview.
    """
    shared = (gun.text or '').strip() == 'shared'
    definition = gun
    if shared:
        definitions = xml_root(vfs.read(
            'scripts/item_defs/vehicles/%s/components/guns.xml' % nation))
        definition = definitions.find('shared/' + gun.tag)
        if definition is None:
            raise ValueError('missing shared gun: ' + gun.tag)
    # The preview has one chamber and one base reload timer. Preserve the model
    # preview without misrepresenting magazine or multi-round firing behavior.
    if any(node.find(field) is not None for node in (gun, definition)
           for field in ('burst', 'clip')):
        return None, 'Magazine and burst firing are not implemented in the static preview.'
    shots = definition.find('shots')
    if shots is None or not len(shots):
        raise ValueError('gun has no authored shots: ' + gun.tag)
    shot = shots[0]
    common = xml_root(vfs.read('scripts/item_defs/vehicles/common/vehicle.xml'))
    factor = float(common.findtext('miscParams/projectileSpeedFactor'))
    reload_time = gun.findtext('reloadTime', definition.findtext('reloadTime'))
    max_ammo = gun.findtext('maxAmmo', definition.findtext('maxAmmo'))
    muzzle = model_node_position(resource_root, gun_component['model'], 'HP_gunFire')
    return {
        'shell': nation + ':' + shot.tag,
        'speed': float(shot.findtext('speed')) * factor,
        'gravity': float(shot.findtext('gravity')) * factor ** 2,
        'maxDistance': float(shot.findtext('maxDistance')),
        'reloadTime': float(reload_time),
        'maxAmmo': int(max_ammo),
        'muzzlePosition': add(gun_component['position'], muzzle),
    }, None


def descriptor_data(node):
    """Preserve repeated fields and source units instead of inventing physics."""
    return {'name': node.tag, 'value': (node.text or '').strip(),
            'children': [descriptor_data(child) for child in node]}


def resolve_vehicle(vfs, requested):
    matches = []
    for key in sorted(vfs.index):
        if not re.fullmatch(r'scripts/item_defs/vehicles/[^/]+/list.xml', key):
            continue
        nation = key.split('/')[-2]
        listing = xml_root(vfs.read(key))
        for item in listing:
            name = nation + ':' + item.tag
            labels = [item.tag, name]
            labels.extend((item.findtext(field) or '').split(':')[-1]
                          for field in ('userString', 'shortUserString'))
            if requested in labels:
                matches.append((nation, item))
    if len(matches) != 1:
        raise ValueError('vehicle must identify one catalogue entry; matches: %s' %
                         ', '.join(nation + ':' + item.tag for nation, item in matches))
    nation, item = matches[0]
    path = 'scripts/item_defs/vehicles/%s/%s.xml' % (nation, item.tag)
    return nation, item, path, xml_root(vfs.read(path))


class Extractor:
    def __init__(self, vfs, output):
        self.vfs = vfs
        self.output = output
        self.pending = deque()
        self.seen = set()
        self.files = []
        self.edges = []
        self.unresolved = []
        self.vertex_formats = set()
        self.vertex_targets = {}
        self.storage_adapters = set()
        self.maximum_palette_bones = 0
        self.material_adapters = []
        self.public_runtime_resources = set()

    def enqueue(self, path, owner):
        path = normalized(path)
        self.edges.append({'owner': owner, 'resource': path})
        self.pending.append(path)

    def source(self, resource):
        key = resource.lower()
        if key in self.vfs.index:
            return self.vfs.index[key][1]
        if resource.endswith(('.visual', '.primitives')):
            candidate = key + '_processed'
            if candidate in self.vfs.index:
                return self.vfs.index[candidate][1]
        return None

    def extract(self):
        while self.pending:
            resource = self.pending.popleft()
            if resource in self.seen:
                continue
            self.seen.add(resource)
            source = self.source(resource)
            if source is None:
                self.unresolved.append(resource)
                continue
            package, unused = self.vfs.index[source.lower()]
            data = self.vfs.read(source)
            destination = self.output / 'res' / resource
            destination.parent.mkdir(parents=True, exist_ok=True)
            converted = resource.endswith(XML_SUFFIXES)
            if converted:
                root = xml_root(data)
                if resource.endswith('.visual'):
                    self.preview_materials(resource, root)
                if resource.startswith('shaders/formats/'):
                    for node in root.findall('.//type'):
                        storage = (node.text or '').strip()
                        if storage in PUBLIC_STORAGE_ADAPTERS:
                            node.text = PUBLIC_STORAGE_ADAPTERS[storage]
                            self.storage_adapters.add(storage)
                    # Public shaders and software skinners expect their packed
                    # GPU target. Let the native converter pack source float UVs.
                    if '/set3/' in resource:
                        target_format = PurePosixPath(resource).stem
                        targets = root.find('targets')
                        if targets is None:
                            targets = ET.SubElement(root, 'targets')
                        target = ET.SubElement(targets, 'target')
                        ET.SubElement(target, 'name').text = 'D3D9'
                        ET.SubElement(target, 'format').text = target_format
                        self.vertex_targets[resource] = target_format
                        self.public_runtime_resources.add(
                            'shaders/formats/' + target_format + '.xml')
                write_xml(destination, root)
                original = self.output / 'source' / source
                original.parent.mkdir(parents=True, exist_ok=True)
                original.write_bytes(data)
                self.xml_dependencies(resource, root)
            else:
                destination.write_bytes(data)
            self.files.append({'resource': resource, 'source': source,
                               'package': package, 'source_bytes': len(data),
                               'decoded_xml': converted})
            if resource.endswith('.primitives'):
                self.primitive_dependencies(resource, data)

    def preview_materials(self, resource, root):
        for render_set in root.findall('renderSet'):
            if render_set.findtext('treatAsWorldSpaceObject') == 'true':
                self.maximum_palette_bones = max(self.maximum_palette_bones,
                                                 len(render_set.findall('node')))
        for material in root.findall('.//material'):
            source_fx = material.findtext('fx')
            if not source_fx:
                continue
            skinned = 'skinned' in source_fx.lower()
            preview_fx = 'shaders/std_effects/lightonly%s.fx' % ('_skinned' if skinned else '')
            diffuse = next((prop for prop in material.findall('property')
                            if (prop.text or '').strip() == 'diffuseMap'), None)
            if diffuse is None or not diffuse.findtext('Texture'):
                raise ValueError('material lacks a diffuse texture: %s/%s' %
                                 (resource, material.findtext('identifier')))
            self.material_adapters.append({
                'visual': resource, 'material': material.findtext('identifier'),
                'source_effect': source_fx, 'preview_effect': preview_fx,
                'diffuse_texture': diffuse.findtext('Texture')})
            material.find('fx').text = preview_fx
            self.public_runtime_resources.add(preview_fx)
            for prop in list(material.findall('property')):
                if (prop.text or '').strip() not in (
                        'diffuseMap', 'alphaTestEnable', 'alphaReference'):
                    material.remove(prop)

    def xml_dependencies(self, resource, root):
        if resource.startswith('scripts/item_defs/'):
            return
        for node in root.iter():
            value = (node.text or '').strip()
            if not value or '\n' in value:
                continue
            if value in self.public_runtime_resources:
                continue
            if node.tag in ('nodefullVisual', 'nodelessVisual', 'billboardVisual'):
                self.enqueue(value + '.visual', resource)
            elif node.tag == 'parent' and resource.endswith('.model'):
                self.enqueue(value + ('' if value.endswith('.model') else '.model'), resource)
            elif node.tag == 'nodes' and resource.endswith('.model'):
                self.enqueue(value + '.animation', resource)
            elif value.lower().endswith(RESOURCE_SUFFIXES) and '/' in value:
                self.enqueue(value, resource)
        if resource.endswith('.visual'):
            primitive = root.findtext('primitivesName') or resource[:-7]
            self.enqueue(primitive + '.primitives', resource)

    def primitive_dependencies(self, resource, data):
        sections = _sections(data)
        for name, blob in sections.items():
            if name.endswith('vertices') and blob.startswith(b'BPVT'):
                if len(blob) < 136:
                    raise ValueError('truncated processed vertex header: ' + resource)
                fmt = blob[68:132].split(b'\0', 1)[0].decode('ascii')
            elif blob.startswith(b'BPVS'):
                if len(blob) < 136:
                    raise ValueError('truncated processed stream header: ' + resource)
                fmt = blob[68:132].split(b'\0', 1)[0].decode('ascii')
            elif name.endswith('vertices'):
                fmt = blob[:64].split(b'\0', 1)[0].decode('ascii')
            else:
                continue
            self.vertex_formats.add(fmt)
            self.enqueue('shaders/formats/' + fmt + '.xml', resource + '/' + name)


def prepare(client_root, requested, output, engine_res=None):
    client = inspect_client(client_root)
    vfs = PackageVFS(str(client_root / 'res/packages'))
    try:
        nation, listing, descriptor_path, descriptor = resolve_vehicle(vfs, requested)
        chassis = next(iter(descriptor.find('chassis')))
        turret = next(iter(descriptor.find('turrets0')))
        gun = next(iter(turret.find('guns')))
        hull = descriptor.find('hull')
        hull_position = vector(chassis, 'hullPosition')
        turret_position = add(hull_position, vector(hull, 'turretPositions/turret'))
        gun_position = add(turret_position, vector(turret, 'gunPosition'))
        components = []
        for name, definition, position in (
                ('chassis', chassis, [0.0, 0.0, 0.0]),
                ('hull', hull, hull_position),
                ('turret', turret, turret_position),
                ('gun', gun, gun_position)):
            model = definition.findtext('models/undamaged')
            if not model:
                raise ValueError('selected %s lacks an undamaged model' % name)
            components.append({'name': name, 'model': normalized(model),
                               'position': position, 'descriptor': descriptor_data(definition)})
        output.mkdir(parents=True, exist_ok=False)
        extractor = Extractor(vfs, output)
        extractor.enqueue(descriptor_path, 'vehicle_descriptor')
        # Shared module files provide the original gun/engine definitions whose
        # inline vehicle overrides are retained in the descriptor above.
        for node in (gun, next(iter(descriptor.find('engines')), None)):
            if node is not None and (node.text or '').strip() == 'shared':
                kind = 'guns' if node is gun else 'engines'
                extractor.enqueue('scripts/item_defs/vehicles/%s/components/%s.xml' %
                                  (nation, kind), descriptor_path)
        for component in components:
            extractor.enqueue(component['model'], 'assembly/' + component['name'])
        extractor.extract()
        if extractor.unresolved:
            raise ValueError('unresolved resource dependencies: %s' %
                             ', '.join(sorted(extractor.unresolved)))
        palette_rows = max(51, extractor.maximum_palette_bones * 3)
        if engine_res is not None and palette_rows > 51:
            include = 'shaders/std_effects/skinned_effect_include.fxh'
            data = (engine_res / include).read_text(encoding='utf-8')
            if 'world[51]' not in data:
                raise ValueError('public skin helper does not have the reviewed world[51] layout')
            destination = output / 'res' / include
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(data.replace('world[51]', 'world[%d]' % palette_rows),
                                   encoding='utf-8')
        assembly_path = 'standalone/vehicles/%s_%s/assembly.xml' % (nation, listing.tag)
        assembly = ET.Element('root')
        for component in components:
            item = ET.SubElement(assembly, 'component')
            for key in ('name', 'model'):
                ET.SubElement(item, key).text = component[key]
            ET.SubElement(item, 'position').text = ' '.join(repr(v) for v in component['position'])
        shot, firing_limitation = preview_shot(
            vfs, output / 'res', nation, gun, components[-1])
        if shot is not None:
            shot_node = ET.SubElement(assembly, 'shot')
            for name, value in shot.items():
                ET.SubElement(shot_node, name).text = (
                    ' '.join(repr(v) for v in value) if isinstance(value, list)
                    else str(value))
        write_xml(output / 'res' / assembly_path, assembly)
        report = {
            'scene_kind': 'vehicle_resource_preview',
            'client_version': client['version'], 'client_build': client['build'],
            'vehicle': nation + ':' + listing.tag,
            'catalogue_id': int(listing.findtext('id')),
            'descriptor_resource': descriptor_path,
            'configuration': {'selection': 'first authored chassis, turret and gun',
                              'chassis': chassis.tag, 'turret': turret.tag, 'gun': gun.tag},
            'components': components, 'assembly': assembly_path,
            'shot': shot,
            'firing_limitation': firing_limitation,
            'resource_paths': [str(output / 'res')],
            'resources': sorted(extractor.files, key=lambda row: row['resource']),
            'dependency_edges': extractor.edges,
            'unresolved_resources': extractor.unresolved,
            'vertex_formats': sorted(extractor.vertex_formats),
            'vertex_format_targets': extractor.vertex_targets,
            'material_adapters': extractor.material_adapters,
            'requires_public_engine_runtime_resources': True,
            'public_runtime_resources': sorted(extractor.public_runtime_resources),
            'vertex_storage_adapters': {name: PUBLIC_STORAGE_ADAPTERS[name]
                                        for name in sorted(extractor.storage_adapters)},
            'maximum_palette_bones': extractor.maximum_palette_bones,
            'public_skin_palette_rows': palette_rows,
            'public_skin_helper_adapted': engine_res is not None and palette_rows > 51,
            'limitations': [
                'Static zero-angle preview; component positions come from the vehicle descriptor.',
                'Firing uses the first authored shell, base reload and full capacity for that shell; crew, dispersion and turret control are not applied.',
                'Authored model nodes, geometry, texture data and LOD parent chains are retained.',
                'Public lightonly materials use original diffuse textures; original PBS appearance is not restored.',
                'Add public engine runtime resources after resource_paths, including compiled lightonly effects.',
                'Compile public shader sources after applying the generated skin helper override.',
                'FLOAT2 source UVs use the public D3D9 target conversion to its SHORT2 GPU format.',
                'Resource extraction does not prove Windows loading, rendering or vehicle behavior.',
            ],
        }
        (output / 'vehicle.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        return report
    finally:
        vfs.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-root', type=Path, required=True)
    parser.add_argument('--vehicle', required=True, help='catalogue name, e.g. ussr:R04_T-34 or T-34')
    parser.add_argument('--output', type=Path, required=True, help='new output directory')
    parser.add_argument('--engine-res', type=Path, required=True,
                        help='public engine runtime resources; adapt its skin helper to the measured palette size')
    args = parser.parse_args()
    report = prepare(args.client_root.expanduser().resolve(), args.vehicle,
                     args.output.expanduser().resolve(),
                     args.engine_res.expanduser().resolve() if args.engine_res else None)
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ('resources', 'dependency_edges', 'components', 'material_adapters')}, indent=2))


if __name__ == '__main__':
    main()
