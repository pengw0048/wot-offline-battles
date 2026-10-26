"""Restore the #1513 application import paths before Python initialisation."""
import argparse
from pathlib import Path


def patch(root):
    path = Path(root) / 'client/script_bigworld.cpp'
    source = path.read_text()
    marker = '\tpaths.addResPath( EntityDef::Constants::userDataObjectsClientPath() );'
    addition = '\n\tpaths.addResPath( "scripts/client_common" );'
    if addition in source:
        return
    if source.count(marker) != 1:
        raise RuntimeError('The reviewed client Python path owner has changed')
    # 00657160 appends client, user-data-object client, then client_common;
    # Script::init adds common and the standard library after these paths.
    path.write_text(source.replace(marker, marker + addition))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bigworld_root', type=Path)
    patch(parser.parse_args().bigworld_root)
