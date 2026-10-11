"""CPython 2.7 differential checks for the persistent destructible frontier.

The maintained sensor is the reference. Native-on runs keep the same identity,
publication and authority owners; the backend must accept every valid query.
These copied host fixtures do not prove Windows native presentation or timing.
"""
from __future__ import print_function
import collections
import copy
import imp
import math
import os
import random
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, 'src', 'res', 'scripts', 'client')
for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
    package = types.ModuleType(name)
    package.__path__ = [os.path.join(CLIENT, 'gui', 'mods', 'offline_lan_0922')] if name.endswith('0922') else []
    sys.modules[name] = package
from gui.mods.offline_lan_0922 import destructibles_sensor as sensor
from gui.mods.offline_lan_0922 import native_destructibles as geometry
from gui.mods.offline_lan_0922 import native_math


class Vector(object):
    def __init__(self, x=0., y=0., z=0.):
        if not isinstance(x, (int, float)):
            x, y, z = x
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]


class Object(object):
    def __init__(self, **values):
        self.__dict__.update(values)


class CheckedBackend(object):
    def __init__(self, backend):
        self.backend, self.calls = backend, collections.Counter()

    def __getattr__(self, name):
        function = getattr(self.backend, name)
        if not name.startswith('destr_'):
            return function
        def checked(*args):
            result = function(*args)
            self.calls[(name, args[1] if len(args) > 1 else -1)] += 1
            assert result is not None, ('native rejection', name, args)
            return result
        return checked


checks = 0


def equal(actual, expected, label):
    global checks
    checks += 1
    if isinstance(expected, dict):
        assert set(actual) == set(expected), (label, actual, expected)
        for key in expected:
            equal(actual[key], expected[key], label + '/' + str(key))
    elif isinstance(expected, (tuple, list)):
        assert len(actual) == len(expected), (label, actual, expected)
        for index, values in enumerate(zip(actual, expected)):
            equal(values[0], values[1], label + '/%d' % index)
    elif isinstance(expected, float):
        assert abs(actual - expected) <= 2.e-12 * max(1., abs(expected)), (label, actual, expected)
    else:
        assert actual == expected, (label, actual, expected)


def modes(function, label):
    geometry._disabled = True
    reference = function()
    geometry._disabled = False
    actual = function()
    equal(actual, reference, label)
    return actual


def box(x, y, z, hx=1., hy=1., hz=1., material=None):
    return ((x, y, z), ((hx, 0., 0.), (0., hy, 0.), (0., 0., hz)), material)


def index_registry(items):
    result = dict(bins={}, extended_bins={}, count=len(items), native_count=len(items),
                  max_radius=0., tree_health={}, slot_diagnostics={})
    for item in items:
        if item[8]:
            for world_box in item[8]:
                for cell in sensor._bin_keys_for_bounds(*sensor._box_xz_bounds(world_box)):
                    result['extended_bins'].setdefault(cell, []).append(item)
        else:
            cell = sensor._destructible_bin_key(item[1], item[3])
            result['bins'].setdefault(cell, []).append(item)
        result['max_radius'] = max(result['max_radius'], item[9])
    return result


def base_environment():
    area = types.ModuleType('AreaDestructibles')
    area.DESTR_TYPE_TREE, area.DESTR_TYPE_FALLING_ATOM = 1, 2
    area.DESTR_TYPE_FRAGILE, area.DESTR_TYPE_STRUCTURE = 3, 4
    area.DESTRUCTIBLE_HIDING_DELAY = .2
    area.chunkIDFromPosition = lambda position: 22
    area.g_destructiblesManager = Object(
        getSpaceID=lambda: 1, _DestructiblesManager__loadedChunkIDs={22: 0})
    sys.modules['AreaDestructibles'] = area
    sys.modules['BigWorld'] = Object(time=lambda: 10.)
    sys.modules['Math'] = Object(Vector3=Vector)
    sys.modules['DestructiblesCache'] = Object(
        scaledDestructibleHealth=lambda scale, health: scale * health)
    sensor.LOG_DEBUG = lambda *args: None
    sensor._diagnostic_contact_1513 = lambda *args, **kwargs: None
    sensor._destructible_catalog = None
    sensor._clear_runtime_registry()
    return area


