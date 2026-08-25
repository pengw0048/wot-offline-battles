import copy
import importlib.util
import json
from collections import deque
from pathlib import Path
import socket
import threading
import time
import types
import unittest


MODULE_PATH = (
    Path(__file__).resolve().parents[1] /
    'src/res/scripts/client/gui/mods/offline_lan_0922/'
    'native_oracle_bridge.py')
SPEC = importlib.util.spec_from_file_location(
    'offline_lan_0922_native_oracle_bridge_test', MODULE_PATH)
BRIDGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BRIDGE)
_DEFAULT_INTERNAL_HITS = object()


class _Vector(object):

    def __init__(self, x, y, z):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __iter__(self):
        return iter((self.x, self.y, self.z))

    def __len__(self):
        return 3

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]


class _Extra(object):

    def __init__(self, name):
        self.name = name


class _Material(object):

    def __init__(self, armor, vehicle_damage_factor, kind=None,
                 collide_once_only=False, use_hit_angle=True,
                 check_caliber_for_hit_angle_norm=True,
                 may_ricochet=True,
                 check_caliber_for_ricochet=True, extra_name=None,
                 chance_to_hit_by_projectile=0.45,
                 chance_to_hit_by_explosion=0.15):
        self.armor = armor
        self.vehicleDamageFactor = vehicle_damage_factor
        self.kind = kind
        self.collideOnceOnly = collide_once_only
        self.useHitAngle = use_hit_angle
        self.checkCaliberForHitAngleNorm = \
            check_caliber_for_hit_angle_norm
        self.mayRicochet = may_ricochet
        # Exact #1513 preserves this historical native spelling.
        self.checkCaliberForRichet = check_caliber_for_ricochet
        self.extra = _Extra(extra_name) if extra_name is not None else None
        self.chanceToHitByProjectile = chance_to_hit_by_projectile
        self.chanceToHitByExplosion = chance_to_hit_by_explosion


class _Collision(object):

    def __init__(self, distance, part, angle, material, normal=None):
        self.dist = float(distance)
        self.compName = part
        self.hitAngleCos = angle
        self.matInfo = material
        if normal is not None:
            self.normal = normal


class _Matrix(object):

    def __init__(self):
        self.position = _Vector(11.0, 12.0, 13.0)
        self.translation = self.position
        self.rotation = None
        self.basis = [
            1.0, 0.0, 0.0,
            0.0, 0.0, -1.0,
            0.0, 1.0, 0.0,
        ]

    def setRotateYPR(self, rotation):
        self.rotation = tuple(float(value) for value in rotation)


class _HitTester(object):
    bbox = (
        _Vector(-1.5, -0.5, -2.5),
        _Vector(1.5, 1.5, 2.5),
    )


class _Chassis(object):
    hitTester = _HitTester()


class _Model(object):

    def node(self, name):
        if name == 'HP_gunFire':
            return _Matrix()
        return None


class _VehicleType(object):
    crewRoles = (
        ('commander',),
        ('driver',),
        ('gunner',),
        ('loader',),
        ('radioman',),
    )

    def __init__(self, tags=()):
        self.tags = frozenset(tags)


class _Descriptor(object):

    def __init__(self, tags=()):
        self.type = _VehicleType(tags)
        self.chassis = _Chassis()


class _Entity(object):

    def __init__(self, bigworld, distance=5.0, tags=()):
        self.bigworld = bigworld
        self.distance = distance
        self.model = _Model()
        self.typeDescriptor = _Descriptor(tags)
        self.collisions = None
        self.hull_material = _Material(
            60.0, 1.0, kind=None, collide_once_only=True,
            may_ricochet=False, check_caliber_for_ricochet=False,
            extra_name='engineHealth')
        self.turret_material = _Material(
            25.0, 0.0, kind=7, use_hit_angle=False)

    def collideSegmentExt(self, unused_start, unused_end):
        self.bigworld.call_threads.append(threading.current_thread().ident)
        if self.bigworld.raise_vehicle:
            raise RuntimeError('vehicle tester exploded')
        if self.collisions is not None:
            return self.collisions
        if self.distance is None:
            return []
        # Deliberately return non-subscriptable objects. The #1513 native hit
        # records expose attributes, not a guaranteed tuple representation.
        # The farther layer comes first to prove distance sorting is stable.
        return [
            _Collision(
                self.distance + 1.0, 'turret', 0.5,
                self.turret_material),
            _Collision(
                self.distance, 'hull', 0.75,
                self.hull_material),
        ]


class _Player(object):
    spaceID = 7


class _BigWorld(object):

    def __init__(self):
        self.call_threads = []
        self.raise_world = False
        self.raise_vehicle = False
        self.entities = {}
        self.entities[41] = _Entity(self)
        self.entities[42] = _Entity(self)
        self.segment_callback = None
        self.callbacks = {}
        self.cancelled_callbacks = []
        self.callback_threads = []
        self.cancel_threads = []
        self.internal_hits = [(5.5, 'commanderHealth')]
        self.internal_calls = []
        self._callback_sequence = 0

    def player(self):
        return _Player()

    def entity(self, entity_id):
        return self.entities.get(entity_id)

    def internal_ray_hits(self, entity, descriptor, start, end, covered):
        self.internal_calls.append((
            entity, descriptor, tuple(start), tuple(end), tuple(covered)))
        return self.internal_hits

    def wg_collideSegment(self, space_id, start, end, mask):
        self.call_threads.append(threading.current_thread().ident)
        if self.raise_world:
            raise RuntimeError('world collider exploded')
        if space_id != 7:
            raise AssertionError('wrong native space')
        if callable(self.segment_callback):
            return self.segment_callback(start, end, mask)
        if start.x == 99.0 or start.z == 99.0:
            return None
        if start.x == end.x and start.z == end.z:
            return (
                _Vector(start.x, 2.5, start.z),
                _Vector(0.0, 1.0, 0.0), 7)
        return (
            _Vector(
                (start.x + end.x) * 0.5,
                (start.y + end.y) * 0.5,
                (start.z + end.z) * 0.5),
            _Vector(-1.0, 0.0, 0.0), int(mask))

    def wg_collideWater(self, start, unused_end, unused_include_objects):
        self.call_threads.append(threading.current_thread().ident)
        if self.raise_world:
            raise RuntimeError('water collider exploded')
        if start.x == 99.0:
            return None
        return start.y - 4.0

    def callback(self, delay, function):
        self.callback_threads.append(threading.current_thread().ident)
        self._callback_sequence += 1
        self.callbacks[self._callback_sequence] = (delay, function)
        return self._callback_sequence

    def cancelCallback(self, callback_id):
        self.cancel_threads.append(threading.current_thread().ident)
        self.cancelled_callbacks.append(callback_id)
        self.callbacks.pop(callback_id, None)


class _FakeSocket(object):

    def __init__(self, incoming=()):
        self.incoming = deque(incoming)
        self.sent = []
        self.send_threads = []
        self.address = None
        self.timeout = None
        self.closed = False
        self._lock = threading.Lock()

    def setsockopt(self, unused_level, unused_option, unused_value):
        pass

    def settimeout(self, value):
        self.timeout = value

    def connect(self, address):
        self.address = address

    def send(self, payload):
        with self._lock:
            self.sent.append(bytes(payload))
            self.send_threads.append(threading.current_thread().ident)
        return len(payload)

    def recv(self, unused_size):
        with self._lock:
            if self.incoming:
                return self.incoming.popleft()
            if self.closed:
                return b''
        raise socket.timeout()

    def close(self):
        with self._lock:
            self.closed = True

    def messages(self):
        with self._lock:
            payloads = list(self.sent)
        result = []
        for payload in payloads:
            for line in payload.splitlines():
                if line:
                    result.append(json.loads(line.decode('utf-8')))
        return result


def _vector(x, y, z):
    return {'x': float(x), 'y': float(y), 'z': float(z)}


def _entity(generation=1, entity_id=42):
    return {'entity_id': entity_id, 'generation': generation}


class _Foliage(object):

    def __init__(self, bonus=0.25):
        self.bonus = bonus
        self.calls = []

    def camouflage_bonus(self, observer, target, fired_recently):
        self.calls.append((tuple(observer), tuple(target), fired_recently))
        return self.bonus


def _shot_destructible_sensor_result():
    return {
        'complete': True,
        'fail_closed': False,
        'ambiguous': False,
        'reasons': (),
        'candidates': ({
            'chunk_id': 22,
            'item_index': 37,
            'mat_kind': None,
            'kind': 'fragile',
            'entry_distance': 4.0,
            'exit_distance': 6.0,
            'impact_point': (0.0, 0.0, 4.0),
            'item_scale': 0.5,
            'scaled_health': 15.0,
            'ap_through': True,
            'piercing_loss': 25.0,
            'ambiguous': False,
        },),
        'destroyed_skipped': 1,
        'uncertain_distance': None,
        'static_collision': {
            'distance': 6.01,
            'point': (0.0, 0.0, 6.01),
            'normal': (0.0, 0.0, -1.0),
        },
    }


def _hull_destructible_sensor_result():
    return {
        'complete': True,
        'fail_closed': False,
        'ambiguous': False,
        'reasons': (),
        'candidates': ({
            'chunk_id': 22,
            'item_index': 38,
            'mat_kind': 73,
            'kind': 'structure',
            'obb_center': (0.0, 0.5, 3.75),
        },),
        'frame_travel': 0.3,
    }


