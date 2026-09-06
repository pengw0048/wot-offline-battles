"""Launcher logic for the exact supported 0.9.22 client.

This module keeps the client contract explicit and writes only the user-owned
settings files that the port already reads at client startup.
"""

from __future__ import annotations

import base64
import fnmatch
import glob
import json
import os
import re
import socket
import subprocess
import sys
import xml.etree.ElementTree as ElementTree

PORT_0_9_22 = "0.9.22"
SUPPORTED_PORTS = (PORT_0_9_22,)

MODE_SINGLE = "single"
MODE_JOIN = "join"
MODES = (MODE_SINGLE, MODE_JOIN)

DEFAULT_SERVER_PORT = 28782
DEFAULT_TEAM_SIZE = 15
DEFAULT_PREFERRED_TEAM = 0
MIN_TEAM_SIZE = 1
MAX_TEAM_SIZE = 15
LOCAL_HOST = "127.0.0.1"
GAME_EXECUTABLE = "WorldOfTanks.exe"
# The client can close its first process and start another one while it
# starts up. The launcher waits this long after the last one before it
# stops the LAN server.
GAME_RESTART_GRACE_SECONDS = 8.0
GAME_SHUTDOWN_TIMEOUT_SECONDS = 10.0
GAME_SHUTDOWN_POLL_SECONDS = 0.1
PAIRED_PLAYER_WINDOW_CLOSE_GRACE_SECONDS = 3.0
PAIRED_PLAYER_WINDOW_POLL_SECONDS = 0.25
KNOWN_FOLDER_LIMIT = 10
COMMON_GAME_ROOTS = (
    "C:\\Games", "C:\\Program Files", "C:\\Program Files (x86)",
    "C:\\WOT", "D:\\", "D:\\Games", "D:\\Program Files",
    "E:\\", "E:\\Games",
)
SERVE_FLAG = "--serve"

_VERSION_PATTERN = re.compile(r"v\.(\d+(?:\.\d+)+)(?:\s+#(\d+))?")
_SUPPORTED_0_9_22_PREFIX = (0, 9, 22)
_SEMANTIC_VERSION_PATTERN = re.compile(
    r"^[0-9]+(?:\.[0-9]+){2}(?:[-+][A-Za-z0-9.-]+)?$")
_BUILD_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")

_MOD_MARKER_0_9_22 = os.path.join(
    "mods", "0.9.22.0.1", "org.peng.offline_lan_0922*.wotmod")

SERVER_TEAM_SIZE_ENV_0922 = "WOT_0922_TEAM_SIZE"
SERVER_TEAM1_SIZE_ENV_0922 = "WOT_0922_TEAM1_SIZE"
SERVER_TEAM2_SIZE_ENV_0922 = "WOT_0922_TEAM2_SIZE"
SERVER_BOT_LINEUP_ENV_0922 = "WOT_0922_BOT_LINEUP"
SERVER_LOOPBACK_ONLY_ENV_0922 = "WOT_0922_LOOPBACK_ONLY"
SERVER_VEHICLE_OVERLAY_ROOT_ENV_0922 = "WOT_0922_VEHICLE_OVERLAY_ROOT"
VEHICLE_OVERLAY_CAPABILITY = "vehicle_overlay_v1"
CLIENT_SERVER_HOST_ENV_0922 = "OFFLINE_LAN_0922_SERVER_HOST"
CLIENT_SERVER_PORT_ENV_0922 = "OFFLINE_LAN_0922_SERVER_PORT"
CLIENT_MODE_ENV_0922 = "OFFLINE_LAN_0922_CLIENT_MODE"
PLAYER_MODE_0922 = "player"
CLIENT_PREFERRED_TEAM_ENV_0922 = "OFFLINE_LAN_0922_PREFERRED_TEAM"
ALLOW_MULTIPLE_CLIENTS_ENV_0922 = "OFFLINE_LAN_0922_ALLOW_MULTIPLE_CLIENTS"
HIDDEN_DESKTOP_ENV_0922 = "OFFLINE_LAN_0922_HIDDEN_DESKTOP"
WORKER_READY_MARKER_ENV_0922 = "OFFLINE_LAN_0922_WORKER_READY_MARKER"
WORKER_STARTER_FILENAME_0922 = "offline_worker_starter.exe"
WORKER_READY_MARKER_FILENAME_0922 = "offline-worker.ready"
WORKER_FAILURE_LOG_FILENAME_0922 = "offline-worker-starter.log"
SERVER_BOT_CHAT_RUNTIME_ENV_0922 = "WOT_BOT_CHAT_RUNTIME"
SERVER_BOT_CHAT_MODEL_ENV_0922 = "WOT_BOT_CHAT_MODEL"

SERVER_LOG_FILENAME = "server.log"
LAUNCHER_LOG_FILENAME = "launcher.log"
BUILD_IDENTITY_FILENAME_0922 = "build_identity.json"
BUILD_IDENTITY_RELATIVE_PATH_0922 = (
    "mods/configs/offline_lan_0922/" + BUILD_IDENTITY_FILENAME_0922)
BUILD_SEMANTIC_VERSION_ENV = "WOT_OFFLINE_SEMANTIC_VERSION"
BUILD_IDENTITY_ENV = "WOT_OFFLINE_BUILD_IDENTITY"
PLAYER_ENGINE_CONFIG_0922 = "engine_config.offline-player.xml"
PLAYER_ARGUMENT_0922 = "--player"
WORKER_ONLY_ARGUMENT_0922 = "--worker-only"
PAIRED_PLAYER_ARGUMENT_0922 = "--paired-player"
STOP_STARTER_ARGUMENT_0922 = "--stop-starter"
WORKER_READY_TIMEOUT_SECONDS_0922 = 60.0
WORKER_FAILURE_DRAIN_SECONDS_0922 = 0.5
STARTER_CONTROL_TIMEOUT_SECONDS_0922 = 5.0
STARTER_SHUTDOWN_TIMEOUT_SECONDS_0922 = 45.0

_CLIENT_RUNTIME_FILES_0_9_22 = (
    WORKER_STARTER_FILENAME_0922,
    "mods/0.9.22.0.1/offline_instance_guard_native.pyd",
    "res_mods/0.9.22.0.1/engine_config.offline-player.xml",
    "res_mods/0.9.22.0.1/engine_config.offline-worker.xml",
)

_BUNDLED_SERVER_ENTRY_0_9_22 = os.path.join(
    "0.9.22", "server", "windows_server.py")
_SOURCE_SERVER_ENTRY_0_9_22 = os.path.join("server", "windows_server.py")

_SERVER_PROBES = {
    PORT_0_9_22: {
        "protocol": 5,
        "client_build": "wot-0.9.22.0.1-cn-1513",
        "vehicle": "ussr:R11_MS-1",
        "capabilities": (
            "projectile_ledger_v2", "destructible_catalog_v5",
            "ram_contact_ledger_v2", "human_ram_timeline_v1",
            "player_fire_intent_v6", "player_environment_v2",
            "effective_params_v1", "ricochet_continuation_v1"),
        "server_capabilities": (
            "destructible_catalog_v5", "ram_contact_ledger_v2",
            "human_ram_timeline_v1", "player_fire_intent_v6",
            "player_environment_v2", "effective_params_v1",
            "ricochet_continuation_v1"),
    },
}

LISTENER_FREE = "free"
LISTENER_COMPATIBLE = "compatible"
LISTENER_OCCUPIED = "occupied"

_DATASETS_0_9_22 = ("navgraphs", "foliage", "destructibles")
_DATA_INVENTORIES = {
    PORT_0_9_22: tuple((
        "mods/configs/offline_lan_0922/%s" % dataset, 41)
        for dataset in _DATASETS_0_9_22),
}


class LauncherError(Exception):
    """A user-correctable launcher failure."""


def game_executable(game_root):
    return os.path.join(game_root, GAME_EXECUTABLE)


def worker_starter_executable(game_root):
    return os.path.join(game_root, WORKER_STARTER_FILENAME_0922)


def worker_ready_marker(game_root):
    return os.path.join(game_root, WORKER_READY_MARKER_FILENAME_0922)


def worker_ready_marker_token(game_root):
    """Identify one regular ready marker without following a symlink."""
    import stat

    try:
        value = os.lstat(worker_ready_marker(game_root))
    except (IOError, OSError):
        return None
    if not stat.S_ISREG(value.st_mode):
        return None
    mtime_ns = getattr(
        value, "st_mtime_ns", int(value.st_mtime * 1000000000))
    ctime_ns = getattr(
        value, "st_ctime_ns", int(value.st_ctime * 1000000000))
    return (value.st_dev, value.st_ino, value.st_size, mtime_ns, ctime_ns)


def worker_failure_log(game_root):
    return os.path.join(game_root, WORKER_FAILURE_LOG_FILENAME_0922)


