import ast
import base64
import hashlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

import core
import preferences_overlay
import server_imports
import stage_payload


class EndpointTest(unittest.TestCase):
    def test_address_without_port_uses_the_default(self):
        self.assertEqual(core.parse_endpoint("192.168.1.10"),
                         ("192.168.1.10", core.DEFAULT_SERVER_PORT))

    def test_address_with_port(self):
        self.assertEqual(core.parse_endpoint(" host.lan:9000 "),
                         ("host.lan", 9000))

    def test_empty_address_is_rejected(self):
        self.assertRaises(core.LauncherError, core.parse_endpoint, "  ")

    def test_port_out_of_range_is_rejected(self):
        self.assertRaises(core.LauncherError, core.parse_endpoint, "host:70000")

    def test_port_text_is_rejected(self):
        self.assertRaises(core.LauncherError, core.parse_endpoint, "host:abc")

    def test_single_player_uses_the_local_endpoint(self):
        for mode in (core.MODE_SINGLE, core.MODE_SINGLE):
            self.assertEqual(core.endpoint_for_mode(mode, "10.0.0.5"),
                             (core.LOCAL_HOST, core.DEFAULT_SERVER_PORT))

    def test_join_uses_the_typed_endpoint(self):
        self.assertEqual(core.endpoint_for_mode(core.MODE_JOIN, "10.0.0.5:1234"),
                         ("10.0.0.5", 1234))


class ServerRequirementTest(unittest.TestCase):
    def test_single_player_needs_a_server(self):
        for port_version in core.SUPPORTED_PORTS:
            self.assertTrue(core.server_required(port_version, core.MODE_SINGLE))

    def test_join_never_starts_a_local_server(self):
        for port_version in core.SUPPORTED_PORTS:
            self.assertFalse(core.server_required(port_version, core.MODE_JOIN))

    def test_single_player_needs_a_server(self):
        for port_version in core.SUPPORTED_PORTS:
            self.assertTrue(
                core.server_required(port_version, core.MODE_SINGLE))


class GameRootTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def _write(self, relative_path, text=""):
        path = os.path.join(self.root, relative_path)
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, "w") as stream:
            stream.write(text)
        return path

    def test_version_file_identifies_the_port(self):
        self._write("version.xml", "<version> v.0.9.22.0.1 #1513 </version>")
        self.assertEqual(core.read_client_version(self.root), "0.9.22.0.1")
        self.assertEqual(core.read_client_identity(self.root),
                         ("0.9.22.0.1", "1513"))
        self.assertEqual(core.detect_port(self.root), core.PORT_0_9_22)

    def test_another_0_9_22_build_uses_the_compatible_port(self):
        self._write("version.xml", "<version> v.0.9.22.0.1 #0789 </version>")
        self._write(
            "mods/0.9.22.0.1/org.peng.offline_lan_0922_0.6.1.wotmod")
        self.assertEqual(core.PORT_0_9_22, core.detect_port(self.root))

    def test_another_0_9_22_patch_uses_the_compatible_port(self):
        self._write("version.xml", "<version> v.0.9.22.1 #1513 </version>")
        self.assertEqual(core.PORT_0_9_22, core.detect_port(self.root))

    def test_unsupported_client_reports_no_port(self):
        self._write("version.xml", "<version> v.1.0.0 #1 </version>")
        self._write(
            "mods/0.9.22.0.1/org.peng.offline_lan_0922_0.6.1.wotmod")
        self.assertIsNone(core.detect_port(self.root))

    def test_an_installed_0_9_22_package_is_a_trusted_version_fallback(self):
        self._write(
            "mods/0.9.22.0.1/org.peng.offline_lan_0922_0.6.1.wotmod")
        self.assertIsNone(core.detect_port(self.root))
        status = core.inspect_game_root(self.root)
        self.assertEqual(core.PORT_0_9_22, status["client"])
        self.assertTrue(status["mod_installed"])

    def test_an_installed_package_recovers_an_unparseable_version_file(self):
        self._write(
            "version.xml",
            "<broken><version>v.0.9.22.0.1 #1513</version>")
        self._write(
            "mods/0.9.22.0.1/org.peng.offline_lan_0922_0.6.1.wotmod")
        self.assertIsNone(core.detect_port(self.root))
        status = core.inspect_game_root(self.root)
        self.assertEqual(core.PORT_0_9_22, status["client"])
        self.assertTrue(status["mod_installed"])

    def test_an_empty_stock_mod_directory_is_not_an_install_marker(self):
        os.makedirs(os.path.join(self.root, "mods", "0.9.22.0.1"))
        self.assertIsNone(core.installed_port(self.root))

    def test_0_8_2_and_its_old_install_marker_are_not_supported(self):
        self._write("version.xml", "<version> v.0.8.2 #100 </version>")
        self._write(core.GAME_EXECUTABLE)
        self._write(os.path.join(
            "res_mods", "0.8.2", "scripts", "client", "gui", "mods",
            "offhangar", "__init__.py"))
        status = core.inspect_game_root(self.root)
        self.assertTrue(status["has_executable"])
        self.assertIsNone(status["client"])
        self.assertFalse(status["mod_installed"])


class SessionPlanTest(unittest.TestCase):
    @staticmethod
    def _status(**overrides):
        status = {
            "path": "C:\\Games\\WoT",
            "has_executable": True,
            "version": "0.9.22.0.1",
            "client": core.PORT_0_9_22,
            "mod_installed": True,
        }
        status.update(overrides)
        return status

    def test_join_plan_carries_the_typed_endpoint(self):
        session = core.plan_session(self._status(), core.MODE_JOIN,
                                    "10.0.0.5:1234")
        self.assertEqual(session["host"], "10.0.0.5")
        self.assertEqual(session["tcp_port"], 1234)
        self.assertFalse(session["needs_server"])

    def test_0_9_22_single_player_plan_starts_a_local_server(self):
        session = core.plan_session(
            self._status(), core.MODE_SINGLE, team_size="7",
            vehicle_profile="Fast MS-1")
        self.assertEqual(session["host"], core.LOCAL_HOST)
        self.assertTrue(session["needs_server"])
        self.assertEqual(7, session["team_size"])
        self.assertEqual("Fast MS-1", session["vehicle_profile"])

    def test_0_9_22_plan_carries_independent_team_sizes_and_preference(self):
        session = core.plan_session(
            self._status(), core.MODE_SINGLE,
            team1_size="3", team2_size="11", preferred_team="2")
        self.assertEqual(11, session["team_size"])
        self.assertEqual(3, session["team1_size"])
        self.assertEqual(11, session["team2_size"])
        self.assertEqual(2, session["preferred_team"])

    def test_vehicle_profile_is_planned_for_online_mode(self):
        # The room host may pin a vehicle profile for an Online battle; the
        # launcher verifies it against the room data at start time.
        session = core.plan_session(
            self._status(), core.MODE_JOIN, "10.0.0.5",
            vehicle_profile="Fast MS-1")
        self.assertEqual("Fast MS-1", session["vehicle_profile"])
        self.assertEqual(core.MODE_JOIN, session["mode"])

    def test_0_9_22_team_size_must_be_between_one_and_fifteen(self):
        for value in ("", "four", 0, 16, 1.5, True):
            with self.assertRaises(core.LauncherError, msg=value):
                core.plan_session(
                    self._status(), core.MODE_SINGLE, team_size=value)

    def test_join_does_not_apply_the_local_team_size(self):
        session = core.plan_session(
            self._status(), core.MODE_JOIN, "10.0.0.5", team_size="invalid")
        self.assertEqual(core.DEFAULT_TEAM_SIZE, session["team_size"])

    def test_join_keeps_team_preference_without_applying_local_capacities(self):
        session = core.plan_session(
            self._status(), core.MODE_JOIN, "10.0.0.5",
            team1_size="invalid", team2_size="invalid", preferred_team="1")
        self.assertEqual(core.DEFAULT_TEAM_SIZE, session["team1_size"])
        self.assertEqual(core.DEFAULT_TEAM_SIZE, session["team2_size"])
        self.assertEqual(1, session["preferred_team"])

    def test_preferred_team_must_be_auto_one_or_two(self):
        for value in ("three", 3, -1, 1.5, True):
            with self.assertRaises(core.LauncherError, msg=value):
                core.plan_session(
                    self._status(), core.MODE_JOIN, "10.0.0.5",
                    preferred_team=value)

    def test_a_missing_executable_stops_the_session(self):
        self.assertRaises(core.LauncherError, core.plan_session,
                          self._status(has_executable=False), core.MODE_SINGLE)

    def test_an_unsupported_client_stops_the_session(self):
        self.assertRaises(core.LauncherError, core.plan_session,
                          self._status(client=None), core.MODE_SINGLE)

    def test_an_unknown_mode_stops_the_session(self):
        self.assertRaises(core.LauncherError, core.plan_session,
                          self._status(), "spectate")

    def test_an_invalid_join_address_stops_the_session(self):
        self.assertRaises(core.LauncherError, core.plan_session,
                          self._status(), core.MODE_JOIN, "")

    def test_a_missing_mod_still_plans_a_session(self):
        session = core.plan_session(self._status(mod_installed=False),
                                    core.MODE_SINGLE)
        self.assertTrue(session["needs_server"])


class SettingsFileTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def _read(self, relative_path):
        with open(os.path.join(self.root, relative_path), "rb") as stream:
            return json.load(stream)

    def test_0_9_22_writes_the_user_owned_endpoint(self):
        written = core.write_settings(self.root, core.PORT_0_9_22,
                                      core.MODE_JOIN, "10.0.0.5", 1234)
        endpoint = self._read(os.path.join(
            "mods", "configs", "offline_lan_0922", "server_endpoint.json"))
        self.assertEqual(endpoint, {"schema": 1, "host": "10.0.0.5",
                                    "port": 1234})
        self.assertEqual(len(written), 1)

    def test_0_9_22_name_updates_an_existing_config(self):
        config_path = os.path.join(self.root, "mods", "configs",
                                   "offline_lan_0922", "config.json")
        os.makedirs(os.path.dirname(config_path))
        with open(config_path, "w") as stream:
            json.dump({"schema": 1, "name": "Player", "max_health": 90}, stream)
        core.write_settings(self.root, core.PORT_0_9_22, core.MODE_SINGLE,
                            core.LOCAL_HOST, core.DEFAULT_SERVER_PORT, "Peng")
        config = self._read(os.path.join(
            "mods", "configs", "offline_lan_0922", "config.json"))
        self.assertEqual(config["name"], "Peng")
        self.assertEqual(config["max_health"], 90)

    def test_0_9_22_name_updates_a_windows_read_only_config(self):
        config_path = os.path.join(self.root, "mods", "configs",
                                   "offline_lan_0922", "config.json")
        os.makedirs(os.path.dirname(config_path))
        with open(config_path, "w") as stream:
            json.dump({"schema": 1, "name": "Player", "max_health": 90},
                      stream)
        os.chmod(config_path, stat.S_IREAD)
        original_replace = os.replace
        config_replace_attempts = []

        def windows_replace(source, target):
            if target == config_path:
                config_replace_attempts.append((source, target))
            if target == config_path and len(config_replace_attempts) == 1:
                error = PermissionError(13, "Access is denied", target)
                error.winerror = 5
                raise error
            return original_replace(source, target)

        with mock.patch("core.os.replace", side_effect=windows_replace):
            core.write_settings(
                self.root, core.PORT_0_9_22, core.MODE_SINGLE,
                core.LOCAL_HOST, core.DEFAULT_SERVER_PORT, "Peng")

        config = self._read(os.path.join(
            "mods", "configs", "offline_lan_0922", "config.json"))
        self.assertEqual(config["name"], "Peng")
        self.assertEqual(config["max_health"], 90)
        self.assertEqual(len(config_replace_attempts), 2)
        self.assertFalse(os.path.exists(config_path + ".tmp"))

    def test_unsupported_port_is_rejected(self):
        self.assertRaises(core.LauncherError, core.write_settings, self.root,
                          "0.1.0", core.MODE_SINGLE, core.LOCAL_HOST, 1)