class _ReadOnlyDestructiblesSensor(object):

    def __init__(self, shot=None, hull=None):
        self.shot = (_shot_destructible_sensor_result()
                     if shot is None else shot)
        self.hull = (_hull_destructible_sensor_result()
                     if hull is None else hull)
        self.shot_calls = []
        self.hull_calls = []
        self.mutation_calls = []
        self.ledger = set()
        self.publish_count = 0

    def shot_destructible_evidence_1513(
            self, bigworld, space_id, start, end, shell_kind):
        self.shot_calls.append((
            bigworld, int(space_id), tuple(start), tuple(end), shell_kind))
        if isinstance(self.shot, Exception):
            raise self.shot
        return copy.deepcopy(self.shot)

    def hull_destructible_evidence_1513(
            self, space_id, position, yaw, bbox, frame_travel):
        self.hull_calls.append((
            int(space_id), tuple(position), float(yaw), tuple(bbox),
            float(frame_travel)))
        if isinstance(self.hull, Exception):
            raise self.hull
        return copy.deepcopy(self.hull)

    def _forbidden_mutation(self, *args, **unused_kwargs):
        self.mutation_calls.append(args)
        raise AssertionError('read-only oracle attempted mutation')

    destroy_tree = _forbidden_mutation
    destroy_column = _forbidden_mutation
    destroy_fragile = _forbidden_mutation
    destroy_module = _forbidden_mutation
    note_destroyed = _forbidden_mutation
    _publish_destroyed = _forbidden_mutation


def _destructible_entity(world, entity_id=42, space_id=7):
    entity = world.entities[entity_id]
    entity.spaceID = space_id
    entity.typeDescriptor.hull = types.SimpleNamespace(
        hitTester=types.SimpleNamespace(bbox=(
            _Vector(-1.6, -1.0, -3.6),
            _Vector(1.6, 1.0, 3.6), None)))
    entity.typeDescriptor.physics = types.SimpleNamespace(weight=40000.0)
    return entity


def _observation_arguments(recent_fire=None):
    arguments = {
        'observer': _entity(entity_id=41),
        'target': _entity(entity_id=42),
        'observer_position': _vector(0, 0, 0),
        'target_position': _vector(20, 0, 0),
        'collision_mask': 128,
    }
    if recent_fire is not None:
        arguments['evaluated_for_recent_fire'] = recent_fire
    return arguments


def _ram_contact_arguments(second_generation=1):
    return {
        'first': _entity(entity_id=41),
        'second': _entity(
            generation=second_generation, entity_id=42),
        'first_pose': {
            'position': _vector(1, 0, 0),
            'yaw': 0.25,
            'pitch': 0.1,
            'roll': -0.05,
            'turret_yaw': 0.35,
            'gun_pitch': -0.12,
            'siege_state': 2,
        },
        'second_pose': {
            'position': _vector(-1, 0, 0),
            'yaw': -0.2,
            'pitch': 0.0,
            'roll': 0.03,
            'turret_yaw': -0.41,
            'gun_pitch': 0.09,
            'siege_state': 0,
        },
        'contact_point': _vector(0, 0.5, 0),
        'contact_normal': _vector(1, 0, 0),
    }


def _explosion_arguments(generation=1):
    return {
        'target': _entity(generation=generation),
        'impact': _vector(0, 0, 0),
        'incoming_direction': _vector(1, 0, 0),
        'caliber_mm': 122.0,
        'target_pose': {
            'position': _vector(5, 0, 0),
            'yaw': 0.25,
            'pitch': -0.1,
            'roll': 0.05,
            'turret_yaw': 0.4,
            'gun_pitch': -0.2,
            'siege_state': 2,
        },
    }


def _destructible_shot_arguments():
    return {
        'space_id': 7,
        'start': _vector(0, 0, 0),
        'end': _vector(0, 0, 10),
        'shell_kind': 'ARMOR_PIERCING',
    }


def _destructible_hull_arguments():
    return {
        'space_id': 7,
        'position': _vector(0, 0, 0),
        'yaw': 0.0,
        'frame_travel': 0.3,
    }


def _operation(name, arguments):
    return {'operation': name, 'arguments': arguments}


def _query(query_id, key, operation, generation=1, entity=None):
    return {
        'query_id': query_id,
        'key': key,
        'query_generation': generation,
        'entity': entity or _entity(),
        'operation': operation,
    }


def _batch(sequence, queries, issued_tick=None, world_revision=None,
           oracle_generation=1):
    if issued_tick is None:
        issued_tick = sequence - 1
    if world_revision is None:
        world_revision = sequence
    return {
        'type': 'query_batch',
        'payload': {
            'protocol_version': 1,
            'round_id': 9,
            'authority_epoch': 2,
            'oracle_generation': oracle_generation,
            'batch_seq': sequence,
            'issued_tick': issued_tick,
            'apply_tick': issued_tick + 3,
            'world_revision': world_revision,
            'queries': queries,
        },
    }


def _welcome(capabilities=None, oracle_generation=None,
             server_capabilities=None):
    message = {
        'type': 'welcome',
        'protocol': BRIDGE.LAN_PROTOCOL_VERSION,
        'role': BRIDGE.WORKER_ROLE,
        'worker_id': BRIDGE.WORKER_ID,
        'client_build': BRIDGE.CLIENT_BUILD,
        'capabilities': list(
            capabilities if capabilities is not None
            else BRIDGE.CLIENT_CAPABILITIES),
        'server_capabilities': list(
            server_capabilities if server_capabilities is not None else (
                'oracle_backed_server_v1',
                BRIDGE.HE_EXPLOSION_EVIDENCE_CAPABILITY)),
    }
    if oracle_generation is not None:
        message['oracle_generation'] = oracle_generation
    return message


def _wire(*messages):
    return ''.join(
        json.dumps(message, separators=(',', ':')) + '\n'
        for message in messages).encode('utf-8')


def _wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError('timed out waiting for native oracle bridge')


def _make_bridge(generation=1, bigworld=None, **kwargs):
    world = bigworld or _BigWorld()
    if 'internal_ray_hits' not in kwargs:
        kwargs['internal_ray_hits'] = world.internal_ray_hits
    bridge = BRIDGE.NativeOracleBridge(
        bigworld=world,
        vector_factory=_Vector,
        **kwargs)
    bridge._begin_embedded_session(generation)
    return bridge, world


def _make_destructible_bridge(sensor=None, entity_space_id=7,
                              donated_generation=1):
    world = _BigWorld()
    entity = _destructible_entity(
        world, entity_id=42, space_id=entity_space_id)
    sensor = sensor or _ReadOnlyDestructiblesSensor()

    def resolve(entity_ref):
        resolved = world.entities.get(entity_ref['entity_id'])
        return resolved, donated_generation

    bridge, unused_world = _make_bridge(
        bigworld=world,
        entity_resolver=resolve,
        space_id_provider=lambda: 7,
        destructibles_sensor=sensor)
    return bridge, world, entity, sensor


def _reply(bridge):
    self_messages = bridge._drain_outbound_messages()
    if len(self_messages) != 1:
        raise AssertionError('expected one reply, got %r' % self_messages)
    if self_messages[0].get('type') != 'query_reply':
        raise AssertionError('expected query_reply, got %r' % self_messages[0])
    return self_messages[0]['payload']


