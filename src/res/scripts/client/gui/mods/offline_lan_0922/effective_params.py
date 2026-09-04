from __future__ import print_function

"""Canonical wire contract for client-derived effective vehicle parameters.

The garage client is the only endpoint that owns the mounted crew,
consumables, optional devices and ammunition.  It therefore publishes the
final values computed by the exact #1513 item pipeline.  The server and the
hidden native worker validate and relay this immutable round input; neither
endpoint reconstructs a second loadout from a bare descriptor.

This module intentionally depends only on the standard library and remains
importable on both the embedded Python 2 client and the Python 3 server.
"""

import math

from gui.mods.offline_lan_0922 import equipment_mechanics


SCHEMA_VERSION = 1
CAPABILITY = 'effective_params_v1'
# Vehicle XML stores these values in 32-bit-era numeric fields.  Keep one
# generous finite wire bound so trusted editor values are preserved without
# admitting infinities or arithmetic-scale payloads.
MAX_CRITICAL_DEVICE_HP = 1000000000.0

_LOADOUT_FLOATS = (
    'crew_level', 'commander_level', 'effective_crew_level',
    'crew_multiplier', 'crew_factor', 'gun_rotation_factor',
    'reload_factor', 'aim_time_factor', 'dispersion_factor',
    'repair_factor', 'vehicle_rotation_factor', 'radio_factor',
    'bloom_move_factor', 'bloom_rotation_factor',
    'bloom_turret_factor',
)
_LOADOUT_BOOLS = (
    'has_big_kit', 'from_client_factors', 'has_rammer',
    'has_aim_drives', 'has_ventilation', 'has_stabiliser', 'has_rations',
    'has_brotherhood', 'has_snap_shot', 'has_smooth_ride',
    'has_sixth_sense',
)
_LOADOUT_KEYS = frozenset(
    _LOADOUT_FLOATS + _LOADOUT_BOOLS + ('terrain_resistance_factors',))

_PHYSICS_FLOATS = (
    'mass', 'powerW', 'speedFwd', 'speedBwd', 'rotSpd',
    'specificFriction', 'brakeDecel', 'trackCenter', 'minPlaneNormalY',
    'nativePowerRatio',
)
_PHYSICS_KEYS = frozenset(_PHYSICS_FLOATS + ('terrainResist',))

_SPOTTING_FLOATS = (
    'commander_level', 'recon_level', 'situational_level',
    'camouflage_level', 'binocular_factor', 'binocular_delay',
    'camouflage_net_bonus', 'camouflage_net_delay', 'vision_factor',
    'camouflage_factor',
)
_SPOTTING_BOOLS = (
    'has_binoculars', 'has_camouflage_net', 'from_client_factors',
)
_SPOTTING_KEYS = frozenset(
    _SPOTTING_FLOATS + _SPOTTING_BOOLS +
    ('invisibility_moving', 'invisibility_still'))

_RAMMING_KEYS = frozenset(('spall_coefficient', 'ramming_bonus'))
_CAMOUFLAGE_KEYS = frozenset(
    ('camouflage_id', 'base_moving', 'base_still', 'shot_factor'))
_SKILL_KEYS = frozenset((
    'sixth_sense', 'expert', 'deadeye', 'intuition_chances',
    'controlled_impact', 'designated_target', 'last_effort'))
_CREW_KEYS = frozenset(('members', 'dynamic_spotting'))
_CREW_MEMBER_KEYS = frozenset(('instance', 'roles', 'skills'))
_CREW_SKILL_KEYS = frozenset(
    ('name', 'level', 'active', 'enabled'))
_DYNAMIC_SPOTTING_KEYS = frozenset(('crew', 'states'))
_DYNAMIC_SPOTTING_ROW_KEYS = frozenset((
    'vision', 'signal', 'camouflage', 'base_moving', 'base_still',
    'invisibility_moving', 'invisibility_still'))
