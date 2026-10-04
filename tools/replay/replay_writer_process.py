"""Isolated offline replay writer. No game, network, or account imports.

Input is a private anonymous pipe from the owning game. Protocol-2 pickle is
used only as a fast primitive-data IPC envelope; globals/persistent references
are rejected. The on-disk format stays the existing schema-1 JSON/gzip replay.
EOF without an explicit, count-checked finish never publishes a complete file.
"""
import datetime
import gzip
import io
import json
import math
import os
import pickle
import struct
import sys
import time

MAGIC = 'WOT_OFFLINE_REPLAY'
SCHEMA = 1
CLIENT = '0.9.22.0.1-cn-1513'
MAX_FRAME = 4 * 1024 * 1024
MAX_LINE = 2 * 1024 * 1024
MAX_TOTAL = 768 * 1024 * 1024
MAX_DURATION = 4 * 3600.0
MESSAGE_TYPES = frozenset((
    'battle_live', 'snapshot', 'events', 'bot_observation', 'team_chat',
    'team_command', 'team_command_terminal', 'player_destructible_contact_result',
    'landing_observation_result',
))


class PrimitiveUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        raise ValueError('IPC global objects are forbidden')

    def persistent_load(self, value):
        raise ValueError('IPC persistent references are forbidden')


def primitive_load(data):
    stream = io.BytesIO(data)
    value = PrimitiveUnpickler(stream, encoding='utf-8', errors='strict').load()
    if stream.read(1):
        raise ValueError('trailing IPC data')
    # Avoid admitting cycles or deeply nested objects even on the private pipe.
    seen = set()
    nodes = [0]
    def check(item, depth):
        nodes[0] += 1
        if nodes[0] > 250000 or depth > 48:
            raise ValueError('IPC object exceeds structure bounds')
        if item is None or type(item) in (bool, int, str):
            return
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError('non-finite IPC value')
            return
        if type(item) not in (list, tuple, dict):
            raise ValueError('IPC must contain primitive JSON data')
        identity = id(item)
        if identity in seen:
            raise ValueError('cyclic IPC object')
        seen.add(identity)
        if type(item) is dict:
            for key, child in item.items():
                if type(key) not in (str, int):
                    raise ValueError('invalid IPC mapping key')
                check(child, depth + 1)
        else:
            for child in item:
                check(child, depth + 1)
        seen.remove(identity)
    check(value, 0)
    if not isinstance(value, dict):
        raise ValueError('IPC envelope must be an object')
    return value


def read_exact(stream, count):
    result = bytearray()
    while len(result) < count:
        part = stream.read(count - len(result))
        if not part:
            raise EOFError('owner pipe ended without a complete finish')
        result.extend(part)
    return bytes(result)


def receive(stream):
    length = struct.unpack('!I', read_exact(stream, 4))[0]
    if not 1 <= length <= MAX_FRAME:
        raise ValueError('IPC frame size outside bound')
    return primitive_load(read_exact(stream, length))


def status(kind, **values):
    row = dict(values, phase=kind, pid=os.getpid())
    sys.stdout.write(json.dumps(row, ensure_ascii=True, separators=(',', ':')) + '\n')
    sys.stdout.flush()


class PublishLock:
    """Serialize only short destination claims/publication, not compression."""
    def __init__(self, directory):
        self.path = os.path.join(directory, '.replay-publish.lock')

    def __enter__(self):
        self.stream = open(self.path, 'a+b')
        self.stream.seek(0, 2)
        if self.stream.tell() == 0:
            self.stream.write(b'0')
            self.stream.flush()
        self.stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            deadline = time.monotonic() + 5.0
            while True:
                try:
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        self.stream.close()
                        raise
                    time.sleep(0.01)
        else:
            import fcntl
            fcntl.flock(self.stream, fcntl.LOCK_EX)
        return self

    def __exit__(self, *unused):
        if os.name == 'nt':
            import msvcrt
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.stream, fcntl.LOCK_UN)
        self.stream.close()


