"""HE presentation follows hit damage and the fired round, not vehicle class."""

import copy
import unittest

from test_port_0922_battle_runtime import (
    BattleRuntime, _Descriptor, _Vehicle, _Vector, _runtime)


class HEFeedbackTests(unittest.TestCase):
    def _fixture(self, vehicle_class='heavyTank'):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        battle._synchronise_player_identity(10)
        descriptor = _Descriptor()
        descriptor.type.tags = (vehicle_class,)
        he_shot = copy.deepcopy(descriptor.gun.shots[0])
        he_shot.shell.kind = 'HIGH_EXPLOSIVE'
        he_shot.shell.effectsIndex = 4
        he_shot.shell.explosionRadius = 3.0
        descriptor.gun.shots.append(he_shot)
        # The user can already have selected AP when the HE reaches its target.
        descriptor.activeGunShotIndex = 0
        runtime.vehicles.g_cache.shotEffects[4] = {
            'armorHit': ('heHitStages', 'heExplosionFx', None),
            'armorResisted': ('heResistedStages', 'heResistedFx', None),
            'armorSplashHit': ('heSplashStages', 'heSplashFx', None),
        }
        shooter = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                           {'health': 500})
        victim = _Vehicle(11, _Descriptor(), _Vector(0, 0, 10), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities.update({10: shooter, 11: victim})
        attacker = {'engine_id': 10, 'local': True, 'kind': 'player',
                    'network_id': 1, 'state': {'team': 1, 'health': 500}}
        target = {'engine_id': 11, 'local': False, 'kind': 'bot',
                  'network_id': 2, 'spot_visible': True,
                  'spot_marker_visible': True,
                  'state': {'team': 2, 'health': 500, 'max_health': 500,
                            'x': 0.0, 'y': 0.0, 'z': 10.0}}
        event = {'kind': 'bot_hit', 'source': 'shot', 'world_pose': True,
                 'x': 0.0, 'y': 1.0, 'z': 9.0, 'shell_index': 1,
                 'damage': 150, 'shot_result': 1, 'splash': False,
                 'dead': False, 'attack_reason': 0, 'death_reason': 0}
        return runtime, battle, attacker, target, event

    def test_all_vehicle_classes_use_hp_for_he_direct_effect_and_voice(self):
        for vehicle_class in ('lightTank', 'mediumTank', 'heavyTank',
                              'AT-SPG', 'SPG'):
            for damage in (0, 150):
                for result in (0, 1, 2):
                    with self.subTest(vehicle_class=vehicle_class,
                                      damage=damage, result=result):
                        runtime, battle, attacker, target, event = (
                            self._fixture(vehicle_class))
                        event.update(damage=damage, shot_result=result)
                        original = copy.deepcopy(event)
                        battle._present_combat_hit(event, target, attacker, 10)
                        battle._present_combat_feedback(event, target, attacker)
                        effect = battle._avatar.terrainEffects.addNew.call_args
                        self.assertEqual('heExplosionFx' if damage else
                                         'heResistedFx', effect.args[1])
                        self.assertEqual(10, effect.kwargs['attackerID'])
                        self.assertEqual(11, effect.kwargs['entity_id'])
                        self.assertFalse(effect.kwargs['isPlayerVehicle'])
                        self.assertEqual(damage / 5.0,
                                         effect.kwargs['damageFactor'])
                        flags = battle._avatar.shot_results[0][0] >> 32
                        vhf = runtime.constants.VEHICLE_HIT_FLAGS
                        expected = (vhf.ATTACK_IS_DIRECT_PROJECTILE |
                            ((vhf.MATERIAL_WITH_POSITIVE_DF_PIERCED_BY_PROJECTILE
                              if result == 2 else
                              vhf.MATERIAL_WITH_POSITIVE_DF_PIERCED_BY_EXPLOSION)
                             if damage else
                             vhf.MATERIAL_WITH_POSITIVE_DF_NOT_PIERCED_BY_PROJECTILE))
                        self.assertEqual(expected, flags)
                        # Presentation must never turn a splash-damage hit
                        # into a real penetration in the event/statistics.
                        self.assertEqual(original, event)

    def test_he_splash_retains_near_explosion_voice_and_hull_sound(self):
        runtime, battle, attacker, target, event = self._fixture()
        event['splash'] = True
        battle._present_combat_hit(event, target, attacker, 10)
        battle._present_combat_feedback(event, target, attacker)
        battle._avatar.terrainEffects.addNew.assert_not_called()
        victim = runtime.bigworld.entities[11]
        self.assertEqual([('hull', 'heSplashFx', 30.0)],
                         victim.bound_effects.played)
        vhf = runtime.constants.VEHICLE_HIT_FLAGS
        self.assertEqual(vhf.ATTACK_IS_EXTERNAL_EXPLOSION |
                         vhf.MATERIAL_WITH_POSITIVE_DF_PIERCED_BY_EXPLOSION,
                         battle._avatar.shot_results[0][0] >> 32)

    def test_he_critical_feedback_keeps_ribbons_without_replacing_plain_voice(self):
        for damage in (0, 150):
            runtime, battle, attacker, target, event = self._fixture()
            event['damage'] = damage
            event['critical'] = {'events': [
                {'kind': 'device', 'name': 'leftTrackHealth',
                 'old_state': 'normal', 'state': 'destroyed', 'cause': 'shot'},
                {'kind': 'device', 'name': 'gunHealth',
                 'old_state': 'normal', 'state': 'critical', 'cause': 'shot'}]}
            original = copy.deepcopy(event)
            battle._present_combat_feedback(event, target, attacker)
            flags = battle._avatar.shot_results[0][0] >> 32
            vhf = runtime.constants.VEHICLE_HIT_FLAGS
            self.assertFalse(flags & (vhf.GUN_DAMAGED_BY_PROJECTILE |
                                      vhf.CHASSIS_DAMAGED_BY_PROJECTILE |
                                      vhf.GUN_DAMAGED_BY_EXPLOSION |
                                      vhf.CHASSIS_DAMAGED_BY_EXPLOSION))
            criticals = [item for batch in battle._avatar.battle_events
                         for item in batch if item['eventType'] == 6]
            self.assertEqual(1, len(criticals))
            self.assertEqual(2, criticals[0]['details'] >> 16)
            self.assertEqual(original, event)

    def test_spg_ap_uses_physical_result_even_after_switching_to_he(self):
        runtime, battle, attacker, target, event = self._fixture('SPG')
        runtime.bigworld.entities[10].typeDescriptor.activeGunShotIndex = 1
        event.update(shell_index=0, damage=0, shot_result=2)
        battle._present_combat_hit(event, target, attacker, 10)
        battle._present_combat_feedback(event, target, attacker)
        self.assertEqual('hitFx',
            battle._avatar.terrainEffects.addNew.call_args.args[1])
        vhf = runtime.constants.VEHICLE_HIT_FLAGS
        self.assertEqual(vhf.ATTACK_IS_DIRECT_PROJECTILE |
                         vhf.MATERIAL_WITH_POSITIVE_DF_PIERCED_BY_PROJECTILE,
                         battle._avatar.shot_results[0][0] >> 32)

    def test_he_blind_hit_does_not_disclose_damage_by_voice_or_ribbon(self):
        runtime, battle, attacker, target, event = self._fixture()
        target.update(spot_visible=False, spot_marker_visible=False)
        self.assertFalse(battle._present_combat_feedback(event, target, attacker))
        self.assertFalse(battle._present_combat_hit(event, target, attacker, 10))
        self.assertEqual([], battle._avatar.shot_results)
        self.assertEqual([], battle._avatar.battle_events)
        battle._avatar.terrainEffects.addNew.assert_not_called()


if __name__ == '__main__':
    unittest.main()
