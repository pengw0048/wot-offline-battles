from __future__ import print_function
"""Exact-source test build. Does not rewrite the historical rollback proof."""
import hashlib
import json
import os
import subprocess
import sys
import full_bot080_build as common

BASE = 'c675b21611afc20f008b832740cd3f8559319cb9'
ROOT = common.ROOT
PREFIX = common.PREFIX
PRODUCTION = ['server/server_bot_ai.py', 'src/res/scripts/client/gui/mods/offline_lan_0922/artillery_arc_queue.py', 'src/res/scripts/client/gui/mods/offline_lan_0922/artillery_controller.py', 'src/res/scripts/client/gui/mods/offline_lan_0922/battle_runtime.py', 'src/res/scripts/client/gui/mods/offline_lan_0922/bot_runtime.py']
EXPECTED = {'server/server_bot_ai.py': '154d51af8dca835f077f2103577c46e3ba2a62dc06e3bf33782e78f05640163e', 'src/res/scripts/client/gui/mods/offline_lan_0922/artillery_arc_queue.py': '046bed7e92e11e321e79c23b9edab0955af87955e3078f6a1b03baf5e85c1d25', 'src/res/scripts/client/gui/mods/offline_lan_0922/artillery_controller.py': 'faf7598571944818ebaee7f4d5047ed1c03935ffc431fc9e003bebd0015a1246', 'src/res/scripts/client/gui/mods/offline_lan_0922/battle_runtime.py': 'c541d1f6f81f89b1e5af7bb52be0254b165f4c077baea1d74071108b9333bc17', 'src/res/scripts/client/gui/mods/offline_lan_0922/bot_runtime.py': 'a23d6982a513f918bb7070892ef6015ad4f3624479137bae3d0ac5b76a4533f6', 'tests/test_port_0922_spg_tracking.py': '338e2530a5c450857bdd98009830861f3405bc22382df89c62be4c07f3fbadb5', 'docs/testing/094-spg-targeting.md': '2bce542e6bf8b86c76aa80b01f9635a07fc8731ba87f1afd04e53de4d1a688b5'}
ALLOWED = set(EXPECTED) | {'tools/spg_tracking_build.py', '.github/workflows/build-spg-tracking-test.yml'} | set('tools/spg_tracking.patch.%d.b64' % i for i in range(4))
PACKAGE = 'wot-0.9.22-offline-battles-0.9.4-spg-targeting-test-20260924-Windows-x64.zip'


def verify():
    changed = set(common.git('diff', '--name-only', BASE, '--').decode().splitlines())
    assert changed.issubset(ALLOWED), sorted(changed-ALLOWED)
    for path, expected in EXPECTED.items():
        assert common.sha(common.read(os.path.join(ROOT,path)).replace(b'\r\n',b'\n')) == expected, path
    old = json.loads(common.git('show',BASE+':docs/testing/bot077-proof.json'))
    assert old['variant'] == 'original'
    proof = dict(old)
    proof['runtime_sha256_lf'] = common.runtime_hashes()
    proof['repair_baseline'] = BASE
    proof['repair_paths'] = PRODUCTION
    proof['native_gameplay_tested'] = False
    # Keep map/catalog hashes and all unchanged historical modules. Changed
    # modules are verified against this repair, not labelled exact upstream077.
    proof['exact_historical_sha256'] = dict((p,h) for p,h in
        old['exact_historical_sha256'].items()
        if common.sha(common.git('show', BASE+':'+p).replace(b'\r\n',b'\n')) == h
        and p not in PRODUCTION)
    for path, expected in proof['exact_historical_sha256'].items():
        assert common.sha(common.read(os.path.join(ROOT,path)).replace(b'\r\n',b'\n')) == expected,path
    for path, digest in proof['runtime_sha256_lf'].items():
        if path not in PRODUCTION:
            assert digest == common.sha(common.git('show', BASE+':'+path).replace(b'\r\n',b'\n')), path
    for path in ('ai/driver.py','ai/traffic.py','ai/adapter.py'):
        assert common.read(os.path.join(ROOT,PREFIX+path)).replace(b'\r\n',b'\n') == common.git('show',BASE+':'+PREFIX+path).replace(b'\r\n',b'\n'),path
    common.save(os.path.join(ROOT,'build-evidence/spg-targeting-source-proof.json'), proof)
    print('PASS exact SPG repair; 5 production files; original driver/traffic/adapter, maps and previous fixes preserved.')
    return proof


