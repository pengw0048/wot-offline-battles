import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODS = ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods'


def _stub_package():
    """gunnery.py imports its sibling by the client package path."""
    for name in ('gui', 'gui.mods', 'gui.mods.offline_battle_2312'):
        sys.modules.setdefault(name, types.ModuleType(name))
    module = types.ModuleType('gui.mods.offline_battle_2312.projectiles')
    module.ProjectileRunner = object
    sys.modules['gui.mods.offline_battle_2312.projectiles'] = module


_stub_package()
_spec = importlib.util.spec_from_file_location(
    'offline_battle_gunnery', MODS / 'offline_battle_2312' / 'gunnery.py')
gunnery = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gunnery)


class _Shell(object):
    def __init__(self, item_id, kind_idx):
        self.id = (0, item_id)
        self.kindIdx = kind_idx


class _Shot(object):
    def __init__(self, item_id, kind_idx):
        self.shell = _Shell(item_id, kind_idx)


class _Gun(object):
    """Shaped like a 2.3.1.2 gun descriptor."""

    def __init__(self, shot_count=3, max_ammo=50, clip=(1, 0.0)):
        self.shots = [_Shot(index + 1, index) for index in range(shot_count)]
        self.maxAmmo = max_ammo
        self.clip = clip
        self.burst = (1, 0.0)
        self.reloadTime = 4.5


def _make_int_cd(item_type, nation_id, item_id):
    return (nation_id << 8) + (item_id << 4) + {'shell': 1}[item_type]


class AmmoLayoutTests(unittest.TestCase):
    def test_every_shot_type_gets_ammo(self):
        layout = gunnery.ammo_layout(_Gun(), _make_int_cd)
        self.assertEqual(len(layout), 3)
        self.assertEqual(sum(entry[1] for entry in layout), 50)

    def test_the_remainder_goes_to_the_first_shell(self):
        layout = gunnery.ammo_layout(_Gun(shot_count=3, max_ammo=50),
                                     _make_int_cd)
        self.assertEqual([entry[1] for entry in layout], [18, 16, 16])

    def test_the_int_compact_descr_comes_from_the_shell_id(self):
        gun = _Gun(shot_count=1)
        layout = gunnery.ammo_layout(gun, _make_int_cd)
        self.assertEqual(layout[0][0],
                         _make_int_cd('shell', 0, gun.shots[0].shell.id[1]))

    def test_a_clip_gun_reports_the_clip_size(self):
        layout = gunnery.ammo_layout(_Gun(shot_count=1, max_ammo=40,
                                          clip=(6, 2.0)), _make_int_cd)
        self.assertEqual(layout[0][2], 6)

    def test_the_clip_never_exceeds_the_carried_rounds(self):
        layout = gunnery.ammo_layout(_Gun(shot_count=1, max_ammo=4,
                                          clip=(6, 2.0)), _make_int_cd)
        self.assertEqual(layout[0][2], 4)

    def test_a_gun_without_shots_publishes_nothing(self):
        gun = _Gun()
        gun.shots = []
        self.assertEqual(gunnery.ammo_layout(gun, _make_int_cd), [])


if __name__ == '__main__':
    unittest.main()
