"""Railside C6 Poplar evidence from the user's Prokhorovka screenshots."""
import copy
import json
import math
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

import test_port_0922_destructibles as fixture
import test_port_0922_battle_runtime as runtime_fixture
from gui.mods.offline_lan_0922 import destructibles_sensor as sensor
from gui.mods.offline_lan_0922.tree_diagnostics import TreeContactDiagnostics

ROOT = Path(__file__).resolve().parents[1]
Vector = fixture._Vector


class IndexedVector(object):
    """Vector boundary exposing three coordinates, without list slicing."""

    def __init__(self, x, y, z):
        self.values = (x, y, z)

    def __getitem__(self, index):
        if not isinstance(index, int):
            raise TypeError('vector indices must be integers')
        return self.values[index]


class RailSideTreeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.xrange_patch = mock.patch.object(sensor, 'xrange', range, create=True)
        self.xrange_patch.start()
        self.addCleanup(self.xrange_patch.stop)
        sensor.set_catalog(json.loads(
            (ROOT / 'destructibles/05_prohorovka.json').read_text()))
        self.observer = TreeContactDiagnostics()
        self.authority = types.SimpleNamespace(_state={})
        self.point = (94.984, 6.043, 274.026)
        self.wire = (32641, 64)
        self.detail = {'status': 'clear', 'token': None,
                       'requires_commit': False}

    def tearDown(self):
        sensor.set_catalog(None)

    def sample(self, now=1.0, stage='proposal', space=1):
        return self.observer.sample(sensor, self.authority, space,
            self.point, self.point, math.pi, 5.0, 0.04, now,
            self.detail, stage)

    def target(self, payload):
        return next(row for row in payload['trees']
                    if tuple(row['wire']) == self.wire)

    def test_missing_registry_is_visible_even_when_motion_says_clear(self):
        sensor.g_offh_tree_state = {'spaceID': 1, 'chunks': {}}
        before = copy.deepcopy(sensor.g_offh_tree_state)
        with mock.patch.object(sensor, '_get_destr_authority',
                side_effect=AssertionError('diagnostics initialized authority')):
            payload = self.sample()
        row = self.target(payload)
        self.assertFalse(row['chunk_registered'])
        self.assertIsNone(row['registry_position'])
        self.assertEqual('speedtree/05_Prohorovka/Poplar.spt', row['resource'])
        self.assertEqual(self.point, row['authored_position'])
        self.assertEqual(before, sensor.g_offh_tree_state)
        self.assertEqual({}, self.authority._state)
        json.dumps(payload)

    def test_health_rejection_is_separate_from_missing_chunk(self):
        sensor.g_offh_tree_state = {'spaceID': 1, 'chunks': {
            self.wire[0]: {'bins': {}, 'tree_health': {64: 5}}}}
        row = self.target(self.sample())
        self.assertTrue(row['chunk_registered'])
        self.assertEqual(5, row['health'])
        self.assertIsNone(row['registry_position'])
        self.assertFalse(row['authority_destroyed'])

    def test_native_no_body_receipt_is_distinct_from_no_contact(self):
        sensor.g_offh_tree_state = {'spaceID': 1, 'chunks': {},
            'native_committed': {self.wire}, 'canonical_published': set()}
        self.authority._state = {'spaceID': 1, 'chunks': {32641: {
            'keys': {(64, None)}, 'treePresentations': {
                64: {'status': 'pending', 'fallPitch': 0.12}}}}}
        self.detail.update(status='crushed', token=((32641, 64, None),))
        before = copy.deepcopy(self.authority._state)
        row = self.target(self.sample(stage='commit'))
        self.assertTrue(row['authority_destroyed'])
        self.assertTrue(row['native_committed'])
        self.assertFalse(row['canonical_published'])
        self.assertEqual('pending', row['presentation_at_accept'])
        self.assertEqual(0.12, row['fall_pitch'])
        self.assertEqual(before, self.authority._state)
        self.assertIsNone(self.sample(now=2.0, stage='commit'))

    def test_sampling_dedup_does_not_hide_fresh_commit_or_new_space(self):
        self.assertIsNotNone(self.sample())
        self.assertIsNone(self.sample(now=1.1))
        self.assertIsNone(self.sample(now=2.0))
        self.detail.update(status='crushed', token=((32641, 64, None),))
        self.assertIsNotNone(self.sample(now=2.01, stage='commit'))
        self.assertIsNotNone(self.sample(now=0.0, space=2))

    def test_layout_completion_rebuilds_observation_wire_without_guessing(self):
        self.sample()
        catalog = sensor._destructible_catalog
        catalog['layout_generations'][32641] = 1
        catalog['layout_repairs'].add(32641)
        self.assertTrue(self.target(self.sample(now=2.0))['layout_pending'])
        record = catalog['tree_instances'].pop(self.wire)
        catalog['tree_instances'][(32641, 65)] = record
        catalog['layout_repairs'].remove(32641)
        self.wire = (32641, 65)
        row = self.target(self.sample(now=3.0))
        self.assertFalse(row['layout_pending'])
        self.assertEqual(self.point, row['authored_position'])

    def test_all_reported_poplars_have_fall_profiles_and_geometric_contacts(self):
        catalog = sensor._destructible_catalog
        rows = [(wire, record) for wire, record in
            catalog['tree_instances'].items()
            if 'poplar' in record['filename'] and
            80000 < record['signature'][0] < 105000 and
            200000 < record['signature'][2] < 370000]
        self.assertEqual(6, len(rows))
        profiles = {tuple(row[:2]) for row in json.loads(
            (ROOT / 'foliage/05_prohorovka.json').read_text())['fallen_trees']}
        bbox = ((-1.6, 0.0, -3.5), (1.6, 2.0, 3.5), None)
        for wire, record in rows:
            with self.subTest(wire=wire):
                self.assertIn(wire, profiles)
                p = [value / 1000.0 for value in record['signature'][:3]]
                item = (wire[1], p[0], p[1], p[2], 1,
                        record['descriptor_filename'], 78, 0, (), 0.0)
                registry = {'bins': {sensor._destructible_bin_key(p[0], p[2]):
                                     [item]}}
                boxes = sensor._tree_pose_sweep_boxes_1513(
                    Vector(p[0], p[1], p[2] + 4), math.pi,
                    Vector(p[0], p[1], p[2] - 4), math.pi, bbox)
                candidates, isolated = sensor._tree_candidates_for_sweeps_1513(
                    wire[0], registry, boxes, 1, {})
                self.assertEqual({wire + (None,)}, set(candidates))
                self.assertFalse(isolated)
                # A tank in the adjoining lane must not knock these down.
                boxes = sensor._tree_pose_sweep_boxes_1513(
                    Vector(p[0] + 5, p[1], p[2] + 4), math.pi,
                    Vector(p[0] + 5, p[1], p[2] - 4), math.pi, bbox)
                self.assertFalse(sensor._tree_candidates_for_sweeps_1513(
                    wire[0], registry, boxes, 1, {})[0])

    def test_indexed_vector_bounds_do_not_drop_the_entire_tree_sweep(self):
        start, end = Vector(94.984, 6.043, 278), Vector(94.984, 6.043, 270)
        plain = ((-1.6, 0, -3.5), (1.6, 2, 3.5), None)
        native_bounds = (IndexedVector(*plain[0]), IndexedVector(*plain[1]), None)
        expected = sensor._tree_pose_sweep_boxes_1513(
            start, math.pi, end, math.pi, plain)
        self.assertIsNotNone(expected)
        self.assertEqual(expected, sensor._tree_pose_sweep_boxes_1513(
            start, math.pi, end, math.pi, native_bounds))

    def test_indexed_vector_reaches_tree_proposal_with_registered_contact(self):
        sensor.set_catalog(None)
        bbox = (IndexedVector(-1.6, 0, -3.5), IndexedVector(1.6, 2, 3.5), None)
        environment = fixture.DestructiblesCompatibilityTests()._tree_motion_fixture(
            (Vector(*self.point),), bbox=bbox)
        unused, area, native, math_module, descriptor, authority, destroyed, calls = environment
        with mock.patch.dict(sys.modules, {'AreaDestructibles': area,
                'BigWorld': native, 'Math': math_module}), mock.patch.object(
                    sensor, '_get_destr_authority', return_value=authority):
            result = sensor._tree_motion_proposal(1,
                Vector(94.984, 6.043, 278), math.pi,
                Vector(94.984, 6.043, 270), math.pi,
                10, descriptor, 1, dt=.1)
        self.assertEqual('crushed', result['status'])
        self.assertEqual(((22, 0, None),), result['token'])
        self.assertTrue(result['requires_commit'])
        self.assertFalse(calls)
        self.assertFalse(destroyed)

    def test_v9_mode_excluded_slot_does_not_disable_other_trees_in_chunk(self):
        sensor.set_catalog(None)
        area, native, math_module, descriptor, tree, unused = (
            fixture.DestructiblesCompatibilityTests()._authored_tree_identity_fixture())
        catalog = sensor._destructible_catalog
        catalog['layout_repair_supported'] = True
        catalog['tree_instances'].pop((22, 1))
        catalog['excluded_instances'].add((22, 1))
        category = native.wg_getDestructibleEffectCategory.side_effect
        matrix = native.wg_getDestructibleMatrix.side_effect

        def category_guard(space, chunk, item, material):
            self.assertNotEqual(1, item, 'queried absent native scene slot')
            return category(space, chunk, item, material)

        def matrix_guard(space, chunk, item):
            self.assertNotEqual(1, item, 'queried absent native scene matrix')
            return matrix(space, chunk, item)

        native.wg_getDestructibleEffectCategory.side_effect = category_guard
        native.wg_getDestructibleMatrix.side_effect = matrix_guard
        authority = types.SimpleNamespace(is_destroyed=lambda *args: False)
        with mock.patch.dict(sys.modules, {'AreaDestructibles': area,
                'BigWorld': native, 'Math': math_module}), mock.patch.object(
                    sensor, '_get_destr_authority', return_value=authority):
            result = sensor._tree_motion_proposal(1,
                Vector(50, 0, 54), math.pi, Vector(50, 0, 49), math.pi,
                10, descriptor, 1, dt=.1)
            self.assertEqual('crushed', result['status'])
            self.assertEqual(((22, 0, None),), result['token'])
            self.assertTrue(result['requires_commit'])
            self.assertEqual(('exact', tree),
                sensor.resolve_native_item_name_1513(1, 22, 0))
            self.assertIn((22, 2), sensor.g_offh_destr_instances)
            self.assertFalse(sensor.is_isolated_1513(22, 0))
            self.assertTrue(sensor.is_isolated_1513(22, 1))
        native.wg_getDestructibleFilename.assert_not_called()


