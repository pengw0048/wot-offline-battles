"""Original package ordering is retained without importing original mods."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tools'))
from prepare_native_client import original_resource_paths


class OriginalResourcesTests(unittest.TestCase):
    def test_hd_packages_keep_authored_precedence_and_original_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            packages = root / 'res/packages'
            packages.mkdir(parents=True)
            for name in ('shared_content.pkg', 'shared_content_hd.pkg', 'scripts.pkg'):
                (packages / name).touch()
            (root / 'paths.xml').write_text('''<root><Paths>
              <Path>./res_mods/0.9.22.0.1</Path>
              <Path mode="recursive" mask="*.wotmod">./mods/0.9.22.0.1</Path>
              <Path type="sd,hd">./res/packages/shared_content.pkg</Path>
              <Path type="hd">./res/packages/shared_content_hd.pkg</Path>
              <Path type="sandbox">./res/packages/uninstalled_sandbox.pkg</Path>
              <Path type="sandbox,sd,hd">./res/packages/scripts.pkg</Path>
              <Path>./res</Path>
            </Paths></root>''')
            self.assertEqual(original_resource_paths(root), [
                packages / 'shared_content.pkg', packages / 'shared_content_hd.pkg',
                packages / 'scripts.pkg', root / 'res'])

    def test_missing_authored_package_is_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / 'res/packages').mkdir(parents=True)
            (root / 'paths.xml').write_text('''<root><Paths>
              <Path type="hd">./res/packages/scripts.pkg</Path>
            </Paths></root>''')
            with self.assertRaisesRegex(ValueError, 'Missing authored HD package'):
                original_resource_paths(root)

    def test_scripts_from_an_unrelated_directory_are_not_original_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / 'other').mkdir()
            (root / 'other/scripts.pkg').touch()
            (root / 'paths.xml').write_text('''<root><Paths>
              <Path>./other/scripts.pkg</Path>
            </Paths></root>''')
            with self.assertRaisesRegex(ValueError, 'do not contain scripts.pkg'):
                original_resource_paths(root)

    def test_native_package_patch_preserves_zip_path_and_is_repeatable(self):
        source = ROOT / 'native/standalone_client/bigworld/patch_resource_packages.py'
        spec = importlib.util.spec_from_file_location('resource_patch', source)
        patcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patcher)
        with tempfile.TemporaryDirectory() as temporary:
            engine = Path(temporary)
            target = engine / 'lib/resmgr/bwresource.cpp'
            target.parent.mkdir(parents=True)
            target.write_text('before\n\t\t' + patcher.ORIGINAL + 'after\n')
            patcher.patch(engine)
            expected = target.read_text()
            patcher.patch(engine)
            self.assertEqual(target.read_text(), expected)
            self.assertIn('== PACKED_EXTENSION ||', expected)
            self.assertIn('== "pkg") &&', expected)
            self.assertTrue(expected.endswith('after\n'))


if __name__ == '__main__':
    unittest.main()
