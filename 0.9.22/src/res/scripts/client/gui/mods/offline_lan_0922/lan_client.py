from __future__ import print_function

import base64
import json
import math
import socket
import threading
import time
import uuid

from gui.mods.offline_lan_0922 import effective_params as effective_params_wire
from gui.mods.offline_lan_0922 import burst_mechanics
from gui.mods.offline_lan_0922 import equipment_mechanics
from gui.mods.offline_lan_0922 import siege_mechanics
from gui.mods.offline_lan_0922 import spotting


PROTOCOL_VERSION = 5
CLIENT_BUILD = 'wot-0.9.22.0.1-cn-1513'
RANDOM_MAP_OPTION = 'server_random'
PROJECTILE_LEDGER_CAPABILITY = 'projectile_ledger_v2'
RICOCHET_CONTINUATION_CAPABILITY = 'ricochet_continuation_v1'
PROJECTILE_HIT_VEHICLE_CAPABILITY = 'projectile_hit_vehicle_v1'
PROJECTILE_WRECK_HIT_CAPABILITY = 'projectile_wreck_hit_v1'
RANDOM_MAP_CAPABILITY = 'random_map_v1'
TEAM_SELECTION_CAPABILITY = 'team_selection_v1'
TEAM_SIZE_SELECTION_CAPABILITY = 'team_size_selection_v1'
DESTRUCTIBLE_CATALOG_V5_CAPABILITY = 'destructible_catalog_v5'
LEAN_SNAPSHOT_MANIFEST_CAPABILITY = 'lean_snapshot_manifest_v1'
RAM_CONTACT_LEDGER_CAPABILITY = 'ram_contact_ledger_v2'
HUMAN_RAM_TIMELINE_CAPABILITY = 'human_ram_timeline_v1'
PLAYER_FIRE_INTENT_CAPABILITY = 'player_fire_intent_v4'
PLAYER_ENVIRONMENT_CAPABILITY = 'player_environment_v2'
EFFECTIVE_PARAMS_CAPABILITY = effective_params_wire.CAPABILITY
SIMULATION_WORKER_CAPABILITY = 'simulation_worker_v1'
CLIENT_CAPABILITIES = (
    PROJECTILE_LEDGER_CAPABILITY,
    RICOCHET_CONTINUATION_CAPABILITY,
    DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
    LEAN_SNAPSHOT_MANIFEST_CAPABILITY,
    RAM_CONTACT_LEDGER_CAPABILITY,
    HUMAN_RAM_TIMELINE_CAPABILITY,
    PLAYER_FIRE_INTENT_CAPABILITY,
    PLAYER_ENVIRONMENT_CAPABILITY,
    EFFECTIVE_PARAMS_CAPABILITY,
)
WORKER_AUTHORITY_ID = -1
POLL_INTERVAL = 1.0 / 60.0
PING_INTERVAL = 1.0
MAX_MESSAGE_BYTES = 256 * 1024
MAX_BUFFER_BYTES = MAX_MESSAGE_BYTES * 2
MAX_PENDING_MESSAGES = 512
MAX_OUTBOUND_MESSAGES = 256
MAX_OUTBOUND_BYTES = MAX_MESSAGE_BYTES * 4
MAX_OUTBOUND_NODES = 16384
MAX_OUTBOUND_DEPTH = 16
MAX_PROJECTILE_BATCH = 30
MAX_HUMAN_RAM_PROBES = 64
MAX_PROJECTILE_DESTRUCTIBLES = 64
MAX_PROJECTILE_ID = 2147483647
MAX_PROJECTILE_DAMAGE_STICKER = (1 << 64) - 1
PLAYER_LANDING_MAX_IMPACT_SPEED = 200.0
MAX_LANDING_OBSERVATION_QUEUE = 32
# A process-relative microsecond clock fits comfortably in this bound for
# centuries while remaining an exact JSON integer on Python 2 and Python 3.
MAX_MOTION_TIME_US = 10000000000000000
MAX_PROJECTILE_ORIGIN = 5000.0
MAX_PROJECTILE_VELOCITY = 3000.0
# #1513 includes SPG shells such as the B-4 with gravity=143.  Keep the
# protocol bound finite without rejecting stock descriptors.
MAX_PROJECTILE_GRAVITY = 500.0
MAX_PROJECTILE_DISTANCE = 10000.0
MAX_PROJECTILE_TIME_MS = 20000
MAX_PROJECTILE_SPLASH_RADIUS = 100.0
MAX_PLAYER_DISPERSION_ANGLE = 0.5
MAX_PLAYER_CLIP_SIZE = 255
MAX_PLAYER_RELOAD_SECONDS = 3600.0
# The exact ordered-input wire contract enforced by the bundled LAN
# server's pre-admission validator.  The launcher always installs the
# matching pair, so these mirror it field for field: a frame this
# client queues must already satisfy the same envelope.
MAX_PLAYER_INPUT_SPEED = 200.0
MAX_PLAYER_GUN_PITCH = 1.2
MAX_PLAYER_INPUT_ATTITUDE = 0.61
PLAYER_INPUT_WORLD_BOUNDS = (2000.0, 1000.0, 2000.0)
MAX_PLAYER_RAM_CONTACTS = 16
MAX_PLAYER_DESTRUCTIBLE_CONTACTS = 16
MAX_PLAYER_DESTRUCTIBLE_CONTACT_TOKEN = 64
MAX_PROJECTILE_PIERCING_LOSS = 100000.0
MAX_CRITICAL_DEVICE_HP = effective_params_wire.MAX_CRITICAL_DEVICE_HP
CRITICAL_DELTA_DEVICE_NAMES = frozenset((
    'engineHealth', 'ammoBayHealth', 'fuelTankHealth', 'radioHealth',
    'leftTrackHealth', 'rightTrackHealth', 'gunHealth',
    'turretRotatorHealth', 'surveyingDeviceHealth'))
CRITICAL_DELTA_CREW_NAMES = frozenset((
    'commander', 'driver', 'gunner', 'gunner1', 'gunner2', 'loader',
    'loader1', 'loader2', 'radioman', 'radioman1', 'radioman2'))
PROJECTILE_SHELL_KINDS = frozenset((
    'HOLLOW_CHARGE', 'HIGH_EXPLOSIVE', 'ARMOR_PIERCING',
    'ARMOR_PIERCING_HE', 'ARMOR_PIERCING_CR'))
PROJECTILE_HE_FACTOR_FIELDS = frozenset((
    'explosionDamageFactor', 'explosionDamageAbsorptionFactor',
    'explosionEdgeDamageFactor'))
OUTFIT_SEASONS = frozenset((1, 2, 4))
MAX_OUTFIT_BYTES = 64 * 1024
MAX_VEHICLE_COMPACT_BYTES = 64 * 1024
RESULT_INTERACTION_LIMITS = {
    'spotted': (0, 1),
    'death_reason': (-1, 10),
    'direct_hits': (0, 65535),
    'explosion_hits': (0, 65535),
    'piercings': (0, 65535),
    'damage': (0, 65535),
    'assist_track': (0, 65535),
    'assist_radio': (0, 65535),
    'assist_stun': (0, 65535),
    'crits': (0, 4294967295),
    'fire': (0, 65535),
    'stun_num': (0, 65535),
    'stun_duration': (0, 65535),
    'damage_blocked': (0, 4294967295),
    'damage_received': (0, 65535),
    'ricochets_received': (0, 65535),
    'no_damage_direct_hits_received': (0, 65535),
    'target_kills': (0, 255),
}
BOT_TIER_MODES = frozenset((
    'random', 'same', 'minus1_0', '0_plus1', 'minus1_plus2'))
SENDER_JOIN_TIMEOUT = 0.1
SEND_STALL_TIMEOUT = 5.0
LEAVE_SEND_TIMEOUT = 0.05
RUNTIME_DROP_LOG_INTERVAL = 5.0
LEAVE_PAYLOAD = b'{"type":"leave"}\n'
_BOT_STATE_WIRE_FIELDS = (
    'id', 'x', 'y', 'z', 'yaw', 'pitch', 'roll', 'aim_yaw', 'gun_pitch',
    'speed', 'movement_dir', 'rotation_dir', 'fire_seq', 'shell_index',
    'next_shell_index', 'ammo_remaining', 'ammo_reload_pending',
    'reload_time', 'reload_duration', 'clip', 'clip_size',
    'burst_active', 'burst_group_seq', 'burst_count',
    'burst_next_index', 'burst_interval', 'burst_time_left',
    'burst_shell_index',
    'siege_state', 'siege_time_left_ms', 'siege_transition_total_ms',
    'health', 'alive', 'critical', 'combat_base_revision', 'combat_seq',
    'combat_fire_elapsed', 'combat_fire_timer',
    'death_reason', 'display_health', 'world_pose', 'equipment_states',
    'stun_end_server_time_ms')
STATE_BARRIER_TYPES = frozenset((
    'welcome', 'roster', 'battle_start', 'battle_live',
    'start_denied', 'team_denied', 'team_size_denied',
    'bot_tier_mode_denied', 'events', 'error'))
ORDERED_RECEIVE_TYPES = STATE_BARRIER_TYPES | frozenset((
    'battle_receipt', 'fire_intent', 'fire_intent_result',
    'landing_observation_result',
    'player_destructible_contact',
    'player_destructible_contact_result'))
SERVER_STATE_TYPES = frozenset((
    'welcome', 'roster', 'battle_start', 'battle_live', 'start_denied',
    'team_denied', 'team_size_denied', 'bot_tier_mode_denied', 'snapshot',
    'events', 'bot_observation',
    'battle_receipt', 'player_destructible_contact'))
RECOVERABLE_RUNTIME_TYPES = frozenset((
    'snapshot', 'events', 'bot_observation',
    'landing_observation_result'))


def _monotonic_time():
    """Return one non-adjustable process clock on #1513 and test hosts."""
    function = getattr(time, 'monotonic', None)
    if callable(function):
        return float(function())
    # Python 2.7 on Windows defines time.clock() as elapsed wall time backed
    # by QueryPerformanceCounter.  That is the clock used by the #1513 client.
    return float(time.clock())


try:
    string_types = (basestring,)
except NameError:
    string_types = (str,)

try:
    integer_types = (int, long)
except NameError:
    integer_types = (int,)


class _OutboundPayloadError(Exception):
    pass


class _PreencodedOutbound(object):
    """Immutable wire bytes owned by the reliable outbound queue."""

    __slots__ = ('payload', 'coalesce_key')

    def __init__(self, payload, coalesce_key=None):
        self.payload = payload
        self.coalesce_key = coalesce_key


def _json_text_size(value):
    """Return a conservative UTF-8 byte bound for JSON's quoted text."""
    size = 2
    for character in value:
        if not isinstance(character, string_types):
            character = chr(character)
        number = ord(character)
        if character in ('"', '\\') or number in (8, 9, 10, 12, 13):
            size += 2
        elif number < 32 or number == 127:
            size += 6
        elif number < 128:
            size += 1
        elif number <= 65535:
            size += 6
        else:
            size += 12
    return size


def _freeze_outbound(value, budget, depth=0):
    """Copy plain JSON data and estimate its maximum encoded wire size."""
    if depth > MAX_OUTBOUND_DEPTH:
        raise _OutboundPayloadError('outbound payload nesting exceeded limit')
    budget[0] += 1
    if budget[0] > MAX_OUTBOUND_NODES:
        raise _OutboundPayloadError('outbound payload node count exceeded limit')
    if value is None:
        return None, 4
    if isinstance(value, bool):
        return value, 4 if value else 5
    if isinstance(value, integer_types):
        try:
            return value, len(str(value))
        except Exception:
            raise _OutboundPayloadError('outbound integer is not encodable')
    if isinstance(value, float):
        try:
            if math.isnan(value) or math.isinf(value):
                raise _OutboundPayloadError(
                    'outbound float must be finite')
            # Allow for encoder spelling differences across Python 2 and 3.
            return value, len(repr(value)) + 8
        except _OutboundPayloadError:
            raise
        except Exception:
            raise _OutboundPayloadError('outbound float is not encodable')
    if isinstance(value, string_types):
        return value, _json_text_size(value)
    if isinstance(value, (list, tuple)):
        frozen = []
        size = 2
        for item in value:
            copied, item_size = _freeze_outbound(item, budget, depth + 1)
            if frozen:
                size += 1
            size += item_size
            if size + 1 > MAX_MESSAGE_BYTES:
                raise _OutboundPayloadError(
                    'outbound payload exceeded wire limit')
            frozen.append(copied)
        return tuple(frozen), size
    if isinstance(value, dict):
        frozen = {}
        size = 2
        for key, item in value.items():
            if not isinstance(key, string_types):
                raise _OutboundPayloadError(
                    'outbound mapping key must be text')
            copied, item_size = _freeze_outbound(item, budget, depth + 1)
            if frozen:
                size += 1
            size += _json_text_size(key) + 1 + item_size
            if size + 1 > MAX_MESSAGE_BYTES:
                raise _OutboundPayloadError(
                    'outbound payload exceeded wire limit')
            frozen[key] = copied
        return frozen, size
    raise _OutboundPayloadError('outbound payload contains non-plain data')


def _finite_float(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float(default)
    try:
        if math.isnan(value) or math.isinf(value):
            return float(default)
    except Exception:
        pass
    return value


def _safe_text(value, default='', limit=80):
    if value is None:
        value = default
    if not isinstance(value, string_types):
        value = str(value)
    return value[:limit]


def _exact_int(value, default=None):
    if isinstance(value, bool):
        return default
    if isinstance(value, integer_types):
        return value
    try:
        parsed = int(value)
        if float(value) != parsed:
            return default
        return parsed
    except (TypeError, ValueError, OverflowError):
        return default


def _team_choice(value, default=0):
    if value in (None, '', 'auto'):
        return 0
    parsed = _exact_int(value)
    return parsed if parsed in (0, 1, 2) else default


def _team_sizes(value, legacy=None, default=None):
    """Validate the asymmetric shape, with the old scalar as fallback."""
    if value is None:
        parsed = _exact_int(legacy)
        if parsed is not None and 1 <= parsed <= 15:
            return {1: parsed, 2: parsed}
        return dict(default or {1: 15, 2: 15})
    if not isinstance(value, dict):
        return None
    result = {}
    for team in (1, 2):
        parsed = _exact_int(value.get(str(team), value.get(team)))
        if parsed is None or not 1 <= parsed <= 15:
            return None
        result[team] = parsed
    return result


def _valid_player_siege_contract(player):
    if not isinstance(player, dict):
        return False
    has_state = 'siege_state' in player
    has_time = 'siege_time_left_ms' in player
    if not has_state and not has_time:
        return True
    if has_state != has_time:
        return False
    state = _exact_int(player.get('siege_state'))
    time_left = _exact_int(player.get('siege_time_left_ms'))
    if state not in (0, 1, 2, 3) or time_left is None:
        return False
    if not 0 <= time_left <= 5000:
        return False
    return ((state in (1, 3) and time_left > 0) or
            (state in (0, 2) and time_left == 0))


def _valid_bot_siege_contract(bot):
    if not isinstance(bot, dict):
        return False
    fields = (
        'siege_state', 'siege_time_left_ms',
        'siege_transition_total_ms')
    present = tuple(name in bot for name in fields)
    if not any(present):
        return True
    if not all(present):
        return False
    return siege_mechanics.valid_wire_state(
        bot.get('siege_state'), bot.get('siege_time_left_ms'),
        transition_total_ms=bot.get('siege_transition_total_ms'))


def _valid_stun_contract(vehicle):
    if not isinstance(vehicle, dict):
        return False
    names = (
        'stun_end_server_time_ms', 'stun_attacker_kind',
        'stun_attacker_id')
    present = tuple(name in vehicle for name in names)
    if not any(present):
        return True
    if not all(present):
        return False
    end = _projectile_int_range(
        vehicle.get('stun_end_server_time_ms'), 0, MAX_PROJECTILE_ID)
    attacker_id = _projectile_int_range(
        vehicle.get('stun_attacker_id'), 0, MAX_PROJECTILE_ID)
    attacker_kind = vehicle.get('stun_attacker_kind')
    if end is None or attacker_id is None:
        return False
    return bool(
        (end == 0 and attacker_kind == '' and attacker_id == 0) or
        (end > 0 and attacker_kind in ('player', 'bot') and
         attacker_id > 0))


def _canonical_angle(value, default=0.0):
    """Return one mathematically equivalent angle inside [-pi, pi].

    Yaw is periodic, so an accumulated turret plus hull sum is normalized
    rather than clipped: clipping would silently point the reported gun
    somewhere the player is not aiming, while normalization reports the exact
    same orientation inside the server's ordered-input contract.
    """
    angle = _finite_float(value, default)
    period = 2.0 * math.pi
    angle = math.fmod(angle + math.pi, period)
    if angle < 0.0:
        angle += period
    return angle - math.pi


def _exact_finite_float(value, default=None):
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
        if math.isnan(parsed) or math.isinf(parsed):
            return default
        return parsed
    except (TypeError, ValueError, OverflowError):
        return default


def _canonical_human_gun_checkpoint(value):
    """Validate one visible-client gun state at an ordered input edge."""
    required = frozenset((
        'reload_time', 'reload_duration', 'clip', 'clip_size',
        'dispersion'))
    if not isinstance(value, dict) or set(value) != required:
        return None
    reload_time = _exact_finite_float(value.get('reload_time'))
    reload_duration = _exact_finite_float(value.get('reload_duration'))
    dispersion = _exact_finite_float(value.get('dispersion'))
    clip = _exact_int(value.get('clip'))
    clip_size = _exact_int(value.get('clip_size'))
    if (reload_time is None or reload_duration is None or
            reload_duration <= 0.0 or reload_time < 0.0 or
            reload_duration > MAX_PLAYER_RELOAD_SECONDS or
            reload_time > reload_duration or clip is None or
            clip_size is None or not 1 <= clip_size <= MAX_PLAYER_CLIP_SIZE or
            not 0 <= clip <= clip_size or dispersion is None or
            not 0.0 <= dispersion <= MAX_PLAYER_DISPERSION_ANGLE):
        return None
    return {
        'reload_time': reload_time,
        'reload_duration': reload_duration,
        'clip': clip,
        'clip_size': clip_size,
        'dispersion': dispersion,
    }


def _valid_player_gun_checkpoint_contract(player):
    """Require one checkpoint for every admitted modern player input."""
    if not isinstance(player, dict):
        return False
    input_seq = _exact_int(player.get('input_seq'))
    has_seq = 'gun_checkpoint_seq' in player
    has_checkpoint = 'gun_checkpoint' in player
    if not has_seq and not has_checkpoint:
        # Waiting/start rosters and compact compatibility fixtures may not
        # have admitted an input yet. Once either field appears, the pair is
        # strict and must identify the current ordered input below.
        return True
    if not has_seq or not has_checkpoint:
        return False
    checkpoint_seq = _exact_int(player.get('gun_checkpoint_seq'))
    return bool(
        input_seq is not None and input_seq > 0 and
        checkpoint_seq == input_seq and
        _canonical_human_gun_checkpoint(
            player.get('gun_checkpoint')) is not None)


def _attach_critical_proposal(message, critical, base_revision, ack_seq,
                              hull_damage, critical_delta):
    """Attach one strict #1513 compare-and-swap critical proposal."""
    if not isinstance(critical, dict):
        return
    parsed_delta = _strict_critical_delta(critical_delta)
    if parsed_delta is None:
        raise ValueError('critical proposal requires a damage delta')
    parsed = []
    for name, value in (
            ('critical_target_base_revision', base_revision),
            ('critical_target_ack_seq', ack_seq),
            ('hull_damage', hull_damage)):
        exact = _exact_int(value)
        if exact is None or exact < 0:
            raise ValueError('%s must be a non-negative integer' % name)
        parsed.append((name, exact))
    message['critical'] = critical
    message['critical_delta'] = parsed_delta
    for name, value in parsed:
        message[name] = value


def _valid_bot_combat_contract(bot):
    if not isinstance(bot, dict) or not isinstance(bot.get('critical'), dict):
        return False
    revision = _exact_int(bot.get('combat_revision'))
    base_revision = _exact_int(bot.get('combat_base_revision'))
    ack_seq = _exact_int(bot.get('combat_ack_seq'))
    fire_elapsed = _exact_finite_float(bot.get('combat_fire_elapsed'))
    fire_timer = _exact_finite_float(bot.get('combat_fire_timer'))
    if (revision is None or revision < 0 or
            base_revision is None or base_revision < 0 or
            base_revision > revision or ack_seq is None or ack_seq < 0 or
            fire_elapsed is None or fire_elapsed < 0.0 or
            fire_elapsed > 10.0 or fire_timer is None or
            fire_timer < 0.0 or fire_timer >= 1.0):
        return False
    if (not bool(bot['critical'].get('fire', False)) and
            (fire_elapsed != 0.0 or fire_timer != 0.0)):
        return False
    return True


def _valid_player_equipment_contract(state, required=False):
    """Validate the complete canonical player equipment ledger."""
    fields = {
        'equipment_states', 'equipment_revision',
        'equipment_intent_seq', 'equipment_intent_result'}
    if not fields.issubset(state):
        return not required and not (set(state) & fields)
    snapshots = state.get('equipment_states')
    revision = _projectile_int_range(
        state.get('equipment_revision'), 0, MAX_PROJECTILE_ID)
    sequence = _projectile_int_range(
        state.get('equipment_intent_seq'), 0, MAX_PROJECTILE_ID)
    result = state.get('equipment_intent_result')
    if (not isinstance(snapshots, list) or len(snapshots) > 3 or
            revision is None or sequence is None or
            not isinstance(result, dict) or set(result) != {
                'intent_seq', 'accepted', 'reason'}):
        return False
    result_sequence = _projectile_int_range(
        result.get('intent_seq'), 0, MAX_PROJECTILE_ID)
    reason = result.get('reason')
    if (result_sequence != sequence or
            not isinstance(result.get('accepted'), bool) or
            not isinstance(reason, string_types) or len(reason) > 64):
        return False
    try:
        equipment_mechanics.restore_equipment_states(snapshots, now=0.0)
    except (TypeError, ValueError):
        return False
    return True


def _valid_bot_equipment_contract(state, required=False):
    snapshots = state.get('equipment_states')
    if snapshots is None:
        return not required and 'equipment_states' not in state
    if not isinstance(snapshots, list):
        return False
    try:
        contracts = equipment_mechanics.bot_consumable_contracts(
            None, snapshot=snapshots)
        equipment_mechanics.restore_equipment_states(
            snapshots, contracts=contracts, now=0.0)
    except (TypeError, ValueError):
        return False
    return True


def _valid_player_environment_contract(state, required=False):
    """Validate canonical pose, input and landing frontiers in a player row."""
    required_fields = {
        'input_seq', 'up_cosine', 'landing_observation_seq'}
    if not required_fields.issubset(state):
        return not required and not (set(state) & required_fields)
    input_sequence = _projectile_int_range(
        state.get('input_seq'), 0, MAX_PROJECTILE_ID)
    landing_sequence = _projectile_int_range(
        state.get('landing_observation_seq'), 0, MAX_PROJECTILE_ID)
    up_cosine = _exact_finite_float(state.get('up_cosine'))
    processed_sequence = input_sequence
    if 'input_processed_seq' in state:
        processed_sequence = _projectile_int_range(
            state.get('input_processed_seq'), 0, MAX_PROJECTILE_ID)
    return bool(
        input_sequence is not None and landing_sequence is not None and
        processed_sequence is not None and
        processed_sequence >= input_sequence and
        up_cosine is not None and -1.0 <= up_cosine <= 1.0)


def _mapping_list(value, limit=30):
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value[:limit]
            if isinstance(item, dict)]


