"""Expose the existing renderer-wide HDR enable owner to stock startup.

The public instance setter already writes one global HDRSettings record. A
forward renderer has no HDRSupport instance, so constructing a temporary one
to write that flag would incorrectly create device resources and watchers.
"""
from pathlib import Path
import argparse


def patch(root):
    root = Path(root)
    header = root / 'lib/romp/hdr_support.hpp'
    source = root / 'lib/romp/hdr_support.cpp'
    text = header.read_text()
    marker = '\tvoid\t\t\t\t\tenable(bool flag);'
    if 'static void setGlobalEnabled(bool flag);' not in text:
        if text.count(marker) != 1:
            raise RuntimeError('HDRSupport enable declaration does not match the reviewed public source')
        text = text.replace(marker, marker + '\n\tstatic void setGlobalEnabled(bool flag);')
        header.write_text(text)
    text = source.read_text()
    old = '''void HDRSupport::enable(bool flag)
{
    BW_GUARD;
    g_cfg.m_enabled = flag;
}'''
    new = '''void HDRSupport::enable(bool flag)
{
    BW_GUARD;
    setGlobalEnabled(flag);
}

void HDRSupport::setGlobalEnabled(bool flag)
{
    BW_GUARD;
    g_cfg.m_enabled = flag;
}'''
    if 'void HDRSupport::setGlobalEnabled(bool flag)' not in text:
        if text.count(old) != 1:
            raise RuntimeError('HDRSupport enable implementation does not match the reviewed public source')
        source.write_text(text.replace(old, new))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bigworld_root', type=Path)
    patch(parser.parse_args().bigworld_root)