class NativeOracleOperationTests(unittest.TestCase):

    @staticmethod
    def _vehicle_status(collisions, internal_hits=_DEFAULT_INTERNAL_HITS):
        bridge, world = _make_bridge()
        world.entities[42].collisions = collisions
        if internal_hits is not _DEFAULT_INTERNAL_HITS:
            world.internal_hits = internal_hits
        query = _query(1, 'vehicle:42', _operation(
            'vehicle_hit_test', {
                'start': _vector(0, 0, 0),
                'end': _vector(10, 0, 0),
                'target': _entity(),
            }))
        if not bridge._accept_server_message(_batch(1, [query])):
            raise AssertionError('vehicle query was rejected before render')
        bridge.process_render_frame()
        return _reply(bridge)['results'][0]['status']

    @staticmethod
    def _explosion_status(collisions, internal_hits, generation=1,
                          mutate_arguments=None):
        world = _BigWorld()
        world.raise_vehicle = True
        provider_calls = []
        cone_calls = []
        frozen_target = object()

        def resolve(entity_ref):
            return world.entities.get(entity_ref['entity_id']), 1

        def frozen_provider(entity, pose, start, end):
            provider_calls.append((
                entity, copy.deepcopy(pose), tuple(start), tuple(end)))
            return (frozen_target, entity.typeDescriptor, collisions)

        def cone_provider(target, descriptor, burst, direction, shell,
                          covered):
            cone_calls.append((
                target, descriptor, tuple(burst), tuple(direction),
                dict(shell), tuple(covered)))
            return internal_hits

        bridge, unused_world = _make_bridge(
            bigworld=world, entity_resolver=resolve,
            explosion_frozen_target_provider=frozen_provider,
            internal_cone_hits=cone_provider)
        arguments = _explosion_arguments(generation=generation)
        if mutate_arguments is not None:
            mutate_arguments(arguments)
        query = _query(
            1, 'explosion:42',
            _operation('explosion_evidence', arguments),
            entity=_entity(generation=generation))
        if not bridge._accept_server_message(_batch(1, [query])):
            raise AssertionError('explosion query was rejected before render')
        bridge.process_render_frame()
        status = _reply(bridge)['results'][0]['status']
        return status, provider_calls, cone_calls, world

    def test_hello_is_only_a_simulation_worker_with_native_oracle_capability(self):
        bridge = BRIDGE.NativeOracleBridge(bigworld=_BigWorld())
        bridge.oracle_generation = 4

        hello = bridge.hello_payload()

        self.assertEqual('hello', hello['type'])
        self.assertEqual('simulation_worker', hello['role'])
        self.assertEqual(4, hello['oracle_generation'])
        self.assertIn('native_oracle_v1', hello['capabilities'])
        self.assertIn('simulation_worker_v1', hello['capabilities'])
        for capability in (
                'projectile_ledger_v2', 'destructible_catalog_v5',
                'lean_snapshot_manifest_v1', 'ram_contact_ledger_v3',
                'human_ram_timeline_v1', 'he_explosion_evidence_v1',
                'player_fire_intent_v4',
                'player_environment_v2', 'effective_params_v1',
                'ricochet_continuation_v1',
                'player_ammo_authority_v1',
                'player_authority_loadout_v1'):
            self.assertIn(capability, hello['capabilities'])
        for forbidden in ('name', 'vehicle', 'team', 'health', 'max_health'):
            self.assertNotIn(forbidden, hello)
        for forbidden in ('apply_damage', 'set_health', 'tick_bot_ai'):
            self.assertFalse(hasattr(bridge, forbidden))

    def test_welcome_requires_he_evidence_in_both_capability_directions(self):
        bridge, unused_world = _make_bridge()
        without_echo = [
            value for value in BRIDGE.CLIENT_CAPABILITIES
            if value != BRIDGE.HE_EXPLOSION_EVIDENCE_CAPABILITY]
        with self.assertRaisesRegex(
                BRIDGE.OracleProtocolError,
                'invalid native oracle welcome'):
            bridge._accept_welcome(_welcome(capabilities=without_echo))

        bridge, unused_world = _make_bridge()
        with self.assertRaisesRegex(
                BRIDGE.OracleProtocolError,
                'invalid native oracle welcome'):
            bridge._accept_welcome(_welcome(
                server_capabilities=('oracle_backed_server_v1',)))

    def test_all_singular_operations_return_strict_fenced_results(self):
        bridge, world = _make_bridge()
        queries = [
            _query(1, 'ground:42', _operation(
                'ground_sample', {'position': _vector(3, 8, 5)})),
            _query(2, 'water:42', _operation(
                'water_sample', {'position': _vector(3, 8, 5)})),
            _query(3, 'world:42', _operation('segment_cast', {
                'start': _vector(0, 0, 0),
                'end': _vector(10, 0, 0),
                'collision_mask': 23,
            })),
            _query(4, 'vehicle:42', _operation('vehicle_hit_test', {
                'start': _vector(0, 0, 0),
                'end': _vector(10, 0, 0),
                'target': _entity(),
            })),
            _query(5, 'node:42', _operation(
                'node_transform', {'node': 'HP_gunFire'})),
            _query(6, 'player-muzzle:42', _operation(
                'player_muzzle_evidence', {})),
        ]
        message = _batch(
            1, queries, issued_tick=30, world_revision=81)

        self.assertTrue(bridge._accept_server_message(message))
        self.assertEqual(1, bridge.process_render_frame())
        reply = _reply(bridge)

        self.assertEqual({
            'protocol_version', 'round_id', 'authority_epoch',
            'oracle_generation', 'batch_seq', 'issued_tick', 'apply_tick',
            'world_revision', 'oracle_frame_seq', 'results',
        }, set(reply))
        self.assertEqual(33, reply['apply_tick'])
        self.assertEqual(81, reply['world_revision'])
        self.assertEqual(1, reply['oracle_frame_seq'])
        self.assertEqual(6, len(reply['results']))
        for query, result in zip(queries, reply['results']):
            self.assertEqual({
                'query_id', 'key', 'query_generation', 'entity', 'status',
            }, set(result))
            self.assertEqual(query['query_id'], result['query_id'])
            self.assertEqual(query['key'], result['key'])
            self.assertEqual(query['entity'], result['entity'])
            self.assertEqual('ok', result['status']['status'])

        outcomes = [result['status']['outcome']
                    for result in reply['results']]
        self.assertEqual('ground_sample', outcomes[0]['result'])
        self.assertEqual(2.5, outcomes[0]['value']['sample']['height'])
        self.assertEqual(7, outcomes[0]['value']['sample']['material_id'])
        self.assertEqual(4.0, outcomes[1]['value']['height'])
        self.assertEqual(0.5, outcomes[2]['value']['hit']['fraction'])
        self.assertEqual(23, outcomes[2]['value']['hit']['material_id'])
        vehicle_hit = outcomes[3]['value']['hit']
        self.assertEqual({
            'fraction', 'position', 'normal', 'hit_part', 'layers',
            'internal_hits',
        }, set(vehicle_hit))
        self.assertEqual(0.5, vehicle_hit['fraction'])
        self.assertEqual('hull', vehicle_hit['hit_part'])
        self.assertEqual(
            {'x': 5.0, 'y': 0.0, 'z': 0.0},
            vehicle_hit['position'])
        self.assertEqual(
            {'x': -1.0, 'y': 0.0, 'z': 0.0},
            vehicle_hit['normal'])
        self.assertEqual(['hull', 'turret'], [
            layer['component'] for layer in vehicle_hit['layers']])
        self.assertEqual([5.0, 6.0], [
            layer['distance_m'] for layer in vehicle_hit['layers']])
        self.assertEqual([0.75, 0.5], [
            layer['hit_angle_cos'] for layer in vehicle_hit['layers']])
        hull_material = world.entities[42].hull_material
        self.assertEqual({
            'distance_m': 5.0,
            'hit_angle_cos': 0.75,
            'component': 'hull',
            'material': {
                'armor_mm': 60.0,
                'vehicle_damage_factor': 1.0,
                'kind': None,
                'native_identity': id(hull_material),
                'collide_once_only': True,
                'use_hit_angle': True,
                'check_caliber_for_hit_angle_norm': True,
                'may_ricochet': False,
                'check_caliber_for_ricochet': False,
            },
            'critical_target': {
                'kind': 'device',
                'name': 'engineHealth',
            },
            'chance_to_hit_by_projectile': 0.45,
            'chance_to_hit_by_explosion': 0.15,
        }, vehicle_hit['layers'][0])
        self.assertEqual({
            'distance_m': 5.5,
            'target': {'kind': 'crew', 'name': 'commander'},
        }, vehicle_hit['internal_hits'][0])
        self.assertEqual({
            'critical_target', 'chance_to_hit_by_projectile',
            'chance_to_hit_by_explosion',
        }, set(vehicle_hit['layers'][1]) - set((
            'distance_m', 'hit_angle_cos', 'component', 'material')))
        self.assertIsNone(vehicle_hit['layers'][1]['critical_target'])
        self.assertIsNone(
            vehicle_hit['layers'][1]['chance_to_hit_by_projectile'])
        self.assertIsNone(
            vehicle_hit['layers'][1]['chance_to_hit_by_explosion'])
        self.assertEqual(7, vehicle_hit['layers'][1]['material']['kind'])
        self.assertIsNone(
            vehicle_hit['layers'][1]['material']['native_identity'])
        self.assertEqual(
            {'x': 11.0, 'y': 12.0, 'z': 13.0},
            outcomes[4]['value']['transform']['position'])
        self.assertEqual(_Matrix().basis,
                         outcomes[4]['value']['transform']['basis'])
        muzzle = outcomes[5]
        self.assertEqual('player_muzzle_evidence', muzzle['result'])
        self.assertEqual(
            {'transform', 'barrel_under_water'}, set(muzzle['value']))
        self.assertEqual(
            {'x': 11.0, 'y': 12.0, 'z': 13.0},
            muzzle['value']['transform']['position'])
        self.assertEqual(_Matrix().basis,
                         muzzle['value']['transform']['basis'])
        self.assertIs(True, muzzle['value']['barrel_under_water'])
        self.assertTrue(world.call_threads)
        self.assertEqual(
            {threading.current_thread().ident}, set(world.call_threads))
        self.assertEqual(1, len(world.internal_calls))
        unused_entity, unused_descriptor, ray_start, ray_end, covered = \
            world.internal_calls[0]
        self.assertEqual((0.0, 0.0, 0.0), ray_start)
        self.assertEqual((10.0, 0.0, 0.0), ray_end)
        self.assertEqual(('engineHealth',), covered)

    def test_player_muzzle_water_fact_uses_positive_distance_and_fails_closed(self):
        def explode(*unused_arguments):
            raise RuntimeError('water probe exploded')

        cases = (
            ('no_water', lambda *unused_arguments: None, False),
            ('zero_distance', lambda *unused_arguments: 0.0, False),
            ('positive_distance', lambda *unused_arguments: 0.001, True),
            ('invalid_distance', lambda *unused_arguments: float('nan'), True),
            ('native_exception', explode, True),
            ('missing_api', None, True),
        )
        for name, collide, expected in cases:
            with self.subTest(name=name):
                bridge, world = _make_bridge()
                world.wg_collideWater = collide
                query = _query(1, 'player-muzzle:42', _operation(
                    'player_muzzle_evidence', {}))
                self.assertTrue(bridge._accept_server_message(
                    _batch(1, [query])))
                bridge.process_render_frame()
                status = _reply(bridge)['results'][0]['status']
                self.assertEqual('ok', status['status'])
                self.assertIs(
                    expected,
                    status['outcome']['value']['barrel_under_water'])

    def test_player_muzzle_request_is_fixed_and_requires_native_transform(self):
        bridge, unused_world = _make_bridge()
        malformed = _query(1, 'player-muzzle:42', _operation(
            'player_muzzle_evidence', {'node': 'HP_gunFire'}))
        self.assertTrue(bridge._accept_server_message(
            _batch(1, [malformed])))
        bridge.process_render_frame()
        status = _reply(bridge)['results'][0]['status']
        self.assertEqual('unavailable', status['status'])
        self.assertEqual('invalid_arguments', status['code'])

        bridge, world = _make_bridge()
        world.entities[42].model = types.SimpleNamespace(
            node=lambda unused_name: None)
        query = _query(1, 'player-muzzle:42', _operation(
            'player_muzzle_evidence', {}))
        self.assertTrue(bridge._accept_server_message(
            _batch(1, [query])))
        bridge.process_render_frame()
        status = _reply(bridge)['results'][0]['status']
        self.assertEqual('unavailable', status['status'])
        self.assertEqual('muzzle_unavailable', status['code'])

    def test_explosion_evidence_uses_frozen_pose_and_returns_only_typed_facts(self):
        engine = _Material(
            60.0, 1.0, kind=9, extra_name='engineHealth',
            chance_to_hit_by_explosion=0.15)
        # The HE-only operation must not require or return projectile chance.
        del engine.chanceToHitByProjectile
        screen = _Material(20.0, 0.0, kind=8)
        collisions = [
            _Collision(3.0, 'screen', 0.5, screen),
            _Collision(2.0, 'hull', 0.75, engine),
        ]

        status, provider_calls, cone_calls, world = self._explosion_status(
            collisions, [(0.4, 'commanderHealth')])

        self.assertEqual('ok', status['status'])
        outcome = status['outcome']
        self.assertEqual('explosion_evidence', outcome['result'])
        self.assertEqual({
            'target_pose', 'vehicle_ray', 'internal_hits',
        }, set(outcome['value']))
        def all_keys(value):
            if isinstance(value, dict):
                result = set(value)
                for child in value.values():
                    result.update(all_keys(child))
                return result
            if isinstance(value, list):
                result = set()
                for child in value:
                    result.update(all_keys(child))
                return result
            return set()

        keys = all_keys(outcome)
        for forbidden in ('damage', 'occluded', 'verdict'):
            self.assertNotIn(forbidden, keys)
        pose = outcome['value']['target_pose']
        self.assertEqual(_explosion_arguments()['target_pose'], pose)
        layers = outcome['value']['vehicle_ray']['layers']
        self.assertEqual([2.0, 3.0], [
            layer['distance_m'] for layer in layers])
        self.assertEqual({
            'distance_m', 'hit_angle_cos', 'component', 'material',
            'critical_target', 'chance_to_hit_by_explosion',
        }, set(layers[0]))
        self.assertNotIn('chance_to_hit_by_projectile', layers[0])
        self.assertEqual({
            'kind': 'device', 'name': 'engineHealth',
        }, layers[0]['critical_target'])
        self.assertEqual([{
            'distance_m': 0.4,
            'target': {'kind': 'crew', 'name': 'commander'},
        }], outcome['value']['internal_hits'])

        self.assertEqual(1, len(provider_calls))
        unused_entity, frozen_pose, start, end = provider_calls[0]
        self.assertEqual(_explosion_arguments()['target_pose'], frozen_pose)
        self.assertEqual((0.0, 0.0, 0.0), start)
        self.assertEqual((5.0, 1.0, 0.0), end)
        self.assertEqual(1, len(cone_calls))
        unused_target, unused_descriptor, burst, direction, shell, covered = \
            cone_calls[0]
        self.assertEqual((0.0, 0.0, 0.0), burst)
        self.assertEqual((1.0, 0.0, 0.0), direction)
        self.assertEqual({'caliber': 122.0}, shell)
        self.assertEqual(('engineHealth',), covered)
        # raise_vehicle proves any accidental live collideSegmentExt fallback
        # would have made the operation unavailable.
        self.assertEqual([], world.call_threads)

    def test_explosion_evidence_preserves_none_and_empty_internal_layouts(self):
        unavailable, unused_provider, unused_cone, unused_world = \
            self._explosion_status([], None)
        clear, unused_provider, unused_cone, unused_world = \
            self._explosion_status([], [])

        unavailable_value = unavailable['outcome']['value']
        clear_value = clear['outcome']['value']
        self.assertIsNone(unavailable_value['vehicle_ray'])
        self.assertIsNone(unavailable_value['internal_hits'])
        self.assertIsNone(clear_value['vehicle_ray'])
        self.assertEqual([], clear_value['internal_hits'])

    def test_explosion_provider_is_discovered_only_through_worker_resolver(self):
        world = _BigWorld()
        calls = []

        class _Runtime(object):
            def resolve_native_oracle_entity(self, entity_ref):
                return world.entities.get(entity_ref['entity_id']), 1

            def native_explosion_evidence_at_pose(
                    self, entity, pose, start, end):
                calls.append((entity, pose, tuple(start), tuple(end)))
                return (object(), entity.typeDescriptor, ())

        class _Entities(object):
            def __init__(self, runtime):
                self._resolver = runtime.resolve_native_oracle_entity

            def resolve(self, entity_ref):
                return self._resolver(entity_ref)

        entities = _Entities(_Runtime())
        bridge, unused_world = _make_bridge(
            bigworld=world, entity_resolver=entities.resolve,
            internal_cone_hits=lambda *unused: [])
        query = _query(1, 'explosion:42', _operation(
            'explosion_evidence', _explosion_arguments()))

        self.assertTrue(bridge._accept_server_message(_batch(1, [query])))
        bridge.process_render_frame()
        status = _reply(bridge)['results'][0]['status']

        self.assertEqual('ok', status['status'])
        self.assertEqual(1, len(calls))
        self.assertEqual((0.0, 0.0, 0.0), calls[0][2])
        self.assertEqual((5.0, 1.0, 0.0), calls[0][3])

    def test_explosion_evidence_rejects_malformed_pose_and_stale_generation(self):
        malformed_cases = (
            lambda arguments: arguments['target_pose'].pop('gun_pitch'),
            lambda arguments: arguments.update(
                incoming_direction=_vector(0.5, 0, 0)),
            lambda arguments: arguments.update(caliber_mm=float('nan')),
            lambda arguments: arguments['target_pose'].update(
                siege_state=4),
        )
        for mutate in malformed_cases:
            with self.subTest(mutate=mutate):
                status, provider_calls, cone_calls, unused_world = \
                    self._explosion_status([], [], mutate_arguments=mutate)
                self.assertEqual('unavailable', status['status'])
                self.assertEqual('invalid_arguments', status['code'])
                self.assertEqual([], provider_calls)
                self.assertEqual([], cone_calls)

        status, provider_calls, cone_calls, unused_world = \
            self._explosion_status([], [], generation=2)
        self.assertEqual('unavailable', status['status'])
        self.assertEqual('stale_entity', status['code'])
        self.assertEqual([], provider_calls)
        self.assertEqual([], cone_calls)

    def test_ram_contact_armor_uses_both_frozen_poses_and_structural_plates(self):
        calls = []
        world = _BigWorld()
        first = world.entities[41]
        second = world.entities[42]
        screen = _Material(20.0, 0.0, kind=8)
        first_plate = _Material(70.0, 1.0, kind=9)
        second_plate = _Material(45.0, 0.8, kind=10)

        def collide(entity, pose, matrix, start, end, chassis_matrix):
            calls.append((
                entity, pose, matrix.rotation, tuple(matrix.translation),
                tuple(start), tuple(end), chassis_matrix is matrix))
            if entity is first:
                # Deliberately unsorted: the screen is encountered before the
                # structural hull even though the native iterable is reversed.
                return [
                    _Collision(2.0, 'hull', 1.0, first_plate),
                    _Collision(1.0, 'screen', 1.0, screen),
                ]
            if entity is second:
                return [_Collision(1.5, 'hull', 1.0, second_plate)]
            raise AssertionError('unexpected ram contact entity')

        bridge, unused_world = _make_bridge(
            bigworld=world, matrix_factory=_Matrix,
            ram_contact_collider=collide)
        query = _query(
            1, 'ram/h1-b1',
            _operation(
                'ram_contact_armor_evidence',
                _ram_contact_arguments()),
            entity=_entity(entity_id=41))

        self.assertTrue(bridge._accept_server_message(
            _batch(1, [query], issued_tick=8)))
        bridge.process_render_frame()
        result = _reply(bridge)['results'][0]

        self.assertEqual('ok', result['status']['status'])
        self.assertEqual({
            'result': 'ram_contact_armor_evidence',
            'value': {
                'first_armor_mm': 70.0,
                'second_armor_mm': 45.0,
            },
        }, result['status']['outcome'])
        self.assertEqual(2, len(calls))
        arguments = _ram_contact_arguments()
        self.assertEqual(arguments['first_pose'], calls[0][1])
        self.assertEqual(arguments['second_pose'], calls[1][1])
        self.assertEqual((0.25, 0.1, -0.05), calls[0][2])
        self.assertEqual((1.0, 0.0, 0.0), calls[0][3])
        self.assertEqual((-0.2, 0.0, 0.03), calls[1][2])
        self.assertEqual((-1.0, 0.0, 0.0), calls[1][3])
        self.assertLess(calls[0][4][0], 0.0)
        self.assertEqual((1.0, 0.5, 0.0), calls[0][5])
        self.assertGreater(calls[1][4][0], 0.0)
        self.assertEqual((-1.0, 0.5, 0.0), calls[1][5])
        self.assertTrue(all(call[6] for call in calls))

    def test_ram_contact_armor_ray_follows_frozen_first_impact_normal(self):
        calls = []
        world = _BigWorld()
        structural = _Material(70.0, 1.0, kind=9)

        def collide(entity, unused_pose, unused_matrix, start, end,
                    unused_chassis):
            calls.append((entity, tuple(start), tuple(end)))
            return [_Collision(1.0, 'hull', 1.0, structural)]

        arguments = _ram_contact_arguments()
        arguments['first_pose']['position'] = _vector(1, 0, 2)
        arguments['second_pose']['position'] = _vector(-1, 0, -2)
        bridge, unused_world = _make_bridge(
            bigworld=world, matrix_factory=_Matrix,
            ram_contact_collider=collide)
        query = _query(
            1, 'ram/h1-b1',
            _operation('ram_contact_armor_evidence', arguments),
            entity=_entity(entity_id=41))

        self.assertTrue(bridge._accept_server_message(_batch(1, [query])))
        bridge.process_render_frame()
        result = _reply(bridge)['results'][0]

        self.assertEqual('ok', result['status']['status'])
        self.assertEqual(2, len(calls))
        # The overlap centroid is offset from both centers. The frozen impact
        # normal, not the centroid-to-center diagonal, owns both native rays.
        self.assertEqual((0.5, 0.0), (calls[0][1][1], calls[0][1][2]))
        self.assertEqual((0.5, 0.0), (calls[0][2][1], calls[0][2][2]))
        self.assertEqual((0.5, 0.0), (calls[1][1][1], calls[1][1][2]))
        self.assertEqual((0.5, 0.0), (calls[1][2][1], calls[1][2][2]))
        self.assertLess(calls[0][1][0], 0.0)
        self.assertEqual(1.0, calls[0][2][0])
        self.assertGreater(calls[1][1][0], 0.0)
        self.assertEqual(-1.0, calls[1][2][0])

    def test_ram_contact_armor_uses_only_the_frozen_compound_provider(self):
        calls = []
        world = _BigWorld()
        world.raise_vehicle = True
        first = world.entities[41]
        first_plate = _Material(70.0, 1.0, kind=9)
        second_plate = _Material(45.0, 0.8, kind=10)

        class _Runtime(object):
            def resolve_native_oracle_entity(self, entity_ref):
                return world.entities.get(entity_ref['entity_id']), 1

            def native_explosion_evidence_at_pose(
                    self, entity, pose, start, end):
                calls.append((entity, pose, tuple(start), tuple(end)))
                material = first_plate if entity is first else second_plate
                return (
                    object(), entity.typeDescriptor,
                    (_Collision(1.0, 'hull', 1.0, material),))

            def _projectile_vehicle_matrices(self, *unused):
                raise AssertionError('live component matrix fallback used')

        class _Entities(object):
            def __init__(self, runtime):
                self._resolver = runtime.resolve_native_oracle_entity

            def resolve(self, entity_ref):
                return self._resolver(entity_ref)

        entities = _Entities(_Runtime())
        bridge, unused_world = _make_bridge(
            bigworld=world, entity_resolver=entities.resolve)
        query = _query(
            1, 'ram/h1-b1',
            _operation(
                'ram_contact_armor_evidence',
                _ram_contact_arguments()),
            entity=_entity(entity_id=41))

        self.assertTrue(bridge._accept_server_message(_batch(1, [query])))
        bridge.process_render_frame()
        result = _reply(bridge)['results'][0]

        self.assertEqual('ok', result['status']['status'])
        self.assertEqual({
            'first_armor_mm': 70.0, 'second_armor_mm': 45.0,
        }, result['status']['outcome']['value'])
        self.assertEqual(2, len(calls))
        arguments = _ram_contact_arguments()
        self.assertEqual(arguments['first_pose'], calls[0][1])
        self.assertEqual(arguments['second_pose'], calls[1][1])
        self.assertLess(calls[0][2][0], 0.0)
        self.assertEqual((1.0, 0.5, 0.0), calls[0][3])
        self.assertGreater(calls[1][2][0], 0.0)
        self.assertEqual((-1.0, 0.5, 0.0), calls[1][3])
        # A live Vehicle collision fallback would raise above.
        self.assertEqual([], world.call_threads)

    def test_ram_contact_armor_rejects_frozen_pitch_hull_without_body_ground(self):
        calls = []
        world = _BigWorld()
        world.entities[41].typeDescriptor.isPitchHullAimingAvailable = True
        structural = _Material(70.0, 1.0, kind=9)

        def frozen_provider(entity, pose, start, end):
            calls.append((entity, pose, tuple(start), tuple(end)))
            return (
                object(), entity.typeDescriptor,
                (_Collision(1.0, 'hull', 1.0, structural),))

        bridge, unused_world = _make_bridge(
            bigworld=world,
            explosion_frozen_target_provider=frozen_provider)
        query = _query(
            1, 'ram/h1-b1',
            _operation(
                'ram_contact_armor_evidence',
                _ram_contact_arguments()),
            entity=_entity(entity_id=41))

        self.assertTrue(bridge._accept_server_message(_batch(1, [query])))
        bridge.process_render_frame()
        result = _reply(bridge)['results'][0]

        self.assertEqual('unavailable', result['status']['status'])
        self.assertEqual(
            'ram_contact_armor_unavailable', result['status']['code'])
        self.assertEqual(1, len(calls))
        self.assertEqual(
            _ram_contact_arguments()['first_pose'], calls[0][1])

    def test_ram_contact_pose_requires_every_frozen_component_field(self):
        mutations = (
            lambda pose: pose.pop('turret_yaw'),
            lambda pose: pose.update(gun_pitch=float('nan')),
            lambda pose: pose.update(siege_state=4),
            lambda pose: pose.update(siege_state=2.0),
        )
        for mutate in mutations:
            calls = []
            arguments = _ram_contact_arguments()
            mutate(arguments['first_pose'])
            bridge, unused_world = _make_bridge(
                matrix_factory=_Matrix,
                ram_contact_collider=lambda *values: calls.append(values))
            query = _query(
                1, 'ram/h1-b1',
                _operation('ram_contact_armor_evidence', arguments),
                entity=_entity(entity_id=41))

            self.assertTrue(bridge._accept_server_message(
                _batch(1, [query])))
            bridge.process_render_frame()
            result = _reply(bridge)['results'][0]

            with self.subTest(mutate=mutate):
                self.assertEqual('unavailable', result['status']['status'])
                self.assertEqual('invalid_arguments', result['status']['code'])
                self.assertEqual([], calls)

    def test_ram_contact_armor_fails_closed_without_either_native_plate(self):
        world = _BigWorld()
        first = world.entities[41]
        screen = _Material(20.0, 0.0, kind=8)
        structural = _Material(70.0, 1.0, kind=9)

        def collide(entity, unused_pose, unused_matrix, unused_start,
                    unused_end, unused_chassis):
            material = structural if entity is first else screen
            return [_Collision(1.0, 'hull', 1.0, material)]

        bridge, unused_world = _make_bridge(
            bigworld=world, matrix_factory=_Matrix,
            ram_contact_collider=collide)
        query = _query(
            1, 'ram/h1-b1',
            _operation(
                'ram_contact_armor_evidence',
                _ram_contact_arguments()),
            entity=_entity(entity_id=41))

        self.assertTrue(bridge._accept_server_message(_batch(1, [query])))
        bridge.process_render_frame()
        result = _reply(bridge)['results'][0]

        self.assertEqual('unavailable', result['status']['status'])
        self.assertEqual(
            'ram_contact_armor_unavailable', result['status']['code'])
        self.assertNotIn('outcome', result['status'])

    def test_ram_contact_armor_fences_the_second_native_generation(self):
        world = _BigWorld()

        def resolve(entity_ref):
            return world.entities.get(entity_ref['entity_id']), 1

        bridge, unused_world = _make_bridge(
            bigworld=world, entity_resolver=resolve,
            matrix_factory=_Matrix,
            ram_contact_collider=lambda *unused: ())
        query = _query(
            1, 'ram/h1-b1',
            _operation(
                'ram_contact_armor_evidence',
                _ram_contact_arguments(second_generation=2)),
            entity=_entity(entity_id=41))

        self.assertTrue(bridge._accept_server_message(_batch(1, [query])))
        bridge.process_render_frame()
        result = _reply(bridge)['results'][0]

        self.assertEqual('unavailable', result['status']['status'])
        self.assertEqual('stale_entity', result['status']['code'])

    def test_destructible_operations_return_compact_json_safe_evidence(self):
        bridge, world, unused_entity, sensor = \
            _make_destructible_bridge()
        queries = [
            _query(1, 'destructible/shot/1', _operation(
                'destructible_shot_evidence',
                _destructible_shot_arguments())),
            _query(2, 'destructible/hull/42', _operation(
                'destructible_hull_evidence',
                _destructible_hull_arguments())),
        ]

        self.assertTrue(bridge._accept_server_message(_batch(1, queries)))
        self.assertEqual(1, bridge.process_render_frame())
        results = _reply(bridge)['results']

        self.assertEqual('ok', results[0]['status']['status'])
        shot = results[0]['status']['outcome']
        self.assertEqual('destructible_shot_evidence', shot['result'])
        self.assertEqual({
            'candidates', 'destroyed_skipped', 'static_collision',
        }, set(shot['value']))
        self.assertEqual(1, shot['value']['destroyed_skipped'])
        self.assertEqual({
            'chunk_id': 22,
            'item_index': 37,
            'mat_kind': None,
            'kind': 'fragile',
            'entry_distance': 4.0,
            'exit_distance': 6.0,
            'impact_position': _vector(0, 0, 4),
            'item_scale': 0.5,
            'scaled_health': 15.0,
            'ap_through': True,
            'piercing_loss': 25.0,
        }, shot['value']['candidates'][0])
        self.assertEqual({
            'distance': 6.01,
            'position': _vector(0, 0, 6.01),
            'normal': _vector(0, 0, -1),
        }, shot['value']['static_collision'])

        self.assertEqual('ok', results[1]['status']['status'])
        hull = results[1]['status']['outcome']
        self.assertEqual('destructible_hull_evidence', hull['result'])
        self.assertEqual({
            'candidates', 'frame_travel',
        }, set(hull['value']))
        self.assertEqual({
            'chunk_id': 22,
            'item_index': 38,
            'mat_kind': 73,
            'kind': 'structure',
            'obb_center': _vector(0, 0.5, 3.75),
        }, hull['value']['candidates'][0])
        self.assertEqual(0.3, hull['value']['frame_travel'])

        self.assertEqual(1, len(sensor.shot_calls))
        self.assertIs(world, sensor.shot_calls[0][0])
        self.assertEqual(
            (7, (0.0, 0.0, 0.0), (0.0, 0.0, 10.0),
             'ARMOR_PIERCING'), sensor.shot_calls[0][1:])
        self.assertEqual(1, len(sensor.hull_calls))
        self.assertEqual((
            7, (0.0, 0.0, 0.0), 0.0,
            ((-1.6, -1.0, -3.6), (1.6, 1.0, 3.6)),
            0.3), sensor.hull_calls[0])
        self.assertEqual(set(), sensor.ledger)
        self.assertEqual(0, sensor.publish_count)
        self.assertEqual([], sensor.mutation_calls)

    def test_destructible_request_shapes_and_numeric_bounds_fail_closed(self):
        shot_base = _destructible_shot_arguments()
        hull_base = _destructible_hull_arguments()
        cases = []
        value = copy.deepcopy(shot_base)
        value['extra'] = True
        cases.append(('destructible_shot_evidence', value))
        value = copy.deepcopy(shot_base)
        value['start']['x'] = True
        cases.append(('destructible_shot_evidence', value))
        value = copy.deepcopy(shot_base)
        value['end']['z'] = 10001.0
        cases.append(('destructible_shot_evidence', value))
        value = copy.deepcopy(shot_base)
        value['shell_kind'] = 'LASER'
        cases.append(('destructible_shot_evidence', value))
        value = copy.deepcopy(hull_base)
        value['yaw'] = 7.0
        cases.append(('destructible_hull_evidence', value))
        value = copy.deepcopy(hull_base)
        value['frame_travel'] = 101.0
        cases.append(('destructible_hull_evidence', value))
        value = copy.deepcopy(hull_base)
        value['kinetic_speed'] = float('nan')
        cases.append(('destructible_hull_evidence', value))

        for index, (operation_name, arguments) in enumerate(cases):
            bridge, unused_world, unused_entity, sensor = \
                _make_destructible_bridge()
            query = _query(1, 'invalid/%d' % index, _operation(
                operation_name, arguments))
            self.assertTrue(bridge._accept_server_message(
                _batch(1, [query])))
            bridge.process_render_frame()
            status = _reply(bridge)['results'][0]['status']
            self.assertEqual('unavailable', status['status'])
            self.assertEqual('invalid_arguments', status['code'])
            self.assertEqual([], sensor.shot_calls)
            self.assertEqual([], sensor.hull_calls)
            self.assertEqual([], sensor.mutation_calls)

    def test_destructible_entity_and_space_lineage_fail_closed(self):
        cases = []
        bridge, unused_world, unused_entity, sensor = \
            _make_destructible_bridge()
        arguments = _destructible_shot_arguments()
        arguments['space_id'] = 8
        cases.append((bridge, sensor, _query(
            1, 'wrong-space', _operation(
                'destructible_shot_evidence', arguments)),
            'destructible_space_mismatch'))

        bridge, unused_world, unused_entity, sensor = \
            _make_destructible_bridge(entity_space_id=8)
        cases.append((bridge, sensor, _query(
            1, 'wrong-entity-space', _operation(
                'destructible_shot_evidence',
                _destructible_shot_arguments())),
            'destructible_space_mismatch'))

        bridge, unused_world, unused_entity, sensor = \
            _make_destructible_bridge(donated_generation=1)
        cases.append((bridge, sensor, _query(
            1, 'stale-entity', _operation(
                'destructible_shot_evidence',
                _destructible_shot_arguments()),
            entity=_entity(generation=2)), 'stale_entity'))

        for bridge, sensor, query, expected_code in cases:
            self.assertTrue(bridge._accept_server_message(
                _batch(1, [query])))
            bridge.process_render_frame()
            status = _reply(bridge)['results'][0]['status']
            self.assertEqual('unavailable', status['status'])
            self.assertEqual(expected_code, status['code'])
            self.assertEqual([], sensor.shot_calls)
            self.assertEqual([], sensor.hull_calls)
            self.assertEqual([], sensor.mutation_calls)

    def test_incomplete_or_malformed_destructible_evidence_is_unavailable(self):
        shot_fail_closed = _shot_destructible_sensor_result()
        shot_fail_closed['complete'] = False
        shot_fail_closed['fail_closed'] = True
        shot_fail_closed['reasons'] = ('live_validation_unavailable',)
        shot_extra = _shot_destructible_sensor_result()
        shot_extra['unexpected'] = True
        shot_nan = _shot_destructible_sensor_result()
        shot_nan['candidates'][0]['scaled_health'] = float('nan')
        hull_retired_verdict = _hull_destructible_sensor_result()
        hull_retired_verdict['candidates'][0]['crushable'] = False
        cases = (
            ('destructible_shot_evidence',
             _destructible_shot_arguments(),
             _ReadOnlyDestructiblesSensor(shot=shot_fail_closed),
             'destructible_evidence_unavailable'),
            ('destructible_shot_evidence',
             _destructible_shot_arguments(),
             _ReadOnlyDestructiblesSensor(shot=shot_extra),
             'invalid_destructible_evidence'),
            ('destructible_shot_evidence',
             _destructible_shot_arguments(),
             _ReadOnlyDestructiblesSensor(shot=shot_nan),
             'invalid_destructible_evidence'),
            ('destructible_hull_evidence',
             _destructible_hull_arguments(),
             _ReadOnlyDestructiblesSensor(hull=hull_retired_verdict),
             'invalid_destructible_evidence'),
            ('destructible_shot_evidence',
             _destructible_shot_arguments(),
             _ReadOnlyDestructiblesSensor(
                 shot=RuntimeError('sensor exploded')),
             'destructible_evidence_unavailable'),
        )
        for index, (operation_name, arguments, sensor,
                    expected_code) in enumerate(cases):
            bridge, unused_world, unused_entity, sensor = \
                _make_destructible_bridge(sensor=sensor)
            query = _query(1, 'malformed/%d' % index, _operation(
                operation_name, arguments))
            self.assertTrue(bridge._accept_server_message(
                _batch(1, [query])))
            bridge.process_render_frame()
            status = _reply(bridge)['results'][0]['status']
            self.assertEqual('unavailable', status['status'])
            self.assertEqual(expected_code, status['code'])
            self.assertEqual(set(), sensor.ledger)
            self.assertEqual(0, sensor.publish_count)
            self.assertEqual([], sensor.mutation_calls)

    def test_all_batch_operations_preserve_cardinality_and_missing_samples(self):
        bridge, unused_world = _make_bridge()
        segments = [
            {'start': _vector(0, 0, 0), 'end': _vector(10, 0, 0),
             'collision_mask': 9},
            {'start': _vector(0, 0, 99), 'end': _vector(10, 0, 99),
             'collision_mask': 9},
        ]
        queries = [
            _query(1, 'ground-batch', _operation(
                'ground_sample_batch', {
                    'positions': [_vector(1, 8, 2), _vector(99, 8, 2)],
                })),
            _query(2, 'water-batch', _operation(
                'water_sample_batch', {
                    'positions': [_vector(1, 8, 2), _vector(99, 8, 2)],
                })),
            _query(3, 'segment-batch', _operation(
                'segment_cast_batch', {'segments': segments})),
        ]

        self.assertTrue(bridge._accept_server_message(_batch(1, queries)))
        self.assertEqual(1, bridge.process_render_frame())
        outcomes = [
            result['status']['outcome']
            for result in _reply(bridge)['results']]

        self.assertEqual(2, len(outcomes[0]['value']['samples']))
        self.assertEqual(2.5,
                         outcomes[0]['value']['samples'][0]['height'])
        self.assertIsNone(outcomes[0]['value']['samples'][1])
        self.assertEqual([4.0, None], outcomes[1]['value']['heights'])
        self.assertEqual(2, len(outcomes[2]['value']['hits']))
        self.assertEqual(0.5,
                         outcomes[2]['value']['hits'][0]['fraction'])
        self.assertIsNone(outcomes[2]['value']['hits'][1])

    def test_spotting_and_barrel_lane_are_independent_pair_evidence(self):
        foliage = _Foliage(0.35)
        bridge, world = _make_bridge(foliage_provider=lambda: foliage)

        def collision(start, unused_end, unused_mask):
            # Visibility starts at +2.0m and is clear. Both independently
            # trimmed barrel rays start at +2.5m and hit static geometry.
            if abs(start.y - 2.0) < 1.0e-6:
                return None
            return (_Vector(start.x + 1.0, start.y, start.z),
                    _Vector(-1.0, 0.0, 0.0), 128)

        world.segment_callback = collision
        queries = [
            _query(1, 'obs/1/spot/b2/l1', _operation(
                'spotting_evidence', _observation_arguments(True))),
            _query(2, 'obs/1/lane/b2/l1', _operation(
                'firing_lane_evidence', _observation_arguments())),
        ]

        self.assertTrue(bridge._accept_server_message(_batch(1, queries)))
        self.assertEqual(1, bridge.process_render_frame())
        results = _reply(bridge)['results']

        spotting = results[0]['status']['outcome']
        lane = results[1]['status']['outcome']
        self.assertEqual('spotting_evidence', spotting['result'])
        self.assertEqual({
            'line_of_sight': True,
            'foliage_bonus': 0.35,
            'evaluated_for_recent_fire': True,
        }, spotting['value'])
        self.assertEqual(
            {'result': 'firing_lane_evidence', 'value': {'clear': False}},
            lane)
        self.assertEqual([
            ((0.0, 0.0, 0.0), (20.0, 0.0, 0.0), True),
        ], foliage.calls)

    def test_missing_foliage_fails_spotting_closed_but_not_barrel_lane(self):
        bridge, world = _make_bridge()
        world.segment_callback = lambda unused_start, unused_end, unused_mask: None
        queries = [
            _query(1, 'obs/1/spot/b2/l1', _operation(
                'spotting_evidence', _observation_arguments(False))),
            _query(2, 'obs/1/lane/b2/l1', _operation(
                'firing_lane_evidence', _observation_arguments())),
        ]

        self.assertTrue(bridge._accept_server_message(_batch(1, queries)))
        bridge.process_render_frame()
        results = _reply(bridge)['results']

        self.assertEqual('unavailable', results[0]['status']['status'])
        self.assertEqual('foliage_unavailable', results[0]['status']['code'])
        self.assertEqual('ok', results[1]['status']['status'])
        self.assertTrue(results[1]['status']['outcome']['value']['clear'])

    def test_loaded_battle_runtime_foliage_is_reused_through_bound_resolver(self):
        world = _BigWorld()
        foliage = _Foliage(0.2)

        class Runtime(object):

            def __init__(self):
                self._foliage = foliage

            def resolve_native_oracle_entity(self, entity_id):
                return world.entities.get(entity_id)

        class Entities(object):

            def __init__(self, runtime):
                self._resolver = runtime.resolve_native_oracle_entity

            def resolve(self, entity_ref):
                return (world.entities.get(entity_ref['entity_id']),
                        entity_ref['generation'])

        entities = Entities(Runtime())
        bridge, unused_world = _make_bridge(
            bigworld=world, entity_resolver=entities.resolve)
        world.segment_callback = lambda unused_start, unused_end, unused_mask: None
        query = _query(1, 'obs/1/spot/b2/l1', _operation(
            'spotting_evidence', _observation_arguments(False)))

        self.assertTrue(bridge._accept_server_message(_batch(1, [query])))
        bridge.process_render_frame()
        status = _reply(bridge)['results'][0]['status']

        self.assertEqual('ok', status['status'])
        self.assertEqual(0.2, status['outcome']['value']['foliage_bonus'])
        self.assertEqual(False, foliage.calls[0][2])

    def test_spg_barrel_lane_is_unavailable_without_artillery_contract(self):
        bridge, world = _make_bridge()
        world.entities[41] = _Entity(world, tags=('SPG',))
        query = _query(1, 'obs/1/lane/b2/l1', _operation(
            'firing_lane_evidence', _observation_arguments()))

        self.assertTrue(bridge._accept_server_message(_batch(1, [query])))
        bridge.process_render_frame()
        status = _reply(bridge)['results'][0]['status']

        self.assertEqual('unavailable', status['status'])
        self.assertEqual('firing_lane_unavailable', status['code'])

    def test_recent_fire_branch_must_be_an_explicit_boolean(self):
        bridge, unused_world = _make_bridge(foliage_provider=_Foliage())
        query = _query(1, 'obs/1/spot/b2/l1', _operation(
            'spotting_evidence', _observation_arguments(1)))

        self.assertTrue(bridge._accept_server_message(_batch(1, [query])))
        bridge.process_render_frame()
        status = _reply(bridge)['results'][0]['status']

        self.assertEqual('unavailable', status['status'])
        self.assertEqual('invalid_arguments', status['code'])

    def test_unknown_operation_and_native_exception_are_unavailable(self):
        bridge, world = _make_bridge()
        world.raise_world = True
        queries = [
            _query(1, 'world-error', _operation('segment_cast', {
                'start': _vector(0, 0, 0),
                'end': _vector(10, 0, 0),
                'collision_mask': 1,
            })),
            _query(2, 'future-operation', _operation(
                'future_operation', {'opaque': True})),
        ]

        self.assertTrue(bridge._accept_server_message(_batch(1, queries)))
        self.assertEqual(1, bridge.process_render_frame())
        results = _reply(bridge)['results']

        self.assertTrue(bridge.running)
        self.assertEqual('unavailable', results[0]['status']['status'])
        self.assertEqual('native_exception', results[0]['status']['code'])
        self.assertEqual('unavailable', results[1]['status']['status'])
        self.assertEqual('unknown_operation', results[1]['status']['code'])
        for result in results:
            self.assertEqual(
                {'status', 'code', 'message'}, set(result['status']))

    def test_empty_vehicle_collision_trace_is_an_explicit_miss(self):
        status = self._vehicle_status([])

        self.assertEqual('ok', status['status'])
        self.assertIsNone(status['outcome']['value']['hit'])

    def test_internal_layout_none_and_empty_have_distinct_wire_meanings(self):
        material = _Material(60.0, 1.0, kind=1)
        collisions = [_Collision(5.0, 'hull', 0.8, material)]

        unavailable = self._vehicle_status(collisions, internal_hits=None)
        clear = self._vehicle_status(collisions, internal_hits=[])

        self.assertIsNone(
            unavailable['outcome']['value']['hit']['internal_hits'])
        self.assertEqual(
            [], clear['outcome']['value']['hit']['internal_hits'])

    def test_descriptor_crew_extra_is_a_typed_native_target(self):
        material = _Material(
            0.0, 0.0, kind=2, extra_name='gunner1Health',
            chance_to_hit_by_projectile=0.33,
            chance_to_hit_by_explosion=0.15)
        status = self._vehicle_status(
            [_Collision(5.0, 'turret', 0.8, material)],
            internal_hits=[])

        self.assertEqual('ok', status['status'])
        layer = status['outcome']['value']['hit']['layers'][0]
        self.assertEqual(
            {'kind': 'crew', 'name': 'gunner1'},
            layer['critical_target'])
        self.assertEqual(0.33, layer['chance_to_hit_by_projectile'])
        self.assertEqual(0.15, layer['chance_to_hit_by_explosion'])

    def test_equal_distance_vehicle_layers_preserve_native_order(self):
        first_material = _Material(20.0, 0.0, kind=1)
        second_material = _Material(60.0, 1.0, kind=2)
        status = self._vehicle_status([
            _Collision(5.0, 'track', 0.9, first_material),
            _Collision(5.0, None, 0.8, second_material),
        ])

        self.assertEqual('ok', status['status'])
        hit = status['outcome']['value']['hit']
        self.assertEqual('track', hit['hit_part'])
        self.assertEqual(['track', None], [
            layer['component'] for layer in hit['layers']])

    def test_malformed_native_vehicle_layers_are_unavailable_atomically(self):
        material = _Material(60.0, 1.0, kind=1)
        missing_distance = _Collision(5.0, 'hull', 0.8, material)
        del missing_distance.dist
        missing_angle = _Collision(5.0, 'hull', 0.8, material)
        del missing_angle.hitAngleCos
        missing_material = _Collision(5.0, 'hull', 0.8, material)
        del missing_material.matInfo
        missing_flag_material = _Material(60.0, 1.0, kind=1)
        del missing_flag_material.useHitAngle
        nan_material = _Material(float('nan'), 1.0, kind=1)
        valid = _Collision(4.0, 'track', 0.9, material)
        cases = (
            [valid, missing_distance],
            [valid, missing_angle],
            [valid, missing_material],
            [_Collision(5.0, 'hull', 0.8, missing_flag_material)],
            [_Collision(5.0, 'hull', 0.8, nan_material)],
            [_Collision(float('nan'), 'hull', 0.8, material)],
            [_Collision(5.0, 'hull', float('nan'), material)],
            [_Collision(5.0, 'hull', 0.8, material)
             for unused in range(BRIDGE.MAX_VEHICLE_HIT_LAYERS + 1)],
        )
        for collisions in cases:
            with self.subTest(layer_count=len(collisions)):
                status = self._vehicle_status(collisions)
                self.assertEqual('unavailable', status['status'])
                self.assertEqual('invalid_vehicle_hit', status['code'])

    def test_recognized_critical_extra_requires_both_native_chances(self):
        missing_projectile = _Material(
            60.0, 1.0, kind=1, extra_name='engineHealth')
        del missing_projectile.chanceToHitByProjectile
        missing_explosion = _Material(
            60.0, 1.0, kind=1, extra_name='engineHealth')
        del missing_explosion.chanceToHitByExplosion
        nan_chance = _Material(
            60.0, 1.0, kind=1, extra_name='engineHealth',
            chance_to_hit_by_projectile=float('nan'))
        cases = (missing_projectile, missing_explosion, nan_chance)

        for material in cases:
            with self.subTest(material=material):
                status = self._vehicle_status([
                    _Collision(5.0, 'hull', 0.8, material)])
                self.assertEqual('unavailable', status['status'])
                self.assertEqual('invalid_vehicle_hit', status['code'])

    def test_internal_trace_is_strict_bounded_and_cannot_repeat_native_extra(self):
        material = _Material(
            60.0, 1.0, kind=1, extra_name='engineHealth')
        collisions = [_Collision(5.0, 'hull', 0.8, material)]
        cases = (
            [(float('nan'), 'commanderHealth')],
            [(5.5, 'unknownHealth')],
            [(5.5, 'engineHealth')],
            [(5.5, 'commanderHealth')
             for unused in range(BRIDGE.MAX_VEHICLE_INTERNAL_HITS + 1)],
        )

        for internal_hits in cases:
            with self.subTest(internal_count=len(internal_hits)):
                status = self._vehicle_status(
                    collisions, internal_hits=internal_hits)
                self.assertEqual('unavailable', status['status'])
                self.assertEqual('invalid_vehicle_hit', status['code'])

    def test_reused_entity_id_requires_the_next_native_generation(self):
        bridge, world = _make_bridge()
        operation = _operation(
            'node_transform', {'node': 'HP_gunFire'})
        self.assertTrue(bridge._accept_server_message(
            _batch(1, [_query(1, 'node', operation)])))
        bridge.process_render_frame()
        self.assertEqual('ok', _reply(bridge)['results'][0]['status']['status'])

        world.entities[42] = _Entity(world)
        stale = _query(2, 'node', operation, generation=2, entity=_entity(1))
        self.assertTrue(bridge._accept_server_message(_batch(2, [stale])))
        bridge.process_render_frame()
        result = _reply(bridge)['results'][0]
        self.assertEqual('unavailable', result['status']['status'])
        self.assertEqual('stale_entity', result['status']['code'])

        current = _query(
            3, 'node', operation, generation=3, entity=_entity(2))
        self.assertTrue(bridge._accept_server_message(_batch(3, [current])))
        bridge.process_render_frame()
        self.assertEqual('ok', _reply(bridge)['results'][0]['status']['status'])


