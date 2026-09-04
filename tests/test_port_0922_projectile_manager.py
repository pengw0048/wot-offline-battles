import importlib.util
import math
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT / 'src' / 'res' / 'scripts' / 'client' /
    'gui' / 'mods' / 'offline_lan_0922' / 'projectile_manager.py')


def _load_module():
    module_dir = str(MODULE_PATH.parent)
    sys.path.insert(0, module_dir)
    try:
        spec = importlib.util.spec_from_file_location(
            'port_0922_projectile_manager_test', MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(module_dir)


def _launch(manager, key='shot', **overrides):
    values = {
        'start': (0.0, 0.0, 0.0),
        'velocity': (100.0, 0.0, 0.0),
        'gravity': (0.0, 0.0, 0.0),
        'launch_time': 0.0,
        'max_time': 10.0,
        'max_distance': 5000.0,
        'payload': {'shell': 1},
    }
    values.update(overrides)
    return manager.launch(
        key, values['start'], values['velocity'], values['gravity'],
        values['launch_time'], values['max_time'], values['max_distance'],
        values['payload'])


def _authoritative_snapshot(key='shot', **overrides):
    values = {
        'key': key,
        'start': (1.0, 2.0, 3.0),
        'velocity': (20.0, 0.0, 0.0),
        'gravity': (0.0, 0.0, 0.0),
        'launch_time': 0.0,
        'max_time': 10.0,
        'max_distance': 5000.0,
        'cursor_time': 0.0,
        'distance': 0.0,
        'payload': {'authority': 'server'},
    }
    values.update(overrides)
    return values


class ProjectileManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_module()

    def test_absolute_trajectory_is_invariant_at_30_60_and_120_fps(self):
        snapshots = []
        for frames_per_second in (30, 60, 120):
            manager = self.module.InFlightProjectiles()
            self.assertTrue(_launch(
                manager, velocity=(80.0, 15.0, 12.0),
                gravity=(0.0, -9.81, 0.0)))
            chords = []

            def observe(_state, _start, _end, absolute_start, absolute_end):
                chords.append((absolute_start, absolute_end))

            for frame in range(frames_per_second):
                self.assertTrue(manager.advance(
                    float(frame + 1) / frames_per_second,
                    observe, lambda _state, _terminal: None))

            snapshot = manager.get('shot')
            snapshots.append(snapshot)
            self.assertAlmostEqual(1.0, snapshot['elapsed'])
            self.assertAlmostEqual(80.0, snapshot['position'][0])
            self.assertAlmostEqual(10.095, snapshot['position'][1])
            self.assertAlmostEqual(12.0, snapshot['position'][2])
            self.assertTrue(all(
                0.0 < end - start <= 0.050000001
                for start, end in chords))

        for snapshot in snapshots[1:]:
            self.assertEqual(snapshots[0]['position'], snapshot['position'])
            self.assertAlmostEqual(
                snapshots[0]['distance'], snapshot['distance'], places=3)

    def test_slow_frame_loses_no_time_and_keeps_bounded_chords(self):
        manager = self.module.InFlightProjectiles()
        self.assertTrue(_launch(manager))
        chords = []

        self.assertTrue(manager.advance(
            0.41,
            lambda _state, _start, _end, first, last:
            chords.append((first, last)),
            lambda _state, _terminal: None))

        self.assertEqual(9, len(chords))
        self.assertAlmostEqual(0.0, chords[0][0])
        self.assertAlmostEqual(0.41, chords[-1][1])
        self.assertAlmostEqual(
            0.41, sum(end - start for start, end in chords))
        self.assertAlmostEqual(0.41, manager.get('shot')['elapsed'])

    def test_distance_accumulates_each_curved_chord_not_endpoint_displacement(self):
        manager = self.module.InFlightProjectiles()
        self.assertTrue(_launch(
            manager, velocity=(10.0, 10.0, 0.0),
            gravity=(0.0, -20.0, 0.0)))
        chord_distance = [0.0]

        def observe(_state, start, end, _absolute_start, _absolute_end):
            chord_distance[0] += math.sqrt(sum(
                (end[index] - start[index]) ** 2 for index in range(3)))

        manager.advance(1.0, observe, lambda _state, _terminal: None)

        snapshot = manager.get('shot')
        self.assertAlmostEqual(chord_distance[0], snapshot['distance'])
        self.assertGreater(snapshot['distance'], 10.0)
        self.assertEqual((10.0, 0.0, 0.0), snapshot['position'])

    def test_launch_order_is_deterministic_for_multiple_projectiles(self):
        manager = self.module.InFlightProjectiles()
        for key in ('third', 'first', 'second'):
            self.assertTrue(_launch(manager, key=key))
        order = []

        manager.advance(
            0.01,
            lambda state, _start, _end, _first, _last:
            order.append(state['key']),
            lambda _state, _terminal: None)

        self.assertEqual(['third', 'first', 'second'], order)
        self.assertEqual(
            ['third', 'first', 'second'],
            [state['key'] for state in manager.snapshot()])

    def test_collision_fraction_truncates_time_position_and_distance(self):
        manager = self.module.InFlightProjectiles()
        self.assertTrue(_launch(manager))
        terminals = []

        manager.advance(
            1.0,
            lambda _state, _start, _end, _first, _last: {
                'reason': 'vehicle', 'fraction': 0.4, 'target_id': 9},
            lambda state, terminal: terminals.append((state, terminal)))

        self.assertEqual(1, len(terminals))
        state, terminal = terminals[0]
        self.assertAlmostEqual(0.02, state['cursor_time'])
        self.assertAlmostEqual(2.0, state['distance'])
        self.assertEqual((2.0, 0.0, 0.0), state['position'])
        self.assertEqual(9, terminal['target_id'])
        self.assertAlmostEqual(0.4, terminal['fraction'])
        self.assertEqual([], manager.snapshot())

    def test_max_distance_truncates_last_chord_strictly(self):
        manager = self.module.InFlightProjectiles()
        self.assertTrue(_launch(
            manager, velocity=(10.0, 0.0, 0.0), max_distance=1.1))
        observed_ends = []
        terminals = []

        manager.advance(
            1.0,
            lambda _state, _start, end, _first, _last:
            observed_ends.append(end),
            lambda state, terminal: terminals.append((state, terminal)))

        self.assertEqual(1, len(terminals))
        state, terminal = terminals[0]
        self.assertEqual('max_distance', terminal['reason'])
        self.assertAlmostEqual(1.1, state['distance'])
        self.assertAlmostEqual(0.11, state['elapsed'])
        self.assertAlmostEqual(1.1, state['position'][0])
        self.assertAlmostEqual(1.1, observed_ends[-1][0])

    def test_max_time_is_strict_even_after_one_slow_frame(self):
        manager = self.module.InFlightProjectiles()
        self.assertTrue(_launch(
            manager, velocity=(20.0, 0.0, 0.0), max_time=0.073))
        terminals = []
        chord_ends = []

        manager.advance(
            2.0,
            lambda _state, _start, _end, _first, last:
            chord_ends.append(last),
            lambda state, terminal: terminals.append((state, terminal)))

        state, terminal = terminals[0]
        self.assertEqual('max_time', terminal['reason'])
        self.assertAlmostEqual(0.073, chord_ends[-1])
        self.assertAlmostEqual(0.073, state['elapsed'])
        self.assertAlmostEqual(1.46, state['position'][0])
        self.assertFalse(manager.contains('shot'))

    def test_duplicate_is_retired_until_reset_and_reset_keeps_clock_safe(self):
        manager = self.module.InFlightProjectiles()
        self.assertTrue(_launch(manager, max_time=0.01))
        self.assertFalse(_launch(manager))
        manager.advance(
            0.01, lambda *_args: None, lambda _state, _terminal: None)
        self.assertFalse(_launch(manager))
        self.assertFalse(manager.reset(now=0.005))
        self.assertFalse(_launch(manager))

        self.assertTrue(manager.reset())
        self.assertAlmostEqual(0.01, manager.now)
        self.assertTrue(_launch(manager, launch_time=0.01))

    def test_remove_keeps_identity_fence_and_duplicate_launch_is_rejected(self):
        manager = self.module.InFlightProjectiles()
        self.assertTrue(_launch(manager))
        self.assertFalse(_launch(manager))
        self.assertTrue(manager.remove('shot'))
        self.assertFalse(manager.contains('shot'))
        self.assertFalse(_launch(manager))

    def test_active_provisional_rollback_releases_identity_for_relaunch(self):
        manager = self.module.InFlightProjectiles()
        self.assertTrue(_launch(manager))

        self.assertTrue(manager.rollback_provisional('shot'))

        self.assertFalse(manager.contains('shot'))
        self.assertEqual([], manager.snapshot())
        self.assertFalse(manager.rollback_provisional('shot'))
        self.assertTrue(_launch(manager))

    def test_terminal_provisional_rollback_releases_identity_for_relaunch(self):
        manager = self.module.InFlightProjectiles()
        self.assertTrue(_launch(manager, max_time=0.01))
        callback_results = []

        def terminal(_state, _result):
            callback_results.append(
                manager.rollback_provisional('shot'))
            callback_results.append(manager.replace_authoritative(
                _authoritative_snapshot(
                    cursor_time=0.01, distance=1.0)))

        self.assertTrue(manager.advance(
            0.01, lambda *_args: None, terminal))

        self.assertEqual([False, False], callback_results)
        self.assertFalse(manager.contains('shot'))
        self.assertFalse(_launch(manager, launch_time=0.01))
        self.assertTrue(manager.rollback_provisional('shot'))
        self.assertTrue(_launch(manager, launch_time=0.01))

    def test_active_authoritative_replace_is_atomic_and_keeps_order(self):
        manager = self.module.InFlightProjectiles(initial_time=0.5)
        self.assertTrue(_launch(manager, key='shot'))
        self.assertTrue(_launch(manager, key='peer'))
        replacement = _authoritative_snapshot(
            start=(5.0, 4.0, 3.0),
            velocity=(20.0, 2.0, -4.0),
            launch_time=0.1,
            cursor_time=0.4,
            distance=6.5,
            payload={'authority': 'canonical'})

        self.assertTrue(manager.replace_authoritative(replacement))

        self.assertEqual(
            ['shot', 'peer'],
            [state['key'] for state in manager.snapshot()])
        state = manager.get('shot')
        self.assertEqual((5.0, 4.0, 3.0), state['start'])
        for expected, actual in zip(
                (11.0, 4.6, 1.8), state['position']):
            self.assertAlmostEqual(expected, actual)
        self.assertAlmostEqual(0.4, state['cursor_time'])
        self.assertAlmostEqual(6.5, state['distance'])
        self.assertEqual('canonical', state['payload']['authority'])

    def test_admission_horizon_preserves_launch_between_manager_advances(self):
        manager = self.module.InFlightProjectiles(initial_time=10.0)

        self.assertTrue(manager.launch(
            'shot', (0.0, 0.0, 0.0), (100.0, 0.0, 0.0),
            (0.0, 0.0, 0.0), 10.04, 10.0, 5000.0,
            admission_time=10.06))

        self.assertAlmostEqual(10.0, manager.now)
        self.assertAlmostEqual(10.04, manager.get('shot')['launch_time'])
        self.assertTrue(manager.advance(
            10.02, lambda *_args: None,
            lambda _state, _terminal: None))
        self.assertAlmostEqual(0.0, manager.get('shot')['elapsed'])
        chords = []
        self.assertTrue(manager.advance(
            10.06,
            lambda _state, _start, _end, first, last:
            chords.append((first, last)),
            lambda _state, _terminal: None))
        self.assertAlmostEqual(0.02, manager.get('shot')['elapsed'])
        self.assertAlmostEqual(10.04, chords[0][0])
        self.assertAlmostEqual(10.06, chords[-1][1])

    def test_admission_horizon_rejects_unadmitted_future_without_mutation(self):
        manager = self.module.InFlightProjectiles(initial_time=10.0)

        self.assertFalse(manager.launch(
            'future', (0.0, 0.0, 0.0), (100.0, 0.0, 0.0),
            (0.0, 0.0, 0.0), 10.07, 10.0, 5000.0,
            admission_time=10.06))
        self.assertFalse(manager.launch(
            'stale-horizon', (0.0, 0.0, 0.0), (100.0, 0.0, 0.0),
            (0.0, 0.0, 0.0), 9.9, 10.0, 5000.0,
            admission_time=9.99))

        self.assertEqual([], manager.snapshot())

    def test_terminal_identity_accepts_inactive_authoritative_replace(self):
        manager = self.module.InFlightProjectiles(initial_time=0.5)
        self.assertTrue(_launch(manager, max_time=0.1))
        self.assertTrue(manager.advance(
            0.5, lambda *_args: None,
            lambda _state, _terminal: None))
        self.assertFalse(manager.contains('shot'))

        self.assertTrue(manager.replace_authoritative(
            _authoritative_snapshot(
                start=(2.0, 0.0, 0.0),
                velocity=(10.0, 0.0, 0.0),
                cursor_time=0.25,
                distance=2.5)))

        state = manager.get('shot')
        self.assertIsNotNone(state)
        self.assertEqual((4.5, 0.0, 0.0), state['position'])
        self.assertAlmostEqual(0.25, state['cursor_time'])
        self.assertAlmostEqual(2.5, state['distance'])

    def test_authoritative_replace_changes_key_atomically_and_keeps_order(self):
        manager = self.module.InFlightProjectiles(initial_time=0.5)
        self.assertTrue(_launch(manager, key='provisional'))
        self.assertTrue(_launch(manager, key='peer'))

        self.assertTrue(manager.replace_authoritative(
            _authoritative_snapshot(
                key='canonical', cursor_time=0.25, distance=5.0),
            provisional_key='provisional'))

        self.assertFalse(manager.contains('provisional'))
        self.assertTrue(manager.contains('canonical'))
        self.assertEqual(
            ['canonical', 'peer'],
            [state['key'] for state in manager.snapshot()])
        self.assertFalse(_launch(manager, key='canonical'))
        self.assertTrue(_launch(manager, key='provisional'))

    def test_invalid_cross_key_replace_preserves_provisional_state_and_fence(self):
        manager = self.module.InFlightProjectiles(initial_time=0.5)
        self.assertTrue(_launch(manager, key='provisional'))
        before = manager.get('provisional')

        self.assertFalse(manager.replace_authoritative(
            _authoritative_snapshot(
                key='canonical', cursor_time=0.6),
            provisional_key='provisional'))

        self.assertEqual(before, manager.get('provisional'))
        self.assertFalse(manager.contains('canonical'))
        self.assertFalse(_launch(manager, key='provisional'))
        self.assertTrue(_launch(manager, key='canonical'))

    def test_invalid_authoritative_replace_preserves_active_state(self):
        manager = self.module.InFlightProjectiles(initial_time=0.5)
        self.assertTrue(_launch(manager))
        before = manager.get('shot')

        self.assertFalse(manager.replace_authoritative(
            _authoritative_snapshot(cursor_time=0.6)))

        self.assertEqual(before, manager.get('shot'))
        self.assertFalse(_launch(manager))

        class Uncopyable(object):
            def __deepcopy__(self, _memo):
                raise RuntimeError('payload cannot be detached')

        self.assertFalse(manager.replace_authoritative(
            _authoritative_snapshot(payload=Uncopyable())))
        self.assertEqual(before, manager.get('shot'))

    def test_invalid_inactive_replace_preserves_terminal_identity_fence(self):
        manager = self.module.InFlightProjectiles(initial_time=0.5)
        self.assertTrue(_launch(manager, max_time=0.1))
        self.assertTrue(manager.advance(
            0.5, lambda *_args: None,
            lambda _state, _terminal: None))

        self.assertFalse(manager.replace_authoritative(
            _authoritative_snapshot(cursor_time=0.6)))

        self.assertFalse(manager.contains('shot'))
        self.assertFalse(_launch(manager))
        self.assertTrue(manager.rollback_provisional('shot'))

    def test_provisional_reconciliation_is_rejected_during_advance(self):
        manager = self.module.InFlightProjectiles()
        self.assertTrue(_launch(manager))
        replacement = _authoritative_snapshot()
        callback_results = []

        def observe(_state, _start, _end, _first, _last):
            if not callback_results:
                callback_results.append(
                    manager.rollback_provisional('shot'))
                callback_results.append(
                    manager.replace_authoritative(replacement))

        self.assertTrue(manager.advance(
            0.01, observe, lambda _state, _terminal: None))

        self.assertEqual([False, False], callback_results)
        self.assertTrue(manager.contains('shot'))
        self.assertAlmostEqual(0.01, manager.get('shot')['cursor_time'])

    def test_future_launch_stale_advance_and_invalid_inputs_fail_closed(self):
        manager = self.module.InFlightProjectiles()
        self.assertFalse(_launch(manager, key='future', launch_time=0.1))
        self.assertFalse(_launch(manager, key='nan', max_time=float('nan')))
        self.assertFalse(_launch(manager, key='bad-vector', start=(0.0, 1.0)))
        self.assertTrue(_launch(manager))
        manager.advance(
            0.1, lambda *_args: None, lambda _state, _terminal: None)
        before = manager.get('shot')

        self.assertFalse(manager.advance(
            0.05, lambda *_args: None, lambda _state, _terminal: None))
        self.assertEqual(before, manager.get('shot'))

    def test_past_launch_can_catch_up_at_current_absolute_time(self):
        manager = self.module.InFlightProjectiles()
        manager.advance(
            2.0, lambda *_args: None, lambda _state, _terminal: None)
        self.assertTrue(_launch(
            manager, launch_time=1.5, velocity=(10.0, 0.0, 0.0)))

        manager.advance(
            2.0, lambda *_args: None, lambda _state, _terminal: None)

        self.assertAlmostEqual(0.5, manager.get('shot')['elapsed'])
        self.assertAlmostEqual(5.0, manager.get('shot')['position'][0])

    def test_capacity_rejects_without_mutating_existing_state(self):
        manager = self.module.InFlightProjectiles(maximum_active=2)
        self.assertTrue(_launch(manager, key='a'))
        self.assertTrue(_launch(manager, key='b'))
        self.assertFalse(_launch(manager, key='c'))
        self.assertEqual(['a', 'b'], [
            state['key'] for state in manager.snapshot()])

    def test_launch_and_remove_from_callback_are_deferred_safely(self):
        manager = self.module.InFlightProjectiles()
        self.assertTrue(_launch(manager, key='a'))
        self.assertTrue(_launch(manager, key='b'))
        callbacks = []
        reentered = [False]

        def observe(state, _start, _end, _first, last):
            callbacks.append(state['key'])
            if state['key'] == 'a' and not reentered[0]:
                reentered[0] = True
                self.assertTrue(_launch(
                    manager, key='c', launch_time=last))
                self.assertTrue(manager.remove('b'))
                self.assertFalse(manager.reset())

        manager.advance(0.1, observe, lambda _state, _terminal: None)

        self.assertEqual(['a'] * 2 + ['b'] * 2, callbacks)
        self.assertEqual(['a', 'c'], [
            state['key'] for state in manager.snapshot()])
        callbacks[:] = []
        manager.advance(0.11, observe, lambda _state, _terminal: None)
        self.assertEqual(['a'] + ['c'] * 2, callbacks)

    def test_self_remove_from_chord_stops_further_chords_without_corruption(self):
        manager = self.module.InFlightProjectiles()
        self.assertTrue(_launch(manager, key='a'))
        self.assertTrue(_launch(manager, key='b'))
        counts = {'a': 0, 'b': 0}

        def observe(state, _start, _end, _first, _last):
            counts[state['key']] += 1
            if state['key'] == 'a':
                manager.remove('a')

        manager.advance(0.1, observe, lambda _state, _terminal: None)

        self.assertEqual(1, counts['a'])
        self.assertEqual(2, counts['b'])
        self.assertEqual(['b'], [state['key'] for state in manager.snapshot()])

    def test_callback_exception_retires_only_affected_projectile(self):
        manager = self.module.InFlightProjectiles()
        self.assertTrue(_launch(manager, key='bad'))
        self.assertTrue(_launch(manager, key='good'))
        terminals = []

        def collide(state, _start, _end, _first, _last):
            if state['key'] == 'bad':
                raise RuntimeError('collision adapter failed')

        manager.advance(
            0.01, collide,
            lambda state, terminal: terminals.append((state, terminal)))

        self.assertEqual('bad', terminals[0][0]['key'])
        self.assertEqual('callback_error', terminals[0][1]['reason'])
        self.assertAlmostEqual(0.0, terminals[0][0]['distance'])
        self.assertFalse(manager.contains('bad'))
        self.assertTrue(manager.contains('good'))
        self.assertAlmostEqual(0.01, manager.get('good')['elapsed'])

    def test_terminal_callback_exception_still_removes_and_advances_others(self):
        manager = self.module.InFlightProjectiles()
        self.assertTrue(_launch(manager, key='a', max_time=0.01))
        self.assertTrue(_launch(manager, key='b'))

        def broken_terminal(_state, _terminal):
            raise RuntimeError('sink failed')

        self.assertTrue(manager.advance(
            0.01, lambda *_args: None, broken_terminal))

        self.assertFalse(manager.contains('a'))
        self.assertTrue(manager.contains('b'))
        self.assertAlmostEqual(0.01, manager.get('b')['elapsed'])

    def test_launch_and_public_snapshots_are_deeply_detached(self):
        manager = self.module.InFlightProjectiles()
        start = [1.0, 2.0, 3.0]
        payload = {'effects': ['damage'], 'nested': {'value': 7}}
        self.assertTrue(_launch(
            manager, key=['player', 3, 8], start=start, payload=payload))
        start[0] = 999.0
        payload['effects'].append('stun')
        payload['nested']['value'] = 99

        snapshot = manager.snapshot()
        self.assertEqual(('player', 3, 8), snapshot[0]['key'])
        self.assertEqual((1.0, 2.0, 3.0), snapshot[0]['start'])
        self.assertEqual(['damage'], snapshot[0]['payload']['effects'])
        self.assertEqual(7, snapshot[0]['payload']['nested']['value'])
        snapshot[0]['payload']['effects'].append('mutated')
        snapshot[0]['position'] = (99.0, 99.0, 99.0)

        fresh = manager.get(('player', 3, 8))
        self.assertEqual(['damage'], fresh['payload']['effects'])
        self.assertEqual((1.0, 2.0, 3.0), fresh['position'])

    def test_restore_resumes_at_cursor_and_strict_remaining_distance(self):
        manager = self.module.InFlightProjectiles(initial_time=0.5)
        restored = {
            'key': ('bot', 4, 9),
            'start': (0.0, 0.0, 0.0),
            'velocity': (100.0, 0.0, 0.0),
            'gravity': (0.0, 0.0, 0.0),
            'launch_time': 0.0,
            'max_time': 2.0,
            'max_distance': 52.0,
            'payload': {'shell': 'AP'},
            'cursor_time': 0.5,
            'distance': 50.0,
            'position': (999.0, 999.0, 999.0),
            'elapsed': 999.0,
        }
        self.assertTrue(manager.restore(restored))
        self.assertEqual((50.0, 0.0, 0.0), manager.get(
            ('bot', 4, 9))['position'])
        chords = []
        terminals = []

        manager.advance(
            1.0,
            lambda _state, start, end, first, last:
            chords.append((start, end, first, last)),
            lambda state, terminal: terminals.append((state, terminal)))

        self.assertEqual(1, len(chords))
        self.assertEqual((50.0, 0.0, 0.0), chords[0][0])
        self.assertEqual((52.0, 0.0, 0.0), chords[0][1])
        self.assertAlmostEqual(0.5, chords[0][2])
        self.assertAlmostEqual(0.52, chords[0][3])
        state, terminal = terminals[0]
        self.assertEqual('max_distance', terminal['reason'])
        self.assertAlmostEqual(52.0, state['distance'])
        self.assertAlmostEqual(0.52, state['cursor_time'])
        self.assertEqual((52.0, 0.0, 0.0), state['position'])
        self.assertFalse(manager.contains(('bot', 4, 9)))

    def test_restore_rejects_invalid_cursor_distance_and_missing_state(self):
        manager = self.module.InFlightProjectiles(initial_time=0.5)
        base = {
            'key': 'restored',
            'start': (0.0, 0.0, 0.0),
            'velocity': (10.0, 0.0, 0.0),
            'gravity': (0.0, 0.0, 0.0),
            'launch_time': 0.1,
            'max_time': 1.0,
            'max_distance': 100.0,
            'cursor_time': 0.4,
            'distance': 3.0,
            'payload': None,
        }
        invalid = []
        invalid.append(dict(base, launch_time=0.45))
        invalid.append(dict(base, cursor_time=0.6))
        invalid.append(dict(base, max_time=0.2))
        invalid.append(dict(base, distance=-0.01))
        invalid.append(dict(base, distance=100.01))
        invalid.append(dict(base, cursor_time=float('nan')))
        missing_cursor = dict(base)
        del missing_cursor['cursor_time']
        invalid.append(missing_cursor)

        for snapshot in invalid:
            self.assertFalse(manager.restore(snapshot))
            self.assertEqual([], manager.snapshot())
        self.assertTrue(manager.restore(base))

    def test_restored_trajectory_is_invariant_at_30_60_and_120_fps(self):
        terminal_states = []
        for frames_per_second in (30, 60, 120):
            manager = self.module.InFlightProjectiles(initial_time=0.37)
            self.assertTrue(manager.restore({
                'key': 'handoff',
                'start': (2.0, 5.0, -3.0),
                'velocity': (80.0, 15.0, 12.0),
                'gravity': (0.0, -9.81, 0.0),
                'launch_time': 0.0,
                'max_time': 1.0,
                'max_distance': 5000.0,
                'cursor_time': 0.37,
                'distance': 31.25,
                'payload': {'owner_epoch': 3},
            }))
            first_chord = []
            terminals = []
            for frame in range(frames_per_second):
                now = 0.37 + 0.63 * float(
                    frame + 1) / frames_per_second

                def observe(_state, _start, _end, first, _last):
                    if not first_chord:
                        first_chord.append(first)

                manager.advance(
                    now, observe,
                    lambda state, terminal:
                    terminals.append((state, terminal)))

            self.assertAlmostEqual(0.37, first_chord[0])
            self.assertEqual('max_time', terminals[0][1]['reason'])
            terminal_states.append(terminals[0][0])

        for state in terminal_states:
            self.assertAlmostEqual(82.0, state['position'][0])
            self.assertAlmostEqual(15.095, state['position'][1])
            self.assertAlmostEqual(9.0, state['position'][2])
            self.assertAlmostEqual(1.0, state['elapsed'])
        for state in terminal_states[1:]:
            self.assertAlmostEqual(
                terminal_states[0]['distance'], state['distance'], places=3)

    def test_restore_freezes_payload_and_recomputes_position(self):
        manager = self.module.InFlightProjectiles(initial_time=0.25)
        start = [3.0, 4.0, 5.0]
        payload = {'effects': ['damage'], 'nested': {'value': 2}}
        takeover = {
            'key': ['player', 7, 11],
            'start': start,
            'velocity': (20.0, 8.0, -4.0),
            'gravity': (0.0, -10.0, 0.0),
            'launch_time': 0.0,
            'max_time': 3.0,
            'max_distance': 1000.0,
            'cursor_time': 0.25,
            'distance': 5.5,
            'position': (-1.0, -1.0, -1.0),
            'payload': payload,
        }
        self.assertTrue(manager.restore(takeover))
        start[0] = 100.0
        payload['effects'].append('stun')
        payload['nested']['value'] = 99

        snapshot = manager.get(('player', 7, 11))
        self.assertEqual((3.0, 4.0, 5.0), snapshot['start'])
        self.assertEqual((8.0, 5.6875, 4.0), snapshot['position'])
        self.assertEqual(['damage'], snapshot['payload']['effects'])
        self.assertEqual(2, snapshot['payload']['nested']['value'])
        snapshot['payload']['effects'].append('mutated')
        snapshot['position'] = (0.0, 0.0, 0.0)

        fresh = manager.get(('player', 7, 11))
        self.assertEqual(['damage'], fresh['payload']['effects'])
        self.assertEqual((8.0, 5.6875, 4.0), fresh['position'])

    def test_restore_honours_unique_keys_capacity_retirement_and_future(self):
        def takeover(key, cursor_time=0.5):
            return {
                'key': key,
                'start': (0.0, 0.0, 0.0),
                'velocity': (10.0, 0.0, 0.0),
                'gravity': (0.0, 0.0, 0.0),
                'launch_time': 0.0,
                'max_time': 2.0,
                'max_distance': 100.0,
                'cursor_time': cursor_time,
                'distance': 5.0,
                'payload': None,
            }

        manager = self.module.InFlightProjectiles(
            maximum_active=1, initial_time=1.0)
        self.assertTrue(manager.restore(takeover('a')))
        self.assertFalse(manager.restore(takeover('a')))
        self.assertFalse(manager.restore(takeover('b')))
        self.assertTrue(manager.remove('a'))
        self.assertFalse(manager.restore(takeover('a')))
        self.assertTrue(manager.restore(takeover('b')))
        self.assertTrue(manager.reset())
        self.assertTrue(manager.restore(takeover('a')))

        future_manager = self.module.InFlightProjectiles(initial_time=1.0)
        self.assertFalse(future_manager.restore(takeover(
            'future', cursor_time=1.01)))
        self.assertEqual([], future_manager.snapshot())

    def test_global_budget_caps_29_and_128_projectile_stall_and_catches_up(self):
        for active_count in (29, 128):
            manager = self.module.InFlightProjectiles(
                maximum_active=active_count)
            keys = ['shot-%d' % index for index in range(active_count)]
            for key in keys:
                self.assertTrue(_launch(
                    manager, key=key, max_time=2.0,
                    max_distance=10000.0))
            callbacks = dict((key, 0) for key in keys)
            callback_total = [0]

            def observe(state, _start, _end, _first, _last):
                callbacks[state['key']] += 1
                callback_total[0] += 1

            # One second of backlog would otherwise cost twenty chords per
            # projectile in one frame.  A one-chord-per-projectile budget
            # bounds this invocation while giving every launch finite work.
            self.assertTrue(manager.advance(
                1.0, observe, lambda _state, _terminal: None,
                maximum_chords=active_count))
            self.assertEqual(active_count, callback_total[0])
            self.assertEqual(
                set([1]), set(callbacks.values()))
            self.assertAlmostEqual(1.0, manager.now)
            for key in keys:
                self.assertAlmostEqual(0.05, manager.get(key)['elapsed'])

            invocations = 1
            while any(manager.get(key)['elapsed'] < 1.0 - 1e-9
                      for key in keys):
                before = callback_total[0]
                self.assertTrue(manager.advance(
                    1.0, observe, lambda _state, _terminal: None,
                    maximum_chords=active_count))
                self.assertLessEqual(
                    callback_total[0] - before, active_count)
                invocations += 1
                self.assertLessEqual(invocations, 21)

            self.assertEqual(20, invocations)
            self.assertEqual(active_count * 20, callback_total[0])
            for key in keys:
                state = manager.get(key)
                self.assertAlmostEqual(1.0, state['elapsed'])
                self.assertAlmostEqual(100.0, state['position'][0])

    def test_stock_max_29_projectile_backlog_is_bounded_and_catches_at_17fps(self):
        active_count = 29

        def populated_manager():
            manager = self.module.InFlightProjectiles(
                maximum_active=active_count)
            for index in range(active_count):
                self.assertTrue(_launch(
                    manager, key='shot-%d' % index,
                    gravity=(0.0, -190.0, 0.0), max_time=10.0,
                    max_distance=100000.0))
            return manager

        # The exact #1513 maximum stock gravity takes 29 * 22 = 638 chords,
        # versus 29 * 40 = 1,160 at the previous fixed 25 ms step.
        fixed = populated_manager()
        fixed_callbacks = [0]
        fixed_invocations = 0
        while fixed.last_advance_metrics()['debt_after'] > 1e-9 or not fixed_invocations:
            self.assertTrue(fixed.advance(
                1.0,
                lambda *_args: fixed_callbacks.__setitem__(
                    0, fixed_callbacks[0] + 1),
                lambda _state, _terminal: None,
                maximum_chords=active_count * 2))
            fixed_invocations += 1
        self.assertEqual(11, fixed_invocations)
        self.assertEqual(638, fixed_callbacks[0])

        # With a live 17 Hz target clock, 58 chords per frame sustains all 29
        # projectiles and retires a one-second initial debt in 29 callbacks
        # (28 render intervals, about 1.647 seconds) without later drift.
        live = populated_manager()
        target = 1.0
        catchup_invocations = 0
        while True:
            before = sum(
                int(state['elapsed'] * 1000000.0)
                for state in live.snapshot())
            self.assertTrue(live.advance(
                target, lambda *_args: None,
                lambda _state, _terminal: None,
                maximum_chords=active_count * 2))
            metrics = live.last_advance_metrics()
            self.assertLessEqual(metrics['chords'], active_count * 2)
            after = sum(
                int(state['elapsed'] * 1000000.0)
                for state in live.snapshot())
            self.assertGreater(after, before)
            catchup_invocations += 1
            if metrics['debt_after'] <= 1e-9:
                break
            target += 1.0 / 17.0
            self.assertLess(catchup_invocations, 30)

        self.assertEqual(29, catchup_invocations)
        self.assertAlmostEqual(28.0 / 17.0, target - 1.0)
        for unused_frame in range(60):
            target += 1.0 / 17.0
            self.assertTrue(live.advance(
                target, lambda *_args: None,
                lambda _state, _terminal: None,
                maximum_chords=active_count * 2))
            metrics = live.last_advance_metrics()
            self.assertEqual(58, metrics['chords'])
            self.assertLessEqual(metrics['debt_after'], 1e-9)

    def test_sustainable_budget_covers_stock_capacity_and_protocol_gravity(self):
        interval = 1.0 / 15.0
        stock = self.module.InFlightProjectiles(maximum_active=128)
        for index in range(128):
            self.assertTrue(_launch(
                stock, key='stock-%d' % index,
                gravity=(0.0, -190.0, 0.0), max_time=10.0,
                max_distance=100000.0))
        self.assertEqual(256, stock.sustainable_chord_budget(interval))

        stock_target = 1.0
        stock_invocations = 0
        while True:
            self.assertTrue(stock.advance(
                stock_target, lambda *_args: None,
                lambda _state, _terminal: None,
                maximum_chords=256))
            stock_invocations += 1
            if stock.last_advance_metrics()['debt_after'] <= 1e-9:
                break
            stock_target += 1.0 / 17.0
            self.assertLess(stock_invocations, 30)
        self.assertEqual(29, stock_invocations)
        for unused_frame in range(60):
            stock_target += 1.0 / 17.0
            self.assertTrue(stock.advance(
                stock_target, lambda *_args: None,
                lambda _state, _terminal: None,
                maximum_chords=256))
            metrics = stock.last_advance_metrics()
            self.assertEqual(256, metrics['chords'])
            self.assertLessEqual(metrics['debt_after'], 1e-9)

        protocol = self.module.InFlightProjectiles(maximum_active=29)
        for index in range(29):
            self.assertTrue(_launch(
                protocol, key='protocol-%d' % index,
                gravity=(0.0, -500.0, 0.0), max_time=10.0,
                max_distance=100000.0))
        self.assertEqual(87, protocol.sustainable_chord_budget(interval))

        target = 1.0
        invocations = 0
        while True:
            self.assertTrue(protocol.advance(
                target, lambda *_args: None,
                lambda _state, _terminal: None,
                maximum_chords=87))
            invocations += 1
            if protocol.last_advance_metrics()['debt_after'] <= 1e-9:
                break
            target += 1.0 / 17.0
            self.assertLess(invocations, 40)
        self.assertEqual(37, invocations)
        for unused_frame in range(60):
            target += 1.0 / 17.0
            self.assertTrue(protocol.advance(
                target, lambda *_args: None,
                lambda _state, _terminal: None,
                maximum_chords=87))
            metrics = protocol.last_advance_metrics()
            self.assertEqual(87, metrics['chords'])
            self.assertLessEqual(metrics['debt_after'], 1e-9)

    def test_advance_metrics_report_chords_debt_and_one_terminal(self):
        manager = self.module.InFlightProjectiles()
        self.assertTrue(_launch(manager, max_time=0.1))
        terminals = []

        self.assertTrue(manager.advance(
            0.1, lambda *_args: None,
            lambda state, terminal: terminals.append((state, terminal)),
            maximum_chords=1))
        first = manager.last_advance_metrics()
        self.assertEqual(1, first['active'])
        self.assertEqual(1, first['chords'])
        self.assertEqual(0, first['terminals'])
        self.assertAlmostEqual(0.1, first['debt_before'])
        self.assertAlmostEqual(0.05, first['debt_after'])

        first['chords'] = 999
        self.assertEqual(1, manager.last_advance_metrics()['chords'])
        self.assertTrue(manager.advance(
            0.1, lambda *_args: None,
            lambda state, terminal: terminals.append((state, terminal)),
            maximum_chords=1))
        second = manager.last_advance_metrics()
        self.assertEqual(1, second['chords'])
        self.assertEqual(1, second['terminals'])
        self.assertAlmostEqual(0.0, second['debt_after'])
        self.assertEqual(1, len(terminals))

    def test_small_budget_rotates_persistently_without_starvation(self):
        manager = self.module.InFlightProjectiles(maximum_active=5)
        keys = ['a', 'b', 'c', 'd', 'e']
        for key in keys:
            self.assertTrue(_launch(manager, key=key))
        observed = []

        for unused_invocation in range(5):
            self.assertTrue(manager.advance(
                0.1,
                lambda state, _start, _end, _first, _last:
                observed.append(state['key']),
                lambda _state, _terminal: None,
                maximum_chords=2))

        self.assertEqual(keys + keys, observed)
        for key in keys:
            self.assertAlmostEqual(0.10, manager.get(key)['elapsed'])

    def test_bounded_terminal_is_emitted_once_after_cross_frame_catch_up(self):
        manager = self.module.InFlightProjectiles(maximum_active=3)
        keys = ['a', 'b', 'c']
        for key in keys:
            self.assertTrue(_launch(
                manager, key=key, max_time=0.05,
                max_distance=10000.0))
        chords = dict((key, 0) for key in keys)
        terminals = []

        def observe(state, _start, _end, _first, _last):
            chords[state['key']] += 1

        for unused_invocation in range(12):
            self.assertTrue(manager.advance(
                1.0, observe,
                lambda state, terminal:
                terminals.append((state['key'], terminal['reason'])),
                maximum_chords=1))

        self.assertEqual({'a': 1, 'b': 1, 'c': 1}, chords)
        self.assertEqual(
            [('a', 'max_time'), ('b', 'max_time'), ('c', 'max_time')],
            terminals)
        self.assertEqual([], manager.snapshot())

        # Repeating the same absolute target after retirement cannot replay a
        # terminal edge or resurrect any consumed trajectory work.
        self.assertTrue(manager.advance(
            1.0, observe,
            lambda state, terminal:
            terminals.append((state['key'], terminal['reason'])),
            maximum_chords=3))
        self.assertEqual(3, len(terminals))

    def test_bounded_api_accepts_zero_and_rejects_non_integer_limits(self):
        manager = self.module.InFlightProjectiles()
        self.assertTrue(_launch(manager))
        observed = []

        self.assertTrue(manager.advance(
            0.1, lambda *args: observed.append(args),
            lambda _state, _terminal: None, maximum_chords=0))
        self.assertEqual([], observed)
        self.assertAlmostEqual(0.0, manager.get('shot')['elapsed'])
        self.assertAlmostEqual(0.1, manager.now)
        self.assertFalse(manager.advance(
            0.1, lambda *_args: None, lambda *_args: None,
            maximum_chords=True))
        self.assertFalse(manager.advance(
            0.1, lambda *_args: None, lambda *_args: None,
            maximum_chords=1.0))
        self.assertFalse(manager.advance(
            0.1, lambda *_args: None, lambda *_args: None,
            maximum_chords=-1))


if __name__ == '__main__':
    unittest.main()
