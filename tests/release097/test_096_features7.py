"""Test7 contracts: real adapters with synthetic native engines and dossier bytes.

No Windows audio/render acceptance is implied. Replay tests exercise the actual
message decoder and runtime presentation, not merely the creation of a file.
"""
import ast
import base64
import copy
import gzip
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src/res/scripts/client'), str(ROOT/'server'), str(ROOT/'tests')]
from gui.mods.offline_lan_0922 import crew_service as cs, offline_replay as rep
from gui.mods.offline_lan_0922.replay_transport import ReplayClient
from gui.mods.offline_lan_0922.lan_client import LANClient, CLIENT_CAPABILITIES
from lan_battle_server import SERVER_CAPABILITIES
from test_port_0922_lan_protocol import _snapshot_player
from effective_params_fixture import effective_params
import test_port_0922_garage as gf
from test_port_0922_battle_runtime import (
    BattleRuntime, _runtime, _Client, _minimal_start, _mounted_current_vehicle_module,
    _plain_bot_factors, bot_runtime, _Vector)

MEDALS = ('warrior','invader','steelwall','mainGun')
class Dossier:
    def __init__(self, compact=b''):
        self.value = json.loads(compact.decode()) if compact else {
            'total': {'battlesCount':0}, 'achievements':dict.fromkeys(MEDALS,0)}
    def __getitem__(self, key): return self.value[key]
    def makeCompDescr(self): return json.dumps(self.value,sort_keys=True).encode()

class Tankman(gf._TankmanDescriptor):
    def __init__(self, compact):
        base, sep, dossier = compact.partition(b'~DOSSIER~')
        super().__init__(base)
        self.dossierCompactDescr = base64.b64decode(dossier) if sep else b''
    def getPassport(self):
        return (self.nationID,self.isPremium,self.isFemale,self.firstNameID,self.lastNameID,self.iconID)
    def makeCompactDescr(self):
        return super().makeCompactDescr()+b'~DOSSIER~'+base64.b64encode(self.dossierCompactDescr)

def native_dossiers():
    return mock.patch.dict(sys.modules, {
        'dossiers2.custom.builders': types.SimpleNamespace(getTankmanDossierDescr=Dossier),
        'dossiers2.custom.tankman_layout': types.SimpleNamespace(TMAN_ACHIEVEMENTS_BLOCK_LAYOUT=MEDALS)})

def get_dossier(compact): return Dossier(Tankman(compact).dossierCompactDescr).value

