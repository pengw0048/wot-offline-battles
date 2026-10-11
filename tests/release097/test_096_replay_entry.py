"""Playback startup: canonical lineup before native readiness, not after it."""
import copy
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from test_096_features7 import header, FakeClock
import test_096_features7 as old_replay_tests
from test_port_0922_battle_runtime import _runtime, BattleRuntime
from gui.mods.offline_lan_0922 import offline_replay as rep
from gui.mods.offline_lan_0922.replay_transport import ReplayClient
from gui.mods.offline_lan_0922.lan_session import LANSession
from test_port_0922_lan_session import _JoinUI


def bot_header(n=2):
    h=header()
    bots=[dict(id=i+2,name='Bot %d'%i,team=2,slot=i,vehicle='ussr:R11_MS-1',
        health=500,max_health=500,alive=True,x=10.+i*5,y=0.,z=15.,yaw=0.,pitch=0.,roll=0.,
        speed=0.,world_pose=True,aim_yaw=0.,gun_pitch=0.,reload_time=0.,reload_duration=1.5)
        for i in range(n)]
    h['start']['bots']=[{k:b[k] for k in ('id','name','team','slot')} for b in bots]
    h['snapshot']['bots']=bots
    h['snapshot']['bot_manifest']=copy.deepcopy(bots)
    return h

class ReplayFixture(unittest.TestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup);self.directory=d.name;self.clock=FakeClock()
    def make(self,n=2,h=None):
        h=h or bot_header(n)
        r=rep.Recorder(self.directory,2,h,self.clock)
        r.append('local',dict(position=[12.,.5,27.],_local_yaw=.2,_local_speed=4.,gun_angles=0,visibility={}),.1)
        r.append('local',dict(position=[15.,.5,30.],_local_yaw=.3,_local_speed=4.,gun_angles=0,visibility={}),1.)
        return r.close()
class StartupTests(ReplayFixture):
    def test_reservations_receive_canonical_snapshot_before_ready(self):
        events=[];c=ReplayClient('',0,'','',replay_path=self.make(29),replay_clock=self.clock,
                                 on_event=lambda k,m:events.append(k))
        self.addCleanup(c.stop)
        c.start();c.pump_replay()
        self.assertEqual(['welcome','battle_start','snapshot'],events)
        self.assertEqual(29,len(c.last_snapshot['bot_manifest']))
        self.assertFalse(c._replay_ready)
        self.clock.t=100;c.pump_replay()
        self.assertEqual(3,len(events));self.assertEqual(0,c.reader.count)
        self.assertIsNone(c._replay_started)
    def test_ready_does_not_open_without_initial_state(self):
        c=ReplayClient('',0,'','',replay_path=self.make(),replay_clock=self.clock)
        self.addCleanup(c.stop);c.start();self.assertFalse(c.send_battle_ready())
        c.pump_replay();self.assertTrue(c.send_battle_ready())
    def test_corrupt_lineup_rejected_instead_of_waiting_forever(self):
        h=bot_header();h['snapshot']['bots'].pop()
        with self.assertRaisesRegex(ValueError,'lineup'):
            ReplayClient('',0,'','',replay_path=self.make(h=h))
    def test_cross_round_header_rejected(self):
        h=bot_header();h['snapshot']['round_id']=2
        with self.assertRaisesRegex(ValueError,'round'):
            ReplayClient('',0,'','',replay_path=self.make(h=h))
    def test_file_time_starts_at_ready_not_at_map_load(self):
        events=[];c=ReplayClient('',0,'','',replay_path=self.make(),replay_clock=self.clock,
                                 on_event=lambda k,m:events.append((k,m)))
        self.addCleanup(c.stop);c.start();c.pump_replay();self.clock.t=50
        c.pump_replay();c.send_battle_ready();c.pump_replay()
        self.assertFalse(any(k=='replay_local' for k,m in events))
        self.clock.t=50.15;c.pump_replay()
        self.assertEqual(12.,[m for k,m in events if k=='replay_local'][0]['position'][0])
    def test_no_network_or_recorder_at_playback_start(self):
        c=ReplayClient('',0,'','',replay_path=self.make(),replay_clock=self.clock)
        self.addCleanup(c.stop)
        with mock.patch('socket.socket',side_effect=AssertionError('no socket')),mock.patch.object(rep,'Recorder',side_effect=AssertionError('no recorder')):
            c.start();c.pump_replay()
        self.assertIsNone(c.sock)
        self.assertIsNone(getattr(c,'_offline_replay_recorder',None))

