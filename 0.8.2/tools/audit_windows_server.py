#!/usr/bin/env python3
"""Prove that the packaged Windows server carries every navigation graph."""

from __future__ import annotations

import argparse
from pathlib import Path
import struct
import sys


VERSION_ROOT = Path(__file__).resolve().parents[1]
NAVGRAPH_DIR = VERSION_ROOT / "scripts/client/gui/mods/offhangar/navgraphs"
ARCHIVE_PREFIX = "scripts\\client\\gui\\mods\\offhangar\\navgraphs\\"
COOKIE = b"MEI\014\013\012\013\016"


def _archive_names(path: Path) -> list[str]:
    data = path.read_bytes()
    start = data.rfind(COOKIE)
    if start < 0:
        raise SystemExit("%s has no PyInstaller archive" % path)
    package_length, toc_offset, toc_length, _ = struct.unpack(
        "!IIII", data[start + 8:start + 24])
    package_start = start + 8 + 16 + 64 - package_length
    toc = data[package_start + toc_offset:package_start + toc_offset + toc_length]
    names = []
    cursor = 0
    while cursor < len(toc):
        entry_length, = struct.unpack("!i", toc[cursor:cursor + 4])
        if entry_length <= 0:
            raise SystemExit("corrupt archive table of contents in %s" % path)
        names.append(
            toc[cursor + 18:cursor + entry_length].rstrip(b"\x00").decode())
        cursor += entry_length
    return names


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    executable = parser.parse_args().executable
    expected = {path.name for path in NAVGRAPH_DIR.glob("*.json")}
    if not expected:
        raise SystemExit("no navigation graphs found in %s" % NAVGRAPH_DIR)
    bundled = {
        name[len(ARCHIVE_PREFIX):] for name in _archive_names(executable)
        if name.startswith(ARCHIVE_PREFIX)
    }
    missing = sorted(expected - bundled)
    if missing:
        raise SystemExit("packaged server is missing %d graph files: %s" % (
            len(missing), ", ".join(missing)))
    print("Audited %s: %d navigation files bundled." % (
        executable, len(bundled)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