class CrewServiceTests(unittest.TestCase):
    def setUp(self):
        gf._request_modules()
        self.stores = gf._load('garage_store')
        self.vehicles, self.tankmen = gf._modules()
        self.tankmen.TankmanDescr=Tankman
        self.snapshot=gf.GaragePersistenceTests._matching_snapshot()
        # Native generated crew normally have distinct passports. Give the
        # two seats distinct identities and retain their genuine fixture XP.
        for mid, compact in list(self.snapshot['vehicles'][0]['tankmen'].items()):
            t=Tankman(compact);t.firstNameID=mid
            self.snapshot['vehicles'][0]['tankmen'][mid]=t.makeCompactDescr()
        d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup)
        self.path=os.path.join(d.name,'garage.json');self.store=self.stores.GarageStore(self.path)
        self.patch=native_dossiers();self.patch.start();self.addCleanup(self.patch.stop)
    def enroll(self, rid='battle:1:1'):
        return self.store.capture_crew_service(self.snapshot,rid,50001,self.tankmen)
    def settle(self,rid='battle:1:1',training=False):
        # Suppress unrelated personal mission assignment, not crew settlement.
        from gui.mods.offline_lan_0922 import personal_campaign_battle
        with mock.patch.object(personal_campaign_battle,'evaluate',return_value={'unsupported':{},'completed':{}}):
            return self.store.apply_battle_crew_xp(
                self.snapshot,rid,50001,100,1,tankmen_module=self.tankmen,
                vehicles_module=self.vehicles,rewards={'credits':100,'xp':100},training=training,
                campaign_receipt={'achievements':['warrior','warrior','notCrewMedal'],
                                  'premature_leave':True})
    def test_native_personal_dossier_and_xp_commit_together(self):
        self.enroll();r=self.settle();self.assertTrue(r['applied'])
        self.assertEqual([101,102],r['touched_tankmen'])
        for cd in self.snapshot['vehicles'][0]['tankmen'].values():
            d=get_dossier(cd);self.assertEqual(1,d['total']['battlesCount'])
            self.assertEqual(1,d['achievements']['warrior']);self.assertNotIn('notCrewMedal',d['achievements'])
            self.assertGreaterEqual(Tankman(cd).totalXP(),100)
    def test_duplicate_receipt_does_not_repeat_medals_or_rewards(self):
        self.enroll();self.settle();before=copy.deepcopy(self.snapshot)
        self.assertFalse(self.settle()['applied']);self.assertEqual(before,self.snapshot)
    def test_enrollment_and_receipt_journals_survive_store_restart(self):
        self.enroll();self.store=self.stores.GarageStore(self.path);self.settle()
        self.store=self.stores.GarageStore(self.path);before=copy.deepcopy(self.snapshot)
        self.settle();self.assertEqual(before,self.snapshot)
        stored=json.loads(Path(self.path).read_text());self.assertIn('battle:1:1',stored['crewServiceRosters'])
    def test_save_failure_has_no_partial_dossier_or_xp(self):
        self.enroll();before=copy.deepcopy(self.snapshot)
        with mock.patch.object(self.store,'_write_state',return_value=False):
            with self.assertRaises(RuntimeError):self.settle()
        self.assertEqual(before,self.snapshot);self.settle()
        self.assertEqual(1,get_dossier(self.snapshot['vehicles'][0]['tankmen'][101])['total']['battlesCount'])
    def test_enrollment_failure_prevents_silent_unattributed_battle(self):
        with mock.patch.object(self.store,'_write_state',return_value=False):
            with self.assertRaises(IOError):self.enroll()
        self.assertEqual({},self.store._crew_service_rosters)
    def test_frozen_crew_follow_barracks_and_retraining_not_replacement(self):
        self.enroll();record=self.snapshot['vehicles'][0]
        old=record['tankmen'].pop(101);t=Tankman(old);t.vehicleTypeID=17;t.addXP(13)
        self.snapshot['barracksTankmen']={7001:t.makeCompactDescr()}
        replacement=Tankman(b'tman:101');replacement.firstNameID=999
        record['tankmen'][101]=replacement.makeCompactDescr()
        self.settle()
        self.assertEqual(1,get_dossier(self.snapshot['barracksTankmen'][7001])['total']['battlesCount'])
        self.assertEqual(0,get_dossier(record['tankmen'][101])['total']['battlesCount'])
    def test_changed_inventory_id_after_restart_does_not_lose_person(self):
        self.enroll();record=self.snapshot['vehicles'][0]
        record['tankmen'][9001]=record['tankmen'].pop(101);record['crew'][0]=9001
        self.store=self.stores.GarageStore(self.path);self.settle()
        self.assertEqual(1,get_dossier(self.snapshot['vehicles'][0]['tankmen'][9001])['total']['battlesCount'])
    def test_training_does_not_create_service_or_medals(self):
        self.enroll();self.settle(training=True)
        for cd in self.snapshot['vehicles'][0]['tankmen'].values():
            self.assertEqual(0,get_dossier(cd)['total']['battlesCount'])
    def test_legacy_receipt_does_not_assign_account_medals_to_current_crew(self):
        self.settle()
        self.assertEqual(0,get_dossier(self.snapshot['vehicles'][0]['tankmen'][101])['total']['battlesCount'])
    def test_duplicate_passport_after_changed_descriptor_fails_closed(self):
        roster=cs.capture(self.snapshot['vehicles'][0],self.tankmen)
        rows=self.snapshot['vehicles'][0]['tankmen'];t=Tankman(rows[101]);t.addXP(1);rows[101]=t.makeCompactDescr()
        self.snapshot['barracksTankmen']={7001:rows[101]}
        before=copy.deepcopy(self.snapshot)
        with self.assertRaises(ValueError):cs.award(self.snapshot,roster,['warrior'],self.tankmen,Dossier,MEDALS)
        self.assertEqual(before,self.snapshot)
    def test_server_enrollment_id_matches_actual_reward_identity(self):
        from test_port_0922_server_projectiles import _state
        s=_state(1);s.phase='waiting';s.host_player_id=1
        message,error=s.request_start(1, '01_karelia')
        self.assertIsNone(error)
        self.assertEqual('%s:%d:1'%(s.receipt_namespace,s.round_id),message['players'][0]['crew_receipt_id'])


