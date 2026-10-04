from __future__ import print_function
"""Source/Python-2.7-bytecode regression; no native game or driving replay."""
import argparse
import copy
import glob
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
import types
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDGES = (
    ((-222., -13.32, -250.), (-226., -12.524, -250.)),
    ((-74., -12.516, -262.), (-74., -12.749, -258.)),
    ((-58., -6.174, -302.), (-54., -4.955, -306.)),
    ((354., -0.319, -22.), (354., -0.015, -18.)),
    ((374., -0.179, -26.), (374., -0.368, -22.)),
)
GRAPH_SHA = '5c981f89f2ab2f36058cf8835e3ea9a666587d15521227a2e121e02c5d0a737f'
TerrainNavigator = None
GRAPH = OLD = None
PATH_RESULTS = []


def read(path):
    with open(path, 'rb') as stream:
        return stream.read()


class ReviewEntryTest(unittest.TestCase):
    def make(self, graph=None):
        self.calls = []
        def obstacle(*args):
            self.calls.append(args)
            return True  # Recorded binary refusal, not a native collision replay.
        self.nav = TerrainNavigator(lambda *a: 0., obstacle,
                                    baked_graph=copy.deepcopy(GRAPH if graph is None else graph))
        self.grid = self.nav.grid
        return self.nav

    def test_production_entry_does_not_enrol_clean_static_cells(self):
        nav = self.make()
        start = tuple(GRAPH['spawn_formations']['1'][0][:3])
        gx, gz = GRAPH['spawn_anchors'][1]
        nav.next_target(101, start, (gx, 0., gz), ('route', 1, 'review-regression', 0), .1)
        self.assertEqual(set(), self.grid._native_review_cells)
        self.assertEqual([], self.calls)

    def test_five_recorded_links_survive_review_and_invalidation(self):
        self.make()
        for a, b in EDGES:
            ca, cb = self.grid.cell_for(a), self.grid.cell_for(b)
            bit = self.grid._NEIGHBOUR_BITS[(cb[0] - ca[0], cb[1] - ca[1])]
            self.assertTrue(self.grid._baked_link_mask(ca) & bit)
            self.grid.review_native_corridor(a, b)
            self.assertTrue(self.grid._native_edge_clear(ca, cb), (a, b))
        self.grid.invalidate_native_review()
        self.assertEqual([], self.calls)
        self.assertEqual(set(), self.grid._native_review_cells)

    def test_legacy_graph_keeps_its_review(self):
        self.make(OLD)
        a, b = EDGES[0]
        self.grid.review_native_corridor(a, b)
        self.assertTrue(self.grid._native_review_cells)
        self.assertFalse(self.grid._native_edge_clear(self.grid.cell_for(a), self.grid.cell_for(b)))
        self.assertTrue(self.calls)

    def test_wreck_edge_penalty_is_retained(self):
        self.make()
        a, b = EDGES[0]
        self.grid.set_static_hulls([(123, a[0], a[2], 0., 3.5, 1.8)])
        self.assertTrue(self.grid.path_crosses_static_hull([a, b]))
        self.assertGreater(self.grid.segment_penalty(a, b, 1.), 0.)

    def test_missing_ground_is_not_filled(self):
        self.make()
        idx = next(i for i, h in enumerate(GRAPH['heights_mm']) if h is None)
        cell = (idx % GRAPH['width'], idx // GRAPH['width'])
        p = self.grid.point_for(cell, 0.)
        self.grid.review_native_corridor(p, p)
        self.assertIsNone(self.grid._soft_cell_height(cell, 0.))
        self.assertEqual({}, self.grid._live_heights)

    def test_contact_local_replan_keeps_failed_edge(self):
        nav = self.make()
        a, b = EDGES[0]
        nav.bot_states[101] = {'blocked_step_replans': 0, 'replan_generation': 0}
        flags = [nav.report_blocked_step(101, a, b, t) for t in (0., .3, .6, .9, 1.2)]
        self.assertTrue(any(flags))
        self.assertTrue(nav.bot_failed_edges[101])

    def test_all_30_spawns_through_actual_target_entry(self):
        del PATH_RESULTS[:]
        for team in (1, 2):
            for slot, pose in enumerate(GRAPH['spawn_formations'][str(team)]):
                calls = []
                def forbidden(*args):
                    calls.append(args)
                    raise AssertionError('Unexpected static native review')
                graph = copy.deepcopy(GRAPH)
                nav = TerrainNavigator(forbidden, forbidden, baked_graph=graph)
                start = tuple(pose[:3])
                gx, gz = GRAPH['spawn_anchors'][2 - team]
                goal = (gx, 0., gz)
                target = None
                for step in range(1, 501):
                    target = nav.next_target(101, start, goal,
                        ('route', team, 'entry-regression', slot), step * .1)
                    state = nav.bot_states[101]
                    if state.get('navigation_status') == 'safe':
                        break
                self.assertEqual('safe', state.get('navigation_status'), (team, slot))
                self.assertEqual([], calls, (team, slot))
                self.assertEqual(set(), nav.grid._native_review_cells)
                self.assertEqual({}, nav.grid._live_heights)
                self.assertEqual({}, nav.grid._live_links)
                self.assertEqual(0, nav.grid._live_edge_count)
                self.assertEqual(GRAPH['heights_mm'], nav.grid._baked_heights)
                self.assertEqual(GRAPH['links'], nav.grid._baked_links)
                PATH_RESULTS.append({'team': team, 'slot': slot, 'steps': step,
                    'target': target, 'status': state.get('navigation_status'),
                    'native_calls': len(calls), 'review_cells': len(nav.grid._native_review_cells),
                    'measured_ground': len(nav.grid._live_heights),
                    'restored_edges': nav.grid._live_edge_count})


def main():
    global GRAPH, OLD, TerrainNavigator
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', action='store_true', help='Load only built CPython 2.7 bytecode')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    raw = read(os.path.join(ROOT, 'navgraphs', '31_airfield.json'))
    assert hashlib.sha256(raw).hexdigest() == GRAPH_SHA
    GRAPH = json.loads(raw)
    OLD = json.loads(read(os.path.join(ROOT, 'tests', 'fixtures', '31_airfield-before.json')))
    extracted = None
    digest = None
    try:
        source = os.path.join(ROOT, 'src', 'res', 'scripts', 'client')
        if args.package:
            assert sys.version_info[:2] == (2, 7), sys.version
            packages = glob.glob(os.path.join(ROOT, 'dist', '*.wotmod'))
            assert len(packages) == 1, packages
            digest = hashlib.sha256(read(packages[0])).hexdigest()
            extracted = tempfile.mkdtemp(prefix='airfield-review-bytecode-')
            with zipfile.ZipFile(packages[0]) as archive:
                assert archive.testzip() is None
                for name in archive.namelist():
                    assert not name.startswith('/') and '..' not in name.split('/')
                    assert not name.endswith('.py'), name
                archive.extractall(extracted)
            source = os.path.join(extracted, 'res', 'scripts', 'client')
        sys.path.insert(0, source)
        for package, relative in (('gui', 'gui'), ('gui.mods', 'gui/mods')):
            stub = types.ModuleType(package)
            stub.__path__ = [os.path.join(source, *relative.split('/'))]
            sys.modules[package] = stub
        sys.modules['gui'].mods = sys.modules['gui.mods']
        from gui.mods.offline_lan_0922.ai import navigation
        TerrainNavigator = navigation.TerrainNavigator
        imported = os.path.realpath(navigation.__file__)
        assert imported.startswith(os.path.realpath(source) + os.sep), imported
        if args.package:
            assert imported.endswith('.pyc'), imported
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReviewEntryTest)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        output = {'python': sys.version, 'mode': 'bytecode' if args.package else 'source',
            'passed': result.wasSuccessful(), 'tests_run': result.testsRun,
            'failures': len(result.failures), 'errors': len(result.errors),
            'paths': PATH_RESULTS, 'airfield_sha256': GRAPH_SHA,
            'wotmod_sha256': digest, 'native_gameplay_tested': False,
            'scope': 'Production target selection and static-review gate; not actual driving.'}
        parent = os.path.dirname(os.path.abspath(args.output))
        if not os.path.isdir(parent):
            os.makedirs(parent)
        with open(args.output, 'wb') as stream:
            stream.write((json.dumps(output, sort_keys=True, indent=2) + '\n').encode('utf-8'))
        return 0 if result.wasSuccessful() else 1
    finally:
        if extracted is not None:
            shutil.rmtree(extracted)


if __name__ == '__main__':
    raise SystemExit(main())
