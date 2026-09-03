from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import critical_damage
from gui.mods.offline_lan_0922 import device_damage
from gui.mods.offline_lan_0922 import internal_hit_layouts
from gui.mods.offline_lan_0922 import internal_layout_profiles


class _Extra(object):

    def __init__(self, name):
        self.name = name


class _Material(object):

    def __init__(self, name, chance=1.0):
        self.extra = _Extra(name)
        self.armor = 20.0
        self.vehicleDamageFactor = 0.0
        self.chanceToHitByProjectile = chance
        self.chanceToHitByExplosion = chance


class _Strict1513Component(object):

    def __init__(self, **values):
        self.__dict__.update(values)
        self.mapping_calls = 0

    def _forbidden(self, *unused_args, **unused_kwargs):
        self.mapping_calls += 1
        raise AssertionError('Operation is not allowed')

    get = _forbidden
    __contains__ = _forbidden
    __getitem__ = _forbidden
    __iter__ = _forbidden
    items = _forbidden
    keys = _forbidden
    values = _forbidden


class _Point(object):

    def __init__(self, x, y, z):
        self.x, self.y, self.z = float(x), float(y), float(z)


class _IdentityMatrix(object):

    def __init__(self, unused_value):
        pass

    def invert(self):
        pass

    def applyPoint(self, value):
        return value


class _TranslateXMatrix(object):

    def __init__(self, offset):
        self.offset = float(offset)

    def applyPoint(self, value):
        return _Point(value.x + self.offset, value.y, value.z)


def _strict_1513_descriptor():
    health = lambda: _Strict1513Component(
        maxHealth=100, maxRegenHealth=50)
    return types.SimpleNamespace(
        chassis=health(),
        engine=_Strict1513Component(
            maxHealth=100, maxRegenHealth=50,
            fireStartingChance=0.12),
        hull=_Strict1513Component(ammoBayHealth=health()),
        fuelTank=health(), radio=health(), gun=health(),
        turret=_Strict1513Component(
            turretRotatorHealth=health(),
            surveyingDeviceHealth=health()),
        miscAttrs=_Strict1513Component(engineHealthFactor=1.5),
        type=types.SimpleNamespace(
            crewRoles=(('commander',), ('driver',), ('loader',))))


def _descriptor():
    return types.SimpleNamespace(
        chassis={'maxHealth': 100, 'maxRegenHealth': 50},
        engine={'maxHealth': 100, 'maxRegenHealth': 50,
                'fireStartingChance': 0.0},
        hull={'ammoBayHealth': {'maxHealth': 100,
                               'maxRegenHealth': 50}},
        fuelTank={'maxHealth': 100, 'maxRegenHealth': 50},
        radio={'maxHealth': 100, 'maxRegenHealth': 50},
        gun={'maxHealth': 100, 'maxRegenHealth': 50},
        turret={
            'turretRotatorHealth': {'maxHealth': 100,
                                   'maxRegenHealth': 50},
            'surveyingDeviceHealth': {'maxHealth': 100,
                                      'maxRegenHealth': 50}},
        miscAttrs={},
        type=types.SimpleNamespace(
            crewRoles=(('commander',), ('driver',), ('loader',))))


def _layout_descriptor(name, crew_roles):
    def component(component_name, **values):
        defaults = {
            'name': component_name,
            'id': 1,
            'compactDescr': 1,
            'models': None,
            'materials': {},
            'hitTester': types.SimpleNamespace(bbox=(
                (-1.5, -0.5, -2.5), (1.5, 1.5, 2.5), None)),
            'weight': 100.0,
        }
        defaults.update(values)
        return _Strict1513Component(**defaults)

    return _Strict1513Component(
        type=types.SimpleNamespace(name=name, crewRoles=crew_roles),
        chassis=component('chassis'),
        hull=component('hull'),
        turret=component('turret', yawLimits=(-3.14, 3.14)),
        gun=component('gun', maxAmmo=30, shots=()),
        engine=component('engine', weight=120.0),
        fuelTank=component('fuelTank', weight=40.0),
        radio=component('radio', weight=15.0))


