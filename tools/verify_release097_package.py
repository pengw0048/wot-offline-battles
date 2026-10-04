"""Inspect and execute the final frozen launcher and embedded replay runtime."""
from pathlib import Path
import importlib.util
import io
import json
import marshal
import subprocess
import sys
import tempfile
import types
import zipfile
from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]
app = Path(sys.argv[1]).resolve()
archive = CArchiveReader(str(app / 'wot-0.9.22-offline-battles.exe'))
pyz = archive.open_embedded_archive('PYZ.pyz')


def normalize(code):
    return code.replace(co_filename='<source>', co_consts=tuple(
        normalize(item) if isinstance(item, types.CodeType) else item
        for item in code.co_consts))


verified = []
for source in sorted((ROOT / 'launcher').glob('*.py')):
    if source.stem == 'wot_launcher':
        actual = marshal.loads(archive.extract(source.stem))
    elif source.stem in pyz.toc:
        actual = pyz.extract(source.stem)
    else:
        continue
    assert normalize(actual) == normalize(compile(source.read_bytes(), '<source>', 'exec')), source
    verified.append(source.name)
assert 'replay_launch.py' in verified
assert 'wot_launcher.py' in verified
assert 'core.py' in verified

with zipfile.ZipFile(app / '_internal/client/0.9.22.zip') as client:
    assert client.read('offline_worker_starter.exe') == (ROOT / 'native/offline_worker_starter.exe').read_bytes()
    mod_name = next(n for n in client.namelist() if n.endswith('.wotmod'))
    assert mod_name.endswith('_0.9.7.wotmod')
    with tempfile.TemporaryDirectory(prefix='release097-') as temp:
        path = Path(temp) / mod_name
        path.parent.mkdir(parents=True)
        path.write_bytes(client.read(mod_name))
        source = ROOT / 'src/res/scripts/client/gui/mods/offline_lan_0922/replay_process.py'
        spec = importlib.util.spec_from_file_location('replay_process_check', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        command = module.prepare_runtime(temp)
        # Execute the official runtime that the installed 0.9.7 mod supplied.
        result = subprocess.check_output(command[:3] + ['-c', 'import sys;print(sys.version_info[:3])'], creationflags=0x08000000)
        assert b'(3, 11, 9)' in result, result
        with zipfile.ZipFile(path) as mod:
            assert all(i.compress_type == zipfile.ZIP_STORED for i in mod.infolist())
            assert mod.read('res/offline_replay/replay_reader_process.py') == (ROOT / 'tools/replay/replay_reader_process.py').read_bytes()
print(json.dumps({'frozen_source_modules_verified': len(verified),
                  'replay_entry_bundled': True, 'recorder_runtime_executed': True,
                  'native_starter_matches_source_tree': True}))
