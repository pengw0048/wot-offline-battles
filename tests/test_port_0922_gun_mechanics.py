import math
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.gun_mechanics import GunState


class _Strict1513Component(object):
    """Attribute-only stand-in for #1513's ``NoLegacyStuff`` mixin."""

    def __init__(self, **values):
        self.__dict__.update(values)

    def _forbidden(self, *unused_args, **unused_kwargs):
        raise AssertionError('Operation is not allowed')

    get = _forbidden
    __contains__ = _forbidden
    __getitem__ = _forbidden
    __iter__ = _forbidden
    items = _forbidden
    keys = _forbidden
    values = _forbidden


class _Vector(object):
    def __init__(self, x, y, z):
        self.x, self.y, self.z = float(x), float(y), float(z)

    def normalise(self):
        length = math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)
        self.x /= length
        self.y /= length
        self.z /= length


def _descriptor(max_ammo=100, clip=(3, 1.0)):
    shells = [types.SimpleNamespace(compactDescr=index + 1)
              for index in range(3)]
    shots = [types.SimpleNamespace(shell=shell) for shell in shells]
    gun = types.SimpleNamespace(
        shots=shots, maxAmmo=max_ammo, clip=clip, reloadTime=6.0,
        aimingTime=2.0, shotDispersionAngle=0.12,
        shotDispersionFactors={'afterShot': 1.5, 'turretRotation': 0.3})
    return types.SimpleNamespace(
        gun=gun, turret=types.SimpleNamespace(maxAmmo=max_ammo),
        chassis={'shotDispersionFactors': (0.2, 0.4)},
        activeGunShotIndex=0)


