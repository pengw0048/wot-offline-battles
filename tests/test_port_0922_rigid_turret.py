import math
import unittest
import random
import struct
from unittest import mock

from test_port_0922_turret_obstacles import descriptor, pose
from gui.mods.offline_lan_0922 import rigid_turret as physics
from gui.mods.offline_lan_0922.entities import turret_obstacles as geometry


def components():
    td = descriptor()
    td.turret.weight = 4000.0
    td.gun.weight = 1000.0
    return geometry.turret_components(td)


def body(position=(0, 1, 0), velocity=(0, 0, 0), angular=(0, 0, 0)):
    return physics.Body(components(), dict(position=position, attitude=(0, 0, 0),
                         velocity=velocity, angular_velocity=angular))


def floor(start, end):
    if start[1] >= 0 and end[1] <= 0 and start[1] != end[1]:
        f = start[1]/(start[1]-end[1])
        return (tuple(start[i]+(end[i]-start[i])*f for i in range(3)), (0, 1, 0))
    return None


class RigidTurretTests(unittest.TestCase):
    def test_barrel_side_contact_survives_same_ground_height_as_tracks(self):
        value = body((0, 1, 0))
        value.grounded = True
        # Only the gun at z=4..5 overlaps. The turret box is far behind;
        # chassis and detached turret both have their underside exactly at 0.
        boxes = (((.4, .4, 4.5), ((1.5, 0, 0), (0, .4, 0), (0, 0, 2))),
                 ((.4, 1., 4.5), ((1.5, 0, 0), (0, .2, 0), (0, 0, 2))))
        for horizontal in (False, True):
            result = physics.vehicle_contact(value, boxes, 50000, (-5, 0, 0),
                                             horizontal=horizontal)
            self.assertIsNotNone(result)
            self.assertAlmostEqual(0, result['normal'][1])
            self.assertLess(result['momentum'][0], 0)

    def test_quantized_resting_corner_cannot_be_shoved_under_a_slope(self):
        f32 = lambda v: struct.unpack('f', struct.pack('f', v))[0]
        def ground(start, end):
            start, end = tuple(map(f32, start)), tuple(map(f32, end))
            a = start[1]-3-.13*(start[0]-400)
            b = end[1]-3-.13*(end[0]-400)
            if a >= 0 and b <= 0 and a != b:
                fraction = a/(a-b)
                return (tuple(f32(start[i]+(end[i]-start[i])*fraction) for i in range(3)),
                        physics.unit((-.13, 1., 0.)))
        value = body((400., 0., 200.))
        height = 3-min(p[1]-.13*(p[0]-400) for p in value.points())
        # A sub-ULP start below the surface is legitimate after native output
        # conversion. The old unskinned recovery swept .7 m through the slope.
        value.com = physics.add(value.com, (0., height-1e-6, 0.))
        moved = physics.translate(value, (.7, 0., 0.), ground)
        self.assertLess(moved[0], .01)
        for unused in range(200):
            physics.advance(value, .04, [], ground)
        self.assertGreaterEqual(min(p[1]-3-.13*(p[0]-400) for p in value.points()), -.025)

    def test_budget_preserves_exact_substeps_and_remaining_time(self):
        value = body((0., 10., 0.), (1., 0., 0.))
        clock = iter((0., .001, .002, .003, .004)).__next__
        elapsed = physics.advance(value, .2, [], lambda *a: None, budget=.004, clock=clock)
        self.assertAlmostEqual(.04, elapsed)
        self.assertAlmostEqual(.04, value.position[0])
        elapsed += physics.advance(value, .2-elapsed, [], lambda *a: None, budget=.004, clock=lambda: 0.)
        self.assertAlmostEqual(.2, elapsed)
        self.assertAlmostEqual(.2, value.position[0])
        self.assertAlmostEqual(10.-.5*9.81*.2**2, value.com[1])

    def test_binary32_sloping_scenery_cannot_lose_contact_and_fall_underground(self):
        f32 = lambda v: struct.unpack('f', struct.pack('f', v))[0]
        slope = .13
        def ground(start, end):
            start, end = tuple(map(f32, start)), tuple(map(f32, end))
            a = start[1]-3-slope*(start[0]-400)
            b = end[1]-3-slope*(end[0]-400)
            if a >= 0 and b <= 0 and a != b:
                fraction = a/(a-b)
                return (tuple(f32(start[i]+(end[i]-start[i])*fraction) for i in range(3)),
                        (-slope/math.sqrt(1+slope*slope), 1/math.sqrt(1+slope*slope), 0))
            return None
        for seed in range(12):
            rng = random.Random(seed)
            value = body((400, 8, 200), (-1.8, 10, -1.6),
                         tuple(rng.uniform(-3, 3) for _ in range(3)))
            for unused in range(200):
                physics.advance(value, .04, [], ground)
            lowest = min(p[1]-3-slope*(p[0]-400) for p in value.points())
            self.assertGreaterEqual(lowest, -.025, 'lost contact at seed %d' % seed)
            self.assertLess(lowest, .025)

    def test_far_vehicles_do_not_enter_the_iterative_contact_solver(self):
        value = body((0, 20, 0), (1, 0, 1))
        vehicles = [dict(boxes=geometry.vehicle_support_boxes(
            descriptor(), pose(x=100+i*10), attached=False),
            mass=50000., velocity=(0., 0., 0.)) for i in range(29)]
        with mock.patch.object(physics, 'vehicle_contact', wraps=physics.vehicle_contact) as contact:
            physics.advance(value, .04, vehicles, floor)
        self.assertEqual(0, contact.call_count)
        self.assertAlmostEqual(.04, value.com[0])

    def test_blocked_correction_does_not_repeat_an_unchanged_contact(self):
        value = body()
        vehicle = dict(boxes=geometry.vehicle_support_boxes(
            descriptor(), pose(x=-1.4), attached=False),
            mass=50000., velocity=(0., 0., 0.))
        for human in (False, True):
            vehicle['human'] = human
            apply = mock.Mock()
            with mock.patch.object(physics, 'scenery_step'), \
                    mock.patch.object(physics, 'translate', return_value=physics.ZERO), \
                    mock.patch.object(physics, 'vehicle_contact',
                                      wraps=physics.vehicle_contact) as contacts:
                physics.advance(value, physics.STEP, [vehicle], floor, apply)
            self.assertEqual(1, contacts.call_count)
            self.assertEqual(0 if human else 1, apply.call_count)
            self.assertEqual((0., 0., 0.), value.velocity)

    def test_vehicle_recovery_still_rechecks_contact_when_turret_is_blocked(self):
        value = body()
        vehicle = dict(boxes=geometry.vehicle_support_boxes(
            descriptor(), pose(x=-1.4), attached=False),
            mass=50000., velocity=(0., 0., 0.))
        def recover(peer, hit, step):
            peer['boxes'] = tuple((physics.add(center, (-.01, 0., 0.)), axes)
                                  for center, axes in peer['boxes'])
        with mock.patch.object(physics, 'scenery_step'), \
                mock.patch.object(physics, 'translate', return_value=physics.ZERO), \
                mock.patch.object(physics, 'vehicle_contact',
                                  wraps=physics.vehicle_contact) as contacts:
            physics.advance(value, physics.STEP, [vehicle], floor, recover)
        self.assertGreater(contacts.call_count, 1)

    def test_mass_and_inertia_use_both_actual_components(self):
        props = physics.properties(components())
        self.assertEqual(5000, props['mass'])
        self.assertAlmostEqual(.9, props['centre'][2])
        for i in range(3):
            self.assertGreater(props['inertia'][4*i], 0)

    def test_missing_weight_has_no_generic_light_turret_fallback(self):
        with self.assertRaises((TypeError, ValueError)):
            physics.properties(geometry.turret_components(descriptor()))

    def test_flat_floor_drop_cannot_stop_in_mid_air(self):
        value = body((0, 8, 0))
        for _ in range(400):
            physics.scenery_step(value, .01, floor)
        # A long offset gun can prop the assembly at an angle. Its lowest
        # real component must touch, rather than forcing the root to Y=1.
        self.assertLess(value.position[1], 2.0)
        self.assertLess(min(p[1] for p in value.points()), .025)
        self.assertGreater(min(p[1] for p in value.points()), -.025)
        self.assertLess(physics.length(value.velocity), .05)

    def test_missing_ground_keeps_falling_after_the_old_flight_window(self):
        value = body((0, 20, 0))
        for _ in range(900):
            physics.scenery_step(value, .01, lambda a, b: None)
        self.assertLess(value.position[1], -300)
        self.assertFalse(value.grounded)

    def test_side_collision_does_not_lift_a_grounded_turret_onto_roof(self):
        value = body()
        boxes = geometry.vehicle_support_boxes(descriptor(), pose(x=-1.4), attached=False)
        result = physics.vehicle_contact(value, boxes, 50000, (5, 0, 0))
        self.assertIsNotNone(result)
        self.assertAlmostEqual(0, result['momentum'][1])
        self.assertAlmostEqual(0, result['body_correction'][1])
        self.assertGreater(result['momentum'][0], 0)

    def test_reciprocal_momentum_is_conserved_and_impact_adds_no_energy(self):
        for vehicle_mass in (1000., 5000., 100000.):
            value = body()
            boxes = geometry.vehicle_support_boxes(descriptor(), pose(x=-1.4), attached=False)
            velocity = (5, 0, 0)
            result = physics.vehicle_contact(value, boxes, vehicle_mass, velocity)
            initial_energy = .5*vehicle_mass*25
            value.momentum(result['momentum'], result['angular_momentum'])
            final_vehicle = physics.add(velocity, result['delta'])
            self.assertAlmostEqual(vehicle_mass*5, vehicle_mass*final_vehicle[0]+value.props['mass']*value.velocity[0])
            self.assertLessEqual(.5*vehicle_mass*physics.dot(final_vehicle, final_vehicle)+value.kinetic_energy(), initial_energy+1e-5)

    def test_off_centre_impact_turns_body_instead_of_translating_it_up(self):
        value = body()
        boxes = geometry.vehicle_support_boxes(descriptor(), pose(x=-1.4, z=.7), attached=False)
        hit = physics.vehicle_contact(value, boxes, 50000, (5, 0, 0))
        self.assertIsNotNone(hit)
        value.momentum(hit['momentum'], hit['angular_momentum'])
        self.assertGreater(abs(value.angular[1]), 0)
        self.assertEqual(0, value.velocity[1])

    def test_rotation_roundtrips_pitch_roll_and_half_turns(self):
        for attitude in ((1, .4, -.6), (2, math.pi, 0), (0, 0, math.pi)):
            original = physics.matrix(attitude)
            restored = physics.matrix(physics.angles(original))
            for a, b in zip(original, restored):
                self.assertAlmostEqual(a, b)

    def test_wall_contact_separates_horizontally(self):
        def wall(start, end):
            if start[0] <= 0 <= end[0] and end[0] != start[0]:
                f = -start[0]/(end[0]-start[0])
                return (tuple(start[i]+(end[i]-start[i])*f for i in range(3)), (-1, 0, 0))
        value = body((-1.01, 5, 0), (5, 0, 0))
        physics.scenery_step(value, .01, wall)
        self.assertLessEqual(max(p[0] for p in value.points()), 1e-5)
        self.assertLess(value.position[1], 5.001)


