# -*- coding: utf-8 -*-
from __future__ import print_function

"""Bounded #1513 native-world oracle for the Rust LAN authority.

The socket thread only frames NDJSON and moves immutable messages through
bounded queues. Every BigWorld, entity, model-node, and hit-tester call runs in
``process_render_frame`` (normally scheduled with ``BigWorld.callback``).
This module has no bot, HP, damage, projectile-resolution, or client-verdict
authority.
"""

import collections
import json
import math
import socket
import threading
import time


LAN_PROTOCOL_VERSION = 5
ORACLE_PROTOCOL_VERSION = 1
ORACLE_PIPELINE_TICKS = 3
CLIENT_BUILD = 'wot-0.9.22.0.1-cn-1513'
WORKER_ROLE = 'simulation_worker'
WORKER_ID = -1

SIMULATION_WORKER_CAPABILITY = 'simulation_worker_v1'
NATIVE_ORACLE_CAPABILITY = 'native_oracle_v1'
PROJECTILE_LEDGER_CAPABILITY = 'projectile_ledger_v2'
DESTRUCTIBLE_CATALOG_CAPABILITY = 'destructible_catalog_v5'
LEAN_SNAPSHOT_CAPABILITY = 'lean_snapshot_manifest_v1'
RAM_CONTACT_CAPABILITY = 'ram_contact_ledger_v3'
HUMAN_RAM_CAPABILITY = 'human_ram_timeline_v1'
HE_EXPLOSION_EVIDENCE_CAPABILITY = 'he_explosion_evidence_v1'
PLAYER_FIRE_INTENT_CAPABILITY = 'player_fire_intent_v4'
PLAYER_ENVIRONMENT_CAPABILITY = 'player_environment_v2'
EFFECTIVE_PARAMS_CAPABILITY = 'effective_params_v1'
RICOCHET_CONTINUATION_CAPABILITY = 'ricochet_continuation_v1'
PLAYER_AMMO_AUTHORITY_CAPABILITY = 'player_ammo_authority_v1'
PLAYER_AUTHORITY_LOADOUT_CAPABILITY = 'player_authority_loadout_v1'

CLIENT_CAPABILITIES = (
    PROJECTILE_LEDGER_CAPABILITY,
    DESTRUCTIBLE_CATALOG_CAPABILITY,
    LEAN_SNAPSHOT_CAPABILITY,
    RAM_CONTACT_CAPABILITY,
    HUMAN_RAM_CAPABILITY,
    HE_EXPLOSION_EVIDENCE_CAPABILITY,
    PLAYER_FIRE_INTENT_CAPABILITY,
    PLAYER_ENVIRONMENT_CAPABILITY,
    EFFECTIVE_PARAMS_CAPABILITY,
    RICOCHET_CONTINUATION_CAPABILITY,
    PLAYER_AMMO_AUTHORITY_CAPABILITY,
    PLAYER_AUTHORITY_LOADOUT_CAPABILITY,
    SIMULATION_WORKER_CAPABILITY,
    NATIVE_ORACLE_CAPABILITY,
)

MAX_LINE_BYTES = 256 * 1024
MAX_BUFFER_BYTES = MAX_LINE_BYTES * 2
MAX_BATCH_QUERIES = 64
MAX_PRIMITIVE_OPERATIONS = 256
MAX_QUERY_KEY_BYTES = 128
MAX_ERROR_CODE_BYTES = 64
MAX_ERROR_MESSAGE_BYTES = 256
MAX_PENDING_MESSAGES = 64
MAX_OUTBOUND_MESSAGES = 64
MAX_OUTBOUND_BYTES = MAX_LINE_BYTES * 4
MAX_MESSAGES_PER_RENDER = 8
MAX_BATCHES_PER_RENDER = 4
MAX_PRIMITIVES_PER_RENDER = 256
MAX_VEHICLE_HIT_LAYERS = 128
MAX_VEHICLE_INTERNAL_HITS = 64
MAX_VEHICLE_HIT_TEXT_BYTES = 128
MAX_VEHICLE_HIT_DISTANCE_M = 10000.0
MAX_VEHICLE_HIT_ARMOR_MM = 1000000000.0
MAX_VEHICLE_DAMAGE_FACTOR = 1000000000.0
MAX_FOLIAGE_CAMOUFLAGE_BONUS = 0.60
MAX_RAM_CONTACT_COORDINATE_M = 5000.0
MAX_RAM_CONTACT_POSE_DISTANCE_M = 100.0
MAX_RAM_CONTACT_POSE_ANGLE_RAD = 1000000.0
RAM_CONTACT_NORMAL_TOLERANCE = 0.001
MAX_EXPLOSION_WORLD_COORDINATE_M = 5000.0
MAX_EXPLOSION_RAY_DISTANCE_M = 101.0
MAX_EXPLOSION_POSE_ANGLE_RAD = 1000000.0
MAX_EXPLOSION_CALIBER_MM = 1000.0
EXPLOSION_DIRECTION_TOLERANCE = 0.001
MAX_DESTRUCTIBLE_WORLD_COORDINATE_M = 100000.0
MAX_DESTRUCTIBLE_SEGMENT_M = 10000.0
MAX_DESTRUCTIBLE_HULL_COORDINATE_M = 100.0
MAX_DESTRUCTIBLE_FRAME_TRAVEL_M = 100.0
MAX_DESTRUCTIBLE_ITEM_SCALE = 1000.0
MAX_DESTRUCTIBLE_CANDIDATES = 64
MAX_DESTRUCTIBLE_HULL_CANDIDATES = 32
MAX_DESTRUCTIBLE_SKIPPED = 256
MAX_DESTRUCTIBLE_REASON_COUNT = 16
MAX_DESTRUCTIBLE_REASON_BYTES = 64
DESTRUCTIBLE_POINT_EPSILON_M = 0.001
DESTRUCTIBLE_AMBIGUITY_EPSILON_M = 0.075
DESTRUCTIBLE_AP_THROUGH_MAX_HP = 19.0
DESTRUCTIBLE_AP_PIERCING_LOSS_MM = 25.0
RENDER_INTERVAL_SECONDS = 1.0 / 60.0
SOCKET_TIMEOUT_SECONDS = 0.05
SEND_STALL_SECONDS = 5.0
THREAD_JOIN_SECONDS = 0.1
PING_INTERVAL_SECONDS = 1.0
WORLD_PROBE_HEIGHT = 2048.0
WORLD_COLLISION_MASK = 128
MAX_U64 = 18446744073709551615
MAX_I64 = 9223372036854775807
MAX_F32 = 3.402823466e38
_MISSING = object()

CRITICAL_DEVICE_HEALTH_NAMES = frozenset((
    'ammoBayHealth',
    'engineHealth',
    'fuelTankHealth',
    'gunHealth',
    'leftTrackHealth',
    'radioHealth',
    'rightTrackHealth',
    'surveyingDeviceHealth',
    'turretRotatorHealth',
))
CRITICAL_CREW_INSTANCES = frozenset((
    'commander',
    'driver',
    'gunner1',
    'gunner2',
    'loader1',
    'loader2',
    'radioman1',
    'radioman2',
))
DESTRUCTIBLE_SHELL_KINDS = frozenset((
    'ARMOR_PIERCING',
    'ARMOR_PIERCING_HE',
    'ARMOR_PIERCING_CR',
    'HOLLOW_CHARGE',
    'HIGH_EXPLOSIVE',
))
DESTRUCTIBLE_AP_SHELL_KINDS = frozenset((
    'ARMOR_PIERCING',
    'ARMOR_PIERCING_HE',
    'ARMOR_PIERCING_CR',
))
DESTRUCTIBLE_KINDS = frozenset(('fragile', 'structure', 'falling'))

try:
    _TEXT_TYPES = (basestring,)
except NameError:
    _TEXT_TYPES = (str,)

try:
    _UNICODE_TYPE = unicode
except NameError:
    _UNICODE_TYPE = str

try:
    _INTEGER_TYPES = (int, long)
except NameError:
    _INTEGER_TYPES = (int,)


class OracleProtocolError(Exception):
    pass


class OracleUnavailable(Exception):

    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = _bounded_text(code, 'native_error', MAX_ERROR_CODE_BYTES)
        self.message = _bounded_text(
            message, 'native oracle query unavailable',
            MAX_ERROR_MESSAGE_BYTES)


def _bounded_text(value, default, maximum):
    try:
        if not isinstance(value, _TEXT_TYPES):
            value = str(value)
        if not isinstance(value, _UNICODE_TYPE):
            value = value.decode('utf-8', 'replace')
    except Exception:
        value = default
        if not isinstance(value, _UNICODE_TYPE):
            value = value.decode('utf-8', 'replace')
    if not value:
        value = default
    result = []
    used = 0
    for character in value:
        codepoint = ord(character)
        if codepoint < 32 or 127 <= codepoint <= 159:
            continue
        encoded = character.encode('utf-8')
        if used + len(encoded) > maximum:
            break
        result.append(character)
        used += len(encoded)
    return ''.join(result) if result else default


def _exact_int(value, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, _INTEGER_TYPES):
        return None
    result = int(value)
    if minimum is not None and result < minimum:
        return None
    if maximum is not None and result > maximum:
        return None
    return result


def _finite(value):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise OracleUnavailable('invalid_numeric', 'native value is not numeric')
    if math.isnan(result) or math.isinf(result):
        raise OracleUnavailable('invalid_numeric', 'native value is not finite')
    if abs(result) > MAX_F32:
        raise OracleUnavailable(
            'invalid_numeric', 'native value exceeds protocol f32 range')
    return result


def _bounded_finite(value, minimum, maximum, field):
    try:
        result = _finite(value)
    except OracleUnavailable:
        raise OracleUnavailable(
            'invalid_vehicle_hit', '%s is not a finite wire number' % field)
    if result < minimum or result > maximum:
        raise OracleUnavailable(
            'invalid_vehicle_hit', '%s is outside the native wire bounds' %
            field)
    return result


def _native_attribute(value, name, field):
    try:
        result = getattr(value, name)
    except Exception:
        raise OracleUnavailable(
            'invalid_vehicle_hit', '%s is unavailable' % field)
    if result is None:
        raise OracleUnavailable(
            'invalid_vehicle_hit', '%s is unavailable' % field)
    return result


def _optional_native_text(value, field):
    if value is None:
        return None
    if not isinstance(value, _TEXT_TYPES):
        raise OracleUnavailable(
            'invalid_vehicle_hit', '%s is not text' % field)
    try:
        if not isinstance(value, _UNICODE_TYPE):
            value = value.decode('utf-8')
        encoded = value.encode('utf-8')
    except (UnicodeDecodeError, UnicodeEncodeError):
        raise OracleUnavailable(
            'invalid_vehicle_hit', '%s is not valid UTF-8' % field)
    if (not value or len(encoded) > MAX_VEHICLE_HIT_TEXT_BYTES or
            any(ord(character) < 32 or 127 <= ord(character) <= 159
                for character in value)):
        raise OracleUnavailable(
            'invalid_vehicle_hit', '%s is outside the text bounds' % field)
    return value


def _native_material_flag(material, canonical_name, legacy_name=None):
    value = _MISSING
    try:
        value = getattr(material, canonical_name)
    except AttributeError:
        pass
    except Exception:
        raise OracleUnavailable(
            'invalid_vehicle_hit', 'material.%s is unavailable' %
            canonical_name)
    if value is _MISSING and legacy_name is not None:
        try:
            value = getattr(material, legacy_name)
        except AttributeError:
            pass
        except Exception:
            raise OracleUnavailable(
                'invalid_vehicle_hit', 'material.%s is unavailable' %
                legacy_name)
    if isinstance(value, bool):
        return value
    parsed = _exact_int(value, 0, 1)
    if parsed is None:
        raise OracleUnavailable(
            'invalid_vehicle_hit', 'material.%s is not a native BOOL' %
            canonical_name)
    return bool(parsed)


def _vehicle_hit_material(material):
    if material is None:
        raise OracleUnavailable(
            'invalid_vehicle_hit', 'collision material is unavailable')
    armor = _bounded_finite(
        _native_attribute(material, 'armor', 'material.armor'),
        0.0, MAX_VEHICLE_HIT_ARMOR_MM, 'material.armor')
    vehicle_damage_factor = _bounded_finite(
        _native_attribute(
            material, 'vehicleDamageFactor',
            'material.vehicleDamageFactor'),
        0.0, MAX_VEHICLE_DAMAGE_FACTOR,
        'material.vehicleDamageFactor')
    try:
        kind = getattr(material, 'kind')
    except AttributeError:
        kind = None
    except Exception:
        raise OracleUnavailable(
            'invalid_vehicle_hit', 'material.kind is unavailable')
    if kind is not None:
        kind = _exact_int(kind, -MAX_I64, MAX_I64)
        if kind is None:
            raise OracleUnavailable(
                'invalid_vehicle_hit', 'material.kind is invalid')
    native_identity = None
    if kind is None:
        # The bridge fences replies by oracle_generation, so CPython's object
        # identity is used only inside the lifetime of this loaded native world.
        native_identity = _exact_int(id(material), 1, MAX_U64)
        if native_identity is None:
            raise OracleUnavailable(
                'invalid_vehicle_hit', 'material native identity is invalid')
    collide_once_only = _native_material_flag(
        material, 'collideOnceOnly')
    if collide_once_only and kind is None and native_identity is None:
        raise OracleUnavailable(
            'invalid_vehicle_hit',
            'collide-once material has no stable native identity')
    return {
        'armor_mm': armor,
        'vehicle_damage_factor': vehicle_damage_factor,
        'kind': kind,
        'native_identity': native_identity,
        'collide_once_only': collide_once_only,
        'use_hit_angle': _native_material_flag(material, 'useHitAngle'),
        'check_caliber_for_hit_angle_norm': _native_material_flag(
            material, 'checkCaliberForHitAngleNorm'),
        'may_ricochet': _native_material_flag(material, 'mayRicochet'),
        'check_caliber_for_ricochet': _native_material_flag(
            material, 'checkCaliberForRicochet',
            'checkCaliberForRichet'),
    }