class CriticalDamageTests(unittest.TestCase):

    def setUp(self):
        self.player = types.SimpleNamespace(
            playerVehicleID=999,
            arena=types.SimpleNamespace(onVehicleKilled=lambda *args: None),
            vehicleTypeDescriptor=_descriptor())
        self.bigworld = types.ModuleType('BigWorld')
        self.bigworld.player = lambda: self.player
        self.bigworld.time = lambda: 12.0
        self.math = types.ModuleType('Math')

    def test_native_1513_components_never_call_forbidden_legacy_get(self):
        descriptor = _strict_1513_descriptor()

        self.assertEqual(150, device_damage.device_max_hp(
            descriptor, 'engineHealth'))
        for name in (
                'ammoBayHealth', 'fuelTankHealth', 'radioHealth',
                'leftTrackHealth', 'rightTrackHealth', 'gunHealth',
                'turretRotatorHealth', 'surveyingDeviceHealth'):
            self.assertEqual(100, device_damage.device_max_hp(
                descriptor, name))

        vehicle = types.SimpleNamespace(
            typeDescriptor=descriptor, health=0,
            devices_hp={}, _destroyed_devices=set(), _crew_ko=set(),
            is_on_fire=False)
        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}):
            payload = critical_damage.apply_death(vehicle, 'shot')
        self.assertIsNotNone(payload)
        self.assertEqual(
            set(critical_damage._OFFH_DEATH_DEVICES),
            vehicle._destroyed_devices)

    def test_equipment_engine_loss_uses_module_hp_and_can_destroy_engine(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500,
            devices_hp={}, _destroyed_devices=set(), _crew_ko=set(),
            is_on_fire=False)

        first = critical_damage.damage_device_over_time(
            vehicle, 'engineHealth', 1.5, 'removedRpmLimiter')
        final = critical_damage.damage_device_over_time(
            vehicle, 'engineHealth', 200.0, 'removedRpmLimiter')

        self.assertAlmostEqual(98.5, first['devices'][0]['hp'])
        self.assertEqual(0.0, vehicle.devices_hp['engineHealth'])
        self.assertIn('engineHealth', vehicle._destroyed_devices)
        self.assertTrue(vehicle.is_engine_dead)
        self.assertEqual('destroyed', final['events'][0]['state'])

    def test_critical_descriptor_adapter_uses_native_attributes(self):
        component = _Strict1513Component(
            itemTypeName='vehicleTurret', fireStartingChance=0.12)

        self.assertEqual(
            'vehicleTurret',
            critical_damage._descriptor_value(component, 'itemTypeName'))
        self.assertEqual(
            0.12,
            critical_damage._descriptor_value(
                component, 'fireStartingChance'))

    def test_interior_zone_reads_native_1513_geometry_attributes(self):
        descriptor = _Strict1513Component(
            chassis=_Strict1513Component(
                hullPosition=_Point(0.0, 0.0, 0.0)),
            hull=_Strict1513Component(
                turretPositions=(_Point(0.0, 0.0, 2.0),),
                hitTester=types.SimpleNamespace(bbox=(
                    _Point(-2.0, -1.0, -3.0),
                    _Point(2.0, 1.0, 3.0), None))))
        material = types.SimpleNamespace(
            vehicleDamageFactor=1.0, armor=20.0)
        component = _Strict1513Component(itemTypeName='vehicleHull')
        target = types.SimpleNamespace(
            typeDescriptor=descriptor, matrix=object())
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Point
        math_module.Matrix = _IdentityMatrix

        with mock.patch.dict(sys.modules, {'Math': math_module}):
            zone = critical_damage._offh_interior_zone(
                target, ((4.0, 1.0, material, component),),
                _Point(0.0, 0.0, 0.0), _Point(0.0, 0.0, 10.0),
                descriptor)

        self.assertEqual('hullFront', zone)

    def test_internal_layout_build_never_probes_native_mapping_api(self):
        from gui.mods.offline_lan_0922 import internal_geometry

        components = []

        def component(name, **values):
            defaults = {
                'name': name,
                'id': 1,
                'compactDescr': 1,
                'models': None,
                'materials': {},
                'hitTester': types.SimpleNamespace(bbox=(
                    (-1.5, -0.5, -2.5),
                    (1.5, 1.5, 2.5), None)),
                'weight': 100.0,
            }
            defaults.update(values)
            value = _Strict1513Component(**defaults)
            components.append(value)
            return value

        descriptor = _Strict1513Component(
            type=types.SimpleNamespace(
                name='ussr:MS-1',
                crewRoles=(
                    ('commander', 'gunner', 'radioman', 'loader'),
                    ('driver',))),
            chassis=component('chassis'),
            hull=component('hull'),
            turret=component('turret', yawLimits=(-3.14, 3.14)),
            gun=component('gun', maxAmmo=30, shots=()),
            engine=component('engine', weight=120.0),
            fuelTank=component('fuelTank', weight=40.0),
            radio=component('radio', weight=15.0))
        components.append(descriptor)

        internal_hit_layouts._LAYOUT_CACHE.clear()
        internal_geometry._PROBE_CACHE.clear()
        self.addCleanup(internal_hit_layouts._LAYOUT_CACHE.clear)
        self.addCleanup(internal_geometry._PROBE_CACHE.clear)
        layout = critical_damage._offh_internal_layout(descriptor)

        self.assertIsNotNone(layout)
        self.assertEqual(('ussr', 'ms1'), layout['profile_key'])
        self.assertGreater(len(layout['targets']), 0)
        self.assertEqual(0, sum(value.mapping_calls for value in components))

    def test_internal_layout_exact_0922_alias_inventory(self):
        aliases = internal_layout_profiles.PROFILE_ALIASES_0922
        profiles = internal_layout_profiles.PROFILES
        absent = {
            ('germany', 'pziiia'),
            ('usa', 'm48a1'),
            ('usa', 't71'),
        }

        self.assertEqual(218, len(aliases))
        self.assertEqual(218, len(set(aliases.values())))
        self.assertTrue(set(aliases).isdisjoint(profiles))
        self.assertTrue(all(target in profiles for target in aliases.values()))
        self.assertTrue(all(source[0] == target[0]
                            for source, target in aliases.items()))
        self.assertTrue(absent.isdisjoint(set(aliases.values())))
        direct = set(profiles) - set(aliases.values()) - absent
        self.assertEqual(30, len(direct))
        self.assertEqual(248, len(direct) + len(aliases))

    def test_internal_layout_uses_exact_aliases_not_suffix_guesses(self):
        expected = {
            'germany:G10_PzIII_AusfJ': ('germany', 'pziii'),
            'germany:G05_StuG_40_AusfG': ('germany', 'stugiii'),
            'usa:A58_T67': ('usa', 't49'),
            'france:F38_Bat_Chatillon155_58': (
                'france', 'batchatillon155'),
            'germany:G79_Pz_IV_AusfGH': ('germany', 'pziv'),
            'germany:G03_PzV_Panther': ('germany', 'pzv'),
            'germany:G04_PzVI_Tiger_I': ('germany', 'pzvi'),
            'usa:A69_T110E5': ('usa', 't110'),
            'ussr:R90_IS_4M': ('ussr', 'is4'),
            'ussr:R38_KV-220_beta': ('ussr', 'kv220action'),
            'ussr:R67_M3_LL': ('ussr', 'm3stuartll'),
        }
        for vehicle_name, profile_key in expected.items():
            with self.subTest(vehicle=vehicle_name):
                actual_key, profile = internal_hit_layouts._compiled_profile(
                    vehicle_name)
                self.assertEqual(profile_key, actual_key)
                self.assertIs(
                    internal_layout_profiles.PROFILES[profile_key], profile)

        for vehicle_name in (
                'germany:G102_Pz_III',
                'germany:G101_StuG_III',
                'usa:A100_T49',
                'germany:G14_PzIII_A',
                'usa:A84_M48A1',
                'usa:A91_T71',
                'ussr:R999_MS-1'):
            with self.subTest(unmapped=vehicle_name):
                unused_key, profile = internal_hit_layouts._compiled_profile(
                    vehicle_name)
                self.assertIsNone(profile)

    def test_internal_layout_0922_crew_drift_bindings(self):
        cases = (
            (
                'china:Ch02_Type62',
                (('commander',), ('gunner',), ('driver',),
                 ('loader', 'radioman')),
                (
                    ('commander', ('commander',), 0, 'crew_00'),
                    ('gunner1', ('gunner',), 1, 'crew_01'),
                    ('driver', ('driver',), 2, 'crew_02'),
                    ('loader1', ('loader', 'radioman'), 3, 'crew_03'),
                )),
            (
                'usa:A27_T82',
                (('commander', 'gunner', 'radioman'), ('driver',),
                 ('gunner',), ('loader',)),
                (
                    ('commander', ('commander', 'gunner', 'radioman'),
                     0, 'crew_00'),
                    ('driver', ('driver',), 1, 'crew_01'),
                    ('gunner1', ('gunner',), 2, 'crew_02'),
                    ('loader1', ('loader',), 3, 'crew_04'),
                )),
            (
                'ussr:R78_SU_85I',
                (('commander',), ('gunner',), ('driver',),
                 ('loader', 'radioman')),
                (
                    ('commander', ('commander',), 0, 'crew_00'),
                    ('gunner1', ('gunner',), 1, 'crew_02'),
                    ('driver', ('driver',), 2, 'crew_03'),
                    ('loader1', ('loader', 'radioman'), 3, 'crew_04'),
                )),
        )
        internal_hit_layouts._LAYOUT_CACHE.clear()
        self.addCleanup(internal_hit_layouts._LAYOUT_CACHE.clear)
        for vehicle_name, crew_roles, expected in cases:
            with self.subTest(vehicle=vehicle_name):
                layout = internal_hit_layouts.build_layout(
                    _layout_descriptor(vehicle_name, crew_roles),
                    log_build=False)
                self.assertTrue(layout['valid'], layout['errors'])
                actual = tuple(
                    (target['entity'], target['roles'],
                     target['crew_index'], target['zone_id'])
                    for target in layout['targets']
                    if target['kind'] == 'crew')
                self.assertEqual(expected, actual)

    def test_internal_layout_crew_profile_mismatch_remains_fail_closed(self):
        vehicle_name = 'china:Ch02_Type62'
        stale_roles = (
            ('commander', 'radioman'), ('gunner',), ('driver',), ('loader',))
        internal_hit_layouts._LAYOUT_CACHE.clear()
        self.addCleanup(internal_hit_layouts._LAYOUT_CACHE.clear)

        layout = internal_hit_layouts.build_layout(
            _layout_descriptor(vehicle_name, stale_roles), log_build=False)

        self.assertFalse(layout['valid'])
        self.assertTrue(any(
            error.startswith('crew_roles_profile_mismatch:')
            for error in layout['errors']))

    def test_external_track_uses_copied_082_crit_loop(self):
        vehicle = types.SimpleNamespace(
            id=1, health=500, typeDescriptor=_descriptor())
        collision = (1.0, 1.0, _Material('leftTrackHealth'), None)
        shell = {'damage': (100.0, 120.0)}

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch('random.uniform', return_value=120.0), \
                mock.patch('random.random', return_value=0.0):
            damage, payload = critical_damage.apply_direct(
                vehicle, (collision,), object(), object(), 0, shell,
                attacker_id=2, penetrated=False)

        self.assertEqual(0, damage)
        self.assertEqual(0.0, vehicle.devices_hp['leftTrackHealth'])
        self.assertIn('leftTrackHealth', vehicle._destroyed_devices)
        self.assertEqual('destroyed', payload['devices'][0]['state'])
        self.assertEqual(
            [{'kind': 'device', 'name': 'leftTrackHealth',
              'old_state': 'normal', 'state': 'destroyed',
              'cause': 'shot'}],
            payload['events'])

    def test_hidden_damage_crosses_half_max_health_not_regen_health(self):
        descriptor = _descriptor()
        descriptor.engine = {
            'maxHealth': 100, 'maxRegenHealth': 80,
            'fireStartingChance': 0.0}
        vehicle = types.SimpleNamespace(
            id=1, health=500, typeDescriptor=descriptor)
        collision = (1.0, 1.0, _Material('engineHealth'), None)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch('random.uniform', return_value=30.0), \
                mock.patch('random.random', return_value=0.0):
            unused_damage, hidden = critical_damage.apply_direct(
                vehicle, (collision,), object(), object(), 0,
                {'damage': (100.0, 30.0)}, attacker_id=2,
                penetrated=False)

        self.assertEqual(70.0, vehicle.devices_hp['engineHealth'])
        self.assertEqual('normal', hidden['devices'][0]['state'])
        self.assertEqual([], hidden['events'])
        self.assertNotIn('engineHealth', vehicle._critical_devices)
        self.assertEqual(
            1.0, device_damage.module_stat_factor(
                vehicle.devices_hp, vehicle._destroyed_devices,
                descriptor, 'mobility', vehicle._critical_devices))

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch('random.uniform', return_value=20.0), \
                mock.patch('random.random', return_value=0.0):
            unused_damage, yellow = critical_damage.apply_direct(
                vehicle, (collision,), object(), object(), 0,
                {'damage': (100.0, 20.0)}, attacker_id=2,
                penetrated=False)

        self.assertEqual(50.0, vehicle.devices_hp['engineHealth'])
        self.assertIn('engineHealth', vehicle._critical_devices)
        self.assertEqual(
            [{'kind': 'device', 'name': 'engineHealth',
              'old_state': 'normal', 'state': 'critical',
              'cause': 'shot'}], yellow['events'])
        self.assertEqual(
            0.5, device_damage.module_stat_factor(
                vehicle.devices_hp, vehicle._destroyed_devices,
                descriptor, 'mobility', vehicle._critical_devices))

    def test_duplicate_boxes_roll_and_damage_one_logical_module_once(self):
        vehicle = types.SimpleNamespace(
            id=1, health=500, typeDescriptor=_descriptor())
        collisions = (
            (1.0, 1.0, _Material('leftTrackHealth'), None),
            (1.1, 1.0, _Material('leftTrackHealth'), None))
        chance = mock.Mock(return_value=0.0)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch('random.uniform', return_value=20.0), \
                mock.patch('random.random', chance):
            unused_damage, payload = critical_damage.apply_direct(
                vehicle, collisions, object(), object(), 0,
                {'damage': (100.0, 20.0)}, attacker_id=2,
                penetrated=False)

        self.assertEqual(80.0, vehicle.devices_hp['leftTrackHealth'])
        self.assertEqual('normal', payload['devices'][0]['state'])
        self.assertEqual(1, chance.call_count)

    def test_no_profile_does_not_manufacture_an_internal_critical(self):
        vehicle = types.SimpleNamespace(
            id=1, health=500, typeDescriptor=_descriptor())
        armor = types.SimpleNamespace(
            extra=None, armor=20.0, vehicleDamageFactor=1.0)
        chance = mock.Mock(return_value=0.0)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch.object(
                    critical_damage, '_offh_internal_layout',
                    return_value=None), \
                mock.patch('random.uniform', return_value=80.0), \
                mock.patch('random.random', chance):
            damage, payload = critical_damage.apply_direct(
                vehicle, ((1.0, 1.0, armor, None),), object(), object(),
                123, {'damage': (100.0, 80.0)}, attacker_id=2,
                penetrated=True)

        self.assertEqual(123, damage)
        self.assertIsNone(payload)
        self.assertEqual({}, vehicle.devices_hp)
        chance.assert_not_called()

    def test_internal_interval_is_converted_to_world_metres(self):
        descriptor = _descriptor()
        target = types.SimpleNamespace(
            matrix=object(), getComponents=lambda: (
                (descriptor.hull, _IdentityMatrix(None)),))
        layout = {'valid': True, 'targets': (
            {'parent': 'hull', 'entity': 'engine'},
            {'parent': 'hull', 'entity': 'fuelTank'})}
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Point
        math_module.Matrix = _IdentityMatrix

        with mock.patch.dict(sys.modules, {'Math': math_module}), \
                mock.patch.object(
                    critical_damage, '_offh_internal_layout',
                    return_value=layout), \
                mock.patch(
                    'gui.mods.offline_lan_0922.internal_geometry.'
                    'target_interval',
                    side_effect=((0.2475, 0.3), (0.2525, 0.3))):
            hits = critical_damage._offh_internal_ray_hits(
                target, descriptor, _Point(0, 0, 0), _Point(0, 0, 4))

        self.assertAlmostEqual(0.99, hits[0][0])
        self.assertEqual('engineHealth', hits[0][1])
        self.assertAlmostEqual(1.01, hits[1][0])
        self.assertEqual('fuelTankHealth', hits[1][1])

    def test_invalid_layout_fails_closed_for_direct_and_he_geometry(self):
        descriptor = _descriptor()
        target = types.SimpleNamespace(
            matrix=object(), getComponents=lambda: ())
        invalid = {'valid': False, 'targets': ({
            'parent': 'hull', 'entity': 'engine'},)}

        with mock.patch.object(
                critical_damage, '_offh_internal_layout',
                return_value=invalid), mock.patch(
                    'gui.mods.offline_lan_0922.internal_geometry.'
                    'target_interval') as interval:
            direct = critical_damage._offh_internal_ray_hits(
                target, descriptor, _Point(0, 0, 0), _Point(0, 0, 1))
            explosion = critical_damage._offh_internal_cone_hits(
                target, descriptor, _Point(0, 0, 0), _Point(0, 0, 1),
                {'caliber': 100.0})

        self.assertIsNone(direct)
        self.assertIsNone(explosion)
        interval.assert_not_called()

    def test_he_internal_cone_uses_45_degrees_and_caliber_depth(self):
        descriptor = _descriptor()
        target = types.SimpleNamespace(
            matrix=object(), getComponents=lambda: (
                (descriptor.hull, _IdentityMatrix(None)),))

        def sphere(entity, center):
            return {
                'parent': 'hull', 'entity': entity,
                'primitives': ({
                    'shape': 'sphere', 'center': center, 'radius': 0.0,
                    'primitive_id': entity + ':point'},),
            }

        layout = {'valid': True, 'targets': (
            sphere('engine', (0.49, 0.0, 0.5)),   # 44.4 degrees
            sphere('fuelTank', (0.51, 0.0, 0.5)), # 45.6 degrees
            sphere('radio', (0.0, 0.0, 1.0)),     # exact depth
            sphere('ammoBay', (0.0, 0.0, 1.01)),  # beyond depth
        )}
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Point
        math_module.Matrix = _IdentityMatrix

        with mock.patch.dict(sys.modules, {'Math': math_module}), \
                mock.patch.object(
                    critical_damage, '_offh_internal_layout',
                    return_value=layout):
            hits = critical_damage._offh_internal_cone_hits(
                target, descriptor, _Point(0, 0, 0), _Point(0, 0, 1),
                {'caliber': 100.0})

        self.assertEqual(
            ['engineHealth', 'radioHealth'], [item[1] for item in hits])
        self.assertAlmostEqual(0.5, hits[0][0])
        self.assertAlmostEqual(1.0, hits[1][0])

    def test_he_cone_uses_current_component_local_transform(self):
        descriptor = _descriptor()
        target = types.SimpleNamespace(
            matrix=object(), getComponents=lambda: (
                (descriptor.hull, _TranslateXMatrix(-10.0)),))
        layout = {'valid': True, 'targets': ({
            'parent': 'hull', 'entity': 'engine',
            'primitives': ({
                'shape': 'sphere', 'center': (0.0, 0.0, 0.5),
                'radius': 0.0, 'primitive_id': 'engine:point'},),
        },)}
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Point
        math_module.Matrix = _IdentityMatrix

        with mock.patch.dict(sys.modules, {'Math': math_module}), \
                mock.patch.object(
                    critical_damage, '_offh_internal_layout',
                    return_value=layout):
            hits = critical_damage._offh_internal_cone_hits(
                target, descriptor, _Point(10, 0, 0), _Point(0, 0, 1),
                {'caliber': 100.0})

        self.assertEqual([(0.5, 'engineHealth')], hits)

    def test_he_cone_hits_a_volume_that_straddles_the_angle_boundary(self):
        descriptor = _descriptor()
        target = types.SimpleNamespace(
            matrix=object(), getComponents=lambda: (
                (descriptor.hull, _IdentityMatrix(None)),))

        def box(entity, minimum, maximum):
            center = tuple((minimum[index] + maximum[index]) * 0.5
                           for index in range(3))
            half = tuple((maximum[index] - minimum[index]) * 0.5
                         for index in range(3))
            return {
                'parent': 'hull', 'entity': entity,
                'minimum': minimum, 'maximum': maximum,
                'center': center, 'half_extents': half,
                'primitives': ({
                    'shape': 'aabb', 'minimum': minimum,
                    'maximum': maximum, 'center': center,
                    'half_extents': half,
                    'primitive_id': entity + ':box'},),
            }

        layout = {'valid': True, 'targets': (
            # Nearest point to the apex is (0.45, 0, 0.40), outside 45
            # degrees, but the upper-left edge reaches (0.45, 0, 0.60).
            box('engine', (0.45, -0.05, 0.40), (0.65, 0.05, 0.60)),
            # This box remains wholly outside: min radial 0.61 > max z 0.60.
            box('radio', (0.61, -0.05, 0.40), (0.75, 0.05, 0.60)),
        )}
        math_module = types.ModuleType('Math')
        math_module.Vector3 = _Point
        math_module.Matrix = _IdentityMatrix

        with mock.patch.dict(sys.modules, {'Math': math_module}), \
                mock.patch.object(
                    critical_damage, '_offh_internal_layout',
                    return_value=layout):
            hits = critical_damage._offh_internal_cone_hits(
                target, descriptor, _Point(0, 0, 0), _Point(0, 0, 1),
                {'caliber': 100.0})

        self.assertEqual(['engineHealth'], [item[1] for item in hits])

    def test_he_cone_convex_intersection_supports_profile_primitive_shapes(self):
        from gui.mods.offline_lan_0922 import internal_hit_layouts

        cases = (
            ('sphere', {
                'shape': 'sphere', 'center': (0.55, 0.0, 0.5),
                'radius': 0.10}, True),
            ('sphere-out', {
                'shape': 'sphere', 'center': (0.80, 0.0, 0.5),
                'radius': 0.10}, False),
            ('ellipsoid', {
                'shape': 'ellipsoid', 'center': (0.57, 0.0, 0.5),
                'radii': (0.10, 0.03, 0.10)}, True),
            ('ellipsoid-out', {
                'shape': 'ellipsoid', 'center': (0.80, 0.0, 0.5),
                'radii': (0.10, 0.03, 0.10)}, False),
            ('capsule', {
                'shape': 'capsule', 'center': (0.57, 0.0, 0.5),
                'radius': 0.03, 'half_length': 0.10, 'axis': 'x'}, True),
            ('capsule-out', {
                'shape': 'capsule', 'center': (0.80, 0.0, 0.5),
                'radius': 0.03, 'half_length': 0.10, 'axis': 'x'}, False),
            ('oriented-box', {
                'shape': 'box', 'center': (0.58, 0.0, 0.5),
                'half_extents': (0.12, 0.03, 0.08),
                'rotation_yaw_degrees': 25.0}, True),
            ('oriented-box-out', {
                'shape': 'box', 'center': (0.85, 0.0, 0.5),
                'half_extents': (0.10, 0.03, 0.08),
                'rotation_yaw_degrees': 25.0}, False),
        )
        for name, primitive, expected in cases:
            target = {
                'center': primitive['center'],
                'half_extents': primitive.get(
                    'half_extents', (0.1, 0.1, 0.1)),
                'minimum': (-1.0, -1.0, -1.0),
                'maximum': (1.0, 1.0, 1.0),
            }
            with self.subTest(shape=name):
                self.assertIs(
                    expected,
                    internal_hit_layouts._primitive_intersects_cone(
                        target, primitive, (0.0, 0.0, 0.0),
                        (0.0, 0.0, 1.0), 1.0, 1.0))

    def test_he_proposal_uses_cone_instead_of_solid_internal_ray(self):
        vehicle = types.SimpleNamespace(
            id=1, health=500, typeDescriptor=_descriptor(),
            position=object(), matrix=object(), getComponents=lambda: ())

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch.object(
                    critical_damage, '_offh_internal_cone_hits',
                    return_value=[(0.5, 'gunHealth')]) as cone_hits, \
                mock.patch.object(
                    critical_damage, '_offh_internal_ray_hits',
                    side_effect=AssertionError('solid ray must not run')), \
                mock.patch('random.uniform', return_value=60.0), \
                mock.patch('random.random', return_value=0.0):
            damage, payload = critical_damage.propose_explosion(
                vehicle, (), _Point(0, 0, 0), _Point(0, 0, 1), 0,
                {'kind': 'HIGH_EXPLOSIVE', 'caliber': 100.0,
                 'damage': (100.0, 60.0)}, attacker_id=2)

        self.assertEqual(0, damage)
        self.assertFalse(hasattr(vehicle, 'devices_hp'))
        self.assertEqual(40.0, payload['devices'][0]['hp'])
        self.assertEqual('critical', payload['devices'][0]['state'])
        cone_hits.assert_called_once()

    def test_he_native_extra_and_cone_boxes_score_each_module_once(self):
        vehicle = types.SimpleNamespace(
            id=1, health=500, typeDescriptor=_descriptor())
        collision = (1.0, 1.0, _Material('gunHealth'), None)
        chance = mock.Mock(return_value=0.0)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch.object(
                    critical_damage, '_offh_internal_cone_hits',
                    return_value=[
                        (0.5, 'gunHealth'), (0.6, 'radioHealth')]) as cone, \
                mock.patch('random.uniform', return_value=20.0), \
                mock.patch('random.random', chance):
            unused_damage, payload = critical_damage.apply_explosion(
                vehicle, (collision,), object(), object(), 0,
                {'kind': 'HIGH_EXPLOSIVE', 'caliber': 100.0,
                 'damage': (100.0, 20.0)}, attacker_id=2)

        self.assertEqual(80.0, vehicle.devices_hp['gunHealth'])
        self.assertEqual(80.0, vehicle.devices_hp['radioHealth'])
        self.assertEqual(2, chance.call_count)
        self.assertEqual(
            set(['gunHealth']), cone.call_args.args[-1])
        self.assertEqual(
            set(['gunHealth', 'radioHealth']),
            set(record['name'] for record in payload['devices']))

    def test_deadeye_adds_three_points_only_for_ap_apcr_and_heat(self):
        collision = (1.0, 1.0, _Material('gunHealth', chance=0.45), None)

        def strike(kind, deadeye):
            vehicle = types.SimpleNamespace(
                id=1, health=500, typeDescriptor=_descriptor())
            with mock.patch.dict(
                    sys.modules,
                    {'BigWorld': self.bigworld, 'Math': self.math}), \
                    mock.patch('random.uniform', return_value=20.0), \
                    mock.patch('random.random', return_value=0.47):
                unused_damage, payload = critical_damage.apply_direct(
                    vehicle, (collision,), object(), object(), 0,
                    {'kind': kind, 'damage': (100.0, 20.0)},
                    attacker_id=2, penetrated=False, deadeye=deadeye)
            return vehicle, payload

        without_deadeye, ordinary = strike('ARMOR_PIERCING', False)
        self.assertEqual({}, without_deadeye.devices_hp)
        self.assertIsNone(ordinary)
        for kind in ('ARMOR_PIERCING', 'ARMOR_PIERCING_CR',
                     'HOLLOW_CHARGE'):
            with self.subTest(kind=kind):
                vehicle, payload = strike(kind, True)
                self.assertEqual(80.0, vehicle.devices_hp['gunHealth'])
                self.assertIsNotNone(payload)
        he_vehicle, he_payload = strike('HIGH_EXPLOSIVE', True)
        self.assertEqual({}, he_vehicle.devices_hp)
        self.assertIsNone(he_payload)

    def test_missing_explosion_material_uses_the_crew_blast_fallback(self):
        material = types.SimpleNamespace(chanceToHitByProjectile=0.33)
        invalid = types.SimpleNamespace(
            chanceToHitByExplosion=object())

        self.assertEqual(
            0.15, device_damage.saving_throw(
                material, 'commanderHealth', by_explosion=True))
        self.assertEqual(
            0.15, device_damage.saving_throw(
                invalid, 'commanderHealth', by_explosion=True))

    def test_legacy_generator_refuses_to_overwrite_the_active_module(self):
        import importlib.util

        tool_path = ROOT / '0.9.22' / 'tools' / 'generate_critical_damage.py'
        spec = importlib.util.spec_from_file_location(
            'generate_critical_damage_audit', str(tool_path))
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        active = (ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client' /
                  'gui' / 'mods' / 'offline_lan_0922' /
                  'critical_damage.py')

        with self.assertRaisesRegex(
                ValueError, 'must not overwrite the active'):
            generator.generate(ROOT, active)

    def test_synthetic_distance_obeys_native_exit_plate_filter(self):
        armor = types.SimpleNamespace(
            extra=None, armor=20.0, vehicleDamageFactor=1.0)
        collisions = (
            (0.0, 1.0, armor, None),
            (1.0, 1.0, armor, None))

        def strike(distance):
            vehicle = types.SimpleNamespace(
                id=1, health=500, typeDescriptor=_descriptor())
            with mock.patch.dict(
                    sys.modules,
                    {'BigWorld': self.bigworld, 'Math': self.math}), \
                    mock.patch.object(
                        critical_damage, '_offh_internal_ray_hits',
                        return_value=[(distance, 'engineHealth')]), \
                    mock.patch('random.uniform', return_value=60.0), \
                    mock.patch('random.random', return_value=0.0):
                unused_damage, payload = critical_damage.apply_direct(
                    vehicle, collisions, object(), object(), 0,
                    {'damage': (100.0, 60.0)}, attacker_id=2,
                    penetrated=True)
            return vehicle, payload

        inside, inside_payload = strike(0.99)
        outside, outside_payload = strike(1.01)

        self.assertEqual(40.0, inside.devices_hp['engineHealth'])
        self.assertEqual('critical', inside_payload['devices'][0]['state'])
        self.assertEqual({}, outside.devices_hp)
        self.assertIsNone(outside_payload)

    def test_engine_fire_roll_starts_at_minimum_device_damage(self):
        descriptor = _descriptor()
        descriptor.engine['fireStartingChance'] = 1.0
        vehicle = types.SimpleNamespace(
            id=1, health=500, typeDescriptor=descriptor)
        collision = (1.0, 1.0, _Material('engineHealth'), None)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch('random.uniform', return_value=21.0), \
                mock.patch('random.random', side_effect=(0.0, 0.0)):
            unused_damage, payload = critical_damage.apply_direct(
                vehicle, (collision,), object(), object(), 0,
                {'damage': (100.0, 21.0)}, attacker_id=2,
                penetrated=False)

        self.assertEqual(79.0, vehicle.devices_hp['engineHealth'])
        self.assertEqual('normal', payload['devices'][0]['state'])
        self.assertTrue(vehicle.is_on_fire)
        self.assertIn(
            {'kind': 'fire', 'state': True, 'cause': 'shot'},
            payload['events'])

    def test_engine_fire_roll_skips_below_minimum_device_damage(self):
        descriptor = _descriptor()
        descriptor.engine['fireStartingChance'] = 1.0
        vehicle = types.SimpleNamespace(
            id=1, health=500, typeDescriptor=descriptor)
        collision = (1.0, 1.0, _Material('engineHealth'), None)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch('random.uniform', return_value=20.0), \
                mock.patch('random.random', return_value=0.0):
            unused_damage, payload = critical_damage.apply_direct(
                vehicle, (collision,), object(), object(), 0,
                {'damage': (100.0, 20.0)}, attacker_id=2,
                penetrated=False)

        self.assertEqual(80.0, vehicle.devices_hp['engineHealth'])
        self.assertEqual('normal', payload['devices'][0]['state'])
        self.assertFalse(vehicle.is_on_fire)
        self.assertFalse(any(
            event['kind'] == 'fire' for event in payload['events']))

    def test_engine_uses_its_descriptor_fire_damage_threshold(self):
        descriptor = _descriptor()
        descriptor.engine['fireStartingChance'] = 1.0
        descriptor.engine['minFireStartingDamage'] = 30.0
        collision = (1.0, 1.0, _Material('engineHealth'), None)

        below = types.SimpleNamespace(
            id=1, health=500, typeDescriptor=descriptor)
        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch('random.uniform', return_value=29.0), \
                mock.patch('random.random', return_value=0.0):
            critical_damage.apply_direct(
                below, (collision,), object(), object(), 0,
                {'damage': (100.0, 29.0)}, attacker_id=2,
                penetrated=False)
        self.assertFalse(below.is_on_fire)

        threshold = types.SimpleNamespace(
            id=2, health=500, typeDescriptor=descriptor)
        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch('random.uniform', return_value=30.0), \
                mock.patch('random.random', side_effect=(0.0, 0.0)):
            critical_damage.apply_direct(
                threshold, (collision,), object(), object(), 0,
                {'damage': (100.0, 30.0)}, attacker_id=2,
                penetrated=False)
        self.assertTrue(threshold.is_on_fire)

    def test_equipment_factor_reduces_engine_fire_roll_in_proposals(self):
        descriptor = _descriptor()
        descriptor.engine['fireStartingChance'] = 1.0
        vehicle = types.SimpleNamespace(
            id=1, health=500, typeDescriptor=descriptor,
            position=object(), matrix=object(), getComponents=lambda: (),
            _fire_starting_chance_factor=0.9)
        collision = (1.0, 1.0, _Material('engineHealth'), None)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch('random.uniform', return_value=25.0), \
                mock.patch('random.random', side_effect=(0.0, 0.95)):
            unused_damage, payload = critical_damage.propose_direct(
                vehicle, (collision,), object(), object(), 0,
                {'damage': (100.0, 25.0)}, attacker_id=2,
                penetrated=False)

        self.assertFalse(payload['fire'])
        self.assertFalse(hasattr(vehicle, 'is_on_fire'))

    def test_large_medkit_reduces_crew_knockout_chance_in_proposals(self):
        vehicle = types.SimpleNamespace(
            id=1, health=500, typeDescriptor=_descriptor(),
            position=object(), matrix=object(), getComponents=lambda: (),
            _medkit_bonus_value=0.30)
        collision = (
            1.0, 1.0, _Material('commanderHealth', chance=0.33), None)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch('random.uniform', return_value=5.0), \
                mock.patch('random.random', return_value=0.30):
            unused_damage, payload = critical_damage.propose_direct(
                vehicle, (collision,), object(), object(), 0,
                {'damage': (100.0, 5.0)}, attacker_id=2,
                penetrated=False)

        self.assertIsNone(payload)
        self.assertFalse(hasattr(vehicle, '_crew_ko'))

    def test_fuel_tank_ignites_only_when_module_hp_reaches_zero(self):
        vehicle = types.SimpleNamespace(
            id=1, health=500, typeDescriptor=_descriptor(),
            devices_hp={'fuelTankHealth': 30.0})
        collision = (1.0, 1.0, _Material('fuelTankHealth'), None)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch('random.uniform', return_value=20.0), \
                mock.patch('random.random', return_value=0.0):
            unused_damage, holed = critical_damage.apply_direct(
                vehicle, (collision,), object(), object(), 0,
                {'damage': (100.0, 20.0)}, attacker_id=2,
                penetrated=False)
        self.assertEqual(10.0, vehicle.devices_hp['fuelTankHealth'])
        self.assertFalse(vehicle.is_on_fire)
        self.assertFalse(any(
            event['kind'] == 'fire' for event in holed['events']))

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch('random.uniform', return_value=20.0), \
                mock.patch('random.random', return_value=0.0):
            unused_damage, destroyed = critical_damage.apply_direct(
                vehicle, (collision,), object(), object(), 0,
                {'damage': (100.0, 20.0)}, attacker_id=2,
                penetrated=False)
        self.assertEqual(0.0, vehicle.devices_hp['fuelTankHealth'])
        self.assertTrue(vehicle.is_on_fire)
        self.assertIn(
            {'kind': 'fire', 'state': True, 'cause': 'shot'},
            destroyed['events'])

    def test_critical_proposal_does_not_mutate_live_vehicle(self):
        self.player.playerVehicleID = 999
        self.player.arena.onVehicleKilled = mock.Mock()
        vehicle = types.SimpleNamespace(
            id=999, health=500, typeDescriptor=_descriptor(),
            position=object(), matrix=object(),
            devices_hp={'ammoBayHealth': 100.0},
            _destroyed_devices=set(), _crew_ko=set(),
            is_on_fire=False, getComponents=lambda: ())
        collision = (1.0, 1.0, _Material('ammoBayHealth'), None)
        shell = {'damage': (100.0, 120.0)}

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch('random.uniform', return_value=120.0), \
                mock.patch('random.random', return_value=0.0):
            damage, payload = critical_damage.propose_direct(
                vehicle, (collision,), object(), object(), 0, shell,
                attacker_id=2, penetrated=False)

        self.assertEqual(510, damage)
        self.assertEqual({'ammoBayHealth': 100.0}, vehicle.devices_hp)
        self.assertEqual(set(), vehicle._destroyed_devices)
        self.assertFalse(hasattr(vehicle, '_ammo_rack_death'))
        self.player.arena.onVehicleKilled.assert_not_called()
        self.assertTrue(payload['ammo_rack_death'])
        self.assertEqual('ammo_rack', payload['events'][-1]['kind'])

    def test_proposal_records_module_operation_before_stale_hp_clamp(self):
        vehicle = types.SimpleNamespace(
            id=1, health=500, typeDescriptor=_descriptor(),
            position=object(), matrix=object(),
            devices_hp={'engineHealth': 0.0},
            _destroyed_devices=set(['engineHealth']),
            _critical_devices=set(), _crew_ko=set(),
            is_on_fire=False, getComponents=lambda: ())
        collision = (1.0, 1.0, _Material('engineHealth'), None)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch('random.uniform', return_value=25.0), \
                mock.patch('random.random', return_value=0.0):
            unused_damage, payload, delta = critical_damage.propose_direct(
                vehicle, (collision,), object(), object(), 0,
                {'damage': (100.0, 25.0)}, attacker_id=2,
                penetrated=False, with_delta=True)

        self.assertEqual({
            'devices': [{'name': 'engineHealth', 'hp_loss': 25.0}],
            'crew_ko': [], 'ignite': False,
        }, delta)
        self.assertIsInstance(payload, dict)
        self.assertEqual([], payload['events'])
        self.assertEqual(0.0, payload['devices'][0]['hp'])
        self.assertEqual({'engineHealth': 0.0}, vehicle.devices_hp)
        self.assertEqual(set(['engineHealth']), vehicle._destroyed_devices)

    def test_proposal_records_crew_operation_before_stale_ko_guard(self):
        vehicle = types.SimpleNamespace(
            id=1, health=500, typeDescriptor=_descriptor(),
            position=object(), matrix=object(), devices_hp={},
            _destroyed_devices=set(), _critical_devices=set(),
            _crew_ko=set(['driver']), is_on_fire=False,
            getComponents=lambda: ())
        collision = (1.0, 1.0, _Material('driverHealth'), None)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch('random.uniform', return_value=25.0), \
                mock.patch('random.random', return_value=0.0):
            unused_damage, payload, delta = critical_damage.propose_direct(
                vehicle, (collision,), object(), object(), 0,
                {'damage': (100.0, 25.0)}, attacker_id=2,
                penetrated=False, with_delta=True)

        self.assertEqual({
            'devices': [], 'crew_ko': ['driver'], 'ignite': False,
        }, delta)
        self.assertIsInstance(payload, dict)
        self.assertEqual([], payload['events'])
        self.assertEqual(['driver'], payload['crew_ko'])
        self.assertEqual(set(['driver']), vehicle._crew_ko)

    def test_payload_is_installed_without_reroll(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500)
        payload = {
            'devices': [{'name': 'engineHealth', 'hp': 0.0,
                         'max_hp': 100.0, 'state': 'destroyed'}],
            'destroyed': ['engineHealth'],
            'crew_ko': ['driver'],
            'fire': True,
            'ammo_rack_death': False,
            'events': [{'kind': 'device', 'name': 'engineHealth',
                        'state': 'destroyed'}],
        }

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}):
            events = critical_damage.apply_payload(vehicle, payload)

        self.assertEqual(0.0, vehicle.devices_hp['engineHealth'])
        self.assertTrue(vehicle.is_engine_dead)
        self.assertEqual(set(['driver']), vehicle._crew_ko)
        self.assertTrue(vehicle.is_on_fire)
        self.assertIsNotNone(vehicle._fire_started)
        self.assertEqual(tuple(payload['events']), events)

    def test_payload_syncs_stock_crashed_track_visuals_on_state_edges(self):
        appearance = types.SimpleNamespace(
            addCrashedTrack=mock.Mock(), delCrashedTrack=mock.Mock())
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500,
            devices_hp={
                'leftTrackHealth': 100.0,
                'rightTrackHealth': 100.0,
            },
            _destroyed_devices=set(), _critical_devices=set(),
            appearance=appearance)
        broken = {
            'devices': [
                {'name': 'leftTrackHealth', 'hp': 0.0,
                 'max_hp': 100.0, 'state': 'destroyed'},
                {'name': 'rightTrackHealth', 'hp': 100.0,
                 'max_hp': 100.0, 'state': 'normal'},
            ],
            'destroyed': ['leftTrackHealth'], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False, 'events': []}
        repaired = dict(broken, devices=[
            {'name': 'leftTrackHealth', 'hp': 50.0,
             'max_hp': 100.0, 'state': 'critical'},
            {'name': 'rightTrackHealth', 'hp': 100.0,
             'max_hp': 100.0, 'state': 'normal'},
        ], destroyed=[])

        critical_damage.apply_payload(vehicle, broken)
        critical_damage.apply_payload(vehicle, broken)
        critical_damage.apply_payload(vehicle, repaired)

        appearance.addCrashedTrack.assert_called_once_with(True)
        appearance.delCrashedTrack.assert_called_once_with(True)
        self.assertEqual([], [
            call for call in appearance.addCrashedTrack.call_args_list
            if call == mock.call(False)])

    def test_eventless_snapshot_derives_missed_damage_transitions(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500)
        payload = {
            'devices': [{'name': 'engineHealth', 'hp': 0.0,
                         'max_hp': 100.0, 'state': 'destroyed'}],
            'destroyed': ['engineHealth'], 'crew_ko': ['driver'],
            'fire': True, 'ammo_rack_death': True, 'events': []}

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}):
            events = critical_damage.apply_payload(vehicle, payload)

        self.assertEqual(
            set([('device', 'destroyed'), ('crew', 'destroyed'),
                 ('fire', True), ('ammo_rack', 'destroyed')]),
            set((event['kind'], event['state']) for event in events))
        self.assertTrue(all(event['cause'] == 'shot' for event in events))

    def test_fire_uses_one_second_five_percent_tick_and_burns_out(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500, maxHealth=500,
            devices_hp={'fuelTankHealth': 0.0},
            _destroyed_devices=set(['fuelTankHealth']), _crew_ko=set(),
            is_on_fire=True, _fire_started=0.0, _fire_timer=0.0)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}):
            damage, payload = critical_damage.tick_fire(
                vehicle, 1.0, now=1.0)
        self.assertEqual(25, damage)
        self.assertIsNone(payload)

        vehicle._fire_timer = 0.9
        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}):
            damage, payload = critical_damage.tick_fire(
                vehicle, 0.1, now=10.0)
        self.assertEqual(25, damage)
        self.assertFalse(vehicle.is_on_fire)
        self.assertEqual(50.0, vehicle.devices_hp['fuelTankHealth'])
        self.assertNotIn('fuelTankHealth', vehicle._destroyed_devices)
        self.assertEqual('repair', payload['events'][0]['cause'])
        self.assertEqual('critical', payload['events'][0]['state'])
        self.assertEqual('fire', payload['events'][1]['kind'])

    def test_fire_consumes_every_whole_second_in_one_slow_frame(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500, maxHealth=500,
            devices_hp={'fuelTankHealth': 0.0},
            _destroyed_devices=set(['fuelTankHealth']), _crew_ko=set(),
            is_on_fire=True, _fire_started=0.0, _fire_timer=0.4)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}):
            damage, payload = critical_damage.tick_fire(
                vehicle, 3.2, now=3.2)

        self.assertEqual(75, damage)
        self.assertAlmostEqual(0.6, vehicle._fire_timer)
        self.assertTrue(vehicle.is_on_fire)
        self.assertIsNone(payload)

    def test_fire_slow_frame_stops_exactly_at_burnout_boundary(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500, maxHealth=500,
            devices_hp={'fuelTankHealth': 0.0},
            _destroyed_devices=set(['fuelTankHealth']), _crew_ko=set(),
            is_on_fire=True, _fire_started=0.0, _fire_timer=0.4)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}):
            damage, payload = critical_damage.tick_fire(
                vehicle, 5.0, now=12.0)

        self.assertEqual(75, damage)
        self.assertAlmostEqual(0.4, vehicle._fire_timer)
        self.assertFalse(vehicle.is_on_fire)
        self.assertEqual(50.0, vehicle.devices_hp['fuelTankHealth'])
        self.assertIsNotNone(payload)

    def test_drowning_knocks_out_all_modules_and_real_crew_roster(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500,
            devices_hp={}, _destroyed_devices=set(), _crew_ko=set(),
            is_on_fire=False)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}):
            payload = critical_damage.apply_drowning(vehicle)

        self.assertEqual(
            set(critical_damage._OFFH_DEATH_DEVICES),
            vehicle._destroyed_devices)
        self.assertEqual(
            set(['commander', 'driver', 'loader1']), vehicle._crew_ko)
        self.assertTrue(all(
            event.get('cause') == 'drowning'
            for event in payload['events']))

    def test_ordinary_death_extinguishes_fire_and_knocks_out_everything(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=0,
            devices_hp={'fuelTankHealth': 0.0},
            _destroyed_devices=set(['fuelTankHealth']), _crew_ko=set(),
            is_on_fire=True, _fire_started=1.0)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}):
            payload = critical_damage.apply_death(vehicle, 'fire')

        self.assertFalse(vehicle.is_on_fire)
        self.assertEqual(
            set(critical_damage._OFFH_DEATH_DEVICES),
            vehicle._destroyed_devices)
        self.assertEqual(
            set(['commander', 'driver', 'loader1']), vehicle._crew_ko)
        self.assertIn(
            {'kind': 'fire', 'state': False, 'cause': 'fire'},
            payload['events'])

    def test_destroyed_track_repairs_to_descriptor_regen_cap(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500,
            devices_hp={'leftTrackHealth': 0.0},
            _destroyed_devices=set(['leftTrackHealth']),
            _crew_ko=set(), is_on_fire=False)

        payload = critical_damage.tick_repair(
            vehicle, 10.0, repair_skill=0.0)

        self.assertEqual(50.0, vehicle.devices_hp['leftTrackHealth'])
        self.assertNotIn('leftTrackHealth', vehicle._destroyed_devices)
        self.assertEqual('critical', payload['devices'][0]['state'])
        self.assertEqual('destroyed', payload['events'][0]['old_state'])
        self.assertEqual('critical', payload['events'][0]['state'])

    def test_functional_yellow_module_does_not_auto_repair(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500,
            devices_hp={'engineHealth': 40.0},
            _destroyed_devices=set(),
            _critical_devices=set(['engineHealth']),
            _crew_ko=set(), is_on_fire=False)

        payload = critical_damage.tick_repair(
            vehicle, 100.0, repair_skill=100.0)

        self.assertEqual(40.0, vehicle.devices_hp['engineHealth'])
        self.assertIn('engineHealth', vehicle._critical_devices)
        self.assertIsNone(payload)

    def test_auto_repair_stays_explicitly_critical_above_half_health(self):
        descriptor = _descriptor()
        descriptor.chassis = {'maxHealth': 100, 'maxRegenHealth': 80}
        vehicle = types.SimpleNamespace(
            id=1, typeDescriptor=descriptor, health=500,
            devices_hp={'leftTrackHealth': 0.0},
            _destroyed_devices=set(['leftTrackHealth']),
            _critical_devices=set(), _crew_ko=set(), is_on_fire=False,
            position=object(), matrix=object(), getComponents=lambda: ())

        payload = critical_damage.tick_repair(
            vehicle, 10.0, repair_skill=0.0)
        shadow = critical_damage._CriticalProposalVehicle(vehicle)

        self.assertEqual(80.0, vehicle.devices_hp['leftTrackHealth'])
        self.assertNotIn('leftTrackHealth', vehicle._destroyed_devices)
        self.assertIn('leftTrackHealth', vehicle._critical_devices)
        self.assertEqual('critical', payload['devices'][0]['state'])
        self.assertIn('leftTrackHealth', shadow._critical_devices)

    def test_destroyed_170_hp_engine_repairs_to_130_but_stays_critical(self):
        descriptor = _descriptor()
        descriptor.engine = {
            'maxHealth': 170, 'maxRegenHealth': 130,
            'fireStartingChance': 0.12}
        vehicle = types.SimpleNamespace(
            id=1, typeDescriptor=descriptor, health=500,
            devices_hp={'engineHealth': 0.0},
            _destroyed_devices=set(['engineHealth']),
            _critical_devices=set(), _crew_ko=set(), is_on_fire=False)

        payload = critical_damage.tick_repair(
            vehicle, 100.0, repair_skill=0.0)

        self.assertEqual(130.0, vehicle.devices_hp['engineHealth'])
        self.assertNotIn('engineHealth', vehicle._destroyed_devices)
        self.assertIn('engineHealth', vehicle._critical_devices)
        self.assertEqual('critical', payload['devices'][0]['state'])

    def test_repair_kit_restores_full_health_and_normal_state(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500,
            devices_hp={'engineHealth': 80.0},
            _destroyed_devices=set(),
            _critical_devices=set(['engineHealth']),
            _crew_ko=set(), is_on_fire=False)

        payload = critical_damage.repair_device(vehicle, 'engine')

        self.assertEqual(100.0, vehicle.devices_hp['engineHealth'])
        self.assertNotIn('engineHealth', vehicle._critical_devices)
        self.assertEqual(
            [{'kind': 'device', 'name': 'engineHealth',
              'old_state': 'critical', 'state': 'normal',
              'cause': 'repair'}], payload['events'])

    def test_network_payload_preserves_explicit_critical_above_half(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500)
        payload = {
            'devices': [{'name': 'engineHealth', 'hp': 80.0,
                         'max_hp': 100.0, 'state': 'critical'}],
            'destroyed': [], 'crew_ko': [], 'fire': False,
            'ammo_rack_death': False, 'events': []}

        events = critical_damage.apply_payload(vehicle, payload)

        self.assertIn('engineHealth', vehicle._critical_devices)
        self.assertEqual('critical', events[0]['state'])
        with mock.patch.dict(sys.modules, {'BigWorld': self.bigworld}):
            self.assertEqual(
                0.5, critical_damage.stat_factor(vehicle, 'mobility'))

    def test_repaired_track_can_be_destroyed_again(self):
        vehicle = types.SimpleNamespace(
            id=1, typeDescriptor=_descriptor(), health=500,
            devices_hp={'leftTrackHealth': 0.0},
            _destroyed_devices=set(['leftTrackHealth']),
            _crew_ko=set(), is_on_fire=False)
        collision = (1.0, 1.0, _Material('leftTrackHealth'), None)
        shell = {'damage': (100.0, 120.0)}

        repaired = critical_damage.tick_repair(
            vehicle, 10.0, repair_skill=0.0)
        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}), \
                mock.patch('random.uniform', return_value=120.0), \
                mock.patch('random.random', return_value=0.0):
            damage, destroyed = critical_damage.apply_direct(
                vehicle, (collision,), object(), object(), 0, shell,
                attacker_id=2, penetrated=False)

        self.assertEqual('critical', repaired['devices'][0]['state'])
        self.assertEqual(0, damage)
        self.assertEqual(0.0, vehicle.devices_hp['leftTrackHealth'])
        self.assertIn('leftTrackHealth', vehicle._destroyed_devices)
        self.assertEqual(
            [{'kind': 'device', 'name': 'leftTrackHealth',
              'old_state': 'critical', 'state': 'destroyed',
              'cause': 'shot'}],
            destroyed['events'])

    def test_extinguisher_uses_copied_fire_stop_transition(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500,
            devices_hp={}, _destroyed_devices=set(), _crew_ko=set(),
            is_on_fire=True, _fire_started=1.0, _fire_timer=0.5)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}):
            payload = critical_damage.use_extinguisher(vehicle)

        self.assertFalse(vehicle.is_on_fire)
        self.assertIsNone(vehicle._fire_started)
        self.assertEqual(
            [{'kind': 'fire', 'state': False, 'cause': 'repair'}],
            payload['events'])

    def test_small_repair_kit_restores_only_selected_device(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500,
            devices_hp={'engineHealth': 0.0, 'gunHealth': 0.0},
            _destroyed_devices=set(['engineHealth', 'gunHealth']),
            _crew_ko=set(), is_on_fire=False)

        payload = critical_damage.repair_device(vehicle, 'engine')

        self.assertEqual(100.0, vehicle.devices_hp['engineHealth'])
        self.assertEqual(0.0, vehicle.devices_hp['gunHealth'])
        self.assertNotIn('engineHealth', vehicle._destroyed_devices)
        self.assertIn('gunHealth', vehicle._destroyed_devices)
        self.assertEqual(
            [('engineHealth', 'normal', 'repair')],
            [(event['name'], event['state'], event['cause'])
             for event in payload['events']])

    def test_small_med_kit_restores_only_selected_crew_member(self):
        vehicle = types.SimpleNamespace(
            typeDescriptor=_descriptor(), health=500,
            devices_hp={}, _destroyed_devices=set(),
            _crew_ko=set(['driver', 'loader1']), is_on_fire=False)

        with mock.patch.dict(
                sys.modules, {'BigWorld': self.bigworld, 'Math': self.math}):
            payload = critical_damage.restore_crew(vehicle, 'driver')

        self.assertNotIn('driver', vehicle._crew_ko)
        self.assertIn('loader1', vehicle._crew_ko)
        self.assertEqual(
            [('driver', 'normal', 'repair')],
            [(event['name'], event['state'], event['cause'])
             for event in payload['events']])


