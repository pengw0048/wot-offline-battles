# -*- coding: utf-8 -*-
"""Client-created #1513 ``DetachedTurret`` presentation for an ammo-bay kill.

Retail spawns this entity from the cell app and interpolates its replicated
pose through the native ``BigWorld.WGTurretFilter``.  This port has no cell
app and no legal way to feed that filter from Python -- the pinned
executable's ``PyWGEntityFilter`` table exposes ``transferInput`` and
``transferInputAsVehicle`` but no script input at all -- so the entity is
created client-side and its compound is driven from the frozen arc in
``turret_detachment``, exactly the way every LAN remote Vehicle compound is
driven.

Everything else is stock: ``DetachedTurret.__prepareModelAssembler`` builds
``turret.models.exploded`` plus ``gun.models.exploded``,
``_TurretDetachmentEffects`` plays the shipped ``turret_flying_*`` chain and
switches to ``turret_touchdown_*`` / ``flamingOnGround`` when
``onStaticCollision`` reports the landing, and ``VehicleStickers`` reattaches
the vehicle's own marks and damage decals.

Collision is owned separately, by ``DetachedTurretObstacles`` below.  Retail's
``DetachedTurret`` is a real entity -- its ``.def`` publishes ``receiveShot``
and ``onDamageVehicle``, and its client half answers ``collideSegment`` out of
the turret and gun hit testers -- but in this port the hidden worker is the
only collision authority in the room, and it draws nothing.  Both halves
therefore resolve the *same* deterministic arc from the same replicated
inputs: the worker keeps the landed turret's geometry, every visible client
keeps its presentation, and neither has to replicate the other.

Two deliberate differences from retail remain:

* the entity this class creates is excluded from the visible client's own
  dynamic collision through the same ``_offlineNativeRemote`` draw gate the
  port already uses for a LAN remote the client must not collide against.  It
  keeps its stock ``ProjectileAwareEntities`` membership so ``onLeaveWorld``
  stays intact.  Shots are stopped by the worker's obstacle instead, so the
  client's aim marker does not colour on a landed turret even though a shell
  fired at one terminates on it.
* ``isCollidingWithWorld`` stays false for its whole life.  It is the only
  gate that makes ``__checkIsBeingPulled`` read native ``Entity.velocity``
  off the unfed ``WGTurretFilter``, and the property drives nothing but the
  drag/pull effect this version does not produce.  Retail's turret is also a
  cell-physics body that a tank can push and be crushed by; nothing here
  reproduces that, so a landed turret blocks shots but not vehicles.
"""

import math
import sys

from gui.mods.offline_lan_0922 import shot_geometry
from gui.mods.offline_lan_0922 import turret_detachment


# One detached turret owns a turret plus gun compound for the rest of the
# round.  The client is 32-bit and has run out of address space on a single
# large texture reservation before, so bound how many can be resident at
# once; beyond the cap the vehicle keeps its burn-off wreck and turret.
MAX_ACTIVE_TURRETS = 12

_ENTITY_TYPE = 'DetachedTurret'


