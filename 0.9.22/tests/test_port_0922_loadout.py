from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = (
    ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import gun_mechanics, loadout, spotting


def _device(name):
    return types.SimpleNamespace(name=name)


def _descriptor(devices=()):
    return types.SimpleNamespace(optionalDevices=list(devices))


def _crew(*skill_lists):
    return tuple(
        types.SimpleNamespace(
            skills=[types.SimpleNamespace(name=name) for name in skills])
        for skills in skill_lists)


class LoadoutLawTests(unittest.TestCase):

    def test_a_bare_crew_uses_the_exact_1513_curve(self):
        values = loadout.baseline()

        self.assertEqual(110.0, values['effective_crew_level'])
        # VehicleDescrCrew._processSkills: 0.57 + 0.43 * (level / 100).
        self.assertAlmostEqual(
            0.57 + 0.0043 * 110.0, values['crew_factor'])
        self.assertAlmostEqual(
            1.0 / (0.57 + 0.0043 * 110.0), values['crew_multiplier'])
        # reload_factor and aim_time_factor are COMPLETE multipliers now.
        self.assertAlmostEqual(
            values['crew_multiplier'], values['reload_factor'])
        self.assertAlmostEqual(
            values['crew_multiplier'], values['aim_time_factor'])
        self.assertAlmostEqual(
            values['crew_multiplier'], values['dispersion_factor'])

    def test_ventilation_brotherhood_and_food_stack_on_the_crew_level(self):
        crew = _crew(['brotherhood'], ['brotherhood'])

        values = loadout.modifiers(
            _descriptor([_device('improvedVentilation_class1')]),
            equipments=[_device('chocolate')], crew_skills=
            loadout.crew_skill_names(crew))

        # 100 + 5 + 5 + 10 for both the crew and the commander share.
        self.assertEqual(120.0, values['crew_level'])
        self.assertEqual(132.0, values['effective_crew_level'])
        self.assertTrue(values['has_ventilation'])
        self.assertTrue(values['has_brotherhood'])
        self.assertTrue(values['has_rations'])
        self.assertAlmostEqual(
            1.0 / (0.57 + 0.0043 * 132.0), values['crew_multiplier'])

    def test_brotherhood_needs_every_crew_member(self):
        crew = _crew(['brotherhood'], ['repair'])

        values = loadout.modifiers(
            crew_skills=loadout.crew_skill_names(crew))

        self.assertFalse(values['has_brotherhood'])
        self.assertEqual(110.0, values['effective_crew_level'])

    def test_rammer_and_gun_laying_drive_use_the_exact_082_factors(self):
        values = loadout.modifiers(
            _descriptor([_device('gunRammer'), _device('aimDrives')]))
        crew = values['crew_multiplier']

        self.assertAlmostEqual(crew * 0.9, values['reload_factor'])
        self.assertAlmostEqual(crew / 1.1, values['aim_time_factor'])

    def test_the_descriptor_factor_beats_the_device_name(self):
        # deluxRammer is 0.875, not the 0.9 the 0.8.2 name match assumed.
        descriptor = _descriptor([_device('deluxRammer')])
        descriptor.miscAttrs = {'gunReloadTimeFactor': 0.875,
                                'gunAimingTimeFactor': 0.89}
        values = loadout.modifiers(descriptor)
        crew = values['crew_multiplier']

        self.assertAlmostEqual(crew * 0.875, values['reload_factor'])
        self.assertAlmostEqual(crew * 0.89, values['aim_time_factor'])

    def test_stabiliser_snap_shot_and_smooth_ride_dampen_the_bloom(self):
        crew = _crew(['snapShot', 'smoothDriving'])

        values = loadout.modifiers(
            _descriptor([_device('stabilizer')]),
            crew_skills=loadout.crew_skill_names(crew))

        self.assertAlmostEqual(0.8 * 0.96, values['bloom_move_factor'])
        self.assertAlmostEqual(0.8, values['bloom_rotation_factor'])
        self.assertAlmostEqual(0.8 * 0.925, values['bloom_turret_factor'])

    def test_unknown_crew_never_claims_brothers_in_arms(self):
        self.assertFalse(loadout.modifiers()['has_brotherhood'])
        self.assertFalse(
            loadout.modifiers(crew_skills=())['has_brotherhood'])

    def test_finished_skill_count_accepts_an_enabled_combined_role(self):
        loader_radioman = types.SimpleNamespace(skills=(
            types.SimpleNamespace(
                name='loader_intuition', level=100.0, isActive=True,
                isEnable=lambda: True),))

        self.assertEqual(
            1,
            loadout.finished_skill_count(
                ((3, loader_radioman),), 'loader_intuition'))
        self.assertEqual(
            1, loadout.intuition_chances(((3, loader_radioman),)))

    def test_finished_skill_count_rejects_inactive_disabled_or_partial(self):
        crew = tuple(types.SimpleNamespace(skills=(skill,)) for skill in (
            types.SimpleNamespace(
                name='loader_intuition', level=100.0, isActive=False,
                isEnable=True),
            types.SimpleNamespace(
                name='loader_intuition', level=100.0, isActive=True,
                isEnable=lambda: False),
            types.SimpleNamespace(
                name='loader_intuition', level=99.0, isActive=True,
                isEnable=True),
        ))

        self.assertEqual(
            0, loadout.finished_skill_count(crew, 'loader_intuition'))

    def test_discrete_skill_projection_requires_every_state_field(self):
        values = {
            'name': 'commander_expert',
            'level': 100.0,
            'isActive': True,
            'isEnable': True,
        }
        self.assertEqual({
            'name': 'commander_expert',
            'level': 100.0,
            'active': True,
            'enabled': True,
        }, loadout.project_skill_state(types.SimpleNamespace(**values)))
        for name in tuple(values):
            incomplete = dict(values)
            del incomplete[name]
            self.assertIsNone(loadout.project_skill_state(
                types.SimpleNamespace(**incomplete)), name)
        malformed = dict(values, isEnable=1)
        self.assertIsNone(loadout.project_skill_state(
            types.SimpleNamespace(**malformed)))
        malformed = dict(values, level=float('nan'))
        self.assertIsNone(loadout.project_skill_state(
            types.SimpleNamespace(**malformed)))

    def test_controlled_impact_requires_proved_activation_and_eligibility(self):
        def member(**values):
            state = {
                'name': 'driver_rammingmaster',
                'level': 100.0,
                'isActive': True,
                'isEnable': True,
            }
            state.update(values)
            return types.SimpleNamespace(
                skills=(types.SimpleNamespace(**state),))

        self.assertEqual(0.15, loadout.ramming_bonus((member(),)))
        self.assertEqual(
            0.0, loadout.ramming_bonus((member(isActive=False),)))
        self.assertEqual(
            0.0, loadout.ramming_bonus((member(isEnable=False),)))
        self.assertEqual(
            0.1485, loadout.ramming_bonus((member(level=99.0),)))
        self.assertEqual(
            0.0, loadout.ramming_bonus((types.SimpleNamespace(skills=(
                types.SimpleNamespace(
                    name='driver_rammingmaster', level=100.0,
                    isActive=True),)),)))


class GunStateLoadoutTests(unittest.TestCase):

    def _gun_descriptor(self):
        return types.SimpleNamespace(
            gun={'shots': [{'shell': {'damage': (100.0,)}}],
                 'shotDispersionAngle': 0.1,
                 'shotDispersionFactors': {'afterShot': 1.5,
                                           'turretRotation': 1.0},
                 'aimingTime': 2.0, 'reloadTime': 10.0,
                 'clip': (1, 2.0), 'maxAmmo': 40},
            chassis={'shotDispersionFactors': (0.1, 0.1)},
            turret={'maxAmmo': 40}, maxAmmo=40, activeGunShotIndex=0,
            optionalDevices=[])

    def test_a_rammer_shortens_the_reload_on_top_of_the_crew_law(self):
        descriptor = self._gun_descriptor()
        plain = gun_mechanics.GunState(descriptor)

        descriptor.optionalDevices = [_device('gunRammer')]
        rammed = gun_mechanics.GunState(
            descriptor, loadout.modifiers(descriptor))

        self.assertAlmostEqual(plain.reload * 0.9, rammed.reload)
        self.assertAlmostEqual(plain.aim_time, rammed.aim_time)

    def test_a_gun_laying_drive_shortens_only_the_aiming_time(self):
        descriptor = self._gun_descriptor()
        plain = gun_mechanics.GunState(descriptor)

        descriptor.optionalDevices = [_device('aimDrives')]
        aided = gun_mechanics.GunState(
            descriptor, loadout.modifiers(descriptor))

        self.assertAlmostEqual(plain.aim_time / 1.1, aided.aim_time)
        self.assertAlmostEqual(plain.reload, aided.reload)

    def test_a_stabiliser_damps_the_movement_bloom(self):
        descriptor = self._gun_descriptor()
        plain = gun_mechanics.GunState(descriptor)
        plain.tick(0.0, False, 10.0, 0.0, 0.0, descriptor)

        descriptor.optionalDevices = [_device('vertStabilizer')]
        steady = gun_mechanics.GunState(
            descriptor, loadout.modifiers(descriptor))
        steady.tick(0.0, False, 10.0, 0.0, 0.0, descriptor)

        self.assertLess(steady.dispersion, plain.dispersion)


class SpottingProfileTests(unittest.TestCase):
    """The device magnitudes come from the client's own descriptors."""

    def _descriptor(self, devices=(), optics=1.0, net_bonus=0.1):
        return types.SimpleNamespace(
            optionalDevices=list(devices),
            miscAttrs={'circularVisionRadiusFactor': optics},
            type=types.SimpleNamespace(
                invisibilityDeltas={'camouflageNetBonus': net_bonus}))

    def _binoculars(self, factor=1.25, delay=3.0):
        return types.SimpleNamespace(
            name='stereoscope', circularVisionRadiusFactor=factor,
            activateWhenStillSec=delay)

    def _camouflage_net(self, delay=3.0):
        return types.SimpleNamespace(
            name='camouflageNet', activateWhenStillSec=delay)

    def test_a_bare_vehicle_carries_no_situational_device(self):
        profile = loadout.spotting_profile(self._descriptor())

        self.assertFalse(profile['has_binoculars'])
        self.assertFalse(profile['has_camouflage_net'])
        self.assertEqual(1.0, profile['binocular_factor'])
        self.assertEqual(0.0, profile['camouflage_net_bonus'])

    def test_binoculars_replace_coated_optics_instead_of_stacking(self):
        # #1513's Stereoscope divides the descriptor's optics factor out.
        profile = loadout.spotting_profile(
            self._descriptor([self._binoculars()], optics=1.1))

        self.assertTrue(profile['has_binoculars'])
        self.assertAlmostEqual(1.25 / 1.1, profile['binocular_factor'])
        base = 400.0 * 1.1
        self.assertAlmostEqual(
            400.0 * 1.25,
            spotting.effective_view_range(
                400.0, misc_factor=1.1,
                binocular_factor=profile['binocular_factor'],
                binocular_active=True))
        self.assertAlmostEqual(
            base,
            spotting.effective_view_range(400.0, misc_factor=1.1))

    def test_the_camouflage_net_bonus_comes_from_the_vehicle_type(self):
        profile = loadout.spotting_profile(
            self._descriptor([self._camouflage_net()], net_bonus=0.13))

        self.assertTrue(profile['has_camouflage_net'])
        self.assertAlmostEqual(0.13, profile['camouflage_net_bonus'])

    def test_a_still_device_waits_for_its_activation_delay(self):
        self.assertFalse(loadout.still_device_active(2.9, 3.0))
        self.assertTrue(loadout.still_device_active(3.0, 3.0))
        self.assertTrue(loadout.still_device_active(9.0, 3.0))

    def test_recon_and_situational_take_the_best_single_crewman(self):
        crew = (
            types.SimpleNamespace(role='commander', roleLevel=100.0, skills=[
                types.SimpleNamespace(name='commander_eagleEye', level=60.0)]),
            types.SimpleNamespace(role='radioman', roleLevel=100.0, skills=[
                types.SimpleNamespace(name='commander_eagleEye', level=90.0),
                types.SimpleNamespace(name='radioman_finder', level=50.0)]),
        )

        profile = loadout.spotting_profile(self._descriptor(), crew)

        self.assertEqual(90.0, profile['recon_level'])
        self.assertEqual(50.0, profile['situational_level'])

    def test_camouflage_is_averaged_over_the_whole_crew(self):
        crew = (
            types.SimpleNamespace(role='commander', roleLevel=100.0, skills=[
                types.SimpleNamespace(name='camouflage', level=100.0)]),
            types.SimpleNamespace(role='driver', roleLevel=100.0, skills=[]),
        )

        profile = loadout.spotting_profile(self._descriptor(), crew)

        # A member without the skill contributes zero, it is not skipped.
        self.assertAlmostEqual(50.0, profile['camouflage_level'])

    def test_an_inactive_skill_contributes_nothing(self):
        crew = (types.SimpleNamespace(role='commander', roleLevel=100.0, skills=[
            types.SimpleNamespace(
                name='radioman_finder', level=100.0, isActive=False)]),)

        profile = loadout.spotting_profile(self._descriptor(), crew)

        self.assertEqual(0.0, profile['situational_level'])

    def test_the_skills_lengthen_the_view_range(self):
        crew = (
            types.SimpleNamespace(role='commander', roleLevel=100.0, skills=[
                types.SimpleNamespace(name='commander_eagleEye', level=100.0),
                types.SimpleNamespace(name='radioman_finder', level=100.0)]),)
        plain = loadout.spotting_profile(self._descriptor())
        keen = loadout.spotting_profile(self._descriptor(), crew)

        self.assertAlmostEqual(
            plain['vision_factor'] * 1.02 * 1.03, keen['vision_factor'])
        self.assertAlmostEqual(
            spotting.effective_view_range(
                400.0, crew_factor=plain['vision_factor']) * 1.02 * 1.03,
            spotting.effective_view_range(
                400.0, crew_factor=keen['vision_factor']))


class CrewLevelIncreaseTests(unittest.TestCase):
    """#1513 folds ventilation into miscAttrs['crewLevelIncrease'] and
    VehicleDescrCrew adds it to every crewman before any efficiency is taken,
    so it must reach view range, not only reload."""

    def _descriptor(self, increase=0.0):
        descriptor = _descriptor([])
        descriptor.miscAttrs = {'crewLevelIncrease': increase}
        return descriptor

    def test_ventilation_raises_the_commander_level_for_spotting(self):
        plain = loadout.spotting_profile(
            self._descriptor(), _crew([], []),
            level_increase=loadout.crew_level_increase(self._descriptor()))
        vented = loadout.spotting_profile(
            self._descriptor(5.0), _crew([], []),
            level_increase=loadout.crew_level_increase(self._descriptor(5.0)))

        self.assertEqual(
            plain['commander_level'] + 5.0, vented['commander_level'])

    def test_brotherhood_and_rations_add_to_the_descriptor_increase(self):
        crew = _crew(['brotherhood'], ['brotherhood'])

        increase = loadout.crew_level_increase(
            self._descriptor(5.0), equipments=[_device('chocolate')],
            crew_skills=loadout.crew_skill_names(crew))

        self.assertAlmostEqual(
            5.0 + loadout.BROTHERHOOD_CREW_BONUS +
            loadout.RATION_CREW_BONUS, increase)

    def test_an_untrained_skill_is_not_raised_from_zero(self):
        # A crewman who never learned camouflage has nothing to add to, which
        # is why the garage concealment number does not move either.
        profile = loadout.spotting_profile(
            self._descriptor(5.0), _crew([], []), level_increase=5.0)

        self.assertEqual(0.0, profile['camouflage_level'])


class ClientFactorTests(unittest.TestCase):
    """With the client's own factors every consumer reads one dictionary."""

    FACTORS = {
        'turret/rotationSpeed': 1.05, 'gun/rotationSpeed': 1.05,
        'gun/reloadTime': 0.8, 'gun/aimingTime': 0.9,
        'shotDispersion': [0.85, 0.0], 'repairSpeed': 0.95,
        'vehicle/rotationSpeed': 1.04, 'radio/distance': 1.02,
        'chassis/terrainResistance': [0.9, 0.8, 0.7],
        'circularVisionRadius': 1.21, 'camouflage': 0.72,
        'invisibility': {0: [0.0, 1.0], 1: [0.11, 1.0]},
        '_aspects': (0, 1),
    }

    def test_native_modules_follow_the_pinned_1513_package_layout(self):
        constants = types.ModuleType('constants')
        constants.VEHICLE_TTC_ASPECTS = object()
        items = types.ModuleType('items')
        items.__path__ = []
        tankmen = types.ModuleType('items.tankmen')
        utils = types.ModuleType('items.utils')
        vehicles = types.ModuleType('items.vehicles')
        qualifiers = types.ModuleType('items.qualifiers')
        qualifiers.QUALIFIER_TYPE = object()
        descriptor_crew = types.ModuleType('items.VehicleDescrCrew')
        descriptor_crew.VehicleDescrCrew = object()
        qualifiers_applier = types.ModuleType('VehicleQualifiersApplier')
        qualifiers_applier.VehicleQualifiersApplier = object()
        items.tankmen = tankmen
        items.utils = utils
        items.vehicles = vehicles

        modules = {
            'constants': constants,
            'items': items,
            'items.tankmen': tankmen,
            'items.utils': utils,
            'items.vehicles': vehicles,
            'items.qualifiers': qualifiers,
            'items.VehicleDescrCrew': descriptor_crew,
            'VehicleQualifiersApplier': qualifiers_applier,
        }
        missing = object()
        old_top_level = sys.modules.pop('VehicleDescrCrew', missing)
        try:
            with mock.patch.dict(sys.modules, modules):
                result = loadout._client_modules()
        finally:
            if old_top_level is not missing:
                sys.modules['VehicleDescrCrew'] = old_top_level

        self.assertIsNotNone(result)
        self.assertIs(result[5], descriptor_crew.VehicleDescrCrew)
        self.assertIs(
            result[6], qualifiers_applier.VehicleQualifiersApplier)

    def _gun_descriptor(self):
        descriptor = _descriptor([])
        descriptor.miscAttrs = {'gunReloadTimeFactor': 0.875,
                                'gunAimingTimeFactor': 0.89}
        return descriptor

    def _native_factor_modules(self):
        utils = types.SimpleNamespace(
            generateDefaultCrew=mock.Mock(return_value=('default',)),
            makeDefaultVehicleAttributeFactors=mock.Mock(
                side_effect=lambda: {}))
        modules = (
            utils, types.SimpleNamespace(MAX_SKILL_LEVEL=100), object(),
            types.SimpleNamespace(DEFAULT=0, WHEN_STILL=1), object(),
            object(), object())
        return utils, modules

    def test_wrong_nation_crew_retries_once_with_the_default_crew(self):
        calls = []

        def update(unused_descriptor, compact_descrs, unused_equipments,
                   unused_factors, unused_flags, unused_is_fire,
                   unused_aspects, unused_qualifier_type,
                   unused_crew_class, unused_qualifiers_class):
            calls.append(tuple(compact_descrs))
            if compact_descrs == ['foreign']:
                raise Exception('wrong tankman nation: foreign, vehicle')

        utils, modules = self._native_factor_modules()
        descriptor = types.SimpleNamespace(
            type=types.SimpleNamespace(crewRoles=(('commander',),)),
            optionalDevices=[])
        crew = (types.SimpleNamespace(strCD='foreign'),)

        with mock.patch.object(loadout, '_client_modules',
                               return_value=modules), \
                mock.patch.object(
                    loadout, '_update_native_attribute_factors_with_split',
                    side_effect=update):
            factors = loadout.attribute_factors(descriptor, crew=crew)

        self.assertEqual([('foreign',), ('default',)], calls)
        utils.generateDefaultCrew.assert_called_once_with(
            descriptor.type, 100)
        self.assertEqual(2, utils.makeDefaultVehicleAttributeFactors.call_count)
        self.assertEqual((0, 1), factors['_aspects'])

    def test_non_crew_native_error_does_not_use_the_default_crew(self):
        def update(*unused_args):
            raise Exception('missing vehicle structure')

        utils, modules = self._native_factor_modules()
        descriptor = types.SimpleNamespace(
            type=types.SimpleNamespace(crewRoles=(('commander',),)),
            optionalDevices=[])
        crew = (types.SimpleNamespace(strCD='valid'),)

        with mock.patch.object(loadout, '_client_modules',
                               return_value=modules), \
                mock.patch.object(
                    loadout, '_update_native_attribute_factors_with_split',
                    side_effect=update), \
                mock.patch.object(loadout.sys.stdout, 'write'):
            factors = loadout.attribute_factors(descriptor, crew=crew)

        self.assertIsNone(factors)
        utils.generateDefaultCrew.assert_not_called()

    def test_every_gun_multiplier_comes_from_the_factor_dictionary(self):
        values = loadout.modifiers(
            self._gun_descriptor(), factors=self.FACTORS)

        self.assertTrue(values['from_client_factors'])
        self.assertAlmostEqual(0.875 * 0.8, values['reload_factor'])
        self.assertAlmostEqual(0.89 * 0.9, values['aim_time_factor'])
        self.assertAlmostEqual(0.85, values['dispersion_factor'])
        self.assertAlmostEqual(1.05, values['crew_factor'])
        self.assertAlmostEqual(0.95, values['repair_factor'])
        self.assertAlmostEqual(1.04, values['vehicle_rotation_factor'])
        self.assertAlmostEqual(1.02, values['radio_factor'])
        self.assertEqual((0.9, 0.8, 0.7), values['terrain_resistance_factors'])

    def test_the_stereoscope_is_divided_out_of_the_vision_factor(self):
        descriptor = types.SimpleNamespace(
            optionalDevices=[types.SimpleNamespace(
                name='stereoscope', circularVisionRadiusFactor=1.25,
                activateWhenStillSec=3.0)],
            miscAttrs={'circularVisionRadiusFactor': 1.0},
            type=types.SimpleNamespace(invisibilityDeltas={}))

        profile = loadout.spotting_profile(descriptor, factors=self.FACTORS)

        self.assertAlmostEqual(1.25, profile['binocular_factor'])
        self.assertAlmostEqual(1.21 / 1.25, profile['vision_factor'])
        self.assertAlmostEqual(0.72, profile['camouflage_factor'])

    def test_the_camouflage_net_lives_in_the_stationary_aspect_only(self):
        profile = loadout.spotting_profile(
            _descriptor([]), factors=self.FACTORS)

        self.assertEqual((0.0, 1.0), profile['invisibility_moving'])
        self.assertEqual((0.11, 1.0), profile['invisibility_still'])


class MountedArtefactTests(unittest.TestCase):
    """#1513 ``FittingItem.__eq__`` reads ``other.intCD`` without a guard."""

    class _GuiItem(object):

        def __init__(self):
            self.descriptor = types.SimpleNamespace(
                updateVehicleAttrFactors=lambda *args: None)

        def __eq__(self, other):
            return self.descriptor is other.intCD

        def __ne__(self, other):
            return not self.__eq__(other)

    def test_a_mounted_item_is_never_compared_against_zero(self):
        item = self._GuiItem()

        self.assertIs(item.descriptor, loadout._artefact(item, None))

    def test_an_empty_slot_still_resolves_to_nothing(self):
        self.assertIsNone(loadout._artefact(0, None))
        self.assertIsNone(loadout._artefact(None, None))

    def test_an_intcd_only_gui_item_resolves_through_the_client_cache(self):
        descriptor = types.SimpleNamespace(name='smallRepairkit')
        vehicles = types.SimpleNamespace(
            getItemByCompactDescr=mock.Mock(return_value=descriptor))

        self.assertIs(
            descriptor,
            loadout._artefact(types.SimpleNamespace(intCD=441), vehicles))
        vehicles.getItemByCompactDescr.assert_called_once_with(441)


if __name__ == '__main__':
    unittest.main()