class ProcDumpDownloadTest(unittest.TestCase):
    EXECUTABLE = (
        b"MZ" + b"\x00" * 58 + b"\x80\x00\x00\x00" +
        b"\x00" * 64 + b"PE\x00\x00" + b"\x4c\x01" +
        b"\x00" * 18 + b"\x0b\x01" + b"\x00" * 32)

    class _Response(io.BytesIO):
        def __init__(self, payload, url=None, content_length=None):
            super().__init__(payload)
            if content_length is None:
                content_length = len(payload)
            self.headers = {"Content-Length": str(content_length)}
            self._url = url or core.PROCDUMP_DOWNLOAD_URL

        def geturl(self):
            return self._url

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.path = os.path.join(self.root, "tools", "procdump.exe")
        signature = mock.patch(
            "core._procdump_authenticode_is_trusted", return_value=True)
        self.signature_is_trusted = signature.start()
        self.addCleanup(signature.stop)

    @staticmethod
    def _archive(*entries):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, payload in entries:
                archive.writestr(name, payload)
        return stream.getvalue()

    @staticmethod
    def _mark_members_encrypted(payload):
        payload = bytearray(payload)
        for signature, flags_offset in ((b"PK\x03\x04", 6),
                                        (b"PK\x01\x02", 8)):
            cursor = 0
            while True:
                cursor = payload.find(signature, cursor)
                if cursor < 0:
                    break
                offset = cursor + flags_offset
                flags = int.from_bytes(payload[offset:offset + 2], "little")
                payload[offset:offset + 2] = (flags | 1).to_bytes(2, "little")
                cursor += len(signature)
        return bytes(payload)

    def _opener(self, payload):
        return mock.Mock(return_value=self._Response(payload))

    def test_default_path_lives_beside_launcher_settings_in_user_cache(self):
        settings = os.path.join(self.root, "launcher.json")
        with mock.patch("core.settings_path", return_value=settings):
            path = core.procdump_executable()

        self.assertEqual(self.path, path)

    def test_frozen_bundle_state_does_not_change_the_user_cache_path(self):
        settings = os.path.join(self.root, "launcher.json")
        bundle = os.path.join(self.root, "launcher", "_internal")
        with mock.patch("core.settings_path", return_value=settings), \
                mock.patch.object(core.sys, "_MEIPASS", bundle, create=True):
            path = core.procdump_executable()

        self.assertEqual(self.path, path)
        self.assertFalse(path.startswith(bundle))

    def test_explicit_cache_base_takes_priority_over_bundle_state(self):
        with mock.patch.object(
                core.sys, "_MEIPASS", "/ignored", create=True):
            path = core.procdump_executable(self.root)

        self.assertEqual(self.path, path)

    def test_download_installs_the_exact_root_32_bit_executable(self):
        payload = self._archive(
            ("procdump.exe", self.EXECUTABLE),
            ("procdump64.exe", b"not-the-selected-executable"),
            ("Eula.txt", b"license"))
        opener = self._opener(payload)

        installed = core.download_procdump(self.path, opener=opener)

        self.assertEqual(self.path, installed)
        with open(self.path, "rb") as stream:
            self.assertEqual(self.EXECUTABLE, stream.read())
        opener.assert_called_once_with(
            core.PROCDUMP_DOWNLOAD_URL,
            timeout=core.PROCDUMP_DOWNLOAD_TIMEOUT_SECONDS)

    def test_valid_cached_executable_is_reused_without_a_download(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "wb") as stream:
            stream.write(self.EXECUTABLE)
        opener = mock.Mock(side_effect=AssertionError("must not download"))

        installed = core.download_procdump(self.path, opener=opener)

        self.assertEqual(self.path, installed)
        opener.assert_not_called()

    def test_untrusted_cached_executable_is_not_reused(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "wb") as stream:
            stream.write(self.EXECUTABLE)
        self.signature_is_trusted.return_value = False

        self.assertFalse(core.procdump_is_installed(self.path))

    def test_untrusted_download_is_not_installed(self):
        payload = self._archive(("procdump.exe", self.EXECUTABLE))
        self.signature_is_trusted.return_value = False

        with self.assertRaisesRegex(core.LauncherError, "signature"):
            core.download_procdump(self.path, opener=self._opener(payload))

        self.assertFalse(os.path.exists(self.path))
        self.assertEqual([], [
            name for name in os.listdir(os.path.dirname(self.path))
            if name.startswith(".procdump-")])

    def test_bad_zip_is_rejected(self):
        with self.assertRaisesRegex(core.LauncherError, "valid ZIP"):
            core.download_procdump(
                self.path, opener=self._opener(b"not a zip"))
        self.assertFalse(os.path.exists(self.path))

    def test_duplicate_root_executable_members_are_rejected(self):
        payload = self._archive(
            ("procdump.exe", self.EXECUTABLE),
            ("PROCDUMP.EXE", self.EXECUTABLE))

        with self.assertRaisesRegex(core.LauncherError, "unique procdump.exe"):
            core.download_procdump(self.path, opener=self._opener(payload))
        self.assertFalse(os.path.exists(self.path))

    def test_non_pe_executable_is_rejected(self):
        payload = self._archive(("procdump.exe", b"not-a-pe-executable"))

        with self.assertRaisesRegex(core.LauncherError, "executable is invalid"):
            core.download_procdump(self.path, opener=self._opener(payload))
        self.assertFalse(os.path.exists(self.path))

    def test_64_bit_pe_executable_is_rejected(self):
        executable = bytearray(self.EXECUTABLE)
        executable[132:134] = b"\x64\x86"
        executable[152:154] = b"\x0b\x02"
        payload = self._archive(("procdump.exe", bytes(executable)))

        with self.assertRaises(core.LauncherError):
            core.download_procdump(self.path, opener=self._opener(payload))
        self.assertFalse(os.path.exists(self.path))

    def test_redirect_away_from_official_https_host_is_rejected(self):
        payload = self._archive(("procdump.exe", self.EXECUTABLE))
        response = self._Response(
            payload, url="https://downloads.example.test/Procdump.zip")

        with self.assertRaises(core.LauncherError):
            core.download_procdump(
                self.path, opener=mock.Mock(return_value=response))
        self.assertFalse(os.path.exists(self.path))

    def test_oversized_download_is_rejected(self):
        with mock.patch.object(core, "PROCDUMP_ARCHIVE_MAX_BYTES", 4):
            with self.assertRaisesRegex(
                    core.LauncherError, "download is unexpectedly large"):
                core.download_procdump(
                    self.path, opener=self._opener(b"12345"))
        self.assertFalse(os.path.exists(self.path))

    def test_oversized_declared_download_is_rejected(self):
        payload = self._archive(("procdump.exe", self.EXECUTABLE))
        response = self._Response(
            payload, content_length=core.PROCDUMP_ARCHIVE_MAX_BYTES + 1)

        with self.assertRaises(core.LauncherError):
            core.download_procdump(
                self.path, opener=mock.Mock(return_value=response))
        self.assertFalse(os.path.exists(self.path))

    def test_oversized_executable_member_is_rejected_before_extraction(self):
        payload = self._archive(("procdump.exe", self.EXECUTABLE))

        with mock.patch.object(core, "PROCDUMP_EXECUTABLE_MAX_BYTES", 8):
            with self.assertRaisesRegex(
                    core.LauncherError, "executable has an invalid size"):
                core.download_procdump(
                    self.path, opener=self._opener(payload))
        self.assertFalse(os.path.exists(self.path))

    def test_encrypted_executable_member_is_rejected(self):
        payload = self._mark_members_encrypted(
            self._archive(("procdump.exe", self.EXECUTABLE)))

        with self.assertRaises(core.LauncherError):
            core.download_procdump(self.path, opener=self._opener(payload))
        self.assertFalse(os.path.exists(self.path))

    def test_failed_atomic_install_does_not_overwrite_existing_file(self):
        os.makedirs(os.path.dirname(self.path))
        original = b"existing invalid cache"
        with open(self.path, "wb") as stream:
            stream.write(original)
        payload = self._archive(("procdump.exe", self.EXECUTABLE))

        with mock.patch("core.os.replace", side_effect=OSError("locked")):
            with self.assertRaises(core.LauncherError):
                core.download_procdump(
                    self.path, opener=self._opener(payload))

        with open(self.path, "rb") as stream:
            self.assertEqual(original, stream.read())
        self.assertEqual([], [
            name for name in os.listdir(os.path.dirname(self.path))
            if name.startswith(".procdump-")])


