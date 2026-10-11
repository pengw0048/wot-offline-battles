from __future__ import print_function
"""Build verification, not a native World of Tanks gameplay test."""
import copy
import glob
import hashlib
import io
import json
import marshal
import math
import os
import shutil
import subprocess
import sys
import tempfile
import types
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPH_SHA = '5c981f89f2ab2f36058cf8835e3ea9a666587d15521227a2e121e02c5d0a737f'
PROOF_PATH = 'docs/testing/airfield-build-inputs-20260923.json'

def read(path):
    with open(path, 'rb') as stream:
        return stream.read()

def sha(data):
    return hashlib.sha256(data).hexdigest()

def save(path, obj):
    with open(path, 'wb') as stream:
        stream.write((json.dumps(obj, sort_keys=True, indent=2) + '\n').encode('utf-8'))

def normalized_code(value):
    if isinstance(value, types.CodeType):
        return tuple(getattr(value, k) for k in (
            'co_argcount', 'co_nlocals', 'co_stacksize', 'co_flags',
            'co_code', 'co_names', 'co_varnames', 'co_freevars', 'co_cellvars',
            'co_filename', 'co_firstlineno', 'co_lnotab')) + (
                tuple(normalized_code(item) for item in value.co_consts),)
    if isinstance(value, tuple):
        return tuple(normalized_code(item) for item in value)
    return value

def bytecode():
    assert sys.version_info[:2] == (2, 7), sys.version
    proof = json.loads(read(os.path.join(ROOT, PROOF_PATH)))
    packages = glob.glob(os.path.join(ROOT, 'dist', '*.wotmod'))
    assert len(packages) == 1, packages
    package = packages[0]
    verified = []
    unpacked = tempfile.mkdtemp(prefix='airfield-compiled-test-')
    try:
        with zipfile.ZipFile(package) as archive:
            assert archive.testzip() is None
            for name in archive.namelist():
                assert not name.endswith('.py'), name
            for source, expected_hash in sorted(proof['runtime_source_sha256_lf'].items()):
                data = read(os.path.join(ROOT, *source.split('/'))).replace(b'\r\n', b'\n')
                assert sha(data) == expected_hash, source
                member = source[len('src/'):] + 'c'
                pyc = archive.read(member)
                assert pyc[:4] == b'\x03\xf3\r\n', member
                actual = marshal.loads(pyc[8:])
                expected = compile(data, actual.co_filename, 'exec', 0, True)
                assert normalized_code(actual) == normalized_code(expected), member
                verified.append({'module': member, 'source_sha256_lf': expected_hash, 'pyc_sha256': sha(pyc)})
            for name in archive.namelist():
                assert not name.startswith('/') and '..' not in name.split('/'), name
            archive.extractall(unpacked)
        sys.path.insert(0, os.path.join(unpacked, 'res', 'scripts', 'client'))
        # WoT owns these parent packages. Supply only their search paths in
        # this isolated CPython 2.7 test; do not alter the distributed mod.
        for package_name, relative in (('gui', 'gui'), ('gui.mods', 'gui/mods')):
            package_stub = types.ModuleType(package_name)
            package_stub.__path__ = [os.path.join(unpacked, 'res', 'scripts', 'client', *relative.split('/'))]
            sys.modules[package_name] = package_stub
        sys.modules['gui'].mods = sys.modules['gui.mods']
        from gui.mods.offline_lan_0922.ai.navigation import TerrainGrid
        graph = json.loads(read(os.path.join(ROOT, 'navgraphs', '31_airfield.json')))
        calls = []
        def no_native(*args):
            calls.append(args)
            raise AssertionError('Compiled navigation attempted native static reconstruction')
        grid = TerrainGrid(no_native, no_native, baked_graph=copy.deepcopy(graph))
        grid.set_destructible_regions([(-500, 500, -500, 500)])
        assert grid._static_topology_complete
        assert not grid._soft_graph_cells and not grid._native_review_cells
        paths = []
        for team in (1, 2):
            gx, gz = graph['spawn_anchors'][2 - team]
            for slot, pose in enumerate(graph['spawn_formations'][str(team)]):
                path = grid.plan(tuple(pose[:3]), (gx, 0.0, gz), max_expansions=20000)
                assert path and math.hypot(path[-1][0] - gx, path[-1][2] - gz) < 4.1, (team, slot)
                paths.append({'team': team, 'slot': slot, 'waypoints': len(path)})
        assert not calls and not grid._live_heights and not grid._live_links
        assert grid._live_edge_count == 0
        assert grid._baked_heights == graph['heights_mm'] and grid._baked_links == graph['links']
        result = {'python': sys.version, 'bytecode_magic': '03f30d0a', 'verified_modules': verified,
                  'compiled_cold_paths': paths, 'native_calls': len(calls), 'restored_edges': grid._live_edge_count,
                  'airfield_sha256': GRAPH_SHA, 'wotmod_sha256': sha(read(package)), 'gameplay_tested': False}
        review_output = os.path.join(ROOT, 'dist', 'airfield-review-entry-verification.json')
        subprocess.check_call([sys.executable, os.path.join(ROOT, 'tools',
            'test_airfield_review_gate.py'), '--package', '--output', review_output], cwd=ROOT)
        review = json.loads(read(review_output))
        assert review['passed'] and review['mode'] == 'bytecode'
        assert review['wotmod_sha256'] == result['wotmod_sha256']
        result['review_entry_verification'] = review
        save(os.path.join(ROOT, 'dist', 'airfield-bytecode-verification.json'), result)
        print('PASS: %d exact CPython 2.7 modules and %d compiled cold paths; no native repairs.' % (len(verified), len(paths)))
    finally:
        shutil.rmtree(unpacked)

