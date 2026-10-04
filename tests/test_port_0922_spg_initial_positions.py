"""Initial SPG plans: actual source cells and real #1513 baked graph tests.

Native map ray tracing/gameplay is not simulated as successful by these tests.
"""
import contextlib
import copy
import importlib.util
import io
import json
import math
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'server'), str(ROOT/'src/res/scripts/client')]
from gui.mods.offline_lan_0922 import spg_positions as positions
from server_bot_ai import BotPlanner
from test_port_0922_server_bot_ai import _bot, _route, _state, _contact
import test_port_0922_bot_runtime as harness


def _graph(name='08_ruinberg'):
    return json.loads((ROOT/'navgraphs'/('%s.json' % name)).read_text())


def _states(graph, count=3):
    result = []
    for team in (1, 2):
        for slot in range(count):
            x, y, z, yaw = graph['spawn_formations'][str(team)][slot]
            result.append(dict(id=team*10+slot, team=team, slot=slot, vehicle='test:SPG',
                               profile={'class_tag':'SPG'}, collision_shape=(1.8,4.,-.8,2.),
                               x=x,y=y,z=z,yaw=yaw,speed=0.0,alive=True))
    return result


def _example_plan():
    return {'schema':1, 'catalog':positions.CATALOG['revision'], 'map':'08_ruinberg',
            'mode':'regular','side':'north','zone':'north_safe_backfield','cell':'A4',
            'source':'guru_ruinberg_archived','vehicle':'test:SPG',
            'point':{'x':-106.,'y':3.406,'z':346.},
            'face':{'x':-86.,'y':3.406,'z':-290.}, 'radius':2.0,'clearance':6.88634244,
            'geometry':'baked_checked','fire_validation':'runtime_required'}


