# -*- coding: utf-8 -*-
"""Short friendly crossing leases; physical contact remains the motion owner."""

import math


PREDICTION_SECONDS = 1.0
YIELD_SECONDS = 1.5
HEAD_ON_OFFSET = 0.42
_EPSILON = 1.0e-9


def _dot(first, second):
    return first[0] * second[0] + first[1] * second[1]


def _cross(first, second):
    return first[0] * second[1] - first[1] * second[0]


def _position(body):
    point = body['position']
    return point[0], point[2]


def _velocity(body):
    value = body.get('velocity', (0.0, 0.0, 0.0))
    return value[0], value[2]


def _axes(body):
    sine, cosine = math.sin(body['yaw']), math.cos(body['yaw'])
    return (cosine, -sine), (sine, cosine)


def _radius(body, axis):
    side, forward = _axes(body)
    shape = body.get('shape')
    width, length = (shape[:2] if shape is not None else
                     (body['half_width'], body['half_length']))
    return abs(_dot(side, axis)) * width + abs(_dot(forward, axis)) * length


def _travel(body):
    velocity = _velocity(body)
    speed = math.hypot(*velocity)
    if speed > _EPSILON:
        return (velocity[0] / speed, velocity[1] / speed), speed
    return _axes(body)[1], 0.0


def _same_level(first, second):
    # The production collision shape ends in lower_y/upper_y, not XZ offsets.
    first_shape, second_shape = first.get('shape'), second.get('shape')
    if first_shape is None or second_shape is None:
        return True
    first_y, second_y = first['position'][1], second['position'][1]
    return min(first_y + first_shape[3], second_y + second_shape[3]) > max(
        first_y + first_shape[2], second_y + second_shape[2])


def _contact_time(first, second):
    """Intersect exact OBB intervals under their actual relative velocity."""
    first_pos, second_pos = _position(first), _position(second)
    first_vel, second_vel = _velocity(first), _velocity(second)
    delta = (second_pos[0] - first_pos[0], second_pos[1] - first_pos[1])
    relative = (second_vel[0] - first_vel[0], second_vel[1] - first_vel[1])
    enter, leave = 0.0, PREDICTION_SECONDS
    for axis in _axes(first) + _axes(second):
        distance, rate = _dot(delta, axis), _dot(relative, axis)
        radius = _radius(first, axis) + _radius(second, axis)
        if abs(rate) <= _EPSILON:
            if abs(distance) >= radius:
                return None
            continue
        start, end = (-radius - distance) / rate, (radius - distance) / rate
        enter, leave = max(enter, min(start, end)), min(leave, max(start, end))
        if enter >= leave:
            return None
    return enter


def _intersection(first, second, first_axis, second_axis):
    denominator = _cross(first_axis, second_axis)
    if abs(denominator) <= _EPSILON:
        return None
    first_pos, second_pos = _position(first), _position(second)
    delta = (second_pos[0] - first_pos[0], second_pos[1] - first_pos[1])
    distance = _cross(delta, second_axis) / denominator
    return (first_pos[0] + first_axis[0] * distance,
            first_pos[1] + first_axis[1] * distance)


def _arrival(body, axis, speed, gate):
    position = _position(body)
    distance = _dot((gate[0] - position[0], gate[1] - position[1]), axis)
    front_distance = distance - _radius(body, axis)
    if front_distance <= 0.0:
        return (0, front_distance, body['id'])
    return (1, front_distance / speed if speed > _EPSILON else float('inf'),
            body['id'])


