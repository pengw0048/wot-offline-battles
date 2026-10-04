"""Nonblocking game-side bridge to the isolated replay writer process.

Only a small primitive-data protocol-2 binary freeze is done on the game
thread. JSON encoding, compression, disk writes, runtime preparation, process
startup, pipe I/O, and finalization never run on that thread. There is no
fallback to the old synchronous recorder. Queue failure aborts this recording
instead of discarding gameplay records or blocking gameplay.
"""
from __future__ import print_function

from collections import deque
import datetime
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
try:
    import cPickle as _pickle
except ImportError:
    import pickle as _pickle

RUNTIME_RESOURCE = 'res/offline_replay/recorder-runtime.zip'
RUNTIME_DIGEST = '8aee9faae63078408922d2ec000dc4211195a62a6921422a096042be3fe5f37b'
PACKAGE_NAME = 'org.colorfulmeans.offline_lan_0922_0.9.7.wotmod'
MAX_FRAME = 4 * 1024 * 1024
MAX_QUEUE_BYTES = 16 * 1024 * 1024
MAX_QUEUE_PACKETS = 512
MAX_DURATION = 4 * 3600.0
_TEXT = type(u'')
_NOW = getattr(time, 'monotonic', getattr(time, 'clock', time.time) if os.name == 'nt' else time.time)


def _text_path(value):
    return value if isinstance(value, _TEXT) else value.decode(
        sys.getfilesystemencoding() or 'utf-8')


def prepare_runtime(game_root):
    """Extract only our verified embedded resource in an owned user cache.

    This is called exclusively by the lifecycle thread. It never installs
    system Python, changes file associations, opens network ports, or alters
    game executables. The signed python.exe from python.org is unmodified.
    """
    game_root = _text_path(game_root)
    package = os.path.join(game_root, 'mods', '0.9.22.0.1', PACKAGE_NAME)
    with zipfile.ZipFile(package, 'r') as outer:
        data = outer.read(RUNTIME_RESOURCE)
    if hashlib.sha256(data).hexdigest() != RUNTIME_DIGEST:
        raise ValueError('recorder runtime resource checksum mismatch')
    base = os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()
    base = os.path.join(_text_path(base), 'WoTOfflineBattles', 'replay-runtime')
    if not os.path.isdir(base):
        try:
            os.makedirs(base)
        except OSError:
            if not os.path.isdir(base):
                raise
    target = os.path.join(base, RUNTIME_DIGEST[:20])
    archive = zipfile.ZipFile(io.BytesIO(data), 'r')
    infos = archive.infolist()
    if (len(infos) > 64 or sum(i.file_size for i in infos) > 40 * 1024 * 1024 or
            len(set(i.filename for i in infos)) != len(infos)):
        raise ValueError('runtime archive exceeds extraction bounds')
    for entry in infos:
        if ('/' in entry.filename or '\\' in entry.filename or
                entry.filename in ('', '.', '..') or ':' in entry.filename or
                ((entry.external_attr >> 16) & 0o170000) == 0o120000):
            raise ValueError('invalid runtime archive member')
    required = set(('python.exe', 'python311.dll', 'python311.zip',
                    'python311._pth', 'replay_writer_process.py', 'LICENSE.txt'))
    if not required.issubset(set(i.filename for i in infos)):
        raise ValueError('incomplete recorder runtime resource')
    expected = [(i.filename, hashlib.sha256(archive.read(i.filename)).hexdigest())
                for i in infos]
    def verified(directory):
        for name, digest in expected:
            filename = os.path.join(directory, name)
            if os.path.islink(filename) or not os.path.isfile(filename):
                return False
            with open(filename, 'rb') as stream:
                if hashlib.sha256(stream.read()).hexdigest() != digest:
                    return False
        return True
    if os.path.isdir(target):
        if not verified(target):
            raise ValueError('cached recorder runtime was changed or quarantined')
    else:
        staging = tempfile.mkdtemp(prefix='.prepare-', dir=base)
        try:
            for info in infos:
                with open(os.path.join(staging, info.filename), 'wb') as stream:
                    stream.write(archive.read(info.filename))
            try:
                os.rename(staging, target)
            except OSError:
                if not os.path.isdir(target) or not verified(target):
                    raise
        finally:
            if os.path.isdir(staging):
                shutil.rmtree(staging)
    archive.close()
    return [os.path.join(target, 'python.exe'), '-I', '-u',
            os.path.join(target, 'replay_writer_process.py')]


