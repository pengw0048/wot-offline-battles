"""Create privacy-bounded reports for one launcher game session.

The pinned client appends its player and worker output to two distinct files.
The launcher records exact byte offsets before either process starts and
freezes the end offsets when the session ends.  It never discovers a session
by timestamp or copies unrelated launcher/game state.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import stat
import struct
import subprocess
import uuid
import zipfile

try:
    from . import core
except ImportError:
    import core


SESSION_SCHEMA = 1
SESSION_STATE_FILENAME = "latest-error-report-session.json"
REPORTS_DIRECTORY_NAME = "reports"
SESSION_LOGS_DIRECTORY_NAME = "session-logs"
SESSION_DUMPS_DIRECTORY_NAME = "session-dumps"
SERVER_SESSION_ENV = "WOT_OFFLINE_REPORT_SESSION"

ROLE_SERVER = "server"
ROLE_LAUNCHER = "launcher"
ROLE_VISIBLE_CLIENT = "visible-client"
ROLE_HIDDEN_WORKER = "hidden-worker"
ROLE_HIDDEN_WORKER_STARTER = "hidden-worker-starter"

PRIMARY_ROLES = (
    ROLE_SERVER,
    ROLE_VISIBLE_CLIENT,
    ROLE_HIDDEN_WORKER,
)
DUMP_ROLES = (
    ROLE_VISIBLE_CLIENT,
    ROLE_HIDDEN_WORKER,
)
_SOURCE_ORDER = (
    ROLE_LAUNCHER,
    ROLE_SERVER,
    ROLE_VISIBLE_CLIENT,
    ROLE_HIDDEN_WORKER,
    ROLE_HIDDEN_WORKER_STARTER,
)
_GAME_LOG_FILENAMES = {
    ROLE_VISIBLE_CLIENT: "offline-player-python.log",
    ROLE_HIDDEN_WORKER: "offline-worker-python.log",
    ROLE_HIDDEN_WORKER_STARTER: core.WORKER_FAILURE_LOG_FILENAME_0922,
}
_ARCHIVE_FILENAMES = {
    ROLE_LAUNCHER: "launcher.log",
    ROLE_SERVER: "server.log",
    ROLE_VISIBLE_CLIENT: "visible-client.log",
    ROLE_HIDDEN_WORKER: "hidden-worker.log",
    ROLE_HIDDEN_WORKER_STARTER: "hidden-worker-starter.log",
}
_DUMP_FILENAMES = {
    ROLE_VISIBLE_CLIENT: "visible-client.dmp",
    ROLE_HIDDEN_WORKER: "hidden-worker.dmp",
}
_SESSION_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
_CHUNK_BYTES = 64 * 1024
_DUMP_MONITOR_SLOTS = 32
_VISIBLE_CLIENT_CLEAN_EXIT_SUFFIX = (
    b": INFO: PostProcessing.Phases.fini()")
_VISIBLE_CLIENT_LOBBY_RESTORED_SUFFIX = (
    b": INFO: [Offline LAN 0.9.22] deferred lobby Account restored")
_VISIBLE_CLIENT_EXIT_TAIL_BYTES = 4096
MINIDUMP_EVIDENCE_ABSENT = "absent"
MINIDUMP_EVIDENCE_TERMINATION = "termination"
MINIDUMP_EVIDENCE_EXCEPTION = "exception"
MINIDUMP_EVIDENCE_UNKNOWN = "unknown"
VISIBLE_CLIENT_EXIT_CLEAN = "clean"
VISIBLE_CLIENT_EXIT_EXCEPTION = "exception"
VISIBLE_CLIENT_EXIT_TERMINATED = "unexpected-termination"
VISIBLE_CLIENT_EXIT_UNKNOWN = "unknown"
_MINIDUMP_SIGNATURE = b"MDMP"
_MINIDUMP_HEADER_BYTES = 32
_MINIDUMP_DIRECTORY_BYTES = 12
_MINIDUMP_EXCEPTION_STREAM = 6
_MINIDUMP_MAX_STREAMS = 4096


def _application_directory():
    return os.path.dirname(os.path.abspath(core.settings_path()))


def session_state_path():
    return os.path.join(_application_directory(), SESSION_STATE_FILENAME)


def reports_directory():
    return os.path.join(_application_directory(), REPORTS_DIRECTORY_NAME)


def session_logs_directory():
    return os.path.join(
        _application_directory(), SESSION_LOGS_DIRECTORY_NAME)


def session_dumps_directory():
    return os.path.join(
        _application_directory(), SESSION_DUMPS_DIRECTORY_NAME)


def _valid_session_id(session_id):
    value = str(session_id or "")
    if _SESSION_ID.fullmatch(value) is None:
        raise core.LauncherError("The diagnostic session identifier is invalid.")
    return value


def session_server_log_path(session_id):
    session_id = _valid_session_id(session_id)
    return os.path.join(
        session_logs_directory(), session_id, core.SERVER_LOG_FILENAME)


def _expected_dump_layout(session_id):
    session_id = _valid_session_id(session_id)
    directory = os.path.join(session_dumps_directory(), session_id)
    paths = dict((role, os.path.join(directory, _DUMP_FILENAMES[role]))
                 for role in DUMP_ROLES)
    return directory, paths


def _is_reparse_point(value):
    attributes = getattr(value, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(flag and attributes & flag)


def _recorded_dump_layout(session, required=True):
    if not isinstance(session, dict):
        raise core.LauncherError(
            "The diagnostic session boundary is unreadable.")
    directory, paths = _expected_dump_layout(session.get("id"))
    recorded_directory = session.get("dumpDirectory")
    recorded_paths = session.get("dumpPaths")
    if recorded_directory is None and recorded_paths is None and not required:
        return None
    if recorded_directory != directory or recorded_paths != paths:
        raise core.LauncherError(
            "The diagnostic dump boundary is unreadable.")
    return directory, paths


def _safe_dump_directory(directory, create=False):
    base = session_dumps_directory()
    for candidate in (base, directory):
        if not os.path.lexists(candidate):
            if not create:
                return False
            try:
                os.makedirs(candidate)
            except (IOError, OSError):
                pass
        try:
            value = os.lstat(candidate)
        except (IOError, OSError):
            raise core.LauncherError(
                "The diagnostic dump folder could not be created.")
        if (_is_reparse_point(value) or stat.S_ISLNK(value.st_mode) or
                not stat.S_ISDIR(value.st_mode)):
            raise core.LauncherError(
                "The diagnostic dump folder is not a regular directory.")
    try:
        contained = os.path.commonpath(
            (os.path.realpath(base), os.path.realpath(directory))) == (
                os.path.realpath(base))
    except ValueError:
        contained = False
    if not contained:
        raise core.LauncherError("The diagnostic dump folder is unsafe.")
    return True


def _prepare_session_dump_directory(session_id):
    directory, _paths = _expected_dump_layout(session_id)
    _safe_dump_directory(directory, create=True)
    return directory


def _normalize_dump_roles(roles, default_all=False):
    if roles is None:
        values = DUMP_ROLES if default_all else ()
    elif isinstance(roles, str):
        values = (roles,)
    else:
        try:
            values = tuple(roles)
        except TypeError:
            raise core.LauncherError(
                "The diagnostic dump role list is invalid.")
    if any(role not in DUMP_ROLES for role in values):
        raise core.LauncherError("The diagnostic dump role is invalid.")
    selected = set(values)
    return tuple(role for role in DUMP_ROLES if role in selected)


def session_dump_paths(session):
    """Return the two fixed launcher-owned dump destinations."""
    directory, paths = _recorded_dump_layout(session)
    _safe_dump_directory(directory, create=True)
    return dict(paths)


def session_dump_path(session, role):
    roles = _normalize_dump_roles((role,))
    return session_dump_paths(session)[roles[0]]


def _session_dump_monitor_paths(paths, roles):
    temporary = []
    for role in roles:
        stem, extension = os.path.splitext(paths[role])
        for slot in range(_DUMP_MONITOR_SLOTS):
            temporary.append(
                "%s.monitor-%02d.tmp%s" % (stem, slot, extension))
    return tuple(temporary)


def _remove_dump_entries(directory, candidates):
    removed = []
    for path in candidates:
        try:
            value = os.lstat(path)
        except (IOError, OSError):
            continue
        if (stat.S_ISDIR(value.st_mode) and
                not stat.S_ISLNK(value.st_mode)):
            raise core.LauncherError(
                "The diagnostic dump path is not a regular file.")
        try:
            os.unlink(path)
        except (IOError, OSError) as error:
            raise core.LauncherError(
                "The diagnostic dump could not be removed: %s" % error)
        removed.append(path)
    try:
        os.rmdir(directory)
    except (IOError, OSError):
        pass
    return tuple(removed)


def cleanup_session_dump_monitors(session, roles=None):
    """Delete only the fixed native-monitor slots for this session."""
    directory, paths = _recorded_dump_layout(session)
    selected = _normalize_dump_roles(roles, default_all=True)
    if not selected or not _safe_dump_directory(directory, create=False):
        return ()
    return _remove_dump_entries(
        directory, _session_dump_monitor_paths(paths, selected))


def cleanup_session_dumps(session, roles=None):
    """Delete fixed final and monitor dump entries for this session."""
    directory, paths = _recorded_dump_layout(session)
    selected = _normalize_dump_roles(roles, default_all=True)
    if not selected:
        return ()
    if not _safe_dump_directory(directory, create=False):
        return ()
    candidates = tuple(paths[role] for role in selected)
    candidates += _session_dump_monitor_paths(paths, selected)
    return _remove_dump_entries(directory, candidates)


def set_session_crash_roles(session, roles):
    """Allow only confirmed crashing process roles into the report ZIP."""
    _recorded_dump_layout(session)
    selected = _normalize_dump_roles(roles)
    if not _is_latest(session):
        return False
    session["crashRoles"] = list(selected)
    _write_state(session)
    return True


def _prepare_session_server_directory(session_id):
    directory = os.path.dirname(session_server_log_path(session_id))
    base = session_logs_directory()
    for candidate in (base, directory):
        if os.path.lexists(candidate):
            value = os.lstat(candidate)
            if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
                raise core.LauncherError(
                    "The diagnostic server log folder is not a regular "
                    "directory.")
        else:
            os.makedirs(candidate)
    if os.path.commonpath((os.path.realpath(base), os.path.realpath(directory))
                          ) != os.path.realpath(base):
        raise core.LauncherError(
            "The diagnostic server log folder is unsafe.")
    return directory


def server_log_for_environment(environment=None):
    environment = os.environ if environment is None else environment
    session_id = environment.get(SERVER_SESSION_ENV)
    if not session_id:
        return core.server_log_path()
    _prepare_session_server_directory(session_id)
    return session_server_log_path(session_id)


def _file_identity(file_stat):
    device = int(getattr(file_stat, "st_dev", 0) or 0)
    inode = int(getattr(file_stat, "st_ino", 0) or 0)
    return [device, inode] if device or inode else None


def _checkpoint(path, kind):
    source = {
        "kind": kind,
        "offset": 0,
        "existed": False,
        "identity": None,
        "blocked": False,
    }
    try:
        value = os.lstat(path)
    except (IOError, OSError):
        return source
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode):
        source["blocked"] = True
        return source
    source.update({
        "offset": int(value.st_size),
        "existed": True,
        "identity": _file_identity(value),
    })
    return source


def _write_state(session):
    path = session_state_path()
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(session, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def _read_state():
    try:
        with open(session_state_path(), "r", encoding="utf-8") as stream:
            session = json.load(stream)
    except (IOError, OSError, ValueError):
        return None
    return session if isinstance(session, dict) else None


def _new_session_id(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    stamp = now.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return "%s-%s" % (stamp, uuid.uuid4().hex[:12])


def begin_session(game_root, needs_worker=False, local_server=False,
                  session_id=None, started_at=None):
    """Replace the report boundary before any process for a game starts."""
    previous = _read_state()
    if isinstance(previous, dict):
        try:
            if _recorded_dump_layout(previous, required=False) is not None:
                cleanup_session_dumps(previous)
        except core.LauncherError:
            # Never follow a stale or redirected boundary just to clean it up.
            pass
    game_root = os.path.realpath(os.path.abspath(game_root))
    session_id = _valid_session_id(session_id or _new_session_id())
    started_at = started_at or datetime.datetime.now(
        datetime.timezone.utc).isoformat()
    dump_directory, dump_paths = _expected_dump_layout(session_id)
    _prepare_session_dump_directory(session_id)
    dump_boundary = {
        "id": session_id,
        "dumpDirectory": dump_directory,
        "dumpPaths": dump_paths,
    }
    cleanup_session_dumps(dump_boundary)
    _prepare_session_dump_directory(session_id)
    expected = [ROLE_VISIBLE_CLIENT]
    if local_server:
        expected.append(ROLE_SERVER)
    if needs_worker:
        expected.append(ROLE_HIDDEN_WORKER)
    sources = {
        ROLE_LAUNCHER: _checkpoint(core.launcher_log_path(), "launcher"),
        ROLE_VISIBLE_CLIENT: _checkpoint(
            os.path.join(game_root, _GAME_LOG_FILENAMES[ROLE_VISIBLE_CLIENT]),
            "game"),
    }
    if needs_worker:
        for role in (ROLE_HIDDEN_WORKER, ROLE_HIDDEN_WORKER_STARTER):
            sources[role] = _checkpoint(
                os.path.join(game_root, _GAME_LOG_FILENAMES[role]), "game")
    session = {
        "schema": SESSION_SCHEMA,
        "id": session_id,
        "gameRoot": game_root,
        "startedAt": str(started_at),
        "endedAt": None,
        "expectedRoles": sorted(expected),
        "sources": sources,
        "dumpDirectory": dump_directory,
        "dumpPaths": dump_paths,
        "crashRoles": [],
    }
    _write_state(session)
    return session


def _is_latest(session):
    current = _read_state()
    return bool(current and current.get("id") == session.get("id"))


def attach_server(session, dedicated=False):
    """Attach either this session's new server or a reused persistent one."""
    if not _is_latest(session):
        return None
    if dedicated:
        _prepare_session_server_directory(session["id"])
        path = session_server_log_path(session["id"])
        kind = "session-server"
    else:
        path = core.server_log_path()
        kind = "launcher-server"
    session.setdefault("sources", {})[ROLE_SERVER] = _checkpoint(path, kind)
    _write_state(session)
    return path


