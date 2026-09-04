import importlib.util
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import gc
import socket
import struct
import sys
import tempfile
import types
import unittest
from unittest import mock
import weakref
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[2]
PORT_ROOT = ROOT / '0.9.22'
CLIENT_SCRIPTS = PORT_ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from effective_params_fixture import effective_params, wire_player


def _load_tool(name):
    path = PORT_ROOT / 'tools' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_port_source(name):
    path = (PORT_ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' /
            'mods' / 'offline_lan_0922' / (name + '.py'))
    spec = importlib.util.spec_from_file_location('port0922_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_navigation_graph(map_name):
    formations = {
        '1': [[float(slot % 5) * 12.0, 0.0,
               -100.0 + float(slot // 5) * 12.0, 0.0]
              for slot in range(15)],
        '2': [[float(slot % 5) * 12.0, 0.0,
               100.0 - float(slot // 5) * 12.0, 3.14159]
              for slot in range(15)],
    }
    return {
        'format': 'offline-lan-0922-navgraph',
        'version': 2,
        'game_version': '0.9.22.0.1-cn-1513',
        'map': map_name,
        'width': 2,
        'height': 1,
        'cell_size': 4.0,
        'origin': [0.0, 0.0],
        'bounds': [0.0, 0.0, 4.0, 0.0],
        'heights_mm': [0, 0],
        'links': [16, 8],
        'hazards': [0, 0],
        'spawn_anchors': [[0.0, 0.0], [4.0, 0.0]],
        'objective_bases': [[4.0, 0.0], [0.0, 0.0]],
        'spawn_formations': formations,
        'routes': {
            '1': [{'id': 'lane', 'waypoints': [
                [0.0, 0.0, False], [4.0, 0.0, False]]}],
            '2': [{'id': 'lane', 'waypoints': [
                [4.0, 0.0, False], [0.0, 0.0, False]]}],
        },
    }


def _write_navigation_batch(graphs, map_names, graph_factory):
    records = []
    for name in map_names:
        payload = (json.dumps(
            graph_factory(name), sort_keys=True) + '\n').encode('utf-8')
        filename = name + '.json'
        (graphs / filename).write_bytes(payload)
        records.append({
            'map': name,
            'file': filename,
            'sha256': hashlib.sha256(payload).hexdigest(),
        })
    (graphs / 'manifest.json').write_text(json.dumps({
        'format': 'offline-lan-0922-navgraph-manifest',
        'version': 2,
        'game_version': '0.9.22.0.1-cn-1513',
        'maps': records,
    }), encoding='utf-8')


def _write_fake_executable(path, machine=0x014C,
                           include_native_vehicle_input=True):
    payload = bytearray(1024)
    payload[0:2] = b'MZ'
    payload[0x3C:0x40] = struct.pack('<I', 0x60)
    payload[0x60:0x64] = b'PE\0\0'
    payload[0x64:0x66] = struct.pack('<H', machine)
    methods = [b'PyWGVehicleFilter\0']
    if include_native_vehicle_input:
        methods.extend((
            b'notifyInputKeysDown\0', b'setVehiclePhysics\0',
            b'getVehiclePhysics\0', b'setTracksSpeed\0',
            b'syncGunAngles\0'))
    methods.append(b'PyWGTurretFilter\0')
    table = b''.join(methods)
    payload[0x100:0x100 + len(table)] = table
    path.write_bytes(payload)


def _write_fake_client(root, inspector, build='1513', changed_entity=False,
                       changed_vehicle=False):
    (root / 'res' / 'packages').mkdir(parents=True)
    _write_fake_executable(root / 'WorldOfTanks.exe')
    (root / 'version.xml').write_text(
        '<version.xml><version>v.0.9.22.0.1 #%s</version></version.xml>' %
        build, encoding='utf-8')
    (root / 'paths.xml').write_text(
        '<root><Paths>'
        '<Path>./res_mods/0.9.22.0.1</Path>'
        '<Path>./mods/0.9.22.0.1</Path>'
        '</Paths></root>', encoding='utf-8')
    with zipfile.ZipFile(root / 'res' / 'packages' / 'scripts.pkg', 'w') as archive:
        for member in inspector.PROBE_MEMBERS:
            archive.writestr(member, b'\x03\xf3\r\n' + b'payload')
        for member in inspector.REQUIRED_SCRIPT_MEMBERS:
            archive.writestr(member, b'payload')
        entity_payload = b'pinned-entity-definition'
        for member in inspector.PINNED_ENTITY_DEFINITION_SHA256:
            payload = entity_payload
            if (changed_entity and member ==
                    'scripts/entity_defs/interfaces/AvatarObserver.def'):
                payload = b'changed'
            if (changed_vehicle and member ==
                    'scripts/entity_defs/Vehicle.def'):
                payload = b'changed-vehicle-schema'
            archive.writestr(member, payload)
    inspector.PINNED_ENTITY_DEFINITION_SHA256 = {
        member: hashlib.sha256(entity_payload).hexdigest()
        for member in inspector.PINNED_ENTITY_DEFINITION_SHA256
    }
    for package_name, members in inspector.REQUIRED_RESOURCE_MEMBERS.items():
        with zipfile.ZipFile(
                root / 'res' / 'packages' / package_name, 'w') as archive:
            for member in members:
                archive.writestr(member, b'payload')


class ClientInspectorTests(unittest.TestCase):
    def test_reads_version_paths_and_python_27_magic(self):
        inspector = _load_tool('inspect_client')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fake_client(root, inspector)

            report = inspector.inspect_client(root)

        self.assertEqual('0.9.22.0.1', report['version'])
        self.assertEqual('1513', report['build'])
        self.assertEqual('./mods/0.9.22.0.1', report['wotmodPath'])
        self.assertEqual('x86', report['architecture'])
        self.assertIn('notifyInputKeysDown',
                      report['nativeVehicleFilter']['requiredMethods'])
        self.assertEqual(['set', 'setPosition'],
                         report['nativeVehicleFilter']['absentPoseMethods'])
        runtimes = {
            value['runtime'] for value in report['bytecode'].values()
        }
        self.assertEqual({'CPython 2.7'}, runtimes)

    def test_rejects_incomplete_client(self):
        inspector = _load_tool('inspect_client')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                inspector.inspect_client(directory)

    def test_rejects_wrong_client_build(self):
        inspector = _load_tool('inspect_client')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fake_client(root, inspector, build='788')
            with self.assertRaisesRegex(ValueError, 'build must be #1513'):
                inspector.inspect_client(root)

    def test_rejects_client_without_native_vehicle_input_boundary(self):
        inspector = _load_tool('inspect_client')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fake_client(root, inspector)
            _write_fake_executable(
                root / 'WorldOfTanks.exe',
                include_native_vehicle_input=False)

            with self.assertRaisesRegex(
                    ValueError, 'PyWGVehicleFilter methods are missing'):
                inspector.inspect_client(root)

    def test_rejects_changed_avatar_entity_definition(self):
        inspector = _load_tool('inspect_client')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fake_client(root, inspector, changed_entity=True)

            with self.assertRaisesRegex(
                    ValueError, 'entity definition differs'):
                inspector.inspect_client(root)

    def test_rejects_changed_vehicle_entity_definition(self):
        inspector = _load_tool('inspect_client')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_fake_client(root, inspector, changed_vehicle=True)

            with self.assertRaisesRegex(
                    ValueError, 'entity definition differs.*Vehicle.def'):
                inspector.inspect_client(root)


class WotmodValidatorTests(unittest.TestCase):
    def _write_archive(self, path, compression, include_directories,
                       pyc_members=None, extras=None):
        entry = 'res/scripts/client/gui/mods/mod_offline_lan_0922.pyc'
        pyc_members = set(pyc_members or (entry,))
        extras = dict(extras or {})
        file_names = set(pyc_members) | set(extras) | {'meta.xml'}
        directories = set()
        for name in file_names:
            parts = name.split('/')[:-1]
            for index in range(1, len(parts) + 1):
                directories.add('/'.join(parts[:index]) + '/')
        meta = (
            '<root><id>org.peng.offline_lan_0922</id>'
            '<version>0.6.1</version></root>')
        with zipfile.ZipFile(path, 'w', compression) as archive:
            if include_directories:
                for directory in sorted(directories):
                    info = zipfile.ZipInfo(directory)
                    info.compress_type = zipfile.ZIP_STORED
                    archive.writestr(info, b'')
            archive.writestr('meta.xml', meta)
            for member in sorted(pyc_members):
                archive.writestr(member, b'\x03\xf3\r\n' + b'payload')
            for member, payload in sorted(extras.items()):
                archive.writestr(member, payload)

    def test_accepts_only_fully_stored_archive_with_parent_directories(self):
        validator = _load_tool('validate_wotmod')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'good.wotmod'
            expected = validator.expected_pyc_members()
            self._write_archive(
                path, zipfile.ZIP_STORED, True, pyc_members=expected)
            with zipfile.ZipFile(path) as archive:
                archive_member_count = len(archive.namelist())
            self.assertEqual(
                archive_member_count, validator.validate(path))

    def test_rejects_missing_directory_members(self):
        validator = _load_tool('validate_wotmod')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing-dirs.wotmod'
            self._write_archive(
                path, zipfile.ZIP_STORED, False,
                pyc_members=validator.expected_pyc_members())
            with self.assertRaisesRegex(ValueError, 'directory members'):
                validator.validate(path)

    def test_rejects_deflated_file_member(self):
        validator = _load_tool('validate_wotmod')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'deflated.wotmod'
            self._write_archive(
                path, zipfile.ZIP_DEFLATED, True,
                pyc_members=validator.expected_pyc_members())
            with self.assertRaisesRegex(ValueError, 'not stored'):
                validator.validate(path)

    def test_rejects_stale_package_missing_current_source_module(self):
        validator = _load_tool('validate_wotmod')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'stale.wotmod'
            expected = validator.expected_pyc_members()
            missing = next(iter(expected - {validator.ENTRY}))
            self._write_archive(
                path, zipfile.ZIP_STORED, True,
                pyc_members=expected - {missing})
            with self.assertRaisesRegex(ValueError, 'manifest mismatch'):
                validator.validate(path)

    def test_rejects_optimized_or_python3_cache_files(self):
        validator = _load_tool('validate_wotmod')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'unwanted.wotmod'
            self._write_archive(
                path, zipfile.ZIP_STORED, True,
                pyc_members=validator.expected_pyc_members(),
                extras={'res/module.pyo': b'optimized'})
            with self.assertRaisesRegex(ValueError, 'unwanted Python files'):
                validator.validate(path)

    def test_rejects_duplicate_archive_members(self):
        validator = _load_tool('validate_wotmod')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'duplicate.wotmod'
            self._write_archive(
                path, zipfile.ZIP_STORED, True,
                pyc_members=validator.expected_pyc_members())
            with zipfile.ZipFile(path, 'a') as archive:
                with self.assertWarns(UserWarning):
                    archive.writestr('meta.xml', b'duplicate')
            with self.assertRaisesRegex(ValueError, 'duplicate archive members'):
                validator.validate(path)


class PortSourceTests(unittest.TestCase):
    def test_release_entry_and_meta_are_present(self):
        self.assertTrue((PORT_ROOT / 'meta.xml').is_file())
        self.assertTrue((
            PORT_ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' /
            'mods' / 'mod_offline_lan_0922.py').is_file())

    def test_release_version_metadata_is_aligned(self):
        packager_path = PORT_ROOT / 'build_wotmod.py'
        spec = importlib.util.spec_from_file_location(
            'build_wotmod_version_test', packager_path)
        packager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packager)
        package_path = (
            PORT_ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' /
            'mods' / 'offline_lan_0922' / '__init__.py')
        spec = importlib.util.spec_from_file_location(
            'offline_lan_0922_version_test', package_path)
        package = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(package)
        meta_version = ET.parse(PORT_ROOT / 'meta.xml').getroot().findtext(
            'version')
        build_script = (PORT_ROOT / 'build_for_client.sh').read_text(
            encoding='utf-8')

        self.assertEqual('0.6.5', packager.MOD_VERSION)
        self.assertEqual(packager.MOD_VERSION, package.PORT_VERSION)
        self.assertEqual(packager.MOD_VERSION, meta_version)
        self.assertIn(
            'org.peng.offline_lan_0922_%s.wotmod' % packager.MOD_VERSION,
            build_script)

    def test_port_sources_are_python_2_compatible_syntax(self):
        source_root = PORT_ROOT / 'src'
        for path in source_root.rglob('*.py'):
            compile(path.read_text(encoding='utf-8'), str(path), 'exec')

    def test_compatibility_never_replaces_native_bigworld_target(self):
        source = (
            PORT_ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' /
            'mods' / 'offline_lan_0922' / 'compat.py').read_text(
                encoding='utf-8')
        self.assertNotRegex(source, r'\bbigworld\.target\s*=')

    def test_vehicle_force_law_uses_drive_intent_for_reverse_steering(self):
        physics = _load_port_source('vehicle_physics')
        params = {
            'speedFwd': 20.0,
            'rotSpd': 1.0,
            'terrainResist': (1.0, 1.0, 1.0),
        }
        forward = physics.traverse_step(
            params, 0.0, 1, 5.0, 0.1)
        reverse = physics.traverse_step(
            params, 0.0, 1, 5.0, 0.1, drive_intent=-1.0)
        backward_slide = physics.traverse_step(
            params, 0.0, 1, -5.0, 0.1, drive_intent=1.0)
        self.assertGreater(forward, 0.0)
        self.assertLess(reverse, 0.0)
        self.assertGreater(backward_slide, 0.0)

    def test_packager_removes_python_3_cache_before_python_2_compile(self):
        packager_path = PORT_ROOT / 'build_wotmod.py'
        spec = importlib.util.spec_from_file_location(
            'build_wotmod_test', packager_path)
        packager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packager)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / 'package' / '__pycache__'
            cache.mkdir(parents=True)
            (cache / 'module.cpython-314.pyc').write_bytes(b'python3')
            (root / 'package' / 'stale.pyc').write_bytes(b'python3')
            (root / 'package' / 'keep.py').write_text(
                'value = 1\n', encoding='utf-8')

            packager._remove_stale_bytecode(str(root))

            self.assertFalse(cache.exists())
            self.assertFalse((root / 'package' / 'stale.pyc').exists())
            self.assertTrue((root / 'package' / 'keep.py').exists())

    def test_archive_bytes_do_not_depend_on_source_timestamps(self):
        packager_path = PORT_ROOT / 'build_wotmod.py'
        spec = importlib.util.spec_from_file_location(
            'build_wotmod_archive_test', packager_path)
        packager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packager)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'package'
            nested = source / 'nested'
            nested.mkdir(parents=True)
            payload = nested / 'module.pyc'
            payload.write_bytes(b'fixed payload')
            first = root / 'first.wotmod'
            second = root / 'second.wotmod'

            os.utime(payload, (1000000000, 1000000000))
            packager._archive_tree(str(source), str(first))
            os.utime(payload, (1700000000, 1700000000))
            packager._archive_tree(str(source), str(second))

            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertTrue(all(
                    info.date_time == (1980, 1, 1, 0, 0, 0)
                    for info in archive.infolist()))

    def test_copy_ready_overlay_always_contains_loopback_endpoint(self):
        packager_path = PORT_ROOT / 'build_wotmod.py'
        spec = importlib.util.spec_from_file_location(
            'build_wotmod_overlay_test', packager_path)
        packager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packager)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'mod.wotmod'
            checksum = root / 'mod.wotmod.sha256'
            native_bridge = root / 'offline_instance_guard_native.pyd'
            worker_starter = root / 'offline_worker_starter.exe'
            server_executable = root / 'WoT-0.9.22-LAN-Server.exe'
            graphs = root / 'navgraphs'
            graphs.mkdir()
            _write_navigation_batch(
                graphs, packager._navigation_schema.SUPPORTED_MAPS,
                _valid_navigation_graph)
            package.write_bytes(b'mod')
            checksum.write_text('checksum\n', encoding='ascii')
            native_bridge.write_bytes(b'native bridge')
            worker_starter.write_bytes(b'worker starter')
            server_executable.write_bytes(b'LAN server')
            overlay, archive = packager._write_client_overlay(
                str(root), str(package), str(checksum), 'a' * 64,
                str(graphs),
                str(PORT_ROOT / 'foliage'),
                str(PORT_ROOT / 'destructibles'),
                native_bridge_source=str(native_bridge),
                worker_starter_source=str(worker_starter),
                server_executable_source=str(server_executable))

            config_path = (Path(overlay) / 'mods' / 'configs' /
                           'offline_lan_0922' / 'config.json')
            config = json.loads(config_path.read_text(encoding='utf-8'))
            self.assertEqual('127.0.0.1', config['host'])
            self.assertEqual(28782, config['port'])
            identity = json.loads((
                config_path.parent / packager.BUILD_IDENTITY_FILENAME
            ).read_text(encoding='utf-8'))
            self.assertEqual(1, identity['schema'])
            self.assertEqual('0.6.5', identity['semanticVersion'])
            self.assertRegex(
                identity['buildIdentity'],
                r'^local-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$')
            self.assertFalse(
                (config_path.parent / 'server_endpoint.json').exists())
            self.assertTrue((config_path.parent / 'navgraphs' /
                             'manifest.json').is_file())
            self.assertTrue((config_path.parent / 'foliage' /
                             'manifest.json').is_file())
            self.assertTrue((config_path.parent / 'destructibles' /
                             'manifest.json').is_file())
            packaged_bridge = (
                Path(overlay) / 'mods' / '0.9.22.0.1' /
                packager.NATIVE_BRIDGE_FILENAME)
            self.assertEqual(b'native bridge', packaged_bridge.read_bytes())
            packed_xml = _load_tool('packed_xml')
            for filename, preferences_leaf in packager.PREFERENCES_CONFIGS:
                packaged_config = (
                    Path(overlay) / 'res_mods' / '0.9.22.0.1' / filename)
                self.assertTrue(packaged_config.is_file())
                config_root = packed_xml.read_packed_xml(
                    packaged_config.read_bytes())
                preferences = [
                    value for name, value in config_root.children
                    if name == b'preferences'
                ]
                self.assertEqual(1, len(preferences))
                self.assertEqual(
                    preferences_leaf.encode('ascii'), preferences[0].value)
            player_batch = Path(overlay) / 'START_OFFLINE_0922.bat'
            self.assertTrue(player_batch.is_file())
            player_text = player_batch.read_text(encoding='utf-8')
            self.assertIn('offline_worker_starter.exe', player_text)
            self.assertEqual(
                1, player_text.count(
                    'start "" "%GAME_ROOT%offline_worker_starter.exe"'))
            lan_client_batch = Path(overlay) / 'START_LAN_CLIENT_0922.bat'
            self.assertTrue(lan_client_batch.is_file())
            self.assertIn(
                'offline_worker_starter.exe" --player',
                lan_client_batch.read_text(encoding='utf-8'))
            self.assertNotIn('powershell.exe', player_text.lower())
            self.assertNotIn('set "APPDATA=', player_text)
            worker_batch = (
                Path(overlay) / 'START_SIMULATION_WORKER_0922.bat')
            self.assertTrue(worker_batch.is_file())
            packaged_starter = (
                Path(overlay) / packager.WORKER_STARTER_FILENAME)
            self.assertEqual(
                b'worker starter', packaged_starter.read_bytes())
            self.assertEqual(
                b'LAN server',
                (Path(overlay) / packager.SERVER_FILENAME).read_bytes())
            worker_text = worker_batch.read_text(encoding='utf-8')
            self.assertIn(
                'OFFLINE_LAN_0922_CLIENT_MODE=simulation_worker',
                worker_text)
            self.assertIn(
                'OFFLINE_LAN_0922_ALLOW_MULTIPLE_CLIENTS=1',
                worker_text)
            self.assertNotIn('set "APPDATA=', worker_text)
            self.assertNotIn('set "LOCALAPPDATA=', worker_text)
            self.assertIn('offline_worker_starter.exe', worker_text)
            self.assertIn('--worker-only', worker_text)
            self.assertNotIn('powershell.exe', worker_text.lower())
            self.assertNotIn('WorldOfTanks.offline-worker.exe', worker_text)
            self.assertNotIn('prepare_worker_client.ps1', worker_text)
            self.assertNotIn('.offline-simulation-worker-copy', worker_text)
            self.assertNotIn('choice /C YN', worker_text)
            self.assertFalse(
                (Path(overlay) /
                 'RESTORE_SIMULATION_WORKER_0922.bat').exists())
            self.assertFalse(
                (Path(overlay) / 'tools' /
                 'prepare_worker_client.ps1').exists())
            self.assertTrue(
                (Path(overlay) / 'tools' /
                 'AUTHORITY_WORKER_PROBE.md').is_file())
            self.assertTrue(
                (Path(overlay) / 'tools' /
                 'authority_worker_probe_supervisor.py').is_file())
            self.assertTrue(Path(archive).is_file())

    def test_build_identity_is_automatic_and_can_be_pinned_by_ci(self):
        packager_path = PORT_ROOT / 'build_wotmod.py'
        spec = importlib.util.spec_from_file_location(
            'build_wotmod_identity_test', packager_path)
        packager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packager)

        self.assertEqual(
            'github-12345-2',
            packager._generated_build_identity({
                packager.BUILD_IDENTITY_ENV: 'github-12345-2'}))
        self.assertEqual(
            'local-19700101T000000Z-abcdef012345',
            packager._generated_build_identity(
                {}, now=0, random_hex='abcdef0123456789'))

    def test_navigation_release_gate_rejects_wrong_41_map_names(self):
        packager_path = PORT_ROOT / 'build_wotmod.py'
        spec = importlib.util.spec_from_file_location(
            'build_wotmod_wrong_graph_names_test', packager_path)
        packager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packager)
        with tempfile.TemporaryDirectory() as directory:
            graphs = Path(directory)
            _write_navigation_batch(
                graphs, ['map%02d' % index for index in range(41)],
                _valid_navigation_graph)

            with self.assertRaisesRegex(SystemExit, 'batch is invalid'):
                packager._validate_navigation_graphs(str(graphs))

    def test_navigation_release_gate_rejects_empty_graph_payload(self):
        packager_path = PORT_ROOT / 'build_wotmod.py'
        spec = importlib.util.spec_from_file_location(
            'build_wotmod_empty_graph_test', packager_path)
        packager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packager)
        first = packager._navigation_schema.SUPPORTED_MAPS[0]

        def graph_factory(name):
            return {} if name == first else _valid_navigation_graph(name)

        with tempfile.TemporaryDirectory() as directory:
            graphs = Path(directory)
            _write_navigation_batch(
                graphs, packager._navigation_schema.SUPPORTED_MAPS,
                graph_factory)

            with self.assertRaisesRegex(
                    SystemExit, 'navigation graph is invalid'):
                packager._validate_navigation_graphs(str(graphs))

    def test_destructible_release_gate_rejects_missing_instance_locator(self):
        packager_path = PORT_ROOT / 'build_wotmod.py'
        spec = importlib.util.spec_from_file_location(
            'build_wotmod_destructible_locator_test', packager_path)
        packager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packager)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for source in (PORT_ROOT / 'destructibles').glob('*.json'):
                (root / source.name).write_bytes(source.read_bytes())
            target = root / '35_steppes.json'
            data = json.loads(target.read_text(encoding='utf-8'))
            resource = next(
                value for value in data['resources'].values()
                if value.get('locators'))
            del resource['locators']
            payload = (json.dumps(
                data, sort_keys=True, separators=(',', ':')) +
                '\n').encode('utf-8')
            target.write_bytes(payload)
            manifest = json.loads(
                (root / 'manifest.json').read_text(encoding='utf-8'))
            next(record for record in manifest['maps']
                 if record['map'] == '35_steppes')['sha256'] = \
                hashlib.sha256(payload).hexdigest()
            (root / 'manifest.json').write_text(
                json.dumps(manifest), encoding='utf-8')

            with self.assertRaisesRegex(
                    SystemExit, 'resource locator'):
                packager._validate_destructibles(str(root))

    def test_navigation_batch_refuses_partial_existing_output(self):
        batch = _load_tool('bake_all_navigation_0922')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'navgraphs'
            output.mkdir()
            (output / '01_karelia.json').write_text(
                '{}\n', encoding='utf-8')

            with self.assertRaisesRegex(
                    ValueError, 'output set is incomplete or extra'):
                batch.bake_all(str(Path(directory) / 'client'), str(output))

    def test_navigation_batch_manifest_has_exact_supported_map_order(self):
        batch = _load_tool('bake_all_navigation_0922')
        with tempfile.TemporaryDirectory() as directory:
            digests = {
                name: hashlib.sha256(name.encode('ascii')).hexdigest()
                for name in batch.schema.SUPPORTED_MAPS
            }
            batch._write_manifest(directory, digests)
            manifest = json.loads(
                (Path(directory) / 'manifest.json').read_text(
                    encoding='utf-8'))

        self.assertEqual(
            list(batch.schema.SUPPORTED_MAPS),
            [record['map'] for record in manifest['maps']])
        self.assertEqual(batch.schema.MANIFEST_FORMAT, manifest['format'])

