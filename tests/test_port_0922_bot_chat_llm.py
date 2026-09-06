import json
import os
from pathlib import Path
import random
import stat
import sys
import tempfile
import threading
import time
import unittest


TEST_ROOT = Path(__file__).resolve().parent
SERVER_ROOT = TEST_ROOT.parent / 'server'
sys.path.insert(0, str(TEST_ROOT))
sys.path.insert(0, str(SERVER_ROOT))

import bot_chat  # noqa: E402
import bot_chat_llm  # noqa: E402
import bot_chat_models as catalog  # noqa: E402
from bot_chat import BotChatDirector  # noqa: E402
from bot_chat_llm import (  # noqa: E402
    LlamaChatBackend, LlamaServerSupervisor, build_messages, extract_content,
    free_loopback_port, inference_threads, sanitize_line,
)


_FAKE_RUNTIME = '''#!/usr/bin/env python3
"""Answer the two endpoints the backend actually calls."""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[sys.argv.index('--port') + 1])
REPLY = '收到, 这就回来'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *unused):
        pass

    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        self.rfile.read(length)
        body = json.dumps({
            'choices': [{'message': {'role': 'assistant', 'content': REPLY}}],
        }).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


HTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
'''


def _write_fake_runtime(directory):
    """Write an executable stand-in that speaks the real wire protocol."""
    script = Path(directory) / 'fake_llama_server.py'
    script.write_text(_FAKE_RUNTIME, encoding='utf-8')
    launcher = Path(directory) / 'llama-server'
    launcher.write_text(
        '#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, script),
        encoding='utf-8')
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP)
    model = Path(directory) / 'model.gguf'
    model.write_bytes(b'GGUF stand-in')
    return str(launcher), str(model)