def expect_worker_starter_reset(session):
    """Record that the launched native starter will delete its old log."""
    if not _is_latest(session):
        return False
    source = session.get("sources", {}).get(ROLE_HIDDEN_WORKER_STARTER)
    if source is None:
        return False
    source["resetExpected"] = True
    _write_state(session)
    return True


def _source_path(session, role, source):
    kind = source.get("kind")
    if kind == "launcher" and role == ROLE_LAUNCHER:
        return core.launcher_log_path()
    if kind == "game" and role in _GAME_LOG_FILENAMES:
        return os.path.join(
            session["gameRoot"], _GAME_LOG_FILENAMES[role])
    if kind == "launcher-server" and role == ROLE_SERVER:
        return core.server_log_path()
    if kind == "session-server" and role == ROLE_SERVER:
        return session_server_log_path(session["id"])
    return None


def _same_identity(expected, actual):
    return expected is None or list(expected) == list(actual or ())


def _freeze_source(session, role, source):
    source["finalized"] = True
    source["invalidated"] = False
    source["end"] = int(source.get("offset", 0))
    if source.get("blocked"):
        source["invalidated"] = True
        return
    path = _source_path(session, role, source)
    if path is None:
        source["invalidated"] = True
        return
    try:
        value = os.lstat(path)
    except (IOError, OSError):
        if source.get("existed"):
            source["invalidated"] = True
        return
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode):
        source["invalidated"] = True
        return
    identity = _file_identity(value)
    offset = int(source.get("offset", 0))
    if (source.get("existed") and
            not _same_identity(source.get("identity"), identity)):
        if source.get("resetExpected"):
            source["offset"] = 0
            source["existed"] = False
            source["identity"] = None
            offset = 0
        else:
            source["invalidated"] = True
            return
    if int(value.st_size) < offset:
        source["invalidated"] = True
        return
    source["end"] = int(value.st_size)
    source["finalIdentity"] = identity


