"""Copy the exact installed Pillow wheel's complete license directory."""
import importlib.metadata
from pathlib import Path
import shutil
import sys


def stage(destination):
    dist = importlib.metadata.distribution('Pillow')
    if dist.version != '12.3.0':
        raise RuntimeError('Unexpected Pillow build dependency: '+dist.version)
    root = Path(destination)/'Pillow'
    written = []
    for member in dist.files or ():
        parts = member.parts
        if 'licenses' not in parts:
            continue
        rel = Path(*parts[parts.index('licenses')+1:])
        if not rel.parts or '..' in rel.parts:
            continue
        source = Path(dist.locate_file(member))
        if not source.is_file():
            continue
        # License files only, never installed fonts or unrelated runtime data.
        if source.suffix.lower() in ('.ttf','.otf','.woff','.woff2','.pyd','.dll'):
            raise RuntimeError('Non-license binary in Pillow license directory')
        source.read_text(encoding='utf8')
        target = root/rel;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target);written.append(str(rel))
    if not written or not any('license' in name.lower() for name in written):
        raise RuntimeError('Pillow wheel licenses missing')
    print('Pillow 12.3.0 licenses:', ', '.join(written))
    return written

if __name__ == '__main__':stage(sys.argv[1])
