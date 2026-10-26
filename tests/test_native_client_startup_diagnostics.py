import contextlib
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile


TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
import package_native_client as packaging
import prepare_native_client as prepare


class NativeClientStartupDiagnostics(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.package = self.root / 'package'
        self.package.mkdir()
        self.record = {'client_version': '0.9.22.0.1', 'client_build': '1513',
                       'resource_paths': ['owned/scripts.pkg']}

    def run_main(self, arguments=(), prepare_error=None, native_call=None):
        argv = ['prepare_native_client.py', '--package-root', str(self.package),
                '--client-root', str(self.root / 'owned')] + list(arguments)
        with patch.object(sys, 'argv', argv), \
                patch.object(prepare, 'os', SimpleNamespace(name='nt', environ={})), \
                patch.object(prepare, 'prepare', return_value=self.record,
                             side_effect=prepare_error), \
                patch.object(prepare.subprocess, 'call', side_effect=native_call,
                             return_value=0) as launch, \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            result = prepare.main()
        return result, launch, errors.getvalue()

    def test_logs_are_archived_before_launch_and_crash_exit_is_recorded(self):
        for name in ('standalone.log', 'python.log'):
            (self.package / name).write_text('first failure: ' + name)

        def launch(*args, **kwargs):
            self.assertEqual(kwargs['cwd'], str(self.package))
            self.assertEqual(args[0], [str(self.package / 'bwclient_h.exe'), '-noConversion'])
            self.assertFalse((self.package / 'standalone.log').exists())
            self.assertFalse((self.package / 'python.log').exists())
            archive, = (self.package / 'logs').iterdir()
            self.assertRegex(archive.name, r'^\d{8}T\d{6}\.\d{6}Z$')
            for name in ('standalone.log', 'python.log'):
                self.assertEqual((archive / name).read_text(), 'first failure: ' + name)
                (self.package / name).write_text('second failure: ' + name)
            return -1073741819

        result, unused_launch, errors = self.run_main(native_call=launch)
        self.assertEqual(result, -1073741819)
        self.assertIn('0xC0000005', errors)
        self.assertIn(str(self.package / 'prepare.log'), errors)
        log = (self.package / 'prepare.log').read_text()
        self.assertIn('Preparation succeeded:', log)
        self.assertIn('code=-1073741819 (0xC0000005)', log)

    def test_preparation_failure_keeps_native_logs_and_persists_exception(self):
        (self.package / 'standalone.log').write_text('previous failure')
        result, launch, errors = self.run_main(prepare_error=ValueError('Wrong client build'))
        self.assertEqual(result, 1)
        launch.assert_not_called()
        self.assertEqual((self.package / 'standalone.log').read_text(), 'previous failure')
        self.assertFalse((self.package / 'logs').exists())
        self.assertIn('Wrong client build', errors)
        log = (self.package / 'prepare.log').read_text()
        self.assertIn('Traceback', log)
        self.assertIn('ValueError: Wrong client build', log)

    def test_prepare_only_appends_diagnostics_without_archiving_or_launching(self):
        (self.package / 'prepare.log').write_text('previous preparation\n')
        (self.package / 'python.log').write_text('previous native failure')
        result, launch, errors = self.run_main(arguments=['--prepare-only'])
        self.assertEqual(result, 0)
        launch.assert_not_called()
        self.assertEqual(errors, '')
        log = (self.package / 'prepare.log').read_text()
        self.assertTrue(log.startswith('previous preparation\n'))
        self.assertIn('Preparation-only run completed', log)
        self.assertEqual((self.package / 'python.log').read_text(), 'previous native failure')

    def test_archive_failure_prevents_launch_and_preserves_existing_logs(self):
        (self.package / 'standalone.log').write_text('previous failure')
        (self.package / 'logs').write_text('directory is blocked')
        result, launch, errors = self.run_main()
        self.assertEqual(result, 1)
        launch.assert_not_called()
        self.assertEqual((self.package / 'standalone.log').read_text(), 'previous failure')
        self.assertIn('Native client preparation failed:', errors)
        self.assertIn('Traceback', (self.package / 'prepare.log').read_text())

    def test_launch_exception_is_persisted(self):
        result, launch, errors = self.run_main(native_call=OSError('Missing runtime DLL'))
        self.assertEqual(result, 1)
        launch.assert_called_once()
        self.assertIn('Missing runtime DLL', errors)
        self.assertIn('OSError: Missing runtime DLL', (self.package / 'prepare.log').read_text())

    def test_package_keeps_interactive_entry_and_handles_negative_exit_codes(self):
        source = self.root / 'source'
        for relative in ('tools/vendor/navigation_baseline_f5b0173',
                         'tools/vendor/wot_space_bin_utils',
                         'native/standalone_client/bigworld/res'):
            (source / relative).mkdir(parents=True)
        (self.package / 'bwclient_h.exe').touch()
        runtime = io.BytesIO()
        with zipfile.ZipFile(runtime, 'w') as archive:
            archive.writestr('python.exe', b'fixture')
        runtime.seek(0)
        with patch.object(packaging, 'ROOT', source), \
                patch.object(packaging, 'TOOL_FILES', ()), \
                patch.object(packaging, 'urlopen', return_value=runtime), \
                contextlib.redirect_stdout(io.StringIO()):
            packaging.package(self.package)
        command = (self.package / 'Start-Client.cmd').read_bytes()
        self.assertIn(b'prepare_native_client.py %*\r\n', command)
        self.assertIn(b'if not "%errorlevel%"=="0" pause\r\n', command)
        self.assertNotIn(b'>', command)
        readme = (self.package / 'README-FIRST.txt').read_text()
        self.assertIn('prepare.log', readme)
        self.assertIn('logs/<UTC timestamp>/', readme)
        self.assertIn('.dmp file if generated', readme)


if __name__ == '__main__':
    unittest.main()
