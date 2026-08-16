#!/usr/bin/env python2.7
from __future__ import print_function

import imp
import os
import py_compile
import shutil
import sys
import tempfile
import zipfile


ROOT = os.path.abspath(os.path.dirname(__file__))
SOURCE_ROOT = os.path.join(ROOT, 'src')
META_PATH = os.path.join(ROOT, 'meta.xml')
DIST_ROOT = os.path.join(ROOT, 'dist')
OUTPUT_NAME = 'org.peng.offline_2312_battle_0.7.2.wotmod'
PYTHON_27_MAGIC = '\x03\xf3\r\n'
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _require_python_27():
    if sys.version_info[:2] != (2, 7):
        raise RuntimeError('build_wotmod.py requires CPython 2.7')
    if imp.get_magic() != PYTHON_27_MAGIC:
        raise RuntimeError('unexpected CPython 2.7 bytecode magic')


def _source_files(root):
    paths = []
    for directory, unused_names, names in os.walk(root):
        for name in names:
            if name.endswith('.py'):
                paths.append(os.path.join(directory, name))
    return sorted(paths)


def _purge_generated_python(stage_root):
    for directory, names, files in os.walk(stage_root, topdown=False):
        for name in files:
            if name.endswith(('.pyc', '.pyo')):
                os.remove(os.path.join(directory, name))
        for name in names:
            if name == '__pycache__':
                shutil.rmtree(os.path.join(directory, name))


def _compile_sources(stage_root):
    for source_path in _source_files(stage_root):
        relative_path = os.path.relpath(source_path, stage_root)
        bytecode_path = source_path + 'c'
        py_compile.compile(
            source_path,
            cfile=bytecode_path,
            dfile=relative_path.replace(os.sep, '/'),
            doraise=True)
        with open(bytecode_path, 'r+b') as bytecode:
            bytecode.seek(4)
            bytecode.write('\x00\x00\x00\x00')
        os.remove(source_path)


def _archive_files(stage_root):
    result = []
    for directory, unused_names, names in os.walk(stage_root):
        for name in names:
            path = os.path.join(directory, name)
            result.append((os.path.relpath(path, stage_root).replace(
                os.sep, '/'), path))
    return sorted(result)


def _archive_directories(files):
    directories = set()
    for archive_name, unused_path in files:
        parts = archive_name.split('/')[:-1]
        for index in range(1, len(parts) + 1):
            directories.add('/'.join(parts[:index]) + '/')
    return sorted(directories)


def _write_member(archive, archive_name, data):
    info = zipfile.ZipInfo(archive_name, FIXED_TIMESTAMP)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 0
    info.external_attr = 16 if archive_name.endswith('/') else 32
    archive.writestr(info, data)


def build():
    _require_python_27()
    stage_root = tempfile.mkdtemp(prefix='offline_2312_battle-')
    try:
        shutil.copytree(SOURCE_ROOT, os.path.join(stage_root, 'source'))
        compiled_root = os.path.join(stage_root, 'source')
        _purge_generated_python(compiled_root)
        _compile_sources(compiled_root)
        shutil.copy2(META_PATH, os.path.join(compiled_root, 'meta.xml'))

        if not os.path.isdir(DIST_ROOT):
            os.makedirs(DIST_ROOT)
        output_path = os.path.join(DIST_ROOT, OUTPUT_NAME)
        files = _archive_files(compiled_root)
        with zipfile.ZipFile(
                output_path, 'w', zipfile.ZIP_STORED,
                allowZip64=True) as archive:
            for directory in _archive_directories(files):
                _write_member(archive, directory, '')
            for archive_name, path in files:
                with open(path, 'rb') as source:
                    _write_member(archive, archive_name, source.read())
        return output_path
    finally:
        shutil.rmtree(stage_root)


def main():
    try:
        output_path = build()
    except Exception as error:
        sys.stderr.write('build failed: %s\n' % error)
        return 1
    print(output_path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
