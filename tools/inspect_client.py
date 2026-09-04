#!/usr/bin/env python3

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
import zipfile


KNOWN_MAGIC = {
    b'\xd1\xf2\r\n': 'CPython 2.6',
    b'\x03\xf3\r\n': 'CPython 2.7',
}

PROBE_MEMBERS = (
    'scripts/client/gui/mods/__init__.pyc',
    'scripts/client/Account.pyc',
    'scripts/client/Avatar.pyc',
    'scripts/client/Vehicle.pyc',
    'scripts/client/OfflineEntity.pyc',
    'scripts/client/vehicle_systems/model_assembler.pyc',
    'scripts/common/items/vehicles.pyc',
)

REQUIRED_SCRIPT_MEMBERS = (
    'scripts/entity_defs/OfflineEntity.def',
    'scripts/item_defs/vehicles/ussr/R11_MS-1.xml',
)

PINNED_ENTITY_DEFINITION_SHA256 = {
    'scripts/entity_defs/alias.xml':
        '20892cd23c5fe285927e599a197ba99436032207c5c071aa726febc42b59274d',
    'scripts/entity_defs/Avatar.def':
        'ddbfb9fb94b574ba5133ff272eab81ad798eea535e4a64d37865cff5b8b91831',
    'scripts/entity_defs/Vehicle.def':
        'e585c59235ebb2cfbb7857645878ed095360a8efe5df666c055e59a74e6a55c5',
    'scripts/entity_defs/interfaces/AvatarObserver.def':
        'fbf32f5462d959648f60757288a49a63cd194df0a77618a703d96d8096a456d8',
}

REQUIRED_RESOURCE_MEMBERS = {
    '01_karelia.pkg': (
        'spaces/01_karelia/space.settings',
    ),
    'vehicles_level_01.pkg': (
        'vehicles/russian/R11_MS-1/normal/lod0/Chassis.model',
        'vehicles/russian/R11_MS-1/normal/lod0/Hull.model',
        'vehicles/russian/R11_MS-1/normal/lod0/Turret_01.model',
        'vehicles/russian/R11_MS-1/normal/lod0/Gun_01.model',
    ),
}

TARGET_VERSION = '0.9.22.0.1'
TARGET_BUILD = '1513'
TARGET_WOTMOD_PATH = './mods/0.9.22.0.1'
TARGET_RESMODS_PATH = './res_mods/0.9.22.0.1'
PE_MACHINE_I386 = 0x014C
WG_FILTER_REQUIRED_METHODS = (
    'notifyInputKeysDown', 'setVehiclePhysics', 'getVehiclePhysics',
    'setTracksSpeed', 'syncGunAngles',
)
WG_FILTER_FORBIDDEN_POSE_METHODS = ('set', 'setPosition')


def _text(element):
    return ''.join(element.itertext()).strip()


def _read_pe_machine(path):
    with path.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError('WorldOfTanks.exe is not a PE executable')
        stream.seek(0x3C)
        pe_offset_raw = stream.read(4)
        if len(pe_offset_raw) != 4:
            raise ValueError('WorldOfTanks.exe has a truncated DOS header')
        pe_offset = int.from_bytes(pe_offset_raw, 'little')
        stream.seek(pe_offset)
        if stream.read(4) != b'PE\0\0':
            raise ValueError('WorldOfTanks.exe has no PE signature')
        machine_raw = stream.read(2)
        if len(machine_raw) != 2:
            raise ValueError('WorldOfTanks.exe has a truncated COFF header')
        return int.from_bytes(machine_raw, 'little')


def _inspect_wg_vehicle_filter(path):
    """Read the pinned native Python method table without loading the EXE."""
    payload = path.read_bytes()
    start = payload.find(b'PyWGVehicleFilter\0')
    if start < 0:
        raise ValueError(
            'WorldOfTanks.exe has no PyWGVehicleFilter method table')
    end = payload.find(b'PyWGTurretFilter\0', start)
    if end < 0 or end - start > 0x4000:
        raise ValueError(
            'WorldOfTanks.exe has an unbounded PyWGVehicleFilter method table')
    table = payload[start:end]
    missing = [name for name in WG_FILTER_REQUIRED_METHODS
               if (name.encode('ascii') + b'\0') not in table]
    present_forbidden = [name for name in WG_FILTER_FORBIDDEN_POSE_METHODS
                         if (name.encode('ascii') + b'\0') in table]
    if missing:
        raise ValueError('PyWGVehicleFilter methods are missing: %s' %
                         ', '.join(missing))
    if present_forbidden:
        raise ValueError('unexpected PyWGVehicleFilter pose methods: %s' %
                         ', '.join(present_forbidden))
    return {
        'tableOffset': '0x%x' % start,
        'requiredMethods': list(WG_FILTER_REQUIRED_METHODS),
        'absentPoseMethods': list(WG_FILTER_FORBIDDEN_POSE_METHODS),
    }


