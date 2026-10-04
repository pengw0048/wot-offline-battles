from __future__ import print_function
"""Restore exact v0.8.0 Bot-owned source/assets; verify the resulting test package.

Shared game physics, native scene adapters and non-Bot features are retained.
This tool does not claim that a hybrid package equals the entire v0.8.0 game.
"""
import ast
import copy
import glob
import hashlib
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST = '5a7c20a124ab8ad76dc40db3012a31436ae6905b'
BASE = 'e41867d739c18373c6049f9d1fb9b826bdc6f27e'
PREFIX = 'src/res/scripts/client/gui/mods/offline_lan_0922/'
PROOF = 'docs/testing/full-bot080-proof.json'
EXACT_ROOTS = (PREFIX + 'ai/', 'navgraphs/', 'foliage/', 'destructibles/')
EXACT_FILES = (PREFIX + 'bot_runtime.py', PREFIX + 'bot_gunnery.py',
               PREFIX + 'bot_state_codec.py', PREFIX + 'prebaked_navigation.py',
               'server/server_bot_ai.py', 'launcher/bot_lineup_profiles.py',
               'launcher/bot_lineup_ui.py')

def read(path):
    with open(path, 'rb') as f:
        return f.read()

def write(path, data):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, 'wb') as f:
        f.write(data)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def save(path, obj):
    write(path, (json.dumps(obj, sort_keys=True, indent=2) + '\n').encode('utf-8'))

def git(*args):
    return subprocess.check_output(['git'] + list(args), cwd=ROOT)

def historical_paths():
    names = git('ls-tree', '-r', '--name-only', HIST).decode('utf-8').splitlines()
    return sorted(n for n in names if n in EXACT_FILES or n.startswith(EXACT_ROOTS))

def runtime_hashes():
    result = {}
    for folder, unused, files in os.walk(os.path.join(ROOT, 'src', 'res', 'scripts', 'client')):
        for name in files:
            if name.endswith('.py'):
                p = os.path.join(folder, name)
                result[os.path.relpath(p, ROOT).replace(os.sep, '/')] = sha(read(p).replace(b'\r\n', b'\n'))
    return result

def integrate():
    subprocess.check_call(['git', 'merge-base', '--is-ancestor', BASE, 'HEAD'], cwd=ROOT)
    paths = historical_paths()
    current = git('ls-tree', '-r', '--name-only', 'HEAD').decode('utf-8').splitlines()
    for n in current:
        if n.startswith(EXACT_ROOTS) and n not in paths:
            os.remove(os.path.join(ROOT, n))
    hashes = {}
    for n in paths:
        data = git('show', HIST + ':' + n)
        write(os.path.join(ROOT, n), data)
        hashes[n] = sha(data)
    # Current BattleRuntime supplies two callbacks absent from the original
    # BotRuntime constructor. Remove those injected arguments, not old code.
    name = PREFIX + 'battle_runtime.py'
    text = git('show', BASE + ':' + name).decode('utf-8')
    for line in ('                rotation_resolver=self._resolve_bot_rotation,\n',
                 '                water_hull_pose=self._bot_water_hull_pose,\n'):
        assert text.count(line) == 1, line
        text = text.replace(line, '')
    write(os.path.join(ROOT, name), text.encode('utf-8'))
    changed = []
    for n in paths + [name]:
        previous = git('show', BASE + ':' + n)
        current_data = read(os.path.join(ROOT, n))
        if previous != current_data:
            changed.append(n)
    proof = {'base': BASE, 'historical_source': HIST, 'exact_historical_sha256': hashes,
             'runtime_sha256_lf': runtime_hashes(), 'changed_paths': sorted(changed),
             'retained_boundary': 'Current shared physics, native scene adapters, protocol and non-Bot features.',
             'native_gameplay_tested': False}
    save(os.path.join(ROOT, PROOF), proof)
    subprocess.check_call(['git', 'add', '--'] + list(EXACT_ROOTS) + list(EXACT_FILES) + [name, PROOF], cwd=ROOT)
    print('Restored %d exact historical files; %d changed against the tested base.' % (len(paths), len(changed)))

