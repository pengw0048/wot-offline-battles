#!/usr/bin/env python2
"""Verify exact #1513 lifecycle ordering that signatures cannot express."""

from __future__ import print_function

import argparse
import json
import os
import sys
import zipfile

from audit_client_abi import _read_module_contract
from audit_lobby_consumers import _instructions


ORDERED_USES = (
    (
        'scripts/client/Vehicle.pyc',
        'Vehicle.prerequisites',
        ('typeDescriptor', 'getDescr', 'appearance_cache',
         'createAppearance'),
        'Vehicle descriptor and appearance prerequisites precede world entry',
    ),
    (
        'scripts/client/Vehicle.pyc',
        'Vehicle.onEnterWorld',
        ('vehicle_onEnterWorld', 'isPlayerVehicle', 'cell',
         'sendStateToOwnClient'),
        'Avatar entry callback precedes the own-client ready mailbox',
    ),
    (
        'scripts/client/Vehicle.pyc',
        'Vehicle.onLeaveWorld',
        ('_Vehicle__stopExtras', 'BigWorld', 'player',
         'vehicle_onLeaveWorld', 'isStarted'),
        'Vehicle leave dereferences the player after stopping extras',
    ),
    (
        'scripts/client/vehicle_systems/CompoundAppearance.pyc',
        'CompoundAppearance.deactivate',
        ('BigWorld', 'player', 'inputHandler',
         'removeVehicleFromCameraCollider', 'BigWorld', 'player', 'arena',
         'onPeriodChange', 'BigWorld', 'player', 'inputHandler',
         'onCameraChanged'),
        'appearance teardown removes all Avatar subscriptions in order',
    ),
    (
        'scripts/client/vehicle_systems/CompoundAppearance.pyc',
        'CompoundAppearance.__onModelsRefresh',
        ('deactivate', 'False', '_CompoundAppearance__compoundModel',
         '_CompoundAppearance__setupModels', 'setVehicle', 'activate'),
        'damaged-model refresh deactivates the old compound before replacement',
    ),
    (
        'scripts/client/vehicle_systems/components/highlighter.pyc',
        'Highlighter.deactivate',
        ('_Highlighter__highlightStatus', 'HIGHLIGHT_OFF', 'BigWorld',
         'wgDelEdgeDetectEntity', 'HIGHLIGHT_OFF',
         '_Highlighter__vehicle'),
        'stock highlighter removes the edge before releasing its vehicle',
    ),
    (
        'scripts/client/Vehicle.pyc',
        'Vehicle.delModel',
        ('enabled', 'highlight', 'False', 'delModel', 'highlight', 'True'),
        'Vehicle model removal preserves an enabled stock highlighter',
    ),
    (
        'scripts/client/VehicleGunRotator.pyc',
        'VehicleGunRotator.update',
        ('_VehicleGunRotator__avatar',
         'getOwnVehicleShotDispersionAngle',
         '_VehicleGunRotator__dispersionAngles'),
        'gun rotator consumes dispersion through the Avatar method',
    ),
    (
        'scripts/client/VehicleGunRotator.pyc',
        'VehicleGunRotator.setShotPosition',
        ('_VehicleGunRotator__dispersionAngles', 0),
        'shot updates mutate dispersion element zero in place',
    ),
    (
        'scripts/client/gui/battle_control/arena_info/listeners.pyc',
        'ArenaTeamBasesListener.__arena_onTeamBasePointsUpdate',
        ('_invokeListenersMethod', 'invalidateTeamBasePoints',
         'team', 'baseID', 'points', 'timeLeft', 'invadersCnt',
         'capturingStopped'),
        'base point events forward every #1513 field in order',
    ),
    (
        'scripts/client/gui/battle_control/arena_info/listeners.pyc',
        'ArenaTeamBasesListener.__arena_onTeamBaseCaptured',
        ('_invokeListenersMethod', 'invalidateTeamBaseCaptured',
         'team', 'baseID'),
        'base capture events forward base team before base id',
    ),
    (
        'scripts/client/Avatar.pyc',
        'PlayerAvatar.set_playerVehicleID',
        ('SET_PLAYER_ID', '_PlayerAvatar__onInitStepCompleted', 'BigWorld',
         'entity', 'playerVehicleID', 'inWorld', 'VEHICLE_ENTERED',
         '_PlayerAvatar__onInitStepCompleted'),
        'player id selection can run before the Vehicle is visible in-world',
    ),
    (
        'scripts/client/Avatar.pyc',
        'PlayerAvatar.vehicle_onEnterWorld',
        ('playerVehicleID', 'VEHICLE_ENTERED',
         '_PlayerAvatar__onInitStepCompleted'),
        'matching Vehicle entry completes the native Avatar init step',
    ),
    (
        'scripts/client/Avatar.pyc',
        'PlayerAvatar.__onInitStepCompleted',
        ('BigWorld', 'entities', 'values', 'inWorld', 'isStarted',
         'base', 'setClientReady'),
        'native readiness scans materialized Vehicles before the mailbox',
    ),
    (
        'scripts/client/Avatar.pyc',
        'PlayerAvatar.__onInitStepCompleted',
        ('SoundGroups', 'g_instance', 'enableArenaSounds',
         'applyPreferences', 'MusicControllerWWISE', 'onEnterArena'),
        'native Avatar starts arena ambience with user sound preferences',
    ),
    (
        'scripts/client/Avatar.pyc',
        'PlayerAvatar.__onArenaPeriodChange',
        ('_PlayerAvatar__setIsOnArena', 'ARENA_PERIOD', 'BATTLE'),
        'battle period synchronously enters the playable arena state',
    ),
    (
        'scripts/client/Avatar.pyc',
        'PlayerAvatar.__onArenaPeriodChange',
        ('ARENA_PERIOD', 'PREBATTLE', 'LightManager', 'GameLights',
         'startTicks'),
        'prebattle period starts the stock countdown presentation',
    ),
    (
        'scripts/client/AvatarInputHandler/__init__.pyc',
        'AvatarInputHandler.onControlModeChanged',
        ('_AvatarInputHandler__isArenaStarted', '_CTRL_MODE',
         'POSTMORTEM'),
        'stock camera mode changes are fenced until the input handler starts',
    ),
    (
        'scripts/client/AvatarInputHandler/__init__.pyc',
        'AvatarInputHandler.onControlModeChanged',
        ('steadyVehicleMatrixCalculator', 'relinkSources',
         '_AvatarInputHandler__isArenaStarted'),
        'every camera transition relinks aiming sources before its mode fence',
    ),
    (
        'scripts/client/AvatarInputHandler/__init__.pyc',
        'AvatarInputHandler.onControlModeChanged',
        ('steadyVehicleMatrixCalculator', 'relinkSources',
         '_AvatarInputHandler__curCtrl', 'disable',
         '_AvatarInputHandler__curCtrl', 'enable'),
        'camera sources relink before the new control captures them',
    ),
    (
        'scripts/client/AvatarInputHandler/__init__.pyc',
        'AvatarInputHandler.__onArenaStarted',
        ('ARENA_PERIOD', 'BATTLE',
         '_AvatarInputHandler__isArenaStarted',
         '_AvatarInputHandler__curCtrl', 'setGunMarkerFlag'),
        'native battle start enables camera modes and their gun marker',
    ),
    (
        'scripts/client/gui/battle_control/controllers/period_ctrl.pyc',
        'ArenaPeriodController._calculate',
        ('_endTime', 'BigWorld', 'serverTime'),
        'native countdown subtracts the advancing server clock from its deadline',
    ),
    (
        'scripts/client/Avatar.pyc',
        'PlayerAvatar.__setIsOnArena',
        ('_PlayerAvatar__isOnArena', 'moveVehicle',
         'makeVehicleMovementCommandByKeys', 'False'),
        'arena entry synchronously sends the initial movement command',
    ),
    (
        'scripts/client/Avatar.pyc',
        'PlayerAvatar.moveVehicle',
        ('base', 'vehicle_moveWith'),
        'initial movement reaches the Avatar server mailbox synchronously',
    ),
    (
        'scripts/client/Avatar.pyc',
        'PlayerAvatar.shoot',
        ('base', 'vehicle_shoot', '_PlayerAvatar__startWaitingForShot'),
        'shoot mailbox starts the native acknowledgement wait synchronously',
    ),
    (
        'scripts/client/Avatar.pyc',
        'PlayerAvatar.__showTimedOutShooting',
        ('typeDescriptor', 'gun', 'burst', 'showShooting', 'True'),
        'shot timeout predicts a finite descriptor burst',
    ),
    (
        'scripts/client/Vehicle.pyc',
        'Vehicle.showShooting',
        ('typeDescriptor', 'extrasDict', 'stopFor', 'startFor',
         'cancelWaitingForShot'),
        'authoritative shooting restarts one finite extra and closes the wait',
    ),
    (
        'scripts/client/Avatar.pyc',
        'PlayerAvatar.onBecomePlayer',
        ('loadPrerequisites', 'ProjectileMover', 'ProjectileMover',
         '_PlayerAvatar__projectileMover'),
        'Avatar constructs its projectile mover after GUI prerequisites',
    ),
    (
        'scripts/client/Avatar.pyc',
        'PlayerAvatar.__onInitStepCompleted',
        ('appearance_cache', 'onSpaceLoaded',
         '_PlayerAvatar__projectileMover', 'setSpaceID', 'spaceID'),
        'Avatar binds projectile ballistics after the map space is loaded',
    ),
    (
        'scripts/client/Avatar.pyc',
        'PlayerAvatar.onBecomeNonPlayer',
        ('_PlayerAvatar__projectileMover',
         '_PlayerAvatar__projectileMover', 'destroy',
         '_PlayerAvatar__projectileMover'),
        'Avatar destroys its projectile mover before releasing its reference',
    ),
    (
        'scripts/client/ProjectileMover.pyc',
        'ProjectileMover.__init__',
        ('PyBallisticsSimulator', '_ProjectileMover__ballistics',
         'inputHandler', 'onCameraChanged',
         '_ProjectileMover__onCameraChanged'),
        'projectile mover creates ballistics before subscribing to camera',
    ),
    (
        'scripts/client/ProjectileMover.pyc',
        'ProjectileMover.destroy',
        ('inputHandler', 'onCameraChanged',
         '_ProjectileMover__onCameraChanged',
         '_ProjectileMover__ballistics',
         '_ProjectileMover__projectiles', 'keys',
         '_ProjectileMover__delProjectile'),
        'projectile mover unsubscribes before retiring native projectiles',
    ),
    (
        'scripts/client/gui/ClientHangarSpace.pyc',
        '_VehicleAppearance.__startBuild',
        ('BigWorld', 'loadResourceListBG',
         '_VehicleAppearance__onResourcesLoaded'),
        'Vehicle appearance resources are loaded asynchronously',
    ),
    (
        'scripts/client/gui/ClientHangarSpace.pyc',
        '_VehicleAppearance.__onResourcesLoaded',
        ('failedIDs', '_VehicleAppearance__setupModel'),
        'appearance resource completion gates model setup',
    ),
    (
        'scripts/client/gui/ClientHangarSpace.pyc',
        '_VehicleAppearance.__doFinalSetup',
        ('BigWorld', 'entity', '_VehicleAppearance__vEntityId', 'model'),
        'appearance finalization resolves the owning entity again',
    ),
    (
        'scripts/client/gui/shared/personality.pyc',
        'onAccountShowGUI',
        ('ServicesLocator', 'gameState', 'onAccountShowGUI',
         'g_appLoader', 'showLobby', 'g_prbLoader', 'onAccountShowGUI'),
        'lobby consumers run before the normal prebattle dispatcher restore',
    ),
    (
        'scripts/client/gui/prb_control/dispatcher.pyc',
        '_PrbControlLoader.onAvatarBecomePlayer',
        ('_PrbControlLoader__isEnabled',
         '_PrbControlLoader__removeDispatcher'),
        'Avatar promotion removes the lobby prebattle dispatcher',
    ),
    (
        'scripts/client/gui/prb_control/dispatcher.pyc',
        '_PrbControlLoader.createBattleDispatcher',
        ('_PrbControlLoader__prbDispatcher', '_PreBattleDispatcher',
         '_PrbControlLoader__prbDispatcher'),
        'battle dispatcher creation installs the replacement synchronously',
    ),
    (
        'scripts/client/game.pyc',
        'fini',
        ('BigWorld', 'clearAllSpaces', 'gui_personality', 'fini',
         'SoundGroups', 'g_instance', 'destroy'),
        'mod shutdown precedes the late sound teardown zombie lookup',
    ),
    (
        'scripts/client/SoundGroups.pyc',
        'SoundGroups.destroy',
        ('BigWorld', 'player', 'inputHandler'),
        'sound teardown directly dereferences the retained player identity',
    ),
    (
        'scripts/client/ChatManager.pyc',
        'ChatManager.switchPlayerProxy',
        ('_ChatManager__cleanupMyCallbacks', 'proxy', 'playerProxy'),
        'old chat proxy cleanup precedes replacement proxy assignment',
    ),
    (
        'scripts/client_common/ClientChat.pyc',
        'ClientChat.__init__',
        ('self', '_ClientChat__chatActionCallbacks'),
        'every chat-capable player initializes its callback registry',
    ),
    (
        'scripts/client/Account.pyc',
        'PlayerAccount.onBecomeNonPlayer',
        ('chatManager', 'switchPlayerProxy', 'syncData',
         'onAccountBecomeNonPlayer', 'events',
         'onAccountBecomeNonPlayer'),
        'Account detaches chat and helpers before GUI retirement completes',
    ),
    (
        'scripts/client/Avatar.pyc',
        'PlayerAvatar.onBecomeNonPlayer',
        ('chatManager', 'switchPlayerProxy', 'g_playerEvents',
         'onAvatarBecomeNonPlayer'),
        'Avatar detaches chat before publishing its retirement event',
    ),
    (
        'scripts/client/Avatar.pyc',
        'PlayerAvatar.onBecomeNonPlayer',
        ('_PlayerAvatar__destroyGUI', 'MusicControllerWWISE',
         'onLeaveArena'),
        'native Avatar closes the arena music and ambience lifecycle',
    ),
    (
        'scripts/client/account_helpers/AccountSyncData.pyc',
        'AccountSyncData.setAccount',
        ('_AccountSyncData__account',
         '_AccountSyncData__savePersistentCache',
         '_AccountSyncData__persistentCache', 'setAccount'),
        'persistent cache save precedes replacement weak-proxy binding',
    ),
    (
        'scripts/client/account_helpers/persistent_caches.pyc',
        'SimpleCache.setAccount',
        ('weakref', 'proxy', '_SimpleCache__account'),
        'Account cache stores a weak proxy',
    ),
    (
        'scripts/client/account_helpers/persistent_caches.pyc',
        'cacheFileName',
        ('name', '__class__', '__name__'),
        'cache filename dereferences Account identity fields',
    ),
    (
        'scripts/client/gui/app_loader/states.pyc',
        'LoginState.init',
        ('_clearEntitiesAndSpaces', '_updateDscDesc'),
        'LoginState clears client-only entities before normal initialization',
    ),
    (
        'scripts/client/gui/app_loader/states.pyc',
        'LoginState.update',
        ('_clearEntitiesAndSpaces', '_updateDscDesc'),
        'LoginState update repeats the destructive entity boundary',
    ),
    (
        'scripts/client/gui/app_loader/loader.pyc',
        '_AppLoader.showBattleLoading',
        ('changeSpace', '_SPACE_ID', 'BATTLE_LOADING'),
        'battle loading entry changes to the native loading GUI space',
    ),
    (
        'scripts/client/gui/Scaleform/daapi/view/lobby/header/'
        'LobbyHeader.pyc',
        'LobbyHeader.fightClick',
        ('lobbyContext', 'isHeaderNavigationPossible',
         'prbDispatcher', 'doAction', 'PrbAction'),
        'native lobby fight click reaches the stock prebattle action boundary',
    ),
    (
        'scripts/client/gui/Scaleform/framework/entities/'
        'BaseDAAPIModule.pyc',
        'BaseDAAPIModule.setFlashObject',
        ('turnDAAPIon', 'isCreated', 'create'),
        'Scaleform binds the Python script before populating LobbyHeader',
    ),
    (
        'scripts/client/gui/Scaleform/daapi/view/lobby/header/'
        'LobbyHeader.pyc',
        'LobbyHeader._updatePrebattleControls',
        ('prbEntity', 'canPlayerDoAction', '_checkFightButtonDisabled',
         'as_disableFightButtonS'),
        'stock prebattle validation computes and paints the fight-button state',
    ),
    (
        'scripts/client/gui/prb_control/events_dispatcher.pyc',
        'EventDispatcher.updateUI',
        ('_EventDispatcher__fireEvent', 'FightButtonEvent',
         'FIGHT_BUTTON_UPDATE', '_EventDispatcher__invalidatePrbEntity'),
        'the stock UI refresh event precedes prebattle entity invalidation',
    ),
    (
        'scripts/client/gui/Scaleform/daapi/view/lobby/header/'
        'LobbyHeader.pyc',
        'LobbyHeader.__addListeners',
        ('FightButtonEvent', 'FIGHT_BUTTON_UPDATE',
         '_LobbyHeader__handleFightButtonUpdated',
         'EVENT_BUS_SCOPE', 'LOBBY'),
        'the lobby header subscribes its repaint handler in lobby scope',
    ),
    (
        'scripts/client/gui/app_loader/states.pyc',
        'LobbyState._getNextState',
        ('guiSpaceID', '_SPACE_ID', 'BATTLE_LOADING',
         'BattleLoadingState', 'arenaGuiType'),
        'lobby enters battle loading with the selected arena GUI type',
    ),
    (
        'scripts/client/gui/app_loader/states.pyc',
        'BattleLoadingState._getNextState',
        ('guiSpaceID', '_SPACE_ID', 'BATTLE', 'True',
         '_doStartBattle', '_createBattleState', '_SPACE_ID', 'LOBBY',
         'LobbyState'),
        'battle loading starts battle before retaining the lobby fallback',
    ),
    (
        'scripts/client/gui/app_loader/states.pyc',
        'BattleLoadingState._createBattleState',
        ('_isBattleReplayPlaying', 'ReplayBattleState', '_arenaGuiType',
         'BattleState', '_arenaGuiType'),
        'battle loading selects replay or normal battle with its GUI type',
    ),
    (
        'scripts/client/gui/app_loader/states.pyc',
        'BattleState._getNextState',
        ('guiSpaceID', '_SPACE_ID', 'WAITING', 'WaitingState'),
        'battle exit passes through the native waiting state',
    ),
    (
        'scripts/client/gui/battle_control/controllers/'
        'arena_load_ctrl.pyc',
        'ArenaLoadController.invalidateArenaInfo',
        ('g_appLoader', 'showBattleLoading'),
        'arena invalidation enters the battle loading GUI space',
    ),
    (
        'scripts/client/gui/battle_control/controllers/'
        'arena_load_ctrl.pyc',
        'ArenaLoadController.arenaLoadCompleted',
        ('True', '_ArenaLoadController__isCompleted', 'g_appLoader',
         'showBattlePage', '_viewComponents', 'arenaLoadCompleted'),
        'arena completion shows battle before notifying view components',
    ),
    (
        'scripts/client/gui/battle_control/controllers/repositories.pyc',
        'SharedControllersLocator.arenaLoad',
        ('_repository', 'getController', 'BATTLE_CTRL_ID',
         'ARENA_LOAD_PROGRESS'),
        'shared arena-load access resolves the native progress controller',
    ),
    (
        'scripts/client/gui/battle_control/controllers/debug_ctrl.pyc',
        'DebugController._update',
        ('statLagDetected', 'statPing', 'updateDebugInfo'),
        'stock battle diagnostics source lag and ping from retail BigWorld',
    ),
    (
        'scripts/client/gui/Scaleform/daapi/view/battle/shared/'
        'debug_panel.pyc',
        'DebugPanel.updateDebugInfo',
        ('as_updatePingFPSLagInfoS', 'as_updatePingFPSInfoS'),
        'debug panel paints the supplied lag and ping values directly',
    ),
    (
        'scripts/client/gui/shared/personality.pyc',
        'onAccountShowGUI',
        ('g_hangarSpace', 'g_currentVehicle', 'showLobby'),
        'native Account GUI owns asynchronous hangar then lobby transition',
    ),
    (
        'scripts/client/gui/shared/personality.pyc',
        'onAccountBecomeNonPlayer',
        ('g_currentVehicle', 'destroy',
         'g_currentPreviewVehicle', 'destroy',
         'g_hangarSpace', 'destroy'),
        'Account retirement destroys lobby vehicles before hangar space',
    ),
    (
        'scripts/client/Avatar.pyc',
        'PlayerAvatar.onBecomePlayer',
        ('cameraSpaceID', 'g_hangarSpace', 'destroy',
         'ClientArena', 'arenaType', 'abort'),
        'Avatar promotion retires hangar before validating its arena',
    ),
    (
        'scripts/client/OfflineMapCreator.pyc',
        'OfflineMapCreator.create',
        ('showBattlePage', 'createSpace', 'createEntity', 'player', 'cancel'),
        'stock map creation can cancel after partial player construction',
    ),
    (
        'scripts/client/OfflineMapCreator.pyc',
        'OfflineMapCreator.destroy',
        ('clearEntitiesAndSpaces', '_OfflineMapCreator__spaceId',
         'isClientSpace', '_OfflineMapCreator__spaceMappingId',
         'delSpaceGeometryMapping', 'clearSpace', 'releaseSpace', 'cancel'),
        'stock map teardown releases mapping and space before lossy cancel',
    ),
    (
        'scripts/client/Avatar.pyc',
        'PlayerAvatar.leaveArena',
        ('base', 'leaveArena', 'BattleReplay'),
        'server leave mailbox returns before native Avatar cleanup completes',
    ),
)


