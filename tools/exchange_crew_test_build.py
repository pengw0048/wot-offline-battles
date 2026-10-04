"""Build the requested v0.9.4 exchange/crew Windows test package.

No main update, tag, release publication, Bot change or map change is allowed.
The range guard still requires acceptance in the original #1513 Flash UI.
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
BASE = '7159332d197a02e7128948c2eed7c3393d8d3db0'
CREW = '6d3af14755d6ba80bc6c25fc536f1bf8be9dbdd9'
PATCH_SHA256 = '5c55bc8d6c24af1c7b0225b1cb57befe6e00df6611cf6ab050cf49e2b2bb3472'
PREFIX = 'src/res/scripts/client/gui/mods/offline_lan_0922/'
PROOF = 'docs/testing/bot077-proof.json'
PACKAGE = 'wot-0.9.22-offline-battles-0.9.4-exchange-crew-test-20260924-Windows-x64.zip'
EXPECTED = {
    'COMPATIBILITY_REVIEW.md': '7c7e761911cb2b402686fe395ce9a570f348222c4f2fb8c32d4ace898d5cc426',
    PREFIX + 'account_rpc/garage.py': '884353b854434b095c660e7de32b709b10685df0fe40a2d9a2936f20f1d2104b',
    PREFIX + 'lan_session.py': '51a19a9fd4c1364f25e3fa85496aabdc3b2b9295433a1310d69a96c46e6504e2',
    PREFIX + 'offline_services_ui.py': '8428b5676f186c588234f8f2f5ee2f55430d4ea54077bbc177dcda2aa95cca13',
    'tests/test_port_0922_account_rpc.py': '2226e186df3c4fa84cfd4fcb5e4f02e48f3d4737084892b37274b01ab0d0dc57',
    'tests/test_port_0922_effective_params.py': '1ff2b49a94ce46a839f345f512f1aaa598604b15e9d4763c5d208df9f46ef8e8',
    'tests/test_port_0922_garage.py': '03e10fb5568ca31982af43050176231ef62d60d2ece67ec83e9b42bc8cbcbd2b',
    'tests/test_port_0922_offline_services_ui.py': 'a4266d62bb991e090785293a8114c1c555ae7f7338d060b9e321a2a807a9af88',
}
RUNTIME = {p for p in EXPECTED if p.startswith('src/')}
ALLOWED = set(EXPECTED) | {PROOF, 'launcher/LAUNCHER_README.txt',
    'tools/exchange_crew_test_build.py', 'tools/exchange_crew_test.patch.b64',
    '.github/workflows/build-exchange-crew-test.yml'}
NORMAL_ROOT = {'wot-0.9.22-offline-battles.exe', '_internal', 'README.txt',
               'LICENSE', 'THIRD_PARTY_NOTICES.md', 'licenses'}


def git(*args):
    return subprocess.check_output(['git'] + list(args), cwd=str(ROOT))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, obj):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, sort_keys=True, indent=2) + '\n', encoding='utf-8')


def verify():
    changed = set(git('diff', '--name-only', BASE, '--').decode().splitlines())
    assert changed.issubset(ALLOWED), sorted(changed - ALLOWED)
    for path, expected in EXPECTED.items():
        assert sha((ROOT / path).read_bytes()) == expected, path
    old = json.loads(git('show', BASE + ':' + PROOF))
    proof = json.loads((ROOT / PROOF).read_bytes())
    for key in ('historical_source', 'base', 'variant', 'exact_historical_sha256',
                'intentional_exceptions', 'removed_paths'):
        assert proof[key] == old[key], key
    assert proof['variant'] == 'original' and not proof['removed_paths']
    assert set(proof['runtime_sha256_lf']) == set(old['runtime_sha256_lf'])
    changed_runtime = {p for p in proof['runtime_sha256_lf']
                       if proof['runtime_sha256_lf'][p] != old['runtime_sha256_lf'][p]}
    assert changed_runtime == RUNTIME, sorted(changed_runtime)
    import bot077_build
    bot077_build.audit()
    result = {'baseline': BASE, 'crew_pr_32_source': CREW,
              'runtime_changes': sorted(changed_runtime),
              'expected_patched_sha256': EXPECTED,
              'original_bot_and_41_maps_preserved': True,
              'native_windows_gameplay_tested': False,
              'credit_range_guard_flash_acceptance_pending': True}
    save('build-evidence/source-identity.json', result)
    print('PASS: exact combined source; only three runtime files changed; Bot and maps preserved.')
    return result


def prepare():
    subprocess.check_call(['git', 'merge-base', '--is-ancestor', BASE, 'HEAD'], cwd=str(ROOT))
    before = set(git('diff', '--name-only', BASE, '--').decode().splitlines())
    assert before.issubset(ALLOWED - set(EXPECTED) - {PROOF, 'launcher/LAUNCHER_README.txt'}), sorted(before)
    patch = zlib.decompress(base64.b64decode((ROOT / 'tools/exchange_crew_test.patch.b64').read_bytes()))
    assert sha(patch) == PATCH_SHA256, sha(patch)
    path = ROOT / 'build-evidence/combined-source.patch'
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(patch)
    subprocess.check_call(['git', 'apply', '--check', str(path)], cwd=str(ROOT))
    subprocess.check_call(['git', 'apply', str(path)], cwd=str(ROOT))
    proof = json.loads(git('show', BASE + ':' + PROOF))
    for name in RUNTIME:
        proof['runtime_sha256_lf'][name] = sha((ROOT / name).read_bytes().replace(b'\r\n', b'\n'))
        proof['preserved_runtime_sha256_lf'].pop(name, None)
    proof['changed_runtime'] = sorted(set(proof['changed_runtime']) | RUNTIME)
    proof['packaged_hotfix'] = {
        'name': 'exchange-crew-test-20260924', 'base': BASE, 'crew_pr_32_source': CREW,
        'runtime_paths': sorted(RUNTIME), 'native_gameplay_tested': False,
        'credit_range_guard_flash_acceptance_pending': True}
    save(PROOF, proof)
    readme = ROOT / 'launcher/LAUNCHER_README.txt'
    readme.write_bytes(readme.read_bytes() + (
        '\n\nExchange / crew TEST BUILD - 2026-09-24\n'
        'This is not the unchanged v0.9.4 release. No new release tag was created.\n'
        'Includes the zero-XP elite-notification fix, exchange-input range guard,\n'
        'and PR #32 lossless requalification / legacy disabled-skill correction.\n'
        'Original Bot077, mutual yielding, all 41 maps, armor and downhill rules are unchanged.\n'
        'The silver input field still requires confirmation in the original #1513 Flash UI.\n'
        'Existing saved zero-XP records are not deleted; no account reset is performed.\n'
        'Close the old launcher and game. Extract this whole ZIP to a NEW empty folder.\n'
        'Keep the EXE and _internal together; select your existing #1513 game directory.\n'
        'Use the same launcher save slot and profile. Back up important saves first.\n'
    ).encode('utf-8'))
    verify()
    subprocess.check_call(['git', 'add', '--'] + sorted(set(EXPECTED) | {PROOF, 'launcher/LAUNCHER_README.txt'}), cwd=str(ROOT))


def package(app_name):
    verify()
    app = Path(app_name).resolve()
    evidence = ROOT / 'build-evidence'
    evidence.mkdir(exist_ok=True)
    ready = json.loads((evidence / 'server-readiness.json').read_bytes())
    assert ready['ready'] is True
    source = git('rev-parse', 'HEAD').decode().strip()
    assert source == os.environ['FULL_BOT080_SOURCE_SHA']
    receipt = json.loads((app / 'FULL_BOT080_BUILD_EVIDENCE.json').read_bytes())
    assert receipt['source_commit'] == source and receipt['fake_install_passed']
    for p in list(app.iterdir()):
        if p.name not in NORMAL_ROOT:
            assert p.is_file(), str(p)
            shutil.move(str(p), str(evidence / p.name))
    assert {p.name for p in app.iterdir()} == NORMAL_ROOT
    assert (app / 'README.txt').read_bytes() == (ROOT / 'launcher/LAUNCHER_README.txt').read_bytes()
    with zipfile.ZipFile(str(app / '_internal/client/0.9.22.zip')) as client:
        assert client.testzip() is None
        identity = json.loads(client.read('mods/configs/offline_lan_0922/build_identity.json'))
        assert identity['semanticVersion'] == '0.9.4'
        assert identity['buildIdentity'] == os.environ['WOT_OFFLINE_BUILD_IDENTITY']
        mods = [p for p in client.namelist() if p.endswith('.wotmod')]
        assert len(mods) == 1 and mods[0].endswith('_0.9.4.wotmod')
        with zipfile.ZipFile(io.BytesIO(client.read(mods[0]))) as mod:
            assert mod.testzip() is None
            for name in RUNTIME:
                assert name[len('src/'):] + 'c' in mod.namelist(), name
    with (app / 'README.txt').open('a', encoding='utf-8') as f:
        f.write('\nSource: ' + source + '\nBuild: ' + identity['buildIdentity'] + '\n')
    archive = ROOT / PACKAGE
    with zipfile.ZipFile(str(archive), 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(app.rglob('*')):
            if path.is_file():
                z.write(str(path), path.relative_to(app).as_posix())
    with zipfile.ZipFile(str(archive)) as z:
        assert z.testzip() is None
        assert {n.split('/')[0] for n in z.namelist()} == NORMAL_ROOT
    digest = sha(archive.read_bytes())
    (ROOT / (PACKAGE + '.sha256')).write_text(digest + '  ' + PACKAGE + '\n', encoding='ascii')
    shutil.copyfile(str(ROOT / 'dist/full-bot080-bytecode.json'), str(evidence / 'compiled-bytecode.json'))
    save('build-evidence/package-manifest.json', {
        'package': PACKAGE, 'sha256': digest, 'bytes': archive.stat().st_size,
        'source': source, 'build_identity': identity, 'run_id': os.environ['GITHUB_RUN_ID'],
        'baseline': BASE, 'crew_pr_32_source': CREW, 'server_readiness': ready,
        'fake_install_passed': True, 'native_windows_gameplay_tested': False,
        'original_bot_and_maps_preserved': True,
        'credit_range_guard_flash_acceptance_pending': True,
        'main_modified': False, 'tag_created': False, 'release_published': False})
    print('PASS: clean flat Windows test ZIP, ' + digest)


if __name__ == '__main__':
    os.chdir(str(ROOT))
    if sys.argv[1:] == ['prepare']:
        prepare()
    elif sys.argv[1:] == ['verify']:
        verify()
    elif len(sys.argv) == 3 and sys.argv[1] == 'package':
        package(sys.argv[2])
    else:
        raise SystemExit('Usage: exchange_crew_test_build.py prepare|verify|package APP_ROOT')
