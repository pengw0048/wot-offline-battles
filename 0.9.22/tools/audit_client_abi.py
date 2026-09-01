#!/usr/bin/env python2
"""Check the pinned #1513 Python ABI without importing BigWorld modules.

Function signatures are read directly from CPython 2.7 code objects in the
client's ``scripts.pkg``.  Data shapes live in the producer contract tests;
signatures alone cannot describe dictionary keys or tuple payload lengths.
"""

from __future__ import print_function

import argparse
import json
import marshal
import opcode
import os
import struct
import sys
import types
import zipfile

try:
    from cStringIO import StringIO
except ImportError:
    from io import BytesIO as StringIO


EXPECTED_ABI = {
    'scripts/client/game.pyc': {
        'wg_onChunkLoad': (
            'spaceID', 'chunkID', 'numDestructibles', 'isOutside'),
        'wg_onChunkLoose': ('spaceID', 'chunkID', 'isOutside'),
    },
    'scripts/common/OldSpaceData.pyc': {
        'getPropertyNameForKey': ('key',),
        'getSpaceDataFirstForKey': ('spaceID', 'key'),
        'setSpaceData': ('spaceID', 'key', 'value'),
    },
    'scripts/common/ArenaType.pyc': {
        'getVisibilityMask': ('gameplayID',),
    },
    'scripts/client/Account.pyc': {
        'PlayerAccount.__init__': ('self',),
        'PlayerAccount.onBecomePlayer': ('self',),
        'PlayerAccount.onBecomeNonPlayer': ('self',),
        'PlayerAccount.onCmdResponse': (
            'self', 'requestID', 'resultID', 'errorStr'),
        'PlayerAccount.onCmdResponseExt': (
            'self', 'requestID', 'resultID', 'errorStr', 'ext'),
        'PlayerAccount.onStreamComplete': ('self', 'id', 'desc', 'data'),
        'PlayerAccount.showGUI': ('self', 'ctx'),
        'PlayerAccount.receiveServerStats': ('self', 'stats'),
        'PlayerAccount._update': ('self', 'triggerEvents', 'diff'),
    },
    'scripts/client_common/ClientChat.pyc': {
        'ClientChat.__init__': ('self',),
        'ClientChat.onChatAction': ('self', 'chatActionData'),
        'ClientChat.unsubscribeChatAction': (
            'self', 'callback', 'action', 'channelId'),
        'ClientChat.__dataTimeProcessor': ('self', 'actionData'),
    },
    'scripts/client/ChatManager.pyc': {
        'ChatManager.switchPlayerProxy': ('self', 'proxy'),
    },
    'scripts/client/account_helpers/AccountSyncData.pyc': {
        'AccountSyncData.setAccount': ('self', 'account'),
    },
    'scripts/client/account_helpers/persistent_caches.pyc': {
        'SimpleCache.setAccount': ('self', 'account'),
        'SimpleCache.getFileName': ('self',),
        'cacheFileName': ('account', 'cacheType', 'cacheName'),
    },
    'scripts/client/account_helpers/QuestProgress.pyc': {
        'QuestProgress.synchronize': ('self', 'isFullSync', 'diff'),
    },
    'scripts/client/account_helpers/AccountValidator.pyc': {
        'AccountValidator.validate': ('self', 'callback'),
    },
    'scripts/client/account_helpers/Shop.pyc': {
        'Shop.__onSyncComplete': ('self', 'syncID', 'data'),
        'Shop.__onSyncDataReceived': ('self', 'data'),
    },
    'scripts/client/account_helpers/DossierCache.pyc': {
        'DossierCache.__onSyncComplete': ('self', 'syncID', 'data'),
    },
    'scripts/client/gui/shared/utils/requesters/QuestsProgressRequester.pyc': {
        '_PersonalMissionsProgressRequester._response': (
            'self', 'resID', 'value', 'callback'),
    },
    'scripts/common/items/tankmen.pyc': {
        'generateTankmen': (
            'nationID', 'vehicleTypeID', 'roles', 'isPremium',
            'roleLevel', 'skillsMask', 'isPreview'),
        'TankmanDescr.__init__': ('self', 'compactDescr', 'battleOnly'),
    },
    'scripts/common/items/__init__.pyc': {
        'ItemsPrices.__init__': ('self', 'prices'),
        'ItemsPrices.getPrices': ('self', 'descriptor'),
        'makeIntCompactDescrByID': (
            'itemTypeName', 'nationID', 'itemID'),
    },
    'scripts/common/items/vehicles.pyc': {
        '_readSiegeModeParams': ('xmlCtx', 'section', 'vehType'),
        'VehicleDescr': ('compactDescr', 'typeID', 'typeName'),
        'getDefaultAmmoForGun': ('gunDescr',),
        'VehicleDescriptor.getHitTesters': ('self',),
        'CompositeVehicleDescriptor.onSiegeStateChanged': (
            'self', 'siegeMode'),
        'VehicleDescriptor.computeBaseInvisibility': (
            'self', 'crewFactor', 'camouflageId'),
        'getItemByCompactDescr': ('compactDescr',),
        'Cache.equipments': ('self',),
        'Cache.customization20': ('self',),
        'VehicleList.getList': ('self', 'nationID'),
    },
    'scripts/common/ModelHitTester.pyc': {
        'ModelHitTester.__init__': ('self', 'dataSection'),
        'ModelHitTester.isBspModelLoaded': ('self',),
        'ModelHitTester.loadBspModel': ('self',),
        'ModelHitTester.releaseBspModel': ('self',),
    },
    'scripts/common/physics_shared.pyc': {
        'configurePhysicsMode': ('cfg', 'typeDesc', 'gravityFactor'),
    },
    'scripts/common/items/components/legacy_stuff.pyc': {
        'NoLegacyStuff.get': ('self', 'k', 'd'),
        'NoLegacyStuff.__getitem__': ('self', 'item'),
        'NoLegacyStuff.__contains__': ('self', 'item'),
        'NoLegacyStuff.__iter__': ('self',),
        'NoLegacyStuff.keys': ('self',),
        'NoLegacyStuff.values': ('self',),
        'NoLegacyStuff.items': ('self',),
    },
    'scripts/common/items/components/shared_components.pyc': {
        'DeviceHealth.__init__': (
            'self', 'maxHealth', 'repairCost', 'maxRegenHealth'),
    },
    'scripts/common/items/vehicle_items.pyc': {
        'InstallableItem.maxHealth': ('self',),
        'InstallableItem.maxRegenHealth': ('self',),
        'Engine.__init__': (
            'self', 'typeID', 'componentID', 'componentName',
            'compactDescr', 'level'),
    },
    'scripts/client/vehicle_systems/model_assembler.pyc': {
        'prepareCompoundAssembler': (
            'vehicleDesc', 'modelStateName', 'spaceID',
            'isTurretDetached'),
        'setupTurretRotations': ('appearance',),
        'assembleRecoil': ('appearance', 'lodLink'),
        'assembleWaterSensor': (
            'vehicleDesc', 'appearance', 'lodStateLink'),
    },
    'scripts/client/OfflineEntity.pyc': {
        'OfflineEntity.__init__': ('self',),
        'OfflineEntity.prerequisites': ('self',),
        'OfflineEntity.onEnterWorld': ('self', 'prereqs'),
        'OfflineEntity.onLeaveWorld': ('self',),
        'OfflineEntity.collideSegment': (
            'self', 'startPoint', 'endPoint', 'skipGun'),
    },
    'scripts/client/ProjectileMover.pyc': {
        'ProjectileMover.__init__': ('self',),
        'ProjectileMover.add': (
            'self', 'shotID', 'effectsDescr', 'gravity', 'refStartPoint',
            'refVelocity', 'startPoint', 'maxDistance', 'attackerID',
            'tracerCameraPos'),
        'ProjectileMover.hide': ('self', 'shotID', 'endPoint'),
        'ProjectileMover.explode': (
            'self', 'shotID', 'effectsDescr', 'effectMaterial', 'endPoint',
            'velocityDir'),
        'ProjectileMover.setSpaceID': ('self', 'spaceID'),
        'ProjectileMover.destroy': ('self',),
        'segmentMayHitEntity': ('entity', 'startPoint', 'endPoint'),
        'collideEntities': (
            'startPoint', 'endPoint', 'entities', 'skipGun'),
        'getCollidableEntities': ('exceptIDs', 'startPoint', 'endPoint'),
    },
    'scripts/client/helpers/EntityExtra.pyc': {
        'EntityExtra.startFor': ('self', 'entity', 'args'),
        'EntityExtra.stopFor': ('self', 'entity'),
        'EntityExtra.stop': ('self', 'data'),
        'EntityExtra.isRunningFor': ('self', 'entity'),
    },
    'scripts/client/helpers/bound_effects.pyc': {
        'ModelBoundEffects.__init__': ('self', 'model'),
        'ModelBoundEffects.addNew': (
            'self', 'matProv', 'effectsList', 'keyPoints', 'waitForKeyOff',
            '**args'),
        'ModelBoundEffects.destroy': ('self',),
        'ModelBoundEffects.stop': ('self',),
    },
    'scripts/client/helpers/EffectsList.pyc': {
        '_ShotSoundEffectDesc.create': ('self', 'model', 'list', 'args'),
    },
    'scripts/client/gui/battle_control/controllers/feedback_adaptor.pyc': {
        'BattleFeedbackAdaptor.__init__': ('self', 'setup'),
        'BattleFeedbackAdaptor.handleBattleEvents': ('self', 'events'),
        'BattleFeedbackAdaptor.startVehicleVisual': (
            'self', 'vProxy', 'isImmediate'),
        'BattleFeedbackAdaptor.stopVehicleVisual': (
            'self', 'vehicleID', 'isPlayerVehicle'),
        'BattleFeedbackAdaptor.setVehicleNewHealth': (
            'self', 'vehicleID', 'newHealth', 'attackerID',
            'attackReasonID'),
        'BattleFeedbackAdaptor._setVehicleHealthChanged': (
            'self', 'vehicleID', 'newHealth', 'attackerID',
            'attackReasonID'),
    },
    'scripts/client/gui/battle_control/battle_session.pyc': {
        'BattleSessionProvider.shared': ('self',),
        'BattleSessionProvider.getArenaDP': ('self',),
        'BattleSessionProvider.addArenaCtrl': ('self', 'controller'),
        'BattleSessionProvider.removeArenaCtrl': ('self', 'controller'),
        'BattleSessionProvider.setVehicleHealth': (
            'self', 'isPlayerVehicle', 'vehicleID', 'newHealth',
            'attackerID', 'attackReasonID'),
    },
    'scripts/client/gui/battle_control/arena_info/arena_dp.pyc': {
        'ArenaDataProvider.isRequiredDataExists': ('self',),
        'ArenaDataProvider.getPlayerVehicleID': ('self', 'forceUpdate'),
    },
    'scripts/client/gui/battle_control/controllers/view_points_ctrl.pyc': {
        'ViewPointsController.updateAttachedVehicle': (
            'self', 'vehicleID'),
        'ViewPointsController.switch': ('self', 'isNext'),
    },
    'scripts/client/gui/Scaleform/daapi/view/battle/shared/markers2d/'
    'plugins.pyc': {
        'VehicleMarkerPlugin.__init__': ('self', 'parentObj', 'clazz'),
        'VehicleMarkerPlugin.init': ('self', '*args'),
        'VehicleMarkerPlugin.start': ('self',),
        'VehicleMarkerPlugin.stop': ('self',),
        'VehicleMarkerPlugin.__onVehicleFeedbackReceived': (
            'self', 'eventID', 'vehicleID', 'value'),
        'VehicleMarkerPlugin.__updateVehicleHealth': (
            'self', 'handle', 'newHealth', 'aInfo', 'attackReasonID'),
    },
    'scripts/client/gui/Scaleform/daapi/view/battle/shared/markers2d/'
    'markers.pyc': {
        'VehicleMarker.attach': ('self', 'vProxy'),
        'VehicleMarker.fetchMatrixProvider': ('cls', 'vProxy'),
        'VehicleMarker.getHealth': ('self',),
        'VehicleMarker.isAlive': ('self',),
    },
    'scripts/client/AvatarPositionControl.pyc': {
        'ConsistentMatrices.__setTarget': ('self', 'matrix', 'asStatic'),
        'ConsistentMatrices.__linkOwnVehicle': ('self', 'vehicle'),
        'AvatarPositionControl.switchViewpoint': (
            'self', 'isViewpoint', 'vehOrPointId'),
    },
    'scripts/client/AvatarInputHandler/DynamicCameras/__init__.pyc': {
        'AccelerationSmoother.update': ('self', 'vehicle', 'deltaTime'),
    },
    'scripts/client/AvatarInputHandler/DynamicCameras/ArcadeCamera.pyc': {
        'ArcadeCamera.create': (
            'self', 'pivotPos', 'onChangeControlMode', 'postmortemMode'),
        'ArcadeCamera.enable': (
            'self', 'preferredPos', 'closesDist', 'postmortemParams',
            'turretYaw', 'gunPitch'),
        'ArcadeCamera.__calcCurOscillatorAcceleration': (
            'self', 'deltaTime'),
        'ArcadeCamera.__setVehicleMProv': ('self', 'vehicleMProv'),
        'ArcadeCamera.__getVehicleMProv': ('self',),
        'ArcadeCamera.setToVehicleDirection': ('self',),
    },
    'scripts/common/BattleFeedbackCommon.pyc': {
        'BATTLE_EVENT_TYPE.packVisibility': ('isVisible', 'isDirect'),
    },
    'scripts/client/gui/battle_control/controllers/feedback_events.pyc': {
        'PlayerFeedbackEvent.fromDict': ('battleEventData',),
    },
    'scripts/client/gui/Scaleform/daapi/view/battle/shared/'
    'ribbons_aggregator.pyc': {
        '_EnemyDetectionRibbon.getType': ('self',),
    },
    'scripts/client/AvatarInputHandler/DynamicCameras/SniperCamera.pyc': {
        'SniperCamera.__calcCurOscillatorAcceleration': (
            'self', 'deltaTime'),
    },
    'scripts/client/AvatarInputHandler/AimingSystems/'
    'ArcadeAimingSystem.pyc': {
        'ArcadeAimingSystem.__setVehicleMProv': ('self', 'value'),
        'ArcadeAimingSystem.enable': (
            'self', 'targetPos', 'turretYaw', 'gunPitch'),
        'ArcadeAimingSystem.focusOnPos': ('self', 'preferredPos'),
    },
    'scripts/client/AvatarInputHandler/AimingSystems/'
    'SniperAimingSystem.pyc': {
        'SniperAimingSystem.enable': (
            'self', 'targetPos', 'playerGunMatFunction'),
        'SniperAimingSystem.focusOnPos': ('self', 'preferredPos'),
        'SniperAimingSystem.__worldYawPitchToTurret': (
            'self', 'worldYaw', 'worldPitch'),
    },
    'scripts/client/AvatarInputHandler/AimingSystems/'
    'steady_vehicle_matrix.pyc': {
        'SteadyVehicleMatrixCalculator.relinkSources': ('self',),
    },
    'scripts/client/gui/battle_control/matrix_factory.pyc': {
        'makeArcadeCameraMatrix': (),
        'makeAttachedVehicleMatrix': (),
        'makeOwnVehicleMatrix': (),
    },
    'scripts/common/items/VehicleDescrCrew.pyc': {
        'VehicleDescrCrew.__init__': (
            'self', 'vehicleDescr', 'crewCompactDescrs',
            'mainSkillQualifiersApplier', 'activityFlags', 'isFire',
            'stunFactors'),
        'VehicleDescrCrew._validateAndComputeCrew': ('self',),
        'VehicleDescrCrew._processSkills': (
            'self', 'skillEfficiencies', 'commonLevelIncrease',
            'nonCommanderLevelIncrease'),
        'VehicleDescrCrew.onCollectFactors': ('self', 'factors'),
        'VehicleDescrCrew.onCollectShotDispersionFactors': (
            'self', 'factors'),
        'VehicleDescrCrew._updateCommanderFactors': (
            'self', 'factor', 'baseAvgLevel'),
        'VehicleDescrCrew._updateGunnerFactors': (
            'self', 'factor', 'baseAvgLevel'),
        'VehicleDescrCrew._updateLoaderFactors': (
            'self', 'factor', 'baseAvgLevel'),
        'VehicleDescrCrew._updateDriverFactors': (
            'self', 'factor', 'baseAvgLevel'),
        'VehicleDescrCrew._updateRadiomanFactors': (
            'self', 'factor', 'baseAvgLevel'),
        'VehicleDescrCrew._updateRepairFactors': (
            'self', 'factor', 'baseAvgLevel'),
        'VehicleDescrCrew._updateCamouflageFactors': (
            'self', 'factor', 'baseAvgLevel'),
    },
    # The single dataset the garage panel and the battle law share.
    'scripts/common/items/utils.pyc': {
        'makeDefaultVehicleAttributeFactors': (),
        'updateAttrFactorsWithSplit': (
            'vehicleDescr', 'crewCompactDescrs', 'eqs', 'factors'),
        'updateVehicleAttrFactors': (
            'vehicleDescr', 'crewCompactDescrs', 'eqs', 'factors', 'aspect'),
        'generateDefaultCrew': ('vehicleType', 'level'),
        'getCircularVisionRadius': ('vehicleDescr', 'factors'),
        'getRadioDistance': ('vehicleDescr', 'factors'),
        'getReloadTime': ('vehicleDescr', 'factors'),
        'getGunAimingTime': ('vehicleDescr', 'factors'),
        'getTurretRotationSpeed': ('vehicleDescr', 'factors'),
        'getGunRotationSpeed': ('vehicleDescr', 'factors'),
        'getChassisRotationSpeed': ('vehicleDescr', 'factors'),
        'getClientShotDispersion': ('vehicleDescr', 'shotDispersionFactor'),
        'getInvisibility': ('factors', 'baseInvisibility', 'isMoving'),
        'getClientInvisibility': (
            'vehicleDescr', 'vehicle', 'camouflageFactor', 'factors'),
    },
    'scripts/common/items/item_price.pyc': {
        'getNextSlotPrice': ('slots', 'slotsPrices'),
        'getNextBerthPackPrice': ('berths', 'berthsPrices'),
    },
    'scripts/client/gui/shared/items_parameters/functions.pyc': {
        'extractCrewDescrs': ('vehicle', 'replaceNone'),
    },
    'scripts/client/gui/shared/utils/requesters/ItemsRequester.pyc': {
        'ItemsRequester.getItemsEx': (
            'self', 'itemTypeIDs', 'criteria', 'nationID'),
    },
    'scripts/client/gui/shared/utils/requesters/parsers/ShopDataParser.pyc': {
        'ShopDataParser.getItemsIterator': (
            'self', 'nationID', 'itemTypeID'),
    },
    'scripts/client/gui/shared/gui_items/Vehicle.pyc': {
        'Vehicle.descriptor': ('self',),
        'Vehicle._calcCrewBonuses': ('self', 'crew', 'proxy'),
        'Vehicle._buildCrew': ('self', 'crew', 'proxy'),
        'Vehicle._parseShells': (
            'self', 'layoutList', 'defaultLayoutList', 'proxy'),
        'Vehicle.shells': ('self',),
        'Vehicle.equipment': ('self',),
        'Vehicle.isLocked': ('self',),
        'Vehicle.typeOfLockingArena': ('self',),
    },
    'scripts/client/gui/shared/gui_items/vehicle_modules.pyc': {
        'Shell.__init__': (
            'self', 'intCompactDescr', 'count', 'defaultCount', 'proxy',
            'isBoughtForCredits'),
        'Shell.count': ('self',),
    },
    'scripts/client/gui/shared/gui_items/vehicle_equipment.pyc': {
        'VehicleEquipment.regularConsumables': ('self',),
        # Appends the battle-booster slot, so an equipment payload is four
        # wide while the published garage holds three regular slots.
        'VehicleEquipment.getConsumablesIntCDs': ('self', 'default'),
        '_VehicleConsumables.getIntCDs': ('self', 'default'),
        '_VehicleConsumables.getInstalledItems': ('self',),
    },
    'scripts/client/account_helpers/Inventory.pyc': {
        'Inventory.equipEquipments': ('self', 'vehInvID', 'eqs', 'callback'),
        'Inventory.setAndFillLayouts': (
            'self', 'vehInvID', 'shellsLayout', 'eqsLayout', 'equipmentType',
            'callback'),
        'Inventory.__setAndFillLayouts_onShopSynced': (
            'self', 'vehInvID', 'shellsLayout', 'equipmentType', 'eqsLayout',
            'callback', 'resultID', 'shopRev'),
    },
    'scripts/client/gui/battle_control/controllers/consumables/'
    'ammo_ctrl.pyc': {
        'AmmoController.changeSetting': ('self', 'intCD', 'avatar'),
        'AmmoController.getNextSettingCode': ('self', 'intCD'),
        'AmmoController.setNextShellCD': ('self', 'intCD'),
        'AmmoController.setCurrentShellCD': ('self', 'intCD'),
    },
    'scripts/client/account_helpers/AccountSettings.pyc': {
        'AccountSettings.__readSection': ('ds', 'name'),
        'AccountSettings.__readUserSection': (),
    },
    'scripts/client/gui/Scaleform/daapi/view/lobby/hangar/'
    'AmmunitionPanel.pyc': {
        'getFittingSlotsData': (
            'vehicle', 'slotsRange', 'VoClass', 'itemsCache'),
    },
    'scripts/client/gui/Scaleform/daapi/view/lobby/shared/'
    'fitting_slot_vo.pyc': {
        'FittingSlotVO._prepareModule': (
            'self', 'modulesData', 'vehicle', 'slotType', 'slotId'),
        'HangarFittingSlotVO._prepareModule': (
            'self', 'modulesData', 'vehicle', 'slotType', 'slotId'),
    },
    'scripts/client/Avatar.pyc': {
        'ClientVisibilityFlags.updateSpaceVisibility': (
            'spaceID', 'clientVisibilityFlags'),
        'PlayerAvatar.__init__': ('self',),
        'PlayerAvatar.onBecomePlayer': ('self',),
        'PlayerAvatar.onBecomeNonPlayer': ('self',),
        'PlayerAvatar.onEnterWorld': ('self', 'prereqs'),
        'PlayerAvatar.onLeaveWorld': ('self',),
        'PlayerAvatar.onPrereqsLoaded': (
            'self', 'resNames', 'resourceRefs'),
        'PlayerAvatar.leaveArena': ('self',),
        'PlayerAvatar.onVehicleChanged': ('self',),
        'PlayerAvatar.onCmdResponse': (
            'self', 'requestID', 'resultID', 'errorStr'),
        'PlayerAvatar.onTokenReceived': (
            'self', 'requestID', 'tokenType', 'data'),
        'PlayerAvatar.receiveAccountStats': (
            'self', 'requestID', 'stats'),
        'PlayerAvatar.prerequisites': ('self',),
        'PlayerAvatar.set_playerVehicleID': ('self', 'prev'),
        'PlayerAvatar.__onSetOwnVehicleAuxPhysicsData': ('self', 'prev'),
        'PlayerAvatar.__onArenaPeriodChange': (
            'self', 'period', 'periodEndTime', 'periodLength',
            'periodAdditionalInfo'),
        'PlayerAvatar.__setIsOnArena': ('self', 'onArena'),
        'PlayerAvatar.__onInitStepCompleted': ('self',),
        'PlayerAvatar.moveVehicle': ('self', 'flags', 'isKeyDown'),
        'PlayerAvatar.handleVehicleCollidedVehicle': (
            'self', 'vehA', 'vehB', 'hitPt', 'time'),
        'PlayerAvatar.enableOwnVehicleAutorotation': ('self', 'enable'),
        'PlayerAvatar.autoAim': ('self', 'target'),
        'PlayerAvatar.targetFocus': ('self', 'entity'),
        'PlayerAvatar.shoot': ('self', 'isRepeat'),
        'PlayerAvatar.__isOwnVehicleSwitchingSiegeMode': ('self',),
        'PlayerAvatar.cancelWaitingForShot': ('self',),
        'PlayerAvatar.__showTimedOutShooting': ('self',),
        'PlayerAvatar.vehicle_onEnterWorld': ('self', 'vehicle'),
        'PlayerAvatar.__startVehicleVisual': ('self', 'vehicle'),
        'PlayerAvatar.getOwnVehicleMatrix': ('self',),
        'PlayerAvatar.getOwnVehicleStabilisedMatrix': ('self',),
        'PlayerAvatar.updateVehicleHealth': (
            'self', 'vehicleID', 'health', 'deathReasonID', 'isCrewActive',
            'isRespawn'),
        'PlayerAvatar.updateVehicleGunReloadTime': (
            'self', 'vehicleID', 'timeLeft', 'baseTime'),
        'PlayerAvatar.updateVehicleAmmo': (
            'self', 'vehicleID', 'compactDescr', 'quantity',
            'quantityInClip', 'timeRemaining'),
        'PlayerAvatar.__processVehicleEquipments': (
            'self', 'vehicleID', 'compactDescr', 'quantity',
            'stage', 'timeRemaining'),
        'PlayerAvatar.updateVehicleSetting': (
            'self', 'vehicleID', 'code', 'value'),
        'PlayerAvatar.updateVehicleMiscStatus': (
            'self', 'vehicleID', 'code', 'intArg', 'floatArgs'),
        'PlayerAvatar.__onSiegeStateUpdated': (
            'self', 'vehicleID', 'newState', 'timeToNextState'),
        'PlayerAvatar.updateVehicleDestroyTimer': (
            'self', 'code', 'period', 'warnLvl'),
        'PlayerAvatar.showVehicleDamageInfo': (
            'self', 'vehicleID', 'damageIndex', 'extraIndex', 'entityID',
            'equipmentID'),
        'PlayerAvatar.showOwnVehicleHitDirection': (
            'self', 'hitDirYaw', 'attackerID', 'damage', 'crits',
            'isBlocked', 'isShellHE', 'damagedID'),
        'PlayerAvatar.updateOwnVehiclePosition': (
            'self', 'position', 'direction', 'speed', 'rspeed'),
        'PlayerAvatar.getOwnVehicleSpeeds': (
            'self', 'getInstantaneous'),
        'PlayerAvatar.updateTargetingInfo': (
            'self', 'turretYaw', 'gunPitch', 'maxTurretRotationSpeed',
            'maxGunRotationSpeed', 'shotDispMultiplierFactor',
            'gunShotDispersionFactorsTurretRotation',
            'chassisShotDispersionFactorsMovement',
            'chassisShotDispersionFactorsRotation', 'aimingTime'),
        'PlayerAvatar.updateGunMarker': (
            'self', 'vehicleID', 'shotPos', 'shotVec', 'dispersionAngle'),
        'PlayerAvatar.onBattleEvents': ('self', 'events'),
        'PlayerAvatar.getOwnVehicleShotDispersionAngle': (
            'self', 'turretRotationSpeed', 'withShot'),
        'PlayerAvatar.stopTracer': ('self', 'shotID', 'endPoint'),
        'PlayerAvatar.explodeProjectile': (
            'self', 'shotID', 'effectsIndex', 'effectMaterialIndex',
            'endPoint', 'velocityDir', 'damagedDestructibles'),
        'PlayerAvatar.syncVehicleAttrs': ('self', 'attrs'),
        'PlayerAvatar.updateArena': ('self', 'updateType', 'argStr'),
        'PlayerAvatar.onRoundFinished': ('self', 'winnerTeam', 'reason'),
    },
    'scripts/client/AvatarInputHandler/__init__.pyc': {
        'AvatarInputHandler.__constructComponents': ('self',),
        'AvatarInputHandler.onControlModeChanged': (
            'self', 'eMode', '**args'),
        'AvatarInputHandler.activatePostmortem': (
            'self', 'isRespawn'),
        'AvatarInputHandler.__onArenaStarted': (
            'self', 'period', '*args'),
        'AvatarInputHandler.getAutorotation': ('self',),
        'AvatarInputHandler.setAutorotation': ('self', 'bValue'),
        '_Targeting.__init__': ('self',),
        '_Targeting.getTargetEntity': ('self',),
        '_Targeting.enable': ('self', 'flag'),
        '_Targeting.onRecreateDevice': ('self',),
    },
    'scripts/client/AvatarInputHandler/commands/siege_mode_control.pyc': {
        'SiegeModeControl.handleKeyEvent': (
            'self', 'isDown', 'key', 'mods', 'event'),
        'SiegeModeControl.notifySiegeModeChanged': (
            'self', 'vehicle', 'newState', 'timeToNextMode'),
        'SiegeModeControl.__switchSiegeMode': ('self',),
    },
    'scripts/client/AvatarInputHandler/control_modes.pyc': {
        'ArcadeControlMode.handleKeyEvent': (
            'self', 'isDown', 'key', 'mods', 'event'),
        'SniperControlMode.handleKeyEvent': (
            'self', 'isDown', 'key', 'mods', 'event'),
        'SniperControlMode.__siegeModeStateChanged': (
            'self', 'newState', 'timeToNewMode'),
        'PostMortemControlMode.enable': ('self', '**args'),
        'PostMortemControlMode.handleKeyEvent': (
            'self', 'isDown', 'key', 'mods', 'event'),
        'PostMortemControlMode.__switch': ('self', 'isNext'),
        'PostMortemControlMode.curPostmortemDelay': ('self',),
    },
    'scripts/client/AvatarInputHandler/PostmortemDelay.pyc': {
        'PostmortemDelay.start': ('self',),
        'PostmortemDelay.__moveCameraTo': (
            'self', 'vehicleID', 'sourceVehicleID'),
    },
    'scripts/client/CommandMapping.pyc': {
        'CommandMapping.isFired': ('self', 'command', 'key'),
    },
    'scripts/client/Vehicle.pyc': {
        'Vehicle.__init__': ('self',),
        'Vehicle.__startWGPhysics': ('self',),
        'Vehicle.getSpeed': ('self',),
        'Vehicle.onPushed': ('self', 'x', 'z'),
        'Vehicle.prerequisites': ('self', 'respawnCompactDescr'),
        'Vehicle.onEnterWorld': ('self', 'prereqs'),
        'Vehicle.onLeaveWorld': ('self',),
        'Vehicle.showShooting': ('self', 'burstCount', 'isPredictedShot'),
        'Vehicle.showAmmoBayEffect': (
            'self', 'mode', 'fireballVolume', 'projectedTurretSpeed'),
        'Vehicle.set_health': ('self', 'prev'),
        'Vehicle.set_isCrewActive': ('self', 'prev'),
        'Vehicle.set_gunAnglesPacked': ('self', 'prev'),
        'Vehicle.set_siegeState': ('self', 'prev'),
        'Vehicle.onSiegeStateUpdated': (
            'self', 'newState', 'timeToNextMode'),
        'Vehicle.getServerGunAngles': ('self',),
        'Vehicle.getAimParams': ('self',),
        'Vehicle.collideSegmentExt': ('self', 'startPoint', 'endPoint'),
        'Vehicle.drawEdge': ('self', 'forceSimpleEdge'),
        'Vehicle.onHealthChanged': (
            'self', 'newHealth', 'attackerID', 'attackReasonID'),
    },
    'scripts/client/VehicleGunRotator.pyc': {
        'VehicleGunRotator.start': ('self',),
        'VehicleGunRotator.reset': ('self',),
        'VehicleGunRotator.update': (
            'self', 'turretYaw', 'gunPitch', 'maxTurretRotationSpeed',
            'maxGunRotationSpeed'),
        'VehicleGunRotator.setShotPosition': (
            'self', 'vehicleID', 'shotPos', 'shotVec',
            'dispersionAngle', 'forceValueRefresh'),
        'VehicleGunRotator.__syncWithServerTurretYaw': (
            'self', 'turretYaw'),
        'VehicleGunRotator.__trackPointOnServer': (
            'self', 'shotPoint'),
        'VehicleGunRotator.getAvatarOwnVehicleStabilisedMatrix': ('self',),
        'VehicleGunRotator.getCurShotPosition': ('self',),
        'VehicleGunRotator.__getShotPosition': (
            'self', 'turretYaw', 'gunPitch'),
        'VehicleGunRotator.__getTurretYawLimits': ('self',),
        'VehicleGunRotator.__rotate': ('self', 'shotPoint', 'timeDiff'),
    },
    'scripts/client/AreaDestructibles.pyc': {
        'init': (),
        'clear': (),
        '_printErrDescNotAvailable': (
            'spaceID', 'chunkID', 'destrIndex'),
        'ClientDestructiblesCache.getDestructibleDesc': (
            'self', 'spaceID', 'chunkID', 'destrIndex'),
        'AreaDestructibles.set_fallenTrees': ('self', 'prev'),
        'AreaDestructibles.set_fallenColumns': ('self', 'prev'),
        'AreaDestructibles.set_destroyedFragiles': ('self', 'prev'),
        'AreaDestructibles.set_destroyedModules': ('self', 'prev'),
        'DestructiblesManager.startSpace': ('self', 'spaceID'),
        'DestructiblesManager.getSpaceID': ('self',),
        'DestructiblesManager.getController': ('self', 'chunkID'),
        'DestructiblesManager.onChunkLoad': (
            'self', 'chunkID', 'numDestructibles'),
        'DestructiblesManager.isChunkLoaded': ('self', 'chunkID'),
        'DestructiblesManager.orderDestructibleDestroy': (
            'self', 'chunkID', 'dmgType', 'destrData',
            'isNeedAnimation', 'syncWithProjectile'),
        'DestructiblesManager.__destroyDestructible': (
            'self', 'chunkID', 'dmgType', 'destData',
            'isNeedAnimation', 'explosionInfo'),
        'DestructiblesManager.__dropDestructible': (
            'self', 'chunkID', 'destrIndex', 'dmgType', 'fallDirYaw',
            'pitchConstr', 'fallSpeed', 'isAnimate',
            'obstacleCollisionFlags'),
        'DestructiblesManager.__launchTreeFallEffect': (
            'self', 'chunkID', 'destrIndex', 'effectName', 'fallDirYaw'),
        'DestructiblesManager.__getDestrInitialMatrix': (
            'self', 'chunkID', 'destrIndex'),
        '_DestructiblesAnimator.showFall': (
            'self', 'spaceID', 'chunkID', 'destrIndex', 'fallDirYaw',
            'pitchConstr', 'discreteInitSpeed', 'isNeedAnimation',
            'initialMatrix', 'touchdownCallback'),
        '_DestructiblesAnimator.showFallTree': (
            'self', 'spaceID', 'chunkID', 'destrIndex', 'fallDirYaw',
            'pitchConstr', 'discreteInitSpeed', 'isNeedAnimation',
            'initialMatrix', 'touchdownCallback'),
        '_DestructiblesAnimator.__moveBody': ('self', 'body', 'dt'),
        '_DestructiblesAnimator.__positionBodyModel': ('self', 'body'),
        '_DestructiblesAnimator.__update': ('self', 'dt'),
    },
    'scripts/client/helpers/EffectMaterialCalculation.pyc': {
        'calcSurfaceMaterialNearPoint': (
            'point', 'normal', 'spaceID', 'defaultEffectMaterial'),
        'isDestructibleBroken': (
            'chunkID', 'itemIndex', 'matKind', 'itemFilename'),
    },
    'scripts/common/DestructiblesCache.pyc': {
        'encodeFallenColumn': ('destrIndex', 'fallYaw', 'fallSpeed'),
        'encodeFallenTree': (
            'destrIndex', 'fallYaw', 'fallPitchConstr', 'fallSpeed'),
        'encodeDestructibleModule': (
            'destrID', 'matKind', 'isShotDamage'),
        'encodeFragile': ('destrID', 'isShotDamage'),
        'decodeFragile': ('data',),
    },
    'scripts/client/VehicleEffects.pyc': {
        'DamageFromShotDecoder.decodeSegment': (
            'segment', 'vehicleDescr'),
    },
    'scripts/client/vehicle_systems/CompoundAppearance.pyc': {
        'CompoundAppearance.start': ('self', 'prereqs'),
        'CompoundAppearance.__linkCompound': ('self',),
        'CompoundAppearance.__onModelsRefresh': (
            'self', 'modelState', 'resourceList'),
        'CompoundAppearance.setupGunMatrixTargets': ('self', 'target'),
        'CompoundAppearance.changeVisibility': ('self', 'modelVisible'),
        'CompoundAppearance.deactivate': ('self', 'stopEffects'),
        'CompoundAppearance.addCrashedTrack': ('self', 'isLeft'),
        'CompoundAppearance.delCrashedTrack': ('self', 'isLeft'),
        'CompoundAppearance.addDamageSticker': (
            'self', 'code', 'componentName', 'stickerID',
            'segStart', 'segEnd'),
    },
    'scripts/client/VehicleStickers.pyc': {
        'VehicleStickers.addDamageSticker': (
            'self', 'code', 'componentName', 'stickerID',
            'segStart', 'segEnd'),
    },
    'scripts/client/vehicle_systems/components/CrashedTracks.pyc': {
        'CrashedTrackController.__setupTrackAssembler': ('self', 'entity'),
        'CrashedTrackController.__onModelLoaded': ('self', 'resources'),
    },
    'scripts/client/gui/battle_control/controllers/consumables/'
    'equipment_ctrl.pyc': {
        '_ExpandedItem.getActivationCode': (
            'self', 'entityName', 'avatar'),
        '_ExtinguisherItem.getActivationCode': (
            'self', 'entityName', 'avatar'),
        'EquipmentsController.setEquipment': (
            'self', 'intCD', 'quantity', 'stage', 'timeRemaining'),
    },
    'scripts/client/OfflineMapCreator.pyc': {
        'OfflineMapCreator.create': ('self', 'mapName'),
        'OfflineMapCreator.destroy': ('self',),
        'OfflineMapCreator.cancel': ('self',),
        'OfflineMapCreator.Active': ('self',),
        'OfflineMapCreator.SetActive': ('self', '_active'),
        'OfflineMapCreator.__setupCamera': ('self',),
    },
    'scripts/client/connection_mgr.pyc': {
        'ConnectionManager.initiateConnection': (
            'self', 'params', 'password', 'serverName'),
        'ConnectionManager.disconnect': ('self',),
    },
    'scripts/client/gui/app_loader/loader.pyc': {
        '_AppLoader.getDefLobbyApp': ('self',),
        '_AppLoader.getSpaceID': ('self',),
        '_AppLoader.showBattleLoading': ('self',),
        '_AppLoader.showBattlePage': ('self',),
        '_AppLoader.showLobby': ('self',),
    },
    'scripts/client/gui/app_loader/states.pyc': {
        'LobbyState.getSpaceID': ('self',),
        'LobbyState._getNextState': ('self', 'ctx'),
        'BattleLoadingState.getSpaceID': ('self',),
        'BattleLoadingState._getNextState': ('self', 'ctx'),
        'BattleLoadingState._createBattleState': ('self',),
        'BattleLoadingState.showGUI': (
            'self', 'appFactory', 'appNS', 'appState'),
        'BattleState.getSpaceID': ('self',),
        'BattleState._getNextState': ('self', 'ctx'),
        'LoginState.init': ('self', 'ctx'),
        'LoginState.update': ('self', 'ctx'),
        'LoginState._clearEntitiesAndSpaces': (),
    },
    'scripts/client/gui/battle_control/controllers/arena_load_ctrl.pyc': {
        'ArenaLoadController.invalidateArenaInfo': ('self',),
        'ArenaLoadController.arenaLoadCompleted': ('self',),
    },
    'scripts/client/gui/battle_control/controllers/period_ctrl.pyc': {
        'ArenaPeriodController._calculate': ('self',),
        'ArenaPeriodController.__tick': ('self',),
    },
    'scripts/client/gui/battle_control/controllers/vehicle_state_ctrl.pyc': {
        '_SpeedStateHandler._invalidate': ('self', 'vehicle'),
    },
    'scripts/client/gui/battle_control/controllers/repositories.pyc': {
        'BattleSessionSetup.arenaDP': ('self',),
        'SharedControllersLocator.arenaLoad': ('self',),
    },
    'scripts/client/gui/battle_control/controllers/debug_ctrl.pyc': {
        'DebugController._update': ('self',),
    },
    'scripts/client/gui/Scaleform/daapi/view/battle/shared/'
    'debug_panel.pyc': {
        'DebugPanel.updateDebugInfo': (
            'self', 'ping', 'fps', 'isLaggingNow', 'fpsReplay'),
    },
    'scripts/client/gui/Scaleform/daapi/view/lobby/LobbyView.pyc': {
        'LobbyView._populate': ('self',),
    },
    'scripts/client/gui/Scaleform/daapi/view/lobby/header/'
    'LobbyHeader.pyc': {
        'LobbyHeader.fightClick': ('self', 'mapID', 'actionName'),
        'LobbyHeader._updatePrebattleControls': ('self',),
        'LobbyHeader._checkFightButtonDisabled': (
            'self', 'canDo', 'isFightButtonForcedDisabled'),
        'LobbyHeader.__handleFightButtonUpdated': ('self', '_'),
        'LobbyHeader.__addListeners': ('self',),
    },
    'scripts/client/gui/Scaleform/daapi/view/meta/LobbyHeaderMeta.pyc': {
        'LobbyHeaderMeta.as_disableFightButtonS': (
            'self', 'isDisabled'),
    },
    'scripts/client/gui/Scaleform/framework/entities/'
    'BaseDAAPIModule.pyc': {
        'BaseDAAPIModule.setFlashObject': (
            'self', 'movieClip', 'autoPopulate', 'setScript'),
    },
    'scripts/client/gui/Scaleform/framework/entities/DAAPIEntity.pyc': {
        'DAAPIEntity.turnDAAPIon': ('self', 'setScript', 'movieClip'),
    },
    'scripts/client/gui/prb_control/events_dispatcher.pyc': {
        'EventDispatcher.updateUI': ('self', 'loadedAlias'),
    },
    'scripts/client/gui/shared/event_bus.pyc': {
        'EventBus.handleEvent': ('self', 'event', 'scope'),
    },
    'scripts/client/gui/Scaleform/framework/application.pyc': {
        'SFApplication.loadView': (
            'self', 'loadParams', '*args', '**kwargs'),
    },
    'scripts/client/gui/Scaleform/framework/managers/loaders.pyc': {
        'ViewLoadParams.__init__': (
            'self', 'alias', 'name', 'loadMode'),
    },
    'scripts/client/gui/Scaleform/daapi/view/lobby/trainings/'
    'TrainingSettingsWindow.pyc': {
        'TrainingSettingsWindow.__init__': ('self', 'ctx'),
        'TrainingSettingsWindow.getMapsData': ('self',),
        'TrainingSettingsWindow.getInfo': ('self',),
        'TrainingSettingsWindow.onWindowClose': ('self',),
        'TrainingSettingsWindow.updateTrainingRoom': (
            'self', 'arena', 'roundLength', 'isPrivate', 'comment'),
    },
    'scripts/client/gui/Scaleform/daapi/view/meta/TrainingWindowMeta.pyc': {
        'TrainingWindowMeta.as_setDataS': ('self', 'info', 'mapsData'),
    },
    'scripts/client_common/ClientArena.pyc': {
        'ClientArena.__init__': (
            'self', 'arenaUniqueID', 'arenaTypeID', 'arenaBonusType',
            'arenaGuiType', 'arenaExtraData', 'weatherPresetID'),
        'ClientArena.update': ('self', 'updateType', 'argStr'),
        'ClientArena.__onBasePointsUpdate': ('self', 'argStr'),
        'ClientArena.__onBaseCaptured': ('self', 'argStr'),
        'ClientArena.__onVehicleListUpdate': ('self', 'argStr'),
        'ClientArena.__onVehicleAddedUpdate': ('self', 'argStr'),
        'ClientArena.__onPeriodInfoUpdate': ('self', 'argStr'),
        'ClientArena.__onVehicleKilled': ('self', 'argStr'),
        'ClientArena.__onVehicleStatisticsUpdate': ('self', 'argStr'),
        'ClientArena.__vehicleStatisticsAsDict': ('self', 'stats'),
        'ClientArena.__onTeamKiller': ('self', 'argStr'),
        'ClientArena.__onAvatarReady': ('self', 'argStr'),
    },
    'scripts/client/gui/battle_control/arena_info/listeners.pyc': {
        'ArenaTeamBasesListener.__arena_onTeamBasePointsUpdate': (
            'self', 'team', 'baseID', 'points', 'timeLeft',
            'invadersCnt', 'capturingStopped'),
        'ArenaTeamBasesListener.__arena_onTeamBaseCaptured': (
            'self', 'team', 'baseID'),
    },
    'scripts/client/gui/battle_control/arena_info/interfaces.pyc': {
        'ITeamsBasesController.invalidateTeamBasePoints': (
            'self', 'baseTeam', 'baseID', 'points', 'timeLeft',
            'invadersCnt', 'capturingStopped'),
        'ITeamsBasesController.invalidateTeamBaseCaptured': (
            'self', 'baseTeam', 'baseID'),
    },
    'scripts/client/gui/battle_control/controllers/team_bases_ctrl.pyc': {
        'BattleTeamsBasesController.invalidateTeamBasePoints': (
            'self', 'baseTeam', 'baseID', 'points', 'timeLeft',
            'invadersCnt', 'capturingStopped'),
        'BattleTeamsBasesController.invalidateTeamBaseCaptured': (
            'self', 'baseTeam', 'baseID'),
    },
    'scripts/client/gui/game_control/RefSystem.pyc': {
        '_getRefSysCfg': ('itemsCache',),
        'RefSystem.__update': ('self', 'data'),
    },
    'scripts/client/gui/game_control/state_tracker.pyc': {
        'GameStateTracker.init': ('self',),
        'GameStateTracker.fini': ('self',),
        'GameStateTracker.onAccountShowGUI': ('self', 'ctx'),
        'GameStateTracker.onLobbyInited': ('self', 'event'),
        'GameStateTracker.onLobbyStarted': ('self', 'ctx'),
        'GameStateTracker._invoke': ('self', 'method', '*args'),
    },
    'scripts/client/gui/shared/personality.pyc': {
        'onAccountShowGUI': ('ctx',),
        'onCenterIsLongDisconnected': ('isLongDisconnected',),
    },
    'scripts/client/gui/shared/utils/HangarSpace.pyc': {
        '_HangarSpace.inited': ('self',),
        '_HangarSpace.spaceInited': ('self',),
        '_HangarSpace.getVehicleEntity': ('self',),
    },
    'scripts/client/CurrentVehicle.pyc': {
        '_CachedVehicle.isPresent': ('self',),
        '_CurrentVehicle.item': ('self',),
    },
    'scripts/client/gui/ClientHangarSpace.pyc': {
        'ClientHangarSpace.create': (
            'self', 'isPremium', 'onSpaceLoadedCallback'),
        'ClientHangarSpace.getVehicleEntity': ('self',),
        '_VehicleAppearance.__startBuild': ('self', 'vDesc', 'vState'),
        '_VehicleAppearance.__onResourcesLoaded': (
            'self', 'buildInd', 'resourceRefs'),
        '_VehicleAppearance.__doFinalSetup': ('self', 'buildIdx', 'model'),
    },
    'scripts/client/gui/prb_control/dispatcher.pyc': {
        '_PrbControlLoader.createBattleDispatcher': ('self',),
        '_PrbControlLoader.getDispatcher': ('self',),
    },
    'scripts/client/gui/prb_control/__init__.pyc': {
        'prbDispatcherProperty.__get__': ('self', 'obj', 'objType'),
    },
    'scripts/client/SoundGroups.pyc': {
        'SoundGroups.destroy': ('self',),
    },
    'scripts/client/helpers/server_settings.pyc': {
        'ServerSettings.isElenEnabled': ('self',),
    },
    'scripts/client/gui/Scaleform/daapi/view/login/EULADispatcher.pyc': {
        'EULADispatcher.processLicense': ('self', 'callback'),
        'EULADispatcher.__saveVersionFile': ('self',),
    },
}


