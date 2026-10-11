"""Exercise the real starter Job lifetime with a controlled native child.

Compile native_starter_fake_game.c as a Windows GUI executable and pass its
path in WOT_STARTER_FAKE_GAME. No installed game or user save is accessed.
"""
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt' and os.environ.get('WOT_STARTER_FAKE_GAME'),
                     'requires Windows and the native fake-game fixture')
class StarterLifetimeTests(unittest.TestCase):
    def _wait_file(self, path, process):
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if path.is_file() and path.stat().st_size:
                return
            self.assertIsNone(process.poll(), 'starter exited before child ready')
            time.sleep(0.01)
        self.fail('native child did not acknowledge startup')

    def _run(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            starter = root / 'offline_worker_starter.exe'
            shutil.copy2(ROOT / 'native/offline_worker_starter.exe', starter)
            shutil.copy2(os.environ['WOT_STARTER_FAKE_GAME'], root / 'WorldOfTanks.exe')
            environment = dict(os.environ, WOT_STARTER_FAKE_MODE=mode,
                               APPDATA=str(root / 'roaming'),
                               LOCALAPPDATA=str(root / 'local'))
            for name in ('WOT_OFFLINE_PROCDUMP_PATH', 'WOT_OFFLINE_CRASH_DUMP_PATH'):
                environment.pop(name, None)
            process = subprocess.Popen([str(starter), '--player'], cwd=str(root),
                env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                self._wait_file(root / 'parent-exiting', process)
                if mode == 'handoff':
                    self._wait_file(root / 'child-ready', process)
                    # Prove that the initial process is gone while its Job-owned
                    # replacement still waits for our explicit release signal.
                    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
                    kernel.OpenProcess.restype = ctypes.c_void_p
                    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
                    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                    parent = kernel.OpenProcess(0x100000, False,
                        int((root / 'parent-exiting').read_text()))
                    if parent:
                        try:
                            self.assertEqual(0, kernel.WaitForSingleObject(parent, 3000))
                        finally:
                            kernel.CloseHandle(parent)
                    self.assertIsNone(process.poll(), 'live replacement was terminated')
                    (root / 'release-child').write_text('release')
                began = time.monotonic()
                self.assertEqual(0, process.wait(timeout=3.0))
                elapsed = time.monotonic() - began
                print('Native starter %s exit: %.3f s' % (mode, elapsed))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5.0)

    def test_normal_exit_has_no_ten_second_delay(self):
        self._run('normal')

    def test_live_replacement_survives_parent_exit_then_closes_promptly(self):
        self._run('handoff')


if __name__ == '__main__':
    unittest.main()
