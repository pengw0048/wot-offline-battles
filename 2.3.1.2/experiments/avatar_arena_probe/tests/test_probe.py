from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE = (
    ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods' /
    'mod_offline_2312_avatar_arena_probe.py')


def _load_probe():
    spec = importlib.util.spec_from_file_location(
        'offline_2312_avatar_arena_probe_test', MODULE)
    module = importlib.util.module_from_spec(spec)
    with redirect_stdout(io.StringIO()):
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

    def contains(self, text):
        return any(text in message for unused_level, message in self.entries)


class _ArenaType(object):
    def __init__(self, geometry='01_karelia', gameplay='ctf'):
        self.geometryName = geometry
        self.gameplayName = gameplay


class ClientArena(object):
    def __init__(self, arena_type):
        self.arenaType = arena_type


class PlayerAvatar(object):
    def __init__(self, arena_type, arena_type_id=101, space_id=41):
        self.id = 7001
        self.inWorld = True
        self.spaceID = space_id
        self.playerVehicleID = 0
        self.arenaTypeID = arena_type_id
        self.weatherPresetID = 0
        self.inputHandler = None
        self.arena = ClientArena(arena_type)


class CursorCamera(object):
    def __init__(self, space_id=41):
        self.spaceID = space_id


class _BigWorld(object):
    def __init__(self):
        self.pending = {}
        self.cancelled = []
        self.next_id = 1
        self.status = 0.0
        self.current_player = None
        self.current_camera = None

    def callback(self, delay, function):
        callback_id = self.next_id
        self.next_id += 1
        self.pending[callback_id] = (delay, function)
        return callback_id

    def cancelCallback(self, callback_id):
        self.cancelled.append(callback_id)
        self.pending.pop(callback_id, None)

    def run(self, callback_id):
        unused_delay, function = self.pending.pop(callback_id)
        function()

    def player(self):
        return self.current_player

    def camera(self):
        return self.current_camera

    def spaceLoadStatus(self):
        return self.status


class _Creator(object):
    def __init__(self, bigworld, arena_type):
        self.bigworld = bigworld
        self.arena_type = arena_type
        self.active = False
        self.created = []
        self.destroyed = 0
        self.activate_after_create = True

    def Active(self):
        return self.active

    def create(self, map_name):
        self.created.append(map_name)
        if not self.activate_after_create:
            return
        self.active = True
        self.bigworld.current_player = PlayerAvatar(self.arena_type)
        self.bigworld.current_camera = CursorCamera()

    def destroy(self):
        self.destroyed += 1
        self.active = False
        self.bigworld.current_player = None
        self.bigworld.current_camera = None


class _OfflineMode(object):
    def __init__(self):
        self.original_calls = []

        def launch(space_name):
            self.original_calls.append(space_name)

        self.launch = launch


class _Game(object):
    def __init__(self):
        self.original_calls = 0

        def fini():
            self.original_calls += 1
            return 'original-fini'

        self.fini = fini