class DetachedTurretPresentation(object):
    """Own every client-created detached turret in one round.

    The runtime builds one of these per battle and drops it in cleanup, so
    round identity is the object's own lifetime; every turret is additionally
    fenced by the dying vehicle's engine id and resolved by entity id on each
    frame, so a retired entity is dropped rather than written to.
    """

    def __init__(self, bigworld, math_module, avatar, collide, log=None,
                 max_active=MAX_ACTIVE_TURRETS):
        self._bigworld = bigworld
        self._math = math_module
        self._avatar = avatar
        self._collide = collide
        self._log = log
        self._max_active = int(max_active)
        self._turrets = []
        self._retiring = []
        self._closed = False

    def active(self):
        return len(self._turrets)

    def prepare(self, entity, pose):
        """Freeze the launch pose and geometry before the death callbacks.

        Returns ``None`` whenever this exact target cannot carry the stock
        detachment.  A missing exploded model is not an error to escalate:
        the vehicle keeps the plain wreck it has today.

        ``pose`` is the authoritative terminal pose the whole room agreed on,
        not this client's interpolated render pose.  Every peer therefore
        starts the identical arc from the identical turret ring, which is what
        lets the hidden worker own the landed turret's collision while a
        visible client owns only its presentation.
        """
        if self._closed or len(self._turrets) >= self._max_active:
            return None
        if not getattr(entity, 'isStarted', False):
            # An unspotted remote never started its visual, so retail would
            # not have it in AOI and stock's own detach handshake would spend
            # its whole search window waiting for it.
            return None
        plan = detachment_plan(entity, pose)
        if plan is None:
            return None
        descriptor = plan['descriptor']
        if (self._exploded_model(getattr(descriptor, 'turret', None)) is None or
                self._exploded_model(getattr(descriptor, 'gun', None)) is None):
            return None
        compact_descr = getattr(descriptor, 'makeCompactDescr', None)
        if not callable(compact_descr):
            return None
        try:
            plan['compact_descr'] = compact_descr()
        except Exception as error:
            self._note('vehicle compact descriptor unavailable', error)
            return None
        plan['space_id'] = int(getattr(self._avatar, 'spaceID', 0))
        return plan

    @staticmethod
    def _exploded_model(component):
        models = getattr(component, 'models', None)
        return getattr(models, 'exploded', None)

    def launch(self, plan, seed, now):
        """Create the stock entity and start driving its compound.

        The caller has already written ``SPECIAL_VEHICLE_HEALTH``'s turret
        value and pre-confirmed the detachment, so stock's
        ``SynchronousDetachment`` finishes inside ``__init__`` without
        re-confirming and without seeding the turret filter from the
        vehicle's own unfed one.
        """
        if self._closed:
            return False
        vehicle_id = int(plan['entity_id'])
        for existing in self._turrets:
            if existing['vehicle_id'] == vehicle_id:
                # One vehicle throws one turret.  The death edge only fires
                # once, but a replayed terminal event must never be able to
                # stack a second compound on the same wreck.
                return False
        impulse = turret_detachment.launch_impulse(seed)
        flight = turret_detachment.resolve_flight(
            plan['launch'], impulse['velocity'], self._collide,
            clearance=plan['clearance'])
        entity_id = None
        try:
            entity_id = self._bigworld.createEntity(
                _ENTITY_TYPE, plan['space_id'], 0,
                self._vector(plan['launch']),
                (plan['attitude'][2], plan['attitude'][1],
                 plan['attitude'][0]),
                {'vehicleID': vehicle_id,
                 'vehicleCompDescr': plan['compact_descr'],
                 'isUnderWater': False,
                 'isCollidingWithWorld': False})
            if entity_id is None:
                raise RuntimeError('createEntity returned no DetachedTurret')
            turret = {
                'id': int(entity_id),
                'vehicle_id': vehicle_id,
                'flight': flight,
                'attitude': plan['attitude'],
                'spin': impulse['spin'],
                'started': float(now),
                'matrix': None,
                'entity': None,
                'impacted': False,
                'settled': False,
            }
            self._turrets.append(turret)
            sys.stdout.write(
                '[Offline LAN 0.9.22] TURRET vehicle=%d entity=%d '
                'state=created\n' % (vehicle_id, int(entity_id)))
        except Exception as error:
            # An allocated id may still be loading prerequisites.  Retain it
            # for safe retirement instead of destroying a not-yet-owned id.
            if entity_id is not None:
                self._retiring.append({'id': int(entity_id), 'entity': None})
                self._retire_entities()
            self._note('detached turret creation failed', error)
            return False
        return True

    def advance(self, now):
        """Write this frame's pose for every live turret."""
        self._retire_entities()
        if not self._turrets:
            return 0
        written = 0
        for turret in tuple(self._turrets):
            entity = self._entity(turret['id'])
            if entity is None:
                # #1513 createEntity returns an id before prerequisites and
                # onEnterWorld publish the entity.  Only a previously seen
                # entity disappearing means retirement, not pending loading.
                if turret['entity'] is not None:
                    self._turrets.remove(turret)
                continue
            if turret['entity'] is not None and turret['entity'] is not entity:
                self._turrets.remove(turret)
                continue
            turret['entity'] = entity
            model = getattr(entity, 'model', None)
            if model is None:
                # #1513 enters a client-created entity only after its
                # compound resources are resident.  Time keeps running, so a
                # late model appears already partway along its arc.
                continue
            if turret['settled']:
                # The rest pose is written once and nothing else drives this
                # compound, so a landed turret costs nothing per frame for
                # the rest of the round.
                continue
            if turret['matrix'] is None:
                if not self._bind(entity, turret):
                    self._retiring.append(turret)
                    self._retire_entities()
                    self._turrets.remove(turret)
                    continue
                sys.stdout.write(
                    '[Offline LAN 0.9.22] TURRET vehicle=%d entity=%d '
                    'state=bound elapsed_ms=%d\n' % (
                        turret['vehicle_id'], turret['id'],
                        max(0, int((float(now) - turret['started']) * 1000))))
            elapsed = max(0.0, float(now) - turret['started'])
            position, attitude = turret_detachment.pose_at(
                turret['flight'], turret['attitude'], turret['spin'],
                elapsed)
            matrix = turret['matrix']
            matrix.setRotateYPR(
                (attitude[0], attitude[1], attitude[2]))
            matrix.translation = self._vector(position)
            written += 1
            if elapsed >= float(turret['flight']['duration']):
                turret['settled'] = True
                if not turret['impacted']:
                    turret['impacted'] = True
                    self._report_impact(entity, turret['flight'])
        return written

    def _bind(self, entity, turret):
        """Replace the native filter matrix with the frozen arc's provider."""
        try:
            matrix = self._math.Matrix()
            matrix.setRotateYPR(
                (turret['attitude'][0], turret['attitude'][1],
                 turret['attitude'][2]))
            matrix.translation = self._vector(turret['flight']['origin'])
            entity.model.matrix = matrix
            entity.targetCaps = []
            turret['matrix'] = matrix
        except Exception as error:
            self._note('detached turret binding failed', error)
            return False
        try:
            # Reuse the reviewed LAN draw gate so stock dynamic collision,
            # the gun marker and its penetration indicator never resolve a
            # turret the hidden worker's projectiles cannot hit.
            entity._offlineNativeRemote = True
            entity._offlineNativeDrawVisible = False
        except Exception as error:
            # A turret that flies but can still catch the aim marker is a
            # smaller fault than no turret at all; the compound is already
            # bound and driven.
            self._note('detached turret collision gate failed', error)
        return True

    def _report_impact(self, entity, flight):
        """Hand the landing to stock's own touchdown effect exactly once."""
        if not flight.get('landed'):
            # No surface inside the search window: the arc still has a
            # terminal pose, but there is no contact to report.
            return False
        collision = getattr(entity, 'onStaticCollision', None)
        if not callable(collision):
            return False
        try:
            collision(
                float(flight['energy']), self._vector(flight['contact']),
                self._math.Vector3(0.0, 1.0, 0.0))
        except Exception as error:
            # A missing terrain material or effect must not stop the arc that
            # has already been published.
            self._note('detached turret impact effect failed', error)
            return False
        return True

    def destroy_all(self):
        """Close presentation and retire only engine-owned turret entities.

        A pending creation stays as a tombstone for the next teardown poll.
        BattleRuntime polls again before retiring the battle space, whose
        native teardown cancels any remaining prerequisite loads.  No timer
        or animation from this closed owner can enter the next round.
        """
        self._closed = True
        self._retiring.extend(self._turrets)
        self._turrets = []
        return self._retire_entities()

    def _retire_entities(self):
        retired = 0
        for turret in tuple(self._retiring):
            entity = self._entity(turret['id'])
            previous = turret['entity']
            if entity is None and previous is None:
                continue
            if entity is not None and (previous is None or previous is entity):
                turret['entity'] = entity
                if not self._destroy_entity(turret['id']):
                    continue
                retired += 1
            self._retiring.remove(turret)
        return retired

    def _destroy_entity(self, entity_id):
        destroy = getattr(self._bigworld, 'destroyEntity', None)
        if not callable(destroy):
            return False
        try:
            destroy(int(entity_id))
        except Exception as error:
            self._note('detached turret teardown failed', error)
            return False
        return True

    def _entity(self, entity_id):
        resolve = getattr(self._bigworld, 'entity', None)
        if not callable(resolve):
            return None
        try:
            return resolve(int(entity_id))
        except Exception:
            return None

    def _vector(self, value):
        return self._math.Vector3(
            float(value[0]), float(value[1]), float(value[2]))

    def _note(self, what, error):
        if callable(self._log):
            self._log(what, error)
        return None


