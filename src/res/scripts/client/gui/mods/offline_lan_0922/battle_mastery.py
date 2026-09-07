# -*- coding: utf-8 -*-
u"""Award the #1513 mastery badge and Marks of Excellence from retail tables.

Both awards compare one player against every other player who drove the same
vehicle, so Wargaming computes them on its own servers and the client only ever
receives the outcome.  The exact rules are the pinned client's own text in
``res/text/LC_MESSAGES/achievements.mo``:

``markOfMasteryContent``
    Earn more battle XP than 50 / 80 / 95 / 99 percent of the players who drove
    that vehicle in the previous seven days, for classes III / II / I / Ace.

``marksOnGun0_descr`` .. ``marksOnGun2_descr`` and ``marksOnGun_condition``
    Average damage over the last fourteen days above 65 / 85 / 95 percent of
    the other players in that vehicle, computed from the last 100 battles and
    updated after every battle; a mark once earned is never lost; Tiers V-X
    only; standard battles only.

Wargaming's support material adds the one part the client text leaves out: the
average counts damage dealt plus the *largest* of the track, spotting and stun
assist values, not their sum.

The percentile distributions themselves are not derivable from client data and
were never shipped, so ``mastery_catalog`` carries captured retail tables.  The
comparison population is therefore retail players, not this installation's
Bots: the bar is the real one, and how easily an offline battle clears it is a
gameplay question, not a scaling question.

This module is pure data.  It runs on the embedded CPython 2.7.7 runtime.
"""

from __future__ import division

from gui.mods.offline_lan_0922 import mastery_catalog


# Retail awards one, two and three marks at these percentiles.
MARK_PERCENTILES = (65, 85, 95)
# ``marksOnGun_condition``: Tiers V-X only.
MARKS_MIN_TIER = 5
# ``marksOnGun_condition``: only the most recent 100 battles count.  Retail
# implements that as an exponential moving average over 100 battles rather
# than a mean of the last hundred results: the smoothing factor is the
# standard ``2 / (N + 1)``, and the Marks of Excellence mods players use to
# predict their next mark compute the next value as
# ``k * (damage + largest assist) + (1 - k) * movingAvgDamage``.
#
# A vehicle with no history starts at zero, which is why a first battle cannot
# reach a mark however good it was: one battle moves the average by about two
# percent of the gap.  A mean of however many battles exist would hand out
# three marks for one strong opening game.
MOVING_AVERAGE_BATTLES = 100
MOVING_AVERAGE_SMOOTHING = 2.0 / (MOVING_AVERAGE_BATTLES + 1)
# The dossier records cap at these values (``dossiers2.custom.records``).
MAX_MARK_OF_MASTERY = 4
MAX_MARKS_ON_GUN = 3
MAX_DAMAGE_RATING = 100.0
MAX_MOVING_AVG_DAMAGE = 60001
# The tags ``list.xml`` uses for the five vehicle classes, in the order the
# baked fallback groups are keyed by.
VEHICLE_CLASS_TAGS = ('lightTank', 'mediumTank', 'heavyTank', 'AT-SPG', 'SPG')


def vehicle_class(tags):
    """Return the class tag of a #1513 vehicle, or ``''`` when unknown."""
    if not tags:
        return ''
    if not isinstance(tags, (list, tuple, set, frozenset)):
        tags = str(tags).split()
    for tag in VEHICLE_CLASS_TAGS:
        if tag in tags:
            return tag
    return ''


def combined_damage(stats):
    """Return retail combined damage for one battle.

    Damage dealt plus the largest single assist component.  Summing the assist
    components would overstate every battle that both spotted and tracked the
    same enemy, which is exactly what Wargaming's rule excludes.
    """
    if not isinstance(stats, dict):
        return 0
    damage = _whole(stats.get('damage'))
    assists = [_whole(stats.get(name)) for name in
               ('assist_track', 'assist_radio', 'assist_stun')]
    return damage + max(assists)


def vehicle_profile(vehicle_type_cd, tier=None, tags=None):
    """Return ``(tier, class tag)`` for one vehicle compact descriptor.

    The pinned client's own roster is baked beside the retail tables, so the
    Tier V-X gate and the fallback key hold even when the caller has no loaded
    item definitions.  An explicit ``tier`` or ``tags`` overrides the baked
    value, which is what a vehicle outside the baked roster needs.
    """
    baked = mastery_catalog.VEHICLE_PROFILE.get(_whole(vehicle_type_cd))
    baked_tier, baked_class = baked if baked else (0, '')
    resolved_tier = baked_tier if tier is None else _whole(tier)
    resolved_class = baked_class if tags is None else vehicle_class(tags)
    return resolved_tier, resolved_class


def mastery_thresholds(vehicle_type_cd, tier=None, tags=None):
    """Return the four retail base-XP thresholds, or ``None`` when unknown."""
    return _lookup(mastery_catalog.MASTERY_XP,
                   mastery_catalog.MASTERY_XP_FALLBACK,
                   vehicle_type_cd, tier, tags)


