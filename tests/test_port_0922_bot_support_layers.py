"""Bot support must stay on its road beneath an unrelated upper deck."""
import contextlib
import io
import math
import sys
import unittest
from unittest import mock

import test_port_0922_battle_runtime as fixture


class BotSupportLayerTests(unittest.TestCase):
    # Captured Highway T71 heights. The collision planes below are an
    # analytic reproduction of the layer gap, not the native map mesh.
    FLOOR = -11.103339195251465
    DECK = -5.151131629943848

    def setUp(self):
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)
        mounted = mock.patch.dict(sys.modules, {
            'CurrentVehicle': fixture._mounted_current_vehicle_module()})
        mounted.start()
        self.addCleanup(mounted.stop)
        self.runtime = fixture._runtime()
        self.battle = fixture.BattleRuntime(self.runtime)
        self.addCleanup(self.battle.stop, show_login=False)
        self.assertTrue(self.battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, fixture._minimal_start(), fixture._Client()))
        for unused in range(8):
            if self.battle.state == 'running':
                break
            self.assertTrue(self.runtime.bigworld.callbacks)
            self.runtime.bigworld.callbacks.pop(0)()
        self.assertEqual('running', self.battle.state, self.battle.error)
        self.bots = self.battle._bots

    def collision_planes(self, floor, deck=None):
        def collide(space, start, end, skip_flags, ground_filter=None):
            if start.y <= end.y:
                return None
            surfaces = [(floor(start.x, start.z), 1.0, 8)]
            if deck is not None:
                surfaces.extend(((deck, 1.0, 0x4980),
                                 (deck - 0.2, -1.0, 0x4980)))
            for height, normal, flags in sorted(surfaces, reverse=True):
                if flags & skip_flags or not end.y <= height <= start.y:
                    continue
                if ground_filter is not None and not ground_filter(
                        flags >> 8, flags & 255, 0, 0):
                    continue
                return (fixture._Vector(start.x, height, start.z),
                        fixture._Vector(0, normal, 0))
            return None
        self.runtime.bigworld.wg_collideSegment = collide

    def state(self, y=None):
        return dict(id=14, x=0.0, y=self.FLOOR if y is None else y,
                    z=0.04, yaw=0.0, speed=0.4, half_length=2.4026,
                    half_width=1.47996, vertical_speed=0.0, airborne=False,
                    grounded_once=True, last_drive_pitch=0.0,
                    terrain_pitch=0.0, pitch=0.0, roll=0.0)

    def test_live_bot_support_ignores_bridge_above_its_drivable_road(self):
        self.collision_planes(lambda x, z: self.FLOOR, self.DECK)
        state = self.state()
        # Exercise the callback supplied by actual BattleRuntime startup.
        # The old broad ground query saw the upper deck within its +6 m band
        # and rolled this clear 4 cm move back on every subsequent tick.
        for unused in range(10):
            old_pose = (state['x'], state['y'], state['z'] - 0.04)
            self.assertFalse(self.bots._integrate_vertical_motion(
                state, 0.1, tick_pose=old_pose))
            self.assertEqual(self.FLOOR, state['y'])
            self.assertFalse(state['airborne'])
            self.assertGreater(state['z'], old_pose[2])
            state['z'] += 0.04

    def test_attitude_uses_lower_road_and_bridge_top_still_supports(self):
        gradient = 0.15
        self.collision_planes(lambda x, z: self.FLOOR + z * gradient,
                              self.DECK)
        under = self.state()
        self.bots._integrate_vertical_motion(under, 0.1)
        self.assertTrue(self.bots._update_slope_pose(under))
        # The pose smoother approaches the sampled road grade; a flat bridge
        # above it must not replace that grade with zero.
        self.assertLess(under['terrain_pitch'], 0.0)
        self.assertGreaterEqual(under['terrain_pitch'], -math.atan(gradient))
        for grounded in (False, True):
            top = self.state(self.DECK)
            top['grounded_once'] = grounded
            self.assertFalse(self.bots._integrate_vertical_motion(top, 0.1))
            self.assertEqual(self.DECK, top['y'])
            self.assertFalse(top['airborne'])

    def test_real_raised_body_keeps_the_vertical_contact_rejection(self):
        self.collision_planes(lambda x, z: self.FLOOR + 1.2)
        state = self.state()
        old_pose = (0.0, self.FLOOR, 0.0)
        self.assertTrue(self.bots._integrate_vertical_motion(
            state, 0.1, tick_pose=old_pose))
        self.assertEqual(old_pose, (state['x'], state['y'], state['z']))
        self.assertEqual(0.0, state['speed'])

    def test_spawn_finds_lower_ground_and_driving_off_an_edge_still_falls(self):
        self.collision_planes(lambda x, z: self.FLOOR)
        spawn = self.state(self.FLOOR + 0.5)
        spawn['grounded_once'] = False
        self.assertFalse(self.bots._integrate_vertical_motion(spawn, 0.1))
        self.assertEqual(self.FLOOR, spawn['y'])
        self.assertTrue(spawn['grounded_once'])
        self.collision_planes(lambda x, z: self.FLOOR - 20.0)
        state = self.state()
        self.assertFalse(self.bots._integrate_vertical_motion(state, 0.1))
        self.assertTrue(state['airborne'])
        self.assertLess(state['y'], self.FLOOR)
        self.assertGreater(state['y'], self.FLOOR - 1.0)


if __name__ == '__main__':
    unittest.main()