class CatalogSchemaTests(unittest.TestCase):
    def test_grid_uses_native_noncentred_bounds(self):
        self.assertEqual((-300.,330.,-230.,400.), positions.cell_bounds('A1',(-300,-300,400,400)))
        self.assertEqual((330.,-300.,400.,-230.), positions.cell_bounds('K0',(-300,-300,400,400)))

    def test_rows_skip_i_and_column_zero_is_tenth(self):
        self.assertEqual(8,positions.ROWS.index('J'))
        self.assertEqual(9,positions.COLUMNS.index('0'))
        for cell in ('I1','A10','L1','A-1','A١'):
            with self.subTest(cell=cell), self.assertRaises(ValueError):
                positions.cell_bounds(cell,(-500,-500,500,500))

    def test_data_has_only_actual_sourced_maps_not_forty_one_fabricated_entries(self):
        self.assertEqual({'08_ruinberg','35_steppes'},set(positions.CATALOG['maps']))
        self.assertEqual('community_guide_mirror', positions.CATALOG['sources']['guru_ruinberg_archived']['kind'])
        self.assertTrue(positions.validate_catalog(positions.CATALOG))

    def test_catalog_rejects_unknown_source_duplicate_zone_and_wrong_version(self):
        for change in ('source','duplicate','version'):
            data=copy.deepcopy(positions.CATALOG)
            if change=='source':data['maps']['08_ruinberg']['zones'][0]['source']='invented'
            elif change=='duplicate':data['maps']['08_ruinberg']['zones'].append(data['maps']['08_ruinberg']['zones'][0])
            else:data['game_version']='1.10'
            with self.subTest(change=change), self.assertRaises(ValueError):positions.validate_catalog(data)

    def test_spawn_side_is_from_bases_not_team_id(self):
        self.assertEqual('north',positions.side_for_team(1,_graph('08_ruinberg')['bases']))
        self.assertEqual('south',positions.side_for_team(1,_graph('35_steppes')['bases']))
        self.assertEqual('east',positions.side_for_team(2,[(-200,0),(200,0)]))
        self.assertIsNone(positions.side_for_team(1,[(0,0),(0,0)]))

    def test_canonical_plan_survives_json_and_does_not_alias_input(self):
        raw=_example_plan()
        result=positions.canonical_plan(json.loads(json.dumps(raw)),'08_ruinberg','test:SPG')
        self.assertEqual(raw,result)
        result['point']['x']=0.
        self.assertEqual(-106.,raw['point']['x'])

    def test_wire_rejects_nonfinite_foreign_map_vehicle_and_outside_source_cell(self):
        for change in ('nan','map','vehicle','cell','source','radius','extra','claimed_clear_fire'):
            raw=_example_plan()
            if change=='nan':raw['point']['x']=float('nan')
            elif change=='map':raw['map']='35_steppes'
            elif change=='vehicle':raw['vehicle']='other:SPG'
            elif change=='cell':raw['point']['x']=350.
            elif change=='source':raw['source']='official_guaranteed'
            elif change=='radius':raw['radius']=15.
            elif change=='extra':raw['fire_allowed']=True
            else:raw['fire_validation']='already_clear'
            with self.subTest(change=change):
                self.assertIsNone(positions.canonical_plan(raw,'08_ruinberg','test:SPG'))

    def test_plan_cannot_be_reused_on_the_opposite_team(self):
        self.assertIsNotNone(positions.canonical_plan(_example_plan(),team=1))
        self.assertIsNone(positions.canonical_plan(_example_plan(),team=2))
        self.assertIsNone(positions.canonical_plan(_example_plan(),team=True))
        bad=_example_plan();bad['schema']=True
        self.assertIsNone(positions.canonical_plan(bad))

    def test_all_map_audit_does_not_count_resource_manifest_as_a_map(self):
        spec = importlib.util.spec_from_file_location(
            'spg_catalog_audit', ROOT / 'tools/spg_position_catalog.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        report = module.audit_graphs(positions.CATALOG)
        self.assertEqual(41, len(report['maps']))
        self.assertNotIn('manifest', {row['map'] for row in report['maps']})
        self.assertEqual(39, sum(row['status'] == 'source_not_catalogued'
                                 for row in report['maps']))
        self.assertEqual(12, sum(len(row.get('plans', {})) for row in report['maps']))

    def test_generated_runtime_module_matches_authoring_json(self):
        spec=importlib.util.spec_from_file_location('spg_catalog_tool',ROOT/'tools/spg_position_catalog.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        source=(ROOT/'spg_positions/positions_0922.json').read_text()
        bundled=(ROOT/'src/res/scripts/client/gui/mods/offline_lan_0922/spg_position_data.py').read_text()
        self.assertEqual(module.generated(source),bundled)


class RealGraphInitialSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases={}
        for name in ('08_ruinberg','35_steppes'):
            graph=_graph(name); states=_states(graph)
            old=copy.deepcopy(states)
            plans,outcomes=positions.assign_initial_positions(name,graph,states)
            cls.cases[name]=(graph,states,plans,outcomes,old)

    def test_both_real_graphs_allocate_three_distinct_plans_per_side(self):
        for name,(graph,states,plans,outcomes,old) in self.cases.items():
            self.assertEqual({s['id']:'assigned' for s in states},outcomes,name)
            self.assertEqual(6,len(plans))
            self.assertEqual(old,states,'selection must not move spawns or mutate routes')
            for team in (1,2):
                group=[plans[s['id']] for s in states if s['team']==team]
                for i,a in enumerate(group):
                    for b in group[i+1:]:
                        self.assertGreaterEqual(math.hypot(a['point']['x']-b['point']['x'],a['point']['z']-b['point']['z']),
                                                a['clearance']+b['clearance']+positions.RESERVATION_GAP-1e-8)

    def test_every_plan_is_an_existing_safe_graph_point_inside_the_cited_cell(self):
        for name,(graph,states,plans,outcomes,old) in self.cases.items():
            grid=positions._Graph(graph,positions.CATALOG['maps'][name]['bounds'])
            for plan in plans.values():
                point=tuple(plan['point'][v] for v in ('x','y','z'))
                self.assertTrue(positions.point_in_cell(point,plan['cell'],graph['bounds'],plan['radius']))
                index=grid.closest(point)
                self.assertEqual(point,grid.point(index))
                self.assertTrue(grid.parking_clear(index,plan['clearance']))
                self.assertEqual('runtime_required',plan['fire_validation'])

    def test_missing_maps_and_training_keep_explicit_no_library_outcome(self):
        states=self.cases['08_ruinberg'][1]
        for name,mode,reason in [('01_karelia','regular','source_not_catalogued'),('08_ruinberg','training','unsupported_mode')]:
            plans,outcomes=positions.assign_initial_positions(name,None,states,mode)
            self.assertEqual({},plans)
            self.assertEqual({s['id']:reason for s in states},outcomes)

    def test_blocked_sourced_areas_do_not_pick_an_unrelated_ordinary_route(self):
        graph=_graph();state=_states(graph,1)[0]
        state['route']={'waypoints':[(0,0,False),(-100,300,True)]}
        graph['heights_mm']=[None]*len(graph['heights_mm'])
        plans,outcomes=positions.assign_initial_positions('08_ruinberg',graph,[state])
        self.assertEqual({},plans)
        self.assertEqual('no_safe_sourced_cell',outcomes[state['id']])

    def test_safe_but_disconnected_cells_are_not_admitted(self):
        graph=_graph();graph['links']=[0]*len(graph['links'])
        state=_states(graph,1)[0]
        plans,outcomes=positions.assign_initial_positions('08_ruinberg',graph,[state])
        self.assertEqual({},plans)
        self.assertEqual('no_reachable_unreserved_sourced_cell',outcomes[state['id']])

    def test_wrong_baked_game_version_and_bounds_fail_locally(self):
        for field,value in [('game_version','1.10'),('bounds',[-500,-500,500,500])]:
            graph=_graph();states=_states(graph,1);graph[field]=value
            plans,outcomes=positions.assign_initial_positions('08_ruinberg',graph,states)
            self.assertEqual({},plans)
            self.assertTrue(all(v=='incompatible_graph' for v in outcomes.values()))

    def test_no_enemy_information_is_needed_and_non_spgs_are_untouched(self):
        self.assertEqual(({},{}),positions.assign_initial_positions('08_ruinberg',None,[{'id':99,'profile':{'class_tag':'AT-SPG'}}]))

    def test_missing_graph_and_bad_footprint_do_not_raise_or_move_any_body(self):
        graph,states,unused_plans,unused_outcomes,old=self.cases['08_ruinberg']
        self.assertEqual({},positions.assign_initial_positions('08_ruinberg',None,states)[0])
        invalid=copy.deepcopy(states[0]);invalid['collision_shape']=(float('nan'),4,0,2)
        plans,why=positions.assign_initial_positions('08_ruinberg',graph,[invalid])
        self.assertEqual({},plans)
        self.assertEqual('vehicle_footprint_unavailable',why[invalid['id']])
        self.assertEqual(old,states)

    def test_reachability_does_not_snap_spawn_through_a_wall(self):
        graph=_graph();grid=positions._Graph(graph,graph['bounds'])
        state=_states(graph,1)[0];origin=tuple(state[k] for k in ('x','y','z'))
        start=grid.closest(origin);self.assertIsNotNone(start)
        graph['heights_mm'][start]=None
        self.assertIsNone(grid.closest(origin))
        self.assertEqual({},grid.distances(origin))

    def test_conditioned_east_corner_is_not_selected_without_support(self):
        plans=self.cases['08_ruinberg'][2]
        self.assertTrue(all(p['zone']!='north_east_flank' for p in plans.values()))
        zone=next(z for z in positions.CATALOG['maps']['08_ruinberg']['zones'] if z['id']=='north_east_flank')
        friends=[{'id':i,'team':1,'profile':{'class_tag':'mediumTank'},'route':{'waypoints':[(0,300),(180,100)]}} for i in range(3)]
        self.assertTrue(positions._has_initial_support(zone,1,friends,(-400,-400,400,400)))
        self.assertFalse(positions._has_initial_support(zone,1,friends[:2],(-400,-400,400,400)))


class InitialPositionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.saved={k:v for k,v in sys.modules.items() if k=='gui' or k.startswith('gui.')}
        self.module=harness._load()
        self.old=self.module.loadout.attribute_factors
        self.module.loadout.attribute_factors=harness._plain_attribute_factors

    def tearDown(self):
        self.module.loadout.attribute_factors=self.old
        for key in list(sys.modules):
            if key=='gui' or key.startswith('gui.'):sys.modules.pop(key,None)
        sys.modules.update(self.saved)

    def _runtime(self):
        descriptor=harness._combat_descriptor()
        graph=_graph()
        def spawn(team,slot):
            row=graph['spawn_formations'][str(team)][slot]
            return tuple(row[:3]),row[3]
        runtime=self.module.BotRuntime(
            1,descriptor_resolver=lambda unused:descriptor,
            ground_probe=lambda *args:0.,physics_ground_probe=lambda *args:0.,
            direction_probe=lambda *args:{'clear':True,'slope':0.},baked_graph=graph,spawn_resolver=spawn)
        return runtime

    def _fixture_bot(self,distance):
        plan=_example_plan(); route=_route('ordinary',[(0,300,False),(0,100,True),(0,-300,False)])
        bot=_bot(11,1,0,route)
        bot.update(vehicle='test:SPG',spg_initial=plan)
        state=_state(11,1,plan['point']['x']+distance,plan['point']['z'])
        return plan,bot,state

    def test_server_initial_goal_is_library_point_not_cached_route_point(self):
        plan,bot,state=self._fixture_bot(14.)
        planner=BotPlanner()
        planner._artillery_anchors[11]={'point':{'x':0.,'y':0.,'z':0.}}
        orders=planner.build_orders([bot],[state],[],1.)['orders']
        self.assertEqual(plan['point'],orders[0]['move_position'])
        self.assertEqual('artillery_deploy',orders[0]['combat_mode'])
        self.assertIsNone(orders[0]['throttle_override'])
        self.assertIsNone(orders[0]['target_id'])
        self.assertFalse(orders[0]['fire_allowed'])

    def test_server_and_worker_do_not_stop_at_fifteen_metres(self):
        runtime=self._runtime()
        for distance,mode,throttle in [(14.,'artillery_deploy',None),(2.1,'artillery_deploy',None),(1.8,'artillery_hold',0.)]:
            plan,bot,state=self._fixture_bot(distance)
            order=BotPlanner().build_orders([bot],[state],[],1.)['orders'][0]
            self.assertEqual(mode,order['combat_mode'])
            state.update(_spg_initial=plan,profile={'class_tag':'SPG'},route={'waypoints':[]})
            local=runtime._artillery_position_order(state,order,{},1.)
            self.assertEqual(mode,local['combat_mode'])
            self.assertEqual(throttle,local['throttle_override'])
            self.assertEqual(plan['point']['x'],local['move_position'][0])

    def test_firing_target_and_radio_restrictions_remain_separate(self):
        plan,bot,state=self._fixture_bot(1.)
        player={'id':2,'team':2,'alive':True}
        planner=BotPlanner()
        contact=_contact(2,0.,0.,[])
        planner.report_contacts([contact],planner.known_targets([state],[player]),1.)
        order=planner.build_orders([bot],[state],[player],1.)['orders'][0]
        self.assertEqual(2,order['target_id'])
        self.assertTrue(order['fire_allowed'])  # attempt only, existing exact arc mandatory
        self.assertEqual(plan['point'],order['move_position'])
        planner.report_contacts([dict(contact,radio_recipients=[])],planner.known_targets([state],[player]),2.)
        order=planner.build_orders([bot],[state],[player],2.)['orders'][0]
        self.assertIsNone(order['target_id'])
        self.assertFalse(order['fire_allowed'])

    def test_worker_does_not_override_library_with_legacy_near_wall_route_point(self):
        runtime=self._runtime();plan,bot,state=self._fixture_bot(0.)
        state.update(_spg_initial=plan,profile={'class_tag':'SPG'},
                     route={'waypoints':[(0,100),(0,200),(0,400)]},
                     _spg_obstruction={'since':0.,'last':10.},_spg_position={'point':(0.,0.,0.)})
        order={'combat_mode':'artillery_hold','target_id':2,'fire_allowed':False,
               'move_position':plan['point']}
        result=runtime._artillery_position_order(state,order,{2:{}},10.)
        self.assertEqual(tuple(plan['point'][v] for v in ('x','y','z')),result['move_position'])
        self.assertNotIn('_spg_position',state)
        self.assertFalse(result['fire_allowed'])
        self.assertEqual('library_position_fire_obstructed',state['_spg_position_event'])

    def test_explicit_base_defense_preempts_library_goal(self):
        runtime=self._runtime();plan,bot,state=self._fixture_bot(0.)
        state.update(_spg_initial=plan,profile={'class_tag':'SPG'})
        order={'combat_mode':'base_defense','move_position':(0.,0.,0.)}
        self.assertEqual(order,runtime._artillery_position_order(state,order,{},1.))

    def test_worker_manifest_contains_plan_and_restore_does_not_reroll(self):
        runtime=self._runtime()
        message={'round_id':7,'map':'08_ruinberg','battle_mode':'regular','bot_authority_id':1,
                 'bots':[{'id':11,'team':1,'slot':0,'vehicle':'test:SPG','name':'SPG',
                          'profile':{'class_tag':'SPG','dominant_role':'artillery','speed':12.,'desired_range':650.,'fire_range':1250.,'shells':[]}}]}
        with contextlib.redirect_stdout(io.StringIO()):out=runtime.battle_start(message)[0]
        self.assertIn('spg_initial',out['bots'][0])
        plan=out['bots'][0]['spg_initial']
        self.assertEqual(plan,runtime.states[11]['_spg_initial'])
        snapshot=copy.deepcopy(runtime.states[11])
        message['bot_manifest']=json.loads(json.dumps(out['bots']))
        with mock.patch.object(self.module.spg_positions,'assign_initial_positions',side_effect=AssertionError('do not reroll')):
            runtime._prepare_initial_spg_positions(message,True)
        self.assertEqual('restored',runtime.states[11]['_spg_initial_status'])
        self.assertEqual(plan,runtime._manifest_entry(runtime.states[11])['spg_initial'])
        self.assertEqual([snapshot[k] for k in ('x','y','z','yaw')],[runtime.states[11][k] for k in ('x','y','z','yaw')])

    def test_real_server_admits_plan_into_canonical_manifest(self):
        from lan_battle_server import BattleState
        runtime=self._runtime()
        message={'round_id':1,'map':'08_ruinberg','bot_authority_id':1,'bots':[
            {'id':11,'team':1,'slot':0,'vehicle':'test:SPG','name':'SPG',
             'profile':{'class_tag':'SPG','dominant_role':'artillery','speed':12.,'desired_range':650.,'fire_range':1250.,'shells':[]}}]}
        with contextlib.redirect_stdout(io.StringIO()):out=runtime.battle_start(message)[0]
        server=BattleState(map_name='08_ruinberg')
        server.phase='battle';server.bot_authority_id=1
        server.bot_roster=[{'id':11,'team':1,'slot':0,'name':'SPG'}]
        out['round_id']=server.round_id
        self.assertTrue(server.update_bot_manifest(1,out))
        self.assertEqual(out['bots'][0]['spg_initial'],server.bot_manifest[0]['spg_initial'])
        order=server.bot_planner.build_orders(server.bot_manifest,list(server.bot_states.values()),[],1.)['orders'][0]
        self.assertEqual(out['bots'][0]['spg_initial']['point'],order['move_position'])

    def test_navigation_goal_key_survives_target_changes(self):
        runtime=self._runtime();plan,bot,state=self._fixture_bot(40.)
        state.update(_spg_initial=plan,now=1.,profile={'class_tag':'SPG'})
        runtime.states={11:state}
        keys=[]
        def next_target(bot_id,position,goal,path_key,*args,**kwargs):
            keys.append(path_key);return goal
        runtime.navigator=types.SimpleNamespace(
            grid=types.SimpleNamespace(cell_size=4.,dry_segment_clear=lambda *args:False),
            next_target=next_target,target_is_terminal=lambda actor:True)
        origin=tuple(state[k] for k in ('x','y','z'))
        goal=tuple(plan['point'][k] for k in ('x','y','z'))
        for target in (2,3,None):
            order={'combat_mode':'artillery_deploy','route_index':0,'target_id':target}
            self.assertEqual(goal,runtime._navigation_target(11,origin,goal,order,{'id':11,'now':1.,'speed':0.}))
        self.assertEqual([keys[0]]*3,keys)
        self.assertEqual('spg_initial',keys[0][0])

    def test_server_worker_adapter_driver_continue_inside_old_fifteen_metre_radius(self):
        runtime=self._runtime();plan,bot,state=self._fixture_bot(0.)
        state.update(z=plan['point']['z']-14.,_spg_initial=plan,profile={'class_tag':'SPG'})
        runtime.states={11:state}
        runtime.navigator=types.SimpleNamespace(
            grid=types.SimpleNamespace(cell_size=4.,dry_segment_clear=lambda *args:True),
            observe_direct_target=lambda bot_id,position,goal,*args:goal,
            target_is_terminal=lambda actor:True)
        runtime.adapter=self.module.BotAdapter('08_ruinberg',1,navigation_target=runtime._navigation_target)
        strategic=BotPlanner().build_orders([bot],[state],[],1.)['orders'][0]
        strategic=runtime._artillery_position_order(state,strategic,{},1.)
        projected={'id':11,'slot':0,'position':tuple(state[k] for k in ('x','y','z')),
                   'yaw':0.,'speed':0.,'dt':.1,'now':1.,'contacts':[],'neighbours':[],
                   'half_length':4.,'half_width':1.8,'stopping_distance':0.}
        command=runtime.adapter.decide_with_order(projected,strategic,lambda yaw:True)
        self.assertEqual('artillery_deploy',strategic['combat_mode'])
        self.assertGreater(command['throttle'],0.)
        self.assertFalse(command['fire_allowed'])
        self.assertTrue(projected['navigation_stop_at_target'])

    def test_malformed_optional_plan_is_contained_by_real_server(self):
        from lan_battle_server import BattleState
        runtime=self._runtime()
        message={'round_id':1,'map':'08_ruinberg','bot_authority_id':1,'bots':[
            {'id':11,'team':1,'slot':0,'vehicle':'test:SPG','name':'SPG',
             'profile':{'class_tag':'SPG','dominant_role':'artillery','speed':12.,'desired_range':650.,'fire_range':1250.,'shells':[]}}]}
        with contextlib.redirect_stdout(io.StringIO()):out=runtime.battle_start(message)[0]
        for change in ('wrong_cell','wrong_mode','wrong_side'):
            outgoing=copy.deepcopy(out)
            server=BattleState(map_name='08_ruinberg')
            server.phase='battle';server.bot_authority_id=1
            server.bot_roster=[{'id':11,'team':1,'slot':0,'name':'SPG'}]
            outgoing['round_id']=server.round_id
            if change=='wrong_cell':outgoing['bots'][0]['spg_initial']['point']['x']=999.
            elif change=='wrong_mode':server.battle_mode='training'
            else:outgoing['bots'][0]['spg_initial']['side']='south'
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(server.update_bot_manifest(1,outgoing))
            self.assertNotIn('spg_initial',server.bot_manifest[0])
            self.assertEqual(1,len(server.bot_states))

    def test_actual_driver_still_approaches_at_fourteen_metres_without_global_changes(self):
        from gui.mods.offline_lan_0922.ai.driver import LocalDriver
        command=LocalDriver().drive(11,0,(0.,0.,0.),0.,0.,.1,(0.,0.,14.),[],lambda yaw:True,
                                    movement_intent=True,stop_at_target=True,stopping_distance=0.)
        self.assertGreater(command['throttle'],0.)
        self.assertNotEqual('arrived',command['recovery_mode'])
        stopped=LocalDriver().drive(11,0,(0.,0.,0.),0.,0.,.1,(0.,0.,1.),[],lambda yaw:True,
                                    movement_intent=True,stop_at_target=True,stopping_distance=0.)
        self.assertEqual(0.,stopped['throttle'])


if __name__=='__main__':unittest.main()
