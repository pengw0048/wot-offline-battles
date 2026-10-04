"""One human click owns one descriptor burst, through canonical worker acks."""
import types
import unittest
from unittest import mock

import test_port_0922_battle_runtime as f
from gui.mods.offline_lan_0922 import gun_mechanics


class PlayerBurstTests(unittest.TestCase):
    def setUp(self):
        runtime = f._runtime()
        self.battle = battle = f.BattleRuntime(runtime)
        self.now = 12.0
        battle._clock = lambda: self.now
        descriptor = f._Descriptor()
        descriptor.gun.burst = (3, 0.1)
        descriptor.gun.clip = (6, 0.5)
        descriptor.gun.shotDispersionFactors['afterShotInBurst'] = 1.25
        self.entity = f._Vehicle(10, descriptor, f._Vector(), (0, 0, 0),
                                 {'health': 500})
        runtime.bigworld.entities[10] = self.entity
        battle.client = f._Client()
        battle.state = 'running'
        battle._battle_live = True
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = f._LANInputSender(battle)
        battle._gun_state = gun_mechanics.GunState(descriptor)
        battle._gun_state.reload_time = 0.0
        battle._gun_state.clip = 6
        battle._publish_ammo_state = mock.Mock()
        battle._publish_reload_event = mock.Mock()
        battle._projectile_server_time_ms = 12000
        battle._projectile_server_local_time = self.now
        self.record = {'engine_id': 10, 'local': True}
        battle._records = {'player:1': self.record}

    def ack(self):
        pending = self.battle._local_fire_intent
        return self.battle._accept_player_fire_commit(dict(
            shooter_kind='player', shooter_id=1,
            fire_intent_seq=pending['intent_seq'],
            fire_input_seq=pending['input_seq'],
            shot_seq=pending['intent_seq'], shell_index=0), self.record)

    def test_click_then_release_fires_three_individually_acknowledged_rounds(self):
        gun = self.battle._gun_state
        ammo = sum(gun.ammo)
        self.assertTrue(self.battle.shoot(0, 0))
        self.assertEqual(ammo, sum(gun.ammo))
        self.assertFalse(self.battle.shoot(0, 0))
        self.assertFalse(self.battle._advance_local_player_burst())
        self.ack()
        self.assertEqual(ammo - 1, sum(gun.ammo))
        self.now += 0.099
        self.assertFalse(self.battle._advance_local_player_burst())
        self.now += 0.001
        self.assertTrue(self.battle._advance_local_player_burst())
        self.assertEqual(ammo - 1, sum(gun.ammo))
        self.ack()
        self.now += 0.1
        self.assertTrue(self.battle._advance_local_player_burst())
        self.ack()
        self.assertEqual(ammo - 3, sum(gun.ammo))
        self.assertEqual(3, gun.clip)
        self.assertAlmostEqual(0.5, gun.reload_time)
        self.assertIsNone(self.battle._local_player_burst)
        self.assertFalse(self.battle._advance_local_player_burst())

    def test_partial_clip_and_delayed_ack_never_create_extra_shells(self):
        self.battle._gun_state.clip = 2
        self.assertTrue(self.battle.shoot(0, 0))
        self.now += 1.0
        self.assertFalse(self.battle._advance_local_player_burst())
        self.ack()
        self.assertTrue(self.battle._advance_local_player_burst())
        self.ack()
        self.assertEqual(0, self.battle._gun_state.clip)
        self.assertIsNone(self.battle._local_player_burst)

    def test_destroyed_gun_cancels_unlaunched_tail_without_ammo_debit(self):
        self.battle.shoot(0, 0)
        self.ack()
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
            self.ack()
        self.assertEqual(0, self.battle._gun_state.clip)
        self.assertGreater(self.battle._gun_state.reload_time, 0.5)

    def test_old_generation_cannot_fire_a_tail_in_another_battle(self):
        self.battle.shoot(0, 0)
        self.ack()
        self.battle._generation += 1
        self.now += 1
        self.assertFalse(self.battle._advance_local_player_burst())
        self.assertIsNone(self.battle._local_player_burst)

    def test_cancelled_tail_applies_queued_reload_without_consuming_tail(self):
        self.battle.shoot(0, 0)
        self.ack()
        ammo = list(self.battle._gun_state.ammo)
        self.battle.change_vehicle_setting(
            self.battle._runtime.constants.VEHICLE_SETTING.RELOAD_PARTIAL_CLIP, 0)
        self.entity.is_gun_destroyed = True
        self.now += 0.1
        self.assertFalse(self.battle._advance_local_player_burst())
        self.assertEqual(ammo, self.battle._gun_state.ammo)
        self.assertEqual(0, self.battle._gun_state.clip)
        self.assertGreater(self.battle._gun_state.reload_time, 0.5)

    def test_stock_wait_guard_receives_one_native_burst_for_three_commits(self):
        calls = []
        self.battle._avatar.isWaitingForShot = True
        def stock_show_shooting(count, predicted=False):
            if not predicted and not self.battle._avatar.isWaitingForShot:
                return
            calls.append((count, predicted))
            self.battle._avatar.isWaitingForShot = False
        self.entity.showShooting = stock_show_shooting
        self.battle.shoot(0, 0)
        for index in range(3):
            if index:
                self.now += 0.1
                self.battle._advance_local_player_burst()
            pending = self.battle._local_fire_intent
            self.battle._show_shot(dict(
                attacker=1, shooter_kind='player', shooter_id=1,
                fire_intent_seq=pending['intent_seq'],
                fire_input_seq=pending['input_seq'],
                shot_seq=pending['intent_seq'], shell_index=0,
                burst_index=0, burst_count=1))
        self.assertEqual([(3, False)], calls)


if __name__ == '__main__':
    unittest.main()