def _strict_mapping_list(value, limit=30):
    if (not isinstance(value, (list, tuple)) or len(value) > limit or
            any(not isinstance(item, dict) for item in value)):
        return None
    return [dict(item) for item in value]


def project_bot_state(state):
    """Return only fields consumed by the v5 server bot-state sanitizer."""
    if not isinstance(state, dict):
        return None
    projected = dict((name, state[name]) for name in _BOT_STATE_WIRE_FIELDS
                     if name in state)
    has_shot_yaw = 'shot_yaw' in state
    has_shot_pitch = 'shot_pitch' in state
    if has_shot_yaw != has_shot_pitch:
        return None
    if has_shot_yaw:
        projected['shot_yaw'] = state['shot_yaw']
        projected['shot_pitch'] = state['shot_pitch']
    ammo_fields = ('shell_index', 'next_shell_index', 'ammo_remaining',
                   'ammo_reload_pending')
    present = tuple(name in state for name in ammo_fields)
    if any(present) and not all(present):
        return None
    if (all(present) and
            not isinstance(state.get('ammo_reload_pending'), bool)):
        return None
    clip_fields = ('clip', 'clip_size')
    clip_present = tuple(name in state for name in clip_fields)
    if any(clip_present) and not all(clip_present):
        return None
    reload_fields = ('reload_time', 'reload_duration')
    if not all(name in state for name in reload_fields):
        return None
    reload_time = _exact_finite_float(state.get('reload_time'))
    reload_duration = _exact_finite_float(state.get('reload_duration'))
    if (reload_time is None or reload_duration is None or
            reload_duration <= 0.0 or reload_time < 0.0 or
            reload_time > reload_duration):
        return None
    siege_fields = (
        'siege_state', 'siege_time_left_ms',
        'siege_transition_total_ms')
    siege_present = tuple(name in state for name in siege_fields)
    if any(siege_present) and not all(siege_present):
        return None
    if (all(siege_present) and
            not siege_mechanics.valid_wire_state(
                state.get('siege_state'),
                state.get('siege_time_left_ms'),
                transition_total_ms=state.get(
                    'siege_transition_total_ms'))):
        return None
    try:
        burst_mechanics.BurstClock().restore(
            projected, projected.get('fire_seq', 0))
    except ValueError:
        return None
    if ('equipment_states' in state and
            not _valid_bot_equipment_contract(state)):
        return None
    if 'stun_end_server_time_ms' in state:
        stun_end = _projectile_int_range(
            state.get('stun_end_server_time_ms'), 0, MAX_PROJECTILE_ID)
        if stun_end is None:
            return None
        projected['stun_end_server_time_ms'] = stun_end
    return projected


def _project_human_ram_armors(raw_results):
    """Return the exact worker-only native contact-armour response batch."""
    if (not isinstance(raw_results, (list, tuple)) or
            len(raw_results) > MAX_HUMAN_RAM_PROBES):
        return None
    projected = []
    seen = set()
    for raw in raw_results:
        if not isinstance(raw, dict):
            return None
        available = raw.get('available')
        allowed = set(('seq', 'first_id', 'second_id', 'available'))
        if available is True:
            allowed.update(('armor_first', 'armor_second'))
        if set(raw) != allowed or not isinstance(available, bool):
            return None
        sequence = _exact_int(raw.get('seq'))
        first_id = _exact_int(raw.get('first_id'))
        second_id = _exact_int(raw.get('second_id'))
        if (sequence is None or not 0 < sequence <= MAX_PROJECTILE_ID or
                sequence in seen or first_id is None or second_id is None or
                not 0 < first_id < second_id <= MAX_PROJECTILE_ID):
            return None
        seen.add(sequence)
        result = {
            'seq': sequence, 'first_id': first_id,
            'second_id': second_id, 'available': available,
        }
        if available:
            armor_first = _projectile_float_range(
                raw.get('armor_first'), 0.000001, 5000.0)
            armor_second = _projectile_float_range(
                raw.get('armor_second'), 0.000001, 5000.0)
            if armor_first is None or armor_second is None:
                return None
            result['armor_first'] = armor_first
            result['armor_second'] = armor_second
        projected.append(result)
    return projected


# Compatibility for engine-free callers which imported the old private name.
_project_bot_state = project_bot_state


def _projectile_int_range(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, integer_types):
        return None
    if value < minimum or value > maximum:
        return None
    return value


def _strict_burst_group(shot_seq, group_seq, burst_index, burst_count):
    """Validate the immutable physical-shell identity of one trigger group."""
    shot_seq = _projectile_int_range(shot_seq, 1, MAX_PROJECTILE_ID)
    group_seq = _projectile_int_range(group_seq, 1, MAX_PROJECTILE_ID)
    burst_count = _projectile_int_range(
        burst_count, 1, burst_mechanics.MAX_BURST_COUNT)
    if burst_count is None:
        return None
    burst_index = _projectile_int_range(
        burst_index, 0, burst_count - 1)
    if (shot_seq is None or group_seq is None or burst_index is None or
            shot_seq != group_seq + burst_index):
        return None
    return group_seq, burst_index, burst_count


def _projectile_float_range(value, minimum, maximum):
    if (isinstance(value, bool) or
            not isinstance(value, integer_types + (float,))):
        return None
    try:
        parsed = float(value)
        if math.isnan(parsed) or math.isinf(parsed):
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed < minimum or parsed > maximum:
        return None
    return parsed


def _valid_visible_authority_id(value):
    """Accept infrastructure authorities, never a visible player id."""
    return _exact_int(value) == WORKER_AUTHORITY_ID


def _valid_visible_authority_message(message):
    """Allow no authority while waiting or after an explicit failure."""
    if not isinstance(message, dict):
        return False
    if _valid_visible_authority_id(message.get('bot_authority_id')):
        return True
    if message.get('bot_authority_id') is not None:
        return False
    if _safe_text(message.get('phase'), '') == 'waiting':
        # A room may legitimately wait for its worker.
        # Visible clients cannot become authority, so this cannot enable a
        # player-side fallback.
        return True
    worker_status = _safe_text(message.get('worker_status'), '')
    worker_reason = _safe_text(message.get('worker_failure_reason'), '')
    if worker_status == 'failed' and worker_reason:
        return True
    return False


def _strict_projectile_source_shot(value):
    """Validate the immutable mounted-gun law carried by one launch."""
    if not isinstance(value, dict) or set(value) != {
            'speed', 'gravity', 'maxDistance', 'piercingPower', 'deadeye',
            'shell'}:
        return None
    shell = value.get('shell')
    shell_fields = set(shell) if isinstance(shell, dict) else set()
    base_shell_fields = {'kind', 'caliber', 'damage', 'explosionRadius'}
    if (not isinstance(shell, dict) or
            shell_fields not in (
                base_shell_fields,
                base_shell_fields | PROJECTILE_HE_FACTOR_FIELDS)):
        return None
    kind = shell.get('kind')
    piercing = value.get('piercingPower')
    damage = shell.get('damage')
    deadeye = value.get('deadeye')
    if (not isinstance(kind, string_types) or
            kind not in PROJECTILE_SHELL_KINDS or
            not isinstance(deadeye, bool) or
            not isinstance(piercing, list) or len(piercing) != 2 or
            not isinstance(damage, list) or len(damage) != 2):
        return None
    speed = _projectile_float_range(
        value.get('speed'), 0.000001, MAX_PROJECTILE_VELOCITY)
    gravity = _projectile_float_range(
        value.get('gravity'), 0.000001, MAX_PROJECTILE_GRAVITY)
    maximum = _projectile_float_range(
        value.get('maxDistance'), 0.000001, MAX_PROJECTILE_DISTANCE)
    piercing = [
        _projectile_float_range(component, 0.0, 10000.0)
        for component in piercing]
    caliber = _projectile_float_range(
        shell.get('caliber'), 0.000001, 1000.0)
    damage = [
        _projectile_float_range(damage[0], 0.000001, 10000.0),
        _projectile_float_range(
            damage[1], 0.0, MAX_CRITICAL_DEVICE_HP),
    ]
    radius = _projectile_float_range(
        shell.get('explosionRadius'), 0.0,
        MAX_PROJECTILE_SPLASH_RADIUS)
    he_factors = None
    if PROJECTILE_HE_FACTOR_FIELDS.issubset(shell_fields):
        he_factors = {
            'explosionDamageFactor': _projectile_float_range(
                shell.get('explosionDamageFactor'), 0.000001, 10000.0),
            'explosionDamageAbsorptionFactor': _projectile_float_range(
                shell.get('explosionDamageAbsorptionFactor'),
                0.000001, 10000.0),
            'explosionEdgeDamageFactor': _projectile_float_range(
                shell.get('explosionEdgeDamageFactor'), 0.000001, 1.0),
        }
    if (speed is None or gravity is None or maximum is None or
            any(component is None for component in piercing) or
            caliber is None or any(component is None for component in damage) or
            radius is None or
            (he_factors is not None and
             any(component is None for component in he_factors.values()))):
        return None
    result = {
        'speed': speed,
        'gravity': gravity,
        'maxDistance': maximum,
        'piercingPower': piercing,
        'deadeye': deadeye,
        'shell': {
            'kind': kind,
            'caliber': caliber,
            'damage': damage,
            'explosionRadius': radius,
        },
    }
    if he_factors is not None:
        result['shell'].update(he_factors)
    return result


def _projectile_source_shot_matches_launch(
        source_shot, velocity, gravity, max_distance, is_he,
        splash_radius):
    """Reject a duplicated launch field that disagrees with its shot law."""
    if source_shot is None or velocity is None:
        return False

    def close(left, right):
        return abs(float(left) - float(right)) <= max(
            0.001, abs(float(right)) * 0.000001)

    speed = math.sqrt(sum(component * component for component in velocity))
    shell = source_shot['shell']
    return (
        close(speed, source_shot['speed']) and
        close(gravity, source_shot['gravity']) and
        close(max_distance, source_shot['maxDistance']) and
        bool(is_he) == (shell['kind'] == 'HIGH_EXPLOSIVE') and
        close(splash_radius, shell['explosionRadius']))


def _strict_vector3(value, maximum_abs):
    """Return one detached JSON vector, rejecting tuples and coercion gaps."""
    if not isinstance(value, list) or len(value) != 3:
        return None
    result = []
    for component in value:
        parsed = _projectile_float_range(
            component, -float(maximum_abs), float(maximum_abs))
        if parsed is None:
            return None
        result.append(parsed)
    return result


def _strict_vector3_bounds(value, lows, highs):
    if (not isinstance(value, list) or len(value) != 3 or
            len(lows) != 3 or len(highs) != 3):
        return None
    result = []
    for index, component in enumerate(value):
        parsed = _projectile_float_range(
            component, float(lows[index]), float(highs[index]))
        if parsed is None:
            return None
        result.append(parsed)
    return result


def _strict_world_position(value):
    return _strict_vector3_bounds(
        value, (-5000.0, -1000.0, -5000.0),
        (5000.0, 3000.0, 5000.0))


def _strict_bot_launch_pose(value):
    """Freeze one Bot hull pose on the worker's source-time clock."""
    if not isinstance(value, (list, tuple)) or len(value) != 6:
        return None
    position = _strict_world_position(list(value[:3]))
    if position is None:
        return None
    angles = [_projectile_float_range(
        value[index], -math.pi * 2.0, math.pi * 2.0)
        for index in range(3, 6)]
    if any(angle is None for angle in angles):
        return None
    return position + angles


def _strict_launch_velocity(value):
    result = _strict_vector3(value, MAX_PROJECTILE_VELOCITY)
    if result is None:
        return None
    speed_sq = sum(component * component for component in result)
    if speed_sq <= 0.000001 or speed_sq > MAX_PROJECTILE_VELOCITY ** 2:
        return None
    return result


def _strict_capabilities(value):
    if not isinstance(value, list) or len(value) > 32:
        return None
    result = []
    for item in value:
        if not isinstance(item, string_types):
            return None
        item = _safe_text(item, '', 80)
        if not item or item in result:
            return None
        result.append(item)
    return result


def _strict_projectile_id(value):
    if (not isinstance(value, string_types) or not value or
            len(value) > 96):
        return None
    for character in value:
        if (ord(character) >= 128 or
                not (character.isalnum() or character in ':_-')):
            return None
    return value


def _strict_critical_delta(value):
    """Validate one monotonic native critical-damage delta."""
    if not isinstance(value, dict) or set(value) != {
            'devices', 'crew_ko', 'ignite'}:
        return None
    raw_devices = value.get('devices')
    raw_crew = value.get('crew_ko')
    if (not isinstance(raw_devices, (list, tuple)) or
            len(raw_devices) > 16 or
            not isinstance(raw_crew, (list, tuple)) or len(raw_crew) > 11 or
            not isinstance(value.get('ignite'), bool)):
        return None
    devices = []
    seen = set()
    for raw in raw_devices:
        if not isinstance(raw, dict) or set(raw) != {'name', 'hp_loss'}:
            return None
        name = _safe_text(raw.get('name'), '', 40)
        hp_loss = _projectile_float_range(
            raw.get('hp_loss'), 0.0, MAX_CRITICAL_DEVICE_HP)
        if (name not in CRITICAL_DELTA_DEVICE_NAMES or name in seen or
                hp_loss is None or hp_loss <= 0.0):
            return None
        seen.add(name)
        devices.append({'name': name, 'hp_loss': hp_loss})
    crew = []
    for raw in raw_crew:
        name = _safe_text(raw, '', 40)
        if name not in CRITICAL_DELTA_CREW_NAMES or name in crew:
            return None
        crew.append(name)
    return {'devices': devices, 'crew_ko': sorted(crew),
            'ignite': bool(value['ignite'])}


def _strict_projectile_effect(value):
    """Validate one terminal direct/splash damage proposal."""
    if not isinstance(value, dict):
        return None
    required = frozenset((
        'target_kind', 'target_id', 'damage', 'shot_result', 'x', 'y', 'z'))
    critical_fields = frozenset((
        'critical', 'critical_target_base_revision',
        'critical_target_ack_seq', 'hull_damage', 'critical_delta'))
    stun_fields = frozenset(('stun_end_server_time_ms',))
    target_pose_fields = frozenset(('target_x', 'target_y', 'target_z'))
    damage_sticker_fields = frozenset(('damage_sticker',))
    keys = set(value)
    if not required.issubset(keys) or not keys.issubset(
            required | critical_fields | stun_fields | target_pose_fields |
            damage_sticker_fields):
        return None
    kind = value.get('target_kind')
    target_id = _projectile_int_range(
        value.get('target_id'), 1, MAX_PROJECTILE_ID)
    damage = _projectile_int_range(value.get('damage'), 0, 5000)
    shot_result = _projectile_int_range(value.get('shot_result'), 0, 2)
    position = []
    for axis in ('x', 'y', 'z'):
        position.append(_projectile_float_range(
            value.get(axis),
            -1000.0 if axis == 'y' else -MAX_PROJECTILE_ORIGIN,
            3000.0 if axis == 'y' else MAX_PROJECTILE_ORIGIN))
    has_critical = 'critical' in value
    has_stun = 'stun_end_server_time_ms' in value
    has_target_pose = bool(keys & target_pose_fields)
    has_damage_sticker = 'damage_sticker' in value
    expected = (required |
                (critical_fields if has_critical else frozenset()) |
                (stun_fields if has_stun else frozenset()) |
                (target_pose_fields if has_target_pose else frozenset()) |
                (damage_sticker_fields if has_damage_sticker else
                 frozenset()))
    if (kind not in ('player', 'bot') or target_id is None or
            damage is None or shot_result is None or
            any(component is None for component in position) or
            (has_target_pose and has_damage_sticker) or
            keys != expected):
        return None
    result = {
        'target_kind': kind,
        'target_id': target_id,
        'damage': damage,
        'shot_result': shot_result,
        'x': position[0],
        'y': position[1],
        'z': position[2],
    }
    if has_critical:
        critical = value.get('critical')
        base_revision = _projectile_int_range(
            value.get('critical_target_base_revision'), 0,
            MAX_PROJECTILE_ID)
        ack_seq = _projectile_int_range(
            value.get('critical_target_ack_seq'), 0,
            MAX_PROJECTILE_ID)
        hull_damage = _projectile_int_range(
            value.get('hull_damage'), 0, 5000)
        critical_delta = _strict_critical_delta(value.get('critical_delta'))
        if (not isinstance(critical, dict) or base_revision is None or
                ack_seq is None or hull_damage is None or
                critical_delta is None):
            return None
        result['critical'] = critical
        result['critical_target_base_revision'] = base_revision
        result['critical_target_ack_seq'] = ack_seq
        result['hull_damage'] = hull_damage
        result['critical_delta'] = critical_delta
    if has_stun:
        stun_end = _projectile_int_range(
            value.get('stun_end_server_time_ms'), 1, MAX_PROJECTILE_ID)
        if stun_end is None:
            return None
        result['stun_end_server_time_ms'] = stun_end
    if has_damage_sticker:
        damage_sticker = _projectile_int_range(
            value.get('damage_sticker'), 0,
            MAX_PROJECTILE_DAMAGE_STICKER)
        if damage_sticker is None:
            return None
        result['damage_sticker'] = damage_sticker
    if has_target_pose:
        target_position = []
        for axis in ('x', 'y', 'z'):
            target_position.append(_projectile_float_range(
                value.get('target_' + axis),
                -1000.0 if axis == 'y' else -MAX_PROJECTILE_ORIGIN,
                3000.0 if axis == 'y' else MAX_PROJECTILE_ORIGIN))
        if any(component is None for component in target_position):
            return None
        result.update({
            'target_x': target_position[0],
            'target_y': target_position[1],
            'target_z': target_position[2],
        })
    return result


def _strict_projectile_wreck_hit(value):
    """Validate one presentation-only impact on a destroyed vehicle."""
    if not isinstance(value, dict) or set(value) != {
            'target_kind', 'target_id'}:
        return None
    kind = value.get('target_kind')
    target_id = _projectile_int_range(
        value.get('target_id'), 1, MAX_PROJECTILE_ID)
    if kind not in ('player', 'bot') or target_id is None:
        return None
    return {'target_kind': kind, 'target_id': target_id}


def _strict_projectile_destructible(value):
    """Validate one shot-created destructible receipt for ledger CAS."""
    if not isinstance(value, dict):
        return None
    required = frozenset((
        'destructible_kind', 'chunk_id', 'item_index',
        'x', 'y', 'z', 'fall_yaw', 'speed', 'is_shot'))
    optional = frozenset(('mat_kind',))
    keys = set(value)
    if not required.issubset(keys) or not keys.issubset(required | optional):
        return None
    kind = value.get('destructible_kind')
    chunk_id = _projectile_int_range(value.get('chunk_id'), 0, 4294967295)
    item_index = _projectile_int_range(value.get('item_index'), 0, 1048575)
    position = _strict_world_position([
        value.get('x'), value.get('y'), value.get('z')])
    fall_yaw = _projectile_float_range(
        value.get('fall_yaw'), -math.pi * 4.0, math.pi * 4.0)
    speed = _projectile_float_range(value.get('speed'), -200.0, 200.0)
    if (kind not in ('tree', 'column', 'fragile', 'module') or
            chunk_id is None or item_index is None or position is None or
            fall_yaw is None or speed is None or value.get('is_shot') is not True):
        return None
    result = {
        'destructible_kind': kind,
        'chunk_id': chunk_id,
        'item_index': item_index,
        'x': position[0], 'y': position[1], 'z': position[2],
        'fall_yaw': fall_yaw,
        'speed': speed,
        'is_shot': True,
    }
    if 'mat_kind' in value:
        mat_kind = _projectile_int_range(value.get('mat_kind'), 0, 65535)
        if mat_kind is None:
            return None
        result['mat_kind'] = mat_kind
    if kind == 'module' and 'mat_kind' not in result:
        return None
    return result


