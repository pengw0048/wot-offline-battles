import json
from pathlib import Path
import socket
import socketserver
import sys
import threading
import unittest
from unittest import mock


PORT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORT_ROOT / 'server'))

from lan_battle_server import (  # noqa: E402
    MAX_TICK_FAILURE_DIAGNOSTIC_CHARS,
    TICK_HZ,
    BattleState,
    Player,
    ThreadedTCPServer,
    _TCPShutdownController,
    _run_tick_loop,
)


class _CaptureConnection(object):
    def __init__(self):
        self.payloads = []

    def sendall(self, payload):
        self.payloads.append(payload)

    def messages(self):
        return [json.loads(payload.decode('utf-8'))
                for payload in self.payloads]


class _SyntheticClock(object):
    def __init__(self):
        self.value = 0.0

    def now(self):
        return self.value

    def sleep(self, delay):
        self.value += delay


class TickLoopSupervisionTest(unittest.TestCase):
    def test_one_failure_publishes_terminal_then_resets_normally(self):
        state = BattleState(map_name='01_karelia')
        connection = _CaptureConnection()
        state.players[1] = Player(
            1, connection, ('127.0.0.1', 10001),
            account_key='tick-supervision')
        state.host_player_id = 1
        state.phase = 'battle'

        original_tick = state.tick_once
        attempts = []

        def injected_tick(dt):
            attempts.append(dt)
            if len(attempts) == 1:
                raise RuntimeError('synthetic\n' + ('x' * 2048))
            original_tick(dt)
            state.running = False

        state.tick_once = injected_tick
        clock = _SyntheticClock()
        shutdown = mock.Mock()
        tick_thread = threading.Thread(
            target=_run_tick_loop,
            args=(state,),
            kwargs={
                'tick_clock': clock.now,
                'sleeper': clock.sleep,
                'shutdown_callback': shutdown,
            },
            daemon=True)
        with mock.patch('lan_battle_server._server_log'):
            tick_thread.start()
            tick_thread.join(1.0)

        self.assertFalse(tick_thread.is_alive())
        self.assertEqual(2, len(attempts))
        shutdown.assert_not_called()
        self.assertEqual('server_tick_failure',
                         state.battle_result['reason'])
        self.assertEqual({}, state.result_receipts)
        self.assertEqual(1, state.server_tick_failure_count)
        self.assertLessEqual(
            len(state.last_server_tick_failure),
            MAX_TICK_FAILURE_DIAGNOSTIC_CHARS)
        self.assertNotIn('\n', state.last_server_tick_failure)

        messages = connection.messages()
        terminal_events = [
            event for message in messages if message.get('type') == 'events'
            for event in message.get('events', ())
            if event.get('kind') == 'battle_result']
        self.assertEqual(1, len(terminal_events))
        self.assertEqual('server_tick_failure',
                         terminal_events[0]['reason'])
        snapshots = [message for message in messages
                     if message.get('type') == 'snapshot']
        self.assertTrue(snapshots)
        self.assertEqual(
            'server_tick_failure',
            snapshots[-1]['battle_result']['reason'])

        failed_round = state.round_id
        state.tick = state.result_reset_tick - 1
        original_tick(1.0 / TICK_HZ)
        self.assertEqual('waiting', state.phase)
        self.assertEqual(failed_round + 1, state.round_id)
        self.assertIsNone(state.battle_result)

    def test_two_consecutive_failures_request_shutdown_once(self):
        class State(object):
            running = True

            def __init__(self):
                self.attempts = 0

            def tick_once(self, unused_dt):
                self.attempts += 1
                raise RuntimeError('persistent failure')

        state = State()
        clock = _SyntheticClock()
        failure_handler = mock.Mock()
        shutdown = mock.Mock()
        with mock.patch('lan_battle_server._server_log'):
            _run_tick_loop(
                state, tick_clock=clock.now, sleeper=clock.sleep,
                failure_handler=failure_handler,
                shutdown_callback=shutdown)

        self.assertEqual(2, state.attempts)
        failure_handler.assert_called_once()
        shutdown.assert_called_once_with()
        self.assertFalse(state.running)

    def test_failure_handler_exception_stops_server(self):
        class State(object):
            running = True
            attempts = 0

            def tick_once(self, unused_dt):
                self.attempts += 1
                raise RuntimeError('tick failure')

        state = State()
        clock = _SyntheticClock()
        shutdown = mock.Mock()
        with mock.patch('lan_battle_server._server_log'):
            _run_tick_loop(
                state, tick_clock=clock.now, sleeper=clock.sleep,
                failure_handler=mock.Mock(
                    side_effect=RuntimeError('handler failure')),
                shutdown_callback=shutdown)

        self.assertEqual(1, state.attempts)
        shutdown.assert_called_once_with()
        self.assertFalse(state.running)

    def test_waiting_tick_remains_a_noop(self):
        state = BattleState(map_name='01_karelia')
        before = (state.round_id, state.tick, state.state_revision)

        state.tick_once(1.0 / TICK_HZ)

        self.assertEqual(before,
                         (state.round_id, state.tick,
                          state.state_revision))
        self.assertIsNone(state.battle_result)
        self.assertEqual(0, state.server_tick_failure_count)

    def test_fixed_step_catch_up_is_preserved(self):
        clock = [0.0]

        class State(object):
            running = True

            def __init__(self):
                self.steps = []

            def tick_once(self, dt):
                self.steps.append(dt)

        state = State()
        sleeps = []

        def sleep(delay):
            sleeps.append(delay)
            if len(sleeps) == 1:
                clock[0] = 1.0
            else:
                state.running = False

        _run_tick_loop(state, tick_clock=lambda: clock[0], sleeper=sleep)

        self.assertEqual(30, len(state.steps))
        self.assertAlmostEqual(1.0, sum(state.steps))
        self.assertTrue(all(
            abs(step - 1.0 / TICK_HZ) < 1e-12
            for step in state.steps))


