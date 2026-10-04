"""Read-only presentation of recorded local state; no live gun/vehicle law.

All discrete values belong to the latest committed record. Only continuous
pose components interpolate between already decoded neighbouring records.
"""
from __future__ import print_function
import math

from gui.mods.offline_lan_0922 import descriptor_donation

ANGLE_FIELDS = ('_local_yaw', '_local_pitch', '_local_roll', 'turret_yaw', 'gun_pitch')
CONTINUOUS_FIELDS = ('_local_speed', '_local_turn_speed', '_local_vertical_speed', '_local_siege_aim_pitch')


def blend_pose(left, right, moment):
    result = dict(left)
    if right is None:
        return result
    start, end = float(left.get('_replay_t', 0)), float(right.get('_replay_t', 0))
    span = end - start
    if span <= 0 or span > 0.25 or moment <= start:
        return result
    # Do not animate across a death, teleport or missing section. The old
    # schema has no teleport marker: reject geometrically implausible spans.
    if (left.get('health', 1) > 0) != (right.get('health', 1) > 0):
        return result
    if left.get('discontinuity') or right.get('discontinuity'):
        return result
    delta = [right['position'][i] - left['position'][i] for i in range(3)]
    speed = max(abs(float(left.get('_local_speed', 0))), abs(float(right.get('_local_speed', 0))),
                abs(float(left.get('_local_vertical_speed', 0))), abs(float(right.get('_local_vertical_speed', 0))))
    if sum(d*d for d in delta) > (1.0 + 3.0 * speed * span) ** 2:
        return result
    a = min(1.0, max(0.0, (float(moment)-start)/span))
    result['position'] = [left['position'][i] + a*delta[i] for i in range(3)]
    for name in ANGLE_FIELDS:
        if name in left and name in right:
            delta_angle = (right[name] - left[name] + math.pi) % (2*math.pi) - math.pi
            result[name] = left[name] + a*delta_angle
    for name in CONTINUOUS_FIELDS:
        if name in left and name in right:
            result[name] = left[name] + a*(right[name]-left[name])
    # In particular, ammo, reload, health and visibility are NEVER interpolated.
    return result


class ReloadEdges(object):
    """One source of gun HUD edges, keyed by recorded cycles, not render FPS.

    A countdown sample can contain a positive internal reload which was NOT
    displayed as reloading in the live client. No ready sound is synthesized
    during that countdown. Between records only HUD interpolation advances.
    """
    def __init__(self):
        self.previous = None
        self.live = False
        self.deadline = None
        self.positive = False
        self.notifications = 0
        self.completions = 0
        self.starts = 0
        self.last_reason = None

    def observe(self, gun, stamp, now, live):
        if not gun or 'reload_time' not in gun:
            return None
        g = dict(gun)
        g['ammo'] = tuple(g.get('ammo', ()))
        previous = self.previous
        was_live = self.live
        self.live = bool(live)
        self.previous = g
        raw = max(0.0, float(g['reload_time']))
        duration = max(0.0, float(g.get('reload_duration', 0.0)))
        reason = None
        if not live:
            # Initial publication is quiet, performed directly via the ammo
            # controller rather than the Avatar completion-event method.
            self.deadline = None
            self.positive = False
            return None
        positive = raw > 0.0
        changed_shell = previous is not None and g.get('shot_index') != previous.get('shot_index')
        ammo_changed = previous is not None and g['ammo'] != previous['ammo']
        clip_changed = previous is not None and g.get('clip') != previous.get('clip')
        duration_changed = previous is not None and abs(duration-float(previous.get('reload_duration',0))) > 1e-5
        restarted = previous is not None and raw > float(previous.get('reload_time',0)) + 0.15
        if positive:
            if not was_live or previous is None or not self.positive:
                reason = 'start'
            elif changed_shell or ammo_changed or clip_changed or duration_changed or restarted:
                reason = 'cycle_change'
        elif self.positive:
            reason = 'complete'
        # Purely monotonic countdown samples do not reset native reloadEffect.
        self.positive = positive
        if reason is None:
            return None
        left = max(0.0, raw - max(0.0, float(now)-float(stamp))) if positive else 0.0
        # A late positive record is not a completion event. Its recorded zero
        # successor is the only completion owner. Preserve a positive epsilon.
        if positive:
            left = max(1e-6, left)
            self.deadline = float(stamp) + raw
            self.starts += 1
        else:
            self.deadline = None
            self.completions += 1
        self.notifications += 1
        self.last_reason = reason
        return left, duration, reason