def _descriptor_crew_instances(descriptor):
    if descriptor is None:
        return None
    try:
        crew_roles = tuple(descriptor.type.crewRoles)
    except Exception:
        return None
    next_index = {'gunner': 1, 'loader': 1, 'radioman': 1}
    result = set()
    for index, roles in enumerate(crew_roles):
        try:
            main_role = roles[0]
        except Exception:
            raise OracleUnavailable(
                'invalid_vehicle_hit',
                'descriptor crew role %d is unavailable' % index)
        if not isinstance(main_role, _TEXT_TYPES):
            raise OracleUnavailable(
                'invalid_vehicle_hit',
                'descriptor crew role %d is not text' % index)
        main_role = str(main_role)
        if main_role in next_index:
            instance = main_role + str(next_index[main_role])
            next_index[main_role] += 1
        else:
            instance = main_role
        if instance not in CRITICAL_CREW_INSTANCES or instance in result:
            raise OracleUnavailable(
                'invalid_vehicle_hit',
                'descriptor crew instance is unsupported')
        result.add(instance)
    return frozenset(result)


def _critical_target_from_extra_name(extra_name, crew_instances,
                                     required=False):
    if extra_name in CRITICAL_DEVICE_HEALTH_NAMES:
        return {'kind': 'device', 'name': extra_name}
    instance = None
    if extra_name.endswith('Health'):
        candidate = extra_name[:-6]
        if candidate in CRITICAL_CREW_INSTANCES:
            instance = candidate
    if instance is not None:
        if crew_instances is None:
            raise OracleUnavailable(
                'invalid_vehicle_hit',
                'descriptor crew roster is unavailable')
        if instance in crew_instances:
            return {'kind': 'crew', 'name': instance}
    if required:
        raise OracleUnavailable(
            'invalid_vehicle_hit',
            'internal critical target is not recognized by the descriptor')
    return None


def _vehicle_layer_critical(material, crew_instances, index):
    try:
        extra = getattr(material, 'extra')
    except AttributeError:
        extra = None
    except Exception:
        raise OracleUnavailable(
            'invalid_vehicle_hit',
            'collision[%d].matInfo.extra is unavailable' % index)
    if extra is None:
        return None, None, None, None
    try:
        extra_name = getattr(extra, 'name')
    except Exception:
        raise OracleUnavailable(
            'invalid_vehicle_hit',
            'collision[%d].matInfo.extra.name is unavailable' % index)
    extra_name = _optional_native_text(
        extra_name, 'collision[%d].matInfo.extra.name' % index)
    if extra_name is None:
        raise OracleUnavailable(
            'invalid_vehicle_hit',
            'collision[%d].matInfo.extra.name is unavailable' % index)
    target = _critical_target_from_extra_name(
        extra_name, crew_instances, required=False)
    if target is None:
        return extra_name, None, None, None
    projectile_chance = _bounded_finite(
        _native_attribute(
            material, 'chanceToHitByProjectile',
            'collision[%d].matInfo.chanceToHitByProjectile' % index),
        0.0, 1.0,
        'collision[%d].matInfo.chanceToHitByProjectile' % index)
    explosion_chance = _bounded_finite(
        _native_attribute(
            material, 'chanceToHitByExplosion',
            'collision[%d].matInfo.chanceToHitByExplosion' % index),
        0.0, 1.0,
        'collision[%d].matInfo.chanceToHitByExplosion' % index)
    return extra_name, target, projectile_chance, explosion_chance


def _explosion_layer_critical(material, crew_instances, index):
    try:
        extra = getattr(material, 'extra')
    except AttributeError:
        extra = None
    except Exception:
        raise OracleUnavailable(
            'invalid_vehicle_hit',
            'collision[%d].matInfo.extra is unavailable' % index)
    if extra is None:
        return None, None, None
    try:
        extra_name = getattr(extra, 'name')
    except Exception:
        raise OracleUnavailable(
            'invalid_vehicle_hit',
            'collision[%d].matInfo.extra.name is unavailable' % index)
    extra_name = _optional_native_text(
        extra_name, 'collision[%d].matInfo.extra.name' % index)
    if extra_name is None:
        raise OracleUnavailable(
            'invalid_vehicle_hit',
            'collision[%d].matInfo.extra.name is unavailable' % index)
    target = _critical_target_from_extra_name(
        extra_name, crew_instances, required=False)
    if target is None:
        return extra_name, None, None
    explosion_chance = _bounded_finite(
        _native_attribute(
            material, 'chanceToHitByExplosion',
            'collision[%d].matInfo.chanceToHitByExplosion' % index),
        0.0, 1.0,
        'collision[%d].matInfo.chanceToHitByExplosion' % index)
    return extra_name, target, explosion_chance


def _protocol_vec(value):
    if not isinstance(value, dict) or set(value) != set(('x', 'y', 'z')):
        raise OracleUnavailable('invalid_vector', 'query vector shape is invalid')
    return (_finite(value['x']), _finite(value['y']), _finite(value['z']))


def _native_vec(value):
    if value is None:
        raise OracleUnavailable('invalid_native_vector', 'native vector is missing')
    try:
        return (_finite(value.x), _finite(value.y), _finite(value.z))
    except AttributeError:
        pass
    try:
        if len(value) != 3:
            raise OracleUnavailable(
                'invalid_native_vector', 'native vector has wrong size')
        return (_finite(value[0]), _finite(value[1]), _finite(value[2]))
    except (TypeError, IndexError):
        raise OracleUnavailable(
            'invalid_native_vector', 'native vector shape is invalid')


def _wire_vec(value):
    vector = _native_vec(value)
    return {'x': vector[0], 'y': vector[1], 'z': vector[2]}


def _length(first, second):
    return math.sqrt(sum(
        (float(second[index]) - float(first[index])) ** 2
        for index in range(3)))


def _lerp(first, second, fraction):
    value = max(0.0, min(1.0, float(fraction)))
    return tuple(
        float(first[index]) +
        (float(second[index]) - float(first[index])) * value
        for index in range(3))


def _normalised(value, fallback=(0.0, 1.0, 0.0)):
    vector = _native_vec(value)
    length = math.sqrt(sum(component * component for component in vector))
    if length <= 1.0e-12:
        return tuple(float(component) for component in fallback)
    return tuple(component / length for component in vector)


def _entity_ref(value):
    if not isinstance(value, dict) or set(value) != set((
            'entity_id', 'generation')):
        raise OracleProtocolError('invalid entity reference')
    entity_id = _exact_int(value.get('entity_id'), 1, MAX_I64)
    generation = _exact_int(value.get('generation'), 1, MAX_U64)
    if entity_id is None or generation is None:
        raise OracleProtocolError('invalid entity reference')
    return {'entity_id': entity_id, 'generation': generation}


def _exact_fields(value, fields, code, message):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise OracleUnavailable(code, message)
    return value


def _destructible_number(value, minimum, maximum, field,
                         code='invalid_destructible_evidence'):
    if isinstance(value, bool):
        raise OracleUnavailable(code, '%s is not numeric' % field)
    try:
        result = _finite(value)
    except OracleUnavailable:
        raise OracleUnavailable(code, '%s is not finite' % field)
    if result < minimum or result > maximum:
        raise OracleUnavailable(code, '%s is outside bounds' % field)
    return result


def _destructible_request_vector(value, field):
    if (isinstance(value, dict) and
            any(isinstance(value.get(axis), bool)
                for axis in ('x', 'y', 'z'))):
        raise OracleUnavailable(
            'invalid_arguments', '%s is not an exact vector' % field)
    try:
        vector = _protocol_vec(value)
    except OracleUnavailable:
        raise OracleUnavailable(
            'invalid_arguments', '%s is not an exact vector' % field)
    if any(abs(component) > MAX_DESTRUCTIBLE_WORLD_COORDINATE_M
           for component in vector):
        raise OracleUnavailable(
            'invalid_arguments', '%s is outside world bounds' % field)
    return vector


def _destructible_wire_vector(value, field,
                              maximum=MAX_DESTRUCTIBLE_WORLD_COORDINATE_M):
    raw_components = None
    try:
        raw_components = (value.x, value.y, value.z)
    except AttributeError:
        try:
            raw_components = (value[0], value[1], value[2])
        except (KeyError, TypeError, IndexError):
            pass
    if (raw_components is not None and
            any(isinstance(component, bool)
                for component in raw_components)):
        raise OracleUnavailable(
            'invalid_destructible_evidence',
            '%s is not an exact native vector' % field)
    try:
        vector = _native_vec(value)
    except OracleUnavailable:
        raise OracleUnavailable(
            'invalid_destructible_evidence',
            '%s is not an exact native vector' % field)
    if any(abs(component) > maximum for component in vector):
        raise OracleUnavailable(
            'invalid_destructible_evidence',
            '%s is outside evidence bounds' % field)
    return {'x': vector[0], 'y': vector[1], 'z': vector[2]}


def _native_member(value, name, field):
    if isinstance(value, dict):
        if name not in value:
            raise OracleUnavailable(
                'destructible_entity_unavailable',
                '%s is unavailable' % field)
        result = value[name]
    else:
        try:
            result = getattr(value, name)
        except Exception:
            raise OracleUnavailable(
                'destructible_entity_unavailable',
                '%s is unavailable' % field)
    if result is None:
        raise OracleUnavailable(
            'destructible_entity_unavailable',
            '%s is unavailable' % field)
    return result


def _json_payload(message):
    encoded = (json.dumps(
        message, separators=(',', ':'), sort_keys=True,
        allow_nan=False) + '\n').encode('utf-8')
    if len(encoded) > MAX_LINE_BYTES:
        raise OracleProtocolError('encoded message exceeds line limit')
    return encoded


