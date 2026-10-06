"""Ordered player-input ledger: recovery, ordering and fire interaction.

One recoverable rejected input frame must end as its own terminal decision
without applying any state, while the next well-formed frame still advances
automatically and a later owner-frozen launch still reaches a terminal.
"""

import json
from pathlib import Path
import sys
import unittest


PORT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORT_ROOT / 'server'))

from lan_battle_server import (  # noqa: E402
    MAX_PLAYER_INPUT_DECISIONS,
    PROJECTILE_MAX_ID,
    SIEGE_ENABLED, SIEGE_SWITCHING_OFF,
    SIMULATION_WORKER_AUTHORITY_ID,
)
from test_port_0922_server_projectiles import (  # noqa: E402
    _attach_worker_authority, _gun_checkpoint, _launch,
    _launch_authority, _player_destructible_contact, _player_ram_contact,
    _resolve, _state,
)


def _frame(state, player_id=1, **changes):
    """Build one healthy wire frame at the player's next eligible sequence."""
    player = state.players[player_id]
    message = {
        'type': 'input', 'round_id': state.round_id,
        'input_seq': player.input_processed_seq + 1,
        'pose_time_us': state._logical_motion_time_us(),
        'forward': 0.0, 'turn': 0.0, 'speed': 0.0,
        'aim_yaw': player.aim_yaw, 'gun_pitch': player.gun_pitch,
        'x': player.x, 'y': player.y, 'z': player.z,
        'yaw': player.yaw, 'pitch': player.pitch, 'roll': player.roll,
        'fire_seq': player.fire_seq, 'shell_index': player.shell_index,
        'next_shell_index': player.next_shell_index,
        'shell_change_pending': player.shell_change_pending,
        'gun_checkpoint': _gun_checkpoint(),
    }
    message.update(changes)
    return json.loads(json.dumps(message))


def _drop(state, player_id=1, field='round_id', **changes):
    message = _frame(state, player_id, **changes)
    message.pop(field, None)
    return message


def _mutable_state(player):
    """Every gameplay field an invalid frame must never be able to write."""
    return (
        player.input_seq, dict(player.input_fingerprints),
        player.gun_checkpoint_seq, dict(player.gun_checkpoint),
        dict(player.gun_checkpoints),
        player.forward, player.turn, player.speed,
        player.aim_yaw, player.gun_pitch,
        player.x, player.y, player.z, player.yaw,
        player.pitch, player.roll, player.up_cosine,
        player.pose_time_us, tuple(player.pose_history),
        player.fire_seq, player.shell_index,
        player.next_shell_index, player.shell_change_pending,
        player.siege_state, player.siege_transition_ticks,
        player.ram_contact_seq, dict(player.ram_contacts),
        player.destructible_contact_seq,
        dict(player.destructible_contacts),
        player.health, player.alive,
    )