class GunMechanicsParityTests(unittest.TestCase):

    def test_siege_descriptor_refresh_preserves_live_ammo_and_reload(self):
        state = GunState(
            _descriptor(clip=(1, 1.0)),
            loadout_modifiers={
                'dispersion_factor': 0.8,
                'aim_time_factor': 0.9,
                'reload_factor': 0.75,
            },
            ammo_layout={1: 20, 2: 10, 3: 5})
        state.shot_index = 1
        state.pending_index = 2
        state.ammo = [19, 8, 4]
        state.clip = 0
        state.reload_time = 2.25
        state.reload_duration = 4.5
        state.dispersion = 0.42
        siege_descriptor = _descriptor(clip=(1, 0.8))
        siege_descriptor.gun.shotDispersionAngle = 0.08
        siege_descriptor.gun.shotDispersionFactors['afterShot'] = 1.2
        siege_descriptor.gun.aimingTime = 1.4
        siege_descriptor.gun.reloadTime = 4.0

        self.assertTrue(state.adopt_descriptor(siege_descriptor))

        self.assertEqual((1, 2), (state.shot_index, state.pending_index))
        self.assertEqual([19, 8, 4], state.ammo)
        self.assertEqual(0, state.clip)
        self.assertEqual(2.25, state.reload_time)
        self.assertEqual(4.5, state.reload_duration)
        self.assertEqual(0.42, state.dispersion)
        self.assertAlmostEqual(0.08 * 0.8, state.base_dispersion)
        self.assertAlmostEqual(1.4 * 0.9, state.aim_time)
        self.assertAlmostEqual(4.0 * 0.75, state.reload)
        self.assertAlmostEqual(0.8, state.clip_reload)

    def test_siege_descriptor_rejects_an_ammunition_contract_change(self):
        state = GunState(_descriptor())
        siege_descriptor = _descriptor()
        siege_descriptor.gun.shots[0].shell.compactDescr = 99

        with self.assertRaisesRegex(RuntimeError, 'ammunition contract'):
            state.adopt_descriptor(siege_descriptor)

    def test_dispersion_reads_native_1513_chassis_attributes(self):
        descriptor = _descriptor()
        descriptor.chassis = _Strict1513Component(
            shotDispersionFactors=(0.2, 0.4))
        state = GunState(descriptor)

        state.tick(
            0.1, True, 2.0, 3.0, 4.0, descriptor)

        target = state.base_dispersion * math.sqrt(
            1.0 + (2.0 * 0.2) ** 2 + (3.0 * 0.4) ** 2 +
            (4.0 * 0.3) ** 2)
        expected = state.base_dispersion + (
            target - state.base_dispersion) * 0.2
        self.assertAlmostEqual(expected, state.dispersion)

    def test_descriptor_state_preserves_082_fallback_ammo_and_crew_factor(self):
        state = GunState(_descriptor())
        crew_multiplier = 1.0 / (0.57 + 0.0043 * 110.0)

        self.assertEqual([60, 30, 10], state.ammo)
        self.assertAlmostEqual(0.12 * crew_multiplier,
                               state.base_dispersion)
        self.assertAlmostEqual(6.0 * crew_multiplier, state.reload)
        self.assertEqual(0, state.clip)
        self.assertAlmostEqual(state.reload, state.reload_time)

    def test_explicit_empty_client_ammo_never_becomes_synthetic_rounds(self):
        state = GunState(_descriptor(), ammo_layout={})

        self.assertEqual([0, 0, 0], state.ammo)
        self.assertEqual(0, state.clip)
        self.assertFalse(state.can_fire(True))

    def test_mismatched_client_ammo_fails_instead_of_becoming_synthetic(self):
        with self.assertRaisesRegex(
                RuntimeError, 'does not match the installed gun'):
            GunState(_descriptor(), ammo_layout={999: 40})

    def test_client_contract_replaces_worker_local_shot_shape(self):
        state = GunState(_descriptor())
        contract = {
            'clip_size': 2,
            'shots': [
                {'compact_descr': 101, 'source_shot': {'speed': 700.0}},
                {'compact_descr': 102, 'source_shot': {'speed': 900.0}},
            ],
        }

        self.assertTrue(state.bind_client_contract(
            contract, {101: 12, 102: 7}))

        self.assertEqual(2, state.clip_size)
        self.assertEqual([12, 7], state.ammo)
        self.assertEqual((700.0, 900.0), tuple(
            shot['speed'] for shot in state.shots))

    def test_reload_does_not_advance_during_countdown(self):
        descriptor = _descriptor()
        state = GunState(descriptor)
        pending = state.reload_time

        state.tick(5.0, False, 0.0, 0.0, 0.0, descriptor)
        self.assertEqual(pending, state.reload_time)
        self.assertEqual(0, state.clip)

        state.tick(pending, True, 0.0, 0.0, 0.0, descriptor)
        self.assertEqual(0.0, state.reload_time)
        self.assertEqual(3, state.clip)

    def test_after_shot_bloom_and_full_reload_factor_match_082(self):
        descriptor = _descriptor(clip=(1, 2.0))
        state = GunState(descriptor)
        state.reload_time = 0.0
        state.clip = 1
        before = state.dispersion

        self.assertTrue(state.commit_fire(2.0))
        jump = state.base_dispersion * state.after_shot
        self.assertAlmostEqual(
            math.sqrt(before * before + jump * jump), state.dispersion)
        self.assertAlmostEqual(state.reload * 2.0, state.reload_time)
        self.assertAlmostEqual(state.reload_time, state.reload_duration)

    def test_intra_clip_interval_ignores_reload_factors(self):
        state = GunState(
            _descriptor(clip=(3, 1.0)),
            loadout_modifiers={'reload_factor': 0.5})
        state.reload_time = 0.0
        state.clip = 3

        self.assertTrue(state.commit_fire(2.0))

        self.assertEqual(2, state.clip)
        self.assertEqual(1.0, state.clip_reload)
        self.assertEqual(1.0, state.reload_time)
        self.assertEqual(1.0, state.reload_duration)

    def test_manual_shell_change_empties_clip_for_full_reload(self):
        state = GunState(_descriptor())
        state.clip = 3
        state.reload_time = 0.0

        self.assertTrue(state.sync_shell_index(1))
        self.assertEqual(1, state.shot_index)
        self.assertEqual(0, state.clip)
        self.assertAlmostEqual(state.reload, state.reload_time)

    def test_partial_clip_reload_empties_the_cassette_for_full_reload(self):
        state = GunState(_descriptor())
        state.clip = 2
        state.reload_time = state.clip_reload
        state.reload_duration = state.clip_reload

        self.assertTrue(state.reload_partial_clip())

        self.assertEqual(0, state.clip)
        self.assertEqual(state.reload, state.reload_time)
        self.assertEqual(state.reload, state.reload_duration)

    def test_partial_clip_reload_promotes_a_queued_shell(self):
        state = GunState(
            _descriptor(), ammo_layout={1: 20, 2: 10, 3: 5})
        state.clip = state.clip_size
        state.reload_time = 0.0
        state.pending_index = 1

        self.assertTrue(state.reload_partial_clip())

        self.assertEqual(1, state.shot_index)
        self.assertIsNone(state.pending_index)
        self.assertEqual(0, state.clip)
        self.assertEqual(state.reload, state.reload_time)

    def test_partial_clip_reload_does_not_restart_an_empty_cycle(self):
        state = GunState(_descriptor())
        state.clip = 0
        state.reload_time = 2.0
        state.reload_duration = state.reload

        self.assertFalse(state.reload_partial_clip())

        self.assertEqual(2.0, state.reload_time)

    def test_scatter_uses_legacy_two_sigma_barrel_plane_distribution(self):
        state = GunState(_descriptor())
        calls = []

        def gauss(mean, sigma):
            calls.append((mean, sigma))
            return sigma

        direction = state.scatter(_Vector(0.0, 0.0, 1.0), gauss=gauss)

        self.assertEqual(1, len(calls))
        self.assertTrue(all(
            abs(sigma - state.dispersion / 2.0) < 1e-12
            for unused_mean, sigma in calls))
        self.assertAlmostEqual(1.0, math.sqrt(
            direction.x ** 2 + direction.y ** 2 + direction.z ** 2))

    def test_scatter_accepts_native_1513_reticle_angle(self):
        state = GunState(_descriptor())
        calls = []

        def gauss(mean, sigma):
            calls.append((mean, sigma))
            return 0.0

        state.scatter(
            _Vector(0.0, 0.0, 1.0), gauss=gauss,
            dispersion_angle=0.03)

        self.assertEqual([(0.0, 0.015)], calls)

    def test_outlying_normal_sample_is_redistributed_inside_aiming_circle(self):
        state = GunState(_descriptor())
        direction = _Vector(0.0, 0.0, 1.0)
        draws = iter((0.25, 0.0))

        state.scatter(
            direction,
            gauss=lambda unused_mean, sigma: sigma * 3.0,
            uniform=lambda unused_low, unused_high: next(draws))

        deviation = math.acos(max(-1.0, min(1.0, direction.z)))
        self.assertAlmostEqual(state.dispersion * 0.25, deviation)
        self.assertLessEqual(deviation, state.dispersion)

    def test_scatter_boundary_holds_for_an_arbitrary_barrel_direction(self):
        state = GunState(_descriptor())
        original = _Vector(1.0, 2.0, 3.0)
        original.normalise()
        direction = _Vector(original.x, original.y, original.z)
        draws = iter((1.0, math.pi * 0.5))

        state.scatter(
            direction,
            gauss=lambda unused_mean, sigma: sigma * 4.0,
            uniform=lambda unused_low, unused_high: next(draws))

        dot = (original.x * direction.x + original.y * direction.y +
               original.z * direction.z)
        deviation = math.acos(max(-1.0, min(1.0, dot)))
        self.assertAlmostEqual(state.dispersion, deviation)

    def _loaded_state(self):
        state = GunState(_descriptor(clip=(1, 1.0)),
                         ammo_layout={1: 20, 2: 10, 3: 5})
        state.reload_time = 0.0
        state.clip = 1
        return state

    def test_next_shell_waits_for_the_loaded_round(self):
        state = self._loaded_state()

        self.assertFalse(state.request_shell_index(1))
        self.assertEqual(0, state.shot_index)
        self.assertEqual(1, state.pending_index)

        state.commit_fire()

        self.assertEqual(1, state.shot_index)
        self.assertIsNone(state.pending_index)
        self.assertEqual(state.reload, state.reload_time)
        self.assertEqual(0, state.clip)
        self.assertEqual([19, 10, 5], state.ammo)

    def test_a_first_press_mid_reload_only_queues_the_next_shell(self):
        """#1513 sends NEXT_SHELLS on the first press whatever the gun does."""
        state = self._loaded_state()
        state.clip = 0
        state.reload_time = 3.0

        self.assertFalse(state.request_shell_index(2))
        self.assertEqual(0, state.shot_index)
        self.assertEqual(2, state.pending_index)
        # The round in progress keeps its remaining time.
        self.assertEqual(3.0, state.reload_time)

    def test_a_queued_shell_loads_when_the_current_type_is_empty(self):
        state = self._loaded_state()
        state.ammo[0] = 0
        state.clip = 0
        state.reload_time = 3.0

        self.assertTrue(state.request_shell_index(2))
        self.assertEqual(2, state.shot_index)
        self.assertIsNone(state.pending_index)
        self.assertEqual(state.reload, state.reload_time)

    def test_a_switch_now_restarts_the_reload_from_zero(self):
        state = self._loaded_state()
        state.reload_time = 1.0
        state.reload_duration = 4.0

        self.assertTrue(state.sync_shell_index(2))
        self.assertEqual(0, state.clip)
        self.assertEqual(state.reload, state.reload_time)
        self.assertEqual(state.reload, state.reload_duration)

    def test_loader_intuition_swaps_without_a_reload(self):
        state = self._loaded_state()

        self.assertTrue(state.sync_shell_index(2, instant=True))
        self.assertEqual(2, state.shot_index)
        self.assertEqual(0.0, state.reload_time)
        self.assertEqual(1, state.clip)
        self.assertTrue(state.can_fire(True))

    def test_loader_intuition_cannot_load_an_empty_shell_type(self):
        state = GunState(_descriptor(clip=(1, 1.0)),
                         ammo_layout={1: 20, 2: 0, 3: 5})
        state.reload_time = 0.0
        state.clip = 1

        self.assertTrue(state.sync_shell_index(1, instant=True))
        self.assertEqual(0, state.clip)
        self.assertEqual(state.reload, state.reload_time)

    def test_selecting_the_current_shell_cancels_a_queued_one(self):
        state = self._loaded_state()
        state.request_shell_index(1)

        self.assertFalse(state.request_shell_index(0))
        self.assertIsNone(state.pending_index)

        state.commit_fire()
        self.assertEqual(0, state.shot_index)

    def test_a_queued_shell_without_ammunition_is_dropped(self):
        state = GunState(_descriptor(clip=(1, 1.0)),
                         ammo_layout={1: 20, 2: 0, 3: 5})
        state.reload_time = 0.0
        state.clip = 1
        state.request_shell_index(1)
        self.assertIsNone(state.pending_index)

        state.commit_fire()

        self.assertEqual(0, state.shot_index)
        self.assertIsNone(state.pending_index)


if __name__ == '__main__':
    unittest.main()
