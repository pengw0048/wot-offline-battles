"""One local human click owns each physical round in a descriptor burst."""
import unittest
from unittest import mock

import test_port_0922_battle_runtime as f
from gui.mods.offline_lan_0922 import gun_mechanics


class PlayerBurstTests(unittest.TestCase):
    def setUp(self):
        self.fixture = f.BattleRuntimeContractTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.battle, unused_gun, unused_settings, unused_client, self.record = (
            self.fixture._pending_fire_shell_change_battle(clip_size=6))
        battle = self.battle
        self.now = battle._clock()
        battle._clock = lambda: self.now
        self.entity = battle._server_entity(10)
        descriptor = self.entity.typeDescriptor
        descriptor.gun.burst = (3, 0.1)
        descriptor.gun.clip = (6, 0.5)
        descriptor.gun.shotDispersionFactors['afterShotInBurst'] = 1.25
        battle._gun_state = gun_mechanics.GunState(
            descriptor, ammo_layout={101: 20, 102: 10})
        battle._gun_state.reload_time = 0.0
        battle._gun_state.clip = 6
        battle._publish_ammo_state = mock.Mock()
        battle._publish_reload_event = mock.Mock()

    def _launches(self):
        return sorted(self.battle._projectile_meta.values(),
                      key=lambda row: row['shot_seq'])

    def test_click_then_release_commits_three_rounds_without_launch_echoes(self):
        gun = self.battle._gun_state
        ammo = sum(gun.ammo)
        self.assertTrue(self.battle.shoot(0, 0))
        self.assertEqual(ammo - 1, sum(gun.ammo))
        self.assertFalse(self.battle.shoot(0, 0))
        self.assertFalse(self.battle._advance_local_player_burst())
        self.assertIsNone(self.battle._local_fire_intent)
        self.now += 0.099
        self.assertFalse(self.battle._advance_local_player_burst())
        self.now += 0.001
        self.assertTrue(self.battle._advance_local_player_burst())
        self.assertEqual(ammo - 2, sum(gun.ammo))
        self.now += 0.1
        self.assertTrue(self.battle._advance_local_player_burst())
        self.assertEqual(ammo - 3, sum(gun.ammo))
        launches = self._launches()
        self.assertEqual([0, 1, 2], [row['burst_index'] for row in launches])
        self.assertEqual([3, 3, 3], [row['burst_count'] for row in launches])
        for index, row in enumerate(launches):
            self.assertAlmostEqual(index * 0.1,
                row['local_launch_time'] - launches[0]['local_launch_time'])
            self.assertLessEqual(abs(index * 100 - (
                row['launch_server_time_ms'] -
                launches[0]['launch_server_time_ms'])), 1)
        self.assertEqual(3, gun.clip)
        self.assertAlmostEqual(0.5, gun.reload_time)
        self.assertIsNone(self.battle._local_player_burst)
        self.assertFalse(self.battle._advance_local_player_burst())

    def test_partial_clip_and_delayed_update_never_create_extra_shells(self):
        self.battle._gun_state.clip = 2
        self.assertTrue(self.battle.shoot(0, 0))
        self.now += 1.0
        self.assertTrue(self.battle._advance_local_player_burst())
        self.assertFalse(self.battle._advance_local_player_burst())
        self.assertEqual(2, len(self._launches()))
        self.assertEqual([18, 10], self.battle._gun_state.ammo)
        self.assertEqual(0, self.battle._gun_state.clip)
        self.assertIsNone(self.battle._local_player_burst)

    def test_destroyed_gun_cancels_unlaunched_tail_without_ammo_debit(self):
        self.battle.shoot(0, 0)
        ammo = list(self.battle._gun_state.ammo)
        self.entity.is_gun_destroyed = True
        self.now += 0.1
        self.assertFalse(self.battle._advance_local_player_burst())
        self.assertEqual(ammo, self.battle._gun_state.ammo)
        self.assertIsNone(self.battle._local_player_burst)
        self.assertEqual(0, self.battle._gun_state._burst_remaining)

    def test_reload_key_waits_for_burst_completion(self):
        self.battle.shoot(0, 0)
        settings = self.battle._runtime.constants.VEHICLE_SETTING
        self.assertTrue(self.battle.change_vehicle_setting(
            settings.RELOAD_PARTIAL_CLIP, 0))
        for index in range(3):
            if index:
                self.now += 0.1
                self.assertTrue(self.battle._advance_local_player_burst())
        self.assertEqual(0, self.battle._gun_state.clip)
        self.assertGreater(self.battle._gun_state.reload_time, 0.5)

    def test_old_generation_cannot_fire_a_tail_in_another_battle(self):
        self.battle.shoot(0, 0)
        self.battle._generation += 1
        self.now += 1
        self.assertFalse(self.battle._advance_local_player_burst())
        self.assertIsNone(self.battle._local_player_burst)

    def test_cancelled_tail_applies_queued_reload_without_consuming_tail(self):
        self.battle.shoot(0, 0)
        ammo = list(self.battle._gun_state.ammo)
        self.battle.change_vehicle_setting(
            self.battle._runtime.constants.VEHICLE_SETTING.RELOAD_PARTIAL_CLIP, 0)
        self.entity.is_gun_destroyed = True
        self.now += 0.1
        self.assertFalse(self.battle._advance_local_player_burst())
        self.assertEqual(ammo, self.battle._gun_state.ammo)
        self.assertEqual(0, self.battle._gun_state.clip)
        self.assertGreater(self.battle._gun_state.reload_time, 0.5)

    def test_stock_wait_guard_receives_one_native_burst_for_three_local_rounds(self):
        calls = []
        def stock_show_shooting(count, predicted=False):
            if not predicted and not self.battle._avatar.isWaitingForShot:
                return
            calls.append((count, predicted))
            self.battle._avatar.isWaitingForShot = False
        self.entity.showShooting = stock_show_shooting
        self.battle._avatar.isWaitingForShot = False
        self.assertTrue(self.battle.shoot(0, 0))
        self.assertEqual([], calls)
        # Exact Avatar.shoot installs this token after the mailbox returns.
        self.battle._avatar.isWaitingForShot = True
        for index in range(3):
            if index:
                self.now += 0.1
                self.assertTrue(self.battle._advance_local_player_burst())
            self.battle._runtime.bigworld.callbacks.pop(0)()
            self.assertIsNone(self.battle._local_fire_intent)
        self.assertEqual([(3, False)], calls)
        self.assertEqual(3, len(self._launches()))
        for launch in self._launches():
            self.assertTrue(self.battle._accept_projectile_event(dict(launch)))
        self.assertEqual([(3, False)], calls)


if __name__ == '__main__':
    unittest.main()
