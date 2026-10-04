"""Report 133334: a reached slope setup must advance in pending A* too.

The two E-25 poses and terrain graph are real report inputs. Finite native
walls below are controlled fixtures that prevent a direct-goal shortcut;
they do not reproduce the report's complete Windows collision scene.
"""
import json
import math
from pathlib import Path
import unittest

from test_port_0922_navigation import TerrainNavigator
from gui.mods.offline_lan_0922 import vehicle_physics
from gui.mods.offline_lan_0922.ai.adapter import BotAdapter
from gui.mods.offline_lan_0922.ai.driver import WAYPOINT_ARRIVAL_RADIUS
from gui.mods.offline_lan_0922.ai.navigation import BAKED_SHALLOW_WATER


ROOT = Path(__file__).resolve().parents[1]
REPORT_STOPS = (
    ((-142.69017683182372, -13.462967872619629, -125.66306132344197),
     (-142.0, -13.353, -126.0), (-138.0, -12.671, -126.0),
     (-114.0, 0.0, -126.0), (-130.0, -132.0, -130.0, -120.0)),
    ((-206.2115402758697, -14.265606880187988, -109.05774943470512),
     (-206.0, -14.33, -110.0), (-210.0, -13.918, -110.0),
     (-234.0, 0.0, -110.0), (-218.0, -116.0, -218.0, -104.0)),
)


def distance(first, second):
    return math.hypot(second[0] - first[0], second[2] - first[2])


class PendingClimbScene:
    def __init__(self, sample):
        self.current, self.start, self.onward, self.goal, wall = sample
        self.walls = [wall]
        self.deferred_walls = []
        graph = json.loads((ROOT / 'navgraphs/31_airfield.json').read_text())
        self.nav = TerrainNavigator(lambda *unused: None, self.obstacle,
                                    baked_graph=graph)
        self.request = ('route_join', 25, 'report_climb')
        self.key = self.nav._cache_key(self.request, self.goal)
        self.nav.grid.review_native_corridor(self.start, self.goal)
        self.target(self.start, 0.0)
        self.search = self.nav.searches[self.key]
        for unused in range(500):
            self.search.step(1)
            if len(self.search.proved_prefix(self.nav.grid)) >= 2:
                break
            assert not self.search.done
        assert self.search.proved_prefix(self.nav.grid)[:2] == (
            self.start, self.onward)

    @staticmethod
    def intersects(start, end, width, rectangle):
        x0, z0, x1, z1 = rectangle
        near, far = 0.0, 1.0
        for position, delta, low, high in (
                (start[0], end[0] - start[0], x0 - width, x1 + width),
                (start[2], end[2] - start[2], z0 - width, z1 + width)):
            if abs(delta) < 1e-9:
                if not low <= position <= high:
                    return False
            else:
                first, last = (low - position) / delta, (high - position) / delta
                near, far = max(near, min(first, last)), min(far, max(first, last))
                if near > far:
                    return False
        return True

    def obstacle(self, start, end, width):
        if any(self.intersects(start, end, width, wall) for wall in self.walls):
            return True
        if any(self.intersects(start, end, width, wall)
               for wall in self.deferred_walls):
            return 'deferred'
        return False

    def target(self, position, now):
        self.nav.begin_frame(0.0)
        try:
            return self.nav.next_target(
                25, position, self.goal, self.request, now)
        finally:
            self.nav.end_frame()