# Signatures cannot describe dictionary payloads or dynamic getattr targets.
# These literals are direct string subscripts or native-method names in exact
# #1513 consumers. Producer contract tests verify dictionary payload shapes.
EXPECTED_CODE_LITERALS = {
    'scripts/common/OldSpaceData.pyc': {
        'getPropertyNameForKey': ('itemsVisibilityMask',),
    },
    'scripts/common/items/components/legacy_stuff.pyc': {
        'NoLegacyStuff.get': ('Operation is not allowed',),
        'NoLegacyStuff.__getitem__': ('Operation is not allowed',),
        'NoLegacyStuff.__contains__': ('Operation is not allowed',),
        'NoLegacyStuff.__iter__': ('Operation is not allowed',),
        'NoLegacyStuff.keys': ('Operation is not supported',),
        'NoLegacyStuff.values': ('Operation is not supported',),
        'NoLegacyStuff.items': ('Operation is not supported',),
    },
    'scripts/common/items/vehicles.pyc': {
        '_readSiegeModeParams': (
            'siege_mode', 'switchOnTime', 'switchOffTime',
            'switchCancelEnabled', 'engineDamageCoeff', 'normal',
            'critical', 'destroyed'),
    },
    'scripts/client/Avatar.pyc': {
        'PlayerAvatar.__onSetOwnVehicleAuxPhysicsData': (
            'syncStabilisedYPR',),
    },
    'scripts/client/gui/Scaleform/daapi/view/battle/shared/markers2d/'
    'plugins.pyc': {
        'VehicleMarkerPlugin.__updateVehicleHealth': ('updateHealth',),
    },
    'scripts/client/Vehicle.pyc': {
        'Vehicle.set_gunAnglesPacked': ('syncGunAngles',),
    },
    'scripts/client/ProjectileMover.pyc': {
        'segmentMayHitEntity': ('segmentMayHitEntity',),
    },
    'scripts/client/gui/battle_control/controllers/feedback_events.pyc': {
        'PlayerFeedbackEvent.fromDict': (
            'eventType', 'details', 'targetID', 'count'),
    },
    'scripts/client_common/ClientArena.pyc': {
        'ClientArena.__vehicleStatisticsAsDict': ('frags',),
        'ClientArena.__onTeamKiller': ('isTeamKiller',),
    },
    'scripts/client_common/ClientChat.pyc': {
        'ClientChat.__dataTimeProcessor': ('time', 'sentTime'),
    },
    'scripts/client/account_helpers/QuestProgress.pyc': {
        'QuestProgress.synchronize': ('quests', 'tokens', 'potapovQuests'),
    },
    'scripts/client/account_helpers/Shop.pyc': {
        'Shop.__onSyncDataReceived': ('sellPriceFactor',),
    },
    'scripts/client/gui/shared/utils/requesters/QuestsProgressRequester.pyc': {
        '_PersonalMissionsProgressRequester._response': (
            'potapovQuests', 'compDescr'),
    },
    'scripts/client/gui/game_control/RefSystem.pyc': {
        '_getRefSysCfg': (
            'periods', 'maxReferralXPPool', 'maxNumberOfReferrals'),
        'RefSystem.__update': ('posByXPinTeam',),
    },
    'scripts/client/gui/game_control/state_tracker.pyc': {
        'GameStateTracker.onLobbyStarted': ('onLobbyStarted',),
    },
    'scripts/client/gui/shared/personality.pyc': {
        'onAccountShowGUI': ('rareAchievements',),
    },
    'scripts/client/gui/Scaleform/daapi/view/login/EULADispatcher.pyc': {
        'EULADispatcher.processLicense': ('version',),
        'EULADispatcher.__saveVersionFile': ('version',),
    },
    'scripts/client/helpers/server_settings.pyc': {
        'ServerSettings.isElenEnabled': ('elenSettings', 'isElenEnabled'),
    },
}


