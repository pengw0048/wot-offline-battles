import contextlib
import copy
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'launcher'),str(ROOT/'server'),str(ROOT/'src/res/scripts/client')]
from gui.mods.offline_lan_0922 import bot_tactics as cfg, bot_tactics_runtime as planning, bot_gunnery, spg_positions
from bot_tactics_store import Store, ViewTransform, graph_data
from test_port_0922_spg_initial_positions import _graph, _states


def profile():
    raw=cfg.empty('Editor regression')
    raw['behavior']=[dict(team=0,class_tag='all',slot=-1,values={'reaction_seconds':2.5,'lead_error':0.0})]
    graph=_graph()
    points=graph['routes']['1'][0]['waypoints'][:4]
    raw['maps']['08_ruinberg']={
        'mode':'regular','resource_sha256':cfg.MAPS['08_ruinberg']['resource_sha256'],
        'routes':[dict(id='west',label='West city',team=1,classes=['heavyTank'],slots=[],policy='fixed',capacity=6,weight=1,
                       points=[[float(p[0]),float(p[1]),int(p[2])] for p in points])],
        'positions':[dict(id='north',label='North SPG',team=1,point=[-106.,346.],radius=34.,heading=-180.,priority=8),
                     dict(id='south',label='South SPG',team=2,point=[-78.,-346.],radius=50.,heading=0.,priority=8)]}
    return cfg.canonical(raw)