class PendingClimbArrivalTests(unittest.TestCase):
    def test_both_report_stops_consume_reached_setup_before_search_completes(self):
        for sample in REPORT_STOPS:
            with self.subTest(start=sample[1]):
                scene = PendingClimbScene(sample)
                self.assertLess(distance(scene.current, scene.start),
                                WAYPOINT_ARRIVAL_RADIUS)
                self.assertFalse(scene.nav.grid.live_shortcut_preserves_climb_approach(
                    scene.current, (scene.start, scene.onward), 0, 1))
                self.assertEqual(scene.onward, scene.target(scene.current, 1.0))
                self.assertFalse(scene.search.done)
                state = scene.nav.bot_states[25]
                state['temporary_stalled'] = True
                state['temporary_visited_cells'] = {
                    scene.nav.grid.cell_for(scene.start)}
                self.assertFalse(scene.nav._temporary_path_repeats(
                    state, state['pending_prefix'], state['pending_prefix_index']))
                for step in range(3):
                    scene.search.step(20)
                    self.assertFalse(scene.search.done)
                    self.assertEqual(scene.onward,
                                     scene.target(scene.current, 1.1 + step * 0.1))

    def test_unreached_turning_setup_remains_required(self):
        for sample in REPORT_STOPS:
            with self.subTest(start=sample[1]):
                scene = PendingClimbScene(sample)
                before = (scene.start[0] + (scene.current[0] - scene.start[0]) * 3.0,
                          scene.current[1],
                          scene.start[2] + (scene.current[2] - scene.start[2]) * 3.0)
                self.assertGreater(distance(before, scene.start),
                                   WAYPOINT_ARRIVAL_RADIUS)
                self.assertEqual(scene.start, scene.target(before, 1.0))

    def test_reached_setup_does_not_grant_wall_shallow_or_deferred_edge(self):
        for change in ('wall', 'shallow', 'deferred', 'penalty'):
            with self.subTest(change=change):
                scene = PendingClimbScene(REPORT_STOPS[0])
                if change in ('wall', 'deferred'):
                    # The added wall covers the onward cell, while the
                    # already reached setup and its actual connector stay clear.
                    wall = (-136.0, -130.0, -136.0, -122.0)
                    (scene.walls if change == 'wall' else
                     scene.deferred_walls).append(wall)
                    scene.nav.grid.invalidate_native_review()
                elif change == 'shallow':
                    grid = scene.nav.grid
                    hazards = list(grid._baked_hazards)
                    hazards[grid._baked_flat_index(grid.cell_for(scene.onward))] |= (
                        BAKED_SHALLOW_WATER)
                    grid._baked_hazards = hazards
                    grid._baked_corridor_cache.clear()
                    grid._baked_corridor_order.clear()
                else:
                    scene.nav.bot_failed_edges[25] = dict(
                        (edge, (100.0, 240.0)) for edge in
                        scene.nav.grid._edge_keys_for_segment(scene.current, scene.onward))
                state = scene.nav.bot_states[25]
                target = scene.nav._pending_search_target(
                    25, scene.current, scene.goal, 1.0, state, scene.key, None)
                self.assertNotEqual(scene.onward, target)
                self.assertFalse(state.get('controlled_shallow_target'))

    def test_report_e25_drives_up_onward_edge_with_copied_longitudinal_physics(self):
        # Exact report kinetic inputs. Unreported speed caps use the copied
        # fixture defaults; this short launch remains well below either cap.
        # Starting aligned removes any dependency on an unreported turn rate.
        params = dict(vehicle_physics._DEFAULTS, mass=26300.0, powerW=514850.0,
                      terrainResist=(1.2051823317100694, 1.3056140929158224,
                                     2.309932662766179), specificFriction=0.6867)
        for sample in REPORT_STOPS:
            with self.subTest(start=sample[1]):
                scene = PendingClimbScene(sample)
                position, initial = scene.current, scene.current
                reach = distance(position, scene.onward)
                unit_x = (scene.onward[0] - position[0]) / reach
                unit_z = (scene.onward[2] - position[2]) / reach
                slope = (scene.onward[1] - position[1]) / reach
                yaw = math.atan2(unit_x, unit_z)
                speed = maximum_speed = 0.0
                adapter = BotAdapter('31_airfield', 133334,
                    navigation_target=lambda bot, current, goal, order, state:
                        scene.target(current, state['now']))
                for tick in range(50):
                    if distance(position, scene.onward) <= WAYPOINT_ARRIVAL_RADIUS:
                        break
                    state = dict(id=25, team=2, slot=1, position=position,
                                 yaw=yaw, speed=speed, dt=0.1, now=1.0 + tick * 0.1,
                                 half_width=1.4158929586410522,
                                 half_length=2.169811964035034,
                                 turn_speed_limit=None, decision_horizon=0.1)

                    def direction_clear(angle, maximum_distance=None):
                        length = (state.get('navigation_probe_distance', 15.0)
                                  if maximum_distance is None else maximum_distance)
                        end = (position[0] + math.sin(angle) * length, position[1],
                               position[2] + math.cos(angle) * length)
                        return scene.obstacle(position, end, state['half_width']) is False

                    command = adapter.decide_with_order(
                        state, dict(combat_mode='route', move_position=scene.goal,
                                    face_position=scene.onward),
                        direction_clear)
                    self.assertAlmostEqual(0.0, command['turn'], places=7)
                    for unused in range(5):
                        speed = vehicle_physics.longitudinal_step(
                            params, speed, command['throttle'], command['turn'],
                            math.atan(slope), 0.02, handbrake=command['brake'])
                        following = (position[0] + unit_x * speed * 0.02,
                                     position[1] + slope * speed * 0.02,
                                     position[2] + unit_z * speed * 0.02)
                        self.assertIs(False, scene.obstacle(position, following, 1.4158929586410522))
                        position = following
                        maximum_speed = max(maximum_speed, abs(speed))
                self.assertLessEqual(distance(position, scene.onward), WAYPOINT_ARRIVAL_RADIUS)
                self.assertGreater(distance(initial, position), 2.0)
                self.assertGreater(position[1] - initial[1], 0.2)
                self.assertLess(maximum_speed, params['speedFwd'])


if __name__ == '__main__':
    unittest.main()