def header():
    ep=effective_params()
    player=_snapshot_player(1,name='Player',team=1,slot=0,vehicle='ussr:R11_MS-1',
        vehicle_compact_descr='dGVzdA==',outfits={},effective_params=ep,
        x=0.,y=0.,z=0.,yaw=0.,health=500,max_health=500,alive=True)
    welcome=dict(type='welcome',protocol=5,player_id=1,name='Player',vehicle='ussr:R11_MS-1',
        vehicle_compact_descr='dGVzdA==',outfits={},effective_params=ep,team=1,slot=0,
        max_health=500,map='01_karelia',map_pool=['01_karelia'],host_player_id=1,
        phase='waiting',round_id=1,state_revision=1,spawn=dict(x=0,y=0,z=0,yaw=0),
        bot_authority_id=-1,authority_epoch=1,capabilities=list(CLIENT_CAPABILITIES),
        server_capabilities=list(SERVER_CAPABILITIES))
    start=dict(type='battle_start',protocol=5,phase='loading',round_id=1,state_revision=2,
        map='01_karelia',host_player_id=1,bot_authority_id=-1,authority_epoch=1,
        players=[player],bots=[],bot_manifest=[])
    snap=dict(type='snapshot',protocol=5,round_id=1,server_tick=1,bot_state_revision=0,
        bot_manifest=[],players=[player],bots=[],bot_authority_id=-1,authority_epoch=1,
        projectile_revision=0,projectiles=[],server_time_ms=0)
    return dict(welcome=welcome,start=start,snapshot=snap,config=dict(map='01_karelia',vehicle='ussr:R11_MS-1'),player_id=1)

class FakeClock:
    def __init__(self):self.t=0.
    def __call__(self):return self.t

