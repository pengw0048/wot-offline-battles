# -*- coding: utf-8 -*-
"""Era-accurate (WoT 0.8.2) module/device damage + repair model.

Pure data + math: NO BigWorld imports, so it is desktop-testable (run this file
directly under Python 2 or 3 to execute the self-test at the bottom). All the
BigWorld/UI/sound side effects stay in offline_battle.py; this module only decides
*which* device is hit, *how much* HP it loses, and *how fast* it repairs.

Almost every constant here is EXACT, read from the running client's own data:
  * device HP pools live on the vehicle type descriptor
      engine        -> td.engine['maxHealth'] / ['maxRegenHealth']
      ammo rack     -> td.hull['ammoBayHealth'][...]
      fuel tank     -> td.fuelTank[...]
      radio         -> td.radio[...]
      tracks        -> td.chassis[...]        (one pool, either track)
      gun           -> td.gun[...]
      turret ring   -> td.turret['turretRotatorHealth'][...]
      optics        -> td.turret['surveyingDeviceHealth'][...]
  * per-material saving throw (chance the device is actually critted when the
    shell ray enters its hitbox) is on the MaterialInfo object the collision
    already returns: h_mat.chanceToHitByProjectile / .chanceToHitByExplosion
  * shell module-damage = shell['damage'][1] ("devices"), rolled +/-25%
  * equipment/crew health & repair-speed multipliers are pre-applied into
    td.miscAttrs (repairSpeedFactor, ammoBayHealthFactor, engineHealthFactor,
    fuelTankHealthFactor, chassisHealthFactor)

The ONLY reconstructed (server-only in 2012, never shipped in the client)
numbers are the ones flagged RECONSTRUCTED below: the base auto-repair rate,
the critical/orange threshold, and the fire tick. Keep them here, documented,
so they are the single place to tune against era footage.
"""

import random

# --- EXACT (from res/scripts/item_defs/vehicles/common/vehicle.xml) ---------
DAMAGE_RANDOMIZATION = 0.25          # shell devices-damage +/-25% (shell descr)
MIN_FIRE_STARTING_DAMAGE = 21        # miscParams/minFireStartingDamage
DEFAULT_FIRE_BURN_FRACTION = 0.0875  # engine default healthBurnPerSec fraction

# --- RECONSTRUCTED (server-only in 2012; tune against era gameplay) ----------
# Fraction of max HP at or below which a functional module shows as
# 'critical' (orange).  This is a damage-state threshold; maxRegenHealth is a
# separate auto-repair destination and must not decide the first yellow state.
CRITICAL_HP_FRACTION = 0.5
# Seconds to auto-repair a destroyed module back to functional with a nominal
# crew at 0% Repair *secondary* skill (major qualification 100%), no toolbox, no
# large kit. Tracks are quicker. The Repair skill / toolbox / kit multiply this.
BASE_TRACK_REPAIR_SECONDS = 10.0
BASE_MODULE_REPAIR_SECONDS = 18.0
# Repair-speed gain at 100% crew Repair skill: 1.0 => ~2x faster than 0% skill.
REPAIR_SKILL_SPEEDUP = 1.0
# Fraction of max HP lost per second while on fire (bots + player DoT).
FIRE_DAMAGE_FRACTION_PER_SEC = 0.05
# How long a fire burns before the crew smothers it, when no extinguisher is
# used. Nothing in the client can supply this: vehicles.py reads healthBurnPerSec,
# healthRegenPerSec and hysteresisHealth only under `not IS_CLIENT or
# IS_DEVELOPMENT`, so the shipped descriptors carry no burn parameters at all.
FIRE_DURATION_SECONDS = 10.0
# The fuel tank is the one device with no repair bar: it is not patched up over
# time, it simply stops being the thing that is burning. When the fire ends it
# comes back at its regen cap, i.e. red -> orange in one step.
NO_REPAIR_PROGRESS_DEVICES = frozenset(['fuelTankHealth'])

# Device extra-name -> (descriptor attr, sub-key or None, miscAttrs health-factor key or None)
_DEVICE_HP_SPEC = {
    'engineHealth':          ('engine', None, 'engineHealthFactor'),
    'ammoBayHealth':         ('hull', 'ammoBayHealth', 'ammoBayHealthFactor'),
    'fuelTankHealth':        ('fuelTank', None, 'fuelTankHealthFactor'),
    'radioHealth':           ('radio', None, None),
    'leftTrackHealth':       ('chassis', None, 'chassisHealthFactor'),
    'rightTrackHealth':      ('chassis', None, 'chassisHealthFactor'),
    'gunHealth':             ('gun', None, None),
    'turretRotatorHealth':   ('turret', 'turretRotatorHealth', None),
    'surveyingDeviceHealth': ('turret', 'surveyingDeviceHealth', None),
}

# Fallback saving throws if a material lacks chanceToHitByProjectile.
# EXACT 0.8.2 values (vehicle.xml materials), used only when the live material
# object is unavailable (e.g. the synthetic-hit fallback path).
_FALLBACK_CHANCE = {
    'ammoBayHealth': 0.27,
    'engineHealth': 0.45,
    'fuelTankHealth': 0.45,
    'radioHealth': 0.45,
    'turretRotatorHealth': 0.45,
    'surveyingDeviceHealth': 0.45,
    'gunHealth': 0.33,
    'leftTrackHealth': 1.0,
    'rightTrackHealth': 1.0,
    'commanderHealth': 0.33,
    'driverHealth': 0.33,
    'gunner1Health': 0.33,
    'gunner2Health': 0.33,
    'loader1Health': 0.33,
    'loader2Health': 0.33,
    'radioman1Health': 0.33,
    'radioman2Health': 0.33,
}

