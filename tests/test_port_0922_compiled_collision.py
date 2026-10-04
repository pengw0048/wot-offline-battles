"""Native #1513 report contacts with callback order deliberately reversed."""
import json
import math
import types
import unittest
from unittest import mock

from test_port_0922_destructibles import (
    ROOT, _Vector as V, _ItemMatrix, destructibles_sensor as sensor)
from gui.mods.offline_lan_0922 import vehicle_physics


class CompiledCollisionTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(sensor.set_catalog, None)
        data = json.loads((ROOT / 'destructibles/02_malinovka.json').read_text())
        sensor.set_catalog(data)
        self.state = mock.patch.dict(sensor.__dict__, {
            'xrange': range, 'g_offh_destr_instances': {},
            'g_offh_destr_contact_bins': {}, 'g_offh_destr_speculative': set(),
            'g_offh_destr_isolated_chunks': set(),
            'g_offh_destr_isolated_slots': set()})
        self.state.start()
        self.addCleanup(self.state.stop)
        for row in data['instances']:
            if row[14] != 32636:
                continue
            record = data['resources'][row[12]]
            identity = tuple(row[14:16])
            instance = dict(filename=row[12].lower(), kind=record['kind'],
                boxes=sensor._baked_world_boxes_1513(
                    record, row[:12], row[13], data['locator_quantization']),
                item_scale=row[16], box_index=row[13])
            sensor.g_offh_destr_instances[identity] = instance
            sensor._index_catalog_instance_1513(
                sensor.g_offh_destr_contact_bins, identity, instance)
        self.broken = set((32636, item, material)
                          for item in range(23, 27) for material in (73, 74))
        authority = types.SimpleNamespace(is_destroyed=lambda *key: key in self.broken)
        patch = mock.patch.object(sensor, '_get_destr_authority', return_value=authority)
        patch.start()
        self.addCleanup(patch.stop)
        self.contacts = json.loads((ROOT / 'tests/fixtures/malinovka_073639_contacts.json').read_text())

    @staticmethod
    def native(surfaces):
        def collide(space, start, end, flags, keep=None):
            direction = end - start
            length = direction.length
            if not length:
                return None
            direction.normalise()
            found = []
            for point, identity in reversed(surfaces):
                delta = point - start
                distance = delta.x * direction.x + delta.y * direction.y + delta.z * direction.z
                if -1e-7 <= distance <= length + 1e-7 and (keep is None or keep(*identity)):
                    found.append((distance, point))
            return (min(found, key=lambda value: value[0])[1], V(1, 0, 0)) if found else None
        return collide

    def evidence(self, contact):
        start, end, hit = (V(contact[name]) for name in ('ray_start', 'ray_end', 'hit'))
        candidate = sensor._catalog_candidate_on_ray_1513(hit, start, end)
        alias = next(tuple(value[:4]) for value in contact['native_surface_candidates']
                     if value[0] == candidate[2] and value[1] & 128)
        return start, end, hit, candidate, alias

    def query(self, start, end, surfaces):
        return sensor.collide_motion_segment(1, start, end, lambda *hit: True,
                                             self.native(surfaces))

    def test_all_five_reported_original_skins_clear_after_acceptance(self):
        for contact in self.contacts:
            with self.subTest(hit=contact['hit']):
                start, end, hit, candidate, alias = self.evidence(contact)
                self.assertIsNone(self.query(start, end, [(hit, alias)]))
                self.broken.remove(candidate[:3])
                self.assertIs(hit, self.query(start, end, [(hit, alias)])[0])
                self.broken.add(candidate[:3])

    def test_latest_report_model_edges_and_overlapping_module_boxes(self):
        contacts = json.loads((ROOT / 'tests/fixtures/malinovka_095351_contacts.json').read_text())
        for contact in contacts:
            start, end, hit = (V(contact[name]) for name in ('ray_start', 'ray_end', 'hit'))
            aliases = [tuple(row[:4]) for row in contact['native_surface_candidates']
                       if 71 <= row[0] <= 86 and row[1] & 128]
            for alias in aliases:
                with self.subTest(hit=contact['hit'], material=alias[0]):
                    self.assertIsNone(self.query(start, end, [(hit, alias)]))
                    direction = end - start
                    direction.normalise()
                    wall = hit + direction.scale(.001)
                    for material in (88, 111):
                        key = (material, 0, 50000, 32636)
                        self.assertIs(wall, self.query(start, end,
                            [(hit, alias), (wall, key)])[0])
                    removed = {key for key in self.broken if key[2] == alias[0]}
                    self.broken.difference_update(removed)
                    self.assertIs(hit, self.query(start, end, [(hit, alias)])[0])
                    self.broken.update(removed)

    def test_replacement_and_backing_wall_inside_box_remain_solid(self):
        for material in (88, 111):
            for contact in self.contacts:
                with self.subTest(material=material, hit=contact['hit']):
                    start, end, hit, candidate, alias = self.evidence(contact)
                    direction = end - start
                    direction.normalise()
                    wall = hit + direction.scale(0.001)
                    key = (material, 0, candidate[1], candidate[0])
                    self.assertIs(wall, self.query(start, end, [(hit, alias), (wall, key)])[0])
                    # Seeing the anonymous callback later cannot classify an
                    # unrelated nearest surface as the broken original skin.
                    self.assertIs(hit, self.query(start, end, [(hit, key), (wall, alias)])[0])

    def test_merged_key_is_not_filtered_beyond_the_destroyed_box(self):
        start, end, hit, candidate, alias = self.evidence(self.contacts[1])
        # The same aggregate key on the next intact tile is independent.
        self.broken.intersection_update({candidate[:3]})
        direction = end - start
        direction.normalise()
        end = end + direction.scale(20.0)
        envelope = sensor._instance_motion_envelope_1513(
            sensor.g_offh_destr_instances[candidate[:2]])
        distance = sensor._segment_world_box_interval(
            start, end, envelope)[1] * (end - start).length
        outside = start + direction.scale(distance + 1.0)
        self.assertIs(outside, self.query(start, end, [(hit, alias), (outside, alias)])[0])

    def test_later_live_owner_inside_model_envelope_keeps_shared_native_key(self):
        start, end, hit, candidate, alias = self.evidence(self.contacts[1])
        direction = end - start
        direction.normalise()
        end = end + direction.scale(2.0)
        wall = hit + direction.scale(.2)
        identity = (32636, 900)
        neighbour = dict(sensor.g_offh_destr_instances[candidate[:2]])
        neighbour['boxes'] = [((wall.x, wall.y, wall.z),
            ((.02, 0, 0), (0, .02, 0), (0, 0, .02)), candidate[2])]
        sensor.g_offh_destr_instances[identity] = neighbour
        sensor._index_catalog_instance_1513(sensor.g_offh_destr_contact_bins,
                                           identity, neighbour)
        self.assertIs(wall, self.query(start, end, [(hit, alias), (wall, alias)])[0])

    def test_ambiguous_overlapping_live_module_stays_solid(self):
        start, end, hit, candidate, alias = self.evidence(self.contacts[1])
        instance = dict(sensor.g_offh_destr_instances[candidate[:2]])
        key = (32636, 900)
        sensor.g_offh_destr_instances[key] = instance
        sensor._index_catalog_instance_1513(sensor.g_offh_destr_contact_bins, key, instance)
        self.assertIs(hit, self.query(start, end, [(hit, alias)])[0])

    def test_player_ground_adapter_recasts_broken_skin_and_keeps_real_support(self):
        from test_port_0922_battle_runtime import BattleRuntime, _runtime

        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._destructibles = types.SimpleNamespace(
            collide_motion_segment=sensor.collide_motion_segment,
            ground_collision_filter=lambda x, z: lambda *hit: True)
        for contact in self.contacts:
            with self.subTest(hit=contact['hit']):
                unused_start, unused_end, hit, candidate, alias = self.evidence(contact)
                ground = hit - V(0, 4, 0)
                native = self.native([(hit, alias), (ground, (0, 0, 0, 0))])

                def collide(*args):
                    result = native(*args)
                    return None if result is None else (result[0], V(0, 1, 0))

                runtime.bigworld.wg_collideSegment = collide
                self.assertAlmostEqual(ground.y, battle._ground_y(hit.x, hit.z, hit.y))
                self.broken.remove(candidate[:3])
                self.assertAlmostEqual(hit.y, battle._ground_y(hit.x, hit.z, hit.y))
                self.broken.add(candidate[:3])

    def test_mannerheim_deflection_cannot_enter_primary_wall_plane(self):
        cases = ((-2.82414586, (.941174, .051361, .333996)),
                 (-2.82414586, (.965634, .052242, -.254601)),
                 (-2.497337, (.541966, .052745, .838744)))
        for yaw, normal in cases:
            for speed in (13.894536, 1.04):
                candidates = vehicle_physics.hard_contact_candidate_yaws(yaw, speed, normal)
                for candidate in candidates:
                    self.assertGreaterEqual(math.sin(candidate) * normal[0] +
                                            math.cos(candidate) * normal[2], -1e-9)
        self.assertNotIn(cases[0][0] - .55,
                         vehicle_physics.hard_contact_candidate_yaws(
                             cases[0][0], 13.894536, cases[0][1]))

    def test_glance_and_reverse_preserve_outward_motion(self):
        for speed, yaw in ((4.0, -0.2), (-4.0, math.pi - 0.2)):
            choices = vehicle_physics.hard_contact_candidate_yaws(yaw, speed, (1, 0, 0))
            self.assertTrue(choices)
            self.assertTrue(all(math.sin(value) * speed >= 0 for value in choices))


