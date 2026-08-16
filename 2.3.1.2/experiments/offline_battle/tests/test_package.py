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



class PackageImportGraphTests(unittest.TestCase):
    """Every package-internal import must name a file that ships.

    ai/maps.py imported a reviewed-routes module the port script did not
    copy, which no test executed, so the mod would have died at load."""

    PACKAGE = 'gui.mods.offline_battle_2312'

    def test_every_internal_import_target_exists(self):
        package = MODS / 'offline_battle_2312'
        missing = []
        for path in sorted(package.rglob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            guarded = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Try) and node.handlers:
                    guarded.update(id(child) for child in ast.walk(node))
            for node in ast.walk(tree):
                if id(node) in guarded:
                    continue
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if (alias.name.startswith(self.PACKAGE) and
                                self._module(package, alias.name) is None):
                            missing.append('%s -> %s' % (path.name,
                                                         alias.name))
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    module = node.module or ''
                    if not module.startswith(self.PACKAGE):
                        continue
                    kind = self._module(package, module)
                    if kind is None:
                        missing.append('%s -> %s' % (path.name, module))
                    elif kind == 'package':
                        for alias in node.names:
                            child = '%s.%s' % (module, alias.name)
                            if (self._module(package, child) is None and
                                    alias.name not in
                                    self._bound(package, module)):
                                missing.append('%s -> %s' % (path.name,
                                                             child))
        self.assertEqual(missing, [])

    def _module(self, package, dotted):
        relative = dotted[len(self.PACKAGE):].lstrip('.')
        parts = relative.split('.') if relative else []
        if package.joinpath(*parts).with_suffix('.py').is_file():
            return 'module'
        if package.joinpath(*parts, '__init__.py').is_file():
            return 'package'
        return None

    def _bound(self, package, dotted):
        relative = dotted[len(self.PACKAGE):].lstrip('.')
        parts = relative.split('.') if relative else []
        init = package.joinpath(*parts, '__init__.py')
        names = set()
        for node in ast.parse(init.read_text(encoding='utf-8')).body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                names.update(target.id for target in node.targets
                             if isinstance(target, ast.Name))
        return names


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


class AttributeShadowTests(unittest.TestCase):
    """An attribute must not take the name of a method on its class.

    `self._contacts = 0` shadowed the `_contacts()` the rotation guard
    called, which killed the motion tick with a TypeError only a real
    battle could show.
    """

    def _classes(self):
        package = (ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' /
                   'mods' / 'offline_battle_2312')
        for path in sorted(package.glob('*.py')):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    yield path.name, node

    def test_no_attribute_shadows_a_method(self):
        collisions = []
        for module, node in self._classes():
            methods = set()
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.add(item.name)
            for child in ast.walk(node):
                if not isinstance(child, ast.Assign):
                    continue
                for target in child.targets:
                    if (isinstance(target, ast.Attribute) and
                            isinstance(target.value, ast.Name) and
                            target.value.id == 'self' and
                            target.attr in methods):
                        collisions.append('%s.%s.%s' % (module, node.name,
                                                        target.attr))
        self.assertEqual(collisions, [])

if __name__ == '__main__':
    unittest.main()