class ReplayTests(unittest.TestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup);self.directory=d.name;self.clock=FakeClock()
    def record(self,mode=2):return rep.Recorder(self.directory,mode,header(),self.clock)
    def local(self,z=0):return dict(position=[0,0,z],_local_yaw=0.,_local_speed=3.,gun_angles=0,visibility={})
    def test_last_replaces_only_after_successful_close(self):
        r=self.record(1);r.append('local',self.local());p=r.close();old=Path(p).read_bytes()
        r2=self.record(1);r2.append('local',self.local(9));self.assertEqual(old,Path(p).read_bytes())
        r2.abort();self.assertEqual(old,Path(p).read_bytes())
        r3=self.record(1);r3.append('local',self.local(12));r3.close()
        reader=rep.Reader(p);self.assertEqual(12,reader.next()['data']['position'][2]);reader.close()
    def test_all_mode_uses_unique_complete_files(self):
        a=self.record();b=self.record();a.close();b.close();self.assertNotEqual(a.path,b.path)
    def test_footer_and_count_are_verified(self):
        r=self.record();r.append('local',self.local());p=r.close();reader=rep.Reader(p)
        self.assertEqual('local',reader.next()['type']);self.assertEqual('end',reader.next()['type'])
        self.assertIsNone(reader.next());reader.close()
    def test_malformed_or_native_extension_not_silently_accepted(self):
        with self.assertRaises(ValueError):rep.Reader(os.path.join(self.directory,'wrong.wotreplay'))
        p=os.path.join(self.directory,'wrong.wotlanreplay');Path(p).write_bytes(b'garbage')
        with self.assertRaises(Exception):rep.Reader(p)
    def test_truncated_gzip_never_claims_complete_recording(self):
        r=self.record();r.append('local',self.local());p=r.close();data=Path(p).read_bytes();Path(p).write_bytes(data[:-8])
        with self.assertRaises(Exception):
            reader=rep.Reader(p)
            try:
                while reader.next():pass
            finally:reader.close()
    def test_reward_message_is_rejected_by_reader(self):
        r=self.record();r.append('wire',dict(type='battle_receipt',reward=100));p=r.close();reader=rep.Reader(p)
        with self.assertRaises(ValueError):reader.next()
        reader.close()
    def test_nonfinite_recording_and_local_state_rejected(self):
        r=self.record()
        with self.assertRaises(ValueError):r.append('local',dict(position=[float('nan'),0,0]))
        r.abort()
        with self.assertRaises(ValueError):rep.validate_local(dict(position=[0,float('inf'),0]))
    def test_no_setting_means_no_recording_and_modes_not_booleanized(self):
        for mode in (0,1,2):
            with mock.patch.dict(sys.modules,{'BattleReplay':types.SimpleNamespace(g_replayCtrl=types.SimpleNamespace(isAutoRecordingEnabled=mode))}):
                self.assertEqual(mode,rep.native_recording_mode())
    def test_transport_waits_for_native_ready_and_replays_timestamps(self):
        r=self.record();r.append('local',self.local(1),0.1);r.append('local',self.local(2),0.2);p=r.close()
        events=[];c=ReplayClient('invalid',1,'P','x',on_event=lambda k,m:events.append((k,m)),replay_path=p,replay_clock=self.clock)
        with mock.patch('socket.socket',side_effect=AssertionError('playback must not connect')):
            c.start();c.pump_replay();self.assertTrue(c.running,c.last_error)
            self.assertEqual(['welcome','battle_start'],[k for k,m in events])
            self.clock.t=100;c.pump_replay();self.assertEqual(2,len(events))
            c.send_battle_ready();c.pump_replay();self.assertEqual('snapshot',events[-1][0])
            self.clock.t=100.11;c.pump_replay();self.assertEqual(1,events[-1][1]['position'][2])
            self.clock.t=100.3;c.pump_replay();self.assertEqual('replay_finished',events[-1][0]);self.assertFalse(c.running)
    def test_transport_returns_original_multi_vehicle_snapshots_and_damage_events(self):
        r=self.record();snap=header()['snapshot'];snap['bots']=[dict(id=20,x=1.,y=0.,z=2.),dict(id=21,x=3.,y=0.,z=4.)]
        snap['server_tick']=2;snap['bot_state_revision']=1
        r.append('wire',snap,0.1)
        r.append('wire',dict(type='events',protocol=5,round_id=1,server_tick=3,server_time_ms=200,authority_epoch=1,bot_authority_id=-1,
            events=[dict(kind='bot_hit',attacker_id=1,bot_id=20,damage=123,health=377)]),0.2)
        p=r.close();events=[];c=ReplayClient('',0,'','',on_event=lambda k,m:events.append((k,m)),replay_path=p,replay_clock=self.clock)
        c.start();c.pump_replay();c.send_battle_ready();self.clock.t=.15;c.pump_replay()
        self.assertEqual([20,21],[b['id'] for k,m in events if k=='snapshot' for b in m['bots']])
        self.clock.t=.3;c.pump_replay();damages=[m for k,m in events if k=='events'];self.assertEqual(123,damages[0]['events'][0]['damage'])
    def test_playback_cannot_fire_or_become_authority(self):
        r=self.record();p=r.close();c=ReplayClient('',0,'','',replay_path=p)
        c.start();self.assertFalse(c.is_bot_authority());self.assertIsNone(c.send_projectile_launch())
        self.assertIsNone(c.send_equipment_intent());self.assertIsNone(c.sock);self.assertIsNone(c.thread);c.stop()
    def test_playback_session_never_accepts_rewards(self):
        from gui.mods.offline_lan_0922.lan_session import LANSession
        s=LANSession.__new__(LANSession);s._stopped=False;s.client=types.SimpleNamespace(is_offline_replay=True)
        s._postbattle_store=mock.Mock()
        s._on_event('battle_receipt',dict(type='battle_receipt',rewards={'xp':999}))
        s._postbattle_store.accept.assert_not_called()
    def test_live_recorder_is_not_created_when_disabled(self):
        c=types.SimpleNamespace(player_id=1)
        b=types.SimpleNamespace(client=c,_worker_mode=False,_client_ready_received=True,_ready_sent=True,_start_message={'round_id':1})
        with mock.patch.object(rep,'native_recording_mode',return_value=0):rep.ensure_recording(b)
        self.assertFalse(hasattr(c,'_offline_replay_recorder'))
    def test_recorder_hooks_copy_payload_and_exclude_settlement(self):
        r=self.record();c=types.SimpleNamespace(_offline_replay_recorder=r)
        msg=dict(type='snapshot',a=[1]);rep.observe_wire(c,msg);msg['a'][0]=99
        rep.observe_wire(c,dict(type='battle_receipt',credits=10000));r.close();reader=rep.Reader(r.path)
        self.assertEqual(1,reader.next()['data']['a'][0]);self.assertEqual('end',reader.next()['type']);reader.close()
    def test_live_recording_after_native_ready_then_native_cleanup_finalizes(self):
        runtime=_runtime();b=BattleRuntime(runtime);client=_Client();client._offline_replay_welcome=header()['welcome'];client.last_snapshot=header()['snapshot']
        b.client=client;b._start_message=header()['start'];b._config=header()['config'];b._client_ready_received=True;b._ready_sent=True
        # Process recording finalizes asynchronously. The test waits for its
        # completion acknowledgement, not a fixed sleep or an empty filename.
        import time
        from gui.mods.offline_lan_0922 import replay_process as process
        constructor=process.ProcessRecorder
        def isolated(directory, *args, **kwargs):
            kwargs['command_factory']=lambda:[sys.executable,'-I','-u',os.environ['RECORDER_HELPER']]
            return constructor(self.directory,*args,**kwargs)
        with mock.patch.object(rep,'native_recording_mode',return_value=2),mock.patch.object(process,'ProcessRecorder',side_effect=isolated):
            rep.ensure_recording(b)
        recorder=client._offline_replay_recorder
        try:
            recorder.append('local',self.local());rep.finish(client,'battle_stop')
            self.assertIsNone(client._offline_replay_recorder)
            deadline=time.monotonic()+10
            while not recorder.done and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue(recorder.done)
            self.assertFalse(recorder.failed,recorder.failure)
            self.assertTrue(os.path.isfile(recorder.result['path']))
            reader=rep.Reader(recorder.result['path'])
            try:self.assertEqual('local',reader.next()['type'])
            finally:reader.close()
        finally:
            if not recorder.done:recorder.abort()


