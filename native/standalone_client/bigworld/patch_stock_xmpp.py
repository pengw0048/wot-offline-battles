"""Bind the real XMPP transport to Python startup, main-thread tick and teardown."""
import argparse
from pathlib import Path


def insert(text, anchor, addition):
    if addition in text:
        return text
    if text.count(anchor) != 1:
        raise RuntimeError('The reviewed XMPP lifecycle anchor has changed: ' + repr(anchor))
    return text.replace(anchor, anchor + addition)


def patch(root):
    root = Path(root)
    updates = []
    path = root / 'client/script_bigworld.cpp'
    source = insert(path.read_text(), '#include "pch.hpp"',
                    '\n#include "stock_xmpp.hpp"')
    source = insert(source,
        '\tif (!Script::init( paths, "client" ) )\n\t{\n\t\treturn false;\n\t}',
        '\n\n\tif (!initialiseStockXmpp())\n\t{\n\t\treturn false;\n\t}')
    updates.append((path, source))
    path = root / 'client/script_app.cpp'
    source = insert(path.read_text(), '#include "pch.hpp"',
                    '\n#include "stock_xmpp.hpp"')
    source = insert(source, '\tBigWorldClientScript::tick();',
                    '\n\ttickStockXmpp();')
    marker = '\tBigWorldClientScript::fini();'
    before = '\tfiniStockXmpp();\n'
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
