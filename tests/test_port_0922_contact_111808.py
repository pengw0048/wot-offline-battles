"""Regressions from the 20260920-111808 native #1513 report."""
import json
import math
import types
import unittest
from unittest import mock

import test_port_0922_battle_runtime as runtime_tests
import test_port_0922_sloped_fence as sloped
from test_port_0922_destructibles import ROOT, _Vector as V
from test_port_0922_world_collision import _miss_mat_info_1513
from gui.mods.offline_lan_0922 import bot_runtime, vehicle_physics, world_collision


class RemainingFenceTests(unittest.TestCase):
    setUp = sloped.SlopedFenceTests.setUp
    install = sloped.SlopedFenceTests.install
    witnesses = sloped.SlopedFenceTests.witnesses
    query = staticmethod(sloped.SlopedFenceTests.query)

    @staticmethod
    def latest_contacts():
        return json.loads((ROOT /
            'tests/fixtures/malinovka_111808_contacts.json').read_text())

    def test_all_fourteen_reported_faces_clear_after_destruction(self):
        contacts = self.latest_contacts()
        self.assertEqual(14, len(contacts))
        for contact in contacts:
            for pruned in (False, True):
                with self.subTest(time=contact['log_time'], pruned=pruned):
                    a, b, surfaces = self.witnesses(contact)
                    result, calls = self.query(a, b, surfaces, pruned)
                    self.assertIsNone(result)
                    self.assertLessEqual(calls, 6)
                    self.broken.clear()
                    result, unused = self.query(a, b, surfaces, pruned)
                    self.assertIsNotNone(result)

    def test_backing_walls_and_replacements_remain_solid(self):
        for contact in self.latest_contacts():
            for material in (88, 111):
                with self.subTest(time=contact['log_time'], material=material):
                    a, b, surfaces = self.witnesses(contact)
                    direction = b - a
                    direction.normalise()
                    first = min(surfaces, key=lambda row: (row[0] - a).length)
                    wall = first[0] + direction.scale(.001)
                    surfaces.append((wall, first[1], (material, 0, 900, 31870)))
                    for pruned in (False, True):
                        result, unused = self.query(a, b, surfaces, pruned)
                        self.assertIs(wall, result[0])

    def test_a_sloping_top_is_not_an_original_side(self):
        for contact in self.latest_contacts():
            a, b, surfaces = self.witnesses(contact)
            for normal in (V(0, 1, 0), V(.7, .1, -.7)):
                with self.subTest(time=contact['log_time'], normal=normal):
                    tilted = [(p, normal, key) for p, unused, key in surfaces]
                    result, unused = self.query(a, b, tilted)
                    self.assertIsNotNone(result)


def paris_contacts():
    return json.loads((ROOT /
        'tests/fixtures/paris_111808_contacts.json').read_text())


class ConcreteScene:
    """Analytic reconstruction, not an assertion about unseen native triangles.

    The deck and bevel planes use the captured points/normals. Three captured
    road samples define the lower road; unrecorded boundaries are controlled
    test geometry. Each native query returns the nearest actual intersection.
    """
    def __init__(self, wall=False, wall_z=201.0):
        rows = paris_contacts()
        self.top = (V(rows[6]['hit']), V(rows[6]['normal']))
        self.bevel = (V(rows[5]['hit']), V(rows[5]['normal']))
        samples = rows[6]['spring_probes']
        a, b, c = [V(samples[i][0], samples[i][4], samples[i][1])
                   for i in (0, 1, 5)]
        u, v = b - a, c - a
        normal = V(u.y*v.z-u.z*v.y, u.z*v.x-u.x*v.z, u.x*v.y-u.y*v.x)
        if normal.y < 0:
            normal = normal.scale(-1)
        normal.normalise()
        self.road = (a, normal)
        self.wall = wall
        self.wall_z = wall_z
        self.calls = 0

    @staticmethod
    def height(plane, x, z):
        point, normal = plane
        return point.y - (normal.x*(x-point.x)+normal.z*(z-point.z))/normal.y

    def native(self, space, start, end, flags, keep=None):
        self.calls += 1
        if keep is not None and not keep(111, 0, 72, 32385):
            return None
        delta = end - start
        hits = []
        for name, (anchor, normal) in (('road', self.road), ('top', self.top),
                                        ('bevel', self.bevel)):
            denominator = delta.x*normal.x + delta.y*normal.y + delta.z*normal.z
            if abs(denominator) <= 1e-10:
                continue
            offset = anchor - start
            fraction = (offset.x*normal.x+offset.y*normal.y+offset.z*normal.z)/denominator
            if not 0 <= fraction <= 1:
                continue
            point = start + delta.scale(fraction)
            if name == 'top' and self.height(self.bevel, point.x, point.z) < point.y-1e-7:
                continue
            if name == 'bevel' and (point.y > self.height(self.top, point.x, point.z)+1e-7 or
                                  point.y < self.height(self.road, point.x, point.z)-1e-7):
                continue
            hits.append((fraction, point, normal))
        if self.wall and abs(delta.z) > 1e-10:
            fraction = (self.wall_z-start.z)/delta.z
            point = start + delta.scale(fraction)
            if 0 <= fraction <= 1 and 1.0 <= point.y <= 5.0:
                hits.append((fraction, point, V(0, 0, -1)))
        return min(hits, key=lambda item: item[0])[1:] if hits else None


