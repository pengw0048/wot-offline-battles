"""Replay the tilted native fence faces from the 20260920-102444 report."""
import json
import unittest

import test_port_0922_fence_rollback as rollback
from test_port_0922_destructibles import ROOT, _Vector as V
from gui.mods.offline_lan_0922 import destructibles_sensor as sensor


class SlopedFenceTests(unittest.TestCase):
    setUp = rollback.BaselineFenceTests.setUp
    install = rollback.BaselineFenceTests.install

    def latest_contacts(self):
        return json.loads((ROOT /
            'tests/fixtures/malinovka_102444_contacts.json').read_text())

    @staticmethod
    def query(start, end, surfaces, pruned=False):
        calls = []
        def collide(space, a, b, flags, keep):
            calls.append((a, b))
            direction = b - a
            length = direction.length
            direction.normalise()
            found = []
            for point, normal, key in reversed(surfaces):
                delta = point - a
                distance = sum(getattr(delta, axis) * getattr(direction, axis)
                               for axis in ('x', 'y', 'z'))
                if -1e-7 <= distance <= length + 1e-7:
                    found.append((distance, point, normal, key))
            if pruned:
                # Match a native traversal that first prunes to the nearest
                # distance and reveals a backing face only after a recast.
                found.sort(key=lambda row: row[0])
                for unused, point, normal, key in found:
                    if keep(*key):
                        return point, normal
                return None
            accepted = [row for row in found if keep(*row[3])]
            if accepted:
                row = min(accepted, key=lambda value: value[0])
                return row[1], row[2]
            return None
        result = sensor.collide_motion_segment(
            1, start, end, lambda *key: True, collide)
        return result, len(calls)

    def witnesses(self, contact):
        start, end, unused, unused_key = self.install(contact)
        surfaces = [(V(row['hit']), V(row['normal']), tuple(row['key']))
            for row in contact['native_contact_evidence']['surface_witnesses']]
        return start, end, surfaces

    def test_reported_sloped_faces_clear_only_after_destruction(self):
        contacts = self.latest_contacts()
        self.assertEqual(13, len(contacts))
        for contact in contacts:
            for pruned in (False, True):
                with self.subTest(time=contact['log_time'], pruned=pruned):
                    a, b, surfaces = self.witnesses(contact)
                    result, calls = self.query(a, b, surfaces, pruned)
                    self.assertIsNone(result)
                    self.assertLessEqual(calls, 6)
                    self.broken.clear()
                    result, unused = self.query(a, b, surfaces, pruned)
                    self.assertIsNotNone(result)

    def test_sloped_skin_does_not_hide_a_backing_wall_or_replacement(self):
        for contact in self.latest_contacts():
            for material in (88, 111):
                with self.subTest(time=contact['log_time'], material=material):
                    a, b, surfaces = self.witnesses(contact)
                    direction = b - a
                    direction.normalise()
                    first = min(surfaces, key=lambda row: (row[0] - a).length)
                    wall = first[0] + direction.scale(.001)
                    surfaces.append((wall, first[1], (material, 0, 900, 31870)))
                    for pruned in (False, True):
                        result, calls = self.query(a, b, surfaces, pruned)
                        self.assertIs(wall, result[0])
                        self.assertLessEqual(calls, 6)

    def test_projection_requires_an_authored_side_face(self):
        for contact in self.latest_contacts():
            a, b, surfaces = self.witnesses(contact)
            # A sloping terrain/top face is not a local vertical original.
            surfaces = [(p, V(0, 1, 0), key) for p, n, key in surfaces]
            result, unused = self.query(a, b, surfaces)
            self.assertIsNotNone(result)

    def test_stacked_live_unregistered_and_isolated_owners_still_block(self):
        contact = self.latest_contacts()[0]
        for state in ('live', 'unregistered', 'isolated'):
            with self.subTest(state=state):
                a, b, surfaces = self.witnesses(contact)
                source = sensor.g_offh_destr_instances[(31871, 2)]
                identity = (31871, 900)
                extra = dict(source)
                # Move along the tilted authored up, preserving the footprint.
                extra['boxes'] = [(tuple(center[i] + 20 * axes[1][i]
                    for i in range(3)), axes, material)
                    for center, axes, material in source['boxes']]
                if state == 'unregistered':
                    catalog = sensor._destructible_catalog
                    catalog['baked_instances'][identity] = extra
                    point = surfaces[0][0]
                    keys = list(sensor._baked_bin_keys_for_bounds_1513(
                        point.x, point.x, point.z, point.z))
                    for key in keys:
                        catalog['baked_shot_bins'].setdefault(key, set()).add(identity)
                else:
                    sensor.g_offh_destr_instances[identity] = extra
                    # The native model may be registered in this contact bin
                    # even when its damage modules are vertically displaced.
                    point = surfaces[0][0]
                    key = sensor._destructible_bin_key(point.x, point.z)
                    sensor.g_offh_destr_contact_bins[key].add(identity)
                    if state == 'isolated':
                        sensor.g_offh_destr_isolated_slots.add(identity)
                        self.broken.update(identity + (box[2],)
                                           for box in extra['boxes'])
                result, unused = self.query(a, b, surfaces)
                self.assertIsNotNone(result)
                if state == 'unregistered':
                    del catalog['baked_instances'][identity]
                    for key in keys:
                        catalog['baked_shot_bins'][key].discard(identity)
