"""Armour and HE laws, taken from the 0.9.22 port.

The law is unchanged. Only the input adapter is version-local: 2.3.1.2
hands the same GunShot objects and SegmentCollisionResultExt tuples the
0.9.22 adapter already accepts.
"""
from __future__ import absolute_import

import random


# The _offh_* functions below retain the 0.8.2 laws; the hull resolver also
# accepts the already-crossed 2.3.1.2 destructible loss before vehicle layers.


def _offh_resolve_hull_hit(shot, dist_m, all_hits, initial_pierce_loss=0.0,
        penetration_factor=None):
    '''Find the first STRUCTURAL plate behind any spaced armour.

    Returns (result, eff_armor, pierce, spaced_mm, angle_cos) where result is the
    _offh_penetration verdict for that plate, or None when the round never reaches
    structure - i.e. the track absorbed it.

    Tracks and external devices carry vehicleDamageFactor 0.0: they are not the
    hull, so they must not take hull damage. What they DO is cost penetration on
    the way through, which is why a shot that clips the track at a shallow angle
    (long path, thick effective plate) is swallowed while a square-on hit carries
    into the hull behind it.

    HEAT is a special case, as in the game: the shaped charge detonates on the
    first spaced plate it touches and the jet does not survive the standoff, so a
    track absorbs it outright regardless of the angle.'''
    import math
    if not all_hits:
        return None
    shell = (shot.get('shell') or {}) if hasattr(shot, 'get') else {}
    kind = shell.get('kind', 'ARMOR_PIERCING')
    spaced = float(initial_pierce_loss or 0.0)
    try:
        _ordered = sorted(all_hits, key=lambda h: h[0])
    except Exception:
        _ordered = all_hits
    for _h in _ordered:
        try:
            _d, _ac, _mat = _h[0], _h[1], _h[2]
        except Exception:
            continue
        if _mat is None:
            continue
        _vdf = getattr(_mat, 'vehicleDamageFactor', 1.0)
        _arm = float(getattr(_mat, 'armor', 0.0) or 0.0)
        if _vdf == 0.0:
            # spaced: never structure. HEAT dies here; everything else pays armour.
            if kind == 'HOLLOW_CHARGE':
                return None
            _a = abs(float(_ac))
            if _a > 1.0: _a = 1.0
            if _a < 0.087: _a = 0.087
            spaced += _arm / _a
            continue
        if _arm <= 0.0:
            continue
        _res, _eff, _p = _offh_penetration(
            shot, dist_m, _arm, _ac, spaced, penetration_factor)
        return (_res, _eff, _p, spaced, _ac)
    return None


#   damage = nominal * SPLASH_FRACTION * (1 - dist/explosionRadius)
#            - ARMOR_FACTOR * nominal_armour
#
# Both constants are overridable from config.json "physics_tuning"-style under
# "he_tuning", so the feel can be corrected without a recompile.
_OFFH_HE_SPLASH_FRACTION = 0.5
_OFFH_HE_ARMOR_FACTOR = 1.1


def _offh_is_he(shot):
    '''True for a high-explosive round. Reads shell['kind'] - never the name:
    every HEAT shell contains the letters 'HE' too, which is exactly the bug the
    shared penetration model was written to kill.'''
    shell = (shot.get('shell') or {}) if hasattr(shot, 'get') else {}
    return shell.get('kind') == 'HIGH_EXPLOSIVE'


def _offh_he_radius(shot):
    '''explosionRadius of this shot's shell, in metres. items/vehicles.py falls
    back to caliber^2 / 5555 when the shell XML omits it - mirror that rather
    than inventing a number.'''
    shell = (shot.get('shell') or {}) if hasattr(shot, 'get') else {}
    try:
        r = float(shell.get('explosionRadius', 0.0) or 0.0)
    except Exception:
        r = 0.0
    if r > 0.0:
        return r
    try:
        cal = float(shell.get('caliber', 0) or 0)
    except Exception:
        cal = 0.0
    return (cal * cal / 5555.0) if cal > 0.0 else 0.0


