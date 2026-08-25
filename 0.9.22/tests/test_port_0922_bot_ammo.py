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



if __name__ == '__main__':
    unittest.main()