# Every pre-admission failure class that a frame with a usable exact ordered
# sequence can hit.  Each entry must become one rejected terminal decision.
RECOVERABLE_FAILURES = (
    ('extra_field', lambda state: _frame(state, health=0),
     'field_whitelist'),
    ('extra_critical', lambda state: _frame(state, critical={'events': []}),
     'field_whitelist'),
    ('wrong_type', lambda state: _frame(state, type='equipment_intent'),
     'field_whitelist'),
    ('shell_pair_missing_pending',
     lambda state: _drop(state, field='shell_change_pending'),
     'shell_pair_shape'),
    ('shell_pair_missing_next',
     lambda state: _drop(state, field='next_shell_index'),
     'shell_pair_shape'),
    ('shell_pair_missing_loaded',
     lambda state: _drop(state, field='shell_index'),
     'shell_pair_shape'),
    ('shell_index_range', lambda state: _frame(state, shell_index=10),
     'shell_selection'),
    ('shell_index_bool', lambda state: _frame(state, shell_index=True),
     'shell_selection'),
    ('next_shell_range',
     lambda state: _frame(state, next_shell_index=10), 'shell_selection'),
    ('pending_not_bool',
     lambda state: _frame(state, shell_change_pending=1),
     'shell_selection'),
    ('pending_mismatch',
     lambda state: _frame(
         state, next_shell_index=1, shell_change_pending=False),
     'shell_selection'),
    ('gun_checkpoint_absent',
     lambda state: _drop(state, field='gun_checkpoint'),
     'gun_checkpoint_missing'),
    ('gun_checkpoint_shape',
     lambda state: _frame(state, gun_checkpoint={'reload_time': 0.0}),
     'gun_checkpoint_shape'),
    ('gun_checkpoint_not_dict',
     lambda state: _frame(state, gun_checkpoint=[0.0]),
     'gun_checkpoint_shape'),
    ('gun_checkpoint_inconsistent',
     lambda state: _frame(state, gun_checkpoint=_gun_checkpoint(
         reload_time=9.0, reload_duration=5.0)),
     'gun_checkpoint_shape'),
    ('gun_checkpoint_clip',
     lambda state: _frame(state, gun_checkpoint=_gun_checkpoint(
         clip=4, clip_size=2)),
     'gun_checkpoint_shape'),
    ('gun_checkpoint_reload_duration',
     lambda state: _frame(state, gun_checkpoint=_gun_checkpoint(
         reload_duration=0.0)),
     'gun_checkpoint_shape'),
    ('gun_checkpoint_dispersion',
     lambda state: _frame(state, gun_checkpoint=_gun_checkpoint(
         dispersion=5.0)),
     'gun_checkpoint_shape'),
    ('up_cosine_bool', lambda state: _frame(state, up_cosine=True),
     'world_up'),
    ('up_cosine_string', lambda state: _frame(state, up_cosine='0.5'),
     'world_up'),
    ('up_cosine_range', lambda state: _frame(state, up_cosine=1.01),
     'world_up'),
    ('round_id_float',
     lambda state: _frame(state, round_id=float(state.round_id)),
     'envelope_round_id'),
    ('forward_range', lambda state: _frame(state, forward=1.5),
     'envelope_numeric'),
    ('forward_bool', lambda state: _frame(state, forward=True),
     'envelope_numeric'),
    ('turn_range', lambda state: _frame(state, turn=-1.5),
     'envelope_numeric'),
    ('speed_range', lambda state: _frame(state, speed=1e300),
     'envelope_numeric'),
    ('aim_yaw_range', lambda state: _frame(state, aim_yaw=7.0),
     'envelope_numeric'),
    ('gun_pitch_range', lambda state: _frame(state, gun_pitch=2.0),
     'envelope_numeric'),
    ('x_range', lambda state: _frame(state, x=4000.0),
     'envelope_numeric'),
    ('y_range', lambda state: _frame(state, y=2000.0),
     'envelope_numeric'),
    ('z_range', lambda state: _frame(state, z=-4000.0),
     'envelope_numeric'),
    ('yaw_range', lambda state: _frame(state, yaw=9.0),
     'envelope_numeric'),
    ('pitch_range', lambda state: _frame(state, pitch=4.0),
     'envelope_numeric'),
    ('roll_range', lambda state: _frame(state, roll=-4.0),
     'envelope_numeric'),
    ('pose_time_fractional',
     lambda state: _frame(state, pose_time_us=1.5), 'envelope_integer'),
    ('pose_time_negative',
     lambda state: _frame(state, pose_time_us=-1), 'envelope_integer'),
    ('fire_seq_bool', lambda state: _frame(state, fire_seq=True),
     'envelope_integer'),
    ('fire_seq_negative', lambda state: _frame(state, fire_seq=-1),
     'envelope_integer'),
    ('siege_shape', lambda state: _frame(state, siege_enabled=1),
     'envelope_siege'),
)


