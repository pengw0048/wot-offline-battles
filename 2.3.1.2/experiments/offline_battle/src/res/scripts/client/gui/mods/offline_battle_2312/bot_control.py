"""Drive the enemies with the copied 0.9.22 planner and driver.

The adapter owns the thinking: it takes a plain state dict per bot and
returns a command with throttle, turn, an aim position and whether to
fire. This module is only the 2.3.1.2 side of that contract, following
the mature caller: the player rides in ``neighbours``, friendly traffic
throttles the follower, and a blocked travel direction is remembered by
the driver so its stuck recovery can run.
"""
from __future__ import absolute_import

import math

from gui.mods.offline_battle_2312 import critical_damage
from gui.mods.offline_battle_2312 import engine_shim
from gui.mods.offline_battle_2312 import entity_setup
from gui.mods.offline_battle_2312 import motion
from gui.mods.offline_battle_2312 import suspension
from gui.mods.offline_battle_2312 import track_visuals
from gui.mods.offline_battle_2312 import world_collision
from gui.mods.offline_battle_2312.ai.adapter import BotAdapter

TICK_SECONDS = 0.1
DECISION_SECONDS = 0.0975
MAX_FRAME_SECONDS = 0.35
BATTLE_SEED = 20260816
HUMAN_TARGET_ID_BASE = 1000000


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _state_position(state):
    position = state.get('position') or (0.0, 0.0, 0.0)
    return (_number(position[0]), _number(position[1]),
            _number(position[2]))


def _angle_delta(target, current):
    value = _number(target) - _number(current)
    while value > math.pi:
        value -= math.pi * 2.0
    while value < -math.pi:
        value += math.pi * 2.0
    return value


def traffic_throttle(source, command, neighbours):
    """Return ``(throttle, waiting)`` for nearby friendly traffic.

    Same-lane followers always respect the vehicle ahead. At a crossing or
    merge, the lower bot id has deterministic right of way; every bot yields
    to a human. This breaks the symmetric stop/turn/reverse loop without
    changing route selection or the physical tank-contact response.
    """
    throttle = max(-1.0, min(1.0, _number(command.get('throttle'))))
    if throttle <= 0.01:
        return throttle, False
    position = _state_position(source)
    source_team = int(_number(source.get('team')))
    if source_team not in (1, 2):
        return throttle, False
    yaw = _number(source.get('yaw'))
    own_speed = abs(_number(source.get('speed')))
    own_length = max(0.5, _number(source.get('half_length'), 3.5))
    own_width = max(0.3, _number(source.get('half_width'), 1.7))
    sine, cosine = math.sin(yaw), math.cos(yaw)
    target_yaw = _number(command.get('target_yaw'), yaw)
    target_sine = math.sin(target_yaw)
    target_cosine = math.cos(target_yaw)
    nearest = None
    for raw in neighbours or ():
        if not isinstance(raw, dict):
            continue
        if int(_number(raw.get('team'))) != source_team:
            continue
        other = raw.get('position') or raw.get('pos')
        if other is None:
            continue
        try:
            dx = float(other[0]) - position[0]
            dz = float(other[2]) - position[2]
            if abs(float(other[1]) - position[1]) > 5.0:
                continue
        except (TypeError, ValueError, IndexError):
            continue
        forward = dx * sine + dz * cosine
        lateral = abs(dx * cosine - dz * sine)
        if abs(_angle_delta(target_yaw, yaw)) > 0.20:
            target_forward = dx * target_sine + dz * target_cosine
            target_lateral = abs(dx * target_cosine - dz * target_sine)
            if target_forward > 0.0 and target_lateral < lateral:
                forward = target_forward
                lateral = target_lateral
        other_length = max(0.5, _number(raw.get('half_length'), 3.5))
        other_width = max(0.3, _number(raw.get('half_width'), 1.7))
        corridor_yaw = yaw
        if abs(_angle_delta(target_yaw, yaw)) > 0.20:
            corridor_yaw = target_yaw
        other_yaw = _number(raw.get('yaw'), corridor_yaw)
        same_direction = abs(_angle_delta(other_yaw, corridor_yaw)) < 0.65
        if (not same_direction and raw.get('id') is not None and
                source.get('id') is not None):
            try:
                other_id = int(raw.get('id'))
                if (other_id < HUMAN_TARGET_ID_BASE and
                        other_id > int(source.get('id'))):
                    continue
            except (TypeError, ValueError):
                pass
        clearance = forward - own_length - other_length
        if (forward <= 0.0 or clearance > 9.0 or
                lateral > own_width + other_width + 0.75):
            continue
        other_velocity = raw.get('velocity') or raw.get('vel')
        try:
            other_vx = float(other_velocity[0])
            other_vz = float(other_velocity[2])
        except (TypeError, ValueError, IndexError):
            other_vx = 0.0
            other_vz = 0.0
        corridor_sine = math.sin(corridor_yaw)
        corridor_cosine = math.cos(corridor_yaw)
        other_forward = max(
            0.0, other_vx * corridor_sine + other_vz * corridor_cosine)
        candidate = (clearance, other_forward)
        if nearest is None or candidate[0] < nearest[0]:
            nearest = candidate
    if nearest is None:
        return throttle, False
    clearance, leader_speed = nearest
    safe_clearance = max(1.5, own_speed * 1.0)
    if clearance <= safe_clearance:
        return 0.0, True
    if own_speed > leader_speed + 0.5:
        limited = min(throttle, max(0.0, min(
            1.0, (clearance - safe_clearance) / 4.0)))
        return limited, limited + 1e-9 < throttle
    return throttle, False