class NativeWorldOracle(object):
    """Read-only adapter over the exact loaded #1513 native space."""

    def __init__(self, bigworld, vector_factory=None,
                 matrix_factory=None, space_id_provider=None,
                 entity_resolver=None, internal_ray_hits=None,
                 foliage_provider=None, destructibles_sensor=None,
                 ram_contact_collider=None,
                 explosion_frozen_target_provider=None,
                 internal_cone_hits=None):
        self.bigworld = bigworld
        self._vector_factory = vector_factory
        self._matrix_factory = matrix_factory
        self._space_id_provider = space_id_provider
        self._entity_resolver = entity_resolver
        self._internal_ray_hits = internal_ray_hits
        self._foliage_provider = foliage_provider
        self._destructibles_sensor = destructibles_sensor
        self._ram_contact_collider = ram_contact_collider
        self._explosion_frozen_target_provider = \
            explosion_frozen_target_provider
        self._internal_cone_hits = internal_cone_hits
        self._observed_entities = {}

    def reset_generation(self):
        self._observed_entities = {}

    def execute(self, query):
        operation = query.get('operation')
        if not isinstance(operation, dict):
            raise OracleUnavailable(
                'invalid_operation', 'query operation is not an object')
        name = operation.get('operation')
        arguments = operation.get('arguments')
        if not isinstance(name, _TEXT_TYPES):
            raise OracleUnavailable(
                'invalid_operation', 'query operation name is missing')
        if not isinstance(arguments, dict):
            raise OracleUnavailable(
                'invalid_arguments', 'query operation arguments are missing')
        handlers = {
            'ground_sample': self._ground_sample,
            'ground_sample_batch': self._ground_sample_batch,
            'water_sample': self._water_sample,
            'water_sample_batch': self._water_sample_batch,
            'segment_cast': self._segment_cast,
            'segment_cast_batch': self._segment_cast_batch,
            'vehicle_hit_test': self._vehicle_hit_test,
            'explosion_evidence': self._explosion_evidence,
            'node_transform': self._node_transform,
            'player_muzzle_evidence': self._player_muzzle_evidence,
            'spotting_evidence': self._spotting_evidence,
            'firing_lane_evidence': self._firing_lane_evidence,
            'ram_contact_armor_evidence':
                self._ram_contact_armor_evidence,
            'destructible_shot_evidence':
                self._destructible_shot_evidence,
            'destructible_hull_evidence':
                self._destructible_hull_evidence,
        }
        handler = handlers.get(name)
        if handler is None:
            raise OracleUnavailable(
                'unknown_operation', 'native oracle operation is unknown')
        return handler(query, arguments)

    def _vector(self, value):
        vector = _native_vec(value)
        factory = self._vector_factory
        if factory is None:
            try:
                import Math
                factory = Math.Vector3
            except Exception:
                factory = None
        if factory is None:
            return vector
        try:
            return factory(vector[0], vector[1], vector[2])
        except TypeError:
            return factory(vector)

    def _space_id(self):
        provider = self._space_id_provider
        if callable(provider):
            value = provider()
        else:
            player = getattr(self.bigworld, 'player', None)
            player = player() if callable(player) else None
            value = getattr(player, 'spaceID', None)
        parsed = _exact_int(value, 1, MAX_U64)
        if parsed is None:
            raise OracleUnavailable(
                'space_unavailable', 'loaded native space is unavailable')
        return parsed

    def _destructible_context(self, query, requested_space_id):
        requested_space_id = _exact_int(
            requested_space_id, 1, MAX_I64)
        if requested_space_id is None:
            raise OracleUnavailable(
                'invalid_arguments', 'destructible space id is invalid')
        loaded_space_id = self._space_id()
        if requested_space_id != loaded_space_id:
            raise OracleUnavailable(
                'destructible_space_mismatch',
                'destructible query does not match the loaded native space')
        entity = self._resolve_entity(query['entity'])
        try:
            entity_space_id = getattr(entity, 'spaceID')
        except Exception:
            raise OracleUnavailable(
                'destructible_entity_unavailable',
                'destructible query entity has no native space')
        entity_space_id = _exact_int(entity_space_id, 1, MAX_I64)
        if entity_space_id is None or entity_space_id != loaded_space_id:
            raise OracleUnavailable(
                'destructible_space_mismatch',
                'destructible query entity is outside the loaded native space')
        return entity, loaded_space_id

    def _loaded_destructibles_sensor(self):
        sensor = self._destructibles_sensor
        if sensor is None:
            try:
                from gui.mods.offline_lan_0922 import destructibles_sensor
                sensor = destructibles_sensor
            except Exception:
                sensor = None
        if sensor is None:
            raise OracleUnavailable(
                'destructible_evidence_unavailable',
                'native destructible evidence sensor is unavailable')
        return sensor

    @staticmethod
    def _destructible_control(raw, fields, candidate_limit, label,
                              destroyed_field=True):
        _exact_fields(
            raw, fields, 'invalid_destructible_evidence',
            '%s evidence fields are invalid' % label)
        complete = raw.get('complete')
        fail_closed = raw.get('fail_closed')
        ambiguous = raw.get('ambiguous')
        if (not isinstance(complete, bool) or
                not isinstance(fail_closed, bool) or
                not isinstance(ambiguous, bool)):
            raise OracleUnavailable(
                'invalid_destructible_evidence',
                '%s evidence control flags are invalid' % label)
        reasons = raw.get('reasons')
        if (not isinstance(reasons, (list, tuple)) or
                len(reasons) > MAX_DESTRUCTIBLE_REASON_COUNT):
            raise OracleUnavailable(
                'invalid_destructible_evidence',
                '%s evidence reasons are invalid' % label)
        for reason in reasons:
            if (not isinstance(reason, _TEXT_TYPES) or not reason or
                    len(reason.encode('utf-8')) >
                    MAX_DESTRUCTIBLE_REASON_BYTES or
                    any(ord(character) < 32 or
                        127 <= ord(character) <= 159
                        for character in reason)):
                raise OracleUnavailable(
                    'invalid_destructible_evidence',
                    '%s evidence reason is invalid' % label)
        candidates = raw.get('candidates')
        if (not isinstance(candidates, (list, tuple)) or
                len(candidates) > candidate_limit):
            raise OracleUnavailable(
                'invalid_destructible_evidence',
                '%s evidence candidate count is invalid' % label)
        destroyed_skipped = 0
        if destroyed_field:
            destroyed_skipped = _exact_int(
                raw.get('destroyed_skipped'), 0, MAX_DESTRUCTIBLE_SKIPPED)
            if destroyed_skipped is None:
                raise OracleUnavailable(
                    'invalid_destructible_evidence',
                    '%s destroyed count is invalid' % label)
        if (not complete or fail_closed or ambiguous or reasons):
            raise OracleUnavailable(
                'destructible_evidence_unavailable',
                '%s evidence failed closed' % label)
        return candidates, destroyed_skipped

    @staticmethod
    def _destructible_candidate_key(raw, label):
        chunk_id = _exact_int(raw.get('chunk_id'), 0, MAX_I64)
        item_index = _exact_int(raw.get('item_index'), 0, MAX_I64)
        raw_mat_kind = raw.get('mat_kind')
        mat_kind = raw_mat_kind
        if raw_mat_kind is not None:
            mat_kind = _exact_int(raw_mat_kind, 71, 130)
        kind = raw.get('kind')
        if (chunk_id is None or item_index is None or
                kind not in DESTRUCTIBLE_KINDS or
                (raw_mat_kind is not None and mat_kind is None) or
                (kind == 'structure') != (mat_kind is not None)):
            raise OracleUnavailable(
                'invalid_destructible_evidence',
                '%s candidate identity is invalid' % label)
        return chunk_id, item_index, mat_kind, kind

    @staticmethod
    def _destructible_shot_value(raw, start, end, shell_kind):
        candidates, destroyed_skipped = \
            NativeWorldOracle._destructible_control(
                raw, (
                    'complete', 'fail_closed', 'ambiguous', 'reasons',
                    'candidates', 'destroyed_skipped',
                    'uncertain_distance', 'static_collision'),
                MAX_DESTRUCTIBLE_CANDIDATES, 'destructible shot')
        if raw.get('uncertain_distance') is not None:
            raise OracleUnavailable(
                'invalid_destructible_evidence',
                'complete destructible shot evidence is uncertain')
        segment_length = _length(start, end)
        rows = []
        seen = set()
        previous_entry = None
        for index, candidate in enumerate(candidates):
            _exact_fields(
                candidate, (
                    'chunk_id', 'item_index', 'mat_kind', 'kind',
                    'entry_distance', 'exit_distance', 'impact_point',
                    'item_scale', 'scaled_health', 'ap_through',
                    'piercing_loss', 'ambiguous'),
                'invalid_destructible_evidence',
                'destructible shot candidate fields are invalid')
            chunk_id, item_index, mat_kind, kind = \
                NativeWorldOracle._destructible_candidate_key(
                    candidate, 'destructible shot')
            key = (chunk_id, item_index, mat_kind)
            if key in seen:
                raise OracleUnavailable(
                    'invalid_destructible_evidence',
                    'destructible shot candidate is duplicated')
            seen.add(key)
            entry = _destructible_number(
                candidate.get('entry_distance'), 0.0, segment_length,
                'destructible shot entry')
            exit_distance = _destructible_number(
                candidate.get('exit_distance'), entry, segment_length,
                'destructible shot exit')
            if (previous_entry is not None and
                    (entry < previous_entry or
                     abs(entry - previous_entry) <=
                     DESTRUCTIBLE_AMBIGUITY_EPSILON_M)):
                raise OracleUnavailable(
                    'invalid_destructible_evidence',
                    'destructible shot candidate ordering is ambiguous')
            previous_entry = entry
            impact = _destructible_wire_vector(
                candidate.get('impact_point'),
                'destructible shot impact')
            impact_native = (
                impact['x'], impact['y'], impact['z'])
            expected_impact = _lerp(
                start, end, entry / segment_length)
            if _length(expected_impact, impact_native) > \
                    DESTRUCTIBLE_POINT_EPSILON_M:
                raise OracleUnavailable(
                    'invalid_destructible_evidence',
                    'destructible shot impact disagrees with its entry')
            item_scale = _destructible_number(
                candidate.get('item_scale'), 1.0e-9,
                MAX_DESTRUCTIBLE_ITEM_SCALE,
                'destructible item scale')
            scaled_health = _destructible_number(
                candidate.get('scaled_health'), 0.0,
                MAX_VEHICLE_DAMAGE_FACTOR,
                'destructible scaled health')
            ap_through = candidate.get('ap_through')
            ambiguous = candidate.get('ambiguous')
            if not isinstance(ap_through, bool) or ambiguous is not False:
                raise OracleUnavailable(
                    'invalid_destructible_evidence',
                    'destructible shot verdict flags are invalid')
            expected_ap_through = bool(
                shell_kind in DESTRUCTIBLE_AP_SHELL_KINDS and
                scaled_health <= DESTRUCTIBLE_AP_THROUGH_MAX_HP)
            piercing_loss = _destructible_number(
                candidate.get('piercing_loss'), 0.0,
                MAX_VEHICLE_HIT_ARMOR_MM,
                'destructible piercing loss')
            expected_loss = (DESTRUCTIBLE_AP_PIERCING_LOSS_MM
                             if expected_ap_through else 0.0)
            if (ap_through != expected_ap_through or
                    abs(piercing_loss - expected_loss) > 1.0e-6):
                raise OracleUnavailable(
                    'invalid_destructible_evidence',
                    'destructible shot penetration verdict is invalid')
            rows.append({
                'chunk_id': chunk_id,
                'item_index': item_index,
                'mat_kind': mat_kind,
                'kind': kind,
                'entry_distance': entry,
                'exit_distance': exit_distance,
                'impact_position': impact,
                'item_scale': item_scale,
                'scaled_health': scaled_health,
                'ap_through': ap_through,
                'piercing_loss': piercing_loss,
            })

        static_collision = raw.get('static_collision')
        static_wire = None
        if static_collision is not None:
            _exact_fields(
                static_collision, ('distance', 'point', 'normal'),
                'invalid_destructible_evidence',
                'destructible static collision fields are invalid')
            distance = _destructible_number(
                static_collision.get('distance'), 0.0, segment_length,
                'destructible static collision distance')
            position = _destructible_wire_vector(
                static_collision.get('point'),
                'destructible static collision point')
            if abs(_length(
                    start, (position['x'], position['y'], position['z'])) -
                    distance) > DESTRUCTIBLE_POINT_EPSILON_M:
                raise OracleUnavailable(
                    'invalid_destructible_evidence',
                    'destructible static collision distance is inconsistent')
            normal = static_collision.get('normal')
            if normal is not None:
                normal = _destructible_wire_vector(
                    normal, 'destructible static collision normal', 1.0)
                normal_length = math.sqrt(
                    normal['x'] * normal['x'] +
                    normal['y'] * normal['y'] +
                    normal['z'] * normal['z'])
                if normal_length <= 1.0e-9:
                    raise OracleUnavailable(
                        'invalid_destructible_evidence',
                        'destructible static collision normal is empty')
            static_wire = {
                'distance': distance,
                'position': position,
                'normal': normal,
            }
        return {
            'candidates': rows,
            'destroyed_skipped': destroyed_skipped,
            'static_collision': static_wire,
        }

    @staticmethod
    def _destructible_hull_value(raw, frame_travel):
        candidates, unused_destroyed_skipped = \
            NativeWorldOracle._destructible_control(
                raw, (
                    'complete', 'fail_closed', 'ambiguous', 'reasons',
                    'candidates', 'frame_travel'),
                MAX_DESTRUCTIBLE_HULL_CANDIDATES, 'destructible hull',
                destroyed_field=False)
        returned_travel = _destructible_number(
            raw.get('frame_travel'), -MAX_DESTRUCTIBLE_FRAME_TRAVEL_M,
            MAX_DESTRUCTIBLE_FRAME_TRAVEL_M,
            'destructible hull frame travel')
        if abs(returned_travel - frame_travel) > 1.0e-6:
            raise OracleUnavailable(
                'invalid_destructible_evidence',
                'destructible hull frame travel changed')
        rows = []
        seen = set()
        previous_key = None
        for candidate in candidates:
            _exact_fields(
                candidate, (
                    'chunk_id', 'item_index', 'mat_kind', 'kind',
                    'obb_center',),
                'invalid_destructible_evidence',
                'destructible hull candidate fields are invalid')
            chunk_id, item_index, mat_kind, kind = \
                NativeWorldOracle._destructible_candidate_key(
                    candidate, 'destructible hull')
            key = (chunk_id, item_index,
                   -1 if mat_kind is None else mat_kind)
            if key in seen or (previous_key is not None and key < previous_key):
                raise OracleUnavailable(
                    'invalid_destructible_evidence',
                    'destructible hull candidates are not unique and ordered')
            seen.add(key)
            previous_key = key
            center = _destructible_wire_vector(
                candidate.get('obb_center'),
                'destructible hull OBB center')
            rows.append({
                'chunk_id': chunk_id,
                'item_index': item_index,
                'mat_kind': mat_kind,
                'kind': kind,
                'obb_center': center,
            })
        return {
            'candidates': rows,
            'frame_travel': returned_travel,
        }

    def _destructible_shot_evidence(self, query, arguments):
        _exact_fields(
            arguments, ('space_id', 'start', 'end', 'shell_kind'),
            'invalid_arguments',
            'destructible_shot_evidence arguments are invalid')
        unused_entity, space_id = self._destructible_context(
            query, arguments.get('space_id'))
        start = _destructible_request_vector(
            arguments.get('start'), 'destructible shot start')
        end = _destructible_request_vector(
            arguments.get('end'), 'destructible shot end')
        segment_length = _length(start, end)
        if (segment_length <= 1.0e-9 or
                segment_length > MAX_DESTRUCTIBLE_SEGMENT_M):
            raise OracleUnavailable(
                'invalid_arguments',
                'destructible shot segment is outside bounds')
        shell_kind = arguments.get('shell_kind')
        if (not isinstance(shell_kind, _TEXT_TYPES) or
                shell_kind not in DESTRUCTIBLE_SHELL_KINDS):
            raise OracleUnavailable(
                'invalid_arguments',
                'destructible shot shell kind is invalid')
        sensor = self._loaded_destructibles_sensor()
        query_sensor = getattr(
            sensor, 'shot_destructible_evidence_1513', None)
        if not callable(query_sensor):
            raise OracleUnavailable(
                'destructible_evidence_unavailable',
                'native destructible shot sensor is unavailable')
        try:
            raw = query_sensor(
                self.bigworld, space_id, self._vector(start),
                self._vector(end), str(shell_kind))
        except Exception:
            raise OracleUnavailable(
                'destructible_evidence_unavailable',
                'native destructible shot evidence query raised')
        value = self._destructible_shot_value(
            raw, start, end, str(shell_kind))
        return {'result': 'destructible_shot_evidence', 'value': value}

    def _destructible_vehicle_geometry(self, entity):
        descriptor = _native_member(
            entity, 'typeDescriptor', 'vehicle.typeDescriptor')
        hull = _native_member(
            descriptor, 'hull', 'vehicle.typeDescriptor.hull')
        hit_tester = _native_member(
            hull, 'hitTester',
            'vehicle.typeDescriptor.hull.hitTester')
        bbox = _native_member(
            hit_tester, 'bbox',
            'vehicle.typeDescriptor.hull.hitTester.bbox')
        try:
            if len(bbox) < 2:
                raise TypeError()
            minimum = _native_vec(bbox[0])
            maximum = _native_vec(bbox[1])
        except (TypeError, IndexError, OracleUnavailable):
            raise OracleUnavailable(
                'destructible_entity_unavailable',
                'native vehicle hull bbox is unavailable')
        if (any(abs(value) > MAX_DESTRUCTIBLE_HULL_COORDINATE_M
                for value in minimum + maximum) or
                any(minimum[index] >= maximum[index]
                    for index in range(3))):
            raise OracleUnavailable(
                'destructible_entity_unavailable',
                'native vehicle hull bbox is outside bounds')
        return minimum, maximum

    def _destructible_hull_evidence(self, query, arguments):
        _exact_fields(
            arguments, (
                'space_id', 'position', 'yaw', 'frame_travel'),
            'invalid_arguments',
            'destructible_hull_evidence arguments are invalid')
        entity, space_id = self._destructible_context(
            query, arguments.get('space_id'))
        position = _destructible_request_vector(
            arguments.get('position'), 'destructible hull position')
        yaw = _destructible_number(
            arguments.get('yaw'), -2.0 * math.pi, 2.0 * math.pi,
            'destructible hull yaw', 'invalid_arguments')
        frame_travel = _destructible_number(
            arguments.get('frame_travel'),
            -MAX_DESTRUCTIBLE_FRAME_TRAVEL_M,
            MAX_DESTRUCTIBLE_FRAME_TRAVEL_M,
            'destructible hull frame travel', 'invalid_arguments')
        bbox = self._destructible_vehicle_geometry(entity)
        sensor = self._loaded_destructibles_sensor()
        query_sensor = getattr(
            sensor, 'hull_destructible_evidence_1513', None)
        if not callable(query_sensor):
            raise OracleUnavailable(
                'destructible_evidence_unavailable',
                'native destructible hull sensor is unavailable')
        try:
            raw = query_sensor(
                space_id, self._vector(position), yaw, bbox,
                frame_travel)
        except Exception:
            raise OracleUnavailable(
                'destructible_evidence_unavailable',
                'native destructible hull evidence query raised')
        value = self._destructible_hull_value(raw, frame_travel)
        return {'result': 'destructible_hull_evidence', 'value': value}

    def _collide_segment(self, start, end, mask):
        collide = getattr(self.bigworld, 'wg_collideSegment', None)
        if not callable(collide):
            raise OracleUnavailable(
                'segment_unavailable', 'wg_collideSegment is unavailable')
        return collide(
            self._space_id(), self._vector(start), self._vector(end), mask)

    def _world_hit(self, start, end, collision):
        if collision is None:
            return None
        try:
            point = _native_vec(collision[0])
        except (TypeError, IndexError):
            raise OracleUnavailable(
                'invalid_world_hit', 'native segment hit has no point')
        try:
            normal = _normalised(collision[1])
        except (TypeError, IndexError, OracleUnavailable):
            raise OracleUnavailable(
                'invalid_world_hit', 'native segment hit has no normal')
        delta = tuple(float(end[index]) - float(start[index])
                      for index in range(3))
        length_squared = sum(component * component for component in delta)
        if length_squared <= 1.0e-12:
            raise OracleUnavailable(
                'invalid_segment', 'native segment has no length')
        fraction = sum(
            (point[index] - float(start[index])) * delta[index]
            for index in range(3)) / length_squared
        if fraction < -1.0e-4 or fraction > 1.0001:
            raise OracleUnavailable(
                'invalid_world_hit', 'native hit lies outside its segment')
        fraction = max(0.0, min(1.0, fraction))
        material_id = None
        if len(collision) > 2:
            material_id = _exact_int(collision[2], 0, 4294967295)
        return {
            'fraction': fraction,
            'position': _wire_vec(point),
            'normal': _wire_vec(normal),
            'material_id': material_id,
            'hit_entity': None,
        }

    def _ground_at(self, position):
        x, y, z = position
        start = (x, y + WORLD_PROBE_HEIGHT, z)
        end = (x, y - WORLD_PROBE_HEIGHT, z)
        collision = self._collide_segment(
            start, end, WORLD_COLLISION_MASK)
        if collision is None:
            return None
        hit = self._world_hit(start, end, collision)
        return {
            'height': hit['position']['y'],
            'normal': hit['normal'],
            'material_id': hit['material_id'],
        }

    def _ground_sample(self, unused_query, arguments):
        if set(arguments) != set(('position',)):
            raise OracleUnavailable(
                'invalid_arguments', 'ground_sample arguments are invalid')
        sample = self._ground_at(_protocol_vec(arguments['position']))
        return {'result': 'ground_sample', 'value': {'sample': sample}}

    def _ground_sample_batch(self, unused_query, arguments):
        if set(arguments) != set(('positions',)):
            raise OracleUnavailable(
                'invalid_arguments', 'ground_sample_batch arguments are invalid')
        positions = arguments.get('positions')
        if not isinstance(positions, list) or not positions:
            raise OracleUnavailable(
                'invalid_arguments', 'ground sample batch is empty')
        samples = [self._ground_at(_protocol_vec(value))
                   for value in positions]
        return {
            'result': 'ground_sample_batch',
            'value': {'samples': samples},
        }

    def _water_at(self, position):
        collide = getattr(self.bigworld, 'wg_collideWater', None)
        if not callable(collide):
            raise OracleUnavailable(
                'water_unavailable', 'wg_collideWater is unavailable')
        x, y, z = position
        start = (x, y + WORLD_PROBE_HEIGHT, z)
        end = (x, y - WORLD_PROBE_HEIGHT, z)
        value = collide(self._vector(start), self._vector(end), False)
        if value is None:
            return None
        if isinstance(value, _INTEGER_TYPES + (float,)):
            distance = _finite(value)
            if distance < 0.0:
                return None
            return start[1] - distance
        try:
            return _native_vec(value)[1]
        except OracleUnavailable:
            try:
                return _native_vec(value[0])[1]
            except (TypeError, IndexError):
                raise OracleUnavailable(
                    'invalid_water_hit', 'native water hit is invalid')

    def _water_sample(self, unused_query, arguments):
        if set(arguments) != set(('position',)):
            raise OracleUnavailable(
                'invalid_arguments', 'water_sample arguments are invalid')
        height = self._water_at(_protocol_vec(arguments['position']))
        return {'result': 'water_sample', 'value': {'height': height}}

    def _water_sample_batch(self, unused_query, arguments):
        if set(arguments) != set(('positions',)):
            raise OracleUnavailable(
                'invalid_arguments', 'water_sample_batch arguments are invalid')
        positions = arguments.get('positions')
        if not isinstance(positions, list) or not positions:
            raise OracleUnavailable(
                'invalid_arguments', 'water sample batch is empty')
        heights = [self._water_at(_protocol_vec(value))
                   for value in positions]
        return {
            'result': 'water_sample_batch',
            'value': {'heights': heights},
        }

    @staticmethod
    def _segment_arguments(arguments):
        if set(arguments) != set(('start', 'end', 'collision_mask')):
            raise OracleUnavailable(
                'invalid_arguments', 'segment_cast arguments are invalid')
        start = _protocol_vec(arguments['start'])
        end = _protocol_vec(arguments['end'])
        if _length(start, end) <= 1.0e-9:
            raise OracleUnavailable(
                'invalid_segment', 'segment_cast has no length')
        mask = _exact_int(arguments.get('collision_mask'), 0, 4294967295)
        if mask is None:
            raise OracleUnavailable(
                'invalid_arguments', 'segment collision mask is invalid')
        return start, end, mask

    def _segment_at(self, arguments):
        start, end, mask = self._segment_arguments(arguments)
        collision = self._collide_segment(start, end, mask)
        return self._world_hit(start, end, collision)

    def _segment_cast(self, unused_query, arguments):
        hit = self._segment_at(arguments)
        return {'result': 'segment_cast', 'value': {'hit': hit}}

    def _segment_cast_batch(self, unused_query, arguments):
        if set(arguments) != set(('segments',)):
            raise OracleUnavailable(
                'invalid_arguments', 'segment_cast_batch arguments are invalid')
        segments = arguments.get('segments')
        if not isinstance(segments, list) or not segments:
            raise OracleUnavailable(
                'invalid_arguments', 'segment cast batch is empty')
        hits = [self._segment_at(value) for value in segments]
        return {
            'result': 'segment_cast_batch',
            'value': {'hits': hits},
        }

    def _resolve_entity(self, entity_ref):
        resolver = self._entity_resolver
        if callable(resolver):
            resolved = resolver(dict(entity_ref))
            if isinstance(resolved, tuple) and len(resolved) == 2:
                entity, generation = resolved
                if _exact_int(generation, 1, MAX_U64) != \
                        entity_ref['generation']:
                    raise OracleUnavailable(
                        'stale_entity', 'native entity generation is stale')
                if entity is None:
                    raise OracleUnavailable(
                        'entity_unavailable', 'native entity is unavailable')
                return entity
            entity = resolved
        else:
            lookup = getattr(self.bigworld, 'entity', None)
            entity = lookup(entity_ref['entity_id']) \
                if callable(lookup) else None
            if entity is None:
                entities = getattr(self.bigworld, 'entities', None)
                if isinstance(entities, dict):
                    entity = entities.get(entity_ref['entity_id'])
        if entity is None:
            raise OracleUnavailable(
                'entity_unavailable', 'native entity is unavailable')

        entity_id = entity_ref['entity_id']
        generation = entity_ref['generation']
        observed = self._observed_entities.get(entity_id)
        if observed is None:
            self._observed_entities[entity_id] = (entity, generation)
        elif observed[0] is entity:
            if observed[1] != generation:
                raise OracleUnavailable(
                    'stale_entity', 'native entity generation is stale')
        else:
            expected = observed[1] + 1
            if generation != expected:
                raise OracleUnavailable(
                    'stale_entity', 'reused native entity has wrong generation')
            self._observed_entities[entity_id] = (entity, generation)
        return entity

    def _loaded_foliage(self):
        provider = self._foliage_provider
        if provider is not None:
            try:
                foliage = provider() if callable(provider) else provider
            except Exception:
                raise OracleUnavailable(
                    'foliage_unavailable',
                    'prebaked foliage provider raised')
        else:
            # The hidden worker passes _NativeOracleEntities.resolve. Follow
            # that bound resolver back to its already-loaded BattleRuntime so
            # this bridge reuses the exact map data instead of loading or
            # approximating vegetation independently.
            resolver = self._entity_resolver
            candidate = getattr(resolver, '__self__', None)
            if candidate is None:
                candidate = getattr(resolver, 'im_self', None)
            foliage = _MISSING
            visited = set()
            for unused in range(4):
                if candidate is None or id(candidate) in visited:
                    break
                visited.add(id(candidate))
                try:
                    foliage = getattr(candidate, '_foliage')
                    break
                except AttributeError:
                    pass
                except Exception:
                    raise OracleUnavailable(
                        'foliage_unavailable',
                        'loaded battle foliage is unavailable')
                nested = getattr(candidate, '_resolver', None)
                candidate = getattr(nested, '__self__', None)
                if candidate is None:
                    candidate = getattr(nested, 'im_self', None)
        if foliage is _MISSING or foliage is None:
            raise OracleUnavailable(
                'foliage_unavailable',
                'prebaked foliage is not loaded for this battle')
        camouflage_bonus = getattr(foliage, 'camouflage_bonus', None)
        if not callable(camouflage_bonus):
            raise OracleUnavailable(
                'foliage_unavailable',
                'prebaked foliage has no camouflage query')
        return foliage

    @staticmethod
    def _observation_arguments(query, arguments, include_recent_fire):
        fields = set((
            'observer', 'target', 'observer_position', 'target_position',
            'collision_mask'))
        if include_recent_fire:
            fields.add('evaluated_for_recent_fire')
        if set(arguments) != fields:
            raise OracleUnavailable(
                'invalid_arguments', 'observation arguments are invalid')
        observer_ref = _entity_ref(arguments.get('observer'))
        target_ref = _entity_ref(arguments.get('target'))
        if target_ref != query.get('entity'):
            raise OracleUnavailable(
                'target_mismatch',
                'observation target does not match query entity')
        if observer_ref == target_ref:
            raise OracleUnavailable(
                'entity_alias',
                'observation pair uses one native entity twice')
        observer_position = _protocol_vec(arguments['observer_position'])
        target_position = _protocol_vec(arguments['target_position'])
        mask = _exact_int(arguments.get('collision_mask'), 0, 4294967295)
        if mask is None:
            raise OracleUnavailable(
                'invalid_arguments', 'observation collision mask is invalid')
        recent_fire = None
        if include_recent_fire:
            recent_fire = arguments.get('evaluated_for_recent_fire')
            if not isinstance(recent_fire, bool):
                raise OracleUnavailable(
                    'invalid_arguments',
                    'recent-fire evidence branch is not boolean')
        return (observer_ref, target_ref, observer_position,
                target_position, mask, recent_fire)

    def _spotting_evidence(self, query, arguments):
        (observer_ref, target_ref, observer_position, target_position,
         mask, recent_fire) = self._observation_arguments(
             query, arguments, True)
        self._resolve_entity(observer_ref)
        self._resolve_entity(target_ref)
        foliage = self._loaded_foliage()

        start = (observer_position[0], observer_position[1] + 2.0,
                 observer_position[2])
        end = (target_position[0], target_position[1] + 1.5,
               target_position[2])
        collision = self._collide_segment(start, end, mask)
        if collision is None:
            line_of_sight = True
        else:
            try:
                hit_position = _native_vec(collision[0])
            except (TypeError, IndexError):
                raise OracleUnavailable(
                    'invalid_world_hit',
                    'spotting ray hit has no native point')
            line_of_sight = bool(
                _length(start, hit_position) + 1.5 >= _length(start, end))

        foliage_bonus = 0.0
        if line_of_sight:
            try:
                foliage_bonus = _finite(foliage.camouflage_bonus(
                    observer_position, target_position, recent_fire))
            except OracleUnavailable:
                raise
            except Exception:
                raise OracleUnavailable(
                    'foliage_unavailable',
                    'prebaked foliage query raised')
            if (foliage_bonus < 0.0 or
                    foliage_bonus > MAX_FOLIAGE_CAMOUFLAGE_BONUS):
                raise OracleUnavailable(
                    'foliage_unavailable',
                    'prebaked foliage bonus is outside spotting bounds')
        return {
            'result': 'spotting_evidence',
            'value': {
                'line_of_sight': line_of_sight,
                'foliage_bonus': foliage_bonus,
                'evaluated_for_recent_fire': recent_fire,
            },
        }

    @staticmethod
    def _is_spg(entity):
        try:
            tags = entity.typeDescriptor.type.tags
        except Exception:
            raise OracleUnavailable(
                'firing_lane_unavailable',
                'observer vehicle class is unavailable')
        try:
            return 'SPG' in tags
        except Exception:
            raise OracleUnavailable(
                'firing_lane_unavailable',
                'observer vehicle class tags are invalid')

    @staticmethod
    def _trimmed_firing_segment(observer, target, target_height,
                                clearance):
        dx = target[0] - observer[0]
        dz = target[2] - observer[2]
        distance = math.sqrt(dx * dx + dz * dz)
        if distance <= clearance + clearance + 0.5:
            return None
        unit_x = dx / distance
        unit_z = dz / distance
        return (
            (observer[0] + unit_x * clearance, observer[1] + 2.5,
             observer[2] + unit_z * clearance),
            (target[0] - unit_x * clearance,
             target[1] + target_height,
             target[2] - unit_z * clearance),
        )

    def _firing_lane_evidence(self, query, arguments):
        (observer_ref, target_ref, observer_position, target_position,
         mask, unused_recent_fire) = self._observation_arguments(
             query, arguments, False)
        observer = self._resolve_entity(observer_ref)
        self._resolve_entity(target_ref)
        if self._is_spg(observer):
            # The legacy SPG lane is an artillery-solution request, not a
            # direct visibility ray. Until that subsystem has its own wire
            # contract, returning unavailable is the only truthful result.
            raise OracleUnavailable(
                'firing_lane_unavailable',
                'SPG artillery firing lane is not available in oracle-v1')
        dx = target_position[0] - observer_position[0]
        dz = target_position[2] - observer_position[2]
        distance = math.sqrt(dx * dx + dz * dz)
        clearance = min(4.0, max(0.0, (distance - 0.75) * 0.5))
        clear = False
        for target_height in (1.5, 2.2):
            segment = self._trimmed_firing_segment(
                observer_position, target_position,
                target_height, clearance)
            if not segment:
                clear = False
                break
            if self._collide_segment(segment[0], segment[1], mask) is None:
                clear = True
                break
        return {
            'result': 'firing_lane_evidence',
            'value': {'clear': clear},
        }

    @staticmethod
    def _ram_request_vector(value, field):
        if (not isinstance(value, dict) or
                set(value) != set(('x', 'y', 'z')) or
                any(isinstance(value.get(axis), bool)
                    for axis in ('x', 'y', 'z'))):
            raise OracleUnavailable(
                'invalid_arguments', '%s is not an exact vector' % field)
        vector = _protocol_vec(value)
        if any(abs(component) > MAX_RAM_CONTACT_COORDINATE_M
               for component in vector):
            raise OracleUnavailable(
                'invalid_arguments', '%s is outside ram contact bounds' %
                field)
        return vector

    @classmethod
    def _ram_request_pose(cls, value, field):
        _exact_fields(
            value, (
                'position', 'yaw', 'pitch', 'roll', 'turret_yaw',
                'gun_pitch', 'siege_state'),
            'invalid_arguments', '%s fields are invalid' % field)
        position = cls._ram_request_vector(
            value.get('position'), '%s.position' % field)
        pose = {'position': _wire_vec(position)}
        for name in ('yaw', 'pitch', 'roll', 'turret_yaw', 'gun_pitch'):
            raw = value.get(name)
            if isinstance(raw, bool):
                raise OracleUnavailable(
                    'invalid_arguments', '%s.%s is not numeric' %
                    (field, name))
            try:
                angle = _finite(raw)
            except OracleUnavailable:
                raise OracleUnavailable(
                    'invalid_arguments', '%s.%s is not finite' %
                    (field, name))
            if abs(angle) > MAX_RAM_CONTACT_POSE_ANGLE_RAD:
                raise OracleUnavailable(
                    'invalid_arguments', '%s.%s is outside bounds' %
                    (field, name))
            pose[name] = angle
        siege_state = _exact_int(value.get('siege_state'), 0, 3)
        if siege_state is None:
            raise OracleUnavailable(
                'invalid_arguments', '%s.siege_state is invalid' % field)
        pose['siege_state'] = siege_state
        return pose

    def _ram_pose_matrix(self, pose):
        factory = self._matrix_factory
        if factory is None:
            try:
                import Math
                factory = Math.Matrix
            except Exception:
                factory = None
        if factory is None:
            raise OracleUnavailable(
                'ram_contact_armor_unavailable',
                'native matrix factory is unavailable')
        try:
            matrix = factory()
            rotate = getattr(matrix, 'setRotateYPR')
            rotate((pose['yaw'], pose['pitch'], pose['roll']))
            matrix.translation = self._vector(_protocol_vec(
                pose['position']))
        except Exception:
            raise OracleUnavailable(
                'ram_contact_armor_unavailable',
                'frozen native vehicle pose is unavailable')
        return matrix

    def _ram_structural_armor(self, entity, pose, hit_point,
                              inward_normal):
        center = _protocol_vec(pose['position'])
        center_depth = sum(
            (center[index] - hit_point[index]) * inward_normal[index]
            for index in range(3))
        if center_depth < -1.0e-6:
            raise OracleUnavailable(
                'ram_contact_armor_unavailable',
                'contact normal points away from native vehicle center')
        center_depth = max(0.0, center_depth)
        # The ray is vehicle-local and generation-fenced, so a conservative
        # fixed extension avoids consulting a T+3 live descriptor merely to
        # choose its start point. Stop on the contacted face's center plane;
        # continuing through the far half could select an unrelated plate.
        # The exact frozen provider still owns every component transform and
        # selected siege descriptor below.
        reach = MAX_RAM_CONTACT_POSE_DISTANCE_M
        start = tuple(
            hit_point[index] - inward_normal[index] * reach
            for index in range(3))
        end = tuple(
            hit_point[index] + inward_normal[index] * center_depth
            for index in range(3))
        start_native = self._vector(start)
        end_native = self._vector(end)
        provider = self._ram_contact_collider
        try:
            if provider is not None:
                pose_matrix = self._ram_pose_matrix(pose)
                collider_pose = dict(pose)
                collider_pose['position'] = dict(pose['position'])
                collisions = provider(
                    entity, collider_pose, pose_matrix, start_native,
                    end_native, pose_matrix)
            else:
                frozen_provider = \
                    self._loaded_explosion_frozen_target_provider()
                provider_pose = dict(pose)
                provider_pose['position'] = dict(pose['position'])
                frozen = frozen_provider(
                    entity, provider_pose, start_native, end_native)
                if not isinstance(frozen, tuple) or len(frozen) != 3:
                    raise OracleUnavailable(
                        'ram_contact_armor_unavailable',
                        'frozen ram provider returned no complete target')
                frozen_target, descriptor, collisions = frozen
                if frozen_target is None or descriptor is None:
                    raise OracleUnavailable(
                        'ram_contact_armor_unavailable',
                        'frozen ram component matrices are unavailable')
                if bool(getattr(
                        descriptor, 'isPitchHullAimingAvailable', False)):
                    raise OracleUnavailable(
                        'ram_contact_armor_unavailable',
                        'frozen ram body-ground transform is unavailable')
        except OracleUnavailable as error:
            if error.code == 'ram_contact_armor_unavailable':
                raise
            raise OracleUnavailable(
                'ram_contact_armor_unavailable',
                'frozen ram contact provider is unavailable')
        except Exception:
            raise OracleUnavailable(
                'ram_contact_armor_unavailable',
                'native ram contact ray failed')
        try:
            collisions = tuple(collisions or ())
        except TypeError:
            raise OracleUnavailable(
                'ram_contact_armor_unavailable',
                'native ram contact ray result is invalid')
        if len(collisions) > MAX_VEHICLE_HIT_LAYERS:
            raise OracleUnavailable(
                'ram_contact_armor_unavailable',
                'native ram contact layer capacity exceeded')
        ordered = []
        for collision in collisions:
            try:
                distance = _finite(getattr(collision, 'dist'))
            except Exception:
                raise OracleUnavailable(
                    'ram_contact_armor_unavailable',
                    'native ram contact layer distance is invalid')
            if distance < 0.0 or distance > MAX_VEHICLE_HIT_DISTANCE_M:
                raise OracleUnavailable(
                    'ram_contact_armor_unavailable',
                    'native ram contact layer distance is outside bounds')
            ordered.append((distance, collision))
        ordered.sort(key=lambda item: item[0])
        for unused_distance, collision in ordered:
            material = getattr(collision, 'matInfo', None)
            if material is None:
                raise OracleUnavailable(
                    'ram_contact_armor_unavailable',
                    'native ram contact material is unavailable')
            try:
                armor = _finite(getattr(material, 'armor'))
                damage_factor = _finite(getattr(
                    material, 'vehicleDamageFactor'))
            except Exception:
                raise OracleUnavailable(
                    'ram_contact_armor_unavailable',
                    'native ram contact material is invalid')
            if (armor > 0.0 and armor <= MAX_VEHICLE_HIT_ARMOR_MM and
                    damage_factor > 0.0 and
                    damage_factor <= MAX_VEHICLE_DAMAGE_FACTOR):
                return armor
        raise OracleUnavailable(
            'ram_contact_armor_unavailable',
            'native ram contact found no structural armour')

    def _ram_contact_armor_evidence(self, query, arguments):
        _exact_fields(
            arguments, (
                'first', 'second', 'first_pose', 'second_pose',
                'contact_point', 'contact_normal'),
            'invalid_arguments',
            'ram contact armor arguments are invalid')
        first_ref = _entity_ref(arguments.get('first'))
        second_ref = _entity_ref(arguments.get('second'))
        if first_ref != query.get('entity'):
            raise OracleUnavailable(
                'target_mismatch',
                'ram contact first entity does not match query entity')
        if first_ref == second_ref:
            raise OracleUnavailable(
                'entity_alias',
                'ram contact pair uses one native entity twice')
        first_pose = self._ram_request_pose(
            arguments.get('first_pose'), 'first_pose')
        second_pose = self._ram_request_pose(
            arguments.get('second_pose'), 'second_pose')
        hit_point = self._ram_request_vector(
            arguments.get('contact_point'), 'contact_point')
        normal = self._ram_request_vector(
            arguments.get('contact_normal'), 'contact_normal')
        normal_length = math.sqrt(sum(
            component * component for component in normal))
        if abs(normal_length - 1.0) > RAM_CONTACT_NORMAL_TOLERANCE:
            raise OracleUnavailable(
                'invalid_arguments',
                'ram contact normal is not a unit vector')
        for label, pose in (('first', first_pose), ('second', second_pose)):
            if _length(
                    _protocol_vec(pose['position']),
                    hit_point) > MAX_RAM_CONTACT_POSE_DISTANCE_M:
                raise OracleUnavailable(
                    'invalid_arguments',
                    '%s ram contact point is outside pose bounds' % label)
        center_delta = tuple(
            _protocol_vec(first_pose['position'])[index] -
            _protocol_vec(second_pose['position'])[index]
            for index in range(3))
        if sum(center_delta[index] * normal[index]
               for index in range(3)) < -RAM_CONTACT_NORMAL_TOLERANCE:
            raise OracleUnavailable(
                'invalid_arguments',
                'ram contact normal is not canonical second-to-first')

        # Resolve both generation-fenced native entities before probing either
        # side. A missing plate on either ray makes the atomic result
        # unavailable; no one-sided armour evidence can escape.
        first_entity = self._resolve_entity(first_ref)
        second_entity = self._resolve_entity(second_ref)
        first_armor = self._ram_structural_armor(
            first_entity, first_pose, hit_point, normal)
        second_armor = self._ram_structural_armor(
            second_entity, second_pose, hit_point,
            tuple(-component for component in normal))
        return {
            'result': 'ram_contact_armor_evidence',
            'value': {
                'first_armor_mm': first_armor,
                'second_armor_mm': second_armor,
            },
        }

    def _internal_critical_hits(self, entity, descriptor, start, end,
                                covered, crew_instances, length):
        provider = self._internal_ray_hits
        if provider is None:
            try:
                from gui.mods.offline_lan_0922 import critical_damage
                provider = critical_damage._offh_internal_ray_hits
            except Exception:
                return None
        try:
            raw_hits = provider(
                entity, descriptor, self._vector(start), self._vector(end),
                tuple(sorted(covered)))
        except Exception:
            # The critical rules distinguish unavailable validated layout from
            # a validated layout whose ray crossed no target.
            return None
        if raw_hits is None:
            return None
        try:
            raw_hits = tuple(raw_hits)
        except TypeError:
            raise OracleUnavailable(
                'invalid_vehicle_hit', 'internal critical trace is invalid')
        if len(raw_hits) > MAX_VEHICLE_INTERNAL_HITS:
            raise OracleUnavailable(
                'invalid_vehicle_hit',
                'internal critical hit capacity exceeded')
        hits = []
        for index, raw_hit in enumerate(raw_hits):
            try:
                if len(raw_hit) != 2:
                    raise TypeError()
                raw_distance, raw_name = raw_hit
            except (TypeError, ValueError):
                raise OracleUnavailable(
                    'invalid_vehicle_hit',
                    'internal critical hit %d is invalid' % index)
            distance = _bounded_finite(
                raw_distance, 0.0,
                min(length, MAX_VEHICLE_HIT_DISTANCE_M),
                'internal_hits[%d].distance_m' % index)
            name = _optional_native_text(
                raw_name, 'internal_hits[%d].target' % index)
            if name is None:
                raise OracleUnavailable(
                    'invalid_vehicle_hit',
                    'internal critical hit %d has no target' % index)
            if name in covered:
                raise OracleUnavailable(
                    'invalid_vehicle_hit',
                    'internal critical hit repeats a native extra')
            target = _critical_target_from_extra_name(
                name, crew_instances, required=True)
            hits.append({
                'distance_m': distance,
                'target': target,
                '_native_order': index,
                '_extra_name': name,
            })
        hits.sort(key=lambda value: (
            value['distance_m'], value['_native_order']))
        seen = set()
        wire_hits = []
        for hit in hits:
            key = (hit['target']['kind'], hit['target']['name'])
            if key in seen:
                raise OracleUnavailable(
                    'invalid_vehicle_hit',
                    'internal critical target is duplicated')
            seen.add(key)
            wire_hits.append({
                'distance_m': hit['distance_m'],
                'target': hit['target'],
            })
        return wire_hits

    def _loaded_explosion_frozen_target_provider(self):
        provider = self._explosion_frozen_target_provider
        if callable(provider):
            return provider
        # The worker resolver is generation-fenced and bound through
        # _NativeOracleEntities to BattleRuntime. Follow only that already
        # loaded chain; never fall back to a live entity matrix or collider.
        resolver = self._entity_resolver
        candidate = getattr(resolver, '__self__', None)
        if candidate is None:
            candidate = getattr(resolver, 'im_self', None)
        visited = set()
        for unused in range(4):
            if candidate is None or id(candidate) in visited:
                break
            visited.add(id(candidate))
            try:
                provider = getattr(
                    candidate, 'native_explosion_evidence_at_pose')
            except AttributeError:
                provider = None
            except Exception:
                raise OracleUnavailable(
                    'explosion_evidence_unavailable',
                    'frozen explosion provider is unavailable')
            if callable(provider):
                return provider
            nested = getattr(candidate, '_resolver', None)
            candidate = getattr(nested, '__self__', None)
            if candidate is None:
                candidate = getattr(nested, 'im_self', None)
        raise OracleUnavailable(
            'explosion_evidence_unavailable',
            'frozen explosion provider is not loaded')

    @staticmethod
    def _explosion_number(value, minimum, maximum, field):
        try:
            result = _finite(value)
        except OracleUnavailable:
            raise OracleUnavailable(
                'invalid_arguments', '%s is not finite' % field)
        if result < minimum or result > maximum:
            raise OracleUnavailable(
                'invalid_arguments', '%s is outside bounds' % field)
        return result

    @staticmethod
    def _explosion_arguments(query, arguments):
        required = set((
            'target', 'impact', 'incoming_direction', 'caliber_mm',
            'target_pose'))
        if set(arguments) != required:
            raise OracleUnavailable(
                'invalid_arguments', 'explosion evidence arguments are invalid')
        target_ref = _entity_ref(arguments.get('target'))
        if target_ref != query.get('entity'):
            raise OracleUnavailable(
                'target_mismatch',
                'explosion target does not match query entity')
        impact = _protocol_vec(arguments.get('impact'))
        direction = _protocol_vec(arguments.get('incoming_direction'))
        pose_value = arguments.get('target_pose')
        pose_fields = set((
            'position', 'yaw', 'pitch', 'roll', 'turret_yaw',
            'gun_pitch', 'siege_state'))
        if not isinstance(pose_value, dict) or set(pose_value) != pose_fields:
            raise OracleUnavailable(
                'invalid_arguments', 'explosion target pose is invalid')
        position = _protocol_vec(pose_value.get('position'))
        vectors = (impact, direction, position)
        if any(abs(component) > MAX_EXPLOSION_WORLD_COORDINATE_M
               for vector in vectors for component in vector):
            raise OracleUnavailable(
                'invalid_arguments', 'explosion vector is outside bounds')
        pose = {'position': _wire_vec(position)}
        for field in ('yaw', 'pitch', 'roll', 'turret_yaw', 'gun_pitch'):
            pose[field] = NativeWorldOracle._explosion_number(
                pose_value.get(field), -MAX_EXPLOSION_POSE_ANGLE_RAD,
                MAX_EXPLOSION_POSE_ANGLE_RAD, field)
        siege_state = _exact_int(pose_value.get('siege_state'), 0, 3)
        if siege_state is None:
            raise OracleUnavailable(
                'invalid_arguments', 'explosion siege state is invalid')
        pose['siege_state'] = siege_state
        caliber = NativeWorldOracle._explosion_number(
            arguments.get('caliber_mm'), 0.0, MAX_EXPLOSION_CALIBER_MM,
            'caliber_mm')
        if caliber <= 0.0:
            raise OracleUnavailable(
                'invalid_arguments', 'explosion caliber is invalid')
        direction_length = math.sqrt(sum(
            component * component for component in direction))
        if abs(direction_length - 1.0) > EXPLOSION_DIRECTION_TOLERANCE:
            raise OracleUnavailable(
                'invalid_arguments', 'explosion direction is not unit length')
        end = (position[0], position[1] + 1.0, position[2])
        if (any(abs(component) > MAX_EXPLOSION_WORLD_COORDINATE_M
                for component in end) or
                _length(impact, end) <= 1.0e-9 or
                _length(impact, end) > MAX_EXPLOSION_RAY_DISTANCE_M):
            raise OracleUnavailable(
                'invalid_arguments', 'explosion vehicle ray is invalid')
        return target_ref, impact, direction, caliber, pose, end

    def _explosion_internal_critical_hits(
            self, frozen_target, descriptor, impact, direction, caliber,
            covered, crew_instances):
        provider = self._internal_cone_hits
        if provider is None:
            try:
                from gui.mods.offline_lan_0922 import critical_damage
                provider = critical_damage._offh_internal_cone_hits
            except Exception:
                provider = None
        if not callable(provider):
            raise OracleUnavailable(
                'explosion_evidence_unavailable',
                'HE internal cone provider is unavailable')
        try:
            raw_hits = provider(
                frozen_target, descriptor, self._vector(impact),
                self._vector(direction), {'caliber': caliber},
                tuple(sorted(covered)))
        except Exception:
            raise OracleUnavailable(
                'explosion_evidence_unavailable',
                'HE internal cone provider raised')
        if raw_hits is None:
            return None
        try:
            raw_hits = tuple(raw_hits)
        except TypeError:
            raise OracleUnavailable(
                'invalid_vehicle_hit', 'HE internal cone trace is invalid')
        if len(raw_hits) > MAX_VEHICLE_INTERNAL_HITS:
            raise OracleUnavailable(
                'invalid_vehicle_hit',
                'HE internal cone hit capacity exceeded')
        depth = caliber / 100.0
        hits = []
        for index, raw_hit in enumerate(raw_hits):
            try:
                if len(raw_hit) != 2:
                    raise TypeError()
                raw_distance, raw_name = raw_hit
            except (TypeError, ValueError):
                raise OracleUnavailable(
                    'invalid_vehicle_hit',
                    'HE internal cone hit %d is invalid' % index)
            distance = _bounded_finite(
                raw_distance, 0.0, depth + 0.0001,
                'internal_hits[%d].distance_m' % index)
            name = _optional_native_text(
                raw_name, 'internal_hits[%d].target' % index)
            if name is None or name in covered:
                raise OracleUnavailable(
                    'invalid_vehicle_hit',
                    'HE internal cone target %d is invalid' % index)
            target = _critical_target_from_extra_name(
                name, crew_instances, required=True)
            hits.append({
                'distance_m': distance,
                'target': target,
                '_native_order': index,
            })
        hits.sort(key=lambda value: (
            value['distance_m'], value['_native_order']))
        seen = set()
        result = []
        for hit in hits:
            key = (hit['target']['kind'], hit['target']['name'])
            if key in seen:
                raise OracleUnavailable(
                    'invalid_vehicle_hit',
                    'HE internal cone target is duplicated')
            seen.add(key)
            result.append({
                'distance_m': hit['distance_m'],
                'target': hit['target'],
            })
        return result

    def _explosion_evidence(self, query, arguments):
        (target_ref, impact, direction, caliber, pose, end) = \
            self._explosion_arguments(query, arguments)
        entity = self._resolve_entity(target_ref)
        provider = self._loaded_explosion_frozen_target_provider()
        provider_pose = dict(pose)
        provider_pose['position'] = dict(pose['position'])
        try:
            frozen = provider(
                entity, provider_pose, self._vector(impact),
                self._vector(end))
        except OracleUnavailable:
            raise
        except Exception:
            raise OracleUnavailable(
                'explosion_evidence_unavailable',
                'frozen explosion provider raised')
        if not isinstance(frozen, tuple) or len(frozen) != 3:
            raise OracleUnavailable(
                'explosion_evidence_unavailable',
                'frozen explosion provider returned no complete target')
        frozen_target, descriptor, collisions = frozen
        if frozen_target is None or descriptor is None:
            raise OracleUnavailable(
                'explosion_evidence_unavailable',
                'frozen explosion component matrices are unavailable')
        if collisions is None:
            collisions = ()
        elif hasattr(collisions, 'dist'):
            collisions = (collisions,)
        try:
            collisions = tuple(collisions)
        except TypeError:
            raise OracleUnavailable(
                'invalid_vehicle_hit',
                'frozen explosion collision layers are invalid')
        if len(collisions) > MAX_VEHICLE_HIT_LAYERS:
            raise OracleUnavailable(
                'invalid_vehicle_hit',
                'frozen explosion layer capacity exceeded')

        crew_instances = _descriptor_crew_instances(descriptor)
        length = _length(impact, end)
        layers = []
        covered = set()
        external_targets = set()
        for index, collision in enumerate(collisions):
            distance = _bounded_finite(
                _native_attribute(
                    collision, 'dist', 'collision[%d].dist' % index),
                0.0, min(length, MAX_VEHICLE_HIT_DISTANCE_M),
                'collision[%d].dist' % index)
            hit_angle_cos = _bounded_finite(
                _native_attribute(
                    collision, 'hitAngleCos',
                    'collision[%d].hitAngleCos' % index),
                -1.0, 1.0, 'collision[%d].hitAngleCos' % index)
            try:
                component = getattr(collision, 'compName')
            except AttributeError:
                component = None
            except Exception:
                raise OracleUnavailable(
                    'invalid_vehicle_hit',
                    'collision[%d].compName is unavailable' % index)
            component = _optional_native_text(
                component, 'collision[%d].compName' % index)
            native_material = _native_attribute(
                collision, 'matInfo', 'collision[%d].matInfo' % index)
            material = _vehicle_hit_material(native_material)
            extra_name, critical_target, explosion = \
                _explosion_layer_critical(
                    native_material, crew_instances, index)
            if extra_name is not None:
                covered.add(extra_name)
            if critical_target is not None:
                critical_key = (
                    critical_target['kind'], critical_target['name'])
                if critical_key in external_targets:
                    raise OracleUnavailable(
                        'invalid_vehicle_hit',
                        'frozen explosion critical target is duplicated')
                external_targets.add(critical_key)
            layers.append({
                'distance_m': distance,
                'hit_angle_cos': hit_angle_cos,
                'component': component,
                'material': material,
                'critical_target': critical_target,
                'chance_to_hit_by_explosion': explosion,
                '_native_order': index,
            })
        layers.sort(key=lambda value: (
            value['distance_m'], value['_native_order']))
        wire_layers = [{
            'distance_m': layer['distance_m'],
            'hit_angle_cos': layer['hit_angle_cos'],
            'component': layer['component'],
            'material': layer['material'],
            'critical_target': layer['critical_target'],
            'chance_to_hit_by_explosion':
                layer['chance_to_hit_by_explosion'],
        } for layer in layers]
        internal_hits = self._explosion_internal_critical_hits(
            frozen_target, descriptor, impact, direction, caliber, covered,
            crew_instances)
        return {
            'result': 'explosion_evidence',
            'value': {
                'target_pose': pose,
                'vehicle_ray': ({'layers': wire_layers}
                                if wire_layers else None),
                'internal_hits': internal_hits,
            },
        }

    def _vehicle_hit_test(self, query, arguments):
        if set(arguments) != set(('start', 'end', 'target')):
            raise OracleUnavailable(
                'invalid_arguments', 'vehicle_hit_test arguments are invalid')
        target_ref = _entity_ref(arguments.get('target'))
        if target_ref != query.get('entity'):
            raise OracleUnavailable(
                'target_mismatch', 'vehicle target does not match query entity')
        start = _protocol_vec(arguments['start'])
        end = _protocol_vec(arguments['end'])
        length = _length(start, end)
        if length <= 1.0e-9:
            raise OracleUnavailable(
                'invalid_segment', 'vehicle trace has no length')
        entity = self._resolve_entity(target_ref)
        collide = getattr(entity, 'collideSegmentExt', None)
        if not callable(collide):
            collide = getattr(entity, 'collideSegment', None)
        if not callable(collide):
            raise OracleUnavailable(
                'vehicle_hit_unavailable', 'vehicle hit tester is unavailable')
        collisions = collide(self._vector(start), self._vector(end))
        if collisions is None:
            return {'result': 'vehicle_hit_test', 'value': {'hit': None}}
        if hasattr(collisions, 'dist'):
            collisions = (collisions,)
        try:
            collisions = tuple(collisions)
        except TypeError:
            raise OracleUnavailable(
                'invalid_vehicle_hit', 'vehicle hit result is invalid')
        if not collisions:
            return {'result': 'vehicle_hit_test', 'value': {'hit': None}}
        if len(collisions) > MAX_VEHICLE_HIT_LAYERS:
            raise OracleUnavailable(
                'invalid_vehicle_hit', 'vehicle hit layer capacity exceeded')

        try:
            descriptor = getattr(entity, 'typeDescriptor')
        except AttributeError:
            descriptor = None
        except Exception:
            raise OracleUnavailable(
                'invalid_vehicle_hit',
                'vehicle type descriptor is unavailable')
        crew_instances = _descriptor_crew_instances(descriptor)
        layers = []
        covered = set()
        for index, collision in enumerate(collisions):
            distance = _bounded_finite(
                _native_attribute(
                    collision, 'dist', 'collision[%d].dist' % index),
                0.0, min(length, MAX_VEHICLE_HIT_DISTANCE_M),
                'collision[%d].dist' % index)
            hit_angle_cos = _bounded_finite(
                _native_attribute(
                    collision, 'hitAngleCos',
                    'collision[%d].hitAngleCos' % index),
                -1.0, 1.0, 'collision[%d].hitAngleCos' % index)
            try:
                component = getattr(collision, 'compName')
            except AttributeError:
                component = None
            except Exception:
                raise OracleUnavailable(
                    'invalid_vehicle_hit',
                    'collision[%d].compName is unavailable' % index)
            component = _optional_native_text(
                component, 'collision[%d].compName' % index)
            native_material = _native_attribute(
                collision, 'matInfo', 'collision[%d].matInfo' % index)
            material = _vehicle_hit_material(native_material)
            extra_name, critical_target, projectile_chance, explosion_chance = \
                _vehicle_layer_critical(
                    native_material, crew_instances, index)
            if extra_name is not None:
                covered.add(extra_name)
            layers.append({
                'distance_m': distance,
                'hit_angle_cos': hit_angle_cos,
                'component': component,
                'material': material,
                'critical_target': critical_target,
                'chance_to_hit_by_projectile': projectile_chance,
                'chance_to_hit_by_explosion': explosion_chance,
                '_native_order': index,
                '_collision': collision,
            })

        # Python's sort is stable, and the explicit native ordinal documents
        # the tie rule instead of accidentally ordering equal plates by name.
        layers.sort(key=lambda value: (
            value['distance_m'], value['_native_order']))
        earliest = layers[0]
        part = earliest['component']
        if part is None:
            raise OracleUnavailable(
                'invalid_vehicle_hit',
                'earliest vehicle hit has no native component')
        fraction = earliest['distance_m'] / length
        position = _lerp(start, end, fraction)
        collision = earliest['_collision']
        try:
            native_normal = getattr(collision, 'normal')
        except AttributeError:
            native_normal = None
        except Exception:
            raise OracleUnavailable(
                'invalid_vehicle_hit',
                'earliest vehicle hit normal is unavailable')
        if native_normal is None:
            # Exact #1513 collideSegmentExt records may expose no surface
            # normal. This reverse-ray unit vector is display-only: armor
            # authority uses each native hitAngleCos from the ordered layers.
            direction = tuple(
                start[axis] - end[axis] for axis in range(3))
            normal = _normalised(direction)
        else:
            normal = _native_vec(native_normal)
            normal_length = math.sqrt(sum(
                component * component for component in normal))
            if normal_length <= 1.0e-12:
                raise OracleUnavailable(
                    'invalid_vehicle_hit',
                    'earliest vehicle hit normal has no direction')
            normal = tuple(
                component / normal_length for component in normal)
        wire_layers = []
        for layer in layers:
            wire_layers.append({
                'distance_m': layer['distance_m'],
                'hit_angle_cos': layer['hit_angle_cos'],
                'component': layer['component'],
                'material': layer['material'],
                'critical_target': layer['critical_target'],
                'chance_to_hit_by_projectile':
                    layer['chance_to_hit_by_projectile'],
                'chance_to_hit_by_explosion':
                    layer['chance_to_hit_by_explosion'],
            })
        internal_hits = self._internal_critical_hits(
            entity, descriptor, start, end, covered, crew_instances, length)
        return {
            'result': 'vehicle_hit_test',
            'value': {'hit': {
                'fraction': fraction,
                'position': _wire_vec(position),
                'normal': _wire_vec(normal),
                'hit_part': part,
                'layers': wire_layers,
                'internal_hits': internal_hits,
            }},
        }

    def _node_transform(self, query, arguments):
        if set(arguments) != set(('node',)):
            raise OracleUnavailable(
                'invalid_arguments', 'node_transform arguments are invalid')
        node_name = arguments.get('node')
        if (not isinstance(node_name, _TEXT_TYPES) or not node_name or
                len(node_name.encode('utf-8')) > MAX_QUERY_KEY_BYTES or
                any(ord(character) < 32 or
                    127 <= ord(character) <= 159
                    for character in node_name)):
            raise OracleUnavailable(
                'invalid_node', 'native node name is invalid')
        entity = self._resolve_entity(query['entity'])
        model = getattr(entity, 'model', None)
        node_lookup = getattr(model, 'node', None)
        if not callable(node_lookup):
            raise OracleUnavailable(
                'node_unavailable', 'native model node lookup is unavailable')
        node = node_lookup(node_name)
        if node is None:
            return {'result': 'node_transform', 'value': {'transform': None}}
        matrix = getattr(node, 'matrix', None)
        if matrix is None:
            factory = self._matrix_factory
            if factory is None:
                try:
                    import Math
                    factory = Math.Matrix
                except Exception:
                    factory = None
            if factory is not None:
                try:
                    matrix = factory(node)
                except Exception:
                    matrix = node
            else:
                matrix = node
        if callable(matrix):
            matrix = matrix()
        position = getattr(matrix, 'position', None)
        if position is None:
            position = getattr(matrix, 'translation', None)
        apply_point = getattr(matrix, 'applyPoint', None)
        if position is None and callable(apply_point):
            position = apply_point(self._vector((0.0, 0.0, 0.0)))
        if position is None:
            raise OracleUnavailable(
                'node_unavailable', 'native node position is unavailable')
        raw_basis = getattr(matrix, 'basis', None)
        if raw_basis is not None:
            try:
                if len(raw_basis) != 9:
                    raise ValueError()
                basis = [_finite(value) for value in raw_basis]
            except (TypeError, ValueError):
                raise OracleUnavailable(
                    'node_unavailable', 'native node basis is invalid')
        else:
            apply_vector = getattr(matrix, 'applyVector', None)
            if not callable(apply_vector):
                raise OracleUnavailable(
                    'node_unavailable', 'native node basis is unavailable')
            axis_x = _native_vec(apply_vector(
                self._vector((1.0, 0.0, 0.0))))
            axis_y = _native_vec(apply_vector(
                self._vector((0.0, 1.0, 0.0))))
            axis_z = _native_vec(apply_vector(
                self._vector((0.0, 0.0, 1.0))))
            basis = [
                axis_x[0], axis_y[0], axis_z[0],
                axis_x[1], axis_y[1], axis_z[1],
                axis_x[2], axis_y[2], axis_z[2],
            ]
        return {
            'result': 'node_transform',
            'value': {'transform': {
                'position': _wire_vec(position),
                'basis': basis,
            }},
        }

    def _player_muzzle_evidence(self, query, arguments):
        if arguments:
            raise OracleUnavailable(
                'invalid_arguments',
                'player_muzzle_evidence arguments are invalid')
        outcome = self._node_transform(query, {'node': 'HP_gunFire'})
        transform = outcome['value']['transform']
        if transform is None:
            raise OracleUnavailable(
                'muzzle_unavailable',
                'native HP_gunFire transform is unavailable')
        return {
            'result': 'player_muzzle_evidence',
            'value': {
                'transform': transform,
                'barrel_under_water': self._barrel_under_water(
                    transform['position']),
            },
        }

    def _barrel_under_water(self, position):
        """Freeze #1513's positive-distance barrel water fact."""
        collide = getattr(self.bigworld, 'wg_collideWater', None)
        if not callable(collide):
            return True
        try:
            point = _protocol_vec(position)
            start = self._vector(point)
            end = self._vector((point[0], point[1] + 0.1, point[2]))
            value = collide(start, end, False)
            if value is None:
                return False
            if isinstance(value, bool):
                return True
            return _finite(value) > 0.0
        except Exception:
            # A missing, unstable, or malformed native water fact must never
            # become a dry muzzle at the server authority boundary.
            return True


