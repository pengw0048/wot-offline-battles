import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PORT_ROOT = Path(__file__).resolve().parents[1]
SERVER_ENTRY = PORT_ROOT / 'server' / 'lan_battle_server.py'


class PortServerLayoutTests(unittest.TestCase):
    def test_server_entry_is_independent_of_the_working_directory(self):
        environment = os.environ.copy()
        environment.pop('PYTHONPATH', None)
        with tempfile.TemporaryDirectory() as workdir:
            result = subprocess.run(
                [sys.executable, str(SERVER_ENTRY), '--help'],
                cwd=workdir,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        self.assertEqual(
            0,
            result.returncode,
            msg='stdout:\n%s\nstderr:\n%s' %
                (result.stdout, result.stderr),
        )
        self.assertIn('LAN server for the offhangar network MVP',
                      result.stdout)


if __name__ == '__main__':
    unittest.main()
