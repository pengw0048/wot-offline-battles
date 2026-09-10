"""Ammo-bay turret detachment: the frozen arc and the stock handshake."""

from pathlib import Path
import math
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import shot_geometry
from gui.mods.offline_lan_0922 import turret_detachment
from gui.mods.offline_lan_0922.entities import detached_turret


# Exact #1513 values, verified against scripts.pkg by tools/audit_client_abi.py.
AMMO_BAY_DESTROYED = -5
TURRET_DETACHED = -13
MAX_COLLISION_ENERGY = 98.10000000000001
MIN_COLLISION_SPEED = 3.5


def _flat_ground(height=0.0):
    """Stand in for BigWorld.wg_collideSegment over level terrain."""

    def collide(start, end):
        if start[1] < height or end[1] > height:
            return None
        span = start[1] - end[1]
        ratio = 0.0 if span <= 0.0 else (start[1] - height) / span
        return (start[0] + (end[0] - start[0]) * ratio, height,
                start[2] + (end[2] - start[2]) * ratio)

    return collide


class LaunchImpulseTest(unittest.TestCase):

    def test_the_same_seed_draws_the_same_impulse(self):
        first = turret_detachment.launch_impulse(918273)
        second = turret_detachment.launch_impulse(918273)
        self.assertEqual(first, second)

    def test_different_seeds_draw_different_impulses(self):
        drawn = set()
        for seed in range(64):
            impulse = turret_detachment.launch_impulse(seed)
            drawn.add(impulse['velocity'])
        self.assertGreater(len(drawn), 60)

    def test_every_impulse_stays_inside_the_shipped_effect_window(self):
        """A landing must be audible and must not saturate the drop RTPC.

        ``_TurretDetachmentEffects.__normalizeEnergy`` clamps the reported
        energy into ``[0.5 * 3.5 ** 2, 98.1]``.  An impulse that always
        clipped the ceiling would make every turret land identically loud,
        and one below the floor would play no impact at all.
        """
        floor = 0.5 * MIN_COLLISION_SPEED ** 2
        for seed in range(256):
            impulse = turret_detachment.launch_impulse(seed)
            # A medium tank's turret ring sits about 2.2 m up and the turret's
            # own underside about 0.85 m below its origin.
            flight = turret_detachment.resolve_flight(
                (0.0, 2.2, 0.0), impulse['velocity'], _flat_ground(),
                clearance=0.85)
            self.assertTrue(flight['landed'])
            self.assertGreater(flight['energy'], floor)
            self.assertLess(flight['energy'], MAX_COLLISION_ENERGY)

    def test_the_throw_is_vertical_with_a_bounded_drift(self):
        impulse = turret_detachment.launch_impulse(4242)
        velocity = impulse['velocity']
        self.assertAlmostEqual(
            velocity[1], turret_detachment.LAUNCH_VERTICAL_SPEED)
        drift = math.sqrt(velocity[0] ** 2 + velocity[2] ** 2)
        self.assertGreaterEqual(
            drift, turret_detachment.LAUNCH_DRIFT_MIN_SPEED - 1e-9)
        self.assertLessEqual(
            drift, turret_detachment.LAUNCH_DRIFT_MAX_SPEED + 1e-9)


