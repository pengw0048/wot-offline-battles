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
        self.teamSpawnPoints = ([], [])
        self.teamBasePositions = (
            {0: _Vector2(397.524078, 402.612030)},
            {0: _Vector2(-401.340332, -399.975006)})
        self.boundingBox = (
            _Vector2(-500.0, -500.0), _Vector2(500.0, 500.0))


class _Vector2(object):
    def __init__(self, x, y):
        self.x = x
        self.y = y


class _Vector3(object):
    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z


class _Matrix(object):
    def __init__(self):
        self.translation = None
        self.rotation = None

    def setTranslate(self, value):
        self.translation = value

    def setRotateYPR(self, value):
        self.rotation = value


class _Math(object):
    Vector3 = _Vector3
    Matrix = _Matrix


class _Collision(object):
    def __init__(self, point):
        self.closestPoint = point


class ClientArena(object):
    def __init__(self, arena_type):
        self.arenaType = arena_type


class PlayerAvatar(object):
    def __init__(self, arena_type, arena_type_id=101, space_id=41,
                 init_progress=2):
        self.preseed_at_init = {
            'arenaUniqueID': self.arenaUniqueID,
            'arenaTypeID': self.arenaTypeID,
            'arenaBonusType': self.arenaBonusType,
            'arenaGuiType': self.arenaGuiType,
            'arenaExtraData': self.arenaExtraData,
            'weatherPresetID': self.weatherPresetID,
            'bonusCapsOverrides': self.bonusCapsOverrides,
        }
        self.id = 7001
        self.inWorld = True
        self.spaceID = space_id
        self.playerVehicleID = 0
        self.arenaTypeID = arena_type_id
        self.weatherPresetID = 0
        self.inputHandler = None
        self.arena = ClientArena(arena_type)
        self._PlayerAvatar__initProgress = init_progress

    def hasBonusCap(self, unused_cap):
        return (self.arenaBonusType is not None and
                self.bonusCapsOverrides is None)

    def onEnterWorld(self, *unused_args):
        return None

    def onBecomePlayer(self):
        return None


class CursorCamera(object):
    def __init__(self, space_id=41):
        self.spaceID = space_id
        self.target = None
        self.source = None
        self.force_updates = 0

    def forceUpdate(self):
        self.force_updates += 1


class _BigWorld(object):
    def __init__(self):
        self.pending = {}
        self.cancelled = []
        self.next_id = 1
        self.status = 0.0
        self.current_player = None
        self.current_camera = None
        self.collision_point = _Vector3(382.0, 56.3056, 386.0)
        self.collision_calls = []

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

    def wg_collideSegment(self, space_id, start, end, mask, only_flags):
        self.collision_calls.append(
            (space_id, start, end, mask, only_flags))
        if self.collision_point is None:
            return None
        return _Collision(self.collision_point)

    def getWindowMode(self):
        return 2

    def wg_getCurrentResolution(self, window_mode):
        if window_mode != 2:
            raise AssertionError('wrong window mode')
        return (2560, 1440)

    def videoModeIndex(self):
        return 7

    def getActiveMonitorIndex(self, window_mode):
        if window_mode != 2:
            raise AssertionError('wrong window mode')
        return 1

    def getBorderlessParameters(self):
        return ('FULLSCREEN', 2560, 1440)

    def isVideoVSync(self):
        return True

    def isTripleBuffered(self):
        return False

    def getDRRScale(self):
        return 1.0

    def isDRRAutoscalingEnabled(self):
        return False

    def getGammaCorrection(self):
        return 1.0


