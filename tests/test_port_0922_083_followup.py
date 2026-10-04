"""Regression cases reported against the published 0.8.3 build."""
import copy
import gc
import sys
import types
import unittest
import weakref
from unittest import mock

import test_port_0922_battle_runtime as fixtures
from test_port_0922_battle_runtime import _runtime
from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime
from gui.mods.offline_lan_0922.entities.native_remote_vehicle import set_engine_audible
from gui.mods.offline_lan_0922.account_rpc import data, economy
from test_port_0922_garage import SNAPSHOT
import test_port_0922_server_projectiles as projectile_fixtures
from gui.mods.offline_lan_0922 import (
    stun_mechanics, descriptor_donation, effective_params, lan_client,
    bot_runtime, critical_damage)


def stun_shell():
    return dict(stunRadius=8.0, stunDuration=20.0, stunFactor=1.0,
                guaranteedStunDuration=0.5, damageDurationCoeff=0.5,
                guaranteedStunEffect=0.75, damageEffectCoeff=0.25)


def stun_config():
    result = {name: (1.5 if stat in stun_mechanics.INCREASING_STATS else 0.8)
              for stat, name in stun_mechanics.STAT_CONFIG.items()}
    result['minStunDuration'] = 5.0
    return result


class Gameplay083Tests(unittest.TestCase):
    def test_he_hp_damage_selects_explosion_without_changing_penetration(self):
        fixture = fixtures.BattleRuntimeContractTests()
        for kind, result, damage, expected in (
                ('HIGH_EXPLOSIVE', 1, 100, 'hitFx'),
                ('HIGH_EXPLOSIVE', 2, 100, 'hitFx'),
                ('HIGH_EXPLOSIVE', 1, 0, 'resistedFx'),
                ('HIGH_EXPLOSIVE', 2, 0, 'resistedFx'),
                ('ARMOR_PIERCING', 1, 0, 'resistedFx'),
                ('ARMOR_PIERCING', 0, 0, 'ricochetFx')):
            with self.subTest(kind=kind, result=result, damage=damage):
                battle, target, attacker = fixture._blocked_hit_fixture(kind)
                event = dict(kind='bot_human_hit', world_pose=True, x=.5,
                             y=1., z=0., shell_index=0, shot_result=result,
                             damage=damage, source='shot', dead=False,
                             attack_reason=0, death_reason=0)
                self.assertTrue(battle._present_combat_hit(event, target, attacker, 11))
                self.assertEqual(expected, battle._avatar.terrainEffects.addNew.call_args.args[1])
                self.assertEqual(result, event['shot_result'])

    def test_live_and_default_shop_publish_the_price_that_the_account_charges(self):
        for mode in (economy.CAREER_DEVICE_REMOVAL, economy.SANDBOX_DEVICE_REMOVAL):
            snapshot = copy.deepcopy(SNAPSHOT)
            snapshot['deviceRemovalCost'] = dict(mode)
            shop = data.shop(selected_vehicle=snapshot)
            self.assertEqual({'gold': 10}, shop['paidRemovalCost'])
            self.assertEqual(shop['paidRemovalCost'], shop['defaults']['paidRemovalCost'])
            self.assertEqual({'crystal': 200}, shop['paidDeluxeRemovalCost'])

    def test_native_stun_component_survives_all_frozen_shot_boundaries(self):
        shot = types.SimpleNamespace(
            speed=720.0, gravity=9.81, maxDistance=1000.0,
            piercingPower=(53.0, 53.0),
            shell=types.SimpleNamespace(
                type=types.SimpleNamespace(name='HIGH_EXPLOSIVE', explosionRadius=8.0),
                caliber=155.0, damage=(1000.0, 150.0),
                stun=types.SimpleNamespace(**stun_shell())))
        projected = descriptor_donation.project_shot(shot)
        for canonicalize in (
                lan_client._strict_projectile_source_shot,
                effective_params._canonical_source_shot,
                projectile_fixtures._projectile_source_shot):
            self.assertEqual(stun_shell(), canonicalize(projected)['shell']['stun'])
        projected['shell']['stun']['stunDuration'] = float('nan')
        self.assertIsNone(lan_client._strict_projectile_source_shot(projected))

    def test_zero_hp_he_can_stun_and_resistance_reduces_duration(self):
        shell = dict(kind='HIGH_EXPLOSIVE', damage=[1000, 150], stun=stun_shell())
        duration, factors = stun_mechanics.impact(shell, 0, 8.0, stun_config())
        self.assertEqual(10.0, duration)
        self.assertLess(factors['vision'], 1.0)
        self.assertGreater(factors['reload'], 1.0)
        protected = stun_mechanics.impact(
            shell, 0, 1.0, stun_config(), {'stunResistanceDuration': 0.1})
        self.assertEqual(9.0, protected[0])
        self.assertIsNone(stun_mechanics.impact(shell, 1000, 8.01, stun_config()))
        self.assertIsNone(stun_mechanics.impact(
            dict(shell, kind='ARMOR_PIERCING'), 1000, 0, stun_config()))

    def test_worker_generates_stun_and_server_expires_its_stat_effects(self):
        state = projectile_fixtures._state()
        launch = projectile_fixtures._launch(is_he=True, splash_radius=8.0)
        launch['source_shot']['shell']['stun'] = stun_shell()
        self.assertTrue(projectile_fixtures._launch_authority(state, launch))
        now = state._server_time_ms()
        battle = BattleRuntime(_runtime())
        battle._config = {}
        battle._turret_server_time_ms = lambda unused: now
        effect = projectile_fixtures._effect(damage=0, shot_result=1)
        target = types.SimpleNamespace(typeDescriptor={'miscAttrs': {}})
        native = types.ModuleType('items.stun')
        native.g_cfg = stun_config()
        with mock.patch.dict(sys.modules, {'items.stun': native}):
            self.assertTrue(battle._projectile_stun_effect(
                effect, launch['source_shot'], {}, target, {'cursor_time': 1.0}, 0.0))
        self.assertIsNotNone(lan_client._strict_projectile_effect(effect))
        self.assertTrue(state.resolve_projectile(
            projectile_fixtures.SIMULATION_WORKER_AUTHORITY_ID,
            projectile_fixtures._resolve('1:p:1:1', direct=effect)))
        victim = state.players[2]
        self.assertEqual(effect['stun_factors'], victim.stun_factors)
        self.assertEqual(effect['stun_factors'], state._public_player(victim)['stun_factors'])
        self.assertEqual(1, state._expire_stuns(effect['stun_end_server_time_ms']))
        self.assertEqual({}, victim.stun_factors)

    def test_elapsed_stun_does_not_discard_late_valid_hp_damage(self):
        state = projectile_fixtures._state()
        self.assertTrue(projectile_fixtures._launch_authority(state, projectile_fixtures._launch()))
        victim = state.players[2]
        health = victim.health
        self.assertTrue(state.resolve_projectile(
            projectile_fixtures.SIMULATION_WORKER_AUTHORITY_ID,
            projectile_fixtures._resolve('1:p:1:1', direct=projectile_fixtures._effect(
                stun_end_server_time_ms=1))))
        self.assertEqual(health - 100, victim.health)
        self.assertEqual(0, victim.stun_end_server_time_ms)

    def test_stun_factors_survive_bot_combat_restore_and_affect_player_stats(self):
        factors = stun_mechanics.impact(
            dict(kind='HIGH_EXPLOSIVE', damage=[1000, 150], stun=stun_shell()),
            0, 0, stun_config())[1]
        state = dict(health=100, max_health=100, alive=True,
                     stun_end_server_time_ms=5000, stun_factors=factors)
        record = bot_runtime._combat_record(state)
        restored = dict(max_health=100)
        bot_runtime._apply_combat_record(restored, record)
        self.assertEqual(factors['reload'], stun_mechanics.factor(restored, 'reload'))
        vehicle = types.SimpleNamespace(_offlineStunFactors=factors)
        with mock.patch.object(critical_damage, '_crew_factor', return_value=1.0), \
                mock.patch.object(critical_damage, '_module_factor', return_value=1.0):
            self.assertEqual(factors['reload'], critical_damage.stat_factor(vehicle, 'reload'))
        record['stun_end_server_time_ms'] = 0
        bot_runtime._apply_combat_record(restored, record)
        self.assertEqual({}, restored['stun_factors'])
        self.assertEqual(1.0, stun_mechanics.factor(restored, 'reload'))


