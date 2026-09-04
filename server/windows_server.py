#!/usr/bin/env python3
"""Zero-configuration Windows entry point for the LAN battle server."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import sys
import traceback

SERVER_HOST = "0.0.0.0"
SERVER_LOOPBACK_HOST = "127.0.0.1"
SERVER_PORT = 28782
SERVER_MAX_PLAYERS = 30
SERVER_TEAM_SIZE = 15
SERVER_TEAM_SIZE_ENV = "WOT_0922_TEAM_SIZE"
SERVER_TEAM1_SIZE_ENV = "WOT_0922_TEAM1_SIZE"
SERVER_TEAM2_SIZE_ENV = "WOT_0922_TEAM2_SIZE"
SERVER_BOT_LINEUP_ENV = "WOT_0922_BOT_LINEUP"
SERVER_LOOPBACK_ONLY_ENV = "WOT_0922_LOOPBACK_ONLY"
SERVER_VEHICLE_OVERLAY_ROOT_ENV = "WOT_0922_VEHICLE_OVERLAY_ROOT"
BUILD_SEMANTIC_VERSION_ENV = "WOT_OFFLINE_SEMANTIC_VERSION"
BUILD_IDENTITY_ENV = "WOT_OFFLINE_BUILD_IDENTITY"
WINDOWS_FIREWALL_RULE_PREFIX = "WoT 0.9.22 LAN Server"
# Get-NetFirewallRule can take many seconds on a busy machine.
FIREWALL_QUERY_TIMEOUT_SECONDS = 60.0
WINDOWS_FIREWALL_REMOTE_IP = "any"


def _is_frozen_windows_executable():
    """Return whether this process is the packaged Windows server."""
    return os.name == "nt" and bool(getattr(sys, "frozen", False))


def _windows_firewall_rule_name(
        executable_path, port, remote_ip=WINDOWS_FIREWALL_REMOTE_IP):
    """Build a stable rule name for this executable, port, and scope."""
    normalized_path = str(executable_path).replace("/", "\\").casefold()
    normalized_remote_ip = str(remote_ip).strip().casefold()
    identity = "%s|%d|%s" % (
        normalized_path, int(port), normalized_remote_ip)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return "%s TCP %d - %s" % (
        WINDOWS_FIREWALL_RULE_PREFIX, int(port), digest)


def _powershell_single_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _windows_firewall_rule_exists(rule_name, runner=None,
                                  powershell_path=None):
    """Check the deterministic inbound rule without requesting elevation."""
    if runner is None:
        runner = subprocess.run
    if powershell_path is None:
        powershell_path = _windows_system_path(
            r"WindowsPowerShell\v1.0\powershell.exe")
    script = (
        "$rule = Get-NetFirewallRule -DisplayName %s "
        "-ErrorAction SilentlyContinue | Where-Object { "
        "$_.Direction -eq 'Inbound' -and $_.Enabled -eq 'True' -and "
        "$_.Action -eq 'Allow' } | Select-Object -First 1; "
        "if ($null -eq $rule) { exit 1 }; exit 0"
    ) % _powershell_single_quote(rule_name)
    result = runner(
        [
            powershell_path, "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-Command", script,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        timeout=FIREWALL_QUERY_TIMEOUT_SECONDS,
    )
    return result.returncode == 0


def _windows_system_path(relative_path, get_system_directory=None):
    """Resolve a relative executable through the trusted system directory."""
    if get_system_directory is None:
        get_system_directory = ctypes.windll.kernel32.GetSystemDirectoryW
        get_system_directory.argtypes = (ctypes.c_wchar_p, ctypes.c_uint)
        get_system_directory.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    length = int(get_system_directory(buffer, len(buffer)))
    if length <= 0 or length >= len(buffer):
        raise OSError("GetSystemDirectoryW failed")
    return (buffer.value.rstrip("\\/") + "\\" +
            str(relative_path).lstrip("\\/"))


def _windows_system_netsh_path(get_system_directory=None):
    return _windows_system_path(
        "netsh.exe", get_system_directory=get_system_directory)


def _request_windows_firewall_rule(rule_name, executable_path, port,
                                   shell_execute=None, netsh_path=None):
    """Open one UAC prompt for the narrowly scoped inbound rule."""
    arguments = subprocess.list2cmdline([
        "advfirewall", "firewall", "add", "rule",
        "name=" + rule_name,
        "dir=in",
        "action=allow",
        "enable=yes",
        "profile=any",
        "program=" + executable_path,
        "protocol=TCP",
        "localport=%d" % int(port),
        "remoteip=" + WINDOWS_FIREWALL_REMOTE_IP,
    ])
    if shell_execute is None:
        shell_execute = ctypes.windll.shell32.ShellExecuteW
        shell_execute.restype = ctypes.c_void_p
    if netsh_path is None:
        netsh_path = _windows_system_netsh_path()
    result = shell_execute(
        None, "runas", netsh_path, arguments, None, 1)
    return int(result or 0) > 32


def _ensure_windows_firewall_rule(port):
    """Request one inbound rule only for the packaged Windows executable."""
    if not _is_frozen_windows_executable():
        return False

    executable_path = os.path.abspath(sys.executable)
    rule_name = _windows_firewall_rule_name(executable_path, port)
    try:
        if _windows_firewall_rule_exists(rule_name):
            return True
        print(
            "Windows Firewall access needs approval for LAN clients; "
            "opening one UAC prompt.")
        if _request_windows_firewall_rule(
                rule_name, executable_path, port):
            print(
                "Windows Firewall rule request launched for TCP %d "
                "(all remote addresses)." % int(port))
            return True
        print(
            "Windows Firewall rule was not requested; remote LAN clients "
            "may remain blocked.")
    except Exception as error:
        print(
            "Windows Firewall rule setup failed (%s); server will continue."
            % error)
    return False


def _pause_after_error():
    if not getattr(sys.stdin, "isatty", lambda: False)():
        return
    try:
        input("Press Enter to close this window...")
    except (EOFError, KeyboardInterrupt):
        pass


def _load_server():
    from lan_battle_server import DEFAULT_MAP, run_server
    return (DEFAULT_MAP, run_server)


def _team_size_from_environment(environment=None):
    environment = os.environ if environment is None else environment
    raw_value = environment.get(SERVER_TEAM_SIZE_ENV, str(SERVER_TEAM_SIZE))
    if isinstance(raw_value, bool):
        raise ValueError("%s must be 1-15" % SERVER_TEAM_SIZE_ENV)
    try:
        team_size = int(raw_value)
    except (TypeError, ValueError):
        raise ValueError("%s must be a number from 1 to 15" %
                         SERVER_TEAM_SIZE_ENV)
    if not 1 <= team_size <= 15:
        raise ValueError("%s must be 1-15" % SERVER_TEAM_SIZE_ENV)
    return team_size


def _team_sizes_from_environment(environment=None):
    """Read independent capacities, falling back to the legacy shared key."""
    environment = os.environ if environment is None else environment
    legacy = _team_size_from_environment(environment)
    values = []
    for name in (SERVER_TEAM1_SIZE_ENV, SERVER_TEAM2_SIZE_ENV):
        raw_value = environment.get(name, legacy)
        if isinstance(raw_value, bool):
            raise ValueError("%s must be 1-15" % name)
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            raise ValueError("%s must be a number from 1 to 15" % name)
        if not 1 <= value <= 15:
            raise ValueError("%s must be 1-15" % name)
        values.append(value)
    return tuple(values)


def _loopback_only_from_environment(environment=None):
    environment = os.environ if environment is None else environment
    return environment.get(SERVER_LOOPBACK_ONLY_ENV) == "1"


def _bot_lineup_from_environment(environment=None):
    """Load the launcher-owned exact lineup without silent fallback."""
    environment = os.environ if environment is None else environment
    raw_value = environment.get(SERVER_BOT_LINEUP_ENV)
    if raw_value is None:
        return []
    try:
        value = json.loads(raw_value)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid exact Bot lineup JSON: %s" % error)
    if not isinstance(value, list):
        raise ValueError("the exact Bot lineup must be a JSON list")
    return value


def _vehicle_overlay_root_from_environment(environment=None):
    """Return the host game root whose res_mods overlay this server pins."""
    environment = os.environ if environment is None else environment
    root = environment.get(SERVER_VEHICLE_OVERLAY_ROOT_ENV)
    if root is None or not str(root).strip():
        return None
    return str(root).strip()


def _session_identity(environment=None):
    """Return launcher-supplied diagnostic labels without validating peers."""
    environment = os.environ if environment is None else environment
    semantic_version = str(
        environment.get(BUILD_SEMANTIC_VERSION_ENV, "unknown") or
        "unknown").strip()
    build_identity = str(
        environment.get(BUILD_IDENTITY_ENV, "unknown") or
        "unknown").strip()
    return semantic_version, build_identity


def main():
    loopback_only = _loopback_only_from_environment()
    server_host = SERVER_LOOPBACK_HOST if loopback_only else SERVER_HOST
    semantic_version, build_identity = _session_identity()
    print("WoT 0.9.22 Offline LAN Server")
    print("Session identity: version=%s build=%s role=server" % (
        semantic_version, build_identity))
    if loopback_only:
        print("Listening on 127.0.0.1, port %d." % SERVER_PORT)
    else:
        print("Listening on all network interfaces, port %d." % SERVER_PORT)
        print("Use 127.0.0.1 in the client on this PC, or this PC's LAN IP on another PC.")
    print("Press Ctrl+C to stop the server.\n")
    try:
        default_map, run_server = _load_server()
        team1_size, team2_size = _team_sizes_from_environment()
        bot_lineup = _bot_lineup_from_environment()
        vehicle_overlay_root = _vehicle_overlay_root_from_environment()
        if not loopback_only:
            _ensure_windows_firewall_rule(SERVER_PORT)
        run_server(
            server_host, SERVER_PORT, default_map, SERVER_MAX_PLAYERS,
            team_size=SERVER_TEAM_SIZE,
            team1_size=team1_size,
            team2_size=team2_size,
            bot_lineup=bot_lineup,
            vehicle_overlay_root=vehicle_overlay_root,
        )
    except Exception:
        traceback.print_exc()
        _pause_after_error()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
