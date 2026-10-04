from __future__ import print_function
"""Promote the user-selected Bot077 original-policy build without gameplay edits."""
import ast
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = 'a3e4c5937789d9dd63fde6902783d5027ec7c6f3'
VERSION = '0.9.4'
INIT = 'src/res/scripts/client/gui/mods/offline_lan_0922/__init__.py'
PROOF = 'docs/testing/bot077-proof.json'
VERSIONS = (
    INIT, 'meta.xml', 'build_wotmod.py', 'build_for_client.sh',
    'launcher/wot_launcher.py', 'launcher/version_info.txt',
    'server/windows_server.py', 'server/version_info.txt',
    'launcher/tests/test_launcher_window.py', 'tests/test_port_0922.py',
)
ALLOWED = set(VERSIONS) | {
    PROOF, 'README.md', 'launcher/LAUNCHER_README.txt',
    'docs/releases/v0.9.4.md', 'tools/release_v094.py',
    '.github/workflows/publish-v0.9.4.yml',
}
PACKAGE = 'wot-0.9.22-offline-battles-0.9.4-Windows-x64.zip'
NORMAL_ROOT = {'wot-0.9.22-offline-battles.exe', '_internal', 'README.txt',
               'LICENSE', 'THIRD_PARTY_NOTICES.md', 'licenses'}


def git(*args):
    return subprocess.check_output(['git'] + list(args), cwd=str(ROOT))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def baseline(path):
    return git('show', BASE + ':' + path)


def save(path, data):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def expected_versions(path):
    old = baseline(path)
    assert b'0.9.3' in old, path
    result = old.replace(b'0.9.3', b'0.9.4')
    if path.endswith('version_info.txt') or path == 'tests/test_port_0922.py':
        assert old.count(b'(0, 9, 3, 0)') == 2, path
        result = result.replace(b'(0, 9, 3, 0)', b'(0, 9, 4, 0)')
    return result


def prepare():
    git('merge-base', '--is-ancestor', BASE, 'HEAD')
    for path in VERSIONS:
        save(path, expected_versions(path))
    readme = baseline('README.md').decode('utf-8')
    start = readme.index('The [v0.9.3 release notes]')
    end = readme.index('\n\nGrand Battles', start)
    readme = readme[:start] + (
        'The [v0.9.4 release notes](docs/releases/v0.9.4.md) describe the\n'
        'promotion of the selected upstream v0.7.7 Bot behavior and original\n'
        'navigation assets. Original mutual yielding is retained; the no-yield\n'
        'comparison is not included. Radio visibility and nonempty-roster startup\n'
        'fixes remain, together with the selected build\'s current non-Bot systems.\n'
        'This is a Bot rollback, not a downgrade of the whole game.'
    ) + readme[end:]
    save('README.md', readme.replace('0.9.3', '0.9.4').encode('utf-8'))
    shipped = baseline('launcher/LAUNCHER_README.txt').decode('utf-8')
    lines = shipped.splitlines(True)
    assert lines[0].startswith('wot-0.9.22-offline-battles v')
    lines[0] = 'wot-0.9.22-offline-battles v0.9.4\n'
    shipped = ''.join(lines).replace(
        'UPDATE_NOTES.md lists the complete changes since v0.8.4.',
        'Release notes are on the GitHub v0.9.4 release page.')
    shipped += ('\n\nVersion 0.9.4\n'
                'Selected Bot source: ' + BASE + '\n'
                'Upstream v0.7.7 Bot behavior and original maps; original mutual yielding retained.\n'
                'Radio visibility and battle-start compatibility fixes are retained.\n'
                'The no-yield comparison is not included. Shared physics and non-Bot game systems\n'
                'remain from the selected build; this is not the complete upstream game.\n'
                'Extract the whole ZIP to a new empty folder; do not mix _internal directories.\n')
    save('launcher/LAUNCHER_README.txt', shipped.encode('utf-8'))
    proof = json.loads(baseline(PROOF))
    assert proof['variant'] == 'original' and not proof['removed_paths']
    digest = sha((ROOT / INIT).read_bytes().replace(b'\r\n', b'\n'))
    proof['runtime_sha256_lf'][INIT] = digest
    if INIT in proof['preserved_runtime_sha256_lf']:
        proof['preserved_runtime_sha256_lf'][INIT] = digest
    proof['release_metadata'] = {
        'version': VERSION, 'selected_source': BASE,
        'runtime_version_only_path': INIT,
        'gameplay_modified_for_release': False,
    }
    save(PROOF, (json.dumps(proof, sort_keys=True, indent=2) + '\n').encode('utf-8'))
    subprocess.check_call(['git', 'add', '--'] + sorted(ALLOWED), cwd=str(ROOT))
    verify_source()


