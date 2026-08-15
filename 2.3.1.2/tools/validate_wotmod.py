#!/usr/bin/env python3

from collections import Counter
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile


PYTHON_27_MAGIC = b'\x03\xf3\r\n'
MOD_ID = 'org.peng.offline_2312_poc'
MOD_VERSION = '0.1.1'
ENTRY = 'res/scripts/client/gui/mods/mod_offline_2312_poc.pyc'
EXPECTED_PYC = {
    ENTRY,
}


def validate(path):
    path = Path(path)
    with zipfile.ZipFile(path, 'r') as archive:
        names = archive.namelist()
        duplicates = sorted(
            name for name, count in Counter(names).items() if count != 1)
        if duplicates:
            raise ValueError('duplicate archive members: %s' % duplicates)

        name_set = set(names)
        if 'meta.xml' not in name_set:
            raise ValueError('meta.xml is missing')
        actual_pyc = {name for name in names if name.endswith('.pyc')}
        if actual_pyc != EXPECTED_PYC:
            raise ValueError(
                'compiled module manifest mismatch: missing=%s extra=%s' %
                (sorted(EXPECTED_PYC - actual_pyc),
                 sorted(actual_pyc - EXPECTED_PYC)))
        unwanted = sorted(
            name for name in names
            if name.endswith(('.py', '.pyo')) or '__pycache__/' in name)
        if unwanted:
            raise ValueError('source files found in release: %s' % unwanted)

        for info in archive.infolist():
            if info.compress_type != zipfile.ZIP_STORED:
                raise ValueError('archive member is compressed: %s' %
                                 info.filename)
            if info.date_time != (1980, 1, 1, 0, 0, 0):
                raise ValueError('archive member timestamp is not fixed: %s' %
                                 info.filename)
            if info.create_system != 0:
                raise ValueError('archive member is not DOS-compatible: %s' %
                                 info.filename)
            expected_attr = 16 if info.is_dir() else 32
            if info.external_attr != expected_attr:
                raise ValueError(
                    'archive member has unexpected DOS attributes: %s' %
                    info.filename)

        root = ET.fromstring(archive.read('meta.xml'))
        if (root.findtext('id') or '').strip() != MOD_ID:
            raise ValueError('unexpected mod id')
        if (root.findtext('version') or '').strip() != MOD_VERSION:
            raise ValueError('unexpected mod version')
        for name in sorted(actual_pyc):
            if archive.read(name)[:4] != PYTHON_27_MAGIC:
                raise ValueError('non-CPython-2.7 bytecode: %s' % name)
    return len(names)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        sys.stderr.write('usage: validate_wotmod.py <wotmod>\n')
        return 2
    try:
        count = validate(argv[0])
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        sys.stderr.write('validation failed: %s\n' % error)
        return 1
    sys.stdout.write('validated %d archive members\n' % count)
    return 0


if __name__ == '__main__':
    sys.exit(main())
