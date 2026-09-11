"""Actual decoded IS-7 geometry through moving-pose and finite-travel consumers."""
import math
import sys
import types
import unittest
from unittest import mock

from test_port_0922_decoded_interior import descriptor_for_record
from test_port_0922_battle_projectiles import _Vector
from gui.mods.offline_lan_0922 import internal_layout_console as catalog
from gui.mods.offline_lan_0922 import internal_hit_layouts as layouts
from gui.mods.offline_lan_0922 import internal_geometry as geometry
from gui.mods.offline_lan_0922 import critical_damage
from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime


class Pose:
    def __init__(self, other=None, yaw=0., translation=(0., 0., 0.)):
        if other is not None:
            self.yaw, self.translation = other.yaw, other.translation
        else:
            self.yaw, self.translation = yaw, translation

    def applyPoint(self, point):
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        return _Vector((point.x*c + point.z*s + self.translation[0],
                        point.y + self.translation[1],
                        -point.x*s + point.z*c + self.translation[2]))

    def invert(self):
        self.yaw = -self.yaw
        previous = self.translation
        self.translation = (0., 0., 0.)
        value = self.applyPoint(_Vector(tuple(-v for v in previous)))
        self.translation = (value.x, value.y, value.z)


class MeshConsumerTests(unittest.TestCase):
    def setUp(self):
        layouts._LAYOUT_CACHE.clear()
        self.record = catalog.CONSOLE_LAYOUTS_0922[layouts._profile_key('ussr:R45_IS-7')]
        self.descriptor = descriptor_for_record('ussr:R45_IS-7', self.record)
        self.layout = layouts.build_layout(self.descriptor, False)
        self.math = types.SimpleNamespace(Vector3=_Vector, Matrix=Pose)
        self.assertTrue(self.layout['valid'], self.layout['validation'])

    def test_current_vehicle_and_turret_poses_preserve_real_mesh_contact_distance(self):
        target = next(t for t in self.layout['targets']
                      if t['entity'] == 'ammoBay' and t['parent'] == 'turret')
        p = target['primitives'][0]
        center = p['center']
        start = (p['minimum'][0] - .2, center[1], center[2])
        end = (p['maximum'][0] + .2, center[1], center[2])
        interval = geometry.target_interval(start, end, target)
        self.assertIsNotNone(interval)
        expected = interval[0] * (end[0] - start[0])
        for position, body_yaw, turret_yaw in (((0.,0.,0.),0.,0.),
                ((80.,3.,-20.),.7,-1.1), ((83.,3.2,-18.),-.4,1.7)):
            with self.subTest(position=position, turret_yaw=turret_yaw):
                body = Pose(yaw=body_yaw, translation=position)
                component = Pose(yaw=turret_yaw, translation=(0.,1.6,.66))
                inverse = Pose(component); inverse.invert()
                vehicle = types.SimpleNamespace(matrix=body,
                    getComponents=lambda: ((self.descriptor.turret, inverse, True),))
                world_start = body.applyPoint(component.applyPoint(_Vector(start)))
                world_end = body.applyPoint(component.applyPoint(_Vector(end)))
                with mock.patch.dict(sys.modules, {'Math': self.math}):
                    hits = critical_damage._offh_internal_ray_hits(
                        vehicle, self.descriptor, world_start, world_end)
                actual = next(distance for distance, name in hits if name == 'ammoBayHealth')
                self.assertAlmostEqual(expected, actual, places=7)
                self.assertEqual(1, sum(name == 'ammoBayHealth' for _, name in hits))

    def test_ten_calibre_consumer_clips_before_and_after_actual_side_rack(self):
        target = next(t for t in self.layout['targets']
                      if t['entity'] == 'ammoBay' and t['parent'] == 'hull')
        piece = target['primitives'][0]
        c = piece['center']
        start = (target['minimum'][0] - 2., c[1], c[2])
        end = (target['maximum'][0] + 2., c[1], c[2])
        entry = geometry.target_interval(start, end, target)[0] * (end[0]-start[0])
        first_material = .5
        for reach, expected in ((entry-.01, False), (entry+.01, True)):
            shell = {'shell': {'caliber': (reach-first_material)*100.}}
            collisions = (types.SimpleNamespace(dist=first_material),)
            _, limited_start, limited_end = BattleRuntime._vehicle_trace(
                shell, _Vector(start), _Vector(end), collisions)
            contact = geometry.target_interval(tuple(limited_start), tuple(limited_end), target)
            self.assertEqual(expected, contact is not None)
            self.assertAlmostEqual(reach, (limited_end-limited_start).length)

    def test_human_and_bot_critical_proposals_use_the_actual_side_rack_mesh(self):
        rack = next(t for t in self.layout['targets']
                    if t['entity']=='ammoBay' and t['parent']=='hull')
        piece = rack['primitives'][0]
        center = piece['center']
        start = _Vector((rack['minimum'][0]-.2, center[1], center[2]))
        end = _Vector((rack['maximum'][0]+.2, center[1], center[2]))
        self.descriptor.hull.ammoBayHealth = types.SimpleNamespace(maxHealth=100, maxRegenHealth=50)
        player = types.SimpleNamespace(playerVehicleID=999,
            arena=types.SimpleNamespace(onVehicleKilled=lambda *args: None))
        bigworld = types.SimpleNamespace(player=lambda: player, time=lambda: 12.)
        results = []
        for target_id in (1, 999):
            vehicle = types.SimpleNamespace(id=target_id, health=500,
                typeDescriptor=self.descriptor, position=_Vector(), matrix=Pose(),
                getComponents=lambda: ((self.descriptor.hull, Pose(), True),))
            armour = types.SimpleNamespace(extra=None, armor=100., vehicleDamageFactor=1.)
            with mock.patch.dict(sys.modules, {'Math':self.math, 'BigWorld':bigworld}), \
                    mock.patch('random.uniform', side_effect=lambda low, high:low), \
                    mock.patch('random.random', return_value=0.):
                damage, payload, delta = critical_damage.propose_direct(vehicle,
                    ((.01,1.,armour,None),), start, end, 100,
                    {'kind':'ARMOR_PIERCING','damage':(100.,2000.)},
                    attacker_id=2, penetrated=True, with_delta=True)
            self.assertEqual(510, damage)
            self.assertTrue(payload['ammo_rack_death'])
            self.assertEqual(1, sum(d['name']=='ammoBayHealth' for d in delta['devices']))
            self.assertFalse(hasattr(vehicle, '_ammo_rack_death'))
            results.append(delta)
        self.assertEqual(results[0], results[1])

    def test_bad_mesh_is_contained_to_its_target(self):
        key = layouts._profile_key('ussr:R45_IS-7')
        record = list(self.record)
        modules = list(record[4]); zone = list(modules[0]); g = dict(zone[3])
        g['payload'] = 'invalid'; zone[3] = g; modules[0] = tuple(zone); record[4] = tuple(modules)
        layouts._LAYOUT_CACHE.clear()
        with mock.patch.dict(catalog.CONSOLE_LAYOUTS_0922, {key: tuple(record)}):
            layout = layouts.build_layout(self.descriptor, False)
        self.assertTrue(any(r['reason']=='invalid_mesh_payload' for r in layout['mesh_rejections']))
        self.assertTrue(layout['valid'], layout['validation'])
        self.assertEqual('profile', layout['logical_entity_sources']['ammoBay']['mode'])
        self.assertTrue(any(t['entity']=='engine' for t in layout['targets']))
        self.assertTrue(any(t['kind']=='crew' for t in layout['targets']))

    def test_shared_destroyed_model_cannot_select_wrong_installed_turret(self):
        self.descriptor.turret.models.undamaged = 'vehicles/example/normal/lod0/turret_99.model'
        self.descriptor.turret.models.destroyed = 'vehicles/example/normal/lod0/turret_01.model'
        layouts._LAYOUT_CACHE.clear()
        layout = layouts.build_layout(self.descriptor, False)
        self.assertFalse(any(t['parent']=='turret' and t['geometry_mode']=='decoded_mesh'
                             for t in layout['targets']))
        self.assertTrue(any(t['parent']=='hull' for t in layout['targets']))


if __name__ == '__main__': unittest.main()