class FlightResolutionTest(unittest.TestCase):

    def test_the_arc_apexes_where_free_fall_says_it_should(self):
        speed = turret_detachment.LAUNCH_VERTICAL_SPEED
        apex = speed ** 2 / (2.0 * turret_detachment.GRAVITY)
        peak = turret_detachment.flight_position(
            (0.0, 0.0, 0.0), (0.0, speed, 0.0),
            speed / turret_detachment.GRAVITY)
        self.assertAlmostEqual(peak[1], apex, places=6)

    def test_the_clearance_rests_the_turret_on_the_surface(self):
        flight = turret_detachment.resolve_flight(
            (0.0, 3.0, 0.0), (0.0, 6.0, 0.0), _flat_ground(),
            clearance=0.85)
        self.assertTrue(flight['landed'])
        self.assertAlmostEqual(flight['contact'][1], 0.0, places=6)
        self.assertAlmostEqual(flight['rest'][1], 0.85, places=6)

    def test_clearance_does_not_shift_the_contact_time(self):
        # Translating the origin and underside together must leave the
        # queried arc, contact time, and impact energy unchanged.
        reference = turret_detachment.resolve_flight(
            (0.0, 2.0, 0.0), (2.0, 6.0, 0.0), _flat_ground())
        lifted = turret_detachment.resolve_flight(
            (0.0, 2.85, 0.0), (2.0, 6.0, 0.0), _flat_ground(),
            clearance=0.85)
        self.assertAlmostEqual(reference['duration'], lifted['duration'])
        self.assertAlmostEqual(reference['energy'], lifted['energy'])

    def test_impact_energy_is_the_specific_kinetic_energy(self):
        self.assertAlmostEqual(
            turret_detachment.impact_energy((3.0, -4.0, 0.0)), 12.5)

    def test_a_missing_surface_still_publishes_a_terminal_pose(self):
        flight = turret_detachment.resolve_flight(
            (0.0, 5.0, 0.0), (0.0, 4.0, 0.0), lambda start, end: None,
            limit=1.0)
        self.assertFalse(flight['landed'])
        self.assertIsNone(flight['contact'])
        self.assertEqual(flight['duration'], 1.0)
        self.assertEqual(
            flight['rest'],
            turret_detachment.flight_position((0.0, 5.0, 0.0),
                                              (0.0, 4.0, 0.0), 1.0))

    def test_a_rising_wall_contact_drops_the_drift_instead_of_landing(self):
        """A turret must fall down a facade, not park inside it.

        ``wg_collideSegment`` reports no normal, so a contact reached while
        the turret is still rising is classified as a wall.  Landing there
        would rest the turret at the wall's face plus its own clearance,
        hovering in the building, and report a ground material for a vertical
        surface.
        """
        ground = _flat_ground()

        def collide(start, end):
            if max(start[0], end[0]) >= 1.0:
                return (1.0, 0.5 * (start[1] + end[1]), 0.0)
            return ground(start, end)

        flight = turret_detachment.resolve_flight(
            (0.0, 2.0, 0.0), (6.0, 8.0, 0.0), collide)
        self.assertTrue(flight['landed'])
        # It came down on the ground, next to the wall, not inside it.
        self.assertAlmostEqual(flight['contact'][1], 0.0, places=6)
        self.assertLessEqual(flight['rest'][0], 1.0)
        self.assertGreater(len(flight['segments']), 1)
        # The sideways motion stops at the wall; the fall continues.
        self.assertEqual(flight['segments'][1]['velocity'][0], 0.0)
        self.assertEqual(flight['segments'][1]['velocity'][2], 0.0)
        self.assertLess(flight['impact_velocity'][1], 0.0)

    def test_a_descending_contact_is_the_ground(self):
        flight = turret_detachment.resolve_flight(
            (0.0, 6.0, 0.0), (2.0, -4.0, 0.0), _flat_ground())
        self.assertTrue(flight['landed'])
        self.assertEqual(len(flight['segments']), 1)
        self.assertAlmostEqual(flight['contact'][1], 0.0, places=6)

    def test_a_turret_wedged_against_a_wall_still_terminates(self):
        """A coplanar restart must not spin the search."""
        flight = turret_detachment.resolve_flight(
            (0.0, 3.0, 0.0), (4.0, 1.0, 0.0),
            lambda start, end: (start[0], start[1], start[2]))
        self.assertTrue(flight['landed'])
        self.assertLessEqual(
            len(flight['segments']),
            turret_detachment.MAX_WALL_DEFLECTIONS + 1)


class PoseTest(unittest.TestCase):

    def test_a_landed_turret_never_moves_again(self):
        flight = turret_detachment.resolve_flight(
            (0.0, 2.0, 0.0), (0.0, 5.0, 0.0), _flat_ground())
        first = turret_detachment.pose_at(flight, (0.5, 0.0, 0.0),
                                          (1.0, 2.0, 3.0), 60.0)
        second = turret_detachment.pose_at(flight, (0.5, 0.0, 0.0),
                                           (1.0, 2.0, 3.0), 900.0)
        self.assertEqual(first, second)
        self.assertEqual(first[0], tuple(flight['rest']))

    def test_a_landed_turret_settles_flat(self):
        """Pitch and roll snap to a half turn, so it lies on roof or ring."""
        attitude = turret_detachment.rest_attitude(
            (0.0, 0.0, 0.0), (0.0, 1.9, 4.4), 1.0)
        for angle in attitude[1:]:
            self.assertAlmostEqual(
                math.sin(angle), 0.0, places=9)

    def test_the_tumble_runs_while_the_turret_is_in_the_air(self):
        flight = turret_detachment.resolve_flight(
            (0.0, 2.0, 0.0), (0.0, 8.0, 0.0), _flat_ground())
        early = turret_detachment.pose_at(flight, (0.0, 0.0, 0.0),
                                          (0.0, 2.0, 0.0), 0.25)
        self.assertAlmostEqual(early[1][1], 0.5)
        self.assertGreater(early[0][1], 2.0)


class _Models(object):

    def __init__(self, exploded='exploded.model'):
        self.exploded = exploded


class _HitTester(object):

    def __init__(self, bbox=((-1.2, -0.8, -1.2), (1.2, 0.9, 1.2), 3.0)):
        self.bbox = bbox


class _Component(object):

    def __init__(self, exploded='exploded.model', tester=None):
        self.models = _Models(exploded)
        self.hitTester = _HitTester() if tester is None else tester


class _Chassis(object):

    def __init__(self, hull_position=None):
        self.hullPosition = (_Vector(0.0, 0.6, 0.0)
                             if hull_position is None else hull_position)


class _Hull(object):

    def __init__(self, ring=None):
        self.turretPositions = (
            (_Vector(0.0, 0.9, -1.5),) if ring is None else ring)


class _Descriptor(object):

    def __init__(self, turret=None, gun=None):
        self.chassis = _Chassis()
        self.hull = _Hull()
        self.turret = _Component() if turret is None else turret
        self.gun = _Component('gun_exploded.model') if gun is None else gun

    @staticmethod
    def makeCompactDescr():
        return 'compact-descr'


