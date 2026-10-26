#!/usr/bin/env python3
"""Expose the existing application preference filename to stock Python."""

import argparse
from pathlib import Path


def patch_engine(root):
    path = Path(root) / 'client/device_app.hpp'
    source = path.read_text()
    declaration = '\tBW::string preferencesFilePath() const;'
    if declaration in source:
        return
    anchor = '\tbool savePreferences();'
    if source.count(anchor) != 1:
        raise ValueError('Pinned DeviceApp preferences owner anchor changed')
    path.write_text(source.replace(anchor, anchor + '\n' + declaration, 1))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root', type=Path)
    patch_engine(parser.parse_args().engine_root)
