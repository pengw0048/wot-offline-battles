"""Private driver physics receipts retain input and one-shot ownership."""
import collections
import copy
import types
import unittest
from unittest import mock

import tests.test_port_0922_battle_runtime as fixtures
from gui.mods.offline_lan_0922 import driver_state


class _Frontend(object):
    ready = True
    error = None

    def __init__(self):
        self.controls = []
        self.receipts = []

    def send_control(self, payload):
        self.controls.append(copy.deepcopy(payload))
        return True

    def drain(self):
        rows, self.receipts = self.receipts, []
        return rows


class _Runtime(driver_state.DriverRuntimeMixin):
    def __init__(self, hidden=False):
        self._reset_player_driver()
        self._player_driver_mode = hidden
        self.now = 10.0
        self._clock = lambda: self.now
        self._server_clock = lambda: self.now + 100.0
        self._estimated_motion_time_us = lambda now: int(now * 1000000)
        self.entity = types.SimpleNamespace(
            id=101, health=100, isCrewActive=True, typeDescriptor=object(),
            devices_hp={'engine': 80.0}, _offlineStunFactors={'speed': 0.7},
            _crew_ko={'driver'}, _crew_impaired={'driver'},
            _destroyed_devices={'engine'}, _critical_devices={'leftTrack'},
            is_tracked=False, is_engine_dead=True, is_gun_destroyed=False,
            is_turret_locked=False)
        self.remote = types.SimpleNamespace(id=202)
        self._server = types.SimpleNamespace(vehicle_id=101)
        self._server_entity = lambda key: {101: self.entity, 202: self.remote}.get(key)
        self._avatar = types.SimpleNamespace(
            spaceID=8, inputHandler=types.SimpleNamespace(getAutorotation=lambda: True),
            gunRotator=types.SimpleNamespace(
                turretYaw=0.4, gunPitch=-0.1, dispersionAngle=0.02),
            updateOwnVehiclePosition=mock.Mock())
        self._runtime = types.SimpleNamespace(constants=types.SimpleNamespace(
            VEHICLE_SETTING=types.SimpleNamespace(SIEGE_MODE_ENABLED=17)))
        self._sender = types.SimpleNamespace(
            forward=1.0, turn=-1.0, handbrake=True, aim_yaw=0.9,
            aim_pitch=-0.8, aim_point=(40.0, 4.0, 70.0), gun_pitch=-0.1)
        self.inputs = []
        self.published = []
        self._sender.send_current = self.send_current
        self.client = types.SimpleNamespace(
            _input_seq=0, publish_driver_state=self.publish,
            send_landing_observation=mock.Mock(return_value=True))
        self._equipment_state = []
        self._local_position = (12.0, 3.0, 24.0)
        for index, name in enumerate(driver_state._STATE_FIELDS):
            setattr(self, '_local_' + name, (index + 1) / 10.0)
        self._local_airborne = True
        self._local_surface_up_cosine = 0.3
        self._local_grind = 2
        self._local_siege_pending = None
        self._pending_landing_impacts = []
        self._local_ram_receipts = collections.OrderedDict()
        self._local_ram_admitted_seq = 0
        self._local_ram_seq = 0
        self._local_ram_receipt = None
        self._local_destructible_contacts = collections.OrderedDict()
        self._local_destructible_safe_poses = collections.OrderedDict()
        self._local_destructible_admitted_seq = 0
        self._local_destructible_contact_seq = 0
        self._local_contact_pushes = {}
        self._local_turret_pushes = {}
        self._records = {'bot:7': {'engine_id': 202}}
        self._native_ram_contact_hook = (None, None, mock.Mock())
        self._collision_feedback = types.SimpleNamespace(present=mock.Mock(return_value=True))
        self._destructibles = types.SimpleNamespace(
            commit_local_prediction=mock.Mock(return_value=True),
            commit_local_tree_prediction=mock.Mock(
                return_value={'status': 'crushed', 'token': [[2, 3, 4]]}))
        self._destructible_contact_token = fixtures.BattleRuntime._destructible_contact_token
        self._vector = tuple
        for name in ('drown', 'overturn'):
            setattr(self, '_' + name + '_level', 0)
            setattr(self, '_' + name + '_time', 0.0)
            setattr(self, '_' + name + '_started', None)
        self._present_drowning_level = mock.Mock()
        self._present_overturn_level = mock.Mock()
        self._advance_local_gun_to = mock.Mock()
        self._update_local_presentation = mock.Mock(
            side_effect=lambda entity, dt: tuple(self._local_position))
        self._publish_rpm = mock.Mock()
        self._apply_mirrored_gun_pose = mock.Mock()
        self.change_vehicle_setting = mock.Mock(return_value=True)
        self._fail = mock.Mock()
        self._present_driver_failure = mock.Mock()
        if not hidden:
            self._player_driver = _Frontend()

    def publish(self, receipt):
        self.published.append(copy.deepcopy(receipt))
        return True

    def send_current(self, siege_enabled=None):
        position, stamp = self._driver_pose_publication()
        self.inputs.append((position, stamp, siege_enabled))
        self.client._input_seq += 1
        self._driver_pose_published()
        return True


