"""v0.8.0 driving rollback contracts; not native game-play or an FPS test."""
from __future__ import print_function
import copy
import hashlib
import json
import math
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.environ.get('BOT080_CLIENT_SOURCE', os.path.join(ROOT, 'src', 'res', 'scripts', 'client'))
if 'gui.mods.offline_lan_0922.ai.navigation' not in sys.modules:
    sys.path.insert(0, SCRIPTS)
    for name, relative in (('gui', 'gui'), ('gui.mods', 'gui/mods')):
        if name not in sys.modules:
            package = types.ModuleType(name)
            package.__path__ = [os.path.join(SCRIPTS, *relative.split('/'))]
            sys.modules[name] = package
    sys.modules['gui'].mods = sys.modules['gui.mods']
from gui.mods.offline_lan_0922.ai import navigation as navmod
from gui.mods.offline_lan_0922 import destructibles_sensor as sensor
TerrainGrid, TerrainNavigator = navmod.TerrainGrid, navmod.TerrainNavigator

REMOVED = ('review_native_corridor', '_native_edge_clear', 'invalidate_native_review',
    '_native_review_cells', '_native_review_edges', '_native_review_evidence',
    '_native_review_order', '_native_proof_revision', 'report_blocked_plan',
    '_report_blocked_planner', 'set_destructible_regions', '_soft_cell_height',
    '_soft_graph_cells', '_soft_graph_edge', '_publish_live_edge', '_live_links',
    '_live_heights', '_live_edge_count', 'navigation_structure_regions',
    'navigation_structure_provider', 'live_shortcut_clear')
RESULTS = {'maps': [], 'airfield_entries': [], 'native_gameplay_tested': False}

def read_json(path):
    with open(path, 'rb') as stream:
        return json.loads(stream.read().decode('utf-8'))

def graph_sample(holes=()):
    w = 15
    heights = [0] * (w*w)
    for x,z in holes:
        heights[z*w+x] = None
    links = []
    for z in range(w):
        for x in range(w):
            mask = 0
            if heights[z*w+x] is not None:
                for bit,(dx,dz,length) in enumerate(TerrainGrid._NEIGHBOURS):
                    xx,zz = x+dx,z+dz
                    if 0 <= xx < w and 0 <= zz < w and heights[zz*w+xx] is not None:
                        if dx and dz and (heights[z*w+xx] is None or heights[zz*w+x] is None):
                            continue
                        mask |= 1 << bit
            links.append(mask)
    return {'format':navmod.BAKED_FORMAT_NAME,'version':navmod.BAKED_FORMAT_VERSION,
        'width':w,'height':w,'origin':[0.,0.],'cell_size':4.,
        'bounds':[-2.,-2.,58.,58.],'heights_mm':heights,'links':links,
        'hazards':[0]*(w*w),'bake':{'max_grade':.3}}

def forbid(calls):
    def probe(*args):
        calls.append(args)
        raise AssertionError('Static baked navigation invoked native probe')
    return probe