def finalize_session(session, ended_at=None):
    """Freeze exact end offsets so a persistent server cannot leak later data."""
    if not _is_latest(session):
        return False
    for role, source in session.get("sources", {}).items():
        _freeze_source(session, role, source)
    session["endedAt"] = str(ended_at or datetime.datetime.now(
        datetime.timezone.utc).isoformat())
    _write_state(session)
    return True


def _validated_session():
    session = _read_state()
    if session is None:
        raise core.LauncherError(
            "No launcher game session is available to report yet.")
    if (session.get("schema") != SESSION_SCHEMA or
            _SESSION_ID.fullmatch(str(session.get("id") or "")) is None or
            not isinstance(session.get("gameRoot"), str) or
            not os.path.isabs(session["gameRoot"]) or
            not isinstance(session.get("sources"), dict)):
        raise core.LauncherError(
            "The latest diagnostic session boundary is unreadable.")
    expected = session.get("expectedRoles")
    if (not isinstance(expected, list) or
            any(role not in PRIMARY_ROLES for role in expected)):
        raise core.LauncherError(
            "The latest diagnostic session boundary is unreadable.")
    if any(role not in _SOURCE_ORDER or not isinstance(source, dict)
           for role, source in session["sources"].items()):
        raise core.LauncherError(
            "The latest diagnostic session boundary is unreadable.")
    dump_layout = _recorded_dump_layout(session, required=False)
    crash_roles = session.get("crashRoles", [])
    if (not isinstance(crash_roles, list) or
            any(role not in DUMP_ROLES for role in crash_roles) or
            len(set(crash_roles)) != len(crash_roles) or
            (dump_layout is None and crash_roles)):
        raise core.LauncherError(
            "The latest diagnostic session boundary is unreadable.")
    return session


