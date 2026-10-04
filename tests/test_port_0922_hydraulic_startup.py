"""Hydraulic support ownership before the first frame and between rounds."""
import contextlib
import io
import sys
import unittest
from unittest import mock

import test_port_0922_battle_runtime as runtime_fixture
from test_port_0922_siege_braking import local_battle
from gui.mods.offline_lan_0922 import siege_mechanics


class HydraulicStartupLifecycleTests(unittest.TestCase):
    def test_first_drive_samples_ground_before_the_next_frame_uses_it(self):
        for vehicle in siege_mechanics.VEHICLE_PARAMS:
            for mode in (siege_mechanics.DISABLED, siege_mechanics.ENABLED):
                with self.subTest(vehicle=vehicle, mode=mode), \
                        contextlib.redirect_stdout(io.StringIO()):
                    battle, entity = local_battle(vehicle, mode, 0.0)
                    # The exact descriptor's hull-aiming property, not merely
                    # hasSiegeMode, selects the hydraulic support/diagnostics
                    # path. Existing mode tests did not model this guard.
                    entity.typeDescriptor.isPitchHullAimingAvailable = True
                    battle._sender.turn = 0.0
                    battle._local_position = (2.0, 3.0, 4.0)
                    # Retain the complete production vertical/support solver.
                    # Only the native vertical collision query is a flat floor.
                    del battle._update_vertical_motion
                    battle._support_column = lambda x, z, y, maximum_y=None: 3.0
                    report = mock.Mock(wraps=battle._report_local_hydraulic_motion)
                    battle._report_local_hydraulic_motion = report

                    battle._drive_local(0.1)

                    self.assertIsNone(report.call_args.kwargs['origin']['support'])
                    first_support = battle._local_legacy_support_sample
                    self.assertEqual(3.0, first_support[3]['center_y'])
                    self.assertTrue(battle._local_fall_armed)
                    self.assertFalse(battle._local_airborne)
                    self.assertGreater(battle._local_position[2], 4.0)
                    self.assertTrue(battle.client.sent)

                    battle._drive_local(0.1)

                    self.assertIs(first_support,
                                  report.call_args.kwargs['origin']['support'])
                    self.assertIsNot(first_support, battle._local_legacy_support_sample)
                    self.assertEqual(3.0, battle._local_position[1])

    def test_start_and_idempotent_stop_retire_previous_round_support(self):
        runtime = runtime_fixture._runtime()
        battle = runtime_fixture.BattleRuntime(runtime)
        battle._support_column = lambda x, z, y, maximum_y=None: 17.0
        descriptor = runtime_fixture._Descriptor('sweden:S22_Strv_S1')
        descriptor.isPitchHullAimingAvailable = True
        battle._terrain_support((2.0, 17.0, 4.0), 0.0, descriptor)
        self.assertIsNotNone(battle._local_legacy_support_sample)
        create = runtime.offline_map_creator.create
        observed = []

        def create_after_support_reset(map_name):
            # Map creation can synchronously re-enter the runtime. Old-map
            # support must be gone before this first native lifecycle seam.
            observed.append(battle._local_legacy_support_sample)
            return create(map_name)

        runtime.offline_map_creator.create = create_after_support_reset
        with mock.patch.dict(sys.modules, {
                'CurrentVehicle': runtime_fixture._mounted_current_vehicle_module()}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(battle.start({
                'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
                'name': 'Player'}, runtime_fixture._minimal_start(),
                runtime_fixture._Client()))
            self.assertEqual([None], observed)
            battle._terrain_support((2.0, 17.0, 4.0), 0.0, descriptor)
            self.assertIsNotNone(battle._local_legacy_support_sample)
            battle.stop(show_login=False)
            self.assertIsNone(battle._local_legacy_support_sample)
            battle.stop(show_login=False)
            self.assertIsNone(battle._local_legacy_support_sample)


if __name__ == '__main__':
    unittest.main()
