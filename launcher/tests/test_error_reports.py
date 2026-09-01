"""Session-boundary and privacy tests for one-click error reports."""

import datetime
import os
import shutil
import struct
import tempfile
import unittest
import zipfile
from unittest import mock

import core
import error_reports


class ErrorReportTest(unittest.TestCase):
    SESSION_1 = "20260823T120000Z-111111111111"
    SESSION_2 = "20260823T130000Z-222222222222"

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.game = os.path.join(self.root, "game")
        os.makedirs(self.game)
        self.settings = os.path.join(self.root, "state", "launcher.json")
        self.settings_patch = mock.patch.object(
            core, "settings_path", return_value=self.settings)
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.server_log_patch = mock.patch.object(
            core, "server_log_path", return_value=os.path.join(
                self.root, "state", "server.log"))
        self.server_log_patch.start()
        self.addCleanup(self.server_log_patch.stop)

    def _game_log(self, role):
        return os.path.join(
            self.game, error_reports._GAME_LOG_FILENAMES[role])

    @staticmethod
    def _write(path, payload, mode="wb"):
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, mode) as stream:
            stream.write(payload)

    @staticmethod
    def _archive(report):
        with zipfile.ZipFile(report["path"], "r") as archive:
            return dict((name, archive.read(name))
                        for name in archive.namelist())

    @staticmethod
    def _minidump(*stream_types):
        directory_rva = 32
        payload_rva = directory_rva + 12 * len(stream_types)
        entries = []
        payloads = []
        for stream_type in stream_types:
            payload = b"\0" * (168 if stream_type == 6 else 56)
            entries.append(struct.pack(
                "<III", stream_type, len(payload), payload_rva))
            payloads.append(payload)
            payload_rva += len(payload)
        return b"".join((
            struct.pack(
                "<4sIIIIIQ", b"MDMP", 0, len(stream_types),
                directory_rva, 0, 0, 0),
            b"".join(entries),
            b"".join(payloads),
        ))

    def test_single_player_report_contains_only_this_session_log_slices(self):
        visible = self._game_log(error_reports.ROLE_VISIBLE_CLIENT)
        worker = self._game_log(error_reports.ROLE_HIDDEN_WORKER)
        starter = self._game_log(error_reports.ROLE_HIDDEN_WORKER_STARTER)
        self._write(visible, b"old visible\n")
        self._write(worker, b"old worker\n")
        self._write(starter, b"old starter line that is much longer\n" * 8)
        self._write(os.path.join(self.game, "preferences.xml"), b"private")
        self._write(os.path.join(
            self.game, "mods", "configs", "offline_lan_0922",
            "vehicle_profiles.json"), b"private")

        session = error_reports.begin_session(
            self.game, needs_worker=True, local_server=True,
            session_id=self.SESSION_1, started_at="start")
        server = error_reports.attach_server(session, dedicated=True)
        error_reports.expect_worker_starter_reset(session)
        self._write(visible, b"new visible\n", "ab")
        self._write(worker, b"new worker\n", "ab")
        replacement = starter + ".new"
        self._write(replacement, b"new starter\n")
        os.replace(replacement, starter)
        self._write(server, b"new server\n")
        self.assertTrue(error_reports.finalize_session(
            session, ended_at="end"))

        self._write(visible, b"future visible\n", "ab")
        self._write(worker, b"future worker\n", "ab")
        self._write(server, b"future server\n", "ab")
        report = error_reports.create_report(
            now=datetime.datetime(2026, 8, 23, 12, 30, 0))
        payloads = self._archive(report)

        self.assertEqual({
            "server.log": b"new server\n",
            "visible-client.log": b"new visible\n",
            "hidden-worker.log": b"new worker\n",
            "hidden-worker-starter.log": b"new starter\n",
        }, payloads)
        self.assertEqual((), report["missing"])
        self.assertEqual((), report["notRun"])
        self.assertNotIn("preferences.xml", payloads)
        self.assertNotIn("vehicle_profiles.json", payloads)
        self.assertEqual(
            os.path.join(self.root, "state", "reports"),
            os.path.dirname(report["path"]))

    def test_new_empty_session_never_falls_back_to_the_previous_logs(self):
        visible = self._game_log(error_reports.ROLE_VISIBLE_CLIENT)
        self._write(visible, b"first session\n")
        first = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="first")
        self._write(visible, b"first new bytes\n", "ab")
        error_reports.finalize_session(first, ended_at="first-end")
        self.assertTrue(error_reports.create_report()["included"])

        second = error_reports.begin_session(
            self.game, session_id=self.SESSION_2, started_at="second")
        error_reports.finalize_session(second, ended_at="second-end")

        with self.assertRaisesRegex(
                core.LauncherError, "No earlier session was included"):
            error_reports.create_report()

    def test_report_includes_only_the_current_launcher_session_slice(self):
        launcher_log = core.launcher_log_path()
        self._write(launcher_log, b"old launcher session\n")
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")
        self._write(launcher_log, b"version=0.6.1 build=test-build-a\n", "ab")
        self._write(
            self._game_log(error_reports.ROLE_VISIBLE_CLIENT), b"visible\n")
        error_reports.finalize_session(session, ended_at="end")

        payloads = self._archive(error_reports.create_report())

        self.assertEqual(
            b"version=0.6.1 build=test-build-a\n",
            payloads["launcher.log"])

    def test_clean_visible_exit_marker_must_end_this_session_log(self):
        visible = self._game_log(error_reports.ROLE_VISIBLE_CLIENT)
        marker = (
            b"2026-08-24 13:38:12.444: INFO: "
            b"PostProcessing.Phases.fini()\r\n")
        self._write(visible, marker)
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")

        self._write(visible, b"current session still running\r\n", "ab")
        self.assertFalse(
            error_reports.visible_client_exited_cleanly(session))

        self._write(visible, marker, "ab")
        self.assertTrue(
            error_reports.visible_client_exited_cleanly(session))

        self._write(visible, b"late failure\r\n", "ab")
        self.assertFalse(
            error_reports.visible_client_exited_cleanly(session))

    def test_termination_dump_does_not_override_a_clean_exit_log_trailer(self):
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")
        self._write(
            self._game_log(error_reports.ROLE_VISIBLE_CLIENT),
            b"2026-08-24 13:38:12.444: INFO: "
            b"PostProcessing.Phases.fini()\r\n")
        self._write(error_reports.session_dump_path(
            session, error_reports.ROLE_VISIBLE_CLIENT), self._minidump(7))

        self.assertEqual(
            error_reports.MINIDUMP_EVIDENCE_TERMINATION,
            error_reports.minidump_evidence(
                session, error_reports.ROLE_VISIBLE_CLIENT))
        self.assertEqual(
            error_reports.VISIBLE_CLIENT_EXIT_CLEAN,
            error_reports.visible_client_exit_evidence(session))
        self.assertTrue(
            error_reports.visible_client_exited_cleanly(session))

    def test_termination_dump_after_lobby_restore_is_normal(self):
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")
        visible = self._game_log(error_reports.ROLE_VISIBLE_CLIENT)
        self._write(
            visible,
            b"2026-08-26 21:24:19.367: INFO: [Offline LAN 0.9.22] "
            b"deferred lobby Account restored\r\n")

        self.assertEqual(
            error_reports.VISIBLE_CLIENT_EXIT_UNKNOWN,
            error_reports.visible_client_exit_evidence(session))

        self._write(error_reports.session_dump_path(
            session, error_reports.ROLE_VISIBLE_CLIENT), self._minidump(7))

        self.assertEqual(
            error_reports.VISIBLE_CLIENT_EXIT_CLEAN,
            error_reports.visible_client_exit_evidence(session))
        self.assertTrue(
            error_reports.visible_client_exited_cleanly(session))

        self._write(visible, b"late failure after lobby restore\r\n", "ab")
        self.assertEqual(
            error_reports.VISIBLE_CLIENT_EXIT_TERMINATED,
            error_reports.visible_client_exit_evidence(session))
        self.assertFalse(
            error_reports.visible_client_exited_cleanly(session))

    def test_worker_termination_dump_after_lobby_restore_is_normal(self):
        session = error_reports.begin_session(
            self.game, needs_worker=True,
            session_id=self.SESSION_1, started_at="start")
        worker = self._game_log(error_reports.ROLE_HIDDEN_WORKER)
        self._write(
            worker,
            b"2026-08-27 09:13:46.798: INFO: [Offline LAN 0.9.22] "
            b"deferred lobby Account restored\r\n")
        self._write(error_reports.session_dump_path(
            session, error_reports.ROLE_HIDDEN_WORKER), self._minidump(7))

        self.assertEqual(
            error_reports.VISIBLE_CLIENT_EXIT_CLEAN,
            error_reports.client_exit_evidence(
                session, error_reports.ROLE_HIDDEN_WORKER))
        self.assertTrue(error_reports.client_exited_cleanly(
            session, error_reports.ROLE_HIDDEN_WORKER))

    def test_worker_termination_without_teardown_is_unexpected(self):
        session = error_reports.begin_session(
            self.game, needs_worker=True,
            session_id=self.SESSION_1, started_at="start")
        self._write(
            self._game_log(error_reports.ROLE_HIDDEN_WORKER),
            b"battle still live when worker output stopped\r\n")
        self._write(error_reports.session_dump_path(
            session, error_reports.ROLE_HIDDEN_WORKER), self._minidump(7))

        self.assertEqual(
            error_reports.VISIBLE_CLIENT_EXIT_TERMINATED,
            error_reports.client_exit_evidence(
                session, error_reports.ROLE_HIDDEN_WORKER))
        self.assertFalse(error_reports.client_exited_cleanly(
            session, error_reports.ROLE_HIDDEN_WORKER))

    def test_worker_exception_overrides_a_lobby_restore(self):
        session = error_reports.begin_session(
            self.game, needs_worker=True,
            session_id=self.SESSION_1, started_at="start")
        self._write(
            self._game_log(error_reports.ROLE_HIDDEN_WORKER),
            b"2026-08-27 09:13:46.798: INFO: [Offline LAN 0.9.22] "
            b"deferred lobby Account restored\r\n")
        self._write(error_reports.session_dump_path(
            session, error_reports.ROLE_HIDDEN_WORKER), self._minidump(6))

        self.assertEqual(
            error_reports.VISIBLE_CLIENT_EXIT_EXCEPTION,
            error_reports.client_exit_evidence(
                session, error_reports.ROLE_HIDDEN_WORKER))
        self.assertFalse(error_reports.client_exited_cleanly(
            session, error_reports.ROLE_HIDDEN_WORKER))

    def test_exception_stream_overrides_a_clean_exit_log_trailer(self):
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")
        self._write(
            self._game_log(error_reports.ROLE_VISIBLE_CLIENT),
            b"2026-08-24 13:38:12.444: INFO: "
            b"PostProcessing.Phases.fini()\r\n")
        self._write(error_reports.session_dump_path(
            session, error_reports.ROLE_VISIBLE_CLIENT), self._minidump(6))

        self.assertEqual(
            error_reports.MINIDUMP_EVIDENCE_EXCEPTION,
            error_reports.minidump_evidence(
                session, error_reports.ROLE_VISIBLE_CLIENT))
        self.assertEqual(
            error_reports.VISIBLE_CLIENT_EXIT_EXCEPTION,
            error_reports.visible_client_exit_evidence(session))
        self.assertFalse(
            error_reports.visible_client_exited_cleanly(session))

    def test_termination_dump_without_a_clean_trailer_is_unexpected(self):
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")
        self._write(
            self._game_log(error_reports.ROLE_VISIBLE_CLIENT),
            b"client stopped before final shutdown\r\n")
        self._write(error_reports.session_dump_path(
            session, error_reports.ROLE_VISIBLE_CLIENT), self._minidump(7))

        self.assertEqual(
            error_reports.VISIBLE_CLIENT_EXIT_TERMINATED,
            error_reports.visible_client_exit_evidence(session))
        self.assertFalse(
            error_reports.visible_client_exited_cleanly(session))

    def test_malformed_minidump_never_proves_a_clean_exit(self):
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")
        self._write(
            self._game_log(error_reports.ROLE_VISIBLE_CLIENT),
            b"2026-08-24 13:38:12.444: INFO: "
            b"PostProcessing.Phases.fini()\r\n")
        malformed = struct.pack(
            "<4sIIIIIQ", b"MDMP", 0, 1, 4096, 0, 0, 0)
        self._write(error_reports.session_dump_path(
            session, error_reports.ROLE_VISIBLE_CLIENT), malformed)

        self.assertEqual(
            error_reports.MINIDUMP_EVIDENCE_UNKNOWN,
            error_reports.minidump_evidence(
                session, error_reports.ROLE_VISIBLE_CLIENT))
        self.assertEqual(
            error_reports.VISIBLE_CLIENT_EXIT_UNKNOWN,
            error_reports.visible_client_exit_evidence(session))
        self.assertFalse(
            error_reports.visible_client_exited_cleanly(session))

    def test_partial_single_player_report_names_missing_current_logs(self):
        session = error_reports.begin_session(
            self.game, needs_worker=True, local_server=True,
            session_id=self.SESSION_1, started_at="start")
        self._write(
            self._game_log(error_reports.ROLE_VISIBLE_CLIENT), b"visible\n")
        error_reports.finalize_session(session, ended_at="end")

        report = error_reports.create_report()

        self.assertEqual(("visible-client.log",), report["included"])
        self.assertEqual(
            ("server.log", "hidden-worker.log"), report["missing"])
        self.assertEqual((), report["notRun"])

    def test_network_join_reports_roles_that_this_session_did_not_run(self):
        session = error_reports.begin_session(
            self.game, needs_worker=False, local_server=False,
            session_id=self.SESSION_1, started_at="start")
        self._write(
            self._game_log(error_reports.ROLE_VISIBLE_CLIENT), b"visible\n")
        error_reports.finalize_session(session, ended_at="end")

        report = error_reports.create_report()

        self.assertEqual((), report["missing"])
        self.assertEqual(
            ("server.log", "hidden-worker.log"), report["notRun"])

    def test_reused_server_is_cut_at_both_session_boundaries(self):
        server = core.server_log_path()
        self._write(server, b"before session\n")
        session = error_reports.begin_session(
            self.game, local_server=True, session_id=self.SESSION_1,
            started_at="start")
        error_reports.attach_server(session, dedicated=False)
        self._write(server, b"during session\n", "ab")
        self._write(
            self._game_log(error_reports.ROLE_VISIBLE_CLIENT), b"visible\n")
        error_reports.finalize_session(session, ended_at="end")
        self._write(server, b"after session\n", "ab")

        payloads = self._archive(error_reports.create_report())

        self.assertEqual(b"during session\n", payloads["server.log"])

    def test_unexpected_log_replacement_is_not_mistaken_for_this_session(self):
        visible = self._game_log(error_reports.ROLE_VISIBLE_CLIENT)
        self._write(visible, b"old visible\n")
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")
        replacement = visible + ".replacement"
        self._write(replacement, b"unrelated replacement\n")
        os.replace(replacement, visible)
        error_reports.finalize_session(session, ended_at="end")

        with self.assertRaisesRegex(
                core.LauncherError, "No earlier session was included"):
            error_reports.create_report()

    def test_a_log_symlink_created_during_the_session_is_never_collected(self):
        private = os.path.join(self.root, "private.txt")
        self._write(private, b"private data")
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")
        try:
            os.symlink(
                private,
                self._game_log(error_reports.ROLE_VISIBLE_CLIENT))
        except (AttributeError, NotImplementedError, OSError):
            self.skipTest("symlinks are unavailable")

        with self.assertRaisesRegex(
                core.LauncherError, "No earlier session was included"):
            error_reports.create_report()
        self.assertIsNone(session["endedAt"])

    def test_a_redirected_session_server_directory_is_refused(self):
        session = error_reports.begin_session(
            self.game, local_server=True, session_id=self.SESSION_1,
            started_at="start")
        session_root = error_reports.session_logs_directory()
        os.makedirs(session_root)
        redirected = os.path.join(self.root, "redirected")
        os.makedirs(redirected)
        try:
            os.symlink(redirected, os.path.join(session_root, self.SESSION_1))
        except (AttributeError, NotImplementedError, OSError):
            self.skipTest("symlinks are unavailable")

        with self.assertRaisesRegex(
                core.LauncherError, "not a regular directory"):
            error_reports.attach_server(session, dedicated=True)

    def test_only_a_confirmed_crash_role_dump_is_added_to_the_zip(self):
        session = error_reports.begin_session(
            self.game, needs_worker=True, session_id=self.SESSION_1,
            started_at="start")
        paths = error_reports.session_dump_paths(session)
        self.assertEqual(
            os.path.join(
                error_reports.session_dumps_directory(), self.SESSION_1),
            session["dumpDirectory"])
        self.assertEqual(paths, session["dumpPaths"])
        self._write(
            self._game_log(error_reports.ROLE_VISIBLE_CLIENT), b"visible\n")
        self._write(paths[error_reports.ROLE_VISIBLE_CLIENT], b"normal exit")
        self._write(paths[error_reports.ROLE_HIDDEN_WORKER], b"worker crash")

        self.assertTrue(error_reports.set_session_crash_roles(
            session, [error_reports.ROLE_HIDDEN_WORKER]))
        error_reports.finalize_session(session, ended_at="end")
        report = error_reports.create_report()
        payloads = self._archive(report)

        self.assertEqual({
            "visible-client.log": b"visible\n",
            "hidden-worker.dmp": b"worker crash",
        }, payloads)
        self.assertNotIn("visible-client.dmp", report["included"])
        with zipfile.ZipFile(report["path"], "r") as archive:
            self.assertEqual(
                zipfile.ZIP_DEFLATED,
                archive.getinfo("hidden-worker.dmp").compress_type)

    def test_windows_reparse_points_are_rejected_from_dump_boundaries(self):
        value = mock.Mock(
            st_mode=0,
            st_file_attributes=error_reports.stat.FILE_ATTRIBUTE_REPARSE_POINT)

        self.assertTrue(error_reports._is_reparse_point(value))

    def test_dump_file_reparse_point_is_not_added_to_the_zip(self):
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")
        self._write(
            self._game_log(error_reports.ROLE_VISIBLE_CLIENT), b"visible\n")
        dump_path = error_reports.session_dump_path(
            session, error_reports.ROLE_VISIBLE_CLIENT)
        self._write(dump_path, b"redirected memory")
        error_reports.set_session_crash_roles(
            session, [error_reports.ROLE_VISIBLE_CLIENT])
        error_reports.finalize_session(session, ended_at="end")
        real_lstat = os.lstat

        def lstat(path):
            if path == dump_path:
                return mock.Mock(
                    st_mode=error_reports.stat.S_IFREG,
                    st_file_attributes=(
                        error_reports.stat.FILE_ATTRIBUTE_REPARSE_POINT))
            return real_lstat(path)

        with mock.patch.object(error_reports.os, "lstat", side_effect=lstat):
            self.assertEqual(
                error_reports.MINIDUMP_EVIDENCE_UNKNOWN,
                error_reports.minidump_evidence(
                    session, error_reports.ROLE_VISIBLE_CLIENT))
            report = error_reports.create_report()

        self.assertEqual(
            {"visible-client.log": b"visible\n"}, self._archive(report))

    def test_report_directory_reparse_point_is_refused(self):
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")
        self._write(
            self._game_log(error_reports.ROLE_VISIBLE_CLIENT), b"visible\n")
        error_reports.finalize_session(session, ended_at="end")
        report_root = error_reports.reports_directory()
        os.makedirs(report_root)
        real_lstat = os.lstat

        def lstat(path):
            if path == report_root:
                return mock.Mock(
                    st_mode=error_reports.stat.S_IFDIR,
                    st_file_attributes=(
                        error_reports.stat.FILE_ATTRIBUTE_REPARSE_POINT))
            return real_lstat(path)

        with mock.patch.object(error_reports.os, "lstat", side_effect=lstat):
            with self.assertRaisesRegex(
                    core.LauncherError, "not a regular directory"):
                error_reports.create_report()

    def test_a_confirmed_crash_dump_can_be_reported_before_any_log(self):
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")
        dump_path = error_reports.session_dump_path(
            session, error_reports.ROLE_VISIBLE_CLIENT)
        self._write(dump_path, b"early crash")
        error_reports.set_session_crash_roles(
            session, [error_reports.ROLE_VISIBLE_CLIENT])
        error_reports.finalize_session(session, ended_at="end")

        report = error_reports.create_report()

        self.assertEqual(
            {"visible-client.dmp": b"early crash"},
            self._archive(report))
        self.assertEqual(("visible-client.log",), report["missing"])

    def test_dump_files_are_ignored_until_a_crash_role_is_confirmed(self):
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")
        self._write(
            self._game_log(error_reports.ROLE_VISIBLE_CLIENT), b"visible\n")
        self._write(error_reports.session_dump_path(
            session, error_reports.ROLE_VISIBLE_CLIENT), b"normal exit")
        error_reports.finalize_session(session, ended_at="end")

        self.assertEqual(
            {"visible-client.log": b"visible\n"},
            self._archive(error_reports.create_report()))

    def test_dump_is_not_collected_before_the_session_is_finalized(self):
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")
        self._write(
            self._game_log(error_reports.ROLE_VISIBLE_CLIENT), b"visible\n")
        self._write(error_reports.session_dump_path(
            session, error_reports.ROLE_VISIBLE_CLIENT), b"still writing")
        error_reports.set_session_crash_roles(
            session, [error_reports.ROLE_VISIBLE_CLIENT])

        self.assertEqual(
            {"visible-client.log": b"visible\n"},
            self._archive(error_reports.create_report()))

    def test_dump_cleanup_is_role_scoped_and_rejects_unknown_roles(self):
        session = error_reports.begin_session(
            self.game, needs_worker=True, session_id=self.SESSION_1,
            started_at="start")
        paths = error_reports.session_dump_paths(session)
        self._write(paths[error_reports.ROLE_VISIBLE_CLIENT], b"visible")
        self._write(paths[error_reports.ROLE_HIDDEN_WORKER], b"worker")

        removed = error_reports.cleanup_session_dumps(
            session, [error_reports.ROLE_VISIBLE_CLIENT])

        self.assertEqual(
            (paths[error_reports.ROLE_VISIBLE_CLIENT],), removed)
        self.assertFalse(os.path.lexists(
            paths[error_reports.ROLE_VISIBLE_CLIENT]))
        self.assertTrue(os.path.isfile(
            paths[error_reports.ROLE_HIDDEN_WORKER]))
        with self.assertRaisesRegex(core.LauncherError, "role is invalid"):
            error_reports.cleanup_session_dumps(session, ["server"])
        with self.assertRaisesRegex(core.LauncherError, "role is invalid"):
            error_reports.set_session_crash_roles(session, ["server"])
        error_reports.cleanup_session_dumps(session)
        self.assertFalse(os.path.lexists(session["dumpDirectory"]))

    def test_next_session_removes_only_fixed_stale_monitor_slots(self):
        first = error_reports.begin_session(
            self.game, needs_worker=True, session_id=self.SESSION_1,
            started_at="first")
        monitor_paths = error_reports._session_dump_monitor_paths(
            first["dumpPaths"], error_reports.DUMP_ROLES)
        self._write(monitor_paths[0], b"partial visible memory")
        self._write(monitor_paths[-1], b"partial worker memory")
        unrelated = os.path.join(first["dumpDirectory"], "keep.txt")
        self._write(unrelated, b"not a monitor slot")

        error_reports.begin_session(
            self.game, session_id=self.SESSION_2, started_at="second")

        self.assertFalse(os.path.lexists(monitor_paths[0]))
        self.assertFalse(os.path.lexists(monitor_paths[-1]))
        self.assertTrue(os.path.isfile(unrelated))

    def test_begin_session_removes_only_the_previous_fixed_dump_files(self):
        first = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="first")
        first_dump = error_reports.session_dump_path(
            first, error_reports.ROLE_VISIBLE_CLIENT)
        unrelated = os.path.join(first["dumpDirectory"], "keep.txt")
        self._write(first_dump, b"old dump")
        self._write(unrelated, b"not a dump")

        second = error_reports.begin_session(
            self.game, session_id=self.SESSION_2, started_at="second")

        self.assertFalse(os.path.lexists(first_dump))
        self.assertTrue(os.path.isfile(unrelated))
        self.assertTrue(os.path.isdir(second["dumpDirectory"]))

    def test_begin_session_never_cleans_a_redirected_previous_boundary(self):
        first = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="first")
        private_directory = os.path.join(self.root, "private")
        private_dump = os.path.join(private_directory, "visible-client.dmp")
        self._write(private_dump, b"private memory")
        first["dumpDirectory"] = private_directory
        first["dumpPaths"] = {
            error_reports.ROLE_VISIBLE_CLIENT: private_dump,
            error_reports.ROLE_HIDDEN_WORKER: os.path.join(
                private_directory, "hidden-worker.dmp"),
        }
        error_reports._write_state(first)

        error_reports.begin_session(
            self.game, session_id=self.SESSION_2, started_at="second")

        with open(private_dump, "rb") as stream:
            self.assertEqual(b"private memory", stream.read())

    def test_redirected_dump_directory_is_never_collected(self):
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")
        self._write(
            self._game_log(error_reports.ROLE_VISIBLE_CLIENT), b"visible\n")
        os.rmdir(session["dumpDirectory"])
        redirected = os.path.join(self.root, "redirected")
        os.makedirs(redirected)
        try:
            os.symlink(redirected, session["dumpDirectory"])
        except (AttributeError, NotImplementedError, OSError):
            self.skipTest("symlinks are unavailable")
        private_dump = os.path.join(redirected, "visible-client.dmp")
        self._write(private_dump, b"private memory")
        error_reports.set_session_crash_roles(
            session, [error_reports.ROLE_VISIBLE_CLIENT])
        error_reports.finalize_session(session, ended_at="end")

        self.assertEqual(
            {"visible-client.log": b"visible\n"},
            self._archive(error_reports.create_report()))
        with self.assertRaisesRegex(
                core.LauncherError, "not a regular directory"):
            error_reports.cleanup_session_dumps(session)
        with open(private_dump, "rb") as stream:
            self.assertEqual(b"private memory", stream.read())

    def test_dump_symlink_is_never_read_and_cleanup_does_not_follow_it(self):
        private = os.path.join(self.root, "private.dmp")
        self._write(private, b"private memory")
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")
        self._write(
            self._game_log(error_reports.ROLE_VISIBLE_CLIENT), b"visible\n")
        dump_path = error_reports.session_dump_path(
            session, error_reports.ROLE_VISIBLE_CLIENT)
        try:
            os.symlink(private, dump_path)
        except (AttributeError, NotImplementedError, OSError):
            self.skipTest("symlinks are unavailable")
        error_reports.set_session_crash_roles(
            session, [error_reports.ROLE_VISIBLE_CLIENT])
        error_reports.finalize_session(session, ended_at="end")

        self.assertEqual(
            {"visible-client.log": b"visible\n"},
            self._archive(error_reports.create_report()))
        error_reports.cleanup_session_dumps(
            session, [error_reports.ROLE_VISIBLE_CLIENT])
        with open(private, "rb") as stream:
            self.assertEqual(b"private memory", stream.read())
        self.assertFalse(os.path.lexists(dump_path))

    def test_tampered_dump_paths_cannot_redirect_report_collection(self):
        private = os.path.join(self.root, "private.dmp")
        self._write(private, b"private memory")
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")
        session["dumpPaths"][error_reports.ROLE_VISIBLE_CLIENT] = private
        session["crashRoles"] = [error_reports.ROLE_VISIBLE_CLIENT]
        error_reports._write_state(session)

        with self.assertRaisesRegex(
                core.LauncherError, "dump boundary is unreadable"):
            error_reports.create_report()
        with open(private, "rb") as stream:
            self.assertEqual(b"private memory", stream.read())

    def test_creating_the_same_report_twice_never_overwrites_the_first(self):
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")
        self._write(
            self._game_log(error_reports.ROLE_VISIBLE_CLIENT), b"visible\n")
        error_reports.finalize_session(session, ended_at="end")
        now = datetime.datetime(2026, 8, 23, 12, 30, 0)
        first = error_reports.create_report(now=now)
        with open(first["path"], "rb") as stream:
            original = stream.read()

        with self.assertRaisesRegex(core.LauncherError, "already exists"):
            error_reports.create_report(now=now)

        with open(first["path"], "rb") as stream:
            self.assertEqual(original, stream.read())

    def test_declined_automatic_report_can_delete_only_the_exact_zip(self):
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")
        self._write(
            self._game_log(error_reports.ROLE_VISIBLE_CLIENT), b"visible\n")
        error_reports.finalize_session(session, ended_at="end")
        report = error_reports.create_report()

        self.assertTrue(error_reports.delete_report(report["path"]))
        self.assertFalse(os.path.lexists(report["path"]))
        self.assertFalse(error_reports.delete_report(report["path"]))
        self.assertFalse(any(
            ".tmp-" in name
            for name in os.listdir(error_reports.reports_directory())))

    def test_report_delete_refuses_a_path_outside_the_report_directory(self):
        outside = os.path.join(self.root, "private.zip")
        self._write(outside, b"private")

        with self.assertRaisesRegex(core.LauncherError, "path is unsafe"):
            error_reports.delete_report(outside)

        with open(outside, "rb") as stream:
            self.assertEqual(b"private", stream.read())

    def test_missing_session_has_a_clear_refusal(self):
        with self.assertRaisesRegex(
                core.LauncherError, "No launcher game session"):
            error_reports.create_report()

    def test_explorer_command_selects_the_exact_zip(self):
        report = os.path.join(self.root, "report with spaces.zip")
        self._write(report, b"zip")
        calls = []

        error_reports.select_in_explorer(
            report, runner=lambda *args, **kwargs: calls.append(
                (args, kwargs)))

        self.assertEqual(
            ["explorer.exe", "/select,", os.path.normpath(report)],
            calls[0][0][0])


if __name__ == "__main__":
    unittest.main()
