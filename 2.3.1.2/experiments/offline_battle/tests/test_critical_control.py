import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import package_stub

package_stub.stub('critical_damage')
critical_control = package_stub.load('critical_control')


class EventCodeTests(unittest.TestCase):
    def test_a_destroyed_device_by_shot(self):
        self.assertEqual(
            critical_control.event_code(
                {'kind': 'device', 'state': 'destroyed', 'cause': 'shot'}),
            'DEVICE_DESTROYED_AT_SHOT')

    def test_a_critical_device_by_fire(self):
        self.assertEqual(
            critical_control.event_code(
                {'kind': 'device', 'state': 'critical', 'cause': 'fire'}),
            'DEVICE_CRITICAL_AT_FIRE')

    def test_a_repaired_device(self):
        self.assertEqual(
            critical_control.event_code(
                {'kind': 'device', 'state': 'normal', 'cause': 'repair'}),
            'DEVICE_REPAIRED')

    def test_a_repair_that_only_reaches_critical(self):
        self.assertEqual(
            critical_control.event_code(
                {'kind': 'device', 'state': 'critical', 'cause': 'repair'}),
            'DEVICE_REPAIRED_TO_CRITICAL')

    def test_crew_hit_and_restored(self):
        self.assertEqual(
            critical_control.event_code(
                {'kind': 'crew', 'state': 'destroyed', 'cause': 'shot'}),
            'TANKMAN_HIT_AT_SHOT')
        self.assertEqual(
            critical_control.event_code(
                {'kind': 'crew', 'state': 'normal', 'cause': 'repair'}),
            'TANKMAN_RESTORED')

    def test_fire_started_and_stopped(self):
        self.assertEqual(
            critical_control.event_code(
                {'kind': 'fire', 'state': True, 'cause': 'shot'}),
            'DEVICE_STARTED_FIRE_AT_SHOT')
        self.assertEqual(
            critical_control.event_code(
                {'kind': 'fire', 'state': False, 'cause': 'repair'}),
            'FIRE_STOPPED')

    def test_an_ammo_rack_event_has_no_code(self):
        self.assertIsNone(critical_control.event_code(
            {'kind': 'ammo_rack', 'state': 'destroyed'}))


class ExtraIndexTests(unittest.TestCase):
    def _descriptor(self):
        return types.SimpleNamespace(extras=(
            types.SimpleNamespace(name='fuelTankHealth'),
            types.SimpleNamespace(name='engineHealth'),
            types.SimpleNamespace(name='chassisHealth'),
        ))

    def test_a_device_name_resolves_to_its_extra(self):
        self.assertEqual(
            critical_control.extra_index(self._descriptor(),
                                         'chassisHealth'), 2)

    def test_a_bare_name_gains_the_health_suffix(self):
        self.assertEqual(
            critical_control.extra_index(self._descriptor(), 'engine'), 1)

    def test_an_unknown_name_reports_zero(self):
        self.assertEqual(
            critical_control.extra_index(self._descriptor(), 'radioman'), 0)


if __name__ == '__main__':
    unittest.main()
