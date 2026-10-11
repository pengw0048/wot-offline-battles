"""Report 122105 mobility in a finite wall scene across real A* retries.

The wall model and unreported speed limits are explicit test fixtures. This
checks navigation, real local controls and copied physics, not native gameplay.
"""
import math
import unittest

from test_port_0922_navigation_pending_prefix import WallScene
from gui.mods.offline_lan_0922 import vehicle_physics
from gui.mods.offline_lan_0922.ai.adapter import BotAdapter
from gui.mods.offline_lan_0922.ai.driver import WAYPOINT_ARRIVAL_RADIUS


# Installed values from report 20260922-122105 motion captures. Full turn
# rates follow atan2(integrated-start)/drive_speed and the published slice dt.
REPORT_MOBILITY = (
    (7, 'Panther II', 54710.0, 514850.0, 36.0,
     (0.8034548678526957, 1.0043185698503567, 2.1090689009064665),
     1.6587040424346924, 3.0905840396881104),
    (9, 'M36', 29084.0, 308910.0, 30.0,
     (1.104750450780213, 1.3056140929158224, 2.309932662766179),
     1.2669010162353516, 2.991621971130371),
    (27, 'SU-122-44', 32800.0, 367750.0, 42.0,
     (0.9587727708533078, 1.1505273707418178, 1.9175455417066156),
     1.5697760581970215, 2.8677990436553955),
)


