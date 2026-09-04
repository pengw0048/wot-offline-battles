from __future__ import print_function

import compileall
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
import zipfile


_SCHEMA_ROOT = os.path.join(
    os.path.dirname(__file__), 'src', 'res', 'scripts', 'client', 'gui',
    'mods', 'offline_lan_0922')
if _SCHEMA_ROOT not in sys.path:
    sys.path.insert(0, _SCHEMA_ROOT)
import navigation_graph_schema as _navigation_schema


MOD_ID = 'org.peng.offline_lan_0922'
MOD_VERSION = '0.6.5'
BUILD_IDENTITY_ENV = 'WOT_OFFLINE_BUILD_IDENTITY'
BUILD_IDENTITY_FILENAME = 'build_identity.json'
BUILD_IDENTITY_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$')
NATIVE_BRIDGE_FILENAME = 'offline_instance_guard_native.pyd'
WORKER_STARTER_FILENAME = 'offline_worker_starter.exe'
SERVER_FILENAME = 'WoT-0.9.22-LAN-Server.exe'
PREFERENCES_CONFIGS = (
    ('engine_config.offline-player.xml', 'playerprefs.xml'),
    ('engine_config.offline-worker.xml', 'workerprefs.xml'),
)
PYTHON_MAGIC = '\x03\xf3\r\n'
FOLIAGE_FORMAT = 'offline-lan-0922-foliage'
FOLIAGE_VERSION = 4
FOLIAGE_MANIFEST_FORMAT = FOLIAGE_FORMAT + '-manifest'
DESTRUCTIBLE_FORMAT = 'offline-lan-0922-destructible-catalog'
DESTRUCTIBLE_VERSION = 7
DESTRUCTIBLE_MANIFEST_FORMAT = DESTRUCTIBLE_FORMAT + '-manifest'
PROJECT_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..'))
LEGAL_FILES = (
    'LICENSE',
    'THIRD_PARTY_NOTICES.md',
    os.path.join('licenses', 'Boost-1.0.txt'),
)


def _copy_legal_files(destination_root):
    for relative_path in LEGAL_FILES:
        source = os.path.join(PROJECT_ROOT, relative_path)
        if not os.path.isfile(source):
            raise SystemExit('required legal file is missing: %s' % relative_path)
        destination = os.path.join(destination_root, relative_path)
        parent = os.path.dirname(destination)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        shutil.copy2(source, destination)


def _remove_stale_bytecode(root):
    for current_root, dirs, files in os.walk(root):
        for dirname in list(dirs):
            if dirname == '__pycache__':
                shutil.rmtree(os.path.join(current_root, dirname))
                dirs.remove(dirname)
        for filename in files:
            if filename.endswith(('.pyc', '.pyo')):
                os.unlink(os.path.join(current_root, filename))


def _remove_sources(root):
    for current_root, _, files in os.walk(root):
        for filename in files:
            if filename.endswith('.py'):
                os.unlink(os.path.join(current_root, filename))


def _archive_tree(source_root, destination):
    archive = zipfile.ZipFile(destination, 'w', zipfile.ZIP_STORED)
    try:
        for current_root, dirs, files in os.walk(source_root):
            dirs.sort()
            files.sort()
            for dirname in dirs:
                absolute_path = os.path.join(current_root, dirname)
                relative_path = os.path.relpath(absolute_path, source_root)
                info = zipfile.ZipInfo(
                    relative_path.replace(os.sep, '/').rstrip('/') + '/')
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 0
                info.external_attr = 16
                archive.writestr(info, '')
            for filename in files:
                absolute_path = os.path.join(current_root, filename)
                relative_path = os.path.relpath(absolute_path, source_root)
                info = zipfile.ZipInfo(
                    relative_path.replace(os.sep, '/'))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 0
                # DOS archive bit.  Keeping this non-zero also prevents
                # zipfile.writestr from injecting host-specific permissions.
                info.external_attr = 32
                with open(absolute_path, 'rb') as stream:
                    archive.writestr(info, stream.read())
    finally:
        archive.close()


