#!/usr/bin/env python3

import argparse
from collections import Counter
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile


PYTHON_27_MAGIC = b'\x03\xf3\r\n'
ENTRY = 'res/scripts/client/gui/mods/mod_offline_lan_0922.pyc'
SOURCE_ROOT = Path(__file__).resolve().parents[1] / 'src'


def expected_pyc_members(source_root=SOURCE_ROOT):
    """Return the exact adjacent-PYC manifest produced by CPython 2.7."""
    source_root = Path(source_root)
    return {
        path.relative_to(source_root).as_posix() + 'c'
        for path in source_root.rglob('*.py')
    }


def validate(path, expected_members=None):
    path = Path(path)
    if expected_members is None:
        expected_members = expected_pyc_members()
    expected_members = set(expected_members)
    with zipfile.ZipFile(str(path), 'r') as archive:
        member_names = archive.namelist()
        duplicates = sorted(
            name for name, count in Counter(member_names).items()
            if count != 1)
        if duplicates:
            raise ValueError('duplicate archive members: %s' % duplicates)
        names = set(member_names)
        corrupt_member = archive.testzip()
        if corrupt_member is not None:
            raise ValueError('archive CRC failed: %s' % corrupt_member)
        if 'meta.xml' not in names:
            raise ValueError('meta.xml is missing')
        if ENTRY not in names:
            raise ValueError('compiled mod entry is missing')
        for info in archive.infolist():
            if info.compress_type != zipfile.ZIP_STORED:
                raise ValueError(
                    'archive member is not stored: %s' % info.filename)
        missing_directories = set()
        for name in names:
            if name.endswith('/'):
                continue
            parts = name.split('/')[:-1]
            for index in range(1, len(parts) + 1):
                directory = '/'.join(parts[:index]) + '/'
                if directory not in names:
                    missing_directories.add(directory)
        if missing_directories:
            raise ValueError('stored directory members are missing: %s' %
                             sorted(missing_directories))
        unwanted_files = sorted(
            name for name in names
            if (name.endswith(('.py', '.pyo')) or
                '__pycache__/' in name))
        if unwanted_files:
            raise ValueError(
                'release package contains unwanted Python files: %s' %
                unwanted_files)
        actual_members = {
            name for name in names if name.endswith('.pyc')
        }
        if actual_members != expected_members:
            raise ValueError(
                'compiled module manifest mismatch: missing=%s extra=%s' %
                (sorted(expected_members - actual_members),
                 sorted(actual_members - expected_members)))
        root = ET.fromstring(archive.read('meta.xml'))
        mod_id = (root.findtext('id') or '').strip()
        version = (root.findtext('version') or '').strip()
        if mod_id != 'org.peng.offline_lan_0922':
            raise ValueError('unexpected mod id: %r' % mod_id)
        if not version:
            raise ValueError('meta.xml has no version')
        for name in sorted(actual_members):
            if archive.read(name)[:4] != PYTHON_27_MAGIC:
                raise ValueError('non-Python-2.7 bytecode: %s' % name)
    return len(names)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Validate the 0.9.22 wotmod.')
    parser.add_argument('wotmod')
    args = parser.parse_args(argv)
    try:
        count = validate(args.wotmod)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        parser.error(str(error))
    sys.stdout.write('validated %d archive members\n' % count)
    return 0


if __name__ == '__main__':
    sys.exit(main())
