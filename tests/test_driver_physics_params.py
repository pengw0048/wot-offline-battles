"""Accepted player factors survive mirrored startup and native mode swaps."""
import copy
import io
import math
import types
import unittest
from unittest import mock

import tests.test_port_0922_battle_runtime as fixtures
from gui.mods.offline_lan_0922 import vehicle_physics


class DriverPhysicsParameterTests(unittest.TestCase):
    def battle(self):
        runtime = fixtures._runtime()
        battle = fixtures.BattleRuntime(runtime)
        battle._player_driver_mode = True
        battle._avatar = runtime.bigworld.avatar
        battle.client = fixtures._Client()
        descriptor = fixtures._Descriptor()
        descriptor.physics.update(
            enginePower=100000.0, weight=12000.0,
            terrainResistance=(0.7, 0.8, 1.7),
            rollingFrictionFactors=(0.95, 0.9, 0.85))
        factors = {'engine/power': 1.15, 'vehicle/rotationSpeed': 1.2,
                   'chassis/terrainResistance': (0.96, 0.93, 0.85)}
        accepted = fixtures._effective_params_snapshot()
        accepted['physics'] = vehicle_physics.derive_params(descriptor, factors)
        accepted['loadout']['vehicle_rotation_factor'] = factors['vehicle/rotationSpeed']
        accepted['loadout']['terrain_resistance_factors'] = factors['chassis/terrainResistance']
        accepted['spotting'].update(recon_level=100.0, camouflage_level=100.0,
                                    vision_factor=1.0537, camouflage_factor=1.0344)
        battle._local_effective_params = copy.deepcopy(accepted)
        battle._local_descriptor = descriptor
        entity = fixtures._Vehicle(
            10, descriptor, fixtures._Vector(2, 3, 4), (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0, aim_yaw=0.0, gun_pitch=0.0,
            handbrake=False, send_current=mock.Mock(return_value=True))
        battle._local_position = (2.0, 3.0, 4.0)
        battle._garage_loadout_snapshot = mock.Mock(
            side_effect=AssertionError('driver read the empty mirrored garage'))
        return battle, entity, accepted, factors

    def test_initial_physics_and_consumers_use_accepted_player_not_empty_crew(self):
        battle, entity, accepted, factors = self.battle()
        self.assertTrue(battle._prepare_local_presentation(entity))
        self.assertEqual(accepted['physics'], battle._local_physics)
        self.assertIsNot(battle._local_effective_params['physics'], battle._local_physics)
        self.assertEqual(factors, battle._local_factors(entity.typeDescriptor))
        self.assertEqual(accepted['loadout'], battle._local_loadout(entity.typeDescriptor))
        self.assertEqual(accepted['spotting'],
                         battle._spotting_profile(entity.typeDescriptor, local=True))
        self.assertEqual(accepted['ramming'], battle._ram_profile(entity.typeDescriptor, local=True))
        self.assertAlmostEqual(330.0 * 1.0537,
                               battle._vision_radius(entity.typeDescriptor, local=True))
        self.assertAlmostEqual(accepted['physics']['rotSpd'],
                               battle._destructible_rotation_speed_cap(battle._local_physics))
        battle._garage_loadout_snapshot.assert_not_called()

    def test_two_siege_roundtrips_reuse_frozen_mounted_factors_and_mode_geometry(self):
        battle, entity, accepted, factors = self.battle()
        travel = entity.typeDescriptor
        travel.hasSiegeMode = True
        self.assertTrue(battle._prepare_local_presentation(entity))
        siege = copy.deepcopy(travel)
        siege.physics.update(enginePower=70000.0, speedLimits=(2.0, 1.5),
                             terrainResistance=(1.0, 1.5, 2.0), trackCenterOffset=1.1)
        siege.chassis.rotationSpeed = 0.3
        states = battle._runtime.constants.VEHICLE_SIEGE_STATE

        def native_swap(unused_id, state, unused_seconds):
            entity.typeDescriptor = siege if state == states.ENABLED else travel
            entity.siegeState = state

        battle._binding = types.SimpleNamespace(update_vehicle_siege_state=native_swap)
        battle._select_local_siege_pose = mock.Mock()
        battle._update_local_hull_aiming = mock.Mock()
        battle._report_local_siege_edge = mock.Mock()
        record = {'local': True, 'engine_id': 10, 'presented_siege_state': states.DISABLED}
        # Optimistic selection and a later row cannot rebind this round's crew.
        battle.client.effective_params = fixtures._effective_params_snapshot()
        battle._records['player:1'] = {'state': {'effective_params': battle.client.effective_params}}
        for unused in range(2):
            self.assertTrue(battle._apply_siege_state(record, {'siege_state': states.ENABLED}))
            self.assertEqual(vehicle_physics.derive_params(siege, factors), battle._local_physics)
            self.assertAlmostEqual(70000.0 * 1.15, battle._local_physics['powerW'])
            self.assertEqual(2.0, battle._local_physics['speedFwd'])
            self.assertEqual(1.1, battle._local_physics['trackCenter'])
            self.assertTrue(battle._apply_siege_state(record, {'siege_state': states.DISABLED}))
            self.assertEqual(accepted['physics'], battle._local_physics)
        self.assertEqual(accepted, battle._local_effective_params)
        battle._garage_loadout_snapshot.assert_not_called()

    def test_damage_and_active_equipment_do_not_enter_frozen_mounting_factors(self):
        battle, entity, accepted, factors = self.battle()
        battle._prepare_local_presentation(entity)
        battle._local_model = entity.model
        battle._smoothed_drive_pitch = mock.Mock(return_value=0.0)
        battle._active_engine_power_factor = mock.Mock(return_value=1.1)
        entity._offlineStunFactors = {'speed': 0.8}
        observed = []

        class ReachedIntegrator(Exception):
            pass

        def integrate(*args):
            observed.append(args)
            raise ReachedIntegrator()

        def damaged(unused_entity, stat):
            return 0.5 if stat == 'mobility' else 1.0

        with mock.patch.object(fixtures.battle_runtime_module.critical_damage,
                               'stat_factor', side_effect=damaged), \
                mock.patch.object(vehicle_physics, 'longitudinal_step', side_effect=integrate):
            with self.assertRaises(ReachedIntegrator):
                battle._drive_local_step(0.1)
        physics, unused_speed, throttle = observed[0][:3]
        self.assertAlmostEqual(100000.0 * 1.15 * 1.1, physics['powerW'])
        self.assertAlmostEqual(accepted['physics']['speedFwd'] * 0.8, physics['speedFwd'])
        self.assertEqual(0.5, throttle)
        self.assertEqual(accepted['physics'], battle._local_physics)
        self.assertEqual(factors, battle._local_factors(entity.typeDescriptor))

    def test_missing_accepted_inputs_do_not_synthesize_default_driver_parameters(self):
        for method in ('physics', 'loadout', 'spotting', 'factors'):
            with self.subTest(method=method):
                battle, entity, unused_accepted, unused_factors = self.battle()
                battle._local_effective_params = None
                battle._accepted_local_effective_params = lambda: None
                with self.assertRaisesRegex(RuntimeError, 'player driver'):
                    if method == 'physics':
                        battle._prepare_local_presentation(entity)
                    elif method == 'spotting':
                        battle._spotting_profile(entity.typeDescriptor, local=True)
                    else:
                        getattr(battle, '_local_' + method)(entity.typeDescriptor)
                battle._garage_loadout_snapshot.assert_not_called()

    def test_invalid_source_power_is_contained_without_a_default_or_zero_division(self):
        for raw in (None, 0.0, -1.0, float('nan'), float('inf')):
            with self.subTest(power=raw):
                battle, entity, unused_accepted, unused_factors = self.battle()
                entity.typeDescriptor.physics['enginePower'] = raw
                with self.assertRaisesRegex(RuntimeError, 'base engine power'):
                    battle._prepare_local_presentation(entity)
                self.assertIsNone(battle._local_factors_cache)

    def test_parameter_diagnostic_reports_actual_driver_values_without_garage_derivation(self):
        battle, entity, accepted, unused_factors = self.battle()
        battle._prepare_local_presentation(entity)
        battle._gun_state = types.SimpleNamespace(reload=3.16)
        output = io.StringIO()
        with mock.patch('sys.stdout', output):
            self.assertTrue(battle._log_effective_parameters(entity.typeDescriptor))
        line = output.getvalue()
        self.assertIn('source=accepted-player-driver', line)
        self.assertIn('hull_deg=%.2f' % math.degrees(accepted['physics']['rotSpd']), line)
        self.assertIn('recon=100.0 camo_crew=100.0', line)
        battle._garage_loadout_snapshot.assert_not_called()


if __name__ == '__main__':
    unittest.main()
