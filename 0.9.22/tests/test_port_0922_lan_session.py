import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = (ROOT / '0.9.22' / 'src' / 'res' / 'scripts' /
                'client' / 'gui' / 'mods' / 'offline_lan_0922')


def _test_adisp_process(function):
    def start(*args, **kwargs):
        generator = function(*args, **kwargs)
        try:
            caller = next(generator)
        except StopIteration:
            return

        def completed(result):
            try:
                generator.send(result)
            except StopIteration:
                pass

        caller(callback=completed)
    return start


def _lazy_result(result):
    return lambda callback: callback(result)


def _load():
    for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(PACKAGE_ROOT)]
            sys.modules[name] = module
    adisp = types.ModuleType('adisp')
    adisp.process = _test_adisp_process
    sys.modules['adisp'] = adisp
    full_name = 'gui.mods.offline_lan_0922.lan_session'
    sys.modules.pop(full_name, None)
    spec = importlib.util.spec_from_file_location(full_name,
                                                   PACKAGE_ROOT / 'lan_session.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


class _Client(object):
    def __init__(self, host, port, name, vehicle, max_health=100, on_event=None):
        self.host = host
        self.port = port
        self.name = name
        self.vehicle = vehicle
        self.max_health = max_health
        self.on_event = on_event
        self.player_id = 'p1'
        self.team = 1
        self.team_sizes = {1: 2, 2: 5}
        self.host_player_id = 'p1'
        self.phase = 'waiting'
        self.map_pool = []
        self.map_name = None
        self.spawn = [1, 2, 3]
        self.round_id = None
        self.ready = False
        self.start_calls = 0
        self.stop_calls = 0
        self.leave_calls = 0
        self.requests = []
        self.selections = []
        self.team_selections = []
        self.team_size_selections = []
        self.receipt_acks = []

    def start(self):
        self.start_calls += 1
        return True

    def stop(self):
        self.stop_calls += 1

    def request_start(self, map_name):
        self.requests.append(map_name)
        return True

    def leave_battle(self):
        self.leave_calls += 1
        return True

    def select_vehicle(self, vehicle, max_health):
        if not self.ready or self.phase != 'waiting':
            return False
        if vehicle == self.vehicle and max_health == self.max_health:
            return False
        self.selections.append((vehicle, max_health))
        self.vehicle = vehicle
        self.max_health = max_health
        return True

    def has_team_selection(self):
        return True

    def select_team(self, team):
        if not self.ready or self.phase != 'waiting':
            return False
        self.team_selections.append(team)
        return True

    def has_team_size_selection(self):
        return True

    def set_team_size(self, team, size):
        if (not self.ready or self.phase != 'waiting' or
                self.player_id != self.host_player_id):
            return False
        self.team_size_selections.append((team, size))
        return True

    def acknowledge_battle_receipt(self, receipt_id):
        self.receipt_acks.append(receipt_id)
        return True


class _Queue(object):
    def __init__(self, request_start, map_pool, endpoint=None, on_close=None):
        self.request_start = request_start
        self.map_pool = map_pool
        self.endpoint = endpoint
        self.on_close = on_close
        self.install_calls = 0
        self.uninstall_calls = 0
        self.close_calls = 0
        self.refresh_calls = 0

    def install(self):
        self.install_calls += 1

    def uninstall(self):
        self.uninstall_calls += 1

    def close(self):
        self.close_calls += 1

    def refresh(self):
        self.refresh_calls += 1
        return True


class _QueueScreen(object):
    def __init__(self, on_exit):
        self.on_exit = on_exit
        self.install_calls = 0
        self.open_calls = 0
        self.leave_calls = 0
        self.uninstall_calls = 0

    def install(self):
        self.install_calls += 1
        return True

    def open(self):
        self.open_calls += 1
        return True

    def leave(self):
        self.leave_calls += 1
        return True

    def uninstall(self):
        self.uninstall_calls += 1
        return True


class _JoinUI(object):
    def __init__(self, on_join):
        self.on_join = on_join
        self.install_calls = 0
        self.uninstall_calls = 0

    def install(self):
        self.install_calls += 1

    def uninstall(self):
        self.uninstall_calls += 1


class _BattleRuntime(object):
    def __init__(self):
        self.started = []
        self.stopped = []
        self.restore_accounts = []
        self.snapshots = []
        self.events = []
        self.rosters = []
        self.observations = []
        self.restore_pending = False

    def start(self, config, message=None, lan_client=None,
              on_local_leave=None):
        self.started.append({
            'config': dict(config), 'message': message,
            'lan_client': lan_client,
            'on_local_leave': on_local_leave})
        return True

    def lobby_restore_pending(self):
        return self.restore_pending

    def on_snapshot(self, message):
        self.snapshots.append(message)

    def on_events(self, message):
        self.events.append(message)

    def on_roster(self, message):
        self.rosters.append(message)

    def on_bot_observation(self, message):
        self.observations.append(message)

    def stop(self, show_login=True, restore_account=True):
        self.stopped.append(show_login)
        self.restore_accounts.append(restore_account)


class LANSessionTests(unittest.TestCase):
    def setUp(self):
        self.module = _load()
        self.saved_room_states = []
        self.module.port_config.load_waiting_room_state = mock.Mock(
            return_value={
                'schema': 1, 'map': None, 'team': 0, 'team_sizes': {}})
        self.module.port_config.save_waiting_room_state = mock.Mock(
            side_effect=self._save_room_state)
        self.clients = []
        self.queues = []
        self.opens = []
        self.battle_runtime = _BattleRuntime()
        self.snapshots = []
        self.statuses = []

        def client_factory(*args, **kwargs):
            client = _Client(*args, **kwargs)
            self.clients.append(client)
            return client

        def queue_factory(*args, **kwargs):
            queue = _Queue(*args, **kwargs)
            self.queues.append(queue)
            return queue

        def queue_screen_factory(on_exit):
            screen = _QueueScreen(on_exit)
            self.queue_screens.append(screen)
            return screen

        self.queue_screens = []
        self.session = self.module.LANSession(
            {'host': '10.0.0.5', 'port': 28782, 'name': 'P',
             'vehicle': 'ussr:MS-1', 'startupTimeoutSeconds': 12.0},
            client_factory=client_factory, queue_factory=queue_factory,
            picker_opener=lambda: self.opens.append(True) or True,
            battle_runtime=self.battle_runtime,
            vehicle_provider=lambda: ('ussr:R11_MS-1', 90),
            vehicle_compact_provider=lambda: 'YQ==',
            on_snapshot=self.snapshots.append,
            status_notifier=self.statuses.append,
            queue_screen_factory=queue_screen_factory)
        self.assertTrue(self.session.start())
        # Production only reaches start() from the Battle click, and only that
        # click may raise the room over the garage.
        self.session._picker_requested = True
        self.client = self.clients[0]

    def _save_room_state(self, value):
        self.saved_room_states.append({
            'schema': value.get('schema'),
            'map': value.get('map'),
            'team': value.get('team'),
            'team_sizes': dict(value.get('team_sizes') or {}),
        })
        return True

    def emit(self, kind, message):
        if kind == 'welcome':
            self.client.ready = True
            self.client.phase = message.get('phase', self.client.phase)
        if 'host_player_id' in message:
            self.client.host_player_id = message['host_player_id']
        if 'players' in message:
            self.client.roster = list(message['players'])
        self.client.on_event(kind, message)

    def test_waiting_room_team_selection_reaches_the_client(self):
        self.client.ready = True
        self.client.phase = 'waiting'
        self.session.state = 'waiting'

        self.assertTrue(self.session.select_team(2))

        self.assertEqual([2], self.client.team_selections)
        self.assertEqual(2, self.saved_room_states[-1]['team'])
        self.assertEqual(
            {1: 0, 2: 0}, self.session._team_status()['counts'])

    def test_host_changes_team_sizes_without_restarting_the_session(self):
        self.client.ready = True
        self.client.phase = 'waiting'
        self.session.state = 'waiting'

        self.assertTrue(self.session.set_team_size(1, 4))
        self.assertTrue(self.session.set_team_size(2, 9))

        self.assertEqual([(1, 4), (2, 9)],
                         self.client.team_size_selections)
        self.assertEqual(
            {1: 4, 2: 9}, self.saved_room_states[-1]['team_sizes'])
        self.assertEqual(1, self.client.start_calls)
        self.assertTrue(self.session._team_status()['size_supported'])

    def test_saved_room_choices_are_applied_after_the_server_welcome(self):
        self.module.port_config.load_waiting_room_state.return_value = {
            'schema': 1,
            'map': '05_prohorovka',
            'team': 2,
            'team_sizes': {1: 4, 2: 9},
        }
        clients = []

        def client_factory(*args, **kwargs):
            client = _Client(*args, **kwargs)
            clients.append(client)
            return client

        session = self.module.LANSession(
            {'host': '10.0.0.5', 'port': 28782, 'name': 'P',
             'vehicle': 'ussr:MS-1'},
            client_factory=client_factory,
            vehicle_provider=lambda: ('ussr:R11_MS-1', 90),
            queue_factory=lambda *args, **kwargs: _Queue(*args, **kwargs),
            room_factory=lambda *args, **kwargs: _Room(*args, **kwargs),
            queue_screen_factory=lambda on_exit: _QueueScreen(on_exit),
            status_notifier=lambda unused: None)

        self.assertTrue(session.start())
        client = clients[0]
        self.assertEqual(2, client.requested_team)
        client.ready = True
        client.on_event('welcome', {
            'phase': 'waiting',
            'map_pool': ['01_karelia', '05_prohorovka'],
            'host_player_id': 'p1',
        })

        self.assertEqual([(1, 4), (2, 9)],
                         client.team_size_selections)
        self.assertEqual([2], client.team_selections)

    def test_guest_cannot_change_team_sizes(self):
        self.client.ready = True
        self.client.phase = 'waiting'
        self.client.host_player_id = 'someone-else'
        self.session.state = 'waiting'

        self.assertFalse(self.session.set_team_size(1, 4))
        self.assertEqual([], self.client.team_size_selections)

    def test_postbattle_request_retries_after_failure_then_completes_once(self):
        class Store(object):
            def pending_arenas(self):
                return [123]
            def service_message_data(self, arena):
                return {'arenaUniqueID': arena}
            def progress(self):
                return {'battles': 0}

        callbacks = [False, True]
        service = types.SimpleNamespace(requestResults=mock.Mock(
            side_effect=lambda context: _lazy_result(callbacks.pop(0))))
        personality = types.ModuleType('gui.shared.personality')
        personality.ServicesLocator = types.SimpleNamespace(
            battleResults=service)
        context_module = types.ModuleType('gui.battle_results.context')
        context_module.RequestResultsContext = mock.Mock(
            side_effect=lambda *args: args)
        scheduled = []
        session = self.module.LANSession(
            {}, postbattle_store=Store(), lobby_ready=lambda: True,
            callback=lambda delay, function: (
                scheduled.append((delay, function)), len(scheduled))[1])
        session._publish_battle_service_message = mock.Mock(return_value=True)
        with mock.patch.dict(sys.modules, {
                'gui.shared.personality': personality,
                'gui.battle_results.context': context_module}):
            self.assertTrue(session._publish_postbattle_results())
            self.assertEqual(set(), session._requested_results)
            self.assertEqual(1, len(scheduled))
            self.assertEqual(
                self.module.POSTBATTLE_RETRY_DELAY, scheduled[0][0])
            scheduled[0][1]()

        self.assertEqual(2, service.requestResults.call_count)
        self.assertEqual({123}, session._completed_results)
        session._publish_battle_service_message.assert_called_once_with(
            123, {'arenaUniqueID': 123})

    def test_install_schedules_durable_postbattle_result_before_lan_join(self):
        class Store(object):
            def pending_arenas(self):
                return [123]
            def progress(self):
                return {'battles': 1}

        scheduled = []
        join_views = []

        def join_factory(on_join):
            view = _JoinUI(on_join)
            join_views.append(view)
            return view

        session = self.module.LANSession(
            {}, postbattle_store=Store(), lobby_ready=lambda: False,
            callback=lambda delay, function: (
                scheduled.append((delay, function)), len(scheduled))[1],
            join_factory=join_factory)

        self.assertTrue(session.install())

        self.assertEqual('ready_to_join', session.state)
        self.assertEqual(1, join_views[0].install_calls)
        self.assertEqual(1, len(scheduled))
        self.assertEqual(
            self.module.POSTBATTLE_RETRY_DELAY, scheduled[0][0])

    def test_postbattle_notification_retries_without_refetching_result(self):
        class Store(object):
            def pending_arenas(self):
                return [123]
            def latest_archived_arena(self):
                return None
            def service_message_data(self, arena):
                return {'arenaUniqueID': arena}
            def progress(self):
                return {'battles': 1}

        scheduled = []
        service = types.SimpleNamespace(requestResults=mock.Mock(
            side_effect=lambda context: _lazy_result(True)))
        personality = types.ModuleType('gui.shared.personality')
        personality.ServicesLocator = types.SimpleNamespace(
            battleResults=service)
        context_module = types.ModuleType('gui.battle_results.context')
        context_module.RequestResultsContext = mock.Mock(
            side_effect=lambda *args: args)
        session = self.module.LANSession(
            {}, postbattle_store=Store(), lobby_ready=lambda: True,
            callback=lambda delay, function: (
                scheduled.append((delay, function)), len(scheduled))[1])
        session._publish_battle_service_message = mock.Mock(
            side_effect=[False, True])
        with mock.patch.dict(sys.modules, {
                'gui.shared.personality': personality,
                'gui.battle_results.context': context_module}):
            self.assertTrue(session._publish_postbattle_results())
            self.assertEqual(1, len(scheduled))
            scheduled[0][1]()

        self.assertEqual(1, service.requestResults.call_count)
        self.assertEqual(2, session._publish_battle_service_message.call_count)
        self.assertEqual({123}, session._completed_results)

    def test_missing_postbattle_notification_data_retries_once_and_logs_once(self):
        class Store(object):
            def pending_arenas(self):
                return []
            def latest_archived_arena(self):
                return None
            def service_message_data(self, arena):
                return None
            def progress(self):
                return {'battles': 1}

        scheduled = []
        session = self.module.LANSession(
            {}, postbattle_store=Store(), lobby_ready=lambda: True,
            callback=lambda delay, function: (
                scheduled.append((delay, function)), len(scheduled))[1])
        session._completed_results.add(123)
        written = []
        with mock.patch.object(sys, 'stdout') as stdout:
            stdout.write = written.append
            self.assertFalse(session._publish_postbattle_results())
            self.assertEqual(1, len(scheduled))
            scheduled[0][1]()

        self.assertEqual(1, len(scheduled))
        self.assertEqual(1, len(written))
        self.assertIn('service-message data is unavailable', written[0])

    def test_stopped_session_ignores_late_postbattle_result_callback(self):
        class Store(object):
            def pending_arenas(self):
                return [123]
            def latest_archived_arena(self):
                return None
            def service_message_data(self, arena):
                return {'arenaUniqueID': arena}
            def progress(self):
                return {'battles': 1}

        result_callbacks = []
        service = types.SimpleNamespace(requestResults=mock.Mock(
            side_effect=lambda context: (
                lambda callback: result_callbacks.append(callback))))
        personality = types.ModuleType('gui.shared.personality')
        personality.ServicesLocator = types.SimpleNamespace(
            battleResults=service)
        context_module = types.ModuleType('gui.battle_results.context')
        context_module.RequestResultsContext = mock.Mock(
            side_effect=lambda *args: args)
        session = self.module.LANSession(
            {}, postbattle_store=Store(), lobby_ready=lambda: True)
        session._publish_battle_service_message = mock.Mock(return_value=True)
        with mock.patch.dict(sys.modules, {
                'gui.shared.personality': personality,
                'gui.battle_results.context': context_module}):
            self.assertTrue(session._publish_postbattle_results())
            session.stop(stop_runtime=False)
            result_callbacks[0](True)

        self.assertEqual(set(), session._completed_results)
        self.assertEqual(set(), session._requested_results)
        session._publish_battle_service_message.assert_not_called()

    def test_invalid_postbattle_service_boundary_logs_once_without_retry(self):
        class Store(object):
            def pending_arenas(self):
                return [123]
            def latest_archived_arena(self):
                return None
            def service_message_data(self, arena):
                return {'arenaUniqueID': arena}
            def progress(self):
                return {'battles': 1}

        scheduled = []
        personality = types.ModuleType('gui.shared.personality')
        personality.ServicesLocator = types.SimpleNamespace(
            battleResults=object())
        context_module = types.ModuleType('gui.battle_results.context')
        context_module.RequestResultsContext = mock.Mock(
            side_effect=lambda *args: args)
        session = self.module.LANSession(
            {}, postbattle_store=Store(), lobby_ready=lambda: True,
            callback=lambda delay, function: scheduled.append(function))
        written = []
        with mock.patch.object(sys, 'stdout') as stdout:
            stdout.write = written.append
            with mock.patch.dict(sys.modules, {
                    'gui.shared.personality': personality,
                    'gui.battle_results.context': context_module}):
                self.assertFalse(session._publish_postbattle_results())
                self.assertFalse(session._publish_postbattle_results())

        self.assertEqual([], scheduled)
        self.assertEqual(1, len(written))
        self.assertIn('native battle-results service is unavailable',
                      written[0])

    def test_new_session_silently_rebuilds_latest_archived_result_entry(self):
        class Store(object):
            def pending_arenas(self):
                return []
            def latest_archived_arena(self):
                return 456
            def service_message_data(self, arena):
                return {'arenaUniqueID': arena}
            def progress(self):
                return {'battles': 3}

        requested = []
        service = types.SimpleNamespace(requestResults=mock.Mock(
            side_effect=lambda context: (
                requested.append(context), _lazy_result(True))[1]))
        personality = types.ModuleType('gui.shared.personality')
        personality.ServicesLocator = types.SimpleNamespace(
            battleResults=service)
        context_module = types.ModuleType('gui.battle_results.context')
        context_module.RequestResultsContext = mock.Mock(
            side_effect=lambda *args: args)
        session = self.module.LANSession(
            {}, postbattle_store=Store(), lobby_ready=lambda: True)
        session._publish_battle_service_message = mock.Mock(return_value=True)
        with mock.patch.dict(sys.modules, {
                'gui.shared.personality': personality,
                'gui.battle_results.context': context_module}):
            self.assertTrue(session._publish_postbattle_results())
            self.assertFalse(session._publish_postbattle_results())

        self.assertEqual([(456, False, False, True)], requested)
        self.assertEqual({456}, session._completed_results)
        self.assertTrue(session._archived_result_replayed)
        session._publish_battle_service_message.assert_not_called()

    def test_postbattle_results_drain_one_request_at_a_time(self):
        class Store(object):
            def pending_arenas(self):
                return [123, 124]
            def latest_archived_arena(self):
                return 456
            def service_message_data(self, arena):
                return {'arenaUniqueID': arena}
            def progress(self):
                return {'battles': 3}

        active = [False]
        requested = []

        def request_results(context):
            self.assertFalse(active[0])
            active[0] = True
            requested.append(context)
            def caller(callback):
                # The exact cache clears its waiting gate before this callback.
                active[0] = False
                callback(True)
            return caller

        service = types.SimpleNamespace(
            requestResults=mock.Mock(side_effect=request_results))
        personality = types.ModuleType('gui.shared.personality')
        personality.ServicesLocator = types.SimpleNamespace(
            battleResults=service)
        context_module = types.ModuleType('gui.battle_results.context')
        context_module.RequestResultsContext = mock.Mock(
            side_effect=lambda *args: args)
        session = self.module.LANSession(
            {}, postbattle_store=Store(), lobby_ready=lambda: True)
        session._publish_battle_service_message = mock.Mock(return_value=True)
        with mock.patch.dict(sys.modules, {
                'gui.shared.personality': personality,
                'gui.battle_results.context': context_module}):
            self.assertTrue(session._publish_postbattle_results())

        self.assertEqual([
            (123, True, False, True),
            (124, True, False, True),
            (456, False, False, True),
        ], requested)
        self.assertEqual({123, 124, 456}, session._completed_results)
        self.assertTrue(session._archived_result_replayed)
        self.assertEqual([
            mock.call(123, {'arenaUniqueID': 123}),
            mock.call(124, {'arenaUniqueID': 124}),
        ], session._publish_battle_service_message.call_args_list)

    def test_departed_battle_posts_clickable_result_without_opening_it(self):
        class Store(object):
            def pending_arenas(self):
                return [123]
            def latest_archived_arena(self):
                return None
            def service_message_data(self, arena):
                return {'arenaUniqueID': arena}
            def should_show_immediately(self, arena):
                return False
            def progress(self):
                return {'battles': 1}

        requested = []
        service = types.SimpleNamespace(requestResults=mock.Mock(
            side_effect=lambda context: (
                requested.append(context), _lazy_result(True))[1]))
        personality = types.ModuleType('gui.shared.personality')
        personality.ServicesLocator = types.SimpleNamespace(
            battleResults=service)
        context_module = types.ModuleType('gui.battle_results.context')
        context_module.RequestResultsContext = mock.Mock(
            side_effect=lambda *args: args)
        session = self.module.LANSession(
            {}, postbattle_store=Store(), lobby_ready=lambda: True)
        session._publish_battle_service_message = mock.Mock(return_value=True)
        with mock.patch.dict(sys.modules, {
                'gui.shared.personality': personality,
                'gui.battle_results.context': context_module}):
            self.assertTrue(session._publish_postbattle_results())

        self.assertEqual([(123, False, False, True)], requested)
        session._publish_battle_service_message.assert_called_once_with(
            123, {'arenaUniqueID': 123})

    def test_postbattle_result_waits_for_lobby_then_opens_stock_window(self):
        class Store(object):
            def pending_arenas(self):
                return [123]
            def latest_archived_arena(self):
                return None
            def service_message_data(self, arena):
                return {'arenaUniqueID': arena}
            def progress(self):
                return {'battles': 1}

        lobby_ready = [False]
        callbacks = []
        service = types.SimpleNamespace(requestResults=mock.Mock(
            side_effect=lambda context: _lazy_result(True)))
        personality = types.ModuleType('gui.shared.personality')
        personality.ServicesLocator = types.SimpleNamespace(
            battleResults=service)
        context_module = types.ModuleType('gui.battle_results.context')
        context_module.RequestResultsContext = mock.Mock(
            side_effect=lambda *args: args)
        session = self.module.LANSession(
            {}, postbattle_store=Store(),
            lobby_ready=lambda: lobby_ready[0],
            callback=lambda delay, function: (
                callbacks.append((delay, function)), len(callbacks))[1])
        session._publish_postbattle_progress = mock.Mock(return_value=True)
        session._publish_battle_service_message = mock.Mock(return_value=True)
        with mock.patch.dict(sys.modules, {
                'gui.shared.personality': personality,
                'gui.battle_results.context': context_module}):
            self.assertFalse(session._publish_postbattle_results())
            self.assertEqual(1, len(callbacks))
            lobby_ready[0] = True
            callbacks[0][1]()

        self.assertEqual(self.module.POSTBATTLE_RETRY_DELAY, callbacks[0][0])
        session._publish_postbattle_progress.assert_called_once_with()
        service.requestResults.assert_called_once_with(
            (123, True, False, True))
        self.assertEqual({123}, session._completed_results)

    def test_lobby_view_notification_starts_postbattle_drain_without_retry(self):
        store = mock.Mock()
        store.progress.return_value = {'battles': 0}
        session = self.module.LANSession(
            {}, postbattle_store=store, lobby_ready=lambda: True)
        session._publish_postbattle_progress = mock.Mock(return_value=True)
        session._publish_postbattle_results = mock.Mock(return_value=True)

        self.assertTrue(session.on_lobby_view_loaded())

        session._publish_postbattle_progress.assert_called_once_with()
        session._publish_postbattle_results.assert_called_once_with()

    def test_clickable_battle_result_uses_native_service_channel_wrapper(self):
        received = []

        class Entry(object):
            def index(self):
                return 17

        chat_shared = types.ModuleType('chat_shared')
        chat_shared.SYS_MESSAGE_TYPE = types.SimpleNamespace(
            battleResults=Entry())
        chat_shared.SYS_MESSAGE_IMPORTANCE = types.SimpleNamespace(
            normal=Entry())
        messenger_entry = types.ModuleType('messenger.MessengerEntry')
        messenger_entry.g_instance = types.SimpleNamespace(
            protos=types.SimpleNamespace(BW=types.SimpleNamespace(
                serviceChannel=types.SimpleNamespace(
                    onReceiveSysMessage=received.append))))
        messenger = types.ModuleType('messenger')
        messenger.MessengerEntry = messenger_entry
        with mock.patch.dict(sys.modules, {
                'chat_shared': chat_shared, 'messenger': messenger,
                'messenger.MessengerEntry': messenger_entry}):
            self.assertTrue(self.session._publish_battle_service_message(
                123, {'arenaUniqueID': 123, 'credits': 7}))
            self.assertFalse(self.session._publish_battle_service_message(
                123, {'arenaUniqueID': 123, 'credits': 7}))

        self.assertEqual(1, len(received))
        action = received[0]
        self.assertEqual(123, action['data']['messageID'])
        self.assertEqual(17, action['data']['type'])
        self.assertEqual(123, action['data']['data']['arenaUniqueID'])

    def test_receipt_pushes_account_progress_once_and_duplicate_does_not(self):
        class Store(object):
            account_key = 'account'
            def __init__(self):
                self.battles = 0
            def progress(self):
                return {'battles': self.battles}
            def accept(self, unused_message):
                if self.battles:
                    return False
                self.battles = 1
                return True
            def pending_arenas(self):
                return []

        store = Store()
        publisher = mock.Mock(return_value=True)
        bigworld = types.ModuleType('BigWorld')
        bigworld.player = lambda: types.SimpleNamespace(
            fakeServer=types.SimpleNamespace(
                publish_postbattle_progress=publisher))
        statuses = []
        session = self.module.LANSession(
            {}, postbattle_store=store, lobby_ready=lambda: True,
            status_notifier=statuses.append)
        session.client = self.client
        session._publish_postbattle_results = mock.Mock(return_value=False)
        with mock.patch.dict(sys.modules, {'BigWorld': bigworld}):
            session._on_event('battle_receipt', {'receipt_id': 'r1'})
            session._on_event('battle_receipt', {'receipt_id': 'r1'})

        publisher.assert_called_once_with()
        self.assertEqual(['r1', 'r1'], self.client.receipt_acks)
        self.assertEqual([], statuses)

    def test_donation_runtime_reads_the_exact_nations_and_vehicle_list(self):
        nations_module = types.ModuleType('nations')
        nations_module.AVAILABLE_NAMES = ('ussr',)
        nations_module.INDICES = {'ussr': 0}
        items_module = types.ModuleType('items')
        vehicles_module = types.ModuleType('items.vehicles')
        vehicles_module.g_list = object()
        vehicles_module.VehicleDescr = object
        items_module.vehicles = vehicles_module
        sys.modules['nations'] = nations_module
        sys.modules['items'] = items_module
        sys.modules['items.vehicles'] = vehicles_module
        try:
            runtime = self.session._donation_runtime()
            self.assertIs(nations_module, runtime.nations)
            self.assertIs(vehicles_module, runtime.vehicles)
        finally:
            for name in ('nations', 'items', 'items.vehicles'):
                sys.modules.pop(name, None)

    def test_donation_runtime_is_none_without_the_exact_modules(self):
        self.assertIsNone(self.session._donation_runtime())

    def test_waiting_messages_install_and_open_picker_once(self):
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.emit('roster', {
            'phase': 'waiting', 'map_pool': ['01_karelia'],
            'players': [{'id': 'p1', 'name': 'Host'}]})

        self.assertEqual('waiting', self.session.state)
        self.assertEqual(1, len(self.queues))
        self.assertEqual(1, self.queues[0].install_calls)
        self.assertEqual(1, self.queues[0].refresh_calls)
        self.assertEqual([True], self.opens)
        self.assertEqual(['01_karelia'], self.queues[0].map_pool())
        self.assertEqual(
            'LAN SERVER: 10.0.0.5:28782\n'
            'PLAYERS (1): Host\n'
            'SELECT A MAP, THEN CLICK CREATE TO START\n'
            'OTHER PLAYERS JOIN WITH THE BATTLE BUTTON',
            self.queues[0].endpoint())

    def test_open_picker_description_refreshes_with_live_roster(self):
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})
        queue = self.queues[0]

        self.emit('roster', {
            'phase': 'waiting', 'map_pool': ['01_karelia'],
            'players': [
                {'id': 'p1', 'name': 'Host'},
                {'id': 'p2', 'name': 'Guest'},
            ]})

        self.assertEqual(1, queue.refresh_calls)
        self.assertIn('PLAYERS (2): Host, Guest', queue.endpoint())
        self.assertEqual([True], self.opens)

    def test_waiting_room_opens_over_the_stock_queue_screen(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})

        self.assertEqual(1, len(self.queue_screens))
        screen = self.queue_screens[0]
        self.assertEqual(1, screen.install_calls)
        self.assertEqual(1, screen.open_calls)
        self.assertTrue(self.session._picker_open)

    def test_leave_room_leaves_the_stock_queue(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})

        self.assertTrue(self.session.leave_room())

        self.assertEqual(1, self.queue_screens[0].leave_calls)
        self.assertEqual(1, self.client.stop_calls)

    def test_stock_queue_exit_leaves_the_lan_room(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})
        screen = self.queue_screens[0]

        self.assertTrue(screen.on_exit())

        self.assertEqual('ready_to_join', self.session.state)
        self.assertIsNone(self.session.client)
        self.assertEqual(1, self.client.stop_calls)
        self.assertFalse(self.session._picker_open)
        self.assertEqual(1, self.queues[0].close_calls)
        self.assertEqual(1, screen.leave_calls)
        self.assertFalse(screen.on_exit())
        self.assertEqual(1, self.client.stop_calls)

    def test_denied_start_keeps_the_same_queue_screen_engaged(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})
        screen = self.queue_screens[0]
        self.assertTrue(self.queues[0].request_start('01_karelia'))

        self.emit('start_denied', {'code': 'host_only'})

        self.assertEqual('waiting', self.session.state)
        self.assertEqual([screen], self.queue_screens)
        self.assertEqual(0, screen.leave_calls)

    def _finish_first_round(self, garage):
        self.session._vehicle_provider = lambda: (garage[0], garage[1])
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia'],
                              'round_id': 1, 'host_player_id': 'p1'})
        self.session.request_start('01_karelia')
        self.emit('battle_start', {
            'round_id': 1, 'map': '01_karelia',
            'players': [{'id': 'p1', 'vehicle': self.client.vehicle,
                         'spawn': {'x': 1, 'y': 2, 'z': 3}}]})
        self.session._on_local_battle_leave()

    def _start_second_round(self):
        self.session.request_start('01_karelia')
        self.emit('battle_start', {
            'round_id': 2, 'map': '01_karelia',
            'players': [{'id': 'p1', 'vehicle': self.client.vehicle,
                         'spawn': {'x': 1, 'y': 2, 'z': 3}}]})
        return self.battle_runtime.started[-1]['config']['vehicle']

    def test_second_round_uses_the_vehicle_chosen_after_the_first_one(self):
        garage = ['ussr:R11_MS-1', 90]
        self._finish_first_round(garage)
        garage[0], garage[1] = 'germany:G01_PzI', 150

        self.emit('roster', {'phase': 'waiting', 'round_id': 2,
                             'map_pool': ['01_karelia'],
                             'host_player_id': 'p1'})

        self.assertEqual([('germany:G01_PzI', 150)], self.client.selections)
        self.assertEqual('germany:G01_PzI', self._start_second_round())
        self.assertEqual(1, len(self.clients))

    def test_battle_click_publishes_a_garage_change_made_in_the_room(self):
        garage = ['ussr:R11_MS-1', 90]
        self._finish_first_round(garage)
        self.emit('roster', {'phase': 'waiting', 'round_id': 2,
                             'map_pool': ['01_karelia'],
                             'host_player_id': 'p1'})
        garage[0], garage[1] = 'usa:T1_Cunningham', 140

        self.session.join()

        self.assertEqual([('usa:T1_Cunningham', 140)], self.client.selections)
        self.assertEqual('usa:T1_Cunningham', self._start_second_round())

    def test_unreadable_garage_selection_keeps_the_accepted_vehicle(self):
        garage = ['ussr:R11_MS-1', 90]
        self._finish_first_round(garage)

        def unavailable():
            raise ValueError('the current garage vehicle is not available')

        self.session._vehicle_provider = unavailable
        self.emit('roster', {'phase': 'waiting', 'round_id': 2,
                             'map_pool': ['01_karelia'],
                             'host_player_id': 'p1'})

        self.assertEqual([], self.client.selections)
        self.assertEqual('waiting', self.session.state)
        self.assertEqual('ussr:R11_MS-1', self._start_second_round())

    def test_battle_start_keeps_the_stock_queue(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.assertTrue(self.queues[0].request_start('01_karelia'))

        self.emit('battle_start', {
            'round_id': 1, 'map': '01_karelia', 'players': [
                {'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                 'vehicle': 'ussr:T-34'}]})

        self.assertEqual('battle', self.session.state)
        self.assertEqual(0, self.queue_screens[0].leave_calls)
        self.assertEqual(0, self.queue_screens[0].uninstall_calls)

    def test_waiting_disconnect_leaves_the_stock_queue(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})

        self.emit('connection_lost', {'message': 'socket closed'})

        self.assertEqual('ready_to_join', self.session.state)
        self.assertEqual(1, self.queue_screens[0].leave_calls)

    def test_stop_uninstalls_the_queue_screen(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})

        self.session.stop()

        self.assertEqual(1, self.queue_screens[0].uninstall_calls)

    def test_unavailable_queue_screen_keeps_the_room_over_the_hangar(self):
        def broken_factory(on_exit):
            raise RuntimeError('no prb runtime')

        clients = []

        def client_factory(*args, **kwargs):
            client = _Client(*args, **kwargs)
            clients.append(client)
            return client

        opens = []
        session = self.module.LANSession(
            {'host': '10.0.0.5', 'port': 28782, 'name': 'P'},
            client_factory=client_factory,
            queue_factory=lambda *args, **kwargs: _Queue(*args, **kwargs),
            picker_opener=lambda: opens.append(True) or True,
            vehicle_provider=lambda: ('ussr:R11_MS-1', 90),
            status_notifier=lambda message: None,
            queue_screen_factory=broken_factory)
        self.assertTrue(session.start())
        session._picker_requested = True
        clients[0].ready = True
        clients[0].on_event('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})

        self.assertTrue(session._picker_open)
        self.assertIsNone(session._queue_screen)
        self.assertIsNone(session._queue_screen_factory)

    def test_install_owns_battle_button_until_join_and_stop(self):
        clients = []
        join_views = []

        def client_factory(*args, **kwargs):
            client = _Client(*args, **kwargs)
            clients.append(client)
            return client

        def join_factory(callback):
            view = _JoinUI(callback)
            join_views.append(view)
            return view

        session = self.module.LANSession(
            {'host': '10.0.0.5', 'port': 28782},
            client_factory=client_factory, join_factory=join_factory,
            vehicle_provider=lambda: ('ussr:R11_MS-1', 90),
            status_notifier=lambda unused_message: None)

        self.assertTrue(session.install())
        self.assertEqual('ready_to_join', session.state)
        self.assertEqual(1, join_views[0].install_calls)
        self.assertEqual([], clients)
        self.assertTrue(join_views[0].on_join(0, 'battle'))
        self.assertEqual(1, len(clients))
        self.assertEqual(1, clients[0].start_calls)
        # An error stop keeps our Battle button; only mod shutdown releases it.
        session.stop(show_login=False)
        self.assertEqual(0, join_views[0].uninstall_calls)
        session._stopped = False
        session.fini(show_login=False)
        self.assertEqual(1, join_views[0].uninstall_calls)

    def test_battle_click_rejoins_a_room_parked_after_a_round(self):
        clients = []
        join_views = []

        def client_factory(*args, **kwargs):
            client = _Client(*args, **kwargs)
            clients.append(client)
            return client

        def join_factory(callback):
            view = _JoinUI(callback)
            join_views.append(view)
            return view

        session = self.module.LANSession(
            {'host': '10.0.0.5', 'port': 28782},
            client_factory=client_factory, join_factory=join_factory,
            vehicle_provider=lambda: ('ussr:R11_MS-1', 90),
            status_notifier=lambda unused_message: None)
        self.assertTrue(session.install())
        self.assertTrue(join_views[0].on_join(0, 'battle'))
        self.assertEqual(1, len(clients))
        session.state = 'awaiting_round_end'

        self.assertTrue(join_views[0].on_join(0, 'battle'))

        self.assertEqual(2, len(clients))
        self.assertEqual(1, clients[0].stop_calls)
        self.assertEqual('connecting', session.state)

    def test_round_end_watchdog_rejoins_without_a_second_click(self):
        clients = []
        callbacks = []

        def client_factory(*args, **kwargs):
            client = _Client(*args, **kwargs)
            clients.append(client)
            return client

        def schedule(unused_delay, callback):
            callbacks.append(callback)
            return len(callbacks)

        session = self.module.LANSession(
            {'host': '10.0.0.5', 'port': 28782},
            client_factory=client_factory,
            join_factory=_JoinUI,
            callback=schedule, cancel_callback=lambda unused_id: None,
            vehicle_provider=lambda: ('ussr:R11_MS-1', 90),
            status_notifier=lambda unused_message: None)
        self.assertTrue(session.install())
        self.assertTrue(session.join())
        self.assertEqual(1, len(clients))

        session._enter_awaiting_round_end()
        self.assertEqual(1, len(callbacks))
        callbacks[0]()

        self.assertEqual(2, len(clients))
        self.assertEqual('connecting', session.state)

    def test_battle_click_revives_a_stopped_session(self):
        clients = []
        join_views = []

        def client_factory(*args, **kwargs):
            client = _Client(*args, **kwargs)
            clients.append(client)
            return client

        def join_factory(callback):
            view = _JoinUI(callback)
            join_views.append(view)
            return view

        session = self.module.LANSession(
            {'host': '10.0.0.5', 'port': 28782},
            client_factory=client_factory, join_factory=join_factory,
            vehicle_provider=lambda: ('ussr:R11_MS-1', 90),
            status_notifier=lambda unused_message: None)
        self.assertTrue(session.install())
        session.stop(show_login=False)
        self.assertEqual('stopped', session.state)

        self.assertTrue(join_views[0].on_join(0, 'battle'))

        self.assertEqual(1, len(clients))
        self.assertFalse(session._stopped)
        self.assertEqual('connecting', session.state)

    def test_selected_vehicle_provider_is_used_for_each_new_client(self):
        clients = []
        callbacks = []
        selections = iter((
            ('china:Ch01_Type59', 1300),
            ('usa:A12_T32', 1550),
        ))

        def client_factory(*args, **kwargs):
            client = _Client(*args, **kwargs)
            clients.append(client)
            return client

        def schedule(unused_delay, callback):
            callbacks.append(callback)
            return len(callbacks)

        session = self.module.LANSession(
            {'host': '10.0.0.5', 'port': 28782,
             'vehicle': 'ussr:R11_MS-1', 'max_health': 90},
            client_factory=client_factory,
            vehicle_provider=lambda: next(selections),
            callback=schedule,
            status_notifier=lambda unused_message: None)

        self.assertTrue(session.start())
        clients[0].on_event('error', {'message': 'connection refused'})
        callbacks[0]()

        self.assertEqual('china:Ch01_Type59', clients[0].vehicle)
        self.assertEqual(1300, clients[0].max_health)
        self.assertEqual('usa:A12_T32', clients[1].vehicle)
        self.assertEqual(1550, clients[1].max_health)

    def test_first_join_with_no_selected_vehicle_stays_ready_to_join(self):
        clients = []
        join_views = []
        statuses = []

        def client_factory(*args, **kwargs):
            client = _Client(*args, **kwargs)
            clients.append(client)
            return client

        def join_factory(callback):
            view = _JoinUI(callback)
            join_views.append(view)
            return view

        def fail_selection():
            raise RuntimeError('garage selection is not ready')

        session = self.module.LANSession(
            {'vehicle': 'ussr:R11_MS-1', 'max_health': 90},
            client_factory=client_factory,
            join_factory=join_factory,
            vehicle_provider=fail_selection,
            status_notifier=statuses.append)

        self.assertTrue(session.install())
        self.assertTrue(join_views[0].on_join(0, 'battle'))

        self.assertEqual('ready_to_join', session.state)
        self.assertIsNone(session.client)
        self.assertEqual([], clients)
        self.assertEqual(
            [self.module.VEHICLE_SELECTION_WARNING], statuses)

    def test_invalid_selected_vehicle_never_falls_back_to_config(self):
        clients = []
        statuses = []

        session = self.module.LANSession(
            {'vehicle': 'ussr:R11_MS-1', 'max_health': 90},
            client_factory=lambda *args, **kwargs: clients.append(
                _Client(*args, **kwargs)),
            vehicle_provider=lambda: ('', 0),
            status_notifier=statuses.append)

        self.assertFalse(session.start())

        self.assertEqual('ready_to_join', session.state)
        self.assertIsNone(session.client)
        self.assertEqual([], clients)
        self.assertEqual(
            [self.module.VEHICLE_SELECTION_WARNING], statuses)

    def test_retry_with_lost_selection_retires_socket_and_can_rejoin(self):
        clients = []
        callbacks = []
        statuses = []
        selections = [
            ('china:Ch01_Type59', 1300),
            RuntimeError('garage selection was cleared'),
            ('usa:A12_T32', 1550),
        ]

        def client_factory(*args, **kwargs):
            client = _Client(*args, **kwargs)
            clients.append(client)
            return client

        def vehicle_provider():
            selection = selections.pop(0)
            if isinstance(selection, Exception):
                raise selection
            return selection

        def schedule(unused_delay, callback):
            callbacks.append(callback)
            return len(callbacks)

        session = self.module.LANSession(
            {'host': '10.0.0.5', 'port': 28782},
            client_factory=client_factory,
            vehicle_provider=vehicle_provider,
            callback=schedule,
            status_notifier=statuses.append)

        self.assertTrue(session.start())
        old_client = clients[0]
        old_client.on_event('error', {'message': 'connection refused'})
        callbacks[0]()

        self.assertEqual(1, old_client.stop_calls)
        self.assertIsNone(old_client.on_event)
        self.assertIsNone(session.client)
        self.assertEqual('ready_to_join', session.state)
        self.assertEqual(
            self.module.VEHICLE_SELECTION_WARNING, statuses[-1])

        self.assertTrue(session.join())
        self.assertEqual('connecting', session.state)
        self.assertEqual(2, len(clients))
        self.assertEqual('usa:A12_T32', clients[1].vehicle)
        self.assertEqual(1550, clients[1].max_health)

    def test_default_provider_reads_exact_0922_current_vehicle_item(self):
        current_vehicle = types.ModuleType('CurrentVehicle')
        current_vehicle.g_currentVehicle = types.SimpleNamespace(
            item=types.SimpleNamespace(
                descriptor=types.SimpleNamespace(
                    type=types.SimpleNamespace(name='germany:G04_PzVI_Tiger_I'),
                    maxHealth=1500)))

        with mock.patch.dict(
                sys.modules, {'CurrentVehicle': current_vehicle}):
            self.assertEqual(
                ('germany:G04_PzVI_Tiger_I', 1500),
                self.module._selected_vehicle_details())

    def test_default_provider_rejects_a_secret_environment_vehicle(self):
        current_vehicle = types.ModuleType('CurrentVehicle')
        current_vehicle.g_currentVehicle = types.SimpleNamespace(
            item=types.SimpleNamespace(
                descriptor=types.SimpleNamespace(
                    type=types.SimpleNamespace(
                        name='germany:Env_Artillery',
                        tags=('SPG', 'secret', 'unrecoverable')),
                    maxHealth=300)))

        with mock.patch.dict(
                sys.modules, {'CurrentVehicle': current_vehicle}):
            with self.assertRaisesRegex(ValueError, 'descriptor is invalid'):
                self.module._selected_vehicle_details()

    def test_picker_is_not_open_before_server_welcome(self):
        self.assertEqual('connecting', self.session.state)
        self.assertEqual([], self.opens)
        self.assertEqual([], self.queues)

    def test_repeated_join_does_not_create_a_second_connection(self):
        self.assertTrue(self.session.join())
        self.assertEqual(1, len(self.clients))
        self.assertEqual(1, self.client.start_calls)
        self.assertIn('Still connecting', self.statuses[-1])
        self.assertEqual([True], self.opens)
        self.assertEqual(1, len(self.queues))
        self.assertIsNone(self.queues[0].map_pool())

    def test_connection_picker_endpoint_change_replaces_unready_client(self):
        self.assertTrue(self.session.join())
        old_client = self.client

        with mock.patch.object(
                self.module.port_config, 'save_endpoint',
                return_value=True) as save_endpoint:
            self.assertTrue(self.queues[0].request_start(
                '01_karelia',
                'LAN SERVER: 10.20.30.40:30000\n'
                'PLAYERS (0): waiting for roster\n'
                'EDIT THE FIRST LINE TO CHANGE THE SERVER\n'
                'THEN CLICK CREATE TO CONNECT'))

        self.assertIsNone(old_client.on_event)
        self.assertEqual(1, old_client.stop_calls)
        self.assertEqual(2, len(self.clients))
        self.assertEqual('01_karelia', self.session._pending_map)
        self.assertEqual('connecting', self.session.state)
        self.assertEqual('10.20.30.40', self.session.client.host)
        self.assertEqual(30000, self.session.client.port)
        save_endpoint.assert_called_once_with('10.20.30.40', 30000)

    def test_guest_discards_map_selected_in_preconnection_settings(self):
        self.assertTrue(self.session.join())
        self.assertTrue(self.queues[0].request_start(
            '01_karelia', 'LAN SERVER: 10.0.0.5:28782'))
        self.assertEqual('01_karelia', self.session._pending_map)

        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia'],
            'host_player_id': 'other'})

        self.assertIsNone(self.session._pending_map)
        self.assertEqual([], self.client.requests)
        self.assertEqual('waiting', self.session.state)
        self.assertFalse(self.session._picker_open)
        self.assertIn('Waiting for host', self.statuses[-1])

    def test_host_selection_starts_once_after_welcome(self):
        message = {'phase': 'waiting', 'map_pool': ['01_karelia']}
        self.emit('welcome', message)
        self.assertTrue(self.queues[0].request_start(
            '01_karelia', 'LAN SERVER: 10.0.0.5:28782'))
        self.assertFalse(self.queues[0].request_start(
            '05_prohorovka', 'LAN SERVER: 10.0.0.5:28782'))
        self.assertIsNone(self.session._pending_map)
        self.assertEqual(['01_karelia'], self.client.requests)
        self.assertTrue(self.session._picker_open)
        self.assertTrue(self.session._start_requested)
        self.assertEqual('awaiting_battle_start', self.session.state)

    def test_guest_waits_without_opening_map_picker(self):
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['05_prohorovka'],
            'host_player_id': 'other'})

        self.assertEqual([], self.client.requests)
        self.assertEqual('waiting', self.session.state)
        self.assertFalse(self.session._picker_open)
        # The stock window cannot present a guest, so it stays closed.
        self.assertEqual([], self.opens)
        self.assertIn('Waiting for host', self.statuses[-1])

    def test_waiting_guest_becomes_host_and_gets_picker(self):
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia'],
            'host_player_id': 'other'})
        self.assertEqual([], self.opens)

        self.emit('roster', {
            'phase': 'waiting', 'map_pool': ['01_karelia'],
            'host_player_id': 'p1'})

        self.assertEqual([True], self.opens)
        self.assertTrue(self.session._picker_open)
        self.assertIn('now the LAN room host', self.statuses[-1])

    def test_edited_endpoint_is_saved_and_replaces_client_generation(self):
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})
        old_client = self.client
        stale_event = old_client.on_event
        with mock.patch.object(
                self.module.port_config, 'save_endpoint',
                return_value=True) as save_endpoint:
            self.assertTrue(self.queues[0].request_start(
                '01_karelia', 'LAN SERVER: 10.20.30.40:30000'))

        self.assertIsNone(old_client.on_event)
        self.assertEqual(1, old_client.stop_calls)
        self.assertEqual(2, len(self.clients))
        replacement = self.session.client
        self.assertEqual('10.20.30.40', replacement.host)
        self.assertEqual(30000, replacement.port)
        self.assertEqual('01_karelia', self.session._pending_map)
        save_endpoint.assert_called_once_with('10.20.30.40', 30000)

        stale_event('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.assertEqual('connecting', self.session.state)
        self.assertEqual([], replacement.requests)

    def test_cancelled_retry_cannot_replace_edited_endpoint_client(self):
        callbacks = {}
        cancelled = []

        def schedule(unused_delay, function):
            callbacks[7] = function
            return 7

        self.session._callback = schedule
        self.session._cancel_callback = cancelled.append
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.client.ready = False
        self.emit('error', {'message': 'connection refused'})
        stale_retry = callbacks[7]

        with mock.patch.object(
                self.module.port_config, 'save_endpoint', return_value=True):
            self.assertTrue(self.queues[0].request_start(
                '01_karelia', 'LAN SERVER: 10.20.30.40:30000'))
        replacement = self.session.client

        stale_retry()

        self.assertEqual([7], cancelled)
        self.assertIs(replacement, self.session.client)
        self.assertEqual(2, len(self.clients))
        self.assertEqual(0, replacement.stop_calls)

    def test_endpoint_write_failure_keeps_existing_connection(self):
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})
        old_client = self.client
        with mock.patch.object(
                self.module.port_config, 'save_endpoint',
                return_value=False):
            self.assertFalse(self.queues[0].request_start(
                '01_karelia', 'LAN SERVER: 10.20.30.40:30000'))

        self.assertIs(old_client, self.session.client)
        self.assertEqual(0, old_client.stop_calls)
        self.assertEqual('10.0.0.5', self.session._config['host'])
        self.assertEqual(28782, self.session._config['port'])
        self.assertTrue(self.session._picker_open)
        self.assertIn('Could not save', self.statuses[-1])

    def test_invalid_edited_endpoint_keeps_picker_open(self):
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.assertFalse(self.queues[0].request_start(
            '01_karelia', 'LAN SERVER: bad host:28782'))

        self.assertIs(self.client, self.session.client)
        self.assertTrue(self.session._picker_open)
        self.assertIn('invalid', self.statuses[-1])

    def test_initial_connection_failure_is_visible_and_retries(self):
        callbacks = {}
        cancelled = []

        def schedule(delay, function):
            callback_id = len(callbacks) + 1
            callbacks[callback_id] = (delay, function)
            return callback_id

        self.session._callback = schedule
        self.session._cancel_callback = cancelled.append

        self.emit('error', {'message': 'connection refused'})

        self.assertEqual('retrying', self.session.state)
        self.assertEqual(1, len(self.statuses))
        self.assertIn('10.0.0.5:28782', self.statuses[0])
        self.assertIn('opening server settings', self.statuses[0])
        self.assertEqual([True], self.opens)
        self.assertTrue(self.session._picker_open)
        self.assertEqual(1, len(callbacks))
        callback_id, (delay, retry) = next(iter(callbacks.items()))
        self.assertEqual(self.module.RECONNECT_DELAY, delay)

        del callbacks[callback_id]
        retry()

        self.assertEqual(2, len(self.clients))
        self.assertEqual(1, self.client.stop_calls)
        replacement = self.clients[-1]
        self.assertEqual(1, replacement.start_calls)
        self.assertEqual('connecting', self.session.state)

        replacement.ready = True
        replacement.on_event(
            'welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})

        self.assertEqual('waiting', self.session.state)
        self.assertEqual(2, len(self.statuses))
        self.assertEqual([True], self.opens)
        self.assertIsNone(self.session._retry_callback_id)
        self.assertEqual([], cancelled)

    def test_stop_cancels_pending_initial_connection_retry(self):
        callbacks = {}
        cancelled = []

        def schedule(unused_delay, function):
            callbacks[7] = function
            return 7

        def cancel(callback_id):
            cancelled.append(callback_id)
            callbacks.pop(callback_id, None)

        self.session._callback = schedule
        self.session._cancel_callback = cancel
        self.emit('error', {'message': 'connection refused'})

        self.session.stop(show_login=False)

        self.assertEqual([7], cancelled)
        self.assertEqual({}, callbacks)
        self.assertEqual('stopped', self.session.state)

    def test_selection_only_sends_start_request_and_denial_reopens(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.assertTrue(self.queues[0].request_start('01_karelia'))
        self.assertFalse(self.queues[0].request_start('01_karelia'))
        self.assertEqual(['01_karelia'], self.client.requests)
        self.assertEqual('01_karelia', self.saved_room_states[-1]['map'])
        self.assertEqual([], self.battle_runtime.started)

        self.emit('start_denied', {'reason': 'host only'})
        self.assertEqual('waiting', self.session.state)
        self.assertEqual([True], self.opens)
        self.assertEqual([], self.battle_runtime.started)

    def test_selection_closes_picker_after_scaleform_event_returns(self):
        callbacks = {}
        cancelled = []

        def schedule(delay, function):
            callback_id = len(callbacks) + 1
            callbacks[callback_id] = (delay, function)
            return callback_id

        def cancel(callback_id):
            cancelled.append(callback_id)
            callbacks.pop(callback_id, None)

        self.session._callback = schedule
        self.session._cancel_callback = cancel
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})

        self.assertTrue(self.queues[0].request_start('01_karelia'))

        self.assertTrue(self.session._picker_open)
        self.assertEqual(0, self.queues[0].close_calls)
        self.assertEqual(1, len(callbacks))
        callback_id, (delay, close_picker) = next(iter(callbacks.items()))
        self.assertEqual(0.0, delay)

        callbacks.pop(callback_id)
        close_picker()

        self.assertFalse(self.session._picker_open)
        self.assertEqual(1, self.queues[0].close_calls)
        self.assertIsNone(self.session._picker_close_callback_id)
        self.assertEqual([], cancelled)

    def test_early_battle_start_finishes_deferred_picker_close_first(self):
        callbacks = {}
        cancelled = []

        def schedule(delay, function):
            callbacks[7] = (delay, function)
            return 7

        def cancel(callback_id):
            cancelled.append(callback_id)
            callbacks.pop(callback_id, None)

        self.session._callback = schedule
        self.session._cancel_callback = cancel
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.assertTrue(self.queues[0].request_start('01_karelia'))

        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})

        self.assertEqual([7], cancelled)
        self.assertEqual({}, callbacks)
        self.assertFalse(self.session._picker_open)
        self.assertEqual(1, self.queues[0].close_calls)
        self.assertEqual(1, len(self.battle_runtime.started))
        self.assertEqual('battle', self.session.state)

    def test_real_close_notification_cannot_reopen_before_early_start(self):
        callbacks = {}

        def schedule(delay, function):
            callbacks[7] = (delay, function)
            return 7

        self.session._callback = schedule
        self.session._cancel_callback = lambda callback_id: callbacks.pop(
            callback_id, None)
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})
        queue = self.queues[0]
        original_close = queue.close

        def close_with_native_notification():
            original_close()
            queue.on_close()

        queue.close = close_with_native_notification
        self.assertTrue(queue.request_start('01_karelia'))

        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})

        self.assertEqual({}, callbacks)
        self.assertEqual([True], self.opens)
        self.assertFalse(self.session._picker_open)
        self.assertEqual(1, queue.close_calls)
        self.assertEqual('battle', self.session.state)

    def test_a_denial_after_a_close_leaves_the_garage_alone(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})

        self.queues[0].on_close()
        self.emit('start_denied', {'reason': 'try again'})

        # Closing the room disarms it; a refused start that arrives afterwards
        # must not raise it over the garage again.
        self.assertEqual([True], self.opens)
        self.assertFalse(self.session._picker_open)

    def test_stock_picker_close_stays_closed_until_explicit_action(self):
        callbacks = []
        self.session._callback = (
            lambda delay, function: callbacks.append((delay, function)) or 7)
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})

        self.queues[0].on_close()

        self.assertFalse(self.session._picker_open)
        self.assertEqual([], callbacks)
        self.assertEqual([True], self.opens)

        self.emit('roster', {
            'phase': 'waiting', 'map_pool': ['01_karelia'],
            'players': [
                {'id': 'p1', 'name': 'Host'},
                {'id': 'p2', 'name': 'Guest'},
            ]})

        self.assertFalse(self.session._picker_open)
        self.assertEqual([], callbacks)
        self.assertEqual([True], self.opens)

        self.assertTrue(self.session.join())
        self.assertTrue(self.session._picker_open)
        self.assertEqual([True, True], self.opens)

    def test_battle_start_uses_server_map_and_local_roster_spawn_once(self):
        self.emit('welcome', {'phase': 'battle'})
        self.assertEqual('awaiting_battle_start', self.session.state)
        start = {'round_id': 7, 'map': '05_prohorovka', 'players': [
            {'id': 'other', 'x': 0, 'y': 0, 'z': 0, 'vehicle': 'germany:PzI'},
            {'id': 'p1', 'x': 7, 'y': 8, 'z': 9, 'vehicle': 'ussr:T-34'},
        ]}
        self.emit('battle_start', start)
        self.emit('battle_start', start)

        self.assertEqual('battle', self.session.state)
        self.assertEqual(1, len(self.battle_runtime.started))
        config = self.battle_runtime.started[0]['config']
        self.assertEqual('05_prohorovka', config['map'])
        self.assertEqual([7.0, 8.0, 9.0], config['spawn'])
        self.assertEqual('ussr:T-34', config['vehicle'])
        self.assertIs(start, self.battle_runtime.started[0]['message'])
        self.assertIs(self.client,
                      self.battle_runtime.started[0]['lan_client'])

    def test_active_round_snapshot_is_stored_and_forwarded(self):
        self.emit('battle_start', {
            'round_id': 1, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})
        snapshot = {'round_id': 1, 'entities': [{'id': 3}]}
        self.emit('snapshot', snapshot)
        self.assertIs(snapshot, self.session.snapshot)
        self.assertEqual([snapshot], self.snapshots)

    def test_only_active_round_bot_observation_reaches_battle_runtime(self):
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})
        current = {'type': 'bot_observation', 'round_id': 7,
                   'contacts': []}

        self.emit('bot_observation', current)
        self.emit('bot_observation', dict(current, round_id=6))

        self.assertEqual([current], self.battle_runtime.observations)

    def test_local_avatar_leave_retires_round_and_waits_for_server_reset(self):
        first = {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', first)

        self.assertTrue(
            self.battle_runtime.started[0]['on_local_leave']())

        self.assertEqual(1, self.client.leave_calls)
        self.assertEqual(0, self.client.stop_calls)
        self.assertEqual([False], self.battle_runtime.stopped)
        self.assertFalse(self.session._battle_started)
        self.assertEqual(7, self.session._departed_round_id)
        self.assertEqual('awaiting_round_end', self.session.state)

        # A duplicate start already queued for the departed round cannot put
        # the recovered Account straight back into an Avatar.
        self.emit('battle_start', first)
        self.assertEqual(1, len(self.battle_runtime.started))

        self.emit('roster', {
            'phase': 'waiting', 'round_id': 8,
            'map_pool': ['05_prohorovka']})
        self.assertEqual('waiting', self.session.state)
        self.assertIsNone(self.session._departed_round_id)

        second = {
            'round_id': 8, 'map': '05_prohorovka', 'players': [{
                'id': 'p1', 'x': 4, 'y': 5, 'z': 6,
                'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', second)
        self.assertEqual(2, len(self.battle_runtime.started))

    def test_departed_round_ignores_same_round_runtime_messages_until_waiting_reset(self):
        start = {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', start)
        self.battle_runtime.on_battle_live = mock.Mock()

        self.assertTrue(
            self.battle_runtime.started[0]['on_local_leave']())

        # LANClient applies the transport phase before notifying LANSession.
        # A current phase=battle roster still cannot reclaim the stopped Avatar.
        self.client.phase = 'battle'
        self.emit('roster', {
            'phase': 'battle', 'round_id': 7,
            'bot_authority_id': 'p2', 'players': start['players']})
        self.emit('battle_live', {'round_id': 7})
        self.emit('snapshot', {'round_id': 7, 'server_tick': 1})
        self.emit('events', {
            'round_id': 7, 'server_tick': 1, 'events': []})
        self.emit('battle_start', start)

        self.assertEqual(1, len(self.battle_runtime.started))
        self.assertEqual([], self.battle_runtime.rosters)
        self.battle_runtime.on_battle_live.assert_not_called()
        self.assertEqual([], self.battle_runtime.snapshots)
        self.assertEqual([], self.battle_runtime.events)
        self.assertFalse(self.session._battle_started)
        self.assertIsNone(self.session._active_round_id)
        self.assertEqual(7, self.session._departed_round_id)
        self.assertEqual('awaiting_round_end', self.session.state)

        self.client.phase = 'waiting'
        self.emit('roster', {
            'phase': 'waiting', 'round_id': 8,
            'map_pool': ['05_prohorovka']})

        self.assertIsNone(self.session._departed_round_id)
        self.assertEqual('waiting', self.session.state)

    def test_synchronous_runtime_failure_keeps_lan_until_server_reset(self):
        start = {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]}

        def fail_start(config, message=None, lan_client=None,
                       on_local_leave=None):
            self.battle_runtime.started.append({
                'config': dict(config), 'message': message,
                'lan_client': lan_client,
                'on_local_leave': on_local_leave})
            lan_client.on_event('battle_failed', {
                'round_id': 7, 'message': 'invalid entity property',
                'lobby_restored': True})
            # Even a buggy runtime return cannot reclaim a round whose
            # synchronous failure callback already consumed start ownership.
            return True

        self.battle_runtime.start = fail_start
        self.emit('battle_start', start)

        self.assertEqual('awaiting_round_end', self.session.state)
        self.assertFalse(self.session._battle_started)
        self.assertEqual(7, self.session._departed_round_id)
        self.assertEqual(1, self.client.leave_calls)
        self.assertEqual(0, self.client.stop_calls)
        self.assertEqual([], self.battle_runtime.stopped)
        self.assertIn('Returning to the map picker', self.statuses[-1])

        self.emit('battle_start', start)
        self.assertEqual(1, len(self.battle_runtime.started))
        self.emit('roster', {
            'phase': 'waiting', 'round_id': 8,
            'map_pool': ['05_prohorovka']})
        self.assertEqual('waiting', self.session.state)
        self.assertIsNone(self.session._departed_round_id)
        # A failed round puts the player back in the GARAGE, so the room waits
        # for another Battle click instead of raising itself over the hangar.
        self.assertFalse(self.session._picker_open)

    def test_deferred_runtime_failure_keeps_start_owner_until_recovery(self):
        start = {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]}

        def fail_start(config, message=None, lan_client=None,
                       on_local_leave=None):
            self.battle_runtime.started.append({
                'config': dict(config), 'message': message,
                'lan_client': lan_client,
                'on_local_leave': on_local_leave})
            self.battle_runtime.restore_pending = True
            return False

        self.battle_runtime.start = fail_start
        self.emit('battle_start', start)

        self.assertEqual(7, self.session._starting_round_id)
        self.assertFalse(self.session._battle_started)
        self.emit('battle_start', start)
        self.assertEqual(1, len(self.battle_runtime.started))

        self.battle_runtime.restore_pending = False
        self.emit('battle_failed', {
            'round_id': 7, 'message': 'invalid entity property',
            'lobby_restored': True})

        self.assertIsNone(self.session._starting_round_id)
        self.assertEqual('awaiting_round_end', self.session.state)
        self.assertEqual(7, self.session._departed_round_id)
        self.assertEqual(1, self.client.leave_calls)

    def test_unrestored_runtime_failure_stops_only_lan_owners(self):
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})
        self.emit('battle_failed', {
            'round_id': 7, 'message': 'lobby restore failed',
            'lobby_restored': False})

        self.assertTrue(self.session._stopped)
        self.assertEqual('stopped', self.session.state)
        self.assertEqual(1, self.client.stop_calls)
        self.assertEqual([], self.battle_runtime.stopped)
        self.assertEqual([], self.battle_runtime.restore_accounts)

    def test_failed_battle_leave_does_not_reenter_runtime_cleanup(self):
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})
        self.client.leave_battle = mock.Mock(return_value=False)

        self.emit('battle_failed', {
            'round_id': 7, 'message': 'invalid entity property',
            'lobby_restored': True})

        self.client.leave_battle.assert_called_once_with()
        self.assertTrue(self.session._stopped)
        self.assertEqual(1, self.client.stop_calls)
        self.assertEqual([], self.battle_runtime.stopped)

    def test_duplicate_and_stale_battle_failures_do_not_retire_new_round(self):
        first = {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', first)
        self.emit('battle_failed', {
            'round_id': 7, 'message': 'first round failed',
            'lobby_restored': True})

        self.assertEqual(1, self.client.leave_calls)
        self.emit('battle_failed', {
            'round_id': 7, 'message': 'duplicate failure',
            'lobby_restored': True})
        self.assertEqual(1, self.client.leave_calls)
        self.assertFalse(self.session._stopped)

        self.emit('roster', {
            'phase': 'waiting', 'round_id': 8,
            'map_pool': ['05_prohorovka']})
        second = {
            'round_id': 8, 'map': '05_prohorovka', 'players': [{
                'id': 'p1', 'x': 4, 'y': 5, 'z': 6,
                'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', second)
        self.emit('battle_failed', {
            'round_id': 7, 'message': 'late old failure',
            'lobby_restored': False})

        self.assertEqual('battle', self.session.state)
        self.assertTrue(self.session._battle_started)
        self.assertEqual(8, self.session._active_round_id)
        self.assertEqual(1, self.client.leave_calls)
        self.assertEqual(0, self.client.stop_calls)

    def test_failed_local_leave_still_cleans_runtime_and_stops_session(self):
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})
        self.client.leave_battle = lambda: False

        with self.assertRaisesRegex(
                RuntimeError, 'did not accept battle leave'):
            self.battle_runtime.started[0]['on_local_leave']()

        self.assertEqual('stopped', self.session.state)
        self.assertTrue(self.session._stopped)
        self.assertEqual([False], self.battle_runtime.stopped)
        self.assertEqual(1, self.client.stop_calls)

    def test_waiting_roster_after_result_stops_old_battle_and_allows_next_round(self):
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})
        first = {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', first)
        self.emit('snapshot', {
            'round_id': 7, 'battle_result': {'winner': 1}})

        self.emit('roster', {
            'phase': 'waiting', 'round_id': 8,
            'map_pool': ['05_prohorovka']})

        self.assertEqual([False], self.battle_runtime.stopped)
        self.assertEqual('waiting', self.session.state)
        self.assertFalse(self.session._battle_started)
        self.assertIsNone(self.session.snapshot)
        # The room stays closed over the garage until the player asks for it.
        self.assertEqual([True], self.opens)
        self.session.join()
        self.assertEqual([True, True], self.opens)

        second = {
            'round_id': 8, 'map': '05_prohorovka', 'players': [{
                'id': 'p1', 'x': 4, 'y': 5, 'z': 6,
                'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', second)
        self.assertEqual(2, len(self.battle_runtime.started))
        self.assertEqual('battle', self.session.state)

    def test_the_room_never_opens_itself_over_the_garage(self):
        """A finished round returns the player to the garage and stays there."""
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})
        opens_before = len(self.opens)

        self.emit('roster', {
            'phase': 'waiting', 'round_id': 8,
            'map_pool': ['01_karelia']})

        self.assertEqual(opens_before, len(self.opens))
        self.assertFalse(self.session._picker_open)
        self.assertTrue(self.session._picker_dismissed)

    def test_an_automatic_rejoin_does_not_present_the_room(self):
        """The watchdog reconnects silently; the player never asked for it."""
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})
        opens_before = len(self.opens)
        statuses_before = len(self.statuses)
        self.session.state = 'awaiting_round_end'
        # revive() reinstalls the Battle button; the stock header is absent.
        self.session._join_ui = _JoinUI(self.session.join)

        self.session._rejoin_room(user_requested=False)

        self.assertEqual(opens_before, len(self.opens))
        self.assertTrue(self.session._picker_dismissed)
        self.assertFalse(self.session._picker_requested)
        self.assertEqual(statuses_before, len(self.statuses))

        # A user-requested rejoin does present it again.
        self.session._rejoin_room(user_requested=True)
        self.assertFalse(self.session._picker_dismissed)
        self.assertTrue(self.session._picker_requested)

    def test_one_battle_click_after_a_round_reopens_the_room(self):
        """revive() disarms the room, so the click that asked for it has to
        arm it again.  Otherwise the player clicks Battle twice."""
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})
        self.assertTrue(self.battle_runtime.started[0]['on_local_leave']())
        self.assertEqual('awaiting_round_end', self.session.state)
        opens_before = len(self.opens)
        self.session._join_ui = _JoinUI(self.session.join)

        self.assertTrue(self.session.join())

        self.assertEqual(2, len(self.clients))
        self.assertEqual('connecting', self.session.state)
        replacement = self.clients[-1]
        replacement.ready = True
        replacement.on_event('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})

        self.assertEqual('waiting', self.session.state)
        self.assertEqual(opens_before + 1, len(self.opens))
        for status in self.statuses:
            self.assertNotIn('Rejoining', status)

    def test_next_picker_waits_for_native_lobby_recovery(self):
        ready = [True]
        pending = []
        self.session._lobby_ready = lambda: ready[0]
        self.session._callback = (
            lambda unused_delay, function: pending.append(function) or
            len(pending))
        self.session._cancel_callback = lambda unused_id: None
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})

        ready[0] = False
        self.emit('roster', {
            'phase': 'waiting', 'round_id': 8,
            'map_pool': ['05_prohorovka']})

        # Returning from a round leaves the player in the garage: the room
        # must not reopen itself, whatever the lobby recovery does.
        self.assertEqual([True], self.opens)
        self.assertFalse(self.session._picker_open)
        ready[0] = True
        for callback in list(pending):
            callback()
        self.assertEqual([True], self.opens)
        self.assertFalse(self.session._picker_open)

        # Only an explicit Battle click brings it back.
        self.session.join()
        self.assertEqual([True, True], self.opens)
        self.assertTrue(self.session._picker_open)

    def test_next_battle_start_waits_for_native_lobby_recovery(self):
        ready = [True]
        pending = {}
        next_id = [0]

        def schedule(unused_delay, function):
            next_id[0] += 1
            pending[next_id[0]] = function
            return next_id[0]

        self.session._lobby_ready = lambda: ready[0]
        self.session._callback = schedule
        self.session._cancel_callback = lambda callback_id: pending.pop(
            callback_id, None)
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})

        ready[0] = False
        self.emit('roster', {
            'phase': 'waiting', 'round_id': 8,
            'map_pool': ['05_prohorovka']})
        self.emit('battle_start', {
            'round_id': 8, 'map': '05_prohorovka', 'players': [{
                'id': 'p1', 'x': 4, 'y': 5, 'z': 6,
                'vehicle': 'ussr:T-34'}]})

        self.assertEqual(1, len(self.battle_runtime.started))
        self.assertEqual('awaiting_lobby_for_battle', self.session.state)
        self.assertIsNotNone(self.session._pending_battle_start)
        self.assertEqual(1, len(pending))

        ready[0] = True
        pending.pop(next(iter(pending)))()

        self.assertEqual(2, len(self.battle_runtime.started))
        self.assertEqual('battle', self.session.state)
        self.assertIsNone(self.session._pending_battle_start)
        self.assertIsNone(self.session._battle_start_callback_id)

    def test_late_start_denial_cannot_cancel_deferred_accepted_battle(self):
        ready = [False]
        pending = []
        self.session._lobby_ready = lambda: ready[0]
        self.session._callback = (
            lambda unused_delay, function: pending.append(function) or
            len(pending))
        self.session._cancel_callback = lambda unused_id: None
        start = {
            'round_id': 8, 'map': '05_prohorovka', 'players': [{
                'id': 'p1', 'x': 4, 'y': 5, 'z': 6,
                'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', start)

        self.emit('start_denied', {
            'round_id': 8, 'code': 'already_started'})

        self.assertEqual('awaiting_lobby_for_battle', self.session.state)
        self.assertEqual(start, self.session._pending_battle_start)
        self.assertEqual(1, len(pending))
        ready[0] = True
        pending.pop()()
        self.assertEqual(1, len(self.battle_runtime.started))
        self.assertEqual('battle', self.session.state)

    def test_new_waiting_round_discards_deferred_stale_battle(self):
        ready = [True]
        callbacks = {}
        cancelled = []
        next_id = [0]

        def schedule(unused_delay, function):
            next_id[0] += 1
            callbacks[next_id[0]] = function
            return next_id[0]

        def cancel(callback_id):
            cancelled.append(callback_id)
            callbacks.pop(callback_id, None)

        self.session._lobby_ready = lambda: ready[0]
        self.session._callback = schedule
        self.session._cancel_callback = cancel
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})

        ready[0] = False
        self.emit('roster', {
            'phase': 'waiting', 'round_id': 8,
            'map_pool': ['05_prohorovka']})
        self.emit('battle_start', {
            'round_id': 8, 'map': '05_prohorovka', 'players': [{
                'id': 'p1', 'x': 4, 'y': 5, 'z': 6,
                'vehicle': 'ussr:T-34'}]})
        stale_callback_id = self.session._battle_start_callback_id

        self.emit('roster', {
            'phase': 'waiting', 'round_id': 9,
            'map_pool': ['01_karelia']})

        self.assertIn(stale_callback_id, cancelled)
        self.assertIsNone(self.session._pending_battle_start)
        self.assertIsNone(self.session._battle_start_callback_id)
        self.assertEqual('waiting', self.session.state)
        ready[0] = True
        for callback in list(callbacks.values()):
            callback()
        self.assertEqual(1, len(self.battle_runtime.started))

    def test_stop_cancels_deferred_battle_start(self):
        ready = [False]
        pending = {}
        cancelled = []

        def schedule(unused_delay, function):
            pending[1] = function
            return 1

        def cancel(callback_id):
            cancelled.append(callback_id)
            pending.pop(callback_id, None)

        self.session._lobby_ready = lambda: ready[0]
        self.session._callback = schedule
        self.session._cancel_callback = cancel
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})
        self.assertEqual('awaiting_lobby_for_battle', self.session.state)

        self.session.stop(show_login=False)

        self.assertEqual([1], cancelled)
        self.assertEqual({}, pending)
        self.assertIsNone(self.session._pending_battle_start)

    def test_failed_round_cleanup_cannot_leave_session_half_in_battle(self):
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})
        self.battle_runtime.stop = mock.Mock(
            side_effect=RuntimeError('account restore failed'))

        with self.assertRaisesRegex(RuntimeError,
                                    'account restore failed'):
            self.emit('roster', {
                'phase': 'waiting', 'round_id': 8,
                'map_pool': ['05_prohorovka']})

        self.assertEqual('stopped', self.session.state)
        self.assertTrue(self.session._stopped)
        self.assertFalse(self.session._battle_started)
        self.assertIsNone(self.session._active_round_id)
        self.assertIsNone(self.session.snapshot)
        self.assertIsNone(self.session._picker_callback_id)
        self.assertIsNone(self.client.on_event)

    def test_battle_phase_roster_during_disconnect_keeps_active_battle(self):
        start = {'round_id': 7, 'map': '01_karelia', 'players': [{
            'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
            'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', start)

        self.emit('roster', {
            'phase': 'battle', 'round_id': 7, 'players': start['players']})

        self.assertEqual('battle', self.session.state)
        self.assertTrue(self.session._battle_started)
        self.assertEqual([], self.battle_runtime.stopped)
        self.assertEqual(1, len(self.battle_runtime.rosters))

    def test_loading_phase_roster_forwards_authority_failover(self):
        start = {
            'round_id': 7, 'map': '01_karelia',
            'bot_authority_id': 'p2',
            'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', start)
        roster = {
            'phase': 'loading', 'round_id': 7,
            'bot_authority_id': 'p1', 'players': start['players']}

        self.emit('roster', roster)

        self.assertEqual('battle', self.session.state)
        self.assertEqual([roster], self.battle_runtime.rosters)

    def test_late_start_denied_cannot_demote_active_battle(self):
        start = {'round_id': 7, 'map': '01_karelia', 'players': [{
            'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
            'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', start)

        self.emit('start_denied', {'round_id': 7, 'code': 'already_started'})

        self.assertEqual('battle', self.session.state)
        self.assertTrue(self.session._battle_started)
        self.assertEqual([], self.battle_runtime.stopped)

    def test_new_round_start_is_a_defensive_barrier_without_roster(self):
        first = {'round_id': 7, 'map': '01_karelia', 'players': [{
            'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
            'vehicle': 'ussr:T-34'}]}
        second = {'round_id': 8, 'map': '05_prohorovka', 'players': [{
            'id': 'p1', 'x': 4, 'y': 5, 'z': 6,
            'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', first)

        self.emit('battle_start', second)

        self.assertEqual([False], self.battle_runtime.stopped)
        self.assertEqual(2, len(self.battle_runtime.started))
        self.assertEqual(8, self.session._active_round_id)

    def test_stale_round_snapshot_and_events_are_not_forwarded(self):
        start = {'round_id': 7, 'map': '01_karelia', 'players': [{
            'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
            'vehicle': 'ussr:T-34'}]}
        self.emit('battle_start', start)
        current = {'round_id': 7, 'server_tick': 2}
        self.emit('snapshot', current)

        self.emit('snapshot', {'round_id': 6, 'server_tick': 99})
        self.emit('events', {'round_id': 6, 'events': [
            {'kind': 'authority', 'player_id': 2}]})

        self.assertIs(current, self.session.snapshot)
        self.assertEqual([current], self.battle_runtime.snapshots)
        self.assertEqual([], self.battle_runtime.events)

    def test_stop_is_idempotent_and_releases_every_owned_boundary_once(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.session.stop(show_login=False)
        self.session.fini(show_login=False)

        self.assertEqual('stopped', self.session.state)
        self.assertEqual(1, self.queues[0].close_calls)
        self.assertEqual(1, self.queues[0].uninstall_calls)
        self.assertEqual(1, self.client.stop_calls)
        self.assertIsNone(self.client.on_event)
        self.assertEqual([False], self.battle_runtime.stopped)
        self.assertEqual([True], self.battle_runtime.restore_accounts)

    def test_global_shutdown_skips_account_restore(self):
        self.session.stop(show_login=False, restore_account=False)

        self.assertEqual([False], self.battle_runtime.stopped)
        self.assertEqual([False], self.battle_runtime.restore_accounts)

    def test_waiting_disconnect_keeps_lobby_hook_and_can_rejoin(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})
        stale_event = self.client.on_event
        self.emit('disconnected', {'reason': 'network'})

        self.assertEqual('ready_to_join', self.session.state)
        self.assertFalse(self.session._stopped)
        self.assertIsNone(self.session.client)
        self.assertEqual(1, self.queues[0].close_calls)
        self.assertEqual(0, self.queues[0].uninstall_calls)
        self.assertEqual(1, self.client.stop_calls)
        self.assertIsNone(self.client.on_event)
        self.assertEqual([], self.battle_runtime.stopped)
        self.assertIn('Click Battle! to rejoin', self.statuses[-1])

        self.assertTrue(self.session.join())
        self.assertEqual(2, len(self.clients))
        self.assertIs(self.clients[-1], self.session.client)
        self.assertEqual(1, self.clients[-1].start_calls)
        self.assertEqual('connecting', self.session.state)

        stale_event('welcome', {
            'phase': 'waiting', 'map_pool': ['05_prohorovka'],
            'host_player_id': 'old-host'})
        self.assertIs(self.clients[-1], self.session.client)
        self.assertEqual('connecting', self.session.state)

    def test_active_battle_disconnect_still_uses_conservative_cleanup(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})

        self.emit('disconnected', {'reason': 'network'})

        self.assertEqual('stopped', self.session.state)
        self.assertTrue(self.session._stopped)
        self.assertEqual(1, self.queues[0].uninstall_calls)
        self.assertEqual(1, self.client.stop_calls)
        self.assertEqual([True], self.battle_runtime.stopped)

    def test_active_battle_transport_error_reports_round_and_reason(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.emit('battle_start', {
            'round_id': 7, 'map': '01_karelia', 'players': [{
                'id': 'p1', 'x': 1, 'y': 2, 'z': 3,
                'vehicle': 'ussr:T-34'}]})
        output = types.SimpleNamespace(write=mock.Mock())

        with mock.patch.object(self.module.sys, 'stdout', output):
            self.emit('error', {
                'message': 'server did not accept client messages for 5 '
                           'seconds'})

        output.write.assert_called_once_with(
            '[Offline LAN 0.9.22] active LAN transport failed kind=error '
            'round=7: server did not accept client messages for 5 seconds\n')
        self.assertEqual(
            'LAN battle connection lost (server did not accept client '
            'messages for 5 seconds). Returning to the garage.',
            self.statuses[-1])
        self.assertEqual('stopped', self.session.state)
        self.assertEqual([True], self.battle_runtime.stopped)

    def test_stop_retains_native_room_owner_when_close_fails(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})
        queue = self.queues[0]
        calls = []

        def fail_close():
            queue.close_calls += 1
            calls.append('close')
            raise RuntimeError('close failed')

        def fail_uninstall():
            queue.uninstall_calls += 1
            calls.append('uninstall')
            raise RuntimeError('uninstall failed')

        def fail_client_stop():
            self.client.stop_calls += 1
            calls.append('client')
            raise RuntimeError('client failed')

        def fail_battle_stop(show_login=True, restore_account=True):
            self.battle_runtime.stopped.append(show_login)
            self.battle_runtime.restore_accounts.append(restore_account)
            calls.append('battle')
            raise RuntimeError('battle failed')

        queue.close = fail_close
        queue.uninstall = fail_uninstall
        self.client.stop = fail_client_stop
        self.battle_runtime.stop = fail_battle_stop

        with self.assertRaisesRegex(RuntimeError, 'close failed'):
            self.session.stop(show_login=False)

        self.assertEqual(['close', 'client', 'battle'], calls)
        self.assertEqual('stopped', self.session.state)
        self.assertIsNone(self.client.on_event)
        self.assertEqual(1, queue.close_calls)
        self.assertEqual(0, queue.uninstall_calls)
        self.assertIs(queue, self.session._queue)
        self.assertEqual(1, self.client.stop_calls)
        self.assertEqual([False], self.battle_runtime.stopped)
        self.session.stop(show_login=False)


class _Room(object):
    guest_view = True

    def __init__(self, request_start, map_pool, status=None, on_close=None,
                 host=None, random_supported=None):
        self.request_start = request_start
        self.map_pool = map_pool
        self.status = status
        self.on_close = on_close
        self.host = host
        self.random_supported = random_supported
        self.install_calls = 0
        self.open_calls = 0
        self.close_calls = 0
        self.refresh_calls = 0
        self.uninstall_calls = 0

    def install(self):
        self.install_calls += 1

    def open(self):
        self.open_calls += 1
        return True

    def close(self):
        self.close_calls += 1
        return True

    def refresh(self):
        self.refresh_calls += 1
        return True

    def uninstall(self):
        self.uninstall_calls += 1


class LANSessionRoomTests(unittest.TestCase):
    """The self-drawn room replaces the stock window and also serves guests."""

    def setUp(self):
        self.module = _load()
        self.module.port_config.load_waiting_room_state = mock.Mock(
            return_value={
                'schema': 1, 'map': None, 'team': 0, 'team_sizes': {}})
        self.module.port_config.save_waiting_room_state = mock.Mock(
            return_value=True)
        self.clients = []
        self.queues = []
        self.rooms = []
        self.opens = []
        self.statuses = []
        self.room_error = None

        def client_factory(*args, **kwargs):
            client = _Client(*args, **kwargs)
            self.clients.append(client)
            return client

        def queue_factory(*args, **kwargs):
            queue = _Queue(*args, **kwargs)
            self.queues.append(queue)
            return queue

        def room_factory(*args, **kwargs):
            if self.room_error is not None:
                raise self.room_error
            room = _Room(*args, **kwargs)
            self.rooms.append(room)
            return room

        self.session = self.module.LANSession(
            {'host': '10.0.0.5', 'port': 28782, 'name': 'P',
             'vehicle': 'ussr:MS-1', 'startupTimeoutSeconds': 12.0},
            client_factory=client_factory, queue_factory=queue_factory,
            room_factory=room_factory,
            picker_opener=lambda: self.opens.append(True) or True,
            battle_runtime=_BattleRuntime(),
            vehicle_provider=lambda: ('ussr:R11_MS-1', 90),
            status_notifier=self.statuses.append)
        self.assertTrue(self.session.start())
        # Production only reaches start() from the Battle click, and only that
        # click may raise the room over the garage.
        self.session._picker_requested = True
        self.client = self.clients[0]

    def emit(self, kind, message):
        if kind == 'welcome':
            self.client.ready = True
            self.client.phase = message.get('phase', self.client.phase)
        if 'host_player_id' in message:
            self.client.host_player_id = message['host_player_id']
        if 'players' in message:
            self.client.roster = list(message['players'])
        self.client.on_event(kind, message)

    def test_incomplete_native_close_keeps_room_owned_for_retry(self):
        self.emit('welcome', {
            'phase': 'waiting', 'map_pool': ['01_karelia']})
        room = self.rooms[0]
        room.close = mock.Mock(side_effect=[False, True])

        self.assertFalse(self.session._close_picker())
        self.assertTrue(self.session._picker_open)
        self.assertTrue(self.session._picker_cleanup_pending)
        self.assertIs(room, self.session._queue)

        self.assertTrue(self.session._close_picker())
        self.assertFalse(self.session._picker_open)
        self.assertFalse(self.session._picker_cleanup_pending)

    def test_the_host_gets_the_room_instead_of_the_stock_window(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia'],
                              'players': [{'id': 'p1', 'name': 'Host'}]})

        self.assertEqual([], self.queues)
        self.assertEqual([], self.opens)
        self.assertEqual(1, len(self.rooms))
        self.assertEqual(1, self.rooms[0].install_calls)
        self.assertEqual(1, self.rooms[0].open_calls)
        self.assertEqual(['01_karelia'], self.rooms[0].map_pool())
        self.assertTrue(self.rooms[0].host())

    def test_the_room_status_names_the_server_and_the_players(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia'],
                              'players': [{'id': 'p1', 'name': 'Host'},
                                          {'id': 'p2', 'name': 'Guest'}]})

        self.assertEqual(
            'LAN SERVER: 10.0.0.5:28782\nPLAYERS (2): Host, Guest',
            self.rooms[0].status())

    def test_a_guest_also_sees_the_room_and_the_notification(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia'],
                              'host_player_id': 'other',
                              'players': [{'id': 'other', 'name': 'Host'},
                                          {'id': 'p1', 'name': 'Me'}]})

        self.assertEqual(1, len(self.rooms))
        self.assertEqual(1, self.rooms[0].open_calls)
        self.assertFalse(self.rooms[0].host())
        self.assertIn('WAITING FOR Host TO START THE BATTLE',
                      self.rooms[0].status())
        self.assertIn('Waiting for host', self.statuses[-1])

    def test_the_room_start_reaches_the_server(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.assertTrue(self.rooms[0].request_start('01_karelia'))
        self.assertEqual(['01_karelia'], self.client.requests)

    def test_a_closed_room_reopens_from_the_battle_button(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.session._on_picker_closed()
        self.assertTrue(self.session._picker_dismissed)

        self.session.join()

        self.assertEqual(2, self.rooms[0].open_calls)

    def test_leaving_the_room_returns_to_the_garage(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})
        room = self.rooms[0]

        room.on_close()

        self.assertEqual('ready_to_join', self.session.state)
        self.assertIsNone(self.session.client)
        self.assertEqual(1, self.client.stop_calls)
        self.assertIn('left the LAN room', self.statuses[-1])

    def test_the_battle_button_joins_again_after_leaving(self):
        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})
        self.rooms[0].on_close()

        self.session.join()

        self.assertEqual(2, len(self.clients))
        self.assertEqual('connecting', self.session.state)

    def test_a_client_without_the_native_gui_uses_the_stock_window(self):
        self.room_error = ImportError('No module named GUI')

        self.emit('welcome', {'phase': 'waiting', 'map_pool': ['01_karelia']})

        self.assertEqual([], self.rooms)
        self.assertEqual(1, len(self.queues))
        self.assertEqual([True], self.opens)
