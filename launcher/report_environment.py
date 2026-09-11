"""Machine, install and crash facts the eight 2026-09-09/10 reports lacked.

Every entry here exists because one of those reports could not be diagnosed
without it:

* ``environment.txt`` - report 20260909-234646 contained only a launcher log
  and a server log; nothing said what kind of machine that was, and its
  ``C:\\users\\xuser`` path is the only reason Wine was ever suspected.
* ``installed-mods.txt`` - blame for the address-space growth turned on
  whether one worker was really mod-free, and the only evidence was the
  client's own ``Mod file`` lines, which name packaged wotmods and nothing
  else.
* ``missing-dependencies.txt`` - report 20260909-205956 exited with
  ``0xC0000135`` without naming a DLL. A static import-file inventory can
  identify candidates, but cannot replace the Windows loader's diagnosis.
* ``crash-text.txt`` - report 20260909-232240 carried a 1.27 GB dump whose
  entire diagnostic value was one sentence BigWorld had already written into
  its own stack: ``FATAL ERROR: The device has been removed.``

Everything is best effort.  A failure here must never stop a report from being
written, so each section catches its own errors and says what it could not
read.
"""

from __future__ import annotations

import os
import platform
import struct
import sys

try:
    from . import core
except ImportError:
    import core


MODS_LISTING_LIMIT = 400
# Directories under `mods/` that this launcher owns.  Older builds may leave
# hundreds of payload files in `mods/configs/offline_lan_0922/worker_res`.
# Summarise those leftovers instead of spending the listing budget on them
# before reaching the player's third-party mods.
OWN_MOD_DIRECTORIES = ("configs/offline_lan_0922",)
# BigWorld writes its own crash banner onto the faulting thread's stack:
# "Application <exe> crashed <date> at <time> / Message: / FATAL ERROR: ...".
# The same words also appear in the module image as printf templates, so a
# candidate that still holds a format specifier or a build path is the
# template, not the event.
CRASH_TEXT_MARKERS = (
    b" crashed ",
    b"EXCEPTION_ACCESS_VIOLATION",
    b"Memory status:",
)
CRASH_TEXT_REJECT = (b"%s", b"%d", b"%ll", b"%3.", b"%02",
                     b"buildagent", b"BuildAgent")
CRASH_TEXT_REQUIRE = (b"FATAL ERROR", b"Memory status", b"EXCEPTION_")
CRASH_TEXT_BEFORE = 512
CRASH_TEXT_WINDOW = 1536
CRASH_TEXT_LIMIT = 8192
_CHUNK = 1 << 22


def _lines(title):
    return ["== %s" % title]


def _memory_status():
    """Return the Windows memory picture, or None off Windows."""
    import ctypes

    class _Status(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        kernel32 = ctypes.windll.kernel32
    except (AttributeError, OSError):
        return None
    status = _Status()
    status.dwLength = ctypes.sizeof(status)
    try:
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
    except (AttributeError, OSError):
        return None
    return {
        "load": int(status.dwMemoryLoad),
        "totalPhys": int(status.ullTotalPhys),
        "availPhys": int(status.ullAvailPhys),
        "totalPageFile": int(status.ullTotalPageFile),
        "availPageFile": int(status.ullAvailPageFile),
        # The launcher is 64-bit, so its own total is not the client's.  It is
        # still the number that says whether this machine gives a 32-bit
        # process 2 GB or the large-address-aware 4 GB.
        "totalVirtual": int(status.ullTotalVirtual),
        "availVirtual": int(status.ullAvailVirtual),
    }


def _wine_version():
    """Name Wine when the client is not really running on Windows.

    ``C:\\users\\<name>`` instead of ``C:\\Users\\<name>`` was the only hint
    that report 20260909-234646 came from a Wine-shaped environment, and a
    hint is not a diagnosis.  Wine exports this from ntdll and Windows does
    not.
    """
    import ctypes

    try:
        ntdll = ctypes.windll.ntdll
        getter = ntdll.wine_get_version
    except (AttributeError, OSError):
        return None
    try:
        getter.restype = ctypes.c_char_p
        value = getter()
    except (AttributeError, OSError, ValueError):
        return "present, version unavailable"
    if isinstance(value, bytes):
        return value.decode("ascii", "replace")
    return str(value)


def _megabytes(value):
    return "%.0f MB" % (float(value) / (1024.0 * 1024.0))


def _disk_free(path):
    try:
        usage = os.statvfs(path)  # type: ignore[attr-defined]
        return usage.f_bavail * usage.f_frsize
    except (AttributeError, OSError):
        pass
    import ctypes

    try:
        free = ctypes.c_ulonglong(0)
        if not ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                ctypes.c_wchar_p(path), ctypes.byref(free), None, None):
            return None
    except (AttributeError, OSError, ValueError):
        return None
    return int(free.value)


