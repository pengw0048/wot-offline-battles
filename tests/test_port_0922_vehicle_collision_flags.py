"""Vehicle-only bridge faces must survive native triangle flag filtering.

This is an analytic collision scene, not the unavailable Live Oaks model.
Unlike the earlier rail fixtures it applies the native skip-mask contract.
0x4900/0x4980/0x4990 all occur in the shipped map's BSMO flag census.
"""
import math
import sys
import types
import unittest
from unittest import mock

import test_port_0922_battle_runtime as fixtures
from gui.mods.offline_lan_0922 import bot_runtime, vehicle_physics, world_collision
from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime


class FlaggedBridge:
    deck = 5.014

    def __init__(self, yaw=-1.95):
        self.sine, self.cosine = math.sin(yaw), math.cos(yaw)

    def world(self, x, y, z):
        return (264.0 + self.cosine*x + self.sine*z, y,
                -248.0 - self.sine*x + self.cosine*z)

    def collide(self, space, start, end, skip, surface_filter=None):
        # Vertical columns intersect finite rectangles. No extrapolated
        # bridge support is supplied outside their authored lateral/end bounds.
        x = self.cosine*(start.x-264.0) - self.sine*(start.z+248.0)
        z = self.sine*(start.x-264.0) + self.cosine*(start.z+248.0)
        layers = [(-8.0, 1.0, 8), (-6.0, 1.0, 64)]  # terrain and water
        if z < 0:
            layers.append((self.deck, 1.0, 0x4900))
        elif z <= 50.0 and abs(x) <= 4.0:
            layers += [(self.deck, 1.0, 0x4980),
                       (self.deck-0.2, -1.0, 0x4980),
                       (self.deck+0.6, 1.0, 0x4990)]  # non-solid decoration
            if abs(abs(x)-0.72) < 0.045:
                layers.append((self.deck+0.338, 1.0, 0x4900))
        if start.y <= end.y:
            return None
        for height, normal, flags in sorted(layers, reverse=True):
            if flags & skip or not end.y <= height <= start.y:
                continue
            hit = (fixtures._Vector(start.x, height, start.z),
                   fixtures._Vector(0, normal, 0))
            if surface_filter is None or surface_filter(flags >> 8, flags & 255, 0, 0):
                return hit
        return None