def launcher_directory(executable=None, frozen=None):
    """Return the folder containing the launcher the user opened."""
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        return os.path.dirname(os.path.abspath(executable or sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def server_log_path(executable=None, frozen=None):
    """Return the server log beside the launcher executable or script."""
    return os.path.join(
        launcher_directory(executable=executable, frozen=frozen),
        SERVER_LOG_FILENAME)


def launcher_log_path():
    """Return the persistent activity log beside launcher settings."""
    return os.path.join(
        os.path.dirname(os.path.abspath(settings_path())),
        LAUNCHER_LOG_FILENAME)


def visible_client_command(game_root, port_version, paired_worker=False):
    """Build the visible client command for one supported port."""
    if port_version != PORT_0_9_22:
        raise LauncherError("This client version is not supported.")
    argument = (PAIRED_PLAYER_ARGUMENT_0922 if paired_worker
                else PLAYER_ARGUMENT_0922)
    return [worker_starter_executable(game_root), argument]


def visible_client_environment(port_version, host=LOCAL_HOST,
                               port=DEFAULT_SERVER_PORT,
                               paired_worker=False, environment=None,
                               preferred_team=DEFAULT_PREFERRED_TEAM, language="en"):
    """Keep worker-only state out of the visible game process."""
    environment = dict(os.environ if environment is None else environment)
    if port_version != PORT_0_9_22:
        return environment
    _apply_payload_identity_environment(
        environment, bundled_payload_identity(port_version))
    for name in (HIDDEN_DESKTOP_ENV_0922, WORKER_READY_MARKER_ENV_0922):
        environment.pop(name, None)
    environment["WOT_OFFLINE_UI_LANGUAGE"] = ("zh" if language == "zh" else "en")
    environment[CLIENT_MODE_ENV_0922] = PLAYER_MODE_0922
    environment[CLIENT_SERVER_HOST_ENV_0922] = str(host)
    environment[CLIENT_SERVER_PORT_ENV_0922] = str(int(port))
    environment[CLIENT_PREFERRED_TEAM_ENV_0922] = str(
        parse_preferred_team(preferred_team))
    if paired_worker:
        environment[ALLOW_MULTIPLE_CLIENTS_ENV_0922] = "1"
    else:
        environment.pop(ALLOW_MULTIPLE_CLIENTS_ENV_0922, None)
    return environment


def worker_child_command(game_root):
    return [worker_starter_executable(game_root), WORKER_ONLY_ARGUMENT_0922]


def starter_stop_command(game_root, process_id):
    try:
        process_id = int(process_id)
    except (TypeError, ValueError):
        raise LauncherError("The starter process identifier is invalid.")
    if process_id <= 0:
        raise LauncherError("The starter process identifier is invalid.")
    return [worker_starter_executable(game_root),
            STOP_STARTER_ARGUMENT_0922, str(process_id)]


def worker_environment(game_root, host=LOCAL_HOST,
                       port=DEFAULT_SERVER_PORT,
                       team_size=DEFAULT_TEAM_SIZE, environment=None,
                       team1_size=None, team2_size=None):
    """Build the endpoint inherited by the hidden simulation client."""
    environment = server_environment(
        PORT_0_9_22, game_root, environment, team_size=team_size,
        team1_size=team1_size, team2_size=team2_size)
    environment[CLIENT_SERVER_HOST_ENV_0922] = str(host)
    environment[CLIENT_SERVER_PORT_ENV_0922] = str(int(port))
    return environment


def read_client_identity(game_root):
    """Return the version and build recorded in the stock version.xml."""
    path = os.path.join(game_root, "version.xml")
    try:
        root = ElementTree.parse(path).getroot()
    except (IOError, OSError, ElementTree.ParseError):
        return None
    version_node = root if root.tag == "version" else root.find("version")
    if version_node is None:
        return None
    text = "".join(version_node.itertext()).strip()
    match = _VERSION_PATTERN.fullmatch(text)
    if match is None:
        return None
    return (match.group(1), match.group(2))


def read_client_version(game_root):
    """Return the dotted client version recorded in the stock version.xml."""
    identity = read_client_identity(game_root)
    return identity[0] if identity is not None else None


def port_for_version(version, build=None):
    if not version:
        return None
    try:
        parts = tuple(int(part) for part in str(version).split(".")[:3])
    except (TypeError, ValueError):
        return None
    if parts == _SUPPORTED_0_9_22_PREFIX:
        return PORT_0_9_22
    return None


def installed_port(game_root):
    """Return the port whose client mod is installed in this game folder."""
    marker = os.path.join(game_root, _MOD_MARKER_0_9_22)
    if any(os.path.isfile(candidate) for candidate in glob.glob(marker)):
        return PORT_0_9_22
    return None


def detect_port(game_root):
    identity = read_client_identity(game_root)
    if identity is None:
        return None
    return port_for_version(identity[0], identity[1])


def inspect_game_root(game_root):
    """Describe one game folder for the launcher window."""
    game_root = os.path.abspath(game_root or "")
    identity = read_client_identity(game_root)
    version, build = identity if identity is not None else (None, None)
    installed = installed_port(game_root)
    port_version = (port_for_version(version, build)
                    if identity is not None else
                    (installed if installed == PORT_0_9_22 else None))
    return {
        "path": game_root,
        "has_executable": os.path.isfile(game_executable(game_root)),
        "version": version,
        "build": build,
        "client": port_version,
        "mod_installed": (port_version is not None and
                          installed == port_version),
    }


def plan_session(status, mode, join_text="", team_size=DEFAULT_TEAM_SIZE,
                 vehicle_profile=None, team1_size=None, team2_size=None,
                 preferred_team=DEFAULT_PREFERRED_TEAM):
    """Turn the window fields into one battle session, or explain the problem."""
    if not status.get("has_executable"):
        raise LauncherError(
            "Select the folder that contains %s." % GAME_EXECUTABLE)
    port_version = status.get("client")
    if port_version not in SUPPORTED_PORTS:
        raise LauncherError("This client version is not supported.")
    if mode not in MODES:
        raise LauncherError("Select single player, host, or join.")
    host, tcp_port = endpoint_for_mode(mode, join_text)
    effective_team_size = DEFAULT_TEAM_SIZE
    effective_team1_size = DEFAULT_TEAM_SIZE
    effective_team2_size = DEFAULT_TEAM_SIZE
    if mode != MODE_JOIN:
        effective_team1_size = parse_team_size(
            team_size if team1_size is None else team1_size)
        effective_team2_size = parse_team_size(
            team_size if team2_size is None else team2_size)
        effective_team_size = max(
            effective_team1_size, effective_team2_size)
    effective_preferred_team = parse_preferred_team(preferred_team)
    profile_name = str(vehicle_profile or "").strip() or None
    return {
        "client": port_version,
        "mode": mode,
        "host": host,
        "tcp_port": tcp_port,
        "needs_server": server_required(port_version, mode),
        "team_size": effective_team_size,
        "team1_size": effective_team1_size,
        "team2_size": effective_team2_size,
        "preferred_team": effective_preferred_team,
        "vehicle_profile": profile_name,
    }


def parse_endpoint(text, default_port=DEFAULT_SERVER_PORT):
    """Parse the address the player types for a join."""
    value = str(text or "").strip()
    if not value:
        raise LauncherError("Enter the address of the PC that hosts the battle.")
    if ":" in value:
        host, raw_port = value.rsplit(":", 1)
    else:
        host, raw_port = value, default_port
    host = host.strip()
    if not host or any(character.isspace() for character in host) or "/" in host:
        raise LauncherError("The server address is invalid.")
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        raise LauncherError("The server port must be a number.")
    if port < 1 or port > 65535:
        raise LauncherError("The server port must be 1-65535.")
    return (host, port)


def parse_team_size(value):
    """Validate the total number of tanks on each team, including players."""
    if isinstance(value, bool):
        raise LauncherError("Tanks per team must be a whole number from 1 to 15.")
    try:
        team_size = int(value)
    except (TypeError, ValueError):
        raise LauncherError("Tanks per team must be a number from 1 to 15.")
    if isinstance(value, float) and value != team_size:
        raise LauncherError("Tanks per team must be a whole number from 1 to 15.")
    if team_size < MIN_TEAM_SIZE or team_size > MAX_TEAM_SIZE:
        raise LauncherError("Tanks per team must be 1-15.")
    return team_size


def parse_preferred_team(value):
    """Return zero for automatic assignment, or the explicit team 1/2."""
    if value in (None, "", "auto", "Auto"):
        return DEFAULT_PREFERRED_TEAM
    if isinstance(value, bool):
        raise LauncherError("Preferred team must be Automatic, Team 1, or Team 2.")
    try:
        team = int(value)
    except (TypeError, ValueError):
        raise LauncherError("Preferred team must be Automatic, Team 1, or Team 2.")
    if isinstance(value, float) and value != team:
        raise LauncherError("Preferred team must be Automatic, Team 1, or Team 2.")
    if team not in (0, 1, 2):
        raise LauncherError("Preferred team must be Automatic, Team 1, or Team 2.")
    return team


def endpoint_for_mode(mode, join_text="", default_port=DEFAULT_SERVER_PORT):
    if mode == MODE_JOIN:
        return parse_endpoint(join_text, default_port)
    return (LOCAL_HOST, default_port)


def server_required(unused_port_version, mode):
    """Report whether the launcher must run a server for this mode.

    Every battle uses the LAN server, including a single-player battle, so only
    joining somebody else's room needs no local server.
    """
    return mode != MODE_JOIN


def _write_json(path, value, indent=2):
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    temporary_path = path + ".tmp"
    payload = json.dumps(value, indent=indent, sort_keys=False) + "\n"
    with open(temporary_path, "wb") as stream:
        stream.write(payload.encode("utf-8"))
    try:
        os.replace(temporary_path, path)
    except OSError as error:
        # Windows refuses to replace an existing file whose read-only bit was
        # preserved by an older installation. Only relax that specific case;
        # locks and directory permission failures must still surface.
        if getattr(error, "winerror", None) != 5 or not os.path.isfile(path):
            raise
        import stat
        original_mode = os.stat(path).st_mode
        if original_mode & stat.S_IWRITE:
            raise
        os.chmod(path, original_mode | stat.S_IWRITE)
        try:
            os.replace(temporary_path, path)
        except OSError:
            try:
                os.chmod(path, original_mode)
            except OSError:
                pass
            raise


def _read_json(path):
    try:
        with open(path, "rb") as stream:
            value = json.load(stream)
    except (IOError, OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_0_9_22_settings(game_root, mode, host, port, name=None):
    config_dir = os.path.join(game_root, "mods", "configs", "offline_lan_0922")
    endpoint_path = os.path.join(config_dir, "server_endpoint.json")
    _write_json(endpoint_path, {
        "schema": 1,
        "host": host,
        "port": int(port),
    })
    written = [endpoint_path]
    if name:
        config_path = os.path.join(config_dir, "config.json")
        config = _read_json(config_path)
        if config is not None:
            config["name"] = name
            _write_json(config_path, config)
            written.append(config_path)
    return written


def write_settings(game_root, port_version, mode, host, port, name=None):
    if port_version == PORT_0_9_22:
        return write_0_9_22_settings(game_root, mode, host, port, name)
    raise LauncherError("This game folder is not a supported client.")


SERVER_PAYLOAD_DIR = "servers"
CLIENT_PAYLOAD_DIR = "client"
PROCDUMP_FILENAME = "procdump.exe"
PROCDUMP_RUNTIME_DIR = "tools"
PROCDUMP_DOWNLOAD_URL = (
    "https://download.sysinternals.com/files/Procdump.zip")
PROCDUMP_LICENSE_URL = (
    "https://learn.microsoft.com/en-us/sysinternals/license-terms")
PROCDUMP_DOWNLOAD_TIMEOUT_SECONDS = 30
PROCDUMP_ARCHIVE_MAX_BYTES = 8 * 1024 * 1024
PROCDUMP_EXECUTABLE_MAX_BYTES = 8 * 1024 * 1024
INSTALL_MARKER_NAME = "launcher_install.json"

# Where each port keeps the files the launcher must not delete, and the
# marker that records which package is installed.
_USER_DIRS = {
    PORT_0_9_22: "mods/configs/offline_lan_0922",
}

_MUTABLE_STATE_0_9_22 = (
    "config.json",
    "server_endpoint.json",
    "account_state.json",
    "garage_state.json",
    "postbattle_state.json",
)

# Directories the launcher replaces as one unit, the files of its own package
# it removes from a shared directory, and the members it writes only when they
# are absent.  The replacement roots keep stale baked data out of a new server
# run without touching the user's endpoint, account state, or configuration.
_CLIENT_INSTALL = {
    PORT_0_9_22: {
        "replace": tuple(
            "mods/configs/offline_lan_0922/%s" % name
            for name in _DATASETS_0_9_22),
        "prune": (
            ("mods/0.9.22.0.1", "org.peng.offline_lan_0922*"),
            ("mods/configs/offline_lan_0922",
             BUILD_IDENTITY_FILENAME_0922),
        ),
        "keep": ("mods/configs/offline_lan_0922/config.json",),
        "allowed": (
            "mods/0.9.22.0.1/",
            "mods/configs/offline_lan_0922/",
            "res_mods/0.9.22.0.1/",
            WORKER_STARTER_FILENAME_0922,
        ),
        "suffixes": (".exe", ".json", ".pyd", ".wotmod", ".xml"),
        "required": tuple(
            "mods/configs/offline_lan_0922/%s/manifest.json" % name
            for name in _DATASETS_0_9_22) + (
            "mods/configs/offline_lan_0922/config.json",
        ) + _CLIENT_RUNTIME_FILES_0_9_22,
        "owned_files": (
            (BUILD_IDENTITY_RELATIVE_PATH_0922,) +
            _CLIENT_RUNTIME_FILES_0_9_22),
        "package_pattern": (
            "mods/0.9.22.0.1/org.peng.offline_lan_0922*.wotmod"),
    },
}


def repository_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def procdump_executable(base_dir=None):
    """Resolve the user-downloaded ProcDump executable."""
    if base_dir is None:
        base_dir = os.path.dirname(os.path.abspath(settings_path()))
    return os.path.join(
        base_dir, PROCDUMP_RUNTIME_DIR, PROCDUMP_FILENAME)


def _is_x86_pe(payload):
    """Recognize the 32-bit PE image required by the #1513 client."""
    import struct

    if len(payload) < 64 or payload[:2] != b"MZ":
        return False
    pe_offset = struct.unpack("<I", payload[60:64])[0]
    if pe_offset < 64 or pe_offset + 26 > len(payload):
        return False
    if payload[pe_offset:pe_offset + 4] != b"PE\0\0":
        return False
    machine = struct.unpack("<H", payload[pe_offset + 4:pe_offset + 6])[0]
    optional_magic = struct.unpack(
        "<H", payload[pe_offset + 24:pe_offset + 26])[0]
    return machine == 0x014C and optional_magic == 0x010B


def _procdump_authenticode_is_trusted(path):
    """Use Windows' Authenticode policy provider for downloaded code."""
    if os.name != "nt":
        return True

    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = (
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        )

    class WINTRUST_FILE_INFO(ctypes.Structure):
        _fields_ = (
            ("cbStruct", wintypes.DWORD),
            ("pcwszFilePath", wintypes.LPCWSTR),
            ("hFile", wintypes.HANDLE),
            ("pgKnownSubject", ctypes.POINTER(GUID)),
        )

    class WINTRUST_DATA(ctypes.Structure):
        _fields_ = (
            ("cbStruct", wintypes.DWORD),
            ("pPolicyCallbackData", ctypes.c_void_p),
            ("pSIPClientData", ctypes.c_void_p),
            ("dwUIChoice", wintypes.DWORD),
            ("fdwRevocationChecks", wintypes.DWORD),
            ("dwUnionChoice", wintypes.DWORD),
            ("pFile", ctypes.POINTER(WINTRUST_FILE_INFO)),
            ("dwStateAction", wintypes.DWORD),
            ("hWVTStateData", wintypes.HANDLE),
            ("pwszURLReference", wintypes.LPCWSTR),
            ("dwProvFlags", wintypes.DWORD),
            ("dwUIContext", wintypes.DWORD),
        )

    policy = GUID(
        0x00AAC56B, 0xCD44, 0x11D0,
        (ctypes.c_ubyte * 8)(
            0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
    file_info = WINTRUST_FILE_INFO(
        ctypes.sizeof(WINTRUST_FILE_INFO),
        os.path.abspath(path), None, None)
    trust_data = WINTRUST_DATA()
    trust_data.cbStruct = ctypes.sizeof(WINTRUST_DATA)
    trust_data.dwUIChoice = 2  # WTD_UI_NONE
    trust_data.fdwRevocationChecks = 0  # WTD_REVOKE_NONE
    trust_data.dwUnionChoice = 1  # WTD_CHOICE_FILE
    trust_data.pFile = ctypes.pointer(file_info)
    trust_data.dwStateAction = 1  # WTD_STATEACTION_VERIFY
    # Do not make an additional network request while checking the signature.
    trust_data.dwProvFlags = 0x10 | 0x1000

    try:
        verify = ctypes.windll.wintrust.WinVerifyTrust
        verify.argtypes = (
            wintypes.HWND, ctypes.POINTER(GUID), ctypes.c_void_p)
        verify.restype = wintypes.LONG
        result = verify(
            wintypes.HWND(-1), ctypes.byref(policy),
            ctypes.byref(trust_data))
        return result == 0
    except Exception:
        return False
    finally:
        if "verify" in locals():
            trust_data.dwStateAction = 2  # WTD_STATEACTION_CLOSE
            try:
                verify(
                    wintypes.HWND(-1), ctypes.byref(policy),
                    ctypes.byref(trust_data))
            except Exception:
                pass


def procdump_is_installed(path=None):
    """Accept only a regular x86 PE file at the launcher-owned cache path."""
    path = path or procdump_executable()
    try:
        if not os.path.isfile(path):
            return False
        size = os.path.getsize(path)
        if size <= 2 or size > PROCDUMP_EXECUTABLE_MAX_BYTES:
            return False
        with open(path, "rb") as stream:
            valid_pe = _is_x86_pe(
                stream.read(PROCDUMP_EXECUTABLE_MAX_BYTES + 1))
        return valid_pe and _procdump_authenticode_is_trusted(path)
    except (IOError, OSError):
        return False


def download_procdump(path=None, opener=None):
    """Download ProcDump from Microsoft and install its 32-bit executable."""
    import io
    import tempfile
    import urllib.parse
    import urllib.request
    import zipfile

    path = path or procdump_executable()
    if procdump_is_installed(path):
        return path
    opener = opener or urllib.request.urlopen
    response = None
    temporary = None
    try:
        response = opener(
            PROCDUMP_DOWNLOAD_URL,
            timeout=PROCDUMP_DOWNLOAD_TIMEOUT_SECONDS)
        geturl = getattr(response, "geturl", None)
        final_url = geturl() if callable(geturl) else PROCDUMP_DOWNLOAD_URL
        parsed_url = urllib.parse.urlparse(final_url)
        if (parsed_url.scheme.lower() != "https" or
                (parsed_url.hostname or "").lower() !=
                "download.sysinternals.com"):
            raise LauncherError(
                "Microsoft's ProcDump download redirected to an "
                "unexpected site.")
        headers = getattr(response, "headers", None)
        declared_size = None
        if headers is not None:
            declared_size = headers.get("Content-Length")
        if declared_size not in (None, ""):
            try:
                declared_size = int(declared_size)
            except (TypeError, ValueError):
                raise LauncherError(
                    "Microsoft's ProcDump download has an invalid size.")
            if (declared_size <= 0 or
                    declared_size > PROCDUMP_ARCHIVE_MAX_BYTES):
                raise LauncherError(
                    "Microsoft's ProcDump download is unexpectedly large.")
        payload = response.read(PROCDUMP_ARCHIVE_MAX_BYTES + 1)
        if len(payload) > PROCDUMP_ARCHIVE_MAX_BYTES:
            raise LauncherError(
                "Microsoft's ProcDump download is unexpectedly large.")
        if declared_size is not None and len(payload) != declared_size:
            raise LauncherError(
                "Microsoft's ProcDump download was incomplete.")
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload), "r")
        except (IOError, OSError, zipfile.BadZipFile) as error:
            raise LauncherError(
                "Microsoft's ProcDump download is not a valid ZIP: %s" %
                error)
        try:
            members = [
                info for info in archive.infolist()
                if info.filename.replace("\\", "/").lower() ==
                PROCDUMP_FILENAME]
            if len(members) != 1:
                raise LauncherError(
                    "Microsoft's ProcDump ZIP has no unique procdump.exe.")
            member = members[0]
            if (member.is_dir() or member.flag_bits & 0x1 or
                    member.file_size <= 2 or
                    member.file_size > PROCDUMP_EXECUTABLE_MAX_BYTES):
                raise LauncherError(
                    "Microsoft's ProcDump executable has an invalid size.")
            executable = archive.read(member)
        finally:
            archive.close()
        if (len(executable) != member.file_size or
                not _is_x86_pe(executable)):
            raise LauncherError(
                "Microsoft's ProcDump executable is invalid.")

        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".procdump-", suffix=".tmp", dir=directory or None)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(executable)
            stream.flush()
            os.fsync(stream.fileno())
        if not _procdump_authenticode_is_trusted(temporary):
            raise LauncherError(
                "Microsoft's ProcDump signature could not be verified by "
                "Windows.")
        os.replace(temporary, path)
        temporary = None
        return path
    except LauncherError:
        raise
    except Exception as error:
        raise LauncherError(
            "ProcDump could not be downloaded from Microsoft: %s" % error)
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        if temporary is not None:
            try:
                os.remove(temporary)
            except (IOError, OSError):
                pass


def server_root(base_dir=None):
    """Return the directory that holds the bundled or checked-out servers."""
    if base_dir is not None:
        return base_dir
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return os.path.join(bundle_dir, SERVER_PAYLOAD_DIR)
    return repository_root()


def client_archive(port_version, base_dir=None):
    """Return the bundled client archive for one port, when it exists."""
    if base_dir is None:
        bundle_dir = getattr(sys, "_MEIPASS", None)
        base_dir = bundle_dir or repository_root()
    path = os.path.join(base_dir, CLIENT_PAYLOAD_DIR,
                        "%s.zip" % port_version)
    return path if os.path.isfile(path) else None


def _normalize_payload_identity(value):
    """Return one diagnostic build identity, never a compatibility gate."""
    if not isinstance(value, dict) or value.get("schema") != 1:
        return None
    semantic_version = value.get("semanticVersion")
    build_identity = value.get("buildIdentity")
    if (not isinstance(semantic_version, str) or
            _SEMANTIC_VERSION_PATTERN.fullmatch(semantic_version) is None or
            not isinstance(build_identity, str) or
            _BUILD_IDENTITY_PATTERN.fullmatch(build_identity) is None):
        return None
    return {
        "schema": 1,
        "semanticVersion": semantic_version,
        "buildIdentity": build_identity,
    }


def _decode_payload_identity(payload):
    try:
        value = json.loads(payload.decode("utf-8"))
    except (AttributeError, TypeError, UnicodeDecodeError, ValueError):
        return None
    return _normalize_payload_identity(value)


def _archive_payload_identity(path, port_version):
    if port_version != PORT_0_9_22 or path is None:
        return None
    import zipfile

    try:
        with zipfile.ZipFile(path, "r") as archive:
            payload = archive.read(BUILD_IDENTITY_RELATIVE_PATH_0922)
    except (IOError, KeyError, OSError, ValueError, zipfile.BadZipFile):
        return None
    return _decode_payload_identity(payload)


def bundled_payload_identity(port_version, base_dir=None):
    """Read the identity generated into this launcher's trusted payload."""
    return _archive_payload_identity(
        client_archive(port_version, base_dir), port_version)


def installed_payload_identity(game_root, port_version):
    """Read the loose identity installed beside the package-owned data."""
    if port_version != PORT_0_9_22:
        return None
    try:
        with open(_relative_path(
                game_root, BUILD_IDENTITY_RELATIVE_PATH_0922), "rb") as stream:
            return _decode_payload_identity(stream.read())
    except (IOError, OSError):
        return None


def payload_identity_text(identity):
    if identity is None:
        return "version=unknown build=unknown"
    return "version=%s build=%s" % (
        identity["semanticVersion"], identity["buildIdentity"])


def _apply_payload_identity_environment(environment, identity):
    if identity is None:
        environment.pop(BUILD_SEMANTIC_VERSION_ENV, None)
        environment.pop(BUILD_IDENTITY_ENV, None)
        return environment
    environment[BUILD_SEMANTIC_VERSION_ENV] = identity["semanticVersion"]
    environment[BUILD_IDENTITY_ENV] = identity["buildIdentity"]
    return environment


def _payload_release(path, port_version):
    """Return a stable package label without hashing the trusted payload."""
    import zipfile

    if port_version == PORT_0_9_22:
        identity = _archive_payload_identity(path, port_version)
        if identity is not None:
            return "%s:%s:%s" % (
                port_version, identity["semanticVersion"],
                identity["buildIdentity"])

    with zipfile.ZipFile(path, "r") as archive:
        packages = sorted(
            os.path.basename(name) for name in archive.namelist()
            if name.lower().endswith(".wotmod") and
            "org.peng.offline_lan_0922" in os.path.basename(name))
    if packages:
        return "%s:%s" % (port_version, ",".join(packages))
    stat_result = os.stat(path)
    modified_ns = getattr(
        stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1000000000))
    return "%s:%s:%d:%d" % (
        port_version, os.path.basename(path), stat_result.st_size, modified_ns)


def install_marker_path(game_root, port_version):
    return os.path.join(game_root,
                        *(_USER_DIRS[port_version].split("/") +
                          [INSTALL_MARKER_NAME]))


def installed_release(game_root, port_version):
    marker = _read_json(install_marker_path(game_root, port_version))
    return (marker or {}).get("release")


def _inside(root, path):
    root = os.path.normcase(os.path.realpath(os.path.abspath(root)))
    path = os.path.normcase(os.path.realpath(os.path.abspath(path)))
    try:
        return os.path.commonpath((root, path)) == root and path != root
    except ValueError:
        return False


def _relative_path(root, relative):
    path = os.path.join(root, *relative.split("/"))
    if not _inside(root, path):
        raise LauncherError("Refusing to write outside the game folder.")
    return path


def _path_is_covered(path, roots):
    path = os.path.normcase(os.path.abspath(path))
    for root in roots:
        root = os.path.normcase(os.path.abspath(root))
        if path == root or _inside(root, path):
            return True
    return False


def _data_inventory(port_version, read_member, has_member):
    """Return the complete baked-data member set, or None when incomplete."""
    expected = set()
    try:
        for data_root, map_count in _DATA_INVENTORIES[port_version]:
            manifest_member = "%s/manifest.json" % data_root
            expected.add(manifest_member)
            manifest = json.loads(read_member(manifest_member).decode("utf-8"))
            records = manifest.get("maps") if isinstance(manifest, dict) else None
            if not isinstance(records, list) or len(records) != map_count:
                return None
            filenames = set()
            for record in records:
                filename = record.get("file") if isinstance(record, dict) else None
                if (not isinstance(filename, str) or not filename or
                        filename in (".", "..") or "/" in filename or
                        "\\" in filename or not filename.endswith(".json") or
                        filename in filenames):
                    return None
                filenames.add(filename)
                data_member = "%s/%s" % (data_root, filename)
                if not has_member(data_member):
                    return None
                expected.add(data_member)
    except Exception:
        return None
    return expected


def _validate_archive(archive, game_root, port_version, layout):
    """Return safe file members after validating the complete client ZIP."""
    import stat

    members = []
    seen = set()
    for info in archive.infolist():
        member = info.filename
        if member.endswith("/"):
            continue
        parts = member.split("/")
        if (not member or "\\" in member or
                any(not part or part in (".", "..") for part in parts) or
                not any(member.startswith(prefix)
                        for prefix in layout["allowed"]) or
                not member.lower().endswith(layout["suffixes"])):
            raise LauncherError(
                "The bundled %s mod contains an invalid path." % port_version)
        target = os.path.join(game_root, *parts)
        if not _inside(game_root, target):
            raise LauncherError(
                "Refusing to write outside the game folder.")
        key = member.casefold()
        if key in seen:
            raise LauncherError(
                "The bundled %s mod contains duplicate paths." % port_version)
        seen.add(key)
        mode = int(info.external_attr) >> 16
        if mode and stat.S_ISLNK(mode):
            raise LauncherError(
                "The bundled %s mod contains a symbolic link." % port_version)
        members.append((info, member))
    names = set(member for unused, member in members)
    missing = [name for name in layout["required"] if name not in names]
    pattern = layout["package_pattern"]
    packages = [name for name in names if pattern and
                fnmatch.fnmatch(name, pattern)]
    if missing or (pattern and len(packages) != 1):
        raise LauncherError(
            "The bundled %s mod is incomplete." % port_version)
    inventory = _data_inventory(
        port_version, archive.read, lambda member: member in names)
    if inventory is None:
        raise LauncherError(
            "The bundled %s baked data is incomplete." % port_version)
    owned_files = set(layout["owned_files"])
    if BUILD_IDENTITY_RELATIVE_PATH_0922 not in names:
        owned_files.discard(BUILD_IDENTITY_RELATIVE_PATH_0922)
    expected = (inventory | set(layout["keep"]) | set(packages) |
                owned_files)
    if names != expected:
        raise LauncherError(
            "The bundled 0.9.22 mod contains unexpected files.")
    bad_member = archive.testzip()
    if bad_member is not None:
        raise LauncherError(
            "The bundled %s mod is corrupt: %s" %
            (port_version, bad_member))
    return members


def _installation_complete(game_root, port_version, layout):
    if any(not os.path.isfile(_relative_path(game_root, relative))
           for relative in layout["required"]):
        return False
    pattern = layout["package_pattern"]
    if pattern is not None:
        matches = glob.glob(_relative_path(game_root, pattern))
        if len([path for path in matches if os.path.isfile(path)]) != 1:
            return False
    def read_member(member):
        with open(_relative_path(game_root, member), "rb") as stream:
            return stream.read()

    if _data_inventory(
            port_version, read_member,
            lambda member: os.path.isfile(
                _relative_path(game_root, member))) is None:
        return False
    return True


def _stage_archive(archive, members, game_root, transaction_root):
    import shutil

    staged_root = os.path.join(transaction_root, "new")
    os.makedirs(staged_root)
    for info, member in members:
        target = os.path.join(staged_root, *member.split("/"))
        if not _inside(staged_root, target):
            raise LauncherError(
                "Refusing to stage outside the installer workspace.")
        directory = os.path.dirname(target)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with archive.open(info) as source:
            with open(target, "wb") as stream:
                shutil.copyfileobj(source, stream)
    return staged_root


def _transactional_install(game_root, staged_root, members, layout):
    """Swap staged package-owned paths in, restoring every old path on error."""
    transaction_root = os.path.dirname(staged_root)
    backup_root = os.path.join(transaction_root, "backup")
    failed_root = os.path.join(transaction_root, "failed")
    os.makedirs(backup_root)
    os.makedirs(failed_root)

    replace_targets = [
        _relative_path(game_root, relative) for relative in layout["replace"]]
    operations = []
    for relative, target in zip(layout["replace"], replace_targets):
        source = os.path.join(staged_root, *relative.split("/"))
        if not os.path.isdir(source):
            raise LauncherError(
                "The bundled mod is missing %s." % relative)
        operations.append((source, target))
    for unused, member in members:
        source = os.path.join(staged_root, *member.split("/"))
        target = _relative_path(game_root, member)
        if _path_is_covered(target, replace_targets):
            continue
        if member in layout["keep"] and os.path.isfile(target):
            continue
        operations.append((source, target))

    operation_targets = [target for unused, target in operations]
    prune_targets = []
    for relative, pattern in layout["prune"]:
        directory = _relative_path(game_root, relative)
        for path in sorted(glob.glob(os.path.join(directory, pattern))):
            if (os.path.isfile(path) and
                    not _path_is_covered(path, operation_targets)):
                prune_targets.append(path)

    for unused, target in operations:
        parent = os.path.dirname(target)
        if not os.path.isdir(parent):
            os.makedirs(parent)

    backup_targets = []
    for target in operation_targets + prune_targets:
        if os.path.lexists(target) and target not in backup_targets:
            backup_targets.append(target)
    backups = []
    installed = []
    try:
        for index, target in enumerate(backup_targets):
            backup = os.path.join(backup_root, str(index))
            os.replace(target, backup)
            backups.append((target, backup))
        for source, target in operations:
            os.replace(source, target)
            installed.append(target)
    except Exception as error:
        rollback_errors = []
        for index, target in enumerate(reversed(installed)):
            if not os.path.lexists(target):
                continue
            try:
                os.replace(target, os.path.join(failed_root, str(index)))
            except Exception as rollback_error:
                rollback_errors.append(rollback_error)
        for target, backup in reversed(backups):
            if not os.path.lexists(backup):
                continue
            try:
                parent = os.path.dirname(target)
                if not os.path.isdir(parent):
                    os.makedirs(parent)
                os.replace(backup, target)
            except Exception as rollback_error:
                rollback_errors.append(rollback_error)
        if rollback_errors:
            failure = LauncherError(
                "Installation failed and could not be fully restored. "
                "Recovery files remain in %s." % transaction_root)
            failure.preserve_install_staging = True
            raise failure
        raise LauncherError(
            "Installation failed; the previous mod was restored: %s" % error)

    actions = []
    for target in backup_targets:
        relative = os.path.relpath(target, game_root)
        actions.append("Replaced the old %s" % relative)
    return actions, len(operations)


def install_client_mod(game_root, port_version, base_dir=None, force=False):
    """Install the bundled mod and report what changed.

    The user's own files stay: this only clears the directories the package
    owns, removes its own older package files from a shared directory, and
    never overwrites a configuration that is already there.
    """
    import shutil
    import tempfile
    import zipfile

    layout = _CLIENT_INSTALL.get(port_version)
    if layout is None:
        raise LauncherError("This game folder is not a supported client.")
    archive_path = client_archive(port_version, base_dir)
    if archive_path is None:
        raise LauncherError(
            "This launcher carries no %s mod files." % port_version)
    try:
        release = _payload_release(archive_path, port_version)
    except (IOError, OSError, ValueError, zipfile.BadZipFile) as error:
        raise LauncherError(
            "The bundled %s mod cannot be read: %s" %
            (port_version, error))
    payload_identity = bundled_payload_identity(port_version, base_dir)
    installed_identity = installed_payload_identity(game_root, port_version)
    identity_actions = [
        "Bundled 0.9.22 payload: %s." %
        payload_identity_text(payload_identity),
        "Installed 0.9.22 payload before installation: %s." %
        payload_identity_text(installed_identity),
    ]
    if payload_identity is None:
        identity_actions.append(
            "The bundled diagnostic build identity is unavailable; "
            "installation will continue and will not be treated as "
            "current on the next launch.")
    identity_current = (
        payload_identity is not None and
        installed_identity == payload_identity)
    if (not force and installed_release(game_root, port_version) == release
            and identity_current
            and _installation_complete(game_root, port_version, layout)):
        identity_actions.append(
            "Install decision: keep the installed 0.9.22 payload; "
            "the build identity and package-owned files are current.")
        return identity_actions
    if force:
        decision = "forced reinstall"
    elif installed_port(game_root) == port_version:
        decision = "reinstall"
    else:
        decision = "install"
    identity_actions.append(
        "Install decision: %s the bundled 0.9.22 payload." % decision)
    transaction_root = None
    preserve_transaction = False
    try:
        try:
            transaction_root = tempfile.mkdtemp(
                prefix=".wot-offline-install-", dir=game_root)
        except (IOError, OSError) as error:
            raise LauncherError(
                "The game folder is not writable. Move the game to a writable "
                "folder or run the launcher with permission to update it: %s" %
                error)
        try:
            archive = zipfile.ZipFile(archive_path)
        except (IOError, OSError, zipfile.BadZipFile) as error:
            raise LauncherError(
                "The bundled %s mod cannot be opened: %s" %
                (port_version, error))
        try:
            members = _validate_archive(
                archive, game_root, port_version, layout)
            staged_root = _stage_archive(
                archive, members, game_root, transaction_root)
        finally:
            archive.close()
        actions, written = _transactional_install(
            game_root, staged_root, members, layout)
        actions[0:0] = identity_actions
        actions.append("Installed %d %s mod paths" % (written, port_version))
        try:
            marker = {"release": release}
            if payload_identity is not None:
                marker.update({
                    "semanticVersion": payload_identity["semanticVersion"],
                    "buildIdentity": payload_identity["buildIdentity"],
                })
            _write_json(install_marker_path(game_root, port_version), marker)
        except (IOError, OSError):
            actions.append(
                "The mod was installed, but its update marker could not be saved.")
        actions.append(
            "Installed 0.9.22 payload after installation: %s." %
            payload_identity_text(installed_payload_identity(
                game_root, port_version)))
        return actions
    except LauncherError as error:
        preserve_transaction = bool(getattr(
            error, "preserve_install_staging", False))
        raise
    finally:
        if transaction_root is not None and not preserve_transaction:
            shutil.rmtree(transaction_root, ignore_errors=True)


def _valid_0_9_22_config(path):
    """Match the startup-fatal part of the client config contract."""
    try:
        with open(path, "rb") as stream:
            value = json.load(stream)
        if not isinstance(value, dict):
            return False
        for name in ("startupTimeoutSeconds", "prebattleCountdownSeconds",
                     "battleDurationSeconds"):
            if name in value:
                float(value[name])
        if "max_health" in value:
            int(value["max_health"])
        if "enabled" in value and not isinstance(value["enabled"], bool):
            return False
        for name in ("vehicle", "name"):
            if name in value and (not isinstance(value[name], str) or
                                  not value[name]):
                return False
        for name in ("physics_tuning", "he_tuning"):
            if name in value and not isinstance(value[name], dict):
                return False
        if ("perfect_accuracy" in value and
                not isinstance(value["perfect_accuracy"], bool)):
            return False
        if "authority_worker_probe" in value:
            probe = value["authority_worker_probe"]
            if (not isinstance(probe, dict) or
                    not isinstance(probe.get("enabled"), bool)):
                return False
            seconds = float(probe.get("stageSeconds"))
            if (seconds != seconds or seconds in (float("inf"),
                                                  float("-inf")) or
                    seconds < 15.0 or seconds > 60.0):
                return False
    except (IOError, OSError, TypeError, ValueError):
        return False
    return True


def _quarantine_file(path):
    candidate = path + ".invalid"
    suffix = 1
    while os.path.exists(candidate):
        candidate = path + ".invalid.%d" % suffix
        suffix += 1
    try:
        os.replace(path, candidate)
    except (IOError, OSError) as error:
        raise LauncherError(
            "The invalid offline configuration could not be quarantined: %s" %
            error)
    return candidate


def _require_0_9_22_maintenance_target(game_root, is_running=None):
    status = inspect_game_root(game_root)
    if not status["has_executable"]:
        raise LauncherError(
            "Select the folder that contains %s." % GAME_EXECUTABLE)
    if status["client"] != PORT_0_9_22:
        raise LauncherError(
            "Startup repair and saved-data reset require the supported "
            "0.9.22 client.")
    is_running = game_is_running if is_running is None else is_running
    if is_running():
        raise LauncherError(
            "Close World of Tanks before repairing or resetting offline data.")
    return status


def ensure_0_9_22_preferences_isolation(game_root):
    """Redirect the exact 0.9.22 client to its launcher-owned profile."""
    try:
        from . import preferences_overlay
    except ImportError:
        import preferences_overlay

    return preferences_overlay.ensure_preferences_overlay(game_root)


def _isolated_0_9_22_preferences_path(environment=None):
    try:
        from . import preferences_overlay
    except ImportError:
        import preferences_overlay

    return preferences_overlay.profile_path(environment)


def _normal_client_preferences_path(environment=None):
    try:
        from . import preferences_overlay
    except ImportError:
        import preferences_overlay

    return preferences_overlay.normal_profile_path(environment)


def backup_normal_client_preferences(game_root, is_running=None,
                                     environment=None, timestamp=None):
    """Move the stock client's preferences aside as a recoverable backup."""
    import time

    _require_0_9_22_maintenance_target(game_root, is_running)
    path = _normal_client_preferences_path(environment)
    if path is None:
        raise LauncherError(
            "The normal World of Tanks preferences path could not be "
            "resolved from APPDATA.")
    if not os.path.lexists(path):
        return [
            "The normal World of Tanks preferences.xml is already absent."
        ]
    if os.path.islink(path) or not os.path.isfile(path):
        raise LauncherError(
            "The normal World of Tanks preferences path is not a regular "
            "file; it was left unchanged.")

    stamp = str(timestamp or time.strftime("%Y%m%d-%H%M%S"))
    backup = "%s.wot-offline-backup-%s" % (path, stamp)
    suffix = 1
    while os.path.lexists(backup):
        backup = "%s.wot-offline-backup-%s-%d" % (path, stamp, suffix)
        suffix += 1
    try:
        os.replace(path, backup)
    except (IOError, OSError) as error:
        raise LauncherError(
            "The normal World of Tanks preferences could not be backed up: "
            "%s" % error)
    return [
        "Moved the normal World of Tanks preferences.xml to backup: %s" %
        backup
    ]


def repair_0_9_22_startup(game_root, base_dir=None, is_running=None):
    """Refresh package-owned files and preserve every usable saved value."""
    _require_0_9_22_maintenance_target(game_root, is_running)
    config_path = _relative_path(
        game_root, "mods/configs/offline_lan_0922/config.json")
    quarantined = None
    if os.path.isfile(config_path) and not _valid_0_9_22_config(config_path):
        quarantined = _quarantine_file(config_path)
    try:
        actions = install_client_mod(
            game_root, PORT_0_9_22, base_dir, force=True)
        actions.append(ensure_0_9_22_preferences_isolation(game_root))
    except Exception:
        if quarantined is not None and os.path.exists(quarantined):
            if os.path.exists(config_path):
                os.unlink(config_path)
            os.replace(quarantined, config_path)
        raise
    if quarantined is not None:
        actions.insert(0, "Quarantined invalid config.json as %s" %
                       os.path.basename(quarantined))
    actions.append(
        "Startup repair kept the saved endpoint, account, garage, "
        "post-battle results, and isolated client preferences.")
    return actions


def _reset_state_name(name):
    for base_name in _MUTABLE_STATE_0_9_22:
        if name == base_name or name in (
                base_name + ".tmp", base_name + ".bak",
                base_name + ".invalid"):
            return True
        prefix = base_name + ".invalid."
        if name.startswith(prefix) and name[len(prefix):].isdigit():
            return True
    return False


def reset_0_9_22_state(game_root, base_dir=None, is_running=None):
    """Delete this mod's mutable state after the caller confirms the reset."""
    import shutil
    import tempfile

    _require_0_9_22_maintenance_target(game_root, is_running)
    state_root = _relative_path(
        game_root, "mods/configs/offline_lan_0922")
    targets = []
    if os.path.isdir(state_root):
        targets = [os.path.join(state_root, name)
                   for name in sorted(os.listdir(state_root))
                   if _reset_state_name(name) and
                   os.path.isfile(os.path.join(state_root, name))]
    preferences_path = _isolated_0_9_22_preferences_path()
    if preferences_path is not None and os.path.lexists(preferences_path):
        if (os.path.islink(preferences_path) or
                not os.path.isfile(preferences_path)):
            raise LauncherError(
                "The isolated client preferences path is not a regular file; "
                "it was left unchanged.")
        targets.append(preferences_path)

    backup_root = tempfile.mkdtemp(prefix=".wot-offline-reset-", dir=game_root)
    backup_roots = [backup_root]
    moved = []
    try:
        preferences_backup_root = None
        if preferences_path is not None and preferences_path in targets:
            # LOCALAPPDATA and the game can be on different volumes. Keep this
            # backup beside the profile so both the delete and rollback remain
            # atomic filesystem replacements.
            preferences_backup_root = tempfile.mkdtemp(
                prefix=".wot-offline-reset-",
                dir=os.path.dirname(preferences_path))
            backup_roots.append(preferences_backup_root)
        for index, target in enumerate(targets):
            target_backup_root = (
                preferences_backup_root
                if target == preferences_path else backup_root)
            backup = os.path.join(target_backup_root, str(index))
            os.replace(target, backup)
            moved.append((target, backup))
        actions = install_client_mod(
            game_root, PORT_0_9_22, base_dir, force=True)
        actions.append(ensure_0_9_22_preferences_isolation(game_root))
    except Exception as error:
        for target, backup in reversed(moved):
            if os.path.exists(backup):
                os.replace(backup, target)
        for directory in reversed(backup_roots):
            shutil.rmtree(directory, ignore_errors=True)
        if isinstance(error, LauncherError):
            raise
        raise LauncherError("Offline data reset failed: %s" % error)
    for directory in reversed(backup_roots):
        shutil.rmtree(directory, ignore_errors=True)
    actions.insert(0, "Deleted %d offline saved-data file(s)." % len(targets))
    return actions


def server_script(port_version, base_dir=None):
    if port_version != PORT_0_9_22:
        return None
    if base_dir is None and not getattr(sys, "_MEIPASS", None):
        return os.path.join(repository_root(), _SOURCE_SERVER_ENTRY_0_9_22)
    return os.path.join(
        server_root(base_dir), _BUNDLED_SERVER_ENTRY_0_9_22)


def server_argv(port_version, base_dir=None):
    script = server_script(port_version, base_dir)
    if script is None:
        return None
    return [script]


def server_environment(port_version, game_root, environment=None,
                       team_size=DEFAULT_TEAM_SIZE, loopback_only=False,
                       team1_size=None, team2_size=None, bot_lineup=None,
                       bot_chat=None):
    """Build the endpoint and roster environment for one LAN server."""
    environment = dict(os.environ if environment is None else environment)
    if port_version == PORT_0_9_22:
        _apply_payload_identity_environment(
            environment, bundled_payload_identity(port_version))
        team1_size = parse_team_size(
            team_size if team1_size is None else team1_size)
        team2_size = parse_team_size(
            team_size if team2_size is None else team2_size)
        environment[SERVER_TEAM_SIZE_ENV_0922] = str(
            max(team1_size, team2_size))
        environment[SERVER_TEAM1_SIZE_ENV_0922] = str(team1_size)
        environment[SERVER_TEAM2_SIZE_ENV_0922] = str(team2_size)
        environment[SERVER_BOT_LINEUP_ENV_0922] = json.dumps(
            list(bot_lineup or ()), separators=(",", ":"))
        environment[SERVER_VEHICLE_OVERLAY_ROOT_ENV_0922] = os.path.abspath(
            game_root)
        if loopback_only:
            environment[SERVER_LOOPBACK_ONLY_ENV_0922] = "1"
        else:
            environment.pop(SERVER_LOOPBACK_ONLY_ENV_0922, None)
        _apply_bot_chat_environment(environment, bot_chat)
    return environment


def _apply_bot_chat_environment(environment, bot_chat):
    """Name the optional chat model, or make sure no stale one is named.

    Both halves must be present. Naming one alone would start a room that
    reports a disabled feature every time it looks for the other.
    """
    runtime = str((bot_chat or {}).get("runtime") or "")
    model = str((bot_chat or {}).get("model") or "")
    if (runtime and model and os.path.isfile(runtime) and
            os.path.isfile(model)):
        environment[SERVER_BOT_CHAT_RUNTIME_ENV_0922] = runtime
        environment[SERVER_BOT_CHAT_MODEL_ENV_0922] = model
        return
    environment.pop(SERVER_BOT_CHAT_RUNTIME_ENV_0922, None)
    environment.pop(SERVER_BOT_CHAT_MODEL_ENV_0922, None)


def bot_chat_catalogue(port_version=PORT_0_9_22, base_dir=None):
    """Import the pinned model catalogue the bundled server carries.

    The launcher downloads what the server will load, so both must read one
    catalogue. The server payload already reaches this process through
    ``sys.path`` when a room runs in it; this makes the same directory
    importable before any room exists.
    """
    script = server_script(port_version, base_dir)
    if script is None:
        raise LauncherError("Unknown client port: %s" % port_version)
    directory = os.path.dirname(script)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    import bot_chat_models

    return bot_chat_models


def server_child_command(port_version, launcher_script=None, executable=None,
                         frozen=None):
    """Build the command that runs one server in a child of this launcher."""
    executable = executable or sys.executable
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    command = [executable]
    if not frozen:
        command.append(launcher_script or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "wot_launcher.py"))
    command.extend([SERVE_FLAG, port_version])
    return command


