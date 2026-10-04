"""Conditional crew effects through native donation, wire and live mechanics."""

import copy
import types
import unittest
from unittest import mock

from test_port_0922_battle_runtime import _runtime, _Descriptor
from effective_params_fixture import effective_params
from gui.mods.offline_lan_0922 import crew_battle, loadout, critical_damage
from gui.mods.offline_lan_0922 import effective_params as wire
from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime
from gui.mods.offline_lan_0922.gun_mechanics import GunState


def donated_snapshot():
    snapshot = effective_params()
    # A combined-role tank keeps all three skills on one physical crewman.
    member = snapshot['crew']['members'][0]
    member['roles'] = ['commander', 'driver', 'gunner', 'loader']
    member['skills'] = [dict(name=name, level=100.0, active=True, enabled=True)
                        for name in ('driver_tidyperson', 'gunner_gunsmith',
                                     'loader_desperado')]
    config = types.SimpleNamespace(vehicleHealthFraction=0.1,
                                   gunReloadTimeFactor=0.909,
                                   fireStartingChanceFactor=0.75,
                                   shotDispersionFactorPerLevel=0.002)
    for key, row in snapshot['crew']['dynamic_spotting']['states'].items():
        mask, fire = [int(value) for value in key.split(':')]
        crew = types.SimpleNamespace(_skillProcessors={})
        factors = loadout._collect_battle_crew_factors(crew)
        for skill in ('loader_desperado', 'driver_tidyPerson', 'gunner_gunsmith'):
            crew._skillProcessors[skill](crew, 0, 100, 0, not mask, bool(fire), config)
        row['battle_factors'] = crew_battle.from_native(factors)
    return wire.canonical(snapshot)


class ConditionalCrewTests(unittest.TestCase):
    def scene(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        entity = types.SimpleNamespace(id=10, typeDescriptor=_Descriptor(),
                                       health=500, maxHealth=500, _crew_ko=set())
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._local_effective_params = donated_snapshot()
        self.assertIsNotNone(battle._local_effective_params)
        return battle, entity

    def test_low_hp_reload_preserves_progress_and_ends_on_loader_injury(self):
        battle, entity = self.scene()
        gun = GunState(entity.typeDescriptor)
        gun.clip = 0
        gun.reload_time = gun.reload * 0.5
        gun.reload_duration = gun.reload
        entity.health = 50
        self.assertFalse(battle._apply_current_reload_factor(gun, entity))
        entity.health = 49
        self.assertTrue(battle._apply_current_reload_factor(gun, entity))
        self.assertAlmostEqual(gun.reload * 0.909, gun.reload_duration)
        self.assertAlmostEqual(0.5, gun.reload_time / gun.reload_duration)
        entity._crew_ko.add('commander')
        self.assertTrue(battle._apply_current_reload_factor(gun, entity))
        self.assertAlmostEqual(gun.reload, gun.reload_duration)
        entity._crew_ko.clear()
        self.assertTrue(battle._apply_current_reload_factor(gun, entity))
        self.assertAlmostEqual(gun.reload * 0.909, gun.reload_duration)
        self.assertAlmostEqual(0.5, gun.reload_time / gun.reload_duration)

    def test_armorer_only_reduces_the_damaged_gun_factor(self):
        battle, entity = self.scene()
        with mock.patch.object(critical_damage, '_module_factor', return_value=2.0), \
                mock.patch.object(critical_damage, '_crew_factor', return_value=1.5):
            self.assertAlmostEqual(2.4, battle._local_stat_factor(entity, 'dispersion'))
            entity._crew_ko.add('commander')
            self.assertAlmostEqual(3.0, battle._local_stat_factor(entity, 'dispersion'))
        entity._crew_ko.clear()
        self.assertEqual(1.0, battle._local_stat_factor(entity, 'dispersion'))

    def test_worker_commits_the_same_low_hp_reload_as_the_visible_client(self):
        battle, entity = self.scene()
        entity.health = 49
        expected = battle._local_stat_factor(entity, 'reload')
        gun = GunState(entity.typeDescriptor)
        gun._effective_params = battle._local_effective_params
        gun.load_started = True
        gun.clip = 1
        gun.reload_time = 0.0
        battle._worker_mode = True
        battle._local_effective_params = None
        battle._player_authority_guns[1] = gun
        battle._player_fire_launch_pending[1] = {
            'intent_seq': 1, 'input_seq': 2, 'shot_seq': 3}
        self.assertTrue(battle._accept_player_fire_commit(
            {'shooter_kind': 'player', 'shooter_id': 1, 'fire_intent_seq': 1,
             'fire_input_seq': 2, 'shot_seq': 3, 'shell_index': 0},
            {'engine_id': 10, 'local': False}))
        self.assertAlmostEqual(gun.reload * expected, gun.reload_duration)

    def test_preventative_maintenance_follows_target_crew_on_the_worker(self):
        battle, entity = self.scene()
        snapshot = battle._local_effective_params
        battle._worker_mode = True
        battle._local_effective_params = None
        record = {'kind': 'player', 'local': False,
                  'state': {'effective_params': snapshot}}
        with mock.patch('gui.mods.offline_lan_0922.equipment_mechanics.passive_effects',
                        return_value={'fireStartingChanceFactor': 0.9}):
            battle._install_critical_equipment_effects(record, entity)
            self.assertAlmostEqual(0.675, entity._fire_starting_chance_factor)
            entity._crew_ko.add('commander')
            battle._install_critical_equipment_effects(record, entity)
            self.assertAlmostEqual(0.9, entity._fire_starting_chance_factor)

    def test_native_config_and_training_gate_are_used_without_fixed_constants(self):
        crew = types.SimpleNamespace(_skillProcessors={})
        factors = loadout._collect_battle_crew_factors(crew)
        config = types.SimpleNamespace(vehicleHealthFraction=0.2,
                                       gunReloadTimeFactor=0.8,
                                       fireStartingChanceFactor=0.6)
        for level in (0, 99):
            for name in ('loader_desperado', 'driver_tidyPerson'):
                crew._skillProcessors[name](crew, 0, level, 20, True, False, config)
            self.assertEqual(crew_battle.DEFAULTS, crew_battle.from_native(factors))
        crew._skillProcessors['loader_desperado'](crew, 0, 100, 0, True, False, config)
        self.assertEqual(0.8, crew_battle.from_native(factors)['adrenaline_reload_factor'])
        self.assertEqual(0.2, crew_battle.from_native(factors)['adrenaline_health_fraction'])

    def test_wire_rejects_partial_or_nonfinite_donation_and_copies_all_states(self):
        snapshot = donated_snapshot()
        projected = wire.canonical(snapshot)
        self.assertEqual(snapshot, projected)
        values = snapshot['crew']['dynamic_spotting']['states']['0:0']['battle_factors']
        values['engine_fire_factor'] = 0.1
        self.assertEqual(0.75, crew_battle.for_critical(projected, {})['engine_fire_factor'])
        for bad in (True, -0.1, float('nan'), float('inf'), 1.1):
            values['engine_fire_factor'] = bad
            self.assertIsNone(wire.canonical(snapshot))
        broken = copy.deepcopy(projected)
        del broken['crew']['dynamic_spotting']['states']['1:1']['battle_factors']
        self.assertIsNone(wire.canonical(broken))
        self.assertIsNotNone(wire.canonical(effective_params()))


if __name__ == '__main__':
    unittest.main()
