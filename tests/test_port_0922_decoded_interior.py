"""Console mesh data, per-component selection, and immutable runtime geometry.

These prove local geometry/contracts, not original PC-server parity, Windows
native rendering, or a gameplay damage probability.
"""
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/res/scripts/client'))
from gui.mods.offline_lan_0922 import internal_hit_layouts
from gui.mods.offline_lan_0922 import internal_layout_console
from gui.mods.offline_lan_0922 import internal_layout_profiles
from gui.mods.offline_lan_0922 import internal_geometry
from gui.mods.offline_lan_0922 import internal_mesh

MODULE_ENTITIES = frozenset(internal_hit_layouts.MODULE_TARGETS)
PARENTS = frozenset(internal_hit_layouts.SUPPORTED_PARENTS)


def all_meshes(record):
    return ([zone[3] for zone in record[4]] +
            [zone[2] for alternatives in record[5] for zone in alternatives or ()])


def descriptor_for_record(vehicle, record, turret='turret_01'):
    from test_port_0922_critical_damage import _layout_descriptor
    descriptor = _layout_descriptor(vehicle, record[2])
    for geometry in all_meshes(record):
        part = geometry['part']
        parent = part.split('_')[0]
        if parent == 'turret' and part != turret:
            continue
        component = getattr(descriptor, parent)
        component.models = types.SimpleNamespace(undamaged='vehicles/example/normal/lod0/' + part + '.model')
        component.hitTester.bbox = tuple(geometry['reference_bounds']) + (None,)
    return descriptor


class DecodedInteriorTableTests(unittest.TestCase):
    def setUp(self):
        self.layouts = internal_layout_console.CONSOLE_LAYOUTS_0922

    def test_table_identifies_the_pinned_client_and_mesh_schema(self):
        self.assertEqual('0.9.22.0.1', internal_layout_console.CLIENT_VERSION)
        self.assertEqual('1513', internal_layout_console.CLIENT_BUILD)
        self.assertEqual('decoded', internal_layout_console.CONFIDENCE)
        self.assertEqual(1, internal_layout_console.MESH_SCHEMA)
        self.assertEqual(internal_layout_console.DECODED_COUNT, len(self.layouts))
        self.assertEqual(680, internal_layout_console.CATALOGUE_SIZE)
        self.assertTrue(internal_layout_console.GEOMETRY_SOURCES)
        self.assertEqual('pc9.22.0', internal_layout_console.REFERENCE_FRAME_SOURCE)

    def test_every_record_contains_indexed_source_meshes_or_explicit_holes(self):
        checked = set()
        for key, record in self.layouts.items():
            with self.subTest(vehicle=key):
                vehicle_class, tier, roster, unmodelled, modules, crew = record
                self.assertTrue(vehicle_class)
                self.assertTrue(1 <= tier <= 10)
                self.assertEqual(len(roster), len(crew))
                self.assertTrue(modules)
                placed = set(zone[0] for zone in modules)
                self.assertTrue(set(unmodelled).issubset(MODULE_ENTITIES))
                self.assertTrue(set(unmodelled).isdisjoint(placed))
                for entity, parent, zone_id, geometry in modules:
                    self.assertIn(entity, MODULE_ENTITIES)
                    self.assertIn(parent, PARENTS)
                    self.assertTrue(zone_id)
                    self.assertIn(geometry['part'].split('_')[0], PARENTS)
                for index, alternatives in enumerate(crew):
                    for parent, zone_id, geometry in alternatives or ():
                        self.assertIn(parent, PARENTS)
                        self.assertEqual('crew_%02d' % index, zone_id)
                for geometry in all_meshes(record):
                    self.assertTrue(geometry['source_package'].startswith('console'))
                    self.assertTrue(geometry['source_member'].lower().endswith(
                        (geometry['part'] + '_proxy.hkx', geometry['part'] + '_proxy.primitives')))
                    self.assertEqual(2, len(geometry['reference_bounds']))
                    payload = geometry['payload']
                    if payload in checked:
                        continue
                    checked.add(payload)
                    vertices, faces = internal_mesh.decode(payload)
                    self.assertEqual(geometry['vertices'], len(vertices))
                    self.assertEqual(geometry['triangles'], len(faces))
                    self.assertTrue(all(len(f) == 3 and min(f) >= 0 and max(f) < len(vertices) for f in faces))
                    pieces = internal_mesh.prepare(vertices, faces)
                    self.assertEqual(geometry['closed_pieces'], sum(p['closed'] for p in pieces))
                    self.assertEqual(geometry['open_pieces'], sum(not p['closed'] for p in pieces))

    def test_no_two_crew_share_a_mesh_in_the_same_component(self):
        for key, record in self.layouts.items():
            with self.subTest(vehicle=key):
                seats = [(zone[2]['part'], zone[2]['payload'])
                         for alternatives in record[5] for zone in alternatives or ()]
                self.assertEqual(len(seats), len(set(seats)))

    def test_every_non_native_module_is_placed_or_declared_unavailable(self):
        required = {'ammoBay', 'engine', 'fuelTank', 'radio', 'surveyingDevice', 'turretRotator'}
        for key, record in self.layouts.items():
            with self.subTest(vehicle=key):
                self.assertTrue(required.issubset(set(z[0] for z in record[4]) | set(record[3])))


