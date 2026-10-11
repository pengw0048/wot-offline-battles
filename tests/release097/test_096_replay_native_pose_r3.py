"""R3 regression: read-only native aim + playback-only failure ownership."""
import copy,gzip,json,os,sys,tempfile,types,unittest
from pathlib import Path
from unittest import mock
from test_port_0922_battle_runtime import _runtime, BattleRuntime, _Client, _minimal_start
from test_096_features7 import header, FakeClock
import test_096_features7 as old_features
from test_096_replay_entry import bot_header
from test_port_0922_lan_session import _JoinUI
from gui.mods.offline_lan_0922 import offline_replay as rep
from gui.mods.offline_lan_0922.lan_session import LANSession
from gui.mods.offline_lan_0922.replay_transport import ReplayClient
from gui.mods.offline_lan_0922 import ui_i18n
from replay_rotator_contract import VehicleGunRotator

class NativePoseTests(unittest.TestCase):
    setUp = old_features.RuntimeReplayTests.setUp
    def make_running(self):
        rt=_runtime();b=BattleRuntime(rt);c=_Client();c.is_offline_replay=True
        self.assertTrue(b.start(dict(map='01_karelia',vehicle='ussr:R11_MS-1',name='Player'),_minimal_start(),c),b.error)
        self.addCleanup(b.stop,show_login=False)
        for i in range(300):
            if b.state=='running':break
            if rt.bigworld.callbacks:rt.bigworld.callbacks.pop(0)()
        self.assertEqual('running',b.state,b.error)
        r=VehicleGunRotator(b._avatar.gunRotator);b._avatar.gunRotator=r
        # The original fixture encoder always returns zero. Give it a sentinel
        # and assert its angle arguments, rather than mistaking that for a failed write.
        b._binding._encode_gun_angles=mock.Mock(return_value=4242)
        return rt,b,r
    def sample(self,**kwargs):
        s=dict(position=[4.,1.,9.],_local_yaw=.3,_local_pitch=.1,_local_roll=0.,
               _local_speed=2.,turret_yaw=1.25,gun_pitch=-.15,gun_angles=0,visibility={})
        s.update(kwargs);return s
    def test_readonly_rotator_reproduces_original_exception(self):
        r=VehicleGunRotator()
        for name in ('turretYaw','gunPitch','dispersionAngle'):
            with self.assertRaises(AttributeError): setattr(r,name,1.)
            self.assertIsNone(getattr(type(r),name).fset)
    def test_actual_presenter_restores_angles_matrices_and_packed_echo(self):
        rt,b,r=self.make_running();vehicle=b._server_entity(b._server.vehicle_id)
        before=vehicle.gunAnglesPacked
        b.apply_replay_local(self.sample());b._present_replay_local(.03)
        self.assertEqual((1.25,-.15),(r.turretYaw,r.gunPitch))
        self.assertEqual((1.25,-.15),(r.turretMatrix.yaw,r.gunMatrix.pitch))
        self.assertNotEqual(before,vehicle.gunAnglesPacked)
        self.assertEqual((1.25,-.15),b._binding._encode_gun_angles.call_args[0][:2])
        self.assertEqual(0,r.timer_stops);self.assertEqual(0,r.timer_starts)
        self.assertEqual(1,len(r.forces));self.assertTrue(b._avatar.isGunLocked)
        self.assertIsNone(VehicleGunRotator.turretYaw.fset)
    def test_packed_echo_ignores_legacy_zero_alias(self):
        rt,b,r=self.make_running();vehicle=b._server_entity(b._server.vehicle_id)
        # A getter-only obsolete alias makes writing it fail, as a native
        # attribute contract would; correct code writes gunAnglesPacked.
        vehicle.__class__=type('ReadOnlyAliasVehicle',(type(vehicle),),{'gunAngles':property(lambda self:0)})
        b.apply_replay_local(self.sample(gun_angles=0));self.assertTrue(b._present_replay_local(.03))
        packed=vehicle.gunAnglesPacked
        b.apply_replay_local(self.sample(gun_angles=4294967295));b._present_replay_local(.03)
        self.assertEqual(packed,vehicle.gunAnglesPacked)
    def test_repeat_display_frame_does_not_repeat_marker_or_animator_work(self):
        rt,b,r=self.make_running();b.apply_replay_local(self.sample())
        for i in range(10):b._present_replay_local(.01)
        self.assertEqual(1,len(r.forces));self.assertEqual(1,len(r.marker_updates))
    def test_mouse_and_battle_unlock_cannot_replace_recorded_angles(self):
        rt,b,r=self.make_running();b.apply_replay_local(self.sample());b._present_replay_local(.03)
        b._set_gun_locked(False);r.advance_mouse(-2.,.6)
        self.assertEqual((1.25,-.15),(r.turretYaw,r.gunPitch));self.assertTrue(b._avatar.isGunLocked)
    def test_live_mode_still_unlocks_and_does_not_consume_replay_pose(self):
        rt,b,r=self.make_running();b._replay_mode=False;b._set_gun_locked(False)
        self.assertFalse(b._avatar.isGunLocked);self.assertFalse(b._apply_replay_gun_pose(self.sample()))
        r.advance_mouse(-2.,.6);self.assertEqual((-2.,.6),(r.turretYaw,r.gunPitch));self.assertEqual([],r.forces)
    def test_static_component_geometry_is_retained_by_native_matrix_methods(self):
        rt,b,r=self.make_running();r.static_yaw=0.;r.static_pitch=.08
        b.apply_replay_local(self.sample());b._present_replay_local(.03)
        self.assertEqual((1.25,-.15),(r.turretYaw,r.gunPitch))
        self.assertEqual((0.,.08),(r.turretMatrix.yaw,r.gunMatrix.pitch))
    def test_partial_angle_preserves_unrecorded_axis(self):
        rt,b,r=self.make_running();s=self.sample();del s['gun_pitch']
        old=r.gunPitch;b.apply_replay_local(s);b._present_replay_local(.03)
        self.assertEqual(old,r.gunPitch);self.assertEqual(1.25,r.turretYaw)
    def test_unsupported_native_contract_stays_an_explicit_error(self):
        rt,b,r=self.make_running();r.forceGunParams=None;b.apply_replay_local(self.sample())
        with self.assertRaisesRegex(RuntimeError,'gun-pose interface'):
            b._present_replay_local(.03)
        self.assertEqual([],r.forces)
    def test_first_real_user_frame_runs_through_scheduled_frame(self):
        path=os.environ.get('USER_REPLAY')
        if not path:self.skipTest('user replay not supplied')
        with gzip.open(path,'rt') as f:
            sample=next(json.loads(line)['data'] for line in f if json.loads(line).get('type')=='local')
        rt,b,r=self.make_running()
        # Native engine fixture is an MS1 descriptor, not the user's KV5;
        # leave the unrelated ammo-display list to its own existing tests.
        sample.pop('gun',None);sample['visibility']={}
        b.apply_replay_local(sample);b._battle_live=True;b._frame()
        self.assertEqual('running',b.state,b.error)
        self.assertEqual(sample['turret_yaw'],r.turretYaw)
        self.assertEqual(sample['gun_pitch'],r.gunPitch)