class _WelcomeHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.sendall(b'welcome\n')


class TCPShutdownSupervisionTest(unittest.TestCase):
    def test_shutdown_request_before_serve_forever_never_blocks(self):
        server = ThreadedTCPServer(('127.0.0.1', 0), _WelcomeHandler)
        self.addCleanup(server.server_close)
        controller = _TCPShutdownController(server)
        finished = threading.Event()

        def request():
            controller.request()
            finished.set()

        thread = threading.Thread(target=request, daemon=True)
        thread.start()
        self.assertTrue(finished.wait(1.0))
        thread.join(1.0)
        self.assertFalse(thread.is_alive())

        serve_thread = threading.Thread(
            target=controller.serve_forever, daemon=True)
        serve_thread.start()
        serve_thread.join(1.0)
        self.assertFalse(serve_thread.is_alive())

    def test_persistent_tick_failure_closes_real_tcp_listener(self):
        server = ThreadedTCPServer(('127.0.0.1', 0), _WelcomeHandler)
        controller = _TCPShutdownController(server)
        address = server.server_address
        serve_done = threading.Event()

        def serve():
            try:
                controller.serve_forever(poll_interval=0.01)
            finally:
                server.server_close()
                serve_done.set()

        serve_thread = threading.Thread(target=serve, daemon=True)
        serve_thread.start()
        connection = socket.create_connection(address, timeout=1.0)
        try:
            connection.settimeout(1.0)
            self.assertEqual(b'welcome\n', connection.recv(32))
        finally:
            connection.close()

        class State(object):
            running = True

            def tick_once(self, unused_dt):
                raise RuntimeError('persistent failure')

        state = State()
        shutdown_calls = []

        def shutdown():
            shutdown_calls.append(True)
            controller.request()

        tick_thread = threading.Thread(
            target=_run_tick_loop,
            args=(state,),
            kwargs={
                'failure_handler': lambda unused_error: None,
                'shutdown_callback': shutdown,
            },
            daemon=True)
        with mock.patch('lan_battle_server._server_log'):
            tick_thread.start()
            tick_thread.join(2.0)

        self.assertFalse(tick_thread.is_alive())
        self.assertTrue(serve_done.wait(2.0))
        serve_thread.join(1.0)
        controller.wait(1.0)
        self.assertFalse(serve_thread.is_alive())
        self.assertEqual([True], shutdown_calls)
        self.assertFalse(state.running)
        with self.assertRaises(OSError):
            socket.create_connection(address, timeout=0.25)


if __name__ == '__main__':
    unittest.main()
