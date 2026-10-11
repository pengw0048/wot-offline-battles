"""14:46 report poses; analytical native stand-ins are not a game playtest."""
import json
import math
import types
import unittest
from unittest import mock

import test_port_0922_compiled_collision as compiled_tests
from test_port_0922_compiled_collision import sensor
from test_port_0922_world_collision import (
    ROOT, _Vector as V, _Strict1513Component as Component,
    _miss_mat_info_1513, world_collision)
from gui.mods.offline_lan_0922 import battle_runtime
from gui.mods.offline_lan_0922.ai.navigation import TerrainGrid, TerrainNavigator


CONTACTS = json.loads((ROOT / 'tests/fixtures/collision_144629_contacts.json').read_text())


class OverlappingBrokenSkins(unittest.TestCase):
    def test_reported_face_requires_its_component_owner_to_be_broken(self):
        setup = compiled_tests.CompiledCollisionTests()
        setup.setUp()
        self.addCleanup(setup.doCleanups)
        row = CONTACTS[3]
        start, end, hit = (V(*row[k]) for k in ('ray_start', 'ray_end', 'hit'))
        alias = next(tuple(key[:4]) for key in row['native_surface_candidates']
                     if key[0] == 74 and key[1] & 128)
        native = setup.native([(hit, alias)])
        def query():
            return sensor.collide_motion_segment(1, start, end, lambda *unused: True, native)
        self.assertIsNone(query())
        for owner in (25, 26):
            setup.broken.remove((32636, owner, 74))
            if owner == 26:
                self.assertIs(hit, query()[0])
            else:
                # Placement 25 overlaps through its other half (material 73).
                # Its intact material 74 does not own this reported face.
                self.assertIsNone(query())
            setup.broken.add((32636, owner, 74))
        for material in (88, 111):
            backing = hit + (end - start).scale(0.001)
            result = setup.query(start, end, [(hit, alias),
                (backing, (material, 0, 25, 32636))])
            self.assertIs(backing, result[0])


class RotationDepartureTests(unittest.TestCase):
    def test_live_rotation_sweep_releases_reported_contact_without_entering_wall(self):
        from test_port_0922_battle_runtime import _runtime
        row = CONTACTS[6]
        hw, back, front = row['extents']
        bbox = ((-hw, 0, -back), (hw, 2, front))
        runtime = _runtime()
        battle = battle_runtime.BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._destructibles = types.SimpleNamespace(_vehicle_body_bbox=lambda td: bbox)
        point, normal = V(*row['hit']), V(*row['normal'])
        def collide(space, start, end, flags, keep=None):
            if abs(start.x - end.x) < 1e-8 and abs(start.z - end.z) < 1e-8:
                return V(start.x, row['position'][1], start.z), V(0, 1, 0)
            delta = end - start
            denominator = delta.x * normal.x + delta.z * normal.z
            if abs(denominator) < 1e-9:
                return None
            t = ((point.x - start.x) * normal.x +
                 (point.z - start.z) * normal.z) / denominator
            if 0 <= t <= 1:
                return start + delta.scale(t), normal
        runtime.bigworld.wg_collideSegment = collide
        runtime.bigworld.wg_getMatInfoNearPoint = _miss_mat_info_1513
        for delta, expected in ((.02, True), (-.02, False)):
            with self.subTest(delta=delta):
                self.assertEqual(expected, battle._native_world_rotation_is_clear(
                    row['position'], row['yaw'], row['yaw'] + delta, {},
                    pitch=row['pitch'], roll=row['roll']))

    def test_malinovka_report_can_turn_out_but_not_deeper(self):
        for index in (1, 6):
            row = CONTACTS[index]
            hw, back, front = row['extents']
            bbox = ((-hw, 0.0, -back), (hw, 2.0, front))
            contact = (V(*row['hit']), V(*row['normal']))
            for delta, expected in ((0.02, True), (-0.02, False)):
                with self.subTest(index=index, delta=delta):
                    predicate = battle_runtime._rotation_departing_contact(row['position'], bbox,
                        row['yaw'], row['yaw'] + delta, row['pitch'], row['roll'])
                    self.assertEqual(expected, predicate(contact))

    def test_wall_outside_start_hull_does_not_become_an_escape(self):
        predicate = battle_runtime._rotation_departing_contact((0, 0, 0),
            ((-1.5, 0, -3), (1.5, 2, 3)), 0.0, 0.2)
        self.assertFalse(predicate((V(1.6, 1, 2), V(-1, 0, 0))))

    def test_enclosing_lane_cannot_skip_a_corner_whose_farther_volume_is_occupied(self):
        # This lane's first contact lies outside the curved body sweep, but
        # the same wall extends forward into the rotating front corner. A
        # point-only envelope exception would discard its only entry face.
        predicate = battle_runtime._rotation_departing_contact((0, 0, 0),
            ((-1.5, 0, -3), (1.5, 2, 3)), 0.0, 0.02)
        self.assertFalse(predicate((V(1.53, 1, 0), V(0, 0, -1))))

    def test_departing_first_face_does_not_hide_a_second_wall(self):
        first, second = V(0, 1, 1), V(0, 1, 2)
        calls = []
        def native(space, start, end, flags):
            calls.append(start.z)
            for point in (first, second):
                if start.z <= point.z <= end.z:
                    return point, V(0, 0, -1)
        with mock.patch.dict('sys.modules', {'BigWorld': types.SimpleNamespace(
                wg_collideSegment=native)}):
            result = world_collision._collide_horizontal(1, V(0, 1, 0), V(0, 1, 3),
                None, lambda hit: hit[0] is first)
        self.assertIs(second, result[0])
        self.assertEqual(2, len(calls))


