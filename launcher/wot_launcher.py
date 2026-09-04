#!/usr/bin/env python3
"""Desktop launcher for the supported World of Tanks offline-battle client.

The launcher installs client payloads, manages the hidden single-player
authority, and can run a persistent LAN server explicitly.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import bot_lineup_profiles
    import bot_lineup_ui
    import core
    import error_reports
    import i18n
    import vehicle_editor_ui
    import vehicle_overlays
else:
    from . import (
        bot_lineup_profiles, bot_lineup_ui, core, error_reports, i18n,
        vehicle_editor_ui, vehicle_overlays)


LAUNCHER_VERSION = "0.6.5"
WINDOW_TITLE = "World of Tanks Offline Battles %s" % LAUNCHER_VERSION

_CHINESE = {
    "Language": "语言",
    "Game client": "游戏客户端",
    "Game folder": "游戏目录",
    "Browse...": "浏览…",
    "Collect a report if the game crashes": "游戏闪退时收集报告",
    "Single player": "单人游戏",
    "Online": "联网游戏",
    "Player name": "玩家名",
    "The launcher starts the private server and hidden simulation client "
    "automatically.": "启动游戏时会自动运行隐藏服务器和模拟客户端。",
    "Start single-player battle": "开始单人战斗",
    "Server address": "服务器地址",
    "Test connection": "测试连接",
    "Start server": "启动服务器",
    "Stop server": "关闭服务器",
    "To host: start the server, then join the game. Other players use a LAN "
    "address shown in the log.": "作为主机：先启动服务器，再加入游戏；其他玩家使用日志中显示的局域网地址。",
    "Join network battle": "加入联网战斗",
    "Vehicle modifier": "坦克属性修改器",
    "Exact lineup": "阵容精确设置",
    "Bot lineup profile": "Bot 阵容方案",
    "New Bot lineup profile...": "新建 Bot 阵容方案…",
    "Edit Bot lineup...": "编辑 Bot 阵容…",
    "Delete Bot lineup profile...": "删除 Bot 阵容方案…",
    "New Bot lineup profile": "新建 Bot 阵容方案",
    "Delete Bot lineup profile?": "删除 Bot 阵容方案？",
    "Delete Bot lineup profile '%s'?": "删除 Bot 阵容方案“%s”？",
    "Vehicle data profile": "车辆属性方案",
    "New profile...": "新建方案…",
    "Edit selected profile...": "编辑所选方案…",
    "Delete selected profile...": "删除所选方案…",
    "The running LAN room keeps the vehicle data it pinned at room start; "
    "the selected profile applies when the room restarts.":
        "运行中的房间保持开房时固定的车辆数据；所选方案将在房间重启后生效。",
    "The running LAN room keeps the vehicle data it pinned at room start.":
        "运行中的房间保持开房时固定的车辆数据。",
    "The LAN room runs original vehicle data; no vehicle profile is shared.":
        "房间使用原版车辆数据，不共享任何属性方案。",
    "The LAN room pins vehicle profile '%s' (%d package member%s); joiners "
    "receive it automatically.":
        "房间已固定属性方案“%s”（%d 个数据包成员）；加入者将自动同步该方案。",
    "The host vehicle data could not be fetched: %s":
        "无法获取房主的车辆数据：%s",
    "The local vehicle data could not be verified: %s":
        "无法校验本机车辆数据：%s",
    "This server does not share vehicle data; the selected vehicle profile "
    "was ignored.": "该服务器不共享车辆数据；已忽略所选属性方案。",
    "This server does not share vehicle data; original vehicle values stay "
    "active.": "该服务器不共享车辆数据；继续使用原版车辆数值。",
    "The LAN room runs original vehicle data, but a vehicle profile is "
    "active locally. Stop the LAN room or select 'Original vehicle values', "
    "then start again.":
        "房间使用原版车辆数据，但本机激活了属性方案。请先关闭房间，或选择"
        "“原版车辆数值”后重新开始。",
    "The host runs original vehicle data; the selected vehicle profile was "
    "ignored.": "房主使用原版车辆数据；已忽略所选属性方案。",
    "The host runs original vehicle data; no vehicle profile is shared.":
        "房主使用原版车辆数据，不共享属性方案。",
    "Your vehicle data matches the room profile '%s'.":
        "本机车辆数据与房间方案“%s”一致。",
    "Your vehicle data matches the room.": "本机车辆数据与房间一致。",
    "The LAN room is pinned to the vehicle profile '%s'. Stop the LAN room "
    "and start it again after changing the vehicle profile.":
        "房间已固定属性方案“%s”。如需更换方案，请先关闭房间再重新启动。",
    "The host vehicle data could not be installed: %s":
        "无法安装房主的车辆数据：%s",
    "Installed the host vehicle data profile '%s' (%d package member%s); "
    "original data is restored after this session.":
        "已安装房主的车辆属性方案“%s”（%d 个数据包成员）；本次结束后恢复原版数据。",
    "The local vehicle profile could not be removed: %s":
        "无法移除本机车辆属性方案：%s",
    "Vehicle profiles are available for the 0.9.22 client.":
        "车辆属性方案仅适用于 0.9.22 客户端。",
    "Repair": "修复",
    "Repair startup (keep saved data)": "修复启动问题（保留存档）",
    "Normal client stuck loading? Clean preferences...":
        "正式客户端卡在加载界面？点击清理配置…",
    "Reset all offline data...": "重置全部离线数据…",
    "Create error report...": "一键汇报错误…",
    "Collect full-memory crash dumps (very large files)":
        "疑难闪退：收集完整内存（文件很大）",
    "Activity log": "运行日志",
    "Close game": "关闭游戏",
    "Select the folder that contains %s.": "请选择包含 %s 的目录。",
    "%s was not found in this folder.": "此目录中没有找到 %s。",
    "This client version is not supported.": "不支持此客户端版本。",
    "World of Tanks %s found. Starting the game installs the mod.":
        "已找到 World of Tanks %s；启动游戏时会安装 Mod。",
    "World of Tanks %s ready. Starting the game updates the mod.":
        "World of Tanks %s 已准备就绪；启动游戏时会更新 Mod。",
    "Select the World of Tanks folder": "选择 World of Tanks 游戏目录",
    "New vehicle profile": "新建车辆属性方案",
    "Profile name:": "方案名称：",
    "Delete vehicle profile?": "删除车辆属性方案？",
    "Delete profile '%s' and all of its saved vehicle edits?":
        "删除方案“%s”及其中保存的全部车辆修改？",
    "Reset all offline data?": "重置全部离线数据？",
    "Clean normal client preferences?": "清理正式客户端配置？",
    "This moves the normal World of Tanks preferences.xml aside as a backup. "
    "Graphics, window, and input settings will reset the next time the normal "
    "client starts. Offline saved data is not changed. Continue?":
        "这会把正式客户端的 preferences.xml 移到备份文件。下次启动正式客户端时，画面、窗口和按键设置会恢复默认；离线 Mod 存档不会改变。是否继续？",
    "This deletes this mod's saved address, account settings, garage fittings, "
    "post-battle results, configuration, and isolated client graphics/input "
    "preferences. Other mods and the normal World of Tanks profile are kept. "
    "Continue?": "这会删除本 Mod 保存的地址、账号设置、车库配件、战后结果、配置，以及独立的客户端画面/输入偏好。其他 Mod 和正常的 World of Tanks 配置会保留。是否继续？",
    "No launcher game session is available to report yet.":
        "还没有可汇报的启动器游戏场次。",
    "The latest game session has not produced any diagnostic logs yet. No "
    "earlier session was included.":
        "最近一局还没有产生诊断日志；不会混入更早场次的日志。",
    "The latest diagnostic session boundary is unreadable.":
        "最近一局的日志边界无法读取；不会打包旧日志。",
    "Created error report: %s": "已创建错误报告：%s",
    "Included files: %s": "已包含文件：%s",
    "Missing logs from this session: %s": "本局缺少日志：%s",
    "Not run in this session: %s": "本局未运行：%s",
    "Could not create the error report: %s": "无法创建错误报告：%s",
    "Could not select the report in Windows Explorer: %s":
        "无法在 Windows 资源管理器中选中报告：%s",
    "Could not delete the declined crash report: %s":
        "无法删除你拒绝上传的闪退报告：%s",
    "Enable crash diagnostics?": "启用闪退调试信息？",
    "To generate debugging information when the game crashes, the launcher "
    "needs to download Microsoft Sysinternals ProcDump from Microsoft's "
    "official site and accept its license terms at %s. Download and enable "
    "it?":
        "为了在游戏闪退时生成调试信息，启动器需要从微软官方网站下载 "
        "Microsoft Sysinternals ProcDump，并接受此处的微软许可条款：%s。"
        "是否下载并启用？",
    "Downloading ProcDump from Microsoft...":
        "正在从微软官方下载 ProcDump…",
    "ProcDump was downloaded and crash dumps are enabled.":
        "ProcDump 下载完成，闪退转储已启用。",
    "ProcDump could not be enabled: %s": "无法启用 ProcDump：%s",
    "Report game crash?": "是否汇报游戏闪退？",
    "The game closed unexpectedly and an error report is ready. Choose Yes "
    "to select the ZIP in Windows Explorer; choosing No deletes it.":
        "检测到游戏闪退，错误报告已经准备好。选择“是”会在 Windows 资源管理器"
        "中选中 ZIP，方便发送；选择“否”会删除它。",
}


COLLECT_CRASH_REPORTS_SETTING = "collect_crash_reports"
FULL_CRASH_DUMPS_SETTING = "full_crash_dumps"
PROCDUMP_CONSENT_SETTING = "procdump_download_consent"
PROCDUMP_PATH_ENV = "WOT_OFFLINE_PROCDUMP_PATH"
CRASH_DUMP_PATH_ENV = "WOT_OFFLINE_CRASH_DUMP_PATH"
CRASH_DUMP_MODE_ENV = "WOT_OFFLINE_CRASH_DUMP_MODE"
_LAUNCHER_LOG_LOCK = threading.Lock()


def _no_console_flags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _append_launcher_log(message, path=None, timestamp=None):
    """Persist one launcher-owned activity line for the current app run."""
    path = path or core.launcher_log_path()
    timestamp = timestamp or time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with _LAUNCHER_LOG_LOCK:
            with open(path, "a", encoding="utf-8", newline="\n") as stream:
                stream.write("[%s] %s\n" %
                             (timestamp, str(message).rstrip()))
        return True
    except (IOError, OSError):
        return False


SERVER_LOG_MAX_BYTES = 1024 * 1024
SERVER_LOG_RETAIN_BYTES = 768 * 1024


class _BoundedLogStream(object):
    """Keep the newest complete UTF-8 log lines within a fixed-size file."""

    def __init__(self, path, max_bytes=SERVER_LOG_MAX_BYTES,
                 retain_bytes=SERVER_LOG_RETAIN_BYTES):
        self._max_bytes = max(1, int(max_bytes))
        self._retain_bytes = max(
            1, min(int(retain_bytes), self._max_bytes - 1))
        # One launcher-owned server run is one diagnostic unit. Starting a
        # new run discards stale output from older builds and battles.
        self._stream = open(path, "w+b")
        self._size = 0

    @staticmethod
    def _complete_tail(payload):
        newline = payload.find(b"\n")
        if newline >= 0:
            return payload[newline + 1:]
        return b""

    def _compact(self, incoming_bytes=0):
        keep = min(
            self._retain_bytes, self._size,
            max(0, self._max_bytes - int(incoming_bytes)))
        self._stream.flush()
        self._stream.seek(max(0, self._size - keep))
        tail = self._stream.read(keep)
        if keep < self._size:
            tail = self._complete_tail(tail)
        self._stream.seek(0)
        self._stream.truncate()
        self._stream.write(tail)
        self._size = len(tail)

    def write(self, value):
        text = str(value)
        payload = text.encode("utf-8", "replace")
        if len(payload) >= self._max_bytes:
            payload = self._complete_tail(payload[-self._retain_bytes:])
        if self._size + len(payload) > self._max_bytes:
            self._compact(len(payload))
        self._stream.seek(0, os.SEEK_END)
        self._stream.write(payload)
        self._size += len(payload)
        return len(text)

    def flush(self):
        self._stream.flush()

    def close(self):
        self._stream.close()

    @property
    def closed(self):
        return self._stream.closed


class _TeeTextStream(object):
    """Mirror server output to its inherited stream and a persistent log."""

    def __init__(self, primary, log_stream, lock):
        self._primary = primary
        self._log_stream = log_stream
        self._lock = lock

    def write(self, value):
        with self._lock:
            result = None
            if self._primary is not None:
                result = self._write_primary(value)
            self._write_log(value)
            return len(value) if result is None else result

    def _write_primary(self, value):
        """Write Unicode without trusting the inherited Windows code page."""
        try:
            return self._primary.write(value)
        except UnicodeEncodeError:
            # Frozen server children inherit a byte pipe whose TextIOWrapper
            # can still advertise the active Windows ANSI code page. Bypass
            # that wrapper so the launcher parent receives valid UTF-8.
            buffer = getattr(self._primary, "buffer", None)
            if buffer is not None:
                try:
                    buffer.write(value.encode("utf-8", "replace"))
                    return len(value)
                except Exception:
                    pass
            encoding = getattr(self._primary, "encoding", None) or "ascii"
            safe_value = value.encode(
                encoding, "backslashreplace").decode(encoding, "strict")
            return self._primary.write(safe_value)

    def flush(self):
        with self._lock:
            if self._primary is not None:
                self._primary.flush()
            self._flush_log()

    def _write_log(self, value):
        try:
            if self._log_stream.closed:
                return
            self._log_stream.write(value)
        except Exception:
            self._disable_log()

    def _flush_log(self):
        try:
            if self._log_stream.closed:
                return
            self._log_stream.flush()
        except Exception:
            self._disable_log()

    def _disable_log(self):
        try:
            self._log_stream.close()
        except Exception:
            pass

    def __getattr__(self, name):
        target = self._primary or self._log_stream
        return getattr(target, name)


class LauncherWindow(object):
    def __init__(self, tk_module, ttk_module, filedialog_module):
        self._tk = tk_module
        self._ttk = ttk_module
        self._filedialog = filedialog_module
        self._server = None
        self._server_persistent = False
        self._server_context = None
        self._worker = None
        self._worker_starter_root = None
        self._worker_stop_lock = threading.Lock()
        # A LAN room owns its simulation worker independently of any visible
        # player client.  Do not reuse the per-session worker slot here: the
        # latter is deliberately torn down when its visible client exits.
        self._room_worker = None
        self._room_worker_starter_root = None
        self._room_worker_stop_lock = threading.Lock()
        self._room_vehicle_overlay_root = None
        self._game = None
        self._game_starter_root = None
        self._busy = False
        self._maintenance_busy = False
        self._report_busy = False
        self._active_report_session = None
        self._crash_capture_enabled = False
        self._full_crash_dump_enabled = False
        self._procdump_path = None
        self._procdump_download_consent = False
        self._initial_crash_prompt_pending = False
        self._worker_exited_unexpectedly = False
        self._observed_crash_roles = set()
        self._forced_stop_roles = set()
        self._stop_requested = False
        self._close_pending = False
        self._selected_client = None
        self._profile_names = []
        self._bot_lineup_profile_names = []
        self._build()

    def _build(self):
        tk = self._tk
        settings = core.load_settings()
        preference = settings.get("language", i18n.LANGUAGE_AUTO)
        if preference not in i18n.LANGUAGES:
            preference = i18n.LANGUAGE_ENGLISH
        self.language_preference = preference
        self.language = i18n.resolve_language(preference)

        self.root = tk.Tk()
        self.root.title(WINDOW_TITLE)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        frame = tk.Frame(self.root, padx=12, pady=12)
        frame.pack(fill="both", expand=True)

        header = tk.Frame(frame)
        header.grid(row=0, column=0, sticky="we", pady=(0, 8))
        tk.Label(
            header, text="World of Tanks Offline Battles",
            font=("TkDefaultFont", 11, "bold")).pack(side="left")
        language_controls = tk.Frame(header)
        language_controls.pack(side="right")
        self.language_label = tk.Label(language_controls, text="")
        self.language_label.pack(side="left", padx=(0, 6))
        self.language_choice = tk.StringVar(
            value=i18n.choice_for_language(self.language_preference))
        self.language_box = self._ttk.Combobox(
            language_controls, textvariable=self.language_choice,
            values=tuple(label for unused, label in i18n.LANGUAGE_CHOICES),
            state="readonly", width=13)
        self.language_box.pack(side="left")
        self.language_box.bind(
            "<<ComboboxSelected>>", self._language_selected)

        self.game_panel = tk.LabelFrame(frame, text="", padx=8, pady=8)
        self.game_panel.grid(row=1, column=0, sticky="we", pady=(0, 8))
        self.game_folder_label = tk.Label(self.game_panel, text="")
        self.game_folder_label.grid(row=0, column=0, sticky="w")
        self._folders = core.known_folders(settings)
        self.game_root = tk.StringVar(
            value=settings.get("game_root", "") or
            (self._folders[0] if self._folders else ""))
        self.folder_box = self._ttk.Combobox(
            self.game_panel, textvariable=self.game_root,
            values=list(self._folders),
            width=50)
        self.folder_box.grid(row=0, column=1, sticky="we", padx=(6, 6))
        self.browse_button = tk.Button(
            self.game_panel, text="", command=self._browse)
        self.browse_button.grid(row=0, column=2, sticky="e")
        self.game_root.trace_add("write", lambda *unused: self._refresh_client())

        self.client_label = tk.Label(self.game_panel, text="", anchor="w")
        self.client_label.grid(
            row=1, column=0, columnspan=3, sticky="we", pady=(4, 0))
        saved_consent = settings.get(PROCDUMP_CONSENT_SETTING)
        self._initial_crash_prompt_pending = not isinstance(
            saved_consent, bool)
        self._procdump_download_consent = saved_consent is True
        saved_crash_capture = settings.get(
            COLLECT_CRASH_REPORTS_SETTING, False)
        if not isinstance(saved_crash_capture, bool):
            saved_crash_capture = False
        if not self._procdump_download_consent:
            saved_crash_capture = False
        self.collect_crash_reports = tk.BooleanVar(
            value=saved_crash_capture)
        self.crash_report_check = tk.Checkbutton(
            self.game_panel, text="", variable=self.collect_crash_reports,
            command=self._crash_collection_toggled, anchor="w")
        self.crash_report_check.grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.report_button = tk.Button(
            self.game_panel, text="", command=self._create_error_report,
            font=("TkDefaultFont", 9, "bold"), relief="raised",
            borderwidth=2, padx=10)
        self.report_button.grid(
            row=2, column=2, sticky="e", pady=(6, 0))
        saved_full_dumps = settings.get(FULL_CRASH_DUMPS_SETTING, False)
        if not isinstance(saved_full_dumps, bool):
            saved_full_dumps = False
        self.full_crash_dumps = tk.BooleanVar(value=saved_full_dumps)
        self.full_crash_dump_check = tk.Checkbutton(
            self.game_panel, text="", variable=self.full_crash_dumps,
            command=self._full_crash_dumps_toggled, anchor="w")
        self.full_crash_dump_check.grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(2, 0))
        self.game_panel.grid_columnconfigure(1, weight=1)

        saved_mode = settings.get("mode", core.MODE_SINGLE)
        # Older launchers exposed Host and Join separately. Both now open the
        # Online tab; hosting is an explicit server action inside that tab.
        saved_mode = (core.MODE_SINGLE if saved_mode == core.MODE_SINGLE
                      else core.MODE_JOIN)
        self.mode = tk.StringVar(value=saved_mode)
        self.player_name = tk.StringVar(value=settings.get("name", ""))
        self.join_address = tk.StringVar(
            value=settings.get(
                "join_address", "%s:%d" %
                (core.LOCAL_HOST, core.DEFAULT_SERVER_PORT)))

        self.battle_tabs = self._ttk.Notebook(frame)
        self.battle_tabs.grid(row=2, column=0, sticky="we", pady=(0, 8))
        self.single_panel = tk.Frame(self.battle_tabs, padx=10, pady=10)
        self.network_panel = tk.Frame(self.battle_tabs, padx=10, pady=10)
        self.battle_tabs.add(self.single_panel, text="")
        self.battle_tabs.add(self.network_panel, text="")
        self.battle_tabs.bind("<<NotebookTabChanged>>", self._mode_tab_changed)

        self.single_player_name_label = tk.Label(self.single_panel, text="")
        self.single_player_name_label.grid(row=0, column=0, sticky="w")
        self.single_player_name_entry = tk.Entry(
            self.single_panel, textvariable=self.player_name, width=52)
        self.single_player_name_entry.grid(
            row=0, column=1, columnspan=2, sticky="we", padx=(6, 0))
        self.single_help_label = tk.Label(
            self.single_panel, text="", anchor="w", justify="left")
        self.single_help_label.grid(
            row=1, column=0, columnspan=3, sticky="we", pady=(8, 6))
        self.single_start_button = tk.Button(
            self.single_panel, text="", command=self._start_single,
            height=2, font=("TkDefaultFont", 10, "bold"))
        self.single_start_button.grid(
            row=2, column=0, columnspan=3, sticky="we")
        self.single_panel.grid_columnconfigure(1, weight=1)

        self.server_address_label = tk.Label(self.network_panel, text="")
        self.server_address_label.grid(row=0, column=0, sticky="w")
        self.join_entry = tk.Entry(
            self.network_panel, textvariable=self.join_address, width=40)
        self.join_entry.grid(row=0, column=1, sticky="we", padx=(6, 6))
        self.test_button = tk.Button(
            self.network_panel, text="", command=self._test_connection)
        self.test_button.grid(row=0, column=2, sticky="e")
        self.network_player_name_label = tk.Label(self.network_panel, text="")
        self.network_player_name_label.grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        self.network_player_name_entry = tk.Entry(
            self.network_panel, textvariable=self.player_name, width=52)
        self.network_player_name_entry.grid(
            row=1, column=1, columnspan=2, sticky="we", padx=(6, 0),
            pady=(6, 0))
        self.server_button = tk.Button(
            self.network_panel, text="", command=self._toggle_lan_server)
        self.server_button.grid(
            row=2, column=0, columnspan=3, sticky="we", pady=(8, 0))
        self.network_help_label = tk.Label(
            self.network_panel, text="", anchor="w", justify="left",
            wraplength=620)
        self.network_help_label.grid(
            row=3, column=0, columnspan=3, sticky="we", pady=(8, 6))
        self.network_start_button = tk.Button(
            self.network_panel, text="", command=self._start_network,
            height=2, font=("TkDefaultFont", 10, "bold"))
        self.network_start_button.grid(
            row=4, column=0, columnspan=3, sticky="we")
        self.network_panel.grid_columnconfigure(1, weight=1)

        self.tools_tabs = self._ttk.Notebook(frame)
        self.tools_tabs.grid(row=3, column=0, sticky="we", pady=(0, 8))
        self.vehicle_panel = tk.Frame(self.tools_tabs, padx=10, pady=10)
        self.bot_lineup_panel = tk.Frame(self.tools_tabs, padx=10, pady=10)
        self.repair_panel = tk.Frame(self.tools_tabs, padx=10, pady=10)
        self.tools_tabs.add(self.vehicle_panel, text="")
        self.tools_tabs.add(self.bot_lineup_panel, text="")
        self.tools_tabs.add(self.repair_panel, text="")

        self._bot_lineup_store = bot_lineup_profiles.normalize_store(
            settings.get("bot_lineup_profiles"))
        self.bot_lineup_profile_label = tk.Label(
            self.bot_lineup_panel, text="")
        self.bot_lineup_profile_label.grid(row=0, column=0, sticky="w")
        self.bot_lineup_profile = tk.StringVar(value=settings.get(
            "bot_lineup_profile",
            bot_lineup_profiles.AUTOMATIC_PROFILE_LABEL))
        self.bot_lineup_profile_box = self._ttk.Combobox(
            self.bot_lineup_panel, textvariable=self.bot_lineup_profile,
            values=(bot_lineup_profiles.AUTOMATIC_PROFILE_LABEL,),
            state="disabled", width=40)
        self.bot_lineup_profile_box.grid(
            row=0, column=1, sticky="we", padx=(6, 0))
        self.bot_lineup_profile_box.bind(
            "<<ComboboxSelected>>", self._bot_lineup_profile_selected)
        bot_lineup_actions = tk.Frame(self.bot_lineup_panel)
        bot_lineup_actions.grid(
            row=1, column=0, columnspan=2, sticky="we", pady=(6, 0))
        self.new_bot_lineup_profile_button = tk.Button(
            bot_lineup_actions, text="", command=self._new_bot_lineup_profile)
        self.new_bot_lineup_profile_button.pack(
            side="left", fill="x", expand=True)
        self.bot_lineup_editor_button = tk.Button(
            bot_lineup_actions, text="", command=self._open_bot_lineup_editor)
        self.bot_lineup_editor_button.pack(
            side="left", fill="x", expand=True, padx=(6, 0))
        self.delete_bot_lineup_profile_button = tk.Button(
            bot_lineup_actions, text="",
            command=self._delete_bot_lineup_profile)
        self.delete_bot_lineup_profile_button.pack(
            side="left", fill="x", expand=True, padx=(6, 0))
        self.bot_lineup_panel.grid_columnconfigure(1, weight=1)

        self.vehicle_profile_label = tk.Label(self.vehicle_panel, text="")
        self.vehicle_profile_label.grid(row=0, column=0, sticky="w")
        self.vehicle_profile = tk.StringVar(
            value=settings.get(
                "vehicle_profile", vehicle_overlays.ORIGINAL_PROFILE_LABEL))
        self.vehicle_profile_box = self._ttk.Combobox(
            self.vehicle_panel, textvariable=self.vehicle_profile,
            values=(vehicle_overlays.ORIGINAL_PROFILE_LABEL,),
            state="disabled", width=40)
        self.vehicle_profile_box.grid(
            row=0, column=1, sticky="we", padx=(6, 0))
        self.vehicle_profile_box.bind(
            "<<ComboboxSelected>>", self._profile_selected)

        profile_actions = tk.Frame(self.vehicle_panel)
        profile_actions.grid(
            row=1, column=0, columnspan=2, sticky="we", pady=(6, 0))
        self.new_profile_button = tk.Button(
            profile_actions, text="",
            command=self._new_vehicle_profile)
        self.new_profile_button.pack(side="left", fill="x", expand=True)
        self.vehicle_editor_button = tk.Button(
            profile_actions, text="",
            command=self._open_vehicle_editor)
        self.vehicle_editor_button.pack(
            side="left", fill="x", expand=True, padx=(6, 0))
        self.delete_profile_button = tk.Button(
            profile_actions, text="",
            command=self._delete_vehicle_profile)
        self.delete_profile_button.pack(
            side="left", fill="x", expand=True, padx=(6, 0))
        self.vehicle_panel.grid_columnconfigure(1, weight=1)

        self.repair_button = tk.Button(
            self.repair_panel, text="",
            command=self._repair_startup)
        self.repair_button.grid(row=0, column=0, sticky="we")
        self.reset_button = tk.Button(
            self.repair_panel, text="",
            command=self._reset_all_state)
        self.reset_button.grid(row=0, column=1, sticky="we", padx=(6, 0))
        self.normal_preferences_button = tk.Button(
            self.repair_panel, text="",
            command=self._clean_normal_client_preferences)
        self.normal_preferences_button.grid(
            row=1, column=0, columnspan=2, sticky="we", pady=(6, 0))
        self.repair_panel.grid_columnconfigure(0, weight=1)
        self.repair_panel.grid_columnconfigure(1, weight=1)

        self.log_panel = tk.LabelFrame(frame, text="", padx=6, pady=6)
        self.log_panel.grid(row=4, column=0, sticky="nsew")
        self.log_view = tk.Text(
            self.log_panel, height=10, width=72, state="disabled",
                                wrap="none")
        self.log_view.pack(fill="both", expand=True)
        self.author_text = tk.StringVar(value=(
            "作者：伪红学家  Bilibili：@tiancaihb  GitHub: "
            "https://github.com/pengw0048/wot-offline-battles"))
        self.author_entry = tk.Entry(
            frame, textvariable=self.author_text, state="readonly",
            relief="flat", borderwidth=0, highlightthickness=0)
        self.author_entry.grid(row=5, column=0, sticky="we", pady=(8, 0))
        self.qq_group_text = tk.StringVar(value=(
            "坦克世界QQ群：1108778562、302519768"))
        self.qq_group_entry = tk.Entry(
            frame, textvariable=self.qq_group_text, state="readonly",
            relief="flat", borderwidth=0, highlightthickness=0)
        self.qq_group_entry.grid(
            row=6, column=0, sticky="we", pady=(2, 0))
        self.distribution_notice_text = tk.StringVar(value=(
            "本mod免费传播、开源、欢迎二创，使用无需付费，售卖与本人无关，"
            "仅供个人学习交流"))
        self.distribution_notice_entry = tk.Entry(
            frame, textvariable=self.distribution_notice_text,
            state="readonly", relief="flat", borderwidth=0,
            highlightthickness=0)
        self.distribution_notice_entry.grid(
            row=7, column=0, sticky="we", pady=(2, 0))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(4, weight=1)

        self.start_button = (self.single_start_button
                             if self.mode.get() == core.MODE_SINGLE
                             else self.network_start_button)
        self._sync_mode_tab()
        self._apply_language(refresh=False)

        self._refresh_client()
        self._refresh_mode()
        self._recover_stale_vehicle_profile()
        identity = core.bundled_payload_identity(core.PORT_0_9_22)
        if identity is None:
            identity = {
                "semanticVersion": LAUNCHER_VERSION,
                "buildIdentity": "unknown",
            }
        self._log("Launcher session: %s role=launcher." %
                  core.payload_identity_text(identity))

    def _t(self, text):
        if self.language == i18n.LANGUAGE_CHINESE:
            return _CHINESE.get(text, text)
        return text

    def _apply_language(self, refresh=True):
        self.language_label.config(text=self._t("Language"))
        self.game_panel.config(text=self._t("Game client"))
        self.game_folder_label.config(text=self._t("Game folder"))
        self.browse_button.config(text=self._t("Browse..."))
        self.crash_report_check.config(
            text=self._t("Collect a report if the game crashes"))
        self.full_crash_dump_check.config(text=self._t(
            "Collect full-memory crash dumps (very large files)"))
        self.battle_tabs.tab(
            self.single_panel, text=self._t("Single player"))
        self.battle_tabs.tab(self.network_panel, text=self._t("Online"))
        self.single_player_name_label.config(text=self._t("Player name"))
        self.network_player_name_label.config(text=self._t("Player name"))
        self.single_help_label.config(text=self._t(
            "The launcher starts the private server and hidden simulation "
            "client automatically."))
        self.server_address_label.config(text=self._t("Server address"))
        self.test_button.config(text=self._t("Test connection"))
        self.network_help_label.config(text=self._t(
            "To host: start the server, then join the game. Other players use "
            "a LAN address shown in the log."))
        self.tools_tabs.tab(
            self.vehicle_panel, text=self._t("Vehicle modifier"))
        self.tools_tabs.tab(
            self.bot_lineup_panel, text=self._t("Exact lineup"))
        self.tools_tabs.tab(self.repair_panel, text=self._t("Repair"))
        self.vehicle_profile_label.config(text=self._t("Vehicle data profile"))
        self.new_profile_button.config(text=self._t("New profile..."))
        self.vehicle_editor_button.config(
            text=self._t("Edit selected profile..."))
        self.delete_profile_button.config(
            text=self._t("Delete selected profile..."))
        self.bot_lineup_profile_label.config(text=self._t("Bot lineup profile"))
        self.new_bot_lineup_profile_button.config(
            text=self._t("New Bot lineup profile..."))
        self.bot_lineup_editor_button.config(text=self._t("Edit Bot lineup..."))
        self.delete_bot_lineup_profile_button.config(
            text=self._t("Delete Bot lineup profile..."))
        self.repair_button.config(
            text=self._t("Repair startup (keep saved data)"))
        self.normal_preferences_button.config(text=self._t(
            "Normal client stuck loading? Clean preferences..."))
        self.reset_button.config(text=self._t("Reset all offline data..."))
        self.report_button.config(text=self._t("Create error report..."))
        self.log_panel.config(text=self._t("Activity log"))
        self._update_action_controls()
        if refresh:
            self._refresh_client()

    def _language_selected(self, unused_event=None):
        self.language_preference = i18n.language_for_choice(
            self.language_choice.get())
        self.language = i18n.resolve_language(self.language_preference)
        self._apply_language()
        self._save_settings()

    def _sync_mode_tab(self):
        panel = (self.single_panel if self.mode.get() == core.MODE_SINGLE
                 else self.network_panel)
        self.battle_tabs.select(panel)
        self.start_button = (self.single_start_button
                             if self.mode.get() == core.MODE_SINGLE
                             else self.network_start_button)

    def _mode_tab_changed(self, unused_event=None):
        try:
            index = self.battle_tabs.index("current")
        except Exception:
            return
        self.mode.set(core.MODE_SINGLE if index == 0 else core.MODE_JOIN)
        self._refresh_mode(sync_tab=False)

    def _start_single(self):
        self.mode.set(core.MODE_SINGLE)
        self._refresh_mode()
        return self._start()

    def _start_network(self):
        self.mode.set(core.MODE_JOIN)
        self._refresh_mode()
        return self._start()

    def _browse(self):
        selected = self._filedialog.askdirectory(
            title=self._t("Select the World of Tanks folder"),
            initialdir=self.game_root.get() or None)
        if selected:
            self.game_root.set(os.path.normpath(selected))
            self._remember_folder()

    def _remember_folder(self):
        """Keep this folder at the top of the list for the next launch."""
        folder = self.game_root.get().strip()
        if not folder or not os.path.isfile(core.game_executable(folder)):
            return False
        self._folders = core.remember_folder(self._folders, folder)
        self.folder_box.config(values=list(self._folders))
        self._save_settings()
        return True

    def _refresh_client(self):
        status = core.inspect_game_root(self.game_root.get())
        if not self.game_root.get().strip():
            text = self._t("Select the folder that contains %s.") % (
                core.GAME_EXECUTABLE,)
        elif not status["has_executable"]:
            text = self._t("%s was not found in this folder.") % (
                core.GAME_EXECUTABLE,)
        elif status["client"] is None:
            text = self._t("This client version is not supported.")
        elif not status["mod_installed"]:
            text = self._t(
                "World of Tanks %s found. Starting the game installs the mod."
            ) % (status["version"] or status["client"])
        else:
            text = self._t(
                "World of Tanks %s ready. Starting the game updates the mod."
            ) % (status["version"] or status["client"])
        self.client_label.config(text=text)
        self._selected_client = status["client"]
        self._refresh_profiles(status)
        self._refresh_bot_lineup_profiles()
        self._update_action_controls()
        self._refresh_mode()
        return status

    def _server_is_running(self):
        return self._server is not None and self._server.poll() is None

    def _room_worker_is_running(self):
        return (self._room_worker is not None and
                self._room_worker.poll() is None)

    def _update_action_controls(self):
        server_running = self._server_is_running()
        self.start_button = (self.single_start_button
                             if self.mode.get() == core.MODE_SINGLE
                             else self.network_start_button)
        self.single_start_button.config(
            text=self._t("Start single-player battle"))
        self.network_start_button.config(text=self._t("Join network battle"))
        if self._busy:
            inactive = (self.network_start_button
                        if self.start_button is self.single_start_button
                        else self.single_start_button)
            inactive.config(state="disabled")
            self.start_button.config(
                state="normal", text=self._t("Close game"))
        elif self._maintenance_busy:
            self.single_start_button.config(state="disabled")
            self.network_start_button.config(state="disabled")
        else:
            self.single_start_button.config(state="normal")
            self.network_start_button.config(state="normal")
        if server_running:
            server_state = (
                "normal" if not self._busy and not self._maintenance_busy
                else "disabled")
            self.server_button.config(
                state=server_state, text=self._t("Stop LAN room"))
        else:
            server_state = (
                "normal" if self._selected_client in core.SUPPORTED_PORTS and
                self.mode.get() == core.MODE_JOIN and not self._busy and
                not self._maintenance_busy else "disabled")
            self.server_button.config(
                state=server_state, text=self._t("Start LAN room"))
        maintenance_state = (
            "normal" if self._selected_client == core.PORT_0_9_22 and
            not self._busy and not self._maintenance_busy and
            not server_running else "disabled")
        self.repair_button.config(state=maintenance_state)
        self.normal_preferences_button.config(state=maintenance_state)
        self.reset_button.config(state=maintenance_state)
        self.report_button.config(
            state="disabled" if self._report_busy else "normal")
        self.crash_report_check.config(
            state=("normal" if
                   self._selected_client == core.PORT_0_9_22 and
                   not self._busy and not self._maintenance_busy
                   else "disabled"))
        self.full_crash_dump_check.config(
            state=("normal" if
                   self._selected_client == core.PORT_0_9_22 and
                   not self._busy and not self._maintenance_busy
                   else "disabled"))
        profile_state = (
            "readonly" if maintenance_state == "normal" and
            self.mode.get() in (core.MODE_SINGLE, core.MODE_JOIN)
            else "disabled")
        self.vehicle_profile_box.config(state=profile_state)
        create_state = (
            "normal" if profile_state == "readonly" else "disabled")
        self.new_profile_button.config(state=create_state)
        selected_profile = self.vehicle_profile.get().strip()
        selected_custom = (
            profile_state == "readonly" and selected_profile in
            self._profile_names)
        edit_state = "normal" if selected_custom else "disabled"
        self.vehicle_editor_button.config(state=edit_state)
        self.delete_profile_button.config(state=edit_state)
        lineup_state = (
            "readonly" if maintenance_state == "normal" else "disabled")
        self.bot_lineup_profile_box.config(state=lineup_state)
        lineup_create_state = (
            "normal" if lineup_state == "readonly" else "disabled")
        self.new_bot_lineup_profile_button.config(state=lineup_create_state)
        selected_lineup = self.bot_lineup_profile.get().strip()
        selected_custom_lineup = (
            lineup_state == "readonly" and
            selected_lineup in self._bot_lineup_profile_names)
        lineup_edit_state = (
            "normal" if selected_custom_lineup else "disabled")
        self.bot_lineup_editor_button.config(state=lineup_edit_state)
        self.delete_bot_lineup_profile_button.config(state=lineup_edit_state)

    def _refresh_profiles(self, status=None):
        status = status or core.inspect_game_root(self.game_root.get())
        names = []
        if status.get("client") == core.PORT_0_9_22:
            try:
                names = vehicle_overlays.list_vehicle_profiles(status["path"])
            except vehicle_overlays.VehicleOverlayError as error:
                if hasattr(self, "log_view"):
                    self._log("Vehicle profiles could not be loaded: %s" % error)
        self._profile_names = list(names)
        values = tuple(
            [vehicle_overlays.ORIGINAL_PROFILE_LABEL] + self._profile_names)
        self.vehicle_profile_box.config(values=values)
        if self.vehicle_profile.get().strip() not in values:
            self.vehicle_profile.set(vehicle_overlays.ORIGINAL_PROFILE_LABEL)
        return values

    def _profile_selected(self, unused_event=None):
        self._update_action_controls()
        self._save_settings()

    def _refresh_bot_lineup_profiles(self):
        self._bot_lineup_store = bot_lineup_profiles.normalize_store(
            self._bot_lineup_store)
        self._bot_lineup_profile_names = bot_lineup_profiles.names(
            self._bot_lineup_store)
        values = tuple(
            [bot_lineup_profiles.AUTOMATIC_PROFILE_LABEL] +
            self._bot_lineup_profile_names)
        self.bot_lineup_profile_box.config(values=values)
        if self.bot_lineup_profile.get().strip() not in values:
            self.bot_lineup_profile.set(
                bot_lineup_profiles.AUTOMATIC_PROFILE_LABEL)
        return values

    def _bot_lineup_profile_selected(self, unused_event=None):
        self._update_action_controls()
        self._save_settings()

    def _recover_stale_vehicle_profile(self):
        game_root = self.game_root.get().strip()
        if self._selected_client != core.PORT_0_9_22:
            return 0
        try:
            manifest_exists = os.path.lexists(
                vehicle_overlays.manifest_path(game_root))
            recovery_exists = vehicle_overlays.has_pending_vehicle_recovery(
                game_root)
        except vehicle_overlays.VehicleOverlayError as error:
            self._log("Vehicle profile recovery could not be checked: %s" % error)
            return 0
        if not manifest_exists and not recovery_exists:
            return 0
        if core.game_is_running():
            self._log(
                "A vehicle profile is active while World of Tanks is running; "
                "close the game and reopen this launcher before any other "
                "launch so it can be cleaned safely.")
            return 0
        try:
            recovered = vehicle_overlays.recover_vehicle_profile_transactions(
                game_root)
            imported = vehicle_overlays.preserve_legacy_vehicle_overlay(
                game_root)
            removed = vehicle_overlays.restore_vehicle_defaults(game_root)
        except vehicle_overlays.VehicleOverlayError as error:
            self._log(
                "A stale vehicle profile could not be cleaned: %s" % error)
            return 0
        if recovered or imported:
            self._refresh_profiles(core.inspect_game_root(game_root))
        if imported:
            self._log(
                "Preserved the previous vehicle edits as profile '%s'." %
                imported)
        if recovered:
            self._log(
                "Recovered an interrupted vehicle profile update.")
        if removed:
            self._log(
                "Cleaned a stale temporary vehicle profile from the previous "
                "launcher session.")
        return removed + recovered

    def _refresh_mode(self, sync_tab=True):
        if self.mode.get() not in (core.MODE_SINGLE, core.MODE_JOIN):
            self.mode.set(core.MODE_JOIN)
        if sync_tab:
            self._sync_mode_tab()
        network = self.mode.get() == core.MODE_JOIN
        network_state = (
            "normal" if network and not self._busy and
            not self._maintenance_busy else "disabled")
        self.join_entry.config(state=network_state)
        self.test_button.config(state=network_state)
        self._update_action_controls()

    def _test_connection(self):
        mode = core.MODE_JOIN
        client = self._refresh_client().get("client")
        if client not in core.SUPPORTED_PORTS:
            self._log("Select a supported game folder before testing.")
            return False
        try:
            host, port = core.endpoint_for_mode(mode, self.join_address.get())
        except core.LauncherError as error:
            self._log(str(error))
            return False
        self.test_button.config(state="disabled")
        self._log("Testing %s:%d..." % (host, port))

        def probe():
            try:
                status = core.listener_status(client, host, port)
                self._log(core.listener_report(mode, host, port, status))
            finally:
                self.root.after(
                    0, lambda: self.test_button.config(state="normal"))

        thread = threading.Thread(target=probe)
        thread.daemon = True
        thread.start()
        return True

    def _log(self, message):
        _append_launcher_log(message)

        def append():
            self.log_view.config(state="normal")
            self.log_view.insert("end", message.rstrip() + "\n")
            self.log_view.see("end")
            self.log_view.config(state="disabled")

        self.root.after(0, append)

    def _create_error_report(self):
        if self._report_busy:
            return False
        self._report_busy = True
        self._update_action_controls()

        def run():
            try:
                result = error_reports.create_report()
            except core.LauncherError as error:
                self._log(self._t(str(error)))
            except Exception as error:
                self._log(self._t(
                    "Could not create the error report: %s") % error)
            else:
                self._log(self._t("Created error report: %s") %
                          result["path"])
                self._log(self._t("Included files: %s") %
                          ", ".join(result["included"]))
                if result["missing"]:
                    self._log(self._t(
                        "Missing logs from this session: %s") %
                        ", ".join(result["missing"]))
                if result["notRun"]:
                    self._log(self._t("Not run in this session: %s") %
                              ", ".join(result["notRun"]))
                try:
                    error_reports.select_in_explorer(result["path"])
                except core.LauncherError as error:
                    self._log(self._t(
                        "Could not select the report in Windows Explorer: "
                        "%s") % error)
            finally:
                self._report_busy = False
                self.root.after(0, self._update_action_controls)

        thread = threading.Thread(target=run)
        thread.daemon = True
        thread.start()
        return True

    def _confirm_enable_crash_capture(self):
        from tkinter import messagebox

        return messagebox.askyesno(
            self._t("Enable crash diagnostics?"),
            self._t(
                "To generate debugging information when the game crashes, "
                "the launcher needs to download Microsoft Sysinternals "
                "ProcDump from Microsoft's official site and accept its "
                "license terms at %s. Download and enable it?") %
            core.PROCDUMP_LICENSE_URL,
            icon="warning")

    def _finish_procdump_download(self, path=None, error=None):
        self._set_maintenance_busy(False)
        if error is not None:
            self.collect_crash_reports.set(False)
            self._log(self._t("ProcDump could not be enabled: %s") % error)
            self._save_settings()
            return False
        self.collect_crash_reports.set(True)
        self._log(self._t(
            "ProcDump was downloaded and crash dumps are enabled."))
        self._save_settings()
        return bool(path)

    def _begin_procdump_download(self):
        path = core.procdump_executable()
        if core.procdump_is_installed(path):
            self.collect_crash_reports.set(True)
            self._save_settings()
            return True
        self.collect_crash_reports.set(False)
        self._save_settings()
        self._log(self._t("Downloading ProcDump from Microsoft..."))
        self._set_maintenance_busy(True)

        def run():
            try:
                installed = core.download_procdump(path)
            except core.LauncherError as error:
                self.root.after(
                    0, lambda message=str(error):
                    self._finish_procdump_download(error=message))
            except Exception as error:
                self.root.after(
                    0, lambda message=str(error):
                    self._finish_procdump_download(error=message))
            else:
                self.root.after(
                    0, lambda installed_path=installed:
                    self._finish_procdump_download(path=installed_path))

        thread = threading.Thread(target=run)
        thread.daemon = True
        thread.start()
        return True

    def _request_crash_collection(self):
        if not self._procdump_download_consent:
            if not self._confirm_enable_crash_capture():
                self.collect_crash_reports.set(False)
                self._save_settings()
                return False
            self._procdump_download_consent = True
        return self._begin_procdump_download()

    def _crash_collection_toggled(self):
        if not bool(self.collect_crash_reports.get()):
            self._save_settings()
            return False
        self.collect_crash_reports.set(False)
        return self._request_crash_collection()

    def _full_crash_dumps_toggled(self):
        self._save_settings()
        return bool(self.full_crash_dumps.get())

    def _prompt_initial_crash_collection(self):
        if not self._initial_crash_prompt_pending:
            return False
        self._initial_crash_prompt_pending = False
        return self._request_crash_collection()

    def _disable_crash_collection(self):
        self.collect_crash_reports.set(False)
        self._save_settings()

    def _enable_crash_capture(self, report_session, requested,
                              full_memory=False):
        self._crash_capture_enabled = False
        self._full_crash_dump_enabled = False
        self._procdump_path = None
        if (not requested or report_session is None or
                not self._procdump_download_consent):
            return False
        procdump_path = core.procdump_executable()
        if not core.procdump_is_installed(procdump_path):
            try:
                core.download_procdump(procdump_path)
            except core.LauncherError as error:
                self._log(self._t("ProcDump could not be enabled: %s") % error)
                self.root.after(0, self._disable_crash_collection)
                return False
        self._crash_capture_enabled = True
        self._full_crash_dump_enabled = bool(full_memory)
        self._procdump_path = procdump_path
        return True

    def _crash_capture_environment(self, environment, role):
        environment = dict(environment)
        if (not self._crash_capture_enabled or
                self._active_report_session is None):
            return environment
        try:
            dump_path = error_reports.session_dump_path(
                self._active_report_session, role)
        except core.LauncherError as error:
            self._log("Crash report collection could not start: %s" % error)
            return environment
        environment[PROCDUMP_PATH_ENV] = self._procdump_path
        environment[CRASH_DUMP_PATH_ENV] = dump_path
        environment[CRASH_DUMP_MODE_ENV] = (
            "full" if self._full_crash_dump_enabled else "mini")
        return environment

    def _confirm_crash_report(self):
        from tkinter import messagebox

        return messagebox.askyesno(
            self._t("Report game crash?"),
            self._t(
                "The game closed unexpectedly and an error report is ready. "
                "Choose Yes to select the ZIP in Windows Explorer; choosing "
                "No deletes it."),
            icon="warning")

    def _offer_crash_report(self, report_path):
        if not self._confirm_crash_report():
            try:
                error_reports.delete_report(report_path)
            except core.LauncherError as error:
                self._log(self._t(
                    "Could not delete the declined crash report: %s") %
                          error)
            return False
        try:
            error_reports.select_in_explorer(report_path)
        except core.LauncherError as error:
            self._log(self._t(
                "Could not select the report in Windows Explorer: %s") %
                error)
            return False
        return True

    def _create_automatic_crash_report(self):
        try:
            result = error_reports.create_report()
        except core.LauncherError as error:
            self._log(self._t(str(error)))
            return None
        except Exception as error:
            self._log(self._t(
                "Could not create the error report: %s") % error)
            return None
        self._log(self._t("Created error report: %s") % result["path"])
        self._log(self._t("Included files: %s") %
                  ", ".join(result["included"]))
        return result["path"]

    def _observe_process_exit(self, process, role):
        """Remember a nonzero role exit before any intentional stop."""
        if process is None:
            return None
        try:
            exit_code = process.poll()
        except Exception:
            return None
        return self._remember_process_exit(exit_code, role)

    def _remember_process_exit(self, exit_code, role):
        if exit_code is None:
            return exit_code
        try:
            dump_evidence = error_reports.minidump_evidence(
                self._active_report_session, role)
        except Exception:
            dump_evidence = error_reports.MINIDUMP_EVIDENCE_UNKNOWN
        if dump_evidence == error_reports.MINIDUMP_EVIDENCE_EXCEPTION:
            self._observed_crash_roles.add(role)
            return exit_code
        if role in self._forced_stop_roles or exit_code == 0:
            return exit_code
        try:
            if role == error_reports.ROLE_VISIBLE_CLIENT:
                clean_exit = error_reports.visible_client_exited_cleanly(
                    self._active_report_session)
            else:
                clean_exit = error_reports.client_exited_cleanly(
                    self._active_report_session, role)
        except Exception:
            clean_exit = False
        if not clean_exit:
            self._observed_crash_roles.add(role)
        return exit_code

    def _set_busy(self, busy):
        self._busy = busy
        self.root.after(0, self._update_action_controls)

    def _set_maintenance_busy(self, busy):
        self._maintenance_busy = busy
        self.root.after(0, self._update_action_controls)

    def _kill_game(self, stop_persistent_server=False):
        """Close a game that did not exit on its own."""
        game = self._game
        worker = self._worker
        game_exit_code = self._observe_process_exit(
            game, error_reports.ROLE_VISIBLE_CLIENT)
        self._observe_process_exit(
            worker, error_reports.ROLE_HIDDEN_WORKER)
        self._stop_requested = True
        self._log("Closing every %s process..." % core.GAME_EXECUTABLE)
        starter_root = self._game_starter_root
        force_cleanup = starter_root is None
        if game is not None and game_exit_code is None:
            stopped = (starter_root is not None and
                       self._request_starter_stop(game, starter_root))
            if not stopped:
                game_exit_code = self._observe_process_exit(
                    game, error_reports.ROLE_VISIBLE_CLIENT)
                if game_exit_code is None:
                    force_cleanup = True
                    self._forced_stop_roles.add(
                        error_reports.ROLE_VISIBLE_CLIENT)
                    try:
                        game.kill()
                    except Exception as error:
                        self._log(
                            "Could not close the started process: %s" % error)
        self._stop_worker()
        if force_cleanup:
            core.kill_game()
        if stop_persistent_server:
            self._stop_lan_room()
        else:
            self._stop_server()
        return True

    def _request_starter_stop(self, process, game_root):
        """Ask one 0.9.22 starter to finish its clients and dump monitors."""
        if process is None or process.poll() is not None:
            return True
        try:
            result = subprocess.run(
                core.starter_stop_command(game_root, process.pid),
                cwd=game_root, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=core.STARTER_CONTROL_TIMEOUT_SECONDS_0922,
                creationflags=_no_console_flags())
        except (AttributeError, OSError, subprocess.TimeoutExpired,
                core.LauncherError) as error:
            self._log("Could not stop the 0.9.22 starter cleanly: %s" % error)
            return False
        if result.returncode != 0:
            self._log(
                "The 0.9.22 starter stop helper returned exit code %s." %
                result.returncode)
            return False
        return True

    def _save_settings(self):
        core.save_settings({
            "game_root": self.game_root.get().strip(),
            "folders": list(self._folders),
            "mode": (core.MODE_SINGLE
                     if self.mode.get() == core.MODE_SINGLE
                     else core.MODE_JOIN),
            "join_address": self.join_address.get().strip(),
            "name": self.player_name.get().strip(),
            "vehicle_profile": self.vehicle_profile.get().strip(),
            "bot_lineup_profile": self.bot_lineup_profile.get().strip(),
            "bot_lineup_profiles": self._bot_lineup_store,
            "language": self.language_preference,
            COLLECT_CRASH_REPORTS_SETTING:
                bool(self.collect_crash_reports.get()),
            FULL_CRASH_DUMPS_SETTING:
                bool(self.full_crash_dumps.get()),
            PROCDUMP_CONSENT_SETTING:
                bool(self._procdump_download_consent),
        })

    def _start_maintenance(self, action):
        if self._busy or self._maintenance_busy:
            self._log("Wait for the current launcher operation to finish.")
            return False
        status = self._refresh_client()
        if status.get("client") != core.PORT_0_9_22:
            self._log("Select the supported 0.9.22 game folder first.")
            return False
        self._remember_folder()
        self._set_maintenance_busy(True)

        def run():
            try:
                for message in action(status["path"]):
                    self._log(message)
            except core.LauncherError as error:
                self._log(str(error))
            except Exception as error:
                self._log("Launcher maintenance failed: %s" % error)
            finally:
                self._set_maintenance_busy(False)
                self.root.after(0, self._refresh_client)

        thread = threading.Thread(target=run)
        thread.daemon = True
        thread.start()
        return True

    def _repair_startup(self):
        return self._start_maintenance(core.repair_0_9_22_startup)

    def _confirm_normal_preferences_cleanup(self):
        from tkinter import messagebox

        return messagebox.askyesno(
            self._t("Clean normal client preferences?"),
            self._t(
                "This moves the normal World of Tanks preferences.xml aside "
                "as a backup. Graphics, window, and input settings will reset "
                "the next time the normal client starts. Offline saved data "
                "is not changed. Continue?"),
            icon="warning")

    def _clean_normal_client_preferences(self):
        if self._busy or self._maintenance_busy:
            self._log("Wait for the current launcher operation to finish.")
            return False
        if self._refresh_client().get("client") != core.PORT_0_9_22:
            self._log("Select the supported 0.9.22 game folder first.")
            return False
        if core.game_is_running():
            self._log(
                "Close World of Tanks before cleaning normal client "
                "preferences.")
            return False
        if not self._confirm_normal_preferences_cleanup():
            self._log("Normal client preferences cleanup was cancelled.")
            return False
        return self._start_maintenance(core.backup_normal_client_preferences)

    def _ask_profile_name(self):
        from tkinter import simpledialog

        return simpledialog.askstring(
            self._t("New vehicle profile"),
            self._t("Profile name:"))

    def _ask_bot_lineup_profile_name(self):
        from tkinter import simpledialog

        return simpledialog.askstring(
            self._t("New Bot lineup profile"), self._t("Profile name:"))

    def _confirm_delete_bot_lineup_profile(self, profile_name):
        from tkinter import messagebox

        return messagebox.askyesno(
            self._t("Delete Bot lineup profile?"),
            self._t("Delete Bot lineup profile '%s'?") % profile_name,
            icon="warning")

    def _save_bot_lineup_store(self, store):
        self._bot_lineup_store = bot_lineup_profiles.normalize_store(store)
        self._refresh_bot_lineup_profiles()
        self._save_settings()

    def _new_bot_lineup_profile(self):
        if self._busy or self._maintenance_busy:
            self._log("Wait for the current launcher operation to finish.")
            return False
        status = self._refresh_client()
        if status.get("client") != core.PORT_0_9_22:
            self._log("Select the supported 0.9.22 game folder first.")
            return False
        raw_name = self._ask_bot_lineup_profile_name()
        if raw_name is None:
            return False
        try:
            self._bot_lineup_store, profile_name = bot_lineup_profiles.create(
                self._bot_lineup_store, raw_name)
        except bot_lineup_profiles.BotLineupProfileError as error:
            self._log(
                "Could not create the Bot lineup profile: %s" % error)
            return False
        self._refresh_bot_lineup_profiles()
        self.bot_lineup_profile.set(profile_name)
        self._save_settings()
        return self._open_bot_lineup_editor()

    def _open_bot_lineup_editor(self):
        if self._busy or self._maintenance_busy:
            self._log("Wait for the current launcher operation to finish.")
            return False
        status = self._refresh_client()
        profile_name = self.bot_lineup_profile.get().strip()
        if (status.get("client") != core.PORT_0_9_22 or
                profile_name not in self._bot_lineup_profile_names):
            self._log("Create or select a Bot lineup profile before editing.")
            return False
        self._remember_folder()
        bot_lineup_ui.open_bot_lineup_editor(
            self.root, status["path"], profile_name,
            self._bot_lineup_store, self._save_bot_lineup_store,
            log=self._log)
        return True

    def _delete_bot_lineup_profile(self):
        if self._busy or self._maintenance_busy:
            self._log("Wait for the current launcher operation to finish.")
            return False
        profile_name = self.bot_lineup_profile.get().strip()
        if profile_name not in self._bot_lineup_profile_names:
            self._log("Select a saved Bot lineup profile before deleting it.")
            return False
        if not self._confirm_delete_bot_lineup_profile(profile_name):
            return False
        try:
            self._bot_lineup_store = bot_lineup_profiles.delete(
                self._bot_lineup_store, profile_name)
        except bot_lineup_profiles.BotLineupProfileError as error:
            self._log(
                "Could not delete the Bot lineup profile: %s" % error)
            return False
        self.bot_lineup_profile.set(
            bot_lineup_profiles.AUTOMATIC_PROFILE_LABEL)
        self._refresh_bot_lineup_profiles()
        self._save_settings()
        return True

    def _confirm_delete_profile(self, profile_name):
        from tkinter import messagebox

        return messagebox.askyesno(
            self._t("Delete vehicle profile?"),
            self._t(
                "Delete profile '%s' and all of its saved vehicle edits?"
            ) % profile_name,
            icon="warning")

    def _new_vehicle_profile(self):
        if self._busy or self._maintenance_busy:
            self._log("Wait for the current launcher operation to finish.")
            return False
        status = self._refresh_client()
        if status.get("client") != core.PORT_0_9_22:
            self._log(
                "Vehicle profiles are available for the 0.9.22 client.")
            return False
        raw_name = self._ask_profile_name()
        if raw_name is None:
            return False
        try:
            profile_name = vehicle_overlays.create_vehicle_profile(
                status["path"], raw_name)
        except vehicle_overlays.VehicleOverlayError as error:
            self._log("Could not create the vehicle profile: %s" % error)
            return False
        self._refresh_profiles(status)
        self.vehicle_profile.set(profile_name)
        self._update_action_controls()
        self._save_settings()
        self._log("Created vehicle profile '%s'." % profile_name)
        return self._open_vehicle_editor()

    def _open_vehicle_editor(self):
        if self._busy or self._maintenance_busy:
            self._log("Wait for the current launcher operation to finish.")
            return False
        status = self._refresh_client()
        if status.get("client") != core.PORT_0_9_22:
            self._log("Select the supported 0.9.22 game folder first.")
            return False
        profile_name = self.vehicle_profile.get().strip()
        if profile_name not in self._profile_names:
            self._log("Create or select a vehicle profile before editing.")
            return False
        self._remember_folder()
        vehicle_editor_ui.open_vehicle_editor(
            self.root, status["path"], profile_name, log=self._log,
            language=self.language)
        return True

    def _delete_vehicle_profile(self):
        if self._busy or self._maintenance_busy:
            self._log("Wait for the current launcher operation to finish.")
            return False
        status = self._refresh_client()
        profile_name = self.vehicle_profile.get().strip()
        if (status.get("client") != core.PORT_0_9_22 or
                profile_name not in self._profile_names):
            self._log("Select a saved vehicle profile before deleting it.")
            return False
        if not self._confirm_delete_profile(profile_name):
            self._log("Vehicle profile deletion was cancelled.")
            return False
        try:
            vehicle_overlays.delete_vehicle_profile(
                status["path"], profile_name)
        except vehicle_overlays.VehicleOverlayError as error:
            self._log("Could not delete the vehicle profile: %s" % error)
            return False
        self.vehicle_profile.set(vehicle_overlays.ORIGINAL_PROFILE_LABEL)
        self._refresh_profiles(status)
        self._update_action_controls()
        self._save_settings()
        self._log("Deleted vehicle profile '%s'." % profile_name)
        return True

    def _confirm_reset(self):
        from tkinter import messagebox

        return messagebox.askyesno(
            self._t("Reset all offline data?"),
            self._t(
                "This deletes this mod's saved address, account settings, "
                "garage fittings, post-battle results, configuration, and "
                "isolated client graphics/input preferences. Other mods and "
                "the normal World of Tanks profile are kept. Continue?"),
            icon="warning")

    def _reset_all_state(self):
        if self._busy or self._maintenance_busy:
            self._log("Wait for the current launcher operation to finish.")
            return False
        if self._refresh_client().get("client") != core.PORT_0_9_22:
            self._log("Select the supported 0.9.22 game folder first.")
            return False
        if core.game_is_running():
            self._log(
                "Close World of Tanks before repairing or resetting offline data.")
            return False
        if not self._confirm_reset():
            self._log("Offline data reset was cancelled.")
            return False
        return self._start_maintenance(core.reset_0_9_22_state)

    def _toggle_lan_server(self):
        if self._busy or self._maintenance_busy:
            self._log("Wait for the current launcher operation to finish.")
            return False
        if self._server_is_running() or self._room_worker_is_running():
            self._stop_lan_room()
            self._update_action_controls()
            return True
        if self._server is not None:
            self._stop_server(force=True)
        status = self._refresh_client()
        if (status.get("client") not in core.SUPPORTED_PORTS or
                self.mode.get() != core.MODE_JOIN):
            self._log(
                "Select Online and a supported game folder first.")
            return False
        try:
            bot_lineup = bot_lineup_profiles.assignments_for(
                self._bot_lineup_store,
                self.bot_lineup_profile.get().strip())
        except bot_lineup_profiles.BotLineupProfileError as error:
            self._log("The selected Bot lineup is invalid: %s" % error)
            return False
        self._remember_folder()
        self._save_settings()
        self._stop_requested = False
        self._set_maintenance_busy(True)
        profile_name = (
            self.vehicle_profile.get().strip()
            if self.vehicle_profile.get().strip() in self._profile_names
            else None)

        def run():
            try:
                self._log("Installing the %s server data into %s..." %
                          (status["client"], status["path"]))
                for action in core.install_client_mod(
                        status["path"], status["client"]):
                    self._log(action)
                if status["client"] == core.PORT_0_9_22:
                    # The room pins one vehicle-data overlay for its whole
                    # lifetime: the hidden worker reads it at startup and the
                    # server serves it to joiners.
                    self._room_vehicle_overlay_root = status["path"]
                    prepared = vehicle_overlays.prepare_vehicle_profile(
                        status["path"], profile_name)
                    if prepared["profile"] is None:
                        self._log(
                            "The LAN room runs original vehicle data; no "
                            "vehicle profile is shared.")
                    else:
                        self._log(
                            "The LAN room pins vehicle profile '%s' (%d "
                            "package member%s); joiners receive it "
                            "automatically." % (
                                prepared["profile"],
                                prepared["installedMembers"],
                                "" if prepared["installedMembers"] == 1
                                else "s"))
                start_options = {
                    "persistent": True,
                    "require_owned": True,
                }
                if bot_lineup:
                    start_options["bot_lineup"] = bot_lineup
                started = self._start_server(
                    status["path"], status["client"], **start_options)
                if started and self._start_worker(
                        status["path"], core.LOCAL_HOST,
                        core.DEFAULT_SERVER_PORT, room_owned=True):
                    self.root.after(0, self._use_local_server_address)
                else:
                    if started:
                        self._log(
                            "The LAN room was not opened because its hidden "
                            "simulation worker is unavailable.")
                    self._stop_lan_room()
            except core.LauncherError as error:
                self._stop_lan_room()
                self._log(str(error))
            except Exception as error:
                self._stop_lan_room()
                self._log("The LAN server could not start: %s" % error)
            finally:
                self._set_maintenance_busy(False)

        thread = threading.Thread(target=run)
        thread.daemon = True
        thread.start()
        return True

    def _use_local_server_address(self):
        self.join_address.set("%s:%d" % (
            core.LOCAL_HOST, core.DEFAULT_SERVER_PORT))
        self._save_settings()

    def _start(self):
        if self._maintenance_busy:
            self._log("Wait for launcher maintenance to finish.")
            return
        if self._busy:
            self._kill_game()
            return
        status = self._refresh_client()
        selected_profile = self.vehicle_profile.get().strip()
        profile_name = (
            selected_profile if selected_profile in self._profile_names
            else None)
        try:
            session_mode = self.mode.get()
            session = core.plan_session(
                status, session_mode, self.join_address.get(),
                vehicle_profile=profile_name)
            session["bot_lineup"] = bot_lineup_profiles.assignments_for(
                self._bot_lineup_store,
                self.bot_lineup_profile.get().strip())
            session[COLLECT_CRASH_REPORTS_SETTING] = bool(
                self.collect_crash_reports.get())
            session[FULL_CRASH_DUMPS_SETTING] = bool(
                self.full_crash_dumps.get())
        except (core.LauncherError,
                bot_lineup_profiles.BotLineupProfileError) as error:
            self._log(str(error))
            return
        self._remember_folder()
        self._save_settings()
        self._observed_crash_roles = set()
        self._forced_stop_roles = set()
        self._stop_requested = False
        self._set_busy(True)
        thread = threading.Thread(
            target=self._run_session,
            args=(status["path"], session, self.player_name.get().strip()))
        thread.daemon = True
        thread.start()

    def _run_session(self, game_root, session, name):
        host = session["host"]
        port = session["tcp_port"]
        needs_worker = (
            session["client"] == core.PORT_0_9_22 and
            session["mode"] == core.MODE_SINGLE)
        server_loopback_only = (
            session["client"] == core.PORT_0_9_22 and
            session["mode"] == core.MODE_SINGLE)
        report_session = None
        crash_roles = set()
        automatic_report_path = None
        self._worker_exited_unexpectedly = False
        reused_server = self._server_is_running()
        owned_room = (
            session["mode"] == core.MODE_JOIN and
            self._owned_room_root(game_root, reused_server))
        try:
            report_session = error_reports.begin_session(
                game_root, needs_worker=needs_worker,
                local_server=(session["needs_server"] or reused_server))
            self._active_report_session = report_session
        except Exception as error:
            self._active_report_session = None
            self._log(
                "Error reporting is unavailable for this session: %s" %
                error)
        self._enable_crash_capture(
            report_session,
            session["client"] == core.PORT_0_9_22 and
            bool(session.get(COLLECT_CRASH_REPORTS_SETTING, False)),
            full_memory=bool(session.get(FULL_CRASH_DUMPS_SETTING, False)))
        if report_session is not None and reused_server:
            try:
                error_reports.attach_server(
                    report_session, dedicated=False)
            except Exception as error:
                self._log(
                    "The persistent server log boundary could not be "
                    "recorded: %s" %
                    error)
        try:
            self._log("Installing the %s mod into %s..." %
                      (session["client"], game_root))
            for action in core.install_client_mod(game_root,
                                                  session["client"]):
                self._log(action)
            if session["client"] == core.PORT_0_9_22:
                if owned_room:
                    # The running room pinned its vehicle data at room start
                    # and its hidden worker (a WorldOfTanks.exe) already
                    # loaded it.  Touching the overlay now would both trip
                    # the game-running guard and desync this client from the
                    # room; only the digest check below may act, and cleanup
                    # runs when the room stops.
                    if session.get("vehicle_profile"):
                        self._log(
                            "The running LAN room keeps the vehicle data it "
                            "pinned at room start; the selected profile "
                            "applies when the room restarts.")
                    else:
                        self._log(
                            "The running LAN room keeps the vehicle data it "
                            "pinned at room start.")
                else:
                    prepared = vehicle_overlays.prepare_vehicle_profile(
                        game_root, session.get("vehicle_profile"))
                    if prepared["profile"] is None:
                        self._log(
                            "No launcher-owned vehicle profile is active; "
                            "other installed mods are unchanged.")
                    else:
                        self._log(
                            "Activated single-player vehicle profile '%s' "
                            "(%d package member%s)." % (
                            prepared["profile"],
                            prepared["installedMembers"],
                            "" if prepared["installedMembers"] == 1 else "s"))
                self._log(core.ensure_0_9_22_preferences_isolation(game_root))
            for path in core.write_settings(game_root, session["client"],
                                            session["mode"], host, port, name):
                self._log("Wrote %s" % path)
            if session["needs_server"]:
                start_options = {
                    "loopback_only": server_loopback_only,
                }
                if session.get("bot_lineup"):
                    start_options["bot_lineup"] = session["bot_lineup"]
                started = self._start_server(
                    game_root, session["client"], **start_options)
                if not started:
                    return
            elif session["mode"] == core.MODE_JOIN:
                if session.get("bot_lineup"):
                    self._log(
                        "The selected exact Bot lineup applies only to a "
                        "server started by this Launcher; this external "
                        "server keeps its own lineup.")
                status = core.listener_status(
                    session["client"], host, port)
                if status == core.LISTENER_COMPATIBLE:
                    self._log("The compatible server at %s:%d answered." %
                              (host, port))
                    if (session["client"] == core.PORT_0_9_22 and
                            not self._sync_vehicle_overlay(
                                game_root, host, port, reused_server)):
                        return
                elif status == core.LISTENER_OCCUPIED:
                    self._log("Something at %s:%d answered, but it is not "
                              "the server for this client. The game was not "
                              "started." % (host, port))
                    return
                else:
                    if session["client"] == core.PORT_0_9_22:
                        self._log(
                            "%s:%d did not answer the compatible 0.9.22 "
                            "protocol. The game was not started." %
                            (host, port))
                        return
                    self._log("Warning: %s:%d did not answer. Start the game "
                              "anyway and click the battle button when the "
                              "host is ready." % (host, port))
            if self._stop_requested:
                return
            if needs_worker:
                worker_started = self._start_worker(game_root, host, port)
                if not worker_started:
                    return
            if self._stop_requested:
                return
            preferred_team = session.get(
                "preferred_team", core.DEFAULT_PREFERRED_TEAM)
            if preferred_team == core.DEFAULT_PREFERRED_TEAM:
                game_crashed = self._run_game(
                    game_root, session["client"], host, port,
                    paired_worker=needs_worker)
            else:
                game_crashed = self._run_game(
                    game_root, session["client"], host, port,
                    paired_worker=needs_worker,
                    preferred_team=preferred_team)
            if game_crashed:
                crash_roles.add(error_reports.ROLE_VISIBLE_CLIENT)
        except core.LauncherError as error:
            self._log(str(error))
        except Exception as error:  # The window must survive any failure.
            self._log("The launcher failed: %s" % error)
        finally:
            if needs_worker and self._worker is not None:
                self._observe_process_exit(
                    self._worker, error_reports.ROLE_HIDDEN_WORKER)
            self._stop_worker()
            if (self._worker_exited_unexpectedly or
                    error_reports.ROLE_HIDDEN_WORKER in
                    self._observed_crash_roles):
                crash_roles.add(error_reports.ROLE_HIDDEN_WORKER)
            crash_roles.update(self._observed_crash_roles)
            self._stop_server()
            if session.get("client") == core.PORT_0_9_22:
                if not owned_room:
                    self._restore_original_vehicle_data(game_root)
            report_finalized = False
            if report_session is not None and self._crash_capture_enabled:
                try:
                    error_reports.cleanup_session_dump_monitors(
                        report_session)
                    error_reports.set_session_crash_roles(
                        report_session, crash_roles)
                    normal_roles = tuple(
                        role for role in error_reports.DUMP_ROLES
                        if role not in crash_roles)
                    error_reports.cleanup_session_dumps(
                        report_session, roles=normal_roles)
                except core.LauncherError as error:
                    self._log(
                        "Could not prepare this session's crash report: %s" %
                        error)
            if report_session is not None:
                try:
                    report_finalized = bool(
                        error_reports.finalize_session(report_session))
                except Exception as error:
                    self._log(
                        "Could not freeze this session's diagnostic logs: "
                        "%s" % error)
            if (report_finalized and self._crash_capture_enabled and
                    crash_roles):
                automatic_report_path = (
                    self._create_automatic_crash_report())
                if automatic_report_path is not None:
                    try:
                        error_reports.cleanup_session_dumps(
                            report_session, roles=crash_roles)
                    except core.LauncherError as error:
                        self._log(
                            "Could not remove the packaged crash dump: %s" %
                            error)
            if self._active_report_session is report_session:
                self._active_report_session = None
            self._crash_capture_enabled = False
            self._procdump_path = None
            self._worker_exited_unexpectedly = False
            self._observed_crash_roles = set()
            self._forced_stop_roles = set()
            self._set_busy(False)
            if automatic_report_path is not None:
                self.root.after(
                    0, lambda path=automatic_report_path:
                    self._offer_crash_report(path))
            if self._close_pending:
                self.root.after(0, self._finish_close)

    def _owned_room_root(self, game_root, reused_server):
        """Return whether the running launcher room shares this game root."""
        context_root = (self._server_context or {}).get("game_root") or ""
        return bool(
            reused_server and context_root and
            os.path.normcase(os.path.realpath(context_root)) ==
            os.path.normcase(os.path.realpath(game_root)))

    def _sync_vehicle_overlay(self, game_root, host, port, reused_server):
        """Make this client run exactly the vehicle data the room serves.

        The room host's launcher pins one overlay at room start.  This client
        either already runs it (host case, digest match), installs the copy
        the server distributes (joiner case), or refuses to start when the
        local game root would disagree with the room's hidden worker.
        """
        try:
            fetched = core.fetch_vehicle_overlay(host, port)
        except core.LauncherError as error:
            self._log("The host vehicle data could not be fetched: %s" %
                      error)
            return False
        try:
            local_digest = vehicle_overlays.vehicle_overlay_digest(game_root)
        except vehicle_overlays.VehicleOverlayError as error:
            self._log("The local vehicle data could not be verified: %s" %
                      error)
            return False
        room_matches = self._owned_room_root(game_root, reused_server)
        if not fetched["supported"]:
            if local_digest:
                try:
                    vehicle_overlays.ensure_original_vehicle_data(game_root)
                except vehicle_overlays.VehicleOverlayError as error:
                    self._log(
                        "The local vehicle profile could not be removed: %s"
                        % error)
                    return False
                self._log(
                    "This server does not share vehicle data; the selected "
                    "vehicle profile was ignored.")
            else:
                self._log(
                    "This server does not share vehicle data; original "
                    "vehicle values stay active.")
            return True
        if not fetched["present"]:
            if local_digest:
                if room_matches:
                    self._log(
                        "The LAN room runs original vehicle data, but a "
                        "vehicle profile is active locally. Stop the LAN "
                        "room or select 'Original vehicle values', then "
                        "start again.")
                    return False
                try:
                    vehicle_overlays.ensure_original_vehicle_data(game_root)
                except vehicle_overlays.VehicleOverlayError as error:
                    self._log(
                        "The local vehicle profile could not be removed: %s"
                        % error)
                    return False
                self._log(
                    "The host runs original vehicle data; the selected "
                    "vehicle profile was ignored.")
            else:
                self._log(
                    "The host runs original vehicle data; no vehicle "
                    "profile is shared.")
            return True
        if fetched["digest"] == local_digest:
            if fetched["profile"]:
                self._log(
                    "Your vehicle data matches the room profile '%s'." %
                    fetched["profile"])
            else:
                self._log("Your vehicle data matches the room.")
            return True
        if room_matches:
            self._log(
                "The LAN room is pinned to the vehicle profile '%s'. Stop "
                "the LAN room and start it again after changing the vehicle "
                "profile." % fetched["profile"])
            return False
        try:
            installed = vehicle_overlays.install_vehicle_overlay(
                game_root, fetched["manifest"], fetched["payload"])
        except vehicle_overlays.VehicleOverlayError as error:
            self._log("The host vehicle data could not be installed: %s" %
                      error)
            return False
        self._log(
            "Installed the host vehicle data profile '%s' (%d package "
            "member%s); original data is restored after this session." % (
                fetched["profile"], installed,
                "" if installed == 1 else "s"))
        return True

    def _start_server(self, game_root, port_version,
                      loopback_only=False, persistent=False,
                      bot_lineup=None, require_owned=False):
        requested_context = {
            "game_root": os.path.normcase(os.path.realpath(
                os.path.abspath(game_root))),
            "port_version": port_version,
            "loopback_only": bool(loopback_only),
            "bot_lineup": list(bot_lineup or ()),
        }
        if self._server_is_running():
            current_context = dict(self._server_context or {})
            current_context.setdefault("bot_lineup", [])
            if current_context != requested_context:
                self._log(
                    "The launcher-owned LAN server uses a different game "
                    "or visibility setting, or a different exact lineup. "
                    "Stop it before starting this session.")
                return False
            self._log("Reusing the launcher-owned %s LAN server." %
                      port_version)
            return True
        if self._server is not None:
            self._stop_server(force=True)
        status = core.listener_status(
            port_version, core.LOCAL_HOST, core.DEFAULT_SERVER_PORT)
        if status == core.LISTENER_COMPATIBLE:
            if loopback_only or require_owned:
                self._log(
                    "This mode needs a fresh launcher-owned server, but a "
                    "compatible server already uses port %d. Close it "
                    "first." % core.DEFAULT_SERVER_PORT)
                return False
            if bot_lineup:
                self._log(
                    "The exact Bot lineup needs a fresh launcher-owned "
                    "server. Stop the compatible server already using port "
                    "%d first." % core.DEFAULT_SERVER_PORT)
                return False
            self._log("A compatible %s LAN server is already running; "
                      "using it." % port_version)
            return True
        if status == core.LISTENER_OCCUPIED:
            self._log("Another program uses port %d and does not speak the "
                      "%s LAN protocol. Close it before starting the game." %
                      (core.DEFAULT_SERVER_PORT, port_version))
            return False
        command = core.server_child_command(port_version)
        environment = core.server_environment(
            port_version, game_root, loopback_only=loopback_only,
            bot_lineup=bot_lineup)
        server_log_path = core.server_log_path()
        report_session = self._active_report_session
        if report_session is not None:
            try:
                attached_path = error_reports.attach_server(
                    report_session, dedicated=True)
                if attached_path is not None:
                    server_log_path = attached_path
                    environment[error_reports.SERVER_SESSION_ENV] = (
                        report_session["id"])
            except Exception as error:
                self._log(
                    "This session's server log could not be isolated: %s" %
                    error)
        self._log("Starting the %s LAN server..." % port_version)
        self._log("Server log: %s" % server_log_path)
        self._server = subprocess.Popen(
            command, env=environment, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, creationflags=_no_console_flags())
        self._server_persistent = bool(persistent)
        self._server_context = requested_context
        pump = threading.Thread(
            target=self._pump_server_output, args=(self._server,))
        pump.daemon = True
        pump.start()
        if not core.wait_for_server(
                port_version, core.LOCAL_HOST, core.DEFAULT_SERVER_PORT,
                cancelled=(None if persistent else
                           lambda: self._stop_requested)):
            if not self._stop_requested:
                self._log(
                    "The LAN server did not answer the %s protocol on port "
                    "%d." % (port_version, core.DEFAULT_SERVER_PORT))
            self._stop_server(force=True)
            return False
        self._log("The LAN server listens on port %d." %
                  core.DEFAULT_SERVER_PORT)
        if not loopback_only:
            for address in core.local_addresses():
                self._log("Other players join with %s:%d" %
                          (address, core.DEFAULT_SERVER_PORT))
        self.root.after(0, self._update_action_controls)
        return True

    def _pump_server_output(self, server=None):
        server = server or self._server
        if server is None or server.stdout is None:
            return
        for line in iter(server.stdout.readline, b""):
            self._log("[server] " + line.decode("utf-8", "replace").rstrip())
        if server is self._server:
            exit_code = server.poll()
            if self._room_worker_is_running():
                self._log(
                    "Stopping the LAN room worker because its server closed.")
                self._stop_worker(room_owned=True)
            if exit_code not in (None, 0):
                self._log("The LAN server stopped with exit code %s." %
                          exit_code)
        self.root.after(0, self._update_action_controls)

    def _start_worker(self, game_root, host, port, room_owned=False):
        self._worker_exited_unexpectedly = False
        starter = core.worker_starter_executable(game_root)
        if not os.path.isfile(starter):
            raise core.LauncherError(
                "The hidden simulation worker starter is missing: %s" %
                starter)
        previous_marker_token = core.worker_ready_marker_token(game_root)
        self._log("Starting the hidden simulation worker...")
        if self._active_report_session is not None:
            try:
                error_reports.expect_worker_starter_reset(
                    self._active_report_session)
            except Exception as error:
                self._log(
                    "The worker starter log boundary could not be recorded: "
                    "%s" % error)
        environment = core.worker_environment(game_root, host, port)
        environment = self._crash_capture_environment(
            environment, error_reports.ROLE_HIDDEN_WORKER)
        worker = subprocess.Popen(
            core.worker_child_command(game_root), cwd=game_root,
            env=environment,
            creationflags=_no_console_flags())
        if room_owned:
            self._room_worker = worker
            self._room_worker_starter_root = game_root
        else:
            self._worker = worker
            self._worker_starter_root = game_root
        if core.wait_for_worker_ready(
                worker, game_root,
                cancelled=lambda: self._stop_requested,
                previous_marker_token=previous_marker_token):
            self._log("The hidden simulation worker is ready.")
            if room_owned:
                watcher = threading.Thread(
                    target=self._watch_room_worker, args=(worker,))
                watcher.daemon = True
                watcher.start()
            return True
        exit_code = self._observe_process_exit(
            worker, error_reports.ROLE_HIDDEN_WORKER)
        if not self._stop_requested:
            if exit_code is None:
                self._log("The hidden simulation worker did not become ready.")
            else:
                self._worker_exited_unexpectedly = True
                self._log(
                    "The hidden simulation worker stopped with exit code %s." %
                    exit_code)
            self._log_worker_failure(game_root)
        self._stop_worker(room_owned=room_owned)
        return False

    def _watch_room_worker(self, worker):
        """Expose a failed room worker as a failed room, never a fallback."""
        try:
            exit_code = worker.wait()
        except Exception:
            return
        if worker is not self._room_worker:
            return
        self._room_worker = None
        self._room_worker_starter_root = None
        self._observe_process_exit(worker, error_reports.ROLE_HIDDEN_WORKER)
        self._log(
            "The LAN room simulation worker stopped with exit code %s. "
            "The room is unavailable; stop it and start a new room." %
            exit_code)
        self.root.after(0, self._update_action_controls)

    def _log_worker_failure(self, game_root):
        try:
            with open(core.worker_failure_log(game_root), "r",
                      encoding="utf-8", errors="replace") as stream:
                detail = stream.read().strip()
        except (IOError, OSError):
            return
        if detail:
            self._log("[worker] %s" % detail.replace("\n", " | "))

    def _stop_worker(self, room_owned=False):
        lock = (self._room_worker_stop_lock if room_owned else
                self._worker_stop_lock)
        with lock:
            return self._stop_worker_locked(room_owned=room_owned)

    def _stop_worker_locked(self, room_owned=False):
        if room_owned:
            worker = self._room_worker
            starter_root = self._room_worker_starter_root
            self._room_worker = None
            self._room_worker_starter_root = None
        else:
            worker = self._worker
            starter_root = self._worker_starter_root
            self._worker = None
            self._worker_starter_root = None
        if worker is None:
            return None
        exit_code = self._observe_process_exit(
            worker, error_reports.ROLE_HIDDEN_WORKER)
        if exit_code is not None:
            return exit_code
        self._log("Stopping the hidden simulation worker...")
        stopped = (starter_root is not None and
                   self._request_starter_stop(worker, starter_root))
        if not stopped:
            exit_code = self._observe_process_exit(
                worker, error_reports.ROLE_HIDDEN_WORKER)
            if exit_code is not None:
                return exit_code
            self._forced_stop_roles.add(
                error_reports.ROLE_HIDDEN_WORKER)
            try:
                worker.terminate()
            except OSError:
                pass
        try:
            exit_code = worker.wait(
                timeout=(core.STARTER_SHUTDOWN_TIMEOUT_SECONDS_0922
                         if stopped else 10))
        except subprocess.TimeoutExpired:
            self._log("The hidden simulation worker did not stop in time.")
            self._forced_stop_roles.add(
                error_reports.ROLE_HIDDEN_WORKER)
            try:
                worker.terminate()
            except OSError:
                pass
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    worker.kill()
                except OSError:
                    pass
            return None
        self._remember_process_exit(
            exit_code, error_reports.ROLE_HIDDEN_WORKER)
        if stopped and exit_code not in (None, 0):
            return exit_code
        return None

    def _stop_visible_starter(self, process, game_root, forced):
        if self._request_starter_stop(process, game_root):
            return
        if process.poll() is not None:
            return
        forced[0] = True
        self._forced_stop_roles.add(error_reports.ROLE_VISIBLE_CLIENT)
        try:
            process.terminate()
        except OSError:
            pass

    def _run_game(self, game_root, port_version, host, port,
                  paired_worker=False,
                  preferred_team=core.DEFAULT_PREFERRED_TEAM):
        self._log("Starting %s..." % core.GAME_EXECUTABLE)
        command = core.visible_client_command(
            game_root, port_version, paired_worker=paired_worker)
        environment = core.visible_client_environment(
            port_version, host, port, paired_worker=paired_worker,
            preferred_team=preferred_team)
        if port_version == core.PORT_0_9_22:
            environment = self._crash_capture_environment(
                environment, error_reports.ROLE_VISIBLE_CLIENT)
        game_process = subprocess.Popen(
            command, cwd=game_root, env=environment)
        self._game = game_process
        self._game_starter_root = (
            game_root if port_version == core.PORT_0_9_22 else None)
        closed_for_required_process = False
        try:
            if paired_worker:
                exit_code, closed_for_required_process = (
                    core.wait_for_paired_player_exit(
                        game_process, game_root,
                        required_process=self._worker))
            else:
                exit_code = game_process.wait()
        finally:
            self._game = None
            self._game_starter_root = None
        worker_exit = (self._worker.poll()
                       if paired_worker and self._worker is not None
                       else None)
        worker_authority_failed = bool(
            paired_worker and not self._stop_requested and
            (closed_for_required_process or worker_exit is not None))
        if worker_authority_failed:
            self._remember_process_exit(
                worker_exit, error_reports.ROLE_HIDDEN_WORKER)
            self._worker_exited_unexpectedly = bool(
                worker_exit is None or
                error_reports.ROLE_HIDDEN_WORKER in
                self._observed_crash_roles)
        authority_closed_visible = (
            closed_for_required_process or worker_authority_failed)
        if authority_closed_visible:
            try:
                visible_exit_evidence = (
                    error_reports.visible_client_exit_evidence(
                        self._active_report_session))
            except Exception:
                visible_exit_evidence = (
                    error_reports.VISIBLE_CLIENT_EXIT_UNKNOWN)
            if (visible_exit_evidence ==
                    error_reports.VISIBLE_CLIENT_EXIT_EXCEPTION and
                    error_reports.ROLE_VISIBLE_CLIENT not in
                    self._forced_stop_roles):
                self._observed_crash_roles.add(
                    error_reports.ROLE_VISIBLE_CLIENT)
        else:
            self._remember_process_exit(
                exit_code, error_reports.ROLE_VISIBLE_CLIENT)
        crashed = (error_reports.ROLE_VISIBLE_CLIENT in
                   self._observed_crash_roles)
        if crashed:
            self._log("The game stopped with exit code %s." % exit_code)
        if paired_worker:
            if worker_exit is not None and not self._stop_requested:
                if self._worker_exited_unexpectedly:
                    self._log(
                        "The hidden simulation worker stopped with exit code "
                        "%s; the game was closed." % worker_exit)
                    self._log_worker_failure(game_root)
                    if (self._server is not None and
                            not self._server_persistent):
                        # The server has already fenced the worker and queued
                        # the terminal roster/result. Give its async outboxes a
                        # short bounded chance to hand those frames to remote
                        # peers before cleanup terminates the local server.
                        time.sleep(core.WORKER_FAILURE_DRAIN_SECONDS_0922)
                else:
                    self._log(
                        "The hidden simulation worker exited normally with "
                        "code %s." % worker_exit)
            self._log("The game closed.")
            return crashed
        if port_version == core.PORT_0_9_22:
            self._log("The game closed.")
            return crashed
        self._log("Waiting %d seconds in case the game restarts itself..." %
                  int(core.GAME_RESTART_GRACE_SECONDS))
        core.wait_for_game_exit(
            core.game_is_running,
            on_restart=lambda: self._log(
                "The game started another process; the server stays up."))
        self._log("The game closed.")
        return crashed

    def _stop_server(self, force=False):
        if self._server_persistent and not force:
            return False
        server = self._server
        self._server = None
        self._server_persistent = False
        self._server_context = None
        if server is not None and server.poll() is None:
            self._log("Stopping the LAN server...")
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        self.root.after(0, self._update_action_controls)
        return server is not None

    def _restore_original_vehicle_data(self, game_root):
        try:
            if not core.wait_for_game_shutdown():
                self._log(
                    "A World of Tanks process did not finish closing; "
                    "vehicle cleanup will retry at the next launcher start.")
                return False
            removed = vehicle_overlays.ensure_original_vehicle_data(game_root)
            if removed:
                self._log(
                    "Removed the temporary vehicle profile; original "
                    "vehicle data is active again.")
            return True
        except vehicle_overlays.VehicleOverlayError as error:
            self._log(
                "Could not restore original vehicle data: %s" % error)
            return False

    def _stop_lan_room(self):
        game_root = self._room_vehicle_overlay_root
        self._stop_worker(room_owned=True)
        stopped = self._stop_server(force=True)
        if (game_root is not None and
                self._restore_original_vehicle_data(game_root)):
            self._room_vehicle_overlay_root = None
        return stopped

    def _on_close(self):
        if self._maintenance_busy:
            self._log(
                "Finish the current launcher maintenance before closing.")
            return False
        if self._busy:
            self._close_pending = True
            self._log("Closing the game and its offline processes...")
            self._kill_game(stop_persistent_server=True)
            return False
        return self._finish_close()

    def _finish_close(self):
        if self._busy or self._maintenance_busy:
            return False
        self._save_settings()
        self._stop_worker()
        self._stop_lan_room()
        self.root.destroy()
        return True

    def run(self):
        if self._initial_crash_prompt_pending:
            self.root.after(0, self._prompt_initial_crash_collection)
        self.root.mainloop()


def _open_server_log(path=None):
    """Persist one bounded server run while preserving the live pipe."""
    try:
        path = path or core.server_log_path()
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        stream = _BoundedLogStream(path)
    except Exception as error:
        try:
            sys.stderr.write(
                "Server log is unavailable; continuing with live output: "
                "%s\n" % error)
            sys.stderr.flush()
        except Exception:
            pass
        return None
    lock = threading.RLock()
    sys.stdout = _TeeTextStream(sys.stdout, stream, lock)
    sys.stderr = _TeeTextStream(sys.stderr, stream, lock)
    return path


def _serve(argv):
    index = argv.index(core.SERVE_FLAG)
    if index + 1 >= len(argv):
        print("--serve needs a client version.")
        return 2
    port_version = argv[index + 1]
    if port_version not in core.SUPPORTED_PORTS:
        print("Unsupported client version: %s" % port_version)
        return 2
    _open_server_log(error_reports.server_log_for_environment())
    print("Server session: version=%s build=%s role=server" % (
        os.environ.get(core.BUILD_SEMANTIC_VERSION_ENV, "unknown"),
        os.environ.get(core.BUILD_IDENTITY_ENV, "unknown")))
    print("Starting the %s LAN server from %s" %
          (port_version, core.server_root()))
    try:
        core.run_server_payload(port_version)
    except Exception:
        # A windowed build turns an unhandled exception into a dialog that
        # waits for a user who is not there. Report it and exit instead.
        import traceback

        print("The %s LAN server stopped: %s" %
              (port_version, traceback.format_exc()))
        return 1
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if core.SERVE_FLAG in argv:
        return _serve(argv)
    import tkinter
    from tkinter import filedialog, ttk

    LauncherWindow(tkinter, ttk, filedialog).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