class ReplaySessionTests(unittest.TestCase):
    def session(self):
        status=[];s=LANSession(dict(name='Player'),status_notifier=status.append)
        s._stopped=False;s._battle_started=True;s._active_round_id=1
        s.client=mock.Mock(is_offline_replay=True)
        s._battle_runtime=mock.Mock()
        return s,status
    def test_native_replay_error_has_no_network_leave_or_duplicate_runtime_cleanup(self):
        s,status=self.session();s.client.leave_battle.side_effect=AssertionError('no room')
        rt=s._battle_runtime;c=s.client
        with mock.patch.dict(os.environ,{'WOT_OFFLINE_UI_LANGUAGE':'zh'}):
            self.assertFalse(s._on_battle_failed(dict(round_id=1,message="can't set attribute",lobby_restored=True)))
        c.leave_battle.assert_not_called();c.stop.assert_called_once();rt.stop.assert_not_called()
        self.assertTrue(s._stopped);self.assertIn('录像回放',status[0]);self.assertNotIn('局域网',status[0])
        self.assertIn("can't set attribute",status[0])
    def test_stale_replay_failure_does_not_stop_current_round(self):
        s,status=self.session();s._on_battle_failed(dict(round_id=0,message='old',lobby_restored=True))
        s.client.stop.assert_not_called();self.assertFalse(s._stopped);self.assertEqual([],status)
    def test_native_failure_without_lobby_restore_does_not_reenter_cleanup(self):
        s,status=self.session();rt=s._battle_runtime
        s._on_battle_failed(dict(round_id=1,message='native unavailable',lobby_restored=False))
        rt.stop.assert_not_called();s.client.leave_battle.assert_not_called();self.assertTrue(s._stopped)
    def test_replay_leave_signature_accepts_shared_session_keyword(self):
        c=ReplayClient.__new__(ReplayClient)
        self.assertTrue(c.leave_battle());self.assertTrue(c.leave_battle(voluntary=False))
    def test_translation_preserves_original_error(self):
        with mock.patch.dict(os.environ,{'WOT_OFFLINE_UI_LANGUAGE':'en'}):
            self.assertEqual('Replay playback stopped (XYZ).',ui_i18n.tr('Replay playback stopped (%s).')%'XYZ')