class OrderedInputLedgerTests(unittest.TestCase):
    def _admit_base_frame(self, state, player_id=1):
        player = state.players[player_id]
        self.assertTrue(state.update_input(player_id, _frame(
            state, player_id, forward=0.5, aim_yaw=0.1)))
        self.assertEqual(1, player.input_seq)
        self.assertEqual(1, player.input_processed_seq)
        self.assertEqual(1, player.gun_checkpoint_seq)
        return player

    def test_each_recoverable_failure_recovers_on_the_next_frame(self):
        for name, build, reason in RECOVERABLE_FAILURES:
            with self.subTest(failure=name):
                state = _state(players=1)
                player = self._admit_base_frame(state)
                before = _mutable_state(player)

                self.assertFalse(state.update_input(1, build(state)))

                # One rejected terminal record, no applied state, no gun
                # checkpoint, and the terminal frontier moved past it.
                self.assertEqual(before, _mutable_state(player))
                self.assertEqual(2, player.input_processed_seq)
                self.assertEqual(1, player.input_seq)
                self.assertEqual(
                    {'outcome': 'rejected', 'reason': reason},
                    {'outcome': player.input_decisions[2]['outcome'],
                     'reason': player.input_decisions[2]['reason']})
                self.assertNotIn(2, player.gun_checkpoints)
                self.assertNotIn(2, player.input_fingerprints)
                self.assertEqual(reason, player.last_input_reject['reason'])
                self.assertEqual(2, player.last_input_reject['submitted_seq'])
                self.assertEqual(2, player.last_input_reject['expected_seq'])
                self.assertEqual(1, player.last_input_reject['processed_seq'])
                self.assertEqual(1, player.last_input_reject['applied_seq'])
                self.assertTrue(player.last_input_reject['consumed'])

                # The next well-formed frame advances automatically and its
                # own state becomes the resulting state.
                self.assertTrue(state.update_input(1, _frame(
                    state, forward=-0.75, turn=0.5, speed=3.0,
                    aim_yaw=0.3, gun_pitch=0.2, shell_index=1,
                    next_shell_index=1, shell_change_pending=False,
                    up_cosine=0.9)))
                self.assertEqual(3, player.input_seq)
                self.assertEqual(3, player.input_processed_seq)
                self.assertEqual(3, player.gun_checkpoint_seq)
                self.assertEqual(
                    (-0.75, 0.5, 3.0, 0.3, 0.2, 1, 0.9),
                    (player.forward, player.turn, player.speed,
                     round(player.aim_yaw, 6), round(player.gun_pitch, 6),
                     player.shell_index, player.up_cosine))
                self.assertEqual(
                    'applied', player.input_decisions[3]['outcome'])
                self.assertIn(3, player.input_fingerprints)

    def test_round_fence_rejection_does_not_consume_current_sequence(self):
        state = _state(players=1)
        player = self._admit_base_frame(state)
        before = _mutable_state(player)

        for message in (
                _drop(state, field='round_id'),
                _frame(state, round_id=state.round_id + 1)):
            self.assertFalse(state.update_input(1, message))
            self.assertEqual(before, _mutable_state(player))
            self.assertEqual(1, player.input_processed_seq)
            self.assertNotIn(2, player.input_decisions)
            self.assertEqual('round_mismatch',
                             player.last_input_reject['reason'])
            self.assertFalse(player.last_input_reject['consumed'])

        self.assertTrue(state.update_input(1, _frame(state)))
        self.assertEqual(2, player.input_processed_seq)
        self.assertEqual(2, player.input_seq)

    def test_one_rejection_never_repeats_the_applied_frame(self):
        state = _state(players=1)
        player = self._admit_base_frame(state)

        self.assertFalse(state.update_input(1, _frame(state, aim_yaw=7.0)))
        applied = dict(player.input_fingerprints)

        self.assertTrue(state.update_input(1, _frame(state, forward=0.25)))
        self.assertEqual(sorted(applied) + [3],
                         sorted(player.input_fingerprints))
        self.assertEqual(
            [1, 2, 3], sorted(player.input_decisions))

    def test_unusable_sequence_identity_consumes_no_frontier(self):
        for name, value in (
                ('bool', True), ('fractional', 1.5), ('string', '2'),
                ('negative', -1), ('zero', 0), ('none', None),
                ('oversized', PROJECTILE_MAX_ID + 1)):
            with self.subTest(sequence=name):
                state = _state(players=1)
                player = self._admit_base_frame(state)
                before = _mutable_state(player)
                decisions = dict(player.input_decisions)

                self.assertFalse(state.update_input(
                    1, _frame(state, input_seq=value)))

                self.assertEqual(before, _mutable_state(player))
                self.assertEqual(1, player.input_processed_seq)
                self.assertEqual(decisions, dict(player.input_decisions))
                self.assertEqual(
                    'sequence_identity',
                    player.last_input_reject['reason'])
                self.assertFalse(player.last_input_reject['consumed'])

                # The healthy next frame is still admitted.
                self.assertTrue(state.update_input(1, _frame(state)))
                self.assertEqual(2, player.input_seq)

    def test_a_missing_sequence_consumes_no_frontier(self):
        state = _state(players=1)
        player = self._admit_base_frame(state)
        before = _mutable_state(player)

        self.assertFalse(state.update_input(
            1, _drop(state, field='input_seq')))

        self.assertEqual(before, _mutable_state(player))
        self.assertEqual(1, player.input_processed_seq)
        self.assertEqual(
            'sequence_identity', player.last_input_reject['reason'])

    def test_an_exhausted_sequence_space_consumes_no_frontier(self):
        state = _state(players=1)
        player = self._admit_base_frame(state)
        player.input_processed_seq = PROJECTILE_MAX_ID
        player.input_seq = PROJECTILE_MAX_ID
        before = _mutable_state(player)

        self.assertFalse(state.update_input(
            1, _frame(state, input_seq=PROJECTILE_MAX_ID + 1)))
        self.assertEqual(
            'sequence_identity', player.last_input_reject['reason'])
        self.assertFalse(player.last_input_reject['consumed'])

        self.assertFalse(state.update_input(
            1, _frame(state, input_seq=PROJECTILE_MAX_ID)))
        self.assertEqual(
            'sequence_retired', player.last_input_reject['reason'])
        self.assertFalse(player.last_input_reject['consumed'])
        self.assertEqual(before, _mutable_state(player))
        self.assertEqual(PROJECTILE_MAX_ID, player.input_processed_seq)

    def test_future_gap_and_reordered_frames_consume_no_frontier(self):
        state = _state(players=1)
        player = self._admit_base_frame(state)
        before = _mutable_state(player)

        for sequence in (3, 4, 500):
            with self.subTest(sequence=sequence):
                self.assertFalse(state.update_input(
                    1, _frame(state, input_seq=sequence)))
                self.assertEqual(
                    'sequence_gap', player.last_input_reject['reason'])
                self.assertFalse(player.last_input_reject['consumed'])
                self.assertEqual(1, player.input_processed_seq)
                self.assertEqual(before, _mutable_state(player))

        # A reordered arrival of an already applied sequence is a retired
        # identifier, never new state.
        self.assertTrue(state.update_input(1, _frame(state)))
        self.assertEqual(2, player.input_processed_seq)
        applied = _mutable_state(player)
        self.assertFalse(state.update_input(
            1, _frame(state, input_seq=1, forward=1.0)))
        self.assertEqual(
            'identity_conflict', player.last_input_reject['reason'])
        self.assertEqual(applied, _mutable_state(player))

    def test_an_exact_rejected_retry_folds_to_the_same_result(self):
        state = _state(players=1)
        player = self._admit_base_frame(state)
        bad = _frame(state, aim_yaw=7.0)

        self.assertFalse(state.update_input(1, bad))
        after_first = _mutable_state(player)
        decision = dict(player.input_decisions[2])

        for unused in range(3):
            self.assertFalse(state.update_input(1, json.loads(
                json.dumps(bad))))
            self.assertEqual(after_first, _mutable_state(player))
            self.assertEqual(2, player.input_processed_seq)
            self.assertEqual(decision, dict(player.input_decisions[2]))
            self.assertEqual(
                'envelope_numeric', player.last_input_reject['reason'])
            self.assertTrue(player.last_input_reject['consumed'])

    def test_an_exact_rejected_retry_keeps_its_original_diagnostic(self):
        state = _state(players=1)
        player = self._admit_base_frame(state)
        player.alive = False
        player.health = 0
        bad = _frame(
            state,
            ram_contacts=[_player_ram_contact(contact_x=True)])

        self.assertFalse(state.update_input(1, bad))
        first = dict(player.last_input_reject)
        self.assertEqual('ram_contacts', first['field'])
        self.assertFalse(first['active'])

        # Lifecycle may move on before an exact transport retry arrives.  Its
        # terminal identity still has to report the field and lifecycle state
        # that caused the original decision, not relabel it as input_seq in
        # the actor's current state.
        player.alive = True
        player.health = player.max_health
        self.assertFalse(state.update_input(1, json.loads(json.dumps(bad))))
        self.assertEqual(first['reason'], player.last_input_reject['reason'])
        self.assertEqual(first['field'], player.last_input_reject['field'])
        self.assertEqual(first['active'], player.last_input_reject['active'])
        self.assertTrue(player.last_input_reject['consumed'])

    def test_an_exact_applied_retry_folds_without_reapplying(self):
        state = _state(players=1)
        player = self._admit_base_frame(state)
        good = _frame(state, forward=0.25)

        self.assertTrue(state.update_input(1, good))
        applied = _mutable_state(player)

        self.assertTrue(state.update_input(1, json.loads(json.dumps(good))))
        self.assertEqual(applied, _mutable_state(player))
        self.assertEqual(2, player.input_processed_seq)

    def test_a_changed_payload_at_a_terminal_sequence_conflicts(self):
        state = _state(players=1)
        player = self._admit_base_frame(state)

        # Changed payload over a rejected decision.
        self.assertFalse(state.update_input(1, _frame(state, aim_yaw=7.0)))
        rejected = dict(player.input_decisions[2])
        before = _mutable_state(player)
        self.assertFalse(state.update_input(
            1, _frame(state, input_seq=2, forward=1.0)))
        self.assertEqual(
            'identity_conflict', player.last_input_reject['reason'])
        self.assertFalse(player.last_input_reject['consumed'])
        self.assertEqual(rejected, dict(player.input_decisions[2]))
        self.assertEqual(before, _mutable_state(player))

        # Changed payload over an applied decision.
        self.assertTrue(state.update_input(1, _frame(state, forward=0.5)))
        applied = dict(player.input_decisions[3])
        before = _mutable_state(player)
        self.assertFalse(state.update_input(
            1, _frame(state, input_seq=3, forward=-1.0)))
        self.assertEqual(
            'identity_conflict', player.last_input_reject['reason'])
        self.assertEqual(applied, dict(player.input_decisions[3]))
        self.assertEqual(before, _mutable_state(player))

    def test_bounded_eviction_cannot_turn_an_old_frame_into_new_state(self):
        state = _state(players=1)
        player = state.players[1]
        for unused in range(MAX_PLAYER_INPUT_DECISIONS + 4):
            self.assertTrue(state.update_input(1, _frame(state)))
        self.assertEqual(
            MAX_PLAYER_INPUT_DECISIONS, len(player.input_decisions))
        self.assertNotIn(1, player.input_decisions)
        before = _mutable_state(player)

        self.assertFalse(state.update_input(
            1, _frame(state, input_seq=1, forward=1.0)))

        self.assertEqual(
            'sequence_retired', player.last_input_reject['reason'])
        self.assertFalse(player.last_input_reject['consumed'])
        self.assertEqual(before, _mutable_state(player))

    def test_two_players_keep_independent_ordered_ledgers(self):
        state = _state(players=2)
        first = state.players[1]
        second = state.players[2]
        self.assertTrue(state.update_input(1, _frame(state, 1)))
        self.assertTrue(state.update_input(2, _frame(state, 2)))
        second_before = _mutable_state(second)

        self.assertFalse(state.update_input(
            1, _frame(state, 1, aim_yaw=7.0)))

        self.assertEqual(2, first.input_processed_seq)
        self.assertEqual(1, first.input_seq)
        self.assertEqual(1, second.input_processed_seq)
        self.assertEqual(second_before, _mutable_state(second))
        self.assertFalse(second.last_input_reject)

        # The unaffected player keeps advancing on its own frontier.
        self.assertTrue(state.update_input(2, _frame(state, 2, forward=1.0)))
        self.assertEqual(2, second.input_seq)
        self.assertEqual(1.0, second.forward)

    def test_an_active_frame_still_contains_bad_contact_rows_per_row(self):
        state = _state(players=1)
        player = self._admit_base_frame(state)

        # Established containment: one malformed optional contact row must not
        # reject the ordered control frame around it.
        self.assertTrue(state.update_input(1, _frame(
            state, forward=1.0,
            ram_contacts=[_player_ram_contact(contact_x=True)],
            destructible_contacts=[_player_destructible_contact(x=True)])))
        self.assertEqual(2, player.input_seq)
        self.assertEqual(2, player.input_processed_seq)
        self.assertEqual(1.0, player.forward)
        self.assertIn(1, player.ram_contact_rejections)
        self.assertIn(1, player.destructible_contact_rejections)

        # An oversized or wrongly typed batch is ignored on an active frame
        # rather than rejecting the control frame.
        self.assertTrue(state.update_input(1, _frame(
            state, turn=0.5,
            ram_contacts=[_player_ram_contact(seq=value)
                          for value in range(2, 20)],
            destructible_contacts='not-a-list')))
        self.assertEqual(3, player.input_seq)
        self.assertEqual(0.5, player.turn)

    def test_an_inactive_frame_folds_as_terminal_without_applying_state(self):
        for condition in ('waiting', 'finished', 'nonparticipating', 'dead'):
            with self.subTest(condition=condition):
                state = _state(players=1)
                player = self._admit_base_frame(state)
                if condition == 'waiting':
                    state.phase = 'waiting'
                elif condition == 'finished':
                    state.battle_result = {'winner': 1}
                elif condition == 'nonparticipating':
                    player.participating = False
                else:
                    player.alive = False
                    player.health = 0
                before = _mutable_state(player)

                self.assertTrue(state.update_input(1, _frame(
                    state, forward=1.0, turn=1.0, speed=25.0)))

                self.assertEqual(before, _mutable_state(player))
                self.assertEqual(2, player.input_processed_seq)
                self.assertEqual(1, player.input_seq)
                self.assertEqual(
                    'inactive', player.input_decisions[2]['outcome'])
                self.assertFalse(player.input_decisions[2]['active'])

                # A frame queued behind it is not stuck behind a gap.
                self.assertTrue(state.update_input(1, _frame(state)))
                self.assertEqual(3, player.input_processed_seq)

    def test_an_inactive_frame_with_a_bad_contact_row_is_terminal(self):
        state = _state(players=1)
        player = self._admit_base_frame(state)
        player.alive = False
        player.health = 0
        before = _mutable_state(player)

        self.assertFalse(state.update_input(1, _frame(
            state, ram_contacts=[_player_ram_contact(contact_x=True)])))

        self.assertEqual(before, _mutable_state(player))
        self.assertEqual(2, player.input_processed_seq)
        self.assertEqual(
            'envelope_contacts', player.last_input_reject['reason'])
        self.assertFalse(player.last_input_reject['active'])
        self.assertTrue(player.last_input_reject['consumed'])

    def test_the_rejection_log_keeps_the_first_typed_cause(self):
        state = _state(players=1)
        self._admit_base_frame(state)

        self.assertFalse(state.update_input(1, _frame(state, aim_yaw=7.0)))
        first_key, first_line = state.player_input_rejection_log(1)

        self.assertEqual('player-input:1:envelope_numeric', first_key)
        self.assertIn('reason=envelope_numeric', first_line)
        self.assertIn('field=aim_yaw', first_line)
        self.assertIn('seq=2', first_line)
        self.assertIn('expected=2', first_line)
        self.assertIn('processed=1', first_line)
        self.assertIn('applied=1', first_line)
        self.assertIn('checkpoint=1', first_line)

        # A later ordering rejection uses a different rate-limit key, so the
        # first causal field is never suppressed by the cascade behind it.
        self.assertFalse(state.update_input(
            1, _frame(state, input_seq=99)))
        gap_key, gap_line = state.player_input_rejection_log(1)
        self.assertEqual('player-input:1:sequence_gap', gap_key)
        self.assertNotEqual(first_key, gap_key)
        self.assertIn('seq=99', gap_line)
        self.assertIn('expected=3', gap_line)


