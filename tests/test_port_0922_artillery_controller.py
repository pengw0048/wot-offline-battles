import importlib.util
import math
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (ROOT / 'src' / 'res' / 'scripts' /
                'client' / 'gui' / 'mods' / 'offline_lan_0922')


def _load():
    for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(PACKAGE_ROOT)]
            sys.modules[name] = module
    name = 'gui.mods.offline_lan_0922.artillery_controller'
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        name, PACKAGE_ROOT / 'artillery_controller.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _descriptor(pitch=(-0.8, 0.15)):
    shot = types.SimpleNamespace(
        speed=425.0, gravity=143.0, maxDistance=10000.0)
    gun = types.SimpleNamespace(
        shots=(shot,), pitchLimits={'absolute': pitch})
    return types.SimpleNamespace(gun=gun)


class ArtilleryControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load()

    @staticmethod
    def source():
        return {'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0}

    @staticmethod
    def target():
        return {
            'kind': 'human', 'network_id': 2,
            'position': (0.0, 0.0, 560.0),
            'yaw': math.pi * 0.5, 'speed': 8.0,
        }

    def test_real_b4_pitch_limit_uses_valid_low_root_not_invalid_high_root(self):
        controller = self.module.ArtilleryController()
        candidates = controller._candidates(
            self.source(), self.target(), _descriptor(), 0)

        self.assertEqual(1, len(candidates))
        self.assertEqual('low', candidates[0]['arc'])
        self.assertGreaterEqual(candidates[0]['pitch'], -0.8)
        self.assertLessEqual(candidates[0]['pitch'], 0.15)
        self.assertGreater(candidates[0]['aim_position'][0], 1.0)

    def test_low_blocked_high_clear_selects_high_only_when_pitch_allows_it(self):
        controller = self.module.ArtilleryController()
        source = self.source()
        target = dict(self.target(), position=(0.0, 0.0, 200.0), speed=0.0)
        candidates = controller._candidates(
            source, target, _descriptor((-1.55, 0.15)), 0)
        self.assertEqual(['low', 'high'], [item['arc'] for item in candidates])
        controller.request(source, target, _descriptor((-1.55, 0.15)), 0, 1.0)

        def probe(first, second):
            crosses_wall = first[2] <= 50.0 <= second[2]
            return ((0.0, 5.0, 50.0)
                    if crosses_wall and max(first[1], second[1]) < 10.0
                    else None)

        for index in range(100):
            controller.advance(1.0 + index * 0.01, 4, probe)
            ready, solution = controller.result(
                source, target, 0, 1.0 + index * 0.01)
            if ready:
                break
        self.assertTrue(ready)
        self.assertEqual('high', solution['arc'])

    def test_solution_is_pending_until_every_chord_is_probed(self):
        controller = self.module.ArtilleryController()
        source = self.source()
        target = self.target()
        self.assertIsNone(controller.solution(
            source, target, _descriptor(), 0, 2.0))
        self.assertEqual(4, controller.advance(
            2.0, 4, lambda unused_a, unused_b: None))
        self.assertIsNone(controller.solution(
            source, target, _descriptor(), 0, 2.0))
        for index in range(100):
            controller.advance(
                2.1 + index * 0.01, 4,
                lambda unused_a, unused_b: None)
            value = controller.solution(
                source, target, _descriptor(), 0, 2.1 + index * 0.01)
            if value is not None:
                break
        self.assertIsNotNone(value)
        self.assertEqual('low', value['arc'])

    def test_pose_change_has_a_distinct_fail_closed_job(self):
        controller = self.module.ArtilleryController()
        target = self.target()
        controller.request(self.source(), target, _descriptor(), 0, 3.0)
        moved = dict(self.source(), x=5.0)
        self.assertEqual((False, None), controller.result(
            moved, target, 0, 3.0))

    def test_moving_target_job_can_finish_but_solution_expires_quickly(self):
        controller = self.module.ArtilleryController()
        source = self.source()
        target = self.target()
        self.assertIsNone(controller.solution(
            source, target, _descriptor(), 0, 4.0))

        solution = None
        now = 4.0
        for frame in range(20):
            now += 0.04
            controller.advance(
                now, 4, lambda unused_a, unused_b: None)
            moved = dict(target)
            moved['position'] = (
                target['position'][0] + 8.0 * (now - 4.0),
                target['position'][1], target['position'][2])
            solution = controller.solution(
                source, moved, _descriptor(), 0, now)
            if solution is not None:
                break

        self.assertIsNotNone(solution)
        self.assertLess(now - 4.0, 0.35)
        self.assertIsNone(controller.solution(
            source, moved, _descriptor(), 0, now + 0.36))

    def test_moving_target_preserves_job_and_reaches_clear_high_arc(self):
        controller = self.module.ArtilleryController(maximum_step=0.12)
        source = self.source()
        descriptor = _descriptor((-1.55, 0.15))
        target = dict(
            self.target(), position=(0.0, 0.0, 200.0),
            yaw=math.pi * 0.5, speed=8.0)
        controller.request(source, target, descriptor, 0, 0.0)
        original_key = controller._planning_keys[(11, 'human', 2, 0)]

        def low_blocked(start, end):
            crosses_wall = start[2] <= 50.0 <= end[2]
            if crosses_wall and max(start[1], end[1]) < 20.0:
                return (0.0, 5.0, 50.0)
            return None

        solution = None
        for frame in range(1, 201):
            now = frame / 20.0
            controller.advance(now, 4, low_blocked)
            moved = dict(target)
            moved['position'] = (8.0 * now, 0.0, 200.0)
            solution = controller.solution(
                source, moved, descriptor, 0, now)
            current_key = controller._planning_keys[
                (11, 'human', 2, 0)]
            self.assertEqual(original_key, current_key)
            if solution is not None:
                break

        self.assertIsNotNone(solution)
        self.assertEqual('high', solution['arc'])
        self.assertLess(now, 3.0)

    def test_proved_arc_is_reled_from_current_moving_target_pose(self):
        controller = self.module.ArtilleryController(maximum_step=0.12)
        source = self.source()
        descriptor = _descriptor((-1.55, 0.15))
        initial = dict(
            self.target(), position=(0.0, 0.0, 200.0),
            yaw=math.pi * 0.5, speed=8.0)
        controller.request(source, initial, descriptor, 0, 0.0)
        original = controller.queue.jobs[
            controller._planning_keys[(11, 'human', 2, 0)]
        ]['candidates'][0]
        for frame in range(1, 20):
            now = frame / 24.0
            controller.advance(
                now, 4, lambda unused_start, unused_end: None)
            current = dict(initial, position=(8.0 * now, 0.0, 200.0))
            solution = controller.solution(
                source, current, descriptor, 0, now)
            if solution is not None:
                break

        self.assertIsNotNone(solution)
        expected_x = current['position'][0] + 8.0 * solution['flight_time']
        self.assertAlmostEqual(expected_x, solution['aim_position'][0], 6)
        self.assertNotEqual(
            original['aim_position'][0], solution['aim_position'][0])

    def test_different_targets_do_not_replace_each_others_planning_jobs(self):
        controller = self.module.ArtilleryController()
        source = self.source()
        first = self.target()
        second = dict(first, network_id=3, position=(100.0, 0.0, 560.0))
        controller.request(source, first, _descriptor(), 0, 1.0)
        first_key = controller._planning_keys[(11, 'human', 2, 0)]
        controller.request(source, second, _descriptor(), 0, 1.0)
        second_key = controller._planning_keys[(11, 'human', 3, 0)]

        self.assertNotEqual(first_key, second_key)
        self.assertIn(first_key, controller.queue.jobs)
        self.assertIn(second_key, controller.queue.jobs)

    def test_two_moving_targets_reach_clear_high_arcs_within_ten_seconds(self):
        controller = self.module.ArtilleryController(maximum_step=0.12)
        descriptor = _descriptor((-1.55, 0.15))
        sources = [dict(self.source(), id=11 + index, x=float(index))
                   for index in range(2)]
        targets = [dict(
            self.target(), network_id=2 + index,
            position=(0.0, 0.0, 200.0),
            yaw=math.pi * 0.5, speed=8.0)
            for index in range(2)]

        def low_blocked(start, end):
            crosses_wall = start[2] <= 50.0 <= end[2]
            if crosses_wall and max(start[1], end[1]) < 20.0:
                return (0.0, 5.0, 50.0)
            return None

        completed = {}
        for frame in range(1, 241):
            now = frame / 24.0
            probe_calls = [0]

            def counted_probe(start, end):
                probe_calls[0] += 1
                return low_blocked(start, end)

            used = controller.advance(now, 4, counted_probe)
            self.assertEqual(probe_calls[0], used)
            self.assertLessEqual(used, 4)
            for source, initial in zip(sources, targets):
                moved = dict(initial)
                moved['position'] = (8.0 * now, 0.0, 200.0)
                solution = controller.solution(
                    source, moved, descriptor, 0, now)
                if solution is not None:
                    completed.setdefault(source['id'], solution)
            if len(completed) == 2:
                break

        self.assertEqual(2, len(completed))
        self.assertLessEqual(now, 10.0)
        self.assertTrue(all(
            solution['arc'] == 'high'
            for solution in completed.values()))

    def test_exact_launch_keeps_point_twelve_second_chords(self):
        controller = self.module.ArtilleryController(maximum_step=0.12)
        source = self.source()
        target = dict(self.target(), speed=0.0)
        controller.request_launch(
            source, target, _descriptor(), 0, 1,
            (0.0, 2.0, 0.0), 0.01, 0.08, 0.8, 1.0)
        key = controller._launch_keys[11]
        path = controller.launch_queue.jobs[key]['candidates'][0]['path']

        self.assertGreaterEqual(len(path), 2)
        self.assertLessEqual(0.8 / float(len(path) - 1), 0.12)

    def test_cancel_launch_discards_pending_and_pinned_receipts(self):
        controller = self.module.ArtilleryController(maximum_step=0.2)
        source = self.source()
        target = dict(self.target(), speed=0.0)
        arguments = (
            source, target, _descriptor(), 0, 1,
            (0.0, 2.0, 0.0), 0.01, 0.08, 0.4)
        controller.request_launch(*(arguments + (1.0,)))
        pending_key = controller._launch_keys[11]
        self.assertTrue(controller.cancel_launch(source))
        self.assertNotIn(pending_key, controller.launch_queue.jobs)
        self.assertNotIn(11, controller._launch_keys)

        controller.request_launch(*(arguments + (2.0,)))
        controller.advance(2.01, 8, lambda unused_a, unused_b: None)
        ready, receipt = controller.request_launch(*(arguments + (2.01,)))
        self.assertTrue(ready)
        self.assertIsNotNone(receipt)
        self.assertTrue(controller.cancel_launch(source))
        self.assertNotIn(receipt['proof_key'], controller._launch_receipts)
        self.assertFalse(controller.cancel_launch(source))

    def test_frozen_moving_intents_complete_exact_proof_at_20_and_24_fps(self):
        descriptor = _descriptor((-1.55, 0.15))

        def low_blocked(start, end):
            crosses_wall = start[2] <= 50.0 <= end[2]
            if crosses_wall and max(start[1], end[1]) < 20.0:
                return (0.0, 5.0, 50.0)
            return None

        for fps in (20, 24):
            for count in (1, 2):
                with self.subTest(fps=fps, count=count):
                    controller = self.module.ArtilleryController(
                        maximum_step=0.12)
                    sources = [dict(
                        self.source(), id=11 + index, x=float(index))
                        for index in range(count)]
                    targets = [dict(
                        self.target(), network_id=2 + index,
                        position=(0.0, 0.0, 200.0),
                        yaw=math.pi * 0.5, speed=8.0)
                        for index in range(count)]
                    intents = {}
                    receipts = {}
                    launch_keys = dict((source['id'], set())
                                       for source in sources)
                    for frame in range(1, fps * 20 + 1):
                        now = frame / float(fps)
                        used = controller.advance(now, 4, low_blocked)
                        self.assertLessEqual(used, 4)
                        for source, initial in zip(sources, targets):
                            target = dict(initial)
                            target['position'] = (
                                8.0 * now, 0.0, 200.0)
                            intent = intents.get(source['id'])
                            if intent is None:
                                solution = controller.solution(
                                    source, target, descriptor, 0, now)
                                if solution is None:
                                    continue
                                intent = (
                                    solution['yaw'], -solution['pitch'],
                                    solution['flight_time'])
                                intents[source['id']] = intent
                            ready, receipt = controller.request_launch(
                                source, target, descriptor, 0, 1,
                                (source['x'], 2.0, 0.0),
                                intent[0], intent[1], intent[2], now)
                            launch_keys[source['id']].add(
                                controller._launch_keys[source['id']])
                            if ready and receipt is not None:
                                receipts[source['id']] = receipt
                        if len(receipts) == count:
                            break

                    self.assertEqual(count, len(receipts))
                    self.assertTrue(all(
                        len(keys) == 1 for keys in launch_keys.values()))
                    self.assertLess(now, 20.0)

    def test_exact_launch_uses_native_muzzle_and_dispersed_physical_angles(self):
        controller = self.module.ArtilleryController(maximum_step=0.05)
        source = self.source()
        target = dict(self.target(), speed=0.0)
        origin = (3.25, 2.75, -4.5)
        shot_yaw = 0.037
        shot_pitch = 0.091
        ready, receipt = controller.request_launch(
            source, target, _descriptor(), 0, 1, origin,
            shot_yaw, shot_pitch, 0.8, 5.0)
        self.assertFalse(ready)
        self.assertIsNone(receipt)
        key = controller._launch_keys[11]
        candidate = controller.launch_queue.jobs[key]['candidates'][0]
        self.assertEqual(origin, candidate['path'][0])
        self.assertEqual(origin, candidate['origin'])
        self.assertEqual(shot_yaw, candidate['shot_yaw'])
        self.assertEqual(shot_pitch, candidate['shot_pitch'])

        first_end = candidate['path'][1]
        hit = tuple(
            origin[index] + (first_end[index] - origin[index]) * 0.25
            for index in range(3))
        probes = []

        def near_muzzle_obstruction(start, end):
            probes.append((start, end))
            return hit

        self.assertEqual(1, controller.advance(
            5.01, 1, near_muzzle_obstruction))
        self.assertEqual((origin, first_end), probes[0])
        self.assertEqual((True, None), controller.request_launch(
            source, target, _descriptor(), 0, 1, origin,
            shot_yaw, shot_pitch, 0.8, 5.01))

    def test_exact_launch_receipt_freezes_probed_origin_and_velocity(self):
        controller = self.module.ArtilleryController(maximum_step=0.2)
        source = self.source()
        target = dict(self.target(), speed=0.0)
        origin = (7.0, 3.0, -2.0)
        yaw = -0.043
        pitch = 0.076
        controller.request_launch(
            source, target, _descriptor(), 0, 4, origin,
            yaw, pitch, 0.4, 6.0)
        for frame in range(10):
            controller.advance(
                6.01 + frame * 0.01, 4,
                lambda unused_start, unused_end: None)
            ready, receipt = controller.request_launch(
                source, target, _descriptor(), 0, 4, origin,
                yaw, pitch, 0.4, 6.01 + frame * 0.01)
            if ready:
                break

        self.assertTrue(ready)
        self.assertIsNotNone(receipt)
        self.assertEqual(origin, receipt['origin'])
        self.assertIn(receipt['proof_key'], controller._launch_receipts)
        self.assertNotIn(receipt['proof_key'], controller.launch_queue.results)
        speed = 425.0
        expected = (
            math.sin(yaw) * math.cos(pitch) * speed,
            math.sin(pitch) * speed,
            math.cos(yaw) * math.cos(pitch) * speed,
        )
        self.assertEqual(expected, receipt['velocity'])
        self.assertLessEqual(receipt['max_time_ms'], 20000)

    def test_exact_launch_origin_or_angle_change_invalidates_pending_proof(self):
        controller = self.module.ArtilleryController(maximum_step=0.05)
        source = self.source()
        target = dict(self.target(), speed=0.0)
        first_origin = (1.0, 2.0, 3.0)
        controller.request_launch(
            source, target, _descriptor(), 0, 2, first_origin,
            0.01, 0.08, 0.8, 7.0)
        first_key = controller._launch_keys[11]
        controller.advance(7.01, 1, lambda unused_a, unused_b: None)

        second_origin = (1.0001, 2.0, 3.0)
        self.assertEqual((False, None), controller.request_launch(
            source, target, _descriptor(), 0, 2, second_origin,
            0.01, 0.08, 0.8, 7.02))
        second_key = controller._launch_keys[11]
        self.assertNotEqual(first_key, second_key)
        self.assertNotIn(first_key, controller.launch_queue.jobs)
        self.assertNotIn(first_key, controller.launch_queue.results)

        self.assertEqual((False, None), controller.request_launch(
            source, target, _descriptor(), 0, 2, second_origin,
            0.010001, 0.08, 0.8, 7.03))
        angle_key = controller._launch_keys[11]
        self.assertNotEqual(second_key, angle_key)
        self.assertNotIn(second_key, controller.launch_queue.jobs)

        self.assertEqual((False, None), controller.request_launch(
            source, target, _descriptor(), 0, 3, second_origin,
            0.010001, 0.08, 0.8, 7.04))
        self.assertNotEqual(angle_key, controller._launch_keys[11])
        self.assertNotIn(angle_key, controller.launch_queue.jobs)

    def test_invalid_launch_input_discards_completed_proof_before_revert(self):
        controller = self.module.ArtilleryController(maximum_step=0.2)
        source = self.source()
        target = dict(self.target(), speed=0.0)
        origin = (2.0, 3.0, 4.0)
        arguments = (
            source, target, _descriptor(), 0, 5, origin,
            0.02, 0.07, 0.4)
        controller.request_launch(*(arguments + (8.0,)))
        controller.advance(
            8.01, 8, lambda unused_start, unused_end: None)
        ready, receipt = controller.request_launch(*(arguments + (8.01,)))
        self.assertTrue(ready)
        self.assertIsNotNone(receipt)
        proved_key = receipt['proof_key']

        invalid = list(arguments)
        invalid[6] = float('nan')
        self.assertEqual((True, None), controller.request_launch(
            *(tuple(invalid) + (8.02,))))
        self.assertNotIn(11, controller._launch_keys)
        self.assertNotIn(proved_key, controller.launch_queue.results)
        self.assertNotIn(proved_key, controller._launch_receipts)

        self.assertEqual((False, None), controller.request_launch(
            *(arguments + (8.03,))))

    def test_twenty_second_exact_launch_completes_at_24_fps_budget(self):
        controller = self.module.ArtilleryController(maximum_step=0.12)
        source = self.source()
        target = dict(self.target(), speed=0.0)
        arguments = (
            source, target, _descriptor(), 0, 6, (0.0, 2.0, 0.0),
            0.01, 0.08, 20.0)
        self.assertEqual((False, None), controller.request_launch(
            *(arguments + (0.0,))))

        receipt = None
        for frame in range(1, 61):
            now = frame / 24.0
            controller.advance(
                now, 4, lambda unused_start, unused_end: None)
            ready, receipt = controller.request_launch(
                *(arguments + (now,)))
            if ready:
                break

        self.assertIsNotNone(receipt)
        self.assertLessEqual(now, 2.0)
        self.assertEqual(20000, receipt['max_time_ms'])

    def test_eight_long_exact_launches_finish_before_job_lifetime(self):
        controller = self.module.ArtilleryController(maximum_step=0.12)
        descriptor = _descriptor()
        target = dict(self.target(), speed=0.0)
        requests = {}
        for index in range(8):
            source = dict(self.source(), id=11 + index, x=float(index))
            requests[11 + index] = (
                source, target, descriptor, 0, 1,
                (float(index), 2.0, 0.0), 0.01, 0.08, 20.0)
            self.assertEqual((False, None), controller.request_launch(
                *(requests[11 + index] + (0.0,))))

        completed = {}
        for frame in range(1, 361):
            now = frame / 24.0
            controller.advance(
                now, 4, lambda unused_start, unused_end: None)
            for bot_id in list(requests):
                ready, receipt = controller.request_launch(
                    *(requests[bot_id] + (now,)))
                if ready and receipt is not None:
                    completed[bot_id] = receipt
                    requests.pop(bot_id)
            if not requests:
                break

        self.assertEqual(8, len(completed))
        self.assertLess(now, 20.0)

    def test_eight_long_strategic_arcs_finish_at_real_frame_budget(self):
        controller = self.module.ArtilleryController(maximum_step=0.12)
        shot = types.SimpleNamespace(
            speed=100.0, gravity=1.0, maxDistance=10000.0)
        descriptor = types.SimpleNamespace(gun=types.SimpleNamespace(
            shots=(shot,), pitchLimits={'absolute': (-1.55, 0.15)}))
        target = {
            'kind': 'human', 'network_id': 2,
            'position': (0.0, 0.0, 1900.0), 'speed': 0.0,
        }
        requests = {}
        for index in range(8):
            source = {
                'id': 11 + index, 'x': float(index),
                'y': 0.0, 'z': 0.0,
            }
            requests[11 + index] = (source, target, descriptor, 0)
            controller.request(*(requests[11 + index] + (0.0,)))

        completed = {}
        for frame in range(1, 481):
            now = frame / 24.0
            controller.advance(
                now, 4, lambda unused_start, unused_end: None)
            for bot_id, arguments in requests.items():
                solution = controller.solution(*(arguments + (now,)))
                if solution is not None:
                    completed.setdefault(bot_id, solution)
            if len(completed) == 8:
                break

        self.assertEqual(8, len(completed))
        self.assertLess(now, 20.0)
        self.assertTrue(all(
            solution['flight_time'] > 19.0
            for solution in completed.values()))

    def test_eight_blocked_low_clear_high_arcs_finish_at_real_budget(self):
        controller = self.module.ArtilleryController(maximum_step=0.12)
        shot = types.SimpleNamespace(
            speed=60.0, gravity=5.0, maxDistance=10000.0)
        descriptor = types.SimpleNamespace(gun=types.SimpleNamespace(
            shots=(shot,), pitchLimits={'absolute': (-1.55, 0.15)}))
        target = {
            'kind': 'human', 'network_id': 2,
            'position': (0.0, 0.0, 700.0), 'speed': 0.0,
        }
        requests = {}
        for index in range(8):
            source = {
                'id': 11 + index, 'x': float(index),
                'y': 0.0, 'z': 0.0,
            }
            requests[11 + index] = (source, target, descriptor, 0)
            controller.request(*(requests[11 + index] + (0.0,)))

        def low_blocked(start, end):
            crosses_wall = start[2] <= 200.0 <= end[2]
            if crosses_wall and max(start[1], end[1]) < 150.0:
                return (0.0, 120.0, 200.0)
            return None

        completed = {}
        for frame in range(1, 721):
            now = frame / 24.0
            controller.advance(now, 4, low_blocked)
            for bot_id, arguments in requests.items():
                solution = controller.solution(*(arguments + (now,)))
                if solution is not None:
                    completed.setdefault(bot_id, solution)
            if len(completed) == 8:
                break

        self.assertEqual(8, len(completed))
        self.assertLess(now, 30.0)
        self.assertTrue(all(
            solution['arc'] == 'high'
            for solution in completed.values()))

    def test_changed_ninth_launch_discards_stale_waiting_key(self):
        controller = self.module.ArtilleryController(maximum_step=0.12)
        descriptor = _descriptor()
        target = dict(self.target(), speed=0.0)
        for index in range(9):
            source = dict(self.source(), id=11 + index)
            controller.request_launch(
                source, target, descriptor, 0, 1,
                (float(index), 2.0, 0.0), 0.01, 0.08, 20.0, 0.0)

        source = dict(self.source(), id=19)
        old_key = controller._launch_keys[19]
        self.assertIn(old_key, controller.launch_queue.waiting)
        self.assertIn(old_key, controller.launch_queue.waiting_order)

        controller.request_launch(
            source, target, descriptor, 0, 1,
            (8.001, 2.0, 0.0), 0.01, 0.08, 20.0, 0.01)
        new_key = controller._launch_keys[19]
        self.assertNotEqual(old_key, new_key)
        self.assertNotIn(old_key, controller.launch_queue.jobs)
        self.assertNotIn(old_key, controller.launch_queue.order)
        self.assertNotIn(old_key, controller.launch_queue.waiting)
        self.assertNotIn(old_key, controller.launch_queue.waiting_order)
        self.assertNotIn(old_key, controller.launch_queue.results)
        self.assertIn(new_key, controller.launch_queue.waiting)

    def test_eight_spg_planning_to_exact_receipts_do_not_ping_pong(self):
        controller = self.module.ArtilleryController(maximum_step=0.12)
        shot = types.SimpleNamespace(
            speed=60.0, gravity=5.0, maxDistance=10000.0)
        descriptor = types.SimpleNamespace(gun=types.SimpleNamespace(
            shots=(shot,), pitchLimits={'absolute': (-1.55, 0.15)}))
        target = {
            'kind': 'human', 'network_id': 2,
            'position': (0.0, 0.0, 700.0), 'speed': 0.0,
        }
        sources = [{
            'id': 11 + index, 'x': float(index),
            'y': 0.0, 'z': 0.0,
        } for index in range(8)]

        def low_blocked(start, end):
            crosses_wall = start[2] <= 200.0 <= end[2]
            if crosses_wall and max(start[1], end[1]) < 150.0:
                return (0.0, 120.0, 200.0)
            return None

        completed = {}
        for frame in range(1, 3601):
            now = frame / 24.0
            probe_calls = [0]

            def counted_probe(start, end):
                probe_calls[0] += 1
                return low_blocked(start, end)

            used = controller.advance(now, 4, counted_probe)
            self.assertEqual(probe_calls[0], used)
            self.assertLessEqual(used, 4)
            for source in sources:
                solution = controller.solution(
                    source, target, descriptor, 0, now)
                if solution is None:
                    continue
                ready, receipt = controller.request_launch(
                    source, target, descriptor, 0, 1,
                    (source['x'], 1.5, 0.0), solution['yaw'],
                    -solution['pitch'], solution['flight_time'], now)
                if ready and receipt is not None:
                    completed.setdefault(source['id'], receipt)
            if len(completed) == 8:
                break

        self.assertEqual(8, len(completed))
        self.assertLess(now, 75.0)
        self.assertTrue(all(
            receipt['arc'] == 'exact_launch'
            for receipt in completed.values()))


if __name__ == '__main__':
    unittest.main()
