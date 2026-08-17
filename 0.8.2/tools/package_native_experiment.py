#!/usr/bin/env python3
"""Build and audit one clean native-physics replacement package."""

from __future__ import annotations

import argparse
import ast
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile


VERSION_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = VERSION_ROOT.parent
VERSION_TOP_LEVEL_FILES = (
    "INSTALL.txt",
    "INSTALL_CLIENT.txt",
    "LAN_SERVER.md",
    "PERFORMANCE_TEST.txt",
    "README.md",
    "RUN_SERVER.bat",
    "RUN_SERVER.command",
    "START_HERE.txt",
    "START_NATIVE_TEST_HERE.txt",
    "VERSION.txt",
    "lan_battle_server.py",
    "server_bot_ai.py",
    "server_bot_navigation.py",
)
SHARED_TOP_LEVEL_FILES = ("LICENSE", "THIRD_PARTY_NOTICES.md")
TOP_LEVEL_FILES = VERSION_TOP_LEVEL_FILES + SHARED_TOP_LEVEL_FILES
PAYLOAD_PREFIXES = ("gui/", "scripts/")
REQUIRED_PYC = {
    "0.8.2/scripts/client/CameraNode.pyc",
    "0.8.2/scripts/client/OfflineEntity.pyc",
}
EXPECTED_TOP_LEVEL = set(TOP_LEVEL_FILES) | {
    "0.8.2", "SHA256SUMS.txt", "licenses"
}


def _tracked_version_files(*prefixes: str) -> list[str]:
    version_prefix = VERSION_ROOT.relative_to(PROJECT_ROOT).as_posix() + "/"
    project_prefixes = [version_prefix + prefix for prefix in prefixes]
    output = subprocess.check_output(
        ["git", "ls-files", "--", *project_prefixes],
        cwd=str(PROJECT_ROOT),
        text=True,
    )
    tracked = []
    for line in output.splitlines():
        if not line:
            continue
        if not line.startswith(version_prefix):
            raise RuntimeError("tracked file escaped version root: %s" % line)
        tracked.append(line[len(version_prefix):])
    return tracked