# These global/attribute names capture lifecycle semantics that signatures and
# string payload literals cannot express.  They are the exact #1513 APIs the
# offline Account preservation and native lobby-ready gate depend on.
EXPECTED_CODE_NAMES = {
    'scripts/client/game.pyc': {
        'wg_onChunkLoad': (
            'AreaDestructibles', 'g_destructiblesManager', 'getSpaceID',
            'startSpace', 'onChunkLoad'),
        'wg_onChunkLoose': (
            'AreaDestructibles', 'g_destructiblesManager', 'getSpaceID',
            'onChunkLoose'),
    },
    'scripts/common/OldSpaceData.pyc': {
        'getSpaceDataFirstForKey': (
            'getattr', 'BigWorld', 'spaces', 'getPropertyNameForKey'),
        'setSpaceData': (
            'setattr', 'BigWorld', 'spaces', 'getPropertyNameForKey'),
    },
    'scripts/common/ModelHitTester.pyc': {
        'ModelHitTester.__init__': ('bbox',),
        'ModelHitTester.loadBspModel': (
            'WGBspCollisionModel', 'setModelName', 'getBoundingBox', 'bbox'),
        'ModelHitTester.releaseBspModel': ('bbox',),
    },
    'scripts/common/items/vehicles.pyc': {
        '_readSiegeModeParams': (
            'readNonNegativeFloat', 'readBool', 'False', 'IS_CLIENT',
            'VEHICLE_SIEGE_STATE', 'SWITCHING_ON', 'SWITCHING_OFF'),
        'VehicleDescriptor.getHitTesters': (
            'chassis', 'hull', 'turrets', 'hitTester', 'append'),
        'CompositeVehicleDescriptor.onSiegeStateChanged': (
            'VEHICLE_SIEGE_STATE', 'ENABLED', 'VEHICLE_MODE', 'SIEGE',
            'DEFAULT'),
    },
    'scripts/common/physics_shared.pyc': {
        'configurePhysicsMode': (
            'chassis', 'hull', 'hitTester', 'bbox', 'hullPosition'),
    },
    'scripts/common/items/components/legacy_stuff.pyc': {
        'NoLegacyStuff.get': ('AssertionError',),
        'NoLegacyStuff.__getitem__': ('AssertionError',),
        'NoLegacyStuff.__contains__': ('AssertionError',),
        'NoLegacyStuff.__iter__': ('AssertionError',),
        'NoLegacyStuff.keys': ('AssertionError',),
        'NoLegacyStuff.values': ('AssertionError',),
        'NoLegacyStuff.items': ('AssertionError',),
    },
    'scripts/common/items/components/shared_components.pyc': {
        'DeviceHealth.__init__': ('maxHealth', 'maxRegenHealth'),
    },
    'scripts/common/items/vehicle_items.pyc': {
        'InstallableItem.maxHealth': ('healthParams', 'maxHealth'),
        'InstallableItem.maxRegenHealth': (
            'healthParams', 'maxRegenHealth'),
        'Engine.__init__': ('fireStartingChance',),
    },
    'scripts/client/vehicle_systems/model_assembler.pyc': {
        'setupTurretRotations': (
            'compoundModel', 'node', 'turretMatrix', 'gunMatrix'),
        'assembleRecoil': (
            'compoundModel', 'node', 'gunRecoil', 'createGunAnimator'),
        'assembleWaterSensor': (
            'turretPositions', 'topRightCarryingPoint', 'Vehicular',
            'WaterSensor', 'sensorPlaneLink', 'onUnderWaterSwitch'),
    },
    'scripts/client/Account.pyc': {
        'PlayerAccount.onBecomePlayer': (
            'BigWorld', 'clearAllSpaces', 'MouseTargetingMatrix',
            'target', 'source', 'maxDistance', 'skeletonCheckEnabled',
            'caps', 'isEnabled'),
        'PlayerAccount.onBecomeNonPlayer': (
            'chatManager', 'switchPlayerProxy', 'events',
            'onAccountBecomeNonPlayer'),
    },
    'scripts/client/Avatar.pyc': {
        'ClientVisibilityFlags.updateSpaceVisibility': (
            'BigWorld', 'wg_getSpaceItemsVisibilityMask',
            'ClientVisibilityFlags', 'SERVER_MASK',
            'wg_setSpaceItemsVisibilityMask'),
        'PlayerAvatar.enableOwnVehicleAutorotation': (
            'base', 'vehicle_changeSetting', 'VEHICLE_SETTING',
            'AUTOROTATION_ENABLED'),
        'PlayerAvatar.__init__': (
            'Account', 'g_accountRepository', 'intUserSettings',
            'prebattleInvitations'),
        'PlayerAvatar.onBecomePlayer': (
            'BigWorld', 'target', 'caps'),
        'PlayerAvatar.onBecomeNonPlayer': (
            'BigWorld', 'target', 'clear', 'chatManager',
            'switchPlayerProxy', 'g_playerEvents',
            'onAvatarBecomeNonPlayer'),
        'PlayerAvatar.set_playerVehicleID': (
            'BigWorld', 'entity', 'inWorld'),
        'PlayerAvatar.vehicle_onEnterWorld': (
            'playerVehicleID', 'VEHICLE_ENTERED',
            '_PlayerAvatar__onInitStepCompleted'),
        'PlayerAvatar.__startVehicleVisual': (
            '_PlayerAvatar__ownVehicleStabMProv', 'target',
            'stabilisedMatrix', 'matrix'),
        'PlayerAvatar.getOwnVehicleMatrix': (
            'getObservedVehicleMatrix', '_PlayerAvatar__ownVehicleMProv'),
        'PlayerAvatar.getOwnVehicleStabilisedMatrix': (
            '_PlayerAvatar__ownVehicleStabMProv',),
        'PlayerAvatar.__onInitStepCompleted': ('setClientReady',),
        'PlayerAvatar.__onArenaPeriodChange': (
            '_PlayerAvatar__setIsOnArena', 'ARENA_PERIOD', 'BATTLE'),
        'PlayerAvatar.__setIsOnArena': (
            'moveVehicle', 'makeVehicleMovementCommandByKeys'),
        'PlayerAvatar.moveVehicle': (
            'filter', 'notifyInputKeysDown', 'base', 'vehicle_moveWith'),
        'PlayerAvatar.getOwnVehicleSpeeds': (
            'BigWorld', 'entity', 'playerVehicleID', 'speedInfo', 'value'),
        'PlayerAvatar.__onSetOwnVehicleAuxPhysicsData': (
            'unpackAuxVehiclePhysicsData', 'guiSessionProvider',
            'invalidateVehicleState', 'VEHICLE_VIEW_STATE', 'RPM'),
        'PlayerAvatar.showOwnVehicleHitDirection': (
            '_PlayerAvatar__isVehicleAlive', 'guiSessionProvider',
            'addHitDirection'),
        'PlayerAvatar.updateGunMarker': (
            'gunRotator', 'setShotPosition'),
        'PlayerAvatar.onBattleEvents': (
            'guiSessionProvider', 'shared', 'feedback',
            'handleBattleEvents'),
        'PlayerAvatar.getOwnVehicleShotDispersionAngle': (
            'getOwnVehicleSpeeds', 'shotDispersionAngle'),
        'PlayerAvatar.autoAim': (
            'isinstance', 'Vehicle', '_PlayerAvatar__autoAimVehID',
            'publicInfo', 'team', 'isAlive', 'cell', 'autoAim',
            'setAimingMode', 'TARGET_LOCK', 'clientMode', 'onLockTarget',
            'TARGET_LOCKED', 'TARGET_UNLOCKED',
            '_PlayerAvatar__aimingInfo', 'BigWorld', 'time',
            'shotDispersionAngle', 'dispersionAngle', 'activateTrigger',
            'deactivateTrigger', 'AUTO_AIM_AT_VEHICLE'),
        'PlayerAvatar.targetFocus': (
            '_PlayerAvatar__vehicles', 'guiSessionProvider',
            'setTargetInFocus', 'drawEdge'),
        'PlayerAvatar.shoot': (
            'base', 'vehicle_shoot', '_PlayerAvatar__startWaitingForShot',
            '_PlayerAvatar__isOwnVehicleSwitchingSiegeMode'),
        'PlayerAvatar.__isOwnVehicleSwitchingSiegeMode': (
            'BigWorld', 'entity', 'playerVehicleID', 'isStarted',
            'siegeState', 'VEHICLE_SIEGE_STATE', 'SWITCHING'),
        'PlayerAvatar.__showTimedOutShooting': (
            'typeDescriptor', 'gun', 'burst', 'showShooting'),
        'PlayerAvatar.cancelWaitingForShot': (
            'BigWorld', 'cancelCallback', 'setAimingMode',
            'targetLastShotPoint'),
        'PlayerAvatar.updateVehicleMiscStatus': (
            'VEHICLE_MISC_STATUS', 'VEHICLE_DROWN_WARNING',
            'updateVehicleDestroyTimer', 'SIEGE_MODE_STATE_CHANGED',
            'VEHICLE_SIEGE_STATE', 'SWITCHING_ON', 'SWITCHING_OFF',
            'moveVehicleByCurrentKeys', 'SIEGE_MODE',
            '_PlayerAvatar__onSiegeStateUpdated'),
        'PlayerAvatar.__onSiegeStateUpdated': (
            'BigWorld', 'entity', 'typeDescriptor', 'hasSiegeMode',
            'onSiegeStateUpdated', 'isPlayerVehicle'),
        'PlayerAvatar.updateVehicleDestroyTimer': (
            'DROWN_WARNING_LEVEL', 'DANGER', 'CAUTION',
            'guiSessionProvider', 'invalidateVehicleState'),
        'PlayerAvatar.__processVehicleEquipments': (
            'vehicles', 'getItemByCompactDescr', 'guiSessionProvider',
            'equipments', 'setEquipment'),
    },
    'scripts/client/gui/battle_control/controllers/period_ctrl.pyc': {
        'ArenaPeriodController._calculate': (
            '_endTime', 'BigWorld', 'serverTime'),
        'ArenaPeriodController.__tick': (
            '_calculate', '_updateCountdown'),
    },
    'scripts/client/gui/battle_control/controllers/vehicle_state_ctrl.pyc': {
        '_SpeedStateHandler._invalidate': (
            'BigWorld', 'player', 'getOwnVehicleSpeeds', 'speedInfo',
            'value', 'SPEED', 'MAX_SPEED'),
    },
    'scripts/client/gui/battle_control/controllers/view_points_ctrl.pyc': {
        'ViewPointsController.updateAttachedVehicle': (
            '_ViewPointsController__currentVehicleID',),
        'ViewPointsController.switch': (
            'getPlayerVehicleID', 'AliveItemsCollection',
            '_ViewPointsController__currentVehicleID',
            '_ViewPointsController__doSwitch',
            '_ViewPointsController__doSelect'),
    },
    'scripts/client/AvatarInputHandler/control_modes.pyc': {
        'ArcadeControlMode.handleKeyEvent': (
            'CMD_CM_LOCK_TARGET', 'BigWorld', 'target', 'autoAim',
            'CMD_CM_LOCK_TARGET_OFF'),
        'SniperControlMode.handleKeyEvent': (
            'CMD_CM_LOCK_TARGET', 'BigWorld', 'target', 'autoAim',
            'CMD_CM_LOCK_TARGET_OFF'),
        'SniperControlMode.__siegeModeStateChanged': (
            'VEHICLE_SIEGE_STATE', 'ENABLED', 'DISABLED', '_cam',
            'aimingSystem', 'forceFullStabilization'),
        'PostMortemControlMode.enable': (
            '_PostMortemControlMode__cam', 'consistentMatrices',
            'attachedVehicleMatrix', 'vehicleMProv', 'PostmortemDelay',
            '_PostMortemControlMode__postmortemDelay', 'start'),
        'PostMortemControlMode.handleKeyEvent': (
            'CMD_CM_POSTMORTEM_NEXT_VEHICLE',
            '_PostMortemControlMode__switch'),
        'PostMortemControlMode.__switch': (
            'guiSessionProvider', 'shared', 'viewPoints', 'switch'),
        'PostMortemControlMode.curPostmortemDelay': (
            '_PostMortemControlMode__postmortemDelay',),
    },
    'scripts/client/AvatarPositionControl.pyc': {
        'AvatarPositionControl.switchViewpoint': (
            '_AvatarPositionControl__avatar', 'cell',
            'switchViewPointOrBindToVehicle'),
    },
    'scripts/client/AvatarInputHandler/PostmortemDelay.pyc': {
        'PostmortemDelay.start': (
            'BigWorld', 'player', 'playerVehicleID',
            '_PostmortemDelay__moveCameraTo'),
        'PostmortemDelay.__moveCameraTo': (
            'BigWorld', 'entity', 'matrix', 'player', 'playerVehicleID',
            'inputHandler',
            'steadyVehicleMatrixCalculator', 'outputMProv',
            '_PostmortemDelay__setCameraSettings'),
    },
    'scripts/client/AvatarInputHandler/__init__.pyc': {
        'AvatarInputHandler.__constructComponents': (
            'vehicleTypeDescriptor', 'hasSiegeMode', 'SiegeModeControl',
            'siegeModeControl', 'onSiegeStateChanged'),
        'AvatarInputHandler.getAutorotation': (
            '_AvatarInputHandler__isAutorotation',),
        'AvatarInputHandler.setAutorotation': (
            '_AvatarInputHandler__curCtrl',
            'enableSwitchAutorotationMode',
            '_AvatarInputHandler__isAutorotation',
            'enableOwnVehicleAutorotation'),
        'AvatarInputHandler.activatePostmortem': (
            '_CTRL_MODE', 'POSTMORTEM', 'onControlModeChanged'),
        '_Targeting.__init__': (
            'BigWorld', 'target', 'selectionFovDegrees',
            'deselectionFovDegrees', 'maxDistance',
            'skeletonCheckEnabled', 'isEnabled',
            'MouseTargettingMatrix'),
        '_Targeting.getTargetEntity': (
            'BigWorld', 'target', 'entity'),
        '_Targeting.enable': (
            'BigWorld', 'target', 'isEnabled', 'source', 'clear'),
        '_Targeting.onRecreateDevice': (
            'BigWorld', 'target', 'isEnabled', 'clear'),
    },
    'scripts/client/AvatarInputHandler/commands/siege_mode_control.pyc': {
        'SiegeModeControl.handleKeyEvent': (
            'CommandMapping', 'g_instance', 'isFired',
            'CMD_CM_VEHICLE_SWITCH_AUTOROTATION', 'BigWorld', 'player',
            'getVehicleAttached', 'isAlive'),
        'SiegeModeControl.notifySiegeModeChanged': (
            'isPlayerVehicle', 'onSiegeStateChanged'),
        'SiegeModeControl.__switchSiegeMode': (
            'BigWorld', 'player', 'deviceStates',
            'VEHICLE_SIEGE_STATE', 'SWITCHING', 'SWITCHING_ON', 'ENABLED',
            'vehicle_changeSetting', 'VEHICLE_SETTING',
            'SIEGE_MODE_ENABLED'),
    },
    'scripts/client/CommandMapping.pyc': {
        'CommandMapping.isFired': (
            '_CommandMapping__mapping', 'get', 'BigWorld',
            'isKeyDown'),
    },
    'scripts/client/Vehicle.pyc': {
        'Vehicle.__collideSegment': (
            'SegmentCollisionResultExt', 'itemTypeName'),
        'Vehicle.__startWGPhysics': ('filter', 'syncGunAngles', 'speedInfo'),
        'Vehicle.getSpeed': ('_Vehicle__speedInfo', 'value'),
        'Vehicle.getServerGunAngles': (
            'decodeGunAngles', 'gunAnglesPacked', 'typeDescriptor'),
        'Vehicle.getAimParams': (
            'appearance', 'turretMatrix', 'gunMatrix',
            'Math', 'Matrix', 'yaw', 'pitch'),
        'Vehicle.onPushed': ('filter', 'setPosition'),
        'Vehicle.prerequisites': (
            'typeDescriptor', 'appearance_cache', 'createAppearance'),
        'Vehicle.onEnterWorld': (
            'vehicle_onEnterWorld', 'sendStateToOwnClient'),
        'Vehicle.onLeaveWorld': (
            '_Vehicle__stopExtras', 'BigWorld', 'player',
            'vehicle_onLeaveWorld', 'isStarted'),
        'Vehicle.showShooting': (
            'siegeState', 'VEHICLE_SIEGE_STATE', 'ENABLED', 'DISABLED',
            'typeDescriptor', 'extrasDict', 'stopFor', 'startFor',
            'isPlayerVehicle', 'cancelWaitingForShot'),
        'Vehicle.set_siegeState': (
            'isPlayerVehicle', 'onSiegeStateUpdated', 'siegeState'),
        'Vehicle.onSiegeStateUpdated': (
            'typeDescriptor', 'hasSiegeMode', 'onSiegeStateChanged',
            'appearance', 'isPlayerVehicle', 'BigWorld', 'player',
            'inputHandler', 'siegeModeControl',
            'notifySiegeModeChanged'),
        'Vehicle.__stopExtras': (
            'typeDescriptor', 'extras', 'items', 'stop'),
        'Vehicle.showAmmoBayEffect': ('appearance', 'showAmmoBayEffect'),
        'Vehicle.collideSegmentExt': (
            '_Vehicle__collideSegment',),
        'Vehicle.drawEdge': ('appearance', 'highlighter', 'highlight'),
    },
    'scripts/client/AvatarInputHandler/gun_marker_ctrl.pyc': {
        '_CrosshairShotResults._getAllCollisionDetails': (
            'collideSegmentExt',),
        '_CrosshairShotResults.getShotResult': (
            'dist', 'hitAngleCos', 'matInfo', 'compName'),
    },
    'scripts/client/ProjectileMover.pyc': {
        # The native method name is a dynamic getattr string literal, while
        # ``filter`` and ``getattr`` are the actual CPython name-table entries.
        'segmentMayHitEntity': ('getattr', 'filter'),
        'collideEntities': (
            'collideSegment', 'EntityCollisionData',
            'hitAngleCos', 'armor'),
        'getCollidableEntities': (
            'arena', 'vehicles', 'entity', 'isStarted',
            'segmentMayHitEntity'),
    },
    'scripts/client/vehicle_extras.pyc': {
        'Fire._start': (
            'appearance', 'isUnderwater', '_Fire__playEffect',
            'switchFireVibrations'),
        'Fire.__playEffect': (
            'typeDescriptor', 'type', 'effects', 'appearance',
            'boundEffects', 'addNew'),
        'Fire._cleanup': (
            'appearance', 'switchFireVibrations', 'health', 'stop',
            'keyOff'),
    },
    'scripts/client/helpers/bound_effects.pyc': {
        'ModelBoundEffects.addNew': ('addNewToNode',),
        'ModelBoundEffects.destroy': ('stop', '_ModelBoundEffects__model'),
    },
    'scripts/client/helpers/EffectsList.pyc': {
        '_ShotSoundEffectDesc.create': (
            'isAlive', 'isStarted', 'appearance', 'engineAudition',
            'getSoundObject', 'play', 'setRTPC'),
    },
    'scripts/client/gui/battle_control/controllers/feedback_adaptor.pyc': {
        'BattleFeedbackAdaptor.__init__': (
            'weakref', 'proxy', 'arenaDP',
            '_BattleFeedbackAdaptor__arenaDP'),
        'BattleFeedbackAdaptor.handleBattleEvents': (
            'PlayerFeedbackEvent', 'fromDict',
            'VEHICLE_VISIBILITY_CHANGED', 'PLAYER_DETECT_ENEMY',
            'isVisible', 'isDirect', 'onPlayerFeedbackReceived'),
        'BattleFeedbackAdaptor.startVehicleVisual': (
            'id', 'getVehicleInfo', 'isObserver', 'team',
            'isPlayerVehicle'),
        'BattleFeedbackAdaptor.startVehicleVisual.__addVehicleToUI': (
            'onVehicleMarkerAdded', 'onMinimapVehicleAdded', 'isAlive'),
        'BattleFeedbackAdaptor.stopVehicleVisual': (
            'onVehicleMarkerRemoved', 'onMinimapVehicleRemoved'),
        'BattleFeedbackAdaptor.setVehicleNewHealth': (
            '_setVehicleHealthChanged',),
        'BattleFeedbackAdaptor._setVehicleHealthChanged': (
            '_BattleFeedbackAdaptor__arenaDP', 'getVehicleInfo',
            'onVehicleFeedbackReceived', '_FET', 'VEHICLE_HEALTH'),
    },
    'scripts/client/gui/battle_control/battle_session.pyc': {
        'BattleSessionProvider.shared': (
            '_BattleSessionProvider__sharedRepo',),
        'BattleSessionProvider.getArenaDP': (
            '_BattleSessionProvider__arenaDP',),
        'BattleSessionProvider.addArenaCtrl': (
            '_BattleSessionProvider__arenaListeners', 'addController'),
        'BattleSessionProvider.removeArenaCtrl': (
            '_BattleSessionProvider__arenaListeners', 'removeController'),
        'BattleSessionProvider.setVehicleHealth': (
            '_BattleSessionProvider__sharedRepo', 'feedback',
            'setVehicleNewHealth'),
    },
    'scripts/client/gui/battle_control/arena_info/arena_dp.pyc': {
        'ArenaDataProvider.isRequiredDataExists': (
            '_ArenaDataProvider__checkRequiredData',),
        'ArenaDataProvider.getPlayerVehicleID': (
            '_ArenaDataProvider__playerVehicleID',
            '_ArenaDataProvider__tryToGetRequiredData'),
    },
    'scripts/client/gui/Scaleform/daapi/view/battle/shared/markers2d/'
    'plugins.pyc': {
        'VehicleMarkerPlugin.init': (
            'sessionProvider', 'shared', 'feedback',
            'onVehicleFeedbackReceived',
            '_VehicleMarkerPlugin__onVehicleFeedbackReceived'),
        'VehicleMarkerPlugin.start': (
            'getArenaDP', 'getPlayerVehicleID',
            '_VehicleMarkerPlugin__playerVehicleID', 'addArenaCtrl'),
        'VehicleMarkerPlugin.stop': ('_markers', 'destroy'),
        'VehicleMarkerPlugin.__onVehicleFeedbackReceived': (
            '_EVENT_ID', 'VEHICLE_HEALTH',
            '_VehicleMarkerPlugin__updateVehicleHealth'),
        'VehicleMarkerPlugin.__updateVehicleHealth': (
            '_invokeMarker', '_VehicleMarkerPlugin__getVehicleDamageType',
            'ATTACK_REASONS'),
        'VehicleMarkerPlugin.__getVehicleDamageType': (
            'vehicleID', '_VehicleMarkerPlugin__playerVehicleID',
            'DAMAGE_TYPE', 'FROM_PLAYER', 'FROM_ALLY'),
    },
    'scripts/client/gui/Scaleform/daapi/view/battle/shared/markers2d/'
    'markers.pyc': {
        'VehicleMarker.attach': ('appearance', 'onModelChanged'),
        'VehicleMarker.fetchMatrixProvider': ('model', 'node'),
        'VehicleMarker.getHealth': ('health',),
        'VehicleMarker.isAlive': ('isAlive',),
    },
    'scripts/client/AvatarPositionControl.pyc': {
        'ConsistentMatrices.__setTarget': (
            '_ConsistentMatrices__attachedVehicleMatrix', 'target',
            'onVehicleMatrixBindingChanged'),
        'ConsistentMatrices.__linkOwnVehicle': (
            'filter', 'WGVehicleFilter',
            '_ConsistentMatrices__ownVehicleMProv', 'target',
            'bodyMatrix', 'matrix'),
    },
    'scripts/client/AvatarInputHandler/DynamicCameras/__init__.pyc': {
        'AccelerationSmoother.update': (
            'filter', 'velocity', 'acceleration', 'engineMode', 'matrix'),
    },
    'scripts/client/AvatarInputHandler/DynamicCameras/ArcadeCamera.pyc': {
        'ArcadeCamera.create': (
            'getTargetMProv', 'BigWorld', 'player', 'matrix',
            '_ArcadeCamera__aimingSystem'),
        'ArcadeCamera.enable': (
            'getVehicleAttached', 'getOwnVehicleMatrix', 'vehicleMProv',
            '_ArcadeCamera__aimingSystem', 'enable'),
        'ArcadeCamera.__calcCurOscillatorAcceleration': (
            'BigWorld', 'player', 'getVehicleAttached', 'filter', 'getattr',
            '_ArcadeCamera__accelerationSmoother', 'update'),
        'ArcadeCamera.__setVehicleMProv': (
            '_ArcadeCamera__refineVehicleMProv',
            '_ArcadeCamera__setupCameraProviders', 'vehicleMProv'),
        'ArcadeCamera.__getVehicleMProv': (
            '_ArcadeCamera__aimingSystem', 'vehicleMProv', 'source'),
        'ArcadeCamera.setToVehicleDirection': (
            'getTargetMProv', 'setYawPitch', 'yaw', 'pitch'),
    },
    'scripts/client/gui/battle_control/controllers/feedback_events.pyc': {
        'PlayerFeedbackEvent.fromDict': (
            '_BATTLE_EVENT_TO_PLAYER_FEEDBACK_EVENT',
            '_PLAYER_FEEDBACK_EXTRA_DATA_CONVERTERS'),
    },
    'scripts/client/gui/Scaleform/daapi/view/battle/shared/'
    'ribbons_aggregator.pyc': {
        '_EnemyDetectionRibbon.getType': (
            'BATTLE_EFFICIENCY_TYPES', 'DETECTION'),
        '_createRibbonFromPlayerFeedbackEvent': (
            '_FEEDBACK_EVENT_TO_RIBBON_CLS_FACTORY',
            'createFromFeedbackEvent'),
    },
    'scripts/client/AvatarInputHandler/DynamicCameras/SniperCamera.pyc': {
        'SniperCamera.__calcCurOscillatorAcceleration': (
            'BigWorld', 'player', 'vehicle', 'isAlive', 'filter', 'velocity',
            '_SniperCamera__accelerationSmoother', 'update'),
    },
    'scripts/client/AvatarInputHandler/AimingSystems/'
    'ArcadeAimingSystem.pyc': {
        'ArcadeAimingSystem.__setVehicleMProv': (
            '_ArcadeAimingSystem__vehicleMProv',
            '_ArcadeAimingSystem__cursor', 'base'),
        'ArcadeAimingSystem.enable': (
            'focusOnPos', '_ArcadeAimingSystem__adjustFocus'),
        'ArcadeAimingSystem.focusOnPos': (
            '_ArcadeAimingSystem__vehicleMProv',
            '_ArcadeAimingSystem__cursor',
            '_ArcadeAimingSystem__getLookToAimMatrix'),
    },
    'scripts/client/AvatarInputHandler/AimingSystems/'
    'SniperAimingSystem.pyc': {
        'SniperAimingSystem.enable': (
            'steadyVehicleMatrixCalculator', 'outputMProv', 'focusOnPos'),
        'SniperAimingSystem.focusOnPos': (
            '_SniperAimingSystem__getPlayerGunMat',
            '_SniperAimingSystem__worldYaw',
            '_SniperAimingSystem__worldPitch',
            '_SniperAimingSystem__worldYawPitchToTurret'),
        'SniperAimingSystem.__worldYawPitchToTurret': (
            '_SniperAimingSystem__vehicleMProv',),
    },
    'scripts/client/AvatarInputHandler/AimingSystems/'
    'steady_vehicle_matrix.pyc': {
        'SteadyVehicleMatrixCalculator.relinkSources': (
            'BigWorld', 'player', 'getVehicleAttached', 'filter',
            'groundPlacingMatrixFiltered', 'stabilisedMatrix',
            '_SteadyVehicleMatrixCalculator__outputMProv', 'rotationSrc',
            'translationSrc',
            '_SteadyVehicleMatrixCalculator__stabilisedMProv', 'target'),
    },
    'scripts/client/gui/battle_control/matrix_factory.pyc': {
        'makeArcadeCameraMatrix': (
            'getOwnVehicleMatrix', 'translationSrc', 'camera',
            'invViewMatrix', 'rotationSrc'),
        'makeAttachedVehicleMatrix': (
            'consistentMatrices', 'attachedVehicleMatrix'),
        'makeOwnVehicleMatrix': (
            'consistentMatrices', 'ownVehicleMatrix'),
    },
    'scripts/client/gui/battle_control/controllers/consumables/'
    'equipment_ctrl.pyc': {
        '_ExpandedItem.getActivationCode': (
            'isEntityRequired', 'makeExtraName', 'index'),
        '_ExtinguisherItem.getActivationCode': ('id',),
    },
    'scripts/client/AreaDestructibles.pyc': {
        '_printErrDescNotAvailable': (
            'BigWorld', 'wg_getDestructibleFilename'),
        'ClientDestructiblesCache.getDestructibleDesc': (
            'BigWorld', 'wg_getDestructibleFilename',
            'getDescByFilename'),
        'DestructiblesManager.onChunkLoad': (
            '_DestructiblesManager__loadedChunkIDs',),
        '_DestructiblesAnimator.showFall': (
            'spaceID', 'chunkID', 'destrIndex'),
        'AreaDestructibles.set_fallenTrees': (
            'orderDestructibleDestroy', 'DESTR_TYPE_TREE'),
        'AreaDestructibles.set_fallenColumns': (
            'orderDestructibleDestroy', 'DESTR_TYPE_FALLING_ATOM'),
        'AreaDestructibles.set_destroyedFragiles': (
            'decodeFragile', 'DESTR_TYPE_FRAGILE'),
        'AreaDestructibles.set_destroyedModules': (
            'decodeDestructibleModule', 'DESTR_TYPE_STRUCTURE'),
        'DestructiblesManager.orderDestructibleDestroy': (
            'DESTR_TYPE_FRAGILE', 'DESTR_TYPE_STRUCTURE',
            'decodeFragile', 'decodeDestructibleModule'),
        'DestructiblesManager.__destroyDestructible': (
            'wg_getDestructibleFallPitchConstr',),
        'DestructiblesManager.__launchTreeFallEffect': (
            'g_cache', 'getDestructibleDesc',
            '_printErrDescNotAvailable'),
        'DestructiblesManager.__getDestrInitialMatrix': (
            '_DestructiblesManager__destrInitialMatrices',
            'wg_getDestructibleMatrix', 'setdefault'),
        '_DestructiblesAnimator.showFall': (
            '_DestructiblesAnimator__bodies', 'append'),
        '_DestructiblesAnimator.showFallTree': (
            'g_cache', 'getDestructibleDesc',
            '_printErrDescNotAvailable'),
        '_DestructiblesAnimator.__positionBodyModel': (
            'BigWorld', 'wg_setDestructibleMatrix'),
    },
    'scripts/client/helpers/EffectMaterialCalculation.pyc': {
        'calcSurfaceMaterialNearPoint': (
            'BigWorld', 'wg_getMatInfoNearPoint',
            'isDestructibleBroken'),
    },
    'scripts/common/DestructiblesCache.pyc': {
        'encodeFallenColumn': ('int', 'PI', 'PI_2'),
        'encodeFallenTree': ('int', 'PI', 'PI_2'),
        'encodeDestructibleModule': ('int',),
        'encodeFragile': ('int',),
        'decodeFragile': ('bool',),
    },
    'scripts/client/vehicle_systems/CompoundAppearance.pyc': {
        'CompoundAppearance': (
            'waterSensor', 'isInWater', 'isUnderwater'),
        'CompoundAppearance.start': ('getHitTesters', 'loadBspModel'),
        'CompoundAppearance.__linkCompound': (
            '_CompoundAppearance__vehicle',
            '_CompoundAppearance__compoundModel', 'model', 'matrix'),
        'CompoundAppearance.__onModelsRefresh': (
            '_CompoundAppearance__filter', 'syncGunAngles'),
        'CompoundAppearance.setupGunMatrixTargets': (
            '_CompoundAppearance__filter', 'turretMatrix', 'gunMatrix',
            'target'),
        'CompoundAppearance.changeVisibility': (
            'compoundModel', 'visible', 'showStickers',
            '_CompoundAppearance__crashedTracksCtrl', 'setVisible'),
        'CompoundAppearance.deactivate': (
            'BigWorld', 'player', 'inputHandler',
            'removeVehicleFromCameraCollider', 'arena',
            'onPeriodChange', 'onCameraChanged'),
    },
    'scripts/client/vehicle_systems/components/CrashedTracks.pyc': {
        'CrashedTrackController.__setupTrackAssembler': (
            'filter', 'groundPlacingMatrix'),
        'CrashedTrackController.__onModelLoaded': (
            'filter', 'groundPlacingMatrix'),
    },
    'scripts/client/VehicleGunRotator.pyc': {
        'VehicleGunRotator.start': (
            '_VehicleGunRotator__isStarted',
            '_VehicleGunRotator__maxTurretRotationSpeed',
            '_VehicleGunRotator__avatar', 'isOnArena'),
        'VehicleGunRotator.reset': (
            '_VehicleGunRotator__turretYaw',
            '_VehicleGunRotator__gunPitch',
            '_VehicleGunRotator__updateTurretMatrix',
            '_VehicleGunRotator__updateGunMatrix',
            '_VehicleGunRotator__isLocked'),
        'VehicleGunRotator.update': (
            '_VehicleGunRotator__avatar',
            'getOwnVehicleShotDispersionAngle',
            '_VehicleGunRotator__dispersionAngles'),
        'VehicleGunRotator.setShotPosition': (
            '_VehicleGunRotator__dispersionAngles',),
        'VehicleGunRotator.__syncWithServerTurretYaw': (
            '_VehicleGunRotator__avatar', 'vehicle',
            'getServerGunAngles', 'LatencyInfo'),
        'VehicleGunRotator.__trackPointOnServer': (
            '_VehicleGunRotator__avatar', 'playerVehicleID',
            'trackRelativePointWithGun'),
        'VehicleGunRotator.getAvatarOwnVehicleStabilisedMatrix': (
            '_VehicleGunRotator__avatar',
            'getOwnVehicleStabilisedMatrix',
            '_VehicleGunRotator__getTurretStaticYaw', 'filter',
            'interpolateStabilisedMatrix', 'BigWorld', 'time'),
        'VehicleGunRotator.getCurShotPosition': (
            '_VehicleGunRotator__getShotPosition',),
        'VehicleGunRotator.__getShotPosition': (
            'getAvatarOwnVehicleStabilisedMatrix', 'applyPoint',
            'applyVector'),
        'VehicleGunRotator.__getTurretYawLimits': (
            '_VehicleGunRotator__avatar', 'vehicleTypeDescriptor',
            'gun', 'turretYawLimits'),
        'VehicleGunRotator.__rotate': (
            '_VehicleGunRotator__getTurretYawLimits',
            'getAvatarOwnVehicleStabilisedMatrix', 'getShotAngles',
            '_VehicleGunRotator__updateTurretMatrix',
            '_VehicleGunRotator__updateGunMatrix'),
    },
    'scripts/client_common/ClientArena.pyc': {
        'ClientArena.__init__': (
            'EventManager', 'onTeamBasePointsUpdate',
            'onTeamBaseCaptured'),
        'ClientArena.__onBasePointsUpdate': (
            'cPickle', 'loads', 'onTeamBasePointsUpdate'),
        'ClientArena.__onBaseCaptured': (
            'cPickle', 'loads', 'onTeamBaseCaptured'),
        'ClientArena.__onVehicleStatisticsUpdate': (
            '_ClientArena__vehicleStatisticsAsDict', 'cPickle', 'loads',
            'zlib', 'decompress', 'onVehicleStatisticsUpdate'),
        'ClientArena.__onTeamKiller': (
            'cPickle', 'loads', 'onTeamKiller'),
    },
    'scripts/client_common/ClientChat.pyc': {
        'ClientChat.__init__': ('_ClientChat__chatActionCallbacks',),
    },
    'scripts/client/ChatManager.pyc': {
        'ChatManager.switchPlayerProxy': (
            '_ChatManager__cleanupMyCallbacks', 'playerProxy'),
    },
    'scripts/client/gui/shared/personality.pyc': {
        'onCenterIsLongDisconnected': (
            'BigWorld', 'player', 'isLongDisconnectedFromCenter'),
    },
    'scripts/client/gui/app_loader/states.pyc': {
        'LobbyState.getSpaceID': ('_SPACE_ID', 'LOBBY'),
        'LobbyState._getNextState': (
            '_SPACE_ID', 'BATTLE_LOADING', 'BattleLoadingState'),
        'BattleLoadingState.getSpaceID': (
            '_SPACE_ID', 'BATTLE_LOADING'),
        'BattleLoadingState._getNextState': (
            '_SPACE_ID', 'BATTLE', '_doStartBattle',
            '_createBattleState', 'LOBBY', 'LobbyState'),
        'BattleLoadingState._createBattleState': ('BattleState',),
        'BattleLoadingState.showGUI': ('destroyLobby', 'loadBattlePage'),
        'BattleState.getSpaceID': ('_SPACE_ID', 'BATTLE'),
        'BattleState._getNextState': (
            '_SPACE_ID', 'WAITING', 'WaitingState'),
        'LoginState.init': ('_clearEntitiesAndSpaces',),
        'LoginState.update': ('_clearEntitiesAndSpaces',),
        'LoginState._clearEntitiesAndSpaces': (
            'BigWorld', 'clearEntitiesAndSpaces'),
    },
    'scripts/client/gui/app_loader/loader.pyc': {
        '_AppLoader.showBattleLoading': (
            'changeSpace', '_SPACE_ID', 'BATTLE_LOADING'),
    },
    'scripts/client/gui/Scaleform/daapi/view/lobby/header/'
    'LobbyHeader.pyc': {
        'LobbyHeader.fightClick': (
            'lobbyContext', 'isHeaderNavigationPossible',
            'prbDispatcher', 'doAction', 'PrbAction'),
        'LobbyHeader._updatePrebattleControls': (
            'prbEntity', 'canPlayerDoAction',
            '_checkFightButtonDisabled', 'as_disableFightButtonS'),
        'LobbyHeader.__handleFightButtonUpdated': (
            '_updatePrebattleControls',),
        'LobbyHeader.__addListeners': (
            'FightButtonEvent', 'FIGHT_BUTTON_UPDATE',
            '_LobbyHeader__handleFightButtonUpdated',
            'EVENT_BUS_SCOPE', 'LOBBY'),
    },
    'scripts/client/gui/Scaleform/daapi/view/meta/LobbyHeaderMeta.pyc': {
        'LobbyHeaderMeta.as_disableFightButtonS': (
            '_isDAAPIInited', 'flashObject', 'as_disableFightButton'),
    },
    'scripts/client/gui/Scaleform/framework/entities/'
    'BaseDAAPIModule.pyc': {
        'BaseDAAPIModule.setFlashObject': (
            'turnDAAPIon', 'isCreated', 'create'),
    },
    'scripts/client/gui/Scaleform/framework/entities/DAAPIEntity.pyc': {
        'DAAPIEntity.turnDAAPIon': (
            '_DAAPIEntity__isDAAPIInited',
            '_DAAPIEntity__flashObject', 'script'),
    },
    'scripts/client/gui/prb_control/events_dispatcher.pyc': {
        'EventDispatcher.updateUI': (
            '_EventDispatcher__fireEvent', 'FightButtonEvent',
            'FIGHT_BUTTON_UPDATE', '_EventDispatcher__invalidatePrbEntity'),
    },
    'scripts/client/gui/shared/event_bus.pyc': {
        'EventBus.handleEvent': (
            '_EventBus__scopes', 'eventType', 'copy'),
    },
    'scripts/client/gui/battle_control/controllers/arena_load_ctrl.pyc': {
        'ArenaLoadController.invalidateArenaInfo': (
            'g_appLoader', 'showBattleLoading'),
        'ArenaLoadController.arenaLoadCompleted': (
            '_ArenaLoadController__isCompleted', 'g_appLoader',
            'showBattlePage', '_viewComponents', 'arenaLoadCompleted'),
    },
    'scripts/client/gui/battle_control/controllers/repositories.pyc': {
        'BattleSessionSetup.arenaDP': (
            'sessionProvider', 'getArenaDP'),
        'SharedControllersLocator.arenaLoad': (
            '_repository', 'getController', 'BATTLE_CTRL_ID',
            'ARENA_LOAD_PROGRESS'),
    },
    'scripts/client/gui/battle_control/controllers/debug_ctrl.pyc': {
        'DebugController._update': (
            'BigWorld', 'statPing', 'statLagDetected',
            'updateDebugInfo'),
    },
    'scripts/client/gui/Scaleform/daapi/view/battle/shared/'
    'debug_panel.pyc': {
        'DebugPanel.updateDebugInfo': (
            'as_updatePingFPSLagInfoS', 'as_updatePingFPSInfoS'),
    },
    'scripts/client/gui/game_control/state_tracker.pyc': {
        'GameStateTracker.init': (
            'g_eventBus', 'addListener', 'LOBBY_VIEW_LOADED'),
        'GameStateTracker.fini': (
            'g_eventBus', 'removeListener', 'LOBBY_VIEW_LOADED'),
    },
    'scripts/client/gui/Scaleform/daapi/view/lobby/LobbyView.pyc': {
        'LobbyView._populate': (
            'fireEvent', 'GUICommonEvent', 'LOBBY_VIEW_LOADED'),
    },
    'scripts/client/gui/ClientHangarSpace.pyc': {
        'ClientHangarSpace.create': (
            'BigWorld', 'createSpace',
            'wg_setSpaceItemsVisibilityMask',
            'addSpaceGeometryMapping'),
        '_VehicleAppearance.__startBuild': ('loadResourceListBG',),
        '_VehicleAppearance.__onResourcesLoaded': (
            '_VehicleAppearance__setupModel',),
        '_VehicleAppearance.__doFinalSetup': ('entity', 'model'),
    },
    'scripts/client/gui/prb_control/dispatcher.pyc': {
        '_PrbControlLoader.createBattleDispatcher': (
            '_PrbControlLoader__prbDispatcher',),
    },
    'scripts/client/gui/prb_control/__init__.pyc': {
        'prbDispatcherProperty.__get__': (
            'g_prbLoader', 'getDispatcher'),
    },
    'scripts/client/SoundGroups.pyc': {
        'SoundGroups.destroy': ('BigWorld', 'player', 'inputHandler'),
    },
    'scripts/client/CurrentVehicle.pyc': {
        '_CurrentVehicle.item': (
            '_CurrentVehicle__vehInvID', 'itemsCache', 'items',
            'getVehicle'),
    },
    'scripts/client/gui/shared/gui_items/Vehicle.pyc': {
        'Vehicle.descriptor': ('_Vehicle__descriptor',),
    },
}


