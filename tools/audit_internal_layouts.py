#!/usr/bin/env python3
"""Inventory internal-module source coverage against a scripts.pkg.

This is a read-only source audit, not client identity verification or native
collision acceptance. --verify-meshes checks indexed topology and runtime layouts. Run inspect_client.py on a complete client first when
one is available. No inferred layout is generated for an uncovered vehicle.
"""

import argparse
import base64
from collections import Counter
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'res' / 'scripts' / 'client'))
from gui.mods.offline_lan_0922 import internal_hit_layouts
from gui.mods.offline_lan_0922 import vehicle_blacklist, internal_mesh, internal_layout_console
from packed_xml import read_packed_xml, TYPE_COMPRESSED_STRING, TYPE_ELEMENT


def _text(value):
    if value.value_type == TYPE_COMPRESSED_STRING:
        return base64.b64encode(value.value).decode('ascii')
    return value.value.decode('ascii')


def scan(package_path, verify_meshes=False, bake_report=None, cache=None):
    rows = []
    frames = {}
    if cache is not None:
        from bake_internal_layout_console_0922 import read_client, pc_bounds
        for vehicle, data in read_client(Path(package_path)).items():
            path = Path(cache) / ('pc9.22.0_collision-%d.data' % data['archive_id'])
            if path.exists() and path.stat().st_size:
                frames[vehicle] = pc_bounds(path.read_bytes())
    if bake_report is not None and cache is not None:
        selections = json.loads(Path(bake_report).read_text())['vehicles']
        for vehicle, selection in selections.items():
            donor = selection.get('inherited_from')
            if donor and not frames.get(vehicle) and frames.get(donor):
                frames[vehicle] = frames[donor]
    with zipfile.ZipFile(package_path) as archive:
        listings = sorted(name for name in archive.namelist()
                          if name.startswith('scripts/item_defs/vehicles/')
                          and name.endswith('/list.xml')
                          and name.count('/') == 4)
        for listing in listings:
            nation = listing.split('/')[-2]
            directory = listing.rsplit('/', 1)[0]
            catalog = read_packed_xml(archive.read(listing))
            for raw_name, unused in catalog.children:
                name = raw_name.decode('ascii')
                if ':' in name:
                    continue
                vehicle = nation + ':' + name
                definition = read_packed_xml(archive.read(
                    directory + '/' + name + '.xml'))
                crew = next(value for key, value in definition.children
                            if key == b'crew')
                if crew.value_type != TYPE_ELEMENT:
                    raise ValueError('invalid crew section: ' + vehicle)
                roles = tuple((key.decode('ascii'),) + tuple(_text(value).split())
                              for key, value in crew.value.children)
                profile_key, compiled = internal_hit_layouts._compiled_profile(vehicle)
                profile = internal_hit_layouts._profile_record(compiled)
                if profile is None:
                    status = 'missing_profile'
                elif profile['crew_roles'] != roles:
                    status = 'crew_mismatch'
                else:
                    status = 'profile_matched'
                geometry = geometry_inventory(vehicle, verify_meshes, frames.get(vehicle))
                rows.append({
                    'vehicle': vehicle,
                    'geometry': geometry,
                    'status': status,
                    'profile_key': profile_key,
                    'crew_roles': roles,
                    'blacklisted_resources': vehicle_blacklist.is_unusable(vehicle),
                })
    result = {
        'scope': 'listed descriptors, source mesh topology and retained fallback; no native acceptance',
        'geometry_summary': dict(Counter(row['geometry']['provenance'] for row in rows)),
        'collision_geometry_verified': False,
        'summary': dict(Counter(row['status'] for row in rows)),
        'vehicles': rows,
    }
    if bake_report is not None:
        report = json.loads(Path(bake_report).read_text())
        result['resource_client_evidence'] = report['client']
        result['resource_rejections'] = report['rejected']
        for row in rows:
            row['resource_selection'] = report['vehicles'].get(row['vehicle'], {})
    return result


