import struct
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(
    Path(__file__).resolve().parents[1] / 'src' / 'res' / 'scripts' / 'client'))
from gui.mods.offline_lan_0922 import driver_transport as transport


TOKEN = 'test-driver-session-token'


def available_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        threading.Event().wait(0.002)
    raise AssertionError('Expected transport state transition did not occur')


def wire(message, key=None):
    return transport._encode({'schema': transport.SCHEMA, 'kind': 'message',
                              'replace_key': key, 'payload': message})


class DriverTransportTests(unittest.TestCase):
    def setUp(self):
        self.resources = []

    def tearDown(self):
        for value in reversed(self.resources):
            if isinstance(value, transport.Bridge):
                value.stop()
                value.stop()
                if value._thread is not None:
                    self.assertFalse(value._thread.is_alive())
            else:
                value.close()

    def listener(self):
        bridge = transport.listen('127.0.0.1', available_port(), TOKEN)
        self.resources.append(bridge)
        self.assertTrue(bridge.listening)
        self.assertFalse(bridge.connected)
        self.assertIsNone(bridge.error)
        return bridge

    def pair(self):
        driver = self.listener()
        visible = transport.connect('127.0.0.1', driver.port, TOKEN)
        self.resources.append(visible)
        wait_for(lambda: driver.connected and visible.connected)
        return driver, visible

    def raw_peer(self, driver, hello=None, suffix=b''):
        peer = socket.create_connection(('127.0.0.1', driver.port), 1.0)
        peer.settimeout(1.0)
        self.resources.append(peer)
        if hello is None:
            hello = {'schema': transport.SCHEMA, 'kind': 'hello', 'token': TOKEN}
        peer.sendall(transport._encode(hello) + suffix)
        return peer

    def admit_without_thread(self):
        with mock.patch.object(transport.threading, 'Thread'):
            bridge = transport.connect('127.0.0.1', available_port(), TOKEN)
        bridge._thread = None
        bridge._connected = True
        self.resources.append(bridge)
        return bridge

    def test_missing_native_codec_fails_before_listening_or_thread_start(self):
        with mock.patch.object(transport.driver_codec, 'check_available',
                               side_effect=ImportError('missing codec')), \
                mock.patch.object(transport.threading, 'Thread') as thread:
            driver = self.listener_unavailable()
        self.assertEqual('codec_unavailable', driver.error)
        self.assertFalse(driver.listening)
        self.assertFalse(driver.connected)
        thread.assert_not_called()

    def listener_unavailable(self):
        bridge = transport.listen('127.0.0.1', available_port(), TOKEN)
        self.resources.append(bridge)
        return bridge

    def test_real_bidirectional_handshake_and_immutable_caller_payload(self):
        driver, visible = self.pair()
        payload = {'type': 'control', 'round_id': 4, 'generation': 8,
                   'axes': [1, 0], 'text': '\u5766\u514b'}
        with mock.patch.object(transport, '_encode', wraps=transport._encode) as encode:
            self.assertTrue(visible.send(payload))
            self.assertEqual(1, encode.call_count)
        payload['axes'][0] = 99
        payload['round_id'] = -1
        received = wait_for(driver.poll)
        self.assertEqual(4, received[0]['round_id'])
        self.assertEqual([1, 0], received[0]['axes'])
        self.assertEqual('\u5766\u514b', received[0]['text'])
        self.assertTrue(driver.send({'type': 'state', 'pose': [2, 3, 4]}))
        self.assertEqual([{'type': 'state', 'pose': [2, 3, 4]}], wait_for(visible.poll))

    def test_io_decodes_but_does_not_invoke_main_thread_consumers(self):
        seen_threads = []
        decode = transport._decode

        def record(line):
            seen_threads.append(threading.current_thread().name)
            return decode(line)

        with mock.patch.object(transport, '_decode', side_effect=record):
            driver, visible = self.pair()
            self.assertTrue(visible.send({'event': 'ready'}))
            wait_for(lambda: bool(driver._incoming))
        self.assertTrue(seen_threads)
        self.assertEqual({'WoTPlayerDriverIO'}, set(seen_threads))
        self.assertEqual([{'event': 'ready'}], driver.poll())
        self.assertEqual([], driver.poll())

    def test_token_and_schema_mismatch_never_authenticate(self):
        for hello in ({'schema': transport.SCHEMA, 'kind': 'hello', 'token': 'wrong-secret'},
                      {'schema': transport.SCHEMA + 1, 'kind': 'hello', 'token': TOKEN},
                      {'schema': True, 'kind': 'hello', 'token': TOKEN}):
            with self.subTest(hello=hello):
                driver = self.listener()
                self.raw_peer(driver, hello)
                wait_for(lambda: driver.error)
                self.assertFalse(driver.connected)
                self.assertEqual([], driver.poll())
                self.assertNotIn(TOKEN, driver.error)
                self.assertNotIn('wrong-secret', driver.error)

    def test_fragmented_frames_and_handshake_remainder_deliver_exact_order(self):
        driver = self.listener()
        peer = self.raw_peer(driver, suffix=wire({'event': 1})[:7])
        self.assertEqual('ready', transport._decode(peer.recv(4096)[4:])['kind'])
        wait_for(lambda: driver.connected)
        rest = wire({'event': 1})[7:] + wire({'event': 2}) + wire({'event': 3})
        for start in range(0, len(rest), 11):
            peer.sendall(rest[start:start + 11])
        wait_for(lambda: len(driver._incoming) == 3)
        self.assertEqual([{'event': 1}, {'event': 2}, {'event': 3}], driver.poll())

    def test_outgoing_coalescing_keeps_barriers_and_partly_sent_frames(self):
        bridge = self.admit_without_thread()
        for value, key in [(1, 'pose'), (2, 'pose'), ('event', None),
                           (3, 'pose'), (4, 'pose')]:
            self.assertTrue(bridge.send({'value': value}, key))
        self.assertEqual([2, 'event', 4], [transport._decode(row[1][4:])['payload']['value']
                                        for row in bridge._outgoing])
        bridge._sending = bridge._outgoing[0]
        self.assertTrue(bridge.send({'value': 5}, 'pose'))
        self.assertEqual([2, 'event', 5], [transport._decode(row[1][4:])['payload']['value']
                                        for row in bridge._outgoing])
        fresh = self.admit_without_thread()
        self.assertTrue(fresh.send({'value': 1}, 'pose'))
        fresh._sending = fresh._outgoing[0]
        self.assertTrue(fresh.send({'value': 2}, 'pose'))
        self.assertEqual([1, 2], [transport._decode(row[1][4:])['payload']['value']
                                 for row in fresh._outgoing])

    def test_incoming_coalescing_never_crosses_event_or_round_barriers(self):
        driver = self.listener()
        peer = self.raw_peer(driver)
        self.assertEqual('ready', transport._decode(peer.recv(4096)[4:])['kind'])
        wait_for(lambda: driver.connected)
        rows = [(1, 'pose'), (2, 'pose'), ('round-end', None),
                ('round-start', None), (3, 'pose'), (4, 'pose'), ('ack', None)]
        peer.sendall(b''.join(wire({'value': value}, key) for value, key in rows))
        wait_for(lambda: len(driver._incoming) == 5)
        self.assertEqual([2, 'round-end', 'round-start', 4, 'ack'],
                         [row['value'] for row in driver.poll()])

    def test_partial_socket_writes_keep_one_frozen_frame_and_fifo(self):
        bridge = self.admit_without_thread()
        bridge.send({'event': 1})
        bridge.send({'event': 2})
        expected = b''.join(row[1] for row in bridge._outgoing)

        class PartialSocket:
            def __init__(self):
                self.written = b''

            def send(self, data):
                self.written += data[:3]
                return min(len(data), 3)

        sock = PartialSocket()

        def ready(unused_read, write, unused_error, unused_timeout):
            if not write:
                bridge._stopped.set()
            return [], write, []

        with mock.patch.object(transport.select, 'select', side_effect=ready):
            bridge._transfer(sock, b'')
        self.assertEqual(expected, sock.written)
        self.assertEqual(0, bridge._outgoing_bytes)
        self.assertEqual([], bridge._outgoing)

    def test_outbound_count_and_byte_overflow_fail_locally_without_admission(self):
        for limit, value in [('MAX_QUEUED_MESSAGES', 1), ('MAX_QUEUED_BYTES', 100)]:
            with self.subTest(limit=limit):
                bridge = self.admit_without_thread()
                self.assertTrue(bridge.send({'event': 1}))
                before = list(bridge._outgoing)
                with mock.patch.object(transport, limit, value):
                    self.assertFalse(bridge.send({'event': 2}))
                self.assertEqual('outbound_overflow', bridge.error)
                self.assertFalse(bridge.connected)
                self.assertEqual(before, bridge._outgoing)

    def test_inbound_overflow_exposes_error_and_preserves_admitted_events(self):
        driver = self.listener()
        peer = self.raw_peer(driver)
        peer.recv(4096)
        wait_for(lambda: driver.connected)
        with mock.patch.object(transport, 'MAX_QUEUED_MESSAGES', 2):
            peer.sendall(wire({'event': 1}) + wire({'event': 2}) + wire({'event': 3}))
            wait_for(lambda: driver.error)
        self.assertEqual('inbound_overflow', driver.error)
        self.assertEqual([{'event': 1}, {'event': 2}], driver.poll())

    def test_inbound_byte_budget_and_replacement_budget_use_wire_bytes(self):
        bridge = self.admit_without_thread()
        first = transport._decode(wire({'value': 1}, 'pose')[4:])
        second = transport._decode(wire({'value': 2}, 'pose')[4:])
        barrier = transport._decode(wire({'event': 1})[4:])
        with mock.patch.object(transport, 'MAX_QUEUED_BYTES', 100):
            self.assertTrue(bridge._admit_incoming(first, 80))
            self.assertTrue(bridge._admit_incoming(second, 90))
            self.assertEqual(90, bridge._incoming_bytes)
            self.assertFalse(bridge._admit_incoming(barrier, 20))
        self.assertEqual('inbound_overflow', bridge.error)
        self.assertEqual([{'value': 2}], bridge.poll())

    def test_large_bootstrap_above_256k_is_allowed_and_oversized_frame_is_not(self):
        driver, visible = self.pair()
        self.assertTrue(visible.send({'bootstrap': 'x' * 300000}))
        self.assertEqual(300000, len(wait_for(driver.poll)[0]['bootstrap']))
        self.assertFalse(visible.send({'bootstrap': 'x' * transport.MAX_FRAME_BYTES}))
        self.assertEqual('invalid_message', visible.error)

    def test_incomplete_and_malformed_frames_are_not_delivered(self):
        bad = b'WDP2d' + struct.pack('<d', float('nan'))
        cases = [(struct.pack('!I', 20) + b'WDP2', 'truncated_frame'),
                 (struct.pack('!I', 5) + b'XXXXX', 'invalid_frame'),
                 (struct.pack('!I', len(bad)) + bad, 'invalid_frame')]
        for payload, expected in cases:
            with self.subTest(payload=payload):
                driver = self.listener()
                peer = self.raw_peer(driver)
                peer.recv(4096)
                wait_for(lambda: driver.connected)
                peer.sendall(payload)
                peer.shutdown(socket.SHUT_WR)
                wait_for(lambda: driver.error)
                self.assertEqual(expected, driver.error)
                self.assertEqual([], driver.poll())

    def test_oversized_unterminated_input_is_bounded(self):
        driver = self.listener()
        peer = self.raw_peer(driver)
        peer.recv(4096)
        wait_for(lambda: driver.connected)
        with mock.patch.object(transport, 'MAX_FRAME_BYTES', 64):
            peer.sendall(struct.pack('!I', 65))
            wait_for(lambda: driver.error)
        self.assertEqual('invalid_frame', driver.error)
        self.assertEqual([], driver.poll())

    def test_eof_keeps_completed_events_and_closes_both_directions(self):
        driver = self.listener()
        peer = self.raw_peer(driver)
        peer.recv(4096)
        wait_for(lambda: driver.connected)
        peer.sendall(wire({'event': 'terminal'}))
        peer.shutdown(socket.SHUT_WR)
        wait_for(lambda: driver.error)
        self.assertEqual('peer_closed', driver.error)
        self.assertFalse(driver.connected)
        self.assertFalse(driver.send({'late': True}))
        self.assertEqual([{'event': 'terminal'}], driver.poll())

    def test_partial_startup_stop_and_failed_connect_are_idempotent(self):
        driver = self.listener()
        self.assertFalse(driver.send({'too_early': True}))
        self.assertIsNone(driver.error)
        driver.stop()
        driver.stop()
        self.assertFalse(driver.listening)
        self.assertFalse(driver.connected)
        self.assertIsNone(driver.error)
        visible = transport.connect('127.0.0.1', available_port(), TOKEN)
        self.resources.append(visible)
        wait_for(lambda: visible.error)
        self.assertEqual('connect_failed', visible.error)

    def test_handshake_timeout_and_stop_during_handshake_are_bounded(self):
        with mock.patch.object(transport, 'HANDSHAKE_SECONDS', 0.025):
            driver = self.listener()
            peer = socket.create_connection(('127.0.0.1', driver.port), 1.0)
            self.resources.append(peer)
            wait_for(lambda: driver.error)
        self.assertEqual('handshake_timeout', driver.error)
        second = self.listener()
        peer = socket.create_connection(('127.0.0.1', second.port), 1.0)
        self.resources.append(peer)
        wait_for(lambda: not second.listening)
        second.stop()
        self.assertFalse(second._thread.is_alive())
        self.assertIsNone(second.error)

    def test_external_host_invalid_port_and_duplicate_bind_do_not_start(self):
        for host, port in [('0.0.0.0', 3000), ('localhost', 3000),
                           ('127.0.0.1', 0), ('127.0.0.1', True)]:
            bridge = transport.listen(host, port, TOKEN)
            self.resources.append(bridge)
            self.assertEqual('invalid_endpoint', bridge.error)
            self.assertFalse(bridge.listening)
        driver = self.listener()
        second = transport.listen('127.0.0.1', driver.port, TOKEN)
        self.resources.append(second)
        self.assertEqual('listen_failed', second.error)

    def test_bad_outgoing_data_fails_without_token_or_payload_in_reason(self):
        for message in ({'x': float('nan')}, {'x': object()}, ['not-dict']):
            bridge = self.admit_without_thread()
            self.assertFalse(bridge.send(message))
            self.assertEqual('invalid_message', bridge.error)
            self.assertEqual([], bridge._outgoing)


if __name__ == '__main__':
    unittest.main()
