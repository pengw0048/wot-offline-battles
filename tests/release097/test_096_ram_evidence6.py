"""Immutable RAM evidence integration tests. Synthetic geometry, no BigWorld."""
import copy
import io
import json
import math
import random
import sys
import types
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest import mock

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src/res/scripts/client'),str(ROOT/'tests'),str(ROOT/'server')]
from gui.mods.offline_lan_0922 import ram_history
from test_port_0922_battle_runtime import BattleRuntime, _runtime, _Vector
from gui.mods.offline_lan_0922.bot_runtime import BotRuntime
from gui.mods.offline_lan_0922.snapshot_sync import SnapshotSync
from test_port_0922_server_projectiles import (
    _state, _update_player_input, _player_ram_contact, SIMULATION_WORKER_AUTHORITY_ID)

PAIR=[100,1000000,102,1200000]
STAMP=1050000
SHAPE=(1.6,2.981,.002,1.552)
PLAYER_SHAPE=(2.047,4.085,.001,1.96)
PROFILE=dict(spall_coefficient=1.,ramming_bonus=0.)

def body(bid=30, **kw):
    value=dict(id=bid,alive=True,team=2,vehicle='china:Ch14_T34_3',x=0.,y=0.,z=6.8,
        yaw=math.pi,pitch=0.,roll=0.,aim_yaw=math.pi,gun_pitch=0.,speed=0.,
        mass=35500.,collision_shape=SHAPE,ram_profile=dict(PROFILE),health=50000,max_health=50000,
        display_health=50000,critical={},combat_revision=0,combat_base_revision=0,combat_ack_seq=0,
        combat_fire_elapsed=0.,combat_fire_timer=0.)
    value.update(kw);return value

def receipt(seq=1,bid=30,**kw):
    value=_player_ram_contact(seq=seq,bot_id=bid,bot_state_revision=102,
        presentation_time_us=STAMP,native_contact_time_us=1300000,
        contact_x=0.,contact_y=1.,contact_z=3.95,contact_armor_player=120.,contact_armor_bot=80.,
        vz=8.,bot_history_bracket=list(PAIR))
    value.update(kw);return value

def server(bids=(30,20)):
    s=_state(players=1)
    s.bot_manifest_authority_id=SIMULATION_WORKER_AUTHORITY_ID
    s.players[1].health=s.players[1].max_health=50000
    s.bot_states=dict((i,body(i)) for i in bids)
    s.human_collision_profiles[1]=dict(mass=130260.,shape=PLAYER_SHAPE,ram_profile=dict(PROFILE))
    for revision,t in ((100,1000000),(101,1100000),(102,1200000)):
        s.bot_state_revision=revision;s.bot_state_time_us=t
        s.ram_pose_archive.remember(revision,t,s.bot_states)
    return s

def worker(bids=(30,20)):
    w=BotRuntime.__new__(BotRuntime)
    w._human_ram_receipt_seq={};w._human_ram_report_cache={};w._ram_seq=0
    w._ram_wait_log_key=None;w._ram_wait_log_next=0.
    w._player_collision_profile=lambda raw:dict(mass=130260.,shape=PLAYER_SHAPE,ram_profile=dict(PROFILE))
    w.states=dict((i,body(i)) for i in bids)
    b=BattleRuntime(_runtime());b._bots=w
    return b,w