class _Creator(object):
    def __init__(self, bigworld, arena_type, avatar_type=PlayerAvatar):
        self.bigworld = bigworld
        self.arena_type = arena_type
        self.avatar_type = avatar_type
        self.active = False
        self.created = []
        self.destroyed = 0
        self.activate_after_create = True
        self.init_progress = 2
        self.swallow_component_errors = False
        self.swallow_lifecycle_errors = False

    def Active(self):
        return self.active

    def create(self, map_name):
        self.created.append(map_name)
        if not self.activate_after_create:
            return
        self.active = True
        avatar = self.avatar_type(
            self.arena_type, init_progress=self.init_progress)
        self.bigworld.current_player = avatar
        self.bigworld.current_camera = CursorCamera()
        try:
            avatar.hasBonusCap('component-init')
        except Exception:
            if not self.swallow_component_errors:
                raise
        for method in (avatar.onEnterWorld, avatar.onBecomePlayer):
            try:
                method()
            except Exception:
                if not self.swallow_lifecycle_errors:
                    raise

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
        self.engine_math = _Math()
        self.avatar_type = PlayerAvatar
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
            engine_math=self.engine_math,
            offline_mode=self.offline_mode,
            creator=self.creator,
            arena_cache=self.cache,
            game_module=self.game,
            avatar_type=self.avatar_type,
            arena_bonus_unknown=17,
            arena_gui_unknown=23,
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

    def test_camera_pose_copies_mature_karelia_spawn_and_base_heading(self):
        x, z, yaw, source = self.lifecycle._camera_spawn_pose(
            self.arena_type)
        self.assertEqual((382.0, 386.0), (x, z))
        self.assertAlmostEqual(-2.358519018, yaw, places=7)
        self.assertEqual('mature_ctf_spawn', source)

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
        self.assertTrue(self.logger.contains(
            'geometry_loaded status=1.000000'))
        self.assertTrue(self.logger.contains(
            'camera_repositioned source=mature_ctf_spawn'))
        self.assertTrue(self.logger.contains(
            'space_lifecycle_missing '
            'reason=offline_battle_session_not_started'))
        self.assertTrue(self.logger.contains('display_state window_mode=2'))
        self.assertTrue(self.logger.contains(
            'avatar_init_observed preseed_applied=True init_returned=True'))
        self.assertTrue(self.logger.contains('bootstrap_ready'))
        self.assertTrue(self.logger.contains('player_space_loaded=False'))
        self.assertEqual({
            'arenaUniqueID': 0,
            'arenaTypeID': 101,
            'arenaBonusType': 17,
            'arenaGuiType': 23,
            'arenaExtraData': {},
            'weatherPresetID': 0,
            'bonusCapsOverrides': None,
        }, self.bigworld.current_player.preseed_at_init)
        camera = self.bigworld.current_camera
        self.assertAlmostEqual(382.0, camera.target.translation.x)
        self.assertAlmostEqual(56.3056, camera.target.translation.y)
        self.assertAlmostEqual(386.0, camera.target.translation.z)
        self.assertIsNotNone(camera.source.rotation)
        self.assertEqual(1, camera.force_updates)
        self.assertEqual(128, self.bigworld.collision_calls[0][3])
        self.assertEqual(8, self.bigworld.collision_calls[0][4])
        ray_start = self.bigworld.collision_calls[0][1]
        ray_end = self.bigworld.collision_calls[0][2]
        self.assertEqual((382.0, 386.0), (ray_start.x, ray_start.z))
        self.assertEqual((382.0, 386.0), (ray_end.x, ray_end.z))

    def test_camera_waits_for_terrain_collision_before_completion(self):
        probe = self.init()
        self.offline_mode.launch('spaces/01_karelia')
        self.bigworld.status = 1.0
        self.bigworld.collision_point = None
        self.bigworld.run(probe.callback_id)
        self.assertFalse(probe.completed)
        self.assertFalse(probe.failed)
        self.assertEqual(1, len(self.bigworld.pending))

        self.bigworld.collision_point = _Vector3(382.0, 56.3056, 386.0)
        self.bigworld.run(probe.callback_id)
        self.assertTrue(probe.completed)
        self.assertTrue(self.logger.contains('camera_repositioned'))

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
        original_init = PlayerAvatar.__dict__['__init__']
        original_has_bonus_cap = PlayerAvatar.__dict__['hasBonusCap']
        original_on_enter_world = PlayerAvatar.__dict__['onEnterWorld']
        original_on_become_player = PlayerAvatar.__dict__['onBecomePlayer']
        self.creator.activate_after_create = False
        probe = self.init()
        self.offline_mode.launch('spaces/01_karelia')
        self.bigworld.run(probe.callback_id)
        self.assertTrue(probe.failed)
        self.assertEqual(['01_karelia'], self.creator.created)
        self.assertEqual([], self.offline_mode.original_calls)
        self.assertTrue(
            self.logger.contains('reason=creator_inactive_after_create'))
        self.assertIs(original_init, PlayerAvatar.__dict__['__init__'])
        self.assertIs(
            original_has_bonus_cap, PlayerAvatar.__dict__['hasBonusCap'])
        self.assertIs(
            original_on_enter_world, PlayerAvatar.__dict__['onEnterWorld'])
        self.assertIs(
            original_on_become_player,
            PlayerAvatar.__dict__['onBecomePlayer'])

    def test_native_create_exception_restores_avatar_routes(self):
        original_init = PlayerAvatar.__dict__['__init__']
        original_has_bonus_cap = PlayerAvatar.__dict__['hasBonusCap']
        original_on_enter_world = PlayerAvatar.__dict__['onEnterWorld']
        original_on_become_player = PlayerAvatar.__dict__['onBecomePlayer']

        def fail_create(unused_map_name):
            raise RuntimeError('native create failed')

        self.creator.create = fail_create
        probe = self.init()
        self.offline_mode.launch('spaces/01_karelia')
        self.bigworld.run(probe.callback_id)

        self.assertTrue(probe.failed)
        self.assertTrue(self.logger.contains('reason=native_exception'))
        self.assertIs(original_init, PlayerAvatar.__dict__['__init__'])
        self.assertIs(
            original_has_bonus_cap, PlayerAvatar.__dict__['hasBonusCap'])
        self.assertIs(
            original_on_enter_world, PlayerAvatar.__dict__['onEnterWorld'])
        self.assertIs(
            original_on_become_player,
            PlayerAvatar.__dict__['onBecomePlayer'])

    def test_unexpected_native_property_error_ends_with_gate_fail(self):
        base_avatar_type = self.avatar_type

        class BrokenPlayerAvatar(base_avatar_type):
            def __init__(self, *args, **kwargs):
                base_avatar_type.__init__(self, *args, **kwargs)

            def hasBonusCap(self, cap):
                return base_avatar_type.hasBonusCap(self, cap)

            def onEnterWorld(self, *args):
                return base_avatar_type.onEnterWorld(self, *args)

            def onBecomePlayer(self):
                return base_avatar_type.onBecomePlayer(self)

            @property
            def arena(self):
                raise RuntimeError('arena stream unavailable')

            @arena.setter
            def arena(self, value):
                self._arena = value

        self.creator.avatar_type = BrokenPlayerAvatar
        probe = self.init(avatar_type=BrokenPlayerAvatar)
        self.offline_mode.launch('spaces/01_karelia')
        self.bigworld.run(probe.callback_id)
        self.assertTrue(probe.failed)
        self.assertFalse(self.bigworld.pending)
        self.assertTrue(self.logger.contains('reason=native_exception'))
        self.assertTrue(self.logger.contains('stage=poll'))
        self.assertTrue(self.logger.contains('arena stream unavailable'))

    def test_native_component_error_cannot_false_pass(self):
        base_avatar_type = self.avatar_type

        class BrokenPlayerAvatar(base_avatar_type):
            def __init__(self, *args, **kwargs):
                base_avatar_type.__init__(self, *args, **kwargs)

            def hasBonusCap(self, unused_cap):
                raise AttributeError('component property missing')

            def onEnterWorld(self, *args):
                return base_avatar_type.onEnterWorld(self, *args)

            def onBecomePlayer(self):
                return base_avatar_type.onBecomePlayer(self)

        self.creator.avatar_type = BrokenPlayerAvatar
        self.creator.swallow_component_errors = True
        probe = self.init(avatar_type=BrokenPlayerAvatar)
        self.offline_mode.launch('spaces/01_karelia')
        self.bigworld.run(probe.callback_id)

        self.assertTrue(probe.failed)
        self.assertFalse(probe.completed)
        self.assertTrue(self.logger.contains('has_bonus_cap_exceptions=1'))
        self.assertTrue(self.logger.contains('reason=bonus_cap_check_failed'))

    def test_late_lifecycle_error_cannot_false_pass(self):
        base_avatar_type = self.avatar_type

        class BrokenPlayerAvatar(base_avatar_type):
            def __init__(self, *args, **kwargs):
                base_avatar_type.__init__(self, *args, **kwargs)

            def hasBonusCap(self, cap):
                return base_avatar_type.hasBonusCap(self, cap)

            def onEnterWorld(self, *args):
                return base_avatar_type.onEnterWorld(self, *args)

            def onBecomePlayer(self):
                raise RuntimeError('late player lifecycle failed')

        self.creator.avatar_type = BrokenPlayerAvatar
        self.creator.swallow_lifecycle_errors = True
        probe = self.init(avatar_type=BrokenPlayerAvatar)
        self.offline_mode.launch('spaces/01_karelia')
        self.bigworld.run(probe.callback_id)

        self.assertTrue(probe.failed)
        self.assertFalse(probe.completed)
        self.assertTrue(self.logger.contains('become_player_exceptions=1'))
        self.assertTrue(self.logger.contains('reason=become_player_failed'))

    def test_missing_enter_world_progress_times_out(self):
        self.creator.init_progress = 0
        probe = self.init(load_timeout=1.0)
        self.offline_mode.launch('spaces/01_karelia')
        self.bigworld.run(probe.callback_id)
        self.bigworld.status = 1.0
        self.clock[0] += 2.0
        self.bigworld.run(probe.callback_id)

        self.assertTrue(probe.failed)
        self.assertFalse(probe.completed)
        self.assertTrue(self.logger.contains(
            'reason=avatar_enter_world_timeout'))
        self.assertTrue(self.logger.contains('init_progress=0'))

    def test_avatar_routes_are_restored_after_synchronous_create(self):
        original_init = PlayerAvatar.__dict__['__init__']
        original_has_bonus_cap = PlayerAvatar.__dict__['hasBonusCap']
        original_on_enter_world = PlayerAvatar.__dict__['onEnterWorld']
        original_on_become_player = PlayerAvatar.__dict__['onBecomePlayer']
        probe = self.init()
        self.offline_mode.launch('spaces/01_karelia')
        self.bigworld.run(probe.callback_id)

        self.assertIs(original_init, PlayerAvatar.__dict__['__init__'])
        self.assertIs(
            original_has_bonus_cap, PlayerAvatar.__dict__['hasBonusCap'])
        self.assertIs(
            original_on_enter_world, PlayerAvatar.__dict__['onEnterWorld'])
        self.assertIs(
            original_on_become_player,
            PlayerAvatar.__dict__['onBecomePlayer'])

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
