"""Bounded, nonblocking playback prefetch from a separate decoder process.

Process/runtime setup, stdout reads, and binary decode run only in this
bridge's background thread. The game callback consumes owned data objects;
it never deep-copies a hydrated snapshot or waits for the disk/decoder.
"""
from __future__ import print_function
from collections import deque
import hashlib
import io
import os
import struct
import subprocess
import sys
import threading
import time
import zipfile
try:
    import cPickle as pickle
except ImportError:
    import pickle

from gui.mods.offline_lan_0922 import offline_replay, replay_process

RESOURCE='res/offline_replay/replay_reader_process.py'
DIGEST='5ad6a0bfaa92e99826d8d9ed3cd385503600f5583aa464a3955033d6355d65ca'
MAX_BYTES=8*1024*1024
MAX_ROWS=128
NOW=getattr(time,'monotonic',time.time)


class Pending(Exception):
    pass


def _forbid(*args):
    raise ValueError('non-primitive object in replay decoder IPC')


def _decode(data):
    if sys.version_info[0]<3:
        u=pickle.Unpickler(io.BytesIO(data))
        u.find_global=_forbid
    else:
        class Safe(pickle.Unpickler):
            def find_class(self,*args):
                return _forbid(*args)
        u=Safe(io.BytesIO(data))
    return u.load()


def reader_command(game_root):
    command=replay_process.prepare_runtime(game_root)
    package=os.path.join(game_root,'mods','0.9.22.0.1',replay_process.PACKAGE_NAME)
    with zipfile.ZipFile(package,'r') as z:
        data=z.read(RESOURCE)
    if len(data)>128*1024 or hashlib.sha256(data).hexdigest()!=DIGEST:
        raise ValueError('replay decoder resource checksum mismatch')
    filename=os.path.join(os.path.dirname(command[0]),'replay_reader_'+DIGEST[:16]+'.py')
    if os.path.isfile(filename):
        with open(filename,'rb') as f:
            if hashlib.sha256(f.read()).hexdigest()!=DIGEST:
                raise ValueError('replay decoder cache was changed or quarantined')
    else:
        # Content-addressed file, single active playback per launcher/game.
        with open(filename,'wb') as f:
            f.write(data)
    return command[:-1]+[filename]


class ProcessReader(object):
    def __init__(self,path,command_factory=None):
        initial=offline_replay.Reader(path)
        self.header=initial.header
        initial.close()
        self.path=path
        self._queue=deque()
        self._written_bytes=self._read_bytes=0
        self._closed=False
        self._done=False
        self.error=None
        self.pid=None
        self._process=None
        self._factory=command_factory
        self._activity=NOW()
        self._started=NOW()
        self._saw_header=False
        self.count=0
        self.peak_bytes=0
        self._thread=threading.Thread(target=self._run,name='WoTReplayReader')
        self._thread.daemon=True
        self._thread.start()

    def _read_exact(self,stream,n):
        result=[]
        while n:
            chunk=stream.read(n)
            if not chunk:
                raise ValueError('replay decoder pipe closed before complete replay')
            result.append(chunk); n-=len(chunk)
        return b''.join(result)

    def _run(self):
        process=None
        try:
            root=os.getcwdu() if hasattr(os,'getcwdu') else os.getcwd()
            command=self._factory(root) if self._factory else reader_command(root)
            environment = dict(os.environ)
            environment.pop('PYTHONHOME', None)
            environment.pop('PYTHONPATH', None)
            kwargs={'env': environment, 'bufsize': 0, 'close_fds': os.name != 'nt'}
            if os.name=='nt':
                kwargs['creationflags']=0x08000000 | 0x00004000 # no console, below normal
            process=subprocess.Popen(command+[self.path],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**kwargs)
            self._process=process; self.pid=process.pid
            process.stdin.close()
            while not self._closed:
                length=struct.unpack('!I',self._read_exact(process.stdout,4))[0]
                if not 0<length<=replay_process.MAX_FRAME:
                    raise ValueError('invalid replay decoder IPC length')
                # Keep the pipe, decoded queue and read-ahead finite. Backpressure
                # stops only the decoder child, never the game thread.
                while (len(self._queue)>=MAX_ROWS or self._written_bytes-self._read_bytes+length>MAX_BYTES) and not self._closed:
                    self._activity=NOW(); time.sleep(0.002)
                if self._closed: break
                kind,value=_decode(self._read_exact(process.stdout,length))
                self._activity=NOW()
                if kind=='error': raise ValueError(value)
                if not self._saw_header:
                    if kind!='header' or value!=self.header:
                        raise ValueError('replay header changed between preflight and playback')
                    self._saw_header=True
                    continue
                if kind!='row' or not isinstance(value,dict):
                    raise ValueError('invalid replay decoder response')
                self._written_bytes+=length
                self.peak_bytes=max(self.peak_bytes,self._written_bytes-self._read_bytes)
                self._queue.append((value,length))
                if value['type']=='end':
                    self._done=True
                    break
        except Exception as error:
            if not self._closed: self.error=str(error)
        finally:
            if process is not None:
                try:
                    if process.poll() is None: process.terminate()
                    process.wait()
                except Exception: pass
                for stream in (process.stdout,process.stderr):
                    try: stream.close()
                    except Exception: pass
            self._done=True

    def next(self):
        if self.error: raise ValueError(self.error)
        if self._queue:
            row,size=self._queue.popleft(); self._read_bytes+=size
            self.count+=1
            return row
        if self._done:
            raise ValueError('replay decoder ended without an end row')
        timeout=30.0 if not self._saw_header else 15.0
        if NOW()-self._activity>timeout:
            self.close()
            raise ValueError('replay decoder stalled; playback stopped without discarding records')
        raise Pending()

    def close(self):
        self._closed=True
        process=self._process
        if process is not None:
            try:
                if process.poll() is None: process.terminate()
            except Exception: pass
        # No wait/join, decompression or file cleanup on the game thread.
