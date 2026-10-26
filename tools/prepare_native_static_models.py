#!/usr/bin/env python3
"""Reconstruct compiled static-model states using their original #1513 assets.

Only local model/visual descriptors are written. Geometry, textures, shaders,
animations and BSP resources remain in the owner's original package mounts.
"""
import copy
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from prepare_native_vehicle import xml_root
from bw_primitives_geometry import _sections


def _text(node, name, value):
    child = node.find(name)
    if child is None:
        child = ET.SubElement(node, name)
    child.text = str(value)
    return child


def _write(path, root):
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)


def _resource(base, value):
    return value if '.primitives/' in value else base + '.primitives/' + value


class StaticModelStates:
    """Preserve BSMO render subsets and collision material selection per state."""
    def __init__(self, compiled, vfs, output, prefix):
        self.models = compiled.sections['BSMO']._data
        self.materials = compiled.sections['BSMA']._data
        self.strings = compiled.sections['BWST']
        self.instances = compiled.sections['BSMI']._data
        self.vfs = vfs
        self.output = Path(output)
        self.prefix = prefix.rstrip('/')
        self._xml = {}
        self._written = {}
        self._geometry = {}

    def xml(self, resource):
        if resource not in self._xml:
            self._xml[resource] = xml_root(self.vfs.read(resource))
        return copy.deepcopy(self._xml[resource])

    def material(self, index, original):
        data = self.materials
        record = data['materials'][index]
        result = copy.deepcopy(original)
        for child in list(result):
            if child.tag in ('fx', 'property'):
                result.remove(child)
        if record['key_fx'] >= 0:
            _text(result, 'fx', self.strings.get(data['fx'][record['key_fx']]))
        first, last = record['key_from'], record['key_to']
        if first == -1 and last == -1:
            return result
        if not 0 <= first <= last < len(data['props']):
            raise ValueError('Compiled static material has an invalid property range')
        kinds = {1: 'Bool', 2: 'Float', 3: 'Int', 4: 'Matrix', 5: 'Vector4', 6: 'Texture'}
        for property_record in data['props'][first:last + 1]:
            kind, value = property_record['value_type'], property_record['value']
            # #1513 0x0093e6b0 skips kind zero before its typed setter switch.
            # It leaves the effect default intact; it does not bind NULL.
            if kind == 0:
                continue
            if kind not in kinds:
                raise ValueError('Unreviewed compiled static property type: %d' % kind)
            prop = ET.SubElement(result, 'property')
            prop.text = self.strings.get(property_record['prop_fnv'])
            child = ET.SubElement(prop, kinds[kind])
            if kind == 1:
                child.text = 'true' if value == 1 else 'false'
            elif kind == 3:
                child.text = str(value if value < 0x80000000 else value - 0x100000000)
            elif kind == 4:
                matrix = data['matrices'][value]
                for row in range(4):
                    ET.SubElement(child, 'row%d' % row).text = ' '.join(
                        repr(v) for v in matrix[row * 4:row * 4 + 4])
            elif kind == 5:
                child.text = ' '.join(repr(v) for v in data['vectors'][value])
            elif kind == 6:
                child.text = self.strings.get(value)
            else:
                child.text = repr(value)
        return result

    def source_model(self, model_id):
        collider = self.models['models_colliders'][model_id]
        resource = self.strings.get(collider['bsp_section_name_fnv'])
        if not resource:
            lod = self.models['models_loddings'][model_id]['lod_begin']
            render = self.models['lod_renders'][lod]['render_set_begin']
            resource = self.strings.get(self.models['renders'][render]['verts_name_fnv'])
        if not resource or '.primitives' not in resource:
            raise ValueError('Compiled static model has no source geometry: %d' % model_id)
        model = resource.split('.primitives', 1)[0] + '.model'
        # A filename is only a candidate. Every LOD/render below must match its
        # compiled geometry before this source model is accepted for reuse.
        return model

    def renders(self, lod):
        record = self.models['lod_renders'][lod]
        first, last = record['render_set_begin'], record['render_set_end']
        if not 0 <= first <= last < len(self.models['renders']):
            raise ValueError('Compiled static LOD has an invalid render range')
        return self.models['renders'][first:last + 1]

    def geometry(self, resource):
        container, section = resource.rsplit('/', 1)
        if not container.endswith('.primitives'):
            raise ValueError('Compiled geometry does not identify a primitives section')
        if container not in self._geometry:
            self._geometry[container] = _sections(self.vfs.read(container + '_processed'))
        if section not in self._geometry[container]:
            raise ValueError('Compiled geometry section is absent: ' + resource)
        return self._geometry[container][section]

    def compiled_visual(self, model_id, lod):
        """Rebuild static Scene Root descriptors from compiled draw records.

        Some shipped geometry has no source visual. BSMO still names its exact
        vertex/index sections and primitive groups; it is the runtime source
        of those draws. Non-root or animated graphs need their original nodes.
        """
        source = ET.Element('root')
        bounds = ET.SubElement(source, 'boundingBox')
        for name in ('min', 'max'):
            _text(bounds, name, ' '.join(repr(v) for v in
                  self.models['models_visibility_bounds'][model_id][name]))
        node = ET.SubElement(source, 'node')
        _text(node, 'identifier', 'Scene Root')
        transform = ET.SubElement(node, 'transform')
        for row in range(4):
            _text(transform, 'row%d' % row, ' '.join(
                '1' if row == col else '0' for col in range(3)))
        for render in self.renders(lod):
            if (render['node_begin'] != 0xffffffff or
                    render['node_end'] != 0xffffffff or render['is_skinned']):
                raise ValueError('Compiled visual requires its authored node graph')
            vertices = self.strings.get(render['verts_name_fnv'])
            indices = self.strings.get(render['prims_name_fnv'])
            self.geometry(vertices)
            self.geometry(indices)
            render_set = ET.SubElement(source, 'renderSet')
            _text(render_set, 'node', 'Scene Root')
            _text(render_set, 'treatAsWorldSpaceObject', 'false')
            geometry = ET.SubElement(render_set, 'geometry')
            _text(geometry, 'vertices', vertices)
            _text(geometry, 'primitive', indices)
            group = ET.SubElement(geometry, 'primitiveGroup')
            group.text = str(render['primitive_index'])
            group.append(self.material(render['material_index'], ET.Element('material')))
        source.append(self.collision(model_id))
        return source

    def descriptor(self, model_id, lod, resource):
        try:
            return self.xml(resource)
        except KeyError:
            for index, instance_model in enumerate(self.instances['bsmo_models_id']):
                if instance_model == model_id and self.instances['chunk_models'][index]['has_animations']:
                    raise ValueError('Animated compiled model lacks its source descriptor: ' + resource)
            source = ET.Element('root')
            squared = self.models['lod_loddings'][lod]
            extent = math.sqrt(squared) if squared < 3.4028234663852886e38 else -1.
            _text(source, 'extent', repr(extent))
            geometry = self.strings.get(self.renders(lod)[0]['verts_name_fnv'])
            _text(source, 'nodelessVisual', geometry.split('.primitives/', 1)[0])
            if lod < self.models['models_loddings'][model_id]['lod_end']:
                parent = self.strings.get(self.renders(lod + 1)[0]['verts_name_fnv'])
                _text(source, 'parent', parent.split('.primitives/', 1)[0])
            return source

    def collision(self, model_id):
        record = self.models['models_colliders'][model_id]
        resource = self.strings.get(record['bsp_section_name_fnv'])
        section = ET.Element('stockBsp')
        if not resource:
            _text(section, 'disabled', 'true')
            return section
        _text(section, 'resource', resource)
        first, last = record['bsp_material_kind_begin'], record['bsp_material_kind_end']
        if first == 0xffffffff:
            _text(section, 'raw', 'true')
            return section
        if not 0 <= first <= last < len(self.models['bsp_material_kinds']):
            raise ValueError('Compiled static collider has an invalid material range')
        for material in self.models['bsp_material_kinds'][first:last + 1]:
            index, flags = material['material_index'], material['flags']
            if not 0 <= index <= 0xffff or not 0 <= flags <= 0xffff:
                raise ValueError('Compiled BSP material does not fit a native triangle')
            row = ET.SubElement(section, 'material')
            _text(row, 'index', index)
            _text(row, 'flags', flags)
        return section

    def visual(self, model_id, lod, source_name):
        try:
            source = self.xml(source_name + '.visual_processed')
        except KeyError:
            return self.compiled_visual(model_id, lod)
        primitives = source.findtext('primitivesName') or source_name
        _text(source, 'primitivesName', primitives)
        renders = self.renders(lod)
        groups = {}
        for render_set in source.findall('renderSet'):
            for geometry in render_set.findall('geometry'):
                vertices = _resource(primitives, geometry.findtext('vertices'))
                indices = _resource(primitives, geometry.findtext('primitive'))
                for group in geometry.findall('primitiveGroup'):
                    key = (vertices.lower(), indices.lower(), int(group.text.strip()))
                    if key in groups:
                        raise ValueError('Ambiguous original static primitive group: ' + source_name)
                    groups[key] = (render_set, geometry, group)
        selected = set()
        for render in renders:
            key = (self.strings.get(render['verts_name_fnv']).lower(),
                   self.strings.get(render['prims_name_fnv']).lower(),
                   render['primitive_index'])
            if key not in groups or key in selected:
                raise ValueError('Compiled render does not uniquely match original visual: ' + source_name)
            render_set, geometry, group = groups[key]
            world_space = render_set.findtext('treatAsWorldSpaceObject', 'false').strip() == 'true'
            if bool(render['is_skinned']) != world_space:
                raise ValueError('Compiled render changed the original node transform mode')
            original = group.find('material')
            group.remove(original)
            group.append(self.material(render['material_index'], original))
            selected.add(key)
        for key, (unused, geometry, group) in groups.items():
            if key not in selected:
                geometry.remove(group)
        for render_set in source.findall('renderSet'):
            for geometry in render_set.findall('geometry'):
                if not geometry.findall('primitiveGroup'):
                    render_set.remove(geometry)
            if not render_set.findall('geometry'):
                source.remove(render_set)
        source.append(self.collision(model_id))
        return source

    def write(self, model_id):
        if model_id in self._written:
            return self._written[model_id]
        model = self.source_model(model_id)
        record = self.models['models_loddings'][model_id]
        first, last = record['lod_begin'], record['lod_end']
        if not 0 <= first <= last < len(self.models['lod_loddings']):
            raise ValueError('Compiled static model has an invalid LOD range')
        pending = []
        for offset, lod in enumerate(range(first, last + 1)):
            descriptor = self.descriptor(model_id, lod, model)
            bounds = self.models['models_visibility_bounds'][model_id]
            visibility = descriptor.find('visibilityBox')
            if visibility is None:
                visibility = ET.SubElement(descriptor, 'visibilityBox')
            for name in ('min', 'max'):
                _text(visibility, name, ' '.join(repr(v) for v in bounds[name]))
            visual_kind = next((kind for kind in ('nodefullVisual', 'nodelessVisual')
                                if descriptor.findtext(kind)), None)
            if visual_kind is None:
                raise ValueError('Original static model has no visual: ' + model)
            source_visual = descriptor.findtext(visual_kind)
            visual = self.visual(model_id, lod, source_visual)
            extent = float(descriptor.findtext('extent', '-1'))
            squared = extent * extent if extent >= 0 else 3.4028234663852886e38
            if not math.isclose(squared, self.models['lod_loddings'][lod], rel_tol=2e-6):
                raise ValueError('Original static LOD differs from compiled selection: ' + model)
            name = '%s/%d/lod%d' % (self.prefix, model_id, offset)
            _text(descriptor, visual_kind, name)
            parent = descriptor.findtext('parent')
            if lod < last:
                if not parent:
                    raise ValueError('Original model lacks a compiled LOD parent: ' + model)
                _text(descriptor, 'parent', '%s/%d/lod%d' % (self.prefix, model_id, offset + 1))
                model = parent + '.model'
            elif parent:
                # A compiled state may intentionally retain only a prefix of
                # the source model's LOD chain (for example damage segments).
                descriptor.remove(descriptor.find('parent'))
            pending.extend(((name + '.model', descriptor), (name + '.visual', visual)))
        # No partial model state is emitted if any original LOD failed to match.
        for resource, tree in pending:
            _write(self.output / resource, tree)
        result = '%s/%d/lod0.model' % (self.prefix, model_id)
        self._written[model_id] = result
        return result
