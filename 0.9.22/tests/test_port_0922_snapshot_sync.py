import importlib.util
import math
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = (ROOT / '0.9.22' / 'src' / 'res' / 'scripts' /
                'client' / 'gui' / 'mods' / 'offline_lan_0922')


def _load():
    for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(PACKAGE_ROOT)]
            sys.modules[name] = module
    name = 'gui.mods.offline_lan_0922.snapshot_sync'
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, PACKAGE_ROOT / 'snapshot_sync.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def player(identifier, x=0, alive=True):
    return {'id': identifier, 'name': 'P%s' % identifier, 'vehicle': 'ussr:T-34',
            'team': 1, 'slot': 0, 'x': x, 'y': 0, 'z': 0, 'yaw': 0,
            'health': 100, 'max_health': 100, 'alive': alive}


class SnapshotSyncTests(unittest.TestCase):
    def setUp(self):
        self.module = _load()
        self.now = [0.0]
        self.callback = []
        self.sync = self.module.SnapshotSync(1, self.callback.append,
                                              clock=lambda: self.now[0])

    def test_manifest_creates_players_and_bots_once(self):
        message = {'round_id': 3, 'players': [player(1), player(2)],
                   'bots': [{'id': 7, 'vehicle': 'germany:PzI', 'team': 2}]}
        events = self.sync.manifest(message) + self.sync.manifest(message)

        self.assertEqual(['player:1', 'player:2', 'bot:7'],
                         [event['entity'] for event in events])
        self.assertEqual(events, self.callback)

    def test_local_is_server_correction_remote_interpolates_and_predicts_50ms(self):
        self.sync.manifest({'round_id': 1, 'players': [player(1), player(2)]})
        first = self.sync.snapshot({'round_id': 1, 'server_tick': 1,
                                    'players': [player(1, 3), player(2, 2)], 'bots': []})
        self.assertTrue([event for event in first if event.get('correction')])
        self.assertEqual(2.0, [event for event in first if event['entity'] == 'player:2'][0]['pose']['x'])

        self.now[0] = 0.1
        self.sync.snapshot({'round_id': 1, 'server_tick': 2,
                            'players': [player(1, 4), player(2, 10)], 'bots': []})
        self.now[0] = 0.15
        event = self.sync.advance()[0]
        self.assertTrue(event['interpolated'])
        self.assertGreater(event['pose']['x'], 2.0)
        self.assertLessEqual(event['pose']['x'], 14.0)

    def test_large_remote_gap_snaps_at_25_metres(self):
        self.sync.manifest({'round_id': 1, 'players': [player(2)]})
        self.sync.snapshot({'round_id': 1, 'server_tick': 1, 'players': [player(2, 0)]})
        self.now[0] = 0.1
        self.sync.snapshot({'round_id': 1, 'server_tick': 2, 'players': [player(2, 40)]})
        events = self.sync.advance(0.1)
        self.assertTrue(events[0]['snap'])
        self.assertEqual(40.0, events[0]['pose']['x'])

    def test_remote_bot_prediction_does_not_cross_a_baked_hazard(self):
        sync = self.module.SnapshotSync(
            1, clock=lambda: self.now[0],
            pose_safe=lambda pose: pose[0] < 5.0)
        bot = player(7, 4.0)
        sync.manifest({'round_id': 1, 'bots': [bot]})
        sync.snapshot({'round_id': 1, 'server_tick': 1, 'bots': [bot]})
        self.now[0] = 0.1
        moved = player(7, 4.8)
        sync.snapshot({'round_id': 1, 'server_tick': 2, 'bots': [moved]})

        event = sync.advance(0.15)[0]

        self.assertLessEqual(event['pose']['x'], 4.8)

    def test_authoritative_fallen_bot_pose_is_not_rewound(self):
        sync = self.module.SnapshotSync(
            1, clock=lambda: self.now[0],
            pose_safe=lambda pose: pose[0] < 5.0)
        initial = player(7, 4.0)
        fallen = player(7, 6.0)
        fallen['y'] = -8.0
        sync.manifest({'round_id': 1, 'bots': [initial]})
        sync.snapshot({'round_id': 1, 'server_tick': 1,
                       'bots': [initial]})
        self.now[0] = 0.1
        sync.snapshot({'round_id': 1, 'server_tick': 2,
                       'bots': [fallen]})

        event = sync.advance(0.1)[0]

        self.assertGreater(event['pose']['x'], initial['x'])
        self.assertLess(event['pose']['y'], initial['y'])

    def test_timed_confirmed_fall_is_not_filtered_as_prediction(self):
        sync = self.module.SnapshotSync(
            1, clock=lambda: self.now[0],
            pose_safe=lambda pose: pose[0] < 5.0)
        initial = player(7, 4.0)
        fallen = player(7, 6.0)
        fallen['y'] = -8.0
        sync.snapshot({
            'round_id': 1, 'server_tick': 1,
            'bot_state_revision': 1,
            'motion_time_us': 0, 'bot_state_time_us': 0,
            'bots': [initial]})
        for tick in range(1, 10):
            sync.advance(tick / 100.0)
        self.now[0] = 0.1
        sync.snapshot({
            'round_id': 1, 'server_tick': 2,
            'bot_state_revision': 2,
            'motion_time_us': 100000, 'bot_state_time_us': 100000,
            'bots': [fallen]})

        event = None
        for tick in range(10, 31):
            event = sync.advance(tick / 100.0)[0]

        self.assertAlmostEqual(6.0, event['pose']['x'])
        self.assertAlmostEqual(-8.0, event['pose']['y'])

    def test_pose_safety_error_is_not_silently_ignored(self):
        def fail(unused_pose):
            raise ValueError('broken graph')

        sync = self.module.SnapshotSync(
            1, clock=lambda: self.now[0], pose_safe=fail)
        first = player(7, 1.0)
        second = player(7, 2.0)
        sync.manifest({'round_id': 1, 'bots': [first]})
        sync.snapshot({'round_id': 1, 'server_tick': 1, 'bots': [first]})
        self.now[0] = 0.1
        sync.snapshot({'round_id': 1, 'server_tick': 2, 'bots': [second]})

        with self.assertRaisesRegex(ValueError, 'broken graph'):
            sync.advance(0.15)

    def test_remote_angles_take_short_path_across_pi_and_aim_is_smoothed(self):
        initial = player(2)
        initial.update(yaw=math.pi - 0.05, aim_yaw=math.pi - 0.10,
                       pitch=-0.18, roll=0.12, gun_pitch=-0.2)
        target = player(2)
        target.update(yaw=-math.pi + 0.05, aim_yaw=-math.pi + 0.10,
                      pitch=0.18, roll=-0.12, gun_pitch=0.2)
        self.sync.manifest({'round_id': 1, 'players': [initial]})
        self.sync.snapshot({'round_id': 1, 'server_tick': 1,
                            'players': [initial]})
        self.now[0] = 0.1
        self.sync.snapshot({'round_id': 1, 'server_tick': 2,
                            'players': [target]})

        pose = self.sync.advance(0.116)[0]['pose']

        self.assertLess(abs(self.module._angle_delta(initial['yaw'],
                                                      pose['yaw'])), 0.1)
        self.assertLess(abs(self.module._angle_delta(initial['aim_yaw'],
                                                      pose['aim_yaw'])), 0.2)
        self.assertGreater(pose['pitch'], initial['pitch'])
        self.assertLess(pose['roll'], initial['roll'])
        self.assertGreater(pose['gun_pitch'], initial['gun_pitch'])

    def test_same_bot_revision_updates_combat_state_without_restarting_pose(self):
        bot = player(7, 0.0)
        self.sync.manifest({'round_id': 1, 'bots': [bot]})
        self.sync.snapshot({
            'round_id': 1, 'server_tick': 1,
            'bot_state_revision': 1, 'bots': [bot]})
        self.now[0] = 0.1
        moved = player(7, 1.0)
        self.sync.snapshot({
            'round_id': 1, 'server_tick': 2,
            'bot_state_revision': 2, 'bots': [moved]})
        record = self.sync._entities['bot:7']
        target_time = record['target_time']
        velocity = record['velocity']

        self.now[0] = 0.133
        damaged = player(7, 99.0)
        damaged['health'] = 50
        damaged['critical'] = {'fire': True}
        events = self.sync.snapshot({
            'round_id': 1, 'server_tick': 3,
            'bot_state_revision': 2, 'bots': [damaged]})

        self.assertEqual(target_time, record['target_time'])
        self.assertEqual(velocity, record['velocity'])
        self.assertEqual(1.0, record['target']['x'])
        update = next(event for event in events
                      if event.get('entity') == 'bot:7')
        self.assertEqual(50, update['state']['health'])
        self.assertEqual({'fire': True}, update['state']['critical'])
        self.assertEqual(1.0, update['target']['x'])
        self.assertIsNone(update['pose'])

        self.now[0] = 0.166
        dead = player(7, 1.0, alive=False)
        destroyed = self.sync.snapshot({
            'round_id': 1, 'server_tick': 4,
            'bot_state_revision': 2, 'bots': [dead]})
        self.assertEqual('destroy', destroyed[0]['type'])
        self.assertEqual(target_time, record['target_time'])
        self.assertEqual(velocity, record['velocity'])

    def test_remote_snapshot_retargets_without_inserting_a_stationary_pose(self):
        bot = player(7, 0.0)
        self.sync.manifest({'round_id': 1, 'bots': [bot]})
        first = self.sync.snapshot({
            'round_id': 1, 'server_tick': 1,
            'bot_state_revision': 1, 'bots': [bot]})
        self.assertEqual(0.0, first[-1]['pose']['x'])

        self.now[0] = 0.05
        moved = player(7, 0.5)
        retargeted = self.sync.snapshot({
            'round_id': 1, 'server_tick': 2,
            'bot_state_revision': 2, 'bots': [moved]})

        update = retargeted[-1]
        self.assertIsNone(update['pose'])
        self.assertEqual(0.5, update['target']['x'])
        self.assertGreater(self.sync.advance(0.06)[0]['pose']['x'], 0.0)

    def test_18hz_bot_source_stays_smooth_through_30hz_repeated_snapshots(self):
        clock = [0.0]
        sync = self.module.SnapshotSync(1, clock=lambda: clock[0])

        def bot(position):
            state = player(7, position)
            state.update(team=2, yaw=0.0, aim_yaw=0.0, gun_pitch=0.0)
            return state

        sync.manifest({'round_id': 1, 'bots': [bot(0.0)]})
        revision = 0
        published_x = 0.0
        last_snapshot_revision = None
        repeated_snapshots = 0
        zeroed_repeat_velocities = 0
        presented = []
        # 180 Hz is the exact common clock for an 18 Hz authority, a 30 Hz
        # server and a 60 Hz renderer.  Authority publication happens before
        # the same-instant server snapshot, matching an already accepted batch.
        for clock_tick in range(3 * 180 + 1):
            now = clock_tick / 180.0
            clock[0] = now
            if clock_tick % 10 == 0:
                revision += 1
                published_x = 10.0 * now
            if clock_tick % 6 == 0:
                repeated = revision == last_snapshot_revision
                sync.snapshot({
                    'round_id': 1, 'server_tick': clock_tick // 6,
                    'bot_state_revision': revision,
                    'bots': [bot(published_x)]})
                if repeated and revision >= 2:
                    repeated_snapshots += 1
                    velocity = sync._entities['bot:7']['velocity']
                    speed = math.sqrt(sum(value * value
                                          for value in velocity))
                    if speed <= 1.0e-9:
                        zeroed_repeat_velocities += 1
                last_snapshot_revision = revision
            if clock_tick % 3 == 0:
                presented.append(sync.advance(now)[0]['pose']['x'])

        render_speeds = [
            (presented[index] - presented[index - 1]) * 60.0
            for index in range(61, len(presented))]
        self.assertGreater(repeated_snapshots, 30)
        self.assertEqual(0, zeroed_repeat_velocities)
        self.assertGreater(min(render_speeds), 5.0)
        self.assertAlmostEqual(
            10.0, sum(render_speeds) / len(render_speeds), places=6)

    def test_timed_18_to_30hz_authority_has_no_speed_pulse_at_60_to_100fps(self):
        def run(authority_hz, render_hz):
            clock = [0.0]
            sync = self.module.SnapshotSync(1, clock=lambda: clock[0])

            def bot(position):
                state = player(7, position)
                state.update(team=2, yaw=0.0, aim_yaw=0.0,
                             gun_pitch=0.0)
                return state

            sync.manifest({'round_id': 1, 'bots': [bot(0.0)]})
            revision = 0
            published_x = 0.0
            sample_time_us = 0
            presented = []
            # 900 Hz is a common exact clock for 18/30 Hz authority,
            # 30 Hz server snapshots, and 60/100 Hz presentation.
            for clock_tick in range(4 * 900 + 1):
                now = clock_tick / 900.0
                clock[0] = now
                if clock_tick % (900 // authority_hz) == 0:
                    revision += 1
                    published_x = 10.0 * now
                    sample_time_us = int(round(now * 1000000.0))
                if clock_tick % 30 == 0:
                    sync.snapshot({
                        'round_id': 1, 'server_tick': clock_tick // 30,
                        'bot_state_revision': revision,
                        'motion_time_us': int(round(now * 1000000.0)),
                        'bot_state_time_us': sample_time_us,
                        'bots': [bot(published_x)]})
                if clock_tick % (900 // render_hz) == 0:
                    presented.append((now, sync.advance(now)[0]['pose']['x']))

            render_speeds = [
                (presented[index][1] - presented[index - 1][1]) /
                (presented[index][0] - presented[index - 1][0])
                for index in range(1, len(presented))
                if presented[index][0] >= 2.0]
            self.assertLess(max(render_speeds) - min(render_speeds), 0.01)
            # Adaptive buffer decay uses a constant 1.005x catch-up while
            # settling; it must not create a speed pulse or materially change
            # the authority trajectory's mean velocity.
            self.assertAlmostEqual(
                10.0, sum(render_speeds) / len(render_speeds), delta=0.051)

        for authority_hz in (18, 30):
            for render_hz in (60, 100):
                with self.subTest(authority_hz=authority_hz,
                                  render_hz=render_hz):
                    run(authority_hz, render_hz)

    def test_socket_dispatch_delay_does_not_shift_the_motion_clock_anchor(self):
        clock = [0.016]
        sync = self.module.SnapshotSync(1, clock=lambda: clock[0])
        bot = player(7, 0.0)

        sync.snapshot({
            'round_id': 1, 'server_tick': 1,
            'bot_state_revision': 1,
            'motion_time_us': 0, 'bot_state_time_us': 0,
            '_client_dispatch_delay': 0.016,
            'bots': [bot],
        })
        record = sync._entities['bot:7']
        self.assertAlmostEqual(0.0, record['motion_anchor_local_time'])
        authority_now_us = (
            record['motion_anchor_time_us'] +
            (clock[0] - record['motion_anchor_local_time']) * 1000000.0)
        self.assertAlmostEqual(16000.0, authority_now_us)

        # A different 60 Hz poll phase still reconstructs the same advancing
        # authority clock instead of resetting it to the older server stamp.
        clock[0] = 0.117
        moved = player(7, 1.0)
        sync.snapshot({
            'round_id': 1, 'server_tick': 2,
            'bot_state_revision': 2,
            'motion_time_us': 100000, 'bot_state_time_us': 100000,
            '_client_dispatch_delay': 0.017,
            'bots': [moved],
        })
        record = sync._entities['bot:7']
        self.assertAlmostEqual(0.100, record['motion_anchor_local_time'])
        authority_now_us = (
            record['motion_anchor_time_us'] +
            (clock[0] - record['motion_anchor_local_time']) * 1000000.0)
        self.assertAlmostEqual(117000.0, authority_now_us)

    def test_timed_gap_and_sudden_stop_never_extrapolate_or_backtrack(self):
        clock = [0.0]
        sync = self.module.SnapshotSync(1, clock=lambda: clock[0])

        def bot(position):
            state = player(7, position)
            state.update(team=2, yaw=0.0, aim_yaw=0.0,
                         gun_pitch=0.0)
            return state

        sync.manifest({'round_id': 1, 'bots': [bot(0.0)]})
        # Authority first moves at 10 m/s, then only advances 0.12 m across an
        # abnormal 170 ms worker gap and stops. A short confirmed-pose hold is
        # safe here; the old timed extrapolator instead presented
        # more than two metres and visibly pulled the bot back on revision 3.
        snapshots = (
            (0.000, 0.000, 0.00, 1),
            (0.034, 0.000, 0.00, 1),
            (0.068, 0.068, 0.68, 2),
            (0.102, 0.068, 0.68, 2),
            (0.136, 0.068, 0.68, 2),
            (0.170, 0.068, 0.68, 2),
            (0.204, 0.068, 0.68, 2),
            (0.238, 0.238, 0.80, 3),
            (0.272, 0.238, 0.80, 3),
            (0.306, 0.306, 0.80, 4),
            (0.340, 0.306, 0.80, 4),
            (0.374, 0.306, 0.80, 4),
            (0.408, 0.306, 0.80, 4),
        )
        presented = []
        next_render = 0.0
        for server_time, sample_time, position, revision in snapshots:
            while next_render < server_time - 1.0e-9:
                clock[0] = next_render
                if sync._entities['bot:7']['target'] is not None:
                    pose = sync.advance(next_render)[0]['pose']['x']
                    confirmed = sync._entities['bot:7']['target']['x']
                    presented.append((next_render, pose, confirmed))
                next_render += 0.01
            clock[0] = server_time
            sync.snapshot({
                'round_id': 1,
                'server_tick': int(round(server_time * 1000.0)),
                'bot_state_revision': revision,
                'motion_time_us': int(round(server_time * 1000000.0)),
                'bot_state_time_us': int(round(sample_time * 1000000.0)),
                'bots': [bot(position)],
            })
            pose = sync.advance(server_time)[0]['pose']['x']
            presented.append((server_time, pose, position))
        while next_render <= 0.6:
            clock[0] = next_render
            pose = sync.advance(next_render)[0]['pose']['x']
            confirmed = sync._entities['bot:7']['target']['x']
            presented.append((next_render, pose, confirmed))
            next_render += 0.01

        positions = [item[1] for item in presented]
        self.assertTrue(all(
            positions[index] + 1.0e-9 >= positions[index - 1]
            for index in range(1, len(positions))))
        self.assertTrue(all(
            pose <= confirmed + 1.0e-9
            for unused_time, pose, confirmed in presented))
        self.assertLessEqual(max(positions), 0.80 + 1.0e-9)
        self.assertAlmostEqual(0.80, positions[-1], places=6)

    def test_timed_jitter_buffer_covers_measured_worker_gaps_at_100fps(self):
        clock = [0.0]
        sync = self.module.SnapshotSync(1, clock=lambda: clock[0])

        def bot(sample_time_us):
            state = player(7, sample_time_us / 100000.0)
            state.update(team=2, yaw=0.0, aim_yaw=0.0,
                         gun_pitch=0.0)
            return state

        # QPC-fixed worker evidence is normally 40-50 ms and reached 66 ms.
        # The 30 Hz snapshot exposure can make the adaptive high-water mark
        # briefly approach 100 ms without retaining that as normal latency.
        measured_gaps_us = (
            40000, 42000, 50000, 43000,
            66000, 40000, 45000, 41000)
        source_times = [0]
        gap_index = 0
        while source_times[-1] < 4200000:
            source_times.append(
                source_times[-1] +
                measured_gaps_us[gap_index % len(measured_gaps_us)])
            gap_index += 1
        server_times = list(range(0, 4200001, 33333))
        render_times = list(range(0, 3800001, 10000))
        source_set = set(source_times)
        server_set = set(server_times)
        render_set = set(render_times)
        revision = 0
        sample_time_us = 0
        presented = []
        for time_us in sorted(source_set | server_set | render_set):
            if time_us in source_set:
                revision += 1
                sample_time_us = time_us
            clock[0] = time_us / 1000000.0
            if time_us in server_set:
                sync.snapshot({
                    'round_id': 1, 'server_tick': time_us,
                    'bot_state_revision': revision,
                    'motion_time_us': time_us,
                    'bot_state_time_us': sample_time_us,
                    'bots': [bot(sample_time_us)],
                })
            if time_us in render_set:
                event = sync.advance(clock[0])[0]
                record = sync._entities['bot:7']
                presented.append((
                    time_us, event['pose']['x'],
                    record['presentation_time_us'],
                    record['target_sample_time_us'],
                    record['presentation_delay_us'],
                    record['interpolation_delay_us']))

        # Ignore only the 90 ms startup fill. The measured trace remains
        # continuous at 100 FPS, while actual presentation delay stays at or
        # below 90 ms even when the safety target reacts to the 66 ms sample.
        steady = [item for item in presented
                  if 250000 <= item[0] <= 3600000]
        steps = [steady[index][1] - steady[index - 1][1]
                 for index in range(1, len(steady))]
        delays = [item[4] for item in steady if item[4] is not None]
        ideal_delays = [item[5] for item in steady if item[5] is not None]
        self.assertGreater(min(steps), 0.099)
        self.assertLessEqual(max(steps), 0.101)
        self.assertEqual(60000.0, self.module.MIN_TIMED_DELAY_US)
        self.assertEqual(90000.0, self.module.INITIAL_TIMED_DELAY_US)
        self.assertGreaterEqual(min(delays), 60000.0)
        self.assertLessEqual(max(delays), 90000.0)
        self.assertGreaterEqual(max(ideal_delays), 99000.0)
        self.assertTrue(all(
            steady[index][2] >= steady[index - 1][2]
            for index in range(1, len(steady))))
        self.assertTrue(all(
            presentation_time <= confirmed_time
            for unused_time, unused_pose, presentation_time,
            confirmed_time, unused_delay, unused_ideal in steady))

    def test_late_200ms_gap_grows_buffer_once_then_decays_slowly(self):
        clock = [0.0]
        sync = self.module.SnapshotSync(1, clock=lambda: clock[0])

        def bot(sample_time_us):
            state = player(7, sample_time_us / 100000.0)
            state.update(team=2, yaw=0.0, aim_yaw=0.0,
                         gun_pitch=0.0)
            return state

        # Warm up on regular 50 ms authority samples, then reproduce the
        # report's later 200 ms worker stalls three times. Regular cadence
        # resumes afterwards so the high-water mark can recover.
        source_times = list(range(0, 1000001, 50000))
        source_times.extend((1200000, 1400000, 1600000))
        while source_times[-1] < 5000000:
            source_times.append(source_times[-1] + 50000)
        server_times = list(range(0, 5200001, 33333))
        render_times = list(range(0, 5000001, 10000))
        source_set = set(source_times)
        server_set = set(server_times)
        render_set = set(render_times)
        revision = 0
        sample_time_us = 0
        previous_target_us = None
        received = {}
        frames = []
        for time_us in sorted(source_set | server_set | render_set):
            if time_us in source_set:
                revision += 1
                sample_time_us = time_us
            clock[0] = time_us / 1000000.0
            if time_us in server_set:
                sync.snapshot({
                    'round_id': 1, 'server_tick': time_us,
                    'bot_state_revision': revision,
                    'motion_time_us': time_us,
                    'bot_state_time_us': sample_time_us,
                    'bots': [bot(sample_time_us)],
                })
                record = sync._entities['bot:7']
                target_us = record['target_sample_time_us']
                if target_us != previous_target_us:
                    received[target_us] = (
                        record['presentation_delay_us'],
                        record['interpolation_delay_us'])
                    previous_target_us = target_us
            if time_us in render_set:
                sync.advance(clock[0])
                record = sync._entities['bot:7']
                frames.append((time_us, record['presentation_time_us']))

        baseline_delay = received[1000000][0]
        for gap_target_us in (1200000, 1400000, 1600000):
            presentation_delay, ideal_delay = received[gap_target_us]
            # Growth happens in the snapshot that reveals the producer gap;
            # it does not wait for many render frames to converge.
            self.assertAlmostEqual(ideal_delay, presentation_delay, places=6)
            self.assertGreater(ideal_delay, baseline_delay + 100000.0)

        # Once the first stall has established the new cushion, the next
        # equally long interval does not consume all confirmed history.
        second_gap_frames = [presentation_time
                             for time_us, presentation_time in frames
                             if 1250000 <= time_us <= 1430000]
        self.assertTrue(all(
            second_gap_frames[index] > second_gap_frames[index - 1]
            for index in range(1, len(second_gap_frames))))

        # Returning to regular traffic must not snap the latency back down.
        # The first normal sample leaves the output cushion above its newly
        # decaying ideal; later samples reduce both gradually.
        first_regular_delay, first_regular_ideal = received[1650000]
        late_delay, late_ideal = received[5000000]
        self.assertGreater(first_regular_delay, first_regular_ideal)
        self.assertLess(late_delay, first_regular_delay)
        self.assertLess(late_ideal, first_regular_ideal)
        self.assertGreater(late_delay,
                           self.module.MIN_TIMED_DELAY_US + 100000.0)

    def test_first_live_intervals_establish_jitter_high_water_immediately(self):
        clock = [0.0]
        sync = self.module.SnapshotSync(1, clock=lambda: clock[0])

        def bot(sample_time_us):
            state = player(7, max(
                0, sample_time_us - 15040000) / 100000.0)
            state.update(team=2, yaw=0.0, aim_yaw=0.0,
                         gun_pitch=0.0)
            return state

        sync.snapshot({
            'round_id': 1, 'server_tick': 0,
            'bot_state_revision': 0,
            'motion_time_us': 0, 'bot_state_time_us': 0,
            'timing': {'phase': 'loading'}, 'bots': [bot(0)],
        })
        clock[0] = 15.0
        sync.snapshot({
            'round_id': 1, 'server_tick': 450,
            'bot_state_revision': 0,
            'motion_time_us': 15000000, 'bot_state_time_us': 0,
            'timing': {'phase': 'prebattle'}, 'bots': [bot(0)],
        })

        source_times = (15040000, 15080000, 15122000, 15188000)
        revision = 0
        sample_time_us = source_times[0]
        for server_time_us in (15040000, 15073333, 15106666,
                               15139999, 15173332, 15206665):
            available = [value for value in source_times
                         if value <= server_time_us]
            next_sample = available[-1]
            if next_sample != sample_time_us or server_time_us == 15040000:
                revision += 1
                sample_time_us = next_sample
            clock[0] = server_time_us / 1000000.0
            sync.snapshot({
                'round_id': 1, 'server_tick': server_time_us,
                'bot_state_revision': revision,
                'motion_time_us': server_time_us,
                'bot_state_time_us': sample_time_us,
                'timing': {'phase': 'battle'},
                'bots': [bot(sample_time_us)],
            })

        record = sync._entities['bot:7']
        self.assertEqual(3, record['timed_warmup_intervals'])
        self.assertTrue(record['timed_warmup_active'])
        self.assertGreater(record['interpolation_delay_us'], 98000.0)
        self.assertEqual(record['interpolation_delay_us'],
                         record['presentation_delay_us'])

    def test_first_live_sample_does_not_replay_the_countdown_as_latency(self):
        clock = [0.0]
        sync = self.module.SnapshotSync(1, clock=lambda: clock[0])

        def bot(position):
            state = player(7, position)
            state.update(team=2, yaw=0.0, aim_yaw=0.0,
                         gun_pitch=0.0)
            return state

        sync.manifest({'round_id': 1, 'bots': [bot(0.0)]})
        sync.snapshot({
            'round_id': 1, 'server_tick': 0,
            'bot_state_revision': 0,
            'motion_time_us': 0, 'bot_state_time_us': 0,
            'bots': [bot(0.0)],
        })
        # Snapshots advance throughout the 15-second countdown while the bot
        # revision and its authoritative sample timestamp intentionally do
        # not.  The first live revision is only one simulation step away.
        clock[0] = 15.0
        sync.snapshot({
            'round_id': 1, 'server_tick': 450,
            'bot_state_revision': 0,
            'motion_time_us': 15000000, 'bot_state_time_us': 0,
            'timing': {'phase': 'prebattle'},
            'bots': [bot(0.0)],
        })
        clock[0] = 15.04
        events = sync.snapshot({
            'round_id': 1, 'server_tick': 451,
            'bot_state_revision': 1,
            'motion_time_us': 15040000, 'bot_state_time_us': 15040000,
            'timing': {'phase': 'battle'},
            'bots': [bot(0.12)],
        })

        update = [event for event in events
                  if event.get('entity') == 'bot:7'][-1]
        self.assertTrue(update['snap'])
        self.assertAlmostEqual(0.12, update['pose']['x'])
        record = sync._entities['bot:7']
        self.assertEqual(1, len(record['timed_samples']))
        self.assertEqual(15040000, record['presentation_time_us'])
        self.assertIsNone(record['presentation_delay_us'])

        clock[0] = 15.08
        sync.snapshot({
            'round_id': 1, 'server_tick': 452,
            'bot_state_revision': 2,
            'motion_time_us': 15080000, 'bot_state_time_us': 15080000,
            'timing': {'phase': 'battle'},
            'bots': [bot(0.32)],
        })
        presented = sync.advance(15.08)[0]
        self.assertFalse(presented['snap'])
        self.assertEqual(
            int(round(record['presentation_time_us'])),
            presented['presentation_time_us'])
        self.assertGreaterEqual(presented['pose']['x'], 0.12)
        self.assertLessEqual(presented['pose']['x'], 0.32)

    def test_battle_phase_anchors_unchanged_pose_before_first_live_revision(self):
        clock = [0.0]
        sync = self.module.SnapshotSync(1, clock=lambda: clock[0])

        def bot(position):
            state = player(7, position)
            state.update(team=2, yaw=0.0, aim_yaw=0.0,
                         gun_pitch=0.0)
            return state

        sync.manifest({'round_id': 1, 'bots': [bot(0.0)]})
        sync.snapshot({
            'round_id': 1, 'server_tick': 0,
            'bot_state_revision': 0,
            'motion_time_us': 0, 'bot_state_time_us': 0,
            'timing': {'phase': 'prebattle'},
            'bots': [bot(0.0)],
        })
        # The server can enter battle before the worker publishes its first
        # live revision. This repeated pose is a confirmed live-time anchor,
        # so a slow first revision forms an interpolation segment rather than
        # snapping after the countdown-sized gap.
        clock[0] = 15.0
        sync.snapshot({
            'round_id': 1, 'server_tick': 450,
            'bot_state_revision': 0,
            'motion_time_us': 15000000, 'bot_state_time_us': 0,
            'timing': {'phase': 'battle'},
            'bots': [bot(0.0)],
        })
        self.assertFalse(sync._live_timeline_reset_pending)
        record = sync._entities['bot:7']
        self.assertEqual([15000000], [
            sample['time_us'] for sample in record['timed_samples']])
        self.assertEqual(15000000, record['presentation_time_us'])

        clock[0] = 15.04
        events = sync.snapshot({
            'round_id': 1, 'server_tick': 451,
            'bot_state_revision': 1,
            'motion_time_us': 15040000, 'bot_state_time_us': 15040000,
            'timing': {'phase': 'battle'},
            'bots': [bot(0.12)],
        })

        update = [event for event in events
                  if event.get('entity') == 'bot:7'][-1]
        self.assertFalse(update['snap'])
        self.assertIsNone(update['pose'])
        record = sync._entities['bot:7']
        self.assertEqual([15000000, 15040000], [
            sample['time_us'] for sample in record['timed_samples']])
        self.assertEqual(15000000, record['presentation_time_us'])
        self.assertFalse(sync._live_timeline_reset_pending)

    def test_first_live_reset_discards_all_prebattle_pose_history(self):
        clock = [0.0]
        sync = self.module.SnapshotSync(1, clock=lambda: clock[0])

        def bot(position):
            state = player(7, position)
            state.update(team=2, yaw=0.0, aim_yaw=0.0,
                         gun_pitch=0.0)
            return state

        # Loading can publish more than one placement revision before the
        # countdown freezes bot simulation. The phase transition, rather than
        # the amount of retained history, identifies the stale time span.
        sync.snapshot({
            'round_id': 1, 'server_tick': 0,
            'bot_state_revision': 0,
            'motion_time_us': 0, 'bot_state_time_us': 0,
            'timing': {'phase': 'loading'},
            'bots': [bot(0.0)],
        })
        clock[0] = 0.04
        sync.snapshot({
            'round_id': 1, 'server_tick': 1,
            'bot_state_revision': 1,
            'motion_time_us': 40000, 'bot_state_time_us': 40000,
            'timing': {'phase': 'prebattle'},
            'bots': [bot(0.02)],
        })
        self.assertEqual(2, len(sync._entities['bot:7']['timed_samples']))

        clock[0] = 15.04
        events = sync.snapshot({
            'round_id': 1, 'server_tick': 451,
            'bot_state_revision': 2,
            'motion_time_us': 15040000, 'bot_state_time_us': 15040000,
            'timing': {'phase': 'battle'},
            'bots': [bot(0.14)],
        })

        update = [event for event in events
                  if event.get('entity') == 'bot:7'][-1]
        self.assertTrue(update['snap'])
        self.assertAlmostEqual(0.14, update['pose']['x'])
        record = sync._entities['bot:7']
        self.assertEqual(1, len(record['timed_samples']))
        self.assertEqual(15040000, record['presentation_time_us'])

    def test_first_live_gap_during_battle_is_not_a_timeline_reset(self):
        clock = [0.0]
        sync = self.module.SnapshotSync(1, clock=lambda: clock[0])

        def bot(position):
            state = player(7, position)
            state.update(team=2, yaw=0.0, aim_yaw=0.0,
                         gun_pitch=0.0)
            return state

        sync.manifest({'round_id': 1, 'bots': [bot(0.0)]})
        sync.snapshot({
            'round_id': 1, 'server_tick': 1,
            'bot_state_revision': 1,
            'motion_time_us': 0, 'bot_state_time_us': 0,
            'timing': {'phase': 'battle'},
            'bots': [bot(0.0)],
        })
        clock[0] = 5.0
        events = sync.snapshot({
            'round_id': 1, 'server_tick': 2,
            'bot_state_revision': 2,
            'motion_time_us': 5000000, 'bot_state_time_us': 5000000,
            'timing': {'phase': 'battle'},
            'bots': [bot(40.0)],
        })

        update = [event for event in events
                  if event.get('entity') == 'bot:7'][-1]
        self.assertFalse(update['snap'])
        self.assertIsNone(update['pose'])
        event = sync.advance(5.0)[0]
        self.assertFalse(event['snap'])
        self.assertAlmostEqual(0.0, event['pose']['x'], places=6)
        self.assertEqual(2, len(sync._entities['bot:7']['timed_samples']))

    def test_timed_gap_uses_confirmed_cursor_before_teleport_guard(self):
        clock = [0.0]
        sync = self.module.SnapshotSync(1, clock=lambda: clock[0])

        def bot(position):
            state = player(7, position)
            state.update(team=2, yaw=0.0, aim_yaw=0.0,
                         gun_pitch=0.0)
            return state

        for revision, sample_us, position in (
                (1, 0, 0.0), (2, 100000, 1.0), (3, 5100000, 41.0)):
            clock[0] = sample_us / 1000000.0
            sync.snapshot({
                'round_id': 1, 'server_tick': revision,
                'bot_state_revision': revision,
                'motion_time_us': sample_us,
                'bot_state_time_us': sample_us,
                'bots': [bot(position)],
            })
            event = sync.advance(clock[0])[0]

        # The latest confirmed target is 40 m away, but the delayed playback
        # cursor is still at the preceding sample.  It must hold rather than
        # misclassify ordinary history interpolation as a teleport.
        self.assertFalse(event['snap'])
        self.assertAlmostEqual(0.0, event['pose']['x'], places=6)

    def test_timed_delay_shrink_cannot_jump_the_presentation_cursor(self):
        clock = [0.0]
        sync = self.module.SnapshotSync(1, clock=lambda: clock[0])

        def bot(sample_time_us):
            state = player(7, sample_time_us / 100000.0)
            state.update(team=2, yaw=0.0, aim_yaw=0.0,
                         gun_pitch=0.0)
            return state

        gaps_us = (68000, 170000, 90000, 35000, 170000, 68000,
                   68000, 68000, 68000)
        source_times = [0]
        for gap_us in gaps_us:
            source_times.append(source_times[-1] + gap_us)
        final_time_us = source_times[-1] + 200000
        server_times = list(range(0, final_time_us + 1, 33333))
        render_times = list(range(0, final_time_us + 1, 10000))
        source_set = set(source_times)
        server_set = set(server_times)
        render_set = set(render_times)
        revision = 0
        sample_time_us = 0
        positions = []
        confirmed = []
        delays = []
        for time_us in sorted(source_set | server_set | render_set):
            if time_us in source_set:
                revision += 1
                sample_time_us = time_us
            clock[0] = time_us / 1000000.0
            if time_us in server_set:
                sync.snapshot({
                    'round_id': 1, 'server_tick': time_us,
                    'bot_state_revision': revision,
                    'motion_time_us': time_us,
                    'bot_state_time_us': sample_time_us,
                    'bots': [bot(sample_time_us)],
                })
                delay = sync._entities['bot:7']['interpolation_delay_us']
                if delay is not None:
                    delays.append(delay)
            if time_us in render_set:
                event = sync.advance(clock[0])[0]
                positions.append(event['pose']['x'])
                confirmed.append(
                    sync._entities['bot:7']['target']['x'])

        # The adaptive buffer rises for the abnormal 170 ms outlier and sheds
        # its excess gradually. Safety permits a short hold but never a jump.
        self.assertGreaterEqual(max(delays) - min(delays), 33000)
        self.assertTrue(all(
            positions[index] + 1.0e-9 >= positions[index - 1]
            for index in range(1, len(positions))))
        self.assertTrue(all(
            pose <= target + 1.0e-9
            for pose, target in zip(positions, confirmed)))
        # The authority trajectory is exactly 10 m/s and presentation runs at
        # 100 FPS. BigWorld's stock latency curve may recover a 135 ms delay
        # error at about 1.018x, but it cannot turn that recovery into the old
        # 0.59 m single-frame jump.
        self.assertLessEqual(max(
            positions[index] - positions[index - 1]
            for index in range(1, len(positions))), 0.102)

    def test_timed_gap_latency_converges_after_regular_samples_resume(self):
        clock = [0.0]
        sync = self.module.SnapshotSync(1, clock=lambda: clock[0])

        def bot(sample_time_us):
            state = player(7, sample_time_us / 100000.0)
            state.update(team=2, yaw=0.0, aim_yaw=0.0,
                         gun_pitch=0.0)
            return state

        # One abnormal 170 ms producer gap follows the regular cadence. Once
        # cadence recovers, bounded catch-up follows the decaying high-water
        # mark instead of leaving it permanent; a short hold remains allowed.
        source_times = [0, 68000, 238000]
        while source_times[-1] < 5200000:
            source_times.append(source_times[-1] + 68000)
        server_times = list(range(0, 5200001, 33333))
        render_times = list(range(0, 5000001, 10000))
        source_set = set(source_times)
        server_set = set(server_times)
        render_set = set(render_times)
        revision = 0
        sample_time_us = 0
        delay_errors = []
        positions = []
        confirmed = []
        for time_us in sorted(source_set | server_set | render_set):
            if time_us in source_set:
                revision += 1
                sample_time_us = time_us
            clock[0] = time_us / 1000000.0
            if time_us in server_set:
                sync.snapshot({
                    'round_id': 1, 'server_tick': time_us,
                    'bot_state_revision': revision,
                    'motion_time_us': time_us,
                    'bot_state_time_us': sample_time_us,
                    'bots': [bot(sample_time_us)],
                })
            if time_us in render_set:
                event = sync.advance(clock[0])[0]
                record = sync._entities['bot:7']
                positions.append(event['pose']['x'])
                confirmed.append(record['target']['x'])
                if (time_us >= 1000000 and
                        record['presentation_delay_us'] is not None and
                        record['interpolation_delay_us'] is not None):
                    delay_errors.append((
                        time_us,
                        record['presentation_delay_us'] -
                        record['interpolation_delay_us']))

        # Presentation follows the decaying ideal within one millisecond. The
        # remaining sub-millisecond sawtooth is only the 10 ms render cadence
        # versus the 68 ms source cadence, not a retained latency jump.
        self.assertLess(max(abs(item[1]) for item in delay_errors), 1000.0)
        self.assertTrue(all(
            positions[index] + 1.0e-9 >= positions[index - 1]
            for index in range(1, len(positions))))
        self.assertTrue(all(
            pose <= target + 1.0e-9
            for pose, target in zip(positions, confirmed)))
        self.assertLessEqual(max(
            positions[index] - positions[index - 1]
            for index in range(1, len(positions))), 0.102)

    def test_timed_interpolation_keeps_short_angles_and_timed_lifecycle(self):
        initial = player(7, 0.0)
        initial.update(yaw=math.pi - 0.05,
                       pitch=-0.16, roll=0.10,
                       aim_yaw=math.pi - 0.10, gun_pitch=-0.2)
        target = player(7, 1.0)
        target.update(yaw=-math.pi + 0.05,
                      pitch=0.16, roll=-0.10,
                      aim_yaw=-math.pi + 0.10, gun_pitch=0.2)
        sync = self.module.SnapshotSync(1, clock=lambda: self.now[0])
        created = sync.snapshot({
            'round_id': 1, 'server_tick': 1,
            'bot_state_revision': 1,
            'motion_time_us': 0, 'bot_state_time_us': 0,
            'bots': [initial]})
        self.assertEqual(['create', 'update'],
                         [event['type'] for event in created])

        self.now[0] = 0.1
        sync.snapshot({
            'round_id': 1, 'server_tick': 2,
            'bot_state_revision': 2,
            'motion_time_us': 100000, 'bot_state_time_us': 100000,
            'bots': [target]})
        pose = sync.advance(0.15)[0]['pose']
        self.assertLess(abs(self.module._angle_delta(
            initial['yaw'], pose['yaw'])), 0.1)
        self.assertLess(abs(self.module._angle_delta(
            initial['aim_yaw'], pose['aim_yaw'])), 0.2)
        # The deliberate 90 ms history fill still presents the first
        # confirmed pose here, but the next suspension target must survive
        # protocol translation for the same delayed interpolation path.
        self.assertEqual(initial['pitch'], pose['pitch'])
        self.assertEqual(initial['roll'], pose['roll'])
        record = sync._entities['bot:7']
        self.assertEqual(target['pitch'], record['target']['pitch'])
        self.assertEqual(target['roll'], record['target']['roll'])

        teleported = player(7, 40.0)
        teleported.update(
            yaw=target['yaw'], pitch=target['pitch'], roll=target['roll'],
            aim_yaw=target['aim_yaw'], gun_pitch=target['gun_pitch'])
        self.now[0] = 0.2
        sync.snapshot({
            'round_id': 1, 'server_tick': 3,
            'bot_state_revision': 3,
            'motion_time_us': 200000, 'bot_state_time_us': 200000,
            'bots': [teleported]})
        event = sync.advance(0.2)[0]
        self.assertTrue(event['snap'])
        self.assertEqual(40.0, event['pose']['x'])

        self.now[0] = 0.21
        sync.snapshot({
            'round_id': 1, 'server_tick': 4,
            'bot_state_revision': 3,
            'motion_time_us': 210000, 'bot_state_time_us': 200000,
            'bots': [teleported]})
        self.assertEqual(40.0, sync.advance(0.21)[0]['pose']['x'])

        dead = dict(teleported, alive=False)
        self.now[0] = 0.22
        destroyed = sync.snapshot({
            'round_id': 1, 'server_tick': 5,
            'bot_state_revision': 3,
            'motion_time_us': 220000, 'bot_state_time_us': 200000,
            'bots': [dead]})
        self.assertEqual(['destroy'],
                         [event['type'] for event in destroyed])

    def test_bot_pose_timing_is_atomic_monotonic_and_revision_bound(self):
        bot = player(7, 0.0)
        self.sync.manifest({'round_id': 1, 'bots': [bot]})
        self.sync.snapshot({
            'round_id': 1, 'server_tick': 1,
            'bot_state_revision': 1,
            'motion_time_us': 100000, 'bot_state_time_us': 90000,
            'bots': [bot]})

        with self.assertRaisesRegex(ValueError, 'sample time'):
            self.sync.snapshot({
                'round_id': 1, 'server_tick': 2,
                'bot_state_revision': 1,
                'motion_time_us': 120000, 'bot_state_time_us': 100000,
                'bots': [bot]})

        advanced = self.module.SnapshotSync(
            1, clock=lambda: self.now[0])
        advanced.manifest({'round_id': 1, 'bots': [bot]})
        advanced.snapshot({
            'round_id': 1, 'server_tick': 1,
            'bot_state_revision': 1,
            'motion_time_us': 100000, 'bot_state_time_us': 90000,
            'bots': [bot]})
        with self.assertRaisesRegex(ValueError, 'did not advance'):
            advanced.snapshot({
                'round_id': 1, 'server_tick': 2,
                'bot_state_revision': 2,
                'motion_time_us': 120000, 'bot_state_time_us': 90000,
                'bots': [bot]})

        fresh = self.module.SnapshotSync(1, clock=lambda: self.now[0])
        fresh.manifest({'round_id': 1, 'bots': [bot]})
        fresh.snapshot({
            'round_id': 1, 'server_tick': 1,
            'bot_state_revision': 1,
            'motion_time_us': 100000, 'bot_state_time_us': 90000,
            'bots': [bot]})
        with self.assertRaisesRegex(ValueError, 'disappeared'):
            fresh.snapshot({
                'round_id': 1, 'server_tick': 2,
                'bot_state_revision': 2, 'bots': [bot]})

        incomplete = self.module.SnapshotSync(
            1, clock=lambda: self.now[0])
        incomplete.manifest({'round_id': 1, 'bots': [bot]})
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            incomplete.snapshot({
                'round_id': 1, 'server_tick': 1,
                'bot_state_revision': 1,
                'motion_time_us': 100000, 'bots': [bot]})

    def test_bot_state_revision_cannot_regress_or_disappear(self):
        bot = player(7, 0.0)
        self.sync.manifest({'round_id': 1, 'bots': [bot]})
        self.sync.snapshot({
            'round_id': 1, 'server_tick': 1,
            'bot_state_revision': 3, 'bots': [bot]})

        with self.assertRaisesRegex(ValueError, 'regressed'):
            self.sync.snapshot({
                'round_id': 1, 'server_tick': 2,
                'bot_state_revision': 2, 'bots': [bot]})
        with self.assertRaisesRegex(ValueError, 'disappeared'):
            self.sync.snapshot({
                'round_id': 1, 'server_tick': 3, 'bots': [bot]})
        self.assertEqual(3, self.sync._last_bot_state_revision)
        self.assertEqual(1, self.sync._last_sequence)
        self.sync.snapshot({
            'round_id': 1, 'server_tick': 2,
            'bot_state_revision': 4, 'bots': [bot]})
        self.assertEqual(4, self.sync._last_bot_state_revision)
        self.assertEqual(2, self.sync._last_sequence)

        self.sync.manifest({'round_id': 2, 'bots': [bot]})
        self.sync.snapshot({
            'round_id': 2, 'server_tick': 0,
            'bot_state_revision': 0, 'bots': [bot]})
        self.assertEqual(0, self.sync._last_bot_state_revision)

    def test_non_finite_snapshot_numbers_fall_back_to_safe_zero(self):
        malformed = player(2, float('nan'))
        malformed.update(yaw=float('inf'), aim_yaw=float('-inf'),
                         gun_pitch=float('nan'))

        event = self.sync.snapshot({
            'server_tick': 1, 'players': [malformed]})[-1]

        self.assertEqual(
            {'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
             'pitch': 0.0, 'roll': 0.0,
             'aim_yaw': 0.0, 'gun_pitch': 0.0}, event['target'])

    def test_stale_round_or_sequence_has_no_events(self):
        self.sync.manifest({'round_id': 4, 'players': [player(1)]})
        self.assertTrue(self.sync.snapshot({'round_id': 4, 'server_tick': 3,
                                            'players': [player(1)]}))
        self.assertEqual([], self.sync.snapshot({'round_id': 4, 'server_tick': 3,
                                                 'players': [player(1)]}))
        self.assertEqual([], self.sync.snapshot({'round_id': 3, 'server_tick': 4,
                                                 'players': [player(1)]}))

    def test_late_join_snapshot_creates_unknown_entity_and_orders_once_per_revision(self):
        events = self.sync.snapshot({'server_tick': 1, 'players': [player(9)],
                                     'bots': [], 'bot_order_revision': 2,
                                     'bot_orders': [{'id': 7, 'target_id': 9}]})
        self.assertEqual(['create', 'update', 'order'], [event['type'] for event in events])
        repeat = self.sync.snapshot({'server_tick': 2, 'players': [player(9)],
                                     'bots': [], 'bot_order_revision': 2,
                                     'bot_orders': [{'id': 7, 'target_id': 1}]})
        self.assertFalse([event for event in repeat if event['type'] == 'order'])

    def test_death_and_missing_corpse_are_idempotent(self):
        self.sync.manifest({'round_id': 1, 'players': [player(2)]})
        dead = self.sync.snapshot({'round_id': 1, 'server_tick': 1,
                                   'players': [player(2, alive=False)]})
        self.assertEqual(['destroy'], [event['type'] for event in dead])
        self.assertTrue(dead[0]['keep_corpse'])
        self.assertEqual([], self.sync.snapshot({'round_id': 1, 'server_tick': 2,
                                                 'players': [player(2, alive=False)]}))
        self.assertEqual([], self.sync.snapshot({'round_id': 1, 'server_tick': 3,
                                                 'players': []}))