class ServerPayloadTest(unittest.TestCase):

    def test_launcher_log_lives_beside_settings(self):
        settings = os.path.join(tempfile.gettempdir(), "state", "launcher.json")
        with mock.patch.object(core, "settings_path", return_value=settings):
            self.assertEqual(
                os.path.join(os.path.dirname(settings), "launcher.log"),
                core.launcher_log_path())

    def test_server_log_lives_beside_the_frozen_launcher(self):
        executable = os.path.join(
            tempfile.gettempdir(), "portable-launcher", "Launcher.exe")

        self.assertEqual(
            os.path.join(os.path.dirname(executable), "server.log"),
            core.server_log_path(executable=executable, frozen=True))

    def test_source_server_log_lives_beside_the_launcher_script(self):
        self.assertEqual(
            os.path.join(os.path.dirname(core.__file__), "server.log"),
            core.server_log_path(frozen=False))

    def test_repository_layout_resolves_the_server(self):
        script = core.server_script(core.PORT_0_9_22)
        self.assertTrue(os.path.isfile(script))
        self.assertEqual(
            os.path.join(core.repository_root(), "server", "windows_server.py"),
            script)

    def test_0_9_22_server_binds_without_arguments(self):
        argv = core.server_argv(core.PORT_0_9_22, "/payload")
        self.assertEqual(argv[1:], [])

    def test_frozen_command_reruns_the_launcher_executable(self):
        command = core.server_child_command(
            core.PORT_0_9_22, executable="C:\\launcher.exe", frozen=True)
        self.assertEqual(command, ["C:\\launcher.exe", core.SERVE_FLAG,
                                   core.PORT_0_9_22])

    def test_source_command_passes_the_launcher_script(self):
        command = core.server_child_command(
            core.PORT_0_9_22, launcher_script="/repo/launcher/wot_launcher.py",
            executable="/usr/bin/python3", frozen=False)
        self.assertEqual(command, ["/usr/bin/python3",
                                   "/repo/launcher/wot_launcher.py",
                                   core.SERVE_FLAG, core.PORT_0_9_22])

    def test_server_environment_keeps_only_live_server_inputs(self):
        environment = core.server_environment(core.PORT_0_9_22, "/game", {})
        self.assertEqual(
            str(core.DEFAULT_TEAM_SIZE),
            environment[core.SERVER_TEAM_SIZE_ENV_0922])
        self.assertEqual("[]", environment[core.SERVER_BOT_LINEUP_ENV_0922])

    def test_server_and_clients_receive_the_bundled_diagnostic_identity(self):
        identity = {
            "schema": 1,
            "semanticVersion": "0.6.1",
            "buildIdentity": "test-build-a",
        }
        with mock.patch.object(
                core, "bundled_payload_identity", return_value=identity):
            environments = (
                core.server_environment(
                    core.PORT_0_9_22, "/game", {}),
                core.worker_environment("/game", environment={}),
                core.visible_client_environment(
                    core.PORT_0_9_22, environment={}),
            )

        for environment in environments:
            self.assertEqual(
                "0.6.1", environment[core.BUILD_SEMANTIC_VERSION_ENV])
            self.assertEqual(
                "test-build-a", environment[core.BUILD_IDENTITY_ENV])

    def test_0_9_22_server_receives_the_selected_team_size(self):
        environment = core.server_environment(
            core.PORT_0_9_22, "/game", {}, team_size=4)
        self.assertEqual("4", environment[core.SERVER_TEAM_SIZE_ENV_0922])

    def test_0_9_22_server_receives_independent_team_sizes(self):
        environment = core.server_environment(
            core.PORT_0_9_22, "/game", {},
            team1_size=4, team2_size=9)
        self.assertEqual("9", environment[core.SERVER_TEAM_SIZE_ENV_0922])
        self.assertEqual("4", environment[core.SERVER_TEAM1_SIZE_ENV_0922])
        self.assertEqual("9", environment[core.SERVER_TEAM2_SIZE_ENV_0922])

    def test_0_9_22_server_receives_the_selected_bot_lineup(self):
        environment = core.server_environment(
            core.PORT_0_9_22, "/game", {}, bot_lineup=[{
                "team": 1, "slot": 2,
                "vehicle": "ussr:R11_MS-1",
            }])
        self.assertEqual(
            '[{"team":1,"slot":2,"vehicle":"ussr:R11_MS-1"}]',
            environment[core.SERVER_BOT_LINEUP_ENV_0922])

    def test_single_player_server_is_explicitly_loopback_only(self):
        environment = core.server_environment(
            core.PORT_0_9_22, "/game", {}, loopback_only=True)
        self.assertEqual(
            "1", environment[core.SERVER_LOOPBACK_ONLY_ENV_0922])
        lan_environment = core.server_environment(
            core.PORT_0_9_22, "/game",
            {core.SERVER_LOOPBACK_ONLY_ENV_0922: "1"})
        self.assertNotIn(core.SERVER_LOOPBACK_ONLY_ENV_0922, lan_environment)

    def test_hidden_worker_inherits_the_selected_server_endpoint(self):
        environment = core.worker_environment(
            "/game", "10.0.0.5", 1234, team_size=7, environment={})
        self.assertEqual(
            "10.0.0.5", environment[core.CLIENT_SERVER_HOST_ENV_0922])
        self.assertEqual(
            "1234", environment[core.CLIENT_SERVER_PORT_ENV_0922])
        self.assertEqual("7", environment[core.SERVER_TEAM_SIZE_ENV_0922])
        self.assertEqual(
            [os.path.join("/game", core.WORKER_STARTER_FILENAME_0922),
             core.WORKER_ONLY_ARGUMENT_0922],
            core.worker_child_command("/game"))
        self.assertEqual(
            [os.path.join("/game", core.WORKER_STARTER_FILENAME_0922),
             core.STOP_STARTER_ARGUMENT_0922, "42"],
            core.starter_stop_command("/game", 42))
        with self.assertRaisesRegex(
                core.LauncherError, "process identifier is invalid"):
            core.starter_stop_command("/game", 0)

    def test_hidden_worker_inherits_independent_team_sizes(self):
        environment = core.worker_environment(
            "/game", team1_size=2, team2_size=8, environment={})
        self.assertEqual("2", environment[core.SERVER_TEAM1_SIZE_ENV_0922])
        self.assertEqual("8", environment[core.SERVER_TEAM2_SIZE_ENV_0922])

    def test_visible_0_9_22_client_uses_isolated_config_and_endpoint(self):
        command = core.visible_client_command("/game", core.PORT_0_9_22)
        self.assertEqual(
            [os.path.join("/game", core.WORKER_STARTER_FILENAME_0922),
             core.PLAYER_ARGUMENT_0922],
            command)
        self.assertEqual(
            [os.path.join("/game", core.WORKER_STARTER_FILENAME_0922),
             core.PAIRED_PLAYER_ARGUMENT_0922],
            core.visible_client_command(
                "/game", core.PORT_0_9_22, paired_worker=True))
        environment = core.visible_client_environment(
            core.PORT_0_9_22, "10.0.0.5", 1234, paired_worker=True,
            environment={
                core.CLIENT_MODE_ENV_0922: "simulation_worker",
                core.HIDDEN_DESKTOP_ENV_0922: "1",
                core.WORKER_READY_MARKER_ENV_0922: "stale",
            })
        self.assertEqual(
            "10.0.0.5", environment[core.CLIENT_SERVER_HOST_ENV_0922])
        self.assertEqual(
            "1234", environment[core.CLIENT_SERVER_PORT_ENV_0922])
        self.assertEqual(
            "1", environment[core.ALLOW_MULTIPLE_CLIENTS_ENV_0922])
        self.assertEqual(
            core.PLAYER_MODE_0922,
            environment[core.CLIENT_MODE_ENV_0922])
        for name in (core.HIDDEN_DESKTOP_ENV_0922,
                     core.WORKER_READY_MARKER_ENV_0922):
            self.assertNotIn(name, environment)

    def test_visible_client_carries_the_preferred_team(self):
        environment = core.visible_client_environment(
            core.PORT_0_9_22, environment={}, preferred_team=2)
        self.assertEqual(
            "2", environment[core.CLIENT_PREFERRED_TEAM_ENV_0922])

    def test_missing_payload_reports_a_launcher_error(self):
        self.assertRaises(core.LauncherError, core.run_server_payload,
                          core.PORT_0_9_22, tempfile.mkdtemp())


class ClientInstallTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.work, True)
        self.game = os.path.join(self.work, "game")
        self.payload = os.path.join(self.work, "payload")
        os.makedirs(self.game)

    def _write(self, root, relative_path, text="x"):
        path = os.path.join(root, *relative_path.split("/"))
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, "w") as stream:
            stream.write(text)
        return path

    def _read(self, relative_path):
        with open(os.path.join(self.game, *relative_path.split("/"))) as stream:
            return stream.read()

    def _archive(self, port_version, members):
        directory = os.path.join(self.payload, core.CLIENT_PAYLOAD_DIR)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        path = os.path.join(directory, "%s.zip" % port_version)
        with zipfile.ZipFile(path, "w") as archive:
            for member, content in members.items():
                archive.writestr(member, content)
        return path

    def _stage_0_9_22(self, content="new", build_identity="test-build-a"):
        members = {
            "mods/0.9.22.0.1/org.peng.offline_lan_0922_9.9.9.wotmod": content,
            "mods/0.9.22.0.1/offline_instance_guard_native.pyd": content,
            "mods/configs/offline_lan_0922/config.json": content,
            core.BUILD_IDENTITY_RELATIVE_PATH_0922: json.dumps({
                "schema": 1,
                "semanticVersion": "0.6.1",
                "buildIdentity": build_identity,
            }),
            "offline_worker_starter.exe": content,
            "res_mods/0.9.22.0.1/engine_config.offline-player.xml": content,
            "res_mods/0.9.22.0.1/engine_config.offline-worker.xml": content,
        }
        for name in ("navgraphs", "foliage", "destructibles"):
            records = []
            for index in range(41):
                filename = "map-%02d.json" % index
                records.append({"file": filename})
                members[
                    "mods/configs/offline_lan_0922/%s/%s" %
                    (name, filename)
                ] = content
            members[
                "mods/configs/offline_lan_0922/%s/manifest.json" % name
            ] = json.dumps({"maps": records})
        return self._archive("0.9.22", members)

    def _make_0_9_22_target(self):
        self._write(self.game, core.GAME_EXECUTABLE, "")
        self._write(
            self.game, "version.xml",
            "<version> v.0.9.22.0.1 #1513 </version>")
        engine_config = preferences_overlay.packed_xml.PackedElement(children=[
            (b"preferences", preferences_overlay.packed_xml.PackedValue(
                preferences_overlay.packed_xml.TYPE_STRING,
                b"preferences.xml")),
        ])
        path = os.path.join(self.game, "res", "engine_config.xml")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as stream:
            stream.write(preferences_overlay.packed_xml.write_packed_xml(
                engine_config))

    def test_0_9_22_install_replaces_old_packages_and_data(self):
        self._stage_0_9_22()
        self._write(self.game,
                    "mods/0.9.22.0.1/org.peng.offline_lan_0922_0.1.0.wotmod",
                    "stale")
        core.install_client_mod(self.game, core.PORT_0_9_22, self.payload)
        self.assertFalse(os.path.exists(os.path.join(
            self.game, "mods", "0.9.22.0.1",
            "org.peng.offline_lan_0922_0.1.0.wotmod")))
        self.assertEqual("new", self._read(
            "mods/0.9.22.0.1/org.peng.offline_lan_0922_9.9.9.wotmod"))

    def test_0_9_22_install_removes_stale_baked_data(self):
        self._stage_0_9_22()
        self._write(self.game,
                    "mods/configs/offline_lan_0922/navgraphs/stale.json",
                    "stale")
        core.install_client_mod(self.game, core.PORT_0_9_22, self.payload)
        self.assertFalse(os.path.exists(os.path.join(
            self.game, "mods", "configs", "offline_lan_0922", "navgraphs",
            "stale.json")))

    def test_0_9_22_install_keeps_another_authors_mod(self):
        self._stage_0_9_22()
        self._write(self.game, "mods/0.9.22.0.1/com.other.mod.wotmod", "theirs")
        core.install_client_mod(self.game, core.PORT_0_9_22, self.payload)
        self.assertEqual("theirs",
                         self._read("mods/0.9.22.0.1/com.other.mod.wotmod"))

    def test_0_9_22_install_keeps_the_saved_settings(self):
        self._stage_0_9_22()
        self._write(self.game,
                    "mods/configs/offline_lan_0922/server_endpoint.json", "mine")
        self._write(self.game, "mods/configs/offline_lan_0922/config.json",
                    "mine")
        core.install_client_mod(self.game, core.PORT_0_9_22, self.payload)
        self.assertEqual("mine", self._read(
            "mods/configs/offline_lan_0922/server_endpoint.json"))
        self.assertEqual("mine", self._read(
            "mods/configs/offline_lan_0922/config.json"))

    def test_0_9_22_install_writes_a_missing_configuration(self):
        self._stage_0_9_22()
        core.install_client_mod(self.game, core.PORT_0_9_22, self.payload)
        self.assertEqual("new", self._read(
            "mods/configs/offline_lan_0922/config.json"))

    def test_startup_repair_quarantines_only_an_invalid_config(self):
        default_config = json.dumps({
            "enabled": True,
            "startupTimeoutSeconds": 30.0,
            "physics_tuning": {},
            "he_tuning": {},
            "perfect_accuracy": False,
        })
        self._stage_0_9_22(default_config)
        self._make_0_9_22_target()
        self._write(
            self.game, "mods/configs/offline_lan_0922/config.json",
            "{broken")
        for name in ("server_endpoint.json", "account_state.json",
                     "garage_state.json"):
            self._write(
                self.game, "mods/configs/offline_lan_0922/" + name,
                "saved-" + name)
        postbattle = json.dumps({
            "schema": 1,
            "accountKey": "offline",
            "pending": [{"arenaUniqueID": 17}],
            "history": [],
            "progress": {},
        })
        self._write(
            self.game,
            "mods/configs/offline_lan_0922/postbattle_state.json",
            postbattle)
        self._write(
            self.game, "mods/0.9.22.0.1/com.other.mod.wotmod", "theirs")

        actions = core.repair_0_9_22_startup(
            self.game, self.payload, is_running=lambda: False)

        self.assertEqual(default_config, self._read(
            "mods/configs/offline_lan_0922/config.json"))
        self.assertEqual("{broken", self._read(
            "mods/configs/offline_lan_0922/config.json.invalid"))
        for name in ("server_endpoint.json", "account_state.json",
                     "garage_state.json"):
            self.assertEqual("saved-" + name, self._read(
                "mods/configs/offline_lan_0922/" + name))
        self.assertEqual(postbattle, self._read(
            "mods/configs/offline_lan_0922/postbattle_state.json"))
        self.assertEqual("theirs", self._read(
            "mods/0.9.22.0.1/com.other.mod.wotmod"))
        self.assertIn("kept the saved endpoint", " ".join(actions))

    def test_startup_repair_keeps_a_valid_config(self):
        default_config = json.dumps({"enabled": True})
        self._stage_0_9_22(default_config)
        self._make_0_9_22_target()
        saved_config = json.dumps({
            "enabled": False, "startupTimeoutSeconds": 45.0})
        self._write(
            self.game, "mods/configs/offline_lan_0922/config.json",
            saved_config)

        core.repair_0_9_22_startup(
            self.game, self.payload, is_running=lambda: False)

        self.assertEqual(saved_config, self._read(
            "mods/configs/offline_lan_0922/config.json"))
        self.assertFalse(os.path.exists(os.path.join(
            self.game, "mods", "configs", "offline_lan_0922",
            "config.json.invalid")))

    def test_startup_repair_refuses_to_touch_a_running_game(self):
        self._stage_0_9_22(json.dumps({"enabled": True}))
        self._make_0_9_22_target()
        self._write(
            self.game, "mods/configs/offline_lan_0922/config.json",
            "{broken")

        with self.assertRaisesRegex(core.LauncherError, "Close World of Tanks"):
            core.repair_0_9_22_startup(
                self.game, self.payload, is_running=lambda: True)

        self.assertEqual("{broken", self._read(
            "mods/configs/offline_lan_0922/config.json"))

    def test_failed_startup_repair_restores_the_invalid_config(self):
        self._make_0_9_22_target()
        self._write(
            self.game, "mods/configs/offline_lan_0922/config.json",
            "{broken")

        def fail_install(game_root, port_version, base_dir, force):
            self._write(
                self.game, "mods/configs/offline_lan_0922/config.json",
                "partial-default")
            raise core.LauncherError("install failed")

        with mock.patch.object(
                core, "install_client_mod", side_effect=fail_install):
            with self.assertRaisesRegex(core.LauncherError, "install failed"):
                core.repair_0_9_22_startup(
                    self.game, self.payload, is_running=lambda: False)

        self.assertEqual("{broken", self._read(
            "mods/configs/offline_lan_0922/config.json"))
        self.assertFalse(os.path.exists(os.path.join(
            self.game, "mods", "configs", "offline_lan_0922",
            "config.json.invalid")))

    def test_normal_client_preferences_are_moved_to_a_recoverable_backup(self):
        self._make_0_9_22_target()
        app_data = os.path.join(self.work, "app-data")
        preferences = os.path.join(
            app_data,
            *preferences_overlay.NORMAL_PROFILE_RELATIVE_PATH.split("/"))
        os.makedirs(os.path.dirname(preferences))
        preferences = preferences_overlay.normal_profile_path(
            {"APPDATA": app_data})
        self.assertIsNotNone(preferences)
        with open(preferences, "w") as stream:
            stream.write("normal client settings")
        first_backup = preferences + ".wot-offline-backup-20260822-120000"
        with open(first_backup, "w") as stream:
            stream.write("older backup")

        actions = core.backup_normal_client_preferences(
            self.game, is_running=lambda: False,
            environment={"APPDATA": app_data},
            timestamp="20260822-120000")

        backup = first_backup + "-1"
        self.assertFalse(os.path.lexists(preferences))
        with open(first_backup) as stream:
            self.assertEqual("older backup", stream.read())
        with open(backup) as stream:
            self.assertEqual("normal client settings", stream.read())
        self.assertIn(backup, actions[0])

    def test_normal_client_preferences_cleanup_is_idempotent(self):
        self._make_0_9_22_target()
        app_data = os.path.join(self.work, "empty-app-data")

        actions = core.backup_normal_client_preferences(
            self.game, is_running=lambda: False,
            environment={"APPDATA": app_data},
            timestamp="20260822-120000")

        self.assertIn("already absent", actions[0])

    def test_normal_client_preferences_cleanup_refuses_a_file_link(self):
        self._make_0_9_22_target()
        app_data = os.path.join(self.work, "linked-app-data")
        preferences = os.path.join(
            app_data,
            *preferences_overlay.NORMAL_PROFILE_RELATIVE_PATH.split("/"))
        os.makedirs(os.path.dirname(preferences))
        target = os.path.join(self.work, "outside-preferences.xml")
        with open(target, "w") as stream:
            stream.write("outside")
        try:
            os.symlink(target, preferences)
        except (AttributeError, NotImplementedError, OSError):
            self.skipTest("symlinks are unavailable")

        with self.assertRaisesRegex(core.LauncherError, "regular file"):
            core.backup_normal_client_preferences(
                self.game, is_running=lambda: False,
                environment={"APPDATA": app_data},
                timestamp="20260822-120000")

        self.assertTrue(os.path.islink(preferences))
        with open(target) as stream:
            self.assertEqual("outside", stream.read())

    def test_reset_deletes_only_known_offline_state_after_confirmation(self):
        default_config = json.dumps({"enabled": True})
        self._stage_0_9_22(default_config)
        self._make_0_9_22_target()
        state_root = "mods/configs/offline_lan_0922/"
        for name in ("config.json", "config.json.invalid",
                     "server_endpoint.json", "server_endpoint.json.tmp",
                     "account_state.json", "garage_state.json.bak",
                     "postbattle_state.json"):
            self._write(self.game, state_root + name, "saved-" + name)
        self._write(self.game, state_root + "notes.json", "keep")
        self._write(
            self.game, state_root + "vehicle_profiles.json", "profiles")
        self._write(
            self.game, "mods/0.9.22.0.1/com.other.mod.wotmod", "theirs")

        actions = core.reset_0_9_22_state(
            self.game, self.payload, is_running=lambda: False)

        self.assertEqual(default_config, self._read(state_root + "config.json"))
        for name in ("config.json.invalid", "server_endpoint.json",
                     "server_endpoint.json.tmp", "account_state.json",
                     "garage_state.json.bak", "postbattle_state.json"):
            self.assertFalse(os.path.exists(os.path.join(
                self.game, *(state_root + name).split("/"))))
        self.assertEqual("keep", self._read(state_root + "notes.json"))
        self.assertEqual(
            "profiles", self._read(state_root + "vehicle_profiles.json"))
        self.assertEqual("theirs", self._read(
            "mods/0.9.22.0.1/com.other.mod.wotmod"))
        self.assertIn("Deleted 7 offline saved-data file(s).", actions)

    def test_reset_also_deletes_only_the_isolated_client_preferences(self):
        default_config = json.dumps({"enabled": True})
        self._stage_0_9_22(default_config)
        self._make_0_9_22_target()
        preferences = os.path.join(
            self.work, "local-app-data",
            *preferences_overlay.PROFILE_RELATIVE_PATH.split("/"))
        os.makedirs(os.path.dirname(preferences))
        with open(preferences, "w") as stream:
            stream.write("offline graphics and input settings")

        with mock.patch.object(
                core, "_isolated_0_9_22_preferences_path",
                return_value=preferences):
            actions = core.reset_0_9_22_state(
                self.game, self.payload, is_running=lambda: False)

        self.assertFalse(os.path.exists(preferences))
        self.assertIn("Deleted 1 offline saved-data file(s).", actions)

    def test_failed_reset_restores_the_isolated_client_preferences(self):
        self._make_0_9_22_target()
        preferences = os.path.join(
            self.work, "local-app-data",
            *preferences_overlay.PROFILE_RELATIVE_PATH.split("/"))
        os.makedirs(os.path.dirname(preferences))
        with open(preferences, "w") as stream:
            stream.write("keep me")

        with mock.patch.object(
                core, "_isolated_0_9_22_preferences_path",
                return_value=preferences), mock.patch.object(
                    core, "install_client_mod",
                    side_effect=core.LauncherError("install failed")):
            with self.assertRaisesRegex(core.LauncherError, "install failed"):
                core.reset_0_9_22_state(
                    self.game, self.payload, is_running=lambda: False)

        with open(preferences) as stream:
            self.assertEqual("keep me", stream.read())

    def test_failed_reset_restores_every_saved_file(self):
        self._make_0_9_22_target()
        state_root = "mods/configs/offline_lan_0922/"
        for name in ("config.json", "server_endpoint.json",
                     "garage_state.json", "postbattle_state.json"):
            self._write(self.game, state_root + name, "saved-" + name)

        with mock.patch.object(
                core, "install_client_mod",
                side_effect=core.LauncherError("install failed")):
            with self.assertRaisesRegex(core.LauncherError, "install failed"):
                core.reset_0_9_22_state(
                    self.game, self.payload, is_running=lambda: False)

        for name in ("config.json", "server_endpoint.json",
                     "garage_state.json", "postbattle_state.json"):
            self.assertEqual("saved-" + name,
                             self._read(state_root + name))

    def test_same_semantic_version_with_a_new_build_identity_reinstalls(self):
        package = (
            "mods/0.9.22.0.1/org.peng.offline_lan_0922_9.9.9.wotmod")
        self._stage_0_9_22(content="first", build_identity="test-build-a")
        core.install_client_mod(self.game, core.PORT_0_9_22, self.payload)
        self._stage_0_9_22(content="second", build_identity="test-build-b")

        actions = core.install_client_mod(
            self.game, core.PORT_0_9_22, self.payload)

        self.assertEqual("second", self._read(package))
        self.assertIn("build=test-build-a", " ".join(actions))
        self.assertIn("build=test-build-b", " ".join(actions))
        self.assertIn("Install decision: reinstall", " ".join(actions))

    def test_same_build_identity_keeps_the_complete_0_9_22_install(self):
        self._stage_0_9_22(build_identity="test-build-a")
        core.install_client_mod(self.game, core.PORT_0_9_22, self.payload)

        actions = core.install_client_mod(
            self.game, core.PORT_0_9_22, self.payload)

        self.assertIn("Install decision: keep", " ".join(actions))
        self.assertEqual({
            "schema": 1,
            "semanticVersion": "0.6.1",
            "buildIdentity": "test-build-a",
        }, core.installed_payload_identity(
            self.game, core.PORT_0_9_22))

    def test_a_missing_required_file_forces_a_reinstall(self):
        self._stage_0_9_22()
        core.install_client_mod(self.game, core.PORT_0_9_22, self.payload)
        manifest = os.path.join(
            self.game, "mods", "configs", "offline_lan_0922", "destructibles",
            "manifest.json")
        os.unlink(manifest)

        actions = core.install_client_mod(
            self.game, core.PORT_0_9_22, self.payload)

        self.assertTrue(os.path.isfile(manifest))
        self.assertNotIn("already up to date", " ".join(actions))

    def test_a_missing_manifest_referenced_map_forces_a_reinstall(self):
        self._stage_0_9_22()
        core.install_client_mod(self.game, core.PORT_0_9_22, self.payload)
        map_path = os.path.join(
            self.game, "mods", "configs", "offline_lan_0922", "destructibles",
            "map-17.json")
        os.unlink(map_path)

        actions = core.install_client_mod(
            self.game, core.PORT_0_9_22, self.payload)

        self.assertTrue(os.path.isfile(map_path))
        self.assertNotIn("already up to date", " ".join(actions))

    def test_an_archive_missing_a_manifest_referenced_map_is_rejected(self):
        archive_path = self._stage_0_9_22()
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = {
                name: archive.read(name) for name in archive.namelist()
                if name != ("mods/configs/offline_lan_0922/destructibles/"
                            "map-17.json")
            }
        self._archive(core.PORT_0_9_22, members)
        self._write(
            self.game,
            "mods/0.9.22.0.1/org.peng.offline_lan_0922_old.wotmod",
            "previous")

        self.assertRaises(core.LauncherError, core.install_client_mod,
                          self.game, core.PORT_0_9_22, self.payload)

        self.assertEqual("previous", self._read(
            "mods/0.9.22.0.1/org.peng.offline_lan_0922_old.wotmod"))

    def test_a_malformed_0_9_22_manifest_archive_is_rejected(self):
        archive_path = self._stage_0_9_22()
        manifest_name = (
            "mods/configs/offline_lan_0922/destructibles/manifest.json")
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = {name: archive.read(name)
                       for name in archive.namelist()}
        members[manifest_name] = "not json"
        self._archive(core.PORT_0_9_22, members)

        self.assertRaises(core.LauncherError, core.install_client_mod,
                          self.game, core.PORT_0_9_22, self.payload)

    def test_a_malformed_build_identity_is_soft_and_forces_reinstall(self):
        package = (
            "mods/0.9.22.0.1/org.peng.offline_lan_0922_9.9.9.wotmod")
        self._stage_0_9_22(content="first", build_identity="test-build-a")
        core.install_client_mod(self.game, core.PORT_0_9_22, self.payload)
        archive_path = self._stage_0_9_22()
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = {name: archive.read(name)
                       for name in archive.namelist()}
        members[package] = "second"
        members[core.BUILD_IDENTITY_RELATIVE_PATH_0922] = json.dumps({
            "schema": 1,
            "semanticVersion": "0.6.1",
            "buildIdentity": "not a valid identity",
        })
        self._archive(core.PORT_0_9_22, members)

        actions = core.install_client_mod(
            self.game, core.PORT_0_9_22, self.payload)

        self.assertEqual("second", self._read(package))
        self.assertIsNone(core.installed_payload_identity(
            self.game, core.PORT_0_9_22))
        self.assertIn("build identity is unavailable", " ".join(actions))
        self.assertIn("Install decision: reinstall", " ".join(actions))

    def test_a_missing_build_identity_is_soft_and_removes_the_stale_one(self):
        self._stage_0_9_22(content="first", build_identity="test-build-a")
        core.install_client_mod(self.game, core.PORT_0_9_22, self.payload)
        archive_path = self._stage_0_9_22(content="second")
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = {
                name: archive.read(name) for name in archive.namelist()
                if name != core.BUILD_IDENTITY_RELATIVE_PATH_0922
            }
        self._archive(core.PORT_0_9_22, members)

        actions = core.install_client_mod(
            self.game, core.PORT_0_9_22, self.payload)

        self.assertFalse(os.path.exists(os.path.join(
            self.game, *core.BUILD_IDENTITY_RELATIVE_PATH_0922.split("/"))))
        self.assertIsNone(core.installed_payload_identity(
            self.game, core.PORT_0_9_22))
        self.assertIn("build identity is unavailable", " ".join(actions))
        self.assertIn("Install decision: reinstall", " ".join(actions))

    def test_a_forced_install_ignores_the_marker(self):
        self._stage_0_9_22()
        core.install_client_mod(self.game, core.PORT_0_9_22, self.payload)
        stale = "mods/configs/offline_lan_0922/navgraphs/stale.json"
        self._write(self.game, stale, "stale")

        core.install_client_mod(self.game, core.PORT_0_9_22, self.payload,
                                force=True)

        self.assertFalse(os.path.exists(os.path.join(
            self.game, *stale.split("/"))))

    def test_a_launcher_without_mod_files_reports_it(self):
        self.assertRaises(core.LauncherError, core.install_client_mod,
                          self.game, core.PORT_0_9_22, self.payload)

    def test_a_member_outside_the_game_folder_is_refused(self):
        self._archive(core.PORT_0_9_22, {"../escape.txt": "no"})
        self.assertRaises(core.LauncherError, core.install_client_mod,
                          self.game, core.PORT_0_9_22, self.payload)

    def test_an_unexpected_payload_file_type_is_refused(self):
        archive_path = self._stage_0_9_22()
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = {name: archive.read(name)
                       for name in archive.namelist()}
        members["mods/configs/offline_lan_0922/development.py"] = "no"
        self._archive(core.PORT_0_9_22, members)
        self.assertRaises(core.LauncherError, core.install_client_mod,
                          self.game, core.PORT_0_9_22, self.payload)

    def test_an_unrelated_0_9_22_mod_is_not_installed_from_the_payload(self):
        archive_path = self._stage_0_9_22()
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = {name: archive.read(name)
                       for name in archive.namelist()}
        members["mods/0.9.22.0.1/com.other.mod.wotmod"] = "no"
        self._archive(core.PORT_0_9_22, members)
        self.assertRaises(core.LauncherError, core.install_client_mod,
                          self.game, core.PORT_0_9_22, self.payload)

    def test_an_invalid_archive_does_not_remove_the_previous_mod(self):
        self._archive(core.PORT_0_9_22, {"../escape.txt": "no"})
        previous = (
            "mods/0.9.22.0.1/org.peng.offline_lan_0922_old.wotmod")
        self._write(self.game, previous, "previous")

        self.assertRaises(core.LauncherError, core.install_client_mod,
                          self.game, core.PORT_0_9_22, self.payload)

        self.assertEqual("previous", self._read(previous))

    def test_a_failed_atomic_swap_restores_the_previous_mod(self):
        self._stage_0_9_22()
        previous = (
            "mods/configs/offline_lan_0922/navgraphs/previous.txt")
        self._write(self.game, previous, "previous")
        original_replace = os.replace

        def replace(source, target):
            normalized = source.replace("\\", "/")
            if ("/.wot-offline-install-" in normalized and
                    "/new/mods/configs/offline_lan_0922/navgraphs" in
                    normalized):
                raise OSError("synthetic install failure")
            return original_replace(source, target)

        with mock.patch("core.os.replace", side_effect=replace):
            self.assertRaises(core.LauncherError, core.install_client_mod,
                              self.game, core.PORT_0_9_22, self.payload)

        self.assertEqual("previous", self._read(previous))

    def test_an_unwritable_game_folder_reports_a_permission_remedy(self):
        self._stage_0_9_22()
        with mock.patch("tempfile.mkdtemp",
                        side_effect=PermissionError("access denied")):
            with self.assertRaises(core.LauncherError) as caught:
                core.install_client_mod(
                    self.game, core.PORT_0_9_22, self.payload)
        self.assertIn("not writable", str(caught.exception))
        self.assertIn("permission", str(caught.exception))


class PayloadStagingTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(tempfile.mkdtemp(), "payload")
        self.addCleanup(shutil.rmtree, os.path.dirname(self.root), True)
        self.written = stage_payload.stage(self.root, include_clients=False)
        self.target = os.path.join(self.root, stage_payload.SERVER_DIR)

    @staticmethod
    def _write_0_9_22_data(overlay):
        runtime_files = (
            "offline_worker_starter.exe",
            "mods/0.9.22.0.1/offline_instance_guard_native.pyd",
            "res_mods/0.9.22.0.1/engine_config.offline-player.xml",
            "res_mods/0.9.22.0.1/engine_config.offline-worker.xml",
        )
        for relative in runtime_files:
            path = os.path.join(overlay, *relative.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as stream:
                stream.write("runtime")
        data_root = os.path.join(
            overlay, "mods", "configs", "offline_lan_0922")
        for dataset in ("navgraphs", "foliage", "destructibles"):
            dataset_root = os.path.join(data_root, dataset)
            os.makedirs(dataset_root)
            records = []
            for index in range(41):
                filename = "map-%02d.json" % index
                records.append({"file": filename})
                with open(os.path.join(dataset_root, filename), "w") as stream:
                    stream.write("{}")
            with open(os.path.join(dataset_root, "manifest.json"), "w") as stream:
                json.dump({"maps": records}, stream)

    def test_supported_server_entry_points_are_staged(self):
        expected_ports = {core.PORT_0_9_22}
        self.assertEqual(expected_ports, set(stage_payload.PAYLOAD_FILES))
        self.assertEqual(expected_ports, set(stage_payload.PAYLOAD_TREES))
        self.assertEqual(expected_ports, set(stage_payload.CLIENT_TREES))
        self.assertEqual(expected_ports, set(stage_payload.CLIENT_FILES))
        for port_version in core.SUPPORTED_PORTS:
            self.assertTrue(
                os.path.isfile(core.server_script(port_version, self.target)),
                port_version)

    def test_windows_distribution_guard_rejects_map_studio_paths(self):
        script_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "build_launcher.ps1")
        with open(script_path, "r", encoding="utf-8") as stream:
            script = stream.read()

        self.assertIn('"mapstudio"', script)
        self.assertIn("Map Studio", script)

    def test_windows_distribution_does_not_bundle_procdump(self):
        launcher_root = os.path.dirname(os.path.dirname(__file__))
        vendor_root = os.path.join(launcher_root, "vendor", "procdump")
        self.assertFalse(os.path.isfile(os.path.join(
            vendor_root, "procdump.exe")))
        self.assertFalse(os.path.isfile(os.path.join(
            vendor_root, "Eula.txt")))

        script_path = os.path.join(launcher_root, "build_launcher.ps1")
        with open(script_path, "r", encoding="utf-8") as stream:
            script = stream.read()
        self.assertNotIn("ProcDumpExecutable", script)
        self.assertNotIn("ProcDumpEula", script)
        self.assertNotIn("procdump.exe", script.lower())
        self.assertNotIn('"_internal\\tools\\Eula.txt"', script)

    def test_the_0_9_22_server_finds_its_client_modules(self):
        self.assertTrue(os.path.isfile(os.path.join(
            self.target, "0.9.22", "src", "res", "scripts", "client", "gui",
            "mods", "offline_lan_0922", "ai", "maps.py")))

    def test_the_0_9_22_server_stages_its_reward_module(self):
        self.assertTrue(os.path.isfile(os.path.join(
            self.target, "0.9.22", "server", "offline_rewards.py")))

    def test_the_0_9_22_server_stages_its_vehicle_overlay_store(self):
        self.assertTrue(os.path.isfile(os.path.join(
            self.target, "0.9.22", "server", "vehicle_overlay_store.py")))

    def test_the_navigation_graphs_stay_out_of_the_bundle(self):
        self.assertFalse(any(
            os.path.sep + "navgraphs" + os.path.sep in path
            for path in self.written))

    def test_client_staging_carries_only_the_0_9_22_mod(self):
        source = os.path.join(tempfile.mkdtemp(), "repo")
        self.addCleanup(shutil.rmtree, os.path.dirname(source), True)
        overlay = os.path.join(source, "dist",
                               "WoT-0.9.22-LAN-Client-abc1234")
        relative_paths = [
            os.path.join(overlay, "mods", "0.9.22.0.1",
                         "org.peng.offline_lan_0922_0.6.1.wotmod"),
            os.path.join(overlay, "mods", "configs", "offline_lan_0922",
                         "config.json"),
            os.path.join(overlay, "mods", "configs", "offline_lan_0922",
                         core.BUILD_IDENTITY_FILENAME_0922),
            os.path.join(overlay, "map_studio", "editor.py"),
        ]
        for relative in relative_paths:
            path = os.path.join(source, relative)
            if not os.path.isdir(os.path.dirname(path)):
                os.makedirs(os.path.dirname(path))
            with open(path, "w") as stream:
                stream.write("x")
        identity_path = os.path.join(
            overlay, *core.BUILD_IDENTITY_RELATIVE_PATH_0922.split("/"))
        with open(identity_path, "w") as stream:
            json.dump({
                "schema": 1,
                "semanticVersion": "0.6.1",
                "buildIdentity": "test-staging",
            }, stream)
        self._write_0_9_22_data(overlay)
        target = os.path.join(source, "staged")
        stage_payload.stage_clients(target, source)
        expected = {
            "0.9.22": ("mods/0.9.22.0.1/"
                       "org.peng.offline_lan_0922_0.6.1.wotmod",
                       "mods/configs/offline_lan_0922/config.json",
                       core.BUILD_IDENTITY_RELATIVE_PATH_0922,
                       "offline_worker_starter.exe",
                       "mods/0.9.22.0.1/offline_instance_guard_native.pyd",
                       "res_mods/0.9.22.0.1/"
                       "engine_config.offline-player.xml",
                       "res_mods/0.9.22.0.1/"
                       "engine_config.offline-worker.xml"),
        }
        for port_version, members in expected.items():
            archive = zipfile.ZipFile(
                os.path.join(target, "%s.zip" % port_version))
            try:
                names = set(archive.namelist())
            finally:
                archive.close()
            for member in members:
                self.assertIn(member, names)
            self.assertFalse(any(
                "mapstudio" in name.replace("_", "").lower()
                for name in names))
        self.assertEqual(["0.9.22.zip"], sorted(os.listdir(target)))

    def test_client_staging_without_a_built_package_reports_it(self):
        source = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, source, True)
        self.assertRaises(ValueError, stage_payload.stage_clients,
                          os.path.join(source, "staged"), source)

    def test_staging_replaces_an_earlier_payload(self):
        stale = os.path.join(self.root, "stale.txt")
        with open(stale, "w") as stream:
            stream.write("old")
        stage_payload.stage(self.root, include_clients=False)
        self.assertFalse(os.path.exists(stale))

    def test_staging_excludes_development_junk(self):
        source = os.path.join(tempfile.mkdtemp(), "repo")
        self.addCleanup(shutil.rmtree, os.path.dirname(source), True)
        overlay = os.path.join(source, "dist",
                               "WoT-0.9.22-LAN-Client-abc1234")
        paths = {}
        for prefix in stage_payload.SKIPPED_CLIENT_PREFIXES:
            paths[os.path.join(
                overlay, "mods", "configs", "offline_lan_0922",
                "%shelper.json" % prefix)] = "secret"
        paths[os.path.join(
            overlay, "mods/0.9.22.0.1",
            "org.peng.offline_lan_0922_0.6.1.wotmod")] = "x"
        paths[os.path.join(
            overlay, "mods/0.9.22.0.1",
            "org.peng.offline_lan_0922_0.6.1.wotmod.sha256")] = "secret"
        paths[os.path.join(
            overlay, "mods/configs/offline_lan_0922/config.json")] = "x"
        paths[os.path.join(
            overlay,
            core.BUILD_IDENTITY_RELATIVE_PATH_0922)] = json.dumps({
                "schema": 1,
                "semanticVersion": "0.6.1",
                "buildIdentity": "test-staging",
            })
        paths[os.path.join(
            overlay, "mods/configs/offline_lan_0922/debug.log")] = "secret"
        for relative, content in paths.items():
            path = (relative if os.path.isabs(relative) else
                    os.path.join(source, *relative.split("/")))
            if not os.path.isdir(os.path.dirname(path)):
                os.makedirs(os.path.dirname(path))
            with open(path, "w") as stream:
                stream.write(content)
        self._write_0_9_22_data(overlay)

        target = os.path.join(source, "staged")
        stage_payload.stage_clients(target, source)

        for port_version in core.SUPPORTED_PORTS:
            with zipfile.ZipFile(os.path.join(
                    target, "%s.zip" % port_version)) as archive:
                self.assertFalse(any(name.endswith("debug.log")
                                     for name in archive.namelist()))
                self.assertFalse(any(
                    name.rsplit("/", 1)[-1].startswith(
                        stage_payload.SKIPPED_CLIENT_PREFIXES)
                    for name in archive.namelist()))
                self.assertFalse(any(name.endswith(".sha256")
                                     for name in archive.namelist()))

    def test_multiple_0_9_22_overlays_are_rejected(self):
        source = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, source, True)
        for suffix in ("aaaaaaa", "bbbbbbb"):
            os.makedirs(os.path.join(
                source, "dist",
                "WoT-0.9.22-LAN-Client-" + suffix))
        self.assertRaises(ValueError, stage_payload.client_source,
                          "0.9.22", source)