def _valid_active_projectiles(value, authority_epoch, server_time_ms):
    """Validate the complete server snapshot ledger without normalizing it."""
    if not isinstance(value, list) or len(value) > 256:
        return False
    expected = frozenset((
        'projectile_id', 'shooter_kind', 'shooter_id', 'shot_seq',
        'burst_group_seq', 'burst_index', 'burst_count',
        'source_vehicle', 'source_shot', 'shell_index', 'team', 'origin',
        'velocity', 'gravity', 'max_distance', 'max_time_ms', 'is_he',
        'splash_radius',
        'penetration_factor', 'launch_server_time_ms',
        'checked_through_ms', 'checked_distance', 'piercing_loss',
        'range_origin', 'segment_origin', 'segment_velocity',
        'segment_start_time_ms', 'ricochet_count',
        'base_penetration_multiplier',
        'authority_epoch'))
    seen = set()
    for projectile in value:
        if not isinstance(projectile, dict):
            return False
        projectile_id = _strict_projectile_id(
            projectile.get('projectile_id'))
        shooter_kind = projectile.get('shooter_kind')
        projectile_fields = set(projectile)
        player_intent_fields = {'fire_intent_seq', 'fire_input_seq'}
        if (projectile_fields != expected and
                projectile_fields != expected | player_intent_fields):
            return False
        source_vehicle = projectile.get('source_vehicle')
        source_shot = _strict_projectile_source_shot(
            projectile.get('source_shot'))
        shooter_id = _projectile_int_range(
            projectile.get('shooter_id'), 1, MAX_PROJECTILE_ID)
        shot_seq = _projectile_int_range(
            projectile.get('shot_seq'), 1, MAX_PROJECTILE_ID)
        burst_group = _strict_burst_group(
            shot_seq, projectile.get('burst_group_seq'),
            projectile.get('burst_index'), projectile.get('burst_count'))
        shell_index = _projectile_int_range(
            projectile.get('shell_index'), 0, 9)
        team = _projectile_int_range(projectile.get('team'), 1, 2)
        origin = _strict_world_position(projectile.get('origin'))
        velocity = _strict_launch_velocity(projectile.get('velocity'))
        range_origin = _strict_world_position(projectile.get('range_origin'))
        segment_origin = _strict_world_position(
            projectile.get('segment_origin'))
        segment_velocity = _strict_launch_velocity(
            projectile.get('segment_velocity'))
        gravity = _projectile_float_range(
            projectile.get('gravity'), 0.000001, MAX_PROJECTILE_GRAVITY)
        max_distance = _projectile_float_range(
            projectile.get('max_distance'), 0.000001,
            MAX_PROJECTILE_DISTANCE)
        max_time_ms = _projectile_int_range(
            projectile.get('max_time_ms'), 1, MAX_PROJECTILE_TIME_MS)
        splash_radius = _projectile_float_range(
            projectile.get('splash_radius'), 0.0,
            MAX_PROJECTILE_SPLASH_RADIUS)
        penetration_factor = _projectile_float_range(
            projectile.get('penetration_factor'), 0.0, 100.0)
        launch_time = _projectile_int_range(
            projectile.get('launch_server_time_ms'), 0,
            MAX_PROJECTILE_ID)
        checked_through = _projectile_int_range(
            projectile.get('checked_through_ms'), 0,
            MAX_PROJECTILE_TIME_MS)
        segment_start_time = _projectile_int_range(
            projectile.get('segment_start_time_ms'), 0,
            MAX_PROJECTILE_TIME_MS)
        ricochet_count = _projectile_int_range(
            projectile.get('ricochet_count'), 0, 1)
        base_multiplier = _projectile_float_range(
            projectile.get('base_penetration_multiplier'), 0.0, 1.0)
        checked_distance = _projectile_float_range(
            projectile.get('checked_distance'), 0.0,
            MAX_PROJECTILE_DISTANCE + 0.1)
        piercing_loss = _projectile_float_range(
            projectile.get('piercing_loss'), 0.0,
            MAX_PROJECTILE_PIERCING_LOSS)
        is_he = projectile.get('is_he')
        epoch = _projectile_int_range(
            projectile.get('authority_epoch'), 0, MAX_PROJECTILE_ID)
        fire_intent_seq = _projectile_int_range(
            projectile.get('fire_intent_seq'), 1, MAX_PROJECTILE_ID)
        fire_input_seq = _projectile_int_range(
            projectile.get('fire_input_seq'), 1, MAX_PROJECTILE_ID)
        if ((shooter_kind == 'player') !=
                player_intent_fields.issubset(projectile_fields)):
            return False
        if (projectile_id is None or projectile_id in seen or
                shooter_kind not in ('player', 'bot') or
                not isinstance(source_vehicle, string_types) or
                not source_vehicle or len(source_vehicle) > 128 or
                shooter_id is None or shot_seq is None or
                burst_group is None or
                shell_index is None or team is None or origin is None or
                velocity is None or range_origin is None or
                segment_origin is None or segment_velocity is None or
                gravity is None or
                max_distance is None or max_time_ms is None or
                not isinstance(is_he, bool) or splash_radius is None or
                not _projectile_source_shot_matches_launch(
                    source_shot, velocity, gravity, max_distance, is_he,
                    splash_radius) or
                penetration_factor is None or launch_time is None or
                launch_time > server_time_ms or checked_through is None or
                checked_through > max_time_ms or checked_distance is None or
                checked_distance > max_distance + 0.1 or
                segment_start_time is None or
                segment_start_time > checked_through or
                (ricochet_count == 1 and
                 segment_start_time >= max_time_ms) or
                ricochet_count is None or base_multiplier is None or
                piercing_loss is None or epoch != authority_epoch or
                (shooter_kind == 'player' and
                 (fire_intent_seq is None or fire_input_seq is None))):
            return False
        if ricochet_count == 0:
            if (segment_start_time != 0 or segment_origin != origin or
                    segment_velocity != velocity or base_multiplier != 1.0):
                return False
        else:
            shell_kind = (source_shot.get('shell') or {}).get('kind')
            expected_multiplier = (
                0.75 if shell_kind in (
                    'ARMOR_PIERCING', 'ARMOR_PIERCING_CR') else
                1.0 if shell_kind == 'HOLLOW_CHARGE' else None)
            if (expected_multiplier is None or
                    base_multiplier != expected_multiplier):
                return False
        seen.add(projectile_id)
    return True


def _valid_battle_receipt(message):
    """Validate the bounded JSON receipt before it reaches persistent state."""
    if not isinstance(message, dict):
        return False
    receipt_id = _safe_text(message.get('receipt_id'), '', 97)
    account_key = _safe_text(message.get('account_key'), '', 65)
    player_name = _safe_text(message.get('player_name'), '', 33)
    vehicle = _safe_text(message.get('vehicle'), '', 97)
    map_name = _safe_text(message.get('map'), '', 97)
    arena_unique_id = _exact_int(message.get('arena_unique_id'))
    round_id = _exact_int(message.get('round_id'))
    player_id = _exact_int(message.get('player_id'))
    team = _exact_int(message.get('team'))
    winner = _exact_int(message.get('winner'))
    duration = _exact_int(message.get('duration'))
    if (not receipt_id or len(receipt_id) > 96 or not account_key or
            len(account_key) > 64 or not player_name or
            len(player_name) > 32 or not vehicle or len(vehicle) > 96 or
            not map_name or len(map_name) > 96 or
            arena_unique_id is None or arena_unique_id < 0 or
            round_id is None or round_id < 1 or team not in (1, 2) or
            player_id is None or not 1 <= player_id <= MAX_PROJECTILE_ID or
            winner not in (0, 1, 2) or duration is None or duration < 0 or
            not isinstance(message.get('premature_leave'), bool)):
        return False
    stats = message.get('stats')
    rewards = message.get('rewards')
    stat_names = (
        'shots', 'direct_hits', 'piercings', 'damage', 'damage_received',
        'damage_blocked', 'assist_track', 'assist_radio', 'assist_stun',
        'kills', 'spotted', 'capture_points', 'dropped_capture_points')
    reward_names = ('credits', 'xp', 'free_xp', 'repair_cost', 'ammo_cost')
    if not isinstance(stats, dict) or not isinstance(rewards, dict):
        return False
    if any(_exact_int(stats.get(name)) is None or
           _exact_int(stats.get(name)) < 0 for name in stat_names):
        return False
    if any(_exact_int(rewards.get(name)) is None or
           _exact_int(rewards.get(name)) < 0 for name in reward_names):
        return False
    if rewards.get('repair_cost') != 0 or rewards.get('ammo_cost') != 0:
        return False
    public_rows = message.get('public_results')
    if (not isinstance(public_rows, list) or
            not 1 <= len(public_rows) <= 30):
        return False
    seen = set()
    row_teams = {}
    personal_row = None
    for row in public_rows:
        if not isinstance(row, dict):
            return False
        actor_kind = row.get('actor_kind')
        actor_id = _exact_int(row.get('actor_id'))
        row_team = _exact_int(row.get('team'))
        health = _exact_int(row.get('health'))
        death_reason = _exact_int(row.get('death_reason'))
        xp = _exact_int(row.get('xp'))
        killer_kind = row.get('killer_kind', '')
        killer_id = _exact_int(row.get('killer_id', 0))
        identity = (actor_kind, actor_id)
        name = _safe_text(row.get('name'), '', 33)
        row_vehicle = _safe_text(row.get('vehicle'), '', 97)
        row_stats = row.get('stats')
        if (actor_kind not in ('player', 'bot') or actor_id is None or
                not 1 <= actor_id <= MAX_PROJECTILE_ID or
                identity in seen or row_team not in (1, 2) or
                health is None or health < 0 or
                death_reason is None or not -1 <= death_reason <= 255 or
                xp is None or xp < 0 or not name or len(name) > 32 or
                not row_vehicle or len(row_vehicle) > 96 or
                not isinstance(row.get('is_team_killer'), bool) or
                killer_kind not in ('', 'player', 'bot') or
                killer_id is None or killer_id < 0 or
                bool(killer_kind) != bool(killer_id) or
                not isinstance(row_stats, dict)):
            return False
        if any(_exact_int(row_stats.get(stat_name)) is None or
               _exact_int(row_stats.get(stat_name)) < 0
               for stat_name in stat_names):
            return False
        seen.add(identity)
        row_teams[identity] = row_team
        if identity == ('player', player_id):
            personal_row = row
    interactions = message.get('interactions', [])
    if (not isinstance(interactions, list) or
            len(interactions) > len(public_rows)):
        return False
    interaction_keys = set(RESULT_INTERACTION_LIMITS) | {
        'target_kind', 'target_id'}
    interaction_targets = set()
    for interaction in interactions:
        if (not isinstance(interaction, dict) or
                set(interaction) != interaction_keys):
            return False
        target = (
            interaction.get('target_kind'),
            _exact_int(interaction.get('target_id')))
        if (target not in seen or target in interaction_targets or
                target == ('player', player_id) or
                row_teams[target] == team):
            return False
        for name, (minimum, maximum) in RESULT_INTERACTION_LIMITS.items():
            field = _exact_int(interaction.get(name))
            if field is None or field < minimum or field > maximum:
                return False
        interaction_targets.add(target)
    return bool(
        personal_row is not None and
        personal_row.get('name') == message.get('player_name') and
        personal_row.get('vehicle') == message.get('vehicle') and
        personal_row.get('team') == message.get('team') and
        personal_row.get('death_reason') == message.get('death_reason') and
        personal_row.get('xp') == rewards.get('xp') and
        personal_row.get('stats') == stats)


