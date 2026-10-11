# -*- coding: utf-8 -*-
"""Executable process tests: no BigWorld, sockets, account or render mocks.

Run with Python 2.7 or 3.11. RECORDER_SOURCE points to the module directory;
RECORDER_HELPER points to the standalone writer. PY3_EXE selects its runtime.
"""
from __future__ import print_function
import copy
import gzip
import hashlib
import io
import json
import os
import pickle
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile

SOURCE = os.environ['RECORDER_SOURCE']
HELPER = os.path.abspath(os.environ['RECORDER_HELPER'])
PY3 = os.environ.get('PY3_EXE', sys.executable)
MODULE_EXTENSION = os.environ.get('RECORDER_MODULE_EXTENSION', '.py')
if sys.version_info[0] < 3:
    import imp
    load = imp.load_compiled if MODULE_EXTENSION == '.pyc' else imp.load_source
else:
    import importlib.util
    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
rp = load('recorder_process_under_test', os.path.join(SOURCE, 'replay_process' + MODULE_EXTENSION))
rep = load('recording_format_under_test', os.path.join(SOURCE, 'offline_replay' + MODULE_EXTENSION))


def header(label='fixture'):
    return {'welcome': {'type': 'welcome', 'player_id': 1},
            'start': {'type': 'battle_start', 'round_id': 1},
            'snapshot': {'type': 'snapshot', 'round_id': 1},
            'config': {'map': '37_caucasus'}, 'player_id': 1, 'label': label}


def command():
    return [PY3, '-I', '-u', HELPER]


def wait_for(condition, timeout=12):
    start = time.time()
    while not condition():
        if time.time() - start > timeout:
            raise AssertionError('condition timed out')
        time.sleep(.01)


def read_rows(path):
    r = rep.Reader(path)
    rows = []
    try:
        while True:
            row = r.next()
            if row is None:
                break
            rows.append(row)
    finally:
        r.close()
    return rows