class LocalTreeDiagnosticBoundaryTests(unittest.TestCase):
    def test_report_serializes_vector_corners_and_reads_existing_authority(self):
        runtime = runtime_fixture._runtime()
        battle = runtime_fixture.BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._destructibles = types.SimpleNamespace(
            _vehicle_hull_bbox=lambda unused: (
                IndexedVector(-1, 0, -3), IndexedVector(1, 2, 3), object()))
        observer = mock.Mock(return_value={'status': 'pending'})
        battle._local_tree_diagnostics = types.SimpleNamespace(sample=observer)
        with mock.patch.dict(sys.modules, {
                'gui.mods.offline_lan_0922.destructibles_authority':
                fixture.destructibles_authority}), \
                mock.patch.object(sys.stdout, 'write') as write:
            battle._report_local_tree_motion((95, 6, 275), (95, 6, 274),
                math.pi, 5, .1, 1, {'status': 'pending'}, descriptor=object(),
                start_yaw=math.pi)
        line = write.call_args[0][0]
        payload = json.loads(line.split('LOCAL TREE ', 1)[1])
        self.assertEqual([[-1., 0., -3.], [1., 2., 3.]], payload['hull_bbox'])
        self.assertEqual(['IndexedVector', 'IndexedVector'], payload['hull_corner_types'])
        self.assertEqual(math.pi, payload['start_yaw'])
        self.assertIs(fixture.destructibles_authority, observer.call_args[0][1])

    def test_raw_pending_verdict_is_logged_without_blocking_visible_motion(self):
        runtime = runtime_fixture._runtime()
        battle = runtime_fixture.BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._destructibles = types.SimpleNamespace(
            _tree_motion_proposal=lambda *args, **kwargs: {
                'status': 'pending', 'token': None, 'requires_commit': False})
        capture = mock.Mock()
        battle._report_local_tree_motion = capture
        result = battle._tree_motion_proposal(
            (95, 6, 275), 0, (95, 6, 274), 0, 5,
            runtime_fixture._Descriptor(), 1, .1)
        self.assertEqual('clear', result['status'])
        self.assertEqual('pending', capture.call_args[0][-1]['status'])

    def test_observer_failure_does_not_change_a_tree_contact(self):
        runtime = runtime_fixture._runtime()
        battle = runtime_fixture.BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        token = ((32641, 64, None),)
        battle._destructibles = types.SimpleNamespace(
            _tree_motion_proposal=lambda *args, **kwargs: {
                'status': 'crushed', 'token': token, 'requires_commit': True})
        battle._local_tree_diagnostics = types.SimpleNamespace(
            sample=mock.Mock(side_effect=OSError('closed output')))
        result = battle._tree_motion_proposal(
            (95, 6, 275), 0, (95, 6, 274), 0, 5,
            runtime_fixture._Descriptor(), 1, .1)
        self.assertEqual('crushed', result['status'])
        self.assertEqual(token, result['token'])


if __name__ == '__main__':
    unittest.main()