def marks_curve(vehicle_type_cd, tier=None, tags=None):
    """Return the retail combined-damage curve, or ``None`` when unknown.

    ``marksOnGun_condition``: nothing below Tier V can carry a mark.
    """
    resolved_tier, unused_class = vehicle_profile(
        vehicle_type_cd, tier=tier, tags=tags)
    if resolved_tier < MARKS_MIN_TIER:
        return None
    return _lookup(mastery_catalog.MARKS_DAMAGE,
                   mastery_catalog.MARKS_DAMAGE_FALLBACK,
                   vehicle_type_cd, tier, tags)


def _lookup(table, fallback, vehicle_type_cd, tier, tags):
    row = table.get(_whole(vehicle_type_cd))
    if row is not None:
        return row
    # A Chinese-server exclusive or a vehicle retail has removed has no row of
    # its own; the baked fallback is the median of the retail rows for the same
    # tier and class, so the bar still comes from retail data.
    return fallback.get(vehicle_profile(
        vehicle_type_cd, tier=tier, tags=tags))


def mark_of_mastery(base_xp, thresholds):
    """Return the mastery class 0-4 one battle's base XP earns."""
    if not thresholds:
        return 0
    earned = 0
    base_xp = _whole(base_xp)
    for index, threshold in enumerate(thresholds):
        if base_xp >= _whole(threshold) > 0:
            earned = index + 1
    return min(earned, MAX_MARK_OF_MASTERY)


def damage_rating(average, curve):
    """Return the percentile of retail players this average beats.

    The baked curve holds combined damage at five-percent steps.  Between two
    steps the percentile is interpolated linearly; below the lowest step it
    falls off linearly to zero.  Both are display smoothing of a captured
    curve, and neither can move a mark: the award reads the exact 65, 85 and 95
    entries.
    """
    if not curve:
        return 0.0
    percentiles = mastery_catalog.MARKS_PERCENTILES
    average = float(max(0, _whole(average)))
    if average <= 0:
        return 0.0
    if average >= curve[-1]:
        return MAX_DAMAGE_RATING
    lower_damage = 0.0
    lower_percentile = 0.0
    for index, damage in enumerate(curve):
        damage = float(damage)
        percentile = float(percentiles[index])
        if average < damage:
            span = damage - lower_damage
            if span <= 0:
                return percentile
            ratio = (average - lower_damage) / span
            return lower_percentile + ratio * (percentile - lower_percentile)
        lower_damage = damage
        lower_percentile = percentile
    return MAX_DAMAGE_RATING


def marks_on_gun(average, curve):
    """Return the mark count 0-3 an average combined damage earns."""
    if not curve:
        return 0
    percentiles = mastery_catalog.MARKS_PERCENTILES
    average = _whole(average)
    earned = 0
    for count, percentile in enumerate(MARK_PERCENTILES):
        try:
            index = percentiles.index(percentile)
        except ValueError:
            continue
        if average >= _whole(curve[index]) > 0:
            earned = count + 1
    return min(earned, MAX_MARKS_ON_GUN)


def next_moving_average(previous, combined):
    """Return the vehicle's updated average combined damage.

    ``k * combined + (1 - k) * previous`` with ``k = 2 / 101``, rounded to the
    whole number the dossier record stores.
    """
    previous = max(0, min(_whole(previous), MAX_MOVING_AVG_DAMAGE))
    combined = max(0, _whole(combined))
    smoothing = MOVING_AVERAGE_SMOOTHING
    average = int(round(smoothing * combined + (1.0 - smoothing) * previous))
    return max(0, min(average, MAX_MOVING_AVG_DAMAGE))


def battle_awards(base_xp, stats, previous_average, vehicle_type_cd,
                  tier=None, tags=None, previous_mastery=0, previous_marks=0):
    """Return this battle's badge outcome and the state it leaves behind.

    ``previous_average`` is the vehicle's stored average combined damage
    before this battle, which is the whole history the average needs: an
    exponential moving average carries its own past, so no per-battle window
    is kept on disk.
    """
    damage = combined_damage(stats)
    average = next_moving_average(previous_average, damage)
    curve = marks_curve(vehicle_type_cd, tier=tier, tags=tags)
    rating = damage_rating(average, curve)
    earned_marks = marks_on_gun(average, curve)
    previous_marks = max(0, min(_whole(previous_marks), MAX_MARKS_ON_GUN))
    previous_mastery = max(
        0, min(_whole(previous_mastery), MAX_MARK_OF_MASTERY))
    mastery = mark_of_mastery(
        base_xp, mastery_thresholds(vehicle_type_cd, tier=tier, tags=tags))
    return {
        # The class this battle earned; the results window shows it and picks
        # the record icon by comparing it against the previous best.
        'markOfMastery': mastery,
        'prevMarkOfMastery': previous_mastery,
        'bestMarkOfMastery': max(mastery, previous_mastery),
        # A mark is never lost when the average drops.
        'marksOnGun': max(earned_marks, previous_marks),
        'prevMarksOnGun': previous_marks,
        'damageRating': rating,
        'movingAvgDamage': average,
        'combinedDamage': damage,
    }


def _whole(value):
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
