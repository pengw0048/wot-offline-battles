"""Tests execute the exact b51324c5 source plus the isolated clean-graph gate.
These are source-level tests, separate from the Python 2.7 compilation and Windows package smoke checks.
"""
import copy,json,math,sys,pathlib,unittest,hashlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/res/scripts/client'))
from gui.mods.offline_lan_0922.ai.navigation import TerrainGrid
from gui.mods.offline_lan_0922.navigation_graph_schema import validate_graph
NEW=json.loads((ROOT/'navgraphs/31_airfield.json').read_text())
# Read the exact old asset still contained in the untouched private build.
OLD=json.loads((ROOT/'tests/fixtures/31_airfield-before.json').read_text())

class CleanGraphIntegrationTest(unittest.TestCase):
 def setUp(self):
  self.calls=[]
  def no_native(*args):
   self.calls.append(args)
   raise AssertionError('Clean cold graph must not need native reconstruction')
  self.grid=TerrainGrid(no_native,no_native,baked_graph=copy.deepcopy(NEW))
  self.grid.set_destructible_regions([(-500,500,-500,500)])
 def test_full_map_soft_regions_cannot_reactivate_static_repairs(self):
  self.assertEqual(set(),self.grid._soft_graph_cells)
  self.assertEqual(set(),self.grid._native_review_cells)
  self.assertTrue(self.grid._static_topology_complete)
 def test_all_30_spawn_to_enemy_base_paths_work_cold_without_native_queries(self):
  paths=[]
  for team in (1,2):
   gx,gz=NEW['spawn_anchors'][2-team];goal=(gx,0.,gz)
   for slot,pose in enumerate(NEW['spawn_formations'][str(team)]):
    path=self.grid.plan(tuple(pose[:3]),goal,max_expansions=20000)
    self.assertTrue(path,(team,slot))
    self.assertLess(math.hypot(path[-1][0]-gx,path[-1][2]-gz),4.1,(team,slot,path[-1]))
    paths.append({'team':team,'slot':slot,'waypoints':len(path),'end':path[-1]})
  self.assertEqual([],self.calls)
  self.assertEqual({},self.grid._live_heights)
  self.assertEqual({},self.grid._live_links)
  self.assertEqual(0,self.grid._live_edge_count)
  self.assertEqual(NEW['heights_mm'],self.grid._baked_heights)
  self.assertEqual(NEW['links'],self.grid._baked_links)
  (ROOT/'airfield-cold-runtime-paths.json').write_text(json.dumps({'paths':paths,'native_calls':len(self.calls),'diagnostics':self.grid.structure_graph_diagnostics()},indent=2))
 def test_remaining_holes_are_not_measured_or_filled(self):
  index=next(i for i,h in enumerate(NEW['heights_mm']) if h is None)
  cell=(index%NEW['width'],index//NEW['width'])
  self.assertIsNone(self.grid._soft_cell_height(cell,0.))
  self.assertEqual([],self.calls)
 def test_old_unrebuilt_maps_keep_their_previous_policy(self):
  grid=TerrainGrid(lambda *a:0.,lambda *a:False,baked_graph=copy.deepcopy(OLD))
  grid.set_destructible_regions([(-10,10,-10,10)])
  self.assertFalse(grid._static_topology_complete)
  self.assertTrue(grid._soft_graph_cells)
 def test_boolean_alone_cannot_certify_an_old_graph(self):
  g=copy.deepcopy(OLD);g['bake']['original_destructible_surfaces_excluded']=True
  grid=TerrainGrid(lambda *a:0.,lambda *a:False,baked_graph=g)
  self.assertFalse(grid._static_topology_complete)
 def test_new_graph_passes_native_loader_schema(self):
  self.assertIsNotNone(validate_graph(copy.deepcopy(NEW),'31_airfield'))

if __name__=='__main__':unittest.main(verbosity=2)
