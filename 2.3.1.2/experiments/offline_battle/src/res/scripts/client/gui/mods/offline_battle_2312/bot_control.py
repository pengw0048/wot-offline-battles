"""Drive the enemies with the copied 0.9.22 planner and driver.

The adapter owns the thinking: it takes a plain state dict per bot and
returns a command with throttle, turn, an aim position and whether to
fire. This module is only the 2.3.1.2 side of that contract, so the AI
itself stays the copy.
"""
from __future__ import absolute_import

import math

from gui.mods.offline_battle_2312 import engine_shim
from gui.mods.offline_battle_2312 import motion
from gui.mods.offline_battle_2312 import suspension
from gui.mods.offline_battle_2312 import world_collision
from gui.mods.offline_battle_2312.ai.adapter import BotAdapter

TICK_SECONDS = 0.1
BATTLE_SEED = 20260816


class BotBody(object):
    """One enemy's pose and speed, integrated by the copied motion law."""

    def __init__(self, vehicle_id, pose, descriptor):
        self.id = vehicle_id
        self.x, self.y, self.z, self.yaw = pose
        self.speed = 0.0
        self.omega = 0.0
        self.pitch = 0.0
        self.roll = 0.0
        self.descriptor = descriptor
        self.params = motion.derive_params(descriptor)
        self.length, self.width = suspension.hull_span(descriptor)
        self.drive_pitch = 0.0
        self.drive_history = []
        self.aim_position = None
        self.fire_allowed = False

    @property
    def pose(self):
        return (self.x, self.y, self.z, self.yaw)

    def position(self):
        return (self.x, self.y, self.z)


class BotControl(object):

    def __init__(self, force, map_name, arena_type, space_id, log):
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
        self._bodies = {}
        self._elapsed = 0.0
        self._stopped = False
        self._logged = 0

    @property
    def bodies(self):
        return self._bodies

    def register(self, vehicle, team):
        pose = self._force.pose(vehicle.id)
        if pose is None:
            return None
        body = BotBody(vehicle.id, pose, vehicle.typeDescriptor)
        self._bodies[vehicle.id] = body
        self._adapter.register(vehicle.id, team, vehicle.typeDescriptor,
                               'Enemy-%s' % (vehicle.id,))
        return body

    def stop(self):
        self._stopped = True

    def start(self, scheduler):
        self._schedule = scheduler
        self._schedule(TICK_SECONDS, self._tick)
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

    def _state(self, body, player, now):
        contacts = []
        if player is not None:
            position = player.position
            contacts.append({
                'id': player.id,
                'team': 1,
                'position': (position.x, position.y, position.z),
                'health': float(getattr(player, 'health', 1)),
                'max_health': float(getattr(player.typeDescriptor,
                                            'maxHealth', 1)),
                'visible': True,
            })
        neighbours = [other.position() for other in self._bodies.values()
                      if other.id != body.id]
        return {
            'id': body.id,
            'position': body.position(),
            'yaw': body.yaw,
            'speed': body.speed,
            'dt': TICK_SECONDS,
            'now': now,
            'contacts': contacts,
            'neighbours': neighbours,
            'half_length': body.length * 0.5,
            'half_width': body.width * 0.5,
        }

    def _tick(self):
        import BigWorld
        if self._stopped:
            return
        self._schedule(TICK_SECONDS, self._tick)
        avatar = BigWorld.player()
        if avatar is None or not avatar.userSeesWorld():
            return
        self._elapsed += TICK_SECONDS
        player = BigWorld.entities.get(avatar.playerVehicleID)
        for body in list(self._bodies.values()):
            if self._force.health(body.id) <= 0:
                continue
            command = self._adapter.decide(self._state(body, player,
                                                       self._elapsed),
                                           self._direction_clear(body))
            self._apply(body, command)
        if self._logged < 3:
            self._logged += 1
            sample = list(self._bodies.values())[0] if self._bodies else None
            if sample is not None:
                self._log('bot_command id=%s throttle=%.2f turn=%.2f '
                          'speed=%.2f pos=(%.1f,%.1f,%.1f) fire=%s'
                          % (sample.id, sample.throttle, sample.turn,
                             sample.speed, sample.x, sample.y, sample.z,
                             sample.fire_allowed))

    def _apply(self, body, command):
        """Integrate the copied motion law with the bot's own command."""
        body.throttle = float(command.get('throttle', 0.0))
        body.turn = float(command.get('turn', 0.0))
        body.aim_position = command.get('aim_position')
        body.fire_allowed = bool(command.get('fire_allowed', False))
        body.drive_pitch = suspension.smooth(
            body.drive_pitch,
            suspension.median_pitch(
                body.drive_history,
                suspension.drive_pitch(self._space_id, body.position(),
                                       body.yaw)))
        body.omega = motion.traverse_step(
            body.params, body.omega, body.turn, body.speed, TICK_SECONDS,
            drive_intent=body.throttle)
        body.speed = motion.longitudinal_step(
            body.params, body.speed, body.throttle, body.turn != 0.0,
            body.drive_pitch, TICK_SECONDS)
        body.yaw += body.omega * TICK_SECONDS
        while body.yaw > math.pi:
            body.yaw -= 2.0 * math.pi
        while body.yaw < -math.pi:
            body.yaw += 2.0 * math.pi
        step = body.speed * TICK_SECONDS
        if step and not self._direction_clear(body)(body.yaw):
            body.speed = 0.0
            step = 0.0
        body.x += math.sin(body.yaw) * step
        body.z += math.cos(body.yaw) * step
        ground = suspension.ground_y(self._space_id, body.x, body.z, body.y)
        if ground is not None:
            body.y = suspension.settle(body.y, ground, body.speed,
                                       TICK_SECONDS)
        self._force.set_pose(body.id, body.pose)
