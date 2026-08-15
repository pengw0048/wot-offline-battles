#!/usr/bin/env python2.7
from __future__ import print_function

import imp
import os
import shutil
import sys
import tempfile
import zipfile


sys.dont_write_bytecode = True


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
PROBE_MODULE = os.path.join(
    ROOT, 'src', 'res', 'scripts', 'client', 'gui', 'mods',
    'mod_offline_2312_avatar_arena_probe.py')
PACKAGE_ENTRY = (
    'res/scripts/client/gui/mods/'
    'mod_offline_2312_avatar_arena_probe.pyc')


class Logger(object):
    def __init__(self):
        self.messages = []

    def _record(self, message, args):
        self.messages.append(message % args if args else message)

    def info(self, message, *args):
        self._record(message, args)

    def error(self, message, *args):
        self._record(message, args)


class ArenaType(object):
    geometryName = '01_karelia'
    gameplayName = 'ctf'


class ClientArena(object):
    arenaType = ArenaType()


class PlayerAvatar(object):
    def __init__(self):
        if self.arenaUniqueID != 0 or self.arenaTypeID != 101:
            raise AssertionError('avatar arena properties were not preseeded')
        if self.arenaBonusType != 17 or self.arenaGuiType != 23:
            raise AssertionError('avatar mode properties were not preseeded')
        if self.arenaExtraData != {} or self.bonusCapsOverrides is not None:
            raise AssertionError('avatar bonus properties were not preseeded')
        self.id = 17
        self.inWorld = True
        self.spaceID = 23
        self.playerVehicleID = 0
        self.inputHandler = None
        self.arena = ClientArena()
        self._PlayerAvatar__initProgress = 2

    def hasBonusCap(self, unused_cap):
        return False

    def onEnterWorld(self, *unused_args):
        return None

    def onBecomePlayer(self):
        return None


class CursorCamera(object):
    spaceID = 23


class BigWorldStub(object):
    def __init__(self):
        self.pending = {}
        self.next_id = 1
        self.status = 1.0
        self.current_player = None
        self.current_camera = None

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
        return self.current_player

    def camera(self):
        return self.current_camera

    def run(self, callback_id):
        unused_delay, function = self.pending.pop(callback_id)
        function()


class Creator(object):
    def __init__(self, bigworld):
        self.bigworld = bigworld
        self.active = False
        self.destroyed = False

    def Active(self):
        return self.active

    def create(self, map_name):
        if map_name != '01_karelia':
            raise AssertionError('wrong map')
        self.active = True
        self.bigworld.current_player = PlayerAvatar()
        self.bigworld.current_camera = CursorCamera()
        self.bigworld.current_player.hasBonusCap('component-init')
        self.bigworld.current_player.onEnterWorld()
        self.bigworld.current_player.onBecomePlayer()

    def destroy(self):
        self.destroyed = True
        self.active = False


class OfflineMode(object):
    def launch(self, space_name):
        raise AssertionError('stock free-camera route was called')


class Game(object):
    def __init__(self):
        self.finished = False

    def fini(self):
        self.finished = True


def _load_probe(package_path=None):
    if package_path is None:
        return imp.load_source(
            'offline_2312_avatar_arena_probe_source_smoke', PROBE_MODULE)
    temporary_root = tempfile.mkdtemp(prefix='avatar_arena_pyc_smoke-')
    try:
        compiled_path = os.path.join(
            temporary_root, 'mod_offline_2312_avatar_arena_probe.pyc')
        with zipfile.ZipFile(package_path, 'r') as archive:
            bytecode = archive.read(PACKAGE_ENTRY)
        with open(compiled_path, 'wb') as output:
            output.write(bytecode)
        return imp.load_compiled(
            'offline_2312_avatar_arena_probe_package_smoke', compiled_path)
    finally:
        shutil.rmtree(temporary_root)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) > 1:
        raise SystemExit('usage: smoke_probe_py27.py [package.wotmod]')
    lifecycle = _load_probe(argv[0] if argv else None)
    bigworld = BigWorldStub()
    creator = Creator(bigworld)
    offline_mode = OfflineMode()
    game = Game()
    logger = Logger()
    probe = lifecycle.init(
        argv=['WorldOfTanks.exe', 'offline', 'spaces/01_karelia',
              'avatarArenaProbe'],
        bigworld=bigworld,
        offline_mode=offline_mode,
        creator=creator,
        arena_cache={101: ArenaType()},
        game_module=game,
        avatar_type=PlayerAvatar,
        arena_bonus_unknown=17,
        arena_gui_unknown=23,
        logger=logger,
        get_client_version=lambda: 'v.2.3.1.2 #919')
    if probe is None:
        raise AssertionError('probe did not install')
    offline_mode.launch('spaces/01_karelia')
    if len(bigworld.pending) != 1:
        raise AssertionError('route did not schedule native preflight')
    bigworld.run(probe.callback_id)
    if not probe.completed or probe.failed:
        raise AssertionError('player/arena gate did not pass')
    if not any('gate_pass gate=player_arena' in message
               for message in logger.messages):
        raise AssertionError('gate pass marker is missing')
    game.fini()
    if not creator.destroyed or not game.finished:
        raise AssertionError('ordered game shutdown did not run')
    lifecycle.fini()
    source = 'package' if argv else 'source'
    print('CPython 2.7 %s avatar/arena probe smoke passed' % source)
    return 0


if __name__ == '__main__':
    sys.exit(main())
