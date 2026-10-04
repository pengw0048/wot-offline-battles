"""Exercise powered pivots through the real catalog and retail health gate."""
import json
import math
import sys
import types
import unittest
from unittest import mock

import test_port_0922_battle_runtime as runtime_tests
from test_port_0922_destructibles import ROOT, _Vector as V
from gui.mods.offline_lan_0922 import battle_runtime as runtime_module
from gui.mods.offline_lan_0922 import destructibles_sensor as sensor


# Positions, rounded yaw/pitch/roll and dt from LOCAL STALL/LOCAL DRIVE in
# report 20260920-102444. X/Z retain the full precision of its support plane.
# The reconstructed step uses the reported angular limit; the report does not
# retain the rejected candidate yaw. Native health is deliberately injected
# below: these replays do not claim to recover the client's missing cache.
PARIS_POSES = (
    ((-21.613544219538756, 3.111, 184.14444860758314),
     2.008, -.027, .056, 1, .011, {(32384, 74, None), (32384, 24, 74)}),
    ((-17.994687827465224, 3.236, 167.95021967039025),
     2.202, -.056, .039, -1, .009, {(32384, 6, None), (32384, 24, 73)}),
    ((19.38729964818379, 3.584, 163.44482149370782),
     1.487, .006, -.047, 1, .009, {(32640, 64, 73)}),
    ((25.832680510689215, 3.0, 125.03879453496256),
     2.911, 0, 0, -1, .014, {(32640, 15, None)}),
)
MUTZ_BBOX = ((-1.4627649784088135, -2.8000000384054147e-05, -3.0866799354553223),
             (1.4627649784088135, 1.6451510190963745, 3.193120002746582), None)


