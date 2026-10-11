"""Ordered gun settings across immediate local shots and unfinished bursts."""
import copy
import itertools
import unittest

import test_port_0922_battle_runtime as runtime_tests


class DeferredAmmoSettingTests(unittest.TestCase):
    def _launched_battle(self, third_shell=False, burst=False):
        fixture = runtime_tests.BattleRuntimeContractTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        battle, gun, settings, client, record = \
            fixture._pending_fire_shell_change_battle(clip_size=4, clip=3)
        if burst:
            battle._server_entity(10).typeDescriptor.gun.burst = (3, 0.1)
        if third_shell:
            shot = copy.copy(gun.shots[0])
            shot.shell = copy.copy(shot.shell)
            shot.shell.compactDescr = 103
            gun.shots = tuple(gun.shots) + (shot,)
            gun.ammo.append(8)
        self.assertTrue(battle.shoot(0.0, 0.0))
        self.launch = copy.deepcopy(fixture._local_launch(battle))
        return battle, gun, settings, client, record

    def _resolve(self, battle, record, accepted):
        launch = copy.deepcopy(self.launch)
        if accepted:
            self.assertTrue(battle._accept_projectile_event(launch))
            self.assertTrue(battle._accept_projectile_event(launch))
        else:
            self.assertTrue(self.fixture._reject_local_launch(battle, launch))
            self.assertFalse(self.fixture._reject_local_launch(battle, launch))
            self.assertFalse(battle._projectiles.contains(launch['manager_key']))
        self.assertIsNone(battle._local_fire_intent)

    def _complete_burst(self, battle, gun, burst):
        if not burst:
            return 1
        self.assertEqual(0, gun.shot_index)
        self.assertEqual(19, gun.ammo[0])
        self.assertEqual(2, gun.clip)
        for fired in (2, 3):
            battle._runtime.bigworld.now += 0.1
            self.assertTrue(battle._advance_local_player_burst())
            self.assertEqual(20 - fired, gun.ammo[0])
        self.assertIsNone(battle._local_player_burst)
        self.assertFalse(battle._advance_local_player_burst())
        return 3

    def _assert_reloading_selection(self, gun, client, loaded, queued,
                                    fired, third_shell=False):
        expected_ammo = [20 - fired, 10]
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
        for burst, accepted in itertools.product((False, True), repeat=2):
            with self.subTest(burst=burst, accepted=accepted):
                battle, gun, settings, client, record = self._launched_battle(burst=burst)
                # The second press requests loading shell 2 now. A later
                # press of shell 1 queues it after that new cassette.
                battle.change_vehicle_setting(settings.NEXT_SHELLS, 102)
                battle.change_vehicle_setting(settings.CURRENT_SHELLS, 102)
                battle.change_vehicle_setting(settings.NEXT_SHELLS, 101)
                fired = self._complete_burst(battle, gun, burst)
                self._assert_reloading_selection(
                    gun, client, loaded=1, queued=0, fired=fired)

                self._resolve(battle, record, accepted)

                self._assert_reloading_selection(
                    gun, client, loaded=1, queued=0, fired=fired)
                battle._runtime.bigworld.now += gun.reload
                battle._ammo_tick()
                self.assertTrue(gun.can_fire(True))
                self.assertEqual(1, gun.shot_index)
                self.assertEqual(0, gun.pending_index)

    def test_partial_reload_then_next_shell_keeps_the_reload_shell(self):
        for burst, accepted in itertools.product((False, True), repeat=2):
            with self.subTest(burst=burst, accepted=accepted):
                battle, gun, settings, client, record = self._launched_battle(burst=burst)
                battle.change_vehicle_setting(settings.RELOAD_PARTIAL_CLIP, 0)
                battle.change_vehicle_setting(settings.NEXT_SHELLS, 102)
                fired = self._complete_burst(battle, gun, burst)
                self._assert_reloading_selection(
                    gun, client, loaded=0, queued=1, fired=fired)

                self._resolve(battle, record, accepted)

                self._assert_reloading_selection(
                    gun, client, loaded=0, queued=1, fired=fired)

    def test_two_current_switches_keep_the_last_requested_shell(self):
        for burst, accepted in itertools.product((False, True), repeat=2):
            with self.subTest(burst=burst, accepted=accepted):
                battle, gun, settings, client, record = \
                    self._launched_battle(third_shell=True, burst=burst)
                for compact in (102, 103):
                    battle.change_vehicle_setting(settings.NEXT_SHELLS, compact)
                    battle.change_vehicle_setting(
                        settings.CURRENT_SHELLS, compact)
                fired = self._complete_burst(battle, gun, burst)
                self._assert_reloading_selection(
                    gun, client, loaded=2, queued=None, fired=fired,
                    third_shell=True)

                self._resolve(battle, record, accepted)

                self._assert_reloading_selection(
                    gun, client, loaded=2, queued=None, fired=fired,
                    third_shell=True)


if __name__ == '__main__':
    unittest.main()