# Devices whose destruction does NOT subtract hull HP (vehicleDamageFactor 0.0).
CRIT_ONLY_DEVICES = frozenset([
    'gunHealth', 'surveyingDeviceHealth', 'leftTrackHealth', 'rightTrackHealth',
    'commanderHealth', 'driverHealth', 'gunner1Health', 'gunner2Health',
    'loader1Health', 'loader2Health', 'radioman1Health', 'radioman2Health',
])

# --- Crew injuries -----------------------------------------------------------
# Crew "device" materials (vehicle.xml): crit-only, one successful saving throw
# knocks the crewman out (binary; med kit revives). chanceToHit 0.33 projectile /
# 0.15 explosion (already in _FALLBACK_CHANCE above).
CREW_HEALTH_NAMES = frozenset([
    'commanderHealth', 'driverHealth', 'gunner1Health', 'gunner2Health',
    'loader1Health', 'loader2Health', 'radioman1Health', 'radioman2Health',
])


# Crew is the ONLY group whose two chances differ (0.33 projectile / 0.15
# explosion in common/vehicle.xml); every device uses the same value for both.
# Kept separate so a synthesized hit cannot silently use the projectile number
# for a blast. NB: an adopted third-party table gives 0.10 here - the shipped
# data says 0.15.
_FALLBACK_CHANCE_EXPLOSION = {
    'commanderHealth': 0.15, 'driverHealth': 0.15,
    'gunner1Health': 0.15, 'gunner2Health': 0.15,
    'loader1Health': 0.15, 'loader2Health': 0.15,
    'radioman1Health': 0.15, 'radioman2Health': 0.15,
}


def fallback_chance(name, by_explosion=False):
    """Era saving throw for a device or crewman with no live material object."""
    if by_explosion and name in _FALLBACK_CHANCE_EXPLOSION:
        return _FALLBACK_CHANCE_EXPLOSION[name]
    return _FALLBACK_CHANCE.get(name, 0.33)


def crew_role_base(ui_name):
    """'gunner1' -> 'gunner'; 'commander' -> 'commander'. Strips a trailing index."""
    return ui_name.rstrip('0123456789')


def crew_impaired_roles(ko_names):
    """Set of base roles that have a knocked-out member (from role-instance names
    like 'gunner1', 'commander')."""
    roles = set()
    for n in ko_names:
        roles.add(crew_role_base(str(n)))
    return roles


# #1513 VehicleDescrCrew._processSkills: factor = 0.57 + 0.43 * efficiency, and
# efficiency is the role's average level over its slots divided by 100.  A dead
# crewman contributes nothing to the sum but still counts as a slot.
CREW_FACTOR_BASE = 0.57
CREW_FACTOR_SLOPE = 0.43
# _calcLeverIncreaseForNonCommander adds commanderLevel/COMMANDER_ADDITION_RATIO
# to every other role, so a live 100% commander lifts them to 110.
COMMANDER_ADDITION_RATIO = 10.0


def crew_role_factor(level_share, commander_alive=True):
    """#1513's role factor for a share of a role's slots still crewed.

    ``level_share`` is the surviving fraction of the role's slots.  A 100% crew
    is the reference, so each surviving slot carries level 100 plus the live
    commander's own 100/10 contribution, and a dead slot carries nothing.
    """
    per_slot = 100.0
    if commander_alive:
        per_slot += 100.0 / COMMANDER_ADDITION_RATIO
    level = per_slot * max(0.0, min(1.0, float(level_share)))
    return CREW_FACTOR_BASE + CREW_FACTOR_SLOPE * (level / 100.0)


# A fit 100% crew, and one whose single-man role is out, on the same curve.
CREW_FACTOR_FIT = crew_role_factor(1.0, True)
CREW_FACTOR_ROLE_OUT = crew_role_factor(0.0, True)
CREW_FACTOR_COMMANDER_OUT = crew_role_factor(1.0, False)
# A slower time divides by the factor; a speed multiplies by it.
CREW_KO_TIME_FACTOR = CREW_FACTOR_FIT / CREW_FACTOR_ROLE_OUT
CREW_KO_SPEED_FACTOR = CREW_FACTOR_ROLE_OUT / CREW_FACTOR_FIT
CREW_KO_COMMANDER_TIME_MALUS = CREW_FACTOR_FIT / CREW_FACTOR_COMMANDER_OUT
CREW_KO_COMMANDER_SPEED_MALUS = CREW_FACTOR_COMMANDER_OUT / CREW_FACTOR_FIT
# The driver's #1513 factor multiplies terrain resistance; this port applies it
# to the throttle instead, which is the mobility knob the battle owns.
CREW_KO_MOBILITY_FACTOR = CREW_KO_SPEED_FACTOR
CREW_KO_VISION_FACTOR = CREW_KO_SPEED_FACTOR


