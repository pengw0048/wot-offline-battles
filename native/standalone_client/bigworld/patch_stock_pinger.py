"""Install the native login pinger before stock Python imports and drain it first."""
import argparse
from pathlib import Path


def insert(text, anchor, addition):
    if addition in text:
        return text
    if text.count(anchor) != 1:
        raise RuntimeError('The reviewed pinger lifecycle anchor has changed: ' + repr(anchor))
    return text.replace(anchor, anchor + addition)


def patch(root):
    root = Path(root)
    updates = []
    path = root / 'client/script_bigworld.cpp'
    source = insert(path.read_text(), '#include "pch.hpp"',
                    '\n#include "stock_pinger.hpp"\n#include "connection_control.hpp"\n#include "cstdmf/bgtask_manager.hpp"')
    source = insert(source,
        '\tif (!Script::init( paths, "client" ) )\n\t{\n\t\treturn false;\n\t}',
        '\n\n\tif (!initialiseStockPinger(ConnectionControl::dispatcher(), BgTaskManager::instance()))'
        '\n\t{\n\t\treturn false;\n\t}')
    updates.append((path, source))
    path = root / 'client/script_app.cpp'
    source = insert(path.read_text(), '#include "pch.hpp"', '\n#include "stock_pinger.hpp"')
    marker = '\tBigWorldClientScript::fini();'
    before = '\tfiniStockPinger();\n'
    if before not in source:
        if source.count(marker) != 1:
            raise RuntimeError('The reviewed Python teardown owner has changed')
        source = source.replace(marker, before + marker)
    updates.append((path, source))
    for path, source in updates:
        path.write_text(source)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bigworld_root', type=Path)
    patch(parser.parse_args().bigworld_root)
