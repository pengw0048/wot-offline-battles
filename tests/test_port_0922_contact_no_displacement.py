"""Zero contact travel retains response bookkeeping without a roster sweep."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tests import test_port_0922_bot_runtime as fixtures


class ContactNoDisplacementTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.BotRuntimeTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.runtime = self.fixture.runtime
        self.runtime.battle_start(self.fixture.start)
        self.state = self.runtime.states[11]
        self.state.update(x=1., y=2., z=3., yaw=0., speed=4.,
                          push_x=0., push_z=0., _contact_forward_speed=0.)

    def test_zero_travel_keeps_forward_impulse_without_reading_roster(self):
        with mock.patch.object(self.runtime, '_contact_motion_bodies',
                               side_effect=AssertionError('unneeded roster')):
            self.runtime._apply_tank_contact_response(
                self.state, {'delta_velocity': (0., 2.),
                             'correction': (0., 0.)}, .1,
                advance_push=False, apply_friction=False)
        self.assertEqual((1., 2., 3.), tuple(self.state[k] for k in ('x', 'y', 'z')))
        self.assertEqual(6., self.state['speed'])
        self.assertEqual(2., self.state['_contact_forward_speed'])
        self.assertEqual((0., 0.), (self.state['push_x'], self.state['push_z']))
        self.assertTrue(self.state['_contact_dynamics'])

    def test_absorbed_push_still_spends_friction_then_skips_roster(self):
        self.state['push_x'] = .05
        with mock.patch.object(self.runtime, '_bleed_contact_push',
                               return_value=(0., 0.)) as friction, \
                mock.patch.object(self.runtime, '_contact_motion_bodies',
                                  side_effect=AssertionError('unneeded roster')):
            self.runtime._apply_tank_contact_response(
                self.state, {'delta_velocity': (0., 0.),
                             'correction': (0., 0.)}, .1)
        friction.assert_called_once_with(self.state, .05, 0., .1)
        self.assertEqual((1., 3.), (self.state['x'], self.state['z']))
        self.assertEqual((0., 0.), (self.state['push_x'], self.state['push_z']))

    def test_nonzero_correction_keeps_the_real_vehicle_sweep(self):
        collision = self.fixture.module.tank_collision
        with mock.patch.object(self.runtime, '_contact_motion_bodies',
                               wraps=self.runtime._contact_motion_bodies) as roster, \
                mock.patch.object(collision, 'translation_fraction',
                                  return_value=0.) as sweep, \
                mock.patch.object(collision, 'slide_translation',
                                  return_value=(0., 0.)) as slide:
            self.runtime._apply_tank_contact_response(
                self.state, {'delta_velocity': (0., 0.),
                             'correction': (1.e-10, 0.)}, .1,
                advance_push=False)
        roster.assert_called_once()
        sweep.assert_called_once()
        slide.assert_called_once()
        self.assertEqual((1.e-10, 0.), sweep.call_args.args[1])
        self.assertEqual((1., 3.), (self.state['x'], self.state['z']))


if __name__ == '__main__':
    unittest.main()