class ArchiveTests(unittest.TestCase):
    def test_actual_endpoints_not_nearest_right(self):
        s=server();s.ram_pose_archive.samples[100][1][30]['x']=0.
        s.ram_pose_archive.samples[101][1][30]['x']=200.
        s.ram_pose_archive.samples[102][1][30]['x']=4.
        p,e=s.ram_pose_archive.pin(30,102,STAMP,PAIR)
        self.assertIsNone(e);self.assertEqual(1.,p['x'])
    def test_freeze_not_shared_with_live_dictionary(self):
        h=ram_history.RamPoseArchive();v=body();h.remember(100,1000000,{30:v})
        v['x']=900.;v['alive']=False
        p,e=h.pin(30,100,1000000,[100,1000000,100,1000000])
        self.assertEqual(0.,p['x']);self.assertTrue(p['alive'])
    def test_pinned_pose_survives_source_eviction_and_death(self):
        s=server();p,e=s.ram_pose_archive.pin(30,102,STAMP,PAIR);saved=copy.deepcopy(p)
        for i in range(103,1000):s.ram_pose_archive.remember(i,i*100000,{30:body(alive=False,x=i)})
        self.assertNotIn(100,s.ram_pose_archive.samples)
        self.assertEqual(512,len(s.ram_pose_archive.samples));self.assertEqual(saved,p)
    def test_every_in_contract_boundary_is_retained(self):
        h=ram_history.RamPoseArchive()
        for i in range(1000):h.remember(i,i*1000,{30:body(x=i)})
        p,e=h.pin(30,744,600000,[489,489000,744,744000])
        self.assertIsNone(e);self.assertAlmostEqual(600.,p['x'])
    def test_invalid_pairs_fail_without_fabrication(self):
        s=server()
        bad=([100,1000000,101,1200000],[True,1000000,102,1200000],
             [100,1000000,102.,1200000],[100,1200000,102,1000000],
             [100,1000000,100,1200000],[100,1000001,102,1200000],
             [100,1000000,102,1200000,0])
        for pair in bad:
            with self.subTest(pair=pair):
                p,e=s.ram_pose_archive.pin(30,102,STAMP,pair)
                self.assertIsNone(p);self.assertIsNotNone(e)
    def test_wreck_and_live_identities_cannot_cross(self):
        s=server(bids=(21,30));s.ram_pose_archive.samples[100][1][21].update(alive=False,x=300.)
        p,e=s.ram_pose_archive.pin(30,102,STAMP,PAIR)
        self.assertEqual(30,p['id']);self.assertEqual(0.,p['x'])
        self.assertIsNone(s.ram_pose_archive.pin(29,102,STAMP,PAIR)[0])
    def test_shortest_angle_matches_renderer(self):
        for key in ('yaw','aim_yaw','pitch','roll'):
            left=body(**{key:math.pi-.03});right=body(**{key:-math.pi+.03})
            value=ram_history.interpolate_pose(left,right,1000000,1200000,1100000)
            self.assertAlmostEqual(math.pi,value[key])
    def test_same_revision_death_does_not_rewrite_pose(self):
        h=ram_history.RamPoseArchive();h.remember(1,1000,{30:body()})
        self.assertFalse(h.remember(1,1000,{30:body(alive=False)}))
        self.assertTrue(h.samples[1][1][30]['alive'])

class RendererTests(unittest.TestCase):
    def sync(self,with_wreck=False):
        now=[0.];sync=SnapshotSync(1,clock=lambda:now[0])
        for tick,revision,t in ((1,100,1000000),(2,102,1200000)):
            now[0]=t/1000000.
            bots=[body(x=(revision-100)*2)]
            if with_wreck:bots.append(body(21,alive=False,x=2.,z=2.))
            sync.snapshot(dict(round_id=1,server_tick=tick,bot_state_revision=revision,
                bot_state_time_us=t,motion_time_us=t,players=[],bots=bots))
        return sync,now
    def test_export_pair_matches_actual_interpolated_pose(self):
        sync,now=self.sync()
        verified=0
        for t in (1.21,1.23,1.27,1.31,1.41):
            now[0]=t
            for e in sync.advance(t):
                if e['kind']!='bot' or e['id']!=30:continue
                pair=e.get('presentation_bracket');self.assertEqual(PAIR,pair)
                p=ram_history.interpolate_pose(body(x=0.),body(x=4.),1000000,1200000,e['presentation_time_us'])
                self.assertAlmostEqual(p['x'],e['pose']['x'],places=5);verified+=1
        self.assertGreater(verified,0)
    def test_wreck_chase_cannot_change_live_bracket(self):
        a,na=self.sync(False);b,nb=self.sync(True)
        for t in (1.21,1.25,1.32,1.44):
            na[0]=nb[0]=t
            ea=[e for e in a.advance(t) if e['id']==30]
            eb=[e for e in b.advance(t) if e['id']==30]
            self.assertEqual(ea,eb)
    def test_teleport_exports_only_destination(self):
        s,n=self.sync();n[0]=1.21
        s.snapshot(dict(round_id=1,server_tick=3,bot_state_revision=103,
            bot_state_time_us=1210000,motion_time_us=1210000,players=[],bots=[body(x=500.)]))
        n[0]=1.22;events=s.advance(n[0]);e=next(e for e in events if e['id']==30)
        self.assertEqual(500.,e['pose']['x']);self.assertEqual([103,1210000,103,1210000],e['presentation_bracket'])
    def test_complete_roundtrip_json_keeps_pair_integer_exact(self):
        s,n=self.sync();n[0]=1.23
        e=next(e for e in s.advance(n[0]) if e['id']==30)
        pair=json.loads(json.dumps(e))['presentation_bracket']
        self.assertEqual(PAIR,pair);self.assertTrue(all(type(i)is int for i in pair))

