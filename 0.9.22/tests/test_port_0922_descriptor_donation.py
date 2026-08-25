import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORT_ROOT = ROOT / '0.9.22'
CLIENT_ROOT = PORT_ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_ROOT))

from gui.mods.offline_lan_0922 import descriptor_donation  # noqa: E402


class _Tester(object):
    def __init__(self, bbox):
        self.bbox = bbox


class _Vector(object):
    """Math.Vector2/3 double: iterable and indexable, not a list or tuple."""

    def __init__(self, *values):
        self._values = values

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __getitem__(self, index):
        return self._values[index]


class _ShellType(object):
    """shell_components.ShellType double: .name carries the kind string."""

    __slots__ = ('name', 'explosionRadius')

    def __init__(self, name, explosionRadius=None):
        self.name = name
        if explosionRadius is not None:
            self.explosionRadius = explosionRadius


class ProjectionBuilderTest(unittest.TestCase):
    def _descriptor(self):
        # Field shapes mirror the exact #1513 readers: hullPosition and
        # turretPositions are vectors (readVector3), turretYawLimits and
        # piercingPower are Vector2, and the shell kind lives on shell.type.
        shell = types.SimpleNamespace(
            type=_ShellType('ARMOR_PIERCING'), caliber=45.0,
            damage=(110.0, 110.0), isTracer=False, effectsIndex=3)
        gun = types.SimpleNamespace(
            name='45mm-20K', id=101,
            hitTester=_Tester(((-0.25, -0.25, -1.2),
                               (0.25, 0.25, 1.2), None)),
            shots=(types.SimpleNamespace(
                shell=shell, speed=700.0, gravity=9.81, maxDistance=720.0,
                piercingPower=_Vector(80.0, 60.0)),),
            reloadTime=2.3, clip=(3, 0.35), burst=(3, 0.1),
            turretYawLimits=_Vector(-3.14, 3.14),
            pitchLimits={'minPitch': [(0.0, -0.35)], 'maxPitch': [(0.0, 0.15)]},
            rotationSpeed=0.7, shotDispersionAngle=0.0046,
            invisibilityFactorAtShot=0.25,
            maxHealth=54, maxRegenHealth=27)
        chassis = types.SimpleNamespace(
            name='MS-1', id=201,
            hitTester=_Tester(((-1.5, -0.8, -3.5), (1.5, 0.8, 3.5), None)),
            hullPosition=_Vector(0.0, 0.6, 0.0), rotationSpeed=0.66,
            shotDispersionFactors=(0.14, 0.14),
            maxHealth=170, maxRegenHealth=130)
        hull = types.SimpleNamespace(
            name='MS-1', id=301,
            hitTester=_Tester(((-1.7, -0.2, -3.5), (1.7, 1.4, 3.5), None)),
            turretPositions=(_Vector(0.0, 1.0, 0.0),),
            primaryArmor=(18.0, 16.0, 16.0),
            materials={
                1: types.SimpleNamespace(
                    armor=18.0, vehicleDamageFactor=1.0),
                2: types.SimpleNamespace(
                    armor=6.0, vehicleDamageFactor=0.0),
                3: types.SimpleNamespace(
                    armor=16.0, vehicleDamageFactor=1.0),
            })
        vehicle_type = types.SimpleNamespace(
            name='ussr:R11_MS-1', level=1, tags=('lightTank',),
            invisibility=(0.1, 0.2), invisibilityDeltas={},
            crewRoles=(('commander', 'gunner', 'radioman', 'loader'),
                       ('driver',)))
        turret = types.SimpleNamespace(
            name='MS-1', id=401,
            hitTester=_Tester(((-0.9, -0.3, -0.9),
                               (0.9, 0.8, 0.9), None)),
            rotationSpeed=0.7, circularVisionRadius=445.0,
            yawLimits=_Vector(-3.14, 3.14),
            gunPosition=_Vector(0.0, 0.25, 0.15))
        descriptor = types.SimpleNamespace(
            type=vehicle_type, maxHealth=1000, gun=gun,
            turret=turret,
            physics={'weight': 8000.0, 'speedLimits': (9.4, 4.0)},
            chassis=chassis, hull=hull,
            miscAttrs={
                'circularVisionRadiusFactor': 1.0,
                'invisibilityFactor': 1.0,
                'antifragmentationLiningFactor': 1.25,
            },
            optionalDevices=(),
            engine={'name': 'T-18', 'id': 501,
                    'maxHealth': 100, 'maxRegenHealth': 50})
        descriptor.computeBaseInvisibility = (
            lambda crew_factor, camouflage_id=None:
            (0.1 * crew_factor, 0.2 * crew_factor))
        return descriptor

    def test_projection_round_trips_through_json(self):
        projection = descriptor_donation.project_descriptor(
            self._descriptor())
        encoded = json.dumps(projection)
        decoded = json.loads(encoded)
        self.assertEqual('ussr:R11_MS-1', decoded['name'])
        self.assertEqual(1, decoded['level'])
        self.assertEqual(['lightTank'], decoded['tags'])
        self.assertEqual('ussr:R11_MS-1', decoded['type']['name'])
        self.assertEqual(
            [['commander', 'gunner', 'radioman', 'loader'], ['driver']],
            decoded['type']['crewRoles'])
        self.assertEqual(1000, decoded['maxHealth'])
        self.assertEqual([3, 0.1], decoded['gun']['burst'])
        shot = decoded['gun']['shots'][0]
        self.assertEqual(700.0, shot['speed'])
        self.assertEqual([80.0, 60.0], shot['piercingPower'])
        self.assertEqual('ARMOR_PIERCING', shot['shell']['kind'])
        self.assertEqual([0.0, 0.6, 0.0],
                         decoded['chassis']['hullPosition'])
        self.assertEqual([[0.0, 1.0, 0.0]],
                         decoded['hull']['turretPositions'])
        self.assertEqual([-3.14, 3.14], decoded['gun']['turretYawLimits'])
        self.assertEqual('45mm-20K', decoded['gun']['name'])
        self.assertEqual([[-1.7, -0.2, -3.5], [1.7, 1.4, 3.5], None],
                         decoded['hull']['hitTester']['bbox'])
        self.assertEqual([18.0, 16.0, 16.0],
                         decoded['hull']['primaryArmor'])
        self.assertEqual([16.0, 18.0],
                         decoded['hull']['heStructuralArmor'])
        self.assertEqual([0.0, 0.25, 0.15],
                         decoded['turret']['gunPosition'])
        self.assertEqual(
            [[-0.9, -0.3, -0.9], [0.9, 0.8, 0.9], None],
            decoded['turret']['hitTester']['bbox'])
        self.assertEqual(
            [[-0.25, -0.25, -1.2], [0.25, 0.25, 1.2], None],
            decoded['gun']['hitTester']['bbox'])
        self.assertNotIn('materials', decoded['hull'])
        self.assertNotIn('materials', decoded['turret'])
        self.assertEqual(100.0, decoded['engine']['maxHealth'])
        self.assertEqual({
            'player': {'available': False},
            'botDefault': {'available': False},
        }, decoded['repairSettings'])
        self.assertEqual({
            'player': {'available': False},
            'botDefault': {'available': False},
        }, decoded['spottingSettings'])
        self.assertEqual({
            'botDefault': {
                'spall_coefficient': 1.25,
                'ramming_bonus': 0.0,
            },
        }, decoded['rammingSettings'])

    def test_exact_player_and_bot_repair_inputs_are_donated_separately(self):
        descriptor = self._descriptor()
        crew = (types.SimpleNamespace(skills=('repair',)),)
        large_kit = types.SimpleNamespace(name='largeRepairKit')
        rpm_limiter = types.SimpleNamespace(name='removedRpmLimiter')
        equipments = (large_kit, rpm_limiter)
        original_factors = descriptor_donation.loadout_law.attribute_factors
        original_modifiers = descriptor_donation.loadout_law.modifiers
        original_skills = descriptor_donation.loadout_law.crew_skill_names
        self.addCleanup(setattr, descriptor_donation.loadout_law,
                        'attribute_factors', original_factors)
        self.addCleanup(setattr, descriptor_donation.loadout_law,
                        'modifiers', original_modifiers)
        self.addCleanup(setattr, descriptor_donation.loadout_law,
                        'crew_skill_names', original_skills)

        factor_calls = []

        def factors(value, crew=None, equipments=()):
            factor_calls.append((value, crew, tuple(equipments)))
            return {'native': True}

        def modifiers(value, mounted=(), crew_skills=None, factors=None):
            self.assertIs(descriptor, value)
            self.assertEqual({'native': True}, factors)
            if mounted:
                self.assertEqual((('repair',),), crew_skills)
                return {'repair_factor': 0.83, 'has_big_kit': True}
            self.assertIsNone(crew_skills)
            return {'repair_factor': 0.57, 'has_big_kit': False}

        descriptor_donation.loadout_law.attribute_factors = factors
        descriptor_donation.loadout_law.modifiers = modifiers
        descriptor_donation.loadout_law.crew_skill_names = (
            lambda value: (('repair',),))

        projection = descriptor_donation.project_descriptor(
            descriptor, player_loadout=(crew, equipments))

        self.assertEqual({
            'player': {
                'available': True,
                'repairFactor': 0.83,
                'hasBigKit': True,
            },
            'botDefault': {
                'available': True,
                'repairFactor': 0.57,
                'hasBigKit': False,
            },
        }, projection['repairSettings'])
        self.assertEqual([
            (descriptor, crew, (large_kit,)),
            (descriptor, None, ()),
        ], factor_calls[:2])
        self.assertEqual([(descriptor, None, ())], factor_calls[2:])

    def test_unproven_or_invalid_repair_inputs_fail_closed(self):
        descriptor = self._descriptor()
        original_factors = descriptor_donation.loadout_law.attribute_factors
        original_modifiers = descriptor_donation.loadout_law.modifiers
        self.addCleanup(setattr, descriptor_donation.loadout_law,
                        'attribute_factors', original_factors)
        self.addCleanup(setattr, descriptor_donation.loadout_law,
                        'modifiers', original_modifiers)

        descriptor_donation.loadout_law.attribute_factors = (
            lambda *args, **kwargs: None)
        projection = descriptor_donation.project_descriptor(
            descriptor, player_loadout=((), ()))
        self.assertEqual({
            'player': {'available': False},
            'botDefault': {'available': False},
        }, projection['repairSettings'])

        descriptor_donation.loadout_law.attribute_factors = (
            lambda *args, **kwargs: {'native': True})
        descriptor_donation.loadout_law.modifiers = (
            lambda *args, **kwargs: {
                'repair_factor': float('nan'), 'has_big_kit': 1})
        projection = descriptor_donation.project_descriptor(
            descriptor, player_loadout=((), ()))
        self.assertEqual({
            'player': {'available': False},
            'botDefault': {'available': False},
        }, projection['repairSettings'])

    def test_exact_player_and_bot_spotting_inputs_are_donated_separately(self):
        descriptor = self._descriptor()
        descriptor.miscAttrs['circularVisionRadiusFactor'] = 1.1
        descriptor.optionalDevices = (
            types.SimpleNamespace(
                name='stereoscope', circularVisionRadiusFactor=1.25,
                activateWhenStillSec=4.0),
            types.SimpleNamespace(
                name='camouflageNet', activateWhenStillSec=5.0),
        )
        crew = (types.SimpleNamespace(
            role='commander', roleLevel=100.0, skills=()),)
        rations = types.SimpleNamespace(name='extraCombatRations')
        rpm_limiter = types.SimpleNamespace(name='removedRpmLimiter')
        equipments = (rations, rpm_limiter)

        original_factors = descriptor_donation.loadout_law.attribute_factors
        original_increase = descriptor_donation.loadout_law.crew_level_increase
        self.addCleanup(setattr, descriptor_donation.loadout_law,
                        'attribute_factors', original_factors)
        self.addCleanup(setattr, descriptor_donation.loadout_law,
                        'crew_level_increase', original_increase)
        factor_calls = []
        increase_calls = []
        base_calls = []

        def factors(value, crew=None, equipments=()):
            factor_calls.append((value, crew, tuple(equipments)))
            if crew is None:
                return {
                    'circularVisionRadius': 1.10,
                    'camouflage': 0.57,
                    'invisibility': {
                        0: (0.0, 1.0), 1: (0.09, 1.0)},
                    '_aspects': (0, 1),
                }
            return {
                'circularVisionRadius': 1.32,
                'camouflage': 0.74,
                'invisibility': {
                    0: (0.01, 0.98), 1: (0.13, 1.02)},
                '_aspects': (0, 1),
            }

        def level_increase(value, mounted=(), crew_skills=None):
            increase_calls.append((value, tuple(mounted), crew_skills))
            return 7.0 if mounted else 0.0

        def base_invisibility(crew_factor, camouflage_id=None):
            base_calls.append((crew_factor, camouflage_id))
            paint = 0.03 if camouflage_id == 77 else 0.0
            return (crew_factor * 0.1 + paint,
                    crew_factor * 0.2 + paint)

        descriptor_donation.loadout_law.attribute_factors = factors
        descriptor_donation.loadout_law.crew_level_increase = level_increase
        descriptor.computeBaseInvisibility = base_invisibility

        projection = json.loads(json.dumps(
            descriptor_donation.project_descriptor(
                descriptor,
                player_loadout=(crew, equipments, 77))))

        player = projection['spottingSettings']['player']
        bot = projection['spottingSettings']['botDefault']
        self.assertTrue(player['available'])
        self.assertTrue(bot['available'])
        self.assertEqual({
            'baseRangeMetres': 445.0,
            'miscFactor': 1.1,
            'crewFactor': 1.32 / (1.25 / 1.1),
            'binocularFactor': 1.25 / 1.1,
            'hasBinoculars': True,
            'binocularDelayUs': 4000000,
        }, player['observer'])
        self.assertEqual({
            'moving': 0.104,
            'stationary': 0.178,
            'movingAspect': {'additive': 0.01, 'multiplier': 0.98},
            'stationaryAspect': {'additive': 0.13, 'multiplier': 1.02},
            'hasCamouflageNet': True,
            'camouflageNetDelayUs': 5000000,
            'invisibilityFactorAtShot': 0.25,
        }, player['target'])
        self.assertAlmostEqual(
            1.10 / (1.25 / 1.1), bot['observer']['crewFactor'])
        self.assertAlmostEqual(0.057, bot['target']['moving'])
        self.assertAlmostEqual(0.114, bot['target']['stationary'])
        self.assertEqual((0.74, 77), base_calls[0])
        self.assertEqual((0.57, None), base_calls[1])
        # The trigger-only RPM limiter is absent from native factor input but
        # the crew-level helper receives the real garage equipment tuple.
        spotting_factor_calls = factor_calls[-2:]
        self.assertEqual((rations,), spotting_factor_calls[0][2])
        self.assertEqual((), spotting_factor_calls[1][2])
        self.assertEqual(equipments, increase_calls[0][1])
        self.assertEqual((), increase_calls[1][1])

    def test_unproven_or_invalid_spotting_inputs_fail_closed(self):
        descriptor = self._descriptor()
        original_factors = descriptor_donation.loadout_law.attribute_factors
        self.addCleanup(setattr, descriptor_donation.loadout_law,
                        'attribute_factors', original_factors)

        descriptor_donation.loadout_law.attribute_factors = (
            lambda *args, **kwargs: {
                'circularVisionRadius': 1.0,
                'camouflage': 0.57,
                # Missing both invisibility and aspect identities must not
                # turn spotting_profile's legacy fallback into authority.
            })
        projection = descriptor_donation.project_descriptor(
            descriptor, player_loadout=((), ()))
        self.assertEqual({
            'player': {'available': False},
            'botDefault': {'available': False},
        }, projection['spottingSettings'])

        descriptor_donation.loadout_law.attribute_factors = (
            lambda *args, **kwargs: {
                'circularVisionRadius': 1.0,
                'camouflage': 0.57,
                'invisibility': {0: (0.0, 1.0), 1: (0.0, 1.0)},
                '_aspects': (0, 1),
            })
        descriptor.computeBaseInvisibility = (
            lambda *args, **kwargs: (float('nan'), 0.2))
        projection = descriptor_donation.project_descriptor(
            descriptor, player_loadout=((), ()))
        self.assertEqual({
            'player': {'available': False},
            'botDefault': {'available': False},
        }, projection['spottingSettings'])

    def test_mounted_shot_projection_is_small_exact_and_json_safe(self):
        shot = self._descriptor().gun.shots[0]

        projected = json.loads(json.dumps(
            descriptor_donation.project_shot(shot)))

        self.assertEqual({
            'speed', 'gravity', 'maxDistance', 'piercingPower', 'deadeye',
            'shell'},
            set(projected))
        self.assertFalse(projected['deadeye'])
        self.assertEqual({
            'kind', 'caliber', 'damage', 'explosionRadius'},
            set(projected['shell']))
        self.assertEqual([80.0, 60.0], projected['piercingPower'])
        self.assertEqual([110.0, 110.0], projected['shell']['damage'])
        self.assertEqual(0.0, projected['shell']['explosionRadius'])
        self.assertNotIn('effectsIndex', projected['shell'])

    def test_high_explosive_radius_comes_from_the_shell_type(self):
        descriptor = self._descriptor()
        descriptor.gun.shots[0].shell = types.SimpleNamespace(
            type=_ShellType('HIGH_EXPLOSIVE', explosionRadius=1.85),
            caliber=122.0, damage=(450.0, 90.0), isTracer=True,
            effectsIndex=7)

        projection = descriptor_donation.project_descriptor(descriptor)

        shell = projection['gun']['shots'][0]['shell']
        self.assertEqual('HIGH_EXPLOSIVE', shell['kind'])
        self.assertEqual(1.85, shell['explosionRadius'])





    def test_modern_catalog_prefix_maps_only_to_an_exact_legacy_profile(self):
        from gui.mods.offline_lan_0922 import internal_hit_layouts

        known = {
            'ussr:R11_MS-1': ('ussr', 'ms1'),
            'ussr:R04_T-34': ('ussr', 't34'),
            'france:F15_AMX_12t': ('france', 'amx12t'),
            'usa:A36_Sherman_Jumbo': ('usa', 'shermanjumbo'),
            'usa:A63_M46_Patton': ('usa', 'm46patton'),
        }
        for vehicle_name, expected_key in known.items():
            with self.subTest(vehicle=vehicle_name):
                key, profile = internal_hit_layouts._compiled_profile(
                    vehicle_name)
                self.assertEqual(expected_key, key)
                self.assertIsNotNone(profile)

        key, profile = internal_hit_layouts._compiled_profile(
            'japan:J24_Mi_To_130_tons')
        self.assertEqual(('japan', 'j24mito130tons'), key)
        self.assertIsNone(profile)

    def test_missing_hull_bbox_fails_closed(self):
        descriptor = self._descriptor()
        descriptor.hull = types.SimpleNamespace(turretPositions=())
        with self.assertRaises(ValueError):
            descriptor_donation.project_descriptor(descriptor)

    def test_vehicle_catalog_reads_the_runtime_list(self):
        entries = {
            0: {1: types.SimpleNamespace(
                name='ussr:R11_MS-1', level=1, tags=('lightTank',))},
            1: {7: types.SimpleNamespace(
                name='germany:G12_Ltraktor', level=1,
                tags=('lightTank',))},
        }
        runtime = types.SimpleNamespace(
            nations=types.SimpleNamespace(
                AVAILABLE_NAMES=('ussr', 'germany'),
                INDICES={'ussr': 0, 'germany': 1}),
            vehicles=types.SimpleNamespace(
                g_list=types.SimpleNamespace(
                    getList=lambda nation_id: entries[nation_id])))
        rows = descriptor_donation.vehicle_catalog(runtime)
        self.assertEqual(
            ['germany:G12_Ltraktor', 'ussr:R11_MS-1'],
            [row['name'] for row in rows])
        self.assertEqual(['lightTank'], rows[0]['tags'])

    def test_project_vehicles_reports_each_projection_failure(self):
        descriptor = self._descriptor()

        def resolve(typeName=None):
            if typeName == 'test:good':
                return descriptor
            raise ValueError('missing descriptor')

        runtime = types.SimpleNamespace(
            vehicles=types.SimpleNamespace(VehicleDescr=resolve))
        failures = []

        projections = descriptor_donation.project_vehicles(
            runtime, ['test:good', 'test:bad'], failures=failures)

        self.assertEqual(['test:good'], sorted(projections))
        self.assertEqual(['test:bad'], failures)

    def test_a_mounted_fitting_replaces_the_stock_descriptor(self):
        stock = self._descriptor()
        fitted = self._descriptor()
        fitted.hull.maxHealth = 4321
        seen = []

        def resolve(typeName=None, compactDescr=None):
            seen.append((typeName, compactDescr))
            return fitted if compactDescr == 'fitted' else stock

        runtime = types.SimpleNamespace(
            vehicles=types.SimpleNamespace(VehicleDescr=resolve))

        projections = descriptor_donation.project_vehicles(
            runtime, ['test:good'], fittings={'test:good': 'fitted'})

        self.assertEqual([(None, 'fitted')], seen)
        self.assertEqual(4321, projections['test:good']['hull']['maxHealth'])

    def test_mounted_fitting_reads_current_players_exact_repair_context(self):
        fitted = self._descriptor()
        fitted.type.name = 'test:good'
        crew = (types.SimpleNamespace(skills=('repair',)),)
        large_kit = types.SimpleNamespace(name='largeRepairKit')
        consumables = types.SimpleNamespace(
            getInstalledItems=lambda: (large_kit,))
        item = types.SimpleNamespace(
            descriptor=fitted, crew=crew,
            getBonusCamo=lambda: types.SimpleNamespace(id=77),
            equipment=types.SimpleNamespace(
                regularConsumables=consumables))
        current = types.SimpleNamespace(
            isPresent=lambda: True, item=item)
        previous_current = sys.modules.get('CurrentVehicle')
        sys.modules['CurrentVehicle'] = types.SimpleNamespace(
            g_currentVehicle=current)

        def restore_current():
            if previous_current is None:
                sys.modules.pop('CurrentVehicle', None)
            else:
                sys.modules['CurrentVehicle'] = previous_current

        self.addCleanup(restore_current)
        self.assertEqual(
            (crew, (large_kit,), 77),
            descriptor_donation._current_player_loadout('test:good'))
        original_factors = descriptor_donation.loadout_law.attribute_factors
        original_modifiers = descriptor_donation.loadout_law.modifiers
        original_skills = descriptor_donation.loadout_law.crew_skill_names
        self.addCleanup(setattr, descriptor_donation.loadout_law,
                        'attribute_factors', original_factors)
        self.addCleanup(setattr, descriptor_donation.loadout_law,
                        'modifiers', original_modifiers)
        self.addCleanup(setattr, descriptor_donation.loadout_law,
                        'crew_skill_names', original_skills)
        descriptor_donation.loadout_law.attribute_factors = (
            lambda *args, **kwargs: {'native': True})
        descriptor_donation.loadout_law.crew_skill_names = (
            lambda value: (('repair',),))
        descriptor_donation.loadout_law.modifiers = (
            lambda descriptor, equipments=(), crew_skills=None, factors=None:
            {'repair_factor': (0.91 if equipments else 0.57),
             'has_big_kit': bool(equipments)})
        runtime = types.SimpleNamespace(vehicles=types.SimpleNamespace(
            VehicleDescr=lambda typeName=None, compactDescr=None: fitted))

        projection = descriptor_donation.project_vehicles(
            runtime, ['test:good'], fittings={'test:good': 'fitted'}
        )['test:good']

        self.assertEqual({
            'available': True, 'repairFactor': 0.91, 'hasBigKit': True,
        }, projection['repairSettings']['player'])
        self.assertEqual({
            'available': True, 'repairFactor': 0.57, 'hasBigKit': False,
        }, projection['repairSettings']['botDefault'])

    def test_player_authority_loadout_is_connection_scoped_to_current_garage(self):
        descriptor = self._descriptor()
        crew = (types.SimpleNamespace(role='commander'),)
        equipment = types.SimpleNamespace(name='largeRepairKit')
        regular = types.SimpleNamespace(
            getInstalledItems=lambda: (equipment,))
        item = types.SimpleNamespace(
            descriptor=descriptor,
            crew=crew,
            equipment=types.SimpleNamespace(regularConsumables=regular),
            getBonusCamo=lambda: types.SimpleNamespace(id=77))
        current = types.SimpleNamespace(
            isPresent=lambda: True, item=item)
        previous_current = sys.modules.get('CurrentVehicle')
        sys.modules['CurrentVehicle'] = types.SimpleNamespace(
            g_currentVehicle=current)
        self.addCleanup(
            lambda: (sys.modules.pop('CurrentVehicle', None)
                     if previous_current is None else
                     sys.modules.__setitem__('CurrentVehicle',
                                             previous_current)))
        original_repair = descriptor_donation._repair_loadout
        original_spotting = descriptor_donation._spotting_loadout
        self.addCleanup(setattr, descriptor_donation, '_repair_loadout',
                        original_repair)
        self.addCleanup(setattr, descriptor_donation, '_spotting_loadout',
                        original_spotting)
        calls = []

        def repair(value, crew=None, equipments=()):
            calls.append(('repair', value, crew, equipments))
            return {'available': True, 'repairFactor': 0.83,
                    'hasBigKit': True}

        def spotting(value, crew=None, equipments=(), camouflage_id=None):
            calls.append(('spotting', value, crew, equipments,
                          camouflage_id))
            return {'available': False}

        descriptor_donation._repair_loadout = repair
        descriptor_donation._spotting_loadout = spotting

        donated = descriptor_donation.current_player_authority_loadout()

        self.assertEqual({
            'repair': {'available': True, 'repairFactor': 0.83,
                       'hasBigKit': True},
            'spotting': {'available': False},
        }, donated)
        self.assertEqual(('repair', descriptor, crew, (equipment,)), calls[0])
        self.assertEqual(
            ('spotting', descriptor, crew, (equipment,), 77), calls[1])


if __name__ == '__main__':
    unittest.main()