EXPECTED_RESOURCE_STRINGS = {
    'scripts/space_defs/GeneralSpaceData.def': (
        'itemsVisibilityMask', 'UINT32', 'Exposed'),
}


EXPECTED_PACKED_XML_PATH_VALUES = {
    'scripts/entity_defs/Avatar.def': {
        ('BaseMethods', 'vehicle_changeSetting', 'Exposed'): (
            (1, ''),),
        ('BaseMethods', 'vehicle_changeSetting', 'Arg'): (
            (1, 'UINT8'), (1, 'INT32')),
        ('ClientMethods', 'updateVehicleMiscStatus', 'Arg'): (
            (1, 'OBJECT_ID'), (1, 'UINT8'), (1, 'INT32'), (1, 'ARRAY')),
        ('ClientMethods', 'updateVehicleMiscStatus', 'Arg', 'of'): (
            (1, 'FLOAT32'),),
    },
    'scripts/entity_defs/Vehicle.def': {
        ('Properties', 'damageStickers', 'Type'): (
            (1, 'ARRAY'),),
        ('Properties', 'damageStickers', 'Type', 'of'): (
            (1, 'UINT64'),),
        ('Properties', 'damageStickers', 'Flags'): (
            (1, 'ALL_CLIENTS'),),
        ('Properties', 'siegeState', 'Type'): (
            (1, 'UINT8'),),
        ('Properties', 'siegeState', 'Flags'): (
            (1, 'ALL_CLIENTS'),),
    },
    'scripts/item_defs/vehicles/sweden/S10_Strv_103_0_Series.xml': {
        ('siege_mode', 'switchOnTime'): ((2, 2),),
        ('siege_mode', 'switchOffTime'): ((1, '1.3'),),
        ('siege_mode', 'engineDamageCoeff'): ((2, 2),),
    },
    'scripts/item_defs/vehicles/sweden/S10_Strv_103_0_Series_siege_mode.xml': {
        ('speedLimits', 'forward'): ((2, 10),),
        ('speedLimits', 'backward'): ((2, 10),),
    },
    'scripts/item_defs/vehicles/sweden/S11_Strv_103B.xml': {
        ('siege_mode', 'switchOnTime'): ((2, 2),),
        ('siege_mode', 'switchOffTime'): ((1, '1.3'),),
        ('siege_mode', 'engineDamageCoeff'): ((2, 2),),
    },
    'scripts/item_defs/vehicles/sweden/S11_Strv_103B_siege_mode.xml': {
        ('speedLimits', 'forward'): ((2, 10),),
        ('speedLimits', 'backward'): ((2, 10),),
    },
    'scripts/item_defs/vehicles/sweden/S21_UDES_03.xml': {
        ('siege_mode', 'switchOnTime'): ((2, 2),),
        ('siege_mode', 'switchOffTime'): ((2, 2),),
        ('siege_mode', 'engineDamageCoeff'): ((2, 2),),
    },
    'scripts/item_defs/vehicles/sweden/S21_UDES_03_siege_mode.xml': {
        ('speedLimits', 'forward'): ((2, 5),),
        ('speedLimits', 'backward'): ((2, 5),),
    },
    'scripts/item_defs/vehicles/sweden/S22_Strv_S1.xml': {
        ('siege_mode', 'switchOnTime'): ((2, 2),),
        ('siege_mode', 'switchOffTime'): ((1, '1.3'),),
        ('siege_mode', 'engineDamageCoeff'): ((2, 2),),
    },
    'scripts/item_defs/vehicles/sweden/S22_Strv_S1_siege_mode.xml': {
        ('speedLimits', 'forward'): ((2, 8),),
        ('speedLimits', 'backward'): ((2, 8),),
    },
}