def run_server_payload(port_version, base_dir=None):
    """Run one bundled server inside this process."""
    import runpy

    # The packaged launcher carries the server sources as data, so their
    # standard-library imports reach PyInstaller only through this module.
    import server_imports  # noqa: F401

    argv = server_argv(port_version, base_dir)
    if argv is None:
        raise LauncherError("Unknown client port: %s" % port_version)
    script = argv[0]
    if not os.path.isfile(script):
        raise LauncherError("The bundled server is missing: %s" % script)
    directory = os.path.dirname(script)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    sys.argv = list(argv)
    runpy.run_path(script, run_name="__main__")


def connection_report(mode, host, port, answered):
    """Describe one connection test for the launcher window."""
    return listener_report(
        mode, host, port,
        LISTENER_COMPATIBLE if answered else LISTENER_FREE)


def listener_report(mode, host, port, status):
    """Describe whether an endpoint is free, compatible, or occupied."""
    endpoint = "%s:%d" % (host, int(port))
    if mode == MODE_JOIN:
        if status == LISTENER_COMPATIBLE:
            return "The compatible server at %s answered." % endpoint
        if status == LISTENER_OCCUPIED:
            return ("Something at %s answered, but it is not the server for "
                    "this client." % endpoint)
        return ("No answer from %s. Check that the host started the battle "
                "and that its firewall allows TCP %d." %
                (endpoint, int(port)))
    if status == LISTENER_COMPATIBLE:
        return ("A compatible server already listens on %s. Start game will "
                "use it." % endpoint)
    if status == LISTENER_OCCUPIED:
        return ("Another program listens on %s. Close it before you host "
                "here." % endpoint)
    return ("Nothing listens on %s yet. Start game runs the server there." %
            endpoint)


