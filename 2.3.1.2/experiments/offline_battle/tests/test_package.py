import ast
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODS = ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods'


def _load_validator():
    path = ROOT / 'tools' / 'validate_wotmod.py'
    spec = importlib.util.spec_from_file_location(
        'offline_battle_validator_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OfflineBattlePackageTests(unittest.TestCase):
    def test_validator_manifest_matches_sources(self):
        validator = _load_validator()
        sources = {
            path.relative_to(ROOT / 'src').as_posix() + 'c'
            for path in (ROOT / 'src').rglob('*.py')}
        self.assertEqual(sources, set(validator.EXPECTED_PYC))

    def test_sources_are_python2_compatible_ast(self):
        for path in sorted((ROOT / 'src').rglob('*.py')):
            source = path.read_text(encoding='utf-8')
            tree = ast.parse(source)
            self.assertIsNotNone(tree, path)
            if path.name != '__init__.py':
                self.assertIn('from __future__ import absolute_import',
                              source, path)

    def test_bridge_has_no_catch_all_getattr(self):
        source = (MODS / 'offline_battle_2312' /
                  'avatar_server.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        defined = {node.name for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef)}
        self.assertNotIn('__getattr__', defined)
        self.assertNotIn('__getattribute__', defined)

    def test_runtime_does_not_touch_init_progress_directly(self):
        source = (MODS / 'offline_battle_2312' /
                  'runtime.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if node.attr == '_PlayerAvatar__initProgress':
                    self.assertIsInstance(
                        node.ctx, ast.Load,
                        'runtime must not write __initProgress')
        self.assertNotIn('onSpaceLoaded', source)

    def test_dist_package_when_present(self):
        validator = _load_validator()
        dist = ROOT / 'dist' / ('%s_%s.wotmod' % (validator.MOD_ID,
                                                  validator.MOD_VERSION))
        if not dist.exists():
            self.skipTest('release package is not built')
        count = validator.validate(dist)
        self.assertGreater(count, 0)



class FilterProxyScopeTests(unittest.TestCase):
    """The native turret sync faults for every client-only vehicle.

    A build that scoped the proxy to the player's vehicle killed the
    client at the first enemy's property update, and cost four runs to
    find, so the scope is asserted here.
    """

    def _source(self):
        path = (ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' /
                'mods' / 'offline_battle_2312' / 'runtime.py')
        return path.read_text()

    def test_the_scope_covers_every_vehicle(self):
        source = self._source()
        start = source.index('def set_gun_angles_packed')
        body = source[start:source.index('original_aux_physics', start)]
        self.assertNotIn('isPlayerVehicle', body)
        self.assertIn('_state.sync_scope_vehicle = vehicle', body)

    def test_the_proxy_is_returned_without_an_owner_test(self):
        source = self._source()
        self.assertIn("if name == 'filter' and "
                      "_state.sync_scope_vehicle is vehicle:", source)

if __name__ == '__main__':
    unittest.main()
