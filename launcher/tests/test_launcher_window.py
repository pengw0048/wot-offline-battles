"""Wiring tests for the launcher window with a fake Tk module.

Widget option names and real Tk behavior stay unproven here. Only the callback
wiring and the guard paths are covered.
"""

import io
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import core
import wot_launcher


class _Widget(object):
    def __init__(self, master=None, **options):
        self.master = master
        self.options = dict(options)
        self.children = []
        if master is not None and hasattr(master, "children"):
            master.children.append(self)

    def pack(self, **unused):
        pass

    def grid(self, **unused):
        pass

    def grid_columnconfigure(self, *unused, **unused_options):
        pass

    grid_rowconfigure = grid_columnconfigure

    def config(self, **options):
        self.options.update(options)

    def bind(self, event, callback):
        self.options.setdefault("bindings", {})[event] = callback

    def cget(self, name):
        return self.options.get(name)


class _Text(_Widget):
    def __init__(self, master=None, **options):
        _Widget.__init__(self, master, **options)
        self.lines = []

    def insert(self, unused_index, text):
        self.lines.append(text)

    def see(self, unused_index):
        pass


class _Notebook(_Widget):
    def __init__(self, master=None, **options):
        _Widget.__init__(self, master, **options)
        self.tabs = []
        self.tab_options = {}
        self.selected = None

    def add(self, child, **options):
        self.tabs.append(child)
        self.tab_options[child] = dict(options)
        if self.selected is None:
            self.selected = child

    def tab(self, child, **options):
        self.tab_options.setdefault(child, {}).update(options)
        return self.tab_options[child]

    def select(self, child=None):
        if child is None:
            return self.selected
        self.selected = child
        return child

    def index(self, value):
        child = self.selected if value == "current" else value
        return self.tabs.index(child)


class _StringVar(object):
    def __init__(self, value=""):
        self._value = value
        self._callbacks = []

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        for callback in self._callbacks:
            callback()

    def trace_add(self, unused_mode, callback):
        self._callbacks.append(lambda: callback())


class _Root(_Widget):
    def __init__(self):
        _Widget.__init__(self)
        self.destroyed = False
        self.mainloop_called = False

    def title(self, unused_title):
        pass

    def protocol(self, unused_name, unused_handler):
        pass

    def after(self, unused_delay, callback):
        callback()

    def destroy(self):
        self.destroyed = True

    def mainloop(self):
        self.mainloop_called = True


class _FakeTk(object):
    Tk = _Root
    Frame = _Widget
    LabelFrame = _Widget
    Label = _Widget
    Entry = _Widget
    Button = _Widget
    Checkbutton = _Widget
    Radiobutton = _Widget
    Text = _Text
    StringVar = _StringVar
    BooleanVar = _StringVar


class _FakeTtk(object):
    Combobox = _Widget
    Notebook = _Notebook


class _FakeFileDialog(object):
    def __init__(self, selection=""):
        self.selection = selection

    def askdirectory(self, **unused):
        return self.selection