class CrossMapRailingCollisionTests(unittest.TestCase):
    """Use shipped placements; native aliases are simulated, not playtest data."""

    def setUp(self):
        self.addCleanup(sensor.set_catalog, None)
        self.broken = set()
        authority = types.SimpleNamespace(
            is_destroyed=lambda *key: key in self.broken,
            destroyed_identities=lambda: set(key[:2] for key in self.broken),
            destroyed_keys=lambda chunk: set(key[1:] for key in self.broken
                                             if key[0] == chunk))
        patch = mock.patch.object(sensor, '_get_destr_authority', return_value=authority)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.dict(sensor.__dict__, {'xrange': range})
        patch.start()
        self.addCleanup(patch.stop)

    def install(self, data, row):
        self.broken.clear()
        for name in ('instances', 'contact_bins', 'broken_cache'):
            sensor.__dict__['g_offh_destr_' + name] = {}
        sensor.g_offh_destr_speculative = set()
        record = data['resources'][row[12]]
        identity = tuple(row[14:16])
        instance = dict(filename=row[12].lower(), kind=record['kind'],
            boxes=sensor._baked_world_boxes_1513(
                record, row[:12], row[13], data['locator_quantization']),
            item_scale=row[16], box_index=row[13])
        sensor.g_offh_destr_instances[identity] = instance
        sensor._index_catalog_instance_1513(sensor.g_offh_destr_contact_bins,
                                           identity, instance)
        return identity, instance

    @staticmethod
    def ray(box):
        centre, axis = V(box[0]), V(box[1][0])
        # Use an interior sample to avoid an ambiguous shared module edge;
        # the captured contact and overlapping-neighbour tests cover edges.
        return centre - axis.scale(1.2), centre + axis.scale(1.2), centre

    @staticmethod
    def query(start, end, surfaces):
        collision_filter = sensor.horizontal_collision_filter(start, end)
        return sensor.collide_motion_segment(1, start, end, collision_filter,
                                             CompiledCollisionTests.native(surfaces))

    def paris(self):
        data = json.loads((ROOT / 'destructibles/112_eiffel_tower_ctf.json').read_text())
        sensor.set_catalog(data)
        row = next(row for row in data['instances']
                   if row[12].endswith('/gaf_112_04_BridgeFence_Tile.model'))
        identity, instance = self.install(data, row)
        return identity, instance, self.ray(instance['boxes'][0])

    def test_paris_fragile_bridge_fence_releases_accepted_compiled_skin(self):
        identity, instance, (start, end, hit) = self.paris()
        for material in (71, 72, 73, 86):
            with self.subTest(material=material):
                self.broken.clear()
                alias = (material, 131, 60000, 1700000)
                self.assertIs(hit, self.query(start, end, [(hit, alias)])[0])
                self.broken.add(identity + (None,))
                self.assertIsNone(self.query(start, end, [(hit, alias)]))

    def test_all_shipped_fence_variants_use_the_same_release_and_wall_rule(self):
        covered_maps, covered_variants = set(), set()
        for path in sorted((ROOT / 'destructibles').glob('*.json')):
            data = json.loads(path.read_text())
            if 'resources' not in data:
                continue
            rows = {}
            for row in data['instances']:
                name = row[12].lower()
                if ('/gatesandfences/' in name or 'barrier' in name or
                        'mil203_militarydefences' in name):
                    rows.setdefault((row[12], row[13]), row)
            if not rows:
                continue
            sensor.set_catalog(data)
            for row in rows.values():
                identity, instance = self.install(data, row)
                for box in instance['boxes']:
                    with self.subTest(map=path.stem, resource=row[12], material=box[2]):
                        start, end, hit = self.ray(box)
                        key = identity + (box[2],)
                        material = box[2] if box[2] is not None else 73
                        alias = (material, 131, 60000, 1700000)
                        self.assertIs(hit, self.query(start, end, [(hit, alias)])[0])
                        self.broken.add(key)
                        self.assertIsNone(self.query(start, end, [(hit, alias)]))
                        direction = end - start
                        direction.normalise()
                        wall = hit + direction.scale(0.001)
                        for wall_material in (88, 111):
                            # Damaged material belongs to this item; the
                            # unrelated backing wall has its own native ID.
                            item = identity[1] if wall_material == 88 else 50000
                            wall_key = (wall_material, 0, item, identity[0])
                            self.assertIs(wall, self.query(start, end,
                                [(hit, alias), (wall, wall_key)])[0])
                        covered_maps.add(path.stem)
                        covered_variants.add((path.stem, row[12], row[13], box[2]))
        self.assertIn('112_eiffel_tower_ctf', covered_maps)
        self.assertIn('02_malinovka', covered_maps)
        self.assertGreater(len(covered_maps), 30)
        print('Cross-map railing fixtures: %d maps, %d placed collider variants' %
              (len(covered_maps), len(covered_variants)))

    def test_fragile_alias_never_erases_adjacent_solid_or_unknown_surfaces(self):
        identity, instance, (start, end, hit) = self.paris()
        self.broken.add(identity + (None,))
        alias = (73, 131, 60000, 1700000)
        # An unregistered material, or a non-compiled original key, is not proof.
        for key in ((111, 131, 60000, 1700000), (73, 0, 60000, 1700000)):
            self.assertIs(hit, self.query(start, end, [(hit, key)])[0])
        direction = end - start
        direction.normalise()
        extended = end + direction.scale(2.0)
        outside = end + direction.scale(1.0)
        self.assertIs(outside, self.query(start, extended,
                      [(hit, alias), (outside, alias)])[0])
        # A second intact object occupying the same bounds remains ambiguous.
        neighbour = (identity[0], 900)
        sensor.g_offh_destr_instances[neighbour] = dict(instance)
        sensor._index_catalog_instance_1513(sensor.g_offh_destr_contact_bins,
                                           neighbour, instance)
        self.assertIs(hit, self.query(start, end, [(hit, alias)])[0])

    def test_revoked_local_prediction_restores_the_fragile_collision(self):
        identity, instance, (start, end, hit) = self.paris()
        key = identity + (None,)
        surfaces = [(hit, (73, 131, 60000, 1700000))]
        sensor.g_offh_destr_speculative.add(key)
        self.assertIsNone(self.query(start, end, surfaces))
        sensor.g_offh_destr_speculative.remove(key)
        self.assertIs(hit, self.query(start, end, surfaces)[0])

    @staticmethod
    def pruned_native(surfaces):
        # A native BSP traversal can prune farther callbacks once it finds a
        # near hit. The second key then appears ONLY after filtering the first.
        def collide(space, a, b, flags, keep=None):
            delta = b - a
            length = delta.length
            delta.normalise()
            for point, key in surfaces:
                distance = sum(getattr(point - a, axis) * getattr(delta, axis)
                               for axis in ('x', 'y', 'z'))
                if -1e-7 <= distance <= length + 1e-7:
                    if keep is None or keep(*key):
                        return point, V(1, 0, 0)
        return collide

    def test_pruned_native_callbacks_reveal_more_than_one_broken_skin(self):
        identity, instance, (start, end, hit) = self.paris()
        self.broken.add(identity + (None,))
        direction = end - start
        direction.normalise()
        next_skin = hit + direction.scale(0.01)
        wall = hit + direction.scale(0.02)
        originals = [(hit, (73, 131, 60000, 1700000)),
                     (next_skin, (74, 131, 60001, 1700000))]

        keep = sensor.horizontal_collision_filter(start, end)
        self.assertIsNone(sensor.collide_motion_segment(
            1, start, end, keep, self.pruned_native(originals)))
        for material in (88, 111):
            with self.subTest(material=material):
                result = sensor.collide_motion_segment(1, start, end, keep,
                    self.pruned_native(originals + [
                        (wall, (material, 0, 50000, identity[0]))]))
                self.assertIs(wall, result[0])

    def test_nested_recasts_share_one_budget_and_keep_unexamined_surface_solid(self):
        identity, instance, (start, end, hit) = self.paris()
        self.broken.add(identity + (None,))
        direction = end - start
        direction.normalise()
        surfaces = [(hit + direction.scale(i * 0.01),
                     (73 + i, 131, 60000 + i, 1700000)) for i in range(6)]
        evidence = {}
        result = sensor.collide_motion_segment(1, start, end,
            sensor.horizontal_collision_filter(start, end),
            self.pruned_native(surfaces), evidence=evidence)
        self.assertIs(surfaces[4][0], result[0])
        self.assertTrue(evidence['budget_exhausted'])
        self.assertEqual(5, len(evidence['queries']))

    def test_contact_diagnostic_distinguishes_actual_wall_from_callback_candidates(self):
        identity, instance, (start, end, hit) = self.paris()
        self.broken.add(identity + (None,))
        direction = end - start
        direction.normalise()
        wall = hit + direction.scale(0.01)
        wall_key = (111, 0, 50000, identity[0])
        native = CompiledCollisionTests.native([
            (hit, (73, 131, 60000, 1700000)), (wall, wall_key)])
        accepted_before = set(self.broken)
        with mock.patch.dict('sys.modules', {
                'BigWorld': types.SimpleNamespace(wg_collideSegment=native),
                'Math': types.SimpleNamespace(Vector3=V)}):
            evidence = sensor.native_contact_evidence(1, start, end, wall)
        self.assertFalse(evidence['replay_clear'])
        self.assertAlmostEqual(0, evidence['replay_contact_distance'])
        self.assertEqual([wall_key], [row['key']
            for row in evidence['surface_witnesses']])
        self.assertAlmostEqual(0, evidence['surface_witnesses'][0]['contact_distance'])
        owner = next(row for row in evidence['nearby_owners']
                     if row['identity'] == identity)
        self.assertTrue(owner['boxes'][0]['broken'])
        self.assertEqual(accepted_before, self.broken)


