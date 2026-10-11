from __future__ import print_function
"""Build/verify the pinned ramming-contact follow-up without retuning damage."""
import json
import os
import shutil
import sys
import zipfile
try:
    from pathlib import Path
except ImportError:  # Python 2.7 bytecode verification imports this module
    Path = None

import full_bot080_build as common

BASE = '10e08f800d093d0df1eee16344737defcde38fcd'
ROOT = common.ROOT
PREFIX = common.PREFIX
PRODUCTION = [
    'src/res/scripts/client/gui/mods/offline_lan_0922/tank_collision.py',
    'src/res/scripts/client/gui/mods/offline_lan_0922/battle_runtime.py',
    'src/res/scripts/client/gui/mods/offline_lan_0922/bot_runtime.py',
]
SUPPORT = {
    'tools/apply_ram_contact_repair.py',
    'tools/ram_contact_build.py',
    'tests/test_port_0922_ram_contact_followup.py',
    'docs/testing/094-ram-contact-followup.md',
    '.github/workflows/build-094-ram-contact-test.yml',
}
ALLOWED = set(PRODUCTION) | SUPPORT
PACKAGE = 'wot-0.9.22-offline-battles-0.9.4-ram-contact-test-20260924-Windows-x64.zip'

MARKERS = {
    PRODUCTION[0]: (
        'def ram_contact_sample_heights(',
        'def post_contact_velocity_bodies(',
        'RAM_DAMAGE_COEFFICIENT = 0.25',
    ),
    PRODUCTION[1]: (
        'def _native_ram_contact_plate_pair(',
        "'contact_y_span': contact_y_span",
        'post_contact_velocity_bodies(',
    ),
    PRODUCTION[2]: (
        'traverse_bodies = tank_collision.post_contact_velocity_bodies(',
    ),
}


def _lf(path):
    return common.read(os.path.join(ROOT, path)).replace(b'\r\n', b'\n')


def verify():
    changed = set(common.git('diff', '--name-only', BASE, '--').decode().splitlines())
    assert changed.issubset(ALLOWED), sorted(changed - ALLOWED)
    assert set(PRODUCTION).issubset(changed), sorted(set(PRODUCTION) - changed)
    for path, markers in MARKERS.items():
        text = _lf(path).decode('utf8')
        for marker in markers:
            assert marker in text, (path, marker)
    # The follow-up must not alter the already accepted global ram curve.
    collision = _lf(PRODUCTION[0]).decode('utf8')
    assert collision.count('RAM_DAMAGE_COEFFICIENT = 0.25') == 1

    previous = json.loads(common.git('show', BASE + ':docs/testing/bot077-proof.json'))
    proof = dict(previous)
    proof['repair_baseline'] = BASE
    proof['repair_paths'] = PRODUCTION
    proof['runtime_sha256_lf'] = common.runtime_hashes()
    proof['native_gameplay_tested'] = False
    proof['accepted_ram_curve_retuned'] = False
    # Every runtime file outside the three repaired contact owners must remain
    # byte-identical to the preceding SPG-targeting package.
    for path, digest in proof['runtime_sha256_lf'].items():
        if path in PRODUCTION:
            continue
        old = common.git('show', BASE + ':' + path).replace(b'\\r\\n', b'\\n')
        assert digest == common.sha(old), path
    for path in ('ai/driver.py', 'ai/traffic.py', 'ai/adapter.py'):
        current = common.read(os.path.join(ROOT, PREFIX + path)).replace(b'\r\n', b'\n')
        old = common.git('show', BASE + ':' + PREFIX + path).replace(b'\r\n', b'\n')
        assert current == old, path
    common.save(os.path.join(ROOT, 'build-evidence/ram-contact-source-proof.json'), proof)
    print('PASS ram-contact follow-up; damage coefficient retained; prior SPG/match/radio fixes preserved.')
    return proof


def package(app_name):
    import io
    verify()
    app = Path(app_name)
    evidence = Path(ROOT) / 'build-evidence'
    evidence.mkdir(exist_ok=True)
    ready = json.loads((evidence / 'server-readiness.json').read_bytes())
    assert ready['ready'] is True
    source = common.git('rev-parse', 'HEAD').decode().strip()
    assert source == os.environ['FULL_BOT080_SOURCE_SHA']
    receipt = json.loads((app / 'FULL_BOT080_BUILD_EVIDENCE.json').read_bytes())
    assert receipt['fake_install_passed'] and receipt['source_commit'] == source

    allowed = {
        'wot-0.9.22-offline-battles.exe', '_internal', 'README.txt',
        'LICENSE', 'THIRD_PARTY_NOTICES.md', 'licenses'
    }
    for path in list(app.iterdir()):
        if path.name not in allowed:
            assert path.is_file(), path
            shutil.move(str(path), str(evidence / path.name))
    assert {p.name for p in app.iterdir()} == allowed

    with zipfile.ZipFile(str(app / '_internal/client/0.9.22.zip')) as client:
        assert client.testzip() is None
        identity = json.loads(client.read(
            'mods/configs/offline_lan_0922/build_identity.json'))
        assert identity['semanticVersion'] == '0.9.4'
        assert identity['buildIdentity'] == os.environ['WOT_OFFLINE_BUILD_IDENTITY']
        mods = [n for n in client.namelist() if n.endswith('.wotmod')]
        assert len(mods) == 1
        with zipfile.ZipFile(io.BytesIO(client.read(mods[0]))) as mod:
            assert mod.testzip() is None
            for path in PRODUCTION:
                assert path[len('src/'):] + 'c' in mod.namelist(), path

    with (app / 'README.txt').open('a', encoding='utf8') as f:
        f.write(
            '\nRamming/contact test: recover a shared native structural damage point '
            'inside the contact span and feed solved normal velocity into traverse.\n'
            'The accepted 0.25 ram damage coefficient is intentionally unchanged.\n'
            'Source: ' + source + '\nBuild: ' + identity['buildIdentity'] + '\n')

    archive = Path(ROOT) / PACKAGE
    with zipfile.ZipFile(str(archive), 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(app.rglob('*')):
            if path.is_file():
                z.write(str(path), path.relative_to(app).as_posix())
    with zipfile.ZipFile(str(archive)) as z:
        assert z.testzip() is None
        assert {p.split('/')[0] for p in z.namelist()} == allowed
    digest = common.sha(archive.read_bytes())
    (Path(ROOT) / (PACKAGE + '.sha256')).write_text(
        digest + '  ' + PACKAGE + '\n')
    shutil.copyfile(
        str(Path(ROOT) / 'dist/full-bot080-bytecode.json'),
        str(evidence / 'bytecode.json'))
    common.save(str(evidence / 'package-manifest.json'), {
        'source': source,
        'baseline': BASE,
        'package': PACKAGE,
        'sha256': digest,
        'bytes': archive.stat().st_size,
        'identity': identity,
        'server_readiness': ready,
        'fake_install_passed': True,
        'native_gameplay_tested': False,
        'production_changes': PRODUCTION,
        'ram_damage_curve_retuned': False,
    })
    print('PACKAGE', PACKAGE, digest, archive.stat().st_size)


if __name__ == '__main__':
    common.audit = verify
    action = sys.argv[1]
    if action == 'verify':
        verify()
    elif action == 'bytecode':
        common.bytecode()
    elif action == 'distribution':
        common.distribution(sys.argv[2])
    elif action == 'package':
        package(sys.argv[2])
    else:
        raise SystemExit('Unknown action')