def package(app_name):
    import io
    import shutil
    import zipfile
    from pathlib import Path
    verify()
    app = Path(app_name)
    evidence = Path(ROOT)/'build-evidence'
    evidence.mkdir(exist_ok=True)
    ready = json.loads((evidence/'server-readiness.json').read_bytes())
    assert ready['ready'] is True
    source = common.git('rev-parse','HEAD').decode().strip()
    assert source == os.environ['FULL_BOT080_SOURCE_SHA']
    receipt = json.loads((app/'FULL_BOT080_BUILD_EVIDENCE.json').read_bytes())
    assert receipt['fake_install_passed'] and receipt['source_commit'] == source
    allowed = {'wot-0.9.22-offline-battles.exe','_internal','README.txt',
               'LICENSE','THIRD_PARTY_NOTICES.md','licenses'}
    for path in list(app.iterdir()):
        if path.name not in allowed:
            assert path.is_file(), path
            shutil.move(str(path),str(evidence/path.name))
    assert {p.name for p in app.iterdir()} == allowed
    with zipfile.ZipFile(str(app/'_internal/client/0.9.22.zip')) as client:
        assert client.testzip() is None
        identity = json.loads(client.read('mods/configs/offline_lan_0922/build_identity.json'))
        assert identity['semanticVersion'] == '0.9.4'
        assert identity['buildIdentity'] == os.environ['WOT_OFFLINE_BUILD_IDENTITY']
        mods = [n for n in client.namelist() if n.endswith('.wotmod')]
        assert len(mods) == 1
        with zipfile.ZipFile(io.BytesIO(client.read(mods[0]))) as mod:
            assert mod.testzip() is None
            for path in PRODUCTION:
                if path.startswith('src/'):
                    assert path[len('src/'):]+'c' in mod.namelist(),path
    with (app/'README.txt').open('a',encoding='utf8') as f:
        f.write('\nSPG targeting test: retain target while aiming; exact launch proof remains required.\nSource: '+source+'\nBuild: '+identity['buildIdentity']+'\n')
    archive = Path(ROOT)/PACKAGE
    with zipfile.ZipFile(str(archive),'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for path in sorted(app.rglob('*')):
            if path.is_file():z.write(str(path),path.relative_to(app).as_posix())
    with zipfile.ZipFile(str(archive)) as z:
        assert z.testzip() is None
        assert {p.split('/')[0] for p in z.namelist()} == allowed
    digest = common.sha(archive.read_bytes())
    (Path(ROOT)/(PACKAGE+'.sha256')).write_text(digest+'  '+PACKAGE+'\n')
    shutil.copyfile(str(Path(ROOT)/'dist/full-bot080-bytecode.json'),str(evidence/'bytecode.json'))
    common.save(str(evidence/'package-manifest.json'),{
        'source':source,'baseline':BASE,'package':PACKAGE,'sha256':digest,
        'bytes':archive.stat().st_size,'identity':identity,'server_readiness':ready,
        'fake_install_passed':True,'native_gameplay_tested':False,
        'production_changes':PRODUCTION,'original_driver_traffic_maps_preserved':True})
    print('PACKAGE',PACKAGE,digest,archive.stat().st_size)

if __name__ == '__main__':
    common.audit = verify
    action = sys.argv[1]
    if action == 'verify':verify()
    elif action == 'bytecode':common.bytecode()
    elif action == 'distribution':common.distribution(sys.argv[2])
    elif action == 'package':package(sys.argv[2])
    else:raise SystemExit('Unknown action')