def geometry_cases():
    rng = random.Random(151307)
    for index in range(300):
        bbox = ((-rng.uniform(.1, 4.), -rng.uniform(.1, 2.), -rng.uniform(.1, 6.)),
                (rng.uniform(.1, 4.), rng.uniform(.1, 2.), rng.uniform(.1, 6.)), None)
        position = Vector(*(rng.uniform(-100., 100.) for unused in range(3)))
        yaw, pitch, roll = (rng.uniform(-math.pi, math.pi) for unused in range(3))
        travel = rng.uniform(-20., 20.) if index % 3 else 0.
        motion_yaw = None if index % 2 else rng.uniform(-math.pi, math.pi)
        current = modes(lambda: sensor._vehicle_contact_box(
            position, yaw, bbox, travel, motion_yaw, pitch, roll), 'vehicle-%d' % index)
        end = Vector(position.x+rng.uniform(-25.,25.), position.y+rng.uniform(-2.,2.),
                     position.z+rng.uniform(-25.,25.))
        end_yaw = yaw + rng.uniform(-3.5, 3.5)
        pivot = rng.uniform(-2., 2.) if index % 4 == 0 else 0.
        sweeps = modes(lambda: sensor._tree_pose_sweep_boxes_1513(
            position, yaw, end, end_yaw, bbox, pivot), 'sweep-%d' % index)
        worlds = [box(position.x+rng.uniform(-12.,12.), position.y+rng.uniform(-4.,4.),
                      position.z+rng.uniform(-12.,12.), rng.uniform(.1,3.),
                      rng.uniform(.1,3.), rng.uniform(.1,3.), 73) for unused in range(12)]
        worlds.extend(value + (None,) for value in sweeps[:3])
        modes(lambda: sensor._catalog_intersections(worlds, current), 'intersections-%d' % index)
    # The same source guard must reject an unbounded sweep before admitting hits.
    geometry._disabled = True
    assert sensor._tree_pose_sweep_boxes_1513(Vector(), 0., Vector(2000.,0.,0.), 0., bbox) is None
    geometry._disabled = False
    assert geometry._owner.backend.backend.destr_query(geometry._owner.handle, 5,
        ((0.,0.,0.),0.,(2000.,0.,0.),0.,bbox[0],bbox[1],0.)) is None


def catalog_cases():
    sensor._clear_runtime_registry()
    sensor._destructible_catalog = {'baked_shot_bins': {(-1,0): {(22,9)}, (0,0): {(22,9),(23,4)}}}
    instances = {
        (22, 1): dict(kind='fragile', filename='f.model', item_scale=1.,
                     boxes=(box(0.,0.,0.), box(0.,0.,0.))),
        (22, 2): dict(kind='structure', filename='s.model', item_scale=1.,
                     boxes=(box(1.,0.,1.,material=74), box(1.,0.,1.,material=73),
                            box(1.,0.,1.,material=73), box(2.,0.,1.,material=73))),
        (4294967000, 8): dict(kind='fragile', filename='wide.model', item_scale=1.,
                     boxes=(box(0.,0.,0.),)),
        (23, 1): dict(kind='falling', filename='c.model', item_scale=.8,
                     boxes=(box(9.,0.,9.),)),
    }
    sensor.g_offh_destr_instances = instances
    sensor.g_offh_destr_contact_bins = bins = {}
    for key, instance in sorted(instances.items()):
        sensor._index_catalog_instance_1513(bins, key, instance)
    for index in range(45):
        sweep = sensor._vehicle_contact_box(Vector(index-20,0.,index*.2-3.), .15*index,
                  ((-2.,-1.,-4.),(2.,1.,4.)), travel=3.)
        modes(lambda: sensor._catalog_contact_candidates(sweep), 'catalog-%d' % index)
        native = geometry.catalog(sensor, sweep, sweep)
        expected_groups = {}
        for candidate in sensor._catalog_contact_candidates(sweep):
            expected_groups.setdefault(candidate[:2], {}).setdefault(candidate[2], candidate)
        converted = dict((key, [(candidate, True) for material,candidate in sorted(
            rows.items(), key=lambda entry: -1 if entry[0] is None else entry[0])])
            for key, rows in expected_groups.items())
        equal(dict((key,value[0]) for key,value in native[1].items()), converted, 'groups')
        expected_baked = set()
        for cell in sensor._bin_keys_for_bounds(*sensor._box_xz_bounds(sweep)):
            expected_baked.update(sensor._destructible_catalog['baked_shot_bins'].get(cell, ()))
        equal(geometry.baked_candidates(sensor,sweep), sorted(expected_baked), 'baked')
    # A falling transform replaces cached boxes at the same wire, then an exact
    # item removal and a streamed chunk retirement invalidate both indexes.
    query = box(0.,0.,0.,30.,4.,30.)
    instances[(23,1)]['boxes'] = (box(100.,0.,100.),)
    sensor._index_catalog_instance_1513(bins,(23,1),instances[(23,1)])
    modes(lambda: sensor._catalog_contact_candidates(query), 'falling-refresh')
    sensor._drop_isolated_destructible_1513(22,1)
    modes(lambda: sensor._catalog_contact_candidates(query), 'item-drop')
    state = {'chunks':{22:{},23:{}}}
    sensor._drop_streamed_chunk_registry_1513(state,22)
    modes(lambda: sensor._catalog_contact_candidates(query), 'chunk-drop')
    sensor._destructible_catalog['baked_shot_bins'] = {(0,0): {(99,7)}}
    geometry.refresh_baked(sensor)
    equal(geometry.baked_candidates(sensor,box(0.,0.,0.)), [(99,7)], 'layout-remap')