def _tracked_project_files(*prefixes: str) -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "--", *prefixes],
        cwd=str(PROJECT_ROOT),
        text=True,
    )
    return [line for line in output.splitlines() if line]


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _write_checksums(package_root: Path) -> None:
    entries = []
    for path in sorted(package_root.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            entries.append("%s  %s" % (
                _sha256(path), path.relative_to(package_root).as_posix()))
    (package_root / "SHA256SUMS.txt").write_text(
        "\n".join(entries) + "\n", encoding="ascii")


def _audit_entries(names: list[str], wrapper: str) -> None:
    prefix = wrapper + "/"
    if any(not name.startswith(prefix) for name in names):
        raise RuntimeError("ZIP contains an entry outside its wrapper directory")
    relative = [name[len(prefix):] for name in names if name != prefix]
    files = [name for name in relative if not name.endswith("/")]
    top_level = {name.split("/", 1)[0] for name in files}
    if top_level != EXPECTED_TOP_LEVEL:
        raise RuntimeError(
            "unexpected package top level: %r" % sorted(top_level))
    pyc = {name for name in files if name.endswith(".pyc")}
    if pyc != REQUIRED_PYC:
        raise RuntimeError("unexpected bytecode payload: %r" % sorted(pyc))
    forbidden_parts = {
        "__pycache__", "offhangar_user", "ports", "tests", "tools", "native"
    }
    for name in files:
        parts = set(Path(name).parts)
        if parts & forbidden_parts:
            raise RuntimeError("forbidden package entry: %s" % name)
        if name.endswith("mod_offhangar.pyc"):
            raise RuntimeError("stale mod entry bytecode is forbidden")
    required = {
        "0.8.2/scripts/client/gui/mods/mod_offhangar.py",
        "0.8.2/scripts/client/gui/mods/offhangar/offhangar_native_seed.pyd",
        "0.8.2/scripts/client/gui/mods/offhangar/spawn_streaming_bootstrap.py",
        "SHA256SUMS.txt",
        "VERSION.txt",
    }
    missing = required.difference(files)
    if missing:
        raise RuntimeError("package is missing required entries: %r" % sorted(missing))


def _assigned_string(path: Path, name: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line != line.lstrip():
            continue
        target, separator, expression = line.partition("=")
        if separator and target.strip() == name:
            value = ast.literal_eval(expression.strip())
            if isinstance(value, str):
                return value
    raise RuntimeError("%s has no string assignment for %s" % (path, name))


def _audit_identity(version: str) -> None:
    expected = "%s-native-experimental-20260815" % version
    version_text = (VERSION_ROOT / "VERSION.txt").read_text(encoding="utf-8")
    required_lines = (
        "Package: native bot physics experiment %s" % version,
        "LAN protocol: 8",
        "Client build: %s" % expected,
        "Diagnostic client revision: %s" % expected,
    )
    for line in required_lines:
        if line not in version_text.splitlines():
            raise RuntimeError("VERSION.txt identity mismatch: %s" % line)
    server_build = _assigned_string(
        VERSION_ROOT / "lan_battle_server.py", "CLIENT_BUILD")
    client_build = _assigned_string(
        VERSION_ROOT / "scripts/client/gui/mods/offhangar/network_battle.py",
        "CLIENT_BUILD")
    offline_build = _assigned_string(
        VERSION_ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py",
        "_OFFH_BUILD")
    if server_build != expected or client_build != expected:
        raise RuntimeError(
            "client/server build mismatch: %s %s expected %s" % (
                client_build, server_build, expected))
    expected_offline = "%s-native-experimental (2026-08-15)" % version
    if offline_build != expected_offline:
        raise RuntimeError(
            "offline build mismatch: %s expected %s" % (
                offline_build, expected_offline))


def build(output_dir: Path, version: str) -> tuple[Path, Path]:
    _audit_identity(version)
    stem = "WoT-0.8.2-native-bot-physics-experimental-%s-20260815" % version
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / stem
    archive = output_dir / (stem + ".zip")
    if destination.exists() or archive.exists():
        raise RuntimeError("refusing to overwrite an existing package")

    with tempfile.TemporaryDirectory(prefix="offh-native-package-") as temp:
        wrapper = Path(temp) / stem
        payload = wrapper / "0.8.2"
        for relative in _tracked_version_files(*PAYLOAD_PREFIXES):
            _copy(VERSION_ROOT / relative, payload / relative)
        for relative in VERSION_TOP_LEVEL_FILES:
            _copy(VERSION_ROOT / relative, wrapper / relative)
        for relative in SHARED_TOP_LEVEL_FILES:
            _copy(PROJECT_ROOT / relative, wrapper / relative)
        for relative in _tracked_project_files("licenses"):
            _copy(PROJECT_ROOT / relative, wrapper / relative)
        _write_checksums(wrapper)

        shutil.copytree(wrapper, destination)
        with zipfile.ZipFile(
                archive, "w", compression=zipfile.ZIP_DEFLATED,
                compresslevel=9) as zipped:
            for path in sorted(wrapper.rglob("*")):
                if path.is_file():
                    zipped.write(path, (Path(stem) / path.relative_to(wrapper)).as_posix())

    with zipfile.ZipFile(archive) as zipped:
        _audit_entries(zipped.namelist(), stem)
        bad = zipped.testzip()
        if bad is not None:
            raise RuntimeError("ZIP CRC failure: %s" % bad)
    return destination, archive


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path.home() / "Downloads")
    parser.add_argument("--version", default="1.8.59")
    args = parser.parse_args()
    destination, archive = build(args.output_dir.resolve(), args.version)
    print(destination)
    print(archive)
    print("sha256=%s" % _sha256(archive))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
