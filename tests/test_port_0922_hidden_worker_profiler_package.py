import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import tempfile
import unittest
import zipfile


PORT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = PORT_ROOT / 'tools'


def _load(name):
    path = TOOLS_ROOT / (name + '.py')
    spec = importlib.util.spec_from_file_location(name + '_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_stored_tree(archive, names):
    directories = set()
    for name in names:
        parts = name.split('/')[:-1]
        for index in range(1, len(parts) + 1):
            directories.add('/'.join(parts[:index]) + '/')
    for directory in sorted(directories):
        archive.writestr(directory, b'')


def _powershell_tokens(source):
    """Tokenize delimiters/try blocks while ignoring strings and comments."""
    tokens = []
    index = 0
    length = len(source)
    while index < length:
        character = source[index]
        if character == '#':
            newline = source.find('\n', index + 1)
            index = length if newline < 0 else newline + 1
            continue
        if character in "'\"":
            quote = character
            index += 1
            while index < length:
                if source[index] == '`':
                    index += 2
                    continue
                if source[index] == quote:
                    if (quote == "'" and index + 1 < length and
                            source[index + 1] == quote):
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            else:
                raise AssertionError('unterminated PowerShell string')
            continue
        if character in '{}()[]':
            tokens.append(character)
            index += 1
            continue
        if character.isalpha() or character == '_':
            end = index + 1
            while (end < length and
                   (source[end].isalnum() or source[end] == '_')):
                end += 1
            tokens.append(source[index:end].lower())
            index = end
            continue
        index += 1
    return tokens


def _assert_powershell_structure(test_case, path):
    tokens = _powershell_tokens(path.read_text(encoding='utf-8'))
    opening = {'{': '}', '(': ')', '[': ']'}
    closing = {value: key for key, value in opening.items()}
    stack = []
    matching = {}
    for index, token in enumerate(tokens):
        if token in opening:
            stack.append((token, index))
        elif token in closing:
            test_case.assertTrue(stack, '%s has an extra %s' % (path, token))
            start, start_index = stack.pop()
            test_case.assertEqual(
                closing[token], start,
                '%s has mismatched %s ... %s' % (path, start, token))
            matching[start_index] = index
    test_case.assertEqual([], stack, '%s has unclosed delimiters' % path)
    for index, token in enumerate(tokens):
        if token != 'try':
            continue
        test_case.assertLess(index + 1, len(tokens))
        test_case.assertEqual(
            '{', tokens[index + 1], '%s try has no block' % path)
        end = matching[index + 1]
        test_case.assertLess(end + 1, len(tokens))
        test_case.assertIn(
            tokens[end + 1], ('catch', 'finally'),
            '%s try block is not followed by catch/finally' % path)


class HiddenWorkerProfilerPackageTests(unittest.TestCase):
    def setUp(self):
        self.builder = _load('build_hidden_worker_profiler')
        self.validator = _load('validate_hidden_worker_profiler_zip')

    def _inner_wotmod(self):
        expected = {self.validator.validate_wotmod.ENTRY}
        payloads = {
            'meta.xml': (
                b'<root><id>org.peng.offline_lan_0922</id>'
                b'<version>0.6.6</version></root>'),
            self.validator.validate_wotmod.ENTRY: b'\x03\xf3\r\ncompiled',
        }
        with tempfile.NamedTemporaryFile(suffix='.wotmod') as stream:
            with zipfile.ZipFile(stream.name, 'w', zipfile.ZIP_STORED) as archive:
                _write_stored_tree(archive, payloads)
                for name, payload in sorted(payloads.items()):
                    archive.writestr(name, payload)
            return Path(stream.name).read_bytes(), expected

    def _outer_zip(self, path, mutate_marker=None, extras=None):
        wotmod, expected = self._inner_wotmod()
        marker = self.builder.build_marker(
            'profiler-test-a',
            Path(self.validator.PACKAGE_MEMBER).name,
            hashlib.sha256(wotmod).hexdigest(),
            'a' * 40,
            True)
        if mutate_marker is not None:
            mutate_marker(marker)
        payloads = dict((name, b'payload')
                        for name in self.validator.REQUIRED_FILES)
        payloads[self.validator.PACKAGE_MEMBER] = wotmod
        payloads[self.validator.MARKER_FILENAME] = (
            json.dumps(marker).encode('utf-8'))
        payloads.update(extras or {})
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_STORED) as archive:
            _write_stored_tree(archive, payloads)
            for name, payload in sorted(payloads.items()):
                archive.writestr(name, payload)
        return expected

    def test_package_only_builder_has_no_server_or_launcher_dependency(self):
        source = inspect.getsource(
            self.builder.build_wotmod.build_wotmod_package)
        self.assertNotIn('SERVER_FILENAME', source)
        self.assertNotIn('_write_client_overlay', source)
        self.assertNotIn('launcher', source.lower())

    def test_default_diagnostic_identity_is_explicit_and_bounded(self):
        identity = self.builder.diagnostic_build_identity(
            environ={}, now=0, random_hex='abcdef1234569999')
        self.assertEqual(
            'profiler-19700101T000000Z-abcdef123456', identity)
        self.assertLessEqual(len(identity), 96)

    def test_explicit_diagnostic_identity_is_validated(self):
        self.assertEqual(
            'windows-run-17',
            self.builder.diagnostic_build_identity(environ={
                self.builder.DIAGNOSTIC_IDENTITY_ENV: 'windows-run-17'}))
        with self.assertRaisesRegex(SystemExit, 'is invalid'):
            self.builder.diagnostic_build_identity(environ={
                self.builder.DIAGNOSTIC_IDENTITY_ENV: '../outside'})

    def test_valid_delta_has_one_same_id_wotmod_and_no_user_state(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'profiler.zip'
            expected = self._outer_zip(archive)
            marker = self.validator.validate(
                archive, expected_pyc_members=expected)
        self.assertEqual('profiler-test-a', marker['diagnosticBuildIdentity'])
        self.assertEqual(
            'org.peng.offline_lan_0922_0.6.6.wotmod',
            marker['packageFile'])
        self.assertNotIn(
            'build_identity.json', self.validator.REQUIRED_FILES)
        self.assertFalse(
            self.validator.FORBIDDEN_STATE_FILES.intersection(
                Path(name).name for name in self.validator.REQUIRED_FILES))

    def test_delta_rejects_an_unexpected_user_state_member(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'profiler.zip'
            expected = self._outer_zip(
                archive, extras={
                    'payload/mods/configs/offline_lan_0922/'
                    'server_endpoint.json': b'private'})
            with self.assertRaisesRegex(ValueError, 'member manifest mismatch'):
                self.validator.validate(
                    archive, expected_pyc_members=expected)

    def test_delta_rejects_a_wotmod_that_does_not_match_the_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'profiler.zip'
            expected = self._outer_zip(
                archive,
                mutate_marker=lambda marker: marker.__setitem__(
                    'packageSha256', '0' * 64))
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                self.validator.validate(
                    archive, expected_pyc_members=expected)

    def test_install_and_uninstall_are_reversible_and_fail_closed(self):
        source = (TOOLS_ROOT / 'hidden_worker_profiler_package.ps1').read_text(
            encoding='utf-8')
        for required in (
                'WorldOfTanks.exe', '0\\.9\\.22\\.0\\.1', '#1513',
                'Get-Process -Name "WorldOfTanks"',
                'Get-Process -Name "WoT-Offline-Battles-Launcher"',
                '.offline-hidden-worker-profiler-backup',
                'Updated hidden-worker profiler to build', 'Get-Sha256',
                'Expected exactly one v0.6.6',
                'previousPackages', 'diagnosticBuildIdentity',
                'Original launcher WOTMOD backup remains at'):
            self.assertIn(required, source)
        self.assertNotIn('build_identity.json', source)
        self.assertNotIn('server_endpoint.json', source)
        self.assertNotIn('garage_state.json', source)

    def test_powershell_package_tools_have_balanced_control_structure(self):
        for name in ('hidden_worker_profiler_package.ps1',
                     'collect_hidden_worker_profile.ps1'):
            _assert_powershell_structure(self, TOOLS_ROOT / name)

    def test_windows_paths_are_passed_outside_powershell_arguments(self):
        install_batch = (TOOLS_ROOT /
                         'INSTALL_HIDDEN_WORKER_PROFILER.bat').read_text(
                             encoding='utf-8')
        uninstall_batch = (TOOLS_ROOT /
                           'UNINSTALL_HIDDEN_WORKER_PROFILER.bat').read_text(
                               encoding='utf-8')
        collect_batch = (TOOLS_ROOT /
                         'COLLECT_HIDDEN_WORKER_PROFILE.bat').read_text(
                             encoding='utf-8')
        package_source = (TOOLS_ROOT /
                          'hidden_worker_profiler_package.ps1').read_text(
                              encoding='utf-8')
        collector_source = (TOOLS_ROOT /
                            'collect_hidden_worker_profile.ps1').read_text(
                                encoding='utf-8')

        for batch in (install_batch, uninstall_batch, collect_batch):
            self.assertIn('setlocal DisableDelayedExpansion', batch)
            self.assertIn(
                'set "WOT_HIDDEN_WORKER_PROFILER_GAME_ROOT=%GAME_ROOT%"',
                batch)
            self.assertNotIn('-GameRoot', batch)
            self.assertNotIn('-OutputRoot', batch)
        self.assertIn(
            'set "WOT_HIDDEN_WORKER_PROFILER_OUTPUT_ROOT=%~dp0"',
            collect_batch)

        self.assertIn(
            '[string]$GameRoot = '
            '$env:WOT_HIDDEN_WORKER_PROFILER_GAME_ROOT', package_source)
        self.assertIn(
            '[string]$GameRoot = '
            '$env:WOT_HIDDEN_WORKER_PROFILER_GAME_ROOT', collector_source)
        self.assertIn(
            '[string]$OutputRoot = '
            '$env:WOT_HIDDEN_WORKER_PROFILER_OUTPUT_ROOT', collector_source)
        self.assertIn('function Resolve-FullPath', collector_source)
        self.assertIn('$Value.Trim().Trim([char]34)', collector_source)
        self.assertIn(
            'Resolve-FullPath $GameRoot "Game folder"', collector_source)
        self.assertIn(
            'Resolve-FullPath $OutputRoot "Report folder"', collector_source)

    def test_collector_uses_only_fixed_paths_and_pid_scoped_counters(self):
        source = (TOOLS_ROOT / 'collect_hidden_worker_profile.ps1').read_text(
            encoding='utf-8')
        batch = (TOOLS_ROOT / 'COLLECT_HIDDEN_WORKER_PROFILE.bat').read_text(
            encoding='utf-8')
        for required in (
                'authority_worker_status.json', 'process_id',
                'heartbeat_epoch', 'heartbeat became stale',
                'Get-Process -Id $workerPid',
                'ProcessName -ne "WorldOfTanks"',
                'changed PID', 'another game folder',
                'GPU Engine(*pid_${ProcessId}_*)',
                'no PID-scoped GPU Engine counter instance',
                'offline-worker-python.log',
                'WoTOfflineBattles', 'launcher.log',
                'collection_manifest.json', 'runtime.frame_performance',
                'PERF and slow-frame lines', '$captureRoot + ".zip"'):
            self.assertIn(required, source)
        self.assertNotIn('Get-ChildItem -Path $env:', source)
        self.assertNotIn('-Recurse', source)
        self.assertIn('[int]$Seconds = 90', source)
        self.assertIn('set "CAPTURE_SECONDS=90"', batch)

    def test_install_text_explains_launcher_workflow_and_restoration(self):
        text = self.builder.install_text('profiler-test-a')
        self.assertIn('hidden-worker Python workload', text)
        self.assertIn('profiler overlay', text)
        self.assertIn('changes no gameplay rule, cadence, budget or wire', text)
        self.assertIn('instrumentation', text)
        self.assertIn('_lsprof', text)
        self.assertIn('offline-worker-lsprof-round<N>-w<K>.txt', text)
        self.assertIn('FIRE SHOWN / FIRE CURSOR / FIRE TRACER MOVING', text)
        self.assertIn('FIRE INTENT RECEIVED', text)
        self.assertIn('LSPROF platform line', text)
        self.assertIn('let it finish its normal v0.6.6 installation', text)
        self.assertIn('still start the game, LAN server, visible client and hidden', text)
        self.assertIn('through that same launcher', text)
        self.assertIn('avoids rebuilding or replacing', text)
        self.assertIn('exact same launcher to start the battle', text)
        self.assertIn('do not switch to another launcher build', text)
        self.assertIn('start the battle normally', text)
        self.assertIn('updates it in place', text)
        self.assertIn('preserves the original launcher WOTMOD backup', text)
        self.assertIn('one comprehensive profile', text)
        self.assertIn('default capture is 90 seconds', text)
        self.assertIn('another physical #1513 client copy', text)
        self.assertIn('installer again', text)
        self.assertIn('restores the exact WOTMOD files', text)
        self.assertIn('unavailable rather than estimating it', text)


if __name__ == '__main__':
    unittest.main()
