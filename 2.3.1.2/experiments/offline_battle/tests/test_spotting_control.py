import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import package_stub

spotting = package_stub.load('spotting')
spotting_control = package_stub.load('spotting_control')


class _Descriptor(object):
    class turret(object):
        circularVisionRadius = 350.0

    class type(object):
        invisibility = (0.25, 0.1)


class LawInputTests(unittest.TestCase):
    def test_camouflage_pair_reads_still_then_moving(self):
        self.assertEqual(spotting_control.camouflage_pair(_Descriptor()),
                         (0.1, 0.25))

    def test_a_bare_descriptor_falls_back(self):
        self.assertEqual(spotting_control.camouflage_pair(object()),
                         spotting_control.DEFAULT_CAMOUFLAGE)

    def test_view_range_follows_the_turret(self):
        self.assertEqual(spotting_control.view_range(_Descriptor()),
                         spotting.effective_view_range(350.0))

    def test_blocked_sight_only_detects_at_proximity(self):
        self.assertFalse(spotting.is_detected(
            100.0, 400.0, 0.0, has_line_of_sight=False))
        self.assertTrue(spotting.is_detected(
            40.0, 400.0, 0.0, has_line_of_sight=False))

    def test_camouflage_shrinks_the_detection_distance(self):
        camouflaged = spotting.detection_distance(400.0, 0.5)
        plain = spotting.detection_distance(400.0, 0.0)
        self.assertLess(camouflaged, plain)


if __name__ == '__main__':
    unittest.main()
