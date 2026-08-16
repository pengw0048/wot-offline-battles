#!/usr/bin/env python3
"""Set the package version in every file that carries it.

A blind text replace once turned "0.9.22", the client this port copies
from, into a version number. Each edit here is anchored to the exact
surrounding text and fails loudly if it does not match.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PATTERN = re.compile(r'^\d+\.\d+\.\d+$')
EDITS = (
    ('tools/validate_wotmod.py', "MOD_VERSION = '%s'"),
    ('meta.xml', '<version>%s</version>'),
    ('build_wotmod.py', "org.peng.offline_2312_battle_%s.wotmod"),
    ('INSTALL.txt', 'org.peng.offline_2312_battle_%s.wotmod'),
    ('INSTALL.txt', 'offline battle %s'),
    ('README.md', 'org.peng.offline_2312_battle_%s.wotmod'),
)


def current():
    text = (ROOT / 'meta.xml').read_text()
    match = re.search(r'<version>(\d+\.\d+\.\d+)</version>', text)
    if match is None:
        raise SystemExit('meta.xml carries no version')
    return match.group(1)


def main(argv):
    if len(argv) != 2 or not PATTERN.match(argv[1]):
        raise SystemExit('usage: set_version.py X.Y.Z')
    new = argv[1]
    old = current()
    if old == new:
        raise SystemExit('already at %s' % new)
    for name, template in EDITS:
        path = ROOT / name
        text = path.read_text()
        needle = template % old
        if needle not in text:
            raise SystemExit('%s does not contain %r' % (name, needle))
        path.write_text(text.replace(needle, template % new))
        print('%s: %s' % (name, template % new))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