def _offh_he_hull_armor(td):
    '''Thinnest STRUCTURAL plate the hull carries, from the descriptor.

    Used when the blast ray finds no plate at all. Returning 0 there let the
    blast through untouched; the thinnest plate is the attacker-friendly but
    still bounded assumption - blast looks for the weak facing.'''
    best = None
    try:
        _hull = getattr(td, 'hull', None)
        if isinstance(_hull, dict):
            mats = _hull.get('materials') or {}
        else:
            mats = getattr(_hull, 'materials', None) or {}
        for m in mats.values():
            if getattr(m, 'vehicleDamageFactor', 1.0) == 0.0:
                continue
            a = float(getattr(m, 'armor', 0.0) or 0.0)
            if a <= 0.0:
                continue
            if best is None or a < best:
                best = a
    except Exception:
        return 0.0
    return best or 0.0


def _offh_he_nominal_armor(all_hits, td=None):
    '''Nominal thickness of the first STRUCTURAL plate on the ray.

    The HE reduction uses the plate's NOMINAL thickness, not the angled effective
    value: a sloped plate does not shrug off blast the way it deflects a solid
    shot. Spaced plates (vehicleDamageFactor 0 - tracks, external gear) are
    skipped; HE bursts on them and what has to hold is the hull behind.'''
    best = None
    for _h in (all_hits or []):
        try:
            _d, _mat = _h[0], _h[2]
        except Exception:
            continue
        if _mat is None or getattr(_mat, 'vehicleDamageFactor', 1.0) == 0.0:
            continue
        _a = float(getattr(_mat, 'armor', 0.0) or 0.0)
        if _a <= 0.0:
            continue
        if best is None or _d < best[0]:
            best = (_d, _a)
    if best is not None:
        return best[1]
    # No plate on the ray. Zero would hand the blast a free pass, so fall back to
    # the hull's thinnest structural plate when the descriptor is available.
    return _offh_he_hull_armor(td) if td is not None else 0.0


def _offh_he_damage(base_damage, armor_nominal, dist_frac=0.0):
    '''Damage an HE burst does to a hull it did NOT get through.

    dist_frac is 0.0 for the vehicle actually struck and rises to 1.0 at the edge
    of explosionRadius for everything else caught in the blast. Returns 0 when the
    plate eats the whole thing - the normal outcome against heavy armour, and the
    reason a derp gun rewards shooting thin plate.'''
    d = (float(base_damage) * _OFFH_HE_SPLASH_FRACTION * (1.0 - float(dist_frac))
         - _OFFH_HE_ARMOR_FACTOR * float(armor_nominal or 0.0))
    return int(d) if d > 0.0 else 0


def _offh_he_apply_tuning(overrides):
    '''Overlay config.json "he_tuning" onto the two blast constants.'''
    g = globals()
    applied = []
    if isinstance(overrides, dict):
        for k, gname in (('splash_fraction', '_OFFH_HE_SPLASH_FRACTION'),
                         ('armor_factor', '_OFFH_HE_ARMOR_FACTOR')):
            if k in overrides:
                try:
                    g[gname] = float(overrides[k])
                    applied.append('%s=%s' % (k, overrides[k]))
                except (TypeError, ValueError):
                    pass
    return applied


