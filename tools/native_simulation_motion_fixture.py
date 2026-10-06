"""Portable complete-physical-stage fixture; synthetic descriptors and analytic engine.

Real BotRuntime, BattleRuntime, collision and destructible laws are retained.
This fixture does not establish BigWorld/Windows behaviour or frame performance.
"""
from __future__ import print_function
import collections
import imp
import json
import math
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CLIENT = os.path.join(REPO, 'src/res/scripts/client')
PACKAGE = os.path.join(CLIENT, 'gui/mods/offline_lan_0922')
for name, path in (('gui', os.path.dirname(os.path.dirname(PACKAGE))),
                   ('gui.mods', os.path.dirname(PACKAGE)),
                   ('gui.mods.offline_lan_0922', PACKAGE),
                   ('gui.mods.offline_lan_0922.ai', os.path.join(PACKAGE, 'ai'))):
    if name not in sys.modules:
        module = types.ModuleType(name)
        module.__path__ = [path]
        sys.modules[name] = module
class Namespace(object):
    def __init__(self, **values):
        self.__dict__.update(values)

def _graph(map_name='01_karelia', waypoint_count=2):
    waypoints = tuple((float(index * 4), 0.0, False)
                      for index in range(waypoint_count))
    reverse = tuple(reversed(waypoints))
    return {
        'format': 'offline-lan-0922-navgraph', 'version': 2,
        'game_version': '0.9.22.0.1-cn-1513', 'map': map_name,
        'cell_size': 4.0, 'origin': (0.0, 0.0), 'bounds': (0, 0, 8, 0),
        'width': 3, 'height': 1, 'heights_mm': (0, 0, 0),
        'links': (1 << 4, (1 << 3) | (1 << 4), 1 << 3),
        'hazards': (0, 0, 0),
        'spawn_anchors': ((0.0, 0.0), (8.0, 0.0)),
        'objective_bases': ((8.0, 0.0), (0.0, 0.0)),
        'spawn_formations': {
            '1': tuple((float(slot % 5) * 12.0, 0.0,
                        -100.0 + float(slot // 5) * 12.0, 0.0)
                       for slot in range(15)),
            '2': tuple((float(slot % 5) * 12.0, 0.0,
                        100.0 - float(slot // 5) * 12.0, 3.14159)
                       for slot in range(15)),
        },
        'routes': {
            '1': ({'id': 'safe-1', 'waypoints': waypoints},),
            '2': ({'id': 'safe-2', 'waypoints': reverse},),
        },
        'bake': {'max_grade': 0.30},
    }

def _flat_open_graph():
    """A flat, fully linked 31x31 graph for pure contact-physics fixtures."""
    graph = _graph()
    width = 31
    directions = ((-1, -1), (0, -1), (1, -1), (-1, 0),
                  (1, 0), (-1, 1), (0, 1), (1, 1))
    links = []
    for z in range(width):
        for x in range(width):
            links.append(sum(
                1 << index for index, (dx, dz) in enumerate(directions)
                if 0 <= x + dx < width and 0 <= z + dz < width))
    graph.update({
        'origin': (-60.0, -60.0), 'bounds': (-62.0, -62.0, 62.0, 62.0),
        'width': width, 'height': width,
        'heights_mm': [0] * (width * width),
        'links': links, 'hazards': [0] * (width * width),
    })
    return graph

def _plain_attribute_factors(unused_descriptor, crew=None, crew_level=None):
    """The #1513 default-crew factor set every Bot fixture needs."""
    level = (100.0 if crew_level is None else
             max(50.0, min(100.0, float(crew_level))))
    factor = 0.57 + 0.0043 * level
    return {
        'turret/rotationSpeed': factor,
        'gun/rotationSpeed': factor,
        'gun/reloadTime': 1.0 / factor,
        'gun/aimingTime': 1.0 / factor,
        'shotDispersion': (1.0 / factor,),
        'repairSpeed': 0.57,
        'vehicle/rotationSpeed': 1.0,
        'engine/power': 1.0,
        'chassis/terrainResistance': (1.0, 1.0, 1.0),
        'radio/distance': 1.0,
        'circularVisionRadius': 1.0,
        'camouflage': 0.57,
    }

class _Strict1513Component(object):
    """Attribute-only stand-in for #1513's ``NoLegacyStuff`` mixin."""

    def __init__(self, **values):
        self.__dict__.update(values)

    def _forbidden(self, *unused_args, **unused_kwargs):
        raise AssertionError('Operation is not allowed')

    get = _forbidden
    __contains__ = _forbidden
    __getitem__ = _forbidden
    __iter__ = _forbidden
    items = _forbidden
    keys = _forbidden
    values = _forbidden

class _HitTester1513(object):
    def __init__(self, minimum, maximum):
        # Exact #1513 bbox exposes min, max and a third derived value.
        self.bbox = (minimum, maximum, None)

def _combat_descriptor(reload_time=0.5, clip=(2, 0.2),
                       turret_yaw_limits=(-math.pi, math.pi),
                       turret_speed=10.0, gun_speed=10.0,
                       dispersion=0.03, max_ammo=None):
    gun = types.SimpleNamespace(
        shots=({'shell': {'effectsIndex': 0}, 'speed': 1000.0,
                'gravity': 10.0, 'maxDistance': 5000.0},),
        reloadTime=reload_time,
        clip=clip, turretYawLimits=turret_yaw_limits,
        pitchLimits={'absolute': (-0.35, 0.15)}, rotationSpeed=gun_speed,
        shotDispersionAngle=dispersion,
        maxHealth=54, maxRegenHealth=27)
    if max_ammo is not None:
        gun.maxAmmo = int(max_ammo)
    chassis = _Strict1513Component(
        hitTester=_HitTester1513(
            (-1.5, -0.8, -3.5), (1.5, 0.8, 3.5)),
        hullPosition=(0.0, 0.6, 0.0), rotationSpeed=0.75,
        topRightCarryingPoint=(1.5, 3.5),
        shotDispersionFactors=(0.14, 0.14),
        maxHealth=170, maxRegenHealth=130)
    hull = _Strict1513Component(
        hitTester=_HitTester1513(
            (-1.7, -0.2, -3.5), (1.7, 1.4, 3.5)),
        turretPositions=((0.0, 1.0, 0.0),))
    return types.SimpleNamespace(
        gun=gun, turret={'rotationSpeed': turret_speed,
                         'circularVisionRadius': 445.0},
        physics={'speedLimits': (14.0, 7.0)}, chassis=chassis,
        hull=hull, maxHealth=1000, radio=types.SimpleNamespace(distance=700.0))

if not hasattr(types, 'SimpleNamespace'):
    types.SimpleNamespace = Namespace
from gui.mods.offline_lan_0922 import bot_runtime, native_math
from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime
bot_runtime.loadout.attribute_factors = _plain_attribute_factors
fixture = sys.modules[__name__]

class Vec(object):
    def __init__(self, x=0., y=0., z=0.):
        if hasattr(x, 'x'):
            x, y, z = x.x, x.y, x.z
        elif isinstance(x, (tuple, list)):
            x, y, z = x
        self.x, self.y, self.z = float(x), float(y), float(z)
    def __iter__(self):
        return iter((self.x, self.y, self.z))
    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]
    def __add__(self, other):
        return Vec(self.x+other.x, self.y+other.y, self.z+other.z)
    def __sub__(self, other):
        return Vec(self.x-other.x, self.y-other.y, self.z-other.z)
    def __mul__(self, value):
        return self.scale(value)
    @property
    def length(self):
        return math.sqrt(self.x*self.x+self.y*self.y+self.z*self.z)
    @property
    def lengthSquared(self):
        return self.x*self.x+self.y*self.y+self.z*self.z
    def scale(self, value):
        return Vec(self.x*value, self.y*value, self.z*value)
    def normalise(self):
        value = self.length
        if value:
            self.x /= value
            self.y /= value
            self.z /= value


def plain(value):
    if isinstance(value, Vec):
        return [value.x, value.y, value.z]
    if isinstance(value, dict):
        return dict((str(key), plain(item)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted([plain(item) for item in value], key=repr)
    if callable(value):
        return {'callable': getattr(value, '__name__', type(value).__name__)}
    if hasattr(value, '__dict__'):
        return {'type': type(value).__name__, 'fields': plain(vars(value))}
    return value


def position(value):
    raw = value.get('position')
    if raw is not None:
        return tuple(raw)
    return (float(value.get('x', 0.)), float(value.get('y', 0.)),
            float(value.get('z', 0.)))


def boundary_actor(value):
    """Record the actual native fixture inputs, excluding Python cache IDs."""
    return dict(id=value.get('network_id',value.get('id')),
        kind=value.get('kind'),team=value.get('team'),alive=value.get('alive'),
        position=position(value),yaw=value.get('yaw',0.),
        pitch=value.get('pitch',0.),roll=value.get('roll',0.),
        gun_pitch=value.get('gun_pitch',0.),turret_yaw=value.get('turret_yaw',0.))


def segment_box(start, end, minimum, maximum):
    """Slab intersection including the first stable entry face."""
    lower, upper, normal = 0., 1., (0., 1., 0.)
    for axis in range(3):
        delta = end[axis] - start[axis]
        if abs(delta) < 1.e-12:
            if start[axis] < minimum[axis] or start[axis] > maximum[axis]:
                return None
            continue
        a = (minimum[axis] - start[axis]) / delta
        b = (maximum[axis] - start[axis]) / delta
        sign = -1.
        if a > b:
            a, b, sign = b, a, 1.
        if a > lower:
            lower = a
            face = [0., 0., 0.]
            face[axis] = sign
            normal = tuple(face)
        upper = min(upper, b)
        if lower > upper:
            return None
    if upper < 0. or lower > 1.:
        return None
    return max(0., lower), normal


class AnalyticEngine(object):
    def __init__(self, scene):
        self.scene = scene
        self.boxes = [
            dict(id='central_wall', minimum=(-15., 0.01, -5.),
                 maximum=(15., 5., 5.), kind='hard', alive=True),
            dict(id='east_crate', minimum=(48., 0.01, 12.),
                 maximum=(58., 2.5, 22.), kind='hard', alive=True),
        ]
    def time(self):
        return self.scene.now
    def intersections(self, start, end, include_floor=True):
        values = []
        if include_floor and abs(end[1]-start[1]) > 1.e-12:
            t = -start[1] / (end[1]-start[1])
            if 0. <= t <= 1.:
                values.append((t, (0., 1., 0.), dict(id='floor', kind='floor')))
        for box in self.boxes:
            if not box.get('alive', True):
                continue
            hit = segment_box(start, end, box['minimum'], box['maximum'])
            if hit is not None:
                values.append((hit[0], hit[1], box))
        return sorted(values, key=lambda row: (row[0], row[2]['id']))
    def wg_collideSegment(self, space, start, end, mask, *filters):
        start, end = Vec(start), Vec(end)
        token = self.scene.frontier_start('wg_collideSegment',
            (space, start, end, mask, tuple(bool(v) for v in filters)))
        result = None
        for fraction, normal, box in self.intersections(tuple(start), tuple(end)):
            identity = (box.get('material', 0), box.get('collision_flags', 0),
                        box.get('item_index', -1), box.get('chunk_id', -1))
            if any(callback is not None and not callback(*identity)
                   for callback in filters):
                continue
            result = (start+(end-start).scale(fraction), Vec(normal),
                      box.get('material', 0))
            self.scene.counts['native_hit_'+box['kind']] += 1
            break
        self.scene.counts['native_segment'] += 1
        self.scene.frontier_end(token, result)
        return result
    def wg_getMatInfoNearPoint(self, *args):
        token = self.scene.frontier_start('wg_getMatInfoNearPoint', args)
        result = (False, Vec(), Vec(), 0, '', 0, 0)
        self.scene.frontier_end(token, result)
        return result


def descriptor():
    value = fixture._combat_descriptor(reload_time=.9, clip=(6, .18),
        turret_speed=2.4, gun_speed=2.4, dispersion=.008, max_ammo=120)
    value.type = fixture.Namespace(tags=frozenset(('heavyTank',)),
                                   name='host:synthetic_heavy')
    value.gun.burst = (3, .06)
    value.gun.aimingTime = .35
    value.gun.shotDispersionFactors = {'afterShot': 1.5,
                                     'afterShotInBurst': 1.15}
    value.gun.shots = ({'shell': {'effectsIndex': 0, 'kind': 'ARMOR_PIERCING',
        'damage': (70., 0.), 'caliber': 45., 'piercingPower': (90., 80.)},
        'speed': 700., 'gravity': 10., 'maxDistance': 1200.},)
    return value


def graph():
    value = fixture._flat_open_graph()
    width = 71
    directions = ((-1,-1),(0,-1),(1,-1),(-1,0),(1,0),(-1,1),(0,1),(1,1))
    def open_cell(x, z):
        wx, wz = -140.+x*4., -140.+z*4.
        return not (-18. <= wx <= 18. and -8. <= wz <= 8.)
    links = []
    for z in range(width):
        for x in range(width):
            links.append(sum(1 << index for index,(dx,dz) in enumerate(directions)
                if open_cell(x,z) and 0<=x+dx<width and 0<=z+dz<width
                and open_cell(x+dx,z+dz)))
    value.update(origin=(-140.,-140.), bounds=(-142.,-142.,142.,142.),
        width=width, height=width, heights_mm=[0]*(width*width),
        links=links, hazards=[0]*(width*width),
        spawn_anchors=((-60.,-46.),(60.,46.)),
        objective_bases=((0.,105.),(0.,-105.)),
        routes={'1': ({'id':'host-west-north','waypoints':
                      ((-70.,-70.,False),(-70.,0.,False),(0.,105.,False))},),
                '2': ({'id':'host-east-south','waypoints':
                      ((70.,70.,False),(70.,0.,False),(0.,-105.,False))},)})
    return value



class HostScene(object):
    def __init__(self, mode, runtime_class=None, native_library=None):
        self.mode = str(mode)
        self.now = 0.
        self.frame_index = -1
        self.frontier_log, self.effect_log = [], []
        self.commands, self.publications, self.frames = [], [], []
        self.counts = collections.Counter()
        self.runtime = None
        self._frontier_seq = 0
        self.engine = AnalyticEngine(self)
        self.math = fixture.Namespace(Vector3=Vec)
        self.descriptor = descriptor()
        self._poses_before = {}
        self._previous_poses = {}
        self._launched = set()
        self._terminal = set()
        self.errors = []
        self.native_library = native_library or ''
        if self.native_library:
            backend = imp.load_dynamic('offline_math_batch_native', self.native_library)
            native_math._backend, native_math._attempted = backend, True
            self.backend_methods = sorted(name for name in dir(backend)
                                          if not name.startswith('_'))
        else:
            native_math._backend, native_math._attempted = None, True
            self.backend_methods = []
        self.owner = BattleRuntime.__new__(BattleRuntime)
        self.owner._runtime = fixture.Namespace(bigworld=self.engine, math=self.math)
        self.owner._avatar = fixture.Namespace(spaceID=1)
        self.owner._destructibles = None
        self.owner._combat_diagnostics = None
        self.owner._bot_motion_kinds = {}
        self.owner._vector = lambda point: Vec(point)
        self.owner._water_depth = lambda point: -1.
        self.owner._detached_turret_obstacles = None
        klass = runtime_class or bot_runtime.BotRuntime
        self.runtime = klass(1,
            descriptor_resolver=lambda unused: self.descriptor,
            player_descriptor_resolver=lambda unused: self.descriptor,
            direction_probe=self.owner._direction_probe,
            ground_probe=self.owner._navigation_ground,
            physics_ground_probe=self.owner._support_column,
            motion_resolver=self.owner._resolve_bot_motion,
            motion_report=self.owner._report_bot_destructible_contact,
            wreck_rotation_probe=self.owner._resolve_bot_rotation,
            water_depth_probe=self.owner._water_depth,
            turret_motion_probe=self.owner._turret_motion_is_clear,
            spawn_resolver=self.spawn,
            baked_graph=graph(), control_seconds=bot_runtime.WORKER_CONTROL_SECONDS)
        self.owner._bots = self.runtime
        self.runtime.battle_start(dict(round_id=5, map='01_karelia',
            bot_authority_id=1, bot_skill_mode='brutal', bots=[
                dict(id=11+i, team=1 if i<14 else 2,
                     slot=i if i<14 else i-14, name='HostBot-%d'%i)
                for i in range(29)]))
        self.runtime.debug_logging = False
        for state in self.runtime.states.values():
            point, yaw = self.spawn(state['team'], state['slot'])
            state.update(x=point[0],y=point[1],z=point[2],yaw=yaw,
                         speed=3.,grounded_once=True)
        # A physical opposing pair begins in contact, with first-impact motion.
        self.runtime.states[11].update(x=-104.,z=-3.,yaw=0.,speed=4.)
        self.runtime.states[25].update(x=-104.,z=3.,yaw=math.pi,speed=4.)
        # A second Bot approaches the fixture's fragile fence under its actual
        # driver command. Keep enough initial momentum to exercise contact.
        self.runtime.states[16].update(x=40.,z=-13.,yaw=0.,speed=8.)
        human_params = {'ammo': [[1, 20]], 'camouflage': {'base_moving': 0.171, 'base_still': 0.228, 'camouflage_id': None, 'shot_factor': 0.1}, 'crew': {'dynamic_spotting': {'crew': ['commander'], 'states': {'0:0': {'base_moving': 0.171, 'base_still': 0.228, 'camouflage': 1.0, 'invisibility_moving': [0.0, 1.0], 'invisibility_still': [0.0, 1.0], 'signal': 1.0, 'vision': 1.0}, '0:1': {'base_moving': 0.171, 'base_still': 0.228, 'camouflage': 1.0, 'invisibility_moving': [0.0, 1.0], 'invisibility_still': [0.0, 1.0], 'signal': 1.0, 'vision': 1.0}, '1:0': {'base_moving': 0.171, 'base_still': 0.228, 'camouflage': 1.0, 'invisibility_moving': [0.0, 1.0], 'invisibility_still': [0.0, 1.0], 'signal': 1.0, 'vision': 1.0}, '1:1': {'base_moving': 0.171, 'base_still': 0.228, 'camouflage': 1.0, 'invisibility_moving': [0.0, 1.0], 'invisibility_still': [0.0, 1.0], 'signal': 1.0, 'vision': 1.0}}}, 'members': [{'instance': 'commander', 'roles': ['commander'], 'skills': []}]}, 'equipment': [], 'gun': {'clip_size': 1, 'shots': [{'compact_descr': 1, 'source_shot': {'deadeye': False, 'gravity': 9.81, 'maxDistance': 500.0, 'piercingPower': [100.0, 80.0], 'shell': {'caliber': 37.0, 'damage': [40.0, 20.0], 'explosionRadius': 0.0, 'kind': 'ARMOR_PIERCING'}, 'speed': 800.0}}]}, 'loadout': {'aim_time_factor': 1.0, 'bloom_move_factor': 1.0, 'bloom_rotation_factor': 1.0, 'bloom_turret_factor': 1.0, 'commander_level': 100.0, 'crew_factor': 1.0, 'crew_level': 100.0, 'crew_multiplier': 1.0, 'dispersion_factor': 1.0, 'effective_crew_level': 100.0, 'from_client_factors': True, 'gun_rotation_factor': 1.0, 'has_aim_drives': False, 'has_big_kit': False, 'has_brotherhood': False, 'has_rammer': False, 'has_rations': False, 'has_sixth_sense': False, 'has_smooth_ride': False, 'has_snap_shot': False, 'has_stabiliser': False, 'has_ventilation': False, 'radio_factor': 1.0, 'reload_factor': 1.0, 'repair_factor': 1.0, 'terrain_resistance_factors': [1.0, 1.0, 1.0], 'vehicle_rotation_factor': 1.0}, 'physics': {'brakeDecel': 4.0, 'mass': 25000.0, 'minPlaneNormalY': 0.2, 'nativePowerRatio': 1.0, 'powerW': 500000.0, 'rotSpd': 0.75, 'rotationIsAroundCenter': True, 'specificFriction': 1.0, 'speedBwd': 7.0, 'speedFwd': 14.0, 'terrainResist': [1.0, 1.0, 1.0], 'trackCenter': 2.0}, 'ramming': {'ramming_bonus': 0.0, 'spall_coefficient': 1.0}, 'skills': {'controlled_impact': False, 'deadeye': False, 'designated_target': False, 'expert': False, 'intuition_chances': 0, 'last_effort': False, 'sixth_sense': False}, 'spotting': {'binocular_delay': 3.0, 'binocular_factor': 1.0, 'camouflage_factor': 0.57, 'camouflage_level': 0.0, 'camouflage_net_bonus': 0.0, 'camouflage_net_delay': 3.0, 'commander_level': 100.0, 'from_client_factors': True, 'has_binoculars': False, 'has_camouflage_net': False, 'invisibility_moving': [0.0, 1.0], 'invisibility_still': [0.0, 1.0], 'recon_level': 0.0, 'situational_level': 0.0, 'vision_factor': 1.0}, 'version': 1}
        self.human = dict(id=1, team=1, alive=True, health=1000.,max_health=1000.,
            x=-28.,y=0.,z=-25.,yaw=math.pi/2.,speed=2.,pitch=0.,roll=0.,
            turret_yaw=0.,gun_pitch=0.,fire_seq=0,vehicle='host:synthetic_heavy',
            effective_params=human_params)
        self.destructibles = None
        helper = os.path.join(HERE, 'native_simulation_motion_destructibles.py')
        if os.path.isfile(helper):
            self.destructibles_module = imp.load_source('full_tick_destructibles',helper)
            attach = getattr(self.destructibles_module, 'attach', None)
            if callable(attach):
                self.destructibles = attach(self,self.owner)
        self._activate()

    @staticmethod
    def spawn(team, slot):
        return ((-60.+(int(slot)%7)*20., 0.,
                 (-46.-(int(slot)//7)*16.) if int(team)==1 else
                 (46.+(int(slot)//7)*16.)), 0. if int(team)==1 else math.pi)

    def _activate(self):
        sys.modules['BigWorld'], sys.modules['Math'] = self.engine, self.math

    def tag(self):
        return dict(frame=self.frame_index,
            stage=getattr(self.runtime,'_structure_stage',None),
            actor_id=getattr(self.runtime,'_structure_actor_id',None),
            sample_time_us=getattr(self.runtime,'_sample_time_us',0))

    def frontier_start(self, name, args):
        self._frontier_seq += 1
        row = self.tag()
        row.update(edge='start',seq=self._frontier_seq,name=name,args=plain(args))
        self.frontier_log.append(row)
        return self._frontier_seq

    def frontier_end(self, token, result):
        row = self.tag()
        row.update(edge='end',seq=token,result=plain(result))
        self.frontier_log.append(row)

    def log_frontier(self, name, args, result):
        token=self.frontier_start(name,args)
        self.frontier_end(token,result)

    def effect(self, kind, **data):
        row=self.tag()
        row.update(kind=kind,data=plain(data))
        self.effect_log.append(row)
        self.counts[kind] += 1