def _release_config():
    return {
        'schema': 2,
        'enabled': True,
        'host': '127.0.0.1',
        'port': 28782,
        'name': 'Player',
        'vehicle': 'ussr:R11_MS-1',
        'max_health': 90,
        'startupTimeoutSeconds': 30.0,
        'prebattleCountdownSeconds': 15.0,
        'battleDurationSeconds': 900.0,
        'physics_tuning': {},
        'he_tuning': {},
        'perfect_accuracy': False,
        'native_remote_vehicles': True,
        'authority_worker_probe': {
            'enabled': False,
            'stageSeconds': 15.0,
        },
    }


def _generated_build_identity(environ=None, now=None, random_hex=None):
    """Create one opaque diagnostic identity without inspecting payload bytes."""
    environ = os.environ if environ is None else environ
    explicit = str(environ.get(BUILD_IDENTITY_ENV, '') or '').strip()
    if explicit:
        if BUILD_IDENTITY_PATTERN.match(explicit) is None:
            raise SystemExit('%s is invalid' % BUILD_IDENTITY_ENV)
        return explicit
    now = time.time() if now is None else float(now)
    random_hex = uuid.uuid4().hex if random_hex is None else str(random_hex)
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now))
    return 'local-%s-%s' % (stamp, random_hex[:12])


def _build_identity_payload(build_identity):
    return {
        'schema': 1,
        'semanticVersion': MOD_VERSION,
        'buildIdentity': str(build_identity),
    }


def _preferences_config_payloads(source_root):
    stock_leaf = b'preferences.xml'
    baseline = None
    payloads = []
    for filename, preferences_leaf in PREFERENCES_CONFIGS:
        encoded_leaf = preferences_leaf.encode('ascii')
        source = os.path.join(source_root, filename)
        if not os.path.isfile(source):
            raise SystemExit(
                'isolated preferences config is missing: %s' % source)
        with open(source, 'rb') as stream:
            payload = stream.read()
        if (not payload.startswith(b'\x45\x4e\xa1\x62') or
                payload.count(encoded_leaf) != 1 or
                stock_leaf in payload or
                len(encoded_leaf) != len(stock_leaf)):
            raise SystemExit(
                'isolated preferences config is invalid: %s' % source)
        restored = payload.replace(encoded_leaf, stock_leaf, 1)
        if baseline is None:
            baseline = restored
        elif restored != baseline:
            raise SystemExit(
                'isolated preferences configs do not share one stock base')
        payloads.append((filename, payload))
    return payloads


