import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CLIENT_PACKAGE = (ROOT / 'src' / 'res' / 'scripts' / 'client' /
                  'gui' / 'mods' / 'offline_lan_0922')
TOOLS = ROOT / 'tools'
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe_module = _load(
    'authority_worker_probe_under_test',
    CLIENT_PACKAGE / 'authority_worker_probe.py')
supervisor_module = _load(
    'authority_worker_probe_supervisor_under_test',
    TOOLS / 'authority_worker_probe_supervisor.py')
config_module = _load(
    'authority_worker_probe_config_under_test', CLIENT_PACKAGE / 'config.py')
from gui.mods.offline_lan_0922 import battle_runtime as battle_runtime_module


class _Clock(object):
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class _BigWorld(object):
    _MISSING = object()

    def __init__(self, draw=True):
        self.now = 10.0
        self.space_status = 1.0
        self.draw = bool(draw)
        self.draw_calls = []
        self.ignore_false = False

    def time(self):
        return self.now

    def spaceLoadStatus(self):
        return self.space_status

    def worldDrawEnabled(self, enabled=_MISSING):
        if enabled is self._MISSING:
            return self.draw
        self.draw_calls.append(bool(enabled))
        if not (self.ignore_false and not enabled):
            self.draw = bool(enabled)
        return self.draw