def _await(predicate, timeout=20.0):
    """Wait for a real state transition rather than assuming a duration."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    return None


def _request(request_id=1, trigger=bot_chat.TRIGGER_REPLY,
             persona=bot_chat.PERSONA_SLACKER, recent=(), prefix=None):
    return {
        'request_id': request_id,
        'trigger': trigger,
        'persona': persona,
        'address_kind': bot_chat.ADDRESS_VEHICLE,
        'address_prefix': prefix,
        'speaker': {'name': 'Peng'},
        'bot': {'id': 1, 'name': '今天不加班', 'vehicle': 'ussr:R04_T-34',
                'hp': 120, 'max_hp': 400},
        'recent_texts': [record['text'] for record in recent],
        'recent': list(recent),
        'rng': random.Random(1),
    }


class CatalogTest(unittest.TestCase):
    def test_both_mirrors_serve_the_same_file_name(self):
        for entry in catalog.MODEL_TIERS:
            modelscope = catalog.model_url(entry['key'], catalog.MODELSCOPE)
            huggingface = catalog.model_url(entry['key'], catalog.HUGGINGFACE)
            self.assertIn(entry['repo'], modelscope)
            self.assertIn(entry['file'], modelscope)
            self.assertIn(entry['repo'], huggingface)
            self.assertIn(entry['file'], huggingface)
            self.assertTrue(modelscope.startswith('https://www.modelscope.cn/'))
            self.assertTrue(huggingface.startswith('https://huggingface.co/'))

    def test_every_tier_declares_an_integrity_digest_and_licence(self):
        for entry in catalog.MODEL_TIERS:
            self.assertEqual(64, len(entry['sha256']))
            self.assertGreater(entry['size'], 0)
            self.assertEqual('Apache-2.0', entry['license'])

    def test_an_unknown_tier_or_source_has_no_url(self):
        self.assertIsNone(catalog.model_url('gigantic', catalog.MODELSCOPE))
        self.assertIsNone(catalog.model_url('small', 'some-other-mirror'))

    def test_the_default_tier_is_the_small_one(self):
        self.assertEqual('small', catalog.default_tier()['key'])

    def test_runtime_architecture_is_named_honestly(self):
        self.assertEqual('x64', catalog.runtime_arch('AMD64'))
        self.assertEqual('arm64', catalog.runtime_arch('ARM64'))
        self.assertEqual('arm64', catalog.runtime_arch('aarch64'))
        # A 32-bit host has no published CPU build, and saying so beats
        # downloading an executable that cannot run.
        self.assertIsNone(catalog.runtime_arch('x86'))
        self.assertIsNone(catalog.runtime_url(None))

    def test_the_runtime_build_is_pinned(self):
        url = catalog.runtime_url('x64')
        self.assertIn(catalog.RUNTIME_BUILD, url)
        self.assertNotIn('latest', url)


class SanitizeTest(unittest.TestCase):
    def test_a_plain_line_survives(self):
        self.assertEqual('收到, 这就回来', sanitize_line('收到, 这就回来'))

    def test_only_the_first_line_is_a_chat_message(self):
        self.assertEqual('收到', sanitize_line('收到\n然后我去B3\n再说'))

    def test_a_leading_callsign_is_removed(self):
        self.assertEqual('这就来', sanitize_line('今天不加班：这就来'))
        self.assertEqual('这就来', sanitize_line('Bot: 这就来'))

    def test_a_sentence_containing_a_colon_is_not_truncated(self):
        # Only a short leading run reads as a callsign.  NFKC folds the
        # full width colon, exactly as the stock outgoing filter does.
        self.assertEqual(
            '我在B3, 情况是这样的, 你听我说:他们从右边来了',
            sanitize_line('我在B3, 情况是这样的, 你听我说：他们从右边来了'))

    def test_wrapping_quotes_are_removed(self):
        self.assertEqual('好的', sanitize_line('“好的”'))
        self.assertEqual('好的', sanitize_line('"好的"'))

    def test_a_reasoning_block_is_stripped(self):
        self.assertEqual(
            '好', sanitize_line('<think>玩家在叫我回防</think>好'))

    def test_chat_markers_are_removed(self):
        self.assertEqual('收到', sanitize_line('收到<|im_end|>'))

    def test_an_over_long_line_is_cut_to_the_stock_limit(self):
        text = sanitize_line('好' * 400)
        self.assertEqual(bot_chat.MAX_CHAT_UTF16_UNITS,
                         len(text.encode('utf-16-le')) // 2)

    def test_nothing_publishable_is_refused(self):
        self.assertIsNone(sanitize_line(''))
        self.assertIsNone(sanitize_line('   '))
        self.assertIsNone(sanitize_line(None))
        self.assertIsNone(sanitize_line('<think>只有思考</think>'))


class ExtractTest(unittest.TestCase):
    def test_a_well_formed_completion_yields_its_content(self):
        self.assertEqual('好', extract_content(
            {'choices': [{'message': {'content': '好'}}]}))

    def test_every_malformed_shape_yields_nothing(self):
        for body in (None, {}, {'choices': []}, {'choices': [{}]},
                     {'choices': [{'message': {}}]},
                     {'choices': [{'message': {'content': 5}}]},
                     {'choices': 'text'}):
            self.assertIsNone(extract_content(body), body)


class PromptTest(unittest.TestCase):
    def test_the_persona_and_identity_reach_the_system_turn(self):
        messages = build_messages(_request())
        system = messages[0]['content']
        self.assertEqual('system', messages[0]['role'])
        self.assertIn('今天不加班', system)
        self.assertIn('T-34', system)
        self.assertIn(bot_chat_llm.PERSONA_STYLE[bot_chat.PERSONA_SLACKER],
                      system)

    def test_the_transcript_and_task_reach_the_user_turn(self):
        recent = ({'name': 'Peng', 'text': '那个t34 回来一下'},)
        messages = build_messages(_request(recent=recent, prefix='Peng'))
        user = messages[1]['content']
        self.assertIn('那个t34 回来一下', user)
        self.assertIn('Peng', user)
        self.assertIn('120/400', user)
        self.assertIn(bot_chat_llm.TRIGGER_TASK[bot_chat.TRIGGER_REPLY], user)

    def test_a_hop_is_told_not_to_repeat_its_teammate(self):
        messages = build_messages(_request(trigger=bot_chat.TRIGGER_HOP))
        self.assertIn('不要重复', messages[1]['content'])


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def _opener(self, text='收到, 这就回来'):
        def opener(url, payload):
            self.calls.append((url, payload))
            return {'choices': [{'message': {'content': text}}]}
        return opener

    def test_a_prefetched_line_becomes_available(self):
        backend = LlamaChatBackend('http://127.0.0.1:1', opener=self._opener())
        backend.start()
        self.addCleanup(backend.stop)
        request = _request()
        backend.prefetch(request)
        self.assertTrue(_await(lambda: backend.compose(request)))

    def test_a_line_is_delivered_once(self):
        backend = LlamaChatBackend('http://127.0.0.1:1', opener=self._opener())
        backend.start()
        self.addCleanup(backend.stop)
        request = _request()
        backend.prefetch(request)
        self.assertTrue(_await(lambda: backend.compose(request)))
        self.assertIsNone(backend.compose(request))

    def test_an_unfetched_line_composes_to_nothing(self):
        backend = LlamaChatBackend('http://127.0.0.1:1', opener=self._opener())
        self.assertIsNone(backend.compose(_request(request_id=99)))

    def test_compose_never_waits_for_the_generator(self):
        release = threading.Event()

        def slow(url, payload):
            release.wait(10.0)
            return {'choices': [{'message': {'content': '慢'}}]}

        backend = LlamaChatBackend('http://127.0.0.1:1', opener=slow)
        backend.start()
        self.addCleanup(backend.stop)
        self.addCleanup(release.set)
        request = _request()
        backend.prefetch(request)
        started = time.monotonic()
        self.assertIsNone(backend.compose(request))
        self.assertLess(time.monotonic() - started, 1.0)

    def test_a_transport_failure_is_contained(self):
        def broken(url, payload):
            raise OSError('connection refused')

        backend = LlamaChatBackend('http://127.0.0.1:1', opener=broken)
        backend.start()
        self.addCleanup(backend.stop)
        request = _request()
        backend.prefetch(request)
        self.assertIsNone(_await(lambda: backend.compose(request),
                                 timeout=1.5))
        # The worker survives to answer the next line.
        backend._opener = self._opener()
        follow_up = _request(request_id=2)
        backend.prefetch(follow_up)
        self.assertTrue(_await(lambda: backend.compose(follow_up)))

    def test_unpublishable_output_produces_no_result(self):
        backend = LlamaChatBackend('http://127.0.0.1:1',
                                   opener=self._opener('   '))
        backend.start()
        self.addCleanup(backend.stop)
        request = _request()
        backend.prefetch(request)
        self.assertIsNone(_await(lambda: backend.compose(request),
                                 timeout=1.5))

    def test_the_same_line_is_not_queued_twice(self):
        backend = LlamaChatBackend('http://127.0.0.1:1', opener=self._opener())
        request = _request()
        backend.prefetch(request)
        backend.prefetch(request)
        self.assertEqual(1, len(backend._queue))

    def test_an_overrun_queue_drops_its_oldest_line(self):
        backend = LlamaChatBackend('http://127.0.0.1:1', opener=self._opener())
        for index in range(bot_chat_llm.PENDING_LIMIT + 5):
            backend.prefetch(_request(request_id=index + 1))
        self.assertEqual(bot_chat_llm.PENDING_LIMIT, len(backend._queue))
        self.assertEqual(6, backend._queue[0][0])

    def test_the_request_carries_the_stock_shaped_sampling_bounds(self):
        backend = LlamaChatBackend('http://127.0.0.1:1', opener=self._opener())
        backend.start()
        self.addCleanup(backend.stop)
        request = _request()
        backend.prefetch(request)
        self.assertTrue(_await(lambda: backend.compose(request)))
        url, payload = self.calls[0]
        self.assertTrue(url.endswith('/v1/chat/completions'))
        self.assertEqual(bot_chat_llm.MAX_TOKENS, payload['max_tokens'])
        self.assertFalse(payload['stream'])
        self.assertIn('<|im_end|>', payload['stop'])
        self.assertIn('\n', payload['stop'])


class _StubBackend(object):
    """A generating backend with the timing behaviour but no model."""

    def __init__(self, text=None, latency_hint_seconds=4.0):
        self.text = text
        self.latency_hint_seconds = float(latency_hint_seconds)
        self.prefetched = []

    def prefetch(self, request):
        self.prefetched.append(request['request_id'])

    def compose(self, request):
        return self.text


class _InstantBackend(object):
    """A backend with no prefetch, so ordinary reply pacing applies."""

    def compose(self, request):
        return '好'


class DirectorIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            'bots': [{'id': 1, 'team': 1, 'name': '今天不加班',
                      'vehicle': 'ussr:R04_T-34', 'vehicle_class': 'mediumTank',
                      'alive': True, 'x': 0.0, 'z': 0.0, 'hp': 400,
                      'max_hp': 400}],
            'speaker': {'name': 'Peng', 'x': 0.0, 'z': 0.0, 'spotted': set()},
            'arena_bounds': None,
        }

    def _director(self, backend):
        director = BotChatDirector(random.Random(5), tick_hz=30.0,
                                   backend=backend)
        director.reset_round(1)
        return director

    def test_a_generating_backend_is_asked_when_the_line_is_scheduled(self):
        backend = _StubBackend('模型写的')
        director = self._director(backend)
        director.observe_player_line(0, 1, '那个t34 回来一下', self.snapshot)
        self.assertEqual(1, len(backend.prefetched))

    def test_a_slow_backend_may_hold_its_line_past_human_pacing(self):
        # Ordinary pacing already reaches nine seconds, so the hint only
        # matters for a generator slower than a teammate typing.
        backend = _StubBackend('模型写的', latency_hint_seconds=12.0)
        director = self._director(backend)
        director.observe_player_line(0, 1, '那个t34 回来一下', self.snapshot)
        early = [line for tick in range(0, 359)
                 for line in director.tick(tick, self.snapshot)]
        self.assertEqual([], early)
        late = [line for tick in range(359, 600)
                for line in director.tick(tick, self.snapshot)]
        self.assertEqual(['模型写的'], [line['text'] for line in late])

    def test_a_hung_generator_cannot_hold_a_line_forever(self):
        backend = _StubBackend('模型写的', latency_hint_seconds=600.0)
        director = self._director(backend)
        director.observe_player_line(0, 1, '那个t34 回来一下', self.snapshot)
        cap = int(bot_chat.MAX_PREFETCH_WAIT_SECONDS * 30.0) + 2
        published = [(tick, line) for tick in range(0, cap)
                     for line in director.tick(tick, self.snapshot)]
        self.assertTrue(published)

    def test_a_model_that_writes_nothing_says_nothing(self):
        director = self._director(_StubBackend(None))
        director.observe_player_line(0, 1, '那个t34 回来一下', self.snapshot)
        published = [line for tick in range(0, 900)
                     for line in director.tick(tick, self.snapshot)]
        self.assertEqual([], published)

    def test_a_backend_without_prefetch_keeps_ordinary_reply_pacing(self):
        director = self._director(_InstantBackend())
        director.observe_player_line(0, 1, '那个t34 回来一下', self.snapshot)
        published = [(tick, line) for tick in range(0, 900)
                     for line in director.tick(tick, self.snapshot)]
        self.assertTrue(published)
        self.assertLessEqual(published[0][0],
                             int(bot_chat.REPLY_DELAY_MAX_SECONDS * 30.0) + 1)


class ServerWiringTest(unittest.TestCase):
    """The room only speaks when both halves of the download are configured."""

    def setUp(self):
        sys.path.insert(0, str(SERVER_ROOT))
        import lan_battle_server
        self.module = lan_battle_server

    def _clear_env(self):
        for name in (self.module.BOT_CHAT_RUNTIME_ENV,
                     self.module.BOT_CHAT_MODEL_ENV):
            previous = os.environ.pop(name, None)
            if previous is not None:
                self.addCleanup(os.environ.__setitem__, name, previous)

    def test_no_configuration_leaves_the_feature_off(self):
        self._clear_env()
        self.assertIsNone(self.module.configured_bot_chat(object()))

    def test_half_a_configuration_leaves_the_feature_off(self):
        self._clear_env()
        self.assertIsNone(self.module.configured_bot_chat(
            object(), executable='/tmp/llama-server'))
        self.assertIsNone(self.module.configured_bot_chat(
            object(), model_path='/tmp/model.gguf'))

    def test_the_environment_configures_the_generator(self):
        self._clear_env()
        os.environ[self.module.BOT_CHAT_RUNTIME_ENV] = '/tmp/llama-server'
        os.environ[self.module.BOT_CHAT_MODEL_ENV] = '/tmp/model.gguf'
        generator = self.module.configured_bot_chat(object())
        self.assertIsNotNone(generator)
        self.assertEqual('/tmp/llama-server', generator.supervisor.executable)

    def test_the_catalogue_prints_both_mirrors_for_every_tier(self):
        import io
        stream = io.StringIO()
        self.module.print_bot_chat_catalogue(stream)
        printed = stream.getvalue()
        for entry in catalog.MODEL_TIERS:
            self.assertIn(entry['key'], printed)
            self.assertIn(entry['license'], printed)
            for source in catalog.SOURCES:
                self.assertIn(catalog.model_url(entry['key'], source),
                              printed)
        self.assertIn('runtime', printed)

    def test_an_uninstalled_generator_refuses_to_start(self):
        generator = self.module.configured_bot_chat(
            object(), executable='/nonexistent/llama-server',
            model_path='/nonexistent/model.gguf')
        self.assertFalse(generator.start())

    @unittest.skipIf(sys.platform == 'win32',
                     'the stand-in runtime is a POSIX shell script')
    def test_a_started_generator_attaches_and_detaches_the_director(self):
        directory = tempfile.mkdtemp()
        executable, model = _write_fake_runtime(directory)
        state = self.module.BattleState(
            map_name='01_karelia', max_players=2, team1_size=1, team2_size=1)
        generator = self.module.configured_bot_chat(
            state, executable=executable, model_path=model)
        self.addCleanup(generator.stop)
        self.assertFalse(state.bot_chat.enabled())
        self.assertTrue(generator.start())
        self.assertTrue(_await(lambda: state.bot_chat.enabled(), timeout=30.0))
        generator.stop()
        self.assertFalse(state.bot_chat.enabled())


class SupervisorTest(unittest.TestCase):
    def test_threads_leave_the_game_some_cores(self):
        self.assertEqual(6, inference_threads(8))
        self.assertEqual(1, inference_threads(2))
        self.assertEqual(1, inference_threads(1))

    def test_a_reserved_port_is_usable(self):
        self.assertTrue(1 <= free_loopback_port() <= 65535)

    def test_a_missing_download_reports_rather_than_launching(self):
        messages = []
        supervisor = LlamaServerSupervisor(
            '/nonexistent/llama-server', '/nonexistent/model.gguf',
            log=messages.append)
        self.assertFalse(supervisor.available())
        self.assertFalse(supervisor.start())
        self.assertTrue(any('not installed' in message
                            for message in messages))

    @unittest.skipIf(sys.platform == 'win32',
                     'the stand-in runtime is a POSIX shell script')
    def test_a_real_child_starts_answers_and_stops(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(
            lambda: [os.remove(os.path.join(directory, name))
                     for name in os.listdir(directory)] and None)
        executable, model = _write_fake_runtime(directory)
        supervisor = LlamaServerSupervisor(executable, model, threads=1)
        self.addCleanup(supervisor.stop)
        self.assertTrue(supervisor.available())
        self.assertTrue(supervisor.start())
        self.assertTrue(supervisor.wait_ready(timeout=30.0))
        self.assertTrue(supervisor.is_ready())

        backend = LlamaChatBackend(supervisor.endpoint)
        backend.start()
        self.addCleanup(backend.stop)
        request = _request()
        backend.prefetch(request)
        self.assertEqual('收到, 这就回来',
                         _await(lambda: backend.compose(request)))

        supervisor.stop()
        self.assertFalse(supervisor.is_ready())
        self.assertIsNone(supervisor.poll())


if __name__ == '__main__':
    unittest.main()