class RuntimeReplayTests(unittest.TestCase):
    def setUp(self):
        p=mock.patch.dict(sys.modules,{'CurrentVehicle':_mounted_current_vehicle_module()});p.start();self.addCleanup(p.stop)
        p=mock.patch.object(bot_runtime,'_bot_default_crew_factors',side_effect=_plain_bot_factors);p.start();self.addCleanup(p.stop)
    def test_actual_runtime_saved_pose_not_drive_input(self):
        runtime=_runtime();b=BattleRuntime(runtime);c=_Client();c.is_offline_replay=True
        self.assertTrue(b.start(dict(map='01_karelia',vehicle='ussr:R11_MS-1',name='Player'),_minimal_start(),c),b.error)
        # Complete the native fixture's existing load callbacks.
        for _ in range(30):
            if b.state=='running':break
            if runtime.bigworld.callbacks:runtime.bigworld.callbacks.pop(0)()
        self.assertEqual('running',b.state,b.error)
        b.apply_replay_local(dict(position=[4.,1.,9.],_local_yaw=.3,_local_pitch=.1,_local_roll=0.,_local_speed=2.,gun_angles=0,visibility={}))
        self.assertTrue(b._present_replay_local(.03))
        self.assertEqual((4.,1.,9.),b._local_position);self.assertEqual(.3,b._local_yaw)
        with mock.patch.object(b,'_drive_local',side_effect=AssertionError('no physics replay')),mock.patch.object(b,'_advance_local_player_burst',side_effect=AssertionError('no burst simulation')):
            b._battle_live=True;b._frame()
        self.assertEqual('running',b.state,b.error)
        self.assertFalse(b.shoot(0.,0.));b.stop(show_login=False)
    def test_recorded_local_shot_does_not_require_or_create_live_trigger(self):
        b=BattleRuntime(_runtime());b._replay_mode=True
        self.assertFalse(b._commit_local_player_fire(dict(shooter_kind='player'),dict(local=True),{}))
        self.assertIsNone(b._local_fire_intent)
    def test_replay_local_voice_presentation_preserves_saved_damage(self):
        from test_port_0922_he_feedback import HEFeedbackTests
        rt,b,attacker,target,event=HEFeedbackTests()._fixture();b._replay_mode=True
        b._present_combat_feedback(event,target,attacker)
        flags=b._avatar.shot_results[0][0]>>32;vhf=rt.constants.VEHICLE_HIT_FLAGS
        self.assertEqual(vhf.ATTACK_IS_DIRECT_PROJECTILE|vhf.MATERIAL_WITH_POSITIVE_DF_PIERCED_BY_EXPLOSION,flags)
        self.assertEqual(150,event['damage'])