def _turret_clearance(turret):
    """Return how far the turret's underside sits below its own origin.

    Landing the turret's origin on the surface would bury half of it, so the
    arc is walked with the underside of its own hit-tester box.  A descriptor
    without a readable box keeps a bounded default instead of guessing a
    shape.
    """
    tester = getattr(turret, 'hitTester', None)
    bounds = getattr(tester, 'bbox', None)
    try:
        minimum = bounds[0]
        return max(0.0, -float(minimum[1]))
    except (IndexError, TypeError, ValueError):
        return 0.0


def turret_mount_offset(descriptor):
    """Return the turret ring in the vehicle's own root space.

    Exact #1513 builds the same point in ``Vehicle.getComponents``:
    ``chassis.hullPosition`` places the hull under the model matrix and
    ``hull.turretPositions[0]`` places the ring inside the hull.  Reading it
    from the descriptor rather than from ``compoundModel.node('turret')``
    makes the launch point a pure function of replicated state, so a hidden
    worker and every visible client agree on it without a wire field.
    """
    chassis = getattr(descriptor, 'chassis', None)
    hull = getattr(descriptor, 'hull', None)
    hull_position = _xyz(getattr(chassis, 'hullPosition', None))
    positions = getattr(hull, 'turretPositions', None)
    if hull_position is None or not positions:
        return None
    try:
        ring = _xyz(positions[0])
    except (IndexError, KeyError, TypeError):
        return None
    if ring is None:
        return None
    return tuple(hull_position[axis] + ring[axis] for axis in range(3))