def _canonical_wire_outfits(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > len(OUTFIT_SEASONS):
        return None
    result = {}
    total = 0
    for raw_season, encoded in value.items():
        try:
            season = int(raw_season)
        except (TypeError, ValueError):
            return None
        if (season not in OUTFIT_SEASONS or
                not isinstance(encoded, string_types)):
            return None
        try:
            ascii_value = encoded.encode('ascii')
            raw = base64.b64decode(ascii_value)
            canonical = base64.b64encode(raw).decode('ascii')
        except Exception:
            return None
        if canonical != encoded or not raw or len(raw) > MAX_OUTFIT_BYTES:
            return None
        total += len(raw)
        if total > MAX_OUTFIT_BYTES * len(OUTFIT_SEASONS):
            return None
        result[str(season)] = canonical
    return result


def _canonical_vehicle_compact_descr(value):
    """Return one canonical base64 mounted vehicle descriptor."""
    if not isinstance(value, string_types) or not value:
        return None
    try:
        ascii_value = value.encode('ascii')
        raw = base64.b64decode(ascii_value)
        canonical = base64.b64encode(raw).decode('ascii')
    except Exception:
        return None
    if (canonical != value or not raw or
            len(raw) > MAX_VEHICLE_COMPACT_BYTES):
        return None
    return canonical


def _canonical_effective_params(value):
    return effective_params_wire.canonical(value)


def _load_bigworld():
    import BigWorld
    return BigWorld


class LANClient(object):

    def __init__(self, host, port, name, vehicle, max_health=100,
                 on_event=None, bigworld=None, account_key=None,
                 outfits=None, requested_team=0,
                 vehicle_compact_descr=None, effective_params=None):
        self.host = _safe_text(host, '127.0.0.1', 255)
        self.port = int(port or 28782)
        self.name = _safe_text(name, 'Player')
        self.vehicle = _safe_text(vehicle, 'ussr:R11_MS-1')
        self.max_health = max(1, int(max_health or 100))
        self.account_key = _safe_text(
            account_key, uuid.uuid4().hex, 64)
        self.outfits = _canonical_wire_outfits(outfits or {}) or {}
        self.vehicle_compact_descr = (
            _canonical_vehicle_compact_descr(vehicle_compact_descr) or '')
        self.effective_params = _canonical_effective_params(effective_params)
        self.requested_team = _team_choice(requested_team)
        self._published_player_outfits = {}
        self._published_player_effective_params = {}
        self.on_event = on_event
        self.bigworld = bigworld
        self.sock = None
        self.thread = None
        self.running = False
        self.connected = False
        self.ready = False
        self.phase = 'disconnected'
        self.player_id = None
        self.team = None
        self.team_sizes = {1: 15, 2: 15}
        self.bot_tier_mode = 'random'
        self.slot = 0
        self.map_name = None
        self.map_pool = []
        self.spawn = None
        self.round_id = None
        self.state_revision = None
        self._battle_start_round_id = None
        self._battle_live_round_id = None
        self.roster = []
        self.host_player_id = None
        self.bot_authority_id = None
        self.authority_epoch = None
        self.server_time_ms = None
        self.capabilities = []
        self.server_capabilities = []
        self._schema_negotiated = False
        self.last_snapshot = None
        self.last_error = None
        self.rtt_ms = None
        self.minimum_rtt_ms = None
        self.combat_phase = 'loading'
        self.combat_deadline = None
        self.combat_end_deadline = None
        self.combat_duration = 900.0
        self._combat_timing_round_id = None
        self._combat_timing_tick = -1
        self._recv_buffer = u''
        self._pending = []
        self._pending_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._outbound_lock = threading.Lock()
        self._outbound_event = threading.Event()
        self._outbound_queue = []
        self._outbound_bytes = 0
        self._outbound_seq = 0
        self._outbound_accepting = False
        self._sender_thread = None
        self._transport_generation = 0
        self._stopping = False
        self._poll_callback = None
        self._last_ping = 0.0
        self._ping_seq = 0
        self._fire_seq = 0
        self._fire_intent_seq = 0
        self._equipment_intent_seq = 0
        self._input_seq = 0
        self._input_seq_round = None
        self._landing_observation_seq = 0
        self._landing_observation_round = None
        self._landing_observation_pending = None
        self._landing_observation_queue = []
        self._projectile_lock = threading.Lock()
        self._runtime_drop_diagnostics = {}

    def start(self):
        with self._outbound_lock:
            if self.running:
                return False
            self._transport_generation += 1
            generation = self._transport_generation
            self._outbound_queue = []
            self._outbound_bytes = 0
            self._outbound_seq = 0
            self._outbound_accepting = False
            self._stopping = False
            self.running = True
            self.connected = False
            self.last_error = None
            self.phase = 'connecting'
            self.capabilities = []
            self.server_capabilities = []
            self._schema_negotiated = False
            self.authority_epoch = None
            self.server_time_ms = None
            self.rtt_ms = None
            self.minimum_rtt_ms = None
            self._input_seq = 0
            self._input_seq_round = None
            self._landing_observation_seq = 0
            self._landing_observation_round = None
            self._landing_observation_pending = None
            self._landing_observation_queue = []
            self._runtime_drop_diagnostics = {}
        with self._pending_lock:
            self._pending = []
            self._recv_buffer = u''
        self._outbound_event.clear()
        self.thread = threading.Thread(
            target=self._worker, args=(generation,),
            name='offline-lan-0922')
        self.thread.setDaemon(True)
        self.thread.start()
        self._schedule_poll()
        return True

    def _hello_payload(self):
        """Return the player hello sent by the transport worker.

        A simulation worker overrides this one construction boundary.  Keeping
        the ordinary payload here prevents the opt-in role from changing the
        player identity fields.  Capability negotiation separately fences the
        schema-5 destructible and optional projectile/map wire extensions.
        """
        effective_params = _canonical_effective_params(self.effective_params)
        if effective_params is None:
            raise ValueError('effective vehicle parameters are unavailable')
        payload = {
            'type': 'hello',
            'protocol': PROTOCOL_VERSION,
            'client_build': CLIENT_BUILD,
            'capabilities': list(CLIENT_CAPABILITIES),
            'name': self.name,
            'vehicle': self.vehicle,
            'max_health': self.max_health,
            'account_key': self.account_key,
            'outfits': dict(self.outfits),
            'vehicle_compact_descr': self.vehicle_compact_descr,
            'effective_params': effective_params,
        }
        if self.requested_team in (1, 2):
            payload['requested_team'] = self.requested_team
        return payload

    def stop(self):
        with self._outbound_lock:
            if (not self.running and self.sock is None and
                    self._poll_callback is None and
                    self._sender_thread is None):
                return
            generation = self._transport_generation
            sender_thread = self._sender_thread
            receive_thread = self.thread
            sock = self.sock
            was_connected = self.connected
            self._stopping = True
            self._outbound_accepting = False
            self.running = False
            self.connected = False
            self.ready = False
            self.phase = 'disconnected'
        self._outbound_event.set()

        # Leave must not sit behind stale state.  Send it synchronously when
        # the sender is not already back-pressured; never wait for that lock.
        acquired = False
        try:
            acquired = self._send_lock.acquire(False)
            if (acquired and was_connected and sock is not None):
                with self._outbound_lock:
                    may_leave = (
                        generation == self._transport_generation and
                        self.sock is sock and self._stopping)
                if may_leave:
                    try:
                        sock.settimeout(LEAVE_SEND_TIMEOUT)
                    except Exception:
                        pass
                    sock.sendall(LEAVE_PAYLOAD)
        except Exception:
            pass
        finally:
            if acquired:
                self._send_lock.release()

        if self._poll_callback is not None and self.bigworld is not None:
            try:
                self.bigworld.cancelCallback(self._poll_callback)
            except Exception:
                pass
            self._poll_callback = None
        with self._pending_lock:
            self._pending = []
            self._recv_buffer = u''
        try:
            if sock is not None:
                sock.close()
        except Exception:
            pass
        with self._outbound_lock:
            if (generation == self._transport_generation and
                    self.sock is sock):
                self.sock = None
        current = threading.current_thread()
        for worker in (sender_thread, receive_thread):
            if worker is not None and worker is not current:
                try:
                    worker.join(SENDER_JOIN_TIMEOUT)
                except Exception:
                    pass
        with self._outbound_lock:
            if generation == self._transport_generation:
                self._outbound_queue = []
                self._outbound_bytes = 0
                self._outbound_accepting = False
                if (sender_thread is not None and
                        self._sender_thread is sender_thread):
                    is_alive = getattr(sender_thread, 'is_alive', None)
                    if is_alive is None:
                        is_alive = sender_thread.isAlive
                    if not is_alive():
                        self._sender_thread = None

    def request_start(self, map_name=None):
        if (not self.ready or self.phase != 'waiting' or
                self.player_id != self.host_player_id):
            return False
        message = {'type': 'start_battle', 'round_id': self.round_id}
        if map_name:
            map_name = _safe_text(map_name, '', 80)
            if (map_name == RANDOM_MAP_OPTION and
                    not self.has_random_map()):
                return False
            if (self.map_pool and map_name not in self.map_pool and
                    map_name != RANDOM_MAP_OPTION):
                return False
            message['map'] = map_name
        return self._send(message)

    def select_vehicle(self, vehicle, max_health, outfits=None,
                       vehicle_compact_descr=None, effective_params=None):
        """Publish one waiting-room garage change for the next round."""
        if not self.ready or self.phase != 'waiting':
            return False
        vehicle = _safe_text(vehicle, '', 64)
        max_health = _exact_int(max_health)
        if not vehicle or max_health is None or max_health < 1:
            return False
        publishes_outfits = outfits is not None
        outfits = (_canonical_wire_outfits(outfits)
                   if publishes_outfits else self.outfits)
        compact = _canonical_vehicle_compact_descr(
            self.vehicle_compact_descr if vehicle_compact_descr is None
            else vehicle_compact_descr)
        params = _canonical_effective_params(
            self.effective_params if effective_params is None
            else effective_params)
        if outfits is None or compact is None or params is None:
            return False
        if (vehicle == self.vehicle and max_health == self.max_health and
                outfits == self.outfits and
                compact == self.vehicle_compact_descr and
                params == self.effective_params):
            return False
        message = {'type': 'select_vehicle', 'vehicle': vehicle,
                   'max_health': max_health,
                   'vehicle_compact_descr': compact,
                   'effective_params': params}
        if publishes_outfits:
            message['outfits'] = outfits
        if not self._send(message):
            return False
        self.vehicle_compact_descr = compact
        self.effective_params = params
        return True

    def select_team(self, team):
        """Request a waiting-room team; the server owns the capacity check."""
        team = _team_choice(team, None)
        if (not self.ready or self.phase != 'waiting' or
                team not in (1, 2) or not self.has_team_selection()):
            return False
        if team == self.team:
            return True
        return self._send({'type': 'select_team', 'team': team})

    def set_team_size(self, team, size):
        """Request one live waiting-room capacity as the elected host."""
        team = _team_choice(team, None)
        size = _exact_int(size)
        if (not self.ready or self.phase != 'waiting' or
                self.player_id != self.host_player_id or
                team not in (1, 2) or size is None or not 1 <= size <= 15 or
                not self.has_team_size_selection()):
            return False
        return self._send({
            'type': 'set_team_size', 'team': team, 'size': size})

    def set_bot_tier_mode(self, mode):
        """Ask the waiting-room server to change the next Bot lineup."""
        if (not self.ready or self.phase != 'waiting' or
                self.player_id != self.host_player_id or
                mode not in BOT_TIER_MODES):
            return False
        return self._send({'type': 'set_bot_tier_mode', 'mode': mode})

    def _adopt_published_vehicle(self, players):
        """Track the vehicle and HP the server holds for this client."""
        for entry in players or ():
            if _exact_int(entry.get('id')) != self.player_id:
                continue
            vehicle = _safe_text(entry.get('vehicle'), '', 64)
            max_health = _exact_int(entry.get('max_health'))
            if vehicle:
                self.vehicle = vehicle
            if max_health is not None and max_health > 0:
                self.max_health = max_health
            team = _exact_int(entry.get('team'))
            slot = _exact_int(entry.get('slot'))
            if team in (1, 2):
                self.team = team
            if slot is not None and 0 <= slot < 15:
                self.slot = slot
            outfits = _canonical_wire_outfits(entry.get('outfits'))
            if outfits is not None:
                self.outfits = outfits
            compact = _canonical_vehicle_compact_descr(
                entry.get('vehicle_compact_descr'))
            if compact is not None:
                self.vehicle_compact_descr = compact
            params = _canonical_effective_params(
                entry.get('effective_params'))
            if params is not None:
                self.effective_params = params
            return

    def _remember_player_outfits(self, players):
        """Canonicalize static player inputs and inherit lean snapshots."""
        result = []
        for raw in players or ():
            entry = dict(raw)
            player_id = _exact_int(entry.get('id'))
            if 'outfits' in entry:
                outfits = _canonical_wire_outfits(entry.get('outfits'))
                if outfits is not None and player_id is not None:
                    self._published_player_outfits[player_id] = outfits
                    entry['outfits'] = outfits
            elif player_id in self._published_player_outfits:
                entry['outfits'] = dict(
                    self._published_player_outfits[player_id])
            if 'effective_params' in entry:
                params = _canonical_effective_params(
                    entry.get('effective_params'))
                if params is not None and player_id is not None:
                    self._published_player_effective_params[player_id] = params
                    entry['effective_params'] = params
            elif player_id in self._published_player_effective_params:
                entry['effective_params'] = _canonical_effective_params(
                    self._published_player_effective_params[player_id])
            result.append(entry)
        return result

    def is_room_host(self):
        return (self.ready and self.phase == 'waiting' and
                self.player_id is not None and
                self.player_id == self.host_player_id)

    def leave_battle(self):
        """Retire this player from the current round without closing TCP."""
        if not self.ready or self.phase not in ('loading', 'battle'):
            return False
        return self._send({
            'type': 'leave_battle',
            'round_id': self.round_id,
        })

    def send_input(self, forward, turn, aim_yaw=0.0, gun_pitch=0.0,
                   position=None, yaw=None, fire_seq=0,
                   speed=None,
                   shell_index=None,
                   next_shell_index=None,
                   shell_change_pending=None,
                   pose_time_us=None,
                   ram_contacts=None,
                   destructible_contacts=None,
                   siege_enabled=None,
                   pitch=None, roll=None,
                   gun_checkpoint=None, up_cosine=None):
        if not self.ready or self.phase != 'battle':
            return False
        if self._input_seq_round != self.round_id:
            self._input_seq_round = self.round_id
            self._input_seq = 0
        if self._landing_observation_round != self.round_id:
            self._landing_observation_round = self.round_id
            self._landing_observation_seq = 0
            self._landing_observation_pending = None
            self._landing_observation_queue = []
        next_input_seq = self._input_seq + 1
        if next_input_seq > MAX_PROJECTILE_ID:
            # The server cannot represent another ordered identity in this
            # round.  Do not queue MAX+1 and move the local frontier into a
            # permanent sequence gap; the next round resets both sides.
            return False
        parsed_fire_seq = _projectile_int_range(
            max(0, int(fire_seq or 0)), 0, MAX_PROJECTILE_ID)
        if parsed_fire_seq is None:
            return False
        message = {
            'type': 'input',
            'round_id': self.round_id,
            'forward': max(-1.0, min(1.0, _finite_float(forward))),
            'turn': max(-1.0, min(1.0, _finite_float(turn))),
            # Periodic: hull yaw plus turret yaw can leave the principal
            # interval, so report the equivalent canonical angle.
            'aim_yaw': _canonical_angle(aim_yaw),
            # Not periodic.  The gun elevation request is bounded by the wire
            # envelope, which is far wider than any #1513 gun's descriptor
            # pitch limits; VehicleGunRotator still owns the exact per-vehicle
            # limits, so no reachable angle is altered here.
            'gun_pitch': max(
                -MAX_PLAYER_GUN_PITCH,
                min(MAX_PLAYER_GUN_PITCH, _finite_float(gun_pitch))),
            'fire_seq': parsed_fire_seq,
        }
        timeline_enabled = bool(
            HUMAN_RAM_TIMELINE_CAPABILITY in self.capabilities and
            HUMAN_RAM_TIMELINE_CAPABILITY in self.server_capabilities)
        if timeline_enabled:
            message['input_seq'] = next_input_seq
        if position is not None and len(position) >= 3:
            coordinates = [
                _finite_float(position[index]) for index in range(3)]
            if any(abs(coordinate) > bound for coordinate, bound in
                   zip(coordinates, PLAYER_INPUT_WORLD_BOUNDS)):
                # No #1513 map reaches this envelope.  Fabricating a clamped
                # world pose would report a position the vehicle is not in, so
                # drop the frame locally instead; the ordered sequence is
                # committed only after the frame is queued, so nothing later
                # is blocked.
                return False
            message['x'], message['y'], message['z'] = coordinates
            message['yaw'] = _canonical_angle(yaw)
            if pitch is not None:
                message['pitch'] = max(
                    -MAX_PLAYER_INPUT_ATTITUDE,
                    min(MAX_PLAYER_INPUT_ATTITUDE, _finite_float(pitch)))
            if roll is not None:
                message['roll'] = max(
                    -MAX_PLAYER_INPUT_ATTITUDE,
                    min(MAX_PLAYER_INPUT_ATTITUDE, _finite_float(roll)))
            if up_cosine is not None:
                if (isinstance(up_cosine, bool) or
                        not isinstance(up_cosine, integer_types + (float,))):
                    return False
                parsed_up_cosine = _finite_float(up_cosine, 2.0)
                if not -1.0 <= parsed_up_cosine <= 1.0:
                    return False
                message['up_cosine'] = parsed_up_cosine
            if timeline_enabled and pose_time_us is not None:
                parsed_pose_time = _exact_int(pose_time_us)
                if (parsed_pose_time is None or
                        not 0 <= parsed_pose_time <= MAX_MOTION_TIME_US):
                    return False
                message['pose_time_us'] = parsed_pose_time
        if speed is not None:
            message['speed'] = max(
                -MAX_PLAYER_INPUT_SPEED,
                min(MAX_PLAYER_INPUT_SPEED, _finite_float(speed)))
        if shell_index is not None:
            parsed_shell = _projectile_int_range(shell_index, 0, 9)
            if parsed_shell is None:
                return False
            message['shell_index'] = parsed_shell
        has_next_shell = next_shell_index is not None
        has_shell_pending = shell_change_pending is not None
        if has_next_shell != has_shell_pending:
            return False
        if has_next_shell:
            parsed_next_shell = _projectile_int_range(
                next_shell_index, 0, 9)
            if (parsed_next_shell is None or
                    not isinstance(shell_change_pending, bool)):
                return False
            message['next_shell_index'] = parsed_next_shell
            message['shell_change_pending'] = shell_change_pending
        checkpoint_required = bool(
            PLAYER_FIRE_INTENT_CAPABILITY in self.capabilities and
            PLAYER_FIRE_INTENT_CAPABILITY in self.server_capabilities)
        parsed_checkpoint = _canonical_human_gun_checkpoint(
            gun_checkpoint)
        if checkpoint_required and parsed_checkpoint is None:
            return False
        if gun_checkpoint is not None:
            if parsed_checkpoint is None:
                return False
            if (not timeline_enabled or 'shell_index' not in message or
                    'next_shell_index' not in message or
                    'shell_change_pending' not in message):
                return False
            message['gun_checkpoint'] = parsed_checkpoint
        if isinstance(ram_contacts, list):
            message['ram_contacts'] = [
                dict(value) for value in ram_contacts[
                    :MAX_PLAYER_RAM_CONTACTS]
                if isinstance(value, dict)]
        if isinstance(destructible_contacts, list):
            message['destructible_contacts'] = [
                dict(value) for value in destructible_contacts[
                    :MAX_PLAYER_DESTRUCTIBLE_CONTACTS]
                if isinstance(value, dict)]
        if siege_enabled is not None:
            if not isinstance(siege_enabled, bool):
                raise ValueError('siege_enabled must be BOOL')
            message['siege_enabled'] = siege_enabled
        if not self._send(message):
            return False
        self._input_seq = next_input_seq
        pending_landing = self._landing_observation_pending
        if (isinstance(pending_landing, dict) and
                pending_landing.get('retry_on_input', False) and
                not pending_landing.get('sent', False)):
            if pending_landing.get('refresh_input_on_retry', False):
                pending_landing['input_seq'] = int(self._input_seq)
                pending_landing.pop('wire', None)
            self._send_pending_landing_observation(rebind=True)
        return True

    def send_track_repair(self, tracks, base_revision, repair_seq):
        """Publish one versioned, track-only repair checkpoint.

        Damage remains server/worker authoritative.  The visible #1513
        process alone owns the mounted crew/loadout repair timer, so this
        narrow message donates only monotonic left/right-track repair facts.
        """
        if not self.ready or self.phase != 'battle':
            return False
        base_revision = _projectile_int_range(
            base_revision, 0, MAX_PROJECTILE_ID)
        repair_seq = _projectile_int_range(
            repair_seq, 1, MAX_PROJECTILE_ID)
        if (base_revision is None or repair_seq is None or
                not isinstance(tracks, (list, tuple)) or
                not 1 <= len(tracks) <= 2):
            return False
        rows = []
        seen = set()
        for raw in tracks:
            if (not isinstance(raw, dict) or
                    set(raw) != set(('name', 'hp', 'max_hp', 'state'))):
                return False
            name = str(raw.get('name', ''))
            state = str(raw.get('state', ''))
            hp = _finite_float(raw.get('hp'), -1.0)
            maximum = _finite_float(raw.get('max_hp'), -1.0)
            if (name not in ('leftTrackHealth', 'rightTrackHealth') or
                    name in seen or state not in ('destroyed', 'critical') or
                    maximum <= 0.0 or hp < 0.0 or hp > maximum):
                return False
            seen.add(name)
            rows.append({
                'name': name,
                'hp': round(hp, 3),
                'max_hp': round(maximum, 3),
                'state': state,
            })
        return self._send({
            'type': 'track_repair',
            'round_id': self.round_id,
            'critical_base_revision': base_revision,
            'repair_seq': repair_seq,
            'tracks': rows,
        })

    def send_player_environment(self, observations, sample_seq):
        """Publish bounded environment classifications from the worker."""
        sequence = _projectile_int_range(
            sample_seq, 1, MAX_PROJECTILE_ID)
        epoch = _projectile_int_range(
            self.authority_epoch, 0, MAX_PROJECTILE_ID)
        if (self.phase != 'battle' or not self.is_bot_authority() or
                self.player_id != WORKER_AUTHORITY_ID or
                sequence is None or epoch is None or
                PLAYER_ENVIRONMENT_CAPABILITY not in self.capabilities or
                PLAYER_ENVIRONMENT_CAPABILITY not in
                self.server_capabilities or
                not isinstance(observations, (list, tuple)) or
                len(observations) > 30):
            return False
        rows = []
        seen = set()
        for raw in observations:
            if (not isinstance(raw, dict) or
                    set(raw) not in (
                        {'player_id', 'input_seq', 'level'},
                        {'player_id', 'input_seq', 'level',
                         'drowning_critical'})):
                return False
            player_id = _projectile_int_range(
                raw.get('player_id'), 1, MAX_PROJECTILE_ID)
            input_seq = _projectile_int_range(
                raw.get('input_seq'), 0, MAX_PROJECTILE_ID)
            level = _projectile_int_range(raw.get('level'), 0, 2)
            if (player_id is None or input_seq is None or level is None or
                    player_id in seen):
                return False
            seen.add(player_id)
            row = {
                'player_id': player_id,
                'input_seq': input_seq,
                'level': level,
            }
            if 'drowning_critical' in raw:
                if level != 2 or not isinstance(raw['drowning_critical'], dict):
                    return False
                row['drowning_critical'] = raw['drowning_critical']
            rows.append(row)
        return self._send({
            'type': 'player_environment',
            'round_id': self.round_id,
            'authority_epoch': epoch,
            'sample_seq': sequence,
            'observations': rows,
        })

    def _send_pending_landing_observation(self, rebind=False):
        pending = self._landing_observation_pending
        if not isinstance(pending, dict):
            return False
        if (self.phase != 'battle' or self.round_id is None or
                self.authority_epoch is None or
                self._input_seq_round != self.round_id or
                self._input_seq <= 0):
            return False
        wire = pending.get('wire')
        if rebind or not isinstance(wire, dict):
            wire = {
                'type': 'landing_observation',
                'round_id': self.round_id,
                'authority_epoch': int(self.authority_epoch),
                'observation_seq': int(pending['observation_seq']),
                'input_seq': int(pending['input_seq']),
                'impact_speed': float(pending['impact_speed']),
            }
            pending['wire'] = wire
        sent = bool(self._send(dict(wire)))
        pending['sent'] = sent
        if sent:
            pending.pop('retry_on_input', None)
            pending.pop('refresh_input_on_retry', None)
        return sent

    def send_landing_observation(self, impact_speed):
        """Publish one physical landing observation, never a damage verdict."""
        if (isinstance(impact_speed, bool) or
                not isinstance(impact_speed, integer_types + (float,))):
            return False
        impact_speed = float(impact_speed)
        if (math.isnan(impact_speed) or math.isinf(impact_speed) or
                not 0.0 <= impact_speed <=
                PLAYER_LANDING_MAX_IMPACT_SPEED or
                self.phase != 'battle' or not self.ready or
                self.player_id is None or self.player_id <= 0 or
                PLAYER_ENVIRONMENT_CAPABILITY not in self.capabilities or
                PLAYER_ENVIRONMENT_CAPABILITY not in
                self.server_capabilities):
            return False
        if self._landing_observation_round != self.round_id:
            self._landing_observation_round = self.round_id
            self._landing_observation_seq = 0
            self._landing_observation_pending = None
            self._landing_observation_queue = []
        if self._landing_observation_pending is not None:
            pending = self._landing_observation_pending
            if not pending.get('reported', False):
                if not pending.get('sent', False):
                    pending['input_seq'] = int(self._input_seq)
                    pending.pop('wire', None)
                    if not self._send_pending_landing_observation(
                            rebind=True):
                        return False
                pending['reported'] = True
                return int(pending['observation_seq'])
            if len(self._landing_observation_queue) >= \
                    MAX_LANDING_OBSERVATION_QUEUE:
                return False
            self._landing_observation_queue.append({
                'input_seq': int(self._input_seq),
                'impact_speed': round(impact_speed, 6),
            })
            if (not pending.get('sent', False) and
                    pending.get('retry_on_input', False)):
                if pending.get('refresh_input_on_retry', False):
                    pending['input_seq'] = int(self._input_seq)
                    pending.pop('wire', None)
                self._send_pending_landing_observation(rebind=True)
            return int(pending['observation_seq'])
        pending = {
            'observation_seq': self._landing_observation_seq + 1,
            'input_seq': int(self._input_seq),
            'impact_speed': round(impact_speed, 6),
            'sent': False,
            'reported': False,
        }
        self._landing_observation_pending = pending
        if not self._send_pending_landing_observation():
            return False
        pending['reported'] = True
        return int(pending['observation_seq'])

    def _start_queued_landing_observation(self):
        if not self._landing_observation_queue:
            return False
        queued = self._landing_observation_queue.pop(0)
        pending = {
            'observation_seq': self._landing_observation_seq + 1,
            'input_seq': int(queued['input_seq']),
            'impact_speed': float(queued['impact_speed']),
            'sent': False,
            'reported': True,
        }
        self._landing_observation_pending = pending
        sent = self._send_pending_landing_observation()
        if not sent:
            pending['retry_on_input'] = True
        return sent

    def _adopt_player_input_frontier(self, players):
        """Recover input and landing sequence frontiers from a snapshot."""
        own = next((row for row in players or ()
                    if _exact_int(row.get('id')) == self.player_id), None)
        if own is None:
            return False
        input_seq = _projectile_int_range(
            own.get('input_seq'), 0, MAX_PROJECTILE_ID)
        landing_seq = _projectile_int_range(
            own.get('landing_observation_seq'), 0, MAX_PROJECTILE_ID)
        if input_seq is None or landing_seq is None:
            return False
        if 'input_processed_seq' in own:
            # The server's terminal frontier can run ahead of its last applied
            # input when one recoverable frame was rejected.  Resume from the
            # next eligible sequence so a reconnect never retries an
            # identifier whose decision is already terminal.
            processed_seq = _projectile_int_range(
                own.get('input_processed_seq'), 0, MAX_PROJECTILE_ID)
            if processed_seq is None or processed_seq < input_seq:
                return False
            input_seq = processed_seq
        if self._input_seq_round != self.round_id:
            self._input_seq_round = self.round_id
            self._input_seq = input_seq
        else:
            self._input_seq = max(self._input_seq, input_seq)
        if self._landing_observation_round != self.round_id:
            self._landing_observation_round = self.round_id
            self._landing_observation_seq = landing_seq
            self._landing_observation_pending = None
            self._landing_observation_queue = []
            return True
        self._landing_observation_seq = max(
            self._landing_observation_seq, landing_seq)
        pending = self._landing_observation_pending
        if (isinstance(pending, dict) and
                int(pending['observation_seq']) <= landing_seq):
            self._landing_observation_pending = None
            self._start_queued_landing_observation()
        elif (isinstance(pending, dict) and
              isinstance(pending.get('wire'), dict) and
              int(pending['wire'].get('authority_epoch', -1)) !=
              int(self.authority_epoch)):
            pending['observation_seq'] = self._landing_observation_seq + 1
            pending.pop('wire', None)
            pending['sent'] = False
            pending['retry_on_input'] = True
            self._send_pending_landing_observation(rebind=True)
        return True

    def _handle_landing_observation_result(self, message):
        required = {
            'type', 'round_id', 'authority_epoch', 'observation_seq',
            'input_seq', 'committed_seq', 'accepted', 'reason'}
        if not isinstance(message, dict):
            return False
        fields = set(message)
        fields.discard('_client_received_time')
        if fields != required:
            return False
        authority_epoch = _projectile_int_range(
            message.get('authority_epoch'), 0, MAX_PROJECTILE_ID)
        sequence = _projectile_int_range(
            message.get('observation_seq'), 1, MAX_PROJECTILE_ID)
        input_seq = _projectile_int_range(
            message.get('input_seq'), 1, MAX_PROJECTILE_ID)
        committed = _projectile_int_range(
            message.get('committed_seq'), 0, MAX_PROJECTILE_ID)
        accepted = message.get('accepted')
        reason = _safe_text(message.get('reason'), '', 32)
        retryable = ('stale_authority', 'sequence_gap', 'stale_input')
        terminal = ('identity_conflict', 'player_dead', 'not_active')
        if (message.get('round_id') != self.round_id or
                authority_epoch is None or sequence is None or
                input_seq is None or committed is None or
                not isinstance(accepted, bool) or
                (accepted and (reason or committed != sequence)) or
                (not accepted and reason not in retryable + terminal) or
                (self.authority_epoch is not None and
                 authority_epoch < self.authority_epoch)):
            return False
        self.authority_epoch = authority_epoch
        self._landing_observation_seq = max(
            self._landing_observation_seq, committed)
        pending = self._landing_observation_pending
        if not isinstance(pending, dict):
            return True
        if int(pending['observation_seq']) != sequence:
            return sequence <= self._landing_observation_seq
        if accepted:
            self._landing_observation_pending = None
            self._start_queued_landing_observation()
            return True
        if reason in retryable:
            pending['observation_seq'] = self._landing_observation_seq + 1
            pending.pop('wire', None)
            pending['sent'] = False
            pending['retry_on_input'] = True
            if reason == 'stale_input':
                pending['refresh_input_on_retry'] = True
                if self._input_seq > input_seq:
                    pending['input_seq'] = int(self._input_seq)
                    self._send_pending_landing_observation(rebind=True)
            else:
                self._send_pending_landing_observation(rebind=True)
            return True
        self._landing_observation_pending = None
        self._landing_observation_queue = []
        return True

    def send_battle_ready(self, bases=None):
        """Join the server-owned #1513 load barrier exactly once per round."""
        if not self.ready or self.phase != 'loading':
            return False
        message = {
            'type': 'battle_ready',
            'round_id': self.round_id,
        }
        if isinstance(bases, dict):
            # SpawnPlanner deliberately indexes its local formations by the
            # integer team ids 1 and 2.  JSON writes those keys as text, but
            # the reliable sender freezes only already-canonical JSON data so
            # it can reject ambiguous mappings before the worker thread.  Do
            # the one schema conversion at this wire boundary.
            wire_bases = {}
            for team in (1, 2):
                points = bases.get(str(team))
                if points is None:
                    points = bases.get(team)
                if points is not None:
                    wire_bases[str(team)] = points
            message['bases'] = wire_bases
        return self._send(message)

    def send_fire(self, shell_index=0, position=None, yaw=None,
                  aim_yaw=None, gun_pitch=None, velocity=None, gravity=None,
                  max_distance=None, max_time_ms=None, is_he=False,
                  splash_radius=0.0, penetration_factor=1.0,
                  source_shot=None, dispersion_angle=0.0):
        """Compatibility wrapper that submits only a player trigger intent."""
        parsed_velocity = _strict_launch_velocity(velocity)
        if parsed_velocity is None:
            return None
        speed = math.sqrt(sum(
            component * component for component in parsed_velocity))
        return self.send_fire_intent(
            shell_index, position,
            [component / speed for component in parsed_velocity],
            dispersion_angle)

    def send_fire_intent(self, shell_index, shot_origin, shot_direction,
                         dispersion_angle):
        """Queue one ordered trigger input without damage or ballistics."""
        if (not self.ready or self.phase != 'battle' or
                self.is_bot_authority()):
            return None
        if (self._input_seq_round != self.round_id or
                self._input_seq <= 0):
            return None
        parsed_shell = _projectile_int_range(shell_index, 0, 9)
        parsed_origin = _strict_world_position(shot_origin)
        parsed_direction = _strict_vector3(shot_direction, 1.0)
        parsed_dispersion = _projectile_float_range(
            dispersion_angle, 0.0, MAX_PLAYER_DISPERSION_ANGLE)
        direction_length = (math.sqrt(sum(
            component * component for component in parsed_direction))
            if parsed_direction is not None else 0.0)
        if (parsed_shell is None or parsed_origin is None or
                parsed_direction is None or parsed_dispersion is None or
                direction_length <= 0.000001):
            return None
        parsed_direction = [
            component / direction_length for component in parsed_direction]
        with self._projectile_lock:
            sequence = self._fire_intent_seq + 1
            message = {
                'type': 'fire_intent', 'round_id': self.round_id,
                'intent_seq': sequence, 'shell_index': parsed_shell,
                'input_seq': self._input_seq,
                'shot_origin': parsed_origin,
                'shot_direction': parsed_direction,
                'dispersion_angle': parsed_dispersion,
            }
            if not self._send(message):
                return None
            self._fire_intent_seq = sequence
            return sequence

    def send_equipment_intent(
            self, equipment_id, activation_code=None, selected=None,
            requested_active=None):
        """Queue one ordered trigger without a local critical verdict."""
        if (not self.ready or self.phase != 'battle' or
                self.is_bot_authority()):
            return None
        parsed_id = _projectile_int_range(equipment_id, 1, 65535)
        parsed_activation = _projectile_int_range(
            activation_code, 1, MAX_PROJECTILE_ID)
        if (parsed_id is None or parsed_activation is None or
                parsed_activation & 65535 != parsed_id):
            return None
        if selected is not None:
            if (not isinstance(selected, string_types) or not selected or
                    len(selected) > 64):
                return None
            selected = str(selected)
        if (requested_active is not None and
                not isinstance(requested_active, bool)):
            return None
        with self._projectile_lock:
            sequence = self._equipment_intent_seq + 1
            message = {
                'type': 'equipment_intent',
                'round_id': self.round_id,
                'intent_seq': sequence,
                'equipment_id': parsed_id,
                'activation_code': parsed_activation,
                'selected': selected,
                'requested_active': requested_active,
            }
            if not self._send(message):
                return None
            self._equipment_intent_seq = sequence
            return sequence

    def send_fire_intent_result(self, player_id, intent_seq, reason):
        """Publish one worker-owned terminal rejection."""
        parsed_player = _projectile_int_range(
            player_id, 1, MAX_PROJECTILE_ID)
        parsed_sequence = _projectile_int_range(
            intent_seq, 1, MAX_PROJECTILE_ID)
        if (not self.is_bot_authority() or parsed_player is None or
                parsed_sequence is None or self.round_id is None or
                self.authority_epoch is None):
            return False
        return self._send({
            'type': 'fire_intent_result', 'round_id': self.round_id,
            'authority_epoch': self.authority_epoch,
            'player_id': parsed_player, 'intent_seq': parsed_sequence,
            'accepted': False,
            'reason': _safe_text(reason, 'rejected', 64),
        })

    def send_projectile_launch(
            self, shooter_kind, shooter_id, shot_seq, shell_index, origin,
            velocity, gravity, max_distance, max_time_ms, is_he,
            splash_radius, authority_epoch=None, penetration_factor=1.0,
            source_shot=None, fire_intent_seq=None, fire_input_seq=None,
            burst_group_seq=None, burst_index=None, burst_count=None,
            launch_time_us=None, launch_pose=None):
        """Enqueue one immutable projectile launch and return its shot seq."""
        if not self.ready or self.phase != 'battle':
            return None
        if shooter_kind not in ('player', 'bot'):
            return None
        parsed_shooter_id = _projectile_int_range(
            shooter_id, 1, MAX_PROJECTILE_ID)
        parsed_shell = _projectile_int_range(shell_index, 0, 9)
        parsed_origin = _strict_world_position(origin)
        parsed_velocity = _strict_launch_velocity(velocity)
        parsed_gravity = _projectile_float_range(
            gravity, 0.000001, MAX_PROJECTILE_GRAVITY)
        parsed_distance = _projectile_float_range(
            max_distance, 0.000001, MAX_PROJECTILE_DISTANCE)
        parsed_time = _projectile_int_range(
            max_time_ms, 1, MAX_PROJECTILE_TIME_MS)
        parsed_splash = _projectile_float_range(
            splash_radius, 0.0, MAX_PROJECTILE_SPLASH_RADIUS)
        parsed_penetration = _projectile_float_range(
            penetration_factor, 0.0, 100.0)
        parsed_source_shot = _strict_projectile_source_shot(source_shot)
        if (parsed_shooter_id is None or parsed_shell is None or
                parsed_origin is None or parsed_velocity is None or
                parsed_gravity is None or parsed_distance is None or
                parsed_time is None or parsed_splash is None or
                parsed_penetration is None or
                not isinstance(is_he, bool) or
                (not is_he and parsed_splash != 0.0) or
                not _projectile_source_shot_matches_launch(
                    parsed_source_shot, parsed_velocity, parsed_gravity,
                    parsed_distance, is_he, parsed_splash)):
            return None

        parsed_epoch = None
        parsed_intent_seq = None
        parsed_input_seq = None
        if shooter_kind == 'player':
            parsed_epoch = _projectile_int_range(
                authority_epoch, 0, MAX_PROJECTILE_ID)
            parsed_intent_seq = _projectile_int_range(
                fire_intent_seq, 1, MAX_PROJECTILE_ID)
            parsed_input_seq = _projectile_int_range(
                fire_input_seq, 1, MAX_PROJECTILE_ID)
            if (not self.is_bot_authority() or
                    _exact_int(self.player_id) != WORKER_AUTHORITY_ID or
                    parsed_epoch is None or parsed_intent_seq is None or
                    parsed_input_seq is None or
                    parsed_epoch != _exact_int(self.authority_epoch)):
                return None
        else:
            parsed_epoch = _projectile_int_range(
                authority_epoch, 0, MAX_PROJECTILE_ID)
            parsed_launch_time = _projectile_int_range(
                launch_time_us, 0, MAX_MOTION_TIME_US)
            parsed_launch_pose = _strict_bot_launch_pose(launch_pose)
            if (not self.is_bot_authority() or parsed_epoch is None or
                    parsed_epoch != _exact_int(self.authority_epoch) or
                    parsed_launch_time is None or
                    parsed_launch_pose is None):
                return None

        with self._projectile_lock:
            parsed_seq = _projectile_int_range(
                shot_seq, 1, MAX_PROJECTILE_ID)
            if parsed_seq is None:
                return None
            supplied_group = (
                burst_group_seq is not None or burst_index is not None or
                burst_count is not None)
            if supplied_group:
                parsed_group = _strict_burst_group(
                    parsed_seq, burst_group_seq, burst_index, burst_count)
            else:
                parsed_group = (parsed_seq, 0, 1)
            if parsed_group is None:
                return None

            message = {
                'type': 'projectile_launch',
                'round_id': self.round_id,
                'shooter_kind': shooter_kind,
                'shooter_id': parsed_shooter_id,
                'shot_seq': parsed_seq,
                'burst_group_seq': parsed_group[0],
                'burst_index': parsed_group[1],
                'burst_count': parsed_group[2],
                'shell_index': parsed_shell,
                'source_shot': parsed_source_shot,
                'origin': parsed_origin,
                'velocity': parsed_velocity,
                'gravity': parsed_gravity,
                'max_distance': parsed_distance,
                'max_time_ms': parsed_time,
                'is_he': is_he,
                'splash_radius': parsed_splash,
                'penetration_factor': parsed_penetration,
            }
            if parsed_epoch is not None:
                message['authority_epoch'] = parsed_epoch
            if shooter_kind == 'bot':
                message['launch_time_us'] = parsed_launch_time
                message['launch_pose'] = parsed_launch_pose
            if parsed_intent_seq is not None:
                message['fire_intent_seq'] = parsed_intent_seq
                message['fire_input_seq'] = parsed_input_seq
            if not self._send(message):
                return None
            return parsed_seq

    def send_projectile_progress(self, authority_epoch, cursors):
        """CAS-advance at most thirty server-ledger projectile cursors."""
        if self.phase != 'battle' or not self.is_bot_authority():
            return False
        parsed_epoch = _projectile_int_range(
            authority_epoch, 0, MAX_PROJECTILE_ID)
        if parsed_epoch is None or parsed_epoch != _exact_int(
                self.authority_epoch):
            return False
        if (not isinstance(cursors, list) or not cursors or
                len(cursors) > MAX_PROJECTILE_BATCH):
            return False
        parsed_cursors = []
        seen = set()
        exact_keys = frozenset((
            'projectile_id', 'base_checked_ms', 'checked_through_ms',
            'checked_distance', 'piercing_loss', 'penetration_factor',
            'destructibles'))
        destructible_count = 0
        for cursor in cursors:
            if not isinstance(cursor, dict) or set(cursor) != exact_keys:
                return False
            projectile_id = _strict_projectile_id(
                cursor.get('projectile_id'))
            base_checked = _projectile_int_range(
                cursor.get('base_checked_ms'), 0, MAX_PROJECTILE_TIME_MS)
            checked_through = _projectile_int_range(
                cursor.get('checked_through_ms'), 0,
                MAX_PROJECTILE_TIME_MS)
            checked_distance = _projectile_float_range(
                cursor.get('checked_distance'), 0.0,
                MAX_PROJECTILE_DISTANCE)
            piercing_loss = _projectile_float_range(
                cursor.get('piercing_loss'), 0.0,
                MAX_PROJECTILE_PIERCING_LOSS)
            penetration_factor = _projectile_float_range(
                cursor.get('penetration_factor'), 0.0, 100.0)
            raw_destructibles = cursor.get('destructibles')
            if not isinstance(raw_destructibles, list):
                return False
            parsed_destructibles = []
            for raw in raw_destructibles:
                parsed = _strict_projectile_destructible(raw)
                if parsed is None:
                    return False
                parsed_destructibles.append(parsed)
            destructible_count += len(parsed_destructibles)
            if (projectile_id is None or projectile_id in seen or
                    base_checked is None or checked_through is None or
                    checked_through < base_checked or
                    checked_distance is None or piercing_loss is None or
                    penetration_factor is None or
                    destructible_count > MAX_PROJECTILE_DESTRUCTIBLES):
                return False
            seen.add(projectile_id)
            parsed_cursors.append({
                'projectile_id': projectile_id,
                'base_checked_ms': base_checked,
                'checked_through_ms': checked_through,
                'checked_distance': checked_distance,
                'piercing_loss': piercing_loss,
                'penetration_factor': penetration_factor,
                'destructibles': parsed_destructibles,
            })
        return self._send({
            'type': 'projectile_progress',
            'round_id': self.round_id,
            'authority_epoch': parsed_epoch,
            'cursors': parsed_cursors,
        })

    def send_projectile_resolve(
            self, authority_epoch, projectile_id, base_checked_ms, outcome,
            resolved_time_ms, impact, direct, splash, checked_distance=0.0,
            piercing_loss=0.0, penetration_factor=1.0,
            destructibles=None, hit_vehicle=None, wreck_hit=None):
        """Resolve one server-ledger projectile with an atomic effect set."""
        if self.phase != 'battle' or not self.is_bot_authority():
            return False
        parsed_epoch = _projectile_int_range(
            authority_epoch, 0, MAX_PROJECTILE_ID)
        parsed_projectile_id = _strict_projectile_id(projectile_id)
        parsed_base = _projectile_int_range(
            base_checked_ms, 0, MAX_PROJECTILE_TIME_MS)
        parsed_time = _projectile_int_range(
            resolved_time_ms, 0, MAX_PROJECTILE_TIME_MS)
        parsed_impact = (_strict_world_position(impact)
                         if outcome == 'impact' else None)
        parsed_distance = _projectile_float_range(
            checked_distance, 0.0, MAX_PROJECTILE_DISTANCE)
        parsed_loss = _projectile_float_range(
            piercing_loss, 0.0, MAX_PROJECTILE_PIERCING_LOSS)
        parsed_factor = _projectile_float_range(
            penetration_factor, 0.0, 100.0)
        if hit_vehicle is None:
            parsed_hit_vehicle = direct is not None
        elif isinstance(hit_vehicle, bool):
            parsed_hit_vehicle = hit_vehicle
        else:
            return False
        parsed_wreck_hit = None
        if wreck_hit is not None:
            parsed_wreck_hit = _strict_projectile_wreck_hit(wreck_hit)
            if parsed_wreck_hit is None:
                return False
        if destructibles is None:
            destructibles = []
        if (not isinstance(destructibles, list) or
                len(destructibles) > MAX_PROJECTILE_DESTRUCTIBLES):
            return False
        parsed_destructibles = []
        for raw in destructibles:
            parsed = _strict_projectile_destructible(raw)
            if parsed is None:
                return False
            parsed_destructibles.append(parsed)
        if (parsed_epoch is None or parsed_epoch != _exact_int(
                self.authority_epoch) or parsed_projectile_id is None or
                parsed_base is None or parsed_time is None or
                parsed_time < parsed_base or
                outcome not in ('impact', 'miss', 'expired') or
                (outcome == 'impact' and parsed_impact is None) or
                (outcome != 'impact' and impact is not None) or
                parsed_distance is None or
                parsed_loss is None or parsed_factor is None or
                (outcome != 'impact' and parsed_hit_vehicle) or
                not isinstance(splash, list) or
                len(splash) > MAX_PROJECTILE_BATCH):
            return False
        parsed_direct = None
        if direct is not None:
            parsed_direct = _strict_projectile_effect(direct)
            if (parsed_direct is None or
                    any(name in parsed_direct for name in
                        ('target_x', 'target_y', 'target_z'))):
                return False
        parsed_splash = []
        targets = set()
        if parsed_direct is not None:
            targets.add((parsed_direct['target_kind'],
                         parsed_direct['target_id']))
        for effect in splash:
            parsed = _strict_projectile_effect(effect)
            if (parsed is None or
                    'damage_sticker' in parsed or
                    not all(name in parsed for name in
                            ('target_x', 'target_y', 'target_z'))):
                return False
            target = (parsed['target_kind'], parsed['target_id'])
            if target in targets:
                return False
            targets.add(target)
            parsed_splash.append(parsed)
        if (outcome != 'impact' and
                (parsed_direct is not None or parsed_splash)):
            return False
        if parsed_direct is not None and not parsed_hit_vehicle:
            return False
        if (parsed_wreck_hit is not None and
                (outcome != 'impact' or not parsed_hit_vehicle or
                 parsed_direct is not None)):
            return False
        message = {
            'type': 'projectile_resolve',
            'round_id': self.round_id,
            'authority_epoch': parsed_epoch,
            'projectile_id': parsed_projectile_id,
            'base_checked_ms': parsed_base,
            'outcome': outcome,
            'resolved_time_ms': parsed_time,
            'checked_distance': parsed_distance,
            'piercing_loss': parsed_loss,
            'penetration_factor': parsed_factor,
            'impact': parsed_impact,
            'direct': parsed_direct,
            'splash': parsed_splash,
            'destructibles': parsed_destructibles,
        }
        if self.has_projectile_hit_vehicle():
            message['hit_vehicle'] = parsed_hit_vehicle
        if (parsed_wreck_hit is not None and
                self.has_projectile_wreck_hit()):
            message['wreck_hit'] = parsed_wreck_hit
        return self._send(message)

    def send_projectile_ricochet(
            self, authority_epoch, projectile_id, base_checked_ms,
            resolved_time_ms, impact, segment_origin, segment_velocity,
            base_penetration_multiplier, direct, checked_distance=0.0,
            piercing_loss=0.0, penetration_factor=1.0,
            destructibles=None):
        """Atomically replace a first-impact projectile with its ricochet."""
        if self.phase != 'battle' or not self.is_bot_authority():
            return False
        parsed_epoch = _projectile_int_range(
            authority_epoch, 0, MAX_PROJECTILE_ID)
        parsed_projectile_id = _strict_projectile_id(projectile_id)
        parsed_base = _projectile_int_range(
            base_checked_ms, 0, MAX_PROJECTILE_TIME_MS)
        parsed_time = _projectile_int_range(
            resolved_time_ms, 0, MAX_PROJECTILE_TIME_MS)
        parsed_impact = _strict_world_position(impact)
        parsed_origin = _strict_world_position(segment_origin)
        parsed_velocity = _strict_launch_velocity(segment_velocity)
        parsed_multiplier = _projectile_float_range(
            base_penetration_multiplier, 0.0, 1.0)
        parsed_distance = _projectile_float_range(
            checked_distance, 0.0, MAX_PROJECTILE_DISTANCE)
        parsed_loss = _projectile_float_range(
            piercing_loss, 0.0, MAX_PROJECTILE_PIERCING_LOSS)
        parsed_factor = _projectile_float_range(
            penetration_factor, 0.0, 100.0)
        parsed_direct = _strict_projectile_effect(direct)
        if destructibles is None:
            destructibles = []
        if (not isinstance(destructibles, list) or
                len(destructibles) > MAX_PROJECTILE_DESTRUCTIBLES):
            return False
        parsed_destructibles = []
        for raw in destructibles:
            parsed = _strict_projectile_destructible(raw)
            if parsed is None:
                return False
            parsed_destructibles.append(parsed)
        direct_fields = {
            'target_kind', 'target_id', 'damage', 'shot_result',
            'x', 'y', 'z'}
        if (parsed_epoch is None or parsed_epoch != _exact_int(
                self.authority_epoch) or parsed_projectile_id is None or
                parsed_base is None or parsed_time is None or
                parsed_time < parsed_base or parsed_impact is None or
                parsed_origin is None or parsed_velocity is None or
                parsed_multiplier not in (0.75, 1.0) or
                parsed_distance is None or parsed_loss is None or
                parsed_factor is None or parsed_direct is None or
                parsed_direct['damage'] != 0 or
                parsed_direct['shot_result'] != 0 or
                set(parsed_direct) not in (
                    direct_fields, direct_fields | {'damage_sticker'})):
            return False
        if math.sqrt(sum(
                (parsed_origin[index] - parsed_impact[index]) ** 2
                for index in range(3))) > 0.1:
            return False
        return self._send({
            'type': 'projectile_ricochet',
            'round_id': self.round_id,
            'authority_epoch': parsed_epoch,
            'projectile_id': parsed_projectile_id,
            'base_checked_ms': parsed_base,
            'resolved_time_ms': parsed_time,
            'checked_distance': parsed_distance,
            'piercing_loss': parsed_loss,
            'penetration_factor': parsed_factor,
            'impact': parsed_impact,
            'segment_origin': parsed_origin,
            'segment_velocity': parsed_velocity,
            'base_penetration_multiplier': parsed_multiplier,
            'direct': parsed_direct,
            'destructibles': parsed_destructibles,
        })

    def send_hit(self, target_id, shot_seq, damage, shot_result,
                 shell_index=0, impact_position=None, critical=None,
                 splash=False, critical_target_base_revision=None,
                 critical_target_ack_seq=None, hull_damage=None,
                 critical_delta=None):
        """Visible #1513 clients never publish hit verdicts."""
        return False

    def send_destructible(self, event):
        """Publish one worker-resolved map destruction."""
        if (not self.ready or self.phase != 'battle' or
                not self.is_bot_authority() or not isinstance(event, dict)):
            return False
        kind = _safe_text(event.get('destructible_kind'), '', 16)
        if kind not in ('tree', 'column', 'fragile', 'module'):
            return False
        chunk_id = _exact_int(event.get('chunk_id'))
        item_index = _exact_int(event.get('item_index'))
        is_shot = event.get('is_shot')
        if (chunk_id is None or item_index is None or
                not isinstance(is_shot, bool)):
            return False
        message = {
            'type': 'destructible', 'round_id': self.round_id,
            'destructible_kind': kind,
            'chunk_id': chunk_id, 'item_index': item_index,
            'x': _finite_float(event.get('x')),
            'y': _finite_float(event.get('y')),
            'z': _finite_float(event.get('z')),
            'fall_yaw': _finite_float(event.get('fall_yaw')),
            'speed': _finite_float(event.get('speed')),
            'is_shot': is_shot,
        }
        if event.get('mat_kind') is not None:
            mat_kind = _exact_int(event.get('mat_kind'))
            if mat_kind is None:
                return False
            message['mat_kind'] = mat_kind
        return self._send(message)

    def send_player_destructible_contact_result(
            self, player_id, contact_seq, accepted, token):
        """Finish one server-bound player hull proposal as native authority."""
        if (not self.ready or self.phase != 'battle' or
                not self.is_bot_authority() or
                not isinstance(accepted, bool) or
                not isinstance(token, (list, tuple)) or
                not 1 <= len(token) <=
                MAX_PLAYER_DESTRUCTIBLE_CONTACT_TOKEN):
            return False
        parsed_player = _projectile_int_range(
            player_id, 1, MAX_PROJECTILE_ID)
        parsed_seq = _projectile_int_range(
            contact_seq, 1, MAX_PROJECTILE_ID)
        parsed_token = []
        for raw in token:
            if not isinstance(raw, (list, tuple)) or len(raw) != 3:
                return False
            chunk_id = _exact_int(raw[0])
            item_index = _exact_int(raw[1])
            mat_kind = None if raw[2] is None else _exact_int(raw[2])
            if (chunk_id is None or chunk_id < 0 or
                    item_index is None or item_index < 0 or
                    (raw[2] is not None and mat_kind is None)):
                return False
            parsed_token.append([chunk_id, item_index, mat_kind])
        if parsed_player is None or parsed_seq is None:
            return False
        return self._send({
            'type': 'player_destructible_contact_result',
            'round_id': self.round_id,
            'player_id': parsed_player,
            'contact_seq': parsed_seq,
            'accepted': accepted,
            'token': parsed_token,
        })

    def is_bot_authority(self):
        """Visible #1513 clients never own authoritative bot messages."""
        return False

    def has_projectile_ledger(self):
        return PROJECTILE_LEDGER_CAPABILITY in self.capabilities

    def has_ricochet_continuation(self):
        return (RICOCHET_CONTINUATION_CAPABILITY in self.capabilities and
                RICOCHET_CONTINUATION_CAPABILITY in
                self.server_capabilities)

    def has_projectile_hit_vehicle(self):
        return (PROJECTILE_HIT_VEHICLE_CAPABILITY in
                self.server_capabilities)

    def has_projectile_wreck_hit(self):
        return (PROJECTILE_WRECK_HIT_CAPABILITY in
                self.server_capabilities)

    def has_random_map(self):
        return RANDOM_MAP_CAPABILITY in self.server_capabilities

    def has_team_selection(self):
        return TEAM_SELECTION_CAPABILITY in self.server_capabilities

    def has_team_size_selection(self):
        return TEAM_SIZE_SELECTION_CAPABILITY in self.server_capabilities

    def send_bot_manifest(self, bots, player_collision_profiles=None):
        if not self.is_bot_authority():
            return False
        message = {'type': 'bot_manifest',
                   'round_id': self.round_id,
                   'bots': list(bots or ())[:30]}
        if player_collision_profiles is not None:
            message['player_collision_profiles'] = list(
                player_collision_profiles or ())[:64]
        return self._send(message)

    def send_bot_state(self, bots, sample_time_us=None,
                       source_batch_horizon_us=None,
                       human_ram_armors=None):
        if not self.is_bot_authority():
            return False
        projected = []
        for state in list(bots or ())[:30]:
            state = project_bot_state(state)
            if state is None:
                return False
            projected.append(state)
        message = {'type': 'bot_state', 'round_id': self.round_id,
                   'bots': projected}
        if sample_time_us is not None:
            sample_time_us = _exact_int(sample_time_us)
            if (sample_time_us is None or
                    not 0 <= sample_time_us <= MAX_MOTION_TIME_US):
                return False
            message['sample_time_us'] = sample_time_us
            source_batch_horizon_us = _exact_int(
                source_batch_horizon_us)
            if (source_batch_horizon_us is None or
                    not sample_time_us <= source_batch_horizon_us <=
                    MAX_MOTION_TIME_US):
                return False
            message['source_batch_horizon_us'] = source_batch_horizon_us
        elif source_batch_horizon_us is not None:
            return False
        if human_ram_armors is not None:
            human_ram_armors = _project_human_ram_armors(human_ram_armors)
            if human_ram_armors is None:
                return False
            message['human_ram_armors'] = human_ram_armors
        return self._send(message)

    def send_projected_bot_state(self, bots, sample_time_us=None,
                                 source_batch_horizon_us=None,
                                 human_ram_armors=None):
        """Send BotRuntime's already-projected canonical publication once."""
        if not self.is_bot_authority():
            return False
        if (not isinstance(bots, (list, tuple)) or len(bots) > 30 or
                any(not isinstance(state, dict) for state in bots)):
            return False
        message = {'type': 'bot_state', 'round_id': self.round_id,
                   'bots': bots}
        if sample_time_us is not None:
            sample_time_us = _exact_int(sample_time_us)
            if (sample_time_us is None or
                    not 0 <= sample_time_us <= MAX_MOTION_TIME_US):
                return False
            message['sample_time_us'] = sample_time_us
            source_batch_horizon_us = _exact_int(
                source_batch_horizon_us)
            if (source_batch_horizon_us is None or
                    not sample_time_us <= source_batch_horizon_us <=
                    MAX_MOTION_TIME_US):
                return False
            message['source_batch_horizon_us'] = source_batch_horizon_us
        elif source_batch_horizon_us is not None:
            return False
        if human_ram_armors is not None:
            human_ram_armors = _project_human_ram_armors(human_ram_armors)
            if human_ram_armors is None:
                return False
            message['human_ram_armors'] = human_ram_armors
        return self._send(message)

    def send_bot_observation(self, contacts, affordances=None):
        if not self.is_bot_authority():
            return False
        return self._send({'type': 'bot_observation',
                           'round_id': self.round_id,
                           'contacts': list(contacts or ())[:64],
                           'affordances': list(affordances or ())[:16]})

    def send_spotted_report(self, targets):
        """Visible spotting is presentation-only until worker validation."""
        return False

    def send_descriptor_catalog(self, vehicles):
        if not self.ready:
            return False
        return self._send({'type': 'descriptor_catalog',
                           'vehicles': list(vehicles or ())[:1024]})

    def send_bot_hit(self, target_id, shot_seq, damage, shot_result,
                     impact_position=None, critical=None, splash=False,
                     critical_target_base_revision=None,
                     critical_target_ack_seq=None, hull_damage=None,
                     critical_delta=None):
        if (not self.ready or self.phase != 'battle' or
                not self.is_bot_authority()):
            return False
        message = {'type': 'bot_hit_report', 'round_id': self.round_id,
                   'target': int(target_id),
                   'shot_seq': int(shot_seq),
                   'damage': max(0, int(damage or 0)),
                   'shot_result': max(0, min(int(shot_result or 0), 2)),
                   'splash': bool(splash)}
        if impact_position is not None and len(impact_position) >= 3:
            message['x'] = _finite_float(impact_position[0])
            message['y'] = _finite_float(impact_position[1])
            message['z'] = _finite_float(impact_position[2])
        _attach_critical_proposal(
            message, critical, critical_target_base_revision,
            critical_target_ack_seq, hull_damage, critical_delta)
        return self._send(message)

    def send_bot_human_hit(self, bot_id, target_id, shot_seq, damage,
                           shot_result, impact_position=None, critical=None,
                           splash=False,
                           critical_target_base_revision=None,
                           critical_target_ack_seq=None, hull_damage=None,
                           critical_delta=None):
        if not self.is_bot_authority():
            return False
        message = {'type': 'bot_human_hit', 'round_id': self.round_id,
                   'attacker_bot': int(bot_id),
                   'target': int(target_id), 'shot_seq': int(shot_seq),
                   'damage': max(0, int(damage or 0)),
                   'shot_result': max(0, min(int(shot_result or 0), 2)),
                   'splash': bool(splash)}
        if impact_position is not None and len(impact_position) >= 3:
            message['x'] = _finite_float(impact_position[0])
            message['y'] = _finite_float(impact_position[1])
            message['z'] = _finite_float(impact_position[2])
        _attach_critical_proposal(
            message, critical, critical_target_base_revision,
            critical_target_ack_seq, hull_damage, critical_delta)
        return self._send(message)

    def send_bot_bot_hit(self, bot_id, target_id, shot_seq, damage,
                         shot_result, impact_position=None, critical=None,
                         splash=False,
                         critical_target_base_revision=None,
                         critical_target_ack_seq=None, hull_damage=None,
                         critical_delta=None):
        """Report an authority-simulated bot shot against another bot."""
        if not self.is_bot_authority():
            return False
        message = {'type': 'bot_hit_report', 'round_id': self.round_id,
                   'attacker_bot': int(bot_id),
                   'target': int(target_id), 'shot_seq': int(shot_seq),
                   'damage': max(0, int(damage or 0)),
                   'shot_result': max(0, min(int(shot_result or 0), 2)),
                   'splash': bool(splash)}
        if impact_position is not None and len(impact_position) >= 3:
            message['x'] = _finite_float(impact_position[0])
            message['y'] = _finite_float(impact_position[1])
            message['z'] = _finite_float(impact_position[2])
        _attach_critical_proposal(
            message, critical, critical_target_base_revision,
            critical_target_ack_seq, hull_damage, critical_delta)
        return self._send(message)

    def send_bot_ram(self, bot_id, target_kind, target_id, ram_seq,
                     damage_to_bot, damage_to_target,
                     ram_contact_player_id=None, ram_contact_seq=None):
        """Report one receipt-owned tank collision as authority."""
        if not self.is_bot_authority():
            return False
        kind = str(target_kind)
        if kind not in ('bot', 'human'):
            return False
        message = {
            'type': 'bot_ram_report', 'round_id': self.round_id,
            'bot_id': int(bot_id), 'target_kind': kind,
            'target_id': int(target_id), 'ram_seq': max(1, int(ram_seq)),
            'damage_to_bot': max(0, int(damage_to_bot or 0)),
            'damage_to_target': max(0, int(damage_to_target or 0)),
        }
        if (ram_contact_player_id is not None and
                ram_contact_seq is not None):
            message['ram_contact_player_id'] = int(ram_contact_player_id)
            message['ram_contact_seq'] = int(ram_contact_seq)
        return self._send(message)

    def send_rules_state(self, bases):
        """Send the server's documented standard-base state shape."""
        if not self.is_bot_authority():
            return False
        rules = {'bases': bases if isinstance(bases, dict) else {}}
        return self._send({'type': 'rules_state', 'round_id': self.round_id,
                           'rules': rules})

    def send_battle_result(self, winner, reason, base_team=0):
        if not self.is_bot_authority():
            return False
        return self._send({'type': 'battle_result',
                           'round_id': self.round_id,
                           'winner': int(winner),
                           'reason': _safe_text(reason, 'battle finished', 80),
                           'base_team': int(base_team or 0)})

    def acknowledge_battle_receipt(self, receipt_id):
        """Ack only after PostBattleStore has durably accepted the receipt."""
        receipt_id = _safe_text(receipt_id, '', 97)
        if not self.ready or not receipt_id or len(receipt_id) > 96:
            return False
        return self._send({
            'type': 'battle_receipt_ack',
            'receipt_id': receipt_id,
        })

    def _publish_connected_transport(self, sock, generation):
        """Atomically publish one hello-complete transport generation."""
        sender = threading.Thread(
            target=self._sender_worker, args=(sock, generation),
            name='offline-lan-0922-sender')
        sender.setDaemon(True)
        with self._outbound_lock:
            if (generation != self._transport_generation or
                    self._stopping or not self.running or
                    self.sock is not sock):
                return False
            self.connected = True
            self._outbound_accepting = True
            self._sender_thread = sender
            # Starting under the lifecycle lock closes the window where stop
            # could join an assigned-but-not-yet-started sender.
            try:
                sender.start()
            except Exception:
                self.connected = False
                self._outbound_accepting = False
                self._sender_thread = None
                raise
        return True

    def _worker(self, generation=None):
        if generation is None:
            generation = self._transport_generation
        sock = None
        recv_buffer = u''
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3.0)
            sock.connect((self.host, self.port))
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass
            with self._outbound_lock:
                if (generation != self._transport_generation or
                        self._stopping or not self.running):
                    return
                self.sock = sock
            # The server requires hello to be the first wire message.  Do not
            # expose the socket to the BigWorld poller until it is on the
            # wire, or the first main-thread ping can win this race.
            hello = self._hello_payload()
            payload = (json.dumps(
                hello, separators=(',', ':')) + '\n').encode('utf-8')
            with self._send_lock:
                with self._outbound_lock:
                    if (generation != self._transport_generation or
                            self._stopping or not self.running or
                            self.sock is not sock):
                        return
                sock.sendall(payload)
            # Keep receive polling responsive.  Queued writes use
            # _send_payload(), which can safely resume after a partial send
            # instead of treating this socket-wide timeout as fatal.
            sock.settimeout(0.5)
            if not self._publish_connected_transport(sock, generation):
                return
            while (self.running and
                   generation == self._transport_generation):
                try:
                    chunk = sock.recv(8192)
                except socket.timeout:
                    continue
                if generation != self._transport_generation:
                    break
                if not chunk:
                    self._record_peer_close(generation, sock)
                    break
                received_time = _monotonic_time()
                try:
                    recv_buffer += chunk.decode('utf-8')
                except UnicodeError:
                    self._record_transport_error(
                        'server sent invalid UTF-8', generation, sock)
                    break
                if len(recv_buffer) > MAX_BUFFER_BYTES:
                    self._record_transport_error(
                        'server message buffer exceeded limit',
                        generation, sock)
                    break
                while u'\n' in recv_buffer:
                    line, recv_buffer = recv_buffer.split(u'\n', 1)
                    if not line:
                        continue
                    try:
                        message = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(message, dict):
                        # Frame stalls must not inflate RTT or countdown
                        # projection: both end in this network thread, not
                        # when the BigWorld main thread eventually drains.
                        message['_client_received_time'] = received_time
                    if generation != self._transport_generation:
                        break
                    self._queue_message(message, generation)
        except Exception as error:
            if not (isinstance(error, socket.error) and
                    self._transport_close_expected(generation, sock)):
                self._record_transport_error(error, generation, sock)
        finally:
            wake_sender = False
            with self._outbound_lock:
                if (generation == self._transport_generation and
                        self.sock is sock):
                    self.connected = False
                    self.running = False
                    self._outbound_accepting = False
                    self._outbound_queue = []
                    self._outbound_bytes = 0
                    self.sock = None
                    wake_sender = True
            if wake_sender:
                self._outbound_event.set()
            try:
                if sock is not None:
                    sock.close()
            except Exception:
                pass

    @staticmethod
    def _snapshot_lineage(message):
        if (not isinstance(message, dict) or
                message.get('type') != 'snapshot'):
            return None
        lineage = (
            message.get('round_id'), message.get('authority_epoch'),
            message.get('bot_authority_id'))
        try:
            hash(lineage)
        except TypeError:
            return None
        return lineage

    def _queue_message(self, message, generation=None):
        if not isinstance(message, dict):
            return
        with self._pending_lock:
            if (generation is not None and
                    (generation != self._transport_generation or
                     self._stopping or not self.running)):
                return
            if len(self._pending) >= MAX_PENDING_MESSAGES:
                latest_manifests = {}
                for index, value in enumerate(self._pending):
                    lineage = self._snapshot_lineage(value)
                    if (lineage is not None and
                            'bot_manifest' in value):
                        latest_manifests[lineage] = index
                incoming_lineage = self._snapshot_lineage(message)
                if (incoming_lineage is not None and
                        'bot_manifest' in message):
                    # The incoming full snapshot supersedes an older barrier
                    # for this exact lineage.
                    latest_manifests.pop(incoming_lineage, None)
                protected_snapshots = set(latest_manifests.values())
                snapshot_index = next((
                    index for index, value in enumerate(self._pending)
                    if (index not in protected_snapshots and
                        value.get('type') == 'snapshot' and
                        'bot_manifest' not in value and
                        self._snapshot_lineage(value) == incoming_lineage)),
                    None)
                if snapshot_index is None:
                    snapshot_index = next((
                        index for index, value in enumerate(self._pending)
                        if (index not in protected_snapshots and
                            value.get('type') == 'snapshot')), None)
                removable_index = snapshot_index
                if removable_index is None:
                    removable_index = next((
                        index for index, value in enumerate(self._pending)
                        if (value.get('type') != 'snapshot' and
                            value.get('type') not in ORDERED_RECEIVE_TYPES)),
                        None)
                if removable_index is not None:
                    del self._pending[removable_index]
                elif message.get('type') not in ORDERED_RECEIVE_TYPES:
                    return
                else:
                    raise RuntimeError(
                        'LAN receive queue overflowed on ordered state')
            self._pending.append(message)

    def _record_transport_error(self, error, generation, sock=None):
        """Record an error only while its transport generation still owns it."""
        with self._outbound_lock:
            if (generation != self._transport_generation or
                    self._stopping or
                    (sock is not None and self.sock is not sock)):
                return False
            self.last_error = str(error)
        return True

    def _transport_close_expected(self, generation, sock=None):
        """Return whether EOF/reset belongs to an intentional lifecycle end."""
        with self._outbound_lock:
            return bool(
                generation != self._transport_generation or
                self._stopping or not self.running or
                (sock is not None and self.sock is not sock) or
                self.phase == 'disconnected' or
                self.combat_phase == 'finished')

    def _record_peer_close(self, generation, sock=None):
        """Record unexpected EOF while keeping a completed battle quiet."""
        if self._transport_close_expected(generation, sock):
            return False
        return self._record_transport_error(
            'server closed the connection', generation, sock)

    def _abort_outbound(self, error, generation):
        with self._outbound_lock:
            if generation != self._transport_generation:
                return False
            if error:
                self.last_error = str(error)
            sock = self.sock
            self.running = False
            self.connected = False
            self._stopping = True
            self._outbound_accepting = False
            self._outbound_queue = []
            self._outbound_bytes = 0
            self.sock = None
        self._outbound_event.set()
        try:
            if sock is not None:
                sock.close()
        except Exception:
            pass
        return True

    def _dequeue_outbound(self, generation):
        with self._outbound_lock:
            if (generation != self._transport_generation or
                    not self._outbound_queue):
                return None
            item = self._outbound_queue.pop(0)
            self._outbound_bytes = max(
                0, self._outbound_bytes - item[2])
            return item

    def _send_wire(self, message, sock, generation):
        """Write one queued message, encoding generic payloads on demand."""
        try:
            if isinstance(message, _PreencodedOutbound):
                payload = message.payload
            else:
                payload = (json.dumps(
                    message, separators=(',', ':')) + '\n').encode('utf-8')
            if len(payload) > MAX_MESSAGE_BYTES:
                if self._record_transport_error(
                        'client message exceeded wire limit',
                        generation, sock):
                    return False
                return None
            with self._send_lock:
                with self._outbound_lock:
                    if (generation != self._transport_generation or
                            self._stopping or not self.running or
                            not self.connected or self.sock is not sock):
                        return None
                sent = self._send_payload(payload, sock, generation)
                if sent is not True:
                    return sent
            return True
        except Exception as error:
            if self._record_transport_error(error, generation, sock):
                return False
            return None

    def _send_payload(self, payload, sock, generation):
        """Send one frame without corrupting it after a partial timeout."""
        sender = getattr(sock, 'send', None)
        if not callable(sender):
            # Test doubles and unusual socket shims may only expose sendall.
            sock.sendall(payload)
            return True
        offset = 0
        stalled_since = None
        while offset < len(payload):
            with self._outbound_lock:
                if (generation != self._transport_generation or
                        self._stopping or not self.running or
                        not self.connected or self.sock is not sock):
                    return None
            try:
                count = sender(payload[offset:])
                if count is None or int(count) <= 0:
                    raise socket.error('server closed the connection')
                offset += int(count)
                stalled_since = None
            except socket.timeout:
                now = _monotonic_time()
                if stalled_since is None:
                    stalled_since = now
                elif now - stalled_since >= SEND_STALL_TIMEOUT:
                    raise socket.timeout(
                        'server did not accept client messages for %.0f '
                        'seconds' % SEND_STALL_TIMEOUT)
        return True

    def _sender_worker(self, sock, generation):
        try:
            while (self.running and self.connected and
                   generation == self._transport_generation):
                item = self._dequeue_outbound(generation)
                if item is None:
                    self._outbound_event.clear()
                    with self._outbound_lock:
                        pending = bool(
                            generation == self._transport_generation and
                            self._outbound_queue)
                    if pending:
                        self._outbound_event.set()
                        continue
                    self._outbound_event.wait(0.1)
                    continue
                send_result = self._send_wire(item[1], sock, generation)
                if send_result is not True:
                    if send_result is False:
                        self._abort_outbound(
                            self.last_error or 'LAN sender stopped',
                            generation)
                    break
        finally:
            with self._outbound_lock:
                if (generation == self._transport_generation and
                        self._sender_thread is threading.current_thread()):
                    self._sender_thread = None

    def _enqueue_outbound(self, message, encoded_size, generation):
        """Apply the common generation, FIFO and queue-size boundaries."""
        overflow = False
        with self._outbound_lock:
            if (generation != self._transport_generation or
                    not self._outbound_accepting or self._stopping or
                    not self.running or not self.connected):
                return False
            replacement_index = None
            if (isinstance(message, _PreencodedOutbound) and
                    message.coalesce_key is not None):
                for index in range(len(self._outbound_queue) - 1, -1, -1):
                    queued = self._outbound_queue[index][1]
                    if isinstance(queued, _PreencodedOutbound):
                        if queued.coalesce_key == message.coalesce_key:
                            replacement_index = index
                        # A different canonical Bot checkpoint is a state
                        # edge.  Never move a later state back across it.
                        break
                    if (isinstance(queued, dict) and
                            queued.get('type') == 'bot_ram_report'):
                        # The server validates this one-shot contact against
                        # the immediately preceding authority pose. A newer
                        # checkpoint may not replace that state from across
                        # the event even when its continuous key is equal.
                        break
            previous = (self._outbound_queue[replacement_index]
                        if replacement_index is not None else None)
            replacement_bytes = (
                self._outbound_bytes - previous[2] + encoded_size
                if previous is not None else
                self._outbound_bytes + encoded_size)
            if (previous is not None and
                    replacement_bytes <= max(
                        MAX_OUTBOUND_BYTES, self._outbound_bytes)):
                # A worker Bot publication is a full canonical checkpoint.
                # Its key retains every combat/ammo/equipment edge, so an
                # equivalent newer pose/timer sample supersedes the older one
                # even when independent FIFO messages were queued between
                # them.  Those discrete messages remain in their exact slots.
                self._outbound_queue[replacement_index] = (
                    previous[0], message, encoded_size)
                self._outbound_bytes = replacement_bytes
            else:
                checkpoint = bool(
                    isinstance(message, _PreencodedOutbound) and
                    message.coalesce_key is not None)
                preserve_discrete = bool(
                    not checkpoint and
                    self._outbound_discrete_headroom_enabled(message))
                message_limit = MAX_OUTBOUND_MESSAGES * (
                    2 if preserve_discrete else 1)
                byte_limit = MAX_OUTBOUND_BYTES * (
                    2 if preserve_discrete else 1)
                if (len(self._outbound_queue) >= message_limit or
                        self._outbound_bytes + encoded_size > byte_limit):
                    overflow = True
                else:
                    self._outbound_seq += 1
                    self._outbound_queue.append((
                        self._outbound_seq, message, encoded_size))
                    self._outbound_bytes += encoded_size
        if overflow:
            # Queue pressure is backpressure, not transport corruption.  Keep
            # every already accepted FIFO event and let the caller retry or
            # supersede this checkpoint after the sender drains capacity.
            return False
        self._outbound_event.set()
        return True

    def _outbound_discrete_headroom_enabled(self, unused_message):
        """Ordinary player input keeps the original exact queue boundary."""
        return False

    def _send_preencoded_trusted(self, message, coalesce_key=None):
        """Encode one trusted canonical message directly into queue bytes.

        This deliberately bypasses the generic recursive freeze/copy pass.
        Callers must therefore own the schema and must not pass data received
        from the network.  Encoding before enqueue still freezes the caller's
        state, rejects non-finite numbers, and gives both the wire and queue an
        exact byte bound.
        """
        with self._outbound_lock:
            if (not self.connected or self.sock is None or
                    self._stopping or not self.running or
                    not self._outbound_accepting):
                return False
            generation = self._transport_generation
        try:
            payload = (json.dumps(
                message, separators=(',', ':'), allow_nan=False) +
                '\n').encode('utf-8')
        except Exception:
            return False
        encoded_size = len(payload)
        if encoded_size > MAX_MESSAGE_BYTES:
            return False
        return self._enqueue_outbound(
            _PreencodedOutbound(payload, coalesce_key), encoded_size,
            generation)

    def _send(self, message):
        """Freeze and enqueue one reliable message without wire I/O."""
        with self._outbound_lock:
            if (not self.connected or self.sock is None or
                    self._stopping or not self.running or
                    not self._outbound_accepting):
                return False
            generation = self._transport_generation
        try:
            frozen, estimated_size = _freeze_outbound(message, [0])
        except Exception:
            return False
        estimated_size += 1
        if estimated_size > MAX_MESSAGE_BYTES:
            return False
        return self._enqueue_outbound(frozen, estimated_size, generation)

    def _schedule_poll(self):
        if self._poll_callback is not None:
            return
        if self.bigworld is None:
            self.bigworld = _load_bigworld()
        self._poll_callback = self.bigworld.callback(
            POLL_INTERVAL, self._poll)

    def _poll(self):
        self._poll_callback = None
        messages = []
        with self._pending_lock:
            if self._pending:
                messages = self._pending
                self._pending = []
        latest_snapshot = None
        for message in messages:
            if message.get('type') == 'snapshot':
                if (latest_snapshot is not None and
                        'bot_manifest' in latest_snapshot and
                        'bot_manifest' not in message):
                    # A manifest-bearing snapshot is a static-lineage
                    # barrier, not a replaceable motion sample.  During native
                    # space loading several server ticks can accumulate in one
                    # poll; consuming only the last lean snapshot would leave
                    # this replica with no manifest to inherit.
                    self._handle_message(latest_snapshot)
                    latest_snapshot = None
                    if not self.running:
                        break
                latest_snapshot = message
            elif (message.get('type') == 'events' and
                  latest_snapshot is not None and
                  message.get('round_id') ==
                  latest_snapshot.get('round_id') and
                  message.get('server_tick') ==
                  latest_snapshot.get('server_tick')):
                # Preserve event-before-state semantics even if a fixture or
                # an older relay batches one tick in the opposite order.
                self._handle_message(message)
                self._handle_message(latest_snapshot)
                latest_snapshot = None
            else:
                # A roster/battle_start is a state-transition barrier.  Flush
                # the newest preceding snapshot before it so a terminal round
                # cannot be replayed after the waiting-room reset.
                if latest_snapshot is not None:
                    self._handle_message(latest_snapshot)
                    latest_snapshot = None
                self._handle_message(message)
        if latest_snapshot is not None:
            self._handle_message(latest_snapshot)
        now = _monotonic_time()
        if self.connected and now - self._last_ping >= PING_INTERVAL:
            self._last_ping = now
            self._ping_seq += 1
            self._send({
                'type': 'ping',
                'seq': self._ping_seq,
                'client_time': now,
            })
        if self.last_error is not None:
            self._notify('error', {'message': self.last_error})
            self.last_error = None
        if self.running:
            self._schedule_poll()

    def _load_server_timing(self, message):
        """Project relative server timing onto this client's receive clock."""
        timing = message.get('timing') if isinstance(message, dict) else None
        if not isinstance(timing, dict):
            return False
        round_id = _exact_int(message.get('round_id'))
        server_tick = _exact_int(message.get('server_tick'))
        phase = _safe_text(timing.get('phase'), '', 16)
        start_ms = _exact_int(timing.get('start_in_ms'))
        remaining_ms = _exact_int(timing.get('remaining_ms'))
        duration_ms = _exact_int(timing.get('duration_ms'))
        if (round_id is None or round_id != self.round_id or
                server_tick is None or server_tick < 0 or
                phase not in ('loading', 'prebattle', 'battle', 'finished') or
                start_ms is None or start_ms < 0 or
                remaining_ms is None or remaining_ms < 0 or
                duration_ms is None or duration_ms <= 0 or
                remaining_ms > duration_ms):
            return False
        if (self._combat_timing_round_id == round_id and
                server_tick <= self._combat_timing_tick):
            return True
        received = _finite_float(
            message.get('_client_received_time'), _monotonic_time())
        one_way = 0.0
        if self.rtt_ms is not None:
            one_way = max(
                0.0, min(0.25, float(self.rtt_ms) / 2000.0))
        duration = float(duration_ms) / 1000.0
        if phase == 'prebattle':
            projected_start = received + float(start_ms) / 1000.0 - one_way
            if (self.combat_deadline is None or
                    abs(self.combat_deadline - projected_start) > 0.25):
                self.combat_deadline = projected_start
            else:
                self.combat_deadline = (
                    self.combat_deadline * 0.8 + projected_start * 0.2)
            projected_end = self.combat_deadline + duration
        elif phase == 'battle':
            if self.combat_deadline is None:
                self.combat_deadline = received - one_way
            projected_end = (
                received + float(remaining_ms) / 1000.0 - one_way)
        elif phase == 'finished':
            projected_end = received - one_way
        else:
            projected_end = received + duration - one_way
        self.combat_phase = phase
        self.combat_duration = duration
        if (self.combat_end_deadline is None or
                abs(self.combat_end_deadline - projected_end) > 0.25):
            self.combat_end_deadline = projected_end
        else:
            self.combat_end_deadline = (
                self.combat_end_deadline * 0.8 + projected_end * 0.2)
        self._combat_timing_round_id = round_id
        self._combat_timing_tick = server_tick
        return True

    def _runtime_recovery_enabled(self):
        """Return whether one bad live-state payload may be ignored.

        The visible client always has a last-known-good presentation state to
        keep rendering while it waits for the next server tick.  Handshake and
        waiting-room barriers intentionally remain fail-closed.
        """
        return self.phase in ('loading', 'battle')

    def _ignore_runtime_payload(self, kind, reason, message):
        """Rate-limit one diagnostic and preserve the active transport.

        This method never writes ``last_error``: the session uses that field as
        a user-visible failure boundary.  Callers must also avoid mutating any
        state before reaching this boundary.
        """
        if (kind != 'battle_receipt' and
                not self._runtime_recovery_enabled()):
            return False
        key = _safe_text(kind, 'unknown', 32)
        now = _monotonic_time()
        diagnostic = self._runtime_drop_diagnostics.get(key)
        suppressed = 0
        if diagnostic is not None:
            last_time, suppressed = diagnostic
            if now - last_time < RUNTIME_DROP_LOG_INTERVAL:
                self._runtime_drop_diagnostics[key] = (
                    last_time, suppressed + 1)
                return True
        round_id = (message.get('round_id')
                    if isinstance(message, dict) else None)
        server_tick = (message.get('server_tick')
                       if isinstance(message, dict) else None)
        suffix = (' suppressed=%s' % suppressed) if suppressed else ''
        print(
            '[Offline LAN 0.9.22] ignored recoverable %s payload '
            'reason=%s round=%s tick=%s%s' % (
                key, _safe_text(reason, 'invalid', 160), round_id,
                server_tick, suffix))
        self._runtime_drop_diagnostics[key] = (now, 0)
        return True

    def _discard_battle_receipt(self, message, reason):
        """Acknowledge one poison receipt without ending the session.

        The server verifies receipt ownership before removing it.  A bounded
        identifier is therefore safe to acknowledge even when the rest of the
        durable row cannot be consumed by this client build.
        """
        receipt_id = (_safe_text(message.get('receipt_id'), '', 97)
                      if isinstance(message, dict) else '')
        if receipt_id and len(receipt_id) <= 96:
            try:
                self.acknowledge_battle_receipt(receipt_id)
            except Exception:
                pass
        self._ignore_runtime_payload('battle_receipt', reason, message)

    def _runtime_round_disposition(self, kind, message, error):
        """Classify a live payload round without hiding identity conflicts."""
        round_id = _exact_int(message.get('round_id'))
        if round_id is None:
            if self._ignore_runtime_payload(kind, 'invalid_round', message):
                return None
            self.last_error = error
            self.stop()
            return None
        if self.round_id is not None and round_id < self.round_id:
            return None
        if round_id != self.round_id:
            # A future live-state packet without its roster/start barrier is a
            # real lineage conflict, not ordinary packet damage.
            self.last_error = error
            self.stop()
            return None
        return round_id

    def _handle_message(self, message):
        if not isinstance(message, dict):
            return
        kind = message.get('type')
        if kind == 'battle_receipt':
            if (not _valid_battle_receipt(message) or
                    message.get('account_key') != self.account_key):
                self._discard_battle_receipt(message, 'invalid_receipt')
                return
        message_round = _exact_int(message.get('round_id'))
        if (kind in RECOVERABLE_RUNTIME_TYPES and
                self.round_id is not None and
                message_round is not None and
                message_round < self.round_id):
            return
        protocol = message.get('protocol')
        if protocol is not None or kind in SERVER_STATE_TYPES:
            parsed_protocol = _exact_int(protocol)
            if kind == 'battle_receipt':
                # The complete receipt schema above is the compatibility
                # boundary.  A stale informational protocol label must not
                # make one durable result lock this account out forever.
                matches_protocol = True
            elif kind == 'welcome' or self._schema_negotiated:
                # Welcome carries the full capability contract below.  A
                # positive version marker is enough to negotiate that known
                # JSON schema; the exact build/version label is informational.
                matches_protocol = bool(
                    parsed_protocol is not None and parsed_protocol > 0)
            else:
                matches_protocol = parsed_protocol == PROTOCOL_VERSION
            if not matches_protocol:
                if (kind in RECOVERABLE_RUNTIME_TYPES and
                        self._ignore_runtime_payload(
                            kind, 'invalid_protocol', message)):
                    return
                self.last_error = 'protocol mismatch'
                self.stop()
                return
        atomic_runtime = kind in RECOVERABLE_RUNTIME_TYPES
        if (message_round is not None and message_round == self.round_id and
                'server_time_ms' in message):
            server_time_ms = _projectile_int_range(
                message.get('server_time_ms'), 0, MAX_PROJECTILE_ID)
            if server_time_ms is None:
                if (atomic_runtime and self._ignore_runtime_payload(
                        kind, 'invalid_server_time', message)):
                    return
                self.last_error = 'invalid server time'
                self.stop()
                return
            # Message construction and socket publication happen on multiple
            # server threads.  The endpoint send lock preserves wire writes,
            # but an older message can acquire it after a newer message whose
            # server clock was sampled later.  Treat the clock as a monotonic
            # observation rather than a transport sequence fence.
            if (self.server_time_ms is not None and
                    server_time_ms < self.server_time_ms):
                message = dict(message)
                server_time_ms = self.server_time_ms
                message['server_time_ms'] = server_time_ms
            if not atomic_runtime:
                self.server_time_ms = server_time_ms
        if kind == 'welcome':
            capabilities = _strict_capabilities(message.get('capabilities'))
            server_capabilities = _strict_capabilities(
                message.get('server_capabilities', []))
            if (capabilities is None or
                    PROJECTILE_LEDGER_CAPABILITY not in capabilities or
                    RAM_CONTACT_LEDGER_CAPABILITY not in capabilities or
                    HUMAN_RAM_TIMELINE_CAPABILITY not in capabilities or
                    PLAYER_FIRE_INTENT_CAPABILITY not in capabilities or
                    PLAYER_ENVIRONMENT_CAPABILITY not in capabilities or
                    EFFECTIVE_PARAMS_CAPABILITY not in capabilities or
                    RICOCHET_CONTINUATION_CAPABILITY not in capabilities or
                    server_capabilities is None or
                    DESTRUCTIBLE_CATALOG_V5_CAPABILITY not in
                    server_capabilities or
                    RAM_CONTACT_LEDGER_CAPABILITY not in
                    server_capabilities or
                    HUMAN_RAM_TIMELINE_CAPABILITY not in
                    server_capabilities or
                    PLAYER_FIRE_INTENT_CAPABILITY not in
                    server_capabilities or
                    PLAYER_ENVIRONMENT_CAPABILITY not in
                    server_capabilities or
                    EFFECTIVE_PARAMS_CAPABILITY not in
                    server_capabilities or
                    RICOCHET_CONTINUATION_CAPABILITY not in
                    server_capabilities):
                self.last_error = 'required LAN capability mismatch'
                self.stop()
                return
            player_id = _exact_int(message.get('player_id'))
            team = _exact_int(message.get('team'))
            round_id = _exact_int(message.get('round_id'))
            state_revision = _exact_int(message.get('state_revision'))
            host_player_id = _exact_int(message.get('host_player_id'))
            slot = _exact_int(message.get('slot'))
            max_health = _exact_int(message.get('max_health'))
            authority_epoch = _projectile_int_range(
                message.get('authority_epoch'), 0, MAX_PROJECTILE_ID)
            welcome_server_time = None
            if 'server_time_ms' in message:
                welcome_server_time = _projectile_int_range(
                    message.get('server_time_ms'), 0, MAX_PROJECTILE_ID)
            phase = _safe_text(message.get('phase'), '')
            map_name = _safe_text(message.get('map'), '')
            spawn = message.get('spawn')
            outfits = _canonical_wire_outfits(message.get('outfits'))
            vehicle_compact_descr = _canonical_vehicle_compact_descr(
                message.get('vehicle_compact_descr'))
            effective_params = _canonical_effective_params(
                message.get('effective_params'))
            team_sizes = _team_sizes(
                message.get('team_sizes'), message.get('team_size'),
                self.team_sizes)
            bot_tier_mode = _safe_text(
                message.get('bot_tier_mode'), self.bot_tier_mode, 32)
            if (player_id is None or state_revision is None or
                    state_revision < 0 or host_player_id is None or
                    host_player_id <= 0 or team not in (1, 2) or
                    round_id is None or slot is None or not 0 <= slot < 15 or
                    max_health is None or max_health <= 0 or
                    authority_epoch is None or
                    ('server_time_ms' in message and
                     welcome_server_time is None) or
                    not _valid_visible_authority_message(message) or
                    phase != 'waiting' or not map_name or outfits is None or
                    effective_params is None or
                    team_sizes is None or
                    bot_tier_mode not in BOT_TIER_MODES or
                    not isinstance(spawn, dict) or
                    not all(axis in spawn for axis in ('x', 'y', 'z'))):
                self.last_error = 'invalid welcome message'
                self.stop()
                return
            self.ready = True
            self._battle_live_round_id = None
            self._combat_timing_round_id = None
            self._combat_timing_tick = -1
            self.combat_deadline = None
            self.combat_end_deadline = None
            self.player_id = player_id
            self.name = _safe_text(message.get('name'), self.name)
            self.vehicle = _safe_text(message.get('vehicle'), self.vehicle)
            self.team = team
            self.team_sizes = team_sizes
            self.bot_tier_mode = bot_tier_mode
            self.slot = slot
            self.max_health = max_health
            self.outfits = outfits
            if vehicle_compact_descr is not None:
                self.vehicle_compact_descr = vehicle_compact_descr
            self.effective_params = effective_params
            self._published_player_effective_params[player_id] = \
                effective_params
            self._published_player_outfits[player_id] = dict(outfits)
            self.map_name = map_name
            self.map_pool = self._map_names(message.get('map_pool'))
            self.spawn = dict(spawn)
            self.phase = phase
            self.round_id = round_id
            self.state_revision = state_revision
            self.host_player_id = host_player_id
            self.bot_authority_id = message.get('bot_authority_id')
            self.authority_epoch = authority_epoch
            self.capabilities = capabilities
            self.server_capabilities = server_capabilities
            self._schema_negotiated = True
            self.server_time_ms = welcome_server_time
        elif kind == 'battle_receipt':
            pass
        elif kind == 'roster':
            round_id = _exact_int(message.get('round_id'))
            if round_id is None:
                self.last_error = 'invalid roster message'
                self.stop()
                return
            if self.round_id is not None and round_id < self.round_id:
                return
            state_revision = _exact_int(message.get('state_revision'))
            if state_revision is None or state_revision < 0:
                self.last_error = 'invalid roster message'
                self.stop()
                return
            if (round_id == self.round_id and
                    self.state_revision is not None and
                    state_revision < self.state_revision):
                return
            phase = _safe_text(message.get('phase'), '')
            map_name = _safe_text(message.get('map'), '')
            players = _strict_mapping_list(message.get('players'), 64)
            host_player_id = _exact_int(message.get('host_player_id'))
            authority_epoch = _projectile_int_range(
                message.get('authority_epoch'), 0, MAX_PROJECTILE_ID)
            team_sizes = _team_sizes(
                message.get('team_sizes'), message.get('team_size'),
                self.team_sizes)
            bot_tier_mode = _safe_text(
                message.get('bot_tier_mode'), self.bot_tier_mode, 32)
            roster_server_time = None
            if 'server_time_ms' in message:
                roster_server_time = _projectile_int_range(
                    message.get('server_time_ms'), 0, MAX_PROJECTILE_ID)
            player_ids = set(_exact_int(value.get('id'))
                             for value in players or ())
            player_outfits_valid = all(
                _canonical_wire_outfits(value.get('outfits')) is not None
                for value in players or ())
            player_effective_params_valid = all(
                _canonical_effective_params(
                    value.get('effective_params')) is not None
                for value in players or ())
            player_siege_contract = all(
                _valid_player_siege_contract(value)
                for value in players or ())
            player_gun_checkpoint_contract = all(
                _valid_player_gun_checkpoint_contract(value)
                for value in players or ())
            player_equipment_contract = all(
                _valid_player_equipment_contract(value, required=True)
                for value in players or ())
            ledger_required = self.has_projectile_ledger()
            if (phase not in ('waiting', 'loading', 'battle') or not map_name or
                    players is None or not player_outfits_valid or
                    not player_effective_params_valid or
                    not player_siege_contract or
                    not player_gun_checkpoint_contract or
                    not player_equipment_contract or
                    team_sizes is None or
                    bot_tier_mode not in BOT_TIER_MODES or
                    host_player_id not in player_ids or
                    not _valid_visible_authority_message(message) or
                    (ledger_required and authority_epoch is None) or
                    (ledger_required and round_id == self.round_id and
                     self.authority_epoch is not None and
                     authority_epoch < self.authority_epoch) or
                    (ledger_required and 'server_time_ms' in message and
                     roster_server_time is None)):
                self.last_error = 'invalid roster message'
                self.stop()
                return
            # Different server threads serialize through Player.send_lock, but
            # a new battle_start can acquire it before the reset thread sends
            # its same-round waiting roster.  Round phase is monotonic: once
            # this client has entered battle, that older waiting roster cannot
            # demote it or cancel a deferred local start.
            if (round_id == self.round_id and
                    self.phase in ('loading', 'battle') and
                    phase == 'waiting'):
                return
            if round_id != self.round_id:
                self.last_snapshot = None
                self._fire_seq = 0
                self._fire_intent_seq = 0
                self._equipment_intent_seq = 0
                self._battle_start_round_id = None
                self._battle_live_round_id = None
                self._combat_timing_round_id = None
                self._combat_timing_tick = -1
                self.combat_deadline = None
                self.combat_end_deadline = None
                self.server_time_ms = None
            self.round_id = round_id
            self.state_revision = state_revision
            self.phase = phase
            self.map_name = map_name
            maps = self._map_names(message.get('map_pool'))
            if maps:
                self.map_pool = maps
            players = self._remember_player_outfits(players)
            self.roster = players
            self.team_sizes = team_sizes
            self.bot_tier_mode = bot_tier_mode
            self._adopt_published_vehicle(players)
            self.host_player_id = host_player_id
            self.bot_authority_id = message.get(
                'bot_authority_id', self.bot_authority_id)
            if authority_epoch is not None:
                self.authority_epoch = authority_epoch
            if roster_server_time is not None:
                self.server_time_ms = roster_server_time
        elif kind == 'battle_start':
            round_id = _exact_int(message.get('round_id'))
            if round_id is None:
                self.last_error = 'invalid battle_start message'
                self.stop()
                return
            if self.round_id is not None and round_id < self.round_id:
                return
            state_revision = _exact_int(message.get('state_revision'))
            if state_revision is None or state_revision < 0:
                self.last_error = 'invalid battle_start message'
                self.stop()
                return
            stale_revision = (round_id == self.round_id and
                              self.state_revision is not None and
                              state_revision < self.state_revision)
            if (stale_revision and
                    self._battle_start_round_id == round_id):
                return
            if stale_revision:
                # battle_start is a transition barrier, not only a state
                # snapshot. A newer membership roster can overtake it on a
                # different server thread. Preserve that newer roster while
                # delivering the first start barrier exactly once.
                message = dict(message)
                state_revision = self.state_revision
                message['state_revision'] = state_revision
                if self.map_name:
                    message['map'] = self.map_name
                if self.roster:
                    message['players'] = list(self.roster)
                if self.host_player_id is not None:
                    message['host_player_id'] = self.host_player_id
                if self.bot_authority_id is not None:
                    message['bot_authority_id'] = self.bot_authority_id
                if self.authority_epoch is not None:
                    message['authority_epoch'] = self.authority_epoch
                if self.server_time_ms is not None:
                    message['server_time_ms'] = self.server_time_ms
            map_name = _safe_text(message.get('map'), '')
            phase = _safe_text(message.get('phase'), '')
            players = _strict_mapping_list(message.get('players'), 64)
            player_outfits_valid = all(
                _canonical_wire_outfits(value.get('outfits')) is not None
                for value in players or ())
            player_effective_params_valid = all(
                _canonical_effective_params(
                    value.get('effective_params')) is not None
                for value in players or ())
            player_siege_contract = all(
                _valid_player_siege_contract(value)
                for value in players or ())
            player_gun_checkpoint_contract = all(
                _valid_player_gun_checkpoint_contract(value)
                for value in players or ())
            player_equipment_contract = all(
                _valid_player_equipment_contract(value, required=True)
                for value in players or ())
            local_ids = set(_exact_int(value.get('id')) for value in players or ())
            host_player_id = _exact_int(message.get('host_player_id'))
            authority_epoch = _projectile_int_range(
                message.get('authority_epoch'), 0, MAX_PROJECTILE_ID)
            team_sizes = _team_sizes(
                message.get('team_sizes'), message.get('team_size'),
                self.team_sizes)
            start_server_time = None
            if 'server_time_ms' in message:
                start_server_time = _projectile_int_range(
                    message.get('server_time_ms'), 0, MAX_PROJECTILE_ID)
            ledger_required = self.has_projectile_ledger()
            if (phase != 'loading' or
                    not map_name or not players or not player_outfits_valid or
                    not player_effective_params_valid or
                    not player_siege_contract or
                    not player_gun_checkpoint_contract or
                    not player_equipment_contract or
                    team_sizes is None or
                    self.player_id not in local_ids or
                    host_player_id not in local_ids or
                    not _valid_visible_authority_id(
                        message.get('bot_authority_id')) or
                    (ledger_required and authority_epoch is None) or
                    (ledger_required and round_id == self.round_id and
                     self.authority_epoch is not None and
                     authority_epoch < self.authority_epoch) or
                    (ledger_required and 'server_time_ms' in message and
                     start_server_time is None)):
                self.last_error = 'invalid battle_start message'
                self.stop()
                return
            if round_id != self.round_id:
                self.last_snapshot = None
                self._fire_seq = 0
                self._fire_intent_seq = 0
                self._equipment_intent_seq = 0
                self._battle_start_round_id = None
                self._battle_live_round_id = None
                self._combat_timing_round_id = None
                self._combat_timing_tick = -1
                self.combat_deadline = None
                self.combat_end_deadline = None
                self.server_time_ms = None
            self.phase = phase
            self.map_name = map_name
            self.round_id = round_id
            self.state_revision = state_revision
            players = self._remember_player_outfits(players)
            self.roster = players
            self.team_sizes = team_sizes
            self._adopt_published_vehicle(players)
            self.host_player_id = host_player_id
            self._battle_start_round_id = round_id
            self.bot_authority_id = message.get(
                'bot_authority_id', self.bot_authority_id)
            if authority_epoch is not None:
                self.authority_epoch = authority_epoch
            if start_server_time is not None:
                self.server_time_ms = start_server_time
        elif kind == 'battle_live':
            round_id = _exact_int(message.get('round_id'))
            state_revision = _exact_int(message.get('state_revision'))
            authority_epoch = _projectile_int_range(
                message.get('authority_epoch'), 0, MAX_PROJECTILE_ID)
            countdown = _finite_float(
                message.get('countdown_seconds'), -1.0)
            duration = _finite_float(
                message.get('battle_duration_seconds'), -1.0)
            if (round_id is None or round_id != self.round_id or
                    self.phase not in ('loading', 'battle') or
                    state_revision is None or state_revision < 0 or
                    not _valid_visible_authority_id(
                        message.get('bot_authority_id')) or
                    (self.has_projectile_ledger() and
                     authority_epoch is None) or
                    (self.has_projectile_ledger() and
                     self.authority_epoch is not None and
                     authority_epoch < self.authority_epoch) or
                    countdown < 0.0 or duration <= 0.0):
                self.last_error = 'invalid battle_live message'
                self.stop()
                return
            if not self._load_server_timing(message):
                if not self._ignore_runtime_payload(
                        kind, 'invalid_timing', message):
                    self.last_error = 'invalid battle timing'
                    self.stop()
                    return
                message = dict(message)
                message.pop('timing', None)
            if self._battle_live_round_id == round_id:
                return
            self.phase = 'battle'
            if (self.state_revision is None or
                    state_revision > self.state_revision):
                self.state_revision = state_revision
            self._battle_live_round_id = round_id
            if authority_epoch is not None:
                self.authority_epoch = authority_epoch
        elif kind == 'start_denied':
            round_id = _exact_int(message.get('round_id'))
            if round_id is None or round_id != self.round_id:
                return
        elif kind == 'team_denied':
            round_id = _exact_int(message.get('round_id'))
            team = _team_choice(message.get('team'), None)
            code = _safe_text(message.get('code'), '', 32)
            team_sizes = _team_sizes(
                message.get('team_sizes'), None, self.team_sizes)
            if (round_id is None or round_id != self.round_id or
                    team not in (1, 2) or
                    code not in ('team_full', 'invalid_team', 'not_waiting') or
                    team_sizes is None):
                self.last_error = 'invalid team_denied message'
                self.stop()
                return
            self.team_sizes = team_sizes
        elif kind == 'team_size_denied':
            round_id = _exact_int(message.get('round_id'))
            team = _team_choice(message.get('team'), None)
            size = _exact_int(message.get('size'))
            code = _safe_text(message.get('code'), '', 32)
            team_sizes = _team_sizes(
                message.get('team_sizes'), None, self.team_sizes)
            if (round_id is None or round_id != self.round_id or
                    team not in (1, 2) or size is None or
                    not 1 <= size <= 15 or
                    code not in ('host_only', 'team_occupied',
                                 'invalid_team', 'invalid_size',
                                 'not_waiting') or
                    team_sizes is None):
                self.last_error = 'invalid team_size_denied message'
                self.stop()
                return
            self.team_sizes = team_sizes
        elif kind == 'bot_tier_mode_denied':
            round_id = _exact_int(message.get('round_id'))
            mode = _safe_text(message.get('bot_tier_mode'), '', 32)
            code = _safe_text(message.get('code'), '', 32)
            if (round_id is None or round_id != self.round_id or
                    mode not in BOT_TIER_MODES or
                    code not in ('host_only', 'invalid_mode',
                                 'not_waiting')):
                self.last_error = 'invalid bot_tier_mode_denied message'
                self.stop()
                return
            self.bot_tier_mode = mode
        elif kind == 'snapshot':
            round_id = self._runtime_round_disposition(
                kind, message, 'invalid snapshot message')
            if round_id is None:
                return
            authority_id = _exact_int(message.get('bot_authority_id'))
            if (authority_id is not None and
                    authority_id != WORKER_AUTHORITY_ID):
                # A well-formed alternate authority is an identity conflict,
                # not a damaged state sample.
                self.last_error = 'invalid snapshot message'
                self.stop()
                return
            server_tick = _exact_int(message.get('server_tick'))
            server_time_ms = _projectile_int_range(
                message.get('server_time_ms'), 0, MAX_PROJECTILE_ID)
            authority_epoch = _projectile_int_range(
                message.get('authority_epoch'), 0, MAX_PROJECTILE_ID)
            projectile_revision = _projectile_int_range(
                message.get('projectile_revision'), 0, MAX_PROJECTILE_ID)
            bot_state_revision = _projectile_int_range(
                message.get('bot_state_revision'), 0, MAX_PROJECTILE_ID)
            has_motion_time = 'motion_time_us' in message
            has_bot_state_time = 'bot_state_time_us' in message
            motion_time_us = (_projectile_int_range(
                message.get('motion_time_us'), 0, MAX_MOTION_TIME_US)
                if has_motion_time else None)
            bot_state_time_us = (_projectile_int_range(
                message.get('bot_state_time_us'), 0, MAX_MOTION_TIME_US)
                if has_bot_state_time else None)
            projectiles = message.get('projectiles')
            players = _strict_mapping_list(message.get('players'), 64)
            player_outfits_valid = all(
                ('outfits' not in value or
                 _canonical_wire_outfits(value.get('outfits')) is not None)
                for value in players or ())
            player_effective_params_valid = all(
                (_canonical_effective_params(
                    value.get('effective_params')) is not None
                 if 'effective_params' in value else
                 _exact_int(value.get('id')) in
                 self._published_player_effective_params)
                for value in players or ())
            bots = _strict_mapping_list(message.get('bots'), 30)
            manifest = None
            if 'bot_manifest' in message:
                manifest = _strict_mapping_list(
                    message.get('bot_manifest'), 30)
            orders = None
            order_revision = None
            if 'bot_orders' in message:
                orders = _strict_mapping_list(message.get('bot_orders'), 30)
                order_revision = _exact_int(
                    message.get('bot_order_revision'))
            destructibles = None
            destructible_revision = None
            if 'destructibles' in message:
                destructibles = _strict_mapping_list(
                    message.get('destructibles'), 4096)
                destructible_revision = _exact_int(
                    message.get('destructible_revision'))
            player_critical_contract = all(
                _exact_int(player.get('critical_revision')) is not None and
                _exact_int(player.get('critical_revision')) >= 0 and
                _exact_int(player.get('critical_base_revision')) is not None and
                _exact_int(player.get('critical_base_revision')) >= 0 and
                _exact_int(player.get('critical_ack_seq')) is not None and
                _exact_int(player.get('critical_ack_seq')) >= 0
                for player in players or ())
            player_equipment_contract = all(
                _valid_player_equipment_contract(player, required=True)
                for player in players or ())
            player_environment_contract = all(
                _valid_player_environment_contract(player, required=True)
                for player in players or ())
            player_siege_contract = all(
                _valid_player_siege_contract(player)
                for player in players or ())
            player_gun_checkpoint_contract = all(
                _valid_player_gun_checkpoint_contract(player)
                for player in players or ())
            player_stun_contract = all(
                _valid_stun_contract(player) for player in players or ())
            bot_combat_contract = all(
                _valid_bot_combat_contract(bot) for bot in bots or ())
            bot_siege_contract = all(
                _valid_bot_siege_contract(bot) for bot in bots or ())
            bot_stun_contract = all(
                _valid_stun_contract(bot) for bot in bots or ())
            ledger_required = self.has_projectile_ledger()
            previous_bot_state_revision = None
            previous_snapshot = None
            previous_motion_time = None
            previous_bot_state_time = None
            if (isinstance(self.last_snapshot, dict) and
                    _exact_int(self.last_snapshot.get('round_id')) ==
                    round_id):
                previous_snapshot = self.last_snapshot
                previous_bot_state_revision = _projectile_int_range(
                    self.last_snapshot.get('bot_state_revision'),
                    0, MAX_PROJECTILE_ID)
            motion_timing_valid = (
                has_motion_time == has_bot_state_time and
                (not has_motion_time or (
                    motion_time_us is not None and
                    bot_state_time_us is not None and
                    bot_state_time_us <= motion_time_us)))
            if motion_timing_valid and previous_snapshot is not None:
                previous_has_motion = (
                    'motion_time_us' in previous_snapshot)
                previous_has_bot_state = (
                    'bot_state_time_us' in previous_snapshot)
                if previous_has_motion != previous_has_bot_state:
                    motion_timing_valid = False
                elif previous_has_motion:
                    previous_motion_time = _projectile_int_range(
                        previous_snapshot.get('motion_time_us'), 0,
                        MAX_MOTION_TIME_US)
                    previous_bot_state_time = _projectile_int_range(
                        previous_snapshot.get('bot_state_time_us'), 0,
                        MAX_MOTION_TIME_US)
                    motion_timing_valid = bool(
                        has_motion_time and
                        previous_motion_time is not None and
                        previous_bot_state_time is not None and
                        motion_time_us >= previous_motion_time and
                        ((bot_state_revision ==
                          previous_bot_state_revision and
                          bot_state_time_us == previous_bot_state_time) or
                         (bot_state_revision >
                          previous_bot_state_revision and
                          bot_state_time_us > previous_bot_state_time)))
            valid_projectiles = (not ledger_required or (
                server_time_ms is not None and authority_epoch is not None and
                projectile_revision is not None and
                (self.authority_epoch is None or
                 authority_epoch >= self.authority_epoch) and
                _valid_active_projectiles(
                    projectiles, authority_epoch, server_time_ms)))
            lean_manifest_valid = bool(
                'bot_manifest' not in message and
                LEAN_SNAPSHOT_MANIFEST_CAPABILITY in
                self.server_capabilities and
                previous_snapshot is not None and
                isinstance(previous_snapshot.get('bot_manifest'), list) and
                message.get('bot_authority_id') ==
                previous_snapshot.get('bot_authority_id') and
                message.get('authority_epoch') ==
                previous_snapshot.get('authority_epoch'))
            invalid_reasons = []
            if server_tick is None or server_tick < 0:
                invalid_reasons.append('server_tick')
            if bot_state_revision is None:
                invalid_reasons.append('bot_state_revision')
            elif (previous_bot_state_revision is not None and
                  bot_state_revision < previous_bot_state_revision):
                invalid_reasons.append('bot_state_revision_regressed')
            if not motion_timing_valid:
                invalid_reasons.append('motion_timing')
            if not valid_projectiles:
                invalid_reasons.append('projectiles')
            if not _valid_visible_authority_message(message):
                invalid_reasons.append('bot_authority')
            if players is None:
                invalid_reasons.append('players')
            if not player_outfits_valid:
                invalid_reasons.append('player_outfits')
            if not player_effective_params_valid:
                invalid_reasons.append('player_effective_params')
            if bots is None:
                invalid_reasons.append('bots')
            if not player_critical_contract:
                invalid_reasons.append('player_critical')
            if not player_equipment_contract:
                invalid_reasons.append('player_equipment')
            if not player_environment_contract:
                invalid_reasons.append('player_environment')
            if not player_siege_contract:
                invalid_reasons.append('player_siege')
            if not player_gun_checkpoint_contract:
                invalid_reasons.append('player_gun_checkpoint')
            if not player_stun_contract:
                invalid_reasons.append('player_stun')
            if not bot_combat_contract:
                invalid_reasons.append('bot_combat')
            if not bot_siege_contract:
                invalid_reasons.append('bot_siege')
            if not bot_stun_contract:
                invalid_reasons.append('bot_stun')
            if 'bot_manifest' in message and manifest is None:
                invalid_reasons.append('bot_manifest')
            if ('bot_manifest' not in message and
                    not lean_manifest_valid):
                invalid_reasons.append('bot_manifest_missing')
            if ('bot_orders' in message and
                    (orders is None or order_revision is None or
                     order_revision < 0)):
                invalid_reasons.append('bot_orders')
            if ('destructibles' in message and
                    (destructibles is None or
                     destructible_revision is None or
                     destructible_revision < 0)):
                invalid_reasons.append('destructibles')
            if invalid_reasons:
                if self._ignore_runtime_payload(
                        kind, 'invalid_snapshot:' +
                        ','.join(invalid_reasons), message):
                    return
                bad_bot = next((
                    value for value in bots or ()
                    if not _valid_bot_combat_contract(value)), None)
                bad_bot_detail = None
                if isinstance(bad_bot, dict):
                    bad_bot_critical = bad_bot.get('critical')
                    bad_bot_detail = {
                        'id': bad_bot.get('id'),
                        'revision': bad_bot.get('combat_revision'),
                        'base': bad_bot.get('combat_base_revision'),
                        'ack': bad_bot.get('combat_ack_seq'),
                        'fire': (bad_bot_critical.get('fire')
                                 if isinstance(bad_bot_critical, dict)
                                 else None),
                        'elapsed': bad_bot.get('combat_fire_elapsed'),
                        'timer': bad_bot.get('combat_fire_timer'),
                    }
                print(
                    '[Offline LAN 0.9.22] snapshot rejected reasons=%s '
                    'round=%s tick=%s bot_revision=%s previous_revision=%s '
                    'motion_us=%s previous_motion_us=%s bot_state_us=%s '
                    'previous_bot_state_us=%s projectiles=%s bots=%s '
                    'players=%s bad_bot=%s' % (
                        ','.join(invalid_reasons), round_id, server_tick,
                        bot_state_revision, previous_bot_state_revision,
                        motion_time_us, previous_motion_time,
                        bot_state_time_us, previous_bot_state_time,
                        (len(projectiles)
                         if isinstance(projectiles, list) else None),
                        (len(bots) if bots is not None else None),
                        (len(players) if players is not None else None),
                        bad_bot_detail))
                self.last_error = 'invalid snapshot message'
                self.stop()
                return
            if ('timing' in message and
                    not self._load_server_timing(message)):
                if not self._ignore_runtime_payload(
                        kind, 'invalid_timing', message):
                    self.last_error = 'invalid battle timing'
                    self.stop()
                    return
                message = dict(message)
                message.pop('timing', None)
            players = self._remember_player_outfits(players)
            if players != message.get('players'):
                message = dict(message)
                message['players'] = players
            if ('bot_manifest' not in message and
                    lean_manifest_valid):
                message = dict(message)
                message['bot_manifest'] = [
                    dict(value)
                    for value in previous_snapshot.get('bot_manifest') or ()]
            self.last_snapshot = message
            local_player = next((
                player for player in players
                if _exact_int(player.get('id')) == self.player_id), None)
            if local_player is not None:
                self._equipment_intent_seq = max(
                    self._equipment_intent_seq,
                    int(local_player['equipment_intent_seq']))
            if server_time_ms is not None:
                self.server_time_ms = server_time_ms
            self.bot_authority_id = message.get(
                'bot_authority_id', self.bot_authority_id)
            if authority_epoch is not None:
                self.authority_epoch = authority_epoch
            self._adopt_player_input_frontier(players)
        elif kind == 'landing_observation_result':
            round_id = self._runtime_round_disposition(
                kind, message, 'invalid landing observation result')
            if round_id is None:
                return
            if not self._handle_landing_observation_result(message):
                if self._ignore_runtime_payload(
                        kind, 'invalid_result', message):
                    return
                self.last_error = 'invalid landing observation result'
                self.stop()
                return
        elif kind == 'events':
            round_id = self._runtime_round_disposition(
                kind, message, 'invalid events message')
            if round_id is None:
                return
            server_tick = _exact_int(message.get('server_tick'))
            events = _strict_mapping_list(message.get('events'), 256)
            ledger_required = self.has_projectile_ledger()
            events_server_time = _projectile_int_range(
                message.get('server_time_ms'), 0, MAX_PROJECTILE_ID)
            events_authority_epoch = _projectile_int_range(
                message.get('authority_epoch'), 0, MAX_PROJECTILE_ID)
            if (events_authority_epoch is not None and
                    self.authority_epoch is not None and
                    events_authority_epoch < self.authority_epoch):
                # Server threads may publish an older envelope after a newer
                # one. It is stale state, not a broken transport.
                return
            if (server_tick is None or server_tick < 0 or events is None or
                    (ledger_required and events_server_time is None) or
                    (ledger_required and events_authority_epoch is None) or
                    (ledger_required and self.authority_epoch is not None and
                     events_authority_epoch < self.authority_epoch)):
                if self._ignore_runtime_payload(
                        kind, 'invalid_envelope', message):
                    return
                self.last_error = 'invalid events message'
                self.stop()
                return
            authority_updates = []
            for event in events:
                if event.get('kind') not in ('authority', 'bot_authority'):
                    continue
                event_authority_epoch = _projectile_int_range(
                    event.get('authority_epoch'), 0, MAX_PROJECTILE_ID)
                authority_id = event.get('player_id')
                parsed_authority_id = _exact_int(authority_id)
                if (parsed_authority_id is not None and
                        parsed_authority_id != WORKER_AUTHORITY_ID):
                    self.last_error = 'invalid bot authority event'
                    self.stop()
                    return
                if not (_valid_visible_authority_id(authority_id) or
                        (authority_id is None and
                         _valid_visible_authority_message(message))):
                    if self._ignore_runtime_payload(
                            kind, 'invalid_authority_event', message):
                        return
                    self.last_error = 'invalid bot authority event'
                    self.stop()
                    return
                authority_id = (_exact_int(authority_id)
                                if authority_id is not None else None)
                if (event_authority_epoch is None or
                        (self.authority_epoch is not None and
                         event_authority_epoch < self.authority_epoch) or
                        (ledger_required and
                         event_authority_epoch > events_authority_epoch)):
                    if self._ignore_runtime_payload(
                            kind, 'invalid_authority_epoch', message):
                        return
                    self.last_error = 'invalid bot authority event'
                    self.stop()
                    return
                authority_updates.append(
                    (authority_id, event_authority_epoch))
            for authority_id, event_authority_epoch in authority_updates:
                self.bot_authority_id = authority_id
                self.authority_epoch = event_authority_epoch
            if events_authority_epoch is not None:
                self.authority_epoch = events_authority_epoch
            if events_server_time is not None:
                self.server_time_ms = events_server_time
        elif kind == 'bot_observation':
            round_id = self._runtime_round_disposition(
                kind, message, 'invalid bot observation message')
            if round_id is None:
                return
            contacts = _strict_mapping_list(message.get('contacts'), 64)
            valid_contacts = contacts is not None and all(
                _exact_int(contact.get('observing_team')) in (1, 2) and
                _exact_int(contact.get('target_team')) in (1, 2) and
                _exact_int(contact.get('observing_team')) !=
                _exact_int(contact.get('target_team')) and
                _exact_int(contact.get('target_id')) is not None and
                _exact_int(contact.get('target_id')) > 0 and
                _safe_text(contact.get('target_kind'), '') in
                ('human', 'bot') and
                isinstance(contact.get('visible'), bool) and
                isinstance(contact.get('fresh'), bool) and
                _projectile_float_range(
                    contact.get('time_left'), 0.0,
                    spotting.DESIGNATED_SPOT_MEMORY_SECONDS) is not None and
                bool(contact.get('visible')) == (
                    float(contact.get('time_left')) > 0.0) and
                isinstance(contact.get('visible_by_bot_ids'),
                           (list, tuple)) and
                isinstance(contact.get('visible_by_player_ids'),
                           (list, tuple)) and
                isinstance(contact.get('shootable_by_bot_ids'),
                           (list, tuple)) and
                all(_projectile_int_range(value, 1, MAX_PROJECTILE_ID)
                    is not None for value in
                    contact.get('visible_by_bot_ids')) and
                all(_projectile_int_range(value, 1, MAX_PROJECTILE_ID)
                    is not None for value in
                    contact.get('visible_by_player_ids')) and
                all(_projectile_int_range(value, 1, MAX_PROJECTILE_ID)
                    is not None for value in
                    contact.get('shootable_by_bot_ids')) and
                len(set(contact.get('visible_by_bot_ids'))) ==
                len(contact.get('visible_by_bot_ids')) and
                len(set(contact.get('visible_by_player_ids'))) ==
                len(contact.get('visible_by_player_ids')) and
                len(set(contact.get('shootable_by_bot_ids'))) ==
                len(contact.get('shootable_by_bot_ids')) and
                bool(contact.get('fresh')) == bool(
                    contact.get('visible_by_bot_ids') or
                    contact.get('visible_by_player_ids')) and
                (not bool(contact.get('fresh')) or
                 bool(contact.get('visible'))) and
                (bool(contact.get('fresh')) or
                 not contact.get('shootable_by_bot_ids'))
                for contact in (contacts or ()))
            if self.phase != 'battle' or not valid_contacts:
                if self._ignore_runtime_payload(
                        kind, 'invalid_contacts', message):
                    return
                self.last_error = 'invalid bot observation message'
                self.stop()
                return
            message = dict(message)
            message['contacts'] = contacts
            if 'server_time_ms' in message:
                self.server_time_ms = _projectile_int_range(
                    message.get('server_time_ms'), 0, MAX_PROJECTILE_ID)
        elif kind == 'pong':
            client_time = _finite_float(message.get('client_time'), 0.0)
            if client_time > 0.0:
                received_time = _finite_float(
                    message.get('_client_received_time'), _monotonic_time())
                sample = max(
                    0.0, (received_time - client_time) * 1000.0)
                if self.rtt_ms is None:
                    self.rtt_ms = sample
                else:
                    self.rtt_ms = self.rtt_ms * 0.75 + sample * 0.25
                if (self.minimum_rtt_ms is None or
                        sample < self.minimum_rtt_ms):
                    self.minimum_rtt_ms = sample
        elif kind == 'error':
            error_message = _safe_text(
                message.get('message'), message.get('code') or 'server error')
            self._notify('error', {
                'message': error_message,
                'code': _safe_text(message.get('code'), '', 32),
            })
            return
        self._notify(kind, message)

    def _notify(self, kind, message):
        if self.on_event is not None and kind is not None:
            if (isinstance(message, dict) and
                    '_client_received_time' in message):
                # Keep a duration rather than forwarding the receive clock's
                # epoch. SnapshotSync normally runs on BigWorld.time(), while
                # the socket thread uses the process monotonic clock.
                dispatched = _monotonic_time()
                received = _finite_float(
                    message.get('_client_received_time'), dispatched)
                message = dict(message)
                message['_client_dispatch_delay'] = max(
                    0.0, dispatched - received)
            self.on_event(kind, message)

    @staticmethod
    def _map_names(values):
        if not isinstance(values, (list, tuple)):
            return []
        result = []
        for value in values or ():
            name = _safe_text(value, '', 80)
            if name and name not in result:
                result.append(name)
        return result
