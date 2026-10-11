"""Reproduce the accepted offline recorder resource from official CPython."""
from pathlib import Path
import hashlib
import io
import re
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip'
INPUT_DIGEST = '009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b'


def build():
    target = ROOT / 'build/replay-runtime/recorder-runtime.zip'
    source = ROOT / 'src/res/scripts/client/gui/mods/offline_lan_0922/replay_process.py'
    expected = re.search(r"RUNTIME_DIGEST = '([a-f0-9]+)'", source.read_text()).group(1)
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
        return target
    with urllib.request.urlopen(URL, timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != INPUT_DIGEST:
        raise ValueError('Official CPython embedded ZIP failed integrity verification')
    buffer = io.BytesIO(data)
    with zipfile.ZipFile(buffer, 'a', zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo('replay_writer_process.py', (2026, 10, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        archive.writestr(info, (ROOT / 'tools/replay/replay_writer_process.py').read_bytes())
    result = buffer.getvalue()
    if hashlib.sha256(result).hexdigest() != expected:
        raise ValueError('Rebuilt recorder differs from the accepted runtime resource')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(result)
    return target


if __name__ == '__main__':
    print('Verified recorder resource:', build())