EXPECTED_GLOBALS = {
    'scripts/common/physics_shared.pyc': {
        'WEIGHT_SCALE': 0.001,
    },
    'scripts/common/AccountCommands.pyc': {
        'RES_FAILURE': -1,
        'RES_SUCCESS': 0,
        'RES_STREAM': 1,
        'CMD_SYNC_DATA': 100,
        'CMD_EQUIP_EQS': 104,
        'CMD_SET_AND_FILL_LAYOUTS': 108,
        'CMD_SYNC_SHOP': 300,
        'CMD_REQ_SERVER_STATS': 501,
        'CMD_SYNC_DOSSIERS': 600,
        'CMD_SET_LANGUAGE': 1000,
        'CMD_COMPLETE_TUTORIAL': 1150,
        'CMD_ADD_INT_USER_SETTINGS': 1600,
        'CMD_DEL_INT_USER_SETTINGS': 1601,
    },
}


EXPECTED_CLASS_CONSTANTS = {
    'scripts/common/BattleFeedbackCommon.pyc': {
        'BATTLE_EVENT_TYPE': {
            'SPOTTED': 0,
            'TANKING': 5,
            'TARGET_VISIBILITY': 12,
        },
    },
    'scripts/client/Avatar.pyc': {
        'ClientVisibilityFlags': {
            'CLIENT_MASK': 4293918720,
            'SERVER_MASK': 1048575,
        },
        '_MOVEMENT_FLAGS': {
            'FORWARD': 1,
            'BACKWARD': 2,
            'ROTATE_LEFT': 4,
            'ROTATE_RIGHT': 8,
            'CRUISE_CONTROL50': 16,
            'CRUISE_CONTROL25': 32,
            'BLOCK_TRACKS': 64,
        },
    },
    'scripts/common/constants.pyc': {
        'DESTRUCTIBLE_MATKIND': {
            'MIN': 71,
            'MAX': 100,
            'NORMAL_MIN': 73,
            'NORMAL_MAX': 86,
            'DAMAGED_MIN': 87,
            'DAMAGED_MAX': 100,
        },
        'ARENA_PERIOD': {
            'PREBATTLE': 2,
            'BATTLE': 3,
        },
        'DROWN_WARNING_LEVEL': {
            'SAFE': 0,
            'CAUTION': 1,
            'DANGER': 2,
        },
        'VEHICLE_MISC_STATUS': {
            'OTHER_VEHICLE_DAMAGED_DEVICES_VISIBLE': 0,
            'VEHICLE_DROWN_WARNING': 4,
            'SIEGE_MODE_STATE_CHANGED': 9,
        },
        'ATTACK_REASON': {
            'SHOT': 'shot',
            'FIRE': 'fire',
            'RAM': 'ramming',
            'WORLD_COLLISION': 'world_collision',
            'DEATH_ZONE': 'death_zone',
            'DROWNING': 'drowning',
        },
        'VEHICLE_SETTING': {
            'CURRENT_SHELLS': 0,
            'NEXT_SHELLS': 1,
            'AUTOROTATION_ENABLED': 2,
            'SIEGE_MODE_ENABLED': 3,
            'ACTIVATE_EQUIPMENT': 16,
            'RELOAD_PARTIAL_CLIP': 17,
        },
        'VEHICLE_HIT_FLAGS': {
            'VEHICLE_KILLED': 1,
            'FIRE_STARTED': 4,
            'RICOCHET': 8,
            'MATERIAL_WITH_POSITIVE_DF_PIERCED_BY_PROJECTILE': 16,
            'MATERIAL_WITH_POSITIVE_DF_NOT_PIERCED_BY_PROJECTILE': 32,
            'DEVICE_DAMAGED_BY_PROJECTILE': 1024,
            'CHASSIS_DAMAGED_BY_PROJECTILE': 2048,
            'GUN_DAMAGED_BY_PROJECTILE': 4096,
            'MATERIAL_WITH_POSITIVE_DF_PIERCED_BY_EXPLOSION': 8192,
            'DEVICE_DAMAGED_BY_EXPLOSION': 65536,
            'CHASSIS_DAMAGED_BY_EXPLOSION': 131072,
            'GUN_DAMAGED_BY_EXPLOSION': 262144,
            'ATTACK_IS_DIRECT_PROJECTILE': 1048576,
            'ATTACK_IS_EXTERNAL_EXPLOSION': 2097152,
        },
        'VEHICLE_SIEGE_STATE': {
            'DISABLED': 0,
            'SWITCHING_ON': 1,
            'ENABLED': 2,
            'SWITCHING_OFF': 3,
        },
        'AMMOBAY_DESTRUCTION_MODE': {
            'POWDER_BURN_OFF': 0,
            'POWDER_EXPLOSION': 1,
            'HE_DETONATION': 2,
        },
        'ARENA_UPDATE': {
            'VEHICLE_ADDED': 2,
            'PERIOD': 3,
            'STATISTICS': 4,
            'VEHICLE_STATISTICS': 5,
            'VEHICLE_KILLED': 6,
            'AVATAR_READY': 7,
            'TEAM_KILLER': 10,
        },
    },
    'scripts/client/gui/app_loader/settings.pyc': {
        'GUI_GLOBAL_SPACE_ID': {
            'LOBBY': 4,
            'BATTLE_LOADING': 5,
            'BATTLE': 6,
        },
    },
    'scripts/client/gui/Scaleform/daapi/view/battle/shared/markers2d/'
    'settings.pyc': {
        'DAMAGE_TYPE': {
            'FROM_UNKNOWN': 0,
            'FROM_ALLY': 1,
            'FROM_ENEMY': 2,
            'FROM_SQUAD': 3,
            'FROM_PLAYER': 4,
        },
    },
    'scripts/client/gui/shared/events.pyc': {
        'FightButtonEvent': {
            'FIGHT_BUTTON_UPDATE': 'updateFightButton',
        },
    },
    'scripts/client/gui/shared/event_bus.pyc': {
        'EVENT_BUS_SCOPE': {
            'LOBBY': 1,
        },
    },
}


