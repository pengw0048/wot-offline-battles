from __future__ import print_function
"""Exact-source and actual Windows executable verification for the Bot editor."""
import json
import os
import sys
import full_bot080_build as common

BASE = '5fda392c88c0987ed110a097e3c668d0be011fa2'
ROOT = common.ROOT
PREFIX = common.PREFIX
PACKAGE = 'wot-0.9.22-offline-battles-0.9.4-bot-editor-test-20260924-Windows-x64.zip'
EXPECTED = {'THIRD_PARTY_NOTICES.md': '4b9fd4a274eb7447e3565adc63a4b43150ac9bb6acd6d163cb38fdddf04d32da',
 'docs/testing/094-launcher-bot-editor.md': 'afbd199aa9c8d98813004d1ad64d69169f09885c0deeadb73ce75ce9f30594ce',
 'docs/testing/094-spg-initial-positions.md': '74b8d3a47f8b89c8a8fb6b5d0eab49cb29bc57f8c47d15b2f2d30a31d30f72af',
 'launcher/LAUNCHER_README.txt': 'f93fab22757c36d8734ba088ffbb74073c9c835bd4c90c634cee6d471051a5c2',
 'launcher/bot_tactics_smoke.py': 'e2f358e031baeca33b6a9832556843e4beeea023b239e1f2c04b1ec216ddaf12',
 'launcher/bot_tactics_store.py': 'adbc90078bcdc2c9f1a6e246650aafe35ed72c88c7fb29f59a41c54527738f71',
 'launcher/bot_tactics_ui.py': 'bcf0370f2245fa227056219a122ea8fd3813cf3506952c3226f8f001d9baf9ca',
 'launcher/build_launcher.ps1': 'c2b4fd74de82956f68e312fe6a335a7924eea6a084f328e50f7860134ccb10c0',
 'launcher/requirements-build.txt': '6694e69a42431d7ba5dd2b52cea24f4bf139ffecce19c13f85e0e7c0f629bdea',
 'launcher/tests/test_bot_tactics_ui.py': 'ea54a196913e142042af6fa172c05a60b1384a6b0c273b1d247341b49bbad0bf',
 'launcher/wot_launcher.py': '30c4511cfc02aa7adc36755eec0fa6ef2214c517b6cc64eeb2cf5de6c529c839',
 'server/lan_battle_server.py': '8df6a33c71b03ae5ec9993b27a9b53d41e0738c30d6c7d7f0ab34abaf4a2d0dc',
 'server/server_bot_ai.py': 'ec9ffdffb749e6e1136a4ca6db3567d955daa1123e54beb3449f1aedb173e484',
 'server/windows_server.py': '0b0db78dbcf54621caef5c4aef456e3f3ed0c1133d1fec90e4605c62e0ac6daf',
 'spg_positions/positions_0922.json': '6cf4f1fefadec433d20fc809606c6c84bc1b28f85a73572555486db51af14eb3',
 'src/res/scripts/client/gui/mods/offline_lan_0922/bot_editor_maps.py': '79383b418a247fb8aeddd34b12d45d3f6aa338bb2448630106e91a71a06121e5',
 'src/res/scripts/client/gui/mods/offline_lan_0922/bot_gunnery.py': '7eb66f8231bca0c109e22ed73e7d6865e78482688537d1597b3cbe1715f3e17a',
 'src/res/scripts/client/gui/mods/offline_lan_0922/bot_runtime.py': '3e1c5f22e1fdfef268401b6a9d981eb4c2f962974387456a9e59a1f9a9fccdc6',
 'src/res/scripts/client/gui/mods/offline_lan_0922/bot_tactics.py': '7b733e934211cfb814e7e87c57821d4883a367ecb71eb6d24955db8776acf605',
 'src/res/scripts/client/gui/mods/offline_lan_0922/bot_tactics_runtime.py': '03dd12262605ed8b7760def0800197ad9e663777fcc05bf384a47fdc5495a979',
 'src/res/scripts/client/gui/mods/offline_lan_0922/spg_position_data.py': '7683555dfd140a7e6f432f5f64ac7f997c15a701e8bdaa1cbd4d42928cfc4890',
 'src/res/scripts/client/gui/mods/offline_lan_0922/spg_positions.py': 'd7ee25218fae6d22b12f05d175b3d24798a08be9199191f09b201ba6cd3efd23',
 'tests/test_port_0922_bot_tactics_editor.py': '2155b3e41ffa009aca197539e0e810114a4afa13b025d6b5e6abc4fece1af654',
 'tests/test_port_0922_spg_initial_positions.py': 'af8041ef4bfc1d7bd8bcd997323ed8cc827e8c43fa9104bc0baeb29d173b5526',
 'tools/build_bot_editor_maps.py': '71f3835df11055b5d48b7599ecf50aa4b84d3ef96d70a3650169f09113a246ee',
 'tools/spg_position_catalog.py': '5bdff084fabbc4fc51d5cd6982c9c4af5151dc689fba7e672986c6a198800713',
 'tools/stage_editor_licenses.py': 'e40cf4b5c64ce3dc3feee7cd3681fd536531f09ebf39d58a6f4814fe47fc8211'}
