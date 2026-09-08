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
        self.assertGreater(internal_layout_console.MAX_SHELL_RESIDUAL_M, 0.0)

    def test_every_record_is_structurally_usable(self):
        for key, record in self.layouts.items():
            with self.subTest(vehicle=key):
                self.assertEqual(2, len(key))
                (vehicle_class, tier, roster, from_archetype, modules,
                 crew) = record
                for entity in from_archetype:
                    self.assertIn(entity, MODULE_ENTITIES)
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
                           for zone in record[5]]
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
                for entity in required:
                    self.assertIn(entity, entities)


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
                engines = [zone for zone in record[4]
                           if zone[0] == 'engine' and zone[1] == 'hull']
                self.assertTrue(engines)
                self.assertLess(min(zone[3][2] for zone in engines), 0.5)
                checked += 1
        self.assertTrue(checked, 'none of the sampled hulls were decoded')


if __name__ == '__main__':
    unittest.main()