class _EngineAudition:
    def __init__(self):
        self.ownership = 'new'
        self.attachToModel = mock.Mock(side_effect=self._attach)
        self.setIsUnderwaterInfo = mock.Mock()
        self.setIsInWaterInfo = mock.Mock()
        self.setWeaponEnergy = mock.Mock()

    def _attach(self, model):
        if self.ownership == 'retired':
            raise ReferenceError('removed native component')

    def onEngineStart(self):
        if self.ownership != 'installed':
            raise ReferenceError('engine callback outlived its component')

    def onEngineStateChanged(self):
        self.onEngineStart()


class _EngineAppearance(types.SimpleNamespace):
    """Model the one-time ownership transfer missing from the old fake."""
    _audition = None

    @property
    def engineAudition(self):
        return self._audition

    @engineAudition.setter
    def engineAudition(self, value):
        if self._audition is not None:
            # Removal destroys the native component, regardless of Python
            # references to its wrapper. Clear its callback owners first.
            assert self.detailedEngineState.onEngineStart is None
            assert self.detailedEngineState.onStateChanged is None
            self._audition.ownership = 'retired'
        if value is not None:
            assert value.ownership == 'new', 'This wrapper own nothing'
            value.ownership = 'installed'
        self._audition = value


class EngineAudioOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.detailed = types.SimpleNamespace(
            onEngineStart=None, onStateChanged=None, vehicleSpeedLink=object())
        self.appearance = _EngineAppearance(
            detailedEngineState=self.detailed, compoundModel=object(),
            waterSensor=object(), _CompoundAppearance__weaponEnergy=723.5)
        self.vehicle = types.SimpleNamespace(
            appearance=self.appearance, isAlive=lambda: True,
            inWorld=True, isStarted=True)
        self.original = _EngineAudition()
        self.appearance.engineAudition = self.original
        self._subscribe(self.original, self.detailed)
        self.created = []
        assembler = types.ModuleType('vehicle_systems.model_assembler')
        assembler.assembleVehicleAudition = mock.Mock(side_effect=self._assemble)
        assembler.subscribeEngineAuditionToEngineState = self._subscribe
        self.assembler = assembler
        package = types.ModuleType('vehicle_systems')
        package.model_assembler = assembler
        self.links = types.ModuleType('DataLinks')
        self.links.createBoolLink = mock.Mock(side_effect=lambda owner, field:
                                             (owner, field))
        patch = mock.patch.dict(sys.modules, {
            'vehicle_systems': package,
            'vehicle_systems.model_assembler': assembler, 'DataLinks': self.links})
        patch.start()
        self.addCleanup(patch.stop)

    def _assemble(self, is_player, appearance):
        self.assertFalse(is_player)
        audition = _EngineAudition()
        self.created.append(audition)
        appearance.engineAudition = audition

    @staticmethod
    def _subscribe(audition, detailed):
        detailed.onEngineStart = audition.onEngineStart
        detailed.onStateChanged = audition.onEngineStateChanged

    def test_repeated_hide_reveal_uses_fresh_native_owners(self):
        speed_link = self.detailed.vehicleSpeedLink
        for index in range(3):
            old = self.appearance.engineAudition
            self.assertTrue(set_engine_audible(self.vehicle, False))
            self.assertEqual('retired', old.ownership)
            self.assertIsNone(self.appearance.engineAudition)
            self.assertIsNone(self.detailed.onEngineStart)
            self.assertIsNone(self.detailed.onStateChanged)
            self.assertTrue(set_engine_audible(self.vehicle, False))
            self.assertTrue(set_engine_audible(self.vehicle, True))
            new = self.appearance.engineAudition
            self.assertIsNot(old, new)
            self.assertEqual(index + 1, len(self.created))
            self.assertFalse(set_engine_audible(self.vehicle, True))
            self.assertIs(speed_link, self.detailed.vehicleSpeedLink)
            self.assertIs(new, self.detailed.onEngineStart.__self__)
            self.assertIs(new, self.detailed.onStateChanged.__self__)
            self.detailed.onEngineStart()
            new.attachToModel.assert_called_once_with(self.appearance.compoundModel)
            new.setIsUnderwaterInfo.assert_called_once_with(
                (self.appearance.waterSensor, 'isUnderWater'))
            new.setIsInWaterInfo.assert_called_once_with(
                (self.appearance.waterSensor, 'isInWater'))
            new.setWeaponEnergy.assert_called_once_with(723.5)

    def test_mute_does_not_retain_the_retired_wrapper_or_bound_callbacks(self):
        reference = weakref.ref(self.original)
        set_engine_audible(self.vehicle, False)
        del self.original
        gc.collect()
        self.assertIsNone(reference())

    def test_death_does_not_resurrect_engine_sound(self):
        set_engine_audible(self.vehicle, False)
        self.vehicle.isAlive = lambda: False
        self.assertFalse(set_engine_audible(self.vehicle, True))
        self.assertIsNone(self.appearance.engineAudition)
        self.assembler.assembleVehicleAudition.assert_not_called()

    def test_world_exit_does_not_resurrect_engine_sound(self):
        set_engine_audible(self.vehicle, False)
        self.vehicle.inWorld = False
        self.assertFalse(set_engine_audible(self.vehicle, True))
        self.assertIsNone(self.appearance.engineAudition)
        self.assembler.assembleVehicleAudition.assert_not_called()

    def test_model_replacement_drops_the_old_generation_request(self):
        set_engine_audible(self.vehicle, False)
        self.appearance.compoundModel = object()
        self.appearance.detailedEngineState = types.SimpleNamespace(
            onEngineStart=None, onStateChanged=None)
        self.assertFalse(set_engine_audible(self.vehicle, True))
        self.assembler.assembleVehicleAudition.assert_not_called()

    def test_stock_replacement_while_hidden_is_preserved(self):
        set_engine_audible(self.vehicle, False)
        replacement = _EngineAudition()
        self.appearance.engineAudition = replacement
        self._subscribe(replacement, self.detailed)
        self.assertFalse(set_engine_audible(self.vehicle, True))
        self.assertIs(replacement, self.appearance.engineAudition)
        self.assertIs(replacement, self.detailed.onEngineStart.__self__)
        self.assembler.assembleVehicleAudition.assert_not_called()

    def test_reveal_waits_for_start_visual(self):
        set_engine_audible(self.vehicle, False)
        self.vehicle.isStarted = False
        self.assertFalse(set_engine_audible(self.vehicle, True))
        self.assembler.assembleVehicleAudition.assert_not_called()
        self.vehicle.isStarted = True
        self.assertTrue(set_engine_audible(self.vehicle, True))

    def test_failed_binding_retires_partial_owner_and_next_reveal_can_retry(self):
        set_engine_audible(self.vehicle, False)

        def fail_binding(is_player, appearance):
            self._assemble(is_player, appearance)
            appearance.engineAudition.attachToModel.side_effect = ValueError('model link')

        self.assembler.assembleVehicleAudition.side_effect = fail_binding
        self.assertFalse(set_engine_audible(self.vehicle, True))
        self.assertEqual('retired', self.created[-1].ownership)
        self.assertIsNone(self.appearance.engineAudition)
        self.assertIsNone(self.detailed.onEngineStart)
        self.assembler.assembleVehicleAudition.side_effect = self._assemble
        self.assertTrue(set_engine_audible(self.vehicle, True))
        self.assertEqual(2, len(self.created))

    def test_native_assembly_reentry_cannot_create_another_owner(self):
        set_engine_audible(self.vehicle, False)

        def reenter(is_player, appearance):
            self.assertFalse(set_engine_audible(self.vehicle, True))
            self._assemble(is_player, appearance)

        self.assembler.assembleVehicleAudition.side_effect = reenter
        self.assertTrue(set_engine_audible(self.vehicle, True))
        self.assertEqual(1, len(self.created))


if __name__ == '__main__':
    unittest.main()
