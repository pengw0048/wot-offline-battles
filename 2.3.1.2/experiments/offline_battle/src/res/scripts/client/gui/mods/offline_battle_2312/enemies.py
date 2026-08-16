"""Put enemy vehicles on the map so there is something to shoot.

Each one is a real Vehicle entity created the same way the player's is,
published through the same roster call, and owned by the same runtime.
They do not drive or shoot yet; they stand, take hits and die.
"""
from __future__ import absolute_import

import math

from gui.mods.offline_battle_2312 import entity_setup, suspension
from gui.mods.offline_battle_2312 import tank_collision

ENEMY_TYPE_NAME = 'ussr:R11_MS-1'
ENEMY_COUNT = 3
ENEMY_SPACING_METRES = 12.0
ENEMY_RANGE_METRES = 90.0
FIRST_ENEMY_ID_OFFSET = 1


def formation(origin, yaw, count=ENEMY_COUNT, spacing=ENEMY_SPACING_METRES,
              distance=ENEMY_RANGE_METRES):
    """Spread the enemies across the player's line of sight."""
    sin_yaw, cos_yaw = math.sin(yaw), math.cos(yaw)
    x, z = float(origin[0]), float(origin[1])
    places = []
    for index in range(count):
        offset = (index - (count - 1) * 0.5) * spacing
        places.append((x + sin_yaw * distance + cos_yaw * offset,
                       z + cos_yaw * distance - sin_yaw * offset,
                       yaw + math.pi))
    return places


class EnemyForce(object):

    def __init__(self, avatar, arena_type_id, log):
        self._avatar = avatar
        self._arena_type_id = arena_type_id
        self._log = log
        self._ids = []
        self._health = {}
        self._poses = {}
        self._comp_descr = None
        self._matrices = {}
        self._velocities = {}

    @property
    def ids(self):
        return tuple(self._ids)

    def alive(self):
        import BigWorld
        live = []
        for vehicle_id in self._ids:
            vehicle = BigWorld.entities.get(vehicle_id)
            if vehicle is not None and self._health.get(vehicle_id, 0) > 0:
                live.append(vehicle)
        return live

    def health(self, vehicle_id):
        return self._health.get(vehicle_id, 0)

    def pose(self, vehicle_id):
        """(x, y, z, yaw) where this enemy was placed; it does not move."""
        return self._poses.get(vehicle_id)

    def set_pose(self, vehicle_id, pose, velocity=None):
        """Move an enemy: the runtime owns these poses, as it owns its own."""
        import BigWorld
        import Math
        self._poses[vehicle_id] = pose
        if velocity is not None:
            self._velocities[vehicle_id] = velocity
        vehicle = BigWorld.entities.get(vehicle_id)
        if vehicle is None:
            return
        model = getattr(vehicle, 'model', None)
        matrix = self._matrices.get(vehicle_id)
        if matrix is None:
            matrix = Math.Matrix()
            self._matrices[vehicle_id] = matrix
        matrix.setRotateYPR((pose[3], 0.0, 0.0))
        matrix.translation = Math.Vector3(pose[0], pose[1], pose[2])
        if model is not None and model.matrix is not matrix:
            model.matrix = matrix

    def bodies(self):
        """Plain-data hulls for the copied tank-against-tank law."""
        import BigWorld
        result = []
        for vehicle_id in self._ids:
            pose = self._poses.get(vehicle_id)
            vehicle = BigWorld.entities.get(vehicle_id)
            if pose is None or vehicle is None:
                continue
            descriptor = vehicle.typeDescriptor
            vx, vz = self._velocities.get(vehicle_id, (0.0, 0.0))
            result.append({
                'id': vehicle_id,
                'x': pose[0], 'y': pose[1], 'z': pose[2], 'yaw': pose[3],
                'mass': float(descriptor.physics['weight']),
                'vx': vx, 'vz': vz,
                'alive': self._health.get(vehicle_id, 0) > 0,
                'shape': tank_collision.chassis_shape(descriptor),
            })
        return result

    def spawn(self, origin, yaw):
        import BigWorld
        import Math
        from items import vehicles
        avatar = self._avatar
        descriptor = vehicles.VehicleDescr(typeName=ENEMY_TYPE_NAME)
        comp_descr = descriptor.makeCompactDescr()
        max_health = int(descriptor.maxHealth)
        self._comp_descr = comp_descr
        roster = []
        for index, place in enumerate(formation(origin, yaw)):
            x, z, facing = place
            ground = suspension.wide_ground_y(avatar.spaceID, x, z)
            if ground is None:
                continue
            name = 'Enemy-%d' % (index + 1,)
            properties = entity_setup.vehicle_properties(
                comp_descr, max_health, avatar.id + FIRST_ENEMY_ID_OFFSET +
                index, self._arena_type_id, avatar.arenaBonusType,
                name=name, team=entity_setup.ENEMY_TEAM, is_my_vehicle=False)
            vehicle_id = BigWorld.createEntity(
                'Vehicle', avatar.spaceID, 0, Math.Vector3(x, ground, z),
                (0.0, 0.0, facing), properties)
            self._ids.append(vehicle_id)
            self._health[vehicle_id] = max_health
            self._poses[vehicle_id] = (x, ground, z, facing)
            roster.append(entity_setup.roster_entry(
                vehicle_id, comp_descr, max_health, name=name,
                team=entity_setup.ENEMY_TEAM, session_id='enemy_%d' % index))
        for entry in roster:
            # updateVehiclesList clears the roster; the player is in it.
            avatar.arena.addVehInfo(entry)
        self._log('enemies_spawned count=%s ids=%s health=%s'
                  % (len(roster), self._ids, max_health))
        return len(roster)

    def apply_damage(self, vehicle_id, damage, attacker_id, reason_id=0):
        """Publish the health change the cell normally publishes."""
        import BigWorld
        vehicle = BigWorld.entities.get(vehicle_id)
        if vehicle is None:
            return None
        previous = self._health.get(vehicle_id, 0)
        if previous <= 0:
            return None
        health = max(0, previous - int(damage))
        self._health[vehicle_id] = health
        vehicle.health = health
        # onHealthChanged publishes the marker health itself.
        vehicle.onHealthChanged(health, previous, int(attacker_id),
                                int(reason_id), 0)
        if health <= 0:
            self._kill(vehicle, attacker_id, reason_id)
        return health

    def _kill(self, vehicle, attacker_id, reason_id):
        was_active = vehicle.isCrewActive
        vehicle.isCrewActive = False
        vehicle.set_isCrewActive(was_active)
        arena = self._avatar.arena
        info = arena.vehicles.get(vehicle.id)
        if info is None:
            return
        info['isAlive'] = False
        info['deathInfo'] = entity_setup.death_info(
            vehicle.id, int(attacker_id), int(reason_id))
        # The arena turns compDescr into vehicleType and drops the key.
        arena.updateVehicleIsAlive(vehicle.id, self._comp_descr, False)
        self._log('enemy_killed id=%s attacker=%s' % (vehicle.id,
                                                      attacker_id))

    def destroy(self):
        import BigWorld
        for vehicle_id in self._ids:
            if BigWorld.entities.get(vehicle_id) is not None:
                BigWorld.destroyEntity(vehicle_id)
        self._ids = []
        self._health = {}
        self._poses = {}
        self._velocities = {}
