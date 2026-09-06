import hashlib
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import zipfile


LAUNCHER_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAUNCHER_ROOT))

import bot_chat_install as install  # noqa: E402
import core  # noqa: E402


PAYLOAD = b"".join(bytes([index % 251]) for index in range(4096))
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()
ENTRY = {"file": "model.gguf", "size": len(PAYLOAD), "sha256": DIGEST}


class _Response(object):
    """The subset of a urlopen result the installer actually uses."""

    def __init__(self, payload, status=200):
        self._stream = io.BytesIO(payload)
        self.status = status
        self.closed = False

    def read(self, size=-1):
        return self._stream.read(size)

    def close(self):
        self.closed = True


class _Host(object):
    """Serve one payload, honouring or refusing range requests."""

    def __init__(self, payload=PAYLOAD, honour_range=True, fail=None,
                 truncate_at=None, extra=b""):
        self.payload = payload
        self.honour_range = honour_range
        self.fail = fail
        self.truncate_at = truncate_at
        self.extra = extra
        self.requests = []
        self.responses = []

    def __call__(self, request, timeout=None):
        header = request.get_header("Range") or ""
        self.requests.append(header)
        if self.fail is not None:
            raise self.fail
        offset = 0
        if header.startswith("bytes=") and self.honour_range:
            offset = int(header[len("bytes="):].rstrip("-"))
            body = self.payload[offset:] + self.extra
            response = _Response(body, status=206)
        else:
            body = self.payload + self.extra
            response = _Response(body, status=200)
        if self.truncate_at is not None:
            response = _Response(body[:self.truncate_at],
                                 status=response.status)
        self.responses.append(response)
        return response


class _Catalogue(object):
    """A stand-in for the server's pinned catalogue."""

    MODELSCOPE = "modelscope"
    HUGGINGFACE = "huggingface"
    GITHUB = "github"
    SOURCES = (MODELSCOPE, HUGGINGFACE)
    MODEL_TIERS = ({"key": "tiny", **ENTRY},)
    RUNTIME_ASSETS = {"x64": {"file": "runtime.zip"}}

    def __init__(self, runtime_entry=None):
        self.runtime_entry = runtime_entry

    def tier(self, key):
        return dict(ENTRY, key="tiny") if key == "tiny" else None

    def model_url(self, key, source):
        return None if self.tier(key) is None else "https://%s/%s" % (
            source, ENTRY["file"])

    def runtime_asset(self, arch):
        return dict(self.runtime_entry) if (
            arch == "x64" and self.runtime_entry) else None

    def runtime_sources(self, arch):
        return (self.MODELSCOPE, self.GITHUB) if arch == "x64" else ()

    def runtime_url(self, arch, source=None):
        if self.runtime_asset(arch) is None:
            return None
        return "https://%s/%s" % (source or self.MODELSCOPE,
                                  self.runtime_entry["file"])

    @staticmethod
    def runtime_arch(machine):
        return "x64" if str(machine).lower() in ("amd64", "x86_64") else None


def _read(path):
    with open(path, 'rb') as stream:
        return stream.read()