class NativeOracleBridge(object):
    """Non-blocking transport plus render-thread native query executor."""

    def __init__(self, host='127.0.0.1', port=28782, bigworld=None,
                 vector_factory=None, matrix_factory=None,
                 space_id_provider=None,
                 entity_resolver=None, internal_ray_hits=None,
                 foliage_provider=None, destructibles_sensor=None,
                 ram_contact_collider=None,
                 explosion_frozen_target_provider=None,
                 internal_cone_hits=None,
                 socket_factory=None,
                 callback=None, cancel_callback=None, on_message=None,
                 on_disconnect=None, clock=None):
        if bigworld is None:
            try:
                import BigWorld
                bigworld = BigWorld
            except Exception:
                pass
        self.host = host
        self.port = int(port)
        self.bigworld = bigworld
        self._socket_factory = socket_factory
        self._callback = callback
        self._cancel_callback = cancel_callback
        self._on_message = on_message
        self._on_disconnect = on_disconnect
        self._clock = clock or time.time
        self.world = NativeWorldOracle(
            bigworld, vector_factory=vector_factory,
            matrix_factory=matrix_factory,
            space_id_provider=space_id_provider,
            entity_resolver=entity_resolver,
            internal_ray_hits=internal_ray_hits,
            foliage_provider=foliage_provider,
            destructibles_sensor=destructibles_sensor,
            ram_contact_collider=ram_contact_collider,
            explosion_frozen_target_provider=
                explosion_frozen_target_provider,
            internal_cone_hits=internal_cone_hits)
        self.running = False
        self.connected = False
        self.ready = False
        self.last_error = None
        self.oracle_generation = 0
        self.oracle_frame_seq = 0
        self._transport_token = 0
        self._socket = None
        self._thread = None
        self._render_callback_id = None
        self._render_thread_id = None
        self._lock = threading.RLock()
        self._inbound = collections.deque()
        self._outbound = collections.deque()
        self._outbound_bytes = 0
        self._last_batch_seq = 0
        self._last_issued_tick = -1
        self._last_world_revision = -1
        self._lineage = None
        self._query_generations = {}

    def hello_payload(self):
        return {
            'type': 'hello',
            'protocol': LAN_PROTOCOL_VERSION,
            'client_build': CLIENT_BUILD,
            'capabilities': list(CLIENT_CAPABILITIES),
            'role': WORKER_ROLE,
            'oracle_generation': int(self.oracle_generation),
        }

    def start(self):
        with self._lock:
            if self.running:
                return False
            self.oracle_generation += 1
            if self.oracle_generation > MAX_U64:
                self.last_error = 'oracle generation exhausted'
                return False
            self._transport_token += 1
            token = self._transport_token
            self.running = True
            self.connected = False
            self.ready = False
            self.last_error = None
            self.oracle_frame_seq = 0
            self._reset_queues_locked()
            self._reset_ordering_locked()
            self.world.reset_generation()
            self._render_thread_id = threading.current_thread().ident
            self._thread = threading.Thread(
                target=self._io_worker, args=(token,),
                name='offline-native-oracle-v1')
            self._thread.setDaemon(True)
            self._thread.start()
        self._schedule_render()
        return True

    def stop(self):
        self._stop('stopped', notify=False)

    def restart(self):
        self.stop()
        return self.start()

    def send_reliable(self, message):
        """Queue a parent-owned lifecycle message without socket I/O."""
        try:
            payload = _json_payload(message)
        except Exception:
            return False
        return self._enqueue_outbound(payload)

    def pending_batches(self):
        with self._lock:
            return sum(1 for kind, unused in self._inbound
                       if kind == 'query')

    def process_render_frame(self):
        """Execute bounded native work; safe to call directly in tests."""
        processed_messages = 0
        processed_batches = 0
        processed_primitives = 0
        while (processed_messages < MAX_MESSAGES_PER_RENDER and
               processed_batches < MAX_BATCHES_PER_RENDER):
            with self._lock:
                if not self.running or not self._inbound:
                    break
                kind, value = self._inbound[0]
                primitives = value.get('_oracle_primitives', 0) \
                    if kind == 'query' else 0
                if (kind == 'query' and processed_batches > 0 and
                        processed_primitives + primitives >
                        MAX_PRIMITIVES_PER_RENDER):
                    break
                self._inbound.popleft()
            processed_messages += 1
            if kind == 'control':
                callback = self._on_message
                if callable(callback):
                    try:
                        callback(dict(value))
                    except Exception as error:
                        self._fail_closed(
                            'parent message callback failed: %s' % error)
                        break
                continue
            try:
                reply = self._execute_batch(value)
                payload = _json_payload({
                    'type': 'query_reply', 'payload': reply})
            except Exception as error:
                self._fail_closed(
                    'native oracle batch failed: %s' % error)
                break
            if not self._enqueue_outbound(payload):
                self._fail_closed('native oracle outbound queue overflow')
                break
            processed_batches += 1
            processed_primitives += primitives
        return processed_batches

    def _begin_embedded_session(self, generation=1):
        """Test/embedding boundary when another v5 transport owns the wire."""
        generation = _exact_int(generation, 1, MAX_U64)
        if generation is None:
            raise ValueError('invalid oracle generation')
        with self._lock:
            self.oracle_generation = generation
            self._transport_token += 1
            self.running = True
            self.connected = True
            self.ready = True
            self.last_error = None
            self.oracle_frame_seq = 0
            self._reset_queues_locked()
            self._reset_ordering_locked()
            self.world.reset_generation()
        return True

    def _accept_server_message(self, message):
        """Test/embedding boundary for one already-decoded v5 object."""
        try:
            self._handle_wire_message(message)
            return True
        except Exception as error:
            self._fail_closed('invalid server message: %s' % error)
            return False

    def _drain_outbound_messages(self):
        """Test/embedding boundary returning detached decoded messages."""
        with self._lock:
            payloads = list(self._outbound)
            self._outbound.clear()
            self._outbound_bytes = 0
        result = []
        for payload in payloads:
            if not isinstance(payload, bytes):
                payload = payload.encode('utf-8')
            result.append(json.loads(payload.decode('utf-8').strip()))
        return result

    def _reset_queues_locked(self):
        self._inbound.clear()
        self._outbound.clear()
        self._outbound_bytes = 0

    def _reset_ordering_locked(self):
        self._last_batch_seq = 0
        self._last_issued_tick = -1
        self._last_world_revision = -1
        self._lineage = None
        self._query_generations = {}

    def _schedule_render(self):
        with self._lock:
            if not self.running or self._render_callback_id is not None:
                return False
        callback = self._callback
        if callback is None:
            callback = getattr(self.bigworld, 'callback', None)
        if not callable(callback):
            return False

        def render():
            with self._lock:
                self._render_callback_id = None
                self._render_thread_id = threading.current_thread().ident
            try:
                self.process_render_frame()
            except Exception as error:
                self._fail_closed('render callback failed: %s' % error)
                return
            self._schedule_render()

        callback_id = callback(RENDER_INTERVAL_SECONDS, render)
        with self._lock:
            if self.running:
                self._render_callback_id = callback_id
                return True
        return False

    def _cancel_render(self):
        with self._lock:
            callback_id = self._render_callback_id
            self._render_callback_id = None
        if callback_id is None:
            return
        cancel = self._cancel_callback
        if cancel is None:
            cancel = getattr(self.bigworld, 'cancelCallback', None)
        if callable(cancel):
            try:
                cancel(callback_id)
            except Exception:
                pass

    def _new_socket(self):
        factory = self._socket_factory
        if callable(factory):
            return factory()
        return socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def _io_worker(self, token):
        sock = None
        reason = None
        try:
            sock = self._new_socket()
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass
            sock.settimeout(SOCKET_TIMEOUT_SECONDS)
            sock.connect((self.host, self.port))
            with self._lock:
                if not self.running or token != self._transport_token:
                    return
                self._socket = sock
                self.connected = True
            self._send_payload(sock, _json_payload(self.hello_payload()))
            buffer_value = b''
            last_ping = self._clock()
            while self._token_running(token):
                while True:
                    payload = self._take_outbound()
                    if payload is None:
                        break
                    self._send_payload(sock, payload)
                now = self._clock()
                if now - last_ping >= PING_INTERVAL_SECONDS:
                    last_ping = now
                    self._send_payload(sock, _json_payload({
                        'type': 'ping',
                        'seq': int(now * 1000.0) % 2147483647,
                        'client_time': float(now),
                    }))
                try:
                    chunk = sock.recv(8192)
                except socket.timeout:
                    continue
                if chunk is None:
                    continue
                if not chunk:
                    raise socket.error('server closed native oracle connection')
                if not isinstance(chunk, bytes):
                    chunk = chunk.encode('utf-8')
                buffer_value += chunk
                if len(buffer_value) > MAX_BUFFER_BYTES:
                    raise OracleProtocolError('receive buffer exceeds limit')
                while b'\n' in buffer_value:
                    line, buffer_value = buffer_value.split(b'\n', 1)
                    if line.endswith(b'\r'):
                        line = line[:-1]
                    if not line:
                        continue
                    if len(line) > MAX_LINE_BYTES:
                        raise OracleProtocolError('server line exceeds limit')
                    try:
                        message = json.loads(line.decode('utf-8'))
                    except Exception:
                        raise OracleProtocolError('server line is not valid JSON')
                    self._handle_wire_message(message)
        except Exception as error:
            reason = str(error) or error.__class__.__name__
        finally:
            try:
                if sock is not None:
                    sock.close()
            except Exception:
                pass
            with self._lock:
                if self._socket is sock:
                    self._socket = None
                active = self.running and token == self._transport_token
            if active:
                self._fail_closed(reason or 'native oracle connection ended')

    def _token_running(self, token):
        with self._lock:
            return self.running and token == self._transport_token

    def _send_payload(self, sock, payload):
        deadline = self._clock() + SEND_STALL_SECONDS
        offset = 0
        send = getattr(sock, 'send', None)
        if not callable(send):
            sock.sendall(payload)
            return
        while offset < len(payload):
            try:
                written = send(payload[offset:])
            except socket.timeout:
                if self._clock() >= deadline:
                    raise socket.timeout('native oracle send stalled')
                continue
            if not written:
                raise socket.error('native oracle socket closed while sending')
            offset += int(written)

    def _enqueue_outbound(self, payload):
        size = len(payload)
        with self._lock:
            if (not self.running or len(self._outbound) >=
                    MAX_OUTBOUND_MESSAGES or
                    self._outbound_bytes + size > MAX_OUTBOUND_BYTES):
                return False
            self._outbound.append(payload)
            self._outbound_bytes += size
        return True

    def _take_outbound(self):
        with self._lock:
            if not self._outbound:
                return None
            payload = self._outbound.popleft()
            self._outbound_bytes -= len(payload)
            return payload

    def _handle_wire_message(self, message):
        if not isinstance(message, dict):
            raise OracleProtocolError('server message is not an object')
        kind = message.get('type')
        if not isinstance(kind, _TEXT_TYPES) or not kind:
            raise OracleProtocolError('server message type is missing')
        if kind == 'welcome':
            self._accept_welcome(message)
            self._queue_control(message)
            return
        if kind == 'pong':
            return
        if kind == 'error':
            raise OracleProtocolError(_bounded_text(
                message.get('message'), 'server rejected native oracle', 256))
        if kind == 'query_batch':
            if not self.ready:
                raise OracleProtocolError('query arrived before welcome')
            payload = message.get('payload')
            batch = self._validate_batch(payload)
            self._queue_query(batch)
            return
        self._queue_control(message)

    def _accept_welcome(self, message):
        capabilities = message.get('capabilities')
        server_capabilities = message.get('server_capabilities')
        if (_exact_int(message.get('protocol')) != LAN_PROTOCOL_VERSION or
                message.get('role') != WORKER_ROLE or
                _exact_int(message.get('worker_id')) != WORKER_ID or
                message.get('client_build') != CLIENT_BUILD or
                not isinstance(capabilities, list) or
                not set(CLIENT_CAPABILITIES).issubset(set(capabilities)) or
                not isinstance(server_capabilities, list) or
                HE_EXPLOSION_EVIDENCE_CAPABILITY not in
                server_capabilities):
            raise OracleProtocolError('invalid native oracle welcome')
        welcome_generation = message.get('oracle_generation')
        if (welcome_generation is not None and
                _exact_int(welcome_generation, 1, MAX_U64) !=
                self.oracle_generation):
            raise OracleProtocolError('oracle generation was not echoed')
        with self._lock:
            self.ready = True

    def _queue_control(self, message):
        with self._lock:
            if len(self._inbound) >= MAX_PENDING_MESSAGES:
                raise OracleProtocolError('native oracle inbound queue overflow')
            self._inbound.append(('control', dict(message)))

    def _queue_query(self, batch):
        with self._lock:
            if len(self._inbound) >= MAX_PENDING_MESSAGES:
                raise OracleProtocolError('native oracle inbound queue overflow')
            self._inbound.append(('query', batch))

    def _validate_batch(self, payload):
        if not isinstance(payload, dict):
            raise OracleProtocolError('query batch payload is invalid')
        required = set((
            'protocol_version', 'round_id', 'authority_epoch',
            'oracle_generation', 'batch_seq', 'issued_tick', 'apply_tick',
            'world_revision', 'queries'))
        if set(payload) != required:
            raise OracleProtocolError('query batch fields are invalid')
        protocol = _exact_int(payload.get('protocol_version'))
        round_id = _exact_int(payload.get('round_id'), 1, MAX_U64)
        authority_epoch = _exact_int(
            payload.get('authority_epoch'), 0, MAX_U64)
        oracle_generation = _exact_int(
            payload.get('oracle_generation'), 1, MAX_U64)
        batch_seq = _exact_int(payload.get('batch_seq'), 1, MAX_U64)
        issued_tick = _exact_int(payload.get('issued_tick'), 0, MAX_U64)
        apply_tick = _exact_int(payload.get('apply_tick'), 0, MAX_U64)
        world_revision = _exact_int(
            payload.get('world_revision'), 0, MAX_U64)
        queries = payload.get('queries')
        if (protocol != ORACLE_PROTOCOL_VERSION or round_id is None or
                authority_epoch is None or oracle_generation is None or
                batch_seq is None or issued_tick is None or
                apply_tick is None or world_revision is None or
                oracle_generation != self.oracle_generation or
                issued_tick > MAX_U64 - ORACLE_PIPELINE_TICKS or
                apply_tick != issued_tick + ORACLE_PIPELINE_TICKS or
                not isinstance(queries, list) or not queries or
                len(queries) > MAX_BATCH_QUERIES):
            raise OracleProtocolError('query batch header is invalid')
        lineage = (round_id, authority_epoch, oracle_generation)
        if self._lineage is not None and lineage != self._lineage:
            raise OracleProtocolError('query lineage changed without reconnect')
        if (batch_seq <= self._last_batch_seq or
                issued_tick < self._last_issued_tick or
                world_revision < self._last_world_revision):
            raise OracleProtocolError('query batch ordering regressed')

        query_ids = set()
        query_keys = set()
        next_generations = dict(self._query_generations)
        primitive_count = 0
        frozen_queries = []
        for raw in queries:
            query, primitives = self._validate_query(raw)
            query_id = query['query_id']
            query_key = query['key']
            if query_id in query_ids or query_key in query_keys:
                raise OracleProtocolError('query batch contains duplicates')
            query_ids.add(query_id)
            query_keys.add(query_key)
            expected_generation = next_generations.get(query_key, 0) + 1
            if query['query_generation'] != expected_generation:
                raise OracleProtocolError('query generation did not advance')
            next_generations[query_key] = expected_generation
            primitive_count += primitives
            if primitive_count > MAX_PRIMITIVE_OPERATIONS:
                raise OracleProtocolError('query primitive limit exceeded')
            frozen_queries.append(query)

        result = dict(payload)
        result['queries'] = frozen_queries
        result['_oracle_primitives'] = primitive_count
        self._lineage = lineage
        self._last_batch_seq = batch_seq
        self._last_issued_tick = issued_tick
        self._last_world_revision = world_revision
        self._query_generations = next_generations
        return result

    def _validate_query(self, raw):
        required = set((
            'query_id', 'key', 'query_generation', 'entity', 'operation'))
        if not isinstance(raw, dict) or set(raw) != required:
            raise OracleProtocolError('oracle query fields are invalid')
        query_id = _exact_int(raw.get('query_id'), 1, MAX_U64)
        query_generation = _exact_int(
            raw.get('query_generation'), 1, MAX_U64)
        key = raw.get('key')
        if (query_id is None or query_generation is None or
                not isinstance(key, _TEXT_TYPES) or not key or
                len(key.encode('utf-8')) > MAX_QUERY_KEY_BYTES or
                any(ord(character) < 32 or
                    127 <= ord(character) <= 159
                    for character in key)):
            raise OracleProtocolError('oracle query identity is invalid')
        entity = _entity_ref(raw.get('entity'))
        operation = raw.get('operation')
        if not isinstance(operation, dict):
            raise OracleProtocolError('oracle query operation is invalid')
        name = operation.get('operation')
        arguments = operation.get('arguments')
        primitives = 1
        if name in ('segment_cast_batch',):
            primitives = len(arguments.get('segments', ())) \
                if isinstance(arguments, dict) else 0
        elif name in ('ground_sample_batch', 'water_sample_batch'):
            primitives = len(arguments.get('positions', ())) \
                if isinstance(arguments, dict) else 0
        elif name in (
                'spotting_evidence', 'firing_lane_evidence',
                'ram_contact_armor_evidence', 'explosion_evidence'):
            primitives = 2
        elif name == 'destructible_shot_evidence':
            primitives = MAX_DESTRUCTIBLE_CANDIDATES
        elif name == 'destructible_hull_evidence':
            primitives = MAX_DESTRUCTIBLE_HULL_CANDIDATES
        if primitives <= 0:
            raise OracleProtocolError('oracle primitive batch is empty')
        query = {
            'query_id': query_id,
            'key': key,
            'query_generation': query_generation,
            'entity': entity,
            'operation': operation,
        }
        return query, primitives

    def _execute_batch(self, batch):
        self.oracle_frame_seq += 1
        if self.oracle_frame_seq > MAX_U64:
            raise OracleProtocolError('oracle frame sequence exhausted')
        results = []
        for query in batch['queries']:
            try:
                outcome = self.world.execute(query)
                status = {'status': 'ok', 'outcome': outcome}
            except OracleUnavailable as error:
                status = {
                    'status': 'unavailable',
                    'code': error.code,
                    'message': error.message,
                }
            except Exception as error:
                status = {
                    'status': 'unavailable',
                    'code': 'native_exception',
                    'message': _bounded_text(
                        error, 'native oracle query raised',
                        MAX_ERROR_MESSAGE_BYTES),
                }
            results.append({
                'query_id': query['query_id'],
                'key': query['key'],
                'query_generation': query['query_generation'],
                'entity': dict(query['entity']),
                'status': status,
            })
        return {
            'protocol_version': ORACLE_PROTOCOL_VERSION,
            'round_id': batch['round_id'],
            'authority_epoch': batch['authority_epoch'],
            'oracle_generation': batch['oracle_generation'],
            'batch_seq': batch['batch_seq'],
            'issued_tick': batch['issued_tick'],
            'apply_tick': batch['apply_tick'],
            'world_revision': batch['world_revision'],
            'oracle_frame_seq': self.oracle_frame_seq,
            'results': results,
        }

    def _fail_closed(self, reason):
        self._stop(_bounded_text(
            reason, 'native oracle failed closed', 256), notify=True)

    def _stop(self, reason, notify):
        with self._lock:
            was_active = self.running or self.connected
            self.running = False
            self.connected = False
            self.ready = False
            self.last_error = reason
            self._transport_token += 1
            sock = self._socket
            self._socket = None
            thread = self._thread
            self._thread = None
            self._reset_queues_locked()
            render_thread_id = self._render_thread_id
        if threading.current_thread().ident == render_thread_id:
            self._cancel_render()
        try:
            if sock is not None:
                sock.close()
        except Exception:
            pass
        current = threading.current_thread()
        if thread is not None and thread is not current:
            try:
                thread.join(THREAD_JOIN_SECONDS)
            except Exception:
                pass
        if notify and was_active and callable(self._on_disconnect):
            try:
                self._on_disconnect(reason)
            except Exception:
                pass


_ACTIVE_BRIDGE = None
_ACTIVE_LOCK = threading.RLock()
_LAST_ORACLE_GENERATION = 0


def start_native_oracle_bridge(**kwargs):
    """Create and start the one process-local native oracle bridge."""
    global _ACTIVE_BRIDGE, _LAST_ORACLE_GENERATION
    with _ACTIVE_LOCK:
        if _ACTIVE_BRIDGE is not None and _ACTIVE_BRIDGE.running:
            return _ACTIVE_BRIDGE
        bridge = NativeOracleBridge(**kwargs)
        bridge.oracle_generation = _LAST_ORACLE_GENERATION
        if not bridge.start():
            return None
        _LAST_ORACLE_GENERATION = bridge.oracle_generation
        _ACTIVE_BRIDGE = bridge
        return bridge


def stop_native_oracle_bridge():
    """Stop and forget the process-local bridge; safe during native teardown."""
    global _ACTIVE_BRIDGE
    with _ACTIVE_LOCK:
        bridge = _ACTIVE_BRIDGE
        _ACTIVE_BRIDGE = None
    if bridge is not None:
        bridge.stop()
        return True
    return False