def _offh_penetration(shot, dist_m, armor, hit_angle_cos, pierce_loss=0.0,
        penetration_factor=None):
    '''Armour test shared by the player and by bot-vs-bot fire.

    Returns (result, eff_armor, pierce): 0 ricochet, 1 no penetration, 2 penetration.

    Fixes two faults of the old inline version:
      * it classified shells with `'HE' not in shell['name']`, a substring test on the
        NAME. Every HEAT round contains 'HE', so both the ricochet and the
        no-penetration branch were skipped for it and it always went through.
        items/vehicles.py stores a proper shell['kind'] - use that.
      * piercingPower is a Vector2 (value at 100 m, value at maxDistance) and it only
        ever read [0], so nothing lost penetration with range.
    Randomisation is WG's own g_cache.commonConfig piercingPowerRandomization = 0.25.
    '''
    import math, random
    shell = (shot.get('shell') or {}) if hasattr(shot, 'get') else {}
    kind = shell.get('kind', 'ARMOR_PIERCING')
    # ARMOR_PIERCING_HE (AP with HE filler) belongs in the AP family: same
    # normalisation and the same 70 deg ricochet rule. It was missing, so it fell
    # through to the HEAT branch - no normalisation, no ricochet, no overmatch.
    # 0.8.2 ships five kinds (vehicles.py _shellKinds): HOLLOW_CHARGE,
    # HIGH_EXPLOSIVE, ARMOR_PIERCING, ARMOR_PIERCING_HE, ARMOR_PIERCING_CR.
    is_ap = kind in ('ARMOR_PIERCING', 'ARMOR_PIERCING_HE', 'ARMOR_PIERCING_CR')
    pp = shot.get('piercingPower', (100.0, 100.0))
    try:
        p100 = float(pp[0]); pfar = float(pp[1])
    except Exception:
        p100 = pfar = 100.0
    maxd = 0.0
    try: maxd = float(shot.get('maxDistance', 0.0) or 0.0)
    except Exception: maxd = 0.0
    if maxd <= 100.0: maxd = 400.0
    if dist_m <= 100.0:
        pierce = p100
    else:
        _t = (min(dist_m, maxd) - 100.0) / (maxd - 100.0)
        pierce = p100 + (pfar - p100) * _t
    if penetration_factor is None:
        penetration_factor = random.uniform(0.75, 1.25)
    pierce *= float(penetration_factor)
    # spaced armour already crossed (tracks, external devices) is subtracted here
    pierce -= float(pierce_loss or 0.0)
    if pierce < 0.0:
        pierce = 0.0
    armor = float(armor or 0.0)
    if armor <= 0.0:
        return (2, 0.0, pierce)
    _ac = abs(float(hit_angle_cos))
    if _ac > 1.0: _ac = 1.0
    if _ac < 0.0001: _ac = 0.0001
    ang = math.acos(_ac)                      # 0 = square on the plate
    caliber = float(shell.get('caliber', 100) or 100)
    # shell normalisation pulls the impact towards the normal: AP 5 deg, APCR 2 deg,
    # HEAT/HE none. A calibre over three times the plate overmatches it: normalisation
    # grows and the round can no longer ricochet.
    norm = math.radians(2.0) if kind == 'ARMOR_PIERCING_CR' else (math.radians(5.0) if is_ap else 0.0)
    overmatch = is_ap and caliber > armor * 3.0
    if overmatch:
        norm *= 1.4 * caliber / (armor * 3.0)
    elif is_ap and ang > math.radians(70.0):
        return (0, armor / max(0.087, _ac), pierce)
    ang_eff = ang - norm
    if ang_eff < 0.0: ang_eff = 0.0
    eff = armor / max(0.0001, math.cos(ang_eff))
    if kind == 'HIGH_EXPLOSIVE':
        # HE penetrates or it does not, like everything else - it just gets no
        # normalisation and cannot ricochet (both already handled above). This used
        # to be an unconditional 2, so every HE round dealt FULL damage through any
        # thickness. A non-penetration here is not a miss: the caller runs
        # _offh_he_damage() for the blast.
        return (2 if pierce >= eff else 1, eff, pierce)
    return (2 if pierce >= eff else 1, eff, pierce)


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _legacy_shell(shell):
    if isinstance(shell, dict):
        return shell
    result = {}
    for name in ('kind', 'caliber', 'damage', 'explosionRadius',
                 'compactDescr', 'name'):
        value = getattr(shell, name, None)
        if value is not None:
            result[name] = value
    return result


def legacy_shot(shot):
    """Convert a 2.3.1.2 GunShot object without changing the copied law."""
    if isinstance(shot, dict):
        return shot
    return {
        'shell': _legacy_shell(_field(shot, 'shell', {}) or {}),
        'piercingPower': _field(shot, 'piercingPower', (100.0, 100.0)),
        'maxDistance': _field(shot, 'maxDistance', 0.0),
    }


def collision_layers(collisions):
    result = []
    for collision in collisions or ():
        distance = getattr(collision, 'dist')
        angle = getattr(collision, 'hitAngleCos')
        material = getattr(collision, 'matInfo')
        component = getattr(collision, 'compName')
        try:
            result.append((float(distance), float(angle), material, component))
        except (TypeError, ValueError):
            raise TypeError('2.3.1.2 collision contains a non-numeric field')
    return sorted(result, key=lambda item: item[0])


