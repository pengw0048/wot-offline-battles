"""Report a hit the way the cell reports it.

The ribbons, the damage numbers and the hit-direction arrow all come
from client methods the cell calls after it resolves a shot. This module
calls the same ones with the values this runtime resolved.
"""
from __future__ import absolute_import

import math

# BattleFeedbackCommon.BATTLE_EVENT_TYPE, frozen so a report needs no
# import inside the tick.
EVENT_CRIT = 6
EVENT_DAMAGE = 7
EVENT_KILL = 8
EVENT_RECEIVED_CRIT = 9
EVENT_RECEIVED_DAMAGE = 10
ATTACK_REASON_SHOT = 0


def _pack_damage(damage, attack_reason=ATTACK_REASON_SHOT):
    from BattleFeedbackCommon import BATTLE_EVENT_TYPE
    return BATTLE_EVENT_TYPE.packDamage(int(damage), attack_reason)


def _pack_crits(count, attack_reason=ATTACK_REASON_SHOT):
    from BattleFeedbackCommon import BATTLE_EVENT_TYPE
    return BATTLE_EVENT_TYPE.packCrits(int(count), attack_reason)


def dealt_events(target_id, damage, crit_count, killed):
    """The battle events one resolved shot produces for the shooter."""
    events = []
    if damage > 0:
        events.append({'eventType': EVENT_DAMAGE, 'targetID': target_id,
                       'count': int(damage),
                       'details': _pack_damage(damage)})
    if crit_count > 0:
        events.append({'eventType': EVENT_CRIT, 'targetID': target_id,
                       'count': int(crit_count),
                       'details': _pack_crits(crit_count)})
    if killed:
        events.append({'eventType': EVENT_KILL, 'targetID': target_id,
                       'count': 1, 'details': 0})
    return events


def received_events(attacker_id, damage, crit_count):
    """The battle events one resolved shot produces for the target."""
    events = []
    if damage > 0:
        events.append({'eventType': EVENT_RECEIVED_DAMAGE,
                       'targetID': attacker_id, 'count': int(damage),
                       'details': _pack_damage(damage)})
    if crit_count > 0:
        events.append({'eventType': EVENT_RECEIVED_CRIT,
                       'targetID': attacker_id, 'count': int(crit_count),
                       'details': _pack_crits(crit_count)})
    return events


def hit_direction_yaw(target_position, attacker_position):
    """Yaw from the target to whoever shot it, which the arrow points at."""
    return math.atan2(attacker_position[0] - target_position[0],
                      attacker_position[2] - target_position[2])


def publish_dealt(avatar, target_id, damage, crit_count, killed):
    events = dealt_events(target_id, damage, crit_count, killed)
    if events:
        avatar.onBattleEvents(events)
    return events


def publish_received(avatar, attacker_id, damage, crit_count, direction_yaw,
                     is_high_explosive=False, target_id=None):
    """Damage numbers, and the arrow that says where it came from."""
    events = received_events(attacker_id, damage, crit_count)
    if events:
        avatar.onBattleEvents(events)
    avatar.showOwnVehicleHitDirection(
        direction_yaw, attacker_id, int(damage), int(crit_count), False,
        bool(is_high_explosive),
        avatar.playerVehicleID if target_id is None else target_id,
        ATTACK_REASON_SHOT)
    return events
