#!/usr/bin/env python3

"""Validate the exact contents of a hidden-worker profiler delta ZIP."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import zipfile


TOOLS_ROOT = Path(__file__).resolve().parent
PORT_ROOT = TOOLS_ROOT.parent
sys.path.insert(0, str(TOOLS_ROOT))
import validate_wotmod


MOD_ID = 'org.peng.offline_lan_0922'
MOD_VERSION = '0.6.7'
MARKER_FILENAME = 'hidden_worker_profiler_build.json'
PACKAGE_MEMBER = (
    'payload/mods/0.9.22.0.1/' + MOD_ID + '_' + MOD_VERSION + '.wotmod')
REQUIRED_FILES = {
    'INSTALL_HIDDEN_WORKER_PROFILER.bat',
    'UNINSTALL_HIDDEN_WORKER_PROFILER.bat',
    'COLLECT_HIDDEN_WORKER_PROFILE.bat',
    'hidden_worker_profiler_package.ps1',
    'collect_hidden_worker_profile.ps1',
    'INSTALL_HIDDEN_WORKER_PROFILER.txt',
    MARKER_FILENAME,
    PACKAGE_MEMBER,
    'LICENSE',
    'THIRD_PARTY_NOTICES.md',
    'licenses/Boost-1.0.txt',
}
FORBIDDEN_STATE_FILES = {
    'server_endpoint.json', 'account_state.json', 'garage_state.json',
    'postbattle_state.json', 'waiting_room_state.json', 'config.json',
    'build_identity.json', 'launcher_install.json',
}
IDENTITY_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$')


def _file_members(archive):
    return {info.filename for info in archive.infolist()
            if not info.filename.endswith('/')}


def _expected_directories(files):
    directories = set()
    for name in files:
        parts = name.split('/')[:-1]
        for index in range(1, len(parts) + 1):
            directories.add('/'.join(parts[:index]) + '/')
    return directories


def validate(path, expected_pyc_members=None):
    path = Path(path)
    with zipfile.ZipFile(str(path), 'r') as archive:
        member_names = archive.namelist()
        duplicates = sorted(
            name for name, count in Counter(member_names).items()
            if count != 1)
        if duplicates:
            raise ValueError('duplicate archive members: %s' % duplicates)
        for info in archive.infolist():
            name = info.filename
            parts = name.rstrip('/').split('/')
            if (not name or '\\' in name or
                    any(not part or part in ('.', '..') for part in parts)):
                raise ValueError('diagnostic ZIP contains an invalid path')
            if info.compress_type != zipfile.ZIP_STORED:
                raise ValueError(
                    'diagnostic ZIP member is not stored: %s' % name)
        corrupt_member = archive.testzip()
        if corrupt_member is not None:
            raise ValueError('archive CRC failed: %s' % corrupt_member)
        files = _file_members(archive)
        if files != REQUIRED_FILES:
            raise ValueError(
                'diagnostic member manifest mismatch: missing=%s extra=%s' %
                (sorted(REQUIRED_FILES - files),
                 sorted(files - REQUIRED_FILES)))
        directories = {
            info.filename for info in archive.infolist()
            if info.filename.endswith('/')}
        if directories != _expected_directories(REQUIRED_FILES):
            raise ValueError('diagnostic directory manifest mismatch')
        forbidden = sorted(
            name for name in files
            if Path(name).name.lower() in FORBIDDEN_STATE_FILES)
        if forbidden:
            raise ValueError(
                'diagnostic ZIP contains user/package state: %s' % forbidden)
        marker = json.loads(archive.read(MARKER_FILENAME).decode('utf-8'))
        package_payload = archive.read(PACKAGE_MEMBER)

    required_marker = {
        'schema', 'diagnostic', 'diagnosticBuildIdentity', 'baseModId',
        'baseSemanticVersion', 'packageFile', 'packageSha256',
        'sourceRevision', 'sourceDirty',
    }
    if (not isinstance(marker, dict) or set(marker) != required_marker or
            marker.get('schema') != 1 or
            marker.get('diagnostic') != 'hidden_worker_profiler' or
            marker.get('baseModId') != MOD_ID or
            marker.get('baseSemanticVersion') != MOD_VERSION or
            marker.get('packageFile') != Path(PACKAGE_MEMBER).name or
            not isinstance(marker.get('sourceDirty'), bool) or
            IDENTITY_PATTERN.fullmatch(
                str(marker.get('diagnosticBuildIdentity') or '')) is None or
            re.fullmatch(r'(?:[0-9a-f]{40}|unknown)',
                         str(marker.get('sourceRevision') or '')) is None or
            re.fullmatch(r'[0-9a-f]{64}',
                         str(marker.get('packageSha256') or '')) is None):
        raise ValueError('diagnostic build marker is invalid')
    actual_digest = hashlib.sha256(package_payload).hexdigest()
    if actual_digest != marker['packageSha256']:
        raise ValueError('diagnostic WOTMOD checksum mismatch')

    handle = tempfile.NamedTemporaryFile(suffix='.wotmod', delete=False)
    temporary = Path(handle.name)
    try:
        handle.write(package_payload)
        handle.close()
        validate_wotmod.validate(
            temporary, expected_members=expected_pyc_members)
    finally:
        try:
            handle.close()
        except Exception:
            pass
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return marker


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive')
    arguments = parser.parse_args(argv)
    try:
        marker = validate(arguments.archive)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        parser.error(str(error))
    print('validated hidden-worker profiler build %s' %
          marker['diagnosticBuildIdentity'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
