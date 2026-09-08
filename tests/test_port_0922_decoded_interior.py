"""The decoded interior table, and the runtime preferring it over archetypes.

These are pure-data tests over the baked table plus the resolution path in
internal_hit_layouts.  They prove the table is internally coherent and that a
decoded vehicle never falls back to a reconstructed archetype.  They cannot
prove that a shot into a decoded ammunition rack crits correctly on the exact
Windows client; that needs the client.
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'res' / 'scripts' / 'client'))

from gui.mods.offline_lan_0922 import internal_hit_layouts
from gui.mods.offline_lan_0922 import internal_layout_console
from gui.mods.offline_lan_0922 import internal_layout_profiles

MODULE_ENTITIES = frozenset(internal_hit_layouts.MODULE_TARGETS)
PARENTS = frozenset(internal_hit_layouts.SUPPORTED_PARENTS)


class DecodedInteriorTableTests(unittest.TestCase):

    def setUp(self):
        self.layouts = internal_layout_console.CONSOLE_LAYOUTS_0922

    def test_table_identifies_the_pinned_client_and_its_sources(self):
        self.assertEqual('0.9.22.0.1', internal_layout_console.CLIENT_VERSION)
        self.assertEqual('1513', internal_layout_console.CLIENT_BUILD)
        self.assertEqual('decoded', internal_layout_console.CONFIDENCE)
        self.assertEqual(internal_layout_console.DECODED_COUNT,
                         len(self.layouts))
        self.assertEqual(680, internal_layout_console.CATALOGUE_SIZE)
        # Provenance must name where the geometry and the reference frame came
        # from, so a reader can re-derive every number.
        self.assertTrue(internal_layout_console.GEOMETRY_SOURCES)
        self.assertTrue(internal_layout_console.REFERENCE_FRAME_SOURCE)
        self.assertGreater(internal_layout_console.MAX_HULL_REGISTRATION_M, 0.0)

    def test_every_record_is_structurally_usable(self):
        for key, record in self.layouts.items():
            with self.subTest(vehicle=key):
                self.assertEqual(2, len(key))
                (vehicle_class, tier, roster, unmodelled, modules,
                 crew) = record
                for entity in unmodelled:
                    self.assertIn(entity, MODULE_ENTITIES)
                    # An entity is either placed or declared unavailable,
                    # never both.
                    self.assertNotIn(entity,
                                     set(zone[0] for zone in modules))
                self.assertTrue(vehicle_class)
                self.assertTrue(1 <= tier <= 10)
                self.assertTrue(roster)
                self.assertTrue(modules)
                # One zone per crew slot, in slot order: build_layout indexes
                # crew_zones by crew index and validates the roster against
                # the live descriptor.
                self.assertEqual(len(roster), len(crew))
                # A crew station the resources do not model is a hole kept in
                # place, so the list stays indexable by the live descriptor's
                # crew index.  It must be exactly None -- never a placeholder
                # box that would read as a real station.
                self.assertTrue(any(zone is not None for zone in crew),
                                'a record with no crew station at all is the '
                                'wrong file, not a hole')
                for entity, parent, zone_id, centre, half in modules:
                    self.assertIn(entity, MODULE_ENTITIES)
                    self.assertIn(parent, PARENTS)
                    self.assertTrue(zone_id)
                    self._check_box(centre, half)
                for zone in crew:
                    if zone is None:
                        continue
                    parent, zone_id, centre, half = zone
                    self.assertIn(parent, PARENTS)
                    self.assertTrue(zone_id)
                    self._check_box(centre, half)

    def _check_box(self, centre, half):
        self.assertEqual(3, len(centre))
        self.assertEqual(3, len(half))
        for axis in range(3):
            self.assertGreaterEqual(centre[axis], 0.0)
            self.assertLessEqual(centre[axis], 1.0)
            self.assertGreater(half[axis], 0.0)
            self.assertLessEqual(half[axis], 0.5)

    def test_no_two_crew_share_a_seat(self):
        for key, record in self.layouts.items():
            with self.subTest(vehicle=key):
                centres = [tuple(round(value, 4) for value in zone[2])
                           for zone in record[5] if zone is not None]
                self.assertEqual(len(centres), len(set(centres)))

    def test_decoded_vehicles_cover_every_module_target(self):
        # build_layout reports a layout invalid when any module target has no
        # geometry source, so a decoded record must carry the complete set
        # less the tracks and gun the client supplies natively.
        required = ('ammoBay', 'engine', 'fuelTank', 'radio',
                    'surveyingDevice', 'turretRotator')
        for key, record in self.layouts.items():
            with self.subTest(vehicle=key):
                entities = set(zone[0] for zone in record[4])
                # A target is accounted for either by a zone or by being
                # declared unavailable; validate_layout accepts both and
                # nothing else.
                accounted = entities | set(record[3])
                for entity in required:
                    self.assertIn(entity, accounted)


class DecodedInteriorResolutionTests(unittest.TestCase):

    def test_runtime_prefers_decoded_geometry_over_the_archetype(self):
        layouts = internal_layout_console.CONSOLE_LAYOUTS_0922
        overlapping = [key for key in layouts
                       if key in internal_layout_profiles.PROFILES]
        self.assertTrue(overlapping, 'expected decoded and authored to overlap')
        key = sorted(overlapping)[0]
        vehicle = '%s:%s' % key
        unused_key, profile = internal_hit_layouts._compiled_profile(vehicle)
        self.assertIsNotNone(profile)
        self.assertEqual('decoded', profile[3])
        self.assertTrue(profile[0].startswith('decoded_collision_surfaces'))
        self.assertIsNot(internal_layout_profiles.PROFILES[key], profile)
        self.assertEqual(layouts[key][4], profile[5])
        self.assertEqual(layouts[key][5], profile[6])

    def test_decoded_layout_available_matches_the_table(self):
        layouts = internal_layout_console.CONSOLE_LAYOUTS_0922
        key = sorted(layouts)[0]
        self.assertTrue(internal_hit_layouts.decoded_layout_available(
            '%s:%s' % key))
        self.assertFalse(internal_hit_layouts.decoded_layout_available(
            'ussr:R999_not_a_vehicle'))

    def test_a_vehicle_with_no_source_is_not_invented(self):
        # These have no counterpart in the source resources.  They must not
        # acquire decoded geometry, and the audit must say so rather than the
        # table quietly carrying a guess.
        for vehicle in ('ussr:R119_Object_777', 'usa:A106_M48A2_120',
                        'germany:G105_T-55_NVA_DDR'):
            with self.subTest(vehicle=vehicle):
                self.assertFalse(
                    internal_hit_layouts.decoded_layout_available(vehicle))

    def test_a_resolved_decoded_record_never_borrows_from_a_reconstruction(self):
        # Every zone in a decoded record comes from one source: the decoded
        # collision surfaces, registered against the PC bounds.  The retained
        # archetypes are a whole-vehicle fallback and must never be mixed into
        # a decoded record -- otherwise a reader could only tell real geometry
        # from a reconstruction by parsing the provenance string.
        layouts = internal_layout_console.CONSOLE_LAYOUTS_0922
        checked = 0
        for key, record in layouts.items():
            unused_key, profile = internal_hit_layouts._compiled_profile(
                '%s:%s' % key)
            self.assertIsNotNone(profile)
            if not profile[0].startswith('decoded_collision_surfaces'):
                continue
            checked += 1
            with self.subTest(vehicle=key):
                self.assertNotIn('archetype', profile[0])
                self.assertEqual(tuple(record[4]), profile[5])
        self.assertTrue(checked, 'no decoded record resolved')

    def test_an_incomplete_decoded_crew_yields_to_a_complete_archetype(self):
        # A decoded record whose resources model no station for some crew
        # leaves those crewmen unhittable.  That is right when the alternative
        # is no interior, but not when an archetype already seats all of them:
        # an approximate crew the player can hit is closer to retail than an
        # exact one he cannot.  A module hole must not trigger this.
        layouts = internal_layout_console.CONSOLE_LAYOUTS_0922
        yielded = kept = 0
        for key, record in layouts.items():
            holed = any(zone is None for zone in record[5])
            unused_key, authored = internal_hit_layouts._profile_for_key(key)
            unused_key, profile = internal_hit_layouts._compiled_profile(
                '%s:%s' % key)
            decoded = profile[0].startswith('decoded_collision_surfaces')
            with self.subTest(vehicle=key):
                if holed and authored is not None:
                    self.assertFalse(decoded)
                    yielded += 1
                else:
                    self.assertTrue(decoded)
                    kept += 1
        self.assertTrue(yielded, 'expected a vehicle to yield')
        self.assertTrue(kept, 'expected decoded records to be kept')

    def test_an_archetype_only_vehicle_is_labelled_as_reconstructed(self):
        # The fallback tier must still exist and must still say what it is.
        decoded = internal_layout_console.CONSOLE_LAYOUTS_0922
        fallback = sorted(key for key in internal_layout_profiles.PROFILES
                          if key not in decoded)
        if not fallback:
            self.skipTest('every authored profile is now decoded')
        vehicle = '%s:%s' % fallback[0]
        self.assertFalse(
            internal_hit_layouts.decoded_layout_available(vehicle))
        unused_key, profile = internal_hit_layouts._compiled_profile(vehicle)
        self.assertIsNotNone(profile)
        self.assertNotIn('decoded_collision_surfaces', profile[0])

    def test_rear_engined_tanks_place_the_engine_at_the_rear(self):
        # A frame or sign error in registration would not survive this: the
        # decoded zones are in the component's own bounding box, where +z is
        # forward, so a rear-engined hull must carry its engine behind centre
        # and its driver ahead of it.
        layouts = internal_layout_console.CONSOLE_LAYOUTS_0922
        checked = 0
        for vehicle in ('ussr:R45_IS-7', 'ussr:R04_T-34', 'germany:G42_Maus',
                        'china:Ch01_Type59'):
            key = internal_hit_layouts._profile_key(vehicle)
            record = layouts.get(key)
            if record is None:
                continue
            with self.subTest(vehicle=vehicle):
                engines = [zone for zone in record[4]
                           if zone[0] == 'engine' and zone[1] == 'hull']
                self.assertTrue(engines)
                self.assertLess(min(zone[3][2] for zone in engines), 0.5)
                checked += 1
        self.assertTrue(checked, 'none of the sampled hulls were decoded')


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
                self.assertEqual(sorted(zone[2] for zone in b[5]),
                                 sorted(zone[2] for zone in a[5]))
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
                for index, zone in enumerate(crew):
                    self.assertEqual('crew_%02d' % index, zone[1])

    def test_the_remap_refuses_a_roster_that_is_not_a_permutation(self):
        zones = (('hull', 'crew_00', (0.1, 0.2, 0.3), (0.01, 0.02, 0.03)),
                 ('turret', 'crew_01', (0.4, 0.5, 0.6), (0.04, 0.05, 0.06)))
        donor = (('commander',), ('gunner',))
        self.assertIsNotNone(
            self.baker._remap_crew(zones, donor, donor))
        swapped = self.baker._remap_crew(
            zones, donor, (('gunner',), ('commander',)))
        self.assertEqual((0.4, 0.5, 0.6), swapped[0][2])
        self.assertEqual('crew_00', swapped[0][1])
        self.assertIsNone(self.baker._remap_crew(
            zones, donor, (('driver',), ('commander',))))
        self.assertIsNone(self.baker._remap_crew(
            zones, donor, (('commander',), ('gunner',), ('driver',))))


class HullRegistrationRuleTests(unittest.TestCase):
    """The rule that decides whether a decoded hull registers at all.

    The baker gates the hull on its footprint extents and on its shell floor
    sitting on the PC box floor, and deliberately does not gate the height
    extent: an open-topped vehicle's armour shell has no roof while the
    collision box encloses the compartment, and because ``fractions`` anchors
    a Console metre coordinate at the box minimum rather than scaling it to
    the box span, that difference does not move a zone.  These tests pin both
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

    def test_anchoring_preserves_a_zone_position_despite_a_span_gap(self):
        # The reason the height extent need not be gated: fractions() anchors
        # at the box minimum, and fit_target reconstructs against the same
        # build's bbox, so metres in equal metres out.
        bound = ((-1.5, 0.0, -3.0), (1.5, 2.0, 3.0))
        box = ((-0.2, 0.6, -0.4), (0.2, 1.0, 0.4))
        shaped = self.baker.fractions(box, bound)
        self.assertIsNotNone(shaped)
        centre, half = shaped
        for axis in range(3):
            span = bound[1][axis] - bound[0][axis]
            middle = bound[0][axis] + centre[axis] * span
            extent = half[axis] * span
            self.assertAlmostEqual(
                (box[0][axis] + box[1][axis]) * 0.5, middle, places=3)
            self.assertAlmostEqual(
                (box[1][axis] - box[0][axis]) * 0.5, extent, places=3)


if __name__ == '__main__':
    unittest.main()