class InputLedgerFireTests(unittest.TestCase):
    def _armed_state(self):
        state = _state(players=2)
        player = state.players[1]
        self.relayed = []
        state.simulation_worker.offer_reliable = lambda message: (
            self.relayed.append(dict(message)) or True)
        self.assertTrue(state.update_input(1, _frame(state)))
        return state, player

    @staticmethod
    def _gun_state(player):
        return (player.shell_index, player.next_shell_index,
                player.shell_change_pending, dict(player.gun_checkpoint),
                player.gun_checkpoint_seq)

    @staticmethod
    def _shot(state, **changes):
        message = _launch(
            authority_epoch=state.authority_epoch, fire_intent_seq=1,
            fire_input_seq=state.players[1].input_processed_seq,
            range_origin=[0.0, 0.0, 0.0],
            launch_server_time_ms=state._server_time_ms())
        message.update(changes)
        return message

    def test_rejected_input_does_not_block_the_owners_frozen_projectile(self):
        state, player = self._armed_state()
        self.assertFalse(state.update_input(1, _frame(state, aim_yaw=7.0)))
        gun_before = self._gun_state(player)
        relayed_before = len(self.relayed)
        message = self._shot(state, fire_input_seq=2)

        self.assertTrue(state.launch_projectile(1, message))
        self.assertTrue(state.launch_projectile(1, json.loads(json.dumps(message))))

        self.assertEqual(2, state.projectiles['1:p:1:1']['fire_input_seq'])
        self.assertEqual(1, state._statistics_row('player', 1)['shots_fired'])
        self.assertEqual(gun_before, self._gun_state(player))
        self.assertEqual(relayed_before, len(self.relayed))

    def test_reported_shell_inventory_marks_the_final_round_at_launch(self):
        state, unused_player = self._armed_state()
        self.assertTrue(state.launch_projectile(
            1, self._shot(state, shells_before_shot=1)))
        self.assertIs(True, state.last_shell_shots['1:p:1:1'])
        self.assertFalse(any(message['type'] == 'fire_intent'
                             for message in self.relayed))

    def test_launch_without_shell_inventory_still_advances(self):
        state, unused_player = self._armed_state()
        self.assertTrue(state.launch_projectile(1, self._shot(state)))
        self.assertNotIn('1:p:1:1', state.last_shell_shots)

    def test_unknown_launch_field_is_rejected_without_consuming_sequence(self):
        state, player = self._armed_state()
        self.assertFalse(state.launch_projectile(
            1, self._shot(state, unexpected_field=1)))
        self.assertEqual(0, player.fire_seq)
        self.assertFalse(state.projectiles)
        self.assertTrue(state.launch_projectile(1, self._shot(state)))

    def test_launch_can_reference_the_last_valid_input_after_a_rejection(self):
        state, unused_player = self._armed_state()
        self.assertFalse(state.update_input(1, _frame(state, aim_yaw=7.0)))
        self.assertTrue(state.launch_projectile(
            1, self._shot(state, fire_input_seq=1)))
        self.assertEqual(1, state.projectiles['1:p:1:1']['fire_input_seq'])

    def test_launch_keeps_the_first_valid_post_rejection_input_identity(self):
        state, player = self._armed_state()
        self.assertFalse(state.update_input(1, _frame(state, aim_yaw=7.0)))
        self.assertTrue(state.update_input(1, _frame(state)))
        self.assertTrue(state.launch_projectile(1, self._shot(state)))
        self.assertEqual(3, player.input_seq)
        self.assertEqual(3, state.projectiles['1:p:1:1']['fire_input_seq'])

    def test_launch_keeps_processed_identity_and_rejects_unreceived_input(self):
        state, player = self._armed_state()
        self.assertFalse(state.update_input(1, _frame(state, aim_yaw=7.0)))
        self.assertTrue(state.update_input(1, _frame(state)))
        gun_before = self._gun_state(player)
        # The owner froze its complete shot locally; neither an old nor a
        # rejected input is replaced by the server's latest gun checkpoint.
        for shot_seq, input_seq in ((1, 1), (2, 2)):
            self.assertTrue(state.launch_projectile(1, self._shot(
                state, shot_seq=shot_seq, fire_intent_seq=shot_seq,
                fire_input_seq=input_seq)))
            self.assertEqual(input_seq, state.projectiles[
                '1:p:1:%d' % shot_seq]['fire_input_seq'])
        for input_seq in (4, 99):
            self.assertFalse(state.launch_projectile(1, self._shot(
                state, shot_seq=3, fire_intent_seq=3,
                fire_input_seq=input_seq)))
        self.assertEqual(2, player.fire_seq)
        self.assertEqual(gun_before, self._gun_state(player))

    def test_changed_launch_payload_at_a_used_sequence_conflicts(self):
        state, player = self._armed_state()
        message = self._shot(state)
        self.assertTrue(state.launch_projectile(1, message))
        gun_before = self._gun_state(player)
        self.assertFalse(state.launch_projectile(
            1, dict(message, origin=[1.0, 1.0, 0.0])))
        self.assertEqual([0.0, 1.0, 0.0],
                         state.projectiles['1:p:1:1']['origin'])
        self.assertEqual(1, state._statistics_row('player', 1)['shots_fired'])
        self.assertEqual(gun_before, self._gun_state(player))

    def test_recovered_frame_reaches_a_projectile_terminal(self):
        state, player = self._armed_state()
        self.assertFalse(state.update_input(1, _frame(state, aim_yaw=7.0)))
        self.assertEqual((1, 2), (player.input_seq, player.input_processed_seq))
        self.assertTrue(state.update_input(1, _frame(state)))
        self.assertTrue(state.launch_projectile(1, self._shot(state)))
        self.assertEqual(3, state.projectiles['1:p:1:1']['fire_input_seq'])
        self.assertTrue(state.resolve_projectile(
            1, _resolve(state, '1:p:1:1')))
        self.assertNotIn('1:p:1:1', state.projectiles)
        self.assertEqual(900, state.players[2].health)
        self.assertEqual(1, player.fire_seq)

    def test_queued_frames_and_launches_keep_their_order(self):
        state, player = self._armed_state()
        queued = [('input', _frame(state, aim_yaw=7.0))]
        for index in range(3):
            queued.append(('input', _frame(
                state, input_seq=3 + index, forward=0.1 * (index + 1))))
        queued.extend((
            ('launch', self._shot(state, fire_input_seq=2)),
            ('launch', self._shot(state, shot_seq=2, fire_intent_seq=2,
                                  fire_input_seq=5))))
        outcomes = []
        for kind, message in queued:
            method = state.update_input if kind == 'input' else state.launch_projectile
            outcomes.append(method(1, message))
        self.assertEqual([False, True, True, True, True, True], outcomes)
        self.assertEqual((5, 5), (player.input_seq, player.input_processed_seq))
        self.assertEqual(0.3, round(player.forward, 6))
        self.assertEqual([2, 5], [state.projectiles[
            '1:p:1:%d' % seq]['fire_input_seq'] for seq in (1, 2)])

    def test_historical_rejection_cascade_recovers_through_twenty_terminals(self):
        """Model report 124651: one rejection then 36 frames and 20 shots."""
        state, player = self._armed_state()
        self.assertFalse(state.update_input(1, _frame(state, aim_yaw=7.0)))
        for index in range(36):
            self.assertTrue(state.update_input(1, _frame(
                state, forward=0.02 * (index + 1))))
            if index < 20:
                shot_seq = index + 1
                self.assertTrue(state.launch_projectile(1, self._shot(
                    state, shot_seq=shot_seq, fire_intent_seq=shot_seq)))
                projectile_id = '1:p:1:%d' % shot_seq
                self.assertEqual(player.input_seq,
                                 state.projectiles[projectile_id]['fire_input_seq'])
                self.assertTrue(state.resolve_projectile(1, _resolve(
                    state, projectile_id, outcome='miss', impact=None,
                    direct=None)))
        self.assertEqual(20, len(state.projectile_tombstones))
        self.assertEqual((38, 38), (player.input_seq, player.input_processed_seq))
        self.assertEqual('rejected', player.input_decisions[2]['outcome'])
        self.assertTrue(player.alive)
        self.assertTrue(player.connected)
        self.assertIsNone(state.battle_result)
        self.assertEqual('battle', state.phase)
        self.assertEqual(SIMULATION_WORKER_AUTHORITY_ID, state.bot_authority_id)
        self.assertFalse(state.projectiles)