class TacticsContractTests(unittest.TestCase):
    def test_default_contains_no_hidden_parameter_retunes(self):
        raw=cfg.canonical(cfg.empty())
        self.assertEqual({},cfg.effective(raw,1,'SPG',3))
        self.assertEqual({},raw['maps'])

    def test_json_roundtrip_hash_and_input_are_stable(self):
        raw=profile();saved=copy.deepcopy(raw)
        self.assertEqual(raw,cfg.canonical(json.loads(cfg.dumps(raw))))
        self.assertEqual(cfg.digest(raw),cfg.digest(json.loads(cfg.dumps(raw))))
        self.assertEqual(saved,raw)

    def test_hierarchy_global_team_class_slot(self):
        raw=cfg.empty()
        raw['behavior']=[dict(team=1,class_tag='all',slot=2,values={'reaction_seconds':.3}),
            dict(team=1,class_tag='heavyTank',slot=-1,values={'reaction_seconds':.7}),
            dict(team=1,class_tag='all',slot=-1,values={'reaction_seconds':1.2}),
            dict(team=0,class_tag='all',slot=-1,values={'reaction_seconds':2.0})]
        raw=cfg.canonical(raw)
        self.assertEqual(.3,cfg.effective(raw,1,'heavyTank',2)['reaction_seconds'])
        self.assertEqual(.7,cfg.effective(raw,1,'heavyTank',3)['reaction_seconds'])
        self.assertEqual(1.2,cfg.effective(raw,1,'SPG',3)['reaction_seconds'])
        self.assertEqual(2,cfg.effective(raw,2,'SPG',3)['reaction_seconds'])

    def test_bad_versions_nonfinite_unknown_fields_and_resource_mismatch_rejected(self):
        for mutation in ('version','nan','bool','extra','resource','coords','spg_route','crew','slot'):
            raw=profile()
            if mutation=='version':raw['client']='1.10'
            elif mutation=='nan':raw['behavior'][0]['values']['reaction_seconds']=float('nan')
            elif mutation=='bool':raw['behavior'][0]['values']['reaction_seconds']=True
            elif mutation=='extra':raw['behavior'][0]['values']['ignore_walls']=True
            elif mutation=='resource':raw['maps']['08_ruinberg']['resource_sha256']='0'*64
            elif mutation=='coords':raw['maps']['08_ruinberg']['positions'][0]['point']=[999,999]
            elif mutation=='spg_route':raw['maps']['08_ruinberg']['routes'][0]['classes']=['invalid']
            elif mutation=='crew':raw['behavior'][0]['values']['crew_level']=99
            else:raw['behavior'][0]['slot']=5
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):cfg.canonical(raw)

    def test_source_map_metadata_matches_all_41_resources(self):
        self.assertEqual(41,len(cfg.MAPS))
        for name in cfg.MAPS:
            self.assertEqual(name,graph_data(name)['map'])

    def test_round_scope_leaves_unselected_maps_out_of_wire(self):
        raw=profile();g=_graph('35_steppes')
        raw['maps']['35_steppes']=dict(mode='regular',resource_sha256=cfg.MAPS['35_steppes']['resource_sha256'],routes=[],positions=[
            dict(id='p',label='P',team=1,point=[200,-350],radius=20,heading=0,priority=4)])
        subset=cfg.for_round(cfg.canonical(raw),'08_ruinberg')
        self.assertEqual({'08_ruinberg'},set(subset['maps']))
        subset['behavior'][0]['values'].clear();self.assertTrue(raw['behavior'][0]['values'])
        self.assertLess(len(cfg.dumps(subset)),65536)

    def test_store_draft_export_import_do_not_apply_implicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp);initial=store.active();raw=profile()
            store.save(raw);self.assertEqual(initial,store.active());self.assertEqual(raw,store.read(raw['name']))
            file=Path(tmp)/'export.json';store.export(raw,file)
            self.assertEqual(raw,store.import_file(file));store.save(raw,apply=True)
            self.assertEqual(raw,store.active());self.assertTrue(Path(str(store.active_path)+'.bak').exists())
            file.write_text('{bad')
            with self.assertRaises(ValueError):store.import_file(file)
            self.assertEqual(raw,store.active())

    def test_host_file_corruption_is_not_silently_defaulted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp);store.ensure_active();store.active_path.write_text('{bad')
            with self.assertRaises(ValueError):store.active()

    def test_profile_label_cannot_escape_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp);raw=cfg.empty('../../name');store.save(raw)
            self.assertEqual(store.profiles,store.profile_path(raw['name']).parent)

    def test_noncentred_map_roundtrip_and_resize(self):
        for width,height in [(750,600),(1024,700),(300,450)]:
            view=ViewTransform((-300,-300,400,400),width,height)
            view.zoom=1.7;view.pan_x=27;view.pan_y=-54
            for point in [(-300,400),(400,-300),(0,0),(70,346)]:
                actual=view.world(*view.screen(point))
                self.assertAlmostEqual(point[0],actual[0]);self.assertAlmostEqual(point[1],actual[1])

    def test_gunnery_override_changes_actual_permission_and_error(self):
        self.assertTrue(bot_gunnery.may_fire(1,1,1,1))
        self.assertFalse(bot_gunnery.may_fire(1,1,1,1,overrides={'reaction_seconds':3}))
        error=bot_gunnery.engagement_error(.34,1,2,('bot',3),0,{'lead_error':0})
        self.assertEqual(1,error['lead_scale'])
        self.assertEqual((0,0),bot_gunnery.aim_offset_metres(.34,error,.02,300,{'aim_bias_factor':0}))
        self.assertEqual(75,bot_gunnery.rating_parameters(1,{'crew_level':75})['crew_level'])

    def test_manual_plan_cannot_cross_team_zone_or_claim_clear_fire(self):
        raw=profile();g=_graph();states=_states(g,1)
        plans,outcomes=planning.assign_manual_positions(raw,'08_ruinberg',g,states)
        self.assertEqual(2,len(plans),outcomes)
        good=plans[states[0]['id']]
        self.assertIsNotNone(spg_positions.canonical_plan(good,team=1,tactics=raw))
        for mutation in ('team','source','hash','fire','position'):
            bad=copy.deepcopy(good)
            team=1
            if mutation=='team':team=2
            elif mutation=='source':bad['source']='official'
            elif mutation=='hash':bad['catalog']='bad'
            elif mutation=='fire':bad['fire_validation']='clear'
            else:bad['point']['x']=350
            with self.subTest(mutation=mutation):self.assertIsNone(spg_positions.canonical_plan(bad,team=team,tactics=raw))

    def test_manual_positions_are_mass_independent_but_size_checked_and_reserved(self):
        raw=profile();graph=_graph();states=_states(graph,3);old=copy.deepcopy(states)
        plans,outcomes=planning.assign_manual_positions(raw,'08_ruinberg',graph,states)
        self.assertEqual(6,len(plans),outcomes);self.assertEqual(old,states)
        for a in states:
            for b in states:
                if a['id']>=b['id'] or a['team']!=b['team']:continue
                pa,pb=plans[a['id']],plans[b['id']]
                distance=math.hypot(pa['point']['x']-pb['point']['x'],pa['point']['z']-pb['point']['z'])
                self.assertGreaterEqual(distance,pa['clearance']+pb['clearance']+3)

    def test_unreachable_manual_zone_does_not_teleport_or_pick_random_location(self):
        raw=profile();graph=_graph();graph['links']=[0]*len(graph['links'])
        states=_states(graph,1)
        plans,status=planning.assign_manual_positions(raw,'08_ruinberg',graph,states)
        self.assertFalse(plans);self.assertTrue(all(s=='manual_no_reachable_parking_space' for s in status.values()))

    def test_route_selection_consumes_classes_slots_capacity_and_fixed_policy(self):
        raw=profile();graph=_graph();states=_states(graph,2)
        for s in states:s['profile']={'class_tag':'heavyTank'}
        raw['maps']['08_ruinberg']['routes'][0]['capacity']=1
        routes,status=planning.assign_routes(raw,'08_ruinberg',graph,states,7)
        self.assertEqual(1,len(routes));self.assertEqual('user_west',next(iter(routes.values()))['id'])
        self.assertEqual('fixed',next(v for k,v in status.items() if k in routes))
        self.assertTrue(all(k<20 for k in routes));self.assertEqual((routes,status),planning.assign_routes(raw,'08_ruinberg',graph,states,7))

    def test_invalid_waypoint_is_not_silently_snapped_through_a_wall(self):
        raw=profile();g=_graph();grid=planning.graph_view('08_ruinberg',g)
        route=raw['maps']['08_ruinberg']['routes'][0]
        index=grid.closest((route['points'][1][0],0,route['points'][1][1]));g['heights_mm'][index]=None
        self.assertEqual('waypoint_unusable',planning.validate_route(grid,route))


