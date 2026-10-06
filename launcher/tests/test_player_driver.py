"""Player-driver startup isolation and lifecycle contracts; no real products."""
import os
import tempfile
import unittest
from unittest import mock

import core
import error_reports
import wot_launcher
import test_launcher_window as fixtures


class PlayerDriverCoreTests(unittest.TestCase):
    def test_driver_endpoint_is_loopback_and_per_session_secret(self):
        listener = mock.MagicMock()
        listener.getsockname.return_value = ('127.0.0.1', 12345)
        listener.__enter__.return_value = listener
        with mock.patch('core.socket.socket', return_value=listener), \
                mock.patch('secrets.token_urlsafe', return_value='session-token'):
            self.assertEqual((12345, 'session-token'), core.player_driver_endpoint())
        listener.bind.assert_called_once_with(('127.0.0.1', 0))
        listener.__exit__.assert_called_once()

    def test_private_endpoint_is_not_inherited_by_worker_or_old_visible_session(self):
        source = {core.DRIVER_PORT_ENV_0922: '12345', core.DRIVER_TOKEN_ENV_0922: 'secret',
                  core.WORKER_READY_MARKER_ENV_0922: 'old',
                  core.CLIENT_SERVER_HOST_ENV_0922: 'room.example',
                  core.CLIENT_SERVER_PORT_ENV_0922: '28782'}
        driver = core.player_driver_environment('/game', 45678, 'fresh', source)
        self.assertEqual('player_driver', driver[core.CLIENT_MODE_ENV_0922])
        self.assertEqual('1', driver[core.ALLOW_MULTIPLE_CLIENTS_ENV_0922])
        self.assertEqual('1', driver[core.HIDDEN_DESKTOP_ENV_0922])
        self.assertEqual('fresh', driver[core.DRIVER_TOKEN_ENV_0922])
        for name in (core.CLIENT_SERVER_HOST_ENV_0922, core.CLIENT_SERVER_PORT_ENV_0922,
                     core.WORKER_READY_MARKER_ENV_0922):
            self.assertNotIn(name, driver)
        worker = core.worker_environment('/game', environment=source)
        plain = core.visible_client_environment(core.PORT_0_9_22, environment=source)
        for row in (worker, plain):
            self.assertNotIn(core.DRIVER_PORT_ENV_0922, row)
            self.assertNotIn(core.DRIVER_TOKEN_ENV_0922, row)
        paired = core.visible_client_environment(core.PORT_0_9_22, environment=source,
                    paired_worker=True, driver_port=45678, driver_token='fresh')
        self.assertEqual('45678', paired[core.DRIVER_PORT_ENV_0922])
        self.assertEqual('fresh', paired[core.DRIVER_TOKEN_ENV_0922])
        self.assertEqual('room.example', source[core.CLIENT_SERVER_HOST_ENV_0922])

    def test_driver_readiness_does_not_accept_worker_or_unchanged_driver_marker(self):
        process = mock.Mock()
        process.poll.return_value = None
        clock = mock.Mock(side_effect=[0.0, 0.0, 1.0])
        with mock.patch('core.driver_ready_marker_token', return_value=('old',)), \
                mock.patch('core.worker_ready_marker_token') as worker:
            self.assertFalse(core.wait_for_driver_ready(process, '/game', timeout=1,
                previous_marker_token=('old',), clock=clock, sleep=lambda unused: None))
        worker.assert_not_called()

    def test_only_room_worker_remains_a_required_visible_dependency(self):
        game = mock.Mock();game.poll.side_effect = [None, 0]
        room_worker = mock.Mock();room_worker.poll.return_value = None
        self.assertEqual((0, False), core.wait_for_paired_player_exit(
            game, '/game', required_process=room_worker, sleep=lambda unused: None))
        game.terminate.assert_not_called()
        room_worker.terminate.assert_not_called()

    def test_driver_engine_config_is_required_and_owned(self):
        member = 'res_mods/0.9.22.0.1/engine_config.offline-driver.xml'
        layout = core._CLIENT_INSTALL[core.PORT_0_9_22]
        self.assertIn(member, layout['required']);self.assertIn(member, layout['owned_files'])

    def test_parallel_readiness_waits_for_both_in_either_order(self):
        for order in ((0, 1), (1, 0)):
            with self.subTest(order=order):
                markers = [None, None]
                published = []
                clients = tuple((fixtures._Process(),
                                 lambda unused, i=i: markers[i], None)
                                for i in range(2))
                def advance(unused):
                    index = order[len(published)]
                    markers[index] = ('new', index)
                    published.append(index)
                self.assertEqual((True, None), core.wait_for_hidden_clients_ready(
                    clients, '/game', clock=lambda: 0.0, sleep=advance))
                self.assertEqual(list(order), published)

    def test_parallel_readiness_keeps_stale_marker_history_per_role(self):
        markers = [('old-worker',), ('old-driver',)]
        clients = tuple((fixtures._Process(),
                         lambda unused, i=i: markers[i], markers[i])
                        for i in range(2))
        transitions = iter(((None, ('old-driver',)),
                            (('old-worker',), ('old-driver',)),
                            (('old-worker',), None),
                            (('old-worker',), ('old-driver',))))
        polls = []
        def advance(unused):
            markers[:] = next(transitions)
            polls.append(tuple(markers))
        self.assertEqual((True, None), core.wait_for_hidden_clients_ready(
            clients, '/game', clock=lambda: 0.0, sleep=advance))
        self.assertEqual(4, len(polls))

    def test_parallel_readiness_rechecks_first_child_after_second_marker(self):
        worker, driver = fixtures._Process(), fixtures._Process()
        def driver_marker(unused):
            worker.exit_code = 7
            return ('new-driver',)
        self.assertEqual((False, 0), core.wait_for_hidden_clients_ready((
            (worker, lambda unused: ('new-worker',), None),
            (driver, driver_marker, None)), '/game'))

    def test_parallel_readiness_reports_unready_role_at_shared_deadline(self):
        self.assertEqual((False, 1), core.wait_for_hidden_clients_ready((
            (fixtures._Process(), lambda unused: ('new-worker',), None),
            (fixtures._Process(), lambda unused: ('old-driver',), ('old-driver',))),
            '/game', timeout=1.0, clock=mock.Mock(side_effect=[0.0, 1.0]),
            sleep=mock.Mock(side_effect=AssertionError('must not exceed deadline'))))