class NativeBarrierTests(ReplayFixture):
    def setUp(self):
        ReplayFixture.setUp(self)
        old_replay_tests.RuntimeReplayTests.setUp(self)
    def test_actual_runtime_multiple_bots_reach_ready_without_manual_ready_call(self):
        for n in (1,2,29):
            with self.subTest(bots=n):
                path=self.make(n);engine=_runtime();battle=BattleRuntime(engine);store=mock.Mock()
                store.progress.return_value={'battles':0}
                def factory(*args,**kwargs):
                    return ReplayClient(*args,replay_path=path,replay_clock=self.clock,**kwargs)
                session=LANSession(dict(name='Player'),client_factory=factory,
                    battle_runtime=battle,lobby_ready=lambda:True,postbattle_store=store,
                    join_factory=_JoinUI,vehicle_provider=lambda:('ussr:R11_MS-1',500),
                    vehicle_compact_provider=lambda:'dGVzdA==',status_notifier=lambda text:None)
                session._outfit_provider=lambda:{}
                self.assertTrue(session.start());c=session.client;c.pump_replay()
                for i in range(600):
                    if engine.bigworld.callbacks:engine.bigworld.callbacks.pop(0)()
                    if c._replay_ready:break
                self.assertTrue(c._replay_ready,(n,battle.state,battle.error,c.last_error,list(battle._records)))
                self.assertEqual(n,len([v for v in battle._records.values() if v.get('kind')=='bot']))
                self.assertTrue(all(v.get('ready') for v in battle._records.values()))
                self.clock.t+=.2;c.pump_replay();battle._present_replay_local(.03)
                self.assertEqual((12.,.5,27.),battle._local_position)
                session.stop(show_login=False)
                store.accept.assert_not_called();store.capture_participants.assert_not_called()

if __name__=='__main__':unittest.main()

class AutoEntryTests(unittest.TestCase):
    def namespace(self, mode='player'):
        import ast,os,sys,time,types
        source=Path(rep.__file__).with_name('bootstrap.py').read_text()
        tree=ast.parse(source)
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('_wait_for_lobby','_autostart_selected_replay')]
        ns=dict(os=os,sys=sys,time=time,_session=mock.Mock(),_config={},
            _client_mode=mode,port_config=types.SimpleNamespace(SIMULATION_WORKER_MODE='worker'),
            _lobby_view_loaded=True,_deadline=10000000000,_player_ready_signaled=False,
            _callback_id=None,_replay_autostart_requested=False,
            g_compatibility=mock.Mock(),_lobby_is_ready=lambda *a:True,
            _signal_player_ready=mock.Mock(return_value=True),_schedule=mock.Mock(),
            _wait_for_worker_connection=lambda:None,_fail_startup=mock.Mock())
        ns['g_compatibility'].is_ready.return_value=True
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'bootstrap-entry','exec'),ns)
        module=types.ModuleType('gui.app_loader');module.g_appLoader=mock.Mock()
        patch=mock.patch.dict(sys.modules,{'gui.app_loader':module});patch.start();self.addCleanup(patch.stop)
        return ns
    def test_visible_entry_once_after_real_lobby_ready(self):
        import os
        ns=self.namespace()
        with mock.patch.object(rep,'replay_request',return_value='test.wotlanreplay'),mock.patch.dict(os.environ,{'WOT_OFFLINE_REPLAY_AUTOSTART':'1'}):
            ns['_wait_for_lobby']();ns['_wait_for_lobby']()
            ns['_signal_player_ready'].assert_called_once()
            ns['_schedule'].assert_called_once_with(.1,ns['_autostart_selected_replay'])
            ns['_autostart_selected_replay']()
            ns['_session'].join.assert_called_once();ns['_fail_startup'].assert_not_called()
    def test_normal_live_game_does_not_auto_join(self):
        ns=self.namespace()
        with mock.patch.object(rep,'replay_request',return_value=None):ns['_wait_for_lobby']()
        ns['_schedule'].assert_not_called();ns['_session'].join.assert_not_called()
    def test_worker_never_joins_replay(self):
        ns=self.namespace('worker')
        with mock.patch.object(rep,'replay_request',return_value='test.wotlanreplay'):
            ns['_autostart_selected_replay']();ns['_wait_for_lobby']()
        ns['_session'].join.assert_not_called();ns['_session'].start.assert_called_once()