class DecodedInteriorResolutionTests(unittest.TestCase):
    def setUp(self):
        internal_hit_layouts.clear_cache()
        self.addCleanup(internal_hit_layouts.clear_cache)

    def test_every_decoded_record_wins_even_with_incomplete_crew(self):
        holed = 0
        for key, record in internal_layout_console.CONSOLE_LAYOUTS_0922.items():
            unused, profile = internal_hit_layouts._compiled_profile('%s:%s' % key)
            with self.subTest(vehicle=key):
                self.assertTrue(profile[0].startswith('decoded_collision_surfaces'))
                self.assertEqual('decoded', profile[3])
                self.assertEqual(record[4], profile[5])
                self.assertEqual(record[5], profile[6])
                holed += any(zone is None for zone in record[5])
        self.assertGreater(holed, 0)

    def test_unavailable_sources_do_not_acquire_decoded_geometry(self):
        self.assertFalse(internal_hit_layouts.decoded_layout_available('ussr:R999_not_a_vehicle'))
        for key in internal_layout_console.CONSOLE_LAYOUTS_0922:
            self.assertTrue(internal_hit_layouts.decoded_layout_available('%s:%s' % key))

    def test_archetype_only_vehicle_is_explicitly_reconstructed(self):
        key = next(key for key in internal_layout_profiles.PROFILES
                   if key not in internal_layout_console.CONSOLE_LAYOUTS_0922)
        unused, profile = internal_hit_layouts._compiled_profile('%s:%s' % key)
        self.assertIsNotNone(profile)
        self.assertNotIn('decoded', profile[0])

    def test_is7_runtime_retains_eight_full_width_side_racks_and_five_crew_meshes(self):
        vehicle = 'ussr:R45_IS-7'
        record = internal_layout_console.CONSOLE_LAYOUTS_0922[internal_hit_layouts._profile_key(vehicle)]
        descriptor = descriptor_for_record(vehicle, record)
        layout = internal_hit_layouts.build_layout(descriptor, log_build=False)
        self.assertTrue(layout['valid'], layout['validation'])
        rack = next(t for t in layout['targets'] if t['entity'] == 'ammoBay' and t['parent'] == 'hull')
        self.assertEqual(8, len(rack['primitives']))
        self.assertAlmostEqual(3.00332598, rack['maximum'][0] - rack['minimum'][0], places=6)
        self.assertFalse(rack['size_correction_applied'])
        self.assertIsNone(internal_geometry.target_interval((0., .3, 3.), (0., .3, -2.), rack))
        self.assertEqual(2, len(internal_geometry.target_intervals((-2., .3, 0.), (2., .3, 0.), rack)))
        crews = [t for t in layout['targets'] if t['kind'] == 'crew']
        self.assertEqual(5, len(crews))
        for target in [rack] + crews:
            if target['kind'] == 'crew':
                source = next(g for parent, zone, g in record[5][target['crew_index']]
                              if parent == target['parent'] and zone == target['zone_id'])
            else:
                source = next(g for entity, parent, zone, g in record[4]
                              if (entity, parent, zone) == (target['entity'], target['parent'], target['zone_id']))
            vertices, unused = internal_mesh.decode(source['payload'])
            self.assertEqual(set(vertices), set(v for p in target['primitives'] for v in p['vertices']))
            self.assertTrue(all(p['shape'] == 'mesh' for p in target['primitives']))
            self.assertEqual('source_geometry_immutable', target['calibration_status'])

    def test_mismatched_installed_component_is_unavailable_not_scaled_or_reconstructed(self):
        vehicle = 'ussr:R45_IS-7'
        record = internal_layout_console.CONSOLE_LAYOUTS_0922[internal_hit_layouts._profile_key(vehicle)]
        descriptor = descriptor_for_record(vehicle, record)
        descriptor.turret.models = types.SimpleNamespace(undamaged='vehicles/example/Turret_99.model')
        layout = internal_hit_layouts.build_layout(descriptor, False)
        self.assertTrue(layout['valid'], layout['validation'])
        self.assertFalse(any(t['parent'] == 'turret' for t in layout['targets']))
        self.assertTrue(any(t['entity'] == 'ammoBay' and t['parent'] == 'hull' for t in layout['targets']))
        self.assertEqual('RESOURCE_GEOMETRY_UNMODELLED', layout['logical_entity_sources']['commander']['mode'])
        self.assertTrue(layout['mesh_rejections'])
        self.assertEqual('decoded_collision_surfaces', layout['profile_geometry_provenance'])

    def test_model_variant_and_reference_bound_both_participate_in_selection(self):
        checked = False
        for key, record in internal_layout_console.CONSOLE_LAYOUTS_0922.items():
            turrets = sorted(set(g['part'] for g in all_meshes(record) if g['part'].startswith('turret_')))
            if len(turrets) < 2:
                continue
            for part in turrets[:2]:
                descriptor = descriptor_for_record('%s:%s' % key, record, part)
                internal_hit_layouts.clear_cache()
                layout = internal_hit_layouts.build_layout(descriptor, False)
                self.assertTrue(layout['valid'], layout['validation'])
                targets = [t for t in layout['targets'] if t['parent'] == 'turret']
                self.assertTrue(targets)
                self.assertTrue(all(t['mesh_source']['part'] == part for t in targets))
            checked = True
            break
        self.assertTrue(checked, 'the full roster must include alternate turret meshes')

    def test_rear_engines_keep_negative_component_z_coordinates(self):
        for vehicle in ('ussr:R45_IS-7', 'ussr:R04_T-34', 'germany:G42_Maus', 'china:Ch01_Type59'):
            record = internal_layout_console.CONSOLE_LAYOUTS_0922[internal_hit_layouts._profile_key(vehicle)]
            engines = [internal_mesh.decode(z[3]['payload'])[0]
                       for z in record[4] if z[0] == 'engine' and z[1] == 'hull']
            self.assertTrue(engines)
            self.assertLess(min(v[2] for vertices in engines for v in vertices), 0.)