def crew_stat_factor(ko_names, stat):
    """Multiplier for one stat given the knocked-out crew.

    ``stat`` is one of 'reload', 'aim_time', 'dispersion', 'turret_speed',
    'mobility', 'vision', 'signal'.  A value above 1.0 lengthens a time; a
    value below 1.0 slows a speed or shrinks a range.
    """
    roles = crew_impaired_roles(ko_names)
    commander_out = 'commander' in roles
    f = 1.0
    if stat == 'reload':
        if 'loader' in roles:
            f *= CREW_KO_TIME_FACTOR
        if commander_out:
            f *= CREW_KO_COMMANDER_TIME_MALUS
    elif stat in ('dispersion', 'aim_time'):
        if 'gunner' in roles:
            f *= CREW_KO_TIME_FACTOR
        if commander_out:
            f *= CREW_KO_COMMANDER_TIME_MALUS
    elif stat == 'turret_speed':
        if 'gunner' in roles:
            f *= CREW_KO_SPEED_FACTOR
        if commander_out:
            f *= CREW_KO_COMMANDER_SPEED_MALUS
    elif stat == 'mobility':
        if 'driver' in roles:
            f *= CREW_KO_MOBILITY_FACTOR
        if commander_out:
            f *= CREW_KO_COMMANDER_SPEED_MALUS
    elif stat == 'vision':
        if commander_out:
            f *= CREW_KO_VISION_FACTOR
        if 'radioman' in roles:
            f *= CREW_KO_VISION_FACTOR
    elif stat == 'signal':
        if 'radioman' in roles:
            f *= CREW_KO_SPEED_FACTOR
        if commander_out:
            f *= CREW_KO_COMMANDER_SPEED_MALUS
    return f


# --- Interior devices: no collision geometry exists ---------------------------
# Verified against the shipped client (2026-07-28): all 1975 collision .visual
# files across 252 vehicles carry only armor_N, gun (252), leftTrack/rightTrack
# (252), surveyingDevice (218) and gunBreech (37). The interior material kinds
# appear on exactly two leftover models (german/G49_G_Panther has the full set,
# american/A38_T92 has ammoBay), and no crewman kind exists anywhere. WG resolved
# engine / ammo bay / fuel tank / radio / turret ring / crew hits server-side,
# against a collision model that was never shipped.
#
# So a penetrating hit gets ONE reconstructed interior roll: pick a candidate
# weighted by (era saving throw x layout weight), then run its own saving throw.
# That lands the per-penetration crit rate around 35-45%, which is the era feel.
# RECONSTRUCTED: the layout weights below - a shell entering the rear deck is far
# more likely to find the engine than the driver, and vice versa.
INTERIOR_LAYOUT = {
    # Calibrated against the only shipped model that still carries WG's own
    # interior boxes: german/G49_G_Panther/collision/Hull. NOTE what that vehicle
    # is - germany/list.xml gives G_Panther `level 6`, tags `SPG mediumSPG`, i.e.
    # the G.W. Panther, an SPG on a Panther chassis with the engine amidships and
    # an open fighting compartment at the rear. It is one sample and not even a
    # turreted tank, so it fixes the ORDER of the compartments, not the weights.
    # Measured in hull coordinates (ring z -1.41, hull z -3.65..+3.13, half width 1.82):
    #   driver        z +1.66..+2.47  x -0.64..-0.18   ahead of the ring, left
    #   fuel tank     z +0.39..+2.45  x +0.50..+0.84   ahead of the ring, right
    #   radio         z -0.28..+0.29  x +0.50..+0.84   ahead of the ring, right
    #   engine/trans  z -0.28..+1.22  centre           ahead of the ring
    #   turret ring   z -1.85..-0.88  x +-0.61
    #   ammo racks    z -2.97..-0.47  |x| 0.89..1.29   behind the ring, BOTH sponsons
    # So: driver's compartment ahead of the gun mount, engine and racks behind or
    # beside it - which is what interior_zone() splits on. On this vehicle the
    # engine sits amidships, ahead of the rear fighting compartment; on a normal
    # turreted tank the same rule puts it behind the ring. The engine entry in
    # the front list covers the mid/front-engine and front-transmission layouts,
    # which WG also tags with the engine material.
    'hullFront': (('driver', 1.4), ('fuelTankHealth', 0.7), ('ammoBayHealth', 0.6),
                  ('radioHealth', 0.6), ('radioman', 0.5), ('engineHealth', 0.35)),
    'hullRear': (('engineHealth', 1.4), ('fuelTankHealth', 1.0), ('ammoBayHealth', 0.5),
                 ('radioHealth', 0.3), ('loader', 0.3), ('radioman', 0.3)),
    # A side shot crosses the sponsons, where the racks live.
    'hullSide': (('ammoBayHealth', 1.2), ('fuelTankHealth', 0.8), ('engineHealth', 0.7),
                 ('driver', 0.5), ('loader', 0.5), ('radioman', 0.4),
                 ('radioHealth', 0.3), ('gunner', 0.3)),
    'turret': (('commander', 1.0), ('gunner', 1.0), ('loader', 1.0),
               ('turretRotatorHealth', 0.8), ('ammoBayHealth', 0.7), ('radioHealth', 0.2)),
}


# Outer band of the hull, as a fraction of its half width, that counts as
# sponson rather than centre. MEASURED off the one model in the game that still
# carries WG's own interior boxes (german/G49_G_Panther/collision/Hull): its two
# ammo-bay boxes sit at |x| 0.89..1.29 m on a hull 1.82 m half-width, i.e. from
# 0.49 to 0.71 of the way out. Anything past half way is sponson.
SPONSON_FRACTION = 0.5


