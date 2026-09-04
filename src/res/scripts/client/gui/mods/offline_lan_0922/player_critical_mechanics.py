from __future__ import print_function

"""Pure player critical/equipment transitions over client-donated pools."""

import math

from gui.mods.offline_lan_0922 import critical_damage
from gui.mods.offline_lan_0922 import device_damage
from gui.mods.offline_lan_0922 import equipment_mechanics


DEVICE_NAMES = (
    'engineHealth', 'ammoBayHealth', 'fuelTankHealth', 'radioHealth',
    'leftTrackHealth', 'rightTrackHealth', 'gunHealth',
    'turretRotatorHealth', 'surveyingDeviceHealth')
TRACK_DEVICE_NAMES = frozenset(('leftTrackHealth', 'rightTrackHealth'))
CREW_NAMES = frozenset((
    'commander', 'driver', 'gunner', 'gunner1', 'gunner2', 'loader',
    'loader1', 'loader2', 'radioman', 'radioman1', 'radioman2'))


def project_profile(descriptor):
    """Project final mounted device HP pools from the exact #1513 client."""
    devices = []
    for name in DEVICE_NAMES:
        maximum = device_damage.device_max_hp(descriptor, name)
        if maximum is None:
            continue
        regen = device_damage.device_regen_hp(descriptor, name)
        devices.append({
            'name': name,
            'max_hp': float(maximum),
            'regen_hp': max(0.0, min(float(maximum), float(regen or 0.0))),
        })
    if not devices:
        raise ValueError('the selected vehicle has no critical device pools')
    devices.sort(key=lambda entry: entry['name'])
    extras = getattr(descriptor, 'extras', None)
    if hasattr(extras, 'items'):
        iterator = extras.items()
    else:
        try:
            iterator = enumerate(extras or ())
        except TypeError:
            iterator = ()
    activation_targets = []
    seen_indexes = set()
    for raw_index, extra in iterator:
        try:
            index = int(raw_index)
            exact = not isinstance(raw_index, bool) and \
                float(raw_index) == index
        except (TypeError, ValueError, OverflowError):
            continue
        name = str(getattr(extra, 'name', '') or '')
        target = name[:-6] if name.endswith('Health') and \
            name[:-6] in CREW_NAMES else name
        if (not exact or index <= 0 or index in seen_indexes or
                target not in DEVICE_NAMES and target not in CREW_NAMES):
            continue
        seen_indexes.add(index)
        activation_targets.append({'index': index, 'name': target})
    activation_targets.sort(key=lambda entry: entry['index'])
    roles = getattr(getattr(descriptor, 'type', None), 'crewRoles', None)
    counters = {'gunner': 1, 'loader': 1, 'radioman': 1}
    crew_roster = []
    for crewman_roles in roles or ():
        if (not isinstance(crewman_roles, (list, tuple)) or
                not crewman_roles):
            continue
        role = str(crewman_roles[0])
        if role in counters:
            name = role + str(counters[role])
            counters[role] += 1
        else:
            name = role
        if name in CREW_NAMES and name not in crew_roster:
            crew_roster.append(name)
    return {
        'devices': devices,
        'activation_targets': activation_targets,
        'crew_roster': crew_roster,
    }


