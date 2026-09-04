from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import track_damage


class _Vector2(object):
    """Stand-in for the native Vector2 the chassis descriptor publishes."""

    def __init__(self, x, y):
        self.x = float(x)
        self.y = float(y)

    def __getitem__(self, index):
        return (self.x, self.y)[index]


class _Point(object):
    """A native Vector3 that refuses sequence access, like Math.Vector3."""

    def __init__(self, x, y, z):
        self.x, self.y, self.z = float(x), float(y), float(z)


class _StrictChassis(object):
    """#1513 item components raise on mapping-style access."""

    def __init__(self, **values):
        self.__dict__.update(values)

    def _forbidden(self, *unused_args, **unused_kwargs):
        raise AssertionError('Operation is not allowed')

    get = _forbidden
    keys = _forbidden
    items = _forbidden


# Exact global 0.9.22 A83_T110E4 chassis data: topRightCarryingPoint
# 1.51097 2.00908, drivingWheels 'WD_L0 WD_L6' with radii 0.329521 and 0.3375,
# published as radius * 2.2.
T110E4_HALF_LENGTH = 2.00908
T110E4_FRONT_WHEEL = 0.329521 * 2.2
T110E4_REAR_WHEEL = 0.3375 * 2.2


def _t110e4_chassis(**overrides):
    values = {
        'name': 'Chassis_T110E4',
        'topRightCarryingPoint': _Vector2(1.51097, T110E4_HALF_LENGTH),
        'drivingWheelsSizes': (T110E4_FRONT_WHEEL, T110E4_REAR_WHEEL),
        'maxHealth': 250,
        'maxRegenHealth': 190,
    }
    values.update(overrides)
    return _StrictChassis(**values)


def _descriptor(chassis=None, name='usa:A83_T110E4', mode=0):
    return types.SimpleNamespace(
        name=name,
        chassis=chassis if chassis is not None else _t110e4_chassis(),
        type=types.SimpleNamespace(name=name, mode=mode))


class _Section(object):

    def __init__(self, values):
        self.values = values
        self.reads = []

    def readFloat(self, path, default):
        self.reads.append(path)
        return self.values.get(path, default)


class _ResMgr(object):

    def __init__(self, sections):
        self.sections = sections
        self.opened = []
        self.purged = []

    def openSection(self, path):
        self.opened.append(path)
        return self.sections.get(path)

    def purge(self, path, recursive):
        self.purged.append((path, bool(recursive)))


def _t110e4_res_mgr(factor=3.0, path=None):
    path = path or 'scripts/item_defs/vehicles/usa/A83_T110E4.xml'
    return _ResMgr({path: _Section({
        'chassis/Chassis_T110E4/bulkHealthFactor': factor})})


class TrackDamageChannelTests(unittest.TestCase):

    def test_nonzero_armour_track_material_selects_armor_damage(self):
        # vehicles.py::_readArmor turns the shipped 'auto' damageKind into 0
        # whenever the material armour is nonzero, which every chassis
        # leftTrack/rightTrack value is.
        material = types.SimpleNamespace(armor=20.0, damageKind=0)
        self.assertEqual(
            track_damage.ARMOR_DAMAGE_INDEX,
            track_damage.material_damage_index(material))

    def test_device_damage_kind_selects_the_devices_channel(self):
        material = types.SimpleNamespace(armor=0.0, damageKind=1)
        self.assertEqual(
            track_damage.DEVICE_DAMAGE_INDEX,
            track_damage.material_damage_index(material))

    def test_missing_or_malformed_damage_kind_is_unresolved(self):
        for material in (types.SimpleNamespace(armor=20.0),
                         types.SimpleNamespace(damageKind=None),
                         types.SimpleNamespace(damageKind='armor'),
                         types.SimpleNamespace(damageKind=7),
                         types.SimpleNamespace(damageKind=True),
                         None):
            self.assertIsNone(track_damage.material_damage_index(material))


