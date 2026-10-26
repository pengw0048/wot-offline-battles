#!/usr/bin/env python3
"""Mount owned WoT packages through BigWorld's existing ZIP filesystem."""

import argparse
from pathlib import Path


ORIGINAL = '''if ( BWResource::getExtension( pakPath ) == PACKED_EXTENSION &&
'''
REPLACEMENT = '''// WoT's .pkg files use the same ZIP container. Keep their authored
		// search order and read the installed resources without copying them.
		if ( (BWResource::getExtension( pakPath ) == PACKED_EXTENSION ||
			BWResource::getExtension( pakPath ) == "pkg") &&
'''


def patch(engine):
    path = engine / 'lib/resmgr/bwresource.cpp'
    source = path.read_text(encoding='utf-8')
    if REPLACEMENT in source:
        return
    if source.count(ORIGINAL) != 1:
        raise ValueError('pinned BWResource package dispatch has changed')
    path.write_text(source.replace(ORIGINAL, REPLACEMENT), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine', type=Path)
    patch(parser.parse_args().engine)