PRODUCTION = ['launcher/LAUNCHER_README.txt',
 'launcher/bot_tactics_smoke.py',
 'launcher/bot_tactics_store.py',
 'launcher/bot_tactics_ui.py',
 'launcher/build_launcher.ps1',
 'launcher/requirements-build.txt',
 'launcher/wot_launcher.py',
 'server/lan_battle_server.py',
 'server/server_bot_ai.py',
 'server/windows_server.py',
 'src/res/scripts/client/gui/mods/offline_lan_0922/bot_editor_maps.py',
 'src/res/scripts/client/gui/mods/offline_lan_0922/bot_gunnery.py',
 'src/res/scripts/client/gui/mods/offline_lan_0922/bot_runtime.py',
 'src/res/scripts/client/gui/mods/offline_lan_0922/bot_tactics.py',
 'src/res/scripts/client/gui/mods/offline_lan_0922/bot_tactics_runtime.py',
 'src/res/scripts/client/gui/mods/offline_lan_0922/spg_position_data.py',
 'src/res/scripts/client/gui/mods/offline_lan_0922/spg_positions.py']


def verify():
    # Local source export has a synthetic baseline tag; CI always uses BASE.
    base = os.environ.get('BOT_EDITOR_LOCAL_BASE', BASE)
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        assert base == BASE
    allowed = set(EXPECTED) | {'tools/bot_editor_build.py',
        '.github/workflows/build-094-bot-editor.yml',
        '.github/workflows/launcher-editor-source.yml'}
    changed = set(common.git('diff','--name-only',base,'--').decode().splitlines())
    extra = set(p for p in changed if p.startswith('tools/bot_editor.patch.') and p.endswith('.b64'))
    assert changed <= allowed | extra, sorted(changed - allowed - extra)
    for path, digest in EXPECTED.items():
        assert common.sha(common.read(os.path.join(ROOT,path)).replace(b'\r\n',b'\n')) == digest, path
    old = json.loads(common.git('show',base+':docs/testing/bot077-proof.json'))
    proof = dict(old)
    proof['runtime_sha256_lf'] = common.runtime_hashes()
    proof['repair_baseline'] = BASE
    proof['editor_source_hashes'] = EXPECTED
    proof['native_gameplay_tested'] = False
    proof['exact_historical_sha256'] = dict((p,h) for p,h in
        old['exact_historical_sha256'].items()
        if p not in EXPECTED and common.sha(common.git('show',base+':'+p).replace(b'\r\n',b'\n')) == h)
    for path, digest in proof['exact_historical_sha256'].items():
        assert common.sha(common.read(os.path.join(ROOT,path)).replace(b'\r\n',b'\n')) == digest, path
    for path, digest in proof['runtime_sha256_lf'].items():
        if path not in EXPECTED:
            assert digest == common.sha(common.git('show',base+':'+path).replace(b'\r\n',b'\n')), path
    # Entire baseline navigation, collision, driver, armor and non-Bot game
    # behavior is protected by both changed-path and content checks.
    for suffix in ('ai/driver.py','ai/traffic.py','ai/adapter.py',
                   'tank_collision.py','battle_runtime.py','vehicle_physics.py','combat_rules.py'):
        path = PREFIX + suffix
        assert common.read(os.path.join(ROOT,path)).replace(b'\r\n',b'\n') == common.git('show',base+':'+path).replace(b'\r\n',b'\n'), path
    for path in common.git('ls-tree','-r','--name-only',base,'navgraphs','foliage','destructibles').decode().splitlines():
        assert common.read(os.path.join(ROOT,path)) == common.git('show',base+':'+path), path
    # Python 2.7 build hosts do not provide retail gui/mods packages.
    metadata = {}
    eval(compile(common.read(os.path.join(ROOT, PREFIX + 'bot_editor_maps.py')),
                 'bot_editor_maps.py', 'exec'), metadata)
    assert len(metadata['MAPS']) == 41
    for name, row in metadata['MAPS'].items():
        assert common.sha(common.read(os.path.join(ROOT,'navgraphs',name+'.json'))) == row['resource_sha256'], name
    common.save(os.path.join(ROOT,'build-evidence/bot-editor-source-proof.json'),proof)
    print('PASS exact Bot editor source, 41 map fingerprints, protected driving/contact/armor/game features.')
    return proof