def send_packet(process, row):
    data = pickle.dumps(row, 2)
    process.stdin.write(struct.pack('!I', len(data)) + data)
    process.stdin.flush()


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='recorder-test-')
        self.active = []

    def tearDown(self):
        for recorder in self.active:
            if not recorder.done:
                recorder.abort()
                wait_for(lambda: recorder.done)
        shutil.rmtree(self.directory, ignore_errors=True)

    def recorder(self, mode=2, **kwargs):
        kwargs.setdefault('command_factory', command)
        r = rp.ProcessRecorder(self.directory, mode, header(), **kwargs)
        self.active.append(r)
        return r

    def complete(self, r):
        r.close()
        wait_for(lambda: r.done)
        self.assertFalse(r.failed, (r.failure, list(r.messages)))
        self.assertNotEqual(os.getpid(), r.pid)
        self.assertTrue(r.result)
        self.assertTrue(os.path.isfile(r.result['path']))
        return read_rows(r.result['path'])

    def test_separate_pid_record_order_timestamps_and_reader(self):
        r = self.recorder()
        for i in range(100):
            r.append('wire', {'type': 'snapshot', 'x': i}, i * .03)
        rows = self.complete(r)
        self.assertEqual(list(range(100)), [row['data']['x'] for row in rows[:-1]])
        self.assertEqual('end', rows[-1]['type'])
        self.assertEqual(100, r.result['records'])
        self.assertEqual(101, r._ack)
        self.assertAlmostEqual(2.97, rows[-2]['t'])

    def test_unicode_paths_and_payload(self):
        self.directory = os.path.join(self.directory, u'录像 子目录')
        r = self.recorder()
        r.append('wire', {'type': 'team_chat', 'text': u'测试 T-34-3 中文'}, .1)
        rows = self.complete(r)
        self.assertEqual(u'测试 T-34-3 中文', rows[0]['data']['text'])

    def test_snapshot_is_immutable_after_handoff(self):
        r = self.recorder()
        source = {'type': 'snapshot', 'a': [{'x': 1, 'v': [2]}]}
        r.append('wire', source, 1.)
        source['a'][0]['x'] = 900
        source['a'][0]['v'].append(99)
        rows = self.complete(r)
        self.assertEqual({'x': 1, 'v': [2]}, rows[0]['data']['a'][0])

    def test_sparse_projection_omits_only_inherited_static(self):
        raw = {'type': 'snapshot', 'players': [{'id': 1, 'x': 9}], 'bots': [{'id': 2}]}
        hint = rep.sparse_hint(raw)
        decoded = copy.deepcopy(raw)
        decoded.update(bot_manifest=[{'id': 2, 'data': 'long'}], _client_received_time=12)
        decoded['players'][0].update(effective_params={'power': 100}, outfits={})
        projected = rep.capture_message(decoded, hint)
        self.assertEqual(raw, projected)
        self.assertIn('bot_manifest', decoded)
        r = self.recorder(); r.append('wire', projected, .1)
        self.assertEqual(raw, self.complete(r)[0]['data'])

    def test_explicit_static_change_and_empty_manifest_are_kept(self):
        raw = {'type': 'snapshot', 'bot_manifest': [], 'players': [
            {'id': 1, 'effective_params': {'power': 23}, 'outfits': {}}]}
        self.assertEqual(raw, rep.capture_message(raw, rep.sparse_hint(raw)))

    def test_failed_and_inactive_recording_has_no_sync_fallback(self):
        r = self.recorder(command_factory=lambda: ['/missing/recorder-python.exe'])
        wait_for(lambda: r.done)
        self.assertTrue(r.failed)
        self.assertFalse(os.path.isfile(r.path))
        self.assertFalse(r.append('local', {'position': [0, 0, 0]}, .1))

    def test_close_does_not_wait_for_delayed_startup(self):
        gate = threading.Event()
        def delayed():
            gate.wait(5)
            return command()
        r = self.recorder(command_factory=delayed)
        r.append('local', {'position': [0, 0, 1]}, .1)
        start = time.time(); r.close()
        self.assertLess(time.time() - start, .10)
        self.assertFalse(r.done)
        gate.set(); wait_for(lambda: r.done)
        self.assertFalse(r.failed, r.failure)

    def test_backpressure_fails_recording_without_game_wait(self):
        gate = threading.Event()
        def delayed():
            gate.wait(5)
            return command()
        old = os.path.join(self.directory, 'replay_last_battle.wotlanreplay')
        with open(old, 'wb') as f: f.write(b'previous-complete')
        r = self.recorder(1, command_factory=delayed, queue_packets=3)
        r.append('local', {'position': [0, 0, 1]}, .1)
        r.append('local', {'position': [0, 0, 2]}, .2)
        start = time.time()
        with self.assertRaises(RuntimeError):
            r.append('local', {'position': [0, 0, 3]}, .3)
        self.assertLess(time.time()-start, .10)
        self.assertTrue(r.failed)
        gate.set(); wait_for(lambda: r.done)
        with open(old, 'rb') as f: self.assertEqual(b'previous-complete', f.read())

    def test_oversized_capture_refused_without_publish(self):
        r = self.recorder()
        with self.assertRaises(RuntimeError):
            r.append('wire', {'type': 'snapshot', 'blob': 'x'*(rp.MAX_FRAME+1)}, 0)
        wait_for(lambda: r.done)
        self.assertFalse(os.path.isfile(r.path))

    def test_stalled_writer_is_terminated_off_game_thread(self):
        r = self.recorder(command_factory=lambda: [PY3, '-I', '-u', '-c', 'import time;time.sleep(30)'], startup_timeout=.2)
        r.append('local', {'position': [0, 0, 0]}, .1)
        wait_for(lambda: r.done)
        self.assertTrue(r.failed)
        self.assertIn('timeout', r.failure)
        self.assertIsNotNone(r._process.poll())

    def test_killed_writer_fails_no_complete_file(self):
        r = self.recorder()
        wait_for(lambda: r._ready)
        r._process.kill(); wait_for(lambda: r.done)
        self.assertTrue(r.failed)
        self.assertFalse(os.path.isfile(r.path))

    def test_last_mode_preserves_old_on_abort_then_replaces_on_success(self):
        a = self.recorder(1); a.append('local', {'position': [1, 0, 0]}, 0)
        self.complete(a)
        with open(a.path, 'rb') as f: previous = f.read()
        b = self.recorder(1); b.append('local', {'position': [2, 0, 0]}, 0)
        wait_for(lambda: b._ready); b.abort(); wait_for(lambda: b.done)
        with open(a.path, 'rb') as f: self.assertEqual(previous, f.read())
        c = self.recorder(1); c.append('local', {'position': [3, 0, 0]}, 0)
        self.assertEqual([3, 0, 0], self.complete(c)[0]['data']['position'])

    def test_all_mode_unique_files(self):
        a, b = self.recorder(), self.recorder()
        self.complete(a); self.complete(b)
        self.assertNotEqual(a.path, b.path)

    def test_older_last_finalizer_cannot_overwrite_newer(self):
        a = self.recorder(1); a.append('local', {'position': [1,0,0]}, 0)
        wait_for(lambda: a._ready)
        b = self.recorder(1); b.append('local', {'position': [2,0,0]}, 0)
        self.complete(b); self.complete(a)
        self.assertNotEqual(a.result['path'], b.result['path'])
        self.assertEqual([2,0,0], read_rows(b.path)[0]['data']['position'])
        self.assertEqual([1,0,0], read_rows(a.result['path'])[0]['data']['position'])

    def test_output_path_failure_isolated(self):
        block = os.path.join(self.directory, 'not-a-directory')
        with open(block, 'wb') as f: f.write(b'file')
        original = self.directory; self.directory = block
        try: r = self.recorder()
        finally: self.directory = original
        wait_for(lambda: r.done)
        self.assertTrue(r.failed)

    def test_nonfinite_data_fails_without_publishing(self):
        r = self.recorder()
        r.append('local', {'position': [float('nan'), 0, 0]}, .1)
        wait_for(lambda: r.done)
        self.assertTrue(r.failed); self.assertFalse(os.path.isfile(r.path))

    def test_reward_record_rejected_not_replayed(self):
        r = self.recorder()
        r.append('wire', {'type': 'battle_receipt', 'rewards': {'xp': 999}}, .1)
        wait_for(lambda: r.done)
        self.assertTrue(r.failed); self.assertFalse(os.path.isfile(r.path))

    def test_clock_regression_preserves_monotonic_recording(self):
        r = self.recorder()
        r.append('local', {'position': [0,0,0]}, 1)
        r.append('local', {'position': [0,0,1]}, .5)
        rows = self.complete(r)
        self.assertEqual([1.,1.], [x['t'] for x in rows[:-1]])

    def test_child_eof_without_finish_never_publishes(self):
        proc = subprocess.Popen(command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        path = os.path.join(self.directory, 'eof.wotlanreplay'); session='a'*32
        send_packet(proc, {'op':'start','seq':0,'mode':2,'session':session,
                         'started_wall':time.time(),'path':path,'temp':path+'.'+session+'.part','header':header()})
        self.assertEqual('ready', json.loads(proc.stdout.readline().decode())['phase'])
        proc.stdin.close(); output=proc.stdout.read(); proc.wait()
        self.assertEqual(2, proc.returncode, output)
        self.assertFalse(os.path.isfile(path))
        self.assertTrue(os.path.isfile(path+'.'+session+'.part'))
        proc.stdout.close();proc.stderr.close()

    def test_child_sequence_gap_never_publishes(self):
        proc = subprocess.Popen(command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        path = os.path.join(self.directory, 'gap.wotlanreplay'); session='b'*32
        send_packet(proc, {'op':'start','seq':0,'mode':2,'session':session,
                         'started_wall':time.time(),'path':path,'temp':path+'.'+session+'.part','header':header()})
        proc.stdout.readline()
        send_packet(proc, {'op':'record','seq':2,'kind':'local','t':0,'data':{'position':[0,0,0]}})
        row=json.loads(proc.stdout.readline().decode()); proc.stdin.close(); proc.wait()
        self.assertEqual('failed', row['phase']);self.assertFalse(os.path.isfile(path))
        proc.stdout.close();proc.stderr.close()

    def test_globals_in_private_ipc_are_rejected(self):
        proc = subprocess.Popen(command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        data=pickle.dumps(os.system,2)
        proc.stdin.write(struct.pack('!I',len(data))+data);proc.stdin.flush()
        row=json.loads(proc.stdout.readline().decode());proc.stdin.close();proc.wait()
        self.assertEqual('failed', row['phase']);self.assertIn('forbidden', row['reason'])
        proc.stdout.close();proc.stderr.close()

    def test_cycle_in_private_ipc_is_rejected(self):
        proc = subprocess.Popen(command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        row={'op':'start'};row['cycle']=row
        send_packet(proc,row)
        answer=json.loads(proc.stdout.readline().decode());proc.stdin.close();proc.wait()
        self.assertEqual('failed',answer['phase']);self.assertIn('cyclic',answer['reason'])
        proc.stdout.close();proc.stderr.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