class PlayerDriverWindowTests(unittest.TestCase):
    def setUp(self):
        fixtures.WindowTest.setUp(self)

    def test_driver_start_and_visible_receive_the_same_private_endpoint(self):
        with open(core.worker_starter_executable(self.settings_dir), 'w') as f:
            f.write('fake starter')
        driver = fixtures._Process(exit_code=None, pid=42)
        with mock.patch('core.player_driver_endpoint', return_value=(12345, 'fresh')), \
                mock.patch('core.wait_for_driver_ready', return_value=True), \
                mock.patch('wot_launcher.subprocess.Popen', return_value=driver) as launch:
            self.assertTrue(self.window._start_driver(self.settings_dir))
        driver_env = launch.call_args.kwargs['env']
        self.assertEqual([core.worker_starter_executable(self.settings_dir), '--driver-only'],
                         launch.call_args.args[0])
        self.assertNotIn(core.CLIENT_SERVER_HOST_ENV_0922, driver_env)
        game = fixtures._Process(exit_code=0, pid=43)
        with mock.patch('wot_launcher.subprocess.Popen', return_value=game) as visible, \
                mock.patch('core.wait_for_paired_player_exit', return_value=(0, False)) as wait:
            self.window._run_game(self.settings_dir, core.PORT_0_9_22, '10.0.0.5', 28782)
        env = visible.call_args.kwargs['env']
        for name in (core.DRIVER_PORT_ENV_0922, core.DRIVER_TOKEN_ENV_0922):
            self.assertEqual(driver_env[name], env[name])
        self.assertEqual('10.0.0.5', env[core.CLIENT_SERVER_HOST_ENV_0922])
        self.assertEqual('--paired-player', visible.call_args.args[0][1])
        self.assertIsNone(wait.call_args.kwargs['required_process'])
        self.assertIsNone(self.window._worker)

    def test_driver_exit_during_visible_play_does_not_close_the_visible_job(self):
        driver = fixtures._Process(exit_code=7, pid=42)
        game = mock.Mock(pid=43)
        game.poll.side_effect = [None, 0]
        self.window._driver = driver
        self.window._driver_endpoint = (12345, 'fresh')
        with mock.patch('wot_launcher.subprocess.Popen', return_value=game), \
                mock.patch('time.sleep'):
            self.assertFalse(self.window._run_game(
                self.settings_dir, core.PORT_0_9_22, '10.0.0.5', 28782))
        game.terminate.assert_not_called()
        game.kill.assert_not_called()

    def test_failed_driver_readiness_prevents_visible_start_and_stops_exact_driver(self):
        with open(core.worker_starter_executable(self.settings_dir), 'w') as f:
            f.write('fake starter')
        driver = fixtures._StoppableProcess(exit_code=0, pid=42)
        worker = fixtures._Process(exit_code=None, pid=43)
        self.window._worker = worker
        with mock.patch('core.wait_for_driver_ready', return_value=False), \
                mock.patch('wot_launcher.subprocess.Popen', return_value=driver), \
                mock.patch.object(self.window, '_request_starter_stop',
                                  side_effect=driver.request_stop) as stop:
            self.assertFalse(self.window._start_driver(self.settings_dir))
        stop.assert_called_once_with(driver, self.settings_dir)
        self.assertIs(self.window._worker, worker)
        self.assertFalse(worker.killed)
        self.assertIsNone(self.window._driver_endpoint)
        self.window._stop_driver()  # Idempotent.
        stop.assert_called_once()

    def test_remote_join_orders_driver_before_visible_without_local_bot_worker(self):
        order=[]
        session=dict(client=core.PORT_0_9_22, mode=core.MODE_JOIN, host='10.0.0.5',
                     tcp_port=28782, needs_server=False)
        with mock.patch('core.install_client_mod', return_value=[]), \
                mock.patch('core.ensure_0_9_22_preferences_isolation', return_value='isolated'), \
                mock.patch('core.write_settings', return_value=[]), \
                mock.patch('core.listener_status', return_value=core.LISTENER_COMPATIBLE), \
                mock.patch('wot_launcher.vehicle_overlays.prepare_vehicle_profile',
                           return_value={'profile':None}), \
                mock.patch.object(self.window, '_sync_vehicle_overlay', return_value=True), \
                mock.patch.object(self.window, '_restore_original_vehicle_data'), \
                mock.patch.object(self.window, '_start_worker') as bot, \
                mock.patch.object(self.window, '_start_driver',
                                  side_effect=lambda unused: order.append('driver') or True), \
                mock.patch.object(self.window, '_run_game',
                                  side_effect=lambda *args, **kwargs: order.append('visible')), \
                mock.patch.object(self.window, '_stop_driver',
                                  side_effect=lambda: order.append('driver_stop')):
            self.window._run_session(self.settings_dir,session,'Player')
        self.assertEqual(['driver','visible','driver_stop'],order)
        bot.assert_not_called()

    def test_session_collects_driver_roles_and_independent_dump(self):
        with mock.patch.dict(os.environ, {'APPDATA':self.settings_dir}):
            session=error_reports.begin_session(self.settings_dir, needs_driver=True,
                             session_id='20261006T190000Z-111111111111')
        self.assertIn(error_reports.ROLE_HIDDEN_PLAYER_DRIVER, session['expectedRoles'])
        self.assertNotIn(error_reports.ROLE_HIDDEN_WORKER, session['expectedRoles'])
        source=session['sources'][error_reports.ROLE_HIDDEN_PLAYER_DRIVER]
        self.assertTrue(error_reports._source_path(session,
            error_reports.ROLE_HIDDEN_PLAYER_DRIVER, source).endswith(
                'offline-driver-python.log'))
        self.assertTrue(error_reports.session_dump_path(session,
                error_reports.ROLE_HIDDEN_PLAYER_DRIVER).endswith('hidden-player-driver.dmp'))
        error_reports.expect_driver_starter_reset(session)
        self.assertTrue(session['sources'][error_reports.ROLE_HIDDEN_PLAYER_DRIVER_STARTER]['resetExpected'])

    def test_replay_stays_visible_only_and_failed_driver_never_launches_visible(self):
        session = dict(client=core.PORT_0_9_22, mode=core.MODE_JOIN,
                       host='10.0.0.5', tcp_port=28782, needs_server=False)
        for replay in (False, True):
            selected = dict(session)
            if replay:
                selected['replay_path'] = 'battle.wotlanreplay'
            with mock.patch('core.install_client_mod', return_value=[]), \
                    mock.patch('wot_launcher.replay_launch.validate', return_value={
                        'map': 'lakeville', 'bots': 29, 'records': 2, 'duration': 1.0}), \
                    mock.patch('core.ensure_0_9_22_preferences_isolation', return_value='isolated'), \
                    mock.patch('core.write_settings', return_value=[]), \
                    mock.patch('core.listener_status', return_value=core.LISTENER_COMPATIBLE), \
                    mock.patch('wot_launcher.vehicle_overlays.prepare_vehicle_profile',
                               return_value={'profile': None}), \
                    mock.patch.object(self.window, '_sync_vehicle_overlay', return_value=True), \
                    mock.patch.object(self.window, '_restore_original_vehicle_data'), \
                    mock.patch.object(self.window, '_start_driver', return_value=False) as driver, \
                    mock.patch.object(self.window, '_run_game', return_value=False) as visible:
                self.window._run_session(self.settings_dir, selected, 'Player')
            if replay:
                driver.assert_not_called()
                visible.assert_called_once()
                self.assertEqual('battle.wotlanreplay', visible.call_args.kwargs['replay_path'])
            else:
                driver.assert_called_once_with(self.settings_dir)
                visible.assert_not_called()

    def _run_local_startup(self, spawn, wait):
        with open(core.worker_starter_executable(self.settings_dir), 'w') as stream:
            stream.write('fake starter')
        session = dict(client=core.PORT_0_9_22, mode=core.MODE_SINGLE,
                       host=core.LOCAL_HOST, tcp_port=core.DEFAULT_SERVER_PORT,
                       needs_server=True)
        def stop(process, root):
            return process.request_stop(process, root)
        with mock.patch('core.install_client_mod', return_value=[]), \
                mock.patch('core.ensure_0_9_22_preferences_isolation', return_value='isolated'), \
                mock.patch('core.write_settings', return_value=[]), \
                mock.patch('wot_launcher.vehicle_overlays.prepare_vehicle_profile',
                           return_value={'profile': None}), \
                mock.patch('core.player_driver_endpoint', return_value=(12345, 'fresh')), \
                mock.patch('wot_launcher.subprocess.Popen', side_effect=spawn) as popen, \
                mock.patch('core.wait_for_hidden_clients_ready', side_effect=wait) as ready, \
                mock.patch.object(self.window, '_start_server', return_value=True), \
                mock.patch.object(self.window, '_stop_server'), \
                mock.patch.object(self.window, '_restore_original_vehicle_data'), \
                mock.patch.object(self.window, '_request_starter_stop', side_effect=stop) as stops, \
                mock.patch.object(self.window, '_run_game', return_value=False) as visible:
            self.window._run_session(self.settings_dir, session, 'Player')
        self.assertIsNone(self.window._worker)
        self.assertIsNone(self.window._driver)
        self.assertIsNone(self.window._driver_endpoint)
        return popen, ready, visible, stops

    def test_local_startup_spawns_both_before_any_readiness_wait(self):
        worker = fixtures._StoppableProcess(pid=41)
        driver = fixtures._StoppableProcess(pid=42)
        order = []
        def spawn(command, **options):
            order.append(command[1])
            if command[1] == '--driver-only':
                self.assertEqual('fresh', options['env'][core.DRIVER_TOKEN_ENV_0922])
                self.assertNotIn(core.CLIENT_SERVER_HOST_ENV_0922, options['env'])
                return driver
            self.assertNotIn(core.DRIVER_TOKEN_ENV_0922, options['env'])
            return worker
        def wait(clients, root, cancelled):
            self.assertEqual(['--worker-only', '--driver-only'], order)
            self.assertEqual((worker, driver), tuple(row[0] for row in clients))
            self.assertEqual((12345, 'fresh'), self.window._driver_endpoint)
            self.assertFalse(cancelled())
            return True, None
        popen, ready, visible, stops = self._run_local_startup(spawn, wait)
        self.assertEqual(2, popen.call_count)
        ready.assert_called_once()
        visible.assert_called_once()
        self.assertEqual([driver, worker], [call.args[0] for call in stops.call_args_list])

    def test_local_startup_child_failure_stops_only_its_loading_sibling(self):
        original_wait = core.wait_for_hidden_clients_ready
        for failed_index in (0, 1):
            with self.subTest(failed_index=failed_index):
                children = [fixtures._StoppableProcess(pid=41),
                            fixtures._StoppableProcess(pid=42)]
                children[failed_index].exit_code = 7
                with mock.patch.object(self.window, '_remember_process_exit',
                                       wraps=self.window._remember_process_exit) as remember:
                    popen, ready, visible, stops = self._run_local_startup(
                        list(children), original_wait)
                self.assertEqual(2, popen.call_count)
                ready.assert_called_once()
                visible.assert_not_called()
                stops.assert_called_once_with(children[1-failed_index], self.settings_dir)
                roles = (error_reports.ROLE_HIDDEN_WORKER,
                         error_reports.ROLE_HIDDEN_PLAYER_DRIVER)
                self.assertIn(mock.call(7, roles[failed_index]), remember.call_args_list)
                self.assertNotIn(mock.call(7, roles[1-failed_index]), remember.call_args_list)
                for child in children:
                    self.assertFalse(child.terminated)
                    self.assertFalse(child.killed)

    def test_second_spawn_failure_reclaims_the_already_started_worker(self):
        worker = fixtures._StoppableProcess(pid=41)
        popen, ready, visible, stops = self._run_local_startup(
            [worker, OSError('driver spawn failed')], mock.Mock())
        self.assertEqual(2, popen.call_count)
        ready.assert_not_called()
        visible.assert_not_called()
        stops.assert_called_once_with(worker, self.settings_dir)

    def test_cancellation_after_first_spawn_does_not_start_driver_or_visible(self):
        worker = fixtures._StoppableProcess(pid=41)
        def spawn(*args, **kwargs):
            self.window._stop_requested = True
            return worker
        popen, ready, visible, stops = self._run_local_startup(spawn, mock.Mock())
        popen.assert_called_once()
        ready.assert_not_called()
        visible.assert_not_called()
        stops.assert_called_once_with(worker, self.settings_dir)

    def test_cancellation_during_parallel_readiness_reclaims_both_children(self):
        worker = fixtures._StoppableProcess(pid=41)
        driver = fixtures._StoppableProcess(pid=42)
        original_wait = core.wait_for_hidden_clients_ready
        def wait(clients, root, cancelled):
            def cancel(unused):
                self.window._stop_requested = True
            return original_wait(clients, root, cancelled=cancelled,
                                 clock=lambda: 0.0, sleep=cancel)
        popen, ready, visible, stops = self._run_local_startup([worker, driver], wait)
        self.assertEqual(2, popen.call_count)
        visible.assert_not_called()
        self.assertEqual([driver, worker], [call.args[0] for call in stops.call_args_list])

    def test_cancellation_at_ready_boundary_still_prevents_visible_launch(self):
        worker = fixtures._StoppableProcess(pid=41)
        driver = fixtures._StoppableProcess(pid=42)
        def wait(clients, root, cancelled):
            self.window._stop_requested = True
            return True, None
        popen, ready, visible, stops = self._run_local_startup([worker, driver], wait)
        visible.assert_not_called()
        self.assertEqual([driver, worker], [call.args[0] for call in stops.call_args_list])

    def test_parallel_readiness_timeout_reclaims_both_without_visible_launch(self):
        worker = fixtures._StoppableProcess(pid=41)
        driver = fixtures._StoppableProcess(pid=42)
        original_wait = core.wait_for_hidden_clients_ready
        def wait(clients, root, cancelled):
            return original_wait(clients, root, cancelled=cancelled, timeout=1.0,
                                 clock=mock.Mock(side_effect=[0.0, 1.0]))
        popen, ready, visible, stops = self._run_local_startup([worker, driver], wait)
        visible.assert_not_called()
        self.assertEqual(2, stops.call_count)
        self.assertEqual({worker, driver}, {call.args[0] for call in stops.call_args_list})