class EndToEndReplayTests(unittest.TestCase):
    setUp = RuntimeReplayTests.setUp
    def test_record_decode_native_start_render_and_readonly_exit(self):
        from gui.mods.offline_lan_0922.lan_session import LANSession
        from test_port_0922_lan_session import _JoinUI
        d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup)
        clock=FakeClock();h=header()
        recorder=rep.Recorder(d.name,2,h,clock)
        recorder.append('local',dict(position=[12.,.5,27.],_local_yaw=.2,
            _local_speed=4.,gun_angles=0,visibility={}),.1)
        recorder.append('local',dict(position=[15.,.5,30.],_local_yaw=.3,
            _local_speed=4.,gun_angles=0,visibility={}),1.)
        path=recorder.close()
        engine=_runtime();battle=BattleRuntime(engine);store=mock.Mock()
        store.progress.return_value={'battles':0}
        def factory(*args,**kwargs):
            return ReplayClient(*args,replay_path=path,replay_clock=clock,**kwargs)
        session=LANSession(dict(name='Player'),client_factory=factory,
            battle_runtime=battle,lobby_ready=lambda:True,postbattle_store=store,
            join_factory=_JoinUI,vehicle_provider=lambda:('ussr:R11_MS-1',500),
            vehicle_compact_provider=lambda:'dGVzdA==',status_notifier=lambda text:None)
        session._outfit_provider=lambda:{}
        with mock.patch('socket.socket',side_effect=AssertionError('no network')):
            self.assertTrue(session.start());client=session.client;client.pump_replay()
            self.assertTrue(session._battle_started,(session.state,client.last_error,battle.error))
            for _ in range(100):
                if not engine.bigworld.callbacks:break
                engine.bigworld.callbacks.pop(0)()
                if battle.state=='running' and client._replay_ready:break
            self.assertEqual('running',battle.state,battle.error)
            self.assertTrue(client._replay_ready,'native ready barrier not reached')
            clock.t=.2;client.pump_replay();battle._present_replay_local(.03)
            self.assertEqual((12.,.5,27.),battle._local_position)
            self.assertIsNone(client.sock);self.assertFalse(client.is_bot_authority())
            clock.t=1.1;client.pump_replay()
            self.assertFalse(client.running);self.assertTrue(session._stopped)
            store.accept.assert_not_called();store.capture_participants.assert_not_called()

class EnrollmentWireTests(unittest.TestCase):
    def test_real_decoder_preserves_native_battle_crew_receipt_identity(self):
        h=header();h['start']['players'][0]['crew_receipt_id']='session:1:1'
        events=[];c=LANClient('',0,'Player','ussr:R11_MS-1',on_event=lambda k,m:events.append((k,m)))
        c._handle_message(h['welcome']);c._handle_message(h['start'])
        starts=[m for k,m in events if k=='battle_start']
        self.assertEqual(1,len(starts),c.last_error)
        self.assertEqual('session:1:1',starts[0]['players'][0]['crew_receipt_id'])

if __name__=='__main__':unittest.main()