class ParisSupportSeamTests(unittest.TestCase):
    @staticmethod
    def native(row, wall_height=None, extra_wall=False, narrow=False):
        # Reconstruct a low supported pavement at the reported contact plane.
        nx, nz = row['normal'][0], row['normal'][2]
        length = math.hypot(nx, nz)
        nx, nz = nx / length, nz / length
        px, unused_y, pz = row['hit']
        base = min(row['profile'])
        top = row['profile'][-1] if wall_height is None else base + wall_height
        def distance(point):
            return (point.x - px) * nx + (point.z - pz) * nz
        def collide(space, start, end, flags, keep=None):
            if keep is not None and not keep(111, 0, 72, 31872):
                return None
            if abs(start.x - end.x) < 1e-8 and abs(start.z - end.z) < 1e-8:
                d = distance(start)
                y = top if d <= 0 and (not narrow or d >= -0.3) else base
                return (V(start.x, y, start.z), V(0, 1, 0)) if end.y <= y <= start.y else None
            delta = end - start
            hits = []
            for plane, height in ((0, top), (-0.3, base + 4) if extra_wall else (0, top)):
                denominator = delta.x * nx + delta.z * nz
                if abs(denominator) > 1e-9:
                    t = (plane - distance(start)) / denominator
                    if 0 <= t <= 1 and base <= start.y + delta.y * t <= height:
                        hits.append((t, start + delta.scale(t), V(nx, 0, nz)))
            for y, upper in ((base, False), (top, True)):
                if abs(delta.y) > 1e-9:
                    t = (y - start.y) / delta.y
                    point = start + delta.scale(t)
                    if 0 <= t <= 1 and (distance(point) <= 0) == upper:
                        hits.append((t, point, V(0, 1, 0)))
            if hits:
                return min(hits, key=lambda hit: hit[0])[1:]
        return collide

    def query(self, row, **native_options):
        hw, back, front = row['extents']
        descriptor = Component(hull=Component(hitTester=types.SimpleNamespace(
            bbox=((-hw, 0, -back), (hw, 2, front)))))
        bigworld = types.SimpleNamespace(wg_collideSegment=self.native(row, **native_options),
            wg_getMatInfoNearPoint=_miss_mat_info_1513)
        return world_collision.check_horizontal_collision(bigworld,
            types.SimpleNamespace(Vector3=V), 1, V(*row['position']), row['yaw'],
            row['speed'], descriptor, False, row['dt'], True,
            pitch=row['pitch'], roll=row['roll'], exact_footprint=row['dt'] == 0)

    def test_reported_straddled_pavement_is_supported(self):
        for index in range(17, 22):
            with self.subTest(index=index):
                self.assertEqual('clear', self.query(CONTACTS[index]))

    def test_real_building_backing_wall_and_narrow_rail_remain_solid(self):
        for options in ({'wall_height': 3}, {'extra_wall': True}, {'narrow': True}):
            with self.subTest(options=options):
                self.assertEqual('hard', self.query(CONTACTS[18], **options))

    def test_unposed_hull_cannot_use_raised_track_exception(self):
        source = CONTACTS[19]
        row = dict(source, roll=0.0, pitch=0.0, yaw=-math.pi / 2,
            position=(source['hit'][0] + 2.0, min(source['profile']), source['hit'][2]))
        self.assertEqual('hard', self.query(row))