class NavigationRetryMotionTests(unittest.TestCase):
    def run_scene(self, mobility, fps):
        bot, name, mass, power, rate, resistance, width, length = mobility
        scene = WallScene()
        nav = scene.navigator()
        # A real small bounded A* search fails while the only exit initially
        # leads away from the goal. Restore ordinary capacity after three
        # failures; geometry never changes and no search result is fabricated.
        nav.search_max_expansions = 5
        params = dict(vehicle_physics._DEFAULTS, mass=mass, powerW=power,
                      rotSpd=math.radians(rate), terrainResist=resistance)
        position = (0.0, 0.0, 12.0)
        anchor = (0.0, 0.0, -20.0)
        goal = (0.0, 0.0, 40.0)
        request = ('route_join', bot, 'retry_wall')
        yaw, speed, omega = math.pi, 0.0, 0.0
        starts = []
        begin_plan = nav.grid.begin_plan

        def observed_plan(start, *args, **kwargs):
            starts.append((tuple(start), position))
            return begin_plan(start, *args, **kwargs)

        nav.grid.begin_plan = observed_plan

        def local_target(bot_id, current, target, order, state):
            return nav.next_target(bot_id, current, target, request,
                                   state['now'], anchor=anchor)

        adapter = BotAdapter('31_airfield', 122105, navigation_target=local_target)

        def occupied(point, angle):
            for x0, z0, x1, z1 in scene.walls:
                if adapter.driver._obb_overlap(
                        point, angle, length, width,
                        ((x0 + x1) * 0.5, 0.0, (z0 + z1) * 0.5),
                        0.0, (z1 - z0) * 0.5, (x1 - x0) * 0.5):
                    return True
            return False

        elapsed = 0.0
        exited = False
        failed_positions = []
        previous_failures = 0
        search_credit = 0.0
        reverse_distance = 0.0
        navigation_reverse_distance = 0.0
        navigation_replans = 0
        for frame in range(fps * 120):
            dt = 1.0 / fps
            elapsed = frame * dt
            nav.begin_frame(0.0)
            state = {'id': bot, 'team': 1, 'slot': 1, 'position': position,
                     'yaw': yaw, 'speed': speed, 'dt': dt, 'now': elapsed,
                     'half_length': length, 'half_width': width,
                     'turn_speed_limit': params['rotSpd'], 'decision_horizon': dt}
            # Match the runtime's obstruction-evidence gate. A slow but healthy
            # search alone cannot request a physical escape. This analytic
            # fixture has no contact grind impulse; erased baked cells still
            # require the same safe-support gate as the production runtime.
            nav_state = nav.bot_states.get(bot, {})
            state['navigation_recovery_allowed'] = bool(
                nav_state.get('hard_contact_episode') or
                nav_state.get('blocked_step_tracker') or
                nav.grid._baked_cell_height(nav.grid.cell_for(position)) is None)

            def direction_clear(angle, maximum_distance=None):
                distance = (state.get('navigation_probe_distance', 15.0)
                            if maximum_distance is None else maximum_distance)
                end = (position[0] + math.sin(angle) * distance, 0.0,
                       position[2] + math.cos(angle) * distance)
                if not scene.blocked(position, position, width):
                    return not scene.blocked(position, end, width)
                # Near the open wall endpoint a legal hull can occupy a corner
                # of the square inflated broadphase: Panther II at approximately
                # (-6.42, -11.47) was one such pose. A ray starting there reports
                # every heading blocked, including travel away from the wall.
                # Resolve that ambiguous start with the complete physical OBB
                # sweep; never simply ignore a hit that starts at the origin.
                return not any(adapter.driver._obb_overlap(
                    ((position[0] + end[0]) * 0.5, 0.0,
                     (position[2] + end[2]) * 0.5),
                    angle, length + distance * 0.5, width,
                    ((x0 + x1) * 0.5, 0.0, (z0 + z1) * 0.5),
                    0.0, (z1 - z0) * 0.5, (x1 - x0) * 0.5)
                    for x0, z0, x1, z1 in scene.walls)

            command = adapter.decide_with_order(
                state, {'combat_mode': 'route', 'move_position': goal},
                direction_clear)
            if command.pop('navigation_replan', False):
                nav.request_replan(bot, position, elapsed)
                navigation_replans += 1
            # The same 15 credits/second keeps genuine pending phases visible
            # without also starving A* merely because controls run at 5 Hz.
            search_credit += dt * 15.0
            work = int(search_credit)
            search_credit -= work
            for key, search in list(nav.searches.items()):
                search.step(work)
                if search.done:
                    nav._finish_search(key, search, elapsed)
            nav.end_frame()
            if nav.search_failed > previous_failures:
                failed_positions.append(position)
                previous_failures = nav.search_failed
            if nav.search_failed >= 3:
                nav.search_max_expansions = 4096

            remaining = dt
            while remaining > 1e-8:
                step = min(remaining, 0.02)
                omega = vehicle_physics.traverse_step(
                    params, omega, command['turn'], speed, step,
                    drive_intent=command['throttle'])
                candidate_yaw = (yaw + omega * step + math.pi) % (2.0 * math.pi) - math.pi
                if not occupied(position, candidate_yaw):
                    yaw = candidate_yaw
                else:
                    omega = 0.0
                speed = vehicle_physics.longitudinal_step(
                    params, speed, command['throttle'], command['turn'],
                    0.0, step, handbrake=command['brake'])
                following = (position[0] + math.sin(yaw) * speed * step, 0.0,
                             position[2] + math.cos(yaw) * speed * step)
                if not occupied(following, yaw):
                    travelled = math.hypot(following[0] - position[0],
                                           following[2] - position[2])
                    if speed < 0.0:
                        reverse_distance += travelled
                        if command.get('navigation_recovery'):
                            navigation_reverse_distance += travelled
                    if travelled > 0.000001:
                        nav.clear_blocked_contact(bot)
                    position = following
                else:
                    attempted_yaw = yaw + (math.pi if speed < 0.0 else 0.0)
                    # The production final-motion veto feeds both consumers:
                    # driver's finite failed-heading memory and navigator's
                    # stable realised edge. Reporting only the intended target
                    # lets recovery repeat a physically disproved manoeuvre.
                    adapter.driver.remember_failure(bot, attempted_yaw, 5.0)
                    nav.report_hard_contact(
                        bot, position, command['move_position'], attempted_yaw, elapsed)
                    speed = 0.0
                self.assertFalse(occupied(position, yaw))
                remaining -= step
            # The U opens at z=-10. A whole hull must leave that mouth before
            # driving around either side and reaching the goal above its cap.
            exited = exited or position[2] + length < -10.0
            if math.hypot(position[0] - goal[0], position[2] - goal[2]) <= WAYPOINT_ARRIVAL_RADIUS:
                break
        return dict(position=position, goal=goal, elapsed=elapsed,
                    failed=nav.search_failed, starts=starts, exited=exited,
                    failed_positions=failed_positions, name=name,
                    reverse_distance=reverse_distance,
                    navigation_reverse_distance=navigation_reverse_distance,
                    navigation_replans=navigation_replans)

    def test_report_vehicles_leave_real_dead_end_after_failed_private_retries(self):
        for mobility in REPORT_MOBILITY:
            for fps in (5, 15):
                with self.subTest(vehicle=mobility[1], fps=fps):
                    result = self.run_scene(mobility, fps)
                    self.assertGreaterEqual(result['failed'], 3)
                    self.assertTrue(result['exited'], result)
                    self.assertLessEqual(math.hypot(
                        result['position'][0] - result['goal'][0],
                        result['position'][2] - result['goal'][2]),
                        WAYPOINT_ARRIVAL_RADIUS, result)
                    # The published route anchor lies behind this already
                    # advanced hull. Every actual private retry belongs to the
                    # live pose, not a new connector back to the spawn anchor.
                    self.assertGreaterEqual(len(result['starts']), 3)
                    for start, live in result['starts']:
                        self.assertEqual(start, live)


if __name__ == '__main__':
    unittest.main()
