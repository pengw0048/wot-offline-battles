"""Planning cadence uses live humans without changing physical detail."""
import copy
import math
import unittest
from unittest import mock

import test_port_0922_bot_runtime as bot_fixture
import test_port_0922_battle_runtime as battle_fixture
from test_port_0922_authority_worker_client import _human
from gui.mods.offline_lan_0922 import authority_worker, lan_client


class BotPlanningTierTests(unittest.TestCase):
    def setUp(self):
        self.base = bot_fixture.BotRuntimeTests('test_runtime_owned_numeric_helpers_do_not_reparse_state')
        self.base.setUp()
        self.module = self.base.module
        self.now = 10.0
        self.epoch = 1
        self.generation = 1
        self.descriptor = bot_fixture._combat_descriptor(
            reload_time=4.0, clip=(8, 2.0), max_ammo=30, dispersion=.01)
        self.descriptor.gun.burst = (3, .1)
        self.command = dict(target_yaw=0., throttle=0., turn=0., shell_index=0,
            fire_allowed=False, target_id=None, fire_range=500.,
            combat_mode='route', aim_position=(0., 1., 100.),
            face_position=(0., 0., 100.), move_position=(0., 0., 0.),
            recovery_mode='arrived', movement_intent=False)
        self.adapter = bot_fixture._FixedAdapter(self.command)
        self.runtime = self.module.BotRuntime(
            1, descriptor_resolver=lambda unused: self.descriptor,
            adapter_factory=lambda *unused, **kwargs: self.adapter,
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.},
            visibility_probe=lambda *unused: False,
            firing_lane_probe=lambda *unused: True,
            friendly_lane_probe=lambda *unused: True,
            direct_launch_origin_probe=lambda state, *unused: (
                state['x'], state['y'] + 1., state['z']),
            ground_probe=lambda *unused: 0.,
            physics_ground_probe=lambda *unused: 0.,
            spawn_resolver=bot_fixture._spawn_resolver,
            baked_graph=bot_fixture._flat_open_graph(),
            control_seconds=self.module.WORKER_CONTROL_SECONDS)
        self.start = dict(self.base.start)
        self.runtime.battle_start(self.start)
        self.runtime.states[11].update(x=0., y=0., z=0., yaw=0., speed=0.,
            pitch=0., roll=0., terrain_pitch=0., grounded_once=True,
            turret_yaw=0., aim_yaw=0., gun_pitch=0.)
        self.runtime._next_observation = 1000.
        self.runtime._next_shot_lane_refresh = 1000.
        self.runtime._next_cover_refresh = 1000.

    def tearDown(self):
        self.runtime.close()
        self.base.tearDown()

    def human(self, identity=2, x=500., z=0., sequence=1, **values):
        result = dict(id=identity, team=1, participating=True, world_pose=True,
            alive=True, x=float(x), y=0., z=float(z), yaw=0., speed=0.,
            input_seq=sequence, health=1000, max_health=1000)
        result.update(values)
        return bot_fixture._admit_player(result)

    def snapshot(self, rows, round_id=None):
        return dict(round_id=self.runtime.round_id if round_id is None else round_id,
            authority_epoch=self.epoch, players=copy.deepcopy(rows))

    def step(self, rows, dt=.1, advance=True, message=None):
        if advance:
            self.now += dt
        self.runtime.set_planning_snapshot(
            self.snapshot(rows) if message is None else message,
            self.now, self.generation)
        physics = [row for row in rows if row.get('world_pose') is True and
            row.get('participating') is True and row.get('id', -1) > 0 and
            all(isinstance(row.get(key), (int, float)) and
                math.isfinite(row[key]) for key in ('x', 'y', 'z'))]
        return self.runtime.update(dt, self.now, players=physics)

    def warm(self, rows):
        self.runtime.set_planning_snapshot(self.snapshot(rows), self.now-.1,
                                           self.generation)
        for row in rows:
            row['input_seq'] += 1
        self.step(rows, advance=False)
        return rows

    def next_decision(self, rows):
        previous = self.runtime._decision_counts.get(11, 0)
        for unused in range(15):
            for row in rows:
                row['input_seq'] += 1
            self.step(rows)
            if self.runtime._decision_counts.get(11, 0) != previous:
                return
        self.fail('decision did not arrive')

    def far_cache(self):
        rows = self.warm([self.human()])
        self.next_decision(rows)
        self.assertEqual(.6, self.runtime._planning_decision_intervals[11])
        return rows

    def test_exact_thresholds_only_change_planning(self):
        rows = self.warm([self.human(x=150.)])
        state = self.runtime.states[11]
        for distance, expected in ((150., 0), (150.000001, 1),
                                   (350., 1), (350.000001, 2)):
            rows[0].update(x=distance, input_seq=rows[0]['input_seq']+1)
            self.step(rows)
            self.assertEqual(expected, self.runtime._planning_detail_tier(state))
            self.assertEqual(0, self.runtime._detail_tier(state))
            self.assertIsNone(self.runtime._camera_position)
        self.assertEqual(500000, self.runtime._sample_time_us)

    def test_multiple_humans_use_nearest_independent_of_team_and_order(self):
        rows = self.warm([self.human(x=600.), self.human(3, x=20., team=2)])
        state = self.runtime.states[11]
        self.assertEqual(0, self.runtime._planning_detail_tier(state))
        rows.reverse()
        for row in rows:
            row['input_seq'] += 1
        self.step(rows)
        self.assertEqual(0, self.runtime._planning_detail_tier(state))

    def test_near_and_fallback_preserve_first_stagger_through_next_update(self):
        rows = self.warm([self.human(x=5.)])
        cached = self.runtime._decision_cache[11]
        expected = self.module._cache_deadline(cached[2], 11, .15, 3, True)
        self.assertGreater(expected, cached[2]+.15)
        rows[0]['input_seq'] += 1
        self.step(rows)
        self.assertEqual(cached, self.runtime._decision_cache[11])
        self.assertEqual(expected, self.runtime._decision_cache[11][1])
        self.step([], dt=.01)
        self.assertEqual(expected, self.runtime._decision_cache[11][1])

    def test_bot_motion_across_boundary_promotes_before_visibility_begin(self):
        rows = self.far_cache()
        previous = self.runtime._decision_cache[11]
        self.runtime.states[11]['x'] = 490.
        seen = []
        original = self.runtime._prepare_visibility_frame
        def capture(*args, **kwargs):
            seen.append(self.runtime._decision_cache[11][1])
            return original(*args, **kwargs)
        self.runtime._prepare_visibility_frame = capture
        rows[0]['input_seq'] += 1
        self.step(rows)
        self.assertTrue(seen)
        self.assertLessEqual(seen[0], previous[2]+.15)

    def test_non_worker_camera_planning_and_slope_tier_are_preserved(self):
        self.runtime._fixed_control = False
        self.runtime.set_camera_position((1000., 0., 0.))
        self.step([])
        state = self.runtime.states[11]
        self.assertEqual(2, self.runtime._detail_tier(state))
        self.assertEqual(2, self.runtime._planning_detail_tier(state))
        self.assertEqual(.6, self.adapter.calls[-1][0]['decision_horizon'])
        cached = self.runtime._decision_cache[11]
        self.assertAlmostEqual(self.module._cache_deadline(
            cached[2], 11, .6, 3, True), cached[1])

    def test_invalid_pose_and_sequence_do_not_become_far_observers(self):
        rows = self.far_cache()
        for change in ({'x': float('nan')}, {'z': float('inf')},
                       {'input_seq': 0}, {'input_seq': None}):
            candidate = dict(rows[0], **change)
            self.step([candidate])
            self.assertEqual((), self.runtime._planning_positions)
            self.assertEqual(0, self.runtime._planning_detail_tier(
                self.runtime.states[11]))

    def test_unknown_participation_in_mixed_roster_restores_near_cadence(self):
        missing = object()
        for value in (missing, None, 0, 1, 'true', []):
            with self.subTest(participating=repr(value)):
                rows = self.far_cache()
                previous = self.runtime._decision_cache[11]
                unknown = self.human(3, x=5.)
                if value is missing:
                    unknown.pop('participating')
                else:
                    unknown['participating'] = value
                rows[0]['input_seq'] += 1
                self.step(rows + [unknown])
                self.assertEqual((), self.runtime._planning_positions)
                self.assertEqual(0, self.runtime._planning_detail_tier(
                    self.runtime.states[11]))
                current = self.runtime._decision_cache[11]
                self.assertEqual(previous[2], current[2])
                self.assertLessEqual(current[1], previous[2] + .15)

    def test_only_explicit_false_participant_is_ignored(self):
        rows = self.far_cache()
        previous = self.runtime._decision_cache[11]
        rows[0]['input_seq'] += 1
        self.step(rows + [dict(participating=False, id=99)])
        self.assertEqual(((500., 0., 0.),), self.runtime._planning_positions)
        self.assertEqual(2, self.runtime._planning_detail_tier(
            self.runtime.states[11]))
        self.assertEqual(previous, self.runtime._decision_cache[11])

    def test_only_exact_worker_identity_bypasses_participant_validation(self):
        rows = self.far_cache()
        previous = self.runtime._decision_cache[11]
        rows[0]['input_seq'] += 1
        self.step(rows + [dict(id=self.module.lan_client.WORKER_AUTHORITY_ID)])
        self.assertEqual(2, self.runtime._planning_detail_tier(
            self.runtime.states[11]))
        self.assertEqual(previous, self.runtime._decision_cache[11])
        for identity in (-2, 0, None):
            with self.subTest(identity=identity):
                rows = self.far_cache()
                previous = self.runtime._decision_cache[11]
                rows[0]['input_seq'] += 1
                self.step(rows + [dict(id=identity)])
                self.assertEqual((), self.runtime._planning_positions)
                self.assertEqual(0, self.runtime._planning_detail_tier(
                    self.runtime.states[11]))
                self.assertLessEqual(self.runtime._decision_cache[11][1],
                    previous[2] + .15)

    def test_first_lease_and_horizon_keep_original_near_phase(self):
        self.warm([self.human(x=500.)])
        cached = self.runtime._decision_cache[11]
        expected = self.module._cache_deadline(cached[2], 11, .15, 3, True)
        self.assertAlmostEqual(expected, cached[1])
        self.assertLess(cached[1] - cached[2], .3)
        self.assertEqual(.15, self.adapter.calls[-1][0]['decision_horizon'])

    def test_later_far_decision_uses_existing_interval_and_horizon(self):
        self.far_cache()
        cached = self.runtime._decision_cache[11]
        self.assertAlmostEqual(.6, cached[1] - cached[2])
        self.assertEqual(.6, self.adapter.calls[-1][0]['decision_horizon'])

    def test_near_crossing_shortens_from_original_decision_time(self):
        rows = self.far_cache()
        original = self.runtime._decision_cache[11]
        rows[0].update(x=10., input_seq=rows[0]['input_seq']+1)
        self.step(rows)
        cached = self.runtime._decision_cache[11]
        self.assertEqual(original[2], cached[2])
        self.assertAlmostEqual(original[2] + .15, cached[1])
        for unused in range(2):
            rows[0]['input_seq'] += 1
            self.step(rows)
        self.assertGreater(self.runtime._decision_cache[11][2], original[2])

    def test_new_near_human_does_not_wait_old_far_lease(self):
        rows = self.far_cache()
        previous = self.runtime._decision_cache[11]
        rows.append(self.human(3, x=5.))
        self.step(rows)
        current = self.runtime._decision_cache[11]
        self.assertAlmostEqual(previous[2]+.15, current[1])
        self.assertEqual(0, self.runtime._planning_detail_tier(self.runtime.states[11]))

    def test_far_demotion_never_extends_existing_deadline(self):
        rows = self.warm([self.human(x=5.)])
        self.next_decision(rows)
        previous = self.runtime._decision_cache[11]
        rows[0].update(x=600., input_seq=rows[0]['input_seq']+1)
        self.step(rows)
        self.assertEqual(previous, self.runtime._decision_cache[11])

    def test_stale_inputs_with_fresh_snapshots_restore_near_cadence(self):
        rows = self.far_cache()
        last_progress = self.runtime._planning_progress[2][1]
        while self.now + .10000001 < last_progress + 2.:
            self.step(rows)
        self.now = last_progress + 2.
        self.step(rows, advance=False)
        self.assertEqual((), self.runtime._planning_positions)
        cached = self.runtime._decision_cache[11]
        self.assertLessEqual(cached[1], cached[2] + .15000001)
        rows[0]['input_seq'] += 1
        self.step(rows)
        self.assertEqual(2, self.runtime._planning_detail_tier(self.runtime.states[11]))

    def test_first_seen_input_and_regression_require_progress(self):
        rows = [self.human(sequence=50)]
        self.step(rows)
        self.assertEqual((), self.runtime._planning_positions)
        rows[0]['input_seq'] = 51
        self.step(rows)
        self.assertTrue(self.runtime._planning_positions)
        rows[0]['input_seq'] = 3
        self.step(rows)
        self.assertEqual((), self.runtime._planning_positions)
        rows[0]['input_seq'] = 4
        self.step(rows)
        self.assertTrue(self.runtime._planning_positions)

    def test_no_human_and_dead_or_unknown_participant_restore_near(self):
        rows = self.far_cache()
        for extra in (self.human(3, alive=False),
                      self.human(3, world_pose=False)):
            rows[0]['input_seq'] += 1
            self.step(rows+[extra])
            self.assertEqual(0, self.runtime._planning_detail_tier(self.runtime.states[11]))
        self.step([])
        self.assertEqual({}, self.runtime._planning_progress)
        self.assertEqual((), self.runtime._planning_positions)

    def test_wrong_round_epoch_session_and_close_clear_observer_progress(self):
        rows = self.far_cache()
        self.step(rows, message=self.snapshot(rows, self.runtime.round_id+1))
        self.assertIsNone(self.runtime._planning_owner)
        self.assertEqual((), self.runtime._planning_positions)
        self.assertLessEqual(self.runtime._decision_cache[11][1],
                             self.runtime._decision_cache[11][2]+.15000001)
        rows[0]['input_seq'] += 1
        self.step(rows)
        rows[0]['input_seq'] += 1
        self.step(rows)
        self.assertTrue(self.runtime._planning_positions)
        self.epoch += 1
        rows[0]['input_seq'] += 1
        self.step(rows)
        self.assertFalse(self.runtime._planning_positions)
        rows[0]['input_seq'] += 1
        self.step(rows)
        self.assertTrue(self.runtime._planning_positions)
        self.generation += 1
        self.step(rows)
        self.assertFalse(self.runtime._planning_positions)
        self.runtime.close()
        self.assertEqual({}, self.runtime._planning_progress)
        self.assertIsNone(self.runtime._planning_owner)

    def test_actual_new_round_restarts_input_observation(self):
        rows = self.far_cache()
        next_start = dict(self.start, round_id=self.start['round_id']+1)
        self.runtime.battle_start(next_start)
        self.runtime.states[11].update(x=0., y=0., z=0., speed=0.)
        self.step(rows)
        self.assertEqual((), self.runtime._planning_positions)
        rows[0]['input_seq'] += 1
        self.step(rows)
        self.assertTrue(self.runtime._planning_positions)

    def test_expired_setter_data_is_not_reused_by_direct_update(self):
        self.far_cache()
        self.now += 2.1
        self.runtime.update(.1, self.now, players=[])
        self.assertEqual((), self.runtime._planning_positions)

    def test_accepted_burst_crosses_reused_decision_and_near_promotion(self):
        rows = self.far_cache()
        runtime = self.runtime
        state = runtime.states[11]
        gun = runtime._gun_states[11]
        ammo = runtime._ammo_states[11]
        gun.elapsed = 10.
        preview = runtime._direct_launch_preview(
            state, self.descriptor, ammo.loaded, gun, {'flight_time': .5})
        before = sum(ammo.remaining)
        start_us = runtime._sample_time_us
        self.assertTrue(runtime._fire(state, gun, 1., self.descriptor,
            ammo_state=ammo, launch_preview=preview, launch_time_us=start_us))
        decision_count = runtime._decision_counts[11]
        rows[0]['input_seq'] += 1
        self.step(rows)
        self.assertEqual(decision_count, runtime._decision_counts[11])
        rows[0].update(x=10., input_seq=rows[0]['input_seq']+1)
        self.step(rows)
        launches = runtime._pending_launches
        self.assertEqual([1, 2, 3], [row['fire_seq'] for row in launches])
        self.assertEqual([start_us, start_us+100000, start_us+200000],
                         [row['launch_time_us'] for row in launches])
        self.assertEqual(before-3, sum(ammo.remaining))
        self.assertFalse(runtime._burst_states[11].active)
        self.assertEqual(start_us+200000, runtime._sample_time_us)
        for row in list(launches):
            self.assertTrue(runtime.ack_projectile_launch(row['id'], row['fire_seq']))
        self.assertFalse(runtime._pending_launches)



class WorkerPlanningIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.planning = BotPlanningTierTests('test_exact_thresholds_only_change_planning')
        self.planning.setUp()
        self.addCleanup(self.planning.tearDown)
        self.bots = self.planning.runtime
        self.bots.local_player_id = lan_client.WORKER_AUTHORITY_ID
        self.bots.authority_id = lan_client.WORKER_AUTHORITY_ID
        self.native = battle_fixture._runtime()
        self.native.bigworld.now = 10.
        self.battle = battle_fixture.BattleRuntime(self.native)
        self.battle._worker_mode = True
        self.battle._generation = 7
        self.battle._fail = lambda error: self.fail('Worker frame failed: %s' % error)
        self.battle.state = 'running'
        self.battle._battle_live = True
        self.battle._last_frame_time = 10.
        self.battle._avatar = self.native.bigworld.avatar
        self.battle._bots = self.bots
        for name in ('_flush_pending_bot_create', '_flush_pending_entities',
                     '_drain_event_journal', '_maybe_send_battle_ready',
                     '_apply_authority_bot_poses', '_schedule',
                     '_resolve_player_destructible_contacts'):
            setattr(self.battle, name, mock.Mock())
        self.battle._projectile_is_authority = lambda: False
        self.battle._enqueue_bot_message = lambda message: True
        self.events = []
        self.calls = []
        original_setter = self.bots.set_planning_snapshot
        original_update = self.bots.update
        def setter(message, now, generation):
            self.calls.append(('planning', [row['id'] for row in message['players']]))
            return original_setter(message, now, generation)
        def update(dt, now, players):
            self.calls.append(('update', [row['id'] for row in players]))
            return original_update(dt, now, players=players)
        self.bots.set_planning_snapshot = setter
        self.bots.update = update

        self.wire = lan_client
        self.human = _human(1)
        self.human.update(participating=True, input_seq=1, x=500., y=0., z=0.)
        def on_event(kind, message):
            self.events.append((kind, message))
            if kind == 'snapshot':
                self.assertTrue(self.battle.on_snapshot(message))
        self.client = authority_worker.AuthorityWorkerLANClient(
            '127.0.0.1', 28782, on_event=on_event)
        self.battle.client = self.client
        self.client.ready = True
        self.client.phase = 'waiting'
        self.client.round_id = 0
        self.client.authority_epoch = 0
        self.client.bot_authority_id = lan_client.WORKER_AUTHORITY_ID
        self.client.capabilities = list(lan_client.CLIENT_CAPABILITIES)
        self.client.server_capabilities = list(lan_client.CLIENT_CAPABILITIES)
        self.client._handle_message(dict(
            type='battle_start', protocol=lan_client.PROTOCOL_VERSION,
            map='01_karelia', phase='loading', round_id=5,
            state_revision=1, host_player_id=1,
            bot_authority_id=lan_client.WORKER_AUTHORITY_ID,
            authority_epoch=1, server_time_ms=10000,
            players=[dict(self.human)], bots=[]))
        self.assertIsNone(self.client.last_error)
        self.assertEqual('battle_start', self.events[-1][0])
        self.assertEqual([1, -1], [row['id'] for row in self.events[-1][1]['players']])
        self.client.phase = 'battle'
        self.tick = 0

    def frame(self, humans=None):
        self.tick += 1
        self.human['input_seq'] += 1
        self.native.bigworld.now += .1
        self.client._handle_message(dict(
            type='snapshot', protocol=self.wire.PROTOCOL_VERSION,
            round_id=5, server_tick=self.tick,
            server_time_ms=10000 + 100 * self.tick, authority_epoch=1,
            projectile_revision=0, bot_state_revision=0,
            bot_authority_id=self.wire.WORKER_AUTHORITY_ID,
            bot_manifest=[], players=[dict(self.human)] if humans is None else humans,
            bots=[], projectiles=[]))
        self.assertIsNone(self.client.last_error)
        self.assertEqual('snapshot', self.events[-1][0])
        self.assertEqual(self.tick, self.battle._last_snapshot['server_tick'])
        self.battle._frame()

    def warm_far(self):
        for unused in range(10):
            self.frame()
            if self.bots._planning_decision_intervals.get(11) == .6:
                return
        self.fail('Actual worker projection never reached far cadence')

    def test_actual_worker_carrier_keeps_far_human_eligible(self):
        self.warm_far()
        projected = self.client.last_snapshot['players']
        carrier = [row for row in projected if row['id'] == -1][0]
        self.assertNotIn('participating', carrier)
        self.assertEqual(0, carrier['input_seq'])
        self.assertEqual(((500., 0., 0.),), self.bots._planning_positions)
        self.assertEqual(2, self.bots._planning_detail_tier(self.bots.states[11]))
        self.assertEqual(.6, self.planning.adapter.calls[-1][0]['decision_horizon'])
        self.assertIsNone(self.bots._camera_position)
        self.assertEqual(0, self.bots._detail_tier(self.bots.states[11]))
        self.assertGreater(self.bots._sample_time_us, 0)
        self.assertEqual(['planning', 'update'] * self.tick,
                         [row[0] for row in self.calls])
        self.assertTrue(all(ids == [1, -1] for kind, ids in self.calls if kind == 'planning'))
        self.assertTrue(all(ids == [1] for kind, ids in self.calls if kind == 'update'))

    def test_actual_projection_preserves_unknown_real_participant_fallback(self):
        self.warm_far()
        old = self.bots._decision_cache[11]
        near = dict(self.human, id=2, x=5.)
        near.pop('participating')
        self.frame([dict(self.human), near])
        self.assertEqual([1, 2, -1], self.calls[-2][1])
        self.assertEqual([1], self.calls[-1][1])
        self.assertEqual((), self.bots._planning_positions)
        self.assertEqual(0, self.bots._planning_detail_tier(self.bots.states[11]))
        self.assertEqual(old[2], self.bots._decision_cache[11][2])
        self.assertLessEqual(self.bots._decision_cache[11][1], old[2] + .15)



class PlanningInputProgressTests(unittest.TestCase):
    def test_stationary_inputs_still_advance_the_actual_client_sequence(self):
        client = lan_client.LANClient('127.0.0.1', 20000, 'Player', 'ussr:T-34')
        client.ready = True
        client.phase = 'battle'
        client.round_id = 5
        client.capabilities = [lan_client.HUMAN_RAM_TIMELINE_CAPABILITY,
                               lan_client.PLAYER_PROJECTILE_OWNER_CAPABILITY]
        client.server_capabilities = list(client.capabilities)
        sent = []
        client._send = lambda value: sent.append(value) or True
        for index in range(3):
            self.assertTrue(client.send_input(0., 0., position=(150., 0., 0.),
                yaw=0., speed=0., pose_time_us=100000*(index+1),
                shell_index=0, next_shell_index=0, shell_change_pending=False,
                gun_checkpoint=dict(reload_time=0., reload_duration=4.,
                                    clip=8, clip_size=8, dispersion=.1)))
        self.assertEqual([1, 2, 3], [row['input_seq'] for row in sent])
        self.assertEqual([(150., 0., 0.)] * 3,
            [(row['x'], row['y'], row['z']) for row in sent])


if __name__ == '__main__':
    unittest.main()