def _write_client_overlay(dist_root, package_path, checksum_path, digest,
                          graph_source=None, foliage_source=None,
                          destructible_source=None,
                          native_bridge_source=None,
                          worker_starter_source=None,
                          server_executable_source=None,
                          build_identity=None):
    release_config = _release_config()
    build_identity = (
        _generated_build_identity() if build_identity is None
        else str(build_identity))
    native_bridge_source = native_bridge_source or os.path.join(
        os.path.dirname(__file__), 'native', NATIVE_BRIDGE_FILENAME)
    worker_starter_source = worker_starter_source or os.path.join(
        os.path.dirname(__file__), 'native', WORKER_STARTER_FILENAME)
    server_executable_source = server_executable_source or os.path.join(
        os.path.dirname(__file__), 'dist', 'server', SERVER_FILENAME)
    native_payloads = (
        ('native instance guard bridge', native_bridge_source),
        ('simulation worker starter', worker_starter_source),
        ('Windows LAN server', server_executable_source),
    )
    native_digests = []
    for description, source in native_payloads:
        if not os.path.isfile(source):
            raise SystemExit('%s is missing: %s' % (description, source))
        with open(source, 'rb') as stream:
            native_digests.append(hashlib.sha256(stream.read()).hexdigest())
    release_seed = '%s\n%s:%s\n%s' % (
        digest, release_config['host'], release_config['port'],
        '\n'.join(native_digests))
    release_digest = hashlib.sha256(release_seed.encode('utf-8')).hexdigest()
    release_name = 'WoT-0.9.22-LAN-Client-%s' % release_digest[:7]
    overlay_root = os.path.join(dist_root, release_name)
    mod_root = os.path.join(
        overlay_root, 'mods', '0.9.22.0.1')
    os.makedirs(mod_root)
    shutil.copy2(package_path, mod_root)
    shutil.copy2(checksum_path, mod_root)
    # Windows cannot load a native extension directly from a wotmod ZIP.
    # Keep the exact-build bridge beside the package for imp.load_dynamic.
    shutil.copy2(native_bridge_source, mod_root)
    preferences_source = os.path.join(
        os.path.dirname(__file__), 'client_overlay', 'res_mods',
        '0.9.22.0.1')
    preferences_root = os.path.join(
        overlay_root, 'res_mods', '0.9.22.0.1')
    os.makedirs(preferences_root)
    for filename, payload in _preferences_config_payloads(
            preferences_source):
        with open(os.path.join(preferences_root, filename), 'wb') as stream:
            stream.write(payload)
    config_root = os.path.join(
        overlay_root, 'mods', 'configs', 'offline_lan_0922')
    os.makedirs(config_root)
    with open(os.path.join(config_root, 'config.json'), 'wb') as stream:
        payload = json.dumps(
            release_config, indent=2, sort_keys=True) + '\n'
        stream.write(payload.encode('utf-8'))
    with open(os.path.join(
            config_root, BUILD_IDENTITY_FILENAME), 'wb') as stream:
        payload = json.dumps(
            _build_identity_payload(build_identity),
            indent=2, sort_keys=True) + '\n'
        stream.write(payload.encode('utf-8'))
    graph_source = graph_source or os.path.join(
        os.path.dirname(__file__), 'navgraphs')
    _validate_navigation_graphs(graph_source)
    shutil.copytree(graph_source, os.path.join(config_root, 'navgraphs'))
    foliage_source = foliage_source or os.path.join(
        os.path.dirname(__file__), 'foliage')
    _validate_foliage(foliage_source)
    shutil.copytree(foliage_source, os.path.join(config_root, 'foliage'))
    destructible_source = destructible_source or os.path.join(
        os.path.dirname(__file__), 'destructibles')
    _validate_destructibles(destructible_source)
    shutil.copytree(
        destructible_source, os.path.join(config_root, 'destructibles'))
    shutil.copy2(os.path.join(os.path.dirname(__file__), 'INSTALL.txt'),
                 overlay_root)
    shutil.copy2(
        os.path.join(os.path.dirname(__file__), 'START_OFFLINE_0922.bat'),
        overlay_root)
    shutil.copy2(
        os.path.join(os.path.dirname(__file__), 'START_LAN_CLIENT_0922.bat'),
        overlay_root)
    shutil.copy2(
        os.path.join(
            os.path.dirname(__file__),
            'START_SIMULATION_WORKER_0922.bat'),
        overlay_root)
    shutil.copy2(worker_starter_source, overlay_root)
    shutil.copy2(server_executable_source, overlay_root)
    tools_root = os.path.join(overlay_root, 'tools')
    os.makedirs(tools_root)
    for filename in (
            'AUTHORITY_WORKER_PROBE.md',
            'authority_worker_probe_supervisor.py'):
        shutil.copy2(
            os.path.join(os.path.dirname(__file__), 'tools', filename),
            tools_root)
    _copy_legal_files(overlay_root)
    zip_path = os.path.join(
        dist_root,
        release_name + '.zip')
    _archive_tree(overlay_root, zip_path)
    print('client endpoint=%s:%s' % (
        release_config['host'], release_config['port']))
    return overlay_root, zip_path