class TrafficCoordinator(object):
    """Coordinate ordinary forward route commands from frozen body snapshots.

    Bodies carry id/team/alive, position, yaw, velocity and actual half_width /
    half_length or the production collision shape. No body is enlarged. The
    returned command never moves an entity or replaces physical collision.
    """

    def __init__(self):
        self._pairs = {}

    def forget(self, bot_id):
        for pair in list(self._pairs):
            if bot_id in pair:
                del self._pairs[pair]

    def _begin(self, first, second, now):
        # A neighbour reversing out of a blockage is not an oncoming route
        # convoy. Its existing recovery and physical contacts retain control.
        if any(_dot(_velocity(body), _axes(body)[1]) < -_EPSILON
               for body in (first, second)):
            return None
        first_axis, first_speed = _travel(first)
        second_axis, second_speed = _travel(second)
        alignment = _dot(first_axis, second_axis)
        # Same-direction neighbours can follow or touch without being diverted.
        if alignment >= math.cos(0.35):
            return None
        contact_time = _contact_time(first, second)
        if alignment <= -math.cos(0.60):
            first_pos, second_pos = _position(first), _position(second)
            delta = (second_pos[0] - first_pos[0], second_pos[1] - first_pos[1])
            ahead = _dot(delta, first_axis)
            second_ahead = -_dot(delta, second_axis)
            side = (first_axis[1], -first_axis[0])
            lateral = abs(_dot(delta, side))
            width = _radius(first, side) + _radius(second, side)
            gap = ahead - _radius(first, first_axis) - _radius(second, first_axis)
            if (ahead <= 0.0 or second_ahead <= 0.0 or lateral >= width or
                    (contact_time is None and gap > width * 0.5)):
                return None
            return {'mode': 'head_on', 'axis': first_axis,
                    'targets': {first['id']: first['yaw'] + HEAD_ON_OFFSET,
                                second['id']: second['yaw'] + HEAD_ON_OFFSET}}
        if contact_time is None:
            return None
        gate = _intersection(first, second, first_axis, second_axis)
        if gate is None:
            return None
        ranked = sorted(((_arrival(first, first_axis, first_speed, gate),
                          first, first_axis, second),
                         (_arrival(second, second_axis, second_speed, gate),
                          second, second_axis, first)), key=lambda value: value[0])
        unused_rank, winner, axis, loser = ranked[0]
        return {'mode': 'yield', 'winner': winner['id'], 'axis': axis,
                'clear_after': _dot(gate, axis) + _radius(winner, axis) +
                               _radius(loser, axis),
                'until': now + YIELD_SECONDS}

    @staticmethod
    def _cleared(lease, first, second):
        if lease['mode'] == 'yield':
            winner = first if first['id'] == lease['winner'] else second
            return _dot(_position(winner), lease['axis']) > lease['clear_after']
        delta = (_position(second)[0] - _position(first)[0],
                 _position(second)[1] - _position(first)[1])
        axis = lease['axis']
        side = (axis[1], -axis[0])
        # Both right-side departures are complete once their real lateral
        # footprints separate, or their centres have passed one another.
        return (_dot(delta, axis) <= 0.0 or
                abs(_dot(delta, side)) >= _radius(first, side) + _radius(second, side))

    def adjust(self, bot_id, body, command, neighbours, now, direction_clear):
        result = dict(command)
        if (not body.get('alive', True) or
                command.get('recovery_mode', 'drive') != 'drive' or
                command.get('combat_mode', 'route') not in ('route', 'advance') or
                command.get('throttle', 0.0) <= 0.0 or
                _dot(_velocity(body), _axes(body)[1]) < -_EPSILON):
            return result
        peers = dict((peer['id'], peer) for peer in neighbours
                     if peer['id'] != bot_id and peer.get('alive', True) and
                     peer.get('team') == body.get('team') and _same_level(body, peer))
        for pair in list(self._pairs):
            if bot_id in pair and any(actor != bot_id and actor not in peers
                                     for actor in pair):
                del self._pairs[pair]
        for peer_id in sorted(peers):
            peer = peers[peer_id]
            first, second = (body, peer) if bot_id < peer_id else (peer, body)
            pair = (first['id'], second['id'])
            lease = self._pairs.get(pair)
            if lease is not None and self._cleared(lease, first, second):
                del self._pairs[pair]
                lease = None
            if lease is None:
                lease = self._begin(first, second, now)
                if lease is None:
                    continue
                self._pairs[pair] = lease
            if lease['mode'] == 'yield':
                if bot_id != lease['winner'] and now < lease['until']:
                    result.update(throttle=0.0, turn=0.0, traffic_mode='yield')
                continue
            if result.get('traffic_mode') == 'yield':
                continue
            target = lease['targets'][bot_id]
            try:
                clear = bool(direction_clear(target))
            except Exception:
                clear = False
            if not clear:
                result.update(throttle=0.0, turn=0.0, target_yaw=body['yaw'],
                              traffic_mode='head_on_blocked')
                continue
            delta = (target - body['yaw'] + math.pi) % (2.0 * math.pi) - math.pi
            result.update(turn=max(-1.0, min(1.0, delta / 0.58)),
                          target_yaw=target, traffic_mode='head_on')
        return result