class ServerImportTest(unittest.TestCase):
    """The bundle must carry every module the servers import.

    PyInstaller cannot see through ``runpy``, so a server import that is not
    declared in ``server_imports`` is missing from the packaged launcher.
    """

    ENTRY_POINTS = {
        core.PORT_0_9_22: ('server/windows_server.py',),
    }

    def setUp(self):
        root = os.path.join(tempfile.mkdtemp(), "payload")
        self.addCleanup(shutil.rmtree, os.path.dirname(root), True)
        stage_payload.stage(root, include_clients=False)
        self.payload = os.path.join(root, stage_payload.SERVER_DIR)

    def _module_file(self, root, name):
        relative = name.replace('.', os.path.sep)
        for candidate in (relative + '.py',
                          os.path.join(relative, '__init__.py')):
            path = os.path.join(root, candidate)
            if os.path.isfile(path):
                return path
        return None

    def _imports(self, path):
        with open(path, 'rb') as stream:
            tree = ast.parse(stream.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                if node.module:
                    names.add(node.module)
        return names

    def _closure(self, port_version):
        port_root = os.path.join(self.payload, port_version)
        search_roots = [port_root]
        if port_version == core.PORT_0_9_22:
            search_roots.append(os.path.join(port_root, 'server'))
            search_roots.append(os.path.join(
                port_root, 'src', 'res', 'scripts', 'client'))
        pending = [os.path.join(port_root, *entry.split('/'))
                   for entry in self.ENTRY_POINTS[port_version]]
        seen = set()
        external = set()
        while pending:
            path = pending.pop()
            if path in seen:
                continue
            seen.add(path)
            for name in self._imports(path):
                local = None
                for root in search_roots:
                    local = self._module_file(root, name)
                    if local is not None:
                        break
                if local is None:
                    external.add(name.split('.')[0])
                else:
                    pending.append(local)
        return external

    def test_every_server_import_is_declared(self):
        declared = set(server_imports.SERVER_STDLIB_MODULES)
        for port_version in core.SUPPORTED_PORTS:
            external = self._closure(port_version)
            required = {name for name in external
                        if name in sys.stdlib_module_names and
                        name != '__future__'}
            self.assertLessEqual(required, declared, port_version)

    def test_the_declared_modules_all_import(self):
        for name in server_imports.SERVER_STDLIB_MODULES:
            self.assertIn(name, sys.modules, name)


class ListenerTest(unittest.TestCase):
    class _ProtocolConnection(object):
        def __init__(self, reply_overrides=None):
            self.reply_overrides = dict(reply_overrides or {})
            self.reply = b""
            self.hello = None

        def settimeout(self, unused):
            pass

        def sendall(self, payload):
            hello = json.loads(payload.decode("utf-8"))
            if hello.get("type") == "leave":
                self.reply = b""
                return
            self.hello = hello
            reply = {
                "type": "welcome",
                "protocol": hello["protocol"],
                "client_build": hello["client_build"],
                "capabilities": hello.get("capabilities", []),
                "server_capabilities": [
                    "destructible_catalog_v5", "ram_contact_ledger_v2",
                    "human_ram_timeline_v1", "player_fire_intent_v6",
                    "player_environment_v2", "effective_params_v1",
                    "ricochet_continuation_v1"],
            }
            reply.update(self.reply_overrides)
            self.reply = (json.dumps(reply) + "\n").encode("utf-8")

        def recv(self, unused):
            reply, self.reply = self.reply, b""
            return reply

        def close(self):
            pass

    def test_probe_reports_a_closed_port(self):
        def refuse(address, timeout):
            raise OSError("refused")

        self.assertFalse(core.probe_endpoint("127.0.0.1", 1, connect=refuse))

    def test_wait_returns_when_the_server_answers(self):
        attempts = []

        class Connection(object):
            def close(self):
                pass

        def connect(address, timeout):
            attempts.append(address)
            if len(attempts) < 3:
                raise OSError("not yet")
            return Connection()

        self.assertTrue(core.wait_for_listener(
            "127.0.0.1", 28782, timeout=5.0, connect=connect,
            clock=lambda: 0.0, sleep=lambda seconds: None))
        self.assertEqual(len(attempts), 3)

    def test_wait_gives_up_after_the_timeout(self):
        times = iter([0.0, 1.0, 2.0, 3.0])

        def connect(address, timeout):
            raise OSError("refused")

        self.assertFalse(core.wait_for_listener(
            "127.0.0.1", 28782, timeout=1.0, connect=connect,
            clock=lambda: next(times), sleep=lambda seconds: None))

    def test_protocol_probe_accepts_each_matching_server(self):
        for port_version in core.SUPPORTED_PORTS:
            self.assertTrue(core.probe_server_protocol(
                port_version, "127.0.0.1", 28782,
                connect=lambda address, timeout: self._ProtocolConnection()))

    def test_protocol_probe_rejects_an_unrelated_listener(self):
        connection = self._ProtocolConnection({"client_build": "wrong"})
        self.assertFalse(core.probe_server_protocol(
            core.PORT_0_9_22, "127.0.0.1", 28782,
            connect=lambda address, timeout: connection))

    def test_protocol_probe_rejects_a_pre_schema_5_server(self):
        connection = self._ProtocolConnection({"server_capabilities": []})
        self.assertFalse(core.probe_server_protocol(
            core.PORT_0_9_22, "127.0.0.1", 28782,
            connect=lambda address, timeout: connection))

    def test_0922_probe_uses_the_non_player_probe_role(self):
        connection = self._ProtocolConnection()

        self.assertTrue(core.probe_server_protocol(
            core.PORT_0_9_22, "127.0.0.1", 28782,
            connect=lambda address, timeout: connection))
        self.assertEqual("probe", connection.hello["role"])
        self.assertEqual("AA==", connection.hello["vehicle_compact_descr"])

    def test_listener_status_distinguishes_protocol_from_raw_tcp(self):
        endpoint = lambda host, port, timeout=None: True
        compatible = lambda version, host, port, timeout=None: True
        incompatible = lambda version, host, port, timeout=None: False
        self.assertEqual(core.LISTENER_COMPATIBLE, core.listener_status(
            core.PORT_0_9_22, "127.0.0.1", 28782,
            endpoint_probe=endpoint, protocol_probe=compatible))
        self.assertEqual(core.LISTENER_OCCUPIED, core.listener_status(
            core.PORT_0_9_22, "127.0.0.1", 28782,
            endpoint_probe=endpoint, protocol_probe=incompatible))
        self.assertEqual(core.LISTENER_FREE, core.listener_status(
            core.PORT_0_9_22, "127.0.0.1", 28782,
            endpoint_probe=lambda host, port, timeout=None: False,
            protocol_probe=compatible))

    def test_wait_for_server_requires_the_protocol_probe(self):
        attempts = []

        def probe(port_version, host, port, timeout=None):
            attempts.append((port_version, host, port))
            return len(attempts) == 3

        self.assertTrue(core.wait_for_server(
            core.PORT_0_9_22, "127.0.0.1", 28782, timeout=5.0,
            probe=probe, clock=lambda: 0.0,
            sleep=lambda seconds: None))
        self.assertEqual(3, len(attempts))

    def test_probe_contracts_match_the_bundled_servers(self):
        def constants(path):
            with open(path, "rb") as stream:
                tree = ast.parse(stream.read())
            values = {}
            for statement in tree.body:
                if (isinstance(statement, ast.Assign) and
                        len(statement.targets) == 1 and
                        isinstance(statement.targets[0], ast.Name)):
                    try:
                        values[statement.targets[0].id] = ast.literal_eval(
                            statement.value)
                    except (TypeError, ValueError):
                        pass
            return values

        server0922 = constants(os.path.join(
            stage_payload.repository_root(), "server",
            "lan_battle_server.py"))
        self.assertEqual(server0922["PROTOCOL_VERSION"],
                         core._SERVER_PROBES[core.PORT_0_9_22]["protocol"])
        self.assertEqual(server0922["CLIENT_BUILD_0922"],
                         core._SERVER_PROBES[core.PORT_0_9_22]["client_build"])
        probe = core._SERVER_PROBES[core.PORT_0_9_22]
        for name in (
                "PROJECTILE_CAPABILITY",
                "DESTRUCTIBLE_CATALOG_V5_CAPABILITY",
                "RAM_CONTACT_LEDGER_CAPABILITY",
                "HUMAN_RAM_TIMELINE_CAPABILITY",
                "PLAYER_FIRE_INTENT_CAPABILITY",
                "PLAYER_ENVIRONMENT_CAPABILITY",
                "RICOCHET_CONTINUATION_CAPABILITY"):
            self.assertIn(server0922[name], probe["capabilities"])
        self.assertIn("effective_params_v1", probe["capabilities"])
        for name in (
                "DESTRUCTIBLE_CATALOG_V5_CAPABILITY",
                "RAM_CONTACT_LEDGER_CAPABILITY",
                "HUMAN_RAM_TIMELINE_CAPABILITY",
                "PLAYER_FIRE_INTENT_CAPABILITY",
                "PLAYER_ENVIRONMENT_CAPABILITY",
                "RICOCHET_CONTINUATION_CAPABILITY"):
            self.assertIn(server0922[name], probe["server_capabilities"])
        self.assertIn("effective_params_v1", probe["server_capabilities"])


class ConnectionReportTest(unittest.TestCase):
    def test_a_reachable_join_target_is_confirmed(self):
        self.assertIn("answered", core.connection_report(
            core.MODE_JOIN, "10.0.0.5", 28782, True))

    def test_an_unreachable_join_target_names_the_firewall(self):
        message = core.connection_report(core.MODE_JOIN, "10.0.0.5", 28782,
                                         False)
        self.assertIn("No answer from 10.0.0.5:28782", message)
        self.assertIn("firewall", message)

    def test_a_busy_port_warns_the_local_player(self):
        message = core.connection_report(core.MODE_SINGLE, core.LOCAL_HOST,
                                         28782, True)
        self.assertIn("already listens", message)

    def test_an_unrelated_listener_is_not_reported_as_the_server(self):
        message = core.listener_report(
            core.MODE_SINGLE, core.LOCAL_HOST, 28782,
            core.LISTENER_OCCUPIED)
        self.assertIn("Another program", message)

    def test_a_free_port_tells_the_host_what_happens_next(self):
        message = core.connection_report(core.MODE_SINGLE, core.LOCAL_HOST,
                                         28782, False)
        self.assertIn("Start game runs the server", message)


class LocalAddressTest(unittest.TestCase):
    def test_loopback_is_never_offered_to_other_players(self):
        self.assertEqual(
            core.local_addresses(lambda: ['127.0.0.1', '192.168.1.20']),
            ['192.168.1.20'])

    def test_duplicate_addresses_collapse(self):
        self.assertEqual(
            core.local_addresses(lambda: ['10.0.0.5', '10.0.0.5']),
            ['10.0.0.5'])

    def test_a_failed_lookup_reports_no_address(self):
        def fail():
            raise OSError('no name')

        self.assertEqual(core.local_addresses(fail), [])


class GameProcessTest(unittest.TestCase):
    """The client can restart itself once while it starts up."""

    class _Process(object):
        def __init__(self, states):
            self.states = list(states)

        def poll(self):
            if len(self.states) > 1:
                return self.states.pop(0)
            return self.states[0]

    class _TerminableProcess(object):
        def __init__(self):
            self.terminated = False
            self.killed = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 1

        def kill(self):
            self.killed = True

    def test_native_process_enumerator_matches_the_image_name(self):
        names = ["explorer.exe", "WorldOfTanks.exe"]

        self.assertTrue(core.game_is_running(enumerator=lambda: names))
        self.assertFalse(core.game_is_running(
            executable="OtherGame.exe", enumerator=lambda: names))

    def test_failed_native_process_enumerator_reports_the_game_as_gone(self):
        def fail():
            raise OSError("process snapshot is unavailable")

        self.assertFalse(core.game_is_running(enumerator=fail))

    def test_visible_game_window_matches_the_selected_client_path(self):
        selected = core.game_executable("/selected-game")

        self.assertTrue(core.game_window_is_visible(
            "/selected-game", enumerator=lambda: [selected]))
        self.assertFalse(core.game_window_is_visible(
            "/selected-game",
            enumerator=lambda: [core.game_executable("/other-game")]))

    def test_missing_window_does_not_latch_paired_player_as_closed(self):
        process = self._Process([None, None, 0])

        self.assertEqual(
            (0, False),
            core.wait_for_paired_player_exit(
                process, "/game", window_visible=lambda: False,
                close_grace=1.0, poll=1.0,
                sleep=lambda unused: None))

    def test_paired_player_ignores_window_loss_until_the_job_exits(self):
        process = self._Process([None, None, None, None, 0])
        visibility_checks = []
        stop = mock.Mock()

        self.assertEqual(
            (0, False),
            core.wait_for_paired_player_exit(
                process, "/game",
                window_visible=lambda: visibility_checks.append(True),
                close_grace=2.0, poll=1.0,
                clock=lambda: 999.0, stop_process=stop,
                shutdown_timeout=45.0,
                sleep=lambda unused: None))
        self.assertEqual([], visibility_checks)
        stop.assert_not_called()

    def test_paired_player_retires_when_required_worker_exits(self):
        process = self._TerminableProcess()
        worker = self._Process([None, 7])

        self.assertEqual(
            (1, True),
            core.wait_for_paired_player_exit(
                process, "/game", required_process=worker,
                window_visible=lambda: True, poll=1.0,
                clock=lambda: 0.0, sleep=lambda unused: None))
        self.assertTrue(process.terminated)
        self.assertFalse(process.killed)

    def test_the_wait_ends_after_a_quiet_grace_period(self):
        ticks = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        self.assertFalse(core.wait_for_game_exit(
            lambda: False, grace=3.0, poll=1.0,
            clock=lambda: next(ticks), sleep=lambda seconds: None))

    def test_a_restarted_game_keeps_the_wait_open(self):
        seen = []
        running = [True, True, False, False, False, False]
        ticks = iter([float(index) for index in range(20)])

        def is_running():
            return running.pop(0) if running else False

        self.assertTrue(core.wait_for_game_exit(
            is_running, on_restart=lambda: seen.append(1), grace=2.0, poll=1.0,
            clock=lambda: next(ticks), sleep=lambda seconds: None))
        self.assertEqual([1], seen)

    def test_shutdown_waits_for_terminated_processes_to_disappear(self):
        running = [True, True, False]
        ticks = iter([0.0, 0.0, 0.1])
        sleeps = []

        self.assertTrue(core.wait_for_game_shutdown(
            is_running=lambda: running.pop(0), timeout=1.0, poll=0.1,
            clock=lambda: next(ticks), sleep=sleeps.append))
        self.assertEqual([0.1, 0.1], sleeps)

    def test_shutdown_wait_is_bounded(self):
        ticks = iter([0.0, 1.0])

        self.assertFalse(core.wait_for_game_shutdown(
            is_running=lambda: True, timeout=1.0, poll=0.1,
            clock=lambda: next(ticks), sleep=lambda unused: None))

    def test_worker_ready_requires_a_live_process_and_marker(self):
        game_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, game_root, True)
        marker = core.worker_ready_marker(game_root)
        attempts = []

        def sleep(unused):
            attempts.append(1)
            with open(marker, "w") as stream:
                stream.write("ready")

        process = self._Process([None])
        self.assertTrue(core.wait_for_worker_ready(
            process, game_root, timeout=1.0, interval=0.1,
            clock=lambda: 0.0, sleep=sleep))
        self.assertEqual([1], attempts)

    def test_worker_ready_rejects_an_unchanged_stale_marker(self):
        game_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, game_root, True)
        marker = core.worker_ready_marker(game_root)
        with open(marker, "w") as stream:
            stream.write("stale")
        previous = core.worker_ready_marker_token(game_root)
        attempts = []

        def sleep(unused):
            attempts.append(1)
            with open(marker, "w") as stream:
                stream.write("new-ready-marker")

        self.assertTrue(core.wait_for_worker_ready(
            self._Process([None]), game_root,
            previous_marker_token=previous, timeout=1.0, interval=0.1,
            clock=lambda: 0.0, sleep=sleep))
        self.assertEqual([1], attempts)

    def test_worker_exit_or_cancellation_rejects_readiness(self):
        game_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, game_root, True)
        with open(core.worker_ready_marker(game_root), "w") as stream:
            stream.write("stale")
        self.assertFalse(core.wait_for_worker_ready(
            self._Process([9]), game_root, clock=lambda: 0.0,
            sleep=lambda unused: None))
        self.assertFalse(core.wait_for_worker_ready(
            self._Process([None]), game_root, cancelled=lambda: True,
            clock=lambda: 0.0, sleep=lambda unused: None))