def probe_endpoint(host, port, timeout=1.5, connect=None):
    """Report whether something accepts TCP connections at this endpoint."""
    connect = connect or socket.create_connection
    try:
        connection = connect((host, int(port)), timeout)
    except (socket.error, OSError, ValueError):
        return False
    try:
        connection.close()
    except (socket.error, OSError):
        pass
    return True


def probe_server_protocol(port_version, host, port, timeout=1.5, connect=None):
    """Report whether the endpoint speaks the selected client's LAN protocol."""
    contract = _SERVER_PROBES.get(port_version)
    if contract is None:
        return False
    connect = connect or socket.create_connection
    connection = None
    try:
        connection = connect((host, int(port)), timeout)
        settimeout = getattr(connection, "settimeout", None)
        if callable(settimeout):
            settimeout(timeout)
        hello = {
            "type": "hello",
            "protocol": contract["protocol"],
            "client_build": contract["client_build"],
            "name": "Launcher-Probe",
            "vehicle": contract["vehicle"],
            "max_health": 1,
        }
        hello["role"] = "probe"
        hello["vehicle_compact_descr"] = "AA=="
        if contract["capabilities"] is not None:
            hello["capabilities"] = list(contract["capabilities"])
        connection.sendall(
            (json.dumps(hello, separators=(",", ":")) + "\n").encode(
                "utf-8"))
        payload = b""
        while b"\n" not in payload and len(payload) < 256 * 1024:
            chunk = connection.recv(4096)
            if not chunk:
                break
            payload += chunk
        line, separator, unused = payload.partition(b"\n")
        if not separator:
            return False
        reply = json.loads(line.decode("utf-8"))
        if (not isinstance(reply, dict) or reply.get("type") != "welcome" or
                int(reply.get("protocol", -1)) != contract["protocol"] or
                reply.get("client_build") != contract["client_build"]):
            return False
        capabilities = contract["capabilities"]
        compatible = (capabilities is None or set(capabilities).issubset(
            set(reply.get("capabilities") or ())))
        server_capabilities = contract.get("server_capabilities")
        compatible = (compatible and (
            server_capabilities is None or set(server_capabilities).issubset(
                set(reply.get("server_capabilities") or ()))))
        if compatible:
            try:
                connection.sendall(b'{"type":"leave"}\n')
                shutdown = getattr(connection, "shutdown", None)
                if callable(shutdown):
                    shutdown(socket.SHUT_WR)
                while connection.recv(4096):
                    pass
            except (IOError, OSError, socket.error):
                pass
        return compatible
    except (IOError, OSError, TypeError, ValueError, socket.error):
        return False
    finally:
        if connection is not None:
            try:
                connection.close()
            except (IOError, OSError, socket.error):
                pass


