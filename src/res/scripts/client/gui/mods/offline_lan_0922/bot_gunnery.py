from __future__ import print_function

"""Bot gunnery skill tiers for the hidden simulation worker.

Retail #1513 ships no Bot skill rating, so every Bot in this project used to
fire the same way: a 100 percent crew, an exact analytic intercept solution
aimed at the target centre, and no delay between holding a target and pulling
the trigger.  Shot dispersion was modelled correctly, but a perfectly centred
circle on a perfectly predicted point is strictly better than a good human
gunner, which is why Bots read as unnaturally accurate.

This module owns the replacement, in two clearly separated halves.

``crew_level`` is a real shipped mechanic.  It selects the level passed to
``items.utils.generateDefaultCrew`` so a weaker tier inherits #1513's own
longer aiming time, wider fully-aimed dispersion, slower reload, slower
turret and shorter view range.  No coefficient here is invented.

Everything else models the human gunner #1513 never simulated: a reaction
delay, a bounded willingness to wait for the aiming circle, a slowly drifting
aim-point bias and an imperfect lead on a moving target.  Those numbers are an
explicit product choice for this project.  They are not retail data, they are
not derived from client files, and they must not be presented as either.

Every draw is seeded from stable round, Bot and target identity, so authority
takeover reproduces the same gunner rather than re-rolling one mid-engagement.
The radius law matches ``bot_runtime._dispersed_barrel_angles`` so the project
keeps one truncated-half-normal idiom instead of two.
"""

import hashlib
import math
import random


SKILL_ROOKIE = 'rookie'
SKILL_REGULAR = 'regular'
SKILL_VETERAN = 'veteran'
SKILL_ELITE = 'elite'

# Ordered weakest to strongest.  Wire values and saved launcher profiles use
# these names, so they are protocol: extend the tuple, never renumber it.
SKILL_TIERS = (SKILL_ROOKIE, SKILL_REGULAR, SKILL_VETERAN, SKILL_ELITE)
DEFAULT_SKILL = SKILL_REGULAR

# One aim-point bias is held for this long before the gunner re-lays the gun.
# A single frozen bias would make a whole engagement uniformly lucky or
# unlucky; re-drawing every frame would read as jitter rather than as aim.
AIM_BIAS_SECONDS = 1.5

# The vertical half of an aim-point error is deliberately smaller than the
# lateral half.  A large vertical bias walks the solution into the terrain or
# outside the gun's pitch limits, and the Bot then holds fire instead of
# missing, which is not the behaviour being modelled.
VERTICAL_BIAS_SHARE = 0.45

# A long shot must not aim at the sky.  Beyond this the angular model stops
# growing; the shot is already a clean miss.
MAX_AIM_OFFSET_METRES = 8.0

_PARAMETERS = {
    SKILL_ROOKIE: {
        # #1513 crew level, fed to generateDefaultCrew.
        'crew_level': 50,
        # Seconds of holding a firing solution on one target before the
        # trigger is available at all.
        'reaction_seconds': 1.20,
        # Seconds this tier is willing to spend laying the gun before it
        # opens fire anyway.  Bounded: a Bot that keeps moving never
        # converges, but this wait always expires, so it still shoots.
        'patience_seconds': 0.60,
        # Multiple of the fully-aimed circle this tier accepts as "aimed".
        'converged_factor': 2.60,
        # Aim-point bias, as a multiple of the gun's own fully-aimed
        # dispersion angle, so an accurate gun stays accurate and a derp gun
        # stays a derp gun.
        'aim_bias_factor': 1.80,
        # Fraction of the computed lead this tier can be wrong by.
        'lead_error': 0.45,
    },
    SKILL_REGULAR: {
        'crew_level': 75,
        'reaction_seconds': 0.75,
        'patience_seconds': 1.10,
        'converged_factor': 1.80,
        'aim_bias_factor': 0.85,
        'lead_error': 0.22,
    },
    SKILL_VETERAN: {
        'crew_level': 90,
        'reaction_seconds': 0.45,
        'patience_seconds': 1.70,
        'converged_factor': 1.35,
        'aim_bias_factor': 0.35,
        'lead_error': 0.09,
    },
    SKILL_ELITE: {
        'crew_level': 100,
        'reaction_seconds': 0.25,
        'patience_seconds': 2.40,
        'converged_factor': 1.12,
        'aim_bias_factor': 0.10,
        'lead_error': 0.02,
    },
}

SKILL_MODE_EASY = 'easy'
SKILL_MODE_RELAXED = 'relaxed'
SKILL_MODE_MIXED = 'mixed'
SKILL_MODE_HARD = 'hard'
SKILL_MODE_BRUTAL = 'brutal'

# Waiting-room presets.  Each is the tier distribution of the whole roster,
# in SKILL_TIERS order.  ``mixed`` is the pub-team shape the project defaults
# to: mostly ordinary Bots, a few clearly weak ones, a few clearly good ones.
SKILL_MODES = (
    SKILL_MODE_EASY, SKILL_MODE_RELAXED, SKILL_MODE_MIXED,
    SKILL_MODE_HARD, SKILL_MODE_BRUTAL)
DEFAULT_SKILL_MODE = SKILL_MODE_MIXED

_MODE_WEIGHTS = {
    SKILL_MODE_EASY: (0.85, 0.15, 0.00, 0.00),
    SKILL_MODE_RELAXED: (0.45, 0.40, 0.15, 0.00),
    SKILL_MODE_MIXED: (0.20, 0.45, 0.25, 0.10),
    SKILL_MODE_HARD: (0.00, 0.25, 0.45, 0.30),
    SKILL_MODE_BRUTAL: (0.00, 0.00, 0.00, 1.00),
}