def mesh_records(record):
    crew, unused = internal_hit_layouts.crew_entities(SimpleNamespace(type=SimpleNamespace(crewRoles=record[2])))
    return ([dict(entity=z[0], parent=z[1], zone_id=z[2], geometry=z[3])
             for z in record[4]] +
            [dict(entity=crew[i]['entity'], parent=z[0], zone_id=z[1], geometry=z[2])
             for i, alternatives in enumerate(record[5]) for z in alternatives or ()])


def descriptor_snapshot(vehicle, record, turret='turret_01', pc_frames=None):
    """Resource frame snapshot for offline queries; no native descriptor claim."""
    values = {'type': SimpleNamespace(name=vehicle, crewRoles=record[2])}
    for parent in ('chassis', 'hull', 'turret', 'gun', 'engine', 'fuelTank', 'radio'):
        values[parent] = SimpleNamespace(name=parent, id=1, compactDescr=1,
            models=None, materials={}, weight=100., yawLimits=(-3.14, 3.14),
            maxAmmo=30, shots=(), hitTester=SimpleNamespace(bbox=None))
    selected = {}
    for item in sorted(mesh_records(record), key=lambda r: r['geometry']['part']):
        g, parent = item['geometry'], item['parent']
        if parent == 'turret' and g['part'] != turret:
            continue
        if parent in selected:
            continue
        selected[parent] = g
        component = values[parent]
        component.name = g['part']
        component.models = SimpleNamespace(undamaged='vehicles/resource/normal/lod0/' + g['part'] + '.model')
        component.hitTester.bbox = tuple(g['reference_bounds']) + (None,)
    for part, bounds in sorted((pc_frames or {}).items()):
        parent = part.split('_')[0]
        if parent not in values or (parent == 'turret' and part != turret):
            continue
        if parent == 'gun' and values[parent].models is not None:
            continue
        component = values[parent]
        component.name = part
        component.models = SimpleNamespace(undamaged='vehicles/resource/normal/lod0/' + part + '.model')
        component.hitTester.bbox = tuple(bounds) + (None,)
    return SimpleNamespace(**values)


def geometry_inventory(vehicle, verify=False, pc_frames=None):
    key, compiled = internal_hit_layouts._compiled_profile(vehicle)
    record = internal_layout_console.CONSOLE_LAYOUTS_0922.get(key)
    if record is None:
        return {'provenance': 'reconstructed_archetype' if compiled is not None else 'unavailable',
                'decoded_meshes': [], 'fallback_retained': compiled is not None}
    meshes = []
    for item in mesh_records(record):
        g = item['geometry']
        row = dict((k, v) for k, v in g.items() if k != 'payload')
        row.update((k, v) for k, v in item.items() if k != 'geometry')
        if verify:
            vertices, faces = internal_mesh.decode(g['payload'])
            pieces = internal_mesh.prepare(vertices, faces)
            row['verified'] = (len(vertices) == g['vertices'] and len(faces) == g['triangles']
                and sum(p['closed'] for p in pieces) == g['closed_pieces']
                and sum(not p['closed'] for p in pieces) == g['open_pieces'])
            if not row['verified']:
                raise ValueError('mesh metadata mismatch: ' + vehicle)
        meshes.append(row)
    result = {'provenance': 'decoded_console_mesh', 'fallback_retained': False,
        'decoded_meshes': meshes, 'missing_modules': record[3],
        'missing_crew_slots': [i for i, alternatives in enumerate(record[5]) if not alternatives],
        'native_owned': ['gun', 'leftTrack', 'rightTrack']}
    if verify:
        configurations = []
        turrets = sorted(set(r['part'] for r in meshes if r['parent'] == 'turret')) or ['turret_01']
        for turret in turrets:
            internal_hit_layouts._LAYOUT_CACHE.clear()
            started = time.perf_counter()
            layout = internal_hit_layouts.build_layout(descriptor_snapshot(vehicle, record, turret, pc_frames), False)
            build_us = (time.perf_counter() - started) * 1e6
            timings = {'build_us': build_us}
            if layout['valid']:
                segments, contexts = {}, {}
                for parent, (low, high) in layout['parents'].items():
                    middle = tuple((low[a]+high[a])*.5 for a in range(3))
                    start = (low[0]-.25,middle[1],middle[2])
                    end = (high[0]+.25,middle[1],middle[2])
                    segments[parent] = (start,end)
                    contexts[parent] = {'point':start,'direction':(1.,0.,0.)}
                operations = {
                    'ray_us': lambda: internal_hit_layouts.resolve_segments(layout,segments),
                    'sphere_us': lambda: internal_hit_layouts.resolve_explosion(layout,contexts,2.),
                    'cone_us': lambda: internal_hit_layouts.resolve_explosion(layout,contexts,2.*2.**.5,
                        mode='cone',cone_cos=2.**-.5,cone_depth_m=2.)}
                for name, operation in operations.items():
                    started = time.perf_counter()
                    operation()
                    timings[name] = (time.perf_counter()-started)*1e6
            configurations.append({'turret': turret, 'targets': len(layout['targets']),
                'valid': layout['valid'], 'query_timings_us': timings, 'errors': layout['errors'], 'rejections': layout['mesh_rejections'],
                'unavailable': sorted(entity for entity, data in layout['logical_entity_sources'].items()
                    if data['mode'] in ('RESOURCE_GEOMETRY_UNMODELLED', 'MISSING'))})
        result['configurations'] = configurations
    return result


