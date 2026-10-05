"""Real destructible laws on an explicit analytic #1513 engine seam.

Only streaming matrices, resource descriptors and WGDE order acceptance are
synthetic. Kinetic admission, accepted identity ledger, publication, proposal /
commit barriers, live collision filters and recasts use production modules.
The accepted original skin remains in the analytic engine deliberately: physical
filters must reveal the independent backing wall. This is not native Windows
WGDE animation, replacement-BSP, rendering or memory acceptance evidence.

The shape/manager seam follows tests/test_port_0922_destructibles.py's
_authority_environment and _direction_catalog_fixture without importing its
Python-3 unittest machinery. Run each arm in a fresh process (module singletons).
"""
from __future__ import print_function

import sys
import types

FILENAME = 'content/environment/host_fixture/fragile.model'
CHUNK, ITEM = 7, 0


class Namespace(object):
    def __init__(self, **values):
        self.__dict__.update(values)


class Matrix(object):
    """Identity basis plus a streamed world translation."""
    def __init__(self, vector, translation=(0., 0., 0.)):
        self.vector = vector
        self.translation = vector(translation)
    def applyVector(self, point):
        return self.vector(point)
    def applyPoint(self, point):
        return self.translation + self.vector(point)


class Manager(object):
    def __init__(self, scene, space):
        self.scene = scene
        self.space = space
        self.orders = []
        self._DestructiblesManager__loadedChunkIDs = {CHUNK: 1}
        self._DestructiblesManager__destructiblesWaitDestroy = {}
        self._DestructiblesManager__destroyCallbacks = {}
        self.controller = Namespace(fallenTrees=[], fallenColumns=[],
                                    destroyedFragiles=[], destroyedModules=[])
    def getSpaceID(self):
        return self.space
    def startSpace(self, space):
        self.space = space
    def isChunkLoaded(self, chunk):
        return int(chunk) in self._DestructiblesManager__loadedChunkIDs
    def getController(self, chunk):
        return self.controller if int(chunk) == CHUNK else None
    def orderDestructibleDestroy(self, chunk, damage_type, data, apply, sync):
        # Engine seam only: accept the stock-style WGDE order. Production
        # authority owns deduplication, ledger commit and canonical publication.
        if int(chunk) != CHUNK or int(damage_type) != 3:
            raise ValueError('unexpected fixture destruction order')
        if (int(data) >> 8) != ITEM:
            raise ValueError('unexpected fixture item')
        order = (int(chunk), int(damage_type), data, bool(apply), bool(sync))
        token = self.scene.frontier_start('orderDestructibleDestroy', order)
        self.orders.append(order)
        self.scene.effect('destructible_native_order', order=order,
                          original_skin_retained=True)
        self.scene.frontier_end(token, None)