class TurretPresentationTests(unittest.TestCase):
    def test_slow_packets_still_move_at_each_high_fps_render_frame(self):
        from test_port_0922_turret_obstacles import row
        buffer = physics.PresentationBuffer()
        positions, cursors = [], []
        next_packet = 0.
        for index in range(301):
            now = index/100.
            if now+1e-8 >= next_packet:
                source = now-.06
                frame = body((source, 10., 0.), (1., 0., 0.))
                accepted = physics.revision(row(), frame, int((source+1.)*1000))
                buffer.push(accepted, now, .06)
                next_packet += .12
            position, unused_attitude, unused_event = buffer.pose(now)
            positions.append(position[0])
            cursors.append(buffer.cursor)
        self.assertEqual(sorted(cursors), cursors)
        steps = [b-a for a, b in zip(positions[60:-1], positions[61:])]
        self.assertTrue(all(0. < step < .011 for step in steps), steps)
        self.assertLess(buffer.cursor, buffer.samples[-1][0])
        self.assertLess(len(buffer.samples), 10)

    def test_rotation_interpolation_crosses_pi_and_pitch_singularity_without_a_flip(self):
        for first, second in (((3.1, 0., 0.), (-3.1, 0., 0.)),
                              ((.2, 1.56, .4), (.2, 1.58, .4))):
            a, b = physics.matrix(first), physics.matrix(second)
            middle = physics.interpolate_rotation(a, b, .5)
            self.assertLess(sum((x-y)**2 for x, y in zip(a, middle)), .02)
            for alpha, expected in ((0., a), (1., b)):
                for x, y in zip(physics.interpolate_rotation(a, b, alpha), expected):
                    self.assertAlmostEqual(x, y)

    def test_landing_and_sleep_follow_playback_and_newer_motion_wakes_same_entity(self):
        from test_port_0922_turret_obstacles import row, descriptor, _BigWorld, _Math, _Vehicle
        from gui.mods.offline_lan_0922.entities.detached_turret import DetachedTurretPresentation
        world = _BigWorld()
        from test_port_0922_turret_detachment import _Avatar
        presentation = DetachedTurretPresentation(world, _Math(), _Avatar(), lambda *a: None)
        source = _Vehicle()
        source.typeDescriptor = descriptor()
        value = body((0., 3., 0.))
        first = physics.revision(row(), value, 1000)
        plan = presentation.prepare_canonical(source, first)
        self.assertTrue(presentation.launch_canonical(plan, first, 1., 0.))
        presentation.advance(1.)
        value.com = physics.add(value.com, (0., -2., 0.))
        value.grounded = value.sleeping = True
        value.impact_serial = 1
        value.impact = dict(point=(0., 0., 0.), normal=(0., 1., 0.), velocity=(0., -1., 0.), energy=.5)
        second = physics.revision(first, value, 1120)
        self.assertTrue(presentation.launch_canonical(plan, second, 1.12, 0.))
        presentation.advance(1.12)
        turret = presentation._turrets[0]
        self.assertFalse(turret['settled'])
        self.assertEqual(0, turret.get('presented_impact_serial', 0))
        for i in range(13, 41):
            presentation.advance(1.+i/100.)
        self.assertTrue(turret['settled'])
        self.assertEqual(1, turret['presented_impact_serial'])
        entity_id = turret['id']
        value.sleeping = False
        value.com = physics.add(value.com, (.1, 0., 0.))
        third = physics.revision(second, value, 11400)
        self.assertTrue(presentation.launch_canonical(plan, third, 11.4, 0.))
        self.assertFalse(turret['settled'])
        presentation.advance(11.41)
        self.assertLess(turret['pose_buffer'].delay, .2)
        self.assertEqual(entity_id, presentation._turrets[0]['id'])
        self.assertEqual(1, turret['presented_impact_serial'])

    def test_late_native_model_load_adopts_current_history_without_replaying_launch(self):
        from test_port_0922_turret_obstacles import row
        buffer = physics.PresentationBuffer()
        for i in range(50):
            now = 1.+i*.04
            value = body((now, 10., 0.), (1., 0., 0.))
            buffer.push(physics.revision(row(), value, int(now*1000)), now, 0.)
        position, unused_angle, unused_event = buffer.pose(now)
        self.assertGreater(position[0], now-.15)