_OVERLAY_DIGEST = re.compile(r"^[0-9a-f]{64}$")
MAX_OVERLAY_MEMBERS = 1024
MAX_OVERLAY_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_OVERLAY_MANIFEST_LINE_BYTES = (
    MAX_OVERLAY_MANIFEST_BYTES + 1024 * 1024)
MAX_OVERLAY_MEMBER_BYTES = 8 * 1024 * 1024
MAX_OVERLAY_TOTAL_BYTES = 64 * 1024 * 1024
MAX_OVERLAY_LINE_BYTES = MAX_OVERLAY_MEMBER_BYTES * 4 // 3 + 1024 * 1024


def _read_json_line(connection, cap):
    """Read one JSON line from a socket, bounded by ``cap`` bytes."""
    payload = b""
    while b"\n" not in payload:
        chunk = connection.recv(4096)
        if not chunk:
            raise LauncherError("The server closed the connection.")
        payload += chunk
        if len(payload) > cap:
            raise LauncherError("The server reply is too large.")
    line, separator, unused = payload.partition(b"\n")
    if not separator:
        raise LauncherError("The server reply is invalid.")
    try:
        value = json.loads(line.decode("utf-8"))
    except (TypeError, ValueError) as error:
        raise LauncherError("The server reply is invalid: %s" % error)
    return value