# IMAGE_FILE_LARGE_ADDRESS_AWARE.  #1513 sets it (COFF characteristics
# 0x0123), which is why a crashed worker can hold 3.4 GiB of private commit.
# Read it rather than assume it: a repacked or patched executable that lost the
# flag would halve the ceiling and change every memory conclusion.
LARGE_ADDRESS_AWARE = 0x0020


def _address_space(exe_path):
    """Say whether this exact client executable can use more than 2 GB."""
    try:
        with open(exe_path, "rb") as handle:
            head = handle.read(0x400)
        offset = struct.unpack_from("<I", head, 0x3C)[0]
        if head[offset:offset + 4] != b"PE\0\0":
            return "unreadable (not a PE image)"
        characteristics = struct.unpack_from("<H", head, offset + 22)[0]
    except (OSError, struct.error, IndexError) as error:
        return "unreadable (%s)" % error
    if characteristics & LARGE_ADDRESS_AWARE:
        return ("4 GB on 64-bit Windows (LARGE_ADDRESS_AWARE, "
                "characteristics 0x%04X)" % characteristics)
    return ("2 GB - NOT large-address-aware (characteristics 0x%04X)"
            % characteristics)


def environment_report(game_root, session=None):
    """Describe the machine and the install this session ran on."""
    lines = _lines("launcher")
    try:
        identity = core.bundled_payload_identity(core.PORT_0_9_22)
    except Exception:
        identity = None
    lines.append("bundled payload: %s" % core.payload_identity_text(identity))
    try:
        installed = core.installed_payload_identity(
            game_root, core.PORT_0_9_22)
    except Exception:
        installed = None
    lines.append("installed payload: %s" % core.payload_identity_text(
        installed))
    lines.append("python: %s" % sys.version.split()[0])
    if session:
        lines.append("session: %s" % session.get("id", "unknown"))
        lines.append("started: %s" % session.get("startedAt", "unknown"))

    lines.extend(_lines("machine"))
    try:
        lines.append("platform: %s" % platform.platform())
        lines.append("machine: %s processor=%s" % (
            platform.machine(), platform.processor() or "unknown"))
        lines.append("python arch: %d-bit" % (struct.calcsize("P") * 8))
        lines.append("cpu count: %s" % (os.cpu_count() or "unknown"))
    except Exception as error:
        lines.append("platform read failed: %s" % error)
    wine = None
    try:
        wine = _wine_version()
    except Exception:
        wine = None
    lines.append("wine: %s" % (wine if wine else "not detected"))
    try:
        status = _memory_status()
    except Exception:
        status = None
    if status is None:
        lines.append("memory: unavailable")
    else:
        lines.append(
            "memory: load=%d%% phys=%s/%s pagefile=%s/%s (available/total)" % (
                status["load"], _megabytes(status["availPhys"]),
                _megabytes(status["totalPhys"]),
                _megabytes(status["availPageFile"]),
                _megabytes(status["totalPageFile"])))
        lines.append("virtual (this launcher process): %s/%s" % (
            _megabytes(status["availVirtual"]),
            _megabytes(status["totalVirtual"])))

    lines.extend(_lines("game"))
    lines.append("root: %s" % game_root)
    try:
        client = core.read_client_identity(game_root)
    except Exception:
        client = None
    lines.append("version.xml: %s" % (
        "v.%s #%s" % client if client else "unreadable"))
    for name in ("paths.xml", core.GAME_EXECUTABLE):
        path = os.path.join(game_root, name)
        try:
            lines.append("%s: %d bytes" % (name, os.path.getsize(path)))
        except OSError as error:
            lines.append("%s: %s" % (name, error))
    lines.append("client address space: %s" % _address_space(
        os.path.join(game_root, core.GAME_EXECUTABLE)))
    free = None
    try:
        free = _disk_free(game_root)
    except Exception:
        free = None
    lines.append("free disk on the game drive: %s" % (
        _megabytes(free) if free is not None else "unavailable"))

    lines.extend(_lines("hidden worker resources"))
    lines.append("resource list: client paths.xml")
    return "\n".join(lines) + "\n"