def interior_zone(local_x, local_z, ring_z, half_width):
    """Compartment a hit lands in, from the entry point in HULL-LOCAL metres
    (+z forward, +x right) plus the tank's own turret-ring z and half width.

    The split is the tank's real geometry, not a guess: everything ahead of the
    ring is the driver's compartment, everything behind it is the engine bay,
    and the outer band on either side is the sponson. That is exactly how the
    Panther's own boxes are laid out - driver, fuel tank and radio ahead of the
    ring, ammo racks in both sponsons behind it.
    """
    # Explicit None checks, not a try/except: under Python 2 a comparison against
    # None silently succeeds (None sorts below every number), so a missing ring
    # position would quietly read as 'hullFront' on every single hit.
    if local_x is None or local_z is None or ring_z is None:
        return 'hullSide'
    try:
        if half_width and abs(local_x) >= SPONSON_FRACTION * abs(half_width):
            return 'hullSide'
        return 'hullFront' if local_z >= ring_z else 'hullRear'
    except (TypeError, ValueError):
        return 'hullSide'


def interior_candidates(zone, roster, td):
    """[(extraName, weight)] a shell entering `zone` could plausibly reach.

    zone: 'hullFront' | 'hullRear' | 'turret'. Devices are dropped when this tank
    has no HP pool for them; crew roles expand to the roster's actual instance
    names ('gunner' -> gunner1, gunner2), splitting the role weight between them.
    Weight = layout weight x the device's EXACT era saving throw, so the ammo bay
    (0.27) is picked less often than the engine (0.45) even at equal layout weight.
    """
    out = []
    for name, w in INTERIOR_LAYOUT.get(zone, ()):
        if name.endswith('Health'):
            if has_hp_pool(td, name):
                out.append((name, w * _FALLBACK_CHANCE.get(name, 0.33)))
            continue
        insts = [r for r in (roster or ()) if crew_role_base(str(r)) == name]
        if not insts:
            continue
        share = w / float(len(insts))
        for r in insts:
            key = str(r) + 'Health'
            out.append((key, share * _FALLBACK_CHANCE.get(key, 0.33)))
    return out


def pick_interior(candidates, roll=None):
    """Weighted pick from interior_candidates(); None when nothing is eligible.
    roll is a 0..1 float for the self-test; production passes None (random)."""
    total = 0.0
    for _n, w in candidates:
        total += w
    if total <= 0.0:
        return None
    r = (random.random() if roll is None else roll) * total
    acc = 0.0
    for n, w in candidates:
        acc += w
        if r < acc:
            return n
    return candidates[-1][0]


# --- Damaged-module penalties -------------------------------------------------
# RECONSTRUCTED. The client never computed these: avatar.py only gates input on
# __cantMoveCriticals (engine/leftTrack/rightTrack/vehicle/crew destroyed) and
# __cantShootCriticals (gun/vehicle/crew destroyed); every degradation was
# server side.  Yellow tracks are intentionally absent below: #1513 only hard
# gates a destroyed track, while the documented damaged-module penalties cover
# engine/ammo/turret/radio/crew rather than reducing track traverse.
DAMAGED_MODULE_EFFICIENCY = 0.5
# For the two modules whose destruction is not already a hard gate (optics and
# radio keep working, just badly), destruction is worse than damage but not zero.
DESTROYED_MODULE_EFFICIENCY = 0.25

# stat -> (device names, damaged factor, destroyed factor)
# A destroyed factor of None means destruction is hard-gated at the call site
# (cannot fire / cannot move), so no multiplier applies.
_MODULE_STAT_SPEC = {
    'reload': (('ammoBayHealth',), 1.0 / DAMAGED_MODULE_EFFICIENCY, None),
    'dispersion': (('gunHealth',), 1.0 / DAMAGED_MODULE_EFFICIENCY, None),
    'aim_time': (('gunHealth',), 1.0 / DAMAGED_MODULE_EFFICIENCY, None),
    'turret_speed': (('turretRotatorHealth',), DAMAGED_MODULE_EFFICIENCY, 0.0),
    'mobility': (('engineHealth',), DAMAGED_MODULE_EFFICIENCY, 0.0),
    'vision': (('surveyingDeviceHealth',), DAMAGED_MODULE_EFFICIENCY, DESTROYED_MODULE_EFFICIENCY),
    'signal': (('radioHealth',), DAMAGED_MODULE_EFFICIENCY, DESTROYED_MODULE_EFFICIENCY),
}


# Floor for the combined view-range malus. Crew and module maluses multiply, so
# a knocked-out commander and radioman on top of a shot-out optic used to reach
# 0.75*0.75*0.25 = 0.14 - a 400 m tank left seeing 56 m, which reads as "enemies
# are invisible again" rather than as a module effect. Retail never blinds you:
# the observation device costs view range, it does not switch vision off.
MIN_VISION_FACTOR = 0.5


def clamp_vision_factor(factor):
    """Combined crew x module vision malus, floored so it can degrade sight but
    never take it away."""
    try:
        value = float(factor)
    except (TypeError, ValueError):
        return 1.0
    if value < MIN_VISION_FACTOR:
        return MIN_VISION_FACTOR
    if value > 1.0:
        return 1.0
    return value


