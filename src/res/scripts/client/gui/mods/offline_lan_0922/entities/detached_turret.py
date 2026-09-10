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

Two deliberate differences from retail, both because the hidden worker owns
projectiles and knows nothing about this object:

* the flying turret is not shootable and does not block a shot.  It keeps its
  stock ``ProjectileAwareEntities`` membership so ``onLeaveWorld`` stays
  intact, and is excluded from dynamic collision through the same
  ``_offlineNativeRemote`` draw gate the port already uses for a LAN remote
  the client must not collide against.
* ``isCollidingWithWorld`` stays false for its whole life.  It is the only
  gate that makes ``__checkIsBeingPulled`` read native ``Entity.velocity``
  off the unfed ``WGTurretFilter``, and the property drives nothing but the
  drag/pull effect this version does not produce.
"""

import sys

from gui.mods.offline_lan_0922 import turret_detachment


# One detached turret owns a turret plus gun compound for the rest of the
# round.  The client is 32-bit and has run out of address space on a single
# large texture reservation before, so bound how many can be resident at
# once; beyond the cap the vehicle keeps its burn-off wreck and turret.
MAX_ACTIVE_TURRETS = 12

_TURRET_PART_NAME = 'turret'
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

    def prepare(self, entity):
        """Freeze the launch pose and geometry before the death callbacks.

        Returns ``None`` whenever this exact target cannot carry the stock
        detachment.  A missing exploded model or turret node is not an error
        to escalate: the vehicle keeps the plain wreck it has today.  Call
        this before writing the special health value, because
        ``onHealthChanged`` replaces the compound this pose is read from.
        """
        if self._closed or len(self._turrets) >= self._max_active:
            return None
        descriptor = getattr(entity, 'typeDescriptor', None)
        appearance = getattr(entity, 'appearance', None)
        if descriptor is None or appearance is None:
            return None
        if not getattr(entity, 'isStarted', False):
            # An unspotted remote never started its visual, so retail would
            # not have it in AOI and stock's own detach handshake would spend
            # its whole search window waiting for it.
            return None
        turret = getattr(descriptor, 'turret', None)
        gun = getattr(descriptor, 'gun', None)
        compound = getattr(appearance, 'compoundModel', None)
        node = getattr(compound, 'node', None)
        if turret is None or gun is None or not callable(node):
            return None
        if (self._exploded_model(turret) is None or
                self._exploded_model(gun) is None):
            return None
        compact_descr = getattr(descriptor, 'makeCompactDescr', None)
        if not callable(compact_descr):
            return None
        try:
            pose = self._math.Matrix(node(_TURRET_PART_NAME))
            translation = pose.translation
            launch = (
                float(translation.x), float(translation.y),
                float(translation.z))
            attitude = (float(pose.yaw), float(pose.pitch), float(pose.roll))
            descr_string = compact_descr()
        except Exception as error:
            self._note('launch pose unavailable', error)
            return None
        return {
            'entity_id': int(getattr(entity, 'id', 0)),
            'compact_descr': descr_string,
            'launch': launch,
            'attitude': attitude,
            'clearance': _turret_clearance(turret),
            'space_id': int(getattr(self._avatar, 'spaceID', 0)),
        }

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