def fetch_vehicle_overlay(host, port, timeout=5.0, connect=None):
    """Fetch the vehicle-data overlay a 0.9.22 room host shares.

    Returns one dict:

    - ``{"supported": False, "present": False, ...}`` when the server
      predates vehicle-data sharing (its room cannot share data);
    - ``{"supported": True, "present": False, ...}`` when the room runs stock
      vehicle data;
    - ``{"supported": True, "present": True, "manifest": ..., "payload": ...}``
      with the host overlay ready to install.
    """
    contract = _SERVER_PROBES.get(PORT_0_9_22)
    if contract is None:
        raise LauncherError("The 0.9.22 vehicle-data probe is unavailable.")
    connect = connect or socket.create_connection
    connection = connect((host, int(port)), timeout)
    try:
        settimeout = getattr(connection, "settimeout", None)
        if callable(settimeout):
            settimeout(timeout)
        hello = {
            "type": "hello",
            "protocol": contract["protocol"],
            "client_build": contract["client_build"],
            "name": "Launcher-Probe",
            "vehicle": contract["vehicle"],
            "max_health": 1,
            "role": "probe",
            "vehicle_compact_descr": "AA==",
            "capabilities": list(contract["capabilities"]),
        }
        connection.sendall(
            (json.dumps(hello, separators=(",", ":")) + "\n").encode(
                "utf-8"))
        welcome = _read_json_line(connection, 256 * 1024)
        if (not isinstance(welcome, dict) or
                welcome.get("type") != "welcome" or
                int(welcome.get("protocol", -1)) != contract["protocol"] or
                welcome.get("client_build") != contract["client_build"]):
            raise LauncherError("The server is not a compatible 0.9.22 room.")
        if VEHICLE_OVERLAY_CAPABILITY not in set(
                welcome.get("server_capabilities") or ()):
            return {"supported": False, "present": False, "digest": "",
                    "profile": "", "manifest": None, "payload": {}}
        connection.sendall(b'{"type":"vehicle_overlay_query"}\n')
        reply = _read_json_line(connection, MAX_OVERLAY_MANIFEST_LINE_BYTES)
        if (not isinstance(reply, dict) or
                reply.get("type") != "vehicle_overlay_manifest"):
            raise LauncherError("The host vehicle-data reply is invalid.")
        if not reply.get("present"):
            digest = str(reply.get("digest") or "")
            if digest and not _OVERLAY_DIGEST.fullmatch(digest):
                raise LauncherError("The host vehicle-data digest is invalid.")
            return {"supported": True, "present": False, "digest": digest,
                    "profile": str(reply.get("profile") or ""),
                    "manifest": None, "payload": {}}
        manifest = reply.get("manifest")
        if not isinstance(manifest, dict):
            raise LauncherError("The host vehicle-data manifest is invalid.")
        digest = str(reply.get("digest") or "")
        if not _OVERLAY_DIGEST.fullmatch(digest):
            raise LauncherError("The host vehicle-data digest is invalid.")
        members = manifest.get("members")
        if not isinstance(members, list) or not members:
            raise LauncherError("The host vehicle-data manifest is empty.")
        if len(members) > MAX_OVERLAY_MEMBERS:
            raise LauncherError(
                "The host vehicle-data manifest contains more than %d "
                "members." % MAX_OVERLAY_MEMBERS)
        manifest_bytes = (json.dumps(
            manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        if len(manifest_bytes) > MAX_OVERLAY_MANIFEST_BYTES:
            raise LauncherError(
                "The host vehicle-data manifest is larger than %d MiB." %
                (MAX_OVERLAY_MANIFEST_BYTES // (1024 * 1024)))
        payload = {}
        total = 0
        for entry in members:
            if not isinstance(entry, dict):
                raise LauncherError(
                    "The host vehicle-data manifest is invalid.")
            member = entry.get("sourceMember")
            if not isinstance(member, str) or not member:
                raise LauncherError(
                    "The host vehicle-data manifest is invalid.")
            connection.sendall((json.dumps(
                {"type": "vehicle_overlay_member", "sourceMember": member},
                separators=(",", ":")) + "\n").encode("utf-8"))
            data_reply = _read_json_line(connection, MAX_OVERLAY_LINE_BYTES)
            if (not isinstance(data_reply, dict) or
                    data_reply.get("type") != "vehicle_overlay_member_data" or
                    data_reply.get("sourceMember") != member):
                raise LauncherError(
                    "The host vehicle-data member reply is invalid.")
            try:
                raw = base64.b64decode(
                    str(data_reply.get("data_b64") or ""), validate=True)
            except (TypeError, ValueError):
                raise LauncherError(
                    "The host vehicle-data member is corrupt: %s" % member)
            if not raw or len(raw) > MAX_OVERLAY_MEMBER_BYTES:
                raise LauncherError(
                    "The host vehicle-data member size is invalid: %s" %
                    member)
            total += len(raw)
            if total > MAX_OVERLAY_TOTAL_BYTES:
                raise LauncherError(
                    "The host vehicle-data overlay is too large.")
            payload[member] = raw
        return {"supported": True, "present": True, "digest": digest,
                "profile": str(reply.get("profile") or ""),
                "manifest": manifest, "payload": payload}
    except LauncherError:
        raise
    except (IOError, OSError, socket.error, ValueError) as error:
        raise LauncherError(
            "The host vehicle data could not be fetched: %s" % error)
    finally:
        try:
            connection.close()
        except (IOError, OSError, socket.error):
            pass


def listener_status(port_version, host, port, timeout=1.5,
                    endpoint_probe=None, protocol_probe=None):
    """Classify a free port, a matching server, or an unrelated listener."""
    endpoint_probe = endpoint_probe or probe_endpoint
    protocol_probe = protocol_probe or probe_server_protocol
    if not endpoint_probe(host, port, timeout=timeout):
        return LISTENER_FREE
    if protocol_probe(port_version, host, port, timeout=timeout):
        return LISTENER_COMPATIBLE
    return LISTENER_OCCUPIED


def wait_for_listener(host, port, timeout=20.0, interval=0.25, connect=None,
                      clock=None, sleep=None):
    """Wait until the local server accepts a connection."""
    import time as time_module

    clock = clock or time_module.monotonic
    sleep = sleep or time_module.sleep
    deadline = clock() + float(timeout)
    while True:
        if probe_endpoint(host, port, timeout=interval, connect=connect):
            return True
        if clock() >= deadline:
            return False
        sleep(interval)


def wait_for_server(port_version, host, port, timeout=20.0, interval=0.25,
                    probe=None, clock=None, sleep=None, cancelled=None):
    """Wait until the selected client's protocol answers at the endpoint."""
    import time as time_module

    probe = probe or probe_server_protocol
    clock = clock or time_module.monotonic
    sleep = sleep or time_module.sleep
    deadline = clock() + float(timeout)
    while True:
        if callable(cancelled) and cancelled():
            return False
        if probe(port_version, host, port, timeout=interval):
            return True
        if clock() >= deadline:
            return False
        sleep(interval)


def wait_for_worker_ready(process, game_root,
                          timeout=WORKER_READY_TIMEOUT_SECONDS_0922,
                          interval=0.05, clock=None, sleep=None,
                          cancelled=None, previous_marker_token=None):
    """Wait for a live hidden client to publish its ready marker."""
    import time as time_module

    clock = clock or time_module.monotonic
    sleep = sleep or time_module.sleep
    deadline = clock() + float(timeout)
    previous_marker_disappeared = False
    while True:
        if callable(cancelled) and cancelled():
            return False
        if process.poll() is not None:
            return False
        current_marker_token = worker_ready_marker_token(game_root)
        if current_marker_token is None:
            if previous_marker_token is not None:
                previous_marker_disappeared = True
        elif (previous_marker_token is None or
              previous_marker_disappeared or
              current_marker_token != previous_marker_token):
            return process.poll() is None
        if clock() >= deadline:
            return False
        sleep(interval)


def local_addresses(resolver=None):
    """Return the addresses other players can use to reach this host."""
    if resolver is None:
        def resolver():
            return socket.gethostbyname_ex(socket.gethostname())[2]
    try:
        addresses = resolver()
    except (socket.error, OSError, IndexError):
        return []
    return sorted({address for address in addresses
                   if address and not address.startswith('127.')})


def _running_process_names():
    """Return Windows process image names without starting a console tool."""
    if os.name != "nt":
        return None

    import ctypes
    from ctypes import wintypes

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = (
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        )

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.argtypes = (
        wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W))
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = (
        wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W))
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot in (None, 0, invalid_handle):
        raise ctypes.WinError()
    names = []
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                names.append(entry.szExeFile)
                if not kernel32.Process32NextW(
                        snapshot, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snapshot)
    return names


def game_is_running(executable=GAME_EXECUTABLE, enumerator=None):
    """Report whether a game process runs without opening a console window."""
    if enumerator is None:
        if os.name != "nt":
            return False
        enumerator = _running_process_names
    try:
        names = enumerator()
    except Exception:
        return False
    if names is None:
        return False
    target = str(executable).casefold()
    return any(
        os.path.basename(str(name)).casefold() == target
        for name in names)


def _visible_window_process_paths():
    """Return executable paths owning visible windows on this desktop."""
    if os.name != "nt":
        return None

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    process_ids = set()
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    @callback_type
    def collect(window, unused):
        if user32.IsWindowVisible(window):
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(
                window, ctypes.byref(process_id))
            if process_id.value:
                process_ids.add(process_id.value)
        return True

    if not user32.EnumWindows(collect, 0):
        raise ctypes.WinError()

    query_limited_information = 0x1000
    paths = []
    for process_id in process_ids:
        process = kernel32.OpenProcess(
            query_limited_information, False, process_id)
        if not process:
            continue
        try:
            path = ctypes.create_unicode_buffer(32768)
            path_length = wintypes.DWORD(len(path))
            if kernel32.QueryFullProcessImageNameW(
                    process, 0, path, ctypes.byref(path_length)):
                paths.append(path.value)
        finally:
            kernel32.CloseHandle(process)
    return paths


def game_window_is_visible(game_root, enumerator=None):
    """Report whether this game's visible client window still exists.

    ``None`` means the Windows window lookup was unavailable. The hidden
    simulation client lives on a private desktop, so it is deliberately absent
    from this lookup.
    """
    try:
        paths = (_visible_window_process_paths() if enumerator is None
                 else enumerator())
    except Exception:
        return None
    if paths is None:
        return None
    target = os.path.normcase(os.path.realpath(game_executable(game_root)))
    return any(
        os.path.normcase(os.path.realpath(path)) == target
        for path in paths)


def wait_for_paired_player_exit(
        process, game_root, window_visible=None, required_process=None,
        close_grace=PAIRED_PLAYER_WINDOW_CLOSE_GRACE_SECONDS,
        poll=PAIRED_PLAYER_WINDOW_POLL_SECONDS, sleep=None, clock=None,
        stop_process=None,
        shutdown_timeout=GAME_SHUTDOWN_TIMEOUT_SECONDS):
    """Wait for the paired player's native starter process to exit.

    The starter owns the complete visible-client job and follows process
    handoffs. Its process handle is therefore the lifecycle authority. The
    #1513 client destroys and recreates its top-level window while loading a
    map, so window visibility must never terminate a still-live player job.

    The unused compatibility arguments remain for older launcher integrations;
    they no longer participate in the shutdown decision.

    If the required hidden worker exits, retire the paired player job instead
    of leaving a visible client running without its simulation authority. The
    second return value records whether that authority loss caused the close.
    """
    import time as time_module

    sleep = sleep or time_module.sleep
    while True:
        exit_code = process.poll()
        if exit_code is not None:
            return exit_code, False
        if (required_process is not None and
                required_process.poll() is not None):
            try:
                process.terminate()
            except OSError:
                pass
            try:
                exit_code = process.wait(
                    timeout=GAME_SHUTDOWN_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                exit_code = process.wait()
            return exit_code, True
        sleep(max(0.001, float(poll)))


def kill_game(runner=None, executable=GAME_EXECUTABLE):
    """Force every game process to close."""
    if runner is None:
        if os.name != "nt":
            return False
        runner = subprocess.run
    try:
        runner(["taskkill", "/IM", executable, "/T", "/F"],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
               timeout=30,
               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return False
    return True


def wait_for_game_exit(is_running, on_restart=None,
                       grace=GAME_RESTART_GRACE_SECONDS, poll=2.0,
                       sleep=None, clock=None):
    """Wait until no game process has run for the whole grace period."""
    import time as time_module

    clock = clock or time_module.monotonic
    sleep = sleep or time_module.sleep
    restarted = False
    quiet_since = clock()
    while clock() - quiet_since < grace:
        if is_running():
            if not restarted and callable(on_restart):
                on_restart()
            restarted = True
            quiet_since = clock()
        sleep(poll)
    return restarted


def wait_for_game_shutdown(
        is_running=None, timeout=GAME_SHUTDOWN_TIMEOUT_SECONDS,
        poll=GAME_SHUTDOWN_POLL_SECONDS, sleep=None, clock=None):
    """Wait a bounded time for terminated game processes to disappear."""
    import time as time_module

    is_running = game_is_running if is_running is None else is_running
    clock = clock or time_module.monotonic
    sleep = sleep or time_module.sleep
    deadline = clock() + max(0.0, float(timeout))
    while is_running():
        remaining = deadline - clock()
        if remaining <= 0.0:
            return False
        sleep(min(max(0.001, float(poll)), remaining))
    return True


def remember_folder(folders, path, limit=KNOWN_FOLDER_LIMIT):
    """Put one folder at the top of the remembered list."""
    path = os.path.normpath(str(path or "").strip())
    if not path or path == ".":
        return [str(folder) for folder in folders or ()]
    key = os.path.normcase(path)
    kept = [str(folder) for folder in folders or ()
            if os.path.normcase(os.path.normpath(str(folder))) != key]
    return [path] + kept[:limit - 1]


def discover_game_folders(roots=None, is_game=None):
    """Find game folders in the usual install locations."""
    roots = COMMON_GAME_ROOTS if roots is None else roots
    if is_game is None:
        def is_game(path):
            return os.path.isfile(game_executable(path))
    found = []
    for root in roots:
        candidates = [root]
        try:
            candidates.extend(sorted(
                os.path.join(root, name) for name in os.listdir(root)))
        except OSError:
            pass
        for candidate in candidates:
            if candidate not in found and is_game(candidate):
                found.append(candidate)
    return found


def known_folders(settings, discovered=None):
    """Merge the remembered folders with the ones found on this PC."""
    if discovered is None:
        discovered = discover_game_folders()
    folders = []
    seen = set()
    for folder in list(settings.get("folders") or ()) + list(discovered):
        path = os.path.normpath(str(folder))
        key = os.path.normcase(path)
        if not path or path == "." or key in seen:
            continue
        seen.add(key)
        folders.append(path)
    return folders


def settings_path():
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "WoTOfflineBattles", "launcher.json")


def load_settings(path=None):
    return _read_json(path or settings_path()) or {}


def save_settings(values, path=None):
    path = path or settings_path()
    try:
        _write_json(path, dict(values))
    except (IOError, OSError):
        return False
    return True