REQUIRED_USES = (
    (
        'scripts/client/gui/prb_control/dispatcher.pyc',
        '_PrbControlLoader.onAccountShowGUI',
        ('createBattleDispatcher',),
        'normal Account GUI recreates the battle dispatcher',
    ),
    (
        'scripts/client/gui/prb_control/__init__.pyc',
        'prbDispatcherProperty.__get__',
        ('g_prbLoader', 'getDispatcher'),
        'lobby views resolve dispatcher state through the global loader',
    ),
    (
        'scripts/client/gui/app_loader/states.pyc',
        'LoginState._clearEntitiesAndSpaces',
        ('BigWorld', 'clearEntitiesAndSpaces'),
        'LoginState uses the engine entity-and-space clear',
    ),
    (
        'scripts/client/Account.pyc',
        'PlayerAccount.onBecomePlayer',
        ('BigWorld', 'clearAllSpaces'),
        'Account promotion normally clears all spaces',
    ),
    (
        'scripts/client/OfflineMapCreator.pyc',
        'OfflineMapCreator.cancel',
        ('worldDrawEnabled', 'setWatcher'),
        'map cancel only resets presentation state',
    ),
)


FORBIDDEN_USES = (
    (
        'scripts/client/OfflineMapCreator.pyc',
        'OfflineMapCreator.cancel',
        ('clearEntitiesAndSpaces', 'clearAllSpaces', 'clearSpace',
         'releaseSpace'),
        'map cancel must not be mistaken for ownership cleanup',
    ),
)