def module_stat_factor(devices_hp, destroyed, td, stat, critical=None):
    """Multiplier for a stat from MODULE state, the counterpart of
    crew_stat_factor. >1 worsens time-like stats (reload, dispersion, aim_time);
    <1 worsens capability-like stats (mobility, turret_speed, vision, signal).
    1.0 when the relevant module is untouched."""
    spec = _MODULE_STAT_SPEC.get(stat)
    if spec is None:
        return 1.0
    names, dmg_f, dead_f = spec
    dead = destroyed or ()
    yellow = critical or ()
    hp_map = devices_hp or {}
    f = 1.0
    for n in names:
        if n in dead:
            if dead_f is not None:
                f *= dead_f
            continue
        hp = hp_map.get(n)
        if hp is None:
            continue
        if (n in yellow or
                device_state(hp, device_max_hp(td, n)) == 'critical'):
            f *= dmg_f
    return f


def _descriptor_value(value, name, default=None):
    """Read a copied 0.8.2 mapping or a native #1513 descriptor object.

    #1513 item components inherit a legacy ``get`` method which deliberately
    raises ``AssertionError('Operation is not allowed')``.  Type-dispatch here
    is therefore part of the version adapter; duck-typing ``get`` is invalid.
    """
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _misc_factor(td, key):
    if not key:
        return 1.0
    attrs = getattr(td, 'miscAttrs', None)
    if attrs is None:
        return 1.0
    return float(_descriptor_value(attrs, key, 1.0))


def _raw_hp(td, name):
    """Return (maxHealth, maxRegenHealth, factor_key) straight from the descriptor,
    or (None, None, None) if this device has no HP pool on this tank."""
    spec = _DEVICE_HP_SPEC.get(name)
    if spec is None or td is None:
        return (None, None, None)
    attr, sub, factor_key = spec
    comp = getattr(td, attr, None)
    if comp is None:
        return (None, None, None)
    if sub is not None:
        comp = _descriptor_value(comp, sub)
        if comp is None:
            return (None, None, None)
    mh = _descriptor_value(comp, 'maxHealth')
    mrh = _descriptor_value(comp, 'maxRegenHealth', 0)
    return (mh, mrh, factor_key)


def device_max_hp(td, name):
    """Real per-tank max HP for a device, scaled by any installed optional-device
    health factor (wet ammo rack 1.5, cyclone filter 1.5, ...). None if N/A."""
    mh, _mrh, fk = _raw_hp(td, name)
    if mh is None:
        return None
    return int(round(mh * _misc_factor(td, fk)))


def device_regen_hp(td, name):
    """The HP level crew auto-repair restores a destroyed module to (~50%)."""
    mh, mrh, fk = _raw_hp(td, name)
    if mh is None:
        return None
    if not mrh:
        mrh = int(mh * CRITICAL_HP_FRACTION)
    return int(round(mrh * _misc_factor(td, fk)))


def has_hp_pool(td, name):
    return _raw_hp(td, name)[0] is not None


def shell_damage_base(shell, index=1):
    """Un-rolled shell damage for one channel of the exact ``(armor, devices)``
    tuple: index 0 is armour damage, index 1 is devices damage. Tolerates a flat
    legacy 'deviceDamage' on the devices channel. None if unknown.

    A live material's ``damageKind`` selects the channel, so this helper must
    stay index-driven; it must not assume every module reads devices damage."""
    damage = _descriptor_value(shell, 'damage')
    dmg = None
    if damage is not None:
        try:
            dmg = damage[index]
        except (TypeError, IndexError, KeyError):
            dmg = None
    if dmg is None and index == 1:
        legacy = _descriptor_value(shell, 'deviceDamage')
        if isinstance(legacy, (tuple, list)):
            dmg = legacy[0] if legacy else None
        elif legacy is not None:
            dmg = legacy
    return dmg


def module_damage_roll(shell, index=1):
    """One channel of a shell's damage, rolled +/-25%. None if unknown."""
    dmg = shell_damage_base(shell, index)
    if dmg is None:
        return None
    if index == 0:
        # Track materials may explicitly select the armour-damage channel.
        # Keep its distribution identical to vehicle HP damage. Public RNG
        # descriptions do not establish the separate devices-channel law.
        from gui.mods.offline_lan_0922 import combat_rules
        return combat_rules.sample_shell_value(dmg)
    lo = dmg * (1.0 - DAMAGE_RANDOMIZATION)
    hi = dmg * (1.0 + DAMAGE_RANDOMIZATION)
    return random.uniform(lo, hi)


def saving_throw(h_mat, name, by_explosion=False):
    """Chance the device is actually critted when the shell enters its hitbox.
    Prefers the live material value; falls back to the EXACT era table."""
    attr = 'chanceToHitByExplosion' if by_explosion else 'chanceToHitByProjectile'
    val = getattr(h_mat, attr, None) if h_mat is not None else None
    if val is None:
        val = fallback_chance(name, by_explosion)
    try:
        return float(val)
    except (TypeError, ValueError):
        return fallback_chance(name, by_explosion)


def is_crit_only(name):
    """True if destroying this device does not also subtract hull HP."""
    return name in CRIT_ONLY_DEVICES


