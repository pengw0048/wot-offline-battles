"""Visible driver display gates and geometry checks at the runtime boundary."""
import contextlib
import copy
import io
import math
import types
import unittest
from unittest import mock

from tests import test_port_0922_battle_runtime as fixtures
from gui.mods.offline_lan_0922 import driver_state


runtime_module = fixtures.battle_runtime_module


class DriverDisplayRuntimeTests(unittest.TestCase):
    def battle(self):
        with contextlib.redirect_stdout(io.StringIO()):
            runtime, battle, entity = (
                fixtures.BattleRuntimeContractTests._camera_motion_battle(self))
        runtime.bigworld.now = 10.0
        battle._player_driver = types.SimpleNamespace(_movement_control_seq=7)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0, handbrake=False,
            send_current=mock.Mock(return_value=True))
        battle._battle_live = True
        battle._send_driver_control = mock.Mock()
        battle._advance_local_gun_to = mock.Mock()
        battle._publish_rpm = mock.Mock()
        battle._server_clock = lambda: 10.0
        battle._warn_optional_failure = mock.Mock()
        return runtime, battle, entity

    @staticmethod
    def receipt(sequence, integrated=7):
        state = dict((name, 0.0) for name in driver_state._STATE_FIELDS)
        state.update(position=(2.0, 3.0, 3.0 + sequence), speed=10.0,
                     airborne=False, surface_up_cosine=1.0, grind=0)
        return dict(sample_seq=sequence, pose_time_us=sequence * 100000,
                    integrated_control_seq=integrated, state=state,
                    prediction=[], ram_contacts=[], destructible_contacts=[],
                    tank_pushes=[], turret_pushes=[], feedback=[], landing=[],
                    environment=dict((kind, dict(level=0, time=0.0, elapsed=None))
                                     for kind in ('drown', 'overturn')))

    def seed(self, battle):
        for sequence in (1, 2):
            self.assertTrue(battle._driver_apply_receipt(self.receipt(sequence)))

    @staticmethod
    def displayed(battle):
        return tuple(battle._local_matrix.translation)

    def test_receipt_ack_and_physical_state_gate_only_continuation(self):
        for ack, flag, expected in (
                (None, None, False), (6, None, False), (7, None, True),
                (8, None, True), (7, '_local_airborne', False),
                (7, '_local_grind', False),
                (7, '_local_siege_braking', False)):
            with self.subTest(ack=ack, flag=flag):
                unused, battle, unused_entity = self.battle()
                if flag:
                    setattr(battle, flag, True)
                receipt = self.receipt(1, ack)
                battle._accept_driver_display_receipt(receipt, 10.0)
                self.assertEqual(expected, battle._driver_display_allow)
                self.assertEqual(receipt['state']['position'],
                                 battle._driver_presentation.canonical['position'])

    def test_returned_input_value_still_requires_latest_movement_ack(self):
        unused, battle, unused_entity = self.battle()
        self.seed(battle)
        battle._driver_display_is_clear = mock.Mock(return_value=True)
        battle._present_player_driver(10.02)
        checked = self.displayed(battle)
        # A release and re-press can restore the same tuple before a receipt.
        battle._sender.forward = 0.0
        battle._player_driver._movement_control_seq = 8
        battle._sender.forward = 1.0
        battle._player_driver._movement_control_seq = 9
        battle._present_player_driver(10.04)
        self.assertEqual(checked, self.displayed(battle))
        battle._driver_apply_receipt(self.receipt(3, 9))
        battle._present_player_driver(10.05)
        self.assertGreater(self.displayed(battle)[2], 6.0)

    def test_input_change_battle_pause_and_expiry_hold_checked_pose(self):
        for gate in ('forward', 'turn', 'handbrake', 'paused', 'expired'):
            with self.subTest(gate=gate):
                unused, battle, unused_entity = self.battle()
                self.seed(battle)
                battle._driver_display_is_clear = mock.Mock(return_value=True)
                battle._present_player_driver(10.02)
                checked = self.displayed(battle)
                if gate in ('forward', 'turn', 'handbrake'):
                    setattr(battle._sender, gate, getattr(battle._sender, gate) + 1)
                elif gate == 'paused':
                    battle._battle_live = False
                battle._present_player_driver(10.11 if gate == 'expired' else 10.04)
                self.assertEqual(checked, self.displayed(battle))

    def test_batched_receipts_advance_camera_only_on_visible_clock(self):
        unused, battle, unused_entity = self.battle()
        battle._driver_display_clock = 9.98
        battle._driver_display_is_clear = mock.Mock(return_value=True)
        for sequence in (1, 2, 3):
            self.assertTrue(battle._driver_apply_receipt(self.receipt(sequence)))
        self.assertEqual(0.0, battle._local_motion_clock)
        battle._present_player_driver(10.0)
        self.assertAlmostEqual(0.02, battle._local_motion_clock)
        battle._present_player_driver(10.04)
        self.assertAlmostEqual(0.06, battle._local_motion_clock)
        self.assertAlmostEqual(6.4, self.displayed(battle)[2])

    def test_display_never_overwrites_canonical_motion_or_publication(self):
        unused, battle, entity = self.battle()
        self.seed(battle)
        fields = ('position',) + driver_state._STATE_FIELDS
        before = dict((name, copy.deepcopy(getattr(battle, '_local_' + name)))
                      for name in fields)
        battle._driver_display_is_clear = mock.Mock(return_value=True)
        battle._sender.send_current.reset_mock()
        battle._present_player_driver(10.05)
        self.assertAlmostEqual(5.5, self.displayed(battle)[2])
        self.assertEqual(before, dict((name, getattr(battle, '_local_' + name))
                                     for name in fields))
        self.assertEqual(((2.0, 3.0, 5.0), 0.0), battle.local_pose())
        battle._sender.send_current.assert_not_called()
        overlay = battle._runtime.compatibility.pose_overlays[id(entity)]
        self.assertEqual(10.0, overlay['speed'])

    def test_same_time_new_receipt_replaces_display_without_continuation(self):
        runtime, battle, unused_entity = self.battle()
        self.seed(battle)
        battle._driver_display_is_clear = mock.Mock(return_value=True)
        battle._present_player_driver(10.02)
        self.assertAlmostEqual(5.2, self.displayed(battle)[2])
        receipt = self.receipt(3)
        receipt['pose_time_us'] = self.receipt(2)['pose_time_us']
        receipt['state']['yaw'] = 0.7
        runtime.bigworld.now = 10.03
        self.assertTrue(battle._driver_apply_receipt(receipt))
        self.assertEqual(receipt['state']['position'], battle._local_position)
        self.assertFalse(battle._driver_display_allow)
        self.assertEqual(0.0, battle._driver_presentation.source_interval)
        battle._driver_display_is_clear.reset_mock()
        for now in (10.04, 10.07):
            self.assertTrue(battle._present_player_driver(now))
            self.assertEqual(receipt['state']['position'], self.displayed(battle))
            self.assertEqual(0.7, battle._local_matrix.yaw)
        battle._driver_display_is_clear.assert_not_called()

    def test_blocked_or_failed_guard_holds_commit_and_reports_once(self):
        unused, battle, unused_entity = self.battle()
        self.seed(battle)
        battle._driver_display_is_clear = mock.Mock(side_effect=(
            True, False, RuntimeError('native query failed'),
            RuntimeError('native query failed')))
        battle._present_player_driver(10.02)
        checked = self.displayed(battle)
        for now in (10.04, 10.06, 10.08):
            self.assertTrue(battle._present_player_driver(now))
            self.assertEqual(checked, self.displayed(battle))
        battle._warn_optional_failure.assert_called_once()
        self.assertFalse(battle._warn_optional_failure.call_args.kwargs['disable'])

    def geometry(self, battle):
        battle._arena_rotation_is_clear = mock.Mock(return_value=True)
        battle._turret_pose_is_clear = mock.Mock(return_value=True)
        battle._collision_shape = mock.Mock(return_value=object())
        battle._contact_tanks = mock.Mock(return_value=[object()])
        battle._native_world_rotation_is_clear = mock.Mock(return_value=True)
        battle._destructibles = types.SimpleNamespace(
            _vehicle_body_bbox=mock.Mock(return_value=((-1, 0, -2), (1, 2, 2))))
        canonical = dict(position=(2.0, 3.0, 5.0), yaw=0.2, pitch=0.1, roll=-0.1)
        candidate = dict(position=(5.0, 3.0, 9.0), yaw=0.3, pitch=0.1, roll=-0.1)
        return canonical, candidate

    def test_geometry_checks_translation_sweeps_without_committing_world(self):
        runtime, battle, entity = self.battle()
        canonical, candidate = self.geometry(battle)
        with mock.patch.object(runtime_module.tank_collision, 'rotation_fraction',
                               return_value=1.0) as tanks, \
                mock.patch.object(runtime_module.world_collision,
                                  'check_horizontal_collision',
                                  return_value='clear') as world:
            self.assertTrue(battle._driver_display_is_clear(entity, canonical, candidate))
        self.assertEqual((3.0, 4.0), tanks.call_args.kwargs['translation'])
        native = battle._native_world_rotation_is_clear.call_args
        self.assertEqual((3.0, 4.0), native.kwargs['translation'])
        self.assertFalse(native.kwargs['record_local'])
        self.assertTrue(native.kwargs['include_static'])
        self.assertEqual(5.0, world.call_args.args[5])
        self.assertFalse(world.call_args.kwargs['commit_enabled'])
        self.assertAlmostEqual(math.atan2(3.0, 4.0),
                               world.call_args.kwargs['motion_yaw'])
        self.assertEqual(0.1, world.call_args.kwargs['pitch'])

    def test_every_obstacle_gate_and_missing_bounds_reject_candidate(self):
        for gate in ('arena', 'turret', 'tank', 'bounds', 'native', 'world'):
            with self.subTest(gate=gate):
                unused, battle, entity = self.battle()
                canonical, candidate = self.geometry(battle)
                for kind, name in (
                        ('arena', '_arena_rotation_is_clear'),
                        ('turret', '_turret_pose_is_clear'),
                        ('native', '_native_world_rotation_is_clear')):
                    if gate == kind:
                        getattr(battle, name).return_value = False
                if gate == 'bounds':
                    battle._destructibles._vehicle_body_bbox.return_value = None
                with mock.patch.object(runtime_module.tank_collision,
                                       'rotation_fraction',
                                       return_value=0.5 if gate == 'tank' else 1.0), \
                        mock.patch.object(runtime_module.world_collision,
                                          'check_horizontal_collision',
                                          return_value='blocked' if gate == 'world' else 'clear'):
                    self.assertFalse(battle._driver_display_is_clear(
                        entity, canonical, candidate))


if __name__ == '__main__':
    unittest.main()
