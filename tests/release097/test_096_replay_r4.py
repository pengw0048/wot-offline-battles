"""R4 exact function contracts; native engine/renderer are explicit doubles."""
import base64,copy,gzip,io,json,math,os,sys,tempfile,time,unittest
from collections import Counter
from pathlib import Path
from unittest import mock
from types import SimpleNamespace as NS
from gui.mods.offline_lan_0922 import replay_presentation as p, offline_replay as rep, replay_resources as resources,replay_reader
from gui.mods.offline_lan_0922.replay_transport import ReplayClient
from gui.mods.offline_lan_0922.battle_runtime import BattleRuntime
from gui.mods.offline_lan_0922.gun_mechanics import GunState
from test_096_features7 import header,FakeClock
from test_port_0922_battle_runtime import _Descriptor

ROOT=Path(__file__).resolve().parents[2]
HELPER=os.environ.get('REPLAY_READER_HELPER',str(ROOT/'replay_reader_process.py'))
USER_REPLAY=os.environ.get('USER_REPLAY')

def sample(t=0.,x=0.,**kw):
 r=dict(_replay_t=t,position=[x,0.,0.],_local_speed=10.,_local_yaw=0.,turret_yaw=0.,gun_pitch=0.,health=100,visibility={},gun={'ammo':[500,500],'clip':50,'shot_index':0,'reload_time':0.,'reload_duration':.1})
 r.update(kw);return r

def gun(left=0.,clip=50,ammo=500,duration=.1):
 return dict(ammo=[ammo,500],clip=clip,shot_index=0,reload_time=left,reload_duration=duration)

class PoseTests(unittest.TestCase):
 def test_midpoint_and_endpoints(self):
  a,b=sample(0,0),sample(.04,.4)
  for t,x in ((0,0),(.01,.1),(.02,.2),(.04,.4)):
   self.assertAlmostEqual(x,p.blend_pose(a,b,t)['position'][0])
 def test_short_arc_wrap(self):
  a,b=sample(0,0,_local_yaw=math.radians(179)),sample(.04,.4,_local_yaw=math.radians(-179))
  self.assertAlmostEqual(math.pi,p.blend_pose(a,b,.02)['_local_yaw'])
 def test_discrete_state_not_leaked(self):
  a,b=sample(0,0),sample(.04,.4)
  b['gun']['clip']=49;b['visibility']={'bot:1':[True,True]}
  r=p.blend_pose(a,b,.03);self.assertEqual(a['gun'],r['gun']);self.assertEqual({},r['visibility'])
 def test_death_does_not_smear(self):
  a,b=sample(0,0),sample(.04,.4,health=0)
  self.assertEqual(a['position'],p.blend_pose(a,b,.02)['position'])
 def test_long_gap_no_extrapolate(self):
  a,b=sample(0,0),sample(1,10)
  self.assertEqual(a['position'],p.blend_pose(a,b,.5)['position'])
 def test_teleport_no_fake_crossing(self):
  a,b=sample(0,0),sample(.04,1000)
  self.assertEqual(a['position'],p.blend_pose(a,b,.02)['position'])
 def test_no_mutation(self):
  a,b=sample(0,0),sample(.04,.4);old=copy.deepcopy((a,b));p.blend_pose(a,b,.02);self.assertEqual(old,(a,b))
 def test_record_timestamp_health_and_dispersion_survive_validation(self):
  a=sample(.32,1,dispersion_angle=.005,gun_angles=0);a['gun'].update(load_started=True,pending_index=None,dispersion=.02)
  self.assertEqual(a,rep.validate_local(a))