def _validate_navigation_graphs(graph_root):
    manifest_path = os.path.join(graph_root, 'manifest.json')
    if not os.path.isfile(manifest_path):
        raise SystemExit('complete #1513 navigation graph batch is missing')
    with open(manifest_path, 'rb') as stream:
        manifest = json.load(stream)
    records = manifest.get('maps') if isinstance(manifest, dict) else None
    try:
        version = int(manifest.get('version', -1))
    except (AttributeError, TypeError, ValueError):
        version = -1
    expected_maps = set(_navigation_schema.SUPPORTED_MAPS)
    if (not isinstance(manifest, dict) or
            manifest.get('format') != _navigation_schema.MANIFEST_FORMAT or
            version != _navigation_schema.FORMAT_VERSION or
            manifest.get('game_version') != _navigation_schema.GAME_VERSION or
            not isinstance(records, list) or
            len(records) != len(expected_maps)):
        raise SystemExit('complete #1513 navigation graph manifest is invalid')
    seen = set()
    expected_files = set(name + '.json' for name in expected_maps)
    for record in records:
        if not isinstance(record, dict):
            raise SystemExit(
                'complete #1513 navigation graph batch is invalid')
        name = str(record.get('map') or '')
        filename = str(record.get('file') or '')
        expected_hash = str(record.get('sha256') or '')
        path = os.path.join(graph_root, filename)
        valid_hash = (
            len(expected_hash) == 64 and
            all(character in '0123456789abcdef'
                for character in expected_hash))
        if (name not in expected_maps or name in seen or
                filename != name + '.json' or not valid_hash or
                not os.path.isfile(path)):
            raise SystemExit('complete #1513 navigation graph batch is invalid')
        with open(path, 'rb') as stream:
            payload = stream.read()
        actual_hash = hashlib.sha256(payload).hexdigest()
        if actual_hash != expected_hash:
            raise SystemExit('navigation graph checksum mismatch: %s' % name)
        try:
            graph = json.loads(payload.decode('utf-8'))
            _navigation_schema.validate_graph(graph, name)
        except (TypeError, ValueError) as error:
            raise SystemExit(
                'navigation graph is invalid for %s: %s' % (name, error))
        seen.add(name)
    actual_files = set(
        filename for filename in os.listdir(graph_root)
        if filename.endswith('.json') and filename != 'manifest.json')
    if seen != expected_maps or actual_files != expected_files:
        raise SystemExit('complete #1513 navigation graph batch is invalid')


def _validate_foliage(foliage_root):
    """Reject partial, stale or tampered #1513 concealment data."""
    manifest_path = os.path.join(foliage_root, 'manifest.json')
    if not os.path.isfile(manifest_path):
        raise SystemExit('complete #1513 foliage batch is missing')
    with open(manifest_path, 'rb') as stream:
        manifest = json.load(stream)
    records = manifest.get('maps') if isinstance(manifest, dict) else None
    try:
        version = int(manifest.get('version', -1))
    except (AttributeError, TypeError, ValueError):
        version = -1
    expected_maps = set(_navigation_schema.SUPPORTED_MAPS)
    if (not isinstance(manifest, dict) or
            manifest.get('format') != FOLIAGE_MANIFEST_FORMAT or
            version != FOLIAGE_VERSION or
            manifest.get('game_version') != _navigation_schema.GAME_VERSION or
            not isinstance(records, list) or
            len(records) != len(expected_maps)):
        raise SystemExit('complete #1513 foliage manifest is invalid')
    seen = set()
    expected_files = set(name + '.json' for name in expected_maps)
    for record in records:
        if not isinstance(record, dict):
            raise SystemExit('complete #1513 foliage batch is invalid')
        name = str(record.get('map') or '')
        filename = str(record.get('file') or '')
        expected_hash = str(record.get('sha256') or '')
        path = os.path.join(foliage_root, filename)
        if (name not in expected_maps or name in seen or
                filename != name + '.json' or
                len(expected_hash) != 64 or not os.path.isfile(path)):
            raise SystemExit('complete #1513 foliage batch is invalid')
        with open(path, 'rb') as stream:
            payload = stream.read()
        if hashlib.sha256(payload).hexdigest() != expected_hash:
            raise SystemExit('foliage checksum mismatch: %s' % name)
        try:
            data = json.loads(payload.decode('utf-8'))
            instances = data.get('instances')
            cells = data.get('cells')
            fallen_trees = data.get('fallen_trees')
            if (data.get('format') != FOLIAGE_FORMAT or
                    int(data.get('version', -1)) != FOLIAGE_VERSION or
                    data.get('game_version') != _navigation_schema.GAME_VERSION or
                    data.get('map') != name or
                    float(data.get('cell_size', 0.0)) <= 0.0 or
                    not isinstance(instances, list) or not instances or
                    not isinstance(cells, dict) or
                    not isinstance(fallen_trees, list) or
                    any(not isinstance(row, list) or len(row) != 10
                        for row in instances) or
                    any(not isinstance(row, list) or len(row) != 9
                        for row in fallen_trees)):
                raise ValueError('invalid foliage data')
        except (AttributeError, TypeError, ValueError) as error:
            raise SystemExit(
                'foliage data is invalid for %s: %s' % (name, error))
        seen.add(name)
    actual_files = set(
        filename for filename in os.listdir(foliage_root)
        if filename.endswith('.json') and filename != 'manifest.json')
    if seen != expected_maps or actual_files != expected_files:
        raise SystemExit('complete #1513 foliage batch is invalid')