def _open_valid_source(session, role, source):
    if source.get("blocked") or source.get("invalidated"):
        return None
    path = _source_path(session, role, source)
    if path is None:
        return None
    try:
        path_stat = os.lstat(path)
        if (_is_reparse_point(path_stat) or
                stat.S_ISLNK(path_stat.st_mode) or
                not stat.S_ISREG(path_stat.st_mode)):
            return None
        stream = open(path, "rb")
    except (IOError, OSError):
        return None
    try:
        value = os.fstat(stream.fileno())
        if (not stat.S_ISREG(value.st_mode) or
                not _same_identity(
                    _file_identity(path_stat), _file_identity(value))):
            stream.close()
            return None
        identity = _file_identity(value)
        expected_identity = (source.get("identity")
                             if source.get("existed")
                             else source.get("finalIdentity"))
        identity_changed = not _same_identity(expected_identity, identity)
        if identity_changed and not source.get("resetExpected"):
            stream.close()
            return None
        start = (0 if identity_changed and source.get("resetExpected")
                 else int(source.get("offset", 0)))
        end = (int(source.get("end", start))
               if source.get("finalized") else int(value.st_size))
        if start < 0 or end <= start or end > int(value.st_size):
            stream.close()
            return None
        stream.seek(start)
        return stream, end - start
    except Exception:
        stream.close()
        return None