def _xyz(value):
    """Read one #1513 ``Vector3`` as a plain tuple, or ``None``."""
    if value is None:
        return None
    try:
        return (float(value.x), float(value.y), float(value.z))
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def detachment_plan(entity, pose):
    """Freeze one detachment's launch geometry from authoritative state.

    ``pose`` carries the terminal ``x``/``y``/``z``/``yaw``/``pitch``/``roll``
    and, when the room published one, ``turret_yaw``.  Returns ``None`` when
    the descriptor cannot describe a turret ring; the caller keeps whatever
    wreck it already has rather than inventing a launch point.
    """
    descriptor = getattr(entity, 'typeDescriptor', None)
    turret = getattr(descriptor, 'turret', None)
    if descriptor is None or turret is None or not isinstance(pose, dict):
        return None
    mount = turret_mount_offset(descriptor)
    if mount is None:
        return None
    try:
        yaw = float(pose.get('yaw', 0.0) or 0.0)
        pitch = float(pose.get('pitch', 0.0) or 0.0)
        roll = float(pose.get('roll', 0.0) or 0.0)
        offset = shot_geometry.transform_vehicle_vector(
            mount, yaw, pitch, roll)
        launch = (float(pose['x']) + offset[0],
                  float(pose['y']) + offset[1],
                  float(pose['z']) + offset[2])
        turret_yaw = float(pose.get('turret_yaw', 0.0) or 0.0)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return {
        'entity_id': int(getattr(entity, 'id', 0)),
        'descriptor': descriptor,
        'launch': launch,
        'attitude': (yaw + turret_yaw, pitch, roll),
        'clearance': _turret_clearance(turret),
    }