def _validate_destructibles(destructible_root):
    """Reject partial or tampered #1513 contact geometry catalogs."""
    manifest_path = os.path.join(destructible_root, 'manifest.json')
    if not os.path.isfile(manifest_path):
        raise SystemExit('complete #1513 destructible catalog is missing')
    with open(manifest_path, 'rb') as stream:
        manifest = json.load(stream)
    records = manifest.get('maps') if isinstance(manifest, dict) else None
    try:
        version = int(manifest.get('version', -1))
        locator_quantization = int(manifest.get('locator_quantization'))
    except (AttributeError, TypeError, ValueError):
        version = -1
        locator_quantization = -1
    expected_maps = set(_navigation_schema.SUPPORTED_MAPS)
    if (not isinstance(manifest, dict) or
            manifest.get('format') != DESTRUCTIBLE_MANIFEST_FORMAT or
            version != DESTRUCTIBLE_VERSION or
            locator_quantization != 1000 or
            manifest.get('game_version') != _navigation_schema.GAME_VERSION or
            not isinstance(records, list) or
            len(records) != len(expected_maps)):
        raise SystemExit(
            'complete #1513 destructible catalog manifest is invalid')
    seen = set()
    expected_files = set(name + '.json' for name in expected_maps)
    for record in records:
        if not isinstance(record, dict):
            raise SystemExit('complete #1513 destructible catalog is invalid')
        name = str(record.get('map') or '')
        filename = str(record.get('file') or '')
        expected_hash = str(record.get('sha256') or '')
        path = os.path.join(destructible_root, filename)
        if (name not in expected_maps or name in seen or
                filename != name + '.json' or
                len(expected_hash) != 64 or not os.path.isfile(path)):
            raise SystemExit('complete #1513 destructible catalog is invalid')
        with open(path, 'rb') as stream:
            payload = stream.read()
        if hashlib.sha256(payload).hexdigest() != expected_hash:
            raise SystemExit(
                'destructible catalog checksum mismatch: %s' % name)
        try:
            data = json.loads(payload.decode('utf-8'))
            resources = data.get('resources')
            if (data.get('format') != DESTRUCTIBLE_FORMAT or
                    int(data.get('version', -1)) != DESTRUCTIBLE_VERSION or
                    int(data.get('locator_quantization', -1)) != 1000 or
                    data.get('game_version') !=
                    _navigation_schema.GAME_VERSION or
                    data.get('map') != name or
                    not isinstance(resources, dict) or not resources):
                raise ValueError('invalid destructible catalog')
            for resource_name, resource in resources.items():
                boxes = resource.get('boxes') if isinstance(
                    resource, dict) else None
                kind = resource.get('kind') if isinstance(
                    resource, dict) else None
                locators = resource.get('locators') if isinstance(
                    resource, dict) else None
                if (not resource_name or
                        kind not in ('falling', 'fragile', 'structure') or
                        not isinstance(boxes, list) or not boxes or
                        any(not isinstance(box, list) or len(box) != 7
                            for box in boxes)):
                    raise ValueError('invalid destructible resource')
                if kind == 'structure' and locators is not None:
                    raise ValueError('invalid destructible resource locator')
                if kind != 'structure' and len(boxes) > 1:
                    if not isinstance(locators, list) or not locators:
                        raise ValueError(
                            'invalid destructible resource locator')
                elif locators is not None:
                    raise ValueError('invalid destructible resource locator')
                seen_locators = set()
                for locator in locators or ():
                    if (not isinstance(locator, list) or
                            len(locator) != 13 or
                            any(type(value) is not int for value in locator)):
                        raise ValueError(
                            'invalid destructible resource locator')
                    signature = tuple(locator[:12])
                    box_index = int(locator[12])
                    if (signature in seen_locators or box_index < 0 or
                            box_index >= len(boxes)):
                        raise ValueError(
                            'invalid destructible resource locator')
                    seen_locators.add(signature)
            instances = data.get('instances')
            ambiguous_instances = data.get('ambiguous_instances')
            if (not isinstance(instances, list) or not instances or
                    not isinstance(ambiguous_instances, list)):
                raise ValueError('invalid destructible instance index')
            seen_instance_signatures = set()
            seen_instance_wires = set()
            instance_kind_counts = dict((kind, 0) for kind in (
                'falling', 'fragile', 'structure'))
            previous_signature = None
            for row in instances:
                if (not isinstance(row, list) or len(row) != 17 or
                        any(type(value) is not int for value in row[:12])):
                    raise ValueError('invalid destructible instance row')
                signature = tuple(row[:12])
                resource = resources.get(row[12])
                box_index = row[13]
                if (signature in seen_instance_signatures or
                        (previous_signature is not None and
                         signature <= previous_signature) or
                        not isinstance(resource, dict)):
                    raise ValueError('invalid destructible instance row')
                if resource.get('kind') == 'structure':
                    if box_index is not None:
                        raise ValueError('invalid destructible instance row')
                elif (type(box_index) is not int or box_index < 0 or
                      box_index >= len(resource.get('boxes') or ())):
                    raise ValueError('invalid destructible instance row')
                chunk_id, item_index, item_scale = row[14:]
                if (type(chunk_id) is not int or type(item_index) is not int
                        or chunk_id < 0 or chunk_id > 0xFFFFFFFF or
                        item_index < 0 or
                        (chunk_id, item_index) in seen_instance_wires):
                    raise ValueError('invalid destructible instance wire')
                seen_instance_wires.add((chunk_id, item_index))
                try:
                    item_scale = float(item_scale)
                except (TypeError, ValueError):
                    raise ValueError('invalid destructible instance scale')
                if (math.isnan(item_scale) or math.isinf(item_scale) or
                        item_scale <= 0.0):
                    raise ValueError('invalid destructible instance scale')
                seen_instance_signatures.add(signature)
                previous_signature = signature
                instance_kind_counts[resource['kind']] += 1
            previous_signature = None
            ambiguous_candidate_count = 0
            for row in ambiguous_instances:
                if (not isinstance(row, list) or len(row) != 13 or
                        any(type(value) is not int for value in row[:12]) or
                        not isinstance(row[12], list) or len(row[12]) < 2):
                    raise ValueError(
                        'invalid ambiguous destructible instance row')
                signature = tuple(row[:12])
                if (signature in seen_instance_signatures or
                        (previous_signature is not None and
                         signature <= previous_signature)):
                    raise ValueError(
                        'invalid ambiguous destructible instance row')
                candidates = []
                for candidate in row[12]:
                    if (not isinstance(candidate, list) or
                            len(candidate) != 2 or
                            candidate[0] not in resources):
                        raise ValueError(
                            'invalid ambiguous destructible candidate')
                    resource = resources[candidate[0]]
                    box_index = candidate[1]
                    if resource.get('kind') == 'structure':
                        if box_index is not None:
                            raise ValueError(
                                'invalid ambiguous destructible candidate')
                    elif (type(box_index) is not int or box_index < 0 or
                          box_index >= len(resource.get('boxes') or ())):
                        raise ValueError(
                            'invalid ambiguous destructible candidate')
                    candidates.append((candidate[0], box_index))
                candidate_sort_key = lambda candidate: (
                    candidate[0],
                    -1 if candidate[1] is None else int(candidate[1]))
                if (candidates != sorted(
                            candidates, key=candidate_sort_key)):
                    raise ValueError(
                        'invalid ambiguous destructible candidate')
                seen_instance_signatures.add(signature)
                previous_signature = signature
                ambiguous_candidate_count += len(candidates)
            census = data.get('census')
            if (not isinstance(census, dict) or
                    int(census.get('instance_signatures', -1)) !=
                    len(instances) or
                    int(census.get('falling_instance_signatures', -1)) !=
                    instance_kind_counts['falling'] or
                    int(census.get('fragile_instance_signatures', -1)) !=
                    instance_kind_counts['fragile'] or
                    int(census.get('structure_instance_signatures', -1)) !=
                    instance_kind_counts['structure'] or
                    int(census.get('ambiguous_instance_signatures', -1)) !=
                    len(ambiguous_instances) or
                    int(census.get('ambiguous_instance_candidates', -1)) !=
                    ambiguous_candidate_count):
                raise ValueError('invalid destructible instance census')
        except (AttributeError, TypeError, ValueError) as error:
            raise SystemExit(
                'destructible catalog is invalid for %s: %s' %
                (name, error))
        seen.add(name)
    actual_files = set(
        filename for filename in os.listdir(destructible_root)
        if filename.endswith('.json') and filename != 'manifest.json')
    if seen != expected_maps or actual_files != expected_files:
        raise SystemExit('complete #1513 destructible catalog is invalid')