class ReloadTests(unittest.TestCase):
 def test_countdown_positive_internal_state_never_fakes_completions(self):
  e=p.ReloadEdges()
  for t in range(1000):self.assertIsNone(e.observe(gun(4.59,0,duration=4.59),t*.01,t*.01,False))
  self.assertEqual(0,e.completions)
 def test_one_countdown_start_one_end(self):
  e=p.ReloadEdges();e.observe(gun(5,0,duration=5),0,0,False)
  self.assertEqual('start',e.observe(gun(5,0,duration=5),10,10,True)[2])
  for i in range(1,500):self.assertIsNone(e.observe(gun(5-i*.01,0,duration=5),10+i*.01,10+i*.01,True))
  self.assertEqual('complete',e.observe(gun(0,50,duration=5),15,15,True)[2])
  for i in range(100):self.assertIsNone(e.observe(gun(0,50,duration=5),15+i*.01,15+i*.01,True))
  self.assertEqual(1,e.completions)
 def test_fifty_rounds_hundred_ms_are_not_merged(self):
  e=p.ReloadEdges();e.observe(gun(),0,0,True)
  for i in range(49):
   t=1+i*.2
   self.assertIsNotNone(e.observe(gun(.1,49-i,499-i),t,t,True))
   self.assertEqual('complete',e.observe(gun(0,49-i,499-i),t+.1,t+.1,True)[2])
  self.assertEqual(49,e.completions)
 def test_clip_reload_distinct_from_intra_clip(self):
  e=p.ReloadEdges();e.observe(gun(),0,0,True)
  e.observe(gun(.1,1,451),1,1,True)
  change=e.observe(gun(4.59,0,450,duration=4.59),1.2,1.2,True)
  self.assertEqual('cycle_change',change[2]);self.assertAlmostEqual(4.59,change[0])
 def test_module_damage_and_repair_change_duration(self):
  e=p.ReloadEdges();e.observe(gun(4.59,0,duration=4.59),0,0,True)
  event=e.observe(gun(7,0,duration=8.76),1,1,True);self.assertEqual('cycle_change',event[2])
  self.assertEqual('cycle_change',e.observe(gun(2,0,duration=4.59),2,2,True)[2])
 def test_elapsed_positive_does_not_invent_completion(self):
  e=p.ReloadEdges();value=e.observe(gun(.02),0,1,True);self.assertGreater(value[0],0);self.assertEqual(0,e.completions)
 def test_live_clock_cannot_change_replay_gun(self):
  b=BattleRuntime.__new__(BattleRuntime);b._replay_mode=True
  g=NS(reload_time=.02,reload_duration=5.,clip=0,ammo=[500]);b._gun_state=g
  before=copy.deepcopy(g.__dict__)
  self.assertIs(g,b._advance_local_gun_to(None,999))
  self.assertIsNone(b._advance_local_gun_edge(g,999))
  self.assertFalse(b._apply_current_reload_factor(g,None))
  b._ammo_tick();self.assertEqual(before,g.__dict__)
 def test_other_publication_sources_cannot_write(self):
  b=BattleRuntime.__new__(BattleRuntime);b._replay_mode=True;b._replay_publishing=False
  b._avatar=NS(updateVehicleGunReloadTime=mock.Mock(side_effect=AssertionError('live writer')))
  self.assertFalse(b._publish_reload_event(0.,0.,True));self.assertFalse(b._publish_ammo_state(None,True))
 def test_no_rescale_of_recorded_duration(self):
  g=NS(_offline_replay_owned=True,reload_time=4.,reload_duration=5.,reload=5.,clip=0)
  self.assertFalse(BattleRuntime._rescale_current_reload(g,4));self.assertEqual(4,g.reload_time)

class ProfileTests(unittest.TestCase):
 def contract(self):
  d=_Descriptor();g=d.gun;s=g.shots[0];sh=s.shell
  ep={'gun':{'clip_size':1,'shots':[{'compact_descr':101,'source_shot':{'speed':s.speed,'gravity':s.gravity,'maxDistance':s.maxDistance,'piercingPower':list(s.piercingPower),'shell':{'damage':list(sh.damage),'kind':sh.kind,'caliber':sh.caliber,'explosionRadius':0.}}}]}}
  return d,ep
 def test_same_profile_accepted(self):
  d,ep=self.contract();self.assertTrue(p.verify_descriptor(d,ep))
 def test_wrong_clip_rejected(self):
  d,ep=self.contract();d.gun.clip=(50,.1)
  with self.assertRaisesRegex(ValueError,'clip'):p.verify_descriptor(d,ep)
 def test_wrong_damage_penetration_and_speed_rejected(self):
  for key in ('speed','gravity','piercingPower'):
   d,ep=self.contract();setattr(d.gun.shots[0],key,(1.,1.) if key=='piercingPower' else 100.)
   with self.assertRaisesRegex(ValueError,'differs'):p.verify_descriptor(d,ep)
  d,ep=self.contract();d.gun.shots[0].shell.damage=(20,5000)
  with self.assertRaisesRegex(ValueError,'damage'):p.verify_descriptor(d,ep)
 def test_armor_contract_detects_change(self):
  d,ep=self.contract();d.hull.armor={'armor_1':100};c=p.descriptor_contract(d);d.hull.armor={'armor_1':50}
  self.assertFalse(p._equal_values(c,p.descriptor_contract(d)))
 def test_legacy_signature_not_claimed_verified(self):
  self.assertFalse(resources.verify(None,'/unused'))
 def test_overlay_content_not_name_defines_profile(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)/'res_mods/0.9.22.0.1';f=root/'scripts/item_defs/vehicles/france/F69.xml';f.parent.mkdir(parents=True)
   f.write_bytes(b'armour100');(root/'vehicle_overlays.json').write_text(json.dumps({'members':[{'overlayRelativePath':'scripts/item_defs/vehicles/france/F69.xml'}]}))
   sig=resources.active_signature(t);self.assertTrue(resources.verify(sig,t));f.write_bytes(b'armour10')
   with self.assertRaisesRegex(ValueError,'profile'):resources.verify(sig,t)
 def test_recorded_descriptor_wins_over_current_garage(self):
  b=BattleRuntime.__new__(BattleRuntime);b._replay_mode=True
  d,ep=self.contract();decode=mock.Mock(return_value=d);b._runtime=NS(vehicles=NS(VehicleDescr=decode))
  row={'id':1,'vehicle':'ussr:R11_MS-1','vehicle_compact_descr':base64.b64encode(b'old_gun_old_turret').decode()}
  b.client=NS(reader=NS(header={'player_id':1,'snapshot':{'players':[row]}}))
  b._garage_loadout_snapshot=mock.Mock(side_effect=AssertionError('current garage accessed'))
  self.assertIs(d,b._local_battle_descriptor('ussr:R11_MS-1'));decode.assert_called_once_with(compactDescr=b'old_gun_old_turret')

