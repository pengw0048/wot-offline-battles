"""Ground-exit regressions from the 20260919-002743 Strv S1 report.

The native fake intersects a piecewise planar reconstruction of the recorded
lane, not the actual Cliff mesh. It proves the Python collision decision only.
"""
import math
import types
import unittest
from unittest import mock

from test_port_0922_world_collision import (
    _Vector, _Strict1513Component, _miss_mat_info_1513)
from gui.mods.offline_lan_0922 import destructibles_sensor, world_collision


CAPTURED = (
    dict(position=(-185.82666015625, -0.7630808353424072, -124.02088928222656),
         yaw=-2.8886927629051202, speed=7.108783986624549,
         dt=0.0169677734375, airborne=True,
         hit=(-184.34048461914062, -0.0627535805106163, -124.86456298828125),
         normal=(-0.6328072547912598, 0.6963102221488953, -0.3386842608451843),
         profile=(0.24798274040222168, -0.17609107494354248,
                  -0.5464429259300232, -0.9168174862861633,
                  -1.3846755027770996, -2.0008115768432617,
                  -3.0918383598327637)),
    dict(position=(-185.97853088378906, -1.128002405166626, -124.40919494628906),
         yaw=-2.853533458975382, speed=0.15888121914929512,
         dt=0.0169677734375, airborne=False,
         hit=(-184.5150604248047, -0.435587614774704, -125.27803802490234),
         normal=(-0.8218789100646973, 0.5535278916358948, -0.13461753726005554),
         profile=(-0.11922347545623779, -0.5706015825271606,
                  -0.9789178967475891, -1.4324491024017334,
                  -2.0894393920898438, -3.2404253482818604,
                  -4.196531295776367)),
)
WIDTH, BACK, FRONT = 1.6500049829483032, 2.967868004925549, 3.340472067706287
PITCH, ROLL = -0.020469163768142208, -0.06306503695001689


class ReportedTerrain(object):
    def __init__(self, captured, wall=None, wall_band=None):
        self.captured = captured
        self.wall = wall
        self.wall_band = wall_band
        self.wall_hits = 0
        self.exit_recasts = 0
        self.native_filters = []
        self.sine, self.cosine = math.sin(captured['yaw']), math.cos(captured['yaw'])
        self.origin = _Vector(*captured['position'])
        self.side_grade = (-captured['normal'][0] * self.cosine +
                           captured['normal'][2] * self.sine) / captured['normal'][1]
        ahead = abs(captured['speed']) * captured['dt'] + 0.2
        if not captured['airborne']:
            ahead = max(0.4, ahead)
        segment = (FRONT + ahead) / 6.0
        self.knots = [(index * segment, height)
                      for index, height in enumerate(captured['profile'])]
        hit_u, unused_v = self.coordinates(_Vector(*captured['hit']))
        self.hit_u = hit_u
        hit_grade = -(captured['normal'][0] * self.sine +
                      captured['normal'][2] * self.cosine) / captured['normal'][1]
        # Preserve the actual hit and its tangent as well as all seven samples.
        for offset in (-0.02, 0.0, 0.02):
            self.knots.append((hit_u + offset, captured['hit'][1] + hit_grade * offset))
        self.knots.sort()
        self.facets = []
        for index, (first, second) in enumerate(zip(self.knots, self.knots[1:])):
            grade = (second[1] - first[1]) / (second[0] - first[0])
            self.facets.append((
                -1000.0 if index == 0 else first[0],
                1000.0 if index == len(self.knots) - 2 else second[0],
                grade, first[1] - grade * first[0]))

    def coordinates(self, point):
        dx, dz = point.x - self.origin.x, point.z - self.origin.z
        return (dx * self.sine + dz * self.cosine,
                dx * self.cosine - dz * self.sine + WIDTH)

    def collide(self, space, start, end, mask, collision_filter=None):
        self.native_filters.append((mask, collision_filter))
        u0, v0 = self.coordinates(start)
        u1, v1 = self.coordinates(end)
        du, dv, dy = u1 - u0, v1 - v0, end.y - start.y
        candidates = []
        vertical = abs(du) + abs(dv) < 1.0e-8
        if not vertical and abs(u0 - self.hit_u) < 0.01:
            self.exit_recasts += 1
        for lower, upper, grade, base in self.facets:
            denominator = dy - grade * du - self.side_grade * dv
            if abs(denominator) <= 1.0e-10:
                continue
            fraction = (grade * u0 + base + self.side_grade * v0 - start.y) / denominator
            u = u0 + fraction * du
            if 0.0 <= fraction <= 1.0 and lower <= u <= upper:
                normal = _Vector(
                    -(grade * self.sine + self.side_grade * self.cosine),
                    1.0, -(grade * self.cosine - self.side_grade * self.sine))
                normal.normalise()
                candidates.append((fraction, normal, False))
        if self.wall is not None and abs(du) > 1.0e-10:
            fraction = (self.wall - u0) / du
            height = start.y + fraction * dy
            if (0.0 <= fraction <= 1.0 and
                    (self.wall_band is None or self.wall_band[0] <= height <= self.wall_band[1])):
                candidates.append((fraction, _Vector(-self.sine, 0, -self.cosine), True))
        if not candidates:
            return None
        fraction, normal, wall = min(candidates, key=lambda item: item[0])
        if wall:
            self.wall_hits += 1
        point = start + (end - start).scale(fraction)
        return point, normal, 0