def audit():
    proof = json.loads(read(os.path.join(ROOT, PROOF)))
    assert proof['historical_source'] == HIST and proof['base'] == BASE
    for n, expected in proof['exact_historical_sha256'].items():
        assert sha(read(os.path.join(ROOT, n))) == expected, n
    assert runtime_hashes() == proof['runtime_sha256_lf']
    for n, record in proof.get('radio_visibility_fix', {}).get('intentional_exceptions', {}).items():
        assert sha(read(os.path.join(ROOT, n)).replace(b'\r\n', b'\n')) == record['fixed_sha256_lf'], n
    # No caller can accidentally pass a callback unsupported by the exact
    # historical class. Check the actual production constructor, not a stub.
    tree = ast.parse(read(os.path.join(ROOT, PREFIX + 'bot_runtime.py')))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'BotRuntime')
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
    args = set(getattr(n, 'arg', getattr(n, 'id', '')) for n in init.args.args)
    boundary = ast.parse(read(os.path.join(ROOT, PREFIX + 'battle_runtime.py')))
    calls = [n for n in ast.walk(boundary) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'BotRuntime']
    assert len(calls) == 1
    assert set(n.arg for n in calls[0].keywords).issubset(args)
    print('PASS exact historical files, all runtime hashes and production constructor signature.')
    return proof

def source_tests():
    import contextlib
    import unittest
    audit()
    sys.path[:0] = [os.path.join(ROOT, 'tests'), os.path.join(ROOT, 'server'), os.path.join(ROOT, 'tools')]
    def load_original(name):
        path = 'tests/' + name + '.py'
        module = types.ModuleType('historical_' + name)
        module.__file__ = os.path.join(ROOT, path)
        exec(compile(git('show', HIST + ':' + path), module.__file__, 'exec'), module.__dict__)
        return module
    ai = load_original('test_port_0922_ai')
    runtime = load_original('test_port_0922_bot_runtime')
    # Preserve the historical behavior assertions. Only the test's human
    # snapshot gets the mandatory field that the retained current client sends.
    old_fixture = runtime._effective_params_snapshot
    def current_fixture(*args, **kwargs):
        row = old_fixture(*args, **kwargs)
        row['physics']['rotationIsAroundCenter'] = True
        return row
    runtime._effective_params_snapshot = current_fixture
    names = ['test_decision_and_copied_motion_reuse_installed_physics',
        'test_direction_probe_receives_speed_and_descriptor_contract',
        'test_baked_planner_clear_cannot_bypass_native_selected_wall',
        'test_hard_final_world_receipt_blocks_the_selected_motion',
        'test_bot_soft_motion_contact_preserves_speed_without_moving',
        'test_realised_hard_contact_invalidates_cached_command_and_probe',
        'test_bot_drowning_requires_ten_continuous_seconds_and_publishes_death',
        'test_bot_drive_uses_contacted_plane_instead_of_corridor_grade',
        'test_initial_and_restored_manifests_share_physics_installation',
        'test_injected_baked_graph_replaces_runtime_grid_and_passes_routes',
        'test_worker_stall_refreshes_control_once_and_consumes_all_elapsed',
        'test_worker_low_fps_reuses_valid_drive_and_moves_continuously',
        'test_worker_four_fps_keeps_turning_drive_active_between_plans',
        'test_reverse_recovery_uses_driver_turn_sign_not_target_bearing',
        'test_driver_proportional_turn_is_not_collapsed_to_keyboard_sign',
        'test_the_manifest_publishes_each_bot_gunnery_tier',
        'test_bot_physics_uses_plain_default_crew_factors',
        'test_high_fps_contact_lease_pays_the_consumed_physical_time',
        'test_baked_planner_ranking_never_replaces_selected_native_gate',
        'test_traffic_wait_does_not_enter_reverse_recovery',
        'test_runtime_caches_one_traffic_decision_with_the_planner_command',
        'test_unavailable_motion_probe_holds_last_drive_command',
        'test_route_lane_narrows_around_bot_local_contact_penalty',
        'test_driver_receives_native_collision_dimensions_and_velocity',
        'test_motion_admits_dry_edges_but_keeps_water_and_shore_veto',
        'test_worker_fixed_control_tracks_wall_time_from_five_to_one_fps',
        'test_server_macro_order_drives_local_adapter_with_human_id_mapping',
        'test_radio_movement_orders_drive_toward_the_commanded_goal',
        'test_radio_stop_reaches_the_driver_as_zero_throttle']
    suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromModule(ai),
                               unittest.TestSuite(runtime.BotRuntimeTests(n) for n in names)])
    with open(os.devnull, 'w') as noise, contextlib.redirect_stdout(noise):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)