# The authoritative terminal pose one detachment starts from.  The turret ring
# sits ``hullPosition + hull.turretPositions[0]`` = (0, 1.5, -1.5) above the
# vehicle's own origin, exactly where #1513's ``Vehicle.getComponents`` puts
# it.
_POSE = {'x': 12.0, 'y': 3.0, 'z': -30.0, 'yaw': 0.0, 'pitch': 0.0,
         'roll': 0.0, 'turret_yaw': 1.25}


class _Matrix(object):
    """Only the members #1513's Math.Matrix exposes to this code."""

    def __init__(self, source=None):
        self.translation = _Vector(0.0, 0.0, 0.0)
        self.yaw = 0.0
        self.pitch = 0.0
        self.roll = 0.0
        self.rotations = []
        if isinstance(source, _Node):
            self.translation = source.translation
            self.yaw, self.pitch, self.roll = source.attitude

    def setRotateYPR(self, angles):
        self.rotations.append(tuple(angles))
        self.yaw, self.pitch, self.roll = angles


class _Vector(object):

    def __init__(self, x, y, z):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __eq__(self, other):
        return (self.x, self.y, self.z) == (other.x, other.y, other.z)

    def __repr__(self):
        return 'Vector(%s, %s, %s)' % (self.x, self.y, self.z)


class _Math(object):
    Matrix = _Matrix
    Vector3 = _Vector


class _Node(object):

    def __init__(self, translation, attitude):
        self.translation = translation
        self.attitude = attitude


class _Compound(object):

    def __init__(self, node):
        self._node = node
        self.matrix = None
        self.visible = True
        self.requested = []

    def node(self, name):
        self.requested.append(name)
        return self._node


class _Appearance(object):

    def __init__(self, compound):
        self.compoundModel = compound


class _Vehicle(object):
    """A stock #1513 Vehicle as far as the detachment handshake is concerned.

    The two detachment properties are reproduced from Vehicle.pyc rather than
    stubbed true: ``isTurretMarkedForDetachment`` is the special-health test
    and ``isTurretDetachmentConfirmationNeeded`` is the negated private flag.
    A fixture that always accepted the handshake could not detect the wrong
    ordering.
    """

    def __init__(self, entity_id=17, node=None):
        self.id = entity_id
        self.health = 240
        self.isStarted = True
        self.typeDescriptor = _Descriptor()
        self.compound = _Compound(
            _Node(_Vector(12.0, 4.5, -30.0), (1.25, 0.05, -0.02))
            if node is None else node)
        self.appearance = _Appearance(self.compound)
        self._Vehicle__turretDetachmentConfirmed = False

    @property
    def isTurretMarkedForDetachment(self):
        return self.health < 0 and (self.health | TURRET_DETACHED) == \
            TURRET_DETACHED

    @property
    def isTurretDetachmentConfirmationNeeded(self):
        return not self._Vehicle__turretDetachmentConfirmed


class _TurretEntity(object):

    def __init__(self, entity_id, properties, position, direction, model=True):
        self.id = entity_id
        self.properties = properties
        self.position = position
        self.direction = direction
        self.model = _Compound(None) if model else None
        self.targetCaps = [1]
        self.impacts = []

    def onStaticCollision(self, energy, point, normal):
        self.impacts.append((energy, point, normal))


class _BigWorld(object):

    def __init__(self, fail_create=False, model=True, defer_enter=False):
        self.entities = {}
        self.pending = {}
        self.created = []
        self.destroyed = []
        self.destroy_attempts = []
        self._next_id = 900
        self._fail_create = fail_create
        self._model = model
        self._defer_enter = defer_enter

    def createEntity(self, class_name, space_id, vehicle_id, position,
                     direction, state):
        self.created.append(
            (class_name, space_id, vehicle_id, position, direction, state))
        if self._fail_create:
            raise RuntimeError('resource list rejected')
        self._next_id += 1
        entity = _TurretEntity(self._next_id, state, position, direction,
                               self._model)
        if self._defer_enter:
            self.pending[self._next_id] = entity
        else:
            self.entities[self._next_id] = entity
        return self._next_id

    def enter_world(self, entity_id):
        entity = self.pending.pop(entity_id)
        self.entities[entity_id] = entity
        return entity

    def entity(self, entity_id):
        return self.entities.get(entity_id)

    def destroyEntity(self, entity_id):
        self.destroy_attempts.append(entity_id)
        if entity_id in self.pending:
            raise RuntimeError('pending entity cannot be destroyed before entry')
        self.destroyed.append(entity_id)
        self.entities.pop(entity_id, None)


class _Avatar(object):
    spaceID = 5