class AvatarArenaProbeTests(unittest.TestCase):
    def setUp(self):
        self.lifecycle = _load_probe()
        self.bigworld = _BigWorld()
        self.arena_type = _ArenaType()
        self.cache = {101: self.arena_type}
        self.creator = _Creator(self.bigworld, self.arena_type)
        self.offline_mode = _OfflineMode()
        self.game = _Game()
        self.logger = _Logger()
        self.clock = [100.0]
        self.argv = [
            'WorldOfTanks.exe', 'offline', 'spaces/01_karelia',
            'avatarArenaProbe']

    def init(self, **overrides):
        kwargs = dict(
            argv=self.argv,
            bigworld=self.bigworld,
            offline_mode=self.offline_mode,
            creator=self.creator,
            arena_cache=self.cache,
            game_module=self.game,
            logger=self.logger,
            now=lambda: self.clock[0],
            get_client_version=lambda: 'v.2.3.1.2 #919')
        kwargs.update(overrides)
        return self.lifecycle.init(**kwargs)

    def test_parse_request_requires_explicit_token_and_stock_offline_shape(self):
        parse = self.lifecycle.parse_request
        self.assertEqual(
            ('spaces/01_karelia', '01_karelia'), parse(self.argv))
        self.assertIsNone(parse(self.argv[:-1]))
        self.assertIsNone(parse(['x', 'avatarArenaProbe']))
        self.assertIsNone(parse(
            ['x', 'offline', '01_karelia', 'avatarArenaProbe']))
        self.assertIsNone(parse(
            ['x', 'offline', 'spaces/a/b', 'avatarArenaProbe']))

    def test_find_arena_type_prefers_the_ctf_variant(self):
        cache = {
            3: _ArenaType(gameplay='assault'),
            5: _ArenaType(gameplay='ctf'),
            4: _ArenaType(gameplay='ctf'),
        }
        match = self.lifecycle._find_arena_type(cache, '01_karelia')
        self.assertEqual(4, match[0])
        self.assertEqual('ctf', match[1].gameplayName)

    def test_route_uses_stock_creator_and_passes_player_arena_gate(self):
        original_launch = self.offline_mode.launch
        original_fini = self.game.fini
        probe = self.init()
        self.assertIsNotNone(probe)
        self.assertIsNot(original_launch, self.offline_mode.launch)
        self.assertIsNot(original_fini, self.game.fini)

        self.offline_mode.launch('spaces/01_karelia')
        self.assertEqual(1, len(self.bigworld.pending))
        callback_id = probe.callback_id
        self.bigworld.status = 1.0
        self.bigworld.run(callback_id)

        self.assertTrue(probe.completed)
        self.assertFalse(probe.failed)
        self.assertEqual(['01_karelia'], self.creator.created)
        self.assertEqual([], self.offline_mode.original_calls)
        self.assertTrue(self.logger.contains('avatar_seen'))
        self.assertTrue(self.logger.contains('client_arena_seen'))
        self.assertTrue(self.logger.contains('space_loaded'))
        self.assertTrue(self.logger.contains('gate_pass gate=player_arena'))

    def test_missing_arena_cache_fails_closed_without_native_create(self):
        probe = self.init(arena_cache={})
        self.offline_mode.launch('spaces/01_karelia')
        self.bigworld.run(probe.callback_id)
        self.assertTrue(probe.failed)
        self.assertEqual([], self.creator.created)
        self.assertEqual([], self.offline_mode.original_calls)
        self.assertTrue(self.logger.contains('reason=arena_cache_empty'))

    def test_player_lookup_failure_never_attempts_native_create(self):
        def fail_player_lookup():
            raise RuntimeError('entity manager unavailable')

        self.bigworld.player = fail_player_lookup
        probe = self.init()
        self.offline_mode.launch('spaces/01_karelia')
        self.bigworld.run(probe.callback_id)
        self.assertTrue(probe.failed)
        self.assertEqual([], self.creator.created)
        self.assertTrue(self.logger.contains('player_lookup_failed'))
        self.assertTrue(self.logger.contains('reason=native_exception'))

    def test_creator_failure_never_falls_back_to_stock_free_camera(self):
        self.creator.activate_after_create = False
        probe = self.init()
        self.offline_mode.launch('spaces/01_karelia')
        self.bigworld.run(probe.callback_id)
        self.assertTrue(probe.failed)
        self.assertEqual(['01_karelia'], self.creator.created)
        self.assertEqual([], self.offline_mode.original_calls)
        self.assertTrue(
            self.logger.contains('reason=creator_inactive_after_create'))

    def test_unexpected_native_property_error_ends_with_gate_fail(self):
        class PlayerAvatar(object):
            id = 88
            inWorld = True
            spaceID = 41
            playerVehicleID = 0
            arenaTypeID = 101
            weatherPresetID = 0
            inputHandler = None

            @property
            def arena(self):
                raise RuntimeError('arena stream unavailable')

        def create(unused_map_name):
            self.creator.active = True
            self.bigworld.current_player = PlayerAvatar()
            self.bigworld.current_camera = CursorCamera()

        self.creator.create = create
        probe = self.init()
        self.offline_mode.launch('spaces/01_karelia')
        self.bigworld.run(probe.callback_id)
        self.assertTrue(probe.failed)
        self.assertFalse(self.bigworld.pending)
        self.assertTrue(self.logger.contains('reason=native_exception'))
        self.assertTrue(self.logger.contains('stage=create'))
        self.assertTrue(self.logger.contains('arena stream unavailable'))

    def test_timeout_reports_the_exact_runtime_snapshot(self):
        probe = self.init(load_timeout=1.0)
        self.offline_mode.launch('spaces/01_karelia')
        self.bigworld.run(probe.callback_id)
        self.bigworld.status = 1.0
        self.bigworld.current_camera = CursorCamera(space_id=99)
        self.clock[0] += 2.0
        self.bigworld.run(probe.callback_id)

        self.assertTrue(probe.failed)
        self.assertTrue(self.logger.contains('reason=space_id_mismatch'))
        self.assertTrue(self.logger.contains('status=1.000000'))
        self.assertTrue(self.logger.contains('creator_active=True'))
        self.assertTrue(self.logger.contains('player_type=PlayerAvatar'))
        self.assertTrue(self.logger.contains('arena_type=ClientArena'))
        self.assertTrue(self.logger.contains('camera_type=CursorCamera'))
        self.assertTrue(self.logger.contains('camera_space_id=99'))

    def test_version_mismatch_does_not_install_routes(self):
        original_launch = self.offline_mode.launch
        original_fini = self.game.fini
        with redirect_stdout(io.StringIO()) as output:
            probe = self.init(get_client_version=lambda: 'wrong')
        self.assertIsNone(probe)
        self.assertIs(original_launch, self.offline_mode.launch)
        self.assertIs(original_fini, self.game.fini)
        self.assertIn('version_mismatch', output.getvalue())

    def test_game_fini_cleans_creator_before_original_shutdown(self):
        probe = self.init()
        self.offline_mode.launch('spaces/01_karelia')
        self.bigworld.status = 1.0
        self.bigworld.run(probe.callback_id)
        routed_fini = self.game.fini

        with redirect_stdout(io.StringIO()) as output:
            result = routed_fini()
        self.assertEqual('original-fini', result)
        self.assertEqual(1, self.creator.destroyed)
        self.assertEqual(1, self.game.original_calls)
        self.assertFalse(self.creator.Active())
        self.assertIs(self.lifecycle._original_launch, self.offline_mode.launch)
        self.assertIs(self.lifecycle._original_game_fini, self.game.fini)
        self.assertIn('cleanup_destroy_begin creator_active=True',
                      output.getvalue())
        self.assertIn('cleanup_destroy_returned creator_active=False',
                      output.getvalue())
        self.assertIn('cleanup_original_fini_returned', output.getvalue())

    def test_cleanup_failure_still_calls_original_game_fini(self):
        probe = self.init()
        self.offline_mode.launch('spaces/01_karelia')
        self.bigworld.status = 1.0
        self.bigworld.run(probe.callback_id)

        def fail_destroy():
            raise RuntimeError('destroy failed')

        self.creator.destroy = fail_destroy
        routed_fini = self.game.fini
        with redirect_stdout(io.StringIO()) as output:
            result = routed_fini()
        self.assertEqual('original-fini', result)
        self.assertEqual(1, self.game.original_calls)
        self.assertIn('cleanup_failed stage=offline_creator_destroy',
                      output.getvalue())

    def test_fini_cancels_callback_and_restores_routes_without_destroy(self):
        probe = self.init()
        self.offline_mode.launch('spaces/01_karelia')
        callback_id = probe.callback_id
        original_launch = self.lifecycle._original_launch
        original_game_fini = self.lifecycle._original_game_fini
        with redirect_stdout(io.StringIO()):
            self.lifecycle.fini()
        self.assertEqual([callback_id], self.bigworld.cancelled)
        self.assertIs(original_launch, self.offline_mode.launch)
        self.assertIs(original_game_fini, self.game.fini)
        self.assertEqual(0, self.creator.destroyed)


if __name__ == '__main__':
    unittest.main()
