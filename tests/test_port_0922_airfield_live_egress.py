"""Report 225339: unsnapped occupied cells must not become synthetic walls.

Positions and goals are from the user's 0.9.3 hidden-worker BOT MOTION records.
The shipped graph is real; native support/collision replies are controlled
fixtures. These tests do not claim Windows acceptance of the captured scene.
"""
from contextlib import redirect_stdout
import io
import json
import math
from pathlib import Path
import unittest
from unittest import mock

from test_port_0922_navigation import TerrainNavigator
from gui.mods.offline_lan_0922.ai.adapter import BotAdapter
from gui.mods.offline_lan_0922.bot_runtime import BotRuntime

ROOT = Path(__file__).resolve().parents[1]
REPORT_POSES = (
    (2, (343.71959872053503, -0.17999887466430664, -161.8906274280862),
     (282.7417022646512, -8.03, -170.94468176434148)),
    (18, (-296.94985268843425, -0.17999887466430664, -191.80153238809643),
     (-313.08065044950047, -0.18, -134.89442719099992)),
    (25, (-288.60957913357714, -0.17999887466430664, -166.60278451821057),
     (-312.005198856603, -5.846, -54.32241294010958)),
    (26, (-278.062505535449, -0.17999839782714844, -144.5884673404018),
     (-234.0, 0.0, -206.0)),
    (27, (-286.0261670751786, -0.17999887466430664, -186.38427289358302),
     (-329.00539956960966, -6.35, -5.767693003237659)),
    (30, (-268.17565537759526, 0.12851428985595703, -137.74413645531496),
     (-229.33997664229352, -8.17, -204.18776869422527)),
)


REPORT_INTERIOR_POSES = (
    (13, (333.48155970190066, -0.18044281005859375, -181.18057068156588),
     (381.1492874992733, -0.18, -56.78732187481833)),
    (7, (371.5066969477162, 0.04060792922973633, -85.16535296397963),
     (358.0, 0.0, -6.0)),
    (22, (-325.87354366764026, 0.040576934814453125, -188.03674104422024),
     (-334.0, 0.0, -6.0)),
    (20, (-344.1419829025407, 0.04848194122314453, -131.54451149537354),
     (-322.0, 0.0, -54.0)),
    (15, (342.51185060529275, 0.15948486328125, -172.26662212844806),
     (30.5329, -0.7783, -130.5846)),
    (3, (360.28312055654845, -0.17999887466430664, -57.22265840657857),
     (374.0, 0.0, 50.0)),
    (9, (356.40981791976725, -0.17999839782714844, -55.10530460609067),
     (358.0, 0.0, -6.0)),
)