def _remove_stale_outputs(dist_root):
    for filename in os.listdir(dist_root):
        is_mod = (filename.startswith(MOD_ID + '_') and
                  (filename.endswith('.wotmod') or
                   filename.endswith('.wotmod.sha256')))
        is_client_release = filename.startswith('WoT-0.9.22-LAN-Client-')
        is_old_overlay = filename == 'client-overlay'
        is_old_zip = filename.startswith(
            'WoT-0.9.22.0.1-Offline-LAN-Vertical-Slice-')
        is_native_bridge = filename == NATIVE_BRIDGE_FILENAME
        output_path = os.path.join(dist_root, filename)
        if (is_mod or is_client_release or is_old_overlay or is_old_zip or
                is_native_bridge):
            if os.path.isdir(output_path):
                shutil.rmtree(output_path)
            else:
                os.unlink(output_path)


def _validate_python():
    if sys.version_info[:2] != (2, 7):
        raise SystemExit('build_wotmod.py requires Python 2.7')
    if hasattr(sys, 'subversion') and sys.subversion[0] != 'CPython':
        raise SystemExit('build_wotmod.py requires CPython 2.7 bytecode')


def _validate_entry(staging_root):
    entry = os.path.join(
        staging_root,
        'res', 'scripts', 'client', 'gui', 'mods',
        'mod_offline_lan_0922.pyc')
    if not os.path.isfile(entry):
        raise SystemExit('compiled mod entry is missing: %s' % entry)
    with open(entry, 'rb') as stream:
        magic = stream.read(4)
    if magic != PYTHON_MAGIC:
        raise SystemExit('unexpected Python bytecode magic: %r' % magic)