class _Projection(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


def _component(maximum, regen):
    return _Projection(maxHealth=float(maximum), maxRegenHealth=float(regen))


def descriptor_from_profile(profile):
    """Rebuild only the descriptor surface used by copied critical laws."""
    if not isinstance(profile, dict):
        return None
    rows = profile.get('devices')
    if not isinstance(rows, (list, tuple)):
        return None
    values = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        try:
            name = str(row['name'])
            maximum = float(row['max_hp'])
            regen = float(row['regen_hp'])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if (name not in DEVICE_NAMES or name in values or
                math.isnan(maximum) or math.isinf(maximum) or
                math.isnan(regen) or math.isinf(regen) or
                maximum <= 0.0 or regen < 0.0 or regen > maximum):
            return None
        values[name] = _component(maximum, regen)
    if not values:
        return None
    descriptor = _Projection(miscAttrs=_Projection())
    descriptor.engine = values.get('engineHealth')
    descriptor.fuelTank = values.get('fuelTankHealth')
    descriptor.radio = values.get('radioHealth')
    descriptor.chassis = values.get('leftTrackHealth')
    descriptor.gun = values.get('gunHealth')
    descriptor.hull = _Projection(ammoBayHealth=values.get('ammoBayHealth'))
    descriptor.turret = _Projection(
        turretRotatorHealth=values.get('turretRotatorHealth'),
        surveyingDeviceHealth=values.get('surveyingDeviceHealth'))
    return descriptor


def _equipment_snapshots(player, now):
    return [equipment.snapshot(now)
            for equipment in (getattr(player, 'equipment_states', ()) or ())]


class _CriticalTarget(object):
    """Detached adapter for server-owned player critical state."""

    def __init__(self, player, descriptor, now):
        self.id = int(player.player_id)
        self.health = int(player.health)
        self.maxHealth = int(player.max_health)
        self.typeDescriptor = descriptor
        self.position = (float(player.x), float(player.y), float(player.z))
        self.matrix = None
        self.devices_hp = {}
        self._destroyed_devices = set()
        self._critical_devices = set()
        self._crew_ko = set()
        self.is_on_fire = False
        self._ammo_rack_death = False
        self._fire_started = None
        self._fire_timer = float(getattr(player, 'combat_fire_timer', 0.0))
        self.is_tracked = False
        self.is_engine_dead = False
        self._load(player.critical)

    def _load(self, critical):
        critical = critical if isinstance(critical, dict) else {}
        for record in critical.get('devices') or ():
            if not isinstance(record, dict):
                continue
            name = str(record.get('name') or '')
            if name not in DEVICE_NAMES:
                continue
            self.devices_hp[name] = float(record.get('hp', 0.0))
            state = str(record.get('state') or '')
            if state == 'destroyed':
                self._destroyed_devices.add(name)
            elif state == 'critical':
                self._critical_devices.add(name)
        self._destroyed_devices.update(
            str(name) for name in (critical.get('destroyed') or ())
            if str(name) in DEVICE_NAMES)
        self._crew_ko.update(
            str(name) for name in (critical.get('crew_ko') or ()))
        self.is_on_fire = bool(critical.get('fire', False))
        self._ammo_rack_death = bool(
            critical.get('ammo_rack_death', False))
        self.is_tracked = bool(
            'leftTrackHealth' in self._destroyed_devices or
            'rightTrackHealth' in self._destroyed_devices)
        self.is_engine_dead = 'engineHealth' in self._destroyed_devices


def _descriptor(player):
    params = getattr(player, 'effective_params', None)
    profile = params.get('critical') if isinstance(params, dict) else None
    return descriptor_from_profile(profile)


def apply_equipment(player, effect, now):
    descriptor = _descriptor(player)
    if descriptor is None or not isinstance(effect, dict):
        return None
    target = _CriticalTarget(player, descriptor, now)
    action = str(effect.get('action') or '')
    if action == 'extinguish_fire':
        return critical_damage.use_extinguisher(target)
    if action == 'repair_devices':
        return critical_damage.repair_device(
            target, effect.get('selected'), bool(effect.get('repairAll')))
    if action == 'restore_crew':
        return critical_damage.restore_crew(
            target, effect.get('selected'), bool(effect.get('repairAll')))
    return None


def advance_critical(player, dt, now):
    descriptor = _descriptor(player)
    if (descriptor is None or not isinstance(player.critical, dict) or
            not player.alive or player.health <= 0 or dt <= 0.0):
        return None
    target = _CriticalTarget(player, descriptor, now)
    params = getattr(player, 'effective_params', None) or {}
    loadout = params.get('loadout') or {}
    repair_factor = max(0.0, float(loadout.get('repair_factor', 1.0)))
    has_big_kit = bool(loadout.get('has_big_kit', False))
    passives = equipment_mechanics.passive_effects(
        getattr(player, 'equipment_states', ()) or ())
    rpm_loss = max(
        0.0, float(passives.get('engineHpLossPerSecond', 0.0))) * float(dt)
    rpm_payload = (critical_damage.damage_device_over_time(
        target, 'engineHealth', rpm_loss, 'equipment')
        if rpm_loss > 0.0 else None)
    repair_before = critical_damage._state(target)
    devices = getattr(target, 'devices_hp', None) or {}
    destroyed = set(getattr(target, '_destroyed_devices', None) or ())
    critical = set(getattr(target, '_critical_devices', None) or ())
    for name in list(devices):
        if name in TRACK_DEVICE_NAMES:
            continue
        cap = device_damage.device_regen_hp(descriptor, name)
        if cap is None or devices[name] >= cap:
            continue
        if (name in device_damage.NO_REPAIR_PROGRESS_DEVICES and
                bool(getattr(target, 'is_on_fire', False))):
            continue
        devices[name] = device_damage.repair_step_hp(
            devices[name], name, descriptor, float(dt),
            has_big_repairkit=has_big_kit,
            repair_factor=repair_factor)
        if name in destroyed and devices[name] >= cap:
            destroyed.discard(name)
            critical.add(name)
    target.devices_hp = devices
    target._destroyed_devices = destroyed
    target._critical_devices = critical
    critical_damage._refresh_mobility_flags(target)
    repair_payload = critical_damage._payload(
        repair_before, critical_damage._state(target), descriptor, 'repair')
    payload = repair_payload or rpm_payload
    if payload is not None and repair_payload is not None and rpm_payload is not None:
        payload = dict(payload)
        payload['events'] = (list(rpm_payload.get('events') or ()) +
                             list(repair_payload.get('events') or ()))
    return payload


def advance_fire(player, dt, now):
    descriptor = _descriptor(player)
    if (descriptor is None or not isinstance(player.critical, dict) or
            not player.critical.get('fire', False) or not player.alive or
            player.health <= 0):
        return None
    elapsed = max(0.0, float(getattr(player, 'combat_fire_elapsed', 0.0)))
    target = _CriticalTarget(player, descriptor, now)
    step = max(0.0, float(dt))
    target._fire_started = float(now) - (elapsed + step)
    damage, payload = critical_damage.tick_fire(target, step, now=float(now))
    burning = bool(target.is_on_fire and damage < int(player.health))
    return {
        'damage': max(0, int(damage)),
        'critical': payload,
        'fire_elapsed': elapsed + step if burning else 0.0,
        'fire_timer': max(0.0, float(target._fire_timer)) if burning else 0.0,
        'burning': burning,
    }
