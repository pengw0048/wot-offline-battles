"""9.22 scenery regressions using the shipped Murovanka placement catalog."""
from pathlib import Path
import json
import sys
import types
import unittest
from unittest import mock

import test_port_0922_destructibles as fixture
from gui.mods.offline_lan_0922 import destructibles_sensor as sensor

ROOT = Path(__file__).resolve().parents[1]
_Vector = fixture._Vector


class _SignatureMatrix(object):
    def __init__(self, signature):
        self.signature = signature

    def applyVector(self, point):
        s = self.signature
        return _Vector(*[sum(s[3 + axis * 3 + row] * value
            for axis, value in enumerate((point.x, point.y, point.z))) / 1000.
            for row in range(3)])

    def applyPoint(self, point):
        return self.applyVector(point) + _Vector(*[
            x / 1000. for x in self.signature[:3]])


class AnonymousDestructibleLayoutTests(unittest.TestCase):
    def setUp(self):
        sensor.set_catalog(json.loads((ROOT / 'destructibles/11_murovanka.json').read_text()))
        prepared = sensor._destructible_catalog
        self.rows = {}
        self.by_name = {}
        categories = {'tree': 0, 'falling': 1, 'fragile': 2, 'structure': 3}
        for group in ('baked_instances', 'tree_instances'):
            for wire, record in prepared[group].items():
                if wire[0] == 32124:
                    self.rows[wire[1] + (wire[1] >= 7)] = record
                    self.by_name[record['descriptor_filename']] = {
                        'type': categories[record['kind']]}
        self.area = types.SimpleNamespace(DESTR_TYPE_TREE=0,
            DESTR_TYPE_FALLING_ATOM=1, DESTR_TYPE_FRAGILE=2, DESTR_TYPE_STRUCTURE=3,
            g_cache=types.SimpleNamespace(getDescByFilename=self.by_name.get))
        self.native = types.SimpleNamespace(now=0.)
        self.native.time = lambda: self.native.now
        self.native.wg_getChunkMatrix = lambda *args: types.SimpleNamespace(translation=_Vector())
        self.native.wg_getDestructibleMatrix = lambda space, chunk, item: _SignatureMatrix(
            self.rows[item]['signature'])
        # Real anonymous fragile groups have no effect handler and return -1;
        # the old test assumed every map model returned its descriptor type.
        self.native.wg_getDestructibleEffectCategory = lambda space, chunk, item, material: (
            0 if item in self.rows and self.rows[item]['kind'] == 'tree' else -1)
        self.native.wg_getDestructibleFilename = mock.Mock(
            side_effect=AssertionError('unsafe native scalar filename query'))
        self.math = types.SimpleNamespace(Matrix=lambda value: value, Vector3=_Vector)

    def tearDown(self):
        sensor.set_catalog(None)

    def test_murovanka_shift_keeps_anonymous_wood_fences_in_live_catalog(self):
        names = [self.rows[item]['descriptor_filename'] for item in sorted(self.rows)
                 if self.rows[item]['kind'] == 'tree']
        with mock.patch.dict(sys.modules, {'Math': self.math}):
            for tick in range(20):
                self.native.now += .04
                mapping, status = sensor._chunk_native_names_1513(
                    self.native, self.area, 1, 32124, max(self.rows) + 1, names)
                if status != 'pending_alignment':
                    break
        self.assertEqual('exact', status)
        self.assertIn('gaf001_WoodFence', mapping[8])
        prepared = sensor._destructible_catalog
        self.assertEqual(self.rows[8], prepared['baked_instances'][(32124, 8)])
        self.assertNotIn((32124, 8), prepared['excluded_instances'])
        self.assertIn((32124, 7), prepared['excluded_instances'])
        self.assertTrue(any((32124, 8) in wires
                            for wires in prepared['baked_shot_bins'].values()))
        self.assertEqual(set(self.rows), set(mapping))
        self.assertFalse(sensor._destructible_isolated_1513(32124, 8))
        self.native.wg_getDestructibleFilename.assert_not_called()

    def test_unregistered_type_requires_unique_exact_placement_and_descriptor(self):
        probe = lambda names=(): sensor._probe_authored_placement_1513(
            self.native, self.area, 1, 32124, 8, -1, names, max(self.rows) + 1)
        with mock.patch.dict(sys.modules, {'Math': self.math}):
            self.assertIsNotNone(probe())
            resource = self.rows[8]['descriptor_filename']
            correct = self.by_name[resource]
            self.by_name[resource] = {'type': 1}
            self.assertIsNone(probe())
            self.by_name[resource] = correct
            names = [''] * (max(self.rows) + 1)
            names[8] = 'content/Environment/other.model'
            self.assertIsNone(probe(names))
            original = self.native.wg_getDestructibleMatrix
            sig = self.rows[8]['signature']
            self.native.wg_getDestructibleMatrix = lambda *args: _SignatureMatrix(
                (sig[0] + 20,) + sig[1:])
            self.assertIsNone(probe())
            self.native.wg_getDestructibleMatrix = original
            # A tree's absent effect handler does not satisfy the stronger
            # SpeedTree identity gate, so the recovery remains model-only.
            tree_item = next(item for item, row in self.rows.items() if row['kind'] == 'tree')
            self.assertIsNone(sensor._probe_authored_placement_1513(
                self.native, self.area, 1, 32124, tree_item, -1, (), max(self.rows) + 1))
        self.native.wg_getDestructibleFilename.assert_not_called()


if __name__ == '__main__':
    unittest.main()