def _open_recorded_dump(session, role):
    """Open one fixed launcher-owned dump without selecting it for a ZIP."""
    layout = _recorded_dump_layout(session, required=False)
    if layout is None or role not in DUMP_ROLES:
        return None
    directory, paths = layout
    try:
        if not _safe_dump_directory(directory, create=False):
            return None
    except core.LauncherError:
        return None
    path = paths[role]
    try:
        path_stat = os.lstat(path)
        if (_is_reparse_point(path_stat) or
                stat.S_ISLNK(path_stat.st_mode) or
                not stat.S_ISREG(path_stat.st_mode)):
            return None
        stream = open(path, "rb")
    except (IOError, OSError):
        return None
    try:
        value = os.fstat(stream.fileno())
        if (not stat.S_ISREG(value.st_mode) or
                not _same_identity(
                    _file_identity(path_stat), _file_identity(value))):
            stream.close()
            return None
        return stream, int(value.st_size)
    except Exception:
        stream.close()
        return None


def _read_minidump_evidence(stream, length):
    # ProcDump's -t mode writes a valid dump for ordinary process termination.
    # Only a bounded, in-file ExceptionStream distinguishes an exception dump.
    if int(length) < _MINIDUMP_HEADER_BYTES:
        return MINIDUMP_EVIDENCE_UNKNOWN
    header = stream.read(_MINIDUMP_HEADER_BYTES)
    if (len(header) != _MINIDUMP_HEADER_BYTES or
            header[:4] != _MINIDUMP_SIGNATURE):
        return MINIDUMP_EVIDENCE_UNKNOWN
    try:
        stream_count, directory_rva = struct.unpack_from("<II", header, 8)
    except struct.error:
        return MINIDUMP_EVIDENCE_UNKNOWN
    if stream_count > _MINIDUMP_MAX_STREAMS:
        return MINIDUMP_EVIDENCE_UNKNOWN
    directory_length = int(stream_count) * _MINIDUMP_DIRECTORY_BYTES
    directory_end = int(directory_rva) + directory_length
    if ((stream_count and directory_rva < _MINIDUMP_HEADER_BYTES) or
            directory_end < int(directory_rva) or
            directory_end > int(length)):
        return MINIDUMP_EVIDENCE_UNKNOWN
    try:
        stream.seek(int(directory_rva))
        directory = stream.read(directory_length)
    except (IOError, OSError, ValueError):
        return MINIDUMP_EVIDENCE_UNKNOWN
    if len(directory) != directory_length:
        return MINIDUMP_EVIDENCE_UNKNOWN
    has_exception = False
    for offset in range(0, directory_length, _MINIDUMP_DIRECTORY_BYTES):
        try:
            stream_type, data_size, data_rva = struct.unpack_from(
                "<III", directory, offset)
        except struct.error:
            return MINIDUMP_EVIDENCE_UNKNOWN
        if data_size and (
                data_rva < _MINIDUMP_HEADER_BYTES or
                data_rva > int(length) or
                data_size > int(length) - data_rva):
            return MINIDUMP_EVIDENCE_UNKNOWN
        if stream_type == _MINIDUMP_EXCEPTION_STREAM:
            if not data_size:
                return MINIDUMP_EVIDENCE_UNKNOWN
            has_exception = True
    return (MINIDUMP_EVIDENCE_EXCEPTION if has_exception
            else MINIDUMP_EVIDENCE_TERMINATION)