class VehicleCollisionFlagsTests(unittest.TestCase):
    def battle(self, scene):
        runtime = fixtures._runtime()
        runtime.bigworld.wg_collideSegment = scene.collide
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._local_fall_armed = True
        entity = fixtures._Vehicle(10, fixtures._suspension_descriptor(),
            fixtures._Vector(), (0, 0, 0), {'health': 500})
        entity.typeDescriptor.chassis.hitTester.bbox = (
            fixtures._Vector(-1.645, -0.8, -3.5),
            fixtures._Vector(1.645, 0.8, 3.5))
        return battle, entity

    def test_vehicle_deck_is_support_even_where_projectile_ray_misses(self):
        scene = FlaggedBridge()
        battle, unused = self.battle(scene)
        for across in (-3.8, -2.5, 0.0, 2.5, 3.8):
            x, y, z = scene.world(across, scene.deck, 20)
            start, end = fixtures._Vector(x, y+0.3, z), fixtures._Vector(x, y-0.1, z)
            self.assertIsNone(scene.collide(1, start, end, 128))
            self.assertAlmostEqual(y, battle._suspension_ground_y(x, z, y-0.1, y+0.3))
            self.assertAlmostEqual(y, battle._ground_y(x, z, y))
            self.assertAlmostEqual(y, battle._navigation_ground(x, z, y))
        # The same shape is transparent to an actual projectile query.
        battle._destructibles = None
        shot = battle._resolve_shot_scene(start, end, fixtures._Vector(0, -1, 0), {})
        self.assertEqual(999999.0, shot['world_distance'])

    def test_lateral_crossings_stay_supported_then_release_beyond_side_and_end(self):
        for dt in (1.0/30, 1.0/120):
            for speed in (2.0, 20.0):
                with self.subTest(dt=dt, speed=speed):
                    scene = FlaggedBridge()
                    battle, entity = self.battle(scene)
                    along = -5.0
                    position = scene.world(0, scene.deck, along)
                    while along < 42.0:
                        along += speed*dt
                        across = 1.5*math.sin(max(0.0, along)*0.18)
                        heading = -1.95 + math.atan(0.27*math.cos(max(0.0, along)*0.18))
                        battle._local_support_motion_pose = position
                        position = battle._update_vertical_motion(entity,
                            scene.world(across, position[1], along), heading, dt)
                        self.assertGreater(position[1], scene.deck-0.12)
                        self.assertLess(abs(battle._local_roll), 0.3)
                    # Drive over the actual end; confirmed vehicle-only deck
                    # faces must not become a remembered infinite plane.
                    for unused in range(int(3.0/dt)):
                        along += 10.0*dt
                        battle._local_support_motion_pose = position
                        position = battle._update_vertical_motion(entity,
                            scene.world(0, position[1], along), -1.95, dt)
                    self.assertLess(position[1], 0.0)
                    # Repeat through the side edge after acquiring the deck.
                    battle, entity = self.battle(scene)
                    position = scene.world(0, scene.deck, 20.0)
                    across = 0.0
                    for unused in range(int(4.0/dt)):
                        across += 3.0*dt
                        battle._local_support_motion_pose = position
                        position = battle._update_vertical_motion(entity,
                            scene.world(across, position[1], 20.0), -1.95, dt)
                    self.assertLess(position[1], 0.0)

    def test_bot_and_under_bridge_ground_share_vehicle_flags_without_snapping_up(self):
        scene = FlaggedBridge()
        battle, entity = self.battle(scene)
        params = vehicle_physics.derive_suspension_params(entity.typeDescriptor)
        runtime = bot_runtime.BotRuntime(1,
            suspension_ground_probe=battle._suspension_ground_y)
        x, y, z = scene.world(2.0, scene.deck, 20.0)
        state = dict(id=11, x=x, y=y, z=z, yaw=-1.95, speed=2.0,
                     terrain_pitch=0.0, roll=0.0, vertical_speed=0.0,
                     airborne=False, grounded_once=True, health=500, max_health=500)
        for unused in range(90):
            runtime._update_suspension_vertical_motion(state, 1.0/30, params)
            self.assertGreater(state['y'], scene.deck-0.12)
        # Looking below the deck rejects its underside and decorative water;
        # an ordinary bridge underpass remains on the actual lower terrain.
        self.assertEqual(-8.0, battle._suspension_ground_y(x, z, -9, scene.deck-0.1))
        self.assertEqual(-8.0, battle._ground_y(x, z, -8.0))

    def test_hull_and_ground_ahead_queries_keep_vehicle_only_geometry(self):
        start, end = fixtures._Vector(0, 1, 0), fixtures._Vector(0, 1, 5)
        wall = (fixtures._Vector(0, 1, 3), fixtures._Vector(0, 0, -1))
        def collide(space, ray_start, ray_end, skip, *filters):
            # A projectile-only blocker at z=1 must not block the hull. A
            # vehicle-only guard at z=3 must block it, including filtered rays.
            candidates = ((1, 0x10), (3, 0x80))
            for distance, flags in candidates:
                if not flags & skip:
                    return wall if distance == 3 else (fixtures._Vector(0, 1, 1), wall[1])
            return None
        with mock.patch.dict(sys.modules, {'BigWorld': types.SimpleNamespace(wg_collideSegment=collide)}):
            self.assertEqual(wall, world_collision._collide_horizontal(1, start, end, None))
            self.assertEqual(wall, world_collision._collide_horizontal(1, start, end, lambda hit: True))
        scene = FlaggedBridge()
        x, y, z = scene.world(2.0, scene.deck, 20)
        with mock.patch.dict(sys.modules, {'BigWorld': types.SimpleNamespace(wg_collideSegment=scene.collide)}):
            self.assertAlmostEqual(y, world_collision._ground_top(1,
                types.SimpleNamespace(Vector3=fixtures._Vector),
                fixtures._Vector(x, y, z), x, z, 1.0, collision_filter=None))


if __name__ == '__main__':
    unittest.main()