def recorded_player(header):
    player_id = header.get('player_id', (header.get('welcome') or {}).get('player_id'))
    for name in ('snapshot', 'start'):
        for row in (header.get(name) or {}).get('players', ()):
            if row.get('id') == player_id:
                return row
    raise ValueError('replay has no recorded local player')


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def verify_descriptor(descriptor, accepted):
    """Compare mounted shots using the SAME projection as live admission.

    #1513 piercingPower (and some shell fields) are native vector objects,
    while a recorded accepted shot contains plain JSON arrays. Comparing the
    native object itself to that list gives a false mismatch even after the
    active resource fingerprint has passed. Project values first; do not
    remove the check, loosen tolerances or change either side's ballistics.
    """
    if not isinstance(accepted, dict):
        raise ValueError('recorded effective vehicle parameters are missing')
    gun = descriptor.gun
    if int(_field(gun, 'clip', (1,0))[0]) != int(accepted['gun']['clip_size']):
        raise ValueError('recorded clip capacity differs from selected vehicle profile')
    shots = dict((int(_field(_field(s, 'shell', {}), 'compactDescr', -1)), s)
                 for s in _field(gun, 'shots', ()))
    for row in accepted['gun']['shots']:
        shell_id = int(row['compact_descr'])
        native_shot = shots.get(shell_id)
        if native_shot is None:
            raise ValueError('recorded ammunition is absent from selected vehicle profile')
        # This is the shared production path used by lan_session when the
        # original battle donates its mounted-shot law to the server.
        actual = descriptor_donation.project_shot(native_shot)
        original = row['source_shot']
        checks = []
        for key in ('speed', 'gravity', 'maxDistance', 'piercingPower'):
            checks.append((key, actual.get(key), original.get(key),
                           type(_field(native_shot, key)).__name__))
        shell = actual.get('shell', {})
        native_shell = _field(native_shot, 'shell', {})
        for key in ('damage', 'caliber', 'kind', 'explosionRadius'):
            default = 0 if key == 'explosionRadius' else None
            checks.append(('shell.' + key, shell.get(key, default),
                           original['shell'].get(key, default),
                           type(_field(native_shell, key)).__name__))
        for key, current, recorded, native_type in checks:
            if not _equal_values(current, recorded):
                raise ValueError(
                    'recorded %s differs from selected vehicle profile; '
                    'shell=%d current=%r recorded=%r native_type=%s '
                    'projection=descriptor_donation; select the profile '
                    'used to record this battle' %
                    (key, shell_id, current, recorded, native_type))
    return True


def _equal_values(a, b):
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a)==len(b) and all(_equal_values(x,y) for x,y in zip(a,b))
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a)==set(b) and all(_equal_values(a[k],b[k]) for k in a)
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):
        a, b = float(a), float(b)
        if math.isnan(a) or math.isnan(b) or math.isinf(a) or math.isinf(b):
            return False
        return abs(a-b) <= 1e-5 * max(1.0, abs(a), abs(b))
    return a==b


def descriptor_contract(descriptor):
    result = {}
    for section, names in (
            ('hull', ('weight','armor','maxHealth')),
            ('turret', ('weight','armor','maxHealth','circularVisionRadius','rotationSpeed')),
            ('chassis', ('maxLoad','weight','rotationSpeed','terrainResistance','shotDispersionFactors')),
            ('engine', ('power','weight')),
            ('gun', ('reloadTime','clip','burst','maxAmmo','aimingTime','shotDispersionAngle','rotationSpeed','shotDispersionFactors'))):
        source = getattr(descriptor, section, None)
        values = {}
        for name in names:
            value = _field(source,name)
            if value is not None and _primitive(value):
                values[name] = _freeze_plain(value)
        result[section] = values
    for name in ('maxHealth','maxAmmo','speedLimits'):
        value = _field(descriptor,name)
        if value is not None and _primitive(value):
            result[name] = _freeze_plain(value)
    return result


def _primitive(value, depth=0):
    if depth > 5:
        return False
    if isinstance(value,(int,float,bool,type(None),str,type(u''))):
        return not isinstance(value,float) or not (math.isnan(value) or math.isinf(value))
    if isinstance(value,(list,tuple)):
        return len(value)<=128 and all(_primitive(x,depth+1) for x in value)
    if isinstance(value,dict):
        return len(value)<=128 and all(isinstance(k,(str,type(u''))) and _primitive(v,depth+1) for k,v in value.items())
    return False


def _freeze_plain(value):
    if isinstance(value,dict):
        return dict((k,_freeze_plain(v)) for k,v in value.items())
    if isinstance(value,(list,tuple)):
        return [_freeze_plain(x) for x in value]
    return value