class DownhillDepartureTests(unittest.TestCase):
    def tearDown(self):
        destructibles_sensor.set_catalog(None)

    def check_scene(self, captured, wall=None, wall_band=None, reverse=False,
                    replay_captured_lane=False):
        terrain = ReportedTerrain(captured, wall, wall_band)
        descriptor = _Strict1513Component(hull=_Strict1513Component(
            hitTester=types.SimpleNamespace(bbox=(
                (-WIDTH, -1.0, -BACK), (WIDTH, 1.0, FRONT)))))
        yaw, speed, pitch, roll = captured['yaw'], captured['speed'], PITCH, ROLL
        dt = captured['dt']
        if replay_captured_lane:
            # The historical ray included an artificial lead. Reproduce that
            # same ray with a longer *actual* integration step so these ground
            # exit/backing-wall controls still exercise their original contact.
            # The ordinary recorded-pose test below keeps the real short dt.
            reach = abs(speed) * dt + 0.2
            if not captured['airborne']:
                reach = max(0.4, reach)
            dt = reach / abs(speed)
        if reverse:
            # Rotate the same occupied hull 180 degrees and back down the lane.
            yaw += math.pi
            speed = -speed
            pitch, roll = -pitch, -roll
            descriptor.hull.hitTester.bbox = (
                (-WIDTH, -1.0, -FRONT), (WIDTH, 1.0, BACK))
        native = types.SimpleNamespace(wg_collideSegment=terrain.collide,
                                       wg_getMatInfoNearPoint=_miss_mat_info_1513)
        trace = {}
        collision_filter = lambda *unused: True
        with mock.patch.object(world_collision, '_destroy_and_recast',
                               return_value=False) as destroy, \
                mock.patch.object(world_collision, 'prepare_horizontal_collision_filter',
                                  return_value=collision_filter):
            status = world_collision.check_horizontal_collision(
                native, types.SimpleNamespace(Vector3=_Vector), 1,
                _Vector(*captured['position']), yaw, speed, descriptor,
                captured['airborne'], dt, True,
                pitch=pitch, roll=roll, trace=trace, commit_enabled=False)
        destroy.assert_not_called()
        # All extra native proofs retain the same accepted-destruction filter
        # and vehicle collision mask as the original sweep.
        self.assertTrue(terrain.native_filters)
        self.assertTrue(all(mask == world_collision.VEHICLE_SKIP_FLAGS
                            for mask, unused_filter in terrain.native_filters))
        filters = [callback for unused_mask, callback in terrain.native_filters]
        self.assertIsNotNone(filters[0])
        self.assertTrue(all(callback is filters[0] for callback in filters))
        return status, trace, terrain

    def test_recorded_cliff_departures_leave_the_ground_in_both_directions(self):
        for captured in CAPTURED:
            for reverse in (False, True):
                with self.subTest(airborne=captured['airborne'], reverse=reverse):
                    status, trace, terrain = self.check_scene(captured, reverse=reverse)
                    self.assertEqual('clear', status, trace)
                    # Removing the old lead changes the posed chord enough
                    # that this short frame no longer grazes the ground.
                    self.assertEqual(0, terrain.exit_recasts)
                    status, trace, terrain = self.check_scene(
                        captured, reverse=reverse, replay_captured_lane=True)
                    self.assertEqual('clear', status, trace)
                    self.assertGreater(terrain.exit_recasts, 0)

    def test_native_wall_behind_departure_contact_still_blocks(self):
        for captured in CAPTURED:
            for reverse in (False, True):
                with self.subTest(airborne=captured['airborne'], reverse=reverse):
                    status, trace, terrain = self.check_scene(
                        captured, wall=1.3, reverse=reverse, replay_captured_lane=True)
                    self.assertEqual('hard', status, trace)
                    self.assertGreater(terrain.wall_hits, 0)

    def test_wall_only_in_upper_hull_lane_still_blocks(self):
        for captured in CAPTURED:
            # The lower lane leaves the ground; a thin beam intersects only
            # the middle hull ray. The low ray's clear recast cannot hide it.
            base = captured['hit'][1]
            status, trace, terrain = self.check_scene(
                captured, wall=1.3, wall_band=(base + 0.45, base + 0.55),
                replay_captured_lane=True)
            self.assertEqual('hard', status, trace)
            self.assertEqual('raised_wall', trace['reason'])
            self.assertGreater(terrain.wall_hits, 0)

    def test_low_wall_behind_ground_is_seen_by_same_lane_recast(self):
        for captured in CAPTURED:
            base = captured['hit'][1]
            status, trace, terrain = self.check_scene(
                captured, wall=1.3, wall_band=(base - 0.02, base + 0.02),
                replay_captured_lane=True)
            self.assertEqual('hard', status, trace)
            self.assertGreater(terrain.wall_hits, 0)

    def test_mixed_rise_and_drop_is_not_a_departure(self):
        for captured in CAPTURED:
            changed = dict(captured)
            heights = list(captured['profile'])
            heights[-2] = heights[-3] + 0.03
            changed['profile'] = heights
            status, trace, unused_terrain = self.check_scene(
                changed, replay_captured_lane=True)
            self.assertEqual('hard', status, trace)
            self.assertEqual('ground_profile', trace['reason'])

    def test_unconfirmed_native_top_remains_solid(self):
        # The sampled lane alone cannot prove the actual hit is ground.
        # For example, a slanted object's side can lie below its own top.
        for captured in CAPTURED:
            with mock.patch.object(world_collision, '_hit_matches_exact_ground_top',
                                   return_value=False):
                status, trace, unused_terrain = self.check_scene(
                    captured, replay_captured_lane=True)
            self.assertEqual('hard', status, trace)

    def test_player_and_worker_adapters_share_the_departure_decision(self):
        from test_port_0922_battle_runtime import BattleRuntime, _runtime

        for mode in (0, 2):
            for captured in CAPTURED:
                with self.subTest(siege_state=mode, airborne=captured['airborne']):
                    runtime = _runtime()
                    runtime.math.Vector3 = _Vector
                    terrain = ReportedTerrain(captured)
                    runtime.bigworld.wg_collideSegment = terrain.collide
                    runtime.bigworld.wg_getMatInfoNearPoint = _miss_mat_info_1513
                    battle = BattleRuntime(runtime)
                    battle._avatar = runtime.bigworld.avatar
                    battle._local_pitch, battle._local_roll = PITCH, ROLL
                    battle._local_airborne = captured['airborne']
                    descriptor = _Strict1513Component(
                        hasSiegeMode=True, isPitchHullAimingAvailable=True,
                        hull=_Strict1513Component(hitTester=types.SimpleNamespace(bbox=(
                            (-WIDTH, -1.0, -BACK), (WIDTH, 1.0, FRONT)))))
                    entity = types.SimpleNamespace(typeDescriptor=descriptor, siegeState=mode)
                    self.assertFalse(battle._local_siege_drive_locked(entity))
                    # Hydraulic vehicles keep their own suspension owner.
                    self.assertIsNone(battle._ensure_local_suspension_params(descriptor))
                    self.assertTrue(battle._motion_is_clear(
                        entity, captured['position'], captured['yaw'],
                        captured['speed'], captured['dt']))
                    battle._bots = types.SimpleNamespace(states={11: {
                        'pitch': PITCH, 'terrain_pitch': PITCH, 'roll': ROLL,
                        'airborne': captured['airborne'], 'siege_state': mode,
                    }})
                    self.assertEqual('clear', battle._resolve_bot_motion(
                        11, captured['position'], captured['yaw'],
                        captured['speed'], descriptor, captured['dt'], 1.0))


if __name__ == '__main__':
    unittest.main()
