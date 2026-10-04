"""Exercise the real starter Job lifetime with a controlled native child.

Compile native_starter_fake_game.c as a Windows GUI executable and pass its
path in WOT_STARTER_FAKE_GAME. No installed game or user save is accessed.
"""
import ctypes
from contextlib import contextmanager
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
    @contextmanager
    def _fixture_directory(self):
        directory = tempfile.mkdtemp(prefix='wot-starter-test-')
        try:
            yield directory
        finally:
            # Even writable images can remain briefly unavailable after the
            # process exits and its handles close. Observe actual deletion.
            deadline = time.monotonic() + 5.0
            while True:
                try:
                    shutil.rmtree(directory)
                    break
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)

    @contextmanager
    def _launch_worker(self, mode):
        with self._fixture_directory() as directory:
            root = Path(directory)
            starter = root / 'offline_worker_starter.exe'
            # Copy contents only: shared source files may be Windows read-only.
            shutil.copyfile(ROOT / 'native/offline_worker_starter.exe', starter)
            shutil.copyfile(os.environ['WOT_STARTER_FAKE_GAME'], root / 'WorldOfTanks.exe')
            shutil.copyfile(os.environ['WOT_STARTER_FAKE_GAME'], root / 'helper.exe')
            environment = dict(os.environ, WOT_STARTER_FAKE_MODE=mode,
                               APPDATA=str(root / 'roaming'),
                               LOCALAPPDATA=str(root / 'local'))
            for name in ('WOT_OFFLINE_PROCDUMP_PATH', 'WOT_OFFLINE_CRASH_DUMP_PATH'):
                environment.pop(name, None)
            process = subprocess.Popen([str(starter), '--worker-only'], cwd=str(root),
                env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                yield root, process, environment
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5.0)
                # Closing the starter's Job initiates child termination. Wait
                # for those exact fixture PIDs before removing their images.
                for name in ('parent-exiting', 'child-ready'):
                    marker = root / name
                    if marker.is_file() and marker.stat().st_size:
                        self._wait_process_exit(int(marker.read_text()))
                # Popen keeps its Windows process HANDLE after wait(). Release
                # our reference before attempting fixture directory cleanup.
                process._handle.Close()

    def _wait_log(self, root, text, process):
        path = root / 'offline-worker-starter.log'
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if path.is_file() and text in path.read_text():
                return
            self.assertIsNone(process.poll(), 'starter exited before acknowledgement')
            time.sleep(0.01)
        self.fail('starter did not acknowledge: ' + text)

    def _wait_process_exit(self, process_id):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, False, process_id)
        if handle:
            try:
                self.assertEqual(0, kernel.WaitForSingleObject(handle, 3000))
            finally:
                kernel.CloseHandle(handle)
        else:
            self.assertEqual(87, ctypes.get_last_error(), 'cannot inspect fake child')

    def _worker_handoff(self, root, process):
        self._wait_file(root / 'child-ready', process)
        self._wait_log(root, 'stage=worker_process_handoff', process)
        self._wait_process_exit(int((root / 'parent-exiting').read_text()))
        self.assertFalse((root / 'offline-worker.ready').exists())
        self.assertIsNone(process.poll())

    def _wait_file(self, path, process):
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if path.is_file() and path.stat().st_size:
                return
            self.assertIsNone(process.poll(), 'starter exited before child ready')
            time.sleep(0.01)
        self.fail('native child did not acknowledge startup')

    def _run(self, mode):
        with self._fixture_directory() as directory:
            root = Path(directory)
            starter = root / 'offline_worker_starter.exe'
            shutil.copyfile(ROOT / 'native/offline_worker_starter.exe', starter)
            shutil.copyfile(os.environ['WOT_STARTER_FAKE_GAME'], root / 'WorldOfTanks.exe')
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
                process._handle.Close()

    def test_normal_exit_has_no_ten_second_delay(self):
        self._run('normal')

    def test_live_replacement_survives_parent_exit_then_closes_promptly(self):
        self._run('handoff')

    def test_worker_replacement_must_publish_its_own_ready_marker(self):
        with self._launch_worker('worker-handoff') as (root, process, unused):
            self._worker_handoff(root, process)
            parent_id = int((root / 'parent-exiting').read_text())
            (root / 'stale-worker-ready').write_text('publish stale generation')
            self._wait_log(root, 'stage=worker_ready_other_process pid=%d' % parent_id,
                           process)
            self.assertFalse((root / 'offline-worker.ready').exists())
            (root / 'publish-worker-ready').write_text('publish current generation')
            self._wait_file(root / 'offline-worker.ready', process)
            (root / 'release-child').write_text('release')
            self.assertEqual(0, process.wait(timeout=3.0))
            self.assertFalse((root / 'offline-worker.ready').exists())

    def test_worker_clean_exit_without_replacement_is_not_ready(self):
        with self._launch_worker('worker-normal') as (root, process, unused):
            self.assertEqual(0, process.wait(timeout=3.0))
            self.assertFalse((root / 'offline-worker.ready').exists())
            self.assertIn('worker_exited_before_ready exit_code=0',
                          (root / 'offline-worker-starter.log').read_text())

    def test_worker_parent_crash_is_not_hidden_by_its_live_child(self):
        with self._launch_worker('worker-parent-failure') as (root, process, unused):
            self.assertEqual(44, process.wait(timeout=5.0))
            self.assertFalse((root / 'offline-worker.ready').exists())
            self._wait_process_exit(int((root / 'child-ready').read_text()))
            self.assertNotIn('worker_process_handoff',
                             (root / 'offline-worker-starter.log').read_text())

    def test_worker_replacement_failure_before_ready_is_preserved(self):
        with self._launch_worker('worker-handoff') as (root, process, unused):
            self._worker_handoff(root, process)
            (root / 'exit-child-before-ready').write_text('fail')
            self.assertEqual(43, process.wait(timeout=3.0))
            self.assertFalse((root / 'offline-worker.ready').exists())

    def test_worker_replacement_failure_after_ready_is_preserved(self):
        with self._launch_worker('worker-child-failure') as (root, process, unused):
            self._worker_handoff(root, process)
            (root / 'publish-worker-ready').write_text('publish')
            self._wait_file(root / 'offline-worker.ready', process)
            (root / 'release-child').write_text('fail')
            self.assertEqual(43, process.wait(timeout=3.0))

    def test_worker_stop_retires_replacement_before_and_after_ready(self):
        for ready in (False, True):
            with self.subTest(ready=ready), self._launch_worker('worker-handoff') as item:
                root, process, unused = item
                self._worker_handoff(root, process)
                if ready:
                    (root / 'publish-worker-ready').write_text('publish')
                    self._wait_file(root / 'offline-worker.ready', process)
                subprocess.run([str(root / 'offline_worker_starter.exe'),
                    '--stop-starter', str(process.pid)], check=True, timeout=3.0,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(0, process.wait(timeout=3.0))
                self._wait_process_exit(int((root / 'child-ready').read_text()))
                self.assertFalse((root / 'offline-worker.ready').exists())

    def test_worker_does_not_adopt_a_different_executable_in_its_job(self):
        with self._launch_worker('worker-helper') as (root, process, unused):
            self.assertEqual(0, process.wait(timeout=5.0))
            self.assertFalse((root / 'offline-worker.ready').exists())
            self._wait_process_exit(int((root / 'child-ready').read_text()))
            self.assertNotIn('worker_process_handoff',
                             (root / 'offline-worker-starter.log').read_text())


if __name__ == '__main__':
    unittest.main()
