import copy
import math
import types
import unittest

from test_port_0922_turret_detachment import (
    _BigWorld, _Math, _POSE, _Vector, _Vehicle, _flat_ground)
from gui.mods.offline_lan_0922 import shot_geometry
from gui.mods.offline_lan_0922.entities import detached_turret
from gui.mods.offline_lan_0922.entities import turret_obstacles


class BoxTester:
    def __init__(self, lower, upper):
        self.bbox = (lower, upper, 1.0)

    def localHitTest(self, start, end):
        origin = (start.x, start.y, start.z)
        delta = (end.x - start.x, end.y - start.y, end.z - start.z)
        length = math.sqrt(sum(value * value for value in delta))
        near, far = 0.0, 1.0
        for axis in range(3):
            lower, upper = self.bbox[0][axis], self.bbox[1][axis]
            if abs(delta[axis]) < 1e-9:
                if not lower <= origin[axis] <= upper:
                    return None
                continue
            a, b = (lower - origin[axis]) / delta[axis], (upper - origin[axis]) / delta[axis]
            near, far = max(near, min(a, b)), min(far, max(a, b))
            if near > far:
                return None
        return [(near * length, None, 1.0, 1)]


def component(lower, upper):
    return types.SimpleNamespace(hitTester=BoxTester(lower, upper),
                                 models=types.SimpleNamespace(exploded='exploded.model'))


def descriptor():
    td = _Vehicle().typeDescriptor
    td.chassis.hitTester = BoxTester((-0.5, -0.5, -0.5), (0.5, 0.5, 0.5))
    td.chassis.hullPosition = _Vector(0, 0, 0)
    td.hull.hitTester = BoxTester((-0.5, -0.5, -0.5), (0.5, 0.5, 0.5))
    td.turret = component((-1, -1, -1), (1, 1, 1))
    td.turret.gunPosition = _Vector(0, 0, 4)
    td.gun = component((-0.2, -0.2, 0), (0.2, 0.2, 1))
    return td


def row(rest=(0, 1, 0), duration=2.0, attitude=(0, 0, 0)):
    return {'actor_kind': 'bot', 'actor_id': 17, 'created_time_ms': 1000,
            'attitude': attitude, 'spin': (0, 0, 0),
            'flight': {'origin': (0, 2, 0), 'velocity': (0, 1, 0),
                       'segments': [{'origin': (0, 2, 0), 'velocity': (0, 1, 0),
                                     'start': 0, 'duration': duration}],
                       'duration': duration, 'contact': (rest[0], 0, rest[2]),
                       'rest': rest, 'impact_velocity': (0, -1, 0),
                       'energy': 0.5, 'landed': True}}


def pose(x=0, y=1, z=0, yaw=0, pitch=0, roll=0):
    return dict(x=x, y=y, z=z, yaw=yaw, pitch=pitch, roll=roll)