class DriverStateTests(unittest.TestCase):
    def receipt(self):
        hidden = _Runtime(True)
        self.assertTrue(hidden._publish_driver_state())
        return hidden.published[-1]

    def test_control_copies_actual_aim_critical_and_native_gun(self):
        visible, hidden = _Runtime(), _Runtime(True)
        self.assertTrue(visible._send_driver_control())
        payload = visible._player_driver.controls[-1]
        payload['control_seq'] = 1
        self.assertTrue(hidden.apply_driver_control(payload))
        self.assertEqual((40.0, 4.0, 70.0), hidden._sender.aim_point)
        self.assertEqual(-0.8, hidden._sender.aim_pitch)
        self.assertEqual({'speed': 0.7}, hidden.entity._offlineStunFactors)
        self.assertEqual({'engine'}, hidden.entity._destroyed_devices)
        self.assertTrue(hidden._driver_autorotation)
        self.assertEqual((0.4, -0.1), hidden._driver_native_gun_angles)
        self.assertEqual(0.02, hidden._apply_mirrored_gun_pose.call_args.args[0]['dispersion_angle'])
        self.assertFalse(hidden.apply_driver_control(payload))

    def test_key_release_within_same_clock_is_delivered(self):
        visible = _Runtime()
        visible._send_driver_control()
        visible._sender.forward = 0.0
        visible._send_driver_control()
        self.assertEqual([1.0, 0.0], [p['forward'] for p in visible._player_driver.controls])

    def test_active_equipment_exact_power_and_relative_clocks_survive_control(self):
        visible, hidden = _Runtime(), _Runtime(True)
        mechanics = driver_state.equipment_mechanics
        contract = mechanics.project_equipment({
            'name': 'removedRpmLimiter', 'enginePowerFactor': 1.1,
            'reuseCount': -1, 'engineHpLossPerSecond': 1.5,
            'cooldownSeconds': 5.0})
        item = mechanics.EquipmentState(contract, visible.now)
        item.active = True
        item.ready_at = visible.now + 3.5
        visible._equipment_state = [item]
        hidden.now = 70.0
        visible._send_driver_control()
        payload = visible._player_driver.controls[-1]
        payload['control_seq'] = 1
        self.assertTrue(hidden.apply_driver_control(payload))
        restored = hidden._equipment_state[0]
        self.assertTrue(restored.active)
        self.assertEqual(73.5, restored.ready_at)
        self.assertEqual(1.1, mechanics.passive_effects(
            hidden._equipment_state)['enginePowerFactor'])

    def test_siege_intent_uses_existing_hidden_request_boundary(self):
        visible, hidden = _Runtime(), _Runtime(True)
        self.assertTrue(visible._send_driver_control(siege_request=True))
        payload = visible._player_driver.controls[-1]
        payload['control_seq'] = 1
        self.assertTrue(hidden.apply_driver_control(payload))
        hidden.change_vehicle_setting.assert_called_once_with(17, 1)

    def test_future_ack_cannot_erase_unpublished_events(self):
        visible, hidden = _Runtime(), _Runtime(True)
        hidden._pending_landing_impacts = [11.0]
        hidden._publish_driver_state()
        visible._send_driver_control()
        payload = visible._player_driver.controls[-1]
        payload.update(control_seq=1, landing_ack=2)
        self.assertFalse(hidden.apply_driver_control(payload))
        self.assertEqual([1], list(hidden._driver_landing_events))
        hidden._fail.assert_not_called()

    def test_event_overflow_is_explicit_without_dropping_existing_events(self):
        hidden = _Runtime(True)
        for unused in range(driver_state._MAX_EVENTS):
            self.assertTrue(hidden._driver_queue_event('landing', {'impact_speed': 11.0}))
        self.assertFalse(hidden._driver_queue_event('landing', {'impact_speed': 12.0}))
        self.assertEqual(driver_state._MAX_EVENTS, len(hidden._driver_landing_events))
        self.assertIn('overflow', hidden._driver_error)
        hidden._fail.assert_not_called()

    def test_visible_failure_message_is_once_and_never_escalates_ui_exception(self):
        visible = _Runtime()
        visible._present_driver_failure.side_effect = RuntimeError('UI unavailable')
        self.assertFalse(visible._driver_local_failure('closed'))
        self.assertFalse(visible._driver_local_failure('closed again'))
        visible._present_driver_failure.assert_called_once_with()
        self.assertEqual('closed', visible._driver_error)
        visible._fail.assert_not_called()

    def test_hidden_failure_never_uses_visible_failure_message(self):
        hidden = _Runtime(True)
        hidden._driver_local_failure('closed')
        hidden._present_driver_failure.assert_not_called()

    def test_invalid_control_has_no_partial_motion_mutation_or_room_failure(self):
        visible, hidden = _Runtime(), _Runtime(True)
        visible._send_driver_control()
        payload = visible._player_driver.controls[-1]
        payload.update(control_seq=1, turn=float('nan'))
        before = hidden._sender.forward
        self.assertFalse(hidden.apply_driver_control(payload))
        self.assertEqual(before, hidden._sender.forward)
        self.assertEqual(0, hidden._driver_control_seq)
        hidden._fail.assert_not_called()

    def test_pose_projection_advances_gun_before_motion_and_publishes_once(self):
        visible = _Runtime()
        visible._local_speed = -5.0
        observed = []
        visible._advance_local_gun_to.side_effect = lambda *args: observed.append(visible._local_speed)
        receipt = self.receipt()
        visible._player_driver.receipts = [receipt]
        self.assertTrue(visible._consume_driver_receipts())
        self.assertEqual([-5.0], observed)
        self.assertEqual(0.4, visible._local_speed)
        self.assertEqual(((12.0, 3.0, 24.0), 10000000, None), visible.inputs[0])
        self.assertEqual((None, None), visible._driver_pose_publication())
        visible._avatar.updateOwnVehiclePosition.assert_called_once_with(
            (12.0, 3.0, 24.0), (0.0, 0.0, 0.1), 0.4, 0.5)

    def test_ordered_same_time_event_does_not_republish_pose_history(self):
        visible = _Runtime()
        first = self.receipt()
        second = copy.deepcopy(first)
        second.update(sample_seq=2, siege_enabled=True)
        second['landing'] = [{'seq': 1, 'impact_speed': 14.0}]
        visible._player_driver.receipts = [first, second]
        self.assertTrue(visible._consume_driver_receipts())
        self.assertEqual((None, None, True), visible.inputs[1])
        visible.client.send_landing_observation.assert_called_once_with(14.0)
        self.assertEqual(2, visible._driver_published_seq)
        self.assertEqual(1, visible._driver_landing_ack)

    def test_receipt_burst_keeps_every_pose_and_event_with_one_final_control(self):
        visible, hidden = _Runtime(), _Runtime(True)
        hidden._pending_landing_impacts = [11.0]
        self.assertTrue(hidden._publish_driver_state())
        hidden.now += 0.02
        hidden._local_position = (13.0, 3.0, 24.0)
        hidden._pending_landing_impacts = [12.0]
        self.assertTrue(hidden._publish_driver_state())
        visible._player_driver.receipts = hidden.published
        self.assertTrue(visible._consume_driver_receipts())
        self.assertEqual([((12.0, 3.0, 24.0), 10000000, None),
                          ((13.0, 3.0, 24.0), 10020000, None)], visible.inputs)
        self.assertEqual([mock.call(11.0), mock.call(12.0)],
                         visible.client.send_landing_observation.call_args_list)
        self.assertEqual(1, len(visible._player_driver.controls))
        control = visible._player_driver.controls[0]
        self.assertEqual((2, 2, 2), (control['published_sample_seq'],
                                   control['published_input_seq'],
                                   control['landing_ack']))
        self.assertFalse(visible._driver_consuming_receipts)

    def test_backpressure_retains_receipt_and_flushes_only_completed_ack(self):
        visible = _Runtime()
        receipt = self.receipt()
        receipt['landing'] = [{'seq': 1, 'impact_speed': 14.0}]
        visible._player_driver.receipts = [receipt]
        visible.client.send_landing_observation.return_value = False
        self.assertFalse(visible._consume_driver_receipts())
        self.assertFalse(visible._driver_consuming_receipts)
        self.assertEqual([receipt], visible._driver_receipts_pending)
        self.assertEqual(1, len(visible._player_driver.controls))
        control = visible._player_driver.controls[0]
        self.assertEqual(1, control['published_sample_seq'])
        self.assertEqual(0, control['landing_ack'])

    def test_landing_is_retained_across_failed_observation_and_acked_once(self):
        visible = _Runtime()
        receipt = self.receipt()
        receipt['landing'] = [{'seq': 1, 'impact_speed': 14.0}]
        visible._player_driver.receipts = [receipt]
        visible.client.send_landing_observation.side_effect = [False, True]
        self.assertFalse(visible._consume_driver_receipts())
        self.assertEqual(1, len(visible._driver_receipts_pending))
        self.assertTrue(visible._consume_driver_receipts())
        self.assertEqual(1, visible._driver_landing_ack)
        self.assertEqual(1, visible._update_local_presentation.call_count)

    def test_hidden_landing_is_durable_until_visible_ack(self):
        visible, hidden = _Runtime(), _Runtime(True)
        hidden._pending_landing_impacts = [11.0]
        hidden._publish_driver_state()
        self.assertEqual([], hidden._pending_landing_impacts)
        self.assertEqual([1], list(hidden._driver_landing_events))
        visible._player_driver.receipts = hidden.published
        visible._consume_driver_receipts()
        payload = visible._player_driver.controls[-1]
        payload['control_seq'] = 1
        self.assertTrue(hidden.apply_driver_control(payload))
        self.assertEqual([], list(hidden._driver_landing_events))

    def test_no_cadence_drop_and_substep_defers_until_real_pose_cursor(self):
        hidden = _Runtime(True)
        hidden._driver_integrating_step = True
        self.assertTrue(hidden._publish_driver_state(True))
        self.assertEqual([], hidden.published)
        self.assertTrue(hidden._driver_receipt_due)
        hidden._driver_integrating_step = False
        hidden._driver_integration_time_us = 10012345
        self.assertTrue(hidden._publish_driver_state())
        self.assertEqual(10012345, hidden.published[0]['pose_time_us'])
        self.assertTrue(hidden.published[0]['siege_enabled'])
        self.assertTrue(hidden._publish_driver_state())
        self.assertEqual(2, len(hidden.published))

    def test_missing_timeline_defers_without_invented_timestamp(self):
        hidden = _Runtime(True)
        hidden._estimated_motion_time_us = lambda now: None
        self.assertFalse(hidden._publish_driver_state())
        self.assertEqual([], hidden.published)

    def test_siege_ack_maps_sample_to_visible_lan_sequence(self):
        visible, hidden = _Runtime(), _Runtime(True)
        hidden._publish_driver_state(True)
        hidden._local_siege_pending = (True, 2 ** 63 - 1)
        hidden._driver_siege_sample_seq = 1
        visible._player_driver.receipts = hidden.published
        visible._consume_driver_receipts()
        payload = visible._player_driver.controls[-1]
        payload['control_seq'] = 1
        self.assertTrue(hidden.apply_driver_control(payload))
        self.assertEqual((True, 1), hidden._local_siege_pending)

    def test_warning_clock_is_rebased_with_source_elapsed(self):
        hidden, visible = _Runtime(True), _Runtime()
        hidden._drown_level = 2
        hidden._drown_time = 3.0
        hidden._drown_started = 107.0
        hidden._publish_driver_state()
        visible.now = 40.0
        visible._player_driver.receipts = hidden.published
        self.assertTrue(visible._consume_driver_receipts())
        self.assertEqual(137.0, visible._drown_started)
        self.assertTrue(visible.entity._offh_drowning)
        visible._present_drowning_level.assert_called_once_with(2, 40.0)

    def test_prediction_preserves_tree_and_catalog_paths_and_is_idempotent(self):
        hidden, visible = _Runtime(True), _Runtime()
        detail = {'_tree_token': [[2, 3, 4]], '_catalog_token': [[5, 6, 7]]}
        hidden._queue_driver_destructible_prediction(
            detail, (1.0, 2.0, 3.0), 0.2, (2.0, 2.0, 4.0), 0.3, 8.0, 0.1, 9.0)
        hidden._publish_driver_state()
        hidden.now += 0.02
        hidden._publish_driver_state()
        visible._player_driver.receipts = hidden.published
        self.assertTrue(visible._consume_driver_receipts())
        self.assertEqual(1, visible._destructibles.commit_local_tree_prediction.call_count)
        self.assertEqual(1, visible._destructibles.commit_local_prediction.call_count)
        self.assertEqual(9.0, visible._destructibles.commit_local_prediction.call_args.args[-1])
        self.assertEqual(1, visible._driver_prediction_ack)

    def test_pending_prediction_is_acknowledged_without_blocking_motion(self):
        hidden, visible = _Runtime(True), _Runtime()
        hidden._queue_driver_destructible_prediction(
            {'_tree_token': [[2, 3, 4]]}, (1, 2, 3), 0, (2, 2, 3), 0, 8, 0.1)
        hidden._publish_driver_state()
        visible._player_driver.receipts = [hidden.published[0]]
        visible._destructibles.commit_local_tree_prediction.side_effect = [
            {'status': 'pending'}, {'status': 'crushed', 'token': [[2, 3, 4]]}]
        self.assertTrue(visible._consume_driver_receipts())
        self.assertEqual(1, len(visible.inputs))
        self.assertEqual(1, visible._driver_prediction_ack)
        hidden.now += 0.02
        hidden._publish_driver_state()
        visible._player_driver.receipts = [hidden.published[-1]]
        self.assertTrue(visible._consume_driver_receipts())
        self.assertEqual(2, len(visible.inputs))
        self.assertEqual(1, visible._driver_prediction_ack)
        self.assertEqual(1, visible._destructibles.commit_local_tree_prediction.call_count)

    def test_unavailable_predictions_do_not_accumulate_until_body_overflow(self):
        hidden, visible = _Runtime(True), _Runtime()
        visible._destructibles = None
        with mock.patch.object(driver_state, '_MAX_EVENTS', 3):
            for index in range(7):
                self.assertTrue(hidden._queue_driver_destructible_prediction(
                    {'_tree_token': [[2, index, 4]]}, (1, 2, 3), 0,
                    (2, 2, 3), 0, 8, 0.1))
                hidden.now += 0.02
                self.assertTrue(hidden._publish_driver_state())
                visible._player_driver.receipts = [hidden.published[-1]]
                self.assertTrue(visible._consume_driver_receipts())
                control = visible._player_driver.controls[-1]
                control['control_seq'] = index + 1
                self.assertTrue(hidden.apply_driver_control(control))
                self.assertFalse(hidden._driver_prediction_events)
        self.assertEqual(7, len(visible.inputs))
        self.assertIsNone(hidden._driver_error)

    def test_feedback_uses_network_identity_and_visible_clock(self):
        hidden, visible = _Runtime(True), _Runtime()
        own = {'vx': 2.0, 'vz': 3.0}
        other = {'vx': 1.0, 'vz': 0.0, '_vehicle': hidden.remote}
        self.assertTrue(hidden._queue_driver_collision(own, other, (2, 3, 4), 10.0))
        self.assertFalse(hidden._queue_driver_collision(own, other, (2, 3, 4), 10.1))
        self.assertTrue(hidden._queue_driver_collision(own, other, (2, 3, 4), 10.3))
        hidden._publish_driver_state()
        visible.now = 40.0
        visible._player_driver.receipts = hidden.published
        self.assertTrue(visible._consume_driver_receipts())
        self.assertEqual(2, visible._collision_feedback.present.call_count)
        self.assertEqual([40.0, 40.0], [call.args[7] for call in
                         visible._collision_feedback.present.call_args_list])

    def test_all_contact_ledgers_preserve_retries_and_existing_admission(self):
        hidden, visible = _Runtime(True), _Runtime()
        hidden._local_ram_receipts[1] = {'seq': 1, 'x': 2.0}
        hidden._local_contact_pushes[7] = [7, 1, 3.0, 4.0, 0.0, 0.0, 0.2]
        hidden._local_turret_pushes['bot:7'] = ['bot:7', 1, 1, 2, 3, 4, 5, 6]
        contact = dict(seq=1, x=1, y=2, z=3, yaw=0.2, speed=8, dt=0.1,
                       end_x=2, end_y=2, end_z=4, end_yaw=0.3, token=[[2, 3, 4]])
        hidden._local_destructible_contacts[1] = contact
        hidden._publish_driver_state()
        visible._local_ram_admitted_seq = 1
        visible._player_driver.receipts = hidden.published
        self.assertTrue(visible._consume_driver_receipts())
        self.assertEqual({}, visible._local_ram_receipts)
        self.assertEqual(contact, visible._local_destructible_contacts[1])
        self.assertEqual(((1, 2, 3), 0.2), visible._local_destructible_safe_poses[1])
        self.assertEqual(hidden._local_contact_pushes, visible._local_contact_pushes)
        self.assertEqual(hidden._local_turret_pushes, visible._local_turret_pushes)

    def test_invalid_receipt_is_atomic_and_local_failure_only(self):
        visible = _Runtime()
        receipt = self.receipt()
        receipt['state']['speed'] = float('inf')
        before = visible._local_position
        visible._player_driver.receipts = [receipt]
        self.assertFalse(visible._consume_driver_receipts())
        self.assertEqual(before, visible._local_position)
        self.assertEqual([], visible.inputs)
        visible._fail.assert_not_called()

    def test_receipts_already_before_eof_are_applied_then_local_failure(self):
        visible = _Runtime()
        visible._player_driver.receipts = [self.receipt()]
        visible._player_driver.error = 'closed'
        self.assertFalse(visible._send_driver_control())
        self.assertIsNone(visible._driver_error)
        self.assertFalse(visible._consume_driver_receipts())
        self.assertEqual(1, len(visible.inputs))
        self.assertEqual('closed', visible._driver_error)
        visible._fail.assert_not_called()


if __name__ == '__main__':
    unittest.main()