def review_report(report):
    """Compact review artifact; full scan retains bounds and per-mesh details."""
    vehicles = []
    for row in report['vehicles']:
        geometry = row['geometry']
        selection = row.get('resource_selection', {})
        meshes = geometry.get('decoded_meshes', ())
        entry = {'vehicle': row['vehicle'], 'provenance': geometry['provenance'],
            'fallback_retained': geometry['fallback_retained'],
            'packages': sorted(set(m['source_package'] for m in meshes)),
            'selected_archive_id': selection.get('archive_id_used'),
            'inherited_from': selection.get('inherited_from'),
            'source_reason': selection.get('reason'),
            'missing_modules': geometry.get('missing_modules', ()),
            'missing_crew_slots': geometry.get('missing_crew_slots', ()),
            'configurations': [dict((k,v) for k,v in c.items() if k != 'query_timings_us')
                for c in geometry.get('configurations', ())],
            'decoded_targets': sorted(set(m['part'] + ':' + m['entity'] + ':' + m['zone_id'] for m in meshes)),
            'open_surface_targets': sorted(set(m['part'] + ':' + m['zone_id'] for m in meshes if m['open_pieces'])),
            'mesh_count': len(meshes),
            'closed_pieces': sum(m['closed_pieces'] for m in meshes),
            'open_pieces': sum(m['open_pieces'] for m in meshes)}
        for key in ('decode_rejections','registration_rejections','earlier_attempts','attempts'):
            if selection.get(key): entry[key] = selection[key]
        vehicles.append(entry)
    return {'scope': report['scope'], 'geometry_summary': report['geometry_summary'],
        'resource_client_evidence': report.get('resource_client_evidence'),
        'resource_rejections': report.get('resource_rejections', {}),
        'vehicles': vehicles}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scripts_package', help='Path to the client scripts.pkg')
    parser.add_argument('--cache', help='Baker cache for exact PC component frames, including native gun')
    parser.add_argument('--verify-meshes', action='store_true')
    parser.add_argument('--bake-report', help='Include selection and rejection evidence from the matching bake')
    parser.add_argument('--compact', action='store_true', help='Emit a compact review artifact')
    args = parser.parse_args()
    report = scan(args.scripts_package, args.verify_meshes, args.bake_report, args.cache)
    print(json.dumps(review_report(report) if args.compact else report, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
