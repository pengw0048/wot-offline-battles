import importlib.util
from pathlib import Path
import tempfile
import unittest


PORT_ROOT = Path(__file__).resolve().parents[1]


class PortLicensingTests(unittest.TestCase):
    def test_packager_copies_required_legal_files(self):
        packager_path = PORT_ROOT / 'build_wotmod.py'
        spec = importlib.util.spec_from_file_location(
            'build_wotmod_licensing_test', packager_path)
        packager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packager)

        with tempfile.TemporaryDirectory() as directory:
            packager._copy_legal_files(directory)
            destination = Path(directory)

            self.assertTrue((destination / 'LICENSE').is_file())
            self.assertTrue(
                (destination / 'THIRD_PARTY_NOTICES.md').is_file())
            self.assertTrue(
                (destination / 'licenses' / 'Boost-1.0.txt').is_file())


if __name__ == '__main__':
    unittest.main()