def distribution(app_name):
    from pathlib import Path
    common.distribution(app_name)
    app = Path(app_name)
    prefix = app/'_internal/servers/0.9.22'
    # Verify the exact server payload list rather than repository-only tools.
    sys.path.insert(0, str(Path(ROOT)/'launcher'))
    import stage_payload
    for relative in stage_payload.PAYLOAD_FILES['0.9.22']:
        source = Path(ROOT)/relative
        installed = prefix/relative
        assert installed.is_file(), str(installed)
        assert installed.read_bytes().replace(b'\r\n',b'\n') == source.read_bytes().replace(b'\r\n',b'\n'), str(source)
    assert any((app/'licenses/Pillow').rglob('*LICENSE*')), 'Pillow wheel licenses'
    # No original game art or workstation fonts may enter this package.
    assert not any(p.suffix.lower() in ('.ttf','.otf','.woff','.woff2') for p in app.rglob('*'))


def package(app_name):
    import io
    import shutil
    import zipfile
    from pathlib import Path
    verify()
    app = Path(app_name)
    evidence = Path(ROOT)/'build-evidence'
    ready = json.loads((evidence/'server-readiness.json').read_bytes())
    ui = json.loads((evidence/'packaged-editor-smoke.json').read_bytes())
    assert ready['ready'] is True and ui['ok'] is True
    assert ui['map_count'] == 41 and ui['canvas_route_points'] >= 4
    assert ui['isolated_profile'] is True and ui['pillow'] == '12.3.0'
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
    with (app/'README.txt').open('a',encoding='utf8') as stream:
        stream.write('\nVerified Bot tactics editor test build.\nSource: '+source+'\nBuild: '+identity['buildIdentity']+'\n')
    archive = Path(ROOT)/PACKAGE
    with zipfile.ZipFile(str(archive),'w',zipfile.ZIP_DEFLATED,compresslevel=6) as pack:
        for path in sorted(app.rglob('*')):
            if path.is_file(): pack.write(str(path),path.relative_to(app).as_posix())
    with zipfile.ZipFile(str(archive)) as pack:
        assert pack.testzip() is None
        assert {p.split('/')[0] for p in pack.namelist()} == allowed
    digest = common.sha(archive.read_bytes())
    (Path(ROOT)/(PACKAGE+'.sha256')).write_text(digest+'  '+PACKAGE+'\n')
    shutil.copyfile(str(Path(ROOT)/'dist/full-bot080-bytecode.json'),str(evidence/'bytecode.json'))
    common.save(str(evidence/'package-manifest.json'),{
        'source':source,'baseline':BASE,'package':PACKAGE,'sha256':digest,
        'bytes':archive.stat().st_size,'identity':identity,'server_readiness':ready,
        'packaged_editor_test':ui,'fake_install_passed':True,'native_gameplay_tested':False,
        'production_paths':PRODUCTION,'protected_navigation_collision_driving':True})
    print('PACKAGE',PACKAGE,digest,archive.stat().st_size)


if __name__ == '__main__':
    common.audit = verify
    action = sys.argv[1]
    if action == 'verify': verify()
    elif action == 'bytecode': common.bytecode()
    elif action == 'distribution': distribution(sys.argv[2])
    elif action == 'package': package(sys.argv[2])
    else: raise SystemExit('Unknown action')
