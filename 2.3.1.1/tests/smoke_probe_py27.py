#!/usr/bin/env python2.7
from __future__ import print_function

import imp
import os
import sys


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
LIFECYCLE = os.path.join(
    ROOT, 'src', 'res', 'scripts', 'client', 'gui', 'mods',
    'offline_2311_poc', 'lifecycle.py')


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


def main():
    lifecycle = imp.load_source('offline_2311_poc_py27_smoke', LIFECYCLE)
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
        get_client_version=lambda: 'v.2.3.1.1 #916')
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
    print('CPython 2.7 probe smoke passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