class DetachedTurretPresentationTest(unittest.TestCase):

    def _presentation(self, bigworld=None, collide=None):
        self.failures = []
        return detached_turret.DetachedTurretPresentation(
            bigworld or _BigWorld(), _Math(), _Avatar(),
            collide or _flat_ground(),
            log=lambda what, error: self.failures.append((what, str(error))))

    def test_prepare_freezes_the_authoritative_ring_pose(self):
        presentation = self._presentation()
        vehicle = _Vehicle()
        plan = presentation.prepare(vehicle, _POSE)
        self.assertIsNotNone(plan)
        self.assertEqual(plan['entity_id'], 17)
        self.assertEqual(plan['compact_descr'], 'compact-descr')
        self.assertEqual(plan['launch'], (12.0, 4.5, -31.5))
        self.assertEqual(plan['attitude'], (1.25, 0.0, 0.0))
        self.assertEqual(plan['space_id'], 5)
        # The launch point is derived from the replicated pose and the
        # descriptor, never from this peer's interpolated render compound.
        # That is what lets the worker's obstacle and every client's drawn
        # turret share one arc without a wire field.
        self.assertEqual(vehicle.compound.requested, [])

    def test_the_ring_follows_the_authoritative_hull_attitude(self):
        presentation = self._presentation()
        pose = dict(_POSE, yaw=math.pi / 2.0)
        plan = presentation.prepare(_Vehicle(), pose)
        # Rotating (0, 1.5, -1.5) a quarter turn about Y moves the ring onto
        # the hull's own left side.
        self.assertAlmostEqual(plan['launch'][0], 10.5)
        self.assertAlmostEqual(plan['launch'][1], 4.5)
        self.assertAlmostEqual(plan['launch'][2], -30.0)
        self.assertAlmostEqual(plan['attitude'][0], math.pi / 2.0 + 1.25)

    def test_a_descriptor_without_a_turret_ring_is_skipped(self):
        presentation = self._presentation()
        vehicle = _Vehicle()
        vehicle.typeDescriptor.hull.turretPositions = ()
        self.assertIsNone(presentation.prepare(vehicle, _POSE))
        self.assertEqual(self.failures, [])

    def test_the_clearance_comes_from_the_turret_hit_tester(self):
        presentation = self._presentation()
        plan = presentation.prepare(_Vehicle(), _POSE)
        self.assertAlmostEqual(plan['clearance'], 0.8)

    def test_a_descriptor_without_an_exploded_turret_is_skipped(self):
        presentation = self._presentation()
        vehicle = _Vehicle()
        vehicle.typeDescriptor.turret.models.exploded = None
        self.assertIsNone(presentation.prepare(vehicle, _POSE))
        self.assertEqual(self.failures, [])

    def test_an_unstarted_remote_is_skipped(self):
        presentation = self._presentation()
        vehicle = _Vehicle()
        vehicle.isStarted = False
        self.assertIsNone(presentation.prepare(vehicle, _POSE))

    def test_the_active_turret_count_is_bounded(self):
        bigworld = _BigWorld()
        presentation = self._presentation(bigworld)
        for index in range(detached_turret.MAX_ACTIVE_TURRETS):
            plan = presentation.prepare(_Vehicle(entity_id=index), _POSE)
            self.assertIsNotNone(plan)
            self.assertTrue(presentation.launch(plan, index, 0.0))
        self.assertEqual(
            presentation.active(), detached_turret.MAX_ACTIVE_TURRETS)
        self.assertIsNone(presentation.prepare(_Vehicle(entity_id=99), _POSE))

    def test_launch_creates_the_stock_entity_with_its_client_properties(self):
        bigworld = _BigWorld()
        presentation = self._presentation(bigworld)
        plan = presentation.prepare(_Vehicle(), _POSE)
        self.assertTrue(presentation.launch(plan, 555, 100.0))
        self.assertEqual(len(bigworld.created), 1)
        name, space_id, vehicle_id, position, direction, state = \
            bigworld.created[0]
        self.assertEqual(name, 'DetachedTurret')
        self.assertEqual(space_id, 5)
        self.assertEqual(vehicle_id, 0)
        self.assertEqual((position.x, position.y, position.z),
                         (12.0, 4.5, -31.5))
        # BigWorld.createEntity takes (roll, pitch, yaw).
        self.assertEqual(direction, (0.0, 0.0, 1.25))
        self.assertEqual(sorted(state), [
            'isCollidingWithWorld', 'isUnderWater', 'vehicleCompDescr',
            'vehicleID'])
        self.assertEqual(state['vehicleID'], 17)
        self.assertEqual(state['vehicleCompDescr'], 'compact-descr')
        # isCollidingWithWorld would be the only gate that makes stock read
        # native Entity.velocity off the filter this port never feeds.
        self.assertIs(state['isCollidingWithWorld'], False)
        self.assertIs(state['isUnderWater'], False)

    def test_a_failed_creation_leaves_no_untracked_entity(self):
        bigworld = _BigWorld(fail_create=True)
        presentation = self._presentation(bigworld)
        plan = presentation.prepare(_Vehicle(), _POSE)
        self.assertFalse(presentation.launch(plan, 1, 0.0))
        self.assertEqual(presentation.active(), 0)
        self.assertEqual(len(self.failures), 1)
        self.assertEqual(
            self.failures[0][0], 'detached turret creation failed')

    def test_advance_drives_the_compound_and_gates_it_out_of_collision(self):
        bigworld = _BigWorld()
        presentation = self._presentation(bigworld)
        plan = presentation.prepare(_Vehicle(), _POSE)
        presentation.launch(plan, 77, 10.0)
        self.assertEqual(presentation.advance(10.0), 1)
        entity = bigworld.entities[901]
        self.assertIsInstance(entity.model.matrix, _Matrix)
        self.assertEqual(entity.targetCaps, [])
        self.assertTrue(entity._offlineNativeRemote)
        self.assertFalse(entity._offlineNativeDrawVisible)

    def test_the_landing_reports_one_impact_to_the_stock_effect(self):
        bigworld = _BigWorld()
        presentation = self._presentation(bigworld)
        plan = presentation.prepare(_Vehicle(), _POSE)
        presentation.launch(plan, 77, 10.0)
        presentation.advance(10.0)
        entity = bigworld.entities[901]
        self.assertEqual(entity.impacts, [])
        self.assertEqual(presentation.advance(60.0), 1)
        # The rest pose is final, so later frames write nothing at all.
        self.assertEqual(presentation.advance(90.0), 0)
        self.assertEqual(len(entity.impacts), 1)
        energy, point, normal = entity.impacts[0]
        self.assertGreater(energy, 0.5 * MIN_COLLISION_SPEED ** 2)
        self.assertAlmostEqual(point.y, 0.0, places=6)
        self.assertEqual((normal.x, normal.y, normal.z), (0.0, 1.0, 0.0))

    def test_a_turret_whose_compound_is_not_resident_yet_is_not_bound(self):
        bigworld = _BigWorld(model=False)
        presentation = self._presentation(bigworld)
        plan = presentation.prepare(_Vehicle(), _POSE)
        presentation.launch(plan, 5, 0.0)
        self.assertEqual(presentation.advance(0.5), 0)
        self.assertEqual(presentation.active(), 1)

    def test_async_entry_waits_then_binds_flies_and_lands_once(self):
        """#1513 returns an ID before prerequisites make entity(id) visible."""
        bigworld = _BigWorld(defer_enter=True)
        presentation = self._presentation(bigworld)
        plan = presentation.prepare(_Vehicle(), _POSE)
        self.assertTrue(presentation.launch(plan, 77, 10.0))
        self.assertIsNone(bigworld.entity(901))

        for now in (10.01, 10.1, 10.2):
            self.assertEqual(presentation.advance(now), 0)
            self.assertEqual(presentation.active(), 1)
        entity = bigworld.enter_world(901)
        self.assertEqual(presentation.advance(10.3), 1)
        matrix = entity.model.matrix
        self.assertIsInstance(matrix, _Matrix)
        initial_position = matrix.translation
        self.assertEqual(entity.impacts, [])
        self.assertEqual(presentation.advance(10.6), 1)
        self.assertIs(entity.model.matrix, matrix)
        self.assertNotEqual(matrix.translation, initial_position)
        self.assertEqual(presentation.advance(60.0), 1)
        self.assertEqual(len(entity.impacts), 1)
        self.assertEqual(presentation.advance(90.0), 0)
        self.assertEqual(len(entity.impacts), 1)
        self.assertEqual(bigworld.destroy_attempts, [])
        self.assertEqual(self.failures, [])

    def test_a_destroyed_entity_is_dropped_without_an_error(self):
        bigworld = _BigWorld()
        presentation = self._presentation(bigworld)
        plan = presentation.prepare(_Vehicle(), _POSE)
        presentation.launch(plan, 5, 0.0)
        self.assertEqual(presentation.advance(0.0), 1)
        entity = bigworld.entity(901)
        rotations = list(entity.model.matrix.rotations)
        bigworld.entities.clear()
        self.assertEqual(presentation.advance(1.0), 0)
        self.assertEqual(presentation.active(), 0)
        self.assertEqual(entity.model.matrix.rotations, rotations)
        self.assertEqual(self.failures, [])

    def test_a_seen_entity_lost_before_model_readiness_is_retired(self):
        bigworld = _BigWorld(model=False, defer_enter=True)
        presentation = self._presentation(bigworld)
        plan = presentation.prepare(_Vehicle(), _POSE)
        presentation.launch(plan, 5, 0.0)
        bigworld.enter_world(901)
        self.assertEqual(presentation.advance(0.1), 0)
        self.assertEqual(presentation.active(), 1)
        bigworld.entities.clear()

        self.assertEqual(presentation.advance(0.2), 0)
        self.assertEqual(presentation.active(), 0)
        self.assertEqual(bigworld.destroy_attempts, [])
        self.assertEqual(self.failures, [])

    def test_one_vehicle_throws_one_turret(self):
        bigworld = _BigWorld()
        presentation = self._presentation(bigworld)
        plan = presentation.prepare(_Vehicle(), _POSE)
        self.assertTrue(presentation.launch(plan, 5, 0.0))
        self.assertFalse(presentation.launch(plan, 5, 0.0))
        self.assertEqual(presentation.active(), 1)
        self.assertEqual(len(bigworld.created), 1)

    def test_destroy_all_is_safe_twice_and_after_a_partial_start(self):
        bigworld = _BigWorld()
        presentation = self._presentation(bigworld)
        self.assertEqual(presentation.destroy_all(), 0)
        self.assertEqual(presentation.destroy_all(), 0)
        presentation = self._presentation(bigworld)
        plan = presentation.prepare(_Vehicle(), _POSE)
        presentation.launch(plan, 5, 0.0)
        self.assertEqual(presentation.destroy_all(), 1)
        self.assertEqual(bigworld.destroyed, [901])
        self.assertEqual(presentation.destroy_all(), 0)
        self.assertEqual(bigworld.destroyed, [901])

    def test_pending_cleanup_waits_for_entry_and_never_starts_the_visual(self):
        for retry in ('advance', 'destroy_all'):
            with self.subTest(retry=retry):
                bigworld = _BigWorld(defer_enter=True)
                presentation = self._presentation(bigworld)
                plan = presentation.prepare(_Vehicle(), _POSE)
                presentation.launch(plan, 5, 0.0)

                self.assertEqual(presentation.destroy_all(), 0)
                self.assertEqual(presentation.destroy_all(), 0)
                self.assertEqual(bigworld.destroy_attempts, [])
                self.assertIsNone(presentation.prepare(_Vehicle(entity_id=18), _POSE))
                self.assertFalse(presentation.launch(plan, 5, 0.1))
                self.assertEqual(len(bigworld.created), 1)

                entity = bigworld.enter_world(901)
                if retry == 'advance':
                    self.assertEqual(presentation.advance(0.2), 0)
                else:
                    self.assertEqual(presentation.destroy_all(), 1)
                self.assertEqual(bigworld.destroyed, [901])
                self.assertIsNone(entity.model.matrix)
                self.assertEqual(entity.impacts, [])
                self.assertEqual(presentation.active(), 0)
                self.assertEqual(presentation.advance(0.3), 0)
                self.assertEqual(presentation.destroy_all(), 0)
                self.assertEqual(bigworld.destroy_attempts, [901])
                self.assertEqual(self.failures, [])

    def test_cleanup_retires_resident_and_pending_entities_when_each_is_safe(self):
        bigworld = _BigWorld(defer_enter=True)
        presentation = self._presentation(bigworld)
        for vehicle_id in (17, 18):
            plan = presentation.prepare(_Vehicle(entity_id=vehicle_id), _POSE)
            presentation.launch(plan, vehicle_id, 0.0)
        resident = bigworld.enter_world(901)

        self.assertEqual(presentation.destroy_all(), 1)
        self.assertEqual(bigworld.destroy_attempts, [901])
        self.assertIsNone(resident.model.matrix)
        self.assertIn(902, bigworld.pending)
        pending = bigworld.enter_world(902)
        self.assertEqual(presentation.destroy_all(), 1)
        self.assertEqual(bigworld.destroy_attempts, [901, 902])
        self.assertIsNone(pending.model.matrix)
        self.assertEqual(presentation.destroy_all(), 0)
        self.assertEqual(self.failures, [])

    def test_a_reused_id_never_writes_or_destroys_the_replacement_entity(self):
        for retire in ('advance', 'destroy_all'):
            with self.subTest(retire=retire):
                bigworld = _BigWorld()
                presentation = self._presentation(bigworld)
                plan = presentation.prepare(_Vehicle(), _POSE)
                presentation.launch(plan, 5, 0.0)
                self.assertEqual(presentation.advance(0.0), 1)
                old_entity = bigworld.entity(901)
                rotations = list(old_entity.model.matrix.rotations)
                replacement = _TurretEntity(
                    901, {}, _Vector(0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
                bigworld.entities[901] = replacement

                if retire == 'advance':
                    self.assertEqual(presentation.advance(0.1), 0)
                else:
                    self.assertEqual(presentation.destroy_all(), 0)
                self.assertEqual(presentation.active(), 0)
                self.assertEqual(presentation.destroy_all(), 0)
                self.assertIs(bigworld.entity(901), replacement)
                self.assertIsNone(replacement.model.matrix)
                self.assertEqual(old_entity.model.matrix.rotations, rotations)
                self.assertEqual(bigworld.destroy_attempts, [])
                self.assertEqual(self.failures, [])


class HandshakeOrderTest(unittest.TestCase):
    """The order #1513 requires around the single models refresh."""

    def test_the_special_health_makes_the_turret_marked_for_detachment(self):
        vehicle = _Vehicle()
        self.assertFalse(vehicle.isTurretMarkedForDetachment)
        vehicle.health = AMMO_BAY_DESTROYED
        self.assertFalse(vehicle.isTurretMarkedForDetachment)
        vehicle.health = TURRET_DETACHED
        self.assertTrue(vehicle.isTurretMarkedForDetachment)

    def test_preconfirmation_removes_the_stock_confirmation_need(self):
        """This is what keeps SynchronousDetachment off the unfed filter.

        ``SynchronousDetachment._onDirectTick`` only calls
        ``confirmTurretDetachment`` and ``transferInputs`` while
        ``isTurretDetachmentConfirmationNeeded`` is true, and
        ``transferInputs`` is the native call this port must not make on a
        ``WGVehicleFilter`` it never feeds.
        """
        vehicle = _Vehicle()
        vehicle.health = TURRET_DETACHED
        self.assertTrue(vehicle.isTurretDetachmentConfirmationNeeded)
        vehicle._Vehicle__turretDetachmentConfirmed = True
        self.assertFalse(vehicle.isTurretDetachmentConfirmationNeeded)
        # It still accepts the vehicle, so the timer finishes synchronously
        # inside createEntity instead of hiding the model for its search.
        self.assertTrue(vehicle.isTurretMarkedForDetachment)


if __name__ == '__main__':
    unittest.main()


class _AffineMatrix(object):
    """The Math.Matrix members ``DetachedTurretObstacles`` actually uses.

    BigWorld transforms row vectors, so ``applyPoint`` is ``p * R + t`` and
    ``preMultiply(B)`` composes B before self.  ``setRotateYPR`` uses the same
    roll-then-pitch-then-yaw order ``shot_geometry`` documents for the #1513
    vehicle matrix, so a rest frame built here matches the launch point the
    plan froze.
    """

    def __init__(self, source=None):
        if isinstance(source, _AffineMatrix):
            self._rotation = [list(row) for row in source._rotation]
            self._translation = list(source._translation)
        else:
            self._rotation = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0],
                              [0.0, 0.0, 1.0]]
            self._translation = [0.0, 0.0, 0.0]

    @property
    def translation(self):
        return _Vector(*self._translation)

    @translation.setter
    def translation(self, value):
        self._translation = [float(value.x), float(value.y), float(value.z)]

    def setRotateYPR(self, angles):
        yaw, pitch, roll = (float(value) for value in angles)
        rows = []
        for basis in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)):
            rows.append(list(shot_geometry.transform_vehicle_vector(
                basis, yaw, pitch, roll)))
        self._rotation = rows
        self._translation = [0.0, 0.0, 0.0]

    def setTranslate(self, value):
        self._rotation = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        self._translation = [float(value.x), float(value.y), float(value.z)]

    def invert(self):
        rotation = [[self._rotation[column][row] for column in range(3)]
                    for row in range(3)]
        translation = [
            -sum(self._translation[axis] * rotation[axis][column]
                 for axis in range(3)) for column in range(3)]
        self._rotation = rotation
        self._translation = translation

    def preMultiply(self, other):
        rotation = [
            [sum(other._rotation[row][axis] * self._rotation[axis][column]
                 for axis in range(3)) for column in range(3)]
            for row in range(3)]
        translation = [
            sum(other._translation[axis] * self._rotation[axis][column]
                for axis in range(3)) + self._translation[column]
            for column in range(3)]
        self._rotation = rotation
        self._translation = translation

    def applyPoint(self, point):
        source = (float(point.x), float(point.y), float(point.z))
        return _Vector(*[
            sum(source[axis] * self._rotation[axis][column]
                for axis in range(3)) + self._translation[column]
            for column in range(3)])


