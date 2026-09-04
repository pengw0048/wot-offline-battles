"""Plain-data adapter between a battle runtime and the pure bot planner.

The adapter deliberately does not inspect BigWorld entities.  A caller sends
JSON-like dictionaries and receives a JSON-safe order: a route/goal, a local
movement command, and optional fire intent.  The caller remains responsible
for visibility, collision probes, and applying commands to any client entity.
"""

import math

from gui.mods.offline_lan_0922.ai.driver import (
    LocalDriver, WAYPOINT_ARRIVAL_RADIUS,
)
from gui.mods.offline_lan_0922.ai.planner import BattleDirector


def _position(value, fallback=(0.0, 0.0, 0.0)):
    if isinstance(value, dict):
        value = (value.get('x'), value.get('y'), value.get('z'))
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError, IndexError):
        return fallback


def _contact(value):
    if not isinstance(value, dict):
        return None
    result = dict(value)
    result['position'] = _position(value.get('position'))
    result['visible'] = bool(value.get('visible', False))
    return result


class BotAdapter(object):
    """Owns pure planner/driver state for one map and battle seed."""

    def __init__(self, map_name, battle_seed, bases=None, bounds=None,
                 navigation_target=None, baked_routes=None):
        self.director = BattleDirector(map_name, battle_seed, bases, bounds,
                                      baked_routes=baked_routes)
        self.driver = LocalDriver()
        self.navigation_target = navigation_target

    def register(self, bot_id, team, descriptor, display_name='Bot'):
        return self.director.register(bot_id, team, descriptor, display_name)

    def forget(self, bot_id):
        self.driver.forget(bot_id)
        self.director.agents.pop(int(bot_id), None)

    def decide(self, state, direction_clear):
        """Return a deterministic, serializable command for one bot.

        ``state`` needs ``id``, ``position``, ``yaw``, ``speed``, ``dt``,
        ``now`` and optionally ``health``, ``max_health``, ``contacts`` and
        ``neighbours``.  ``direction_clear(yaw)`` is the sole runtime probe.
        """
        state = state if isinstance(state, dict) else {}
        bot_id = int(state.get('id', 0))
        position = _position(state.get('position'))
        contacts = [_contact(item) for item in state.get('contacts', ())]
        contacts = [item for item in contacts if item is not None]
        team = self.director.agents[bot_id]['team']
        now = float(state.get('now', 0.0))
        for contact in contacts:
            self.director.update_contact(
                team, contact.get('id', 0), contact.get('team', 0),
                contact['position'], contact.get('health', 1.0),
                contact.get('max_health', 1.0), contact.get('class_tag'),
                contact['visible'], now, contact.get('armor', 0.0),
                contact.get('speed', 0.0))
        strategic = self.director.order_for(
            bot_id, position, float(state.get('yaw', 0.0)),
            state.get('health', 1.0), state.get('max_health', 1.0), now)
        return self._drive_order(
            bot_id, state, position, strategic, direction_clear)

    def decide_with_order(self, state, strategic, direction_clear):
        """Apply a server macro order through the same local terrain driver."""
        state = state if isinstance(state, dict) else {}
        strategic = strategic if isinstance(strategic, dict) else {}
        bot_id = int(state.get('id', 0))
        position = _position(state.get('position'))
        return self._drive_order(
            bot_id, state, position, strategic, direction_clear)

    def _drive_order(self, bot_id, state, position, strategic,
                     direction_clear):
        aim_position = strategic.get('aim_position')
        move_position = strategic.get('move_position')
        face_position = strategic.get('face_position')
        if aim_position is not None:
            aim_position = _position(aim_position, position)
        if move_position is not None:
            move_position = _position(move_position, position)
        if face_position is not None:
            face_position = _position(face_position, position)
        aim_position, move_position, face_position, unused_stop = (
            self.driver.resolve_order_positions(
                position, aim_position, move_position, face_position))
        target = move_position
        if callable(self.navigation_target):
            target = _position(self.navigation_target(
                bot_id, position, target, strategic, state), target)
        stop_at_target = bool(state.get(
            'navigation_stop_at_target',
            strategic.get('combat_mode') not in ('route', 'advance')))
        throttle_override = strategic.get('throttle_override')
        movement_intent = not (
            throttle_override is not None and
            float(throttle_override) <= 0.0)
        requested_dx = float(move_position[0]) - float(position[0])
        requested_dz = float(move_position[2]) - float(position[2])
        target_dx = float(target[0]) - float(position[0])
        target_dz = float(target[2]) - float(position[2])
        navigation_wait = bool(
            movement_intent and
            requested_dx * requested_dx + requested_dz * requested_dz > 225.0 and
            target_dx * target_dx + target_dz * target_dz <=
            WAYPOINT_ARRIVAL_RADIUS * WAYPOINT_ARRIVAL_RADIUS)
        if navigation_wait:
            # TerrainNavigator returned the current pose because a resumable A*
            # job is still pending. This is a planner wait, not route arrival and
            # not physical evidence that should advance LocalDriver recovery.
            local = {
                'throttle': 0.0,
                'turn': 0.0,
                'target_yaw': float(state.get('yaw', 0.0)),
                'recovery_mode': 'nav_wait',
            }
        else:
            local = self.driver.drive(
                bot_id, position, float(state.get('yaw', 0.0)),
                float(state.get('speed', 0.0)), float(state.get('dt', 0.0)),
                target, state.get('neighbours', ()), direction_clear,
                velocity=state.get('velocity'),
                half_length=float(state.get('half_length', 3.5)),
                half_width=float(state.get('half_width', 1.7)),
                movement_intent=movement_intent,
                stopping_distance=state.get('stopping_distance'),
                stop_at_target=stop_at_target,
                decision_horizon=float(state.get('decision_horizon', 0.0)))
        # Preserve the mature face-position intent which is separate from the
        # gun target.  At a route/cover stop it gives armoured turreted tanks
        # their stable 12-30 degree hull angle while the turret keeps tracking
        # ``aim_position``.  Local recovery directions still outrank it.
        recovery_mode = local.get('recovery_mode', 'drive')
        target_yaw = float(local['target_yaw'])
        turn = float(local['turn'])
        dx = float(face_position[0]) - float(position[0])
        dz = float(face_position[2]) - float(position[2])
        if (recovery_mode in ('arrived', 'nav_wait') and
                dx * dx + dz * dz > 0.01):
            target_yaw = math.atan2(dx, dz)
            difference = target_yaw - float(state.get('yaw', 0.0))
            while difference > math.pi:
                difference -= 2.0 * math.pi
            while difference < -math.pi:
                difference += 2.0 * math.pi
            turn = max(-1.0, min(1.0, difference / 0.58))
        result = {
            'bot_id': bot_id,
            'target_id': strategic.get('target_id'),
            'aim_position': aim_position,
            'face_position': face_position,
            'fire_range': float(strategic.get('fire_range', 0.0)),
            'move_position': target,
            'combat_mode': strategic.get('combat_mode', 'route'),
            'fire_allowed': bool(strategic.get('fire_allowed', False)),
            'shell_index': int(strategic.get('shell_index', 0)),
            'throttle': float(local['throttle']),
            'turn': turn,
            'target_yaw': target_yaw,
            'recovery_mode': recovery_mode,
            'movement_intent': movement_intent,
        }
        if strategic.get('hull_angle_degrees') is not None:
            result['hull_angle_degrees'] = float(
                strategic.get('hull_angle_degrees'))
        difference = target_yaw - float(state.get('yaw', 0.0))
        while difference > math.pi:
            difference -= 2.0 * math.pi
        while difference < -math.pi:
            difference += 2.0 * math.pi
        if (throttle_override is not None and
                recovery_mode in ('drive', 'arrived') and
                abs(difference) < 0.65):
            result['throttle'] = max(
                -1.0, min(1.0, float(throttle_override)))
        return result
