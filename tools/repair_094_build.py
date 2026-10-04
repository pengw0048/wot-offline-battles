from __future__ import print_function
"""Exact-source test build. Does not rewrite the historical rollback proof."""
import hashlib
import json
import os
import subprocess
import sys
import full_bot080_build as common

BASE = '599612c1eaa91fc4e9935cccab929f3bbbd5457e'
ROOT = common.ROOT
PREFIX = common.PREFIX
PRODUCTION = [PREFIX+'ai/planner.py', PREFIX+'battle_runtime.py',
              PREFIX+'bot_runtime.py', PREFIX+'bot_state_codec.py',
              'server/server_bot_ai.py']
EXPECTED = {'launcher/LAUNCHER_README.txt': 'd354a9540698d0a8211fee789092f491d69ab2192bf129bdd3e8622af89e9aa5', 'server/server_bot_ai.py': 'd00fa379a1beaf05718c225a928e1c91840251baaf4d77777333185a86fdb0e0', 'src/res/scripts/client/gui/mods/offline_lan_0922/ai/planner.py': '6fd471c88c2c58f9e55209531b09837212035bfbb6f0884d7b187c23c4b4e9f3', 'src/res/scripts/client/gui/mods/offline_lan_0922/battle_runtime.py': '81597f2411f4f782fca6fb7d9ab533b6035f43902298ef4585c75a9e65594a9f', 'src/res/scripts/client/gui/mods/offline_lan_0922/bot_runtime.py': '7302fd6e4b3538965ecbfc52363ad256c43f8d0c123e9c98365aed43df0cde6d', 'src/res/scripts/client/gui/mods/offline_lan_0922/bot_state_codec.py': '3a11ab1334bb8bbd44be4dbc2d7a93209a59e4a2775786aa6f839c66319c96ab', 'tests/test_port_0922_094_repair.py': '771383cca5953257e0e9eb416cb54d1d5a56c20c50799190a1210de996d7c4e6', 'tools/test_094_repairs.py': '2300bc7127fa0444a5fbd0823cd4f34ab77233cd4bb43a3698d074315289dc1e', 'docs/testing/094-match-spg-contact-radio.md': 'bd15fad02e11c5d707f86a173d4e528e77556beed5b41435bb2fe0859b2823c9'}
ALLOWED = set(EXPECTED) | set((
    'tools/repair_094_build.py', 'tools/repair_094.patch.b64',
    '.github/workflows/build-094-mechanics-test.yml')) | set(
    'tools/repair_094.patch.%d.b64' % part for part in range(4))
PACKAGE = 'wot-0.9.22-offline-battles-0.9.4-mechanics-test-20260924-Windows-x64.zip'


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
        old['exact_historical_sha256'].items() if p not in PRODUCTION)
    for path, expected in proof['exact_historical_sha256'].items():
        assert common.sha(common.read(os.path.join(ROOT,path)).replace(b'\r\n',b'\n')) == expected,path
    runtime_changes = set(p for p,h in proof['runtime_sha256_lf'].items()
                          if h != old['runtime_sha256_lf'].get(p))
    assert runtime_changes == set(p for p in PRODUCTION if p.startswith('src/')), sorted(runtime_changes)
    for path in ('ai/driver.py','ai/traffic.py','ai/adapter.py'):
        assert common.read(os.path.join(ROOT,PREFIX+path)).replace(b'\r\n',b'\n') == common.git('show',BASE+':'+PREFIX+path).replace(b'\r\n',b'\n'),path
    common.save(os.path.join(ROOT,'build-evidence/mechanics-source-proof.json'), proof)
    print('PASS exact repair source; 5 production files; original driving/traffic, maps, armor and prior account fixes preserved.')
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
        f.write('\nSource: '+source+'\nBuild: '+identity['buildIdentity']+'\n')
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
