"""Verify a launcher-only localization build against delivered PR #38 bytes."""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = 'c584fa591ccff2429c3850d92bafb1d4d6afbd1a'
PREVIOUS_PACKAGE_SHA = 'a8d181aae1c75c9e077bc42dc7e030a24ff5ac7f88d19ab4f86ff98d72c3647e'
PACKAGE = 'wot-0.9.22-offline-battles-0.9.4-bot-editor-i18n-20260924-Windows-x64.zip'
CHANGED_LAUNCHER = {
    'launcher/bot_tactics_labels.py', 'launcher/bot_tactics_ui.py',
    'launcher/bot_tactics_store.py', 'launcher/wot_launcher.py',
    'launcher/bot_tactics_smoke.py',
}
SUPPORT = {
    'launcher/tests/test_bot_tactics_localization.py',
    'docs/testing/094-bot-editor-localization.md',
    'tools/bot_editor_localization_build.py',
    '.github/workflows/build-094-bot-editor-i18n.yml',
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(*args):
    return subprocess.check_output(['git'] + list(args), cwd=ROOT)


def record(name, value):
    path = ROOT / 'build-evidence' / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')


def verify():
    changed = set(git('diff','--name-only',BASE,'--').decode().splitlines())
    unexpected = {p for p in changed if p not in CHANGED_LAUNCHER | SUPPORT and
                  not (p.startswith('tools/editor_i18n.patch.') and p.endswith('.b64'))}
    assert not unexpected, sorted(unexpected)
    # Everything responsible for actual battle behavior and maps stays exact.
    assert not git('diff', BASE, '--', 'src', 'server', 'navgraphs', 'spg_positions', 'build_wotmod.py')
    sys.path.insert(0, str(ROOT / 'launcher'))
    import bot_tactics_labels as labels
    import bot_tactics_store as storage
    assert set(labels.MAP_NAMES) == set(storage.contract.MAPS)
    assert labels.map_label('04_himmelsdorf','zh') == '锡莫尔斯多夫'
    record('localization-source.json', {
        'baseline': BASE, 'source': git('rev-parse','HEAD').decode().strip(),
        'launcher_changes': sorted(CHANGED_LAUNCHER),
        'runtime_changed': False, 'map_count': len(labels.MAP_NAMES),
        'route_count': len(labels.ROUTE_NAMES),
        'hashes': {p:sha((ROOT/p).read_bytes()) for p in sorted(CHANGED_LAUNCHER)},
    })
    print('PASS launcher-only localization; game runtime, 41 maps and config schema unchanged.')


def prepare(previous):
    previous = Path(previous)
    assert sha(previous.read_bytes()) == PREVIOUS_PACKAGE_SHA
    base = ROOT / '.localization-baseline'
    base.mkdir(exist_ok=True)
    with zipfile.ZipFile(previous) as pack:
        assert pack.testzip() is None
        for entry in pack.namelist():
            target = (base / entry).resolve()
            assert str(target).startswith(str(base.resolve()) + os.sep)
        pack.extractall(base)
    client = base / '_internal/client/0.9.22.zip'
    overlay = ROOT / 'dist/WoT-0.9.22-LAN-Client-localization'
    overlay.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(client) as pack:
        assert pack.testzip() is None
        pack.extractall(overlay)
    print('Prepared original client payload; no .wotmod recompilation or behavior changes.')


def distribution(app):
    verify()
    app = Path(app)
    base = ROOT / '.localization-baseline'
    # stage_payload reconstructs a ZIP; restore the exact delivered ZIP bytes.
    shutil.copy2(base / '_internal/client/0.9.22.zip', app / '_internal/client/0.9.22.zip')
    old_files = {p.relative_to(base/'_internal/servers').as_posix():sha(p.read_bytes())
                 for p in (base/'_internal/servers').rglob('*') if p.is_file()}
    new_files = {p.relative_to(app/'_internal/servers').as_posix():sha(p.read_bytes())
                 for p in (app/'_internal/servers').rglob('*') if p.is_file()}
    assert old_files == new_files, 'Bundled server/runtime differs from previous package'
    archive = app/'_internal/client/0.9.22.zip'
    with zipfile.ZipFile(archive) as client:
        assert client.testzip() is None
        identity = json.loads(client.read('mods/configs/offline_lan_0922/build_identity.json'))
        for member in client.namelist():
            if member.endswith('.wotmod'):
                with zipfile.ZipFile(io.BytesIO(client.read(member))) as mod:
                    assert mod.testzip() is None
    assert identity['buildIdentity'] == 'colorfulmeans-v094-bot-editor-35995108297-1'
    forbidden = [str(p) for p in app.rglob('*') if p.suffix.lower() in ('.ttf','.ttc','.otf','.woff','.woff2')]
    assert not forbidden, forbidden
    record('runtime-preservation.json', {
        'identical_server_source_files':len(old_files),
        'client_zip_sha256':sha(archive.read_bytes()),
        'client_zip_byte_identical':True, 'unchanged_runtime_identity':identity,
        'no_font_files':True,
    })
    print('PASS byte-identical previous client and all %d server/runtime sources.' % len(old_files))


def package(app):
    app = Path(app)
    evidence = ROOT/'build-evidence'
    smoke = json.loads((evidence/'packaged-editor-smoke.json').read_text())
    assert smoke['ok'] and smoke['localization_checked'] and smoke['language_switch_preserves_active']
    assert smoke['translated_map_count']==41 and smoke['translated_route_count']==96
    ready = json.loads((evidence/'server-readiness.json').read_text())
    assert ready['ready'] is True
    # Versioned authoring data is not a live-language preference or user save.
    source=git('rev-parse','HEAD').decode().strip()
    with (app/'README.txt').open('a',encoding='utf8') as f:
        f.write('\nLauncher localization update (2026-09-24)\n')
        f.write('Main language selector controls all Bot editor captions, including already open windows.\n')
        f.write('41 full map names / 96 built-in route captions / canonical profile IDs unchanged.\n')
        f.write('Launcher source: '+source+'\n')
        f.write('Client and server payloads remain byte-identical to the previous Bot editor package.\n')
    # The required --serve smoke writes server.log beside its frozen EXE.
    # Keep that build-only trace as evidence, not inside the user distribution.
    for name in ('TESTING_20260916_GROUP1_ZH.md', 'server.log'):
        extra = app / name
        if extra.exists():
            assert extra.is_file() and not extra.is_symlink(), str(extra)
            shutil.move(str(extra), str(evidence / name))
    allowed={'wot-0.9.22-offline-battles.exe','_internal','README.txt','LICENSE','THIRD_PARTY_NOTICES.md','licenses'}
    actual = {p.name for p in app.iterdir()}
    assert actual == allowed, {'unexpected': sorted(actual - allowed),
                               'missing': sorted(allowed - actual)}
    target=ROOT/PACKAGE
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(app.rglob('*')):
            if p.is_file():z.write(p,p.relative_to(app).as_posix())
    with zipfile.ZipFile(target) as z:assert z.testzip() is None
    digest=sha(target.read_bytes())
    (ROOT/(PACKAGE+'.sha256')).write_text(digest+'  '+PACKAGE+'\n',encoding='ascii')
    record('package-manifest.json',{'source':source,'baseline':BASE,'package':PACKAGE,
        'sha256':digest,'bytes':target.stat().st_size,'runtime_changed':False,
        'native_gameplay_tested':False,'packaged_editor_smoke':smoke})
    print('PACKAGE',PACKAGE,digest,target.stat().st_size)


if __name__=='__main__':
    action=sys.argv[1]
    if action=='verify':verify()
    elif action=='prepare':prepare(sys.argv[2])
    elif action=='distribution':distribution(sys.argv[2])
    elif action=='package':package(sys.argv[2])
    else:raise SystemExit('Unknown action')
