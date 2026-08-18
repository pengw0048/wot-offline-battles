"""Spotting authority: the copied law decides who sees whom.

Runs at the cell cadence, one line-of-sight ray per pair per tick, and
publishes one visibility result to every consumer: the enemy models,
the bot planner's contact flags, and the sixth-sense lamp.
"""
from __future__ import absolute_import

import math

from gui.mods.offline_battle_2312 import spotting

TICK_SECONDS = 0.5
SIXTH_SENSE_DELAY_SECONDS = 3.0
LOS_MASK = 128
EYE_HEIGHT_METRES = 2.0
DEFAULT_CAMOUFLAGE = (0.1, 0.2)
MOVING_SPEED = 0.5


def camouflage_pair(descriptor):
    """(moving, still) base camouflage from the vehicle type."""
    try:
        invisibility = descriptor.type.invisibility
        still = float(invisibility[0])
        moving = float(invisibility[1])
        return (moving, still)
    except (AttributeError, IndexError, TypeError, ValueError):
        return DEFAULT_CAMOUFLAGE


def view_range(descriptor):
    try:
        return spotting.effective_view_range(
            float(descriptor.turret.circularVisionRadius))
    except (AttributeError, TypeError, ValueError):
        return spotting.effective_view_range(350.0)


class SpottingControl(object):

    def __init__(self, avatar, player_vehicle_id, enemies, log,
                 player_speed=None):
        self._avatar = avatar
        self._player_vehicle_id = player_vehicle_id
        self._enemies = enemies
        self._log = log
        self._player_speed = player_speed
        self._player_spotted = {}
        self._enemy_sees = {}
        self._seen_since = None
        self._lamp = False
        self._stopped = False
        self._callback_id = None

    def enemy_sees(self, vehicle_id):
        """The bot planner's contact flag, at the last tick's answer."""
        return bool(self._enemy_sees.get(vehicle_id, False))

    def start(self):
        self._tick()

    def stop(self):
        import BigWorld
        self._stopped = True
        if self._callback_id is not None:
            try:
                BigWorld.cancelCallback(self._callback_id)
            except Exception:
                pass
            self._callback_id = None

    def _tick(self):
        import BigWorld
        if self._stopped:
            return
        self._callback_id = BigWorld.callback(TICK_SECONDS, self._tick)
        player = BigWorld.entities.get(self._player_vehicle_id)
        if player is None or not getattr(player, 'isStarted', False):
            return
        player_eye = self._eye(player.position)
        player_view = view_range(player.typeDescriptor)
        player_speed = (abs(self._player_speed())
                        if self._player_speed is not None else 0.0)
        player_camouflage = spotting.effective_camouflage(
            camouflage_pair(player.typeDescriptor),
            moving=player_speed > MOVING_SPEED)
        anyone_sees = False
        for vehicle_id in self._enemies.ids():
            health = self._enemies.health(vehicle_id)
            enemy = BigWorld.entities.get(vehicle_id)
            if enemy is None:
                continue
            if health is None or health <= 0:
                self._apply_player_view(enemy, True)
                self._enemy_sees[vehicle_id] = False
                continue
            pose = self._enemies.pose(vehicle_id)
            position = (enemy.position if pose is None else
                        self._vector(pose[0], pose[1], pose[2]))
            distance = (position - player.position).length
            los = BigWorld.wg_collideSegment(
                player.spaceID, player_eye, self._eye(position),
                LOS_MASK) is None
            velocity = self._enemies._velocities.get(vehicle_id, (0.0, 0.0))
            moving = math.hypot(velocity[0], velocity[1]) > MOVING_SPEED
            enemy_camouflage = spotting.effective_camouflage(
                camouflage_pair(enemy.typeDescriptor), moving=moving)
            sees_enemy = spotting.is_detected(
                distance, player_view, enemy_camouflage,
                has_line_of_sight=los)
            self._apply_player_view(enemy, sees_enemy)
            sees_player = spotting.is_detected(
                distance, view_range(enemy.typeDescriptor),
                player_camouflage, has_line_of_sight=los)
            self._enemy_sees[vehicle_id] = sees_player
            anyone_sees = anyone_sees or sees_player
        self._update_lamp(anyone_sees, BigWorld.time())

    def _vector(self, x, y, z):
        import Math
        return Math.Vector3(x, y, z)

    def _eye(self, position):
        import Math
        return Math.Vector3(position.x, position.y + EYE_HEIGHT_METRES,
                            position.z)

    def _apply_player_view(self, enemy, visible):
        previous = self._player_spotted.get(enemy.id)
        if previous == visible:
            return
        self._player_spotted[enemy.id] = visible
        try:
            enemy.show(visible)
        except Exception as error:
            self._log('spot_show_failed id=%s error=%r' % (enemy.id, error))
            return
        self._log('%s id=%s' % ('spotted' if visible else 'unspotted',
                                enemy.id))

    def _update_lamp(self, anyone_sees, now):
        if anyone_sees:
            if self._seen_since is None:
                self._seen_since = now
            if not self._lamp and now - self._seen_since >= \
                    SIXTH_SENSE_DELAY_SECONDS:
                self._lamp = True
                self._push_lamp(True)
        else:
            self._seen_since = None
            if self._lamp:
                self._lamp = False
                self._push_lamp(False)

    def _push_lamp(self, value):
        from gui.battle_control.battle_constants import VEHICLE_VIEW_STATE
        try:
            self._avatar.guiSessionProvider.invalidateVehicleState(
                VEHICLE_VIEW_STATE.OBSERVED_BY_ENEMY, value)
            self._log('sixth_sense observed=%s' % (value,))
        except Exception as error:
            self._log('sixth_sense_failed error=%r' % (error,))