def _call_with_uniform(function, uniform, *args):
    if uniform is None:
        return function(*args)
    original = random.uniform
    random.uniform = uniform
    try:
        return function(*args)
    finally:
        random.uniform = original


def penetration(shot, distance, armor, hit_angle_cos,
                pierce_loss=0.0, random_uniform=None,
                penetration_factor=None):
    return _call_with_uniform(
        _offh_penetration, random_uniform, legacy_shot(shot),
        distance, armor, hit_angle_cos, pierce_loss, penetration_factor)


def sample_penetration_factor(random_uniform=None):
    """Draw the one 2.3.1.2 penetration random factor owned by a shell."""
    sampler = random.uniform if random_uniform is None else random_uniform
    return float(sampler(0.75, 1.25))


def range_piercing(shot, distance):
    """Return the non-randomized piercing mean at one travelled distance."""
    converted = legacy_shot(shot)
    pp = converted.get('piercingPower', (100.0, 100.0))
    try:
        near, far = float(pp[0]), float(pp[1])
    except Exception:
        near = far = 100.0
    try:
        maximum = float(converted.get('maxDistance', 0.0) or 0.0)
    except Exception:
        maximum = 0.0
    if maximum <= 100.0:
        maximum = 400.0
    if distance <= 100.0:
        return near
    factor = (min(float(distance), maximum) - 100.0) / (maximum - 100.0)
    return near + (far - near) * factor


def sampled_piercing(shot, distance, penetration_factor,
                     pierce_loss=0.0):
    """Reuse one shell-owned factor at distance after external obstacles."""
    return max(0.0, range_piercing(shot, distance) *
               float(penetration_factor) - float(pierce_loss or 0.0))


def nominal_piercing_after_loss(shot, distance, pierce_loss=0.0):
    """Return the non-randomized 2.3.1.2 range value after external obstacles."""
    return max(0.0, range_piercing(shot, distance) -
               float(pierce_loss or 0.0))


def resolve_hull_hit(shot, distance, collisions, random_uniform=None,
                     pierce_loss=0.0, penetration_factor=None):
    return _call_with_uniform(
        _offh_resolve_hull_hit, random_uniform, legacy_shot(shot),
        distance, collision_layers(collisions), pierce_loss,
        penetration_factor)


def he_nominal_armor(collisions, descriptor=None):
    return _offh_he_nominal_armor(
        collision_layers(collisions), descriptor)


def damage(shot, result, nominal_armor, random_uniform=None):
    """Apply the legacy direct-damage formula to the resolved vehicle hit."""
    converted = legacy_shot(shot)
    shell = converted.get('shell') or {}
    raw = shell.get('damage')
    try:
        average = float(raw[0])
    except (TypeError, ValueError, IndexError):
        try:
            average = float(raw)
        except (TypeError, ValueError):
            return 0
    uniform = random_uniform or random.uniform
    rolled = int(uniform(average * 0.75, average * 1.25))
    if int(result) == 2:
        return rolled
    if _offh_is_he(converted):
        return _offh_he_damage(rolled, nominal_armor, 0.0)
    return 0


def he_radius(shot):
    return _offh_he_radius(legacy_shot(shot))


def is_he(shot):
    return _offh_is_he(legacy_shot(shot))


def he_hull_armor(descriptor):
    return _offh_he_hull_armor(descriptor)


def he_splash_damage(shot, nominal_armor, distance_fraction,
                     random_uniform=None):
    converted = legacy_shot(shot)
    shell = converted.get('shell') or {}
    raw = shell.get('damage')
    try:
        average = float(raw[0])
    except (TypeError, ValueError, IndexError):
        try:
            average = float(raw)
        except (TypeError, ValueError):
            return 0
    uniform = random_uniform or random.uniform
    rolled = uniform(average * 0.75, average * 1.25)
    return _offh_he_damage(rolled, nominal_armor, distance_fraction)


def apply_he_tuning(overrides):
    return _offh_he_apply_tuning(overrides)
