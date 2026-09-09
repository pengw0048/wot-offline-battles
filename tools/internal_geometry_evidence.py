#!/usr/bin/env python3
"""Reproduce IS-7 runtime X-rays, finite shoulder rays and roster mesh timings.

Requires matplotlib/numpy for plots; inputs are the baker cache and scripts.pkg.
Exports actual build_layout primitives in isolated processes for each revision.
The output is source/logic evidence, never a Windows render or damage probability.
"""
import argparse
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import time
import zipfile

from audit_internal_layouts import descriptor_snapshot, mesh_records
from bake_internal_layout_console_0922 import pc_bounds, _section
from bake_navigation_0922 import _vertex_positions_0922
import bw_primitives_geometry as bw
from packed_xml import read_packed_xml
from gui.mods.offline_lan_0922 import internal_hit_layouts as layouts
from gui.mods.offline_lan_0922 import internal_layout_console as catalog
from gui.mods.offline_lan_0922 import internal_mesh as mesh

ROOT = Path(__file__).resolve().parents[1]
VEHICLE = 'ussr:R45_IS-7'
EXPORT = '''
import json,sys
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from gui.mods.offline_lan_0922 import internal_hit_layouts as layouts
raw=json.load(open(sys.argv[2]))
def obj(value):
    if isinstance(value, dict):
        return SimpleNamespace(**dict((k, obj(v)) for k,v in value.items()))
    if isinstance(value, list): return tuple(obj(v) for v in value)
    return value
descriptor=obj(raw)
for name in ('chassis','hull','turret','gun','engine','fuelTank','radio'):
    getattr(descriptor,name).materials={}
layout=layouts.build_layout(descriptor,False)
for target in layout['targets']:
    for primitive in target.get('primitives',()): primitive.pop('bvh',None)
json.dump(layout,open(sys.argv[3],'w'),indent=1,sort_keys=True)
'''


def export_runtime(runtime_root, descriptor, output):
    snapshot = output.with_suffix('.descriptor.json')
    snapshot.write_text(json.dumps(descriptor, default=lambda o: vars(o)))
    subprocess.run([sys.executable, '-c', EXPORT, str(runtime_root / 'src/res/scripts/client'),
                    str(snapshot), str(output)], check=True)
    return json.loads(output.read_text())


def frames(package):
    with zipfile.ZipFile(package) as archive:
        root = read_packed_xml(archive.read('scripts/item_defs/vehicles/ussr/R45_IS-7.xml'))
    hull = _section(root, b'hull').value
    turret = _section(root, b'turrets0').value.children[0][1].value
    def vector(value):
        return struct.unpack('<3f', value.value) if len(value.value) == 12 else tuple(map(float, value.value.split()))
    hp = vector(_section(_section(root, b'chassis').value.children[0][1].value, b'hullPosition'))
    tp = vector(_section(hull, b'turretPositions').value.children[0][1])
    gp = vector(_section(turret, b'gunPosition'))
    add = lambda a, b: tuple(a[i] + b[i] for i in range(3))
    return {'chassis': (0., 0., 0.), 'hull': hp, 'turret': add(hp, tp), 'gun': add(add(hp, tp), gp)}


def exterior(path):
    result = {}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if '/collision_client/' not in name or not name.endswith('.primitives_processed'):
                continue
            part = name.rsplit('/', 1)[-1].split('.')[0].lower()
            sections = bw._sections(archive.read(name))
            vertices = _vertex_positions_0922(sections['vertices'])
            indices, groups = bw._indices_and_groups(sections['indices'])
            faces = []
            for material, (start, count, unused, unused2) in zip(bw._material_names(sections['bsp2_materials']), groups):
                if material.startswith('armor') or part == 'chassis' or material == 'gun':
                    faces.extend(indices[i:i + 3] for i in range(start, start + count * 3, 3))
            result[part.split('_')[0]] = (vertices, faces)
    return result


