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

A Bot's competence is one continuous ``rating`` in [0, 1], not a tier.  The
four reviewed tier bundles survive as the anchors that rating interpolates
between, so every existing calibration is still exactly itself at its own
anchor; the tier names remain the wire, launcher-profile and log vocabulary.

The same rating is also the probability that a Bot does the tactically right
thing on one occasion, which is what ``capability_allowed`` answers.  That
half is consumed by the server planner, not here: this module owns the number
and the draw, so worker, server and launcher reach the same answer from
identity alone.

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

# Where each tier name sits on the continuous axis.  Index-aligned with
# SKILL_TIERS: the names are a label for a rating, not a separate ladder.
RATING_ANCHORS = (0.00, 0.34, 0.67, 1.00)
DEFAULT_RATING = RATING_ANCHORS[SKILL_TIERS.index(DEFAULT_SKILL)]

# Crew levels this project has actually seen #1513 train.  A level the client
# refuses makes ``_bot_default_crew_factors`` fall back to the full default
# crew, which moves a weak Bot the wrong way, so the interpolated level snaps
# to a proven one instead of trusting an unverified value.
PROVEN_CREW_LEVELS = (75, 90, 100)

# Tactical capabilities the rating gates.  The rating is the probability that
# a Bot does the right thing on one occasion; the caller decides how wide an
# occasion is by choosing what identity it seeds the draw with.  Two of them
# are latched to the round and slot on purpose, so a roster holds Bots with a
# recognisable habit ("that one never angles") instead of only Bots that
# hesitate uniformly.
CAPABILITY_WEAK_SPOT = 'weak_spot'
CAPABILITY_SHELL_CHOICE = 'shell_choice'
CAPABILITY_TARGET_PRIORITY = 'target_priority'
CAPABILITY_URGENT_COVER = 'urgent_cover'
CAPABILITY_TACTICAL_COVER = 'tactical_cover'
CAPABILITY_COVER_PEEK = 'cover_peek'
CAPABILITY_CROSSFIRE = 'crossfire'
CAPABILITY_WITHDRAW = 'withdraw'
CAPABILITY_HULL_ANGLING = 'hull_angling'
CAPABILITY_FLANK = 'flank'

CAPABILITIES = (
    CAPABILITY_WEAK_SPOT, CAPABILITY_SHELL_CHOICE,
    CAPABILITY_TARGET_PRIORITY, CAPABILITY_URGENT_COVER,
    CAPABILITY_TACTICAL_COVER, CAPABILITY_COVER_PEEK,
    CAPABILITY_CROSSFIRE, CAPABILITY_WITHDRAW,
    CAPABILITY_HULL_ANGLING, CAPABILITY_FLANK)

# Rolled once for the whole round from round and slot identity.
LATCHED_CAPABILITIES = frozenset((
    CAPABILITY_HULL_ANGLING, CAPABILITY_FLANK))

# Maps the rating onto the capability probability.  1.0 is the reviewed
# product choice: the gunnery half and the tactical half read the same number.
# It exists because those two halves are calibrated on different scales, and
# one exponent is the whole correction if playtesting says the tactical half
# bites harder than the gunnery half.
CAPABILITY_EXPONENT = 1.0

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