class KnownFolderTest(unittest.TestCase):
    def test_a_folder_moves_to_the_top_without_duplicates(self):
        folders = core.remember_folder([], os.path.join("C:", "Games", "WoT"))
        folders = core.remember_folder(folders, os.path.join("D:", "WoT922"))
        folders = core.remember_folder(folders, os.path.join("C:", "Games",
                                                             "WoT"))
        self.assertEqual([os.path.join("C:", "Games", "WoT"),
                          os.path.join("D:", "WoT922")], folders)

    def test_the_list_stays_bounded(self):
        folders = []
        for index in range(15):
            folders = core.remember_folder(folders, "/games/wot%d" % index,
                                           limit=4)
        self.assertEqual(4, len(folders))
        self.assertEqual(os.path.normpath("/games/wot14"), folders[0])

    def test_an_empty_folder_is_ignored(self):
        self.assertEqual(["/games/wot"],
                         core.remember_folder(["/games/wot"], "   "))

    def test_discovery_finds_a_game_beside_the_common_roots(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        for name in ("World_of_Tanks_Custom", "Some Other Game"):
            os.makedirs(os.path.join(root, name))
        with open(os.path.join(root, "World_of_Tanks_Custom",
                               core.GAME_EXECUTABLE), "w") as stream:
            stream.write("")
        self.assertEqual(
            [os.path.join(root, "World_of_Tanks_Custom")],
            core.discover_game_folders(roots=(root,)))

    def test_discovery_accepts_a_root_that_is_the_game(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        with open(os.path.join(root, core.GAME_EXECUTABLE), "w") as stream:
            stream.write("")
        self.assertEqual([root], core.discover_game_folders(roots=(root,)))

    def test_discovery_survives_a_missing_root(self):
        self.assertEqual([], core.discover_game_folders(
            roots=("/nonexistent-root",)))

    def test_remembered_folders_come_before_discovered_ones(self):
        folders = core.known_folders(
            {"folders": ["/games/remembered"]},
            discovered=["/games/discovered", "/games/remembered"])
        self.assertEqual([os.path.normpath("/games/remembered"),
                          os.path.normpath("/games/discovered")], folders)


class LauncherSettingsTest(unittest.TestCase):
    def test_settings_round_trip(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, "launcher.json")
        self.assertTrue(core.save_settings({"mode": core.MODE_SINGLE}, path))
        self.assertEqual(core.load_settings(path), {"mode": core.MODE_SINGLE})

    def test_missing_settings_are_empty(self):
        self.assertEqual(core.load_settings("/nonexistent/launcher.json"), {})


class VehicleOverlayFetchTest(unittest.TestCase):
    """core.fetch_vehicle_overlay speaks the launcher probe + overlay exchange."""

    MEMBER = "scripts/item_defs/vehicles/ussr/R11_MS-1.xml"
    MEMBER_DATA = b"member-data"

    @staticmethod
    def _manifest():
        return {
            "schema": 1,
            "targetVersion": "0.9.22.0.1",
            "targetBuild": "1513",
            "sourcePackage": "res/packages/scripts.pkg",
            "createdAt": "2026-08-23T12:00:00Z",
            "updatedAt": "2026-08-23T12:00:00Z",
            "activeProfile": "Fast MS-1",
            "members": [{
                "sourceMember": VehicleOverlayFetchTest.MEMBER,
                "sourcePackage": "res/packages/scripts.pkg",
                "overlayRelativePath": VehicleOverlayFetchTest.MEMBER,
                "overlaySha256": hashlib.sha256(
                    VehicleOverlayFetchTest.MEMBER_DATA).hexdigest(),
                "edits": [{
                    "fieldPath": "speedLimits/forward",
                    "originalPackedType": "integer",
                    "originalValue": 32,
                    "replacementValue": 40,
                }],
            }],
        }

    @staticmethod
    def _manifest_with_member_count(count):
        manifest = VehicleOverlayFetchTest._manifest()
        manifest["members"] = []
        checksum = hashlib.sha256(
            VehicleOverlayFetchTest.MEMBER_DATA).hexdigest()
        for index in range(count):
            member = (
                "scripts/item_defs/vehicles/ussr/Capacity_%04d.xml" %
                index)
            manifest["members"].append({
                "sourceMember": member,
                "sourcePackage": "res/packages/scripts.pkg",
                "overlayRelativePath": member,
                "overlaySha256": checksum,
                "edits": [{
                    "fieldPath": "speedLimits/forward",
                    "originalPackedType": "integer",
                    "originalValue": 32,
                    "replacementValue": 40,
                }],
            })
        return manifest

    class _OverlayConnection(object):
        def __init__(self, manifest=None, present=True, capability=True,
                     member_reply=None):
            self.manifest = manifest
            self.present = present
            self.capability = capability
            self.member_reply = member_reply
            self.reply = b""
            self.sent = []
            self.closed = False

        def settimeout(self, unused):
            pass

        def sendall(self, payload):
            message = json.loads(payload.decode("utf-8"))
            self.sent.append(message)
            message_type = message.get("type")
            if message_type == "hello":
                reply = {
                    "type": "welcome",
                    "protocol": 5,
                    "client_build": "wot-0.9.22.0.1-cn-1513",
                    "capabilities": message.get("capabilities", []),
                    "server_capabilities": (
                        ["vehicle_overlay_v1"] if self.capability else []),
                }
            elif message_type == "vehicle_overlay_query":
                if not self.present:
                    reply = {
                        "type": "vehicle_overlay_manifest",
                        "present": False,
                        "digest": "",
                        "profile": "",
                        "manifest": None,
                        "members": [],
                    }
                else:
                    digest = hashlib.sha256(json.dumps(
                        self.manifest, sort_keys=True).encode("utf-8"))
                    reply = {
                        "type": "vehicle_overlay_manifest",
                        "present": True,
                        "digest": digest.hexdigest(),
                        "profile": "Fast MS-1",
                        "manifest": self.manifest,
                        "members": [{
                            "sourceMember": entry["sourceMember"],
                            "overlaySha256": entry["overlaySha256"],
                            "size": len(VehicleOverlayFetchTest.MEMBER_DATA),
                        } for entry in self.manifest["members"]],
                    }
            elif message_type == "vehicle_overlay_member":
                reply = self.member_reply
                if reply is None:
                    reply = {
                        "type": "vehicle_overlay_member_data",
                        "sourceMember": message["sourceMember"],
                        "size": len(VehicleOverlayFetchTest.MEMBER_DATA),
                        "sha256": hashlib.sha256(
                            VehicleOverlayFetchTest.MEMBER_DATA).hexdigest(),
                        "data_b64": base64.b64encode(
                            VehicleOverlayFetchTest.MEMBER_DATA).decode(
                            "ascii"),
                    }
            else:
                reply = {"type": "error", "code": "unexpected"}
            self.reply = (json.dumps(reply) + "\n").encode("utf-8")

        def recv(self, unused):
            reply, self.reply = self.reply, b""
            return reply

        def close(self):
            self.closed = True

    def _fetch(self, **kwargs):
        connection = self._OverlayConnection(**kwargs)
        result = core.fetch_vehicle_overlay(
            "127.0.0.1", 28782, connect=lambda address, timeout: connection)
        return result, connection

    def test_fetch_returns_the_host_overlay(self):
        result, connection = self._fetch(manifest=self._manifest())

        self.assertTrue(result["supported"])
        self.assertTrue(result["present"])
        self.assertEqual("Fast MS-1", result["profile"])
        self.assertEqual(64, len(result["digest"]))
        self.assertEqual(
            {self.MEMBER: self.MEMBER_DATA}, result["payload"])
        self.assertEqual(self.MEMBER, result["manifest"]["members"][0][
            "sourceMember"])
        self.assertEqual(
            ["hello", "vehicle_overlay_query", "vehicle_overlay_member"],
            [message["type"] for message in connection.sent])
        self.assertTrue(connection.closed)

    def test_fetch_accepts_max_members_above_the_old_manifest_line_cap(self):
        manifest = self._manifest_with_member_count(
            core.MAX_OVERLAY_MEMBERS)
        encoded = json.dumps({
            "type": "vehicle_overlay_manifest",
            "present": True,
            "digest": "0" * 64,
            "profile": "Capacity",
            "manifest": manifest,
            "members": manifest["members"],
        }).encode("utf-8")

        result, unused_connection = self._fetch(manifest=manifest)

        self.assertGreater(len(encoded), 256 * 1024)
        self.assertEqual(core.MAX_OVERLAY_MEMBERS, len(result["payload"]))

    def test_fetch_rejects_one_member_over_the_supported_count(self):
        manifest = self._manifest_with_member_count(
            core.MAX_OVERLAY_MEMBERS + 1)

        with self.assertRaisesRegex(
                core.LauncherError, "more than 1024 members"):
            self._fetch(manifest=manifest)

    def test_fetch_reports_a_server_without_the_capability(self):
        result, connection = self._fetch(
            manifest=self._manifest(), capability=False)

        self.assertFalse(result["supported"])
        self.assertEqual(
            ["hello"], [message["type"] for message in connection.sent])

    def test_fetch_reports_a_room_without_an_overlay(self):
        result, connection = self._fetch(manifest=None, present=False)

        self.assertTrue(result["supported"])
        self.assertFalse(result["present"])
        self.assertEqual("", result["digest"])
        self.assertEqual({}, result["payload"])

    def test_fetch_rejects_corrupt_member_data(self):
        reply = {
            "type": "vehicle_overlay_member_data",
            "sourceMember": self.MEMBER,
            "size": 4,
            "sha256": "0" * 64,
            "data_b64": "not base64!",
        }
        with self.assertRaises(core.LauncherError):
            self._fetch(manifest=self._manifest(), member_reply=reply)

    def test_fetch_rejects_a_member_reply_for_another_member(self):
        reply = {
            "type": "vehicle_overlay_member_data",
            "sourceMember": "scripts/item_defs/vehicles/ussr/R12_Test.xml",
            "size": 4,
            "sha256": "0" * 64,
            "data_b64": "bWVtYmVy",
        }
        with self.assertRaises(core.LauncherError):
            self._fetch(manifest=self._manifest(), member_reply=reply)


if __name__ == "__main__":
    unittest.main()