class MultiBotStrictIntegration(unittest.TestCase):
    setUp = old_features.RuntimeReplayTests.setUp
    def test_29_bots_then_saved_gun_samples_then_readonly_exit(self):
        clock=FakeClock();h=bot_header(29)
        with tempfile.TemporaryDirectory() as d:
            recorder=rep.Recorder(d,2,h,clock)
            for t,yaw,pitch in ((.1,.4,.1),(.2,1.,-.12),(.3,-1.,.35)):
                recorder.append('local',dict(position=[12.,.5,27.],_local_yaw=.2,_local_speed=4.,
                    gun_angles=0,turret_yaw=yaw,gun_pitch=pitch,visibility={}),t)
            recorder.last_time=1.;path=recorder.close()
            engine=_runtime();battle=BattleRuntime(engine);store=mock.Mock();store.progress.return_value={'battles':0}
            def factory(*a,**kw):return ReplayClient(*a,replay_path=path,replay_clock=clock,**kw)
            session=LANSession(dict(name='Player'),client_factory=factory,battle_runtime=battle,
                lobby_ready=lambda:True,postbattle_store=store,join_factory=_JoinUI,
                vehicle_provider=lambda:('ussr:R11_MS-1',500),vehicle_compact_provider=lambda:'dGVzdA==',
                status_notifier=lambda text:None)
            session._outfit_provider=lambda:{}
            self.addCleanup(session.stop,show_login=False)
            self.assertTrue(session.start());c=session.client;c.pump_replay()
            for i in range(600):
                if engine.bigworld.callbacks:engine.bigworld.callbacks.pop(0)()
                if c._replay_ready:break
            self.assertTrue(c._replay_ready,(battle.error,c.last_error))
            r=VehicleGunRotator(battle._avatar.gunRotator);battle._avatar.gunRotator=r
            start=clock.t
            with mock.patch('socket.socket',side_effect=AssertionError('no network')):
                for t,yaw,pitch in ((.1,.4,.1),(.2,1.,-.12),(.3,-1.,.35)):
                    clock.t=start+t+.001;c.pump_replay();battle._frame()
                    self.assertEqual('running',battle.state,battle.error)
                    self.assertEqual((yaw,pitch),(r.turretYaw,r.gunPitch))
                    self.assertEqual((yaw,pitch),(r.turretMatrix.yaw,r.gunMatrix.pitch))
                clock.t=start+2.;c.pump_replay()
            self.assertFalse(c.running);self.assertTrue(session._stopped)
            store.accept.assert_not_called();store.capture_participants.assert_not_called()

if __name__=='__main__':unittest.main()