def verify_source():
    changed = set(git('diff', '--name-only', BASE, '--').decode('utf-8').splitlines())
    assert changed.issubset(ALLOWED), sorted(changed - ALLOWED)
    for path in VERSIONS:
        assert (ROOT / path).read_bytes() == expected_versions(path), path
    names = git('ls-tree', '-r', '--name-only', BASE).decode('utf-8').splitlines()
    protected = []
    for name in names:
        if name.startswith(('src/', 'server/', 'launcher/', 'navgraphs/', 'foliage/', 'destructibles/')) and name not in ALLOWED:
            assert (ROOT / name).is_file(), name
            assert (ROOT / name).read_bytes() == baseline(name), name
            protected.append(name)
    import bot077_build
    proof = bot077_build.audit()
    assert proof['variant'] == 'original' and not proof['removed_paths']
    traffic = ROOT / 'src/res/scripts/client/gui/mods/offline_lan_0922/ai/traffic.py'
    assert traffic.is_file() and b'class TrafficCoordinator' in traffic.read_bytes()
    bot = (ROOT / 'src/res/scripts/client/gui/mods/offline_lan_0922/bot_runtime.py').read_bytes()
    assert b'self._traffic_coordinator.adjust' in bot
    print('PASS release 0.9.4 metadata; %d protected files exactly match %s.' % (len(protected), BASE))
    return {'selected_source': BASE, 'version': VERSION, 'protected_file_count': len(protected),
            'changed_paths': sorted(changed), 'original_yield_retained': True,
            'gameplay_modified_for_release': False, 'native_game_tested': False}


def package(app_name):
    import xml.etree.ElementTree as ET
    app = Path(app_name).resolve()
    proof = verify_source()
    evidence = ROOT / 'build-evidence'
    evidence.mkdir(exist_ok=True)
    for p in list(app.iterdir()):
        if p.name not in NORMAL_ROOT:
            assert p.is_file(), str(p)
            shutil.move(str(p), str(evidence / p.name))
    assert set(p.name for p in app.iterdir()) == NORMAL_ROOT
    assert (app / 'README.txt').read_bytes() == (ROOT / 'launcher/LAUNCHER_README.txt').read_bytes()
    with zipfile.ZipFile(str(app / '_internal/client/0.9.22.zip')) as client:
        assert client.testzip() is None
        ident = json.loads(client.read('mods/configs/offline_lan_0922/build_identity.json'))
        assert ident['semanticVersion'] == VERSION
        assert ident['buildIdentity'] == os.environ['WOT_OFFLINE_BUILD_IDENTITY']
        mods = [n for n in client.namelist() if n.endswith('.wotmod')]
        assert len(mods) == 1 and mods[0].endswith('_0.9.4.wotmod'), mods
        with zipfile.ZipFile(io.BytesIO(client.read(mods[0]))) as mod:
            assert mod.testzip() is None
            assert ET.fromstring(mod.read('meta.xml')).findtext('version') == VERSION
            prefix = 'res/scripts/client/gui/mods/offline_lan_0922/'
            assert prefix + 'ai/traffic.pyc' in mod.namelist()
            assert b'TrafficCoordinator' in mod.read(prefix + 'bot_runtime.pyc')
        map_prefix = 'mods/configs/offline_lan_0922/navgraphs/'
        manifest = json.loads(client.read(map_prefix + 'manifest.json'))
        assert len(manifest['maps']) == 41
        for item in manifest['maps']:
            name = item['file']
            content = client.read(map_prefix + name)
            assert sha(content) == item['sha256']
            assert content == baseline('navgraphs/' + name)
    output = ROOT / PACKAGE
    with zipfile.ZipFile(str(output), 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(app.rglob('*')):
            assert not p.is_symlink(), str(p)
            if p.is_file():
                z.write(str(p), p.relative_to(app).as_posix())
    with zipfile.ZipFile(str(output)) as z:
        assert z.testzip() is None
        assert len(z.namelist()) == len(set(z.namelist()))
        assert set(n.split('/')[0] for n in z.namelist()) == NORMAL_ROOT
        assert z.read('wot-0.9.22-offline-battles.exe')[:2] == b'MZ'
        for n in z.namelist():
            assert not n.startswith('/') and '..' not in n.split('/') and '\\' not in n
    proof.update({'release_source': os.environ['FULL_BOT080_SOURCE_SHA'],
                  'identity': ident, 'package': PACKAGE,
                  'package_sha256': sha(output.read_bytes()),
                  'package_bytes': output.stat().st_size, 'root_entries': sorted(NORMAL_ROOT)})
    (evidence / 'release094-package.json').write_text(json.dumps(proof, sort_keys=True, indent=2) + '\n')
    shutil.copy(str(ROOT / 'dist/full-bot080-bytecode.json'), str(evidence / 'bytecode.json'))
    print('PASS complete clean v0.9.4 Windows package:', proof['package_sha256'])


if __name__ == '__main__':
    os.chdir(str(ROOT))
    if sys.argv[1] == 'prepare':
        prepare()
    elif sys.argv[1] == 'verify':
        verify_source()
    elif sys.argv[1] == 'package':
        package(sys.argv[2])
    else:
        raise SystemExit('Usage: release_v094.py prepare|verify|package APP_ROOT')
