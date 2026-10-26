"""Register the original shared decor owner during personality initialization."""
import argparse
from pathlib import Path


def patch(root):
    path = Path(root) / 'client/finale_app.cpp'
    source = path.read_text(encoding='utf-8-sig')
    include = '#include "stock_scene_settings.hpp"'
    if include not in source:
        source = source.replace('#include "pch.hpp"', '#include "pch.hpp"\n' + include, 1)
    anchor = '    LodSettings::instance().init(lodOptionsSec);'
    hook = anchor + '\n\n    // #1513 006250f0 registers DecorSettings immediately after object LOD.\n' \
        '    if (!initialiseStockSceneSettings()) return false;'
    if hook not in source:
        if source.count(anchor) != 1:
            raise ValueError('Pinned personality graphics initialization changed')
        source = source.replace(anchor, hook)
    path.write_text(source, encoding='utf-8')
    destination = Path(root) / 'client/stock_scene_settings.hpp'
    destination.write_bytes(Path(__file__).with_name('stock_scene_settings.hpp').read_bytes())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine', type=Path)
    patch(parser.parse_args().engine)