def inspect_client(client_root):
    client_root = Path(client_root).expanduser().resolve()
    version_path = client_root / 'version.xml'
    paths_path = client_root / 'paths.xml'
    archive_path = client_root / 'res' / 'packages' / 'scripts.pkg'
    executable_path = client_root / 'WorldOfTanks.exe'
    required = (version_path, paths_path, archive_path, executable_path)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError('missing required client files: %s' % ', '.join(missing))

    version_root = ET.parse(str(version_path)).getroot()
    version_text = _text(version_root.find('version'))
    version_match = re.search(r'v\.([^\s]+)\s+#(\d+)', version_text)
    if not version_match:
        raise ValueError('unrecognized version.xml value: %r' % version_text)

    paths_root = ET.parse(str(paths_path)).getroot()
    resource_paths = [_text(element) for element in paths_root.findall('.//Path')]
    mod_path = next((path for path in resource_paths if path.startswith('./mods/')), None)
    res_mod_path = next(
        (path for path in resource_paths if path.startswith('./res_mods/')), None)

    bytecode = {}
    entity_definition_hashes = {}
    missing_assets = []
    with zipfile.ZipFile(str(archive_path), 'r') as archive:
        names = set(archive.namelist())
        for member in PROBE_MEMBERS:
            if member not in names:
                bytecode[member] = {'present': False}
                continue
            magic = archive.read(member)[:4]
            bytecode[member] = {
                'present': True,
                'magicHex': magic.hex(),
                'runtime': KNOWN_MAGIC.get(magic, 'unknown'),
            }
        for member in REQUIRED_SCRIPT_MEMBERS:
            if member not in names:
                missing_assets.append('scripts.pkg:%s' % member)
        for member in PINNED_ENTITY_DEFINITION_SHA256:
            if member not in names:
                missing_assets.append('scripts.pkg:%s' % member)
                continue
            entity_definition_hashes[member] = hashlib.sha256(
                archive.read(member)).hexdigest()

    packages_root = client_root / 'res' / 'packages'
    for package_name, required_members in REQUIRED_RESOURCE_MEMBERS.items():
        package_path = packages_root / package_name
        if not package_path.is_file():
            missing_assets.append(package_name)
            continue
        with zipfile.ZipFile(str(package_path), 'r') as archive:
            names = set(archive.namelist())
            for member in required_members:
                if member not in names:
                    missing_assets.append('%s:%s' % (package_name, member))

    machine = _read_pe_machine(executable_path)
    native_vehicle_filter = _inspect_wg_vehicle_filter(executable_path)
    errors = []
    if version_match.group(1) != TARGET_VERSION:
        errors.append('version must be %s' % TARGET_VERSION)
    if version_match.group(2) != TARGET_BUILD:
        errors.append('build must be #%s' % TARGET_BUILD)
    if mod_path != TARGET_WOTMOD_PATH:
        errors.append('wotmod path must be %s' % TARGET_WOTMOD_PATH)
    if res_mod_path != TARGET_RESMODS_PATH:
        errors.append('res_mods path must be %s' % TARGET_RESMODS_PATH)
    if machine != PE_MACHINE_I386:
        errors.append('WorldOfTanks.exe must be x86 PE machine 0x014c')
    for member in PROBE_MEMBERS:
        probe = bytecode[member]
        if not probe.get('present'):
            errors.append('scripts.pkg member is missing: %s' % member)
        elif probe.get('runtime') != 'CPython 2.7':
            errors.append('scripts.pkg member is not CPython 2.7: %s' % member)
    if missing_assets:
        errors.append('battle assets are missing: %s' %
                      ', '.join(missing_assets))
    for member, expected_hash in PINNED_ENTITY_DEFINITION_SHA256.items():
        actual_hash = entity_definition_hashes.get(member)
        if actual_hash is not None and actual_hash != expected_hash:
            errors.append(
                'entity definition differs from pinned #1513: %s' % member)
    if errors:
        raise ValueError('; '.join(errors))

    return {
        'clientRoot': str(client_root),
        'version': version_match.group(1),
        'build': version_match.group(2),
        'wotmodPath': mod_path,
        'resModsPath': res_mod_path,
        'peMachine': '0x%04x' % machine,
        'architecture': 'x86',
        'nativeVehicleFilter': native_vehicle_filter,
        'battleProbeAssets': '01_karelia + ussr:R11_MS-1',
        'entityDefinitionSha256': entity_definition_hashes,
        'scriptsPackageBytes': archive_path.stat().st_size,
        'bytecode': bytecode,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Inspect a pinned World of Tanks 0.9.22 client read-only.')
    parser.add_argument('client_root')
    args = parser.parse_args(argv)
    try:
        report = inspect_client(args.client_root)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        parser.error(str(error))
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