class _Process(object):
    def __init__(self, exit_code=None, stdout=None, pid=1234):
        self.exit_code = exit_code
        self.stdout = stdout
        self.pid = pid
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.terminated = True
        self.exit_code = 0

    def kill(self):
        self.killed = True
        self.exit_code = -9

    def wait(self, timeout=None):
        return self.exit_code


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.settings_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.settings_dir, True)
        self.addCleanup(setattr, core, "settings_path", core.settings_path)
        self.addCleanup(setattr, core, "discover_game_folders",
                        core.discover_game_folders)
        core.settings_path = lambda: os.path.join(self.settings_dir,
                                                  "launcher.json")
        core.discover_game_folders = lambda *unused, **unused_options: []
        language_patch = mock.patch.object(
            wot_launcher.i18n, "detect_system_language", return_value="en")
        language_patch.start()
        self.addCleanup(language_patch.stop)
        self.dialog = _FakeFileDialog()
        self.window = wot_launcher.LauncherWindow(_FakeTk, _FakeTtk, self.dialog)

    def _log_text(self):
        return "".join(self.window.log_view.lines)

    def _game(self, version="0.9.22.0.1", build="1513"):
        game_root = os.path.join(self.settings_dir, "game-" + version)
        os.makedirs(game_root)
        with open(os.path.join(game_root, core.GAME_EXECUTABLE), "w") as stream:
            stream.write("")
        with open(os.path.join(game_root, "version.xml"), "w") as stream:
            stream.write("<version> v.%s #%s </version>" % (version, build))
        self.window.game_root.set(game_root)
        return game_root

    def test_layout_separates_play_vehicle_and_repair_controls(self):
        self.assertEqual("0.6.5", wot_launcher.LAUNCHER_VERSION)
        self.assertEqual(
            "Single player",
            self.window.battle_tabs.tab(self.window.single_panel).get("text"))
        self.assertEqual(
            "Online",
            self.window.battle_tabs.tab(self.window.network_panel).get("text"))
        self.assertIs(
            self.window.single_start_button.master, self.window.single_panel)
        self.assertIs(
            self.window.network_start_button.master, self.window.network_panel)
        self.assertIs(
            self.window.vehicle_profile_box.master, self.window.vehicle_panel)
        self.assertEqual(
            "Exact lineup",
            self.window.tools_tabs.tab(
                self.window.bot_lineup_panel).get("text"))
        self.assertIs(
            self.window.bot_lineup_profile_box.master,
            self.window.bot_lineup_panel)
        self.assertIs(self.window.repair_button.master, self.window.repair_panel)
        self.assertIs(
            self.window.normal_preferences_button.master,
            self.window.repair_panel)
        self.assertIs(self.window.report_button.master, self.window.game_panel)
        self.assertEqual(
            "Create error report...",
            self.window.report_button.cget("text"))
        self.assertIn("bold", str(self.window.report_button.cget("font")))
        self.assertEqual(
            "作者：伪红学家  Bilibili：@tiancaihb  "
            "GitHub: https://github.com/pengw0048/wot-offline-battles",
            self.window.author_text.get())
        self.assertEqual(
            "坦克世界QQ群：1108778562、302519768",
            self.window.qq_group_text.get())
        self.assertEqual(
            "本mod免费传播、开源、欢迎二创，使用无需付费，售卖与本人无关，仅供个人学习交流",
            self.window.distribution_notice_text.get())
        self.assertEqual("readonly", self.window.author_entry.cget("state"))
        self.assertEqual("readonly", self.window.qq_group_entry.cget("state"))
        self.assertEqual(
            "readonly", self.window.distribution_notice_entry.cget("state"))
        self.assertIs(
            self.window.author_text,
            self.window.author_entry.cget("textvariable"))
        self.assertIs(
            self.window.qq_group_text,
            self.window.qq_group_entry.cget("textvariable"))
        self.assertIs(
            self.window.distribution_notice_text,
            self.window.distribution_notice_entry.cget("textvariable"))
        self.assertFalse(self.window.collect_crash_reports.get())
        self.assertFalse(self.window.full_crash_dumps.get())
        self.assertEqual(
            "Collect a report if the game crashes",
            self.window.crash_report_check.cget("text"))
        self.assertEqual(
            "Collect full-memory crash dumps (very large files)",
            self.window.full_crash_dump_check.cget("text"))

    def test_launcher_session_identity_is_visible_and_persisted(self):
        self.assertIn(
            "Launcher session: version=0.6.5 build=unknown role=launcher",
            self._log_text())
        with open(core.launcher_log_path(), encoding="utf-8") as stream:
            persisted = stream.read()
        self.assertIn(
            "Launcher session: version=0.6.5 build=unknown role=launcher",
            persisted)

    def test_first_run_prompts_once_when_the_launcher_starts(self):
        with mock.patch.object(
                self.window, "_request_crash_collection",
                return_value=False) as request:
            self.window.run()
            self.window.run()

        request.assert_called_once_with()
        self.assertTrue(self.window.root.mainloop_called)

    def test_crash_collection_defaults_off_and_persists_a_decline(self):
        self.assertFalse(self.window.collect_crash_reports.get())
        self.assertTrue(self.window._initial_crash_prompt_pending)
        with mock.patch.object(
                self.window, "_confirm_enable_crash_capture",
                return_value=False) as confirm, mock.patch.object(
                    core, "download_procdump") as download:
            self.assertFalse(
                self.window._prompt_initial_crash_collection())

        confirm.assert_called_once_with()
        download.assert_not_called()
        self.assertFalse(self.window.collect_crash_reports.get())
        self.assertFalse(self.window._procdump_download_consent)
        settings = core.load_settings()
        self.assertFalse(settings.get(
            wot_launcher.COLLECT_CRASH_REPORTS_SETTING))
        self.assertFalse(settings.get(wot_launcher.PROCDUMP_CONSENT_SETTING))

        reopened = wot_launcher.LauncherWindow(
            _FakeTk, _FakeTtk, self.dialog)

        self.assertFalse(reopened.collect_crash_reports.get())
        self.assertFalse(reopened._initial_crash_prompt_pending)
        with mock.patch.object(
                reopened, "_confirm_enable_crash_capture") as confirm:
            reopened.run()
        confirm.assert_not_called()

    def test_manual_enable_confirms_and_downloads_procdump_in_background(self):
        installed_path = os.path.join(
            self.settings_dir, "tools", "procdump.exe")
        self.window.collect_crash_reports.set(True)
        with mock.patch.object(
                self.window, "_confirm_enable_crash_capture",
                return_value=True) as confirm, mock.patch.object(
                    core, "procdump_executable",
                    return_value=installed_path), mock.patch.object(
                    core, "procdump_is_installed",
                    return_value=False), mock.patch.object(
                    core, "download_procdump",
                    return_value=installed_path) as download, mock.patch(
                        "wot_launcher.threading.Thread") as thread:
            self.assertTrue(self.window._crash_collection_toggled())
            download.assert_not_called()
            worker = thread.call_args.kwargs["target"]
            worker()

        confirm.assert_called_once_with()
        thread.return_value.start.assert_called_once_with()
        download.assert_called_once_with(installed_path)
        self.assertTrue(self.window._procdump_download_consent)
        self.assertTrue(self.window.collect_crash_reports.get())
        self.assertFalse(self.window._maintenance_busy)
        settings = core.load_settings()
        self.assertTrue(settings.get(
            wot_launcher.COLLECT_CRASH_REPORTS_SETTING))
        self.assertTrue(settings.get(wot_launcher.PROCDUMP_CONSENT_SETTING))
        self.assertIn("ProcDump was downloaded", self._log_text())

    def test_failed_procdump_download_keeps_crash_collection_disabled(self):
        installed_path = os.path.join(
            self.settings_dir, "tools", "procdump.exe")
        self.window.collect_crash_reports.set(True)
        with mock.patch.object(
                self.window, "_confirm_enable_crash_capture",
                return_value=True), mock.patch.object(
                    core, "procdump_executable",
                    return_value=installed_path), mock.patch.object(
                    core, "procdump_is_installed",
                    return_value=False), mock.patch.object(
                    core, "download_procdump",
                    side_effect=core.LauncherError(
                        "official download unavailable")), mock.patch(
                            "wot_launcher.threading.Thread") as thread:
            self.assertTrue(self.window._crash_collection_toggled())
            worker = thread.call_args.kwargs["target"]
            worker()

        self.assertTrue(self.window._procdump_download_consent)
        self.assertFalse(self.window.collect_crash_reports.get())
        self.assertFalse(self.window._maintenance_busy)
        settings = core.load_settings()
        self.assertFalse(settings.get(
            wot_launcher.COLLECT_CRASH_REPORTS_SETTING))
        self.assertTrue(settings.get(wot_launcher.PROCDUMP_CONSENT_SETTING))
        self.assertIn("official download unavailable", self._log_text())

    def test_missing_cache_retry_failure_disables_saved_collection(self):
        self.window._procdump_download_consent = True
        self.window.collect_crash_reports.set(True)
        self.window._save_settings()
        with mock.patch.object(
                core, "procdump_is_installed", return_value=False), \
                mock.patch.object(
                    core, "download_procdump",
                    side_effect=core.LauncherError("offline")):
            self.assertFalse(self.window._enable_crash_capture({}, True))

        self.assertFalse(self.window.collect_crash_reports.get())
        settings = core.load_settings()
        self.assertFalse(settings.get(
            wot_launcher.COLLECT_CRASH_REPORTS_SETTING))
        self.assertTrue(settings.get(wot_launcher.PROCDUMP_CONSENT_SETTING))

    def test_crash_collection_control_is_only_enabled_for_0_9_22(self):
        self._game("0.8.2", "")
        self.assertEqual(
            "disabled", self.window.crash_report_check.cget("state"))
        self.assertEqual(
            "disabled", self.window.full_crash_dump_check.cget("state"))

        self._game()
        self.assertEqual("normal", self.window.crash_report_check.cget("state"))
        self.assertEqual(
            "normal", self.window.full_crash_dump_check.cget("state"))

    def test_full_crash_dump_choice_defaults_off_and_persists(self):
        self.assertFalse(self.window.full_crash_dumps.get())

        self.window.full_crash_dumps.set(True)
        self.assertTrue(self.window._full_crash_dumps_toggled())

        reopened = wot_launcher.LauncherWindow(
            _FakeTk, _FakeTtk, self.dialog)
        self.assertTrue(reopened.full_crash_dumps.get())
        self.assertTrue(core.load_settings().get(
            wot_launcher.FULL_CRASH_DUMPS_SETTING))

    def test_old_host_setting_migrates_to_the_online_tab(self):
        core.save_settings({"mode": "host"})

        reopened = wot_launcher.LauncherWindow(
            _FakeTk, _FakeTtk, self.dialog)

        self.assertEqual(core.MODE_JOIN, reopened.mode.get())
        self.assertIs(reopened.network_panel, reopened.battle_tabs.select())

    def test_auto_chinese_and_explicit_english_are_applied_and_saved(self):
        core.save_settings({"language": wot_launcher.i18n.LANGUAGE_AUTO})
        with mock.patch.object(
                wot_launcher.i18n, "detect_system_language",
                return_value=wot_launcher.i18n.LANGUAGE_CHINESE):
            reopened = wot_launcher.LauncherWindow(
                _FakeTk, _FakeTtk, self.dialog)

        self.assertEqual("游戏客户端", reopened.game_panel.cget("text"))
        self.assertEqual(
            "开始单人战斗", reopened.single_start_button.cget("text"))
        self.assertEqual(
            "坦克属性修改器",
            reopened.tools_tabs.tab(reopened.vehicle_panel).get("text"))
        self.assertEqual("一键汇报错误…", reopened.report_button.cget("text"))
        self.assertEqual(
            "游戏闪退时收集报告",
            reopened.crash_report_check.cget("text"))

        reopened.language_choice.set("English")
        reopened._language_selected()

        self.assertEqual("Game client", reopened.game_panel.cget("text"))
        self.assertEqual(
            "Start single-player battle",
            reopened.single_start_button.cget("text"))
        self.assertEqual(
            wot_launcher.i18n.LANGUAGE_ENGLISH,
            core.load_settings().get("language"))

    def test_primary_action_text_follows_the_selected_tab(self):
        self.assertEqual(
            "Start single-player battle",
            self.window.single_start_button.cget("text"))
        self.window.mode.set(core.MODE_JOIN)
        self.window._refresh_mode()
        self.assertEqual(
            "Join network battle",
            self.window.network_start_button.cget("text"))
        self.assertIs(self.window.network_start_button, self.window.start_button)

    def test_error_report_button_stays_available_during_a_game(self):
        self.window._busy = True
        self.window._update_action_controls()

        self.assertEqual("normal", self.window.report_button.cget("state"))

    def test_error_report_button_packages_and_selects_the_zip(self):
        report = {
            "path": os.path.join(self.settings_dir, "reports", "report.zip"),
            "included": ("server.log", "visible-client.log"),
            "missing": ("hidden-worker.log",),
            "notRun": (),
        }
        with mock.patch.object(
                wot_launcher.error_reports, "create_report",
                return_value=report) as create, mock.patch.object(
                    wot_launcher.error_reports, "select_in_explorer") \
                as select:
            self.assertTrue(self.window._create_error_report())
            for unused in range(200):
                if not self.window._report_busy:
                    break
                time.sleep(0.01)

        create.assert_called_once_with()
        select.assert_called_once_with(report["path"])
        self.assertIn("Created error report", self._log_text())
        self.assertIn("server.log, visible-client.log", self._log_text())
        self.assertIn("hidden-worker.log", self._log_text())

    def test_error_report_button_explains_an_empty_latest_session(self):
        message = (
            "The latest game session has not produced any diagnostic logs "
            "yet. No earlier session was included.")
        with mock.patch.object(
                wot_launcher.error_reports, "create_report",
                side_effect=core.LauncherError(message)), mock.patch.object(
                    wot_launcher.error_reports, "select_in_explorer") \
                as select:
            self.assertTrue(self.window._create_error_report())
            for unused in range(200):
                if not self.window._report_busy:
                    break
                time.sleep(0.01)

        select.assert_not_called()
        self.assertIn("No earlier session was included", self._log_text())

    def test_crash_prompt_selects_the_zip_only_after_consent(self):
        report_path = os.path.join(self.settings_dir, "crash.zip")
        with mock.patch.object(
                self.window, "_confirm_crash_report", return_value=True), \
                mock.patch.object(
                    wot_launcher.error_reports, "select_in_explorer") \
                as select, mock.patch.object(
                    wot_launcher.error_reports, "delete_report") as delete:
            self.assertTrue(self.window._offer_crash_report(report_path))
        select.assert_called_once_with(report_path)
        delete.assert_not_called()

        with mock.patch.object(
                self.window, "_confirm_crash_report", return_value=False), \
                mock.patch.object(
                    wot_launcher.error_reports, "select_in_explorer") \
                as select, mock.patch.object(
                    wot_launcher.error_reports, "delete_report") as delete:
            self.assertFalse(self.window._offer_crash_report(report_path))
        select.assert_not_called()
        delete.assert_called_once_with(report_path)

    def test_crash_prompt_explains_how_to_find_the_report(self):
        ask = mock.Mock(return_value=False)
        tkinter = mock.Mock(messagebox=mock.Mock(askyesno=ask))
        with mock.patch.dict("sys.modules", {"tkinter": tkinter}):
            self.assertFalse(self.window._confirm_crash_report())

        message = ask.call_args.args[1]
        self.assertIn("error report is ready", message)
        self.assertIn("Windows Explorer", message)
        self.assertNotIn("password", message)

    def test_procdump_consent_explains_the_debugging_download(self):
        ask = mock.Mock(return_value=False)
        tkinter = mock.Mock(messagebox=mock.Mock(askyesno=ask))
        with mock.patch.dict("sys.modules", {"tkinter": tkinter}):
            self.assertFalse(self.window._confirm_enable_crash_capture())

        title, message = ask.call_args.args[:2]
        self.assertIn("crash diagnostics", title)
        self.assertIn("debugging information", message)
        self.assertIn("Microsoft's official site", message)
        self.assertNotIn("password", message)

    def test_network_start_always_plans_a_join_session(self):
        self._game()
        self.window.join_address.set("10.0.0.5:1234")
        session = {
            "client": core.PORT_0_9_22,
            "mode": core.MODE_JOIN,
            "host": "10.0.0.5",
            "tcp_port": 1234,
            "needs_server": False,
            "vehicle_profile": None,
        }
        with mock.patch("core.plan_session", return_value=session) as plan, \
                mock.patch("wot_launcher.threading.Thread") as thread:
            self.window._start_network()

        self.assertEqual(core.MODE_JOIN, plan.call_args.args[1])
        thread.return_value.start.assert_called_once_with()

    def test_local_room_owner_still_plans_a_join_session(self):
        self._game()
        self.window.join_address.set(
            "%s:%d" % (core.LOCAL_HOST, core.DEFAULT_SERVER_PORT))
        self.window._server = _Process(exit_code=None)
        self.window._server_persistent = True
        session = {
            "client": core.PORT_0_9_22,
            "mode": core.MODE_JOIN,
            "host": core.LOCAL_HOST,
            "tcp_port": core.DEFAULT_SERVER_PORT,
            "needs_server": False,
            "vehicle_profile": None,
        }
        with mock.patch("core.plan_session", return_value=session) as plan, \
                mock.patch("wot_launcher.threading.Thread") as thread:
            self.window._start_network()

        self.assertEqual(core.MODE_JOIN, plan.call_args.args[1])
        thread.return_value.start.assert_called_once_with()

    def test_the_address_field_and_test_button_follow_the_mode(self):
        self.window.mode.set(core.MODE_JOIN)
        self.window._refresh_mode()
        self.assertEqual(self.window.join_entry.cget("state"), "normal")
        self.assertEqual(self.window.test_button.cget("state"), "normal")
        self.window.mode.set(core.MODE_SINGLE)
        self.window._refresh_mode()
        self.assertEqual(self.window.join_entry.cget("state"), "disabled")
        self.assertEqual(self.window.test_button.cget("state"), "disabled")

    def test_team_sizes_are_configured_only_in_the_waiting_room(self):
        self.assertFalse(hasattr(self.window, "team_size"))
        self.assertFalse(hasattr(self.window, "team1_size"))
        self.assertFalse(hasattr(self.window, "team2_size"))
        self.assertFalse(hasattr(self.window, "single_team_size_box"))
        self.assertFalse(hasattr(self.window, "network_team_size_box"))

    def test_team_is_selected_only_in_the_waiting_room(self):
        self.assertFalse(hasattr(self.window, "preferred_team"))
        self.assertFalse(hasattr(self.window, "single_preferred_team_box"))
        self.assertFalse(hasattr(self.window, "network_preferred_team_box"))

    def test_server_button_is_an_explicit_online_action(self):
        self._game("0.9.22.0.1", "1513")
        self.assertEqual("disabled", self.window.server_button.cget("state"))
        self.window.mode.set(core.MODE_JOIN)
        self.window._refresh_mode()
        self.assertEqual("normal", self.window.server_button.cget("state"))
        self.assertEqual(
            "Start LAN room", self.window.server_button.cget("text"))
        self.window.mode.set(core.MODE_SINGLE)
        self.window._refresh_mode()
        self.assertEqual("disabled", self.window.server_button.cget("state"))

    def test_lan_room_button_starts_persistent_server_and_worker(self):
        game_root = self._game("0.9.22.0.1", "1513")
        self.window.mode.set(core.MODE_JOIN)
        self.window._refresh_mode()
        with mock.patch(
                "core.install_client_mod", return_value=["installed"]) \
                as install, mock.patch(
                    "wot_launcher.vehicle_overlays.prepare_vehicle_profile",
                    return_value={"profile": None, "installedMembers": 0,
                                  "removedMembers": 0}) as prepare, \
                mock.patch.object(
                    self.window, "_start_server", return_value=True) \
                as start_server, mock.patch.object(
                    self.window, "_start_worker", return_value=True) \
                as start_worker:
            self.assertTrue(self.window._toggle_lan_server())
            for unused in range(200):
                if not self.window._maintenance_busy:
                    break
                time.sleep(0.01)

        install.assert_called_once_with(game_root, core.PORT_0_9_22)
        prepare.assert_called_once_with(game_root, None)
        start_server.assert_called_once_with(
            game_root, core.PORT_0_9_22, persistent=True, require_owned=True)
        start_worker.assert_called_once_with(
            game_root, core.LOCAL_HOST, core.DEFAULT_SERVER_PORT,
            room_owned=True)
        self.assertEqual(
            "%s:%d" % (core.LOCAL_HOST, core.DEFAULT_SERVER_PORT),
            self.window.join_address.get())

    def test_lan_room_button_pins_the_selected_vehicle_profile(self):
        game_root = self._game("0.9.22.0.1", "1513")
        self.window.mode.set(core.MODE_JOIN)
        self.window._refresh_mode()
        with mock.patch(
                "core.install_client_mod", return_value=["installed"]), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.list_vehicle_profiles",
                    return_value=["Fast MS-1"]), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.prepare_vehicle_profile",
                    return_value={"profile": "Fast MS-1",
                                  "installedMembers": 2,
                                  "removedMembers": 0}) as prepare, \
                mock.patch.object(
                    self.window, "_start_server", return_value=True), \
                mock.patch.object(
                    self.window, "_start_worker", return_value=True):
            self.window.vehicle_profile.set("Fast MS-1")
            self.assertTrue(self.window._toggle_lan_server())
            for unused in range(200):
                if not self.window._maintenance_busy:
                    break
                time.sleep(0.01)

        prepare.assert_called_once_with(game_root, "Fast MS-1")
        self.assertIn("pins vehicle profile", self._log_text())
        self.assertIn("Fast MS-1", self._log_text())

    def test_lan_room_server_start_failure_restores_vehicle_data(self):
        game_root = self._game("0.9.22.0.1", "1513")
        self.window.mode.set(core.MODE_JOIN)
        self.window._refresh_mode()
        order = []
        with mock.patch("core.install_client_mod", return_value=[]), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.prepare_vehicle_profile",
                    side_effect=lambda *unused: (
                        order.append("profile") or {
                            "profile": "Fast MS-1",
                            "installedMembers": 1,
                            "removedMembers": 0,
                        })), \
                mock.patch.object(
                    self.window, "_start_server",
                    side_effect=lambda *args, **kwargs: (
                        order.append("server") or False)), \
                mock.patch.object(
                    self.window, "_start_worker") as start_worker, \
                mock.patch.object(
                    self.window, "_stop_worker",
                    side_effect=lambda *args, **kwargs:
                    order.append("worker_stop")), \
                mock.patch.object(
                    self.window, "_stop_server",
                    side_effect=lambda *args, **kwargs: (
                        order.append("server_stop") or False)), \
                mock.patch(
                    "core.wait_for_game_shutdown",
                    side_effect=lambda: order.append("shutdown_wait") or True), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.ensure_original_vehicle_data",
                    side_effect=lambda *unused:
                    order.append("profile_cleanup") or 1) as cleanup:
            self.window.vehicle_profile.set("Fast MS-1")
            self.assertTrue(self.window._toggle_lan_server())
            for unused in range(200):
                if not self.window._maintenance_busy:
                    break
                time.sleep(0.01)

        start_worker.assert_not_called()
        cleanup.assert_called_once_with(game_root)
        self.assertEqual(
            ["profile", "server", "worker_stop", "server_stop",
             "shutdown_wait", "profile_cleanup"], order)
        self.assertIsNone(self.window._room_vehicle_overlay_root)

    def test_lan_room_worker_start_failure_restores_vehicle_data(self):
        game_root = self._game("0.9.22.0.1", "1513")
        self.window.mode.set(core.MODE_JOIN)
        self.window._refresh_mode()
        order = []
        with mock.patch("core.install_client_mod", return_value=[]), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.prepare_vehicle_profile",
                    return_value={"profile": "Fast MS-1",
                                  "installedMembers": 1,
                                  "removedMembers": 0}), \
                mock.patch.object(
                    self.window, "_start_server", return_value=True), \
                mock.patch.object(
                    self.window, "_start_worker", return_value=False), \
                mock.patch.object(
                    self.window, "_stop_worker",
                    side_effect=lambda *args, **kwargs:
                    order.append("worker_stop")), \
                mock.patch.object(
                    self.window, "_stop_server",
                    side_effect=lambda *args, **kwargs: (
                        order.append("server_stop") or True)), \
                mock.patch(
                    "core.wait_for_game_shutdown",
                    side_effect=lambda: order.append("shutdown_wait") or True), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.ensure_original_vehicle_data",
                    side_effect=lambda *unused:
                    order.append("profile_cleanup") or 1) as cleanup:
            self.window.vehicle_profile.set("Fast MS-1")
            self.assertTrue(self.window._toggle_lan_server())
            for unused in range(200):
                if not self.window._maintenance_busy:
                    break
                time.sleep(0.01)

        cleanup.assert_called_once_with(game_root)
        self.assertEqual(
            ["worker_stop", "server_stop", "shutdown_wait",
             "profile_cleanup"], order)
        self.assertIsNone(self.window._room_vehicle_overlay_root)
        self.assertIn("hidden simulation worker is unavailable",
                      self._log_text())

    def test_stopping_lan_room_restores_vehicle_data_after_processes(self):
        game_root = self._game("0.9.22.0.1", "1513")
        self.window.mode.set(core.MODE_JOIN)
        self.window._server = _Process(exit_code=None)
        self.window._room_vehicle_overlay_root = game_root
        order = []
        with mock.patch.object(
                self.window, "_stop_worker",
                side_effect=lambda *args, **kwargs:
                order.append("worker_stop")), \
                mock.patch.object(
                    self.window, "_stop_server",
                    side_effect=lambda *args, **kwargs: (
                        order.append("server_stop") or True)), \
                mock.patch(
                    "core.wait_for_game_shutdown",
                    side_effect=lambda: order.append("shutdown_wait") or True), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.ensure_original_vehicle_data",
                    side_effect=lambda *unused:
                    order.append("profile_cleanup") or 1) as cleanup:
            self.assertTrue(self.window._toggle_lan_server())

        cleanup.assert_called_once_with(game_root)
        self.assertEqual(
            ["worker_stop", "server_stop", "shutdown_wait",
             "profile_cleanup"], order)
        self.assertIsNone(self.window._room_vehicle_overlay_root)

    def test_0_8_2_folder_cannot_start_a_server(self):
        self._game("0.8.2", "335")
        self.window.mode.set(core.MODE_JOIN)
        self.window._refresh_mode()
        with mock.patch.object(self.window, "_start_server") as start_server:
            self.assertFalse(self.window._toggle_lan_server())

        start_server.assert_not_called()
        self.assertIn("supported game folder", self._log_text())

    def test_hidden_server_entry_rejects_0_8_2(self):
        with mock.patch("builtins.print") as output:
            self.assertEqual(
                2, wot_launcher._serve(
                    [core.SERVE_FLAG, core.PORT_0_8_2]))

        self.assertIn("Unsupported client version", output.call_args.args[0])

    def test_an_empty_folder_asks_for_the_game_executable(self):
        self.window.game_root.set("")
        self.assertIn(core.GAME_EXECUTABLE,
                      self.window.client_label.cget("text"))

    def test_a_folder_without_the_executable_is_reported(self):
        self.window.game_root.set(self.settings_dir)
        self.assertIn("was not found", self.window.client_label.cget("text"))

    def test_browsing_fills_in_the_selected_folder(self):
        self.dialog.selection = self.settings_dir
        self.window._browse()
        self.assertEqual(self.window.game_root.get(),
                         os.path.normpath(self.settings_dir))

    def test_start_reports_the_problem_and_runs_nothing(self):
        self.window.game_root.set(self.settings_dir)
        self.window._start()
        self.assertIn(core.GAME_EXECUTABLE, self._log_text())
        self.assertFalse(self.window._busy)

    def test_start_reports_an_invalid_join_address(self):
        game_root = self._game()
        self.window.mode.set(core.MODE_JOIN)
        self.window.join_address.set("")
        self.window._start()
        self.assertIn("Enter the address", self._log_text())
        self.assertFalse(self.window._busy)

    def test_settings_survive_a_new_window(self):
        self.window.game_root.set(self.settings_dir)
        self.window.mode.set(core.MODE_JOIN)
        self.window.player_name.set("Peng")
        self.window._save_settings()
        reopened = wot_launcher.LauncherWindow(_FakeTk, _FakeTtk, self.dialog)
        self.assertEqual(reopened.mode.get(), core.MODE_JOIN)
        self.assertEqual(reopened.player_name.get(), "Peng")

    def test_legacy_team_size_settings_are_ignored_and_removed(self):
        game_root = self._game("0.9.22.0.1", "1513")
        core.save_settings({
            "game_root": game_root,
            "team_size": "invalid",
            "team1_size": 0,
            "team2_size": 99,
        })
        reopened = wot_launcher.LauncherWindow(
            _FakeTk, _FakeTtk, self.dialog)
        session = {
            "client": core.PORT_0_9_22,
            "mode": core.MODE_SINGLE,
            "host": core.LOCAL_HOST,
            "tcp_port": core.DEFAULT_SERVER_PORT,
            "needs_server": True,
            "vehicle_profile": None,
        }
        with mock.patch("core.plan_session", return_value=session) as plan, \
                mock.patch("wot_launcher.threading.Thread") as thread:
            reopened._start()

        self.assertNotIn("team_size", plan.call_args.kwargs)
        self.assertNotIn("team1_size", plan.call_args.kwargs)
        self.assertNotIn("team2_size", plan.call_args.kwargs)
        thread.return_value.start.assert_called_once_with()
        reopened._save_settings()
        saved = core.load_settings()
        self.assertNotIn("team_size", saved)
        self.assertNotIn("team1_size", saved)
        self.assertNotIn("team2_size", saved)

    def test_legacy_preferred_team_is_ignored_and_removed(self):
        game_root = self._game("0.9.22.0.1", "1513")
        core.save_settings({
            "game_root": game_root,
            "preferred_team": 2,
        })
        reopened = wot_launcher.LauncherWindow(
            _FakeTk, _FakeTtk, self.dialog)
        session = {
            "client": core.PORT_0_9_22,
            "mode": core.MODE_SINGLE,
            "host": core.LOCAL_HOST,
            "tcp_port": core.DEFAULT_SERVER_PORT,
            "needs_server": True,
            "vehicle_profile": None,
        }
        with mock.patch("core.plan_session", return_value=session) as plan, \
                mock.patch("wot_launcher.threading.Thread") as thread:
            reopened._start()

        self.assertNotIn("preferred_team", plan.call_args.kwargs)
        thread.return_value.start.assert_called_once_with()
        reopened._save_settings()
        self.assertNotIn("preferred_team", core.load_settings())


    def test_a_selected_game_folder_joins_the_known_list(self):
        game = os.path.join(self.settings_dir, "game")
        os.makedirs(game)
        with open(os.path.join(game, core.GAME_EXECUTABLE), "w") as stream:
            stream.write("")
        self.dialog.selection = game
        self.window._browse()
        self.assertEqual([game], self.window._folders)
        self.assertEqual([game], self.window.folder_box.cget("values"))
        reopened = wot_launcher.LauncherWindow(_FakeTk, _FakeTtk, self.dialog)
        self.assertEqual([game], reopened._folders)
        self.assertEqual(game, reopened.game_root.get())

    def test_a_folder_without_the_game_is_not_remembered(self):
        self.dialog.selection = self.settings_dir
        self.window._browse()
        self.assertEqual([], self.window._folders)

    def test_maintenance_buttons_are_only_enabled_for_0_9_22(self):
        self._game("0.8.2", "335")
        self.assertEqual("disabled",
                         self.window.repair_button.cget("state"))
        self.assertEqual("disabled",
                         self.window.vehicle_editor_button.cget("state"))
        self._game("0.9.22.0.1", "1513")
        self.assertEqual("normal", self.window.repair_button.cget("state"))
        self.assertEqual(
            "normal", self.window.normal_preferences_button.cget("state"))
        self.assertEqual("normal", self.window.reset_button.cget("state"))
        self.assertEqual(
            "readonly", self.window.vehicle_profile_box.cget("state"))
        self.assertEqual("normal", self.window.new_profile_button.cget("state"))
        self.assertEqual(
            "disabled", self.window.vehicle_editor_button.cget("state"))

    def test_vehicle_editor_opens_for_the_selected_0_9_22_folder(self):
        with mock.patch(
                "wot_launcher.vehicle_overlays.list_vehicle_profiles",
                return_value=["Fast MS-1"]):
            game_root = self._game("0.9.22.0.1", "1513")
        self.window.vehicle_profile.set("Fast MS-1")
        self.window._profile_selected()
        self.window.language = wot_launcher.i18n.LANGUAGE_CHINESE
        with mock.patch(
                "wot_launcher.vehicle_overlays.list_vehicle_profiles",
                return_value=["Fast MS-1"]), mock.patch(
                "wot_launcher.vehicle_editor_ui.open_vehicle_editor") as open_editor:
            self.assertTrue(self.window._open_vehicle_editor())

        open_editor.assert_called_once_with(
            self.window.root, game_root, "Fast MS-1", log=self.window._log,
            language=wot_launcher.i18n.LANGUAGE_CHINESE)

    def test_vehicle_profile_selector_is_available_in_online_mode(self):
        with mock.patch(
                "wot_launcher.vehicle_overlays.list_vehicle_profiles",
                return_value=["Fast MS-1"]):
            self._game("0.9.22.0.1", "1513")
        self.window.vehicle_profile.set("Fast MS-1")
        self.window._profile_selected()
        self.assertEqual(
            "normal", self.window.vehicle_editor_button.cget("state"))

        self.window.mode.set(core.MODE_JOIN)
        self.window._refresh_mode()

        self.assertEqual(
            "Fast MS-1",
            self.window.vehicle_profile.get())
        self.assertEqual(
            "readonly", self.window.vehicle_profile_box.cget("state"))
        self.assertEqual(
            "normal", self.window.vehicle_editor_button.cget("state"))

    def test_stale_profile_recovery_reports_an_unsafe_manifest_path(self):
        self._game("0.9.22.0.1", "1513")
        with mock.patch(
                "wot_launcher.vehicle_overlays.manifest_path",
                side_effect=wot_launcher.vehicle_overlays.VehicleOverlayError(
                    "unsafe overlay path")):
            self.assertEqual(0, self.window._recover_stale_vehicle_profile())

        self.assertIn("could not be checked", self._log_text())
        self.assertIn("unsafe overlay path", self._log_text())

    def test_new_profile_is_selected_and_opened(self):
        with mock.patch(
                "wot_launcher.vehicle_overlays.list_vehicle_profiles",
                side_effect=[[], [], ["Fast MS-1"], ["Fast MS-1"]]), \
                mock.patch.object(
                    self.window, "_ask_profile_name",
                    return_value="Fast MS-1"), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.create_vehicle_profile",
                    return_value="Fast MS-1") as create, \
                mock.patch(
                    "wot_launcher.vehicle_editor_ui.open_vehicle_editor") as editor:
            game_root = self._game("0.9.22.0.1", "1513")
            self.assertTrue(self.window._new_vehicle_profile())

        create.assert_called_once_with(game_root, "Fast MS-1")
        editor.assert_called_once_with(
            self.window.root, game_root, "Fast MS-1", log=self.window._log,
            language=wot_launcher.i18n.LANGUAGE_ENGLISH)
        self.assertEqual("Fast MS-1", self.window.vehicle_profile.get())

    def test_single_player_profile_is_removed_after_a_launch_failure(self):
        session = {
            "client": core.PORT_0_9_22,
            "host": core.LOCAL_HOST,
            "tcp_port": core.DEFAULT_SERVER_PORT,
            "needs_server": False,
            "mode": core.MODE_SINGLE,
            "vehicle_profile": "Fast MS-1",
        }
        prepared = {
            "profile": "Fast MS-1",
            "installedMembers": 1,
            "removedMembers": 0,
        }
        with mock.patch("core.install_client_mod", return_value=[]), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.prepare_vehicle_profile",
                    return_value=prepared) as prepare, \
                mock.patch(
                    "wot_launcher.vehicle_overlays.ensure_original_vehicle_data",
                    return_value=1) as cleanup, \
                mock.patch(
                    "core.ensure_0_9_22_preferences_isolation",
                    return_value="preferences isolated"), \
                mock.patch(
                    "core.wait_for_game_shutdown",
                    return_value=True), \
                mock.patch("core.write_settings", return_value=[]), \
                mock.patch.object(
                    self.window, "_run_game",
                    side_effect=RuntimeError("synthetic launch failure")):
            self.window._run_session(self.settings_dir, session, "Peng")

        prepare.assert_called_once_with(self.settings_dir, "Fast MS-1")
        cleanup.assert_called_once_with(self.settings_dir)
        self.assertIn("temporary vehicle profile", self._log_text())

    def test_session_report_boundary_wraps_every_game_launch(self):
        session = {
            "client": core.PORT_0_9_22,
            "host": "10.0.0.5",
            "tcp_port": core.DEFAULT_SERVER_PORT,
            "needs_server": False,
            "mode": core.MODE_JOIN,
            "vehicle_profile": None,
        }
        boundary = {"id": "20260823T120000Z-111111111111"}
        order = []
        with mock.patch.object(
                wot_launcher.error_reports, "begin_session",
                side_effect=lambda *args, **kwargs: (
                    order.append("begin") or boundary)) as begin, \
                mock.patch.object(
                    wot_launcher.error_reports, "finalize_session",
                    side_effect=lambda value: (
                        order.append("finalize") or True)) as finalize, \
                mock.patch("core.install_client_mod", return_value=[]), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.prepare_vehicle_profile",
                    return_value={"profile": None, "installedMembers": 0,
                                  "removedMembers": 0}), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.ensure_original_vehicle_data",
                    return_value=0), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.vehicle_overlay_digest",
                    return_value=""), \
                mock.patch(
                    "core.fetch_vehicle_overlay",
                    return_value={"supported": True, "present": False,
                                  "digest": "", "profile": "",
                                  "manifest": None, "payload": {}}), \
                mock.patch(
                    "core.ensure_0_9_22_preferences_isolation",
                    return_value="preferences isolated"), \
                mock.patch("core.write_settings", return_value=[]), \
                mock.patch("core.listener_status",
                           return_value=core.LISTENER_COMPATIBLE), \
                mock.patch.object(
                    self.window, "_run_game",
                    side_effect=lambda *args, **kwargs: order.append("game")), \
                mock.patch.object(self.window, "_stop_worker"), \
                mock.patch.object(self.window, "_stop_server"), \
                mock.patch("core.wait_for_game_shutdown", return_value=True):
            self.window._run_session(self.settings_dir, session, "Peng")

        begin.assert_called_once_with(
            self.settings_dir, needs_worker=False, local_server=False)
        finalize.assert_called_once_with(boundary)
        self.assertEqual(["begin", "game", "finalize"], order)
        self.assertIsNone(self.window._active_report_session)

    def _join_session_overlay_mocks(self, fetched, local_digest,
                                    install_returns=1):
        manifest = {
            "schema": 1,
            "targetVersion": "0.9.22.0.1",
            "targetBuild": "1513",
            "sourcePackage": "res/packages/scripts.pkg",
            "members": [],
        }
        fetched = dict(fetched)
        fetched.setdefault("manifest", manifest)
        fetched.setdefault("payload", {})
        return mock.patch(
            "wot_launcher.vehicle_overlays.vehicle_overlay_digest",
            return_value=local_digest), mock.patch(
            "core.fetch_vehicle_overlay", return_value=fetched), mock.patch(
            "wot_launcher.vehicle_overlays.install_vehicle_overlay",
            return_value=install_returns)

    def test_join_installs_the_host_vehicle_overlay(self):
        session = {
            "client": core.PORT_0_9_22,
            "host": "10.0.0.5",
            "tcp_port": core.DEFAULT_SERVER_PORT,
            "needs_server": False,
            "mode": core.MODE_JOIN,
            "vehicle_profile": None,
        }
        digest, fetch, install = self._join_session_overlay_mocks(
            {"supported": True, "present": True, "digest": "d" * 64,
             "profile": "Fast MS-1"}, local_digest="")
        with mock.patch(
                "core.install_client_mod", return_value=[]), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.prepare_vehicle_profile",
                    return_value={"profile": None, "installedMembers": 0,
                                  "removedMembers": 0}), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.ensure_original_vehicle_data",
                    return_value=0), digest, fetch as fetch_mock, \
                install as install_mock, \
                mock.patch(
                    "core.ensure_0_9_22_preferences_isolation",
                    return_value="preferences isolated"), \
                mock.patch("core.write_settings", return_value=[]), \
                mock.patch("core.listener_status",
                           return_value=core.LISTENER_COMPATIBLE), \
                mock.patch.object(self.window, "_run_game") as run_game, \
                mock.patch.object(self.window, "_stop_worker"), \
                mock.patch.object(self.window, "_stop_server"), \
                mock.patch("core.wait_for_game_shutdown", return_value=True):
            self.window._run_session(self.settings_dir, session, "Peng")

        install_mock.assert_called_once_with(
            self.settings_dir, fetch_mock.return_value["manifest"],
            fetch_mock.return_value["payload"])
        run_game.assert_called_once()
        self.assertIn("Installed the host vehicle data profile", self._log_text())
        self.assertIn("Fast MS-1", self._log_text())

    def test_owned_room_keeps_its_pinned_vehicle_data(self):
        session = {
            "client": core.PORT_0_9_22,
            "host": core.LOCAL_HOST,
            "tcp_port": core.DEFAULT_SERVER_PORT,
            "needs_server": False,
            "mode": core.MODE_JOIN,
            "vehicle_profile": "Fast MS-1",
        }
        self.window._server = mock.Mock()
        self.window._server.poll.return_value = None
        self.window._server_context = {
            "game_root": os.path.normcase(os.path.realpath(
                self.settings_dir)),
        }
        digest, fetch, install = self._join_session_overlay_mocks(
            {"supported": True, "present": True, "digest": "d" * 64,
             "profile": "Fast MS-1"}, local_digest="d" * 64)
        with mock.patch(
                "core.install_client_mod", return_value=[]), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.prepare_vehicle_profile",
                    return_value={"profile": None, "installedMembers": 0,
                                  "removedMembers": 0}) as prepare, \
                mock.patch(
                    "wot_launcher.vehicle_overlays.ensure_original_vehicle_data",
                    return_value=0), digest, fetch, install as install_mock, \
                mock.patch(
                    "core.ensure_0_9_22_preferences_isolation",
                    return_value="preferences isolated"), \
                mock.patch("core.write_settings", return_value=[]), \
                mock.patch("core.listener_status",
                           return_value=core.LISTENER_COMPATIBLE), \
                mock.patch.object(self.window, "_run_game") as run_game, \
                mock.patch.object(self.window, "_stop_worker"), \
                mock.patch.object(self.window, "_stop_server"), \
                mock.patch("core.wait_for_game_shutdown", return_value=True):
            self.window._run_session(self.settings_dir, session, "Peng")

        # The running room's hidden worker is a WorldOfTanks.exe; the local
        # prepare must not touch the pinned overlay and must not be called.
        prepare.assert_not_called()
        install_mock.assert_not_called()
        run_game.assert_called_once()
        self.assertIn("keeps the vehicle data it pinned", self._log_text())

    def test_owned_room_refuses_when_local_data_changed(self):
        session = {
            "client": core.PORT_0_9_22,
            "host": core.LOCAL_HOST,
            "tcp_port": core.DEFAULT_SERVER_PORT,
            "needs_server": False,
            "mode": core.MODE_JOIN,
            "vehicle_profile": None,
        }
        self.window._server = mock.Mock()
        self.window._server.poll.return_value = None
        self.window._server_context = {
            "game_root": os.path.normcase(os.path.realpath(
                self.settings_dir)),
        }
        digest, fetch, install = self._join_session_overlay_mocks(
            {"supported": True, "present": True, "digest": "d" * 64,
             "profile": "Fast MS-1"}, local_digest="e" * 64)
        with mock.patch(
                "core.install_client_mod", return_value=[]), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.prepare_vehicle_profile",
                    return_value={"profile": None, "installedMembers": 0,
                                  "removedMembers": 0}) as prepare, \
                mock.patch(
                    "wot_launcher.vehicle_overlays.ensure_original_vehicle_data",
                    return_value=0), digest, fetch, install as install_mock, \
                mock.patch(
                    "core.ensure_0_9_22_preferences_isolation",
                    return_value="preferences isolated"), \
                mock.patch("core.write_settings", return_value=[]), \
                mock.patch("core.listener_status",
                           return_value=core.LISTENER_COMPATIBLE), \
                mock.patch.object(self.window, "_run_game") as run_game, \
                mock.patch.object(self.window, "_stop_worker"), \
                mock.patch.object(self.window, "_stop_server"), \
                mock.patch("core.wait_for_game_shutdown", return_value=True):
            self.window._run_session(self.settings_dir, session, "Peng")

        prepare.assert_not_called()
        install_mock.assert_not_called()
        run_game.assert_not_called()
        self.assertIn("pinned to the vehicle profile", self._log_text())

    def _run_join_session_with_crash_result(
            self, crashed, collect=True, client=core.PORT_0_9_22):
        session = {
            "client": client,
            "host": "10.0.0.5",
            "tcp_port": core.DEFAULT_SERVER_PORT,
            "needs_server": False,
            "mode": core.MODE_JOIN,
            "team_size": core.DEFAULT_TEAM_SIZE,
            "vehicle_profile": None,
            wot_launcher.COLLECT_CRASH_REPORTS_SETTING: collect,
        }
        report = {
            "path": os.path.join(self.settings_dir, "reports", "crash.zip"),
            "included": ("visible-client.dmp",),
            "missing": (),
            "notRun": (),
        }

        def enable(unused_boundary, requested, full_memory=False):
            self.window._crash_capture_enabled = bool(requested)
            self.window._full_crash_dump_enabled = bool(full_memory)
            self.window._procdump_path = (
                "C:\\tools\\procdump.exe" if requested else None)
            return bool(requested)

        with mock.patch("core.install_client_mod", return_value=[]), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.prepare_vehicle_profile",
                    return_value={"profile": None, "installedMembers": 0,
                                  "removedMembers": 0}), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.ensure_original_vehicle_data",
                    return_value=0), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.vehicle_overlay_digest",
                    return_value=""), \
                mock.patch(
                    "core.fetch_vehicle_overlay",
                    return_value={"supported": True, "present": False,
                                  "digest": "", "profile": "",
                                  "manifest": None, "payload": {}}), \
                mock.patch(
                    "core.ensure_0_9_22_preferences_isolation",
                    return_value="preferences isolated"), \
                mock.patch("core.write_settings", return_value=[]), \
                mock.patch("core.listener_status",
                           return_value=core.LISTENER_COMPATIBLE), \
                mock.patch.object(
                    self.window, "_enable_crash_capture",
                    side_effect=enable), \
                mock.patch.object(
                    self.window, "_run_game", return_value=crashed), \
                mock.patch.object(self.window, "_stop_worker"), \
                mock.patch.object(self.window, "_stop_server"), \
                mock.patch("core.wait_for_game_shutdown", return_value=True), \
                mock.patch.object(
                    wot_launcher.error_reports, "set_session_crash_roles",
                    wraps=wot_launcher.error_reports.set_session_crash_roles) \
                as set_roles, \
                mock.patch.object(
                    wot_launcher.error_reports, "create_report",
                    return_value=report) as create_report, \
                mock.patch.object(
                    self.window, "_offer_crash_report") as offer:
            self.window._run_session(self.settings_dir, session, "Peng")
        return set_roles, create_report, offer, report

    def test_unexpected_visible_exit_creates_zip_and_offers_to_report(self):
        set_roles, create_report, offer, report = (
            self._run_join_session_with_crash_result(True))

        self.assertEqual(
            {wot_launcher.error_reports.ROLE_VISIBLE_CLIENT},
            set(set_roles.call_args.args[1]))
        create_report.assert_called_once_with()
        offer.assert_called_once_with(report["path"])

    def test_unexpected_hidden_worker_exit_is_reported(self):
        session = {
            "client": core.PORT_0_9_22,
            "host": core.LOCAL_HOST,
            "tcp_port": core.DEFAULT_SERVER_PORT,
            "needs_server": False,
            "mode": core.MODE_SINGLE,
            "team_size": 7,
            "vehicle_profile": None,
            wot_launcher.COLLECT_CRASH_REPORTS_SETTING: True,
        }
        report = {
            "path": os.path.join(self.settings_dir, "reports", "worker.zip"),
            "included": ("hidden-worker.dmp",),
            "missing": (),
            "notRun": (),
        }

        def enable(unused_boundary, unused_requested, full_memory=False):
            self.window._crash_capture_enabled = True
            self.window._full_crash_dump_enabled = bool(full_memory)
            self.window._procdump_path = "C:\\tools\\procdump.exe"
            return True

        def start_worker(*unused_args, **unused_options):
            self.window._worker = _Process(exit_code=23)
            return True

        with mock.patch("core.install_client_mod", return_value=[]), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.prepare_vehicle_profile",
                    return_value={"profile": None, "installedMembers": 0,
                                  "removedMembers": 0}), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.ensure_original_vehicle_data",
                    return_value=0), \
                mock.patch(
                    "core.ensure_0_9_22_preferences_isolation",
                    return_value="preferences isolated"), \
                mock.patch("core.write_settings", return_value=[]), \
                mock.patch.object(
                    self.window, "_enable_crash_capture",
                    side_effect=enable), \
                mock.patch.object(
                    self.window, "_start_worker",
                    side_effect=start_worker), \
                mock.patch.object(
                    self.window, "_run_game", return_value=False), \
                mock.patch.object(self.window, "_stop_server"), \
                mock.patch("core.wait_for_game_shutdown", return_value=True), \
                mock.patch.object(
                    wot_launcher.error_reports, "set_session_crash_roles",
                    wraps=wot_launcher.error_reports.set_session_crash_roles) \
                as set_roles, \
                mock.patch.object(
                    wot_launcher.error_reports, "create_report",
                    return_value=report), \
                mock.patch.object(
                    self.window, "_offer_crash_report") as offer:
            self.window._run_session(self.settings_dir, session, "Peng")

        self.assertEqual(
            {wot_launcher.error_reports.ROLE_HIDDEN_WORKER},
            set(set_roles.call_args.args[1]))
        offer.assert_called_once_with(report["path"])

    def test_normal_exit_does_not_offer_a_crash_report(self):
        set_roles, create_report, offer, unused_report = (
            self._run_join_session_with_crash_result(False))

        self.assertEqual(set(), set(set_roles.call_args.args[1]))
        create_report.assert_not_called()
        offer.assert_not_called()

    def test_opted_out_session_does_not_collect_or_offer_after_a_crash(self):
        set_roles, create_report, offer, unused_report = (
            self._run_join_session_with_crash_result(True, collect=False))

        set_roles.assert_not_called()
        create_report.assert_not_called()
        offer.assert_not_called()

    def test_crash_collection_is_limited_to_the_0_9_22_client(self):
        set_roles, create_report, offer, unused_report = (
            self._run_join_session_with_crash_result(
                True, client=core.PORT_0_8_2))

        set_roles.assert_not_called()
        create_report.assert_not_called()
        offer.assert_not_called()

    def test_single_player_orders_server_worker_player_and_profile_cleanup(self):
        session = {
            "client": core.PORT_0_9_22,
            "host": core.LOCAL_HOST,
            "tcp_port": core.DEFAULT_SERVER_PORT,
            "needs_server": True,
            "mode": core.MODE_SINGLE,
            "vehicle_profile": "Fast MS-1",
        }
        order = []
        prepared = {
            "profile": "Fast MS-1",
            "installedMembers": 1,
            "removedMembers": 0,
        }
        with mock.patch("core.install_client_mod", return_value=[]), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.prepare_vehicle_profile",
                    side_effect=lambda *unused: (
                        order.append("profile") or prepared)), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.ensure_original_vehicle_data",
                    side_effect=lambda *unused: (
                        order.append("profile_cleanup") or 1)), \
                mock.patch(
                    "core.ensure_0_9_22_preferences_isolation",
                    return_value="preferences isolated"), \
                mock.patch("core.write_settings", return_value=[]), \
                mock.patch.object(
                    self.window, "_start_server",
                    side_effect=lambda *args, **kwargs: (
                        order.append("server") or True)) as start_server, \
                mock.patch.object(
                    self.window, "_start_worker",
                    side_effect=lambda *unused: (
                        order.append("worker") or True)) as start_worker, \
                mock.patch.object(
                    self.window, "_run_game",
                    side_effect=lambda *args, **kwargs: order.append("player")) \
                    as run_game, \
                mock.patch.object(
                    self.window, "_stop_worker",
                    side_effect=lambda: order.append("worker_stop")), \
                mock.patch.object(
                    self.window, "_stop_server",
                    side_effect=lambda: order.append("server_stop")), \
                mock.patch(
                    "core.wait_for_game_shutdown",
                    side_effect=lambda: (
                        order.append("shutdown_wait") or True)):
            self.window._run_session(self.settings_dir, session, "Peng")

        start_server.assert_called_once_with(
            self.settings_dir, core.PORT_0_9_22, loopback_only=True)
        start_worker.assert_called_once_with(
            self.settings_dir, core.LOCAL_HOST, core.DEFAULT_SERVER_PORT)
        run_game.assert_called_once_with(
            self.settings_dir, core.PORT_0_9_22, core.LOCAL_HOST,
            core.DEFAULT_SERVER_PORT, paired_worker=True)
        self.assertEqual(
            ["profile", "server", "worker", "player", "worker_stop",
             "server_stop", "shutdown_wait", "profile_cleanup"], order)

    def test_startup_repair_runs_in_the_background_and_reports_actions(self):
        game_root = self._game("0.9.22.0.1", "1513")
        with mock.patch(
                "core.repair_0_9_22_startup",
                return_value=["repair complete"]) as repair:
            self.assertTrue(self.window._repair_startup())
            for unused in range(200):
                if not self.window._maintenance_busy:
                    break
                time.sleep(0.01)

        repair.assert_called_once_with(game_root)
        self.assertIn("repair complete", self._log_text())
        self.assertEqual("normal", self.window.start_button.cget("state"))

    def test_normal_preferences_cleanup_runs_after_confirmation(self):
        game_root = self._game("0.9.22.0.1", "1513")
        with mock.patch.object(
                self.window, "_confirm_normal_preferences_cleanup",
                return_value=True), mock.patch(
                "core.game_is_running", return_value=False), mock.patch(
                "core.backup_normal_client_preferences",
                return_value=["normal preferences backed up"]) as cleanup:
            self.assertTrue(self.window._clean_normal_client_preferences())
            for unused in range(200):
                if not self.window._maintenance_busy:
                    break
                time.sleep(0.01)

        cleanup.assert_called_once_with(game_root)
        self.assertIn("normal preferences backed up", self._log_text())

    def test_reset_requires_confirmation(self):
        self._game("0.9.22.0.1", "1513")
        with mock.patch.object(
                self.window, "_confirm_reset", return_value=False), \
                mock.patch("core.reset_0_9_22_state") as reset:
            self.assertFalse(self.window._reset_all_state())

        reset.assert_not_called()
        self.assertIn("reset was cancelled", self._log_text())

    def test_confirmed_reset_runs_only_after_the_game_is_closed(self):
        game_root = self._game("0.9.22.0.1", "1513")
        with mock.patch.object(
                self.window, "_confirm_reset", return_value=True), \
                mock.patch("core.game_is_running", return_value=False), \
                mock.patch(
                    "core.reset_0_9_22_state",
                    return_value=["reset complete"]) as reset:
            self.assertTrue(self.window._reset_all_state())
            for unused in range(200):
                if not self.window._maintenance_busy:
                    break
                time.sleep(0.01)

        reset.assert_called_once_with(game_root)
        self.assertIn("reset complete", self._log_text())

    def test_reset_refuses_a_running_game_before_confirmation(self):
        self._game("0.9.22.0.1", "1513")
        with mock.patch.object(self.window, "_confirm_reset") as confirm, \
                mock.patch("core.game_is_running", return_value=True):
            self.assertFalse(self.window._reset_all_state())

        confirm.assert_not_called()
        self.assertIn("Close World of Tanks", self._log_text())

    def test_the_test_button_probes_the_typed_address(self):
        probed = []
        self._game()
        self.addCleanup(setattr, core, "listener_status", core.listener_status)
        core.listener_status = lambda client, host, port: (
            probed.append((client, host, port)) or core.LISTENER_COMPATIBLE)
        self.window.mode.set(core.MODE_JOIN)
        self.window.join_address.set("10.0.0.5:1234")
        self.assertTrue(self.window._test_connection())
        for attempt in range(200):
            if probed:
                break
            time.sleep(0.01)
        self.assertEqual([(core.PORT_0_9_22, "10.0.0.5", 1234)], probed)
        self.assertIn("Testing 10.0.0.5:1234", self._log_text())

    def test_the_test_button_reports_an_invalid_address(self):
        self._game()
        self.window.mode.set(core.MODE_JOIN)
        self.window.join_address.set("")
        self.assertFalse(self.window._test_connection())
        self.assertIn("Enter the address", self._log_text())

    def test_a_matching_existing_server_is_reused(self):
        with mock.patch("core.listener_status",
                        return_value=core.LISTENER_COMPATIBLE), \
                mock.patch("wot_launcher.subprocess.Popen") as popen:
            self.assertTrue(self.window._start_server(
                self.settings_dir, core.PORT_0_9_22))
        popen.assert_not_called()
        self.assertIn("already running", self._log_text())

    def test_an_unrelated_listener_blocks_server_start(self):
        with mock.patch("core.listener_status",
                        return_value=core.LISTENER_OCCUPIED), \
                mock.patch("wot_launcher.subprocess.Popen") as popen:
            self.assertFalse(self.window._start_server(
                self.settings_dir, core.PORT_0_9_22))
        popen.assert_not_called()
        self.assertIn("does not speak", self._log_text())

    def test_single_player_refuses_an_external_compatible_server(self):
        with mock.patch("core.listener_status",
                        return_value=core.LISTENER_COMPATIBLE), \
                mock.patch("wot_launcher.subprocess.Popen") as popen:
            self.assertFalse(self.window._start_server(
                self.settings_dir, core.PORT_0_9_22,
                loopback_only=True))
        popen.assert_not_called()
        self.assertIn("fresh launcher-owned server", self._log_text())

    def test_exact_lineup_refuses_an_external_compatible_server(self):
        lineup = [{
            "team": 2, "slot": 0,
            "vehicle": "germany:G12_Ltraktor",
        }]
        with mock.patch("core.listener_status",
                        return_value=core.LISTENER_COMPATIBLE), \
                mock.patch("wot_launcher.subprocess.Popen") as popen:
            self.assertFalse(self.window._start_server(
                self.settings_dir, core.PORT_0_9_22,
                bot_lineup=lineup))
        popen.assert_not_called()
        self.assertIn("exact Bot lineup", self._log_text())

    def test_launcher_owned_server_receives_the_exact_lineup(self):
        server = _Process()
        lineup = [{
            "team": 2, "slot": 0,
            "vehicle": "germany:G12_Ltraktor",
        }]
        with mock.patch("core.listener_status",
                        return_value=core.LISTENER_FREE), \
                mock.patch("core.wait_for_server", return_value=True), \
                mock.patch("core.local_addresses", return_value=[]), \
                mock.patch("wot_launcher.subprocess.Popen",
                           return_value=server) as popen:
            self.assertTrue(self.window._start_server(
                self.settings_dir, core.PORT_0_9_22,
                bot_lineup=lineup))

        environment = popen.call_args.kwargs["env"]
        self.assertEqual(
            '[{"team":2,"slot":0,"vehicle":"germany:G12_Ltraktor"}]',
            environment[core.SERVER_BOT_LINEUP_ENV_0922])
        self.window._stop_server(force=True)

    def test_launcher_owned_server_reuse_requires_game_and_visibility_context(self):
        server = _Process()
        self.window._server = server
        game_root = os.path.realpath(self.settings_dir)
        self.window._server_context = {
            "game_root": os.path.normcase(game_root),
            "port_version": core.PORT_0_9_22,
            "loopback_only": False,
        }
        with mock.patch("core.listener_status") as listener:
            self.assertTrue(self.window._start_server(
                self.settings_dir, core.PORT_0_9_22))
            self.assertFalse(self.window._start_server(
                self.settings_dir, core.PORT_0_9_22, loopback_only=True))
            self.assertFalse(self.window._start_server(
                os.path.join(self.settings_dir, "other"),
                core.PORT_0_9_22))
        listener.assert_not_called()
        self.assertIn("different game or visibility", self._log_text())

    def test_persistent_server_survives_session_cleanup_until_stopped(self):
        server = _Process()
        with mock.patch("core.listener_status",
                        return_value=core.LISTENER_FREE), \
                mock.patch("core.wait_for_server", return_value=True), \
                mock.patch("core.local_addresses", return_value=[]), \
                mock.patch("wot_launcher.subprocess.Popen",
                           return_value=server) as popen:
            self.assertTrue(self.window._start_server(
                self.settings_dir, core.PORT_0_9_22, persistent=True))

        self.assertEqual(
            wot_launcher._no_console_flags(),
            popen.call_args.kwargs["creationflags"])
        self.assertTrue(self.window._server_persistent)
        self.assertIn(
            "Server log: %s" % core.server_log_path(), self._log_text())
        self.assertFalse(self.window._stop_server())
        self.assertFalse(server.terminated)
        self.assertTrue(self.window._stop_server(force=True))
        self.assertTrue(server.terminated)

    def test_new_session_server_uses_its_dedicated_log(self):
        server = _Process()
        boundary = {"id": "20260823T120000Z-111111111111"}
        dedicated = os.path.join(self.settings_dir, "session-server.log")
        self.window._active_report_session = boundary
        with mock.patch("core.listener_status",
                        return_value=core.LISTENER_FREE), \
                mock.patch("core.wait_for_server", return_value=True), \
                mock.patch("core.local_addresses", return_value=[]), \
                mock.patch.object(
                    wot_launcher.error_reports, "attach_server",
                    return_value=dedicated) as attach, \
                mock.patch("wot_launcher.subprocess.Popen",
                           return_value=server) as popen:
            self.assertTrue(self.window._start_server(
                self.settings_dir, core.PORT_0_9_22))

        attach.assert_called_once_with(boundary, dedicated=True)
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(
            boundary["id"],
            environment[wot_launcher.error_reports.SERVER_SESSION_ENV])
        self.assertIn("Server log: %s" % dedicated, self._log_text())
        self.window._stop_server(force=True)

    def test_server_entry_uses_the_session_log_selected_by_environment(self):
        selected = os.path.join(self.settings_dir, "selected-server.log")
        with mock.patch.object(
                wot_launcher.error_reports, "server_log_for_environment",
                return_value=selected), mock.patch.object(
                    wot_launcher, "_open_server_log") as open_log, \
                mock.patch("core.server_root", return_value="/server"), \
                mock.patch("core.run_server_payload") as run:
            self.assertEqual(0, wot_launcher._serve(
                [core.SERVE_FLAG, core.PORT_0_9_22]))

        open_log.assert_called_once_with(selected)
        run.assert_called_once_with(core.PORT_0_9_22)

    def test_server_process_output_is_teed_to_the_live_pipe_and_log_file(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        log_path = os.path.join(self.settings_dir, "server.log")
        with mock.patch.object(wot_launcher.sys, "stdout", stdout), \
                mock.patch.object(wot_launcher.sys, "stderr", stderr), \
                mock.patch("core.server_log_path", return_value=log_path):
            self.assertEqual(
                log_path, wot_launcher._open_server_log())
            log_stream = wot_launcher.sys.stdout._log_stream
            wot_launcher.sys.stdout.write("server stdout\n")
            wot_launcher.sys.stderr.write("server stderr\n")
            wot_launcher.sys.stdout.flush()
            wot_launcher.sys.stderr.flush()
            log_stream.close()

        self.assertEqual("server stdout\n", stdout.getvalue())
        self.assertEqual("server stderr\n", stderr.getvalue())
        with open(log_path, encoding="utf-8") as stream:
            saved = stream.read()
        self.assertIn("server stdout\n", saved)
        self.assertIn("server stderr\n", saved)

    def test_server_output_bypasses_a_narrow_windows_code_page(self):
        class _Cp1252Pipe(object):
            encoding = "cp1252"

            def __init__(self):
                self.buffer = io.BytesIO()

            def write(self, value):
                self.buffer.write(value.encode(self.encoding))
                return len(value)

            def flush(self):
                pass

        primary = _Cp1252Pipe()
        log_stream = io.StringIO()
        tee = wot_launcher._TeeTextStream(
            primary, log_stream, wot_launcher.threading.RLock())

        message = "服务器已启动\n"
        self.assertEqual(len(message), tee.write(message))

        self.assertEqual(message, primary.buffer.getvalue().decode("utf-8"))
        self.assertEqual(message, log_stream.getvalue())

    def test_server_log_keeps_only_the_current_bounded_run(self):
        log_path = os.path.join(self.settings_dir, "server.log")
        with open(log_path, "w", encoding="utf-8") as stream:
            stream.write("older server run\n")

        stream = wot_launcher._BoundedLogStream(
            log_path, max_bytes=80, retain_bytes=48)
        for index in range(12):
            stream.write("current line %02d\n" % index)
        stream.close()

        with open(log_path, "rb") as saved:
            payload = saved.read()
        self.assertLessEqual(len(payload), 80)
        self.assertNotIn(b"older server run", payload)
        self.assertIn(b"current line 11", payload)
        payload.decode("utf-8")

    def test_server_log_write_failure_keeps_the_live_pipe_running(self):
        class _FailingLog(object):
            def __init__(self):
                self.closed = False

            def write(self, unused_value):
                raise IOError("disk full")

            def flush(self):
                raise IOError("disk full")

            def close(self):
                self.closed = True

        primary = io.StringIO()
        log_stream = _FailingLog()
        tee = wot_launcher._TeeTextStream(
            primary, log_stream, wot_launcher.threading.RLock())

        self.assertEqual(len("still live\n"), tee.write("still live\n"))
        tee.flush()

        self.assertEqual("still live\n", primary.getvalue())
        self.assertTrue(log_stream.closed)

    def test_unavailable_server_log_does_not_stop_the_server(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        log_path = os.path.join(self.settings_dir, "server.log")
        with mock.patch.object(wot_launcher.sys, "stdout", stdout), \
                mock.patch.object(wot_launcher.sys, "stderr", stderr), \
                mock.patch("core.server_log_path", return_value=log_path), \
                mock.patch.object(
                    wot_launcher, "_BoundedLogStream",
                    side_effect=IOError("launcher folder is read-only")):
            self.assertIsNone(wot_launcher._open_server_log())
            self.assertIs(stdout, wot_launcher.sys.stdout)
            self.assertIs(stderr, wot_launcher.sys.stderr)

        self.assertIn("continuing with live output", stderr.getvalue())

    def test_worker_start_failure_reports_the_native_failure_log(self):
        starter = core.worker_starter_executable(self.settings_dir)
        with open(starter, "w") as stream:
            stream.write("starter")
        marker = core.worker_ready_marker(self.settings_dir)
        with open(marker, "w") as stream:
            stream.write("live-worker-marker")
        previous_marker_token = core.worker_ready_marker_token(
            self.settings_dir)
        with open(core.worker_failure_log(self.settings_dir), "w") as stream:
            stream.write("stage=worker_exited_before_ready exit_code=7\n")
        worker = _Process(exit_code=23)
        with mock.patch(
                "core.wait_for_worker_ready", return_value=False) as wait, \
                mock.patch("wot_launcher.subprocess.Popen",
                           return_value=worker) as popen:
            self.assertFalse(self.window._start_worker(
                self.settings_dir, "10.0.0.5", 1234))

        self.assertEqual(self.settings_dir, popen.call_args.kwargs["cwd"])
        self.assertEqual(
            wot_launcher._no_console_flags(),
            popen.call_args.kwargs["creationflags"])
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(
            "10.0.0.5", environment[core.CLIENT_SERVER_HOST_ENV_0922])
        self.assertEqual(
            "1234", environment[core.CLIENT_SERVER_PORT_ENV_0922])
        self.assertEqual(
            previous_marker_token,
            wait.call_args.kwargs["previous_marker_token"])
        self.assertTrue(os.path.isfile(marker))
        self.assertIn("worker_exited_before_ready", self._log_text())

    def test_worker_starter_receives_its_session_dump_destination(self):
        starter = core.worker_starter_executable(self.settings_dir)
        with open(starter, "w") as stream:
            stream.write("starter")
        boundary = wot_launcher.error_reports.begin_session(
            self.settings_dir, needs_worker=True,
            session_id="20260823T120000Z-111111111111")
        self.window._active_report_session = boundary
        self.window._crash_capture_enabled = True
        self.window._procdump_path = "C:\\tools\\procdump.exe"
        worker = _Process(exit_code=None)
        with mock.patch(
                "core.wait_for_worker_ready", return_value=True), \
                mock.patch("wot_launcher.subprocess.Popen",
                           return_value=worker) as popen:
            self.assertTrue(self.window._start_worker(
                self.settings_dir, "10.0.0.5", 1234))

        environment = popen.call_args.kwargs["env"]
        self.assertEqual(
            "C:\\tools\\procdump.exe",
            environment[wot_launcher.PROCDUMP_PATH_ENV])
        self.assertEqual(
            wot_launcher.error_reports.session_dump_path(
                boundary, wot_launcher.error_reports.ROLE_HIDDEN_WORKER),
            environment[wot_launcher.CRASH_DUMP_PATH_ENV])
        self.assertEqual(
            "mini", environment[wot_launcher.CRASH_DUMP_MODE_ENV])
        self.window._stop_worker()

    def test_crash_capture_environment_requests_full_memory_dumps(self):
        boundary = wot_launcher.error_reports.begin_session(
            self.settings_dir, needs_worker=True,
            session_id="20260823T120000Z-222222222222")
        self.window._active_report_session = boundary
        self.window._crash_capture_enabled = True
        self.window._full_crash_dump_enabled = True
        self.window._procdump_path = "C:\\tools\\procdump.exe"

        environment = self.window._crash_capture_environment(
            {}, wot_launcher.error_reports.ROLE_HIDDEN_WORKER)

        self.assertEqual(
            "full", environment[wot_launcher.CRASH_DUMP_MODE_ENV])

    def test_worker_stop_waits_for_the_native_starter_cleanup(self):
        worker = _Process(exit_code=None, pid=42)
        worker.wait = mock.Mock(return_value=0)
        self.window._worker = worker
        self.window._worker_starter_root = self.settings_dir

        with mock.patch.object(
                self.window, "_request_starter_stop",
                return_value=True) as request:
            self.assertIsNone(self.window._stop_worker())

        request.assert_called_once_with(worker, self.settings_dir)
        worker.wait.assert_called_once_with(
            timeout=core.STARTER_SHUTDOWN_TIMEOUT_SECONDS_0922)
        self.assertFalse(worker.terminated)
        self.assertFalse(worker.killed)

    def test_worker_crash_wins_a_simultaneous_starter_stop(self):
        worker = _Process(exit_code=None, pid=42)
        worker.wait = mock.Mock(return_value=23)
        self.window._worker = worker
        self.window._worker_starter_root = self.settings_dir

        with mock.patch.object(
                self.window, "_request_starter_stop", return_value=True):
            self.assertEqual(23, self.window._stop_worker())

        self.assertFalse(worker.terminated)
        self.assertIn(
            wot_launcher.error_reports.ROLE_HIDDEN_WORKER,
            self.window._observed_crash_roles)

    def test_clean_worker_termination_is_not_a_crash(self):
        worker = _Process(exit_code=3, pid=42)
        self.window._worker = worker
        self.window._worker_starter_root = self.settings_dir

        with mock.patch.object(
                wot_launcher.error_reports, "minidump_evidence",
                return_value=(
                    wot_launcher.error_reports.MINIDUMP_EVIDENCE_TERMINATION)), \
                mock.patch.object(
                    wot_launcher.error_reports, "client_exited_cleanly",
                    return_value=True) as clean_exit:
            self.assertEqual(3, self.window._stop_worker())

        clean_exit.assert_called_once_with(
            self.window._active_report_session,
            wot_launcher.error_reports.ROLE_HIDDEN_WORKER)
        self.assertNotIn(
            wot_launcher.error_reports.ROLE_HIDDEN_WORKER,
            self.window._observed_crash_roles)

    def test_worker_exception_dump_is_a_crash_even_with_zero_exit(self):
        worker = _Process(exit_code=0, pid=42)
        self.window._worker = worker
        self.window._worker_starter_root = self.settings_dir

        with mock.patch.object(
                wot_launcher.error_reports, "minidump_evidence",
                return_value=(
                    wot_launcher.error_reports.MINIDUMP_EVIDENCE_EXCEPTION)):
            self.assertEqual(0, self.window._stop_worker())

        self.assertIn(
            wot_launcher.error_reports.ROLE_HIDDEN_WORKER,
            self.window._observed_crash_roles)

    def test_close_game_uses_the_native_starter_stop_protocol(self):
        game = _Process(exit_code=None, pid=42)
        self.window._game = game
        self.window._game_starter_root = self.settings_dir

        with mock.patch.object(
                self.window, "_request_starter_stop",
                return_value=True) as request, \
                mock.patch.object(self.window, "_stop_worker"), \
                mock.patch.object(self.window, "_stop_server"), \
                mock.patch("core.kill_game") as kill_game:
            self.assertTrue(self.window._kill_game())

        request.assert_called_once_with(game, self.settings_dir)
        self.assertFalse(game.killed)
        kill_game.assert_not_called()
        self.assertEqual(set(), self.window._observed_crash_roles)

    def test_close_game_captures_an_already_exited_visible_client(self):
        game = _Process(exit_code=3, pid=42)
        self.window._game = game
        self.window._game_starter_root = self.settings_dir

        with mock.patch.object(
                self.window, "_request_starter_stop") as request, \
                mock.patch.object(self.window, "_stop_worker"), \
                mock.patch.object(self.window, "_stop_server"), \
                mock.patch("core.kill_game"):
            self.assertTrue(self.window._kill_game())

        request.assert_not_called()
        self.assertIn(
            wot_launcher.error_reports.ROLE_VISIBLE_CLIENT,
            self.window._observed_crash_roles)

    def test_forced_visible_starter_kill_is_not_a_crash(self):
        game = _Process(exit_code=None, pid=42)
        self.window._game = game
        self.window._game_starter_root = self.settings_dir

        with mock.patch.object(
                self.window, "_request_starter_stop", return_value=False), \
                mock.patch.object(self.window, "_stop_worker"), \
                mock.patch.object(self.window, "_stop_server"), \
                mock.patch("core.kill_game"):
            self.assertTrue(self.window._kill_game())

        self.assertTrue(game.killed)
        self.assertNotIn(
            wot_launcher.error_reports.ROLE_VISIBLE_CLIENT,
            self.window._observed_crash_roles)
        self.assertIn(
            wot_launcher.error_reports.ROLE_VISIBLE_CLIENT,
            self.window._forced_stop_roles)

    def test_starter_stop_helper_targets_the_started_process(self):
        game = _Process(exit_code=None, pid=42)
        result = mock.Mock(returncode=0)

        with mock.patch(
                "wot_launcher.subprocess.run", return_value=result) as run:
            self.assertTrue(self.window._request_starter_stop(
                game, self.settings_dir))

        self.assertEqual(
            core.starter_stop_command(self.settings_dir, 42),
            run.call_args.args[0])
        self.assertEqual(
            core.STARTER_CONTROL_TIMEOUT_SECONDS_0922,
            run.call_args.kwargs["timeout"])

    def test_visible_crash_wins_a_stop_helper_exit_race(self):
        game = _Process(exit_code=3, pid=42)
        forced = [False]

        with mock.patch.object(
                self.window, "_request_starter_stop", return_value=False):
            self.window._stop_visible_starter(
                game, self.settings_dir, forced)

        self.assertFalse(forced[0])
        self.assertFalse(game.terminated)

    def test_paired_player_starter_receives_its_dump_destination(self):
        boundary = wot_launcher.error_reports.begin_session(
            self.settings_dir,
            session_id="20260823T120000Z-222222222222")
        self.window._active_report_session = boundary
        self.window._crash_capture_enabled = True
        self.window._procdump_path = "C:\\tools\\procdump.exe"
        game = _Process(exit_code=None)
        with mock.patch(
                "wot_launcher.subprocess.Popen", return_value=game) as popen, \
                mock.patch(
                    "core.wait_for_paired_player_exit",
                    return_value=(0, False)):
            self.assertFalse(self.window._run_game(
                self.settings_dir, core.PORT_0_9_22, core.LOCAL_HOST,
                core.DEFAULT_SERVER_PORT, paired_worker=True))

        environment = popen.call_args.kwargs["env"]
        self.assertEqual(
            "C:\\tools\\procdump.exe",
            environment[wot_launcher.PROCDUMP_PATH_ENV])
        self.assertEqual(
            wot_launcher.error_reports.session_dump_path(
                boundary, wot_launcher.error_reports.ROLE_VISIBLE_CLIENT),
            environment[wot_launcher.CRASH_DUMP_PATH_ENV])

    def test_online_0_9_22_starter_receives_dump_destination(self):
        boundary = wot_launcher.error_reports.begin_session(
            self.settings_dir,
            session_id="20260823T120000Z-333333333333")
        self.window._active_report_session = boundary
        self.window._crash_capture_enabled = True
        self.window._procdump_path = "C:\\tools\\procdump.exe"
        game = _Process(exit_code=3, pid=42)
        with mock.patch(
                "wot_launcher.subprocess.Popen",
                return_value=game) as popen, \
                mock.patch("core.wait_for_game_exit") as wait_for_game_exit:
            self.assertTrue(self.window._run_game(
                self.settings_dir, core.PORT_0_9_22, core.LOCAL_HOST,
                core.DEFAULT_SERVER_PORT))

        self.assertEqual(
            [core.worker_starter_executable(self.settings_dir),
             core.PLAYER_ARGUMENT_0922],
            popen.call_args.args[0])
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(
            "C:\\tools\\procdump.exe",
            environment[wot_launcher.PROCDUMP_PATH_ENV])
        self.assertEqual(
            wot_launcher.error_reports.session_dump_path(
                boundary, wot_launcher.error_reports.ROLE_VISIBLE_CLIENT),
            environment[wot_launcher.CRASH_DUMP_PATH_ENV])
        self.assertIn("exit code 3", self._log_text())
        wait_for_game_exit.assert_not_called()

    def test_visible_crash_result_survives_a_simultaneous_close_request(self):
        game = _Process(exit_code=3, pid=42)
        self.window._stop_requested = True
        with mock.patch(
                "wot_launcher.subprocess.Popen", return_value=game):
            self.assertTrue(self.window._run_game(
                self.settings_dir, core.PORT_0_9_22, core.LOCAL_HOST,
                core.DEFAULT_SERVER_PORT, paired_worker=False))

        self.assertIn(
            wot_launcher.error_reports.ROLE_VISIBLE_CLIENT,
            self.window._observed_crash_roles)

    def test_paired_player_uses_the_starter_process_exit_monitor(self):
        game = _Process(exit_code=None)
        worker = _Process(exit_code=None)
        self.window._worker = worker
        with mock.patch(
                "wot_launcher.subprocess.Popen", return_value=game), \
                mock.patch(
                    "core.wait_for_paired_player_exit",
                    return_value=(1, False)) as wait:
            self.assertTrue(self.window._run_game(
                self.settings_dir, core.PORT_0_9_22, core.LOCAL_HOST,
                core.DEFAULT_SERVER_PORT, paired_worker=True))

        wait.assert_called_once_with(
            game, self.settings_dir, required_process=worker)
        self.assertIsNone(self.window._game)
        self.assertIn("exit code 1", self._log_text())
        self.assertIn("The game closed.", self._log_text())

    def test_worker_authority_exit_is_not_a_visible_client_crash(self):
        game = _Process(exit_code=None)
        worker = _Process(exit_code=9)
        self.window._worker = worker
        with mock.patch(
                "wot_launcher.subprocess.Popen", return_value=game), \
                mock.patch(
                    "core.wait_for_paired_player_exit",
                    return_value=(1, True)), \
                mock.patch.object(self.window, "_log_worker_failure") as log:
            self.assertFalse(self.window._run_game(
                self.settings_dir, core.PORT_0_9_22, core.LOCAL_HOST,
                core.DEFAULT_SERVER_PORT, paired_worker=True))

        self.assertNotIn(
            wot_launcher.error_reports.ROLE_VISIBLE_CLIENT,
            self.window._observed_crash_roles)
        self.assertIn(
            wot_launcher.error_reports.ROLE_HIDDEN_WORKER,
            self.window._observed_crash_roles)
        log.assert_called_once_with(self.settings_dir)
        self.assertNotIn("The game stopped with exit code 1", self._log_text())
        self.assertIn("worker stopped with exit code 9", self._log_text())

    def test_clean_worker_authority_exit_is_not_reported_as_failure(self):
        game = _Process(exit_code=None)
        worker = _Process(exit_code=3)
        self.window._worker = worker
        with mock.patch(
                "wot_launcher.subprocess.Popen", return_value=game), \
                mock.patch(
                    "core.wait_for_paired_player_exit",
                    return_value=(1, True)), \
                mock.patch.object(
                    wot_launcher.error_reports, "minidump_evidence",
                    return_value=(wot_launcher.error_reports.
                                  MINIDUMP_EVIDENCE_TERMINATION)), \
                mock.patch.object(
                    wot_launcher.error_reports, "client_exited_cleanly",
                    return_value=True), \
                mock.patch.object(
                    wot_launcher.error_reports,
                    "visible_client_exit_evidence",
                    return_value=(wot_launcher.error_reports.
                                  VISIBLE_CLIENT_EXIT_TERMINATED)), \
                mock.patch.object(self.window, "_log_worker_failure") as log:
            self.assertFalse(self.window._run_game(
                self.settings_dir, core.PORT_0_9_22, core.LOCAL_HOST,
                core.DEFAULT_SERVER_PORT, paired_worker=True))

        self.assertFalse(self.window._worker_exited_unexpectedly)
        self.assertNotIn(
            wot_launcher.error_reports.ROLE_HIDDEN_WORKER,
            self.window._observed_crash_roles)
        log.assert_not_called()
        self.assertIn("worker exited normally with code 3", self._log_text())

    def test_worker_exit_does_not_hide_a_real_visible_exception_stream(self):
        game = _Process(exit_code=None)
        worker = _Process(exit_code=9)
        exception_evidence = (
            wot_launcher.error_reports.VISIBLE_CLIENT_EXIT_EXCEPTION)
        self.window._worker = worker
        with mock.patch(
                "wot_launcher.subprocess.Popen", return_value=game), \
                mock.patch(
                    "core.wait_for_paired_player_exit",
                    return_value=(1, True)), \
                mock.patch.object(
                    wot_launcher.error_reports,
                    "visible_client_exit_evidence",
                    return_value=exception_evidence), \
                mock.patch.object(self.window, "_log_worker_failure"):
            self.assertTrue(self.window._run_game(
                self.settings_dir, core.PORT_0_9_22, core.LOCAL_HOST,
                core.DEFAULT_SERVER_PORT, paired_worker=True))

        self.assertEqual(
            {wot_launcher.error_reports.ROLE_VISIBLE_CLIENT,
             wot_launcher.error_reports.ROLE_HIDDEN_WORKER},
            self.window._observed_crash_roles)

    def test_paired_player_clean_log_makes_nonzero_starter_exit_normal(self):
        boundary = wot_launcher.error_reports.begin_session(
            self.settings_dir,
            session_id="20260823T120000Z-444444444444")
        self.window._active_report_session = boundary
        game = _Process(exit_code=None)
        worker = _Process(exit_code=None)
        self.window._worker = worker
        with mock.patch(
                "wot_launcher.subprocess.Popen", return_value=game), \
                mock.patch(
                    "core.wait_for_paired_player_exit",
                    return_value=(1, False)), \
                mock.patch.object(
                    wot_launcher.error_reports,
                    "visible_client_exited_cleanly",
                    return_value=True) as clean_exit:
            self.assertFalse(self.window._run_game(
                self.settings_dir, core.PORT_0_9_22, core.LOCAL_HOST,
                core.DEFAULT_SERVER_PORT, paired_worker=True))

        clean_exit.assert_called_once_with(boundary)
        self.assertNotIn(
            wot_launcher.error_reports.ROLE_VISIBLE_CLIENT,
            self.window._observed_crash_roles)
        self.assertNotIn("exit code 1", self._log_text())

    def test_paired_player_logs_worker_failure(self):
        game = _Process(exit_code=None)
        worker = _Process(exit_code=9)
        self.window._worker = worker
        with mock.patch(
                "wot_launcher.subprocess.Popen", return_value=game), \
                mock.patch(
                    "core.wait_for_paired_player_exit",
                    return_value=(0, False)), \
                mock.patch.object(self.window, "_log_worker_failure") as log:
            self.window._run_game(
                self.settings_dir, core.PORT_0_9_22, core.LOCAL_HOST,
                core.DEFAULT_SERVER_PORT, paired_worker=True)

        log.assert_called_once_with(self.settings_dir)
        self.assertIn(
            "worker stopped with exit code 9", self._log_text())

    def test_worker_failure_gives_owned_server_a_bounded_delivery_grace(self):
        game = _Process(exit_code=None)
        worker = _Process(exit_code=9)
        self.window._worker = worker
        self.window._server = _Process(exit_code=None)
        self.window._server_persistent = False
        with mock.patch(
                "wot_launcher.subprocess.Popen", return_value=game), \
                mock.patch(
                    "core.wait_for_paired_player_exit",
                    return_value=(0, False)), \
                mock.patch.object(self.window, "_log_worker_failure"), \
                mock.patch("wot_launcher.time.sleep") as sleep:
            self.window._run_game(
                self.settings_dir, core.PORT_0_9_22, core.LOCAL_HOST,
                core.DEFAULT_SERVER_PORT, paired_worker=True)

        sleep.assert_called_once_with(
            core.WORKER_FAILURE_DRAIN_SECONDS_0922)

    def test_join_does_not_start_the_game_for_an_unrelated_listener(self):
        session = {
            "client": core.PORT_0_9_22,
            "host": "10.0.0.5",
            "tcp_port": 28782,
            "needs_server": False,
            "mode": core.MODE_JOIN,
        }
        with mock.patch("core.install_client_mod", return_value=[]), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.prepare_vehicle_profile",
                    return_value={"profile": None, "installedMembers": 0,
                                  "removedMembers": 0}), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.ensure_original_vehicle_data",
                    return_value=0), \
                mock.patch(
                    "core.ensure_0_9_22_preferences_isolation",
                    return_value="preferences isolated") as isolate, \
                mock.patch("core.write_settings", return_value=[]), \
                mock.patch("core.listener_status",
                           return_value=core.LISTENER_OCCUPIED), \
                mock.patch.object(self.window, "_run_game") as run_game:
            self.window._run_session(self.settings_dir, session, "Peng")

        isolate.assert_called_once_with(self.settings_dir)
        run_game.assert_not_called()
        self.assertIn("not the server for this client", self._log_text())

    def test_0922_join_waits_for_a_compatible_server_before_starting(self):
        session = {
            "client": core.PORT_0_9_22,
            "host": "10.0.0.5",
            "tcp_port": 28782,
            "needs_server": False,
            "mode": core.MODE_JOIN,
        }
        with mock.patch("core.install_client_mod", return_value=[]), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.prepare_vehicle_profile",
                    return_value={"profile": None, "installedMembers": 0,
                                  "removedMembers": 0}), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.ensure_original_vehicle_data",
                    return_value=0), \
                mock.patch(
                    "core.ensure_0_9_22_preferences_isolation",
                    return_value="preferences isolated"), \
                mock.patch("core.write_settings", return_value=[]), \
                mock.patch("core.listener_status",
                           return_value=core.LISTENER_FREE), \
                mock.patch("core.fetch_vehicle_overlay") as fetch_overlay, \
                mock.patch.object(self.window, "_run_game") as run_game:
            self.window._run_session(self.settings_dir, session, "Peng")

        fetch_overlay.assert_not_called()
        run_game.assert_not_called()
        self.assertIn(
            "did not answer the compatible 0.9.22 protocol",
            self._log_text())

    def test_closing_the_window_saves_the_settings(self):
        self.window.player_name.set("Peng")
        self.window._on_close()
        self.assertEqual("Peng", core.load_settings().get("name"))

    def test_close_stops_children_but_waits_for_profile_cleanup(self):
        self.window._busy = True

        self.assertFalse(self.window._on_close())

        self.assertFalse(self.window.root.destroyed)
        self.assertTrue(self.window._close_pending)
        self.assertTrue(self.window._stop_requested)
        self.assertIn("Closing the game", self._log_text())

    def test_closing_the_window_stops_a_persistent_server(self):
        stopped = []

        class _Server(object):
            def poll(self):
                return None

            def terminate(self):
                stopped.append("terminate")

            def wait(self, timeout=None):
                return 0

        self.window._server = _Server()
        self.window._server_persistent = True
        self.window._room_vehicle_overlay_root = self.settings_dir
        with mock.patch(
                "core.wait_for_game_shutdown", return_value=True), \
                mock.patch(
                    "wot_launcher.vehicle_overlays.ensure_original_vehicle_data",
                    return_value=1) as cleanup:
            self.window._on_close()

        cleanup.assert_called_once_with(self.settings_dir)
        self.assertEqual(stopped, ["terminate"])
        self.assertIsNone(self.window._room_vehicle_overlay_root)
        self.assertTrue(self.window.root.destroyed)


if __name__ == "__main__":
    unittest.main()