DISCRETE_SKILL_ROLES = {
    'commander_sixthsense': 'commander',
    'commander_expert': 'commander',
    'gunner_sniper': 'gunner',
    'loader_intuition': 'loader',
    'driver_rammingmaster': 'driver',
    'gunner_rancorous': 'gunner',
    'radioman_lasteffort': 'radioman',
}
MAX_PROJECTED_SKILLS_PER_MEMBER = 32
_SKILL_SUMMARY_NAMES = (
    ('sixth_sense', 'commander_sixthsense'),
    ('expert', 'commander_expert'),
    ('deadeye', 'gunner_sniper'),
    ('controlled_impact', 'driver_rammingmaster'),
    ('designated_target', 'gunner_rancorous'),
    ('last_effort', 'radioman_lasteffort'),
)
_GUN_KEYS = frozenset(('clip_size', 'shots'))
_GUN_SHOT_KEYS = frozenset(('compact_descr', 'source_shot'))
_SOURCE_SHOT_KEYS = frozenset((
    'speed', 'gravity', 'maxDistance', 'piercingPower', 'deadeye', 'shell'))
_SOURCE_SHELL_KEYS = frozenset((
    'kind', 'caliber', 'damage', 'explosionRadius'))
_SOURCE_SHELL_HE_FACTOR_KEYS = frozenset((
    'explosionDamageFactor', 'explosionDamageAbsorptionFactor',
    'explosionEdgeDamageFactor'))
_PROJECTILE_SHELL_KINDS = frozenset((
    'HOLLOW_CHARGE', 'HIGH_EXPLOSIVE', 'ARMOR_PIERCING',
    'ARMOR_PIERCING_HE', 'ARMOR_PIERCING_CR'))
_TOP_LEVEL_KEYS = frozenset((
    'version', 'loadout', 'physics', 'spotting', 'ramming', 'ammo',
    'camouflage', 'skills', 'crew', 'gun'))
_TOP_LEVEL_KEYS_WITH_EQUIPMENT = frozenset(
    tuple(_TOP_LEVEL_KEYS) + ('equipment',))
_TOP_LEVEL_KEYS_WITH_CRITICAL = frozenset(
    tuple(_TOP_LEVEL_KEYS) + ('equipment', 'critical'))
_CRITICAL_KEYS = frozenset(('devices',))
_CRITICAL_KEYS_WITH_TARGETS = frozenset(
    ('devices', 'activation_targets'))
_CRITICAL_KEYS_COMPLETE = frozenset(
    ('devices', 'activation_targets', 'crew_roster'))
_CRITICAL_DEVICE_KEYS = frozenset(('name', 'max_hp', 'regen_hp'))
_CRITICAL_TARGET_KEYS = frozenset(('index', 'name'))
_CRITICAL_DEVICE_NAMES = frozenset((
    'engineHealth', 'ammoBayHealth', 'fuelTankHealth', 'radioHealth',
    'leftTrackHealth', 'rightTrackHealth', 'gunHealth',
    'turretRotatorHealth', 'surveyingDeviceHealth'))
_CRITICAL_TARGET_NAMES = _CRITICAL_DEVICE_NAMES | frozenset((
    'commander', 'driver', 'gunner', 'gunner1', 'gunner2', 'loader',
    'loader1', 'loader2', 'radioman', 'radioman1', 'radioman2'))


try:
    integer_types = (int, long)
except NameError:
    integer_types = (int,)

try:
    string_types = (basestring,)
except NameError:
    string_types = (str,)


def _exact_int(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, integer_types):
        return None
    value = int(value)
    return value if minimum <= value <= maximum else None


