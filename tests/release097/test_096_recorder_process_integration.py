"""Live LAN decoding -> isolated writer -> unchanged replay reader/renderer."""
import copy
import json
import os
import sys
import tempfile
import time
import types
import unittest
from unittest import mock
from pathlib import Path

from test_096_features7 import header, FakeClock, RuntimeReplayTests
from test_port_0922_battle_runtime import _runtime, BattleRuntime, _Client
from gui.mods.offline_lan_0922 import offline_replay as rep, replay_process as rp
from gui.mods.offline_lan_0922.lan_client import LANClient
from gui.mods.offline_lan_0922.replay_transport import ReplayClient

HELPER = os.environ['RECORDER_HELPER']
PY3 = os.environ.get('PY3_EXE', sys.executable)
ORIGINAL = rp.ProcessRecorder

def factory(*args, **kwargs):
    kwargs['command_factory'] = lambda: [PY3, '-I', '-u', HELPER]
    return ORIGINAL(*args, **kwargs)

def wait_done(r):
    deadline=time.monotonic()+10
    while not r.done and time.monotonic()<deadline: time.sleep(.01)
    if not r.done: raise AssertionError('writer still running')
    if r.failed: raise AssertionError(r.failure)

def rows(path):
    reader=rep.Reader(path); out=[]
    try:
        while (row:=reader.next()) is not None: out.append(row)
    finally: reader.close()
    return out

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.active=[];self.clock=FakeClock();self.events=[]
        self.client=LANClient('',0,'Player','ussr:R11_MS-1',on_event=lambda k,m:self.events.append((k,copy.deepcopy(m))))
        self.h=header()
        self.client._handle_message(copy.deepcopy(self.h['welcome']))
        self.client._handle_message(copy.deepcopy(self.h['start']))
        self.client._handle_message(copy.deepcopy(self.h['snapshot']))
        self.assertIsNone(self.client.last_error)
    def tearDown(self):
        for r in self.active:
            if not r.closed:r.abort()
            try: wait_done(r)
            except AssertionError: pass
        rep._FINISHING[:]=[r for r in rep._FINISHING if not r.done]
    def start(self):
        r=factory(self.directory.name,2,self.h,self.clock);self.active.append(r)
        self.client._offline_replay_recorder=r
        return r
    def finish(self,r):
        rep.finish(self.client,'test_complete');wait_done(r)
        return rows(r.result['path'])
    def play(self,path):
        events=[];clock=FakeClock()
        c=ReplayClient('',0,'','',on_event=lambda k,m:events.append((k,copy.deepcopy(m))),
                       replay_path=path,replay_clock=clock)
        c.start();c.pump_replay();c.send_battle_ready();c.pump_replay()
        clock.t=10;c.pump_replay()
        return c,events
    def test_real_decoder_sparse_static_matches_playback(self):
        r=self.start()
        original=copy.deepcopy(self.h['snapshot'])
        original.pop('bot_manifest'); original['players'][0].pop('effective_params');original['players'][0].pop('outfits')
        original.update(server_tick=2,server_time_ms=100)
        original['players'][0]['x']=12.25
        self.clock.t=.1;self.client._handle_message(original)
        live=copy.deepcopy(self.client.last_snapshot)
        data=self.finish(r)
        self.assertEqual(2,len(data))
        self.assertNotIn('bot_manifest',data[0]['data'])
        self.assertNotIn('effective_params',data[0]['data']['players'][0])
        self.assertIn('effective_params',live['players'][0])
        c,events=self.play(r.path)
        self.assertIsNone(c.last_error)
        played=[m for k,m in events if k=='snapshot'][-1]
        for key in ('players','bots','bot_manifest','server_tick','server_time_ms'):
            self.assertEqual(live[key],played[key],key)
    def test_explicit_static_update_then_omitted_static_retains_updated_value(self):
        r=self.start()
        update=copy.deepcopy(self.h['snapshot']);update.update(server_tick=2,server_time_ms=100)
        update['players'][0]['effective_params']['physics']['powerW']*=1.1
        self.clock.t=.1;self.client._handle_message(update)
        accepted=copy.deepcopy(self.client.last_snapshot)
        self.assertEqual(2,accepted['server_tick'],self.client.last_error)
        next_update=copy.deepcopy(update);next_update.update(server_tick=3,server_time_ms=200)
        next_update.pop('bot_manifest');next_update['players'][0].pop('effective_params');next_update['players'][0].pop('outfits')
        self.clock.t=.2;self.client._handle_message(next_update)
        live=copy.deepcopy(self.client.last_snapshot)
        data=self.finish(r)
        self.assertIn('effective_params',data[0]['data']['players'][0])
        self.assertNotIn('effective_params',data[1]['data']['players'][0])
        c,events=self.play(r.path)
        self.assertEqual(live['players'],[m for k,m in events if k=='snapshot'][-1]['players'])
    def test_mutation_in_game_event_cannot_modify_recorded_snapshot(self):
        r=self.start()
        def mutate(k,m):
            if k=='snapshot':m['players'][0]['x']=555
        self.client.on_event=mutate
        update=copy.deepcopy(self.h['snapshot']);update.update(server_tick=2,server_time_ms=100)
        update['players'][0]['x']=22.0
        self.clock.t=.1;self.client._handle_message(update)
        self.assertEqual(555,self.client.last_snapshot['players'][0]['x'])
        self.assertEqual(22.,self.finish(r)[0]['data']['players'][0]['x'])
    def test_rejected_network_snapshot_does_not_create_record(self):
        r=self.start();update=copy.deepcopy(self.h['snapshot']);update['round_id']=999
        self.client._handle_message(update)
        data=self.finish(r);self.assertEqual(['end'],[x['type'] for x in data])
    def test_recording_disabled_does_not_launch_process(self):
        runtime=types.SimpleNamespace(client=self.client,_worker_mode=False,_client_ready_received=True,_ready_sent=True,_start_message=self.h['start'])
        with mock.patch.object(rep,'native_recording_mode',return_value=0),mock.patch.object(rp,'ProcessRecorder',side_effect=AssertionError('no process')):
            rep.ensure_recording(runtime)
        self.assertIsNone(getattr(self.client,'_offline_replay_recorder',None))
    def test_simulation_worker_and_playback_never_record(self):
        for is_worker,is_replay in ((True,False),(False,True)):
            c=types.SimpleNamespace(is_offline_replay=is_replay)
            runtime=types.SimpleNamespace(client=c,_worker_mode=is_worker,_client_ready_received=True,_ready_sent=True)
            with mock.patch.object(rp,'ProcessRecorder',side_effect=AssertionError('not foreground')):
                rep.ensure_recording(runtime)
    def test_native_ready_hook_starts_process_then_async_cleanup(self):
        engine=_runtime();b=BattleRuntime(engine);c=_Client();b.client=c
        c._offline_replay_welcome=self.h['welcome'];c.last_snapshot=self.h['snapshot']
        b._start_message=self.h['start'];b._config=self.h['config'];b._client_ready_received=True;b._ready_sent=True
        def isolated(*a,**kw):
            r=factory(self.directory.name,*a[1:],**kw);self.active.append(r);return r
        with mock.patch.object(rep,'native_recording_mode',return_value=2),mock.patch.object(rp,'ProcessRecorder',side_effect=isolated):
            rep.ensure_recording(b)
        r=c._offline_replay_recorder
        r.append('local',dict(position=[1,2,3]),.1)
        rep.finish(c,'stop');self.assertIsNone(c._offline_replay_recorder)
        wait_done(r);self.assertEqual([1,2,3],rows(r.path)[0]['data']['position'])
    def test_no_sync_deepcopy_json_or_gzip_on_snapshot_hook(self):
        r=self.start();message=copy.deepcopy(self.h['snapshot']);hint=rep.sparse_hint(message)
        with mock.patch.object(rep,'_process_log',lambda row:None), \
             mock.patch.object(rep.copy,'deepcopy',side_effect=AssertionError('main deep copy')), \
             mock.patch.object(rep.json,'dumps',side_effect=AssertionError('main JSON')), \
             mock.patch.object(rep.gzip,'open',side_effect=AssertionError('main gzip')):
            rep.observe_wire(self.client,message,hint)
        self.assertFalse(r.failed,r.failure)
        self.assertEqual(2,len(self.finish(r)))
    def test_failure_does_not_prevent_game_event_delivery(self):
        r=self.start();r.abort()
        update=copy.deepcopy(self.h['snapshot']);update.update(server_tick=2,server_time_ms=100)
        self.client._handle_message(update)
        self.assertEqual(2,self.client.last_snapshot['server_tick'])
        self.assertIsNone(self.client.last_error)
        self.assertIsNone(self.client._offline_replay_recorder)
    def test_real_gameplay_presentation_accepts_new_files_without_rewards(self):
        from test_096_features7 import EndToEndReplayTests
        originals=rep.Recorder
        active=self.active
        class SynchronousTestCompletion:
            # Only the test waits so the unchanged fixture can immediately
            # open the finished file. Production close remains nonblocking.
            def __init__(self,*args,**kwargs):
                self.r=factory(*args,**kwargs);active.append(self.r)
            def append(self,*args,**kwargs):return self.r.append(*args,**kwargs)
            def close(self,*args,**kwargs):
                self.r.close(*args,**kwargs);wait_done(self.r);return self.r.result['path']
        case=EndToEndReplayTests('test_record_decode_native_start_render_and_readonly_exit')
        result=unittest.TestResult()
        with mock.patch.object(rep,'Recorder',SynchronousTestCompletion):case.run(result)
        self.assertEqual([],result.errors);self.assertEqual([],result.failures)

if __name__=='__main__': unittest.main(verbosity=2)