class WheelZoneGeometryTests(unittest.TestCase):

    def test_exact_t110e4_bounds_anchor_each_wheel_to_its_own_end(self):
        bounds = track_damage.wheel_zone_bounds(_t110e4_chassis())
        self.assertAlmostEqual(
            T110E4_HALF_LENGTH - T110E4_FRONT_WHEEL, bounds[0], places=6)
        self.assertAlmostEqual(
            T110E4_REAR_WHEEL - T110E4_HALF_LENGTH, bounds[1], places=6)

    def test_a_native_component_is_read_without_mapping_access(self):
        # _StrictChassis raises on .get(), exactly as a #1513 item component
        # does; reaching the bounds at all proves attribute dispatch.
        self.assertIsNotNone(
            track_damage.wheel_zone_bounds(_t110e4_chassis()))

    def test_front_middle_and_rear_zones_on_the_exact_boundaries(self):
        bounds = track_damage.wheel_zone_bounds(_t110e4_chassis())
        front_bound, rear_bound = bounds
        # The boundary point itself belongs to the wheel; one micron inside
        # the run is already the reduced middle zone.
        self.assertEqual(
            track_damage.ZONE_FRONT,
            track_damage.classify_zone(front_bound, bounds))
        self.assertEqual(
            track_damage.ZONE_MIDDLE,
            track_damage.classify_zone(front_bound - 1.0e-6, bounds))
        self.assertEqual(
            track_damage.ZONE_REAR,
            track_damage.classify_zone(rear_bound, bounds))
        self.assertEqual(
            track_damage.ZONE_MIDDLE,
            track_damage.classify_zone(rear_bound + 1.0e-6, bounds))
        self.assertEqual(
            track_damage.ZONE_MIDDLE,
            track_damage.classify_zone(0.0, bounds))

    def test_overhanging_idler_beyond_the_carrying_extent_is_still_a_wheel(
            self):
        bounds = track_damage.wheel_zone_bounds(_t110e4_chassis())
        self.assertEqual(
            track_damage.ZONE_FRONT,
            track_damage.classify_zone(T110E4_HALF_LENGTH + 0.2, bounds))
        self.assertEqual(
            track_damage.ZONE_REAR,
            track_damage.classify_zone(-T110E4_HALF_LENGTH - 0.2, bounds))

    def test_exact_t1_hmc_overlapping_end_zones_use_safe_fallback(self):
        chassis = {
            'topRightCarryingPoint': _Vector2(1.0, 0.76849),
            'drivingWheelsSizes': (0.35 * 2.2, 0.464022994 * 2.2),
        }

        self.assertIsNone(track_damage.wheel_zone_bounds(chassis))

    def test_impossible_geometry_is_rejected_instead_of_guessed(self):
        cases = [
            {'topRightCarryingPoint': None},
            {'topRightCarryingPoint': _Vector2(1.5, 0.0)},
            {'topRightCarryingPoint': _Vector2(1.5, -2.0)},
            {'topRightCarryingPoint': _Vector2(1.5, float('nan'))},
            {'topRightCarryingPoint': _Vector2(1.5, float('inf'))},
            {'drivingWheelsSizes': None},
            {'drivingWheelsSizes': (0.7,)},
            {'drivingWheelsSizes': (0.0, 0.7)},
            {'drivingWheelsSizes': (0.7, -0.7)},
            {'drivingWheelsSizes': (float('nan'), 0.7)},
            # Zones that meet or overlap would make the whole track a wheel.
            {'drivingWheelsSizes': (2.0, 2.02)},
            {'drivingWheelsSizes': (2.00908, 2.00908)},
        ]
        for overrides in cases:
            self.assertIsNone(
                track_damage.wheel_zone_bounds(_t110e4_chassis(**overrides)),
                overrides)
        self.assertIsNone(track_damage.wheel_zone_bounds(None))

    def test_classify_rejects_missing_bounds_and_non_finite_coordinates(self):
        bounds = track_damage.wheel_zone_bounds(_t110e4_chassis())
        self.assertIsNone(track_damage.classify_zone(0.0, None))
        self.assertIsNone(track_damage.classify_zone(float('nan'), bounds))
        self.assertIsNone(track_damage.classify_zone(None, bounds))