def _listing(root, limit, skip=()):
    """List files under root, summarising only owned relative directories."""
    rows = []
    truncated = False
    summarised = []
    for base, directories, files in os.walk(root):
        for name in sorted(directories):
            relative = os.path.relpath(
                os.path.join(base, name), root).replace("\\", "/")
            if relative.lower() in skip:
                directories.remove(name)
                summarised.append(relative)
        directories.sort()
        for name in sorted(files):
            if len(rows) >= limit:
                truncated = True
                return rows + _summary_rows(root, summarised), truncated
            path = os.path.join(base, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = -1
            rows.append("%12d  %s" % (
                size, os.path.relpath(path, root).replace("\\", "/")))
    return rows + _summary_rows(root, summarised), truncated


def _summary_rows(root, names):
    """One counted line per launcher-owned directory, instead of its files."""
    rows = []
    for name in names:
        total = 0
        try:
            for unused_base, unused_dirs, files in os.walk(
                    os.path.join(root, name)):
                total += len(files)
        except OSError:
            total = -1
        rows.append("%12s  %s/  (%s files, installed by this launcher)" % (
            "-", name, total if total >= 0 else "unreadable"))
    return rows


def installed_mods_report(game_root):
    """List every mod file present, not only the ones the client announces.

    The client's own log names packaged ``.wotmod`` archives.  A loose script
    tree under ``res_mods`` announces nothing, so 'that worker was mod-free'
    could only ever be an inference.
    """
    lines = []
    for relative in ("mods", "res_mods"):
        root = os.path.join(game_root, relative)
        lines.extend(_lines(relative))
        if not os.path.isdir(root):
            lines.append("(absent)")
            continue
        try:
            rows, truncated = _listing(
                root, MODS_LISTING_LIMIT,
                skip=OWN_MOD_DIRECTORIES if relative == "mods" else ())
        except OSError as error:
            lines.append("unreadable: %s" % error)
            continue
        if not rows:
            lines.append("(empty)")
        lines.extend(rows)
        if truncated:
            lines.append("... more than %d files; listing truncated" %
                         MODS_LISTING_LIMIT)
    return "\n".join(lines) + "\n"


def _import_table(path):
    """Return the load-time DLL imports of one PE image."""
    with open(path, "rb") as stream:
        data = stream.read()
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise ValueError("not a PE image")
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    if data[offset:offset + 4] != b"PE\0\0":
        raise ValueError("not a PE image")
    coff = offset + 4
    sections = struct.unpack_from("<H", data, coff + 2)[0]
    optional_size = struct.unpack_from("<H", data, coff + 16)[0]
    optional = coff + 20
    magic = struct.unpack_from("<H", data, optional)[0]
    if magic != 0x10B:
        raise ValueError("not a 32-bit PE image")
    base = struct.unpack_from("<I", data, optional + 28)[0]
    table = []
    for index in range(sections):
        entry = optional + optional_size + index * 40
        virtual_address = struct.unpack_from("<I", data, entry + 12)[0]
        raw_size = struct.unpack_from("<I", data, entry + 16)[0]
        raw_pointer = struct.unpack_from("<I", data, entry + 20)[0]
        table.append((virtual_address, raw_size, raw_pointer))

    def read(address, length):
        rva = address - base
        for virtual_address, raw_size, raw_pointer in table:
            if virtual_address <= rva < virtual_address + raw_size:
                start = raw_pointer + (rva - virtual_address)
                return data[start:start + length]
        return b""

    directory = optional + (96 if magic == 0x10B else 112)
    import_rva = struct.unpack_from("<I", data, directory + 8)[0]
    if not import_rva:
        return []
    names = []
    index = 0
    while len(names) < 256:
        entry = read(base + import_rva + index * 20, 20)
        if len(entry) < 20 or entry == b"\0" * 20:
            break
        name_rva = struct.unpack_from("<I", entry, 12)[0]
        if not name_rva:
            break
        raw = read(base + name_rva, 64).split(b"\0")[0]
        if not raw:
            break
        names.append(raw.decode("ascii", "replace"))
        index += 1
    return names


def _search_directories(game_root):
    directories = [game_root]
    windows = os.environ.get("SystemRoot") or "C:\\Windows"
    # The client is 32-bit, so on 64-bit Windows its system DLLs live in
    # SysWOW64; on 32-bit Windows they live in System32.
    for leaf in ("SysWOW64", "System32", "system"):
        directories.append(os.path.join(windows, leaf))
    directories.append(windows)
    return directories


def missing_dependencies_report(game_root):
    """Inventory ordinary import files in a bounded set of directories.

    API-set names are virtual contracts and need not name a file on disk.
    This scan neither loads DLLs nor determines their runtime resolution.
    """
    executable = os.path.join(game_root, core.GAME_EXECUTABLE)
    lines = _lines("load-time imports of %s" % core.GAME_EXECUTABLE)
    try:
        names = _import_table(executable)
    except (IOError, OSError, ValueError, struct.error) as error:
        return "\n".join(lines + ["unreadable: %s" % error]) + "\n"
    if not names:
        return "\n".join(lines + ["no import table"]) + "\n"
    directories = _search_directories(game_root)
    missing = []
    found_count = 0
    contracts = 0
    for name in sorted(names, key=str.lower):
        if name.lower().startswith(("api-ms-", "ext-ms-")):
            contracts += 1
            lines.append(
                "API-SET  %s  (virtual contract; loader resolution not checked)"
                % name)
            continue
        found = None
        for directory in directories:
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate):
                found = directory
                break
        if found is None:
            missing.append(name)
            lines.append("MISSING  %s" % name)
        else:
            found_count += 1
            lines.append("found    %s  (%s)" % (name, found))
    lines.extend(_lines("verdict"))
    lines.append(
        "physical import files: found=%d not_found=%d (scanned directories)"
        % (found_count, len(missing)))
    lines.append("virtual API-set contracts: %d (loader resolution not checked)"
                 % contracts)
    lines.append(
        "Missing files are candidates only; file presence does not prove "
        "loadability. This static scan cannot identify the cause of "
        "0xC0000135. API-set mapping, other loader search rules and transitive "
        "dependencies are not checked.")
    return "\n".join(lines) + "\n"