class RoundFreezeTests(unittest.TestCase):
    def test_host_freezes_config_next_round_reloads_and_late_messages_match(self):
        from lan_battle_server import BattleState
        from test_port_0922_server_team_size import _attach_worker,_Connection,_hello
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()):
            store=Store(tmp);store.save(profile(),apply=True)
            state=BattleState(map_name='08_ruinberg',team_size=2,bot_tactics_path=store.ensure_active())
            worker=_attach_worker(state);player,error=state.add_player(_Connection(),('127.0.0.1',1001),_hello(1));self.assertIsNone(error)
            start,error=state.request_start(player.player_id);self.assertIsNone(error)
            first=copy.deepcopy(start['bot_tactics']);self.assertEqual(first,state.bot_tactics)
            new=profile();new['behavior'][0]['values']['reaction_seconds']=4;store.save(new,apply=True)
            state.phase='battle';self.assertEqual(first,state.current_battle_message()['bot_tactics'])
            state._reset_round();start,error=state.request_start(player.player_id);self.assertIsNone(error)
            self.assertEqual(4,start['bot_tactics']['behavior'][0]['values']['reaction_seconds'])
            stale=dict(start,bot_tactics=first)
            with mock.patch.object(worker,'offer_reliable',return_value=True) as offer,mock.patch.object(player,'offer_reliable',return_value=True):
                self.assertTrue(state.broadcast_loading_transition(stale))
            self.assertEqual(start['bot_tactics'],offer.call_args.args[0]['bot_tactics'])

    def test_corrupt_applied_file_refuses_start_without_loading(self):
        from lan_battle_server import BattleState
        from test_port_0922_server_team_size import _attach_worker,_Connection,_hello
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()):
            store=Store(tmp);path=store.ensure_active();Path(path).write_text('bad')
            state=BattleState(map_name='08_ruinberg',team_size=2,bot_tactics_path=path)
            _attach_worker(state);player,error=state.add_player(_Connection(),('127.0.0.1',1001),_hello(1))
            out,error=state.request_start(player.player_id)
            self.assertIsNone(out);self.assertEqual('invalid_bot_tactics',error);self.assertEqual('waiting',state.phase)