EXPECTED_ACCOUNT_BINDERS = (
    ('scripts/client/account_helpers/AccountSyncData.pyc',
     'AccountSyncData.setAccount'),
    ('scripts/client/account_helpers/ClientBadges.pyc',
     'ClientBadges.setAccount'),
    ('scripts/client/account_helpers/ClientGoodies.pyc',
     'ClientGoodies.setAccount'),
    ('scripts/client/account_helpers/ClientNewYear.pyc',
     'ClientNewYear.setAccount'),
    ('scripts/client/account_helpers/ClientRanked.pyc',
     'ClientRanked.setAccount'),
    ('scripts/client/account_helpers/DossierCache.pyc',
     'DossierCache.setAccount'),
    ('scripts/client/account_helpers/Inventory.pyc',
     'Inventory.setAccount'),
    ('scripts/client/account_helpers/QuestProgress.pyc',
     'QuestProgress.setAccount'),
    ('scripts/client/account_helpers/Shop.pyc', 'Shop.setAccount'),
    ('scripts/client/account_helpers/Stats.pyc', 'Stats.setAccount'),
    ('scripts/client/account_helpers/client_recycle_bin.pyc',
     'ClientRecycleBin.setAccount'),
    ('scripts/client/account_helpers/persistent_caches.pyc',
     'SimpleCache.setAccount'),
    ('scripts/client/account_helpers/vehicle_rotation.pyc',
     'VehicleRotation.setAccount'),
)