def _stable_seed(*parts):
    """Return the same positive integer on Python 2 and Python 3."""
    text_parts = []
    for part in parts:
        try:
            text_parts.append(str(part))
        except Exception:
            text_parts.append('?')
    payload = '|'.join(text_parts).encode('utf-8')
    return int(hashlib.sha1(payload).hexdigest()[:8], 16) & 0x7fffffff


def normalize_skill(value):
    """Return one supported gunnery tier name."""
    return value if value in SKILL_TIERS else DEFAULT_SKILL


def normalize_skill_mode(value):
    """Return one supported waiting-room Bot skill preset."""
    return value if value in SKILL_MODES else DEFAULT_SKILL_MODE


def skill_parameters(skill):
    """Return the shared parameter bundle for one tier.  Do not mutate it."""
    return _PARAMETERS[normalize_skill(skill)]


def crew_level(skill):
    """Return the #1513 crew level this tier trains its Bots to."""
    return int(skill_parameters(skill)['crew_level'])


def resolve_skill(mode, round_id, team, slot):
    """Pick one roster slot's tier from a preset, without shared state.

    The draw depends only on the round and the slot, so the worker, the
    server and any UI reach the same answer for the same Bot without the
    tier having to travel between them first.
    """
    mode = normalize_skill_mode(mode)
    weights = _MODE_WEIGHTS[mode]
    roll = random.Random(
        _stable_seed('bot-skill-v1', round_id, mode, team, slot)).random()
    total = 0.0
    for index, weight in enumerate(weights):
        total += weight
        if roll < total:
            return SKILL_TIERS[index]
    return SKILL_TIERS[-1]


def bias_epoch(hold_seconds):
    """Return the aiming epoch index for one continuous target hold."""
    try:
        held = float(hold_seconds)
    except (TypeError, ValueError, OverflowError):
        return 0
    if math.isnan(held) or math.isinf(held) or held <= 0.0:
        return 0
    return int(held / AIM_BIAS_SECONDS)


def engagement_error(skill, round_id, bot_id, target_key, epoch):
    """Return one gunner's frozen error for one aiming epoch.

    ``radius`` is a truncated half-normal in [0, 1]; the caller scales it by
    the tier's bias factor and the gun's own dispersion.  ``lead_scale``
    multiplies the target velocity fed to the intercept solve, so a weak
    gunner under- or over-leads a moving target for the whole epoch instead
    of correcting between shots.
    """
    params = skill_parameters(skill)
    generator = random.Random(_stable_seed(
        'bot-gunner-v1', round_id, bot_id, target_key, epoch))
    radius = abs(generator.gauss(0.0, 0.5))
    if radius > 1.0:
        radius = generator.random()
    return {
        'radius': radius,
        'azimuth': generator.uniform(0.0, 2.0 * math.pi),
        'lead_scale': (1.0 + generator.uniform(-1.0, 1.0) *
                       float(params['lead_error'])),
    }


def aim_offset_metres(skill, error, dispersion_angle, distance):
    """Return the (lateral, vertical) aim-point error for one shot, in metres.

    The caller maps these onto the line of sight.  The error is angular so it
    grows with range exactly like the aiming circle it is measured against.
    """
    params = skill_parameters(skill)
    try:
        angle = float(dispersion_angle)
        reach = float(distance)
        radius = float(error['radius'])
        azimuth = float(error['azimuth'])
    except (KeyError, TypeError, ValueError, OverflowError):
        return 0.0, 0.0
    if (math.isnan(angle) or math.isinf(angle) or angle <= 0.0 or
            math.isnan(reach) or math.isinf(reach) or reach <= 0.0):
        return 0.0, 0.0
    magnitude = min(
        MAX_AIM_OFFSET_METRES,
        float(params['aim_bias_factor']) * angle * reach * radius)
    return (magnitude * math.cos(azimuth),
            magnitude * math.sin(azimuth) * VERTICAL_BIAS_SHARE)


def may_fire(skill, hold_seconds, laying_seconds, dispersion_factor,
             opening_shot=True):
    """Return whether this gunner has reacted and finished laying.

    ``hold_seconds`` is the continuous hold on the current target and drives
    the reaction delay.  ``laying_seconds`` runs from that acquisition and
    drives the aiming patience.  ``dispersion_factor`` is the live circle as
    a multiple of the fully-aimed circle, so 1.0 is fully aimed.

    Only the opening shot of an engagement waits for the circle.  Every gun
    blooms after firing, so a better tier - which accepts a tighter circle
    and waits longer for it - would otherwise fire a fast or clip gun less
    often than a worse tier holding the same gun, and the whole ladder would
    invert.  After the opening shot the gun's own reload owns the cadence;
    the aim-point bias and the lead error still separate the tiers on every
    round.
    """
    params = skill_parameters(skill)
    try:
        held = float(hold_seconds)
        laying = float(laying_seconds)
        factor = float(dispersion_factor)
    except (TypeError, ValueError, OverflowError):
        return False
    if (math.isnan(held) or math.isinf(held) or math.isnan(laying) or
            math.isnan(factor)):
        return False
    if held < float(params['reaction_seconds']):
        return False
    if not opening_shot:
        return True
    if not math.isinf(factor) and factor <= float(
            params['converged_factor']):
        return True
    return laying >= float(params['patience_seconds'])