def tree_cases(area):
    sensor._clear_runtime_registry()
    sensor._destructible_catalog = None
    rng = random.Random(707)
    items=[]
    for index in range(90):
        x,z = rng.uniform(-15.,15.),rng.uniform(-15.,15.)
        typ = 1 if index % 3 else 2
        boxes = (box(x,0.,z,2.,1.,2.),) if index % 5 == 0 else ()
        items.append((index,x,0.,z,typ,'tree.model' if index%7 else '',50.,100.,boxes,3. if boxes else 0.))
    registry=index_registry(items)
    for index in range(60):
        start,end=Vector(rng.uniform(-10.,10.),0.,rng.uniform(-10.,10.)),Vector(rng.uniform(-10.,10.),0.,rng.uniform(-10.,10.))
        sweeps=sensor._tree_pose_sweep_boxes_1513(start,.1*index,end,.15*index,((-2.,-1.,-4.),(2.,1.,4.)))
        for radius in (0., .5):
            modes(lambda: sensor._tree_candidates_for_sweeps_1513(22,registry,sweeps,1,{},radius), 'trees-%d' % index)
    # A new registry at the same chunk ID must replace the persistent numeric copy.
    replacement = index_registry([items[0]])
    modes(lambda: sensor._tree_candidates_for_sweeps_1513(22,replacement,
        (box(0.,0.,0.,30.,3.,30.),),1,{},.5), 'chunk-reload')


def proximity_run(area, enabled, registry, speed):
    sensor._clear_runtime_registry()
    geometry._disabled = not enabled
    sensor._destructible_catalog = None
    sensor.g_offh_destr_runtime_space = 1
    state = {'chunks': {22: copy.deepcopy(registry)}, 'felled': set(), 'spaceID': 1,
             'canonical_published': set(), 'publish_pending': {}}
    state['native_committed'] = state['felled']
    sensor.g_offh_tree_state = state
    area.g_destructiblesManager._DestructiblesManager__loadedChunkIDs = {22: registry['native_count']}
    events, calls, destroyed = [], [], set()
    def destroy(kind, *args):
        calls.append((kind, args[:-1], (args[-1].x,args[-1].y,args[-1].z)))
        destroyed.add((args[1],args[2],None))
        return True
    authority=Object(is_destroyed=lambda *key: key in destroyed,
        destroy_tree=lambda *args: destroy('tree',*args),
        destroy_column=lambda *args: destroy('column',*args))
    sensor._get_destr_authority=lambda: authority
    sensor.validate_tree_identity_1513=lambda *args: True
    sensor.set_event_sink(lambda event: events.append(copy.deepcopy(event)) or True)
    for unused in range(2):
        sensor._fell_trees_near(1,Vector(),.13,speed)
    assert len(calls) == (2 if abs(speed) >= 1. else 0), (speed, calls)
    assert len(events) == len(calls), (speed, events)
    return calls, events, state['felled'], state['publish_pending']