class NativeGatewayNavigationTests(unittest.TestCase):
    @staticmethod
    def graph():
        width = 25
        links = []
        for z in range(width):
            for x in range(width):
                links.append(sum(1 << i for i, (dx, dz, unused) in
                    enumerate(TerrainGrid._NEIGHBOURS)
                    if 0 <= x + dx < width and 0 <= z + dz < width))
        return dict(format='offline-lan-0922-navgraph', version=2,
            width=width, height=width, origin=[-48, -48], cell_size=4,
            heights_mm=[0] * width ** 2, links=links)

    def setUp(self):
        self.queries = 0
        self.wall = True
        def obstacle(start, end, half_width):
            self.queries += 1
            if not self.wall:
                return False
            dx, dz = end[0] - start[0], end[2] - start[2]
            length = math.hypot(dx, dz)
            if length < 0.01:
                return False
            for offset in (-half_width, 0, half_width):
                x, z = start[0] + dz / length * offset, start[2] - dx / length * offset
                if abs(dz) < 1e-9:
                    if abs(z) < .01 and not (10 <= min(x, x + dx) and max(x, x + dx) <= 22):
                        return True
                else:
                    t = -z / dz
                    if 0 <= t <= 1 and not 10 <= x + t * dx <= 22:
                        return True
            return False
        self.obstacle = obstacle
        self.nav = TerrainNavigator(lambda *unused: 0, obstacle,
            bounds=(-50, -50, 50, 50), baked_graph=self.graph())
        self.grid = self.nav.grid
        self.start, self.goal = (-12, 0, -12), (-12, 0, 12)

    def test_contact_replans_through_measured_door_for_both_teams(self):
        old = self.grid.plan(self.start, self.goal)
        self.assertTrue(self.obstacle(old[0], old[-1], 2.15))
        self.nav.bot_states[101] = {}
        self.nav.report_hard_contact(101, self.start, self.goal, 0.0, 0.0)
        self.assertTrue(self.grid.path_has_penalty(old, 0))
        self.assertFalse(self.grid.segment_clear(self.start, self.goal))
        for start, goal in ((self.start, self.goal), (self.goal, self.start)):
            path = self.grid.plan(start, goal)
            self.assertEqual(goal, path[-1])
            self.assertTrue(any(point[0] >= 12 for point in path))
            for a, b in zip(path, path[1:]):
                self.assertFalse(self.obstacle(a, b, 2.15), (a, b))
        count = self.queries
        self.grid.plan(self.start, self.goal)
        self.assertEqual(count, self.queries)

    def test_clear_traffic_contact_does_not_create_a_wall(self):
        self.wall = False
        self.grid.review_native_corridor(self.start, self.goal)
        self.assertTrue(self.grid.segment_clear(self.start, self.goal))
        self.assertFalse(self.grid.path_has_penalty((self.start, self.goal), 0))

    def test_delivered_destruction_reopens_a_previously_blocked_edge(self):
        self.grid.review_native_corridor(self.start, self.goal)
        self.assertFalse(self.grid.segment_clear(self.start, self.goal))
        self.wall = False
        self.grid.invalidate_native_review()
        self.assertTrue(self.grid.segment_clear(self.start, self.goal))


class PragueAuthoredDoorwayTests(unittest.TestCase):
    def test_shipped_routes_cross_both_actual_workshop_doorways(self):
        from gui.mods.offline_lan_0922.ai.reviewed_routes_20260811 import REVIEWED_ROUTE_POINTS
        graph = json.loads((ROOT / 'navgraphs/114_czech.json').read_text())
        catalog = json.loads((ROOT / 'destructibles/114_czech.json').read_text())
        grid = TerrainGrid(lambda *unused: None, baked_graph=graph)
        doors = []
        for row in catalog['instances']:
            if tuple(row[14:16]) not in ((32638, 48), (32640, 89)):
                continue
            boxes = sensor._baked_world_boxes_1513(catalog['resources'][row[12]],
                row[:12], row[13], catalog['locator_quantization'])
            doors.append(next(box for box in boxes if box[2] == 74))
        self.assertEqual(2, len(doors))
        routes = [REVIEWED_ROUTE_POINTS['114_czech']['valley']]
        for team in ('1', '2'):
            route = next(r for r in graph['routes'][team] if r['id'] == 'valley')
            self.assertLessEqual(len(route['waypoints']), 16)
            planned = []
            for a, b in zip(route['waypoints'], route['waypoints'][1:]):
                leg = grid.plan((a[0], 0, a[1]), (b[0], 0, b[1]), max_expansions=12000)
                self.assertTrue(leg)
                self.assertEqual((b[0], b[1]), (leg[-1][0], leg[-1][2]))
                planned.extend((p[0], p[2]) for p in leg)
            routes.append(planned)
        for route in routes:
            for centre, axes, unused_material in doors:
                normal_length = math.hypot(axes[0][0], axes[0][2])
                nx, nz = axes[0][0] / normal_length, axes[0][2] / normal_length
                half_width = math.hypot(axes[2][0], axes[2][2])
                wx, wz = axes[2][0] / half_width, axes[2][2] / half_width
                crossed = []
                for a, b in zip(route, route[1:]):
                    da = (a[0] - centre[0]) * nx + (a[1] - centre[2]) * nz
                    db = (b[0] - centre[0]) * nx + (b[1] - centre[2]) * nz
                    if da * db < 0:
                        t = da / (da - db)
                        x, z = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
                        crossed.append(abs((x - centre[0]) * wx + (z - centre[2]) * wz))
                self.assertTrue(crossed)
                self.assertLess(max(crossed) + 2.15, half_width)


if __name__ == '__main__':
    unittest.main()
