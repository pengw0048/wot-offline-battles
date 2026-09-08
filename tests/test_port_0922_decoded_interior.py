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
                (vehicle_class, tier, roster, from_archetype, unmodelled,
                 modules, crew) = record
                for entity in from_archetype:
                    self.assertIn(entity, MODULE_ENTITIES)
                for entity in unmodelled:
                    self.assertIn(entity, MODULE_ENTITIES)
                    # An entity is either placed or declared unavailable,
                    # never both.
                    self.assertNotIn(entity, from_archetype)
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
                for entity, parent, zone_id, centre, half in modules:
                    self.assertIn(entity, MODULE_ENTITIES)
                    self.assertIn(parent, PARENTS)
                    self.assertTrue(zone_id)
                    self._check_box(centre, half)
                for parent, zone_id, centre, half in crew:
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
                           for zone in record[6]]
                self.assertEqual(len(centres), len(set(centres)))

    def test_decoded_vehicles_cover_every_module_target(self):
        # build_layout reports a layout invalid when any module target has no
        # geometry source, so a decoded record must carry the complete set
        # less the tracks and gun the client supplies natively.
        required = ('ammoBay', 'engine', 'fuelTank', 'radio',
                    'surveyingDevice', 'turretRotator')
        for key, record in self.layouts.items():
            with self.subTest(vehicle=key):
                entities = set(zone[0] for zone in record[5])
                # A target is accounted for either by a zone or by being
                # declared unavailable; validate_layout accepts both and
                # nothing else.
                accounted = entities | set(record[4])
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
        self.assertEqual(layouts[key][5], profile[5])
        self.assertEqual(layouts[key][6], profile[6])

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

    def test_an_archetype_filled_entity_is_named_in_the_source(self):
        # A casemate has no traverse mechanism in the resources, so that one
        # entity keeps the retained archetype.  The record must say so rather
        # than presenting the whole layout as decoded.
        layouts = internal_layout_console.CONSOLE_LAYOUTS_0922
        mixed = sorted(key for key, record in layouts.items() if record[3])
        if not mixed:
            self.skipTest('no vehicle needed an archetype-filled entity')
        key = mixed[0]
        vehicle = '%s:%s' % key
        unused_key, profile = internal_hit_layouts._compiled_profile(vehicle)
        self.assertTrue(profile[0].startswith('decoded_collision_surfaces+'))
        for entity in layouts[key][3]:
            self.assertIn(entity, profile[0])
        self.assertEqual(tuple(layouts[key][3]),
                         internal_hit_layouts.decoded_archetype_entities(
                             vehicle))
        # A purely decoded vehicle reports no borrowed entity.
        pure = sorted(k for k, record in layouts.items() if not record[3])
        self.assertTrue(pure)
        self.assertEqual((), internal_hit_layouts.decoded_archetype_entities(
            '%s:%s' % pure[0]))

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
                engines = [zone for zone in record[5]
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
                              if record[4])

    def test_some_vehicle_declares_an_unmodelled_module(self):
        self.assertTrue(self.records,
                        'expected vehicles whose resources model no traverse')

    def test_an_unmodelled_module_is_named_in_the_source(self):
        key = self.records[0]
        unused_key, profile = internal_hit_layouts._compiled_profile(
            '%s:%s' % key)
        self.assertIsNotNone(profile)
        self.assertIn('+unmodelled:', profile[0])
        for entity in self.layouts[key][4]:
            self.assertIn(entity, profile[0])

    def test_the_helper_reports_exactly_the_recorded_entities(self):
        key = self.records[0]
        self.assertEqual(
            tuple(self.layouts[key][4]),
            internal_hit_layouts.decoded_unmodelled_entities('%s:%s' % key))
        pure = sorted(k for k, record in self.layouts.items()
                      if not record[4])
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