def primitive_triangles(p):
    """Tessellate only for display; queries always use the runtime primitives."""
    import numpy as np
    if p['shape'] == 'mesh':
        return np.asarray(p['vertices'])[p['triangles']]
    center = np.asarray(p['center'])
    if p['shape'] in ('aabb', 'obb', 'box'):
        half = p['half_extents']
        vertices = np.array([(x, y, z) for x in (-half[0], half[0])
            for y in (-half[1], half[1]) for z in (-half[2], half[2])])
        faces = ((0,1,3),(0,3,2),(4,6,7),(4,7,5),(0,4,5),(0,5,1),
                 (2,3,7),(2,7,6),(0,2,6),(0,6,4),(1,5,7),(1,7,3))
    else:
        points, faces = [], []
        rings, sectors = 17, 24
        axis = {'x': 0, 'y': 1, 'z': 2}.get(p.get('axis'), 1)
        for j in range(rings):
            latitude = -math.pi / 2 + math.pi * j / (rings - 1)
            for i in range(sectors):
                angle = math.tau * i / sectors
                v = [math.cos(latitude)*math.cos(angle), math.sin(latitude), math.cos(latitude)*math.sin(angle)]
                if p['shape'] == 'capsule':
                    v = [x * p['radius'] for x in v]
                    v[1] += (-1 if latitude < 0 else 1) * p['half_length']
                    v[axis], v[1] = v[1], v[axis]
                else:
                    radii = p.get('radii', [p.get('radius', 0.)] * 3)
                    v = [v[a]*radii[a] for a in range(3)]
                points.append(v)
        for j in range(rings - 1):
            for i in range(sectors):
                a, b = j*sectors+i, j*sectors+(i+1)%sectors
                faces.extend(((a,b,b+sectors),(a,b+sectors,a+sectors)))
        vertices = np.asarray(points)
    yaw = math.radians(p.get('rotation_yaw_degrees', 0.))
    rotation = np.array(((math.cos(yaw),0,-math.sin(yaw)),(0,1,0),(math.sin(yaw),0,math.cos(yaw))))
    return (vertices @ rotation.T + center)[np.asarray(faces)]