class InputLedgerLifecycleTests(unittest.TestCase):
    def test_a_new_round_retires_both_frontiers_coherently(self):
        state = _state(players=1)
        player = state.players[1]
        self.assertTrue(state.update_input(1, _frame(state)))
        self.assertFalse(state.update_input(1, _frame(state, aim_yaw=7.0)))
        self.assertEqual((1, 2), (
            player.input_seq, player.input_processed_seq))
        stale = _frame(state, input_seq=3)

        state._reset_round()

        self.assertEqual((0, 0), (
            player.input_seq, player.input_processed_seq))
        self.assertFalse(player.input_decisions)
        self.assertFalse(player.input_fingerprints)
        self.assertFalse(player.last_input_reject)
        self.assertFalse(player.input_reject_counts)
        # A late old-round frame is a local no-op for the new round.
        self.assertFalse(state.update_input(1, stale))
        self.assertEqual((0, 0), (
            player.input_seq, player.input_processed_seq))

    def test_a_rejection_around_death_does_not_block_the_next_frame(self):
        state = _state(players=1)
        player = state.players[1]
        self.assertTrue(state.update_input(1, _frame(state)))
        self.assertFalse(state.update_input(1, _frame(state, aim_yaw=7.0)))

        player.alive = False
        player.health = 0
        self.assertTrue(state.update_input(1, _frame(state, forward=1.0)))
        self.assertEqual(3, player.input_processed_seq)
        self.assertEqual(1, player.input_seq)
        self.assertEqual(0.0, player.forward)

        player.alive = True
        player.health = player.max_health
        self.assertTrue(state.update_input(1, _frame(state, forward=1.0)))
        self.assertEqual(4, player.input_seq)
        self.assertEqual(1.0, player.forward)

    def test_a_disconnected_player_consumes_no_frontier(self):
        state = _state(players=1)
        player = state.players[1]
        self.assertTrue(state.update_input(1, _frame(state)))
        player.connected = False
        message = _frame(state, aim_yaw=7.0)

        self.assertFalse(state.update_input(1, message))
        self.assertEqual(1, player.input_processed_seq)
        self.assertEqual(
            'player_disconnected', player.last_input_reject['reason'])
        self.assertFalse(player.last_input_reject['consumed'])
        self.assertFalse(player.last_input_reject['active'])

        player.connected = True
        self.assertFalse(state.update_input(1, message))
        self.assertEqual(2, player.input_processed_seq)
        self.assertTrue(state.update_input(1, _frame(state)))
        self.assertEqual(3, player.input_seq)

    def test_a_wrong_round_frame_consumes_no_frontier(self):
        state = _state(players=1)
        player = state.players[1]
        self.assertTrue(state.update_input(1, _frame(state)))

        # Seed an earlier same-round failure: the diagnostic for the early
        # round fence must replace it rather than reusing this stale reason.
        self.assertFalse(state.update_input(
            1, _frame(state, aim_yaw=7.0)))
        self.assertEqual(
            'envelope_numeric', player.last_input_reject['reason'])

        self.assertFalse(state.update_input(
            1, _frame(
                state, input_seq=3, round_id=state.round_id + 1,
                aim_yaw=7.0)))

        self.assertEqual(2, player.input_processed_seq)
        self.assertEqual('round_mismatch', player.last_input_reject['reason'])
        self.assertEqual('round_id', player.last_input_reject['field'])
        self.assertFalse(player.last_input_reject['consumed'])

    def test_a_rejection_does_not_disturb_a_siege_transition(self):
        state = _state(players=1)
        player = state.players[1]
        player.vehicle = 'sweden:S21_UDES_03'
        player.siege_state = SIEGE_ENABLED
        self.assertTrue(state.update_input(1, _frame(state)))
        self.assertTrue(state.update_input(
            1, _frame(state, siege_enabled=False)))
        self.assertEqual(SIEGE_SWITCHING_OFF, player.siege_state)
        siege_before = (player.siege_state, player.siege_transition_ticks)

        self.assertFalse(state.update_input(1, _frame(state, aim_yaw=7.0)))

        self.assertEqual(siege_before, (
            player.siege_state, player.siege_transition_ticks))
        self.assertEqual(3, player.input_processed_seq)

    def test_a_landing_observation_never_binds_to_a_rejected_input(self):
        state = _state(players=1)
        player = state.players[1]
        results = []
        player.offer_reliable = lambda message: (
            results.append(dict(message)) or True)
        self.assertTrue(state.update_input(1, _frame(state)))
        self.assertFalse(state.update_input(1, _frame(state, aim_yaw=7.0)))
        health_before = player.health

        self.assertFalse(state.submit_landing_observation(1, {
            'type': 'landing_observation', 'round_id': state.round_id,
            'authority_epoch': state.authority_epoch,
            'observation_seq': 1, 'input_seq': 2, 'impact_speed': 20.0,
        }))

        self.assertEqual('stale_input', results[-1]['reason'])
        self.assertEqual(health_before, player.health)

        # The applied frame that follows it can carry the observation.
        self.assertTrue(state.update_input(1, _frame(state)))
        self.assertTrue(state.submit_landing_observation(1, {
            'type': 'landing_observation', 'round_id': state.round_id,
            'authority_epoch': state.authority_epoch,
            'observation_seq': 1, 'input_seq': 3, 'impact_speed': 20.0,
        }))
        self.assertLess(player.health, health_before)

    def test_contacts_after_a_rejection_bind_to_the_applied_input(self):
        state = _state(players=1)
        player = state.players[1]
        state.human_collision_profiles[player.player_id] = {
            'shape': (1.5, 3.5, -0.8, 2.0),
            'ram_profile': {
                'spall_coefficient': 1.0, 'ramming_bonus': 0.0,
            },
        }
        self.assertTrue(state.update_input(1, _frame(state)))
        self.assertFalse(state.update_input(1, _frame(state, aim_yaw=7.0)))

        self.assertTrue(state.update_input(1, _frame(
            state, forward=1.0, speed=5.0,
            destructible_contacts=[_player_destructible_contact()])))

        self.assertEqual(3, player.input_seq)
        contact = player.destructible_contacts.get(1)
        if contact is not None:
            self.assertEqual(3, contact['input_seq'])
        else:
            self.assertIn(1, player.destructible_contact_rejections)

    def test_a_rejection_keeps_the_worker_authority_and_the_round(self):
        state = _state(players=1)
        player = state.players[1]
        worker = state.simulation_worker
        self.assertTrue(state.update_input(1, _frame(state)))

        self.assertFalse(state.update_input(1, _frame(state, aim_yaw=7.0)))

        # A recoverable rejection is contained to that one operation: it never
        # drops the hidden worker, ends the round or disconnects anyone.
        self.assertIs(worker, state.simulation_worker)
        self.assertTrue(worker.connected)
        self.assertEqual(
            SIMULATION_WORKER_AUTHORITY_ID, state.bot_authority_id)
        self.assertIsNone(state.battle_result)
        self.assertEqual('battle', state.phase)
        self.assertTrue(player.connected)
        self.assertEqual(2, player.input_processed_seq)

        self.assertTrue(state.update_input(1, _frame(state, forward=1.0)))
        self.assertEqual(3, player.input_seq)
        self.assertEqual(1.0, player.forward)

    def test_recovery_survives_a_worker_authority_epoch_change(self):
        state = _state(players=1)
        player = state.players[1]
        self.assertTrue(state.update_input(1, _frame(state)))
        self.assertFalse(state.update_input(1, _frame(state, aim_yaw=7.0)))

        # Worker loss and recovery inside the same round bump the authority
        # epoch; the ordered input ledger is per actor and round, so the next
        # legal frame still advances on the same frontier.
        state.authority_epoch += 1
        _attach_worker_authority(state)

        self.assertTrue(state.update_input(1, _frame(state, forward=1.0)))
        self.assertEqual(3, player.input_seq)
        self.assertEqual(3, player.input_processed_seq)
        self.assertEqual(1.0, player.forward)


if __name__ == '__main__':
    unittest.main()
