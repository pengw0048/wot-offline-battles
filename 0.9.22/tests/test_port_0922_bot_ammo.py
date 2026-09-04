import importlib.util
import math
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[2]
PORT_ROOT = ROOT / '0.9.22'
PACKAGE_ROOT = (PORT_ROOT / 'src' / 'res' / 'scripts' / 'client' /
                'gui' / 'mods' / 'offline_lan_0922')
sys.path.insert(0, str(PORT_ROOT / 'server'))

from lan_battle_server import BattleState  # noqa: E402
from server_bot_ai import BotPlanner  # noqa: E402
from effective_params_fixture import bot_default_crew_factors  # noqa: E402


def _load_bot_runtime():
    for name, path in (
            ('gui', PACKAGE_ROOT.parents[2]),
            ('gui.mods', PACKAGE_ROOT.parents[1]),
            ('gui.mods.offline_lan_0922', PACKAGE_ROOT),
            ('gui.mods.offline_lan_0922.ai', PACKAGE_ROOT / 'ai')):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(path)]
            sys.modules[name] = module
    name = 'gui.mods.offline_lan_0922.bot_runtime'
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        name, PACKAGE_ROOT / 'bot_runtime.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _shell(kind, penetration, damage, speed=900.0):
    return {
        'shell': {
            'kind': kind,
            'piercingPower': (penetration, penetration),
            'damage': (damage, damage),
        },
        'speed': speed,
        'gravity': 9.81,
        'maxDistance': 5000.0,
    }


def _descriptor(max_ammo=60, clip=(1,)):
    shots = (
        _shell('ARMOR_PIERCING', 180.0, 300.0),
        _shell('ARMOR_PIERCING_CR', 260.0, 300.0, 1100.0),
        _shell('HIGH_EXPLOSIVE', 60.0, 420.0, 700.0),
    )
    gun = types.SimpleNamespace(
        shots=shots, maxAmmo=max_ammo, reloadTime=1.0, clip=clip,
        shotDispersionAngle=0.03)
    return types.SimpleNamespace(
        gun=gun, turret=types.SimpleNamespace(maxAmmo=max_ammo),
        maxAmmo=max_ammo)


def _profile(class_tag='mediumTank'):
    return {
        'class_tag': class_tag,
        'shells': [
            {'index': 0, 'kind': 'ARMOR_PIERCING',
             'penetration': 180.0, 'damage': 300.0, 'speed': 900.0},
            {'index': 1, 'kind': 'ARMOR_PIERCING_CR',
             'penetration': 260.0, 'damage': 300.0, 'speed': 1100.0},
            {'index': 2, 'kind': 'HIGH_EXPLOSIVE',
             'penetration': 60.0, 'damage': 420.0, 'speed': 700.0},
        ],
    }


class BotAmmunitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime_module = _load_bot_runtime()
        cls._attribute_factors = cls.runtime_module.loadout.attribute_factors
        cls.runtime_module.loadout.attribute_factors = \
            bot_default_crew_factors

    @classmethod
    def tearDownClass(cls):
        cls.runtime_module.loadout.attribute_factors = cls._attribute_factors

    def test_real_capacity_uses_fixed_three_two_one_inventory(self):
        ammo = self.runtime_module._BotAmmoState(
            _descriptor(60), _profile())

        self.assertEqual([30, 20, 10], ammo.remaining)
        self.assertEqual(60, sum(ammo.remaining))
        self.assertEqual((0, 0), (ammo.loaded, ammo.next))

    def test_artillery_inventory_is_explosive_led(self):
        ammo = self.runtime_module._BotAmmoState(
            _descriptor(60), _profile('SPG'))

        self.assertEqual([10, 10, 40], ammo.remaining)
        self.assertEqual(60, sum(ammo.remaining))

    def test_loaded_round_fires_before_planned_round_is_promoted(self):
        descriptor = _descriptor(6)
        ammo = self.runtime_module._BotAmmoState(descriptor, _profile())
        gun = self.runtime_module._BotGunState(descriptor)
        runtime = self.runtime_module.BotRuntime(1)
        runtime.round_id = 7
        state = {
            'id': 11, 'fire_seq': 0, 'shell_index': 0,
            'aim_yaw': 0.0, 'gun_pitch': 0.0,
            'critical': {}, 'profile': _profile(),
        }
        gun.elapsed = 10.0
        ammo.stage(1, gun.ready())
        ammo.stage(2, gun.ready())
        ammo.publish(state)

        self.assertTrue(runtime._fire(
            state, gun, 1.0, descriptor, ammo_state=ammo))

        self.assertEqual(1, state['fire_seq'])
        self.assertEqual(0, state['shell_index'])
        self.assertEqual(1, state['next_shell_index'])
        self.assertEqual([2, 2, 1], state['ammo_remaining'])
        self.assertTrue(state['ammo_reload_pending'])
        self.assertFalse(ammo.can_fire())

        gun.tick(1.01)
        ammo.stage(2, gun.ready())
        ammo.publish(state)
        self.assertEqual((1, 2), (
            state['shell_index'], state['next_shell_index']))
        self.assertFalse(state['ammo_reload_pending'])

    def test_autoloader_promotes_planned_shell_only_after_full_reload(self):
        descriptor = _descriptor(6, clip=(3, 0.2))
        ammo = self.runtime_module._BotAmmoState(descriptor, _profile())
        gun = self.runtime_module._BotGunState(descriptor)
        gun.elapsed = 10.0

        boundary = gun.complete_reload()
        ammo.stage(1, boundary is not None, boundary == 'full')
        self.assertTrue(gun.fire())
        self.assertTrue(ammo.consume_loaded())
        self.assertEqual((0, 1, 2), (ammo.loaded, ammo.next, gun.clip))

        gun.tick(0.21)
        boundary = gun.complete_reload()
        ammo.stage(2, boundary is not None, boundary == 'full')
        self.assertEqual('intra', boundary)
        self.assertEqual((0, 2, 2), (ammo.loaded, ammo.next, gun.clip))
        self.assertTrue(ammo.can_fire())

        self.assertTrue(gun.fire())
        self.assertTrue(ammo.consume_loaded())
        gun.tick(0.21)
        boundary = gun.complete_reload()
        ammo.stage(1, boundary is not None, boundary == 'full')
        self.assertEqual((0, 1, 1), (ammo.loaded, ammo.next, gun.clip))

        self.assertTrue(gun.fire())
        self.assertTrue(ammo.consume_loaded())
        self.assertEqual((0, 'full'), (gun.clip, gun.reload_kind))
        gun.tick(1.01)
        boundary = gun.complete_reload()
        ammo.stage(2, boundary is not None, boundary == 'full')
        self.assertEqual('full', boundary)
        self.assertEqual((1, 2, 3), (ammo.loaded, ammo.next, gun.clip))

    def test_autoloader_exhausted_shell_discards_clip_for_full_reload(self):
        descriptor = _descriptor(6, clip=(3, 0.2))
        ammo = self.runtime_module._BotAmmoState(descriptor, _profile())
        gun = self.runtime_module._BotGunState(descriptor)
        runtime = self.runtime_module.BotRuntime(1)
        runtime.round_id = 7
        ammo.remaining = [1, 2, 3]
        state = {
            'id': 11, 'fire_seq': 0, 'shell_index': 0,
            'aim_yaw': 0.0, 'gun_pitch': 0.0,
            'critical': {}, 'profile': _profile(),
        }
        gun.elapsed = 10.0

        self.assertTrue(runtime._fire(
            state, gun, 1.0, descriptor, ammo_state=ammo))
        self.assertEqual((0, 'full'), (gun.clip, gun.reload_kind))
        self.assertEqual((0, 1, [0, 2, 3]), (
            ammo.loaded, ammo.next, ammo.remaining))

        gun.tick(1.01)
        boundary = gun.complete_reload(1.0, ammo.planned_rounds())
        ammo.stage(2, boundary is not None, boundary == 'full')
        self.assertEqual('full', boundary)
        self.assertEqual((1, 2, 2), (ammo.loaded, ammo.next, gun.clip))
        self.assertTrue(ammo.can_fire())

    def test_takeover_restores_inventory_without_consuming_again(self):
        ammo = self.runtime_module._BotAmmoState(
            _descriptor(60), _profile(), {
                'fire_seq': 1, 'shell_index': 0,
                'next_shell_index': 1,
                'ammo_remaining': [29, 20, 10],
                'ammo_reload_pending': True,
            })

        self.assertEqual([29, 20, 10], ammo.remaining)
        self.assertFalse(ammo.can_fire())
        ammo.stage(2, False)
        self.assertEqual([29, 20, 10], ammo.remaining)
        self.assertEqual((0, 1), (ammo.loaded, ammo.next))

        ammo.stage(2, True)
        self.assertEqual([29, 20, 10], ammo.remaining)
        self.assertEqual((1, 2), (ammo.loaded, ammo.next))

    def test_takeover_of_completed_reload_does_not_skip_loaded_round(self):
        ammo = self.runtime_module._BotAmmoState(
            _descriptor(60), _profile(), {
                'fire_seq': 1, 'shell_index': 1,
                'next_shell_index': 2,
                'ammo_remaining': [29, 20, 10],
                'ammo_reload_pending': False,
            })

        self.assertTrue(ammo.can_fire())
        ammo.stage(0, True)
        self.assertEqual((1, 2), (ammo.loaded, ammo.next))
        self.assertFalse(ammo.reload_pending)

    def test_restore_requires_boolean_atomic_reload_state(self):
        snapshot = {
            'fire_seq': 1, 'shell_index': 0,
            'next_shell_index': 1,
            'ammo_remaining': [29, 20, 10],
        }
        with self.assertRaisesRegex(
                ValueError, 'snapshot is incomplete'):
            self.runtime_module._BotAmmoState(
                _descriptor(60), _profile(), snapshot)
        snapshot['ammo_reload_pending'] = 1
        with self.assertRaisesRegex(
                ValueError, 'reload state is invalid'):
            self.runtime_module._BotAmmoState(
                _descriptor(60), _profile(), snapshot)

    def test_ready_restore_rejects_exhausted_loaded_round(self):
        snapshot = {
            'fire_seq': 1, 'shell_index': 0,
            'next_shell_index': 1,
            'ammo_remaining': [0, 20, 10],
            'ammo_reload_pending': False,
        }
        with self.assertRaisesRegex(
                ValueError, 'loaded ammunition is exhausted'):
            self.runtime_module._BotAmmoState(
                _descriptor(60), _profile(), snapshot)

        snapshot['ammo_reload_pending'] = True
        ammo = self.runtime_module._BotAmmoState(
            _descriptor(60), _profile(), snapshot)
        self.assertFalse(ammo.can_fire())

    def test_server_plans_standard_he_and_premium_and_skips_depleted(self):
        personality = {'aggression': 0.5}
        profile = _profile()

        self.assertEqual(0, BotPlanner._shell_index(
            profile, {'armor': 150.0, 'health': 1000}, personality,
            {'ammo_remaining': [30, 20, 10]}))
        self.assertEqual(2, BotPlanner._shell_index(
            profile, {'armor': 40.0, 'health': 1000}, personality,
            {'ammo_remaining': [30, 20, 10]}))
        self.assertEqual(1, BotPlanner._shell_index(
            profile, {'armor': 210.0, 'health': 1000}, personality,
            {'ammo_remaining': [30, 20, 10]}))
        self.assertEqual(0, BotPlanner._shell_index(
            profile, {'armor': 210.0, 'health': 1000}, personality,
            {'ammo_remaining': [30, 0, 10]}))
        self.assertEqual(0, BotPlanner._shell_index(
            profile, {'armor': 0.0, 'health': 1000}, personality,
            {'ammo_remaining': [30, 20, 10]}))

    def test_server_accepts_only_conserved_shot_decrement(self):
        identity = {
            'id': 11, 'team': 2, 'slot': 0, 'name': 'Bot',
            'vehicle': 'ussr:R11_MS-1', 'max_health': 1000,
            'profile': _profile(),
        }
        previous = BattleState._sanitize_bot_state({
            'id': 11, 'health': 1000, 'alive': True,
            'fire_seq': 0, 'shell_index': 0, 'next_shell_index': 1,
            'ammo_remaining': [30, 20, 10],
            'ammo_reload_pending': False,
        }, identity, None)
        current = BattleState._sanitize_bot_state({
            'id': 11, 'health': 1000, 'alive': True,
            'fire_seq': 1, 'shell_index': 0, 'next_shell_index': 1,
            'ammo_remaining': [29, 20, 10],
            'ammo_reload_pending': True,
        }, identity, previous)

        self.assertTrue(BattleState._validate_bot_ammo_transition(
            previous, current))
        invalid = dict(current, ammo_remaining=[29, 19, 10])
        with self.assertRaisesRegex(
                ValueError, 'inventory is not conserved'):
            BattleState._validate_bot_ammo_transition(previous, invalid)

    def test_server_accepts_planned_fallback_when_shot_exhausts_shell(self):
        identity = {
            'id': 11, 'team': 2, 'slot': 0, 'name': 'Bot',
            'vehicle': 'ussr:R11_MS-1', 'max_health': 1000,
            'profile': _profile(),
        }
        previous = BattleState._sanitize_bot_state({
            'id': 11, 'health': 1000, 'alive': True,
            'fire_seq': 0, 'shell_index': 0, 'next_shell_index': 0,
            'ammo_remaining': [1, 20, 10],
            'ammo_reload_pending': False,
        }, identity, None)
        fallback = BattleState._sanitize_bot_state({
            'id': 11, 'health': 1000, 'alive': True,
            'fire_seq': 1, 'shell_index': 0, 'next_shell_index': 1,
            'ammo_remaining': [0, 20, 10],
            'ammo_reload_pending': True,
        }, identity, previous)

        self.assertTrue(BattleState._validate_bot_ammo_transition(
            previous, fallback))

        still_available = dict(previous)
        still_available['ammo_remaining'] = [2, 20, 10]
        consumed = dict(fallback, ammo_remaining=[1, 20, 10])
        with self.assertRaisesRegex(
                ValueError, 'planned shell changed outside reload'):
            BattleState._validate_bot_ammo_transition(
                still_available, consumed)

        last_round = dict(
            previous, shell_index=2, next_shell_index=2,
            burst_shell_index=2, ammo_remaining=[0, 0, 1])
        empty = dict(
            fallback, shell_index=2, next_shell_index=0,
            burst_shell_index=2, ammo_remaining=[0, 0, 0])
        self.assertTrue(BattleState._validate_bot_ammo_transition(
            last_round, empty))

    def test_server_accepts_initial_reload_planning_boundaries(self):
        previous = {
            'fire_seq': 0, 'shell_index': 0, 'next_shell_index': 0,
            'ammo_remaining': [2, 2, 1],
            'ammo_reload_pending': False,
            'clip': 1, 'clip_size': 1,
            'reload_time': 0.1, 'reload_duration': 1.0,
        }
        ready = dict(
            previous, next_shell_index=1,
            reload_time=0.0, reload_duration=1.0)
        self.assertTrue(BattleState._validate_bot_ammo_transition(
            previous, ready))

        fired = dict(
            previous, fire_seq=1, next_shell_index=1,
            ammo_remaining=[1, 2, 1],
            ammo_reload_pending=True,
            reload_time=1.0, reload_duration=1.0)
        self.assertTrue(BattleState._validate_bot_ammo_transition(
            previous, fired))

        not_ready = dict(ready, reload_time=0.05)
        with self.assertRaisesRegex(
                ValueError, 'planned shell changed outside reload'):
            BattleState._validate_bot_ammo_transition(
                previous, not_ready)

    def test_server_accepts_autoloader_shell_exhaustion_full_reload(self):
        previous = {
            'fire_seq': 0, 'shell_index': 0, 'next_shell_index': 0,
            'ammo_remaining': [1, 2, 3],
            'ammo_reload_pending': False,
            'clip': 3, 'clip_size': 3,
            'reload_time': 0.0, 'reload_duration': 0.2,
        }
        pending = dict(
            previous, fire_seq=1, next_shell_index=1,
            ammo_remaining=[0, 2, 3],
            ammo_reload_pending=True,
            clip=0, reload_time=1.0, reload_duration=1.0)
        self.assertTrue(BattleState._validate_bot_ammo_transition(
            previous, pending))

        ready = dict(
            pending, shell_index=1, next_shell_index=2,
            ammo_reload_pending=False,
            clip=2, reload_time=0.0, reload_duration=1.0)
        self.assertTrue(BattleState._validate_bot_ammo_transition(
            pending, ready))

    def test_server_accepts_only_explicit_reload_boundary_promotion(self):
        identity = {
            'id': 11, 'team': 2, 'slot': 0, 'name': 'Bot',
            'vehicle': 'ussr:R11_MS-1', 'max_health': 1000,
            'profile': _profile(),
        }
        pending = BattleState._sanitize_bot_state({
            'id': 11, 'health': 1000, 'alive': True,
            'fire_seq': 1, 'shell_index': 0, 'next_shell_index': 1,
            'ammo_remaining': [29, 20, 10],
            'ammo_reload_pending': True,
            'reload_time': 0.1, 'reload_duration': 1.0,
        }, identity, None)
        ready = BattleState._sanitize_bot_state({
            'id': 11, 'health': 1000, 'alive': True,
            'fire_seq': 1, 'shell_index': 1, 'next_shell_index': 2,
            'ammo_remaining': [29, 20, 10],
            'ammo_reload_pending': False,
        }, identity, pending)

        self.assertTrue(BattleState._validate_bot_ammo_transition(
            pending, ready))
        skipped = dict(ready, shell_index=2)
        with self.assertRaisesRegex(
                ValueError, 'skipped its planned boundary'):
            BattleState._validate_bot_ammo_transition(pending, skipped)

        ready_changed = dict(ready, shell_index=2)
        with self.assertRaisesRegex(
                ValueError, 'outside reload'):
            BattleState._validate_bot_ammo_transition(ready, ready_changed)

    def test_server_accepts_reload_edge_and_fire_in_one_update(self):
        identity = {
            'id': 11, 'team': 2, 'slot': 0, 'name': 'Bot',
            'vehicle': 'ussr:R11_MS-1', 'max_health': 1000,
            'profile': _profile(),
        }
        previous = BattleState._sanitize_bot_state({
            'id': 11, 'health': 1000, 'alive': True,
            'fire_seq': 1, 'shell_index': 0, 'next_shell_index': 1,
            'ammo_remaining': [29, 20, 10],
            'ammo_reload_pending': True,
            'reload_time': 0.1, 'reload_duration': 1.0,
        }, identity, None)
        current = BattleState._sanitize_bot_state({
            'id': 11, 'health': 1000, 'alive': True,
            'fire_seq': 2, 'shell_index': 1, 'next_shell_index': 2,
            'ammo_remaining': [29, 19, 10],
            'ammo_reload_pending': True,
            'reload_time': 1.0, 'reload_duration': 1.0,
        }, identity, previous)

        self.assertTrue(BattleState._validate_bot_ammo_transition(
            previous, current))
        wrong_slot = dict(current, ammo_remaining=[28, 20, 10])
        with self.assertRaisesRegex(
                ValueError, 'inventory is not conserved'):
            BattleState._validate_bot_ammo_transition(previous, wrong_slot)

    def test_server_accepts_partial_clip_reload_and_fire_in_one_update(self):
        previous = {
            'fire_seq': 1, 'shell_index': 0, 'next_shell_index': 1,
            'ammo_remaining': [0, 2, 10],
            'ammo_reload_pending': True,
            'clip': 0, 'clip_size': 3,
            'reload_time': 1.0, 'reload_duration': 1.0,
        }
        current = dict(
            previous, fire_seq=2, shell_index=1, next_shell_index=2,
            ammo_remaining=[0, 1, 10], ammo_reload_pending=True,
            clip=1)

        self.assertTrue(BattleState._validate_bot_ammo_transition(
            previous, current))

    def test_server_keeps_planned_round_stable_during_reload(self):
        identity = {
            'id': 11, 'team': 2, 'slot': 0, 'name': 'Bot',
            'vehicle': 'ussr:R11_MS-1', 'max_health': 1000,
            'profile': _profile(),
        }
        previous = BattleState._sanitize_bot_state({
            'id': 11, 'health': 1000, 'alive': True,
            'fire_seq': 1, 'shell_index': 0, 'next_shell_index': 1,
            'ammo_remaining': [29, 20, 10],
            'ammo_reload_pending': True,
        }, identity, None)
        stable = BattleState._sanitize_bot_state({
            'id': 11, 'health': 1000, 'alive': True,
            'fire_seq': 1, 'shell_index': 0, 'next_shell_index': 1,
            'ammo_remaining': [29, 20, 10],
            'ammo_reload_pending': True,
        }, identity, previous)

        self.assertTrue(BattleState._validate_bot_ammo_transition(
            previous, stable))
        changed = dict(stable, next_shell_index=2)
        with self.assertRaisesRegex(
                ValueError, 'planned shell changed before reload'):
            BattleState._validate_bot_ammo_transition(previous, changed)

    def test_server_distinguishes_intra_clip_and_full_reload_edges(self):
        identity = {
            'id': 11, 'team': 2, 'slot': 0, 'name': 'Bot',
            'vehicle': 'ussr:R11_MS-1', 'max_health': 1000,
            'profile': _profile(),
        }
        initial = BattleState._sanitize_bot_state({
            'id': 11, 'health': 1000, 'alive': True,
            'fire_seq': 0, 'shell_index': 0, 'next_shell_index': 1,
            'ammo_remaining': [30, 20, 10],
            'ammo_reload_pending': False, 'clip': 3, 'clip_size': 3,
        }, identity, None)
        intra_pending = BattleState._sanitize_bot_state({
            'id': 11, 'health': 1000, 'alive': True,
            'fire_seq': 1, 'shell_index': 0, 'next_shell_index': 1,
            'ammo_remaining': [29, 20, 10],
            'ammo_reload_pending': True, 'clip': 2, 'clip_size': 3,
        }, identity, initial)
        self.assertTrue(BattleState._validate_bot_ammo_transition(
            initial, intra_pending))

        intra_ready = BattleState._sanitize_bot_state({
            'id': 11, 'health': 1000, 'alive': True,
            'fire_seq': 1, 'shell_index': 0, 'next_shell_index': 2,
            'ammo_remaining': [29, 20, 10],
            'ammo_reload_pending': False, 'clip': 2, 'clip_size': 3,
        }, identity, intra_pending)
        self.assertTrue(BattleState._validate_bot_ammo_transition(
            intra_pending, intra_ready))
        wrong_loaded = dict(intra_ready, shell_index=1)
        with self.assertRaisesRegex(
                ValueError, 'intra-clip reload changed'):
            BattleState._validate_bot_ammo_transition(
                intra_pending, wrong_loaded)

        full_pending = BattleState._sanitize_bot_state({
            'id': 11, 'health': 1000, 'alive': True,
            'fire_seq': 2, 'shell_index': 0, 'next_shell_index': 1,
            'ammo_remaining': [28, 20, 10],
            'ammo_reload_pending': True, 'clip': 0, 'clip_size': 3,
            'reload_time': 1.0, 'reload_duration': 1.0,
        }, identity, None)
        full_ready = BattleState._sanitize_bot_state({
            'id': 11, 'health': 1000, 'alive': True,
            'fire_seq': 2, 'shell_index': 1, 'next_shell_index': 2,
            'ammo_remaining': [28, 20, 10],
            'ammo_reload_pending': False, 'clip': 3, 'clip_size': 3,
            'reload_time': 0.0, 'reload_duration': 1.0,
        }, identity, full_pending)
        self.assertTrue(BattleState._validate_bot_ammo_transition(
            full_pending, full_ready))

        full_ready_and_fired = BattleState._sanitize_bot_state({
            'id': 11, 'health': 1000, 'alive': True,
            'fire_seq': 3, 'shell_index': 1, 'next_shell_index': 2,
            'ammo_remaining': [28, 19, 10],
            'ammo_reload_pending': True, 'clip': 2, 'clip_size': 3,
            'reload_time': 0.2, 'reload_duration': 0.2,
        }, identity, full_pending)
        self.assertTrue(BattleState._validate_bot_ammo_transition(
            full_pending, full_ready_and_fired))

    def test_server_rejects_ready_exhausted_loaded_round(self):
        identity = {
            'id': 11, 'team': 2, 'slot': 0, 'name': 'Bot',
            'vehicle': 'ussr:R11_MS-1', 'max_health': 1000,
            'profile': _profile(),
        }
        raw = {
            'id': 11, 'health': 1000, 'alive': True,
            'fire_seq': 1, 'shell_index': 0, 'next_shell_index': 1,
            'ammo_remaining': [0, 20, 10],
            'ammo_reload_pending': False,
        }

        with self.assertRaisesRegex(
                ValueError, 'loaded ammunition is exhausted'):
            BattleState._sanitize_bot_state(raw, identity, None)
        raw['ammo_reload_pending'] = True
        pending = BattleState._sanitize_bot_state(raw, identity, None)
        self.assertTrue(pending['ammo_reload_pending'])


if __name__ == '__main__':
    unittest.main()
