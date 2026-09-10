import copy
import unittest

import test_port_0922_battle_runtime as runtime_tests


class DeferredAmmoSettingTests(unittest.TestCase):
    def _pending_battle(self, third_shell=False):
        fixture = runtime_tests.BattleRuntimeContractTests()
        battle, gun, settings, client, record = \
            fixture._pending_fire_shell_change_battle(clip_size=4, clip=3)
        if third_shell:
            shot = copy.copy(gun.shots[0])
            shot.shell = copy.copy(shot.shell)
            shot.shell.compactDescr = 103
            gun.shots = tuple(gun.shots) + (shot,)
            gun.ammo.append(8)
        self.assertTrue(battle.shoot(0.0, 0.0))
        return battle, gun, settings, client, record

    def _resolve(self, battle, record, accepted):
        pending = dict(battle._local_fire_intent)
        if accepted:
            self.assertTrue(battle._accept_player_fire_commit({
                'shooter_kind': 'player', 'shooter_id': 1,
                'fire_intent_seq': pending['intent_seq'],
                'fire_input_seq': pending['input_seq'],
                'shot_seq': 1, 'shell_index': 0,
            }, record))
        else:
            self.assertTrue(battle.on_fire_intent_result({
                'type': 'fire_intent_result', 'round_id': 7,
                'player_id': 1, 'intent_seq': pending['intent_seq'],
                'accepted': False, 'reason': 'projectile_launch_rejected',
            }))
            battle._avatar.cancelWaitingForShot.assert_called_once_with()
        self.assertIsNone(battle._local_fire_intent)

    def _assert_reloading_selection(self, gun, client, loaded, queued,
                                    accepted, third_shell=False):
        expected_ammo = [19 if accepted else 20, 10]
        if third_shell:
            expected_ammo.append(8)
        self.assertEqual(expected_ammo, gun.ammo)
        self.assertEqual(loaded, gun.shot_index)
        self.assertEqual(queued, gun.pending_index)
        self.assertEqual(0, gun.clip)
        self.assertAlmostEqual(gun.reload, gun.reload_time)
        self.assertAlmostEqual(gun.reload, gun.reload_duration)
        self.assertFalse(gun.can_fire(True))
        inputs = [message for message in client.sent
                  if message[0] == 'input']
        self.assertTrue(inputs)
        checkpoint = inputs[-1][2]
        self.assertEqual(loaded, checkpoint['shell_index'])
        self.assertEqual(loaded if queued is None else queued,
                         checkpoint['next_shell_index'])
        self.assertEqual(queued is not None,
                         checkpoint['shell_change_pending'])
        self.assertEqual(0, checkpoint['gun_checkpoint']['clip'])
        self.assertAlmostEqual(
            gun.reload, checkpoint['gun_checkpoint']['reload_time'])

    def test_current_switch_then_next_shell_preserves_the_later_queue(self):
        for accepted in (True, False):
            with self.subTest(accepted=accepted):
                battle, gun, settings, client, record = self._pending_battle()
                # The second press requests loading shell 2 now. A later
                # press of shell 1 queues it after that new cassette.
                battle.change_vehicle_setting(settings.NEXT_SHELLS, 102)
                battle.change_vehicle_setting(settings.CURRENT_SHELLS, 102)
                battle.change_vehicle_setting(settings.NEXT_SHELLS, 101)
                self.assertEqual(0, gun.shot_index)
                self.assertEqual([20, 10], gun.ammo)

                self._resolve(battle, record, accepted)

                self._assert_reloading_selection(
                    gun, client, loaded=1, queued=0, accepted=accepted)
                battle._runtime.bigworld.now += gun.reload
                battle._ammo_tick()
                self.assertTrue(gun.can_fire(True))
                self.assertEqual(1, gun.shot_index)
                self.assertEqual(0, gun.pending_index)

    def test_partial_reload_then_next_shell_keeps_the_reload_shell(self):
        for accepted in (True, False):
            with self.subTest(accepted=accepted):
                battle, gun, settings, client, record = self._pending_battle()
                battle.change_vehicle_setting(settings.RELOAD_PARTIAL_CLIP, 0)
                battle.change_vehicle_setting(settings.NEXT_SHELLS, 102)
                self.assertEqual(0, gun.shot_index)

                self._resolve(battle, record, accepted)

                self._assert_reloading_selection(
                    gun, client, loaded=0, queued=1, accepted=accepted)

    def test_two_current_switches_keep_the_last_requested_shell(self):
        for accepted in (True, False):
            with self.subTest(accepted=accepted):
                battle, gun, settings, client, record = \
                    self._pending_battle(third_shell=True)
                for compact in (102, 103):
                    battle.change_vehicle_setting(settings.NEXT_SHELLS, compact)
                    battle.change_vehicle_setting(
                        settings.CURRENT_SHELLS, compact)
                self.assertEqual(0, gun.shot_index)

                self._resolve(battle, record, accepted)

                self._assert_reloading_selection(
                    gun, client, loaded=2, queued=None, accepted=accepted,
                    third_shell=True)


if __name__ == '__main__':
    unittest.main()