class TurretObstacleTests(unittest.TestCase):
    def obstacle(self, accepted=None, td=None):
        obstacles = detached_turret.DetachedTurretObstacles(_Math())
        self.assertTrue(obstacles.add('bot:17', accepted or row(), td or descriptor()))
        return obstacles

    def test_source_geometry_is_required_before_raycast(self):
        vehicle = _Vehicle()
        vehicle.typeDescriptor = descriptor()
        vehicle.typeDescriptor.gun.hitTester = None
        called = []
        self.assertIsNone(detached_turret.freeze_obstacle_plan(
            vehicle, _POSE, 7, lambda *args: called.append(args)))
        self.assertEqual(called, [])

    def test_proposal_supports_the_final_rotated_gun_and_turret(self):
        vehicle = _Vehicle()
        vehicle.typeDescriptor = descriptor()
        vehicle.typeDescriptor.gun.hitTester = BoxTester((-0.2, -0.2, 0), (0.2, 3, 1))
        proposed = detached_turret.freeze_obstacle_plan(vehicle, _POSE, 555, _flat_ground())
        self.assertIsNotNone(proposed)
        flight = proposed['flight']
        attitude = detached_turret.turret_detachment.rest_attitude(
            proposed['attitude'], proposed['spin'], flight['duration'])
        ys = [flight['rest'][1] + shot_geometry.transform_vehicle_vector(corner, *attitude)[1]
              for _, _, offset, bounds in turret_obstacles.turret_components(vehicle.typeDescriptor)
              for corner in turret_obstacles._corners(bounds, offset)]
        self.assertAlmostEqual(min(ys), flight['contact'][1])

    def test_landing_clock_and_historical_chord_hit_time(self):
        obstacles = self.obstacle()
        start, end = _Vector(-3, 1, 0), _Vector(3, 1, 0)
        self.assertIsNone(obstacles.block_distance(start, end, 2999))
        self.assertAlmostEqual(obstacles.block_distance(start, end, 3000), 2)
        self.assertIsNone(obstacles.block_distance(start, end, 4000, start_time_ms=2000))
        self.assertAlmostEqual(obstacles.block_distance(start, end, 4000, start_time_ms=3000), 2)

    def test_separate_components_leave_the_empty_gap_open(self):
        obstacles = self.obstacle()
        self.assertIsNone(obstacles.block_distance(_Vector(-3, 1, 2.5), _Vector(3, 1, 2.5), 4000))
        self.assertIsNotNone(obstacles.block_distance(_Vector(-3, 1, 4.5), _Vector(3, 1, 4.5), 4000))
        self.assertFalse(obstacles.sweep_blocks(pose(z=2.5), pose(z=2.5), descriptor(), 4000))

    def test_radius_preserves_hits_at_mixed_bbox_corners(self):
        td = descriptor()
        td.turret.hitTester = BoxTester((-2, -0.2, -0.2), (0.2, 2, 0.2))
        td.turret.gunPosition = _Vector(0, 0, 0)
        td.gun.hitTester = BoxTester((-0.1, -0.1, -0.1), (0.1, 0.1, 0.1))
        obstacles = self.obstacle(td=td)
        self.assertAlmostEqual(obstacles.block_distance(_Vector(-1.8, 2.8, -1), _Vector(-1.8, 2.8, 1), 4000), 0.8)

    def test_continuous_translation_blocks_between_disjoint_endpoints(self):
        obstacles = self.obstacle()
        self.assertTrue(obstacles.sweep_blocks(pose(x=-10), pose(x=10), descriptor(), 4000))
        self.assertFalse(obstacles.sweep_blocks(pose(x=-10), pose(x=10), descriptor(), 2999))

    def test_continuous_yaw_catches_a_middle_only_corner(self):
        td = descriptor()
        td.chassis.hitTester = BoxTester((-0.1, -0.1, 0), (0.1, 0.1, 3))
        td.hull.hitTester = BoxTester((-0.1, -0.1, 0), (0.1, 0.1, 3))
        fixed = descriptor()
        fixed.turret.hitTester = BoxTester((-0.1, -0.1, -0.1), (0.1, 0.1, 0.1))
        obstacles = self.obstacle(row(rest=(0, 1, 2.8)), fixed)
        self.assertFalse(obstacles.sweep_blocks(pose(yaw=-math.pi/4), pose(yaw=-math.pi/4), td, 4000))
        self.assertTrue(obstacles.sweep_blocks(pose(yaw=-math.pi/4), pose(yaw=math.pi/4), td, 4000))

    def test_pitch_and_roll_are_part_of_contact_volume(self):
        td = descriptor()
        td.chassis.hitTester = BoxTester((-0.1, -0.1, -2), (0.1, 0.1, 2))
        td.hull.hitTester = td.chassis.hitTester
        obstacles = self.obstacle()
        self.assertFalse(obstacles.sweep_blocks(pose(y=3.2), pose(y=3.2), td, 4000))
        self.assertTrue(obstacles.sweep_blocks(pose(y=3.2, pitch=math.pi/2), pose(y=3.2, pitch=math.pi/2), td, 4000))
        td.chassis.hitTester = BoxTester((-2, -0.1, -0.1), (2, 0.1, 0.1))
        td.hull.hitTester = td.chassis.hitTester
        self.assertFalse(obstacles.sweep_blocks(pose(y=3.2), pose(y=3.2), td, 4000))
        self.assertTrue(obstacles.sweep_blocks(pose(y=3.2, roll=math.pi/2), pose(y=3.2, roll=math.pi/2), td, 4000))

    def test_initial_overlap_allows_retreat_without_tunneling(self):
        obstacles = self.obstacle()
        self.assertFalse(obstacles.sweep_blocks(pose(x=1.25), pose(x=1.4), descriptor(), 4000))
        self.assertTrue(obstacles.sweep_blocks(pose(x=1.25), pose(x=1.1), descriptor(), 4000))
        self.assertTrue(obstacles.sweep_blocks(pose(x=1.25), pose(x=-3), descriptor(), 4000))

    def test_rows_are_immutable_and_navigation_components_have_stable_ids(self):
        accepted = row()
        obstacles = self.obstacle(accepted)
        accepted['flight']['rest'] = (100, 100, 100)
        self.assertFalse(obstacles.add('bot:17', accepted, descriptor()))
        self.assertEqual(obstacles.navigation_hulls(2999), ())
        hulls = obstacles.navigation_hulls(3000)
        self.assertEqual(len(hulls), 2)
        self.assertEqual([entry[0] for entry in hulls], [-69, -70])
        self.assertAlmostEqual(hulls[0][1], 0)
        self.assertAlmostEqual(hulls[1][2], 4.5)
        self.assertEqual(obstacles.clear(), 1)
        self.assertEqual(obstacles.clear(), 0)

    def test_unlanded_flight_is_never_an_obstacle(self):
        accepted = row()
        accepted['flight']['landed'] = False
        obstacles = detached_turret.DetachedTurretObstacles(_Math())
        self.assertFalse(obstacles.add('bot:17', accepted, descriptor()))
        self.assertEqual(obstacles.active(), 0)