def crew_repair_factor(repair_skill_pct):
    """Repair-speed multiplier from the crew's average Repair *secondary* skill.
    0% Repair -> 1.0 (base time); 100% Repair -> 1 + REPAIR_SKILL_SPEEDUP (~2x)."""
    s = repair_skill_pct
    if s < 0.0:
        s = 0.0
    elif s > 100.0:
        s = 100.0
    return 1.0 + REPAIR_SKILL_SPEEDUP * (s / 100.0)


def repair_seconds(name, td, repair_skill_pct=0.0, has_big_repairkit=False,
                   repair_factor=None):
    """Seconds to auto-repair the named device from destroyed to functional,
    combining the reconstructed base (at 0% Repair skill) with the multipliers:
      crew Repair skill  (factors['repairSpeed'] = 0.57 + 0.43*efficiency)
      toolbox            (td.miscAttrs['repairSpeedFactor'], 1.25 when mounted)
      large repair kit   (passive +10% while carried, bonusValue 0.1).
    A ``repair_factor`` from the client's own factor dictionary replaces the
    percentage, and stays normalized so a fully trained crew keeps the speed
    this port has always used."""
    base = BASE_TRACK_REPAIR_SECONDS if 'track' in name.lower() else BASE_MODULE_REPAIR_SECONDS
    if repair_factor is None:
        factor = crew_repair_factor(repair_skill_pct)
    else:
        factor = (1.0 + REPAIR_SKILL_SPEEDUP) * max(0.0, float(repair_factor))
    factor *= _misc_factor(td, 'repairSpeedFactor')
    if has_big_repairkit:
        factor *= 1.10
    if factor <= 0.0:
        factor = 1.0
    return base / factor


def repair_step_hp(current_hp, name, td, dt, repair_skill_pct=0.0,
                   has_big_repairkit=False, repair_factor=None):
    """Advance a device's HP one tick toward its regen cap (~50%). Returns the new
    HP (unchanged if already at/above the cap). Repair kits set HP directly and
    should not go through here."""
    cap = device_regen_hp(td, name)
    if cap is None or current_hp >= cap:
        return current_hp
    secs = repair_seconds(name, td, repair_skill_pct, has_big_repairkit,
                          repair_factor)
    rate = cap / max(0.1, secs)          # HP per second
    new_hp = current_hp + rate * dt
    if new_hp > cap:
        new_hp = cap
    return new_hp


def device_state(current_hp, max_hp):
    """UI/gameplay state from HP: 'destroyed' (0), 'critical' (orange, functional),
    or 'normal' (undamaged).

    A successful module hit above 50% remaining HP is hidden damage: it still
    consumes module HP, but does not turn the icon yellow or apply a damaged
    module penalty.  Auto-repair is the exceptional transition and is tracked
    explicitly by critical_damage, because some #1513 maxRegenHealth values are
    above this threshold.
    """
    if current_hp <= 0:
        return 'destroyed'
    if (max_hp and float(current_hp) <=
            float(max_hp) * CRITICAL_HP_FRACTION):
        return 'critical'
    return 'normal'