class RealRuntimeIntegrationTests(unittest.TestCase):
    def setUp(self):
        import test_port_0922_bot_runtime as harness
        self.harness=harness;self.saved={k:v for k,v in sys.modules.items() if k=='gui' or k.startswith('gui.')}
        self.module=harness._load();self.old=self.module.loadout.attribute_factors
        self.module.loadout.attribute_factors=harness._plain_attribute_factors

    def tearDown(self):
        self.module.loadout.attribute_factors=self.old
        for key in list(sys.modules):
            if key=='gui' or key.startswith('gui.'):sys.modules.pop(key,None)
        sys.modules.update(self.saved)

    def runtime(self):
        descriptor=self.harness._combat_descriptor();graph=_graph()
        def spawn(team,slot):
            row=graph['spawn_formations'][str(team)][slot];return tuple(row[:3]),row[3]
        return self.module.BotRuntime(1,descriptor_resolver=lambda unused:descriptor,
            ground_probe=lambda *args:0.,physics_ground_probe=lambda *args:0.,
            direction_probe=lambda *args:{'clear':True,'slope':0.},baked_graph=graph,spawn_resolver=spawn)

    def message(self):
        raw=profile();raw['behavior'][0]['values'].update(crew_level=75,skill='elite')
        return {'round_id':1,'map':'08_ruinberg','bot_authority_id':1,'bot_tactics':raw,
            'bots':[dict(id=11,team=1,slot=0,name='SPG',vehicle='test:SPG',profile=dict(class_tag='SPG',dominant_role='artillery',speed=12.,desired_range=650.,fire_range=1250.,shells=[])),
                    dict(id=12,team=1,slot=1,name='Heavy',vehicle='test:HT',profile=dict(class_tag='heavyTank',dominant_role='brawler',speed=12.,desired_range=100.,fire_range=600.,shells=[]))]}

    def test_full_worker_manifest_contains_authored_plan_route_skill_and_crew(self):
        rt=self.runtime();message=self.message()
        with contextlib.redirect_stdout(io.StringIO()):out=rt.battle_start(message)[0]
        byid={b['id']:b for b in out['bots']}
        self.assertEqual('launcher_manual_v1',byid[11]['spg_initial']['source'])
        self.assertEqual('user_west',byid[12]['route']['id'])
        self.assertEqual('elite',byid[11]['skill']);self.assertEqual(75,rt.bot_crew_level(11))
        self.assertEqual(2.5,rt._bot_behavior[11]['reaction_seconds'])
        gun=types.SimpleNamespace(current_dispersion_factor=1)
        target={'id':15,'target_kind':'bot','position':(10,0,10)}
        self.assertFalse(rt._gunner_ready(rt.states[11],gun,target,1.0))
        self.assertFalse(rt._gunner_ready(rt.states[11],gun,target,2.0))
        self.assertTrue(rt._gunner_ready(rt.states[11],gun,target,4.0))

    def test_manifest_server_orders_and_navigation_share_same_manual_goal(self):
        from lan_battle_server import BattleState
        rt=self.runtime();message=self.message()
        with contextlib.redirect_stdout(io.StringIO()):out=rt.battle_start(message)[0]
        server=BattleState(map_name='08_ruinberg');server.phase='battle';server.bot_authority_id=1
        server.bot_tactics=copy.deepcopy(message['bot_tactics']);server.bot_planner.tactics=server.bot_tactics;server.bot_planner.tactics_map='08_ruinberg'
        server.bot_roster=[{k:b[k] for k in ('id','team','slot','name')} for b in message['bots']]
        out['round_id']=server.round_id
        self.assertTrue(server.update_bot_manifest(1,out))
        orders=server.bot_planner.build_orders(server.bot_manifest,list(server.bot_states.values()),[],1.)['orders']
        plan=rt.states[11]['_spg_initial'];art=next(o for o in orders if o['id']==11)
        self.assertEqual(plan['point'],art['move_position'])
        heavy=next(o for o in orders if o['id']==12);self.assertEqual('user_west',heavy['route_id'])
        # Reconnect restores the published plan, never choosing a new one.
        restored=dict(message,bot_manifest=out['bots'])
        rt._prepare_initial_spg_positions(restored,True);rt._prepare_user_routes(restored,True)
        self.assertEqual(plan,rt.states[11]['_spg_initial'])
        self.assertEqual('user_west',rt.states[12]['route']['id'])

    def test_fixed_routes_are_not_rebalanced_to_a_different_lane(self):
        from server_bot_ai import BotPlanner
        from test_port_0922_server_bot_ai import _bot,_route,_state,_contact
        planner=BotPlanner();planner.tactics=profile();planner.tactics_map='08_ruinberg'
        west=_route('user_west',[(-100,300,False),(-100,-250,False)])
        east=_route('other',[(200,300,False),(200,-250,False)])
        bots=[_bot(11,1,0,west),_bot(12,1,1,west),_bot(13,1,2,east)]
        for b in bots:b['profile']['class_tag']='heavyTank'
        states=[_state(b['id'],1,-100,250) for b in bots]
        # Direct planner route stage is driven by a visible enemy contact, not omniscient state.
        live=planner._alive_bots(bots,states,planner.tactics)
        planner._rebalance_routes(1,live,[dict(id=9,position=dict(x=200,y=0,z=0),health=100,max_health=100)],10.)
        for i in (11,12):self.assertEqual('user_west',planner._route_assignments[i]['route']['id'])


if __name__=='__main__':unittest.main()