def _code(archive, cache, member, function):
    if member not in cache:
        unused_signatures, code_objects, unused_globals = \
            _read_module_contract(archive, member)
        cache[member] = code_objects
    value = cache[member].get(function)
    if value is None:
        raise ValueError('%s: missing %s' % (member, function))
    return value


def _ordered_offsets(code, names):
    instructions = _instructions(code)
    offsets = []
    after = -1
    for name in names:
        match = None
        for instruction in instructions:
            if (instruction['offset'] > after and
                    instruction['value'] == name):
                match = instruction['offset']
                break
        if match is None:
            return None
        offsets.append(match)
        after = match
    return offsets


def audit(client_root):
    package_path = os.path.join(
        os.path.abspath(client_root), 'res', 'packages', 'scripts.pkg')
    if not os.path.isfile(package_path):
        raise ValueError('scripts.pkg not found: %s' % package_path)
    errors = []
    checked = []
    cache = {}
    with zipfile.ZipFile(package_path, 'r') as archive:
        names = set(archive.namelist())
        members = set(item[0] for item in (
            ORDERED_USES + REQUIRED_USES + FORBIDDEN_USES))
        missing = sorted(member for member in members if member not in names)
        errors.extend('missing bytecode member: %s' % member
                      for member in missing)
        actual_binders = []
        for member in sorted(names):
            if (not member.startswith('scripts/client/account_helpers/') or
                    not member.endswith('.pyc')):
                continue
            try:
                signatures, unused_codes, unused_globals = \
                    _read_module_contract(archive, member)
            except (KeyError, ValueError):
                continue
            for function in signatures:
                if function.endswith('.setAccount'):
                    actual_binders.append((member, function))
        actual_binders = tuple(sorted(actual_binders))
        expected_binders = tuple(sorted(EXPECTED_ACCOUNT_BINDERS))
        if actual_binders != expected_binders:
            errors.append(
                'Account setAccount inventory changed: actual=%r expected=%r' %
                (actual_binders, expected_binders))
        else:
            checked.append({
                'contract': 'complete Account helper binding inventory',
                'binders': len(actual_binders),
            })
        avatar_prerequisites = _code(
            archive, cache, 'scripts/client/Avatar.pyc',
            'PlayerAvatar.prerequisites')
        prerequisite_instructions = _instructions(avatar_prerequisites)
        prerequisite_opnames = tuple(
            item['opname'] for item in prerequisite_instructions)
        prerequisite_values = tuple(
            item['value'] for item in prerequisite_instructions)
        if (prerequisite_opnames != ('LOAD_CONST', 'RETURN_VALUE') or
                prerequisite_values != ((), None)):
            errors.append(
                'PlayerAvatar.prerequisites is no longer the immediate '
                'empty-tuple boundary: opnames=%r values=%r' %
                (prerequisite_opnames, prerequisite_values))
        else:
            checked.append({
                'member': 'scripts/client/Avatar.pyc',
                'function': 'PlayerAvatar.prerequisites',
                'contract': 'Avatar itself has no asynchronous prerequisites',
            })
        for member, function, expected, reason in ORDERED_USES:
            if member in missing:
                continue
            code = _code(archive, cache, member, function)
            offsets = _ordered_offsets(code, expected)
            if offsets is None:
                errors.append('%s:%s violates order %r' %
                              (member, function, expected))
            else:
                checked.append({
                    'member': member, 'function': function,
                    'contract': reason, 'offsets': offsets,
                })
        for member, function, expected, reason in REQUIRED_USES:
            if member in missing:
                continue
            code = _code(archive, cache, member, function)
            instructions = _instructions(code)
            used = set(item['value'] for item in instructions)
            absent = tuple(name for name in expected if name not in used)
            if absent:
                errors.append('%s:%s missing lifecycle names %r' %
                              (member, function, absent))
            else:
                checked.append({
                    'member': member, 'function': function,
                    'contract': reason,
                })
        for member, function, forbidden, reason in FORBIDDEN_USES:
            if member in missing:
                continue
            code = _code(archive, cache, member, function)
            instructions = _instructions(code)
            used = set(item['value'] for item in instructions)
            present = tuple(name for name in forbidden if name in used)
            if present:
                errors.append('%s:%s unexpectedly uses %r' %
                              (member, function, present))
            else:
                checked.append({
                    'member': member, 'function': function,
                    'contract': reason,
                })
    if errors:
        raise ValueError('; '.join(errors))
    return {
        'clientRoot': os.path.abspath(client_root),
        'pythonRuntime': '%d.%d.%d' % sys.version_info[:3],
        'checkedLifecycleContracts': len(checked),
        'contracts': checked,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Audit exact WoT #1513 lifecycle ordering read-only.')
    parser.add_argument('client_root')
    args = parser.parse_args(argv)
    if sys.version_info[:2] != (2, 7):
        parser.error('this auditor must run under CPython 2.7')
    try:
        report = audit(args.client_root)
    except (IOError, KeyError, ValueError, zipfile.BadZipfile) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