def render(before, after, shell, offsets, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from matplotlib.collections import PolyCollection
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    import numpy as np
    views = [('Top', (2,0), (-4,7), (-2,2)),
             ('Left side', (2,1), (-4,7), (0,3.2)),
             ('Front', (0,1), (-2,2), (0,3.2)),
             ('Oblique', None, None, None)]
    fig = plt.figure(figsize=(14,16), facecolor='#f5f7fa')
    labels = ('X / m', 'Y / m', 'Z / m (front +)')
    for row,(title,axes,xlim,ylim) in enumerate(views):
        for col,(label,layout) in enumerate((('Before: runtime fitted primitives',before),('After: runtime Console triangles',after))):
            if axes is None:
                ax=fig.add_subplot(4,2,row*2+col+1,projection='3d',computed_zorder=False)
            else:
                ax=fig.add_subplot(4,2,row*2+col+1)
            def polygons(triangles,color,alpha,order):
                if axes is None:
                    collection=Poly3DCollection(triangles[:,:,[0,2,1]],facecolor=color,edgecolor=color,linewidth=.15,alpha=alpha,zorder=order)
                    ax.add_collection3d(collection)
                else:
                    collection=PolyCollection(triangles[:,:,axes],facecolor=color,edgecolor=color,linewidth=.15,alpha=alpha,zorder=order)
                    ax.add_collection(collection)
            for parent,(vertices,faces) in shell.items():
                polygons(np.asarray(vertices)[np.asarray(faces)]+offsets[parent], '#788b9f', .065, 1)
            for target in layout['targets']:
                if target['entity'] != 'ammoBay' and target['kind'] != 'crew':
                    continue
                color='#edae13' if target['entity']=='ammoBay' else '#178fc0'
                for primitive in target['primitives']:
                    polygons(primitive_triangles(primitive)+offsets[target['parent']],color,.52,2)
            if axes is None:
                ax.set(xlim=(-2,2),ylim=(-4,7),zlim=(0,3.2),xlabel=labels[0],ylabel=labels[2],zlabel=labels[1])
                ax.set_box_aspect((4,11,3.2));ax.view_init(elev=24,azim=48);ax.set_proj_type('ortho')
                ax.set_xticks([-2,0,2]);ax.set_zticks([0,1.5,3])
            else:
                ax.set(xlim=xlim,ylim=ylim,xlabel=labels[axes[0]],ylabel=labels[axes[1]])
                ax.set_aspect('equal');ax.grid(alpha=.15)
            ax.set_title(title+' | '+label,fontsize=11)
    fig.suptitle('IS-7 | actual runtime geometry, before and after',fontsize=18,y=.99)
    fig.legend(handles=[Patch(color='#edae13',label='Ammunition'),Patch(color='#178fc0',label='Crew'),Patch(color='#788b9f',alpha=.3,label='PC 9.22 collision_client exterior')],loc='upper center',bbox_to_anchor=(.5,.972),ncol=3)
    fig.text(.5,.012,'Console 4.3 interiors; PC 9.22 component frames from scripts.pkg. Offline X-ray, not a Windows capture.\nBoth revisions use the same PC frame snapshot. Geometry alone establishes neither penetration nor damage probability.',ha='center',fontsize=10)
    fig.subplots_adjust(top=.935,bottom=.055,hspace=.38,wspace=.18)
    fig.savefig(output/'is7-runtime-xray.png',dpi=160)
    fig.savefig(output/'is7-runtime-xray.svg')
    plt.close(fig)


def shoulder_rays(shell, layout, output, label='after'):
    """Defined front-shoulder grid, not a reconstruction of an absent screenshot."""
    vertices, faces = shell['hull']
    rows=[]
    targets=[t for t in layout['targets'] if t['entity']=='ammoBay' and t['parent']=='hull']
    from gui.mods.offline_lan_0922 import internal_geometry
    # Original exported targets retain exact faces but omit only acceleration.
    for target in targets:
        target['primitives']=tuple(piece for p in target['primitives'] for piece in (mesh.prepare(p['vertices'],p['triangles'],p['primitive_id']) if p['shape'] == 'mesh' else (p,)))
    for x in (-1.35,-1.15,-.95,.95,1.15,1.35):
        for y in (.2,.4,.6):
            for yaw in (-20.,0.,20.):
                direction=(math.sin(math.radians(yaw)),0.,-math.cos(math.radians(yaw)))
                aim=(x,y,2.3)
                start=tuple(aim[a]-direction[a]*4 for a in range(3))
                hits=[]
                for f in faces:
                    hit=mesh._triangle_line(start,direction,*(vertices[i] for i in f))
                    if hit is not None and hit[0]>=0: hits.append(hit[0])
                if not hits: continue
                first=min(hits)
                for caliber in (100.,130.,152.):
                    travel=first+caliber/100.
                    end=tuple(start[a]+direction[a]*travel for a in range(3))
                    contacts=[interval for t in targets for interval in internal_geometry.target_intervals(start,end,t)]
                    rows.append({'aim_hull_m':aim,'yaw_degrees':yaw,'caliber_mm':caliber,'start_hull_m':start,
                        'first_hull_material_m':first,'end_hull_m':end,'ammo_contact':bool(contacts),
                        'first_ammo_distance_m':min(c[0]*travel for c in contacts) if contacts else None})
    report={'scope':'Front-shoulder geometry grid; finite ten-calibre hull-only trace. Tracks/spaced plates may shorten this budget. No penetration or damage roll.',
        'cases':len(rows),'ammo_contacts':sum(r['ammo_contact'] for r in rows),'rays':rows}
    (output/('is7-shoulder-rays-' + label + '.json')).write_text(json.dumps(report,indent=1))
    return report


def benchmark(output):
    seen=set(); timings={'prepare':[],'ray':[],'distance':[],'cone':[]}; stats={k:{} for k in ('ray','distance','cone')}
    triangles=pieces_count=0
    flat_timings={k:[] for k in ('ray','distance','cone')}; comparisons=0
    for record in catalog.CONSOLE_LAYOUTS_0922.values():
        for row in mesh_records(record):
            payload=row['geometry']['payload']
            if payload in seen: continue
            seen.add(payload)
            before=time.perf_counter();pieces=mesh.prepare(*mesh.decode(payload));timings['prepare'].append(time.perf_counter()-before)
            for p in pieces:
                pieces_count+=1;triangles+=len(p['triangles'])
                low,high=p['minimum'],p['maximum'];c=p['center']
                start=(low[0]-.5,c[1],c[2]);end=(high[0]+.5,c[1],c[2])
                operations={'ray':lambda:mesh.intervals(start,end,p,stats['ray']),
                    'distance':lambda:mesh.distance(start,p,stats['distance']),
                    'cone':lambda:mesh.intersects_cone(p,start,(1.,0.,0.),high[0]-low[0]+1.,.25,stats['cone'])}
                for name,call in operations.items():
                    before=time.perf_counter();actual=call();timings[name].append(time.perf_counter()-before)
                    tree=p['bvh']
                    p['bvh']=(p['minimum'],p['maximum'],None,None,tuple(range(len(p['triangles']))))
                    before=time.perf_counter()
                    if name=='ray': expected=mesh.intervals(start,end,p)
                    elif name=='distance': expected=mesh.distance(start,p)
                    else: expected=mesh.intersects_cone(p,start,(1.,0.,0.),high[0]-low[0]+1.,.25)
                    flat_timings[name].append(time.perf_counter()-before);p['bvh']=tree
                    # Equidistant faces may choose different closest points.
                    if name=='distance':
                        if abs(actual[0]-expected[0])>1e-9: raise AssertionError('BVH distance mismatch')
                    elif actual!=expected: raise AssertionError('BVH query mismatch: '+name)
                    comparisons+=1
    summary={}
    for name,values in timings.items():
        values.sort();summary[name]={'queries':len(values),'median_us':values[len(values)//2]*1e6,'p95_us':values[int(len(values)*.95)]*1e6,'max_us':max(values)*1e6,'total_s':sum(values)}
    report={'scope':'CPython source geometry benchmark; one center-crossing ray, external distance and cone per actual piece. Not Windows frame pacing.',
        'python':sys.version,'unique_meshes':len(seen),'pieces':pieces_count,'triangles':triangles,'timings':summary,'tested_triangles':stats,'brute_force_comparisons':comparisons,
        'brute_force_query_seconds':{k:sum(v) for k,v in flat_timings.items()}}
    (output/'roster-mesh-benchmark.json').write_text(json.dumps(report,indent=1));return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scripts-package',required=True)
    parser.add_argument('--cache',required=True)
    parser.add_argument('--baseline-root',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args();output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    path=Path(args.cache)/'pc9.22.0_collision-7169.data'
    record=catalog.CONSOLE_LAYOUTS_0922[layouts._profile_key(VEHICLE)]
    descriptor=descriptor_snapshot(VEHICLE,record,pc_frames=pc_bounds(path.read_bytes()))
    before=export_runtime(Path(args.baseline_root),descriptor,output/'is7-before.json')
    after=export_runtime(ROOT,descriptor,output/'is7-after.json')
    shell=exterior(path);offsets=frames(args.scripts_package)
    (output/'is7-source-frames.json').write_text(json.dumps(offsets,indent=1))
    render(before,after,shell,offsets,output)
    previous=shoulder_rays(shell,before,output,'before')
    rays=shoulder_rays(shell,after,output);bench=benchmark(output)
    print(json.dumps({'shoulder_cases':rays['cases'],'before_shoulder_contacts':previous['ammo_contacts'],'after_shoulder_contacts':rays['ammo_contacts'],'benchmark':bench},indent=1))


if __name__=='__main__': main()