class CanonicalTurretPresentationTests(unittest.TestCase):
    def presentation(self, bigworld):
        return detached_turret.DetachedTurretPresentation(
            bigworld, _Math(), types.SimpleNamespace(spaceID=5),
            lambda *args: self.fail('canonical presentation must not raycast'))

    def test_late_admission_uses_accepted_rest_and_retries_source_readiness(self):
        world = _BigWorld()
        presentation = self.presentation(world)
        vehicle, accepted = _Vehicle(), row(rest=(15, 3, 10))
        vehicle.isStarted = False
        self.assertIsNone(presentation.prepare_canonical(vehicle, accepted))
        vehicle.isStarted = True
        plan = presentation.prepare_canonical(vehicle, accepted)
        self.assertTrue(presentation.launch_canonical(plan, accepted, 100, 10))
        self.assertTrue(presentation.has_vehicle(17))
        self.assertEqual(world.created[0][3], _Vector(15, 3, 10))
        self.assertTrue(presentation.launch_canonical(plan, accepted, 100.1, 10.1))
        self.assertEqual(len(world.created), 1)
        presentation.advance(100.1)
        self.assertEqual(world.entity(901).model.matrix.translation, _Vector(15, 3, 10))

    def test_failed_creation_has_cooldown_without_permanent_abandonment(self):
        world = _BigWorld(fail_create=True)
        presentation = self.presentation(world)
        accepted = row()
        plan = presentation.prepare_canonical(_Vehicle(), accepted)
        for now in (0, 0.25, 1, 1.25, 2, 3, 4):
            self.assertFalse(presentation.launch_canonical(plan, accepted, now, now))
        self.assertEqual(len(world.created), 5)
        world._fail_create = False
        self.assertTrue(presentation.launch_canonical(plan, accepted, 5, 5))
        presentation.advance(5)
        self.assertIsNotNone(world.entity(901).model.matrix)

    def test_timed_out_async_load_is_retired_when_owned_and_replayed(self):
        world = _BigWorld(defer_enter=True)
        presentation = self.presentation(world)
        accepted = row()
        plan = presentation.prepare_canonical(_Vehicle(), accepted)
        self.assertTrue(presentation.launch_canonical(plan, accepted, 0, 0))
        presentation.advance(30)
        self.assertTrue(presentation.has_vehicle(17))
        self.assertEqual(world.destroy_attempts, [])
        self.assertFalse(presentation.launch_canonical(plan, accepted, 31, 31))
        self.assertFalse(presentation.launch_canonical(plan, accepted, 120, 120))
        self.assertEqual(len(world.created), 1)
        old = world.enter_world(901)
        presentation.advance(121)
        self.assertFalse(presentation.has_vehicle(17))
        self.assertTrue(presentation.launch_canonical(plan, accepted, 122, 122))
        current = world.enter_world(902)
        presentation.advance(122.1)
        self.assertEqual(world.destroyed, [901])
        self.assertIsNone(old.model.matrix)
        self.assertIsNotNone(current.model.matrix)
        self.assertEqual(current.model.matrix.translation, _Vector(*accepted['flight']['rest']))

    def test_repeated_owned_load_failures_recover_after_safe_retirement(self):
        world = _BigWorld(model=False)
        presentation = self.presentation(world)
        accepted = row()
        plan = presentation.prepare_canonical(_Vehicle(), accepted)
        for attempt in range(4):
            now = attempt * 31
            self.assertTrue(presentation.launch_canonical(plan, accepted, now, now))
            presentation.advance(now + 30)
            self.assertFalse(presentation.has_vehicle(17))
            self.assertEqual(len(world.entities), 0)
        self.assertEqual(world.destroyed, [901, 902, 903, 904])
        world._model = True
        self.assertTrue(presentation.launch_canonical(plan, accepted, 124, 124))
        presentation.advance(124)
        self.assertEqual(world.entity(905).model.matrix.translation,
                         _Vector(*accepted['flight']['rest']))

    def test_unobserved_reused_id_is_never_destroyed(self):
        world = _BigWorld(defer_enter=True)
        presentation = self.presentation(world)
        accepted = row()
        plan = presentation.prepare_canonical(_Vehicle(), accepted)
        presentation.launch_canonical(plan, accepted, 0, 0)
        replacement = types.SimpleNamespace(id=901, vehicleID=999, model=None)
        world.entities[901] = replacement
        presentation.destroy_all()
        self.assertEqual(world.destroy_attempts, [])
        self.assertIs(world.entities[901], replacement)