_FILTER_SYNC_METHODS = ('syncGunAngles', 'syncStabilisedYPR')
EXPECTED_FILTER_SYNC_CALLS = frozenset((
    ('scripts/client/Avatar.pyc',
     'PlayerAvatar.__onSetOwnVehicleAuxPhysicsData',
     'syncStabilisedYPR'),
    ('scripts/client/Vehicle.pyc',
     'Vehicle.__startWGPhysics', 'syncGunAngles'),
    ('scripts/client/Vehicle.pyc',
     'Vehicle.set_gunAnglesPacked', 'syncGunAngles'),
    ('scripts/client/vehicle_systems/CompoundAppearance.pyc',
     'CompoundAppearance.__onModelsRefresh', 'syncGunAngles'),
))


# A method signature cannot reveal that an exposed value is getter-only.
# The #1513 gun rotator creates this property with property(getter), and a
# direct assignment terminates the offline startup with "can't set attribute".
EXPECTED_READ_ONLY_PROPERTIES = {
    'scripts/client/VehicleGunRotator.pyc': {
        'VehicleGunRotator': ('dispersionAngle',),
    },
}


# The pinned producer returns a mutable two-element list, and the consumer
# later updates element zero in place.  Returning a tuple from an adapter
# passes signature checks but fails deterministically in setShotPosition().
EXPECTED_LIST_RETURNS = {
    'scripts/client/Avatar.pyc': {
        'PlayerAvatar.getOwnVehicleShotDispersionAngle': 2,
    },
}

