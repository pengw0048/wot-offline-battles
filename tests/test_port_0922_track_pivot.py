"""Descriptor-owned single-track steering and continuous collision regressions."""
import math
import json
import struct
import sys
import types
import unittest
from unittest import mock

import test_port_0922_battle_runtime as battle_fixture
import test_port_0922_bot_runtime as bot_fixture
from gui.mods.offline_lan_0922 import vehicle_physics as physics
from gui.mods.offline_lan_0922 import tank_collision


class TrackPivotGeometryTests(unittest.TestCase):
    def test_descriptor_flag_and_real_track_gauge_control_held_track(self):
        for around in (False, True):
            descriptor = battle_fixture._Descriptor()
            descriptor.chassis.rotationIsAroundCenter = around
            descriptor.physics['trackCenterOffset'] = 1.73
            params = physics.derive_params(descriptor)
            self.assertEqual(around, params['rotationIsAroundCenter'])
            for direction in (-1., 1.):
                offset = physics.track_pivot_offset(params, 0., direction)
                before = (3., 2., -7.)
                yaw, end_yaw = .8, .8 + direction * .31
                end = physics.track_pivot_position(before, yaw, end_yaw, offset)
                left, right = physics.track_scroll(params, 0., direction)
                if around:
                    self.assertEqual(before, end)
                    self.assertAlmostEqual(left, -right)
                else:
                    # Observe the fixed inner-track point in world space.
                    for start_value, end_value in zip(
                            (before[0]+offset*math.cos(yaw),
                             before[2]-offset*math.sin(yaw)),
                            (end[0]+offset*math.cos(end_yaw),
                             end[2]-offset*math.sin(end_yaw))):
                        self.assertAlmostEqual(start_value, end_value)
                    self.assertEqual(0., min(left, right))
                    self.assertGreater(max(left, right), 0.)
                    self.assertAlmostEqual(offset, physics.track_pivot_from_poses(
                        params, before, yaw, end, end_yaw))

    def test_reverse_and_airborne_have_distinct_motion(self):
        params = {'rotationIsAroundCenter': False, 'trackCenter': 1.4,
                  'speedFwd': 20.}
        for omega in (-.7, .7):
            offset = physics.track_pivot_offset(params, 0., omega,
                                                drive_intent=-1.)
            end = physics.track_pivot_position((0., 0., 0.), 0., omega*.1, offset)
            self.assertLess(end[2], 0.)
            belts = physics.track_scroll(params, 0., omega, drive_intent=-1.)
            self.assertEqual(0., max(belts))
            self.assertLess(min(belts), 0.)
            self.assertEqual(0., physics.track_pivot_offset(
                params, 0., omega, airborne=True))
            # Road speed already faster than the inner-track reversal limit.
            self.assertEqual(0., physics.track_pivot_offset(params, 4., omega))

    def test_missing_track_gauge_is_not_invented_from_a_hull_box(self):
        descriptor = battle_fixture._Descriptor()
        descriptor.physics.pop('trackCenterOffset')
        self.assertIsNone(physics.track_pivot_descriptor_params(descriptor))
        with self.assertRaisesRegex(RuntimeError, 'pivot geometry'):
            physics.derive_params(descriptor)
        self.assertEqual(0., physics.track_pivot_from_poses(
            None, (0., 0., 0.), 0., (1., 0., 1.), 1.))

    def test_interval_box_contains_every_true_arc_corner(self):
        bbox = ((-1.7, -.2, -3.1), (1.4, 1.4, 3.8))
        half = .31
        for offset in (-1.5, 1.5):
            box = battle_fixture.battle_runtime_module._destructible_rotation_interval_bbox(
                bbox, half, offset)
            for index in range(101):
                angle = -half + 2.*half*index/100.
                centre = physics.track_pivot_position((0., 0., 0.), 0., angle, offset)
                for x in (bbox[0][0], bbox[1][0]):
                    for z in (bbox[0][2], bbox[1][2]):
                        world_x = centre[0] + x*math.cos(angle) + z*math.sin(angle)
                        world_z = centre[2] - x*math.sin(angle) + z*math.cos(angle)
                        self.assertLessEqual(box[0][0]-1e-9, world_x)
                        self.assertGreaterEqual(box[1][0]+1e-9, world_x)
                        self.assertLessEqual(box[0][2]-1e-9, world_z)
                        self.assertGreaterEqual(box[1][2]+1e-9, world_z)

    def test_tank_contact_checks_arc_translation_not_only_final_heading(self):
        shape = (.3, .3, -.5, .5)
        start = (0., 0., 0.)
        # The small hull passes z=1 on an arc about x=1.5, while a centre
        # pivot is wholly clear of this stationary hull.
        others = [{'id': 2, 'x': .5, 'y': 0., 'z': 1., 'yaw': 0., 'shape': shape}]
        self.assertEqual(1., tank_collision.rotation_fraction(
            start, 0., math.pi, shape, others))
        fraction = tank_collision.rotation_fraction(
            start, 0., math.pi-.001, shape, others, pivot_offset=1.5)
        self.assertGreater(fraction, 0.)
        self.assertLess(fraction, .5)

    def test_arena_rejects_an_arc_whose_endpoints_both_fit(self):
        runtime = battle_fixture._runtime()
        battle = battle_fixture.BattleRuntime(runtime)
        descriptor = battle_fixture._Descriptor()
        entity = types.SimpleNamespace(typeDescriptor=descriptor)
        start, yaw, end_yaw, offset = (0., 0., 0.), .4, 1., 1.5
        end = physics.track_pivot_position(start, yaw, end_yaw, offset)
        shape = battle._collision_shape(descriptor)
        maximum_z = max(physics.track_pivot_sweep_bounds(
            point, angle, angle, offset, shape[0], shape[1])[3]
            for point, angle in ((start, yaw), (end, end_yaw)))
        battle._arena_bounds = (-100., -100., 100., maximum_z + .001)
        self.assertEqual((0., 0., 0., 0.), battle._arena_pose_violations(entity, start, yaw))
        self.assertEqual((0., 0., 0., 0.), battle._arena_pose_violations(entity, end, end_yaw))
        self.assertFalse(battle._arena_rotation_is_clear(entity, start, yaw, end_yaw, end))

    def test_player_broadphase_keeps_a_hull_reached_only_by_the_track_arc(self):
        runtime = battle_fixture._runtime()
        battle = battle_fixture.BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        runtime.bigworld.entities[11] = battle_fixture._Vehicle(
            11, battle_fixture._Descriptor(), battle_fixture._Vector(),
            (0, 0, 0), {'health': 500})
        battle._records['bot:11'] = {
            'engine_id': 11, 'network_id': 11, 'kind': 'bot', 'ready': True,
            'state': {'id': 11, 'x': -.522756, 'y': 0., 'z': 4.289342,
                      'yaw': 0., 'speed': 0., 'team': 2,
                      'collision_shape': (.03, .03, -.5, .5)}}
        shape = (1.7, 3.5, -.5, .5)
        self.assertEqual([], battle._contact_tanks((0., 0., 0.), shape))
        bodies = battle._contact_tanks((0., 0., 0.), shape, extra_reach=1.5*.3)
        self.assertEqual(1, len(bodies))
        self.assertLess(tank_collision.rotation_fraction(
            (0., 0., 0.), 0., .3, shape, bodies, pivot_offset=1.5), 1.)

    def test_native_and_catalog_sweeps_use_arc_midpoints_before_commit(self):
        runtime = battle_fixture._runtime()
        battle = battle_fixture.BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        descriptor = battle_fixture._Descriptor()
        resolver = mock.Mock(return_value={'status': 'clear'})
        battle._destructibles = types.SimpleNamespace(
            _vehicle_hull_bbox=lambda td: td.hull.hitTester.bbox,
            _catalog_motion_proposal=resolver,
            native_replacement_bsp_active=lambda: False)
        start, yaw, angle, offset = (2., 3., 4.), 0., .16, 1.5
        end = physics.track_pivot_position(start, yaw, angle, offset)
        battle._destructible_pose_sweep(start, yaw, end, angle, 0.,
                                       descriptor, 1., .1)
        for call in resolver.call_args_list:
            self.assertEqual(0., call.kwargs['travel_reach'])
            expected = physics.track_pivot_position(start, yaw, call.args[2], offset)
            self.assertEqual(expected, tuple(call.args[1]))
        with mock.patch.object(battle_fixture.battle_runtime_module.world_collision,
                               'check_horizontal_collision', return_value='hard') as world:
            self.assertFalse(battle._native_world_rotation_is_clear(
                start, yaw, angle, descriptor, pivot_offset=offset))
        self.assertEqual(1, world.call_count)
        self.assertNotEqual(start, tuple(world.call_args.args[3]))
        self.assertIsNone(world.call_args.kwargs['departing_contact'])

    def test_support_rise_rejects_local_arc_centre_and_heading_together(self):
        from test_port_0922_siege_braking import local_battle
        battle, entity = local_battle('sweden:S22_Strv_S1', 0, 0., .5)
        battle._sender.forward = 0.
        battle._sender.turn = 1.
        battle._local_position = (0., 0., 0.)
        battle._local_suspension_disabled = True
        battle._local_fall_armed = True
        battle._terrain_support = lambda *args, **kw: (5., 5.)
        battle._update_vertical_motion = types.MethodType(
            battle_fixture.BattleRuntime._update_vertical_motion, battle)
        battle._resolve_local_tank_contacts = lambda entity, p, yaw, dt: p
        battle._pose_sweep_is_clear = mock.Mock(return_value=True)
        battle._report_local_hydraulic_motion = mock.Mock()
        battle._update_local_presentation = lambda entity, dt: battle._vector(
            battle._local_position)
        battle._drive_local_step(.1)
        self.assertGreater(battle._pose_sweep_is_clear.call_args.args[3][2], 0.)
        self.assertTrue(battle._local_support_rise_blocked)
        self.assertEqual((0., 0., 0.), battle._local_position)
        self.assertEqual(0., battle._local_yaw)
        self.assertEqual(0., battle._local_turn_speed)

    def test_tree_sweep_contains_arc_midpoint_outside_the_endpoint_chord(self):
        from gui.mods.offline_lan_0922 import destructibles_sensor as sensor
        bbox = ((-.2, -.2, -.2), (.2, .2, .2))
        start, yaw, angle, offset = (0., 0., 0.), 0., 1.2, 1.5
        end = physics.track_pivot_position(start, yaw, angle, offset)
        boxes = sensor._tree_pose_sweep_boxes_1513(
            battle_fixture._Vector(*start), yaw,
            battle_fixture._Vector(*end), angle, bbox, offset)
        # Every slice has zero chord generator: its interval box already
        # encloses the complete curved displacement around the fixed track.
        self.assertGreater(len(boxes), 1)
        self.assertTrue(all(box[1][3] == (0., 0., 0.) for box in boxes))
        for index, box in enumerate(boxes):
            at = angle*(index+.5)/len(boxes)
            centre = physics.track_pivot_position(start, yaw, at, offset)
            point_box = (centre, ((0., 0., 0.),)*3)
            self.assertTrue(sensor._boxes_intersect(box, point_box))

    def test_tree_geometry_keeps_doubles_across_native_float32_vectors(self):
        from gui.mods.offline_lan_0922 import destructibles_sensor as sensor
        runtime = battle_fixture._runtime()
        battle = battle_fixture.BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        descriptor = battle_fixture._Descriptor()
        start, yaw, end_yaw = (100.123456, 0., 200.765432), .123456, .198456
        end = physics.track_pivot_position(start, yaw, end_yaw, 1.5)
        def float32_vector(values):
            return battle_fixture._Vector(*(struct.unpack('f', struct.pack('f', value))[0]
                                            for value in values))
        quant_start, quant_end = float32_vector(start), float32_vector(end)
        params = physics.track_pivot_descriptor_params(descriptor)
        self.assertEqual(0., physics.track_pivot_from_poses(
            params, tuple(quant_start), yaw, tuple(quant_end), end_yaw))
        battle._vector = float32_vector
        battle._destructibles = sensor
        with mock.patch.object(sensor, '_tree_pose_sweep_boxes_1513',
                               wraps=sensor._tree_pose_sweep_boxes_1513) as boxes, \
                mock.patch.object(sensor, '_tree_motion_required_chunks_1513',
                                  return_value=('hard', ())):
            battle._tree_motion_proposal(start, yaw, end, end_yaw,
                                         0., descriptor, 1., .1)
        self.assertEqual(start, boxes.call_args.args[0])
        self.assertEqual(end, boxes.call_args.args[2])
        self.assertAlmostEqual(1.5, boxes.call_args.args[5])

    def test_worker_receipt_replays_the_same_arc_after_json_and_server_validation(self):
        runtime = battle_fixture._runtime()
        battle = battle_fixture.BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        descriptor = battle_fixture._Descriptor()
        start, yaw, end_yaw = (100.123456, 0., 200.765432), .123456, .198456
        end = physics.track_pivot_position(start, yaw, end_yaw, 1.5)
        params = physics.track_pivot_descriptor_params(descriptor)
        self.assertEqual(0., physics.track_pivot_from_poses(
            params, tuple(round(v, 4) for v in start), round(yaw, 5),
            tuple(round(v, 4) for v in end), round(end_yaw, 5)))
        token = ((22, 37, 73),)
        detail = {'status': 'crushed', 'token': token, 'requires_commit': True}
        self.assertTrue(battle._queue_local_destructible_contact(
            detail, start, yaw, 0., .1, end, end_yaw))
        wire = json.loads(json.dumps(battle.local_destructible_contacts()[0]))
        contact = bot_fixture.BattleState._validated_player_destructible_contact(wire)
        self.assertIsNotNone(contact)
        received_start = tuple(contact[key] for key in ('x', 'y', 'z'))
        received_end = tuple(contact[key] for key in ('end_x', 'end_y', 'end_z'))
        self.assertEqual(start, received_start)
        self.assertEqual(end, received_end)
        self.assertEqual(yaw, contact['yaw'])
        self.assertEqual(end_yaw, contact['end_yaw'])
        resolver = mock.Mock(return_value=detail)
        battle._worker_mode = True
        battle.client = battle_fixture._Client()
        battle.client.send_player_destructible_contact_result = mock.Mock(return_value=True)
        battle._resolve_player_descriptor = lambda unused: descriptor
        battle._destructibles = types.SimpleNamespace(
            _vehicle_hull_bbox=lambda td: td.hull.hitTester.bbox,
            _catalog_motion_proposal=resolver, _catalog_motion_blocked=resolver)
        effective = battle_fixture._effective_params_snapshot()
        effective['physics'].update(rotationIsAroundCenter=False, trackCenter=1.5)
        player = {'id': 2, 'vehicle': 'ussr:R11_MS-1',
                  'vehicle_compact_descr': 'dGVzdA==', 'effective_params': effective,
                  'destructible_contacts': [contact]}
        authority_name = 'gui.mods.offline_lan_0922.destructibles_authority'
        authority = types.SimpleNamespace(is_destroyed=lambda *unused: False)
        package = sys.modules['gui.mods.offline_lan_0922']
        with mock.patch.dict(sys.modules, {authority_name: authority}), \
                mock.patch.object(package, 'destructibles_authority', authority, create=True):
            self.assertEqual(1, battle._resolve_player_destructible_contacts([player], 1.))
        self.assertEqual(2, resolver.call_count)
        for call in resolver.call_args_list:
            self.assertEqual(0., call.kwargs['travel_reach'])
            expected = physics.track_pivot_position(start, yaw, call.args[2], 1.5)
            for wanted, actual in zip(expected, tuple(call.args[1])):
                self.assertAlmostEqual(wanted, actual)
        battle.client.send_player_destructible_contact_result.assert_called_once_with(
            2, 1, True, [list(row) for row in token])


