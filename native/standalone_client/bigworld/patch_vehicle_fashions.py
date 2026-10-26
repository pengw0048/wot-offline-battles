#!/usr/bin/env python3
"""Preserve the original vehicle fashion's null-material primitive skip."""

import argparse
from pathlib import Path


def patch_engine(root):
    path = Path(root) / 'lib' / 'moo' / 'draw_context.cpp'
    source = path.read_text(encoding='utf-8')
    before = '''	const bool canUseHWInstancing = this->hwInstancingAvailable() &&
									immediateModeCounter_ == 0;'''
    after = '''	// Original vehicle fashions hide primitive groups with a null material.
	if (!material)
		return;

''' + before
    if source.count(after) == 1:
        return
    if source.count(before) != 1:
        raise ValueError('Unrecognized pinned null vehicle material seam: ' + str(path))
    path.write_text(source.replace(before, after), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root', type=Path)
    patch_engine(parser.parse_args().engine_root)
