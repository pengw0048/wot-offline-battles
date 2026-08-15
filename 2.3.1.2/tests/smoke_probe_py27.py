#!/usr/bin/env python2.7
from __future__ import print_function

import imp
import os
import shutil
import sys
import tempfile
import zipfile


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
PROBE_MODULE = os.path.join(
    ROOT, 'src', 'res', 'scripts', 'client', 'gui', 'mods',
    'mod_offline_2312_poc.py')
PACKAGE_ENTRY = 'res/scripts/client/gui/mods/mod_offline_2312_poc.pyc'


class Logger(object):
    def __init__(self):
        self.messages = []

    def _record(self, message, args):
        self.messages.append(message % args if args else message)

    def info(self, message, *args):
        self._record(message, args)

    def error(self, message, *args):
        self._record(message, args)

    def exception(self, message, *args):
        self._record(message, args)


class OfflineEntity(object):
    spaceID = 23


class FreeCamera(object):
    pass


class BigWorldStub(object):
    def __init__(self):
        self.pending = {}
        self.next_id = 1
        self.status = 0.0
        self.spaces = {}

    def callback(self, delay, function):
        callback_id = self.next_id
        self.next_id += 1
        self.pending[callback_id] = (delay, function)
        return callback_id

    def cancelCallback(self, callback_id):
        self.pending.pop(callback_id, None)

    def spaceLoadStatus(self):
        return self.status

    def player(self):
        return OfflineEntity()

    def camera(self):
        return FreeCamera()


class OfflineModeStub(object):
    is_enabled = False
    is_loaded = False

    def enabled(self):
        return self.is_enabled

    def isSpaceLoaded(self):
        return self.is_loaded


def _load_probe(package_path=None):
    if package_path is None:
        return imp.load_source(
            'offline_2312_poc_py27_source_smoke', PROBE_MODULE)

    temporary_root = tempfile.mkdtemp(prefix='offline_2312_pyc_smoke-')
    try:
        compiled_path = os.path.join(
            temporary_root, 'mod_offline_2312_poc.pyc')
        with zipfile.ZipFile(package_path, 'r') as archive:
            bytecode = archive.read(PACKAGE_ENTRY)
        with open(compiled_path, 'wb') as output:
            output.write(bytecode)
        return imp.load_compiled(
            'offline_2312_poc_py27_package_smoke', compiled_path)
    finally:
        shutil.rmtree(temporary_root)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) > 1:
        raise SystemExit('usage: smoke_probe_py27.py [package.wotmod]')
    package_path = argv[0] if argv else None
    lifecycle = _load_probe(package_path)
    bigworld = BigWorldStub()
    offline_mode = OfflineModeStub()
    logger = Logger()
    clock = [10.0]
    probe = lifecycle.init(
        argv=['WorldOfTanks.exe', 'offline', 'spaces/01_karelia'],
        bigworld=bigworld,
        offline_mode=offline_mode,
        logger=logger,
        now=lambda: clock[0],
        get_client_version=lambda: 'v.2.3.1.2 #919')
    if probe is None or len(bigworld.pending) != 1:
        raise AssertionError('probe did not schedule exactly one callback')

    offline_mode.is_enabled = True
    offline_mode.is_loaded = True
    bigworld.status = 1.0
    bigworld.spaces = {23: object()}
    callback_id = probe.callback_id
    unused_delay, function = bigworld.pending.pop(callback_id)
    function()
    if not probe.completed or bigworld.pending:
        raise AssertionError('probe did not finish after stock loaded state')
    if not any('space_loaded' in message for message in logger.messages):
        raise AssertionError('space_loaded evidence marker is missing')
    lifecycle.fini()
    source = 'package' if package_path is not None else 'source'
    print('CPython 2.7 %s probe smoke passed' % source)
    return 0


if __name__ == '__main__':
    sys.exit(main())