def minidump_evidence(session, role):
    """Classify a current session dump without trusting its filename alone."""
    if (not isinstance(session, dict) or not _is_latest(session) or
            role not in DUMP_ROLES):
        return MINIDUMP_EVIDENCE_UNKNOWN
    try:
        layout = _recorded_dump_layout(session)
        directory, paths = layout
        if not _safe_dump_directory(directory, create=False):
            return MINIDUMP_EVIDENCE_UNKNOWN
        exists = os.path.lexists(paths[role])
        opened = _open_recorded_dump(session, role)
    except (core.LauncherError, IOError, OSError):
        return MINIDUMP_EVIDENCE_UNKNOWN
    if opened is None:
        return (MINIDUMP_EVIDENCE_UNKNOWN if exists
                else MINIDUMP_EVIDENCE_ABSENT)
    stream, length = opened
    try:
        return _read_minidump_evidence(stream, length)
    except Exception:
        return MINIDUMP_EVIDENCE_UNKNOWN
    finally:
        stream.close()


def _client_exit_tail(session, role):
    if role not in DUMP_ROLES:
        return None
    sources = session.get("sources")
    if not isinstance(sources, dict):
        return None
    source = sources.get(role)
    if not isinstance(source, dict):
        return None
    try:
        opened = _open_valid_source(session, role, source)
    except Exception:
        return None
    if opened is None:
        return None
    stream, length = opened
    try:
        tail_length = min(int(length), _VISIBLE_CLIENT_EXIT_TAIL_BYTES)
        stream.seek(int(length) - tail_length, os.SEEK_CUR)
        payload = stream.read(tail_length)
        if len(payload) != tail_length:
            return None
        payload = payload.rstrip(b"\r\n\t ")
        if payload.endswith(_VISIBLE_CLIENT_CLEAN_EXIT_SUFFIX):
            return "clean-shutdown"
        if payload.endswith(_VISIBLE_CLIENT_LOBBY_RESTORED_SUFFIX):
            return "lobby-restored"
        return None
    except (IOError, OSError, ValueError):
        return None
    finally:
        stream.close()


