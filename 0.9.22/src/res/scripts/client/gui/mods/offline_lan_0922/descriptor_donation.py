from __future__ import print_function

"""Freeze only the mounted shot law and lobby vehicle tiers for LAN."""

from gui.mods.offline_lan_0922 import vehicle_blacklist
from gui.mods.offline_lan_0922 import vehicle_configuration
from gui.mods.offline_lan_0922 import vehicle_physics
from gui.mods.offline_lan_0922 import loadout as loadout_law
from gui.mods.offline_lan_0922 import tank_collision


_MAX_REPAIR_FACTOR = 100.0
_MAX_SPOTTING_RANGE_METRES = 1000.0
_MAX_SPOTTING_FACTOR = 10.0
_MAX_SPOTTING_ASPECT_ADDITIVE = 10.0
_MAX_SPOTTING_DELAY_SECONDS = 60.0
_MICROSECONDS_PER_SECOND = 1000000.0
_CAMOUFLAGE_UNAVAILABLE = object()


def _value(source, name, default=None):
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _json_safe(value, depth=0):
    if depth > 6:
        return None
    if isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        if value != value or abs(value) == float("inf"):
            return None
        return value
    if isinstance(value, str):
        return value
    try:
        text_types = (unicode,)
    except NameError:
        text_types = ()
    if isinstance(value, text_types):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, depth + 1) for item in value]
    if isinstance(value, dict):
        return dict((str(key), _json_safe(item, depth + 1))
                    for key, item in value.items())
    if value is None or isinstance(value, bool):
        return value
    try:
        return [_json_safe(item, depth + 1) for item in list(value)]
    except Exception:
        return None


def _copy_fields(source, names):
    result = {}
    for name in names:
        value = _value(source, name)
        if value is not None:
            safe = _json_safe(value)
            if safe is not None:
                result[name] = safe
    return result


def _hit_tester_bbox(component):
    tester = _value(component, 'hitTester')
    if tester is None:
        return None
    bbox = getattr(tester, 'bbox', None)
    if bbox is None:
        load = getattr(tester, 'loadBspModel', None)
        if callable(load):
            try:
                load()
            except Exception:
                return None
            bbox = getattr(tester, 'bbox', None)
    if bbox is None or len(bbox) < 2:
        return None
    try:
        minimum = [float(bbox[0][index]) for index in range(3)]
        maximum = [float(bbox[1][index]) for index in range(3)]
    except (TypeError, ValueError, IndexError):
        return None
    return [minimum, maximum, None]


def _he_structural_armor(component):
    """Project only the hull thicknesses used by the copied HE fallback."""
    materials = _value(component, 'materials', {}) or {}
    iterator = getattr(materials, 'itervalues', None)
    if callable(iterator):
        values = iterator()
    elif isinstance(materials, dict):
        values = materials.values()
    else:
        try:
            values = iter(materials)
        except TypeError:
            values = ()
    result = []
    for material in values:
        try:
            armor = float(_value(material, 'armor', 0.0) or 0.0)
            vehicle_damage_factor = float(
                _value(material, 'vehicleDamageFactor', 1.0))
        except (TypeError, ValueError):
            continue
        if (armor > 0.0 and vehicle_damage_factor != 0.0 and
                armor == armor and abs(armor) != float('inf') and
                vehicle_damage_factor == vehicle_damage_factor and
                abs(vehicle_damage_factor) != float('inf')):
            result.append(armor)
    return sorted(result)


def _unavailable_repair_loadout():
    """Describe a repair input the donor could not prove from #1513 data."""
    return {'available': False}


def _unavailable_spotting_loadout():
    """Describe spotting inputs this donor could not prove from #1513."""
    return {'available': False}


def _bounded_number(value, minimum, maximum):
    """Return one finite non-boolean float, or raise on invented input."""
    if isinstance(value, bool):
        raise ValueError('boolean is not a numeric spotting input')
    result = float(value)
    if (result != result or abs(result) == float('inf') or
            result < float(minimum) or result > float(maximum)):
        raise ValueError('spotting input is outside its admitted range')
    return result


def _delay_microseconds(value):
    seconds = _bounded_number(value, 0.0, _MAX_SPOTTING_DELAY_SECONDS)
    return int(seconds * _MICROSECONDS_PER_SECOND + 0.5)


