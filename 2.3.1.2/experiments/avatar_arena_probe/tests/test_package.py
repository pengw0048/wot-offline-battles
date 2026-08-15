import ast
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load_validator():
    path = ROOT / 'tools' / 'validate_wotmod.py'
    spec = importlib.util.spec_from_file_location(
        'avatar_arena_probe_validator_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AvatarArenaPackageTests(unittest.TestCase):
    def test_release_is_one_direct_loader_entry(self):
        validator = _load_validator()
        self.assertEqual({validator.ENTRY}, validator.EXPECTED_PYC)
        source = (
            ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods' /
            'mod_offline_2312_avatar_arena_probe.py').read_text(
                encoding='utf-8')
        self.assertIn('module_import argv=%r', source)
        self.assertIn('route_installed target=helpers.OfflineMode.launch',
                      source)
        self.assertIn('bootstrap_ready', source)

    def test_probe_has_no_direct_entity_vehicle_or_network_implementation(self):
        source = '\n'.join(
            path.read_text(encoding='utf-8')
            for path in sorted((ROOT / 'src').rglob('*.py')))
        tree = ast.parse(source)
        names = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attributes = {
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)}
        for forbidden in (
                'createSpace', 'addSpaceGeometryMapping', 'createEntity',
                'destroyEntity', 'controlEntity', 'Vehicle',
                'AvatarInputHandler', 'onSpaceLoaded',
                'setWindowMode', 'setVideoVSync', 'setTripleBuffering',
                'setWatcher', 'socket', 'urllib', 'requests'):
            self.assertNotIn(forbidden, names | attributes)
        self.assertIn('creator.create', source)
        self.assertIn('creator.destroy', source)
        self.assertIn('wg_collideSegment', source)
        self.assertIn('collision.closestPoint', source)
        self.assertIn('camera.target = target', source)
        self.assertIn('camera.source = source', source)
        self.assertIn('camera.forceUpdate()', source)

    def test_target_adapter_is_scoped_to_synchronous_stock_create(self):
        source = (
            ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods' /
            'mod_offline_2312_avatar_arena_probe.py').read_text(
                encoding='utf-8')
        self.assertIn('avatar_type.__init__ = routed_avatar_init', source)
        self.assertIn('avatar_type.hasBonusCap = routed_has_bonus_cap', source)
        self.assertIn(
            'avatar_type.onEnterWorld = routed_on_enter_world', source)
        self.assertIn(
            'avatar_type.onBecomePlayer = routed_on_become_player', source)
        self.assertIn('finally:', source)
        self.assertIn("self._creator.create(self._map_name)", source)
        self.assertIn("'bonusCapsOverrides':", (
            ROOT / 'tests' / 'test_probe.py').read_text(encoding='utf-8'))

    def test_route_is_explicit_and_does_not_fallback_to_stock_launch(self):
        source = (
            ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods' /
            'mod_offline_2312_avatar_arena_probe.py').read_text(
                encoding='utf-8')
        self.assertIn("ACTIVATION_TOKEN = 'avatarArenaProbe'", source)
        self.assertNotIn('_original_launch(space_name)', source)
        self.assertIn('game_module.fini = _routed_game_fini', source)
        self.assertIn('creator.destroy()', source)

    def test_builder_is_pinned_to_cpython_27_and_dos_zip_metadata(self):
        source = (ROOT / 'build_wotmod.py').read_text(encoding='utf-8')
        self.assertIn("sys.version_info[:2] != (2, 7)", source)
        self.assertIn("PYTHON_27_MAGIC = '\\x03\\xf3\\r\\n'", source)
        self.assertIn('info.create_system = 0', source)
        self.assertIn("16 if archive_name.endswith('/') else 32", source)
        self.assertNotIn('scripts.pkg', source)
        self.assertNotIn('01_karelia.pkg', source)

    def test_documented_command_forwards_every_token(self):
        docs = '\n'.join(
            (ROOT / name).read_text(encoding='utf-8')
            for name in ('README.md', 'INSTALL.txt'))
        command = (
            'win64\\WorldOfTanks.exe --script-arg avatarArenaProbe '
            '--script-arg offline --script-arg spaces/01_karelia')
        self.assertIn(command, docs)
        self.assertIn('offline map bootstrap 0.1.2', docs)
        self.assertIn('camera_repositioned', docs)
        self.assertIn('ArenaInfo/TeamInfo', docs)


if __name__ == '__main__':
    unittest.main()
