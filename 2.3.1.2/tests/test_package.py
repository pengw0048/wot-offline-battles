import importlib.util
from pathlib import Path
import ast
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def _load_validator():
    path = ROOT / 'tools' / 'validate_wotmod.py'
    spec = importlib.util.spec_from_file_location(
        'offline_2312_poc_validator_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PackageContractTests(unittest.TestCase):
    def test_formal_porting_baseline_preserves_the_mature_source_rule(self):
        baseline = (ROOT / 'PORTING_BASELINE.md').read_text(encoding='utf-8')
        for required in (
                'Behavior source:',
                'Modern structural template:',
                'Target adapters only:',
                'Copy working battle law unchanged',
                'real own Vehicle',
                'second in-process round'):
            self.assertIn(required, baseline)

    def test_probe_source_has_no_world_mutation_or_network_surface(self):
        source_root = ROOT / 'src'
        sources = '\n'.join(
            path.read_text(encoding='utf-8')
            for path in sorted(source_root.rglob('*.py')))
        tree = ast.parse(sources)
        names = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attributes = {
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)}
        for forbidden in (
                'createSpace', 'clearAllSpaces', 'createEntity',
                'destroyEntity', 'controlEntity', 'launch', 'onStartup',
                'socket', 'urllib', 'requests'):
            self.assertNotIn(forbidden, names | attributes)

    def test_probe_is_one_direct_loader_entry_with_early_markers(self):
        validator = _load_validator()
        self.assertEqual({validator.ENTRY}, validator.EXPECTED_PYC)
        source = (
            ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' / 'mods' /
            'mod_offline_2312_poc.py').read_text(encoding='utf-8')
        self.assertIn("module_import argv=%r", source)
        self.assertIn("init_enter argv=%r", source)
        self.assertIn("inactive reason=offline_request_missing", source)

    def test_runtime_command_forwards_each_python_token_explicitly(self):
        instructions = '\n'.join(
            (ROOT / name).read_text(encoding='utf-8')
            for name in ('README.md', 'INSTALL.txt'))
        command = (
            'win64\\WorldOfTanks.exe --script-arg offline '
            '--script-arg spaces/01_karelia')
        self.assertIn(command, instructions)
        self.assertNotIn(
            'win64\\WorldOfTanks.exe offline spaces/01_karelia',
            instructions)

    def test_validator_rejects_python_source_in_release(self):
        validator = _load_validator()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.wotmod'
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_STORED) as archive:
                archive.writestr('meta.xml', '<root/>')
                archive.writestr(
                    'res/scripts/client/gui/mods/mod_bad.py', 'pass\n')
            with self.assertRaisesRegex(ValueError, 'manifest mismatch'):
                validator.validate(path)

    def test_builder_is_explicitly_pinned_to_cpython_27(self):
        source = (ROOT / 'build_wotmod.py').read_text(encoding='utf-8')
        self.assertIn("sys.version_info[:2] != (2, 7)", source)
        self.assertIn("PYTHON_27_MAGIC = '\\x03\\xf3\\r\\n'", source)
        self.assertIn("name == '__pycache__'", source)
        self.assertIn('info.create_system = 0', source)
        self.assertIn("16 if archive_name.endswith('/') else 32", source)
        self.assertNotIn('scripts.pkg', source)
        self.assertNotIn('01_karelia.pkg', source)

    def test_ci_imports_the_compiled_module_from_the_final_package(self):
        workflow = (
            ROOT.parent / '.github' / 'workflows' / 'tests.yml').read_text(
                encoding='utf-8')
        self.assertIn(
            'python tests/smoke_probe_py27.py \\\n'
            '            dist/org.peng.offline_2312_poc_0.1.1.wotmod',
            workflow)


if __name__ == '__main__':
    unittest.main()