def proximity_cases(area):
    items=[(0,0.,0.,1.,1,'tree.model',50.,100.,(),0.),
           (1,1.,0.,1.,2,'pole.model',50.,100.,(),0.),
           (2,0.,0.,2.,3,'fence.model',5.,100.,(box(0.,0.,2.),),2.),
           (3,0.,0.,20.,1,'tree.model',50.,100.,(),0.),
           (4,0.,0.,-1.,1,'',50.,100.,(),0.),
           (5,9.,0.,0.,2,'pole.model',50.,100.,(),0.)]
    registry=index_registry(items)
    for speed in (0.,.999,1.,-1.,12.):
        equal(proximity_run(area,True,registry,speed),proximity_run(area,False,registry,speed),
              'body-owner-%g' % speed)


def catalog_owner_run(area, enabled):
    sensor._clear_runtime_registry()
    geometry._disabled = not enabled
    sensor._destructible_catalog = {'resources': {}}
    sensor.g_offh_destr_runtime_space = 1
    sensor.g_offh_destr_contact_bins = bins = {}
    sensor.g_offh_destr_instances = instances = {}
    for index,kind in enumerate(('fragile','structure','falling')):
        key=(22,index+37)
        material=73 if kind=='structure' else None
        instance=dict(kind=kind,filename=kind+'.model',item_scale=1.,
            boxes=(box(.2*index,0.,2.,.5,1.,.5,material),))
        if kind=='structure':
            instance['boxes'] += (box(.2*index,0.,2.,.5,1.,.5,74),)
        instances[key]=instance
        sensor._index_catalog_instance_1513(bins,key,instance)
    descriptor=Object(physics={'weight':40000.},hull=Object(hitTester=Object(
        bbox=((-1.6,-1.,-3.6),(1.6,1.,3.6),None))))
    descriptions={'fragile.model': {'type':3,'health':5,'kineticDamageCorrection':1.},
        'structure.model': {'type':4,'modules': {73:{'health':5},74:{'health':100000}}},
        'falling.model': {'type':2,'health':50,'mass':100.,'kineticDamageCorrection':1.}}
    area.g_cache=Object(unitVehicleMass=10000.,getDescByFilename=lambda name: descriptions[name])
    calls,events,destroyed=[],[],set()
    def destroy(kind,*args):
        item=args[1:3];material=args[3] if kind=='module' else None
        calls.append((kind,item,material));destroyed.add(item+(material,));return True
    authority=Object(is_destroyed=lambda *key: key in destroyed,
        destroy_fragile=lambda *args: destroy('fragile',*args),
        destroy_module=lambda *args: destroy('module',*args),
        destroy_column=lambda *args: destroy('column',*args))
    sensor._get_destr_authority=lambda: authority
    sensor.set_event_sink(lambda event: events.append(copy.deepcopy(event)) or True)
    output=[]
    for time,speed in ((10.,.5),(11.,20.),(12.,1.),(13.,20.)):
        output.append(sensor._catalog_motion_blocked(1,Vector(),0.,speed,descriptor,time,
            return_detail=True,kinetic_speed=speed,kinetic_commit=True))
    assert ('fragile',(22,37),None) in calls and ('module',(22,38),73) in calls, calls
    assert not any(value[2] == 74 for value in calls), calls
    assert len(events) == len(calls), (events,calls)
    return output,calls,events


def main():
    backend=CheckedBackend(imp.load_dynamic('offline_math_batch_native',sys.argv[1]))
    native_math._backend=backend
    native_math._attempted=True
    area=base_environment()
    geometry_cases()
    catalog_cases()
    tree_cases(area)
    proximity_cases(area)
    equal(catalog_owner_run(area,True),catalog_owner_run(area,False),'catalog-owner')
    geometry.reset()
    # Close is idempotent; stale handles and malformed finite geometry reject.
    raw=backend.backend
    handle=raw.destr_open()
    assert raw.destr_query(handle,4,((0.,0.,0.),float('nan'),0.,0.,0.,None,(-1.,-1.,-1.),(1.,1.,1.))) is None
    assert raw.destr_update(handle,2,(22.,((0,(0.,0.,0.),1,1,0,0.),),(((0,0),(1,)),),())) is None
    assert raw.destr_close(handle)==1 and raw.destr_close(handle)==1
    assert raw.destr_query(handle,3,box(0.,0.,0.)) is None
    assert set(op for (name,op) in backend.calls if name=='destr_query') == set(range(7))
    assert set(op for (name,op) in backend.calls if name=='destr_update') == set(range(5))
    print('Native destructible frontier parity passed: %d field checks; %d accepted calls; geometry, ordered candidates, full body/catalog owners, stream replacement and lifecycle.' % (checks,sum(backend.calls.values())))


if __name__=='__main__':
    main()