class UnmodelledModuleTests(unittest.TestCase):
    """A module the resources do not model is unavailable, never invented.

    Console models no traverse mechanism for a fixed superstructure and
    sometimes no separate optic.  Discarding such a vehicle's decoded
    ammunition rack, engine, fuel tank, radio and full crew to protect that
    one module is what left these tanks with no interior at all, so the port
    publishes the module unavailable instead -- the same contract it already
    uses for a track with no native collision extra.
    """

    def setUp(self):
        self.layouts = internal_layout_console.CONSOLE_LAYOUTS_0922
        self.records = sorted(key for key, record in self.layouts.items()
                              if record[3])

    def test_some_vehicle_declares_an_unmodelled_module(self):
        self.assertTrue(self.records,
                        'expected vehicles whose resources model no traverse')

    def test_an_unmodelled_module_is_named_in_the_source(self):
        key = self.records[0]
        unused_key, profile = internal_hit_layouts._compiled_profile(
            '%s:%s' % key)
        self.assertIsNotNone(profile)
        self.assertIn('+unmodelled:', profile[0])
        for entity in self.layouts[key][3]:
            self.assertIn(entity, profile[0])

    def test_the_helper_reports_exactly_the_recorded_entities(self):
        key = self.records[0]
        self.assertEqual(
            tuple(self.layouts[key][3]),
            internal_hit_layouts.decoded_unmodelled_entities('%s:%s' % key))
        pure = sorted(k for k, record in self.layouts.items()
                      if not record[3])
        self.assertTrue(pure)
        self.assertEqual((),
                         internal_hit_layouts.decoded_unmodelled_entities(
                             '%s:%s' % pure[0]))

    def test_validate_layout_accepts_an_unmodelled_target(self):
        # The mode must be treated as an honest boundary, not as a hole that
        # invalidates the layout -- otherwise these vehicles gain nothing.
        layout = {
            'targets': (),
            'expected_module_entities': ('turretRotator',),
            'expected_crew_entities': (),
            'logical_entity_sources': {
                'turretRotator': {'mode': 'RESOURCE_GEOMETRY_UNMODELLED'},
            },
            'official_geometry': {},
            'parent_transforms': {},
            'required_parents': (),
        }
        validation = internal_hit_layouts.validate_layout(layout)
        self.assertEqual([], list(validation['missing']))

    def test_an_unknown_source_mode_still_fails(self):
        # The guard above must not have widened into accepting anything.
        layout = {
            'targets': (),
            'expected_module_entities': ('turretRotator',),
            'expected_crew_entities': (),
            'logical_entity_sources': {
                'turretRotator': {'mode': 'SOMETHING_ELSE'},
            },
            'official_geometry': {},
            'parent_transforms': {},
            'required_parents': (),
        }
        validation = internal_hit_layouts.validate_layout(layout)
        self.assertTrue(validation['missing'])


