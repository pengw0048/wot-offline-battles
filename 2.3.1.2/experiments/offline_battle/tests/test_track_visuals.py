import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import package_stub

track_visuals = package_stub.load('track_visuals')


class EngineModeTests(unittest.TestCase):
    def test_forward_drive(self):
        self.assertEqual(track_visuals.engine_mode(3.0, 0.0, False), (2, 1))

    def test_reverse_drive(self):
        self.assertEqual(track_visuals.engine_mode(-2.0, 0.0, False), (2, 2))

    def test_pivot_turns(self):
        self.assertEqual(track_visuals.engine_mode(0.0, 0.5, False), (2, 4))
        self.assertEqual(track_visuals.engine_mode(0.0, -0.5, False), (2, 8))

    def test_idle(self):
        self.assertEqual(track_visuals.engine_mode(0.0, 0.0, False), (1, 0))

    def test_dead_engine(self):
        self.assertEqual(track_visuals.engine_mode(5.0, 0.0, True), (0, 0))


class _Appearance(object):
    def __init__(self):
        self.modes = []
        self.trackScrollController = None

    def changeEngineMode(self, mode):
        self.modes.append(mode)


class _Vehicle(object):
    def __init__(self):
        self.id = 5
        self.appearance = _Appearance()


class DriveEngineTests(unittest.TestCase):
    def test_publishes_only_the_changes(self):
        vehicle = _Vehicle()
        track_visuals.drive_engine(vehicle, 3.0, 0.0, False)
        track_visuals.drive_engine(vehicle, 4.0, 0.0, False)
        track_visuals.drive_engine(vehicle, 0.0, 0.0, False)
        self.assertEqual(vehicle.appearance.modes, [(2, 1), (1, 0)])
        self.assertEqual(vehicle.engineMode, (1, 0))


class EnsureScrollTests(unittest.TestCase):
    def test_a_missing_controller_reports_once(self):
        vehicle = _Vehicle()
        logs = []
        self.assertFalse(track_visuals.ensure_scroll(vehicle, logs.append))
        self.assertFalse(track_visuals.ensure_scroll(vehicle, logs.append))
        self.assertEqual(len(logs), 1)

    def test_activates_with_the_native_filter(self):
        calls = []

        class _Controller(object):
            def activate(self):
                calls.append('activate')

            def setData(self, entity_filter):
                calls.append(('setData', entity_filter))

        vehicle = _Vehicle()
        vehicle.appearance.trackScrollController = _Controller()
        native = object()
        vehicle.filter = types.SimpleNamespace(_filter=native)
        self.assertTrue(track_visuals.ensure_scroll(vehicle, None))
        self.assertEqual(calls, ['activate', ('setData', native)])


if __name__ == '__main__':
    unittest.main()
