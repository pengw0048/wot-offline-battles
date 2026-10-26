#!/usr/bin/env python3
"""Connect compound reflection priority to the public renderer's priority path."""

import argparse
from pathlib import Path


def patch_engine(root):
    root = Path(root)
    patches = {
        root / 'lib' / 'duplo' / 'pymodel.hpp': [(
            '\tbool visible() const\t\t\t{ return visible_; }',
            '\tbool highPriorityReflection() const { return highPriorityReflection_; }\n'
            '\tvoid highPriorityReflection( bool value ) { highPriorityReflection_ = value; }\n'
            '\tbool visible() const\t\t\t{ return visible_; }'), (
            '\tbool\t\t\t\tvisible_;',
            '\tbool\t\t\t\tvisible_;\n\tbool highPriorityReflection_;')],
        root / 'lib' / 'duplo' / 'pymodel.cpp': [(
            ' visible_( true ),',
            ' visible_( true ),\n highPriorityReflection_( false ),')],
        root / 'lib' / 'romp' / 'water_scene_renderer.cpp': [(
            'else if (playerModel() == model)',
            '// Compound priority uses the public renderer\'s existing player\n'
            '    // reflection policy. This is an SDK adaptation of the stock flag.\n'
            '    else if (playerModel() == model || model->highPriorityReflection())')],
    }
    updates = []
    for path, replacements in patches.items():
        source = path.read_text(encoding='utf-8')
        for before, after in replacements:
            if source.count(after) == 1:
                continue
            if source.count(before) != 1:
                raise ValueError('Unrecognized pinned reflection seam: ' + str(path))
            source = source.replace(before, after)
        updates.append((path, source))
    for path, source in updates:
        path.write_text(source, encoding='utf-8')
    print('Compound priority connected to the public reflection renderer')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine_root', type=Path)
    patch_engine(parser.parse_args().engine_root)