# The four reviewed bundles are the anchors of the continuous rating axis.
# ``rating_parameters`` interpolates between them, so an anchor rating still
# returns exactly the bundle below it and no existing calibration moves.
_PARAMETERS = {
    SKILL_ROOKIE: {
        # #1513 crew level, fed to generateDefaultCrew.  The crew level also
        # shortens view range and slows reload, so only the two weaker tiers
        # spend it at all and the top two share a full crew: this is a
        # gunnery setting, not a spotting setting.
        'crew_level': 75,
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
        'crew_level': 90,
        'reaction_seconds': 0.75,
        'patience_seconds': 1.10,
        'converged_factor': 1.80,
        'aim_bias_factor': 0.85,
        'lead_error': 0.22,
    },
    SKILL_VETERAN: {
        'crew_level': 100,
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

# Each preset is one roster's rating distribution, written as the five
# control points of a piecewise-linear inverse CDF at u = 0, .25, .5, .75, 1.
# One ``random()`` draw per slot indexes it, so Python 2.7 and Python 3 agree
# bit for bit without depending on a library distribution's implementation.
#
# The product specification is the mean of each row: .10 / .30 / .60 / .80 /
# 1.00.  ``_mode_mean`` recomputes it and a test holds the table to it.  The
# spans are deliberate: ``mixed`` is the widest, so one pub roster really does
# hold a hopeless Bot and a dangerous one, while the ends stay predictable.
_MODE_RATING_POINTS = {
    SKILL_MODE_EASY: (0.00, 0.03, 0.08, 0.14, 0.30),
    SKILL_MODE_RELAXED: (0.05, 0.16, 0.28, 0.40, 0.67),
    SKILL_MODE_MIXED: (0.12, 0.42, 0.62, 0.80, 1.00),
    SKILL_MODE_HARD: (0.52, 0.70, 0.82, 0.92, 1.00),
    SKILL_MODE_BRUTAL: (1.00, 1.00, 1.00, 1.00, 1.00),
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


def normalize_rating(value):
    """Return one usable competence rating in [0, 1].

    An unusable value becomes the default rather than raising: a Bot with a
    corrupt rating still has to fight this round.
    """
    try:
        rating = float(value)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_RATING
    if math.isnan(rating) or math.isinf(rating):
        return DEFAULT_RATING
    return max(0.0, min(1.0, rating))


def rating_for_skill(skill):
    """Return the rating one tier name stands for."""
    return RATING_ANCHORS[SKILL_TIERS.index(normalize_skill(skill))]


def skill_for_rating(rating):
    """Return the nearest tier name, for the wire, a log line or a panel.

    Ties round down.  A label may understate a Bot; it must never claim a
    competence the Bot does not have.
    """
    rating = normalize_rating(rating)
    best_index = 0
    best_distance = None
    for index, anchor in enumerate(RATING_ANCHORS):
        distance = abs(rating - anchor)
        if best_distance is None or distance < best_distance - 1e-12:
            best_index, best_distance = index, distance
    return SKILL_TIERS[best_index]


def _interpolate(points, position):
    """Return the piecewise-linear value of evenly spaced control points."""
    span = len(points) - 1
    scaled = max(0.0, min(1.0, position)) * span
    index = int(scaled)
    if index >= span:
        return float(points[-1])
    fraction = scaled - index
    lower = float(points[index])
    return lower + (float(points[index + 1]) - lower) * fraction


def _anchor_segment(rating):
    """Return the (index, fraction) of the anchor segment holding a rating."""
    last = len(RATING_ANCHORS) - 2
    for index in range(last + 1):
        lower = RATING_ANCHORS[index]
        upper = RATING_ANCHORS[index + 1]
        if rating <= upper or index == last:
            if upper <= lower:
                return index, 0.0
            return index, max(0.0, min(1.0, (rating - lower) /
                                       (upper - lower)))
    return last, 1.0


def _anchor_value(points, rating):
    """Return one reviewed row's value at a rating, exact on every anchor.

    The anchors are not evenly spaced, and ``a + (b - a) * 1.0`` is not
    reliably ``b`` in binary floating point.  Returning the endpoint verbatim
    is what keeps an anchor rating identical to the tier it stands for, which
    is the whole reason the tier bundles survived as anchors.
    """
    index, fraction = _anchor_segment(rating)
    if fraction <= 0.0:
        return float(points[index])
    if fraction >= 1.0:
        return float(points[index + 1])
    lower = float(points[index])
    return lower + (float(points[index + 1]) - lower) * fraction


def rating_parameters(rating):
    """Return one Bot's gunner bundle, interpolated between the anchors.

    The bundle is built per call, so a caller may not rely on identity and
    cannot mutate a shared row.  ``crew_level`` is snapped to a level #1513
    is known to train instead of trusting an interpolated one.
    """
    rating = normalize_rating(rating)
    rows = [_PARAMETERS[skill] for skill in SKILL_TIERS]
    result = {}
    for name in ('reaction_seconds', 'patience_seconds', 'converged_factor',
                 'aim_bias_factor', 'lead_error'):
        result[name] = _anchor_value([row[name] for row in rows], rating)
    level = _anchor_value([row['crew_level'] for row in rows], rating)
    result['crew_level'] = min(
        PROVEN_CREW_LEVELS, key=lambda proven: (abs(proven - level), proven))
    return result


def skill_parameters(skill):
    """Return the parameter bundle at one tier's own anchor."""
    return rating_parameters(rating_for_skill(skill))


def rating_crew_level(rating):
    """Return the #1513 crew level one rating trains its Bot to."""
    return int(rating_parameters(rating)['crew_level'])


def crew_level(skill):
    """Return the #1513 crew level one tier trains its Bots to."""
    return rating_crew_level(rating_for_skill(skill))


def _mode_mean(mode):
    """Return one preset's mean rating, the number the presets specify."""
    points = _MODE_RATING_POINTS[normalize_skill_mode(mode)]
    span = len(points) - 1
    return sum((points[index] + points[index + 1]) * 0.5 / span
               for index in range(span))


def resolve_rating(mode, round_id, team, slot):
    """Pick one roster slot's rating from a preset, without shared state.

    The draw depends only on the round and the slot, so the worker, the
    server and any UI reach the same answer for the same Bot without the
    rating having to travel between them first.
    """
    mode = normalize_skill_mode(mode)
    roll = random.Random(
        _stable_seed('bot-skill-rating-v1', round_id, mode, team,
                     slot)).random()
    return _interpolate(_MODE_RATING_POINTS[mode], roll)


def resolve_skill(mode, round_id, team, slot):
    """Return the tier name labelling one roster slot's drawn rating."""
    return skill_for_rating(resolve_rating(mode, round_id, team, slot))


def capability_allowed(rating, capability, *occasion):
    """Return whether one Bot does the tactically right thing this time.

    ``occasion`` is the identity of the single decision being made, and it is
    what decides how long an answer lasts.  Seeding it with a target lease,
    an aiming epoch or a cover-manoeuvre instance gives a Bot that hesitates;
    seeding it with the round and slot latches the answer for the battle.
    Nothing is stored either way, so the planner may recompute an order every
    tick and an authority takeover cannot make a Bot change its mind.

    A rating of 0 never succeeds and a rating of 1 always does, so the ends
    of a preset stay honest.
    """
    rating = normalize_rating(rating)
    if rating <= 0.0:
        return False
    probability = rating ** CAPABILITY_EXPONENT
    if probability >= 1.0:
        return True
    parts = ('bot-capability-v1', capability) + occasion
    return random.Random(_stable_seed(*parts)).random() < probability


def bias_epoch(hold_seconds):
    """Return the aiming epoch index for one continuous target hold."""
    try:
        held = float(hold_seconds)
    except (TypeError, ValueError, OverflowError):
        return 0
    if math.isnan(held) or math.isinf(held) or held <= 0.0:
        return 0
    return int(held / AIM_BIAS_SECONDS)


def engagement_error(rating, round_id, bot_id, target_key, epoch):
    """Return one gunner's frozen error for one aiming epoch.

    ``radius`` is a truncated half-normal in [0, 1]; the caller scales it by
    the Bot's bias factor and the gun's own dispersion.  ``lead_scale``
    multiplies the target velocity fed to the intercept solve, so a weak
    gunner under- or over-leads a moving target for the whole epoch instead
    of correcting between shots.
    """
    params = rating_parameters(rating)
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


def aim_offset_metres(rating, error, dispersion_angle, distance):
    """Return the (lateral, vertical) aim-point error for one shot, in metres.

    The caller maps these onto the line of sight.  The error is angular so it
    grows with range exactly like the aiming circle it is measured against.
    """
    params = rating_parameters(rating)
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


def may_fire(rating, hold_seconds, laying_seconds, dispersion_factor,
             opening_shot=True):
    """Return whether this gunner has reacted and finished laying.

    ``hold_seconds`` is the continuous hold on the current target and drives
    the reaction delay.  ``laying_seconds`` runs from that acquisition and
    drives the aiming patience.  ``dispersion_factor`` is the live circle as
    a multiple of the fully-aimed circle, so 1.0 is fully aimed.

    Only the opening shot of an engagement waits for the circle.  Every gun
    blooms after firing, so a better gunner - which accepts a tighter circle
    and waits longer for it - would otherwise fire a fast or clip gun less
    often than a worse one holding the same gun, and the whole ladder would
    invert.  After the opening shot the gun's own reload owns the cadence;
    the aim-point bias and the lead error still separate Bots on every round.
    """
    params = rating_parameters(rating)
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