class PivotCrushTests(unittest.TestCase):
    def setUp(self):
        runtime = runtime_tests._runtime()
        self.battle = runtime_module.BattleRuntime(runtime)
        self.battle._avatar = runtime.bigworld.avatar
        self.battle._destructibles = sensor
        self.addCleanup(sensor.set_catalog, None)
        sensor.set_catalog(json.loads(
            (ROOT / 'destructibles/112_eiffel_tower_ctf.json').read_text()))
        state = mock.patch.dict(sensor.__dict__, {
            'xrange': range, 'g_offh_destr_instances': {},
            'g_offh_destr_contact_bins': {},
            'g_offh_destr_speculative': set(),
            'g_offh_destr_isolated_chunks': set(),
            'g_offh_destr_isolated_slots': set(),
            'g_offh_destr_empty_contact_receipts': {'entries': {}, 'order': []}})
        state.start()
        self.addCleanup(state.stop)
        self.broken = set()
        self.health = 80.0
        self.destroy_calls = []
        self.angular_cap = math.pi / 4.0
        self.drive_cap = 13.889
        self.descriptor = {
            'physics': {'weight': 35500},
            'hull': {'hitTester': types.SimpleNamespace(bbox=MUTZ_BBOX)}}
        identities = {key[:2] for row in PARIS_POSES for key in row[-1]}
        for key in identities:
            item = sensor._destructible_catalog['baked_instances'][key]
            sensor.g_offh_destr_instances[key] = item
            sensor._index_catalog_instance_1513(
                sensor.g_offh_destr_contact_bins, key, item)
        def descriptor(filename):
            record = sensor._destructible_catalog['resources'][filename.lower()]
            if record['kind'] == 'structure':
                return {'type': 4, 'modules': {
                    material: {'health': self.health} for material in (73, 74)}}
            return {'type': 3, 'health': self.health, 'kineticDamageCorrection': 0}
        area = types.SimpleNamespace(
            DESTR_TYPE_STRUCTURE=4, DESTR_TYPE_FRAGILE=3,
            DESTR_TYPE_FALLING_ATOM=2,
            g_cache=types.SimpleNamespace(
                unitVehicleMass=35000, getDescByFilename=descriptor))
        modules = mock.patch.dict(sys.modules, {
            'Math': types.SimpleNamespace(Vector3=V),
            'AreaDestructibles': area,
            'DestructiblesCache': types.SimpleNamespace(
                scaledDestructibleHealth=lambda scale, health:
                    int(math.ceil(scale * scale * health)))})
        modules.start()
        self.addCleanup(modules.stop)
        def destroy(kind, args):
            key = (args[1], args[2], args[3] if kind == 'module' else None)
            self.destroy_calls.append(key)
            self.broken.add(key)
            return True
        authority = types.SimpleNamespace(
            is_destroyed=lambda *key: key in self.broken,
            destroy_fragile=lambda *args: destroy('fragile', args),
            destroy_module=lambda *args: destroy('module', args))
        for name, value in (
                ('_get_destr_authority', authority),
                ('_stream_baked_motion_instances_1513', []),
                ('_refresh_destroyed_falling_instances_1513', None),
                ('_retry_catalog_publications_1513', None),
                ('_publish_catalog_once_1513', None)):
            patch = mock.patch.object(sensor, name, return_value=value)
            patch.start()
            self.addCleanup(patch.stop)

    def sweep(self, row, drive_cap=None, commit=False, angular_cap=None):
        pos, yaw, pitch, roll, direction, dt, unused = row
        return self.battle._destructible_pose_sweep(
            pos, yaw, pos, yaw + direction * self.angular_cap * dt,
            0.0, self.descriptor, 12.5, dt, commit_enabled=commit,
            rotation_speed_cap=(self.angular_cap if angular_cap is None
                                else angular_cap),
            drive_speed_cap=drive_cap, pitch=pitch, roll=roll)

    def test_report_poses_share_translation_eligibility_without_fake_speed(self):
        for row in PARIS_POSES:
            with self.subTest(position=row[0]):
                old = self.sweep(row)
                self.assertEqual('hard', old['status'])
                detail = self.sweep(row, self.drive_cap)
                self.assertEqual('crushed', detail['status'])
                self.assertEqual(row[-1], set(detail['token']))
                self.assertTrue(detail['requires_commit'])
                self.assertTrue(detail['used_kinetic_speed'])
                self.assertEqual(old['impact_speed'], detail['impact_speed'])
                self.assertLess(detail['impact_speed'], self.drive_cap)
                self.assertEqual([], self.destroy_calls)

    def test_worker_commit_destroys_the_exact_proposed_subset_once(self):
        for row in PARIS_POSES:
            self.broken.clear()
            self.destroy_calls[:] = []
            proposed = self.sweep(row, self.drive_cap)
            committed = self.sweep(row, self.drive_cap, commit=True)
            self.assertEqual('crushed', committed['status'])
            self.assertEqual(set(proposed['token']), set(self.destroy_calls))
            self.assertEqual(len(proposed['token']), len(self.destroy_calls))
            self.sweep(row, self.drive_cap, commit=True)
            self.assertEqual(len(proposed['token']), len(self.destroy_calls))

    def test_health_mass_and_disabled_traverse_still_reject(self):
        row = PARIS_POSES[2]
        self.health = 10000
        self.assertEqual('hard', self.sweep(row, self.drive_cap)['status'])
        self.health = 80
        self.descriptor['physics']['weight'] = 100
        self.assertEqual('hard', self.sweep(row, self.drive_cap)['status'])
        self.descriptor['physics']['weight'] = 35500
        self.assertEqual('hard', self.sweep(
            row, self.drive_cap, angular_cap=0.0)['status'])
        self.assertEqual([], self.destroy_calls)

    def test_no_turn_or_distant_hull_cannot_destroy_from_drive_cap(self):
        row = list(PARIS_POSES[2])
        row[4] = 0
        self.assertEqual('clear', self.sweep(row, self.drive_cap)['status'])
        row[4] = 1
        row[0] = (row[0][0] - 40, row[0][1], row[0][2] - 40)
        detail = self.sweep(row, self.drive_cap)
        self.assertEqual('clear', detail['status'])
        self.assertFalse(detail['requires_commit'])
        self.assertEqual([], self.destroy_calls)

    def test_visible_rotation_uses_the_published_directional_drive_limit(self):
        battle = self.battle
        battle._local_physics = {
            'rotSpd': self.angular_cap, 'speedFwd': 13.889, 'speedBwd': 5.5556}
        clear = {'status': 'clear', 'kinds': '-', 'requires_commit': False}
        battle._destructible_pose_sweep = mock.Mock(return_value=clear)
        battle._tree_motion_proposal = mock.Mock(return_value=clear)
        battle._native_world_rotation_is_clear = mock.Mock(return_value=True)
        entity = runtime_tests._Vehicle(
            10, runtime_tests._Descriptor(), runtime_tests._Vector(),
            (0, 0, 0), {'health': 500})
        for speed, limit in ((0.0, 13.889), (1.0, 13.889), (-1.0, 5.5556)):
            self.assertTrue(battle._pose_sweep_is_clear(
                entity, (0, 0, 0), 0.0, (0, 0, 0), .01, speed, .04))
            call = battle._destructible_pose_sweep.call_args
            self.assertEqual(limit, call.kwargs['drive_speed_cap'])
            self.assertEqual(speed, call.args[4])