class ConcreteSupportTests(unittest.TestCase):
    @staticmethod
    def battle(row, scene):
        runtime = runtime_tests._runtime()
        battle = runtime_tests.BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._local_fall_armed = True
        battle._local_pitch, battle._local_roll = row['pitch'], row['roll']
        descriptor = runtime_tests._suspension_descriptor()
        hw, back, front = row['extents']
        descriptor.chassis.hitTester.bbox = (
            runtime_tests._Vector(-hw, 0, -back),
            runtime_tests._Vector(hw, 1.65, front), None)
        descriptor.hull.hitTester.bbox = (
            runtime_tests._Vector(-hw, -.2, -back),
            runtime_tests._Vector(hw, 1.05, front), None)
        descriptor.chassis.hullPosition = runtime_tests._Vector(0, .6, 0)
        descriptor.physics['trackCenterOffset'] = 1.32
        chassis = descriptor.type.xphysics['detailed']['chassis']['test_chassis']
        chassis['roadWheelPositions'] = (-1.9, -.95, 0.0, .95, 1.9)
        entity = runtime_tests._Vehicle(10, descriptor, runtime_tests._Vector(),
                                        (0, 0, 0), {'health': 500})
        runtime.bigworld.wg_collideSegment = scene.native
        runtime.bigworld.wg_getMatInfoNearPoint = _miss_mat_info_1513
        battle._ensure_local_suspension_params(descriptor)
        return battle, entity

    def test_tilted_tracks_recover_the_concrete_support(self):
        for index in (3, 6, 7, 17):
            with self.subTest(index=index):
                row, scene = paris_contacts()[index], ConcreteScene()
                battle, entity = self.battle(row, scene)
                position = tuple(row['position'])
                for unused in range(50):
                    position = battle._update_suspension_vertical_motion(
                        entity, position, row['yaw'], .02)
                self.assertGreater(position[1], row['position'][1])
                self.assertFalse(battle._local_airborne)
                samples = battle._local_suspension_ground_samples(position, row['yaw'])
                self.assertTrue(any(value > 2.59 for value in samples if value is not None))

    def test_concrete_top_is_passable_after_support_settles(self):
        for index in (3, 4, 5, 6, 7, 10, 14, 15, 16, 17):
            for wall in (False, True):
                with self.subTest(index=index, wall=wall):
                    row = paris_contacts()[index]
                    scene = ConcreteScene(wall=wall,
                        wall_z=row['position'][2] + 2*math.cos(row['yaw']))
                    battle, entity = self.battle(row, scene)
                    position = tuple(row['position'])
                    for unused in range(50):
                        position = battle._update_suspension_vertical_motion(
                            entity, position, row['yaw'], .02)
                    trace = {}
                    result = world_collision.check_horizontal_collision(
                        battle._runtime.bigworld, types.SimpleNamespace(Vector3=runtime_tests._Vector),
                        1, runtime_tests._Vector(position), row['yaw'], 1.0,
                        entity.typeDescriptor, False, .02, True,
                        pitch=battle._local_pitch, roll=battle._local_roll, trace=trace)
                    self.assertEqual('hard' if wall else 'clear', result, trace)

    def test_drive_and_reverse_cross_the_deck_without_stalling(self):
        row = paris_contacts()[6]
        for direction in (1., -1.):
            with self.subTest(direction=direction):
                scene = ConcreteScene()
                battle, entity = self.battle(row, scene)
                yaw = row['yaw'] + (math.pi if direction < 0 else 0)
                if direction < 0:
                    battle._local_pitch *= -1
                    battle._local_roll *= -1
                position = (2., scene.height(scene.road, 2., 194.), 194.)
                for unused in range(150):
                    status = world_collision.check_horizontal_collision(
                        battle._runtime.bigworld,
                        types.SimpleNamespace(Vector3=runtime_tests._Vector),
                        1, runtime_tests._Vector(position), yaw, direction*2.,
                        entity.typeDescriptor, False, .04, True,
                        pitch=battle._local_pitch, roll=battle._local_roll)
                    self.assertEqual('clear', status)
                    position = (position[0] + math.sin(yaw)*direction*.08,
                                position[1], position[2] + math.cos(yaw)*direction*.08)
                    position = battle._update_suspension_vertical_motion(
                        entity, position, yaw, .04)
                self.assertGreater(position[2], 205.)
                self.assertGreaterEqual(position[1],
                    scene.height(scene.top, position[0], position[2]) - .01)

    def test_player_and_bot_select_the_same_support_without_more_columns(self):
        row, scene = paris_contacts()[6], ConcreteScene()
        battle, unused = self.battle(row, scene)
        params = battle._local_suspension_params
        bot = object.__new__(bot_runtime.BotRuntime)
        bot._suspension_ground_value = battle._suspension_ground_y
        state = dict(zip(('x', 'y', 'z'), row['position']))
        state.update(yaw=row['yaw'], pitch=row['pitch'], roll=row['roll'])
        for airborne in (False, True):
            with self.subTest(airborne=airborne):
                state['airborne'] = battle._local_airborne = airborne
                state['_spring_ground_memory'] = None
                battle._local_spring_ground_memory = None
                scene.calls = 0
                local = battle._local_suspension_ground_samples(row['position'], row['yaw'])
                local_calls = scene.calls
                scene.calls = 0
                remote = bot._suspension_ground_samples(state, params)
                self.assertEqual(local, remote)
                self.assertEqual(local_calls, scene.calls)
                self.assertLessEqual(local_calls, 20)
                if not airborne:
                    self.assertTrue(any(value > 2.59 for value in local if value is not None))
                else:
                    self.assertTrue(all(value < 2.59 for value in local if value is not None))

    def test_flat_roof_above_every_carrier_is_not_acquired(self):
        row = dict(paris_contacts()[6], position=(0., 0., 0.))
        for pitch, roll in ((0., 0.), (.12, -.08)):
            with self.subTest(pitch=pitch, roll=roll):
                row.update(pitch=pitch, roll=roll)
                battle, unused = self.battle(row, ConcreteScene())
                limit = vehicle_physics.suspension_flat_support_limit(
                    battle._local_suspension_params, 0., pitch, roll)
                roof = limit + .1
                def native(space, start, end, flags, keep=None):
                    for height in (roof, 0.):
                        if end.y <= height <= start.y:
                            return V(start.x, height, start.z), V(0, 1, 0)
                    return None
                battle._runtime.bigworld.wg_collideSegment = native
                samples = battle._local_suspension_ground_samples((0., 0., 0.), row['yaw'])
                self.assertEqual((0.,)*10, samples)

    def test_captured_gentle_top_does_not_hide_a_following_wall(self):
        row = paris_contacts()[0]
        point, normal = V(row['hit']), V(row['normal'])
        original_start, original_end = V(row['ray_start']), V(row['ray_end'])
        direction = original_end - original_start
        direction.y = 0
        direction.normalise()
        wall_point = point + direction.scale(.05)
        for wall in (False, True):
            with self.subTest(wall=wall):
                scene = ConcreteScene()
                battle, entity = self.battle(row, scene)
                def native(space, start, end, flags, keep=None):
                    delta = end - start
                    hits = []
                    planes = [(point, normal)]
                    if wall:
                        planes.append((wall_point, direction.scale(-1)))
                    for anchor, face in planes:
                        denominator = delta.x*face.x + delta.y*face.y + delta.z*face.z
                        if abs(denominator) <= 1e-10:
                            continue
                        offset = anchor - start
                        fraction = (offset.x*face.x + offset.y*face.y + offset.z*face.z)/denominator
                        if 0 <= fraction <= 1:
                            hits.append((fraction, start + delta.scale(fraction), face))
                    return min(hits, key=lambda value: value[0])[1:] if hits else None
                battle._runtime.bigworld.wg_collideSegment = native
                result = world_collision.check_horizontal_collision(
                    battle._runtime.bigworld, types.SimpleNamespace(Vector3=runtime_tests._Vector),
                    1, runtime_tests._Vector(row['position']), row['yaw'], row['speed'],
                    entity.typeDescriptor, False, row['dt'], True,
                    pitch=row['pitch'], roll=row['roll'])
                self.assertEqual('hard' if wall else 'clear', result)