def normalized_code(value):
    if isinstance(value, types.CodeType):
        names = ('co_argcount', 'co_nlocals', 'co_stacksize', 'co_flags', 'co_code',
                 'co_names', 'co_varnames', 'co_freevars', 'co_cellvars', 'co_filename',
                 'co_firstlineno', 'co_lnotab')
        return tuple(getattr(value, n) for n in names) + (tuple(normalized_code(n) for n in value.co_consts),)
    if isinstance(value, tuple):
        return tuple(normalized_code(n) for n in value)
    return value

def bytecode():
    import marshal
    import zipfile
    assert sys.version_info[:2] == (2, 7), sys.version
    proof = audit()
    packages = glob.glob(os.path.join(ROOT, 'dist', '*.wotmod'))
    assert len(packages) == 1, packages
    package = packages[0]
    tmp = tempfile.mkdtemp(prefix='full-bot080-compiled-')
    verified = []
    try:
        with zipfile.ZipFile(package) as z:
            assert z.testzip() is None
            assert not any(n.endswith('.py') for n in z.namelist())
            for source, expected in sorted(proof['runtime_sha256_lf'].items()):
                member = source[len('src/'):] + 'c'
                data = read(os.path.join(ROOT, source)).replace(b'\r\n', b'\n')
                pyc = z.read(member)
                assert sha(data) == expected and pyc[:4] == b'\x03\xf3\r\n', member
                actual = marshal.loads(pyc[8:])
                assert normalized_code(actual) == normalized_code(compile(data, actual.co_filename, 'exec', 0, True)), member
                verified.append({'member': member, 'sha256': sha(pyc)})
            for n in z.namelist():
                assert not n.startswith('/') and '..' not in n.split('/'), n
            z.extractall(tmp)
        client = os.path.join(tmp, 'res', 'scripts', 'client')
        sys.path.insert(0, client)
        for name, rel in (('gui', 'gui'), ('gui.mods', 'gui/mods')):
            m = types.ModuleType(name)
            m.__path__ = [os.path.join(client, *rel.split('/'))]
            sys.modules[name] = m
        sys.modules['gui'].mods = sys.modules['gui.mods']
        from gui.mods.offline_lan_0922.bot_runtime import BotRuntime
        from gui.mods.offline_lan_0922.ai.navigation import TerrainGrid
        BotRuntime(1)
        calls = []
        def no_native(*args):
            calls.append(args)
            raise AssertionError('Unexpected native grid reconstruction')
        graph = json.loads(read(os.path.join(ROOT, 'navgraphs/31_airfield.json')))
        grid = TerrainGrid(no_native, no_native, baked_graph=copy.deepcopy(graph))
        assert not hasattr(grid, 'review_native_corridor')
        paths = []
        for team in (1, 2):
            gx, gz = graph['spawn_anchors'][2 - team]
            for slot, pose in enumerate(graph['spawn_formations'][str(team)]):
                path = grid.plan(tuple(pose[:3]), (gx, 0.0, gz), max_expansions=20000)
                assert path, (team, slot)
                paths.append({'team': team, 'slot': slot, 'waypoints': len(path),
                              'distance_to_requested_anchor': math.hypot(path[-1][0]-gx, path[-1][2]-gz)})
        assert not calls
        assert grid._baked_heights == graph['heights_mm'] and grid._baked_links == graph['links']
        result = {'python': sys.version, 'verified_modules': verified, 'wotmod_sha256': sha(read(package)),
                  'original_airfield_plan_smoke': paths, 'native_queries': len(calls),
                  'path_test_scope': 'Nonempty original search results, including historical partial paths; not native driving or all-goal reachability.',
                  'native_gameplay_tested': False}
        save(os.path.join(ROOT, 'dist/full-bot080-bytecode.json'), result)
        print('PASS %d exact compiled modules, original BotRuntime construction and 30 original-map search smoke checks.' % len(verified))
    finally:
        shutil.rmtree(tmp)