class CrewInjuryLawTests(unittest.TestCase):
    """#1513 VehicleDescrCrew: factor = 0.57 + 0.43 * role average / 100."""

    def test_a_fit_100_percent_crew_sits_on_the_commander_bonus(self):
        # Level 100 plus the live commander's 100/10 gives 1.043.
        self.assertAlmostEqual(1.043, device_damage.CREW_FACTOR_FIT)
        self.assertAlmostEqual(0.57, device_damage.CREW_FACTOR_ROLE_OUT)
        self.assertAlmostEqual(1.0, device_damage.CREW_FACTOR_COMMANDER_OUT)

    def test_a_dead_single_man_role_lengthens_a_time_by_the_curve(self):
        self.assertAlmostEqual(
            1.043 / 0.57, device_damage.crew_stat_factor(
                ('loader1',), 'reload'))
        self.assertAlmostEqual(
            0.57 / 1.043, device_damage.crew_stat_factor(
                ('driver',), 'mobility'))

    def test_a_dead_gunner_now_reaches_aim_time_and_turret_traverse(self):
        for stat in ('dispersion', 'aim_time'):
            self.assertAlmostEqual(
                1.043 / 0.57,
                device_damage.crew_stat_factor(('gunner1',), stat))
        self.assertAlmostEqual(
            0.57 / 1.043,
            device_damage.crew_stat_factor(('gunner1',), 'turret_speed'))

    def test_a_dead_radioman_reaches_signal_and_view_range(self):
        self.assertAlmostEqual(
            0.57 / 1.043,
            device_damage.crew_stat_factor(('radioman1',), 'signal'))
        self.assertAlmostEqual(
            0.57 / 1.043,
            device_damage.crew_stat_factor(('radioman1',), 'vision'))

    def test_a_dead_commander_costs_every_other_role_its_bonus(self):
        # His own factor drops to 1.0, so times grow and speeds shrink by 4.3%.
        self.assertAlmostEqual(
            1.043, device_damage.crew_stat_factor(
                ('commander',), 'reload'))
        self.assertAlmostEqual(
            1.0 / 1.043, device_damage.crew_stat_factor(
                ('commander',), 'turret_speed'))
        # Commander out also takes his own view-range role down.
        self.assertAlmostEqual(
            0.57 / 1.043, device_damage.crew_stat_factor(
                ('commander',), 'vision'))

    def test_a_fit_crew_changes_nothing(self):
        for stat in ('reload', 'aim_time', 'dispersion', 'turret_speed',
                     'mobility', 'vision', 'signal'):
            self.assertEqual(1.0, device_damage.crew_stat_factor((), stat))


if __name__ == '__main__':
    unittest.main()