class PortConfigTests(unittest.TestCase):
    def test_windows_user_state_lives_outside_the_mods_directory(self):
        config_module = _load_port_source('config')
        appdata = r'C:\Users\Player\AppData\Roaming'

        path = config_module._default_user_data_dir({'APPDATA': appdata})

        self.assertEqual(
            os.path.join(appdata, 'Wargaming.net', 'WorldOfTanks',
                         'offline_lan_0922'), path)
        self.assertNotIn(os.path.join('mods', 'configs'), path)

    def test_waiting_room_choices_round_trip_in_player_owned_state(self):
        config_module = _load_port_source('config')
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'waiting_room_state.json')
            choices = {
                'schema': 1,
                'map': '05_prohorovka',
                'team': 2,
                'team_sizes': {1: 4, 2: 9},
            }

            self.assertTrue(config_module.save_waiting_room_state(
                choices, path))

            self.assertEqual(
                choices, config_module.load_waiting_room_state(path))
            self.assertEqual(
                {'schema': 1, 'map': '05_prohorovka', 'team': 2,
                 'team_sizes': {'1': 4, '2': 9}},
                json.loads(Path(path).read_text(encoding='utf-8')))

    def test_invalid_waiting_room_state_falls_back_without_blocking_login(self):
        config_module = _load_port_source('config')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'waiting_room_state.json'
            path.write_text(
                '{"schema": 1, "map": "01_karelia", "team": 9, '
                '"team_sizes": {"1": 99}}', encoding='utf-8')

            self.assertEqual(
                {'schema': 1, 'map': None, 'team': 0, 'team_sizes': {}},
                config_module.load_waiting_room_state(str(path)))

    def test_legacy_user_state_is_copied_without_deleting_the_old_file(self):
        config_module = _load_port_source('config')
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / 'mods' / 'configs' / 'state.json'
            target = Path(directory) / 'appdata' / 'state.json'
            legacy.parent.mkdir(parents=True)
            payload = b'{"schema": 1, "saved": true}\n'
            legacy.write_bytes(payload)

            resolved = config_module.migrate_legacy_user_file(
                str(target), str(legacy))

            self.assertEqual(str(target), resolved)
            self.assertEqual(payload, target.read_bytes())
            self.assertEqual(payload, legacy.read_bytes())
            self.assertFalse(Path(str(target) + '.migrate.tmp').exists())

    def test_failed_user_state_migration_keeps_using_the_legacy_file(self):
        config_module = _load_port_source('config')
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / 'legacy.json'
            blocked_parent = Path(directory) / 'not-a-directory'
            target = blocked_parent / 'state.json'
            legacy.write_text('{"schema": 1}\n', encoding='utf-8')
            blocked_parent.write_text('blocked', encoding='utf-8')

            resolved = config_module.migrate_legacy_user_file(
                str(target), str(legacy))

            self.assertEqual(str(legacy), resolved)
            self.assertTrue(legacy.is_file())

    def test_writes_default_and_reads_override(self):
        config_module = _load_port_source('config')
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'config.json')
            config = config_module.load(path)
            self.assertTrue(config['enabled'])
            self.assertEqual('127.0.0.1', config['host'])
            self.assertEqual({}, config['physics_tuning'])
            self.assertTrue(Path(path).is_file())
            Path(path).write_text(
                '{"enabled": false, "host": "10.20.30.40"}',
                encoding='utf-8')
            config = config_module.load(path)
            self.assertFalse(config['enabled'])
            self.assertEqual('10.20.30.40', config['host'])
            saved = json.loads(
                (Path(directory) / 'server_endpoint.json').read_text(
                    encoding='utf-8'))
            self.assertEqual(
                {'schema': 1, 'host': '10.20.30.40', 'port': 28782},
                saved)

    def test_old_config_defaults_to_auto_team_and_process_can_override_it(self):
        config_module = _load_port_source('config')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text('{"enabled": true}', encoding='utf-8')

            config = config_module.load(str(path))

            self.assertEqual(0, config['preferred_team'])
            self.assertEqual(0, config_module.preferred_team(config, {}))
            self.assertEqual(
                2,
                config_module.preferred_team(
                    config, {config_module.PREFERRED_TEAM_ENV: '2'}))
            with self.assertRaises(ValueError):
                config_module.preferred_team(
                    config, {config_module.PREFERRED_TEAM_ENV: '3'})

    def test_schema_one_migrates_completed_native_remote_presentation_on(self):
        config_module = _load_port_source('config')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(
                '{"schema": 1, "native_remote_vehicles": false}',
                encoding='utf-8')

            migrated = config_module.load(str(path))

            self.assertEqual(2, migrated['schema'])
            self.assertTrue(migrated['native_remote_vehicles'])

            path.write_text(
                '{"schema": 2, "native_remote_vehicles": false}',
                encoding='utf-8')
            explicit_fallback = config_module.load(str(path))
            self.assertFalse(explicit_fallback['native_remote_vehicles'])

    def test_user_endpoint_survives_release_config_replacement(self):
        config_module = _load_port_source('config')
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'config.json'
            endpoint_path = Path(directory) / 'server_endpoint.json'
            config_path.write_text(
                '{"host": "127.0.0.1", "port": 28782}',
                encoding='utf-8')
            self.assertTrue(config_module.save_endpoint(
                'lan-host.local', 30000, str(endpoint_path)))

            # A later release replaces config.json but does not distribute or
            # overwrite the user-owned endpoint file.
            config_path.write_text(
                '{"host": "127.0.0.1", "port": 28782, '
                '"prebattleCountdownSeconds": 20}', encoding='utf-8')
            config = config_module.load(
                str(config_path), str(endpoint_path))

            self.assertEqual('lan-host.local', config['host'])
            self.assertEqual(30000, config['port'])
            self.assertEqual(20.0, config['prebattleCountdownSeconds'])

    def test_process_endpoint_override_does_not_replace_saved_lan_room(self):
        config_module = _load_port_source('config')
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'config.json'
            endpoint_path = Path(directory) / 'server_endpoint.json'
            config_path.write_text(
                '{"host": "127.0.0.1", "port": 28782}',
                encoding='utf-8')
            endpoint_path.write_text(
                '{"schema": 1, "host": "lan-host.local", "port": 30000}',
                encoding='utf-8')

            config = config_module.load(
                str(config_path), str(endpoint_path), {
                    config_module.SERVER_HOST_ENV: '127.0.0.1',
                    config_module.SERVER_PORT_ENV: '28782',
                })

            self.assertEqual('127.0.0.1', config['host'])
            self.assertEqual(28782, config['port'])
            self.assertEqual(
                {'schema': 1, 'host': 'lan-host.local', 'port': 30000},
                json.loads(endpoint_path.read_text(encoding='utf-8')))

    def test_invalid_process_endpoint_override_is_rejected(self):
        config_module = _load_port_source('config')
        with tempfile.TemporaryDirectory() as directory:
            config_path = str(Path(directory) / 'config.json')

            with self.assertRaises(ValueError):
                config_module.load(config_path, environ={
                    config_module.SERVER_HOST_ENV: 'bad host',
                })

    def test_invalid_json_is_quarantined_and_replaced_with_defaults(self):
        config_module = _load_port_source('config')
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'config.json'
            config_path.write_text('{not json', encoding='utf-8')

            config = config_module.load(str(config_path))

            self.assertTrue(config['enabled'])
            self.assertEqual('127.0.0.1', config['host'])
            self.assertEqual('{not json', (Path(directory) /
                             'config.json.invalid').read_text(encoding='utf-8'))
            self.assertEqual(
                config_module.DEFAULT_CONFIG,
                json.loads(config_path.read_text(encoding='utf-8')))

    def test_invalid_config_types_cannot_prevent_offline_login(self):
        config_module = _load_port_source('config')
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'config.json'
            endpoint_path = Path(directory) / 'server_endpoint.json'
            config_path.write_text(
                '{"enabled": "yes", "startupTimeoutSeconds": "never"}',
                encoding='utf-8')
            endpoint_path.write_text(
                '{"schema": 1, "host": "lan-host.local", "port": 30000}',
                encoding='utf-8')

            config = config_module.load(
                str(config_path), str(endpoint_path))

            self.assertTrue(config['enabled'])
            self.assertEqual('lan-host.local', config['host'])
            self.assertEqual(30000, config['port'])
            self.assertTrue(
                (Path(directory) / 'config.json.invalid').is_file())

    def test_overflowing_config_number_cannot_prevent_offline_login(self):
        config_module = _load_port_source('config')
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'config.json'
            config_path.write_text(
                '{"max_health": 1e999}', encoding='utf-8')

            config = config_module.load(str(config_path))

            self.assertEqual(
                config_module.DEFAULT_CONFIG['max_health'],
                config['max_health'])
            self.assertTrue(
                (Path(directory) / 'config.json.invalid').is_file())

    def test_failed_endpoint_replace_restores_previous_user_value(self):
        config_module = _load_port_source('config')
        with tempfile.TemporaryDirectory() as directory:
            endpoint_path = Path(directory) / 'server_endpoint.json'
            endpoint_path.write_text(
                '{"schema": 1, "host": "old-host.local", "port": 28782}',
                encoding='utf-8')
            real_rename = config_module.os.rename
            rename_calls = []

            def fail_replacement(source, target):
                rename_calls.append((source, target))
                if len(rename_calls) == 2:
                    raise OSError('replacement failed')
                return real_rename(source, target)

            with mock.patch.object(
                    config_module.os, 'rename', side_effect=fail_replacement):
                self.assertFalse(config_module.save_endpoint(
                    'new-host.local', 30000, str(endpoint_path)))

            saved = json.loads(endpoint_path.read_text(encoding='utf-8'))
            self.assertEqual(
                {'schema': 1, 'host': 'old-host.local', 'port': 28782},
                saved)
            self.assertFalse(Path(str(endpoint_path) + '.tmp').exists())
            self.assertFalse(Path(str(endpoint_path) + '.bak').exists())

    def test_invalid_user_endpoint_fails_safe_to_loopback(self):
        config_module = _load_port_source('config')
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'config.json'
            endpoint_path = Path(directory) / 'server_endpoint.json'
            config_path.write_text(
                '{"host": "10.20.30.40", "port": 30000}',
                encoding='utf-8')
            endpoint_path.write_text(
                '{"schema": 1, "host": "bad host", "port": 70000}',
                encoding='utf-8')

            config = config_module.load(
                str(config_path), str(endpoint_path))

            self.assertEqual('127.0.0.1', config['host'])
            self.assertEqual(28782, config['port'])

    def test_invalid_legacy_endpoint_fails_safe_to_loopback(self):
        config_module = _load_port_source('config')
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / 'config.json'
            config_path.write_text(
                '{"host": "bad host", "port": "not-a-port"}',
                encoding='utf-8')

            config = config_module.load(str(config_path))

            self.assertEqual('127.0.0.1', config['host'])
            self.assertEqual(28782, config['port'])
            self.assertFalse(
                (Path(directory) / 'server_endpoint.json').exists())

    def test_native_picker_endpoint_round_trip_and_validation(self):
        config_module = _load_port_source('config')

        value = config_module.format_endpoint('10.20.30.40', 28782)
        self.assertEqual('LAN SERVER: 10.20.30.40:28782', value)
        self.assertEqual(
            ('10.20.30.40', 28782),
            config_module.parse_endpoint(value))
        self.assertEqual(
            ('wot-host.local', 28782),
            config_module.parse_endpoint('wot-host.local'))
        for invalid in ('', 'host:0', 'host:65536', 'bad host:28782',
                        'http://host:28782'):
            with self.assertRaises(ValueError, msg=invalid):
                config_module.parse_endpoint(invalid)


class _Vector3(object):
    def __init__(self, x=0.0, y=None, z=None):
        if y is None and z is None and hasattr(x, 'x'):
            x, y, z = x.x, x.y, x.z
        elif y is None and z is None:
            y = z = x
        self.x = x
        self.y = y
        self.z = z

    @property
    def length(self):
        return (self.x * self.x + self.y * self.y +
                self.z * self.z) ** 0.5


class _Matrix(object):
    def __init__(self, other=None):
        self.translation = getattr(other, 'translation', None)

    def setRotateYPR(self, value):
        self.rotation = value

    def setTranslate(self, value):
        self.translation = value


class _Entity(object):
    pass


class _Model(object):
    pass


class _Resources(dict):
    failedIDs = ()


class _BigWorld(object):
    def __init__(self, auto_load_resources=True):
        self.entities = {}
        self._callbacks = []
        self._next_callback = 1
        self._now = 0.0
        self._next_entity = 100
        self.keys_down = set()
        self.operations = []
        self.compatibility = None
        self.auto_load_resources = auto_load_resources
        self.pending_resource_callback = None
        self._player = None

    def time(self):
        return self._now

    def callback(self, delay, function):
        callback_id = self._next_callback
        self._next_callback += 1
        self._callbacks.append((callback_id, delay, function))
        return callback_id

    def cancelCallback(self, callback_id):
        self._callbacks = [item for item in self._callbacks
                           if item[0] != callback_id]

    def quit(self):
        self.operations.append(('quit',))

    def run_next(self):
        callback_id, delay, function = self._callbacks.pop(0)
        self._now += delay
        function()

    def worldDrawEnabled(self, enabled):
        self.operations.append(('draw', enabled))

    def setWatcher(self, name, enabled):
        self.operations.append(('watcher', enabled))

    def createSpace(self):
        self.operations.append(('create_space', 7))
        return 7

    def addSpaceGeometryMapping(self, space_id, matrix, path):
        self.operations.append(('add_mapping', space_id, path))
        return 9

    def delSpaceGeometryMapping(self, space_id, mapping_id):
        self.operations.append(('del_mapping', space_id, mapping_id))

    def createEntity(self, entity_type, space_id, client_only, position,
                     orientation, properties):
        entity_id = self._next_entity
        self._next_entity += 1
        entity = _Entity()
        if entity_type == 'Avatar':
            if self.compatibility is not None:
                if not self.compatibility.map_active:
                    raise AssertionError(
                        'offline map must be active before Avatar creation')
            entity.playerVehicleID = 0
        self.entities[entity_id] = entity
        self.operations.append(('create_entity', entity_type))
        return entity_id

    def player(self, value=None):
        if value is not None:
            self._player = value
        return self._player

    def CursorCamera(self):
        return _Entity()

    def camera(self, value):
        self.operations.append(('camera', value))

    def cameraSpaceID(self, value):
        self.operations.append(('camera_space', value))

    def spaceLoadStatus(self):
        return 1.0

    def wg_collideSegment(self, space_id, start, end, flags):
        if start.x == end.x and start.z == end.z:
            return (_Vector3(start.x, 0.0, start.z), None)
        return None

    def loadResourceListBG(self, resources, callback):
        if self.auto_load_resources:
            callback(_Resources(tank_model=_Model()))
        else:
            self.pending_resource_callback = callback

    def isKeyDown(self, key):
        return key in self.keys_down

    def isClientSpace(self, space_id):
        return True

    def clearEntitiesAndSpaces(self):
        self.operations.append(('clear_entities_spaces',))

    def clearAllSpaces(self):
        self.operations.append(('clear_all_spaces',))

    def clearSpace(self, space_id):
        self.operations.append(('clear_space', space_id))

    def releaseSpace(self, space_id):
        self.operations.append(('release_space', space_id))


class _Compatibility(object):
    def __init__(self, bigworld):
        self.bigworld = bigworld
        self.map_active = False
        self.connected = True
        bigworld.compatibility = self

    def is_ready(self):
        return self.connected

    def activate_map(self):
        self.map_active = True
        self.bigworld.operations.append(('activate_map',))

    def prepare_avatar(self, avatar):
        avatar.inputHandler = object()
        avatar.playLimits = {}
        self.bigworld.operations.append(('prepare_avatar',))

    def deactivate_map(self):
        self.map_active = False
        self.bigworld.operations.append(('deactivate_map',))

    def disconnect(self):
        self.connected = False
        self.bigworld.operations.append(('disconnect',))


class _VerticalOfflineMapCreator(object):
    def __init__(self, bigworld, compatibility, app_loader):
        self.bigworld = bigworld
        self.compatibility = compatibility
        self.app_loader = app_loader
        self.active = False
        self.space_id = None
        self.mapping_id = None

    def Active(self):
        return self.active

    def create(self, map_name):
        self.app_loader.showBattlePage()
        self.space_id = self.bigworld.createSpace()
        self.mapping_id = self.bigworld.addSpaceGeometryMapping(
            self.space_id, None, 'spaces/' + map_name)
        self.compatibility.activate_map()
        avatar_id = self.bigworld.createEntity(
            'Avatar', self.space_id, 0, _Vector3(50.0, 0.0, 50.0),
            (0.0, 0.0, 0.0), {})
        avatar = self.bigworld.entities[avatar_id]
        avatar.id = avatar_id
        avatar.spaceID = self.space_id
        self.bigworld.player(avatar)
        self.active = True

    def destroy(self):
        self.bigworld.operations.append(('offline_map_destroy',))
        if self.space_id is not None and self.mapping_id is not None:
            self.bigworld.delSpaceGeometryMapping(
                self.space_id, self.mapping_id)
        self.bigworld.clearEntitiesAndSpaces()
        self.compatibility.deactivate_map()
        self.app_loader.destroyBattle()
        self.active = False


class _AppLoader(object):
    def __init__(self, operations):
        self.operations = operations

    def createBattle(self, arena_gui_type):
        self.operations.append(('create_battle', arena_gui_type))

    def showBattleLoading(self):
        self.operations.append(('show_battle_loading',))
        return True

    def showBattlePage(self):
        self.operations.append(('show_battle_page',))
        return True

    def destroyBattle(self):
        self.operations.append(('destroy_battle',))
        return True

    def showLogin(self):
        self.operations.append(('show_login',))
        return True


class _CompatEvent(object):
    def __init__(self, operations, name):
        self.operations = operations
        self.name = name

    def __call__(self, *args):
        self.operations.append((self.name,) + args)


class _CompatSubscriptionEvent(object):
    def __init__(self, operations, name):
        self.operations = operations
        self.name = name

    def __iadd__(self, callback):
        self.operations.append((self.name + '_add', callback))
        return self

    def __isub__(self, callback):
        self.operations.append((self.name + '_remove', callback))
        return self


class _CompatChatManager(object):
    def __init__(self, operations):
        self.operations = operations
        self.playerProxy = None

    def switchPlayerProxy(self, player):
        if self.playerProxy is not None:
            # Exact #1513 cleans the previous proxy before assigning the new
            # one.  Dereferencing this field makes a bulk-cleared zombie
            # Account fail in the same place as the native client.
            callbacks = self.playerProxy._ClientChat__chatActionCallbacks
            self.operations.append(('chat_cleanup', len(callbacks)))
        self.playerProxy = player
        self.operations.append(('chat_proxy', player))


class _CompatTargetController(object):
    """Callable #1513 TargetMatrix surface, not a plain Python function."""

    def __init__(self, owner, operations):
        self._owner = owner
        self._operations = operations
        self.source = None
        self.maxDistance = 0.0
        self.selectionFovDegrees = 0.0
        self.deselectionFovDegrees = 0.0
        self.skeletonCheckEnabled = False
        self.isEnabled = False
        self.exclude = None

    @property
    def entity(self):
        return self._owner._target

    def __call__(self):
        return self._owner._target

    def caps(self, *args):
        self._operations.append(('target_caps', args))

    def clear(self):
        self._operations.append(('target_clear',))
        self._owner._target = None


class _CompatBigWorld(object):
    _MISSING = object()

    def __init__(self, operations):
        self.operations = operations
        self.account_type = None
        self._callbacks = []
        self.entities = {}
        self._player = None
        self._target = None
        self.target = _CompatTargetController(self, operations)
        self._next_entity = 1
        self._now = 100.0

    def time(self):
        return self._now

    def serverTime(self):
        # A client-only BigWorld connection does not receive retail server
        # clock samples, so this value remains frozen until the compatibility
        # layer supplies the scoped offline battle clock.
        return 500.0

    def connect(self, server, login_params, progress):
        self.operations.append(('original_connect', server))
        return 'online-connect'

    def disconnect(self):
        self.operations.append(('original_disconnect',))
        return 'online-disconnect'

    def createSpace(self):
        self.operations.append(('account_space',))
        return 21

    def createEntity(self, entity_type, space_id, client_only, position,
                     orientation, properties):
        self.operations.append(('account_entity', entity_type))
        if entity_type != 'Account':
            raise AssertionError(entity_type)
        entity_id = self._next_entity
        self._next_entity += 1
        self.entities[entity_id] = self.account_type()
        return entity_id

    def AvatarFilter(self):
        return _CompatAvatarFilter(self.operations)

    def MouseTargettingMatrix(self):
        return object()

    def MouseTargetingMatrix(self):
        return object()

    def player(self, value=_MISSING):
        if value is not self._MISSING:
            self._player = value
            self.operations.append(('player', value))
            on_become_player = getattr(value, 'onBecomePlayer', None)
            if callable(on_become_player):
                on_become_player()
        return self._player

    def clearAllSpaces(self):
        self.operations.append(('clear_all_spaces',))
        player = self._player
        if player is not None:
            retire = getattr(player, 'onBecomeNonPlayer', None)
            if callable(retire):
                retire()
        retired = list(self.entities.values())
        if player is not None and player not in retired:
            retired.append(player)
        self.entities.clear()
        self._player = None
        for entity in retired:
            entity.__dict__.clear()

    def WGC_onServerResponse(self, accepted):
        self.operations.append(('wgc', accepted))

    def callback(self, delay, function):
        self._callbacks.append((delay, function))
        return len(self._callbacks)


class _CompatConnectionManager(object):
    def __init__(self, bigworld, statuses, operations):
        self.bigworld = bigworld
        self.statuses = statuses
        self.operations = operations
        self._ConnectionManager__connectionStatus = statuses.NOT_SET
        self.onLoggedOn = _CompatEvent(operations, 'logged_on')
        self.onConnected = _CompatEvent(operations, 'connected')
        self.onDisconnected = _CompatEvent(operations, 'disconnected')

    def initiateConnection(self, params, password, server):
        self.operations.append(('initiate', server))

        def progress(stage, status, response):
            self._ConnectionManager__connectionStatus = status
            self.operations.append(('progress', stage, status))
            if stage == 1 and status == self.statuses.LOGGED_ON:
                self.bigworld.WGC_onServerResponse(True)
                self.onLoggedOn({})
                self.onConnected()

        return self.bigworld.connect(server, params, progress)

    def disconnect(self):
        self.operations.append(('manager_disconnect',))
        return self.bigworld.disconnect()

    def isConnected(self):
        return (self._ConnectionManager__connectionStatus ==
                self.statuses.LOGGED_ON)


class _OfflineMapCreator(object):
    def __init__(self, operations):
        self.active = False
        self.operations = operations

    def SetActive(self, active):
        self.active = active
        self.operations.append(('map_active', active))


class _PrbLoader(object):
    def __init__(self, operations):
        self.operations = operations
        self.dispatcher = None

    def createBattleDispatcher(self):
        self.operations.append(('prb_dispatcher_create',))
        if self.dispatcher is None:
            self.dispatcher = object()

    def getDispatcher(self):
        return self.dispatcher


class _SoundGroups(object):
    def __init__(self, bigworld, operations):
        self.bigworld = bigworld
        self.operations = operations

    def destroy(self):
        player = self.bigworld.player()
        self.operations.append(('sound_destroy_player', player))
        if player is not None and player.inputHandler is not None:
            self.operations.append(('sound_input_handler',))


class _CompatAvatarFilter(object):
    def __init__(self, operations):
        self.operations = operations

    def enableLagDetection(self, enabled):
        self.operations.append(('avatar_filter_lag', enabled))

    def syncVector3(self, *args):
        return None

    def getVector3(self, *args):
        return None

    def resetVector3(self, *args):
        return None

    def setInterpolationType(self, *args):
        return None

    def set(self, time_value, space_id, entity_id, position, rotation,
            error):
        self.operations.append((
            'avatar_filter_set', time_value, space_id, entity_id,
            position, rotation, error))


class _Hosts(object):
    def __init__(self, existing=None, fail=False):
        self._hosts = list(existing or ())
        self.fail = fail

    def _makeHostItem(self, name, short_name, url):
        if self.fail:
            raise RuntimeError('host creation failed')
        return types.SimpleNamespace(name=name, shortName=short_name, url=url)


class _PreferencesSection(object):
    """The subset of a BigWorld DataSection AccountSettings touches."""

    def __init__(self, values=None):
        self._values = dict(values or {})
        self._children = []

    def items(self):
        return list(self._children)

    def readString(self, key, default=''):
        return self._values.get(key, default)

    def writeString(self, key, value):
        self._values[key] = value

    def createSection(self, key):
        section = _PreferencesSection()
        self._children.append((key, section))
        return section


def _fake_account_settings(accounts=None):
    """Build the #1513 AccountSettings surface the offline pin replaces."""
    root = _PreferencesSection()
    for login in (accounts or ()):
        root.createSection('account').writeString('login', login)

    class AccountSettings(object):
        version = 33
        _AccountSettings__cache = {'login': None, 'section': None}
        _AccountSettings__isFirstRun = True
        converted = []

        @staticmethod
        def convert():
            AccountSettings.converted.append('convert')

        @staticmethod
        def invalidateNewSettingsCounter():
            AccountSettings.converted.append('invalidate')

        @staticmethod
        def _AccountSettings__readSection(data_section, name):
            return root

        @staticmethod
        def _AccountSettings__readUserSection():
            return None

    module = types.ModuleType('account_helpers.AccountSettings')
    module.AccountSettings = AccountSettings
    module.KEY_FILTERS = 'filters'
    module.DEFAULT_VALUES = {'filters': {}}
    package = types.ModuleType('account_helpers')
    package.AccountSettings = AccountSettings
    settings = types.ModuleType('Settings')
    settings.KEY_ACCOUNT_SETTINGS = 'accounts'
    settings.g_instance = types.SimpleNamespace(userPrefs=object())
    return root, module, package, settings


class OfflineCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.preferences, module, package, settings = _fake_account_settings()
        self._saved_modules = {}
        for name, value in (('account_helpers', package),
                            ('account_helpers.AccountSettings', module),
                            ('Settings', settings)):
            self._saved_modules[name] = sys.modules.get(name)
            sys.modules[name] = value

    def tearDown(self):
        for name, value in self._saved_modules.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value

    def test_the_garage_survives_the_account_rebuild_after_a_battle(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility._account_context = {'selected_vehicle': {
            'vehicles': [{'id': 1, 'compDescr': b'v', 'eqs': [1, 2, 3]}]}}

        first = compatibility.seed_account_context()
        # The player empties a slot; #1513 then leaves battle, which destroys
        # the lobby Account and constructs another one.
        first['garage'].snapshot()['vehicles'][0]['eqs'] = [1, 0, 3]
        second = compatibility.seed_account_context()

        self.assertIs(first['garage'], second['garage'])
        self.assertEqual(
            [1, 0, 3], second['selected_vehicle']['vehicles'][0]['eqs'])
        # The bootstrap snapshot stays the seed, not a second live copy.
        self.assertEqual(
            [1, 2, 3],
            compatibility._account_context[
                'selected_vehicle']['vehicles'][0]['eqs'])

    def test_battle_interface_settings_survive_client_restart(self):
        compatibility_module = _load_port_source('compat')
        from gui.mods.offline_lan_0922.account_rpc import commands
        from gui.mods.offline_lan_0922.account_rpc.state import AccountState

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'account_state.json')
            compatibility = compatibility_module.OfflineCompatibility(
                self._runtime()[0])
            compatibility._account_state = AccountState(path)

            result = compatibility.dispatch_account_int_command(
                commands.CMD_ADD_INT_USER_SETTINGS, [54, 3, 81, 1])

            self.assertEqual((commands.RES_SUCCESS, ''), result)
            self.assertEqual({54: 3, 81: 1}, AccountState(path).snapshot())

    def test_offline_battle_server_time_advances_and_restores(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        original = runtime.bigworld.__class__.__dict__['serverTime']
        compatibility = compatibility_module.OfflineCompatibility(runtime)

        compatibility.install()
        self.assertEqual(500.0, runtime.bigworld.serverTime())
        compatibility.configure_battle()
        self.assertEqual(500.0, runtime.bigworld.serverTime())

        runtime.bigworld._now += 4.25
        self.assertEqual(504.25, runtime.bigworld.serverTime())

        compatibility.deactivate_map()
        self.assertEqual(500.0, runtime.bigworld.serverTime())
        compatibility.fini()
        self.assertIs(original, runtime.bigworld.__class__.serverTime)

    def test_offline_current_shell_change_defers_stock_optimistic_update(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        settings = types.SimpleNamespace(CURRENT_SHELLS=0, NEXT_SHELLS=1)
        runtime.constants.VEHICLE_SETTING = settings
        calls = []
        state = {'alive': True, 'on_arena': True}

        getter = types.SimpleNamespace(
            isVehicleAlive=lambda unused_avatar: state['alive'],
            isPlayerOnArena=lambda unused_avatar: state['on_arena'],
            updateVehicleSetting=lambda code, value, unused_avatar: \
                calls.append(('update', code, value)),
            changeVehicleSetting=lambda code, value, unused_avatar: \
                calls.append(('change', code, value)))
        runtime.avatar_getter = getter

        class AmmoController(object):
            def __init__(self, code):
                self.code = code

            def getNextSettingCode(self, unused_int_cd):
                return self.code

            def changeSetting(self, int_cd, avatar=None):
                if not getter.isVehicleAlive(avatar):
                    return False
                code = self.getNextSettingCode(int_cd)
                if code is None:
                    return False
                getter.updateVehicleSetting(code, int_cd, avatar)
                if getter.isPlayerOnArena(avatar):
                    getter.changeVehicleSetting(code, int_cd, avatar)
                return True

        runtime.ammo_controller_type = AmmoController
        original = AmmoController.__dict__['changeSetting']
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()

        controller = AmmoController(settings.CURRENT_SHELLS)
        self.assertTrue(controller.changeSetting(102, avatar='outside'))
        self.assertEqual([
            ('update', settings.CURRENT_SHELLS, 102),
            ('change', settings.CURRENT_SHELLS, 102),
        ], calls)

        compatibility.configure_battle()
        calls[:] = []
        self.assertTrue(controller.changeSetting(102, avatar='battle'))
        self.assertEqual([
            ('change', settings.CURRENT_SHELLS, 102),
        ], calls)

        calls[:] = []
        controller.code = settings.NEXT_SHELLS
        self.assertTrue(controller.changeSetting(102, avatar='battle'))
        self.assertEqual([
            ('update', settings.NEXT_SHELLS, 102),
            ('change', settings.NEXT_SHELLS, 102),
        ], calls)

        calls[:] = []
        controller.code = settings.CURRENT_SHELLS
        state['on_arena'] = False
        self.assertTrue(controller.changeSetting(102, avatar='loading'))
        self.assertEqual([
            ('update', settings.CURRENT_SHELLS, 102),
        ], calls)

        calls[:] = []
        state['alive'] = False
        self.assertFalse(controller.changeSetting(102, avatar='dead'))
        self.assertEqual([], calls)

        compatibility.fini()
        self.assertIs(original, AmmoController.__dict__['changeSetting'])

    def test_offline_battle_debug_panel_uses_lan_transport_health(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()

        class DebugPanel(object):
            def updateDebugInfo(self, ping, fps, isLaggingNow,
                                fpsReplay=-1):
                operations.append(
                    ('debug_info', ping, fps, isLaggingNow, fpsReplay))

        runtime.debug_panel_type = DebugPanel
        original = DebugPanel.__dict__['updateDebugInfo']
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()
        panel = DebugPanel()

        # The global patch must preserve stock diagnostics outside the
        # explicit LAN-battle lifetime, even when a client is attached.
        compatibility.set_battle_network_client(types.SimpleNamespace(
            connected=True, rtt_ms=42.6))
        panel.updateDebugInfo(999, 60, True, 55)
        self.assertEqual(
            ('debug_info', 999, 60, True, 55), operations[-1])

        compatibility.configure_battle()
        panel.updateDebugInfo(999, 60, True, 55)
        self.assertEqual(
            ('debug_info', 43, 60, False, 55), operations[-1])

        compatibility.set_battle_network_client(types.SimpleNamespace(
            connected=True, rtt_ms=None))
        panel.updateDebugInfo(999, 59, True)
        self.assertEqual(
            ('debug_info', 0, 59, False, -1), operations[-1])

        compatibility.set_battle_network_client(types.SimpleNamespace(
            connected=False, rtt_ms=43.2))
        panel.updateDebugInfo(1, 58, False)
        self.assertEqual(
            ('debug_info', 43, 58, True, -1), operations[-1])

        compatibility.deactivate_map()
        panel.updateDebugInfo(999, 57, True)
        self.assertEqual(
            ('debug_info', 999, 57, True, -1), operations[-1])
        self.assertIsNone(compatibility._battle_network_client)

        compatibility.fini()
        self.assertIs(original, DebugPanel.__dict__['updateDebugInfo'])

    def test_native_ready_is_deferred_until_runtime_bridge_attaches(self):
        compatibility_module = _load_port_source('compat')
        deferred = compatibility_module._DeferredAvatarServer()
        target = mock.Mock()

        deferred.setClientReady()
        deferred.autoAim(0)
        deferred.attach(target)

        self.assertEqual(
            [mock.call.setClientReady(), mock.call.autoAim(0)],
            target.mock_calls)

    def test_vehicle_enter_wraps_stock_handler_with_two_phase_barrier(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        avatar = runtime.avatar_module.PlayerAvatar()

        class Server(object):
            def prepareVehicleEnter(self, vehicle):
                operations.append(('prepare_vehicle_enter', vehicle.id))

            def acceptVehicleEnter(self, vehicle_id):
                operations.append(('accept_vehicle_enter', vehicle_id))

            def completeVehicleEnter(self, vehicle_id):
                operations.append(('complete_vehicle_enter', vehicle_id))

        avatar.fakeServer = Server()
        avatar.vehicle_onEnterWorld(types.SimpleNamespace(id=91))

        names = [item[0] for item in operations]
        self.assertLess(names.index('prepare_vehicle_enter'),
                        names.index('accept_vehicle_enter'))
        self.assertLess(names.index('accept_vehicle_enter'),
                        names.index('original_avatar_vehicle_enter'))
        self.assertLess(names.index('original_avatar_vehicle_enter'),
                        names.index('complete_vehicle_enter'))
        compatibility.fini()

    def test_enemy_is_hidden_before_vehicle_enter_barrier_completes(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle(player_team=1)
        avatar = runtime.avatar_module.PlayerAvatar()
        avatar.team = 1
        avatar.playerVehicleID = 10

        class Server(object):
            def acceptVehicleEnter(self, vehicle_id):
                operations.append(('accept_vehicle_enter', vehicle_id))

            def completeVehicleEnter(self, vehicle_id):
                operations.append(('complete_vehicle_enter', vehicle_id))

        class Provider(object):
            def stopVehicleVisual(self, vehicle_id, is_player):
                operations.append(
                    ('stop_vehicle_visual', vehicle_id, is_player))

        enemy = types.SimpleNamespace(
            id=11, publicInfo={'team': 2}, targetCaps=[1],
            model=types.SimpleNamespace(visible=True))

        def show(visible):
            operations.append(('vehicle_show', visible))
            enemy.model.visible = bool(visible)

        enemy.show = show
        avatar.fakeServer = Server()
        avatar.guiSessionProvider = Provider()

        avatar.vehicle_onEnterWorld(enemy)

        names = [item[0] for item in operations]
        self.assertLess(names.index('original_avatar_vehicle_enter'),
                        names.index('vehicle_show'))
        self.assertLess(names.index('vehicle_show'),
                        names.index('stop_vehicle_visual'))
        self.assertLess(names.index('stop_vehicle_visual'),
                        names.index('complete_vehicle_enter'))
        self.assertFalse(enemy.model.visible)
        self.assertEqual([], enemy.targetCaps)
        self.assertFalse(enemy._spot_visible)
        self.assertFalse(enemy._offlineNativeDrawVisible)
        self.assertFalse(enemy._offlineNativeMarkerVisible)
        compatibility.fini()

    def test_enemy_stays_hidden_after_full_stock_vehicle_enter_lifecycle(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()

        def stock_vehicle_enter(vehicle, prereqs):
            operations.append(('stock_vehicle_enter_before', prereqs))
            runtime.bigworld.player().vehicle_onEnterWorld(vehicle)
            # Exact #1513 may finish startVisual/model refresh after the
            # nested PlayerAvatar callback has returned. Simulate that late
            # stock edge explicitly; the compatibility layer must not skip it.
            operations.append(('stock_vehicle_controllers_started',))
            vehicle.show(True)
            vehicle.targetCaps = [1]
            vehicle._offlineNativeMarkerVisible = True
            operations.append(('stock_vehicle_enter_after',))
            return 'stock-entered'

        vehicle_type = runtime.vehicle_module.Vehicle
        vehicle_type.onEnterWorld = stock_vehicle_enter
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle(player_team=1)
        avatar = runtime.avatar_module.PlayerAvatar()
        avatar.team = 1
        avatar.playerVehicleID = 10

        class Server(object):
            def acceptVehicleEnter(self, vehicle_id):
                operations.append(('accept_vehicle_enter', vehicle_id))

            def completeVehicleEnter(self, vehicle_id):
                operations.append(('complete_vehicle_enter', vehicle_id))

        class Provider(object):
            def stopVehicleVisual(self, vehicle_id, is_player):
                operations.append(
                    ('stop_vehicle_visual', vehicle_id, is_player))

        enemy = vehicle_type()
        enemy.id = 11
        enemy.publicInfo = {'team': 2}
        enemy.targetCaps = [1]
        enemy.model = types.SimpleNamespace(visible=True)

        def show(visible):
            operations.append(('vehicle_show', visible))
            enemy.model.visible = bool(visible)

        enemy.show = show
        avatar.fakeServer = Server()
        avatar.guiSessionProvider = Provider()
        runtime.bigworld._player = avatar

        result = enemy.onEnterWorld('prereqs')

        self.assertEqual('stock-entered', result)
        self.assertIn(('stock_vehicle_controllers_started',), operations)
        show_values = [item[1] for item in operations
                       if item[0] == 'vehicle_show']
        self.assertEqual([False, True, False], show_values)
        names = [item[0] for item in operations]
        self.assertLess(names.index('stock_vehicle_enter_after'),
                        len(names) - 1)
        self.assertEqual('stop_vehicle_visual', names[-1])
        self.assertFalse(enemy.model.visible)
        self.assertEqual([], enemy.targetCaps)
        self.assertFalse(enemy._spot_visible)
        self.assertFalse(enemy._offlineNativeDrawVisible)
        self.assertFalse(enemy._offlineNativeMarkerVisible)

        compatibility.fini()
        self.assertIs(stock_vehicle_enter,
                      vehicle_type.__dict__['onEnterWorld'])

    def test_enemy_start_visual_initializes_stock_without_first_spot_flash(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        vehicle_type = runtime.vehicle_module.Vehicle

        def stock_show(vehicle, visible):
            operations.append(('stock_vehicle_show', vehicle.id, visible))
            vehicle.model.visible = bool(visible)

        def stock_start_visual(vehicle):
            operations.append(('stock_visual_controllers_started', vehicle.id))
            vehicle.show(True)
            vehicle.guiSessionProvider.startVehicleVisual(vehicle, True)
            vehicle.isStarted = True
            return 'stock-started'

        vehicle_type.show = stock_show
        vehicle_type.startVisual = stock_start_visual
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle(player_team=1)
        avatar = runtime.avatar_module.PlayerAvatar()
        avatar.team = 1
        avatar.playerVehicleID = 10

        class Provider(object):
            def startVehicleVisual(self, vehicle, is_immediate):
                operations.append((
                    'start_vehicle_visual', vehicle.id, is_immediate))

            def stopVehicleVisual(self, vehicle_id, is_player):
                operations.append((
                    'stop_vehicle_visual', vehicle_id, is_player))

        provider = Provider()
        avatar.guiSessionProvider = provider
        runtime.bigworld._player = avatar
        enemy = vehicle_type()
        enemy.id = 11
        enemy.publicInfo = {'team': 2}
        enemy.guiSessionProvider = provider
        enemy.targetCaps = [1]
        enemy.model = types.SimpleNamespace(visible=True)
        enemy.isStarted = False

        result = enemy.startVisual()

        self.assertEqual('stock-started', result)
        self.assertIn(
            ('stock_visual_controllers_started', 11), operations)
        self.assertNotIn(('stock_vehicle_show', 11, True), operations)
        self.assertNotIn(('start_vehicle_visual', 11, True), operations)
        self.assertFalse(enemy.model.visible)
        self.assertEqual([], enemy.targetCaps)

        # A later LAN spotting edge is outside the exact stock startVisual
        # call and must still be able to reveal both model and native UI.
        enemy._offlineNativeDrawVisible = True
        enemy._offlineNativeMarkerVisible = True
        enemy.show(True)
        provider.startVehicleVisual(enemy, True)
        self.assertIn(('stock_vehicle_show', 11, True), operations)
        self.assertIn(('start_vehicle_visual', 11, True), operations)

        compatibility.fini()
        self.assertIs(stock_start_visual,
                      vehicle_type.__dict__['startVisual'])

    def test_compatibility_does_not_replace_stock_vehicle_filters(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        avatar = runtime.avatar_module.PlayerAvatar()
        avatar.spaceID = 7

        class Server(object):
            vehicle_id = 10

            def acceptVehicleEnter(self, vehicle_id):
                return vehicle_id == self.vehicle_id

            def completeVehicleEnter(self, vehicle_id):
                return True

        avatar.fakeServer = Server()
        original_filter = object()
        local = types.SimpleNamespace(
            id=10, filter=original_filter,
            position=_Vector3(0.0, 0.0, 0.0), yaw=0.25)
        remote = types.SimpleNamespace(
            id=11, filter=object(),
            position=_Vector3(1.0, 2.0, 3.0), yaw=-0.5)

        avatar.vehicle_onEnterWorld(local)
        avatar.vehicle_onEnterWorld(remote)

        self.assertIs(original_filter, local.filter)
        self.assertIs(original_filter, local.filter)
        self.assertNotIsInstance(remote.filter, _CompatAvatarFilter)
        self.assertFalse(any(item[0] == 'avatar_filter_set'
                             for item in operations))
        compatibility.fini()

    def test_vehicle_accept_failure_is_latched_before_stock_handler(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        avatar = runtime.avatar_module.PlayerAvatar()

        class Server(object):
            def acceptVehicleEnter(self, vehicle_id):
                operations.append(('accept_vehicle_enter', vehicle_id))
                raise RuntimeError('select failed')

            def failVehicleEnter(self, vehicle_id, error):
                operations.append(
                    ('fail_vehicle_enter', vehicle_id, str(error)))

        avatar.fakeServer = Server()
        with self.assertRaisesRegex(RuntimeError, 'select failed'):
            avatar.vehicle_onEnterWorld(types.SimpleNamespace(id=91))

        names = [item[0] for item in operations]
        self.assertIn('fail_vehicle_enter', names)
        self.assertNotIn('original_avatar_vehicle_enter', names)
        compatibility.fini()

    def test_fini_arms_one_shot_sound_guard_for_zombie_account(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()
        zombie = object.__new__(runtime.account_module.PlayerAccount)
        zombie.__dict__.clear()
        runtime.bigworld._player = zombie
        compatibility.disconnect = lambda: None

        compatibility.fini()
        runtime.sound_groups_module.g_instance.destroy()

        self.assertIn(('sound_destroy_player', None), operations)
        self.assertNotIn(
            'destroy', runtime.sound_groups_module.g_instance.__dict__)
        self.assertIs(zombie, runtime.bigworld.player())

    def test_control_mode_listener_runs_after_completed_native_transition(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        handler_type = runtime.avatar_input_handler.AvatarInputHandler
        original = handler_type.__dict__['onControlModeChanged']
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        listener = mock.Mock()

        compatibility.set_control_mode_listener(listener)
        handler = handler_type()
        result = handler.onControlModeChanged('sniper', source='wheel')

        self.assertEqual('changed', result)
        self.assertIn(
            ('control_mode', 'sniper', {'source': 'wheel'}), operations)
        listener.assert_called_once_with(handler, 'sniper')

        compatibility.fini()
        self.assertIs(original, handler_type.__dict__['onControlModeChanged'])

    def test_failed_strategic_tick_keeps_the_spg_camera_loop_alive(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        camera_type = runtime.strategic_camera_type
        original = camera_type.__dict__['_StrategicCamera__cameraUpdate']
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        camera = camera_type()
        camera_type.ticks = 0
        camera_type.failures = 1

        with contextlib.redirect_stdout(io.StringIO()):
            delay = camera._StrategicCamera__cameraUpdate()
            recovered = camera._StrategicCamera__cameraUpdate()

        # A zero delay is what CallbackDelayer needs to re-arm the tick.
        self.assertEqual(0.0, delay)
        self.assertEqual(0.0, recovered)
        self.assertEqual(2, camera_type.ticks)

        compatibility.fini()
        self.assertIs(
            original,
            camera_type.__dict__['_StrategicCamera__cameraUpdate'])

    def test_marker_cache_refreshes_when_local_vehicle_identity_arrives(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        marker_type = runtime.vehicle_marker_plugin_type
        original_start = marker_type.__dict__['start']
        original_stop = marker_type.__dict__['stop']
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()

        arena_dp = types.SimpleNamespace(
            player_vehicle_id=0,
            getPlayerVehicleID=lambda: arena_dp.player_vehicle_id,
            isRequiredDataExists=lambda: True,
            getVehicleInfo=lambda vehicle_id: types.SimpleNamespace(
                vehicleID=int(vehicle_id), team=1))
        plugin = marker_type(arena_dp)
        plugin.start()
        self.assertEqual(
            0, plugin._VehicleMarkerPlugin__playerVehicleID)
        self.assertEqual('from-ally', plugin.getVehicleDamageType(91))

        arena_dp.player_vehicle_id = 91
        self.assertTrue(
            compatibility.synchronise_vehicle_marker_identity(91))
        self.assertEqual(
            91, plugin._VehicleMarkerPlugin__playerVehicleID)
        self.assertEqual('from-player', plugin.getVehicleDamageType(91))
        self.assertTrue(compatibility.assert_vehicle_marker_identity(91))
        avatar = types.SimpleNamespace(
            guiSessionProvider=plugin.sessionProvider)
        self.assertTrue(compatibility.assert_vehicle_marker_damage_type(
            avatar, 91))

        old_plugin = weakref.ref(plugin)
        plugin.stop()
        self.assertEqual({}, compatibility._vehicle_marker_plugins)
        del avatar
        del plugin
        gc.collect()
        self.assertIsNone(old_plugin())

        compatibility.deactivate_map()
        compatibility.configure_battle()
        next_arena_dp = types.SimpleNamespace(
            player_vehicle_id=92,
            getPlayerVehicleID=lambda force=False:
                next_arena_dp.player_vehicle_id,
            isRequiredDataExists=lambda: True,
            getVehicleInfo=lambda vehicle_id: types.SimpleNamespace(
                vehicleID=int(vehicle_id), team=1))
        self.assertTrue(
            compatibility.synchronise_vehicle_marker_identity(92))
        next_plugin = marker_type(next_arena_dp)
        next_plugin.start()
        self.assertEqual(
            92, next_plugin._VehicleMarkerPlugin__playerVehicleID)
        self.assertEqual(
            'from-player', next_plugin.getVehicleDamageType(92))
        self.assertEqual(
            [next_plugin],
            list(compatibility._vehicle_marker_plugins.values()))
        next_plugin.stop()
        self.assertEqual({}, compatibility._vehicle_marker_plugins)
        self.assertEqual(
            [('vehicle_marker_start',), ('vehicle_marker_stop',),
             ('vehicle_marker_start',), ('vehicle_marker_stop',)],
            [item for item in operations
             if item[0].startswith('vehicle_marker_')])
        compatibility.fini()
        self.assertIs(original_start, marker_type.__dict__['start'])
        self.assertIs(original_stop, marker_type.__dict__['stop'])

    def test_active_marker_provider_chain_classifies_local_hit_from_player(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        class ArenaDP(object):
            player_vehicle_id = 91

            def getPlayerVehicleID(self, force=False):
                return self.player_vehicle_id

            def isRequiredDataExists(self):
                return True

            def getVehicleInfo(self, vehicle_id):
                return types.SimpleNamespace(
                    vehicleID=int(vehicle_id), team=1)

        arena_dp = ArenaDP()
        plugin = runtime.vehicle_marker_plugin_type(arena_dp)
        plugin.start()
        emitted = []

        class FeedbackAdaptor(object):
            def __init__(self):
                setattr(
                    self, '_BattleFeedbackAdaptor__arenaDP',
                    weakref.proxy(arena_dp))

            def setVehicleNewHealth(self, vehicle_id, health,
                                    attacker_id, reason_id):
                self._setVehicleHealthChanged(
                    vehicle_id, health, attacker_id, reason_id)

            def _setVehicleHealthChanged(self, vehicle_id, health,
                                         attacker_id, reason_id):
                feedback_arena_dp = getattr(
                    self, '_BattleFeedbackAdaptor__arenaDP')
                attacker_info = (
                    feedback_arena_dp.getVehicleInfo(attacker_id)
                    if attacker_id else None)
                value = (health, attacker_info, reason_id)
                emitted.append(('vehicle-health', vehicle_id, value))
                handler = getattr(
                    plugin,
                    '_VehicleMarkerPlugin__onVehicleFeedbackReceived')
                handler('vehicle-health', vehicle_id, value)

        class SessionProvider(object):
            def __init__(self, feedback):
                self.feedback = feedback
                self.shared = types.SimpleNamespace(feedback=feedback)

            def getArenaDP(self):
                return arena_dp

            def setVehicleHealth(self, is_player, vehicle_id, health,
                                 attacker_id, reason_id):
                if not is_player:
                    self.feedback.setVehicleNewHealth(
                        vehicle_id, health, attacker_id, reason_id)

        provider = SessionProvider(FeedbackAdaptor())
        plugin.sessionProvider = provider
        avatar = types.SimpleNamespace(guiSessionProvider=provider)

        self.assertTrue(
            compatibility.synchronise_vehicle_marker_identity(91))
        self.assertEqual(
            [plugin],
            list(compatibility._vehicle_marker_plugins.values()))
        self.assertTrue(compatibility.assert_vehicle_marker_damage_type(
            avatar, 91))
        provider.setVehicleHealth(False, 2001, 450, 91, 0)

        self.assertEqual(1, len(emitted))
        self.assertEqual('vehicle-health', emitted[0][0])
        self.assertEqual(2001, emitted[0][1])
        self.assertEqual(450, emitted[0][2][0])
        self.assertEqual(91, emitted[0][2][1].vehicleID)
        self.assertEqual(0, emitted[0][2][2])
        self.assertEqual([
            (2001, 'updateHealth', 450,
             runtime.vehicle_marker_damage_type.FROM_PLAYER, 'shot')],
            plugin.marker_updates)
        plugin.stop()
        compatibility.fini()

    def test_marker_damage_boundary_rejects_missing_feedback_adaptor(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        arena_dp = types.SimpleNamespace(
            player_vehicle_id=91,
            getPlayerVehicleID=lambda force=False: 91,
            isRequiredDataExists=lambda: True,
            getVehicleInfo=lambda vehicle_id: types.SimpleNamespace(
                vehicleID=int(vehicle_id), team=1))
        plugin = runtime.vehicle_marker_plugin_type(arena_dp)
        plugin.start()
        plugin.sessionProvider.shared.feedback = None
        avatar = types.SimpleNamespace(
            guiSessionProvider=plugin.sessionProvider)

        self.assertTrue(
            compatibility.synchronise_vehicle_marker_identity(91))
        with self.assertRaisesRegex(
                RuntimeError, 'battle feedback adaptor is unavailable'):
            compatibility.assert_vehicle_marker_damage_type(avatar, 91)

        plugin.stop()
        compatibility.fini()

    def test_late_marker_start_rolls_back_stale_native_cache(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        marker_type = runtime.vehicle_marker_plugin_type
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        arena_dp = types.SimpleNamespace(
            player_vehicle_id=91,
            getPlayerVehicleID=lambda force=False: arena_dp.player_vehicle_id,
            isRequiredDataExists=lambda: True,
            getVehicleInfo=lambda vehicle_id: types.SimpleNamespace(
                vehicleID=int(vehicle_id), team=1))
        self.assertTrue(
            compatibility.synchronise_vehicle_marker_identity(91))
        original_start = compatibility._original_vehicle_marker_start

        def stale_native_start(plugin):
            result = original_start(plugin)
            plugin._VehicleMarkerPlugin__playerVehicleID = 0
            return result

        compatibility._original_vehicle_marker_start = stale_native_start
        plugin = marker_type(arena_dp)
        provider = plugin.sessionProvider
        plugin._markers[1] = object()

        with self.assertRaisesRegex(
                RuntimeError, 'captured a stale player identity'):
            plugin.start()

        self.assertEqual({}, compatibility._vehicle_marker_plugins)
        self.assertNotIn(plugin, provider.arena_controllers)
        self.assertEqual({}, plugin._markers)
        self.assertEqual(
            [('vehicle_marker_start',), ('vehicle_marker_stop',)],
            [item for item in operations
             if item[0].startswith('vehicle_marker_')])
        compatibility.fini()

    def test_partial_native_marker_start_rolls_back_and_preserves_error(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        marker_type = runtime.vehicle_marker_plugin_type
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        arena_dp = types.SimpleNamespace(
            player_vehicle_id=91,
            getPlayerVehicleID=lambda force=False: arena_dp.player_vehicle_id,
            isRequiredDataExists=lambda: True,
            getVehicleInfo=lambda vehicle_id: types.SimpleNamespace(
                vehicleID=int(vehicle_id), team=1))
        self.assertTrue(
            compatibility.synchronise_vehicle_marker_identity(91))
        plugin = marker_type(arena_dp)
        provider = plugin.sessionProvider

        def partial_native_start(target):
            target.sessionProvider.addArenaCtrl(target)
            target._markers[1] = object()
            raise ValueError('native marker start failed')

        compatibility._original_vehicle_marker_start = partial_native_start

        with self.assertRaisesRegex(ValueError, 'native marker start failed'):
            plugin.start()

        self.assertNotIn(plugin, provider.arena_controllers)
        self.assertEqual({}, plugin._markers)
        self.assertEqual({}, compatibility._vehicle_marker_plugins)
        self.assertEqual(
            [('vehicle_marker_stop',)],
            [item for item in operations
             if item[0].startswith('vehicle_marker_')])
        compatibility.fini()

    def test_late_marker_start_refreshes_arena_before_native_start(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        marker_type = runtime.vehicle_marker_plugin_type
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        refreshes = []
        arena_dp = types.SimpleNamespace(player_vehicle_id=0)

        def required():
            refreshes.append(arena_dp.player_vehicle_id)
            arena_dp.player_vehicle_id = 91
            return True

        arena_dp.isRequiredDataExists = required
        arena_dp.getPlayerVehicleID = (
            lambda force=False: arena_dp.player_vehicle_id)
        arena_dp.getVehicleInfo = (
            lambda vehicle_id: types.SimpleNamespace(
                vehicleID=int(vehicle_id), team=1))
        self.assertTrue(
            compatibility.synchronise_vehicle_marker_identity(91))

        plugin = marker_type(arena_dp)
        plugin.start()

        self.assertEqual([0], refreshes)
        self.assertEqual(
            91, plugin._VehicleMarkerPlugin__playerVehicleID)
        self.assertEqual('from-player', plugin.getVehicleDamageType(91))
        self.assertEqual(
            [plugin],
            list(compatibility._vehicle_marker_plugins.values()))
        self.assertEqual(
            [('vehicle_marker_start',)],
            [item for item in operations
             if item[0].startswith('vehicle_marker_')])
        plugin.stop()
        compatibility.fini()

    def test_late_marker_start_rejects_stale_arena_before_native_start(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        marker_type = runtime.vehicle_marker_plugin_type
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        self.assertTrue(
            compatibility.synchronise_vehicle_marker_identity(91))
        arena_dp = types.SimpleNamespace(
            player_vehicle_id=0,
            getPlayerVehicleID=lambda force=False: arena_dp.player_vehicle_id,
            isRequiredDataExists=lambda: False,
            getVehicleInfo=lambda vehicle_id: types.SimpleNamespace(
                vehicleID=int(vehicle_id), team=1))
        plugin = marker_type(arena_dp)

        with self.assertRaisesRegex(
                RuntimeError, 'ArenaDP identity is incomplete before start'):
            plugin.start()

        self.assertEqual({}, compatibility._vehicle_marker_plugins)
        self.assertEqual(
            [], [item for item in operations
                 if item[0].startswith('vehicle_marker_')])
        compatibility.fini()

    def test_marker_damage_boundary_rejects_no_active_plugin(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        arena_dp = types.SimpleNamespace(
            getVehicleInfo=lambda vehicle_id: types.SimpleNamespace(
                vehicleID=int(vehicle_id), team=1))
        avatar = types.SimpleNamespace(
            guiSessionProvider=types.SimpleNamespace(
                getArenaDP=lambda: arena_dp))
        self.assertTrue(
            compatibility.synchronise_vehicle_marker_identity(91))

        with self.assertRaisesRegex(
                RuntimeError, 'active vehicle-marker plugin'):
            compatibility.assert_vehicle_marker_damage_type(avatar, 91)

        compatibility.fini()

    def test_respawnable_marker_super_lifecycle_registers_real_instance(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        arena_dp = types.SimpleNamespace(
            player_vehicle_id=91,
            getPlayerVehicleID=lambda force=False: arena_dp.player_vehicle_id,
            isRequiredDataExists=lambda: True,
            getVehicleInfo=lambda vehicle_id: types.SimpleNamespace(
                vehicleID=int(vehicle_id), team=1))

        class RespawnableVehicleMarkerPlugin(
                runtime.vehicle_marker_plugin_type):
            def start(self):
                return super(RespawnableVehicleMarkerPlugin, self).start()

            def stop(self):
                return super(RespawnableVehicleMarkerPlugin, self).stop()

        plugin = RespawnableVehicleMarkerPlugin(arena_dp)
        plugin.start()
        self.assertEqual(
            [plugin],
            list(compatibility._vehicle_marker_plugins.values()))
        self.assertTrue(
            compatibility.synchronise_vehicle_marker_identity(91))
        avatar = types.SimpleNamespace(
            guiSessionProvider=plugin.sessionProvider)
        self.assertTrue(compatibility.assert_vehicle_marker_damage_type(
            avatar, 91))

        plugin.stop()
        self.assertEqual({}, compatibility._vehicle_marker_plugins)
        compatibility.fini()

    def test_marker_identity_sync_rejects_a_missing_native_cache(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        arena_dp = types.SimpleNamespace(getPlayerVehicleID=lambda: 0)
        plugin = runtime.vehicle_marker_plugin_type(arena_dp)
        plugin.start()
        del plugin._VehicleMarkerPlugin__playerVehicleID

        with self.assertRaisesRegex(RuntimeError, 'cache is missing'):
            compatibility.synchronise_vehicle_marker_identity(91)

        self.assertFalse(hasattr(
            plugin, '_VehicleMarkerPlugin__playerVehicleID'))
        plugin.stop()
        compatibility.fini()

    def test_live_pose_owns_minimap_and_pretransition_aiming_sources(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        avatar = runtime.avatar_module.PlayerAvatar()
        avatar.playerVehicleID = 91
        avatar.inputHandler = \
            runtime.avatar_input_handler.AvatarInputHandler()
        vehicle = runtime.vehicle_module.Vehicle()
        vehicle.matrix = 'native-matrix'
        vehicle.filter = types.SimpleNamespace(
            bodyMatrix='native-body-matrix',
            stabilisedMatrix='native-stabilised-matrix')
        runtime.bigworld.entities[91] = vehicle
        runtime.bigworld.entity = runtime.bigworld.entities.get
        runtime.bigworld._player = avatar

        live_matrix = object()
        steady_rotation_matrix = object()
        stabilised_matrix = object()
        compatibility.set_vehicle_pose_overlay(
            vehicle, 'live-position', 0.75, live_matrix, 8.0, 0.2,
            steady_rotation_matrix=steady_rotation_matrix,
            stabilised_matrix=stabilised_matrix)
        compatibility.bind_vehicle_pose_sources(avatar, vehicle)

        own = avatar.consistentMatrices.\
            _ConsistentMatrices__ownVehicleMProv
        attached = avatar.consistentMatrices.\
            _ConsistentMatrices__attachedVehicleMatrix
        calculator = avatar.inputHandler.steadyVehicleMatrixCalculator
        output = calculator.\
            _SteadyVehicleMatrixCalculator__outputMProv
        steady = calculator.\
            _SteadyVehicleMatrixCalculator__stabilisedMProv
        self.assertIs(live_matrix, own.target)
        self.assertIs(live_matrix, attached.target)
        self.assertIs(steady_rotation_matrix, output.rotationSrc)
        self.assertIs(stabilised_matrix, output.translationSrc)
        self.assertIs(stabilised_matrix, steady.target)
        self.assertIs(
            stabilised_matrix,
            avatar._PlayerAvatar__ownVehicleStabMProv.target)

        avatar.inputHandler.onControlModeChanged('sniper', source='wheel')
        self.assertIs(steady_rotation_matrix, output.rotationSrc)
        self.assertIs(stabilised_matrix, output.translationSrc)
        self.assertIs(stabilised_matrix, steady.target)
        self.assertIn(
            ('control_mode', 'sniper', {'source': 'wheel'}), operations)

        self.assertTrue(compatibility.clear_vehicle_pose_overlay(vehicle))
        compatibility.restore_vehicle_pose_sources(
            avatar, vehicle, 'native-matrix', 'native-stabilised-matrix')
        self.assertEqual('native-body-matrix', own.target)
        self.assertEqual('native-matrix', attached.target)
        self.assertEqual(
            'native-stabilised-matrix',
            avatar._PlayerAvatar__ownVehicleStabMProv.target)
        self.assertEqual('native-stabilised-matrix', output.rotationSrc)
        self.assertEqual('native-stabilised-matrix', output.translationSrc)
        self.assertEqual('native-stabilised-matrix', steady.target)
        compatibility.fini()

    def _runtime(self, existing_hosts=None, host_failure=False):
        operations = []
        statuses = types.SimpleNamespace(NOT_SET=0, LOGGED_ON=1)
        bigworld = _CompatBigWorld(operations)
        chat_manager = _CompatChatManager(operations)

        class PlayerAccount(object):
            def __init__(self):
                operations.append(('original_account_init',))
                required_name, required_value = account_module._CLIENT_SERVER_VERSION
                if getattr(self, required_name) != required_value:
                    raise AssertionError('client/server version was not injected')
                if self.name != 'offline_account':
                    raise AssertionError('offline account name was not injected')
                if 'file_server' not in self.initialServerSettings:
                    raise AssertionError('file server settings were not injected')
                regional = self.initialServerSettings['regional_settings']
                if 'starting_time_of_a_new_game_day' not in regional:
                    raise AssertionError('game-day start was not injected')
                if 'voipDomain' in self.initialServerSettings:
                    raise AssertionError('offline settings must not enable VOIP')
                ranked = self.initialServerSettings['ranked_config']
                if ranked.get('isEnabled') is not False:
                    raise AssertionError('ranked battles must be disabled')
                self._ClientChat__chatActionCallbacks = {}
                self._PlayerAccount__onCmdResponse = {}
                self._PlayerAccount__onStreamComplete = {}
                self.isLongDisconnectedFromCenter = False
                self._idGen = object()

            def onBecomePlayer(self):
                operations.append(('original_account_become_player',))
                bigworld.clearAllSpaces()
                target = bigworld.target
                target.source = bigworld.MouseTargetingMatrix()
                target.maxDistance = 700.0
                target.skeletonCheckEnabled = True
                target.caps()
                target.isEnabled = True
                chat_manager.switchPlayerProxy(self)

            def onBecomeNonPlayer(self):
                operations.append(('original_account_become_non_player',))
                chat_manager.switchPlayerProxy(None)

            def showGUI(self, context):
                operations.append(('show_gui', context))

        class AvatarObserver(object):
            @staticmethod
            def onEnterWorld(avatar):
                required = (
                    'syncVector3', 'getVector3', 'resetVector3',
                    'setInterpolationType')
                if not all(hasattr(avatar.filter, name)
                           for name in required):
                    raise AssertionError('native AvatarFilter is incomplete')
                operations.append(('avatar_observer_enter_world',
                                   avatar.filter))

        class MatrixProvider(object):
            def __init__(self):
                self.target = None
                self.rotationSrc = None
                self.translationSrc = None

        class ConsistentMatrices(object):
            def __init__(self):
                self._ConsistentMatrices__attachedVehicleMatrix = \
                    MatrixProvider()
                self._ConsistentMatrices__ownVehicleMProv = MatrixProvider()

            def __setTarget(self, matrix, as_static=True):
                unused_as_static = as_static
                self._ConsistentMatrices__attachedVehicleMatrix.target = \
                    matrix

            def __linkOwnVehicle(self, vehicle):
                vehicle_filter = getattr(vehicle, 'filter', None)
                self._ConsistentMatrices__ownVehicleMProv.target = getattr(
                    vehicle_filter, 'bodyMatrix', vehicle.matrix)

        class SteadyVehicleMatrixCalculator(object):
            def __init__(self):
                self._SteadyVehicleMatrixCalculator__outputMProv = \
                    MatrixProvider()
                self._SteadyVehicleMatrixCalculator__stabilisedMProv = \
                    MatrixProvider()

            def relinkSources(self):
                player = bigworld.player()
                vehicle = (player.getVehicleAttached()
                           if player is not None else None)
                matrix = getattr(
                    getattr(vehicle, 'filter', None),
                    'stabilisedMatrix', 'native-steady-matrix')
                output = self.\
                    _SteadyVehicleMatrixCalculator__outputMProv
                stabilised = self.\
                    _SteadyVehicleMatrixCalculator__stabilisedMProv
                output.rotationSrc = matrix
                output.translationSrc = matrix
                stabilised.target = matrix

        class PlayerAvatar(object):
            def __setattr__(self, name, value):
                if (name in ('name', 'clientCtx') and
                        not isinstance(value, bytes)):
                    raise NameError(
                        'Attempted to set attribute %s on Avatar to an '
                        'invalid value.' % name)
                if name == 'remoteCamera':
                    if (not isinstance(value, dict) or
                            set(value) != {'time', 'shotPoint', 'zoom'} or
                            not isinstance(value['time'], float) or
                            not isinstance(value['shotPoint'], _Vector3) or
                            not isinstance(value['zoom'], int) or
                            not 0 <= value['zoom'] <= 255):
                        raise TypeError('invalid REMOTE_CAMERA_DATA')
                    value = types.SimpleNamespace(**value)
                object.__setattr__(self, name, value)

            def __init__(self):
                operations.append(('original_avatar_init',))
                self._ClientChat__chatActionCallbacks = {}
                self._PlayerAvatar__initProgress = 0
                self._PlayerAvatar__autoAimVehID = 0
                self._PlayerAvatar__aimingInfo = [0.0, 1.0]
                self.consistentMatrices = ConsistentMatrices()
                self._PlayerAvatar__consistentMatrices = \
                    self.consistentMatrices
                self._PlayerAvatar__ownVehicleStabMProv = MatrixProvider()
                self.arena = types.SimpleNamespace(
                    onPeriodChange=_CompatSubscriptionEvent(
                        operations, 'arena_period'))

            def onEnterWorld(self, prereqs):
                unused = self._PlayerAvatar__initProgress
                operations.append(('original_avatar_enter_world', prereqs))

            def onLeaveWorld(self):
                unused = self._PlayerAvatar__consistentMatrices
                operations.append(('original_avatar_leave_world',))

            def onBecomePlayer(self):
                operations.append(('original_avatar_become_player',))
                chat_manager.switchPlayerProxy(self)
                self.filter = bigworld.AvatarFilter()
                self.arena = types.SimpleNamespace(arenaType=object())
                bigworld.target.caps(1)

            def onBecomeNonPlayer(self):
                operations.append(('original_avatar_become_non_player',))
                bigworld.target.clear()
                chat_manager.switchPlayerProxy(None)

            def onPrereqsLoaded(self, resource_names, resource_refs):
                operations.append(
                    ('avatar_prereqs_loaded', resource_names, resource_refs))

            def __onSetOwnVehicleAuxPhysicsData(self, previous):
                operations.append(('avatar_aux_physics_before', previous))
                self.aux_nested_filter = self._readAuxVehicleFilter()
                self.aux_vehicle.filter.syncStabilisedYPR(0.1, 0.2, 0.3)
                if getattr(self, 'fail_aux_physics', False):
                    raise RuntimeError('aux physics update failed')
                operations.append(('avatar_aux_physics_after',))

            def _readAuxVehicleFilter(self):
                return self.aux_vehicle.filter

            def vehicle_onEnterWorld(self, vehicle):
                operations.append(('original_avatar_vehicle_enter',
                                   vehicle.id))

            def vehicle_onLeaveWorld(self, vehicle):
                operations.append(('original_avatar_vehicle_leave',
                                   vehicle.id))

            def getOwnVehicleSpeeds(self, get_instantaneous=False):
                return (0.0, 0.0)

            def autoAim(self, target):
                operations.append(('original_avatar_auto_aim', target))
                if target is None or not isinstance(target, Vehicle):
                    vehicle_id = 0
                elif target.id == self._PlayerAvatar__autoAimVehID:
                    vehicle_id = 0
                elif target.publicInfo['team'] == self.team:
                    vehicle_id = 0
                elif not target.isAlive():
                    vehicle_id = 0
                else:
                    vehicle_id = target.id
                if self._PlayerAvatar__autoAimVehID == vehicle_id:
                    return None
                self._PlayerAvatar__autoAimVehID = vehicle_id
                self.cell.autoAim(vehicle_id)
                aiming_mode = 'target-lock'
                if vehicle_id:
                    self.inputHandler.setAimingMode(True, aiming_mode)
                    self.gunRotator.clientMode = False
                else:
                    self.inputHandler.setAimingMode(False, aiming_mode)
                    self.gunRotator.clientMode = True
                    self._PlayerAvatar__aimingInfo[0] = bigworld.time()
                    minimum = self.vehicleTypeDescriptor.gun.\
                        shotDispersionAngle
                    self._PlayerAvatar__aimingInfo[1] = (
                        self.gunRotator.dispersionAngle / minimum)
                return None

            def getVehicleAttached(self):
                return bigworld.entity(getattr(self, 'playerVehicleID', 0))

        class Vehicle(object):
            def __setattr__(self, name, value):
                if (name in ('health', 'isCrewActive') and
                        getattr(self, 'reject_server_properties', False)):
                    raise RuntimeError('Operation is not allowed')
                object.__setattr__(self, name, value)

            def __stopExtras(self):
                operations.append(('vehicle_stop_extras',))

            def stopVisual(self, show_stipple=False):
                operations.append(('vehicle_stop_visual', show_stipple))
                self.isStarted = False

            def onLeaveWorld(self):
                self._Vehicle__stopExtras()
                bigworld.player().vehicle_onLeaveWorld(self)
                if self.isStarted:
                    raise AssertionError('Vehicle remained started')

            def __startWGPhysics(self):
                operations.extend((
                    ('vehicle_physics_init',),
                    ('vehicle_physics_bounds',),
                    ('vehicle_physics_owner',),
                    ('vehicle_physics_static_mode',),
                    ('vehicle_physics_movement_signals',),
                ))
                self.nested_start_filter = self._readFilterFromHelper()
                vehicle_filter = self.filter
                vehicle_filter.setVehiclePhysics(self.physics)
                operations.append(('vehicle_physics_visibility',))
                vehicle_filter.syncGunAngles(0.25, -0.5)
                self.speed_info = vehicle_filter.speedInfo
                operations.append(('vehicle_physics_speed', self.speed_info))

            def set_gunAnglesPacked(self, previous):
                operations.append(('vehicle_gun_angles_before', previous))
                self.nested_gun_filter = self._readFilterFromHelper()
                self.filter.syncGunAngles(0.75, -0.25)
                if getattr(self, 'fail_gun_angles', False):
                    raise RuntimeError('gun angle update failed')
                operations.append(('vehicle_gun_angles_after',))

            def _readFilterFromHelper(self):
                return self.filter

            def getSpeed(self):
                return getattr(self, 'native_speed', 0.0)

            def __collideSegment(self, start_point, end_point,
                                 skip_gun=False, only_nearest=True):
                unused_only_nearest = only_nearest
                if not self.filter.segmentMayHitEntity(
                        start_point, end_point, skip_gun):
                    return None
                return ('visible-hit',)

            def collideSegment(self, start_point, end_point, skip_gun=False,
                               optimized=True):
                self.native_collide_args = (skip_gun, optimized)
                return self.__collideSegment(
                    start_point, end_point, skip_gun, True)

            def collideSegmentExt(self, start_point, end_point):
                return self.__collideSegment(
                    start_point, end_point, False, False)

        class CompoundAppearance(object):
            def __init__(self, vehicle_filter):
                self._CompoundAppearance__filter = vehicle_filter
                self._arena_callback = lambda *unused: None
                self._camera_callback = lambda *unused: None

            def __onModelsRefresh(self, model_state, resource_list):
                operations.append(
                    ('compound_refresh_before', model_state, resource_list))
                replacement = getattr(self, 'replacement_filter', None)
                if replacement is not None:
                    self._CompoundAppearance__filter = replacement
                self.nested_filter = self._readFilterDuringRefresh()
                self._CompoundAppearance__filter.syncGunAngles(0.5, -0.1)
                if getattr(self, 'fail_models_refresh', False):
                    raise RuntimeError('models refresh failed')
                operations.append(('compound_refresh_after',))

            def _readFilterDuringRefresh(self):
                return self._CompoundAppearance__filter

            def deactivate(self, stopEffects=True):
                operations.append(('compound_deactivate', stopEffects))
                bigworld.player().inputHandler.removeVehicleFromCameraCollider(
                    self)
                bigworld.player().arena.onPeriodChange -= self._arena_callback
                bigworld.player().inputHandler.onCameraChanged -= \
                    self._camera_callback
                if getattr(self, 'fail_deactivate', False):
                    raise RuntimeError('compound deactivate failed')

        class CrashedTrackController(object):
            def __init__(self, entity=None):
                self.entity = entity

            def __setupTrackAssembler(self, entity):
                return entity.filter.groundPlacingMatrix

            def __onModelLoaded(self, unused_resources):
                return self.entity.filter.groundPlacingMatrix

        class AvatarInputHandler(object):
            def __init__(self):
                self._AvatarInputHandler__ctrlModeName = None
                self.steadyVehicleMatrixCalculator = \
                    SteadyVehicleMatrixCalculator()

            def onControlModeChanged(self, eMode, **args):
                self.steadyVehicleMatrixCalculator.relinkSources()
                operations.append(('control_mode', eMode, args))
                self._AvatarInputHandler__ctrlModeName = eMode
                return 'changed'

        class CommandMappingInstance(object):
            def __init__(self):
                self.overrides = {}

            def isFired(self, command, key):
                commands = self.overrides.get(key)
                if commands is not None:
                    return command in commands
                return command == key

        class CommandMapping(object):
            CMD_CM_FREE_CAMERA = 'free-camera'
            CMD_CM_LOCK_TARGET = 'lock-target'
            CMD_CM_LOCK_TARGET_OFF = 'lock-target-off'
            g_instance = CommandMappingInstance()

        class ArcadeControlMode(object):
            def handleKeyEvent(self, isDown, key, mods, event):
                if getattr(self, 'raise_event', False):
                    raise RuntimeError('control-mode input failed')
                if getattr(self, 'consume_event', False):
                    return True
                is_free = CommandMapping.g_instance.isFired(
                    CommandMapping.CMD_CM_FREE_CAMERA, key)
                is_lock = (isDown and CommandMapping.g_instance.isFired(
                    CommandMapping.CMD_CM_LOCK_TARGET, key))
                if is_free:
                    pass
                if is_lock:
                    bigworld.player().autoAim(bigworld.target())
                if (isDown and CommandMapping.g_instance.isFired(
                        CommandMapping.CMD_CM_LOCK_TARGET_OFF, key)):
                    bigworld.player().autoAim(None)
                    return True
                return False

        class SniperControlMode(ArcadeControlMode):
            def handleKeyEvent(self, isDown, key, mods, event):
                if getattr(self, 'raise_event', False):
                    raise RuntimeError('control-mode input failed')
                if getattr(self, 'consume_event', False):
                    return True
                is_free = CommandMapping.g_instance.isFired(
                    CommandMapping.CMD_CM_FREE_CAMERA, key)
                is_lock = (isDown and CommandMapping.g_instance.isFired(
                    CommandMapping.CMD_CM_LOCK_TARGET, key))
                if is_free:
                    pass
                if is_lock:
                    bigworld.player().autoAim(bigworld.target())
                if (isDown and CommandMapping.g_instance.isFired(
                        CommandMapping.CMD_CM_LOCK_TARGET_OFF, key)):
                    bigworld.player().autoAim(None)
                    return True
                return False

        class AccelerationSmoother(object):
            def update(self, vehicle, delta_time):
                unused_delta_time = delta_time
                return (vehicle.filter.velocity,
                        vehicle.filter.acceleration)

        class ArcadeCamera(object):
            def __init__(self):
                self._ArcadeCamera__accelerationSmoother = \
                    AccelerationSmoother()

            def __calcCurOscillatorAcceleration(self, delta_time):
                vehicle = bigworld.player().getVehicleAttached()
                velocity = vehicle.filter.velocity
                motion = self._ArcadeCamera__accelerationSmoother.update(
                    vehicle, delta_time)
                return velocity, motion

        class SniperCamera(object):
            def __init__(self):
                self._SniperCamera__accelerationSmoother = \
                    AccelerationSmoother()

            def __calcCurOscillatorAcceleration(self, delta_time):
                vehicle = bigworld.player().vehicle
                if vehicle is None:
                    return None
                velocity = vehicle.filter.velocity
                motion = self._SniperCamera__accelerationSmoother.update(
                    vehicle, delta_time)
                return velocity, motion

        class StrategicCamera(object):
            ticks = 0
            failures = 0

            def _StrategicCamera__cameraUpdate(self):
                StrategicCamera.ticks += 1
                if StrategicCamera.failures:
                    StrategicCamera.failures -= 1
                    raise RuntimeError('strategic aiming system is missing')
                return 0.0

        class VehicleGunRotator(object):
            def getAvatarOwnVehicleStabilisedMatrix(self, vehicle):
                return vehicle.filter.interpolateStabilisedMatrix(123.0)

            def predictLockedTargetShotPoint(self):
                target = bigworld.player().autoAimVehicle
                return target.position, target.matrix

        def segment_may_hit_entity(entity, start_point, end_point):
            return entity.filter.segmentMayHitEntity(
                start_point, end_point, 1)

        def visible_vehicle_collision(
                vehicle, matrix, start_point, end_point, math_module):
            vehicle.visible_collision_calls.append(
                (matrix, start_point, end_point, math_module))
            return list(vehicle.visible_collisions)

        remote_vehicle_module = __import__(
            'gui.mods.offline_lan_0922.entities.remote_vehicle',
            fromlist=['_RemoteFilter'])

        marker_damage_types = types.SimpleNamespace(
            FROM_UNKNOWN='from-unknown', FROM_PLAYER='from-player',
            FROM_SQUAD='from-squad', FROM_ALLY='from-ally',
            FROM_ENEMY='from-enemy')
        marker_attack_reasons = ('shot', 'fire', 'ramming')

        class VehicleMarkerPlugin(object):
            def __init__(self, arena_dp):
                self.arena_dp = arena_dp
                self._markers = {}
                self.marker_updates = []
                feedback = types.SimpleNamespace()
                setattr(
                    feedback, '_BattleFeedbackAdaptor__arenaDP',
                    self.arena_dp)
                self.sessionProvider = types.SimpleNamespace(
                    getArenaDP=lambda: self.arena_dp,
                    setVehicleHealth=lambda *unused: None,
                    shared=types.SimpleNamespace(feedback=feedback),
                    arena_controllers=weakref.WeakSet())
                self.sessionProvider.addArenaCtrl = lambda controller: \
                    self.sessionProvider.arena_controllers.add(controller)
                self.sessionProvider.removeArenaCtrl = lambda controller: \
                    self.sessionProvider.arena_controllers.discard(controller)
                self._VehicleMarkerPlugin__playerVehicleID = 0

            def start(self):
                self._VehicleMarkerPlugin__playerVehicleID = \
                    self.arena_dp.getPlayerVehicleID()
                self.sessionProvider.addArenaCtrl(self)
                operations.append(('vehicle_marker_start',))

            def stop(self):
                self._markers.clear()
                operations.append(('vehicle_marker_stop',))

            def __getVehicleDamageType(self, attacker_info):
                if (attacker_info.vehicleID ==
                        self._VehicleMarkerPlugin__playerVehicleID):
                    return marker_damage_types.FROM_PLAYER
                return marker_damage_types.FROM_ALLY

            def getVehicleDamageType(self, attacker_id):
                return self.__getVehicleDamageType(types.SimpleNamespace(
                    vehicleID=int(attacker_id), team=1))

            def __updateVehicleHealth(self, handle, new_health,
                                      attacker_info, reason_id):
                self.marker_updates.append((
                    handle, 'updateHealth', new_health,
                    self.__getVehicleDamageType(attacker_info),
                    marker_attack_reasons[reason_id]))

            def __onVehicleFeedbackReceived(self, event_id, vehicle_id,
                                            value):
                if event_id == 'vehicle-health':
                    self.__updateVehicleHealth(vehicle_id, *value)

        account_module = types.SimpleNamespace(
            PlayerAccount=PlayerAccount,
            _CLIENT_SERVER_VERSION=('requiredVersion_92200', '0.9.22'))
        trigger_manager = types.SimpleNamespace(
            activateTrigger=lambda trigger, **kwargs: operations.append(
                ('trigger_activate', trigger, kwargs)),
            deactivateTrigger=lambda trigger: operations.append(
                ('trigger_deactivate', trigger)))
        avatar_module = types.SimpleNamespace(
            PlayerAvatar=PlayerAvatar, AvatarObserver=AvatarObserver,
            AimSound=types.SimpleNamespace(
                TARGET_LOCKED='target_locked',
                TARGET_UNLOCKED='target_unlocked'),
            TriggersManager=types.SimpleNamespace(g_manager=trigger_manager),
            TRIGGER_TYPE=types.SimpleNamespace(
                AUTO_AIM_AT_VEHICLE='auto_aim_at_vehicle'))
        bigworld.account_type = PlayerAccount
        manager = _CompatConnectionManager(bigworld, statuses, operations)
        player_events = types.SimpleNamespace(
            onDisconnected=_CompatEvent(operations, 'player_disconnected'))
        sound_groups = _SoundGroups(bigworld, operations)
        runtime = types.SimpleNamespace(
            account_module=account_module,
            acceleration_smoother_type=AccelerationSmoother,
            arcade_camera_type=ArcadeCamera,
            avatar_module=avatar_module,
            avatar_input_handler=types.SimpleNamespace(
                AvatarInputHandler=AvatarInputHandler),
            control_modes=types.SimpleNamespace(
                ArcadeControlMode=ArcadeControlMode,
                SniperControlMode=SniperControlMode,
                CommandMapping=CommandMapping),
            avatar_position_control=types.SimpleNamespace(
                ConsistentMatrices=ConsistentMatrices),
            bigworld=bigworld,
            chat_manager=chat_manager,
            compound_appearance_module=types.SimpleNamespace(
                CompoundAppearance=CompoundAppearance),
            crashed_tracks_controller_type=CrashedTrackController,
            connection_manager=manager,
            login_status=statuses,
            offline_map_creator=_OfflineMapCreator(operations),
            player_events=player_events,
            projectile_mover_module=types.SimpleNamespace(
                segmentMayHitEntity=segment_may_hit_entity),
            predefined_hosts=_Hosts(existing_hosts, host_failure),
            prb_loader=_PrbLoader(operations),
            remote_filter_type=remote_vehicle_module._RemoteFilter,
            segment_collision_result_type=(
                remote_vehicle_module._SegmentCollisionResult),
            sound_groups_module=types.SimpleNamespace(
                g_instance=sound_groups),
            sniper_camera_type=SniperCamera,
            strategic_camera_type=StrategicCamera,
            steady_vehicle_matrix=types.SimpleNamespace(
                SteadyVehicleMatrixCalculator=
                SteadyVehicleMatrixCalculator),
            constants=types.SimpleNamespace(
                AIMING_MODE=types.SimpleNamespace(TARGET_LOCK='target-lock')),
            math=types.SimpleNamespace(Vector3=_Vector3, Matrix=_Matrix),
            vehicle_module=types.SimpleNamespace(Vehicle=Vehicle),
            vehicle_marker_plugin_type=VehicleMarkerPlugin,
            vehicle_marker_damage_type=marker_damage_types,
            vehicle_gun_rotator=types.SimpleNamespace(
                VehicleGunRotator=VehicleGunRotator),
            visible_vehicle_collision=visible_vehicle_collision)
        return runtime, operations

    def test_connects_account_in_native_event_order_and_disconnects_once(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        original_init = runtime.account_module.PlayerAccount.__dict__['__init__']
        original_account_getattribute = \
            runtime.account_module.PlayerAccount.__getattribute__
        original_avatar_getattribute = \
            runtime.avatar_module.PlayerAvatar.__getattribute__
        original_vehicle_getattribute = \
            runtime.vehicle_module.Vehicle.__getattribute__
        original_vehicle_start_wg_physics = (
            runtime.vehicle_module.Vehicle.__dict__[
                '_Vehicle__startWGPhysics'])
        original_connect = runtime.bigworld.connect
        original_disconnect = runtime.bigworld.disconnect
        original_clear_all_spaces = runtime.bigworld.clearAllSpaces
        original_target = runtime.bigworld.target
        compatibility = compatibility_module.OfflineCompatibility(runtime)

        compatibility.connect(show_lobby=True)

        self.assertIs(original_target, runtime.bigworld.target)
        self.assertTrue(compatibility.is_ready())
        account = runtime.bigworld.player()
        self.assertTrue(account.isOffline)
        self.assertIs(account.fakeServer, account.base)
        names = [item[0] for item in operations]
        self.assertLess(names.index('progress'), names.index('account_entity'))
        self.assertLess(names.index('connected'), names.index('account_entity'))
        self.assertLess(names.index('account_entity'),
                        names.index('original_account_init'))
        self.assertLess(names.index('original_account_init'),
                        names.index('player'))
        self.assertLess(names.index('player'),
                        names.index('original_account_become_player'))
        self.assertNotIn('clear_all_spaces', names)
        self.assertIn(account, runtime.bigworld.entities.values())
        self.assertTrue(hasattr(account, '_ClientChat__chatActionCallbacks'))
        self.assertTrue(hasattr(account, '_PlayerAccount__onCmdResponse'))
        self.assertEqual(1, names.count('show_gui'))
        self.assertEqual(original_clear_all_spaces,
                         runtime.bigworld.clearAllSpaces)
        self.assertIs(account, account.fakeServer._player())
        runtime.bigworld._player = object()
        self.assertIsNone(account.fakeServer._player())
        runtime.bigworld._player = account
        self.assertFalse(compatibility._connecting)

        avatar = runtime.avatar_module.PlayerAvatar()
        self.assertFalse(avatar.isObserverFPV)
        self.assertEqual(0, avatar.observerFPVControlMode)
        self.assertEqual(0, avatar.numOfObservers)
        self.assertEqual(0.0, avatar.remoteCamera.time)
        self.assertEqual(
            (0.0, 0.0, 0.0),
            (avatar.remoteCamera.shotPoint.x,
             avatar.remoteCamera.shotPoint.y,
             avatar.remoteCamera.shotPoint.z))
        self.assertEqual(0, avatar.remoteCamera.zoom)
        first_filter = avatar.filter
        original_filter_factory = runtime.bigworld.AvatarFilter
        runtime.avatar_module.AvatarObserver.onEnterWorld(avatar)
        avatar.onBecomePlayer()
        self.assertIs(original_target, runtime.bigworld.target)
        self.assertIs(first_filter, avatar.filter)
        observer_events = [item for item in operations
                           if item[0] == 'avatar_observer_enter_world']
        self.assertEqual(1, len(observer_events))
        self.assertIs(avatar.filter, observer_events[-1][1])
        self.assertEqual(original_filter_factory,
                         runtime.bigworld.AvatarFilter)

        compatibility.disconnect()
        compatibility.disconnect()
        names = [item[0] for item in operations]
        self.assertEqual(1, names.count('manager_disconnect'))
        self.assertEqual(1, names.count('disconnected'))
        self.assertEqual(1, names.count('player_disconnected'))
        self.assertFalse(runtime.offline_map_creator.active)

        compatibility.fini()
        self.assertIs(original_target, runtime.bigworld.target)
        self.assertIs(
            original_init,
            runtime.account_module.PlayerAccount.__dict__['__init__'])
        self.assertIs(
            original_account_getattribute,
            runtime.account_module.PlayerAccount.__getattribute__)
        self.assertIs(
            original_avatar_getattribute,
            runtime.avatar_module.PlayerAvatar.__getattribute__)
        self.assertIs(
            original_vehicle_getattribute,
            runtime.vehicle_module.Vehicle.__getattribute__)
        self.assertIs(
            original_vehicle_start_wg_physics,
            runtime.vehicle_module.Vehicle.__dict__[
                '_Vehicle__startWGPhysics'])
        self.assertEqual(original_connect, runtime.bigworld.connect)
        self.assertEqual(original_disconnect, runtime.bigworld.disconnect)
        self.assertEqual([], runtime.predefined_hosts._hosts)

    def test_offline_vehicle_health_uses_python_overlay_not_server_property(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()
        vehicle = runtime.vehicle_module.Vehicle()
        vehicle.health = 500
        vehicle.isCrewActive = True
        vehicle.reject_server_properties = True

        compatibility.configure_battle()
        vehicle.health = 375
        vehicle.isCrewActive = False

        self.assertEqual(375, vehicle.health)
        self.assertFalse(vehicle.isCrewActive)
        compatibility.deactivate_map()
        self.assertEqual(500, vehicle.health)
        self.assertTrue(vehicle.isCrewActive)
        compatibility.fini()

    def test_postmortem_callback_sees_the_selected_observed_vehicle(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        avatar = runtime.avatar_module.PlayerAvatar()
        observed = runtime.vehicle_module.Vehicle()
        runtime.bigworld.entities[92] = observed
        runtime.bigworld.entity = runtime.bigworld.entities.get
        runtime.bigworld._player = avatar

        self.assertEqual(0, compatibility.set_postmortem_vehicle(92))
        self.assertIs(observed, avatar.vehicle)
        self.assertEqual(92, compatibility.set_postmortem_vehicle(0))
        with self.assertRaises(AttributeError):
            unused_vehicle = avatar.vehicle

        compatibility.fini()

    def test_postmortem_attachment_can_clear_before_battle_activation(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility._postmortem_vehicle_id = 92

        self.assertEqual(92, compatibility.clear_postmortem_vehicle())
        self.assertEqual(0, compatibility._postmortem_vehicle_id)

    def test_offline_vehicle_pose_overlay_preserves_native_entity_transform(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()
        vehicle = runtime.vehicle_module.Vehicle()
        vehicle.position = 'native-position'
        vehicle.yaw = 0.25
        vehicle.matrix = 'native-matrix'
        vehicle.native_speed = 2.0

        compatibility.configure_battle()
        compatibility.set_vehicle_pose_overlay(
            vehicle, 'copied-position', 1.5, 'copied-matrix', 7.5, 0.25)

        self.assertEqual('copied-position', vehicle.position)
        self.assertEqual(1.5, vehicle.yaw)
        self.assertEqual('copied-matrix', vehicle.matrix)
        self.assertEqual(7.5, vehicle.getSpeed())
        self.assertEqual(
            'native-position',
            compatibility.native_vehicle_attribute(vehicle, 'position'))
        self.assertEqual(
            'native-matrix',
            compatibility.native_vehicle_attribute(vehicle, 'matrix'))

        vehicle.position = 'next-copied-position'
        self.assertEqual('next-copied-position', vehicle.position)
        self.assertEqual(
            'native-position',
            compatibility.native_vehicle_attribute(vehicle, 'position'))
        self.assertTrue(compatibility.clear_vehicle_pose_overlay(vehicle))
        self.assertEqual('native-position', vehicle.position)
        self.assertEqual('native-matrix', vehicle.matrix)
        self.assertEqual(2.0, vehicle.getSpeed())
        compatibility.fini()

    def test_native_remote_aim_collides_at_the_one_visible_pose(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        vehicle_type = runtime.vehicle_module.Vehicle
        original_collide = vehicle_type.__dict__['collideSegment']
        original_collide_ext = vehicle_type.__dict__['collideSegmentExt']
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()

        vehicle = vehicle_type()
        vehicle._offlineNativeRemote = True
        native_filter = mock.Mock()
        native_filter.segmentMayHitEntity.return_value = False
        vehicle.filter = native_filter
        vehicle.visible_collision_calls = []
        visible_matrix = _Matrix()
        visible_position = _Vector3(100.0, 2.0, 3.0)
        visible_matrix.translation = visible_position
        raw_position = _Vector3(0.0, 0.0, 0.0)
        vehicle.position = raw_position
        vehicle.matrix = _Matrix()
        result_ext_type = __import__(
            'gui.mods.offline_lan_0922.entities.remote_vehicle',
            fromlist=['_SegmentCollisionResultExt']
        )._SegmentCollisionResultExt
        gun_material = types.SimpleNamespace(armor=25.0)
        hull_material = types.SimpleNamespace(armor=75.0)
        vehicle.visible_collisions = [
            result_ext_type(0.8, 0.7, gun_material, 'vehicleGun'),
            result_ext_type(0.4, 0.9, hull_material, 'vehicleHull')]
        start = _Vector3(90.0, 2.0, 3.0)
        end = _Vector3(110.0, 2.0, 3.0)

        compatibility.configure_battle()
        compatibility.set_vehicle_pose_overlay(
            vehicle, raw_position, 0.0, visible_matrix, 0.0, 0.0,
            _Vector3(0.0, 0.0, 0.0))

        self.assertTrue(
            runtime.projectile_mover_module.segmentMayHitEntity(
                vehicle, start, end))
        native_filter.segmentMayHitEntity.assert_not_called()
        nearest = vehicle.collideSegment(
            start, end, skipGun=True, optimized=False)
        self.assertEqual((0.4, 0.9, 75.0), tuple(nearest))
        extended = vehicle.collideSegmentExt(start, end)
        self.assertEqual([0.4, 0.8], [item.dist for item in extended])
        self.assertTrue(vehicle.visible_collision_calls)
        self.assertTrue(all(
            call[0] is visible_matrix
            for call in vehicle.visible_collision_calls))

        avatar = runtime.avatar_module.PlayerAvatar()
        avatar.autoAimVehicle = vehicle
        runtime.bigworld._player = avatar
        rotator = runtime.vehicle_gun_rotator.VehicleGunRotator()
        predicted_position, predicted_matrix = \
            rotator.predictLockedTargetShotPoint()
        self.assertIs(visible_position, predicted_position)
        self.assertIs(visible_matrix, predicted_matrix)
        self.assertIs(raw_position, vehicle.position)

        vehicle._offlineNativeRemote = False
        self.assertIsNone(vehicle.collideSegment(
            start, end, skipGun=True, optimized=False))
        self.assertEqual((True, False), vehicle.native_collide_args)
        vehicle._offlineNativeRemote = True
        vehicle.visible_collisions = []
        self.assertIsNone(vehicle.collideSegment(start, end))
        self.assertIsNone(vehicle.collideSegmentExt(start, end))

        compatibility.fini()
        self.assertIs(
            original_collide, vehicle_type.__dict__['collideSegment'])
        self.assertIs(
            original_collide_ext, vehicle_type.__dict__['collideSegmentExt'])

    def test_offline_avatar_speed_boundary_uses_copied_pose_overlay(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()
        avatar = runtime.avatar_module.PlayerAvatar()
        avatar.playerVehicleID = 91
        vehicle = runtime.vehicle_module.Vehicle()
        runtime.bigworld.entities[91] = vehicle
        runtime.bigworld.entity = runtime.bigworld.entities.get

        compatibility.configure_battle()
        compatibility.set_vehicle_pose_overlay(
            vehicle, 'position', 0.0, 'matrix', 8.25, -0.4)

        self.assertEqual((8.25, -0.4), avatar.getOwnVehicleSpeeds())
        self.assertEqual(
            (8.25, -0.4), avatar.getOwnVehicleSpeeds(True))

        compatibility.fini()
        self.assertEqual((0.0, 0.0), avatar.getOwnVehicleSpeeds())

    def test_dynamic_camera_reads_copied_motion_without_replacing_filter(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()
        avatar = runtime.avatar_module.PlayerAvatar()
        avatar.playerVehicleID = 91
        avatar.vehicle = None
        vehicle = runtime.vehicle_module.Vehicle()
        native_filter = types.SimpleNamespace(
            velocity='native-velocity',
            acceleration='native-acceleration')
        vehicle.filter = native_filter
        runtime.bigworld.entities[91] = vehicle
        runtime.bigworld.entity = runtime.bigworld.entities.get
        runtime.bigworld._player = avatar
        smoother = runtime.acceleration_smoother_type()
        arcade = runtime.arcade_camera_type()
        sniper = runtime.sniper_camera_type()

        self.assertEqual(
            ('native-velocity', 'native-acceleration'),
            smoother.update(vehicle, 0.016))
        compatibility.configure_battle()
        compatibility.set_vehicle_pose_overlay(
            vehicle, 'position', 0.0, 'matrix', 8.0, 0.0,
            'copied-velocity', 'copied-acceleration')

        expected = ('copied-velocity', 'copied-acceleration')
        self.assertEqual(expected, smoother.update(vehicle, 0.016))
        self.assertEqual(
            ('copied-velocity', expected),
            arcade._ArcadeCamera__calcCurOscillatorAcceleration(0.016))
        self.assertEqual(
            ('copied-velocity', expected),
            sniper._SniperCamera__calcCurOscillatorAcceleration(0.016))
        self.assertIsNone(avatar.vehicle)
        self.assertIs(native_filter, vehicle.filter)
        self.assertIs(
            native_filter,
            compatibility.native_vehicle_attribute(vehicle, 'filter'))

        self.assertTrue(compatibility.clear_vehicle_pose_overlay(vehicle))
        self.assertEqual(
            ('native-velocity', 'native-acceleration'),
            smoother.update(vehicle, 0.016))
        self.assertIsNone(
            sniper._SniperCamera__calcCurOscillatorAcceleration(0.016))
        compatibility.fini()

    def test_remote_autoaim_admits_exact_outline_candidate_and_switches(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        original_auto_aim = \
            runtime.avatar_module.PlayerAvatar.__dict__['autoAim']
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        avatar = runtime.avatar_module.PlayerAvatar()
        avatar.team = 1
        avatar.cell = types.SimpleNamespace(autoAim=mock.Mock())
        avatar.inputHandler = types.SimpleNamespace(
            setAimingMode=mock.Mock())
        avatar.gunRotator = types.SimpleNamespace(
            clientMode=True, dispersionAngle=0.24)
        avatar.vehicleTypeDescriptor = types.SimpleNamespace(
            gun=types.SimpleNamespace(shotDispersionAngle=0.08))
        avatar.onLockTarget = mock.Mock()
        runtime.bigworld.entity = runtime.bigworld.entities.get
        runtime.bigworld._player = avatar
        arcade = runtime.control_modes.ArcadeControlMode()
        sniper = runtime.control_modes.SniperControlMode()
        first_visual = object()
        second_visual = object()
        first = types.SimpleNamespace(
            _offlineLANPresentation=True, bw_entity=first_visual,
            id=1000, team=2, _spot_visible=True,
            isAlive=lambda: True)
        second = types.SimpleNamespace(
            _offlineLANPresentation=True, bw_entity=second_visual,
            id=1001, team=2, _spot_visible=True,
            isAlive=lambda: True)
        runtime.bigworld.entities.update({1000: first, 1001: second})

        compatibility.set_target_lock_candidate(first)
        self.assertIsNone(runtime.bigworld.target())
        arcade.handleKeyEvent(True, 'lock-target', 0, None)
        self.assertEqual(1000, avatar._PlayerAvatar__autoAimVehID)
        avatar.cell.autoAim.assert_called_once_with(1000)
        avatar.inputHandler.setAimingMode.assert_called_once_with(
            True, 'target-lock')
        self.assertFalse(avatar.gunRotator.clientMode)
        avatar.onLockTarget.assert_called_once_with('target_locked', True)
        self.assertIn(
            ('trigger_activate', 'auto_aim_at_vehicle',
             {'vehicleId': 1000}), operations)

        compatibility.set_target_lock_candidate(second)
        self.assertIsNone(runtime.bigworld.target())
        sniper.handleKeyEvent(True, 'lock-target', 0, None)
        self.assertEqual(1001, avatar._PlayerAvatar__autoAimVehID)
        self.assertEqual(
            [mock.call(1000), mock.call(1001)],
            avatar.cell.autoAim.call_args_list)

        # The explicit native lock-off path passes literal None and must take
        # the complete stock unlock path, including convergence bookkeeping.
        arcade.handleKeyEvent(True, 'lock-target-off', 0, None)
        self.assertEqual(0, avatar._PlayerAvatar__autoAimVehID)
        self.assertEqual(100.0, avatar._PlayerAvatar__aimingInfo[0])
        self.assertEqual(3.0, avatar._PlayerAvatar__aimingInfo[1])
        self.assertIn(('original_avatar_auto_aim', None), operations)
        avatar.autoAim(None)
        self.assertEqual(0, avatar._PlayerAvatar__autoAimVehID)
        self.assertEqual(
            [mock.call(1000), mock.call(1001), mock.call(0)],
            avatar.cell.autoAim.call_args_list)

        compatibility.fini()
        self.assertIs(
            original_auto_aim,
            runtime.avatar_module.PlayerAvatar.__dict__['autoAim'])

    def test_target_lock_input_scope_clears_on_consume_and_failure(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        original_target = runtime.bigworld.target
        original_arcade = runtime.control_modes.ArcadeControlMode.\
            __dict__['handleKeyEvent']
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        avatar = runtime.avatar_module.PlayerAvatar()
        avatar.team = 1
        avatar.cell = types.SimpleNamespace(autoAim=mock.Mock())
        avatar.inputHandler = types.SimpleNamespace(
            setAimingMode=mock.Mock())
        avatar.gunRotator = types.SimpleNamespace(
            clientMode=True, dispersionAngle=0.24)
        avatar.vehicleTypeDescriptor = types.SimpleNamespace(
            gun=types.SimpleNamespace(shotDispersionAngle=0.08))
        avatar.onLockTarget = mock.Mock()
        runtime.bigworld._player = avatar
        target = types.SimpleNamespace(
            _offlineLANPresentation=True, bw_entity=object(),
            id=1000, team=2, _spot_visible=True,
            isAlive=lambda: True)
        compatibility.set_target_lock_candidate(target)

        consumed = runtime.control_modes.ArcadeControlMode()
        consumed.consume_event = True
        self.assertTrue(
            consumed.handleKeyEvent(True, 'lock-target', 0, None))
        self.assertFalse(compatibility._target_lock_input_pending)
        self.assertIsNone(compatibility._target_lock_input_avatar)
        self.assertEqual(0, avatar._PlayerAvatar__autoAimVehID)

        failed = runtime.control_modes.ArcadeControlMode()
        failed.raise_event = True
        with self.assertRaisesRegex(
                RuntimeError, 'control-mode input failed'):
            failed.handleKeyEvent(True, 'lock-target', 0, None)
        self.assertFalse(compatibility._target_lock_input_pending)
        self.assertIsNone(compatibility._target_lock_input_avatar)
        self.assertIs(original_target, runtime.bigworld.target)

        commands = runtime.control_modes.CommandMapping
        commands.g_instance.overrides['lock-and-off'] = {
            commands.CMD_CM_LOCK_TARGET,
            commands.CMD_CM_LOCK_TARGET_OFF,
        }
        avatar.cell.autoAim.reset_mock()
        runtime.control_modes.ArcadeControlMode().handleKeyEvent(
            True, 'lock-and-off', 0, None)
        self.assertEqual(0, avatar._PlayerAvatar__autoAimVehID)
        self.assertEqual(
            [mock.call(1000), mock.call(0)],
            avatar.cell.autoAim.call_args_list)

        commands.g_instance.overrides['free-lock-and-off'] = {
            commands.CMD_CM_FREE_CAMERA,
            commands.CMD_CM_LOCK_TARGET,
            commands.CMD_CM_LOCK_TARGET_OFF,
        }
        avatar.cell.autoAim.reset_mock()
        runtime.control_modes.SniperControlMode().handleKeyEvent(
            True, 'free-lock-and-off', 0, None)
        self.assertEqual(0, avatar._PlayerAvatar__autoAimVehID)
        self.assertEqual(
            [mock.call(1000), mock.call(0)],
            avatar.cell.autoAim.call_args_list)

        compatibility.fini()
        self.assertIs(original_target, runtime.bigworld.target)
        self.assertIs(
            original_arcade,
            runtime.control_modes.ArcadeControlMode.__dict__[
                'handleKeyEvent'])

    def test_target_lock_wrapper_is_inert_outside_battle(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()
        control = runtime.control_modes.ArcadeControlMode()
        control.consume_event = True
        runtime.control_modes.CommandMapping.g_instance = None

        self.assertTrue(
            control.handleKeyEvent(True, 'lock-target', 0, None))
        self.assertFalse(compatibility._target_lock_input_pending)
        self.assertIsNone(compatibility._target_lock_input_avatar)
        compatibility.fini()

    def test_remote_autoaim_delegates_unrelated_native_vehicle(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        avatar = runtime.avatar_module.PlayerAvatar()
        avatar.team = 1
        avatar.cell = types.SimpleNamespace(autoAim=mock.Mock())
        avatar.inputHandler = types.SimpleNamespace(
            setAimingMode=mock.Mock())
        avatar.gunRotator = types.SimpleNamespace(
            clientMode=True, dispersionAngle=0.24)
        avatar.vehicleTypeDescriptor = types.SimpleNamespace(
            gun=types.SimpleNamespace(shotDispersionAngle=0.08))
        avatar.onLockTarget = mock.Mock()
        outlined = types.SimpleNamespace(
            _offlineLANPresentation=True, bw_entity=object(),
            id=1000, team=2, isAlive=lambda: True)
        native = runtime.vehicle_module.Vehicle()
        native.id = 77
        native.publicInfo = {'team': 2}
        native.isAlive = lambda: True

        compatibility.set_target_lock_candidate(outlined)
        runtime.bigworld._target = native
        self.assertIs(native, runtime.bigworld.target())
        avatar.autoAim(runtime.bigworld.target())

        self.assertEqual(77, avatar._PlayerAvatar__autoAimVehID)
        self.assertIn(('original_avatar_auto_aim', native), operations)
        compatibility.fini()

    def test_remote_autoaim_unlocks_through_stock_when_target_is_lost(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        avatar = runtime.avatar_module.PlayerAvatar()
        avatar.team = 1
        avatar.cell = types.SimpleNamespace(autoAim=mock.Mock())
        avatar.inputHandler = types.SimpleNamespace(
            setAimingMode=mock.Mock())
        avatar.gunRotator = types.SimpleNamespace(
            clientMode=True, dispersionAngle=0.24)
        avatar.vehicleTypeDescriptor = types.SimpleNamespace(
            gun=types.SimpleNamespace(shotDispersionAngle=0.08))
        avatar.onLockTarget = mock.Mock()
        runtime.bigworld.entity = runtime.bigworld.entities.get
        runtime.bigworld._player = avatar
        arcade = runtime.control_modes.ArcadeControlMode()
        target = types.SimpleNamespace(
            _offlineLANPresentation=True, bw_entity=object(),
            id=1000, team=2, _spot_visible=True, model=object(),
            inWorld=True, isAlive=lambda: True)
        runtime.bigworld.entities[1000] = target
        compatibility.set_target_lock_candidate(target)
        arcade.handleKeyEvent(True, 'lock-target', 0, None)

        self.assertFalse(compatibility.validate_target_lock(avatar))
        target._spot_visible = False
        self.assertTrue(compatibility.validate_target_lock(avatar))

        self.assertEqual(0, avatar._PlayerAvatar__autoAimVehID)
        self.assertEqual(100.0, avatar._PlayerAvatar__aimingInfo[0])
        self.assertEqual(3.0, avatar._PlayerAvatar__aimingInfo[1])
        self.assertEqual(
            ('original_avatar_auto_aim', None),
            [item for item in operations
             if item[0] == 'original_avatar_auto_aim'][-1])
        compatibility.fini()

    def test_a_locked_target_that_loses_its_visual_drops_the_lock(self):
        """A wreck keeps rendering after death, so presence is not enough:
        native aiming must never be handed a target mid-teardown."""
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        avatar = runtime.avatar_module.PlayerAvatar()
        avatar.team = 1
        avatar.cell = types.SimpleNamespace(autoAim=mock.Mock())
        avatar.inputHandler = types.SimpleNamespace(setAimingMode=mock.Mock())
        avatar.gunRotator = types.SimpleNamespace(
            clientMode=True, dispersionAngle=0.24)
        avatar.vehicleTypeDescriptor = types.SimpleNamespace(
            gun=types.SimpleNamespace(shotDispersionAngle=0.08))
        avatar.onLockTarget = mock.Mock()
        runtime.bigworld.entity = runtime.bigworld.entities.get
        runtime.bigworld._player = avatar
        arcade = runtime.control_modes.ArcadeControlMode()
        target = types.SimpleNamespace(
            _offlineLANPresentation=True, bw_entity=object(),
            id=1000, team=2, _spot_visible=True, model=object(),
            inWorld=True, isAlive=lambda: True)
        runtime.bigworld.entities[1000] = target
        compatibility.set_target_lock_candidate(target)
        arcade.handleKeyEvent(True, 'lock-target', 0, None)
        self.assertFalse(compatibility.validate_target_lock(avatar))

        target.bw_entity = None

        self.assertTrue(compatibility.validate_target_lock(avatar))
        self.assertEqual(0, avatar._PlayerAvatar__autoAimVehID)
        compatibility.fini()

    def test_an_unreadable_locked_target_drops_the_lock(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        avatar = runtime.avatar_module.PlayerAvatar()
        avatar.team = 1
        avatar.cell = types.SimpleNamespace(autoAim=mock.Mock())
        avatar.inputHandler = types.SimpleNamespace(setAimingMode=mock.Mock())
        avatar.gunRotator = types.SimpleNamespace(
            clientMode=True, dispersionAngle=0.24)
        avatar.vehicleTypeDescriptor = types.SimpleNamespace(
            gun=types.SimpleNamespace(shotDispersionAngle=0.08))
        avatar.onLockTarget = mock.Mock()
        avatar._PlayerAvatar__autoAimVehID = 1000

        def explode(unused_entity_id):
            raise RuntimeError('entity lookup rejected')

        runtime.bigworld.entity = explode

        self.assertTrue(compatibility.validate_target_lock(avatar))
        self.assertEqual(0, avatar._PlayerAvatar__autoAimVehID)
        compatibility.fini()

    def test_a_dead_target_lock_is_released_in_the_same_frame(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        avatar = runtime.avatar_module.PlayerAvatar()
        avatar.team = 1
        avatar.cell = types.SimpleNamespace(autoAim=mock.Mock())
        avatar.inputHandler = types.SimpleNamespace(setAimingMode=mock.Mock())
        avatar.gunRotator = types.SimpleNamespace(
            clientMode=True, dispersionAngle=0.24)
        avatar.vehicleTypeDescriptor = types.SimpleNamespace(
            gun=types.SimpleNamespace(shotDispersionAngle=0.08))
        avatar.onLockTarget = mock.Mock()
        avatar._PlayerAvatar__autoAimVehID = 1000

        self.assertFalse(compatibility.release_target_lock(avatar, 1001))
        self.assertEqual(1000, avatar._PlayerAvatar__autoAimVehID)

        self.assertTrue(compatibility.release_target_lock(avatar, 1000))
        self.assertEqual(0, avatar._PlayerAvatar__autoAimVehID)
        compatibility.fini()

    def test_fixed_turret_aim_reads_copied_pose_without_replacing_filter(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()

        class VehicleFilter(object):
            def interpolateStabilisedMatrix(self, timestamp):
                return 'native-stabilised-%s' % timestamp

        vehicle = runtime.vehicle_module.Vehicle()
        vehicle.filter = VehicleFilter()
        rotator = runtime.vehicle_gun_rotator.VehicleGunRotator()

        self.assertEqual(
            'native-stabilised-123.0',
            rotator.getAvatarOwnVehicleStabilisedMatrix(vehicle))
        compatibility.configure_battle()
        compatibility.set_vehicle_pose_overlay(
            vehicle, 'copied-position', 1.5, 'copied-matrix', 7.5, 0.25)

        self.assertEqual(
            'copied-matrix',
            rotator.getAvatarOwnVehicleStabilisedMatrix(vehicle))
        self.assertIs(vehicle.filter, vehicle.__dict__['filter'])
        self.assertEqual(
            'native-stabilised-123.0',
            vehicle.filter.interpolateStabilisedMatrix(123.0))
        compatibility.fini()

    def test_crashed_track_models_read_the_copied_live_pose(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()

        native_filter = types.SimpleNamespace(
            groundPlacingMatrix='spawn-matrix')
        vehicle = runtime.vehicle_module.Vehicle()
        vehicle.filter = native_filter
        controller = runtime.crashed_tracks_controller_type(vehicle)

        setup = getattr(
            controller,
            '_CrashedTrackController__setupTrackAssembler')
        loaded = getattr(
            controller,
            '_CrashedTrackController__onModelLoaded')
        self.assertEqual('spawn-matrix', setup(vehicle))
        self.assertEqual('spawn-matrix', loaded({}))

        compatibility.configure_battle()
        compatibility.set_vehicle_pose_overlay(
            vehicle, 'copied-position', 1.5, 'render-matrix', 7.5, 0.25)

        self.assertEqual('render-matrix', setup(vehicle))
        self.assertEqual('render-matrix', loaded({}))
        self.assertIs(native_filter, vehicle.filter)
        compatibility.fini()

    def test_offline_vehicle_physics_skips_only_initial_native_gun_sync(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        vehicle_type = runtime.vehicle_module.Vehicle
        original = vehicle_type.__dict__['_Vehicle__startWGPhysics']

        class VehicleFilter(object):
            speedInfo = 'native-speed-info'

            def setVehiclePhysics(self, physics):
                operations.append(('vehicle_filter_physics', physics))

            def syncGunAngles(self, yaw, pitch):
                operations.append(('unsafe_initial_gun_sync', yaw, pitch))

        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()

        normal_vehicle = vehicle_type()
        normal_vehicle.filter = VehicleFilter()
        normal_vehicle.physics = 'normal-physics'
        normal_vehicle._Vehicle__startWGPhysics()
        self.assertIn(
            ('unsafe_initial_gun_sync', 0.25, -0.5), operations)

        operations[:] = []
        compatibility.configure_battle()
        operations[:] = []
        offline_vehicle = vehicle_type()
        offline_vehicle.filter = VehicleFilter()
        offline_vehicle.physics = 'offline-physics'
        offline_vehicle._Vehicle__startWGPhysics()

        self.assertEqual(
            [
                ('vehicle_physics_init',),
                ('vehicle_physics_bounds',),
                ('vehicle_physics_owner',),
                ('vehicle_physics_static_mode',),
                ('vehicle_physics_movement_signals',),
                ('vehicle_filter_physics', 'offline-physics'),
                ('vehicle_physics_visibility',),
                ('vehicle_physics_speed', 'native-speed-info'),
            ],
            operations)
        self.assertIs(
            offline_vehicle.nested_start_filter,
            offline_vehicle.__dict__['filter'])
        self.assertIsNone(compatibility._vehicle_starting_wg_physics)

        compatibility.fini()
        self.assertIs(
            original,
            vehicle_type.__dict__['_Vehicle__startWGPhysics'])

    def test_vehicle_physics_scope_is_cleared_when_stock_setup_raises(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        vehicle_type = runtime.vehicle_module.Vehicle

        class FailingFilter(object):
            def setVehiclePhysics(self, physics):
                raise RuntimeError('native physics setup failed')

        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        vehicle = vehicle_type()
        vehicle.filter = FailingFilter()
        vehicle.physics = object()

        with self.assertRaisesRegex(
                RuntimeError, 'native physics setup failed'):
            vehicle._Vehicle__startWGPhysics()

        self.assertIsNone(compatibility._vehicle_starting_wg_physics)
        self.assertIs(vehicle.filter, vehicle.__dict__['filter'])
        compatibility.fini()

    def test_offline_gun_property_notifier_suppresses_native_sync(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        vehicle_type = runtime.vehicle_module.Vehicle
        original = vehicle_type.__dict__['set_gunAnglesPacked']

        class VehicleFilter(object):
            def syncGunAngles(self, yaw, pitch):
                operations.append(('unsafe_gun_sync', yaw, pitch))

        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()
        vehicle = vehicle_type()
        vehicle.filter = VehicleFilter()

        vehicle.set_gunAnglesPacked('normal')
        self.assertIn(('unsafe_gun_sync', 0.75, -0.25), operations)

        compatibility.configure_battle()
        operations[:] = []
        vehicle.set_gunAnglesPacked('offline')
        self.assertEqual(
            [('vehicle_gun_angles_before', 'offline'),
             ('vehicle_gun_angles_after',)],
            operations)
        self.assertIsNone(compatibility._vehicle_syncing_gun_angles)
        self.assertIs(vehicle.nested_gun_filter, vehicle.__dict__['filter'])
        self.assertIs(vehicle.filter, vehicle.__dict__['filter'])

        vehicle.fail_gun_angles = True
        with self.assertRaisesRegex(RuntimeError, 'gun angle update failed'):
            vehicle.set_gunAnglesPacked('failure')
        self.assertIsNone(compatibility._vehicle_syncing_gun_angles)

        compatibility.fini()
        self.assertIs(original, vehicle_type.__dict__['set_gunAnglesPacked'])

    def test_offline_damaged_model_refresh_suppresses_native_gun_sync(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        appearance_type = (
            runtime.compound_appearance_module.CompoundAppearance)
        method_name = '_CompoundAppearance__onModelsRefresh'
        original = appearance_type.__dict__[method_name]
        original_getattribute = appearance_type.__getattribute__

        class VehicleFilter(object):
            def __init__(self, name):
                self.name = name

            def syncGunAngles(self, yaw, pitch):
                operations.append(
                    ('unsafe_compound_gun_sync', self.name, yaw, pitch))

        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()
        appearance = appearance_type(VehicleFilter('initial'))

        getattr(appearance, method_name)('normal', {'normal': True})
        self.assertIn(
            ('unsafe_compound_gun_sync', 'initial', 0.5, -0.1),
            operations)

        compatibility.configure_battle()
        operations[:] = []
        replacement = VehicleFilter('replacement')
        appearance.replacement_filter = replacement
        getattr(appearance, method_name)('offline', {'offline': True})
        self.assertEqual(
            [('compound_refresh_before', 'offline', {'offline': True}),
             ('compound_refresh_after',)],
            operations)
        self.assertIsNone(compatibility._compound_refreshing_models)
        self.assertIs(replacement, appearance.nested_filter)
        self.assertIs(
            replacement,
            appearance._CompoundAppearance__filter)

        appearance.fail_models_refresh = True
        with self.assertRaisesRegex(RuntimeError, 'models refresh failed'):
            getattr(appearance, method_name)('failure', {})
        self.assertIsNone(compatibility._compound_refreshing_models)

        compatibility.fini()
        self.assertIs(original, appearance_type.__dict__[method_name])
        self.assertIs(
            original_getattribute, appearance_type.__getattribute__)

    def test_offline_appearance_teardown_survives_destroyed_input_handler(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        appearance_type = (
            runtime.compound_appearance_module.CompoundAppearance)
        original = appearance_type.__dict__['deactivate']
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        avatar = runtime.avatar_module.PlayerAvatar()
        avatar.inputHandler = None
        runtime.bigworld._player = avatar
        appearance = appearance_type(object())

        appearance.deactivate(False)

        self.assertIn(('compound_deactivate', False), operations)
        self.assertIsNone(avatar.inputHandler)
        compatibility.fini()
        self.assertIs(original, appearance_type.__dict__['deactivate'])

    def test_offline_appearance_teardown_survives_cleared_player_entity(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        appearance_type = (
            runtime.compound_appearance_module.CompoundAppearance)
        original = appearance_type.__dict__['deactivate']
        original_player = runtime.bigworld.__class__.__dict__['player']
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        runtime.bigworld._player = None
        appearance = appearance_type(object())

        appearance.deactivate(False)

        self.assertIn(('compound_deactivate', False), operations)
        self.assertNotIn('player', runtime.bigworld.__dict__)
        self.assertIs(original_player,
                      runtime.bigworld.__class__.__dict__['player'])
        self.assertIsNone(runtime.bigworld.player())
        compatibility.fini()
        self.assertIs(original, appearance_type.__dict__['deactivate'])

    def test_offline_appearance_teardown_restores_player_after_failure(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        appearance_type = (
            runtime.compound_appearance_module.CompoundAppearance)
        original_player = runtime.bigworld.__class__.__dict__['player']
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        runtime.bigworld._player = None
        appearance = appearance_type(object())
        appearance.fail_deactivate = True

        with self.assertRaisesRegex(
                RuntimeError, 'compound deactivate failed'):
            appearance.deactivate(False)

        self.assertNotIn('player', runtime.bigworld.__dict__)
        self.assertIs(original_player,
                      runtime.bigworld.__class__.__dict__['player'])
        self.assertIsNone(runtime.bigworld.player())
        compatibility.fini()

    def test_offline_vehicle_leave_survives_cleared_player_entity(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        vehicle_type = runtime.vehicle_module.Vehicle
        original = vehicle_type.__dict__['onLeaveWorld']
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()
        vehicle = vehicle_type()
        vehicle.id = 91
        vehicle.isStarted = True
        runtime.bigworld._player = None

        vehicle.onLeaveWorld()

        self.assertIn(('vehicle_stop_extras',), operations)
        self.assertIn(('vehicle_stop_visual', False), operations)
        self.assertFalse(vehicle.isStarted)
        compatibility.fini()
        self.assertIs(original, vehicle_type.__dict__['onLeaveWorld'])

    def test_fini_preserves_late_third_party_teardown_wrappers(self):
        compatibility_module = _load_port_source('compat')
        runtime, unused_operations = self._runtime()
        vehicle_type = runtime.vehicle_module.Vehicle
        appearance_type = (
            runtime.compound_appearance_module.CompoundAppearance)
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()

        def late_vehicle(vehicle):
            return None

        def late_appearance(appearance, stopEffects=True):
            return None

        vehicle_type.onLeaveWorld = late_vehicle
        appearance_type.deactivate = late_appearance
        compatibility.fini()

        self.assertIs(late_vehicle, vehicle_type.__dict__['onLeaveWorld'])
        self.assertIs(late_appearance,
                      appearance_type.__dict__['deactivate'])

    def test_offline_aux_physics_skips_only_native_stabilised_sync(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        avatar_type = runtime.avatar_module.PlayerAvatar
        vehicle_type = runtime.vehicle_module.Vehicle
        method_name = '_PlayerAvatar__onSetOwnVehicleAuxPhysicsData'
        original = avatar_type.__dict__[method_name]

        class VehicleFilter(object):
            def syncStabilisedYPR(self, yaw, pitch, roll):
                operations.append(
                    ('unsafe_stabilised_sync', yaw, pitch, roll))

        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()
        avatar = avatar_type()
        vehicle = vehicle_type()
        vehicle.filter = VehicleFilter()
        avatar.aux_vehicle = vehicle

        getattr(avatar, method_name)('normal')
        self.assertIn(
            ('unsafe_stabilised_sync', 0.1, 0.2, 0.3), operations)

        compatibility.configure_battle()
        operations[:] = []
        getattr(avatar, method_name)('offline')

        self.assertEqual(
            [('avatar_aux_physics_before', 'offline'),
             ('avatar_aux_physics_after',)],
            operations)
        self.assertIsNone(compatibility._avatar_syncing_aux_physics)
        self.assertIs(vehicle.__dict__['filter'], avatar.aux_nested_filter)
        self.assertIs(vehicle.filter, vehicle.__dict__['filter'])

        compatibility.fini()
        self.assertIs(original, avatar_type.__dict__[method_name])

    def test_aux_physics_scope_is_cleared_when_stock_handler_raises(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        avatar_type = runtime.avatar_module.PlayerAvatar
        vehicle_type = runtime.vehicle_module.Vehicle
        method_name = '_PlayerAvatar__onSetOwnVehicleAuxPhysicsData'
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.configure_battle()

        class VehicleFilter(object):
            def syncStabilisedYPR(self, yaw, pitch, roll):
                raise AssertionError('native sync must be suppressed')

        avatar = avatar_type()
        avatar.aux_vehicle = vehicle_type()
        avatar.aux_vehicle.filter = VehicleFilter()
        avatar.fail_aux_physics = True
        with self.assertRaisesRegex(
                RuntimeError, 'aux physics update failed'):
            getattr(avatar, method_name)('offline')

        self.assertIsNone(compatibility._avatar_syncing_aux_physics)
        self.assertIs(
            avatar.aux_vehicle.filter,
            avatar.aux_vehicle.__dict__['filter'])
        compatibility.fini()

    def test_manual_offline_host_login_prepares_account_properties(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        account_type = runtime.account_module.PlayerAccount
        property_name, property_value = \
            runtime.account_module._CLIENT_SERVER_VERSION
        # Exact #1513 supplies entity-definition properties before Python
        # __init__, but does not supply Account.name while the offline map is
        # inactive.  This mirrors the second login after accepting the EULA.
        setattr(account_type, property_name, property_value)
        account_type.initialServerSettings = dict(
            compatibility_module._SERVER_SETTINGS)
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()

        runtime.connection_manager.initiateConnection(
            {}, '', compatibility_module.OFFLINE_SERVER_ADDRESS)

        account = runtime.bigworld.player()
        self.assertTrue(compatibility.is_ready())
        self.assertEqual('offline_account', account.name)
        self.assertTrue(account.isOffline)
        self.assertIs(account.fakeServer, account.base)
        self.assertFalse(compatibility._connecting)
        compatibility.fini()

    def test_retired_account_drops_callbacks_even_if_player_identity_lingers(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        account = runtime.bigworld.player()
        server = account.fakeServer

        server.doCmdInt3(91, 999999, 0, 0, 0)
        self.assertEqual(1, len(runtime.bigworld._callbacks))
        account.__dict__.clear()
        self.assertIs(account, runtime.bigworld.player())
        unused_delay, callback = runtime.bigworld._callbacks.pop(0)

        callback()

        self.assertIsNone(server._player())
        compatibility.disconnect()
        compatibility.fini()

    def test_relogin_cache_can_read_retired_offline_account_name(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        account_type = runtime.account_module.PlayerAccount
        native_init = account_type.__init__
        cache_keys = []
        repository_creations = [0]

        class PersistentCache(object):
            def __init__(self):
                self.account = None

            def setAccount(self, account):
                self.account = (weakref.proxy(account)
                                if account is not None else None)

            def save(self):
                if self.account is not None:
                    cache_keys.append('%s_%s_data' % (
                        self.account.name,
                        self.account.__class__.__name__))

        class SyncData(object):
            def __init__(self):
                self.account = None
                self._AccountSyncData__persistentCache = PersistentCache()

            def setAccount(self, account):
                # Exact #1513 saves through the old cache proxy before it
                # normally rebinds that proxy to the replacement Account.
                self.account = account
                self._AccountSyncData__persistentCache.save()
                if account is not None:
                    self._AccountSyncData__persistentCache.setAccount(account)

        class Repository(object):
            def __init__(self):
                self.className = account_type.__name__
                self.syncData = SyncData()

        def repository_init(account):
            native_init(account)
            repository = runtime.account_module.g_accountRepository
            if repository is None:
                repository_creations[0] += 1
                repository = Repository()
                runtime.account_module.g_accountRepository = repository
            account.syncData = repository.syncData
            account.syncData.setAccount(account)

        runtime.account_module.g_accountRepository = None
        account_type.__init__ = repository_init
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        first_account = runtime.bigworld.player()
        repository = runtime.account_module.g_accountRepository
        persistent_cache = (
            repository.syncData._AccountSyncData__persistentCache)

        # PyEntity::onEntityDestroyed clears the complete instance dictionary,
        # while #1513's shared persistent cache still holds its weak proxy.
        # Detach ChatManager explicitly so this test isolates the intentionally
        # stale persistent-cache proxy rather than the separate chat lifecycle.
        runtime.chat_manager.playerProxy = None
        first_account.__dict__.clear()
        with self.assertRaises(AttributeError):
            unused = persistent_cache.account.name
        runtime.bigworld.entities.clear()
        runtime.bigworld._player = None

        restored = compatibility.restore_lobby_account()

        self.assertEqual(1, repository_creations[0])
        self.assertEqual(
            ['offline_account_PlayerAccount_data'], cache_keys)
        self.assertFalse(compatibility._connecting)
        restored.name = 'native_name'
        self.assertEqual('native_name', restored.name)

        # A dead weak proxy fails before any attribute getter can run.  The
        # prebind must replace it without dereferencing the retired object.
        class RetiredAccount(object):
            pass

        retired = RetiredAccount()
        retired.name = 'retired'
        persistent_cache.setAccount(retired)
        dead_proxy = persistent_cache.account
        del retired
        gc.collect()
        with self.assertRaises(ReferenceError):
            unused = dead_proxy.name
        runtime.bigworld.entities.clear()
        runtime.bigworld._player = None

        compatibility.restore_lobby_account()

        self.assertEqual(
            ['offline_account_PlayerAccount_data'] * 2, cache_keys)
        compatibility.fini()

    def test_relogin_does_not_prebind_a_different_account_repository(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        persistent_cache = mock.Mock()
        runtime.account_module.g_accountRepository = types.SimpleNamespace(
            className='DifferentPlayerAccount',
            syncData=types.SimpleNamespace(
                _AccountSyncData__persistentCache=persistent_cache))
        compatibility = compatibility_module.OfflineCompatibility(runtime)

        compatibility.connect(show_lobby=True)

        persistent_cache.setAccount.assert_not_called()
        compatibility.fini()

    def test_avatar_properties_and_mailboxes_exist_during_native_init(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        observed = {}

        def strict_avatar_init(avatar):
            observed['name'] = avatar.name
            observed['client_ctx'] = avatar.clientCtx
            observed['team'] = avatar.team
            observed['vehicle_id'] = avatar.playerVehicleID
            observed['mailboxes'] = (
                avatar.base, avatar.cell, avatar.server, avatar.bwProto)

        runtime.avatar_module.PlayerAvatar.__init__ = strict_avatar_init
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        compatibility.configure_battle(
            player_name=u'Player-玩家', player_team=2)

        avatar = runtime.avatar_module.PlayerAvatar()

        self.assertEqual(b'Player-\xe7\x8e\xa9\xe5\xae\xb6',
                         observed['name'])
        self.assertIsInstance(observed['name'], bytes)
        self.assertEqual(b'', observed['client_ctx'])
        self.assertIsInstance(observed['client_ctx'], bytes)
        self.assertEqual(2, observed['team'])
        self.assertEqual(0, observed['vehicle_id'])
        self.assertEqual(
            (avatar.fakeServer,) * 4, observed['mailboxes'])
        compatibility.fini()

    def test_partial_avatar_world_callbacks_skip_stock_fields_offline(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)

        partial = object.__new__(runtime.avatar_module.PlayerAvatar)
        partial.onEnterWorld(('partial',))
        partial.onLeaveWorld()

        names = [item[0] for item in operations]
        self.assertNotIn('original_avatar_enter_world', names)
        self.assertNotIn('original_avatar_leave_world', names)

        complete = runtime.avatar_module.PlayerAvatar()
        complete.onEnterWorld(('complete',))
        complete.onLeaveWorld()
        self.assertIn(
            ('original_avatar_enter_world', ('complete',)), operations)
        self.assertIn(('original_avatar_leave_world',), operations)
        compatibility.fini()

    def test_partial_avatar_world_callbacks_are_not_hidden_online(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()
        partial = object.__new__(runtime.avatar_module.PlayerAvatar)

        with self.assertRaises(AttributeError):
            partial.onEnterWorld(('online',))
        with self.assertRaises(AttributeError):
            partial.onLeaveWorld()
        compatibility.fini()

    def test_avatar_team_rejects_out_of_entity_range(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)

        with self.assertRaisesRegex(ValueError, 'team must be 1 or 2'):
            compatibility.configure_battle(player_team=3)
        compatibility.fini()

    def test_avatar_normal_return_without_arena_is_not_marked_ready(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        runtime.avatar_module.PlayerAvatar.onBecomePlayer = (
            lambda avatar: None)
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        compatibility.configure_battle()
        avatar = runtime.avatar_module.PlayerAvatar()

        with self.assertRaisesRegex(RuntimeError,
                                    'no initialized arena type'):
            avatar.onBecomePlayer()

        self.assertFalse(getattr(
            avatar, '_offlineLANPlayerReady', False))
        compatibility.fini()

    def test_partial_avatar_promotion_is_retired_exactly_once(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()

        def partial_avatar_become_player(avatar):
            operations.append(('partial_avatar_become_player',))
            runtime.chat_manager.switchPlayerProxy(avatar)
            raise RuntimeError('native Avatar promotion failed')

        runtime.avatar_module.PlayerAvatar.onBecomePlayer = \
            partial_avatar_become_player
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        self.assertTrue(compatibility.retire_current_player())
        runtime.bigworld.clearAllSpaces()
        compatibility.configure_battle()
        avatar = runtime.avatar_module.PlayerAvatar()
        runtime.bigworld._player = avatar

        with self.assertRaisesRegex(
                RuntimeError, 'native Avatar promotion failed'):
            avatar.onBecomePlayer()

        self.assertIs(avatar, runtime.chat_manager.playerProxy)
        self.assertTrue(getattr(
            avatar, '_offlineLANRetirePending', False))
        self.assertFalse(getattr(
            avatar, '_offlineLANPlayerReady', False))
        self.assertTrue(compatibility.retire_current_player())
        self.assertFalse(compatibility.retire_current_player())
        self.assertIsNone(runtime.chat_manager.playerProxy)
        self.assertEqual(
            1, [item[0] for item in operations].count(
                'original_avatar_become_non_player'))
        compatibility.fini()

    def test_failed_native_retirement_still_detaches_chat_proxy(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()

        def failing_avatar_become_non_player(avatar):
            operations.append(('failing_avatar_become_non_player',))
            raise RuntimeError('native Avatar retirement failed')

        runtime.avatar_module.PlayerAvatar.onBecomeNonPlayer = \
            failing_avatar_become_non_player
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        self.assertTrue(compatibility.retire_current_player())
        runtime.bigworld.clearAllSpaces()
        compatibility.configure_battle()
        avatar = runtime.avatar_module.PlayerAvatar()
        runtime.bigworld._player = avatar
        avatar.onBecomePlayer()

        with self.assertRaisesRegex(
                RuntimeError, 'native Avatar retirement failed'):
            compatibility.retire_current_player()

        self.assertIsNone(runtime.chat_manager.playerProxy)
        self.assertFalse(getattr(
            avatar, '_offlineLANRetirePending', False))
        self.assertFalse(compatibility.retire_current_player())
        self.assertEqual(
            1, [item[0] for item in operations].count(
                'failing_avatar_become_non_player'))
        runtime.bigworld.clearAllSpaces()
        compatibility.fini()

    def test_retired_avatar_drops_uncancellable_resource_callback(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        compatibility.configure_battle()
        avatar = runtime.avatar_module.PlayerAvatar()
        runtime.bigworld._player = avatar
        avatar.onBecomePlayer()
        delayed = avatar.onPrereqsLoaded

        delayed(('tank',), {'tank': object()})
        self.assertEqual(
            1, len([item for item in operations
                   if item[0] == 'avatar_prereqs_loaded']))
        avatar.__dict__.clear()
        self.assertIs(avatar, runtime.bigworld.player())

        delayed(('tank',), {'tank': object()})

        self.assertEqual(
            1, len([item for item in operations
                   if item[0] == 'avatar_prereqs_loaded']))
        compatibility.fini()

    def test_vehicle_cell_uses_only_vehicle_or_avatar_mailbox(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        account = runtime.bigworld.player()
        compatibility.configure_battle()
        vehicle = runtime.vehicle_module.Vehicle()

        explicit_cell = object()
        vehicle.fakeCell = explicit_cell
        self.assertIs(explicit_cell, vehicle.cell)
        del vehicle.fakeCell

        runtime.bigworld._player = account
        with self.assertRaises(AttributeError):
            unused = vehicle.cell

        avatar = runtime.avatar_module.PlayerAvatar()
        runtime.bigworld._player = avatar
        self.assertIs(avatar.fakeServer, vehicle.cell)

        compatibility.deactivate_map()
        with self.assertRaises(AttributeError):
            unused = vehicle.cell
        compatibility.fini()

    def test_delegates_real_server_and_preserves_existing_offline_host(self):
        compatibility_module = _load_port_source('compat')
        existing = types.SimpleNamespace(
            url=compatibility_module.OFFLINE_SERVER_ADDRESS)
        runtime, operations = self._runtime(existing_hosts=[existing])
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()

        result = runtime.bigworld.connect('real.example:2000', {}, mock.Mock())
        self.assertEqual('online-connect', result)
        compatibility.fini()

        self.assertEqual([existing], runtime.predefined_hosts._hosts)
        self.assertIn(('original_connect', 'real.example:2000'), operations)

    def test_restores_account_after_offline_map_clears_all_entities(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        first_account = runtime.bigworld.player()

        self.assertTrue(compatibility.retire_current_player())
        runtime.bigworld.clearAllSpaces()
        restore_offset = len(operations)
        restored = compatibility.restore_lobby_account()

        self.assertIsNot(first_account, restored)
        self.assertIs(restored, runtime.bigworld.player())
        self.assertTrue(restored.isOffline)
        self.assertIs(restored.fakeServer, restored.base)
        self.assertTrue(compatibility.is_ready())
        names = [item[0] for item in operations]
        self.assertEqual(2, names.count('account_space'))
        self.assertEqual(2, names.count('account_entity'))
        self.assertEqual(2, names.count('original_account_init'))
        self.assertFalse(compatibility._connecting)
        restore_names = [item[0] for item in operations[restore_offset:]]
        self.assertLess(restore_names.index('prb_dispatcher_create'),
                        restore_names.index('account_entity'))

    def test_account_avatar_account_handoff_detaches_chat_before_each_clear(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        original_target = runtime.bigworld.target
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        first_account = runtime.bigworld.player()
        self.assertIs(original_target, runtime.bigworld.target)

        self.assertTrue(compatibility.retire_current_player())
        self.assertIsNone(runtime.chat_manager.playerProxy)
        runtime.bigworld.clearAllSpaces()
        self.assertEqual({}, first_account.__dict__)

        compatibility.configure_battle()
        avatar = runtime.avatar_module.PlayerAvatar()
        runtime.bigworld._player = avatar
        avatar.onBecomePlayer()
        self.assertIs(avatar, runtime.chat_manager.playerProxy)
        self.assertIs(original_target, runtime.bigworld.target)

        self.assertTrue(compatibility.retire_current_player())
        self.assertFalse(compatibility.retire_current_player())
        self.assertIsNone(runtime.chat_manager.playerProxy)
        runtime.bigworld.clearAllSpaces()
        self.assertEqual({}, avatar.__dict__)

        compatibility.deactivate_map()
        replacement = compatibility.restore_lobby_account()
        self.assertIs(replacement, runtime.chat_manager.playerProxy)
        self.assertIsNot(first_account, replacement)
        self.assertIs(original_target, runtime.bigworld.target)
        self.assertIsNone(compatibility._target_lock_candidate)
        self.assertFalse(compatibility._target_lock_input_pending)

        self.assertTrue(compatibility.retire_current_player())
        runtime.bigworld.clearAllSpaces()
        compatibility.configure_battle()
        second_avatar = runtime.avatar_module.PlayerAvatar()
        runtime.bigworld._player = second_avatar
        second_avatar.onBecomePlayer()
        self.assertIs(second_avatar, runtime.chat_manager.playerProxy)
        self.assertIs(original_target, runtime.bigworld.target)

        self.assertTrue(compatibility.retire_current_player())
        runtime.bigworld.clearAllSpaces()
        compatibility.deactivate_map()
        final_account = compatibility.restore_lobby_account()
        self.assertIs(final_account, runtime.chat_manager.playerProxy)
        self.assertIsNot(replacement, final_account)
        self.assertIs(original_target, runtime.bigworld.target)
        self.assertIsNone(compatibility._target_lock_candidate)
        self.assertFalse(compatibility._target_lock_input_pending)
        names = [item[0] for item in operations]
        self.assertEqual(3, names.count('original_account_become_player'))
        self.assertEqual(2, names.count('original_account_become_non_player'))
        self.assertEqual(2, names.count('original_avatar_become_non_player'))

    def test_account_promotion_restores_clear_all_spaces_after_failure(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        original_clear_all_spaces = runtime.bigworld.clearAllSpaces

        def failing_become_player(account):
            runtime.bigworld.clearAllSpaces()
            raise RuntimeError('native account promotion failed')

        runtime.account_module.PlayerAccount.onBecomePlayer = \
            failing_become_player
        compatibility = compatibility_module.OfflineCompatibility(runtime)

        with self.assertRaisesRegex(
                RuntimeError, 'native account promotion failed'):
            compatibility.connect(show_lobby=True)

        self.assertEqual(original_clear_all_spaces,
                         runtime.bigworld.clearAllSpaces)
        self.assertIsNone(runtime.bigworld.player())
        self.assertEqual({}, runtime.bigworld.entities)
        self.assertEqual(
            2, runtime.bigworld.operations.count(('clear_all_spaces',)))
        self.assertIn(('wgc', False), runtime.bigworld.operations)
        self.assertEqual(
            1, runtime.bigworld.operations.count(('disconnected',)))
        self.assertEqual(
            1, runtime.bigworld.operations.count(('player_disconnected',)))
        self.assertFalse(compatibility._connecting)
        runtime.bigworld.clearAllSpaces()
        self.assertEqual(
            3, runtime.bigworld.operations.count(('clear_all_spaces',)))

    def test_partial_account_promotion_detaches_chat_before_entity_clear(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()

        def partial_account_become_player(account):
            runtime.bigworld.clearAllSpaces()
            runtime.chat_manager.switchPlayerProxy(account)
            operations.append(('partial_account_become_player',))
            raise RuntimeError('native Account promotion failed')

        runtime.account_module.PlayerAccount.onBecomePlayer = \
            partial_account_become_player
        compatibility = compatibility_module.OfflineCompatibility(runtime)

        with self.assertRaisesRegex(
                RuntimeError, 'native Account promotion failed'):
            compatibility.connect(show_lobby=True)

        self.assertIsNone(runtime.chat_manager.playerProxy)
        self.assertIsNone(runtime.bigworld.player())
        self.assertEqual({}, runtime.bigworld.entities)
        self.assertEqual(
            1, [item[0] for item in operations].count(
                'original_account_become_non_player'))
        self.assertFalse(compatibility._fake_connected)
        compatibility.fini()

    def test_logged_on_listener_failure_rolls_back_entire_connection(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        deleted = []
        runtime.account_module.g_accountRepository = object()

        def delete_repository():
            deleted.append(True)
            runtime.account_module.g_accountRepository = None

        def fail_logged_on(unused_context):
            operations.append(('logged_on_failed',))
            raise RuntimeError('logged-on listener failed')

        runtime.account_module._delAccountRepository = delete_repository
        runtime.connection_manager.onLoggedOn = fail_logged_on
        compatibility = compatibility_module.OfflineCompatibility(runtime)

        with self.assertRaisesRegex(RuntimeError,
                                    'logged-on listener failed'):
            compatibility.connect(show_lobby=True)

        self.assertIsNone(runtime.bigworld.player())
        self.assertEqual({}, runtime.bigworld.entities)
        self.assertEqual(runtime.login_status.NOT_SET,
                         runtime.connection_manager.
                         _ConnectionManager__connectionStatus)
        self.assertEqual([True], deleted)
        self.assertNotIn('account_entity', [item[0] for item in operations])
        self.assertIn(('wgc', False), operations)
        self.assertIn(('disconnected',), operations)
        self.assertIn(('player_disconnected',), operations)
        self.assertFalse(compatibility._fake_connected)
        self.assertFalse(compatibility._connecting)
        compatibility.fini()

    def test_swallowed_account_init_failure_is_never_promoted(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        account_type = runtime.account_module.PlayerAccount
        deleted = []

        def fail_native_init(account):
            operations.append(('partial_account_init',))
            account.partial = True
            raise RuntimeError('native Account init failed')

        account_type.__init__ = fail_native_init

        def swallow_create_error(entity_type, unused_space_id,
                                 unused_client_only, unused_position,
                                 unused_orientation, unused_properties):
            self.assertEqual('Account', entity_type)
            entity_id = runtime.bigworld._next_entity
            runtime.bigworld._next_entity += 1
            account = account_type.__new__(account_type)
            try:
                account_type.__init__(account)
            except RuntimeError:
                pass
            runtime.bigworld.entities[entity_id] = account
            return entity_id

        def delete_repository():
            deleted.append(True)
            runtime.account_module.g_accountRepository = None

        runtime.bigworld.createEntity = swallow_create_error
        runtime.account_module.g_accountRepository = object()
        runtime.account_module._delAccountRepository = delete_repository
        compatibility = compatibility_module.OfflineCompatibility(runtime)

        with self.assertRaisesRegex(
                RuntimeError, 'partial offline Account'):
            compatibility.connect(show_lobby=True)

        names = [item[0] for item in operations]
        self.assertNotIn('player', names)
        self.assertNotIn('show_gui', names)
        self.assertIsNone(runtime.bigworld.player())
        self.assertEqual({}, runtime.bigworld.entities)
        self.assertEqual([True, True], deleted)
        self.assertFalse(compatibility._fake_connected)
        compatibility.fini()

    def test_failed_lobby_restore_retires_partial_connection_and_reconnects(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        account_type = runtime.account_module.PlayerAccount
        native_init = account_type.__init__
        constructions = [0]
        deleted = []

        def fail_second_construction(account):
            constructions[0] += 1
            native_init(account)
            if constructions[0] == 2:
                raise RuntimeError('replacement Account init failed')

        def delete_repository():
            deleted.append(True)
            runtime.account_module.g_accountRepository = None

        account_type.__init__ = fail_second_construction
        runtime.account_module._delAccountRepository = delete_repository
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        runtime.account_module.g_accountRepository = object()
        self.assertTrue(compatibility.retire_current_player())
        runtime.bigworld.clearAllSpaces()

        with self.assertRaisesRegex(
                RuntimeError, 'replacement Account init failed'):
            compatibility.restore_lobby_account()

        self.assertEqual({}, runtime.bigworld.entities)
        self.assertIsNone(runtime.bigworld.player())
        self.assertIsNone(runtime.account_module.g_accountRepository)
        self.assertFalse(compatibility._fake_connected)
        self.assertGreaterEqual(len(deleted), 1)

        compatibility.connect(show_lobby=True)
        self.assertTrue(compatibility.is_ready())
        self.assertEqual(1, len(runtime.bigworld.entities))
        compatibility.fini()

    def test_disconnect_listener_failure_still_cleans_every_boundary(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        deleted = []

        def fail_disconnected():
            operations.append(('disconnected_failed',))
            raise RuntimeError('disconnect listener failed')

        def delete_repository():
            deleted.append(True)

        runtime.connection_manager.onDisconnected = fail_disconnected
        runtime.account_module._delAccountRepository = delete_repository
        runtime.offline_map_creator.active = True

        with self.assertRaisesRegex(RuntimeError,
                                    'disconnect listener failed'):
            compatibility.disconnect()

        self.assertIsNone(runtime.bigworld.player())
        self.assertEqual({}, runtime.bigworld.entities)
        self.assertEqual([True], deleted)
        self.assertIn(('player_disconnected',), operations)
        self.assertFalse(runtime.offline_map_creator.active)
        self.assertFalse(compatibility._fake_connected)
        compatibility.fini()

    def test_fini_restores_all_patches_even_when_disconnect_listener_fails(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        account_type = runtime.account_module.PlayerAccount
        avatar_type = runtime.avatar_module.PlayerAvatar
        vehicle_type = runtime.vehicle_module.Vehicle
        originals = (
            account_type.__dict__['__init__'],
            account_type.__getattribute__,
            avatar_type.__dict__['__init__'],
            avatar_type.__getattribute__,
            avatar_type.__dict__['onEnterWorld'],
            avatar_type.__dict__['onLeaveWorld'],
            vehicle_type.__getattribute__,
            vehicle_type.__dict__['_Vehicle__startWGPhysics'],
            runtime.control_modes.ArcadeControlMode.__dict__[
                'handleKeyEvent'],
            runtime.control_modes.SniperControlMode.__dict__[
                'handleKeyEvent'],
            runtime.bigworld.connect,
            runtime.bigworld.disconnect,
        )
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        compatibility.configure_battle()

        def fail_disconnected():
            raise RuntimeError('disconnect listener failed')

        runtime.connection_manager.onDisconnected = fail_disconnected
        with self.assertRaisesRegex(RuntimeError,
                                    'disconnect listener failed'):
            compatibility.fini()

        self.assertFalse(compatibility._installed)
        self.assertFalse(compatibility._battle_active)
        self.assertFalse(compatibility._native_battle)
        self.assertEqual([], runtime.predefined_hosts._hosts)
        self.assertIs(originals[0], account_type.__dict__['__init__'])
        self.assertIs(originals[1], account_type.__getattribute__)
        self.assertIs(originals[2], avatar_type.__dict__['__init__'])
        self.assertIs(originals[3], avatar_type.__getattribute__)
        self.assertIs(originals[4], avatar_type.__dict__['onEnterWorld'])
        self.assertIs(originals[5], avatar_type.__dict__['onLeaveWorld'])
        self.assertIs(originals[6], vehicle_type.__getattribute__)
        self.assertIs(
            originals[7],
            vehicle_type.__dict__['_Vehicle__startWGPhysics'])
        self.assertIs(
            originals[8],
            runtime.control_modes.ArcadeControlMode.__dict__[
                'handleKeyEvent'])
        self.assertIs(
            originals[9],
            runtime.control_modes.SniperControlMode.__dict__[
                'handleKeyEvent'])
        self.assertIs(originals[10].__func__,
                      runtime.bigworld.connect.__func__)
        self.assertIs(originals[10].__self__,
                      runtime.bigworld.connect.__self__)
        self.assertIs(originals[11].__func__,
                      runtime.bigworld.disconnect.__func__)
        self.assertIs(originals[11].__self__,
                      runtime.bigworld.disconnect.__self__)

    def test_lobby_restore_does_not_replace_an_existing_player(self):
        compatibility_module = _load_port_source('compat')
        runtime, operations = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.connect(show_lobby=True)
        first_account = runtime.bigworld.player()

        restored = compatibility.restore_lobby_account()

        self.assertIs(first_account, restored)
        names = [item[0] for item in operations]
        self.assertEqual(1, names.count('account_entity'))

    def test_failed_install_rolls_back_without_leaving_patches(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime(host_failure=True)
        original_init = runtime.account_module.PlayerAccount.__dict__['__init__']
        original_avatar_init = (
            runtime.avatar_module.PlayerAvatar.__dict__['__init__'])
        original_avatar_become_player = (
            runtime.avatar_module.PlayerAvatar.__dict__['onBecomePlayer'])
        original_connect = runtime.bigworld.connect
        compatibility = compatibility_module.OfflineCompatibility(runtime)

        with self.assertRaisesRegex(RuntimeError, 'host creation failed'):
            compatibility.install()

        self.assertIs(
            original_init,
            runtime.account_module.PlayerAccount.__dict__['__init__'])
        self.assertIs(
            original_avatar_init,
            runtime.avatar_module.PlayerAvatar.__dict__['__init__'])
        self.assertIs(
            original_avatar_become_player,
            runtime.avatar_module.PlayerAvatar.__dict__['onBecomePlayer'])
        self.assertEqual(original_connect, runtime.bigworld.connect)
        self.assertFalse(compatibility._installed)

    def test_saved_interface_settings_use_one_account_section(self):
        # #1513 AccountSettings keys its section on BigWorld.player().name,
        # which offline differs between the lobby account, the LAN roster name
        # in battle and the empty pre-login state.
        compatibility_module = _load_port_source('compat')
        settings_module = sys.modules['account_helpers.AccountSettings']
        settings_type = settings_module.AccountSettings
        original = settings_type.__dict__['_AccountSettings__readUserSection']

        self.assertTrue(
            compatibility_module.pin_account_settings(settings_module))

        section = settings_type._AccountSettings__readUserSection()
        self.assertEqual('offline_account', section.readString('login'))
        self.assertEqual(['convert', 'invalidate'], settings_type.converted)
        settings_type._AccountSettings__cache['login'] = 'Player-1'
        self.assertIs(
            section, settings_type._AccountSettings__readUserSection())
        self.assertEqual(
            1, len([key for key, unused in self.preferences.items()
                    if key == 'account']))
        # The pin must outlive every connect/disconnect: an earlier build
        # installed it with the rest of the layer, so a battle removed it.
        self.assertIsNot(
            original,
            settings_type.__dict__['_AccountSettings__readUserSection'])
        compatibility = compatibility_module.OfflineCompatibility(
            self._runtime()[0])
        compatibility.install()
        compatibility.fini()
        self.assertIsNot(
            original,
            settings_type.__dict__['_AccountSettings__readUserSection'])

    def test_fini_does_not_overwrite_later_third_party_wrappers(self):
        compatibility_module = _load_port_source('compat')
        runtime, _ = self._runtime()
        compatibility = compatibility_module.OfflineCompatibility(runtime)
        compatibility.install()

        def later_account_init(account):
            account.later = True

        def later_connect(server, params, progress):
            return 'later'

        def later_avatar_enter(avatar, prereqs):
            return 'later-enter'

        def later_avatar_leave(avatar):
            return 'later-leave'

        def later_arcade(control, isDown, key, mods, event):
            return 'later-arcade'

        def later_sniper(control, isDown, key, mods, event):
            return 'later-sniper'

        runtime.account_module.PlayerAccount.__init__ = later_account_init
        runtime.avatar_module.PlayerAvatar.onEnterWorld = later_avatar_enter
        runtime.avatar_module.PlayerAvatar.onLeaveWorld = later_avatar_leave
        runtime.control_modes.ArcadeControlMode.handleKeyEvent = \
            later_arcade
        runtime.control_modes.SniperControlMode.handleKeyEvent = \
            later_sniper
        runtime.bigworld.connect = later_connect
        compatibility.fini()

        self.assertIs(
            later_account_init,
            runtime.account_module.PlayerAccount.__dict__['__init__'])
        self.assertIs(
            later_avatar_enter,
            runtime.avatar_module.PlayerAvatar.__dict__['onEnterWorld'])
        self.assertIs(
            later_avatar_leave,
            runtime.avatar_module.PlayerAvatar.__dict__['onLeaveWorld'])
        self.assertIs(
            later_arcade,
            runtime.control_modes.ArcadeControlMode.__dict__[
                'handleKeyEvent'])
        self.assertIs(
            later_sniper,
            runtime.control_modes.SniperControlMode.__dict__[
                'handleKeyEvent'])
        self.assertIs(later_connect, runtime.bigworld.connect)


class _LANSocket(object):
    def __init__(self):
        self.payloads = []
        self.closed = False

    def sendall(self, payload):
        self.payloads.append(payload)

    def close(self):
        self.closed = True


class _LANBigWorld(object):
    def __init__(self):
        self.callbacks = []
        self.cancelled = []

    def callback(self, delay, function):
        self.callbacks.append((delay, function))
        return len(self.callbacks)

    def cancelCallback(self, callback_id):
        self.cancelled.append(callback_id)


class LANClientTests(unittest.TestCase):
    def _client(self):
        module = _load_port_source('lan_client')
        events = []
        bigworld = _LANBigWorld()
        client = module.LANClient(
            '127.0.0.1', 28782, 'Player', 'ussr:MS-1', 100,
            on_event=lambda kind, message: events.append((kind, message)),
            bigworld=bigworld, effective_params=effective_params())
        return module, client, events, bigworld

    @staticmethod
    def _activate_outbound(client):
        client.sock = _LANSocket()
        client.running = True
        client.connected = True
        client._stopping = False
        with client._outbound_lock:
            client._outbound_accepting = True

    def test_start_worker_connects_and_sends_protocol_hello(self):
        module = _load_port_source('lan_client')
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.settimeout(2.0)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        connection = None
        client = module.LANClient(
            '127.0.0.1', listener.getsockname()[1], 'Loopback',
            'china:Ch01_Type59', 1300, bigworld=_LANBigWorld(),
            effective_params=effective_params())
        try:
            self.assertTrue(client.start())
            connection, unused_address = listener.accept()
            connection.settimeout(2.0)
            payload = b''
            while b'\n' not in payload:
                payload += connection.recv(4096)
            hello = json.loads(payload.split(b'\n', 1)[0].decode('utf-8'))

            self.assertEqual('hello', hello['type'])
            self.assertEqual(module.PROTOCOL_VERSION, hello['protocol'])
            self.assertEqual(module.CLIENT_BUILD, hello['client_build'])
            self.assertEqual('Loopback', hello['name'])
            self.assertEqual('china:Ch01_Type59', hello['vehicle'])
            self.assertEqual(1300, hello['max_health'])
        finally:
            if connection is not None:
                connection.close()
            client.stop()
            if client.thread is not None:
                client.thread.join(2.0)
            listener.close()

    def test_requested_team_is_carried_only_when_explicit(self):
        module = _load_port_source('lan_client')
        automatic = module.LANClient(
            '127.0.0.1', 28782, 'Auto', 'ussr:R11_MS-1',
            effective_params=effective_params())
        selected = module.LANClient(
            '127.0.0.1', 28782, 'TeamTwo', 'ussr:R11_MS-1',
            requested_team=2, effective_params=effective_params())

        self.assertNotIn('requested_team', automatic._hello_payload())
        self.assertEqual(2, selected._hello_payload()['requested_team'])

    def test_visible_client_cannot_send_projected_bot_state(self):
        module, client, unused_events, unused_bigworld = self._client()
        client.ready = True
        client.phase = 'battle'
        client.player_id = 1
        client.bot_authority_id = module.WORKER_AUTHORITY_ID
        client.round_id = 3
        client._send = mock.Mock(return_value=True)
        bots = [{'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
                 'yaw': 0.0, 'health': 1000, 'alive': True,
                 'fire_seq': 0}]
        original = module.project_bot_state
        module.project_bot_state = mock.Mock(
            side_effect=AssertionError('second projection'))
        try:
            self.assertFalse(client.send_projected_bot_state(bots))
        finally:
            module.project_bot_state = original

        client._send.assert_not_called()

    def test_visible_client_cannot_send_bot_ram(self):
        module, client, unused_events, unused_bigworld = self._client()
        client.ready = True
        client.phase = 'battle'
        client.player_id = 1
        client.bot_authority_id = module.WORKER_AUTHORITY_ID
        client.round_id = 3
        client._send = mock.Mock(return_value=True)

        self.assertFalse(client.send_bot_ram(
            11, 'human', 2, 4, 20, 40, 2, 9))

        client._send.assert_not_called()

    def test_welcome_roster_and_server_validated_start_request(self):
        module, client, events, _ = self._client()
        self._activate_outbound(client)
        client._handle_message({
            'type': 'welcome',
            'protocol': module.PROTOCOL_VERSION,
            'client_build': module.CLIENT_BUILD,
            'capabilities': list(module.CLIENT_CAPABILITIES),
            'server_capabilities': [
                module.DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
                module.RICOCHET_CONTINUATION_CAPABILITY,
                module.PROJECTILE_HIT_VEHICLE_CAPABILITY,
                module.RAM_CONTACT_LEDGER_CAPABILITY,
                module.HUMAN_RAM_TIMELINE_CAPABILITY,
                module.PLAYER_FIRE_INTENT_CAPABILITY,
                module.PLAYER_ENVIRONMENT_CAPABILITY,
                module.EFFECTIVE_PARAMS_CAPABILITY,
                module.RANDOM_MAP_CAPABILITY,
                module.TEAM_SELECTION_CAPABILITY],
            'authority_epoch': 1,
            'player_id': 7,
            'host_player_id': 7,
            'name': 'Player',
            'vehicle': 'ussr:MS-1',
            'max_health': 100,
            'team': 1,
            'team_sizes': {'1': 2, '2': 5},
            'slot': 0,
            'map': '01_karelia',
            'map_pool': ['01_karelia', '04_himmelsdorf'],
            'phase': 'waiting',
            'round_id': 3,
            'state_revision': 4,
            'spawn': {'x': 0, 'y': 0, 'z': 0, 'yaw': 0},
            'effective_params': effective_params(),
        })
        client._handle_message({
            'type': 'roster',
            'protocol': module.PROTOCOL_VERSION,
            'phase': 'waiting',
            'round_id': 3,
            'state_revision': 4,
            'map': '01_karelia',
            'map_pool': ['01_karelia', '04_himmelsdorf'],
            'host_player_id': 7,
            'authority_epoch': 1,
            'players': [wire_player(7), wire_player(8)],
        })

        self.assertTrue(client.ready)
        self.assertEqual(7, client.player_id)
        self.assertTrue(client.is_room_host())
        self.assertEqual(2, len(client.roster))
        self.assertEqual({1: 2, 2: 5}, client.team_sizes)
        self.assertFalse(client.request_start('99_missing'))
        self.assertTrue(client.request_start('04_himmelsdorf'))
        queued = client._outbound_queue[-1][1]
        self.assertEqual('start_battle', queued['type'])
        self.assertEqual('04_himmelsdorf', queued['map'])
        self.assertEqual(['welcome', 'roster'],
                         [item[0] for item in events])

        self.assertTrue(client.select_team(2))
        self.assertEqual(
            {'type': 'select_team', 'team': 2},
            client._outbound_queue[-1][1])

    def test_guest_cannot_request_start_or_select_map(self):
        _, client, _, _ = self._client()
        client.ready = True
        client.connected = True
        client.phase = 'waiting'
        client.player_id = 8
        client.host_player_id = 7
        client.round_id = 3
        client.map_pool = ['01_karelia', '04_himmelsdorf']
        client.sock = _LANSocket()

        self.assertFalse(client.is_room_host())
        self.assertFalse(client.request_start('04_himmelsdorf'))
        self.assertEqual([], client.sock.payloads)

    def test_room_host_can_request_random_but_unknown_maps_stay_closed(self):
        module, client, _, _ = self._client()
        self._activate_outbound(client)
        client.ready = True
        client.phase = 'waiting'
        client.player_id = 7
        client.host_player_id = 7
        client.round_id = 3
        client.map_pool = ['01_karelia', '04_himmelsdorf']
        client.server_capabilities = [module.RANDOM_MAP_CAPABILITY]

        self.assertFalse(client.request_start('99_missing'))
        self.assertTrue(client.request_start(module.RANDOM_MAP_OPTION))
        self.assertEqual(
            module.RANDOM_MAP_OPTION, client._outbound_queue[-1][1]['map'])

    def test_older_same_round_roster_cannot_roll_back_room_host(self):
        _, client, events, _ = self._client()
        self._activate_outbound(client)
        client.ready = True
        client.player_id = 2
        client.round_id = 3
        client.state_revision = 4
        client.phase = 'waiting'
        client.map_pool = ['01_karelia']

        client._handle_message({
            'type': 'roster', 'protocol': 5, 'round_id': 3,
            'state_revision': 6, 'phase': 'waiting',
            'map': '01_karelia', 'host_player_id': 2,
            'players': [wire_player(2, name='NewHost')]})
        client._handle_message({
            'type': 'roster', 'protocol': 5, 'round_id': 3,
            'state_revision': 5, 'phase': 'waiting',
            'map': '01_karelia', 'host_player_id': 1,
            'players': [wire_player(1, name='OldHost'),
                        wire_player(2, name='NewHost')]})

        self.assertEqual(6, client.state_revision)
        self.assertEqual(2, client.host_player_id)
        self.assertEqual([2], [value['id'] for value in client.roster])
        self.assertEqual(['roster'], [value[0] for value in events])
        self.assertTrue(client.request_start('01_karelia'))
        queued = client._outbound_queue[-1][1]
        self.assertEqual('start_battle', queued['type'])
        self.assertEqual('01_karelia', queued['map'])

    def test_overtaken_battle_start_keeps_newer_roster_and_fires_once(self):
        _, client, events, _ = self._client()
        client.running = True
        client.ready = True
        client.player_id = 2
        client.round_id = 3
        client.state_revision = 4
        client.phase = 'waiting'

        client._handle_message({
            'type': 'roster', 'protocol': 5, 'round_id': 3,
            'state_revision': 6, 'phase': 'battle',
            'map': '01_karelia', 'host_player_id': 2,
            'bot_authority_id': -1,
            'players': [wire_player(2, name='NewHost')]})
        stale_start = {
            'type': 'battle_start', 'protocol': 5, 'round_id': 3,
            'state_revision': 5, 'phase': 'loading',
            'map': '01_karelia',
            'host_player_id': 1,
            'bot_authority_id': 1,
            'players': [wire_player(1, name='OldHost'),
                        wire_player(2, name='NewHost')],
            'bots': [],
        }
        client._handle_message(stale_start)
        client._handle_message(stale_start)

        self.assertEqual(6, client.state_revision)
        self.assertEqual(2, client.host_player_id)
        self.assertEqual([2], [value['id'] for value in client.roster])
        self.assertEqual(['roster', 'battle_start'],
                         [value[0] for value in events])
        delivered = events[-1][1]
        self.assertEqual(6, delivered['state_revision'])
        self.assertEqual(2, delivered['host_player_id'])
        self.assertEqual(-1, delivered['bot_authority_id'])
        self.assertEqual(-1, client.bot_authority_id)
        self.assertEqual([2], [value['id']
                              for value in delivered['players']])

    def test_poll_coalesces_snapshots_without_crossing_state_barriers(self):
        _, client, events, bigworld = self._client()
        client.running = True
        client.round_id = 4
        client._queue_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 4, 'server_tick': 0,
            'bot_state_revision': 0,
            'bot_authority_id': -1, 'bot_manifest': [],
            'players': [], 'bots': []})
        client._queue_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 4, 'server_tick': 1,
            'bot_state_revision': 1,
            'bot_authority_id': -1, 'bot_manifest': [],
            'players': [], 'bots': []})
        client._queue_message({
            'type': 'roster', 'protocol': 5,
            'round_id': 4, 'state_revision': 2, 'phase': 'battle',
            'map': '01_karelia', 'host_player_id': 7,
            'bot_authority_id': -1,
            'players': [wire_player(7)]})
        client._queue_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 4, 'server_tick': 2,
            'bot_state_revision': 2,
            'bot_authority_id': -1, 'bot_manifest': [],
            'players': [], 'bots': []})
        client._queue_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 4, 'server_tick': 3,
            'bot_state_revision': 3,
            'bot_authority_id': -1, 'bot_manifest': [],
            'players': [], 'bots': []})
        client._poll()

        self.assertEqual(3, client.last_snapshot['server_tick'])
        self.assertEqual(['snapshot', 'roster', 'snapshot'],
                         [item[0] for item in events])
        self.assertIsNotNone(client._poll_callback)
        callback_id = client._poll_callback
        client.stop()
        self.assertEqual([callback_id], bigworld.cancelled)
        self.assertIsNone(client._poll_callback)

    def test_protocol_mismatch_stops_without_raising(self):
        module, client, _, _ = self._client()
        client.running = True
        client._handle_message({
            'type': 'welcome', 'protocol': 'invalid',
            'client_build': module.CLIENT_BUILD, 'player_id': 7,
            'host_player_id': 7, 'name': 'Player',
            'vehicle': 'ussr:MS-1', 'max_health': 100,
            'team': 1, 'slot': 0, 'round_id': 1,
            'state_revision': 1,
            'phase': 'waiting', 'map': '01_karelia',
            'spawn': {'x': 0, 'y': 0, 'z': 0}})
        self.assertFalse(client.running)
        self.assertEqual('protocol mismatch', client.last_error)

    def test_welcome_negotiates_protocol_and_build_labels_by_capability(self):
        module, client, _, _ = self._client()
        client.running = True
        client._handle_message({
            'type': 'welcome', 'protocol': module.PROTOCOL_VERSION + 1,
            'client_build': 'launcher-local-server',
            'capabilities': list(module.CLIENT_CAPABILITIES),
            'server_capabilities': [
                module.DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
                module.RICOCHET_CONTINUATION_CAPABILITY,
                module.PROJECTILE_HIT_VEHICLE_CAPABILITY,
                module.RAM_CONTACT_LEDGER_CAPABILITY,
                module.HUMAN_RAM_TIMELINE_CAPABILITY,
                module.PLAYER_FIRE_INTENT_CAPABILITY,
                module.PLAYER_ENVIRONMENT_CAPABILITY,
                module.EFFECTIVE_PARAMS_CAPABILITY,
            ],
            'authority_epoch': 1,
            'player_id': 7, 'host_player_id': 7, 'name': 'Player',
            'vehicle': 'ussr:MS-1', 'max_health': 100,
            'team': 1, 'slot': 0, 'round_id': 1,
            'state_revision': 1,
            'phase': 'waiting', 'map': '01_karelia',
            'spawn': {'x': 0, 'y': 0, 'z': 0, 'yaw': 0},
            'effective_params': effective_params(),
        })
        self.assertTrue(client.running)
        self.assertTrue(client.ready)
        self.assertTrue(client._schema_negotiated)
        self.assertIsNone(client.last_error)

    def test_round_barriers_drop_stale_snapshot_and_clear_terminal_cache(self):
        _, client, events, _ = self._client()
        client.round_id = 5
        client._handle_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 4, 'server_tick': 99,
            'players': [], 'bots': []})
        client._handle_message({
            'type': 'events', 'protocol': 5,
            'round_id': 4, 'server_tick': 99, 'events': [
                {'kind': 'battle_result'}]})
        current = {'type': 'snapshot', 'protocol': 5,
                   'round_id': 5, 'server_tick': 3,
                   'bot_state_revision': 3,
                   'bot_authority_id': -1, 'bot_manifest': [],
                   'players': [], 'bots': []}
        client._handle_message(current)

        self.assertIs(current, client.last_snapshot)
        self.assertEqual(['snapshot'], [value[0] for value in events])

        client._fire_seq = 9
        client._handle_message({
            'type': 'roster', 'protocol': 5,
            'round_id': 6, 'state_revision': 8, 'phase': 'waiting',
            'map': '01_karelia', 'host_player_id': 7,
            'players': [wire_player(7)]})
        self.assertEqual(6, client.round_id)
        self.assertIsNone(client.last_snapshot)
        self.assertEqual(0, client._fire_seq)

    def test_pending_overflow_preserves_state_transition_barrier(self):
        module, client, _, _ = self._client()
        for tick in range(module.MAX_PENDING_MESSAGES):
            client._queue_message({
                'type': 'snapshot', 'round_id': 1, 'server_tick': tick})

        client._queue_message({
            'type': 'roster', 'round_id': 2, 'phase': 'waiting',
            'host_player_id': 7, 'players': [{'id': 7}]})

        self.assertEqual(module.MAX_PENDING_MESSAGES, len(client._pending))
        self.assertEqual('roster', client._pending[-1]['type'])

    def test_malformed_required_server_messages_fail_closed(self):
        module, client, _, _ = self._client()
        client.running = True

        client._handle_message({
            'type': 'welcome', 'protocol': 5,
            'client_build': module.CLIENT_BUILD, 'player_id': 'bad',
            'capabilities': list(module.CLIENT_CAPABILITIES),
            'server_capabilities': [
                module.DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
                module.RICOCHET_CONTINUATION_CAPABILITY,
                module.RAM_CONTACT_LEDGER_CAPABILITY,
                module.HUMAN_RAM_TIMELINE_CAPABILITY,
                module.PLAYER_FIRE_INTENT_CAPABILITY,
                module.PLAYER_ENVIRONMENT_CAPABILITY,
                module.EFFECTIVE_PARAMS_CAPABILITY],
            'authority_epoch': 1,
            'host_player_id': 7, 'name': 'Player',
            'vehicle': 'ussr:MS-1', 'max_health': 100,
            'team': 1, 'slot': 0, 'round_id': 1,
            'state_revision': 1,
            'phase': 'waiting', 'map': '01_karelia',
            'spawn': {'x': 0, 'y': 0, 'z': 0},
            'effective_params': effective_params()})

        self.assertFalse(client.running)
        self.assertFalse(client.ready)
        self.assertEqual('invalid welcome message', client.last_error)

    def test_missing_welcome_host_fails_closed(self):
        module, client, _, _ = self._client()
        client.running = True

        client._handle_message({
            'type': 'welcome', 'protocol': 5,
            'client_build': module.CLIENT_BUILD, 'player_id': 7,
            'capabilities': list(module.CLIENT_CAPABILITIES),
            'server_capabilities': [
                module.DESTRUCTIBLE_CATALOG_V5_CAPABILITY,
                module.RICOCHET_CONTINUATION_CAPABILITY,
                module.RAM_CONTACT_LEDGER_CAPABILITY,
                module.HUMAN_RAM_TIMELINE_CAPABILITY,
                module.PLAYER_FIRE_INTENT_CAPABILITY,
                module.PLAYER_ENVIRONMENT_CAPABILITY,
                module.EFFECTIVE_PARAMS_CAPABILITY],
            'authority_epoch': 1,
            'name': 'Player', 'vehicle': 'ussr:MS-1',
            'max_health': 100, 'team': 1, 'slot': 0, 'round_id': 1,
            'state_revision': 1,
            'phase': 'waiting', 'map': '01_karelia',
            'spawn': {'x': 0, 'y': 0, 'z': 0},
            'effective_params': effective_params()})

        self.assertFalse(client.running)
        self.assertFalse(client.ready)
        self.assertEqual('invalid welcome message', client.last_error)

    def test_malformed_roster_host_fails_closed(self):
        _, client, _, _ = self._client()
        client.running = True
        client.round_id = 3

        client._handle_message({
            'type': 'roster', 'protocol': 5, 'round_id': 3,
            'state_revision': 4,
            'phase': 'waiting', 'map': '01_karelia',
            'host_player_id': 'not-an-id',
            'players': [wire_player(7)]})

        self.assertFalse(client.running)
        self.assertEqual('invalid roster message', client.last_error)

    def test_battle_start_host_must_be_in_roster(self):
        _, client, _, _ = self._client()
        client.running = True
        client.ready = True
        client.player_id = 7
        client.round_id = 3

        client._handle_message({
            'type': 'battle_start', 'protocol': 5, 'round_id': 3,
            'state_revision': 4, 'phase': 'loading',
            'map': '01_karelia', 'host_player_id': 8,
            'bot_authority_id': -1,
            'players': [wire_player(7)]})

        self.assertFalse(client.running)
        self.assertFalse(client.ready)
        self.assertEqual('invalid battle_start message', client.last_error)

    def test_malformed_current_round_snapshot_fails_closed(self):
        _, client, _, _ = self._client()
        client.running = True
        client.round_id = 3

        client._handle_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 3, 'server_tick': 4,
            'players': 'not-a-list', 'bots': []})

        self.assertFalse(client.running)
        self.assertEqual('invalid snapshot message', client.last_error)

    def test_snapshot_requires_monotonic_bot_state_revision(self):
        _, client, _, _ = self._client()
        client.running = True
        client.round_id = 3

        client._handle_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 3, 'server_tick': 4,
            'players': [], 'bots': []})

        self.assertFalse(client.running)
        self.assertEqual('invalid snapshot message', client.last_error)

        _, client, _, _ = self._client()
        client.running = True
        client.round_id = 3
        client._handle_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 3, 'server_tick': 4,
            'bot_state_revision': 5,
            'bot_authority_id': -1, 'bot_manifest': [],
            'players': [], 'bots': []})
        client._handle_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 3, 'server_tick': 5,
            'bot_state_revision': 4,
            'bot_authority_id': -1, 'bot_manifest': [],
            'players': [], 'bots': []})

        self.assertFalse(client.running)
        self.assertEqual('invalid snapshot message', client.last_error)

    def test_snapshot_bot_pose_timing_is_atomic_and_monotonic(self):
        _, client, _, _ = self._client()
        client.running = True
        client.round_id = 3
        client._handle_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 3, 'server_tick': 4,
            'bot_state_revision': 5,
            'motion_time_us': 120000, 'bot_state_time_us': 90000,
            'bot_authority_id': -1, 'bot_manifest': [],
            'players': [], 'bots': []})
        self.assertTrue(client.running)

        client._handle_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 3, 'server_tick': 5,
            'bot_state_revision': 5,
            'motion_time_us': 150000, 'bot_state_time_us': 100000,
            'bot_authority_id': -1, 'bot_manifest': [],
            'players': [], 'bots': []})
        self.assertFalse(client.running)
        self.assertEqual('invalid snapshot message', client.last_error)

        _, client, _, _ = self._client()
        client.running = True
        client.round_id = 3
        client._handle_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 3, 'server_tick': 4,
            'bot_state_revision': 5,
            'motion_time_us': 120000, 'bot_state_time_us': 90000,
            'bot_authority_id': -1, 'bot_manifest': [],
            'players': [], 'bots': []})
        client._handle_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 3, 'server_tick': 5,
            'bot_state_revision': 6,
            'bot_authority_id': -1, 'bot_manifest': [],
            'players': [], 'bots': []})
        self.assertFalse(client.running)
        self.assertEqual('invalid snapshot message', client.last_error)

        _, client, _, _ = self._client()
        client.running = True
        client.round_id = 3
        client._handle_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 3, 'server_tick': 4,
            'bot_state_revision': 5,
            'motion_time_us': 120000, 'bot_state_time_us': 90000,
            'players': [], 'bots': []})
        client._handle_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 3, 'server_tick': 5,
            'bot_state_revision': 6,
            'motion_time_us': 150000, 'bot_state_time_us': 90000,
            'players': [], 'bots': []})
        self.assertFalse(client.running)
        self.assertEqual('invalid snapshot message', client.last_error)

        _, client, _, _ = self._client()
        client.running = True
        client.round_id = 3
        client._handle_message({
            'type': 'snapshot', 'protocol': 5,
            'round_id': 3, 'server_tick': 4,
            'bot_state_revision': 5,
            'motion_time_us': 120000,
            'players': [], 'bots': []})
        self.assertFalse(client.running)
        self.assertEqual('invalid snapshot message', client.last_error)

    def test_state_message_without_protocol_fails_closed(self):
        _, client, _, _ = self._client()
        client.running = True
        client.round_id = 3

        client._handle_message({
            'type': 'snapshot', 'round_id': 3, 'server_tick': 4,
            'players': [], 'bots': []})

        self.assertFalse(client.running)
        self.assertEqual('protocol mismatch', client.last_error)

    def test_malformed_server_order_batch_fails_closed(self):
        _, client, _, _ = self._client()
        client.running = True
        client.round_id = 3

        client._handle_message({
            'type': 'snapshot', 'protocol': 5, 'round_id': 3,
            'server_tick': 4, 'players': [], 'bots': [],
            'bot_order_revision': 2, 'bot_orders': {'id': 11}})

        self.assertFalse(client.running)
        self.assertEqual('invalid snapshot message', client.last_error)

    def test_stale_start_denied_does_not_cross_round_barrier(self):
        _, client, events, _ = self._client()
        client.running = True
        client.round_id = 4

        client._handle_message({
            'type': 'start_denied', 'protocol': 5,
            'round_id': 3, 'code': 'already_started'})

        self.assertEqual([], events)


class BootstrapContractTests(unittest.TestCase):
    def test_entry_delegates_init_and_fini(self):
        entry = (PORT_ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' /
                 'mods' / 'mod_offline_lan_0922.py')
        bootstrap = types.SimpleNamespace(init=mock.Mock(), fini=mock.Mock())
        package = types.ModuleType('gui.mods.offline_lan_0922')
        package.bootstrap = bootstrap
        modules = {
            'gui': types.ModuleType('gui'),
            'gui.mods': types.ModuleType('gui.mods'),
            'gui.mods.offline_lan_0922': package,
        }
        with mock.patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location('entry0922', entry)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.init()
            module.fini()
        bootstrap.init.assert_called_once_with()
        bootstrap.fini.assert_called_once_with()

    def test_bootstrap_schedules_once_starts_lan_session_and_stops_cleanly(self):
        bootstrap_path = (
            PORT_ROOT / 'src' / 'res' / 'scripts' / 'client' / 'gui' /
            'mods' / 'offline_lan_0922' / 'bootstrap.py')
        bigworld = _BigWorld()
        package = types.ModuleType('gui.mods.offline_lan_0922')
        package.PORT_VERSION = '0.6.1'
        package.TARGET_CLIENT_VERSION = '0.9.22.0.1'
        package.TARGET_CLIENT_BUILD = '1513'
        package.__path__ = []
        config = types.ModuleType('gui.mods.offline_lan_0922.config')
        config.PLAYER_MODE = 'player'
        config.SIMULATION_WORKER_MODE = 'simulation_worker'
        config.CLIENT_MODE_ENV = 'OFFLINE_LAN_0922_CLIENT_MODE'
        config.client_mode = mock.Mock(return_value=config.PLAYER_MODE)
        config.load = mock.Mock(return_value={
            'enabled': True,
            'vehicle': 'ussr:R11_MS-1',
            'serverHost': '127.0.0.1',
            'serverPort': 28782,
            'playerName': 'OfflinePlayer',
            'maxHealth': 880,
            'startupTimeoutSeconds': 30.0,
        })
        vehicle_blacklist = types.ModuleType(
            'gui.mods.offline_lan_0922.vehicle_blacklist')
        vehicle_blacklist.is_unusable = mock.Mock(return_value=False)
        vehicle_blacklist.missing_resources = mock.Mock(return_value=())
        vehicle_configuration = types.ModuleType(
            'gui.mods.offline_lan_0922.vehicle_configuration')
        vehicle_configuration.install_top_modules = mock.Mock()
        vehicle_configuration.is_standard_battle_vehicle = mock.Mock(
            return_value=True)
        vehicle_configuration.top_component = mock.Mock()
        instance_guard = types.ModuleType(
            'gui.mods.offline_lan_0922.instance_guard')
        instance_guard.release_if_requested = mock.Mock(return_value=False)
        session = types.SimpleNamespace(
            install=mock.Mock(return_value=True), stop=mock.Mock())
        lan_session = types.ModuleType(
            'gui.mods.offline_lan_0922.lan_session')
        lan_session.LANSession = mock.Mock(return_value=session)
        compatibility = types.SimpleNamespace(
            connect=mock.Mock(), is_ready=mock.Mock(return_value=True),
            fini=mock.Mock())
        lobby_entry = mock.Mock()
        lobby_entry.attach_mock(session.install, 'install')
        lobby_entry.attach_mock(compatibility.connect, 'connect')
        compatibility_module = types.ModuleType(
            'gui.mods.offline_lan_0922.compat')
        compatibility_module.g_compatibility = compatibility
        account_state = types.SimpleNamespace()
        state_module = types.ModuleType(
            'gui.mods.offline_lan_0922.account_rpc.state')
        state_module.AccountState = mock.Mock(return_value=account_state)
        postbattle = types.SimpleNamespace(account_key='test-account')
        postbattle_module = types.ModuleType(
            'gui.mods.offline_lan_0922.account_rpc.postbattle_store')
        postbattle_module.PostBattleStore = mock.Mock(
            return_value=postbattle)
        class EventBus(object):
            def __init__(self):
                self.listeners = {}

            def addListener(self, event_type, handler):
                self.listeners.setdefault(event_type, []).append(handler)

            def removeListener(self, event_type, handler):
                self.listeners[event_type].remove(handler)

            def fire(self, event_type):
                for handler in list(self.listeners.get(event_type, ())):
                    handler(object())

        event_bus = EventBus()
        lobby_loaded = 'lobby_view_loaded'
        gui_shared = types.ModuleType('gui.shared')
        gui_shared.events = types.SimpleNamespace(
            GUICommonEvent=types.SimpleNamespace(
                LOBBY_VIEW_LOADED=lobby_loaded))
        gui_shared.g_eventBus = event_bus

        app_loader = types.ModuleType('gui.app_loader')
        lobby = types.SimpleNamespace(initialized=False)
        loader = types.SimpleNamespace(
            getDefLobbyApp=mock.Mock(return_value=lobby),
            getSpaceID=mock.Mock(return_value=3))
        app_loader.g_appLoader = loader
        app_loader_settings = types.ModuleType('gui.app_loader.settings')
        app_loader_settings.GUI_GLOBAL_SPACE_ID = types.SimpleNamespace(
            LOGIN=3, LOBBY=4)

        hangar_vehicle = types.SimpleNamespace(model=None)
        hangar_space = types.SimpleNamespace(
            inited=True, spaceInited=False,
            getVehicleEntity=mock.Mock(return_value=hangar_vehicle))
        hangar_module = types.ModuleType('gui.shared.utils.HangarSpace')
        hangar_module.g_hangarSpace = hangar_space
        current_vehicle = types.SimpleNamespace(
            isPresent=mock.Mock(return_value=True))
        current_vehicle_module = types.ModuleType('CurrentVehicle')
        current_vehicle_module.g_currentVehicle = current_vehicle
        announcement_ui = mock.Mock()
        intro_skip = mock.Mock()
        lobby_ui_module = types.ModuleType(
            'gui.mods.offline_lan_0922.lobby_ui')
        lobby_ui_module.ServerAnnouncementUI = mock.Mock(
            return_value=announcement_ui)
        lobby_ui_module.IntroVideoSkip = mock.Mock(return_value=intro_skip)
        worker_presentation = mock.Mock()
        worker_presentation.activate.return_value = True
        worker_presentation_module = types.ModuleType(
            'gui.mods.offline_lan_0922.worker_presentation')
        worker_presentation_module.WorkerPresentation = mock.Mock(
            return_value=worker_presentation)
        modules = {
            'BigWorld': bigworld,
            'gui': types.ModuleType('gui'),
            'gui.shared': gui_shared,
            'gui.shared.utils': types.ModuleType('gui.shared.utils'),
            'gui.shared.utils.HangarSpace': hangar_module,
            'gui.mods': types.ModuleType('gui.mods'),
            'gui.mods.offline_lan_0922': package,
            'gui.mods.offline_lan_0922.compat': compatibility_module,
            'gui.mods.offline_lan_0922.config': config,
            'gui.mods.offline_lan_0922.instance_guard': instance_guard,
            'gui.mods.offline_lan_0922.vehicle_blacklist': vehicle_blacklist,
            'gui.mods.offline_lan_0922.vehicle_configuration':
                vehicle_configuration,
            'gui.mods.offline_lan_0922.account_rpc.state': state_module,
            'gui.mods.offline_lan_0922.account_rpc.postbattle_store':
                postbattle_module,
            'gui.mods.offline_lan_0922.lan_session': lan_session,
            'gui.mods.offline_lan_0922.lobby_ui': lobby_ui_module,
            'gui.mods.offline_lan_0922.worker_presentation':
                worker_presentation_module,
            'gui.app_loader': app_loader,
            'gui.app_loader.settings': app_loader_settings,
            'CurrentVehicle': current_vehicle_module,
        }
        package.config = config
        package.instance_guard = instance_guard
        with mock.patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location(
                'bootstrap0922', bootstrap_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module._selected_vehicle = lambda value: {
                'id': 1, 'compDescr': 12345}
            module._signal_worker_ready = mock.Mock(return_value=True)
            module._signal_player_ready = mock.Mock(return_value=True)
            module.init()
            module.init()
            instance_guard.release_if_requested.assert_called_once_with()
            self.assertEqual(
                [module._on_lobby_view_loaded],
                event_bus.listeners[lobby_loaded])
            self.assertEqual(1, len(bigworld._callbacks))
            bigworld.run_next()
            self.assertEqual(1, len(bigworld._callbacks))
            module._deadline = 1.0
            with mock.patch.object(module.time, 'time', return_value=1000.0):
                # Every native readiness condition except the public lobby
                # event is true.  The session must still not start, and the
                # stale deadline must not turn first-run EULA time into a
                # startup failure.
                lobby.initialized = True
                loader.getSpaceID.return_value = 4
                hangar_space.spaceInited = True
                hangar_vehicle.model = object()
                bigworld.run_next()
                lan_session.LANSession.assert_not_called()
                self.assertEqual(1, len(bigworld._callbacks))
                module._deadline = 0.0
                loader.getSpaceID.return_value = 3
                hangar_space.spaceInited = False
                hangar_vehicle.model = None
                event_bus.fire(lobby_loaded)
                self.assertEqual(0.0, module._deadline)
                # First stable LOGIN observation deliberately waits one more
                # engine tick so LoginState's entity clear cannot race the
                # client-only Account construction.
                bigworld.run_next()
                self.assertEqual(0.0, module._deadline)
                compatibility.connect.assert_not_called()
                bigworld.run_next()
                self.assertEqual(0.0, module._deadline)
                lan_session.LANSession.assert_called_once()
                session.install.assert_called_once_with()
                compatibility.connect.assert_called_once()
                self.assertEqual(
                    [mock.call.install(), mock.call.connect(
                        show_lobby=True,
                        account_context={'selected_vehicle': {
                            'id': 1, 'compDescr': 12345},
                            'garage_store': None,
                            'account_state': account_state})],
                    lobby_entry.mock_calls)
                bigworld.run_next()
                self.assertEqual(1030.0, module._deadline)
                self.assertEqual(1, lan_session.LANSession.call_count)
                self.assertEqual(1, len(bigworld._callbacks))
                loader.getSpaceID.return_value = 4
                bigworld.run_next()
                self.assertEqual(1, lan_session.LANSession.call_count)
                self.assertEqual(1, len(bigworld._callbacks))
                hangar_space.spaceInited = True
                bigworld.run_next()
                self.assertEqual(1, lan_session.LANSession.call_count)
                self.assertEqual(1, len(bigworld._callbacks))
                hangar_vehicle.model = object()
                bigworld.run_next()
            module.fini()
            self.assertFalse(module._started)
            module._signal_player_ready.assert_called_once_with()

            # A lobby-stage timeout must fully undo the connection adapter
            # and listener, then allow a clean init.  Keep the hangar not
            # ready so no second LANSession can be constructed.
            hangar_space.spaceInited = False
            loader.getSpaceID.return_value = 3
            module.init()
            self.assertEqual(1, len(bigworld._callbacks))
            self.assertEqual(
                [module._on_lobby_view_loaded],
                event_bus.listeners[lobby_loaded])
            bigworld.run_next()
            bigworld.run_next()
            event_bus.fire(lobby_loaded)
            loader.getSpaceID.return_value = 4
            clock = [1000.0]
            with mock.patch.object(
                    module.time, 'time', side_effect=lambda: clock[0]):
                bigworld.run_next()
                self.assertEqual(1030.0, module._deadline)
                self.assertEqual(1, len(bigworld._callbacks))
                clock[0] = 1031.0
                bigworld.run_next()
            self.assertFalse(module._started)
            self.assertIsNone(module._callback_id)
            self.assertEqual([], bigworld._callbacks)
            self.assertEqual([], event_bus.listeners[lobby_loaded])

            # Fini while the initial callback is still pending cancels it and
            # removes the one reinstalled listener.
            module.init()
            self.assertEqual(1, len(bigworld._callbacks))
            self.assertEqual(1, len(event_bus.listeners[lobby_loaded]))
            module.fini()
            self.assertFalse(module._started)
            self.assertEqual([], bigworld._callbacks)

            # A process explicitly launched as the simulation worker must not
            # schedule any bootstrap work unless the owner-thread release was
            # proven successful.
            with mock.patch.dict(os.environ, {
                    config.CLIENT_MODE_ENV: config.SIMULATION_WORKER_MODE}):
                module.init()
            self.assertFalse(module._started)
            # Even a worker rejected before callbacks must bypass #1513's
            # compulsory first-run movie on its fresh preferences leaf.
            self.assertEqual(4, intro_skip.install.call_count)
            self.assertEqual(('quit',), bigworld.operations[-1])
            self.assertEqual([], bigworld._callbacks)
            self.assertEqual([], event_bus.listeners[lobby_loaded])

            # A proven worker release activates presentation isolation before
            # any deferred Account or lobby work is allowed to run.
            instance_guard.release_if_requested.return_value = True
            with mock.patch.dict(os.environ, {
                    config.CLIENT_MODE_ENV: config.SIMULATION_WORKER_MODE}):
                module.init()
            worker_presentation.activate.assert_called_once_with()
            # Presentation and guard release are not enough: the starter is
            # released only after native Hangar readiness and a LAN welcome.
            module._signal_worker_ready.assert_not_called()
            self.assertEqual(1, len(bigworld._callbacks))
            module.fini()
            worker_presentation.deactivate.assert_called_once_with(
                restore=False)

            # Presentation isolation failures terminate the worker without
            # restoring sound or exposing a login window.
            worker_presentation.reset_mock()
            worker_presentation.activate.side_effect = RuntimeError(
                'window isolation failed')
            with mock.patch.dict(os.environ, {
                    config.CLIENT_MODE_ENV: config.SIMULATION_WORKER_MODE}):
                module.init()
            self.assertFalse(module._started)
            self.assertIsNone(module._worker_presentation)
            worker_presentation.deactivate.assert_called_once_with(
                restore=False)
            module._signal_worker_ready.assert_not_called()
            self.assertEqual(('quit',), bigworld.operations[-1])
            self.assertEqual([], bigworld._callbacks)
        expected_session = mock.call(
            config.load.return_value,
            lobby_ready=module._native_lobby_is_ready,
            callback=bigworld.callback,
            cancel_callback=bigworld.cancelCallback,
            postbattle_store=postbattle)
        self.assertEqual(
            [expected_session, expected_session],
            lan_session.LANSession.call_args_list)
        self.assertEqual(2, session.install.call_count)
        self.assertEqual(2, announcement_ui.install.call_count)
        self.assertEqual(2, announcement_ui.uninstall.call_count)
        self.assertEqual(5, intro_skip.install.call_count)
        self.assertEqual(5, intro_skip.uninstall.call_count)
        self.assertEqual(
            [mock.call(show_login=False, restore_account=False,
                       release_join=True),
             mock.call(show_login=False, restore_account=False,
                       release_join=True)],
            session.stop.call_args_list)
        expected_connect = mock.call(
            show_lobby=True,
            account_context={'selected_vehicle': {
                'id': 1, 'compDescr': 12345},
                'garage_store': None,
                'account_state': account_state})
        self.assertEqual([expected_connect, expected_connect],
                         compatibility.connect.call_args_list)
        self.assertEqual(2, state_module.AccountState.call_count)
        self.assertEqual(5, compatibility.fini.call_count)
        self.assertEqual([], event_bus.listeners[lobby_loaded])


if __name__ == '__main__':
    unittest.main()
