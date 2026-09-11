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

The vehicle's detachment flag removes the source turret/gun collision on
every peer. A server-accepted frozen flight separately owns the landed
obstacle: the same rest pose supplies exact shell hit tests, static vehicle
contact boxes and late visible admission. Presentation never reruns a local
arc for an accepted record.

The client-created entity remains outside stock dynamic collision, because
the canonical obstacle is the room's collision owner. Its stock
``ProjectileAwareEntities`` membership remains intact for cleanup. Native
``isCollidingWithWorld`` stays false: the unfed WGTurretFilter cannot supply
the drag-effect velocity. Landed obstacles do not push, roll, or crush tanks;
movement is resolved as contact with a fixed accepted volume.
"""

import copy
import sys

from gui.mods.offline_lan_0922 import shot_geometry
from gui.mods.offline_lan_0922 import turret_detachment
from gui.mods.offline_lan_0922.entities.turret_obstacles import (
    DetachedTurretObstacles, rest_on_component_bounds, turret_components)


# One detached turret owns a turret plus gun compound for the rest of the
# round.  The client is 32-bit and has run out of address space on a single
# large texture reservation before, so bound how many can be resident at
# once. The server applies the same accepted-record cap to the whole room.
MAX_ACTIVE_TURRETS = 12
CANONICAL_CREATE_RETRY_SECONDS = 1.0
CANONICAL_MODEL_TIMEOUT_SECONDS = 30.0

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
        self._canonical_attempts = {}
        self._closed = False

    def active(self):
        return len(self._turrets)

    def has_vehicle(self, engine_id):
        """Whether a source still owns any pending, resident or retiring id."""
        return any(turret['vehicle_id'] == int(engine_id)
                   for turret in self._turrets + self._retiring)

    def prepare(self, entity, pose):
        """Freeze the launch pose and geometry before the death callbacks.

        Returns ``None`` whenever this exact target cannot carry the stock
        detachment.  A missing exploded model is not an error to escalate:
        the vehicle still becomes a turretless wreck without a flying entity.

        ``pose`` is the latest admitted terminal state available on this
        client.  The descriptor places the launch ring without relying on a
        compound that a health callback may already have replaced.
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

    def prepare_canonical(self, entity, row):
        """Prepare a visible source for a previously accepted frozen flight.

        Exact SynchronousDetachment still searches for a started source
        Vehicle. The runtime can retry this preparation when that source
        becomes ready or visible; absence here never consumes a load attempt.
        The accepted flight is not recomputed from this client's pose.
        """
        if self._closed or not getattr(entity, 'isStarted', False):
            return None
        descriptor = getattr(entity, 'typeDescriptor', None)
        if (self._exploded_model(getattr(descriptor, 'turret', None)) is None or
                self._exploded_model(getattr(descriptor, 'gun', None)) is None):
            return None
        try:
            return {
                'entity_id': int(entity.id),
                'descriptor': descriptor,
                'compact_descr': descriptor.makeCompactDescr(),
                'space_id': int(self._avatar.spaceID),
                'attitude': tuple(row['attitude']),
                'launch': tuple(row['flight']['origin']),
            }
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            self._note('canonical turret descriptor unavailable', error)
            return None

    def launch_canonical(self, plan, row, now, elapsed):
        """Materialize one accepted flight, including late AOI admission.

        ``elapsed`` is the age of the server-stamped record in seconds. A
        late admission therefore starts at the current flight/rest pose and
        performs no local raycast. Failed creation/load attempts are spaced
        apart and never overlap an unretired id for the same actor. An
        accepted obstacle remains eligible after a safely retired failure.
        """
        if self._closed or plan is None:
            return False
        key = (str(row['actor_kind']), int(row['actor_id']))
        attempt = self._canonical_attempts.get(key)
        if attempt is None:
            if len(self._canonical_attempts) >= self._max_active:
                return False
            attempt = {'row': copy.deepcopy(row),
                       'next_retry': float(now)}
            self._canonical_attempts[key] = attempt
        elif attempt['row'] != row:
            # One accepted actor owns one immutable throw for this round.
            return False
        for turret in self._turrets:
            if turret.get('canonical_key') == key:
                return True
        self._retire_entities()
        if (self.has_vehicle(plan['entity_id']) or
                any(turret.get('canonical_key') == key
                    for turret in self._retiring) or
                float(now) < attempt['next_retry']):
            return False
        attempt['next_retry'] = float(now) + CANONICAL_CREATE_RETRY_SECONDS
        return self._launch_frozen(
            plan, row['flight'], row['spin'], float(now) - max(0.0, float(elapsed)),
            float(now), canonical_key=key)

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
        return self._launch_frozen(plan, flight, impulse['spin'], now, now)

    def _launch_frozen(self, plan, flight, spin, started, now,
                       canonical_key=None):
        vehicle_id = int(plan['entity_id'])
        position, attitude = turret_detachment.pose_at(
            flight, plan['attitude'], spin, max(0.0, float(now) - float(started)))
        entity_id = None
        try:
            entity_id = self._bigworld.createEntity(
                _ENTITY_TYPE, plan['space_id'], 0,
                self._vector(position),
                (attitude[2], attitude[1], attitude[0]),
                {'vehicleID': vehicle_id,
                 'vehicleCompDescr': plan['compact_descr'],
                 'isUnderWater': False,
                 'isCollidingWithWorld': False})
            if entity_id is None:
                raise RuntimeError('createEntity returned no DetachedTurret')
            turret = {
                'id': int(entity_id),
                'vehicle_id': vehicle_id,
                'flight': copy.deepcopy(flight),
                'attitude': tuple(plan['attitude']),
                'spin': tuple(spin),
                'started': float(started),
                'created': float(now),
                'canonical_key': canonical_key,
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
                self._retiring.append({'id': int(entity_id), 'entity': None,
                                       'vehicle_id': vehicle_id,
                                       'canonical_key': canonical_key})
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
                elif self._canonical_load_expired(turret, now):
                    self._retire_failed_load(turret, now)
                continue
            if turret['entity'] is not None and turret['entity'] is not entity:
                self._turrets.remove(turret)
                continue
            if not self._entity_matches(entity, turret):
                self._turrets.remove(turret)
                continue
            turret['entity'] = entity
            model = getattr(entity, 'model', None)
            if model is None:
                # #1513 enters a client-created entity only after its
                # compound resources are resident.  Time keeps running, so a
                # late model appears already partway along its arc.
                if self._canonical_load_expired(turret, now):
                    self._retire_failed_load(turret, now)
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

    @staticmethod
    def _canonical_load_expired(turret, now):
        return (turret.get('canonical_key') is not None and
                float(now) - turret['created'] >= CANONICAL_MODEL_TIMEOUT_SECONDS)

    def _retire_failed_load(self, turret, now):
        self._turrets.remove(turret)
        self._retiring.append(turret)
        attempt = self._canonical_attempts.get(turret['canonical_key'])
        if attempt is not None:
            attempt['next_retry'] = float(now) + CANONICAL_CREATE_RETRY_SECONDS
        self._retire_entities()
        self._note('canonical turret model load timed out', RuntimeError(
            'vehicle %d entity %d' % (turret['vehicle_id'], turret['id'])))

    def _bind(self, entity, turret):
        """Replace the native filter matrix with the frozen arc's provider."""
        try:
            matrix = self._math.Matrix()
            entity.model.matrix = matrix
            entity.targetCaps = []
            turret['matrix'] = matrix
        except Exception as error:
            self._note('detached turret binding failed', error)
            return False
        try:
            # Reuse the LAN gate so stock collision does not compete with
            # the canonical obstacle's server-timed hit tests.
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
                if not self._entity_matches(entity, turret):
                    self._retiring.remove(turret)
                    continue
                turret['entity'] = entity
                if not self._destroy_entity(turret['id']):
                    continue
                retired += 1
            self._retiring.remove(turret)
        return retired

    @staticmethod
    def _entity_matches(entity, turret):
        """Check the source identity before adopting a never-observed id."""
        vehicle_id = getattr(entity, 'vehicleID', None)
        if vehicle_id is None:
            properties = getattr(entity, 'properties', None)
            if isinstance(properties, dict):
                vehicle_id = properties.get('vehicleID')
        try:
            return int(vehicle_id) == int(turret['vehicle_id'])
        except (KeyError, TypeError, ValueError):
            return False

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
    makes the launch point a pure function of the admitted state available
    on this client, independent of its interpolated render compound.
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


def freeze_obstacle_plan(entity, pose, seed, collide):
    """Resolve one worker proposal using this vehicle's exact loaded geometry.

    The server supplies actor identity and creation time only after admission.
    Both collision and presentation consume the returned frozen flight; the
    final rest Y supports the complete rotated turret/gun underside.
    """
    plan = detachment_plan(entity, pose)
    if plan is None:
        return None
    try:
        descriptor = plan['descriptor']
        components = turret_components(descriptor)
        if any(DetachedTurretPresentation._exploded_model(component) is None
               for unused_name, component, unused_offset, unused_bounds in components):
            return None
        impulse = turret_detachment.launch_impulse(seed)
        flight = turret_detachment.resolve_flight(
            plan['launch'], impulse['velocity'], collide,
            clearance=plan['clearance'])
        flight = rest_on_component_bounds(
            flight, plan['attitude'], impulse['spin'], components)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return None
    return {'flight': flight, 'attitude': plan['attitude'],
            'spin': impulse['spin']}