def _crash_candidate(window, index):
    text = window[max(0, index - CRASH_TEXT_BEFORE):index + CRASH_TEXT_WINDOW]
    # The banner is one NUL-terminated string; keep the piece the marker fell
    # in and nothing around it.
    text = text.split(b"\0")
    for piece in text:
        if window[index:index + 8] in piece:
            text = piece
            break
    else:
        return None
    if any(token in text for token in CRASH_TEXT_REJECT):
        return None
    if not any(token in text for token in CRASH_TEXT_REQUIRE):
        return None
    rendered = "".join(
        chr(byte) if 32 <= byte < 127 or byte in (9, 10, 13) else " "
        for byte in text).strip()
    return rendered or None


class CrashTextScanner(object):
    """Collect BigWorld's crash banner from a dump as its bytes go past.

    The whole diagnostic value of the 1.27 GB dump in report 20260909-232240
    was one sentence the client had already written onto its stack.  Capturing
    it means the next report carries the sentence whether or not the player
    uploads the dump at all.

    This is a scanner rather than a reader so that ``create_report`` can feed
    it the chunks it is already copying into the ZIP.  A dump that size is not
    worth a second pass, and the copy has already validated the file.
    """

    def __init__(self, limit=CRASH_TEXT_LIMIT):
        self.limit = limit
        self._found = []
        self._total = 0
        self._tail = b""

    def feed(self, chunk):
        """Offer the next bytes of the dump. Never raises."""
        if self._total >= self.limit or not chunk:
            return
        try:
            window = self._tail + chunk
            for marker in CRASH_TEXT_MARKERS:
                start = 0
                while self._total < self.limit:
                    index = window.find(marker, start)
                    if index < 0:
                        break
                    start = index + 1
                    rendered = _crash_candidate(window, index)
                    if rendered and rendered not in self._found:
                        self._found.append(rendered)
                        self._total += len(rendered)
            self._tail = window[-(CRASH_TEXT_BEFORE + CRASH_TEXT_WINDOW):]
        except Exception:
            pass

    def result(self):
        """Return the banner text, or None when the dump held none.

        A chunk boundary inside the banner yields a truncated render from one
        window and the whole one from the next, so drop any candidate that is
        contained in another.  The report should carry the crash once.
        """
        if not self._found:
            return None
        kept = [text for text in self._found
                if not any(text != other and text in other
                           for other in self._found)]
        return "\n\n".join(kept or self._found)


def crash_text(path, limit=CRASH_TEXT_LIMIT):
    """Extract BigWorld's crash banner from a dump named by path."""
    scanner = CrashTextScanner(limit)
    try:
        with open(path, "rb") as stream:
            while True:
                chunk = stream.read(_CHUNK)
                if not chunk:
                    break
                scanner.feed(chunk)
    except (IOError, OSError):
        return None
    return scanner.result()
