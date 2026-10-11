"""Regression scenes for the v0.8.0 cliff and hull-impact gameplay report."""
import math
import types
import unittest
from unittest import mock

import test_port_0922_battle_runtime as fixtures
from gui.mods.offline_lan_0922 import bot_runtime, vehicle_physics
from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime


class CliffAndImpactTests(unittest.TestCase):
    def battle(self):
        runtime = fixtures._runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._local_fall_armed = True
        entity = fixtures._Vehicle(
            10, fixtures._suspension_descriptor(), fixtures._Vector(),
            (0, 0, 0), {'health': 500})
        return battle, entity

    def test_downhill_contact_correction_has_no_time_to_pull_or_accelerate(self):
        battle, entity = self.battle()
        battle._local_ground_plane = {
            'center_x': 0.0, 'center_z': 0.0, 'center_y': 10.0,
            'gradient_x': -2.0, 'gradient_z': 0.0,
        }
        battle._local_suspension_support_gradient = (-2.0, 0.0)
        battle._local_vertical_speed = -1.0
        # The new endpoint is over a void, not the extrapolated old slope.
        battle._suspension_ground_y = mock.Mock(return_value=None)
        result = battle._resettle_local_suspension_endpoint(
            entity, (0.0, 10.0, 0.0), (1.0, 10.0, 0.0), 0.0, 0.05)
        self.assertEqual((1.0, 10.0, 0.0), result)
        self.assertEqual(-1.0, battle._local_vertical_speed)
        self.assertTrue(battle._local_airborne)
        self.assertFalse(battle._local_support_rise_blocked)

    def test_fast_fall_is_not_rolled_back_as_a_five_metre_pose_error(self):
        battle, entity = self.battle()
        battle._local_airborne = True
        battle._local_vertical_speed = -80.0
        battle._local_pitch = 0.8
        battle._local_roll = -0.4
        battle._local_support_tick_pose = (0.0, 100.0, 0.0)
        battle._suspension_ground_y = mock.Mock(return_value=None)
        result = battle._update_vertical_motion(
            entity, (0.0, 100.0, 0.0), 0.0, 0.1)
        self.assertLess(result[1], 92.0)
        self.assertAlmostEqual(-80.0 - vehicle_physics.GRAVITY * 0.1,
                               battle._local_vertical_speed)
        self.assertAlmostEqual(0.8, battle._local_pitch)
        self.assertAlmostEqual(-0.4, battle._local_roll)
        self.assertTrue(battle._local_airborne)
        self.assertFalse(battle._local_support_rise_blocked)

    def test_shallow_ledge_does_not_pull_legacy_player_to_lower_floor(self):
        battle, entity = self.battle()
        battle._ensure_local_suspension_params = lambda unused: None
        battle._terrain_support = lambda *args, **kw: (-0.5, -0.5)
        result = battle._update_vertical_motion(
            entity, (0.0, 0.0, 0.0), 0.0, 0.02)
        self.assertTrue(battle._local_airborne)
        self.assertGreater(result[1], -0.01)
        self.assertAlmostEqual(-vehicle_physics.GRAVITY * 0.02,
                               battle._local_vertical_speed)

    def test_shallow_ledge_uses_the_same_world_gravity_for_bot(self):
        runtime = bot_runtime.BotRuntime(1, physics_ground_probe=lambda *args: -0.5)
        state = dict(id=11, x=0.0, y=0.0, z=0.0, yaw=0.0, speed=0.0,
                     grounded_once=True, airborne=False, vertical_speed=0.0)
        runtime._update_vertical_motion(state, 0.02)
        self.assertTrue(state['airborne'])
        self.assertGreater(state['y'], -0.01)
        self.assertAlmostEqual(-vehicle_physics.GRAVITY * 0.02,
                               state['vertical_speed'])

    def test_wall_impact_queues_canonical_hp_once_for_all_hull_lanes(self):
        for reason in ('solid_lane', 'upper_lane', 'raised_wall'):
            with self.subTest(reason=reason):
                battle, entity = self.battle()
                battle._local_airborne = True
                battle._clock = lambda: 10.0
                trace = dict(hit=(0, 1, 3), normal=(0, 0, -1), reason=reason)
                battle._apply_world_contact_impact(entity, trace, 20.0, 0.0)
                battle._apply_world_contact_impact(entity, trace, 20.0, 0.0)
                self.assertEqual([20.0], battle._pending_landing_impacts)
                self.assertEqual(500, entity.health)

    def test_scenery_probe_and_tangential_scrape_cannot_invent_damage(self):
        battle, entity = self.battle()
        battle._apply_world_contact_impact(entity, {}, 100.0, 0.0)
        battle._apply_world_contact_impact(
            entity, dict(hit=(0, 0, 3), normal=(0, 0, -1)),
            100.0, math.pi / 2)
        self.assertEqual([], battle._pending_landing_impacts)
        self.assertEqual(0.0, vehicle_physics.world_impact_speed(
            (0, 0, -20), (0, 0, -1)))

    def test_airborne_glancing_wall_keeps_tangent_and_downward_momentum(self):
        self.assertEqual((6.0, -12.0, 0.0),
            vehicle_physics.world_contact_velocity((6, -12, 20), (0, 0, -1)))
        normal = (0.6, 0.0, -0.8)
        before = (0.0, -12.0, 20.0)
        after = vehicle_physics.world_contact_velocity(before, normal)
        self.assertAlmostEqual(0.0, sum(a * n for a, n in zip(after, normal)))
        self.assertEqual(-12.0, after[1])
        self.assertLess(sum(v*v for v in after), sum(v*v for v in before))

    def test_actual_airborne_drive_contact_stops_inward_speed_and_reports_hp(self):
        battle, entity = self.battle()
        battle._runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=0.0, turn=0.0, handbrake=False, send_current=lambda: True)
        battle._local_descriptor = entity.typeDescriptor
        battle._local_position = (0.0, 30.0, 0.0)
        battle._attach_local_presentation()
        battle._local_fall_armed = True
        battle._local_airborne = True
        battle._local_speed = 20.0
        battle._local_vertical_speed = -12.0
        battle._suspension_ground_y = mock.Mock(return_value=None)
        battle._resolve_local_tank_contacts = lambda entity, pos, *args: pos

        def wall(*args, **kwargs):
            battle._local_motion_soft_block = False
            battle._local_world_collision_trace = dict(
                hit=(0, 30, 3), normal=(0, 0, -1), reason='upper_lane')
            return False

        battle._motion_is_clear = wall
        battle._drive_local_step(0.04)
        self.assertEqual([20.0], battle._pending_landing_impacts)
        self.assertAlmostEqual(0.0, battle._local_speed)
        self.assertLess(battle._local_vertical_speed, -12.0)
        self.assertTrue(battle._local_airborne)

    def test_suspension_runs_off_a_platform_and_falls_in_world_y(self):
        for dt in (1.0 / 30.0, 1.0 / 120.0):
            with self.subTest(dt=dt):
                battle, entity = self.battle()
                def terrain(x, z, minimum, maximum, **kwargs):
                    height = 0.0 if x < 0.0 else -30.0
                    return height if minimum <= height <= maximum else None
                battle._suspension_ground_y = terrain
                position = (-3.0, 0.0, 0.0)
                for unused in range(int(3.0 / dt)):
                    battle._local_support_motion_pose = position
                    position = battle._update_vertical_motion(
                        entity, (position[0] + 3.0 * dt, position[1], 0.0),
                        0.0, dt)
                self.assertTrue(battle._local_airborne)
                self.assertLess(position[1], -4.0)
                self.assertLess(battle._local_vertical_speed, -5.0)
                self.assertFalse(battle._local_support_rise_blocked)

    def test_bridge_lookahead_cannot_reset_gravity_on_repeated_air_contacts(self):
        # 021934: the upper hull ray hits the bridge at y=0.8602448 while
        # the unsupported body centre is already below it (y=0.5155965).
        normal = (-1.771251589843814e-8, 1.0, -2.6763425253761852e-8)
        for dt in (1.0 / 30.0, 1.0 / 120.0):
            with self.subTest(dt=dt):
                battle, entity = self.battle()
                battle._runtime.bigworld.entities[10] = entity
                battle._server = types.SimpleNamespace(vehicle_id=10)
                battle._sender = types.SimpleNamespace(
                    forward=0.0, turn=0.0, handbrake=False,
                    send_current=lambda: True)
                battle._local_descriptor = entity.typeDescriptor
                battle._local_position = (-3.2659, 0.5155965, 116.7048)
                battle._attach_local_presentation()
                battle._local_fall_armed = True
                battle._local_airborne = True
                battle._local_speed = 6.7629
                battle._local_vertical_speed = -0.135
                battle._suspension_ground_y = mock.Mock(return_value=None)
                battle._resolve_local_tank_contacts = lambda e, pos, *args: pos

                def bridge(*args, **kwargs):
                    battle._local_motion_soft_block = False
                    battle._local_world_collision_trace = dict(
                        hit=(-2.1971, 0.8602448, 116.1028), normal=normal,
                        reason='solid_lane')
                    return False

                battle._motion_is_clear = bridge
                for unused in range(round(1.0 / dt)):
                    battle._drive_local_step(dt)
                self.assertLess(battle._local_position[1], -4.0)
                self.assertLess(battle._local_vertical_speed, -9.0)
                self.assertTrue(battle._local_airborne)
                self.assertEqual([], battle._pending_landing_impacts)

    def test_sideways_air_contact_keeps_fall_speed_and_stops_into_wall(self):
        battle, entity = self.battle()
        battle._local_airborne = True
        battle._local_vertical_speed = -20.0
        battle._local_world_collision_trace = dict(
            hit=(1, 1, 1), normal=(-0.8, 0.6, 0), reason='solid_lane')
        self.assertEqual((0.0, 4.0),
            battle._settle_airborne_lateral_contact(entity, 3.0, 4.0))
        self.assertEqual(-20.0, battle._local_vertical_speed)
        self.assertEqual([], battle._pending_landing_impacts)

    def test_bot_world_contact_changes_hp_and_consumes_only_realised_witness(self):
        runtime = bot_runtime.BotRuntime(1)
        state = dict(id=11, yaw=0.0, vertical_speed=0.0, airborne=True,
                     health=500, max_health=500, alive=True,
                     _world_contact_trace=dict(hit=(0, 1, 3), normal=(0, 0, -1)))
        damage = runtime._apply_world_contact_impact(state, 20.0, 10.0)
        self.assertEqual(vehicle_physics.fall_damage(500, 20), damage)
        self.assertEqual(500 - damage, state['health'])
        self.assertEqual(0, runtime._apply_world_contact_impact(state, 20, 11))

    def test_bot_bridge_lookahead_does_not_turn_downward_speed_into_hp_loss(self):
        runtime = bot_runtime.BotRuntime(1)
        state = dict(id=11, yaw=0.0, vertical_speed=-20.0, airborne=True,
                     health=500, max_health=500, alive=True,
                     _world_contact_trace=dict(hit=(0, 1, 3), normal=(0, 1, 0)))
        self.assertEqual(0, runtime._apply_world_contact_impact(state, 20, 10))
        self.assertEqual(500, state['health'])
        self.assertEqual(-20.0, state['vertical_speed'])
        self.assertNotIn('_world_contact_trace', state)


if __name__ == '__main__':
    unittest.main()