class ProcessRecorder(object):
    def __init__(self, directory, mode, header, clock=time.time,
                 command_factory=None, queue_bytes=MAX_QUEUE_BYTES,
                 queue_packets=MAX_QUEUE_PACKETS, startup_timeout=30.0,
                 progress_timeout=15.0):
        if mode not in (1, 2):
            raise ValueError('recording mode must be last or all')
        self.clock = clock
        self.started = float(clock())
        self.started_wall = time.time()
        self.last_time = 0.0
        self.last_pose_at = -1.0
        self.count = 0
        self.closed = False
        self.failed = False
        self.done = False
        self.failure = None
        self.pid = None
        self.result = None
        self._abort = False
        self._queue = deque()
        self.messages = deque()
        self._produced_bytes = 0
        self._written_bytes = 0
        self._produced_packets = 0
        self._written_packets = 0
        self._max_queue_bytes = int(queue_bytes)
        self._max_queue_packets = int(queue_packets)
        self._peak_queue_bytes = 0
        self._ack = -1
        self._ack_time = _NOW()
        self._ready = False
        self._saved = False
        self._finish_seq = None
        self._stdin_closed = False
        self._process = None
        self._capture_calls = 0
        self._capture_ms_sum = 0.0
        self._capture_ms_max = 0.0
        self._last_summary = _NOW()
        self._startup_timeout = float(startup_timeout)
        self._progress_timeout = float(progress_timeout)
        directory = _text_path(directory)
        session = uuid.uuid4().hex
        basename = ('replay_last_battle' if mode == 1 else
                    datetime.datetime.now().strftime('%Y%m%d_%H%M%S_') + session[:8])
        self.path = os.path.abspath(os.path.join(directory, basename + '.wotlanreplay'))
        self.temp = self.path + '.' + session + '.part'
        self._game_root = os.getcwdu() if hasattr(os, 'getcwdu') else os.getcwd()
        self._command_factory = command_factory
        self._enqueue({'op': 'start', 'seq': 0, 'mode': mode,
                       'session': session, 'started_wall': self.started_wall,
                       'path': self.path, 'temp': self.temp, 'header': header})
        self._thread = threading.Thread(target=self._run, name='WoTReplayProcess')
        self._thread.daemon = True
        self._thread.start()

    def _notice(self, row):
        # Status events are bounded; regular per-record acknowledgements are
        # counters, not a growing list of log lines.
        if len(self.messages) < 32:
            self.messages.append(row)

    def _fail(self, reason):
        if self._saved:
            return
        if not self.failed:
            self.failure = str(reason)[:500]
            self.failed = True
            self.closed = True
            self._abort = True
            self._notice({'phase': 'failed', 'reason': self.failure,
                          'pid': self.pid, 'part': self.temp})

    def _enqueue(self, value):
        if self.failed or self._abort:
            raise RuntimeError(self.failure or 'recorder stopped')
        started = _NOW()
        data = _pickle.dumps(value, 2)
        elapsed = max(0.0, (_NOW() - started) * 1000.0)
        self._capture_calls += 1
        self._capture_ms_sum += elapsed
        self._capture_ms_max = max(self._capture_ms_max, elapsed)
        length = len(data) + 4
        pending_bytes = self._produced_bytes - self._written_bytes
        pending_packets = self._produced_packets - self._written_packets
        if (len(data) > MAX_FRAME or pending_bytes + length > self._max_queue_bytes or
                pending_packets >= self._max_queue_packets):
            self._fail('capture_queue_full_or_oversized')
            raise RuntimeError(self.failure)
        # One game-thread producer and one I/O-thread consumer under CPython.
        # Each counter has one writer; the producer reads a conservatively
        # stale drain count. No frame waits for a mutex, pipe or file handle.
        self._produced_bytes += length
        self._produced_packets += 1
        self._peak_queue_bytes = max(self._peak_queue_bytes, pending_bytes + length)
        self._queue.append(struct.pack('!I', len(data)) + data)

    def append(self, kind, data, moment=None):
        if self.closed:
            return False
        elapsed = float(self.clock()) - self.started if moment is None else float(moment)
        if math_bad(elapsed) or not 0.0 <= elapsed <= MAX_DURATION:
            self._fail('invalid_capture_timestamp')
            raise ValueError(self.failure)
        elapsed = max(self.last_time, elapsed)
        self._enqueue({'op': 'record', 'seq': self.count + 1,
                       'kind': kind, 't': elapsed, 'data': data})
        self.last_time = elapsed
        self.count += 1
        return True

    def close(self, reason='leave'):
        if self.closed:
            return None if self.failed else self.path
        self._finish_seq = self.count + 1
        self._enqueue({'op': 'finish', 'seq': self._finish_seq,
                       'records': self.count, 'reason': str(reason)[:80]})
        self.closed = True
        self._notice({'phase': 'finalizing', 'pid': self.pid,
                      'records': self.count, 'path': self.path})
        return self.path

    def abort(self):
        self._fail('recording_aborted')

    def stats(self):
        return {'phase': 'summary', 'pid': self.pid, 'accepted': self.count,
                'writer_ack': self._ack,
                'pending_bytes': max(0, self._produced_bytes - self._written_bytes),
                'peak_pending_bytes': self._peak_queue_bytes,
                'capture_calls': self._capture_calls,
                'capture_ms_avg': self._capture_ms_sum / max(1, self._capture_calls),
                'capture_ms_max': self._capture_ms_max,
                'failed': self.failed, 'done': self.done}

    def poll_messages(self):
        rows = []
        while self.messages:
            rows.append(self.messages.popleft())
        if _NOW() - self._last_summary >= 30.0 and not self.done:
            self._last_summary = _NOW()
            rows.append(self.stats())
        return rows

    def _read_status(self, process):
        try:
            while True:
                raw = process.stdout.readline(16385)
                if not raw:
                    break
                if len(raw) > 16384:
                    raise ValueError('oversized writer status')
                row = json.loads(raw.decode('utf-8'))
                phase = row.get('phase')
                if phase == 'failed':
                    self._fail('writer: ' + str(row.get('reason')))
                    return
                if phase not in ('ready', 'ack', 'saved') or row.get('pid') != self.pid:
                    raise ValueError('invalid writer status identity')
                seq = int(row['seq'])
                if seq != self._ack + 1:
                    raise ValueError('writer acknowledgement gap')
                self._ack = seq
                self._ack_time = _NOW()
                if phase == 'ready':
                    self._ready = True
                    self._notice(row)
                elif phase == 'saved':
                    if seq != self._finish_seq or row.get('records') != self.count:
                        raise ValueError('writer completion count mismatch')
                    self._saved = True
                    self.result = row
                    self._notice(row)
                    return
        except Exception as error:
            self._fail('status: ' + str(error))

    def _write_pipe(self, process):
        try:
            while not self._abort:
                if self._queue:
                    packet = self._queue.popleft()
                    # The bytes snapshot cannot be mutated by later game or
                    # networking callbacks. Blocking writes occur only here.
                    offset = 0
                    while offset < len(packet):
                        written = process.stdin.write(packet[offset:])
                        if written is None:  # Python 2 file.write contract
                            written = len(packet) - offset
                        if written <= 0:
                            raise IOError('writer pipe made no progress')
                        offset += written
                    process.stdin.flush()
                    self._written_bytes += len(packet)
                    self._written_packets += 1
                elif self.closed:
                    process.stdin.close()
                    self._stdin_closed = True
                    return
                else:
                    time.sleep(0.002)
        except Exception as error:
            if not self._abort:
                self._fail('pipe: ' + str(error))

    def _run(self):
        process = None
        reader = None
        try:
            command = (self._command_factory() if self._command_factory is not None
                       else prepare_runtime(self._game_root))
            if self._abort:
                return
            environment = dict(os.environ)
            environment.pop('PYTHONHOME', None)
            environment.pop('PYTHONPATH', None)
            kwargs = {'stdin': subprocess.PIPE, 'stdout': subprocess.PIPE,
                      'stderr': subprocess.STDOUT, 'bufsize': 0,
                      'cwd': os.path.dirname(os.path.abspath(command[0])),
                      'env': environment}
            if os.name == 'nt':
                kwargs.update(creationflags=0x08000000 | 0x00004000,
                              close_fds=False)
            else:
                kwargs['close_fds'] = True
            process = subprocess.Popen(command, **kwargs)
            self._process = process
            self.pid = process.pid
            self._ack_time = _NOW()
            self._notice({'phase': 'process_started', 'pid': self.pid,
                          'game_pid': os.getpid()})
            reader = threading.Thread(target=self._read_status, args=(process,), name='WoTReplayStatus')
            reader.daemon = True
            reader.start()
            sender = threading.Thread(target=self._write_pipe, args=(process,), name='WoTReplayPipe')
            sender.daemon = True
            sender.start()
            while process.poll() is None:
                if self._abort:
                    process.terminate()
                    break
                idle = _NOW() - self._ack_time
                timeout = self._startup_timeout if not self._ready else self._progress_timeout
                outstanding = self._produced_packets > self._ack + 1
                if idle > timeout and (not self._ready or outstanding):
                    self._fail('writer_progress_timeout')
                    process.terminate()
                    break
                time.sleep(0.02)
            deadline = _NOW() + 2.0
            while process.poll() is None and _NOW() < deadline:
                time.sleep(0.02)
            if process.poll() is None:
                process.kill()
            process.wait()
            reader.join(1.0)
            if not self._saved and not self.failed:
                self._fail('writer_exited_without_completion:%s' % process.returncode)
        except Exception as error:
            self._fail('process: ' + str(error))
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                except Exception:
                    pass
        finally:
            if process is not None:
                for stream in (process.stdout, process.stdin):
                    try:
                        stream.close()
                    except Exception:
                        pass
            # Release immutable queued bytes off the frame thread.
            self._queue.clear()
            self.done = True


def math_bad(value):
    return value != value or value in (float('inf'), float('-inf'))