class ImmutableNavigationTests(unittest.TestCase):
    def make(self, graph=None):
        calls=[];probe=forbid(calls)
        navigator=TerrainNavigator(probe, probe, baked_graph=copy.deepcopy(graph or graph_sample()))
        return navigator,calls

    def test_four_driving_modules_match_the_verified_v080_source(self):
        proof=read_json(os.path.join(ROOT,'docs/testing/airfield-build-inputs-20260923.json'))
        for path,expected in proof['bot080_rollback']['core_sha256'].items():
            with open(os.path.join(ROOT,*path.split('/')),'rb') as stream:
                self.assertEqual(expected,hashlib.sha256(stream.read()).hexdigest(),path)

    def test_second_traffic_and_rotation_veto_owners_are_absent(self):
        from gui.mods.offline_lan_0922.ai.traffic import TrafficCoordinator
        from gui.mods.offline_lan_0922.ai.driver import LocalDriver
        self.assertFalse(hasattr(TrafficCoordinator,'safe_controls'))
        self.assertFalse(hasattr(LocalDriver,'wait_for_navigation'))
        source=os.path.join(ROOT,'src','res','scripts','client','gui','mods','offline_lan_0922','bot_runtime.py')
        with open(source,'rb') as stream: text=stream.read().decode('utf-8')
        for symbol in ('_blocked_rotations','sample_rotation_clear','traffic_obstacles','_settled_navigation_feedback'):
            self.assertNotIn(symbol,text)

    def test_removed_mechanism_has_no_methods_or_state(self):
        navigator,calls=self.make()
        for name in REMOVED:
            self.assertFalse(hasattr(navigator,name),name)
            self.assertFalse(hasattr(navigator.grid,name),name)
        self.assertFalse(hasattr(sensor,'navigation_structure_regions'))

    def test_all_runtime_callers_are_removed_from_source(self):
        for base,dirs,names in os.walk(os.path.join(ROOT,'src')):
            for name in names:
                if name.endswith('.py'):
                    path=os.path.join(base,name)
                    with open(path,'rb') as stream: source=stream.read().decode('utf-8')
                    for symbol in REMOVED:
                        self.assertNotIn(symbol,source,(path,symbol))

    def test_missing_cells_and_links_stay_missing(self):
        graph=graph_sample([(7,z) for z in range(15)])
        navigator,calls=self.make(graph);grid=navigator.grid
        before=copy.deepcopy((grid._baked_heights,grid._baked_links))
        self.assertIsNone(grid._baked_cell_height((7,7)))
        self.assertFalse(grid.segment_clear((8.,0.,28.),(48.,0.,28.)))
        path=grid.plan((8.,0.,28.),(48.,0.,28.),max_expansions=2000)
        self.assertTrue(not path or path[-1][0] < 28.)
        self.assertEqual(before,(grid._baked_heights,grid._baked_links))
        self.assertFalse(calls)

    def test_open_baked_segments_ignore_a_false_native_wall(self):
        navigator,calls=self.make();grid=navigator.grid
        for start,end in [((8.,0.,8.),(44.,0.,44.)),((8.4,0.,8.3),(9.2,0.,8.7))]:
            self.assertTrue(grid.segment_clear(start,end))
        self.assertFalse(grid.path_has_penalty([(8.,0.,8.),(44.,0.,44.)],1.))
        self.assertFalse(calls)

    def test_world_boundary_is_not_disabled(self):
        navigator,calls=self.make()
        self.assertFalse(navigator.grid.segment_clear((56.,0.,8.),(60.,0.,8.)))

    def test_wreck_cost_and_removal_do_not_rewrite_static_links(self):
        navigator,calls=self.make();grid=navigator.grid
        start,end=(20.,0.,28.),(36.,0.,28.)
        original=list(grid._baked_links)
        self.assertTrue(grid.set_static_hulls([(123,28.,28.,0.,3.5,1.8)]))
        self.assertTrue(grid.path_crosses_static_hull([start,end]))
        self.assertGreater(grid.segment_penalty(start,end,1.),0.)
        self.assertFalse(grid.dry_segment_clear(start,end,1.))
        grid.set_static_hulls([])
        self.assertEqual(0.,grid.segment_penalty(start,end,2.))
        self.assertEqual(original,grid._baked_links)
        self.assertFalse(calls)

    def test_actual_contact_penalty_stays_local_to_one_bot(self):
        navigator,calls=self.make();grid=navigator.grid
        a,b=(20.,0.,28.),(24.,0.,28.)
        navigator.bot_states[101]={'blocked_step_replans':0,'replan_generation':0}
        results=[navigator.report_blocked_step(101,a,b,t) for t in (0.,.3,.6,.9,1.2)]
        self.assertTrue(any(results));self.assertTrue(navigator.bot_failed_edges[101])
        self.assertFalse(navigator.bot_failed_edges.get(102))
        self.assertFalse(grid.path_has_penalty([a,b],2.))
        self.assertFalse(calls)



    def test_shallow_water_cost_not_removed(self):
        graph=graph_sample();graph['hazards'][7*15+7]=navmod.BAKED_SHALLOW_WATER
        navigator,calls=self.make(graph)
        self.assertFalse(navigator.grid.dry_segment_clear((24.,0.,28.),(28.,0.,28.),1.))
        self.assertTrue(navigator.grid.segment_clear((24.,0.,28.),(28.,0.,28.)))
        self.assertFalse(calls)


    def test_planning_material_filter_is_not_reverted(self):
        keep=sensor.prepare_navigation_collision_filter(None,None)
        for mat in range(71,86):self.assertFalse(keep(mat,0,1,2),mat)
        for mat in (0,11,70,86,87,100,111):self.assertTrue(keep(mat,0,1,2),mat)
        self.assertTrue(keep(73,0,1))


    def test_all_airfield_spawn_entries_use_real_next_target_without_static_probes(self):
        graph=read_json(os.path.join(ROOT,'navgraphs','31_airfield.json'))
        RESULTS['airfield_entries']=[]
        for team in (1,2):
            for slot,pose in enumerate(graph['spawn_formations'][str(team)]):
                navigator,calls=self.make(graph)
                start=tuple(pose[:3]);gx,gz=graph['spawn_anchors'][2-team];goal=(gx,0.,gz)
                before=copy.deepcopy((navigator.grid._baked_heights,navigator.grid._baked_links))
                for step in range(1,501):
                    target=navigator.next_target(101,start,goal,('route',team,'removed-review',slot),step*.1)
                    state=navigator.bot_states[101]
                    if state.get('navigation_status')=='safe':break
                self.assertEqual('safe',state.get('navigation_status'),(team,slot,state))
                self.assertFalse(calls,(team,slot,len(calls)))
                self.assertEqual(before,(navigator.grid._baked_heights,navigator.grid._baked_links))
                RESULTS['airfield_entries'].append({'team':team,'slot':slot,'steps':step,'target':target,'native_calls':len(calls)})