def _factor_surface_is_complete(factors):
    """Require every native factor consumed by ``spotting_profile``.

    ``spotting_profile`` intentionally retains engine-free fallbacks for the
    old local mode and tests. Descriptor donation has a stricter contract: a
    missing native factor is unavailable evidence, never permission to send
    those fallback values to the Rust authority.
    """
    try:
        for name in ('circularVisionRadius', 'camouflage',
                     'invisibility', '_aspects'):
            factors[name]
    except (KeyError, TypeError):
        return False
    return True


def _spotting_aspect(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError('spotting aspect must contain two values')
    return {
        'additive': _bounded_number(
            value[0], -_MAX_SPOTTING_ASPECT_ADDITIVE,
            _MAX_SPOTTING_ASPECT_ADDITIVE),
        'multiplier': _bounded_number(
            value[1], 0.0, _MAX_SPOTTING_FACTOR),
    }


def _spotting_loadout(descriptor, crew=None, equipments=(),
                      camouflage_id=None):
    """Project one already-composed #1513 view/concealment profile.

    The selected garage vehicle can donate its crew, consumables and paint;
    generated bots use the same descriptor with the default crew, no
    consumables and no player paint. The descriptor exchange still has one
    donor and one mounted descriptor per vehicle key, so this cannot describe
    a second human player's different garage loadout. That actor must remain
    unavailable until the exchange grows a per-participant contract.
    """
    if camouflage_id is _CAMOUFLAGE_UNAVAILABLE:
        return _unavailable_spotting_loadout()
    try:
        factor_equipments = tuple(
            equipment for equipment in (equipments or ())
            if not any('removedrpmlimiter' in name for name in
                       loadout_law.equipment_names((equipment,))))
        factors = loadout_law.attribute_factors(
            descriptor, crew=crew, equipments=factor_equipments)
        if not _factor_surface_is_complete(factors):
            return _unavailable_spotting_loadout()
        crew_skills = (loadout_law.crew_skill_names(crew)
                       if crew else None)
        level_increase = loadout_law.crew_level_increase(
            descriptor, equipments, crew_skills)
        profile = loadout_law.spotting_profile(
            descriptor, crew, level_increase=level_increase,
            factors=factors)

        turret = _value(descriptor, 'turret')
        misc = _value(descriptor, 'miscAttrs')
        gun = _value(descriptor, 'gun')
        if turret is None or misc is None or gun is None:
            raise ValueError('spotting descriptor surface is unavailable')
        base_range = _bounded_number(
            _value(turret, 'circularVisionRadius'), 1.0,
            _MAX_SPOTTING_RANGE_METRES)
        misc_factor = _bounded_number(
            _value(misc, 'circularVisionRadiusFactor'), 0.000001,
            _MAX_SPOTTING_FACTOR)
        crew_factor = _bounded_number(
            profile['vision_factor'], 0.000001, _MAX_SPOTTING_FACTOR)
        binocular_factor = _bounded_number(
            profile['binocular_factor'], 1.0, _MAX_SPOTTING_FACTOR)
        has_binoculars = profile['has_binoculars']
        has_camouflage_net = profile['has_camouflage_net']
        if (not isinstance(has_binoculars, bool) or
                not isinstance(has_camouflage_net, bool)):
            raise ValueError('spotting device flags are not booleans')

        calculator = getattr(descriptor, 'computeBaseInvisibility', None)
        if not callable(calculator):
            raise ValueError('computeBaseInvisibility is unavailable')
        base_values = calculator(profile['camouflage_factor'], camouflage_id)
        if (not isinstance(base_values, (list, tuple)) or
                len(base_values) < 2):
            raise ValueError('computeBaseInvisibility returned no pair')
        moving = _bounded_number(base_values[0], 0.0,
                                 _MAX_SPOTTING_FACTOR)
        stationary = _bounded_number(base_values[1], 0.0,
                                     _MAX_SPOTTING_FACTOR)
        shot_factor = _bounded_number(
            _value(gun, 'invisibilityFactorAtShot'), 0.0, 1.0)
        binocular_delay = (_delay_microseconds(profile['binocular_delay'])
                            if has_binoculars else 0)
        camouflage_net_delay = (
            _delay_microseconds(profile['camouflage_net_delay'])
            if has_camouflage_net else 0)
        moving_aspect = _spotting_aspect(profile['invisibility_moving'])
        stationary_aspect = _spotting_aspect(
            profile['invisibility_still'])
    except Exception:
        return _unavailable_spotting_loadout()

    return {
        'available': True,
        'observer': {
            'baseRangeMetres': base_range,
            'miscFactor': misc_factor,
            'crewFactor': crew_factor,
            'binocularFactor': binocular_factor,
            'hasBinoculars': has_binoculars,
            'binocularDelayUs': binocular_delay,
        },
        'target': {
            'moving': moving,
            'stationary': stationary,
            'movingAspect': moving_aspect,
            'stationaryAspect': stationary_aspect,
            'hasCamouflageNet': has_camouflage_net,
            'camouflageNetDelayUs': camouflage_net_delay,
            'invisibilityFactorAtShot': shot_factor,
        },
    }


def _repair_loadout(descriptor, crew=None, equipments=()):
    """Project the exact repair inputs consumed by the Rust authority.

    ``attribute_factors`` is the native #1513 calculation used by the current
    battle law.  Its absence is not permission to rebuild the value from item
    names: the Rust authority must see an explicit unavailable state and stop
    repair progression until a proven input is donated.
    """
    try:
        # Match BattleRuntime._local_factors: the trigger-only RPM limiter is
        # not treated as permanently active, while all other mounted items
        # participate in #1513's native factor calculation.
        factor_equipments = tuple(
            equipment for equipment in (equipments or ())
            if not any('removedrpmlimiter' in name for name in
                       loadout_law.equipment_names((equipment,))))
        factors = loadout_law.attribute_factors(
            descriptor, crew=crew, equipments=factor_equipments)
        if factors is None:
            return _unavailable_repair_loadout()
        crew_skills = (loadout_law.crew_skill_names(crew)
                       if crew else None)
        values = loadout_law.modifiers(
            descriptor, equipments, crew_skills, factors=factors)
        repair_factor = float(values['repair_factor'])
        has_big_kit = values['has_big_kit']
    except Exception:
        return _unavailable_repair_loadout()
    if (repair_factor != repair_factor or
            abs(repair_factor) == float('inf') or
            repair_factor <= 0.0 or
            repair_factor > _MAX_REPAIR_FACTOR or
            not isinstance(has_big_kit, bool)):
        return _unavailable_repair_loadout()
    return {
        'available': True,
        'repairFactor': repair_factor,
        'hasBigKit': has_big_kit,
    }


def _current_player_loadout(vehicle_name):
    """Read the selected garage vehicle's crew and mounted consumables.

    Only the selected vehicle has this context.  Other requested types are bot
    defaults, so advertising a synthetic player loadout for them would make a
    later actor-role selection silently authoritative over invented data.
    """
    try:
        from CurrentVehicle import g_currentVehicle
        if not g_currentVehicle.isPresent():
            return None
        item = g_currentVehicle.item
        descriptor = item.descriptor
        if str(descriptor.type.name) != str(vehicle_name):
            return None
        consumables = getattr(
            getattr(item, 'equipment', None), 'regularConsumables', None)
        equipments = (() if consumables is None else
                      tuple(consumables.getInstalledItems()))
        crew = tuple(getattr(item, 'crew', None) or ())
        camouflage_id = _CAMOUFLAGE_UNAVAILABLE
        camouflage_reader = getattr(item, 'getBonusCamo', None)
        if callable(camouflage_reader):
            try:
                camouflage = camouflage_reader()
            except Exception:
                camouflage = _CAMOUFLAGE_UNAVAILABLE
            if camouflage is None:
                camouflage_id = None
            elif camouflage is not _CAMOUFLAGE_UNAVAILABLE:
                camouflage_id = getattr(
                    camouflage, 'id', _CAMOUFLAGE_UNAVAILABLE)
    except Exception:
        return None
    return crew, equipments, camouflage_id


def current_player_authority_loadout():
    """Project the selected garage actor's exact repair/spotting inputs.

    Descriptor exchange has one donor for the whole round, so its embedded
    ``player`` settings cannot describe another participant's crew, mounted
    consumables or camouflage.  This smaller connection-scoped donation is
    sent by every player with hello and waiting-room vehicle changes.

    An unavailable native surface is explicit evidence too.  The server must
    leave that actor's repair and spotting inputs uninstalled instead of
    borrowing values from the descriptor donor.
    """
    unavailable = {
        'repair': _unavailable_repair_loadout(),
        'spotting': _unavailable_spotting_loadout(),
    }
    try:
        from CurrentVehicle import g_currentVehicle
        if not g_currentVehicle.isPresent():
            return unavailable
        item = g_currentVehicle.item
        if item is None:
            return unavailable
        descriptor = item.descriptor
        vehicle_name = str(descriptor.type.name)
        loadout = _current_player_loadout(vehicle_name)
        if loadout is None:
            return unavailable
        camouflage_id = (loadout[2] if len(loadout) > 2 else
                          _CAMOUFLAGE_UNAVAILABLE)
        return {
            'repair': _repair_loadout(
                descriptor, crew=loadout[0], equipments=loadout[1]),
            'spotting': _spotting_loadout(
                descriptor, crew=loadout[0], equipments=loadout[1],
                camouflage_id=camouflage_id),
        }
    except Exception:
        return unavailable


_COMPONENT_ID_FIELDS = ('name', 'id', 'compactDescr')
_GUN_FIELDS = _COMPONENT_ID_FIELDS + (
    'reloadTime', 'clip', 'turretYawLimits', 'pitchLimits',
    'rotationSpeed', 'shotDispersionAngle', 'shotDispersionFactors',
    'aimingTime', 'maxAmmo', 'maxHealth', 'maxRegenHealth', 'burst',
)
_SHOT_FIELDS = (
    'speed', 'gravity', 'maxDistance', 'piercingPower',
)
_SHELL_FIELDS = (
    'kind', 'caliber', 'damage', 'explosionRadius', 'piercingPower',
    'effectsIndex', 'isTracer',
)
_HE_FACTOR_DEFAULTS = (
    ("explosionDamageFactor", 0.5),
    ("explosionDamageAbsorptionFactor", 1.3),
    ("explosionEdgeDamageFactor", 0.15),
)
_PROJECTILE_SHELL_FIELDS = (
    "kind", "caliber", "damage", "explosionRadius",
    "explosionDamageFactor", "explosionDamageAbsorptionFactor",
    "explosionEdgeDamageFactor",
)
_TURRET_FIELDS = _COMPONENT_ID_FIELDS + (
    'rotationSpeed', 'circularVisionRadius', 'primaryArmor', 'maxHealth',
    'maxRegenHealth', 'turretRotatorHealth', 'surveyingDeviceHealth',
    'invisibilityFactor', 'yawLimits', 'gunPosition',
)
_CHASSIS_FIELDS = _COMPONENT_ID_FIELDS + (
    'hullPosition', 'rotationSpeed', 'shotDispersionFactors',
    'maxHealth', 'maxRegenHealth', 'terrainResistance',
)
_HULL_FIELDS = _COMPONENT_ID_FIELDS + (
    'turretPositions', 'primaryArmor', 'maxHealth', 'maxRegenHealth',
    'ammoBayHealth',
)
_COMPONENT_HEALTH_FIELDS = _COMPONENT_ID_FIELDS + (
    'maxHealth', 'maxRegenHealth')
_TYPE_FIELDS = ('invisibility', 'invisibilityFactorAtShot', 'crewRoles')
_MISC_ATTR_FIELDS = (
    'repairSpeedFactor', 'ammoBayHealthFactor', 'engineHealthFactor',
    'fuelTankHealthFactor', 'chassisHealthFactor')


def _complete_shell_projection(shell, names, default_radius=False):
    projection = _copy_fields(shell, names)
    shell_type = _value(shell, "type")
    if "kind" not in projection:
        kind = _value(shell_type, "name")
        if kind:
            projection["kind"] = str(kind)
    if "explosionRadius" not in projection:
        radius = _json_safe(_value(shell_type, "explosionRadius"))
        if radius is not None:
            projection["explosionRadius"] = radius
        elif default_radius:
            projection["explosionRadius"] = 0.0
    if projection.get("kind") == "HIGH_EXPLOSIVE":
        for name, default in _HE_FACTOR_DEFAULTS:
            value = _json_safe(_value(shell, name))
            if value is None:
                value = _json_safe(_value(shell_type, name))
            try:
                value = float(value)
            except (TypeError, ValueError, OverflowError):
                value = default
            if (value != value or abs(value) == float('inf') or
                    value <= 0.0 or
                    (name == "explosionEdgeDamageFactor" and value > 1.0)):
                value = default
            projection[name] = value
    return projection


def project_shot(shot, deadeye=False):
    """Freeze one mounted gun shot for the worker projectile ledger."""
    projection = _copy_fields(shot, _SHOT_FIELDS)
    projection["deadeye"] = bool(deadeye)
    projection["shell"] = _complete_shell_projection(
        _value(shot, "shell", {}), _PROJECTILE_SHELL_FIELDS,
        default_radius=True)
    return projection


def project_descriptor(descriptor, player_loadout=None):
    """Return one vehicle's JSON projection for the server authority."""
    vehicle_type = _value(descriptor, 'type', descriptor)
    projection = {
        'name': str(_value(vehicle_type, 'name', '') or ''),
        'level': int(_value(vehicle_type, 'level', 1) or 1),
        'tags': sorted(str(tag) for tag in
                       (_value(vehicle_type, 'tags', ()) or ())),
        'maxHealth': int(_value(descriptor, 'maxHealth', 1) or 1),
    }
    projection.update(_copy_fields(vehicle_type, _TYPE_FIELDS))
    # The shared internal-profile resolver reads the same nested ``type``
    # surface as a live VehicleDescr.  Keep the historical top-level fields
    # for the server's existing consumers, while donating only this small
    # identity/crew view rather than native collision materials.
    projection['type'] = {
        'name': projection['name'],
        'level': projection['level'],
        'tags': list(projection['tags']),
    }
    projection['type'].update(_copy_fields(vehicle_type, _TYPE_FIELDS))
    gun = _value(descriptor, 'gun', {})
    gun_projection = _copy_fields(gun, _GUN_FIELDS)
    shots = []
    for shot in (_value(gun, 'shots', ()) or ()):
        shot_projection = _copy_fields(shot, _SHOT_FIELDS)
        shell = _value(shot, 'shell', {})
        shell_projection = _complete_shell_projection(shell, _SHELL_FIELDS)
        shot_projection['shell'] = shell_projection
        shots.append(shot_projection)
    gun_projection['shots'] = shots
    bbox = _hit_tester_bbox(gun)
    if bbox is not None:
        gun_projection['hitTester'] = {'bbox': bbox}
    projection['gun'] = gun_projection
    turret = _value(descriptor, 'turret', {})
    turret_projection = _copy_fields(turret, _TURRET_FIELDS)
    bbox = _hit_tester_bbox(turret)
    if bbox is not None:
        turret_projection['hitTester'] = {'bbox': bbox}
    projection['turret'] = turret_projection
    physics = _value(descriptor, 'physics', {}) or {}
    projection['physics'] = _json_safe(dict(
        (str(key), physics[key]) for key in physics) if
        isinstance(physics, dict) else {}) or {}
    # The server cannot see VehicleType.xphysics. Donate the already-selected
    # #1513 detailed-engine override as a dimensionless ratio so its copied
    # integrator uses the same effective power as client authority mode.
    projection['physics']['nativePowerRatio'] = float(
        vehicle_physics.derive_params(descriptor)['nativePowerRatio'])
    chassis = _value(descriptor, 'chassis', {})
    chassis_projection = _copy_fields(chassis, _CHASSIS_FIELDS)
    bbox = _hit_tester_bbox(chassis)
    if bbox is not None:
        chassis_projection['hitTester'] = {'bbox': bbox}
    projection['chassis'] = chassis_projection
    hull = _value(descriptor, 'hull', {})
    hull_projection = _copy_fields(hull, _HULL_FIELDS)
    hull_projection['heStructuralArmor'] = _he_structural_armor(hull)
    bbox = _hit_tester_bbox(hull)
    if bbox is None:
        raise ValueError('descriptor hull bbox is unavailable')
    hull_projection['hitTester'] = {'bbox': bbox}
    projection['hull'] = hull_projection
    for component_name in ('engine', 'fuelTank', 'radio'):
        component = _value(descriptor, component_name)
        if component is None:
            continue
        values = _copy_fields(component, _COMPONENT_HEALTH_FIELDS)
        if component_name == 'engine':
            values['fireStartingChance'] = float(
                _value(component, 'fireStartingChance', 0.15))
        if values:
            projection[component_name] = values
    misc_attrs = _value(descriptor, 'miscAttrs', {}) or {}
    projection['miscAttrs'] = dict(
        (name, float(_value(misc_attrs, name, 1.0)))
        for name in _MISC_ATTR_FIELDS)
    player_repair = _unavailable_repair_loadout()
    if player_loadout is not None:
        player_repair = _repair_loadout(
            descriptor, crew=player_loadout[0],
            equipments=player_loadout[1])
    projection['repairSettings'] = {
        'player': player_repair,
        # Rust's bot repair law consumes a generated-default-crew profile with
        # no consumables. Passing those exact inputs through the client factor
        # chain preserves that law, including mounted optional devices.
        'botDefault': _repair_loadout(descriptor),
    }
    player_spotting = _unavailable_spotting_loadout()
    if player_loadout is not None:
        player_camouflage_id = (
            player_loadout[2] if len(player_loadout) > 2 else
            _CAMOUFLAGE_UNAVAILABLE)
        player_spotting = _spotting_loadout(
            descriptor, crew=player_loadout[0],
            equipments=player_loadout[1],
            camouflage_id=player_camouflage_id)
    projection['spottingSettings'] = {
        'player': player_spotting,
        'botDefault': _spotting_loadout(descriptor),
    }
    # Rust's bot ramming law consumes this profile from the bot's descriptor
    # with no player equipment or Controlled Impact skill. Freeze that exact
    # actor-role input instead of inventing a generic bot profile or borrowing
    # the descriptor donor's garage loadout.
    projection['rammingSettings'] = {
        'botDefault': tank_collision.descriptor_ram_profile(descriptor),
    }
    if not projection['name']:
        raise ValueError('descriptor has no type name')
    if not shots:
        raise ValueError('descriptor has no gun shots')
    return projection


def vehicle_catalog(runtime):
    """Return eligible vehicle tiers for the waiting-room roster."""
    rows = []
    nations = runtime.nations
    vehicle_list = runtime.vehicles.g_list
    for nation in nations.AVAILABLE_NAMES:
        nation_id = nations.INDICES[nation]
        values = vehicle_list.getList(nation_id)
        iterator = getattr(values, "itervalues", None)
        entries = iterator() if callable(iterator) else values.values()
        for entry in entries:
            name = str(_value(entry, "name", "") or "")
            if (not name or vehicle_blacklist.is_unusable(name) or
                    not vehicle_configuration.is_standard_battle_vehicle(
                        entry)):
                continue
            try:
                level = int(_value(entry, "level", 1) or 1)
            except (TypeError, ValueError):
                continue
            rows.append({"name": name, "level": level,
                         "tags": sorted(str(tag) for tag in
                                        (_value(entry, "tags", ()) or ()))})
    rows.sort(key=lambda row: row["name"])
    return rows


def project_vehicles(runtime, names, failures=None, fittings=None):
    """Build requested projections and optionally report every failed name.

    ``fittings`` maps a type name to a mounted compact descriptor, so the
    server measures the tank the owner actually fitted instead of the stock
    one.
    """
    projections = {}
    for name in names:
        name = str(name)
        try:
            fitting = (fittings or {}).get(name)
            if fitting is None:
                descriptor = runtime.vehicles.VehicleDescr(typeName=name)
            else:
                descriptor = runtime.vehicles.VehicleDescr(
                    compactDescr=fitting)
            player_loadout = (_current_player_loadout(name)
                              if fitting is not None else None)
            projections[name] = project_descriptor(
                descriptor, player_loadout=player_loadout)
        except Exception:
            if failures is not None:
                failures.append(name)
            continue
    return projections