EXPECTED_SUBSCRIPT_MUTATIONS = {
    'scripts/client/VehicleGunRotator.pyc': {
        'VehicleGunRotator.setShotPosition': (
            '_VehicleGunRotator__dispersionAngles', 0),
    },
}


# Ordered semantic skeletons for exact-client behavior that signatures and
# name-presence checks cannot prove.  The matcher permits unrelated loads and
# arithmetic between steps, but bounds each gap so dead or distant code cannot
# accidentally satisfy a contract.
EXPECTED_ORDERED_INSTRUCTION_PATTERNS = {
    'scripts/common/DestructiblesCache.pyc': {
        'encodeDestructibleModule': (
            'module material occupies one seven-bit field', 4, (
                ('LOAD_FAST', 'value', 'destrID'),
                ('LOAD_CONST', 'value', 8),
                ('BINARY_LSHIFT', None, None),
                ('LOAD_FAST', 'value', 'matKind'),
                ('LOAD_CONST', 'value', 1),
                ('BINARY_LSHIFT', None, None),
                ('BINARY_OR', None, None),
                ('LOAD_GLOBAL', 'value', 'int'),
                ('LOAD_FAST', 'value', 'isShotDamage'),
                ('CALL_FUNCTION', 'argument', 1),
                ('BINARY_OR', None, None),
                ('RETURN_VALUE', None, None),
            )),
    },
    'scripts/client/Avatar.pyc': {
        'PlayerAvatar.handleVehicleCollidedVehicle': (
            'vehicle collision uses full relative Vector3 speed', 5, (
                ('LOAD_CONST', 'value', 0.2),
                ('COMPARE_OP', 'argument', 0),
                ('POP_JUMP_IF_FALSE', None, None),
                ('LOAD_FAST', 'value', 'vehA'),
                ('LOAD_ATTR', 'value', 'filter'),
                ('LOAD_ATTR', 'value', 'velocity'),
                ('LOAD_FAST', 'value', 'vehB'),
                ('LOAD_ATTR', 'value', 'filter'),
                ('LOAD_ATTR', 'value', 'velocity'),
                ('BINARY_SUBTRACT', None, None),
                ('LOAD_ATTR', 'value', 'length'),
                ('STORE_FAST', 'value', 'vehSpeedSum'),
            )),
    },
    'scripts/client/AreaDestructibles.pyc': {
        'DestructiblesManager.__dropDestructible': (
            'animated falling callback', 16, (
                ('LOAD_GLOBAL', 'value', 'DestructiblesCache'),
                ('LOAD_ATTR', 'value', 'DESTR_TYPE_FALLING_ATOM'),
                ('COMPARE_OP', 'argument', 2),
                ('POP_JUMP_IF_FALSE', None, None),
                ('LOAD_FAST', 'value', 'isAnimate'),
                ('POP_JUMP_IF_FALSE', None, None),
                ('LOAD_FAST', 'value', 'useEffectsOnTouchDown'),
                ('POP_JUMP_IF_FALSE', None, None),
                ('LOAD_GLOBAL', 'value', 'partial'),
                ('LOAD_ATTR', 'value',
                 '_DestructiblesManager__touchDownWithEffect'),
                ('CALL_FUNCTION', 'argument', 6),
                ('STORE_FAST', 'value', 'touchdownCallback'),
                ('LOAD_GLOBAL', 'value', 'partial'),
                ('LOAD_ATTR', 'value', '_DestructiblesManager__touchDown'),
                ('CALL_FUNCTION', 'argument', 1),
                ('STORE_FAST', 'value', 'touchdownCallback'),
                ('LOAD_CONST', 'value', None),
                ('STORE_FAST', 'value', 'touchdownCallback'),
                ('LOAD_ATTR', 'value', 'showFall'),
                ('LOAD_FAST', 'value', 'touchdownCallback'),
                ('CALL_FUNCTION', 'argument', 9),
            )),
        '_DestructiblesAnimator.__moveBody': (
            'first-touch callback deletion', 16, (
                ('LOAD_CONST', 'value', 'springAngle'),
                ('COMPARE_OP', 'argument', 4),
                ('POP_JUMP_IF_FALSE', None, None),
                ('LOAD_FAST', 'value', 'body'),
                ('LOAD_ATTR', 'value', 'get'),
                ('LOAD_CONST', 'value', 'touchdownCallback'),
                ('CALL_FUNCTION', 'argument', 1),
                ('STORE_FAST', 'value', 'touchdownCallback'),
                ('LOAD_FAST', 'value', 'touchdownCallback'),
                ('LOAD_CONST', 'value', None),
                ('COMPARE_OP', 'argument', 9),
                ('POP_JUMP_IF_FALSE', None, None),
                ('LOAD_FAST', 'value', 'touchdownCallback'),
                ('CALL_FUNCTION', 'argument', 0),
                ('LOAD_FAST', 'value', 'body'),
                ('LOAD_CONST', 'value', 'touchdownCallback'),
                ('DELETE_SUBSCR', None, None),
            )),
        '_DestructiblesAnimator.__update': (
            'move-remove-position order', 32, (
                ('LOAD_ATTR', 'value', '_DestructiblesAnimator__moveBody'),
                ('CALL_FUNCTION', 'argument', 2),
                ('LOAD_ATTR', 'value', 'remove'),
                ('CALL_FUNCTION', 'argument', 1),
                ('LOAD_ATTR', 'value', '_DestructiblesAnimator__bodies'),
                ('GET_ITER', None, None),
                ('LOAD_ATTR', 'value',
                 '_DestructiblesAnimator__positionBodyModel'),
                ('LOAD_FAST', 'value', 'body'),
                ('CALL_FUNCTION', 'argument', 1),
            )),
        '_DestructiblesAnimator.__positionBodyModel': (
            'native matrix update', 16, (
                ('LOAD_GLOBAL', 'value', 'BigWorld'),
                ('LOAD_ATTR', 'value', 'wg_setDestructibleMatrix'),
                ('CALL_FUNCTION', 'argument', 5),
            )),
    },
}

EXPECTED_UNPACK_WIDTHS = {
    'scripts/client_common/ClientArena.pyc': {
        'ClientArena.__onBasePointsUpdate': 6,
        'ClientArena.__onBaseCaptured': 2,
    },
    'scripts/common/physics_shared.pyc': {
        'configurePhysicsMode': 3,
    },
    'scripts/client/helpers/EffectMaterialCalculation.pyc': {
        'calcSurfaceMaterialNearPoint': 7,
    },
    'scripts/client/AreaDestructibles.pyc': {
        'DestructiblesManager.__destroyDestructible': 2,
    },
}

EXPECTED_CALL_WIDTHS = {
    'scripts/client/gui/battle_control/battle_session.pyc': {
        'BattleSessionProvider.setVehicleHealth': 4,
    },
    'scripts/client/gui/battle_control/controllers/feedback_adaptor.pyc': {
        'BattleFeedbackAdaptor.setVehicleNewHealth': 4,
        'BattleFeedbackAdaptor._setVehicleHealthChanged': 3,
    },
    'scripts/client/gui/Scaleform/daapi/view/battle/shared/markers2d/'
    'plugins.pyc': {
        'VehicleMarkerPlugin.__updateVehicleHealth': 5,
    },
    'scripts/client_common/ClientArena.pyc': {
        'ClientArena.__onBasePointsUpdate': 6,
        'ClientArena.__onBaseCaptured': 2,
    },
    'scripts/client/helpers/EffectMaterialCalculation.pyc': {
        'calcSurfaceMaterialNearPoint': 5,
    },
}

EXPECTED_TUPLE_WIDTHS = {
    'scripts/client/gui/battle_control/controllers/feedback_adaptor.pyc': {
        'BattleFeedbackAdaptor._setVehicleHealthChanged': 3,
    },
}

EXPECTED_VAR_CALL_WIDTHS = {
    'scripts/client/gui/Scaleform/daapi/view/battle/shared/markers2d/'
    'plugins.pyc': {
        'VehicleMarkerPlugin.__onVehicleFeedbackReceived': 1,
    },
}

EXPECTED_EQUALITY_BRANCHES = {
    'scripts/client/gui/Scaleform/daapi/view/battle/shared/markers2d/'
    'plugins.pyc': {
        'VehicleMarkerPlugin.__getVehicleDamageType': (
            'vehicleID', '_VehicleMarkerPlugin__playerVehicleID',
            'FROM_PLAYER', 'FROM_ALLY'),
    },
}


def _signature(code):
    values = list(code.co_varnames[:code.co_argcount])
    offset = code.co_argcount
    if code.co_flags & 0x04:
        values.append('*' + code.co_varnames[offset])
        offset += 1
    if code.co_flags & 0x08:
        values.append('**' + code.co_varnames[offset])
    return tuple(values)


def _walk_code(code, path, signatures, code_objects):
    current = path + (code.co_name,)
    if code.co_name not in ('<module>', '<lambda>', '<genexpr>'):
        name = '.'.join(part for part in current if part != '<module>')
        signatures[name] = _signature(code)
        code_objects[name] = code
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            _walk_code(value, current, signatures, code_objects)


def _module_constant_globals(code):
    """Extract immediate ``NAME = constant`` assignments from a code body."""
    result = {}
    bytecode = code.co_code
    index = 0
    extended = 0
    pending = None
    has_pending = False
    while index < len(bytecode):
        operation = ord(bytecode[index])
        index += 1
        argument = None
        if operation >= opcode.HAVE_ARGUMENT:
            argument = (ord(bytecode[index]) |
                        (ord(bytecode[index + 1]) << 8) |
                        extended)
            index += 2
            if operation == opcode.EXTENDED_ARG:
                extended = argument << 16
                has_pending = False
                continue
            extended = 0
        name = opcode.opname[operation]
        if name == 'LOAD_CONST':
            pending = code.co_consts[argument]
            has_pending = True
        elif name in ('STORE_NAME', 'STORE_GLOBAL') and has_pending:
            result[code.co_names[argument]] = pending
            has_pending = False
        else:
            has_pending = False
    return result


def _instructions(code):
    result = []
    bytecode = code.co_code
    index = 0
    extended = 0
    while index < len(bytecode):
        offset = index
        operation = ord(bytecode[index])
        index += 1
        argument = None
        if operation >= opcode.HAVE_ARGUMENT:
            argument = (ord(bytecode[index]) |
                        (ord(bytecode[index + 1]) << 8) |
                        extended)
            index += 2
            if operation == opcode.EXTENDED_ARG:
                extended = argument << 16
            else:
                extended = 0
        value = None
        if argument is not None:
            if operation in opcode.hasconst:
                value = code.co_consts[argument]
            elif operation in opcode.hasname:
                value = code.co_names[argument]
            elif operation in opcode.haslocal:
                value = code.co_varnames[argument]
        result.append({
            'offset': offset,
            'opname': opcode.opname[operation],
            'argument': argument,
            'value': value,
        })
    return result


def _is_read_only_property(class_code, property_name):
    instructions = _instructions(class_code)
    for index, instruction in enumerate(instructions):
        if (instruction['opname'] != 'STORE_NAME' or
                instruction['value'] != property_name or index < 4):
            continue
        window = instructions[index - 4:index + 1]
        if ([item['opname'] for item in window] == [
                'LOAD_NAME', 'LOAD_CONST', 'MAKE_FUNCTION',
                'CALL_FUNCTION', 'STORE_NAME'] and
                window[0]['value'] == 'property' and
                isinstance(window[1]['value'], types.CodeType) and
                window[3]['argument'] == 1):
            return True
    return False


def _returns_list(code, width):
    instructions = _instructions(code)
    for index, instruction in enumerate(instructions[:-1]):
        if (instruction['opname'] == 'BUILD_LIST' and
                instruction['argument'] == width and
                instructions[index + 1]['opname'] == 'RETURN_VALUE'):
            return True
    return False


def _mutates_subscript(code, attribute_name, index_value):
    instructions = _instructions(code)
    for index, instruction in enumerate(instructions):
        if (instruction['opname'] != 'LOAD_ATTR' or
                instruction['value'] != attribute_name):
            continue
        window = instructions[index:index + 4]
        if (len(window) >= 3 and
                window[1]['opname'] == 'LOAD_CONST' and
                window[1]['value'] == index_value and
                window[2]['opname'] == 'STORE_SUBSCR'):
            return True
    return False


def _has_ordered_instruction_pattern(code, pattern, maximum_gap):
    instructions = _instructions(code)
    previous = None
    for opname, field, expected in pattern:
        start = 0 if previous is None else previous + 1
        stop = (len(instructions) if previous is None else
                min(len(instructions), start + int(maximum_gap) + 1))
        match = None
        for index in xrange(start, stop):
            instruction = instructions[index]
            if instruction['opname'] != opname:
                continue
            if field is not None and instruction[field] != expected:
                continue
            match = index
            break
        if match is None:
            return False
        previous = match
    return True


def _unpacks_width(code, width):
    return any(
        instruction['opname'] == 'UNPACK_SEQUENCE' and
        instruction['argument'] == width
        for instruction in _instructions(code))


def _calls_width(code, width):
    return any(
        instruction['opname'] == 'CALL_FUNCTION' and
        instruction['argument'] == width
        for instruction in _instructions(code))


def _builds_tuple_width(code, width):
    return any(
        instruction['opname'] == 'BUILD_TUPLE' and
        instruction['argument'] == width
        for instruction in _instructions(code))


def _calls_var_width(code, width):
    return any(
        instruction['opname'] == 'CALL_FUNCTION_VAR' and
        instruction['argument'] == width
        for instruction in _instructions(code))


def _has_equality_return_branches(code, left_attribute, right_attribute,
                                  true_result, false_result):
    """Pin an attribute equality whose true/false paths return named values."""
    instructions = _instructions(code)
    offsets = dict(
        (instruction['offset'], index)
        for index, instruction in enumerate(instructions))
    for index, instruction in enumerate(instructions):
        if (instruction['opname'] != 'COMPARE_OP' or
                instruction['argument'] != 2 or index < 2 or
                index + 1 >= len(instructions)):
            continue
        before = instructions[max(0, index - 8):index]
        names = [item['value'] for item in before
                 if item['opname'] == 'LOAD_ATTR']
        if (left_attribute not in names or right_attribute not in names):
            continue
        branch = instructions[index + 1]
        if branch['opname'] != 'POP_JUMP_IF_FALSE':
            continue
        false_index = offsets.get(branch['argument'])
        if false_index is None:
            continue
        true_path = instructions[index + 2:false_index]
        false_path = instructions[false_index:]
        true_names = set(item['value'] for item in true_path
                         if item['opname'] == 'LOAD_ATTR')
        false_names = set(item['value'] for item in false_path
                          if item['opname'] == 'LOAD_ATTR')
        if (true_result in true_names and false_result in false_names and
                any(item['opname'] == 'RETURN_VALUE'
                    for item in true_path) and
                any(item['opname'] == 'RETURN_VALUE'
                    for item in false_path)):
            return True
    return False


def _read_module_contract(archive, member):
    payload = archive.read(member)
    if payload[:4] != '\x03\xf3\r\n':
        raise ValueError('%s is not CPython 2.7 bytecode' % member)
    code = marshal.loads(payload[8:])
    signatures = {}
    code_objects = {}
    _walk_code(code, (), signatures, code_objects)
    return signatures, code_objects, _module_constant_globals(code)


def _find_filter_sync_calls(archive):
    """Inventory every Python call site for unsafe retail filter syncs."""
    calls = set()
    for member in sorted(archive.namelist()):
        if not member.endswith('.pyc'):
            continue
        payload = archive.read(member)
        if payload[:4] != '\x03\xf3\r\n':
            continue
        code = marshal.loads(payload[8:])
        signatures = {}
        code_objects = {}
        _walk_code(code, (), signatures, code_objects)
        for name, item in code_objects.items():
            references = set(item.co_names)
            references.update(
                value for value in item.co_consts
                if isinstance(value, basestring))
            for method in _FILTER_SYNC_METHODS:
                if method in references:
                    calls.add((member, name, method))
    return calls