class SameTankAliasTests(unittest.TestCase):
    """Catalogue entries that are one physical tank published twice.

    Neither the model-path rule nor the content-hash rule sees these, because
    the variant ships its own model directory and its own resources, so the
    pairs are reviewed by hand.  What must hold is that a pair really is one
    vehicle and that the crew is remapped by role: two identities of one tank
    can list the same crew in a different slot order, and copying positionally
    would seat the driver in the gunner's station.
    """

    def setUp(self):
        tools = ROOT / 'tools'
        if str(tools) not in sys.path:
            sys.path.insert(0, str(tools))
        try:
            import bake_internal_layout_console_0922 as baker
        except ImportError as error:  # pragma: no cover - tool deps absent
            self.skipTest('baker not importable: %s' % error)
        self.baker = baker
        self.layouts = internal_layout_console.CONSOLE_LAYOUTS_0922

    def test_every_alias_pair_is_admitted_on_stated_evidence(self):
        # Two kinds of evidence are accepted, and the baker checks both at
        # bake time rather than trusting the table: the item_defs per-vehicle
        # code slot, so Ch01_Type59 and Ch01_Type59_Gold are one vehicle; or,
        # for a pair that does not share the slot, identical PC hull bounds
        # plus an identical crew roster, as the T-44-100 (P) has against the
        # T-44-100.  A bare name suffix is never evidence on its own.
        import re
        pattern = r'^([a-z]+):([A-Za-z]+[0-9]+)'
        by_slot = 0
        by_measurement = 0
        for target, donor in self.baker.SAME_TANK_ALIASES.items():
            with self.subTest(target=target):
                left = re.match(pattern, target)
                right = re.match(pattern, donor)
                self.assertIsNotNone(left)
                self.assertIsNotNone(right)
                # Same nation either way -- the archive keys on it.
                self.assertEqual(left.group(1), right.group(1))
                if left.group(2) == right.group(2):
                    by_slot += 1
                else:
                    by_measurement += 1
        self.assertTrue(by_slot, 'expected slot-evidenced pairs')
        self.assertTrue(by_measurement, 'expected a measured pair')

    def test_the_evidence_check_refuses_an_unrelated_pair(self):
        # The guard must not have widened into accepting any two vehicles of
        # the same class.
        vehicles = {
            'ussr:R01_Alpha': {'code': 'R01_Alpha', 'vehicle_class': 'x',
                               'crew': (('commander',),), 'archive_id': 1},
            'ussr:R02_Beta': {'code': 'R02_Beta', 'vehicle_class': 'x',
                              'crew': (('gunner',),), 'archive_id': 2},
            'ussr:R03_Gamma': {'code': 'R03_Gamma', 'vehicle_class': 'y',
                               'crew': (('commander',),), 'archive_id': 3},
            'ussr:R01_Alpha_Gold': {'code': 'R01_Alpha_Gold',
                                    'vehicle_class': 'x',
                                    'crew': (('commander',),),
                                    'archive_id': 4},
        }
        cache = ROOT / 'tests'
        # A different class is refused outright.
        self.assertIsNone(self.baker._alias_evidence(
            cache, vehicles, 'ussr:R01_Alpha', 'ussr:R03_Gamma'))
        # Same class, different slot, different roster: refused before any
        # bound is read.
        self.assertIsNone(self.baker._alias_evidence(
            cache, vehicles, 'ussr:R01_Alpha', 'ussr:R02_Beta'))
        # The slot is evidence on its own.
        self.assertEqual('slot', self.baker._alias_evidence(
            cache, vehicles, 'ussr:R01_Alpha_Gold', 'ussr:R01_Alpha'))

    def test_an_aliased_record_carries_the_donor_geometry(self):
        applied = 0
        for target, donor in self.baker.SAME_TANK_ALIASES.items():
            target_key = internal_hit_layouts._profile_key(target)
            donor_key = internal_hit_layouts._profile_key(donor)
            if target_key not in self.layouts or donor_key not in self.layouts:
                continue
            applied += 1
            with self.subTest(target=target):
                a, b = self.layouts[target_key], self.layouts[donor_key]
                # Same modules, same holes, and the same set of crew stations.
                self.assertEqual(b[4], a[4])
                self.assertEqual(b[3], a[3])
                self.assertEqual(sorted(z[2]['payload'] for alt in b[5] for z in alt or ()),
                                 sorted(z[2]['payload'] for alt in a[5] for z in alt or ()))
        self.assertTrue(applied, 'no alias pair reached the table')

    def test_crew_zones_follow_the_target_roster_order(self):
        for target in self.baker.SAME_TANK_ALIASES:
            key = internal_hit_layouts._profile_key(target)
            record = self.layouts.get(key)
            if record is None:
                continue
            with self.subTest(target=target):
                roster, crew = record[2], record[5]
                self.assertEqual(len(roster), len(crew))
                for index, alternatives in enumerate(crew):
                    for zone in alternatives or ():
                        self.assertEqual('crew_%02d' % index, zone[1])

    def test_the_remap_refuses_a_roster_that_is_not_a_permutation(self):
        zones = ((('hull', 'crew_00', {'payload': 'driver'}),),
                 (('turret', 'crew_01', {'payload': 'gunner'}),))
        donor = (('commander',), ('gunner',))
        self.assertIsNotNone(
            self.baker._remap_crew(zones, donor, donor))
        swapped = self.baker._remap_crew(
            zones, donor, (('gunner',), ('commander',)))
        self.assertEqual({'payload': 'gunner'}, swapped[0][0][2])
        self.assertEqual('crew_00', swapped[0][0][1])
        self.assertIsNone(self.baker._remap_crew(
            zones, donor, (('driver',), ('commander',))))
        self.assertIsNone(self.baker._remap_crew(
            zones, donor, (('commander',), ('gunner',), ('driver',))))


