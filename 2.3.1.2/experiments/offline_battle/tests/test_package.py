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


if __name__ == '__main__':
    unittest.main()