class ReaderTests(unittest.TestCase):
 def make(self,t,rows=None):
  c=FakeClock();r=rep.Recorder(t,2,header(),c)
  for row in (rows or [sample(i*.04,i*.4) for i in range(20)]):
   data=dict(row);stamp=data.pop('_replay_t');r.append('local',data,stamp)
  return r.close()
 def reader(self,path):
  r=replay_reader.ProcessReader(path,command_factory=lambda root:[sys.executable,HELPER]);self.addCleanup(r.close);return r
 def drain(self,r):
  result=[];deadline=time.time()+20
  while time.time()<deadline:
   try:row=r.next()
   except replay_reader.Pending:time.sleep(.001);continue
   result.append(row)
   if row['type']=='end':return result
  self.fail('decoder never completed')
 def test_actual_process_and_order(self):
  with tempfile.TemporaryDirectory() as t:
   r=self.reader(self.make(t));items=self.drain(r);self.assertEqual(21,len(items));self.assertNotEqual(os.getpid(),r.pid)
   self.assertEqual(list(range(20)),[round(x['data']['position'][0]/.4) for x in items[:-1]])
 def test_chinese_path(self):
  with tempfile.TemporaryDirectory(prefix='回放 空格 ') as t:self.assertEqual(21,len(self.drain(self.reader(self.make(t)))) )
 def test_crc_truncation_not_success(self):
  with tempfile.TemporaryDirectory() as t:
   path=self.make(t);raw=Path(path).read_bytes();Path(path).write_bytes(raw[:-8]);r=self.reader(path)
   with self.assertRaises(ValueError):self.drain(r)
 def test_restricted_pickle_rejects_globals(self):
  import pickle
  with self.assertRaises(ValueError):replay_reader._decode(pickle.dumps(os.system,protocol=2))
 def test_nonblocking_empty_queue(self):
  with tempfile.TemporaryDirectory() as t:
   r=replay_reader.ProcessReader(self.make(t),command_factory=lambda root:[sys.executable,'-c','import time; time.sleep(30)'])
   self.addCleanup(r.close);start=time.perf_counter()
   with self.assertRaises(replay_reader.Pending):r.next()
   self.assertLess(time.perf_counter()-start,.1)
 def test_bounded_prefetch(self):
  with tempfile.TemporaryDirectory() as t:
   r=self.reader(self.make(t,[sample(i*.04,i*.4) for i in range(1000)]));time.sleep(.2)
   self.assertLessEqual(len(r._queue),replay_reader.MAX_ROWS)
   self.assertLessEqual(r.peak_bytes,replay_reader.MAX_BYTES)
 def test_real_uploaded_replay_crc_count(self):
  if not USER_REPLAY:self.skipTest('no uploaded replay')
  r=self.reader(USER_REPLAY);data=self.drain(r);self.assertEqual(3940,len(data));self.assertEqual(3939,data[-1]['records'])

class TransportTests(unittest.TestCase):
 def test_lookahead_does_not_emit_future_discrete_events(self):
  clock=FakeClock()
  with tempfile.TemporaryDirectory() as t:
   r=rep.Recorder(t,2,header(),clock)
   a,b=sample(.01,0),sample(.05,.4);a.pop('_replay_t');b.pop('_replay_t')
   r.append('local',a,.01);r.append('local',b,.05);r.close()
   seen=[];c=ReplayClient('',0,'','',replay_path=r.path,replay_clock=clock,replay_reader_factory=rep.Reader,on_event=lambda k,m:seen.append((k,m)))
   self.addCleanup(c.stop);c.start();c.pump_replay();c.send_battle_ready()
   clock.t=.02;c.pump_replay()
   local=[m for k,m in seen if k=='replay_local'];self.assertEqual(1,len(local));self.assertEqual(.01,local[0]['_replay_t'])
   self.assertEqual(.05,c._replay_local_future['_replay_t']);self.assertAlmostEqual(.1,p.blend_pose(local[0],c._replay_local_future,c.presentation_time())['position'][0])
 def test_user_file_all_messages_decode_without_reward_or_socket(self):
  if not USER_REPLAY:self.skipTest('no uploaded replay')
  clock=FakeClock();counts=Counter()
  c=ReplayClient('',0,'','',replay_path=USER_REPLAY,replay_clock=clock,replay_reader_factory=rep.Reader,on_event=lambda k,m:counts.update([k]))
  self.addCleanup(c.stop);c.start();c.pump_replay();c.send_battle_ready()
  while c.running and clock.t<100:
   clock.t+=.01;c.pump_replay()
  self.assertEqual(2163,counts['replay_local']);self.assertEqual(1,counts['replay_finished']);self.assertEqual(0,counts['replay_error']);self.assertIsNone(c.sock)

if __name__=='__main__':unittest.main()