class HullRegistrationRuleTests(unittest.TestCase):
    """The rule that decides whether a decoded hull registers at all.

    The baker gates the hull on its footprint extents and on its shell floor
    sitting on the PC box floor, and deliberately does not gate the height
    extent: an open-topped vehicle's armour shell has no roof while the
    collision box encloses the compartment. Vertices remain in source metres,
    so that difference does not move a zone.  These tests pin both
    halves of that rule, because loosening the wrong half would place
    geometry at the wrong height.
    """

    def setUp(self):
        tools = ROOT / 'tools'
        if str(tools) not in sys.path:
            sys.path.insert(0, str(tools))
        try:
            import bake_internal_layout_console_0922 as baker
        except ImportError as error:  # pragma: no cover - tool deps absent
            self.skipTest('baker not importable: %s' % error)
        self.baker = baker

    def _hull(self, low, high):
        return {'armor_1': {'minimum': low, 'maximum': high,
                            'vertices': ()}}

    def test_a_roofless_shell_still_registers(self):
        # Same footprint, floor on the floor, 0.8 m short at the ceiling.
        bound = ((-1.5, 0.0, -3.0), (1.5, 2.0, 3.0))
        surfaces = self._hull((-1.5, 0.0, -3.0), (1.5, 1.2, 3.0))
        gaps = self.baker.residual(surfaces, bound)
        floor = self.baker.floor_offset(surfaces, bound)
        self.assertAlmostEqual(0.0, gaps[0], places=6)
        self.assertAlmostEqual(0.0, gaps[2], places=6)
        self.assertAlmostEqual(0.8, gaps[1], places=6)
        self.assertAlmostEqual(0.0, floor, places=6)
        self.assertLessEqual(max(gaps[0], gaps[2]),
                             internal_layout_console.MAX_HULL_REGISTRATION_M)
        self.assertLessEqual(abs(floor),
                             internal_layout_console.MAX_HULL_REGISTRATION_M)

    def test_a_vertically_shifted_shell_does_not_register(self):
        # Every extent agrees, so the old extent-only check passed it, but the
        # shell sits 0.9 m up: its modules would be placed that far too low.
        bound = ((-1.5, 0.0, -3.0), (1.5, 2.0, 3.0))
        surfaces = self._hull((-1.5, 0.9, -3.0), (1.5, 2.9, 3.0))
        gaps = self.baker.residual(surfaces, bound)
        floor = self.baker.floor_offset(surfaces, bound)
        self.assertEqual((0.0, 0.0, 0.0),
                         tuple(round(value, 6) for value in gaps))
        self.assertAlmostEqual(0.9, floor, places=6)
        self.assertGreater(abs(floor),
                           internal_layout_console.MAX_HULL_REGISTRATION_M)

    def test_a_wider_shell_does_not_register(self):
        # The footprint is the part both platforms must agree on, because it
        # is the closed track-to-track hull.
        bound = ((-1.5, 0.0, -3.0), (1.5, 2.0, 3.0))
        surfaces = self._hull((-2.1, 0.0, -3.0), (2.1, 2.0, 3.0))
        gaps = self.baker.residual(surfaces, bound)
        self.assertGreater(max(gaps[0], gaps[2]),
                           internal_layout_console.MAX_HULL_REGISTRATION_M)

    def test_bake_preserves_source_mesh_positions_despite_a_span_gap(self):
        from test_internal_mesh import box
        bound = ((-1.5, 0.0, -3.0), (1.5, 2.0, 3.0))
        vertices, triangles = box((-.2, .6, -.4), (.2, 1., .4))
        geometry = self.baker._geometry_record(
            {'vertices': vertices, 'triangles': triangles}, bound, 'hull')
        self.assertEqual((vertices, triangles), internal_mesh.decode(geometry['payload']))
        self.assertEqual(bound, geometry['reference_bounds'])


if __name__ == '__main__':
    unittest.main()
