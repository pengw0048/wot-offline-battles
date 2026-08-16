"""Player module damage, fire and repair, through the copied law.

critical_damage owns the rolls, the state and the repair clock; this is
the 2.3.1.2 presentation side: DAMAGE_INFO codes to the damage panel and
stat factors to the motion driver.
"""
from __future__ import absolute_import

from gui.mods.offline_battle_2312 import critical_damage

TICK_SECONDS = 0.5
CAUSE_SUFFIXES = {
    'fire': '_AT_FIRE',
    'ramming': '_AT_RAMMING',
    'world_collision': '_AT_WORLD_COLLISION',
    'drowning': '_AT_DROWNING',
}


def extra_index(descriptor, name):
    """The descriptor extra index the stock damage panel keys on."""
    extra_name = str(name)
    if not extra_name.endswith('Health'):
        extra_name += 'Health'
    for index, extra in enumerate(getattr(descriptor, 'extras', None) or ()):
        if str(getattr(extra, 'name', '')) == extra_name:
            return index
    return 0


def event_code(event):
    """The DAMAGE_INFO code one payload event publishes, or None."""
    kind = event.get('kind')
    state = event.get('state')
    cause = event.get('cause', 'shot')
    if kind == 'device':
        if cause == 'repair':
            return ('DEVICE_REPAIRED' if state == 'normal' else
                    'DEVICE_REPAIRED_TO_CRITICAL')
        base = ('DEVICE_DESTROYED' if state == 'destroyed' else
                'DEVICE_CRITICAL')
        return base + CAUSE_SUFFIXES.get(cause, '_AT_SHOT')
    if kind == 'crew':
        if state == 'normal':
            return 'TANKMAN_RESTORED'
        if cause in ('world_collision', 'drowning'):
            return 'TANKMAN_HIT' + CAUSE_SUFFIXES[cause]
        if cause == 'fire':
            return 'TANKMAN_HIT'
        return 'TANKMAN_HIT_AT_SHOT'
    if kind == 'fire':
        if state:
            return ('DEVICE_STARTED_FIRE_AT_RAMMING' if cause == 'ramming'
                    else 'DEVICE_STARTED_FIRE_AT_SHOT')
        return 'FIRE_STOPPED'
    return None


class CriticalControl(object):

    def __init__(self, avatar, vehicle_id, scheduler, log,
                 on_factors=None, on_fire_damage=None):
        import constants
        self._avatar = avatar
        self._vehicle_id = vehicle_id
        self._schedule = scheduler
        self._log = log
        self._on_factors = on_factors
        self._on_fire_damage = on_fire_damage
        self._stopped = False
        self._indices = {code: index for index, code
                         in enumerate(constants.DAMAGE_INFO_CODES)}

    def start(self):
        self._schedule(TICK_SECONDS, self._tick)

    def stop(self):
        self._stopped = True

    def _vehicle(self):
        import BigWorld
        return BigWorld.entities.get(self._vehicle_id)

    def apply_shot(self, vehicle, landing, hull_damage, law_shell,
                   attacker_id, penetrated):
        """Roll the copied crit law for one enemy shell on the player."""
        _unused, payload = critical_damage.apply_direct(
            vehicle, landing.collisions, landing.segment_start,
            landing.segment_end, int(hull_damage), law_shell,
            int(attacker_id), penetrated=penetrated,
            by_explosion=(law_shell['kind'] == 'HIGH_EXPLOSIVE' and
                          not penetrated))
        self._present(vehicle, payload, attacker_id)
        self._push_factors(vehicle)
        return payload

    def _tick(self):
        import BigWorld
        if self._stopped:
            return
        self._schedule(TICK_SECONDS, self._tick)
        vehicle = self._vehicle()
        if vehicle is None or int(getattr(vehicle, 'health', 0)) <= 0:
            return
        repair = critical_damage.tick_repair(vehicle, TICK_SECONDS)
        burn, fire = critical_damage.tick_fire(vehicle, TICK_SECONDS,
                                               BigWorld.time())
        if repair is not None:
            self._present(vehicle, repair, 0)
        if fire is not None:
            self._present(vehicle, fire, 0)
        if burn and self._on_fire_damage is not None:
            self._on_fire_damage(int(burn))
        if repair is not None or fire is not None:
            self._push_factors(vehicle)

    def present(self, vehicle, payload, attacker_id=0):
        """Publish one payload's events and refresh the stat factors."""
        self._present(vehicle, payload, attacker_id)
        self._push_factors(vehicle)

    def _push_factors(self, vehicle):
        if self._on_factors is None:
            return
        self._on_factors({
            'mobility': critical_damage.stat_factor(vehicle, 'mobility'),
            'traverse': critical_damage.stat_factor(vehicle, 'traverse'),
        })

    def _present(self, vehicle, payload, attacker_id):
        if payload is None:
            return
        descriptor = vehicle.typeDescriptor
        for event in payload.get('events') or ():
            code = event_code(event)
            if code is None:
                continue
            index = self._indices.get(code)
            if index is None:
                self._log('damage_info_code_missing code=%s' % (code,))
                continue
            extra = 0
            if event.get('kind') in ('device', 'crew'):
                extra = extra_index(descriptor, event.get('name'))
                if extra <= 0:
                    self._log('damage_info_extra_missing name=%s'
                              % (event.get('name'),))
                    continue
            self._avatar.showVehicleDamageInfo(
                self._vehicle_id, index, extra, int(attacker_id or 0), 0)
            self._log('damage_info code=%s extra=%s name=%s'
                      % (code, extra, event.get('name')))