_PACKED_XML_MAGIC = '\x45\x4e\xa1\x62'
_PACKED_XML_TYPE_ELEMENT = 0
_PACKED_XML_TYPE_INTEGER = 2
_PACKED_XML_TYPE_BOOLEAN = 4
_PACKED_XML_OFFSET_MASK = 0x0fffffff


def _packed_xml_read_exact(reader, size):
    payload = reader.read(size)
    if len(payload) != size:
        raise ValueError('unexpected end of Packed XML')
    return payload


def _packed_xml_read_cstring(reader):
    chunks = []
    while True:
        value = _packed_xml_read_exact(reader, 1)
        if value == '\0':
            return ''.join(chunks)
        chunks.append(value)


def _packed_xml_descriptor(raw):
    return raw >> 28, raw & _PACKED_XML_OFFSET_MASK


def _packed_xml_read_value(reader, dictionary, value_type, end_offset,
                           current_offset):
    size = end_offset - current_offset
    if size < 0:
        raise ValueError('Packed XML offsets are not monotonic')
    start = reader.tell()
    if value_type == _PACKED_XML_TYPE_ELEMENT:
        value, unused_size = _packed_xml_read_element(reader, dictionary)
    else:
        value = _packed_xml_read_exact(reader, size)
        if value_type == _PACKED_XML_TYPE_INTEGER:
            if not value:
                value = 0
            elif len(value) in (1, 2, 4, 8):
                value = struct.unpack(
                    {1: '<b', 2: '<h', 4: '<i', 8: '<q'}[len(value)],
                    value)[0]
            else:
                raise ValueError('invalid Packed XML integer length')
        elif value_type == _PACKED_XML_TYPE_BOOLEAN:
            if len(value) not in (0, 1):
                raise ValueError('invalid Packed XML boolean length')
            value = bool(value and ord(value))
    consumed = reader.tell() - start
    if consumed != size:
        raise ValueError(
            'Packed XML value consumed %d bytes, expected %d' %
            (consumed, size))
    return (value_type, value), end_offset


def _packed_xml_read_element(reader, dictionary):
    start = reader.tell()
    child_count = struct.unpack(
        '<H', _packed_xml_read_exact(reader, 2))[0]
    root_descriptor = _packed_xml_descriptor(struct.unpack(
        '<I', _packed_xml_read_exact(reader, 4))[0])
    child_descriptors = []
    for unused in xrange(child_count):
        name_index, raw_descriptor = struct.unpack(
            '<HI', _packed_xml_read_exact(reader, 6))
        if name_index >= len(dictionary):
            raise ValueError('Packed XML dictionary index out of range')
        child_descriptors.append(
            (dictionary[name_index], _packed_xml_descriptor(raw_descriptor)))
    current_offset = 0
    root_value, current_offset = _packed_xml_read_value(
        reader, dictionary, root_descriptor[0], root_descriptor[1],
        current_offset)
    children = []
    for name, descriptor in child_descriptors:
        value, current_offset = _packed_xml_read_value(
            reader, dictionary, descriptor[0], descriptor[1],
            current_offset)
        children.append((name, value))
    return (root_value, children), reader.tell() - start


def _read_packed_xml_path_values(payload):
    reader = StringIO(payload)
    if _packed_xml_read_exact(reader, 4) != _PACKED_XML_MAGIC:
        raise ValueError('invalid Packed XML magic')
    _packed_xml_read_exact(reader, 1)
    dictionary = []
    while True:
        name = _packed_xml_read_cstring(reader)
        if not name:
            break
        dictionary.append(name)
    root, unused_size = _packed_xml_read_element(reader, dictionary)
    if reader.read(1):
        raise ValueError('trailing bytes after Packed XML root')
    result = {}

    def walk(element, prefix):
        unused_root_value, children = element
        for name, value in children:
            value_type, item = value
            path = prefix + (name,)
            if value_type == _PACKED_XML_TYPE_ELEMENT:
                root_value, unused_children = item
                root_type, root_item = root_value
                if root_type != 1 or root_item:
                    result.setdefault(path, []).append(
                        (root_type, root_item))
                walk(item, path)
            else:
                result.setdefault(path, []).append((value_type, item))

    walk(root, ())
    return dict((path, tuple(values)) for path, values in result.items())


def audit(client_root):
    package_path = os.path.join(
        os.path.abspath(client_root), 'res', 'packages', 'scripts.pkg')
    if not os.path.isfile(package_path):
        raise ValueError('scripts.pkg not found: %s' % package_path)
    checked = []
    checked_literals = []
    checked_names = []
    checked_globals = []
    checked_class_constants = []
    checked_filter_sync_calls = []
    checked_read_only_properties = []
    checked_list_returns = []
    checked_subscript_mutations = []
    checked_instruction_patterns = []
    checked_unpack_widths = []
    checked_call_widths = []
    checked_tuple_widths = []
    checked_var_call_widths = []
    checked_equality_branches = []
    checked_resource_strings = []
    checked_packed_xml_paths = []
    errors = []
    with zipfile.ZipFile(package_path, 'r') as archive:
        names = set(archive.namelist())
        members = (set(EXPECTED_ABI) | set(EXPECTED_CODE_LITERALS) |
                   set(EXPECTED_CODE_NAMES) | set(EXPECTED_GLOBALS) |
                   set(EXPECTED_CLASS_CONSTANTS) |
                   set(EXPECTED_READ_ONLY_PROPERTIES) |
                   set(EXPECTED_LIST_RETURNS) |
                   set(EXPECTED_SUBSCRIPT_MUTATIONS) |
                   set(EXPECTED_ORDERED_INSTRUCTION_PATTERNS) |
                   set(EXPECTED_UNPACK_WIDTHS) |
                   set(EXPECTED_CALL_WIDTHS) |
                   set(EXPECTED_TUPLE_WIDTHS) |
                   set(EXPECTED_VAR_CALL_WIDTHS) |
                   set(EXPECTED_EQUALITY_BRANCHES))
        for member in sorted(members):
            if member not in names:
                errors.append('missing bytecode member: %s' % member)
                continue
            actual, code_objects, module_globals = _read_module_contract(
                archive, member)
            for name, expected_args in sorted(
                    EXPECTED_ABI.get(member, {}).items()):
                actual_args = actual.get(name)
                if actual_args is None:
                    errors.append('%s: missing %s' % (member, name))
                elif actual_args != expected_args:
                    errors.append(
                        '%s: %s args %r, expected %r' %
                        (member, name, actual_args, expected_args))
                else:
                    checked.append('%s:%s' % (member, name))
            for name, expected_literals in sorted(
                    EXPECTED_CODE_LITERALS.get(member, {}).items()):
                code = code_objects.get(name)
                if code is None:
                    errors.append('%s: missing %s for literals' %
                                  (member, name))
                    continue
                constants = set(value for value in code.co_consts
                                if isinstance(value, basestring))
                for literal in expected_literals:
                    if literal not in constants:
                        errors.append('%s: %s missing literal %r' %
                                      (member, name, literal))
                    else:
                        checked_literals.append(
                            '%s:%s:%s' % (member, name, literal))
            for name, expected_names in sorted(
                    EXPECTED_CODE_NAMES.get(member, {}).items()):
                code = code_objects.get(name)
                if code is None:
                    errors.append('%s: missing %s for names' %
                                  (member, name))
                    continue
                code_names = set(code.co_names)
                for code_name in expected_names:
                    if code_name not in code_names:
                        errors.append('%s: %s missing code name %r' %
                                      (member, name, code_name))
                    else:
                        checked_names.append(
                            '%s:%s:%s' % (member, name, code_name))
            for name, expected_value in sorted(
                    EXPECTED_GLOBALS.get(member, {}).items()):
                if name not in module_globals:
                    errors.append('%s: missing constant global %s' %
                                  (member, name))
                elif module_globals[name] != expected_value:
                    errors.append('%s: %s is %r, expected %r' %
                                  (member, name, module_globals[name],
                                   expected_value))
                else:
                    checked_globals.append('%s:%s=%r' %
                                           (member, name, expected_value))
            for class_name, expected_constants in sorted(
                    EXPECTED_CLASS_CONSTANTS.get(member, {}).items()):
                class_code = code_objects.get(class_name)
                if class_code is None:
                    errors.append('%s: missing class body %s for constants' %
                                  (member, class_name))
                    continue
                class_constants = _module_constant_globals(class_code)
                for name, expected_value in sorted(
                        expected_constants.items()):
                    if name not in class_constants:
                        errors.append('%s: %s missing constant %s' %
                                      (member, class_name, name))
                    elif class_constants[name] != expected_value:
                        errors.append('%s: %s.%s is %r, expected %r' %
                                      (member, class_name, name,
                                       class_constants[name], expected_value))
                    else:
                        checked_class_constants.append(
                            '%s:%s.%s=%r' %
                            (member, class_name, name, expected_value))
            for class_name, properties in sorted(
                    EXPECTED_READ_ONLY_PROPERTIES.get(member, {}).items()):
                class_code = code_objects.get(class_name)
                if class_code is None:
                    errors.append(
                        '%s: missing class body %s for properties' %
                        (member, class_name))
                    continue
                for property_name in properties:
                    if not _is_read_only_property(
                            class_code, property_name):
                        errors.append(
                            '%s: %s.%s is not the expected getter-only '
                            'property' %
                            (member, class_name, property_name))
                    else:
                        checked_read_only_properties.append(
                            '%s:%s.%s' %
                            (member, class_name, property_name))
            for name, width in sorted(
                    EXPECTED_LIST_RETURNS.get(member, {}).items()):
                code = code_objects.get(name)
                if code is None:
                    errors.append('%s: missing %s for list return' %
                                  (member, name))
                elif not _returns_list(code, width):
                    errors.append(
                        '%s: %s does not return a %d-element list' %
                        (member, name, width))
                else:
                    checked_list_returns.append(
                        '%s:%s:list[%d]' % (member, name, width))
            for name, mutation in sorted(
                    EXPECTED_SUBSCRIPT_MUTATIONS.get(member, {}).items()):
                code = code_objects.get(name)
                attribute_name, index_value = mutation
                if code is None:
                    errors.append('%s: missing %s for subscript mutation' %
                                  (member, name))
                elif not _mutates_subscript(
                        code, attribute_name, index_value):
                    errors.append(
                        '%s: %s does not mutate %s[%r]' %
                        (member, name, attribute_name, index_value))
                else:
                    checked_subscript_mutations.append(
                        '%s:%s:%s[%r]' %
                        (member, name, attribute_name, index_value))
            for name, contract in sorted(
                    EXPECTED_ORDERED_INSTRUCTION_PATTERNS.get(
                        member, {}).items()):
                code = code_objects.get(name)
                label, maximum_gap, pattern = contract
                if code is None:
                    errors.append('%s: missing %s for %s' %
                                  (member, name, label))
                elif not _has_ordered_instruction_pattern(
                        code, pattern, maximum_gap):
                    errors.append('%s: %s lacks %s control flow' %
                                  (member, name, label))
                else:
                    checked_instruction_patterns.append(
                        '%s:%s:%s' % (member, name, label))
            for name, width in sorted(
                    EXPECTED_UNPACK_WIDTHS.get(member, {}).items()):
                code = code_objects.get(name)
                if code is None:
                    errors.append('%s: missing %s for unpack width' %
                                  (member, name))
                elif not _unpacks_width(code, width):
                    errors.append('%s: %s does not unpack %d values' %
                                  (member, name, width))
                else:
                    checked_unpack_widths.append(
                        '%s:%s:unpack[%d]' % (member, name, width))
            for name, width in sorted(
                    EXPECTED_CALL_WIDTHS.get(member, {}).items()):
                code = code_objects.get(name)
                if code is None:
                    errors.append('%s: missing %s for call width' %
                                  (member, name))
                elif not _calls_width(code, width):
                    errors.append('%s: %s does not call with %d values' %
                                  (member, name, width))
                else:
                    checked_call_widths.append(
                        '%s:%s:call[%d]' % (member, name, width))
            for name, width in sorted(
                    EXPECTED_TUPLE_WIDTHS.get(member, {}).items()):
                code = code_objects.get(name)
                if code is None:
                    errors.append('%s: missing %s for tuple width' %
                                  (member, name))
                elif not _builds_tuple_width(code, width):
                    errors.append('%s: %s does not build a %d-value tuple' %
                                  (member, name, width))
                else:
                    checked_tuple_widths.append(
                        '%s:%s:tuple[%d]' % (member, name, width))
            for name, width in sorted(
                    EXPECTED_VAR_CALL_WIDTHS.get(member, {}).items()):
                code = code_objects.get(name)
                if code is None:
                    errors.append('%s: missing %s for var-call width' %
                                  (member, name))
                elif not _calls_var_width(code, width):
                    errors.append('%s: %s does not var-call with %d values' %
                                  (member, name, width))
                else:
                    checked_var_call_widths.append(
                        '%s:%s:var-call[%d]' % (member, name, width))
            for name, branch in sorted(
                    EXPECTED_EQUALITY_BRANCHES.get(member, {}).items()):
                code = code_objects.get(name)
                if code is None:
                    errors.append('%s: missing %s for equality branch' %
                                  (member, name))
                elif not _has_equality_return_branches(code, *branch):
                    errors.append(
                        '%s: %s does not compare %s == %s and return '
                        '%s/%s on its branches' %
                        ((member, name) + branch))
                else:
                    checked_equality_branches.append(
                        '%s:%s:%s==%s:%s/%s' %
                        ((member, name) + branch))
        for member, expected_strings in sorted(
                EXPECTED_RESOURCE_STRINGS.items()):
            if member not in names:
                errors.append('missing resource member: %s' % member)
                continue
            payload = archive.read(member)
            for value in expected_strings:
                if value not in payload:
                    errors.append('%s: missing resource string %r' %
                                  (member, value))
                else:
                    checked_resource_strings.append(
                        '%s:%s' % (member, value))
        for member, expected_paths in sorted(
                EXPECTED_PACKED_XML_PATH_VALUES.items()):
            if member not in names:
                errors.append('missing Packed XML member: %s' % member)
                continue
            try:
                actual_paths = _read_packed_xml_path_values(
                    archive.read(member))
            except ValueError as error:
                errors.append('%s: %s' % (member, error))
                continue
            for path, expected_values in sorted(expected_paths.items()):
                actual_values = actual_paths.get(path)
                if actual_values != expected_values:
                    errors.append(
                        '%s: Packed XML path %s is %r, expected %r' %
                        (member, '/'.join(path), actual_values,
                         expected_values))
                else:
                    checked_packed_xml_paths.append(
                        '%s:%s=%r' %
                        (member, '/'.join(path), expected_values))
        actual_filter_sync_calls = _find_filter_sync_calls(archive)
        missing_filter_calls = (
            EXPECTED_FILTER_SYNC_CALLS - actual_filter_sync_calls)
        unexpected_filter_calls = (
            actual_filter_sync_calls - EXPECTED_FILTER_SYNC_CALLS)
        for item in sorted(missing_filter_calls):
            errors.append('missing filter sync call site: %s:%s:%s' % item)
        for item in sorted(unexpected_filter_calls):
            errors.append('unexpected filter sync call site: %s:%s:%s' % item)
        checked_filter_sync_calls.extend(
            '%s:%s:%s' % item
            for item in sorted(
                actual_filter_sync_calls & EXPECTED_FILTER_SYNC_CALLS))
    if errors:
        raise ValueError('; '.join(errors))
    return {
        'clientRoot': os.path.abspath(client_root),
        'pythonRuntime': '%d.%d.%d' % sys.version_info[:3],
        'checkedSignatures': len(checked),
        'checkedConsumerLiterals': len(checked_literals),
        'checkedCodeNames': len(checked_names),
        'checkedConstantGlobals': len(checked_globals),
        'checkedClassConstants': len(checked_class_constants),
        'checkedFilterSyncCalls': len(checked_filter_sync_calls),
        'checkedReadOnlyProperties': len(
            checked_read_only_properties),
        'checkedListReturns': len(checked_list_returns),
        'checkedSubscriptMutations': len(checked_subscript_mutations),
        'checkedInstructionPatterns': len(checked_instruction_patterns),
        'checkedUnpackWidths': len(checked_unpack_widths),
        'checkedCallWidths': len(checked_call_widths),
        'checkedTupleWidths': len(checked_tuple_widths),
        'checkedVarCallWidths': len(checked_var_call_widths),
        'checkedEqualityBranches': len(checked_equality_branches),
        'checkedResourceStrings': len(checked_resource_strings),
        'checkedPackedXmlPaths': len(checked_packed_xml_paths),
        'contracts': checked,
        'consumerLiterals': checked_literals,
        'codeNames': checked_names,
        'constantGlobals': checked_globals,
        'classConstants': checked_class_constants,
        'filterSyncCalls': checked_filter_sync_calls,
        'readOnlyProperties': checked_read_only_properties,
        'listReturns': checked_list_returns,
        'subscriptMutations': checked_subscript_mutations,
        'instructionPatterns': checked_instruction_patterns,
        'unpackWidths': checked_unpack_widths,
        'callWidths': checked_call_widths,
        'tupleWidths': checked_tuple_widths,
        'varCallWidths': checked_var_call_widths,
        'equalityBranches': checked_equality_branches,
        'resourceStrings': checked_resource_strings,
        'packedXmlPaths': checked_packed_xml_paths,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Audit exact WoT #1513 PYC signatures read-only.')
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
