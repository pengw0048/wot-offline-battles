"""Replay the fence witnesses in the requested f84001a4 baseline report."""
import json
import types
import unittest
from unittest import mock

from test_port_0922_destructibles import ROOT, _Vector as V
import test_port_0922_compiled_collision as compiled
from gui.mods.offline_lan_0922 import destructibles_sensor as sensor


class BaselineFenceTests(unittest.TestCase):
    def setUp(self):
        self.contacts = json.loads((ROOT /
            'tests/fixtures/malinovka_202347_contacts.json').read_text())
        self.broken = set()
        self.addCleanup(sensor.set_catalog, None)
        sensor.set_catalog(json.loads(
            (ROOT / 'destructibles/02_malinovka.json').read_text()))
        state = mock.patch.dict(sensor.__dict__, {
            'xrange': range, 'g_offh_destr_instances': {},
            'g_offh_destr_contact_bins': {},
            'g_offh_destr_speculative': set(),
            'g_offh_destr_isolated_chunks': set(),
            'g_offh_destr_isolated_slots': set()})
        state.start()
        self.addCleanup(state.stop)
        authority = types.SimpleNamespace(
            is_destroyed=lambda *key: key in self.broken)
        patch = mock.patch.object(sensor, '_get_destr_authority',
                                  return_value=authority)
        patch.start()
        self.addCleanup(patch.stop)

    def install(self, contact):
        sensor.g_offh_destr_instances.clear()
        sensor.g_offh_destr_contact_bins.clear()
        self.broken.clear()
        for owner in contact['native_contact_evidence']['nearby_owners']:
            identity = tuple(owner['identity'])
            instance = dict(filename=owner['filename'], kind=owner['kind'],
                boxes=[(b['center'], b['axes'], b['material'])
                       for b in owner['boxes']])
            sensor.g_offh_destr_instances[identity] = instance
            sensor._index_catalog_instance_1513(
                sensor.g_offh_destr_contact_bins, identity, instance)
            self.broken.update(identity + (b['material'],)
                               for b in owner['boxes'] if b['broken'])
        witness = contact['native_contact_evidence']['surface_witnesses'][0]
        return (V(contact['ray_start']), V(contact['ray_end']),
                V(witness['hit']), tuple(witness['key']))

    @staticmethod
    def query(a, b, surfaces, pruned=False):
        calls = []
        native = (compiled.CrossMapRailingCollisionTests.pruned_native if pruned else
                  compiled.CompiledCollisionTests.native)(surfaces)
        def collide(*args):
            calls.append(args)
            return native(*args)
        result = sensor.collide_motion_segment(
            1, a, b, lambda *key: True, collide)
        return result, len(calls)

    def test_all_five_reported_broken_faces_clear_with_live_neighbour_half(self):
        self.assertEqual(5, len(self.contacts))
        for contact in self.contacts:
            with self.subTest(time=contact['log_time']):
                a, b, point, alias = self.install(contact)
                result, calls = self.query(a, b, [(point, alias)])
                self.assertIsNone(result)
                self.assertLessEqual(calls, 4)
                self.broken.clear()
                result, unused = self.query(a, b, [(point, alias)])
                self.assertIs(point, result[0])

    def test_reported_faces_do_not_hide_replacements_or_vehicle_only_walls(self):
        for contact in self.contacts:
            for material in (88, 111):
                for pruned in (False, True):
                    with self.subTest(time=contact['log_time'],
                                      material=material, pruned=pruned):
                        a, b, point, alias = self.install(contact)
                        direction = b - a
                        direction.normalise()
                        wall = point + direction.scale(.001)
                        result, calls = self.query(a, b, [
                            (point, alias), (wall, (material, 0, 23, 32636))],
                            pruned=pruned)
                        self.assertIs(wall, result[0])
                        self.assertLessEqual(calls, 4)

    def test_a_live_component_inside_the_same_model_envelope_remains_solid(self):
        a, b, point, alias = self.install(self.contacts[0])
        direction = b - a
        direction.normalise()
        wall = point + direction.scale(.2)
        b = b + direction.scale(.3)
        identity = next(identity for identity, instance in
            sensor.g_offh_destr_instances.items()
            if identity + (alias[0],) not in self.broken)
        instance = sensor.g_offh_destr_instances[identity]
        instance['boxes'] = list(instance['boxes']) + [
            ((wall.x, wall.y, wall.z),
             ((.005, 0, 0), (0, .005, 0), (0, 0, .005)), alias[0])]
        sensor._index_catalog_instance_1513(
            sensor.g_offh_destr_contact_bins, identity, instance)
        result, unused = self.query(a, b, [(point, alias), (wall, alias)])
        self.assertIs(wall, result[0])