class NativeOracleOrderingAndBoundsTests(unittest.TestCase):

    @staticmethod
    def _ground_query(query_id=1, key='ground', generation=1):
        return _query(query_id, key, _operation(
            'ground_sample', {'position': _vector(1, 8, 2)}),
            generation=generation)

    def test_frame_sequence_and_query_generation_advance_in_order(self):
        bridge, unused_world = _make_bridge()
        first = _batch(
            10, [self._ground_query()], issued_tick=40,
            world_revision=100)
        second = _batch(
            11, [self._ground_query(2, generation=2)], issued_tick=40,
            world_revision=100)

        self.assertTrue(bridge._accept_server_message(first))
        self.assertTrue(bridge._accept_server_message(second))
        self.assertEqual(2, bridge.process_render_frame())
        replies = bridge._drain_outbound_messages()

        self.assertEqual([10, 11], [
            message['payload']['batch_seq'] for message in replies])
        self.assertEqual([1, 2], [
            message['payload']['oracle_frame_seq'] for message in replies])
        self.assertEqual([1, 2], [
            message['payload']['results'][0]['query_generation']
            for message in replies])

    def test_lineage_order_and_t_plus_three_violations_fail_closed(self):
        cases = []
        duplicate_sequence = _batch(
            1, [self._ground_query(2, generation=2)], issued_tick=5,
            world_revision=10)
        cases.append(duplicate_sequence)
        regressed_tick = _batch(
            2, [self._ground_query(2, generation=2)], issued_tick=4,
            world_revision=10)
        cases.append(regressed_tick)
        regressed_revision = _batch(
            2, [self._ground_query(2, generation=2)], issued_tick=5,
            world_revision=9)
        cases.append(regressed_revision)
        stale_query_generation = _batch(
            2, [self._ground_query(2, generation=1)], issued_tick=5,
            world_revision=10)
        cases.append(stale_query_generation)
        wrong_oracle_generation = _batch(
            2, [self._ground_query(2, generation=2)], issued_tick=5,
            world_revision=10, oracle_generation=2)
        cases.append(wrong_oracle_generation)
        wrong_pipeline = _batch(
            2, [self._ground_query(2, generation=2)], issued_tick=5,
            world_revision=10)
        wrong_pipeline['payload']['apply_tick'] = 9
        cases.append(wrong_pipeline)

        for invalid in cases:
            bridge, unused_world = _make_bridge()
            first = _batch(
                1, [self._ground_query()], issued_tick=5,
                world_revision=10)
            self.assertTrue(bridge._accept_server_message(first))
            self.assertFalse(bridge._accept_server_message(invalid))
            self.assertFalse(bridge.running)
            self.assertFalse(bridge.connected)
            self.assertFalse(bridge.ready)
            self.assertEqual([], bridge._drain_outbound_messages())

    def test_render_processing_is_bounded_for_queries_and_controls(self):
        bridge, unused_world = _make_bridge()
        for sequence in range(1, 6):
            query = self._ground_query(sequence, 'ground-%d' % sequence)
            self.assertTrue(bridge._accept_server_message(
                _batch(sequence, [query])))

        self.assertEqual(BRIDGE.MAX_BATCHES_PER_RENDER,
                         bridge.process_render_frame())
        self.assertEqual(4, len(bridge._drain_outbound_messages()))
        self.assertEqual(1, bridge.pending_batches())
        self.assertEqual(1, bridge.process_render_frame())
        self.assertEqual(1, len(bridge._drain_outbound_messages()))

        observed = []
        control_bridge, unused_world = _make_bridge(
            on_message=observed.append)
        for sequence in range(BRIDGE.MAX_MESSAGES_PER_RENDER + 2):
            self.assertTrue(control_bridge._accept_server_message({
                'type': 'lifecycle-%d' % sequence,
                'sequence': sequence,
            }))
        self.assertEqual(0, control_bridge.process_render_frame())
        self.assertEqual(BRIDGE.MAX_MESSAGES_PER_RENDER, len(observed))
        self.assertEqual(2, len(control_bridge._inbound))

    def test_query_primitive_pending_and_encoded_line_limits_fail_closed(self):
        too_many_queries = [
            self._ground_query(index, 'ground-%d' % index)
            for index in range(1, BRIDGE.MAX_BATCH_QUERIES + 2)]
        bridge, unused_world = _make_bridge()
        self.assertFalse(bridge._accept_server_message(
            _batch(1, too_many_queries)))
        self.assertFalse(bridge.running)

        bridge, unused_world = _make_bridge()
        positions = [_vector(index, 8, 2)
                     for index in range(
                         BRIDGE.MAX_PRIMITIVE_OPERATIONS + 1)]
        query = _query(1, 'too-many-ground-samples', _operation(
            'ground_sample_batch', {'positions': positions}))
        self.assertFalse(bridge._accept_server_message(_batch(1, [query])))
        self.assertFalse(bridge.running)

        bridge, unused_world = _make_bridge()
        for index in range(BRIDGE.MAX_PENDING_MESSAGES):
            self.assertTrue(bridge._accept_server_message({
                'type': 'control', 'sequence': index}))
        self.assertFalse(bridge._accept_server_message({
            'type': 'control', 'sequence': BRIDGE.MAX_PENDING_MESSAGES}))
        self.assertFalse(bridge.running)

        with self.assertRaises(BRIDGE.OracleProtocolError):
            BRIDGE._json_payload({
                'type': 'oversized',
                'value': 'x' * BRIDGE.MAX_LINE_BYTES,
            })

    def test_reconnect_generation_fences_prior_connection(self):
        bridge, unused_world = _make_bridge(generation=1)
        self.assertEqual(1, bridge.hello_payload()['oracle_generation'])
        bridge.stop()
        bridge._begin_embedded_session(2)
        self.assertEqual(2, bridge.hello_payload()['oracle_generation'])

        old = _batch(1, [self._ground_query()], oracle_generation=1)
        self.assertFalse(bridge._accept_server_message(old))
        self.assertFalse(bridge.running)


