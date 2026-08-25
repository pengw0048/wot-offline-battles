from pathlib import Path
import sys
import unittest
from unittest import mock
import types


ROOT = Path(__file__).resolve().parents[2]
CLIENT_ROOT = ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_ROOT))

from effective_params_fixture import effective_params
from gui.mods.offline_lan_0922 import effective_params as contract
from gui.mods.offline_lan_0922 import lan_session
from gui.mods.offline_lan_0922 import loadout
from gui.mods.offline_lan_0922.lan_client import (
    CLIENT_CAPABILITIES, EFFECTIVE_PARAMS_CAPABILITY, LANClient)


class EffectiveParamsContractTests(unittest.TestCase):
    def test_dynamic_spotting_ratios_use_native_factor_pairs(self):
        healthy = {
            'circularVisionRadius': 1.2,
            'radio/distance': 0.8,
            'camouflage': 0.6,
        }
        injured = {
            'circularVisionRadius': 0.9,
            'radio/distance': 0.4,
            'camouflage': 0.3,
        }

        self.assertEqual(
            {'vision': 0.75, 'signal': 0.5, 'camouflage': 0.5},
            loadout.dynamic_spotting_ratios(healthy, injured))

    def test_garage_builder_uses_exact_client_final_value_providers(self):
        expected = effective_params()
        descriptor = types.SimpleNamespace()
        descriptor.gun = types.SimpleNamespace(
            invisibilityFactorAtShot=0.4, clip=(2, 1.0), shots=tuple(
                types.SimpleNamespace(
                    speed=entry['source_shot']['speed'],
                    gravity=entry['source_shot']['gravity'],
                    maxDistance=entry['source_shot']['maxDistance'],
                    piercingPower=entry['source_shot']['piercingPower'],
                    shell=types.SimpleNamespace(
                        compactDescr=entry['compact_descr'],
                        **entry['source_shot']['shell']))
                for entry in expected['gun']['shots']))
        descriptor.computeBaseInvisibility = mock.Mock(
            return_value=(0.2, 0.3))
        descriptor.engine = {
            'maxHealth': 100.0, 'maxRegenHealth': 50.0}
        descriptor.miscAttrs = {}
        descriptor.type = types.SimpleNamespace(
            crewRoles=(('commander',),))
        crew = [types.SimpleNamespace(skills=[types.SimpleNamespace(
            name='gunner_sniper', isActive=True, level=100.0)])]
        equipment_descriptor = types.SimpleNamespace(
            name='ration', id=(0, 9), compactDescr=1009,
            reuseCount=-1, cooldownSeconds=0.0,
            crewLevelIncrease=10.0)
        consumables = types.SimpleNamespace(
            getInstalledItems=lambda: (
                types.SimpleNamespace(intCD=1009),))
        item = types.SimpleNamespace(
            descriptor=descriptor,
            crew=crew,
            equipment=types.SimpleNamespace(regularConsumables=consumables),
            shells=(types.SimpleNamespace(intCD=2, count=10),
                    types.SimpleNamespace(intCD=1, count=20)),
            getBonusCamo=lambda: types.SimpleNamespace(id=7))
        current_vehicle = types.ModuleType('CurrentVehicle')
        current_vehicle.g_currentVehicle = types.SimpleNamespace(item=item)
        factors = {'exact': True}

        items_module = types.ModuleType('items')
        items_module.vehicles = types.SimpleNamespace(
            getItemByCompactDescr=mock.Mock(
                return_value=equipment_descriptor))

        with mock.patch.dict(sys.modules, {
                'CurrentVehicle': current_vehicle, 'items': items_module}), \
                mock.patch(
                    'gui.mods.offline_lan_0922.loadout.attribute_factors',
                    return_value=factors) as attribute_factors, \
                mock.patch(
                    'gui.mods.offline_lan_0922.loadout.'
                    'dynamic_spotting_ratios',
                    return_value={
                        'vision': 1.0, 'signal': 1.0,
                        'camouflage': 1.0}), \
                mock.patch(
                    'gui.mods.offline_lan_0922.loadout.invisibility_pair',
                    return_value=(0.0, 1.0)), \
                mock.patch(
                    'gui.mods.offline_lan_0922.loadout.crew_skill_names',
                    return_value=(('gunner_sniper',),)), \
                mock.patch(
                    'gui.mods.offline_lan_0922.loadout.modifiers',
                    return_value=expected['loadout']), \
                mock.patch(
                    'gui.mods.offline_lan_0922.loadout.spotting_profile',
                    return_value=expected['spotting']), \
                mock.patch(
                    'gui.mods.offline_lan_0922.loadout.crew_level_increase',
                    return_value=0.0), \
                mock.patch(
                    'gui.mods.offline_lan_0922.loadout.ramming_bonus',
                    return_value=0.0), \
                mock.patch(
                    'gui.mods.offline_lan_0922.loadout.intuition_chances',
                    return_value=1), \
                mock.patch(
                    'gui.mods.offline_lan_0922.vehicle_physics.derive_params',
                    return_value=expected['physics']) as derive_params, \
                mock.patch(
                    'gui.mods.offline_lan_0922.tank_collision.'
                    'descriptor_ram_profile',
                    return_value=expected['ramming']):
            result = lan_session._selected_vehicle_effective_params()

        self.assertEqual(5, attribute_factors.call_count)
        self.assertTrue(all(
            call.args[0] is descriptor
            for call in attribute_factors.call_args_list))
        dynamic_calls = attribute_factors.call_args_list[1:]
        self.assertEqual(
            [(True,), (True,), (False,), (False,)],
            [tuple(call.kwargs['activity_flags'])
             for call in dynamic_calls])
        self.assertEqual(
            [False, True, False, True],
            [call.kwargs['is_fire'] for call in dynamic_calls])
        derive_params.assert_called_once_with(descriptor, factors)
        self.assertEqual(5, descriptor.computeBaseInvisibility.call_count)
        descriptor.computeBaseInvisibility.assert_any_call(0.57, 7)
        self.assertEqual([[1, 20], [2, 10]], result['ammo'])
        self.assertEqual(0.2, result['camouflage']['base_moving'])
        self.assertEqual(0.4, result['camouflage']['shot_factor'])
        self.assertTrue(result['skills']['deadeye'])
        self.assertEqual(1, result['skills']['intuition_chances'])
        self.assertEqual(2, result['gun']['clip_size'])
        self.assertEqual([1, 2], [
            shot['compact_descr'] for shot in result['gun']['shots']])
        self.assertTrue(all(
            shot['source_shot']['deadeye']
            for shot in result['gun']['shots']))
        self.assertEqual(['commander'],
                         result['crew']['dynamic_spotting']['crew'])
        self.assertEqual(
            {'0:0', '0:1', '1:0', '1:1'},
            set(result['crew']['dynamic_spotting']['states']))

    def test_complete_snapshot_is_canonical_and_detached(self):
        source = effective_params()
        source['physics']['terrainResist'] = (1.1, 1.4, 2.6)

        result = contract.canonical(source)

        self.assertIsNotNone(result)
        self.assertEqual([1.1, 1.4, 2.6],
                         result['physics']['terrainResist'])
        source['loadout']['reload_factor'] = 99.0
        self.assertEqual(0.96, result['loadout']['reload_factor'])

    def test_snapshot_preserves_large_finite_module_values(self):
        source = effective_params()
        source['critical']['devices'][0].update({
            'max_hp': 500000000.0,
            'regen_hp': 250000000.0,
        })
        source['gun']['shots'][0]['source_shot']['shell']['damage'][1] = \
            750000000.0

        result = contract.canonical(source)

        self.assertIsNotNone(result)
        self.assertEqual(
            500000000.0, result['critical']['devices'][0]['max_hp'])
        self.assertEqual(
            750000000.0,
            result['gun']['shots'][0]['source_shot']['shell']['damage'][1])

        over_limit = effective_params()
        over_limit['critical']['devices'][0]['max_hp'] = \
            contract.MAX_CRITICAL_DEVICE_HP + 1.0
        self.assertIsNone(contract.canonical(over_limit))

    def test_snapshot_preserves_complete_he_shell_factors(self):
        source = effective_params()
        shell = source['gun']['shots'][0]['source_shot']['shell']
        shell.update({
            'kind': 'HIGH_EXPLOSIVE',
            'explosionRadius': 2.5,
            'explosionDamageFactor': 0.55,
            'explosionDamageAbsorptionFactor': 1.4,
            'explosionEdgeDamageFactor': 0.2,
        })

        result = contract.canonical(source)

        self.assertIsNotNone(result)
        self.assertEqual(
            0.55,
            result['gun']['shots'][0]['source_shot']['shell'][
                'explosionDamageFactor'])
        self.assertEqual(
            1.4,
            result['gun']['shots'][0]['source_shot']['shell'][
                'explosionDamageAbsorptionFactor'])
        self.assertEqual(
            0.2,
            result['gun']['shots'][0]['source_shot']['shell'][
                'explosionEdgeDamageFactor'])

    def test_snapshot_rejects_partial_or_invalid_he_shell_factors(self):
        partial = effective_params()
        partial_shell = partial['gun']['shots'][0]['source_shot']['shell']
        partial_shell['explosionDamageFactor'] = 0.55
        self.assertIsNone(contract.canonical(partial))

        invalid = effective_params()
        invalid_shell = invalid['gun']['shots'][0]['source_shot']['shell']
        invalid_shell.update({
            'kind': 'HIGH_EXPLOSIVE',
            'explosionDamageFactor': 0.55,
            'explosionDamageAbsorptionFactor': 1.4,
            'explosionEdgeDamageFactor': 1.1,
        })
        self.assertIsNone(contract.canonical(invalid))

    def test_schema_rejects_omission_non_finite_and_duplicate_ammo(self):
        missing = effective_params()
        del missing['skills']
        self.assertIsNone(contract.canonical(missing))

        non_finite = effective_params()
        non_finite['physics']['powerW'] = float('nan')
        self.assertIsNone(contract.canonical(non_finite))

        duplicate = effective_params()
        duplicate['ammo'] = [[1, 20], [1, 10]]
        self.assertIsNone(contract.canonical(duplicate))

        fallback_loadout = effective_params()
        fallback_loadout['loadout']['from_client_factors'] = False
        self.assertIsNone(contract.canonical(fallback_loadout))

        fallback_spotting = effective_params()
        fallback_spotting['spotting']['from_client_factors'] = False
        self.assertIsNone(contract.canonical(fallback_spotting))

        mismatched_deadeye = effective_params()
        mismatched_deadeye['gun']['shots'][0][
            'source_shot']['deadeye'] = True
        self.assertIsNone(contract.canonical(mismatched_deadeye))

        duplicate_shot = effective_params()
        duplicate_shot['gun']['shots'][1]['compact_descr'] = 1
        self.assertIsNone(contract.canonical(duplicate_shot))

        incomplete_crew_projection = effective_params()
        del incomplete_crew_projection['crew'][
            'dynamic_spotting']['states']['1:1']
        self.assertIsNone(contract.canonical(incomplete_crew_projection))

        mismatched_crew_roster = effective_params()
        mismatched_crew_roster['critical']['crew_roster'] = ['driver']
        self.assertIsNone(contract.canonical(mismatched_crew_roster))

    def test_critical_and_equipment_projection_rejects_forged_identity(self):
        unknown = effective_params()
        unknown['critical']['devices'][0]['name'] = 'inventedHealth'
        self.assertIsNone(contract.canonical(unknown))

        changed_pool = effective_params()
        changed_pool['critical']['devices'][0]['regen_hp'] = 101.0
        self.assertIsNone(contract.canonical(changed_pool))

        duplicate_pool = effective_params()
        duplicate_pool['critical']['devices'].append(dict(
            duplicate_pool['critical']['devices'][0]))
        self.assertIsNone(contract.canonical(duplicate_pool))

        equipment = {
            'name': 'smallRepairkit', 'kind': 'repairkit',
            'id': 41, 'compactDescr': 441, 'tags': ['repairkit'],
            'reuseCount': 0, 'cooldownSeconds': 90.0,
            'autoactivate': False, 'fireStartingChanceFactor': 1.0,
            'repairAll': False, 'bonusValue': 0.0,
            'crewLevelIncrease': 0.0, 'enginePowerFactor': 1.0,
            'turretRotationSpeedFactor': 1.0,
            'engineHpLossPerSecond': 0.0,
            'autoReactionSeconds': 0.0,
        }
        duplicate_equipment_id = effective_params()
        duplicate_equipment_id['equipment'] = [
            equipment, dict(equipment, compactDescr=442)]
        self.assertIsNone(contract.canonical(duplicate_equipment_id))

    def test_player_hello_requires_and_publishes_snapshot(self):
        client = LANClient(
            '127.0.0.1', 28782, 'Player', 'ussr:R11_MS-1',
            max_health=90, account_key='account', outfits={},
            vehicle_compact_descr='dGVzdA==',
            effective_params=effective_params(),
            ammo_remaining=[51], ammo_loaded_shell=0,
            player_authority_loadout={
                'repair': {'available': False},
                'spotting': {'available': False},
            })

        hello = client._hello_payload()

        self.assertIn(EFFECTIVE_PARAMS_CAPABILITY, CLIENT_CAPABILITIES)
        self.assertEqual(
            effective_params()['loadout']['reload_factor'],
            hello['effective_params']['loadout']['reload_factor'])
        invalid = LANClient(
            '127.0.0.1', 28782, 'Player', 'ussr:R11_MS-1',
            max_health=90, account_key='account', outfits={},
            vehicle_compact_descr='dGVzdA==')
        with self.assertRaises(ValueError):
            invalid._hello_payload()

    def test_lean_snapshot_inherits_canonical_static_parameters(self):
        client = LANClient(
            '127.0.0.1', 28782, 'Player', 'ussr:R11_MS-1',
            effective_params=effective_params())
        full = client._remember_player_outfits([{
            'id': 1, 'outfits': {},
            'effective_params': effective_params(),
        }])
        lean = client._remember_player_outfits([{'id': 1}])

        self.assertEqual(full[0]['effective_params'],
                         lean[0]['effective_params'])
        self.assertIsNot(full[0]['effective_params'],
                         lean[0]['effective_params'])


if __name__ == '__main__':
    unittest.main()
