"""Offline aiming always presents and fires from the local native gun."""

import contextlib
import copy
import io
import types
import unittest
from unittest import mock

import test_port_0922_battle_runtime as fixture


class LocalAimTests(unittest.TestCase):
    def battle(self):
        runtime = fixture._runtime()
        battle = fixture.BattleRuntime(runtime)
        battle.state = 'running'
        battle._battle_live = True
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle.client = types.SimpleNamespace(player_id=2, authority_epoch=4)
        battle._start_message = {'round_id': 7}
        return battle, runtime

    def firing_battle(self):
        result = fixture.BattleRuntimeContractTests()._pending_fire_shell_change_battle()
        battle, unused_gun, unused_settings, client, record = result
        client.authority_epoch = 4
        record['state']['vehicle'] = client.vehicle
        record['state']['effective_params'] = client.effective_params
        if battle._projectiles is None:
            battle._projectiles = fixture.battle_runtime_module.InFlightProjectiles(
                initial_time=battle._clock())
        if not callable(getattr(client, 'send_projectile_launch', None)):
            def send(*args, **kwargs):
                client.sent.append(('projectile_launch', args, kwargs))
                return args[2]
            client.send_projectile_launch = send
        return result

    def test_native_dispersion_property_remains_read_only_and_shared(self):
        battle, unused = self.battle()
        rotator = battle._avatar.gunRotator
        values = rotator._VehicleGunRotator__dispersionAngles
        with self.assertRaises(AttributeError):
            rotator.dispersionAngle = 0.5
        self.assertEqual(0.25, battle._native_dispersion_angle())
        values[0] = 0.02
        self.assertEqual(0.02, battle._native_dispersion_angle())
        self.assertIs(values, rotator._VehicleGunRotator__dispersionAngles)

    def test_either_server_marker_setting_uses_the_local_native_marker(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                battle, unused = self.battle()
                rotator = battle._avatar.gunRotator
                rotator.showServerMarker = enabled
                rotator.clientMode = False
                values = rotator._VehicleGunRotator__dispersionAngles
                before = list(values)
                battle._sync_local_server_marker()
                self.assertFalse(rotator.showServerMarker)
                self.assertFalse(rotator.clientMode)
                self.assertIs(values, rotator._VehicleGunRotator__dispersionAngles)
                self.assertEqual(before, values)
                self.assertEqual([], battle._avatar.gun_marker_updates)

    def test_later_stock_mode_callback_cannot_revive_the_server_marker(self):
        battle, unused = self.battle()
        battle._sync_local_server_marker()
        handler = battle._avatar.inputHandler
        for unused in range(2):
            battle._avatar.gunRotator.showServerMarker = True
            battle._avatar.gunRotator.clientMode = False
            handler.showGunMarker2(True)
            handler.showGunMarker(False)
            battle._sync_local_server_marker()
            self.assertFalse(battle._avatar.gunRotator.showServerMarker)
            self.assertFalse(battle._avatar.gunRotator.clientMode)

    def test_stale_remote_marker_metadata_never_overwrites_local_dispersion(self):
        battle, unused = self.battle()
        rotator = battle._avatar.gunRotator
        values = rotator._VehicleGunRotator__dispersionAngles
        battle._last_snapshot = {
            'round_id': 7, 'authority_epoch': 4,
            'players': [{'id': 2, 'gun_marker': {
                'input_seq': 8, 'origin': [0, 2, 0],
                'direction': [0, 0, 1], 'dispersion_angle': 0.001,
                'shot_speed': 800.0}}]}
        for angle in (0.25, 0.12, 0.04, 0.30):
            values[0] = angle
            battle._sync_local_server_marker()
            self.assertEqual(angle, battle._native_dispersion_angle())
            self.assertIs(values, rotator._VehicleGunRotator__dispersionAngles)
        self.assertEqual([], battle._avatar.gun_marker_updates)

    def test_input_does_not_sample_native_aim_or_publish_a_marker_checkpoint(self):
        battle, unused_gun, unused_settings, client, unused_record = self.firing_battle()
        rotator = battle._avatar.gunRotator
        matrix = mock.Mock(side_effect=ReferenceError('not ready'))
        ray = mock.Mock(side_effect=ReferenceError('not ready'))
        rotator.getAvatarOwnVehicleStabilisedMatrix = matrix
        rotator.getCurShotPosition = ray
        self.assertTrue(battle._sender.send_current())
        matrix.assert_not_called()
        ray.assert_not_called()
        self.assertEqual('input', client.sent[-1][0])
        self.assertNotIn('gun_aim_checkpoint', client.sent[-1][2])

    def test_fire_uses_local_dispersion_with_either_saved_marker_setting(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                battle, gun, unused_settings, client, unused_record = self.firing_battle()
                rotator = battle._avatar.gunRotator
                rotator.showServerMarker = enabled
                rotator._VehicleGunRotator__dispersionAngles[0] = 0.15
                battle._sync_local_server_marker()
                before = sum(gun.ammo)
                with mock.patch.object(gun, 'scatter', wraps=gun.scatter) as scatter:
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertTrue(battle.shoot(0.0, 0.0))
                self.assertEqual(0.15, scatter.call_args.kwargs['dispersion_angle'])
                self.assertEqual(before - 1, sum(gun.ammo))
                launches = [row for row in client.sent if row[0] == 'projectile_launch']
                self.assertEqual(1, len(launches))
                self.assertFalse(any(row[0] == 'fire_intent' for row in client.sent))

    def test_shot_needs_native_ray_but_not_retired_stabilised_marker_checkpoint(self):
        battle, gun, unused_settings, client, unused_record = self.firing_battle()
        rotator = battle._avatar.gunRotator
        matrix = mock.Mock(side_effect=ReferenceError('not ready'))
        rotator.getAvatarOwnVehicleStabilisedMatrix = matrix
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(battle.shoot(0.0, 0.0))
        matrix.assert_not_called()
        self.assertEqual(1, len([row for row in client.sent
                                if row[0] == 'projectile_launch']))

        battle, gun, unused_settings, client, unused_record = self.firing_battle()
        battle._avatar.gunRotator.getCurShotPosition = None
        before = (list(gun.ammo), gun.clip, gun.reload_time)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(battle.shoot(0.0, 0.0))
        self.assertEqual(before, (gun.ammo, gun.clip, gun.reload_time))
        self.assertFalse(any(row[0] == 'projectile_launch' for row in client.sent))

    def test_launch_freezes_the_real_ray_and_mounted_shell_before_newer_pose(self):
        battle, unused_gun, unused_settings, client, unused_record = self.firing_battle()
        battle._config = {'perfect_accuracy': True}
        shot = client.effective_params['gun']['shots'][0]['source_shot']
        shot['speed'] = 625.0
        start = fixture._Vector(4.0, 2.0, 8.0)
        direction = fixture._Vector(0.6, 0.0, 0.8)
        battle._avatar.gunRotator.getCurShotPosition = lambda: (start, direction)
        frozen = []

        def send(*args, **kwargs):
            frozen.append((copy.deepcopy(args), copy.deepcopy(kwargs)))
            start.x = 900.0
            direction.z = 0.0
            shot['speed'] = 900.0
            return args[2]

        client.send_projectile_launch = send
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(battle.shoot(0.0, 0.0))
        self.assertEqual(1, len(frozen))
        args, kwargs = frozen[0]
        self.assertEqual([4.0, 2.0, 8.0], args[4])
        self.assertEqual([375.0, 0.0, 500.0], args[5])
        self.assertEqual(625.0, kwargs['source_shot']['speed'])
        meta = next(iter(battle._projectile_meta.values()))
        self.assertEqual((4.0, 2.0, 8.0), meta['origin'])
        self.assertEqual((375.0, 0.0, 500.0), meta['velocity'])
        self.assertEqual(625.0, meta['source_shot']['speed'])


if __name__ == '__main__':
    unittest.main()