# --- Desktop self-test -------------------------------------------------------
if __name__ == '__main__':
    class _Td(object):
        # Minimal stand-in for a real VehicleDescr (IS heavy, sample values).
        engine = {'maxHealth': 105, 'maxRegenHealth': 52}
        hull = {'ammoBayHealth': {'maxHealth': 180, 'maxRegenHealth': 120}}
        fuelTank = {'maxHealth': 100, 'maxRegenHealth': 40}
        radio = {'maxHealth': 60, 'maxRegenHealth': 30}
        chassis = {'maxHealth': 170, 'maxRegenHealth': 130}
        gun = {'maxHealth': 54, 'maxRegenHealth': 27}
        turret = {'turretRotatorHealth': {'maxHealth': 140, 'maxRegenHealth': 70},
                  'surveyingDeviceHealth': {'maxHealth': 90, 'maxRegenHealth': 45}}
        miscAttrs = {'repairSpeedFactor': 1.0, 'ammoBayHealthFactor': 1.0,
                     'engineHealthFactor': 1.0, 'fuelTankHealthFactor': 1.0,
                     'chassisHealthFactor': 1.0}

    class _Mat(object):
        def __init__(self, p, e):
            self.chanceToHitByProjectile = p
            self.chanceToHitByExplosion = e

    td = _Td()
    checks = 0
    fails = 0

    def check(label, cond):
        global checks, fails
        checks += 1
        if not cond:
            fails += 1
            print('FAIL: ' + label)

    # HP lookups
    check('engine max hp', device_max_hp(td, 'engineHealth') == 105)
    check('ammo max hp', device_max_hp(td, 'ammoBayHealth') == 180)
    check('track max hp', device_max_hp(td, 'leftTrackHealth') == 170)
    check('turret ring hp', device_max_hp(td, 'turretRotatorHealth') == 140)
    check('optics hp', device_max_hp(td, 'surveyingDeviceHealth') == 90)
    check('gun hp', device_max_hp(td, 'gunHealth') == 54)
    check('unknown device -> None', device_max_hp(td, 'nopeHealth') is None)
    check('ammo regen ~120', device_regen_hp(td, 'ammoBayHealth') == 120)

    # Health-factor scaling (wet rack 1.5)
    td.miscAttrs['ammoBayHealthFactor'] = 1.5
    check('wet rack scales hp', device_max_hp(td, 'ammoBayHealth') == 270)
    td.miscAttrs['ammoBayHealthFactor'] = 1.0

    # Damage roll from the correct field, +/-25% band
    shell = {'damage': (390, 165), 'name': '_122mm_UBR-471'}
    rolls = [module_damage_roll(shell) for _ in range(2000)]
    check('module dmg uses devices field, not armor',
          abs(sum(rolls) / len(rolls) - 165.0) < 8.0)
    check('module dmg low bound', min(rolls) >= 165 * 0.75 - 0.01)
    check('module dmg high bound', max(rolls) <= 165 * 1.25 + 0.01)

    # Saving throws: live material wins, fallback otherwise
    check('live material chance', abs(saving_throw(_Mat(0.27, 0.27), 'ammoBayHealth') - 0.27) < 1e-9)
    check('fallback engine chance', abs(saving_throw(None, 'engineHealth') - 0.45) < 1e-9)
    check('fallback track chance', abs(saving_throw(None, 'leftTrackHealth') - 1.0) < 1e-9)
    check('HE splash crew chance', abs(saving_throw(_Mat(0.33, 0.15), 'commanderHealth', by_explosion=True) - 0.15) < 1e-9)
    check('fallback crew blast chance is 0.15, not the shell value',
          abs(fallback_chance('commanderHealth', True) - 0.15) < 1e-9 and
          abs(fallback_chance('commanderHealth', False) - 0.33) < 1e-9)
    check('devices use one value for both',
          fallback_chance('engineHealth', True) == fallback_chance('engineHealth', False) == 0.45)

    # Repair: base is at 0% Repair skill; skill/toolbox/kit only speed it up.
    check('crew factor 0%% == 1.0', abs(crew_repair_factor(0.0) - 1.0) < 1e-9)
    check('crew factor 100%% == 2.0', abs(crew_repair_factor(100.0) - 2.0) < 1e-9)
    check('track base repair == 10s', abs(repair_seconds('leftTrackHealth', td) - 10.0) < 1e-6)
    check('module base repair == 18s', abs(repair_seconds('engineHealth', td) - 18.0) < 1e-6)
    check('100%% repair halves time', abs(repair_seconds('leftTrackHealth', td, repair_skill_pct=100.0) - 5.0) < 1e-6)
    td.miscAttrs['repairSpeedFactor'] = 1.25
    check('toolbox speeds repair', abs(repair_seconds('leftTrackHealth', td) - 8.0) < 1e-6)
    td.miscAttrs['repairSpeedFactor'] = 1.0
    check('big kit speeds repair', repair_seconds('engineHealth', td, has_big_repairkit=True) < 18.0)
    check('trained crew speeds repair', repair_seconds('engineHealth', td, repair_skill_pct=50.0) < 18.0)

    # Repair step reaches the regen cap (destroyed track -> ~130 over ~10s at 100% crew)
    hp = 0.0
    for _ in range(600):
        hp = repair_step_hp(hp, 'leftTrackHealth', td, 0.02)
    check('destroyed track repairs to regen cap', abs(hp - 130) < 1.0)
    check('never over-repairs past cap', repair_step_hp(130, 'leftTrackHealth', td, 5.0) == 130)

    # States
    check('state destroyed', device_state(0, 180) == 'destroyed')
    check('state critical', device_state(50, 180) == 'critical')
    check('state hidden damage above half', device_state(100, 180) == 'normal')
    check('state normal', device_state(180, 180) == 'normal')

    # Crit-only classification
    check('track is crit-only', is_crit_only('leftTrackHealth'))
    check('ammo is not crit-only', not is_crit_only('ammoBayHealth'))

    # Crew injuries
    check('crew role base gunner1', crew_role_base('gunner1') == 'gunner')
    check('crew role base commander', crew_role_base('commander') == 'commander')
    check('impaired roles', crew_impaired_roles(['gunner1', 'commander']) == set(['gunner', 'commander']))
    check('gunner KO worsens dispersion', abs(
        crew_stat_factor(['gunner1'], 'dispersion') -
        CREW_KO_TIME_FACTOR) < 1e-9)
    check('loader KO slows reload', abs(
        crew_stat_factor(['loader1'], 'reload') -
        CREW_KO_TIME_FACTOR) < 1e-9)
    check('driver KO reduces mobility', abs(
        crew_stat_factor(['driver'], 'mobility') -
        CREW_KO_MOBILITY_FACTOR) < 1e-9)
    check('commander KO cuts vision', crew_stat_factor(['commander'], 'vision') <= 0.75 + 1e-9)
    check('healthy crew = no penalty', crew_stat_factor([], 'reload') == 1.0)
    check('crew health names', 'gunner1Health' in CREW_HEALTH_NAMES and 'engineHealth' not in CREW_HEALTH_NAMES)

    # Interior candidates (no collision geometry exists for these)
    roster = ['commander', 'driver', 'gunner1', 'loader1', 'radioman1']
    rear_list = interior_candidates('hullRear', roster, td)
    rear = dict(rear_list)
    front = dict(interior_candidates('hullFront', roster, td))
    turret = dict(interior_candidates('turret', roster, td))
    check('rear reaches the engine', 'engineHealth' in rear)
    check('rear does not reach the driver', 'driverHealth' not in rear)
    check('front reaches the driver', 'driverHealth' in front)
    check('turret reaches the ring and the gunner',
          'turretRotatorHealth' in turret and 'gunner1Health' in turret)
    check('engine outweighs the ammo bay at the rear', rear['engineHealth'] > rear['ammoBayHealth'])
    check('crew roles expand to roster instances', 'radioman1Health' in rear)
    side = dict(interior_candidates('hullSide', roster, td))
    check('a side shot exposes the ammo rack more than a rear shot does',
          side['ammoBayHealth'] > rear['ammoBayHealth'])
    check('every zone is populated',
          all(len(interior_candidates(z, roster, td)) > 0
              for z in ('hullFront', 'hullRear', 'hullSide', 'turret')))
    check('an unknown zone yields nothing', interior_candidates('nope', roster, td) == [])

    # Zone split against the Panther's own measured geometry: hull half width
    # 1.82 m, turret ring at z -1.41, driver at z +1.66..+2.47 / x -0.64..-0.18,
    # ammo racks at |x| 0.89..1.29 behind the ring.
    check('driver seat is ahead of the ring', interior_zone(-0.4, 2.0, -1.41, 1.82) == 'hullFront')
    check('engine deck is behind the ring', interior_zone(0.0, -3.0, -1.41, 1.82) == 'hullRear')
    check('sponson rack counts as a side hit', interior_zone(1.1, -1.7, -1.41, 1.82) == 'hullSide')
    check('sponson wins on either side', interior_zone(-1.1, -1.7, -1.41, 1.82) == 'hullSide')
    check('centre line just behind the ring is rear', interior_zone(0.1, -1.5, -1.41, 1.82) == 'hullRear')
    check('missing geometry falls back to side', interior_zone(0.0, 0.0, None, 1.82) == 'hullSide')
    check('a role missing from the roster is dropped',
          'gunner1Health' not in dict(interior_candidates('turret', ['commander', 'driver'], td)))
    check('picks something', pick_interior(rear_list, roll=0.5) in rear)
    check('picker is deterministic for a given roll',
          pick_interior(rear_list, roll=0.0) == rear_list[0][0])
    check('empty candidates -> None', pick_interior([]) is None)
    # A tank with no turret rotator pool must not have it offered
    class _NoRing(_Td):
        turret = {'surveyingDeviceHealth': {'maxHealth': 90, 'maxRegenHealth': 45}}
    check('missing HP pool is dropped',
          'turretRotatorHealth' not in dict(interior_candidates('turret', roster, _NoRing())))

    # Damaged-module penalties
    dh = {'engineHealth': 105, 'gunHealth': 54, 'leftTrackHealth': 170,
          'turretRotatorHealth': 140, 'surveyingDeviceHealth': 90, 'radioHealth': 60,
          'ammoBayHealth': 180}
    check('healthy tank has no module penalty',
          module_stat_factor(dh, set(), td, 'mobility') == 1.0 and
          module_stat_factor(dh, set(), td, 'reload') == 1.0)
    dmg = dict(dh)
    dmg['engineHealth'] = 50
    check('damaged engine halves throttle',
          abs(module_stat_factor(dmg, set(), td, 'mobility') - 0.5) < 1e-9)
    check('destroyed engine is hard-gated, not scaled',
          module_stat_factor(dmg, set(['engineHealth']), td, 'mobility') == 0.0)
    dmg2 = dict(dh)
    dmg2['ammoBayHealth'] = 90
    check('damaged ammo bay doubles the reload',
          abs(module_stat_factor(dmg2, set(), td, 'reload') - 2.0) < 1e-9)
    dmg3 = dict(dh)
    dmg3['gunHealth'] = 20
    check('damaged gun doubles dispersion and aim time',
          abs(module_stat_factor(dmg3, set(), td, 'dispersion') - 2.0) < 1e-9 and
          abs(module_stat_factor(dmg3, set(), td, 'aim_time') - 2.0) < 1e-9)
    check('destroyed gun does not scale dispersion (cannot fire at all)',
          module_stat_factor(dmg3, set(['gunHealth']), td, 'dispersion') == 1.0)
    dmg4 = dict(dh)
    dmg4['surveyingDeviceHealth'] = 40
    check('damaged optics halve view range',
          abs(module_stat_factor(dmg4, set(), td, 'vision') - 0.5) < 1e-9)
    check('destroyed optics are worse but not blind',
          abs(module_stat_factor(dmg4, set(['surveyingDeviceHealth']), td, 'vision') - 0.25) < 1e-9)
    dmg5 = dict(dh)
    dmg5['leftTrackHealth'] = 100
    check('a damaged track does not reduce traverse',
          module_stat_factor(dmg5, set(), td, 'traverse') == 1.0)
    check('unknown stat -> 1.0', module_stat_factor(dh, set(), td, 'nope') == 1.0)
    check('vision malus is floored, never blinding',
          abs(clamp_vision_factor(0.75 * 0.75 * 0.25) - MIN_VISION_FACTOR) < 1e-9)
    check('a mild vision malus passes through',
          abs(clamp_vision_factor(0.75) - 0.75) < 1e-9)
    check('healthy vision stays 1.0', clamp_vision_factor(1.0) == 1.0)
    check('garbage vision factor -> 1.0', clamp_vision_factor(None) == 1.0)

    print('%d checks, %d failures' % (checks, fails))
    raise SystemExit(1 if fails else 0)