class _AffineMath(object):
    Matrix = _AffineMatrix
    Vector3 = _Vector


class _BoxHitTester(object):
    """One #1513 hit tester whose armour is a single local box."""

    def __init__(self, bbox=((-1.2, -0.8, -1.2), (1.2, 0.9, 1.2), 3.0),
                 material_kind=1):
        self.bbox = bbox
        self._material_kind = material_kind
        self.queries = []

    def localHitTest(self, start, end):
        self.queries.append(((start.x, start.y, start.z),
                             (end.x, end.y, end.z)))
        lower, upper = self.bbox[0], self.bbox[1]
        origin = (start.x, start.y, start.z)
        direction = (end.x - start.x, end.y - start.y, end.z - start.z)
        length = math.sqrt(sum(value * value for value in direction))
        if length <= 0.0:
            return None
        near, far = 0.0, 1.0
        for axis in range(3):
            if abs(direction[axis]) < 1.0e-9:
                if not lower[axis] <= origin[axis] <= upper[axis]:
                    return None
                continue
            first = (lower[axis] - origin[axis]) / direction[axis]
            second = (upper[axis] - origin[axis]) / direction[axis]
            if first > second:
                first, second = second, first
            near = max(near, first)
            far = min(far, second)
            if near > far:
                return None
        return [(near * length, None, 1.0, self._material_kind)]