class AirfieldLiveEgressTests(unittest.TestCase):
    def setUp(self):
        self.graph = json.loads((ROOT / 'navgraphs/31_airfield.json').read_text())

    def scene(self, current, goal, ground=None, obstacle=None):
        samples, rays = [], []
        def ground_probe(x, z, hint):
            samples.append((x, z, hint))
            return current[1] if ground is None else ground(x, z, hint)
        def obstacle_probe(start, end, half_width):
            rays.append((tuple(start), tuple(end), half_width))
            return False if obstacle is None else obstacle(start, end, half_width)
        nav = TerrainNavigator(ground_probe, obstacle_probe, baked_graph=self.graph)
        nav.grid.review_native_corridor(current, goal)
        return nav, samples, rays

    def test_spic_eroded_slope_exit_requires_live_proof_without_contact_review(self):
        # Report 154804: SP I C rests in hazard-2 cell (67, 70), with no
        # baked height. Native terrain here is a controlled continuous plane
        # through the recorded pose and the adjacent western graph node.
        current = (-230.92294168762407, -4.964513778686523,
                   -219.6344910028294)
        goal = (-166.0, 0.0, -234.0)
        west = (-234.0, -4.419, -218.0)
        grade_x = (west[1] - current[1]) / (west[0] - current[0])
        nav, samples, rays = self.scene(
            current, goal, ground=lambda x, z, hint:
            current[1] + grade_x * (x - current[0]))
        nav.grid._native_review_cells.clear()
        self.assertEqual((67, 70), nav.grid.cell_for(current))
        self.assertIsNone(nav.grid._baked_cell_height((67, 70)))
        self.assertTrue(nav.grid.point_has_baked_hazard(current, 2))
        self.assertTrue(nav.grid.dry_segment_clear(current, west, 1.0))
        self.assertTrue(samples)
        self.assertEqual(current, rays[-1][0])
        target = nav._pending_target(
            29, current, goal, 1.0, {'pending_since': 0.0})
        self.assertNotEqual(current, target)
        self.assertIsNotNone(nav.grid._baked_cell_height(nav.grid.cell_for(target)))
        self.assertFalse(nav.grid._native_review_cells)

        # The same unreviewed connector must never inherit a snapped clear
        # graph edge when its actual terrain or wall proof is unavailable.
        nav.grid.obstacle_probe = lambda *unused: True
        self.assertFalse(nav.grid.dry_segment_clear(current, west, 2.0))
        nav.grid.obstacle_probe = lambda *unused: False
        for invalid in (None, float('nan'), current[1] + 5.0):
            nav.grid.ground_probe = lambda x, z, hint: (
                current[1] if (x, z) == (current[0], current[2]) else invalid)
            self.assertFalse(nav.grid.dry_segment_clear(current, west, 2.0))

    def test_interior_connector_does_not_require_prior_hard_contact(self):
        # A supported endpoint can be outside the short local fan. The
        # explicit nearest-node candidate must be offered before any contact.
        bot, current, goal = REPORT_INTERIOR_POSES[0]
        nav, samples, rays = self.scene(current, goal)
        nav.grid._native_review_cells.clear()
        nearest = nav.grid._nearest_baked_cell(nav.grid.cell_for(current), 2)
        expected = nav.grid.point_for(nearest, nav.grid._baked_cell_height(nearest))
        target = nav._pending_target(bot, current, goal, 1.0,
                                     {'pending_since': 0.0})
        self.assertEqual(expected, target)
        self.assertTrue(samples)
        self.assertEqual(1, len(rays))
        self.assertFalse(nav.grid._native_review_cells)

    def test_failed_airfield_route_keeps_its_short_exit_until_arrival(self):
        # 104150: this reproduces SU-122-44's exact first local target with
        # the shipped graph. Native replies are a stated flat/clear fixture.
        current = (-286.0, -0.18, -190.0)
        goal = (-324.01079913921933, -6.35, -5.535386006475318)
        nav, unused_samples, unused_rays = self.scene(current, goal)
        state = {}
        first = nav._fallback_target(27, current, goal, 0.0, None, state)
        self.assertAlmostEqual(-287.2641060073615, first[0])
        self.assertAlmostEqual(-188.34820219089846, first[2])
        distance = math.hypot(first[0] - current[0], first[2] - current[2])
        self.assertAlmostEqual(2.08, distance)
        choose = mock.Mock(wraps=nav.grid.safe_local_target)
        nav.grid.safe_local_target = choose
        for progress in (0.1, 0.2, 0.4):
            position = (current[0] + (first[0] - current[0]) * progress / distance,
                        current[1],
                        current[2] + (first[2] - current[2]) * progress / distance)
            selected = nav._fallback_target(27, position, goal, progress,
                                            None, state)
            self.assertEqual(first, selected)
        choose.assert_not_called()
        next_target = nav._fallback_target(27, first, goal, 1.0, None, state)
        self.assertNotEqual(first, next_target)
        self.assertEqual(1, choose.call_count)

    def test_pending_exit_survives_the_search_failure_transition(self):
        current = (-286.0, -0.18, -190.0)
        goal = (-324.01079913921933, -6.35, -5.535386006475318)
        nav, unused_samples, unused_rays = self.scene(current, goal)
        state = {'pending_since': 0.0}
        first = nav._pending_target(27, current, goal, 1.0, state)
        position = (current[0] - 0.05, current[1], current[2] + 0.1)
        self.assertEqual(first, nav._fallback_target(
            27, position, goal, 1.1, None, state))
        self.assertEqual('safe', state['navigation_status'])
        self.assertEqual(first, nav._pending_target(
            27, position, goal, 1.2, state))
        self.assertEqual(first, nav._fallback_target(
            27, position, goal, 1.3, None, state))

    def test_retained_exit_rechecks_geometry_and_current_intent(self):
        from gui.mods.offline_lan_0922.ai.navigation import BAKED_SHALLOW_WATER
        for changed in ('wall', 'goal', 'request', 'replan', 'owner',
                        'shallow', 'global_penalty', 'bot_penalty',
                        'macro_penalty'):
            with self.subTest(changed=changed):
                bot, current, goal = REPORT_POSES[0]
                nav, unused_samples, unused_rays = self.scene(current, goal)
                state = {'request_key': ('route', bot, 1)}
                first = nav._fallback_target(bot, current, goal, 0.0, None, state)
                edges = nav.grid._edge_keys_for_segment(current, first)
                self.assertTrue(edges)
                if changed == 'wall':
                    nav.grid.obstacle_probe = lambda *unused: True
                    nav.grid.invalidate_native_review()
                elif changed == 'goal':
                    goal = (current[0], current[1], current[2] - 40.0)
                elif changed == 'request':
                    state['request_key'] = ('route', bot, 2)
                elif changed == 'replan':
                    state['replan_generation'] = 1
                elif changed == 'owner':
                    state['last_target'] = goal
                elif changed == 'shallow':
                    hazards = list(nav.grid._baked_hazards)
                    index = nav.grid._baked_flat_index(nav.grid.cell_for(first))
                    hazards[index] |= BAKED_SHALLOW_WATER
                    nav.grid._baked_hazards = hazards
                    nav.grid._baked_corridor_cache.clear()
                    nav.grid._baked_corridor_order.clear()
                else:
                    penalties = dict((edge, (10.0, 100.0)) for edge in edges)
                    if changed == 'global_penalty':
                        nav.grid._failed_edges.update(penalties)
                    elif changed == 'bot_penalty':
                        nav.bot_failed_edges[bot] = penalties
                    else:
                        nav.bot_macro_edges[bot] = penalties
                choose = mock.Mock(wraps=nav.grid.safe_local_target)
                nav.grid.safe_local_target = choose
                selected = nav._fallback_target(bot, current, goal, 0.1, None, state)
                choose.assert_called_once()
                if changed == 'wall':
                    self.assertNotEqual(first, selected)
                    self.assertEqual('blocked', state['navigation_status'])

    def test_airfield_pair_resumes_real_paths_after_pending_failed_switches(self):
        from test_port_0922_bot_runtime import _combat_descriptor
        from effective_params_fixture import bot_default_crew_factors
        from gui.mods.offline_lan_0922 import loadout
        # 104150 report positions/yaws, the shipped graph and real Driver,
        # traffic and copied physics. Ground, model sweeps and the vehicle
        # descriptors are explicit fixtures, not retail scene acceptance.
        poses = {
            27: ((-286.0, -0.18, -190.0), 0.4407653849388573),
            18: ((-297.176389346006, -0.18, -193.61054246942632),
                 0.7222540553091804),
        }
        goals = {
            27: (-324.01079913921933, -6.35, -5.535386006475318),
            18: (-313.08065044950047, -0.18, -134.89442719099992),
        }
        with mock.patch.object(loadout, 'attribute_factors',
                               bot_default_crew_factors), redirect_stdout(io.StringIO()):
            runtime = BotRuntime(1,
                descriptor_resolver=lambda *args: _combat_descriptor(),
                direction_probe=lambda *args: dict(
                    clear=True, collision=False, slope=0.0),
                spawn_resolver=lambda team, slot: poses[(18, 27)[slot]],
                ground_probe=lambda *args: -0.18,
                physics_ground_probe=lambda *args: -0.18,
                obstacle_probe=lambda *args: False,
                baked_graph=self.graph,
                visibility_probe=lambda *args: False,
                firing_lane_probe=lambda *args: False)
            runtime.battle_start({
                'map': '31_airfield', 'round_id': 1, 'bot_authority_id': 1,
                'bots': [dict(id=bot, team=2, slot=slot,
                              vehicle='fixture', name='Probe')
                         for slot, bot in enumerate((18, 27))]})
            nav = runtime.navigator
            real_path = nav._path
            def delayed_path(key, start, goal, now, avoid, native_capability=None):
                result = real_path(key, start, goal, now, avoid, native_capability)
                # A genuine A* result eventually takes over. Reproduce the
                # report's 28.1-second wait, with synthetic pending/failure
                # alternation to exercise retention across both lifecycles.
                if now < 28.1:
                    return result[0], (() if int(now // 4) % 2 else None)
                return result
            nav._path = delayed_path
            def navigation_target(bot, position, goal, order, state):
                return nav.next_target(bot, position, goal,
                    ('route_join', bot, 'report'), state['now'])
            runtime.adapter.navigation_target = navigation_target
            visited = dict((bot, set()) for bot in poses)
            routed = set()
            for bot in poses:
                nav.grid.review_native_corridor(poses[bot][0], goals[bot])
            for frame in range(600):
                runtime._server_orders = dict((bot, dict(
                    move_position=goal, fire_allowed=False, fire_range=400,
                    combat_mode='route', shell_index=0))
                    for bot, goal in goals.items())
                runtime.update(0.1, frame * 0.1)
                for bot in poses:
                    state = runtime.states[bot]
                    visited[bot].add(nav.grid.cell_for(
                        (state['x'], state['y'], state['z'])))
                    route = nav.bot_states.get(bot, {})
                    if (frame * 0.1 >= 28.1 and
                            nav.paths.get(route.get('path_key')) and
                            route.get('navigation_status') == 'safe' and
                            bot not in nav.fallback_modes):
                        routed.add(bot)
            self.assertEqual(set(poses), routed)
            for bot, (start, unused_yaw) in poses.items():
                state = runtime.states[bot]
                net = math.hypot(state['x'] - start[0], state['z'] - start[2])
                initial_error = math.hypot(start[0] - goals[bot][0],
                                          start[2] - goals[bot][2])
                final_error = math.hypot(state['x'] - goals[bot][0],
                                        state['z'] - goals[bot][2])
                self.assertGreater(net, nav.grid.cell_size * 8)
                self.assertLess(final_error, initial_error * 0.6)
                self.assertGreater(len(visited[bot]), 8)

    def test_occupied_ground_must_exist_before_sampling_the_exit(self):
        bot, current, goal = REPORT_POSES[0]
        nav, unused_samples, unused_rays = self.scene(current, goal)
        target = nav._pending_target(bot, current, goal, 1.0,
                                     {'pending_since': 0.0})
        for missing in (None, float('nan'), float('inf')):
            with self.subTest(missing=missing):
                nav, samples, rays = self.scene(current, goal,
                    ground=lambda x, z, hint: missing if (x, z) ==
                    (current[0], current[2]) else current[1])
                self.assertFalse(nav.grid.segment_clear(current, target))
                self.assertEqual(1, len(samples))
                self.assertFalse(rays)

    def test_grounded_origin_does_not_turn_hull_clearance_into_a_slope(self):
        bot, current, goal = REPORT_POSES[0]
        ground_y = current[1] - 0.6
        nav, samples, rays = self.scene(current, goal,
                                       ground=lambda *unused: ground_y)
        target = nav._pending_target(bot, current, goal, 1.0,
                                     {'pending_since': 0.0})
        self.assertNotEqual(current, target)
        self.assertTrue(nav.grid.segment_clear(current, target))
        self.assertTrue(all(start == (current[0], ground_y, current[2])
                            for start, end, width in rays))

    def test_nearest_diagonal_connector_includes_the_raw_half_cell_offset(self):
        from test_port_0922_bot_runtime import _flat_open_graph
        graph = _flat_open_graph()
        graph['heights_mm'] = [None] * len(graph['heights_mm'])
        graph['heights_mm'][17 * graph['width'] + 17] = 0
        current, goal = (-1.99, 0.0, -1.99), (40.0, 0.0, 40.0)
        ground = mock.Mock(return_value=0.0)
        obstacle = mock.Mock(return_value=False)
        nav = TerrainNavigator(ground, obstacle, baked_graph=graph)
        nav.grid.review_native_corridor(current, goal)
        target = nav.grid.safe_local_target(current, goal, 1.0)
        self.assertEqual((8.0, 0.0, 8.0), target)
        self.assertGreater(math.hypot(target[0] - current[0],
                                      target[2] - current[2]),
                           nav.grid.cell_size * 2.0 * math.sqrt(2.0))
        self.assertLessEqual(ground.call_count, 10)
        self.assertEqual(1, obstacle.call_count)

    def test_himmelsdorf_reported_corners_can_rejoin_from_eroded_cells(self):
        graph = json.loads((ROOT / 'navgraphs/86_himmelsdorf_winter.json').read_text())
        # Report 040924 native poses; support/obstacle replies remain fixtures.
        cases = (
            (2, (179.5970318176, 1.1000003815, -16.071486159), (162., 0., -10.)),
            (6, (320.8798389848, 7.4163742065, -267.71910971), (394., 0., -198.)),
            (7, (183.8932076127, 1.1000003815, -39.3217384245), (2.505, 0., -252.6)),
            (19, (370.737776053, 42.5325088501, -93.1834492886), (390., 0., -106.)),
        )
        for bot, current, goal in cases:
            with self.subTest(bot=bot):
                rays = []
                def obstacle(start, end, width):
                    rays.append((start, end, width))
                    return False
                nav = TerrainNavigator(lambda *unused: current[1], obstacle,
                                       baked_graph=graph)
                self.assertIsNone(nav.grid._baked_cell_height(nav.grid.cell_for(current)))
                nav.grid.review_native_corridor(current, goal)
                target = nav._pending_target(bot, current, goal, 1.0,
                                             {'pending_since': 0.0})
                self.assertNotEqual(current, target)
                self.assertTrue(rays)
                self.assertTrue(nav.grid.dry_segment_clear(current, target, 1.0))

    def test_reported_occupied_cells_can_exit_after_native_review(self):
        for bot, current, goal in REPORT_POSES:
            with self.subTest(bot=bot):
                nav, samples, rays = self.scene(current, goal)
                self.assertIsNone(nav.grid._baked_cell_height(nav.grid.cell_for(current)))
                state = {'pending_since': 0.0}
                target = nav._pending_target(bot, current, goal, 1.0, state)
                self.assertNotEqual(current, target)
                self.assertGreater(math.hypot(target[0]-current[0], target[2]-current[2]), 1.5)
                self.assertTrue(nav.grid.dry_segment_clear(current, target, 1.0))
                self.assertTrue(samples)
                self.assertTrue(any(ray[0] == current and ray[1][0] == target[0]
                                    and ray[1][2] == target[2] for ray in rays))
                self.assertEqual('pending', state['navigation_status'])

    def test_pending_real_navigator_releases_driver_at_simulated_cadences(self):
        descriptor = {'type': {'name': 'fixture', 'tags': ('mediumTank',)},
                      'physics': {'speedLimits': (18.0,)}, 'hull': {},
                      'turret': {}, 'gun': {'shots': ()}}
        yaws = (-1.6190630752041757, -0.33802882616391305,
                -0.35569306375834053, 2.6040338608473577,
                -0.25993647587052265, 2.6744495558238204)
        for (bot, current, goal), yaw in zip(REPORT_POSES, yaws):
            for fps in (60, 15, 5):
                with self.subTest(bot=bot, fps=fps):
                    nav, samples, rays = self.scene(current, goal)
                    def navigation_target(bot_id, position, target, order, state):
                        return nav.next_target(bot_id, position, target,
                            ('join', bot_id, nav.grid.cell_for(position)), state['now'])
                    adapter = BotAdapter('31_airfield', 7,
                                         navigation_target=navigation_target)
                    adapter.register(bot, 1, descriptor)
                    state = {'id': bot, 'slot': 0, 'position': current,
                             'yaw': yaw, 'speed': 0.0, 'dt': 1.0 / fps,
                             'now': 0.0, 'neighbours': ()}
                    strategic = {'move_position': goal, 'fire_allowed': False,
                                 'fire_range': 400.0, 'combat_mode': 'route',
                                 'shell_index': 0}
                    # Keep a genuine A* job queued: this is a pending-job exit,
                    # not a test replacing the navigator with a clear fake.
                    for frame in range(fps + 1):
                        nav.begin_frame(0.0)
                        nav.search_credit = 0.0
                        state['now'] = float(frame) / fps
                        order = adapter.decide_with_order(state, strategic,
                                                           lambda angle: True)
                        nav.end_frame()
                    self.assertTrue(nav.searches)
                    self.assertNotEqual('nav_wait', order['recovery_mode'])
                    self.assertTrue(order['movement_intent'])
                    self.assertTrue(abs(order['turn']) > 0.0 or
                                    abs(order['throttle']) > 0.0)
                    self.assertEqual(current, state['position'])
                    # Once facing the proven local waypoint the ordinary
                    # driver must request travel, not retain a waiting brake.
                    local = order['move_position']
                    state['yaw'] = math.atan2(local[0] - current[0],
                                              local[2] - current[2])
                    nav.begin_frame(0.0)
                    nav.search_credit = 0.0
                    state['now'] += 1.0 / fps
                    order = adapter.decide_with_order(state, strategic,
                                                       lambda angle: True)
                    nav.end_frame()
                    self.assertGreater(order['throttle'], 0.0)
                    self.assertFalse(order['brake'])
                    self.assertEqual(current, state['position'])

    def test_adjacent_missing_cell_does_not_require_a_snapped_graph_link(self):
        # This is a generated pose after 0.37 m of integrated movement from
        # report Bot 30, not an additional captured Windows position. The
        # nearest-cell corridor changes, but the actual native ray stays clear.
        bot, unused_current, goal = REPORT_POSES[-1]
        current = (-267.85435219250706, 0.12851428985595703,
                   -137.93039933299315)
        yaw = math.atan2(goal[0] - current[0], goal[2] - current[2]) - 1.75
        target = (current[0] + math.sin(yaw) * 3.12, current[1],
                  current[2] + math.cos(yaw) * 3.12)
        nav, samples, rays = self.scene(current, goal)
        self.assertIsNone(nav.grid._baked_cell_height(nav.grid.cell_for(current)))
        self.assertEqual((False, 0), nav.grid._baked_corridor(current, target))
        self.assertTrue(nav.grid.dry_segment_clear(current, target, 1.0))
        self.assertTrue(samples)
        self.assertEqual(current, rays[-1][0])
        # The earlier bypass must still require each native safety condition.
        nav.grid.obstacle_probe = lambda *args: True
        self.assertFalse(nav.grid.dry_segment_clear(current, target, 1.0))
        nav.grid.obstacle_probe = lambda *args: False
        for height in (None, float('nan'), 20.0, -20.0):
            nav.grid.ground_probe = lambda x, z, hint: (
                current[1] if (x, z) == (current[0], current[2]) else height)
            self.assertFalse(nav.grid.dry_segment_clear(current, target, 1.0))

    def test_integrated_motion_continues_beyond_the_first_missing_cell(self):
        from test_port_0922_bot_runtime import _combat_descriptor
        from effective_params_fixture import bot_default_crew_factors
        from gui.mods.offline_lan_0922 import loadout
        # Exercise the real integrator, not just a positive drive command at
        # a fixed pose. Native probes are flat/clear; the generic descriptor
        # is deliberately not presented as these vehicles' retail physics.
        with mock.patch.object(loadout, 'attribute_factors',
                               bot_default_crew_factors), redirect_stdout(io.StringIO()):
            for bot, current, goal in REPORT_POSES + REPORT_INTERIOR_POSES:
                for fps in (5, 15):
                    with self.subTest(bot=bot, fps=fps):
                        runtime = BotRuntime(1,
                            descriptor_resolver=lambda *args: _combat_descriptor(),
                            direction_probe=lambda *args: dict(
                                clear=True, collision=False, slope=0.0),
                            spawn_resolver=lambda *args: (current, math.atan2(
                                goal[0] - current[0], goal[2] - current[2])),
                            ground_probe=lambda *args: current[1],
                            physics_ground_probe=lambda *args: current[1],
                            obstacle_probe=lambda *args: False,
                            baked_graph=self.graph,
                            visibility_probe=lambda *args: False,
                            firing_lane_probe=lambda *args: False)
                        runtime.battle_start({
                            'map': '31_airfield', 'round_id': fps,
                            'bot_authority_id': 1,
                            'bots': [{'id': bot, 'team': 1 if bot < 16 else 2,
                                      'slot': 0, 'vehicle': 'fixture', 'name': 'Probe',
                                      'profile': {
                                          'class_tag': 'mediumTank',
                                          'dominant_role': 'support',
                                          'roles': {'support': 1.0}, 'shells': [],
                                          'vehicle_name': 'fixture',
                                          'desired_range': 200, 'fire_range': 500}}]})
                        nav = runtime.navigator
                        nav.grid.review_native_corridor(current, goal)
                        nav.search_credit = 0.0
                        # A* remains pending for the whole test. Local egress
                        # must not depend on it happening to complete first.
                        nav._accrue_search_credit = lambda elapsed: None
                        def navigation_target(bot_id, position, target, order, state):
                            return nav.next_target(bot_id, position, target,
                                ('join', bot_id, nav.grid.cell_for(position)), state['now'])
                        runtime.adapter.navigation_target = navigation_target
                        duration = 20 if bot == 30 else 6
                        for frame in range(fps * duration):
                            runtime._server_orders = {bot: {
                                'move_position': goal, 'fire_allowed': False,
                                'fire_range': 400, 'combat_mode': 'route', 'shell_index': 0}}
                            runtime.update(1.0 / fps, float(frame) / fps)
                        state = runtime.states[bot]
                        displacement = math.hypot(state['x'] - current[0],
                                                  state['z'] - current[2])
                        self.assertTrue(state['alive'])
                        self.assertGreater(displacement, 5.0 if bot == 30 else 1.5)

    def test_interior_report_poses_join_the_existing_nearest_cell(self):
        for bot, current, goal in REPORT_INTERIOR_POSES:
            with self.subTest(bot=bot):
                nav, samples, rays = self.scene(current, goal)
                cell = nav.grid._nearest_baked_cell(nav.grid.cell_for(current), 2)
                expected = nav.grid.point_for(cell, nav.grid._baked_cell_height(cell))
                target = nav._pending_target(bot, current, goal, 1.0,
                                             {'pending_since': 0.0})
                self.assertEqual(expected, target)
                self.assertLessEqual(len(samples), 10)
                self.assertEqual(1, len(rays))
                self.assertEqual(current, rays[0][0])
                # One known node is a candidate, not permission to skip a wall,
                # a failed edge, native ground proof or the caller's side gate.
                nav.grid.obstacle_probe = lambda *args: True
                self.assertIsNone(nav.grid.safe_local_target(current, goal, 2.0))
                nav.grid.obstacle_probe = lambda *args: False
                penalties = {edge: 1.0 for edge in
                             nav.grid._edge_keys_for_segment(current, target)}
                self.assertIsNone(nav.grid.safe_local_target(current, goal, 2.0,
                                                             edge_penalties=penalties))
                self.assertIsNone(nav.grid.safe_local_target(current, goal, 2.0,
                                                             minimum_offset=math.pi))
                nav.grid.ground_probe = lambda *args: None
                self.assertIsNone(nav.grid.safe_local_target(current, goal, 2.0))

    def test_native_connector_work_is_bounded_before_a_long_route(self):
        bot, current, goal = REPORT_POSES[0]
        nav, samples, rays = self.scene(current, goal)
        self.assertFalse(nav.grid._live_baked_egress_clear(current,
            (current[0] + 200.0, current[1], current[2])))
        self.assertFalse(samples)
        self.assertFalse(rays)
        local = nav._pending_target(bot, current, goal, 1.0,
                                    {'pending_since': 0.0})
        self.assertNotEqual(current, local)
        samples[:] = []
        rays[:] = []
        self.assertTrue(nav.grid.segment_clear(current, local))
        self.assertLessEqual(len(samples), 10)
        self.assertEqual(1, len(rays))

    def test_unreviewed_and_valid_baked_links_keep_the_existing_cache(self):
        bot, current, goal = REPORT_POSES[0]
        nav, samples, rays = self.scene(current, goal)
        cell = nav.grid._nearest_baked_cell(nav.grid.cell_for(current), 2)
        first = nav.grid.point_for(cell, nav.grid._baked_cell_height(cell))
        other = next(neighbour for dx, dz, unused in nav.grid._NEIGHBOURS
                     for neighbour in [(cell[0] + dx, cell[1] + dz)]
                     if nav.grid._baked_edge_height(cell, neighbour) is not None)
        second = nav.grid.point_for(other, nav.grid._baked_cell_height(other))
        self.assertTrue(nav.grid.segment_clear(first, second))
        calls = len(rays)
        self.assertGreater(calls, 0)
        self.assertTrue(nav.grid.segment_clear(first, second))
        self.assertEqual(calls, len(rays))
        self.assertFalse(samples)
        nav.grid._native_review_cells.clear()
        nav.grid.invalidate_native_review()
        self.assertTrue(nav.grid.segment_clear(first, second))
        self.assertEqual(calls, len(rays))

    def test_stall_evidence_records_real_grid_cell_without_native_work(self):
        bot, current, goal = REPORT_POSES[0]
        nav, samples, rays = self.scene(current, goal)
        nav.bot_states[bot] = {'navigation_status': 'pending',
                               'pending_since': 0.0}
        runtime = BotRuntime.__new__(BotRuntime)
        runtime.navigator = nav
        runtime.native_motion = False
        runtime._decision_cache = {}
        runtime._server_orders = {}
        runtime._hard_contact_grinds = {}
        state = {'id': bot, 'x': current[0], 'y': current[1], 'z': current[2]}
        command = {'recovery_mode': 'nav_wait', 'movement_intent': True}
        output = io.StringIO()
        with redirect_stdout(output):
            for now in (0.0, 2.9, 3.0, 3.1):
                runtime._log_motion_stall(state, command, 0.0, 0.0, True, {}, now)
        self.assertEqual(1, output.getvalue().count('[BOT STALL]'))
        evidence = state['_motion_stall_pending']['navigation']
        self.assertEqual((210, 84), evidence['current_cell'])
        self.assertIsNone(evidence['current_cell_height'])
        self.assertEqual(3000, evidence['pending_ms'])
        self.assertGreater(evidence['native_review_cells'], 0)
        self.assertEqual([], samples)
        self.assertEqual([], rays)

    def test_actual_exit_is_checked_not_a_snapped_surrogate(self):
        bot, current, goal = REPORT_POSES[0]
        nav, samples, rays = self.scene(current, goal)
        target = nav._pending_target(bot, current, goal, 1.0, {'pending_since': 0.0})
        self.assertNotEqual(current, target)
        snapped = nav.grid._nearest_baked_cell(nav.grid.cell_for(current), 2)
        centre = nav.grid.point_for(snapped, nav.grid._baked_cell_height(snapped))
        self.assertNotEqual(current, centre)
        self.assertTrue(any(start == current for start, end, width in rays))
        # A wall crossed only by the real pose must not be bypassed by snapping.
        nav.grid.obstacle_probe = lambda start, end, width: tuple(start) == current
        self.assertFalse(nav.grid.segment_clear(current, target))

    def test_hard_wall_still_holds_every_reported_pose(self):
        for bot, current, goal in REPORT_POSES:
            with self.subTest(bot=bot):
                nav, unused_samples, unused_rays = self.scene(current, goal,
                    obstacle=lambda *args: True)
                self.assertEqual(current, nav._pending_target(
                    bot, current, goal, 1.0, {'pending_since': 0.0}))

    def test_missing_nonfinite_and_wrong_layer_support_hold(self):
        bot, current, goal = REPORT_POSES[0]
        for height in (None, float('nan'), float('inf'), -float('inf'), 20.0, -20.0):
            with self.subTest(height=height):
                nav, unused_samples, unused_rays = self.scene(current, goal,
                    # The same-layer producer supplies occupied support; an
                    # exit that jumps to another layer must still be refused.
                    ground=lambda x, z, hint: current[1] if (x, z) ==
                    (current[0], current[2]) else height)
                self.assertEqual(current, nav._pending_target(
                    bot, current, goal, 1.0, {'pending_since': 0.0}))

    def test_probe_exception_does_not_grant_an_exit(self):
        bot, current, goal = REPORT_POSES[0]
        def failed(*args):
            raise RuntimeError('native query unavailable')
        for kwargs in ({'ground': failed}, {'obstacle': failed}):
            with self.subTest(kwargs=kwargs):
                nav, unused_samples, unused_rays = self.scene(current, goal, **kwargs)
                self.assertEqual(current, nav._pending_target(
                    bot, current, goal, 1.0, {'pending_since': 0.0}))

    def test_no_unsafe_cell_entry_or_out_of_bounds_shortcut(self):
        bot, current, goal = REPORT_POSES[0]
        nav, samples, rays = self.scene(current, goal)
        target = nav._pending_target(bot, current, goal, 1.0, {'pending_since': 0.0})
        self.assertNotEqual(current, target)
        index = nav.grid._baked_flat_index(nav.grid.cell_for(target))
        for hazard in (1, 2, 4):
            nav.grid._baked_hazards[index] = hazard
            nav.grid._baked_corridor_cache.clear()
            nav.grid._baked_corridor_order.clear()
            self.assertFalse(nav.grid.dry_segment_clear(current, target, 1.0))
        self.assertFalse(nav.grid.segment_clear(current, (9999.0, 0, 9999.0)))

    def test_egress_is_not_cached_across_native_changes(self):
        bot, current, goal = REPORT_POSES[0]
        nav, samples, rays = self.scene(current, goal)
        target = nav._pending_target(bot, current, goal, 1.0, {'pending_since': 0.0})
        self.assertNotEqual(current, target)
        nav.grid.obstacle_probe = lambda *args: True
        self.assertFalse(nav.grid.segment_clear(current, target))
        nav.grid.obstacle_probe = lambda *args: False
        self.assertTrue(nav.grid.segment_clear(current, target))


if __name__ == '__main__':
    unittest.main()