def map_test(filename):
    def test(self):
        graph=read_json(os.path.join(ROOT,'navgraphs',filename))
        navigator,calls=self.make(graph);grid=navigator.grid
        saved=copy.deepcopy((grid._baked_heights,grid._baked_links))
        edge=None
        for index,value in enumerate(grid._baked_heights):
            if value is None:continue
            cell=index%graph['width'],index//graph['width']
            for dx,dz,length,next_cell,next_y in grid._baked_neighbours(cell):
                a=grid.point_for(cell,grid._baked_cell_height(cell));b=grid.point_for(next_cell,next_y)
                if grid._inside(b[0],b[2]) and not grid.segment_has_baked_hazard(a,b,navmod.BAKED_SHALLOW_WATER):
                    edge=(a,b);break
            if edge:break
        self.assertIsNotNone(edge,filename)
        a,b=edge
        self.assertTrue(grid.segment_clear(a,b),filename)
        self.assertFalse(grid.path_has_penalty(edge,1.),filename)
        path=grid.plan(a,b,max_expansions=100)
        self.assertTrue(path,filename)
        for step in range(1,101):
            navigator.next_target(201,a,b,('local',201,'immutable-map'),step*.1)
            if navigator.bot_states[201].get('navigation_status')=='safe':break
        self.assertFalse(calls,filename)
        self.assertEqual(saved,(grid._baked_heights,grid._baked_links),filename)
        self.assertFalse(hasattr(grid,'_native_review_cells'))
        RESULTS['maps'].append({'map':graph.get('map',filename),'static_probe_calls':len(calls),'sample_edge_passed':True})
    return test

MANIFEST=read_json(os.path.join(ROOT,'navgraphs','manifest.json'))
for record in MANIFEST['maps']:
    setattr(ImmutableNavigationTests,'test_map_'+record['file'].replace('.','_'),map_test(record['file']))

def run_suite():
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ImmutableNavigationTests))
    RESULTS['tests_run']=result.testsRun
    RESULTS['success']=result.wasSuccessful()
    output=os.environ.get('BOT080_TEST_REPORT',os.path.join(ROOT,'bot080-contract-tests.json'))
    with open(output,'wb') as stream:stream.write((json.dumps(RESULTS,sort_keys=True,indent=2)+'\n').encode('utf-8'))
    return result.wasSuccessful()
if __name__=='__main__':sys.exit(0 if run_suite() else 1)
