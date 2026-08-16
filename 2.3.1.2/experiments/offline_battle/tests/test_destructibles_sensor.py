import importlib.util
import unittest
from pathlib import Path

# Loaded from the file, because other tests park an inert stand-in for
# this module under its package name.
_PATH = (Path(__file__).resolve().parents[1] / 'src' / 'res' / 'scripts' /
         'client' / 'gui' / 'mods' / 'offline_battle_2312' /
         'destructibles_sensor.py')
_spec = importlib.util.spec_from_file_location(
    'destructibles_sensor_under_test', _PATH)
sensor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sensor)


class DecodeMatInfoTests(unittest.TestCase):
    """Pin the measured 2.3.1.2 ABI: five items, no chunk/item tail.

    The stock consumer is EffectMaterialCalculation, which unpacks
    (collided, hitPoint, surfNormal, matKind, fileName)."""

    def test_a_hit_decodes_with_no_item_identity(self):
        decoded = sensor._decode_mat_info_1513(
            (True, 'point', 'normal', 73, 'tree.model'))
        self.assertEqual(decoded,
                         ('point', 'normal', None, None, 73, 'tree.model'))

    def test_a_miss_decodes_to_none(self):
        self.assertIsNone(sensor._decode_mat_info_1513(
            (False, None, None, 0, '')))

    def test_the_1513_seven_item_shape_fails_loudly(self):
        with self.assertRaises(RuntimeError):
            sensor._decode_mat_info_1513(
                (True, 'point', 'normal', 73, 'tree.model', 4, 9))

    def test_a_non_tuple_fails_loudly(self):
        with self.assertRaises(RuntimeError):
            sensor._decode_mat_info_1513(None)

    def test_the_none_identity_fails_closed_in_the_registries(self):
        self.assertIsNone(sensor._registered_item_scale_1513(
            None, None, 'tree.model'))


if __name__ == '__main__':
    unittest.main()