def client_exit_evidence(session, role):
    """Classify one current-session game client's shutdown evidence."""
    if (not isinstance(session, dict) or not _is_latest(session) or
            role not in DUMP_ROLES):
        return VISIBLE_CLIENT_EXIT_UNKNOWN
    dump_evidence = minidump_evidence(session, role)
    if dump_evidence == MINIDUMP_EVIDENCE_EXCEPTION:
        return VISIBLE_CLIENT_EXIT_EXCEPTION
    if dump_evidence == MINIDUMP_EVIDENCE_UNKNOWN:
        return VISIBLE_CLIENT_EXIT_UNKNOWN
    exit_tail = _client_exit_tail(session, role)
    if exit_tail == "clean-shutdown":
        return VISIBLE_CLIENT_EXIT_CLEAN
    if (exit_tail == "lobby-restored" and
            dump_evidence == MINIDUMP_EVIDENCE_TERMINATION):
        return VISIBLE_CLIENT_EXIT_CLEAN
    if dump_evidence == MINIDUMP_EVIDENCE_TERMINATION:
        return VISIBLE_CLIENT_EXIT_TERMINATED
    return VISIBLE_CLIENT_EXIT_UNKNOWN


def client_exited_cleanly(session, role):
    """Return whether one current-session game client exited normally."""
    return client_exit_evidence(session, role) == VISIBLE_CLIENT_EXIT_CLEAN


def visible_client_exit_evidence(session):
    """Classify the visible client's current-session shutdown evidence."""
    return client_exit_evidence(session, ROLE_VISIBLE_CLIENT)


def visible_client_exited_cleanly(session):
    """Return whether current-session evidence proves a clean client exit."""
    return client_exited_cleanly(session, ROLE_VISIBLE_CLIENT)


def _open_valid_dump(session, role):
    if (not session.get("endedAt") or
            role not in session.get("crashRoles", ())):
        return None
    return _open_recorded_dump(session, role)


def _write_slice(archive, archive_name, stream, length):
    remaining = int(length)
    with archive.open(archive_name, "w") as target:
        while remaining:
            payload = stream.read(min(_CHUNK_BYTES, remaining))
            if not payload:
                raise IOError("A diagnostic log changed while it was copied.")
            target.write(payload)
            remaining -= len(payload)