class LocalContactTests(unittest.TestCase):

    def test_contact_is_a_pure_function_of_the_ray_and_the_distance(self):
        start = _Point(0.0, 0.0, -5.0)
        end = _Point(0.0, 0.0, 5.0)
        self.assertEqual(
            (0.0, 0.0, -3.0),
            track_damage.local_contact_point(start, end, 2.0))
        # Two collisions at one distance on one component share one point, so
        # a duplicate material identity cannot attach a different contact.
        self.assertEqual(
            track_damage.local_contact_point(start, end, 2.0),
            track_damage.local_contact_point(start, end, 2.0))

    def test_sequence_and_attribute_vectors_agree(self):
        self.assertEqual(
            track_damage.local_contact_point(
                (1.0, 2.0, 3.0), (1.0, 2.0, 7.0), 1.0),
            track_damage.local_contact_point(
                _Point(1.0, 2.0, 3.0), _Point(1.0, 2.0, 7.0), 1.0))

    def test_degenerate_and_malformed_rays_are_rejected(self):
        point = _Point(1.0, 1.0, 1.0)
        self.assertIsNone(
            track_damage.local_contact_point(point, point, 1.0))
        self.assertIsNone(track_damage.local_contact_point(
            point, _Point(1.0, 1.0, 5.0), -1.0))
        self.assertIsNone(track_damage.local_contact_point(
            point, _Point(1.0, 1.0, 5.0), float('nan')))
        self.assertIsNone(track_damage.local_contact_point(
            None, _Point(1.0, 1.0, 5.0), 1.0))


class BulkHealthFactorTests(unittest.TestCase):

    def setUp(self):
        track_damage.reset_caches()
        track_damage.reset_diagnostics()

    def tearDown(self):
        track_damage.reset_caches()
        track_damage.reset_diagnostics()

    def test_a_descriptor_that_already_carries_the_factor_wins(self):
        descriptor = _descriptor(_t110e4_chassis(bulkHealthFactor=3.0))
        res_mgr = _t110e4_res_mgr()
        self.assertEqual(
            3.0, track_damage.bulk_health_factor(descriptor, res_mgr))
        self.assertEqual([], res_mgr.opened)

    def test_vehicle_local_raw_resource_supplies_the_client_only_gap(self):
        # #788 _readChassis reads bulkHealthFactor only when
        # `not IS_CLIENT and not IS_BOT`, so the client descriptor never has
        # it and the value has to come from the raw vehicle XML.
        descriptor = _descriptor()
        res_mgr = _t110e4_res_mgr()
        self.assertEqual(
            3.0, track_damage.bulk_health_factor(descriptor, res_mgr))
        self.assertEqual(
            ['scripts/item_defs/vehicles/usa/A83_T110E4.xml'],
            res_mgr.opened)
        self.assertEqual(
            ['chassis/Chassis_T110E4/bulkHealthFactor'],
            res_mgr.sections[res_mgr.opened[0]].reads)
        self.assertEqual(
            [('scripts/item_defs/vehicles/usa/A83_T110E4.xml', True)],
            res_mgr.purged)

    def test_a_non_default_factor_is_read_from_the_data_not_assumed(self):
        # Pz.Kpfw. III Ausf. K is the shipped 5.0 exception; a blanket /3
        # would silently mis-scale it.
        descriptor = _descriptor(
            _t110e4_chassis(name='Chassis_PzIII_K'), name='germany:G94_PzIII_K')
        res_mgr = _ResMgr({
            'scripts/item_defs/vehicles/germany/G94_PzIII_K.xml': _Section({
                'chassis/Chassis_PzIII_K/bulkHealthFactor': 5.0})})
        self.assertEqual(
            5.0, track_damage.bulk_health_factor(descriptor, res_mgr))

    def test_a_siege_descriptor_reads_its_own_mode_file(self):
        descriptor = _descriptor(
            _t110e4_chassis(name='Chassis_S04_Strv_103B'),
            name='sweden:S04_Strv_103B', mode=1)
        res_mgr = _ResMgr({
            'scripts/item_defs/vehicles/sweden/'
            'S04_Strv_103B_siege_mode.xml': _Section({
                'chassis/Chassis_S04_Strv_103B/bulkHealthFactor': 3.0})})
        self.assertEqual(
            3.0, track_damage.bulk_health_factor(descriptor, res_mgr))

    def test_the_resolved_value_is_cached_by_vehicle_and_chassis_identity(
            self):
        descriptor = _descriptor()
        res_mgr = _t110e4_res_mgr()
        for _unused in range(5):
            self.assertEqual(
                3.0, track_damage.bulk_health_factor(descriptor, res_mgr))
        self.assertEqual(1, len(res_mgr.opened))

    def test_a_capability_failure_is_contained_and_cached_as_unresolved(self):
        descriptor = _descriptor()
        res_mgr = _ResMgr({})
        self.assertIsNone(
            track_damage.bulk_health_factor(descriptor, res_mgr))
        self.assertIsNone(
            track_damage.bulk_health_factor(descriptor, res_mgr))
        self.assertEqual(1, len(res_mgr.opened))

    def test_a_raising_resource_manager_does_not_escape(self):
        class _Broken(object):
            def openSection(self, unused_path):
                raise RuntimeError('resource subsystem unavailable')

        self.assertIsNone(
            track_damage.bulk_health_factor(_descriptor(), _Broken()))

    def test_an_absent_or_impossible_factor_stays_unresolved(self):
        for value in (0.0, -3.0, float('nan')):
            track_damage.reset_caches()
            self.assertIsNone(track_damage.bulk_health_factor(
                _descriptor(), _t110e4_res_mgr(factor=value)))

    def test_an_unidentifiable_descriptor_never_reaches_the_resources(self):
        res_mgr = _t110e4_res_mgr()
        for descriptor in (
                _descriptor(name='A83_T110E4'),
                _descriptor(_t110e4_chassis(name=None)),
                types.SimpleNamespace(
                    name='usa:A83_T110E4',
                    chassis=_t110e4_chassis(),
                    type=types.SimpleNamespace(mode=99))):
            self.assertIsNone(
                track_damage.bulk_health_factor(descriptor, res_mgr))
        self.assertEqual([], res_mgr.opened)