def distribution(app_root):
    import zipfile
    app_root = os.path.abspath(app_root)
    proof = audit()
    compiled = json.loads(read(os.path.join(ROOT, 'dist/full-bot080-bytecode.json')))
    payload = os.path.join(app_root, '_internal')
    exe = os.path.join(app_root, 'wot-0.9.22-offline-battles.exe')
    assert read(exe)[:2] == b'MZ'
    with zipfile.ZipFile(os.path.join(payload, 'client', '0.9.22.zip')) as client:
        assert client.testzip() is None
        cfg = 'mods/configs/offline_lan_0922/'
        identity = json.loads(client.read(cfg + 'build_identity.json'))
        assert identity['buildIdentity'] == os.environ['WOT_OFFLINE_BUILD_IDENTITY']
        for path, expected in proof['exact_historical_sha256'].items():
            if path.startswith(('navgraphs/', 'foliage/', 'destructibles/')):
                assert sha(client.read(cfg + path)) == expected, path
        manifest = json.loads(client.read(cfg + 'navgraphs/manifest.json'))
        assert len(manifest['maps']) == 41
        for item in manifest['maps']:
            assert sha(client.read(cfg+'navgraphs/'+item['file'])) == item['sha256']
        mods = [n for n in client.namelist() if n.endswith('.wotmod')]
        assert len(mods) == 1
        data = client.read(mods[0])
        assert sha(data) == compiled['wotmod_sha256']
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for rec in compiled['verified_modules']:
                assert sha(z.read(rec['member'])) == rec['sha256']
    for path, expected in proof['runtime_sha256_lf'].items():
        # The entry loader belongs only to the client wotmod, whose exact
        # compiled bytes were already verified above; it is not a server import.
        if path == 'src/res/scripts/client/gui/mods/mod_offline_lan_0922.py':
            continue
        assert path.startswith(PREFIX), path
        assert sha(read(os.path.join(payload, 'servers/0.9.22', path)).replace(b'\r\n', b'\n')) == expected, path
    server = 'server/server_bot_ai.py'
    assert sha(read(os.path.join(payload, 'servers/0.9.22', server)).replace(b'\r\n', b'\n')) == sha(read(os.path.join(ROOT, server)).replace(b'\r\n', b'\n'))
    sys.path.insert(0, os.path.join(ROOT, 'launcher'))
    import core
    tmp = tempfile.mkdtemp(prefix='full-bot080-install-')
    try:
        write(os.path.join(tmp, 'WorldOfTanks.exe'), b'')
        write(os.path.join(tmp, 'version.xml'), b'<version> v.0.9.22.0.1 #1513 </version>')
        core.install_client_mod(tmp, core.PORT_0_9_22, base_dir=payload)
        assert core._installation_complete(tmp, core.PORT_0_9_22, core._CLIENT_INSTALL[core.PORT_0_9_22])
        assert sha(read(os.path.join(tmp, 'mods/configs/offline_lan_0922/navgraphs/31_airfield.json'))) == proof['exact_historical_sha256']['navgraphs/31_airfield.json']
    finally:
        shutil.rmtree(tmp)
    save(os.path.join(app_root, 'FULL_BOT080_BUILD_EVIDENCE.json'),
         {'source_commit': os.environ['FULL_BOT080_SOURCE_SHA'], 'run_id': os.environ['GITHUB_RUN_ID'],
          'build_identity': identity, 'proof': proof, 'compiled': compiled,
          'launcher_sha256': sha(read(exe)), 'fake_install_passed': True, 'native_gameplay_tested': False})
    print('PASS final bytecode, historical map/catalog bytes, runtime source and launcher installation.')

if __name__ == '__main__':
    action = sys.argv[1] if len(sys.argv) > 1 else ''
    if action == 'integrate':
        integrate()
    elif action == 'source-tests':
        source_tests()
    elif action == 'bytecode':
        bytecode()
    elif action == 'distribution' and len(sys.argv) == 3:
        distribution(sys.argv[2])
    else:
        raise SystemExit('Usage: full_bot080_build.py integrate|source-tests|bytecode|distribution APP_ROOT')