def _prepare_reports_directory():
    directory = reports_directory()
    if not os.path.lexists(directory):
        os.makedirs(directory)
    try:
        value = os.lstat(directory)
    except (IOError, OSError):
        raise core.LauncherError(
            "The error report folder is not a regular directory.")
    if (_is_reparse_point(value) or stat.S_ISLNK(value.st_mode) or
            not stat.S_ISDIR(value.st_mode)):
        raise core.LauncherError(
            "The error report folder is not a regular directory.")
    return directory


def _publish_report(temporary, report_path):
    try:
        descriptor = os.open(
            report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise core.LauncherError(
            "An error report with this name already exists.")
    try:
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, report_path)
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(report_path)
        except (IOError, OSError):
            pass
        raise


def create_report(now=None):
    """Zip only this session's logs and confirmed-crash process dumps."""
    session = _validated_session()
    directory = _prepare_reports_directory()
    now = now or datetime.datetime.now()
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    filename = "wot-error-report-%s-%s.zip" % (
        timestamp, session["id"].rsplit("-", 1)[-1])
    report_path = os.path.join(directory, filename)
    temporary = report_path + ".tmp-" + uuid.uuid4().hex
    included_roles = []
    included_files = []
    try:
        with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED,
                allowZip64=True) as archive:
            for role in _SOURCE_ORDER:
                source = session["sources"].get(role)
                if source is None:
                    continue
                opened = _open_valid_source(session, role, source)
                if opened is None:
                    continue
                stream, length = opened
                try:
                    archive_name = _ARCHIVE_FILENAMES[role]
                    _write_slice(archive, archive_name, stream, length)
                finally:
                    stream.close()
                included_roles.append(role)
                included_files.append(archive_name)
            for role in DUMP_ROLES:
                opened = _open_valid_dump(session, role)
                if opened is None:
                    continue
                stream, length = opened
                try:
                    archive_name = _DUMP_FILENAMES[role]
                    _write_slice(archive, archive_name, stream, length)
                finally:
                    stream.close()
                included_files.append(archive_name)
        if not included_files:
            raise core.LauncherError(
                "The latest game session has not produced any diagnostic "
                "logs yet. No earlier session was included.")
        _publish_report(temporary, report_path)
    except Exception:
        try:
            os.unlink(temporary)
        except (IOError, OSError):
            pass
        raise
    expected = set(session["expectedRoles"])
    included_primary = set(included_roles).intersection(PRIMARY_ROLES)
    return {
        "path": report_path,
        "included": tuple(included_files),
        "missing": tuple(
            _ARCHIVE_FILENAMES[role] for role in PRIMARY_ROLES
            if role in expected and role not in included_primary),
        "notRun": tuple(
            _ARCHIVE_FILENAMES[role] for role in PRIMARY_ROLES
            if role not in expected),
    }


def delete_report(report_path):
    """Delete one exact regular ZIP from the launcher report directory."""
    directory = _prepare_reports_directory()
    report_path = os.path.abspath(report_path)
    if (os.path.normcase(os.path.dirname(report_path)) !=
            os.path.normcase(os.path.abspath(directory))):
        raise core.LauncherError("The error report ZIP path is unsafe.")
    try:
        value = os.lstat(report_path)
    except (IOError, OSError):
        return False
    if (_is_reparse_point(value) or stat.S_ISLNK(value.st_mode) or
            not stat.S_ISREG(value.st_mode)):
        raise core.LauncherError(
            "The error report ZIP is not a regular file.")
    try:
        os.unlink(report_path)
    except (IOError, OSError) as error:
        raise core.LauncherError(
            "The error report ZIP could not be removed: %s" % error)
    return True


def select_in_explorer(report_path, runner=None):
    """Ask Windows Explorer to select the newly created archive."""
    report_path = os.path.abspath(report_path)
    if os.path.islink(report_path) or not os.path.isfile(report_path):
        raise core.LauncherError("The error report ZIP is missing.")
    runner = subprocess.Popen if runner is None else runner
    try:
        return runner(
            ["explorer.exe", "/select,", os.path.normpath(report_path)],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (IOError, OSError) as error:
        raise core.LauncherError(
            "Windows Explorer could not select the error report: %s" % error)