def _runtime_zip(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, payload in members:
            bundle.writestr(name, payload)
    return buffer.getvalue()


class _Temp(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="bot-chat-test-")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def _destination(self, name="model.gguf"):
        return os.path.join(install.install_root(self.base), name)


class PathTest(_Temp):
    def test_everything_lives_under_one_removable_directory(self):
        root = install.install_root(self.base)
        self.assertTrue(install.model_path(ENTRY, self.base).startswith(root))
        self.assertTrue(install.runtime_executable(
            self.base).startswith(root))

    def test_the_generator_is_named_by_the_installer(self):
        self.assertTrue(install.runtime_executable(self.base).endswith(
            'llama-server.exe'))


class DigestTest(_Temp):
    def _write(self, payload):
        path = self._destination()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as stream:
            stream.write(payload)
        return path

    def test_a_matching_file_is_installed(self):
        self.assertTrue(install.is_installed(self._write(PAYLOAD), ENTRY))

    def test_a_short_file_is_rejected_without_hashing(self):
        self.assertFalse(install.is_installed(self._write(PAYLOAD[:-1]),
                                              ENTRY))

    def test_a_same_length_different_file_is_rejected(self):
        corrupt = bytearray(PAYLOAD)
        corrupt[7] ^= 0xFF
        self.assertFalse(install.is_installed(self._write(bytes(corrupt)),
                                              ENTRY))

    def test_a_missing_file_is_not_installed(self):
        self.assertFalse(install.is_installed(
            os.path.join(self.base, 'absent'), ENTRY))

    def test_hashing_can_be_cancelled(self):
        path = self._write(PAYLOAD)
        self.assertRaises(install.InstallCancelled, install.file_digest,
                          path, lambda: True)


class DownloadTest(_Temp):
    def test_a_fresh_download_is_verified_and_placed(self):
        host = _Host()
        path = install.download_file('https://host/f', self._destination(),
                                     ENTRY, opener=host)
        self.assertEqual(PAYLOAD, _read(path))
        self.assertEqual([''], host.requests)
        self.assertFalse(os.path.exists(path + install.PART_SUFFIX))

    def test_progress_reaches_the_declared_total(self):
        seen = []
        install.download_file('https://host/f', self._destination(), ENTRY,
                              progress=lambda done, total: seen.append(
                                  (done, total)),
                              opener=_Host())
        self.assertEqual((0, ENTRY['size']), seen[0])
        self.assertEqual((ENTRY['size'], ENTRY['size']), seen[-1])
        self.assertEqual(sorted(done for done, unused in seen),
                         [done for done, unused in seen])

    def test_a_partial_file_is_resumed_rather_than_restarted(self):
        destination = self._destination()
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with open(destination + install.PART_SUFFIX, 'wb') as stream:
            stream.write(PAYLOAD[:1000])
        host = _Host()
        path = install.download_file('https://host/f', destination, ENTRY,
                                     opener=host)
        self.assertEqual(PAYLOAD, _read(path))
        self.assertEqual(['bytes=1000-'], host.requests)

    def test_a_host_that_ignores_range_restarts_cleanly(self):
        destination = self._destination()
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with open(destination + install.PART_SUFFIX, 'wb') as stream:
            stream.write(PAYLOAD[:1000])
        host = _Host(honour_range=False)
        path = install.download_file('https://host/f', destination, ENTRY,
                                     opener=host)
        self.assertEqual(PAYLOAD, _read(path))

    def test_a_partial_file_longer_than_the_real_one_is_discarded(self):
        destination = self._destination()
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with open(destination + install.PART_SUFFIX, 'wb') as stream:
            stream.write(PAYLOAD + b'junk')
        host = _Host()
        install.download_file('https://host/f', destination, ENTRY,
                              opener=host)
        self.assertEqual([''], host.requests)

    def test_a_complete_partial_file_is_only_verified(self):
        destination = self._destination()
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with open(destination + install.PART_SUFFIX, 'wb') as stream:
            stream.write(PAYLOAD)
        host = _Host()
        install.download_file('https://host/f', destination, ENTRY,
                              opener=host)
        self.assertEqual([], host.requests)

    def test_a_wrong_checksum_installs_nothing(self):
        host = _Host(payload=bytes(len(PAYLOAD)))
        destination = self._destination()
        self.assertRaises(install.InstallError, install.download_file,
                          'https://host/f', destination, ENTRY,
                          opener=host)
        self.assertFalse(os.path.exists(destination))

    def test_a_truncated_response_keeps_the_partial_file_for_resume(self):
        destination = self._destination()
        host = _Host(truncate_at=1000)
        self.assertRaises(install.InstallError, install.download_file,
                          'https://host/f', destination, ENTRY, opener=host)
        part = destination + install.PART_SUFFIX
        self.assertTrue(os.path.isfile(part))
        self.assertEqual(1000, os.path.getsize(part))

    def test_a_host_sending_far_too_much_is_refused(self):
        host = _Host(extra=b'x' * (install.SIZE_SLACK_BYTES + 16))
        self.assertRaises(install.InstallError, install.download_file,
                          'https://host/f', self._destination(), ENTRY,
                          opener=host)

    def test_a_transport_failure_becomes_an_install_error(self):
        host = _Host(fail=OSError('connection refused'))
        self.assertRaises(install.InstallError, install.download_file,
                          'https://host/f', self._destination(), ENTRY,
                          opener=host)

    def test_a_cancel_stops_within_a_chunk_and_keeps_progress(self):
        destination = self._destination()
        calls = []

        def cancel():
            calls.append(1)
            return len(calls) > 2

        big = _Host(payload=PAYLOAD)
        self.assertRaises(install.InstallCancelled, install.download_file,
                          'https://host/f', destination, ENTRY,
                          cancel=cancel, opener=big)
        self.assertFalse(os.path.exists(destination))

    def test_the_response_is_always_closed(self):
        host = _Host()
        install.download_file('https://host/f', self._destination(), ENTRY,
                              opener=host)
        self.assertTrue(all(r.closed for r in host.responses))


class SourceFallbackTest(_Temp):
    def test_a_dead_first_host_falls_through_to_the_second(self):
        catalogue = _Catalogue()
        attempts = []

        def opener(request, timeout=None):
            attempts.append(request.full_url)
            if 'modelscope' in request.full_url:
                raise OSError('unreachable')
            return _Host()(request, timeout)

        path = install.install_model(catalogue, 'tiny', self.base,
                                     opener=opener)
        self.assertEqual(PAYLOAD, _read(path))
        self.assertEqual(2, len(attempts))
        self.assertIn('modelscope', attempts[0])

    def test_every_host_failing_reports_the_first_failure(self):
        catalogue = _Catalogue()

        def opener(request, timeout=None):
            raise OSError('unreachable')

        with self.assertRaises(install.InstallError) as caught:
            install.install_model(catalogue, 'tiny', self.base, opener=opener)
        self.assertIn('modelscope', str(caught.exception))

    def test_a_cancel_does_not_try_the_next_host(self):
        catalogue = _Catalogue()
        attempts = []

        def opener(request, timeout=None):
            attempts.append(request.full_url)
            return _Host()(request, timeout)

        self.assertRaises(install.InstallCancelled, install.install_model,
                          catalogue, 'tiny', self.base, cancel=lambda: True,
                          opener=opener)
        self.assertEqual([], attempts)

    def test_an_unknown_model_is_refused(self):
        self.assertRaises(install.InstallError, install.install_model,
                          _Catalogue(), 'enormous', self.base)

    def test_an_installed_model_is_not_downloaded_again(self):
        catalogue = _Catalogue()
        attempts = []

        def opener(request, timeout=None):
            attempts.append(request.full_url)
            return _Host()(request, timeout)

        install.install_model(catalogue, 'tiny', self.base, opener=opener)
        install.install_model(catalogue, 'tiny', self.base, opener=opener)
        self.assertEqual(1, len(attempts))


class RuntimeTest(_Temp):
    def _catalogue(self, members=None):
        members = members or (
            ('llama-server.exe', b'MZ server'),
            ('ggml-base.dll', b'MZ ggml'),
            ('llama-cli.exe', b'MZ unwanted tool'),
            ('rpc-server.exe', b'MZ unwanted tool'),
        )
        archive = _runtime_zip(members)
        entry = {'file': 'runtime.zip', 'size': len(archive),
                 'sha256': hashlib.sha256(archive).hexdigest()}
        return _Catalogue(runtime_entry=entry), archive

    def test_only_the_generator_and_its_libraries_are_installed(self):
        catalogue, archive = self._catalogue()
        path = install.install_runtime(catalogue, 'x64', self.base,
                                       opener=_Host(payload=archive))
        root = install.runtime_root(self.base)
        self.assertTrue(os.path.isfile(path))
        self.assertEqual({'llama-server.exe', 'ggml-base.dll'},
                         set(os.listdir(root)))

    def test_the_archive_is_removed_once_unpacked(self):
        catalogue, archive = self._catalogue()
        install.install_runtime(catalogue, 'x64', self.base,
                                opener=_Host(payload=archive))
        self.assertNotIn('runtime.zip',
                         os.listdir(install.runtime_root(self.base)))

    def test_an_archive_without_the_generator_is_refused(self):
        catalogue, archive = self._catalogue(
            members=(('ggml-base.dll', b'MZ ggml'),))
        self.assertRaises(install.InstallError, install.install_runtime,
                          catalogue, 'x64', self.base,
                          opener=_Host(payload=archive))

    def test_a_member_naming_a_path_is_refused(self):
        catalogue, archive = self._catalogue(
            members=(('llama-server.exe', b'MZ'),
                     ('../escape.dll', b'MZ')))
        self.assertRaises(install.InstallError, install.install_runtime,
                          catalogue, 'x64', self.base,
                          opener=_Host(payload=archive))

    def test_an_installed_runtime_is_not_downloaded_again(self):
        catalogue, archive = self._catalogue()
        attempts = []

        def opener(request, timeout=None):
            attempts.append(request.full_url)
            return _Host(payload=archive)(request, timeout)

        install.install_runtime(catalogue, 'x64', self.base, opener=opener)
        install.install_runtime(catalogue, 'x64', self.base, opener=opener)
        self.assertEqual(1, len(attempts))

    def test_a_machine_with_no_published_runtime_is_told_so(self):
        catalogue, unused = self._catalogue()
        self.assertRaises(install.InstallError, install.install_runtime,
                          catalogue, 'arm64', self.base)


class StateTest(_Temp):
    def test_nothing_installed_is_not_ready(self):
        state = install.installation_state(_Catalogue(), 'tiny', 'x64',
                                           self.base)
        self.assertFalse(state['ready'])
        self.assertFalse(state['model_present'])
        self.assertFalse(state['runtime_present'])

    def test_both_halves_present_is_ready(self):
        catalogue, archive = RuntimeTest()._catalogue()
        install.install_model(catalogue, 'tiny', self.base, opener=_Host())
        install.install_runtime(catalogue, 'x64', self.base,
                                opener=_Host(payload=archive))
        state = install.installation_state(catalogue, 'tiny', 'x64',
                                           self.base)
        self.assertTrue(state['ready'])

    def test_one_half_alone_is_not_ready(self):
        catalogue = _Catalogue()
        install.install_model(catalogue, 'tiny', self.base, opener=_Host())
        state = install.installation_state(catalogue, 'tiny', 'x64',
                                           self.base)
        self.assertTrue(state['model_present'])
        self.assertFalse(state['ready'])

    def test_removal_deletes_everything_downloaded(self):
        install.install_model(_Catalogue(), 'tiny', self.base, opener=_Host())
        self.assertTrue(install.remove_installation(self.base))
        self.assertFalse(os.path.isdir(install.install_root(self.base)))
        self.assertFalse(install.remove_installation(self.base))


class MachineTest(unittest.TestCase):
    def test_the_architecture_comes_from_the_catalogue(self):
        self.assertEqual('x64', install.machine_architecture(
            _Catalogue(), 'AMD64'))
        self.assertIsNone(install.machine_architecture(_Catalogue(), 'x86'))

    def test_sizes_read_as_sizes(self):
        self.assertEqual('512 B', install.format_bytes(512))
        self.assertEqual('1.0 KB', install.format_bytes(1024))
        self.assertEqual('610.0 MB', install.format_bytes(610 * 1024 * 1024))


class CatalogueBridgeTest(unittest.TestCase):
    def test_the_launcher_reads_the_server_catalogue(self):
        catalogue = core.bot_chat_catalogue()
        self.assertTrue(catalogue.MODEL_TIERS)
        self.assertIn(catalogue.DEFAULT_TIER_KEY,
                      [entry['key'] for entry in catalogue.MODEL_TIERS])
        self.assertTrue(catalogue.RUNTIME_BUILD)


class ServerEnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='bot-chat-env-')
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.runtime = os.path.join(self.root, 'llama-server.exe')
        self.model = os.path.join(self.root, 'model.gguf')
        for path in (self.runtime, self.model):
            with open(path, 'wb') as stream:
                stream.write(b'x')

    def _environment(self, bot_chat):
        return core.server_environment(
            core.PORT_0_9_22, self.root, environment={},
            bot_chat=bot_chat)

    def test_both_halves_present_names_the_model(self):
        environment = self._environment(
            {'runtime': self.runtime, 'model': self.model})
        self.assertEqual(
            self.runtime,
            environment[core.SERVER_BOT_CHAT_RUNTIME_ENV_0922])
        self.assertEqual(
            self.model, environment[core.SERVER_BOT_CHAT_MODEL_ENV_0922])

    def test_a_missing_half_names_nothing(self):
        for bot_chat in (None, {}, {'runtime': self.runtime},
                         {'model': self.model},
                         {'runtime': self.runtime,
                          'model': os.path.join(self.root, 'absent.gguf')}):
            environment = self._environment(bot_chat)
            self.assertNotIn(core.SERVER_BOT_CHAT_RUNTIME_ENV_0922,
                             environment, bot_chat)
            self.assertNotIn(core.SERVER_BOT_CHAT_MODEL_ENV_0922,
                             environment, bot_chat)

    def test_a_stale_name_is_cleared_rather_than_inherited(self):
        environment = core.server_environment(
            core.PORT_0_9_22, self.root,
            environment={core.SERVER_BOT_CHAT_RUNTIME_ENV_0922: 'old',
                         core.SERVER_BOT_CHAT_MODEL_ENV_0922: 'old'},
            bot_chat=None)
        self.assertNotIn(core.SERVER_BOT_CHAT_RUNTIME_ENV_0922, environment)
        self.assertNotIn(core.SERVER_BOT_CHAT_MODEL_ENV_0922, environment)


if __name__ == '__main__':
    unittest.main()