class BotBody(object):
    """One enemy's pose and speed, integrated by the copied motion law."""

    def __init__(self, vehicle_id, pose, descriptor, team):
        self.id = vehicle_id
        self.x, self.y, self.z, self.yaw = pose
        self.team = int(team)
        self.speed = 0.0
        self.omega = 0.0
        self.throttle = 0.0
        self.turn = 0.0
        self.descriptor = descriptor
        self.params = motion.derive_params(descriptor)
        self.length, self.width = suspension.hull_span(descriptor)
        self.max_health = float(getattr(descriptor, 'maxHealth', 1))
        self.drive_pitch = 0.0
        self.drive_history = []
        self.aim_position = None
        self.fire_allowed = False
        self.clear = True
        self.next_decision = 0.0
        self.last_decision = None

    @property
    def pose(self):
        return (self.x, self.y, self.z, self.yaw)

    def position(self):
        return (self.x, self.y, self.z)

    def velocity(self):
        return (math.sin(self.yaw) * self.speed, 0.0,
                math.cos(self.yaw) * self.speed)

    def neighbour(self):
        return {
            'id': self.id,
            'position': self.position(),
            'team': self.team,
            'yaw': self.yaw,
            'velocity': self.velocity(),
            'half_length': self.length * 0.5,
            'half_width': self.width * 0.5,
        }