class Writer:
    def __init__(self, start):
        if start.get('op') != 'start' or start.get('seq') != 0:
            raise ValueError('first IPC envelope must be start seq zero')
        self.path = os.path.abspath(start['path'])
        self.temp = os.path.abspath(start['temp'])
        self.session = start['session']
        self.mode = start['mode']
        self.started_wall = float(start['started_wall'])
        if (self.mode not in (1, 2) or not self.path.endswith('.wotlanreplay') or
                self.temp != self.path + '.' + self.session + '.part' or
                len(self.session) != 32 or
                any(char not in '0123456789abcdef' for char in self.session)):
            raise ValueError('invalid output file contract')
        header = start.get('header')
        if not isinstance(header, dict):
            raise ValueError('invalid replay header')
        for name in ('welcome', 'start', 'snapshot', 'config'):
            if not isinstance(header.get(name), dict):
                raise ValueError('missing replay header section ' + name)
        if header['start'].get('type') != 'battle_start':
            raise ValueError('invalid battle start')
        if not math.isfinite(self.started_wall):
            raise ValueError('invalid session creation time')
        self.directory = os.path.dirname(self.path)
        os.makedirs(self.directory, exist_ok=True)
        self.claim = os.path.join(self.directory, '.replay-last-claim.json')
        self.claim_owner = False
        if self.mode == 1:
            with PublishLock(self.directory):
                try:
                    with open(self.claim, encoding='utf-8') as stream:
                        previous = json.load(stream)
                except (OSError, ValueError):
                    previous = {}
                # An older recording that starts slowly must not replace a
                # newer completed last-battle recording.
                previous_start = float(previous.get('started_wall', -1))
                if self.started_wall >= previous_start:
                    new_claim = self.claim + '.' + self.session
                    with open(new_claim, 'x', encoding='utf-8') as stream:
                        json.dump({'session': self.session,
                                   'started_wall': self.started_wall}, stream)
                    os.replace(new_claim, self.claim)
                    self.claim_owner = True
        self.raw = open(self.temp, 'xb')
        self.stream = gzip.GzipFile(fileobj=self.raw, mode='wb', compresslevel=3)
        self.count = 0
        self.bytes = 0
        self.last_time = 0.0
        self.closed = False
        header = dict(header, magic=MAGIC, schema=SCHEMA, client=CLIENT,
                      created=datetime.datetime.now().isoformat(),
                      recorder='isolated-process-v1')
        try:
            self.write({'type': 'header', 'data': header})
        except BaseException:
            self.abort()
            raise

    def write(self, value):
        data = (json.dumps(value, ensure_ascii=True, allow_nan=False,
                           separators=(',', ':')) + '\n').encode('utf-8')
        if len(data) > MAX_LINE or self.bytes + len(data) > MAX_TOTAL:
            raise ValueError('recording exceeds original file bounds')
        self.stream.write(data)
        self.bytes += len(data)

    def append(self, row):
        if row.get('op') != 'record' or row.get('seq') != self.count + 1:
            raise ValueError('noncontiguous replay IPC sequence')
        kind, data = row.get('kind'), row.get('data')
        stamp = row.get('t')
        if (kind not in ('wire', 'local') or not isinstance(data, dict) or
                type(stamp) not in (int, float) or not math.isfinite(stamp) or
                not self.last_time <= stamp <= MAX_DURATION):
            raise ValueError('invalid replay record')
        if kind == 'wire' and data.get('type') not in MESSAGE_TYPES:
            raise ValueError('non-gameplay message rejected')
        self.write({'type': kind, 't': stamp, 'data': data})
        self.count += 1
        self.last_time = stamp

    def finish(self, row):
        if (row.get('seq') != self.count + 1 or
                row.get('records') != self.count):
            raise ValueError('finish count does not match accepted records')
        self.write({'type': 'end', 't': self.last_time,
                    'records': self.count, 'reason': str(row.get('reason', 'leave'))[:80]})
        self.stream.close()
        self.raw.flush()
        os.fsync(self.raw.fileno())
        self.raw.close()
        with PublishLock(self.directory):
            destination = self.path
            if self.mode == 1:
                try:
                    with open(self.claim, encoding='utf-8') as stream:
                        owner = json.load(stream).get('session')
                except (OSError, ValueError):
                    owner = None
                if owner != self.session:
                    destination = os.path.join(
                        self.directory, 'completed_' + self.session + '.wotlanreplay')
            os.replace(self.temp, destination)
        self.closed = True
        return destination

    def abort(self):
        # Close the compressed prefix but leave it marked .part; no end row
        # or atomic publication may be manufactured on failure.
        if not self.closed:
            try:
                self.stream.close()
            except Exception:
                pass
            try:
                self.raw.close()
            except Exception:
                pass
            self.closed = True


def main():
    writer = None
    seq = -1
    try:
        start = receive(sys.stdin.buffer)
        writer = Writer(start)
        seq = 0
        status('ready', seq=0, path=writer.path, temp=writer.temp,
               records=0, logical_bytes=writer.bytes)
        while True:
            row = receive(sys.stdin.buffer)
            seq = row.get('seq', -1)
            if row.get('op') == 'record':
                writer.append(row)
                status('ack', seq=seq, records=writer.count,
                       logical_bytes=writer.bytes)
            elif row.get('op') == 'finish':
                path = writer.finish(row)
                status('saved', seq=seq, records=writer.count,
                       logical_bytes=writer.bytes, path=path,
                       compressed_bytes=os.path.getsize(path))
                return 0
            else:
                raise ValueError('unknown recorder operation')
    except BaseException as error:
        if writer is not None:
            writer.abort()
        try:
            status('failed', seq=seq, reason=type(error).__name__ + ': ' + str(error)[:500])
        except Exception:
            pass
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