def distribution(app_root):
    app_root = os.path.abspath(app_root)
    proof = json.loads(read(os.path.join(ROOT, PROOF_PATH)))
    compiled = json.loads(read(os.path.join(ROOT, 'dist', 'airfield-bytecode-verification.json')))
    exe = os.path.join(app_root, 'wot-0.9.22-offline-battles.exe')
    assert os.path.isfile(exe) and read(exe)[:2] == b'MZ'
    payload = os.path.join(app_root, '_internal')
    with zipfile.ZipFile(os.path.join(payload, 'client', '0.9.22.zip')) as client:
        assert client.testzip() is None
        config = 'mods/configs/offline_lan_0922/'
        graph_data = client.read(config + 'navgraphs/31_airfield.json')
        assert sha(graph_data) == GRAPH_SHA
        graph = json.loads(graph_data)
        assert graph['bake']['navigation_collision_policy'] == 'compiled-vehicle-surfaces-no-original-destructibles-v1'
        assert graph['bake']['original_destructible_surfaces_excluded'] is True
        manifest_data = client.read(config + 'navgraphs/manifest.json')
        assert manifest_data == read(os.path.join(ROOT, 'navgraphs', 'manifest.json'))
        manifest = json.loads(manifest_data)
        assert len(manifest['maps']) == 41
        for rec in manifest['maps']:
            assert sha(client.read(config + 'navgraphs/' + rec['file'])) == rec['sha256'], rec
        mods = [name for name in client.namelist() if name.endswith('.wotmod')]
        assert len(mods) == 1
        wotmod = client.read(mods[0])
        assert sha(wotmod) == compiled['wotmod_sha256']
        with zipfile.ZipFile(io.BytesIO(wotmod)) as archive:
            for rec in compiled['verified_modules']:
                assert sha(archive.read(rec['module'])) == rec['pyc_sha256'], rec['module']
        identity = json.loads(client.read(config + 'build_identity.json'))
        assert identity['buildIdentity'] == os.environ['WOT_OFFLINE_BUILD_IDENTITY'], identity
    for rel, expected_hash in proof['runtime_source_sha256_lf'].items():
        source = os.path.join(payload, 'servers', '0.9.22', *rel.split('/'))
        assert sha(read(source).replace(b'\r\n', b'\n')) == expected_hash, rel
    # Exercise the real launcher installation path in a disposable fake client.
    sys.path.insert(0, os.path.join(ROOT, 'launcher'))
    import core
    fake = tempfile.mkdtemp(prefix='airfield-install-test-')
    try:
        with open(os.path.join(fake, 'WorldOfTanks.exe'), 'wb') as stream:
            stream.write(b'')
        with open(os.path.join(fake, 'version.xml'), 'wb') as stream:
            stream.write(b'<version> v.0.9.22.0.1 #1513 </version>')
        assert core.inspect_game_root(fake)['client'] == core.PORT_0_9_22
        core.install_client_mod(fake, core.PORT_0_9_22, base_dir=payload)
        assert core._installation_complete(fake, core.PORT_0_9_22, core._CLIENT_INSTALL[core.PORT_0_9_22])
        assert sha(read(os.path.join(fake, 'mods', 'configs', 'offline_lan_0922', 'navgraphs', '31_airfield.json'))) == GRAPH_SHA
    finally:
        shutil.rmtree(fake)
    result = {'source_commit': os.environ['AIRFIELD_SOURCE_SHA'], 'build_identity': identity,
              'github_run_id': os.environ['GITHUB_RUN_ID'], 'launcher_sha256': sha(read(exe)),
              'airfield_sha256': GRAPH_SHA, 'manifest_sha256': sha(manifest_data),
              'bytecode_verification': compiled, 'source_provenance': proof,
              'fake_client_install_passed': True, 'native_gameplay_tested': False}
    review = compiled['review_entry_verification']
    assert review['passed'] and review['tests_run'] == 7 and len(review['paths']) == 30
    assert review['wotmod_sha256'] == sha(wotmod)
    result['review_entry_verification'] = review
    save(os.path.join(app_root, 'AIRFIELD_REVIEW_ENTRY_TESTS.json'), review)
    with open(os.path.join(app_root, 'REVIEW_GATE_TEST_NOTES.txt'), 'wb') as stream:
        stream.write(b'Airfield review-gate follow-up TEST BUILD.\n'
            b'Based on 5e2ee54b; only ai/navigation.py changes at runtime (six added lines).\n'
            b'The existing clean Airfield graph no longer enters legacy broad native edge review.\n'
            b'Other maps, final physical collisions, traffic and recovery logic are unchanged.\n'
            b'Source and compiled production-entry tests are not native driving tests.\n'
            b'Tiger/north-side stalls and performance still require Windows gameplay testing.\n')
    save(os.path.join(app_root, 'AIRFIELD_BUILD_EVIDENCE.json'), result)
    print('PASS: real distribution contains the verified bytecode, all 139 source modules, 41 graph checksums and correct build identity; fake install passed.')

if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] == 'bytecode':
        bytecode()
    elif len(sys.argv) == 3 and sys.argv[1] == 'distribution':
        distribution(sys.argv[2])
    else:
        raise SystemExit('Usage: verify_airfield_test_build.py bytecode | distribution APP_ROOT')