class TrackPivotBotTests(unittest.TestCase):
    def setUp(self):
        self.fixture = bot_fixture.BotRuntimeTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_bot_track_arc_commits_only_after_the_full_pose_is_clear(self):
        fixture = self.fixture
        for around, clear, support_block in (
                (False, False, False), (False, True, False),
                (True, True, False), (False, True, True)):
            with self.subTest(around=around, clear=clear, support=support_block):
                command = fixture._stationary_command()
                command.update(turn=1., target_yaw=1.)
                descriptor = bot_fixture._combat_descriptor()
                descriptor.chassis.rotationIsAroundCenter = around
                descriptor.physics['trackCenterOffset'] = 1.35
                rotation = mock.Mock(return_value=clear)
                runtime = fixture.module.BotRuntime(
                    1, descriptor_resolver=lambda unused: descriptor,
                    adapter_factory=lambda *unused: bot_fixture._FixedAdapter(command),
                    direction_probe=lambda *unused: {'clear': True, 'slope': 0.},
                    ground_probe=lambda *unused: 0.,
                    physics_ground_probe=lambda *unused: 0.,
                    spawn_resolver=bot_fixture._spawn_resolver,
                    baked_graph=bot_fixture._graph(),
                    motion_resolver=lambda *unused: 'clear',
                    rotation_resolver=rotation)
                runtime.battle_start(fixture.start)
                state = runtime.states[11]
                state.update(x=0., y=0., z=0., yaw=0., speed=0., grounded_once=True)
                if support_block:
                    runtime._physics_ground_probe = lambda x, z, hint: (
                        10. if z > 1.e-5 else 0.)
                runtime.update(.04, 1.)
                if not clear or support_block:
                    self.assertEqual((0., 0., 0.), (state['x'], state['z'], state['yaw']))
                elif around:
                    self.assertGreater(state['yaw'], 0.)
                    self.assertEqual((0., 0.), (state['x'], state['z']))
                else:
                    self.assertGreater(state['yaw'], 0.)
                    expected = physics.track_pivot_position((0., 0., 0.), 0., state['yaw'], 1.35)
                    self.assertAlmostEqual(expected[0], state['x'])
                    self.assertAlmostEqual(expected[2], state['z'])
                    self.assertEqual(1.35, rotation.call_args.args[-1])