def build():
    _validate_python()
    port_root = os.path.abspath(os.path.dirname(__file__))
    source_root = os.path.join(port_root, 'src')
    dist_root = os.path.join(port_root, 'dist')
    if not os.path.isdir(dist_root):
        os.makedirs(dist_root)
    _remove_stale_outputs(dist_root)
    build_identity = _generated_build_identity()
    staging_parent = tempfile.mkdtemp(prefix='offline-lan-0922-')
    try:
        staging_root = os.path.join(staging_parent, 'package')
        shutil.copytree(source_root, staging_root)
        shutil.copy2(os.path.join(port_root, 'meta.xml'), staging_root)
        _copy_legal_files(staging_root)
        _remove_stale_bytecode(staging_root)
        # A random temporary build path in code.co_filename changes every PYC
        # and defeats the content-hash release name.  Compile against a stable
        # package-relative root so identical sources produce identical bytes.
        if not compileall.compile_dir(
                staging_root, ddir='.', force=1, quiet=1):
            raise SystemExit('Python 2.7 compilation failed')
        _remove_sources(staging_root)
        _validate_entry(staging_root)
        filename = '%s_%s.wotmod' % (MOD_ID, MOD_VERSION)
        destination = os.path.join(dist_root, filename)
        _archive_tree(staging_root, destination)
        with open(destination, 'rb') as stream:
            digest = hashlib.sha256(stream.read()).hexdigest()
        checksum_path = destination + '.sha256'
        with open(checksum_path, 'wb') as stream:
            stream.write(('%s  %s\n' % (digest, filename)).encode('ascii'))
        native_bridge_source = os.path.join(
            port_root, 'native', NATIVE_BRIDGE_FILENAME)
        if not os.path.isfile(native_bridge_source):
            raise SystemExit(
                'native instance guard bridge is missing: %s' %
                native_bridge_source)
        native_bridge_path = os.path.join(
            dist_root, NATIVE_BRIDGE_FILENAME)
        shutil.copy2(native_bridge_source, native_bridge_path)
        overlay_root, overlay_zip = _write_client_overlay(
            dist_root, destination, checksum_path, digest,
            native_bridge_source=native_bridge_path,
            build_identity=build_identity)
        print('build identity=%s version=%s' %
              (build_identity, MOD_VERSION))
        print(destination)
        print('sha256=%s' % digest)
        print(overlay_root)
        print(overlay_zip)
    finally:
        shutil.rmtree(staging_parent)


if __name__ == '__main__':
    build()