class NativeOracleTransportTests(unittest.TestCase):

    def test_socket_io_stays_off_render_thread_and_native_calls_stay_on_it(self):
        world = _BigWorld()
        query = _query(1, 'world', _operation('segment_cast', {
            'start': _vector(0, 0, 0),
            'end': _vector(10, 0, 0),
            'collision_mask': 3,
        }))
        fake_socket = _FakeSocket([
            _wire(_welcome(), _batch(1, [query])),
        ])
        bridge = BRIDGE.NativeOracleBridge(
            host='127.0.0.1', port=28782, bigworld=world,
            vector_factory=_Vector, socket_factory=lambda: fake_socket)

        self.assertTrue(bridge.start())
        try:
            _wait_until(lambda: bridge.pending_batches() == 1)
            main_thread = threading.current_thread().ident
            self.assertEqual(1, bridge.process_render_frame())
            _wait_until(lambda: any(
                message.get('type') == 'query_reply'
                for message in fake_socket.messages()))

            messages = fake_socket.messages()
            hello = next(message for message in messages
                         if message.get('type') == 'hello')
            reply = next(message for message in messages
                         if message.get('type') == 'query_reply')
            self.assertEqual(1, hello['oracle_generation'])
            self.assertIn('native_oracle_v1', hello['capabilities'])
            self.assertEqual(1, reply['payload']['oracle_frame_seq'])
            self.assertEqual({main_thread}, set(world.call_threads))
            self.assertEqual({main_thread}, set(world.callback_threads))
            self.assertNotIn(main_thread, fake_socket.send_threads)
            self.assertEqual(('127.0.0.1', 28782), fake_socket.address)
            self.assertEqual(BRIDGE.SOCKET_TIMEOUT_SECONDS,
                             fake_socket.timeout)
        finally:
            bridge.stop()

    def test_actual_restart_increments_generation_in_each_hello(self):
        world = _BigWorld()
        sockets = [
            _FakeSocket([_wire(_welcome())]),
            _FakeSocket([_wire(_welcome())]),
        ]

        def socket_factory():
            return sockets.pop(0)

        first_socket = sockets[0]
        second_socket = sockets[1]
        bridge = BRIDGE.NativeOracleBridge(
            bigworld=world, vector_factory=_Vector,
            socket_factory=socket_factory)
        self.assertTrue(bridge.start())
        _wait_until(lambda: bridge.ready)
        bridge.stop()
        self.assertTrue(bridge.start())
        try:
            _wait_until(lambda: bridge.ready)
            first_hello = next(
                message for message in first_socket.messages()
                if message.get('type') == 'hello')
            second_hello = next(
                message for message in second_socket.messages()
                if message.get('type') == 'hello')
            self.assertEqual(1, first_hello['oracle_generation'])
            self.assertEqual(2, second_hello['oracle_generation'])
        finally:
            bridge.stop()

    def test_oversized_receive_buffer_disconnects_fail_closed(self):
        world = _BigWorld()
        fake_socket = _FakeSocket([
            b'x' * (BRIDGE.MAX_BUFFER_BYTES + 1),
        ])
        bridge = BRIDGE.NativeOracleBridge(
            bigworld=world, vector_factory=_Vector,
            socket_factory=lambda: fake_socket)

        self.assertTrue(bridge.start())
        _wait_until(lambda: not bridge.running)

        self.assertFalse(bridge.connected)
        self.assertFalse(bridge.ready)
        self.assertIn('receive buffer exceeds limit', bridge.last_error)
        self.assertEqual([], bridge._drain_outbound_messages())
        self.assertEqual([], world.cancel_threads)


if __name__ == '__main__':
    unittest.main()