def _obstacle_descriptor():
    descriptor = _Descriptor()
    descriptor.turret.hitTester = _BoxHitTester()
    descriptor.turret.gunPosition = _Vector(0.0, 0.0, 1.0)
    descriptor.gun.hitTester = _BoxHitTester(
        bbox=((-0.2, -0.2, 0.0), (0.2, 0.2, 3.0), 3.0))
    return descriptor


class DetachedTurretObstacleTest(unittest.TestCase):
    """Retail's DetachedTurret answers collideSegment; so must the worker.

    ``DetachedTurret.collideSegment`` in scripts.pkg tests the turret model
    matrix and the gun node against the same ``__componentsDesc`` hit testers
    a Vehicle uses, so a shot that reaches a thrown turret stops on it.  This
    port has no such entity inside the hidden worker, so the worker resolves
    the identical arc and owns the landed turret's geometry directly.
    """

    def setUp(self):
        self.failures = []

    def _obstacles(self, collide=None):
        return detached_turret.DetachedTurretObstacles(
            _AffineMath(), collide or _flat_ground(),
            log=lambda what, error: self.failures.append((what, str(error))))

    def _plan(self, entity_id=17, pose=None):
        vehicle = _Vehicle(entity_id=entity_id)
        vehicle.typeDescriptor = _obstacle_descriptor()
        return detached_turret.detachment_plan(vehicle, pose or _POSE)

    def _settled(self, seed=555, now=100.0):
        obstacles = self._obstacles()
        plan = self._plan()
        self.assertTrue(obstacles.add(plan, seed, now))
        impulse = turret_detachment.launch_impulse(seed)
        flight = turret_detachment.resolve_flight(
            plan['launch'], impulse['velocity'], _flat_ground(),
            clearance=plan['clearance'])
        self.assertTrue(flight['landed'])
        return obstacles, flight, now

    def test_a_settled_turret_stops_a_shot_through_its_rest_pose(self):
        obstacles, flight, now = self._settled()
        rest = flight['rest']
        start = _Vector(rest[0] - 20.0, rest[1], rest[2])
        end = _Vector(rest[0] + 20.0, rest[1], rest[2])
        distance = obstacles.block_distance(
            start, end, now + flight['duration'])
        self.assertIsNotNone(distance)
        # The box reaches 1.2 m out along its own widest axis, so a level ray
        # from 20 m away enters somewhere inside the last two metres.
        self.assertGreater(distance, 17.0)
        self.assertLess(distance, 20.0)

    def test_a_turret_still_in_the_air_owns_no_obstacle(self):
        obstacles, flight, now = self._settled()
        rest = flight['rest']
        start = _Vector(rest[0] - 20.0, rest[1], rest[2])
        end = _Vector(rest[0] + 20.0, rest[1], rest[2])
        self.assertIsNone(obstacles.block_distance(
            start, end, now + flight['duration'] - 0.01))

    def test_a_shot_that_misses_the_rest_pose_is_not_blocked(self):
        obstacles, flight, now = self._settled()
        rest = flight['rest']
        start = _Vector(rest[0] - 20.0, rest[1] + 40.0, rest[2])
        end = _Vector(rest[0] + 20.0, rest[1] + 40.0, rest[2])
        self.assertIsNone(obstacles.block_distance(
            start, end, now + flight['duration']))

    def test_the_broad_phase_measures_the_barrel_from_the_ring(self):
        """The barrel reaches past its own mount, so the sphere must too.

        A sphere measured only from each box's own origin would reject a ray
        the exact gun hit test still answers, and a shell would pass through a
        barrel it should have stopped on.
        """
        gun = _Component('gun_exploded.model')
        gun.hitTester = _BoxHitTester(
            bbox=((-0.2, -0.2, 0.0), (0.2, 0.2, 3.0), 3.0))
        mounted = detached_turret._component_radius(gun, (0.0, 0.0, 1.0))
        self.assertAlmostEqual(
            mounted, math.sqrt(0.2 ** 2 + 0.2 ** 2 + 4.0 ** 2))
        self.assertGreater(
            mounted, detached_turret._component_radius(gun, (0.0, 0.0, 0.0)))

    def test_an_unreadable_box_never_narrows_the_broad_phase(self):
        blind = _Component('gun_exploded.model')
        blind.hitTester = None
        self.assertEqual(
            float('inf'), detached_turret._component_radius(blind, (0.0,) * 3))

    def test_one_wreck_owns_one_obstacle(self):
        obstacles = self._obstacles()
        self.assertTrue(obstacles.add(self._plan(), 555, 0.0))
        self.assertFalse(obstacles.add(self._plan(), 555, 0.0))
        self.assertEqual(obstacles.active(), 1)

    def test_the_resident_obstacle_count_is_bounded(self):
        obstacles = self._obstacles()
        for index in range(detached_turret.MAX_ACTIVE_TURRETS):
            self.assertTrue(obstacles.add(
                self._plan(entity_id=index), index, 0.0))
        self.assertFalse(obstacles.add(self._plan(entity_id=99), 99, 0.0))

    def test_clear_and_drop_are_safe_twice(self):
        obstacles = self._obstacles()
        self.assertTrue(obstacles.add(self._plan(), 555, 0.0))
        self.assertEqual(obstacles.drop(17), 1)
        self.assertEqual(obstacles.drop(17), 0)
        self.assertEqual(obstacles.clear(), 0)
        self.assertEqual(obstacles.clear(), 0)

    def test_the_arc_matches_the_presentation_for_the_same_inputs(self):
        # The worker's obstacle and every client's drawn turret must land in
        # the same place, and they only do so because both derive the arc
        # from the replicated pose, the descriptor and the round seed.
        plan = self._plan()
        impulse = turret_detachment.launch_impulse(555)
        flight = turret_detachment.resolve_flight(
            plan['launch'], impulse['velocity'], _flat_ground(),
            clearance=plan['clearance'])
        drawn, unused_attitude = turret_detachment.pose_at(
            flight, plan['attitude'], impulse['spin'],
            flight['duration'] + 5.0)
        self.assertEqual(tuple(drawn), tuple(flight['rest']))