class RigidTurretLifecycleTests(unittest.TestCase):
    def vehicle(self):
        return dict(boxes=geometry.vehicle_support_boxes(descriptor(), pose(), attached=False),
                    mass=50000., velocity=(0., 0., 0.), human=True, alive=True)

    def test_landing_on_vehicle_then_driving_away_releases_gravity(self):
        value = body((0, 4, 0), angular=(0, 0, 0))
        vehicle = self.vehicle()
        physics.advance(value, 1., [vehicle], floor)
        supported = value.position[1]
        self.assertGreater(supported, 1.5)
        self.assertTrue(value.grounded)
        self.assertIsNotNone(value.impact)
        serial = value.impact_serial
        physics.advance(value, .1, [vehicle], floor)
        self.assertEqual(serial, value.impact_serial)
        physics.advance(value, .3, [], floor)
        self.assertLess(value.position[1], supported-.1)
        self.assertFalse(value.sleeping)

    def test_deep_side_overlap_uses_horizontal_entry_not_roof_axis(self):
        value = body((0, 1, 0))
        value.grounded = True
        vehicle = self.vehicle()
        for x in (-1.4, -.7, -.1):
            vehicle['boxes'] = geometry.vehicle_support_boxes(descriptor(), pose(x=x), attached=False)
            result = physics.vehicle_contact(value, vehicle['boxes'], vehicle['mass'], (5, 0, 0))
            self.assertIsNotNone(result)
            self.assertAlmostEqual(0, result['body_correction'][1])
            self.assertAlmostEqual(0, result['momentum'][1])

    def test_dynamic_obstacle_cannot_restore_old_immovable_wall_gate(self):
        from test_port_0922_turret_obstacles import row, _Math
        value = body()
        accepted = physics.revision(row(), value, 4000)
        obstacles = geometry.DetachedTurretObstacles(_Math())
        self.assertTrue(obstacles.add('bot:17', accepted, descriptor()))
        with mock.patch.object(geometry, '_component_sweeps',
                               wraps=geometry._component_sweeps) as sweeps:
            for x in (-.01, .01):
                self.assertFalse(obstacles.sweep_blocks(pose(), pose(x=x), descriptor(), 4000))
            self.assertEqual(0, sweeps.call_count)
        self.assertEqual([], list(obstacles.navigation_hulls(4000)))
        newer = physics.revision(accepted, value, 4040)
        self.assertTrue(obstacles.add('bot:17', newer, descriptor()))
        self.assertFalse(obstacles.add('bot:17', accepted, descriptor()))
        self.assertEqual(1, obstacles.active())

    def test_velocity_impact_and_receipt_are_one_owned_wire_revision(self):
        import json
        from test_port_0922_turret_obstacles import row
        from gui.mods.offline_lan_0922 import turret_obstacle_schema as wire
        value = body((0, 4, 0))
        value.acks = [['player:1', 8, 123., 0., 0., 0., 0., 90.]]
        accepted = physics.revision(row(), value, 4000)
        decoded = wire.normalize_proposal(json.loads(json.dumps(accepted)))
        self.assertIsNotNone(decoded)
        self.assertEqual(accepted['flight']['body']['acks'], decoded['flight']['body']['acks'])
        self.assertEqual(list(value.velocity), decoded['flight']['body']['velocity'])
        self.assertFalse(decoded['flight']['landed'])
        invalid = json.loads(json.dumps(accepted))
        invalid['flight']['body']['velocity'][1] = float('nan')
        self.assertIsNone(wire.normalize_proposal(invalid))
