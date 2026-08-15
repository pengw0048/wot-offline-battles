import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = (
    ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods' /
    'offline_2311_poc' / 'lifecycle.py')


def _load_lifecycle():
    spec = importlib.util.spec_from_file_location(
        'offline_2311_poc_lifecycle_test', LIFECYCLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Logger(object):
    def __init__(self):
        self.entries = []

    def _add(self, level, message, args):
        self.entries.append((level, message % args if args else message))

    def info(self, message, *args):
        self._add('info', message, args)

    def error(self, message, *args):
        self._add('error', message, args)

    def exception(self, message, *args):
        self._add('exception', message, args)

    def contains(self, text):
        return any(text in message for unused_level, message in self.entries)


class _BigWorld(object):
    def __init__(self):
        self.callbacks = {}
        self.cancelled = []
        self.load_status = 0.0
        self.current_player = None
        self.current_camera = None
        self.spaces = {}
        self._next_id = 1

    def callback(self, delay, function):
        callback_id = self._next_id
        self._next_id += 1
        self.callbacks[callback_id] = (delay, function)
        return callback_id

    def cancelCallback(self, callback_id):
        self.cancelled.append(callback_id)
        self.callbacks.pop(callback_id, None)

    def spaceLoadStatus(self):
        return self.load_status

    def player(self):
        return self.current_player

    def camera(self):
        return self.current_camera

    def run(self, callback_id):
        unused_delay, function = self.callbacks.pop(callback_id)
        function()


class _OfflineMode(object):
    def __init__(self):
        self.is_enabled = False
        self.is_loaded = False

    def enabled(self):
        return self.is_enabled

    def isSpaceLoaded(self):
        return self.is_loaded


class OfflineEntity(object):
    def __init__(self, space_id):
        self.spaceID = space_id


class FreeCamera(object):
    pass


class OfflineProbeTests(unittest.TestCase):
    def setUp(self):
        self.lifecycle = _load_lifecycle()
        self.bigworld = _BigWorld()
        self.offline_mode = _OfflineMode()
        self.logger = _Logger()
        self.clock = [100.0]

    def make_probe(self, timeout=120.0):
        return self.lifecycle.OfflineModeProbe(
            self.bigworld, self.offline_mode, 'spaces/01_karelia',
            logger=self.logger, now=lambda: self.clock[0], timeout=timeout)

    def test_command_line_selection_matches_stock_offline_mode(self):
        parse = self.lifecycle.parse_offline_request
        self.assertEqual(
            'spaces/01_karelia',
            parse(['WorldOfTanks.exe', 'offline', 'spaces/01_karelia']))
        self.assertEqual(
            'spaces/02_malinovka',
            parse(['WorldOfTanks.exe', 'x', 'offline',
                   'spaces/02_malinovka', 'offline', 'ignored']))
        self.assertIsNone(parse(['WorldOfTanks.exe']))
        self.assertIsNone(parse(['WorldOfTanks.exe', 'offline']))

    def test_probe_observes_official_state_transition_and_stops(self):
        probe = self.make_probe()
        probe.start()
        first_id = probe.callback_id
        self.assertIsNotNone(first_id)
        self.assertTrue(self.logger.contains('probe_start'))

        self.offline_mode.is_enabled = True
        self.bigworld.load_status = 0.4
        self.bigworld.run(first_id)
        second_id = probe.callback_id
        self.assertTrue(self.logger.contains('offline_mode_entered'))

        self.offline_mode.is_loaded = True
        self.bigworld.load_status = 1.0
        self.bigworld.current_player = OfflineEntity(17)
        self.bigworld.current_camera = FreeCamera()
        self.bigworld.spaces = {17: object()}
        self.bigworld.run(second_id)
        self.assertTrue(probe.completed)
        self.assertIsNone(probe.callback_id)
        self.assertFalse(self.bigworld.callbacks)
        self.assertTrue(self.logger.contains('space_loaded'))
        self.assertTrue(self.logger.contains('space_id=17'))
        self.assertTrue(self.logger.contains('spaces=[17]'))

    def test_probe_times_out_without_creating_replacement_state(self):
        probe = self.make_probe(timeout=5.0)
        probe.start()
        callback_id = probe.callback_id
        self.clock[0] = 105.0
        self.bigworld.run(callback_id)
        self.assertFalse(probe.completed)
        self.assertIsNone(probe.callback_id)
        self.assertFalse(self.bigworld.callbacks)
        self.assertTrue(self.logger.contains('probe_timeout'))

    def test_stop_cancels_only_the_probe_callback(self):
        probe = self.make_probe()
        probe.start()
        callback_id = probe.callback_id
        unused_delay, stale_function = self.bigworld.callbacks[callback_id]
        probe.stop()
        self.assertEqual([callback_id], self.bigworld.cancelled)
        self.assertFalse(self.bigworld.callbacks)
        self.assertTrue(self.logger.contains('probe_stop'))
        stale_function()
        self.assertFalse(self.bigworld.callbacks)

    def test_a_transient_getter_error_does_not_abort_sampling(self):
        original = self.bigworld.spaceLoadStatus
        calls = [0]

        def fail_once():
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError('not ready')
            return original()

        self.bigworld.spaceLoadStatus = fail_once
        probe = self.make_probe()
        probe.start()
        callback_id = probe.callback_id
        self.assertTrue(self.logger.contains('space_load_status_failed'))

        self.offline_mode.is_enabled = True
        self.offline_mode.is_loaded = True
        self.bigworld.load_status = 1.0
        self.bigworld.current_player = OfflineEntity(5)
        self.bigworld.current_camera = FreeCamera()
        self.bigworld.spaces = {5: object()}
        self.bigworld.run(callback_id)
        self.assertTrue(probe.completed)
        self.assertTrue(self.logger.contains('space_loaded'))

    def test_incomplete_loaded_snapshot_is_not_reported_as_success(self):
        probe = self.make_probe(timeout=5.0)
        probe.start()
        first_id = probe.callback_id
        self.offline_mode.is_enabled = True
        self.offline_mode.is_loaded = True
        self.bigworld.load_status = 1.0
        self.bigworld.run(first_id)
        second_id = probe.callback_id
        self.assertFalse(probe.completed)
        self.assertIsNotNone(second_id)
        self.assertTrue(
            self.logger.contains('space_loaded_snapshot_incomplete'))
        self.assertFalse(any(
            message.startswith('[OFFLINE_2311_POC] space_loaded ')
            for unused_level, message in self.logger.entries))

        self.bigworld.current_player = OfflineEntity(9)
        self.bigworld.current_camera = FreeCamera()
        self.bigworld.spaces = {9: object()}
        self.bigworld.run(second_id)
        self.assertTrue(probe.completed)
        self.assertTrue(any(
            message.startswith('[OFFLINE_2311_POC] space_loaded ')
            for unused_level, message in self.logger.entries))

    def test_lifecycle_is_inactive_without_the_exact_offline_token(self):
        result = self.lifecycle.init(
            argv=['WorldOfTanks.exe', 'spaces/01_karelia'],
            bigworld=self.bigworld, offline_mode=self.offline_mode,
            logger=self.logger, now=lambda: self.clock[0])
        self.assertIsNone(result)
        self.assertFalse(self.bigworld.callbacks)

    def test_lifecycle_fails_closed_on_a_different_client_version(self):
        result = self.lifecycle.init(
            argv=['WorldOfTanks.exe', 'offline', 'spaces/01_karelia'],
            bigworld=self.bigworld, offline_mode=self.offline_mode,
            logger=self.logger, now=lambda: self.clock[0],
            get_client_version=lambda: 'v.2.3.1.0 #900')
        self.assertIsNone(result)
        self.assertFalse(self.bigworld.callbacks)
        self.assertTrue(self.logger.contains('version_mismatch'))

    def test_exact_version_starts_and_online_lifecycle_is_only_reported(self):
        self.lifecycle._logger = self.logger
        probe = self.lifecycle.init(
            argv=['WorldOfTanks.exe', 'offline', 'spaces/01_karelia'],
            bigworld=self.bigworld, offline_mode=self.offline_mode,
            logger=self.logger, now=lambda: self.clock[0],
            get_client_version=lambda: ' v.2.3.1.1 #916 ')
        self.assertIsNotNone(probe)
        callback_count = len(self.bigworld.callbacks)
        self.lifecycle.record_online_lifecycle(
            'onConnected', args=(object(),), kwargs={'unexpected': True})
        self.assertEqual(callback_count, len(self.bigworld.callbacks))
        self.assertTrue(self.logger.contains('unexpected_online_lifecycle'))
        self.lifecycle.fini()


if __name__ == '__main__':
    unittest.main()