class NativeFenceFollowupTests(unittest.TestCase):
    """Replay the reported native face, bounds and accepted module state."""

    setUp = CrossMapRailingCollisionTests.setUp
    install = CrossMapRailingCollisionTests.install
    paris = CrossMapRailingCollisionTests.paris
    ray = staticmethod(CrossMapRailingCollisionTests.ray)

    def install_report(self, contact, map_name='112_eiffel_tower_ctf'):
        sensor.set_catalog(json.loads(
            (ROOT / ('destructibles/%s.json' % map_name)).read_text()))
        sensor.g_offh_destr_instances = {}
        sensor.g_offh_destr_contact_bins = {}
        sensor.g_offh_destr_speculative = set()
        sensor.g_offh_destr_broken_cache = {}
        self.broken.clear()
        for owner in contact['native_contact_evidence']['nearby_owners']:
            identity = tuple(owner['identity'])
            instance = dict(filename=owner['filename'], kind=owner['kind'],
                boxes=[(b['center'], b['axes'], b['material']) for b in owner['boxes']])
            sensor.g_offh_destr_instances[identity] = instance
            sensor._index_catalog_instance_1513(
                sensor.g_offh_destr_contact_bins, identity, instance)
            self.broken.update(identity + (b['material'],)
                               for b in owner['boxes'] if b['broken'])

    def test_murovanka_remapped_chunk_original_faces_use_spatial_owner(self):
        rows = json.loads((ROOT / 'tests/fixtures/murovanka_143656_contacts.json').read_text())
        self.assertEqual(5, len(rows))
        for row in rows:
            with self.subTest(hit=row['hit']):
                self.install_report(row, '11_murovanka')
                a, b, point = [V(row[k]) for k in ('ray_start', 'ray_end', 'hit')]
                # Leave room for a distinct backing face inside the same ray.
                direction = b - a
                direction.normalise()
                b = b + direction.scale(.01)
                key = tuple(row['native_contact_evidence']['surface_witnesses'][0]['key'])
                keep = sensor.horizontal_collision_filter(a, b)
                def cast(surfaces):
                    native = CompiledCollisionTests.native(surfaces)
                    def query(*args):
                        result = native(*args)
                        return None if result is None else (result[0], V(row['normal']))
                    return sensor.collide_motion_segment(1, a, b, keep, query)
                # The log proves a completed live-layout repair for 32635.
                self.assertIs(point, cast([(point, key)])[0])

                sensor._destructible_catalog['layout_generations'][32635] = 1
                if not any(box['contains_hit'] for owner in
                        row['native_contact_evidence']['nearby_owners']
                        for box in owner['boxes']):
                    # One recorded end face has no proved owner volume. Keep
                    # that unresolved face solid; the report cannot prove a
                    # safe exclusion outside the authored bounds.
                    self.assertIs(point, cast([(point, key)])[0])
                    continue
                self.assertIsNone(cast([(point, key)]))
                direction = b - a
                direction.normalise()
                wall = point + direction.scale(.001)
                for material in (88, 111):
                    self.assertIs(wall, cast([(point, key),
                        (wall, (material, 0, key[2], key[3]))])[0])
                self.broken.clear()
                self.assertIs(point, cast([(point, key)])[0])


    def test_spotting_recasts_remapped_broken_original_without_changing_mask(self):
        row = json.loads((ROOT / 'tests/fixtures/murovanka_143656_contacts.json').read_text())[0]
        self.install_report(row, '11_murovanka')
        sensor._destructible_catalog['layout_generations'][32635] = 1
        a, b, point = [V(row[k]) for k in ('ray_start', 'ray_end', 'hit')]
        key = tuple(row['native_contact_evidence']['surface_witnesses'][0]['key'])
        self.assertEqual(0, key[1])
        direction = b - a
        direction.normalise()
        b = b + direction.scale(.1)
        keep = sensor.sight_collision_filter()
        surfaces = [(point, key)]
        masks = []

        def native(space, start, end, flags, callback=None):
            masks.append(flags)
            result = CompiledCollisionTests.native([
                value for value in surfaces if not value[1][1] & flags])(
                    space, start, end, flags, callback)
            return None if result is None else (result[0], V(row['normal']))

        # The old one-pass callback still sees the original BSP's stale slot.
        self.assertIs(point, native(1, a, b, 128, keep)[0])
        self.assertIsNone(sensor.collide_sight_segment(1, a, b, keep, native))
        wall = point + direction.scale(.01)
        for material in (88, 111):
            surfaces.append((wall, (material, 0, 50000, 32635)))
            self.assertIs(wall, sensor.collide_sight_segment(
                1, a, b, keep, native)[0])
            surfaces.pop()
        self.broken.clear()
        self.assertIs(point, sensor.collide_sight_segment(
            1, a, b, keep, native)[0])
        self.assertEqual({128}, set(masks))

    def test_sight_mask_keeps_vehicle_only_faces_out_of_spotting(self):
        a, b, point = V(0, 1, 0), V(5, 1, 0), V(2, 1, 0)
        key = (111, 128, 50000, 32635)
        calls = []

        def native(space, start, end, flags, callback=None):
            calls.append(flags)
            return CompiledCollisionTests.native([
                (point, key)] if not key[1] & flags else [])(
                    space, start, end, flags, callback)

        self.assertIsNone(sensor.collide_sight_segment(1, a, b, None, native))
        self.assertIs(point, sensor.collide_motion_segment(
            1, a, b, None, native)[0])
        self.assertEqual([128, 80], calls)

    def test_default_sight_diagnostic_is_rate_limited_and_preserves_obstacle_state(self):
        identity, instance, (a, b, point) = self.paris()
        key = (111, 0, 50000, identity[0])
        queries = []
        clock = [10.0]
        native = CompiledCollisionTests.native([(point, key)])
        def query(*args):
            queries.append(args)
            return native(*args)
        messages = []
        broken_before = set(self.broken)
        with mock.patch.dict(sensor.__dict__, {
                '_DIAGNOSTICS_ENABLED': False,
                '_diagnostic_writer': messages.append,
                'g_offh_destr_contact_diagnostic_times': {}}), \
                mock.patch.object(sensor, 'static_contact_evidence', return_value=[]), \
                mock.patch.dict('sys.modules', {
                    'BigWorld': types.SimpleNamespace(wg_collideSegment=query,
                                                      time=lambda: clock[0]),
                    'Math': types.SimpleNamespace(Vector3=V)}):
            sensor.report_sight_contact(1, a, b, (point, V(1, 0, 0)))
            count = len(queries)
            sensor.report_sight_contact(1, a, b, (point, V(1, 0, 0)))
            self.assertEqual(count, len(queries))
            self.assertEqual(1, len(messages))
            payload = json.loads(messages[0].split('SIGHT CONTACT ', 1)[1])
            self.assertEqual(128, payload['native_contact_evidence']['skip_flags'])
            witnesses = payload['native_contact_evidence']['surface_witnesses']
            self.assertEqual([list(key)], [value['key'] for value in witnesses])
            self.assertEqual([point.x, point.y, point.z], payload['hit'])
            clock[0] += 5.0
            sensor.report_sight_contact(1, a, b, (point, V(1, 0, 0)))
            self.assertEqual(2, len(messages))
        self.assertEqual(broken_before, self.broken)
    def test_westfield_remapped_stone_fence_bevels_clear_only_in_broken_bounds(self):
        rows = json.loads((ROOT / 'tests/fixtures/westfield_114133_contacts.json').read_text())
        self.assertEqual(11, len(rows))
        resolved = 0
        for row in rows:
            with self.subTest(time=row['log_time']):
                self.install_report(row, '23_westfeld')
                a, b, point = [V(row[k]) for k in ('ray_start', 'ray_end', 'hit')]
                direction = b - a
                direction.normalise()
                b = b + direction.scale(.01)
                key = tuple(row['native_contact_evidence']['surface_witnesses'][0]['key'])
                keep = sensor.horizontal_collision_filter(a, b)
                def cast(surfaces):
                    native = CompiledCollisionTests.native(surfaces)
                    def query(*args):
                        hit = native(*args)
                        return None if hit is None else (hit[0], V(row['normal']))
                    return sensor.collide_motion_segment(1, a, b, keep, query)
                self.assertIs(point, cast([(point, key)])[0])
                # The coherent visible log completes this chunk's live
                # remapping at 11:40:37, before all eleven contacts.
                sensor._destructible_catalog['layout_generations'][32639] = 1
                if not any(box['contains_hit'] for owner in
                        row['native_contact_evidence']['nearby_owners']
                        for box in owner['boxes']):
                    self.assertIs(point, cast([(point, key)])[0])
                    continue
                self.assertIsNone(cast([(point, key)]))
                resolved += 1
                wall = point + direction.scale(.001)
                for material in (88, 111):
                    self.assertIs(wall, cast([(point, key),
                        (wall, (material, 0, key[2], key[3]))])[0])
                # A mismatched chunk is never spatially reattributed.
                self.assertIs(point, cast([(point,
                    (key[0], key[1], key[2], key[3] + 1))])[0])
                self.broken.clear()
                self.assertIs(point, cast([(point, key)])[0])
        self.assertEqual(10, resolved)

    def test_unresolved_original_surface_diagnostic_records_live_slot_read_only(self):
        row = json.loads((ROOT / 'tests/fixtures/westfield_114133_contacts.json').read_text())[2]
        self.install_report(row, '23_westfeld')
        catalog = sensor._destructible_catalog
        catalog['layout_generations'][32639] = 1
        manager = types.SimpleNamespace(getSpaceID=lambda: 1,
            _DestructiblesManager__loadedChunkIDs={32639: 112})
        matrix_query = mock.Mock(return_value=_ItemMatrix(V(3, 4, 5)))
        native = types.SimpleNamespace(
            wg_getChunkMatrix=lambda *unused: _ItemMatrix(V(100, 0, 200)),
            wg_getDestructibleMatrix=matrix_query,
            wg_getDestructibleEffectCategory=lambda *unused: -1)
        isolate = mock.Mock(side_effect=AssertionError('diagnostic mutated identity'))
        with mock.patch.dict('sys.modules', {
                'BigWorld': native,
                'AreaDestructibles': types.SimpleNamespace(g_destructiblesManager=manager),
                'Math': types.SimpleNamespace(Vector3=V, Matrix=lambda value: value)}), \
                mock.patch.object(sensor, '_isolate_destructible_1513', isolate):
            result = sensor._native_contact_slot_evidence_1513(1, (73, 0, 8, 32639))
            self.assertTrue(result['excluded'])
            self.assertEqual(1, result['layout_generation'])
            self.assertEqual(-1, result['native_category'])
            self.assertEqual((103000, 4000, 205000), result['native_signature'][:3])
            matrix_query.reset_mock()
            manager._DestructiblesManager__loadedChunkIDs = None
            result = sensor._native_contact_slot_evidence_1513(1, (73, 0, 8, 32639))
            self.assertNotIn('native_signature', result)
            matrix_query.assert_not_called()
            isolate.assert_not_called()

    def test_prague_broken_door_uses_live_component_bounds_not_stale_material(self):
        rows = json.loads((ROOT / 'tests/fixtures/prague_173230_contacts.json').read_text())
        self.assertEqual(4, len(rows))
        for row in rows:
            with self.subTest(time=row['log_time']):
                self.install_report(row, '114_czech')
                a, b, point = [V(row[k]) for k in ('ray_start', 'ray_end', 'hit')]
                key = tuple(row['native_contact_evidence']['surface_witnesses'][0]['key'])
                keep = sensor.horizontal_collision_filter(a, b)
                def cast(surfaces):
                    return sensor.collide_motion_segment(
                        1, a, b, keep, CompiledCollisionTests.native(surfaces))
                self.assertIsNone(cast([(point, key)]))
                direction = b - a
                direction.normalise()
                wall = point + direction.scale(.001)
                for material in (88, 111):
                    self.assertIs(wall, cast([(point, key),
                        (wall, (material, 0, key[2], key[3]))])[0])
                self.broken.clear()
                self.assertIs(point, cast([(point, key)])[0])
                self.broken.add((key[3], key[2], 74))
                # An overlapping intact component makes ownership ambiguous.
                instance = sensor.g_offh_destr_instances[(key[3], key[2])]
                instance['boxes'].append(((point.x, point.y, point.z),
                    ((.1, 0, 0), (0, .1, 0), (0, 0, .1)), 75))
                self.assertIs(point, cast([(point, key)])[0])

    def test_prague_material_alias_stops_before_intact_component(self):
        row = json.loads((ROOT / 'tests/fixtures/prague_173230_contacts.json').read_text())[0]
        self.install_report(row, '114_czech')
        a, b, point = [V(row[k]) for k in ('ray_start', 'ray_end', 'hit')]
        direction = b - a
        direction.normalise()
        b = b + direction.scale(2)
        wall = point + direction.scale(.1)
        instance = sensor.g_offh_destr_instances[(32640, 89)]
        instance['boxes'].append(((wall.x, wall.y, wall.z),
            ((.01, 0, 0), (0, .01, 0), (0, 0, .01)), 75))
        key = (73, 0, 89, 32640)
        result = sensor.collide_motion_segment(1, a, b,
            sensor.horizontal_collision_filter(a, b),
            CompiledCollisionTests.native([(point, key), (wall, key)]))
        self.assertIs(wall, result[0])

    def test_remapped_key_cannot_erase_its_own_intact_owner(self):
        row = json.loads((ROOT / 'tests/fixtures/murovanka_143656_contacts.json').read_text())[0]
        self.install_report(row, '11_murovanka')
        sensor._destructible_catalog['layout_generations'][32635] = 1
        a, b, point = [V(row[k]) for k in ('ray_start', 'ray_end', 'hit')]
        key = tuple(row['native_contact_evidence']['surface_witnesses'][0]['key'])
        identity = (key[3], key[2])
        instance = dict(sensor.g_offh_destr_instances[(32635, 31)])
        sensor.g_offh_destr_instances[identity] = instance
        sensor._index_catalog_instance_1513(sensor.g_offh_destr_contact_bins,
                                           identity, instance)
        self.assertIsNone(sensor._compiled_motion_skin_1513(
            point, a, b, {key}, V(row['normal'])))

    @staticmethod
    def transient_native(surfaces):
        calls = [0]
        def collide(space, start, end, flags, keep=None):
            calls[0] += 1
            current = [(point, (key[0], key[1], key[2] + calls[0],
                key[3] + calls[0]) if key[3] > 100000 else key)
                for point, key in surfaces]
            result = CompiledCollisionTests.native(current)(
                space, start, end, flags, keep)
            return None if result is None else (result[0], V(0, 0, 1))
        return collide

    def test_reported_paris_vertical_original_faces_above_baked_boxes(self):
        rows = json.loads((ROOT / 'tests/fixtures/paris_191002_contacts.json').read_text())
        for row in rows:
            with self.subTest(time=row['log_time']):
                self.install_report(row)
                a, b, point = [V(row[k]) for k in ('ray_start', 'ray_end', 'hit')]
                alias = tuple(row['native_contact_evidence']['queries'][0]['candidates'][0])
                keep = sensor.horizontal_collision_filter(a, b)
                cast = self.transient_native([(point, alias)])
                self.assertIsNone(sensor.collide_motion_segment(1, a, b, keep, cast))
                direction = b - a
                direction.normalise()
                wall = point + direction.scale(.001)
                for material in (88, 111):
                    result = sensor.collide_motion_segment(1, a, b, keep,
                        self.transient_native([(point, alias),
                            (wall, (material, 0, 999, 32384))]))
                    self.assertIs(wall, result[0])
                self.broken.clear()
                self.assertIs(point, sensor.collide_motion_segment(
                    1, a, b, keep, cast)[0])

    def test_transient_aliases_inside_bounds_and_diagnostic_witness(self):
        identity, instance, (a, b, point) = self.paris()
        self.broken.add(identity + (None,))
        cast = self.transient_native([(point, (73, 131, 60000, 1700000))])
        self.assertIsNone(sensor.collide_motion_segment(1, a, b,
            sensor.horizontal_collision_filter(a, b), cast))
        self.broken.clear()
        with mock.patch.dict('sys.modules', {
                'BigWorld': types.SimpleNamespace(wg_collideSegment=cast),
                'Math': types.SimpleNamespace(Vector3=V)}):
            evidence = sensor.native_contact_evidence(1, a, b, point)
        self.assertFalse(evidence['replay_clear'])
        self.assertEqual(0, evidence['surface_witnesses'][0]['contact_distance'])

    def test_projected_owner_does_not_hide_stacked_live_or_unregistered_items(self):
        row = json.loads((ROOT / 'tests/fixtures/paris_191002_contacts.json').read_text())[0]
        for registered in (True, False):
            with self.subTest(registered=registered):
                self.install_report(row)
                a, b, point = [V(row[k]) for k in ('ray_start', 'ray_end', 'hit')]
                identity = (32384, 999)
                extra = dict(next(iter(sensor.g_offh_destr_instances.values())))
                extra['boxes'] = [(tuple(c[i] + (30 if i == 1 else 0)
                    for i in range(3)), axes, mat) for c, axes, mat in extra['boxes']]
                if registered:
                    sensor.g_offh_destr_instances[identity] = extra
                    sensor._index_catalog_instance_1513(
                        sensor.g_offh_destr_contact_bins, identity, extra)
                else:
                    sensor._destructible_catalog['baked_instances'][identity] = extra
                    for key in sensor._baked_bin_keys_for_bounds_1513(
                            point.x, point.x, point.z, point.z):
                        sensor._destructible_catalog['baked_shot_bins'].setdefault(key,set()).add(identity)
                cast = self.transient_native([(point, (74, 131, 60000, 1700000))])
                self.assertIs(point, sensor.collide_motion_segment(1, a, b,
                    sensor.horizontal_collision_filter(a, b), cast)[0])

    def test_above_bounds_projection_preserves_ground_and_real_wire_identity(self):
        row = json.loads((ROOT / 'tests/fixtures/paris_191002_contacts.json').read_text())[0]
        self.install_report(row)
        a, b, point = [V(row[k]) for k in ('ray_start', 'ray_end', 'hit')]
        aliases = {(74, 131, 60000, 1700000)}
        self.assertIsNone(sensor._compiled_motion_skin_1513(
            point, a, b, aliases, V(0, 1, 0)))
        self.assertIsNone(sensor._compiled_motion_skin_1513(
            point, point + V(0, 1, 0), point - V(0, 1, 0), aliases, V(1, 0, 0)))
        live = next(key for key in sensor._destructible_catalog['baked_instances']
                    if key not in sensor.g_offh_destr_instances)
        self.assertIsNone(sensor._anonymous_original_surface_1513(
            (74, 131, live[1], live[0])))