class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.quiet=redirect_stdout(io.StringIO());self.quiet.__enter__()
    def tearDown(self):self.quiet.__exit__(None,None,None)
    def admit(self,s,rows):
        self.assertTrue(_update_player_input(s,1,ram_contacts=rows))
        return s.players[1]
    def test_real_server_admission_pins_before_worker(self):
        s=server();p=self.admit(s,[receipt()])
        self.assertEqual([1],list(p.ram_contacts));self.assertEqual({},p.ram_contact_rejections)
        self.assertEqual(30,p.ram_contacts[1]['ram_bot_state']['id'])
    def test_client_cannot_submit_canonical_pose(self):
        s=server();r=receipt();r['ram_bot_state']=body(x=200.)
        self.assertIsNone(s._normalize_ram_contact_envelope(s.players[1],r)[0])
    def test_client_false_time_is_explicitly_rejected_not_computed(self):
        s=server();r=receipt(bot_history_bracket=[100,1000001,102,1200000])
        p=self.admit(s,[r]);self.assertEqual([],list(p.ram_contacts))
        self.assertEqual('canonical_history_time_mismatch',p.ram_contact_rejections[1])
    def test_missing_local_history_still_computes_positive_damage(self):
        s=server();p=self.admit(s,[receipt()]);b,w=worker()
        raw=json.loads(json.dumps(dict(id=1,team=1,alive=True,ram_contacts=list(p.ram_contacts.values()))))
        decorated=b._decorate_ram_contacts(raw)
        self.assertEqual({},b._ram_bot_history)
        rows=w._resolve_human_ram_receipts([decorated],1.)
        self.assertEqual(1,len(rows));self.assertGreater(rows[0]['damage_to_bot'],0)
    def test_original_nearest_right_failure_cannot_block_new_receipt(self):
        s=server();b,w=worker()
        for rev,t in ((100,1000000),(101,1100000),(102,1200000)):
            b._remember_ram_bot_snapshot(dict(bot_state_revision=rev,bot_state_time_us=t,bots=[body()]))
        # Reproduce the original helper's failure, then exercise the new owner.
        self.assertIsNone(b._ram_bot_state_at(30,102,STAMP))
        p=self.admit(s,[receipt()]);raw=dict(id=1,alive=True,team=1,ram_contacts=list(p.ram_contacts.values()))
        out=w._resolve_human_ram_receipts([b._decorate_ram_contacts(raw)],1.)
        self.assertGreater(out[0]['damage_to_bot'],0)
    def test_16_in_flight_drain_after_all_display_histories_are_gone(self):
        s=server();p=self.admit(s,[receipt(i,30 if i%2 else 20) for i in range(1,17)])
        self.assertEqual(list(range(1,17)),list(p.ram_contacts));b,w=worker()
        # Destroy the original archive window AFTER successful admission.
        for rev in range(103,1000):s.ram_pose_archive.remember(rev,rev*100000,{30:body(),20:body(20)})
        before=s.bot_states[30]['health']+s.bot_states[20]['health'];damage=0
        for seq in range(1,17):
            raw=dict(id=1,alive=True,team=1,ram_contacts=list(p.ram_contacts.values()),
                ram_contact_resolved_seq=p.ram_contact_resolved_seq)
            rows=w._resolve_human_ram_receipts([b._decorate_ram_contacts(json.loads(json.dumps(raw)))],seq)
            self.assertEqual(seq,rows[0]['ram_contact_seq']);self.assertGreater(rows[0]['damage_to_bot'],0)
            message=dict(rows[0],type='bot_ram_report',round_id=s.round_id,authority_epoch=s.authority_epoch)
            self.assertTrue(s.report_bot_ram(SIMULATION_WORKER_AUTHORITY_ID,message))
            damage+=rows[0]['damage_to_bot'];self.assertEqual(seq,p.ram_contact_resolved_seq)
            # Retry must acknowledge without applying a second HP change.
            hp=s.bot_states[30]['health']+s.bot_states[20]['health']
            self.assertTrue(s.report_bot_ram(SIMULATION_WORKER_AUTHORITY_ID,message))
            self.assertEqual(hp,s.bot_states[30]['health']+s.bot_states[20]['health'])
        self.assertEqual([],list(p.ram_contacts));self.assertEqual(before-damage,hp)
        self.assertEqual({},p.ram_contact_rejections)
    def test_dead_head_finishes_then_live_next_damages(self):
        s=server();p=self.admit(s,[receipt(1,30),receipt(2,20)]);b,w=worker()
        w.states[30]['alive']=False;s.bot_states[30]['alive']=False;s.bot_states[30]['health']=0
        for seq in (1,2):
            raw=dict(id=1,alive=True,team=1,ram_contacts=list(p.ram_contacts.values()),ram_contact_resolved_seq=p.ram_contact_resolved_seq)
            row=w._resolve_human_ram_receipts([b._decorate_ram_contacts(raw)],seq)[0]
            self.assertEqual(seq,row['ram_contact_seq'])
            if seq==1:self.assertEqual(0,row['damage_to_bot'])
            else:self.assertGreater(row['damage_to_bot'],0)
            self.assertTrue(s.report_bot_ram(SIMULATION_WORKER_AUTHORITY_ID,dict(row,round_id=s.round_id)))
        self.assertEqual(2,p.ram_contact_resolved_seq)
    def test_no_time_based_discard_of_unavailable_live_evidence(self):
        b,w=worker();raw=dict(id=1,alive=True,team=1,ram_contacts=[dict(seq=1,bot_id=30)])
        for t in (0.,1.,3.,60.,300.):self.assertEqual([],w._resolve_human_ram_receipts([raw],t))
        self.assertEqual({},w._human_ram_receipt_seq);self.assertEqual({},w._human_ram_report_cache)
    def test_friend_ram_remains_zero_with_pinned_evidence(self):
        s=server();s.bot_states[30]['team']=1;s.ram_pose_archive.samples[100][1][30]['team']=1
        s.ram_pose_archive.samples[102][1][30]['team']=1
        p=self.admit(s,[receipt()]);b,w=worker();w.states[30]['team']=1
        row=w._resolve_human_ram_receipts([b._decorate_ram_contacts(dict(id=1,alive=True,team=1,ram_contacts=list(p.ram_contacts.values())))],1.)[0]
        self.assertEqual((0,0),(row['damage_to_bot'],row['damage_to_target']))
    def test_round_reset_discards_source_archive(self):
        s=server();s._reset_round();self.assertEqual(0,len(s.ram_pose_archive.samples))
    def test_damage_values_equal_existing_formula_without_changing_motor_state(self):
        s=server();p=self.admit(s,[receipt()]);b,w=worker()
        raw=dict(id=1,alive=True,team=1,ram_contacts=list(p.ram_contacts.values()))
        before=copy.deepcopy(w.states)
        out=w._resolve_human_ram_receipts([b._decorate_ram_contacts(raw)],1.)
        self.assertEqual(before,w.states)
        legacy=copy.deepcopy(raw);legacy['ram_contacts'][0]['_ram_contact_bot_state']=body()
        b2,w2=worker();expected=w2._resolve_human_ram_receipts([legacy],1.)
        self.assertEqual(out,expected)