class ZoneScaleTests(unittest.TestCase):

    def test_both_end_wheels_take_the_full_roll(self):
        self.assertEqual(
            1.0, track_damage.zone_damage_scale(track_damage.ZONE_FRONT, 3.0))
        self.assertEqual(
            1.0, track_damage.zone_damage_scale(track_damage.ZONE_REAR, 3.0))

    def test_the_middle_run_divides_by_the_chassis_bulk_factor(self):
        self.assertAlmostEqual(
            1.0 / 3.0,
            track_damage.zone_damage_scale(track_damage.ZONE_MIDDLE, 3.0))
        self.assertAlmostEqual(
            1.0 / 5.0,
            track_damage.zone_damage_scale(track_damage.ZONE_MIDDLE, 5.0))

    def test_an_unresolved_middle_factor_has_no_scale(self):
        self.assertIsNone(
            track_damage.zone_damage_scale(track_damage.ZONE_MIDDLE, None))
        self.assertIsNone(
            track_damage.zone_damage_scale(track_damage.ZONE_MIDDLE, 0.0))
        self.assertIsNone(track_damage.zone_damage_scale(None, 3.0))


class DiagnosticBudgetTests(unittest.TestCase):

    def setUp(self):
        track_damage.reset_diagnostics()

    def tearDown(self):
        track_damage.reset_diagnostics()

    def test_one_line_per_signature_and_a_hard_session_cap(self):
        written = []

        class _Stream(object):
            @staticmethod
            def write(text):
                written.append(text)

        stdout = sys.stdout
        sys.stdout = _Stream()
        try:
            self.assertTrue(track_damage.report(('a', 1), 'first'))
            self.assertFalse(track_damage.report(('a', 1), 'first again'))
            self.assertTrue(track_damage.report(('a', 2), 'second'))
            for index in range(track_damage._MAX_DIAGNOSTIC_SIGNATURES):
                track_damage.report(('cap', index), 'x')
        finally:
            sys.stdout = stdout
        self.assertEqual(
            ['[Offline LAN 0.9.22] TRACK first\n',
             '[Offline LAN 0.9.22] TRACK second\n'], written[:2])
        self.assertEqual(
            track_damage._MAX_DIAGNOSTIC_SIGNATURES, len(written))


if __name__ == '__main__':
    unittest.main()