class DetachedTurretObstacles(object):
    """Authoritative collision for turrets an ammo-bay kill threw off.

    Retail's ``DetachedTurret`` is a real entity, not a decoration: its
    ``.def`` publishes ``receiveShot`` and ``onDamageVehicle``, and the client
    half answers ``collideSegment(start, end, skipGun)`` out of the turret and
    gun hit testers exactly the way ``Vehicle`` does.  This port cannot
    replicate the cell-side arc, so the hidden worker -- the only collision
    authority in the room -- resolves the same deterministic arc from the same
    replicated inputs and owns the landed turret as an obstacle.

    Nothing here draws, loads or owns a model, so the worker's address space
    is untouched.  A turret is an obstacle only once its frozen arc has ended:
    while it is still in the air it has no owner able to move a hit tester
    with it, and inventing a mid-flight sweep would be a fabricated physical
    result rather than a replicated one.
    """

    def __init__(self, math_module, collide, log=None,
                 max_active=MAX_ACTIVE_TURRETS):
        self._math = math_module
        self._collide = collide
        self._log = log
        self._max_active = int(max_active)
        self._turrets = []

    def active(self):
        return len(self._turrets)

    def add(self, plan, seed, now):
        """Resolve one arc and retain its landed turret as an obstacle."""
        if plan is None or len(self._turrets) >= self._max_active:
            return False
        vehicle_id = int(plan['entity_id'])
        for existing in self._turrets:
            if existing['vehicle_id'] == vehicle_id:
                # One vehicle throws one turret.  A replayed terminal event
                # must not stack a second obstacle on the same wreck.
                return False
        try:
            impulse = turret_detachment.launch_impulse(seed)
            flight = turret_detachment.resolve_flight(
                plan['launch'], impulse['velocity'], self._collide,
                clearance=plan['clearance'])
            attitude = turret_detachment.rest_attitude(
                plan['attitude'], impulse['spin'], flight['duration'])
            body = self._rest_frame(flight['rest'], attitude)
        except Exception as error:
            self._note('detached turret obstacle unavailable', error)
            return False
        if body is None:
            return False
        descriptor = plan['descriptor']
        components, radius = self._components(descriptor, body['to_turret'])
        body.update({
            'vehicle_id': vehicle_id,
            'settles_at': float(now) + float(flight['duration']),
            'components': components,
            'rest': tuple(float(value) for value in flight['rest']),
            'radius': radius,
        })
        self._turrets.append(body)
        return True

    def _rest_frame(self, rest, attitude):
        try:
            matrix = self._math.Matrix()
            matrix.setRotateYPR(
                (float(attitude[0]), float(attitude[1]), float(attitude[2])))
            matrix.translation = self._math.Vector3(
                float(rest[0]), float(rest[1]), float(rest[2]))
            to_turret = self._math.Matrix(matrix)
            to_turret.invert()
        except Exception as error:
            self._note('detached turret rest frame unavailable', error)
            return None
        return {'matrix': matrix, 'to_turret': to_turret}

    def _components(self, descriptor, to_turret):
        """Build the turret/gun pair stock ``collideSegment`` tests, and the
        bounding sphere about the turret origin that contains both."""
        components = []
        radius = 0.0
        turret = getattr(descriptor, 'turret', None)
        if turret is not None:
            components.append((turret, to_turret))
            radius = max(radius, _component_radius(turret, (0.0, 0.0, 0.0)))
        gun = getattr(descriptor, 'gun', None)
        gun_position = getattr(turret, 'gunPosition', None)
        if gun is not None and gun_position is not None:
            try:
                to_gun = self._math.Matrix()
                to_gun.setTranslate(-gun_position)
                to_gun.preMultiply(to_turret)
            except Exception as error:
                self._note('detached turret gun frame unavailable', error)
            else:
                components.append((gun, to_gun))
                radius = max(radius, _component_radius(
                    gun, _xyz(gun_position) or (0.0, 0.0, 0.0)))
        return tuple(components), radius

    def block_distance(self, start, end, now):
        """Return how far along ``start``..``end`` a landed turret stops it.

        ``None`` means no settled turret is in the way.  The distance is
        measured on the world segment: every frame below is a rigid transform
        of it, so a local hit distance is the world distance.
        """
        if not self._turrets:
            return None
        nearest = None
        for turret in self._turrets:
            if float(now) < turret['settles_at']:
                continue
            if _segment_distance_squared(
                    turret['rest'], start, end) > turret['radius'] ** 2:
                # Broad phase.  A projectile chord asks this on every step of
                # every shot, and a native hit test costs about seven
                # microseconds; a turret nowhere near the ray must not pay it.
                continue
            for component, matrix in turret['components']:
                tester = getattr(component, 'hitTester', None)
                local_hit_test = getattr(tester, 'localHitTest', None)
                if not callable(local_hit_test):
                    continue
                try:
                    collisions = local_hit_test(
                        matrix.applyPoint(start), matrix.applyPoint(end))
                except Exception as error:
                    # One unusable hit tester must not stop a shot that the
                    # world and every vehicle have already resolved.
                    self._note('detached turret hit test failed', error)
                    continue
                for collision in collisions or ():
                    try:
                        distance = float(collision[0])
                    except (IndexError, TypeError, ValueError):
                        continue
                    if nearest is None or distance < nearest:
                        nearest = distance
        return nearest

    def drop(self, vehicle_id):
        """Forget one wreck's turret, e.g. when its round identity ends."""
        vehicle_id = int(vehicle_id)
        remaining = [turret for turret in self._turrets
                     if turret['vehicle_id'] != vehicle_id]
        dropped = len(self._turrets) - len(remaining)
        self._turrets = remaining
        return dropped

    def clear(self):
        """Drop every turret.  Safe after a partial start, and safe twice."""
        count = len(self._turrets)
        self._turrets = []
        return count

    def _note(self, what, error):
        if callable(self._log):
            self._log(what, error)
        return None