class BotControl(object):

    def __init__(self, force, map_name, arena_type, space_id, log,
                 player_motion=None, spotting=None):
        bounds = None
        box = getattr(arena_type, 'boundingBox', None)
        if box is not None:
            try:
                bounds = (float(box[0][0]), float(box[0][1]),
                          float(box[1][0]), float(box[1][1]))
            except (IndexError, TypeError, ValueError):
                bounds = None
        self._adapter = BotAdapter(map_name, BATTLE_SEED, bounds=bounds)
        self._force = force
        self._space_id = space_id
        self._log = log
        self._player_motion = player_motion
        self._spotting = spotting
        self._bodies = {}
        self._last_time = None
        self._stopped = False
        self._held = False
        self._logged = 0

    def hold(self):
        """Freeze every bot: the after-battle rule."""
        self._held = True
        for body in self._bodies.values():
            body.throttle = 0.0
            body.turn = 0.0
            body.fire_allowed = False

    @property
    def bodies(self):
        return self._bodies

    def register(self, vehicle, team):
        pose = self._force.pose(vehicle.id)
        if pose is None:
            return None
        body = BotBody(vehicle.id, pose, vehicle.typeDescriptor, team)
        self._bodies[vehicle.id] = body
        self._adapter.register(vehicle.id, team, vehicle.typeDescriptor,
                               'Enemy-%s' % (vehicle.id,))
        return body

    def stop(self):
        self._stopped = True

    def start(self, scheduler):
        self._schedule = scheduler
        self._schedule(0.0, self._tick)
        self._log('bot_control_started map=%s bots=%s tactical_map=%s'
                  % (self._adapter.director.map_name, len(self._bodies),
                     self._adapter.director.map_data is not None))

    def _direction_clear(self, body):
        import BigWorld
        import Math

        def clear(yaw):
            blocked = world_collision.check_horizontal_collision(
                engine_shim.wrap(BigWorld), Math, self._space_id,
                Math.Vector3(body.x, body.y, body.z), yaw,
                max(1.0, abs(body.speed)), body.descriptor, False,
                TICK_SECONDS)
            return not blocked

        return clear

    def _player_neighbour(self, player):
        import Math
        yaw = Math.Matrix(player.matrix).yaw
        speed = (self._player_motion.speed
                 if self._player_motion is not None else 0.0)
        position = player.position
        return {
            'id': HUMAN_TARGET_ID_BASE + int(player.id),
            'position': (position.x, position.y, position.z),
            'team': entity_setup.PLAYER_TEAM,
            'yaw': yaw,
            'velocity': (math.sin(yaw) * speed, 0.0,
                         math.cos(yaw) * speed),
        }

    def _state(self, body, player, now):
        contacts = []
        neighbours = [other.neighbour() for other in self._bodies.values()
                      if other.id != body.id and
                      self._force.health(other.id) > 0]
        if player is not None:
            position = player.position
            speed = (self._player_motion.speed
                     if self._player_motion is not None else 0.0)
            contacts.append({
                'id': player.id,
                'team': entity_setup.PLAYER_TEAM,
                'position': (position.x, position.y, position.z),
                'health': float(getattr(player, 'health', 1)),
                'max_health': float(getattr(player.typeDescriptor,
                                            'maxHealth', 1)),
                'speed': speed,
                'visible': (bool(self._spotting(body.id))
                            if self._spotting is not None else True),
            })
            neighbours.append(self._player_neighbour(player))
        return {
            'id': body.id,
            'team': body.team,
            'position': body.position(),
            'yaw': body.yaw,
            'speed': body.speed,
            'velocity': body.velocity(),
            'health': float(self._force.health(body.id)),
            'max_health': body.max_health,
            'dt': (min(MAX_FRAME_SECONDS, now - body.last_decision)
                   if body.last_decision is not None else DECISION_SECONDS),
            'now': now,
            'contacts': contacts,
            'neighbours': neighbours,
            'half_length': body.length * 0.5,
            'half_width': body.width * 0.5,
        }

    def _tick(self):
        """Decide at the mature cadence, integrate every render frame."""
        import BigWorld
        if self._stopped:
            return
        self._schedule(0.0, self._tick)
        now = BigWorld.time()
        last = self._last_time
        self._last_time = now
        avatar = BigWorld.player()
        if avatar is None or not avatar.userSeesWorld() or last is None:
            return
        dt = min(MAX_FRAME_SECONDS, max(0.0, now - last))
        if dt <= 0.0:
            return
        player = BigWorld.entities.get(avatar.playerVehicleID)
        for body in list(self._bodies.values()):
            if self._force.health(body.id) <= 0:
                continue
            if not self._held and now >= body.next_decision:
                self._decide(body, player, now)
            self._integrate(body, dt)

    def _decide(self, body, player, now):
        state = self._state(body, player, now)
        command = self._adapter.decide(state, self._direction_clear(body))
        command['throttle'], waiting = traffic_throttle(
            state, command, state['neighbours'])
        if waiting:
            self._adapter.driver.wait_for_traffic(body.id)
        self._advance_criticals(body, command, state['dt'])
        self._apply(body, command)
        body.last_decision = now
        body.next_decision = now + DECISION_SECONDS

    def _advance_criticals(self, body, command, dt):
        """The mature bot rule: broken running gear stops the command."""
        import BigWorld
        vehicle = BigWorld.entities.get(body.id)
        if vehicle is None:
            return
        critical_damage.tick_repair(vehicle, dt)
        burn, _unused = critical_damage.tick_fire(vehicle, dt,
                                                  BigWorld.time())
        track_visuals.refresh(vehicle)
        if burn:
            self._force.apply_damage(body.id, burn, 0)
        if (getattr(vehicle, 'is_tracked', False) or
                getattr(vehicle, 'is_engine_dead', False)):
            command['throttle'] = 0.0
            command['turn'] = 0.0
        else:
            command['throttle'] = (float(command.get('throttle', 0.0)) *
                                   critical_damage.stat_factor(vehicle,
                                                               'mobility'))
        if self._logged < 3:
            self._logged += 1
            self._log('bot_command id=%s throttle=%.2f turn=%.2f '
                      'speed=%.2f pos=(%.1f,%.1f,%.1f) fire=%s'
                      % (body.id, body.throttle, body.turn, body.speed,
                         body.x, body.y, body.z, body.fire_allowed))

    def _apply(self, body, command):
        """Store one command, probing the travel direction it implies."""
        body.throttle = float(command.get('throttle', 0.0))
        body.turn = float(command.get('turn', 0.0))
        body.aim_position = command.get('aim_position')
        body.fire_allowed = bool(command.get('fire_allowed', False))
        travel_yaw = (body.yaw if body.throttle >= 0.0
                      else body.yaw + math.pi)
        body.clear = True
        if abs(body.throttle) > 0.01:
            body.clear = self._direction_clear(body)(travel_yaw)
            if not body.clear:
                body.throttle = 0.0
                self._adapter.driver.remember_failure(body.id, travel_yaw)
        body.drive_pitch = suspension.smooth(
            body.drive_pitch,
            suspension.median_pitch(
                body.drive_history,
                suspension.drive_pitch(self._space_id, body.position(),
                                       body.yaw)))

    def _integrate(self, body, dt):
        """Advance the copied motion law with the stored command."""
        body.omega = motion.traverse_step(
            body.params, body.omega, body.turn, body.speed, dt,
            drive_intent=body.throttle)
        body.yaw += body.omega * dt
        while body.yaw > math.pi:
            body.yaw -= 2.0 * math.pi
        while body.yaw < -math.pi:
            body.yaw += 2.0 * math.pi
        body.speed = motion.longitudinal_step(
            body.params, body.speed, body.throttle, abs(body.turn) > 0.01,
            body.drive_pitch, dt)
        if not body.clear:
            body.speed *= 0.2
        else:
            step = body.speed * dt
            body.x += math.sin(body.yaw) * step
            body.z += math.cos(body.yaw) * step
        ground = suspension.ground_y(self._space_id, body.x, body.z, body.y)
        if ground is not None:
            body.y = suspension.settle(body.y, ground, body.speed, dt)
        vx, _unused, vz = body.velocity()
        self._force.set_pose(body.id, body.pose, velocity=(vx, vz))
        self._scroll_tracks(body)

    def _scroll_tracks(self, body):
        import BigWorld
        vehicle = BigWorld.entities.get(body.id)
        if vehicle is None:
            return
        track_visuals.ensure_scroll(vehicle, self._log)
        track_visuals.drive_engine(vehicle, body.speed, body.omega,
                                   getattr(vehicle, 'is_engine_dead', False))
        left, right = motion.track_scroll(body.params, body.speed,
                                          body.omega)
        track_visuals.feed_scroll(vehicle, left, right)
