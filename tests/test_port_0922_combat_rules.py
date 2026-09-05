from pathlib import Path
import math
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import combat_rules
from gui.mods.offline_lan_0922 import tank_collision


def _shot(kind='ARMOR_PIERCING', caliber=90.0,
          piercing=(160.0, 120.0), maximum=720.0, damage=240.0,
          explosion_radius=0.0):
    shell = types.SimpleNamespace(
        kind=kind, caliber=caliber, damage=(damage,),
        explosionRadius=explosion_radius)
    return types.SimpleNamespace(
        shell=shell, piercingPower=piercing, maxDistance=maximum)


def _material(armor, vehicle_damage_factor=1.0, **flags):
    values = {
        'armor': armor,
        'vehicleDamageFactor': vehicle_damage_factor,
    }
    values.update(flags)
    return types.SimpleNamespace(**values)


def _collision(distance, angle_cos, material, component='vehicleHull'):
    return types.SimpleNamespace(
        dist=distance, hitAngleCos=angle_cos, matInfo=material,
        compName=component)


class CombatRulesTests(unittest.TestCase):

    def test_p100_p500_use_the_fixed_400_metre_slope(self):
        shot = _shot(piercing=(200.0, 100.0), maximum=900.0)

        self.assertEqual(200.0, combat_rules.range_piercing(shot, 100.0))
        self.assertEqual(150.0, combat_rules.range_piercing(shot, 300.0))
        self.assertEqual(100.0, combat_rules.range_piercing(shot, 500.0))
        self.assertEqual(50.0, combat_rules.range_piercing(shot, 700.0))
        self.assertEqual(0.25, combat_rules.range_piercing(shot, 899.0))
        self.assertEqual(0.0, combat_rules.range_piercing(shot, 900.0))

    def test_range_law_exact_boundaries_and_early_zero(self):
        shot = _shot(piercing=(100.0, -100.0), maximum=720.0)

        self.assertEqual(
            100.0, combat_rules.range_piercing(shot, 100.0 - 1e-6))
        self.assertEqual(100.0, combat_rules.range_piercing(shot, 100.0))
        self.assertLess(
            combat_rules.range_piercing(shot, 100.0 + 1e-6), 100.0)
        self.assertEqual(0.0, combat_rules.range_piercing(shot, 300.0))
        self.assertEqual(0.0, combat_rules.range_piercing(shot, 500.0))

    def test_max_distance_is_a_hard_zero_not_the_p500_endpoint(self):
        shot = _shot(piercing=(200.0, 100.0), maximum=350.0)

        # A short lifetime does not change the P100/P500 slope before cutoff.
        self.assertEqual(150.0, combat_rules.range_piercing(shot, 300.0))
        self.assertAlmostEqual(
            137.50025, combat_rules.range_piercing(shot, 349.999),
            places=5)
        self.assertEqual(0.0, combat_rules.range_piercing(shot, 350.0))
        self.assertEqual(0.0, combat_rules.range_piercing(shot, 500.0))

    def test_penetration_uses_the_same_range_cutoff(self):
        result = combat_rules.penetration(
            _shot(piercing=(200.0, 100.0), maximum=500.0),
            500.0, 1.0, 1.0,
            random_uniform=lambda unused_low, unused_high: 1.0)

        self.assertEqual(1, result[0])
        self.assertEqual(0.0, result[2])

    def test_nominal_piercing_after_obstacles_has_no_random_roll(self):
        shot = _shot(piercing=(100.0, 50.0), maximum=720.0)

        self.assertEqual(
            75.0, combat_rules.nominal_piercing_after_loss(
                shot, 100.0, 25.0))
        self.assertEqual(
            0.0, combat_rules.nominal_piercing_after_loss(
                shot, 500.0, 50.0))

    def test_one_penetration_factor_is_reused_across_range_and_vehicle(self):
        draws = []

        def low_roll(low, high):
            draws.append((low, high))
            return 0.75

        factor = combat_rules.sample_penetration_factor(low_roll)
        self.assertEqual(0.75, factor)
        self.assertEqual([(0.75, 1.25)], draws)
        self.assertEqual(
            30.0, combat_rules.sampled_piercing(
                _shot(piercing=(40.0, 40.0)), 10.0, factor, 0.0))

        hull = types.SimpleNamespace(armor=20.0, vehicleDamageFactor=1.0)
        result = combat_rules.resolve_hull_hit(
            _shot(piercing=(40.0, 40.0)), 10.0,
            (types.SimpleNamespace(
                dist=10.0, hitAngleCos=1.0, matInfo=hull,
                compName='vehicleHull'),),
            pierce_loss=5.0, penetration_factor=factor,
            random_uniform=lambda unused_low, unused_high: self.fail(
                'vehicle resolution must not draw penetration again'))

        self.assertEqual(2, result[0])
        self.assertEqual(25.0, result[2])

    def test_ricochet_base_multiplier_precedes_roll_and_accumulated_loss(self):
        shot = _shot(piercing=(200.0, 200.0))

        result = combat_rules.penetration(
            shot, 50.0, 100.0, 1.0, pierce_loss=20.0,
            penetration_factor=0.8, base_penetration_multiplier=0.75)

        self.assertEqual(2, result[0])
        self.assertEqual(100.0, result[2])
        self.assertEqual(100.0, combat_rules.sampled_piercing(
            shot, 50.0, 0.8, 20.0,
            base_penetration_multiplier=0.75))

    def test_first_ricochet_multiplier_is_shell_kind_specific(self):
        self.assertEqual(
            0.75,
            combat_rules.first_ricochet_penetration_multiplier(
                'ARMOR_PIERCING'))
        self.assertEqual(
            0.75,
            combat_rules.first_ricochet_penetration_multiplier(
                'ARMOR_PIERCING_CR'))
        self.assertEqual(
            1.0,
            combat_rules.first_ricochet_penetration_multiplier(
                'HOLLOW_CHARGE'))
        for shell_kind in ('HIGH_EXPLOSIVE', 'ARMOR_PIERCING_HE', None):
            with self.subTest(shell_kind=shell_kind):
                self.assertIsNone(
                    combat_rules.first_ricochet_penetration_multiplier(
                        shell_kind))

    def test_hull_resolver_draws_one_factor_for_every_layer(self):
        draws = []

        def one_roll(low, high):
            draws.append((low, high))
            return 1.0

        screen = _material(10.0, 0.0)
        hull = _material(50.0)
        result = combat_rules.resolve_hull_hit(
            _shot(piercing=(100.0, 100.0)), 50.0,
            (_collision(5.0, 1.0, screen, 'vehicleChassis'),
             _collision(5.2, 1.0, hull)),
            random_uniform=one_roll)

        self.assertEqual(2, result[0])
        self.assertEqual([(0.75, 1.25)], draws)

    def test_two_caliber_normalization_has_an_exact_boundary(self):
        armor = 60.0
        angle = math.radians(60.0)
        for kind, base_degrees in (
                ('ARMOR_PIERCING', 5.0),
                ('ARMOR_PIERCING_CR', 2.0)):
            with self.subTest(kind=kind, boundary='exact'):
                exact = combat_rules.penetration(
                    _shot(kind=kind, caliber=120.0,
                          piercing=(1000.0, 1000.0)),
                    50.0, armor, math.cos(angle), penetration_factor=1.0)
                expected = armor / math.cos(
                    angle - math.radians(base_degrees))
                self.assertAlmostEqual(expected, exact[1], places=8)
            with self.subTest(kind=kind, boundary='above'):
                caliber = 120.001
                above = combat_rules.penetration(
                    _shot(kind=kind, caliber=caliber,
                          piercing=(1000.0, 1000.0)),
                    50.0, armor, math.cos(angle), penetration_factor=1.0)
                normalized = (math.radians(base_degrees) * 1.4 *
                              caliber / (2.0 * armor))
                expected = armor / math.cos(angle - normalized)
                self.assertAlmostEqual(expected, above[1], places=8)

    def test_three_caliber_no_ricochet_is_strictly_greater(self):
        angle_cos = math.cos(math.radians(75.0))
        for kind in ('ARMOR_PIERCING', 'ARMOR_PIERCING_CR'):
            with self.subTest(kind=kind, boundary='exact'):
                exact = combat_rules.penetration(
                    _shot(kind=kind, caliber=180.0,
                          piercing=(1000.0, 1000.0)),
                    50.0, 60.0, angle_cos, penetration_factor=1.0)
                self.assertEqual(0, exact[0])
            with self.subTest(kind=kind, boundary='above'):
                above = combat_rules.penetration(
                    _shot(kind=kind, caliber=180.001,
                          piercing=(1000.0, 1000.0)),
                    50.0, 60.0, angle_cos, penetration_factor=1.0)
                self.assertNotEqual(0, above[0])

    def test_ap_and_apcr_ricochet_at_exactly_70_degrees(self):
        for kind in ('ARMOR_PIERCING', 'ARMOR_PIERCING_CR'):
            with self.subTest(kind=kind, boundary='below'):
                below = combat_rules.penetration(
                    _shot(kind=kind, caliber=90.0,
                          piercing=(1000.0, 1000.0)),
                    50.0, 60.0, math.cos(math.radians(69.999)),
                    penetration_factor=1.0)
                self.assertNotEqual(0, below[0])
            with self.subTest(kind=kind, boundary='exact'):
                exact = combat_rules.penetration(
                    _shot(kind=kind, caliber=90.0,
                          piercing=(1000.0, 1000.0)),
                    50.0, 60.0, math.cos(math.radians(70.0)),
                    penetration_factor=1.0)
                self.assertEqual(0, exact[0])

    def test_aphe_has_no_normalization_ricochet_or_caliber_rules(self):
        angle = math.radians(75.0)
        result = combat_rules.penetration(
            _shot(kind='ARMOR_PIERCING_HE', caliber=180.0,
                  piercing=(1000.0, 1000.0)),
            50.0, 60.0, math.cos(angle), penetration_factor=1.0)

        self.assertEqual(2, result[0])
        self.assertAlmostEqual(60.0 / math.cos(angle), result[1], places=8)

    def test_heat_does_not_inherit_ap_ricochet_rule(self):
        result = combat_rules.penetration(
            _shot(kind='HOLLOW_CHARGE', piercing=(400.0, 400.0)),
            50.0, 60.0, 0.30,
            random_uniform=lambda unused_low, unused_high: 1.0)

        self.assertEqual(2, result[0])

    def test_heat_ricochet_boundary_is_85_degrees(self):
        shot = _shot(
            kind='HOLLOW_CHARGE', caliber=1000.0,
            piercing=(2000.0, 2000.0))
        below = combat_rules.penetration(
            shot, 50.0, 60.0, math.cos(math.radians(84.999)),
            penetration_factor=1.0)
        exact = combat_rules.penetration(
            shot, 50.0, 60.0, math.cos(math.radians(85.0)),
            penetration_factor=1.0)

        self.assertEqual(2, below[0])
        # HEAT never receives the AP/APCR three-calibre exemption.
        self.assertEqual(0, exact[0])

    def test_material_may_ricochet_can_disable_auto_bounce(self):
        material = _material(60.0, mayRicochet=False)
        result = combat_rules.penetration(
            _shot(caliber=90.0, piercing=(1000.0, 1000.0)),
            50.0, 60.0, math.cos(math.radians(75.0)),
            penetration_factor=1.0, material=material)

        self.assertEqual(2, result[0])

    def test_material_use_hit_angle_makes_the_plate_nominal(self):
        material = _material(60.0, useHitAngle=False)
        result = combat_rules.penetration(
            _shot(caliber=90.0, piercing=(100.0, 100.0)),
            50.0, 60.0, math.cos(math.radians(89.0)),
            penetration_factor=1.0, material=material)

        self.assertEqual(2, result[0])
        self.assertEqual(60.0, result[1])

    def test_material_can_disable_three_caliber_ricochet_check(self):
        material = _material(
            60.0, checkCaliberForRicochet=False)
        result = combat_rules.penetration(
            _shot(caliber=200.0, piercing=(1000.0, 1000.0)),
            50.0, 60.0, math.cos(math.radians(75.0)),
            penetration_factor=1.0, material=material)

        self.assertEqual(0, result[0])

    def test_exact_1513_richet_field_typo_is_honored(self):
        material = _material(60.0, checkCaliberForRichet=False)
        result = combat_rules.penetration(
            _shot(caliber=200.0, piercing=(1000.0, 1000.0)),
            50.0, 60.0, math.cos(math.radians(75.0)),
            penetration_factor=1.0, material=material)

        self.assertEqual(0, result[0])

    def test_exact_1513_richet_typo_wins_over_legacy_alias(self):
        material = _material(
            60.0, checkCaliberForRichet=False,
            checkCaliberForRicochet=True)
        result = combat_rules.penetration(
            _shot(caliber=200.0, piercing=(1000.0, 1000.0)),
            50.0, 60.0, math.cos(math.radians(75.0)),
            penetration_factor=1.0, material=material)

        self.assertEqual(0, result[0])

    def test_material_can_disable_two_caliber_normalization(self):
        material = _material(
            60.0, checkCaliberForHitAngleNorm=False)
        angle = math.radians(60.0)
        result = combat_rules.penetration(
            _shot(caliber=150.0, piercing=(1000.0, 1000.0)),
            50.0, 60.0, math.cos(angle),
            penetration_factor=1.0, material=material)

        expected = 60.0 / math.cos(angle - math.radians(5.0))
        self.assertAlmostEqual(expected, result[1], places=8)

    def test_grazing_effective_armor_uses_exact_1513_cosine_floor(self):
        result = combat_rules.penetration(
            _shot(kind='ARMOR_PIERCING_HE', caliber=90.0,
                  piercing=(50000.0, 50000.0)),
            50.0, 1.0, 0.0, penetration_factor=1.0)

        self.assertEqual(1, result[0])
        self.assertAlmostEqual(100000.0, result[1], places=5)
        self.assertEqual(50000.0, result[2])

    def test_negative_cosine_is_not_reflected_into_a_front_face(self):
        result = combat_rules.penetration(
            _shot(kind='ARMOR_PIERCING_HE', caliber=90.0,
                  piercing=(1000.0, 1000.0)),
            50.0, 60.0, -0.5, penetration_factor=1.0)

        self.assertEqual(1, result[0])
        self.assertAlmostEqual(6000000.0, result[1], places=3)

    def test_ap_normalization_floors_only_the_final_cosine(self):
        material = _material(60.0, mayRicochet=False)
        result = combat_rules.penetration(
            _shot(kind='ARMOR_PIERCING', caliber=90.0,
                  piercing=(1000.0, 1000.0)),
            50.0, 60.0, -0.5, penetration_factor=1.0,
            material=material)

        self.assertEqual(1, result[0])
        self.assertAlmostEqual(6000000.0, result[1], places=3)

    def test_he_non_penetration_uses_1513_common_factors(self):
        shot = _shot(kind='HIGH_EXPLOSIVE', damage=400.0)

        value = combat_rules.damage(
            shot, 1, 100.0,
            random_uniform=lambda low, high: (low + high) * 0.5)

        self.assertEqual(70, value)

    def test_ap_damage_roll_stays_within_twenty_five_percent(self):
        shot = _shot(kind='ARMOR_PIERCING_CR', damage=400.0)

        low = combat_rules.damage(
            shot, 2, 100.0,
            random_uniform=lambda minimum, unused_maximum: minimum)
        high = combat_rules.damage(
            shot, 2, 100.0,
            random_uniform=lambda unused_minimum, maximum: maximum)

        self.assertEqual(300, low)
        self.assertEqual(500, high)

    def test_every_solid_shell_kind_uses_armor_damage_not_module_damage(self):
        for kind in ('ARMOR_PIERCING', 'ARMOR_PIERCING_HE',
                     'ARMOR_PIERCING_CR', 'HOLLOW_CHARGE'):
            with self.subTest(kind=kind):
                shot = _shot(kind=kind, damage=400.0)
                # Exact #1513 stores shell damage as (vehicle HP, module HP).
                # A 165 module roll must never scale a penetrating hull hit.
                shot.shell.damage = (400.0, 165.0)
                low = combat_rules.damage(
                    shot, 2, 100.0,
                    random_uniform=lambda minimum, unused_maximum: minimum)
                high = combat_rules.damage(
                    shot, 2, 100.0,
                    random_uniform=lambda unused_minimum, maximum: maximum)

                self.assertEqual(300, low)
                self.assertEqual(500, high)

    def test_spaced_armour_is_paid_before_structural_plate(self):
        track = _material(20.0, 0.0)
        hull = _material(100.0)
        collisions = (
            _collision(5.0, 0.5, track, 'vehicleChassis'),
            _collision(5.2, 1.0, hull),
        )

        result = combat_rules.resolve_hull_hit(
            _shot(piercing=(120.0, 120.0)), 50.0, collisions,
            random_uniform=lambda unused_low, unused_high: 1.0)

        self.assertEqual(1, result[0])
        # The 90 mm shell triggers the two-calibre normalization rule on the
        # 20 mm external plate before that effective thickness is deducted.
        normalization = math.radians(5.0) * 1.4 * 90.0 / 40.0
        expected = 20.0 / math.cos(math.radians(60.0) - normalization)
        self.assertAlmostEqual(expected, result[3], places=8)

    def test_external_plate_must_itself_be_penetrated(self):
        screen = _material(30.0, 0.0)
        hull = _material(10.0)

        result = combat_rules.resolve_hull_hit(
            _shot(piercing=(20.0, 20.0)), 50.0,
            (_collision(5.0, 1.0, screen, 'vehicleChassis'),
             _collision(5.2, 1.0, hull)),
            penetration_factor=1.0)

        self.assertIsNone(result)

    def test_he_detonates_after_penetrating_spaced_armor(self):
        screen = _material(20.0, 0.0)
        hull = _material(1.0)
        collisions = (
            _collision(5.0, 1.0, screen, 'vehicleChassis'),
            _collision(5.2, 1.0, hull),
        )

        contact = combat_rules.resolve_armor_contact(
            _shot(kind='HIGH_EXPLOSIVE', piercing=(1000.0, 1000.0)),
            50.0, collisions, penetration_factor=1.0)

        self.assertEqual(('external', 1),
                         (contact['layer'], contact['result']))
        self.assertIsNone(combat_rules.resolve_hull_hit(
            _shot(kind='HIGH_EXPLOSIVE', piercing=(1000.0, 1000.0)),
            50.0, collisions, penetration_factor=1.0))

    def test_armor_contact_preserves_external_ricochet(self):
        screen = _material(60.0, 0.0)
        hull = _material(10.0)
        collisions = (
            _collision(
                5.0, math.cos(math.radians(75.0)), screen,
                'vehicleChassis'),
            _collision(5.2, 1.0, hull),
        )

        contact = combat_rules.resolve_armor_contact(
            _shot(piercing=(1000.0, 1000.0)), 50.0, collisions,
            pierce_loss=12.5, penetration_factor=1.0)

        self.assertEqual(0, contact['result'])
        self.assertEqual('external', contact['layer'])
        self.assertEqual(5.0, contact['distance'])
        self.assertIs(screen, contact['material'])
        self.assertEqual(12.5, contact['accumulated_loss'])
        self.assertIsNone(combat_rules.resolve_hull_hit(
            _shot(piercing=(1000.0, 1000.0)), 50.0, collisions,
            pierce_loss=12.5, penetration_factor=1.0))

    def test_armor_contact_preserves_external_non_penetration(self):
        screen = _material(30.0, 0.0)
        hull = _material(10.0)
        collisions = (
            _collision(5.0, 1.0, screen, 'vehicleChassis'),
            _collision(5.2, 1.0, hull),
        )

        contact = combat_rules.resolve_armor_contact(
            _shot(piercing=(20.0, 20.0)), 50.0, collisions,
            pierce_loss=7.0, penetration_factor=1.0)

        self.assertEqual(1, contact['result'])
        self.assertEqual('external', contact['layer'])
        self.assertEqual(5.0, contact['distance'])
        self.assertIs(screen, contact['material'])
        self.assertEqual(7.0, contact['accumulated_loss'])

    def test_armor_contact_keeps_structural_result_legacy_compatible(self):
        track = _material(20.0, 0.0)
        hull = _material(100.0)
        collisions = (
            _collision(5.0, 1.0, track, 'vehicleChassis'),
            _collision(5.2, 1.0, hull),
        )
        shot = _shot(piercing=(120.0, 120.0))

        contact = combat_rules.resolve_armor_contact(
            shot, 50.0, collisions, pierce_loss=5.0,
            penetration_factor=1.0)
        legacy = combat_rules.resolve_hull_hit(
            shot, 50.0, collisions, pierce_loss=5.0,
            penetration_factor=1.0)

        self.assertEqual(1, contact['result'])
        self.assertEqual('structural', contact['layer'])
        self.assertEqual(5.2, contact['distance'])
        self.assertIs(hull, contact['material'])
        self.assertEqual('vehicleHull', contact['component'])
        self.assertEqual(25.0, contact['accumulated_loss'])
        self.assertEqual(
            (contact['result'], contact['effective_armor'],
             contact['piercing'], contact['accumulated_loss'],
             contact['angle_cos']),
            legacy)

    def test_armor_contact_applies_base_multiplier_before_layer_loss(self):
        screen = _material(20.0, 0.0)
        hull = _material(100.0)

        contact = combat_rules.resolve_armor_contact(
            _shot(piercing=(200.0, 200.0)), 50.0,
            (_collision(5.0, 1.0, screen, 'vehicleChassis'),
             _collision(5.2, 1.0, hull, 'vehicleHull')),
            penetration_factor=0.8,
            base_penetration_multiplier=0.75)

        self.assertEqual(('structural', 2, 'vehicleHull'),
                         (contact['layer'], contact['result'],
                          contact['component']))
        self.assertEqual(20.0, contact['accumulated_loss'])
        self.assertEqual(100.0, contact['piercing'])

    def test_zero_thickness_structural_material_is_still_a_contact(self):
        material = _material(0.0)

        contact = combat_rules.resolve_armor_contact(
            _shot(piercing=(100.0, 100.0)), 50.0,
            (_collision(5.0, 1.0, material),),
            penetration_factor=1.0)

        self.assertEqual('structural', contact['layer'])
        self.assertEqual(2, contact['result'])
        self.assertEqual(0.0, contact['effective_armor'])

    def test_external_plate_exactly_exhausting_power_is_terminal(self):
        screen = _material(100.0, 0.0)
        hull = _material(1.0)
        collisions = (
            _collision(5.0, 1.0, screen, 'vehicleChassis'),
            _collision(5.2, 1.0, hull),
        )

        below = combat_rules.resolve_armor_contact(
            _shot(piercing=(99.999, 99.999)), 50.0, collisions,
            penetration_factor=1.0)
        exact = combat_rules.resolve_armor_contact(
            _shot(piercing=(100.0, 100.0)), 50.0, collisions,
            penetration_factor=1.0)
        above = combat_rules.resolve_armor_contact(
            _shot(piercing=(100.001, 100.001)), 50.0, collisions,
            penetration_factor=1.0)

        self.assertEqual(('external', 1),
                         (below['layer'], below['result']))
        self.assertEqual(('external', 1),
                         (exact['layer'], exact['result']))
        self.assertEqual(('structural', 1),
                         (above['layer'], above['result']))

    def test_collide_once_only_deducts_one_copy_of_the_same_plate(self):
        once_entry = _material(
            20.0, 0.0, kind=7, collideOnceOnly=True)
        once_exit = _material(
            20.0, 0.0, kind=7, collideOnceOnly=True)
        repeated = _material(20.0, 0.0, collideOnceOnly=False)
        hull = _material(80.0)

        def resolve(first, second):
            return combat_rules.resolve_hull_hit(
                _shot(piercing=(110.0, 110.0)), 50.0,
                (_collision(5.0, 1.0, first, 'vehicleChassis'),
                 _collision(5.1, 1.0, second, 'vehicleChassis'),
                 _collision(5.2, 1.0, hull)),
                penetration_factor=1.0)

        once_result = resolve(once_entry, once_exit)
        repeated_result = resolve(repeated, repeated)

        self.assertEqual(2, once_result[0])
        self.assertEqual(20.0, once_result[3])
        self.assertEqual(1, repeated_result[0])
        self.assertEqual(40.0, repeated_result[3])

    def test_destructible_loss_accumulates_before_vehicle_spaced_armour(self):
        track = types.SimpleNamespace(armor=20.0, vehicleDamageFactor=0.0)
        hull = types.SimpleNamespace(armor=100.0, vehicleDamageFactor=1.0)
        collisions = (
            types.SimpleNamespace(
                dist=5.0, hitAngleCos=1.0, matInfo=track,
                compName='vehicleChassis'),
            types.SimpleNamespace(
                dist=5.2, hitAngleCos=1.0, matInfo=hull,
                compName='vehicleHull'),
        )

        result = combat_rules.resolve_hull_hit(
            _shot(piercing=(160.0, 160.0)), 50.0, collisions,
            pierce_loss=50.0,
            random_uniform=lambda unused_low, unused_high: 1.0)

        self.assertEqual(1, result[0])
        self.assertEqual(70.0, result[3])
        self.assertEqual(90.0, result[2])

    def test_destructible_penetration_loss_does_not_reduce_damage(self):
        hull = types.SimpleNamespace(armor=50.0, vehicleDamageFactor=1.0)
        collisions = (types.SimpleNamespace(
            dist=5.0, hitAngleCos=1.0, matInfo=hull,
            compName='vehicleHull'),)
        shot = _shot(piercing=(160.0, 160.0), damage=240.0)
        clear = combat_rules.resolve_hull_hit(
            shot, 50.0, collisions,
            random_uniform=lambda unused_low, unused_high: 1.0)
        crossed = combat_rules.resolve_hull_hit(
            shot, 50.0, collisions,
            random_uniform=lambda unused_low, unused_high: 1.0,
            pierce_loss=25.0)

        self.assertEqual(2, clear[0])
        self.assertEqual(2, crossed[0])
        self.assertEqual(
            combat_rules.damage(
                shot, clear[0], 50.0,
                random_uniform=lambda unused_low, unused_high: 1.0),
            combat_rules.damage(
                shot, crossed[0], 50.0,
                random_uniform=lambda unused_low, unused_high: 1.0))

    def test_collision_adapter_rejects_incomplete_1513_result(self):
        collision = types.SimpleNamespace(
            dist=5.0, hitAngleCos=1.0,
            matInfo=types.SimpleNamespace(armor=100.0))

        with self.assertRaises(AttributeError):
            combat_rules.collision_layers((collision,))

    def test_heat_penetrates_external_plate_then_reaches_structure(self):
        track = _material(20.0, 0.0)
        hull = _material(100.0)

        result = combat_rules.resolve_hull_hit(
            _shot(kind='HOLLOW_CHARGE', piercing=(400.0, 400.0)),
            50.0,
            (_collision(5.0, 1.0, track, 'vehicleChassis'),
             _collision(5.2, 1.0, hull)),
            penetration_factor=1.0)

        self.assertEqual(2, result[0])
        # After the 20 mm screen, 380 mm remains. The native jet starts behind
        # that nominal 20 mm, so its 18 cm air gap costs 9% of 380 mm.
        self.assertAlmostEqual(54.2, result[3], places=8)
        self.assertAlmostEqual(345.8, result[2], places=8)

    def test_heat_must_penetrate_the_external_plate(self):
        track = _material(20.0, 0.0)
        hull = _material(1.0)

        result = combat_rules.resolve_hull_hit(
            _shot(kind='HOLLOW_CHARGE', piercing=(19.999, 19.999)),
            50.0,
            (_collision(5.0, 1.0, track, 'vehicleChassis'),
             _collision(5.1, 1.0, hull)),
            penetration_factor=1.0)

        self.assertIsNone(result)

    def test_heat_gap_halves_current_jet_penetration_per_metre(self):
        track = _material(20.0, 0.0)
        hull = _material(95.0)
        shot = _shot(kind='HOLLOW_CHARGE', piercing=(200.0, 200.0))

        short_gap = combat_rules.resolve_hull_hit(
            shot, 50.0,
            (_collision(5.0, 1.0, track, 'vehicleChassis'),
             _collision(5.82, 1.0, hull)),
            penetration_factor=1.0)
        one_metre = combat_rules.resolve_hull_hit(
            shot, 50.0,
            (_collision(5.0, 1.0, track, 'vehicleChassis'),
             _collision(6.02, 1.0, hull)),
            penetration_factor=1.0)

        self.assertEqual(2, short_gap[0])
        self.assertAlmostEqual(92.0, short_gap[3], places=8)
        self.assertAlmostEqual(108.0, short_gap[2], places=8)
        self.assertEqual(1, one_metre[0])
        self.assertAlmostEqual(110.0, one_metre[3], places=8)
        self.assertAlmostEqual(90.0, one_metre[2], places=8)

    def test_heat_charges_gap_before_collide_once_only_deduplication(self):
        screen_entry = _material(
            20.0, 0.0, kind=7, collideOnceOnly=True)
        screen_exit = _material(
            20.0, 0.0, kind=7, collideOnceOnly=True)
        hull = _material(80.0)

        contact = combat_rules.resolve_armor_contact(
            _shot(kind='HOLLOW_CHARGE', piercing=(200.0, 200.0)),
            50.0,
            (_collision(5.0, 1.0, screen_entry, 'vehicleChassis'),
             _collision(5.5, 1.0, screen_exit, 'vehicleChassis'),
             _collision(6.0, 1.0, hull)),
            penetration_factor=1.0)

        self.assertEqual(('structural', 1),
                         (contact['layer'], contact['result']))
        self.assertAlmostEqual(130.232, contact['accumulated_loss'], places=8)
        self.assertAlmostEqual(69.768, contact['piercing'], places=8)

    def test_heat_empty_material_starts_the_jet_gap(self):
        hull = _material(110.0)

        contact = combat_rules.resolve_armor_contact(
            _shot(kind='HOLLOW_CHARGE', piercing=(200.0, 200.0)),
            50.0,
            (_collision(5.0, 1.0, None, 'vehicleGun'),
             _collision(6.0, 1.0, hull)),
            penetration_factor=1.0)

        self.assertEqual(('structural', 1),
                         (contact['layer'], contact['result']))
        self.assertEqual(100.0, contact['accumulated_loss'])
        self.assertEqual(100.0, contact['piercing'])

    def test_heat_zero_thickness_material_starts_the_jet_gap(self):
        screen = _material(0.0, 0.0)
        hull = _material(110.0)

        contact = combat_rules.resolve_armor_contact(
            _shot(kind='HOLLOW_CHARGE', piercing=(200.0, 200.0)),
            50.0,
            (_collision(5.0, 1.0, screen, 'vehicleChassis'),
             _collision(6.0, 1.0, hull)),
            penetration_factor=1.0)

        self.assertEqual(('structural', 1),
                         (contact['layer'], contact['result']))
        self.assertEqual(100.0, contact['accumulated_loss'])
        self.assertEqual(100.0, contact['piercing'])

    def test_heat_jet_does_not_ricochet_after_the_external_plate(self):
        track = _material(20.0, 0.0)
        hull = _material(60.0)

        result = combat_rules.resolve_hull_hit(
            _shot(kind='HOLLOW_CHARGE', caliber=10.0,
                  piercing=(2000.0, 2000.0)),
            50.0,
            (_collision(5.0, 1.0, track, 'vehicleChassis'),
             _collision(5.2, math.cos(math.radians(85.0)), hull)),
            penetration_factor=1.0)

        self.assertEqual(2, result[0])

    def test_he_uses_first_structural_nominal_armour(self):
        track = types.SimpleNamespace(armor=40.0, vehicleDamageFactor=0.0)
        hull = types.SimpleNamespace(armor=75.0, vehicleDamageFactor=1.0)

        armor = combat_rules.he_nominal_armor((
            types.SimpleNamespace(
                dist=2.0, hitAngleCos=1.0, matInfo=track,
                compName='vehicleChassis'),
            types.SimpleNamespace(
                dist=2.5, hitAngleCos=0.5, matInfo=hull,
                compName='vehicleHull'),
        ))

        self.assertEqual(75.0, armor)

    def test_he_splash_interpolates_from_center_to_edge_factor(self):
        shot = _shot(
            kind='HIGH_EXPLOSIVE', damage=400.0,
            explosion_radius=10.0)

        uniform = lambda low, high: (low + high) * 0.5
        center = combat_rules.he_splash_damage(
            shot, 0.0, 0.0, random_uniform=uniform)
        middle = combat_rules.he_splash_damage(
            shot, 50.0, 0.5, random_uniform=uniform)
        edge = combat_rules.he_splash_damage(
            shot, 0.0, 1.0, random_uniform=uniform)

        self.assertTrue(combat_rules.is_he(shot))
        self.assertEqual(10.0, combat_rules.he_radius(shot))
        self.assertEqual(200, center)
        self.assertEqual(65, middle)
        self.assertEqual(60, edge)

    def test_spall_liner_scales_the_he_armour_absorption_term(self):
        shot = _shot(
            kind='HIGH_EXPLOSIVE', damage=400.0, explosion_radius=10.0)
        uniform = lambda low, high: (low + high) * 0.5

        bare = combat_rules.he_splash_damage(
            shot, 50.0, 0.0, random_uniform=uniform)
        lined = combat_rules.he_splash_damage(
            shot, 50.0, 0.0, random_uniform=uniform, spall_coefficient=1.5)
        direct_bare = combat_rules.damage(
            shot, 1, 50.0, random_uniform=uniform)
        direct_lined = combat_rules.damage(
            shot, 1, 50.0, random_uniform=uniform, spall_coefficient=1.5)

        # 400 * 0.5 - 1.3 * 50 * spall
        self.assertEqual(135, bare)
        self.assertEqual(102, lined)
        self.assertEqual(135, direct_bare)
        self.assertEqual(102, direct_lined)

    def test_he_absorption_ignores_an_absent_or_invalid_spall_factor(self):
        shot = _shot(
            kind='HIGH_EXPLOSIVE', damage=400.0, explosion_radius=10.0)
        uniform = lambda low, high: (low + high) * 0.5
        baseline = combat_rules.he_splash_damage(
            shot, 50.0, 0.0, random_uniform=uniform)

        for value in (None, 'x', float('nan'), float('inf'), 0.0, -2.0, 0.5):
            self.assertEqual(baseline, combat_rules.he_splash_damage(
                shot, 50.0, 0.0, random_uniform=uniform,
                spall_coefficient=value), value)

    def test_spall_coefficient_reads_the_1513_descriptor_default(self):
        self.assertEqual(1.0, tank_collision.descriptor_spall_coefficient(
            types.SimpleNamespace(miscAttrs={})))
        self.assertEqual(1.0, tank_collision.descriptor_spall_coefficient(None))
        self.assertEqual(1.5, tank_collision.descriptor_spall_coefficient(
            types.SimpleNamespace(
                miscAttrs={'antifragmentationLiningFactor': 1.5})))
        # A liner never reduces absorption below the no-liner value.
        self.assertEqual(1.0, tank_collision.descriptor_spall_coefficient(
            types.SimpleNamespace(
                miscAttrs={'antifragmentationLiningFactor': 0.4})))

    def test_native_1513_shell_type_overrides_all_he_factors(self):
        shell_type = types.SimpleNamespace(
            name='HIGH_EXPLOSIVE', explosionRadius=10.0,
            explosionDamageFactor=0.6,
            explosionDamageAbsorptionFactor=1.0,
            explosionEdgeDamageFactor=0.2)
        shot = types.SimpleNamespace(
            shell=types.SimpleNamespace(
                type=shell_type, caliber=122.0, damage=(400.0, 90.0)),
            piercingPower=(60.0, 60.0), maxDistance=720.0)

        value = combat_rules.he_splash_damage(
            shot, 50.0, 0.5,
            random_uniform=lambda low, high: (low + high) * 0.5)

        self.assertEqual((0.6, 1.0, 0.2), combat_rules.he_factors(shot))
        self.assertEqual(110, value)

    def test_he_missing_contact_does_not_borrow_descriptor_armor(self):
        material = types.SimpleNamespace(
            armor=35.0, vehicleDamageFactor=1.0)

        class Hull(object):
            materials = {'armor': material}

            def get(self, *unused_args, **unused_kwargs):
                raise AssertionError('Operation is not allowed')

        descriptor = types.SimpleNamespace(hull=Hull())

        self.assertIsNone(combat_rules.he_nominal_armor((), descriptor))

    def test_he_blast_stops_at_first_structural_plate_not_weaker_backside(self):
        first = _material(100.0)
        weaker_backside = _material(10.0)
        result = combat_rules.he_blast_contact(
            _shot(kind='HIGH_EXPLOSIVE', damage=400.0,
                  explosion_radius=10.0),
            (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (10.0, 0.0, 0.0),
            (_collision(2.0, 1.0, weaker_backside),
             _collision(1.0, 1.0, first)), 400.0)

        self.assertIs(first, result['collision'].matInfo)
        self.assertEqual(100.0, result['nominal_armor'])
        self.assertEqual(56, result['damage'])
        self.assertEqual((1.0, 0.0, 0.0), result['point'])

    def test_he_blast_accepts_zero_armour_structural_contact(self):
        collision = _collision(0.0, 1.0, _material(0.0))

        result = combat_rules.he_blast_contact(
            _shot(kind='HIGH_EXPLOSIVE', damage=400.0,
                  explosion_radius=10.0),
            (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (10.0, 0.0, 0.0),
            (collision,), 400.0)

        self.assertEqual(0.0, result['nominal_armor'])
        self.assertEqual(200, result['damage'])
        self.assertEqual((collision,), result['collisions'])

    def test_he_blast_requires_native_structural_evidence(self):
        shot = _shot(kind='HIGH_EXPLOSIVE', damage=400.0,
                     explosion_radius=10.0)
        external = _collision(0.0, 1.0, _material(20.0, 0.0))
        missing_material = _collision(1.0, 1.0, None)

        self.assertIsNone(combat_rules.he_blast_contact(
            shot, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            (10.0, 0.0, 0.0), (external, missing_material), 400.0))
        self.assertIsNone(combat_rules.he_blast_contact(
            shot, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            (10.0, 0.0, 0.0), (), 400.0))

    def test_he_blast_missing_armour_is_not_a_zero_armour_plate(self):
        collision = _collision(1.0, 1.0, types.SimpleNamespace(
            vehicleDamageFactor=1.0))
        self.assertIsNone(combat_rules.he_blast_contact(
            _shot(kind='HIGH_EXPLOSIVE', explosion_radius=4.0),
            (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (4.0, 0.0, 0.0),
            (collision,), 400.0))

    def test_he_blast_uses_structural_point_distance_after_external_screen(self):
        screen = _collision(0.001, 1.0, _material(20.0, 0.0),
                            'vehicleChassis')
        hull = _collision(5.0, 1.0, _material(0.0), 'vehicleHull')
        result = combat_rules.he_blast_contact(
            _shot(kind='HIGH_EXPLOSIVE', damage=400.0,
                  explosion_radius=10.0),
            (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (10.0, 0.0, 0.0),
            (hull, screen), 400.0)

        self.assertEqual(5.0, result['distance'])
        self.assertEqual(130, result['damage'])
        self.assertEqual((screen, hull), result['collisions'])

    def test_he_blast_allows_upstream_query_start_and_tolerance_backstep(self):
        hull = _collision(4.9995, 1.0, _material(0.0))
        result = combat_rules.he_blast_contact(
            _shot(kind='HIGH_EXPLOSIVE', damage=400.0,
                  explosion_radius=10.0),
            (5.0, 0.0, 0.0), (0.0, 0.0, 0.0), (10.0, 0.0, 0.0),
            (hull,), 400.0)

        self.assertAlmostEqual(0.0005, result['distance'])
        self.assertEqual((1.0, 0.0, 0.0), result['direction'])

    def test_he_blast_rejects_structural_hit_outside_radius(self):
        result = combat_rules.he_blast_contact(
            _shot(kind='HIGH_EXPLOSIVE', damage=400.0,
                  explosion_radius=4.0),
            (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (10.0, 0.0, 0.0),
            (_collision(4.001, 1.0, _material(0.0)),), 400.0)

        self.assertIsNone(result)

    def test_he_blast_reuses_caller_roll_without_random_sampling(self):
        original_uniform = combat_rules.random.uniform
        combat_rules.random.uniform = lambda *unused: self.fail(
            'HE contact must use the caller-owned rolled damage')
        try:
            result = combat_rules.he_blast_contact(
                _shot(kind='HIGH_EXPLOSIVE', damage=400.0,
                      explosion_radius=10.0),
                (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
                (10.0, 0.0, 0.0),
                (_collision(0.0, 1.0, _material(0.0)),), 321.0)
        finally:
            combat_rules.random.uniform = original_uniform

        self.assertEqual(160, result['damage'])

    def test_he_blast_material_can_opt_out_of_spall_liner(self):
        lined = _collision(0.0, 1.0, _material(50.0))
        opt_out = _collision(
            0.0, 1.0, _material(50.0, useAntifragmentationLining=False))
        shot = _shot(kind='HIGH_EXPLOSIVE', damage=400.0,
                     explosion_radius=10.0)

        lined_result = combat_rules.he_blast_contact(
            shot, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            (10.0, 0.0, 0.0), (lined,), 400.0, 1.5)
        opt_out_result = combat_rules.he_blast_contact(
            shot, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            (10.0, 0.0, 0.0), (opt_out,), 400.0, 1.5)

        self.assertEqual(102, lined_result['damage'])
        self.assertEqual(135, opt_out_result['damage'])


if __name__ == '__main__':
    unittest.main()