def _component_radius(component, offset):
    """Return how far one component reaches from the turret origin.

    ``offset`` is where the component sits in the turret's own frame, so a
    gun barrel is measured from the ring rather than from its own mount.  An
    unreadable box widens the sphere to infinity: a broad phase may only
    reject what the exact hit test would also have rejected.
    """
    tester = getattr(component, 'hitTester', None)
    bounds = getattr(tester, 'bbox', None)
    radius = 0.0
    for corner in (0, 1):
        point = _xyz(bounds[corner]) if bounds is not None else None
        if point is None:
            return float('inf')
        radius = max(radius, math.sqrt(sum(
            (point[axis] + offset[axis]) ** 2 for axis in range(3))))
    return radius


def _segment_distance_squared(point, start, end):
    """Squared distance from ``point`` to the ``start``..``end`` segment."""
    origin = (float(start.x), float(start.y), float(start.z))
    direction = (float(end.x) - origin[0], float(end.y) - origin[1],
                 float(end.z) - origin[2])
    delta = tuple(float(point[axis]) - origin[axis] for axis in range(3))
    length_squared = sum(value * value for value in direction)
    if length_squared <= 0.0:
        return sum(value * value for value in delta)
    fraction = sum(delta[axis] * direction[axis]
                   for axis in range(3)) / length_squared
    fraction = max(0.0, min(1.0, fraction))
    return sum((delta[axis] - direction[axis] * fraction) ** 2
               for axis in range(3))