class Fixture(object):
    def __init__(self, scene, owner):
        self.scene, self.owner = scene, owner
        self.vector = scene.math.Vector3
        self.space = int(owner._avatar.spaceID)
        self.manager = Manager(scene, self.space)
        # The original host descriptor only declared speed limits. Copy its
        # already-resolved actor mass into the exact kinetic input field; do
        # not invent a new mass or change the physics integrator parameters.
        physics = scene.descriptor.physics
        if 'weight' not in physics:
            runtime = getattr(scene, 'runtime', None)
            masses = set(float(state['mass']) for state in
                         getattr(runtime, 'states', {}).values())
            if len(masses) != 1 or min(masses) <= 0.:
                raise ValueError('fixture needs descriptor.physics weight or one resolved actor mass')
            physics['weight'] = masses.pop()
        self._saved_modules = dict((name, sys.modules.get(name)) for name in
                                  ('BigWorld', 'Math', 'AreaDestructibles',
                                   'DestructiblesCache'))
        engine = scene.engine
        # Keep the same live geometry in every arm; receipt filters, not an
        # always-clear engine response, must distinguish the intact skin.
        self.fragile = dict(id='host_fragile', minimum=(30., .01, -8.),
            maximum=(50., 2., -7.), kind='fragile', alive=True,
            material=73, collision_flags=0, chunk_id=CHUNK, item_index=ITEM)
        self.wall = dict(id='host_fragile_backing_wall',
            minimum=(30., .01, 4.), maximum=(50., 5., 5.),
            kind='hard', alive=True)
        engine.boxes.extend((self.fragile, self.wall))
        area = types.ModuleType('AreaDestructibles')
        area.g_destructiblesManager = self.manager
        area._DAMAGE_TYPE_TREE = area.DESTR_TYPE_TREE = 1
        area._DAMAGE_TYPE_COLUMN = area.DESTR_TYPE_FALLING_ATOM = 2
        area._DAMAGE_TYPE_FRAGILE = area.DESTR_TYPE_FRAGILE = 3
        area._DAMAGE_TYPE_MODULE = area.DESTR_TYPE_STRUCTURE = 4
        area.DESTRUCTIBLE_HIDING_DELAY = .2
        area.DESTRUCTIBLE_MATKIND = Namespace(NORMAL_MIN=71)
        area.chunkIDFromPosition = lambda point: (
            CHUNK if 0. <= point.x < 100. and -100. <= point.z < 0. else None)
        # Exact seven-item MatInfo and original material range are preserved.
        area.encodeFragile = lambda item, shot: (int(item) << 8) | int(bool(shot))
        area.g_cache = Namespace(unitVehicleMass=10000.,
            getDescByFilename=lambda filename: ({'type': 3, 'health': 5.,
                'kineticDamageCorrection': 1.} if filename == FILENAME else None))
        cache = types.ModuleType('DestructiblesCache')
        # The resource scale is exactly 1 here; no scaled health law is inferred.
        cache.scaledDestructibleHealth = self.scaled_health
        scene.math.Matrix = lambda value: value
        engine.wg_getChunkDestrFilenames = lambda space, chunk: (
            (FILENAME,) if int(chunk) == CHUNK else ())
        engine.wg_getDestructibleEffectCategory = lambda space, chunk, item, *args: (
            3 if (int(chunk), int(item)) == (CHUNK, ITEM) else -1)
        engine.wg_getChunkMatrix = lambda space, chunk: Matrix(self.vector)
        engine.wg_getDestructibleMatrix = lambda space, chunk, item: (
            Matrix(self.vector, (40., 0., -7.5)))
        self.manager.orderDestructibleDestroy = self.traced(
            'orderDestructibleDestroy', self.manager.orderDestructibleDestroy)
        for name in ('wg_getChunkDestrFilenames',
                     'wg_getDestructibleEffectCategory',
                     'wg_getChunkMatrix', 'wg_getDestructibleMatrix'):
            setattr(engine, name, self.traced(name, getattr(engine, name)))
        sys.modules.update(BigWorld=engine, Math=scene.math,
                           AreaDestructibles=area, DestructiblesCache=cache)
        from gui.mods.offline_lan_0922 import destructibles_sensor as sensor
        from gui.mods.offline_lan_0922 import destructibles_authority as authority
        from gui.mods.offline_lan_0922 import destructibles_compat as compat
        authority.BigWorld, authority.Math = engine, scene.math
        authority.reset(self.space)
        compat.reset_safe_descriptor_cache()
        sensor.set_diagnostics(False)
        sensor.reset(self.space)
        sensor.set_catalog({'format': 'offline-lan-0922-destructible-catalog',
            'version': 1, 'game_version': '0.9.22', 'map': 'host_analytic',
            'locator_quantization': 1000, 'resources': {FILENAME: {
                'kind': 'fragile', 'boxes': [[-10., .01, -.5,
                                              10., 2., .5, None]]}}})
        record = sensor._destructible_catalog['resources'][FILENAME]
        boxes = sensor._world_catalog_boxes(record,
            Matrix(self.vector, (40., 0., -7.5)), self.vector(), scene.math)
        instance = {'filename': FILENAME, 'descriptor_filename': FILENAME,
                    'kind': 'fragile', 'boxes': boxes, 'item_scale': 1.}
        sensor.g_offh_destr_instances = {(CHUNK, ITEM): instance}
        sensor.g_offh_destr_contact_bins = {}
        sensor._index_catalog_instance_1513(sensor.g_offh_destr_contact_bins,
                                           (CHUNK, ITEM), instance)
        sensor.set_event_sink(self.publish)
        self.sensor, self.authority = sensor, authority
        owner._destructibles = sensor
        self.events = []

    def traced(self, name, function):
        def query(*args):
            token = self.scene.frontier_start(name, args)
            try:
                result = function(*args)
            except Exception as error:
                self.scene.frontier_end(token, {'exception': type(error).__name__})
                raise
            self.scene.frontier_end(token, result)
            return result
        return query

    @staticmethod
    def scaled_health(scale, health):
        if float(scale) != 1.:
            raise ValueError('fixture only provides the exact unit-scale case')
        return health

    def publish(self, event):
        self.events.append(dict(event))
        self.scene.effect('destructible_event', event=dict(event))
        return True

    def check(self):
        """Independent low/high-speed and physical backing-wall check.

        This deliberately commits fixture state. Use a fresh scene before the
        structural benchmark rather than calling check in a timed workload.
        """
        sensor, vector = self.sensor, self.vector
        descriptor = self.scene.descriptor
        pose = vector(40., 0., -9.)
        contact = sensor._catalog_hull_contact(pose, 0., 4., descriptor, dt=.2)
        low = sensor._catalog_motion_blocked(self.space, pose, 0., .01,
            descriptor, self.scene.now, return_detail=True, dt=.2)
        proposal = sensor._catalog_motion_blocked(self.space, pose, 0., 8.,
            descriptor, self.scene.now, return_detail=True, dt=.2,
            kinetic_speed=8., kinetic_commit=True, proposal_only=True,
            commit_enabled=True)
        assert proposal['requires_commit'] and not self.manager.orders, proposal
        assert not self.authority.is_destroyed(CHUNK, ITEM)
        high = sensor._catalog_motion_blocked(self.space, pose, 0., 8.,
            descriptor, self.scene.now, return_detail=True, dt=.2)
        start, end = vector(40., 1., -12.), vector(40., 1., 10.)
        keep = sensor.prepare_horizontal_collision_filter(start, end)
        hit = sensor.collide_motion_segment(self.space, start, end, keep,
                                            self.scene.engine.wg_collideSegment)
        planning = sensor.prepare_navigation_collision_filter(start, end)
        plan_hit = self.scene.engine.wg_collideSegment(self.space, start, end,
                                                       0, planning)
        ground = sensor.ground_collision_filter(40., -7.5)
        ground_hit = self.scene.engine.wg_collideSegment(self.space,
            vector(40., 4., -7.5), vector(40., -1., -7.5), 0, ground)
        assert contact is True
        assert low['status'] == 'hard', low
        assert high['status'] == 'crushed' and high['accepted_now'], high
        assert self.authority.is_destroyed(CHUNK, ITEM)
        assert len(self.manager.orders) == len(self.events) == 1
        assert hit is not None and abs(hit[0].z - 4.) < 1.e-8, hit
        assert plan_hit is not None and abs(plan_hit[0].z - 4.) < 1.e-8, plan_hit
        assert ground_hit is not None and abs(ground_hit[0].y) < 1.e-8, ground_hit
        # Destroyed-ledger re-entry must not publish or send another WGDE order.
        sensor._catalog_motion_blocked(self.space, pose, 0., 8., descriptor,
            self.scene.now, return_detail=True, dt=.2)
        assert len(self.manager.orders) == len(self.events) == 1
        return dict(contact=contact, low=low, proposal=proposal, high=high,
            ordered_native_destroy_count=len(self.manager.orders),
            publication_count=len(self.events),
            physical_backing_wall=tuple(hit[0]), planning_backing_wall=tuple(plan_hit[0]),
            ground_after_acceptance=tuple(ground_hit[0]),
            original_skin_alive=self.fragile['alive'],
            limitations='Analytic WGDE acceptance and unit-scale resource seam; no Windows native destruction claim.')

    def close(self):
        self.sensor.set_event_sink(None)
        self.sensor.set_catalog(None)
        self.sensor.reset()
        for name, previous in self._saved_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def attach(scene, owner):
    return Fixture(scene, owner)


if __name__ == '__main__':
    import imp
    import json
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    host = imp.load_source('destructible_check_host',
                           os.path.join(here, 'host_scene.py'))
    scene = host.build_scene(native_library='')
    print(json.dumps(scene.destructibles.check(), indent=2, sort_keys=True))