def _number(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        return None
    return value if minimum <= value <= maximum else None


def _bool(value):
    return value if isinstance(value, bool) else None


def _tuple(value, size, minimum, maximum):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        return None
    result = []
    for entry in value:
        entry = _number(entry, minimum, maximum)
        if entry is None:
            return None
        result.append(entry)
    return result


def _mapping(value, keys):
    return isinstance(value, dict) and set(value) == keys


def _valid_skill_name(value):
    if not isinstance(value, string_types) or not 1 <= len(value) <= 64:
        return False
    try:
        name = str(value)
    except Exception:
        return False
    allowed = 'abcdefghijklmnopqrstuvwxyz0123456789_'
    return name == name.lower() and all(
        character in allowed for character in name)


def skill_required_role(name):
    """Return a role encoded by an exact or role-prefixed skill name."""
    name = str(name).lower()
    exact = DISCRETE_SKILL_ROLES.get(name)
    if exact is not None:
        return exact
    prefix = name.split('_', 1)[0]
    if prefix in ('commander', 'driver', 'gunner', 'loader', 'radioman'):
        return prefix
    return None


def _canonical_loadout(value):
    if not _mapping(value, _LOADOUT_KEYS):
        return None
    result = {}
    for name in _LOADOUT_FLOATS:
        maximum = 1000.0 if name.endswith('_level') else 100.0
        number = _number(value.get(name), 0.0, maximum)
        if number is None:
            return None
        result[name] = number
    for name in _LOADOUT_BOOLS:
        flag = _bool(value.get(name))
        if flag is None:
            return None
        result[name] = flag
    if not result['from_client_factors']:
        return None
    terrain = _tuple(
        value.get('terrain_resistance_factors'), 3, 0.000001, 1000.0)
    if terrain is None:
        return None
    result['terrain_resistance_factors'] = terrain
    return result


def _canonical_physics(value):
    if not _mapping(value, _PHYSICS_KEYS):
        return None
    bounds = {
        'mass': (1.0, 1000000.0),
        'powerW': (1.0, 1000000000.0),
        'speedFwd': (0.0, 1000.0),
        'speedBwd': (0.0, 1000.0),
        'rotSpd': (0.0, 100.0),
        'specificFriction': (0.0, 1000.0),
        'brakeDecel': (0.0, 10000.0),
        'trackCenter': (0.01, 100.0),
        'minPlaneNormalY': (-1.0, 1.0),
        'nativePowerRatio': (0.000001, 1000.0),
    }
    result = {}
    for name in _PHYSICS_FLOATS:
        number = _number(value.get(name), *bounds[name])
        if number is None:
            return None
        result[name] = number
    terrain = _tuple(value.get('terrainResist'), 3, 0.000001, 1000000.0)
    if terrain is None:
        return None
    result['terrainResist'] = terrain
    return result


def _canonical_spotting(value):
    if not _mapping(value, _SPOTTING_KEYS):
        return None
    result = {}
    for name in _SPOTTING_FLOATS:
        maximum = 1000.0 if name.endswith('_level') else 100.0
        number = _number(value.get(name), 0.0, maximum)
        if number is None:
            return None
        result[name] = number
    for name in _SPOTTING_BOOLS:
        flag = _bool(value.get(name))
        if flag is None:
            return None
        result[name] = flag
    if not result['from_client_factors']:
        return None
    for name in ('invisibility_moving', 'invisibility_still'):
        pair = _tuple(value.get(name), 2, -100.0, 100.0)
        if pair is None or pair[1] < 0.0:
            return None
        result[name] = pair
    return result


def _canonical_ramming(value):
    if not _mapping(value, _RAMMING_KEYS):
        return None
    spall = _number(value.get('spall_coefficient'), 1.0, 100.0)
    bonus = _number(value.get('ramming_bonus'), 0.0, 0.15)
    if spall is None or bonus is None:
        return None
    return {'spall_coefficient': spall, 'ramming_bonus': bonus}


def _canonical_ammo(value):
    if not isinstance(value, (list, tuple)) or len(value) > 64:
        return None
    result = []
    previous = -1
    for entry in value:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            return None
        compact_descr = _exact_int(entry[0], 1, 4294967295)
        count = _exact_int(entry[1], 0, 1000000)
        if compact_descr is None or count is None or compact_descr <= previous:
            return None
        previous = compact_descr
        result.append([compact_descr, count])
    return result


def _canonical_camouflage(value):
    if not _mapping(value, _CAMOUFLAGE_KEYS):
        return None
    camouflage_id = value.get('camouflage_id')
    if camouflage_id is not None:
        camouflage_id = _exact_int(camouflage_id, 0, 4294967295)
        if camouflage_id is None:
            return None
    moving = _number(value.get('base_moving'), 0.0, 100.0)
    still = _number(value.get('base_still'), 0.0, 100.0)
    shot = _number(value.get('shot_factor'), 0.0, 1.0)
    if moving is None or still is None or shot is None:
        return None
    return {
        'camouflage_id': camouflage_id,
        'base_moving': moving,
        'base_still': still,
        'shot_factor': shot,
    }


def _canonical_skills(value):
    if not _mapping(value, _SKILL_KEYS):
        return None
    result = {}
    for name in (
            'sixth_sense', 'expert', 'deadeye', 'controlled_impact',
            'designated_target', 'last_effort'):
        flag = _bool(value.get(name))
        if flag is None:
            return None
        result[name] = flag
    intuition = _exact_int(value.get('intuition_chances'), 0, 16)
    if intuition is None:
        return None
    result['intuition_chances'] = intuition
    return result


def _canonical_source_shot(value):
    """Freeze one exact mounted-shell law for worker-side resolution."""
    if not _mapping(value, _SOURCE_SHOT_KEYS):
        return None
    shell = value.get('shell')
    if (not isinstance(shell, dict) or
            set(shell) not in (
                _SOURCE_SHELL_KEYS,
                _SOURCE_SHELL_KEYS | _SOURCE_SHELL_HE_FACTOR_KEYS)):
        return None
    kind = shell.get('kind')
    deadeye = _bool(value.get('deadeye'))
    if (not isinstance(kind, string_types) or
            kind not in _PROJECTILE_SHELL_KINDS or deadeye is None):
        return None
    speed = _number(value.get('speed'), 0.000001, 3000.0)
    gravity = _number(value.get('gravity'), 0.000001, 500.0)
    maximum = _number(value.get('maxDistance'), 0.000001, 10000.0)
    piercing = _tuple(value.get('piercingPower'), 2, 0.0, 10000.0)
    caliber = _number(shell.get('caliber'), 0.000001, 1000.0)
    raw_damage = shell.get('damage')
    damage = None
    if isinstance(raw_damage, (list, tuple)) and len(raw_damage) == 2:
        hull_damage = _number(raw_damage[0], 0.0, 10000.0)
        device_damage = _number(
            raw_damage[1], 0.0, MAX_CRITICAL_DEVICE_HP)
        if hull_damage is not None and device_damage is not None:
            damage = [hull_damage, device_damage]
    radius = _number(shell.get('explosionRadius'), 0.0, 100.0)
    he_factors = None
    if _SOURCE_SHELL_HE_FACTOR_KEYS.issubset(set(shell)):
        he_factors = {
            'explosionDamageFactor': _number(
                shell.get('explosionDamageFactor'), 0.000001, 10000.0),
            'explosionDamageAbsorptionFactor': _number(
                shell.get('explosionDamageAbsorptionFactor'),
                0.000001, 10000.0),
            'explosionEdgeDamageFactor': _number(
                shell.get('explosionEdgeDamageFactor'), 0.000001, 1.0),
        }
    if (speed is None or gravity is None or maximum is None or
            piercing is None or caliber is None or damage is None or
            damage[0] <= 0.0 or radius is None or
            (he_factors is not None and
             any(entry is None for entry in he_factors.values()))):
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


def _canonical_gun(value):
    """Validate shot order and clip shape donated by the mounted gun."""
    if not _mapping(value, _GUN_KEYS):
        return None
    clip_size = _exact_int(value.get('clip_size'), 1, 255)
    shots = value.get('shots')
    if (clip_size is None or not isinstance(shots, (list, tuple)) or
            not 1 <= len(shots) <= 64):
        return None
    result = []
    compact_descrs = set()
    for entry in shots:
        if not _mapping(entry, _GUN_SHOT_KEYS):
            return None
        compact_descr = _exact_int(
            entry.get('compact_descr'), 1, 4294967295)
        source_shot = _canonical_source_shot(entry.get('source_shot'))
        if (compact_descr is None or compact_descr in compact_descrs or
                source_shot is None):
            return None
        compact_descrs.add(compact_descr)
        result.append({
            'compact_descr': compact_descr,
            'source_shot': source_shot,
        })
    return {'clip_size': clip_size, 'shots': result}


def _canonical_equipment(value):
    """Validate the immutable mounted regular-consumable contracts."""
    if not isinstance(value, (list, tuple)) or len(value) > 3:
        return None
    result = []
    ids = set()
    compact_descriptors = set()
    for raw in value:
        try:
            contract = equipment_mechanics._validate_contract(raw)
        except (TypeError, ValueError):
            return None
        equipment_id = contract['id']
        compact_descriptor = contract['compactDescr']
        if (equipment_id <= 0 or compact_descriptor <= 0 or
                equipment_id in ids or
                compact_descriptor in compact_descriptors):
            return None
        ids.add(equipment_id)
        compact_descriptors.add(compact_descriptor)
        result.append(contract)
    return result


def _canonical_critical(value):
    """Validate final mounted module pools donated by the exact client."""
    if (not isinstance(value, dict) or
            set(value) not in (
                _CRITICAL_KEYS, _CRITICAL_KEYS_WITH_TARGETS,
                _CRITICAL_KEYS_COMPLETE)):
        return None
    devices = value.get('devices')
    if (not isinstance(devices, (list, tuple)) or
            not 1 <= len(devices) <= len(_CRITICAL_DEVICE_NAMES)):
        return None
    result = []
    seen = set()
    for entry in devices:
        if not _mapping(entry, _CRITICAL_DEVICE_KEYS):
            return None
        name = entry.get('name')
        maximum = _number(
            entry.get('max_hp'), 1.0, MAX_CRITICAL_DEVICE_HP)
        regen = _number(
            entry.get('regen_hp'), 0.0, MAX_CRITICAL_DEVICE_HP)
        if (not isinstance(name, string_types) or
                name not in _CRITICAL_DEVICE_NAMES or name in seen or
                maximum is None or regen is None or regen > maximum):
            return None
        seen.add(name)
        result.append({
            'name': str(name), 'max_hp': maximum, 'regen_hp': regen})
    result.sort(key=lambda entry: entry['name'])
    targets = []
    target_indexes = set()
    for raw in value.get('activation_targets') or ():
        if not _mapping(raw, _CRITICAL_TARGET_KEYS):
            return None
        index = _exact_int(raw.get('index'), 1, 65535)
        name = raw.get('name')
        if (index is None or index in target_indexes or
                not isinstance(name, string_types) or
                name not in _CRITICAL_TARGET_NAMES):
            return None
        target_indexes.add(index)
        targets.append({'index': index, 'name': str(name)})
    targets.sort(key=lambda entry: entry['index'])
    canonical = {'devices': result}
    if 'activation_targets' in value:
        canonical['activation_targets'] = targets
    if 'crew_roster' in value:
        raw_roster = value.get('crew_roster')
        if (not isinstance(raw_roster, (list, tuple)) or
                not 1 <= len(raw_roster) <= 11):
            return None
        roster = []
        for raw in raw_roster:
            if (not isinstance(raw, string_types) or
                    raw not in _CRITICAL_TARGET_NAMES or
                    raw in _CRITICAL_DEVICE_NAMES or raw in roster):
                return None
            roster.append(str(raw))
        canonical['crew_roster'] = roster
    return canonical


def _canonical_crew(value):
    """Validate physical slots, discrete perks and native spotting states."""
    if not _mapping(value, _CREW_KEYS):
        return None
    raw_members = value.get('members')
    if (not isinstance(raw_members, (list, tuple)) or
            not 1 <= len(raw_members) <= 6):
        return None
    members = []
    instances = []
    allowed_roles = frozenset(
        ('commander', 'driver', 'gunner', 'loader', 'radioman'))
    for raw in raw_members:
        if not _mapping(raw, _CREW_MEMBER_KEYS):
            return None
        instance = raw.get('instance')
        roles = raw.get('roles')
        skills = raw.get('skills')
        if (not isinstance(instance, string_types) or
                instance not in _CRITICAL_TARGET_NAMES or
                instance in _CRITICAL_DEVICE_NAMES or
                instance in instances or
                not isinstance(roles, (list, tuple)) or
                not 1 <= len(roles) <= len(allowed_roles) or
                not isinstance(skills, (list, tuple)) or
                len(skills) > MAX_PROJECTED_SKILLS_PER_MEMBER):
            return None
        canonical_roles = []
        for role in roles:
            if (not isinstance(role, string_types) or
                    role not in allowed_roles or role in canonical_roles):
                return None
            canonical_roles.append(str(role))
        base_instance = str(instance).rstrip('0123456789')
        if base_instance not in canonical_roles:
            return None
        canonical_skills = []
        skill_names = set()
        for skill in skills:
            if not _mapping(skill, _CREW_SKILL_KEYS):
                return None
            name = skill.get('name')
            active = _bool(skill.get('active'))
            enabled = _bool(skill.get('enabled'))
            level = _number(skill.get('level'), 0.0, 100.0)
            required_role = (skill_required_role(name)
                             if _valid_skill_name(name) else None)
            if (not _valid_skill_name(name) or name in skill_names or
                    active is None or enabled is None or level is None or
                    required_role is not None and
                    required_role not in canonical_roles):
                return None
            skill_names.add(name)
            canonical_skills.append({
                'name': str(name), 'level': level,
                'active': active, 'enabled': enabled})
        canonical_skills.sort(key=lambda entry: entry['name'])
        instances.append(str(instance))
        members.append({
            'instance': str(instance), 'roles': canonical_roles,
            'skills': canonical_skills})

    dynamic = value.get('dynamic_spotting')
    if not _mapping(dynamic, _DYNAMIC_SPOTTING_KEYS):
        return None
    raw_roster = dynamic.get('crew')
    raw_states = dynamic.get('states')
    if (not isinstance(raw_roster, (list, tuple)) or
            list(raw_roster) != instances or not isinstance(raw_states, dict)):
        return None
    expected = set('%d:%d' % (mask, fire)
                   for mask in range(1 << len(instances))
                   for fire in (0, 1))
    if set(raw_states) != expected:
        return None
    states = {}
    for key in sorted(expected):
        row = raw_states.get(key)
        if not _mapping(row, _DYNAMIC_SPOTTING_ROW_KEYS):
            return None
        canonical_row = {}
        for name in ('vision', 'signal', 'camouflage'):
            value = _number(row.get(name), 0.0, 10.0)
            if value is None:
                return None
            canonical_row[name] = value
        for name in ('base_moving', 'base_still'):
            value = _number(row.get(name), 0.0, 100.0)
            if value is None:
                return None
            canonical_row[name] = value
        for name in ('invisibility_moving', 'invisibility_still'):
            pair = _tuple(row.get(name), 2, -100.0, 100.0)
            if pair is None or pair[1] < 0.0:
                return None
            canonical_row[name] = pair
        states[key] = canonical_row
    return {
        'members': members,
        'dynamic_spotting': {'crew': list(instances), 'states': states},
    }


def _crew_projection(value):
    if not isinstance(value, dict):
        raise ValueError('crew skill snapshot is invalid')
    crew = value.get('crew') if 'crew' in value else value
    if not isinstance(crew, dict) or not isinstance(crew.get('members'), list):
        raise ValueError('crew skill snapshot is invalid')
    return crew


def _knocked_out_instances(crew, crew_ko):
    if isinstance(crew_ko, dict):
        crew_ko = crew_ko.get('crew_ko', ())
    if not isinstance(crew_ko, (list, tuple, set, frozenset)):
        raise ValueError('critical crew state is invalid')
    roster = set(str(member['instance']) for member in crew['members'])
    knocked_out = set(str(instance) for instance in crew_ko)
    if knocked_out.difference(roster):
        raise ValueError('critical crew state is outside its snapshot')
    return knocked_out


def living_skill_states(value, wanted, crew_ko=()):
    """Return completed skill states whose physical carriers are conscious.

    The round snapshot owns immutable skill affiliation.  ``crew_ko`` owns
    current health; a medkit is represented by the repaired instance leaving
    that current set.  No role-wide substitution is inferred when one of two
    loaders is knocked out.
    """
    wanted = str(wanted).lower()
    if not _valid_skill_name(wanted):
        raise ValueError('invalid crew skill name')
    crew = _crew_projection(value)
    knocked_out = _knocked_out_instances(crew, crew_ko)
    result = []
    for member in crew['members']:
        if str(member['instance']) in knocked_out:
            continue
        for skill in member['skills']:
            if (skill['name'] == wanted and skill['active'] is True and
                    skill['enabled'] is True and skill['level'] >= 100.0):
                result.append({
                    'instance': str(member['instance']),
                    'roles': list(member['roles']),
                    'level': float(skill['level']),
                })
    return result


def living_skill_carriers(value, wanted, crew_ko=()):
    """Return physical instance names for conscious completed carriers."""
    return tuple(state['instance'] for state in
                 living_skill_states(value, wanted, crew_ko))


def living_skill_count(value, wanted, crew_ko=()):
    """Count conscious completed carriers of one discrete perk."""
    return len(living_skill_states(value, wanted, crew_ko))


def living_skill_level(value, wanted, crew_ko=()):
    """Return the highest active and enabled level on a conscious carrier."""
    wanted = str(wanted).lower()
    if not _valid_skill_name(wanted):
        raise ValueError('invalid crew skill name')
    crew = _crew_projection(value)
    knocked_out = _knocked_out_instances(crew, crew_ko)
    result = 0.0
    for member in crew['members']:
        if str(member['instance']) in knocked_out:
            continue
        for skill in member['skills']:
            if (skill['name'] == wanted and skill['active'] is True and
                    skill['enabled'] is True):
                result = max(result, float(skill['level']))
    return result


def skill_summary(value, crew_ko=()):
    """Derive convenience flags from authoritative physical carriers."""
    result = {}
    for summary_name, skill_name in _SKILL_SUMMARY_NAMES:
        result[summary_name] = bool(
            living_skill_count(value, skill_name, crew_ko))
    result['intuition_chances'] = living_skill_count(
        value, 'loader_intuition', crew_ko)
    return result


def canonical(value):
    """Return a detached canonical snapshot, or ``None`` when invalid."""
    if (not isinstance(value, dict) or
            set(value) not in (
                _TOP_LEVEL_KEYS, _TOP_LEVEL_KEYS_WITH_EQUIPMENT,
                _TOP_LEVEL_KEYS_WITH_CRITICAL)):
        return None
    if _exact_int(value.get('version'), 1, 1) != SCHEMA_VERSION:
        return None
    loadout = _canonical_loadout(value.get('loadout'))
    physics = _canonical_physics(value.get('physics'))
    spotting = _canonical_spotting(value.get('spotting'))
    ramming = _canonical_ramming(value.get('ramming'))
    ammo = _canonical_ammo(value.get('ammo'))
    camouflage = _canonical_camouflage(value.get('camouflage'))
    skills = _canonical_skills(value.get('skills'))
    crew = _canonical_crew(value.get('crew'))
    gun = _canonical_gun(value.get('gun'))
    equipment = _canonical_equipment(value.get('equipment', ()))
    critical = (_canonical_critical(value.get('critical'))
                if 'critical' in value else None)
    if any(entry is None for entry in (
            loadout, physics, spotting, ramming, ammo, camouflage, skills,
            crew,
            gun, equipment)):
        return None
    if 'critical' in value and critical is None:
        return None
    if (critical is not None and critical.get('crew_roster') is not None and
            critical.get('crew_roster') !=
            crew['dynamic_spotting']['crew']):
        return None
    try:
        expected_skills = skill_summary(crew)
        controlled_impact_level = living_skill_level(
            crew, 'driver_rammingmaster')
    except (KeyError, TypeError, ValueError):
        return None
    if skills != expected_skills:
        return None
    if loadout['has_sixth_sense'] != skills['sixth_sense']:
        return None
    expected_ramming_bonus = controlled_impact_level * 0.0015
    if abs(ramming['ramming_bonus'] - expected_ramming_bonus) > 0.0000001:
        return None
    if any(shot['source_shot']['deadeye'] != skills['deadeye']
           for shot in gun['shots']):
        return None
    result = {
        'version': SCHEMA_VERSION,
        'loadout': loadout,
        'physics': physics,
        'spotting': spotting,
        'ramming': ramming,
        'ammo': ammo,
        'camouflage': camouflage,
        'skills': skills,
        'crew': crew,
        'gun': gun,
        'equipment': equipment,
    }
    if critical is not None:
        result['critical'] = critical
    return result
