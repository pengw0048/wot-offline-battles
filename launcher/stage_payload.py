#!/usr/bin/env python3
"""Copy the 0.9.22 files the packaged launcher carries."""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys
import zipfile

SERVER_DIR = "servers"
CLIENT_DIR = "client"

PAYLOAD_FILES = {
    "0.9.22": (
        "server/lan_battle_server.py",
        "server/offline_rewards.py",
        "server/server_bot_ai.py",
        "server/vehicle_overlay_store.py",
        "server/windows_server.py",
    ),
}

PAYLOAD_TREES = {
    "0.9.22": ("src/res/scripts/client/gui/mods/offline_lan_0922",),
}

# Client mod trees, as (source directory, path inside the game folder).
CLIENT_TREES = {
    "0.9.22": (("mods", "mods"),
               ("res_mods/0.9.22.0.1", "res_mods/0.9.22.0.1")),
}

CLIENT_FILES = {
    "0.9.22": (("offline_worker_starter.exe",
                 "offline_worker_starter.exe"),),
}

CLIENT_0922_OVERLAY = "WoT-0.9.22-LAN-Client-*"

CLIENT_FILE_SUFFIXES = {
    "0.9.22": (".exe", ".json", ".pyd", ".wotmod", ".xml"),
}


def repository_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def client_source(port_version, source_root=None):
    """Return the directory that holds one port's installable client mod."""
    source_root = source_root or repository_root()
    if port_version != "0.9.22":
        raise ValueError("unsupported client port: %s" % port_version)
    overlays = sorted(glob.glob(os.path.join(
        source_root, "dist", CLIENT_0922_OVERLAY)))
    overlays = [path for path in overlays if os.path.isdir(path)]
    if not overlays:
        return None
    if len(overlays) != 1:
        raise ValueError(
            "multiple 0.9.22 client overlays found; clean dist and rebuild")
    return overlays[0]


def _copy_file(source, target):
    directory = os.path.dirname(target)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    shutil.copy2(source, target)


# Retired one-off tools and local caches stay out.
SKIPPED_CLIENT_PREFIXES = (
    "bw_", "dis_", "fix_", "inject_", "patch_", "remove_", "test_",
)


def _copy_tree(source, target, keep_bytecode=False, suffixes=None):
    written = []
    for directory, directories, names in os.walk(source):
        directories[:] = sorted(
            name for name in directories
            if name != "__pycache__" and not name.startswith(".") and
            not os.path.islink(os.path.join(directory, name)))
        for name in sorted(names):
            if name.endswith(".pyc") and not keep_bytecode:
                continue
            if name.startswith(".") or (suffixes is not None and
                                         not name.lower().endswith(suffixes)):
                continue
            source_path = os.path.join(directory, name)
            if os.path.islink(source_path):
                continue
            target_path = os.path.join(
                target, os.path.relpath(source_path, source))
            _copy_file(source_path, target_path)
            written.append(target_path)
    return written


def stage_servers(target_root, source_root=None):
    source_root = source_root or repository_root()
    written = []
    for port_version, relative_paths in PAYLOAD_FILES.items():
        for relative_path in relative_paths:
            source = os.path.join(source_root, *relative_path.split("/"))
            target = os.path.join(target_root, port_version,
                                  *relative_path.split("/"))
            _copy_file(source, target)
            written.append(target)
    for port_version, relative_dirs in PAYLOAD_TREES.items():
        for relative_dir in relative_dirs:
            source = os.path.join(source_root, *relative_dir.split("/"))
            target = os.path.join(target_root, port_version,
                                  *relative_dir.split("/"))
            written.extend(_copy_tree(source, target, suffixes=(".py",)))
    return written


def stage_clients(target_root, source_root=None, client_0922=None):
    """Write one archive per port, with members relative to the game folder."""
    source_root = source_root or repository_root()
    if not os.path.isdir(target_root):
        os.makedirs(target_root)
    written = []
    for port_version, trees in CLIENT_TREES.items():
        if port_version == "0.9.22" and client_0922 is not None:
            port_source = client_0922
        else:
            port_source = client_source(port_version, source_root)
        if port_source is None or not os.path.isdir(port_source):
            raise ValueError(
                "no installable client mod for %s; build it first" %
                port_version)
        archive_path = os.path.join(target_root, "%s.zip" % port_version)
        archive = zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED)
        try:
            for source_relative, target_relative in trees:
                source = os.path.join(port_source, *source_relative.split("/"))
                if not os.path.isdir(source):
                    raise ValueError("client mod is incomplete: %s/%s" %
                                     (port_version, source_relative))
                for directory, directories, names in os.walk(source):
                    directories[:] = sorted(
                        name for name in directories
                        if name != "__pycache__" and not name.startswith(".")
                        and not os.path.islink(os.path.join(directory, name)))
                    for name in sorted(names):
                        if (name.startswith(SKIPPED_CLIENT_PREFIXES) or
                                name.startswith(".") or
                                not name.lower().endswith(
                                    CLIENT_FILE_SUFFIXES[port_version])):
                            continue
                        source_path = os.path.join(directory, name)
                        if os.path.islink(source_path):
                            continue
                        member = "/".join(
                            [target_relative] +
                            os.path.relpath(
                                source_path, source).split(os.path.sep))
                        archive.write(source_path, member)
            for source_relative, target_relative in CLIENT_FILES[port_version]:
                source_path = os.path.join(
                    port_source, *source_relative.split("/"))
                if not os.path.isfile(source_path):
                    raise ValueError("client mod is incomplete: %s/%s" %
                                     (port_version, source_relative))
                archive.write(source_path, target_relative)
        finally:
            archive.close()
        _validate_client_archive(archive_path, port_version)
        written.append(archive_path)
    return written


def _validate_client_archive(path, port_version):
    """Apply the launcher's install-time whitelist to every staged client."""
    import core

    validation_root = os.path.join(
        os.path.dirname(path), ".payload-validation-root")
    archive = zipfile.ZipFile(path)
    try:
        try:
            core._validate_archive(
                archive, validation_root, port_version,
                core._CLIENT_INSTALL[port_version])
        except core.LauncherError as error:
            raise ValueError(str(error))
    finally:
        archive.close()


def stage(target_root, source_root=None, include_clients=True,
          client_0922=None):
    """Write the complete payload under target_root and return every file."""
    if os.path.isdir(target_root):
        shutil.rmtree(target_root)
    written = stage_servers(os.path.join(target_root, SERVER_DIR), source_root)
    if include_clients:
        written.extend(stage_clients(
            os.path.join(target_root, CLIENT_DIR), source_root, client_0922))
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True,
                        help="directory that receives the payload")
    parser.add_argument("--source", default=None,
                        help="repository root (default: this checkout)")
    parser.add_argument("--client-0922", default=None,
                        help="0.9.22 client overlay directory that holds mods")
    parser.add_argument("--servers-only", action="store_true",
                        help="stage the LAN servers without the client mods")
    arguments = parser.parse_args(argv)
    try:
        written = stage(arguments.output, arguments.source,
                        include_clients=not arguments.servers_only,
                        client_0922=arguments.client_0922)
    except ValueError as error:
        sys.stderr.write("%s\n" % error)
        return 1
    print("Staged %d payload files in %s" % (len(written), arguments.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