class AuthorityWorkerProbeTests(unittest.TestCase):
    def _advance(self, probe, clock, bigworld, counters, seconds, hz=30):
        ticks = int(seconds * hz)
        for index in range(ticks):
            step = 1.0 / hz
            clock.value += step
            bigworld.now += step
            counters['authority_callbacks'] += 1
            counters['alive_bot_ticks'] += 2
            counters['bot_probes']['ground'] += 2
            if index % max(1, hz // 30) == 0:
                counters['bot_state_generated'] += 1
                counters['bot_state_enqueued'] += 1
                counters['bot_state_revision'] += 1
            probe.tick()

    def test_three_stages_restore_draw_and_sample_only_at_one_hz(self):
        clock = _Clock()
        bigworld = _BigWorld(draw=True)
        records = []
        sample_calls = [0]
        counters = {
            'authority_callbacks': 0,
            'bot_state_generated': 0,
            'bot_state_enqueued': 0,
            'bot_state_send_failed': 0,
            'bot_state_revision': 0,
            'bot_probes': {'ground': 0, 'motion': 0},
            'bot_count': 2,
            'simulation_caps': 0,
            'alive_bot_ticks': 0,
        }

        def sample():
            sample_calls[0] += 1
            value = dict(counters)
            value['bot_probes'] = dict(counters['bot_probes'])
            return value

        probe = probe_module.AuthorityWorkerProbe(
            bigworld, sample, stage_seconds=1.0,
            writer=records.append, clock=clock,
            context={'process_id': 42, 'run_id': 'run-42'})
        self.assertTrue(probe.start())
        self.assertTrue(bigworld.draw)
        self._advance(probe, clock, bigworld, counters, 1.0)
        self.assertFalse(bigworld.draw)
        self._advance(probe, clock, bigworld, counters, 1.0)
        self.assertFalse(bigworld.draw)
        self._advance(probe, clock, bigworld, counters, 1.0)

        self.assertTrue(probe.finished)
        self.assertTrue(bigworld.draw)
        self.assertEqual(
            ('draw_on', 'draw_off', 'window_hidden'),
            tuple(result['stage'] for result in probe.results))
        self.assertEqual('PASS_OPERATIONAL', probe.results[0]['assessment'])
        self.assertEqual('PASS_OPERATIONAL', probe.results[1]['assessment'])
        self.assertEqual(
            'RAW_ONLY_EXTERNAL_WINDOW_EVIDENCE_REQUIRED',
            probe.results[2]['assessment'])
        self.assertLessEqual(sample_calls[0], 10)
        hidden_start = next(
            record for record in records
            if record['event'] == 'stage_start' and
            record['stage'] == 'window_hidden')
        self.assertEqual(42, hidden_start['process_id'])
        self.assertEqual('run-42', hidden_start['run_id'])
        self.assertTrue(hidden_start['external_supervisor_required'])

    def test_stop_restores_the_original_false_draw_state(self):
        clock = _Clock()
        bigworld = _BigWorld(draw=False)
        probe = probe_module.AuthorityWorkerProbe(
            bigworld, lambda: {}, stage_seconds=5.0,
            writer=lambda unused: None, clock=clock)

        self.assertTrue(probe.start())
        self.assertTrue(bigworld.draw)
        self.assertTrue(probe.stop('test'))
        self.assertFalse(bigworld.draw)

    def test_fifteen_second_stage_discards_first_five_seconds(self):
        clock = _Clock()
        bigworld = _BigWorld(draw=True)
        counters = {
            'authority_callbacks': 0,
            'bot_state_generated': 0,
            'bot_state_enqueued': 0,
            'bot_state_send_failed': 0,
            'bot_state_revision': 0,
            'bot_probes': {'ground': 0},
            'bot_count': 2,
            'simulation_caps': 0,
            'alive_bot_ticks': 0,
        }

        def sample():
            value = dict(counters)
            value['bot_probes'] = dict(counters['bot_probes'])
            return value

        probe = probe_module.AuthorityWorkerProbe(
            bigworld, sample, stage_seconds=15.0,
            writer=lambda unused: None, clock=clock)
        self.assertTrue(probe.start())
        self._advance(probe, clock, bigworld, counters, 15.0)

        result = probe.results[0]
        self.assertAlmostEqual(5.0, result['discarded_seconds'], places=4)
        self.assertAlmostEqual(10.0, result['wall_seconds'], places=4)
        self.assertEqual(300, result['authority_callback_delta'])
        self.assertEqual('PASS_OPERATIONAL', result['assessment'])

    def test_failed_draw_readback_skips_and_restores(self):
        clock = _Clock()
        bigworld = _BigWorld(draw=True)
        records = []
        probe = probe_module.AuthorityWorkerProbe(
            bigworld, lambda: {}, stage_seconds=1.0,
            writer=records.append, clock=clock)
        self.assertTrue(probe.start())
        bigworld.ignore_false = True
        clock.value = 1.0
        bigworld.now = 11.0
        probe.tick()

        self.assertTrue(bigworld.draw)
        self.assertTrue(any(
            record.get('status') == 'skipped' and
            'readback mismatch' in (record.get('reason') or '')
            for record in records if record.get('event') == 'stage_result'))

    def test_missing_draw_getter_aborts_without_mutation(self):
        class _NoGetter(object):
            def time(self):
                return 0.0

            def spaceLoadStatus(self):
                return 1.0

            def worldDrawEnabled(self, enabled):
                return enabled

        records = []
        probe = probe_module.AuthorityWorkerProbe(
            _NoGetter(), lambda: {}, writer=records.append)

        self.assertFalse(probe.start())
        self.assertTrue(probe.finished)
        self.assertEqual('draw_state_unavailable', records[-1]['reason'])


class AuthorityWorkerProbeConfigTests(unittest.TestCase):
    def test_probe_is_disabled_by_default_and_accepts_bounded_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            defaults = config_module.load(str(path))
            self.assertEqual(
                {'enabled': False, 'stageSeconds': 15.0},
                defaults['authority_worker_probe'])
            path.write_text(json.dumps({
                'authority_worker_probe': {
                    'enabled': True, 'stageSeconds': 20.0},
            }), encoding='utf-8')

            configured = config_module.load(str(path))

        self.assertEqual(
            {'enabled': True, 'stageSeconds': 20.0},
            configured['authority_worker_probe'])

    def test_invalid_probe_duration_is_quarantined(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps({
                'authority_worker_probe': {
                    'enabled': True, 'stageSeconds': 1.0},
            }), encoding='utf-8')

            configured = config_module.load(str(path))

            self.assertFalse(configured['authority_worker_probe']['enabled'])
            self.assertTrue(Path(str(path) + '.invalid').is_file())


class SupervisorRecordTests(unittest.TestCase):
    def test_hidden_request_requires_matching_pid_and_run_id(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'probe.jsonl'
            rows = [
                {'probe': 'authority_worker', 'event': 'stage_start',
                 'stage': 'window_hidden', 'process_id': 41,
                 'run_id': 'wrong', 'wall_time_epoch': 100.0},
                {'probe': 'authority_worker', 'event': 'stage_start',
                 'stage': 'window_hidden', 'process_id': 42,
                 'run_id': 'right', 'wall_time_epoch': 101.0},
            ]
            report.write_text(
                ''.join(json.dumps(row) + '\n' for row in rows),
                encoding='utf-8')

            record, offset = supervisor_module.wait_for_hidden_stage(
                str(report), 42, 100.0, 0.5)

        self.assertEqual('right', record['run_id'])
        self.assertGreater(offset, 0)

    def test_monitor_correlates_stage_result_to_run(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'probe.jsonl'
            rows = [
                {'probe': 'authority_worker', 'event': 'stage_result',
                 'stage': 'window_hidden', 'process_id': 42,
                 'run_id': 'other'},
                {'probe': 'authority_worker', 'event': 'stage_heartbeat',
                 'stage': 'window_hidden', 'process_id': 42,
                 'run_id': 'right'},
                {'probe': 'authority_worker', 'event': 'stage_result',
                 'stage': 'window_hidden', 'process_id': 42,
                 'run_id': 'right'},
            ]
            report.write_text(
                ''.join(json.dumps(row) + '\n' for row in rows),
                encoding='utf-8')

            completion, offset = supervisor_module.monitor_hidden_stage(
                str(report), 0, 42, 'right', 1.0, 0.5)

        self.assertEqual('client_stage_ended', completion)
        self.assertGreater(offset, 0)

    def test_unstable_window_handle_is_rejected_before_hide(self):
        supervisor = object.__new__(supervisor_module.WindowSupervisor)
        supervisor.find_window = mock.Mock(side_effect=[100, 101])

        with mock.patch.object(supervisor_module.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'not stable'):
                supervisor.hide()

    def test_restore_refuses_a_reused_window_handle(self):
        class _User32(object):
            @staticmethod
            def IsWindow(unused_window):
                return True

        supervisor = object.__new__(supervisor_module.WindowSupervisor)
        supervisor.process_id = 42
        supervisor.user32 = _User32()
        supervisor.window = 100
        supervisor.placement = None
        supervisor.hidden = True
        supervisor._window_process_id = lambda unused_window: 99

        with self.assertRaisesRegex(RuntimeError, 'ownership changed'):
            supervisor.restore()

    def test_monitor_timeout_still_runs_external_restore(self):
        class _Supervisor(object):
            instance = None

            def __init__(self, unused_pid):
                self.restored = False
                _Supervisor.instance = self

            def hide(self):
                return True

            def restore(self):
                self.restored = True
                return True

        args = types.SimpleNamespace(
            report='unused.jsonl', pid=42, timeout=10.0,
            duration=None, heartbeat_timeout=2.5)
        stage = {
            'run_id': 'right', 'stage_seconds': 15.0,
            'sample': {'bot_state_revision': 7},
        }
        records = []
        with mock.patch.object(supervisor_module.os, 'name', 'nt'):
            with mock.patch.object(
                    supervisor_module, 'wait_for_hidden_stage',
                    return_value=(stage, 0)):
                with mock.patch.object(
                        supervisor_module, 'WindowSupervisor', _Supervisor):
                    with mock.patch.object(
                            supervisor_module, 'monitor_hidden_stage',
                            side_effect=RuntimeError('heartbeat stalled')):
                        with mock.patch.object(
                                supervisor_module, 'append_record',
                                side_effect=lambda unused, row:
                                records.append(dict(row))):
                            self.assertEqual(2, supervisor_module.run(args))

        self.assertTrue(_Supervisor.instance.restored)
        self.assertEqual('failed', records[-1]['status'])
        self.assertTrue(records[-1]['restored'])
        self.assertEqual('right', records[-1]['run_id'])


class BattleRuntimeProbeIntegrationTests(unittest.TestCase):
    def test_probe_starts_only_for_live_authority_and_stops_on_handoff(self):
        class _Bots(object):
            authority = True

            def is_authority(self):
                return self.authority

            def probe_totals(self):
                return (0, 0, 0, 0, 0)

            def diagnostic_totals(self):
                return {'alive_bot_ticks': 0}

        class _Probe(object):
            def __init__(self):
                self.active = False
                self.finished = False
                self.stops = []
                self.ticks = 0

            def start(self):
                self.active = True
                return True

            def tick(self):
                self.ticks += 1
                return True

            def stop(self, reason):
                self.stops.append(reason)
                self.active = False
                self.finished = True
                return True

        bigworld = _BigWorld()
        runtime = types.SimpleNamespace(bigworld=bigworld)
        battle = battle_runtime_module.BattleRuntime(runtime=runtime)
        battle._config = {
            'map': 'spaces/01_karelia',
            'authority_worker_probe': {
                'enabled': True, 'stageSeconds': 15.0},
        }
        battle._start_message = {'round_id': 7}
        battle._battle_live = True
        battle._bots = _Bots()
        battle._worker_probe_bot_count = 2
        battle.client = types.SimpleNamespace(player_id=3)
        fake_probe = _Probe()

        with mock.patch.object(
                battle_runtime_module, 'AuthorityWorkerProbe',
                return_value=fake_probe) as factory:
            self.assertTrue(battle._advance_authority_worker_probe())
            self.assertEqual(1, fake_probe.ticks)
            self.assertEqual(3, factory.call_args.kwargs['context']['player_id'])
            battle._bots.authority = False
            self.assertFalse(battle._advance_authority_worker_probe())

        self.assertEqual(['authority_lost'], fake_probe.stops)

    def test_probe_waits_until_at_least_one_bot_is_present(self):
        class _Bots(object):
            def is_authority(self):
                return True

        battle = battle_runtime_module.BattleRuntime(
            runtime=types.SimpleNamespace(bigworld=_BigWorld()))
        battle._config = {
            'authority_worker_probe': {
                'enabled': True, 'stageSeconds': 15.0},
        }
        battle._battle_live = True
        battle._bots = _Bots()

        with mock.patch.object(
                battle_runtime_module, 'AuthorityWorkerProbe') as factory:
            self.assertFalse(battle._advance_authority_worker_probe())

        factory.assert_not_called()
        self.assertFalse(battle._worker_probe_attempted)

if __name__ == '__main__':
    unittest.main()