class CaptureTests(unittest.TestCase):
    def test_first_impact_pair_survives_armour_retry_and_record_mutation(self):
        from test_port_0922_ram_contact_followup import RamCornerContactTests
        f=RamCornerContactTests();f.setUp();b=f.battle
        record=dict(network_id=29,presentation_time_us=1050000,presentation_bracket=list(PAIR))
        with mock.patch.object(b,'_native_ram_contact_plate_pair',return_value=(None,None,None)):
            self.assertFalse(b._queue_ram_contact_proof(record,f.player,f.bot,_Vector(1.45,1.,3.4),
                (0.,0.,8.),(0.,0.,0.),1300000,own_pose=(0,0,0,0,0,0),bot_pose=(2.9,0,6.8,0,0,0),
                player_ram_profile=dict(PROFILE),contact_normal=(0,-1),contact_y_span=(0,2)))
        record['presentation_time_us']=2000000;record['presentation_bracket'][:]=[200,2000000,201,2100000]
        b._ram_bot_revision_at=mock.Mock(side_effect=AssertionError('mutable history must not be queried'))
        with f.native_patch():self.assertTrue(b._retry_native_ram_contact_proof(1))
        result=b.local_ram_contact();self.assertEqual(PAIR,result['bot_history_bracket'])
        self.assertEqual(1050000,result['presentation_time_us']);self.assertEqual(8.,result['vz'])
    def test_actual_pose_write_keeps_matching_pair(self):
        b=BattleRuntime(_runtime());key='bot:30'
        b._records[key]=dict(ready=True,state={},kind='bot',network_id=30)
        b._apply_record_pose=lambda *args:None
        b._update_entity(dict(entity=key,kind='bot',id=30,interpolated=True,
            pose=dict(x=1.,y=0.,z=6.8,yaw=0.),presentation_time_us=STAMP,presentation_bracket=list(PAIR)))
        self.assertEqual(PAIR,b._records[key]['presentation_bracket'])
        self.assertEqual(STAMP,b._records[key]['presentation_time_us'])
    def test_actual_wreck_contact_is_witness_not_a_damage_target(self):
        b=BattleRuntime(_runtime());b._estimated_motion_time_us=lambda now:1300000
        b._queue_ram_contact_proof=mock.Mock(return_value=True)
        own=dict(x=0.,y=0.,z=0.,yaw=0.,vx=0.,vy=0.,vz=8.,shape=PLAYER_SHAPE,team=1,ram_profile=PROFILE)
        live=dict(body(),kind='bot',network_id=30,shape=SHAPE,vx=0.,vy=0.,vz=0.,
                  _record=dict(network_id=30,presentation_time_us=STAMP,presentation_bracket=PAIR),_vehicle=object())
        wreck=dict(live,network_id=21,id=21,alive=False,x=2.,z=0.)
        output=io.StringIO()
        with redirect_stdout(output):self.assertTrue(b._poll_local_ram_contact_episodes(object(),own,[wreck,live]))
        self.assertEqual(1,b._queue_ram_contact_proof.call_count)
        self.assertEqual(30,b._queue_ram_contact_proof.call_args.args[0]['network_id'])
        self.assertIn('(21, False, 2)',output.getvalue());self.assertIn('(30, True, 2)',output.getvalue())

if __name__=='__main__':unittest.main(verbosity=2)
